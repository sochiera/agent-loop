import json
import subprocess
from pathlib import Path

import pytest

from forge.agents import AgentConfigurationFailure, AgentRequest, AgentTimeout
from forge.models import AgentResult, ModelSpec, ROLE_NAMES, RunConfig, Usage
from forge.orchestrator import ForgeOrchestrator


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, text=True, check=True, stdout=subprocess.PIPE
    ).stdout.strip()


class FakeRunner:
    def __init__(self):
        self.brain_calls = 0

    def run(self, request: AgentRequest) -> AgentResult:
        session = request.session_id or f"{request.role}-session"
        if request.role == "brain":
            self.brain_calls += 1
            if self.brain_calls == 1:
                text = json.dumps(
                    {
                        "tool": "forge.run_batch",
                        "reason": "build the feature",
                        "objective": "Deliver the greeting feature",
                        "success_criteria": ["feature.txt contains a greeting"],
                    }
                )
            else:
                text = json.dumps(
                    {
                        "tool": "forge.finish",
                        "reason": "black-box evidence proves the brief",
                        "summary": "Greeting delivered.",
                    }
                )
        elif request.role == "planner":
            text = """# Batch plan
## Intent
Deliver an observable greeting.
## Tasks
- [ ] TASK-001: Add the greeting artifact.
- [ ] TASK-002: Verify the greeting artifact.
## Validation commands
- `test -s feature.txt`
"""
        elif request.role.startswith("coder_"):
            plan = request.cwd / ".forge" / "plan.md"
            if "selected your implementation" in request.prompt:
                with (request.cwd / "feature.txt").open("a", encoding="utf-8") as handle:
                    handle.write("reviewed\n")
                text = "Applied reviewer feedback."
            else:
                candidate = request.role.removeprefix("coder_")
                (request.cwd / "feature.txt").write_text(
                    f"hello from {candidate}\n", encoding="utf-8"
                )
                plan.write_text(
                    plan.read_text(encoding="utf-8").replace("[ ]", "[x]"),
                    encoding="utf-8",
                )
                text = "Implemented all tasks and validation passes."
        elif request.role == "reviewer":
            text = json.dumps(
                {
                    "winner": "tdd",
                    "reason": "best tests and behavior",
                    "feedback": ["Add a reviewed marker"],
                    "candidates": {
                        "tdd": {"score": 95, "summary": "best", "strengths": [], "problems": []},
                        "explore": {"score": 80, "summary": "good", "strengths": [], "problems": []},
                        "classic": {"score": 75, "summary": "ok", "strengths": [], "problems": []},
                    },
                }
            )
        elif request.role == "tester":
            assert "hello from tdd" in (request.cwd / "feature.txt").read_text(encoding="utf-8")
            text = json.dumps(
                {
                    "summary": "Greeting works",
                    "working": ["greeting"],
                    "missing": [],
                    "observations": ["reviewed"],
                    "evidence": ["feature.txt public output"],
                }
            )
        else:
            raise AssertionError(request.role)
        return AgentResult(
            text=text,
            session_id=session,
            usage=Usage(input_tokens=10, output_tokens=5),
            elapsed_seconds=0.01,
            raw_output=text,
        )


class MissingBrainSessionRunner(FakeRunner):
    def run(self, request: AgentRequest) -> AgentResult:
        result = super().run(request)
        if request.role == "brain":
            result.session_id = None
        return result


class FinishRecoveryRunner:
    def __init__(self):
        self.session_id = ""

    def run(self, request: AgentRequest) -> AgentResult:
        assert request.role == "brain"
        self.session_id = request.session_id or ""
        text = json.dumps(
            {
                "tool": "forge.finish",
                "reason": "the delivered batch satisfies the brief",
                "objective": "",
                "success_criteria": [],
                "summary": "Recovered with the original brain.",
            }
        )
        return AgentResult(
            text=text,
            session_id=request.session_id,
            usage=Usage(input_tokens=3, output_tokens=2),
            elapsed_seconds=0.01,
            raw_output=text,
        )


class TimeoutRunner:
    def __init__(self):
        self.calls = 0

    def run(self, request: AgentRequest) -> AgentResult:
        self.calls += 1
        raise AgentTimeout("deliberate timeout", raw_output="timed out")


class ReviewFailureRunner(FakeRunner):
    def run(self, request: AgentRequest) -> AgentResult:
        if request.role == "reviewer":
            raise AgentConfigurationFailure("review unavailable", raw_output="usage limit")
        return super().run(request)


class CapturedReviewRecoveryRunner(FakeRunner):
    def run(self, request: AgentRequest) -> AgentResult:
        if request.role == "brain":
            text = json.dumps(
                {
                    "tool": "forge.finish",
                    "reason": "recovered batch satisfies the brief",
                    "objective": "",
                    "success_criteria": [],
                    "summary": "Captured review recovered.",
                }
            )
            return AgentResult(
                text=text,
                session_id=request.session_id,
                usage=Usage(input_tokens=3, output_tokens=2),
                elapsed_seconds=0.01,
                raw_output=text,
            )
        return super().run(request)


def test_full_competition_flow_without_model_calls(tmp_path: Path):
    repo = tmp_path / "target"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "forge@example.test")
    git(repo, "config", "user.name", "Forge Test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    brief = tmp_path / "brief.md"
    brief.write_text("Build a greeting feature.\n", encoding="utf-8")
    models = {role: ModelSpec("codex", "fake", "low") for role in ROLE_NAMES}
    config = RunConfig(str(repo), str(brief), "main", models, push=False)
    orchestrator = ForgeOrchestrator(
        config,
        run_id="integration-run",
        runner=FakeRunner(),
        state_home=tmp_path / "state",
        check_binaries=False,
    )
    state = orchestrator.run()
    assert state.status == "complete"
    assert state.cycle == 1
    assert "hello from tdd" in (repo / "feature.txt").read_text(encoding="utf-8")
    assert "reviewed" in (repo / "feature.txt").read_text(encoding="utf-8")
    batch = state.batches[0]
    assert batch["winner"] == "tdd"
    assert batch["candidate_metrics"]["tdd"]["selected"] is True
    assert batch["candidate_metrics"]["tdd"]["total_tokens"] >= 15
    artifacts = repo / ".forge" / "runs" / "integration-run"
    assert (artifacts / "batches/001/candidates/tdd/candidate.patch").is_file()
    assert (artifacts / "batches/001/candidates/tdd/metrics.json").is_file()
    assert (artifacts / "batches/001/review.json").is_file()
    assert (artifacts / "batches/001/black-box.json").is_file()
    assert (artifacts / "usage.jsonl").is_file()
    assert git(repo, "status", "--porcelain") == ""

    # Simulate an interrupt after the next batch number was reserved during
    # planning but before anything reached main.
    orchestrator.state.status = "running"
    orchestrator.state.cycle = 2
    orchestrator.store.save_state(orchestrator.state)
    recovery_runner = FinishRecoveryRunner()
    orchestrator.runner = recovery_runner
    recovered = orchestrator.recover_failed()
    assert recovered.status == "complete"
    assert recovered.cycle == 1
    assert recovery_runner.session_id == "brain-session"

    timeout_runner = TimeoutRunner()
    orchestrator.runner = timeout_runner
    with pytest.raises(AgentTimeout):
        orchestrator._invoke(
            role="coder_tdd",
            model=models["coder_tdd"],
            prompt="continue",
            cwd=repo,
            relative="timeout-check",
            candidate="tdd",
        )
    assert timeout_runner.calls == 1
    assert orchestrator.state.active_agents == {}


def test_brain_must_return_a_resumable_session(tmp_path: Path):
    repo = tmp_path / "target"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "forge@example.test")
    git(repo, "config", "user.name", "Forge Test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    brief = tmp_path / "brief.md"
    brief.write_text("Build a greeting feature.\n", encoding="utf-8")
    models = {role: ModelSpec("codex", "fake", "low") for role in ROLE_NAMES}
    orchestrator = ForgeOrchestrator(
        RunConfig(str(repo), str(brief), "main", models, push=False),
        run_id="missing-brain-session",
        runner=MissingBrainSessionRunner(),
        state_home=tmp_path / "state",
        check_binaries=False,
    )

    with pytest.raises(RuntimeError, match="resumable session id"):
        orchestrator.run()

    assert orchestrator.state.status == "failed"


def test_recovery_restores_captured_candidates_and_resumes_review(tmp_path: Path):
    repo = tmp_path / "target"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "forge@example.test")
    git(repo, "config", "user.name", "Forge Test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    brief = tmp_path / "brief.md"
    brief.write_text("Build a greeting feature.\n", encoding="utf-8")
    models = {role: ModelSpec("codex", "fake", "low") for role in ROLE_NAMES}
    orchestrator = ForgeOrchestrator(
        RunConfig(str(repo), str(brief), "main", models, push=False),
        run_id="captured-review",
        runner=ReviewFailureRunner(),
        state_home=tmp_path / "state",
        check_binaries=False,
    )

    with pytest.raises(AgentConfigurationFailure):
        orchestrator.run()

    artifacts = repo / ".forge" / "runs" / "captured-review" / "batches" / "001"
    assert (artifacts / "review-bundle.json").is_file()
    assert not (tmp_path / "state" / "worktrees" / "captured-review" / "tdd").exists()
    orchestrator.state.batches = [{"cycle": 0}]
    orchestrator.store.save_state(orchestrator.state)
    orchestrator.runner = CapturedReviewRecoveryRunner()

    recovered = orchestrator.recover_failed()

    assert recovered.status == "complete"
    assert recovered.cycle == 1
    assert recovered.batches[-1]["winner"] == "tdd"
    assert "hello from tdd" in (repo / "feature.txt").read_text(encoding="utf-8")
    assert git(repo, "status", "--porcelain") == ""
