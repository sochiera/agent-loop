from __future__ import annotations

from types import SimpleNamespace
import pytest

from forge.task_pipeline import (InvalidDecision, parse_coder_decision, parse_review_decision,
                                 parse_tester_decision, run_tdd_loop)


def test_decision_contracts_are_small_and_strict() -> None:
    assert parse_tester_decision('{"status":"red","reason":"x"}').status == "red"
    assert parse_coder_decision('{"status":"green"}').status == "green"
    assert parse_review_decision('{"verdict":"approve"}').status == "approve"
    with pytest.raises(InvalidDecision): parse_tester_decision('not json')
    with pytest.raises(InvalidDecision): parse_coder_decision('{"status":"retry"}')


def test_red_green_returns_to_same_tester() -> None:
    state = SimpleNamespace(task_phase="", tdd_round=0, tester_decision={})
    tester = iter(["red", "review"])
    seen = []
    handoffs = []
    def run_tester(handoff):
        value = next(tester); seen.append(value); handoffs.append(handoff)
        return parse_tester_decision('{"status":"' + value + '"}')
    outcome = run_tdd_loop(state=state, max_rounds=4, run_tester=run_tester,
        run_coder=lambda _: parse_coder_decision(
            '{"status":"green","summary":"kod i testy zielone"}'),
        checkpoint=lambda _: None)
    assert outcome == "review" and seen == ["red", "review"] and state.tdd_round == 1
    assert handoffs == ["", "kod i testy zielone"]


def test_code_decision_does_not_require_a_new_test() -> None:
    state = SimpleNamespace(task_phase="", tdd_round=0, tester_decision={})
    tester = iter(("code", "review"))
    coder_decisions = []

    result = run_tdd_loop(
        state=state, max_rounds=4,
        run_tester=lambda _: parse_tester_decision(
            '{"status":"' + next(tester) + '"}'),
        run_coder=lambda decision: (
            coder_decisions.append(decision.status)
            or parse_coder_decision('{"status":"green"}')
        ),
        checkpoint=lambda _: None)

    assert result == "review"
    assert coder_decisions == ["code"]


def test_coder_can_return_test_feedback_to_same_tester() -> None:
    state = SimpleNamespace(task_phase="", tdd_round=0, tester_decision={})
    handoffs = []
    tester = iter(("red", "review"))

    def run_tester(handoff):
        handoffs.append(handoff)
        return parse_tester_decision(
            '{"status":"' + next(tester) + '"}')

    result = run_tdd_loop(
        state=state, max_rounds=4,
        run_tester=run_tester,
        run_coder=lambda _: parse_coder_decision(
            '{"status":"test_changes_needed","reason":"błędne oczekiwanie"}'),
        checkpoint=lambda _: None)

    assert result == "review"
    assert handoffs[0] == ""
    assert "PROŚBA DO CIEBIE od kodera" in handoffs[1]
    assert "status `test_changes_needed`" in handoffs[1]
    assert "błędne oczekiwanie" in handoffs[1]
    assert "wykonaj ją albo uzasadnij odmowę" in handoffs[1]


def test_coder_can_request_tester_decision_after_review_feedback() -> None:
    state = SimpleNamespace(task_phase="", tdd_round=0, tester_decision={})
    handoffs = []
    tester = iter(("code", "blocked"))

    def run_tester(handoff):
        handoffs.append(handoff)
        status = next(tester)
        return parse_tester_decision(
            '{"status":"' + status + '","reason":"tester zdecydował"}')

    result = run_tdd_loop(
        state=state, max_rounds=4,
        run_tester=run_tester,
        run_coder=lambda _: parse_coder_decision(
            '{"status":"tester_input_needed",'
            '"reason":"uwagi review są sprzeczne z kontraktem"}'),
        checkpoint=lambda _: None)

    assert result == "blocked: tester zdecydował"
    assert handoffs[0] == ""
    assert "PROŚBA DO CIEBIE od kodera" in handoffs[1]
    assert "status `tester_input_needed`" in handoffs[1]
    assert "uwagi review są sprzeczne z kontraktem" in handoffs[1]


def test_coder_changes_are_evaluated_by_tester_without_file_guard() -> None:
    state = SimpleNamespace(task_phase="", tdd_round=0, tester_decision={})
    tester = iter(("red", "review"))
    coder_calls = []

    result = run_tdd_loop(
        state=state, max_rounds=4,
        run_tester=lambda _: parse_tester_decision(
            '{"status":"' + next(tester) + '"}'),
        run_coder=lambda decision: (
            coder_calls.append(decision.status)
            or parse_coder_decision('{"status":"green"}')
        ),
        checkpoint=lambda _: None)

    assert result == "review"
    assert coder_calls == ["red"]


def test_restart_after_coder_edits_returns_to_tester_without_repeating_coder() -> None:
    state = SimpleNamespace(
        task_phase="coder", tdd_round=0,
        tester_decision={"status": "red"}, tester_handoff="",
        coder_tree_hash="before")
    tester_calls = []
    coder_calls = []

    result = run_tdd_loop(
        state=state,
        max_rounds=4,
        run_tester=lambda handoff: (
            tester_calls.append(handoff)
            or parse_tester_decision('{"status":"review"}')
        ),
        run_coder=lambda decision: (
            coder_calls.append(decision)
            or parse_coder_decision('{"status":"green"}')
        ),
        checkpoint=lambda _: None,
        worktree_fingerprint=lambda: "after")

    assert result == "review"
    assert tester_calls == [
        "Poprzednia tura kodera zostawiła zmiany przed checkpointem. "
        "Oceń zastany diff zamiast zakładać, że trzeba ponownie uruchomić kodera."
    ]
    assert coder_calls == []
    assert state.tdd_round == 1


def test_review_notes_are_normalised_to_strings() -> None:
    """Recenzent bywa niekarny w kształcie 'notes'; reszta pipeline'u je łączy
    i zapisuje, więc nie-string nie może wybuchać po akceptacji zadania."""
    result = parse_review_decision('{"verdict":"approve","notes":[1,{"a":2},"ok"]}')

    assert result.status == "approve"
    assert result.data["notes"] == ["1", "{'a': 2}", "ok"]
    assert "; ".join(result.data["notes"])


def test_review_without_notes_gets_empty_list() -> None:
    assert parse_review_decision('{"verdict":"approve"}').data["notes"] == []


def test_review_with_scalar_notes_does_not_explode() -> None:
    assert parse_review_decision('{"verdict":"changes","notes":"popraw"}').data["notes"] == ["popraw"]
