import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from forge import brief, orchestrate, prompts
from forge.config import Config
from forge.state import State
from forge.agents import AgentError


BACKLOG = """# Backlog

## US-001 — Pierwszy wynik  [nowa]

Jako użytkownik chcę zobaczyć wynik, żeby podjąć decyzję.

- Dlaczego teraz: PROJECT.md opisuje tę potrzebę.
- Sprawdzenie: uruchom demo i zobacz wynik.
- Poza zakresem: historia wyników.
"""


def _project(tmp_path: Path) -> tuple[Path, Config, State]:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text(".forge/\n", encoding="utf-8")
    (tmp_path / "BACKLOG.md").write_text(BACKLOG, encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "PROJECT.md").write_text("# Projekt\n\nCel.\n", encoding="utf-8")
    brief = tmp_path / "brief.md"
    brief.write_text("Cel.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    state = State(bootstrapped=True, backlog_migrated=True)
    cfg = Config(brief_path=str(brief), git_push=False, max_bootstrap_reviews=2)
    return tmp_path, cfg, state


def _decision(summary="aktualizacja"):
    return ('{"summary":"' + summary + '","stories_added":[],'
            '"stories_dropped":[],"changes":["zmiana"],'
            '"replan":false,"goal_reached":false,"notebook":"obserwacja"}')


def test_product_owner_prompt_has_all_trigger_variants_and_rules() -> None:
    for trigger in ("refill", "brief", "cadence"):
        prompt = prompts.product_owner_prompt(
            trigger=trigger, notebook_path=".forge/notebooks/product-owner.md")
        assert "{{" not in prompt
        assert "Pionowa i pokazywalna" in prompt
        assert "Sprawdzenie:" in prompt
        assert "US-NNN" in prompt
        assert "stories_dropped" in prompt


def test_product_owner_repair_happens_before_reviewer(tmp_path: Path) -> None:
    project, cfg, state = _project(tmp_path)
    state.brief_digest = brief.digest(Path(cfg.brief_path).read_text(encoding="utf-8"))
    calls: list[str] = []
    valid = BACKLOG + "\n## US-002 — Drugi wynik  [nowa]\n\n"
    valid += "Jako użytkownik chcę drugi wynik, żeby działać.\n\n"
    valid += "- Dlaczego teraz: nowy dowód.\n- Sprawdzenie: otwórz ekran.\n"
    valid += "- Poza zakresem: eksport.\n"

    def agent(name, prompt, *_args, **_kwargs):
        calls.append(name + ":" + prompt[:30])
        if "świeża recenzentka Product Ownera" in prompt:
            return '{"verdict":"approve","notes":[]}'
        if len([item for item in calls if item.startswith("claude:")]) == 1:
            (project / "BACKLOG.md").write_text(
                BACKLOG.replace("- Sprawdzenie: uruchom demo i zobacz wynik.\n", ""),
                encoding="utf-8")
        else:
            (project / "BACKLOG.md").write_text(valid, encoding="utf-8")
        return _decision()

    with patch("forge.agents.run_agent", side_effect=agent):
        orchestrate.phase_product_owner(cfg, str(project), state, lambda phase: phase)
    assert "US-002" in (project / "BACKLOG.md").read_text(encoding="utf-8")
    assert len([item for item in calls if "świeża" in item]) == 1
    assert (project / ".forge" / "notebooks" / "product-owner.md").exists()


def test_structural_failure_never_calls_reviewer_and_reverts(tmp_path: Path) -> None:
    project, cfg, state = _project(tmp_path)
    cfg.max_bootstrap_reviews = 1
    original = (project / "BACKLOG.md").read_text(encoding="utf-8")
    calls: list[str] = []

    def agent(name, prompt, *_args, **_kwargs):
        calls.append(prompt)
        (project / "BACKLOG.md").write_text(
            original.replace("- Sprawdzenie: uruchom demo i zobacz wynik.\n", ""),
            encoding="utf-8")
        return _decision()

    with patch("forge.agents.run_agent", side_effect=agent):
        with pytest.raises(AgentError, match="budżet"):
            orchestrate.phase_product_owner(
                cfg, str(project), state, lambda phase: phase)
    assert not any("świeża recenzentka Product Ownera" in prompt for prompt in calls)
    assert (project / "BACKLOG.md").read_text(encoding="utf-8") == original


def test_illegal_status_is_healed_without_costing_a_single_extra_turn(
        tmp_path: Path) -> None:
    """Regresja awarii z 10.08: `[gotowe]` zakleszczało PO na cztery tury.

    Status spoza kontraktu nie mógł ani zostać (naruszenie), ani zostać
    poprawiony (drugie naruszenie). Dziś przepisuje go Forge, więc tura PO w
    ogóle nie widzi problemu i nie ma czego korygować.
    """
    project, cfg, state = _project(tmp_path)
    state.brief_digest = brief.digest(Path(cfg.brief_path).read_text(encoding="utf-8"))
    (project / "BACKLOG.md").write_text(
        BACKLOG.replace("[nowa]", "[gotowe]"), encoding="utf-8")
    orchestrate.ledger.append(
        str(project), "task-678 r1 koder→green pliki=[BACKLOG.md]:")
    po_prompts: list[str] = []

    def agent(_name, prompt, *_args, **_kwargs):
        if "świeża recenzentka Product Ownera" in prompt:
            return '{"verdict":"approve","notes":[]}'
        po_prompts.append(prompt)
        return _decision()

    with patch("forge.agents.run_agent", side_effect=agent):
        orchestrate.phase_product_owner(cfg, str(project), state, lambda phase: phase)

    assert len(po_prompts) == 1
    # Ślad, po którym PO może sam rozpoznać źródło skażenia: turę kodera, która
    # ruszyła BACKLOG.md. Bez dziennika ta informacja nie istnieje dla niego
    # nigdzie — plik pokazuje skutek, nie sprawcę.
    assert "DZIENNIK PROCESU" in po_prompts[0]
    assert "task-678 r1 koder→green" in po_prompts[0]
    assert "[do weryfikacji]" in (project / "BACKLOG.md").read_text(encoding="utf-8")
    assert "statusy przywrócone przez Forge" in (
        project / ".forge" / "ledger.md").read_text(encoding="utf-8")


def test_po_status_edit_is_overwritten_instead_of_rejected(tmp_path: Path) -> None:
    project, cfg, state = _project(tmp_path)
    state.brief_digest = brief.digest(Path(cfg.brief_path).read_text(encoding="utf-8"))
    (project / "BACKLOG.md").write_text(
        BACKLOG.replace("[nowa]", "[w toku]"), encoding="utf-8")
    calls: list[str] = []

    def agent(_name, prompt, *_args, **_kwargs):
        if "świeża recenzentka Product Ownera" in prompt:
            return '{"verdict":"approve","notes":[]}'
        calls.append(prompt)
        (project / "BACKLOG.md").write_text(
            BACKLOG.replace("[nowa]", "[zrobiona]"), encoding="utf-8")
        return _decision()

    with patch("forge.agents.run_agent", side_effect=agent):
        orchestrate.phase_product_owner(cfg, str(project), state, lambda phase: phase)

    assert len(calls) == 1
    assert "[w toku]" in (project / "BACKLOG.md").read_text(encoding="utf-8")


def test_repeated_violations_stop_before_exhausting_the_budget(tmp_path: Path) -> None:
    project, cfg, state = _project(tmp_path)
    cfg.max_bootstrap_reviews = 4
    state.brief_digest = brief.digest(Path(cfg.brief_path).read_text(encoding="utf-8"))
    original = (project / "BACKLOG.md").read_text(encoding="utf-8")
    calls: list[str] = []

    def agent(_name, prompt, *_args, **_kwargs):
        calls.append(prompt)
        (project / "BACKLOG.md").write_text(
            original.replace("- Sprawdzenie: uruchom demo i zobacz wynik.\n", ""),
            encoding="utf-8")
        return _decision()

    with patch("forge.agents.run_agent", side_effect=agent):
        with pytest.raises(AgentError, match="te same naruszenia drugi raz"):
            orchestrate.phase_product_owner(
                cfg, str(project), state, lambda phase: phase)

    # Dwie tury zamiast czterech: druga identyczna lista naruszeń dowodzi, że
    # kolejne nic nie wniosą, a to najdroższy model w całym przebiegu.
    assert len(calls) == 2
    assert (project / "BACKLOG.md").read_text(encoding="utf-8") == original


def test_stories_reopened_returns_story_to_queue_with_a_reason(tmp_path: Path) -> None:
    project, cfg, state = _project(tmp_path)
    state.brief_digest = brief.digest(Path(cfg.brief_path).read_text(encoding="utf-8"))
    (project / "BACKLOG.md").write_text(
        BACKLOG.replace("[nowa]", "[zrobiona]"), encoding="utf-8")

    def agent(_name, prompt, *_args, **_kwargs):
        if "świeża recenzentka Product Ownera" in prompt:
            return '{"verdict":"approve","notes":[]}'
        return ('{"summary":"wynik nie działa","stories_added":[],'
                '"stories_dropped":[],'
                '"stories_reopened":[{"id":"US-001","reason":"wynik gubi się '
                'po restarcie"}],'
                '"changes":[],"replan":false,"goal_reached":false,'
                '"notebook":""}')

    with patch("forge.agents.run_agent", side_effect=agent):
        orchestrate.phase_product_owner(cfg, str(project), state, lambda phase: phase)

    assert "## US-001 — Pierwszy wynik  [nowa]" in (
        project / "BACKLOG.md").read_text(encoding="utf-8")
    assert "wynik gubi się po restarcie" in (
        project / ".forge" / "ledger.md").read_text(encoding="utf-8")
    # Powód musi dojechać do planisty, inaczej zaplanuje historyjkę od zera.
    steering = orchestrate._steering_path(cfg, str(project))
    assert "wynik gubi się po restarcie" in steering.read_text(encoding="utf-8")


def test_hallucinated_reopen_never_becomes_a_fact(tmp_path: Path) -> None:
    """Wznowienie nieistniejącego ID nie ma prawa dojść do dziennika i planisty."""
    project, cfg, state = _project(tmp_path)
    cfg.max_bootstrap_reviews = 2
    state.brief_digest = brief.digest(Path(cfg.brief_path).read_text(encoding="utf-8"))
    reviewed: list[str] = []

    def agent(_name, prompt, *_args, **_kwargs):
        if "świeża recenzentka Product Ownera" in prompt:
            reviewed.append(prompt)
            return '{"verdict":"approve","notes":[]}'
        return ('{"summary":"x","stories_added":[],"stories_dropped":[],'
                '"stories_reopened":[{"id":"US-999","reason":"nie działa"}],'
                '"changes":[],"replan":false,"goal_reached":false,"notebook":""}')

    with patch("forge.agents.run_agent", side_effect=agent):
        with pytest.raises(AgentError):
            orchestrate.phase_product_owner(
                cfg, str(project), state, lambda phase: phase)

    ledger_text = (project / ".forge" / "ledger.md").read_text(encoding="utf-8")
    assert "nieznane ID" in ledger_text
    assert "wraca do kolejki" not in ledger_text
    assert not reviewed
    assert not orchestrate._steering_path(cfg, str(project)).exists()


def test_recurring_violation_after_a_clean_turn_is_not_treated_as_a_streak(
        tmp_path: Path) -> None:
    """Odsiew ma ucinać brak postępu, nie nawrót po uwadze recenzentki.

    Kolejność: struktura zła → dobra, ale recenzentka odsyła → znów ta sama
    zła → dobra i akceptacja. Poprawna tura w środku przerywa serię, więc
    przebieg musi domknąć się normalnie zamiast stanąć w rundzie trzeciej.
    """
    project, cfg, state = _project(tmp_path)
    cfg.max_bootstrap_reviews = 4
    state.brief_digest = brief.digest(Path(cfg.brief_path).read_text(encoding="utf-8"))
    original = (project / "BACKLOG.md").read_text(encoding="utf-8")
    broken = original.replace("- Sprawdzenie: uruchom demo i zobacz wynik.\n", "")
    po_calls: list[str] = []
    verdicts = iter(('{"verdict":"request_changes","notes":["za ogólne"]}',
                     '{"verdict":"approve","notes":[]}'))

    def agent(_name, prompt, *_args, **_kwargs):
        if "świeża recenzentka Product Ownera" in prompt:
            return next(verdicts)
        po_calls.append(prompt)
        (project / "BACKLOG.md").write_text(
            broken if len(po_calls) % 2 else original, encoding="utf-8")
        return _decision()

    with patch("forge.agents.run_agent", side_effect=agent):
        orchestrate.phase_product_owner(cfg, str(project), state, lambda phase: phase)

    assert len(po_calls) == 4
    assert (project / "BACKLOG.md").read_text(encoding="utf-8") == original


def test_po_reviewer_sees_declarations_that_are_not_on_disk_yet() -> None:
    prompt = prompts.po_review_prompt({
        "summary": "przegląd",
        "stories_dropped": [{"id": "US-005", "reason": "potrzeba zniknęła"}],
        "stories_reopened": [{"id": "US-003", "reason": "gubi stan po restarcie"}],
    })
    assert "{{" not in prompt
    assert "US-005: potrzeba zniknęła" in prompt
    assert "US-003: gubi stan po restarcie" in prompt
    # Bez tego zdania recenzentka szukałaby tych zmian w BACKLOG.md, gdzie
    # jeszcze ich nie ma, i uznała deklarację za niewykonaną.
    assert "po twojej" in prompt and "akceptacji" in prompt
    assert "(brak)" in prompts.po_review_prompt({"summary": "x"})


def test_stories_reopened_without_reason_is_a_contract_violation() -> None:
    with pytest.raises(Exception, match="stories_reopened wymaga"):
        orchestrate._parse_product_owner_decision(
            '{"summary":"x","stories_added":[],"stories_dropped":[],'
            '"stories_reopened":[{"id":"US-001"}],"changes":[],'
            '"replan":false,"goal_reached":false,"notebook":""}')


def test_po_triggers_have_priority_and_refill_guard(tmp_path: Path) -> None:
    project, cfg, state = _project(tmp_path)
    state.brief_digest = brief.digest(Path(cfg.brief_path).read_text(encoding="utf-8"))
    cfg.backlog_low_water = 2
    state.plan_batches = 3
    state.steered_at_batch = 0
    assert orchestrate._po_trigger(cfg, str(project), state) == "refill"
    state.po_refill_batch = state.plan_batches
    assert orchestrate._po_trigger(cfg, str(project), state) == "cadence"
    state.task_queue = [{"id": "task-001"}]
    assert orchestrate._po_trigger(cfg, str(project), state) == ""
