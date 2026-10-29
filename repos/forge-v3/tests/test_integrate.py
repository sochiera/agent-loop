"""Local CAS/rebase and actual SIGKILL recovery with real temporary git repos."""
import json
import os
import signal
from pathlib import Path

import pytest

from forge.gitops import Git, Integrator, IntegrationBlocked
from forge.gate import GateError
from forge.state import State
from test_gitops import project, hostgit, prepare
from test_gate import feature, make_gate, review_fixture, DOCS


def validated(p, candidate=None):
    candidate = candidate or feature(p)
    gate = make_gate(p)
    checked = gate.check('t', candidate, DOCS)
    reviewed = review_fixture(p, gate, candidate, checked)
    return candidate, gate, reviewed


def external_commit(p, conflict=False):
    path = p.target / ('app.py' if conflict else 'external.txt')
    path.write_text('external writer content\n')
    hostgit(p.target, 'add', '.')
    hostgit(p.target, 'commit', '-m', 'external main advance')
    return p.git.head()


def kill_at(p, point, operation):
    pid = os.fork()
    if pid == 0:
        try:
            os.environ['FORGE_TEST_CRASH_AT'] = point
            # No inherited active transaction; child owns its own connection.
            p.state.close()
            p.state = State(p.git.root)
            operation()
        except BaseException:
            import traceback
            traceback.print_exc()
            os._exit(3)
        os._exit(0)
    _, status = os.waitpid(pid, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL


def test_accepted_commit_tree_eq_tested_eq_reviewed(project):
    p = project
    c, gate, reviewed = validated(p)
    integrator = Integrator(p.git, p.state, gate)
    assert integrator.accept('t', c, reviewed) == c.commit
    assert p.git.head() == c.commit
    assert p.git.tree(p.git.head()) == c.tree
    assert 'docs/architecture.md' in p.git.changed(p.base, c.commit)
    assert integrator.accept('t', c, reviewed) == c.commit
    assert hostgit(p.target, 'rev-list', '--count', 'main') == '2'


@pytest.mark.parametrize('point', ['after_pending', 'after_cas'])
def test_crash_recovery_exactly_one_accepted_commit(project, point):
    p = project
    c, gate, receipt = validated(p)
    kill_at(p, point, lambda: Integrator(p.git, p.state, make_gate(p)).accept('t', c, receipt))
    assert p.state.db.execute("SELECT state FROM commits WHERE task='t'").fetchone()[0] == 'pending'
    integrator = Integrator(p.git, p.state, gate)
    integrator.recover()
    integrator.recover()
    assert p.git.head() == c.commit
    assert hostgit(p.target, 'rev-list', '--count', 'main') == '2'
    assert p.state.db.execute("SELECT state FROM tasks WHERE id='t'").fetchone()[0] == 'COMMITTED'


@pytest.mark.parametrize('point', ['after_candidate_ref', 'after_candidate_durable'])
def test_candidate_crash_never_integrates_without_gate(project, point):
    p = project
    (p.work / 'app.py').write_text('preserved candidate work\n')
    def create():
        c = p.git.candidate('t', p.work, p.base)
        p.git.save_candidate(p.state, 't', c)
    kill_at(p, point, create)
    refs = hostgit(p.target, 'for-each-ref', '--format=%(objectname)', 'refs/forge/tasks/t').splitlines()
    assert len(refs) == 1
    Integrator(p.git, p.state, make_gate(p)).recover()
    assert p.git.head() == p.base
    assert (p.work / 'app.py').read_text() == 'preserved candidate work\n'
    assert p.state.db.execute('SELECT count(*) FROM commits').fetchone()[0] == 0


def test_cancel_before_pending_prevents_accept(project):
    p = project
    c, gate, receipt = validated(p)
    with p.state.transaction() as db:
        db.execute("INSERT INTO cancels(target,requested) VALUES('t',1)")
    assert Integrator(p.git, p.state, gate).accept('t', c, receipt) is None
    assert p.git.head() == p.base
    assert p.state.db.execute('SELECT count(*) FROM commits').fetchone()[0] == 0


def test_cancel_after_pending_reports_accepted(project):
    p = project
    c, gate, receipt = validated(p)
    kill_at(p, 'after_pending', lambda: Integrator(p.git, p.state, make_gate(p)).accept('t', c, receipt))
    with p.state.transaction() as db:
        db.execute("INSERT INTO cancels(target,requested) VALUES('t',2)")
    Integrator(p.git, p.state, gate).recover()
    assert p.git.head() == c.commit
    assert p.state.db.execute("SELECT state FROM tasks WHERE id='t'").fetchone()[0] == 'COMMITTED'


def test_main_moved_clean_merge_regates_rereviews(project):
    p = project
    c, gate, receipt = validated(p)
    moved = external_commit(p)
    seen = []
    def revalidate(merged):
        assert merged.base == moved
        assert merged.commit != c.commit
        seen.append(merged)
        checked = gate.check('t', merged, DOCS)
        return review_fixture(p, gate, merged, checked)
    accepted = Integrator(p.git, p.state, gate).accept('t', c, receipt, revalidate)
    assert len(seen) == 1 and accepted == seen[0].commit
    assert p.git.text('rev-parse', c.ref) == c.commit
    assert 'external.txt' in p.git.files(accepted)
    assert p.state.db.execute("SELECT count(*) FROM artifacts WHERE kind='review'").fetchone()[0] == 2
    assert p.git.text('cat-file', '-p', accepted).splitlines()[1] == 'parent ' + moved


def test_main_moved_conflict_blocks_preserved(project):
    p = project
    c, gate, receipt = validated(p)
    moved = external_commit(p, conflict=True)
    with pytest.raises(IntegrationBlocked, match='base_conflict'):
        Integrator(p.git, p.state, gate).accept('t', c, receipt)
    assert p.git.head() == moved
    assert p.git.text('rev-parse', c.ref) == c.commit
    blocked = json.loads(p.state.db.execute("SELECT blocked FROM tasks WHERE id='t'").fetchone()[0])
    assert blocked['preserved']['tree'] == c.tree and blocked['unblock_condition']


def test_new_candidate_cannot_reuse_old_gate_review(project):
    p = project
    c, gate, receipt = validated(p)
    external_commit(p)
    with pytest.raises(GateError, match='candidate mismatch'):
        Integrator(p.git, p.state, gate).accept('t', c, receipt, lambda merged: receipt)
    assert p.git.head() != c.commit


def test_tampered_receipt_cannot_accept(project):
    p = project
    c, gate, receipt = validated(p)
    Path(receipt.path).write_text('{}')
    with pytest.raises(GateError, match='changed'):
        Integrator(p.git, p.state, gate).accept('t', c, receipt)
    assert p.git.head() == p.base


def test_pending_recovery_main_movement_requires_new_gate_review(project):
    p = project
    c, gate, receipt = validated(p)
    kill_at(p, 'after_pending', lambda: Integrator(p.git, p.state, make_gate(p)).accept('t', c, receipt))
    moved = external_commit(p)
    seen = []
    def revalidate(merged):
        seen.append(merged.commit)
        return review_fixture(p, gate, merged, gate.check('t', merged, DOCS))
    Integrator(p.git, p.state, gate).recover(revalidate)
    assert len(seen) == 1 and p.git.head() == seen[0]
    assert p.git.head() != c.commit and p.git.head() != moved
    assert hostgit(p.target, 'rev-list', '--count', 'main') == '3'


def test_cas_crash_then_external_descendant_does_not_duplicate_acceptance(project):
    p = project
    c, gate, receipt = validated(p)
    kill_at(p, 'after_cas', lambda: Integrator(p.git, p.state, make_gate(p)).accept('t', c, receipt))
    # Simulate another writer updating from the now-accepted commit.
    hostgit(p.target, 'reset', '--hard', 'main')
    moved = external_commit(p)
    Integrator(p.git, p.state, gate).recover()
    assert p.git.head() == moved
    assert hostgit(p.target, 'rev-list', '--count', 'main') == '3'
    assert p.state.db.execute("SELECT candidate,state FROM commits WHERE task='t'").fetchone()[:] == (c.commit, 'done')


def test_rebase_budget_blocks_with_both_refs(project):
    p = project
    c, gate, receipt = validated(p)
    external_commit(p)
    with pytest.raises(IntegrationBlocked, match='budget'):
        Integrator(p.git, p.state, gate, max_rebases=0).accept('t', c, receipt)
    assert p.git.text('rev-parse', c.ref) == c.commit


def test_accept_retries_pending_exact_candidate_after_cas(project):
    p = project
    c, gate, receipt = validated(p)
    kill_at(p, 'after_cas', lambda: Integrator(p.git, p.state, make_gate(p)).accept('t', c, receipt))
    assert Integrator(p.git, p.state, gate).accept('t', c, receipt) == c.commit
    note = json.loads(p.git.text('notes', '--ref=forge', 'show', c.commit))
    assert note['sandbox'] == 'bwrap' and len(note['evidence']) == 2
    assert hostgit(p.target, 'rev-list', '--count', 'main') == '2'


def test_main_moves_between_pending_and_cas_preserves_acceptance_point(project, monkeypatch):
    p = project
    c, gate, receipt = validated(p)
    original = p.git.cas
    moved_once = []
    def racing_cas(candidate):
        if not moved_once:
            moved_once.append(external_commit(p))
            with p.state.transaction() as db:
                db.execute("INSERT INTO cancels(target,requested) VALUES('t',3)")
        return original(candidate)
    monkeypatch.setattr(p.git, 'cas', racing_cas)
    renewed = []
    def revalidate(merged):
        renewed.append(merged)
        return review_fixture(p, gate, merged, gate.check('t', merged, DOCS))
    accepted = Integrator(p.git, p.state, gate).accept('t', c, receipt, revalidate)
    assert len(renewed) == 1 and accepted == renewed[0].commit
    assert p.git.head() == accepted
    assert p.state.db.execute("SELECT state FROM tasks WHERE id='t'").fetchone()[0] == 'COMMITTED'
