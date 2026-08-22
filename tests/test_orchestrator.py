import json
import subprocess
from pathlib import Path

import pytest

from forge.agents import AgentConfigurationFailure, AgentRequest, AgentTimeout
from forge.models import AgentResult, ModelSpec, ROLE_NAMES, RunConfig, Usage
from forge.orchestrator import PROBE_PROMPT, ForgeOrchestrator


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, text=True, check=True, stdout=subprocess.PIPE
    ).stdout.strip()


class FakeRunner:
    def __init__(self):
        self.brain_calls = 0

    def run(self, request: AgentRequest) -> AgentResult:
        session = request.session_id or f"{request.role}-session"
        if request.role == "probe":
            assert request.access == "none"
            return AgentResult(
                text="ready",
                session_id=session,
                usage=Usage(input_tokens=1, output_tokens=1),
                elapsed_seconds=0.01,
                raw_output="ready",
            )
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
                    "borrow": [{"from": "classic", "what": "clearer greeting wording"}],
                    "candidates": {
                        "tdd": {"score": 95, "summary": "best", "strengths": [], "problems": []},
                        "explore": {"score": 80, "summary": "good", "strengths": [], "problems": []},
                        "classic": {"score": 75, "summary": "ok", "strengths": [], "problems": []},
                    },
                }
            )
        elif request.role == "whitebox":
            text = json.dumps(
                {
                    "summary": "Short checks passed",
                    "short": ["feature.txt exists"],
                    "long": [],
                    "red_flags": [],
                    "recommendation": "Continue the brief",
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
                    "happy_path": "exercised",
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
    models = {role: ModelSpec.parse("codex:gpt-5.6-luna:low") for role in ROLE_NAMES}
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
    assert batch["brain_report"]["winner"] == "tdd"
    assert batch["whitebox"]["recommendation"]
    assert batch["candidate_metrics"]["tdd"]["selected"] is True
    assert batch["candidate_metrics"]["tdd"]["total_tokens"] >= 15
    artifacts = repo / ".forge" / "runs" / "integration-run"
    assert (artifacts / "batches/001/candidates/tdd/candidate.patch").is_file()
    assert (artifacts / "batches/001/candidates/tdd/metrics.json").is_file()
    assert (artifacts / "batches/001/review.json").is_file()
    assert (artifacts / "batches/001/black-box.json").is_file()
    assert (artifacts / "batches/001/whitebox.json").is_file()
    assert (artifacts / "batches/001/brain-report.json").is_file()
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
    models = {role: ModelSpec.parse("codex:gpt-5.6-luna:low") for role in ROLE_NAMES}
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
    models = {role: ModelSpec.parse("codex:gpt-5.6-luna:low") for role in ROLE_NAMES}
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
    orchestrator.state.batches = [{"cycle": 0}]
    orchestrator.store.save_state(orchestrator.state)
    orchestrator.runner = CapturedReviewRecoveryRunner()

    recovered = orchestrator.recover_failed()

    assert recovered.status == "complete"
    assert recovered.cycle == 1
    assert recovered.batches[-1]["winner"] == "tdd"
    assert "hello from tdd" in (repo / "feature.txt").read_text(encoding="utf-8")
    assert git(repo, "status", "--porcelain") == ""


class BoomReviewer(FakeRunner):
    def __init__(self):
        super().__init__()
        self.reviews = 0

    def run(self, request: AgentRequest) -> AgentResult:
        if request.role == "reviewer":
            self.reviews += 1
            if self.reviews == 1:
                raise RuntimeError("reviewer exploded")
        return super().run(request)


def test_recover_continues_from_existing_candidates_after_reviewer_crash(tmp_path: Path):
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
    models = {role: ModelSpec.parse("codex:gpt-5.6-luna:low") for role in ROLE_NAMES}
    runner = BoomReviewer()
    first = ForgeOrchestrator(
        RunConfig(str(repo), str(brief), "main", models, push=False),
        run_id="recover-review",
        runner=runner,
        state_home=tmp_path / "state",
        check_binaries=False,
    )
    with pytest.raises(RuntimeError, match="reviewer exploded"):
        first.run()
    assert first.state.status == "failed"
    assert (tmp_path / "state" / "worktrees" / "recover-review" / "tdd").is_dir()

    recovered = ForgeOrchestrator.from_existing(
        repo,
        "recover-review",
        runner=runner,
        state_home=tmp_path / "state",
        check_binaries=False,
    )
    state = recovered.recover()
    assert state.status == "complete"
    assert "hello from tdd" in (repo / "feature.txt").read_text(encoding="utf-8")


class CrashAfterDelivery(ForgeOrchestrator):
    def _finish_delivered_batch(self, *args, **kwargs):
        raise RuntimeError("post-delivery crash")


def test_recover_skips_recommit_after_delivery(tmp_path: Path):
    repo = tmp_path / "target"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "forge@example.test")
    git(repo, "config", "user.name", "Forge Test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    first_sha = git(repo, "rev-parse", "HEAD")
    brief = tmp_path / "brief.md"
    brief.write_text("Build a greeting feature.\n", encoding="utf-8")
    models = {role: ModelSpec.parse("codex:gpt-5.6-luna:low") for role in ROLE_NAMES}
    runner = FakeRunner()
    first = CrashAfterDelivery(
        RunConfig(str(repo), str(brief), "main", models, push=False),
        run_id="recover-delivery",
        runner=runner,
        state_home=tmp_path / "state",
        check_binaries=False,
    )
    with pytest.raises(RuntimeError, match="post-delivery crash"):
        first.run()
    delivered = git(repo, "rev-parse", "HEAD")
    assert delivered != first_sha
    assert (
        repo / ".forge" / "runs" / "recover-delivery" / "batches" / "001" / "delivery.json"
    ).is_file()

    recovered = ForgeOrchestrator.from_existing(
        repo,
        "recover-delivery",
        runner=runner,
        state_home=tmp_path / "state",
        check_binaries=False,
    )
    state = recovered.recover()
    assert state.status == "complete"
    assert git(repo, "rev-parse", "HEAD") == delivered
    assert "hello from tdd" in (repo / "feature.txt").read_text(encoding="utf-8")


def test_recover_rejects_a_completed_run(tmp_path: Path):
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
    models = {role: ModelSpec.parse("codex:gpt-5.6-luna:low") for role in ROLE_NAMES}
    orchestrator = ForgeOrchestrator(
        RunConfig(str(repo), str(brief), "main", models, push=False),
        run_id="complete-then-recover",
        runner=FakeRunner(),
        state_home=tmp_path / "state",
        check_binaries=False,
    )
    orchestrator.run()
    with pytest.raises(RuntimeError, match="only failed or paused"):
        orchestrator.recover()


def test_mark_interrupted_clears_live_agents(tmp_path: Path):
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
    models = {role: ModelSpec.parse("codex:gpt-5.6-luna:low") for role in ROLE_NAMES}
    orchestrator = ForgeOrchestrator(
        RunConfig(str(repo), str(brief), "main", models, push=False),
        run_id="interrupt-run",
        runner=FakeRunner(),
        state_home=tmp_path / "state",
        check_binaries=False,
    )
    orchestrator.state.status = "running"
    orchestrator.state.active_agents["coder_tdd"] = {"role": "coder_tdd"}
    orchestrator.mark_interrupted("Controller restarted.")
    assert orchestrator.state.status == "failed"
    assert orchestrator.state.active_agents == {}
    assert "Controller restarted" in orchestrator.state.message


def test_recover_failed_clears_stale_active_agents(tmp_path: Path):
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
    models = {role: ModelSpec.parse("codex:gpt-5.6-luna:low") for role in ROLE_NAMES}
    orchestrator = ForgeOrchestrator(
        RunConfig(str(repo), str(brief), "main", models, push=False),
        run_id="stale-agents",
        runner=FakeRunner(),
        state_home=tmp_path / "state",
        check_binaries=False,
    )
    orchestrator.run()
    orchestrator.state.status = "running"
    orchestrator.state.active_agents["coder_tdd"] = {"role": "coder_tdd"}
    orchestrator.store.save_state(orchestrator.state)
    orchestrator.runner = FinishRecoveryRunner()
    recovered = orchestrator.recover_failed()
    assert recovered.status == "complete"
    assert recovered.active_agents == {}


class RecordingRunner(FakeRunner):
    def __init__(self):
        super().__init__()
        self.requests: list[AgentRequest] = []

    def run(self, request: AgentRequest) -> AgentResult:
        self.requests.append(request)
        return super().run(request)


def _repo_with_brief(tmp_path: Path) -> tuple[Path, Path]:
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
    return repo, brief


def test_first_start_probes_each_unique_model_without_tools(tmp_path: Path):
    repo, brief = _repo_with_brief(tmp_path)
    models = {
        "brain": ModelSpec.parse("codex:gpt-5.6-sol:high"),
        "planner": ModelSpec.parse("codex:gpt-5.6-sol:high"),
        "coder_tdd": ModelSpec.parse("codex:gpt-5.6-luna:high"),
        "coder_explore": ModelSpec.parse("codex:gpt-5.6-luna:high"),
        "coder_classic": ModelSpec.parse("opencode:grok-4.6"),
        "reviewer": ModelSpec.parse("codex:gpt-5.6-terra:high"),
        "tester": ModelSpec.parse("codex:gpt-5.6-terra:high"),
        "whitebox": ModelSpec.parse("codex:gpt-5.6-terra:high"),
    }
    runner = RecordingRunner()
    orchestrator = ForgeOrchestrator(
        RunConfig(str(repo), str(brief), "main", models, push=False),
        run_id="probe-unique",
        runner=runner,
        state_home=tmp_path / "state",
        check_binaries=False,
    )
    state = orchestrator.run()
    assert state.status == "complete"
    probes = [request for request in runner.requests if request.role == "probe"]
    assert {request.model.display() for request in probes} == {
        "codex:gpt-5.6-sol:high",
        "codex:gpt-5.6-luna:high",
        "opencode:xai/grok-4.6",
        "codex:gpt-5.6-terra:high",
    }
    assert len(probes) == 4
    assert all(request.access == "none" for request in probes)
    assert all(request.prompt == PROBE_PROMPT for request in probes)
    assert (repo / ".forge" / "runs" / "probe-unique" / "preflight").is_dir()


def test_failed_model_probe_stops_before_brain(tmp_path: Path):
    repo, brief = _repo_with_brief(tmp_path)
    models = {role: ModelSpec.parse("codex:gpt-5.6-sol:high") for role in ROLE_NAMES}

    class FailingProbe(RecordingRunner):
        def run(self, request: AgentRequest) -> AgentResult:
            if request.role == "probe":
                self.requests.append(request)
                raise AgentConfigurationFailure("usage limit", raw_output="quota")
            return super().run(request)

    runner = FailingProbe()
    orchestrator = ForgeOrchestrator(
        RunConfig(str(repo), str(brief), "main", models, push=False),
        run_id="probe-fail",
        runner=runner,
        state_home=tmp_path / "state",
        check_binaries=False,
    )
    with pytest.raises(ValueError, match="model preflight failed"):
        orchestrator.run()
    assert runner.brain_calls == 0
    assert orchestrator.state.status == "failed"
    assert not any(request.role == "brain" for request in runner.requests)
