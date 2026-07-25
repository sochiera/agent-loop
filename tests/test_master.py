from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from forge import ledger, orchestrate, prompts
from forge.agents import AgentError, LimitExhausted
from forge.config import Config
from forge.state import State


def _git(project: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=project, check=True, text=True, capture_output=True)


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
    state = State(bootstrapped=True, test_cmd="python3 -m pytest -q",
                  task_queue=[task])
    return task, state, Config(git_push=False)


def _one_round(tmp_path: Path):
    """Tester → koder → tester(review): jedna pełna runda TDD."""
    tester_answers = iter(('{"status":"red"}', '{"status":"review"}'))
    seen: dict[str, str] = {}

    def role_call(_cfg, project, _state, role, prompt, _log):
        seen.setdefault(role, prompt)
        if role == "tester":
            answer = next(tester_answers)
            if '"red"' in answer:
                path = Path(project, "tests", "test_app.py")
                path.write_text(
                    path.read_text(encoding="utf-8")
                    + "\ndef test_new():\n    assert VALUE == 1\n",
                    encoding="utf-8")
            return answer
        Path(project, "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        return '{"status":"green"}'

    return role_call, seen


# --- Prompt mistrza ---------------------------------------------------------

def test_master_prompt_is_process_only_and_carries_ledger() -> None:
    prompt = prompts.master_prompt("[10:00] task-001 r1 tester→red")

    assert "MISTRZ" in prompt
    assert "[10:00] task-001 r1 tester→red" in prompt
    # Milczenie jest odpowiedzią domyślną — inaczej mistrz zatruwa każdy prompt.
    assert "pust" in prompt.lower()
    assert '"tester"' in prompt and '"coder"' in prompt and '"planner"' in prompt


def test_empty_note_adds_nothing_to_prompt() -> None:
    assert prompts.master_note_suffix("") == ""
    assert prompts.master_note_suffix("   ") == ""


def test_note_is_labelled_as_advisory() -> None:
    suffix = prompts.master_note_suffix("przestań odbijać bez konkretu")

    assert "przestań odbijać bez konkretu" in suffix
    assert "MISTRZA" in suffix
    # Kryteria zadania pozostają nadrzędne — nota nie może ich nadpisywać.
    assert "nie zmienia kryteriów" in suffix


# --- Routing roli -----------------------------------------------------------

def test_master_runs_at_efficient_level() -> None:
    cfg = Config()

    assert cfg.model_level("master") == "efficient"
    assert cfg.role("master")[0]


# --- Wstrzyknięcie feedbacku ------------------------------------------------

def test_master_notes_reach_tester_and_coder_prompts(tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    role_call, seen = _one_round(tmp_path)

    def agent_call(_agent, prompt, _cfg, _project, _log, **_kwargs):
        if "MISTRZ" in prompt:
            return '{"tester":"nie powtarzaj testu","coder":"podaj konkretną linię"}'
        return '{"verdict":"approve"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._run_boundary", return_value=(True, ["ok"])), \
         patch("forge.orchestrate.run_agent", side_effect=agent_call):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert "nie powtarzaj testu" in seen["tester"]
    assert "podaj konkretną linię" in seen["coder"]


def test_master_sees_ledger_history(tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    ledger.append(str(tmp_path), "task-000 r9 koder→test_changes_needed")
    role_call, _seen = _one_round(tmp_path)
    master_prompts: list[str] = []

    def agent_call(_agent, prompt, _cfg, _project, _log, **_kwargs):
        if "MISTRZ" in prompt:
            master_prompts.append(prompt)
            return "{}"
        return '{"verdict":"approve"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._run_boundary", return_value=(True, ["ok"])), \
         patch("forge.orchestrate.run_agent", side_effect=agent_call):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert master_prompts
    assert "task-000 r9 koder→test_changes_needed" in master_prompts[0]


def test_round_decisions_are_recorded_in_ledger(tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    role_call, _seen = _one_round(tmp_path)

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._run_boundary", return_value=(True, ["ok"])), \
         patch("forge.orchestrate.run_agent", return_value='{"verdict":"approve"}'):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    text = ledger.tail(str(tmp_path))
    assert "task-001" in text
    assert "tester" in text and "koder" in text
    assert "red" in text and "green" in text


def test_ledger_records_whether_the_turn_changed_files(tmp_path: Path) -> None:
    """Mistrz ma odróżniać kolejną legalną decyzję od pętli bez postępu —
    sam status decyzji tego nie niesie."""
    _task, state, cfg = _task_repo(tmp_path)
    role_call, _seen = _one_round(tmp_path)

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate._run_boundary", return_value=(True, ["ok"])), \
         patch("forge.orchestrate.run_agent", return_value='{"verdict":"approve"}'):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    lines = ledger.tail(str(tmp_path)).splitlines()
    coder_line = next(line for line in lines if "koder→green" in line)
    tester_line = next(line for line in lines if "tester→red" in line)
    # Koder i tester realnie edytowali pliki w tej rundzie.
    assert "pliki=zmienione" in coder_line
    assert "pliki=zmienione" in tester_line


def test_ledger_marks_turn_without_file_changes(tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)

    def role_call(_cfg, _project, _state, role, _prompt, _log):
        # Nikt nic nie zmienia — dokładnie kształt pętli z task-381.
        if role == "tester":
            return '{"status":"red"}'
        return '{"status":"test_changes_needed","reason":"zmień test"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate._fail_task"):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    lines = ledger.tail(str(tmp_path)).splitlines()
    assert any("koder→test_changes_needed" in line and "pliki=bez_zmian" in line
               for line in lines)


def test_master_is_consulted_when_resuming_straight_into_coder(tmp_path: Path) -> None:
    """Po restarcie w fazie kodera pętla omija testera — rada dla kodera
    nie może przez to zniknąć."""
    task, state, cfg = _task_repo(tmp_path)
    state.current_task = task
    state.task_queue = []
    state.task_phase = "coder"
    state.tester_decision = {"status": "red", "reason": "czerwony test"}
    state.task_start_tag = "forge/task-001-start"
    _git(tmp_path, "tag", state.task_start_tag)
    seen: dict[str, str] = {}

    def role_call(_cfg, project, _state, role, prompt, _log):
        seen.setdefault(role, prompt)
        if role == "coder":
            Path(project, "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            return '{"status":"green"}'
        return '{"status":"review"}'

    def agent_call(_agent, prompt, _cfg, _project, _log, **_kwargs):
        if "MISTRZ" in prompt:
            return '{"coder":"nie odbijaj bez wskazania linii"}'
        return '{"verdict":"approve"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._run_boundary", return_value=(True, ["ok"])), \
         patch("forge.orchestrate.run_agent", side_effect=agent_call):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert "nie odbijaj bez wskazania linii" in seen["coder"]


def test_master_is_consulted_once_per_round(tmp_path: Path) -> None:
    """Milczenie mistrza to najczęstszy przypadek — nie może kosztować
    drugiego wywołania w tej samej rundzie."""
    _task, state, cfg = _task_repo(tmp_path)
    role_call, _seen = _one_round(tmp_path)
    calls: list[str] = []

    def agent_call(_agent, prompt, _cfg, _project, _log, **_kwargs):
        if "MISTRZ" in prompt:
            calls.append("master")
            return '{"tester":"","coder":"","planner":""}'
        return '{"verdict":"approve"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._run_boundary", return_value=(True, ["ok"])), \
         patch("forge.orchestrate.run_agent", side_effect=agent_call):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    # Dwie tury testera = dwie rundy = dokładnie dwa wywołania mistrza.
    assert len(calls) == 2


# --- Niezawodność: mistrz jest wyłącznie doradczy ---------------------------

def test_master_failure_leaves_pipeline_untouched(tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    role_call, seen = _one_round(tmp_path)

    def agent_call(_agent, prompt, _cfg, _project, _log, **_kwargs):
        if "MISTRZ" in prompt:
            raise AgentError("mistrz padł")
        return '{"verdict":"approve"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._run_boundary", return_value=(True, ["ok"])), \
         patch("forge.orchestrate.run_agent", side_effect=agent_call):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert "MISTRZA" not in seen["tester"]
    assert state.current_task == {}
    assert _git(tmp_path, "log", "-1", "--pretty=%s").stdout.strip() == "feat: Zmiana wartości"


def test_master_limit_does_not_stop_the_run(tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    role_call, _seen = _one_round(tmp_path)

    def agent_call(_agent, prompt, _cfg, _project, _log, **_kwargs):
        if "MISTRZ" in prompt:
            raise LimitExhausted("limit")
        return '{"verdict":"approve"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._run_boundary", return_value=(True, ["ok"])), \
         patch("forge.orchestrate.run_agent", side_effect=agent_call):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert _git(tmp_path, "log", "-1", "--pretty=%s").stdout.strip() == "feat: Zmiana wartości"


def test_master_garbage_answer_is_ignored(tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    role_call, seen = _one_round(tmp_path)

    def agent_call(_agent, prompt, _cfg, _project, _log, **_kwargs):
        if "MISTRZ" in prompt:
            return "kompletnie nie-JSON"
        return '{"verdict":"approve"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._run_boundary", return_value=(True, ["ok"])), \
         patch("forge.orchestrate.run_agent", side_effect=agent_call):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert "MISTRZA" not in seen["tester"]
    assert state.current_task == {}


def test_master_unexpected_crash_is_swallowed(tmp_path: Path) -> None:
    """Nawet nieprzewidziany typ awarii mistrza nie może zatrzymać pipeline'u."""
    _task, state, cfg = _task_repo(tmp_path)
    role_call, _seen = _one_round(tmp_path)

    def agent_call(_agent, prompt, _cfg, _project, _log, **_kwargs):
        if "MISTRZ" in prompt:
            raise TypeError("nieoczekiwany kształt odpowiedzi")
        return '{"verdict":"approve"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._run_boundary", return_value=(True, ["ok"])), \
         patch("forge.orchestrate.run_agent", side_effect=agent_call):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert _git(tmp_path, "log", "-1", "--pretty=%s").stdout.strip() == "feat: Zmiana wartości"


def test_master_never_owns_the_backoff(tmp_path: Path) -> None:
    """Doradcza rola nie może przespać godzin backoffu przed realną pracą."""
    _task, state, cfg = _task_repo(tmp_path)
    role_call, _seen = _one_round(tmp_path)
    retries: list[int] = []

    def agent_call(_agent, prompt, call_cfg, _project, _log, **_kwargs):
        if "MISTRZ" in prompt:
            retries.append(call_cfg.max_limit_retries)
            return "{}"
        return '{"verdict":"approve"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._run_boundary", return_value=(True, ["ok"])), \
         patch("forge.orchestrate.run_agent", side_effect=agent_call):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert retries and all(value == 0 for value in retries)
    assert cfg.max_limit_retries > 0  # realne role zachowują pełny backoff


def test_master_cannot_change_the_worktree(tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    role_call, _seen = _one_round(tmp_path)

    def agent_call(_agent, prompt, _cfg, project, _log, **_kwargs):
        if "MISTRZ" in prompt:
            Path(project, "mistrz-zmiana.py").write_text("bad = True\n", encoding="utf-8")
            return '{"tester":"cokolwiek"}'
        return '{"verdict":"approve"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._run_boundary", return_value=(True, ["ok"])), \
         patch("forge.orchestrate.run_agent", side_effect=agent_call):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert not (tmp_path / "mistrz-zmiana.py").exists()
