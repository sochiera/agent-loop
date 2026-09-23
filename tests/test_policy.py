import json
import random
from pathlib import Path

import pytest

from forge.models import CODER_ROLES, ModelSpec
from forge.policy import (
    DEEPSEEK,
    GLM,
    LUNA,
    MIMO,
    PROMOTION_ACTIVE,
    PROMOTION_INACTIVE,
    PROMOTION_UNKNOWN,
    SOL,
    TERRA,
    cheap_coder_for,
    load_policy,
)


def write_policy(tmp_path: Path, payload) -> Path:
    path = tmp_path / "model-policy.json"
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_promotion_active_enables_deepseek_and_disables_mimo(tmp_path):
    snapshot = load_policy(write_policy(tmp_path, {"promotion_state": "active"}))
    assert snapshot.state == PROMOTION_ACTIVE
    assert snapshot.cheap_coder == DEEPSEEK
    assert snapshot.allows(DEEPSEEK)
    assert not snapshot.allows(MIMO)
    assert {spec for spec, _ in snapshot.coder_options()} == {LUNA, GLM, DEEPSEEK}


def test_promotion_inactive_enables_mimo_and_disables_deepseek(tmp_path):
    snapshot = load_policy(write_policy(tmp_path, {"promotion_state": "inactive"}))
    assert snapshot.state == PROMOTION_INACTIVE
    assert snapshot.cheap_coder == MIMO
    assert snapshot.allows(MIMO)
    assert not snapshot.allows(DEEPSEEK)
    assert {spec for spec, _ in snapshot.coder_options()} == {LUNA, GLM, MIMO}


@pytest.mark.parametrize(
    "payload",
    [
        '{"promotion_state": "weird"}',
        '{"unexpected": true}',
        "not json",
        "",
    ],
)
def test_missing_or_invalid_policy_fails_closed(tmp_path, payload):
    snapshot = load_policy(write_policy(tmp_path, payload))
    assert snapshot.state == PROMOTION_UNKNOWN
    assert snapshot.cheap_coder is None
    assert not snapshot.allows(DEEPSEEK)
    assert not snapshot.allows(MIMO)
    assert {spec for spec, _ in snapshot.coder_options()} == {LUNA, GLM}


def test_missing_policy_file_fails_closed(tmp_path):
    snapshot = load_policy(tmp_path / "absent.json")
    assert snapshot.state == PROMOTION_UNKNOWN
    assert snapshot.cheap_coder is None


def test_planner_and_reviewer_pools_are_sol_and_terra(tmp_path):
    for state in ("active", "inactive", "unknown"):
        path = write_policy(tmp_path, {"promotion_state": state})
        snapshot = load_policy(path)
        assert {spec for spec, _ in snapshot.role_options("brain")} == {SOL, TERRA}
        assert {spec for spec, _ in snapshot.role_options("planner")} == {SOL, TERRA}
        assert {spec for spec, _ in snapshot.role_options("reviewer")} == {SOL, TERRA}


def test_coder_and_test_pools_are_luna_glm_and_current_cheap(tmp_path):
    active = load_policy(write_policy(tmp_path, {"promotion_state": "active"}))
    assert {spec for spec, _ in active.role_options("coder_tdd")} == {
        LUNA,
        GLM,
        DEEPSEEK,
    }
    assert {spec for spec, _ in active.role_options("test_author")} == {
        LUNA,
        GLM,
        DEEPSEEK,
    }
    inactive = load_policy(write_policy(tmp_path, {"promotion_state": "inactive"}))
    assert {spec for spec, _ in inactive.role_options("coder_tdd")} == {
        LUNA,
        GLM,
        MIMO,
    }


def test_seeded_weighted_chooser_is_deterministic_and_covers_the_pool(tmp_path):
    snapshot = load_policy(write_policy(tmp_path, {"promotion_state": "active"}))
    first = [snapshot.select_role("planner", random.Random(seed)) for seed in range(50)]
    second = [snapshot.select_role("planner", random.Random(seed)) for seed in range(50)]
    assert first == second
    assert set(first) == {SOL, TERRA}
    coder = [
        snapshot.select_role("coder_tdd", random.Random(seed)) for seed in range(80)
    ]
    assert set(coder) == {LUNA, GLM, DEEPSEEK}


def test_coder_roster_is_family_diverse_when_three_models_are_available(tmp_path):
    snapshot = load_policy(write_policy(tmp_path, {"promotion_state": "active"}))
    for seed in range(25):
        roster = snapshot.select_coder_roster(random.Random(seed))
        assert set(roster) == set(CODER_ROLES)
        assert set(roster.values()) == {LUNA, GLM, DEEPSEEK}


def test_coder_roster_reuses_models_when_promotion_is_unknown(tmp_path):
    snapshot = load_policy(write_policy(tmp_path, {"promotion_state": "unknown"}))
    roster = snapshot.select_coder_roster(random.Random(1))
    assert set(roster.values()) <= {LUNA, GLM}
    assert len(set(roster.values())) == 2


def test_representative_defaults_are_sol_terra_and_luna():
    from forge.policy import PromotionSnapshot

    snapshot = PromotionSnapshot(state=PROMOTION_UNKNOWN)
    defaults = snapshot.representative_defaults()
    assert defaults["brain"] == SOL.display()
    assert defaults["planner"] == SOL.display()
    assert defaults["reviewer"] == TERRA.display()
    assert defaults["tester"] == TERRA.display()
    for role in CODER_ROLES:
        assert defaults[role] == LUNA.display()


def test_reviewer_becomes_independent_of_an_openai_winner(tmp_path):
    snapshot = load_policy(write_policy(tmp_path, {"promotion_state": "active"}))
    reviewer, note = snapshot.independent_reviewer("gpt", rng=random.Random(0))
    assert note == ""
    assert reviewer in (GLM, DEEPSEEK)


def test_reviewer_exception_is_recorded_when_no_independent_family_is_healthy(tmp_path):
    snapshot = load_policy(write_policy(tmp_path, {"promotion_state": "active"}))
    reviewer, note = snapshot.independent_reviewer(
        "gpt", disabled=[f"{GLM.provider}:{GLM.model}", f"{DEEPSEEK.provider}:{DEEPSEEK.model}"]
    )
    assert reviewer is None
    assert "independent" in note


def test_failover_options_respect_promotion_state_and_family_preference(tmp_path):
    active = load_policy(write_policy(tmp_path, {"promotion_state": "active"}))
    options = active.failover_options("coder_tdd")
    assert DEEPSEEK in options and MIMO not in options
    reviewer_options = active.failover_options("reviewer")
    assert SOL in reviewer_options and TERRA in reviewer_options
    inactive = load_policy(write_policy(tmp_path, {"promotion_state": "inactive"}))
    options = inactive.failover_options("coder_tdd")
    assert MIMO in options and DEEPSEEK not in options


def test_cheap_coder_for_unknown_is_none():
    assert cheap_coder_for("active") == DEEPSEEK
    assert cheap_coder_for("inactive") == MIMO
    assert cheap_coder_for("unknown") is None
    assert cheap_coder_for("") is None


def test_policy_snapshot_round_trips_without_secrets():
    from forge.policy import snapshot_from_dict

    snapshot = load_policy(None)
    restored = snapshot_from_dict(snapshot.to_dict())
    assert restored.state == snapshot.state
    assert restored.cheap_coder == snapshot.cheap_coder
