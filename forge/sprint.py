"""Domain constants and small invariants for continuous Forge sprints."""

from __future__ import annotations

from typing import Any, Iterable


SPRINT_SCHEDULE: tuple[str, ...] = (
    "feature",
    "feature",
    "feature",
    "feature",
    "cleanup",
    "feature",
    "feature",
    "feature",
    "feature",
    "cleanup",
)

CODER_CANDIDATES: tuple[str, ...] = ("tdd", "explore", "classic")

MIN_BACKLOG_STORIES = 12
MIN_FEATURE_STORIES = SPRINT_SCHEDULE.count("feature")
MIN_CLEANUP_STORIES = SPRINT_SCHEDULE.count("cleanup")
MIN_BACKLOG_MINUTES = 60


def slot_kind(index: int) -> str:
    if not 0 <= index < len(SPRINT_SCHEDULE):
        raise ValueError(f"sprint slot must be 0..{len(SPRINT_SCHEDULE) - 1}, got {index}")
    return SPRINT_SCHEDULE[index]


def ready_stories(stories: Iterable[dict[str, Any]], kind: str | None = None) -> list[dict[str, Any]]:
    return [
        item
        for item in stories
        if item.get("status", "ready") == "ready"
        and (kind is None or item.get("kind") == kind)
    ]


def backlog_capacity(stories: Iterable[dict[str, Any]]) -> dict[str, int]:
    ready = ready_stories(stories)
    return {
        "stories": len(ready),
        "feature": sum(item.get("kind") == "feature" for item in ready),
        "cleanup": sum(item.get("kind") == "cleanup" for item in ready),
        "estimated_minutes": sum(int(item.get("estimated_minutes") or 0) for item in ready),
    }


def assert_sprint_cursor(iteration: int) -> None:
    if not 0 <= iteration <= len(SPRINT_SCHEDULE):
        raise ValueError(
            f"sprint iteration must be 0..{len(SPRINT_SCHEDULE)}, got {iteration}"
        )


def compact_iteration(iteration: dict[str, Any]) -> dict[str, Any]:
    """Return the durable product-facing subset used in future role prompts."""

    return {
        key: iteration.get(key)
        for key in (
            "id",
            "sprint",
            "slot",
            "kind",
            "story_id",
            "objective",
            "commit",
            "review_summary",
            "test_summary",
            "nits",
        )
    }
