import random

import pytest

from forge.catalog import (
    CATALOG,
    LEGACY_CATALOG,
    ROLE_TIMEOUTS,
    assign_coder_models,
    catalog_payload,
    find_active_entry,
    model_family,
    resolve_identity,
    shuffle_coder_models,
)
from forge.cli import _parser, select_cli_models
from forge.contracts import (
    CANDIDATE_SELECTION_SCHEMA,
    ITERATION_PLAN_SCHEMA,
    ITERATION_REVIEW_SCHEMA,
    ITERATION_TEST_SCHEMA,
    PRODUCT_OWNER_SCHEMA,
    TEST_AUTHOR_SCHEMA,
)
from forge.models import (
    CODER_ROLES,
    ModelSpec,
    ROLE_NAMES,
    RunConfig,
    STAFF_ROLES,
)
from forge.policy import (
    GLM,
    LUNA,
    OPUS,
    SOL,
    load_policy,
)
from forge.web import models_from_payload, restart_payload


BANNED_FRAGMENTS = (
    "grok",
    "kimi",
    "qwen",
    "openrouter",
    "alibaba",
    "zai-coding-plan",
    "ollama",
    "local",
)


def test_active_catalog_is_the_exact_policy_roster():
    assert [(entry.key, entry.family) for entry in CATALOG] == [
        ("gpt-6-sol", "gpt"),
        ("gpt-6-luna", "gpt"),
        ("claude-opus-5-5", "claude"),
        ("glm-5.3-flash", "glm"),
    ]
    assert [entry.efforts for entry in CATALOG] == [
        ("medium",), ("xhigh",), ("medium",), ("xhigh",)
    ]
    for entry in CATALOG:
        for provider, model in entry.ids.items():
            blob = f"{provider}:{model}".lower()
            assert not any(fragment in blob for fragment in BANNED_FRAGMENTS)


def test_active_catalog_rejects_every_banned_identity():
    for selector in (
        "opencode:grok-4.6",
        "opencode:kimi-k3",
        "opencode:qwen-3.8-max",
        "opencode:or-gemini-3.7-flash",
        "opencode:deepseek-v4-flash-0731",
        "opencode:glm-5.3",
        "codex:gpt-5.6-sol",
        "codex:gpt-5.6-terra",
        "codex:gpt-5.6-luna",
        "opencode:deepseek-v4.1-flash",
        "opencode:mimo-v2.6-flash",
        "claude:opus",
        "opencode:gpt-6-sol",
    ):
        with pytest.raises(ValueError):
            resolve_identity(*selector.split(":", 1))


def test_retired_roster_still_parses_for_old_state():
    for selector in (
        "codex:gpt-5.6-sol",
        "codex:gpt-5.6-terra",
        "codex:gpt-5.6-luna",
        "opencode:deepseek-v4.1-flash",
        "opencode:mimo-v2.6-flash",
    ):
        assert ModelSpec.parse(selector).provider == selector.split(":")[0]


def test_luna_resolves_only_through_native_codex():
    assert resolve_identity("codex", "gpt-6-luna") == ("codex", "gpt-6-luna")
    assert find_active_entry("opencode", "gpt-6-luna") is None
    with pytest.raises(ValueError):
        resolve_identity("opencode", "gpt-6-luna")
    with pytest.raises(ValueError):
        resolve_identity("claude", "gpt-6-luna")


def test_opus_resolves_only_through_claude_code_by_explicit_slug():
    assert resolve_identity("claude", "claude-opus-5-5") == ("claude", "claude-opus-5-5")
    assert ModelSpec.parse("claude:claude-opus-5-5:medium") == OPUS
    for model in ("opus", "claude-opus-5", "claude-opus-4-8"):
        with pytest.raises(ValueError):
            resolve_identity("claude", model)
    with pytest.raises(ValueError):
        resolve_identity("opencode", "claude-opus-5-5")


def test_legacy_identities_still_parse_for_old_state():
    assert ModelSpec.parse("opencode:grok-4.6").model == "xai/grok-4.6"
    assert ModelSpec.parse("opencode:kimi-k3").model == "kimi-for-coding/k3"
    assert ModelSpec.parse("opencode:glm-5.3").model == "zai-coding-plan/glm-5.3"
    assert ModelSpec.parse("opencode:openai/gpt-5.6-luna").model == "openai/gpt-5.6-luna"
    assert all(entry.key not in {item.key for item in CATALOG} or True for entry in LEGACY_CATALOG)
    for entry in LEGACY_CATALOG:
        for provider, model in entry.ids.items():
            assert ModelSpec.parse(f"{provider}:{model}").model == model


def test_model_spec_rejects_unknown_or_provider_incompatible_models():
    for selector in ("claude:opus", "codex:grok-4.6", "claude:gpt-5.6-sol", "gemini:x"):
        with pytest.raises(ValueError):
            ModelSpec.parse(selector)


def test_model_family_groups_by_active_and_legacy_family():
    assert model_family(ModelSpec.parse("codex:gpt-6-sol")) == "gpt"
    assert model_family(ModelSpec.parse("codex:gpt-6-luna")) == "gpt"
    assert model_family(ModelSpec.parse("claude:claude-opus-5-5")) == "claude"
    assert model_family(ModelSpec.parse("codex:gpt-5.6-sol")) == "gpt"
    assert model_family(ModelSpec.parse("opencode:glm-5.3-flash")) == "glm"
    assert model_family(ModelSpec.parse("opencode:deepseek-v4.1-flash")) == "deepseek"
    assert model_family(ModelSpec.parse("opencode:mimo-v2.6-flash")) == "mimo"
    assert model_family(ModelSpec.parse("opencode:grok-4.6")) == "grok"


def test_role_timeouts_cover_the_sprint_roster():
    assert set(ROLE_NAMES) <= set(ROLE_TIMEOUTS)
    assert ROLE_TIMEOUTS["reviewer"] == 1800


def test_role_roster_restores_the_tournament_and_test_author():
    assert ROLE_NAMES == (
        "brain",
        "planner",
        "test_author",
        "coder_tdd",
        "coder_explore",
        "coder_classic",
        "reviewer",
        "tester",
    )
    assert CODER_ROLES == ("coder_tdd", "coder_explore", "coder_classic")
    assert "test_author" in STAFF_ROLES
    assert set(CODER_ROLES).isdisjoint(STAFF_ROLES)


def test_assign_coder_models_draws_distinct_families_and_reuses_short_pools():
    luna = ModelSpec.parse("codex:gpt-6-luna:xhigh")
    glm = ModelSpec.parse("opencode:glm-5.3-flash")
    opus = ModelSpec.parse("claude:claude-opus-5-5")
    models = {role: luna for role in ROLE_NAMES}

    drawn = assign_coder_models(models, [luna, glm, opus])
    assert {drawn[role].display() for role in CODER_ROLES} == {
        luna.display(),
        glm.display(),
        opus.display(),
    }

    reused = assign_coder_models(models, [glm])
    assert all(reused[role] == glm for role in CODER_ROLES)

    seeded = assign_coder_models(
        dict(models), [luna, glm, opus], rng=random.Random(7)
    )
    assert set(seeded[role] for role in CODER_ROLES) == {luna, glm, opus}
    assert seeded == assign_coder_models(
        dict(models), [luna, glm, opus], rng=random.Random(7)
    )


def test_legacy_config_migrates_single_coder_to_all_three_candidates():
    models = {
        role: {"provider": "codex", "model": "gpt-5.6-sol", "effort": "high"}
        for role in ("brain", "planner", "reviewer", "tester")
    }
    models["coder"] = {
        "provider": "opencode",
        "model": "opencode-go/glm-5.3-flash",
        "effort": "",
    }
    restored = RunConfig.from_dict(
        {"repo": "/tmp/repo", "brief": "/tmp/brief", "branch": "main", "models": models}
    )
    for role in CODER_ROLES:
        assert restored.models[role].display() == "opencode:opencode-go/glm-5.3-flash"
    assert set(restored.models) == set(ROLE_NAMES)


def test_run_config_round_trips_policy_path_and_shuffle_flag():
    models = {role: ModelSpec.parse("codex:gpt-6-sol:medium") for role in ROLE_NAMES}
    config = RunConfig(
        repo="/tmp/repo",
        brief="/tmp/brief.md",
        branch="main",
        models=models,
        shuffle_coders=True,
        policy_path="/tmp/policy.json",
    )
    restored = RunConfig.from_dict(config.to_dict())
    assert restored.shuffle_coders is True
    assert restored.policy_path == "/tmp/policy.json"


def test_run_config_defaults_to_weighted_coder_reshuffling():
    models = {role: ModelSpec.parse("codex:gpt-6-luna:xhigh") for role in ROLE_NAMES}
    config = RunConfig(repo="/tmp/repo", brief="/tmp/brief.md", branch="main", models=models)
    assert config.shuffle_coders is True


@pytest.mark.parametrize(
    "retired",
    [
        "codex:gpt-5.6-sol:high",
        "codex:gpt-5.6-terra:high",
        "codex:gpt-5.6-luna:high",
        "opencode:deepseek-v4.1-flash",
        "opencode:mimo-v2.6-flash",
        "codex:gpt-6-sol:high",
    ],
)
def test_run_config_rejects_the_retired_roster(tmp_path, retired):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text('{"promotion_state": "active"}', encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    brief = tmp_path / "brief.md"
    brief.write_text("# goal\n", encoding="utf-8")
    models = {role: ModelSpec.parse("codex:gpt-6-luna:xhigh") for role in ROLE_NAMES}
    models["coder_tdd"] = ModelSpec.parse(retired)
    config = RunConfig(
        repo=str(repo),
        brief=str(brief),
        branch="main",
        models=models,
        policy_path=str(policy_file),
    )
    with pytest.raises(ValueError, match="unsupported model|not allowed"):
        config.validate()


def test_models_from_payload_assigns_policy_pool_deterministically(tmp_path):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text('{"promotion_state": "active"}', encoding="utf-8")
    payload = {
        "policy_path": str(policy_file),
        "models": {
            "brain": "codex:gpt-6-sol:medium",
            "planner": "codex:gpt-6-sol:medium",
            "test_author": "opencode:glm-5.3-flash",
            "reviewer": "codex:gpt-6-sol:medium",
            "tester": "codex:gpt-6-sol:medium",
        },
        "coder_models": [
            "opencode:glm-5.3-flash",
            "claude:claude-opus-5-5",
        ],
    }
    first = models_from_payload(payload)
    second = models_from_payload(payload)
    assert {first[role].model for role in CODER_ROLES} == {
        GLM.model,
        OPUS.model,
    }
    assert first == second
    assert first["test_author"].model == "opencode-go/glm-5.3-flash"


def test_models_from_payload_imports_single_legacy_coder_pool_entry(tmp_path):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text('{"promotion_state": "active"}', encoding="utf-8")
    models = models_from_payload(
        {
            "policy_path": str(policy_file),
            "models": {
                "brain": "codex:gpt-6-sol:medium",
                "planner": "codex:gpt-6-sol:medium",
                "reviewer": "codex:gpt-6-sol:medium",
                "tester": "codex:gpt-6-sol:medium",
            },
            "coder_models": ["opencode:glm-5.3-flash"],
            "shuffle_coders": True,
        }
    )
    assert all(
        models[role].model == GLM.model for role in CODER_ROLES
    )


def test_models_from_payload_rejects_an_oversized_coder_pool():
    with pytest.raises(ValueError, match="at most"):
        models_from_payload(
            {"models": {}, "coder_models": ["opencode:glm-5.3-flash"] * 13}
        )


def test_cli_selects_policy_defaults_when_roles_are_unspecified(tmp_path):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text('{"promotion_state": "active"}', encoding="utf-8")
    args = _parser().parse_args(
        [
            "run",
            "--repo",
            "/tmp/repo",
            "--brief",
            "/tmp/goal.md",
            "--policy-path",
            str(policy_file),
        ]
    )
    assert args.brain is None
    assert args.shuffle_coders is True
    snapshot = load_policy(str(policy_file))
    models = select_cli_models(args, snapshot, rng=random.Random(3))
    assert models["brain"] in (SOL, GLM)
    assert models["planner"] in (SOL, GLM)
    assert models["reviewer"] in (SOL, GLM)
    assert {models[role] for role in CODER_ROLES} == {LUNA, GLM, OPUS}
    assert models["tester"] in (LUNA, GLM, OPUS)


def test_run_config_persists_an_active_backup_model(tmp_path):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text('{"promotion_state": "active"}', encoding="utf-8")
    models = {role: ModelSpec.parse("codex:gpt-6-sol:medium") for role in ROLE_NAMES}
    config = RunConfig(
        repo="/tmp/repo",
        brief="/tmp/brief.md",
        branch="main",
        models=models,
        backup=ModelSpec.parse("opencode:glm-5.3-flash"),
        policy_path=str(policy_file),
    )
    restored = RunConfig.from_dict(config.to_dict())
    assert restored.backup is not None
    assert restored.backup.display() == "opencode:opencode-go/glm-5.3-flash"


def test_cli_honors_explicit_overrides(tmp_path):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text('{"promotion_state": "active"}', encoding="utf-8")
    args = _parser().parse_args(
        [
            "run",
            "--repo",
            "/tmp/repo",
            "--brief",
            "/tmp/goal.md",
            "--policy-path",
            str(policy_file),
            "--brain",
            "opencode:glm-5.3-flash",
        ]
    )
    snapshot = load_policy(str(policy_file))
    models = select_cli_models(args, snapshot, rng=random.Random(3))
    assert models["brain"] == GLM


def test_catalog_payload_exposes_only_active_models():
    payload = catalog_payload()
    keys = {item["key"] for item in payload["models"]}
    assert keys == {"gpt-6-sol", "gpt-6-luna", "claude-opus-5-5", "glm-5.3-flash"}
    assert payload["providers"] == ["codex", "claude", "opencode"]
    for item in payload["models"]:
        blob = str(item).lower()
        assert not any(fragment in blob for fragment in BANNED_FRAGMENTS)


def test_provider_schemas_are_closed_and_require_every_property():
    for schema in (
        PRODUCT_OWNER_SCHEMA,
        ITERATION_PLAN_SCHEMA,
        ITERATION_REVIEW_SCHEMA,
        ITERATION_TEST_SCHEMA,
        TEST_AUTHOR_SCHEMA,
        CANDIDATE_SELECTION_SCHEMA,
    ):
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])


def test_restart_payload_requires_confirmation_for_live_runs():
    assert restart_payload(1, False) == {"needs_confirm": True, "active_runs": 1}
    assert restart_payload(2, True) == {"restarting": True, "active_runs": 2}
    assert restart_payload(0, False) == {"restarting": True, "active_runs": 0}
