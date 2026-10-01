import json
import random
from pathlib import Path

import pytest

from forge.models import CODER_ROLES, ModelSpec
from forge.policy import (
    CHEAP_CODER_POOL,
    DEEPSEEK,
    GLM,
    LUNA,
    MIMO,
    OPUS,
    PROMOTION_ACTIVE,
    PROMOTION_INACTIVE,
    POLICY_IDS,
    PROMOTION_UNKNOWN,
    SOL,
    SWARM_CODER_ROLE,
    SWARM_REVIEWER_ROLE,
    load_policy,
)

RETIRED = (
    ModelSpec("codex", "gpt-5.6-sol", "high"),
    ModelSpec("codex", "gpt-5.6-terra", "high"),
    ModelSpec("codex", "gpt-5.6-luna", "high"),
    ModelSpec("opencode", "opencode-go/deepseek-v4-flash", "xhigh"),
    ModelSpec("opencode", "opencode-go/mimo-v2.5", "xhigh"),
)

ROSTER = (SOL, LUNA, OPUS, GLM, DEEPSEEK, MIMO)
STAFF_ROLES = ("brain", "planner", "test_author", "reviewer", "tester")


def write_policy(tmp_path: Path, payload) -> Path:
    path = tmp_path / "model-policy.json"
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_roster_is_the_smart_four_plus_the_swarm_only_models():
    assert SOL == ModelSpec("codex", "gpt-6-sol", "medium")
    assert LUNA == ModelSpec("codex", "gpt-6-luna", "xhigh")
    assert OPUS == ModelSpec("claude", "claude-opus-5-5", "medium")
    assert GLM == ModelSpec("opencode", "opencode-go/glm-5.3-flash", "xhigh")
    assert DEEPSEEK == ModelSpec("opencode", "opencode-go/deepseek-v4.1-flash", "xhigh")
    assert MIMO == ModelSpec("opencode", "opencode-go/mimo-v2.6-flash", "xhigh")
    payload = load_policy(None).to_dict()
    assert payload["allowed_models"] == [
        "codex:gpt-6-sol:medium",
        "codex:gpt-6-luna:xhigh",
        "claude:claude-opus-5-5:medium",
        "opencode:opencode-go/glm-5.3-flash:xhigh",
        "opencode:opencode-go/deepseek-v4.1-flash:xhigh",
        "opencode:opencode-go/mimo-v2.6-flash:xhigh",
    ]
    assert payload["policy_ids"] == [
        "openai-codex/gpt-6-sol",
        "openai-codex/gpt-6-luna",
        "claude-code/claude-opus-5-5",
        "opencode-go/glm-5.3-flash",
        "opencode-go/deepseek-v4.1-flash",
        "opencode-go/mimo-v2.6-flash",
    ]
    assert payload["coder_only_models"] == [DEEPSEEK.display(), MIMO.display()]
    assert payload["cheap_pool"] == [spec.display() for spec in CHEAP_CODER_POOL]


def test_cheap_pool_is_six_slots_with_three_lunas():
    assert CHEAP_CODER_POOL == (DEEPSEEK, MIMO, GLM, LUNA, LUNA, LUNA)
    assert load_policy(None).cheap_pool() == CHEAP_CODER_POOL
    assert LUNA == ModelSpec("codex", "gpt-6-luna", "xhigh")
    assert POLICY_IDS["codex:gpt-6-luna"] == "openai-codex/gpt-6-luna"


def test_swarm_only_models_never_staff_tournament_or_staff_roles():
    snapshot = load_policy(None)
    for spec in (DEEPSEEK, MIMO):
        assert snapshot.allows(spec)
        for role in (SWARM_CODER_ROLE, SWARM_REVIEWER_ROLE, "probe"):
            assert snapshot.allows(spec, role)
        for role in (*STAFF_ROLES, *CODER_ROLES, "backup"):
            assert not snapshot.allows(spec, role)
    for role in (*STAFF_ROLES, *CODER_ROLES, SWARM_CODER_ROLE, SWARM_REVIEWER_ROLE, "backup"):
        assert snapshot.allows(GLM, role)
        assert snapshot.allows(LUNA, role)


@pytest.mark.parametrize("state", ["active", "inactive", "unknown"])
def test_promotion_state_never_admits_the_retired_roster(tmp_path, state):
    snapshot = load_policy(write_policy(tmp_path, {"promotion_state": state}))
    assert snapshot.allowed_models() == ROSTER
    for spec in RETIRED:
        assert not snapshot.allows(spec)
    for role in STAFF_ROLES:
        assert {spec for spec, _ in snapshot.role_options(role)} <= {SOL, LUNA, OPUS, GLM}
    for role in CODER_ROLES:
        assert {spec for spec, _ in snapshot.role_options(role)} == {OPUS, GLM, LUNA}


def test_promotion_state_is_still_reported(tmp_path):
    assert load_policy(write_policy(tmp_path, {"promotion_state": "active"})).state == PROMOTION_ACTIVE
    assert load_policy(write_policy(tmp_path, {"promotion_state": "inactive"})).state == PROMOTION_INACTIVE


@pytest.mark.parametrize(
    "payload",
    ['{"promotion_state": "weird"}', '{"unexpected": true}', "not json", ""],
)
def test_invalid_policy_state_keeps_the_same_roster(tmp_path, payload):
    snapshot = load_policy(write_policy(tmp_path, payload))
    assert snapshot.state == PROMOTION_UNKNOWN
    assert snapshot.allowed_models() == ROSTER


def test_missing_policy_file_is_unknown(tmp_path):
    assert load_policy(tmp_path / "absent.json").state == PROMOTION_UNKNOWN


def test_effort_is_pinned_per_model():
    snapshot = load_policy(None)
    assert snapshot.allows(ModelSpec("codex", "gpt-6-sol", ""))
    assert not snapshot.allows(ModelSpec("codex", "gpt-6-sol", "high"))
    assert not snapshot.allows(ModelSpec("codex", "gpt-6-luna", "medium"))
    assert snapshot.pin(ModelSpec("codex", "gpt-6-luna", "")) == LUNA
    assert snapshot.pin(ModelSpec("claude", "claude-opus-5-5", "")) == OPUS
    assert snapshot.pin(ModelSpec("opencode", "opencode-go/deepseek-v4.1-flash", "")) == DEEPSEEK
    assert snapshot.pin(ModelSpec("opencode", "opencode-go/mimo-v2.6-flash", "")) == MIMO
    assert not snapshot.allows(ModelSpec("opencode", "opencode-go/mimo-v2.6-flash", "high"))


def test_role_pools_follow_the_central_weights():
    snapshot = load_policy(None)
    assert dict(snapshot.role_options("brain")) == {SOL: 50, GLM: 50}
    assert dict(snapshot.role_options("planner")) == {SOL: 50, GLM: 50}
    assert dict(snapshot.role_options("reviewer")) == {SOL: 50, GLM: 50}
    for role in CODER_ROLES:
        assert dict(snapshot.role_options(role)) == {OPUS: 25, LUNA: 50, GLM: 25}
    assert dict(snapshot.role_options("test_author")) == {OPUS: 20, LUNA: 55, GLM: 25}
    assert dict(snapshot.role_options("tester")) == {OPUS: 20, LUNA: 55, GLM: 25}


def test_seeded_weighted_chooser_is_deterministic_and_covers_the_pool():
    snapshot = load_policy(None)
    first = [snapshot.select_role("planner", random.Random(seed)) for seed in range(50)]
    second = [snapshot.select_role("planner", random.Random(seed)) for seed in range(50)]
    assert first == second
    assert set(first) == {SOL, GLM}
    coder = [snapshot.select_role("coder_tdd", random.Random(seed)) for seed in range(80)]
    assert set(coder) == {OPUS, GLM, LUNA}


def test_tournament_coder_roster_never_consumes_the_cheap_pool():
    snapshot = load_policy(None)
    draws = [snapshot.select_coder_roster(random.Random(seed)) for seed in range(400)]
    assert draws[:20] == [
        snapshot.select_coder_roster(random.Random(seed)) for seed in range(20)
    ]
    seen = set()
    for roster in draws:
        assert set(roster) == set(CODER_ROLES)
        values = list(roster.values())
        assert len(set(values)) == 3
        seen.update(values)
    assert seen == {OPUS, GLM, LUNA}


def test_new_run_never_draws_swarm_only_models():
    snapshot = load_policy(None)
    for seed in range(200):
        models = snapshot.select_new_run_models(random.Random(seed))
        for role in (*STAFF_ROLES, *CODER_ROLES):
            assert models[role] not in (DEEPSEEK, MIMO)


def test_new_run_overrides_are_pinned():
    snapshot = load_policy(None)
    models = snapshot.select_new_run_models(
        random.Random(0), {"brain": ModelSpec("claude", "claude-opus-5-5")}
    )
    assert models["brain"] == OPUS


def test_representative_defaults_are_sol_and_luna():
    defaults = load_policy(None).representative_defaults()
    assert defaults["brain"] == SOL.display()
    assert defaults["planner"] == SOL.display()
    assert defaults["reviewer"] == SOL.display()
    assert defaults["tester"] == LUNA.display()
    for role in CODER_ROLES:
        assert defaults[role] == LUNA.display()


def test_reviewer_becomes_independent_of_the_winner_family():
    snapshot = load_policy(None)
    assert snapshot.independent_reviewer("gpt", rng=random.Random(0)) == (GLM, "")
    assert snapshot.independent_reviewer("glm", rng=random.Random(0)) == (SOL, "")
    reviewer, note = snapshot.independent_reviewer("claude", rng=random.Random(0))
    assert reviewer in (SOL, GLM) and note == ""


def test_reviewer_exception_is_recorded_when_no_independent_family_is_healthy():
    snapshot = load_policy(None)
    reviewer, note = snapshot.independent_reviewer(
        "gpt", disabled=[f"{GLM.provider}:{GLM.model}"]
    )
    assert reviewer is None
    assert "independent" in note


def test_failover_options_stay_inside_the_roster():
    snapshot = load_policy(None)
    assert set(snapshot.failover_options("coder_tdd")) == {OPUS, LUNA, GLM, SOL}
    assert set(snapshot.failover_options("reviewer")) == {SOL, GLM, OPUS, LUNA}
    for role in (*STAFF_ROLES, *CODER_ROLES):
        assert not {DEEPSEEK, MIMO} & set(snapshot.failover_options(role))


def test_policy_snapshot_round_trips_without_secrets():
    from forge.policy import snapshot_from_dict

    snapshot = load_policy(None)
    restored = snapshot_from_dict(snapshot.to_dict())
    assert restored.state == snapshot.state
    assert restored.allowed_models() == snapshot.allowed_models()
