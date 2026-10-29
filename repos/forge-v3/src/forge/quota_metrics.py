"""Durable quota episodes and truthful wait-time accounting for the goal loop.

Writers take a sqlite3 connection already inside the caller's transaction, so an
event insert and its episode transition commit (or roll back) together. Replay is
idempotent through INSERT OR IGNORE on event_id. Every timestamp is injected and
clamped to the observation's persisted max(last_effective, at); a backward clock
is counted, never turned into negative time. snapshot() is strictly read-only.

Episode identity is observation+account+provider+model+role+cause. Repeated
waits extend one open episode; only a confirmed admitted continuation of the same
account/provider/model/role closes it. Wait time is only counted inside confirmed
authorized spans; gaps between spans are reported as uncovered, never as waiting.
"""

import json
from pathlib import Path
import re
import sqlite3

CAUSES = ('5h', 'weekly', 'model_window', 'quota_unspecified', 'unknown_reader', 'auth', 'infra', 'policy')
QUOTA_CAUSES = ('5h', 'weekly', 'model_window', 'quota_unspecified')
SEPARATE_CAUSES = ('unknown_reader', 'auth', 'infra')
KINDS = ('prestart_deferral', 'midflight_interruption', 'fallback_avoided', 'policy',
         'continuation_proposed', 'continuation')
EPISODE_KINDS = ('prestart_deferral', 'midflight_interruption')
FACETS = ('prestart_deferral', 'midflight_interruption', 'fallback_avoided')
EVIDENCE_KEYS = ('reason', 'outcome', 'window', 'recheck_at', 'reset_at', 'attempt', 'basis', 'http_status',
                 'exit_code', 'simulated', 'source_kind')
MAX_TEXT = 160
MAX_EVIDENCE = 2048
EPSILON = 1e-6

_q = lambda values: ','.join(f"'{v}'" for v in values)
METRICS_SCHEMA = f"""
CREATE TABLE quota_observation (
 id TEXT PRIMARY KEY, goal TEXT NOT NULL, started_at REAL NOT NULL, last_effective REAL NOT NULL,
 clock_anomalies INTEGER NOT NULL DEFAULT 0 CHECK(clock_anomalies >= 0)
);
CREATE TABLE quota_events (
 event_id TEXT PRIMARY KEY, obs TEXT NOT NULL REFERENCES quota_observation(id),
 kind TEXT NOT NULL CHECK(kind IN ({_q(KINDS)})), cause TEXT NOT NULL CHECK(cause IN ({_q(CAUSES + ('',))})),
 account TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, role TEXT NOT NULL,
 at REAL NOT NULL, effective REAL NOT NULL, call_id TEXT, source TEXT NOT NULL,
 evidence TEXT NOT NULL DEFAULT '{{}}' CHECK(json_valid(evidence) AND length(evidence) <= {MAX_EVIDENCE}),
 episode TEXT REFERENCES quota_episodes(id)
);
CREATE TABLE quota_episodes (
 id TEXT PRIMARY KEY, obs TEXT NOT NULL REFERENCES quota_observation(id),
 account TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, role TEXT NOT NULL,
 cause TEXT NOT NULL CHECK(cause IN ({_q(CAUSES)}) AND cause != 'policy'), first_source TEXT NOT NULL,
 opened_at REAL NOT NULL, last_seen REAL NOT NULL, checks INTEGER NOT NULL DEFAULT 1 CHECK(checks >= 0),
 facets TEXT NOT NULL DEFAULT '{{}}' CHECK(json_valid(facets)),
 closed_at REAL, close_event TEXT, closure TEXT CHECK(closure IS NULL OR json_valid(closure)),
 CHECK(closed_at IS NULL OR (closed_at >= opened_at AND close_event IS NOT NULL))
);
CREATE UNIQUE INDEX quota_episodes_open ON quota_episodes(obs,account,provider,model,role,cause) WHERE closed_at IS NULL;
CREATE TABLE quota_spans (
 id INTEGER PRIMARY KEY, obs TEXT NOT NULL REFERENCES quota_observation(id),
 start REAL NOT NULL, end REAL NOT NULL, open INTEGER NOT NULL DEFAULT 1 CHECK(open IN (0,1)), CHECK(end >= start)
);
CREATE UNIQUE INDEX quota_spans_open ON quota_spans(obs) WHERE open = 1;
"""
TABLES = ('quota_observation', 'quota_events', 'quota_episodes', 'quota_spans')


def _explicit_windows(text):
    found = []
    if any(w in text for w in ('five_hour', 'window=5h', 'window:5h', '5h_window', '5h_left')):
        found.append('5h')
    # An underscore is a word character: seven_day_opus is not the account's seven_day window.
    if re.search(r'\bseven_day\b', text) or any(w in text for w in (
            'window=weekly', 'window:weekly', 'weekly_window', 'weekly_left')):
        found.append('weekly')
    if any(w in text for w in ('model_window', 'model window', 'opus_window', 'seven_day_opus', 'seven_day_sonnet')):
        found.append('model_window')
    return tuple(found)


def classify(reason: str, evidence: str | None = None, outcome: str | None = None) -> str:
    """Conservative cause from known wrapper vocabulary; never infer 5h from a reset duration."""
    text = ' '.join(str(v) for v in (reason, evidence, outcome) if v).lower()
    if '429' in text or 'reader' in text or 'no window' in text or 'missing window' in text or 'window missing' in text:
        return 'unknown_reader'
    if 'unsupported' in text or 'policy' in text:
        return 'policy'
    if 'auth' in text or 'login' in text or 'credential' in text:
        return 'auth'
    windows = _explicit_windows(text)
    if windows:
        return windows[0]
    if any(w in text for w in ('usage_limit', 'quota', 'rate_limit', 'rate limit', 'exhausted')):
        return 'quota_unspecified'
    if any(w in text for w in ('sandbox', 'bwrap', 'network', 'infra', 'env')):
        return 'infra'
    return 'unknown_reader'


def causes(reason, evidence=None, outcome=None):
    """Preserve multiple explicit windows; unknown reader evidence always wins."""
    first = classify(reason, evidence, outcome)
    if first in ('unknown_reader', 'auth', 'infra', 'policy'):
        return (first,)
    text = ' '.join(str(v) for v in (reason, evidence, outcome) if v).lower()
    return _explicit_windows(text) or (first,)


def _sanitize(evidence):
    """Allowlisted scalar fields only; strings truncated; the whole blob bounded."""
    clean = {}
    for key in EVIDENCE_KEYS:
        value = (evidence or {}).get(key)
        if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
            if value is not None:
                clean[key] = value
        elif isinstance(value, str):
            clean[key] = value[:MAX_TEXT]
    text = json.dumps(clean, sort_keys=True, allow_nan=False)
    return text if len(text) <= MAX_EVIDENCE else json.dumps({'truncated': True})


def _effective(db, obs, at):
    """Clamp to persisted max(last_effective, at); count a backward clock once per write."""
    row = db.execute('SELECT last_effective FROM quota_observation WHERE id=?', (obs,)).fetchone()
    if row is None:
        raise ValueError(f'unknown observation {obs}')
    last = row[0]
    if at < last:
        db.execute('UPDATE quota_observation SET clock_anomalies=clock_anomalies+1 WHERE id=?', (obs,))
        return last
    db.execute('UPDATE quota_observation SET last_effective=? WHERE id=?', (at, obs))
    return at


def touch(db, obs: str, at: float) -> float:
    """Extend last_effective and the open authorized span; returns the effective time."""
    effective = _effective(db, obs, float(at))
    db.execute('UPDATE quota_spans SET end=max(end,?) WHERE obs=? AND open=1', (effective, obs))
    return effective


def close_span(db, obs: str, at: float) -> None:
    effective = touch(db, obs, at)
    db.execute('UPDATE quota_spans SET end=max(end,?), open=0 WHERE obs=? AND open=1', (effective, obs))


def ensure_observation(db, goal_id: str, now: float) -> str:
    """Call once per authorized invocation. No backfill: a dangling span from an
    earlier process stays capped at its last durable end; a new span starts now."""
    obs = f'obs:{goal_id}'
    now = float(now)
    db.execute('INSERT OR IGNORE INTO quota_observation(id,goal,started_at,last_effective) VALUES(?,?,?,?)',
               (obs, goal_id, now, now))
    effective = _effective(db, obs, now)
    db.execute('UPDATE quota_spans SET open=0 WHERE obs=? AND open=1 AND start<?', (obs, effective))
    db.execute('INSERT OR IGNORE INTO quota_spans(obs,start,end) VALUES(?,?,?)', (obs, effective, effective))
    return obs


def record(db, obs: str, *, event_id: str, kind: str, cause: str, account: str, provider: str, model: str,
           role: str, at: float, call_id: str | None = None, source: str = '', evidence: dict | None = None) -> bool:
    """Insert one event and apply its episode transition. Returns True if newly inserted."""
    if kind not in KINDS:
        raise ValueError(f'unknown metrics event kind {kind!r}')
    if kind in ('continuation', 'continuation_proposed'):
        cause = cause or ''
    elif kind == 'policy':
        cause = 'policy'
    elif cause not in CAUSES or cause == 'policy':
        raise ValueError(f'invalid quota cause {cause!r} for {kind}')
    dims = (obs, account or '', provider or '', model or '', role or '')
    inserted = db.execute(
        """INSERT OR IGNORE INTO quota_events(event_id,obs,kind,cause,account,provider,model,role,at,effective,
           call_id,source,evidence) VALUES(?,?,?,?,?,?,?,?,?,0,?,?,?)""",
        (event_id, *dims[:1], kind, cause, *dims[1:], float(at), call_id, str(source)[:MAX_TEXT],
         _sanitize(evidence))).rowcount
    if not inserted:
        return False
    effective = touch(db, obs, at)
    db.execute('UPDATE quota_events SET effective=? WHERE event_id=?', (effective, event_id))
    if kind in EPISODE_KINDS + ('fallback_avoided',):
        row = db.execute("""SELECT id,facets FROM quota_episodes WHERE obs=? AND account=? AND provider=? AND model=?
                            AND role=? AND cause=? AND closed_at IS NULL""", (*dims, cause)).fetchone()
        if row is None and kind == 'fallback_avoided':
            return True  # a facet never manufactures an episode
        if row is None:
            episode = f'ep:{event_id}'
            db.execute("""INSERT INTO quota_episodes(id,obs,account,provider,model,role,cause,first_source,
                          opened_at,last_seen,facets) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                       (episode, *dims, cause, kind if not source else f'{kind}:{source}'[:MAX_TEXT],
                        effective, effective, json.dumps({kind: 1})))
        else:
            episode, facets = row[0], json.loads(row[1])
            facets[kind] = facets.get(kind, 0) + 1
            checks = 1 if kind in EPISODE_KINDS else 0
            db.execute('UPDATE quota_episodes SET last_seen=max(last_seen,?), checks=checks+?, facets=? WHERE id=?',
                       (effective, checks, json.dumps(facets, sort_keys=True), episode))
        db.execute('UPDATE quota_events SET episode=? WHERE event_id=?', (episode, event_id))
    elif kind == 'continuation':
        # Confirmation proves that work continued; its persisted start tells
        # when quota wait ended. Keep confirmation time as separate evidence.
        proposed = db.execute("""SELECT effective FROM quota_events
                                 WHERE obs=? AND account=? AND provider=? AND model=? AND role=?
                                   AND call_id=? AND kind='continuation_proposed'""",
                              (*dims, call_id)).fetchone()
        if proposed is None:
            return True
        started_at = proposed[0]
        closure = json.dumps({'event': event_id, 'call_id': call_id,
                              'basis': json.loads(_sanitize(evidence)).get('basis'),
                              'started_at': started_at, 'confirmed_at': effective}, sort_keys=True)
        db.execute("""UPDATE quota_episodes SET closed_at=max(opened_at,?), close_event=?, closure=?
                      WHERE obs=? AND account=? AND provider=? AND model=? AND role=? AND closed_at IS NULL""",
                   (started_at, event_id, closure, *dims))
    return True


def record_process_start(db, observation, call_id: str, at: float) -> None:
    """Durable proposed start, recorded inside the runner's running-row transaction."""
    record(db, observation['obs'], event_id=f'{call_id}:start', kind='continuation_proposed', cause='',
           account=observation.get('account', ''), provider=observation.get('provider', ''),
           model=observation.get('model', ''), role=observation.get('role', ''), at=at, call_id=call_id,
           source='process_start', evidence={'simulated': observation.get('simulated')})


def confirm_continuation(db, obs: str, call_id: str, at: float, basis: str) -> None:
    """Finalize a persisted proposed start; without one, no closure is invented."""
    row = db.execute("""SELECT account,provider,model,role,evidence FROM quota_events
                        WHERE obs=? AND call_id=? AND kind='continuation_proposed'""", (obs, call_id)).fetchone()
    if row is None:
        return
    simulated = json.loads(row[4]).get('simulated')
    record(db, obs, event_id=f'{call_id}:continuation', kind='continuation', cause='', account=row[0],
           provider=row[1], model=row[2], role=row[3], at=at, call_id=call_id, source='confirmed',
           evidence={'basis': basis, 'simulated': simulated})


def _union(intervals):
    """Sweep sorted endpoints into disjoint intervals; overlap counts once."""
    merged = []
    for start, end in sorted(i for i in intervals if i[1] > i[0]):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _measure(intervals):
    return sum(end - start for start, end in _union(intervals))


def _intersect(interval, spans):
    start, end = interval
    return [(max(start, s), min(end, e)) for s, e in spans if min(end, e) > max(start, s)]


def snapshot(db_path: Path, now: float, *, goal_id: str | None = None, live: bool = False) -> dict:
    """Read-only metrics view (sqlite mode=ro); never creates directories, databases or tables.

    `live=True` only when the caller itself holds the active invocation (open span
    capped at now); otherwise an open span is capped at its last durable end.
    """
    db_path = Path(db_path)
    if not db_path.is_file():
        return {'available': False, 'reason': f'no state database at {db_path}'}
    try:
        db = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=5)
    except sqlite3.Error as exc:
        return {'available': False, 'reason': f'cannot open state database read-only: {exc}'}
    try:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not set(TABLES) <= names:
            return {'available': False, 'reason': 'metrics tables missing (schema older than v3)'}
        where, params = ('WHERE goal=?', (goal_id,)) if goal_id else ('', ())
        observations = db.execute(f'SELECT id,started_at,last_effective,clock_anomalies FROM quota_observation {where}',
                                  params).fetchall()
        if not observations:
            return {'available': False, 'reason': 'no observation recorded'}
        ids = [r[0] for r in observations]
        marks = ','.join('?' * len(ids))
        return _report(db, observations, ids, marks, float(now), live)
    except sqlite3.Error as exc:
        return {'available': False, 'reason': f'metrics read failed: {exc}'}
    finally:
        db.close()


def _report(db, observations, ids, marks, now, live):
    last_effective = max(r[2] for r in observations)
    final = max(last_effective, now)
    started = min(r[1] for r in observations)
    spans = _union((s, max(e, final) if (live and o) else e) for s, e, o in db.execute(
        f'SELECT start,end,open FROM quota_spans WHERE obs IN ({marks})', ids))
    covered = _measure(spans)
    uncovered = max(0.0, (final - started) - covered)
    totals = {dim: {} for dim in ('cause', 'account', 'provider', 'model', 'role')}
    by_cause, facets, open_count, closed_count, five_h = {}, dict.fromkeys(FACETS, 0), 0, 0, 0
    for account, provider, model, role, cause, opened, closed, raw in db.execute(
            f"""SELECT account,provider,model,role,cause,opened_at,closed_at,facets FROM quota_episodes
                WHERE obs IN ({marks})""", ids):
        authorized = _intersect((opened, final if closed is None else closed), spans)
        seconds = sum(e - s for s, e in authorized)
        for dim, key in zip(totals, (cause, account, provider, model, role)):
            totals[dim][key] = totals[dim].get(key, 0.0) + seconds
        by_cause.setdefault(cause, []).extend(authorized)
        open_count += closed is None
        closed_count += closed is not None
        five_h += cause == '5h'
        for facet in json.loads(raw):
            facets[facet] = facets.get(facet, 0) + 1
    checks = dict(db.execute(f'SELECT kind,count(*) FROM quota_events WHERE obs IN ({marks}) GROUP BY kind', ids))
    simulated = db.execute(f"""SELECT count(*) FROM quota_events WHERE obs IN ({marks})
                               AND json_extract(evidence,'$.simulated') = 1""", ids).fetchone()[0]
    calls = db.execute(f"""SELECT count(DISTINCT call_id) FROM quota_events WHERE obs IN ({marks})
                           AND kind='continuation' AND call_id IS NOT NULL
                           AND coalesce(json_extract(evidence,'$.simulated'),0) != 1""", ids).fetchone()[0]
    quota = [i for c in QUOTA_CAUSES for i in by_cause.get(c, [])]
    blockers = quota + [i for c in SEPARATE_CAUSES for i in by_cause.get(c, [])]
    hours = covered / 3600
    return {
        'available': True,
        'simulated': bool(simulated),
        'observation': {'start_utc': started, 'end_utc': final, 'covered_seconds': covered,
                        'uncovered_seconds': uncovered,
                        'coverage': 'complete' if uncovered <= EPSILON else 'partial',
                        'open_span_capped_at_now': bool(live)},
        'clock': {'anomalies': sum(r[3] for r in observations), 'backward_now': now < last_effective,
                  'last_effective': last_effective},
        'episodes': {'open': open_count, 'closed': closed_count, 'facets': facets},
        'checks': {kind: checks.get(kind, 0) for kind in KINDS},
        'wait_seconds': totals,
        'quota_walltime_seconds': _measure(quota),
        'separate_seconds': {c: _measure(by_cause.get(c, [])) for c in SEPARATE_CAUSES},
        'all_blockers_walltime_seconds': _measure(blockers),
        'frequency': {'five_h_episodes': five_h, 'admitted_continued_calls': calls,
                      'per_call': five_h / calls if calls else None,
                      'per_covered_hour': five_h / hours if hours > 0 else None},
    }
