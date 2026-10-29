"""Doctor: stdlib PNG, REAL sandbox probe, and capability-record logic exercised through REAL
bwrap with a FIXTURE codex stand-in (proves record pinning, not live model capability)."""

import json
from pathlib import Path
import struct
import zlib

from forge import doctor
from forge.cli import main
from forge.config import load_config
from forge.state import State
from test_sandbox import make_layout

FAKE_CODEX = r'''#!/usr/bin/python3
import json, os, re, sys
args = sys.argv[1:]
if args == ['--version']:
    print('codex-cli 0.159.3 (FIXTURE)'); sys.exit(0)
prompt = sys.stdin.read()
store = os.path.expanduser('~/.codex/fixture-session')
def say(text, sid='fixture-sid'):
    print(json.dumps({"type": "thread.started", "thread_id": sid}))
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "```json\n" + json.dumps(text) + "\n```"}}))
    print(json.dumps({"type": "turn.completed", "usage": {}}))
if 'resume' in args:
    nonce = open(store).read()
    open('/work/resume-file.txt', 'w').write(nonce)
    say({"nonce": nonce}, args[-2])
elif 'readonly-probe' in prompt:
    try:
        open('/work/readonly-probe.txt', 'w').write('x')
    except OSError:
        pass
    say({"created": False})
elif 'attached image' in prompt:
    say({"top_left": "purple", "bottom_right": "purple"})
else:
    nonce = re.search(r'"nonce": "(\d+)"', prompt).group(1)
    open(store, 'w').write(nonce)
    say({"nonce": nonce})
'''


def decode_png(data):
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    width, height = struct.unpack('>II', data[16:24])
    idat = data[data.index(b'IDAT') + 4:data.index(b'IEND') - 8]
    raw = zlib.decompress(idat)
    stride = width * 3 + 1
    return width, height, lambda x, y: tuple(raw[y * stride + 1 + x * 3:y * stride + 4 + x * 3])


def test_quadrant_png_is_real_pixels():
    w, h, pixel = decode_png(doctor.quadrant_png(['red', 'green', 'blue', 'yellow']))
    assert (w, h) == (64, 64)
    assert pixel(0, 0) == doctor.COLOURS['red'] and pixel(63, 0) == doctor.COLOURS['green']
    assert pixel(0, 63) == doctor.COLOURS['blue'] and pixel(63, 63) == doctor.COLOURS['yellow']


def test_real_sandbox_probe_passes_and_records(tmp_path):
    roots, _ = make_layout(tmp_path)
    record = doctor.sandbox_probe(roots, roots.state, [roots.home / '.ssh/id_rsa'])
    assert record['status'] == 'pass', record
    assert set(record['checks']) == {'readonly', 'writable', 'gate'}
    assert 'WORK-RO' in record['checks']['readonly']['stdout'] and 'NET-DOWN' in record['checks']['gate']['stdout']
    saved = json.loads((roots.state / 'smoke/sandbox.json').read_text())
    assert saved['status'] == 'pass' and str(roots.state / 'forge.db') in saved['secrets_checked']


def test_cli_doctor_real_probe_against_real_home(operator_config, capsys):
    path = operator_config()
    assert main(['doctor', '--config', str(path)]) == 0
    out = json.loads(capsys.readouterr().out.splitlines()[0])
    assert out['sandbox_probe'] == 'pass'
    record = json.loads((load_config(path).state_dir / 'smoke/sandbox.json').read_text())
    assert str(Path.home() / '.ssh') in record['secrets_checked']


def test_smoke_record_pins_binary_model_effort_binds(operator_config, tmp_path, monkeypatch):
    roots, bins = make_layout(tmp_path / 'layout')
    monkeypatch.setenv('HOME', str(roots.home))
    bins['codex'].write_text(FAKE_CODEX)
    path = operator_config(f'[harness.codex]\nbinary = "{bins["codex"]}"\n')
    config = load_config(path)
    from forge.sandbox import project_roots
    proots = project_roots(config)
    config.state_dir.mkdir(parents=True)
    probe = doctor.sandbox_probe(proots, config.state_dir)
    assert probe['status'] == 'pass'
    profile = next(p for p in config.profiles if p.id == 'sol-high')
    with State(config.state_dir) as state:
        record = doctor.smoke_profile(config, state, proots, profile, probe_record=probe)
        row = state.db.execute('SELECT * FROM smoke WHERE profile=?', ('sol-high',)).fetchone()
    assert record['binary']['path'] == str(bins['codex'].resolve())
    assert record['binary']['version'] == 'codex-cli 0.159.3 (FIXTURE)'
    assert (record['model'], record['effort']) == ('gpt-6.1-sol', 'high')
    assert record['binds_rw'] == ['~/.codex'] and record['sandbox_probe'] == 'pass'
    assert record['capabilities'] == ['text', 'resume', 'readonly']  # wrong colours → no image capability
    assert record['steps']['resume']['sid'] == record['steps']['text']['sid']
    assert json.loads(row['capabilities']) == record['capabilities'] and row['fingerprint'] == record['fingerprint']
    assert not (config.state_dir / 'ws' / 'doctor-sol-high' / 'src' / 'readonly-probe.txt').exists()


def test_failed_probe_blocks_smoke_without_native_calls(operator_config, tmp_path, monkeypatch):
    roots, bins = make_layout(tmp_path / 'layout')
    monkeypatch.setenv('HOME', str(roots.home))
    config = load_config(operator_config(f'[harness.codex]\nbinary = "{bins["codex"]}"\n'))
    config.state_dir.mkdir(parents=True)
    profile = next(p for p in config.profiles if p.id == 'sol-high')
    with State(config.state_dir) as state:
        from forge.sandbox import project_roots
        record = doctor.smoke_profile(config, state, project_roots(config), profile, probe_record={'status': 'fail'})
        assert state.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0] == 0
    assert record['status'] == 'env_block' and record['capabilities'] == []


def test_doctor_shared_account_wait_prevents_other_profile_calls(operator_config, monkeypatch, capsys):
    import time
    config = load_config(operator_config())
    invoked = []
    reset = time.time() + 900
    def smoke(config, state, roots, profile, **kwargs):
        invoked.append(profile.id)
        with state.transaction() as db:
            db.execute("INSERT OR REPLACE INTO accounts(id,state,recheck_at,outcome) VALUES(?,'waiting',?,'quota_wait')", (profile.account, reset))
        return {'status':'quota_wait','steps':{'text':{'outcome':'quota_wait'}}}
    monkeypatch.setattr(doctor,'smoke_profile',smoke)
    with State(config.state_dir) as state:
        from forge.sandbox import project_roots
        profiles = [p for p in config.profiles if p.harness=='codex' and p.enabled]
        assert doctor._profiles(config,state,project_roots(config),{'status':'pass'},profiles)==75
    assert invoked == ['luna-xhigh']
    assert str(reset) in capsys.readouterr().out


def test_codex_completed_error_is_not_success():
    from forge.adapters.codex import CodexAdapter
    adapter = CodexAdapter()
    event = adapter.parse_line(json.dumps({'type':'item.completed','item':{'type':'error','message':'missing code-mode host'}}))
    assert event.kind == 'error'


# SIMULATED Terra proof helper: fake `run`/`probe`/quota and synthetic evidence; no native binary or inference.
import hashlib
from types import SimpleNamespace

import pytest

from forge import config as cfg

TERRA = ('[[profile]]\nid="terra"\nharness="codex"\nmodel="gpt-6.1-terra"\neffort="low"\nfamily="openai"\n'
         'tiers=["normal"]\naccount="codex"\nenabled=false\n')
META = {'model': 'gpt-6.1-terra', 'effort': 'low', 'context_window': 400000, 'tools': ['shell']}


@pytest.fixture
def terra(operator_config, tmp_path, monkeypatch):
    evidence = tmp_path / 'evidence'
    hashes = {}
    for rel in cfg.TERRA_DISCOVERY_SHA256:
        (evidence / rel).parent.mkdir(parents=True, exist_ok=True)
        (evidence / rel).write_text(f'synthetic {rel}')
        hashes[rel] = hashlib.sha256(f'synthetic {rel}'.encode()).hexdigest()
    monkeypatch.setattr(cfg, 'TERRA_DISCOVERY_SHA256', hashes)
    monkeypatch.setattr(doctor, 'smoke_profile', lambda *a, **k: pytest.fail('smoke_profile must never run for Terra'))
    config = load_config(operator_config(TERRA))
    calls = []
    fixture = SimpleNamespace(turn_cap=True)
    monkeypatch.setattr(doctor, '_terra_exec_help', lambda *args: '  --max-turns <MAX_TURNS>  SIMULATED turn control\n')

    def fake(meta=META, outcome='ok', model='gpt-6.1-terra', match=True, persist_metadata=True):
        def run(state, call):
            calls.append(call)
            nonce = (call.workdir / 'nonce.txt').read_text().strip()
            raw = state.root / 'calls' / call.id
            raw.mkdir(parents=True)
            (raw / 'manifest.json').write_text(json.dumps({'SIMULATED': True}))
            from forge.adapters.codex import CodexAdapter
            adapter = CodexAdapter()
            event = adapter.parse_line(json.dumps({'type': 'turn.completed', **meta}))
            observed = adapter.finish(call, 0, [event], b'', '').native_metadata
            result = {'call_id': call.id, 'outcome': outcome, 'actual_model': model, 'model_match': match}
            if persist_metadata:
                result['native_metadata'] = observed
            (raw / 'result.json').write_text(json.dumps(result))
            for path in raw.iterdir():
                path.chmod(0o600)
            return SimpleNamespace(outcome=outcome, actual_model=model, model_match=match, native_metadata=observed,
                                   final_json={'nonce': nonce, 'created': False}, error_fields={})
        return run

    def proof(run, quota='ALLOW', root=evidence):
        def factory(config, roots, profile, binary_info):
            binary = doctor.sandbox.resolve_binary(profile.harness, None)
            binary_info.update(path=str(binary), version='SIMULATED', sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
            def prepare(call):
                from forge.adapters.codex import CodexAdapter
                in_dir = config.state_dir / 'calls' / call.id / 'in'
                argv, _ = CodexAdapter().build_argv(call, '/opt/forge-native/bin/codex', in_dir)
                if fixture.turn_cap:
                    argv[-1:-1] = ['--max-turns', str(call.max_turns)]
                return argv
            run.prepare = prepare
            return run
        def probe():
            dest = config.state_dir / 'smoke' / 'sandbox.json'
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(json.dumps({'status': 'pass', 'SIMULATED': True}))
            dest.chmod(0o600)
            return {'status': 'pass'}
        monkeypatch.setattr(doctor, '_terra_runner', factory)
        return doctor.terra_proof(config, quota_check=lambda: quota, evidence_root=root,
                                  now=lambda: cfg.TERRA_DISCOVERY_AT + 3600, probe=probe)
    fixture.config, fixture.calls, fixture.fake, fixture.proof, fixture.evidence = config, calls, fake, proof, evidence
    return fixture


def test_terra_proof_quota_wait_and_hash_mismatch_never_run(terra):
    assert terra.proof(terra.fake(), quota='WAIT') == {'state': 'TERRA_DISCOVERED_REGISTRATION_PENDING',
                                                       'reason': 'quota: WAIT'}
    (terra.evidence / 'terra-availability.md').write_text('tampered')
    assert 'hash mismatch' in terra.proof(terra.fake())['reason']
    assert terra.calls == [] and not (terra.config.state_dir / 'smoke/terra-proof-attempt.json').exists()


def test_terra_proof_single_call_verified_then_marker_blocks_relaunch(terra):
    record = terra.proof(terra.fake())
    assert record['verdict'] == 'TERRA_VERIFIED_NORMAL' and record['capabilities'] == ['text', 'readonly']
    [call] = terra.calls
    assert (call.role, call.model, call.effort, call.writable) == ('probe', 'gpt-6.1-terra', 'low', False)
    assert call.timeout <= 120 and call.max_turns <= 4 and call.children.mode == 'off'
    smoke = terra.config.state_dir / 'smoke'
    assert json.loads((smoke / 'terra-proof-attempt.json').read_text())['call'] == call.id
    assert json.loads((smoke / 'terra-proof.json').read_text()) == record
    assert cfg.verify_terra_proof(smoke / 'terra-proof.json', cfg.TERRA_PROFILE, record['at'] + 1) == (True, 'verified')
    again = terra.proof(terra.fake())
    assert again['reason'] == 'attempt already consumed; separate parent authorization required'
    assert len(terra.calls) == 1


@pytest.mark.parametrize('kwargs, verdict, reason', [
    ({'model': None, 'match': None}, 'TERRA_DISCOVERED_REGISTRATION_PENDING', 'missing metadata'),
    ({'meta': {}}, 'TERRA_DISCOVERED_REGISTRATION_PENDING', 'missing metadata'),
    ({'meta': {'effort': 'low'}}, 'TERRA_DISCOVERED_REGISTRATION_PENDING', 'context/tools UNKNOWN'),
    ({'meta': {**META, 'effort': 'high'}}, 'TERRA_DISCOVERED_REGISTRATION_PENDING', 'mismatch'),
    ({'outcome': 'timeout'}, 'TERRA_DISCOVERED_REGISTRATION_PENDING', 'uncertain execution'),
    ({'outcome': 'model_unavailable', 'model': None, 'match': None}, 'TERRA_UNAVAILABLE_EVIDENCE', 'denial'),
])
def test_terra_proof_metadata_outcomes(terra, kwargs, verdict, reason):
    record = terra.proof(terra.fake(**kwargs))
    assert record['verdict'] == verdict and reason in record['reason'] and len(terra.calls) == 1
    assert (terra.config.state_dir / 'smoke/terra-proof-attempt.json').exists()


def test_terra_proof_refuses_unfinished_calls_and_wrong_tuple(terra, operator_config):
    with State(terra.config.state_dir) as state, state.transaction() as db:
        db.execute("INSERT INTO calls(id, role, profile, status, started) VALUES('x','probe','sol-high','launching',1)")
    assert 'unfinished call' in terra.proof(terra.fake())['reason']
    with State(terra.config.state_dir) as state, state.transaction() as db:
        db.execute("UPDATE calls SET status='done'")
    terra.config = load_config(operator_config())          # default TBD tuple only
    assert 'tuple missing' in doctor.terra_proof(terra.config, quota_check=lambda: 'ALLOW', evidence_root=terra.evidence,
                                                 run=terra.fake(), probe=lambda: {'status': 'pass'})['reason']
    assert terra.calls == []


def test_terra_uncertain_launch_never_purchases_another_attempt(terra):
    def crash(state, call):
        terra.calls.append(call)
        raise RuntimeError('SIMULATED uncertain launch')
    with pytest.raises(RuntimeError, match='uncertain launch'):
        terra.proof(crash)
    assert 'attempt already consumed' in terra.proof(terra.fake())['reason']
    assert len(terra.calls) == 1


def test_terra_cannot_use_ordinary_multi_call_smoke(operator_config):
    config = load_config(operator_config(TERRA))
    with State(config.state_dir) as state:
        with pytest.raises(ValueError, match='single-call'):
            doctor.smoke_profile(config, state, None, config.profiles[0], probe_record={'status': 'pass'})
        assert state.db.execute('SELECT count(*) FROM calls').fetchone()[0] == 0


def test_terra_memory_only_metadata_cannot_create_verified_proof(terra):
    """Regression: the legacy fixture's hashed result omits its in-memory metadata."""
    record = terra.proof(terra.fake(persist_metadata=False))
    assert record['verdict'] == 'TERRA_DISCOVERED_REGISTRATION_PENDING'
    assert 'hashed result' in record['reason']
    assert not cfg.verify_terra_proof(terra.config.state_dir / 'smoke/terra-proof.json',
                                     cfg.TERRA_PROFILE, record['at'] + 1)[0]


def test_terra_argv_without_installed_turn_cap_never_consumes_attempt(terra, monkeypatch):
    terra.turn_cap = False
    monkeypatch.setattr(doctor, '_terra_exec_help', lambda *args: 'Usage: codex exec [OPTIONS]\n', raising=False)
    record = terra.proof(terra.fake())
    assert terra.calls == []
    assert not (terra.config.state_dir / 'smoke/terra-proof-attempt.json').exists()
    assert record.get('state') == 'TERRA_DISCOVERED_REGISTRATION_PENDING'


@pytest.mark.parametrize('help_available, cap', [(True, False), (False, True), (False, False)])
def test_terra_installed_help_and_effective_cap_are_both_required(terra, monkeypatch, help_available, cap):
    terra.turn_cap = cap
    if not help_available:
        monkeypatch.setattr(doctor, '_terra_exec_help', lambda *args: 'Usage: codex exec [OPTIONS]\n')
    record = terra.proof(terra.fake())
    assert record['state'] == 'TERRA_DISCOVERED_REGISTRATION_PENDING'
    assert terra.calls == []
    assert not (terra.config.state_dir / 'smoke/terra-proof-attempt.json').exists()


@pytest.mark.parametrize('mutation', ['model', 'effort', 'children', 'turns', 'enable', 'timeout'])
def test_terra_argv_preflight_rejects_pin_or_limit_drift(tmp_path, mutation):
    from dataclasses import replace
    from forge.adapters.codex import CodexAdapter
    from forge.adapters.base import Call
    from forge.native_children import policy_for
    call = Call('fixture', 'probe', 'terra', 'codex', 'gpt-6.1-terra', 'low', tmp_path, False,
                'nonce', timeout=120, max_turns=4, children=policy_for(cfg.TERRA_PROFILE, 'off', False))
    argv, _ = CodexAdapter().build_argv(call, '/opt/forge-native/bin/codex', tmp_path)
    argv[-1:-1] = ['--max-turns', '4']
    help_text = '  --max-turns <MAX_TURNS> SIMULATED\n'
    assert doctor._terra_argv_safe(call, argv, help_text)
    if mutation == 'model': argv[argv.index('-m') + 1] = 'gpt-6.1-sol'
    if mutation == 'effort': argv[argv.index('-c') + 1] = 'model_reasoning_effort="high"'
    if mutation == 'children': argv[argv.index('multi_agent')] = 'other_feature'
    if mutation == 'turns': argv[argv.index('--max-turns') + 1] = '5'
    if mutation == 'enable': argv[-1:-1] = ['--enable', 'multi_agent']
    if mutation == 'timeout': call = replace(call, timeout=121)
    assert not doctor._terra_argv_safe(call, argv, help_text)


from test_runner import env as native_runner_env


@pytest.mark.parametrize('missing', [None, 'model', 'effort', 'context_window', 'tools'])
def test_observed_native_fields_reach_real_hashed_runner_result(native_runner_env, missing):
    """SIMULATED Codex program; real bwrap and production Result/result.json persistence."""
    from test_runner import launch
    roots, bins, state = native_runner_env
    observed = dict(META)
    if missing:
        observed.pop(missing)
    script = ('#!/usr/bin/python3\nimport json,sys\nsys.stdin.read()\n'
              'print(json.dumps({"type":"thread.started","thread_id":"fixture-metadata"}))\n'
              'print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"```json\\n{}\\n```"}}))\n'
              f'print(json.dumps({{"type":"turn.completed", **{observed!r}}}))\n')
    call, work, result = launch(roots, bins, state, 'codex', script, model='gpt-6.1-terra', effort='low')
    durable = json.loads((Path(result.raw_dir) / 'result.json').read_text())
    assert result.outcome == 'ok'
    assert result.native_metadata == durable['native_metadata'] == observed
    if missing == 'model':
        assert result.actual_model is None and result.model_match is None


@pytest.mark.parametrize('field', ['model', 'effort', 'context_window', 'tools'])
def test_terra_verification_requires_all_four_observed_event_fields(terra, field):
    record = terra.proof(terra.fake(meta={key: value for key, value in META.items() if key != field}))
    assert record['verdict'] == 'TERRA_DISCOVERED_REGISTRATION_PENDING'
    assert not cfg.verify_terra_proof(terra.config.state_dir / 'smoke/terra-proof.json', cfg.TERRA_PROFILE,
                                     record['at'] + 1)[0]


def test_prepared_terra_runner_executes_exact_checked_argv(native_runner_env, monkeypatch):
    """SIMULATED future turn flag, real adapter/runner/bwrap persistence; no model inference."""
    from forge.adapters.codex import CodexAdapter
    from forge.adapters.base import Call
    from forge.native_children import policy_for
    from test_sandbox import leaf
    roots, bins, state = native_runner_env
    script = ('#!/usr/bin/python3\nimport json,sys\n'
              'if sys.argv[1:]==["--version"]:\n print("SIMULATED codex"); sys.exit(0)\n'
              'assert sys.argv[sys.argv.index("--max-turns")+1]=="4"\n'
              'sys.stdin.read()\n'
              'print(json.dumps({"type":"thread.started","thread_id":"fixture-prepared"}))\n'
              'print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"```json\\n{}\\n```"}}))\n'
              f'print(json.dumps({{"type":"turn.completed", **{META!r}}}))\n')
    bins['codex'].write_text(script)
    built = []
    class FixtureAdapter(CodexAdapter):
        def build_argv(self, call, exe, in_dir):
            assert not built, 'runner must reuse the checked argv, not rebuild it'
            argv, stdin = super().build_argv(call, exe, in_dir)
            argv[-1:-1] = ['--max-turns', '4']
            built.append(list(argv))
            return argv, stdin
    monkeypatch.setattr(doctor, 'adapter_for', lambda _: FixtureAdapter())
    config = SimpleNamespace(state_dir=roots.state, harness={'codex': {'binary': str(bins['codex']),
        'runtime_dir': None, 'binds_rw': [str(roots.home / '.codex')], 'binds_ro': []}})
    call = Call('terra-proof-abcdef012345', 'probe', 'terra', 'codex', 'gpt-6.1-terra', 'low',
                leaf(roots, 'prepared-proof'), False, 'fixture challenge', timeout=120, max_turns=4,
                children=policy_for(cfg.TERRA_PROFILE, 'off', False))
    run = doctor._terra_runner(config, roots, cfg.TERRA_PROFILE, {})
    checked = run.prepare(call)
    assert doctor._terra_argv_safe(call, checked, '  --max-turns <MAX_TURNS> SIMULATED\n')
    assert not (roots.state / 'calls' / call.id / 'in').exists()
    result = run(state, call)
    assert result.outcome == 'ok' and result.native_metadata == META and len(built) == 1
    manifest = json.loads((Path(result.raw_dir) / 'manifest.json').read_text())
    assert manifest['command'] == checked
    assert json.loads((Path(result.raw_dir) / 'result.json').read_text())['native_metadata'] == META
