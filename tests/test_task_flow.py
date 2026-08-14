from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from forge import ledger, notebooks, orchestrate
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
         patch("forge.agents.run_agent", return_value='{"verdict":"approve"}') as reviewer:
        assert orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert roles == ["tester", "coder", "tester"]
    assert reviewer.call_count == 1
    assert _git(tmp_path, "log", "-1", "--pretty=%s").stdout.strip() == "feat: Zmiana wartości"
    assert state.current_task == {}
    assert state.task_phase == ""
    assert not (tmp_path / ".forge" / "notebooks" / "task-001").exists()
    assert state.tester_session == state.coder_session == ""


def test_review_request_changes_starts_a_new_tdd_cycle_then_commit(
        tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    tester_answers = iter((
        '{"status":"review"}',
        '{"status":"code","command":"python3 -m pytest -q tests/test_app.py",'
        '"reason":"uwaga review: ustaw wartość 2"}',
        '{"status":"review"}',
    ))
    reviewer_answers = iter((
        '{"verdict":"request_changes","notes":["ustaw 2"]}',
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
         patch("forge.agents.run_agent", side_effect=reviewer_answers):
        # Pierwsza recenzja nie uruchamia specjalnej tury kodera.
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)
        assert state.task_phase == "tester"
        assert state.current_task
        assert prompts_seen["coder"] == []
        assert state.review_notes == ["ustaw 2"]

        # Tester rozpoczyna nowy cykl, przekazuje poprawkę koderowi i ponownie
        # kieruje wynik do świeżego reviewera.
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert "ustaw 2" in prompts_seen["tester"][1]
    assert "uwaga review: ustaw wartość 2" in prompts_seen["coder"][0]
    assert "ustawiono VALUE=2" in prompts_seen["tester"][2]
    assert _git(tmp_path, "log", "-1", "--pretty=%s").stdout.strip() == "feat: Zmiana wartości"


def test_notebook_lines_are_persisted_and_return_in_the_next_round(
        tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    tester_answers = iter((
        '{"status":"red","command":"python3 -m pytest -q tests/test_app.py",'
        '"notebook":"bramka celowana to tests/test_app.py, nie cała suita"}',
        '{"status":"code","command":"python3 -m pytest -q tests/test_app.py",'
        '"reason":"popraw wartość","notebook":""}',
        '{"status":"review"}',
    ))
    coder_answers = iter((
        '{"status":"green","summary":"ustawiono","refactor":"done",'
        '"notebook":"stała żyje w app.py:1, nie w konfiguracji"}',
        # Pusta notatka nie zostawia śladu, a `summary` nie wchodzi do notatnika.
        '{"status":"green","summary":"poprawiono","refactor":"done",'
        '"notebook":""}',
    ))
    seen: dict[str, list[str]] = {"tester": [], "coder": []}

    def role_call(_cfg, project, _state, role, prompt, _log):
        seen[role].append(prompt)
        if role == "tester":
            return next(tester_answers)
        Path(project, "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        return next(coder_answers)

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.agents.run_agent",
               return_value='{"verdict":"approve"}'):
        assert orchestrate.run_task(
            cfg, str(tmp_path), state, lambda phase: phase)

    # Notatka z rundy 1 wraca w rundzie 2 bez tury narzędziowej, a `summary`
    # i `reason` — które kapsuła niesie osobno — do notatnika nie wchodzą.
    assert "stała żyje w app.py:1" not in seen["coder"][0]
    assert "- r1: stała żyje w app.py:1, nie w konfiguracji" in seen["coder"][1]
    assert "ustawiono" not in seen["coder"][1]
    assert "bramka celowana" not in seen["tester"][0]
    assert "- r1: bramka celowana to tests/test_app.py, nie cała suita" \
        in seen["tester"][1]
    # Notatniki są prywatne: wpis roli nie może wyciec do drugiej.
    assert "bramka celowana" not in seen["coder"][1]
    assert "stała żyje" not in seen["tester"][1]


def test_oversized_notebook_announces_itself_in_the_log(
        tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    notebooks.ensure(str(tmp_path), ".forge", "task-001")
    Path(tmp_path, ".forge", "notebooks", "task-001", "tester.md").write_text(
        "- r1: " + "x" * 5000 + "\n", encoding="utf-8")
    messages: list[str] = []

    def role_call(_cfg, project, _state, role, _prompt, _log):
        if role == "tester":
            return '{"status":"review"}'
        return '{"status":"green","summary":"nic","refactor":"not_needed"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate.log", side_effect=messages.append), \
         patch("forge.agents.run_agent",
               return_value='{"verdict":"approve"}'):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert any("notatnik roli tester" in message and "znaków" in message
               for message in messages)


def test_tester_receives_task_scoped_context_in_every_prompt(tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    orchestrate.ledger.append(str(tmp_path), "task-999 sekret innego zadania")
    orchestrate.ledger.append(str(tmp_path), "task-001 wcześniejszy wpis")
    tester_answers = iter((
        '{"status":"red","command":"python3 -m pytest -q tests/test_app.py",'
        '"reason":"brakuje VALUE=1"}',
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
         patch("forge.agents.run_agent", return_value='{"verdict":"approve"}'):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    confirmation = tester_prompts[-1]
    assert "brakuje VALUE=1" in confirmation
    assert "ustawiono VALUE na 1" in confirmation
    assert "app.py" in confirmation and "tests/test_app.py" in confirmation
    # Dziennik procesu jest świadomie SZERSZY niż zadanie: tura roli widzi
    # również cudze wpisy, bo to jedyny zapis tego, kto ruszył wspólne pliki i
    # które zadania padły. Wąskie okno kosztowało już pełne tury przepalone na
    # rekonstruowanie kontekstu, który stał w dzienniku dwie linie obok.
    assert "task-001 wcześniejszy wpis" in tester_prompts[0]
    assert "sekret innego zadania" in tester_prompts[0]
    assert "DZIENNIK PROCESU" in confirmation


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
         patch("forge.agents.run_agent", return_value='{"verdict":"approve"}'):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert "task-000 z tego wsadu został porzucony" in seen[0]


def test_review_runs_full_suite_boundary_before_commit(tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    with patch("forge.orchestrate._call_role", return_value='{"status":"review"}'), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate.run_shellfree", return_value=(0, "ok")) as boundary, \
         patch("forge.agents.run_agent", return_value='{"verdict":"approve"}') as reviewer:
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
         patch("forge.agents.run_agent",
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

    tester_prompts: list[str] = []

    def capture_tester(
            _cfg, _project, _state, role, prompt, _log):
        assert role == "tester"
        tester_prompts.append(prompt)
        return '{"status":"blocked","reason":"koniec testu"}'

    with patch("forge.orchestrate._call_role", side_effect=capture_tester), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate._fail_task"):
        orchestrate.run_task(
            cfg, str(tmp_path), state, lambda phase: phase)

    assert "FAIL integration_test" in tester_prompts[0]
    assert "trace tail" in tester_prompts[0]


def test_red_commit_gate_announces_itself_in_log_and_ledger(
        tmp_path: Path) -> None:
    """Cicha bramka wyglądała jak zwis: po `finalize` zadanie wracało do
    testera bez śladu w logu, a mistrz — który widzi wyłącznie dziennik —
    dostawał niewyjaśnioną lukę i wypełniał ją domysłem o urwanym cyklu."""
    _task, state, cfg = _task_repo(tmp_path)
    lines: list[str] = []

    with patch("forge.orchestrate._call_role",
               return_value='{"status":"review"}'), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate.log", side_effect=lines.append), \
         patch("forge.agents.run_agent",
               return_value='{"verdict":"approve"}'), \
         patch("forge.orchestrate.build_then_test_result",
               return_value=(False, "FAIL integration_test\ntrace tail")):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    announced = [line for line in lines if "bramka przed commitem" in line]
    assert any("pełny pakiet" in line for line in announced), lines
    assert any("CZERWONA" in line for line in announced), lines
    journal = ledger.tail(str(tmp_path))
    assert "bramka przed commitem CZERWONA" in journal
    assert "FAIL integration_test" in journal


def test_reviewer_write_announces_itself_in_log_and_ledger(
        tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    lines: list[str] = []

    def modifying_review(_agent, _prompt, _cfg, project, _log, **_kwargs):
        Path(project, "reviewer-change.py").write_text(
            "bad = True\n", encoding="utf-8")
        return '{"verdict":"approve"}'

    with patch("forge.orchestrate._call_role",
               return_value='{"status":"review"}'), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate.log", side_effect=lines.append), \
         patch("forge.agents.run_agent", side_effect=modifying_review):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert any("read-only" in line and "reviewer-change.py" in line
               for line in lines), lines
    assert "reviewer-change.py" in ledger.tail(str(tmp_path))


def test_tester_works_on_full_suite_after_gate_regression(
        tmp_path: Path) -> None:
    """Sygnał regresji wymusza jedną turę, nie połyka potwierdzenia po green."""
    _task, state, cfg = _task_repo(tmp_path)
    tester_answers = iter((
        '{"status":"review"}',
        '{"status":"code","command":"python3 -m pytest -q",'
        '"reason":"odtworzona regresja"}',
        '{"status":"review"}',
    ))
    tester_prompts: list[str] = []

    def role_call(_cfg, _project, _state, role, prompt, _log):
        if role == "tester":
            tester_prompts.append(prompt)
            return next(tester_answers)
        return '{"status":"green","summary":"regresja naprawiona"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.agents.run_agent",
               return_value='{"verdict":"approve"}'), \
         patch("forge.orchestrate.build_then_test_result",
               side_effect=((False, "FAIL integration_test"), (True, "ok"))):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)
        assert state.suite_regression
        tester_prompts.clear()
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert len(tester_prompts) == 2
    assert state.test_cmd in tester_prompts[0]
    assert "PEŁNA BRAMKA wykryła regresję" in tester_prompts[0]
    assert "tej komendy nie zawężaj" in tester_prompts[0]
    assert "TURA POTWIERDZAJĄCA" in tester_prompts[1]
    assert "PEŁNA BRAMKA wykryła regresję" not in tester_prompts[1]
    assert not state.suite_regression


def test_tester_chooses_command_and_commit_gate_uses_full_suite(
        tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    prompts_seen: list[str] = []

    def role_call(_cfg, _project, _state, role, prompt, _log):
        if role == "tester":
            prompts_seen.append(prompt)
            return '{"status":"review"}'
        raise AssertionError("coder should not run")

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.agents.run_agent",
               return_value='{"verdict":"approve"}'), \
         patch("forge.orchestrate.build_then_test_result",
               return_value=(True, "ok")) as gate:
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert state.test_cmd in prompts_seen[0]
    assert "fallbackiem, nie domyślną komendą" in prompts_seen[0]
    gate.assert_called_once_with(
        str(tmp_path), state.build_cmd, state.test_cmd, cfg.agent_timeout_s)


def test_confirmation_reuses_tester_command_without_requiring_full_suite(
        tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    tester_answers = iter((
        '{"status":"red","command":"python3 -m pytest -q tests/test_app.py",'
        '"reason":"VALUE ma być 1"}',
        '{"status":"review"}',
    ))
    tester_prompts: list[str] = []
    coder_prompts: list[str] = []

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
        coder_prompts.append(prompt)
        Path(project, "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        return '{"status":"green","summary":"VALUE ustawione na 1"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.agents.run_agent",
               return_value='{"verdict":"approve"}'), \
         patch("forge.orchestrate.build_then_test_result",
               return_value=(True, "ok")):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    command = "python3 -m pytest -q tests/test_app.py"
    assert "TURA POTWIERDZAJĄCA" not in tester_prompts[0]
    assert "TURA POTWIERDZAJĄCA" in tester_prompts[1]
    assert command in coder_prompts[0]
    assert f"ostatniej bramki testera `{command}`" in tester_prompts[1]
    assert "należy do Forge przed commitem" in tester_prompts[1]
    assert f"uruchom `{state.test_cmd}`" not in tester_prompts[1]
    assert "Nie oceniaj jakości implementacji" in tester_prompts[1]


def test_legacy_coder_checkpoint_without_command_falls_back_to_full_suite(
        tmp_path: Path) -> None:
    task, state, cfg = _task_repo(tmp_path)
    state.current_task = task
    state.task_queue = []
    state.task_phase = "coder"
    state.task_start_tag = "forge/task-001-start"
    state.tester_decision = {"status": "code", "reason": "stary checkpoint"}
    _git(tmp_path, "tag", state.task_start_tag)
    coder_prompts: list[str] = []

    def role_call(_cfg, _project, _state, role, prompt, _log):
        if role == "coder":
            coder_prompts.append(prompt)
            return '{"status":"green","summary":"bez zmian"}'
        return '{"status":"review"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.agents.run_agent",
               return_value='{"verdict":"approve"}'), \
         patch("forge.orchestrate.build_then_test_result",
               return_value=(True, "ok")):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert len(coder_prompts) == 1
    assert f"Bramka testera: {state.test_cmd}" in coder_prompts[0]


def test_review_suggestions_can_be_rejected_and_finalized_without_rereview(
        tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    tester_answers = iter((
        '{"status":"review"}',
        '{"status":"finalize",'
        '"reason":"sugestia odrzucona: obecna nazwa opisuje kontrakt"}',
    ))
    tester_prompts: list[str] = []

    def role_call(_cfg, _project, _state, role, prompt, _log):
        assert role == "tester"
        tester_prompts.append(prompt)
        return next(tester_answers)

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.agents.run_agent", return_value=(
             '{"verdict":"suggestions","notes":'
             '["rozważ krótszą nazwę helpera"]}')) as reviewer, \
         patch("forge.orchestrate.build_then_test_result",
               return_value=(True, "ok")):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)
        assert state.task_phase == "tester"
        assert state.review_suggestions_pending
        assert state.current_task
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert reviewer.call_count == 1
    assert "rozważ krótszą nazwę helpera" in tester_prompts[1]
    assert "finalize" in tester_prompts[1]
    assert not state.review_suggestions_pending
    assert state.current_task == {}
    assert not (tmp_path / "BACKLOG.md").exists()


def test_nits_do_not_reopen_tdd_loop(tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    roles: list[str] = []

    def role_call(_cfg, _project, _state, role, _prompt, _log):
        roles.append(role)
        assert role == "tester"
        return '{"status":"review"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.agents.run_agent", return_value=(
             '{"verdict":"approve","notes":[],"nits":["skrót docstringa"]}')), \
         patch("forge.orchestrate.build_then_test_result", return_value=(True, "ok")):
        assert orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert roles == ["tester"]
    assert not state.review_suggestions_pending
    assert state.current_task == {}
    assert "task-001: skrót docstringa" in (
        tmp_path / ".forge" / "nits.md").read_text(encoding="utf-8")


def test_nits_follow_configured_runtime_dir(tmp_path: Path) -> None:
    cfg = Config(runtime_dir=".forge-runtime", git_push=False)

    orchestrate._append_review_nits(cfg, str(tmp_path), "task-001", ["kosmetyka"])

    assert (tmp_path / ".forge-runtime" / "nits.md").read_text(
        encoding="utf-8") == "- task-001: kosmetyka\n"


def test_interrupt_after_finalize_resumes_at_commit(
        tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    tester_answers = iter((
        '{"status":"review"}',
        '{"status":"finalize","reason":"sugestia świadomie odrzucona"}',
    ))
    real_append = orchestrate.ledger.append

    def interrupt_finalize(project: str, line: str) -> None:
        if "sugestie→finalize" in line:
            raise KeyboardInterrupt
        real_append(project, line)

    with patch(
            "forge.orchestrate._call_role",
            side_effect=lambda *_args: next(tester_answers)), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.agents.run_agent", return_value=(
             '{"verdict":"suggestions","notes":["rozważ krótszą nazwę"]}'
         )):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)
        with patch(
                "forge.orchestrate.ledger.append",
                side_effect=interrupt_finalize):
            with pytest.raises(KeyboardInterrupt):
                orchestrate.run_task(
                    cfg, str(tmp_path), state, lambda phase: phase)

    saved = State.load(str(tmp_path / ".forge" / "STATE.json"))
    assert state.task_phase == saved.task_phase == "commit"
    assert not saved.review_suggestions_pending
    assert saved.review_notes == []

    with patch(
            "forge.orchestrate.build_then_test_result",
            return_value=(True, "ok")):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert state.current_task == {}


def test_review_suggestions_can_be_applied_by_coder_without_rereview(
        tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    tester_answers = iter((
        '{"status":"review"}',
        '{"status":"code","command":"python3 -m pytest -q tests/test_app.py",'
        '"reason":"stosuję sugestię: uprość VALUE do 1"}',
        '{"status":"finalize",'
        '"reason":"sugestia zastosowana; celowana bramka zielona"}',
    ))
    coder_prompts: list[str] = []

    def role_call(_cfg, project, _state, role, prompt, _log):
        if role == "tester":
            return next(tester_answers)
        coder_prompts.append(prompt)
        Path(project, "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        return '{"status":"green","summary":"ustawiono VALUE=1"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.agents.run_agent", return_value=(
             '{"verdict":"suggestions","notes":["uprość VALUE do 1"]}'
         )) as reviewer, \
         patch("forge.orchestrate.build_then_test_result",
               return_value=(True, "ok")):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert reviewer.call_count == 1
    assert len(coder_prompts) == 1
    assert "stosuję sugestię" in coder_prompts[0]
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert state.current_task == {}


def test_suggestions_cycle_never_opens_a_second_review(
        tmp_path: Path) -> None:
    """Drobne uwagi nie kupują drugiej recenzji. Recenzent zaakceptował diff,
    więc kolejna tura recenzji nie ma czego rozstrzygnąć — a może odesłać pracę
    na następne okrążenie. `review` ze starego checkpointu domyka zadanie."""
    _task, state, cfg = _task_repo(tmp_path)
    tester_answers = iter((
        '{"status":"review"}',
        '{"status":"review","reason":"stary checkpoint sprzed zmiany kontraktu"}',
    ))

    with patch(
            "forge.orchestrate._call_role",
            side_effect=lambda *_args: next(tester_answers)), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch(
             "forge.agents.run_agent",
             return_value=(
                 '{"verdict":"suggestions",'
                 '"notes":["rozważ zmianę granicy modułu"]}')) as reviewer, \
         patch("forge.orchestrate.build_then_test_result",
               return_value=(True, "ok")):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert reviewer.call_count == 1
    assert state.current_task == {}


def test_request_changes_notes_do_not_enter_backlog_before_fix(
        tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)

    with patch("forge.orchestrate._call_role",
               return_value='{"status":"review"}'), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.agents.run_agent", return_value=(
             '{"verdict":"request_changes","notes":["napraw kontrakt"]}')):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert state.task_phase == "tester"
    assert state.review_notes == ["napraw kontrakt"]
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

    with patch("forge.agents.run_agent", return_value='{"verdict":"approve"}') as reviewer, \
         patch("forge.agents.run_agent_session") as session_call:
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
         patch("forge.agents.run_agent", side_effect=modifying_review):
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
            return ('{"status":"red",'
                    '"command":"python3 -m pytest -q tests/test_app.py"}')
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
    assert state.review_notes == ["ustaw 3"]


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
    assert "Powód odrzucenia: agent nie zwrócił poprawnego JSON-a" in prompts[1]


def test_json_retry_prompt_carries_decoder_position() -> None:
    answers = iter((
        '```json\n{"summary":"wariant „zaplanowany". dalej","replan":}\n```',
        '{"status":"review"}',
    ))
    prompts_seen = []

    result = orchestrate._decision_with_retry(
        "base",
        lambda prompt: prompts_seen.append(prompt) or next(answers),
        orchestrate.parse_tester_decision)

    assert result.status == "review"
    assert "kolumna" in prompts_seen[1]
    assert "zaplanowany" in prompts_seen[1]


def test_invalid_tester_decision_retry_explains_missing_command() -> None:
    answers = iter((
        '{"status":"red"}',
        '{"status":"red","command":"pytest tests/test_app.py"}',
    ))
    prompts = []

    result = orchestrate._decision_with_retry(
        "base",
        lambda prompt: prompts.append(prompt) or next(answers),
        orchestrate.parse_tester_decision)

    assert result.status == "red"
    assert result.data["command"] == "pytest tests/test_app.py"
    assert (
        "Powód odrzucenia: decyzja testera 'red' wymaga niepustego `command`"
        in prompts[1]
    )


def test_finalize_outside_suggestions_gets_format_retry(
        tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    answers = iter((
        '{"status":"finalize","reason":"za wcześnie"}',
        '{"status":"review"}',
    ))
    tester_prompts: list[str] = []

    def role_call(_cfg, _project, _state, role, prompt, _log):
        assert role == "tester"
        tester_prompts.append(prompt)
        return next(answers)

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.agents.run_agent",
               return_value='{"verdict":"approve"}'):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert len(tester_prompts) == 2
    assert "`finalize` jest dozwolone tylko po werdykcie suggestions" \
        in tester_prompts[1]


def test_second_invalid_decision_stops() -> None:
    calls = []
    with pytest.raises(InvalidDecision):
        orchestrate._decision_with_retry(
            "base",
            lambda prompt: calls.append(prompt) or "still-not-json",
            orchestrate.parse_tester_decision)
    assert len(calls) == 2


def test_second_invalid_decision_carries_both_raw_attempts() -> None:
    answers = iter(("pierwsze wyjście", "drugie wyjście"))

    with pytest.raises(InvalidDecision) as caught:
        orchestrate._decision_with_retry(
            "base", lambda _prompt: next(answers),
            orchestrate.parse_tester_decision)

    assert caught.value.raw_attempts == ["pierwsze wyjście", "drugie wyjście"]


def test_dump_invalid_decision_writes_both_attempts(tmp_path: Path) -> None:
    state = State()
    state.current_task = {"id": "task-645"}
    state.task_phase = "review"
    exc = InvalidDecision("agent nie zwrócił poprawnego JSON-a")
    exc.raw_attempts = ["surowe A", "surowe B"]

    orchestrate._dump_invalid_decision(
        str(tmp_path), Config(), state, exc)

    dumps = list((tmp_path / ".forge" / "failed" / "task-645"
                  / "invalid_json").glob("*.txt"))
    assert len(dumps) == 1
    body = dumps[0].read_text(encoding="utf-8")
    assert "faza=review" in body
    assert "agent nie zwrócił poprawnego JSON-a" in body
    assert "surowe A" in body and "surowe B" in body


def test_dump_invalid_decision_without_raw_attempts_writes_nothing(
        tmp_path: Path) -> None:
    orchestrate._dump_invalid_decision(
        str(tmp_path), Config(), State(),
        InvalidDecision("niedozwolony werdykt"))

    assert not (tmp_path / ".forge").exists()


def test_dump_invalid_decision_survives_unwritable_target(
        tmp_path: Path) -> None:
    """Zrzut biegnie w handlerze ostatniej szansy: OSError nie może zjeść
    komunikatu o bezpiecznym zatrzymaniu ani kontraktu kodów wyjścia."""
    state = State()
    state.current_task = {"id": "task-645"}
    blocker = tmp_path / ".forge" / "failed"
    blocker.parent.mkdir(parents=True)
    blocker.write_text("plik tam, gdzie orkiestrator chce katalog\n",
                       encoding="utf-8")
    exc = InvalidDecision("agent nie zwrócił poprawnego JSON-a")
    exc.raw_attempts = ["surowe A", "surowe B"]

    orchestrate._dump_invalid_decision(str(tmp_path), Config(), state, exc)

    assert blocker.is_file()


def test_agent_session_files_are_not_a_reviewer_edit(tmp_path: Path) -> None:
    """Opencode zakłada w projekcie katalog sesji przy KAŻDYM wywołaniu. Bez
    odsiania tych plików każda recenzja „zmieniała drzewo" i wracała do testera
    mimo approve — jeden bieg zrobił tak 108 okrążeń i nie zacommitował nic."""
    _task, state, cfg = _task_repo(tmp_path)
    roles: list[str] = []

    def session_writing_review(_agent, _prompt, _cfg, project, _log, **_kwargs):
        session = Path(project, ".opencode", "goals", "state.json.sessions", "abc")
        session.mkdir(parents=True, exist_ok=True)
        session.joinpath("state.json").write_text("{}", encoding="utf-8")
        return '{"verdict":"approve","notes":[]}'

    def role_call(_cfg, _project, _state, role, _prompt, _log):
        roles.append(role)
        return '{"status":"review"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.agents.run_agent", side_effect=session_writing_review), \
         patch("forge.orchestrate.build_then_test_result", return_value=(True, "ok")):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert roles == ["tester"]
    assert state.current_task == {}
    assert "review-zapis" not in ledger.tail(str(tmp_path))


def test_reviewer_write_is_settled_by_the_tester_who_then_delivers(
        tmp_path: Path) -> None:
    """Zapis read-only reviewera ocenia tester i sam dostarcza. Mechaniczna
    bramka odsyłała to na kolejną recenzję, która znów pisała po drzewie."""
    _task, state, cfg = _task_repo(tmp_path)
    tester_answers = iter((
        '{"status":"review"}',
        '{"status":"finalize","reason":"zmiana recenzenta zachowana"}',
    ))
    tester_prompts: list[str] = []

    def modifying_review(_agent, _prompt, _cfg, project, _log, **_kwargs):
        Path(project, "reviewer-change.py").write_text(
            "VALUE = 1\n", encoding="utf-8")
        return '{"verdict":"approve","notes":[]}'

    def role_call(_cfg, _project, _state, role, prompt, _log):
        assert role == "tester"
        tester_prompts.append(prompt)
        return next(tester_answers)

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.agents.run_agent", side_effect=modifying_review) as reviewer, \
         patch("forge.orchestrate.build_then_test_result", return_value=(True, "ok")):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)
        assert state.review_suggestions_pending
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert reviewer.call_count == 1
    assert state.current_task == {}
    assert "reviewer-change.py" in tester_prompts[1]
    assert "read-only" in tester_prompts[1]
    assert '"status":"red|code|finalize|blocked"' in tester_prompts[1]


def test_idle_review_cycles_end_the_task_instead_of_looping(
        tmp_path: Path) -> None:
    """Powrót z recenzji, po którym drzewo wygląda identycznie, nie wnosi nic —
    a powtórzony bez końca kosztuje tyle, co cały przebieg."""
    cfg = Config(git_push=False, max_review_cycles=2)
    _task, state, _cfg = _task_repo(tmp_path)

    with patch("forge.orchestrate._call_role",
               return_value='{"status":"review"}'), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.agents.run_agent", return_value=(
             '{"verdict":"request_changes","notes":["napraw kontrakt"]}')), \
         patch("forge.orchestrate.build_then_test_result", return_value=(True, "ok")):
        for _ in range(cfg.max_review_cycles + 1):
            assert orchestrate.run_task(
                cfg, str(tmp_path), state, lambda phase: phase)

    journal = ledger.tail(str(tmp_path))
    assert "PORZUCONE: review_loop" in journal
    assert state.current_task == {}
    assert (tmp_path / ".forge" / "failed" / "task-001" / "reason.txt").exists()


def test_real_progress_resets_the_review_cycle_counter(tmp_path: Path) -> None:
    """Licznik pilnuje JAŁOWYCH okrążeń. Dopóki tester faktycznie poprawia kod,
    kolejne rundy recenzji są normalną pracą i nie mają prawa zabić zadania.

    Budżet blokad (`max_review_turns`) jest tu świadomie wyłączony: to osobny
    bezpiecznik na churn produktywny i ma własny test. Ten sprawdza wyłącznie,
    że postęp zeruje licznik livelocka.
    """
    cfg = Config(git_push=False, max_review_cycles=2, max_review_turns=99)
    _task, state, _cfg = _task_repo(tmp_path)
    edits = iter(range(1, 99))

    def role_call(_cfg, project, _state, _role, _prompt, _log):
        Path(project, "app.py").write_text(
            f"VALUE = {next(edits)}\n", encoding="utf-8")
        return '{"status":"review"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.agents.run_agent", return_value=(
             '{"verdict":"request_changes","notes":["jeszcze raz"]}')), \
         patch("forge.orchestrate.build_then_test_result", return_value=(True, "ok")):
        for _ in range(cfg.max_review_cycles + 2):
            assert orchestrate.run_task(
                cfg, str(tmp_path), state, lambda phase: phase)

    assert state.review_cycles == 1
    assert "PORZUCONE" not in ledger.tail(str(tmp_path))


def test_review_turn_budget_degrades_a_blocking_verdict_instead_of_killing_task(
        tmp_path: Path) -> None:
    """Churn produktywny ma sufit, a jego przekroczenie nie kasuje pracy.

    `max_review_cycles` mierzy okrążenia JAŁOWE i z definicji nie widzi tego
    przypadku: recenzent zgłasza za każdym razem inną uwagę, koder ją stosuje,
    odcisk drzewa się zmienia, licznik wraca do zera — a zadanie kręci się bez
    końca, wyglądając na postęp. Trzecia blokada degraduje się więc do
    sugestii: zadanie idzie do commitu, a uwaga zostaje trwałym śladem.
    """
    cfg = Config(git_push=False, max_review_cycles=99, max_review_turns=2)
    _task, state, _cfg = _task_repo(tmp_path)
    edits = iter(range(1, 99))

    def role_call(_cfg, project, _state, _role, _prompt, _log):
        Path(project, "app.py").write_text(
            f"VALUE = {next(edits)}\n", encoding="utf-8")
        return '{"status":"review"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.agents.run_agent", return_value=(
             '{"verdict":"request_changes","notes":["wciąż coś nowego"]}')), \
         patch("forge.orchestrate.build_then_test_result", return_value=(True, "ok")):
        for _ in range(12):
            if not orchestrate.run_task(
                    cfg, str(tmp_path), state, lambda phase: phase):
                break
            if not state.current_task:
                break

    tail = ledger.tail(str(tmp_path))
    assert "review-degradacja" in tail
    assert "PORZUCONE" not in tail
    assert "UKOŃCZONE" in tail
    # Uwaga nie ginie razem z blokadą — zostaje w trwałym śladzie audytowym.
    nits = Path(tmp_path, ".forge", "nits.md")
    assert nits.is_file() and "wciąż coś nowego" in nits.read_text(encoding="utf-8")
