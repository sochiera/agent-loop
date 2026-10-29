"""F1/F1b: real Git/gates/crashes; model review content is a labelled fixture."""
import json

import pytest

from forge.engine import Engine
from forge.gitops import Integrator, IntegrationBlocked
from test_gitops import project, hostgit
from test_integrate import validated, external_commit, kill_at
from test_gate import make_gate, review_fixture, DOCS


@pytest.mark.parametrize('external_path', ['external.txt', '.gitignore'])
def test_rebased_workspace_fix_preserves_external_content(project, external_path):
    p = project
    candidate, gate, review = validated(p)
    path = p.target / external_path
    before = path.read_text() if path.exists() else ''
    path.write_text(before + 'external-main-line\n')
    hostgit(p.target, 'add', '.')
    hostgit(p.target, 'commit', '-m', 'External file or existing-line advance')
    moved = p.git.head()
    ignored = p.work / '__pycache__'
    ignored.mkdir()
    (ignored / 'preserved.pyc').write_bytes(b'preserved ignored bytes')
    with pytest.raises(IntegrationBlocked):
        Integrator(p.git, p.state, gate).accept('t', candidate, review)
    row = p.state.db.execute("SELECT * FROM tasks WHERE id='t'").fetchone()
    assert row['base_commit'] == moved
    assert (p.work / external_path).read_text() == before + 'external-main-line\n'
    assert p.git.snapshot(p.work, moved) == row['candidate_tree']
    # A real later fix writes the workspace, then takes Engine's normal snapshot.
    (p.work / 'fix.txt').write_text('later fix round\n')
    fixed = p.git.candidate('t', p.work, moved)
    assert 'fix.txt' in p.git.files(fixed.commit)
    assert p.git.blob(p.git.files(fixed.commit)[external_path][1]).decode() == before + 'external-main-line\n'
    checked = gate.check('t', fixed, DOCS)
    receipt = review_fixture(p, gate, fixed, checked)
    assert Integrator(p.git, p.state, gate).accept('t', fixed, receipt) == fixed.commit
    # Re-cloning must retain the old workspace (including ignored files) as evidence.
    assert any(q.read_bytes() == b'preserved ignored bytes' for q in p.work.parent.glob('rebase-preserved-*/__pycache__/preserved.pyc'))


def test_engine_recovery_rebase_regates_once_and_retires_old_pending(project):
    p = project
    candidate, gate, receipt = validated(p)
    with p.state.transaction() as db:
        db.execute("UPDATE tasks SET state='INTEGRATING'")
    kill_at(p, 'after_pending', lambda: Integrator(p.git, p.state, make_gate(p)).accept('t', candidate, receipt))
    external_commit(p)
    engine = Engine.__new__(Engine)
    engine.state, engine.git = p.state, p.git
    engine.integrator = Integrator(p.git, p.state, gate)
    engine.recover()
    first = dict(p.state.db.execute("SELECT * FROM tasks WHERE id='t'").fetchone())
    assert first['state'] == 'GATING'
    assert first['candidate_commit'] != candidate.commit
    assert 'external.txt' in p.git.files(first['candidate_commit'])
    # The acceptance point survives, but its superseded candidate is never replayed.
    assert tuple(p.state.db.execute("SELECT candidate,state FROM commits WHERE task='t'").fetchone()) == (candidate.commit, 'pending')
    for _ in range(3):
        engine.recover()
        row = p.state.db.execute("SELECT * FROM tasks WHERE id='t'").fetchone()
        assert (row['state'], row['rebases'], row['candidate_commit']) == ('GATING', 1, first['candidate_commit'])
        assert p.git.head() == first['base_commit']  # Never CAS an ungated merged tree.
    merged = engine.candidate(first)
    fresh = review_fixture(p, gate, merged, gate.check('t', merged, DOCS))
    assert engine.integrator.accept('t', merged, fresh) == merged.commit
    engine.recover()
    assert p.state.db.execute("SELECT state FROM commits WHERE task='t'").fetchone()[0] == 'done'


def test_cancel_after_recovery_rebase_keeps_acceptance_point(project):
    p = project
    candidate, gate, receipt = validated(p)
    with p.state.transaction() as db:
        db.execute("UPDATE tasks SET state='INTEGRATING'")
    kill_at(p, 'after_pending', lambda: Integrator(p.git, p.state, make_gate(p)).accept('t', candidate, receipt))
    external_commit(p)
    with pytest.raises(IntegrationBlocked):
        Integrator(p.git, p.state, gate).recover()
    with p.state.transaction() as db:
        db.execute("INSERT INTO cancels(target,requested) VALUES('t',1)")
    for _ in range(2):
        Integrator(p.git, p.state, gate).recover()  # No replay of the stale candidate.
    row = p.state.db.execute("SELECT * FROM tasks WHERE id='t'").fetchone()
    assert (row['state'], row['rebases']) == ('GATING', 1)
    merged = p.git.candidate('t', p.work, row['base_commit'])
    assert merged.tree == row['candidate_tree'] and 'external.txt' in p.git.files(merged.commit)
    fresh = review_fixture(p, gate, merged, gate.check('t', merged, DOCS))
    # Cancellation after the pending row reports already accepted (plan P2).
    assert Integrator(p.git, p.state, gate).accept('t', merged, fresh) == merged.commit
    assert p.git.head() == merged.commit
