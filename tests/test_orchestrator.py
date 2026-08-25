import json
import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from forge.agents import AgentCancelled, AgentConfigurationFailure, AgentRequest
from forge.models import AgentResult, ModelSpec, ROLE_NAMES, RunConfig, RunState, Usage
from forge.gitops import GitWorkspace
from forge.locking import ExecutionLocked, RepositoryExecutionLock
from forge.orchestrator import (
    EVENT_QUEUE_LIMIT,
    ForgeOrchestrator,
    IterationStalled,
    _product_owner_retry_prompt,
)
from forge.sprint import SPRINT_SCHEDULE


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, text=True, check=True, stdout=subprocess.PIPE
    ).stdout.strip()


def repo_and_brief(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "target"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "forge@example.test")
    git(repo, "config", "user.name", "Forge Test")
    (repo / "README.md").write_text("# Product\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    brief = tmp_path / "brief.md"
    brief.write_text("Build a useful product continuously.\n", encoding="utf-8")
    return repo, brief


def config(repo: Path, brief: Path, **changes) -> RunConfig:
    models = {role: ModelSpec.parse("codex:gpt-5.6-luna:low") for role in ROLE_NAMES}
    return RunConfig(str(repo), str(brief), "main", models, push=False, **changes)


def result(request: AgentRequest, text: str, *, tools: int = 0) -> AgentResult:
    return AgentResult(
        text=text,
        session_id=request.session_id or f"{request.role}-session",
        usage=Usage(input_tokens=10, output_tokens=5),
        elapsed_seconds=0.01,
        raw_output=text,
        tool_calls=tools,
    )


def backlog() -> list[dict]:
    stories = []
    for kind, count in (("feature", 8), ("cleanup", 4)):
        prefix = "F" if kind == "feature" else "C"
        for index in range(1, count + 1):
            story_id = f"{prefix}{index:02d}"
            stories.append(
                {
                    "id": story_id,
                    "kind": kind,
                    "title": f"{kind.title()} {index}",
                    "user_story": f"As a user I want {story_id} so that the product improves.",
                    "acceptance_criteria": [f"{story_id} is observable"],
                    "priority": index,
                    "estimated_minutes": 10,
                }
            )
    return stories


def product_owner_json() -> str:
    return json.dumps(
        {
            "assessment": {
                "summary": "The product was launched and inspected.",
                "working": ["base entry point"],
                "problems": ["the backlog remains substantial"],
                "evidence": ["README and public smoke"],
            },
            "sprint_goal": "Deliver a balanced product sprint.",
            "stories": backlog(),
            "retired_story_ids": [],
        }
    )


def fingerprint(prompt: str) -> str:
    matches = re.findall(r"\b[0-9a-f]{64}\b", prompt)
    assert matches
    return matches[0]


def story_from_prompt(prompt: str) -> str:
    match = re.search(r'"story_id":\s*"([FC]\d+)"', prompt)
    assert match, prompt
    return match.group(1)


def test_product_owner_retry_prompt_requires_missing_inspection() -> None:
    missing = _product_owner_retry_prompt(ValueError("no tools"), inspected=False)
    corrected = _product_owner_retry_prompt(ValueError("bad JSON"), inspected=True)

    assert "no product inspection tool call" in missing
    assert "Inspect and exercise the product with tools now" in missing
    assert "inspection remains valid" not in missing
    assert "inspection remains valid" in corrected


class SprintRunner:
    def __init__(self, *, stop_after: int = 10):
        self.stop_after = stop_after
        self.product_owner_calls = 0
        self.accepted = 0
        self.feature_index = 0
        self.cleanup_index = 0
        self.requests: list[AgentRequest] = []
        self.last_story = ""
        self.last_fingerprint = ""

    def run(self, request: AgentRequest) -> AgentResult:
        self.requests.append(request)
        if request.role == "probe":
            return result(request, "ready")
        if request.role == "brain":
            self.product_owner_calls += 1
            if self.product_owner_calls > 1:
                raise AgentCancelled("stop after one complete sprint")
            assert request.access == "inspect"
            assert not (request.cwd / ".git").exists()
            return result(request, product_owner_json(), tools=3)
        if request.role == "planner":
            if self.accepted >= self.stop_after:
                raise AgentCancelled("requested test boundary reached")
            kind = re.search(r"REQUIRED ITERATION TYPE: (feature|cleanup)", request.prompt)
            assert kind
            if kind.group(1) == "feature":
                self.feature_index += 1
                story = f"F{self.feature_index:02d}"
            else:
                self.cleanup_index += 1
                story = f"C{self.cleanup_index:02d}"
            text = json.dumps(
                {
                    "story_id": story,
                    "objective": f"Deliver {story}",
                    "tasks": [
                        {
                            "id": "TASK-001",
                            "title": f"Implement {story}",
                            "description": f"Create an observable {story} artifact.",
                            "acceptance_criteria": [
                                f"{story} is observable",
                                f"{story.lower()}.txt exists",
                            ],
                        }
                    ],
                    "validation_commands": [f"test -s {story.lower()}.txt"],
                    "public_checks": [f"Read {story.lower()}.txt through the public workspace"],
                    "addressed_nit_ids": [],
                }
            )
            return result(request, text)
        if request.role == "coder":
            story = story_from_prompt(request.prompt)
            self.last_story = story
            path = request.cwd / f"{story.lower()}.txt"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"implemented {story}\n")
            return result(request, f"Implemented {story} and ran its focused check.")
        if request.role == "reviewer":
            match = re.search(r'"story_id":\s*"([FC]\d+)"', request.prompt)
            story = match.group(1) if match else self.last_story
            self.last_story = story
            hashes = re.findall(r"\b[0-9a-f]{64}\b", request.prompt)
            if hashes:
                self.last_fingerprint = hashes[0]
            assert (request.cwd / f"{story.lower()}.txt").is_file()
            text = json.dumps(
                {
                    "verdict": "accept",
                    "summary": "All planned behavior is present.",
                    "implementation_fingerprint": self.last_fingerprint,
                    "task_results": [
                        {"task_id": "TASK-001", "verdict": "accept", "evidence": ["artifact exists"]}
                    ],
                    "blocking_findings": [],
                    "nits": [],
                    "blocker": "",
                }
            )
            return result(request, text)
        if request.role == "tester":
            match = re.search(r'"story_id":\s*"([FC]\d+)"', request.prompt)
            story = match.group(1) if match else self.last_story
            hashes = re.findall(r"\b[0-9a-f]{64}\b", request.prompt)
            if hashes:
                self.last_fingerprint = hashes[0]
            assert (request.cwd / f"{story.lower()}.txt").is_file()
            self.accepted += 1
            text = json.dumps(
                {
                    "verdict": "accept",
                    "summary": "Automated and public checks pass.",
                    "implementation_fingerprint": self.last_fingerprint,
                    "task_results": [
                        {"task_id": "TASK-001", "verdict": "accept", "evidence": ["public check"]}
                    ],
                    "whitebox": {"summary": "green", "checks": ["validation passed"], "observations": []},
                    "blackbox": {
                        "summary": "happy path works",
                        "happy_path": "exercised",
                        "scenarios": [f"Read {story.lower()}.txt through the public workspace"],
                        "evidence": [f"{story.lower()}.txt"],
                        "observations": [],
                    },
                    "blocking_findings": [],
                    "nits": [],
                    "blocker": "",
                }
            )
            return result(request, text)
        raise AssertionError(request.role)


def make_orchestrator(tmp_path: Path, runner, **config_changes) -> ForgeOrchestrator:
    repo, brief = repo_and_brief(tmp_path)
    return ForgeOrchestrator(
        config(repo, brief, **config_changes),
        run_id="sprint-run",
        runner=runner,
        state_home=tmp_path / "state",
        check_binaries=False,
    )


def test_full_sprint_uses_fixed_schedule_and_returns_to_fresh_product_owner(tmp_path: Path):
    runner = SprintRunner()
    orchestrator = make_orchestrator(tmp_path, runner)

    state = orchestrator.run()

    assert state.status == "cancelled"
    assert state.cycle == 10
    assert [item["kind"] for item in state.iterations] == list(SPRINT_SCHEDULE)
    assert len(state.completed_sprints) == 1
    assert state.needs_product_owner is True
    assert state.sprint_iteration == 0
    assert runner.product_owner_calls == 2
    assert git(orchestrator.repo, "rev-list", "--count", "HEAD") == "11"
    assert git(orchestrator.repo, "status", "--porcelain") == ""

    roles = [request.role for request in runner.requests if request.role != "probe"]
    assert roles[0] == "brain"
    assert roles[-1] == "brain"
    assert roles[1:-1] == [role for _ in range(10) for role in ("planner", "coder", "reviewer", "tester")]
    for role in ("planner", "coder", "reviewer", "tester"):
        first_calls = [request for request in runner.requests if request.role == role]
        assert first_calls
        assert all(request.session_id is None for request in first_calls)


class ProductOwnerCorrectionRunner(SprintRunner):
    def __init__(self):
        super().__init__(stop_after=0)

    def run(self, request: AgentRequest) -> AgentResult:
        if request.role == "brain":
            self.requests.append(request)
            self.product_owner_calls += 1
            if self.product_owner_calls == 1:
                return result(request, '{"assessment":"invalid"}', tools=2)
            return result(request, product_owner_json(), tools=0)
        return super().run(request)


def test_product_owner_tool_inspection_survives_json_contract_correction(tmp_path: Path):
    runner = ProductOwnerCorrectionRunner()
    orchestrator = make_orchestrator(tmp_path, runner)

    state = orchestrator.run()

    assert state.status == "cancelled"
    assert state.backlog_revision == 1
    assert state.sprint_goal == "Deliver a balanced product sprint."
    assert state.product_owner_inspected is False
    assert runner.product_owner_calls == 2


class ReviewRejectRunner(SprintRunner):
    def __init__(self):
        super().__init__(stop_after=1)
        self.review_calls = 0

    def run(self, request: AgentRequest) -> AgentResult:
        if request.role == "reviewer":
            self.requests.append(request)
            self.review_calls += 1
            rejected = self.review_calls == 1
            text = json.dumps(
                {
                    "verdict": "reject" if rejected else "accept",
                    "summary": "A serious gap remains." if rejected else "The gap is fixed.",
                    "implementation_fingerprint": fingerprint(request.prompt),
                    "task_results": [
                        {
                            "task_id": "TASK-001",
                            "verdict": "reject" if rejected else "accept",
                            "evidence": ["first review" if rejected else "fixed artifact"],
                        }
                    ],
                    "blocking_findings": (
                        [
                            {
                                "id": "REV-001",
                                "summary": "Missing reviewed marker",
                                "evidence": "f01.txt",
                                "suggested_fix": "Append a reviewed marker",
                                "task_ids": ["TASK-001"],
                            }
                        ]
                        if rejected
                        else []
                    ),
                    "nits": ["A tiny naming preference"] if rejected else [],
                    "blocker": "",
                }
            )
            return result(request, text)
        return super().run(request)


def test_reviewer_rejection_returns_to_same_coder_context_and_nits_do_not_block(tmp_path: Path):
    runner = ReviewRejectRunner()
    orchestrator = make_orchestrator(tmp_path, runner)

    state = orchestrator.run()

    assert state.status == "cancelled"
    assert state.cycle == 1
    record = state.iterations[0]
    assert record["coder_rounds"] == 2
    assert record["review_rounds"] == 2
    assert record["tester_rounds"] == 1
    coder_requests = [item for item in runner.requests if item.role == "coder"]
    assert coder_requests[0].session_id is None
    assert coder_requests[1].session_id == "coder-session"
    assert len([item for item in runner.requests if item.role == "tester"]) == 1
    assert any(item["text"] == "A tiny naming preference" for item in state.quality_backlog)


class QARejectRunner(SprintRunner):
    def __init__(self):
        super().__init__(stop_after=1)
        self.test_calls = 0

    def run(self, request: AgentRequest) -> AgentResult:
        if request.role == "tester":
            self.requests.append(request)
            self.test_calls += 1
            rejected = self.test_calls == 1
            if not rejected:
                self.accepted += 1
            text = json.dumps(
                {
                    "verdict": "reject" if rejected else "accept",
                    "summary": "Public failure" if rejected else "Retest passed",
                    "implementation_fingerprint": fingerprint(request.prompt),
                    "task_results": [
                        {
                            "task_id": "TASK-001",
                            "verdict": "reject" if rejected else "accept",
                            "evidence": ["public scenario"],
                        }
                    ],
                    "whitebox": {"summary": "green", "checks": ["validation"], "observations": []},
                    "blackbox": {
                        "summary": "failed" if rejected else "works",
                        "happy_path": "missing" if rejected else "exercised",
                        "scenarios": [f"Read {self.last_story.lower()}.txt through the public workspace"],
                        "evidence": ["public scenario output"] if not rejected else [],
                        "observations": [],
                    },
                    "blocking_findings": (
                        [
                            {
                                "id": "TEST-001",
                                "summary": "Public output is incomplete",
                                "evidence": "scenario output",
                                "suggested_fix": "Complete the output",
                                "task_ids": ["TASK-001"],
                            }
                        ]
                        if rejected
                        else []
                    ),
                    "nits": [],
                    "blocker": "",
                }
            )
            return result(request, text)
        return super().run(request)


def test_tester_rejection_requires_coder_and_reviewer_before_retest(tmp_path: Path):
    runner = QARejectRunner()
    orchestrator = make_orchestrator(tmp_path, runner)

    state = orchestrator.run()

    assert state.status == "cancelled"
    roles = [request.role for request in runner.requests if request.role != "probe"]
    assert roles[:8] == [
        "brain",
        "planner",
        "coder",
        "reviewer",
        "tester",
        "coder",
        "reviewer",
        "tester",
    ]
    assert state.iterations[0]["coder_rounds"] == 2
    assert state.iterations[0]["review_rounds"] == 2
    assert state.iterations[0]["tester_rounds"] == 2


class NoProgressRunner(SprintRunner):
    def run(self, request: AgentRequest) -> AgentResult:
        if request.role == "coder":
            self.requests.append(request)
            return result(request, "No changes were necessary.")
        if request.role == "reviewer":
            self.requests.append(request)
            text = json.dumps(
                {
                    "verdict": "reject",
                    "summary": "The task is not implemented.",
                    "implementation_fingerprint": fingerprint(request.prompt),
                    "task_results": [
                        {"task_id": "TASK-001", "verdict": "reject", "evidence": ["missing artifact"]}
                    ],
                    "blocking_findings": [
                        {
                            "id": "REV-NO-PROGRESS",
                            "summary": "No implementation exists",
                            "evidence": "missing f01.txt",
                            "suggested_fix": "Implement the planned artifact",
                            "task_ids": ["TASK-001"],
                        }
                    ],
                    "nits": [],
                    "blocker": "",
                }
            )
            return result(request, text)
        return super().run(request)


def test_repeated_unchanged_coder_rounds_stall_without_delivery(tmp_path: Path):
    runner = NoProgressRunner(stop_after=1)
    orchestrator = make_orchestrator(tmp_path, runner, stalled_turns=2)
    base = git(orchestrator.repo, "rev-parse", "HEAD")

    state = orchestrator.run()

    assert state.status == "stalled"
    assert state.cycle == 0
    assert state.active_iteration["phase"] == "coding"
    assert git(orchestrator.repo, "rev-parse", "HEAD") == base
    assert "no workspace progress" in state.message
    assert state.stalled_recoverable is False
    with pytest.raises(RuntimeError, match="deterministic safety limit"):
        orchestrator.recover()


class ExternallyBlockedReviewRunner(SprintRunner):
    def __init__(self):
        super().__init__(stop_after=1)
        self.blocked_once = False

    def run(self, request: AgentRequest) -> AgentResult:
        if request.role == "reviewer" and not self.blocked_once:
            self.blocked_once = True
            self.requests.append(request)
            return result(
                request,
                json.dumps(
                    {
                        "verdict": "blocked",
                        "summary": "The external preview service is unavailable.",
                        "implementation_fingerprint": fingerprint(request.prompt),
                        "task_results": [
                            {
                                "task_id": "TASK-001",
                                "verdict": "accept",
                                "evidence": ["implementation inspected"],
                            }
                        ],
                        "blocking_findings": [],
                        "nits": [],
                        "blocker": "Preview service outage",
                    }
                ),
            )
        return super().run(request)


def test_external_review_blocker_can_recover_without_consuming_round(tmp_path: Path):
    runner = ExternallyBlockedReviewRunner()
    orchestrator = make_orchestrator(tmp_path, runner)

    stalled = orchestrator.run()

    assert stalled.status == "stalled"
    assert stalled.stalled_recoverable is True
    assert stalled.active_iteration["phase"] == "review"
    assert stalled.active_iteration["review_round"] == 0

    recovered = orchestrator.recover()

    assert recovered.status == "cancelled"
    assert recovered.cycle == 1


class ReviewCrashRunner(SprintRunner):
    def run(self, request: AgentRequest) -> AgentResult:
        if request.role == "reviewer":
            self.requests.append(request)
            raise AgentConfigurationFailure("review process crashed", raw_output="crash")
        return super().run(request)


class ResumeAtReviewRunner(SprintRunner):
    def __init__(self):
        super().__init__(stop_after=1)
        self.accepted = 0

    def run(self, request: AgentRequest) -> AgentResult:
        if request.role == "planner":
            raise AgentCancelled("stop after recovered iteration")
        return super().run(request)


def test_recovery_resumes_review_without_replaying_completed_coder(tmp_path: Path):
    first = ReviewCrashRunner(stop_after=1)
    orchestrator = make_orchestrator(tmp_path, first)

    with pytest.raises(AgentConfigurationFailure):
        orchestrator.run()
    assert orchestrator.state.status == "failed"
    assert orchestrator.state.active_iteration["phase"] == "review"

    resumed_runner = ResumeAtReviewRunner()
    recovered = ForgeOrchestrator.from_existing(
        orchestrator.repo,
        orchestrator.run_id,
        runner=resumed_runner,
        state_home=tmp_path / "state",
        check_binaries=False,
    ).recover()

    assert recovered.status == "cancelled"
    assert recovered.cycle == 1
    assert not any(request.role == "coder" for request in resumed_runner.requests)
    assert not any(request.role == "probe" for request in resumed_runner.requests)


class CrashOnceRunner(SprintRunner):
    def __init__(self, target_role: str):
        super().__init__(stop_after=1)
        self.target_role = target_role
        self.crashed = False

    def run(self, request: AgentRequest) -> AgentResult:
        if request.role == self.target_role and not self.crashed:
            self.requests.append(request)
            self.crashed = True
            raise AgentConfigurationFailure(f"{request.role} crashed", raw_output="crash")
        return super().run(request)


@pytest.mark.parametrize("role", ["brain", "planner", "coder", "tester"])
def test_recovery_continues_from_each_agent_phase_without_losing_iteration(
    tmp_path: Path, role: str
):
    first = CrashOnceRunner(role)
    orchestrator = make_orchestrator(tmp_path, first)

    with pytest.raises(AgentConfigurationFailure, match="crashed"):
        orchestrator.run()
    assert orchestrator.state.status == "failed"

    resumed_runner = SprintRunner(stop_after=1)
    recovered = ForgeOrchestrator.from_existing(
        orchestrator.repo,
        orchestrator.run_id,
        runner=resumed_runner,
        state_home=tmp_path / "state",
        check_binaries=False,
    ).recover()

    assert recovered.status == "cancelled"
    assert recovered.cycle == 1
    assert len(recovered.iterations) == 1
    assert git(orchestrator.repo, "rev-list", "--count", "HEAD") == "2"


class InflightEditCrashRunner(SprintRunner):
    def run(self, request: AgentRequest) -> AgentResult:
        if request.role == "coder":
            self.requests.append(request)
            story = story_from_prompt(request.prompt)
            (request.cwd / f"{story.lower()}.txt").write_text(
                "edit completed before process crash\n", encoding="utf-8"
            )
            raise AgentConfigurationFailure("coder crashed after editing", raw_output="crash")
        return super().run(request)


def test_final_inflight_coder_round_recovers_edits_before_enforcing_round_limit(tmp_path: Path):
    first = InflightEditCrashRunner(stop_after=1)
    orchestrator = make_orchestrator(tmp_path, first, max_revision_rounds=1)

    with pytest.raises(AgentConfigurationFailure, match="after editing"):
        orchestrator.run()
    assert orchestrator.state.active_iteration["coder_inflight"] is True
    assert orchestrator.state.active_iteration["coder_round"] == 1

    resumed_runner = SprintRunner(stop_after=1)
    recovered = ForgeOrchestrator.from_existing(
        orchestrator.repo,
        orchestrator.run_id,
        runner=resumed_runner,
        state_home=tmp_path / "state",
        check_binaries=False,
    ).recover()

    assert recovered.status == "cancelled"
    assert recovered.cycle == 1
    assert not any(request.role == "coder" for request in resumed_runner.requests)


@pytest.mark.parametrize("crash_method", ["prepare_commit", "reconcile_delivery", "cleanup"])
def test_delivery_crash_windows_reconcile_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crash_method: str
):
    runner = SprintRunner(stop_after=1)
    orchestrator = make_orchestrator(tmp_path, runner)
    original = getattr(GitWorkspace, crash_method)
    crashed = False

    def fail_after_success(workspace, *args, **kwargs):
        nonlocal crashed
        value = original(workspace, *args, **kwargs)
        if not crashed:
            crashed = True
            raise AgentConfigurationFailure(
                f"crash after {crash_method}", raw_output="delivery crash"
            )
        return value

    monkeypatch.setattr(GitWorkspace, crash_method, fail_after_success)
    with pytest.raises(AgentConfigurationFailure, match=crash_method):
        orchestrator.run()
    monkeypatch.setattr(GitWorkspace, crash_method, original)

    resumed_runner = SprintRunner(stop_after=0)
    recovered = ForgeOrchestrator.from_existing(
        orchestrator.repo,
        orchestrator.run_id,
        runner=resumed_runner,
        state_home=tmp_path / "state",
        check_binaries=False,
    ).recover()

    assert recovered.status == "cancelled"
    assert recovered.cycle == 1
    assert len(recovered.iterations) == 1
    assert git(orchestrator.repo, "rev-list", "--count", "HEAD") == "2"
    assert git(orchestrator.repo, "status", "--porcelain") == ""


class FalseValidationRunner(SprintRunner):
    def run(self, request: AgentRequest) -> AgentResult:
        if request.role == "planner":
            self.requests.append(request)
            text = json.dumps(
                {
                    "story_id": "F01",
                    "objective": "Fail validation honestly",
                    "tasks": [
                        {
                            "id": "TASK-001",
                            "title": "Implement",
                            "description": "Create the artifact",
                            "acceptance_criteria": ["F01 is observable", "artifact exists"],
                        }
                    ],
                    "validation_commands": ["false"],
                    "public_checks": ["read artifact"],
                    "addressed_nit_ids": [],
                }
            )
            return result(request, text)
        return super().run(request)


def test_review_cannot_accept_failed_mechanical_validation(tmp_path: Path):
    runner = FalseValidationRunner(stop_after=1)
    orchestrator = make_orchestrator(tmp_path, runner)
    base = git(orchestrator.repo, "rev-parse", "HEAD")

    with pytest.raises(RuntimeError, match="reviewer failed its contract"):
        orchestrator.run()

    assert orchestrator.state.status == "failed"
    assert orchestrator.state.cycle == 0
    assert git(orchestrator.repo, "rev-parse", "HEAD") == base


def test_legacy_state_is_visible_but_not_recoverable(tmp_path: Path):
    repo, brief = repo_and_brief(tmp_path)
    models = {role: ModelSpec.parse("codex:gpt-5.6-luna:low") for role in ROLE_NAMES}
    run_id = "legacy"
    root = repo / ".forge/runs" / run_id
    root.mkdir(parents=True)
    state = RunState(
        run_id=run_id,
        status="failed",
        phase="brain",
        created_at="now",
        updated_at="now",
        config=RunConfig(str(repo), str(brief), "main", models, push=False).to_dict(),
        schema_version=1,
    )
    (root / "state.json").write_text(json.dumps(state.to_dict()), encoding="utf-8")

    orchestrator = ForgeOrchestrator.from_existing(
        repo, run_id, runner=SprintRunner(), state_home=tmp_path / "state", check_binaries=False
    )
    with pytest.raises(RuntimeError, match="legacy Forge runs"):
        orchestrator.recover()


@pytest.mark.parametrize("action", ["pause", "cancel", "interrupt"])
def test_control_save_waits_for_atomic_state_transition(tmp_path: Path, action: str):
    orchestrator = make_orchestrator(tmp_path, SprintRunner(stop_after=0))
    started = threading.Event()
    finished = threading.Event()

    def request_control() -> None:
        started.set()
        if action == "interrupt":
            orchestrator.mark_interrupted("simulated controller interruption")
        else:
            getattr(orchestrator, action)()
        finished.set()

    with orchestrator._state_lock:
        thread = threading.Thread(target=request_control)
        thread.start()
        assert started.wait(1)
        assert finished.wait(0.05) is False
    thread.join(timeout=1)

    assert finished.is_set()
    if action == "pause":
        assert orchestrator.state.paused is True
    elif action == "cancel":
        assert orchestrator.state.cancel_requested is True
    else:
        assert orchestrator.state.status == "failed"


def test_cancel_during_startup_stops_before_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    orchestrator = make_orchestrator(tmp_path, SprintRunner(stop_after=0))
    startup_blocked = threading.Event()
    release_startup = threading.Event()
    preflight_called = threading.Event()
    original_dispatcher = orchestrator._ensure_event_dispatcher
    original_preflight = orchestrator._preflight

    def blocked_dispatcher(generation: int) -> None:
        startup_blocked.set()
        release_startup.wait(2)
        original_dispatcher(generation)

    def observed_preflight() -> None:
        preflight_called.set()
        original_preflight()

    monkeypatch.setattr(orchestrator, "_ensure_event_dispatcher", blocked_dispatcher)
    monkeypatch.setattr(orchestrator, "_preflight", observed_preflight)
    run_thread = threading.Thread(target=orchestrator.run)
    run_thread.start()
    assert startup_blocked.wait(1)

    orchestrator.cancel()
    release_startup.set()
    run_thread.join(timeout=2)

    assert run_thread.is_alive() is False
    assert preflight_called.is_set() is False
    assert orchestrator.state.status == "cancelled"
    assert orchestrator.state.cancel_requested is True


def test_agent_cancellation_during_model_probe_cancels_run(tmp_path: Path):
    class CancelledProbeRunner:
        def allow(self) -> None:
            pass

        def run(self, request: AgentRequest) -> AgentResult:
            assert request.role == "probe"
            raise AgentCancelled("probe cancelled")

    orchestrator = make_orchestrator(tmp_path, CancelledProbeRunner())

    state = orchestrator.run()

    assert state.status == "cancelled"
    assert state.preflight_probed is False


def test_cancel_during_recovery_reload_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    orchestrator = make_orchestrator(tmp_path, SprintRunner(stop_after=0))
    orchestrator.state.status = "failed"
    orchestrator.store.save_state(orchestrator.state)
    load_started = threading.Event()
    release_load = threading.Event()
    cancel_finished = threading.Event()
    preflight_called = threading.Event()
    original_load = orchestrator.store.load_state
    original_dispatcher = orchestrator._ensure_event_dispatcher

    def blocked_load() -> RunState:
        stale_state = original_load()
        load_started.set()
        release_load.wait(2)
        return stale_state

    def dispatcher_after_cancel(generation: int) -> None:
        assert cancel_finished.wait(1)
        original_dispatcher(generation)

    def observed_preflight() -> None:
        preflight_called.set()

    monkeypatch.setattr(orchestrator.store, "load_state", blocked_load)
    monkeypatch.setattr(orchestrator, "_ensure_event_dispatcher", dispatcher_after_cancel)
    monkeypatch.setattr(orchestrator, "_preflight", observed_preflight)
    recovery_thread = threading.Thread(target=orchestrator.recover_failed)
    recovery_thread.start()
    assert load_started.wait(1)

    def request_cancel() -> None:
        orchestrator.cancel()
        cancel_finished.set()

    cancel_thread = threading.Thread(target=request_cancel)
    cancel_thread.start()
    assert cancel_finished.wait(0.05) is False
    release_load.set()
    cancel_thread.join(timeout=1)
    recovery_thread.join(timeout=2)

    assert cancel_finished.is_set()
    assert recovery_thread.is_alive() is False
    assert preflight_called.is_set() is False
    assert orchestrator.state.status == "cancelled"
    assert orchestrator.state.cancel_requested is True


def test_recovery_reloads_config_without_restoring_old_repository_path(tmp_path: Path):
    orchestrator = make_orchestrator(tmp_path, SprintRunner(stop_after=0))
    orchestrator.state.status = "failed"
    persisted = RunConfig.from_dict(orchestrator.state.config)
    persisted.models["brain"] = ModelSpec.parse("opencode:grok-4.6:high")
    orchestrator.state.config = persisted.to_dict()
    orchestrator.store.save_state(orchestrator.state)

    execution_lock, generation = orchestrator._acquire_execution(
        recover=True, reload_state=True
    )
    try:
        assert orchestrator.config.models["brain"].model == "xai/grok-4.6"
        assert orchestrator.config.repo == str(orchestrator.repo)
        assert orchestrator.state.config == orchestrator.config.to_dict()
    finally:
        orchestrator._release_execution(execution_lock, generation)


def test_from_existing_uses_repository_containing_copied_run(tmp_path: Path):
    orchestrator = make_orchestrator(tmp_path, SprintRunner(stop_after=0))
    orchestrator.state.status = "failed"
    orchestrator.store.save_state(orchestrator.state)
    copied_repo = tmp_path / "copied"
    shutil.copytree(orchestrator.repo, copied_repo)

    restored = ForgeOrchestrator.from_existing(
        copied_repo,
        orchestrator.run_id,
        runner=SprintRunner(stop_after=0),
        state_home=tmp_path / "copied-state",
        check_binaries=False,
    )

    assert restored.repo == copied_repo.resolve()
    assert restored.config.repo == str(copied_repo.resolve())
    assert restored.store.root == copied_repo / ".forge" / "runs" / orchestrator.run_id


def test_recover_failed_rejects_active_execution_before_replacing_state(tmp_path: Path):
    orchestrator = make_orchestrator(tmp_path, SprintRunner(stop_after=0))
    orchestrator.state.status = "failed"
    orchestrator.store.save_state(orchestrator.state)
    original_state = orchestrator.state
    execution_lock, generation = orchestrator._acquire_execution(recover=True)
    try:
        with pytest.raises(RuntimeError, match="already has an active execution"):
            orchestrator.recover_failed()
        assert orchestrator.state is original_state
    finally:
        orchestrator._release_execution(execution_lock, generation)


def test_control_after_execution_seal_remains_pending_for_recovery(tmp_path: Path):
    orchestrator = make_orchestrator(tmp_path, SprintRunner(stop_after=0))
    generation = orchestrator._begin_execution(recover=False)
    orchestrator._seal_execution_controls()

    orchestrator.cancel()
    orchestrator._finish_execution(generation)
    next_generation = orchestrator._begin_execution(recover=True)
    try:
        assert orchestrator.state.cancel_requested is True
    finally:
        orchestrator._finish_execution(next_generation)


def test_control_arriving_during_terminal_seal_remains_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    orchestrator = make_orchestrator(tmp_path, SprintRunner(stop_after=0))
    orchestrator.state.preflight_probed = True
    generation = orchestrator._begin_execution(recover=False)
    seal_entered = threading.Event()
    release_seal = threading.Event()
    cancel_finished = threading.Event()
    original_seal = orchestrator._seal_execution_controls

    monkeypatch.setattr(orchestrator, "_preflight", lambda: None)

    def stall() -> RunState:
        raise IterationStalled("terminal boundary")

    def blocked_seal() -> None:
        seal_entered.set()
        release_seal.wait(2)
        original_seal()

    monkeypatch.setattr(orchestrator, "_drive", stall)
    monkeypatch.setattr(orchestrator, "_seal_execution_controls", blocked_seal)
    execution_thread = threading.Thread(
        target=lambda: orchestrator._execute(recover=False)
    )
    execution_thread.start()
    assert seal_entered.wait(1)

    def request_cancel() -> None:
        orchestrator.cancel()
        cancel_finished.set()

    cancel_thread = threading.Thread(target=request_cancel)
    cancel_thread.start()
    assert cancel_finished.wait(0.05) is False
    release_seal.set()
    execution_thread.join(timeout=1)
    cancel_thread.join(timeout=1)
    orchestrator._finish_execution(generation)

    assert cancel_finished.is_set()
    next_generation = orchestrator._begin_execution(recover=True)
    try:
        assert orchestrator.state.cancel_requested is True
    finally:
        orchestrator._finish_execution(next_generation)


def test_stale_controller_cannot_overwrite_cross_process_owner_state(tmp_path: Path):
    orchestrator = make_orchestrator(tmp_path, SprintRunner(stop_after=0))
    orchestrator.state.status = "running"
    orchestrator.store.save_state(orchestrator.state)
    stale = ForgeOrchestrator.from_existing(
        orchestrator.repo,
        orchestrator.run_id,
        runner=SprintRunner(stop_after=0),
        state_home=tmp_path / "stale-state",
        check_binaries=False,
    )
    owner_lock = RepositoryExecutionLock(orchestrator.repo, "main", "owner")

    with owner_lock:
        orchestrator.state.cycle = 7
        orchestrator.store.save_state(orchestrator.state)
        with pytest.raises(ExecutionLocked, match="another Forge execution"):
            stale.cancel()

    persisted = orchestrator.store.load_state()
    assert persisted.cycle == 7
    assert persisted.cancel_requested is False

    stale.cancel()
    persisted = orchestrator.store.load_state()
    assert persisted.cycle == 7
    assert persisted.cancel_requested is True


def test_event_callback_can_request_control_without_lock_inversion(tmp_path: Path):
    repo, brief = repo_and_brief(tmp_path)
    holder = {}
    callback_started = threading.Event()
    callback_finished = threading.Event()

    def callback(event: dict) -> None:
        if event.get("message") != "callback-control":
            return
        callback_started.set()
        holder["orchestrator"].cancel()
        callback_finished.set()

    orchestrator = ForgeOrchestrator(
        config(repo, brief),
        run_id="callback-run",
        runner=SprintRunner(stop_after=0),
        on_event=callback,
        state_home=tmp_path / "state",
        check_binaries=False,
    )
    holder["orchestrator"] = orchestrator
    generation = orchestrator._begin_execution(recover=False)
    control_held = threading.Event()

    def overlapping_pause() -> None:
        with orchestrator._control:
            control_held.set()
            orchestrator.pause()

    with orchestrator._state_lock:
        pause_thread = threading.Thread(target=overlapping_pause)
        pause_thread.start()
        assert control_held.wait(1)
        orchestrator._save("callback-control")
        assert callback_started.wait(1)
        assert callback_finished.is_set() is False

    pause_thread.join(timeout=1)
    assert pause_thread.is_alive() is False
    assert callback_finished.wait(1)
    assert orchestrator.state.cancel_requested is True
    orchestrator._finish_execution(generation)
    orchestrator._shutdown_event_dispatcher()


def test_event_dispatch_is_bounded_and_drains_on_shutdown(tmp_path: Path):
    repo, brief = repo_and_brief(tmp_path)
    callback_started = threading.Event()
    release_callback = threading.Event()
    seen: list[int] = []

    def slow_callback(event: dict) -> None:
        callback_started.set()
        release_callback.wait(2)
        seen.append(int(event["sequence"]))

    orchestrator = ForgeOrchestrator(
        config(repo, brief),
        run_id="bounded-events",
        runner=SprintRunner(stop_after=0),
        on_event=slow_callback,
        state_home=tmp_path / "state",
        check_binaries=False,
    )
    generation = orchestrator._begin_execution(recover=False)
    orchestrator._queue_event({"sequence": 0})
    assert callback_started.wait(1)
    for sequence in range(1, EVENT_QUEUE_LIMIT * 2):
        orchestrator._queue_event({"sequence": sequence})
    with orchestrator._event_lock:
        assert orchestrator._event_queue is not None
        assert orchestrator._event_queue.qsize() <= EVENT_QUEUE_LIMIT

    release_callback.set()
    orchestrator._finish_execution(generation)
    orchestrator._shutdown_event_dispatcher()

    assert EVENT_QUEUE_LIMIT * 2 - 1 in seen
    assert orchestrator._event_thread is None
    assert orchestrator._event_queue is None


def test_blocked_event_callback_quarantines_same_object_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo, brief = repo_and_brief(tmp_path)
    callback_started = threading.Event()
    release_callback = threading.Event()
    holder = {}

    def blocked_callback(_event: dict) -> None:
        callback_started.set()
        release_callback.wait(2)
        holder["orchestrator"].cancel()

    orchestrator = ForgeOrchestrator(
        config(repo, brief),
        run_id="blocked-callback",
        runner=SprintRunner(stop_after=0),
        on_event=blocked_callback,
        state_home=tmp_path / "state",
        check_binaries=False,
    )
    holder["orchestrator"] = orchestrator
    generation = orchestrator._begin_execution(recover=False)
    monkeypatch.setattr("forge.orchestrator.EVENT_SHUTDOWN_TIMEOUT_SECONDS", 0.05)
    orchestrator._queue_event({"kind": "state", "message": "old execution"})
    assert callback_started.wait(1)

    orchestrator._finish_execution(generation)
    orchestrator._shutdown_event_dispatcher()

    assert orchestrator._event_thread is not None
    assert orchestrator._event_thread.is_alive()
    orchestrator.state.status = "failed"
    orchestrator.store.save_state(orchestrator.state)
    with pytest.raises(RuntimeError, match="previous event callback"):
        orchestrator.recover()

    release_callback.set()
    orchestrator._event_thread.join(timeout=1)
    assert orchestrator.state.cancel_requested is False
    new_generation = orchestrator._begin_execution(recover=False)
    orchestrator._ensure_event_dispatcher(new_generation)
    assert orchestrator._event_thread is not None
    assert orchestrator._event_thread.is_alive()
    orchestrator._finish_execution(new_generation)
    orchestrator._shutdown_event_dispatcher()


def test_recovery_deduplicates_partially_persisted_finalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runner = SprintRunner(stop_after=1)
    orchestrator = make_orchestrator(tmp_path, runner)
    original = GitWorkspace.cleanup

    def crash_after_cleanup(workspace):
        original(workspace)
        raise AgentConfigurationFailure("crash during finalization", raw_output="crash")

    monkeypatch.setattr(GitWorkspace, "cleanup", crash_after_cleanup)
    with pytest.raises(AgentConfigurationFailure, match="finalization"):
        orchestrator.run()
    monkeypatch.setattr(GitWorkspace, "cleanup", original)

    acceptance_path = (
        orchestrator.store.root / "sprints/001/iterations/01/acceptance.json"
    )
    record = json.loads(acceptance_path.read_text(encoding="utf-8"))
    orchestrator.state.iterations.append(record)
    orchestrator.state.cycle = 1
    orchestrator.state.sprint_iteration = 1
    orchestrator.store.save_state(orchestrator.state)

    recovered = ForgeOrchestrator.from_existing(
        orchestrator.repo,
        orchestrator.run_id,
        runner=SprintRunner(stop_after=0),
        state_home=tmp_path / "state",
        check_binaries=False,
    ).recover()

    assert recovered.status == "cancelled"
    assert recovered.cycle == 1
    assert len(recovered.iterations) == 1
    assert recovered.sprint_iteration == 1


class CancelProductOwnerRunner:
    def run(self, request: AgentRequest) -> AgentResult:
        assert request.role == "brain"
        raise AgentCancelled("stop before reassessment")


def test_recovery_reconstructs_partial_sprint_close_before_starting_another_slot(
    tmp_path: Path,
):
    orchestrator = make_orchestrator(tmp_path, SprintRunner())
    state = orchestrator.run()
    assert len(state.iterations) == 10
    assert len(state.completed_sprints) == 1

    state.completed_sprints = []
    state.sprint_iteration = 0
    state.needs_product_owner = False
    state.status = "failed"
    state.phase = "planning"
    orchestrator.store.save_state(state)

    recovered = ForgeOrchestrator.from_existing(
        orchestrator.repo,
        orchestrator.run_id,
        runner=CancelProductOwnerRunner(),
        state_home=tmp_path / "state",
        check_binaries=False,
    ).recover()

    assert recovered.status == "cancelled"
    assert len(recovered.iterations) == 10
    assert len(recovered.completed_sprints) == 1
    assert recovered.sprint_iteration == 0
    assert recovered.needs_product_owner is True
