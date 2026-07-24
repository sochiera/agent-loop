from __future__ import annotations

from pathlib import Path
import subprocess
from types import SimpleNamespace
import pytest

from forge.task_pipeline import (InvalidDecision, parse_coder_decision, parse_review_decision,
                                 parse_tester_decision, run_tdd_loop,
                                 test_fingerprint as fingerprint_tests)


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
    def run_tester(_handoff):
        value = next(tester); seen.append(value)
        return parse_tester_decision('{"status":"' + value + '"}')
    outcome = run_tdd_loop(state=state, max_rounds=4, run_tester=run_tester,
        run_coder=lambda _: parse_coder_decision('{"status":"green"}'), checkpoint=lambda _: None,
        fingerprint=lambda: "same")
    assert outcome == "review" and seen == ["red", "review"] and state.tdd_round == 1


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
        checkpoint=lambda _: None,
        fingerprint=lambda: "same")

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
        checkpoint=lambda _: None,
        fingerprint=lambda: "same")

    assert result == "review"
    assert handoffs == ["", "błędne oczekiwanie"]


def test_changed_tests_block_coder() -> None:
    state = SimpleNamespace(task_phase="", tdd_round=0, tester_decision={})
    values = iter(["before", "after"])
    outcome = run_tdd_loop(state=state, max_rounds=4,
        run_tester=lambda _: parse_tester_decision('{"status":"red"}'),
        run_coder=lambda _: parse_coder_decision('{"status":"green"}'), checkpoint=lambda _: None,
        fingerprint=lambda: next(values))
    assert outcome.startswith("blocked:")


def test_restart_in_coder_phase_detects_modified_test_before_new_call() -> None:
    state = SimpleNamespace(task_phase="coder", tdd_round=0,
        tester_decision={"status": "red"}, tester_handoff="",
        coder_test_hash="before", coder_tree_hash="tree")
    called = False
    def coder(_decision):
        nonlocal called; called = True
        return parse_coder_decision('{"status":"green"}')
    result = run_tdd_loop(state=state, max_rounds=4,
        run_tester=lambda _: parse_tester_decision('{"status":"review"}'), run_coder=coder,
        checkpoint=lambda _: None, fingerprint=lambda: "after")
    assert result.startswith("blocked:") and not called


def test_restart_after_coder_edits_returns_to_tester_without_repeating_coder() -> None:
    state = SimpleNamespace(
        task_phase="coder", tdd_round=0,
        tester_decision={"status": "red"}, tester_handoff="",
        coder_test_hash="tests", coder_tree_hash="before")
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
        fingerprint=lambda: "tests",
        worktree_fingerprint=lambda: "after")

    assert result == "review"
    assert tester_calls == [""]
    assert coder_calls == []
    assert state.tdd_round == 1


def test_fingerprint_fallback_uses_test_conventions_not_substrings(
        tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "latest.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "test_feature.py").write_text("assert True\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    before = fingerprint_tests(str(tmp_path), [])

    (tmp_path / "latest.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert fingerprint_tests(str(tmp_path), []) == before

    (tmp_path / "test_feature.py").write_text("assert False\n", encoding="utf-8")
    assert fingerprint_tests(str(tmp_path), []) != before
