import json
from pathlib import Path
import subprocess
import uuid

import pytest

from forge.gate import CHECKLIST, Gate, GateError, junit, validate_node
from forge.sandbox import Spec, argv, revalidate, work_mount
from forge.state import atomic_json
from test_gitops import project, prepare


COMMANDS = {'test': ('/usr/bin/python3', '-m', 'pytest', '-q', '-p', 'no:cacheprovider'),
            'lint': (), 'test_one': ('/usr/bin/python3', '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                                    '--junitxml={junit}', '{node}')}
DOCS = ['docs/architecture.md']


def make_gate(p, commands=None):
    return Gate(p.git, p.state, p.roots, commands or COMMANDS)


def feature(p):
    (p.work / 'tests').mkdir(exist_ok=True)
    (p.work / 'tests/test_value.py').write_text('from app import value\ndef test_value():\n    assert value() == 1\n')
    return prepare(p)


def review_fixture(p, gate, candidate, checked, *, result=None, family='anthropic', session=None, mutate=False):
    """FIXTURE review record; real bwrap read-only mount. No LLM claim."""
    work = gate.review_export('t', candidate)
    spec = Spec('agent', (work_mount(p.roots, work, False),), False, Path('/tmp/home'),
                ('/usr/bin/python3', '-c', "import errno,pathlib\ntry: pathlib.Path('/work/app.py').write_text('bad')\nexcept OSError as e: assert e.errno == errno.EROFS\nelse: raise AssertionError('write allowed')"))
    revalidate(spec)
    command = subprocess.run(argv(spec), capture_output=True, text=True)
    assert command.returncode == 0, command.stderr
    call = uuid.uuid4().hex
    directory = p.state.root / 'calls' / call
    directory.mkdir(parents=True)
    manifest = directory / 'manifest.json'
    atomic_json(manifest, spec.manifest())
    with p.state.transaction() as db:
        db.execute("INSERT INTO calls(id,task,role,profile,status,outcome,started,session_out,sandbox,manifest_path) VALUES(?,'t','review','fixture-review','done','ok',1,?,'bwrap',?)",
                   (call, session or uuid.uuid4().hex, str(manifest)))
    if mutate:
        (work / 'app.py').write_text('altered outside sandbox')
    result = result or {'verdict': 'PASS', 'tree': candidate.tree,
                        'checklist': {key: 'pass' for key in CHECKLIST}, 'findings': []}
    return gate.review('t', candidate, checked, work, result, call_id=call,
                       reviewer_family=family, implementer_families=['openai'])


def test_gate_requires_all_checks(project):
    p = project
    c = feature(p)
    gate = make_gate(p)
    checked = gate.check('t', c, DOCS)
    receipt = review_fixture(p, gate, c, checked)
    gate.verify_receipt('t', c, receipt)
    bad = make_gate(p, {**COMMANDS, 'lint': ('/usr/bin/false',)})
    with pytest.raises(GateError, match='lint failed'):
        bad.check('t', c, DOCS)


@pytest.mark.parametrize('mutation', ["open('app.py','w').write('bad')", "__import__('os').unlink('app.py')", "open('new.py','w').write('bad')"])
def test_gate_mutation_of_tracked_file_fails(project, mutation):
    p = project
    c = feature(p)
    gate = make_gate(p, {**COMMANDS, 'test': ('/usr/bin/python3', '-c', mutation)})
    with pytest.raises(GateError, match='gate mutated'):
        gate.check('t', c, DOCS)


def test_gate_ignored_outputs_allowed(project):
    p = project
    c = feature(p)
    gate = make_gate(p, {**COMMANDS, 'lint': ('/usr/bin/python3', '-c', "import pathlib; pathlib.Path('__pycache__').mkdir(exist_ok=True); pathlib.Path('__pycache__/output').write_text('ok')")})
    assert gate.check('t', c, DOCS)


@pytest.mark.parametrize('path', ['docs/source/spec.md', 'docs/forge/intent/requirements.md', 'docs/forge/adr/0001.md'])
def test_protected_paths_reject(project, path):
    p = project
    dest = p.work / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text('weaken intent')
    c = feature(p)
    with pytest.raises(GateError, match='protected'):
        make_gate(p).check('t', c, DOCS)


def test_controller_new_adr_exact_blob_allowed(project):
    p = project
    path = 'docs/forge/adr/0002.md'
    dest = p.work / path
    dest.parent.mkdir(parents=True)
    dest.write_text('Controller approved decision')
    c = feature(p)
    blob = p.git.files(c.commit)[path][1]
    gate = make_gate(p)
    gate.check('t', c, DOCS, adr_additions={path: blob})
    with pytest.raises(GateError, match='protected'):
        gate.check('t', c, DOCS, adr_additions={path: 'bad'})


def test_doc_targets_required(project):
    p = project
    c = feature(p)
    with pytest.raises(GateError, match='doc target'):
        make_gate(p).check('t', c, ['docs/missing.md'])
    (p.work / 'docs/forge/tasks/t.md').write_text('## Plan\n')
    c = p.git.candidate('t', p.work, p.base)
    with pytest.raises(GateError, match='sections'):
        make_gate(p).check('t', c, DOCS)


@pytest.mark.parametrize('node', ['-x', '../outside.py', '/etc/passwd', 'tests/a.py;id', 'tests/a.py::test$(id)'])
def test_node_arg_validated(project, node):
    with pytest.raises(GateError):
        validate_node(node, project.work)


@pytest.mark.parametrize('bad', ['family', 'checklist', 'tree', 'content', 'reused'])
def test_review_checklist_family_tree_content_and_freshness(project, bad):
    p = project
    c = feature(p)
    gate = make_gate(p)
    checked = gate.check('t', c, DOCS)
    result = {'verdict': 'PASS', 'tree': c.tree, 'checklist': {key: 'pass' for key in CHECKLIST}, 'findings': []}
    if bad == 'checklist':
        del result['checklist']['naming_clear']
    if bad == 'tree':
        result['tree'] = p.git.tree(p.base)
    if bad == 'reused':
        review_fixture(p, gate, c, checked, session='same')
    with pytest.raises(GateError):
        review_fixture(p, gate, c, checked, result=result, family='openai' if bad == 'family' else 'anthropic',
                       mutate=bad == 'content', session='same' if bad == 'reused' else None)


def red_candidate(p, body='assert value() == 1'):
    (p.work / 'tests').mkdir(exist_ok=True)
    (p.work / 'tests/test_value.py').write_text('from app import value\ndef test_value():\n    ' + body + '\n')
    with p.state.transaction() as db:
        db.execute("UPDATE tasks SET kind='bug' WHERE id='t'")
    return p.git.candidate('t', p.work, p.base)


def test_red_green_actual_assertion_and_same_blobs(project):
    p = project
    gate = make_gate(p)
    red = red_candidate(p)
    receipt = gate.red('t', red, ['tests/test_value.py::test_value'])
    assert p.state.db.execute("SELECT red_commit FROM tasks WHERE id='t'").fetchone()[0] == red.commit
    c = prepare(p)
    gate.check('t', c, DOCS, red=receipt)
    (p.work / 'tests/test_value.py').write_text('def test_value(): assert True\n')
    altered = p.git.candidate('t', p.work, p.base)
    with pytest.raises(GateError, match='unchanged RED'):
        gate.check('t', altered, DOCS, red=receipt)


@pytest.mark.parametrize('case', ['pass', 'import', 'setup', 'skip', 'missing', 'zero', 'exception'])
def test_red_rejects_invalid_proof(project, case):
    p = project
    gate = make_gate(p)
    c = red_candidate(p)
    path = p.work / 'tests/test_value.py'
    contents = {'pass': 'def test_value(): assert True\n', 'import': 'import nonexistent_module_xyz\n',
                'setup': 'import pytest\n@pytest.fixture(autouse=True)\ndef broken(): raise RuntimeError("setup")\ndef test_value(): assert False\n',
                'skip': 'import pytest\n@pytest.mark.skip\ndef test_value(): assert False\n',
                'zero': 'x=1\n', 'exception': 'def test_value(): raise ValueError("bad")\n'}
    if case in contents:
        path.write_text(contents[case])
        c = p.git.candidate('t', p.work, p.base)
    node = 'tests/test_value.py::absent' if case == 'missing' else 'tests/test_value.py::test_value'
    with pytest.raises(GateError):
        gate.red('t', c, [node])


def test_red_requires_test_paths_only_and_junit_config(project):
    p = project
    c = red_candidate(p)
    (p.work / 'app.py').write_text('fix before RED\n')
    c = p.git.candidate('t', p.work, p.base)
    with pytest.raises(GateError, match='test paths only'):
        make_gate(p).red('t', c, ['tests/test_value.py::test_value'])
    p.git.export(p.base, p.state.root / 'ws/t/gate-1')
    with pytest.raises(GateError, match='requires'):
        make_gate(p, {**COMMANDS, 'test_one': ('/usr/bin/true', '{node}')}).command(
            't', p.work, ('/usr/bin/true', '{node}'), node='tests/test_value.py::test_value')


def test_junit_cannot_substitute_other_node():
    with pytest.raises(GateError, match='missing'):
        junit(b'<testsuite><testcase classname="tests.other" name="test_value"><failure>AssertionError</failure></testcase></testsuite>',
              1, 'tests/test_value.py::test_value', True)


def test_red_evidence_precedes_fix_call(project):
    p = project
    gate = make_gate(p)
    red = red_candidate(p)
    receipt = gate.red('t', red, ['tests/test_value.py::test_value'])
    assert gate.require_red_before_fix('t', receipt) == red.commit
    with p.state.transaction() as db:
        db.execute("UPDATE tasks SET red_commit=NULL WHERE id='t'")
    with pytest.raises(GateError, match='durable before fix'):
        gate.require_red_before_fix('t', receipt)


def test_gate_actual_command_cannot_read_state_or_real_home(project):
    p = project
    secret = p.state.root / 'controller-secret'
    secret.write_text('must stay hidden')
    c = feature(p)
    script = f"from pathlib import Path; assert not Path({str(secret)!r}).exists(); assert not Path({str(Path.home() / '.ssh')!r}).exists(); assert not Path('/work/.git').exists()"
    gate = make_gate(p, {**COMMANDS, 'lint': ('/usr/bin/python3', '-c', script)})
    gate.check('t', c, DOCS)


def test_bug_gate_without_red_cannot_accept(project):
    p = project
    c = feature(p)
    with p.state.transaction() as db:
        db.execute("UPDATE tasks SET kind='bug' WHERE id='t'")
    with pytest.raises(GateError, match='requires durable RED'):
        make_gate(p).check('t', c, DOCS)


def test_configured_source_paths_always_protected(project):
    p = project
    path = p.work / 'reference/spec.md'
    path.parent.mkdir()
    path.write_text('source requirement')
    c = feature(p)
    gate = Gate(p.git, p.state, p.roots, COMMANDS, sources=('reference/**',), protected=())
    with pytest.raises(GateError, match='protected'):
        gate.check('t', c, DOCS)


def test_gate_timeout_has_durable_exit_and_no_surviving_writer(project):
    p = project
    c = feature(p)
    gate = Gate(p.git, p.state, p.roots, {**COMMANDS, 'lint': ('/usr/bin/sleep', '60')}, timeout=0.1)
    with pytest.raises(GateError, match='timed out'):
        gate.check('t', c, DOCS)
    row = p.state.db.execute("SELECT status,outcome,result_path FROM calls WHERE role='gate'").fetchone()
    assert row['status'] == 'done' and row['outcome'] == 'timeout'
    result = json.loads(Path(row['result_path']).read_text())
    assert result['rc'] < 0 and Path(result['raw_dir'], 'stderr').exists()
