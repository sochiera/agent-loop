"""Native child policy. All inputs SIMULATED: no live child spawn/result proof exists."""

import pytest

from forge.config import DEFAULT_PROFILES
from forge.native_children import (VERIFIED_ROUTES, ChildPolicy, capability_fingerprint, child_controls, policy_for,
                                   policy_hash)


@pytest.mark.parametrize('profile', DEFAULT_PROFILES, ids=lambda p: p.id)
def test_unproven_routes_downgrade_to_off_with_parent_pin(profile):
    for setting in ('off', 'verified'):
        for verified in (False, True):
            policy = policy_for(profile, setting, verified)
            assert policy == ChildPolicy('off', profile.harness, profile.model, profile.effort)
            assert (policy.max_children, policy.depth, policy.readonly) == (1, 1, True)


def test_verified_routes_all_false_and_bad_setting_rejected():
    assert VERIFIED_ROUTES == {'codex': False, 'claude': False, 'grok': False}
    with pytest.raises(ValueError):
        policy_for(DEFAULT_PROFILES[0], 'on', True)


def test_proven_route_needs_setting_and_proof_and_grok_stays_off(monkeypatch):
    monkeypatch.setitem(VERIFIED_ROUTES, 'codex', True)
    monkeypatch.setitem(VERIFIED_ROUTES, 'grok', True)
    codex = next(p for p in DEFAULT_PROFILES if p.harness == 'codex')
    grok = next(p for p in DEFAULT_PROFILES if p.harness == 'grok')
    assert policy_for(codex, 'verified', True).mode == 'verified'
    assert policy_for(codex, 'verified', False).mode == 'off' and policy_for(codex, 'off', True).mode == 'off'
    assert policy_for(grok, 'verified', True).mode == 'off'
    with pytest.raises(ValueError):  # no pinned child controls exist yet: still fail closed
        child_controls(policy_for(codex, 'verified', True), 'codex', codex.model, codex.effort, [])


def test_policy_hash_and_fingerprint_stable():
    a, b = ChildPolicy('off', 'codex', 'm', 'high'), ChildPolicy('off', 'codex', 'm', 'high')
    assert policy_hash(a) == policy_hash(b) and len(policy_hash(a)) == 64
    assert policy_hash(a) != policy_hash(ChildPolicy('off', 'codex', 'm', 'xhigh'))
    fp = capability_fingerprint('codex', '0.159.3', 'm', 'high', 'h')
    assert fp == capability_fingerprint('codex', '0.159.3', 'm', 'high', 'h')
    assert fp != capability_fingerprint('codex', '0.159.4', 'm', 'high', 'h')


def test_child_controls_none_off_and_pin():
    assert child_controls(None, 'codex', 'm', 'high', ['--x']) == []
    assert child_controls(ChildPolicy('off', 'codex', 'm', 'high'), 'codex', 'm', 'high', ['--x']) == ['--x']
    for harness, model in (('claude', 'm'), ('codex', 'n')):
        with pytest.raises(ValueError):
            child_controls(ChildPolicy('off', 'codex', 'm', 'high'), harness, model, 'high', ['--x'])


def test_capability_fingerprint_binds_binary_settings_and_never_grants_children():
    from forge.native_children import capability_for
    from forge.config import DEFAULT_HARNESS
    p = DEFAULT_PROFILES[0]
    info = {'path': '/fixture/codex', 'sha256': 'a' * 64, 'version': 'SIMULATED'}
    a = capability_for(p, info, DEFAULT_HARNESS['codex'])
    assert a['status'] == 'UNVERIFIED' and a['children'] == 'off'
    assert a['fingerprint'] != capability_for(p, {**info, 'sha256': 'b' * 64}, DEFAULT_HARNESS['codex'])['fingerprint']
    settings = {**DEFAULT_HARNESS['codex'], 'binds_ro': ['/fixture/tools']}
    assert a['fingerprint'] != capability_for(p, info, settings)['fingerprint']
