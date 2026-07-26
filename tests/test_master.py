from __future__ import annotations

import re
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
    }
    state = State(bootstrapped=True, test_cmd="python3 -m pytest -q",
                  task_queue=[task])
    return task, state, Config(git_push=False)


def _one_round(tmp_path: Path):
    """Tester → koder → tester(review): jedna pełna runda TDD."""
    tester_answers = iter((
        '{"status":"red","command":"python3 -m pytest -q tests/test_app.py"}',
        '{"status":"review"}'))
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

def _flat(text: str) -> str:
    """Prompt jest zawijany dla czytelności — asercje mają pilnować treści,
    nie miejsca łamania linii."""
    return " ".join(text.split())


def test_master_prompt_is_process_only_and_carries_ledger() -> None:
    prompt = _flat(prompts.master_prompt(
        "[10:00] task-001 r1 koder→green pliki=[tests/test_app.py]"))

    assert "MISTRZ" in prompt
    assert "tests/test_app.py" in prompt
    assert "poproś testera o świadomą ocenę" in prompt
    assert "`recenzja→changes`" in prompt
    assert "nie sugeruj rozwiązań technicznych" in prompt
    # Milczenie jest odpowiedzią domyślną — inaczej mistrz zatruwa każdy prompt.
    assert "pust" in prompt.lower()
    assert '"tester"' in prompt and '"coder"' in prompt and '"planner"' in prompt


def test_master_knows_process_and_legal_code_status() -> None:
    prompt = _flat(prompts.master_system_prompt())

    assert "`red` i `code` przekazują pracę koderowi" in prompt
    assert "`code` jest legalne" in prompt
    assert "`recenzja→changes`" in prompt
    assert "`recenzja→approve`" in prompt
    assert "pełnej bramki testów" in prompt


def test_master_only_intervenes_on_observable_process_patterns() -> None:
    prompt = _flat(prompts.master_system_prompt())

    assert "co najmniej dwie kolejne tury" in prompt
    assert "zmianę pliku testowego przez kodera" in prompt
    assert "kolejne `recenzja→changes` bez zmian" in prompt
    assert "co najmniej dwa zadania na liście `round_limit`" in prompt
    assert "nie oceniaj poprawności implementacji" in prompt
    assert "kompletności `reason`/`summary`" in prompt
    assert "UKOŃCZONE" in prompt and "PORZUCONE" in prompt
    assert "Nie uzupełniaj brakujących informacji domysłami" in prompt


def test_every_intervention_rule_says_what_to_ask_for() -> None:
    """Reguła bez zaleconego działania zmusza mistrza do improwizacji —
    a improwizacja to dokładnie ta merytoryka, której ma nie uprawiać."""
    block = prompts.master_system_prompt().partition(
        "Interweniuj tylko")[2].partition("\n\n")[0]
    rules = [_flat(rule) for rule in block.split("\n- ")[1:]]

    assert len(rules) == 4
    for rule in rules:
        assert "—" in rule, f"reguła bez zalecenia: {rule}"


def test_round_limit_rule_survives_the_ban_on_finished_tasks() -> None:
    """`round_limit` kończy zadanie jako PORZUCONE, więc bez wyjątku wprost
    obie reguły promptu wskazują przeciwnie."""
    assert "obowiązuje mimo `PORZUCONE`" in _flat(
        prompts.master_system_prompt())


def test_master_prompt_uses_vocabulary_the_ledger_actually_writes(
        tmp_path: Path) -> None:
    """Prompt każe dopasowywać wzorce dosłownie, więc jego słownik musi
    pochodzić z dziennika, a nie z tłumaczenia nazw ról (`recenzja` ≠ `review`).
    """
    _task, state, cfg = _task_repo(tmp_path)
    role_call, _seen = _one_round(tmp_path)

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate.run_agent", return_value='{"verdict":"approve"}'):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    def labels(text: str) -> set[str]:
        return set(re.findall(r"(\w+)→", text))

    written = labels(ledger.tail(str(tmp_path)))
    prompt = prompts.master_system_prompt()

    assert {"tester", "koder", "recenzja"} <= written
    assert written >= labels(prompt), (
        f"prompt mówi o zdarzeniach, których dziennik nie zapisuje: "
        f"{labels(prompt) - written}")
    assert all(label in prompt for label in written)


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

def test_master_runs_at_cheapest_level() -> None:
    """Wołany co rundę, więc siedzi na dnie drabinki modeli."""
    cfg = Config()

    assert cfg.model_level("master") == "economy"
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
         patch("forge.orchestrate.run_agent", side_effect=agent_call):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert master_prompts
    assert "task-000 r9 koder→test_changes_needed" in master_prompts[0]


def test_master_receives_compact_ledger_view(tmp_path: Path) -> None:
    for index in range(30):
        ledger.append(str(tmp_path), f"task-{index:03d} " + "x" * 250)

    with patch("forge.orchestrate.run_agent", return_value="{}") as run:
        orchestrate._master_notes(
            Config(), str(tmp_path), lambda phase: str(tmp_path / f"{phase}.log"))

    prompt = run.call_args.args[1]
    journal = prompt.split("DZIENNIK (najstarsze u góry):\n", 1)[1]
    lines = journal.splitlines()
    assert len(lines) == 20
    assert all(len(line) <= 120 for line in lines)


def test_master_receives_round_limit_failures_it_cannot_see_in_the_window(
        tmp_path: Path) -> None:
    """Reguła o zbyt grubych zadaniach potrzebuje dwóch porażek, a te nigdy
    nie mieszczą się razem w oknie dziennika."""
    ledger.append(str(tmp_path), "task-001 PORZUCONE: round_limit: limit 10")
    for index in range(25):
        ledger.append(str(tmp_path), f"task-002 r{index} tester→red pliki=bez_zmian: x")
    ledger.append(str(tmp_path), "task-002 PORZUCONE: round_limit: limit 10")

    with patch("forge.orchestrate.run_agent", return_value="{}") as run:
        orchestrate._master_notes(
            Config(), str(tmp_path), lambda phase: str(tmp_path / f"{phase}.log"))

    prompt = run.call_args.args[1]
    assert "task-001" not in prompt.split("DZIENNIK", 1)[1]
    assert "round_limit (cała pamięć dziennika): task-001, task-002" in prompt


def test_master_declares_thin_advisory_role(tmp_path: Path) -> None:
    with patch("forge.orchestrate.run_agent", return_value="{}") as run:
        orchestrate._master_notes(
            Config(), str(tmp_path), lambda phase: str(tmp_path / f"{phase}.log"))

    assert run.call_args.kwargs["thin"] is True
    assert "MISTRZ" in run.call_args.kwargs["system_prompt"]
    assert '"tester"' in run.call_args.kwargs["json_schema"]


def test_round_decisions_are_recorded_in_ledger(tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    role_call, _seen = _one_round(tmp_path)

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
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
         patch("forge.orchestrate.run_agent", return_value='{"verdict":"approve"}'):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    lines = ledger.tail(str(tmp_path)).splitlines()
    coder_line = next(line for line in lines if "koder→green" in line)
    tester_line = next(line for line in lines if "tester→red" in line)
    # Koder i tester realnie edytowali pliki w tej rundzie.
    assert "pliki=[app.py]" in coder_line
    assert "pliki=[tests/test_app.py]" in tester_line


def test_ledger_marks_turn_without_file_changes(tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)

    def role_call(_cfg, _project, _state, role, _prompt, _log):
        # Nikt nic nie zmienia — dokładnie kształt pętli z task-381.
        if role == "tester":
            return ('{"status":"red",'
                    '"command":"python3 -m pytest -q tests/test_app.py"}')
        return '{"status":"test_changes_needed","reason":"zmień test"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate._fail_task"):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    lines = ledger.tail(str(tmp_path)).splitlines()
    assert any("koder→test_changes_needed" in line and "pliki=bez_zmian" in line
               for line in lines)


def test_two_complete_rounds_without_changes_warn_next_tester(
        tmp_path: Path) -> None:
    _task, state, cfg = _task_repo(tmp_path)
    tester_answers = iter((
        '{"status":"red","command":"python3 -m pytest -q tests/test_app.py"}',
        '{"status":"red","command":"python3 -m pytest -q tests/test_app.py"}',
        '{"status":"blocked","reason":"brak bezpiecznej drogi"}',
    ))
    tester_prompts: list[str] = []

    def role_call(_cfg, _project, _state, role, prompt, _log):
        if role == "tester":
            tester_prompts.append(prompt)
            return next(tester_answers)
        return '{"status":"test_changes_needed","reason":"bez zmian"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate._fail_task"):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert "2 kolejne rundy bez zmian w plikach" in tester_prompts[2]
    assert "zmień podejście albo zwróć `blocked`" in tester_prompts[2]


def test_file_change_resets_no_change_round_counter(tmp_path: Path) -> None:
    task, state, cfg = _task_repo(tmp_path)
    state.current_task = task
    state.task_queue = []
    state.task_phase = "tester"
    state.task_start_tag = "forge/task-001-start"
    _git(tmp_path, "tag", state.task_start_tag)
    state.no_change_rounds = 2
    tester_answers = iter((
        '{"status":"red","command":"python3 -m pytest -q tests/test_app.py"}',
        '{"status":"blocked","reason":"done"}',
    ))

    def role_call(_cfg, project, _state, role, _prompt, _log):
        if role == "tester":
            answer = next(tester_answers)
            if '"red"' in answer:
                Path(project, "tests", "test_app.py").write_text(
                    "changed\n", encoding="utf-8")
            return answer
        return '{"status":"test_changes_needed","reason":"wróć"}'

    with patch("forge.orchestrate._call_role", side_effect=role_call), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate._fail_task"):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert state.no_change_rounds == 0


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
         patch("forge.orchestrate.run_agent", side_effect=agent_call):
        orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert not (tmp_path / "mistrz-zmiana.py").exists()
