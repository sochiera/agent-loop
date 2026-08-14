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


def test_first_backlog_after_bootstrap_fires_the_start_trigger(
        tmp_path: Path) -> None:
    """Bootstrap nie pisze historyjek, więc pierwszy backlog zakłada PO."""
    project, state, cfg = _po_repo(tmp_path)
    Path(project, "BACKLOG.md").unlink()

    assert orchestrate._po_trigger(cfg, str(project), state) == "start"
    # Ten sam wyzwalacz nie może paść drugi raz, nawet gdy PO nic nie dopisał.
    state.po_refill_batch = state.plan_batches
    assert orchestrate._po_trigger(cfg, str(project), state) == ""


def test_start_prompt_asks_for_the_whole_mvp_map() -> None:
    """Ten test strzegł wcześniej dokładnej odwrotności tego, czego chcemy.

    Po przepisaniu promptu startu asercja „maksymalnie 3 historyjki" nadal
    przechodziła — trafiała w akapit, który cytuje starą regułę jako opis
    nieudanego przebiegu. Test zielony, a chroniona reguła odwrotna: dlatego
    sprawdzamy teraz zapis instrukcji, a nie fragment uzasadnienia.
    """
    prompt = prompts.product_owner_prompt(trigger="start")

    assert "Pierwszy backlog projektu" in prompt
    assert "BACKLOG.md\nmoże nie istnieć" in prompt
    assert "KSZTAŁT CAŁEGO MVP" in prompt
    assert "Każda sekcja mapy pokrycia dostaje dokładnie jedną historyjkę" in prompt
    assert "ŻADNEJ GŁĘBI" in prompt
    # Pominięcie sekcji jest legalne WYŁĄCZNIE jako deklaracja — inaczej
    # kontrakt startu i reguła blokująca recenzentki byłyby sprzeczne.
    assert "zgłoś ją w `sections_skipped`" in prompt
    assert "NIE pomijaj jej milczeniem ani zdaniem w `summary`" in prompt


def test_start_turn_creates_the_backlog_from_scratch(tmp_path: Path) -> None:
    """Po bootstrapie pliku nie ma wcale — tura `start` musi go założyć."""
    project, state, cfg = _po_repo(tmp_path)
    Path(project, "BACKLOG.md").unlink()
    _git(project, "commit", "-qam", "backlog zakłada Product Owner")

    assert orchestrate._po_trigger(cfg, str(project), state) == "start"
    _run_po(project, state, cfg, trigger="start",
            answers=(_decision(stories_added=["US-001"]),),
            write=lambda dir_: Path(dir_, "BACKLOG.md").write_text(
                BACKLOG, encoding="utf-8"))

    # Założony backlog musi zostać scommitowany: brudne drzewo zatrzymałoby
    # następną fazę na niezmienniku czystości.
    assert Path(project, "BACKLOG.md").is_file()
    assert not orchestrate.has_changes(str(project))
    assert orchestrate._po_trigger(cfg, str(project), state) == ""


def test_bootstrap_handoff_reaches_the_product_owner_once(tmp_path: Path) -> None:
    project, state, cfg = _po_repo(tmp_path)
    orchestrate._write_po_handoff(
        cfg, str(project), ["lista GPU nie pokazuje dopasowania do zestawu"])

    seen = _run_po(project, state, cfg, trigger="start")

    assert "UWAGI RECENZENTA ARCHITEKTURY" in seen[0]
    assert "lista GPU nie pokazuje dopasowania do zestawu" in seen[0]
    # Rozliczone uwagi żyją dalej w backlogu; druga tura płaciłaby za ich
    # ponowne przeczytanie.
    assert not Path(project, ".forge", "po-handoff.md").exists()


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


def test_iteration_proves_stories_before_letting_the_product_owner_plan(
        tmp_path: Path) -> None:
    """Weryfikacja jest własną fazą i wyprzedza KAŻDĄ turę Product Ownera.

    Dopóki wisiała pod triggerem `cadence`, refill ją zagładzał: odpalał z
    niskiego stanu kolejki, a ten wracał po każdym domknięciu. W mierzonym
    biegu weryfikacja nie ruszyła ani razu przez dwadzieścia wsadów, więc
    `zrobiona` nie powstało nigdy, a rosnąca zaległość wyglądała dla PO jak
    zakaz poszerzania zakresu.
    """
    project, state, cfg = _po_repo(tmp_path)
    Path(project, "BACKLOG.md").write_text(
        BACKLOG.replace("[nowa]", "[do weryfikacji]"), encoding="utf-8")
    _git(project, "commit", "-qam", "historyjka czeka na dowód")
    cfg.backlog_low_water = 9

    order: list[str] = []
    with patch("forge.orchestrate.phase_verify_stories",
               side_effect=lambda *a, **k: order.append("verify") or True) as verify, \
         patch("forge.orchestrate.phase_product_owner",
               side_effect=lambda *a, **k: order.append("po")) as po:
        orchestrate.one_iteration(cfg, str(project), state)

    verify.assert_called_once()
    po.assert_not_called()
    assert order == ["verify"]


def test_verification_never_judges_stories_that_were_only_planned(
        tmp_path: Path) -> None:
    """Weryfikacji podlega wyłącznie `do weryfikacji`, nigdy `nowa`.

    Dawna gałąź „pierwszej inwentaryzacji" brała do sześciu dowolnych
    nieporzuconych historyjek, więc razem z jedną domkniętą trafiał do
    weryfikatorki komplet świeżo zaplanowanych. Było to nieszkodliwe dopóty,
    dopóki weryfikacja praktycznie nie ruszała; po naprawie kadencji odpala już
    po pierwszym commicie historyjki i jej jedynym możliwym skutkiem byłaby
    szkoda: `potwierdzona` zamyka niezbudowaną historyjkę jako `zrobiona`,
    a `niepotwierdzona` przestawia ją na `w toku`, gdzie utknie bez zadania,
    bo planista bierze pracę z `nowa`.
    """
    project, state, cfg = _po_repo(tmp_path)
    Path(project, "BACKLOG.md").write_text(
        BACKLOG.replace("[nowa]", "[do weryfikacji]")
        + "\n## US-002 — Dopiero zaplanowana  [nowa]\n\n"
        "Jako użytkownik chcę czegoś.\n\n"
        "- Dlaczego teraz: dowód.\n- Sprawdzenie: uruchom demo.\n",
        encoding="utf-8")
    _git(project, "commit", "-qam", "jedna czeka na dowód, druga dopiero stoi")
    seen: list[str] = []

    def agent(_name, prompt, *_args, **_kwargs):
        seen.append(prompt)
        return ('{"stories":[{"id":"US-001","status":"potwierdzona",'
                '"evidence":"demo działa"}],"verdict":"complete","notes":[]}')

    with patch("forge.agents.run_agent", side_effect=agent), \
         patch("forge.orchestrate.verify.collect_evidence", return_value={}):
        assert orchestrate.phase_verify_stories(
            cfg, str(project), state, lambda phase: phase)

    assert "US-001" in seen[0]
    assert "US-002" not in seen[0]
    backlog_text = Path(project, "BACKLOG.md").read_text(encoding="utf-8")
    assert "## US-001 — Pierwszy wynik  [zrobiona]" in backlog_text
    # Historyjka wyłącznie zaplanowana zostaje tam, gdzie planista ją znajdzie.
    assert "## US-002 — Dopiero zaplanowana  [nowa]" in backlog_text


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

def _reject(note: str) -> str:
    import json
    return json.dumps({"verdict": "request_changes", "notes": [note]})


def _run_bootstrap(project: Path, cfg: Config, state: State,
                   verdicts, suites=None) -> tuple[list[str], list[str]]:
    """Uruchom bootstrap, rozdzielając prompty budowniczego od recenzenta.

    ``suites`` podmienia wynik sprawdzianu w kolejnych podejściach; domyślnie
    każde jest zielone."""
    built: list[str] = []
    reviewed: list[str] = []
    answers = iter(verdicts)
    checks = iter(suites or ())

    def planner(prompt, *_args, **_kwargs):
        built.append(prompt)
        Path(project, "docs", "PROJECT.md").write_text(
            "# Projekt\n", encoding="utf-8")
        return '{"kind":"app","test_cmd":"true","build_cmd":""}'

    def reviewer(_name, prompt, *_args, **_kwargs):
        reviewed.append(prompt)
        return next(answers)

    def suite(*_args, **_kwargs):
        return next(checks, (True, ""))

    with patch("forge.orchestrate.run_planner", side_effect=planner), \
         patch("forge.agents.run_agent", side_effect=reviewer), \
         patch("forge.orchestrate.build_then_test_result", side_effect=suite):
        orchestrate.phase_bootstrap(cfg, str(project), state, lambda p: p)
    return built, reviewed


def test_bootstrap_builds_a_skeleton_and_leaves_stories_to_the_product_owner() -> None:
    prompt = prompts.bootstrap_prompt("brief")

    assert "CHODZĄCY SZKIELET" in prompt
    assert "Nie budujesz\nproduktu i nie planujesz go" in prompt
    # Historyjki są własnością Product Ownera; bootstrap, który je pisze,
    # zamienia plan na późniejszą pracę w zobowiązanie oceniane od razu.
    assert "BACKLOG.md NIE należy do ciebie" in prompt
    assert "Product Owner" in prompt
    assert "Jedno źródło\nprawdy dla każdej reguły" in prompt
    assert "docs/PROJECT.md" in prompt
    assert "świadomie odłożone" in prompt
    assert "Jawnie odróżnij wymagania, preferencje i pomysły" in prompt


def test_bootstrap_review_may_reject_only_structural_defects() -> None:
    prompt = prompts.bootstrap_architecture_review_prompt(
        "game.md", "make smoke", round_number=1, budget=4)

    assert "runda 1 z 4" in prompt
    assert "POZA ZAKRESEM recenzji" in prompt
    assert "pisze je Product Owner po tobie, nie bootstrap" in prompt
    assert "naprawiło jedno zadanie TDD" in prompt
    assert "suggestions" in prompt
    assert "(to pierwsza runda)" in prompt


def test_review_notes_accumulate_across_rounds(tmp_path: Path) -> None:
    project, _seeded, cfg = _po_repo(tmp_path)
    state = State()

    built, reviewed = _run_bootstrap(
        project, cfg, state,
        (_reject("test mierzy inną implementację"),
         _reject("PROJECT.md nie niesie celu"), APPROVE))

    # Recenzent widzi, w której rundzie jest i co zgłoszono wcześniej — bez tego
    # każda tura zaczyna od zera i seria nigdy nie zbiega się do akceptacji.
    assert "runda 2 z 4" in reviewed[1]
    assert "test mierzy inną implementację" in reviewed[1]
    assert "PROJECT.md nie niesie celu" in reviewed[2]
    # Budowniczy dostaje uwagi skumulowane, więc nie cofnie starszej poprawki.
    assert "POPRAWKI PO ODRZUCONYM SZKIELECIE" in built[1]
    assert "test mierzy inną implementację" in built[2]
    assert "PROJECT.md nie niesie celu" in built[2]
    assert state.bootstrapped is True
    assert state.test_cmd == "true"


MAKE_FAIL = (False, "make: *** No rule to make target 'build'.  Stop.")

# Wyjście sprawdzianu jest w większości wspólną ramką, więc dwa przebiegi tej
# samej suity są do siebie podobne nawet przy wyraźnym postępie autora.
PYTEST_THREE_RED = (False, """============================= test session starts ==========
collected 12 items

tests/test_parts.py::test_socket_match FAILED                            [ 25%]
tests/test_parts.py::test_psu_headroom FAILED                            [ 50%]
tests/test_parts.py::test_price_total FAILED                             [ 75%]

=================================== FAILURES ===================================
E       AssertionError: assert 'AM4' == 'AM5'
E       AssertionError: assert 450 >= 550
E       AssertionError: assert 4210 == 4200
========================= 3 failed, 1 passed in 0.42s ==========================""")
PYTEST_ONE_RED = (False, """============================= test session starts ==========
collected 12 items

tests/test_parts.py::test_socket_match PASSED                            [ 25%]
tests/test_parts.py::test_psu_headroom PASSED                            [ 50%]
tests/test_parts.py::test_price_total FAILED                             [ 75%]

=================================== FAILURES ===================================
E       AssertionError: assert 4210 == 4200
========================= 1 failed, 3 passed in 0.39s ==========================""")


def test_red_suite_buys_a_repair_round_instead_of_stopping_the_run(
        tmp_path: Path) -> None:
    """Zadeklarowana komenda, która nie istnieje, to pomyłka autora.

    Zatrzymanie przebiegu kosztowałoby tu cały bootstrap i decyzję człowieka za
    literówkę w jednym poleceniu, choć autor umie ją poprawić sam.
    """
    project, _seeded, cfg = _po_repo(tmp_path)
    state = State()

    built, reviewed = _run_bootstrap(
        project, cfg, state, (APPROVE,), suites=(MAKE_FAIL,))

    assert len(built) == 2
    # Autor musi zobaczyć wyjście sprawdzianu i obie drogi naprawy.
    assert "No rule to make target 'build'" in built[1]
    assert "zadeklaruj komendy, które w nim naprawdę działają" in built[1]
    # Recenzent architektury dostaje gwarancję zielonej suity, więc zaległy wpis
    # o czerwonej kazałby mu sprawdzać rzecz już sprawdzoną przez Forge.
    assert "No rule to make target" not in reviewed[0]
    assert state.bootstrapped is True


def test_green_attempt_drops_the_settled_check_note(tmp_path: Path) -> None:
    """Uwagi sprawdzianu odwołuje dowód, a nie kolejna opinia.

    Niesiona dalej po zielonym podejściu kazałaby autorowi „naprawiać" komendę,
    którą Forge przed chwilą uruchomił — szablon poprawek każe przecież
    rozliczyć każdą uwagę, której nikt nie odwołał.
    """
    project, _seeded, cfg = _po_repo(tmp_path)
    state = State()

    built, _reviewed = _run_bootstrap(
        project, cfg, state,
        (_reject("kierunek do przepisania"), APPROVE), suites=(MAKE_FAIL,))

    assert len(built) == 3
    assert "No rule to make target" in built[1]
    assert "No rule to make target" not in built[2]
    # Uwaga recenzenta nie jest odwoływalna dowodowo, więc zostaje.
    assert "kierunek do przepisania" in built[2]


def test_progress_between_two_red_suites_is_not_a_stall(tmp_path: Path) -> None:
    """Mniej czerwonych testów to postęp autora, a nie powtórzony sprawdzian.

    Próg podobieństwa `_notes_repeat` kalibrowaliśmy dla jednozdaniowych uwag
    recenzenta; pod nim te dwa logi wychodzą identyczne (1,00) i przebieg
    stawałby mimo naprawionych dwóch z trzech awarii.
    """
    project, _seeded, cfg = _po_repo(tmp_path)
    state = State()

    built, _reviewed = _run_bootstrap(
        project, cfg, state, (APPROVE,),
        suites=(PYTEST_THREE_RED, PYTEST_ONE_RED))

    assert len(built) == 3
    assert state.bootstrapped is True


def test_check_signature_ignores_only_what_changes_between_runs() -> None:
    same = orchestrate._check_signature("1 failed in 0.42s (pid 8811)")
    assert same == orchestrate._check_signature("1 failed in 0.39s (pid 24)")
    assert same != orchestrate._check_signature("1 error in 0.42s (pid 8811)")
    assert (orchestrate._check_signature("No rule to make target 'build'")
            != orchestrate._check_signature("No rule to make target 'smoke'"))


def test_suite_red_to_the_last_round_never_passes_as_a_skeleton(
        tmp_path: Path) -> None:
    """Zielona suita jest warunkiem wejścia do dalszej pętli, nie opinią."""
    project, _seeded, cfg = _po_repo(tmp_path)
    cfg.max_bootstrap_reviews = 2
    state = State()

    with pytest.raises(orchestrate.AgentError, match="obala deklarację autora"):
        _run_bootstrap(project, cfg, state, (APPROVE,), suites=(
            MAKE_FAIL,
            (False, "ModuleNotFoundError: No module named 'pytest'")))

    assert state.bootstrapped is False


def test_same_failing_check_twice_stops_the_run(tmp_path: Path) -> None:
    """Ten sam wynik po pełnej rundzie naprawczej to realne zakleszczenie."""
    project, _seeded, cfg = _po_repo(tmp_path)
    state = State()

    with pytest.raises(orchestrate.AgentError, match="dwa razy z rzędu"):
        _run_bootstrap(project, cfg, state, (APPROVE,),
                       suites=(MAKE_FAIL, MAKE_FAIL))

    assert state.bootstrapped is False


def test_exhausted_review_budget_hands_the_notes_to_the_product_owner(
        tmp_path: Path) -> None:
    """Seria RÓŻNYCH uwag to recenzja bez dna, a nie zepsuty szkielet."""
    project, _seeded, cfg = _po_repo(tmp_path)
    state = State()

    _built, reviewed = _run_bootstrap(
        project, cfg, state,
        (_reject("brakuje ekranu podsumowania"),
         _reject("nieznane identyfikatory nie są walidowane"),
         _reject("lista GPU nie pokazuje dopasowania"),
         _reject("koszt zestawu nie jest zaokrąglany")))

    assert len(reviewed) == 4
    assert state.bootstrapped is True
    handoff = Path(project, ".forge", "po-handoff.md").read_text(encoding="utf-8")
    assert "brakuje ekranu podsumowania" in handoff
    assert "koszt zestawu nie jest zaokrąglany" in handoff


def test_suggestions_verdict_accepts_the_skeleton_at_once(tmp_path: Path) -> None:
    project, _seeded, cfg = _po_repo(tmp_path)
    state = State()

    built, _reviewed = _run_bootstrap(
        project, cfg, state,
        ('{"verdict":"suggestions","notes":["demo warto rozszerzyć o zapis"]}',))

    assert len(built) == 1
    assert state.bootstrapped is True
    handoff = Path(project, ".forge", "po-handoff.md").read_text(encoding="utf-8")
    assert "demo warto rozszerzyć o zapis" in handoff


def test_note_returning_despite_fixes_stops_the_run(tmp_path: Path) -> None:
    """Wracająca uwaga to jedyny dowód, że bootstrap jej nie umie rozliczyć."""
    project, _seeded, cfg = _po_repo(tmp_path)
    state = State()

    with pytest.raises(orchestrate.AgentError, match="dwa razy wrócił do uwagi"):
        _run_bootstrap(
            project, cfg, state,
            (_reject("test mierzy inną implementację niż aplikacja"),
             _reject("test wciąż mierzy inną implementację niż aplikacja"),
             _reject("test nadal mierzy inną implementację niż aplikacja")))

    assert state.bootstrapped is False
    assert not Path(project, ".forge", "po-handoff.md").exists()


def test_single_repeat_still_gets_another_round(tmp_path: Path) -> None:
    """Jedno powtórzenie bywa parafrazą; stop kosztowałby cały bootstrap."""
    project, _seeded, cfg = _po_repo(tmp_path)
    state = State()

    built, _reviewed = _run_bootstrap(
        project, cfg, state,
        (_reject("test mierzy inną implementację niż aplikacja"),
         _reject("test wciąż mierzy inną implementację niż aplikacja"),
         APPROVE))

    assert len(built) == 3
    assert state.bootstrapped is True


def test_note_returning_at_the_budget_limit_stops_the_run(tmp_path: Path) -> None:
    project, _seeded, cfg = _po_repo(tmp_path)
    cfg.max_bootstrap_reviews = 2
    state = State()

    with pytest.raises(orchestrate.AgentError, match="uwaga wróciła mimo poprawek"):
        _run_bootstrap(
            project, cfg, state,
            (_reject("test mierzy inną implementację niż aplikacja"),
             _reject("test wciąż mierzy inną implementację niż aplikacja")))

    assert state.bootstrapped is False


def test_repeat_detector_sees_through_polish_inflection() -> None:
    assert orchestrate._notes_repeat(
        ["lista GPU nie odrzuca modeli wymagających złącza zasilacza"],
        ["lista GPU wciąż nie odrzuca modelu wymagającego złączy zasilacza"])
    assert not orchestrate._notes_repeat(
        ["lista GPU nie odrzuca modeli wymagających złącza zasilacza"],
        ["docs/PROJECT.md nie zawiera kryterium sukcesu"])


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
