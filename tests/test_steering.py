"""Przegląd kierunku (diff-bootstrap): kadencja, zakres, recenzja i koniec projektu."""
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
STEERED = ('{"summary":"kolejny plasterek","changes":["sieć"],'
           '"replan":true,"goal_reached":false}')


def _git(project: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=project, check=True, text=True, capture_output=True)


def _steered_repo(tmp_path: Path) -> tuple[Path, State, Config]:
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


def _change_brief(cfg: Config, text: str = "Cel: gra.\nTryb sieciowy.\n") -> None:
    Path(cfg.brief_path).write_text(text, encoding="utf-8")


def _write_in_scope(project_dir: Path) -> None:
    (project_dir / "BACKLOG.md").write_text(
        "- [ ] stary wpis\n- [ ] tryb sieciowy\n", encoding="utf-8")
    (project_dir / "docs" / "PROJECT.md").write_text(
        "# Projekt\n\nNowy cel.\n", encoding="utf-8")


def _run_steering(project: Path, state: State, cfg: Config, *,
                  trigger: str = "cadence", answers=(STEERED,),
                  verdicts=(APPROVE,), write=_write_in_scope) -> list[str]:
    """Uruchom przegląd, rozdzielając wywołania przeglądu od jego recenzji."""
    seen: list[str] = []
    steering = iter(answers)
    review = iter(verdicts)

    def agent(_name, prompt, _cfg, project_dir, _log, **_kwargs):
        seen.append(prompt)
        if "recenzent przeglądu kierunku" in prompt:
            return next(review)
        if write:
            write(Path(project_dir))
        return next(steering)

    with patch("forge.orchestrate.run_agent", side_effect=agent):
        orchestrate.phase_diff_bootstrap(
            cfg, str(project), state, lambda phase: phase, trigger)
    return seen


# --- Wyzwalacze --------------------------------------------------------------

def test_settled_project_does_not_ask_for_a_review(tmp_path: Path) -> None:
    project, state, cfg = _steered_repo(tmp_path)

    assert orchestrate._steering_trigger(cfg, str(project), state) == ""


def test_cadence_triggers_review_after_configured_batches(
        tmp_path: Path) -> None:
    project, state, cfg = _steered_repo(tmp_path)
    state.plan_batches = 2

    assert orchestrate._steering_trigger(cfg, str(project), state) == ""

    state.plan_batches = 3

    assert orchestrate._steering_trigger(cfg, str(project), state) == "cadence"


def test_cadence_counts_from_the_last_review(tmp_path: Path) -> None:
    project, state, cfg = _steered_repo(tmp_path)
    state.plan_batches, state.steered_at_batch = 5, 3

    assert orchestrate._steering_trigger(cfg, str(project), state) == ""

    state.plan_batches = 6

    assert orchestrate._steering_trigger(cfg, str(project), state) == "cadence"


def test_changed_brief_wins_over_every_other_reason(tmp_path: Path) -> None:
    project, state, cfg = _steered_repo(tmp_path)
    state.plan_batches, state.steering_due = 9, True
    _change_brief(cfg)

    assert orchestrate._steering_trigger(cfg, str(project), state) == "brief"


def test_exhausted_backlog_asks_for_a_review(tmp_path: Path) -> None:
    project, state, cfg = _steered_repo(tmp_path)
    state.steering_due = True

    assert orchestrate._steering_trigger(cfg, str(project), state) == "backlog"


def test_missing_brief_file_never_triggers_a_brief_review(
        tmp_path: Path) -> None:
    project, state, cfg = _steered_repo(tmp_path)
    Path(cfg.brief_path).unlink()

    assert orchestrate._steering_trigger(cfg, str(project), state) == ""


def test_project_bootstrapped_before_the_mechanism_syncs_once(
        tmp_path: Path) -> None:
    """Brak snapshotu i skrótu = jednorazowa migracja, nie pełny bootstrap."""
    project, state, cfg = _steered_repo(tmp_path)
    Path(project, brief.SNAPSHOT_PATH).unlink()
    state.brief_digest = ""

    assert orchestrate._steering_trigger(cfg, str(project), state) == "brief"


# --- Przebieg przeglądu ------------------------------------------------------

def test_review_records_its_own_cadence_and_requeues_planning(
        tmp_path: Path) -> None:
    project, state, cfg = _steered_repo(tmp_path)
    state.plan_batches = 3
    state.task_queue = [{"id": "task-007", "title": "Stary plan"}]
    state.task_phase = "verify_goal"
    _change_brief(cfg)

    _run_steering(project, state, cfg, trigger="brief")

    assert state.brief_digest == brief.digest("Cel: gra.\nTryb sieciowy.\n")
    assert Path(project, brief.SNAPSHOT_PATH).read_text(
        encoding="utf-8") == "Cel: gra.\nTryb sieciowy.\n"
    assert state.steered_at_batch == 3
    assert state.steered_at_sha
    assert state.task_queue == []
    assert state.task_phase == ""
    assert state.goal_confirmed is False
    assert not orchestrate.has_changes(str(project))
    note = Path(project, ".forge", "steering.md").read_text(encoding="utf-8")
    assert "kolejny plasterek" in note
    assert "task-007: Stary plan" in note


def test_reached_goal_goes_straight_to_final_verification(
        tmp_path: Path) -> None:
    """Nawet przy replan=false stara kolejka nie może przeżyć osiągniętego celu."""
    project, state, cfg = _steered_repo(tmp_path)
    state.task_queue = [{"id": "task-007", "title": "Stary plan"}]

    _run_steering(project, state, cfg,
                  answers=('{"summary":"gotowe","replan":false,'
                           '"goal_reached":true}',))

    assert state.goal_confirmed is True
    assert state.task_queue == []
    assert state.task_phase == "verify_goal"

    with patch("forge.orchestrate.phase_plan_batch") as plan, \
         patch("forge.orchestrate.phase_verify_goal",
               return_value=False) as verify:
        orchestrate.one_iteration(cfg, str(project), state)

    verify.assert_called_once()
    plan.assert_not_called()


def test_red_verification_forgets_the_confirmed_goal(tmp_path: Path) -> None:
    project, state, cfg = _steered_repo(tmp_path)
    state.goal_confirmed = True
    state.verify_targets = ["smoke"]
    state.smoke_cmd = "false"

    with patch("forge.verify.collect_evidence",
               return_value={"smoke": {"rc": 1}}):
        assert orchestrate.phase_verify_goal(
            cfg, str(project), state, lambda phase: phase) is True

    assert state.goal_confirmed is False


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


def test_valid_verdict_keeps_its_defaults(tmp_path: Path) -> None:
    data = orchestrate._parse_steering_decision('{"summary":" x "}')

    assert data == {"summary": "x", "replan": True, "goal_reached": False,
                    "changes": []}


def test_typed_verdict_error_gets_one_cheap_correction(
        tmp_path: Path) -> None:
    project, state, cfg = _steered_repo(tmp_path)

    _run_steering(project, state, cfg,
                  answers=('{"summary":"x","goal_reached":"true"}', STEERED))

    assert state.goal_confirmed is False
    assert state.task_phase == ""


def test_cadence_review_does_not_resend_the_unchanged_brief(
        tmp_path: Path) -> None:
    project, state, cfg = _steered_repo(tmp_path)

    seen = _run_steering(project, state, cfg, trigger="cadence")

    assert "brief bez zmian od ostatniego przeglądu" in seen[0]
    assert "Cel: gra." not in seen[0]


def test_review_sees_what_was_built_since_the_last_one(
        tmp_path: Path) -> None:
    project, state, cfg = _steered_repo(tmp_path)
    state.steered_at_sha = _git(project, "rev-parse", "HEAD").stdout.strip()
    (project / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(project, "commit", "-qam", "feat: nowa wartość")

    seen = _run_steering(project, state, cfg)

    assert "feat: nowa wartość" in seen[0]
    assert "seed" not in seen[0]


def test_rejected_review_gets_the_notes_and_a_second_chance(
        tmp_path: Path) -> None:
    project, state, cfg = _steered_repo(tmp_path)

    seen = _run_steering(project, state, cfg,
                         answers=(STEERED, STEERED),
                         verdicts=(REJECT, APPROVE))

    steering_prompts = [
        p for p in seen if "recenzent przeglądu kierunku" not in p]
    assert len(steering_prompts) == 2
    assert "POPRAWKI PO RECENZJI" not in steering_prompts[0]
    assert "za dużo naprzód" in steering_prompts[1]


def test_four_rejections_stop_the_run_and_leave_the_tree_untouched(
        tmp_path: Path) -> None:
    project, state, cfg = _steered_repo(tmp_path)
    _change_brief(cfg)

    with pytest.raises(orchestrate.AgentError, match="decyzja użytkownika"):
        _run_steering(project, state, cfg, trigger="brief",
                      answers=(STEERED,) * 4, verdicts=(REJECT,) * 4)

    assert Path(project, "BACKLOG.md").read_text(
        encoding="utf-8") == "- [ ] stary wpis\n"
    assert Path(project, "docs", "PROJECT.md").read_text(
        encoding="utf-8") == "# Projekt\n\nStary cel.\n"
    assert state.brief_digest == brief.digest("Cel: gra.\n")
    assert orchestrate._steering_trigger(cfg, str(project), state) == "brief"


def test_review_budget_is_configurable(tmp_path: Path) -> None:
    project, state, cfg = _steered_repo(tmp_path)
    cfg.max_bootstrap_reviews = 2

    with pytest.raises(orchestrate.AgentError):
        _run_steering(project, state, cfg,
                      answers=(STEERED,) * 2, verdicts=(REJECT,) * 2)


def test_review_writing_files_is_not_accepted_as_read_only(
        tmp_path: Path) -> None:
    project, state, cfg = _steered_repo(tmp_path)

    def agent(_name, prompt, _cfg, project_dir, _log, **_kwargs):
        if "recenzent przeglądu kierunku" in prompt:
            (Path(project_dir) / "app.py").write_text(
                "HACK = 1\n", encoding="utf-8")
            return APPROVE
        _write_in_scope(Path(project_dir))
        return STEERED

    with patch("forge.orchestrate.run_agent", side_effect=agent), \
         pytest.raises(orchestrate.AgentError, match="zmienił drzewo"):
        orchestrate.phase_diff_bootstrap(
            cfg, str(project), state, lambda phase: phase, "cadence")


def test_review_reverts_writes_outside_its_scope(tmp_path: Path) -> None:
    project, state, cfg = _steered_repo(tmp_path)

    def write(project_dir: Path) -> None:
        (project_dir / "BACKLOG.md").write_text(
            "- [ ] tryb sieciowy\n", encoding="utf-8")
        (project_dir / "app.py").write_text("VALUE = 99\n", encoding="utf-8")
        (project_dir / "hack.py").write_text("print('nowy')\n", encoding="utf-8")

    _run_steering(project, state, cfg, write=write)

    assert Path(project, "app.py").read_text(encoding="utf-8") == "VALUE = 0\n"
    assert not Path(project, "hack.py").exists()
    assert Path(project, "BACKLOG.md").read_text(
        encoding="utf-8") == "- [ ] tryb sieciowy\n"


def test_own_commit_does_not_smuggle_changes_past_the_scope_gate(
        tmp_path: Path) -> None:
    """Cofanie musi kotwiczyć się na SHA sprzed fazy, nie na ruchomym HEAD."""
    project, state, cfg = _steered_repo(tmp_path)
    base = _git(project, "rev-parse", "HEAD").stdout.strip()

    def write(project_dir: Path) -> None:
        _write_in_scope(project_dir)
        (project_dir / "app.py").write_text("VALUE = 99\n", encoding="utf-8")
        _git(project_dir, "add", "-A")
        _git(project_dir, "commit", "-qm", "przemycam kod")

    _run_steering(project, state, cfg, write=write)

    assert Path(project, "app.py").read_text(encoding="utf-8") == "VALUE = 0\n"
    history = _git(project, "log", f"{base}..HEAD", "-p").stdout
    assert "VALUE = 99" not in history
    assert "tryb sieciowy" in Path(project, "BACKLOG.md").read_text(
        encoding="utf-8")


def test_review_prompt_diffs_against_the_state_before_the_phase(
        tmp_path: Path) -> None:
    project, state, cfg = _steered_repo(tmp_path)
    base = _git(project, "rev-parse", "HEAD").stdout.strip()

    seen = _run_steering(project, state, cfg)

    review = [p for p in seen if "recenzent przeglądu kierunku" in p][0]
    assert base in review


def test_failure_after_a_reviewer_write_still_leaves_a_clean_tree(
        tmp_path: Path) -> None:
    project, state, cfg = _steered_repo(tmp_path)

    def agent(_name, prompt, _cfg, project_dir, _log, **_kwargs):
        if "recenzent przeglądu kierunku" in prompt:
            (Path(project_dir) / "app.py").write_text(
                "HACK = 1\n", encoding="utf-8")
            return APPROVE
        _write_in_scope(Path(project_dir))
        return STEERED

    with patch("forge.orchestrate.run_agent", side_effect=agent), \
         pytest.raises(orchestrate.AgentError):
        orchestrate.phase_diff_bootstrap(
            cfg, str(project), state, lambda phase: phase, "cadence")

    assert Path(project, "app.py").read_text(encoding="utf-8") == "VALUE = 0\n"
    assert not orchestrate.has_changes(str(project))


def test_reviewer_commit_is_caught_even_with_an_untouched_tree(
        tmp_path: Path) -> None:
    project, state, cfg = _steered_repo(tmp_path)

    def agent(_name, prompt, _cfg, project_dir, _log, **_kwargs):
        if "recenzent przeglądu kierunku" in prompt:
            _git(Path(project_dir), "add", "-A")
            _git(Path(project_dir), "commit", "-qm", "recenzent commituje")
            return APPROVE
        _write_in_scope(Path(project_dir))
        return STEERED

    with patch("forge.orchestrate.run_agent", side_effect=agent), \
         pytest.raises(orchestrate.AgentError, match="zmienił historię"):
        orchestrate.phase_diff_bootstrap(
            cfg, str(project), state, lambda phase: phase, "cadence")

    assert not orchestrate.has_changes(str(project))
    assert Path(project, "BACKLOG.md").read_text(
        encoding="utf-8") == "- [ ] stary wpis\n"


def test_unreadable_brief_never_overwrites_the_snapshot(
        tmp_path: Path) -> None:
    project, state, cfg = _steered_repo(tmp_path)
    Path(cfg.brief_path).unlink()

    _run_steering(project, state, cfg, trigger="cadence")

    assert Path(project, brief.SNAPSHOT_PATH).read_text(
        encoding="utf-8") == "Cel: gra.\n"
    assert state.brief_digest == brief.digest("Cel: gra.\n")


def test_unparsable_verdict_keeps_the_previous_brief_as_reference(
        tmp_path: Path) -> None:
    project, state, cfg = _steered_repo(tmp_path)
    _change_brief(cfg)

    with pytest.raises(InvalidDecision):
        _run_steering(project, state, cfg, trigger="brief",
                      answers=("bez JSON-a", "nadal bez JSON-a"))

    assert state.brief_digest == brief.digest("Cel: gra.\n")
    assert Path(project, brief.SNAPSHOT_PATH).read_text(
        encoding="utf-8") == "Cel: gra.\n"
    # Brudne drzewo wywróciłoby następną iterację na bramce czystości.
    assert not orchestrate.has_changes(str(project))


# --- Osadzenie w pętli -------------------------------------------------------

def test_iteration_reviews_direction_before_planning(tmp_path: Path) -> None:
    project, state, cfg = _steered_repo(tmp_path)
    state.plan_batches = 3

    with patch("forge.orchestrate.phase_diff_bootstrap") as steering, \
         patch("forge.orchestrate.phase_plan_batch") as plan:
        orchestrate.one_iteration(cfg, str(project), state)

    assert steering.call_args.args[-1] == "cadence"
    plan.assert_not_called()


def test_iteration_never_reviews_during_an_active_task(tmp_path: Path) -> None:
    project, state, cfg = _steered_repo(tmp_path)
    state.plan_batches = 9
    state.current_task = {"id": "task-001", "title": "W toku",
                          "file": "task.md", "difficulty": "simple"}
    state.task_phase = "tester"

    with patch("forge.orchestrate.phase_diff_bootstrap") as steering, \
         patch("forge.orchestrate.run_task", return_value=True):
        orchestrate.one_iteration(cfg, str(project), state)

    steering.assert_not_called()


def _plan_iteration(project: Path, state: State, cfg: Config, no_more: bool):
    with patch("forge.orchestrate.phase_plan_batch",
               return_value={"no_more_tasks": no_more}) as plan, \
         patch("forge.orchestrate.phase_verify_goal",
               return_value=False) as verify, \
         patch("forge.orchestrate.run_task", return_value=True):
        result = orchestrate.one_iteration(cfg, str(project), state)
    return result, plan, verify


def test_empty_backlog_asks_for_direction_instead_of_ending_the_project(
        tmp_path: Path) -> None:
    project, state, cfg = _steered_repo(tmp_path)

    result, _plan, verify = _plan_iteration(project, state, cfg, no_more=True)

    assert result is True
    verify.assert_not_called()
    assert state.steering_due is True


def test_confirmed_goal_lets_the_empty_backlog_finish_the_project(
        tmp_path: Path) -> None:
    project, state, cfg = _steered_repo(tmp_path)
    state.goal_confirmed = True

    _result, _plan, verify = _plan_iteration(project, state, cfg, no_more=True)

    verify.assert_called_once()


def test_two_idle_reviews_in_a_row_still_reach_final_verification(
        tmp_path: Path) -> None:
    """Bezpiecznik: para planista↔przegląd nie może kręcić się w nieskończoność."""
    project, state, cfg = _steered_repo(tmp_path)
    state.empty_plans = 2

    _result, _plan, verify = _plan_iteration(project, state, cfg, no_more=True)

    verify.assert_called_once()


def test_planner_counts_idle_batches_and_forgets_them_after_real_work(
        tmp_path: Path) -> None:
    project, state, cfg = _steered_repo(tmp_path)
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
    project, state, cfg = _steered_repo(tmp_path)
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


# --- Bootstrap ---------------------------------------------------------------

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
    project, _seeded, cfg = _steered_repo(tmp_path)
    state = State()
    seen: list[str] = []
    verdicts = iter((REJECT, APPROVE))

    def planner(prompt, *_args, **_kwargs):
        seen.append(prompt)
        Path(project, "docs", "PROJECT.md").write_text(
            "# Projekt\n", encoding="utf-8")
        return '{"kind":"app","test_cmd":"true","build_cmd":""}'

    with patch("forge.orchestrate.run_planner", side_effect=planner), \
         patch("forge.orchestrate.run_agent",
               side_effect=lambda *_a, **_k: next(verdicts)), \
         patch("forge.orchestrate.build_then_test", return_value=True):
        orchestrate.phase_bootstrap(cfg, str(project), state, lambda p: p)

    assert len(seen) == 2
    assert "POPRAWKI PO RECENZJI ARCHITEKTURY" in seen[1]
    assert "za dużo naprzód" in seen[1]
    assert state.bootstrapped is True
    assert state.test_cmd == "true"


# --- Prompty i routing -------------------------------------------------------

def test_steering_prompt_states_its_narrow_write_scope() -> None:
    prompt = prompts.diff_bootstrap_prompt(
        trigger="cadence", batches=3, queued_tasks=["task-007: Stary plan"])

    assert "WYŁĄCZNIE BACKLOG.md oraz docs/PROJECT.md" in prompt
    assert "task-007: Stary plan" in prompt
    assert "Nie dotykaj kodu" in prompt
    assert "nie cofa ukończonego kodu" in prompt
    assert "Nie kasuj po cichu" in prompt
    assert "najcieńszy sensowny plasterek" in prompt
    assert "nie planuj całego produktu naprzód" in prompt
    assert '"goal_reached":false' in prompt
    assert "3 wsady planisty" in prompt
    assert "POPRAWKI PO RECENZJI" not in prompt


def test_steering_prompt_carries_the_reason_it_was_started() -> None:
    backlog = prompts.diff_bootstrap_prompt(trigger="backlog")
    change = prompts.diff_bootstrap_prompt("-stare\n+nowe", trigger="brief")

    assert "wyczerpany backlog" in backlog
    assert "goal_reached" in backlog
    assert "zmiana briefu" in change
    assert "-stare" in change and "+nowe" in change


def test_first_review_prompt_explains_the_missing_snapshot() -> None:
    prompt = prompts.diff_bootstrap_prompt(
        "+cały brief", trigger="brief", initial=True)

    assert "PIERWSZA synchronizacja" in prompt
    assert "traktuj zrealizowaną już część projektu jako fakt" in prompt


def test_unknown_trigger_is_refused_instead_of_silently_rendered() -> None:
    with pytest.raises(ValueError, match="powód przeglądu"):
        prompts.diff_bootstrap_prompt(trigger="whatever")


def test_direction_review_prompt_judges_direction_not_style() -> None:
    prompt = prompts.diff_bootstrap_review_prompt(
        "HEAD", summary="dodałem sieć", goal_reached=True)

    assert "read-only" in prompt
    assert "dodałem sieć" in prompt
    assert "Deklaracja osiągnięcia celu: tak" in prompt
    assert "najcieńszym sensownym przyrostem" in prompt
    assert "zniknął po cichu" in prompt
    assert "Nie oceniaj jakości kodu" in prompt
    assert '"verdict":"request_changes"' in prompt


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


def test_direction_roles_use_the_strongest_model() -> None:
    cfg = Config()

    assert cfg.model_level("diff_bootstrap", "standard") == "max"
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
    project, state, cfg = _steered_repo(tmp_path)
    _change_brief(cfg, "nowy\n" * 5000)
    cfg_limits = {"limit": 10, "full_limit": 100}

    with patch("forge.brief.DIFF_LIMIT", cfg_limits["limit"]), \
         patch("forge.brief.FULL_LIMIT", cfg_limits["full_limit"]), \
         pytest.raises(orchestrate.AgentError, match="zbyt duży"):
        _run_steering(project, state, cfg, trigger="brief")

    assert state.brief_digest == brief.digest("Cel: gra.\n")
    assert Path(project, brief.SNAPSHOT_PATH).read_text(
        encoding="utf-8") == "Cel: gra.\n"
