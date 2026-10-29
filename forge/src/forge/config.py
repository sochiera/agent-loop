"""Strict, operator-owned TOML configuration; never discover config in a target."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from string import Formatter
import time
import tomllib
from types import MappingProxyType
from typing import Mapping


class ConfigError(ValueError):
    """Invalid or untrusted operator configuration."""


@dataclass(frozen=True)
class Profile:
    id: str
    harness: str
    model: str
    effort: str
    family: str
    tiers: tuple[str, ...]
    enabled: bool = True
    account: str = ""


DEFAULT_PROFILES = (
    Profile("luna-xhigh", "codex", "gpt-6-luna", "xhigh", "openai", ("cheap",), account="codex"),
    Profile("haiku-high", "claude", "claude-haiku-5-5", "high", "anthropic", ("cheap",), account="claude"),
    Profile("sol-high", "codex", "gpt-6.1-sol", "high", "openai", ("normal", "strong"), account="codex"),
    Profile("sonnet-high", "claude", "claude-sonnet-5-5", "high", "anthropic", ("normal",), account="claude"),
    Profile("grok-high", "grok", "grok-4.7", "high", "xai", ("normal",), account="grok"),
    Profile("opus-high", "claude", "claude-opus-5-5", "high", "anthropic", ("strong",), account="claude"),
    Profile("terra", "codex", "TBD", "TBD", "openai", ("strong",), False, "codex"),
)
# Terra 6.1 (plan §3/§7): only this exact discovered operator tuple may use effort low.
TERRA_PROFILE = Profile("terra", "codex", "gpt-6.1-terra", "low", "openai", ("normal",), False, "codex")
TERRA_STATES = ('TERRA_DISCOVERED_REGISTRATION_PENDING', 'TERRA_VERIFIED_NORMAL', 'TERRA_UNAVAILABLE_EVIDENCE')
TERRA_STATE = TERRA_STATES[0]
TERRA_STATE_REASON = 'local registration reviewed helper and one inference pending'
TERRA_DISCOVERY_SHA256 = {
    'terra-availability.md': 'bc1dc8aff12693a1a707e0b7e3a71a5d33f84f4c32f4db9be3673c594b6b3f98',
    'terra-evidence/catalog-full.json': '38e0c265acc872b2f13c2ef83147c987486b65cad64cfbbeee59367a211d277b',
    'terra-evidence/terra61-entry.json': '255746ebcbd6f4313282b3d545153c290c34fad15539e041253c496028ce0985',
    'terra-evidence/catalog-live2.json': '6d54e7827ddbe50728cd4b6da045c2ed5b1570c6765650c092a9beb7c68a4f48',
    'terra-evidence/live-models-cache.json': 'd56c8b5270e243edc02454649b0b7e4fe682a1695d32e2842bb267f5df22fc5a',
}
TERRA_DISCOVERY_AT = 1793046729.0   # 2026-10-26T20:32:09Z native live fetch
TERRA_PROOF_TTL = 24 * 3600
DEFAULT_BUDGETS = {
    "easy": {"plan_attempts": 2, "impl_attempts": 2, "fix_rounds": 2},
    "hard": {"plan_attempts": 2, "impl_attempts": 2, "fix_rounds": 3},
    "escalation": {"fix_rounds": 2}, "role_attempts": 2, "protocol_retries": 1,
    "profile_strikes": 2, "rebases": 2, "stall_batches": 2,
}
DEFAULT_TIMEOUTS = {"po_read": 1800, "po": 2400, "architect": 1800, "plan": 900,
                    "implement": 2700, "review": 1200, "probe": 120}
DEFAULT_TURNS = dict(zip(DEFAULT_TIMEOUTS, (40, 60, 40, 30, 80, 40, 2)))
DEFAULT_COMMANDS = {
    "test": ["python3", "-m", "pytest", "-q"],
    "test_one": ["python3", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                 "--junitxml={junit}", "{node}"], "lint": [],
}
DEFAULT_HARNESS = {
    "codex": {"binds_rw": ["~/.codex"], "binds_ro": []},
    "claude": {"binds_rw": ["~/.claude", "~/.claude.json"], "binds_ro": ["~/.local/share/claude"]},
    "grok": {"binds_rw": ["~/.grok"], "binds_ro": []},
}


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(value)
    return value


@dataclass(frozen=True)
class Config:
    path: Path
    slug: str
    target_repo: Path
    branch: str
    sources: tuple[str, ...]
    protected: tuple[str, ...]
    test_paths: tuple[str, ...]
    parallel: int
    commands: Mapping
    execution: Mapping
    accounts: Mapping
    harness: Mapping
    budgets: Mapping
    timeouts: Mapping
    max_turns: Mapping
    profiles: tuple[Profile, ...]
    state_dir: Path
    loop: Mapping | None = None


def config_root() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")).expanduser().resolve() / "forge" / "projects"


def state_root() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")).expanduser().resolve() / "forge"


def _table(value, allowed, label):
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be a table")
    unknown = value.keys() - set(allowed)
    if unknown:
        raise ConfigError(f"unknown {label} keys: {', '.join(sorted(unknown))}")
    return value


def _string(value, label):
    if not isinstance(value, str) or not value or '\x00' in value:
        raise ConfigError(f"{label} must be a nonempty string without NUL")
    return value


def _bool(value, label):
    if type(value) is not bool:
        raise ConfigError(f"{label} must be boolean")
    return value


def _positive(value, label):
    if type(value) is not int or value < 1:
        raise ConfigError(f"{label} must be a positive integer")
    return value


def _strings(value, label, paths=False):
    if not isinstance(value, list):
        raise ConfigError(f"{label} must be an array")
    for item in value:
        _string(item, label)
        if paths and (PurePosixPath(item).is_absolute() or '..' in PurePosixPath(item).parts):
            raise ConfigError(f"{label} paths must stay relative to the target")
    return tuple(value)


def _merged_numbers(raw, defaults, label):
    _table(raw, defaults, label)
    result = {}
    for key, default in defaults.items():
        value = raw.get(key, default)
        result[key] = (_merged_numbers(value, default, f"{label}.{key}") if isinstance(default, dict)
                       else _positive(value, f"{label}.{key}"))
    return result


def _commands(raw):
    _table(raw, DEFAULT_COMMANDS, "commands")
    result = {}
    for key, default in DEFAULT_COMMANDS.items():
        args = _strings(raw.get(key, default), f"commands.{key}")
        if key != "lint" and not args:
            raise ConfigError(f"commands.{key} cannot be empty")
        for arg in args:
            try:
                fields = list(Formatter().parse(arg))
            except ValueError as exc:
                raise ConfigError(f"invalid command placeholder: {arg}") from exc
            for _, field, spec, conversion in fields:
                if field is not None and (key != "test_one" or field not in {"node", "junit"} or spec or conversion):
                    raise ConfigError(f"unsupported command placeholder: {arg}")
        if args and ('{' in args[0] or '}' in args[0]):
            raise ConfigError("command executable cannot be a placeholder")
        result[key] = args
    return result


def _ref(branch, label):
    if (branch.startswith(('-', '/')) or branch.endswith(('/', '.', '.lock')) or
            any(c in branch for c in ('..', '@{', '//')) or
            re.search(r'[\s~^:?*\[\\\x00-\x1f\x7f]', branch) or
            any(p.startswith('.') or p.endswith('.lock') for p in branch.split('/')) or branch == '@'):
        raise ConfigError(f"invalid {label} ref")
    return branch


LOOP_TIERS = {"leads": {"normal", "strong"}, "reviewers": {"normal", "strong"}, "writers": {"cheap", "normal"}}


def _loop(raw, branch, profiles, accounts):
    """Optional [loop] table; mandatory only for forge.loop.run_loop."""
    if raw is None:
        return None
    _table(raw, {"writers", "reviewers", "branch", "step_timeout", "max_failures", "env",
                 "mode", "leads", "fix_rounds", "native_children",
                 "sources_remote", "sources_branch", "sources_script"}, "loop")
    mode = raw.get("mode", "direct")
    if mode not in ("direct", "lead"):
        raise ConfigError("loop.mode must be direct or lead")
    if mode == "direct" and {"leads", "fix_rounds"} & raw.keys():
        raise ConfigError("loop.leads and loop.fix_rounds are lead-only keys; set loop.mode = \"lead\"")
    fix_rounds = raw.get("fix_rounds", 2)
    if type(fix_rounds) is not int or not 0 <= fix_rounds <= 2:
        raise ConfigError("loop.fix_rounds must be an integer 0..2")
    native_children = raw.get("native_children", "off")
    if native_children not in ("off", "verified"):
        raise ConfigError("loop.native_children must be off or verified")
    if "branch" not in raw:
        raise ConfigError("loop.branch is required; there is no fallback to the project branch")
    loop_branch = _ref(_string(raw["branch"], "loop.branch"), "loop.branch")
    if loop_branch == branch:
        raise ConfigError("loop.branch must be a dedicated ref, not the project branch")
    by_id = {p.id: p for p in profiles}
    lists = {}
    for key in ("writers", "reviewers") + (("leads",) if mode == "lead" else ()):
        ids = _strings(raw.get(key), f"loop.{key}")
        if not ids or len(set(ids)) != len(ids):
            raise ConfigError(f"loop.{key} must be a nonempty list of distinct profile ids")
        for ident in ids:
            profile = by_id.get(ident)
            if profile is None or not profile.enabled or not accounts[profile.account]["enabled"]:
                raise ConfigError(f"loop.{key} references unknown or disabled profile {ident}")
            if mode == "lead" and not LOOP_TIERS[key] & set(profile.tiers):
                raise ConfigError(f"loop.{key} profile {ident} is not eligible for {'/'.join(sorted(LOOP_TIERS[key]))}")
        lists[key] = ids
    if mode == "lead":
        for ident in lists["writers"]:
            if not any(by_id[r].family != by_id[ident].family for r in lists["reviewers"]):
                raise ConfigError(f"loop writer {ident} has no reviewer of a different family")
    env = raw.get("env", {})
    if not isinstance(env, dict):
        raise ConfigError("loop.env must be a table")
    for key, value in env.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or re.search(r"KEY|TOKEN|SECRET|PASSWORD|AUTH", key) or key == "HOME":
            raise ConfigError(f"loop.env variable {key} is not allowed")
        _string(value, f"loop.env.{key}")
    sources = _sources(raw)
    return {"writers": lists["writers"], "reviewers": lists["reviewers"], "branch": loop_branch,
            "step_timeout": _positive(raw.get("step_timeout", 2700), "loop.step_timeout"),
            "max_failures": _positive(raw.get("max_failures", 3), "loop.max_failures"), "env": dict(env),
            "mode": mode, "leads": lists.get("leads", ()), "fix_rounds": fix_rounds,
            "native_children": native_children, **sources}


def _sources(raw):
    """Optional product-sources delivery target: all three keys or none (fail closed on a partial set)."""
    keys = ("sources_remote", "sources_branch", "sources_script")
    present = [k for k in keys if k in raw]
    if not present:
        return dict.fromkeys(keys)
    if len(present) != len(keys):
        raise ConfigError("loop.sources_remote, loop.sources_branch and loop.sources_script must be set together")
    remote = _string(raw["sources_remote"], "loop.sources_remote")
    if remote.startswith('-') or re.search(r'\s', remote):
        raise ConfigError("loop.sources_remote must not start with '-' or contain whitespace")
    script = _string(raw["sources_script"], "loop.sources_script")
    if not PurePosixPath(script).is_absolute():
        raise ConfigError("loop.sources_script must be an absolute path")
    return {"sources_remote": remote,
            "sources_branch": _ref(_string(raw["sources_branch"], "loop.sources_branch"), "loop.sources_branch"),
            "sources_script": script}


def tier_writers(config, tier) -> tuple[str, ...]:
    """Ordered loop writers whose profile tiers contain the lead task tier (membership, never model names)."""
    if tier not in ("cheap", "normal"):
        raise ConfigError("lead task tier must be cheap or normal")
    by_id = {p.id: p for p in config.profiles}
    return tuple(w for w in config.loop["writers"] if tier in by_id[w].tiers)


def _terra_tuple(profile) -> bool:
    """Exact discovered Terra operator tuple, ignoring only the enabled flag."""
    return (profile.id, profile.harness, profile.model, profile.effort, profile.family, tuple(profile.tiers),
            profile.account) == (TERRA_PROFILE.id, TERRA_PROFILE.harness, TERRA_PROFILE.model, TERRA_PROFILE.effort,
                                 TERRA_PROFILE.family, TERRA_PROFILE.tiers, TERRA_PROFILE.account)


def _sha_map(value) -> bool:
    return (isinstance(value, dict) and bool(value) and all(isinstance(k, str) and isinstance(v, str)
            and re.fullmatch(r"[0-9a-f]{64}", v) for k, v in value.items()))


def terra_binding(profile, harness, binary):
    """Small stable pin of the settings relevant to the one Terra route."""
    from forge.native_children import policy_for, policy_hash
    return {'binary': binary, 'model': profile.model, 'effort': profile.effort, 'account': profile.account,
            'binds_rw': list(harness['binds_rw']), 'binds_ro': list(harness['binds_ro']),
            'runtime_dir': harness.get('runtime_dir'), 'children': policy_hash(policy_for(profile, 'off', False))}


def _proof_bytes(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError('evidence must be an operator-owned regular file')
        return stream.read()


def terra_metadata_matches(proof, result):
    """The proof's values must exist, unchanged, in the hashed native result."""
    if not isinstance(result, dict):
        return False
    meta = result.get('native_metadata')
    if not isinstance(meta, dict):
        return False
    return (meta.get('model') == proof['requested']['model'] == result.get('actual_model')
            and meta.get('effort') == proof['requested']['effort'] == proof['actual']['effort']
            and type(meta.get('context_window')) is int and meta['context_window'] > 0
            and isinstance(meta.get('tools'), list) and bool(meta['tools'])
            and all(isinstance(tool, str) and bool(tool.strip()) and tool != 'UNKNOWN' for tool in meta['tools'])
            and meta['context_window'] == proof['context_window'] and meta['tools'] == proof['tools'])


def verify_terra_proof(proof_path: Path, profile, now: float, *, harness=None) -> tuple[bool, str]:
    """Read-only check of the controller-owned Terra proof (plan §7.2/7.3); never executes anything."""
    proof_path = Path(proof_path)
    if not _terra_tuple(profile):
        return False, "profile is not the exact Terra tuple"
    root = state_root()
    if (proof_path.name != "terra-proof.json" or proof_path.parent.name != "smoke" or not proof_path.is_absolute()
            or proof_path.parent.parent.parent != root or os.path.realpath(proof_path.parent) != str(proof_path.parent)):
        return False, "proof must be <state_root>/<slug>/smoke/terra-proof.json without symlinks"
    try:
        fd = os.open(proof_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
                return False, "proof is not an operator-owned regular file"
            proof = json.loads(stream.read(1 << 20))
    except FileNotFoundError:
        return False, "proof missing"
    except (OSError, ValueError) as exc:
        return False, f"proof unreadable: {type(exc).__name__}"
    if not isinstance(proof, dict):
        return False, "proof is not an object"
    if proof.get("verdict") != "TERRA_VERIFIED_NORMAL":
        return False, f"verdict is {proof.get('verdict')!r}"
    pins = (profile.model, profile.effort)
    for key in ("requested", "actual"):
        item = proof.get(key)
        if not isinstance(item, dict) or (item.get("model"), item.get("effort")) != pins:
            return False, f"{key} model/effort mismatch"
    if (proof.get("model"), proof.get("effort"), proof.get("account")) != (*pins, profile.account):
        return False, "proof model/effort/account mismatch"
    if proof.get("tier") != "normal":
        return False, "proof tier must be normal"
    if proof.get("discovery") != TERRA_DISCOVERY_SHA256:
        return False, "adopted discovery hashes mismatch"
    at = proof.get("at")
    if type(at) not in (int, float) or not 0 <= now - at < TERRA_PROOF_TTL:
        return False, "proof stale or from the future"
    if now - TERRA_DISCOVERY_AT >= TERRA_PROOF_TTL:
        refreshed = proof.get("refreshed_discovery")
        if (not isinstance(refreshed, dict) or not _sha_map(refreshed.get("hashes"))
                or type(refreshed.get("at")) not in (int, float) or not 0 <= now - refreshed["at"] < TERRA_PROOF_TTL):
            return False, "discovery stale and no fresh refreshed hashes recorded"
    for key in ("context_window", "tools"):
        if proof.get(key) in (None, "", "UNKNOWN", [], {}):
            return False, f"{key} metadata unknown"
    try:
        state_dir = proof_path.parent.parent
        call_id = proof['call']
        if not isinstance(call_id, str) or not re.fullmatch('terra-proof-[0-9a-f]{12}', call_id):
            return False, 'invalid proof call identity'
        if (proof.get('outcome') != 'ok' or proof.get('model_match') is not True
                or proof.get('capabilities') != ['text', 'readonly'] or proof.get('sandbox_probe') != 'pass'):
            return False, 'incomplete native/sandbox evidence'
        for rel, expected in TERRA_DISCOVERY_SHA256.items():
            copied = state_dir / 'smoke' / 'terra-discovery' / rel
            if (os.path.realpath(copied) != str(copied)
                    or hashlib.sha256(_proof_bytes(copied)).hexdigest() != expected):
                return False, 'discovery bytes changed or missing'
        required = {'manifest': f'calls/{call_id}/manifest.json', 'result': f'calls/{call_id}/result.json',
                    'sandbox': 'smoke/sandbox.json'}
        evidence = proof['evidence']
        if set(evidence) != set(required):
            return False, 'incomplete evidence hashes'
        blobs = {}
        for key, rel in required.items():
            path = state_dir / rel
            if os.path.realpath(path) != str(path):
                return False, 'evidence path has symlinks'
            data = _proof_bytes(path)
            if evidence[key] != hashlib.sha256(data).hexdigest():
                return False, 'evidence bytes changed'
            blobs[key] = json.loads(data)
        if (blobs['result']['call_id'] != call_id or blobs['result']['outcome'] != 'ok'
                or blobs['result']['actual_model'] != profile.model or blobs['result']['model_match'] is not True
                or blobs['sandbox'].get('status') != 'pass'):
            return False, 'native evidence identity mismatch'
        if not terra_metadata_matches(proof, blobs['result']):
            return False, 'observed native metadata missing or mismatched in hashed result'
        binary = proof['binding']['binary']
        if (set(binary) != {'path', 'version', 'sha256'} or not binary['version']
                or not Path(binary['path']).is_absolute()
                or hashlib.sha256(Path(binary['path']).read_bytes()).hexdigest() != binary['sha256']):
            return False, 'binary fingerprint changed'
        if harness is not None:
            from forge.sandbox import resolve_binary
            resolved = resolve_binary(profile.harness, harness.get('binary'))
            if str(resolved) != binary['path'] or proof['binding'] != terra_binding(profile, harness, binary):
                return False, 'settings/binary binding changed'
        manifest_sha = proof['evidence']['manifest']
        if proof.get('sandbox_manifest_sha256') != manifest_sha:
            return False, 'sandbox fingerprint mismatch'
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
        return False, 'proof artifacts missing or invalid'
    return True, "verified"


def load_config(path: str | Path) -> Config:
    """Read one explicit trusted file beneath XDG_CONFIG_HOME/forge/projects.

    No target-local overrides or automatic current-directory discovery exist.
    Bind safety and smoke eligibility are enforced by later sandbox/doctor phases.
    """
    path = Path(path).expanduser().resolve()
    root = config_root()
    if path.parent != root or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*\.toml", path.name):
        raise ConfigError(f"config must be a named TOML file directly in {root}")
    try:
        # Validate and read the same file descriptor, without following a final symlink.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise ConfigError("config must be a regular operator-owned file, not group/world writable")
            raw = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read operator config: {exc}") from exc
    _table(raw, {"target_repo", "branch", "sources", "protected", "test_paths", "parallel", "commands",
                 "exec", "accounts", "harness", "budgets", "timeouts", "max_turns", "profile", "loop"}, "config")
    target = Path(_string(raw.get("target_repo"), "target_repo")).expanduser()
    if not target.is_absolute() or not target.is_dir():
        raise ConfigError("target_repo must be an existing absolute directory")
    target = target.resolve()
    if path.is_relative_to(target):
        raise ConfigError("operator config must be outside the target repo")
    branch = _ref(_string(raw.get("branch", "main"), "branch"), "branch")
    parallel = _positive(raw.get("parallel", 1), "parallel")
    if parallel != 1:
        raise ConfigError("only parallel=1 is supported")
    accounts = raw.get("accounts", {k: {"enabled": True} for k in DEFAULT_HARNESS})
    if not isinstance(accounts, dict) or not accounts:
        raise ConfigError("accounts must be a nonempty table")
    for name, account in accounts.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ConfigError("invalid account id")
        _table(account, {"enabled"}, f"accounts.{name}")
        _bool(account.get("enabled", True), f"accounts.{name}.enabled")
    accounts = {k: {"enabled": v.get("enabled", True)} for k, v in accounts.items()}
    harness_raw = _table(raw.get("harness", {}), DEFAULT_HARNESS, "harness")
    harness = {}
    for name, defaults in DEFAULT_HARNESS.items():
        entry = _table(harness_raw.get(name, {}), {*defaults, 'binary', 'runtime_dir'}, f"harness.{name}")
        harness[name] = {k: _strings(entry.get(k, v), f"harness.{name}.{k}") for k, v in defaults.items()}
        # Optional operator-vetted executable / release dir; canonical bind validation happens in sandbox.py.
        for key in ('binary', 'runtime_dir'):
            harness[name][key] = _string(entry[key], f"harness.{name}.{key}") if key in entry else None
    execution = _table(raw.get("exec", {}), {"gate_network", "ro_binds"}, "exec")
    execution = {"gate_network": _bool(execution.get("gate_network", False), "exec.gate_network"),
                 "ro_binds": _strings(execution.get("ro_binds", []), "exec.ro_binds")}
    profiles = DEFAULT_PROFILES
    if "profile" in raw:
        if not isinstance(raw['profile'], list) or not raw['profile']:
            raise ConfigError("profile must be a nonempty array of tables")
        profiles = []
        for item in raw['profile']:
            _table(item, {"id", "harness", "model", "effort", "family", "tiers", "enabled", "account"}, "profile")
            required = {k: _string(item.get(k), f"profile.{k}") for k in ('id', 'harness', 'model', 'effort', 'family')}
            tiers = _strings(item.get('tiers'), 'profile.tiers')
            profiles.append(Profile(**required, tiers=tiers, enabled=_bool(item.get('enabled', True), 'profile.enabled'),
                                    account=_string(item.get('account', required['harness']), 'profile.account')))
    seen = set()
    for profile in profiles:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", profile.id) or profile.id in seen:
            raise ConfigError("invalid or duplicate profile id")
        seen.add(profile.id)
        families = {"codex": "openai", "claude": "anthropic", "grok": "xai"}
        if profile.harness not in families or profile.family != families[profile.harness]:
            raise ConfigError("profile harness/family mismatch")
        if profile.account not in accounts:
            raise ConfigError("profile references unknown account")
        if not profile.tiers or len(set(profile.tiers)) != len(profile.tiers) or set(profile.tiers) - {'cheap', 'normal', 'strong'}:
            raise ConfigError("invalid profile tiers")
        if profile.enabled and (profile.model == 'TBD' or profile.effort == 'TBD'):
            raise ConfigError("unverified placeholder profile cannot be enabled")
        terra = _terra_tuple(profile)
        if not terra and ('terra' in profile.model.lower() or (profile.id == 'terra' and profile.model != 'TBD')):
            raise ConfigError("Terra requires the exact discovered gpt-6.1-terra/low/normal operator tuple")
        if profile.effort not in {'medium', 'high', 'xhigh'} and not (not profile.enabled and profile.effort == 'TBD') \
                and not terra:
            raise ConfigError("unsupported effort")
        if terra and profile.enabled:
            ok, reason = verify_terra_proof(state_root() / path.stem / 'smoke' / 'terra-proof.json', profile, time.time(),
                                            harness=harness[profile.harness])
            if not ok:
                raise ConfigError(f"Terra cannot be enabled without verified proof: {reason}")
        if profile.effort == 'xhigh' and (profile.harness != 'codex' or profile.model != 'gpt-6-luna'):
            raise ConfigError("xhigh is reserved for Luna")
        if profile.model == 'claude-haiku-5-5' and profile.effort != 'high':
            raise ConfigError("Haiku requires high effort")
    timeouts_raw = dict(raw.get('timeouts', {})) if isinstance(raw.get('timeouts', {}), dict) else None
    if timeouts_raw is None:
        raise ConfigError("timeouts must be a table")
    # Accept the plan's nested [timeouts.max_turns] spelling as well as root max_turns.
    nested_turns = timeouts_raw.pop('max_turns', None)
    if nested_turns is not None and 'max_turns' in raw:
        raise ConfigError("max_turns specified twice")
    return Config(path, path.stem, target, branch,
                  _strings(raw.get('sources', ['docs/source/**/*.md']), 'sources', True),
                  _strings(raw.get('protected', ['docs/source/**', 'docs/forge/intent/**', 'docs/forge/adr/**']), 'protected', True),
                  _strings(raw.get('test_paths', ['tests/**']), 'test_paths', True), parallel,
                  _freeze(_commands(raw.get('commands', {}))), _freeze(execution), _freeze(accounts), _freeze(harness),
                  _freeze(_merged_numbers(raw.get('budgets', {}), DEFAULT_BUDGETS, 'budgets')),
                  _freeze(_merged_numbers(timeouts_raw, DEFAULT_TIMEOUTS, 'timeouts')),
                  _freeze(_merged_numbers(raw.get('max_turns', nested_turns if nested_turns is not None else {}), DEFAULT_TURNS, 'max_turns')),
                  tuple(profiles), state_root() / path.stem, _freeze(_loop(raw.get('loop'), branch, profiles, accounts)))
