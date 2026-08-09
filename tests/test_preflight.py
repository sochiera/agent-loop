import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from forge import orchestrate, preflight
from forge.agents import AgentError
from forge.config import Config
from forge.state import State


def _repo(path: Path, *, commit: bool = True) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    if commit:
        (path / ".gitignore").write_text(".forge/\n", encoding="utf-8")
        (path / "seed").write_text("seed\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitignore", "seed"], cwd=path, check=True)
        subprocess.run(["git", "commit", "-qm", "seed"], cwd=path, check=True)
    return path


def test_clean_repo_has_no_preflight_side_effects(tmp_path: Path) -> None:
    project = _repo(tmp_path)
    state = State(backlog_migrated=False)
    result = preflight.run(str(project), Config(git_push=False), state)
    assert result == preflight.PreflightResult()
    assert state.backlog_migrated is True
    assert not (project / ".forge" / "parked.md").exists()


def test_head_state_distinguishes_unborn_branch_and_detached_head(tmp_path: Path) -> None:
    project = _repo(tmp_path, commit=False)
    assert preflight.head_state(str(project)) == ("unborn", "")
    (project / "seed").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "seed"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=project, check=True)
    assert preflight.head_state(str(project))[0] == "branch"
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=project, text=True,
        capture_output=True, check=True).stdout.strip()
    subprocess.run(["git", "switch", "--detach", sha], cwd=project, check=True,
                   capture_output=True)
    assert preflight.head_state(str(project)) == ("detached", sha)


def test_dirty_tree_with_active_task_is_not_parked(tmp_path: Path) -> None:
    project = _repo(tmp_path)
    (project / "work.txt").write_text("wip\n", encoding="utf-8")
    state = State(current_task={"id": "task-001"})
    assert preflight.park_dirty_tree(str(project), Config(git_push=False), state) == ("", [])
    assert (project / "work.txt").exists()
    assert not list(project.glob("forge/parked/*"))


def test_dirty_tree_is_parked_and_returns_by_branch_name(tmp_path: Path) -> None:
    project = _repo(tmp_path)
    original_branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=project, text=True,
        capture_output=True, check=True).stdout.strip()
    (project / "work.txt").write_text("wip\n", encoding="utf-8")
    state = State()
    parked, paths = preflight.park_dirty_tree(str(project), Config(git_push=False), state)
    assert parked.startswith("forge/parked/")
    assert paths == ["work.txt"]
    assert subprocess.run(["git", "branch", "--show-current"], cwd=project,
                          text=True, capture_output=True, check=True).stdout.strip() == original_branch
    assert not preflight.has_changes(str(project))
    assert subprocess.run(["git", "show", f"{parked}:work.txt"], cwd=project,
                          text=True, capture_output=True, check=True).stdout == "wip\n"
    note = (project / ".forge" / "parked.md").read_text(encoding="utf-8")
    assert parked in note and "work.txt" in note


def test_unborn_dirty_tree_is_left_for_bootstrap(tmp_path: Path) -> None:
    project = _repo(tmp_path, commit=False)
    (project / "initial.txt").write_text("initial\n", encoding="utf-8")
    assert preflight.park_dirty_tree(str(project), Config(git_push=False), State()) == ("", [])
    assert preflight.head_state(str(project)) == ("unborn", "")
    assert (project / "initial.txt").exists()
    assert not (project / ".git" / "refs" / "heads" / "forge").exists()


def test_parking_failure_rolls_back_to_original_dirty_tree(tmp_path: Path) -> None:
    project = _repo(tmp_path)
    original_branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=project, text=True,
        capture_output=True, check=True).stdout.strip()
    (project / "work.txt").write_text("wip\n", encoding="utf-8")
    with patch("forge.preflight.commit_all", side_effect=RuntimeError("boom")):
        with pytest.raises(AgentError, match="parking"):
            preflight.park_dirty_tree(str(project), Config(git_push=False), State())
    assert subprocess.run(["git", "branch", "--show-current"], cwd=project,
                          text=True, capture_output=True, check=True).stdout.strip() == original_branch
    assert (project / "work.txt").read_text(encoding="utf-8") == "wip\n"
    assert not (project / ".git" / "refs" / "heads" / "forge" / "parked").exists()


def test_stale_tags_are_removed_but_active_tag_survives(tmp_path: Path) -> None:
    project = _repo(tmp_path)
    for tag in ("forge/task-001-start", "forge/task-002-start", "forge/task-003-start"):
        subprocess.run(["git", "tag", tag], cwd=project, check=True)
    state = State(current_task={"id": "task-002"})
    assert preflight.drop_stale_task_tags(str(project), state) == [
        "forge/task-001-start", "forge/task-003-start"]
    tags = subprocess.run(["git", "tag", "--list"], cwd=project, text=True,
                          capture_output=True, check=True).stdout.splitlines()
    assert tags == ["forge/task-002-start"]


def test_legacy_backlog_sets_migration_flag(tmp_path: Path) -> None:
    project = _repo(tmp_path)
    (project / "BACKLOG.md").write_text("- [ ] stara proza\n", encoding="utf-8")
    state = State()
    assert preflight.detect_legacy_backlog(str(project), state) is True
    assert state.backlog_migrated is False


def test_require_clean_identifies_preflight_invariant() -> None:
    # Regresja zakazu reflogowego powrotu: kod Forge ma używać jawnych celów.
    source = Path(orchestrate.__file__).read_text(encoding="utf-8")
    assert "git switch -" not in source
    assert "@{-1}" not in source
