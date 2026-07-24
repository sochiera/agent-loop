from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from forge import orchestrate
from forge.config import Config
from forge.state import State
from forge.task_pipeline import InvalidDecision


def _git(project: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=project, check=True, text=True,
        capture_output=True)


def _task_repo(tmp_path: Path) -> tuple[dict, State, Config]:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "tests@example.test")
    _git(tmp_path, "config", "user.name", "Forge Tests")
    (tmp_path / ".gitignore").write_text(".forge/\n", encoding="utf-8")
    (tmp_path / "task.md").write_text("Cel: zmień zachowanie.\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text(
        "from app import VALUE\n\ndef test_value():\n    assert VALUE >= 0\n",
        encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "seed")
    task = {
        "id": "task-001",
        "title": "Zmiana wartości",
        "file": "task.md",
        "difficulty": "simple",
        "test_globs": ["tests/test_*.py"],
        "targeted_test_cmd": "python3 -m pytest -q tests/test_app.py",
    }
    state = State(
        bootstrapped=True,
        test_cmd="python3 -m pytest -q",
        task_queue=[task])
    return task, state, Config(git_push=False)


def test_full_happy_path_reaches_commit(tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    tester_answers = iter((
        '{"status":"red","command":"python3 -m pytest -q tests/test_app.py"}',
        '{"status":"review"}',
    ))
    roles = []

    def role_call(_cfg, project, _state, role, _prompt, _log):
        roles.append(role)
        if role == "tester":
            answer = next(tester_answers)
            if '"red"' in answer:
                path = Path(project, "tests", "test_app.py")
                path.write_text(
                    path.read_text(encoding="utf-8")
                    + "\ndef test_new_value():\n    assert VALUE == 1\n",
                    encoding="utf-8")
            return answer
        Path(project, "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        return '{"status":"green","refactor":"done"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._run_boundary", return_value=(True, ["pytest: rc=0"])), \
         patch("forge.orchestrate.run_agent", return_value='{"verdict":"approve"}') as reviewer:
        assert orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert roles == ["tester", "coder", "tester"]
    assert reviewer.call_count == 1
    assert _git(tmp_path, "log", "-1", "--pretty=%s").stdout.strip() == "feat: Zmiana wartości"
    assert state.current_task == {}
    assert state.task_phase == ""
    assert state.tester_session == state.coder_session == ""


def test_review_changes_are_fixed_by_coder_then_committed(tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    prompts_seen = []

    def role_call(_cfg, project, _state, role, prompt, _log):
        assert role == "tester" or role == "coder"
        if role == "tester":
            return '{"status":"review"}'
        prompts_seen.append(prompt)
        Path(project, "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        Path(project, "tests", "test_app.py").write_text(
            "from app import VALUE\n\ndef test_value():\n    assert VALUE == 2\n",
            encoding="utf-8")
        return '{"status":"green","refactor":"done"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._run_boundary", return_value=(True, ["pytest: rc=0"])) as boundary, \
         patch("forge.orchestrate.run_agent", return_value='{"verdict":"changes","notes":["ustaw 2"]}'):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert boundary.call_count == 2
    assert len(prompts_seen) == 1
    assert "git diff forge/task-001-start" in prompts_seen[0]
    assert "python3 -m pytest -q tests/test_app.py" in prompts_seen[0]
    assert "pełną suitę `python3 -m pytest -q`" in prompts_seen[0]
    assert "VALUE == 2" in Path(tmp_path, "tests", "test_app.py").read_text(encoding="utf-8")
    assert _git(tmp_path, "log", "-1", "--pretty=%s").stdout.strip() == "feat: Zmiana wartości"


def test_red_boundary_returns_control_to_tester(tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    with patch("forge.orchestrate._call_role", return_value='{"status":"review"}'), \
         patch("forge.orchestrate._run_boundary", return_value=(False, ["pytest: rc=1"])), \
         patch("forge.orchestrate.run_agent") as reviewer:
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    reviewer.assert_not_called()
    assert state.task_phase == "tester"
    assert "Granica przed review jest czerwona" in state.tester_handoff
    assert state.tdd_round == 1


def test_reviewer_is_fresh_and_never_receives_author_records(tmp_path: Path) -> None:
    task, state, cfg = _task_repo(tmp_path)
    state.current_task = task
    state.task_queue = []
    state.task_phase = "review"
    state.task_start_tag = "forge/task-001-start"
    state.tester_session = "TESTER-SECRET"
    state.coder_session = "CODER-SECRET"
    state.tester_record = "TESTER-RECORD"
    state.coder_record = "CODER-RECORD"
    _git(tmp_path, "tag", state.task_start_tag)

    with patch("forge.orchestrate._run_boundary", return_value=(True, ["pytest: rc=0"])), \
         patch("forge.orchestrate.run_agent", return_value='{"verdict":"approve"}') as reviewer, \
         patch("forge.orchestrate.run_agent_session") as session_call:
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    review_prompt = reviewer.call_args.args[1]
    assert "TESTER-SECRET" not in review_prompt
    assert "CODER-SECRET" not in review_prompt
    assert "TESTER-RECORD" not in review_prompt
    assert "CODER-RECORD" not in review_prompt
    session_call.assert_not_called()


def test_reviewer_tree_change_fails_and_is_rolled_back(tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)

    def modifying_review(_agent, _prompt, _cfg, project, _log, **_kwargs):
        Path(project, "reviewer-change.py").write_text("bad = True\n", encoding="utf-8")
        return '{"verdict":"approve"}'

    with patch("forge.orchestrate._call_role", return_value='{"status":"review"}'), \
         patch("forge.orchestrate._run_boundary", return_value=(True, ["pytest: rc=0"])), \
         patch("forge.orchestrate.run_agent", side_effect=modifying_review):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert not (tmp_path / "reviewer-change.py").exists()
    assert state.current_task == {}
    reason = tmp_path / ".forge" / "failed" / "task-001" / "reason.txt"
    assert "reviewer zmienił drzewo" in reason.read_text(encoding="utf-8")


def test_round_limit_routes_to_failure(tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    cfg.max_tdd_rounds = 1

    def role_call(_cfg, _project, _state, role, _prompt, _log):
        if role == "tester":
            return '{"status":"red"}'
        return '{"status":"test_changes_needed","reason":"zły test"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._fail_task") as fail:
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert "round_limit" in fail.call_args.args[-1]


def test_restart_after_correction_edits_runs_boundary_without_repeating_coder(
        tmp_path: Path) -> None:
    task, state, cfg = _task_repo(tmp_path)
    state.current_task = task
    state.task_queue = []
    state.task_phase = "corrections"
    state.task_start_tag = "forge/task-001-start"
    state.review_notes = ["ustaw 3"]
    _git(tmp_path, "tag", state.task_start_tag)
    state.corrections_tree_hash = orchestrate._tree_fingerprint(str(tmp_path))
    (tmp_path / "app.py").write_text("VALUE = 3\n", encoding="utf-8")

    with patch("forge.orchestrate._call_role") as coder, \
         patch("forge.orchestrate._run_boundary", return_value=(True, ["pytest: rc=0"])):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    coder.assert_not_called()
    assert _git(tmp_path, "log", "-1", "--pretty=%s").stdout.strip() == "feat: Zmiana wartości"
    assert state.current_task == {}


def test_invalid_decision_gets_exactly_one_format_retry() -> None:
    answers = iter(("not-json", '{"status":"review"}'))
    prompts = []

    result = orchestrate._decision_with_retry(
        "base",
        lambda prompt: prompts.append(prompt) or next(answers),
        orchestrate.parse_tester_decision)

    assert result.status == "review"
    assert prompts[0] == "base"
    assert "wyłącznie jeden poprawny obiekt JSON" in prompts[1]


def test_second_invalid_decision_stops() -> None:
    calls = []
    with pytest.raises(InvalidDecision):
        orchestrate._decision_with_retry(
            "base",
            lambda prompt: calls.append(prompt) or "still-not-json",
            orchestrate.parse_tester_decision)
    assert len(calls) == 2
