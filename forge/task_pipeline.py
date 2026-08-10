"""Mały, wznawialny cykl życia pojedynczego zadania Forge KISS."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .agents import _extract_json_detail, log
from . import ledger
from .verdict import (CODER_STATUSES, InvalidDecision, REVIEW_VERDICTS,
                      TASK_PHASES, TESTER_STATUSES, validate_coder,
                      validate_review, validate_tester)

__all__ = [
    "CODER_STATUSES", "InvalidDecision", "PhaseResult", "REVIEW_VERDICTS",
    "TASK_PHASES", "TESTER_STATUSES", "parse_coder_decision",
    "parse_review_decision", "parse_tester_decision", "run_tdd_loop",
    "select_decision",
]


@dataclass(frozen=True)
class PhaseResult:
    status: str
    data: dict


def select_decision(text: str, validate: Callable[[dict], dict], *,
                    project: str = "") -> dict:
    """Pierwszy kandydat JSON, który spełnia kontrakt roli.

    Sam tekst nie rozstrzyga, który obiekt jest werdyktem, więc rozstrzyga
    kontrakt. Bez tego jedno zdanie doklejone po werdykcie (poprawka notatnika
    w drugim bloku ```json```) kasowało całą turę — a tura testera potrafi
    kosztować 11 M tokenów i 40 minut."""
    found = _extract_json_detail(text)
    if not found.candidates:
        reason = f" — {found.error}" if found.error else ""
        raise InvalidDecision("agent nie zwrócił poprawnego JSON-a" + reason)
    rejected: list[str] = []
    for index, candidate in enumerate(found.candidates):
        try:
            data = validate(candidate.data)
        except InvalidDecision as exc:
            rejected.append(str(exc))
            continue
        if candidate.repaired:
            log("  UWAGA: werdykt odzyskany po naprawie cudzysłowów — rola pisze niepoprawny JSON")
            if project:
                ledger.append(project, "json: werdykt odzyskany warstwą naprawczą")
        if index:
            log(f"  UWAGA: werdykt wzięty z wcześniejszego bloku JSON — rola "
                f"dokleiła po nim {index} obiekt(y) niebędące werdyktem")
            if project:
                ledger.append(project, "json: werdykt poprzedzał "
                                       f"{index} obcy obiekt(y) w odpowiedzi")
        return data
    raise InvalidDecision(
        rejected[0] if len(rejected) == 1 else
        f"żaden z {len(rejected)} obiektów JSON nie jest werdyktem tej roli: "
        + "; ".join(dict.fromkeys(rejected)))


def parse_tester_decision(text: str, *, project: str = "",
                          allow_finalize: bool = True) -> PhaseResult:
    """``allow_finalize`` odwzorowuje cykl sugestii: poza nim `finalize` jest
    obejściem review, więc nie może wygrać wyboru kandydata."""
    statuses = (TESTER_STATUSES if allow_finalize else
                tuple(name for name in TESTER_STATUSES if name != "finalize"))
    data = select_decision(
        text, lambda item: validate_tester(item, statuses=statuses),
        project=project)
    return PhaseResult(data["status"], data)


def parse_coder_decision(text: str, *, project: str = "") -> PhaseResult:
    data = select_decision(text, validate_coder, project=project)
    return PhaseResult(data["status"], data)


def parse_review_decision(text: str, *, project: str = "") -> PhaseResult:
    data = select_decision(text, validate_review, project=project)
    return PhaseResult(data["verdict"], data)


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
