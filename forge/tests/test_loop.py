"""KISS goal loop tests. SIMULATED: fixture native programs, injected admission and clocks.

Real bwrap, runner, git, SQLite state and controller lock; no live provider evidence.
"""
from dataclasses import asdict
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

import pytest

from forge import loop
from forge.adapters.base import Adapter, Event, Native
from forge.config import load_config
from forge.loop import ALLOW, Admission, block, run_loop, run_with_capsule, update_capsule, wait

ROOT = Path(__file__).resolve().parents[1]
FAKE_NATIVE = r'''
import json, os, sys, time
action = json.load(open('/in/action.json'))
sys.stdin.read()
if not action.get('no_sid'):
    print(json.dumps({'sid': sys.argv[1]}), flush=True)
for path, content in action.get('writes', {}).items():
    target = os.path.join('/work', path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    open(target, 'w').write(content)
for path in action.get('delete', []):
    os.remove(os.path.join('/work', path))
for path in action.get('chmod', []):
    os.chmod(os.path.join('/work', path), 0o755)
if action.get('sleep'):
    time.sleep(action['sleep'])
if action.get('quota'):
    print(json.dumps({'error': {'message': 'usage_limit_reached', 'resets_at': action['quota']}}), flush=True)
    sys.exit(1)
print(json.dumps({'answer': action['answer']}), flush=True)
'''


class FakeAdapter(Adapter):
    """FIXTURE protocol; never registered in production adapter_for."""
    def __init__(self, queue, log):
        self.queue, self.log = Path(queue), Path(log)

    def build_argv(self, call, exe, in_dir):
        actions = json.loads(self.queue.read_text())
        action = actions.pop(0)
        self.queue.write_text(json.dumps(actions))
        with self.log.open('a') as stream:
            stream.write(json.dumps({'call': call.id, 'role': call.role, 'profile': call.profile, 'model': call.model,
                                     'effort': call.effort, 'resume': call.resume_sid, 'preset': call.preset_sid,
                                     'writable': call.writable, 'prompt': call.prompt_text}) + '\n')
        (in_dir / 'action.json').write_text(json.dumps(action))
        (in_dir / 'fake.py').write_text(FAKE_NATIVE)
        sid = call.resume_sid or call.preset_sid or 'thread-' + call.id
        return [exe, '/in/fake.py', sid], call.prompt_text.encode()

    def parse_line(self, line):
        value = json.loads(line)
        if 'sid' in value:
            return Event('sid', value)
        return Event('error' if 'error' in value else 'final', value)

    def finish(self, call, rc, events, stdout, stderr):
        sid = next((e.data['sid'] for e in events if e.kind == 'sid'), None)
        errors = [e.data for e in events if e.kind == 'error']
        if errors:
            return Native('failure', sid, error_fields={'stderr': stderr, 'native_errors': errors})
        answer = next((e.data['answer'] for e in events if e.kind == 'final'), None)
        if rc != 0 or answer is None:
            return Native('absent', sid, error_fields={'stderr': stderr})
        return Native('success', sid, '```json\n' + json.dumps(answer) + '\n```', call.model, True)


def install(target, queue, log, caps=None):
    """Install fixture seams on module `target` (monkeypatch or the loop module itself)."""
    caps = caps or {'text', 'resume', 'readonly'}
    setter = target.setattr if hasattr(target, 'setattr') else (lambda obj, name, value: setattr(obj, name, value))
    setter(loop, '_adapter', lambda profile: FakeAdapter(queue, log))
    setter(loop, '_binary', lambda config, roots, profile: (Path('/usr/bin/python3'), {'path': '/usr/bin/python3', 'version': 'fixture'}))
    setter(loop, '_capabilities', lambda config, state, profile: set(caps))


def writer(status='done', writes=None, **extra):
    answer = {'status': status, 'summary': extra.pop('summary', 'did ' + status), 'next': 'continue',
              'blocker': extra.pop('blocker', None)}
    return {'writes': writes or {}, 'answer': answer, **extra}


def review(verdict='PASS', findings=()):
    return {'answer': {'verdict': verdict, 'findings': list(findings)}}


class Admit:
    def __init__(self, decisions=None):
        self.decisions, self.calls = dict(decisions or {}), []

    def __call__(self, account, profile, stage, now):
        self.calls.append((account, profile, stage, now))
        decision = self.decisions.get(stage)
        if isinstance(decision, list):
            decision = decision.pop(0) if decision else None
        return decision or ALLOW


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, parent, message):
    return git(repo, '-c', 'user.name=Fixture', '-c', 'user.email=f@local', 'commit-tree', '-p', parent, '-m', message,
               parent + '^{tree}')


CONFIG = '''protected = ["docs/source/**"]
[commands]
test = ["/usr/bin/sh", "-c", "{gate}"]
[accounts.acct-a]
enabled = true
[accounts.acct-b]
enabled = true
[accounts.acct-c]
enabled = true
[[profile]]
id = "w1"
harness = "claude"
model = "claude-opus-5-5"
effort = "medium"
family = "anthropic"
tiers = ["strong"]
account = "acct-a"
[[profile]]
id = "r1"
harness = "codex"
model = "gpt-6.1-sol"
effort = "medium"
family = "openai"
tiers = ["strong"]
account = "acct-b"
[[profile]]
id = "w2"
harness = "codex"
model = "gpt-6.1-sol"
effort = "high"
family = "openai"
tiers = ["strong"]
account = "acct-c"
[loop]
writers = {writers}
reviewers = {reviewers}
branch = "forge/loop"
step_timeout = {timeout}
max_failures = {failures}
env = {{ CARGO_TARGET_DIR = "/work/target" }}
'''


class Env:
    def __init__(self, tmp_path, operator_config, monkeypatch):
        self.tmp, self.write_config, self.monkeypatch = tmp_path, operator_config, monkeypatch
        self.queue, self.log = tmp_path / 'queue.json', tmp_path / 'launches.jsonl'
        self.queue.write_text('[]')
        self.goal = tmp_path / 'goal.md'
        self.goal.write_text('Make done.txt exist.\n')
        self.capsule = tmp_path / 'capsule.json'
        self.configure()
        repo = self.config.target_repo
        git(repo, 'init', '-q', '-b', 'main')
        (repo / 'seed.txt').write_text('seed\n')
        (repo / 'docs/source').mkdir(parents=True)
        (repo / 'docs/source/spec.md').write_text('spec\n')
        git(repo, 'add', '.')
        git(repo, '-c', 'user.name=Fixture', '-c', 'user.email=f@local', 'commit', '-qm', 'base')
        git(repo, 'branch', 'forge/loop')
        self.repo, self.base = repo, git(repo, 'rev-parse', 'HEAD')
        install(monkeypatch, self.queue, self.log)

    def configure(self, gate='test ! -e FAIL', writers='["w1"]', reviewers='["r1"]', timeout=30, failures=3):
        self.config = load_config(self.write_config(CONFIG.format(gate=gate, writers=writers, reviewers=reviewers,
                                                                  timeout=timeout, failures=failures)))
        return self.config

    def actions(self, *items):
        self.queue.write_text(json.dumps(list(items)))

    def launches(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def run(self, admit=None, **kwargs):
        return run_loop(self.config, self.goal, admit=admit or Admit(), **kwargs)

    def ref(self):
        return git(self.repo, 'rev-parse', 'refs/heads/forge/loop')

    def checkpoint_path(self):
        goal_id = hashlib.sha256(self.goal.read_bytes()).hexdigest()[:16]
        return self.config.state_dir / 'loop' / goal_id / 'checkpoint.json'

    def checkpoint(self):
        return json.loads(self.checkpoint_path().read_text())

    def db(self):
        return sqlite3.connect(self.config.state_dir / 'forge.db')

    def child(self, crash=None, max_steps=1):
        """Run run_loop in a separate controller process (for SIGKILL crash windows)."""
        env = {**os.environ, 'PYTHONPATH': f'{ROOT / "src"}:{ROOT / "tests"}',
               'XDG_CONFIG_HOME': os.environ['XDG_CONFIG_HOME'], 'XDG_STATE_HOME': os.environ['XDG_STATE_HOME']}
        env.pop('FORGE_TEST_CRASH_AT', None)
        if crash:
            env['FORGE_TEST_CRASH_AT'] = crash
        code = ('import sys, test_loop; sys.exit(test_loop.child_main(sys.argv[1:]))')
        return subprocess.Popen([sys.executable, '-c', code, str(self.config.path), str(self.goal), str(self.queue),
                                 str(self.log), str(max_steps)], env=env, cwd=self.tmp)


def child_main(argv):
    config_path, goal, queue, log, max_steps = argv
    install(loop, queue, log)
    return run_loop(load_config(config_path), goal, max_steps=int(max_steps), admit=lambda *a: ALLOW)


@pytest.fixture
def env(tmp_path, operator_config, monkeypatch):
    return Env(tmp_path, operator_config, monkeypatch)


def manifest(state_dir, call):
    return json.loads((state_dir / 'calls' / call / 'manifest.json').read_text())


# --- B1: exact accepted DONE --------------------------------------------------------------

def test_first_call_changed_done_advances_ref_to_exact_reviewed_tree(env):
    env.actions(writer('done', {'done.txt': 'yes\n'}), review('PASS'))
    admit = Admit()
    assert env.run(admit) == 0
    cp = env.checkpoint()
    assert cp['status'] == 'DONE' and cp['accepted_commit'] == env.ref() != env.base
    assert git(env.repo, 'rev-parse', env.ref() + '^{tree}') == cp['accepted_tree']
    assert git(env.repo, 'rev-parse', env.ref() + '^') == env.base
    assert git(env.repo, 'show', env.ref() + ':done.txt') == 'yes'
    assert [c[2] for c in admit.calls] == ['writer', 'reviewer']
    launches = env.launches()
    assert [(l['role'], l['profile'], l['model'], l['effort'], l['writable']) for l in launches] == [
        ('writer', 'w1', 'claude-opus-5-5', 'medium', True), ('reviewer', 'r1', 'gpt-6.1-sol', 'medium', False)]
    history = cp['history'][-1]
    assert history['commit'] == cp['accepted_commit']
    assert git(env.repo, 'rev-parse', 'refs/heads/main') == env.base  # project branch untouched
    assert env.run() == 0 and len(env.launches()) == 2  # DONE is terminal and idempotent


def test_progress_then_unchanged_done_same_config(env):
    env.configure(gate='test -e part.txt')
    env.actions(writer('progress', {'part.txt': '1\n'}), writer('done'), review('PASS'))
    assert env.run(max_steps=1) == 3
    progress = env.ref()
    assert env.run(max_steps=1) == 0
    cp = env.checkpoint()
    assert env.ref() == progress == cp['accepted_commit']  # no duplicate commit for unchanged done
    assert cp['accepted_tree'] == git(env.repo, 'rev-parse', progress + '^{tree}')
    assert [l['resume'] for l in env.launches() if l['role'] == 'writer'][1] is not None


def test_changed_final_done_after_progress(env):
    env.actions(writer('progress', {'a.txt': '1\n'}), writer('done', {'b.txt': '2\n'}), review('PASS'))
    assert env.run(max_steps=2) == 0
    cp = env.checkpoint()
    assert git(env.repo, 'show', cp['accepted_commit'] + ':b.txt') == '2'
    assert git(env.repo, 'show', cp['accepted_commit'] + ':a.txt') == '1'
    assert env.ref() == cp['accepted_commit']


# --- B3: admission seam -----------------------------------------------------------------------

def test_writer_admission_wait_then_exact_profile_after_recheck(env):
    now = [1000.0]
    admit = Admit({'writer': [wait('quota low', 2000.0)]})
    env.actions(writer('done', {'done.txt': 'x'}), review())
    assert env.run(admit, clock=lambda: now[0]) == 75
    cp = env.checkpoint()
    assert cp['status'] == 'WAITING' and cp['waiting']['recheck_at'] == 2000.0 and cp['waiting']['stage'] == 'writer'
    assert env.launches() == []
    with env.db() as db:
        assert db.execute("SELECT count(*) FROM calls").fetchone()[0] == 0  # no fabricated provider call
    now[0] = 1500.0
    assert env.run(admit, clock=lambda: now[0]) == 75
    assert len(admit.calls) == 1  # before recheck: zero probes
    now[0] = 2000.0
    assert env.run(admit, clock=lambda: now[0]) == 0
    assert [c[1:3] for c in admit.calls] == [('w1', 'writer'), ('w1', 'writer'), ('r1', 'reviewer')]
    assert env.launches()[0]['profile'] == 'w1'


def test_reviewer_wait_keeps_candidate_and_does_not_rerun_writer(env):
    now = [1000.0]
    admit = Admit({'reviewer': [wait('weekly low', 3000.0)]})
    env.actions(writer('done', {'done.txt': 'x'}), review())
    assert env.run(admit, clock=lambda: now[0]) == 75
    cp = env.checkpoint()
    candidate = cp['candidate']
    assert cp['stage'] == 'reviewer' and candidate['gate']['rc'] == 0 and cp['reviewer']['profile'] == 'r1'
    assert env.ref() == env.base
    now[0] = 3000.0
    assert env.run(admit, clock=lambda: now[0]) == 0
    assert [l['role'] for l in env.launches()] == ['writer', 'reviewer']
    assert env.checkpoint()['accepted_commit'] == candidate['commit']


@pytest.mark.parametrize('decision', [None, 'ALLOW', Admission('MAYBE'), wait('past', 10.0), wait('inf', float('inf')),
                                      block('unsupported account')])
def test_untrusted_or_blocking_admission_fails_closed(env, decision):
    env.actions(writer('done', {'done.txt': 'x'}))
    admit = (lambda *a: decision)
    assert run_loop(env.config, env.goal, admit=admit, clock=lambda: 1000.0) == 2
    assert env.launches() == []


def test_admission_callback_is_required(env):
    with pytest.raises(TypeError):
        run_loop(env.config, env.goal)
    assert run_loop(env.config, env.goal, admit=None) == 2
    assert not env.config.state_dir.exists()


def test_provider_quota_result_waits_and_effects_apply_once(env):
    now = [1000.0]
    env.actions({'quota': 4102444800}, writer('done', {'done.txt': 'x'}), review())
    assert env.run(clock=lambda: now[0]) == 75
    cp = env.checkpoint()
    assert cp['waiting']['recheck_at'] == 4102444800 and cp['writer'] is None
    with env.db() as db:
        assert db.execute("SELECT state,k FROM accounts WHERE id='acct-a'").fetchone() == ('waiting', 1)
        assert db.execute('SELECT count(*) FROM call_effects').fetchone()[0] == 1


def test_monitor_cancellation_waits_and_resumes_same_session(env):
    now = [1000.0]
    env.actions(writer('done', {'done.txt': 'x'}, sleep=30), writer('done', {'done.txt': 'x'}), review())
    started = time.monotonic()

    def monitor():
        return wait('weekly <=10%', 5000.0) if time.monotonic() - started > 1.5 else False
    assert env.run(should_cancel=monitor, clock=lambda: now[0]) == 75
    cp = env.checkpoint()
    first = env.launches()[0]
    assert cp['waiting']['recheck_at'] == 5000.0 and cp['writer'] == {'profile': 'w1', 'sid': first['preset']}
    assert (env.config.state_dir / 'ws').exists()
    now[0] = 5000.0
    assert env.run(clock=lambda: now[0]) == 0
    assert env.launches()[1]['resume'] == first['preset'] and env.launches()[1]['profile'] == 'w1'


def test_stop_request_returns_130_and_preserves_session(env):
    env.actions(writer('done', {'done.txt': 'x'}, sleep=30))
    started = time.monotonic()
    assert env.run(should_cancel=lambda: time.monotonic() - started > 1.5) == 130
    cp = env.checkpoint()
    assert cp['status'] == 'RUNNING' and cp['writer']['sid'] == env.launches()[0]['preset'] and cp['active'] is None


# --- selection ---------------------------------------------------------------------------------

def test_selection_order_family_and_pinned_wait_without_substitution(env):
    env.configure(writers='["w1", "w2"]', reviewers='["w2", "r1"]')
    env.actions(writer('progress', {'a.txt': '1'}), writer('done', {'b.txt': '2'}), review())
    assert env.run(max_steps=1) == 3
    assert env.launches()[0]['profile'] == 'w1'
    with env.db() as db:
        db.execute("UPDATE accounts SET state='waiting',recheck_at=? WHERE id='acct-a'", (time.time() + 600,))
    assert env.run() == 75  # pinned writer account waits; w2 is never substituted
    assert len(env.launches()) == 1
    with env.db() as db:
        db.execute("UPDATE accounts SET state='available',recheck_at=NULL WHERE id='acct-a'")
    assert env.run() == 75  # the checkpoint recheck time still governs
    assert env.run(clock=lambda: time.time() + 601) == 0
    reviewer = env.launches()[-1]
    assert reviewer['role'] == 'reviewer' and reviewer['profile'] == 'w2'  # first different-family reviewer


def test_no_different_family_reviewer_blocks_with_reasons(env, capsys):
    env.configure(reviewers='["w1"]')
    env.actions(writer('done', {'done.txt': 'x'}))
    assert env.run() == 2
    assert 'same family' in capsys.readouterr().err
    assert [l['role'] for l in env.launches()] == ['writer']
    assert env.ref() == env.base


# --- B2: crash windows and recovery -----------------------------------------------------------

def run_child(env, crash, max_steps=1):
    proc = env.child(crash, max_steps)
    assert proc.wait(60) == -signal.SIGKILL
    return proc


@pytest.mark.parametrize('point', ['runner_after_result_file', 'runner_after_done', 'loop_after_effects'])
def test_completed_result_replays_without_duplicate_launch(env, point):
    env.actions(writer('done', {'done.txt': 'x'}), review())
    run_child(env, point)
    assert len(env.launches()) == 1
    assert env.run() == 0
    assert [l['role'] for l in env.launches()] == ['writer', 'reviewer']
    with env.db() as db:
        writer_call = env.launches()[0]['call']
        assert db.execute('SELECT count(*) FROM call_effects WHERE call=?', (writer_call,)).fetchone()[0] == 1
        assert db.execute("SELECT status FROM calls WHERE id=?", (writer_call,)).fetchone()[0] == 'done'
        assert db.execute("SELECT k FROM accounts WHERE id='acct-a'").fetchone()[0] == 0


def test_quota_replay_after_effects_does_not_double_backoff(env):
    env.actions({'quota': 4102444800})
    run_child(env, 'loop_after_effects')
    assert env.run(clock=lambda: 1000.0) == 75
    with env.db() as db:
        assert db.execute("SELECT k FROM accounts WHERE id='acct-a'").fetchone()[0] == 1
    assert len(env.launches()) == 1


def test_crash_after_active_launches_prepared_call_once(env):
    env.actions(writer('done', {'done.txt': 'x'}), review())
    run_child(env, 'loop_after_active')
    active = env.checkpoint()['active']
    assert active['stage'] == 'writer' and env.launches() == []
    assert env.run() == 0
    assert env.launches()[0]['call'] == active['call_id']
    assert len(env.launches()) == 2


def test_launching_row_without_result_is_never_relaunched(env):
    env.actions(writer('done', {'done.txt': 'x'}), review())
    run_child(env, 'runner_after_launching')
    assert env.run() == 2
    cp = env.checkpoint()
    assert cp['status'] == 'BLOCKED' and 'resume_unproven' in cp['blocker']
    assert env.launches() == []


def test_preset_session_without_native_evidence_blocks(env):
    env.actions(writer('done', {'done.txt': 'x'}, no_sid=True, sleep=60))
    proc = env.child()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if (env.config.state_dir / 'forge.db').exists():
            with env.db() as db:
                # The database file appears before its initial schema transaction commits.
                ready = db.execute("SELECT 1 FROM sqlite_master WHERE name='calls'").fetchone()
                row = db.execute("SELECT session_out FROM calls WHERE role='writer'").fetchone() if ready else None
            if row and row[0]:
                break
        time.sleep(0.1)
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait(30)
    assert env.run() == 2
    assert 'resume_unproven' in env.checkpoint()['blocker']
    assert len(env.launches()) == 1


def test_established_session_interrupt_resumes_same_profile_and_sid(env):
    env.actions(writer('progress', {'kept.txt': 'work'}, sleep=60), writer('done'), review())
    proc = env.child()
    workspace = None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        workspace = next((env.config.state_dir / 'ws').glob('*/src/kept.txt'), None) if (env.config.state_dir / 'ws').exists() else None
        if workspace:
            break
        time.sleep(0.1)
    assert workspace is not None
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait(30)
    first = env.launches()[0]
    assert env.run(max_steps=1) == 0
    resumed = env.launches()[1]
    assert resumed['profile'] == 'w1' and resumed['resume'] == first['preset'] and resumed['preset'] is None
    assert resumed['prompt'].startswith('Your previous turn was interrupted')
    with env.db() as db:
        assert db.execute('SELECT status FROM calls WHERE id=?', (first['call'],)).fetchone()[0] == 'interrupted'
    cp = env.checkpoint()
    assert git(env.repo, 'show', cp['accepted_commit'] + ':kept.txt') == 'work'  # earlier work retained
    assert cp['failures'] == 1


@pytest.mark.parametrize('point', ['loop_after_pending', 'loop_after_cas'])
def test_acceptance_crash_converges_to_same_commit(env, point):
    env.actions(writer('done', {'done.txt': 'x'}), review())
    run_child(env, point)
    pending = env.checkpoint()['pending']
    assert pending['intent'] == 'DONE'
    assert env.run() == 0
    cp = env.checkpoint()
    assert cp['accepted_commit'] == pending['candidate']['commit'] == env.ref()
    assert len(env.launches()) == 2


def test_reviewer_crash_during_wait_keeps_candidate(env):
    now = [1000.0]
    env.actions(writer('done', {'done.txt': 'x'}), review())
    assert env.run(Admit({'reviewer': [wait('low', 2000.0)]}), clock=lambda: now[0]) == 75
    run_child(env, 'loop_after_active')  # reviewer prepared after recheck, controller killed
    assert env.checkpoint()['active']['stage'] == 'reviewer'
    assert env.run() == 0
    assert [l['role'] for l in env.launches()] == ['writer', 'reviewer']


# --- B1/B4: CAS and drift ---------------------------------------------------------------------

def test_failed_cas_never_reports_done(env, monkeypatch):
    env.actions(writer('done', {'done.txt': 'x'}), review())
    from forge.gitops import Git
    monkeypatch.setattr(Git, 'cas', lambda self, candidate: False)
    assert env.run() == 2
    cp = env.checkpoint()
    assert cp['status'] != 'DONE' and cp['pending'] is not None and env.ref() == env.base


def test_pending_head_matching_neither_old_nor_candidate_blocks(env):
    env.actions(writer('done', {'done.txt': 'x'}), review())
    run_child(env, 'loop_after_pending')
    git(env.repo, 'update-ref', 'refs/heads/forge/loop', commit(env.repo, env.base, 'x'))
    assert env.run() == 2
    assert env.checkpoint()['status'] != 'DONE'


def test_drift_between_steps_blocks_before_writer(env):
    env.actions(writer('progress', {'a.txt': '1'}), writer('done'))
    assert env.run(max_steps=1) == 3
    accepted = env.ref()
    moved = commit(env.repo, accepted, 'external')
    git(env.repo, 'update-ref', 'refs/heads/forge/loop', moved)
    assert env.run() == 2
    assert len(env.launches()) == 1 and env.ref() == moved


def test_drift_during_review_blocks_acceptance(env):
    env.actions(writer('done', {'done.txt': 'x'}), review())
    moved = commit(env.repo, env.base, 'external')

    def admit(account, profile, stage, now):
        if stage == 'reviewer':
            git(env.repo, 'update-ref', 'refs/heads/forge/loop', moved)
        return ALLOW
    assert env.run(admit) == 2
    assert env.ref() == moved and env.checkpoint()['status'] != 'DONE'


def test_missing_or_checked_out_loop_ref_blocks_without_calls(env):
    index = (env.repo / '.git/index').read_bytes()
    git(env.repo, 'branch', '-D', 'forge/loop')
    admit = Admit()
    assert env.run(admit) == 2
    assert admit.calls == [] and not env.checkpoint_path().exists()
    git(env.repo, 'branch', 'forge/loop')
    head = (env.repo / '.git/HEAD').read_text()
    (env.repo / '.git/HEAD').write_text('ref: refs/heads/forge/loop\n')
    assert env.run(admit) == 2
    (env.repo / '.git/HEAD').write_text(head)
    worktree = env.repo / '.git/worktrees/other'
    worktree.mkdir(parents=True)
    (worktree / 'HEAD').write_text('ref: refs/heads/forge/loop\n')
    (worktree / 'locked').write_text('locked')
    assert env.run(admit) == 2
    (worktree / 'HEAD').chmod(0)
    assert env.run(admit) == 2
    (worktree / 'HEAD').chmod(0o600)
    assert admit.calls == [] and env.launches() == []
    assert (env.repo / '.git/index').read_bytes() == index
    assert git(env.repo, 'rev-parse', 'refs/heads/main') == env.base


def test_changed_goal_or_config_blocks_without_writes(env, capsys):
    env.actions(writer('progress', {'a.txt': '1'}))
    assert env.run(max_steps=1) == 3
    before = env.checkpoint_path().read_bytes()
    env.configure(failures=5)
    assert env.run() == 2
    assert 'config_sha changed' in capsys.readouterr().err
    assert env.checkpoint_path().read_bytes() == before
    env.configure()
    env.goal.write_text('Another goal\n')
    assert env.run() == 2
    assert 'fresh state root' in capsys.readouterr().err


# --- acceptance rules -------------------------------------------------------------------------

@pytest.mark.parametrize('change', [{'writes': {'docs/source/spec.md': 'evil'}}, {'delete': ['docs/source/spec.md']},
                                    {'chmod': ['docs/source/spec.md']}])
def test_protected_path_rejection_resets_workspace_and_session(env, change):
    env.actions({**writer('done'), **change}, writer('done', {'done.txt': 'x'}), review())
    assert env.run(max_steps=2) == 0
    cp = env.checkpoint()
    launches = env.launches()
    assert launches[1]['resume'] is None and launches[1]['preset'] != launches[0]['preset']  # fresh session
    assert 'Protected paths' in launches[1]['prompt']
    assert git(env.repo, 'show', cp['accepted_commit'] + ':docs/source/spec.md') == 'spec'
    assert list((env.config.state_dir / 'ws').glob('*/rejected-*'))  # rejected evidence retained


def test_unchanged_progress_consumes_failure_budget(env):
    env.configure(failures=2)
    env.actions(writer('progress'), writer('progress'))
    assert env.run(max_steps=3) == 2
    cp = env.checkpoint()
    assert cp['status'] == 'BLOCKED' and cp['failures'] == 2 and env.ref() == env.base


def test_agent_blocked_preserves_verbatim_blocker(env):
    env.actions(writer('blocked', {'wip.txt': 'x'}, blocker='Need the asset license decision.'))
    assert env.run() == 2
    cp = env.checkpoint()
    assert cp['status'] == 'BLOCKED' and cp['blocker'] == 'Need the asset license decision.'
    assert env.ref() == env.base
    assert (next((env.config.state_dir / 'ws').glob('*/src')) / 'wip.txt').exists()


def test_gate_failure_feeds_back_to_pinned_writer(env):
    env.actions(writer('done', {'FAIL': 'x'}), writer('done', {}, delete=['FAIL']), review())
    assert env.run(max_steps=2) == 0
    first, second = env.launches()[:2]
    assert second['resume'] == first['preset'] and 'Gate failed' in second['prompt']
    assert env.checkpoint()['failures'] == 1


def test_review_fail_feeds_findings_back(env):
    env.actions(writer('done', {'done.txt': 'x'}), review('FAIL', ['missing test']), writer('done', {'t.txt': 'y'}),
                review('PASS'))
    assert env.run(max_steps=2) == 0
    assert 'missing test' in env.launches()[2]['prompt']
    assert git(env.repo, 'show', env.ref() + ':t.txt') == 'y'


@pytest.mark.parametrize('gate', ['/nonexistent-compiler', 'echo error: could not compile >&2; exit 101',
                                  'echo running 0 tests; exit 5', 'sleep 30'])
def test_compile_setup_timeout_and_no_tests_never_fake_success(env, gate):
    env.configure(gate=gate, timeout=2, failures=1)
    env.actions(writer('done', {'done.txt': 'x'}))
    assert env.run() == 2
    cp = env.checkpoint()
    assert cp['status'] == 'BLOCKED' and env.ref() == env.base
    assert [l['role'] for l in env.launches()] == ['writer']  # no reviewer after a failing gate


# --- security, status, isolation --------------------------------------------------------------

def test_sandbox_mounts_env_and_no_state_leak(env):
    env.actions(writer('done', {'done.txt': 'x'}), review())
    assert env.run() == 0
    writer_call, reviewer_call = (l['call'] for l in env.launches())
    w = manifest(env.config.state_dir, writer_call)
    r = manifest(env.config.state_dir, reviewer_call)
    work = {m['dest']: m for m in w['mounts']}['/work']
    assert work['mode'] == 'rw' and work['source'].endswith('/src')
    rwork = {m['dest']: m for m in r['mounts']}['/work']
    assert rwork['mode'] == 'ro' and '/review-' in rwork['source']
    assert 'CARGO_TARGET_DIR' in w['argv'] and 'CARGO_TARGET_DIR' not in r['argv']
    for m in w['mounts'] + r['mounts']:
        assert m['source'] in (str(Path(m['source'])),)
        assert not m['source'].startswith(str(env.config.state_dir / 'loop'))
        assert m['source'] != str(env.config.state_dir)


def test_loop_status_is_read_only(env, capsys):
    from forge.cli import main
    assert main(['loop-status', '--config', str(env.config.path), '--goal', str(env.goal)]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'NONE'
    assert not env.config.state_dir.exists()
    env.actions(writer('done', {'done.txt': 'x'}), review())
    assert env.run() == 0
    db = env.config.state_dir / 'forge.db'
    before = (db.stat().st_mtime_ns, env.checkpoint_path().read_bytes())
    assert main(['loop-status', '--config', str(env.config.path), '--goal', str(env.goal)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['status'] == 'DONE' and out['accepted_commit'] == env.ref()
    assert (db.stat().st_mtime_ns, env.checkpoint_path().read_bytes()) == before


def test_no_mutating_loop_cli_and_legacy_marked():
    from forge.cli import parser
    choices = parser()._subparsers._group_actions[0].choices
    assert 'loop' not in choices and 'loop-status' in choices
    assert 'legacy' in choices['run'].description.lower()


def test_result_summary_is_regenerated_from_checkpoint(env):
    env.actions(writer('done', {'done.txt': 'x'}), review())
    assert env.run() == 0
    summary = env.checkpoint_path().parent / 'result.md'
    assert 'DONE' in summary.read_text()
    summary.unlink()
    assert env.run() == 0
    assert 'DONE' in summary.read_text() and env.checkpoint()['accepted_commit'] in summary.read_text()


def test_no_legacy_runtime_imports_or_dispatch(env, monkeypatch):
    probe = subprocess.run([sys.executable, '-c', 'import sys, forge.loop; print(sorted(m for m in sys.modules if m in '
                            '("forge.roles","forge.ingest","forge.docsindex","forge.canary","forge.engine")))'],
                           env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')}, capture_output=True, text=True, check=True)
    assert probe.stdout.strip() == '[]'
    from forge import engine
    monkeypatch.setattr(engine.Engine, 'run', lambda *a, **k: pytest.fail('legacy Engine.run dispatched'))
    env.actions(writer('done', {'done.txt': 'x'}), review())
    assert env.run() == 0


# --- C1: lock contention and capsule updates through the wrapper seam -------------------------

def hold_lock(state_dir):
    state_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(state_dir / 'controller.lock', os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def test_lock_contention_rc4_writes_nothing_and_calls_nothing(env):
    capsule = {'status': 'READY', 'authorization': 'parent-granted', 'window_end': 9999999999}
    env.capsule.write_text(json.dumps(capsule))
    raw = env.capsule.read_bytes()
    admit = Admit()
    env.actions(writer('done', {'done.txt': 'x'}))
    fd = hold_lock(env.config.state_dir)
    try:
        assert run_with_capsule(env.config, env.goal, env.capsule, admit=admit) == 4
        assert env.run(admit) == 4
    finally:
        os.close(fd)
    assert admit.calls == [] and env.launches() == []
    assert env.capsule.read_bytes() == raw and not env.checkpoint_path().exists()
    assert not (env.config.state_dir / 'forge.db').exists()
    # With an existing checkpoint, its bytes are unchanged as well.
    env.actions(writer('progress', {'a.txt': '1'}))
    assert run_with_capsule(env.config, env.goal, env.capsule, admit=admit) == 3
    before, raw = env.checkpoint_path().read_bytes(), env.capsule.read_bytes()
    calls = len(admit.calls)
    fd = hold_lock(env.config.state_dir)
    try:
        assert run_with_capsule(env.config, env.goal, env.capsule, admit=admit) == 4
    finally:
        os.close(fd)
    assert env.checkpoint_path().read_bytes() == before and env.capsule.read_bytes() == raw and len(admit.calls) == calls


def test_genuine_block_is_rc2_and_wrapper_never_nests_lock(env, monkeypatch):
    env.capsule.write_text(json.dumps({'status': 'READY'}))
    entries = []
    original = loop.controller_lock

    def counting(root):
        entries.append(root)
        return original(root)
    monkeypatch.setattr(loop, 'controller_lock', counting)
    env.actions(writer('blocked', blocker='no'))
    assert run_with_capsule(env.config, env.goal, env.capsule, admit=Admit()) == 2
    assert len(entries) == 1
    assert json.loads(env.capsule.read_text())['status'] == 'BLOCKED'


@pytest.mark.parametrize('rc', [0, 2, 3, 75, 130])
def test_parent_stopped_capsule_survives_any_return(env, monkeypatch, rc):
    env.capsule.write_text(json.dumps({'status': 'READY', 'authorization': 'old', 'window_end': 1}))

    def fake_run(config, goal, **kwargs):
        stopped = {'status': 'STOPPED', 'authorization': 'revoked', 'window_end': 2, 'stopped': True}
        loop.atomic_json(env.capsule, stopped)
        return rc
    monkeypatch.setattr(loop, 'run_loop', fake_run)
    assert run_with_capsule(env.config, env.goal, env.capsule, admit=Admit()) == rc
    assert json.loads(env.capsule.read_bytes()) == {'status': 'STOPPED', 'authorization': 'revoked', 'window_end': 2, 'stopped': True}


@pytest.mark.parametrize('rc,status', [(0, 'DONE'), (2, 'BLOCKED'), (3, 'RUNNING'), (75, 'WAITING'), (130, 'STOPPED')])
def test_non_stopped_capsule_changes_only_status_and_recheck(env, monkeypatch, rc, status):
    parent = {'status': 'WAITING', 'recheck_at': 5.0, 'authorization': 'a', 'automatic_resume': False,
              'restart_forbidden': True, 'window_end': 7, 'engine_sha': 'abc'}
    env.capsule.write_text(json.dumps(parent))

    def fake_run(config, goal, **kwargs):
        changed = {**parent, 'authorization': 'b'}
        loop.atomic_json(env.capsule, changed)
        return rc
    monkeypatch.setattr(loop, 'run_loop', fake_run)
    monkeypatch.setattr(loop, 'loop_status', lambda config, goal: {'waiting': {'recheck_at': 99.0}})
    assert run_with_capsule(env.config, env.goal, env.capsule, admit=Admit()) == rc
    after = json.loads(env.capsule.read_text())
    expected = {**parent, 'authorization': 'b', 'status': status, 'recheck_at': 99.0 if rc == 75 else None}
    assert after == expected


def test_capsule_gates_fail_closed_without_writes(env):
    admit = Admit()
    assert run_with_capsule(env.config, env.goal, env.capsule, admit=admit) == 2  # missing: never recreated
    assert not env.capsule.exists()
    env.capsule.write_text('{not json')
    assert run_with_capsule(env.config, env.goal, env.capsule, admit=admit) == 2
    env.capsule.write_text(json.dumps({'status': 'STOPPED'}))
    raw = env.capsule.read_bytes()
    assert run_with_capsule(env.config, env.goal, env.capsule, admit=admit) == 130
    assert env.capsule.read_bytes() == raw and admit.calls == [] and not env.config.state_dir.exists()
    assert update_capsule(env.capsule, 4) is False and env.capsule.read_bytes() == raw


# --- K5 review regressions (reviewer reproductions as inputs; SIMULATED) ---------------------

def test_k5_01_symbolic_loop_ref_aliasing_checked_out_branch_blocks(env):
    git(env.repo, 'symbolic-ref', 'refs/heads/forge/loop', 'refs/heads/main')
    env.actions(writer('done', {'done.txt': 'accepted'}), review())
    admit = Admit()
    assert env.run(admit) == 2
    assert git(env.repo, 'rev-parse', 'refs/heads/main') == env.base
    assert admit.calls == [] and env.launches() == [] and not env.checkpoint_path().exists()


@pytest.mark.parametrize('checkout_stage', ['writer', 'reviewer'])
def test_k5_01_ref_checked_out_during_invocation_blocks(env, checkout_stage):
    env.actions(writer('done', {'done.txt': 'accepted'}), review())

    def admit(account, profile, stage, now):
        if stage == checkout_stage:
            git(env.repo, 'checkout', '-q', 'forge/loop')
        return ALLOW
    assert env.run(admit) == 2
    assert env.ref() == env.base and not (env.repo / 'done.txt').exists()
    cp = env.checkpoint()
    assert cp['status'] != 'DONE' and cp['active'] is None
    if checkout_stage == 'writer':
        assert env.launches() == []
    else:
        assert [l['role'] for l in env.launches()] == ['writer'] and cp['candidate']['gate']['rc'] == 0


@pytest.mark.parametrize('damage', ['delete_gate', 'delete_review', 'tamper_gate', 'tamper_review'])
def test_k5_02_pending_requires_existing_matching_receipts(env, damage):
    env.actions(writer('done', {'done.txt': 'accepted'}), review())
    run_child(env, 'loop_after_pending')
    cp = env.checkpoint()
    gate = Path(cp['candidate']['gate']['log'])
    review_result = env.config.state_dir / 'calls' / cp['pending']['review']['call'] / 'result.json'
    target = gate if damage.endswith('gate') else review_result
    if damage.startswith('delete'):
        target.unlink()
    else:
        target.write_bytes(target.read_bytes() + b' ')
    assert env.run() == 2
    assert env.ref() == env.base and env.checkpoint()['status'] != 'DONE' and env.checkpoint()['pending']
    assert len(env.launches()) == 2  # no new paid call


def test_k5_03_replay_rejects_schema_corrupt_completed_result(env):
    env.actions(writer('done', {'done.txt': 'accepted'}), review())
    run_child(env, 'runner_after_done')
    result = Path(env.checkpoint()['active']['raw_dir']) / 'result.json'
    data = json.loads(result.read_text())
    data['final_json'] = None
    result.write_text(json.dumps(data))
    assert env.run() == 2
    cp = env.checkpoint()
    assert cp['status'] == 'BLOCKED' and env.ref() == env.base and len(env.launches()) == 1
    assert result.exists()
    with env.db() as db:
        assert db.execute('SELECT count(*) FROM call_effects').fetchone()[0] == 0


def test_k5_03_replay_crosschecks_pinned_call_identity(env):
    env.actions(writer('done', {'done.txt': 'accepted'}), review())
    run_child(env, 'runner_after_done')
    with env.db() as db:
        db.execute("UPDATE calls SET model='conflicting-model',effort='high',account='acct-c' WHERE role='writer'")
    assert env.run() == 2
    assert env.checkpoint()['status'] == 'BLOCKED' and env.ref() == env.base and len(env.launches()) == 1


def test_k5_04_loop_gate_never_shares_network(env, monkeypatch):
    config = CONFIG.format(gate='test ! -e FAIL', writers='["w1"]', reviewers='["r1"]', timeout=30, failures=3)
    env.config = load_config(env.write_config(config + '\n[exec]\ngate_network=true\n'))
    env.actions(writer('done', {'done.txt': 'accepted'}), review())
    from forge import sandbox
    original, seen = sandbox.gate_spec, []

    def capture(*args, **kwargs):
        spec = original(*args, **kwargs)
        seen.append(spec.network)
        return spec
    monkeypatch.setattr(sandbox, 'gate_spec', capture)
    assert env.run() == 0
    assert seen == [False]


def test_k5_05_blocking_admission_is_durable_and_terminal(env):
    env.actions(writer('done', {'done.txt': 'accepted'}), review())
    assert env.run(lambda *a: block('operator decision required')) == 2
    cp = env.checkpoint()
    assert cp['status'] == 'BLOCKED' and 'operator decision required' in cp['blocker']
    admit = Admit()
    assert env.run(admit) == 2
    assert admit.calls == [] and env.launches() == []


@pytest.mark.parametrize('crash', [None, 'loop_after_pending'])
def test_k5_06_progress_gate_failure_reaches_next_writer(env, crash):
    env.configure(gate='echo independent-progress-gate-error >&2; exit 101')
    env.actions(writer('progress', {'part.txt': 'work'}), writer('blocked', blocker='end reproduction'))
    if crash:
        run_child(env, crash)
        assert env.run(max_steps=1) == 2
    else:
        assert env.run(max_steps=2) == 2
    first, second = env.launches()
    assert env.ref() != env.base  # failing progress is still accepted, not DONE
    assert second['resume'] == first['preset']
    assert 'independent-progress-gate-error' in second['prompt'] and 'rc=101' in second['prompt']


@pytest.mark.parametrize('damage', ['input_tree', 'receipt_tree'])
def test_k5_fix2_pending_review_linkage_blocks_before_cas(env, damage):
    env.actions(writer('done', {'done.txt': 'yes'}), review())
    run_child(env, 'loop_after_pending')
    cp = env.checkpoint()
    if damage == 'input_tree':
        path = env.config.state_dir / 'calls' / cp['pending']['review']['call'] / 'input.json'
        data = json.loads(path.read_text())
        assert cp['pending']['candidate']['tree'] in data['prompt']
        data['prompt'] = data['prompt'].replace(cp['pending']['candidate']['tree'], '0' * 40)
        path.write_text(json.dumps(data))
    else:
        cp['pending']['review']['tree'] = '0' * 40
        env.checkpoint_path().write_text(json.dumps(cp))
    assert env.run() == 2
    assert env.ref() == env.base
    assert env.checkpoint()['status'] == 'BLOCKED' and env.checkpoint()['pending']
    assert len(env.launches()) == 2


@pytest.mark.parametrize('damage', ['row_session_out', 'result_sid', 'input_images'])
def test_k5_fix2_completed_replay_rejects_identity_conflicts_before_effects(env, damage):
    env.actions(writer('done', {'done.txt': 'yes'}), review())
    run_child(env, 'runner_after_done')
    a = env.checkpoint()['active']
    if damage == 'row_session_out':
        with env.db() as db:
            db.execute('UPDATE calls SET session_out=? WHERE id=?', ('conflicting-session', a['call_id']))
    else:
        path = Path(a['raw_dir']) / ('input.json' if damage == 'input_images' else 'result.json')
        data = json.loads(path.read_text())
        data['images' if damage == 'input_images' else 'sid'] = (
            [{'path': '/foreign', 'content_sha256': 'wrong'}] if damage == 'input_images' else 'conflicting-session')
        path.write_text(json.dumps(data))
    assert env.run() == 2
    assert env.ref() == env.base and len(env.launches()) == 1
    assert env.checkpoint()['status'] == 'BLOCKED' and env.checkpoint()['active'] == a
    with env.db() as db:
        assert db.execute('SELECT count(*) FROM call_effects').fetchone()[0] == 0


@pytest.mark.parametrize('damage', ['missing_input_hash', 'null_review', 'null_gate', 'unsupported_intent'])
def test_k5_fix2_incomplete_persisted_receipts_fail_closed(env, damage):
    env.actions(writer('done', {'done.txt': 'yes'}), review())
    run_child(env, 'loop_after_pending')
    cp = env.checkpoint()
    if damage == 'missing_input_hash':
        del cp['pending']['review']['input_sha256']
    elif damage == 'unsupported_intent':
        cp['pending']['intent'] = 'UNKNOWN'
    else:
        cp['pending'][damage.removeprefix('null_')] = None
    env.checkpoint_path().write_text(json.dumps(cp))
    assert env.run() == 2
    assert env.checkpoint()['status'] == 'BLOCKED' and env.checkpoint()['pending']
    assert env.ref() == env.base and len(env.launches()) == 2


def test_k5_fix2_completed_review_needs_readonly_not_resume_capability(env, monkeypatch):
    monkeypatch.setattr(loop, '_capabilities', lambda config, state, p:
                        {'text', 'resume'} if p.id == 'w1' else {'text', 'readonly'})
    env.actions(writer('done', {'done.txt': 'yes'}), review())
    assert env.run() == 0
    assert env.checkpoint()['accepted_commit'] == env.ref()


class GrokShapedAdapter(FakeAdapter):
    """FIXTURE with the production grok SID contract: no streamed events, SID only from finish()."""
    def parse_line(self, line):
        return None

    def finish(self, call, rc, events, stdout, stderr):
        lines = [json.loads(line) for line in stdout.decode().splitlines() if line.strip()]
        sid = next((line['sid'] for line in lines if 'sid' in line), None)
        answer = next((line['answer'] for line in lines if 'answer' in line), None)
        if rc != 0 or answer is None:
            return Native('absent', sid, error_fields={'stderr': stderr})
        return Native('success', sid, '```json\n' + json.dumps(answer) + '\n```', call.model, True)


def grok_writer(env):
    env.config = load_config(env.write_config(
        CONFIG.format(gate='test ! -e FAIL', writers='["w1"]', reviewers='["r1"]', timeout=30, failures=3)
        .replace('harness = "claude"', 'harness = "grok"', 1).replace('family = "anthropic"', 'family = "xai"', 1)))
    fake = FakeAdapter(env.queue, env.log)
    env.monkeypatch.setattr(loop, '_adapter', lambda p: GrokShapedAdapter(env.queue, env.log)
                            if p.harness == 'grok' else fake)


def test_r3_01_grok_writer_and_reviewer_reach_done(env):
    grok_writer(env)
    env.actions(writer('done', {'done.txt': 'yes'}), review())
    assert env.run() == 0
    cp = env.checkpoint()
    assert cp['status'] == 'DONE' and cp['accepted_commit'] == env.ref() != env.base
    assert [l['role'] for l in env.launches()] == ['writer', 'reviewer']
    with env.db() as db:
        assert db.execute('SELECT count(*) FROM call_effects').fetchone()[0] == 2


@pytest.mark.parametrize('damage', [None, 'row_session_out', 'result_sid'])
def test_r3_01_grok_replay_checks_durable_sid_pins_before_effects(env, damage):
    grok_writer(env)
    env.actions(writer('done', {'done.txt': 'yes'}), review())
    run_child(env, 'runner_after_done')
    a = env.checkpoint()['active']
    if damage == 'row_session_out':
        with env.db() as db:
            db.execute('UPDATE calls SET session_out=? WHERE id=?', ('conflicting-session', a['call_id']))
    elif damage == 'result_sid':
        path = Path(a['raw_dir']) / 'result.json'
        data = json.loads(path.read_text())
        data['sid'] = 'conflicting-session'
        path.write_text(json.dumps(data))
    if damage is None:
        assert env.run() == 0 and env.checkpoint()['status'] == 'DONE'
        assert len(env.launches()) == 2
        return
    assert env.run() == 2
    assert env.ref() == env.base and len(env.launches()) == 1
    assert env.checkpoint()['status'] == 'BLOCKED' and env.checkpoint()['active'] == a
    with env.db() as db:
        assert db.execute('SELECT count(*) FROM call_effects').fetchone()[0] == 0


class SimulatedCallClock:
    """Advance only when the fixture native call finishes; no real long sleep."""
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def simulated_long_call(env, clock, finished_at):
    class LongAdapter(FakeAdapter):
        def finish(self, *args):
            result = super().finish(*args)
            clock.now = finished_at
            return result
    env.monkeypatch.setattr(loop, '_adapter', lambda profile: LongAdapter(env.queue, env.log))


def test_simulated_wait_then_long_admitted_call_excludes_runtime(env):
    from forge import quota_metrics as qm
    clock = SimulatedCallClock()
    assert env.run(Admit({'writer': wait('five_hour', 100)}), clock=clock) == 75
    assert env.launches() == []
    clock.now = 100
    simulated_long_call(env, clock, 2800)
    env.actions(writer('progress', {'kept.txt': 'work'}))
    assert env.run(clock=clock) == 3
    with env.db() as db:
        closed_at, closure = db.execute('SELECT closed_at,closure FROM quota_episodes').fetchone()
    assert closed_at == 100
    assert json.loads(closure)['started_at'] == 100
    assert json.loads(closure)['confirmed_at'] == 2800
    report = qm.snapshot(env.config.state_dir / 'forge.db', 2800, live=False)
    # The cron gap [0,100] is uncovered; admitted execution [100,2800]
    # is covered active time, but contributes no quota wait.
    assert report['quota_walltime_seconds'] == 0
    assert sum(report['wait_seconds']['cause'].values()) == 0
    assert report['episodes']['closed'] == 1 and report['episodes']['open'] == 0


def test_simulated_cooldown_candidate_skipped_for_admitted_writer_has_no_wait(env):
    from forge import quota_metrics as qm
    from forge.state import State
    env.configure(writers='["w1", "w2"]')
    with State(env.config.state_dir, metrics=True) as state:
        with state.transaction() as db:
            db.execute("INSERT INTO accounts(id,state,recheck_at,outcome,reason) VALUES(?,?,?,?,?)",
                       ('acct-a', 'waiting', 100, 'quota_wait', 'five_hour'))
    clock = SimulatedCallClock()
    simulated_long_call(env, clock, 50)
    env.actions(writer('progress', {'kept.txt': 'work'}))
    assert env.run(clock=clock) == 3
    assert [item['profile'] for item in env.launches()] == ['w2']
    report = qm.snapshot(env.config.state_dir / 'forge.db', 50, live=False)
    assert report['episodes']['open'] == report['episodes']['closed'] == 0
    assert report['checks']['prestart_deferral'] == 0
    assert report['quota_walltime_seconds'] == 0
    assert sum(report['wait_seconds']['cause'].values()) == 0
