"""The README Sprint loop diagram stays generated from the controller."""

import shutil
from pathlib import Path

import pytest

from forge import diagram
from forge.orchestrator import ITERATION_PHASES
from forge.sprint import SPRINT_SCHEDULE

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"


def test_readme_diagram_is_current() -> None:
    assert diagram.check_readme(str(README)), (
        "README.md Sprint loop diagram is stale; run `python3 -m forge.diagram --update README.md`"
    )


def test_diagram_is_derived_from_controller_constants() -> None:
    rendered = diagram.render_diagram()
    assert f"{len(SPRINT_SCHEDULE)} slots" in rendered
    assert "/".join(kind[0].upper() for kind in SPRINT_SCHEDULE).replace("/", " / ") in rendered
    for phase in ITERATION_PHASES:
        assert phase.replace("-", "_") in rendered or phase in rendered


def test_diagram_changes_when_the_schedule_changes() -> None:
    original = diagram.render_diagram()
    monkey = pytest.MonkeyPatch()
    monkey.setattr(diagram, "SPRINT_SCHEDULE", ("feature", "cleanup"))
    try:
        updated = diagram.render_diagram()
    finally:
        monkey.undo()
    assert original != updated
    assert "2 slots (F / C)" in updated


def test_render_rejects_phase_label_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diagram, "ITERATION_PHASES", ("planning", "unknown-phase"))
    with pytest.raises(ValueError):
        diagram.render_diagram()


def test_check_fails_on_a_stale_copy(tmp_path: Path) -> None:
    stale = tmp_path / "README.md"
    shutil.copy(README, stale)
    assert diagram.check_readme(str(stale))
    stale.write_text(stale.read_text().replace("fresh Product Owner", "stale label"))
    assert not diagram.check_readme(str(stale))


def test_update_rewrites_a_stale_copy(tmp_path: Path) -> None:
    stale = tmp_path / "README.md"
    shutil.copy(README, stale)
    stale.write_text(stale.read_text().replace("fresh Product Owner", "stale label"))
    assert not diagram.check_readme(str(stale))
    diagram.update_readme(str(stale))
    assert diagram.check_readme(str(stale))
    def section(text: str) -> str:
        start = text.index(diagram.DIAGRAM_BEGIN)
        end = text.index(diagram.DIAGRAM_END) + len(diagram.DIAGRAM_END)
        return text[start:end]
    assert section(stale.read_text()) == section(README.read_text())
