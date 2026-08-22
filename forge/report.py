"""Compact product-state reports for the persistent brain."""

from __future__ import annotations

from typing import Any


def _scores(review: dict[str, Any]) -> dict[str, Any]:
    candidates = review.get("candidates") or {}
    return {
        name: {
            "score": item.get("score"),
            "summary": item.get("summary"),
            "strengths": item.get("strengths") or [],
            "problems": item.get("problems") or [],
        }
        for name, item in candidates.items()
        if isinstance(item, dict)
    }


def _validation_view(results: list[dict[str, Any]]) -> dict[str, Any]:
    short: list[dict[str, Any]] = []
    long: list[dict[str, Any]] = []
    flags: list[str] = []
    for item in results:
        entry = {
            "command": item.get("command"),
            "kind": item.get("kind") or "short",
            "passed": item.get("return_code") == 0 and not item.get("timed_out"),
            "timed_out": bool(item.get("timed_out")),
            "elapsed_seconds": item.get("elapsed_seconds"),
        }
        bucket = long if entry["kind"] == "long" else short
        bucket.append(entry)
        if item.get("timed_out"):
            flags.append(f"{entry['kind']} timed out: {entry['command']}")
        elif item.get("return_code") not in (0, None):
            flags.append(f"{entry['kind']} failed: {entry['command']}")
    return {"short": short, "long": long, "red_flags": flags}


def build_brain_report(
    *,
    cycle: int,
    objective: str,
    winner: str,
    review: dict[str, Any],
    black_box: dict[str, Any],
    whitebox: dict[str, Any] | None,
    metrics: dict[str, Any],
    winner_validation: list[dict[str, Any]],
    housekeeping: bool,
) -> dict[str, Any]:
    validation = _validation_view(winner_validation)
    extra_turns = [
        name
        for name, item in metrics.items()
        if isinstance(item, dict) and int(item.get("turns") or 0) > 1
    ]
    flags = list(validation["red_flags"])
    flags.extend((whitebox.get("red_flags") or []) if whitebox else [])
    if black_box.get("happy_path") == "unreachable":
        flags.append("black-box could not reach the documented happy path")
    if extra_turns:
        flags.append("coder continuation turns: " + ", ".join(extra_turns))
    return {
        "cycle": cycle,
        "completed_batch_objective": objective,
        "housekeeping": housekeeping,
        "winner": winner,
        "reason": review.get("reason"),
        "scores": _scores(review),
        "feedback": review.get("feedback") or [],
        "borrow": review.get("borrow") or [],
        "validation": validation,
        "whitebox": whitebox or {},
        "black_box": {
            "summary": black_box.get("summary"),
            "working": black_box.get("working") or [],
            "missing": black_box.get("missing") or [],
            "observations": black_box.get("observations") or [],
            "happy_path": black_box.get("happy_path", "exercised"),
        },
        "candidate_metrics": {
            name: {
                "status": item.get("status"),
                "selected": item.get("selected"),
                "review_score": item.get("review_score"),
                "turns": item.get("turns"),
                "tasks_completed": item.get("tasks_completed"),
                "tasks_total": item.get("tasks_total"),
                "validation_passed": item.get("validation_passed"),
                "validation_total": item.get("validation_total"),
            }
            for name, item in metrics.items()
            if isinstance(item, dict)
        },
        "red_flags": flags,
    }
