"""Durable deterministic lifecycle controller.

Content is provided by a RoleDriver; it cannot pick routes, advance tasks, spend
budgets, accept commits or assert owner satisfaction. NativeRoleDriver is the
production transport. P4 supplies its content hooks, never a simulated provider.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
import re
from pathlib import Path
from posixpath import normpath
import shutil
import signal
import time
import uuid

from forge import sandbox
from forge.adapters import adapter_for
from forge.adapters.base import Call
from forge.doctor import binary_version
from forge.gate import Gate, GateError, Receipt, matches
from forge.gitops import Candidate, Git, GitError, IntegrationBlocked, Integrator, crash
from forge.runner import Result, proc_start, run_call
from forge.state import State, StateError, atomic_json, atomic_write, controller_lock

NON_TASK = {'po_read', 'po', 'architect', 'adr_review'}
WRITERS = {'implement', 'red', 'fix', 'escalate'}
TERMINAL = {'COMMITTED', 'BLOCKED', 'CANCELLED', 'SUPPRESSED_DUP'}
# No account timer: recheck on the plan-timeout cadence instead of polling.
REVIEWER_HOLD_SECONDS = 900
STAGES = {'READY': 'plan', 'PLANNING': 'plan', 'PLANNED': 'implement',
          'RED_WRITING': 'red', 'IMPLEMENTING': 'implement', 'FIXING': 'fix',
          'ESCALATING': 'escalate', 'REVIEWING': 'review'}


class EngineBlocked(RuntimeError):
    def __init__(self, message, outcome='config_block'):
        super().__init__(message)
        self.outcome = outcome


@dataclass(frozen=True)
class Request:
    prompt: str
    work: Path
    writable: bool = False
    images: tuple = ()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def role_tier(role, difficulty='easy'):
    if difficulty not in ('easy', 'hard'):
        raise ValueError('difficulty must be exactly easy or hard')
    if role in NON_TASK or role == 'escalate':
        return 'strong'
    if role == 'plan':
        return 'normal' if difficulty == 'easy' else 'strong'
    if role in ('implement', 'red', 'fix'):
        return 'cheap' if difficulty == 'easy' else 'normal'
    if role == 'review':
        return 'normal' if difficulty == 'easy' else 'strong'
    raise ValueError('unknown content role: ' + role)


class NativeRoleDriver:
    """Actual runner transport plus P4 content hooks (prepare/request/validate).

    Hooks receive engine.role() for every content call, including selection and
    comparison chunks. No hook is allowed to supply scheduling verdicts.
    """
    def __init__(self, config, state, content=None):
        self.config, self.state, self.content = config, state, content
        self.roots = sandbox.project_roots(config)

    def verified(self, profile):
        row = self.state.db.execute('SELECT * FROM smoke WHERE profile=?', (profile.id,)).fetchone()
        if not row or row['status'] != 'pass':
            return set()
        try:
            record = json.loads(Path(row['manifest_path']).read_text())
            h = self.config.harness[profile.harness]
            binary = sandbox.resolve_binary(profile.harness, h.get('binary'))
            info = {'path': str(binary), 'version': binary_version(self.roots, profile.harness, binary, h.get('runtime_dir')),
                    'sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}
            expected = {'binary': info, 'model': profile.model, 'effort': profile.effort,
                        'binds_rw': list(h['binds_rw']), 'binds_ro': list(h['binds_ro']),
                        'runtime_dir': h.get('runtime_dir')}
            fingerprint = hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()
            if record.get('sandbox_probe') != 'pass' or row['fingerprint'] != fingerprint:
                return set()
            return set(json.loads(row['capabilities']))
        except (OSError, ValueError, sandbox.SandboxError):
            return set()

    def invoke(self, call, account, validate, cancelled, *, task=None, batch=None, run=None):
        profile = next(p for p in self.config.profiles if p.id == call.profile)
        if not self.verified(profile):
            raise EngineBlocked('smoke fingerprint changed before launch; run doctor')
        h = self.config.harness[profile.harness]
        binary = sandbox.resolve_binary(profile.harness, h.get('binary'))
        info = {'path': str(binary), 'version': binary_version(self.roots, profile.harness, binary, h.get('runtime_dir'))}

        def spec_for(args):
            return sandbox.agent_spec(self.roots, harness=profile.harness, binary=binary, native_args=tuple(args),
                                      work=call.workdir, writable=call.writable,
                                      input_dir=self.state.root / 'calls' / call.id / 'in',
                                      binds_rw=h['binds_rw'], binds_ro=h['binds_ro'], runtime_dir=h.get('runtime_dir'))
        return run_call(self.state, call, adapter_for(profile.harness), spec_for,
                        calls_root=self.state.root / 'calls', account=account, validate=validate,
                        should_cancel=cancelled, task=task, batch=batch, run=run, binary_info=info)

    def _content(self):
        if self.content is None:
            # P4 is a real content module, never an implicit fake provider.
            try:
                from forge.roles import ContentDriver
            except ImportError as exc:
                raise EngineBlocked('P4 content roles unavailable; install the ingest/roles layer') from exc
            self.content = ContentDriver(self.config, self.state)
        return self.content

    def prepare(self, engine, batch):
        return self._content().prepare(engine, batch)

    def request(self, engine, task, role):
        return self._content().request(engine, task, role)

    def validate(self, role, difficulty, value):
        return self._content().validate(role, difficulty, value)

    def finalize(self, engine, task):
        return self._content().finalize(engine, task)


class Engine:
    def __init__(self, config, state, driver=None, *, clock=time.time, sleep=time.sleep):
        self.config, self.state = config, state
        self.clock, self.sleep = clock, sleep
        self.driver = driver or NativeRoleDriver(config, state)
        self.git = Git(config.target_repo, state.root, config.branch)
        self.gate = Gate(self.git, state, sandbox.project_roots(config), config.commands,
                         protected=config.protected, sources=config.sources, test_paths=config.test_paths,
                         ro_binds=config.execution['ro_binds'], network=config.execution['gate_network'], should_cancel=self.cancelled)
        self.integrator = Integrator(self.git, state, self.gate, config.budgets['rebases'])
        self.run_id = None
        self._signal_cancel = False
        with state.transaction() as db:
            for account in config.accounts:
                db.execute('INSERT OR IGNORE INTO accounts(id) VALUES(?)', (account,))

    def task(self, task):
        row = self.state.db.execute('SELECT * FROM tasks WHERE id=?', (task,)).fetchone()
        if row is None:
            raise EngineBlocked('unknown task: ' + task)
        return dict(row)

    def update(self, task, **values):
        with self.state.transaction() as db:
            db.execute('UPDATE tasks SET ' + ','.join(k + '=?' for k in values) + ' WHERE id=?',
                       (*values.values(), task))

    def metadata(self, task, **values):
        data = json.loads(self.task(task)['metadata'])
        data.update(values)
        self.update(task, metadata=json.dumps(data))
        return data

    def blocked_record(self, task, cause, outcome='config_block', **extra):
        row = self.task(task) if task else {}
        calls = self.state.db.execute('SELECT id,raw_dir FROM calls WHERE task IS ? ORDER BY started DESC LIMIT 10', (task,)).fetchall()
        return {'cause': cause, 'outcome': outcome, 'last_calls': [r['id'] for r in calls],
                'evidence_paths': [r['raw_dir'] for r in calls if r['raw_dir']],
                'preserved': {'ref': row.get('candidate_ref'), 'tree': row.get('candidate_tree'),
                              'workspace': row.get('workspace')},
                'unblock_condition': 'correct configuration and pass doctor; then operator unblock with note', **extra}

    def block(self, task, cause, outcome='config_block', **extra):
        record = self.blocked_record(task, cause, outcome, **extra)
        self.update(task, prior_state=self.task(task)['state'], state='BLOCKED', blocked=json.dumps(record))
        return None

    def cancelled(self, task=None):
        return self._signal_cancel or bool(self.state.db.execute(
            "SELECT 1 FROM cancels WHERE target IN ('all','*',?) AND handled IS NULL", (task,)).fetchone())

    def families(self, task):
        profiles = {p.id: p.family for p in self.config.profiles}
        # Calls, not the current session alone: both writers remain excluded.
        return set(json.loads(self.task(task)['metadata']).get('writer_families', [])) | {profiles[r[0]] for r in self.state.db.execute(
            "SELECT DISTINCT profile FROM calls WHERE task=? AND role IN ('implement','red','fix','escalate')", (task,))
                if r[0] in profiles}

    def route(self, role, task=None, *, images=False, excluded=()):
        row = self.task(task) if task else {}
        difficulty = row.get('difficulty', 'hard')
        tier = 'strong' if row.get('escalated') and role in WRITERS else role_tier(role, difficulty)
        resume = role == 'fix' or role == 'implement' and row.get('kind') == 'bug' or role in WRITERS and bool(row.get('session_id'))
        if resume and not row.get('session_id'):
            if task:
                self.block(task, 'session_lost', 'session_lost', unblock_condition='operator unblock --fresh-session --note TEXT')
            return [], {'session': 'session_lost'}
        excluded = set(excluded) | (self.families(task) if role == 'review' and task else set())
        disabled = {r[0] for r in self.state.db.execute('SELECT profile FROM profiles_disabled WHERE run IS ? OR run IS NULL', (self.run_id,))}
        routes, reasons = [], {}
        for p in self.config.profiles:
            reason = None
            if not p.enabled or not self.config.accounts[p.account]['enabled'] or p.id in disabled:
                reason = 'disabled profile/account'
            elif tier not in p.tiers:
                reason = 'tier ' + tier
            elif p.family in excluded:
                reason = 'excluded family ' + p.family
            elif resume and p.id != row.get('session_profile'):
                reason = 'exact session profile required'
            else:
                capabilities = self.driver.verified(p)
                required = {'text', 'resume'} if role in WRITERS else {'text', 'readonly'}
                if images:
                    required.add('image')
                if not required <= capabilities:
                    reason = 'capabilities/smoke: ' + ','.join(sorted(required - capabilities))
            if reason:
                reasons[p.id] = reason
            else:
                routes.append(p)
        return routes, reasons

    def _disable(self, profile, reason, outcome):
        with self.state.transaction() as db:
            db.execute('INSERT OR REPLACE INTO profiles_disabled VALUES(?,?,?,?,?)',
                       (profile.id, self.run_id, reason, outcome, self.clock()))

    def _account_effect(self, profile, result):
        # A crash between durable result and task transition must not double
        # account backoff, strikes, or disable effects when that result is replayed.
        with self.state.transaction() as db:
            if db.execute('SELECT 1 FROM call_effects WHERE call=?', (result.call_id,)).fetchone():
                return
            if result.outcome == 'ok':
                db.execute("UPDATE accounts SET state='available',last_ok=?,k=0,recheck_at=NULL,outcome='ok' WHERE id=?",
                           (self.clock(), profile.account))
            elif result.effects.get('account_wait'):
                if result.recheck_at is None or not math.isfinite(result.recheck_at) or result.recheck_at <= self.clock():
                    raise EngineBlocked('invalid account wait timer')
                db.execute("UPDATE accounts SET state='waiting',recheck_at=?,k=k+1,outcome=?,reason=? WHERE id=?",
                           (result.recheck_at, result.outcome, json.dumps(result.error_fields), profile.account))
            run_key = self.run_id or ''
            db.execute('INSERT OR IGNORE INTO profile_counters(profile,run) VALUES(?,?)', (profile.id, run_key))
            counters = db.execute('SELECT * FROM profile_counters WHERE profile=? AND run=?', (profile.id, run_key)).fetchone()
            protocol = counters['protocol'] + 1 if result.outcome == 'protocol' else 0
            unknown = counters['unknown_fail'] + 1 if result.outcome == 'unknown_fail' else 0
            db.execute('UPDATE profile_counters SET protocol=?,unknown_fail=? WHERE profile=? AND run=?',
                       (protocol, unknown, profile.id, run_key))
            disable = result.effects.get('disable')
            if protocol > self.config.budgets['protocol_retries'] or unknown >= self.config.budgets['profile_strikes']:
                disable = 'profile'
            if disable:
                targets = self.config.profiles if disable == 'sandbox' else (profile,)
                for p in targets:
                    db.execute('INSERT OR REPLACE INTO profiles_disabled VALUES(?,?,?,?,?)',
                               (p.id, self.run_id, json.dumps(result.error_fields), result.outcome, self.clock()))
            db.execute('INSERT INTO call_effects VALUES(?,?)', (result.call_id, self.clock()))

    def _profile_exhausted(self, profile_id):
        row = self.state.db.execute(
            'SELECT protocol,unknown_fail FROM profile_counters WHERE profile=? AND run=?',
            (profile_id, self.run_id or '')).fetchone()
        if row is None:
            return False
        return (row['unknown_fail'] >= self.config.budgets['profile_strikes']
                or row['protocol'] > self.config.budgets['protocol_retries'])

    def _writer_author(self, routes):
        """First writer route that is not in a future account wait. None when every route is waiting."""
        now = self.clock()
        for profile in routes:
            account = self.state.db.execute('SELECT state,recheck_at FROM accounts WHERE id=?', (profile.account,)).fetchone()
            if account['state'] == 'waiting' and account['recheck_at'] is not None and account['recheck_at'] > now:
                continue
            return profile
        return None

    def _reviewer_hold(self, role, task, author):
        """Wait spec when no enabled different-family reviewer is admissible for this author now."""
        review_tier = role_tier('review', self.task(task)['difficulty'])
        excluded = set(self.families(task))
        excluded.add(author.family)
        now = self.clock()
        disabled = {row[0] for row in self.state.db.execute(
            'SELECT profile FROM profiles_disabled WHERE run IS ? OR run IS NULL', (self.run_id,))}
        rejected, waiting_accounts, waiting_timers = {}, [], []
        ready = same = exhausted = disabled_other = False
        for profile in self.config.profiles:
            different = profile.family not in excluded
            if not profile.enabled or not self.config.accounts[profile.account]['enabled'] or profile.id in disabled:
                rejected[profile.id] = 'disabled profile/account'
                disabled_other = disabled_other or different
                continue
            if review_tier not in profile.tiers:
                rejected[profile.id] = 'tier ' + review_tier
                continue
            if not {'text', 'readonly'} <= set(self.driver.verified(profile)):
                rejected[profile.id] = 'capabilities/smoke'
                continue
            if self._profile_exhausted(profile.id):
                rejected[profile.id] = 'exhausted'
                exhausted = exhausted or different
                continue
            if not different:
                rejected[profile.id] = 'same-family ' + profile.family
                same = True
                continue
            account = self.state.db.execute('SELECT * FROM accounts WHERE id=?', (profile.account,)).fetchone()
            if account['state'] == 'waiting' and account['recheck_at'] is not None and account['recheck_at'] > now:
                rejected[profile.id] = 'waiting'
                waiting_accounts.append(account['id'])
                waiting_timers.append(account['recheck_at'])
                continue
            ready = True
        if ready:
            return None
        if waiting_timers:
            detail, timer, accounts = 'waiting', min(waiting_timers), sorted(set(waiting_accounts))
        elif exhausted:
            detail, timer, accounts = 'exhausted', now + REVIEWER_HOLD_SECONDS, []
        elif disabled_other:
            detail, timer, accounts = 'disabled', now + REVIEWER_HOLD_SECONDS, []
        elif same:
            detail, timer, accounts = 'same_family', now + REVIEWER_HOLD_SECONDS, []
        else:
            detail, timer, accounts = 'none', now + REVIEWER_HOLD_SECONDS, []
        return {'reason': 'no independent reviewer', 'detail': detail, 'recheck_at': timer,
                'waiting_accounts': accounts, 'author_family': author.family, 'author_profile': author.id,
                'rejected': rejected}

    def _apply_reviewer_hold(self, task, role, hold):
        row = self.task(task)
        data = json.loads(row['metadata'])
        data['reviewer_admission'] = hold
        self.update(task, metadata=json.dumps(data), state='WAITING',
                    prior_state=row['prior_state'] if row['state'] == 'WAITING' else row['state'],
                    waiting_role=role, recheck_at=hold['recheck_at'],
                    waiting_accounts=json.dumps(hold['waiting_accounts']))

    def _reject_writer(self, role, task, author):
        """Hold a writer when this author has no admissible different-family reviewer."""
        if author is None or not task or role not in WRITERS or self.task(task)['state'] in TERMINAL:
            return False
        hold = self._reviewer_hold(role, task, author)
        if hold is None:
            return False
        self._apply_reviewer_hold(task, role, hold)
        return True

    def admission(self, role, request, task=None, batch=None, excluded=()):
        routes, reasons = self.route(role, task, images=bool(request.images), excluded=excluded)
        if not routes:
            if task and self.task(task)['state'] != 'BLOCKED':
                outcome = 'capability_block' if any('capabilities' in r for r in reasons.values()) else 'config_block'
                self.block(task, 'empty structural route', outcome, role=role, excluded_profiles=reasons)
            if not task and batch:
                record = self.blocked_record(None, 'empty structural route', 'capability_block' if any('capabilities' in r for r in reasons.values()) else 'config_block', role=role, excluded_profiles=reasons)
                with self.state.transaction() as db:
                    db.execute("UPDATE batches SET state='BLOCKED',blocked=? WHERE id=?", (json.dumps(record), batch))
            raise EngineBlocked('empty structural route: ' + json.dumps(reasons))
        # Before probes or author launch: a waiting or missing independent reviewer must not fall through to a write.
        if task and role in WRITERS and self._reject_writer(role, task, self._writer_author(routes)):
            return None
        for p in routes:
            account = self.state.db.execute('SELECT * FROM accounts WHERE id=?', (p.account,)).fetchone()
            now = self.clock()
            if account['state'] == 'waiting' and account['recheck_at'] > now:
                continue
            if account['state'] != 'available' or account['last_ok'] is None or account['last_ok'] < now - 1800:
                with self.state.transaction() as db:
                    db.execute("UPDATE accounts SET state='unknown',recheck_at=NULL WHERE id=?", (p.account,))
                nonce = uuid.uuid4().hex
                call = self.make_call('probe', p, Request('Reply only with fenced JSON {"nonce":"' + nonce + '"}', request.work), None)
                result = self.driver.invoke(call, p.account, lambda v: v == {'nonce': nonce}, lambda: self.cancelled(task),
                                            task=task, batch=batch, run=self.run_id)
                self._account_effect(p, result)
                if result.outcome != 'ok':
                    if not result.effects.get('account_wait'):
                        self._disable(p, 'admission probe: ' + result.outcome, result.outcome)
                    continue
            # The probe loop may select a later family than the pre-loop author.
            if self._reject_writer(role, task, p):
                return None
            return p
        # Recompute after deterministic probe failures; only temporary account deferral waits.
        remaining, reasons = self.route(role, task, images=bool(request.images), excluded=excluded)
        if not remaining:
            return self.admission(role, request, task, batch, excluded)
        accounts = [dict(self.state.db.execute('SELECT * FROM accounts WHERE id=?', (p.account,)).fetchone()) for p in remaining]
        if any(a['state'] != 'waiting' or a['recheck_at'] is None for a in accounts):
            raise EngineBlocked('admission failed without a finite wait')
        timer = min(a['recheck_at'] for a in accounts)
        if task:
            row = self.task(task)
            self.update(task, state='WAITING', prior_state=row['state'] if row['state'] != 'WAITING' else row['prior_state'],
                        waiting_role=role, recheck_at=timer, waiting_accounts=json.dumps([a['id'] for a in accounts]))
        else:
            with self.state.transaction() as db:
                db.execute("UPDATE batches SET state='WAITING',blocked=? WHERE id=?", (json.dumps({'role': role, 'recheck_at': timer}), batch))
        return None

    def make_call(self, role, profile, request, task, call_id=None):
        row = self.task(task) if task else {}
        resume = role == 'fix' or role in WRITERS and bool(row.get('session_id'))
        sid = row.get('session_id') if resume else None
        limit_role = 'implement' if role in WRITERS else 'architect' if role == 'adr_review' else role
        preset = str(uuid.uuid4()) if not sid and profile.harness in ('claude', 'grok') else None
        prompt = request.prompt
        if json.loads(row.get('metadata', '{}')).get('interrupted'):
            prompt = 'Previous turn interrupted. Continue the preserved work.\n' + prompt
        return Call(call_id or uuid.uuid4().hex, role, profile.id, profile.harness, profile.model, profile.effort,
                    request.work, request.writable, prompt, tuple(request.images), sid, preset,
                    self.config.timeouts[limit_role], self.config.max_turns[limit_role])

    def role(self, role, request, *, task=None, batch=None, excluded=(), key=None, validate=None):
        """One durable content operation. Finished output is replayed after crash.

        Batch keys distinguish successful chunks; the failure budget is shared
        across all calls of the same role, as required by the PO contract.
        """
        key = key or role
        holder = self.task(task) if task else dict(self.state.db.execute('SELECT * FROM batches WHERE id=?', (batch,)).fetchone())
        data = json.loads(holder['metadata'] if task else holder['role_attempts'])
        operations = data.setdefault('operations', {})
        operation = operations.get(key)
        if operation and operation.get('done'):
            saved = Result(**json.loads(Path(operation['result']).read_text()))
            if saved.outcome == 'ok':
                return saved
        if not operation:
            p = self.admission(role, request, task, batch, excluded)
            if p is None:
                return None
            call = self.make_call(role, p, request, task)
            operation = {'call': call.id, 'profile': p.id, 'role': role,
                         'request': {'prompt': request.prompt, 'work': str(request.work), 'writable': request.writable,
                                     'images': [str(p) for p in request.images]}}
            operations[key] = operation
            self._save_holder(task, batch, data)
            crash('engine_before_launch')
        else:
            if operation.get('request'):
                saved = operation['request']
                request = Request(saved['prompt'], Path(saved['work']), saved['writable'], tuple(map(Path, saved['images'])))
            p = next((p for p in self.config.profiles if p.id == operation['profile']), None)
            if p is None:
                raise EngineBlocked('recorded profile removed; operator configuration/session handoff required')
            call = self.make_call(role, p, request, task, operation['call'])
        stored = self.state.db.execute('SELECT * FROM calls WHERE id=?', (call.id,)).fetchone()
        if stored and stored['status'] == 'done':
            result = Result(**json.loads(Path(stored['result_path']).read_text()))
        elif stored:
            # Recovery marks interrupted calls and clears their operation entry.
            raise EngineBlocked('unfinished operation requires recovery')
        else:
            # A persisted operation has no calls row until launch, so recovery retries it here.
            if self._reject_writer(role, task, p):
                return None
            if role in WRITERS and task:
                self.update(task, session_profile=p.id)
                self.metadata(task, writer_families=sorted(self.families(task) | {p.family}))
            result = self.driver.invoke(call, p.account,
                                        lambda v: self.driver.validate(role, holder.get('difficulty', 'hard'), v) and (validate(v) if validate else True),
                                        lambda: self.cancelled(task), task=task, batch=batch, run=self.run_id)
        crash('engine_after_result')
        self._account_effect(p, result)
        crash('engine_after_effect')
        data = json.loads((self.task(task)['metadata'] if task else self.state.db.execute(
            'SELECT role_attempts FROM batches WHERE id=?', (batch,)).fetchone()[0]))
        data.setdefault('operations', {})[key] = {**operation, 'done': True, 'result': str(Path(result.raw_dir) / 'result.json')}
        updates = {}
        if task and role in WRITERS and result.sid:
            if call.resume_sid and result.sid != call.resume_sid:
                self.block(task, 'resume returned a different SID', 'session_lost')
                return None
            updates.update(session_id=result.sid, session_profile=p.id)
        if result.outcome == 'ok':
            if task:
                timing = self.state.db.execute('SELECT started,ended FROM calls WHERE id=?', (result.call_id,)).fetchone()
                if role in WRITERS and timing and timing['ended'] is not None:
                    updates['coding_seconds'] = self.task(task)['coding_seconds'] + max(0, timing['ended'] - timing['started'])
                self.update(task, metadata=json.dumps(data), **updates)
            else:
                self._save_holder(task, batch, data)
            crash('engine_after_operation')
        elif updates:
            self.update(task, **updates)
        if result.outcome == 'ok':
            if task and role in WRITERS and not result.sid:
                self.block(task, 'writer returned no SID', 'session_lost')
                return None
            return result
        if task and call.resume_sid and any(s in json.dumps(result.error_fields).lower() for s in ('session not found', 'session_not_found', 'session-not-found')):
            self.block(task, 'session_lost', 'session_lost', unblock_condition='operator unblock --fresh-session --note TEXT')
        else:
            self.failure(task, batch, role, result, key)
        return None

    def _save_holder(self, task, batch, data):
        with self.state.transaction() as db:
            if task:
                db.execute('UPDATE tasks SET metadata=? WHERE id=?', (json.dumps(data), task))
            else:
                db.execute('UPDATE batches SET role_attempts=? WHERE id=?', (json.dumps(data), batch))

    def clear_operation(self, task, role):
        data = json.loads(self.task(task)['metadata'])
        data.get('operations', {}).pop(role, None)
        self.update(task, metadata=json.dumps(data))

    def failure(self, task, batch, role, result, key):
        holder = self.task(task) if task else dict(self.state.db.execute('SELECT * FROM batches WHERE id=?', (batch,)).fetchone())
        data = json.loads(holder['metadata'] if task else holder['role_attempts'])
        data.get('operations', {}).pop(key, None)
        token = role + ':' + str(result.call_id)
        counted = data.setdefault('counted', [])
        if token in counted:
            return
        counted.append(token)
        profile = next((p for p in self.config.profiles if p.id == self.state.db.execute('SELECT profile FROM calls WHERE id=?', (result.call_id,)).fetchone()[0]), None)
        counters = self.state.db.execute('SELECT * FROM profile_counters WHERE profile=? AND run=?',
                                         (profile.id, self.run_id or '')).fetchone()
        counted_failure = result.effects.get('coding_budget') or (result.outcome == 'protocol' and counters and
                         counters['protocol'] > self.config.budgets['protocol_retries'])
        if not task:
            if counted_failure:
                data[role] = data.get(role, 0) + 1
            exhausted = data.get(role, 0) >= self.config.budgets['role_attempts']
            with self.state.transaction() as db:
                db.execute('UPDATE batches SET role_attempts=? WHERE id=?', (json.dumps(data), batch))
                if exhausted:
                    db.execute("UPDATE batches SET state='BLOCKED',blocked=? WHERE id=?",
                               (json.dumps(self.blocked_record(None, role + ' attempt budget exhausted', 'budget_exhausted')), batch))
            return
        if counted_failure:
            self.spend(task, 'plan_attempts' if role == 'plan' else 'fix_rounds' if role in ('fix', 'red', 'escalate', 'review') else 'impl_attempts', data=data)
        else:
            values = {'metadata': json.dumps(data)}
            if result.outcome == 'cancelled':
                values.update(state='CANCELLED', prior_state=holder['state'])
            self.update(task, **values)

    def spend(self, task, counter, data=None):
        row = self.task(task)
        data = data if data is not None else json.loads(row['metadata'])
        value = row[counter] + 1
        limit = self.config.budgets['escalation']['fix_rounds'] if row['escalated'] else self.config.budgets[row['difficulty']][counter]
        values = {counter: value, 'attempts': row['attempts'] + 1}
        if value >= limit:
            if row['escalated']:
                values.update(state='BLOCKED', prior_state=row['state'], blocked=json.dumps(
                    self.blocked_record(task, 'attempt/fix budget exhausted after one escalation', 'budget_exhausted')))
            else:
                data['needs_handoff'] = 'budget exhausted'
        self.update(task, metadata=json.dumps(data), **values)
        if data.get('needs_handoff'):
            self.handoff(task, data['needs_handoff'])

    def handoff(self, task, reason, *, operator=False):
        row = self.task(task)
        directory = self.state.root / 'evidence' / task
        directory.mkdir(parents=True, exist_ok=True)
        directory = directory / ('handoff-' + uuid.uuid4().hex)
        directory.mkdir()
        path = directory / 'handoff.md'
        tree = self.git.snapshot(Path(row['workspace']), row['base_commit']) if row['workspace'] else None
        patch = self.git.run('diff', '--no-ext-diff', row['base_commit'], tree).stdout.decode(errors='replace') if tree else ''
        evidence = self.blocked_record(task, reason)
        payload = {'reason': reason, 'old_profile': row['session_profile'], 'old_sid': row['session_id'],
                   'diff': patch, 'findings_and_gate_logs': evidence['evidence_paths'], 'metadata': json.loads(row['metadata'])}
        atomic_write(path, ('# Session handoff\n\nPreserved diff, findings and gate logs:\n\n```json\n' +
                            json.dumps(payload, sort_keys=True, indent=2) + '\n```\n').encode())
        metadata = json.loads(row['metadata'])
        metadata.pop('needs_handoff', None)
        with self.state.transaction() as db:
            db.execute('INSERT INTO handoffs(task,at,old_profile,old_sid,reason,artifact_path) VALUES(?,?,?,?,?,?)',
                       (task, self.clock(), row['session_profile'], row['session_id'], reason, str(path)))
            db.execute("UPDATE tasks SET state='ESCALATING',escalated=1,session_id=NULL,session_profile=NULL,fix_rounds=0,metadata=? WHERE id=?", (json.dumps(metadata), task))
        self.clear_operation(task, 'escalate')

    def input_fingerprint(self):
        files = self.git.files(self.git.head())
        patterns = (*self.config.sources, 'docs/forge/intent/**', 'docs/forge/implemented/**', *self.config.test_paths)
        blobs = {p: oid for p, (_, oid) in files.items() if matches(p, patterns)}
        evidence = {}
        # Only linked result evidence affects input: new logs from an unchanged
        # failure must not manufacture a new requirement input on every batch.
        documents = {path: self.git.blob(oid).decode(errors='replace') for path, (_, oid) in files.items()
                     if matches(path, (*self.config.sources, 'docs/forge/implemented/**', 'docs/forge/intent/**', 'docs/forge/tasks/**'))}
        texts = list(documents.values())
        linked = set()
        for source, text in documents.items():
            for target in re.findall(r'\]\(([^)]+)\)', text):
                target = target.split('#', 1)[0]
                linked.add(target)
                linked.add(normpath(str(Path(source).parent / target)))
        for path in linked:
            if path in files:
                evidence[path] = files[path][1]
        for artifact in self.state.db.execute('SELECT path,sha256 FROM artifacts'):
            path = artifact['path']
            if not any(path in text for text in texts):
                continue
            try:
                evidence[path] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            except OSError as exc:
                raise EngineBlocked('linked evidence unreadable: ' + path) from exc
        notes = [tuple(r) for r in self.state.db.execute('SELECT task,note,fresh_session FROM unblocks ORDER BY id')]
        return digest({'blobs': blobs, 'evidence': evidence, 'unblocks': notes,
                       'roster': [asdict(p) for p in self.config.profiles if p.enabled]})

    def add_tasks(self, batch, proposals, input_fp):
        if len(proposals) > 6:
            raise EngineBlocked('batch exceeds six tasks')
        ids = [p.get('id') or uuid.uuid4().hex for p in proposals]
        if len(ids) != len(set(ids)):
            raise EngineBlocked('duplicate task IDs')
        for ident, p in zip(ids, proposals):
            if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', ident):
                raise EngineBlocked('invalid task ID')
            role_tier('plan', p['difficulty'])
            deps = [ids[d] if type(d) is int and 0 <= d < len(ids) else d for d in p.get('depends_on', [])]
            if any(d not in ids or d == ident for d in deps):
                raise EngineBlocked('invalid dependency')
            key = digest([p['kind'], sorted(p['req_ids']), sorted(p['target_paths'])])
            duplicate = self.state.db.execute("SELECT 1 FROM tasks WHERE work_key=? AND input_fp=? AND state IN ('BLOCKED','SUPPRESSED_DUP')", (key, input_fp)).fetchone()
            with self.state.transaction() as db:
                db.execute('INSERT INTO tasks(id,batch,kind,difficulty,work_key,req_ids,depends_on,state,input_fp,metadata) VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (ident, batch, p['kind'], p['difficulty'], key, json.dumps(p['req_ids']), json.dumps(deps),
                            'SUPPRESSED_DUP' if duplicate else 'READY', input_fp, json.dumps({'proposal': p})))
        # Cycles cannot produce timerless dependency waits.
        graph = {ident: json.loads(self.task(ident)['depends_on']) for ident in ids}
        def visit(node, stack):
            if node in stack:
                raise EngineBlocked('dependency cycle')
            for dep in graph[node]:
                visit(dep, stack | {node})
        for ident in ids:
            visit(ident, set())
        return ids

    def candidate(self, row):
        return Candidate(row['candidate_commit'], row['candidate_tree'], row['base_commit'], row['candidate_ref'])

    def _receipt(self, data, key):
        value = data.get(key)
        return Receipt(**value) if isinstance(value, dict) and set(value) == {'path', 'sha256'} else None

    def step(self, ident):
        row = self.task(ident)
        if self.cancelled(ident) and not self.state.db.execute('SELECT 1 FROM commits WHERE task=?', (ident,)).fetchone():
            self.update(ident, state='CANCELLED', prior_state=row['state'])
            return
        stage = row['state']
        data = json.loads(row['metadata'])
        if data.get('needs_handoff'):
            self.handoff(ident, data['needs_handoff'])
            return
        if stage == 'READY':
            base = self.git.head()
            workspace = self.state.root / 'ws' / ident / 'src'
            if workspace.exists():
                shutil.rmtree(workspace)
            self.git.clone(base, workspace)
            self.update(ident, base_commit=base, workspace=str(workspace), state='PLANNING')
            return
        if stage == 'ESCALATING' and row['kind'] == 'bug' and self._receipt(data, 'red') is None:
            self.update(ident, state='RED_WRITING')
            return
        if stage in STAGES:
            role = STAGES[stage]
            if stage == 'PLANNED':
                if row['kind'] == 'bug':
                    if not all(any('{' + token + '}' in arg for arg in self.config.commands['test_one']) for token in ('node', 'junit')):
                        self.block(ident, 'bug requires test_one with node and junit placeholders')
                        return
                    self.update(ident, state='RED_WRITING')
                else:
                    self.update(ident, state='IMPLEMENTING')
                return
            if role == 'implement' and row['kind'] == 'bug':
                self.gate.require_red_before_fix(ident, self._receipt(data, 'red'))
            previous = data.get('operations', {}).get(role, {}).get('request')
            request = (Request(previous['prompt'], Path(previous['work']), previous['writable'], tuple(map(Path, previous['images'])))
                       if previous else self.driver.request(self, row, role))
            if request.writable != (role in WRITERS):
                raise EngineBlocked('role write mode invariant')
            result = self.role(role, request, task=ident, batch=row['batch'])
            if result is None:
                return
            data = json.loads(self.task(ident)['metadata'])
            data[role] = result.final_json
            data['last_call'] = result.call_id
            data['interrupted'] = False
            if role == 'review':
                p = next(p for p in self.config.profiles if p.id == self.state.db.execute('SELECT profile FROM calls WHERE id=?', (result.call_id,)).fetchone()[0])
                try:
                    receipt = self.gate.review(ident, self.candidate(row), self._receipt(data, 'gate'), request.work,
                                               result.final_json, call_id=result.call_id, reviewer_family=p.family,
                                               implementer_families=self.families(ident))
                except GateError as exc:
                    self.metadata(ident, findings=str(exc))
                    self.clear_operation(ident, role)
                    self.clear_operation(ident, 'fix')
                    self.update(ident, state='FIXING')
                    self.spend(ident, 'fix_rounds')
                    return
                data['review_receipt'] = asdict(receipt)
                next_stage = 'INTEGRATING'
            elif role == 'plan':
                next_stage = 'PLANNED'
            elif role == 'red':
                next_stage = 'RED_CHECK'
            else:
                next_stage = 'CANDIDATE'
            self.update(ident, metadata=json.dumps(data), state=next_stage)
            if role == 'escalate' or (role == 'red' and row['escalated']):
                with self.state.transaction() as db:
                    db.execute('UPDATE handoffs SET new_profile=?,new_sid=? WHERE id=(SELECT max(id) FROM handoffs WHERE task=?)',
                               (self.task(ident)['session_profile'], result.sid, ident))
            return
        if stage == 'RED_CHECK':
            candidate = self.git.candidate(ident, Path(row['workspace']), row['base_commit'], 'Regression checkpoint')
            nodes = data.get('red', {}).get('nodes', [])
            try:
                receipt = self.gate.red(ident, candidate, nodes)
            except GateError as exc:
                if self.cancelled(ident):
                    self.update(ident, state='CANCELLED', prior_state=stage)
                    return
                self.metadata(ident, findings=str(exc))
                self.clear_operation(ident, 'red')
                self.update(ident, state='RED_WRITING')
                self.spend(ident, 'fix_rounds')
                return
            self.metadata(ident, red_output=data.get('red'), red=asdict(receipt))
            self.update(ident, state='IMPLEMENTING')
            return
        if stage == 'CANDIDATE':
            # A crash after save_candidate is safe: the same snapshot is re-gated.
            self.driver.finalize(self, row)
            candidate = self.git.candidate(ident, Path(row['workspace']), row['base_commit'])
            self.git.save_candidate(self.state, ident, candidate)
            self.update(ident, state='GATING')
            return
        if stage == 'GATING':
            if row['kind'] == 'bug' and self._receipt(data, 'red') is None:
                self.block(ident, 'bug candidate has no durable RED; preserve candidate and restart regression-first through operator unblock',
                           unblock_condition='operator unblock with note: preserve candidate, restore base workspace, then RED and same-session fix')
                return
            try:
                receipt = self.gate.check(ident, self.candidate(row), data['proposal']['doc_targets'], red=self._receipt(data, 'red'))
            except GateError as exc:
                if self.cancelled(ident):
                    self.update(ident, state='CANCELLED', prior_state=stage)
                    return
                self.metadata(ident, findings=str(exc))
                self.clear_operation(ident, 'fix')
                self.clear_operation(ident, 'review')
                self.update(ident, state='FIXING')
                self.spend(ident, 'fix_rounds')
                return
            self.metadata(ident, gate=asdict(receipt))
            self.clear_operation(ident, 'review')
            self.update(ident, state='REVIEWING')
            return
        if stage in ('INTEGRATING', 'REBASING'):
            try:
                self.integrator.accept(ident, self.candidate(row), self._receipt(data, 'review_receipt'))
            except IntegrationBlocked:
                if self.task(ident)['state'] != 'BLOCKED':
                    self.clear_operation(ident, 'review')
                    self.update(ident, state='GATING')
            return
        raise EngineBlocked('unsupported task state: ' + stage)

    def drain(self, batch, *, until_idle=False):
        while True:
            progressed, timers = False, []
            for stored in self.state.db.execute('SELECT id FROM tasks WHERE batch=? ORDER BY rowid', (batch,)).fetchall():
                row = self.task(stored[0])
                if row['state'] in TERMINAL:
                    continue
                deps = [self.task(dep)['state'] for dep in json.loads(row['depends_on'])]
                if any(s != 'COMMITTED' for s in deps):
                    if row['state'] != 'DEP_WAIT':
                        self.update(row['id'], prior_state=row['state'], state='DEP_WAIT')
                    continue
                if row['state'] == 'DEP_WAIT':
                    self.update(row['id'], state=row['prior_state'] or 'READY')
                    row = self.task(row['id'])
                if row['state'] == 'WAITING':
                    if row['recheck_at'] > self.clock():
                        timers.append(row['recheck_at'])
                        continue
                    self.update(row['id'], state=row['prior_state'], recheck_at=None)
                try:
                    self.step(row['id'])
                except (EngineBlocked, sandbox.SandboxError, GitError) as exc:
                    if self.task(row['id'])['state'] != 'BLOCKED':
                        self.block(row['id'], str(exc), 'env_block' if isinstance(exc, sandbox.SandboxError) else getattr(exc, 'outcome', 'config_block'))
                progressed = True
                break  # parallel=1: finish this runnable task before starting another
            if progressed:
                continue
            if self.cancelled():
                return 130
            if timers:
                if until_idle:
                    return 75
                self.sleep(max(0, min(timers) - self.clock()))
                continue
            states = [r[0] for r in self.state.db.execute('SELECT state FROM tasks WHERE batch=?', (batch,))]
            return 2 if any(s in ('BLOCKED', 'DEP_WAIT') for s in states) else 130 if 'CANCELLED' in states else 0

    def recover(self):
        """Must hold controller_lock. Resolve process ownership before any new writer."""
        from forge.runner import recover_calls
        recover_calls(self.state)
        try:
            self.integrator.recover()
        except IntegrationBlocked:
            for pending in self.state.db.execute("SELECT id FROM tasks WHERE state='REBASING'").fetchall():
                self.update(pending[0], state='GATING')
        for row in self.state.db.execute("SELECT DISTINCT task FROM calls WHERE status='interrupted' AND task IS NOT NULL").fetchall():
            ident = row[0]
            task = self.task(ident)
            if task['state'] in TERMINAL:
                continue
            data = json.loads(task['metadata'])
            interrupted = self.state.db.execute("SELECT * FROM calls WHERE task=? AND status='interrupted' ORDER BY started DESC LIMIT 1", (ident,)).fetchone()
            for key, op in list(data.get('operations', {}).items()):
                if op['call'] == interrupted['id']:
                    del data['operations'][key]
            data['interrupted'] = True
            self.update(ident, metadata=json.dumps(data))
            if interrupted['role'] in WRITERS:
                sid = interrupted['session_out'] or interrupted['session_in'] or interrupted['preset_sid']
                if sid:
                    self.update(ident, session_id=sid, session_profile=interrupted['profile'])
                elif interrupted['role'] == 'implement' and task['fix_rounds'] == 0 and task['kind'] != 'bug':
                    workspace = Path(task['workspace'])
                    tree = self.git.snapshot(workspace, task['base_commit'])
                    patch = self.git.run('diff', '--no-ext-diff', task['base_commit'], tree).stdout
                    directory = self.state.root / 'evidence' / ident
                    directory.mkdir(parents=True, exist_ok=True)
                    atomic_write(directory / ('interrupted-' + interrupted['id'] + '.patch'), patch)
                    shutil.rmtree(workspace)
                    self.git.clone(task['base_commit'], workspace)
                    self.update(ident, session_id=None, session_profile=None)
                else:
                    self.block(ident, 'session_lost', 'session_lost', unblock_condition='operator unblock --fresh-session --note TEXT')
        retained = {op['request']['work'] for task in self.state.db.execute("SELECT metadata FROM tasks WHERE state NOT IN ('COMMITTED','BLOCKED','CANCELLED','SUPPRESSED_DUP')")
                    for op in json.loads(task[0]).get('operations', {}).values() if op.get('request')}
        retained.update(op['request']['work'] for batch in self.state.db.execute("SELECT role_attempts FROM batches WHERE state IN ('PREPARING','WAITING')")
                        for op in json.loads(batch[0]).get('operations', {}).values() if op.get('request'))
        for parent in (self.state.root / 'ws').glob('*'):
            if parent.is_symlink() or not parent.is_dir():
                continue
            for export in parent.iterdir():
                if re.fullmatch(r'(gate|review)-[0-9]+', export.name) and not export.is_symlink() and str(export) not in retained:
                    shutil.rmtree(export)
        # Interrupted batch calls are retried under the existing bounded role budget.
        for row in self.state.db.execute("SELECT id,role_attempts FROM batches WHERE state NOT IN ('DONE','BLOCKED','NO_JUSTIFIED_WORK')").fetchall():
            data = json.loads(row['role_attempts'])
            for key, op in list(data.get('operations', {}).items()):
                call = self.state.db.execute('SELECT status FROM calls WHERE id=?', (op['call'],)).fetchone()
                if call and call[0] == 'interrupted':
                    del data['operations'][key]
            self._save_holder(None, row['id'], data)

    def run(self, *, max_batches=None, until_idle=False):
        if max_batches is not None and (type(max_batches) is not int or max_batches < 1):
            raise ValueError('max_batches must be positive')
        with controller_lock(self.state.root):
            old_handlers = {}
            def cancel_signal(signum, frame):
                if signum != signal.SIGUSR1:
                    self._signal_cancel = True
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGUSR1):
                old_handlers[sig] = signal.signal(sig, cancel_signal)
            try:
                unfinished = self.state.db.execute("SELECT * FROM runs WHERE state='RUNNING' ORDER BY started DESC LIMIT 1").fetchone()
                self.run_id = unfinished['id'] if unfinished else uuid.uuid4().hex
                self.recover()
                with self.state.transaction() as db:
                    db.execute("INSERT INTO runs(id,state,started,pid,proc_start) VALUES(?,'RUNNING',?,?,?) ON CONFLICT(id) DO UPDATE SET pid=excluded.pid,proc_start=excluded.proc_start",
                               (self.run_id, self.clock(), os.getpid(), proc_start(os.getpid())))
                completed = 0
                while max_batches is None or completed < max_batches:
                    if self.cancelled():
                        return self._finish(130, 'CANCELLED')
                    if self.state.db.execute('SELECT satisfied_at FROM owner').fetchone()[0] is not None:
                        return self._finish(0, 'OWNER_SATISFIED')
                    fp = self.input_fingerprint()
                    previous = self.state.db.execute("SELECT * FROM batches WHERE state IN ('DONE','NO_JUSTIFIED_WORK') ORDER BY rowid DESC LIMIT ?", (self.config.budgets['stall_batches'],)).fetchall()
                    if len(previous) == self.config.budgets['stall_batches'] and all(r['accepted'] == 0 and r['input_fp'] == fp for r in previous):
                        return self._finish(3, 'STALLED_NO_PROGRESS')
                    active = self.state.db.execute("SELECT * FROM batches WHERE state IN ('PREPARING','WAITING','TASKS') ORDER BY rowid LIMIT 1").fetchone()
                    if active:
                        batch = active['id']
                    else:
                        batch = uuid.uuid4().hex
                        with self.state.transaction() as db:
                            n = db.execute('SELECT COALESCE(max(n),0)+1 FROM batches WHERE run=?', (self.run_id,)).fetchone()[0]
                            db.execute("INSERT INTO batches(id,run,n,state,input_fp) VALUES(?,?,?,'PREPARING',?)", (batch, self.run_id, n, fp))
                    row = self.state.db.execute('SELECT * FROM batches WHERE id=?', (batch,)).fetchone()
                    if row['state'] != 'TASKS':
                        try:
                            proposals = self.driver.prepare(self, batch)
                            current = self.state.db.execute('SELECT * FROM batches WHERE id=?', (batch,)).fetchone()
                            if current['state'] == 'BLOCKED':
                                return self._finish(2, 'BLOCKED_CONFIG')
                            if proposals is None:
                                wait = json.loads(current['blocked'] or '{}').get('recheck_at')
                                if wait is None:
                                    if current['role_attempts'] != row['role_attempts']:
                                        continue
                                    raise EngineBlocked('batch preparation has no result or finite wait')
                                if until_idle:
                                    return 75
                                self.sleep(max(0, wait - self.clock()))
                                continue
                            self.add_tasks(batch, proposals, fp)
                        except (EngineBlocked, sandbox.SandboxError, GitError) as exc:
                            with self.state.transaction() as db:
                                db.execute("UPDATE batches SET state='BLOCKED',blocked=COALESCE(blocked,?) WHERE id=?", (json.dumps(self.blocked_record(None, str(exc), getattr(exc, 'outcome', 'config_block'))), batch))
                            return self._finish(2, 'BLOCKED_CONFIG')
                        with self.state.transaction() as db:
                            db.execute("UPDATE batches SET state=? WHERE id=?", ('TASKS' if proposals else 'NO_JUSTIFIED_WORK', batch))
                        if not proposals:
                            print('no justified work; owner satisfaction not established')
                            return self._finish(0, 'NO_JUSTIFIED_WORK')
                    code = self.drain(batch, until_idle=until_idle)
                    if code == 75:
                        return 75
                    with self.state.transaction() as db:
                        accepted = db.execute("SELECT count(*) FROM tasks WHERE batch=? AND state='COMMITTED'", (batch,)).fetchone()[0]
                        db.execute("UPDATE batches SET state='DONE',accepted=? WHERE id=?", (accepted, batch))
                    completed += 1
                    if code:
                        return self._finish(code, 'BLOCKED_CONFIG' if code == 2 else 'CANCELLED')
                return self._finish(0, 'VALIDATION_BOUND')
            finally:
                for sig, handler in old_handlers.items():
                    signal.signal(sig, handler)

    def _finish(self, code, status):
        with self.state.transaction() as db:
            if code == 130:
                for row in db.execute("SELECT id,state FROM tasks WHERE state NOT IN ('COMMITTED','BLOCKED','CANCELLED','SUPPRESSED_DUP')").fetchall():
                    if not db.execute('SELECT 1 FROM commits WHERE task=?', (row['id'],)).fetchone():
                        db.execute("UPDATE tasks SET prior_state=state,state='CANCELLED' WHERE id=?", (row['id'],))
                        db.execute('INSERT INTO cancels(target,requested) VALUES(?,?)', (row['id'], self.clock()))
                db.execute("UPDATE cancels SET handled=? WHERE target IN ('all','*')", (self.clock(),))
            db.execute('UPDATE runs SET state=?,ended=? WHERE id=?', (status, self.clock(), self.run_id))
        return code


def status(state):
    return {table: [dict(r) for r in state.db.execute('SELECT * FROM ' + table)]
            for table in ('runs', 'batches', 'tasks', 'accounts', 'profiles_disabled', 'owner', 'smoke')}


def request_cancel(state, target):
    with state.transaction() as db:
        db.execute('INSERT INTO cancels(target,requested) VALUES(?,?)', (target, time.time()))
    for row in state.db.execute("SELECT pid,proc_start FROM runs WHERE state='RUNNING'"):
        try:
            if proc_start(row['pid']) == row['proc_start']:
                os.kill(row['pid'], signal.SIGUSR1)
        except (OSError, TypeError):
            pass
    accepted = state.db.execute('SELECT state FROM commits WHERE task=?', (target,)).fetchone()
    return 'already accepted' if accepted else 'cancellation requested'


def satisfy(state, note):
    if not note.strip():
        raise EngineBlocked('operator note required')
    with state.transaction() as db:
        db.execute('UPDATE owner SET satisfied_at=?,note=? WHERE id=1', (time.time(), note))


def unblock(engine, ident, note, fresh_session=False):
    if not note.strip():
        raise EngineBlocked('operator note required')
    batch = engine.state.db.execute('SELECT * FROM batches WHERE id=?', (ident,)).fetchone()
    if batch is not None:
        data = json.loads(batch['role_attempts'])
        if fresh_session or batch['state'] != 'BLOCKED' or data.get('operator_unblock'):
            raise EngineBlocked('only one operator budget reset for a blocked batch; no session handoff')
        # Retain successful reads/comparisons and historical call accounting. Retry
        # only failed content with its corrected input; never turn failure into success.
        data['operator_unblock'] = {'at': engine.clock(), 'note': note, 'previous_blocked': json.loads(batch['blocked'] or '{}')}
        for role in ('po_read', 'po', 'architect', 'adr_review'):
            data.pop(role, None)
        with engine.state.transaction() as db:
            db.execute("UPDATE batches SET state='PREPARING',blocked=NULL,role_attempts=? WHERE id=?", (json.dumps(data), ident))
        return
    row = engine.task(ident)
    if row['state'] not in ('BLOCKED', 'CANCELLED') or row['unblock_count']:
        raise EngineBlocked('only one operator budget reset for a blocked/cancelled task')
    cause = json.loads(row['blocked'] or '{}').get('outcome')
    if cause == 'session_lost' and not fresh_session:
        raise EngineBlocked('session_lost requires explicit --fresh-session')
    metadata = json.loads(row['metadata'])
    metadata['operations'] = {}
    restart_red = row['kind'] == 'bug' and row['prior_state'] == 'GATING' and engine._receipt(metadata, 'red') is None
    if restart_red:
        # The operator authorizes one restart, never retroactive RED approval.
        # Preserve every byte under an immutable local ref before replacing the
        # writable clone with its original base. Retain the actual implementer SID.
        intent = metadata.get('operator_red_restart')
        if intent is None:
            preserved = engine.git.candidate('operator-red-' + ident, Path(row['workspace']), row['base_commit'], 'Preserve failed regression-first candidate before operator restart')
            intent = {'candidate': preserved.__dict__, 'restart_base': engine.git.head(), 'note': note, 'at': engine.clock(), 'session_id': row['session_id'],
                      'workspace_backup': str(Path(row['workspace']).parent / ('operator-red-preserved-' + uuid.uuid4().hex))}
            metadata['operator_red_restart'] = intent
            with engine.state.transaction() as db:
                db.execute('UPDATE tasks SET metadata=? WHERE id=?', (json.dumps(metadata), ident))
        backup = Path(intent['workspace_backup'])
        if not backup.exists():
            Path(row['workspace']).rename(backup)
        # A failed clone after the durable intent is safely repeatable. Original
        # bytes, including ignored files, remain in backup and the candidate ref.
        if Path(row['workspace']).exists():
            shutil.rmtree(row['workspace'])
        restart_base = intent.get('restart_base', row['base_commit'])
        engine.git.clone(restart_base, row['workspace'])
        metadata.pop('red', None)
    with engine.state.transaction() as db:
        db.execute('INSERT INTO unblocks(task,at,note,fresh_session) VALUES(?,?,?,?)', (ident, engine.clock(), note, int(fresh_session)))
        db.execute('UPDATE tasks SET state=?,blocked=NULL,attempts=0,plan_attempts=0,impl_attempts=0,fix_rounds=0,rebases=0,unblock_count=unblock_count+1,metadata=? WHERE id=?',
                   ('RED_WRITING' if restart_red else row['prior_state'] or 'READY', json.dumps(metadata), ident))
        if restart_red:
            db.execute('UPDATE tasks SET base_commit=?,red_commit=NULL,candidate_commit=NULL,candidate_tree=NULL,candidate_ref=NULL WHERE id=?', (restart_base, ident))
        db.execute("UPDATE cancels SET handled=? WHERE target=?", (engine.clock(), ident))
        db.execute("UPDATE batches SET state='TASKS' WHERE id=?", (row['batch'],))
    if fresh_session:
        engine.handoff(ident, 'operator authorized fresh session: ' + note, operator=True)
