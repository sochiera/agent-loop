import json

import pytest

from forge.contracts import ContractError, parse_brain, parse_review, parse_test
from forge.models import ModelSpec
from forge.plans import progress, validate_plan, validation_commands


def test_model_spec_round_trip():
    value = ModelSpec.parse("opencode:provider/model:high")
    assert value.provider == "opencode"
    assert value.model == "provider/model"
    assert value.effort == "high"
    assert value.display() == "opencode:provider/model:high"


def test_model_spec_rejects_unknown_provider():
    with pytest.raises(ValueError):
        ModelSpec.parse("other:model")


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


def test_markdown_plan_progress_and_commands():
    plan = """# Batch plan
## Tasks
- [x] TASK-001: first behavior
- [ ] TASK-002: second behavior
## Validation commands
- `python3 -m pytest`
"""
    state = progress(plan)
    assert (state.completed, state.total) == (1, 2)
    assert state.remaining == ("TASK-002: second behavior",)
    assert validation_commands(plan) == ("python3 -m pytest",)
    validate_plan(plan)
