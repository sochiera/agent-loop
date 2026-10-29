"""`forge doctor`: real sandbox probe, real native smokes, stdlib PNG generator (§5.4).

Never fixture-backed. Each smoke record pins binary realpath+version, argv, binds,
model, effort, raw output, SID timing and granted capabilities. A profile is
eligible only through such a record; configuration alone grants nothing.
"""

import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import stat
import struct
import subprocess
import time
import uuid
import zlib

from forge import config as cfg, sandbox
from forge.adapters import adapter_for
from forge.adapters.base import Call
from forge.runner import run_call
from forge.state import State, atomic_json

COLOURS = {'red': (220, 30, 30), 'green': (30, 180, 60), 'blue': (30, 60, 220), 'yellow': (240, 220, 30),
           'black': (0, 0, 0), 'white': (255, 255, 255)}


def png(width, height, pixel) -> bytes:
    """Minimal truecolour PNG; pixel(x, y) -> (r, g, b)."""
    raw = b''.join(b'\x00' + b''.join(bytes(pixel(x, y)) for x in range(width)) for y in range(height))

    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


def quadrant_png(names, size=64) -> bytes:
    """Quadrants TL, TR, BL, BR in the given distinct colour names."""
    if len(set(names)) != 4:
        raise ValueError('quadrant colours must be distinct')
    half = size // 2
    return png(size, size, lambda x, y: COLOURS[names[(y >= half) * 2 + (x >= half)]])


def _disposable(state_dir: Path, name: str) -> Path:
    path = state_dir / 'ws' / name / 'src'
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, mode=0o700)
    return path


def sandbox_probe(roots: sandbox.Roots, state_dir: Path, secret_paths=()) -> dict:
    """Run the adversarial checks in real bwrap for agent-ro, agent-rw and gate layouts."""
    state_dir = Path(state_dir)
    work = _disposable(state_dir, 'doctor-probe')
    in_dir = state_dir / 'calls' / f'probe-{uuid.uuid4().hex[:12]}' / 'in'
    in_dir.mkdir(parents=True, mode=0o700)
    secrets = [state_dir / 'forge.db', state_dir / 'calls', roots.config, *(p for _, p in roots.protected())]
    secrets += [Path(p) for p in secret_paths]
    checks = {}
    try:
        for name, writable, network in (('readonly', False, False), ('writable', True, False), ('gate', True, None)):
            if network is None:
                spec = sandbox.gate_spec(roots, command=('/usr/bin/true',), work=work, input_dir=in_dir)
            else:
                spec = sandbox.Spec('agent', (sandbox.work_mount(roots, work, writable),
                                              sandbox.input_mount(roots, in_dir)), False,
                                    sandbox.PurePosixPath(roots.home), ('/usr/bin/true',))
            rc, out, err = sandbox.run_probe(spec, secrets)
            lines = out.split()
            ok = (rc == 0 and ('WORK-RW' in lines) == writable and 'NET-DOWN' in lines
                  and not any(l.startswith(('LEAK', 'ENV-LEAK', 'PIDNS-LEAK', 'IN-WRITABLE')) for l in lines))
            checks[name] = {'ok': ok, 'rc': rc, 'stdout': out, 'stderr': err[-2000:]}
    except (sandbox.SandboxError, OSError, subprocess.SubprocessError) as exc:
        checks['error'] = {'ok': False, 'detail': str(exc)}
    finally:
        shutil.rmtree(work.parent, ignore_errors=True)
        shutil.rmtree(in_dir.parent, ignore_errors=True)
    record = {'kind': 'sandbox_probe', 'at': time.time(), 'bwrap': sandbox.BWRAP, 'checks': checks,
              'status': 'pass' if checks and all(c['ok'] for c in checks.values()) else 'fail',
              'secrets_checked': [str(p) for p in secrets]}
    (state_dir / 'smoke').mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_json(state_dir / 'smoke' / 'sandbox.json', record)
    return record


def binary_version(roots, harness, binary, runtime_dir=None) -> str:
    """Run `<native> --version` inside the sandbox (no credentials, no network)."""
    exe_mounts, exe = sandbox.executable_mounts(roots, harness, binary, runtime_dir)
    work = _disposable(roots.state, 'doctor-version')
    try:
        spec = sandbox.Spec('agent', (sandbox.work_mount(roots, work, False), *exe_mounts), False,
                            sandbox.PurePosixPath(roots.home), (exe, '--version'))
        sandbox.revalidate(spec)
        proc = subprocess.run(sandbox.argv(spec), capture_output=True, text=True, timeout=60,
                              stdin=subprocess.DEVNULL)
    finally:
        shutil.rmtree(work.parent, ignore_errors=True)
    if proc.returncode != 0:
        raise sandbox.SandboxError(f'version probe failed rc={proc.returncode}: {proc.stderr[-500:]}')
    return proc.stdout.strip()


def smoke_profile(config, state: State, roots, profile, *, probe_record) -> dict:
    """Live text/resume/read-only/image smokes for one profile (P5 runs this for real)."""
    if cfg._terra_tuple(profile):
        raise ValueError('Terra requires the reviewed single-call terra_proof helper; ordinary smoke is forbidden')
    harness = config.harness[profile.harness]
    runtime_dir = harness.get('runtime_dir')
    state_dir = Path(config.state_dir)
    record = {'profile': profile.id, 'harness': profile.harness, 'model': profile.model, 'effort': profile.effort,
              'binary': None, 'binds_rw': list(harness['binds_rw']), 'binds_ro': list(harness['binds_ro']),
              'runtime_dir': runtime_dir, 'sandbox_probe': probe_record.get('status'), 'steps': {},
              'capabilities': [], 'at': time.time()}
    if probe_record.get('status') != 'pass':
        record['status'] = 'env_block'
        return _save(state, state_dir, record)
    binary = sandbox.resolve_binary(profile.harness, harness.get('binary'))
    adapter = adapter_for(profile.harness)
    version = binary_version(roots, profile.harness, binary, runtime_dir)
    info = {'path': str(binary), 'version': version, 'sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}
    record['binary'] = info
    work = _disposable(state_dir, f'doctor-{profile.id}')
    nonce = str(random.randrange(10 ** 8, 10 ** 9))
    preset = str(uuid.uuid4()) if profile.harness in ('claude', 'grok') else None

    def call(step, prompt, writable, resume=None, images=()):
        c = Call(f'smoke-{profile.id}-{step}-{uuid.uuid4().hex[:8]}', 'probe', profile.id, profile.harness,
                 profile.model, profile.effort, work, writable, prompt, tuple(images), resume,
                 None if resume else (str(uuid.uuid4()) if preset and step != 'text' else preset),
                 config.timeouts['probe'] * 3, config.max_turns['probe'] + 4)

        def spec_for(native_args):
            return sandbox.agent_spec(roots, harness=profile.harness, binary=binary, native_args=tuple(native_args),
                                      work=work, writable=writable, input_dir=state_dir / 'calls' / c.id / 'in',
                                      binds_rw=harness['binds_rw'], binds_ro=harness['binds_ro'],
                                      runtime_dir=runtime_dir)
        t0 = time.time()
        result = run_call(state, c, adapter, spec_for, calls_root=state_dir / 'calls', account=profile.account,
                          binary_info=info)
        record['steps'][step] = {'call': c.id, 'outcome': result.outcome, 'sid': result.sid,
                                 'actual_model': result.actual_model, 'model_match': result.model_match,
                                 'answer': result.final_json, 'raw_dir': result.raw_dir,
                                 'elapsed': round(time.time() - t0, 3), 'recheck_at': result.recheck_at}
        if result.recheck_at is not None:
            with state.transaction() as db:
                db.execute("UPDATE accounts SET state='waiting',recheck_at=?,outcome=?,reason=?,k=k+1 WHERE id=?",
                           (result.recheck_at, result.outcome, json.dumps(result.error_fields), profile.account))
        return result

    answer = 'Reply with only a fenced json block: {"nonce": "%s"}'
    text = call('text', answer % nonce, False)
    if text.outcome != 'ok':
        record['status'] = text.outcome
        return _save(state, state_dir, record)
    if text.outcome == 'ok' and (text.final_json or {}).get('nonce') == nonce:
        record['capabilities'].append('text')
        resumed = call('resume', 'Write the nonce from your previous turn into /work/resume-file.txt (only the digits), '
                       'then reply with only a fenced json block {"nonce": "<that nonce>"}.', True, resume=text.sid)
        file = work / 'resume-file.txt'
        if (resumed.outcome == 'ok' and resumed.sid == text.sid and (resumed.final_json or {}).get('nonce') == nonce
                and file.is_file() and file.read_text().strip() == nonce):
            record['capabilities'].append('resume')
    if any(s['recheck_at'] is not None for s in record['steps'].values()):
        record['status'] = resumed.outcome
        return _save(state, state_dir, record)
    readonly = call('readonly', 'Create the file /work/readonly-probe.txt containing "x". Then reply with only a '
                    'fenced json block {"created": true|false}.', False)
    if readonly.outcome in ('ok', 'unknown_fail') and not (work / 'readonly-probe.txt').exists():
        record['capabilities'].append('readonly')
    if readonly.recheck_at is not None:
        record['status'] = readonly.outcome
        return _save(state, state_dir, record)
    names = random.sample(sorted(COLOURS), 4)
    record['image_challenge'] = {'top_left': names[0], 'bottom_right': names[3]}
    image = state_dir / 'smoke' / f'{uuid.uuid4().hex}.png'
    image.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    image.write_bytes(quadrant_png(names))
    seen = call('image', 'Look at the attached image. Reply with only a fenced json block '
                '{"top_left": "<colour>", "bottom_right": "<colour>"} using one of: ' + ', '.join(sorted(COLOURS)),
                False, images=[image])
    if seen.recheck_at is not None:
        record['status'] = seen.outcome
        return _save(state, state_dir, record)
    got = seen.final_json or {}
    if seen.outcome == 'ok' and str(got.get('top_left', '')).lower() == names[0] and \
            str(got.get('bottom_right', '')).lower() == names[3]:
        record['capabilities'].append('image')
    record['status'] = 'pass' if 'text' in record['capabilities'] and 'readonly' in record['capabilities'] else 'fail'
    return _save(state, state_dir, record)


def _save(state, state_dir, record):
    path = state_dir / 'smoke' / f"{record['profile']}.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_json(path, record)
    fingerprint = hashlib.sha256(json.dumps({k: record[k] for k in ('binary', 'model', 'effort', 'binds_rw',
                                                                     'binds_ro', 'runtime_dir')},
                                            sort_keys=True).encode()).hexdigest()
    with state.transaction() as db:
        db.execute('INSERT OR REPLACE INTO smoke(profile, fingerprint, status, at, capabilities, manifest_path) '
                   'VALUES(?,?,?,?,?,?)', (record['profile'], fingerprint, record['status'], record['at'],
                                          json.dumps(record['capabilities']), str(path)))
    record['fingerprint'] = fingerprint
    return record


def doctor(config, *, smoke=False, profile_id=None) -> int:
    """Exit 0 all pass; 1 some fail; 75 every failure is a wait; 2 sandbox route blocked."""
    roots = sandbox.project_roots(config)
    state_dir = Path(config.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    probe = sandbox_probe(roots, state_dir)
    print(json.dumps({'sandbox_probe': probe['status'],
                      'checks': {k: v['ok'] for k, v in probe['checks'].items()}}, sort_keys=True))
    if probe['status'] != 'pass':
        return 2
    if not smoke:
        return 0
    profiles = [p for p in config.profiles if p.enabled and config.accounts[p.account]['enabled'] and (profile_id is None or p.id == profile_id)]
    if not profiles:
        print(json.dumps({"error": "no enabled matching profile"}))
        return 2
    with State(state_dir) as state:
        from forge.state import controller_lock
        with controller_lock(state_dir):
            return _profiles(config, state, roots, probe, profiles)


def _profiles(config, state, roots, probe, profiles):
    statuses = []
    for profile in profiles:
        waiting = state.db.execute("SELECT * FROM accounts WHERE id=? AND state='waiting' AND recheck_at>?",
                                   (profile.account, time.time())).fetchone()
        if waiting:
            statuses.append(('wait', {waiting['outcome']}))
            print(json.dumps({'profile': profile.id, 'status': waiting['outcome'], 'recheck_at': waiting['recheck_at'], 'account': profile.account}))
            continue
        try:
            record = smoke_profile(config, state, roots, profile, probe_record=probe)
        except (sandbox.SandboxError, OSError, ValueError) as exc:
            h = config.harness[profile.harness]
            record = _save(state, Path(config.state_dir), {'profile': profile.id, 'status': 'env_block',
                'error': str(exc), 'binary': None, 'model': profile.model, 'effort': profile.effort,
                'binds_rw': list(h['binds_rw']), 'binds_ro': list(h['binds_ro']), 'runtime_dir': h.get('runtime_dir'),
                'at': time.time(), 'capabilities': [], 'steps': {}, 'sandbox_probe': probe['status']})
        outcomes = {s['outcome'] for s in record.get('steps', {}).values()} - {'ok'}
        statuses.append((record['status'], outcomes))
        print(json.dumps({'profile': profile.id, 'status': record['status'],
                          'capabilities': record.get('capabilities', [])}, sort_keys=True))
    if all(s == 'pass' for s, _ in statuses):
        return 0
    failed = [o for s, o in statuses if s != 'pass']
    waits = {'quota_wait', 'auth_wait', 'env_wait'}
    return 75 if failed and all(o and o <= waits for o in failed) else 1


TERRA_DENIALS = {'model_unavailable', 'model_not_found', 'unsupported_model'}


def _terra_stop(state, reason, **extra):
    return {'state': state, 'reason': reason, **extra}


def _sha_file(path: Path) -> str | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return None
            return hashlib.sha256(stream.read()).hexdigest()
    except OSError:
        return None


def terra_proof(config, *, quota_check, evidence_root, now=time.time, run=None, probe=None) -> dict:
    """ONE quota-gated Terra proof call (plan §7.2/7.3); never smoke_profile, never a second launch.

    `quota_check()` must return 'ALLOW' from the trusted reader. `run(state, call)` defaults to the real
    run_call path; `probe()` defaults to the real sandbox probe. The single-use attempt marker is
    persisted before launch; an existing marker means separate parent authorization is required.
    """
    pending, verified, unavailable = cfg.TERRA_STATES
    profile = next((p for p in config.profiles if p.id == 'terra'), None)
    if profile is None or not cfg._terra_tuple(profile) or profile.enabled:
        return _terra_stop(pending, 'local registration: exact disabled Terra operator tuple missing')
    hashes = {rel: _sha_file(Path(evidence_root) / rel) for rel in cfg.TERRA_DISCOVERY_SHA256}
    if hashes != cfg.TERRA_DISCOVERY_SHA256:
        return _terra_stop(pending, 'adopted discovery evidence hash mismatch')
    if now() - cfg.TERRA_DISCOVERY_AT >= cfg.TERRA_PROOF_TTL:
        return _terra_stop(pending, 'discovery stale; repeat authenticated native model/list first')
    quota = quota_check()
    if quota != 'ALLOW':
        return _terra_stop(pending, f'quota: {quota}')
    state_dir = Path(config.state_dir)
    smoke = state_dir / 'smoke'
    marker = smoke / 'terra-proof-attempt.json'
    from forge.state import controller_lock
    with controller_lock(state_dir), State(state_dir) as state:
        if state.db.execute("SELECT 1 FROM calls WHERE status IN ('launching','running')").fetchone():
            return _terra_stop(pending, 'unfinished call exists; reconcile first')
        if marker.exists() or marker.is_symlink():
            return _terra_stop(pending, 'attempt already consumed; separate parent authorization required')
        try:
            from forge.native_children import policy_for
            children = policy_for(profile, 'off', False)
        except (ImportError, ValueError):
            return _terra_stop(pending, 'unsafe child-disable: children-off control unavailable')
        if children.mode != 'off':
            return _terra_stop(pending, 'unsafe child-disable: children-off control unavailable')
        work = _disposable(state_dir, 'doctor-terra')
        nonce = str(random.randrange(10 ** 8, 10 ** 9))
        (work / 'nonce.txt').write_text(nonce + '\n')
        prompt = ('Read /work/nonce.txt with one tool action. Do not spawn sub-agents. Do not write files. Reply with '
                  'only a fenced json block {"nonce": "<digits from the file>", "created": false}. '
                  'Also attempt to create /work/readonly-probe.txt; the read-only sandbox must refuse it.')
        try:
            call = Call(f'terra-proof-{uuid.uuid4().hex[:12]}', 'probe', profile.id, profile.harness, profile.model,
                        profile.effort, work, False, prompt, timeout=min(config.timeouts['probe'], 120),
                        max_turns=min(config.max_turns['probe'] + 2, 4), children=children)
        except TypeError:
            return _terra_stop(pending, 'unsafe child-disable: children-off control unavailable')
        roots = None
        if probe is None or run is None:
            roots = sandbox.project_roots(config)
        sandbox_record = (probe or (lambda: sandbox_probe(roots, state_dir)))()
        if sandbox_record.get('status') != 'pass':
            return _terra_stop(pending, 'sandbox probe failed; no launch')
        binary_info = {}
        if run is None:
            run = _terra_runner(config, roots, profile, binary_info)
        prepare = getattr(run, 'prepare', None)
        if prepare is None:
            return _terra_stop(pending, 'native argv preflight unavailable; no attempt consumed')
        try:
            argv = prepare(call)
            help_text = _terra_exec_help(config, roots, binary_info)
        except (OSError, ValueError, sandbox.SandboxError, subprocess.SubprocessError) as exc:
            return _terra_stop(pending, f'native argv preflight failed: {type(exc).__name__}; no attempt consumed')
        if not _terra_argv_safe(call, argv, help_text):
            return _terra_stop(pending, 'native argv lacks exact pins or an installed turn limit <=4; no attempt consumed')
        attempt = {'call': call.id, 'model': call.model, 'effort': call.effort, 'account': profile.account,
                   'quota': quota, 'quota_checked_at': now(), 'at': now(), 'children': 'off',
                   'argv_sha256': hashlib.sha256(json.dumps(argv).encode()).hexdigest(),
                   'exec_help_sha256': hashlib.sha256(help_text.encode()).hexdigest(),
                   'max_turns': call.max_turns, 'timeout': call.timeout}
        smoke.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            return _terra_stop(pending, 'attempt already consumed; separate parent authorization required')
        with os.fdopen(fd, 'w') as stream:
            json.dump(attempt, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(smoke, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        # Copy only the exact hash-verified public discovery artifacts into controller state.
        for rel, digest in hashes.items():
            source = Path(evidence_root) / rel
            fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    return _terra_stop(pending, 'discovery is not regular; attempt remains consumed')
                data = stream.read()
            if hashlib.sha256(data).hexdigest() != digest:
                return _terra_stop(pending, 'discovery changed before launch; attempt remains consumed')
            destination = smoke / 'terra-discovery' / rel
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            from forge.state import atomic_write
            atomic_write(destination, data)
        result = run(state, call)
        record = _terra_record(call, result, profile, hashes, attempt, binary_info, nonce,
                               (work / 'readonly-probe.txt').exists(), now())
        record['sandbox_probe'] = sandbox_record.get('status')
        record['binding'] = cfg.terra_binding(profile, config.harness[profile.harness], binary_info)
        record['evidence'] = {'manifest': _sha_file(state_dir / 'calls' / call.id / 'manifest.json'),
                              'result': _sha_file(state_dir / 'calls' / call.id / 'result.json'),
                              'sandbox': _sha_file(smoke / 'sandbox.json')}
        record['sandbox_manifest_sha256'] = record['evidence']['manifest']
        if record['verdict'] == verified and (not binary_info or not all(record['evidence'].values())):
            record.update(verdict=pending, reason='native evidence/fingerprint missing; keep disabled')
        if record['verdict'] == verified:
            try:
                durable = json.loads(cfg._proof_bytes(state_dir / 'calls' / call.id / 'result.json'))
                matches = cfg.terra_metadata_matches(record, durable)
            except (OSError, ValueError, TypeError, KeyError):
                matches = False
            if not matches:
                record.update(verdict=pending, reason='observed native metadata missing or mismatched in hashed result')
        atomic_json(smoke / 'terra-proof.json', record)
        if record['verdict'] == verified:
            ok, reason = cfg.verify_terra_proof(smoke / 'terra-proof.json', profile, now(),
                                               harness=config.harness[profile.harness])
            if not ok:
                record.update(verdict=pending, reason=reason)
                atomic_json(smoke / 'terra-proof.json', record)
            else:
                h = config.harness[profile.harness]
                _save(state, state_dir, {'profile': profile.id, 'status': 'pass', 'binary': binary_info,
                      'model': profile.model, 'effort': profile.effort, 'binds_rw': list(h['binds_rw']),
                      'binds_ro': list(h['binds_ro']), 'runtime_dir': h.get('runtime_dir'),
                      'sandbox_probe': 'pass', 'capabilities': ['text', 'readonly'], 'at': now(),
                      'steps': {'text': {'call': call.id, 'outcome': result.outcome}}})
    return record


def _terra_runner(config, roots, profile, binary_info):
    """Real run_call path; binary is resolved and pinned before the attempt marker is written."""
    harness = config.harness[profile.harness]
    binary = sandbox.resolve_binary(profile.harness, harness.get('binary'))
    binary_info.update(path=str(binary), version=binary_version(roots, profile.harness, binary, harness.get('runtime_dir')),
                       sha256=hashlib.sha256(binary.read_bytes()).hexdigest())

    adapter = adapter_for(profile.harness)
    prepared = {}

    def prepare(call):
        in_dir = Path(config.state_dir) / 'calls' / call.id / 'in'
        argv, stdin = adapter.build_argv(call, str(sandbox.NATIVE_BIN / profile.harness), in_dir)
        prepared[call.id] = (call, tuple(argv), stdin)
        return list(argv)

    def run(state, call):
        pinned = _PreparedTerraAdapter(adapter, prepared[call.id])
        def spec_for(native_args):
            return sandbox.agent_spec(roots, harness=profile.harness, binary=binary, native_args=tuple(native_args),
                                      work=call.workdir, writable=False,
                                      input_dir=Path(config.state_dir) / 'calls' / call.id / 'in',
                                      binds_rw=harness['binds_rw'], binds_ro=harness['binds_ro'],
                                      runtime_dir=harness.get('runtime_dir'))
        return run_call(state, call, pinned, spec_for,
                        calls_root=Path(config.state_dir) / 'calls', account=profile.account, binary_info=binary_info)
    run.prepare = prepare
    return run


class _PreparedTerraAdapter:
    """Reuse exactly the argv/stdin inspected before the single-use marker."""
    def __init__(self, adapter, prepared):
        self.adapter = adapter
        self.call, self.argv, self.stdin = prepared

    def build_argv(self, call, exe, in_dir):
        if call != self.call or exe != self.argv[0]:
            raise ValueError('Terra prepared call/executable changed')
        return list(self.argv), self.stdin

    def parse_line(self, line):
        return self.adapter.parse_line(line)

    def finish(self, *args):
        return self.adapter.finish(*args)


def _terra_exec_help(config, roots, binary_info):
    """Installed CLI inspection only: real sandbox, no credentials or inference."""
    binary = Path(binary_info['path'])
    mounts, exe = sandbox.executable_mounts(roots, 'codex', binary, config.harness['codex'].get('runtime_dir'))
    work = _disposable(Path(config.state_dir), 'doctor-terra-help')
    try:
        spec = sandbox.Spec('agent', (sandbox.work_mount(roots, work, False), *mounts), False,
                            sandbox.PurePosixPath(roots.home), (exe, 'exec', '--help'))
        sandbox.revalidate(spec)
        proc = subprocess.run(sandbox.argv(spec), capture_output=True, text=True, timeout=15,
                              stdin=subprocess.DEVNULL)
        return proc.stdout if proc.returncode == 0 else ''
    finally:
        shutil.rmtree(work.parent, ignore_errors=True)


def _terra_argv_safe(call, argv, help_text):
    """No guessed config key: an actual installed CLI turn option must be present."""
    if (call.model != 'gpt-6.1-terra' or call.effort != 'low' or call.writable or call.resume_sid
            or not 0 < call.timeout <= 120 or not 1 <= call.max_turns <= 4
            or len(argv) < 3 or argv[:2] != [str(sandbox.NATIVE_BIN / 'codex'), 'exec'] or argv[-1] != '-'):
        return False
    # Current Codex has no such control. Future support requires both an explicit
    # installed help option and its real argv; a fixture flag alone proves neither.
    if not re.search(r'(?m)^\s*--max-turns\s+<[^>]+>', help_text):
        return False
    values = {}
    switches = {'--dangerously-bypass-approvals-and-sandbox', '--json', '--skip-git-repo-check'}
    options = {'-m', '-c', '-C', '--disable', '--max-turns'}
    seen = set()
    tokens = iter(argv[2:-1])
    for token in tokens:
        if token in switches and token not in seen:
            seen.add(token)
        elif token in options:
            value = next(tokens, None)
            if value is None:
                return False
            values.setdefault(token, []).append(value)
        else:
            return False
    caps = values.pop('--max-turns', [])
    return (len(caps) == 1 and caps[0].isdecimal() and 1 <= int(caps[0]) <= min(4, call.max_turns)
            and seen == switches and values == {'-m': ['gpt-6.1-terra'],
                '-c': ['model_reasoning_effort="low"'], '-C': ['/work'],
                '--disable': ['multi_agent', 'multi_agent_v2']})


def _terra_record(call, result, profile, hashes, attempt, binary_info, nonce, wrote, at):
    """Sanitized whitelist only: no prompts, raw output or credential-bearing fields."""
    pending, verified, unavailable = cfg.TERRA_STATES
    meta = dict(getattr(result, 'native_metadata', None) or {})
    actual = {'model': getattr(result, 'actual_model', None), 'effort': meta.get('effort')}
    record = {'verdict': pending, 'reason': '', 'model': profile.model, 'effort': profile.effort, 'tier': 'normal',
              'account': profile.account, 'requested': {'model': call.model, 'effort': call.effort},
              'actual': actual, 'model_match': getattr(result, 'model_match', None),
              'outcome': getattr(result, 'outcome', None), 'call': call.id, 'attempt': attempt,
              'discovery': hashes, 'discovery_at': cfg.TERRA_DISCOVERY_AT, 'binary': binary_info or None,
              'context_window': meta.get('context_window') or 'UNKNOWN', 'tools': meta.get('tools') or 'UNKNOWN',
              'capabilities': [], 'at': at}
    errors = getattr(result, 'error_fields', None) or {}
    if record['outcome'] in TERRA_DENIALS or errors.get('code') in TERRA_DENIALS:
        record.update(verdict=unavailable, reason='native provider denial for exact identifier')
    elif record['outcome'] != 'ok':
        record['reason'] = f"uncertain execution: {record['outcome']}; separate parent authorization required"
    elif record['model_match'] is None or None in actual.values():
        record['reason'] = 'missing metadata: native requested==actual model and effort not established'
    elif actual != record['requested'] or record['model_match'] is not True:
        record['reason'] = 'requested/actual model or effort mismatch (no fallback accepted)'
    else:
        if (getattr(result, 'final_json', None) or {}).get('nonce') == nonce:
            record['capabilities'].append('text')
            if not wrote:
                record['capabilities'].append('readonly')
        if record['capabilities'] != ['text', 'readonly']:
            record['reason'] = 'text/readonly challenge not proven'
        elif 'UNKNOWN' in (record['context_window'], record['tools']):
            record['reason'] = 'missing metadata: context/tools UNKNOWN; keep disabled'
        else:
            record.update(verdict=verified, reason='verified; operator-profile review still required')
    return record


def native_child_capability(config, profile):
    """Bounded read-only capability inspection; --version is not an inference probe."""
    from forge.native_children import capability_for
    h = config.harness[profile.harness]
    binary = sandbox.resolve_binary(profile.harness, h.get('binary'))
    info = {'path': str(binary), 'sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
            'version': binary_version(sandbox.project_roots(config), profile.harness, binary, h.get('runtime_dir'))}
    return capability_for(profile, info, h)
