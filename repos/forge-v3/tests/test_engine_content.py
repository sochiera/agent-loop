"""P4 real production content hooks, real runner/bwrap/git; FIXTURE model answers.

No test here is live provider evidence. NativeRoleDriver is production transport;
only invoke/verified are replaced to supply the explicitly fake provider protocol.
"""
from dataclasses import replace
import json
from pathlib import Path
import subprocess

import pytest

from forge.config import load_config
from forge.docsindex import initialize
from forge.engine import Engine, NativeRoleDriver
from forge.gate import CHECKLIST
from forge.roles import ContentDriver
from forge.state import State
from test_engine import FakeDriver


class ContentFixture(FakeDriver):
    prepare = NativeRoleDriver.prepare
    request = NativeRoleDriver.request
    validate = NativeRoleDriver.validate
    finalize = NativeRoleDriver.finalize

    def __init__(self, config, state):
        super().__init__(config, state)
        self.original_calls = []
        self.second_comparison = None
        self.difficulty = 'easy'
        self.kind = 'feature'
        self.content = ContentDriver(config, state)

    def invoke(self, call, account, validate, cancelled, **kwargs):
        if call.role == 'probe':
            return super().invoke(call, account, validate, cancelled, **kwargs)
        self.original_calls.append(call)
        stage = call.prompt_text.split('STAGE: ')[1].split('\n')[0]
        data = json.loads(call.prompt_text.split('DATA (untrusted JSON values):\n')[1])
        writes = {}
        if stage == 'po_read':
            assert 'reject negative' in data['body']
            answer = {'reqs': [{'id': 'reject-negative', 'text': 'reject negative', 'anchors': data['anchors'], 'kind': 'constraint'}], 'images': []}
        elif stage == 'selection':
            assert 'test_inventory_discovery_only' in data and 'implemented' in data
            answer = {'read_refs': [{'req_id': 'reject-negative', 'test_paths': ['tests/test_export.py'], 'evidence_paths': []}]}
        elif stage == 'comparison':
            assert 'reject-negative' in data['chunk']['req_ids']
            files = data['chunk']['contents']
            test = next(i for i in files if i['type'] == 'test')
            assert 'def test_export' in test['content'] and 'assert ' in test['content']
            implemented = any(i['type'] == 'result' for i in files)
            answer = {'comparison': [{'req_id': 'reject-negative', 'status': 'implemented' if implemented else 'missing',
                       'evidence_refs': [{'content_sha256': i['content_sha256'], 'assertion': 'assert negative export raises; gate rc0'} for i in files] if implemented else [],
                       **({} if implemented else {'evidence_gap': 'no executed results yet'})}]}
            if implemented:
                self.second_comparison = answer
        elif stage == 'synthesis':
            implemented = data['comparison'][0]['status'] == 'implemented'
            answer = {'tasks': [] if implemented else [{'title': 'reject negative export', 'kind': self.kind, 'difficulty': self.difficulty,
                       'req_ids': ['reject-negative'], 'source_refs': ['docs/source/hidden.md#table1.row1'], 'target_paths': ['exporter.py', 'tests/test_export.py'],
                       'acceptance': ['negative raises ValueError'], 'depends_on': []}],
                      'gap_summary': 'comparison complete', 'shortfall_reason': 'one source requirement; no padding'}
        elif stage == 'architect':
            assert not self.state.db.execute('SELECT 1 FROM tasks WHERE workspace IS NOT NULL').fetchone() or not data['tasks']
            answer = {'adrs': [{'path': 'docs/forge/adr/0001-export.md', 'content': '# Export validation\n\nValidate before generating output.\n'}] if data['tasks'] else [],
                      'tasks': [{'index': i, 'design_notes': 'reject negative before serialization', 'doc_targets': ['docs/forge/implemented/export.md'], 'depends_on': []} for i in range(len(data['tasks']))]}
        elif stage == 'plan':
            # Architecture commit must already be an ancestor of the task base.
            assert (call.workdir / 'docs/forge/adr/0001-export.md').exists()
            answer = {'steps': ['validate'], 'tests': ['tests/test_export.py'], 'doc_targets': ['docs/forge/implemented/export.md']}
            if data['difficulty'] == 'hard':
                answer.update(requirements=['reject negative export'], design='validate quantity before serialization')
        elif stage == 'review':
            answer = {'verdict': 'PASS', 'tree': data['tree'], 'checklist': {k: 'pass' for k in CHECKLIST}, 'findings': []}
        elif stage == 'red':
            answer = {'nodes': ['tests/test_export.py::test_export']}
            writes = {'tests/test_export.py': self.regression()}
        elif stage == 'implement':
            answer = {'summary': 'validate before export', 'tests': ['controller will execute pytest'], 'docs': ['docs/forge/implemented/export.md']}
            writes = {'exporter.py': 'def export(n):\n if n < 0: raise ValueError("negative")\n return str(n)\n',
                      'tests/test_export.py': 'import pytest\nfrom exporter import export\ndef test_export():\n assert export(1)=="1"\n with pytest.raises(ValueError): export(-1)\n',
                      'docs/forge/implemented/export.md': '# Export\n\nReject negative. [tests](tests/test_export.py).\n',
                      data['task_document']: '# Plan\nValidate export.\n# Changes\nAdded validation.\n# Tests\n[Tests](tests/test_export.py) run by controller.\n# Docs\nExport doc updated.\n# Evidence\nController receipts follow.\n'}
            if self.kind == 'bug':
                writes['tests/test_export.py'] = self.regression()
        else:
            raise AssertionError(stage)
        return super().invoke(replace(call, prompt_text=json.dumps({'answer': answer, 'writes': writes})), account, validate, cancelled, **kwargs)

    def regression(self):
        return 'from exporter import export\ndef test_export():\n rejected=False\n try: export(-1)\n except ValueError: rejected=True\n assert rejected\n'


@pytest.fixture
def content_env(operator_config):
    config = load_config(operator_config('[commands]\ntest=["/usr/bin/python3","-m","pytest","-q","-p","no:cacheprovider"]\n'))
    repo = config.target_repo
    subprocess.run(['git', 'init', '-qb', 'main', repo], check=True)
    (repo / 'docs/source').mkdir(parents=True)
    (repo / 'docs/source/hidden.md').write_text('# Source\n| Action | Constraint |\n|---|---|\n| export | reject negative |\n')
    (repo / 'tests').mkdir()
    (repo / 'tests/test_export.py').write_text('def test_export():\n assert True\n')
    (repo / 'exporter.py').write_text('def export(n):\n return str(n)\n')
    (repo / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    subprocess.run(['git', '-C', repo, 'add', '.'], check=True)
    subprocess.run(['git', '-C', repo, '-c', 'user.name=Fixture', '-c', 'user.email=f@local', 'commit', '-qm', 'base'], check=True)
    with State(config.state_dir) as state:
        initialize(config, state)
        driver = ContentFixture(config, state)
        engine = Engine(config, state, driver)
        yield engine, driver, state


def test_table_only_constraint_reaches_po_and_native_content_cycle(content_env):
    engine, driver, state = content_env
    assert engine.run(max_batches=2) == 0
    task = dict(state.db.execute('SELECT * FROM tasks').fetchone())
    assert task['state'] == 'COMMITTED'
    assert driver.second_comparison['comparison'][0]['status'] == 'implemented'
    assert state.db.execute('SELECT count(*) FROM calls WHERE role="po_read"').fetchone()[0] == 1  # committed digest reuse
    assert not state.db.execute('SELECT satisfied_at FROM owner').fetchone()[0]
    base_files = engine.git.files(task['base_commit'])
    assert 'docs/forge/adr/0001-export.md' in base_files
    assert any(p.startswith('docs/forge/digests/') and p.endswith('.json') for p in base_files)
    calls = [c for c in driver.original_calls if c.role == 'adr_review']
    assert len(calls) == 1 and not calls[0].writable
    author = next(c for c in driver.original_calls if c.role == 'architect')
    families = {p.id: p.family for p in engine.config.profiles}
    assert families[calls[0].profile] != families[author.profile]
    accepted = engine.git.files(task['candidate_commit'])
    assert 'docs/forge/INDEX.md' in accepted and 'docs/forge/implemented/export.md' in accepted
    assert state.db.execute('SELECT candidate FROM commits WHERE task=?', (task['id'],)).fetchone()[0] == task['candidate_commit']


def test_architect_before_workers(content_env):
    engine, driver, state = content_env
    assert engine.run(max_batches=1) == 0
    roles = [c.role for c in driver.original_calls]
    assert roles.index('architect') < roles.index('adr_review') < roles.index('plan') < roles.index('implement') < roles.index('review')


def test_po_input_contains_implemented_state_and_test_inventory(content_env):
    engine, driver, state = content_env
    assert engine.run(max_batches=1) == 0
    selection = next(c for c in driver.original_calls if 'STAGE: selection' in c.prompt_text)
    assert 'test_export' in selection.prompt_text and 'implemented' in selection.prompt_text
    comparison = next(c for c in driver.original_calls if 'STAGE: comparison' in c.prompt_text)
    assert 'assert True' in comparison.prompt_text and 'blob_sha' in comparison.prompt_text


def test_architecture_cas_movement_preserves_review_and_starts_no_task(content_env, monkeypatch):
    engine, driver, state = content_env
    real = engine.git.cas
    monkeypatch.setattr(engine.git, 'cas', lambda candidate: False if candidate.ref.startswith('refs/forge/tasks/architecture-') else real(candidate))
    assert engine.run(max_batches=1) == 2
    assert not state.db.execute('SELECT 1 FROM tasks').fetchone()
    assert any(c.role == 'adr_review' for c in driver.original_calls)
    receipts = list((state.root / 'evidence').glob('batch-*/architecture.json'))
    assert len(receipts) == 1
    candidate = json.loads(receipts[0].read_text())['candidate']
    assert engine.git.tree(candidate['commit']) == candidate['tree']


def test_digest_paths_are_protected_from_task_writers(content_env):
    engine, driver, state = content_env
    from forge.gate import matches
    assert matches('docs/forge/digests/abc.json', engine.gate.protected)


def test_architect_rejects_old_adr_and_unsafe_doc_targets(content_env):
    engine, driver, state = content_env
    content = driver.content
    tasks = [{'title': 'one'}]
    value = {'adrs': [], 'tasks': [{'index': 0, 'design_notes': 'safe', 'doc_targets': ['docs/affected.md'], 'depends_on': []}]}
    assert content._architect_schema(value, tasks, {})
    value['adrs'] = [{'path': 'docs/forge/adr/0001-old.md', 'content': 'rewrite'}]
    assert not content._architect_schema(value, tasks, {'docs/forge/adr/0001-old.md': ('100644', 'hash')})
    value['adrs'] = []
    value['tasks'][0]['doc_targets'] = ['docs/forge/intent/requirements.md']
    assert not content._architect_schema(value, tasks, {})


def test_hard_native_content_plan_and_independent_acceptance(content_env):
    engine, driver, state = content_env
    driver.difficulty = 'hard'
    assert engine.run(max_batches=1) == 0
    row = state.db.execute('SELECT * FROM tasks').fetchone()
    assert row['difficulty'] == 'hard' and row['state'] == 'COMMITTED'
    plan = json.loads(row['metadata'])['plan']
    assert plan['requirements'] and plan['design'] and plan['tests'] and plan['doc_targets']
    profiles = {p.id: p for p in engine.config.profiles}
    plan_call = next(c for c in driver.original_calls if c.role == 'plan')
    review_call = next(c for c in driver.original_calls if c.role == 'review')
    assert 'strong' in profiles[plan_call.profile].tiers and 'strong' in profiles[review_call.profile].tiers


def test_bug_native_content_red_precedes_fix_and_preserves_test_blobs(content_env):
    engine, driver, state = content_env
    driver.kind = 'bug'
    assert engine.run(max_batches=1) == 0
    task = dict(state.db.execute('SELECT * FROM tasks').fetchone())
    assert task['state'] == 'COMMITTED' and task['red_commit']
    calls = [c for c in driver.original_calls if c.role in ('red', 'implement')]
    assert [c.role for c in calls] == ['red', 'implement']
    assert calls[1].resume_sid == task['session_id']
    assert engine.git.files(task['red_commit'])['tests/test_export.py'] == engine.git.files(task['candidate_commit'])['tests/test_export.py']
    meta = json.loads(task['metadata'])
    red = json.loads(Path(meta['red']['path']).read_text())
    assert red['results'][0]['rc'] == 1
    assert state.db.execute('SELECT created FROM artifacts WHERE path=?', (meta['red']['path'],)).fetchone()[0] <= state.db.execute('SELECT started FROM calls WHERE id=?', (next(c.id for c in calls if c.role == 'implement'),)).fetchone()[0]


def test_po_read_multidoc_reserved_ids_reach_prompt_and_collision_rejected(content_env, monkeypatch):
    """FIXTURE responses exercise real prepare, retry, bwrap and contextual validation."""
    engine, driver, state = content_env
    repo = engine.config.target_repo
    # Source labels can repeat; Forge digest identities cannot.
    for name in ('hidden.md', 'second.md'):
        (repo / 'docs/source' / name).write_text(
            '# Source\n| ID | Constraint |\n|---|---|\n| UC-COMBAT-001 | reject negative |\n' +
            ('\n![Reference](reference.png)\n' if name == 'second.md' else ''))
    (repo / 'docs/source/reference.png').write_bytes(b'\x89PNG\r\n\x1a\nfixture image')
    subprocess.run(['git', '-C', repo, 'add', 'docs/source'], check=True)
    subprocess.run(['git', '-C', repo, '-c', 'user.name=Fixture', '-c', 'user.email=f@local',
                    'commit', '-qm', 'two source documents with repeated source label'], check=True)
    reads, accepted = [], []

    class ReadsComplete(Exception):
        pass

    original_invoke = driver.invoke

    def invoke(call, account, validate, cancelled, **kwargs):
        if call.role == 'probe':
            return original_invoke(call, account, validate, cancelled, **kwargs)
        stage = call.prompt_text.split('STAGE: ')[1].split('\n')[0]
        if stage == 'selection':
            raise ReadsComplete
        assert stage == 'po_read'
        data = json.loads(call.prompt_text.split('DATA (untrusted JSON values):\n')[1])
        reads.append((call, data))
        second_attempt = sum(d['source'] == data['source'] for _, d in reads)
        req_id = 'second-UC-COMBAT-001' if data['reserved_req_ids'] and second_attempt == 2 else 'UC-COMBAT-001'
        answer = {'reqs': [{'id': req_id, 'text': 'UC-COMBAT-001: reject negative',
                            'anchors': data['anchors'], 'kind': 'constraint'}],
                  'images': [{'sha': image['sha'], 'observation': 'Fixture reference image'} for image in data['images']]}

        def checked(value):
            valid = validate(value)
            accepted.append((req_id, valid))
            return valid

        return FakeDriver.invoke(driver, replace(call, prompt_text=json.dumps({'answer': answer})),
                                 account, checked, cancelled, **kwargs)

    monkeypatch.setattr(driver, 'invoke', invoke)
    with pytest.raises(ReadsComplete):
        engine.run(max_batches=1)
    assert accepted == [('UC-COMBAT-001', True), ('UC-COMBAT-001', False), ('second-UC-COMBAT-001', True)]
    assert len(reads) == 3  # One rejected collision, one bounded retry; no dropped requirement.
    assert reads[0][1]['reserved_req_ids'] == []
    second = reads[1:]
    for call, data in second:
        assert data['source'] == 'docs/source/second.md'
        assert data['reserved_req_ids'] == ['UC-COMBAT-001']
        assert data['anchors'] == ['docs/source/second.md#table1.row1']
        assert len(call.images) == len(data['images']) == 1
        trusted = call.prompt_text.split('DATA (untrusted JSON values):')[0]
        assert 'Every reqs[].id must be absent from DATA reserved_req_ids and unique in this answer' in trusted
    assert [row[0] for row in state.db.execute("SELECT outcome FROM calls WHERE role='po_read' ORDER BY started")] == ['ok', 'protocol', 'ok']
    assert not state.db.execute('SELECT 1 FROM tasks').fetchone()
