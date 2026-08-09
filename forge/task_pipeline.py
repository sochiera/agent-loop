"""Mały, wznawialny cykl życia pojedynczego zadania Forge KISS."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .agents import _extract_json_detail, log
from . import ledger


class InvalidDecision(ValueError):
    """Agent nie zwrócił poprawnego kontraktu decyzji."""


TASK_PHASES = ("tester", "coder", "review", "corrections", "commit")
TESTER_STATUSES = ("red", "code", "review", "finalize", "blocked")
CODER_STATUSES = ("green", "test_changes_needed", "tester_input_needed")
REVIEW_VERDICTS = ("approve", "suggestions", "request_changes")


@dataclass(frozen=True)
class PhaseResult:
    status: str
    data: dict


def _decision(text: str, *, project: str = "") -> dict:
    found = _extract_json_detail(text)
    if found.repaired:
        log("  UWAGA: werdykt odzyskany po naprawie cudzysłowów — rola pisze niepoprawny JSON")
        if project:
            ledger.append(project, "json: werdykt odzyskany warstwą naprawczą")
    if not isinstance(found.data, dict):
        reason = f" — {found.error}" if found.error else ""
        raise InvalidDecision("agent nie zwrócił poprawnego JSON-a" + reason)
    return found.data


def parse_tester_decision(text: str, *, project: str = "") -> PhaseResult:
    data = _decision(text, project=project)
    status = data.get("status")
    if status not in TESTER_STATUSES:
        raise InvalidDecision(f"niedozwolona decyzja testera: {status!r}")
    if status in {"red", "code"}:
        command = data.get("command")
        if not isinstance(command, str) or not command.strip():
            raise InvalidDecision(
                f"decyzja testera {status!r} wymaga niepustego `command`")
        data["command"] = command.strip()
    if status == "finalize":
        reason = data.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise InvalidDecision(
                "decyzja testera 'finalize' wymaga niepustego `reason`")
        data["reason"] = reason.strip()
    return PhaseResult(status, data)


def parse_coder_decision(text: str, *, project: str = "") -> PhaseResult:
    data = _decision(text, project=project)
    status = data.get("status")
    if status not in CODER_STATUSES:
        raise InvalidDecision(f"niedozwolona decyzja kodera: {status!r}")
    return PhaseResult(status, data)


def parse_review_decision(text: str, *, project: str = "") -> PhaseResult:
    data = _decision(text, project=project)
    verdict = data.get("verdict")
    if verdict not in REVIEW_VERDICTS:
        raise InvalidDecision(f"niedozwolony werdykt review: {verdict!r}")
    notes = _as_strings(data.get("notes"))
    nits = _as_strings(data.get("nits"))
    data["notes"] = notes
    data["nits"] = nits
    if verdict == "approve" and notes:
        raise InvalidDecision("werdykt 'approve' wymaga pustego `notes`")
    if verdict in {"suggestions", "request_changes"} and not notes:
        raise InvalidDecision(
            f"werdykt {verdict!r} wymaga co najmniej jednej notatki")
    return PhaseResult(verdict, data)


def _as_strings(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    elif not isinstance(value, (list, tuple)):
        value = [value]
    notes = []
    for item in value:
        text = item if isinstance(item, str) else str(item)
        if text.strip():
            notes.append(text.strip())
    return notes


def _coder_request_handoff(status: str, reason: str) -> str:
    detail = reason or "Koder potrzebuje decyzji albo działania testera."
    return (
        f"PROŚBA DO CIEBIE od kodera (status `{status}`): {detail} "
        "— wykonaj ją albo uzasadnij odmowę w `reason`."
    )


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
            if decision.status in {"review", "finalize"}:
                return decision.status
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
            handoff = _coder_request_handoff(
                result.status, str(result.data.get("reason", "")))
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
