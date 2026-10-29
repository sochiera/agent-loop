"""Bounded KISS goal loop: goal -> native writable agent -> trusted gate/review -> CAS -> checkpoint.

The agent owns product choices and runs the project's own build/test commands.
The controller owns admission, execution, retries, persistence, protected-path
checks, sandboxed verification and compare-and-swap acceptance on a dedicated,
preexisting, unchecked-out loop ref. One checkpoint file holds the current
operation (`active`) and at most one acceptance (`pending`); it is not a journal.

Exit codes: 0 DONE, 2 BLOCKED, 3 step budget used, 4 LOCKED (controller lock
contention only, nothing written), 75 WAITING, 130 cancelled/STOPPED.
The legacy Engine lifecycle is never imported or dispatched from here.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import re
from pathlib import Path
import subprocess
import sys
import time
import uuid

from forge import quota_metrics as qm
from forge import sandbox
from forge.adapters import adapter_for
from forge.adapters.base import Call
from forge.classify import QUOTA_BASE
from forge.gate import matches
from forge.gitops import Candidate, Git, GitError, crash
from forge.prompts import REVIEW_CONTRACT, WRITER_CONTRACT, render, spec_text  # noqa: F401 (re-exported)
from forge.runner import Result, recover_calls, run_call, terminate_group
from forge.state import ControllerLocked, State, StateError, atomic_json, atomic_write, controller_lock

DONE, BLOCKED, BUDGET, LOCKED, WAITING, CANCELLED = 0, 2, 3, 4, 75, 130
CAPSULE_STATUS = {DONE: 'DONE', BLOCKED: 'BLOCKED', BUDGET: 'RUNNING', WAITING: 'WAITING', CANCELLED: 'STOPPED'}
WRITER_CAPS, REVIEW_CAPS = frozenset({'text', 'resume'}), frozenset({'text', 'readonly'})
LEAD_CAPS = frozenset({'text', 'readonly', 'resume'})
WAIT_OUTCOMES = frozenset({'quota_wait', 'auth_wait', 'env_wait'})
RESUME_OUTCOMES = frozenset({'timeout', 'turn_limit', 'interrupted', 'cancelled'})
GATE_TAIL, HISTORY, TEXT_LIMIT = 60, 5, 2000
TASK_KINDS = frozenset({'feature', 'bug', 'refactor', 'cleanup', 'docs', 'mixed'})
FIELD_LIMIT, ITEM_LIMIT, ITEMS, SPEC_LIMIT = 4000, 1000, 20, 16000
@dataclass(frozen=True)
class Admission:
    """Quota admission verdict: ALLOW, WAIT(reason, finite recheck_at) or BLOCK(reason)."""
    decision: str
    reason: str = ''
    recheck_at: float | None = None
    evidence: str | None = None


ALLOW = Admission('ALLOW')


def wait(reason, recheck_at, evidence=None):
    return Admission('WAIT', reason, recheck_at, evidence)


def block(reason, evidence=None):
    return Admission('BLOCK', reason, None, evidence)


class Stop(Exception):
    """End this invocation with rc; durable stops record BLOCKED in the checkpoint."""
    def __init__(self, rc, reason='', durable=False):
        super().__init__(reason)
        self.rc, self.reason, self.durable = rc, reason, durable


# Native seams. Tests replace these with SIMULATED fixtures; production uses the real adapters.
def _adapter(profile):
    return adapter_for(profile.harness)


def _binary(config, roots, profile):
    from forge.doctor import binary_version
    harness = config.harness[profile.harness]
    binary = sandbox.resolve_binary(profile.harness, harness.get('binary'))
    return binary, {'path': str(binary), 'version': binary_version(roots, profile.harness, binary, harness.get('runtime_dir'))}


def _capabilities(config, state, profile):
    """Smoke-verified capabilities, rechecked against the current binary fingerprint."""
    from forge.engine import NativeRoleDriver
    return NativeRoleDriver(config, state).verified(profile)


def goal_identity(goal_path):
    data = Path(goal_path).read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    return data.decode('utf-8'), sha, sha[:16]


def loop_status(config, goal_path):
    """Read-only checkpoint view; never creates or migrates state."""
    _, _, goal_id = goal_identity(goal_path)
    path = Path(config.state_dir) / 'loop' / goal_id / 'checkpoint.json'
    return json.loads(path.read_text()) if path.is_file() else None


def run_loop(config, goal_path, *, max_steps=1, admit, should_cancel=lambda: False, clock=time.time) -> int:
    """One bounded invocation under the controller lock. `admit` is required and in-process.

    `should_cancel()` may return a truthy stop request or a WAIT Admission (quota monitor);
    either terminates only this runner's recorded process group.
    """
    if not callable(admit):
        return _refuse('quota admission callback is required')
    if getattr(config, 'loop', None) is None:
        return _refuse('[loop] configuration is required')
    if type(max_steps) is not int or max_steps < 1:
        raise ValueError('max_steps must be a positive integer')
    try:
        with controller_lock(config.state_dir):
            return _Loop(config, goal_path, admit, should_cancel, clock).run(max_steps)
    except ControllerLocked:
        return LOCKED


def run_with_capsule(config, goal_path, capsule_path, *, max_steps=1, admit, should_cancel=lambda: False,
                     clock=time.time) -> int:
    """Trusted wrapper core. Never takes the controller lock; rc4 writes nothing.

    A missing/unreadable capsule fails closed and is never recreated. A STOPPED
    capsule (status or parent marker) refuses to run and is never modified.
    """
    capsule = _read_capsule(capsule_path)
    if capsule is None:
        return _refuse(f'capsule {capsule_path} is missing or unreadable')
    if _stopped(capsule):
        print(f'forge loop: capsule {capsule_path} is STOPPED', file=sys.stderr)
        return CANCELLED

    def cancel():
        latest = _read_capsule(capsule_path)
        return True if latest is None or _stopped(latest) else should_cancel()
    rc = run_loop(config, goal_path, max_steps=max_steps, admit=admit, should_cancel=cancel, clock=clock)
    if rc != LOCKED:
        status = loop_status(config, goal_path) or {}
        update_capsule(capsule_path, rc, ((status.get('waiting') or {}).get('recheck_at')))
    return rc


def update_capsule(path, rc, recheck_at=None) -> bool:
    """Reread the capsule now and change only status/recheck_at; parent STOPPED always wins."""
    if rc not in CAPSULE_STATUS:
        return False
    capsule = _read_capsule(path)
    if capsule is None or _stopped(capsule):
        return False
    status = CAPSULE_STATUS[rc]
    atomic_json(Path(path), {**capsule, 'status': status, 'recheck_at': recheck_at if status == 'WAITING' else None})
    return True


def _read_capsule(path):
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _stopped(capsule):
    marker = capsule.get('stopped_marker')
    return capsule.get('status') == 'STOPPED' or capsule.get('stopped') is True or bool(marker and Path(marker).exists())


def _refuse(reason):
    print('forge loop BLOCKED: ' + reason, file=sys.stderr)
    return BLOCKED


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _valid_writer(value):
    return (value.get('status') in ('progress', 'done', 'blocked') and isinstance(value.get('summary'), str)
            and isinstance(value.get('next'), str) and (value.get('blocker') is None or isinstance(value['blocker'], str))
            and (value['status'] != 'blocked' or bool(value.get('blocker'))))


def _valid_review(value):
    findings = value.get('findings')
    return value.get('verdict') in ('PASS', 'FAIL') and isinstance(findings, list) and all(isinstance(f, str) for f in findings)


def _valid_lead_review(value):
    findings = value.get('findings')
    return (value.get('verdict') in ('PASS', 'FAIL', 'BLOCK') and isinstance(findings, list)
            and all(isinstance(f, str) for f in findings) and (value['verdict'] == 'PASS' or bool(findings)))


def _text(value, limit=FIELD_LIMIT):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _items(value, required=True):
    return (isinstance(value, list) and len(value) <= ITEMS and (bool(value) or not required)
            and all(_text(item, ITEM_LIMIT) for item in value))


def _scope_path(path):
    parts = path.split('/')
    return not path.startswith('/') and '\\' not in path and '\x00' not in path and '..' not in parts and '' not in parts


def _valid_lead(value):
    """One bounded Lead decision: task, blocked (one necessary question) or done."""
    try:
        if len(json.dumps(value)) > SPEC_LIMIT:
            return False
    except (TypeError, ValueError):
        return False
    kind = value.get('disposition')
    keys = {'task': {'disposition', 'kind', 'mixture_rationale', 'objective', 'scope_in', 'scope_rationale', 'scope_out',
                     'invariants', 'acceptance', 'tests', 'docs_impact', 'tier', 'tier_rationale', 'question'},
            'blocked': {'disposition', 'blocker', 'question'},
            'done': {'disposition', 'summary', 'evidence', 'limitations'}}.get(kind)
    if keys is None or set(value) - keys:
        return False
    if kind == 'blocked':
        return _text(value.get('blocker')) and _text(value.get('question'))
    if kind == 'done':
        return _text(value.get('summary')) and _items(value.get('evidence')) and _items(value.get('limitations'), False)
    optional = ('mixture_rationale', 'scope_rationale')
    return (value.get('kind') in TASK_KINDS and _text(value.get('objective')) and _items(value.get('scope_in'))
            and all(_scope_path(path) for path in value['scope_in']) and _items(value.get('scope_out'), False)
            and _items(value.get('invariants'), False) and _items(value.get('acceptance')) and _items(value.get('tests'))
            and _text(value.get('docs_impact')) and value.get('tier') in ('cheap', 'normal')
            and _text(value.get('tier_rationale')) and value.get('question') is None
            and all(value.get(key) is None or _text(value[key]) for key in optional)
            and (value['kind'] != 'mixed' or _text(value.get('mixture_rationale')))
            and (not any(all(part and set(part) <= {'*'} for part in path.split('/'))
                         for path in value['scope_in']) or _text(value.get('scope_rationale'))))


def _validator(stage, lead_mode):
    if stage == 'lead':
        return _valid_lead
    if stage == 'writer':
        return _valid_writer
    return _valid_lead_review if lead_mode else _valid_review


LEAD_STATE = {'mode': 'lead', 'lead': None, 'task': None, 'task_seq': 0, 'tasks_accepted': 0,
              'reconciliations': 0, 'last_task': None, 'handoff': None}


def _caps(stage):
    return {'lead': LEAD_CAPS, 'writer': WRITER_CAPS}.get(stage, REVIEW_CAPS)


def _child_policy(profile, policy):
    """Task-local native child policy; unproven routes are explicitly off."""
    from forge import native_children
    setting = policy.get('native_children', 'off')
    return native_children.policy_for(profile, setting, bool(native_children.VERIFIED_ROUTES.get(profile.harness)))


def _lead_shape(cp):
    """Validate supported extension fields before even a terminal checkpoint is adopted."""
    def pin(value):
        return value is None or (isinstance(value, dict) and set(value) == {'profile', 'sid'}
                                 and _text(value.get('profile')) and _text(value.get('sid')))
    try:
        task = cp['task']
        counters = ('task_seq', 'tasks_accepted', 'reconciliations')
        return (cp['mode'] == 'lead' and all(pin(cp[k]) for k in ('lead', 'writer', 'reviewer'))
                and all(type(cp[k]) is int and cp[k] >= 0 for k in counters)
                and cp['reconciliations'] <= 1
                and (task is None or (isinstance(task, dict) and isinstance(task.get('spec'), dict)
                     and _valid_lead(task['spec']) and type(task.get('fixes')) is int and 0 <= task['fixes'] <= 2
                     and type(task['seq']) is int and task['id'] == f"T{task['seq']}"
                     and task['seq'] == cp['task_seq'] and re.fullmatch('[0-9a-f]{40}', task['base'])
                     and task['spec_sha'] == _spec_sha(task['spec'])))
                and cp['stage'] in ('lead', 'writer', 'gate', 'reviewer', 'accept'))
    except (KeyError, TypeError, AttributeError):
        return False


def _spec_sha(spec):
    return hashlib.sha256(spec_text(spec).encode()).hexdigest()


def _in_scope(path, scope):
    return matches(path, scope) or any(path.startswith(item.rstrip('/') + '/') for item in scope)


class _Loop:
    def __init__(self, config, goal_path, admit, should_cancel, clock):
        self.config, self.admit, self.should_cancel, self.clock = config, admit, should_cancel, clock
        self.policy = config.loop
        self.root = Path(config.state_dir)
        self.goal_text, self.goal_sha, self.goal_id = goal_identity(goal_path)
        self.dir = self.root / 'loop' / self.goal_id
        self.path = self.dir / 'checkpoint.json'
        self.ws = self.root / 'ws' / self.goal_id / 'src'
        self.profiles = {p.id: p for p in config.profiles}
        self.lead_mode = self.policy.get('mode', 'direct') == 'lead'
        self.cp = self.state = None
        self.obs = None
        self.active_at = None
        if clock is time.time:
            wall, monotonic = clock(), time.monotonic()
            self.clock = lambda: wall + time.monotonic() - monotonic
        self.cancel_reason = None

    # -- invocation -------------------------------------------------------------------------
    def run(self, max_steps):
        try:
            return self._run(max_steps)
        except Stop as stop:
            if stop.durable and self.cp is not None:
                self.cp.update(status='BLOCKED', blocker=stop.reason)
                self._save()
            print(f'forge loop {"BLOCKED" if stop.rc == BLOCKED else stop.rc}: {stop.reason}', file=sys.stderr)
            return stop.rc
        except (GitError, StateError, sandbox.SandboxError, OSError, RuntimeError) as exc:
            print(f'forge loop BLOCKED: {type(exc).__name__}: {exc}', file=sys.stderr)
            return BLOCKED
        finally:
            if self.state is not None:
                if self.obs is not None:
                    with self.state.transaction() as db:
                        last = db.execute('SELECT last_effective FROM quota_observation WHERE id=?', (self.obs,)).fetchone()[0]
                        qm.close_span(db, self.obs, max(last, self.active_at))
                self.state.close()

    def _run(self, max_steps):
        loops = self.root / 'loop'
        others = sorted(p.name for p in loops.iterdir() if p.name != self.goal_id) if loops.is_dir() else []
        if others:
            raise Stop(BLOCKED, f'goal changed: state root is pinned to goal {others[0]}; use a fresh state root')
        identity = {'goal_sha': self.goal_sha, 'config_sha': _sha(self.config.path),
                    'target': str(self.config.target_repo), 'ref': 'refs/heads/' + self.policy['branch']}
        self.cp = json.loads(self.path.read_text()) if self.path.is_file() else None
        if self.cp is not None:
            if (not isinstance(self.cp, dict) or type(self.cp.get('version')) is not int
                    or self.cp['version'] != 1 or not isinstance(self.cp.get('identity'), dict)):
                raise Stop(BLOCKED, 'unsupported or corrupt checkpoint version/identity; nothing adopted')
            for key, value in identity.items():
                if self.cp['identity'].get(key) != value:
                    raise Stop(BLOCKED, f'{key} changed: checkpoint {self.cp["identity"].get(key)} != current {value}')
        if self.cp is not None and self.lead_mode and (not _lead_shape(self.cp)
                or (self.cp['task'] and self.cp['task']['fixes'] > self.policy['fix_rounds'])):
            raise Stop(BLOCKED, 'lead checkpoint extension is missing or corrupt; nothing adopted')
        self.git = self._git()
        if self.cp is not None and self.cp['status'] in ('DONE', 'BLOCKED'):
            self._summary()
            if self.cp['status'] == 'BLOCKED':
                return BLOCKED
            if self.git.head() != self.cp['accepted_commit']:
                raise Stop(BLOCKED, 'loop ref moved after DONE; accepted commit ' + self.cp['accepted_commit'])
            return DONE
        self.state = State(self.root, metrics=True)
        self.roots = sandbox.project_roots(self.config)
        if self.cp is None:
            self.cp = {'version': 1, 'identity': identity, 'goal_id': self.goal_id, 'status': 'RUNNING',
                       'stage': 'writer', 'step': 0, 'failures': 0, 'expected_base': self.git.head(),
                       'writer': None, 'reviewer': None, 'interrupted': False, 'summary': '', 'next': '',
                       'blocker': None, 'feedback': None, 'history': [], 'waiting': None, 'candidate': None,
                       'active': None, 'pending': None, 'rejected': [], 'accepted_commit': None, 'accepted_tree': None}
            if self.lead_mode:
                self.cp.update(LEAD_STATE, stage='lead')
            self._save()
        elif self.lead_mode and not _lead_shape(self.cp):
            raise Stop(BLOCKED, 'lead checkpoint extension is missing or corrupt; nothing adopted')
        if not self.ws.exists():
            if self.cp['writer'] or self.cp['candidate'] or self.cp['pending'] or self.cp['step']:
                raise Stop(BLOCKED, f'workspace {self.ws} is missing', durable=True)
            self._clone(self.ws)
        self._check_unfinished()
        initial_stop = self._poll_cancel()
        if initial_stop is not None:
            return initial_stop
        self.active_at = self.clock()
        with self.state.transaction() as db:
            self.obs = qm.ensure_observation(db, self.goal_id, self.active_at)
        launched = 0
        while True:
            if self.cp['pending']:
                rc = self._finish_pending()
            elif self.cp['active']:
                rc = self._reconcile_active()
            elif self.cp['status'] == 'DONE':
                return DONE
            elif self.cp['status'] == 'BLOCKED':
                return BLOCKED
            elif self.cp['status'] == 'WAITING' and self.clock() < self.cp['waiting']['recheck_at']:
                self._summary()
                return WAITING
            else:
                if self.cp['status'] == 'WAITING':
                    self.cp.update(status='RUNNING', waiting=None)
                    self._save()
                rc = self._poll_cancel()
                if rc is None:
                    stage = self.cp['stage']
                    # Direct mode budgets writer launches; lead mode budgets every native launch.
                    if stage == 'writer' or (self.lead_mode and stage in ('lead', 'reviewer')):
                        if launched >= max_steps:
                            self._summary()
                            return BUDGET
                        launched += 1
                    if stage == 'lead':
                        rc = self._lead()
                    elif stage == 'writer':
                        rc = self._writer()
                    elif stage == 'gate':
                        rc = self._gate()
                    elif stage == 'reviewer':
                        rc = self._reviewer()
                    else:
                        rc = self._accept()
            if rc is not None:
                return rc

    def _git(self):
        """Dedicated loop ref: must exist, must be direct and must not be checked out in any worktree."""
        branch = self.policy['branch']
        self._owned()
        try:
            git = Git(self.config.target_repo, self.root / 'git', branch)
            git.head()
        except GitError as exc:
            raise Stop(BLOCKED, f'loop ref refs/heads/{branch} is unavailable; provision it explicitly: {exc}') from exc
        return git

    def _owned(self):
        """Trusted metadata check, repeated before every launch and CAS (not only once per invocation)."""
        branch = self.policy['branch']
        git_dir = Path(self.config.target_repo) / '.git'
        if git_dir.is_symlink() or not git_dir.is_dir():
            raise Stop(BLOCKED, 'unsupported git layout: target needs an ordinary trusted .git directory')
        heads = [git_dir / 'HEAD']
        worktrees = git_dir / 'worktrees'
        if worktrees.exists() or worktrees.is_symlink():
            if worktrees.is_symlink() or not worktrees.is_dir():
                raise Stop(BLOCKED, 'unsupported git layout: unreadable worktree metadata')
            heads += sorted(entry / 'HEAD' for entry in worktrees.iterdir())
        for head in heads:
            try:
                text = head.read_text().strip()
            except OSError as exc:
                raise Stop(BLOCKED, f'unreadable worktree metadata {head}: {exc}') from exc
            if text == 'ref: refs/heads/' + branch:
                raise Stop(BLOCKED, f'loop ref refs/heads/{branch} is checked out ({head})')
        loose = git_dir / 'refs' / 'heads' / branch
        try:
            symbolic = loose.is_symlink() or (loose.is_file() and loose.read_bytes().startswith(b'ref:'))
        except OSError as exc:
            raise Stop(BLOCKED, f'unreadable loop ref {loose}: {exc}') from exc
        if symbolic:
            raise Stop(BLOCKED, f'loop ref refs/heads/{branch} is a symbolic alias; a direct dedicated ref is required')

    def _clone(self, destination):
        temporary = destination.parent / ('clone-' + uuid.uuid4().hex)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.git.clone(self.cp['expected_base'], temporary)
        temporary.rename(destination)

    def _check_unfinished(self):
        active = (self.cp.get('active') or {}).get('call_id')
        rows = self.state.db.execute("SELECT id,role FROM calls WHERE status IN ('launching','running')").fetchall()
        stray = [r['id'] for r in rows if r['id'] != active and r['role'] != 'probe']
        if stray:
            raise Stop(BLOCKED, 'unlinked unfinished calls require operator review: ' + ', '.join(stray))
        if any(r['role'] == 'probe' for r in rows) and active not in {r['id'] for r in rows}:
            recover_calls(self.state)

    def _save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        atomic_json(self.path, self.cp)
        self._summary()

    def _summary(self):
        cp = self.cp
        waiting = cp.get('waiting') or {}
        lines = ['# Forge loop result', '', f"status: {cp['status']}", f"stage: {cp['stage']}", f"step: {cp['step']}",
                 f"failures: {cp['failures']}", f"goal_sha256: {cp['identity']['goal_sha']}",
                 f"expected_base: {cp['expected_base']}", f"accepted_commit: {cp['accepted_commit']}",
                 f"accepted_tree: {cp['accepted_tree']}"]
        if waiting:
            lines.append(f"waiting: {waiting.get('reason')} until {waiting.get('recheck_at')}")
        for key in ('blocker', 'summary', 'next', 'feedback'):
            if cp.get(key):
                lines.append(f'{key}: {str(cp[key])[:TEXT_LIMIT]}')
        self.dir.mkdir(parents=True, exist_ok=True)
        atomic_write(self.dir / 'result.md', ('\n'.join(lines) + '\n').encode())

    def _poll_cancel(self, stage=None):
        value = self.should_cancel()
        if not value:
            self._metric_touch()
            return None
        if isinstance(value, Admission) and value.decision == 'WAIT':
            return self._wait(stage or self.cp['stage'], 'monitor: ' + value.reason, value.recheck_at)
        return CANCELLED

    def _wait(self, stage, reason, recheck_at, durable_timer=False):
        if not isinstance(recheck_at, (int, float)) or not math.isfinite(recheck_at) or recheck_at <= self.clock():
            if not durable_timer:
                raise Stop(BLOCKED, f'WAIT without a finite future recheck_at ({reason})')
            recheck_at = self.clock() + QUOTA_BASE
        self.cp.update(status='WAITING', stage=stage, active=None, metric_attempt=None,
                       waiting={'stage': stage, 'reason': reason, 'recheck_at': float(recheck_at)})
        self._save()
        return WAITING

    def _drift(self, label):
        self._owned()
        head = self.git.head()
        if head != self.cp['expected_base']:
            raise Stop(BLOCKED, f'expected-base drift before {label}: loop ref is {head}, '
                                f'expected {self.cp["expected_base"]}; nothing adopted')

    # -- selection and launch ---------------------------------------------------------------
    def _select(self, stage):
        pin = self.cp[stage]
        ids = [pin['profile']] if pin else list(self.policy[{'lead': 'leads', 'writer': 'writers'}.get(stage, 'reviewers')])
        if stage == 'writer' and self.lead_mode and not pin:
            # The Lead's tier filters executors by declared profile tiers, never by model name.
            tier = self.cp['task']['spec']['tier']
            ids = [i for i in ids if tier in self.profiles[i].tiers]
        caps = _caps(stage)
        disabled = {r[0] for r in self.state.db.execute('SELECT profile FROM profiles_disabled')}
        author = self.profiles.get((self.cp['candidate'] or {}).get('writer_profile'))
        now, reasons, waits, deferred = self.clock(), {}, [], []
        for ident in ids:
            p = self.profiles.get(ident)
            if p is not None:
                self._terra_gate(p)
            if p is None or not p.enabled or not self.config.accounts[p.account]['enabled'] or ident in disabled:
                reasons[ident] = 'disabled profile/account'
            elif stage == 'reviewer' and (author is None or p.family == author.family):
                reasons[ident] = 'same family as writer'
            elif not caps <= _capabilities(self.config, self.state, p):
                reasons[ident] = 'smoke capabilities missing: ' + ','.join(sorted(caps))
            else:
                account = self.state.db.execute('SELECT state,recheck_at,outcome,reason FROM accounts WHERE id=?', (p.account,)).fetchone()
                if account and account['state'] == 'waiting' and account['recheck_at'] and account['recheck_at'] > now:
                    reasons[ident] = 'account waiting'
                    deferred.append((p, account))
                    waits.append(account['recheck_at'])
                else:
                    return p, None
        if waits:
            for p, account in deferred:
                self._metric_wait(p, stage, 'prestart_deferral', 'cooldown:' + uuid.uuid4().hex,
                                  account['outcome'], account['reason'], fallback=bool(pin))
            return None, self._wait(stage, 'account waiting: ' + json.dumps(reasons, sort_keys=True), min(waits))
        raise Stop(BLOCKED, f'no admissible {stage} profile: ' + json.dumps(reasons, sort_keys=True))

    def _admission(self, p, stage):
        now = self.clock()
        attempt = self.cp.get('metric_attempt')
        if not attempt or (attempt['profile'], attempt['stage']) != (p.id, stage):
            attempt = {'id': uuid.uuid4().hex, 'profile': p.id, 'stage': stage}
            self.cp['metric_attempt'] = attempt
            self._save()
        try:
            decision = self.admit(p.account, p.id, stage, now)
        except Exception as exc:  # The callback is trusted code, but a failure never grants a call.
            raise Stop(BLOCKED, f'admission failed: {type(exc).__name__}: {exc}') from exc
        if not isinstance(decision, Admission) or decision.decision not in ('ALLOW', 'WAIT', 'BLOCK'):
            raise Stop(BLOCKED, 'untrusted admission result: ' + repr(decision)[:200])
        if decision.decision == 'BLOCK':
            self._metric_wait(p, stage, 'policy', attempt['id'], decision.reason, decision.evidence)
            raise Stop(BLOCKED, 'admission BLOCK: ' + decision.reason, durable=True)
        if decision.decision == 'WAIT':
            self._metric_wait(p, stage, 'prestart_deferral', attempt['id'], decision.reason, decision.evidence,
                              fallback=bool(self.cp[stage]))
            crash('loop_after_metric_admission')
            if stage == 'reviewer' and not self.lead_mode:
                self.cp['reviewer'] = self.cp['reviewer'] or {'profile': p.id, 'sid': None}
            return self._wait(stage, 'admission: ' + decision.reason, decision.recheck_at)
        self.cp['metric_attempt'] = None
        return None

    def _prepare(self, stage, p, prompt, work, writable, resume, **extra):
        call_id = uuid.uuid4().hex
        preset = None if resume or p.harness not in ('claude', 'grok') else str(uuid.uuid4())
        return {'call_id': call_id, 'stage': stage, 'profile': p.id, 'account': p.account, 'model': p.model,
                'effort': p.effort, 'preset_sid': preset, 'resume_sid': resume, 'prompt': prompt, 'work': str(work),
                'writable': writable, 'raw_dir': str(self.root / 'calls' / call_id),
                'expected_base': self.cp['expected_base'], 'step': self.cp['step'], 'failures': self.cp['failures'], **extra}

    def _start(self, a):
        """Revalidate smoke fingerprint and ref, persist `active`, then enter the runner."""
        p = self.profiles[a['profile']]
        self._terra_gate(p)
        caps = _caps(a['stage'])
        if a['resume_sid']:
            caps = caps | {'resume'}
        if not caps <= _capabilities(self.config, self.state, p):
            raise Stop(BLOCKED, f'smoke fingerprint/capabilities changed for {p.id}; run doctor --smoke')
        self._drift(a['stage'] + ' launch')
        from dataclasses import asdict
        from forge.native_children import capability_for, policy_hash
        binary, info = _binary(self.config, self.roots, p)
        info = dict(info, sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
        capability = capability_for(p, info, self.config.harness[p.harness])
        children = _child_policy(p, self.policy)
        child_receipt = {'policy': asdict(children), 'sha256': policy_hash(children), 'capability': capability}
        if a.get('children') not in (None, child_receipt):
            raise Stop(BLOCKED, 'native child policy changed during recovery', durable=True)
        a['children'] = child_receipt
        self.cp['active'] = a
        self._save()
        crash('loop_after_active')
        harness = self.config.harness[p.harness]
        writer = a['stage'] == 'writer'
        limits = {'writer': 'implement', 'lead': 'plan'}.get(a['stage'], 'review')
        timeout = self.policy['step_timeout'] if writer else self.config.timeouts[limits]
        extra = {'children': children, 'observation': self._observation(a), 'child_capability': capability}
        call = Call(a['call_id'], a['stage'], p.id, p.harness, p.model, p.effort, Path(a['work']), a['writable'],
                    a['prompt'], (), a['resume_sid'], a['preset_sid'], timeout, self.config.max_turns[limits], **extra)

        def spec_for(args):
            return sandbox.agent_spec(self.roots, harness=p.harness, binary=binary, native_args=tuple(args),
                                      work=call.workdir, writable=call.writable,
                                      input_dir=self.root / 'calls' / call.id / 'in', binds_rw=harness['binds_rw'],
                                      binds_ro=harness['binds_ro'], runtime_dir=harness.get('runtime_dir'),
                                      extra_env=tuple(self.policy['env'].items()) if writer else (),
                                      ro_binds=self.config.execution['ro_binds'] if writer else ())

        def cancelled():
            value = self.should_cancel()
            if value:
                self.cancel_reason = value
                if isinstance(value, Admission) and value.decision == 'WAIT':
                    a['monitor_wait'] = {'reason': value.reason, 'evidence': value.evidence,
                                         'recheck_at': value.recheck_at}
                    self._save()
                    self._metric_wait(p, a['stage'], 'midflight_interruption', a['call_id'] + ':monitor',
                                      value.reason, value.evidence, call_id=a['call_id'], fallback=True)
            else:
                self._metric_touch()
            return bool(value)
        self.cancel_reason = None
        result = run_call(self.state, call, _adapter(p), spec_for, calls_root=self.root / 'calls', account=p.account,
                          validate=_validator(a['stage'], self.lead_mode), should_cancel=cancelled,
                          binary_info=info, now=self.clock)
        return self._apply(a, result)

    # -- recovery ---------------------------------------------------------------------------
    def _reconcile_active(self):
        a = self.cp['active']
        p = self.profiles[a['profile']]
        self._terra_gate(p)
        from dataclasses import asdict
        from forge.native_children import policy_hash
        expected = _child_policy(p, self.policy)
        if a.get('children') and (a['children']['policy'] != asdict(expected)
                                  or a['children']['sha256'] != policy_hash(expected)):
            raise Stop(BLOCKED, 'native child policy changed during recovery', durable=True)
        row = self.state.db.execute('SELECT * FROM calls WHERE id=?', (a['call_id'],)).fetchone()
        raw = Path(a['raw_dir'])
        if row is None:
            if raw.exists():
                if (raw / 'stdout').exists():
                    raise Stop(BLOCKED, f'call {a["call_id"]} has execution evidence but no call row', durable=True)
                raw.rename(raw.with_name(f'{raw.name}.unlaunched-{time.time_ns()}'))
            p = self.profiles[a['profile']]
            rc = self._admission(p, a['stage'])
            return rc if rc is not None else self._start(a)
        try:
            prepared = json.loads((raw / 'input.json').read_text())
        except (OSError, ValueError):
            prepared = None
        pinned = {'profile': a['profile'], 'role': a['stage'], 'account': a['account'], 'model': a['model'],
                  'effort': a['effort'], 'raw_dir': a['raw_dir'], 'preset_sid': a['preset_sid'], 'session_in': a['resume_sid']}
        if (any(row[key] != value for key, value in pinned.items()) or not isinstance(prepared, dict)
                or prepared != {'role': a['stage'], 'prompt': a['prompt'], 'images': []}):
            raise Stop(BLOCKED, f'call row/input {a["call_id"]} conflicts with the checkpoint', durable=True)
        manifest_path = raw / 'manifest.json'
        if a.get('children') and manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            if manifest.get('children') != a['children']:
                raise Stop(BLOCKED, 'native child manifest/checkpoint mismatch', durable=True)
        result = self._load_result(a)
        if row['status'] == 'done':
            if result is None:
                raise Stop(BLOCKED, f'done call {a["call_id"]} has a missing or corrupt result', durable=True)
            return self._apply(a, result)
        if row['status'] in ('launching', 'running'):
            if result is not None:
                # Same row update as runner.run_call after its durable result file.
                with self.state.transaction() as db:
                    db.execute('UPDATE calls SET status=?, outcome=?, ended=?, actual_model=?, session_out=COALESCE(session_out, ?), '
                               'result_path=?, recheck_at=?, usage=? WHERE id=?',
                               ('done', result.outcome, time.time(), result.actual_model, result.sid,
                                str(raw / 'result.json'), result.recheck_at, json.dumps(result.usage, default=str),
                                a['call_id']))
                return self._apply(a, result)
            recover_calls(self.state)
        return self._resume_or_block(a, count=True)

    def _load_result(self, a):
        """None when no result file exists; a present but corrupt/conflicting result BLOCKS durably."""
        path = Path(a['raw_dir']) / 'result.json'
        if not path.exists():
            return None
        try:
            result = Result(**json.loads(path.read_text()))
            valid = (result.call_id == a['call_id'] and result.raw_dir == a['raw_dir']
                     and isinstance(result.outcome, str) and isinstance(result.effects, dict)
                     and isinstance(result.error_fields, dict) and isinstance(result.usage, dict)
                     and (result.sid is None or isinstance(result.sid, str))
                     and (result.final_json is None or isinstance(result.final_json, dict))
                     and (result.recheck_at is None or isinstance(result.recheck_at, (int, float))))
            if valid and result.outcome == 'ok':
                valid = result.final_json is not None and _validator(a['stage'], self.lead_mode)(result.final_json)
        except (OSError, ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise Stop(BLOCKED, f'durable result of call {a["call_id"]} is corrupt or conflicts with the checkpoint', durable=True)
        return result

    def _session_evidence(self, a):
        """(row.session_out, SIDs streamed in this call's own stdout), or None when either is missing."""
        row = self.state.db.execute('SELECT session_out FROM calls WHERE id=?', (a['call_id'],)).fetchone()
        stdout = Path(a['raw_dir']) / 'stdout'
        if row is None or not stdout.is_file():
            return None
        adapter, sids = _adapter(self.profiles[a['profile']]), set()
        for line in stdout.read_text(errors='replace').splitlines():
            try:
                event = adapter.parse_line(line)
            except ValueError:
                continue
            if event is not None and event.kind == 'sid':
                sids.add(event.data['sid'])
        return row['session_out'], sids

    def _proven_sid(self, a):
        """SID from native session events in this call's own stdout, cross-checked with the row."""
        if 'resume' not in _capabilities(self.config, self.state, self.profiles[a['profile']]):
            return None
        evidence = self._session_evidence(a)
        if evidence is None or len(evidence[1]) != 1:
            return None
        session_out, (sid,) = evidence
        expected = a['resume_sid'] or a['preset_sid']
        if (session_out and session_out != sid) or (expected and sid != expected):
            return None
        return sid

    def _sid_conflicts(self, a, sid):
        """A result SID must match the durable pin, a recorded session_out and any streamed SIDs.

        Adapters that report the SID only from finish() (grok) stream no SID events."""
        evidence = self._session_evidence(a)
        if evidence is None:
            return True
        session_out, sids = evidence
        expected = a['resume_sid'] or a['preset_sid']
        return bool((expected and sid != expected) or (session_out and session_out != sid) or (sids and sids != {sid}))

    def _resume_or_block(self, a, count):
        sid = self._proven_sid(a)
        if sid is None:
            self.cp['active'] = None
            raise Stop(BLOCKED, f'resume_unproven: {a["stage"]} call {a["call_id"]} on {a["profile"]} has no '
                                'established native session evidence; work and call evidence preserved', durable=True)
        self.cp[a['stage']] = {'profile': a['profile'], 'sid': sid}
        self.cp.update(active=None, interrupted=a['stage'] == 'writer' or self.cp['interrupted'],
                       failures=a['failures'] + (1 if count else 0))
        return self._budget()

    def _budget(self, reason='failure budget exhausted'):
        if self.cp['failures'] >= self.policy['max_failures']:
            self.cp.update(status='BLOCKED', blocker=f'{reason} ({self.cp["failures"]}/{self.policy["max_failures"]}); '
                                                     f'last feedback: {self.cp.get("feedback")}')
            self._save()
            return BLOCKED
        self._save()
        return None

    def _effects(self, p, result, a):
        """Account/profile effects exactly once per call (call_effects marker)."""
        with self.state.transaction() as db:
            self._metric_result(db, p, a, result)
            if db.execute('SELECT 1 FROM call_effects WHERE call=?', (result.call_id,)).fetchone():
                return
            now = self.clock()
            if result.outcome == 'ok':
                db.execute("UPDATE accounts SET state='available',last_ok=?,k=0,recheck_at=NULL,outcome='ok' WHERE id=?",
                           (now, p.account))
            elif result.effects.get('account_wait'):
                recheck = result.recheck_at
                if not isinstance(recheck, (int, float)) or not math.isfinite(recheck) or recheck <= now:
                    recheck = now + QUOTA_BASE
                db.execute("UPDATE accounts SET state='waiting',recheck_at=?,k=k+1,outcome=?,reason=? WHERE id=?",
                           (recheck, result.outcome, json.dumps(result.error_fields, default=str)[:4000], p.account))
            db.execute("INSERT OR IGNORE INTO profile_counters(profile,run) VALUES(?,'')", (p.id,))
            counters = db.execute("SELECT * FROM profile_counters WHERE profile=? AND run=''", (p.id,)).fetchone()
            protocol = counters['protocol'] + 1 if result.outcome == 'protocol' else 0
            unknown = counters['unknown_fail'] + 1 if result.outcome == 'unknown_fail' else 0
            db.execute("UPDATE profile_counters SET protocol=?,unknown_fail=? WHERE profile=? AND run=''",
                       (protocol, unknown, p.id))
            disable = result.effects.get('disable')
            if protocol > self.config.budgets['protocol_retries'] or unknown >= self.config.budgets['profile_strikes']:
                disable = 'profile'
            if disable:
                for target in (self.config.profiles if disable == 'sandbox' else (p,)):
                    db.execute('INSERT OR REPLACE INTO profiles_disabled VALUES(?,?,?,?,?)',
                               (target.id, None, json.dumps(result.error_fields, default=str)[:4000], result.outcome, now))
            db.execute('INSERT INTO call_effects VALUES(?,?)', (result.call_id, now))

    # -- result processing --------------------------------------------------------------------
    def _apply(self, a, result):
        p = self.profiles[a['profile']]
        if ((result.sid is not None and self._sid_conflicts(a, result.sid))
                or (result.outcome == 'ok' and not result.sid)):
            raise Stop(BLOCKED, f'call {a["call_id"]} result/session evidence conflicts', durable=True)
        if self.lead_mode and result.outcome == 'ok':
            evidence = self._session_evidence(a)
            if evidence is None or evidence[1] != {result.sid}:
                raise Stop(BLOCKED, 'native SID is not proven by this call; work preserved', durable=True)
        if self.lead_mode and result.outcome == 'ok' and not a['resume_sid']:
            reused = self.state.db.execute('SELECT 1 FROM calls WHERE id!=? AND session_out=?',
                                          (a['call_id'], result.sid)).fetchone()
            if reused:
                raise Stop(BLOCKED, 'fresh role call reused a previous native SID', durable=True)
        self._effects(p, result, a)
        crash('loop_after_effects')
        stage, outcome = a['stage'], result.outcome
        if outcome == 'ok':
            if stage == 'lead':
                return self._lead_result(a, result)
            return self._writer_result(a, result) if stage == 'writer' else self._review_result(a, result)
        if outcome in WAIT_OUTCOMES:
            if self.lead_mode:
                sid = self._proven_sid(a)
                if sid:
                    self.cp[stage] = {'profile': a['profile'], 'sid': sid}
                    self.cp['interrupted'] = stage == 'writer' or self.cp['interrupted']
            # An existing pin is kept; a provider wait never pins a new writer session.
            if stage == 'reviewer' and not self.lead_mode:
                self.cp['reviewer'] = self.cp['reviewer'] or {'profile': a['profile'], 'sid': None}
            return self._wait(stage, outcome, result.recheck_at, durable_timer=True)
        if outcome == 'cancelled':
            reason = self.cancel_reason
            if reason is None and a.get('monitor_wait'):
                m = a['monitor_wait']
                reason = wait(m['reason'], m['recheck_at'], m.get('evidence'))
            if isinstance(reason, Admission) and reason.decision == 'WAIT':
                self._resume_or_block(a, count=False)
                return self._wait(stage, 'monitor: ' + reason.reason, reason.recheck_at, durable_timer=True)
            rc = self._resume_or_block(a, count=False)
            return rc if rc is not None else CANCELLED
        if outcome in RESUME_OUTCOMES:
            return self._resume_or_block(a, count=True)
        if result.effects.get('disable'):
            if a['resume_sid']:
                self.cp['active'] = None
                raise Stop(BLOCKED, f'pinned {stage} profile {a["profile"]} failed deterministically: {outcome}', durable=True)
            self.cp.update(active=None, **({'reviewer': None} if stage == 'reviewer' else {}))
            self._save()
            return None
        sid = self._proven_sid(a)
        if sid:
            self.cp[stage] = {'profile': a['profile'], 'sid': sid}
        self.cp.update(active=None, failures=a['failures'] + 1, feedback=f'previous {stage} call ended with {outcome}')
        return self._budget()

    def _writer_result(self, a, result):
        answer = result.final_json
        if not result.sid:
            self.cp['active'] = None
            raise Stop(BLOCKED, 'writer returned no session id', durable=True)
        self.cp.update(writer={'profile': a['profile'], 'sid': result.sid}, active=None, step=a['step'] + 1,
                       failures=a['failures'], interrupted=False, feedback=None,
                       summary=answer['summary'][:TEXT_LIMIT], next=answer['next'][:TEXT_LIMIT])
        if answer['status'] == 'blocked':
            if self.lead_mode:
                return self._reconcile('Executor reported a blocker: ' + answer['blocker'][:TEXT_LIMIT])
            self.cp.update(status='BLOCKED', blocker=answer['blocker'])
            self._save()
            return BLOCKED
        base = self.cp['expected_base']
        base_tree = self.git.tree(base)
        tree = self.git.snapshot(self.ws, base)
        paths = self.git.changed(base, tree) if tree != base_tree else []
        protected = [path for path in paths if matches(path, self.config.protected)]
        if self.lead_mode:
            scope = self.cp['task']['spec']['scope_in']
            # Scope grants no host paths. Reject changed symlinks escaping /work as well as
            # scope prefixes that currently resolve outside the selected workspace.
            escapes = [path for path in paths if not (self.ws / path).resolve().is_relative_to(self.ws.resolve())]
            outside = [path for path in paths if not _in_scope(path, scope)
                       or _in_scope(path, self.cp['task']['spec']['scope_out'])]
            if protected or outside or escapes:
                # Lead mode never resets to a fresh author: rejected work stays in /work for the operator.
                raise Stop(BLOCKED, f'task {self.cp["task"]["id"]} changed protected {protected[:20]} or out-of-scope '
                                    f'{outside[:20]} paths or symlink escapes {escapes[:20]}; workspace preserved', durable=True)
            if tree == base_tree:
                self.cp.update(failures=self.cp['failures'] + 1,
                               feedback='/work is unchanged from the accepted base; finish the task or report blocked.')
                return self._budget('non-progress budget exhausted')
        if protected:
            return self._reject_protected(protected)
        intent = 'task' if self.lead_mode else answer['status']
        if tree == base_tree:
            if intent == 'progress':
                self.cp.update(failures=self.cp['failures'] + 1,
                               feedback='You reported progress but /work is unchanged from the accepted base.')
                return self._budget('non-progress budget exhausted')
            candidate = {'commit': base, 'tree': tree, 'base': base, 'ref': None, 'changed': False}
        else:
            made = self.git.candidate(self.goal_id, None, base, f'Forge loop step {self.cp["step"]}', tree=tree)
            candidate = {'commit': made.commit, 'tree': made.tree, 'base': made.base, 'ref': made.ref, 'changed': True}
        candidate.update(intent=intent, writer_call=a['call_id'], writer_profile=a['profile'],
                         summary=answer['summary'][:TEXT_LIMIT], paths=paths, gate=None, review=None)
        if self.lead_mode:
            candidate['task'] = self._task_receipt()
        self.cp.update(candidate=candidate, stage='gate')
        self._save()
        return None

    def _reject_protected(self, paths):
        rejected = self.ws.parent / f'rejected-{time.time_ns()}'
        fresh = self.ws.parent / ('clone-' + uuid.uuid4().hex)
        self.git.clone(self.cp['expected_base'], fresh)
        self.ws.rename(rejected)
        fresh.rename(self.ws)
        self.cp['rejected'] = (self.cp.get('rejected') or [])[-(HISTORY - 1):] + [str(rejected)]
        self.cp.update(writer=None, stage='writer', failures=self.cp['failures'] + 1,
                       feedback='Protected paths may not change: ' + ', '.join(paths[:20]) +
                                '. /work was reset to the accepted base and this is a fresh session.')
        return self._budget('protected path budget exhausted')

    def _reject_candidate(self, feedback):
        if self.lead_mode:
            return self._repair(feedback)
        self.cp.update(candidate=None, reviewer=None, stage='writer', feedback=feedback[:TEXT_LIMIT * 2],
                       failures=self.cp['failures'] + 1)
        return self._budget()

    def _review_result(self, a, result):
        candidate = self.cp['candidate']
        if candidate is None or candidate['commit'] != a['candidate'] or candidate['tree'] != a['tree']:
            self.cp['active'] = None
            raise Stop(BLOCKED, 'review result does not bind the current candidate', durable=True)
        answer = result.final_json
        if self.lead_mode and (candidate.get('task') != self._task_receipt() or a.get('spec_sha') != candidate['task']['spec_sha']
                               or result.sid in {(self.cp['lead'] or {}).get('sid'), (self.cp['writer'] or {}).get('sid')}):
            self.cp['active'] = None
            raise Stop(BLOCKED, 'review does not bind the current task spec or reused an executor/Lead session', durable=True)
        candidate['review'] = {'call': a['call_id'], 'profile': a['profile'], 'verdict': answer['verdict'],
                               'findings': [f[:500] for f in answer['findings'][:20]], 'tree': a['tree'],
                               'commit': a['candidate'], 'input_sha256': _sha(Path(a['raw_dir']) / 'input.json'),
                               'evidence_sha256': _sha(Path(a['raw_dir']) / 'result.json')}
        if self.lead_mode:
            candidate['review'].update(spec_sha=a['spec_sha'], sid=result.sid)
        self.cp.update(active=None, reviewer=None)
        if answer['verdict'] == 'BLOCK':
            self.cp.update(status='BLOCKED', blocker='Independent review BLOCK:\n- ' + '\n- '.join(candidate['review']['findings']))
            self._save()
            return BLOCKED
        if answer['verdict'] != 'PASS':
            return self._reject_candidate('Independent review FAIL:\n- ' + '\n- '.join(candidate['review']['findings']))
        self.cp['stage'] = 'accept'
        self._save()
        return None

    # -- lead mode ----------------------------------------------------------------------------
    def _task_receipt(self):
        task = self.cp['task']
        return {'id': task['id'], 'spec_sha': task['spec_sha'], 'base': task['base']}

    def _lead(self):
        self._drift('lead')
        p, rc = self._select('lead')
        if rc is not None:
            return rc
        rc = self._admission(p, 'lead')
        if rc is not None:
            return rc
        # Read-only export of the accepted base: the Lead always sees the current docs and code.
        export = self.git.export(self.cp['expected_base'], self.ws.parent / f'review-{time.time_ns()}')
        pin = self.cp['lead']
        a = self._prepare('lead', p, self._lead_prompt(), export, False, pin['sid'] if pin else None)
        return self._start(a)

    def _lead_prompt(self):
        cp = self.cp
        parts = ['# Goal', self.goal_text.strip(), '# Accepted state',
                 f"Accepted base commit: {cp['expected_base']}. Accepted tasks: {cp['tasks_accepted']}. "
                 f"/work is a fresh read-only export of exactly this commit; reread changed docs there."]
        if cp['last_task']:
            parts += ['Last accepted task receipt:', json.dumps(cp['last_task'], sort_keys=True)]
        if cp['handoff']:
            parts += ['# Handoff (one reconciliation; issue a smaller replacement task or block)',
                      json.dumps(cp['handoff'], sort_keys=True)]
        if cp['feedback']:
            parts += ['# Controller feedback', cp['feedback']]
        parts += ['# Constraints', 'Protected paths (never in scope): ' + ', '.join(self.config.protected),
                  f"Trusted gate command: {' '.join(self.config.commands['test'])}.",
                  'Executor tiers available: ' + ', '.join(sorted({t for i in self.policy['writers']
                                                                    for t in self.profiles[i].tiers} & {'cheap', 'normal'}))]
        return render('lead', parts)

    def _executor_prompt(self):
        cp, task = self.cp, self.cp['task']
        parts = ['Your previous turn was interrupted. Continue the preserved work in /work.'] if cp['interrupted'] else []
        parts += ['# Goal (context only; the Technical Lead owns planning)', self.goal_text.strip(),
                  '# Task', f"Task {task['id']} spec sha256 {task['spec_sha']} on accepted base {task['base']}:",
                  spec_text(task['spec'])]
        if task['fixes']:
            parts.append(f"Correction round {task['fixes']} of {self.policy['fix_rounds']}.")
        if cp['feedback']:
            parts += ['# Controller feedback', cp['feedback']]
        return render('executor', parts)

    def _lead_result(self, a, result):
        answer = result.final_json
        if not result.sid:
            self.cp['active'] = None
            raise Stop(BLOCKED, 'Lead returned no session id', durable=True)
        pin = self.cp['lead']
        if pin and pin['sid'] != result.sid:
            self.cp['active'] = None
            raise Stop(BLOCKED, 'Lead session continuity broken: resumed call reported a different SID', durable=True)
        self.cp.update(lead={'profile': a['profile'], 'sid': result.sid}, active=None, failures=a['failures'],
                       feedback=None, summary='Lead: ' + json.dumps(answer, sort_keys=True)[:TEXT_LIMIT - 6])
        kind = answer['disposition']
        if kind == 'blocked':
            self.cp.update(status='BLOCKED', blocker=f"Lead blocker: {answer['blocker']} Question: {answer['question']}")
            self._save()
            return BLOCKED
        seq = self.cp['task_seq'] + 1
        spec = dict(answer)
        task = {'id': f'T{seq}', 'seq': seq, 'spec': spec, 'spec_sha': _spec_sha(spec), 'base': self.cp['expected_base'],
                'fixes': 0, 'lead_call': a['call_id'], 'replaces': (self.cp['handoff'] or {}).get('task_id')}
        if kind == 'done':
            # The Lead's statement alone never ends the goal: the unchanged accepted base goes through the
            # trusted gate and an independent full-goal review bound to this decision.
            last = (self.cp['last_task'] or {}).get('executor') or a['profile']
            base = self.cp['expected_base']
            task['final'] = True
            self.cp.update(task_seq=seq, task=task, writer=None, reviewer=None, stage='gate',
                           candidate={'commit': base, 'tree': self.git.tree(base), 'base': base, 'ref': None,
                                      'changed': False, 'intent': 'done', 'writer_call': a['call_id'],
                                      'writer_profile': last, 'summary': answer['summary'][:TEXT_LIMIT], 'paths': [],
                                      'gate': None, 'review': None, 'task': {'id': task['id'], 'spec_sha': task['spec_sha'],
                                                                             'base': base}})
            self._save()
            return None
        if not [i for i in self.policy['writers'] if spec['tier'] in self.profiles[i].tiers]:
            raise Stop(BLOCKED, f"no configured executor for Lead tier {spec['tier']}", durable=True)
        self.cp.update(task_seq=seq, task=task, writer=None, reviewer=None, candidate=None, stage='writer', interrupted=False)
        self._save()
        return None

    def _repair(self, feedback):
        """Gate or reviewer FAIL: the same executor SID repairs within the finite correction budget."""
        task = self.cp['task']
        if task.get('final'):
            self.cp.update(candidate=None, reviewer=None, task=None, stage='lead', failures=self.cp['failures'] + 1,
                           feedback=('Final verification of your DONE decision failed:\n' + feedback)[:TEXT_LIMIT * 2])
            return self._budget('final verification budget exhausted')
        if task['fixes'] >= self.policy['fix_rounds']:
            return self._reconcile(feedback)
        # The ordinal is persisted before the repair launch, so a crash or replay cannot buy another round.
        task['fixes'] += 1
        self.cp.update(candidate=None, reviewer=None, stage='writer', feedback=feedback[:TEXT_LIMIT * 2])
        self._save()
        return None

    def _reconcile(self, feedback):
        """Exhausted or blocked task: one return to the same Lead per lineage, then BLOCK."""
        task = self.cp['task']
        if task.get('replaces') or self.cp['reconciliations'] >= 1:
            self.cp.update(status='BLOCKED', blocker=f"task {task['id']} failed again; "
                                                     f"the reconciliation budget is used. Operator review of the "
                                                     f"preserved workspace is required. Last feedback: {feedback[:TEXT_LIMIT]}")
            self._save()
            return BLOCKED
        rejected = self.ws.parent / f'rejected-{time.time_ns()}'
        fresh = self.ws.parent / ('clone-' + uuid.uuid4().hex)
        self.git.clone(self.cp['expected_base'], fresh)
        self.ws.rename(rejected)
        fresh.rename(self.ws)
        self.cp['rejected'] = (self.cp.get('rejected') or [])[-(HISTORY - 1):] + [str(rejected)]
        self.cp.update(handoff={'task_id': task['id'], 'spec_sha': task['spec_sha'], 'fixes': task['fixes'],
                                'executor': self.cp['writer'], 'rejected_workspace': str(rejected),
                                'feedback': feedback[:TEXT_LIMIT]},
                       reconciliations=self.cp['reconciliations'] + 1, task=None, writer=None, reviewer=None,
                       candidate=None, stage='lead', feedback=None)
        self._save()
        return None

    # Metering uses the existing execution/checkpoint truth; it never admits work.
    def _terra_gate(self, p):
        from forge.config import _terra_tuple, verify_terra_proof
        if _terra_tuple(p):
            ok, reason = verify_terra_proof(self.root / 'smoke' / 'terra-proof.json', p, self.clock(),
                                            harness=self.config.harness[p.harness])
            if not ok:
                raise Stop(BLOCKED, 'Terra proof no longer valid: ' + reason, durable=True)

    def _metric_touch(self):
        if self.obs is not None:
            self.active_at = self.clock()
            with self.state.transaction() as db:
                qm.touch(db, self.obs, self.active_at)

    def _observation(self, a):
        if self.obs is None:
            return None
        p = self.profiles[a['profile']]
        return {'obs': self.obs, 'account': p.account, 'provider': p.harness, 'model': p.model,
                'role': a['stage'], 'clock': self.clock}

    def _metric_wait(self, p, stage, kind, ident, reason, evidence=None, *, call_id=None, fallback=False):
        if self.obs is None:
            return
        causes = ('policy',) if kind == 'policy' else qm.causes(reason, evidence)
        self.active_at = self.clock()
        with self.state.transaction() as db:
            for cause in causes:
                self._metric_event(db, p, stage, kind, ident, cause, reason, call_id, fallback)

    def _metric_event(self, db, p, stage, kind, ident, cause, reason, call_id=None, fallback=False):
        if cause == 'policy':
            kind = 'policy'
        payload = dict(obs=self.obs, cause=cause, account=p.account, provider=p.harness, model=p.model,
                       role=stage, at=self.clock(), call_id=call_id, source=kind,
                       evidence={'reason': reason, 'source_kind': kind})
        qm.record(db, event_id=ident + ':' + kind + ':' + cause, kind=kind, **payload)
        if fallback and cause != 'policy':
            qm.record(db, event_id=ident + ':fallback:' + cause, kind='fallback_avoided', **payload)

    def _metric_result(self, db, p, a, result):
        if self.obs is None:
            return
        if result.outcome in WAIT_OUTCOMES:
            # Only classification reads provider fields; never persist raw errors.
            reason = json.dumps(result.error_fields, default=str)
            for cause in qm.causes(result.outcome, reason):
                self._metric_event(db, p, a['stage'], 'midflight_interruption', result.call_id + ':result',
                                   cause, result.outcome, result.call_id, bool(a['resume_sid']))
        elif result.outcome == 'cancelled' and a.get('monitor_wait'):
            m = a['monitor_wait']
            for cause in qm.causes(m['reason'], m.get('evidence')):
                self._metric_event(db, p, a['stage'], 'midflight_interruption', result.call_id + ':monitor',
                                   cause, m['reason'], result.call_id, True)
        elif result.outcome == 'ok':
            # A bwrap PID or even an initial SID followed by quota denial is insufficient.
            qm.confirm_continuation(db, self.obs, result.call_id, self.clock(), 'successful_native_result')

    # -- stages -------------------------------------------------------------------------------
    def _writer(self):
        self._drift('writer')
        p, rc = self._select('writer')
        if rc is not None:
            return rc
        rc = self._admission(p, 'writer')
        if rc is not None:
            return rc
        pin = self.cp['writer']
        a = self._prepare('writer', p, self._writer_prompt(), self.ws, True, pin['sid'] if pin else None)
        return self._start(a)

    def _writer_prompt(self):
        cp = self.cp
        if self.lead_mode:
            return self._executor_prompt()
        parts = ['Your previous turn was interrupted. Continue the preserved work in /work.'] if cp['interrupted'] else []
        parts += ['# Goal', self.goal_text.strip(), '# Loop state',
                  f"Accepted steps: {cp['step']}. Accepted base commit: {cp['expected_base']}."]
        if cp['summary']:
            parts.append('Latest summary: ' + cp['summary'])
        if cp['next']:
            parts.append('Planned next: ' + cp['next'])
        if cp['feedback']:
            parts += ['# Controller feedback', cp['feedback']]
        return render('writer', parts)

    def _gate(self):
        candidate = self.cp['candidate']
        export = self.git.export(candidate['commit'], self.ws.parent / f'gate-{time.time_ns()}')
        log = self.dir / f'{export.name}.log'
        try:
            spec = sandbox.gate_spec(self.roots, command=list(self.config.commands['test']), work=export,
                                     ro_binds=self.config.execution['ro_binds'],
                                     network=False,
                                     extra_env=tuple(self.policy['env'].items()))
            sandbox.revalidate(spec)
            argv = sandbox.argv(spec)
        except sandbox.SandboxError as exc:
            raise Stop(BLOCKED, f'env_block: gate sandbox invalid: {exc}') from exc
        timed_out = False
        with open(log, 'wb') as output:
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            deadline = time.monotonic() + self.policy['step_timeout']
            while True:
                try:
                    process.wait(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    pass
                value = self.should_cancel()
                if value and not isinstance(value, Admission):
                    terminate_group(process)
                    return CANCELLED
                if time.monotonic() >= deadline:
                    timed_out = True
                    terminate_group(process)
                    break
        data = log.read_bytes()
        rc = None if timed_out else process.returncode
        if rc not in (0, None) and data.startswith(b'bwrap:'):
            raise Stop(BLOCKED, 'env_block: bwrap gate setup failed: ' + data.decode(errors='replace')[-500:])
        tail = '\n'.join(data.decode(errors='replace').splitlines()[-GATE_TAIL:])
        candidate['gate'] = {'rc': rc, 'timed_out': timed_out, 'log': str(log), 'log_sha256': hashlib.sha256(data).hexdigest(),
                             'export': str(export), 'tree': candidate['tree']}
        if candidate['intent'] == 'progress' and not self.lead_mode:
            candidate['gate']['tail'] = tail
            self.cp['stage'] = 'accept'
        elif rc == 0:
            self.cp['stage'] = 'reviewer'
        else:
            state = 'timed out' if timed_out else f'rc={rc}'
            return self._reject_candidate(f'Gate failed ({state}) on candidate {candidate["commit"]}. '
                                          f'Last output:\n{tail}')
        self._save()
        return None

    def _reviewer(self):
        self._drift('review')
        candidate = self.cp['candidate']
        p, rc = self._select('reviewer')
        if rc is not None:
            return rc
        rc = self._admission(p, 'reviewer')
        if rc is not None:
            return rc
        export = self.git.export(candidate['commit'], self.ws.parent / f'review-{time.time_ns()}')
        gate = candidate['gate']
        log_tail = '\n'.join(Path(gate['log']).read_text(errors='replace').splitlines()[-GATE_TAIL:])
        parts = ['# Goal', self.goal_text.strip()]
        extra = {}
        if self.lead_mode:
            task = self.cp['task']
            parts += ['# Task specification' if not task.get('final') else '# Lead completion decision',
                      f"Task {task['id']} spec sha256 {task['spec_sha']}:", spec_text(task['spec'])]
            extra = {'spec_sha': task['spec_sha']}
        parts += ['# Candidate', f"Commit {candidate['commit']} tree {candidate['tree']} based on {candidate['base']}.",
                  'Writer summary: ' + candidate['summary']]
        if self.lead_mode:
            parts.append('Changed paths: ' + (', '.join(candidate['paths']) or 'none'))
        parts += [f"Trusted gate `{' '.join(self.config.commands['test'])}` exited {gate['rc']}. Last output:", log_tail]
        template = 'reviewer' if not self.lead_mode else 'final_reviewer' if self.cp['task'].get('final') else 'task_reviewer'
        pin = self.cp['reviewer']
        a = self._prepare('reviewer', p, render(template, parts), export, False, pin['sid'] if pin else None,
                          candidate=candidate['commit'], tree=candidate['tree'], **extra)
        return self._start(a)

    def _accept(self):
        candidate = self.cp['candidate']
        done = candidate['intent'] == 'done'
        task = candidate['intent'] == 'task'
        reviewed = done or task
        if reviewed and not self._receipts_ok(candidate['gate'], candidate['review'], candidate['tree']):
            raise Stop(BLOCKED, 'done candidate lacks matching gate/review receipts', durable=True)
        if self.lead_mode and (not reviewed or candidate.get('task') != self._task_receipt()
                               or candidate['review'].get('spec_sha') != self.cp['task']['spec_sha']):
            raise Stop(BLOCKED, 'lead-mode acceptance lacks a matching task/spec/review receipt', durable=True)
        self._drift('acceptance')
        pending = {'old_head': self.cp['expected_base'], 'intent': 'DONE' if done else 'TASK' if task else 'RUNNING',
                   'candidate': {k: candidate[k] for k in ('commit', 'tree', 'base', 'ref')},
                   'step': self.cp['step'], 'writer_call': candidate['writer_call'], 'summary': candidate['summary'],
                   'gate': {k: candidate['gate'][k] for k in ('rc', 'timed_out', 'log', 'log_sha256', 'tree')},
                   'review': candidate['review'] if reviewed else None, 'feedback': None}
        if self.lead_mode:
            pending.update(task=candidate['task'], executor=candidate['writer_profile'])
        if not done and candidate['gate']['rc'] != 0:
            state = 'timed out' if candidate['gate']['timed_out'] else f'rc={candidate["gate"]["rc"]}'
            pending['feedback'] = (f"Trusted gate `{' '.join(self.config.commands['test'])}` failed ({state}) on your "
                                   f"accepted progress commit {candidate['commit']}. Last output:\n"
                                   f"{candidate['gate'].get('tail', '')}")[:TEXT_LIMIT * 2]
        if not candidate['changed']:
            # Unchanged DONE names the already accepted commit; no duplicate commit is created.
            if self.git.tree(candidate['commit']) != candidate['tree']:
                raise Stop(BLOCKED, 'unchanged candidate tree differs from accepted base', durable=True)
            return self._finalize(pending)
        self.cp['pending'] = pending
        self._save()
        crash('loop_after_pending')
        return None

    def _receipts_ok(self, gate, review, tree):
        return bool(isinstance(gate, dict) and gate.get('rc') == 0 and gate.get('tree') == tree
                    and isinstance(review, dict) and review.get('verdict') == 'PASS' and review.get('tree') == tree)

    def _verify_receipts(self, pending):
        """Reject incomplete/conflicting persisted receipts before CAS and recovered finalization."""
        try:
            gate, review = pending['gate'], pending['review']
            candidate = pending['candidate']
            if gate['tree'] != candidate['tree'] or _sha(gate['log']) != gate['log_sha256']:
                raise ValueError('gate evidence mismatch')
            if pending['intent'] not in ('DONE', 'RUNNING', 'TASK') or (pending['intent'] == 'TASK' and not self.lead_mode):
                raise ValueError('unsupported acceptance intent')
            if self.lead_mode:
                task, current = pending['task'], self.cp['task']
                if (pending['intent'] == 'RUNNING' or current is None or task != self._task_receipt()
                        or review['spec_sha'] != task['spec_sha'] or task['base'] != pending['old_head']):
                    raise ValueError('task/spec/base receipt mismatch')
            if pending['intent'] in ('DONE', 'TASK'):
                if not self._receipts_ok(gate, review, candidate['tree']) or review['commit'] != candidate['commit']:
                    raise ValueError('review does not bind the candidate commit/tree')
                raw = self.root / 'calls' / review['call']
                if _sha(raw / 'input.json') != review['input_sha256'] or _sha(raw / 'result.json') != review['evidence_sha256']:
                    raise ValueError('review input/result missing or changed')
                prepared = json.loads((raw / 'input.json').read_bytes())
                data = json.loads((raw / 'result.json').read_bytes())
                row = self.state.db.execute('SELECT role,profile,status,raw_dir FROM calls WHERE id=?',
                                            (review['call'],)).fetchone()
                if self.lead_mode:
                    author = self.profiles[pending['executor']]
                    reviewer = self.profiles[review['profile']]
                    if (author.family == reviewer.family or not {'normal', 'strong'} & set(reviewer.tiers)
                            or data['sid'] != review['sid']
                            or data['sid'] in {(self.cp['lead'] or {}).get('sid'),
                                              (self.cp['writer'] or {}).get('sid')}
                            or spec_text(self.cp['task']['spec']) not in prepared['prompt']
                            or candidate['commit'] not in prepared['prompt']
                            or candidate['tree'] not in prepared['prompt']):
                        raise ValueError('review family/session/spec/candidate pins mismatch')
                if (prepared['role'] != 'reviewer' or prepared['images'] != []
                        or not isinstance(prepared['prompt'], str)
                        or data['call_id'] != review['call'] or data['raw_dir'] != str(raw)
                        or data['outcome'] != 'ok' or not _valid_review(data['final_json'])
                        or data['final_json']['verdict'] != 'PASS' or row is None
                        or tuple(row) != ('reviewer', review['profile'], 'done', str(raw))):
                    raise ValueError('review evidence is not linked to its call/PASS')
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise Stop(BLOCKED, f'pending acceptance receipts invalid: {exc}', durable=True) from exc

    def _finish_pending(self):
        pending = self.cp['pending']
        candidate = Candidate(**pending['candidate'])
        self._verify_receipts(pending)
        self._owned()
        head = self.git.head()
        if head == pending['old_head']:
            if self.git.tree(candidate.commit) != candidate.tree:
                raise Stop(BLOCKED, 'pending candidate tree mismatch', durable=True)
            if not self.git.cas(candidate):
                raise Stop(BLOCKED, f'CAS of {candidate.commit} failed; pending acceptance and candidate preserved')
            crash('loop_after_cas')
        elif head != candidate.commit:
            raise Stop(BLOCKED, f'loop ref is {head}: neither accepted base {pending["old_head"]} nor candidate '
                                f'{candidate.commit}; candidate and workspace preserved')
        return self._finalize(pending)

    def _finalize(self, pending):
        commit, tree = pending['candidate']['commit'], pending['candidate']['tree']
        self._verify_receipts(pending)
        if self.git.head() != commit or self.git.tree(commit) != tree:
            raise Stop(BLOCKED, 'accepted ref does not hold the candidate tree', durable=True)
        history = (self.cp['history'] + [{'step': pending['step'], 'commit': commit, 'tree': tree,
                                          'intent': pending['intent'], 'summary': pending['summary'][:300]}])[-HISTORY:]
        self.cp.update(expected_base=commit, pending=None, candidate=None, reviewer=None, history=history)
        if pending['intent'] == 'TASK':
            # Task boundary: fresh executor next time; the Lead pin and its SID are retained.
            task = self.cp['task']
            self.cp.update(stage='lead', status='RUNNING', task=None, writer=None, failures=0, feedback=None,
                           tasks_accepted=self.cp['tasks_accepted'] + 1, handoff=None,
                           last_task={'id': task['id'], 'spec_sha': task['spec_sha'], 'objective': task['spec']['objective'][:300],
                                      'commit': commit, 'tree': tree, 'base': pending['old_head'],
                                      'executor': pending['executor'], 'executor_sid': (self.cp['writer'] or {}).get('sid'),
                                      'summary': pending['summary'][:TEXT_LIMIT], 'gate_rc': pending['gate']['rc'],
                                      'review': {k: pending['review'][k] for k in ('call', 'profile', 'verdict', 'findings')},
                                      'fixes': task['fixes']})
            self._save()
            crash('loop_after_task_finalize')
            return None
        if pending['intent'] == 'DONE':
            self.cp.update(status='DONE', stage='accept', accepted_commit=commit, accepted_tree=tree)
            self._save()
            return DONE
        if pending['gate']['rc'] == 0:
            self.cp['failures'] = 0
        self.cp.update(stage='writer', status='RUNNING', feedback=pending.get('feedback'))
        self._save()
        return None
