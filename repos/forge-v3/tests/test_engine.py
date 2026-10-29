"""P3: explicitly FakeAdapter/provider content, real runner/bwrap/git/state.

No result in this module establishes live native model availability.
"""
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

import pytest

from forge import sandbox
from forge.adapters.base import Adapter, Event, Native
from forge.config import load_config
from forge.engine import (WRITERS, Engine, EngineBlocked, NativeRoleDriver, Request, role_tier,
                          request_cancel, satisfy, status, unblock)
from forge.gate import CHECKLIST
from forge.runner import Result, run_call
from forge.state import State, atomic_json, controller_lock

FAKE_PROGRAM = '''#!/usr/bin/python3
import json,sys,uuid
request=json.load(sys.stdin)
sid=sys.argv[1] if len(sys.argv)>1 else str(uuid.uuid4())
print(json.dumps({'sid':sid}),flush=True)
for path,content in request.get('writes',{}).items():
 from pathlib import Path
 p=Path('/work')/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content)
print(json.dumps({'answer':request['answer']}),flush=True)
'''


class FakeAdapter(Adapter):
    """FIXTURE provider protocol, never registered in production adapter_for."""
    def __init__(self, program=FAKE_PROGRAM):
        self.program = program

    def build_argv(self, call, exe, in_dir):
        (in_dir / 'fixture-native.py').write_text(self.program)
        return [exe, '/in/fixture-native.py', *([call.resume_sid] if call.resume_sid else [])], call.prompt_text.encode()

    def parse_line(self, line):
        value = json.loads(line)
        return Event('sid', value) if 'sid' in value else Event('final', value)

    def finish(self, call, rc, events, stdout, stderr):
        sid = next((e.data['sid'] for e in events if e.kind == 'sid'), None)
        answer = next((e.data['answer'] for e in events if e.kind == 'final'), None)
        return Native('success' if rc == 0 and answer is not None else 'failure', sid,
                      '```json\n' + json.dumps(answer) + '\n```', model_match=True)


class FakeDriver(NativeRoleDriver):
    """FIXTURE content hooks driving the actual production scheduler and runner."""
    def __init__(self, config, state, *, batches=None):
        super().__init__(config, state)
        self.binary = state.root.parent / 'fake-native'
        self.binary.write_text(FAKE_PROGRAM)
        self.binary.chmod(0o700)
        self.capabilities = {p.id: {'text', 'readonly', 'resume', 'image'} for p in config.profiles}
        self.invocations = []
        self.outcomes = []
        self.batches = batches if batches is not None else [[proposal('one')], []]
        self.prepared = 0

    def verified(self, p):
        return self.capabilities.get(p.id, set())

    def invoke(self, call, account, validate, cancelled, *, task=None, batch=None, run=None):
        self.invocations.append(call)
        if self.outcomes and call.role != 'probe':
            outcome = self.outcomes.pop(0)
            return fake_result(self.state, call, account, outcome, task, batch, run)
        if call.role == 'probe':
            nonce = call.prompt_text.split('"nonce":"')[1].split('"')[0]
            from dataclasses import replace
            call = replace(call, prompt_text=json.dumps({'answer': {'nonce': nonce}}))
        if call.prompt_text.startswith('Previous turn interrupted.'):
            from dataclasses import replace
            call = replace(call, prompt_text=call.prompt_text.split('\n', 1)[1])
        def spec(args):
            return sandbox.agent_spec(self.roots, harness=call.harness, binary=Path('/usr/bin/python3'), native_args=tuple(args),
                                      work=call.workdir, writable=call.writable,
                                      input_dir=self.state.root / 'calls' / call.id / 'in', binds_rw=(), binds_ro=())
        return run_call(self.state, call, FakeAdapter(self.binary.read_text()), spec, calls_root=self.state.root / 'calls',
                        account=account, validate=validate, should_cancel=cancelled, task=task, batch=batch, run=run)

    def prepare(self, engine, batch):
        result = self.batches[self.prepared]
        self.prepared += 1
        return result

    def request(self, engine, task, role):
        if role == 'review':
            work = engine.gate.review_export(task['id'], engine.candidate(task))
            answer = {'verdict': 'PASS', 'tree': task['candidate_tree'],
                      'checklist': {k: 'pass' for k in CHECKLIST}, 'findings': []}
            writes = {}
        elif role == 'plan':
            work = engine.git.export(task['base_commit'], self.state.root / 'ws' / task['id'] / ('review-' + str(time.time_ns())))
            answer = {'requirements': ['r'], 'design': 'd', 'steps': ['s'], 'tests': ['t']}
            writes = {}
        else:
            work = Path(task['workspace'])
            answer = {'done': True}
            writes = {task['id'] + '.txt': 'implemented', 'docs/affected.md': task['id'],
                      'docs/forge/tasks/' + task['id'] + '.md': '# Plan\np\n# Changes\nc\n# Tests\nt\n# Docs\nd\n# Evidence\ne\n'}
        return Request(json.dumps({'answer': answer, 'writes': writes}), work, role in ('implement', 'red', 'fix', 'escalate'))

    def validate(self, role, difficulty, value):
        return isinstance(value, dict)

    def finalize(self, engine, task):
        pass


def fake_result(state, call, account, outcome, task=None, batch=None, run=None):
    effects = {'coding_budget': outcome in ('timeout', 'unknown_fail', 'turn_limit'),
               'account_wait': outcome in ('quota_wait', 'auth_wait', 'env_wait'),
               'disable': 'profile' if outcome in ('config_block', 'model_mismatch', 'env_block') else None}
    result = Result(call.id, outcome, 0, call.resume_sid or 'fixture-' + call.id, None, True, None, {}, {}, {},
                    str(state.root / 'calls' / call.id), 2000 if effects['account_wait'] else None, 0, effects)
    Path(result.raw_dir).mkdir(parents=True)
    atomic_json(Path(result.raw_dir) / 'result.json', asdict(result))
    with state.transaction() as db:
        db.execute("INSERT INTO calls(id,task,batch,run,role,profile,account,status,outcome,started,session_in,session_out,result_path,raw_dir) VALUES(?,?,?,?,?,?,?,'done',?,?,?,?,?,?)",
                   (call.id, task, batch, run, call.role, call.profile, account, outcome, 1, call.resume_sid,
                    result.sid, str(Path(result.raw_dir) / 'result.json'), result.raw_dir))
    return result


def proposal(ident='one', **values):
    return {'id': ident, 'kind': 'feature', 'difficulty': 'easy', 'req_ids': ['req-' + ident],
            'target_paths': [ident + '.txt'], 'doc_targets': ['docs/affected.md'], **values}


@pytest.fixture
def env(operator_config):
    config = load_config(operator_config('[commands]\ntest=["/usr/bin/true"]\n'))
    repo = config.target_repo
    subprocess.run(['git', 'init', '-q', '-b', 'main', repo], check=True)
    (repo / 'seed').write_text('seed')
    subprocess.run(['git', '-C', repo, 'add', '.'], check=True)
    subprocess.run(['git', '-C', repo, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@local', 'commit', '-qm', 'base'], check=True)
    with State(config.state_dir) as state:
        driver = FakeDriver(config, state)
        engine = Engine(config, state, driver, clock=lambda: 1000)
        with state.transaction() as db:
            db.execute("INSERT INTO batches(id,n,state) VALUES('b',1,'TASKS')")
        yield engine, driver, state


def add(engine, p=None):
    engine.add_tasks('b', [p or proposal()], 'fp')
    return engine.task((p or {}).get('id', 'one'))


@pytest.mark.parametrize('difficulty,role,tier', [
    ('easy', 'plan', 'normal'), ('easy', 'implement', 'cheap'), ('easy', 'review', 'normal'),
    ('hard', 'plan', 'strong'), ('hard', 'implement', 'normal'), ('hard', 'review', 'strong'),
    *[('easy', r, 'strong') for r in ('po', 'po_read', 'architect', 'adr_review', 'escalate')]])
def test_role_tier_table(difficulty, role, tier):
    assert role_tier(role, difficulty) == tier


def test_exactly_easy_hard():
    with pytest.raises(ValueError):
        role_tier('plan', 'medium')


def test_easy_hard_routes_and_resume_capability(env):
    engine, driver, state = env
    add(engine)
    assert engine.route('implement', 'one')[0][0].id == 'luna-xhigh'
    driver.capabilities['luna-xhigh'].remove('resume')
    assert engine.route('implement', 'one')[0][0].id == 'haiku-high'
    engine.update('one', difficulty='hard')
    assert engine.route('implement', 'one')[0][0].id == 'sol-high'
    assert engine.route('plan', 'one')[0][0].id == 'sol-high'


@pytest.mark.parametrize('case', ['all_disabled', 'image', 'resume', 'family', 'double_family'])
def test_empty_structural_route_blocks_not_waits(env, case):
    engine, driver, state = env
    add(engine)
    role = 'implement'
    images = ()
    if case == 'all_disabled':
        driver.capabilities = {}
    elif case == 'image':
        for caps in driver.capabilities.values():
            caps.discard('image')
        images = (Path('fixture.png'),)
        role = 'po_read'
    elif case == 'resume':
        for caps in driver.capabilities.values():
            caps.discard('resume')
    else:
        role = 'review'
        driver.capabilities = {'sol-high': {'text', 'readonly'}, 'opus-high': {'text', 'readonly'}} if case == 'double_family' else {'sol-high': {'text', 'readonly'}}
        engine.metadata('one', writer_families=['openai', 'anthropic'] if case == 'double_family' else ['openai'])
        if case == 'double_family':
            engine.update('one', difficulty='hard', escalated=1)
    with pytest.raises(EngineBlocked, match='empty structural'):
        engine.admission(role, Request('fixture', Path('.'), images=images), 'one')
    row = engine.task('one')
    assert row['state'] == 'BLOCKED' and row['recheck_at'] is None
    assert set(json.loads(row['blocked'])) >= {'cause', 'outcome', 'last_calls', 'evidence_paths', 'preserved', 'unblock_condition', 'role', 'excluded_profiles'}
    assert not driver.invocations


def test_account_wait_shared_no_downgrade_and_family_timer(env):
    engine, driver, state = env
    add(engine)
    with state.transaction() as db:
        db.execute("UPDATE accounts SET state='waiting',recheck_at=1234")
    assert engine.admission('plan', Request('fixture', Path('.')), 'one') is None
    assert engine.task('one')['recheck_at'] == 1234
    engine.metadata('one', writer_families=['openai'])
    assert engine.admission('review', Request('fixture', Path('.')), 'one') is None
    assert json.loads(engine.task('one')['waiting_accounts']) == ['claude', 'grok']
    assert not driver.invocations


def test_unknown_account_probe_and_expiry(env):
    engine, driver, state = env
    add(engine)
    engine.step('one')
    request = driver.request(engine, engine.task('one'), 'plan')
    assert engine.admission('plan', request, 'one').id == 'sol-high'
    assert [c.role for c in driver.invocations] == ['probe']
    with state.transaction() as db:
        db.execute("UPDATE accounts SET state='waiting',recheck_at=999 WHERE id='codex'")
    assert engine.admission('plan', request, 'one').id == 'sol-high'
    assert [c.role for c in driver.invocations] == ['probe', 'probe']


def test_fix_exact_sid_lost_session_no_fresh(env):
    engine, driver, state = env
    add(engine)
    engine.update('one', state='FIXING', session_id='exact', session_profile='haiku-high')
    routes, reasons = engine.route('fix', 'one')
    assert [p.id for p in routes] == ['haiku-high']
    call = engine.make_call('fix', routes[0], Request('{}', Path('.'), True), 'one')
    assert call.resume_sid == 'exact' and call.preset_sid is None
    engine.update('one', session_id=None)
    assert engine.route('fix', 'one')[0] == []
    assert json.loads(engine.task('one')['blocked'])['outcome'] == 'session_lost'
    with pytest.raises(EngineBlocked, match='fresh-session'):
        unblock(engine, 'one', 'retry')


def test_budget_escalates_once_handoff_and_review_exclusions(env):
    engine, driver, state = env
    add(engine)
    engine.step('one')
    engine.update('one', session_id='original', session_profile='luna-xhigh')
    engine.metadata('one', writer_families=['openai'])
    engine.spend('one', 'fix_rounds')
    engine.spend('one', 'fix_rounds')
    row = engine.task('one')
    assert row['state'] == 'ESCALATING' and row['escalated'] == 1 and row['session_id'] is None
    handoff = state.db.execute('SELECT * FROM handoffs').fetchone()
    assert handoff['old_sid'] == 'original' and Path(handoff['artifact_path']).is_file()
    p = engine.route('escalate', 'one')[0][0]
    assert engine.make_call('escalate', p, Request('{}', Path(row['workspace']), True), 'one').resume_sid is None
    engine.spend('one', 'fix_rounds'); engine.spend('one', 'fix_rounds')
    assert engine.task('one')['state'] == 'BLOCKED'
    assert state.db.execute('SELECT count(*) FROM handoffs').fetchone()[0] == 1


@pytest.mark.parametrize('role', ['po_read', 'po', 'architect', 'adr_review'])
def test_non_task_role_attempts_bounded(env, role):
    engine, driver, state = env
    work = engine.git.export(engine.git.head(), state.root / 'ws' / 'batch' / 'review-1')
    driver.outcomes = ['timeout', 'timeout']
    for i in range(2):
        assert engine.role(role, Request('{}', work), batch='b') is None
    row = state.db.execute("SELECT * FROM batches WHERE id='b'").fetchone()
    assert row['state'] == 'BLOCKED'
    assert json.loads(row['role_attempts'])[role] == 2


def test_wait_never_spends_budget(env):
    engine, driver, state = env
    add(engine); engine.step('one')
    driver.outcomes = ['quota_wait']
    assert engine.role('plan', driver.request(engine, engine.task('one'), 'plan'), task='one', batch='b') is None
    row = engine.task('one')
    assert row['plan_attempts'] == row['attempts'] == 0
    assert state.db.execute("SELECT state FROM accounts WHERE id='codex'").fetchone()[0] == 'waiting'


def test_full_task_real_runner_gate_commit_fresh_sessions(env):
    engine, driver, state = env
    engine.add_tasks('b', [proposal('one'), proposal('two')], 'fp')
    with controller_lock(state.root):
        assert engine.drain('b', until_idle=True) == 0
    assert [engine.task(i)['state'] for i in ('one', 'two')] == ['COMMITTED', 'COMMITTED']
    assert engine.task('one')['session_id'] != engine.task('two')['session_id']
    assert state.db.execute('SELECT count(*) FROM commits').fetchone()[0] == 2
    for ident in ('one', 'two'):
        row = engine.task(ident)
        assert engine.git.tree(row['candidate_commit']) == row['candidate_tree']
        review = json.loads(Path(json.loads(row['metadata'])['review_receipt']['path']).read_text())
        assert review['reviewer_family'] not in review['implementer_families']


def test_blocked_dependent_skipped_independent_continues(env):
    engine, driver, state = env
    engine.add_tasks('b', [proposal('bad'), proposal('dep', depends_on=['bad']), proposal('independent')], 'fp')
    engine.block('bad', 'fixture route block')
    with controller_lock(state.root):
        assert engine.drain('b', until_idle=True) == 2
    assert engine.task('dep')['state'] == 'DEP_WAIT'
    assert engine.task('independent')['state'] == 'COMMITTED'


def test_duplicate_blocked_suppressed(env):
    engine, driver, state = env
    add(engine)
    engine.block('one', 'fixture')
    with state.transaction() as db:
        db.execute("INSERT INTO batches(id,n,state) VALUES('b2',2,'TASKS')")
    engine.add_tasks('b2', [proposal('dup', req_ids=['req-one'], target_paths=['one.txt'])], 'fp')
    assert engine.task('dup')['state'] == 'SUPPRESSED_DUP'


def test_input_fp_actual_test_blob_and_evidence_changes(env):
    engine, driver, state = env
    before = engine.input_fingerprint()
    work = engine.git.export(engine.git.head(), state.root / 'ws' / 'fingerprint' / 'src')
    (work / 'tests').mkdir(); (work / 'tests/test_same.py').write_text('def test_same(): assert True\n')
    c = engine.git.candidate('fp', work, engine.git.head()); engine.git.cas(c)
    first = engine.input_fingerprint(); assert first != before
    (work / 'tests/test_same.py').write_text('def test_same(): assert False\n')
    c = engine.git.candidate('fp', work, engine.git.head()); engine.git.cas(c)
    assert engine.input_fingerprint() != first


def test_stall_same_input_and_change_clears_without_provider(env):
    engine, driver, state = env
    fp = engine.input_fingerprint()
    with state.transaction() as db:
        db.execute("DELETE FROM batches")
        for i in range(2):
            db.execute("INSERT INTO batches(id,n,state,input_fp) VALUES(?,?,'DONE',?)", (str(i), i, fp))
    assert engine.run(until_idle=True) == 3
    assert driver.prepared == 0 and not driver.invocations
    # Operator note changes the input independently of any model verdict.
    with state.transaction() as db:
        db.execute("INSERT INTO unblocks(task,at,note) SELECT id,1,'changed' FROM tasks")
        db.execute("UPDATE batches SET input_fp='old'")
    driver.batches = [[]]
    assert engine.run(until_idle=True) == 0
    assert driver.prepared == 1
    assert state.db.execute('SELECT satisfied_at FROM owner').fetchone()[0] is None


def test_continuous_default_seven_batches_and_explicit_bound(env):
    engine, driver, state = env
    # Use actual accepted tasks: seven consecutive batches, then zero justified work.
    with state.transaction() as db:
        db.execute('DELETE FROM batches')
    driver.batches = [[proposal('task' + str(i))] for i in range(7)] + [[]]
    assert engine.run(until_idle=True) == 0
    assert driver.prepared == 8
    assert state.db.execute('SELECT count(*) FROM commits').fetchone()[0] == 7
    assert state.db.execute('SELECT satisfied_at FROM owner').fetchone()[0] is None


def test_max_batches_and_operator_only_satisfied(env):
    engine, driver, state = env
    with state.transaction() as db:
        db.execute('DELETE FROM batches')
    assert engine.run(max_batches=1, until_idle=True) == 0
    assert driver.prepared == 1
    satisfy(state, 'operator verified')
    assert engine.run() == 0
    assert status(state)['runs'][-1]['state'] == 'OWNER_SATISFIED'


def test_native_default_never_fake_and_cli_commands(env, capsys):
    engine, driver, state = env
    from forge.cli import main
    assert isinstance(Engine(engine.config, state).driver, NativeRoleDriver)
    assert main(['status', '--config', str(engine.config.path), '--json']) == 0
    assert 'accounts' in capsys.readouterr().out
    assert main(['satisfied', '--config', str(engine.config.path), '--note', 'owner']) == 0
    assert state.db.execute('SELECT note FROM owner').fetchone()[0] == 'owner'
    assert main(['cancel', '--config', str(engine.config.path), '--task', 'missing']) == 0
    assert state.db.execute('SELECT target FROM cancels').fetchone()[0] == 'missing'


def test_real_bug_red_before_fix_and_same_session(env):
    engine, driver, state = env
    from forge.gate import Gate
    from test_gate import COMMANDS
    engine.gate = Gate(engine.git, state, driver.roots, COMMANDS)
    engine.integrator.gate = engine.gate
    base = engine.git.export(engine.git.head(), state.root / 'ws' / 'seedbug' / 'src')
    (base / 'app.py').write_text('def value(): return 0\n')
    (base / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    c = engine.git.candidate('seedbug', base, engine.git.head()); engine.git.cas(c)
    original = driver.request
    def request(e, task, role):
        if role == 'red':
            return Request(json.dumps({'answer': {'nodes': ['tests/test_value.py::test_value']},
                'writes': {'tests/test_value.py': 'from app import value\ndef test_value(): assert value() == 1\n'}}), Path(task['workspace']), True)
        req = original(e, task, role)
        if role in ('implement', 'fix', 'escalate'):
            receipts = state.db.execute("SELECT * FROM artifacts WHERE task=? AND kind='red'", (task['id'],)).fetchall()
            assert receipts, 'actual fix attempted before durable RED evidence'
            v = json.loads(req.prompt); v['writes']['app.py'] = 'def value(): return 1\n'
            return Request(json.dumps(v), req.work, True)
        return req
    driver.request = request
    add(engine, proposal(kind='bug'))
    with controller_lock(state.root):
        assert engine.drain('b', until_idle=True) == 0
    red_call, fix_call = [c for c in driver.invocations if c.role in ('red', 'implement')]
    assert red_call.resume_sid is None
    assert fix_call.resume_sid == engine.task('one')['session_id']
    assert engine.task('one')['red_commit'] and engine.task('one')['state'] == 'COMMITTED'


def test_review_changes_resumes_writer_exact_sid(env):
    engine, driver, state = env
    original = driver.request
    reviews = 0
    def request(e, task, role):
        nonlocal reviews
        req = original(e, task, role)
        if role == 'review':
            reviews += 1
            if reviews == 1:
                value = json.loads(req.prompt)
                value['answer'].update(verdict='CHANGES', findings=['fixture finding'])
                return Request(json.dumps(value), req.work)
        return req
    driver.request = request
    add(engine)
    with controller_lock(state.root):
        assert engine.drain('b', until_idle=True) == 0
    writer = next(c for c in driver.invocations if c.role == 'implement')
    fixes = [c for c in driver.invocations if c.role == 'fix']
    assert len(fixes) == 1 and fixes[0].resume_sid == engine.task('one')['session_id']
    assert writer.resume_sid is None and reviews == 2


def test_failure_profile_strikes_shared_across_tasks_and_effect_replay(env):
    engine, driver, state = env
    engine.add_tasks('b', [proposal('one'), proposal('two')], 'fp')
    p = engine.config.profiles[2]
    for ident in ('one', 'two'):
        call = engine.make_call('plan', p, Request('{}', Path('.')), ident)
        r = fake_result(state, call, p.account, 'unknown_fail', ident, 'b')
        engine._account_effect(p, r)
        engine._account_effect(p, r)
    assert state.db.execute('SELECT unknown_fail FROM profile_counters WHERE profile=?', (p.id,)).fetchone()[0] == 2
    assert state.db.execute('SELECT outcome FROM profiles_disabled WHERE profile=?', (p.id,)).fetchone()[0] == 'unknown_fail'
    assert state.db.execute('SELECT count(*) FROM call_effects').fetchone()[0] == 2


def test_linked_evidence_changes_fp_unlinked_failures_do_not(env):
    engine, driver, state = env
    before = engine.input_fingerprint()
    work = engine.git.export(engine.git.head(), state.root / 'ws' / 'fp' / 'src')
    evidence = state.root / 'evidence/result.json'; evidence.parent.mkdir(); evidence.write_text('one')
    with state.transaction() as db:
        db.execute("INSERT INTO artifacts(path,sha256,kind,created) VALUES(?,'ignored','fixture',1)", (str(evidence),))
    assert engine.input_fingerprint() == before
    (work / 'docs/forge/implemented').mkdir(parents=True)
    (work / 'docs/forge/implemented/req.md').write_text('[result](' + str(evidence) + ')')
    c = engine.git.candidate('fp', work, engine.git.head()); engine.git.cas(c)
    linked = engine.input_fingerprint()
    evidence.write_text('two')
    assert engine.input_fingerprint() != linked


def test_wait_until_idle_no_llm_polling_and_timer_sleep(env):
    engine, driver, state = env
    add(engine); engine.step('one')
    with state.transaction() as db:
        db.execute("UPDATE accounts SET state='waiting',recheck_at=1200")
    assert engine.drain('b', until_idle=True) == 75
    assert not driver.invocations
    sleeps = []
    def wake(seconds):
        sleeps.append(seconds)
        with state.transaction() as db:
            db.execute("UPDATE accounts SET state='available',last_ok=1200")
        engine.clock = lambda: 1200
    engine.sleep = wake
    assert engine.drain('b') == 0
    assert sleeps == [200]


def test_schema_v1_migrates_without_losing_tasks(tmp_path):
    import sqlite3
    path = tmp_path / 'migration'
    with State(path) as state:
        state.db.execute("INSERT INTO batches(id,n,state) VALUES('preserved',1,'DONE')")
    db = sqlite3.connect(path / 'forge.db')
    db.execute('DROP TABLE call_effects'); db.execute('DROP TABLE profile_counters')
    db.execute('PRAGMA user_version=1'); db.commit(); db.close()
    with State(path) as state:
        assert state.db.execute('PRAGMA user_version').fetchone()[0] == 2
        assert state.db.execute('SELECT id FROM batches').fetchone()[0] == 'preserved'
        assert state.db.execute('SELECT count(*) FROM call_effects').fetchone()[0] == 0


def test_actual_escalation_fresh_handoff_then_third_family_review(env):
    engine, driver, state = env
    # Sol can plan/review, but only Opus has strong resume capability here.
    driver.capabilities['sol-high'].discard('resume')
    original = driver.request
    reviews = 0
    def request(e, task, role):
        nonlocal reviews
        req = original(e, task, role)
        if role == 'review':
            reviews += 1
            if reviews <= 2:
                value = json.loads(req.prompt)
                value['answer'].update(verdict='CHANGES', findings=['fixture finding'])
                return Request(json.dumps(value), req.work)
        return req
    driver.request = request
    add(engine)
    with controller_lock(state.root):
        assert engine.drain('b', until_idle=True) == 0
    calls = [c for c in driver.invocations if c.role in ('implement', 'fix', 'escalate', 'review')]
    escalation = next(c for c in calls if c.role == 'escalate')
    assert escalation.profile == 'opus-high' and escalation.resume_sid is None
    final_review = [c for c in calls if c.role == 'review'][-1]
    assert final_review.profile == 'grok-high'
    assert engine.families('one') == {'openai', 'anthropic'}
    handoff = state.db.execute('SELECT * FROM handoffs').fetchone()
    assert handoff['new_sid'] == engine.task('one')['session_id']
    assert handoff['old_sid'] != handoff['new_sid']


def test_batch_preparation_retries_role_to_finite_budget(env):
    engine, driver, state = env
    with state.transaction() as db:
        db.execute('DELETE FROM batches')
    work = state.root / 'ws' / 'batch' / 'review-1'
    driver.outcomes = ['timeout', 'timeout']
    def prepare(e, batch):
        if not work.exists():
            e.git.export(e.git.head(), work)
        assert e.role('po', Request('{}', work), batch=batch) is None
        return None
    driver.prepare = prepare
    assert engine.run(until_idle=True) == 2
    assert len([c for c in driver.invocations if c.role == 'po']) == 2
    assert state.db.execute('SELECT state FROM batches').fetchone()[0] == 'BLOCKED'


def test_pixel_only_linked_source_change_clears_fingerprint(env):
    engine, driver, state = env
    work = engine.git.export(engine.git.head(), state.root / 'ws' / 'source' / 'src')
    (work / 'docs/source').mkdir(parents=True)
    (work / 'docs/source/spec.md').write_text('![layout](layout.png)')
    image = work / 'docs/source/layout.png'; image.write_bytes(b'FIXTURE pixels one')
    c = engine.git.candidate('source', work, engine.git.head()); engine.git.cas(c)
    before = engine.input_fingerprint()
    image.write_bytes(b'FIXTURE pixels two')
    c = engine.git.candidate('source', work, engine.git.head()); engine.git.cas(c)
    assert engine.input_fingerprint() != before


# Structural reviewer holds recheck 900s after the fixture clock (1000).
# Account-capacity holds use that account's own recheck_at.
_REVIEWER_STRUCTURAL_RECHECK = 1900
_REVIEWER_AUTHOR = {'implement': 'luna-xhigh', 'red': 'luna-xhigh', 'fix': 'luna-xhigh', 'escalate': 'sol-high'}


def _reviewer_ready(state):
    with state.transaction() as db:
        db.execute("UPDATE accounts SET state='available',last_ok=1000,recheck_at=NULL")


def _reviewer_disable(state, profiles):
    with state.transaction() as db:
        for profile in profiles:
            db.execute('INSERT OR REPLACE INTO profiles_disabled VALUES(?,NULL,?,?,?)',
                       (profile, 'fixture', 'config_block', 1))


def _reviewer_arm(engine, role):
    if role == 'fix':
        engine.update('one', state='FIXING', session_id='fixture-sid', session_profile='luna-xhigh')
    elif role == 'red':
        engine.update('one', state='RED_WRITING')
    elif role == 'escalate':
        engine.update('one', state='ESCALATING', escalated=1)
    else:
        engine.update('one', state='IMPLEMENTING')


def _reviewer_close(engine, driver, state, role, case):
    add(engine)
    _reviewer_ready(state)
    _reviewer_arm(engine, role)
    if case == 'none':
        for caps in driver.capabilities.values():
            caps.discard('readonly')
    elif case == 'same_family':
        for profile in ('haiku-high', 'sonnet-high', 'opus-high', 'grok-high'):
            driver.capabilities[profile].discard('readonly')
    elif case == 'disabled':
        _reviewer_disable(state, ('haiku-high', 'sonnet-high', 'opus-high', 'grok-high'))
    elif case == 'waiting':
        _reviewer_disable(state, ('sonnet-high',))
        with state.transaction() as db:
            db.execute("UPDATE accounts SET state='waiting',recheck_at=4321,outcome='env_wait',reason=? WHERE id='grok'",
                       ('selected model is at capacity',))
    elif case == 'exhausted':
        with state.transaction() as db:
            for profile in ('sonnet-high', 'grok-high'):
                db.execute('INSERT INTO profile_counters(profile,run,protocol,unknown_fail) VALUES(?,?,0,2)', (profile, ''))
    before = engine.task('one')
    result = engine.admission(role, Request('fixture', Path('.')), 'one')
    row = engine.task('one')
    assert result is None
    assert row['state'] == 'WAITING' and row['blocked'] is None and row['waiting_role'] == role
    assert row['attempts'] == row['impl_attempts'] == row['fix_rounds'] == row['plan_attempts'] == 0
    assert row['coding_seconds'] == before['coding_seconds'] == 0
    assert not driver.invocations
    assert state.db.execute('SELECT count(*) FROM calls').fetchone()[0] == 0
    info = json.loads(row['metadata'])['reviewer_admission']
    assert info['reason'] == 'no independent reviewer'
    assert info['author_profile'] == _REVIEWER_AUTHOR[role]
    assert info['author_family'] == 'openai'
    detail = {'same_family': 'same_family', 'waiting': 'waiting', 'exhausted': 'exhausted',
              'disabled': 'disabled', 'none': 'none'}[case]
    assert info['detail'] == detail
    if case == 'waiting':
        assert row['recheck_at'] == 4321 and json.loads(row['waiting_accounts']) == ['grok']
        assert info['waiting_accounts'] == ['grok']
        account = state.db.execute("SELECT outcome,reason FROM accounts WHERE id='grok'").fetchone()
        assert account['outcome'] == 'env_wait' and 'capacity' in account['reason']
    else:
        assert row['recheck_at'] == _REVIEWER_STRUCTURAL_RECHECK
        assert json.loads(row['waiting_accounts']) == []
    return info


@pytest.mark.parametrize('case', ('none', 'same_family', 'disabled', 'waiting', 'exhausted'))
@pytest.mark.parametrize('role', sorted(WRITERS))
def test_prewrite_reviewer_closed(env, case, role):
    engine, driver, state = env
    info = _reviewer_close(engine, driver, state, role, case)
    if case == 'exhausted':
        assert info['rejected']['sonnet-high'] == 'exhausted' and info['rejected']['grok-high'] == 'exhausted'
    if case == 'disabled':
        assert info['rejected']['grok-high'] == 'disabled profile/account'


def test_role_waits_for_grok_capacity_without_author_or_budget(env):
    engine, driver, state = env
    add(engine)
    engine.step('one')
    _reviewer_ready(state)
    _reviewer_disable(state, ('sonnet-high',))
    with state.transaction() as db:
        db.execute("UPDATE accounts SET state='waiting',recheck_at=4321,outcome='env_wait',reason=? WHERE id='grok'",
                   ('selected model is at capacity',))
    engine.update('one', state='IMPLEMENTING')
    result = engine.role('implement', driver.request(engine, engine.task('one'), 'implement'), task='one', batch='b')
    row = engine.task('one')
    assert result is None and row['state'] == 'WAITING' and row['recheck_at'] == 4321
    assert json.loads(row['waiting_accounts']) == ['grok']
    assert row['attempts'] == row['impl_attempts'] == row['fix_rounds'] == 0
    assert not any(call.role in WRITERS or call.role == 'probe' for call in driver.invocations)


def test_escalation_admission_uses_new_author_family(env):
    engine, driver, state = env
    add(engine)
    _reviewer_ready(state)
    engine.update('one', difficulty='hard', state='ESCALATING', escalated=1)
    engine.metadata('one', writer_families=['xai'])
    driver.capabilities['opus-high'].discard('readonly')
    driver.capabilities['opus-high'].discard('resume')
    assert engine.admission('escalate', Request('fixture', Path('.')), 'one') is None
    row = engine.task('one')
    info = json.loads(row['metadata'])['reviewer_admission']
    assert row['state'] == 'WAITING'
    assert info['author_family'] == 'openai' and info['author_profile'] == 'sol-high'
    assert info['rejected']['sol-high'].startswith('same-family')
    assert info['rejected']['grok-high'] == 'tier strong'
    assert row['attempts'] == row['fix_rounds'] == 0 and not driver.invocations


def test_escalation_allows_other_family_while_grok_waits(env):
    engine, driver, state = env
    add(engine)
    _reviewer_ready(state)
    engine.update('one', difficulty='easy', state='ESCALATING', escalated=1)
    engine.metadata('one', writer_families=['xai'])
    with state.transaction() as db:
        db.execute("UPDATE accounts SET state='waiting',recheck_at=4321,outcome='env_wait',reason='capacity' WHERE id='grok'")
    chosen = engine.admission('escalate', Request('fixture', Path('.')), 'one')
    assert chosen.id == 'sol-high' and engine.task('one')['state'] == 'ESCALATING'
    assert not driver.invocations


def test_grok_is_not_a_strong_reviewer(env):
    engine, driver, state = env
    add(engine)
    _reviewer_ready(state)
    engine.update('one', difficulty='hard', state='IMPLEMENTING')
    driver.capabilities['opus-high'].discard('readonly')
    grok = next(profile for profile in engine.config.profiles if profile.id == 'grok-high')
    assert grok.tiers == ('normal',) and 'strong' not in grok.tiers
    assert engine.admission('implement', Request('fixture', Path('.')), 'one') is None
    row = engine.task('one')
    info = json.loads(row['metadata'])['reviewer_admission']
    assert row['state'] == 'WAITING' and info['rejected']['grok-high'] == 'tier strong'
    assert 'grok-high' not in [profile.id for profile in engine.route('review', 'one')[0]]
    assert row['impl_attempts'] == row['attempts'] == 0 and not driver.invocations


def test_compliant_reviewer_allows_write(env):
    engine, driver, state = env
    add(engine)
    engine.step('one')
    _reviewer_ready(state)
    engine.update('one', state='IMPLEMENTING')
    assert engine.admission('implement', Request('fixture', Path(engine.task('one')['workspace'])), 'one').id == 'luna-xhigh'
    reviewers = {profile.id for profile in engine.route('review', 'one')[0]}
    assert reviewers & {'sonnet-high', 'grok-high'}
    result = engine.role('implement', driver.request(engine, engine.task('one'), 'implement'), task='one', batch='b')
    assert result is not None and result.outcome == 'ok'
    assert [call.role for call in driver.invocations if call.role in WRITERS] == ['implement']
    assert engine.task('one')['state'] != 'WAITING'


def _assert_no_writer_launch(engine, driver, role, work, recheck, accounts):
    row = engine.task('one')
    info = json.loads(row['metadata'])['reviewer_admission']
    assert row['state'] == 'WAITING' and row['blocked'] is None and row['waiting_role'] == role
    assert row['recheck_at'] == recheck and json.loads(row['waiting_accounts']) == accounts
    assert info['reason'] == 'no independent reviewer' and info['recheck_at'] == recheck
    assert info['waiting_accounts'] == accounts
    assert row['attempts'] == row['impl_attempts'] == row['fix_rounds'] == row['plan_attempts'] == 0
    assert row['coding_seconds'] == 0
    assert not any(call.role in WRITERS for call in driver.invocations)
    assert not (work / 'one.txt').exists()
    return info


@pytest.mark.parametrize('role', ('implement', 'red', 'escalate'))
def test_probe_fallback_rechecks_actual_author_family(env, monkeypatch, role):
    """A failed probe must not launch the next author until that family has a reviewer."""
    engine, driver, state = env
    add(engine)
    engine.step('one')
    _reviewer_ready(state)
    engine.update('one', difficulty='hard')
    _reviewer_arm(engine, role)
    with state.transaction() as db:
        db.execute("UPDATE accounts SET state='unknown',last_ok=NULL WHERE id='codex'")
        db.execute("UPDATE accounts SET state='waiting',recheck_at=4321,outcome='env_wait',reason='capacity' WHERE id='grok'")
    original = driver.invoke

    def invoke(call, account, validate, cancelled, **kwargs):
        if call.role == 'probe' and account == 'codex':
            driver.invocations.append(call)
            return fake_result(state, call, account, 'env_wait', **kwargs)
        return original(call, account, validate, cancelled, **kwargs)

    monkeypatch.setattr(driver, 'invoke', invoke)
    request = driver.request(engine, engine.task('one'), role)
    result = engine.role(role, request, task='one', batch='b')
    assert result is None
    info = _assert_no_writer_launch(engine, driver, role, request.work, 2000, ['codex'])
    assert info['author_family'] == 'anthropic'
    assert info['author_profile'] == ('opus-high' if role == 'escalate' else 'sonnet-high')
    assert info['rejected']['grok-high'] == 'tier strong'
    grok = next(profile for profile in engine.config.profiles if profile.id == 'grok-high')
    assert grok.tiers == ('normal',)


@pytest.mark.parametrize('role', ('implement', 'red', 'fix', 'escalate'))
def test_unlaunched_durable_operation_rechecks_reviewer(env, monkeypatch, role):
    """A saved operation that never launched must be admitted again after reviewers wait."""
    import forge.engine as engine_module
    engine, driver, state = env
    add(engine)
    engine.step('one')
    _reviewer_ready(state)
    _reviewer_arm(engine, role)
    request = driver.request(engine, engine.task('one'), role)

    class SimulatedCrash(Exception):
        pass

    def crash(point):
        if point == 'engine_before_launch':
            raise SimulatedCrash()

    with monkeypatch.context() as patch:
        patch.setattr(engine_module, 'crash', crash)
        with pytest.raises(SimulatedCrash):
            engine.role(role, request, task='one', batch='b')
    operation = json.loads(engine.task('one')['metadata'])['operations'][role]
    assert state.db.execute('SELECT 1 FROM calls WHERE id=?', (operation['call'],)).fetchone() is None
    with state.transaction() as db:
        db.execute("UPDATE accounts SET state='waiting',recheck_at=4321,outcome='env_wait',reason='capacity' WHERE id IN ('claude','grok')")
    engine.recover()
    result = engine.role(role, request, task='one', batch='b')
    assert result is None
    info = _assert_no_writer_launch(engine, driver, role, request.work, 4321, ['claude', 'grok'])
    assert info['detail'] == 'waiting'
    assert info['author_family'] == 'openai'
