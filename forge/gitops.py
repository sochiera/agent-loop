"""Explicit Git worktree lifecycle for the three-way competition."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class GitError(RuntimeError):
    pass


FORGE_LOCAL_EXCLUDES = (
    ".forge/",
    "node_modules/",
    ".venv/",
    "venv/",
    "__pycache__/",
    ".pytest_cache/",
    "*.py[cod]",
    "*.tsbuildinfo",
)

MAX_REVIEW_PATCH_BYTES = 2_000_000


def _run(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if check and result.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed:\n{result.stdout.strip()}")
    return result


@dataclass(frozen=True)
class CandidateWorktree:
    name: str
    path: Path
    branch: str


class GitCompetition:
    def __init__(
        self,
        repo: Path,
        branch: str,
        run_id: str,
        worktree_root: Path,
        *,
        local_excludes: tuple[str, ...] = (),
    ):
        self.repo = repo.resolve()
        self.branch = branch
        self.run_id = run_id
        self.worktree_root = worktree_root.resolve()
        self.local_excludes = local_excludes
        self.base_sha = ""
        self.candidates: dict[str, CandidateWorktree] = {}
        self.detached: list[Path] = []

    def prepare(self, *, require_remote: bool) -> str:
        exclude_value = _run(self.repo, "rev-parse", "--git-path", "info/exclude").stdout.strip()
        exclude = Path(exclude_value)
        if not exclude.is_absolute():
            exclude = self.repo / exclude
        exclude.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        wanted_excludes = (*FORGE_LOCAL_EXCLUDES, *self.local_excludes)
        known_excludes = {line.strip() for line in existing.splitlines()}
        missing_excludes = [item for item in wanted_excludes if item not in known_excludes]
        if missing_excludes:
            with exclude.open("a", encoding="utf-8") as handle:
                if existing and not existing.endswith("\n"):
                    handle.write("\n")
                handle.write("".join(f"{item}\n" for item in missing_excludes))
        if require_remote and _run(self.repo, "remote", "get-url", "origin", check=False).returncode:
            raise GitError("push is enabled but the target repository has no origin remote")
        if _run(self.repo, "var", "GIT_AUTHOR_IDENT", check=False).returncode:
            raise GitError("Git author identity is not configured for the target repository")
        if _run(self.repo, "status", "--porcelain").stdout.strip():
            raise GitError("target repository must be clean before Forge starts")
        has_head = _run(self.repo, "rev-parse", "--verify", "HEAD", check=False).returncode == 0
        if not has_head:
            _run(self.repo, "check-ref-format", "--branch", self.branch)
            current = _run(self.repo, "symbolic-ref", "--short", "HEAD").stdout.strip()
            if current != self.branch:
                _run(self.repo, "symbolic-ref", "HEAD", f"refs/heads/{self.branch}")
            _run(self.repo, "commit", "--allow-empty", "-m", "Initialize repository for Forge")
        else:
            if _run(
                self.repo,
                "show-ref",
                "--verify",
                f"refs/heads/{self.branch}",
                check=False,
            ).returncode:
                raise GitError(f"local branch does not exist: {self.branch}")
            current = _run(self.repo, "branch", "--show-current").stdout.strip()
            if current != self.branch:
                _run(self.repo, "switch", self.branch)
        self.base_sha = _run(self.repo, "rev-parse", "HEAD").stdout.strip()
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        return self.base_sha

    def create_candidates(self) -> dict[str, CandidateWorktree]:
        if not self.base_sha:
            raise GitError("competition must be prepared first")
        for name in ("tdd", "explore", "classic"):
            path = self.worktree_root / name
            branch = f"forge/{self.run_id}/{name}"
            if path.exists():
                shutil.rmtree(path)
            _run(self.repo, "worktree", "add", "-b", branch, str(path), self.base_sha)
            self.candidates[name] = CandidateWorktree(name, path, branch)
        return dict(self.candidates)

    def capture(self, candidate: CandidateWorktree) -> dict[str, Any]:
        # Intent-to-add makes untracked source files visible in the patch without staging content.
        _run(candidate.path, "add", "-N", ".", check=False)
        patch = _run(candidate.path, "diff", "--binary").stdout
        review_patch = _run(candidate.path, "diff", "--no-ext-diff", "--unified=3").stdout
        review_patch_truncated = len(review_patch.encode("utf-8")) > MAX_REVIEW_PATCH_BYTES
        if review_patch_truncated:
            review_patch = (
                "Forge omitted this oversized textual patch from the review context. "
                "Inspect the candidate worktree directly and use the recorded status/diffstat.\n"
            )
        return {
            "status": _run(candidate.path, "status", "--short").stdout,
            "diffstat": _run(candidate.path, "diff", "--stat").stdout,
            "patch": patch,
            "review_patch": review_patch,
            "review_patch_truncated": review_patch_truncated,
        }

    def commit_and_deliver(self, winner: CandidateWorktree, message: str, *, push: bool) -> str:
        _run(winner.path, "add", "-A")
        if _run(winner.path, "diff", "--cached", "--quiet", check=False).returncode == 0:
            raise GitError("winning candidate has no changes to commit")
        _run(winner.path, "commit", "-m", message)
        winner_sha = _run(winner.path, "rev-parse", "HEAD").stdout.strip()
        if _run(self.repo, "rev-parse", "HEAD").stdout.strip() != self.base_sha:
            raise GitError("selected branch changed during the competition; refusing non-fast-forward delivery")
        if _run(self.repo, "status", "--porcelain").stdout.strip():
            raise GitError("target checkout changed during the competition; refusing delivery")
        _run(self.repo, "merge", "--ff-only", winner.branch)
        if push:
            _run(self.repo, "push", "origin", self.branch)
        return winner_sha

    def create_detached(self, name: str) -> Path:
        path = self.worktree_root / name
        if path.exists():
            shutil.rmtree(path)
        _run(self.repo, "worktree", "add", "--detach", str(path), "HEAD")
        self.detached.append(path)
        return path

    def remove_detached(self, path: Path) -> None:
        _run(self.repo, "worktree", "remove", "--force", str(path), check=False)
        if path in self.detached:
            self.detached.remove(path)

    def cleanup(self) -> None:
        for candidate in self.candidates.values():
            _run(self.repo, "worktree", "remove", "--force", str(candidate.path), check=False)
        for path in tuple(self.detached):
            self.remove_detached(path)
        _run(self.repo, "worktree", "prune", check=False)
        for candidate in self.candidates.values():
            _run(self.repo, "branch", "-D", candidate.branch, check=False)
        try:
            self.worktree_root.rmdir()
        except OSError:
            pass


def list_branches(repo: Path) -> list[str]:
    result = _run(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    branches = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    symbolic = _run(repo, "symbolic-ref", "--short", "HEAD", check=False)
    current = symbolic.stdout.strip() if symbolic.returncode == 0 else ""
    if current and current not in branches:
        branches.insert(0, current)
    return branches


def repository_summary(repo: Path) -> dict[str, Any]:
    repo = repo.expanduser().resolve()
    branches = list_branches(repo)
    symbolic = _run(repo, "symbolic-ref", "--short", "HEAD", check=False)
    current = symbolic.stdout.strip() if symbolic.returncode == 0 else ""
    has_head = _run(repo, "rev-parse", "--verify", "HEAD", check=False).returncode == 0
    status = _run(repo, "status", "--porcelain").stdout.splitlines()
    return {
        "repo": str(repo),
        "branches": branches,
        "current_branch": current,
        "has_head": has_head,
        "status": status,
    }
