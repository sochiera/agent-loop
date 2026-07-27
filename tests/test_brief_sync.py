"""Diff-bootstrap: wykrycie zmiany briefu i jej kontrolowana synchronizacja."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from forge import brief, orchestrate, prompts
from forge.config import Config
from forge.state import State
from forge.task_pipeline import InvalidDecision


def _git(project: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=project, check=True, text=True, capture_output=True)


def _synced_repo(tmp_path: Path) -> tuple[Path, State, Config]:
    """Projekt po bootstrapie: snapshot briefu zgodny z plikiem briefu."""
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init", "-q")
    _git(project, "config", "user.email", "tests@example.test")
    _git(project, "config", "user.name", "Forge Tests")
    (project / ".gitignore").write_text(".forge/\n", encoding="utf-8")
    (project / "BACKLOG.md").write_text("- [ ] stary wpis\n", encoding="utf-8")
    (project / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
    docs = project / "docs"
    docs.mkdir()
    (docs / "PROJECT.md").write_text("# Projekt\n\nStary cel.\n", encoding="utf-8")
    brief_path = tmp_path / "game.md"
    brief_path.write_text("Cel: gra.\n", encoding="utf-8")
    brief.write_snapshot(str(project), "Cel: gra.\n")
    _git(project, "add", ".")
    _git(project, "commit", "-qm", "seed")
    state = State(
        bootstrapped=True, test_cmd="python3 -m pytest -q",
        brief_digest=brief.digest("Cel: gra.\n"))
    return project, state, Config(brief_path=str(brief_path), git_push=False)


def _change_brief(cfg: Config, text: str) -> None:
    Path(cfg.brief_path).write_text(text, encoding="utf-8")


# --- Wykrywanie zmiany -------------------------------------------------------

def test_unchanged_brief_does_not_trigger_synchronisation(tmp_path: Path) -> None:
    project, state, cfg = _synced_repo(tmp_path)

    assert not orchestrate._brief_needs_sync(cfg, str(project), state)


def test_changed_brief_triggers_synchronisation(tmp_path: Path) -> None:
    project, state, cfg = _synced_repo(tmp_path)
    _change_brief(cfg, "Cel: gra.\nNowe wymaganie: tryb sieciowy.\n")

    assert orchestrate._brief_needs_sync(cfg, str(project), state)


def test_project_bootstrapped_before_the_mechanism_syncs_once(
        tmp_path: Path) -> None:
    """Brak snapshotu i skrótu = jednorazowa migracja, nie pełny bootstrap."""
    project, state, cfg = _synced_repo(tmp_path)
    Path(project, brief.SNAPSHOT_PATH).unlink()
    state.brief_digest = ""

    assert orchestrate._brief_needs_sync(cfg, str(project), state)


def test_missing_brief_file_never_triggers_synchronisation(
        tmp_path: Path) -> None:
    project, state, cfg = _synced_repo(tmp_path)
    Path(cfg.brief_path).unlink()

    assert not orchestrate._brief_needs_sync(cfg, str(project), state)


# --- Przebieg synchronizacji -------------------------------------------------

def _run_sync(project: Path, state: State, cfg: Config, answer: str,
              write=lambda _project: None):
    def agent(_name, _prompt, _cfg, project_dir, _log, **_kwargs):
        write(Path(project_dir))
        return answer

    with patch("forge.orchestrate.run_agent", side_effect=agent):
        orchestrate.phase_brief_sync(cfg, str(project), state, lambda phase: phase)


def test_synchronisation_records_snapshot_and_requeues_planning(
        tmp_path: Path) -> None:
    project, state, cfg = _synced_repo(tmp_path)
    state.task_queue = [{"id": "task-007", "title": "Stary plan"}]
    state.task_phase = "verify_goal"
    _change_brief(cfg, "Cel: gra.\nNowe wymaganie: tryb sieciowy.\n")

    def write(project_dir: Path) -> None:
        (project_dir / "BACKLOG.md").write_text(
            "- [ ] stary wpis\n- [ ] tryb sieciowy\n", encoding="utf-8")
        (project_dir / "docs" / "PROJECT.md").write_text(
            "# Projekt\n\nNowy cel.\n", encoding="utf-8")

    _run_sync(project, state, cfg,
              '{"summary":"tryb sieciowy","changes":["sieć"],"replan":true}',
              write)

    assert state.brief_digest == brief.digest(
        "Cel: gra.\nNowe wymaganie: tryb sieciowy.\n")
    assert Path(project, brief.SNAPSHOT_PATH).read_text(encoding="utf-8") == \
        "Cel: gra.\nNowe wymaganie: tryb sieciowy.\n"
    assert state.task_queue == []
    assert state.task_phase == ""
    assert not orchestrate.has_changes(str(project))
    note = Path(project, ".forge", "brief-change.md").read_text(encoding="utf-8")
    assert "tryb sieciowy" in note
    assert "task-007: Stary plan" in note


def test_cosmetic_change_keeps_planned_queue(tmp_path: Path) -> None:
    project, state, cfg = _synced_repo(tmp_path)
    state.task_queue = [{"id": "task-007", "title": "Stary plan"}]
    _change_brief(cfg, "Cel: gra!\n")

    _run_sync(project, state, cfg,
              '{"summary":"literówka","changes":[],"replan":false}')

    assert [task["id"] for task in state.task_queue] == ["task-007"]
    assert state.brief_digest == brief.digest("Cel: gra!\n")


def test_synchronisation_reverts_writes_outside_its_scope(
        tmp_path: Path) -> None:
    project, state, cfg = _synced_repo(tmp_path)
    _change_brief(cfg, "Cel: gra.\nNowe wymaganie: tryb sieciowy.\n")

    def write(project_dir: Path) -> None:
        (project_dir / "BACKLOG.md").write_text(
            "- [ ] tryb sieciowy\n", encoding="utf-8")
        (project_dir / "app.py").write_text("VALUE = 99\n", encoding="utf-8")
        (project_dir / "hack.py").write_text("print('nowy')\n", encoding="utf-8")

    _run_sync(project, state, cfg, '{"summary":"sieć","replan":true}', write)

    assert Path(project, "app.py").read_text(encoding="utf-8") == "VALUE = 0\n"
    assert not Path(project, "hack.py").exists()
    assert Path(project, "BACKLOG.md").read_text(encoding="utf-8") == \
        "- [ ] tryb sieciowy\n"


def test_failed_synchronisation_keeps_previous_brief_as_reference(
        tmp_path: Path) -> None:
    project, state, cfg = _synced_repo(tmp_path)
    _change_brief(cfg, "Cel: gra.\nNowe wymaganie: tryb sieciowy.\n")

    with pytest.raises(InvalidDecision):
        _run_sync(project, state, cfg, "bez JSON-a")

    assert state.brief_digest == brief.digest("Cel: gra.\n")
    assert Path(project, brief.SNAPSHOT_PATH).read_text(
        encoding="utf-8") == "Cel: gra.\n"
    assert orchestrate._brief_needs_sync(cfg, str(project), state)


# --- Osadzenie w pętli -------------------------------------------------------

def test_iteration_syncs_brief_before_planning(tmp_path: Path) -> None:
    project, state, cfg = _synced_repo(tmp_path)
    _change_brief(cfg, "Cel: gra.\nNowe wymaganie: tryb sieciowy.\n")

    with patch("forge.orchestrate.phase_brief_sync") as sync, \
         patch("forge.orchestrate.phase_plan_batch") as plan:
        orchestrate.one_iteration(cfg, str(project), state)

    sync.assert_called_once()
    plan.assert_not_called()


def test_iteration_never_syncs_during_an_active_task(tmp_path: Path) -> None:
    project, state, cfg = _synced_repo(tmp_path)
    state.current_task = {"id": "task-001", "title": "W toku",
                          "file": "task.md", "difficulty": "simple"}
    state.task_phase = "tester"
    _change_brief(cfg, "Cel: gra.\nNowe wymaganie: tryb sieciowy.\n")

    with patch("forge.orchestrate.phase_brief_sync") as sync, \
         patch("forge.orchestrate.run_task", return_value=True):
        orchestrate.one_iteration(cfg, str(project), state)

    sync.assert_not_called()


def test_planner_consumes_brief_change_note_exactly_once(
        tmp_path: Path) -> None:
    project, state, cfg = _synced_repo(tmp_path)
    note = Path(project, ".forge", "brief-change.md")
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# Zmiana briefu\n", encoding="utf-8")
    seen: list[str] = []

    def planner(prompt, *_args, **_kwargs):
        seen.append(prompt)
        return '{"no_more_tasks":true,"tasks":[]}'

    with patch("forge.orchestrate._housekeeping"), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate.run_planner", side_effect=planner):
        orchestrate.phase_plan_batch(cfg, str(project), state, lambda phase: phase)
        orchestrate.phase_plan_batch(cfg, str(project), state, lambda phase: phase)

    assert "brief-change.md" in seen[0]
    assert "brief-change.md" not in seen[1]
    assert not note.exists()


# --- Prompty i routing -------------------------------------------------------

def test_diff_bootstrap_prompt_states_its_narrow_write_scope() -> None:
    prompt = prompts.diff_bootstrap_prompt(
        "-stare\n+nowe", queued_tasks=["task-007: Stary plan"])

    assert "WYŁĄCZNIE BACKLOG.md oraz docs/PROJECT.md" in prompt
    assert "-stare" in prompt and "+nowe" in prompt
    assert "task-007: Stary plan" in prompt
    assert "Nie dotykaj kodu" in prompt
    assert "nie cofa ukończonego kodu" in prompt
    assert "Nie kasuj po cichu" in prompt
    assert '"replan":true' in prompt
    assert "PIERWSZA synchronizacja" not in prompt


def test_first_synchronisation_prompt_explains_missing_snapshot() -> None:
    prompt = prompts.diff_bootstrap_prompt("+cały brief", initial=True)

    assert "PIERWSZA synchronizacja" in prompt
    assert "traktuj zrealizowaną już część projektu jako fakt" in prompt


def test_bootstrap_materialises_project_context_for_the_planner() -> None:
    prompt = prompts.bootstrap_prompt("brief")

    assert "docs/PROJECT.md" in prompt
    assert "kryterium sukcesu" in prompt
    assert "klimat" in prompt
    assert "wymagań, preferencji i pomysłów opcjonalnych" in prompt


def test_planner_reads_project_context_instead_of_the_brief() -> None:
    prompt = prompts.plan_batch_prompt(4, 1)

    assert "docs/PROJECT.md" in prompt
    assert "Głównego briefu ani docs/BRIEF-SNAPSHOT.md nie" in prompt
    assert "brief-change" not in prompt


def test_brief_change_reaches_the_planner_prompt() -> None:
    prompt = prompts.plan_batch_prompt(
        4, 1, brief_change_path=".forge/brief-change.md")

    assert ".forge/brief-change.md" in prompt
    assert "przed zwykłym backlogiem" in prompt


def test_synchronisation_uses_the_strongest_model() -> None:
    cfg = Config()

    assert cfg.model_level("diff_bootstrap", "standard") == "max"
    assert cfg.model_level("diff_bootstrap") == cfg.model_level("bootstrap")
    assert cfg.role("diff_bootstrap")[0] == cfg.role("bootstrap")[0]


def test_large_brief_snapshot_is_not_documentation_debt(tmp_path: Path) -> None:
    brief.write_snapshot(str(tmp_path), "x" * 30_000)

    orchestrate._housekeeping(Config(), str(tmp_path))

    backlog = Path(tmp_path, "BACKLOG.md")
    assert not backlog.exists() or "Dług dokumentacji" not in backlog.read_text(
        encoding="utf-8")


def test_diff_of_a_rewritten_brief_stays_bounded() -> None:
    text = brief.diff("stary\n" * 5000, "nowy\n" * 5000, limit=500)

    assert len(text) < 700
    assert "obcięto" in text
