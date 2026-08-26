import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from forge.gitops import GitError, GitWorkspace, export_revision, list_branches


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


def test_export_revision_handles_empty_commit(tmp_path: Path):
    repo = tmp_path / "empty-repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "forge@example.test")
    git(repo, "config", "user.name", "Forge Test")
    git(repo, "commit", "--allow-empty", "-m", "empty")
    destination = tmp_path / "snapshot"

    commit = export_revision(repo, destination)

    assert commit == git(repo, "rev-parse", "HEAD")
    assert destination.is_dir()
    assert list(destination.iterdir()) == []


def test_export_revision_replaces_file_only_after_success(tmp_path: Path):
    repo = initialized_repo(tmp_path)
    destination = tmp_path / "snapshot"
    destination.write_text("old\n", encoding="utf-8")

    export_revision(repo, destination)

    assert destination.is_dir()
    assert (destination / "README.md").read_text(encoding="utf-8") == "base\n"


@pytest.mark.parametrize(
    "destination_name", ["repo", "repo/inside", ".", "repo/staging/.."]
)
def test_export_revision_rejects_repository_overlap(
    tmp_path: Path, destination_name: str
):
    repo = initialized_repo(tmp_path)
    destination = repo if destination_name == "repo" else tmp_path / destination_name

    with pytest.raises(GitError, match="must not overlap"):
        export_revision(repo, destination)

    assert (repo / "README.md").read_text(encoding="utf-8") == "base\n"


def test_prepare_bootstraps_unborn_branch_and_excludes_brief(tmp_path: Path):
    repo = tmp_path / "empty"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "forge@example.test")
    git(repo, "config", "user.name", "Forge Test")
    (repo / "goal.md").write_text("Build something.\n", encoding="utf-8")
    assert list_branches(repo) == ["main"]

    workspace = GitWorkspace(
        repo,
        "main",
        "run",
        tmp_path / "worktrees",
        local_excludes=("/goal.md",),
    )
    base = workspace.prepare(require_remote=False)

    assert git(repo, "rev-parse", "HEAD") == base
    assert git(repo, "branch", "--show-current") == "main"
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "log", "-1", "--pretty=%s") == "Initialize repository for Forge"


def test_workspace_capture_excludes_generated_dependency_trees(tmp_path: Path):
    repo = initialized_repo(tmp_path)
    workspace = GitWorkspace(repo, "main", "run", tmp_path / "worktrees")
    base = workspace.prepare(require_remote=False)
    candidate = workspace.create_or_reattach("tdd", base)
    (candidate.path / "node_modules/pkg").mkdir(parents=True)
    (candidate.path / "node_modules/pkg/index.js").write_text("generated\n", encoding="utf-8")
    (candidate.path / "feature.js").write_text("product\n", encoding="utf-8")

    captured = workspace.capture(candidate)

    assert "feature.js" in captured["patch"]
    assert "node_modules" not in captured["patch"]
    assert "node_modules" not in captured["status"]
    workspace.cleanup()


def test_workspace_delivers_clean_candidate_idempotently(tmp_path: Path):
    repo = initialized_repo(tmp_path)
    workspace = GitWorkspace(repo, "main", "sprint", tmp_path / "worktrees")
    base = workspace.prepare(require_remote=False)
    candidate = workspace.create_or_reattach("tdd", base)
    (candidate.path / "feature.txt").write_text("delivered\n", encoding="utf-8")

    captured = workspace.capture(candidate)
    assert set(captured) == {
        "status",
        "diffstat",
        "patch",
        "review_patch",
        "review_patch_truncated",
        "modes",
        "tree",
        "fingerprint",
    }
    assert "feature.txt" in captured["patch"]
    assert candidate.name == "tdd"
    assert candidate.branch == "forge/sprint/tdd"

    commit = workspace.prepare_commit(candidate, "Implement the sprint")
    assert workspace.prepare_commit(candidate, "Must not commit twice") == commit
    assert workspace.candidate_head(candidate) == commit
    assert git(candidate.path, "rev-parse", "HEAD^") == base
    assert workspace.integrate(commit, base, push=False) == commit
    assert workspace.integrate(commit, base, push=False) == commit
    assert workspace.target_head() == commit
    assert (repo / "feature.txt").read_text(encoding="utf-8") == "delivered\n"

    workspace.cleanup()
    assert not candidate.path.exists()


def test_workspace_reattaches_dirty_implementation(tmp_path: Path):
    repo = initialized_repo(tmp_path)
    root = tmp_path / "worktrees"
    original = GitWorkspace(repo, "main", "sprint", root)
    base = original.prepare(require_remote=False)
    candidate = original.create_or_reattach("tdd", base)
    (candidate.path / "README.md").write_text("changed\n", encoding="utf-8")
    (candidate.path / "new.txt").write_text("untracked\n", encoding="utf-8")

    recovered = GitWorkspace(repo, "main", "sprint", root)
    assert recovered.prepare(require_remote=False) == base
    reattached = recovered.create_or_reattach("tdd", base, recover=True)

    assert reattached == candidate
    assert (reattached.path / "README.md").read_text(encoding="utf-8") == "changed\n"
    assert (reattached.path / "new.txt").read_text(encoding="utf-8") == "untracked\n"
    assert "README.md" in recovered.capture(reattached)["status"]
    recovered.cleanup()


def test_workspace_refuses_external_target_branch_drift(tmp_path: Path):
    repo = initialized_repo(tmp_path)
    workspace = GitWorkspace(repo, "main", "sprint", tmp_path / "worktrees")
    base = workspace.prepare(require_remote=False)
    candidate = workspace.create_or_reattach("tdd", base)
    (candidate.path / "feature.txt").write_text("candidate\n", encoding="utf-8")
    commit = workspace.prepare_commit(candidate, "Candidate commit")

    (repo / "external.txt").write_text("external\n", encoding="utf-8")
    git(repo, "add", "external.txt")
    git(repo, "commit", "-m", "External change")
    external_head = git(repo, "rev-parse", "HEAD")

    with pytest.raises(GitError, match="external drift"):
        workspace.integrate(commit, base, push=False)

    assert workspace.target_head() == external_head
    assert not (repo / "feature.txt").exists()
    workspace.cleanup()


def test_workspace_recovers_committed_candidate_before_integration(tmp_path: Path):
    repo = initialized_repo(tmp_path)
    root = tmp_path / "worktrees"
    original = GitWorkspace(repo, "main", "sprint", root)
    base = original.prepare(require_remote=False)
    candidate = original.create_or_reattach("tdd", base)
    (candidate.path / "feature.txt").write_text("recover me\n", encoding="utf-8")
    commit = original.prepare_commit(candidate, "Prepared before crash")
    assert original.target_head() == base

    recovered = GitWorkspace(repo, "main", "sprint", root)
    assert recovered.prepare(require_remote=False) == base
    candidate = recovered.create_or_reattach("tdd", base, recover=True)

    assert recovered.reconcile_delivery(candidate, base, push=False) == commit
    assert recovered.target_head() == commit
    assert (repo / "feature.txt").read_text(encoding="utf-8") == "recover me\n"
    recovered.cleanup()


def test_workspace_recovers_when_main_is_already_integrated(tmp_path: Path):
    repo = initialized_repo(tmp_path)
    root = tmp_path / "worktrees"
    original = GitWorkspace(repo, "main", "sprint", root)
    base = original.prepare(require_remote=False)
    candidate = original.create_or_reattach("tdd", base)
    (candidate.path / "feature.txt").write_text("already delivered\n", encoding="utf-8")
    commit = original.prepare_commit(candidate, "Prepared and integrated")
    original.integrate(commit, base, push=False)

    recovered = GitWorkspace(repo, "main", "sprint", root)
    assert recovered.prepare(require_remote=False) == commit
    candidate = recovered.create_or_reattach("tdd", base, recover=True)

    assert (
        recovered.reconcile_delivery(
            candidate,
            base,
            recorded_commit=commit,
            push=False,
        )
        == commit
    )
    assert recovered.target_head() == commit
    recovered.cleanup()


def test_workspace_rejects_empty_candidate_patch(tmp_path: Path):
    repo = initialized_repo(tmp_path)
    workspace = GitWorkspace(repo, "main", "sprint", tmp_path / "worktrees")
    base = workspace.prepare(require_remote=False)
    candidate = workspace.create_or_reattach("tdd", base)

    with pytest.raises(GitError, match="no changes"):
        workspace.prepare_commit(candidate, "Empty sprint")

    assert workspace.candidate_head(candidate) == base
    workspace.cleanup()


def test_workspace_fingerprint_is_stable_and_tracks_binary_patch(tmp_path: Path):
    repo = initialized_repo(tmp_path)
    workspace = GitWorkspace(repo, "main", "sprint", tmp_path / "worktrees")
    base = workspace.prepare(require_remote=False)
    candidate = workspace.create_or_reattach("tdd", base)
    asset = candidate.path / "asset.bin"
    asset.write_bytes(b"\x00candidate-v1\xff")

    first = workspace.fingerprint(candidate)
    second = workspace.fingerprint(candidate)
    patch = workspace.capture(candidate)["patch"]

    assert first == second
    tree = workspace.capture(candidate)["tree"]
    assert first == hashlib.sha256(f"{base}\0{tree}".encode("ascii")).hexdigest()
    assert len(first) == 64

    asset.write_bytes(b"\x00candidate-v2\xff")
    assert workspace.fingerprint(candidate) != first
    workspace.cleanup()


def test_workspace_fingerprint_covers_staged_changes_and_survives_commit(tmp_path: Path):
    repo = initialized_repo(tmp_path)
    workspace = GitWorkspace(repo, "main", "sprint", tmp_path / "worktrees")
    base = workspace.prepare(require_remote=False)
    candidate = workspace.create_or_reattach("tdd", base)
    (candidate.path / "staged.txt").write_text("first\n", encoding="utf-8")
    git(candidate.path, "add", "staged.txt")

    first = workspace.capture(candidate)
    (candidate.path / "staged.txt").write_text("second\n", encoding="utf-8")
    git(candidate.path, "add", "staged.txt")
    second = workspace.capture(candidate)

    assert first["fingerprint"] != second["fingerprint"]
    assert first["tree"] != second["tree"]
    commit = workspace.prepare_commit(
        candidate, "Commit accepted tree", expected_tree=second["tree"]
    )
    after_commit = workspace.capture(candidate)
    assert after_commit["fingerprint"] == second["fingerprint"]
    assert after_commit["tree"] == second["tree"] == git(candidate.path, "show", "-s", "--format=%T", commit)
    workspace.cleanup()


def test_workspace_refuses_to_commit_a_tree_changed_after_acceptance(tmp_path: Path):
    repo = initialized_repo(tmp_path)
    workspace = GitWorkspace(repo, "main", "sprint", tmp_path / "worktrees")
    base = workspace.prepare(require_remote=False)
    candidate = workspace.create_or_reattach("tdd", base)
    (candidate.path / "accepted.txt").write_text("accepted\n", encoding="utf-8")
    accepted_tree = workspace.capture(candidate)["tree"]
    (candidate.path / "late.txt").write_text("late mutation\n", encoding="utf-8")

    with pytest.raises(GitError, match="changed after acceptance"):
        workspace.prepare_commit(candidate, "Must not commit", expected_tree=accepted_tree)

    assert workspace.candidate_head(candidate) == base
    workspace.cleanup()


def test_workspace_disposable_copy_is_isolated_and_omits_local_artifacts(tmp_path: Path):
    repo = initialized_repo(tmp_path)
    workspace = GitWorkspace(repo, "main", "sprint", tmp_path / "worktrees")
    base = workspace.prepare(require_remote=False)
    candidate = workspace.create_or_reattach("tdd", base)
    (candidate.path / "README.md").write_text("candidate\n", encoding="utf-8")
    (candidate.path / "feature.txt").write_text("working state\n", encoding="utf-8")
    (candidate.path / "node_modules/pkg").mkdir(parents=True)
    (candidate.path / "node_modules/pkg/index.js").write_text("dependency\n", encoding="utf-8")
    (candidate.path / ".forge").mkdir()
    (candidate.path / ".forge/plan.md").write_text("local plan\n", encoding="utf-8")

    destination = workspace.create_disposable_copy(candidate, tmp_path / "tester-copy")

    assert (destination / "README.md").read_text(encoding="utf-8") == "candidate\n"
    assert (destination / "feature.txt").read_text(encoding="utf-8") == "working state\n"
    assert (destination / "node_modules/pkg/index.js").read_text(encoding="utf-8") == "dependency\n"
    assert not (destination / ".git").exists()
    assert not (destination / ".forge").exists()

    (destination / "feature.txt").write_text("tester mutation\n", encoding="utf-8")
    assert (candidate.path / "feature.txt").read_text(encoding="utf-8") == "working state\n"
    (candidate.path / "README.md").write_text("later candidate state\n", encoding="utf-8")
    assert (destination / "README.md").read_text(encoding="utf-8") == "candidate\n"

    workspace.cleanup()
    assert (destination / "feature.txt").read_text(encoding="utf-8") == "tester mutation\n"


def test_workspace_creates_three_isolated_candidates_from_one_base(tmp_path: Path):
    from forge.sprint import CODER_CANDIDATES

    repo = initialized_repo(tmp_path)
    workspace = GitWorkspace(repo, "main", "run", tmp_path / "worktrees")
    base = workspace.prepare(require_remote=False)

    candidates = {
        name: workspace.create_or_reattach(name, base) for name in CODER_CANDIDATES
    }

    assert {name: item.name for name, item in candidates.items()} == {
        name: name for name in CODER_CANDIDATES
    }
    assert len({item.branch for item in candidates.values()}) == 3
    assert len({item.path for item in candidates.values()}) == 3
    for item in candidates.values():
        assert git(item.path, "rev-parse", "HEAD") == base

    (candidates["tdd"].path / "tdd.txt").write_text("only in tdd\n", encoding="utf-8")
    assert workspace.capture(candidates["tdd"])["fingerprint"] != workspace.capture(
        candidates["explore"]
    )["fingerprint"]
    assert "tdd.txt" not in workspace.capture(candidates["explore"])["patch"]
    assert "tdd.txt" not in workspace.capture(candidates["classic"])["patch"]
    assert not (candidates["explore"].path / "tdd.txt").exists()
    workspace.cleanup()


def test_workspace_reattaches_each_candidate_independently(tmp_path: Path):
    repo = initialized_repo(tmp_path)
    root = tmp_path / "worktrees"
    original = GitWorkspace(repo, "main", "run", root)
    base = original.prepare(require_remote=False)
    for name in ("tdd", "explore", "classic"):
        candidate = original.create_or_reattach(name, base)
        (candidate.path / f"{name}.txt").write_text(f"{name} work\n", encoding="utf-8")

    recovered = GitWorkspace(repo, "main", "run", root)
    assert recovered.prepare(require_remote=False) == base
    for name in ("explore", "tdd", "classic"):
        candidate = recovered.create_or_reattach(name, base, recover=True)
        assert (candidate.path / f"{name}.txt").read_text(encoding="utf-8") == f"{name} work\n"
        assert f"{name}.txt" in recovered.capture(candidate)["patch"]
    recovered.cleanup()


def test_capture_preserves_executable_mode_bits(tmp_path: Path):
    repo = initialized_repo(tmp_path)
    workspace = GitWorkspace(repo, "main", "run", tmp_path / "worktrees")
    base = workspace.prepare(require_remote=False)
    candidate = workspace.create_or_reattach("tdd", base)
    script = candidate.path / "run.sh"
    script.write_text("#!/usr/bin/env bash\necho ran\n", encoding="utf-8")

    before = workspace.capture(candidate)
    assert before["modes"]["run.sh"] == "100644"

    script.chmod(0o755)
    after = workspace.capture(candidate)

    assert after["modes"]["run.sh"] == "100755"
    assert after["fingerprint"] != before["fingerprint"]
    assert "100755" in after["patch"]
    workspace.cleanup()


def test_disposable_copy_and_delivery_preserve_executable_mode(tmp_path: Path):
    repo = initialized_repo(tmp_path)
    workspace = GitWorkspace(repo, "main", "run", tmp_path / "worktrees")
    base = workspace.prepare(require_remote=False)
    candidate = workspace.create_or_reattach("tdd", base)
    script = candidate.path / "run.sh"
    script.write_text("#!/usr/bin/env bash\necho ran\n", encoding="utf-8")
    script.chmod(0o755)

    copy = workspace.create_disposable_copy(candidate, tmp_path / "tester-copy")
    assert os.access(copy / "run.sh", os.X_OK)

    tree = workspace.capture(candidate)["tree"]
    commit = workspace.prepare_commit(candidate, "Executable delivery", expected_tree=tree)
    workspace.integrate(commit, base, push=False)

    delivered_mode = git(repo, "ls-tree", "HEAD", "run.sh").split()[0]
    assert delivered_mode == "100755"
    assert os.access(repo / "run.sh", os.X_OK)
    workspace.cleanup()


def test_export_revision_preserves_executable_mode(tmp_path: Path):
    repo = initialized_repo(tmp_path)
    git(repo, "commit", "--allow-empty", "-m", "still plain")
    (repo / "setup.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    os.chmod(repo / "setup.sh", 0o755)
    git(repo, "add", "setup.sh")
    git(repo, "commit", "-m", "add executable script")
    destination = tmp_path / "snapshot"

    export_revision(repo, destination)

    assert os.access(destination / "setup.sh", os.X_OK)
