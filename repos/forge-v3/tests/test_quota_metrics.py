"""SIMULATED fixture-backed tests for durable quota episodes and time accounting.

Every event here is synthetic; no native CLI, quota reader or model is invoked.
Expected seconds are hand-computed from the injected timestamps.
"""

import json

import pytest

from forge import quota_metrics as qm
from forge.state import State


@pytest.fixture
def state(tmp_path):
    with State(tmp_path / 'state', metrics=True) as s:
        yield s


def _db(state):
    return state.root / 'forge.db'


def start(state, at=0.0, goal='g'):
    with state.transaction() as db:
        return qm.ensure_observation(db, goal, at)


def wait(state, obs, event_id, at, cause='5h', kind='prestart_deferral', account='a', model='m', role='writer',
         **extra):
    with state.transaction() as db:
        return qm.record(db, obs, event_id=event_id, kind=kind, cause=cause, account=account, provider='p',
                         model=model, role=role, at=at, source='simulated', evidence={'simulated': True, **extra})


def resume(state, obs, call, started, confirmed, account='a', model='m', role='writer', basis='native_sid'):
    with state.transaction() as db:
        qm.record_process_start(db, {'obs': obs, 'account': account, 'provider': 'p', 'model': model,
                                     'role': role}, call, started)
    with state.transaction() as db:
        qm.confirm_continuation(db, obs, call, confirmed, basis)


def episodes(state):
    return [dict(r) for r in state.db.execute('SELECT * FROM quota_episodes ORDER BY opened_at')]


def snap(state, now, live=True):
    return qm.snapshot(_db(state), now, goal_id='g', live=live)


def test_simulated_repeated_cron_wait_checks_are_one_episode(state):
    obs = start(state)
    for i, at in enumerate((10, 20, 30)):
        assert wait(state, obs, f'attempt-{i}:wait', at)
    [episode] = episodes(state)
    assert episode['checks'] == 3 and episode['last_seen'] == 30 and episode['closed_at'] is None
    report = snap(state, 100)
    assert report['episodes']['open'] == 1 and report['checks']['prestart_deferral'] == 3
    assert report['wait_seconds']['cause'] == {'5h': 90.0} and report['quota_walltime_seconds'] == 90.0


def test_simulated_replay_same_event_id_is_not_duplicated(state):
    obs = start(state)
    assert wait(state, obs, 'call-1:result:5h', 10, kind='midflight_interruption')
    assert not wait(state, obs, 'call-1:result:5h', 50, kind='midflight_interruption')
    assert state.db.execute('SELECT count(*) FROM quota_events').fetchone()[0] == 1
    [episode] = episodes(state)
    assert episode['checks'] == 1 and episode['last_seen'] == 10


def test_simulated_two_episodes_separated_by_proven_continuation(state):
    obs = start(state)
    wait(state, obs, 'w1', 10)
    wait(state, obs, 'w-other-role', 10, role='reviewer')
    resume(state, obs, 'call-1', 50, 60)
    wait(state, obs, 'w2', 100)
    first, other, second = episodes(state)
    assert first['closed_at'] == 50
    closure = json.loads(first['closure'])
    assert closure['basis'] == 'native_sid'
    assert closure['started_at'] == 50 and closure['confirmed_at'] == 60
    assert other['role'] == 'reviewer' and other['closed_at'] is None  # another role resuming closes nothing
    assert second['opened_at'] == 100 and second['closed_at'] is None
    report = snap(state, 150)
    assert report['wait_seconds']['role'] == {'writer': 90.0, 'reviewer': 140.0}
    assert report['episodes'] == {'open': 2, 'closed': 1, 'facets': {
        'prestart_deferral': 3, 'midflight_interruption': 0, 'fallback_avoided': 0}}
    freq = report['frequency']
    assert freq['five_h_episodes'] == 3 and freq['admitted_continued_calls'] == 1 and freq['per_call'] == 3.0
    assert freq['per_covered_hour'] == pytest.approx(3 / (150 / 3600))


def test_simulated_unconfirmed_start_and_reset_time_never_close(state):
    obs = start(state)
    wait(state, obs, 'w1', 10, recheck_at=50.0)
    with state.transaction() as db:
        qm.record_process_start(db, {'obs': obs, 'account': 'a', 'provider': 'p', 'model': 'm', 'role': 'writer'},
                                'call-1', 60)
        qm.confirm_continuation(db, obs, 'call-unknown', 70, 'native_sid')  # no proposed start => no closure
    assert episodes(state)[0]['closed_at'] is None
    assert snap(state, 1000)['wait_seconds']['cause']['5h'] == 990.0


def test_simulated_overlapping_distinct_dimensions_and_union(state):
    obs = start(state)
    wait(state, obs, 'a5h', 10)
    wait(state, obs, 'bweekly', 20, cause='weekly', account='b')
    wait(state, obs, 'rmodel', 30, cause='model_window', role='reviewer', model='m2')
    wait(state, obs, 'reader', 40, cause='unknown_reader')
    assert len(episodes(state)) == 4
    report = snap(state, 100)
    assert report['wait_seconds']['cause'] == {'5h': 90.0, 'weekly': 80.0, 'model_window': 70.0,
                                               'unknown_reader': 60.0}
    assert report['wait_seconds']['account'] == {'a': 220.0, 'b': 80.0}
    assert report['quota_walltime_seconds'] == 90.0  # union, not 240
    assert report['separate_seconds'] == {'unknown_reader': 60.0, 'auth': 0, 'infra': 0}
    assert report['all_blockers_walltime_seconds'] == 90.0


def test_simulated_classify_is_conservative():
    assert qm.classify('quota reader 429') == 'unknown_reader'
    assert qm.classify('reader unavailable') == 'unknown_reader'
    assert qm.classify('quota_wait', 'missing window') == 'unknown_reader'
    assert qm.classify('usage_limit_reached') == 'quota_unspecified'
    assert qm.classify('exhausted (monitor)', 'resets in 5h') == 'quota_unspecified'
    assert qm.classify('quota_wait', 'window=5h') == '5h'
    assert qm.classify('quota_wait', 'seven_day') == 'weekly'
    assert qm.classify('unsupported profile') == 'policy'
    assert qm.classify('something new') == 'unknown_reader'
    assert set(qm.CAUSES) >= {qm.classify(r) for r in ('auth expired', 'bwrap failed', 'x')}


def test_simulated_facets_share_one_episode_and_policy_is_event_only(state):
    obs = start(state)
    assert wait(state, obs, 'fb-before', 5, kind='fallback_avoided')  # facet never creates an episode
    wait(state, obs, 'pre', 10)
    wait(state, obs, 'mid', 20, kind='midflight_interruption')
    wait(state, obs, 'fb', 30, kind='fallback_avoided')
    wait(state, obs, 'pol', 30, kind='policy', cause='policy')
    [episode] = episodes(state)
    assert json.loads(episode['facets']) == {'prestart_deferral': 1, 'midflight_interruption': 1,
                                             'fallback_avoided': 1}
    assert episode['checks'] == 2
    report = snap(state, 40)
    assert report['episodes']['facets'] == {'prestart_deferral': 1, 'midflight_interruption': 1,
                                            'fallback_avoided': 1}
    assert report['checks']['fallback_avoided'] == 2 and report['checks']['policy'] == 1
    assert report['quota_walltime_seconds'] == 30.0 and 'policy' not in report['wait_seconds']['cause']
    with pytest.raises(ValueError):
        wait(state, obs, 'bad', 41, cause='policy')


def test_simulated_zero_denominators_are_null(state):
    obs = start(state, 0)
    wait(state, obs, 'w', 0)
    freq = snap(state, 0)['frequency']
    assert freq['per_call'] is None and freq['per_covered_hour'] is None and freq['five_h_episodes'] == 1


def test_simulated_simulated_continuation_excluded_from_denominator(state):
    obs = start(state)
    wait(state, obs, 'w', 10)
    with state.transaction() as db:
        qm.record_process_start(db, {'obs': obs, 'account': 'a', 'provider': 'p', 'model': 'm', 'role': 'writer',
                                     'simulated': True}, 'sim-call', 20)
        qm.confirm_continuation(db, obs, 'sim-call', 25, 'fixture')
    report = snap(state, 30)
    assert report['simulated'] is True and episodes(state)[0]['closed_at'] == 20
    assert report['frequency']['admitted_continued_calls'] == 0 and report['frequency']['per_call'] is None


def test_simulated_crash_boundary_idempotency(tmp_path):
    root = tmp_path / 'state'
    with State(root, metrics=True) as s:
        obs = start(s)
        wait(s, obs, 'committed', 10)
        with pytest.raises(RuntimeError):
            with s.transaction() as db:
                qm.record(db, obs, event_id='rolled-back', kind='midflight_interruption', cause='weekly',
                          account='a', provider='p', model='m', role='writer', at=20)
                raise RuntimeError('simulated crash before commit')
        assert s.db.execute("SELECT count(*) FROM quota_events WHERE event_id='rolled-back'").fetchone()[0] == 0
    with State(root, metrics=True) as s:  # restarted process replays both
        assert not wait(s, obs, 'committed', 10)
        assert wait(s, obs, 'rolled-back', 20, cause='weekly', kind='midflight_interruption')
        assert not wait(s, obs, 'rolled-back', 20, cause='weekly', kind='midflight_interruption')
        assert [e['cause'] for e in episodes(s)] == ['5h', 'weekly']
        assert [e['checks'] for e in episodes(s)] == [1, 1]


def test_simulated_backward_clock_is_clamped_and_flagged(state):
    obs = start(state, 100)
    wait(state, obs, 'w', 90)
    assert state.db.execute('SELECT effective FROM quota_events').fetchone()[0] == 100
    assert episodes(state)[0]['opened_at'] == 100
    report = snap(state, 50)
    assert report['clock']['anomalies'] == 1 and report['clock']['backward_now'] is True
    assert report['wait_seconds']['cause']['5h'] == 0.0 and report['observation']['end_utc'] == 100


def test_simulated_open_episode_final_now_is_monotonic_without_writes(state):
    obs = start(state)
    wait(state, obs, 'w', 10)
    db = _db(state)
    before = (db.stat().st_mtime_ns, db.read_bytes())
    live = [snap(state, now)['wait_seconds']['cause']['5h'] for now in (50, 200, 300)]
    assert live == [40.0, 190.0, 290.0]
    capped = [snap(state, now, live=False)['wait_seconds']['cause']['5h'] for now in (50, 300)]
    assert capped == [0.0, 0.0]  # the durable span ends at the last write (t=10)
    assert (db.stat().st_mtime_ns, db.read_bytes()) == before


def test_simulated_span_intersection_union_and_stopped_gap(state):
    obs = start(state, 0)
    wait(state, obs, 'w5h', 10)
    with state.transaction() as db:
        qm.touch(db, obs, 40)
        qm.close_span(db, obs, 40)  # STOPPED from 40 until a parent-authorized restart at 100
    start(state, 100)
    wait(state, obs, 'wweekly', 120, cause='weekly')
    with state.transaction() as db:
        qm.touch(db, obs, 150)
    report = snap(state, 200, live=False)  # spans [0,40] and [100,150]
    assert report['observation']['covered_seconds'] == 90.0
    assert report['observation']['uncovered_seconds'] == 110.0 and report['observation']['coverage'] == 'partial'
    assert report['wait_seconds']['cause'] == {'5h': 80.0, 'weekly': 30.0}  # 30+50 and 30
    assert report['quota_walltime_seconds'] == 80.0
    live = snap(state, 200)  # open span now capped at final-now: [100,200]
    assert live['observation']['covered_seconds'] == 140.0 and live['observation']['uncovered_seconds'] == 60.0
    assert live['wait_seconds']['cause'] == {'5h': 130.0, 'weekly': 80.0}
    assert live['quota_walltime_seconds'] == 130.0


def test_simulated_dangling_span_is_not_backfilled_after_crash(state):
    obs = start(state, 0)
    with state.transaction() as db:
        qm.touch(db, obs, 30)  # process dies without closing its span
    start(state, 100)
    report = snap(state, 100)
    assert report['observation']['covered_seconds'] == 30.0 and report['observation']['coverage'] == 'partial'


def test_snapshot_never_creates_state(tmp_path):
    missing = tmp_path / 'absent' / 'forge.db'
    assert qm.snapshot(missing, 1.0) == {'available': False, 'reason': f'no state database at {missing}'}
    assert not (tmp_path / 'absent').exists()
    import sqlite3
    legacy = tmp_path / 'legacy.db'
    with sqlite3.connect(legacy) as db:
        db.execute('CREATE TABLE runs(id)')
        db.execute('PRAGMA user_version=2')
    report = qm.snapshot(legacy, 1.0)
    assert report['available'] is False and 'missing' in report['reason']
    with sqlite3.connect(legacy) as db:
        assert [r[0] for r in db.execute("SELECT name FROM sqlite_master")] == ['runs']
        assert db.execute('PRAGMA user_version').fetchone()[0] == 2


def test_evidence_is_allowlisted_and_bounded(state):
    obs = start(state)
    wait(state, obs, 'w', 10, reason='x' * 5000, token='sk-secret', raw_log='Authorization: Bearer y')
    evidence = json.loads(state.db.execute('SELECT evidence FROM quota_events').fetchone()[0])
    assert set(evidence) == {'reason', 'simulated'} and len(evidence['reason']) == qm.MAX_TEXT


@pytest.fixture
def cli_env(tmp_path, operator_config):
    from forge.config import load_config
    path = operator_config()
    goal = tmp_path / 'goal.md'
    goal.write_text('simulated goal\n')
    return path, goal, load_config(path)


def test_cli_loop_status_default_unchanged_and_metrics_read_only(cli_env, capsys, tmp_path):
    from forge.cli import main
    path, goal, config = cli_env
    assert main(['loop-status', '--config', str(path), '--goal', str(goal)]) == 0
    assert capsys.readouterr().out == json.dumps({'status': 'NONE'}, sort_keys=True) + '\n'
    assert main(['loop-status', '--config', str(path), '--goal', str(goal), '--json']) == 0
    assert capsys.readouterr().out == json.dumps({'status': 'NONE'}, sort_keys=True) + '\n'
    capsule = tmp_path / 'capsule.json'
    capsule.write_text(json.dumps({'status': 'STOPPED', 'mission': 'x'}))
    before = capsule.read_bytes()
    assert main(['loop-status', '--config', str(path), '--goal', str(goal), '--metrics', '--capsule',
                 str(capsule)]) == 0
    view = json.loads(capsys.readouterr().out)
    assert view['checkpoint'] == {'status': 'NONE'} and view['metrics']['available'] is False
    assert view['authorization'] == {'status': 'STOPPED', 'stopped': True}
    assert capsule.read_bytes() == before
    assert not config.state_dir.exists()


def test_cli_loop_status_metrics_reads_existing_db(cli_env, capsys):
    from forge.cli import main
    from forge.loop import goal_identity
    path, goal, config = cli_env
    with State(config.state_dir, metrics=True) as s:
        obs = start(s, 0, goal=goal_identity(goal)[2])
        wait(s, obs, 'w', 10)
    db = config.state_dir / 'forge.db'
    before = db.read_bytes()
    assert main(['loop-status', '--config', str(path), '--goal', str(goal), '--metrics']) == 0
    view = json.loads(capsys.readouterr().out)
    assert view['metrics']['available'] is True and view['metrics']['simulated'] is True
    assert view['metrics']['episodes']['open'] == 1 and db.read_bytes() == before


def test_multiple_explicit_windows_and_known_wrapper_vocabulary():
    assert qm.causes('low quota: 5h_left=0 weekly_left=0') == ('5h', 'weekly')
    assert qm.causes('quota reader 429', 'five_hour seven_day') == ('unknown_reader',)


@pytest.mark.parametrize('window', ['seven_day_opus', 'seven_day_sonnet'])
def test_model_specific_weekly_window_is_not_account_weekly(window):
    assert qm.classify('low quota: ' + window) == 'model_window'
    assert qm.causes('low quota: ' + window) == ('model_window',)
    assert set(qm.causes('low quota: five_hour seven_day ' + window)) == {'5h', 'weekly', 'model_window'}


def test_simulated_confirmation_uses_persisted_effective_start_and_replays_once(state):
    obs = start(state)
    wait(state, obs, 'wait', 10)
    with state.transaction() as db:
        qm.touch(db, obs, 40)
    resume(state, obs, 'call-clamped', 20, 100)
    [episode] = episodes(state)
    assert episode['closed_at'] == 40  # persisted effective start, not raw 20 or result 100
    closure = json.loads(episode['closure'])
    assert closure['started_at'] == 40 and closure['confirmed_at'] == 100
    with state.transaction() as db:
        qm.confirm_continuation(db, obs, 'call-clamped', 200, 'replayed')
    assert episodes(state)[0]['closure'] == episode['closure']
    assert snap(state, 200)['wait_seconds']['cause'] == {'5h': 30.0}
