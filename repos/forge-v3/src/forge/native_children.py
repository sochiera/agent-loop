"""Static task-local native child policy (plan §6). No scheduler, no child loop.

A policy allows at most one read-only depth-1 child pinned to the exact parent
model/effort, and only on a route with live spawn/result proof. Anything unproven
is mode 'off', which adapters turn into explicit per-invocation disable controls.
"""

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Literal

SETTINGS = ('off', 'verified')
# Live spawn/result proof per harness. None exists in this unit; a help/schema demo is not proof.
# Grok is never admissible here (wrapper blocks the account) and stays off.
VERIFIED_ROUTES = {'codex': False, 'claude': False, 'grok': False}


@dataclass(frozen=True)
class ChildPolicy:
    mode: Literal['off', 'verified']
    harness: str
    model: str
    effort: str
    max_children: int = 1
    depth: int = 1
    readonly: bool = True


def policy_for(profile, setting: str, verified: bool) -> ChildPolicy:
    if setting not in SETTINGS:
        raise ValueError(f'native_children must be one of {SETTINGS}')
    proven = (setting == 'verified' and verified is True and profile.harness != 'grok'
              and VERIFIED_ROUTES.get(profile.harness) is True)
    return ChildPolicy('verified' if proven else 'off', profile.harness, profile.model, profile.effort)


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def policy_hash(policy: ChildPolicy) -> str:
    return _digest(asdict(policy))


def capability_fingerprint(harness: str, binary_version: str, model: str, effort: str, settings_hash: str) -> str:
    """Key for child verification; any change disables children until reproved."""
    return _digest({'harness': harness, 'binary_version': binary_version, 'model': model, 'effort': effort,
                    'settings_hash': settings_hash})


def child_controls(policy: ChildPolicy | None, harness: str, model: str, effort: str, off: list[str]) -> list[str]:
    """Adapter args for a policy: None => [] (legacy argv), 'off' => explicit task-local disables."""
    if policy is None:
        return []
    if (policy.harness, policy.model, policy.effort) != (harness, model, effort):
        raise ValueError('child policy must pin the exact parent harness, model and effort')
    if policy.mode == 'off':
        return list(off)
    if not VERIFIED_ROUTES.get(harness):
        raise ValueError(f'native children requested on unverified route: {harness}')
    raise ValueError(f'no pinned native child controls implemented for {harness}')


def capability_for(profile, binary_info, harness):
    """Current route evidence: fingerprinted but never promoted by help/config alone."""
    settings = {'binds_rw': list(harness['binds_rw']), 'binds_ro': list(harness['binds_ro']),
                'runtime_dir': harness.get('runtime_dir'), 'policy': policy_hash(policy_for(profile, 'off', False))}
    pins = {'binary': binary_info, 'settings': settings, 'native_schema': 'UNVERIFIED',
            'harness': profile.harness, 'model': profile.model, 'effort': profile.effort}
    return {'status': 'NOT_RUN_ADMISSION_BLOCK' if profile.harness == 'grok' else 'UNVERIFIED',
            'fingerprint': _digest(pins), 'pins': pins, 'children': 'off',
            'reason': 'Static child override pins and real spawn/result evidence are not established.'}
