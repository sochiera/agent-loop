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

    with patch("forge.orchestrate.run_agent", side_effect=agent):
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

    with patch("forge.orchestrate.run_agent", side_effect=agent):
        with pytest.raises(AgentError, match="budżet"):
            orchestrate.phase_product_owner(
                cfg, str(project), state, lambda phase: phase)
    assert not any("świeża recenzentka Product Ownera" in prompt for prompt in calls)
    assert (project / "BACKLOG.md").read_text(encoding="utf-8") == original


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
