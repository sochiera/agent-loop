import subprocess
from pathlib import Path

from forge.gitops import GitCompetition, list_branches


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


def test_competition_fast_forwards_selected_branch(tmp_path: Path):
    repo = initialized_repo(tmp_path)
    competition = GitCompetition(repo, "main", "run", tmp_path / "worktrees")
    base = competition.prepare(require_remote=False)
    candidates = competition.create_candidates()
    (candidates["tdd"].path / "feature.txt").write_text("winner\n", encoding="utf-8")
    captured = competition.capture(candidates["tdd"])
    assert "feature.txt" in captured["patch"]
    assert "feature.txt" in captured["review_patch"]
    sha = competition.commit_and_deliver(candidates["tdd"], "winner", push=False)
    assert git(repo, "rev-parse", "HEAD") == sha
    assert git(repo, "rev-parse", "HEAD~1") == base
    assert (repo / "feature.txt").read_text(encoding="utf-8") == "winner\n"
    competition.cleanup()
    assert not (tmp_path / "worktrees" / "tdd").exists()


def test_competition_restores_candidates_from_captured_binary_patches(tmp_path: Path):
    repo = initialized_repo(tmp_path)
    root = tmp_path / "worktrees"
    original = GitCompetition(repo, "main", "run", root)
    original.prepare(require_remote=False)
    candidates = original.create_candidates()
    (candidates["tdd"].path / "feature.txt").write_text("restored\n", encoding="utf-8")
    (candidates["classic"].path / "asset.bin").write_bytes(bytes(range(256)))
    patches = {name: original.capture(candidate)["patch"] for name, candidate in candidates.items()}
    original.cleanup()

    recovered = GitCompetition(repo, "main", "run", root)
    recovered.prepare(require_remote=False)
    restored = recovered.restore_candidates(patches)

    assert (restored["tdd"].path / "feature.txt").read_text(encoding="utf-8") == "restored\n"
    assert (restored["classic"].path / "asset.bin").read_bytes() == bytes(range(256))
    recovered.cleanup()


def test_prepare_bootstraps_unborn_branch_and_excludes_brief(tmp_path: Path):
    repo = tmp_path / "empty"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "forge@example.test")
    git(repo, "config", "user.name", "Forge Test")
    (repo / "goal.md").write_text("Build something.\n", encoding="utf-8")
    assert list_branches(repo) == ["main"]

    competition = GitCompetition(
        repo,
        "main",
        "run",
        tmp_path / "worktrees",
        local_excludes=("/goal.md",),
    )
    base = competition.prepare(require_remote=False)

    assert git(repo, "rev-parse", "HEAD") == base
    assert git(repo, "branch", "--show-current") == "main"
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "log", "-1", "--pretty=%s") == "Initialize repository for Forge"


def test_capture_excludes_generated_dependency_trees(tmp_path: Path):
    repo = initialized_repo(tmp_path)
    competition = GitCompetition(repo, "main", "run", tmp_path / "worktrees")
    competition.prepare(require_remote=False)
    candidate = competition.create_candidates()["tdd"]
    (candidate.path / "node_modules/pkg").mkdir(parents=True)
    (candidate.path / "node_modules/pkg/index.js").write_text("generated\n", encoding="utf-8")
    (candidate.path / "feature.js").write_text("product\n", encoding="utf-8")

    captured = competition.capture(candidate)

    assert "feature.js" in captured["patch"]
    assert "node_modules" not in captured["patch"]
    assert "node_modules" not in captured["status"]
    competition.cleanup()


def test_reattach_and_restore_from_patches(tmp_path: Path):
    repo = initialized_repo(tmp_path)
    root = tmp_path / "worktrees"
    competition = GitCompetition(repo, "main", "run", root)
    competition.prepare(require_remote=False)
    candidates = competition.create_candidates()
    (candidates["explore"].path / "note.txt").write_text("kept\n", encoding="utf-8")
    captured = competition.capture(candidates["explore"])
    patch = tmp_path / "explore.patch"
    patch.write_text(captured["patch"], encoding="utf-8")
    attached = GitCompetition(repo, "main", "run", root)
    attached.prepare(require_remote=False)
    again = attached.reattach_candidates()
    assert (again["explore"].path / "note.txt").read_text(encoding="utf-8") == "kept\n"
    attached.cleanup()
    restored = GitCompetition(repo, "main", "run-2", tmp_path / "restored")
    restored.prepare(require_remote=False)
    trees, warnings = restored.restore_from_patches({"explore": patch})
    assert warnings == []
    assert (trees["explore"].path / "note.txt").read_text(encoding="utf-8") == "kept\n"
    restored.cleanup()
