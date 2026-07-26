"""Minimalny, trwały stan Forge."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

from .task_pipeline import TASK_PHASES


@dataclass
class State:
    bootstrapped: bool = False
    iteration: int = 0
    plan_batches: int = 0
    test_cmd: str = ""
    build_cmd: str = ""
    project_kind: str = "app"
    task_queue: list[dict] = field(default_factory=list)
    current_task: dict = field(default_factory=dict)
    task_phase: str = ""
    tdd_round: int = 0
    tester_session: str = ""
    coder_session: str = ""
    tester_decision: dict = field(default_factory=dict)
    tester_handoff: str = ""
    coder_summary: str = ""
    no_change_rounds: int = 0
    suite_regression: bool = False
    round_changed: bool = False
    tester_record: str = ""
    coder_record: str = ""
    review_notes: list[str] = field(default_factory=list)
    review_suggestions_pending: bool = False
    corrections_done: bool = False
    corrections_tree_hash: str = ""
    task_start_tag: str = ""
    coder_tree_hash: str = ""
    # Weryfikacja celu pozostaje niezależna od mechaniki zadania.
    verify_targets: list[str] = field(default_factory=list)
    smoke_cmd: str = ""
    flash_cmd: str = ""
    target_cmd: str = ""
    ci_status_cmd: str = ""
    ci_logs_cmd: str = ""
    verify_cycle: int = 0

    @classmethod
    def load(cls, path: str) -> "State":
        if not os.path.exists(path):
            return cls()
        with open(path, encoding="utf-8") as source:
            data = json.load(source)
        active = data.get("task_phase") or data.get("phase", "")
        if active and active not in TASK_PHASES and active not in {"idle", "verify_goal"}:
            raise ValueError(
                f"Nie można wznowić starej fazy {active!r}. Dokończ lub porzuć stare zadanie przed migracją KISS."
            )
        known = {key: data[key] for key in cls.__annotations__ if key in data}
        if active in TASK_PHASES or active == "verify_goal":
            known["task_phase"] = active
        return cls(**known)

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as target:
            json.dump(asdict(self), target, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
