"""Real disposable repositories; no native-model evidence is claimed here."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

import pytest

from forge.gitops import Git, GitError
from forge.state import State
from forge.sandbox import Roots


def hostgit(repo, *args):
    return subprocess.check_output(['/usr/bin/git', '-C', str(repo), *args], stderr=subprocess.STDOUT).decode().strip()


@pytest.fixture
def project(tmp_path):
    target = tmp_path / 'repo'
    target.mkdir()
    hostgit(target, 'init', '-b', 'main')
    hostgit(target, 'config', 'user.name', 'Test')
    hostgit(target, 'config', 'user.email', 'test@localhost')
    (target / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n*.pyc\n')
    (target / 'app.py').write_text('def value():\n    return -1\n')
    (target / 'docs').mkdir()
    (target / 'docs/architecture.md').write_text('Initial architecture\n')
    hostgit(target, 'add', '.')
    hostgit(target, 'commit', '-m', 'base')
    state = State(tmp_path / 'state')
    with state.transaction() as db:
        db.execute("INSERT INTO batches(id,n,state) VALUES('b',1,'OPEN')")
        db.execute("INSERT INTO tasks(id,batch,kind,difficulty,work_key,state) VALUES('t','b','feature','easy','key','IMPLEMENTING')")
    git = Git(target, state.root)
    base = git.head()
    work = state.root / 'ws/t/src'
    work.parent.mkdir(parents=True)
    git.clone(base, work)
    roots = Roots(tmp_path / 'home', state.root, tmp_path / 'config', target, {})
    roots.home.mkdir()
    with state.transaction() as db:
        db.execute('UPDATE tasks SET base_commit=?,workspace=? WHERE id=?', (base, str(work), 't'))
    yield SimpleNamespace(git=git, state=state, target=target, base=base, work=work, roots=roots)
    state.close()


def prepare(project):
    p = project
    (p.work / 'app.py').write_text('def value():\n    return 1\n')
    (p.work / 'docs/architecture.md').write_text('Value is positive.\n')
    directory = p.work / 'docs/forge/tasks'
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 't.md').write_text(''.join(f'## {s}\nConcrete task {s.lower()}.\n' for s in ['Plan','Changes','Tests','Docs','Evidence']))
    candidate = p.git.candidate('t', p.work, p.base)
    p.git.save_candidate(p.state, 't', candidate)
    return candidate


def test_git_ops_allowlist(project):
    for command in ('push', 'remote', 'fetch', 'config', 'checkout', 'reset'):
        with pytest.raises(GitError, match='forbidden'):
            project.git.run(command)


def test_controller_git_ignores_workspace_config(project):
    p = project
    marker = p.state.root / 'compromised'
    (p.work / '.git/config').write_text(f'[core]\nfsmonitor = touch {marker}\n[filter "evil"]\nclean = touch {marker}\nrequired = true\n')
    (p.work / '.gitattributes').write_text('*.py filter=evil\n')
    nested = p.work / 'nested/.git'
    nested.mkdir(parents=True)
    (nested / 'config').write_text(f'[core]\nfsmonitor = touch {marker}\n')
    (nested.parent / 'content.txt').write_text('preserve content\n')
    candidate = prepare(p)
    assert not marker.exists()
    assert 'nested/content.txt' in p.git.files(candidate.commit)
    assert not any('.git/' in path for path in p.git.files(candidate.commit))


def test_clone_has_no_origin_and_no_shared_objects(project):
    p = project
    assert 'remote' not in (p.work / '.git/config').read_text()
    assert (p.work / 'app.py').read_text() == (p.target / 'app.py').read_text()
    for obj in (p.target / '.git/objects').glob('*/*'):
        cloned = p.work / '.git/objects' / obj.parent.name / obj.name
        if cloned.exists():
            assert obj.stat().st_ino != cloned.stat().st_ino


def test_candidate_preserved_and_export_exact(project):
    p = project
    candidate = prepare(p)
    assert p.git.head() == p.base
    assert p.git.text('rev-parse', candidate.ref) == candidate.commit
    work = p.git.export(candidate.commit, p.state.root / 'ws/t/review-1')
    assert p.git.snapshot(work, candidate.commit) == candidate.tree
    assert not (work / '.git').exists()
    assert f'Forge-Tree: {candidate.tree}' in p.git.text('cat-file', '-p', candidate.commit)


def _committed_empty_repo(tmp_path):
    target = tmp_path / 'repo'
    target.mkdir()
    subprocess.check_call(['git', 'init', '--initial-branch=main', str(target)])
    subprocess.check_call(['git', '-C', str(target), '-c', 'user.name=Forge Empty',
                           '-c', 'user.email=empty@localhost', 'commit', '--allow-empty', '-m', 'empty bootstrap'])
    return target


def test_export_nonempty_tree_still_extracts(project):
    destination = project.state.root / 'ws' / 'plain-export'
    project.git.export(project.base, destination)
    assert (destination / 'app.py').read_text().startswith('def value')
    assert project.git.snapshot(destination, project.base) == project.git.tree(project.base)


class CommittedEmptyExportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    def test_export_committed_empty_tree(self):
        target = _committed_empty_repo(self.tmp_path)
        git = Git(target, self.tmp_path / 'state')
        destination = self.tmp_path / 'state' / 'export'
        git.export(git.head(), destination)
        self.assertEqual(git.files(git.head()), {})
        self.assertEqual(list(destination.iterdir()), [])
        self.assertEqual(git.snapshot(destination, git.head()), git.tree(git.head()))

    def test_corrupt_empty_archive_header_is_rejected(self):
        target = _committed_empty_repo(self.tmp_path)
        git = Git(target, self.tmp_path / 'state')
        real = git.run

        def wrapped(command, *args, **kwargs):
            proc = real(command, *args, **kwargs)
            if command == 'archive':
                # Real git archive, then one header byte flipped. The stored
                # checksum stays put, so the empty-tree shape still matches.
                payload = bytearray(proc.stdout)
                self.assertGreaterEqual(len(payload), 512)
                payload[0] ^= 1
                proc.stdout = bytes(payload)
            return proc

        git.run = wrapped
        with self.assertRaises(GitError) as caught:
            git.export(git.head(), self.tmp_path / 'state' / 'corrupt')
        message = str(caught.exception)
        self.assertIn('invalid archive', message)
        self.assertIn('bad checksum', message)

    def test_invalid_archive_is_not_an_empty_export(self):
        target = _committed_empty_repo(self.tmp_path)
        git = Git(target, self.tmp_path / 'state')
        real = git.run

        def wrapped(command, *args, **kwargs):
            proc = real(command, *args, **kwargs)
            if command == 'archive':
                proc.stdout = b'not-a-tar'
            return proc

        git.run = wrapped
        with self.assertRaises(GitError) as caught:
            git.export(git.head(), self.tmp_path / 'state' / 'bad')
        self.assertIn('invalid archive', str(caught.exception))

    def test_archive_process_failure_stays_git_error(self):
        target = _committed_empty_repo(self.tmp_path)
        git = Git(target, self.tmp_path / 'state')
        with self.assertRaises(GitError) as caught:
            git.export('deadbeef' * 5, self.tmp_path / 'state' / 'missing')
        message = str(caught.exception)
        self.assertNotIn('invalid archive', message)
        self.assertNotIn('end of file header', message)


def test_archive_cannot_omit_candidate_files(project):
    p = project
    (p.work / '.gitattributes').write_text('app.py export-ignore\n')
    candidate = prepare(p)
    with pytest.raises(GitError, match='archive attributes'):
        p.git.export(candidate.commit, p.state.root / 'ws/t/gate-1')


def test_snapshot_never_executes_target_configured_clean_driver(project):
    p = project
    marker = p.state.root / 'filter-executed'
    hostgit(p.target, 'config', 'filter.evil.clean', f'touch {marker}')
    hostgit(p.target, 'config', 'filter.evil.required', 'true')
    (p.work / '.gitattributes').write_text('*.py filter=evil\n')
    c = prepare(p)
    assert not marker.exists()
    assert p.git.blob(p.git.files(c.commit)['app.py'][1]) == (p.work / 'app.py').read_bytes()


def test_merge_never_executes_target_configured_driver(project):
    p = project
    marker = p.state.root / 'merge-executed'
    hostgit(p.target, 'config', 'merge.evil.driver', f'touch {marker}')
    (p.work / '.gitattributes').write_text('*.py merge=evil\n')
    c = prepare(p)
    (p.target / 'app.py').write_text('external conflicting content\n')
    hostgit(p.target, 'add', '.')
    hostgit(p.target, 'commit', '-m', 'external')
    from forge.gitops import IntegrationBlocked
    with pytest.raises(IntegrationBlocked):
        p.git.merge(c, p.git.head(), 't')
    assert not marker.exists()
