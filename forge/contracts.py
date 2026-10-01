"""Strict final-response contracts for orchestration decisions."""

from __future__ import annotations

import json
from typing import Any

from .sprint import (
    CODER_CANDIDATES,
    MIN_BACKLOG_MINUTES,
    MIN_BACKLOG_STORIES,
    MIN_CLEANUP_STORIES,
    MIN_FEATURE_STORIES,
    backlog_capacity,
)


class ContractError(ValueError):
    pass


_STORY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string"},
        "kind": {"type": "string", "enum": ["feature", "cleanup"]},
        "title": {"type": "string"},
        "user_story": {"type": "string"},
        "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
        "priority": {"type": "integer"},
        "estimated_minutes": {"type": "integer"},
    },
    "required": [
        "id",
        "kind",
        "title",
        "user_story",
        "acceptance_criteria",
        "priority",
        "estimated_minutes",
    ],
}

PRODUCT_OWNER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "assessment": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "summary": {"type": "string"},
                "working": {"type": "array", "items": {"type": "string"}},
                "problems": {"type": "array", "items": {"type": "string"}},
                "evidence": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary", "working", "problems", "evidence"],
        },
        "sprint_goal": {"type": "string"},
        "stories": {"type": "array", "items": _STORY_SCHEMA},
        "retired_story_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["assessment", "sprint_goal", "stories", "retired_story_ids"],
}

_TASK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string"},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["id", "title", "description", "acceptance_criteria"],
}

ITERATION_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "story_id": {"type": "string"},
        "objective": {"type": "string"},
        "tasks": {"type": "array", "items": _TASK_SCHEMA},
        "validation_commands": {"type": "array", "items": {"type": "string"}},
        "public_checks": {"type": "array", "items": {"type": "string"}},
        "addressed_nit_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "story_id",
        "objective",
        "tasks",
        "validation_commands",
        "public_checks",
        "addressed_nit_ids",
    ],
}

_TASK_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "task_id": {"type": "string"},
        "verdict": {"type": "string", "enum": ["accept", "reject"]},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["task_id", "verdict", "evidence"],
}

_FINDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string"},
        "summary": {"type": "string"},
        "evidence": {"type": "string"},
        "suggested_fix": {"type": "string"},
        "task_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["id", "summary", "evidence", "suggested_fix", "task_ids"],
}

ITERATION_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": ["accept", "reject", "blocked"]},
        "summary": {"type": "string"},
        "implementation_fingerprint": {"type": "string"},
        "task_results": {"type": "array", "items": _TASK_RESULT_SCHEMA},
        "blocking_findings": {"type": "array", "items": _FINDING_SCHEMA},
        "nits": {"type": "array", "items": {"type": "string"}},
        "blocker": {"type": "string"},
    },
    "required": [
        "verdict",
        "summary",
        "implementation_fingerprint",
        "task_results",
        "blocking_findings",
        "nits",
        "blocker",
    ],
}

ITERATION_TEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": ["accept", "reject", "blocked"]},
        "summary": {"type": "string"},
        "implementation_fingerprint": {"type": "string"},
        "task_results": {"type": "array", "items": _TASK_RESULT_SCHEMA},
        "whitebox": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "summary": {"type": "string"},
                "checks": {"type": "array", "items": {"type": "string"}},
                "observations": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary", "checks", "observations"],
        },
        "blackbox": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "summary": {"type": "string"},
                "happy_path": {
                    "type": "string",
                    "enum": ["exercised", "unreachable", "missing"],
                },
                "scenarios": {"type": "array", "items": {"type": "string"}},
                "evidence": {"type": "array", "items": {"type": "string"}},
                "observations": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary", "happy_path", "scenarios", "evidence", "observations"],
        },
        "blocking_findings": {"type": "array", "items": _FINDING_SCHEMA},
        "nits": {"type": "array", "items": {"type": "string"}},
        "blocker": {"type": "string"},
    },
    "required": [
        "verdict",
        "summary",
        "implementation_fingerprint",
        "task_results",
        "whitebox",
        "blackbox",
        "blocking_findings",
        "nits",
        "blocker",
    ],
}

_XFAIL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "nodeid": {"type": "string"},
        "reason": {"type": "string"},
        "repair_notes": {"type": "string"},
    },
    "required": ["nodeid", "reason", "repair_notes"],
}

TEST_AUTHOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "tests_root": {"type": "string"},
        "summary": {"type": "string"},
        "covered": {"type": "array", "items": {"type": "string"}},
        "xfails": {"type": "array", "items": _XFAIL_SCHEMA},
    },
    "required": ["tests_root", "summary", "covered", "xfails"],
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

_BORROW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "from": {"type": "string"},
        "what": {"type": "string"},
    },
    "required": ["from", "what"],
}

CANDIDATE_SELECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "winner": {"type": "string"},
        "reason": {"type": "string"},
        "candidates": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "tdd": _ASSESSMENT_SCHEMA,
                "explore": _ASSESSMENT_SCHEMA,
                "classic": _ASSESSMENT_SCHEMA,
            },
            "required": [],
        },
        "borrow": {"type": "array", "items": _BORROW_SCHEMA},
        "feedback": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["winner", "reason", "candidates", "borrow", "feedback"],
}


def candidate_selection_schema(submitted: tuple[str, ...]) -> dict[str, Any]:
    """A closed selection schema limited to the candidates that were submitted.

    The returned mapping for the full coder pool is the shared canonical
    ``CANDIDATE_SELECTION_SCHEMA`` and must not be mutated.
    """

    names = tuple(dict.fromkeys(str(name) for name in submitted))
    if names == CODER_CANDIDATES:
        return CANDIDATE_SELECTION_SCHEMA
    unknown = [name for name in names if name not in CODER_CANDIDATES]
    if unknown:
        raise ValueError(f"unknown tournament candidates: {', '.join(unknown)}")
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "winner": {"type": "string"},
            "reason": {"type": "string"},
            "candidates": {
                "type": "object",
                "additionalProperties": False,
                "properties": {name: _ASSESSMENT_SCHEMA for name in names},
                "required": list(names),
            },
            "borrow": {"type": "array", "items": _BORROW_SCHEMA},
            "feedback": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["winner", "reason", "candidates", "borrow", "feedback"],
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


def _required_text(value: dict[str, Any], key: str, context: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ContractError(f"{context} requires a non-empty {key}")
    return item.strip()


def _string_list(value: Any, key: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ContractError(f"{key} must be an array of strings")
    normalized = [item.strip() for item in value if item.strip()]
    if nonempty and not normalized:
        raise ContractError(f"{key} must not be empty")
    return normalized


def _exact_object(value: dict[str, Any], keys: set[str], context: str) -> None:
    missing = keys - set(value)
    extra = set(value) - keys
    if missing:
        raise ContractError(f"{context} is missing: {', '.join(sorted(missing))}")
    if extra:
        raise ContractError(f"{context} has unsupported fields: {', '.join(sorted(extra))}")


def parse_product_owner(
    text: str,
    *,
    previous_ready: tuple[str, ...] = (),
    accepted_story_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    value = _extract_json(text)
    _exact_object(value, {"assessment", "sprint_goal", "stories", "retired_story_ids"}, "product owner response")
    assessment = value.get("assessment")
    if not isinstance(assessment, dict):
        raise ContractError("assessment must be an object")
    _exact_object(assessment, {"summary", "working", "problems", "evidence"}, "assessment")
    assessment["summary"] = _required_text(assessment, "summary", "assessment")
    for key in ("working", "problems", "evidence"):
        assessment[key] = _string_list(assessment.get(key), f"assessment.{key}")
    value["sprint_goal"] = _required_text(value, "sprint_goal", "product owner response")

    stories = value.get("stories")
    if not isinstance(stories, list):
        raise ContractError("stories must be an array")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    accepted = set(accepted_story_ids)
    story_keys = {
        "id",
        "kind",
        "title",
        "user_story",
        "acceptance_criteria",
        "priority",
        "estimated_minutes",
    }
    for index, raw in enumerate(stories, start=1):
        if not isinstance(raw, dict):
            raise ContractError(f"story {index} must be an object")
        _exact_object(raw, story_keys, f"story {index}")
        story_id = _required_text(raw, "id", f"story {index}")
        if story_id in seen:
            raise ContractError(f"duplicate story id: {story_id}")
        if story_id in accepted:
            raise ContractError(f"accepted story id cannot be reused: {story_id}")
        seen.add(story_id)
        kind = raw.get("kind")
        if kind not in {"feature", "cleanup"}:
            raise ContractError(f"story {story_id} kind must be feature or cleanup")
        criteria = _string_list(
            raw.get("acceptance_criteria"),
            f"story {story_id} acceptance_criteria",
            nonempty=True,
        )
        priority = raw.get("priority")
        estimate = raw.get("estimated_minutes")
        if not isinstance(priority, int) or isinstance(priority, bool) or not 1 <= priority <= 100:
            raise ContractError(f"story {story_id} priority must be an integer 1..100")
        if not isinstance(estimate, int) or isinstance(estimate, bool) or not 1 <= estimate <= 240:
            raise ContractError(
                f"story {story_id} estimated_minutes must be an integer 1..240"
            )
        normalized.append(
            {
                "id": story_id,
                "kind": kind,
                "title": _required_text(raw, "title", f"story {story_id}"),
                "user_story": _required_text(raw, "user_story", f"story {story_id}"),
                "acceptance_criteria": criteria,
                "priority": priority,
                "estimated_minutes": estimate,
                "status": "ready",
            }
        )

    retired = _string_list(value.get("retired_story_ids"), "retired_story_ids")
    if len(retired) != len(set(retired)):
        raise ContractError("retired_story_ids contains duplicates")
    previous = set(previous_ready)
    unknown_retired = set(retired) - previous
    if unknown_retired:
        raise ContractError(
            "only existing ready stories may be retired: " + ", ".join(sorted(unknown_retired))
        )
    missing_previous = previous - seen - set(retired)
    if missing_previous:
        raise ContractError(
            "existing ready stories must be carried or retired: "
            + ", ".join(sorted(missing_previous))
        )
    capacity = backlog_capacity(normalized)
    if capacity["stories"] < MIN_BACKLOG_STORIES:
        raise ContractError(f"backlog requires at least {MIN_BACKLOG_STORIES} ready stories")
    if capacity["feature"] < MIN_FEATURE_STORIES:
        raise ContractError(f"backlog requires at least {MIN_FEATURE_STORIES} feature stories")
    if capacity["cleanup"] < MIN_CLEANUP_STORIES:
        raise ContractError(f"backlog requires at least {MIN_CLEANUP_STORIES} cleanup stories")
    if capacity["estimated_minutes"] < MIN_BACKLOG_MINUTES:
        raise ContractError(
            f"backlog must estimate at least {MIN_BACKLOG_MINUTES} minutes of work"
        )
    value["stories"] = normalized
    value["retired_story_ids"] = retired
    value["assessment"] = assessment
    value["capacity"] = capacity
    return value


def parse_iteration_plan(
    text: str,
    *,
    backlog: list[dict[str, Any]],
    required_kind: str,
    available_nit_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    value = _extract_json(text)
    _exact_object(
        value,
        {
            "story_id",
            "objective",
            "tasks",
            "validation_commands",
            "public_checks",
            "addressed_nit_ids",
        },
        "iteration plan",
    )
    story_id = _required_text(value, "story_id", "iteration plan")
    story = next(
        (
            item
            for item in backlog
            if item.get("id") == story_id and item.get("status", "ready") == "ready"
        ),
        None,
    )
    if story is None:
        raise ContractError(f"planner selected unavailable story: {story_id}")
    if story.get("kind") != required_kind:
        raise ContractError(
            f"story {story_id} is {story.get('kind')}, current slot requires {required_kind}"
        )
    eligible = [
        item
        for item in backlog
        if item.get("status", "ready") == "ready" and item.get("kind") == required_kind
    ]
    highest_priority = min(int(item.get("priority", 100)) for item in eligible)
    if int(story.get("priority", 100)) != highest_priority:
        raise ContractError(
            f"planner must select a priority {highest_priority} {required_kind} story before {story_id}"
        )
    value["objective"] = _required_text(value, "objective", "iteration plan")
    tasks = value.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ContractError("iteration plan requires at least one task")
    task_ids: set[str] = set()
    task_keys = {"id", "title", "description", "acceptance_criteria"}
    for index, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            raise ContractError(f"task {index} must be an object")
        _exact_object(task, task_keys, f"task {index}")
        task_id = _required_text(task, "id", f"task {index}")
        if task_id in task_ids:
            raise ContractError(f"duplicate task id: {task_id}")
        task_ids.add(task_id)
        task["title"] = _required_text(task, "title", f"task {task_id}")
        task["description"] = _required_text(task, "description", f"task {task_id}")
        task["acceptance_criteria"] = _string_list(
            task.get("acceptance_criteria"),
            f"task {task_id} acceptance_criteria",
            nonempty=True,
        )
    covered_criteria = {
        criterion
        for task in tasks
        for criterion in task["acceptance_criteria"]
    }
    missing_story_criteria = set(story.get("acceptance_criteria") or []) - covered_criteria
    if missing_story_criteria:
        raise ContractError(
            "plan tasks must preserve every Product Owner acceptance criterion verbatim: "
            + "; ".join(sorted(missing_story_criteria))
        )
    value["validation_commands"] = _string_list(
        value.get("validation_commands"), "validation_commands", nonempty=True
    )
    value["public_checks"] = _string_list(
        value.get("public_checks"), "public_checks", nonempty=True
    )
    nit_ids = _string_list(value.get("addressed_nit_ids"), "addressed_nit_ids")
    unknown_nits = set(nit_ids) - set(available_nit_ids)
    if unknown_nits:
        raise ContractError("plan references unknown nits: " + ", ".join(sorted(unknown_nits)))
    if required_kind == "feature" and nit_ids:
        raise ContractError("feature iterations cannot consume cleanup nits")
    task_text = "\n".join(
        [
            str(task.get("description") or "")
            + "\n"
            + "\n".join(str(item) for item in task.get("acceptance_criteria") or [])
            for task in tasks
        ]
    )
    unmapped_nits = [nit_id for nit_id in nit_ids if nit_id not in task_text]
    if unmapped_nits:
        raise ContractError(
            "addressed nits must be mapped by id to a task description or criterion: "
            + ", ".join(unmapped_nits)
        )
    value["addressed_nit_ids"] = nit_ids
    value["story"] = story
    return value


def _parse_task_results(value: Any, task_ids: tuple[str, ...], context: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ContractError(f"{context}.task_results must be an array")
    expected = set(task_ids)
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ContractError(f"{context} task result must be an object")
        _exact_object(item, {"task_id", "verdict", "evidence"}, f"{context} task result")
        task_id = _required_text(item, "task_id", f"{context} task result")
        if task_id not in expected or task_id in seen:
            raise ContractError(f"{context} has unknown or duplicate task result: {task_id}")
        verdict = item.get("verdict")
        if verdict not in {"accept", "reject"}:
            raise ContractError(f"{context} task {task_id} verdict must be accept or reject")
        evidence = _string_list(
            item.get("evidence"), f"{context} task {task_id} evidence", nonempty=True
        )
        normalized.append({"task_id": task_id, "verdict": verdict, "evidence": evidence})
        seen.add(task_id)
    if seen != expected:
        raise ContractError(
            f"{context} must assess every task; missing: {', '.join(sorted(expected - seen))}"
        )
    return normalized


def _parse_findings(value: Any, task_ids: tuple[str, ...], context: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ContractError(f"{context} must be an array")
    expected = set(task_ids)
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    keys = {"id", "summary", "evidence", "suggested_fix", "task_ids"}
    for raw in value:
        if not isinstance(raw, dict):
            raise ContractError(f"{context} finding must be an object")
        _exact_object(raw, keys, f"{context} finding")
        finding_id = _required_text(raw, "id", f"{context} finding")
        if finding_id in seen:
            raise ContractError(f"duplicate finding id: {finding_id}")
        seen.add(finding_id)
        affected = _string_list(
            raw.get("task_ids"), f"finding {finding_id} task_ids", nonempty=True
        )
        if set(affected) - expected:
            raise ContractError(f"finding {finding_id} references an unknown task")
        normalized.append(
            {
                "id": finding_id,
                "summary": _required_text(raw, "summary", f"finding {finding_id}"),
                "evidence": _required_text(raw, "evidence", f"finding {finding_id}"),
                "suggested_fix": _required_text(raw, "suggested_fix", f"finding {finding_id}"),
                "task_ids": affected,
            }
        )
    return normalized


def parse_iteration_review(
    text: str,
    *,
    task_ids: tuple[str, ...],
    expected_fingerprint: str,
    validation_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    value = _extract_json(text)
    _exact_object(
        value,
        {
            "verdict",
            "summary",
            "implementation_fingerprint",
            "task_results",
            "blocking_findings",
            "nits",
            "blocker",
        },
        "review",
    )
    verdict = value.get("verdict")
    if verdict not in {"accept", "reject", "blocked"}:
        raise ContractError("review verdict must be accept, reject, or blocked")
    value["summary"] = _required_text(value, "summary", "review")
    fingerprint = _required_text(value, "implementation_fingerprint", "review")
    if fingerprint != expected_fingerprint:
        raise ContractError("review assessed a stale implementation fingerprint")
    results = _parse_task_results(value.get("task_results"), task_ids, "review")
    findings = _parse_findings(value.get("blocking_findings"), task_ids, "review blocking_findings")
    nits = _string_list(value.get("nits"), "review.nits")
    blocker = str(value.get("blocker") or "").strip()
    rejected = any(item["verdict"] == "reject" for item in results)
    failed_validation = any(
        item.get("return_code") != 0 or item.get("timed_out")
        for item in (validation_results or [])
    )
    if verdict == "accept" and (findings or rejected or blocker or failed_validation):
        raise ContractError(
            "review accept requires green validation, accepted tasks, and no blocking findings"
        )
    if verdict == "reject" and not findings:
        raise ContractError("review reject requires an actionable blocking finding")
    if verdict == "blocked" and not blocker:
        raise ContractError("review blocked requires a blocker")
    value.update(task_results=results, blocking_findings=findings, nits=nits, blocker=blocker)
    return value


def parse_iteration_test(
    text: str,
    *,
    task_ids: tuple[str, ...],
    expected_fingerprint: str,
    validation_results: list[dict[str, Any]],
    public_checks: tuple[str, ...] = (),
) -> dict[str, Any]:
    value = _extract_json(text)
    _exact_object(
        value,
        {
            "verdict",
            "summary",
            "implementation_fingerprint",
            "task_results",
            "whitebox",
            "blackbox",
            "blocking_findings",
            "nits",
            "blocker",
        },
        "tester response",
    )
    verdict = value.get("verdict")
    if verdict not in {"accept", "reject", "blocked"}:
        raise ContractError("tester verdict must be accept, reject, or blocked")
    value["summary"] = _required_text(value, "summary", "tester response")
    fingerprint = _required_text(value, "implementation_fingerprint", "tester response")
    if fingerprint != expected_fingerprint:
        raise ContractError("tester assessed a stale implementation fingerprint")
    results = _parse_task_results(value.get("task_results"), task_ids, "tester")
    findings = _parse_findings(value.get("blocking_findings"), task_ids, "tester blocking_findings")
    nits = _string_list(value.get("nits"), "tester.nits")
    blocker = str(value.get("blocker") or "").strip()
    for section in ("whitebox", "blackbox"):
        if not isinstance(value.get(section), dict):
            raise ContractError(f"tester {section} must be an object")
    _exact_object(value["whitebox"], {"summary", "checks", "observations"}, "tester whitebox")
    value["whitebox"]["summary"] = _required_text(value["whitebox"], "summary", "tester whitebox")
    value["whitebox"]["checks"] = _string_list(value["whitebox"].get("checks"), "whitebox.checks")
    value["whitebox"]["observations"] = _string_list(
        value["whitebox"].get("observations"), "whitebox.observations"
    )
    _exact_object(
        value["blackbox"],
        {"summary", "happy_path", "scenarios", "evidence", "observations"},
        "tester blackbox",
    )
    value["blackbox"]["summary"] = _required_text(value["blackbox"], "summary", "tester blackbox")
    if value["blackbox"].get("happy_path") not in {"exercised", "unreachable", "missing"}:
        raise ContractError("blackbox happy_path must be exercised, unreachable, or missing")
    for key in ("scenarios", "evidence", "observations"):
        value["blackbox"][key] = _string_list(
            value["blackbox"].get(key), f"blackbox.{key}"
        )
    rejected = any(item["verdict"] == "reject" for item in results)
    failed_validation = any(
        item.get("return_code") != 0 or item.get("timed_out") for item in validation_results
    )
    missing_public_checks = set(public_checks) - set(value["blackbox"]["scenarios"])
    if verdict == "accept" and (
        findings
        or rejected
        or blocker
        or failed_validation
        or value["blackbox"]["happy_path"] != "exercised"
        or not value["whitebox"]["checks"]
        or not value["blackbox"]["scenarios"]
        or not value["blackbox"]["evidence"]
        or missing_public_checks
    ):
        raise ContractError(
            "tester accept requires green validation, accepted tasks with evidence, white-box checks, "
            "every public scenario, exercised happy path, black-box evidence, and no blockers"
        )
    if verdict == "reject" and not findings:
        raise ContractError("tester reject requires an actionable blocking finding")
    if verdict == "blocked" and not blocker:
        raise ContractError("tester blocked requires a blocker")
    value.update(task_results=results, blocking_findings=findings, nits=nits, blocker=blocker)
    return value


def parse_test_author(text: str) -> dict[str, Any]:
    value = _extract_json(text)
    _exact_object(
        value, {"tests_root", "summary", "covered", "xfails"}, "test author response"
    )
    tests_root = _required_text(value, "tests_root", "test author response")
    if tests_root.startswith("/") or ".." in tests_root.split("/"):
        raise ContractError("tests_root must be a relative path inside the product")
    if not tests_root.startswith("tests/") or tests_root.endswith("/"):
        raise ContractError("tests_root must be a directory path starting with tests/")
    if any(character.isspace() for character in tests_root):
        raise ContractError("tests_root must not contain whitespace")
    value["tests_root"] = tests_root
    value["summary"] = _required_text(value, "summary", "test author response")
    value["covered"] = _string_list(
        value.get("covered"), "covered", nonempty=True
    )
    raw_xfails = value.get("xfails")
    if not isinstance(raw_xfails, list):
        raise ContractError("xfails must be an array")
    xfails: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_xfails, start=1):
        if not isinstance(raw, dict):
            raise ContractError(f"xfail {index} must be an object")
        _exact_object(raw, {"nodeid", "reason", "repair_notes"}, f"xfail {index}")
        nodeid = _required_text(raw, "nodeid", f"xfail {index}")
        if nodeid in seen:
            raise ContractError(f"duplicate xfail nodeid: {nodeid}")
        seen.add(nodeid)
        xfails.append(
            {
                "nodeid": nodeid,
                "reason": _required_text(raw, "reason", f"xfail {nodeid}"),
                "repair_notes": _required_text(
                    raw, "repair_notes", f"xfail {nodeid}"
                ),
            }
        )
    value["xfails"] = xfails
    return value


def parse_candidate_selection(
    text: str,
    *,
    submitted: tuple[str, ...],
    eligible: tuple[str, ...],
) -> dict[str, Any]:
    value = _extract_json(text)
    _exact_object(
        value,
        {"winner", "reason", "candidates", "borrow", "feedback"},
        "candidate selection",
    )
    winner = value.get("winner")
    if winner not in submitted:
        raise ContractError(
            f"selected winner is not a submitted candidate: {winner}"
        )
    if winner not in eligible:
        raise ContractError(
            f"selected winner is not an eligible candidate: {winner}"
        )
    value["winner"] = str(winner)
    value["reason"] = _required_text(value, "reason", "candidate selection")
    candidates = value.get("candidates")
    if not isinstance(candidates, dict):
        raise ContractError("candidates must be an object keyed by candidate name")
    for name in sorted(set(candidates) - set(submitted)):
        del candidates[name]
    missing = sorted(set(submitted) - set(candidates))
    if missing:
        raise ContractError(
            "candidate selection candidates are missing assessments: "
            + ", ".join(missing)
        )
    for name in submitted:
        assessment = candidates[name]
        if not isinstance(assessment, dict):
            raise ContractError(f"candidate {name} assessment must be an object")
        _exact_object(
            assessment,
            {"score", "summary", "strengths", "problems"},
            f"candidate {name} assessment",
        )
        score = assessment.get("score")
        if (
            not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not 0 <= score <= 100
        ):
            raise ContractError(f"candidate {name} score must be a number 0..100")
        assessment["summary"] = _required_text(
            assessment, "summary", f"candidate {name} assessment"
        )
        assessment["strengths"] = _string_list(
            assessment.get("strengths"), f"candidate {name} strengths"
        )
        assessment["problems"] = _string_list(
            assessment.get("problems"), f"candidate {name} problems"
        )
    raw_borrow = value.get("borrow")
    if not isinstance(raw_borrow, list):
        raise ContractError("borrow must be an array")
    borrow: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_borrow, start=1):
        if not isinstance(raw, dict):
            raise ContractError(f"borrow {index} must be an object")
        _exact_object(raw, {"from", "what"}, f"borrow {index}")
        source = raw.get("from")
        if source not in submitted:
            raise ContractError(f"borrow {index} references unknown candidate: {source}")
        borrow.append(
            {
                "from": str(source),
                "what": _required_text(raw, "what", f"borrow {index}"),
            }
        )
    value["borrow"] = borrow
    value["feedback"] = _string_list(value.get("feedback"), "feedback")
    return value


def parse_swarm_backlog(text: str, *, minimum_tasks: int) -> dict[str, Any]:
    """Parse a swarm planner backlog: distinct parallel tasks over separate areas."""

    value = _extract_json(text)
    _exact_object(value, {"summary", "tasks"}, "swarm backlog")
    value["summary"] = _required_text(value, "summary", "swarm backlog")
    tasks: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw = value.get("tasks")
    if not isinstance(raw, list):
        raise ContractError("swarm backlog tasks must be an array")
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ContractError(f"swarm task {index} must be an object")
        keys = {"id", "title", "area", "description", "acceptance_criteria",
                "validation_commands", "priority"}
        missing = keys - set(item)
        extra = set(item) - keys
        if missing:
            raise ContractError(
                f"swarm task {index} is missing keys: {', '.join(sorted(missing))}"
            )
        if extra:
            item = {key: item[key] for key in sorted(keys)}
        task_id = str(item["id"]).strip()
        if not task_id:
            raise ContractError(f"swarm task {index} has an empty id")
        if task_id in seen:
            raise ContractError(f"swarm task id is not unique: {task_id}")
        seen.add(task_id)
        title = _required_text(item, "title", f"swarm task {task_id}")
        area = _required_text(item, "area", f"swarm task {task_id}")
        description = _required_text(item, "description", f"swarm task {task_id}")
        criteria = item.get("acceptance_criteria")
        if not isinstance(criteria, list) or not any(
            str(entry).strip() for entry in criteria
        ):
            raise ContractError(
                f"swarm task {task_id} needs at least one acceptance criterion"
            )
        commands = item.get("validation_commands") or []
        if not isinstance(commands, list) or not all(
            isinstance(entry, str) and entry.strip() for entry in commands
        ):
            raise ContractError(
                f"swarm task {task_id} validation_commands must be an array of strings"
            )
        priority = item.get("priority")
        if (
            not isinstance(priority, int)
            or isinstance(priority, bool)
            or not 1 <= priority <= 99
        ):
            raise ContractError(f"swarm task {task_id} priority must be an integer 1..99")
        tasks.append(
            {
                "id": task_id,
                "title": title,
                "area": area,
                "description": description,
                "acceptance_criteria": [str(entry) for entry in criteria],
                "validation_commands": list(commands),
                "priority": priority,
            }
        )
    if len(tasks) < minimum_tasks:
        raise ContractError(
            f"swarm backlog needs at least {minimum_tasks} distinct tasks, got {len(tasks)}"
        )
    value["tasks"] = tasks
    return value


def parse_swarm_replan(text: str, *, unfinished: tuple[str, ...]) -> dict[str, Any]:
    """Parse a threshold-triggered replan: new tasks plus reprioritized pending ids."""

    value = _extract_json(text)
    _exact_object(value, {"summary", "new_tasks", "priorities"}, "swarm replan")
    value["summary"] = _required_text(value, "summary", "swarm replan")
    raw_new = value.get("new_tasks")
    if not isinstance(raw_new, list) or not raw_new:
        raise ContractError("swarm replan must add at least one new task")
    new_tasks: list[dict[str, Any]] = []
    seen: set[str] = set(unfinished)
    for item in raw_new:
        if not isinstance(item, dict):
            raise ContractError("each swarm replan new task must be an object")
        single = parse_swarm_backlog(json.dumps({"summary": "x", "tasks": [item]}),
                                     minimum_tasks=1)
        task = single["tasks"][0]
        if task["id"] in seen:
            raise ContractError(f"swarm replan task id is not unique: {task['id']}")
        seen.add(task["id"])
        new_tasks.append(task)
    value["new_tasks"] = new_tasks
    raw_priorities = value.get("priorities")
    if not isinstance(raw_priorities, dict):
        raise ContractError("swarm replan priorities must be an object of task ids")
    priorities: dict[str, int] = {}
    for key, entry in raw_priorities.items():
        if (
            not isinstance(entry, int)
            or isinstance(entry, bool)
            or not 1 <= entry <= 99
        ):
            raise ContractError(f"swarm replan priority for {key} must be 1..99")
        priorities[str(key)] = entry
    value["priorities"] = priorities
    missing = [task_id for task_id in unfinished if task_id not in priorities]
    if missing:
        raise ContractError(
            "swarm replan priorities must cover every unfinished task; missing: "
            + ", ".join(missing)
        )
    return value


def parse_swarm_review(text: str) -> dict[str, Any]:
    """Parse a cheap swarm reviewer verdict for one candidate version."""

    value = _extract_json(text)
    _exact_object(value, {"verdict", "summary", "blocking"}, "swarm review")
    verdict = value.get("verdict")
    if verdict not in {"approve", "fix"}:
        raise ContractError(f"swarm review verdict must be approve or fix, got: {verdict}")
    value["verdict"] = str(verdict)
    value["summary"] = _required_text(value, "summary", "swarm review")
    blocking: list[dict[str, Any]] = []
    raw = value.get("blocking") or []
    if not isinstance(raw, list):
        raise ContractError("swarm review blocking must be an array")
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ContractError(f"swarm blocking finding {index} must be an object")
        _exact_object(item, {"problem", "detail"}, f"swarm blocking finding {index}")
        blocking.append(
            {
                "problem": _required_text(item, "problem", f"swarm blocking {index}"),
                "detail": _required_text(item, "detail", f"swarm blocking {index}"),
            }
        )
    if verdict == "fix" and not blocking:
        raise ContractError("a fix verdict needs at least one blocking finding")
    if verdict == "approve" and blocking:
        raise ContractError("an approve verdict must not keep blocking findings")
    value["blocking"] = blocking
    return value


def parse_swarm_selection(
    text: str, *, submitted: tuple[str, ...]
) -> dict[str, Any]:
    """Parse the strong reviewer's choice of the better cheap candidate."""

    value = _extract_json(text)
    _exact_object(value, {"winner", "reason", "candidates", "feedback"}, "swarm selection")
    winner = value.get("winner")
    if not isinstance(winner, str) or winner not in submitted:
        raise ContractError(
            f"swarm selection winner must be one of the submitted versions: {winner!r}"
        )
    value["winner"] = winner
    value["reason"] = _required_text(value, "reason", "swarm selection")
    value["feedback"] = _string_list(value.get("feedback"), "swarm selection feedback")
    candidates = value.get("candidates")
    if not isinstance(candidates, dict):
        raise ContractError("swarm selection candidates must be an object")
    for name in sorted(set(candidates) - set(submitted)):
        del candidates[name]
    for name in submitted:
        assessment = candidates.get(name)
        if not isinstance(assessment, dict):
            raise ContractError(f"swarm selection is missing assessment for {name}")
        _exact_object(assessment, {"score", "summary"}, f"swarm selection {name}")
        score = assessment.get("score")
        if (
            not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not 0 <= score <= 100
        ):
            raise ContractError(f"swarm selection score for {name} must be 0..100")
        assessment["summary"] = _required_text(
            assessment, "summary", f"swarm selection {name}"
        )
    return value
