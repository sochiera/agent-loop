from pathlib import Path
from unittest.mock import patch

import pytest

from forge import notebooks, orchestrate
from forge.config import Config
from forge.state import State


TESTER_TEMPLATE = """# Prywatny notatnik testera

## Notatki z rund
"""

CODER_TEMPLATE = """# Prywatny notatnik kodera

## Notatki z rund
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


def test_untouched_notebook_reads_as_nothing_to_show(tmp_path: Path) -> None:
    assert notebooks.read(str(tmp_path), ".forge", "task-123", "coder") == ""
    notebooks.ensure(str(tmp_path), ".forge", "task-123")

    assert notebooks.read(str(tmp_path), ".forge", "task-123", "coder") == ""
    assert notebooks.read(str(tmp_path), ".forge", "task-123", "tester") == ""


def test_appended_entries_keep_order_and_survive_repeated_turn(
        tmp_path: Path) -> None:
    def write(rnd: int, text: object) -> str:
        return notebooks.append_entry(
            str(tmp_path), ".forge", "task-123", "coder", rnd, text)

    assert write(1, "most w bridge_client.gd:158") == "most w bridge_client.gd:158"
    assert write(2, "odrzucony  reset\nw advance_turn") == "odrzucony reset w advance_turn"
    # Powtórzona po padzie tura nie dubluje identycznej linii.
    assert write(2, "odrzucony reset w advance_turn") == ""
    assert write(2, "") == ""
    assert write(2, None) == ""

    text = notebooks.read(str(tmp_path), ".forge", "task-123", "coder")
    assert text.splitlines()[-2:] == [
        "- r1: most w bridge_client.gd:158",
        "- r2: odrzucony reset w advance_turn",
    ]
    assert notebooks.read(str(tmp_path), ".forge", "task-123", "tester") == ""


def test_failed_atomic_replace_preserves_previous_notebook(
        tmp_path: Path) -> None:
    def write(rnd: int, text: str) -> str:
        return notebooks.append_entry(
            str(tmp_path), ".forge", "task-123", "coder", rnd, text)

    write(1, "kosztowne ustalenie o środowisku")
    path = tmp_path / ".forge" / "notebooks" / "task-123" / "coder.md"
    original = path.read_text(encoding="utf-8")

    with patch("forge.notebooks.os.replace", side_effect=OSError("boom")):
        with pytest.raises(OSError, match="boom"):
            write(2, "nowe ustalenie")

    assert path.read_text(encoding="utf-8") == original
    assert not path.with_name(".coder.md.tmp").exists()


def test_notebook_from_older_template_is_still_treated_as_empty(
        tmp_path: Path) -> None:
    # Zadanie wznowione po zmianie szablonu ma na dysku starą, pustą postać.
    directory = notebooks.ensure(str(tmp_path), ".forge", "task-123")
    for role, name in (("tester", "testera"), ("coder", "kodera")):
        (directory / f"{role}.md").write_text(
            f"# Prywatny notatnik {name}\n\n## Następna tura\n\n"
            "## Ustalenia\n\n## Próby i pułapki\n",
            encoding="utf-8")

        assert notebooks.read(
            str(tmp_path), ".forge", "task-123", role) == ""


def test_whitespace_around_template_does_not_make_it_look_like_content(
        tmp_path: Path) -> None:
    # Inaczej same nagłówki wchodziłyby do każdej tury sesji jako „notatki”.
    directory = notebooks.ensure(str(tmp_path), ".forge", "task-123")
    (directory / "tester.md").write_text(
        "\n" + TESTER_TEMPLATE + "\n\n", encoding="utf-8")

    assert notebooks.read(str(tmp_path), ".forge", "task-123", "tester") == ""


def test_unreadable_notebook_costs_a_hint_not_the_turn(tmp_path: Path) -> None:
    directory = notebooks.ensure(str(tmp_path), ".forge", "task-123")
    (directory / "coder.md").write_bytes(b"- r1: \xff\xfe uszkodzony bajt")

    assert notebooks.read(str(tmp_path), ".forge", "task-123", "coder") == ""


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
