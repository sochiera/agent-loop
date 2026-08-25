"""Cross-process ownership for one repository execution at a time."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO


class ExecutionLocked(RuntimeError):
    pass


class RepositoryLockError(RuntimeError):
    pass


def git_common_directory(repo: Path) -> Path:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        raise RepositoryLockError(
            f"cannot inspect repository lock directory for {repo}: {exc}"
        ) from exc
    if result.returncode != 0:
        raise RepositoryLockError(
            "cannot determine the repository lock directory:\n" + result.stdout.strip()
        )
    common = Path(result.stdout.strip())
    if not common.is_absolute():
        common = repo / common
    return common.resolve()


class RepositoryExecutionLock:
    """Hold an advisory OS lock across an entire run or recovery execution."""

    def __init__(self, repo: Path, branch: str, run_id: str):
        common = git_common_directory(repo.expanduser().resolve())
        lock_root = common / "forge-locks"
        lock_root.mkdir(mode=0o700, exist_ok=True)
        lock_root.chmod(0o700)
        self.path = lock_root / "execution.lock"
        self.branch = branch
        self.run_id = run_id
        self._handle: TextIO | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            raise RuntimeError("repository execution lock is already held")
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(descriptor, 0o600)
        handle = os.fdopen(descriptor, "r+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.seek(0)
            owner = handle.read().strip() or "unknown owner"
            handle.close()
            raise ExecutionLocked(
                f"another Forge execution owns this repository ({owner})"
            ) from exc
        metadata = {
            "pid": os.getpid(),
            "run_id": self.run_id,
            "branch": self.branch,
            "acquired_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(metadata, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self) -> "RepositoryExecutionLock":
        self.acquire()
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.release()
