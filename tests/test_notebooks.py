from pathlib import Path
from unittest.mock import patch

from forge import notebooks, orchestrate
from forge.config import Config
from forge.state import State


TESTER_TEMPLATE = """# Prywatny notatnik testera

## Następna tura

## Ustalenia

## Próby i pułapki
"""

CODER_TEMPLATE = """# Prywatny notatnik kodera

## Następna tura

## Ustalenia

## Próby i pułapki
"""


def test_ensure_creates_exact_templates_without_overwriting(tmp_path: Path) -> None:
    directory = notebooks.ensure(str(tmp_path), ".forge", "task-123")
    tester = directory / "tester.md"
    coder = directory / "coder.md"

    assert tester.read_text(encoding="utf-8") == TESTER_TEMPLATE
    assert coder.read_text(encoding="utf-8") == CODER_TEMPLATE

    tester.write_text(TESTER_TEMPLATE + "\nważne ustalenie\n", encoding="utf-8")
    notebooks.ensure(str(tmp_path), ".forge", "task-123")
    assert tester.read_text(encoding="utf-8").endswith("ważne ustalenie\n")


def test_migration_preserves_records_once_and_clears_legacy_state(
        tmp_path: Path) -> None:
    state = State(tester_record="stary tester", coder_record="stary koder")

    assert notebooks.migrate_records(
        str(tmp_path), ".forge", "task-123", state)
    assert state.tester_record == state.coder_record == ""
    for role, record in (("tester", "stary tester"), ("coder", "stary koder")):
        text = (tmp_path / ".forge" / "notebooks" / "task-123"
                / f"{role}.md").read_text(encoding="utf-8")
        assert text.count("## Poprzedni rekord po migracji") == 1
        assert record in text

    state.tester_record = "stary tester"
    notebooks.migrate_records(str(tmp_path), ".forge", "task-123", state)
    text = (tmp_path / ".forge" / "notebooks" / "task-123"
            / "tester.md").read_text(encoding="utf-8")
    assert text.count("## Poprzedni rekord po migracji") == 1


def test_failure_moves_notebooks_into_artifact(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text(".forge/\n", encoding="utf-8")
    (tmp_path / "seed").write_text("seed", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "tag", "forge/task-123-start"], cwd=tmp_path, check=True)
    directory = notebooks.ensure(str(tmp_path), ".forge", "task-123")
    (directory / "tester.md").write_text("diagnostyka\n", encoding="utf-8")
    state = State(
        current_task={"id": "task-123"},
        task_start_tag="forge/task-123-start",
    )

    orchestrate._fail_task(
        Config(git_push=False), str(tmp_path), state, "boom")

    assert not directory.exists()
    archived = (
        tmp_path / ".forge" / "failed" / "task-123" / "notebooks")
    assert (archived / "tester.md").read_text(
        encoding="utf-8") == "diagnostyka\n"
    assert (archived / "coder.md").exists()


def test_retried_failure_never_replaces_archived_notes_with_templates(
        tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text(".forge/\n", encoding="utf-8")
    (tmp_path / "seed").write_text("seed", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "tag", "forge/task-123-start"], cwd=tmp_path, check=True)
    directory = notebooks.ensure(str(tmp_path), ".forge", "task-123")
    (directory / "tester.md").write_text(
        "ważna diagnostyka\n", encoding="utf-8")
    state = State(
        current_task={"id": "task-123"},
        task_start_tag="forge/task-123-start",
    )

    with patch(
            "forge.orchestrate._untracked",
            side_effect=RuntimeError("symulowane przerwanie")):
        try:
            orchestrate._fail_task(
                Config(git_push=False), str(tmp_path), state, "boom")
        except RuntimeError:
            pass

    # Restart widzi nadal aktywny stan i odtwarza brakujące template'y.
    orchestrate._fail_task(
        Config(git_push=False), str(tmp_path), state, "boom")

    archived = (
        tmp_path / ".forge" / "failed" / "task-123" / "notebooks")
    assert (archived / "tester.md").read_text(
        encoding="utf-8") == "ważna diagnostyka\n"


def test_housekeeping_removes_only_orphan_active_notebook_dirs(
        tmp_path: Path) -> None:
    active = notebooks.ensure(str(tmp_path), ".forge", "task-123")
    orphan = notebooks.ensure(str(tmp_path), ".forge", "task-999")
    State(current_task={"id": "task-123"}).save(
        str(tmp_path / ".forge" / "STATE.json"))

    orchestrate._housekeeping(Config(), str(tmp_path))

    assert active.exists()
    assert not orphan.exists()


def test_notebook_edits_do_not_count_as_task_changes(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text(".forge/\n", encoding="utf-8")
    (tmp_path / "seed").write_text("seed", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "tag", "forge/task-123-start"], cwd=tmp_path, check=True)

    directory = notebooks.ensure(str(tmp_path), ".forge", "task-123")
    (directory / "coder.md").write_text("nowa notatka\n", encoding="utf-8")

    assert orchestrate._changed(
        str(tmp_path), "forge/task-123-start") == []
    assert not orchestrate.has_changes(str(tmp_path))
