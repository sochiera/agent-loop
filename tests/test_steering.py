"""Product Owner: wyzwalacze, zakres zapisu, recenzja i osadzenie w pętli.

Bootstrap, brief-diff i notatka `steering.md` zostają tu też — to generyczna
maszyneria, z której PO korzysta razem z resztą ról nadzadaniowych.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from forge import brief, orchestrate, prompts
from forge.config import Config
from forge.state import State
from forge.task_pipeline import InvalidDecision

APPROVE = '{"verdict":"approve","notes":[]}'
REJECT = '{"verdict":"request_changes","notes":["za dużo naprzód"]}'

BACKLOG = """# Backlog

## US-001 — Pierwszy wynik  [nowa]

Jako użytkownik chcę zobaczyć wynik, żeby podjąć decyzję.

- Dlaczego teraz: PROJECT.md opisuje tę potrzebę.
- Sprawdzenie: uruchom demo i zobacz wynik.
- Poza zakresem: historia wyników.
"""


def _git(project: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=project, check=True, text=True, capture_output=True)


def _po_repo(tmp_path: Path) -> tuple[Path, State, Config]:
    """Projekt po bootstrapie, backlog już w formacie historyjek."""
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init", "-q")
    _git(project, "config", "user.email", "tests@example.test")
    _git(project, "config", "user.name", "Forge Tests")
    (project / ".gitignore").write_text(".forge/\n", encoding="utf-8")
    (project / "BACKLOG.md").write_text(BACKLOG, encoding="utf-8")
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
        bootstrapped=True, test_cmd="python3 -m pytest -q", backlog_migrated=True,
        brief_digest=brief.digest("Cel: gra.\n"))
    return project, state, Config(brief_path=str(brief_path), git_push=False,
                                  max_bootstrap_reviews=4)


def _decision(**overrides) -> str:
    import json
    data = {"summary": "aktualizacja", "stories_added": [], "stories_dropped": [],
             "changes": ["zmiana"], "replan": False, "goal_reached": False,
             "notebook": ""}
    data.update(overrides)
    return json.dumps(data)


def _run_po(project: Path, state: State, cfg: Config, *, trigger: str = "cadence",
           answers=(_decision(),), verdicts=(APPROVE,), write=None) -> list[str]:
    """Uruchom turę PO, rozdzielając wywołania autora od jego recenzentki."""
    seen: list[str] = []
    author = iter(answers)
    review = iter(verdicts)

    def agent(_name, prompt, _cfg, project_dir, _log, **_kwargs):
        seen.append(prompt)
        if "świeża recenzentka Product Ownera" in prompt:
            return next(review)
        if write:
            write(Path(project_dir))
        return next(author)

    with patch("forge.agents.run_agent", side_effect=agent):
        orchestrate.phase_product_owner(
            cfg, str(project), state, lambda phase: phase, trigger)
    return seen


# --- Wyzwalacze ---------------------------------------------------------------

def test_brief_change_wins_over_every_other_reason(tmp_path: Path) -> None:
    project, state, cfg = _po_repo(tmp_path)
    state.plan_batches, state.steering_due = 9, True
    Path(cfg.brief_path).write_text("Cel: gra.\nTryb sieciowy.\n", encoding="utf-8")

    assert orchestrate._po_trigger(cfg, str(project), state) == "brief"


def test_refill_fires_when_backlog_runs_low(tmp_path: Path) -> None:
    project, state, cfg = _po_repo(tmp_path)
    cfg.backlog_low_water = 2

    assert orchestrate._po_trigger(cfg, str(project), state) == "refill"


def test_cadence_fires_after_configured_batches_once_stocked(tmp_path: Path) -> None:
    project, state, cfg = _po_repo(tmp_path)
    cfg.backlog_low_water = 0
    state.plan_batches = cfg.steering_batches

    assert orchestrate._po_trigger(cfg, str(project), state) == "cadence"


def test_refill_guard_does_not_loop_before_any_batch_is_planned(
        tmp_path: Path) -> None:
    """Regresja: `po_refill_batch` musi blokować drugi `refill` nawet gdy
    `plan_batches` wciąż wynosi 0, bo żaden wsad jeszcze nie ruszył."""
    project, state, cfg = _po_repo(tmp_path)
    cfg.backlog_low_water = 2
    assert state.plan_batches == 0

    assert orchestrate._po_trigger(cfg, str(project), state) == "refill"
    state.po_refill_batch = state.plan_batches

    # Backlog nadal ubogi, ale ten sam wsad (0) już dostał swój refill.
    assert orchestrate._po_trigger(cfg, str(project), state) == ""


def test_active_task_or_queue_blocks_every_po_trigger(tmp_path: Path) -> None:
    project, state, cfg = _po_repo(tmp_path)
    cfg.backlog_low_water = 2
    state.task_queue = [{"id": "task-001"}]

    assert orchestrate._po_trigger(cfg, str(project), state) == ""


# --- Zakres zapisu i recenzja --------------------------------------------------

def test_po_reviewer_experiment_is_undone_without_losing_the_turn(
        tmp_path: Path) -> None:
    """Recenzentce wolno eksperymentować; zostaje z tego tylko werdykt."""
    project, state, cfg = _po_repo(tmp_path)

    def write(project_dir: Path) -> None:
        (project_dir / "docs" / "PROJECT.md").write_text(
            "# Projekt\n\nNowy cel.\n", encoding="utf-8")

    def agent(_name, prompt, _cfg, project_dir, _log, **_kwargs):
        if "świeża recenzentka Product Ownera" in prompt:
            (Path(project_dir) / "app.py").write_text("HACK = 1\n", encoding="utf-8")
            (Path(project_dir) / "probe.py").write_text(
                "print('sonda')\n", encoding="utf-8")
            return APPROVE
        write(Path(project_dir))
        return _decision()

    with patch("forge.agents.run_agent", side_effect=agent):
        orchestrate.phase_product_owner(
            cfg, str(project), state, lambda phase: phase, "cadence")

    assert Path(project, "app.py").read_text(encoding="utf-8") == "VALUE = 0\n"
    assert not Path(project, "probe.py").exists()
    assert not orchestrate.has_changes(str(project))
    # Praca autora PO przeżywa cofnięcie tury recenzentki.
    assert "Nowy cel" in Path(project, "docs", "PROJECT.md").read_text(
        encoding="utf-8")


def test_po_review_reverts_writes_outside_its_scope(tmp_path: Path) -> None:
    project, state, cfg = _po_repo(tmp_path)

    def write(project_dir: Path) -> None:
        (project_dir / "docs" / "PROJECT.md").write_text(
            "# Projekt\n\nNowy cel.\n", encoding="utf-8")
        (project_dir / "app.py").write_text("VALUE = 99\n", encoding="utf-8")
        (project_dir / "hack.py").write_text("print('nowy')\n", encoding="utf-8")

    _run_po(project, state, cfg, write=write)

    assert Path(project, "app.py").read_text(encoding="utf-8") == "VALUE = 0\n"
    assert not Path(project, "hack.py").exists()
    assert "Nowy cel" in Path(project, "docs", "PROJECT.md").read_text(
        encoding="utf-8")


def test_po_own_commit_does_not_smuggle_changes_past_the_scope_gate(
        tmp_path: Path) -> None:
    project, state, cfg = _po_repo(tmp_path)
    base = _git(project, "rev-parse", "HEAD").stdout.strip()

    def write(project_dir: Path) -> None:
        (project_dir / "docs" / "PROJECT.md").write_text(
            "# Projekt\n\nNowy cel.\n", encoding="utf-8")
        (project_dir / "app.py").write_text("VALUE = 99\n", encoding="utf-8")
        _git(project_dir, "add", "-A")
        _git(project_dir, "commit", "-qm", "przemycam kod")

    _run_po(project, state, cfg, write=write)

    assert Path(project, "app.py").read_text(encoding="utf-8") == "VALUE = 0\n"
    history = _git(project, "log", f"{base}..HEAD", "-p").stdout
    assert "VALUE = 99" not in history


def test_review_budget_is_configurable(tmp_path: Path) -> None:
    project, state, cfg = _po_repo(tmp_path)
    cfg.max_bootstrap_reviews = 2

    with pytest.raises(orchestrate.AgentError):
        _run_po(project, state, cfg,
                answers=(_decision(),) * 2, verdicts=(REJECT,) * 2)

    assert not orchestrate.has_changes(str(project))


def test_rejected_review_gets_the_notes_and_a_second_chance(
        tmp_path: Path) -> None:
    project, state, cfg = _po_repo(tmp_path)

    seen = _run_po(project, state, cfg,
                   answers=(_decision(), _decision()),
                   verdicts=(REJECT, APPROVE))

    author_prompts = [p for p in seen if "świeża recenzentka" not in p]
    assert len(author_prompts) == 2
    assert "za dużo naprzód" in author_prompts[1]


def test_goal_reached_goes_straight_to_final_verification(
        tmp_path: Path) -> None:
    project, state, cfg = _po_repo(tmp_path)
    state.task_queue = [{"id": "task-007", "title": "Stary plan"}]

    _run_po(project, state, cfg,
            answers=(_decision(replan=False, goal_reached=True),))

    assert state.goal_confirmed is True
    assert state.task_queue == []
    assert state.task_phase == "verify_goal"


def test_string_booleans_are_refused_instead_of_ending_the_project() -> None:
    with pytest.raises(InvalidDecision, match="goal_reached"):
        orchestrate._parse_steering_decision(
            '{"summary":"x","goal_reached":"false"}')
    with pytest.raises(InvalidDecision, match="replan"):
        orchestrate._parse_steering_decision(
            '{"summary":"x","replan":"false"}')
    with pytest.raises(InvalidDecision, match="changes"):
        orchestrate._parse_steering_decision(
            '{"summary":"x","changes":"jedna zmiana"}')
    with pytest.raises(InvalidDecision, match="summary"):
        orchestrate._parse_steering_decision('{"replan":true}')


def test_valid_verdict_keeps_its_defaults() -> None:
    data = orchestrate._parse_steering_decision('{"summary":" x "}')

    assert data == {"summary": "x", "replan": True, "goal_reached": False,
                    "changes": []}


def test_red_verification_forgets_the_confirmed_goal(tmp_path: Path) -> None:
    project, state, cfg = _po_repo(tmp_path)
    state.goal_confirmed = True
    state.verify_targets = ["smoke"]
    state.smoke_cmd = "false"

    with patch("forge.verify.collect_evidence",
               return_value={"smoke": {"rc": 1}}):
        assert orchestrate.phase_verify_goal(
            cfg, str(project), state, lambda phase: phase) is True

    assert state.goal_confirmed is False


def test_unreadable_brief_never_overwrites_the_snapshot(
        tmp_path: Path) -> None:
    project, state, cfg = _po_repo(tmp_path)
    Path(cfg.brief_path).unlink()

    _run_po(project, state, cfg, trigger="cadence")

    assert Path(project, brief.SNAPSHOT_PATH).read_text(
        encoding="utf-8") == "Cel: gra.\n"
    assert state.brief_digest == brief.digest("Cel: gra.\n")


# --- Osadzenie w pętli ---------------------------------------------------------

def test_iteration_reviews_direction_before_planning(tmp_path: Path) -> None:
    project, state, cfg = _po_repo(tmp_path)
    cfg.backlog_low_water = 0
    state.plan_batches = cfg.steering_batches

    with patch("forge.orchestrate.phase_product_owner") as po, \
         patch("forge.orchestrate.phase_verify_stories"), \
         patch("forge.orchestrate.phase_plan_batch") as plan:
        orchestrate.one_iteration(cfg, str(project), state)

    assert po.call_args.args[4] == "cadence"
    plan.assert_not_called()


def test_iteration_never_reviews_during_an_active_task(tmp_path: Path) -> None:
    project, state, cfg = _po_repo(tmp_path)
    cfg.backlog_low_water = 2
    state.current_task = {"id": "task-001", "title": "W toku",
                          "file": "task.md", "difficulty": "simple"}
    state.task_phase = "tester"

    with patch("forge.orchestrate.phase_product_owner") as po, \
         patch("forge.orchestrate.run_task", return_value=True):
        orchestrate.one_iteration(cfg, str(project), state)

    po.assert_not_called()


def test_empty_backlog_asks_for_direction_instead_of_ending_the_project(
        tmp_path: Path) -> None:
    project, state, cfg = _po_repo(tmp_path)
    cfg.backlog_low_water = 0

    with patch("forge.orchestrate.phase_plan_batch",
               return_value={"no_more_tasks": True}), \
         patch("forge.orchestrate.phase_verify_goal",
               return_value=False) as verify, \
         patch("forge.orchestrate.run_task", return_value=True):
        result = orchestrate.one_iteration(cfg, str(project), state)

    assert result is True
    verify.assert_not_called()
    assert state.steering_due is True


def test_confirmed_goal_lets_the_empty_backlog_finish_the_project(
        tmp_path: Path) -> None:
    project, state, cfg = _po_repo(tmp_path)
    cfg.backlog_low_water = 0
    state.goal_confirmed = True

    with patch("forge.orchestrate.phase_plan_batch",
               return_value={"no_more_tasks": True}), \
         patch("forge.orchestrate.phase_verify_goal",
               return_value=False) as verify, \
         patch("forge.orchestrate.run_task", return_value=True):
        orchestrate.one_iteration(cfg, str(project), state)

    verify.assert_called_once()


def test_planner_counts_idle_batches_and_forgets_them_after_real_work(
        tmp_path: Path) -> None:
    project, state, cfg = _po_repo(tmp_path)
    answers = iter(('{"no_more_tasks":true,"tasks":[]}',
                    '{"no_more_tasks":false,"tasks":[{"id":"task-001",'
                    '"title":"Coś","file":"BACKLOG.md"}]}'))

    with patch("forge.orchestrate._housekeeping"), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate.run_planner",
               side_effect=lambda *_a, **_k: next(answers)):
        orchestrate.phase_plan_batch(cfg, str(project), state, lambda p: p)
        assert state.empty_plans == 1
        orchestrate.phase_plan_batch(cfg, str(project), state, lambda p: p)

    assert state.empty_plans == 0


def test_planner_consumes_the_steering_note_exactly_once(
        tmp_path: Path) -> None:
    project, state, cfg = _po_repo(tmp_path)
    note = Path(project, ".forge", "steering.md")
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# Przegląd kierunku\n", encoding="utf-8")
    seen: list[str] = []

    def planner(prompt, *_args, **_kwargs):
        seen.append(prompt)
        return '{"no_more_tasks":true,"tasks":[]}'

    with patch("forge.orchestrate._housekeeping"), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate.run_planner", side_effect=planner):
        orchestrate.phase_plan_batch(cfg, str(project), state, lambda p: p)
        orchestrate.phase_plan_batch(cfg, str(project), state, lambda p: p)

    assert "steering.md" in seen[0]
    assert "steering.md" not in seen[1]
    assert not note.exists()


# --- Bootstrap ------------------------------------------------------------------

def test_bootstrap_plans_only_a_thin_demo_slice() -> None:
    prompt = prompts.bootstrap_prompt("brief")

    assert "NIE planuj całego produktu" in prompt
    assert "najcieńszy pionowy plasterek" in prompt
    assert "maksymalnie 3 wpisy" in prompt
    assert "docs/PROJECT.md" in prompt
    assert "świadomie odłożone" in prompt
    assert "Jawnie odróżnij wymagania, preferencje i pomysły" in prompt


def test_rejected_bootstrap_is_rebuilt_with_the_review_notes(
        tmp_path: Path) -> None:
    project, _seeded, cfg = _po_repo(tmp_path)
    state = State()
    seen: list[str] = []
    verdicts = iter((REJECT, APPROVE))

    def planner(prompt, *_args, **_kwargs):
        seen.append(prompt)
        Path(project, "docs", "PROJECT.md").write_text(
            "# Projekt\n", encoding="utf-8")
        return '{"kind":"app","test_cmd":"true","build_cmd":""}'

    with patch("forge.orchestrate.run_planner", side_effect=planner), \
         patch("forge.agents.run_agent",
               side_effect=lambda *_a, **_k: next(verdicts)), \
         patch("forge.orchestrate.build_then_test_result", return_value=(True, "")):
        orchestrate.phase_bootstrap(cfg, str(project), state, lambda p: p)

    assert len(seen) == 2
    assert "POPRAWKI PO RECENZJI ARCHITEKTURY" in seen[1]
    assert "za dużo naprzód" in seen[1]
    assert state.bootstrapped is True
    assert state.test_cmd == "true"


# --- Prompty i routing ------------------------------------------------------------

def test_planner_reads_project_context_instead_of_the_brief() -> None:
    prompt = prompts.plan_batch_prompt(4, 1)

    assert "docs/PROJECT.md" in prompt
    assert "Głównego briefu ani docs/BRIEF-SNAPSHOT.md nie" in prompt
    assert "Backlog jest celowo krótki" in prompt
    assert "nie ma z czego planować" in prompt
    assert "steering.md" not in prompt


def test_steering_note_reaches_the_planner_prompt() -> None:
    prompt = prompts.plan_batch_prompt(4, 1, steering_path=".forge/steering.md")

    assert ".forge/steering.md" in prompt
    assert "przed resztą backlogu" in prompt


def test_po_prompt_rejects_unknown_trigger() -> None:
    with pytest.raises(ValueError, match="powód uruchomienia Product Ownera"):
        prompts.product_owner_prompt(trigger="whatever")


def test_direction_roles_use_the_strongest_models() -> None:
    cfg = Config()

    assert cfg.model_level("product_owner", "standard") == "max"
    assert cfg.model_level("po_reviewer", "standard") == "strong"
    assert cfg.model_level("bootstrap_reviewer", "standard") == "max"
    assert cfg.role("bootstrap_reviewer")[0] == cfg.role("bootstrap")[0]


def test_large_brief_snapshot_is_not_documentation_debt(tmp_path: Path) -> None:
    brief.write_snapshot(str(tmp_path), "x" * 30_000)

    orchestrate._housekeeping(Config(), str(tmp_path))

    backlog = Path(tmp_path, "BACKLOG.md")
    assert not backlog.exists() or "Dług dokumentacji" not in backlog.read_text(
        encoding="utf-8")


def test_oversized_diff_falls_back_to_the_whole_brief() -> None:
    """Obcięty diff gubiłby wymagania na zawsze: snapshot zapisuje cały brief."""
    new = "nowy\n" * 200 + "OSTATNIE WYMAGANIE\n"

    text = brief.diff("stary\n" * 200, new, limit=100)

    assert "OSTATNIE WYMAGANIE" in text
    assert "PEŁNA bieżąca treść briefu" in text
    assert "obcięto" not in text


def test_brief_too_large_to_sync_stops_instead_of_guessing() -> None:
    with pytest.raises(brief.TooLargeToSync, match="Podziel brief"):
        brief.diff("stary\n" * 5000, "nowy\n" * 5000, limit=500, full_limit=1000)


def test_unsyncable_brief_stops_the_run_without_touching_the_snapshot(
        tmp_path: Path) -> None:
    project, state, cfg = _po_repo(tmp_path)
    Path(cfg.brief_path).write_text("nowy\n" * 5000, encoding="utf-8")
    cfg_limits = {"limit": 10, "full_limit": 100}

    with patch("forge.brief.DIFF_LIMIT", cfg_limits["limit"]), \
         patch("forge.brief.FULL_LIMIT", cfg_limits["full_limit"]), \
         pytest.raises(orchestrate.AgentError, match="zbyt duży"):
        _run_po(project, state, cfg, trigger="brief")

    assert state.brief_digest == brief.digest("Cel: gra.\n")
    assert Path(project, brief.SNAPSHOT_PATH).read_text(
        encoding="utf-8") == "Cel: gra.\n"
