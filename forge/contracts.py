"""Strict final-response contracts for orchestration decisions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class ContractError(ValueError):
    pass


BRAIN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "tool": {"type": "string", "enum": ["forge.run_batch", "forge.finish"]},
        "reason": {"type": "string"},
        "objective": {"type": "string"},
        "success_criteria": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["tool", "reason", "objective", "success_criteria", "summary"],
}


_ASSESSMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "score": {"type": "number"},
        "summary": {"type": "string"},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "problems": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["score", "summary", "strengths", "problems"],
}


REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "winner": {"type": "string", "enum": ["tdd", "explore", "classic"]},
        "reason": {"type": "string"},
        "feedback": {"type": "array", "items": {"type": "string"}},
        "candidates": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "tdd": _ASSESSMENT_SCHEMA,
                "explore": _ASSESSMENT_SCHEMA,
                "classic": _ASSESSMENT_SCHEMA,
            },
            "required": ["tdd", "explore", "classic"],
        },
    },
    "required": ["winner", "reason", "feedback", "candidates"],
}


TEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "working": {"type": "array", "items": {"type": "string"}},
        "missing": {"type": "array", "items": {"type": "string"}},
        "observations": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "working", "missing", "observations", "evidence"],
}


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, character in enumerate(stripped):
            if character != "{":
                continue
            try:
                value, _ = decoder.raw_decode(stripped[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                break
        else:
            raise ContractError("response does not contain a JSON object")
    if not isinstance(value, dict):
        raise ContractError("response must be a JSON object")
    return value


@dataclass(frozen=True)
class BrainDecision:
    tool: str
    reason: str
    objective: str = ""
    success_criteria: tuple[str, ...] = ()
    summary: str = ""


def parse_brain(text: str) -> BrainDecision:
    value = _extract_json(text)
    tool = value.get("tool")
    reason = value.get("reason")
    if tool not in {"forge.run_batch", "forge.finish"}:
        raise ContractError("tool must be forge.run_batch or forge.finish")
    if not isinstance(reason, str) or not reason.strip():
        raise ContractError("reason must be a non-empty string")
    if tool == "forge.run_batch":
        objective = value.get("objective")
        criteria = value.get("success_criteria")
        if not isinstance(objective, str) or not objective.strip():
            raise ContractError("forge.run_batch requires a non-empty objective")
        if not isinstance(criteria, list) or not criteria or not all(
            isinstance(item, str) and item.strip() for item in criteria
        ):
            raise ContractError("forge.run_batch requires non-empty success_criteria")
        return BrainDecision(tool, reason, objective, tuple(criteria))
    summary = value.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ContractError("forge.finish requires a non-empty summary")
    return BrainDecision(tool, reason, summary=summary)


def parse_review(text: str) -> dict[str, Any]:
    value = _extract_json(text)
    if value.get("winner") not in {"tdd", "explore", "classic"}:
        raise ContractError("review winner must be tdd, explore, or classic")
    if not isinstance(value.get("reason"), str) or not value["reason"].strip():
        raise ContractError("review reason is required")
    if not isinstance(value.get("feedback"), list):
        raise ContractError("review feedback must be an array")
    candidates = value.get("candidates")
    if not isinstance(candidates, dict) or set(candidates) != {"tdd", "explore", "classic"}:
        raise ContractError("review must assess all three candidates")
    for name, assessment in candidates.items():
        if not isinstance(assessment, dict):
            raise ContractError(f"assessment for {name} must be an object")
        score = assessment.get("score")
        if not isinstance(score, (int, float)) or not 0 <= score <= 100:
            raise ContractError(f"assessment for {name} needs score 0..100")
    return value


def parse_test(text: str) -> dict[str, Any]:
    value = _extract_json(text)
    for key in ("summary", "working", "missing", "observations", "evidence"):
        if key not in value:
            raise ContractError(f"black-box report is missing {key}")
    if not isinstance(value["summary"], str):
        raise ContractError("black-box summary must be a string")
    for key in ("working", "missing", "observations", "evidence"):
        if not isinstance(value[key], list) or not all(
            isinstance(item, str) for item in value[key]
        ):
            raise ContractError(f"black-box {key} must be an array of strings")
    return value
