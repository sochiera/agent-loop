"""Mały, wznawialny cykl życia pojedynczego zadania Forge KISS."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .agents import extract_json


class InvalidDecision(ValueError):
    """Agent nie zwrócił poprawnego kontraktu decyzji."""


TASK_PHASES = ("tester", "coder", "review", "corrections", "commit")
TESTER_STATUSES = ("red", "code", "review", "blocked")
CODER_STATUSES = ("green", "test_changes_needed", "tester_input_needed")
REVIEW_VERDICTS = ("approve", "changes")


@dataclass(frozen=True)
class PhaseResult:
    status: str
    data: dict


def _decision(text: str) -> dict:
    data = extract_json(text)
    if not isinstance(data, dict):
        raise InvalidDecision("agent nie zwrócił poprawnego JSON-a")
    return data


def parse_tester_decision(text: str) -> PhaseResult:
    data = _decision(text)
    status = data.get("status")
    if status not in TESTER_STATUSES:
        raise InvalidDecision(f"niedozwolona decyzja testera: {status!r}")
    return PhaseResult(status, data)


def parse_coder_decision(text: str) -> PhaseResult:
    data = _decision(text)
    status = data.get("status")
    if status not in CODER_STATUSES:
        raise InvalidDecision(f"niedozwolona decyzja kodera: {status!r}")
    return PhaseResult(status, data)


def parse_review_decision(text: str) -> PhaseResult:
    data = _decision(text)
    verdict = data.get("verdict")
    if verdict not in REVIEW_VERDICTS:
        raise InvalidDecision(f"niedozwolony werdykt review: {verdict!r}")
    # Reszta pipeline'u łączy notes i zapisuje je do stanu, handoffu testera
    # oraz dziennika. Nie-string wybuchłby dopiero PO zaakceptowaniu zadania,
    # więc kształt normalizujemy tu — w jedynym miejscu, przez które przechodzą.
    data["notes"] = _as_strings(data.get("notes"))
    return PhaseResult(verdict, data)


def _as_strings(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [item if isinstance(item, str) else str(item) for item in value]
    return [str(value)]


def run_tdd_loop(*, state, max_rounds: int, run_tester: Callable[[str], PhaseResult],
                 run_coder: Callable[[PhaseResult], PhaseResult], checkpoint: Callable[[str], None],
                 worktree_fingerprint: Callable[[], str] | None = None) -> str:
    """Jedyna pętla tester↔koder, wznawialna z checkpointu następnej akcji."""
    if state.task_phase not in {"", "tester", "coder"}:
        raise ValueError(f"faza {state.task_phase!r} nie należy do pętli TDD")
    track_worktree = worktree_fingerprint is not None
    handoff = getattr(state, "tester_handoff", "")
    while True:
        if state.task_phase != "coder":
            state.task_phase = "tester"
            checkpoint("tester")
            decision = run_tester(handoff)
            state.tester_decision = decision.data
            state.tester_handoff = ""
            if decision.status == "blocked":
                return f"blocked: {decision.data.get('reason', 'tester nie podał powodu')}"
            if decision.status == "review":
                return "review"
            if state.tdd_round >= max_rounds:
                return f"round_limit: zadanie wymaga podziału (limit {max_rounds})"
            state.task_phase = "coder"
            state.coder_tree_hash = worktree_fingerprint() if track_worktree else ""
            checkpoint("coder")
        else:
            decision = PhaseResult(state.tester_decision["status"], state.tester_decision)
            before = getattr(state, "coder_tree_hash", "")
            if before and track_worktree and before != worktree_fingerprint():
                # Agent mógł zakończyć edycje tuż przed awarią procesu. Tester
                # oceni zastany kod; ponowienie kodera grozi podwójną zmianą.
                state.tdd_round += 1
                handoff = ("Poprzednia tura kodera zostawiła zmiany przed "
                           "checkpointem. Oceń zastany diff zamiast zakładać, "
                           "że trzeba ponownie uruchomić kodera.")
                state.tester_handoff = handoff
                state.coder_tree_hash = ""
                state.task_phase = "tester"
                checkpoint("tester")
                if state.tdd_round >= max_rounds:
                    return f"round_limit: zadanie wymaga podziału (limit {max_rounds})"
                continue
        result = run_coder(decision)
        state.tdd_round += 1
        if result.status != "green":
            state.coder_summary = ""
            if state.tdd_round >= max_rounds:
                return f"round_limit: zadanie wymaga podziału (limit {max_rounds})"
            handoff = result.data.get(
                "reason", "Koder potrzebuje decyzji albo działania testera.")
            state.tester_handoff = handoff
            state.coder_tree_hash = ""
            state.task_phase = "tester"
            checkpoint("tester")
            continue
        state.coder_tree_hash = ""
        state.task_phase = "tester"
        handoff = str(result.data.get("summary", "")).strip()
        if not handoff:
            handoff = "Koder zgłosił green bez podsumowania; oceń zastany diff."
        state.coder_summary = handoff
        state.tester_handoff = handoff
        checkpoint("tester")
    raise AssertionError("pętla TDD kończy się wyłącznie przez return")
