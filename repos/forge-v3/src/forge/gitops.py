"""Controller-owned git, immutable candidates and recoverable local acceptance.

No invocation discovers git metadata in an agent workspace. Indexes, git directory
and cwd are controller-owned; even nested agent .git directories are never read.
"""
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
import uuid


ALLOWED = frozenset({'read-tree', 'add', 'write-tree', 'commit-tree', 'update-ref',
                     'merge-tree', 'archive', 'clone', 'notes', 'rev-parse', 'cat-file',
                     'diff', 'ls-tree'})
ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
OID = re.compile(r'^[0-9a-f]{40,64}$')


def _ustar_checksum_matches(header):
    """POSIX/GNU header checksum. Unsigned and signed sums, checksum bytes as spaces."""
    field = header[148:156].split(b'\0', 1)[0].strip()
    try:
        stored = int(field or b'0', 8)
    except ValueError:
        return False
    unsigned = 256 + sum(header[:148]) + sum(header[156:])
    signed = 256
    for byte in header[:148] + header[156:]:
        signed += byte - 256 if byte > 127 else byte
    return stored in (unsigned, signed)


def _committed_empty_archive(payload):
    """Tar with no file members: zero blocks, or one valid pax global header then zeros.

    Git archive of a committed empty tree is a pax global header and end
    blocks. Python 3.14 tarfile rejects that sequence with ReadError because
    the header has no following member. A header whose checksum does not match
    is a corrupt archive, not that empty export.
    """
    if not payload or len(payload) % 512:
        return False
    if payload.strip(b'\0') == b'':
        return True
    header = payload[:512]
    if header[156:157] != b'g' or not _ustar_checksum_matches(header):
        return False
    size_field = header[124:136].split(b'\0', 1)[0].strip()
    try:
        size = int(size_field or b'0', 8)
    except ValueError:
        return False
    if size < 0:
        return False
    body = 512 + ((size + 511) // 512) * 512
    return body <= len(payload) and payload[body:] == b'\0' * (len(payload) - body)


class GitError(RuntimeError):
    pass


class IntegrationBlocked(GitError):
    pass


@dataclass(frozen=True)
class Candidate:
    commit: str
    tree: str
    base: str
    ref: str


def crash(point):
    """Fault injection is controller-only; sandbox environments never inherit it."""
    if os.environ.get('FORGE_TEST_CRASH_AT') == point:
        os.kill(os.getpid(), signal.SIGKILL)


class Git:
    def __init__(self, target, state, branch='main'):
        self.target = Path(target).resolve(strict=True)
        self.root = Path(state).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.git_dir = self.target / '.git'
        if not self.git_dir.is_dir() or self.git_dir.is_symlink():
            raise GitError('target requires an operator-trusted ordinary .git directory')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]*', branch) or '..' in branch or branch.endswith('/'):
            raise GitError('invalid branch')
        self.branch = 'refs/heads/' + branch
        (self.root / 'idx').mkdir(exist_ok=True)
        # Attributes can launch clean/merge drivers. Snapshot and merge must read
        # no target-selected attributes, even when trusted config defines a driver.
        for filename in ('attributes', 'exclude'):
            policy = self.git_dir / 'info' / filename
            if policy.exists() and any(line.strip() and not line.lstrip().startswith('#')
                                       for line in policy.read_text().splitlines()):
                raise GitError(f'config_block: target info/{filename} must contain no active rules')
        index = self.root / 'idx' / ('empty-' + uuid.uuid4().hex)
        self.run('read-tree', '--empty', index=index)
        self.empty_tree = self.text('write-tree', index=index)
        index.unlink(missing_ok=True)

    def run(self, command, *args, work=None, index=None, data=None, check=True, timestamp=None):
        if command not in ALLOWED:
            raise GitError(f'git operation forbidden: {command}')
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': str(self.root),
               'GIT_DIR': str(self.git_dir), 'GIT_CONFIG_NOSYSTEM': '1',
               'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0', 'GIT_ATTR_NOSYSTEM': '1',
               'GIT_INDEX_FILE': str(index or self.root / 'idx' / 'unused'),
               'GIT_AUTHOR_NAME': 'Forge', 'GIT_AUTHOR_EMAIL': 'forge@localhost',
               'GIT_COMMITTER_NAME': 'Forge', 'GIT_COMMITTER_EMAIL': 'forge@localhost'}
        if command in ('add', 'merge-tree'):
            env['GIT_ATTR_SOURCE'] = self.empty_tree
        if timestamp is not None:
            env['GIT_AUTHOR_DATE'] = env['GIT_COMMITTER_DATE'] = f'@{timestamp} +0000'
        argv = ['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                '-c', 'core.excludesFile=/dev/null', '-c', 'core.attributesFile=/dev/null',
                '-c', 'core.autocrlf=false', '-c', 'core.fileMode=true', '-c', 'core.symlinks=true',
                '-c', 'core.sparseCheckout=false', '-c', 'merge.default=text', '-c', 'commit.gpgSign=false']
        if work is not None:
            argv += ['--work-tree=' + str(work)]
        proc = subprocess.run([*argv, command, *map(str, args)], cwd=self.root,
                              env=env, input=data, capture_output=True)
        if check and proc.returncode:
            raise GitError(proc.stderr.decode(errors='replace'))
        return proc

    def text(self, command, *args, **kwargs):
        return self.run(command, *args, **kwargs).stdout.decode().strip()

    def head(self):
        return self.text('rev-parse', '--verify', self.branch)

    def tree(self, commit):
        if not OID.fullmatch(commit):
            raise GitError('expected immutable object id')
        return self.text('rev-parse', commit + '^{tree}')

    def files(self, commit):
        entries = {}
        for entry in self.run('ls-tree', '-rz', commit).stdout.split(b'\0'):
            if entry:
                metadata, name = entry.split(b'\t', 1)
                mode, kind, oid = metadata.decode().split()
                if kind != 'blob':
                    raise GitError('gitlinks are unsupported')
                entries[os.fsdecode(name)] = (mode, oid)
        return entries

    def blob(self, oid):
        return self.run('cat-file', 'blob', oid).stdout

    def changed(self, base, candidate):
        return [os.fsdecode(p) for p in self.run('diff', '--no-ext-diff', '--name-only', '-z', '--no-renames', base, candidate).stdout.split(b'\0') if p]

    def snapshot(self, work, base):
        """Copy content without git metadata before git add can inspect nested repos."""
        with tempfile.TemporaryDirectory(dir=self.root / 'idx') as temporary:
            temporary = Path(temporary)
            shadow = temporary / 'tree'
            shutil.copytree(work, shadow, symlinks=True, ignore=lambda p, names: ['.git'] if '.git' in names else [])
            index = temporary / 'index'
            self.run('read-tree', base, index=index)
            self.run('add', '-A', '--', '.', work=shadow, index=index)
            return self.text('write-tree', index=index)

    def candidate(self, task, work, base, message='Implement task', tree=None):
        if not ID.fullmatch(task):
            raise GitError('invalid task id')
        tree = tree or self.snapshot(work, base)
        stamp = int(time.time())
        # Git inherits neither agent config nor ambient identity/date variables.
        body = f'{message}\n\nForge-Task: {task}\nForge-Tree: {tree}\nForge-Snapshot: {stamp}\n'
        commit = self.text('commit-tree', tree, '-p', base, data=body.encode(), timestamp=stamp)
        ref = f'refs/forge/tasks/{task}/{uuid.uuid4().hex}'
        self.run('update-ref', ref, commit, '0' * len(commit))
        crash('after_candidate_ref')
        return Candidate(commit, tree, base, ref)

    def save_candidate(self, state, task, candidate):
        with state.transaction() as db:
            db.execute('UPDATE tasks SET candidate_commit=?,candidate_tree=?,candidate_ref=?,base_commit=?,state=? WHERE id=?',
                       (candidate.commit, candidate.tree, candidate.ref, candidate.base, 'CANDIDATE', task))
        crash('after_candidate_durable')

    def export(self, commit, destination):
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=False)
        # Archive attributes must never silently change the tested content.
        with tempfile.TemporaryFile(dir=self.root / 'idx') as archive:
            payload = self.run('archive', '--format=tar', commit).stdout
            archive.write(payload)
            archive.seek(0)
            try:
                with tarfile.open(fileobj=archive) as stream:
                    stream.extractall(destination, filter='data')
            except tarfile.TarError as exc:
                # Only a committed empty tree's header-only archive may proceed.
                # Nonempty trees, corrupt bytes and git failures stay distinct.
                empty = isinstance(exc, tarfile.ReadError) and not self.files(commit) and _committed_empty_archive(payload)
                if not empty:
                    raise GitError('invalid archive: ' + str(exc)) from exc
        if self.snapshot(destination, commit) != self.tree(commit):
            raise GitError('archive attributes changed candidate content')
        return destination

    def clone(self, base, destination):
        destination = Path(destination)
        self.run('clone', '--no-hardlinks', '--no-checkout', '--', self.target, destination)
        # Do not invoke agent-writable git even to remove origin or checkout.
        config = destination / '.git' / 'config'
        config.write_text('[core]\nrepositoryformatversion = 0\nbare = false\n')
        content = destination.parent / ('export-' + uuid.uuid4().hex)
        self.export(base, content)
        for path in content.iterdir():
            shutil.move(str(path), destination / path.name)
        content.rmdir()
        # This clone has not been exposed to an agent yet. Seed its selected
        # branch and index from the trusted controller repository, never by
        # invoking git with agent-writable metadata. HEAD/index/files must agree.
        (destination / '.git' / 'HEAD').write_text('ref: ' + self.branch + '\n')
        ref = destination / '.git' / self.branch
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_text(base + '\n')
        index = self.root / 'idx' / ('clone-' + uuid.uuid4().hex)
        try:
            self.run('read-tree', base, index=index)
            shutil.copyfile(index, destination / '.git' / 'index')
        finally:
            index.unlink(missing_ok=True)
        return destination

    def merge(self, candidate, head, task):
        proc = self.run('merge-tree', '--write-tree', '--merge-base=' + candidate.base,
                        head, candidate.commit, check=False)
        if proc.returncode:
            raise IntegrationBlocked('base_conflict: main and candidate refs preserved')
        tree = proc.stdout.decode().splitlines()[0]
        return self.candidate(task, None, head, 'Reconcile moved main', tree=tree)

    def contains(self, head, commit):
        pending, seen = [head], set()
        while pending:
            current = pending.pop()
            if current == commit:
                return True
            if current in seen:
                continue
            seen.add(current)
            header = self.text('cat-file', '-p', current).split('\n\n', 1)[0]
            pending.extend(line[7:] for line in header.splitlines() if line.startswith('parent '))
        return False

    def cas(self, candidate):
        return self.run('update-ref', self.branch, candidate.commit, candidate.base, check=False).returncode == 0


class Integrator:
    """Must run under controller_lock. Gate/review receipts come from Gate only."""
    def __init__(self, git, state, gate, max_rebases=2):
        self.git, self.state, self.gate = git, state, gate
        self.max_rebases = max_rebases

    def _blocked(self, task, candidate, cause):
        record = {'cause': cause, 'outcome': 'task_fail',
                  'last_calls': [row[0] for row in self.state.db.execute('SELECT id FROM calls WHERE task=? ORDER BY started DESC LIMIT 10', (task,))],
                  'evidence_paths': [row[0] for row in self.state.db.execute('SELECT path FROM artifacts WHERE task=? ORDER BY created', (task,))], 'preserved': {'ref': candidate.ref, 'tree': candidate.tree,
                  'workspace': self.state.db.execute('SELECT workspace FROM tasks WHERE id=?', (task,)).fetchone()[0]},
                  'unblock_condition': 'resolve main conflict or unblock with renewed rebase budget'}
        with self.state.transaction() as db:
            db.execute('UPDATE tasks SET state=?,blocked=? WHERE id=?', ('BLOCKED', json.dumps(record), task))
        raise IntegrationBlocked(cause)

    def _done(self, task):
        row = self.state.db.execute('SELECT candidate,tree FROM commits WHERE task=?', (task,)).fetchone()
        # Notes do not change the candidate tree. Recovery adds a missing note,
        # and an existing note is left intact, avoiding duplicate note commits.
        note = self.git.run('notes', '--ref=forge', 'show', row['candidate'], check=False)
        if note.returncode:
            evidence = []
            for artifact in self.state.db.execute("SELECT path,sha256,kind FROM artifacts WHERE task=? AND kind IN ('gate','review')", (task,)):
                value = json.loads(Path(artifact['path']).read_text())
                if value['candidate']['commit'] == row['candidate']:
                    item = dict(artifact)
                    item['calls'] = [result['call'] for result in value.get('results', [])] if artifact['kind'] == 'gate' else [value['call']]
                    evidence.append(item)
            self.git.run('notes', '--ref=forge', 'add', '-m', json.dumps(
                {'task': task, 'tree': row['tree'], 'sandbox': 'bwrap', 'evidence': evidence}, sort_keys=True), row['candidate'])
        with self.state.transaction() as db:
            db.execute("UPDATE commits SET state='done' WHERE task=?", (task,))
            db.execute("UPDATE tasks SET state='COMMITTED' WHERE id=?", (task,))

    def accept(self, task, candidate, receipt, revalidate=None):
        while True:
            self.gate.verify_receipt(task, candidate, receipt)
            review_evidence = json.loads(Path(receipt.path).read_text())
            gate_evidence = json.loads(Path(review_evidence['gate']['path']).read_text())
            gate_call = gate_evidence['results'][-1]['call']
            with self.state.transaction() as db:
                existing = db.execute('SELECT * FROM commits WHERE task=?', (task,)).fetchone()
                if existing and existing['state'] == 'done':
                    return existing['candidate']
                if db.execute("SELECT 1 FROM cancels WHERE target IN (?, 'all', '*') AND handled IS NULL", (task,)).fetchone() and not existing:
                    db.execute("UPDATE tasks SET state='CANCELLED' WHERE id=?", (task,))
                    return None
                head = self.git.head()
                if existing and self.git.contains(head, existing['candidate']):
                    already_accepted = True
                else:
                    already_accepted = False
                if head == candidate.base:
                    db.execute("INSERT INTO commits(task,candidate,tree,old_head,state,accepted_at) VALUES(?,?,?,?,'pending',?) ON CONFLICT(task) DO UPDATE SET candidate=excluded.candidate,tree=excluded.tree,old_head=excluded.old_head,state='pending'",
                               (task, candidate.commit, candidate.tree, candidate.base, time.time()))
                    db.execute('UPDATE commits SET gate_call=?,review_call=? WHERE task=?',
                               (gate_call, review_evidence['call'], task))
                else:
                    db.execute("UPDATE tasks SET state='REBASING' WHERE id=?", (task,))
            if already_accepted:
                self._done(task)
                return existing['candidate']
            if head != candidate.base:
                candidate, receipt = self._rebase(task, candidate, head, revalidate)
                continue
            crash('after_pending')
            if not self.git.cas(candidate):
                candidate, receipt = self._rebase(task, candidate, self.git.head(), revalidate)
                continue
            crash('after_cas')
            self._done(task)
            return candidate.commit

    def _rebase(self, task, candidate, head, revalidate):
        row = self.state.db.execute('SELECT * FROM tasks WHERE id=?', (task,)).fetchone()
        if row['rebases'] >= self.max_rebases:
            self._blocked(task, candidate, 'rebase budget exhausted')
        try:
            merged = self.git.merge(candidate, head, task)
        except IntegrationBlocked:
            self._blocked(task, candidate, 'base_conflict')
        data = json.loads(row['metadata'])
        work = Path(row['workspace'])
        token = uuid.uuid4().hex
        data['rebase_sync'] = {'candidate': asdict(merged),
                               'staging': str(work.parent / ('rebase-stage-' + token)),
                               'backup': str(work.parent / ('rebase-preserved-' + token))}
        with self.state.transaction() as db:
            db.execute("UPDATE tasks SET candidate_commit=?,candidate_tree=?,candidate_ref=?,base_commit=?,state='REBASING',rebases=rebases+1,metadata=? WHERE id=?",
                       (merged.commit, merged.tree, merged.ref, merged.base, json.dumps(data), task))
        crash('after_rebase_intent')
        self._sync_rebase(task)
        if revalidate is None:
            raise IntegrationBlocked('new candidate needs gate and fresh independent review')
        return merged, revalidate(merged)

    def _sync_rebase(self, task):
        """Replay the controller-owned clone replacement before another writer."""
        row = self.state.db.execute('SELECT * FROM tasks WHERE id=?', (task,)).fetchone()
        data = json.loads(row['metadata'])
        intent = data['rebase_sync']
        merged = Candidate(**intent['candidate'])
        work, stage, backup = Path(row['workspace']), Path(intent['staging']), Path(intent['backup'])
        if not backup.exists():
            # An interrupted clone is never exposed to an agent or trusted as Git.
            if stage.exists():
                shutil.rmtree(stage)
            self.git.clone(merged.commit, stage)
            crash('after_rebase_clone')
            work.rename(backup)
            crash('after_rebase_backup')
        if not work.exists():
            stage.rename(work)
        crash('after_rebase_workspace')
        if self.git.snapshot(work, merged.commit) != merged.tree:
            raise GitError('rebased workspace differs from merged candidate')
        data.pop('rebase_sync')
        for role in ('review', 'fix'):
            data.get('operations', {}).pop(role, None)
        with self.state.transaction() as db:
            db.execute("UPDATE tasks SET state='GATING',metadata=? WHERE id=?", (json.dumps(data), task))

    def recover(self, revalidate=None):
        for task in self.state.db.execute('SELECT id,metadata FROM tasks').fetchall():
            if 'rebase_sync' in json.loads(task['metadata']):
                self._sync_rebase(task['id'])
        blocked = None
        for row in self.state.db.execute("SELECT * FROM commits WHERE state='pending'").fetchall():
            task = self.state.db.execute('SELECT candidate_ref,candidate_commit FROM tasks WHERE id=?', (row['task'],)).fetchone()
            candidate = Candidate(row['candidate'], row['tree'], row['old_head'], task['candidate_ref'])
            head = self.git.head()
            if self.git.contains(head, candidate.commit):
                self._done(row['task'])
            elif task['candidate_commit'] != candidate.commit:
                # Superseded by a recorded rebase: the row stays the acceptance
                # point, but only the re-gated merged candidate may replace it.
                continue
            elif head == candidate.base and self.git.cas(candidate):
                self._done(row['task'])
            else:
                try:
                    candidate, receipt = self._rebase(row['task'], candidate, self.git.head(), revalidate)
                    self.accept(row['task'], candidate, receipt, revalidate)
                except IntegrationBlocked as error:
                    blocked = blocked or error  # Other pending tasks still recover.
        if blocked:
            raise blocked
