"""Explicit Git worktree lifecycles for Forge orchestrators."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
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


def _prepare_repository(
    repo: Path,
    branch: str,
    worktree_root: Path,
    local_excludes: tuple[str, ...],
    *,
    require_remote: bool,
) -> str:
    exclude_value = _run(repo, "rev-parse", "--git-path", "info/exclude").stdout.strip()
    exclude = Path(exclude_value)
    if not exclude.is_absolute():
        exclude = repo / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    wanted_excludes = (*FORGE_LOCAL_EXCLUDES, *local_excludes)
    known_excludes = {line.strip() for line in existing.splitlines()}
    missing_excludes = [item for item in wanted_excludes if item not in known_excludes]
    if missing_excludes:
        with exclude.open("a", encoding="utf-8") as handle:
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write("".join(f"{item}\n" for item in missing_excludes))
    if require_remote and _run(repo, "remote", "get-url", "origin", check=False).returncode:
        raise GitError("push is enabled but the target repository has no origin remote")
    if _run(repo, "var", "GIT_AUTHOR_IDENT", check=False).returncode:
        raise GitError("Git author identity is not configured for the target repository")
    if _run(repo, "var", "GIT_COMMITTER_IDENT", check=False).returncode:
        raise GitError("Git committer identity is not configured for the target repository")
    if _run(repo, "status", "--porcelain").stdout.strip():
        raise GitError("target repository must be clean before Forge starts")
    has_head = _run(repo, "rev-parse", "--verify", "HEAD", check=False).returncode == 0
    if not has_head:
        _run(repo, "check-ref-format", "--branch", branch)
        current = _run(repo, "symbolic-ref", "--short", "HEAD").stdout.strip()
        if current != branch:
            _run(repo, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
        _run(repo, "commit", "--allow-empty", "-m", "Initialize repository for Forge")
    else:
        if _run(
            repo,
            "show-ref",
            "--verify",
            f"refs/heads/{branch}",
            check=False,
        ).returncode:
            raise GitError(f"local branch does not exist: {branch}")
        current = _run(repo, "branch", "--show-current").stdout.strip()
        if current != branch:
            _run(repo, "switch", branch)
    base_sha = _run(repo, "rev-parse", "HEAD").stdout.strip()
    worktree_root.mkdir(parents=True, exist_ok=True)
    return base_sha


def _capture_workspace_candidate(
    candidate: CandidateWorktree, base_sha: str
) -> dict[str, Any]:
    """Capture the complete prospective tree without changing the real index.

    A workspace may contain staged, unstaged, untracked, or already committed
    changes.  A base-to-tree snapshot gives all of those states one stable
    identity and remains unchanged when the controller prepares the commit.
    """

    descriptor, index_name = tempfile.mkstemp(prefix="forge-index-")
    os.close(descriptor)
    index_path = Path(index_name)
    index_path.unlink()
    environment = os.environ.copy()
    environment["GIT_INDEX_FILE"] = str(index_path)

    def snapshot_git(*args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=candidate.path,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if result.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed:\n{result.stdout.strip()}")
        return result.stdout

    try:
        snapshot_git("read-tree", "HEAD")
        snapshot_git("add", "-A")
        tree = snapshot_git("write-tree").strip()
        patch = snapshot_git("diff", "--binary", base_sha, tree)
        review_patch = snapshot_git(
            "diff", "--no-ext-diff", "--unified=3", base_sha, tree
        )
        diffstat = snapshot_git("diff", "--stat", base_sha, tree)
    finally:
        index_path.unlink(missing_ok=True)
        Path(f"{index_path}.lock").unlink(missing_ok=True)

    review_patch_truncated = len(review_patch.encode("utf-8")) > MAX_REVIEW_PATCH_BYTES
    if review_patch_truncated:
        review_patch = (
            "Forge omitted this oversized textual patch from the review context. "
            "Inspect the candidate worktree directly and use the recorded status/diffstat.\n"
        )
    digest = hashlib.sha256()
    digest.update(base_sha.encode("ascii"))
    digest.update(b"\0")
    digest.update(tree.encode("ascii"))
    return {
        "status": _run(candidate.path, "status", "--short").stdout,
        "diffstat": diffstat,
        "patch": patch,
        "review_patch": review_patch,
        "review_patch_truncated": review_patch_truncated,
        "tree": tree,
        "fingerprint": digest.hexdigest(),
    }


class GitWorkspace:
    """A recoverable single-candidate workspace with strict fast-forward delivery."""

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
        self._candidate: CandidateWorktree | None = None
        self._candidate_base_sha = ""

    @property
    def implementation_branch(self) -> str:
        return f"forge/{self.run_id}/implementation"

    def prepare(self, require_remote: bool) -> str:
        self.base_sha = _prepare_repository(
            self.repo,
            self.branch,
            self.worktree_root,
            self.local_excludes,
            require_remote=require_remote,
        )
        return self.base_sha

    def create_or_reattach(
        self, base_sha: str, recover: bool = False
    ) -> CandidateWorktree:
        self._require_prepared()
        base_sha = self._resolve_commit(base_sha, "candidate base")
        branch = self.implementation_branch
        _run(self.repo, "check-ref-format", "--branch", branch)
        path = self.worktree_root / "implementation"
        candidate = CandidateWorktree("implementation", path, branch)
        path_exists = path.exists() or path.is_symlink()
        branch_exists = self._branch_exists(branch)

        if path_exists:
            if not recover:
                raise GitError(f"implementation worktree already exists: {path}")
            head = self._inspect_candidate(candidate)
        elif branch_exists:
            if not recover:
                raise GitError(f"implementation branch already exists: {branch}")
            head = self._resolve_commit(f"refs/heads/{branch}", "implementation branch")
            self._validate_candidate_position(head, base_sha)
            _run(self.repo, "worktree", "prune", check=False)
            _run(self.repo, "worktree", "add", str(path), branch)
            head = self._inspect_candidate(candidate)
        else:
            if self.target_head() != base_sha:
                raise GitError(
                    "target branch changed after the candidate base was selected; "
                    "refusing to create a stale implementation branch"
                )
            _run(self.repo, "worktree", "add", "-b", branch, str(path), base_sha)
            head = self._inspect_candidate(candidate)

        self._validate_candidate_position(head, base_sha)
        self._candidate = candidate
        self._candidate_base_sha = base_sha
        return candidate

    def capture(self, candidate: CandidateWorktree) -> dict[str, Any]:
        self._inspect_candidate(candidate)
        return _capture_workspace_candidate(candidate, self._candidate_base(candidate))

    def fingerprint(self, candidate: CandidateWorktree) -> str:
        return str(self.capture(candidate)["fingerprint"])

    def candidate_head(self, candidate: CandidateWorktree) -> str:
        return self._inspect_candidate(candidate)

    def target_head(self) -> str:
        return _run(self.repo, "rev-parse", "HEAD").stdout.strip()

    def prepare_commit(
        self,
        candidate: CandidateWorktree,
        message: str,
        *,
        expected_tree: str | None = None,
    ) -> str:
        head = self._inspect_candidate(candidate)
        base_sha = self._candidate_base(candidate)
        if head != base_sha:
            self._verify_candidate_commit(candidate, head, base_sha)
            if expected_tree is not None and self._tree(head) != expected_tree:
                raise GitError("prepared commit tree does not match the accepted candidate tree")
            return head

        _run(candidate.path, "add", "-A")
        if _run(candidate.path, "diff", "--cached", "--quiet", check=False).returncode == 0:
            raise GitError("implementation candidate has no changes to commit")
        candidate_tree = _run(candidate.path, "write-tree").stdout.strip()
        if expected_tree is not None and expected_tree != candidate_tree:
            raise GitError("candidate tree changed after acceptance")
        _run(candidate.path, "commit", "-m", message)
        commit = self._inspect_candidate(candidate)
        self._validate_delivery_commit(commit, base_sha)
        actual_tree = self._tree(commit)
        if actual_tree != candidate_tree:
            raise GitError("prepared commit tree does not match the candidate state")
        self._verify_candidate_commit(candidate, commit, base_sha)
        return commit

    def integrate(self, commit: str, expected_base: str, push: bool) -> str:
        self._require_prepared()
        expected_base = self._resolve_commit(expected_base, "expected target base")
        commit = self._resolve_commit(commit, "implementation commit")
        self._validate_delivery_commit(commit, expected_base)
        if push and _run(
            self.repo, "remote", "get-url", "origin", check=False
        ).returncode:
            raise GitError("push is enabled but the target repository has no origin remote")
        self._verify_target_checkout()

        target = self.target_head()
        if target == expected_base:
            _run(self.repo, "merge", "--ff-only", commit)
            target = self.target_head()
            if target != commit:
                raise GitError("fast-forward delivery did not reach the implementation commit")
            self._verify_target_checkout()
        elif target != commit:
            raise GitError(
                "target branch changed after the expected base; refusing external drift"
            )

        if push:
            ref = f"refs/heads/{self.branch}:refs/heads/{self.branch}"
            _run(self.repo, "push", "origin", ref)
        return commit

    def reconcile_delivery(
        self,
        candidate: CandidateWorktree,
        expected_base: str,
        recorded_commit: str | None = None,
        *,
        push: bool,
    ) -> str:
        expected_base = self._resolve_commit(expected_base, "expected target base")
        candidate_commit = self._inspect_candidate(candidate)
        if candidate_commit == expected_base:
            raise GitError("implementation candidate has not been committed")
        self._verify_candidate_commit(candidate, candidate_commit, expected_base)
        if recorded_commit is not None:
            recorded_commit = self._resolve_commit(recorded_commit, "recorded implementation commit")
            if recorded_commit != candidate_commit:
                raise GitError("recorded implementation commit does not match the candidate branch")
        return self.integrate(candidate_commit, expected_base, push)

    def create_disposable_copy(
        self, candidate: CandidateWorktree, destination: Path
    ) -> Path:
        self._inspect_candidate(candidate)
        source = candidate.path.resolve()
        destination = destination.expanduser().resolve()
        if destination == source or source in destination.parents:
            raise GitError("disposable copy destination must be outside the candidate worktree")
        if destination.exists() or destination.is_symlink():
            raise GitError(f"disposable copy destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)

        def ignore_local_artifacts(_directory: str, names: list[str]) -> list[str]:
            return [name for name in (".git", ".forge") if name in names]

        shutil.copytree(
            source,
            destination,
            symlinks=True,
            ignore=ignore_local_artifacts,
        )
        return destination

    def cleanup(self) -> None:
        if self._candidate is not None:
            candidate = self._candidate
            _run(
                self.repo,
                "worktree",
                "remove",
                "--force",
                str(candidate.path),
                check=False,
            )
            _run(self.repo, "worktree", "prune", check=False)
            _run(self.repo, "branch", "-D", candidate.branch, check=False)
            self._candidate = None
            self._candidate_base_sha = ""
        try:
            self.worktree_root.rmdir()
        except OSError:
            pass

    def _require_prepared(self) -> None:
        if not self.base_sha:
            raise GitError("workspace must be prepared first")

    def _resolve_commit(self, value: str, label: str) -> str:
        if not value.strip():
            raise GitError(f"{label} is empty")
        result = _run(
            self.repo,
            "rev-parse",
            "--verify",
            f"{value}^{{commit}}",
            check=False,
        )
        if result.returncode != 0:
            raise GitError(f"{label} is not an available commit: {value}")
        return result.stdout.strip()

    def _branch_exists(self, branch: str) -> bool:
        return (
            _run(
                self.repo,
                "show-ref",
                "--verify",
                f"refs/heads/{branch}",
                check=False,
            ).returncode
            == 0
        )

    def _inspect_candidate(self, candidate: CandidateWorktree) -> str:
        expected = CandidateWorktree(
            "implementation",
            self.worktree_root / "implementation",
            self.implementation_branch,
        )
        if candidate != expected:
            raise GitError("candidate does not belong to this implementation workspace")
        if not candidate.path.is_dir():
            raise GitError(f"implementation worktree is missing: {candidate.path}")
        top = Path(_run(candidate.path, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
        if top != candidate.path.resolve():
            raise GitError(f"invalid implementation worktree: {candidate.path}")
        candidate_common = Path(
            _run(candidate.path, "rev-parse", "--git-common-dir").stdout.strip()
        )
        if not candidate_common.is_absolute():
            candidate_common = candidate.path / candidate_common
        repo_common = Path(_run(self.repo, "rev-parse", "--git-common-dir").stdout.strip())
        if not repo_common.is_absolute():
            repo_common = self.repo / repo_common
        if candidate_common.resolve() != repo_common.resolve():
            raise GitError("implementation worktree belongs to a different repository")
        current = _run(candidate.path, "branch", "--show-current").stdout.strip()
        if current != candidate.branch:
            raise GitError(
                f"implementation worktree is on {current or 'detached HEAD'}, "
                f"expected {candidate.branch}"
            )
        head = _run(candidate.path, "rev-parse", "HEAD").stdout.strip()
        branch_head = self._resolve_commit(
            f"refs/heads/{candidate.branch}", "implementation branch"
        )
        if branch_head != head:
            raise GitError("implementation branch and worktree HEAD do not match")
        return head

    def _candidate_base(self, candidate: CandidateWorktree) -> str:
        if self._candidate != candidate or not self._candidate_base_sha:
            raise GitError("candidate has not been attached to this workspace")
        return self._candidate_base_sha

    def _validate_candidate_position(self, head: str, base_sha: str) -> None:
        if head != base_sha:
            self._validate_delivery_commit(head, base_sha)

    def _validate_delivery_commit(self, commit: str, base_sha: str) -> None:
        parents = _run(self.repo, "rev-list", "--parents", "-n", "1", commit).stdout.split()
        if parents != [commit, base_sha]:
            raise GitError("implementation must be exactly one commit over its recorded base")
        if self._tree(commit) == self._tree(base_sha):
            raise GitError("implementation commit is empty")

    def _verify_candidate_commit(
        self, candidate: CandidateWorktree, commit: str, base_sha: str
    ) -> None:
        self._validate_delivery_commit(commit, base_sha)
        if self._inspect_candidate(candidate) != commit:
            raise GitError("implementation worktree is not at the prepared commit")
        if _run(candidate.path, "status", "--porcelain").stdout.strip():
            raise GitError("implementation changed after its commit was prepared")
        if _run(candidate.path, "write-tree").stdout.strip() != self._tree(commit):
            raise GitError("prepared commit tree does not match the candidate state")

    def _verify_target_checkout(self) -> None:
        current = _run(self.repo, "branch", "--show-current").stdout.strip()
        if current != self.branch:
            raise GitError(
                f"target checkout is on {current or 'detached HEAD'}, expected {self.branch}"
            )
        if _run(self.repo, "status", "--porcelain").stdout.strip():
            raise GitError("target checkout changed during delivery; refusing external drift")

    def _tree(self, commit: str) -> str:
        return _run(self.repo, "rev-parse", f"{commit}^{{tree}}").stdout.strip()


def export_revision(repo: Path, destination: Path, revision: str = "HEAD") -> str:
    """Export a committed tree without exposing repository metadata or untracked files."""

    repo = repo.expanduser().resolve()
    requested_destination = destination.expanduser()
    normalized_destination = Path(os.path.abspath(requested_destination))
    destination = normalized_destination.parent.resolve() / normalized_destination.name
    if (
        destination == repo
        or destination.is_relative_to(repo)
        or repo.is_relative_to(destination)
    ):
        raise GitError("export destination must not overlap the source repository")
    commit = _run(repo, "rev-parse", "--verify", f"{revision}^{{commit}}").stdout.strip()
    result = subprocess.run(
        ["git", "archive", "--format=tar", commit],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise GitError(
            "git archive failed:\n" + result.stderr.decode("utf-8", errors="replace").strip()
        )
    temporary: Path | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{destination.name}.export-", dir=destination.parent)
        )
        tracked = _run(repo, "ls-tree", "-r", "--name-only", commit).stdout.strip()
        if tracked:
            with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
                archive.extractall(temporary, filter="data")
        if destination.is_symlink() or destination.is_file():
            destination.unlink()
        elif destination.exists():
            shutil.rmtree(destination)
        temporary.replace(destination)
        temporary = None
    except (tarfile.TarError, OSError) as exc:
        raise GitError(f"could not export product revision {commit}: {exc}") from exc
    finally:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)
    return commit


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
