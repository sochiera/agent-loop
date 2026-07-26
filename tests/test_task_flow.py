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
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate.run_agent", return_value='{"verdict":"approve"}') as reviewer:
        assert orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert roles == ["tester", "coder", "tester"]
    assert reviewer.call_count == 1
    assert _git(tmp_path, "log", "-1", "--pretty=%s").stdout.strip() == "feat: Zmiana wartości"
    assert state.current_task == {}
    assert state.task_phase == ""
    assert state.tester_session == state.coder_session == ""


def test_review_changes_start_a_new_tdd_cycle_then_commit(tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    tester_answers = iter((
        '{"status":"review"}',
        '{"status":"code","reason":"uwaga review: ustaw wartość 2"}',
        '{"status":"review"}',
    ))
    reviewer_answers = iter((
        '{"verdict":"changes","notes":["ustaw 2"]}',
        '{"verdict":"approve"}',
    ))
    prompts_seen: dict[str, list[str]] = {"tester": [], "coder": []}

    def role_call(_cfg, project, _state, role, prompt, _log):
        prompts_seen[role].append(prompt)
        if role == "tester":
            return next(tester_answers)
        Path(project, "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        return '{"status":"green","summary":"ustawiono VALUE=2","refactor":"done"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate.run_agent", side_effect=reviewer_answers):
        # Pierwsza recenzja nie uruchamia specjalnej tury kodera.
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)
        assert state.task_phase == "tester"
        assert state.current_task
        assert prompts_seen["coder"] == []
        assert "ustaw 2" in state.tester_handoff

        # Tester rozpoczyna nowy cykl, przekazuje poprawkę koderowi i ponownie
        # kieruje wynik do świeżego reviewera.
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert "ustaw 2" in prompts_seen["tester"][1]
    assert "uwaga review: ustaw wartość 2" in prompts_seen["coder"][0]
    assert "ustawiono VALUE=2" in prompts_seen["tester"][2]
    assert _git(tmp_path, "log", "-1", "--pretty=%s").stdout.strip() == "feat: Zmiana wartości"


def test_tester_receives_task_scoped_context_in_every_prompt(tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    orchestrate.ledger.append(str(tmp_path), "task-999 sekret innego zadania")
    orchestrate.ledger.append(str(tmp_path), "task-001 wcześniejszy wpis")
    tester_answers = iter((
        '{"status":"red","reason":"brakuje VALUE=1"}',
        '{"status":"review"}',
    ))
    tester_prompts: list[str] = []

    def role_call(_cfg, project, _state, role, prompt, _log):
        if role == "tester":
            tester_prompts.append(prompt)
            answer = next(tester_answers)
            if '"red"' in answer:
                path = Path(project, "tests", "test_app.py")
                path.write_text(
                    path.read_text(encoding="utf-8")
                    + "\ndef test_new_value():\n    assert VALUE == 1\n",
                    encoding="utf-8")
            return answer
        Path(project, "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        return '{"status":"green","summary":"ustawiono VALUE na 1"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate.run_agent", return_value='{"verdict":"approve"}'):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    confirmation = tester_prompts[-1]
    assert "brakuje VALUE=1" in confirmation
    assert "ustawiono VALUE na 1" in confirmation
    assert "app.py" in confirmation and "tests/test_app.py" in confirmation
    assert "task-001 wcześniejszy wpis" in tester_prompts[0]
    assert "sekret innego zadania" not in tester_prompts[0]


def test_independent_task_receives_failed_batch_handoff(tmp_path: Path) -> None:
    task, state, cfg = _task_repo(tmp_path)
    task["batch_handoff"] = (
        "task-000 z tego wsadu został porzucony: kontrakt niemożliwy")
    seen: list[str] = []

    def role_call(_cfg, _project, _state, role, prompt, _log):
        if role == "tester":
            seen.append(prompt)
            return '{"status":"review"}'
        raise AssertionError("coder should not run")

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate.run_agent", return_value='{"verdict":"approve"}'):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert "task-000 z tego wsadu został porzucony" in seen[0]


def test_review_runs_full_suite_boundary_before_commit(tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    with patch("forge.orchestrate._call_role", return_value='{"status":"review"}'), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate.run_shellfree", return_value=(0, "ok")) as boundary, \
         patch("forge.orchestrate.run_agent", return_value='{"verdict":"approve"}') as reviewer:
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    reviewer.assert_called_once()
    boundary.assert_called_once_with(
        str(tmp_path), state.test_cmd, cfg.agent_timeout_s)
    assert state.current_task == {}


def test_full_suite_failure_after_review_returns_to_tester_without_commit(
        tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)

    with patch("forge.orchestrate._call_role",
               return_value='{"status":"review"}'), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate.run_agent",
               return_value='{"verdict":"approve"}'), \
         patch("forge.orchestrate.build_then_test_result",
               return_value=(False, "FAIL integration_test\ntrace tail")) as gate:
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    gate.assert_called_once_with(
        str(tmp_path), state.build_cmd, state.test_cmd, cfg.agent_timeout_s)
    assert state.current_task
    assert state.task_phase == "tester"
    assert "pełny pakiet" in state.tester_handoff
    assert "FAIL integration_test" in state.tester_handoff
    assert "napraw albo zwróć `blocked`" in state.tester_handoff
    assert _git(
        tmp_path, "log", "-1", "--pretty=%s").stdout.strip() == "seed"


def test_tester_works_on_full_suite_after_gate_regression(
        tmp_path: Path) -> None:
    """Regresję wykrytą pełnym pakietem trzeba naprawiać pełnym pakietem —
    test ukierunkowany z definicji jej nie odtworzy."""
    task, state, cfg = _task_repo(tmp_path)
    prompts_seen: list[str] = []

    def role_call(_cfg, _project, _state, role, prompt, _log):
        prompts_seen.append(prompt)
        return '{"status":"review"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate.run_agent",
               return_value='{"verdict":"approve"}'), \
         patch("forge.orchestrate.build_then_test_result",
               return_value=(False, "FAIL integration_test")):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)
        assert state.suite_regression
        prompts_seen.clear()
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert state.test_cmd in prompts_seen[0]
    assert task["targeted_test_cmd"] not in prompts_seen[0]


def test_tdd_uses_targeted_command_but_commit_gate_uses_full_suite(
        tmp_path: Path) -> None:
    task, state, cfg = _task_repo(tmp_path)
    prompts_seen: list[str] = []

    def role_call(_cfg, _project, _state, role, prompt, _log):
        if role == "tester":
            prompts_seen.append(prompt)
            return '{"status":"review"}'
        raise AssertionError("coder should not run")

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate.run_agent",
               return_value='{"verdict":"approve"}'), \
         patch("forge.orchestrate.build_then_test_result",
               return_value=(True, "ok")) as gate:
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert task["targeted_test_cmd"] in prompts_seen[0]
    gate.assert_called_once_with(
        str(tmp_path), state.build_cmd, state.test_cmd, cfg.agent_timeout_s)


def test_confirmation_turn_is_narrow_and_uses_full_suite(tmp_path: Path) -> None:
    task, state, cfg = _task_repo(tmp_path)
    tester_answers = iter((
        '{"status":"red","reason":"VALUE ma być 1"}',
        '{"status":"review"}',
    ))
    tester_prompts: list[str] = []

    def role_call(_cfg, project, _state, role, prompt, _log):
        if role == "tester":
            tester_prompts.append(prompt)
            answer = next(tester_answers)
            if '"red"' in answer:
                path = Path(project, "tests", "test_app.py")
                path.write_text(
                    path.read_text(encoding="utf-8")
                    + "\ndef test_new_value():\n    assert VALUE == 1\n",
                    encoding="utf-8")
            return answer
        Path(project, "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        return '{"status":"green","summary":"VALUE ustawione na 1"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate.run_agent",
               return_value='{"verdict":"approve"}'), \
         patch("forge.orchestrate.build_then_test_result",
               return_value=(True, "ok")):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert task["targeted_test_cmd"] in tester_prompts[0]
    assert "TURA POTWIERDZAJĄCA" not in tester_prompts[0]
    assert "TURA POTWIERDZAJĄCA" in tester_prompts[1]
    assert f"uruchom `{state.test_cmd}`" in tester_prompts[1]
    assert "Nie oceniaj jakości implementacji" in tester_prompts[1]


def test_nonblocking_reviewer_notes_are_added_to_backlog(
        tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)

    with patch("forge.orchestrate._call_role",
               return_value='{"status":"review"}'), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate.run_agent", return_value=(
             '{"verdict":"approve","notes":'
             '["nazwa helpera jest nieprecyzyjna"]}')), \
         patch("forge.orchestrate.build_then_test_result",
               return_value=(True, "ok")):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    backlog = (tmp_path / "BACKLOG.md").read_text(encoding="utf-8")
    assert "task-001" in backlog
    assert "nazwa helpera jest nieprecyzyjna" in backlog


def test_blocking_reviewer_notes_do_not_enter_backlog_before_fix(
        tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)

    with patch("forge.orchestrate._call_role",
               return_value='{"status":"review"}'), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate.run_agent", return_value=(
             '{"verdict":"changes","notes":["napraw kontrakt"]}')):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert not (tmp_path / "BACKLOG.md").exists()


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

    with patch("forge.orchestrate.run_agent", return_value='{"verdict":"approve"}') as reviewer, \
         patch("forge.orchestrate.run_agent_session") as session_call:
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    review_prompt = reviewer.call_args.args[1]
    assert "TESTER-SECRET" not in review_prompt
    assert "CODER-SECRET" not in review_prompt
    assert "TESTER-RECORD" not in review_prompt
    assert "CODER-RECORD" not in review_prompt
    session_call.assert_not_called()


def test_reviewer_tree_change_returns_to_tester_without_rollback(tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)

    def modifying_review(_agent, _prompt, _cfg, project, _log, **_kwargs):
        Path(project, "reviewer-change.py").write_text("bad = True\n", encoding="utf-8")
        return '{"verdict":"approve"}'

    with patch("forge.orchestrate._call_role", return_value='{"status":"review"}'), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate.run_agent", side_effect=modifying_review):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert (tmp_path / "reviewer-change.py").read_text(encoding="utf-8") == "bad = True\n"
    assert state.current_task
    assert state.task_phase == "tester"
    assert "reviewer-change.py" in state.tester_handoff
    assert not (tmp_path / ".forge" / "failed" / "task-001").exists()
    assert _git(tmp_path, "log", "-1", "--pretty=%s").stdout.strip() == "seed"


def test_round_limit_routes_to_failure(tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    cfg.max_tdd_rounds = 1

    def role_call(_cfg, _project, _state, role, _prompt, _log):
        if role == "tester":
            return '{"status":"red"}'
        return '{"status":"test_changes_needed","reason":"zły test"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate._fail_task") as fail:
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert "round_limit" in fail.call_args.args[-1]


def test_legacy_corrections_checkpoint_returns_to_tester_without_coder(
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

    with patch("forge.orchestrate._call_role") as coder:
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    coder.assert_not_called()
    assert _git(tmp_path, "log", "-1", "--pretty=%s").stdout.strip() == "seed"
    assert state.current_task
    assert state.task_phase == "tester"
    assert "ustaw 3" in state.tester_handoff


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
