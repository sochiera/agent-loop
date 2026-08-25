from forge.catalog import ROLE_TIMEOUTS, model_family
from forge.cli import _parser
from forge.contracts import (
    ITERATION_PLAN_SCHEMA,
    ITERATION_REVIEW_SCHEMA,
    ITERATION_TEST_SCHEMA,
    PRODUCT_OWNER_SCHEMA,
)
from forge.models import ModelSpec, ROLE_NAMES, RunConfig
from forge.web import models_from_payload, restart_payload


def test_run_config_persists_backup_model():
    models = {role: ModelSpec.parse("codex:gpt-5.6-sol:high") for role in ROLE_NAMES}
    config = RunConfig(
        repo="/tmp/repo",
        brief="/tmp/brief.md",
        branch="main",
        models=models,
        backup=ModelSpec.parse("opencode:grok-4.6"),
    )
    restored = RunConfig.from_dict(config.to_dict())
    assert restored.backup is not None
    assert restored.backup.display() == "opencode:xai/grok-4.6"


def test_role_timeouts_cover_the_sprint_roster():
    assert set(ROLE_NAMES) <= set(ROLE_TIMEOUTS)
    assert ROLE_TIMEOUTS["reviewer"] == 1800


def test_model_spec_round_trip_and_catalog_aliases():
    value = ModelSpec.parse("opencode:gpt-5.6-luna:high")
    assert value.provider == "opencode"
    assert value.model == "openai/gpt-5.6-luna"
    assert value.effort == "high"
    assert value.display() == "opencode:openai/gpt-5.6-luna:high"
    assert ModelSpec.parse("codex:gpt-5.6-sol:high").model == "gpt-5.6-sol"
    assert ModelSpec.parse("opencode:grok-4.6").model == "xai/grok-4.6"
    assert ModelSpec.parse("opencode:kimi-k3").model == "kimi-for-coding/k3"


def test_model_spec_rejects_unknown_or_provider_incompatible_models():
    for selector in ("claude:opus", "codex:grok-4.6"):
        try:
            ModelSpec.parse(selector)
        except ValueError as exc:
            assert "model" in str(exc)
        else:
            raise AssertionError(f"accepted unsupported selector {selector}")


def test_cloud_and_openrouter_catalog_entries_resolve():
    assert (
        ModelSpec.parse("opencode:deepseek-v4-flash-0731").model
        == "alibaba-token-plan/deepseek-v4-flash-0731"
    )
    assert (
        ModelSpec.parse("opencode:deepseek-v4-pro-0813").model
        == "alibaba-token-plan/deepseek-v4-pro-0813"
    )
    expected = {
        "or-gemini-3.7-flash": "openrouter/google/gemini-3.7-flash",
        "or-gpt-5.6-luna": "openrouter/openai/gpt-5.6-luna",
        "or-deepseek-v4-flash-0731": "openrouter/deepseek/deepseek-v4-flash-0731",
        "or-deepseek-v4-pro": "openrouter/deepseek/deepseek-v4-pro",
        "or-deepseek-v4-pro-0813": "openrouter/deepseek/deepseek-v4-pro-0813",
    }
    for key, model in expected.items():
        assert ModelSpec.parse(f"opencode:{key}").model == model


def test_model_family_groups_failover_candidates_by_model_family():
    assert model_family(ModelSpec.parse("codex:gpt-5.6-sol")) == "gpt"
    assert model_family(ModelSpec.parse("opencode:or-gpt-5.6-luna")) == "gpt"
    assert model_family(ModelSpec.parse("opencode:grok-4.6")) == "grok"


def test_role_roster_has_one_coder_and_one_unified_tester():
    assert ROLE_NAMES == ("brain", "planner", "coder", "reviewer", "tester")


def test_legacy_config_migrates_first_old_coder_to_single_coder():
    models = {
        role: {"provider": "codex", "model": "gpt-5.6-sol", "effort": "high"}
        for role in ("brain", "planner", "reviewer", "tester")
    }
    models["coder_tdd"] = {
        "provider": "opencode",
        "model": "xai/grok-4.6",
        "effort": "",
    }
    restored = RunConfig.from_dict(
        {"repo": "/tmp/repo", "brief": "/tmp/brief", "branch": "main", "models": models}
    )
    assert restored.models["coder"].display() == "opencode:xai/grok-4.6"
    assert set(restored.models) == set(ROLE_NAMES)


def test_models_from_payload_imports_first_legacy_coder_pool_entry():
    models = models_from_payload(
        {
            "models": {
                "brain": "codex:gpt-5.6-sol:high",
                "planner": "codex:gpt-5.6-sol:high",
                "reviewer": "codex:gpt-5.6-terra:high",
                "tester": "codex:gpt-5.6-terra:high",
            },
            "coder_models": ["opencode:grok-4.6"],
            "shuffle_coders": True,
        }
    )
    assert models["coder"].display() == "opencode:xai/grok-4.6"


def test_cli_defaults_every_sprint_role():
    args = _parser().parse_args(
        ["run", "--repo", "/tmp/repo", "--brief", "/tmp/goal.md"]
    )
    assert args.brain == "codex:gpt-5.6-sol:high"
    assert args.coder == "codex:gpt-5.6-luna:high"
    assert args.tester == "codex:gpt-5.6-terra:high"


def test_provider_schemas_are_closed_and_require_every_property():
    for schema in (
        PRODUCT_OWNER_SCHEMA,
        ITERATION_PLAN_SCHEMA,
        ITERATION_REVIEW_SCHEMA,
        ITERATION_TEST_SCHEMA,
    ):
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])


def test_restart_payload_requires_confirmation_for_live_runs():
    assert restart_payload(1, False) == {"needs_confirm": True, "active_runs": 1}
    assert restart_payload(2, True) == {"restarting": True, "active_runs": 2}
    assert restart_payload(0, False) == {"restarting": True, "active_runs": 0}
