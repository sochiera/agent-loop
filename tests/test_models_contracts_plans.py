import json
from pathlib import Path

import pytest

from forge.cli import _parser
from forge.contracts import (
    BRAIN_SCHEMA,
    REVIEW_SCHEMA,
    ContractError,
    parse_brain,
    parse_review,
    parse_test,
)
from forge.models import ModelSpec
from forge.plans import (
    candidate_validation_commands,
    progress,
    validate_plan,
    validation_commands,
)
from forge.prompts import planner_prompt, tester_prompt as _tester_prompt


def test_model_spec_round_trip():
    value = ModelSpec.parse("opencode:provider/model:high")
    assert value.provider == "opencode"
    assert value.model == "provider/model"
    assert value.effort == "high"
    assert value.display() == "opencode:provider/model:high"


def test_model_spec_rejects_unknown_provider():
    with pytest.raises(ValueError):
        ModelSpec.parse("other:model")


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
        "candidates": {
            name: {"score": score, "summary": "ok"}
            for name, score in (("tdd", 90), ("explore", 80), ("classic", 70))
        },
    }
    assert parse_review(json.dumps(review))["winner"] == "tdd"


def test_black_box_contract():
    report = {
        "summary": "works",
        "working": ["login"],
        "missing": [],
        "observations": ["fast"],
        "evidence": ["screenshot.png"],
    }
    assert parse_test(json.dumps(report)) == report


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
