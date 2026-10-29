import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

from forge.cli import main, parser
import os
import time

from forge import config as cfg
from forge.config import ConfigError, load_config, tier_writers, verify_terra_proof


def test_runtime_roster_has_all_tiers(operator_config):
    cfg = load_config(operator_config())
    assert [(p.id, p.model, p.effort, p.tiers, p.enabled) for p in cfg.profiles] == [
        ('luna-xhigh', 'gpt-6-luna', 'xhigh', ('cheap',), True),
        ('haiku-high', 'claude-haiku-5-5', 'high', ('cheap',), True),
        ('sol-high', 'gpt-6.1-sol', 'high', ('normal', 'strong'), True),
        ('sonnet-high', 'claude-sonnet-5-5', 'high', ('normal',), True),
        ('grok-high', 'grok-4.7', 'high', ('normal',), True),
        ('opus-high', 'claude-opus-5-5', 'high', ('strong',), True),
        ('terra', 'TBD', 'TBD', ('strong',), False),
    ]
    assert {tier for p in cfg.profiles if p.enabled for tier in p.tiers} == {'cheap', 'normal', 'strong'}
    assert cfg.parallel == 1
    assert cfg.budgets['easy']['fix_rounds'] == 2
    assert cfg.budgets['hard']['fix_rounds'] == 3
    assert cfg.budgets['escalation']['fix_rounds'] == 2
    assert cfg.timeouts['implement'] == 2700
    assert cfg.max_turns['probe'] == 2
    assert cfg.execution['gate_network'] is False
    with pytest.raises(TypeError):
        cfg.accounts['codex']['enabled'] = False


def test_medium_effort_independent_of_tier(operator_config):
    cfg = load_config(operator_config('''[[profile]]
id="sol-medium"
harness="codex"
model="gpt-6.1-sol"
effort="medium"
family="openai"
tiers=["normal", "strong"]
'''))
    assert cfg.profiles[0].effort == 'medium'
    assert cfg.profiles[0].tiers == ('normal', 'strong')


@pytest.mark.parametrize('extra', [
    'paralell=1', 'parallel=true', 'parallel=2', 'sources=["../secret"]',
    'sources=["/absolute"]', 'sources="docs/**"', 'branch="-bad"', 'branch="a/../b"',
    '[exec]\ngate_network="false"', '[exec]\nmode="host"',
    '[accounts.codex]\nenabled=1', '[budgets]\nrole_attempts=0',
    '[budgets.easy]\nimpl_attempts=true', '[budgets]\nextra=1',
    '[timeouts]\npo=-1', '[harness.codex]\ncommand=["evil"]',
    '[commands]\nother=["evil"]', '[commands]\ntest=[]',
    '[commands]\ntest="pytest"', '[commands]\ntest=["pytest", "{node}"]',
    '[commands]\ntest_one=["pytest", "{node.__class__}"]',
    '[commands]\ntest_one=["pytest", "{node!r}"]',
    '[commands]\ntest_one=["pytest", "{oops}"]',
    '[commands]\ntest_one=["pytest", "{node"]',
    '[[profile]]\nid="terra"\nharness="codex"\nmodel="TBD"\neffort="TBD"\nfamily="openai"\ntiers=["strong"]\nenabled=true',
    '[[profile]]\nid="fake"\nharness="codex"\nmodel="gpt-6.1-sol"\neffort="high"\nfamily="anthropic"\ntiers=["strong"]',
    '[[profile]]\nid="fake"\nharness="codex"\nmodel="gpt-6.1-sol"\neffort="high"\nfamily="openai"\ntiers=["ultra"]',
])
def test_strict_invalid_config(operator_config, extra):
    with pytest.raises(ConfigError):
        load_config(operator_config(extra))


def test_nested_turns_and_argv_are_preserved(operator_config):
    cfg = load_config(operator_config('''[timeouts]
probe=60
max_turns={probe=2}
[commands]
test_one=["python3", "-m", "pytest", "--junitxml={junit}", "{node}"]
'''))
    assert cfg.timeouts['probe'] == 60
    assert cfg.commands['test_one'][-2:] == ('--junitxml={junit}', '{node}')


def test_repo_config_cannot_define_commands(operator_config, monkeypatch):
    path = operator_config()
    target = load_config(path).target_repo
    malicious = target / 'forge.toml'
    malicious.write_text('target_repo="/"\n[commands]\ntest=["evil"]\n')
    monkeypatch.chdir(target)
    with pytest.raises(ConfigError):
        load_config(malicious)
    assert load_config(path).commands['test'][0] == 'python3'
    with pytest.raises(ConfigError):
        load_config(operator_config(f'target_repo="{path.parent}"'))


def test_untrusted_permissions_and_target_overlap(operator_config):
    path = operator_config()
    path.chmod(0o666)
    with pytest.raises(ConfigError, match='operator-owned'):
        load_config(path)
    path = operator_config()
    path.write_text(f'target_repo="{path.parent}"\n')
    with pytest.raises(ConfigError, match='outside'):
        load_config(path)


def test_no_pkr_dependency_and_no_forbidden_refs():
    root = Path(__file__).resolve().parents[1]
    source = '\n'.join(p.read_text() for p in (root / 'src/forge').glob('*.py'))
    for forbidden in ('pkr_worker', '.hermes', 'agent-loop', 'Trello', 'Kiro', 'git push', 'remote add'):
        assert forbidden not in source
    project = tomllib.loads((root / 'pyproject.toml').read_text())['project']
    assert project['dependencies'] == []
    assert project['requires-python'] == '>=3.12'
    assert project['scripts'] == {'forge': 'forge.cli:main'}


def test_cli_foundation_and_no_runtime_claim(operator_config, capsys):
    path = operator_config()
    assert main(['config-check', '--config', str(path)]) == 0
    assert json.loads(capsys.readouterr().out)['runtime_verified'] is False
    assert main(['--config', str(path), 'config-check']) == 0
    assert parser().parse_args(['run']).max_batches is None
    assert main(['run']) == 2
    assert 'not yet implemented' in capsys.readouterr().err
    assert not load_config(path).state_dir.exists()
    result = subprocess.run([sys.executable, '-m', 'forge', '--version'], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == 'forge 3.0.0\n'


LOOP_PROFILES = '''[[profile]]
id="w1"
harness="claude"
model="claude-opus-5-5"
effort="medium"
family="anthropic"
tiers=["strong"]
[[profile]]
id="r1"
harness="codex"
model="gpt-6.1-sol"
effort="medium"
family="openai"
tiers=["strong"]
[[profile]]
id="off"
harness="codex"
model="gpt-6.1-sol"
effort="high"
family="openai"
tiers=["strong"]
enabled=false
'''


def test_loop_table_is_optional_and_strict(operator_config):
    assert load_config(operator_config()).loop is None
    cfg = load_config(operator_config(LOOP_PROFILES + '[loop]\nwriters=["w1"]\nreviewers=["r1"]\nbranch="forge/kiss-loop"\n'
                                      'env={CARGO_TARGET_DIR="/work/target", PATH="/usr/bin:/bin"}\n'))
    assert cfg.loop['branch'] == 'forge/kiss-loop' and cfg.loop['writers'] == ('w1',)
    assert cfg.loop['step_timeout'] == 2700 and cfg.loop['max_failures'] == 3
    assert dict(cfg.loop['env']) == {'CARGO_TARGET_DIR': '/work/target', 'PATH': '/usr/bin:/bin'}
    assert (cfg.loop['mode'], cfg.loop['leads'], cfg.loop['fix_rounds'], cfg.loop['native_children']) == \
        ('direct', (), 2, 'off')


@pytest.mark.parametrize('loop', [
    'writers=["w1"]\nreviewers=["r1"]',                                  # branch is mandatory, no fallback
    'writers=["w1"]\nreviewers=["r1"]\nbranch="main"',                    # project branch is not a loop ref
    'writers=["w1"]\nreviewers=["r1"]\nbranch="bad..ref"',
    'writers=["nope"]\nreviewers=["r1"]\nbranch="forge/loop"',
    'writers=["off"]\nreviewers=["r1"]\nbranch="forge/loop"',
    'writers=[]\nreviewers=["r1"]\nbranch="forge/loop"',
    'writers=["w1"]\nreviewers=["r1"]\nbranch="forge/loop"\nenv={API_TOKEN="x"}',
    'writers=["w1"]\nreviewers=["r1"]\nbranch="forge/loop"\nenv={HOME="/tmp"}',
    'writers=["w1"]\nreviewers=["r1"]\nbranch="forge/loop"\nenv={lower="x"}',
    'writers=["w1"]\nreviewers=["r1"]\nbranch="forge/loop"\nstep_timeout=0',
    'writers=["w1"]\nreviewers=["r1"]\nbranch="forge/loop"\ncreate_branch=true',
])
def test_loop_table_rejects_unsafe_values(operator_config, loop):
    with pytest.raises(ConfigError):
        load_config(operator_config(LOOP_PROFILES + '[loop]\n' + loop + '\n'))


# SIMULATED: lead-mode config and Terra registration checks; no native process is launched.
LEAD = ('[loop]\nmode="lead"\nbranch="forge/lead"\nleads=["sonnet-high", "sol-high"]\n'
        'writers=["luna-xhigh", "haiku-high", "sonnet-high", "sol-high"]\nreviewers=["sol-high", "sonnet-high", "opus-high"]\n')


def test_lead_mode_keys_and_tier_writers(operator_config):
    config = load_config(operator_config(LEAD + 'fix_rounds=0\nnative_children="verified"\n'))
    assert (config.loop['mode'], config.loop['leads'], config.loop['fix_rounds'], config.loop['native_children']) == \
        ('lead', ('sonnet-high', 'sol-high'), 0, 'verified')
    assert tier_writers(config, 'cheap') == ('luna-xhigh', 'haiku-high')
    assert tier_writers(config, 'normal') == ('sonnet-high', 'sol-high')   # Sol normal+strong is a normal writer
    with pytest.raises(ConfigError):
        tier_writers(config, 'strong')
    assert load_config(operator_config(LEAD)).loop['fix_rounds'] == 2


@pytest.mark.parametrize('loop', [
    'writers=["sol-high"]\nreviewers=["sonnet-high"]\nbranch="forge/l"\nleads=["sol-high"]',      # lead-only in direct
    'writers=["sol-high"]\nreviewers=["sonnet-high"]\nbranch="forge/l"\nfix_rounds=2',
    'writers=["sol-high"]\nreviewers=["sonnet-high"]\nbranch="forge/l"\nmode="auto"',
    'writers=["sol-high"]\nreviewers=["sonnet-high"]\nbranch="forge/l"\nnative_children="on"',
    'writers=["sol-high"]\nreviewers=["sonnet-high"]\nbranch="forge/l"\nexecutors=["sol-high"]',
])
def test_direct_rejects_lead_only_and_invalid_keys(operator_config, loop):
    with pytest.raises(ConfigError):
        load_config(operator_config('[loop]\n' + loop + '\n'))


@pytest.mark.parametrize('loop', [
    LEAD + 'fix_rounds=true\n', LEAD + 'fix_rounds=3\n', LEAD + 'fix_rounds=-1\n', LEAD + 'fix_rounds="2"\n',
    LEAD.replace('"sol-high"]\nreviewers', '"sol-high", "opus-high"]\nreviewers'),         # opus is strong-only
    LEAD.replace('leads=["sonnet-high", "sol-high"]', 'leads=["haiku-high"]'),           # haiku is cheap-only
    LEAD.replace('"opus-high"]', '"haiku-high"]'),
    LEAD.replace('leads=["sonnet-high", "sol-high"]', 'leads=[]'),
    LEAD.replace('leads=["sonnet-high", "sol-high"]', 'leads=["sol-high", "sol-high"]'),
    LEAD.replace('leads=["sonnet-high", "sol-high"]\n', ''),
    '[loop]\nmode="lead"\nbranch="forge/lead"\nleads=["sol-high"]\nwriters=["luna-xhigh"]\nreviewers=["sol-high"]\n',
])
def test_lead_mode_rejects_tier_family_and_number_errors(operator_config, loop):
    with pytest.raises(ConfigError):
        load_config(operator_config(loop))


TERRA = ('[[profile]]\nid="terra"\nharness="codex"\nmodel="gpt-6.1-terra"\neffort="low"\nfamily="openai"\n'
         'tiers=["normal"]\naccount="codex"\nenabled=false\n')


def test_terra_default_unchanged_and_exact_disabled_tuple_accepted(operator_config):
    default = load_config(operator_config()).profiles[-1]
    assert default == cfg.Profile('terra', 'codex', 'TBD', 'TBD', 'openai', ('strong',), False, 'codex')
    assert cfg.TERRA_STATE == 'TERRA_DISCOVERED_REGISTRATION_PENDING' and cfg.TERRA_STATE in cfg.TERRA_STATES
    assert load_config(operator_config(TERRA)).profiles == (cfg.TERRA_PROFILE,)


@pytest.mark.parametrize('old, new', [
    ('gpt-6.1-terra', 'gpt-5.6-terra'), ('"normal"', '"strong"'), ('"normal"', '"normal", "strong"'),
    ('id="terra"', 'id="terra2"'), ('effort="low"', 'effort="high"'), ('account="codex"', 'account="claude"'),
    ('gpt-6.1-terra', 'gpt-6.1-sol'),                                         # low stays reserved for Terra
    ('enabled=false', 'enabled=true'),                                        # no proof present
])
def test_terra_low_only_for_exact_tuple(operator_config, old, new):
    with pytest.raises(ConfigError):
        load_config(operator_config(TERRA.replace(old, new)))


def proof(**changes):
    value = {'verdict': 'TERRA_VERIFIED_NORMAL', 'model': 'gpt-6.1-terra', 'effort': 'low', 'tier': 'normal',
             'account': 'codex', 'requested': {'model': 'gpt-6.1-terra', 'effort': 'low'},
             'actual': {'model': 'gpt-6.1-terra', 'effort': 'low'}, 'discovery': dict(cfg.TERRA_DISCOVERY_SHA256),
             'context_window': 400000, 'tools': ['shell'], 'at': cfg.TERRA_DISCOVERY_AT + 3000}
    value.update(changes)
    return value


def write_proof(operator_config, value):
    """SIMULATED evidence with real file/hash validation, never a native inference."""
    from forge import sandbox
    operator_config()
    root = cfg.state_root() / 'demo'
    path = root / 'smoke' / 'terra-proof.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    call = 'terra-proof-0123456789ab'
    binary = sandbox.resolve_binary('codex', None)
    info = {'path': str(binary), 'version': 'SIMULATED', 'sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}
    value.update(call=call, outcome='ok', model_match=True, capabilities=['text', 'readonly'], sandbox_probe='pass',
                 binding=cfg.terra_binding(cfg.TERRA_PROFILE, cfg.DEFAULT_HARNESS['codex'], info), evidence={})
    blobs = {'manifest': (f'calls/{call}/manifest.json', {'SIMULATED': True}),
             'result': (f'calls/{call}/result.json', {'call_id': call, 'outcome': 'ok',
                          'actual_model': 'gpt-6.1-terra', 'model_match': True,
                          'native_metadata': {'model': 'gpt-6.1-terra', 'effort': 'low',
                                              'context_window': 400000, 'tools': ['shell']}}),
             'sandbox': ('smoke/sandbox.json', {'status': 'pass', 'SIMULATED': True})}
    for key, (rel, blob) in blobs.items():
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(blob))
        dest.chmod(0o600)
        value['evidence'][key] = hashlib.sha256(dest.read_bytes()).hexdigest()
    for rel, sha in cfg.TERRA_DISCOVERY_SHA256.items():
        # Original artifacts must not be copied into tests or treated as live test evidence.
        # The fixture pins a synthetic catalog set instead.
        dest = root / 'smoke' / 'terra-discovery' / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text('SIMULATED ' + rel)
        dest.chmod(0o600)
    value['sandbox_manifest_sha256'] = value['evidence']['manifest']
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return path


@pytest.fixture(autouse=True)
def simulated_terra_catalog(monkeypatch):
    monkeypatch.setattr(cfg, 'TERRA_DISCOVERY_SHA256', {
        key: hashlib.sha256(('SIMULATED ' + key).encode()).hexdigest() for key in cfg.TERRA_DISCOVERY_SHA256})


NOW = cfg.TERRA_DISCOVERY_AT + 3600


@pytest.mark.parametrize('changes, now, ok', [
    ({}, NOW, True),
    ({'verdict': 'TERRA_DISCOVERED_REGISTRATION_PENDING'}, NOW, False),
    ({'actual': {'model': 'gpt-6.1-terra', 'effort': 'medium'}}, NOW, False),
    ({'actual': {'model': 'gpt-6.1-sol', 'effort': 'low'}}, NOW, False),
    ({'tier': 'strong'}, NOW, False),
    ({'discovery': {**cfg.TERRA_DISCOVERY_SHA256, 'terra-availability.md': '0' * 64}}, NOW, False),
    ({'context_window': 'UNKNOWN'}, NOW, False),
    ({'tools': 'UNKNOWN'}, NOW, False),
    ({'at': NOW - 25 * 3600}, NOW, False),                                       # stale proof
    ({'at': NOW + 60}, NOW, False),                                              # future proof
    ({'at': NOW + 86000}, NOW + 86400, False),                                   # discovery stale, no refresh
    ({'at': NOW + 86000, 'refreshed_discovery': {'at': NOW + 85000, 'hashes': {'x': 'a' * 64}}}, NOW + 86400, True),
])
def test_verify_terra_proof_cases(operator_config, changes, now, ok):
    path = write_proof(operator_config, proof(**changes))
    assert verify_terra_proof(path, cfg.TERRA_PROFILE, now)[0] is ok


def test_verify_terra_proof_missing_symlink_and_foreign_path(operator_config, tmp_path):
    path = write_proof(operator_config, proof())
    assert verify_terra_proof(path, cfg.Profile('sol-high', 'codex', 'gpt-6.1-sol', 'high', 'openai', ('normal',)),
                              NOW)[0] is False
    foreign = tmp_path / 'smoke' / 'terra-proof.json'
    foreign.parent.mkdir()
    foreign.write_text(path.read_text())
    assert verify_terra_proof(foreign, cfg.TERRA_PROFILE, NOW) == (
        False, 'proof must be <state_root>/<slug>/smoke/terra-proof.json without symlinks')
    path.unlink()
    os.symlink(foreign, path)
    assert verify_terra_proof(path, cfg.TERRA_PROFILE, NOW)[0] is False
    path.unlink()
    assert verify_terra_proof(path, cfg.TERRA_PROFILE, NOW) == (False, 'proof missing')


def test_enabled_terra_requires_verified_proof(operator_config):
    enabled = TERRA.replace('enabled=false', 'enabled=true')
    with pytest.raises(ConfigError, match='proof missing'):
        load_config(operator_config(enabled))
    now = time.time()
    write_proof(operator_config, proof(at=now - 60, refreshed_discovery={'at': now - 60, 'hashes': {'x': 'b' * 64}}))
    assert load_config(operator_config(enabled)).profiles[0].enabled is True


@pytest.mark.parametrize('metadata', [None, {}, {'model': 'gpt-6.1-terra'},
    {'model': 'gpt-6.1-terra', 'effort': 'low', 'context_window': 'UNKNOWN', 'tools': ['shell']},
    {'model': 'gpt-6.1-terra', 'effort': 'low', 'context_window': 400000, 'tools': 'UNKNOWN'},
    {'model': 'gpt-6.1-terra', 'effort': 'medium', 'context_window': 400000, 'tools': ['shell']},
    {'model': 'gpt-6.1-sol', 'effort': 'low', 'context_window': 400000, 'tools': ['shell']},
    {'model': 'gpt-6.1-terra', 'effort': 'low', 'context_window': 200000, 'tools': ['shell']},
    {'model': 'gpt-6.1-terra', 'effort': 'low', 'context_window': 400000, 'tools': ['other-tool']},
])
def test_terra_enable_rejects_metadata_absent_or_mismatched_in_hashed_result(operator_config, metadata, monkeypatch):
    # Test the metadata gate at the same simulated instant used by verify_terra_proof.
    monkeypatch.setattr(cfg.time, 'time', lambda: NOW)
    path = write_proof(operator_config, proof())
    data = json.loads(path.read_text())
    result_path = path.parent.parent / 'calls' / data['call'] / 'result.json'
    result = json.loads(result_path.read_text())
    result.pop('native_metadata')
    if metadata is not None:
        result['native_metadata'] = metadata
    result_path.write_text(json.dumps(result))
    data['evidence']['result'] = hashlib.sha256(result_path.read_bytes()).hexdigest()
    path.write_text(json.dumps(data))
    assert verify_terra_proof(path, cfg.TERRA_PROFILE, NOW) == (
        False, 'observed native metadata missing or mismatched in hashed result')
    with pytest.raises(ConfigError, match='observed native metadata'):
        load_config(operator_config(TERRA.replace('enabled=false', 'enabled=true')))
