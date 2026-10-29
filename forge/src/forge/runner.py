"""Launch, record, stream and kill sandboxed native calls (§5.1).

Order, each step durable before the next: calls row (launching, preset SID) →
spawn bwrap in its own session → pid/pgid/proc_start (running) → stdout streamed to
calls/<id>/stdout with session_out persisted on the first SID event → result.json
(atomic) → calls row done/outcome. bwrap runs --unshare-pid --die-with-parent, so the
sandboxed tree dies with bwrap and bwrap dies with the controller.
"""

from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time

from forge import sandbox
from forge.adapters.base import Call, final_json
from forge.classify import Facts, Outcome, classify
from forge.state import State, atomic_json

KILL_GRACE = 10.0


@dataclass
class Result:
    call_id: str
    outcome: str
    row: int
    sid: str | None
    actual_model: str | None
    model_match: bool | None
    final_text: str | None
    final_json: dict | None
    error_fields: dict
    usage: dict
    raw_dir: str
    recheck_at: float | None
    rc: int | None
    effects: dict
    native_metadata: dict = field(default_factory=dict)


def proc_start(pid: int) -> str:
    """Field 22 (starttime) of /proc/<pid>/stat; the comm field may contain spaces."""
    data = Path(f'/proc/{pid}/stat').read_text()
    return data[data.rindex(')') + 2:].split()[19]


def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def terminate_group(proc: subprocess.Popen, grace=KILL_GRACE):
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 5.0)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            break
        try:
            proc.wait(wait)
            break
        except subprocess.TimeoutExpired:
            continue


def run_call(state: State, call: Call, adapter, spec_for, *, calls_root: Path, account: str,
             validate=None, should_cancel=lambda: False, task=None, batch=None, run=None, binary_info=None, now=time.time) -> Result:
    """Run one native call. `spec_for(exe_args)` returns a validated sandbox Spec for argv.

    `validate(final_json) -> bool` applies the role schema; default requires an object.
    """
    raw_dir = Path(calls_root) / call.id
    in_dir = raw_dir / 'in'
    in_dir.mkdir(parents=True, mode=0o700)
    # Persist exact model input independently of adapter stdin/file conventions.
    atomic_json(raw_dir / 'input.json', {'role': call.role, 'prompt': call.prompt_text,
                'images': [{'path': str(p), 'content_sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in call.images]})
    started = now()
    with state.transaction() as db:
        db.execute('INSERT OR IGNORE INTO accounts(id) VALUES(?)', (account,))
        db.execute('INSERT INTO calls(id, task, role, profile, account, model, effort, binary_path, binary_version, '
                   'preset_sid, session_in, status, started, raw_dir, batch, run) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (call.id, task, call.role, call.profile, account, call.model, call.effort,
                    (binary_info or {}).get('path'), (binary_info or {}).get('version'), call.preset_sid,
                    call.resume_sid, 'launching', started, str(raw_dir), batch, run))
    from forge.gitops import crash
    crash('runner_after_launching')
    facts = {}
    native = None
    rc = None
    stdout_path, stderr_path = raw_dir / 'stdout', raw_dir / 'stderr'
    try:
        exe_alias = spec_for(()).command[0]
        native_argv, stdin_bytes = adapter.build_argv(call, exe_alias, in_dir)
        spec = spec_for(native_argv[1:])
        sandbox.revalidate(spec)
        full_argv = sandbox.argv(spec)
    except (sandbox.SandboxError, ValueError, OSError) as exc:
        facts = {'sandbox_error': str(exc)}
        spec, full_argv = None, None
    if spec is not None:
        manifest = {**spec.manifest(), 'argv': full_argv}
        if call.children is not None:
            from forge.native_children import policy_hash
            manifest['children'] = {'policy': asdict(call.children), 'sha256': policy_hash(call.children),
                                    'capability': call.child_capability}
        atomic_json(raw_dir / 'manifest.json', manifest)
        with state.transaction() as db:
            db.execute('UPDATE calls SET argv_sha=?, sandbox=?, manifest_path=? WHERE id=?',
                       (hashlib.sha256(json.dumps(full_argv).encode()).hexdigest(), spec.label,
                        str(raw_dir / 'manifest.json'), call.id))
        rc, facts, native = _spawn_and_stream(state, call, adapter, full_argv, stdin_bytes, stdout_path,
                                              stderr_path, should_cancel)
    stderr = stderr_path.read_text(errors='replace') if stderr_path.exists() else ''
    stdout_size = stdout_path.stat().st_size if stdout_path.exists() else 0
    parsed = final_json(native.final_text) if native else None
    try:
        valid = parsed is not None and (validate(parsed) if validate else True)
    except (TypeError, ValueError, KeyError, AttributeError):
        # Malformed model values are protocol failures, never controller crashes.
        valid = False
    error_fields = dict(native.error_fields) if native else {'stderr': stderr}
    for key in ('sandbox_error', 'spawn_error'):
        if facts.get(key):
            error_fields[key] = facts[key]
    k = _account_k(state, account)
    outcome: Outcome = classify(Facts(
        cancelled=facts.get('cancelled', False), spawn_error=facts.get('spawn_error'),
        binary_mismatch=bool(binary_info and binary_info.get('mismatch')), sandbox_error=facts.get('sandbox_error'),
        timed_out=facts.get('timed_out', False), rc=rc, stdout_bytes=stdout_size,
        native_status=native.status if native else 'absent', final_valid=valid,
        model_match=native.model_match if native else None, turn_limit=native.turn_limit if native else False,
        error_fields=error_fields, stderr=stderr), now(), k)
    result = Result(call.id, outcome.outcome, outcome.row, native.sid if native else None,
                    native.actual_model if native else None, native.model_match if native else None,
                    native.final_text if native else None, parsed, error_fields,
                    native.usage if native else {}, str(raw_dir), outcome.recheck_at, rc, asdict(outcome),
                    dict(native.native_metadata) if native else {})
    atomic_json(raw_dir / 'result.json', asdict(result))
    crash('runner_after_result_file')
    with state.transaction() as db:
        db.execute('UPDATE calls SET status=?, outcome=?, ended=?, actual_model=?, session_out=COALESCE(session_out, ?), '
                   'result_path=?, recheck_at=?, usage=? WHERE id=?',
                   ('done', outcome.outcome, now(), result.actual_model, result.sid, str(raw_dir / 'result.json'),
                    outcome.recheck_at, json.dumps(result.usage, default=str), call.id))
    crash('runner_after_done')
    return result


def _account_k(state: State, account: str) -> int:
    row = state.db.execute('SELECT k FROM accounts WHERE id=?', (account,)).fetchone()
    return row['k'] if row else 0


def _spawn_and_stream(state, call, adapter, full_argv, stdin_bytes, stdout_path, stderr_path, should_cancel):
    facts, events = {}, []
    with open(stdout_path, 'wb') as out, open(stderr_path, 'wb') as err:
        try:
            proc = subprocess.Popen(full_argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                                    start_new_session=True, close_fds=True)
        except OSError as exc:
            return None, {'spawn_error': f'{type(exc).__name__}: {exc}'}, None
        from forge.gitops import crash
        crash('runner_after_spawn')
        try:
            started = proc_start(proc.pid)
        except OSError:
            started = 'exited'
        with state.transaction() as db:
            db.execute('UPDATE calls SET pid=?, pgid=?, proc_start=?, status=? WHERE id=?',
                       (proc.pid, proc.pid, started, 'running', call.id))
            if getattr(call, 'observation', None):
                # Loop-only metering: a proposed continuation, finalized only on positive native evidence.
                from forge import quota_metrics
                quota_metrics.record_process_start(db, call.observation, call.id, call.observation.get('clock', time.time)())
        crash('runner_after_pid')
        try:
            proc.stdin.write(stdin_bytes)
        except BrokenPipeError:
            pass
        proc.stdin.close()
        deadline = time.monotonic() + call.timeout
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        buffer, captured, sid_saved = b'', bytearray(), call.preset_sid is not None
        if sid_saved:
            with state.transaction() as db:
                db.execute('UPDATE calls SET session_out=? WHERE id=? AND session_out IS NULL',
                           (call.resume_sid or call.preset_sid, call.id))
        while True:
            if should_cancel():
                facts['cancelled'] = True
                terminate_group(proc)
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                facts['timed_out'] = True
                terminate_group(proc)
                break
            ready = selector.select(min(remaining, 0.5))
            if not ready:
                continue
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                break
            out.write(chunk)
            out.flush()
            captured += chunk
            buffer += chunk
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                event = adapter.parse_line(line.decode('utf-8', 'replace'))
                if event is None:
                    continue
                events.append(event)
                if event.kind == 'sid' and not sid_saved:
                    os.fsync(out.fileno())
                    with state.transaction() as db:
                        db.execute('UPDATE calls SET session_out=? WHERE id=?', (event.data['sid'], call.id))
                    sid_saved = True
                    crash('runner_after_sid')
        if buffer:
            event = adapter.parse_line(buffer.decode('utf-8', 'replace'))
            if event is not None:
                events.append(event)
        selector.close()
        rc = proc.wait()
        os.fsync(out.fileno())
        err.flush()
    stderr = stderr_path.read_text(errors='replace')
    return rc, facts, adapter.finish(call, rc, events, bytes(captured), stderr)


def recover_calls(state: State, *, grace=KILL_GRACE):
    """Kill positively identified surviving groups before allowing a new writer.

    A reused PID is never signalled. A group whose leader identity cannot be
    proved is refused while it has live members. Zombies cannot write and do
    not prevent recovery on hosts whose PID 1 delays reaping adopted children.
    """
    def live_members(pgid):
        members = []
        for path in Path('/proc').iterdir():
            if not path.name.isdigit():
                continue
            try:
                data = (path / 'stat').read_text()
                fields = data[data.rindex(')') + 2:].split()
                if int(fields[2]) == pgid and fields[0] != 'Z':
                    members.append(int(path.name))
            except (OSError, ValueError):
                continue
        return members

    rows = state.db.execute("SELECT * FROM calls WHERE status IN ('launching','running')").fetchall()
    for row in rows:
        pgid = row['pgid']
        if pgid and live_members(pgid):
            try:
                identity = proc_start(row['pid']) == row['proc_start']
            except OSError:
                identity = False
            if not identity:
                raise RuntimeError(f"env_block: cannot identify surviving writer group {pgid}")
            for sig, duration in ((signal.SIGTERM, grace), (signal.SIGKILL, 5.0)):
                try:
                    os.killpg(pgid, sig)
                except ProcessLookupError:
                    break
                deadline = time.monotonic() + duration
                while live_members(pgid) and time.monotonic() < deadline:
                    time.sleep(0.05)
                if not live_members(pgid):
                    break
            if live_members(pgid):
                raise RuntimeError(f"env_block: surviving writer pid {row['pid']}")
        with state.transaction() as db:
            db.execute("UPDATE calls SET status='interrupted',outcome='interrupted',ended=? WHERE id=?",
                       (time.time(), row['id']))
