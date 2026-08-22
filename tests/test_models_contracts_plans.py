import json
from pathlib import Path

import pytest

import random

from forge.catalog import shuffle_coder_models
from forge.cli import _parser
from forge.contracts import (
    BRAIN_SCHEMA,
    REVIEW_SCHEMA,
    ContractError,
    parse_brain,
    parse_review,
    parse_test,
    parse_whitebox,
)
from forge.models import ModelSpec
from forge.plans import (
    candidate_validation_commands,
    progress,
    validate_plan,
    validation_commands,
)
from forge.prompts import planner_prompt, tester_prompt as _tester_prompt, winner_fix_prompt
from forge.validation import classify_command as classify


def test_model_spec_round_trip():
    value = ModelSpec.parse("opencode:gpt-5.6-luna:high")
    assert value.provider == "opencode"
    assert value.model == "openai/gpt-5.6-luna"
    assert value.effort == "high"
    assert value.display() == "opencode:openai/gpt-5.6-luna:high"


def test_model_spec_accepts_catalog_key_for_codex():
    value = ModelSpec.parse("codex:gpt-5.6-sol:high")
    assert value.model == "gpt-5.6-sol"


def test_model_spec_rejects_unknown_provider():
    with pytest.raises(ValueError):
        ModelSpec.parse("claude:opus")


def test_model_spec_rejects_grok_on_codex():
    with pytest.raises(ValueError, match="unsupported model"):
        ModelSpec.parse("codex:grok-4.6")


def test_shuffle_coder_models_preserves_the_pool():
    models = {
        "coder_tdd": ModelSpec.parse("opencode:gpt-5.6-luna"),
        "coder_explore": ModelSpec.parse("opencode:grok-4.6"),
        "coder_classic": ModelSpec.parse("opencode:glm-5.3"),
        "brain": ModelSpec.parse("codex:gpt-5.6-sol:high"),
    }
    shuffled = shuffle_coder_models(models, rng=random.Random(0))
    pool = {
        models["coder_tdd"].display(),
        models["coder_explore"].display(),
        models["coder_classic"].display(),
    }
    assigned = {
        shuffled["coder_tdd"].display(),
        shuffled["coder_explore"].display(),
        shuffled["coder_classic"].display(),
    }
    assert assigned == pool
    assert shuffled["brain"].display() == models["brain"].display()


def test_model_spec_accepts_grok_on_opencode():
    value = ModelSpec.parse("opencode:grok-4.6")
    assert value.model == "xai/grok-4.6"


def test_cli_defaults_all_coders_to_codex_luna_high():
    args = _parser().parse_args(
        [
            "run",
            "--repo",
            "/tmp/repo",
            "--brief",
            "/tmp/goal.md",
            "--brain",
            "codex:brain",
            "--planner",
            "codex:planner",
            "--reviewer",
            "codex:reviewer",
            "--tester",
            "codex:tester",
        ]
    )
    assert args.coder_tdd == "codex:gpt-5.6-luna:high"
    assert args.coder_explore == "codex:gpt-5.6-luna:high"
    assert args.coder_classic == "codex:gpt-5.6-luna:high"


def test_brain_contract_supports_both_virtual_tools():
    batch = parse_brain(
        json.dumps(
            {
                "tool": "forge.run_batch",
                "reason": "highest value",
                "objective": "Add accounts",
                "success_criteria": ["Users can sign in"],
            }
        )
    )
    assert batch.objective == "Add accounts"
    done = parse_brain(
        '{"tool":"forge.finish","reason":"brief complete","summary":"Delivered"}'
    )
    assert done.summary == "Delivered"


def test_brain_contract_rejects_empty_batch():
    with pytest.raises(ContractError):
        parse_brain('{"tool":"forge.run_batch","reason":"x"}')


def test_provider_schemas_are_strict_and_require_every_property():
    assert set(BRAIN_SCHEMA["required"]) == set(BRAIN_SCHEMA["properties"])
    candidates = REVIEW_SCHEMA["properties"]["candidates"]
    assert candidates["additionalProperties"] is False
    assert set(candidates["required"]) == set(candidates["properties"])
    for assessment in candidates["properties"].values():
        assert assessment["additionalProperties"] is False
        assert set(assessment["required"]) == set(assessment["properties"])


def test_review_requires_all_competitors_and_scores():
    review = {
        "winner": "tdd",
        "reason": "best",
        "feedback": [],
        "borrow": [{"from": "explore", "what": "better comparison table"}],
        "candidates": {
            name: {"score": score, "summary": "ok"}
            for name, score in (("tdd", 90), ("explore", 80), ("classic", 70))
        },
    }
    parsed = parse_review(json.dumps(review))
    assert parsed["winner"] == "tdd"
    assert parsed["borrow"][0]["from"] == "explore"


def test_review_defaults_empty_borrow():
    review = {
        "winner": "classic",
        "reason": "ok",
        "feedback": [],
        "candidates": {
            name: {"score": 50, "summary": "ok"}
            for name in ("tdd", "explore", "classic")
        },
    }
    assert parse_review(json.dumps(review))["borrow"] == []


def test_black_box_contract():
    report = {
        "summary": "works",
        "working": ["login"],
        "missing": [],
        "observations": ["fast"],
        "evidence": ["screenshot.png"],
        "happy_path": "unreachable",
    }
    assert parse_test(json.dumps(report))["happy_path"] == "unreachable"


def test_whitebox_contract():
    report = parse_whitebox(
        json.dumps(
            {
                "summary": "tests ran",
                "short": ["lint passed"],
                "long": ["import timed out"],
                "red_flags": ["import timed out"],
                "recommendation": "repair the importer next",
            }
        )
    )
    assert "import" in report["red_flags"][0]


def test_tester_prompt_reuses_expensive_validation_evidence(tmp_path: Path):
    prompt = _tester_prompt(
        objective="Import the catalog",
        criteria=("Catalog is complete",),
        commands=("npm run import:full",),
        validation=[
            {
                "command": "npm run import:full",
                "elapsed_seconds": 900.8,
                "output": "cpu complete; motherboard page 20",
                "return_code": -15,
                "timed_out": True,
            }
        ],
        evidence_dir=tmp_path,
    )
    assert "TIMED OUT after 900.8s" in prompt
    assert "motherboard page 20" in prompt
    assert "Do not repeat an already-recorded expensive or timed-out command" in prompt


def test_winner_fix_prompt_does_not_repeat_unchanged_timeout():
    prompt = winner_fix_prompt(
        ["Improve the bounded importer"],
        [
            {
                "command": "npm run import:full",
                "elapsed_seconds": 900,
                "return_code": -15,
                "timed_out": True,
            }
        ],
    )
    assert "TIMED OUT after 900.0s" in prompt
    assert "Do not repeat an expensive or timed-out command unchanged" in prompt


def test_markdown_plan_progress_and_commands():
    plan = """# Batch plan
## Tasks
- [x] TASK-001: first behavior
- [ ] TASK-002: second behavior
## Validation commands
- `python3 -m pytest`
- [winner-only] `python3 -m expensive_live_test`
"""
    state = progress(plan)
    assert (state.completed, state.total) == (1, 2)
    assert state.remaining == ("TASK-002: second behavior",)
    assert validation_commands(plan) == (
        "python3 -m pytest",
        "python3 -m expensive_live_test",
    )
    assert candidate_validation_commands(plan) == ("python3 -m pytest",)
    validate_plan(plan)


def test_planner_receives_mechanical_repo_and_toolchain_context():
    prompt = planner_prompt(
        "Build it",
        ("It works",),
        Path("plan.md"),
        repository_context="The selected branch has no tracked product files.",
        environment_context="Available commands: git, python3.",
    )
    assert "MECHANICAL REPOSITORY SNAPSHOT" in prompt
    assert "no tracked product files" in prompt
    assert "Available commands: git, python3" in prompt


def test_classify_long_validation_commands():
    assert classify("npm test") == "short"
    assert classify("python3 -c 'import json'") == "short"
    assert classify("npm run import:full") == "long"
    assert classify("npm run test:e2e") == "long"
    assert classify("LIVE_TEST=1 npm run test:integration") == "long"
