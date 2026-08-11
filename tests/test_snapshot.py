import os
import subprocess
import time
from pathlib import Path

from forge import snapshot


def _package(root: Path, marker: str = "start") -> Path:
    package = root / "forge"
    (package / "prompts" / "templates").mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "orchestrate.py").write_text(
        f"VERSION = {marker!r}\n", encoding="utf-8")
    (package / "prompts" / "templates" / "planner.md").write_text(
        f"plan {marker}\n", encoding="utf-8")
    (package / "__pycache__").mkdir()
    (package / "__pycache__" / "orchestrate.pyc").write_text("x", encoding="utf-8")
    return package


def _environ(root: Path) -> dict[str, str]:
    return {"XDG_CACHE_HOME": str(root / "cache")}


def test_snapshot_copies_the_package_with_its_templates(tmp_path: Path) -> None:
    package = _package(tmp_path)
    result = snapshot.create(package, _environ(tmp_path))
    copied = result.path / "forge"
    assert (copied / "orchestrate.py").read_text(encoding="utf-8") == "VERSION = 'start'\n"
    assert (copied / "prompts" / "templates" / "planner.md").is_file()


def test_compiled_leftovers_are_not_copied(tmp_path: Path) -> None:
    package = _package(tmp_path)
    result = snapshot.create(package, _environ(tmp_path))
    assert not (result.path / "forge" / "__pycache__").exists()


def test_editing_the_source_does_not_touch_a_taken_snapshot(tmp_path: Path) -> None:
    """Sedno awarii B: commit pod pracującą pętlą nie może zmienić jej kodu."""
    package = _package(tmp_path)
    taken = snapshot.create(package, _environ(tmp_path))
    (package / "prompts" / "templates" / "planner.md").write_text(
        "plan {{NOWY_SLOT}}\n", encoding="utf-8")
    (package / "orchestrate.py").write_text("VERSION = 'po zmianie'\n", encoding="utf-8")
    frozen = taken.path / "forge"
    assert (frozen / "orchestrate.py").read_text(encoding="utf-8") == "VERSION = 'start'\n"
    assert "NOWY_SLOT" not in (frozen / "prompts" / "templates" / "planner.md").read_text(
        encoding="utf-8")


def test_unchanged_code_reuses_one_copy(tmp_path: Path) -> None:
    package = _package(tmp_path)
    first = snapshot.create(package, _environ(tmp_path))
    second = snapshot.create(package, _environ(tmp_path))
    assert second.path == first.path
    assert second.reused and not first.reused


def test_changed_code_gets_its_own_copy(tmp_path: Path) -> None:
    package = _package(tmp_path)
    first = snapshot.create(package, _environ(tmp_path))
    (package / "orchestrate.py").write_text("VERSION = 'nowa'\n", encoding="utf-8")
    second = snapshot.create(package, _environ(tmp_path))
    assert second.path != first.path
    assert (first.path / "forge" / "orchestrate.py").read_text(
        encoding="utf-8") == "VERSION = 'start'\n"


def test_snapshot_records_the_source_commit(tmp_path: Path) -> None:
    package = _package(tmp_path)
    subprocess.run(("git", "init", "-q", str(tmp_path)), check=True)
    subprocess.run(("git", "-C", str(tmp_path), "config", "user.email", "t@example.com"), check=True)
    subprocess.run(("git", "-C", str(tmp_path), "config", "user.name", "Test"), check=True)
    subprocess.run(("git", "-C", str(tmp_path), "add", "-A"), check=True)
    subprocess.run(("git", "-C", str(tmp_path), "commit", "-qm", "init"), check=True)
    clean = snapshot.create(package, _environ(tmp_path))
    assert len(clean.head) == 40
    assert not clean.dirty
    assert clean.head[:12] in clean.describe()

    (package / "orchestrate.py").write_text("VERSION = 'brudna'\n", encoding="utf-8")
    dirty = snapshot.create(package, _environ(tmp_path))
    assert dirty.dirty
    assert "+brudne" in dirty.describe()


def test_source_outside_git_still_yields_a_snapshot(tmp_path: Path) -> None:
    result = snapshot.create(_package(tmp_path), _environ(tmp_path))
    assert result.head == ""
    assert "bez gita" in result.describe()


def test_a_snapshot_in_use_survives_pruning(tmp_path: Path) -> None:
    """Pętla bez limitu iteracji może pracować dłużej niż okres przechowywania."""
    package = _package(tmp_path)
    working = snapshot.create(package, _environ(tmp_path))
    old = time.time() - (snapshot.KEEP_DAYS + 1) * 86_400
    lease = snapshot.hold(working.path, _environ(tmp_path))
    assert lease is not None, "migawka musi dać się wydzierżawić"
    os.utime(working.path, (old, old))

    snapshot.prune(tmp_path / "cache" / "forge" / "code")

    assert (working.path / "forge" / "orchestrate.py").is_file()

    lease.release()
    snapshot.prune(tmp_path / "cache" / "forge" / "code")
    assert not working.path.exists()


def test_several_runs_can_lease_one_snapshot(tmp_path: Path) -> None:
    package = _package(tmp_path)
    shared = snapshot.create(package, _environ(tmp_path))

    first = snapshot.hold(shared.path, _environ(tmp_path))
    second = snapshot.hold(shared.path, _environ(tmp_path))

    assert first is not None and second is not None
    first.release()
    second.release()


def test_the_working_tree_is_not_leased(tmp_path: Path) -> None:
    """Drzewo robocze nie jest migawką — nie zaśmiecamy go plikiem dzierżawy."""
    package = _package(tmp_path)

    assert snapshot.hold(package.parent, _environ(tmp_path)) is None
    assert not (package.parent / snapshot.LEASE_NAME).exists()


def test_a_snapshot_recognises_itself(tmp_path: Path) -> None:
    taken = snapshot.create(_package(tmp_path), _environ(tmp_path))
    environ = _environ(tmp_path)

    assert snapshot.is_snapshot(taken.path, environ)
    assert not snapshot.is_snapshot(tmp_path, environ)


def test_the_lease_does_not_change_the_next_fingerprint(tmp_path: Path) -> None:
    package = _package(tmp_path)
    taken = snapshot.create(package, _environ(tmp_path))
    lease = snapshot.hold(taken.path, _environ(tmp_path))

    again = snapshot.create(package, _environ(tmp_path))

    assert again.path == taken.path
    assert lease is not None
    lease.release()


def test_old_copies_are_pruned_but_fresh_ones_survive(tmp_path: Path) -> None:
    package = _package(tmp_path)
    stale = snapshot.create(package, _environ(tmp_path))
    old = time.time() - (snapshot.KEEP_DAYS + 1) * 86_400
    os.utime(stale.path, (old, old))
    (package / "orchestrate.py").write_text("VERSION = 'nowa'\n", encoding="utf-8")
    current = snapshot.create(package, _environ(tmp_path))
    assert not stale.path.exists()
    assert current.path.exists()


def test_reused_copy_is_marked_as_used_so_pruning_spares_it(tmp_path: Path) -> None:
    package = _package(tmp_path)
    taken = snapshot.create(package, _environ(tmp_path))
    old = time.time() - (snapshot.KEEP_DAYS + 1) * 86_400
    os.utime(taken.path, (old, old))
    again = snapshot.create(package, _environ(tmp_path))
    assert again.path == taken.path
    assert again.path.stat().st_mtime > old


def test_snapshots_are_temporary_files_free(tmp_path: Path) -> None:
    package = _package(tmp_path)
    snapshot.create(package, _environ(tmp_path))
    root = tmp_path / "cache" / "forge" / "code"
    assert [path.name for path in root.iterdir() if path.name.startswith(".")] == []


def test_project_key_separates_projects_with_the_same_name(tmp_path: Path) -> None:
    first, second = tmp_path / "a" / "game", tmp_path / "b" / "game"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    assert snapshot.project_key(str(first)) != snapshot.project_key(str(second))
    assert snapshot.project_key(str(first)).startswith("game-")
