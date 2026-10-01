import subprocess
import sys
import stat
from pathlib import Path

import pytest

from forge.locking import (
    ExecutionLocked,
    RepositoryExecutionLock,
    RepositoryLockError,
    git_common_directory,
)
from forge.models import ModelSpec, ROLE_NAMES, RunConfig
from forge.orchestrator import ForgeOrchestrator


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, text=True, check=True, stdout=subprocess.PIPE
    ).stdout.strip()


def initialized_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "forge@example.test")
    git(repo, "config", "user.name", "Forge Test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    return repo


LOCK_PROBE = """
import sys
from pathlib import Path
from forge.locking import ExecutionLocked, RepositoryExecutionLock

lock = RepositoryExecutionLock(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
try:
    lock.acquire()
except ExecutionLocked:
    raise SystemExit(23)
else:
    lock.release()
"""


def probe(repo: Path, branch: str, run_id: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", LOCK_PROBE, str(repo), branch, run_id],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_repository_execution_lock_excludes_other_processes_and_runs(tmp_path: Path):
    repo = initialized_repo(tmp_path)
    lock = RepositoryExecutionLock(repo, "main", "run-one")
    lock.acquire()
    try:
        blocked_same_run = probe(repo, "main", "run-one")
        blocked_other_run = probe(repo, "other-branch", "run-two")
        assert blocked_same_run.returncode == 23
        assert blocked_other_run.returncode == 23
        assert "run-one" in lock.path.read_text(encoding="utf-8")
    finally:
        lock.release()

    assert probe(repo, "main", "run-three").returncode == 0


def test_lock_uses_canonical_git_common_directory(tmp_path: Path):
    repo = initialized_repo(tmp_path)
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-b", "other", str(linked))

    assert git_common_directory(repo) == git_common_directory(linked)
    first = RepositoryExecutionLock(repo, "main", "main-run")
    second = RepositoryExecutionLock(linked, "other", "linked-run")
    first.acquire()
    try:
        try:
            second.acquire()
        except ExecutionLocked:
            pass
        else:
            raise AssertionError("linked worktree bypassed the repository execution lock")
    finally:
        first.release()


def test_lock_file_permissions_and_invalid_repository_error(tmp_path: Path):
    repo = initialized_repo(tmp_path)
    lock = RepositoryExecutionLock(repo, "main", "permissions")
    lock.acquire()
    try:
        assert stat.S_IMODE(lock.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(lock.path.parent.stat().st_mode) == 0o700
    finally:
        lock.release()

    with pytest.raises(RepositoryLockError, match="cannot inspect repository"):
        RepositoryExecutionLock(tmp_path / "missing", "main", "invalid")


def test_orchestrator_acquires_ownership_before_persisting_run_state(tmp_path: Path):
    repo = initialized_repo(tmp_path)
    brief = tmp_path / "brief.md"
    brief.write_text("Build continuously.\n", encoding="utf-8")
    models = {role: ModelSpec.parse("codex:gpt-6-sol:medium") for role in ROLE_NAMES}
    config = RunConfig(str(repo), str(brief), "main", models, push=False)
    orchestrator = ForgeOrchestrator(
        config,
        run_id="blocked-run",
        state_home=tmp_path / "state",
        check_binaries=False,
    )
    owner = RepositoryExecutionLock(repo, "main", "owner-run")
    owner.acquire()
    try:
        with pytest.raises(ExecutionLocked, match="another Forge execution"):
            orchestrator.run()
    finally:
        owner.release()

    assert orchestrator.state.status == "created"
    assert orchestrator.store.state_path.exists() is False
