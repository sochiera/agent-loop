from pathlib import Path
import subprocess
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
         patch("forge.agents.run_agent", return_value='{"verdict":"complete"}'):
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
         patch("forge.agents.run_agent", return_value="wciąż nie JSON"):
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
         patch("forge.agents.run_agent") as verifier:
        git.return_value.stdout = "abc123\n"
        assert orchestrate.phase_verify_goal(
            Config(), str(tmp_path), state, lambda phase: phase)
    verifier.assert_not_called()
    feedback = tmp_path / ".forge" / "verification" / "latest-feedback.md"
    assert "smoke: rc=1" in feedback.read_text(encoding="utf-8")


def _story_repo(tmp_path: Path) -> tuple[Config, State]:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text(".forge/\n", encoding="utf-8")
    (tmp_path / "BACKLOG.md").write_text(
        "## US-001 — Wynik  [do weryfikacji]\n\n"
        "Jako gracz chcę wynik.\n\n"
        "- Dlaczego teraz: cel.\n- Sprawdzenie: uruchom demo.\n"
        "- Poza zakresem: historia.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    state = State(plan_batches=7, verify_targets=[], backlog_migrated=True)
    return Config(git_push=False), state


def test_story_verifier_writes_fresh_report_and_marks_story_done(tmp_path: Path) -> None:
    cfg, state = _story_repo(tmp_path)
    with patch("forge.orchestrate.verify.collect_evidence", return_value={}), \
         patch("forge.agents.run_agent",
               return_value='{"stories":[{"id":"US-001","status":"potwierdzona","evidence":"demo działa"}],"verdict":"complete","notes":[]}'):
        assert orchestrate.phase_verify_stories(
            cfg, str(tmp_path), state, lambda phase: phase)
    text = (tmp_path / ".forge" / "verification" / "stories-latest.md").read_text()
    assert "verified_at_batch: 7" in text
    assert state.stories_verified_sha in text
    assert "[zrobiona]" in (tmp_path / "BACKLOG.md").read_text()
    assert orchestrate._fresh_story_report(str(tmp_path), state) == text


def test_story_report_with_wrong_header_is_not_reused(tmp_path: Path) -> None:
    cfg, state = _story_repo(tmp_path)
    state.stories_verified_sha = "real"
    state.stories_verified_at_batch = 7
    path = tmp_path / ".forge" / "verification" / "stories-latest.md"
    path.parent.mkdir(parents=True)
    path.write_text("<!-- verified_at_batch: 6 -->\n<!-- verified_sha: fake -->\n")
    assert orchestrate._fresh_story_report(str(tmp_path), state) == ""
