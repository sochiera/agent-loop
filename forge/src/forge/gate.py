"""Deterministic candidate gates and provenance-bound review/RED receipts.

Target commands execute only in bwrap. Receipts are controller artifacts, never
files supplied by a target or a model; prose PASS cannot create an acceptance.
"""
from dataclasses import dataclass
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET

from forge import sandbox
from forge.gitops import ID
from forge.state import atomic_json, atomic_write
from forge.runner import proc_start, terminate_group

CHECKLIST = frozenset({'intent_match', 'adr_consistent', 'docs_updated', 'tests_meaningful',
                       'no_dead_code', 'naming_clear', 'error_handling', 'no_scope_creep', 'regression_first'})
NODE = re.compile(r'^[A-Za-z0-9_./-]+(::[A-Za-z0-9_\[\]./-]+)*$')
SECTIONS = frozenset({'Plan', 'Changes', 'Tests', 'Docs', 'Evidence'})


class GateError(RuntimeError):
    pass


def matches(path, patterns):
    # A recursive glob includes zero directories (fnmatch alone requires the
    # slash in **/ and would omit docs/source/spec.md from the default pattern).
    pending, seen = list(patterns), set()
    while pending:
        pattern = pending.pop()
        if pattern in seen:
            continue
        seen.add(pattern)
        if fnmatch.fnmatchcase(path, pattern):
            return True
        for match in re.finditer(r'\*\*/', pattern):
            pending.append(pattern[:match.start()] + pattern[match.end():])
    return False


def validate_node(node, export):
    if not isinstance(node, str) or not NODE.fullmatch(node) or node.startswith('-'):
        raise GateError('invalid test node')
    path = Path(export) / node.split('::')[0]
    if not path.resolve().is_relative_to(Path(export).resolve()) or not path.is_file():
        raise GateError('test node escapes export or is missing')
    return node


def junit(data, rc, node, red):
    """One command per node; require an executed, matching assertion result."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise GateError('invalid or missing JUnit') from exc
    cases = list(root.iter('testcase'))
    if rc != (1 if red else 0) or not cases:
        raise GateError('wrong test exit or zero executed tests')
    if any(list(root.iter(tag)) for tag in ('error', 'skipped')):
        raise GateError('import/setup/error/skipped is not assertion evidence')
    parts = node.split('::')
    filename = parts[0].removesuffix('.py').replace('/', '.')
    selected = [case for case in cases if
                (case.get('classname', '') == filename or case.get('classname', '').startswith(filename + '.') or
                 case.get('file', '').removesuffix('.py') == parts[0].removesuffix('.py')) and
                (len(parts) == 1 or case.get('name') == parts[-1] or
                 case.get('name', '').startswith(parts[-1] + '['))]
    if len(parts) > 2:
        expected_class = filename + '.' + '.'.join(parts[1:-1])
        selected = [case for case in selected if case.get('classname') == expected_class]
    if not selected:
        raise GateError('declared node missing from JUnit')
    failures = list(root.iter('failure'))
    if red:
        if not any(case.find('failure') is not None and
                   ('AssertionError' in ''.join(case.find('failure').itertext()) or
                    'assert ' in ''.join(case.find('failure').itertext()) or
                    (case.find('failure').get('message', '').startswith('Failed: DID NOT RAISE ') and
                     'with pytest.raises(' in ''.join(case.find('failure').itertext()))) for case in selected):
            raise GateError('RED requires an assertion failure for declared node')
    elif failures:
        raise GateError('GREEN has failures')
    return len(selected)


@dataclass(frozen=True)
class Receipt:
    path: str
    sha256: str


class Gate:
    def __init__(self, git, state, roots, commands, *, protected=None, test_paths=('tests/**',),
                 ro_binds=(), network=False, timeout=300, sources=('docs/source/**',), should_cancel=lambda task: False):
        self.git, self.state, self.roots = git, state, roots
        self.commands = commands
        self.should_cancel = should_cancel
        self.protected = tuple(dict.fromkeys((*sources, 'docs/forge/intent/**', 'docs/forge/adr/**', 'docs/forge/digests/**', *(protected or ()))))
        self.test_paths = tuple(test_paths)
        self.ro_binds, self.network, self.timeout = ro_binds, network, timeout

    def _record(self, task, kind, value):
        if not ID.fullmatch(task):
            raise GateError('invalid task id')
        directory = self.state.root / 'evidence' / task
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (kind + '-' + uuid.uuid4().hex + '.json')
        atomic_json(path, value)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.state.transaction() as db:
            db.execute('INSERT INTO artifacts(path,sha256,task,kind,created) VALUES(?,?,?,?,?)',
                       (str(path), sha, task, kind, time.time()))
        return Receipt(str(path), sha)

    def _load(self, task, receipt, kind):
        row = self.state.db.execute('SELECT * FROM artifacts WHERE path=? AND task=? AND kind=?',
                                    (receipt.path, task, kind)).fetchone()
        if row is None or row['sha256'] != receipt.sha256:
            raise GateError('unregistered evidence')
        path = Path(receipt.path)
        if not path.resolve().is_relative_to(self.state.root / 'evidence') or path.is_symlink():
            raise GateError('evidence containment')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != receipt.sha256:
            raise GateError('evidence changed')
        return json.loads(data)

    def _export(self, task, candidate, kind):
        if not ID.fullmatch(task):
            raise GateError('invalid task id')
        parent = self.state.root / 'ws' / task
        parent.mkdir(parents=True, exist_ok=True)
        # Unique increasing export names satisfy the exact sandbox leaf contract.
        n = time.time_ns()
        return self.git.export(candidate.commit, parent / f'{kind}-{n}')

    def command(self, task, work, command, *, node=None):
        call = uuid.uuid4().hex
        directory = self.state.root / 'calls' / call
        directory.mkdir(parents=True)
        output = work / ('.forge-junit-' + call + '.xml')
        if output.exists() or output.is_symlink():
            raise GateError('JUnit path collision')
        if node is not None:
            validate_node(node, work)
            if not any('{node}' in arg for arg in command):
                raise GateError('config_block: test_one requires {node}')
            if not any('{junit}' in arg for arg in command):
                raise GateError('config_block: test_one requires {junit}')
        argv = [arg.replace('{node}', node or '').replace('{junit}', '/work/' + output.name) for arg in command]
        spec = sandbox.gate_spec(self.roots, command=argv, work=work,
                                 ro_binds=self.ro_binds, network=self.network)
        manifest = directory / 'manifest.json'
        atomic_json(manifest, spec.manifest())
        sandbox.revalidate(spec)
        with self.state.transaction() as db:
            db.execute("INSERT INTO calls(id,task,role,profile,status,started,raw_dir,sandbox,manifest_path) VALUES(?,?,'gate','deterministic','launching',?,?,'bwrap',?)",
                       (call, task, time.time(), str(directory), str(manifest)))
        started = time.monotonic()
        try:
            process = subprocess.Popen(sandbox.argv(spec), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, start_new_session=True)
        except OSError as exc:
            atomic_json(directory / 'result.json', {'outcome': 'env_block', 'error': str(exc)})
            with self.state.transaction() as db:
                db.execute("UPDATE calls SET status='done',outcome='env_block',ended=? WHERE id=?", (time.time(), call))
            raise sandbox.SandboxError('env_block: gate spawn failed') from exc
        try:
            identity = proc_start(process.pid)
        except OSError:
            identity = 'exited'
        with self.state.transaction() as db:
            db.execute("UPDATE calls SET pid=?,pgid=?,proc_start=?,status='running' WHERE id=?",
                       (process.pid, process.pid, identity, call))
        timed_out = cancelled = False
        deadline = time.monotonic() + self.timeout
        while True:
            if self.should_cancel(task):
                cancelled = True
                terminate_group(process)
                stdout, stderr = process.communicate()
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                terminate_group(process)
                stdout, stderr = process.communicate()
                break
            try:
                stdout, stderr = process.communicate(timeout=min(0.25, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        atomic_write(directory / 'stdout', stdout)
        atomic_write(directory / 'stderr', stderr)
        evidence = {'call': call, 'argv': argv, 'rc': process.returncode, 'sandbox': 'bwrap',
                    'elapsed': time.monotonic() - started, 'raw_dir': str(directory)}
        atomic_json(directory / 'result.json', evidence)
        outcome = 'cancelled' if cancelled else 'timeout' if timed_out else ('ok' if process.returncode == 0 else 'task_fail')
        if process.returncode and stderr.startswith(b'bwrap:'):
            outcome = 'env_block'
        with self.state.transaction() as db:
            db.execute("UPDATE calls SET status='done',outcome=?,ended=?,result_path=? WHERE id=?",
                       (outcome, time.time(), str(directory / 'result.json'), call))
        if cancelled:
            raise GateError('gate cancelled')
        if timed_out:
            raise GateError('gate command timed out')
        if process.returncode and stderr.startswith(b'bwrap:'):
            raise sandbox.SandboxError('env_block: bwrap gate setup failed: ' + stderr.decode(errors='replace')[-1000:])
        if node is not None:
            if not output.is_file() or output.is_symlink():
                raise GateError('missing JUnit output')
            data = output.read_bytes()
            atomic_write(directory / 'junit.xml', data)
            output.unlink()
            evidence['junit'] = str(directory / 'junit.xml')
        else:
            data = None
        atomic_json(directory / 'result.json', evidence)
        if process.returncode and stderr.startswith(b'bwrap:'):
            raise sandbox.SandboxError('env_block: bwrap gate setup failed')
        return evidence, data

    def structure(self, task, candidate, doc_targets, adr_additions=None):
        changed = set(self.git.changed(candidate.base, candidate.commit))
        before, after = self.git.files(candidate.base), self.git.files(candidate.commit)
        additions = adr_additions or {}  # controller-written path -> exact blob id
        for path in changed:
            if matches(path, self.protected):
                if not (path.startswith('docs/forge/adr/') and path not in before and
                        path in after and additions.get(path) == after[path][1]):
                    raise GateError('protected path changed: ' + path)
        if not doc_targets or any(path not in changed or path not in after for path in doc_targets):
            raise GateError('every affected doc target must change and exist')
        task_doc = f'docs/forge/tasks/{task}.md'
        if task_doc not in after or task_doc not in changed:
            raise GateError('task documentation missing or unchanged')
        text = self.git.blob(after[task_doc][1]).decode()
        sections = set(re.findall(r'^#{1,6}\s+(Plan|Changes|Tests|Docs|Evidence)\s*$', text, re.M))
        if not SECTIONS <= sections:
            raise GateError('required task sections missing')

    def red(self, task, candidate, nodes):
        changed = self.git.changed(candidate.base, candidate.commit)
        if not changed or any(not matches(path, self.test_paths) for path in changed):
            raise GateError('RED checkpoint must change test paths only')
        if not nodes or len(nodes) != len(set(nodes)):
            raise GateError('RED needs distinct declared nodes')
        if any(node.split('::')[0] not in changed for node in nodes):
            raise GateError('RED nodes must belong to changed test paths')
        after = self.git.files(candidate.commit)
        if any(path not in after for path in changed):
            raise GateError('RED cannot delete tests')
        work = self._export(task, candidate, 'gate')
        results = []
        for node in nodes:
            result, data = self.command(task, work, self.commands['test_one'], node=node)
            junit(data, result['rc'], node, True)
            results.append(result)
        if self.git.snapshot(work, candidate.commit) != candidate.tree:
            raise GateError('RED command mutated tests')
        receipt = self._record(task, 'red', {'candidate': candidate.__dict__, 'nodes': nodes,
                    'blobs': {path: after[path] for path in changed}, 'results': results})
        with self.state.transaction() as db:
            db.execute('UPDATE tasks SET red_commit=? WHERE id=?', (candidate.commit, task))
        return receipt

    def require_red_before_fix(self, task, receipt):
        red = self._load(task, receipt, 'red')
        stored = self.state.db.execute('SELECT red_commit FROM tasks WHERE id=?', (task,)).fetchone()
        if stored is None or stored[0] != red['candidate']['commit']:
            raise GateError('RED evidence must be durable before fix launch')
        return red['candidate']['commit']

    def green(self, task, candidate, work, receipt):
        red = self._load(task, receipt, 'red')
        # On a rebase, RED remains proof against its original unchanged base;
        # its regression blobs must still be identical in the new candidate.
        after = self.git.files(candidate.commit)
        for path, blob in red['blobs'].items():
            if list(after.get(path, ())) != blob:
                raise GateError('GREEN requires unchanged RED test blobs')
        results = []
        for node in red['nodes']:
            result, data = self.command(task, work, self.commands['test_one'], node=node)
            junit(data, result['rc'], node, False)
            results.append(result)
        return results

    def check(self, task, candidate, doc_targets, *, red=None, adr_additions=None):
        self.structure(task, candidate, doc_targets, adr_additions)
        work = self._export(task, candidate, 'gate')
        results = []
        if not self.commands.get('test'):
            raise GateError('test command required')
        for name in ('lint', 'test'):
            if self.commands.get(name):
                result, _ = self.command(task, work, self.commands[name])
                if result['rc']:
                    raise GateError(f'{name} failed: {result["raw_dir"]}')
                results.append(result)
        kind = self.state.db.execute('SELECT kind FROM tasks WHERE id=?', (task,)).fetchone()[0]
        if kind == 'bug' and red is None:
            raise GateError('bug requires durable RED before fix')
        if red is not None:
            results += self.green(task, candidate, work, red)
        if self.git.snapshot(work, candidate.commit) != candidate.tree:
            raise GateError('gate mutated candidate')
        return self._record(task, 'gate', {'candidate': candidate.__dict__, 'results': results,
                                         'red': red.__dict__ if red else None})

    def review_export(self, task, candidate):
        return self._export(task, candidate, 'review')

    def review(self, task, candidate, gate_receipt, work, result, *, call_id,
               reviewer_family, implementer_families):
        gate = self._load(task, gate_receipt, 'gate')
        if gate['candidate'] != candidate.__dict__:
            raise GateError('gate candidate mismatch')
        call = self.state.db.execute('SELECT * FROM calls WHERE id=? AND task=?', (call_id, task)).fetchone()
        if call is None or call['role'] != 'review' or call['status'] != 'done' or call['outcome'] != 'ok' or call['session_in']:
            raise GateError('review needs a successful fresh recorded call')
        if not call['session_out'] or not reviewer_family or reviewer_family in implementer_families or not implementer_families:
            raise GateError('reviewer family/session independence')
        manifest = json.loads(Path(call['manifest_path']).read_text())
        mounts = manifest.get('mounts', [])
        if manifest.get('sandbox') != 'bwrap' or not any(m.get('class') == 'work' and
                m.get('source') == str(work) and m.get('mode') == 'ro' for m in mounts):
            raise GateError('review work must be mounted read-only')
        for row in self.state.db.execute("SELECT path FROM artifacts WHERE task=? AND kind='review'", (task,)):
            previous = json.loads(Path(row[0]).read_text())
            if previous['session'] == call['session_out']:
                raise GateError('review session already used')
        if result.get('verdict') != 'PASS' or result.get('tree') != candidate.tree:
            raise GateError('review rejected or tree mismatch')
        checklist = result.get('checklist', {})
        if set(checklist) != CHECKLIST:
            raise GateError('concrete review checklist required')
        for value in checklist.values():
            if value == 'pass':
                continue
            if not isinstance(value, dict) or value.get('status') != 'n/a' or not str(value.get('reason', '')).strip():
                raise GateError('every checklist item requires pass or n/a with reason')
        if not isinstance(result.get('findings'), list) or result['findings']:
            raise GateError('PASS cannot contain unresolved findings')
        if self.git.snapshot(work, candidate.commit) != candidate.tree:
            raise GateError('review content changed')
        return self._record(task, 'review', {'candidate': candidate.__dict__, 'gate': gate_receipt.__dict__,
                            'call': call_id, 'session': call['session_out'], 'result': result,
                            'reviewer_family': reviewer_family, 'implementer_families': list(implementer_families)})

    def verify_receipt(self, task, candidate, receipt):
        review = self._load(task, receipt, 'review')
        if review['candidate'] != candidate.__dict__:
            raise GateError('review candidate mismatch')
        gate = self._load(task, Receipt(**review['gate']), 'gate')
        if gate['candidate'] != candidate.__dict__ or self.git.tree(candidate.commit) != candidate.tree:
            raise GateError('tested/reviewed/accepted tree mismatch')
