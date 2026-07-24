from pathlib import Path
from unittest.mock import patch

from forge import orchestrate
from forge.config import Config
from forge.state import State


def test_verification_without_targets_finishes() -> None:
    state = State(task_phase="verify_goal")
    assert not orchestrate.phase_verify_goal(
        Config(), "/tmp", state, lambda phase: phase)
    assert state.task_phase == ""


def test_green_evidence_and_verifier_complete_goal(tmp_path: Path) -> None:
    state = State(
        task_phase="verify_goal",
        verify_targets=["smoke"],
        smoke_cmd="true")
    evidence = {"smoke": {"rc": 0, "output": "ok"}}
    with patch("forge.orchestrate.git") as git, \
         patch("forge.orchestrate.verify.collect_evidence", return_value=evidence), \
         patch("forge.orchestrate.run_agent", return_value='{"verdict":"complete"}'):
        git.return_value.stdout = "abc123\n"
        assert not orchestrate.phase_verify_goal(
            Config(), str(tmp_path), state, lambda phase: phase)
    assert state.task_phase == ""
    assert state.verify_cycle == 1


def test_unparsable_verdict_degrades_to_replan_not_crash(tmp_path: Path) -> None:
    state = State(
        task_phase="verify_goal",
        verify_targets=["smoke"],
        smoke_cmd="true")
    evidence = {"smoke": {"rc": 0, "output": "ok"}}
    with patch("forge.orchestrate.git") as git, \
         patch("forge.orchestrate.verify.collect_evidence", return_value=evidence), \
         patch("forge.orchestrate.run_agent", return_value="wciąż nie JSON"):
        git.return_value.stdout = "abc123\n"
        # Nie wolno rzucić InvalidDecision — weryfikacja celu jest tolerancyjna.
        assert orchestrate.phase_verify_goal(
            Config(), str(tmp_path), state, lambda phase: phase)
    assert state.task_phase == ""
    feedback = tmp_path / ".forge" / "verification" / "latest-feedback.md"
    assert "poprawnego werdyktu" in feedback.read_text(encoding="utf-8")


def test_red_evidence_returns_to_planning_with_feedback(tmp_path: Path) -> None:
    state = State(
        task_phase="verify_goal",
        verify_targets=["smoke"],
        smoke_cmd="false")
    evidence = {"smoke": {"rc": 1, "output": "boom"}}
    with patch("forge.orchestrate.git") as git, \
         patch("forge.orchestrate.verify.collect_evidence", return_value=evidence), \
         patch("forge.orchestrate.run_agent") as verifier:
        git.return_value.stdout = "abc123\n"
        assert orchestrate.phase_verify_goal(
            Config(), str(tmp_path), state, lambda phase: phase)
    verifier.assert_not_called()
    feedback = tmp_path / ".forge" / "verification" / "latest-feedback.md"
    assert "smoke: rc=1" in feedback.read_text(encoding="utf-8")
