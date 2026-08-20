"""Markdown plan helpers used by the uniform coder goal loop."""

from __future__ import annotations

import re
from dataclasses import dataclass


_TASK = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+(.+?)\s*$", re.MULTILINE)
_COMMAND = re.compile(
    r"^\s*[-*]\s+(\[winner-only\]\s+)?`([^`]+)`\s*$",
    re.MULTILINE | re.IGNORECASE,
)


@dataclass(frozen=True)
class PlanProgress:
    completed: int
    total: int
    remaining: tuple[str, ...]

    @property
    def done(self) -> bool:
        return self.total > 0 and self.completed == self.total


def progress(markdown: str) -> PlanProgress:
    tasks = _TASK.findall(markdown)
    completed = sum(mark.lower() == "x" for mark, _ in tasks)
    remaining = tuple(text for mark, text in tasks if mark == " ")
    return PlanProgress(completed, len(tasks), remaining)


def validation_commands(markdown: str) -> tuple[str, ...]:
    heading = re.search(
        r"^##\s+Validation commands\s*$([\s\S]*?)(?=^##\s+|\Z)",
        markdown,
        re.MULTILINE | re.IGNORECASE,
    )
    return tuple(command for _, command in _COMMAND.findall(heading.group(1))) if heading else ()


def candidate_validation_commands(markdown: str) -> tuple[str, ...]:
    """Return checks worth running on all three candidates.

    Expensive external or end-to-end acceptance checks can be marked winner-only
    by the planner. Forge runs those once after review on the selected candidate.
    """
    heading = re.search(
        r"^##\s+Validation commands\s*$([\s\S]*?)(?=^##\s+|\Z)",
        markdown,
        re.MULTILINE | re.IGNORECASE,
    )
    if not heading:
        return ()
    return tuple(
        command for marker, command in _COMMAND.findall(heading.group(1)) if not marker
    )


def validate_plan(markdown: str) -> None:
    state = progress(markdown)
    if state.total < 2:
        raise ValueError("plan must contain at least two Markdown checkbox tasks")
    if not validation_commands(markdown):
        raise ValueError("plan must contain at least one command under ## Validation commands")
