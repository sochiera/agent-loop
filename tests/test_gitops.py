import subprocess
from pathlib import Path

from forge.gitops import GitCompetition


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
    sha = competition.commit_and_deliver(candidates["tdd"], "winner", push=False)
    assert git(repo, "rev-parse", "HEAD") == sha
    assert git(repo, "rev-parse", "HEAD~1") == base
    assert (repo / "feature.txt").read_text(encoding="utf-8") == "winner\n"
    competition.cleanup()
    assert not (tmp_path / "worktrees" / "tdd").exists()
