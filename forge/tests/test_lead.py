"""Optional lead mode. SIMULATED: fixture native programs, injected admission; real bwrap, git, SQLite and lock.

Live Lead/executor/reviewer evidence is a separate native canary and is never claimed here.
"""
import json

import pytest

from forge import loop, prompts
from forge.config import load_config
from test_loop import FAKE_NATIVE, Admit, Env, FakeAdapter, git, review

LEAD_CONFIG = '''protected = ["docs/source/**"]
[commands]
test = ["/usr/bin/sh", "-c", "test ! -e FAIL"]
[accounts.acct-a]
enabled = true
[accounts.acct-b]
enabled = true
[[profile]]
id = "lead1"
harness = "claude"
model = "claude-sonnet-5-5"
effort = "high"
family = "anthropic"
tiers = ["normal"]
account = "acct-a"
[[profile]]
id = "ex1"
harness = "claude"
model = "claude-haiku-5-5"
effort = "high"
family = "anthropic"
tiers = ["cheap"]
account = "acct-a"
[[profile]]
id = "ex2"
harness = "codex"
model = "gpt-6.1-sol"
effort = "high"
family = "openai"
tiers = ["normal", "strong"]
account = "acct-b"
[[profile]]
id = "rv1"
harness = "codex"
model = "gpt-6.1-sol"
effort = "medium"
family = "openai"
tiers = ["strong"]
account = "acct-b"
[[profile]]
id = "rv2"
harness = "claude"
model = "claude-opus-5-5"
effort = "high"
family = "anthropic"
tiers = ["strong"]
account = "acct-a"
[loop]
mode = "lead"
leads = ["lead1"]
writers = ["ex1", "ex2"]
reviewers = ["rv1", "rv2"]
branch = "forge/loop"
step_timeout = 30
max_failures = 3
fix_rounds = {fix_rounds}
'''
# Lead fixture extension: optionally echo a /work file into the Lead's answer to prove it read the accepted export.
LEAD_NATIVE = FAKE_NATIVE.replace("print(json.dumps({'answer': action['answer']}), flush=True)",
                                  "answer = action['answer']\n"
                                  "if action.get('echo'):\n"
                                  "    answer['summary'] = open(os.path.join('/work', action['echo'])).read()\n"
                                  "print(json.dumps({'answer': answer}), flush=True)")


class LeadFake(FakeAdapter):
    def build_argv(self, call, exe, in_dir):
        argv, stdin = super().build_argv(call, exe, in_dir)
        (in_dir / 'fake.py').write_text(LEAD_NATIVE)
        return argv, stdin


def task(scope=('notes.md', 'src', 'FAIL'), tier='cheap', **extra):
    return {'answer': {'disposition': 'task', 'kind': 'feature', 'mixture_rationale': None, 'objective': 'Add notes.',
                       'scope_in': list(scope), 'scope_rationale': None, 'scope_out': ['docs/source'],
                       'invariants': ['seed.txt unchanged'], 'acceptance': ['notes.md exists'],
                       'tests': ['gate passes'], 'docs_impact': 'notes.md', 'tier': tier,
                       'tier_rationale': 'one small file', 'question': None, **extra}}


def done(echo=None):
    action = {'answer': {'disposition': 'done', 'summary': 'goal met', 'evidence': ['notes.md'], 'limitations': []}}
    return {**action, 'echo': echo} if echo else action


def execute(status='done', writes=None, **extra):
    return {'writes': writes or {}, 'answer': {'status': status, 'summary': extra.pop('summary', 'executed'),
                                               'next': '', 'blocker': extra.pop('blocker', None)}, **extra}


@pytest.fixture
def lenv(tmp_path, operator_config, monkeypatch):
    environment = Env(tmp_path, operator_config, monkeypatch)
    environment.lead_config = lambda fix_rounds=2: setattr(
        environment, 'config', load_config(operator_config(LEAD_CONFIG.format(fix_rounds=fix_rounds))))
    environment.lead_config()
    monkeypatch.setattr(loop, '_adapter', lambda profile: LeadFake(environment.queue, environment.log))
    return environment


def roles(env):
    return [(l['role'], l['profile']) for l in env.launches()]


def test_lead_task_executor_review_accept_then_same_lead_sid_reads_updated_docs_and_done_is_reviewed(lenv):
    lenv.actions(task(), execute(writes={'notes.md': 'NOTE-v1\n'}), review('PASS'),
                 done(echo='notes.md'), review('PASS'))
    admit = Admit()
    assert lenv.run(admit, max_steps=10) == loop.DONE
    launches = lenv.launches()
    assert roles(lenv) == [('lead', 'lead1'), ('writer', 'ex1'), ('reviewer', 'rv1'), ('lead', 'lead1'), ('reviewer', 'rv1')]
    first_lead, executor, task_review, second_lead, final_review = launches
    for lead_call in (first_lead, second_lead):
        assert lead_call['prompt'].endswith('# Contract\n\n' + prompts.LEAD_CONTRACT)
        assert 'prefer substantial, coherent playable vertical slices' in lead_call['prompt']
    # Explicit Lead continuity: the second Lead call resumes the proven first SID with freshly rendered state.
    assert second_lead['resume'] == first_lead['preset'] and first_lead['resume'] is None
    assert executor['resume'] is None and executor['preset'] not in (first_lead['preset'],)
    assert not first_lead['writable'] and executor['writable'] and not task_review['writable']
    cp = lenv.checkpoint()
    accepted = git(lenv.repo, 'rev-parse', 'refs/heads/forge/loop')
    assert cp['status'] == 'DONE' and cp['accepted_commit'] == accepted == cp['last_task']['commit']
    assert git(lenv.repo, 'rev-parse', accepted + '^') == lenv.base  # one task commit, no duplicate DONE commit
    assert cp['last_task']['id'] == 'T1' and cp['tasks_accepted'] == 1 and cp['lead']['sid'] == first_lead['preset']
    assert 'Last accepted task receipt' in second_lead['prompt'] and accepted in second_lead['prompt']
    # The Lead read notes.md from the fresh export of the new accepted base.
    assert 'NOTE-v1' in final_review['prompt'] and 'Lead completion decision' in final_review['prompt']
    assert 'spec sha256 ' + cp['task']['spec_sha'] in final_review['prompt']
    # Executor prompt carries the bounded task, not a global planning contract.
    assert '"objective": "Add notes."' in executor['prompt'] and prompts.EXECUTOR_CONTRACT in executor['prompt']
    assert prompts.TASK_REVIEW_CONTRACT in task_review['prompt'] and 'Changed paths: notes.md' in task_review['prompt']
    assert [c[2] for c in admit.calls] == ['lead', 'writer', 'reviewer', 'lead', 'reviewer']


def test_runtime_consumes_tracked_templates(lenv, monkeypatch):
    """Prompt consumption: changing the tracked template changes the real runtime input."""
    monkeypatch.setitem(prompts.CONTRACTS, 'lead', 'MARKER-LEAD-TEMPLATE')
    monkeypatch.setitem(prompts.CONTRACTS, 'executor', 'MARKER-EXECUTOR-TEMPLATE')
    lenv.actions(task(), execute(writes={'notes.md': 'x\n'}))
    assert lenv.run(max_steps=2) == loop.BUDGET
    lead_call, executor = lenv.launches()
    assert lead_call['prompt'].endswith('# Contract\n\nMARKER-LEAD-TEMPLATE')
    assert executor['prompt'].endswith('# Contract\n\nMARKER-EXECUTOR-TEMPLATE')
    calls = lenv.config.state_dir / 'calls'
    inputs = [json.loads(p.read_text())['prompt'] for p in calls.glob('*/input.json')]
    assert sorted(i[-30:] for i in inputs) == sorted(l['prompt'][-30:] for l in lenv.launches())


def test_every_lead_mode_launch_consumes_max_steps(lenv):
    lenv.actions(task(), execute(writes={'notes.md': 'x\n'}))
    assert lenv.run(max_steps=1) == loop.BUDGET
    assert roles(lenv) == [('lead', 'lead1')] and lenv.checkpoint()['stage'] == 'writer'


def test_normal_tier_selects_normal_executor_and_other_family_reviewer(lenv):
    lenv.actions(task(tier='normal'), execute(writes={'notes.md': 'x\n'}), review('PASS'))
    assert lenv.run(max_steps=3) == loop.BUDGET
    assert roles(lenv) == [('lead', 'lead1'), ('writer', 'ex2'), ('reviewer', 'rv2')]


def test_gate_fail_and_review_fail_repair_with_same_executor_sid_within_budget(lenv):
    lenv.actions(task(), execute(writes={'notes.md': 'x\n', 'FAIL': '1'}),
                 execute(delete=['FAIL']), review('FAIL', ['notes.md lacks a heading']),
                 execute(writes={'notes.md': '# Notes\n'}), review('PASS'), done(), review('PASS'))
    assert lenv.run(max_steps=20) == loop.DONE
    launches = lenv.launches()
    executors = [l for l in launches if l['role'] == 'writer']
    assert len(executors) == 3
    assert executors[1]['resume'] == executors[2]['resume'] == executors[0]['preset']
    assert 'Correction round 1 of 2' in executors[1]['prompt'] and 'Gate failed' in executors[1]['prompt']
    assert 'Correction round 2 of 2' in executors[2]['prompt'] and 'lacks a heading' in executors[2]['prompt']
    reviewers = [l['preset'] or 'thread-' + l['call'] for l in launches if l['role'] == 'reviewer']
    assert len(set(reviewers)) == len(reviewers)  # every review is a fresh session
    cp = lenv.checkpoint()
    assert cp['last_task']['fixes'] == 2 and git(lenv.repo, 'show', cp['accepted_commit'] + ':notes.md') == '# Notes'


def test_exhausted_corrections_reconcile_once_then_block(lenv):
    lenv.lead_config(fix_rounds=0)
    lenv.actions(task(), execute(writes={'notes.md': 'x\n', 'FAIL': '1'}),
                 task(objective='Smaller: add notes only.'), execute(writes={'notes.md': 'y\n', 'FAIL': '1'}))
    assert lenv.run(max_steps=10) == loop.BLOCKED
    assert roles(lenv) == [('lead', 'lead1'), ('writer', 'ex1'), ('lead', 'lead1'), ('writer', 'ex1')]
    lead2 = lenv.launches()[2]
    assert lead2['resume'] == lenv.launches()[0]['preset'] and '# Handoff' in lead2['prompt'] and '"task_id": "T1"' in lead2['prompt']
    second_executor = lenv.launches()[3]
    assert second_executor['resume'] is None  # replacement task: fresh executor context
    cp = lenv.checkpoint()
    assert cp['reconciliations'] == 1 and cp['task']['replaces'] == 'T1' and 'reconciliation budget' in cp['blocker']
    assert git(lenv.repo, 'rev-parse', 'refs/heads/forge/loop') == lenv.base
    assert (lenv.config.state_dir / cp['rejected'][-1]).joinpath('FAIL').exists()


def test_out_of_scope_or_protected_change_blocks_and_preserves_work(lenv):
    lenv.actions(task(scope=['notes.md']), execute(writes={'notes.md': 'x\n', 'other.txt': 'y\n'}))
    assert lenv.run(max_steps=5) == loop.BLOCKED
    cp = lenv.checkpoint()
    assert 'out-of-scope' in cp['blocker'] and "'other.txt'" in cp['blocker']
    ws = lenv.config.state_dir / 'ws' / cp['goal_id'] / 'src'
    assert (ws / 'other.txt').exists() and cp['rejected'] == []


def test_reviewer_block_and_lead_question_are_durable_blocks(lenv):
    lenv.actions(task(), execute(writes={'notes.md': 'x\n'}), review('BLOCK', ['needs owner decision']))
    assert lenv.run(max_steps=5) == loop.BLOCKED
    assert 'needs owner decision' in lenv.checkpoint()['blocker']


def test_lead_blocked_question(tmp_path, lenv):
    lenv.actions({'answer': {'disposition': 'blocked', 'blocker': 'two specs conflict', 'question': 'Which wins?'}})
    assert lenv.run(max_steps=5) == loop.BLOCKED
    assert 'Which wins?' in lenv.checkpoint()['blocker'] and roles(lenv) == [('lead', 'lead1')]


def test_final_verification_fail_returns_to_lead(lenv):
    lenv.actions(done(), review('FAIL', ['README promises a CLI that does not exist']), task(),
                 execute(writes={'notes.md': 'x\n'}))
    assert lenv.run(max_steps=4) == loop.BUDGET
    third = lenv.launches()[2]
    assert third['role'] == 'lead' and 'Final verification of your DONE decision failed' in third['prompt']
    assert lenv.checkpoint()['status'] == 'RUNNING'


def test_receipt_tamper_blocks_before_cas_and_clean_replay_accepts_once(lenv, monkeypatch):
    def crash(point):
        if point == 'loop_after_pending':
            raise RuntimeError('SIMULATED crash after pending')
    monkeypatch.setattr(loop, 'crash', crash)
    lenv.actions(task(), execute(writes={'notes.md': 'x\n'}), review('PASS'))
    assert lenv.run(max_steps=5) == loop.BLOCKED
    cp = lenv.checkpoint()
    assert cp['pending']['intent'] == 'TASK'
    monkeypatch.setattr(loop, 'crash', lambda point: None)
    tampered = json.loads(json.dumps(cp))
    tampered['pending']['task']['spec_sha'] = '0' * 64
    lenv.checkpoint_path().write_text(json.dumps(tampered))
    assert lenv.run(max_steps=5) == loop.BLOCKED
    assert git(lenv.repo, 'rev-parse', 'refs/heads/forge/loop') == lenv.base
    cp['status'] = 'RUNNING'
    lenv.checkpoint_path().write_text(json.dumps(cp))
    lenv.actions(done(), review('PASS'))
    assert lenv.run(max_steps=5) == loop.DONE
    accepted = git(lenv.repo, 'rev-parse', 'refs/heads/forge/loop')
    assert git(lenv.repo, 'rev-parse', accepted + '^') == lenv.base and lenv.checkpoint()['tasks_accepted'] == 1


def test_corrupt_lead_extension_fails_closed(lenv):
    lenv.actions(task(), execute(writes={'notes.md': 'x\n'}))
    assert lenv.run(max_steps=1) == loop.BUDGET
    cp = lenv.checkpoint()
    cp['task']['spec']['objective'] = 'silently widened'
    lenv.checkpoint_path().write_text(json.dumps(cp))
    assert lenv.run(max_steps=1) == loop.BLOCKED
    assert len(lenv.launches()) == 1


@pytest.mark.parametrize('change', [
    {'tier': 'strong'}, {'scope_in': ['/etc']}, {'scope_in': ['../x']}, {'scope_in': ['**']}, {'kind': 'mixed'},
    {'question': 'why?'}, {'acceptance': []}, {'objective': ''}, {'extra': 1}, {'scope_in': ['x'] * 21},
    {'docs_impact': 'x' * 5000},
])
def test_lead_task_schema_rejects_unbounded_or_malformed(change):
    assert loop._valid_lead(task()['answer'])
    assert not loop._valid_lead({**task()['answer'], **change})


def test_lead_schema_other_dispositions():
    assert loop._valid_lead({**task()['answer'], 'scope_in': ['**'], 'scope_rationale': 'repo-wide rename'})
    assert loop._valid_lead({**task()['answer'], 'kind': 'mixed', 'mixture_rationale': 'bug fix plus its docs'})
    assert not loop._valid_lead({'disposition': 'blocked', 'blocker': 'x', 'question': ''})
    assert not loop._valid_lead({'disposition': 'done', 'summary': 'x', 'evidence': [], 'limitations': []})
    assert not loop._valid_lead({'disposition': 'maybe'})
    assert loop._valid_lead_review({'verdict': 'BLOCK', 'findings': ['x']})
    assert not loop._valid_lead_review({'verdict': 'FAIL', 'findings': []})


def test_final_done_receipts_can_be_recovered_before_cas(lenv):
    """Regression: DONE is a valid lead intent, alongside TASK (not RUNNING)."""
    lenv.actions(task(), execute(writes={'notes.md': 'x\n'}), review('PASS'), done(), review('PASS'))
    assert lenv.run(max_steps=5) == loop.DONE


def test_runtime_records_admission_and_confirmed_continuation(lenv):
    """SIMULATED native/admission, actual runtime metering transactions."""
    from forge import quota_metrics as qm
    lenv.actions(task())
    assert lenv.run(Admit({'lead': loop.wait('low quota: five_hour', 200)}), clock=lambda: 100) == loop.WAITING
    report = qm.snapshot(lenv.config.state_dir / 'forge.db', 150)
    assert report['available'] and report['episodes']['open'] == 1
    assert report['frequency']['five_h_episodes'] == 1
    assert lenv.run(clock=lambda: 201, max_steps=1) == loop.BUDGET
    report = qm.snapshot(lenv.config.state_dir / 'forge.db', 220)
    assert report['episodes']['closed'] == 1 and report['checks']['continuation'] == 1
    assert report['observation']['coverage'] == 'partial'


@pytest.mark.parametrize('point', ['loop_after_pending', 'loop_after_cas', 'loop_after_task_finalize'])
def test_task_acceptance_crash_replays_exactly_once(lenv, monkeypatch, point):
    def crash(where):
        if where == point:
            raise RuntimeError('SIMULATED crash')
    monkeypatch.setattr(loop, 'crash', crash)
    lenv.actions(task(), execute(writes={'notes.md': 'x\n'}), review('PASS'))
    assert lenv.run(max_steps=3) == loop.BLOCKED
    monkeypatch.setattr(loop, 'crash', lambda point: None)
    lenv.actions(done(), review('PASS'))
    assert lenv.run(max_steps=2) == loop.DONE
    cp = lenv.checkpoint()
    assert cp['tasks_accepted'] == 1 and cp['last_task']['executor_sid']
    assert git(lenv.repo, 'rev-parse', cp['accepted_commit'] + '^') == lenv.base


def test_correction_ordinal_reserved_across_prelaunch_crash(lenv, monkeypatch):
    lenv.actions(task(), execute(writes={'notes.md': 'x\n', 'FAIL': 'x'}))
    assert lenv.run(max_steps=2) == loop.BUDGET
    assert lenv.checkpoint()['task']['fixes'] == 1
    def crash(where):
        if where == 'loop_after_active':
            raise RuntimeError('SIMULATED prelaunch crash')
    monkeypatch.setattr(loop, 'crash', crash)
    assert lenv.run(max_steps=1) == loop.BLOCKED
    assert lenv.checkpoint()['task']['fixes'] == 1
    monkeypatch.setattr(loop, 'crash', lambda point: None)
    lenv.actions(execute(delete=['FAIL']), review('PASS'))
    assert lenv.run(max_steps=1) == loop.BUDGET
    assert lenv.checkpoint()['last_task']['fixes'] == 1
    executors = [c for c in lenv.launches() if c['role'] == 'writer']
    assert executors[1]['resume'] == executors[0]['preset']


def test_runtime_metrics_repaired_even_when_call_effects_already_applied(lenv, monkeypatch):
    from forge import quota_metrics as qm
    from forge.state import State
    lenv.actions(task())
    def crash(point):
        if point == 'loop_after_effects':
            raise RuntimeError('SIMULATED effects/checkpoint crash')
    monkeypatch.setattr(loop, 'crash', crash)
    assert lenv.run(max_steps=1) == loop.BLOCKED
    with State(lenv.config.state_dir, metrics=True) as state, state.transaction() as db:
        assert db.execute('SELECT count(*) FROM call_effects').fetchone()[0] == 1
        db.execute("DELETE FROM quota_events WHERE kind='continuation'")
    monkeypatch.setattr(loop, 'crash', lambda point: None)
    lenv.actions(execute(writes={'notes.md': 'x\n'}))
    assert lenv.run(max_steps=1) == loop.BUDGET
    report = qm.snapshot(lenv.config.state_dir / 'forge.db', 1)
    assert report['checks']['continuation'] == 2  # repaired Lead + executor result


def test_stopped_and_locked_never_open_metrics(lenv):
    from forge.state import controller_lock
    lenv.capsule.write_text(json.dumps({'status': 'STOPPED'}))
    assert loop.run_with_capsule(lenv.config, lenv.goal, lenv.capsule, admit=Admit()) == loop.CANCELLED
    assert not (lenv.config.state_dir / 'forge.db').exists()
    with controller_lock(lenv.config.state_dir):
        assert lenv.run() == loop.LOCKED
    assert not (lenv.config.state_dir / 'forge.db').exists()


def test_admission_metric_commit_replays_one_event_and_episode(lenv, monkeypatch):
    from forge import quota_metrics as qm
    def crash(point):
        if point == 'loop_after_metric_admission':
            raise RuntimeError('SIMULATED checkpoint/event boundary')
    monkeypatch.setattr(loop, 'crash', crash)
    admission = Admit({'lead': loop.wait('low quota: five_hour', 200)})
    assert lenv.run(admission, clock=lambda: 100) == loop.BLOCKED
    monkeypatch.setattr(loop, 'crash', lambda point: None)
    assert lenv.run(admission, clock=lambda: 100) == loop.WAITING
    report = qm.snapshot(lenv.config.state_dir / 'forge.db', 150)
    assert report['frequency']['five_h_episodes'] == 1
    assert report['checks']['prestart_deferral'] == 1


def test_child_evidence_drift_blocks_result_replay(lenv, monkeypatch):
    def crash(point):
        if point == 'loop_after_effects':
            raise RuntimeError('SIMULATED durable-result boundary')
    monkeypatch.setattr(loop, 'crash', crash)
    lenv.actions(task())
    assert lenv.run(max_steps=1) == loop.BLOCKED
    cp = lenv.checkpoint()
    cp['active']['children']['capability']['fingerprint'] = '0' * 64
    lenv.checkpoint_path().write_text(json.dumps(cp))
    monkeypatch.setattr(loop, 'crash', lambda point: None)
    assert lenv.run(max_steps=1) == loop.BLOCKED
    assert 'manifest/checkpoint mismatch' in lenv.checkpoint()['blocker']
    assert len(lenv.launches()) == 1


@pytest.mark.parametrize('pattern', ['*', '**/*', '**/**'])
def test_whole_repository_wildcards_need_explicit_justification(pattern):
    spec = {**task()['answer'], 'scope_in': [pattern]}
    assert not loop._valid_lead(spec)
    assert loop._valid_lead({**spec, 'scope_rationale': 'A justified repository-wide rename.'})


def test_future_checkpoint_version_blocks_before_any_launch(lenv):
    lenv.actions(task())
    assert lenv.run(max_steps=1) == loop.BUDGET
    cp = lenv.checkpoint()
    cp['version'] = 2
    lenv.checkpoint_path().write_text(json.dumps(cp))
    lenv.actions(execute(writes={'notes.md': 'x\n'}))
    assert lenv.run(max_steps=1) == loop.BLOCKED
    assert len(lenv.launches()) == 1
