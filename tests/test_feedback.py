import json
import threading
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from forge.agents import AgentFailure, AgentRequest
from forge.artifacts import ArtifactStore
from forge.cli import main as cli_main
from forge.contracts import parse_iteration_review
from forge.feedback import RunConversationStore
from forge.models import AgentResult, ModelSpec, ROLE_NAMES, RunConfig, Usage
from forge.orchestrator import ForgeOrchestrator


def _orchestrator(tmp_path: Path, runner) -> ForgeOrchestrator:
    repo = tmp_path / "target"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "forge@test.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Forge tests"], check=True)
    (repo / "README.md").write_text("# Fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "fixture"], check=True, capture_output=True)
    brief = tmp_path / "brief.md"
    brief.write_text("A test product.\n", encoding="utf-8")
    config = RunConfig(
        str(repo),
        str(brief),
        "main",
        {role: ModelSpec.parse("codex:gpt-6-luna:xhigh") for role in ROLE_NAMES},
        push=False,
    )
    orchestrator = ForgeOrchestrator(
        config,
        run_id="feedback-run",
        runner=runner,
        state_home=tmp_path / "state",
        check_binaries=False,
    )
    orchestrator.state.phase = "coding"
    orchestrator.state.status = "running"
    orchestrator.state.active_iteration = {"id": "ITER-01", "story_id": "F01", "phase": "coding"}
    return orchestrator


class RecordingRunner:
    def __init__(self, blocked_role: str = ""):
        self.blocked_role = blocked_role
        self.started = threading.Event()
        self.release = threading.Event()
        self.cancelled = threading.Event()
        self.requests: list[AgentRequest] = []
        self.lock = threading.Lock()

    def run(self, request: AgentRequest) -> AgentResult:
        with self.lock:
            self.requests.append(request)
        if request.role == self.blocked_role:
            self.started.set()
            assert self.release.wait(3)
        return AgentResult(
            text="acknowledged",
            session_id=request.session_id,
            usage=Usage(),
            elapsed_seconds=0.01,
            raw_output="acknowledged",
        )

    def cancel(self) -> None:
        self.cancelled.set()


def test_feedback_survives_recovery_and_duplicate_concurrent_posts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def submit(_index: int):
        store = RunConversationStore(repo, "run-1")
        return store.add_feedback(
            "Keep the public response backwards compatible.",
            target_role="reviewer",
            idempotency_key="request-123",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(16)))
    ids = {item["id"] for item, _created in results}
    assert len(ids) == 1
    assert sum(created for _item, created in results) == 1

    recovered = RunConversationStore(repo, "run-1")
    snapshot = recovered.snapshot()
    assert len(snapshot["feedback"]) == 1
    assert snapshot["feedback"][0]["status"] == "received"
    assert snapshot["feedback"][0]["message"].startswith("Keep the public response")


def test_cli_add_and_list_use_the_durable_run_queue(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "cli-repo"
    repo.mkdir()
    state = type(
        "State",
        (),
        {
            "to_dict": lambda _self: {
                "run_id": "cli-run",
                "phase": "review",
                "active_iteration": {"id": "ITER-CLI"},
            }
        },
    )()
    ArtifactStore(repo, "cli-run").save_state(state)

    assert cli_main(
        [
            "feedback", "add", "--repo", str(repo), "--run-id", "cli-run",
            "--message", "Keep the old error code.", "--target-role", "reviewer",
        ]
    ) == 0
    added = json.loads(capsys.readouterr().out)
    assert added["feedback"]["status"] == "received"
    assert added["feedback"]["phase_received"] == "review"

    assert cli_main(["feedback", "list", "--repo", str(repo), "--run-id", "cli-run"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert len(listed["feedback"]) == 1


def test_review_contract_accepts_bounded_operator_suggestion_payload() -> None:
    suggestion = {
        "id": "SUG-001",
        "kind": "question",
        "title": "Which response shape remains supported?",
        "context": "The review found an API compatibility question.",
        "rationale": "Two public formats currently overlap.",
        "expected_impact": "The answer will guide this review.",
        "recommendation": "Keep both formats for this iteration.",
        "target_role": "reviewer",
        "feedback_kind": "guidance",
        "requires_decision": False,
    }
    payload = {
        "verdict": "accept",
        "summary": "The implementation matches the plan.",
        "implementation_fingerprint": "tree-fingerprint",
        "task_results": [{"task_id": "TASK-1", "verdict": "accept", "evidence": ["checked"]}],
        "blocking_findings": [],
        "nits": [],
        "blocker": "",
        "operator_suggestions": [suggestion],
    }
    parsed = parse_iteration_review(
        json.dumps(payload),
        task_ids=("TASK-1",),
        expected_fingerprint="tree-fingerprint",
        validation_results=[],
    )
    assert parsed["operator_suggestions"] == [suggestion]

    payload["operator_suggestions"] = [{**suggestion, "requires_decision": "no"}]
    try:
        parse_iteration_review(
            json.dumps(payload),
            task_ids=("TASK-1",),
            expected_fingerprint="tree-fingerprint",
            validation_results=[],
        )
    except ValueError as exc:
        assert "requires_decision must be boolean" in str(exc)
    else:
        raise AssertionError("malformed operator suggestion was accepted")


def test_scope_change_waits_for_decision_and_next_product_owner_boundary(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = RunConversationStore(repo, "run-2")
    item, _ = store.add_feedback(
        "Replace the current local workflow with a hosted multi-tenant service.",
        kind="scope_change",
        phase="coding",
        iteration_id="ITER-02",
    )
    assert item["status"] == "needs_decision"
    assert store.prepare_feedback(
        role="coder_tdd", phase="coding", iteration_id="ITER-02", relative="coder/1"
    ) == []

    scheduled = store.decide_feedback(item["id"], "schedule_replan", active_iteration_id="ITER-02")
    assert scheduled["status"] == "pending"
    assert store.prepare_feedback(
        role="brain", phase="product-owner", iteration_id="ITER-02", relative="brain/1"
    ) == []
    ready = store.prepare_feedback(
        role="brain", phase="product-owner", iteration_id="", relative="brain/2"
    )
    assert [entry["id"] for entry in ready] == [item["id"]]
    store.complete_delivery([item["id"]], relative="brain/2", response_path="brain/2.response.md")
    assert store.snapshot()["feedback"][0]["status"] == "applied"


def test_auto_feedback_does_not_split_parallel_coder_contexts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = RunConversationStore(repo, "run-coders")
    item, _ = store.add_feedback("Keep the old pagination token.", phase="coding")
    assert store.prepare_feedback(
        role="coder_tdd",
        phase="coding",
        iteration_id="ITER-01",
        relative="candidates/tdd",
        code_started=True,
    ) == []
    assert store.snapshot()["feedback"][0]["status"] == "received"
    selected = store.prepare_feedback(
        role="reviewer",
        phase="selection",
        iteration_id="ITER-01",
        relative="selection/attempt-1",
        code_started=True,
    )
    assert [entry["id"] for entry in selected] == [item["id"]]


def test_mid_coding_feedback_waits_for_safe_boundary_without_interrupting_worker(tmp_path: Path) -> None:
    runner = RecordingRunner(blocked_role="coder_tdd")
    orchestrator = _orchestrator(tmp_path, runner)
    failures: list[BaseException] = []

    def invoke_coder() -> None:
        try:
            orchestrator._invoke(
                role="coder_tdd",
                model=orchestrator.config.models["coder_tdd"],
                prompt="Implement the accepted plan.",
                cwd=tmp_path,
                relative="iteration/coder/round-1",
            )
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=invoke_coder)
    worker.start()
    assert runner.started.wait(1)
    item, created = orchestrator.conversation.add_feedback(
        "Use the existing response shape in the new endpoint.",
        phase="coding",
        iteration_id="ITER-01",
    )
    assert created and item["status"] == "received"
    assert worker.is_alive()
    assert not runner.cancelled.is_set()

    runner.release.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert failures == []
    assert orchestrator.conversation.snapshot()["feedback"][0]["status"] == "received"

    orchestrator.state.phase = "selection"
    orchestrator._invoke(
        role="reviewer",
        model=orchestrator.config.models["reviewer"],
        prompt="Review the tournament result.",
        cwd=tmp_path,
        relative="iteration/review/round-1",
    )
    reviewer_prompt = orchestrator.store.root / "iteration/review/round-1.prompt.md"
    assert item["id"] in reviewer_prompt.read_text(encoding="utf-8")
    status = orchestrator.conversation.snapshot()["feedback"][0]
    assert status["status"] == "applied"
    assert status["deliveries"][0]["role"] == "reviewer"
    assert not runner.cancelled.is_set()


def test_mid_review_feedback_is_delivered_to_tester_after_reviewer_finishes(tmp_path: Path) -> None:
    runner = RecordingRunner(blocked_role="reviewer")
    orchestrator = _orchestrator(tmp_path, runner)
    orchestrator.state.phase = "review"
    failures: list[BaseException] = []

    def invoke_reviewer() -> None:
        try:
            orchestrator._invoke(
                role="reviewer",
                model=orchestrator.config.models["reviewer"],
                prompt="Assess the reviewed implementation.",
                cwd=tmp_path,
                relative="iteration/review/round-1",
            )
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=invoke_reviewer)
    worker.start()
    assert runner.started.wait(1)
    item, _ = orchestrator.conversation.add_feedback(
        "Retain the old empty-result behavior.",
        phase="review",
        iteration_id="ITER-01",
    )
    assert worker.is_alive() and not runner.cancelled.is_set()
    runner.release.set()
    worker.join(timeout=2)
    assert failures == []

    orchestrator.state.phase = "testing"
    orchestrator._invoke(
        role="tester",
        model=orchestrator.config.models["tester"],
        prompt="Exercise public behavior.",
        cwd=tmp_path,
        relative="iteration/tester/round-1",
    )
    tester_prompt = orchestrator.store.root / "iteration/tester/round-1.prompt.md"
    assert item["id"] in tester_prompt.read_text(encoding="utf-8")
    record = orchestrator.conversation.snapshot()["feedback"][0]
    assert record["status"] == "applied"
    assert record["deliveries"][0]["role"] == "tester"
    assert not runner.cancelled.is_set()


def test_provider_retry_keeps_the_feedback_in_the_retried_prompt(tmp_path: Path) -> None:
    class FailOnceRunner(RecordingRunner):
        def run(self, request: AgentRequest) -> AgentResult:
            with self.lock:
                self.requests.append(request)
                attempt = len(self.requests)
            if attempt == 1:
                raise AgentFailure("temporary provider failure")
            return AgentResult(
                text="acknowledged",
                session_id=request.session_id,
                usage=Usage(),
                elapsed_seconds=0.01,
                raw_output="acknowledged",
            )

    runner = FailOnceRunner()
    orchestrator = _orchestrator(tmp_path, runner)
    orchestrator.state.phase = "review"
    item, _ = orchestrator.conversation.add_feedback(
        "Keep the old pagination token.", target_role="reviewer"
    )

    orchestrator._invoke(
        role="reviewer",
        model=orchestrator.config.models["reviewer"],
        prompt="Review the implementation.",
        cwd=tmp_path,
        relative="iteration/review/round-1",
    )

    assert len(runner.requests) == 2
    assert all(item["id"] in request.prompt for request in runner.requests)
    assert orchestrator.conversation.snapshot()["feedback"][0]["status"] == "applied"


def test_suggestion_answer_enters_the_same_feedback_queue_and_dedupes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = RunConversationStore(repo, "run-3")
    suggestion, created = store.publish_suggestion(
        kind="question",
        title="Which compatibility target should remain supported?",
        context="Reviewer is checking iteration ITER-03, story F04.",
        rationale="The implementation exposes two existing response shapes.",
        expected_impact="The answer will determine whether the review can accept the API change.",
        recommendation="Keep both response shapes until a later migration.",
        source="review:ITER-03",
        target_role="reviewer",
    )
    duplicate, duplicate_created = store.publish_suggestion(
        kind="question",
        title="Which compatibility target should remain supported?",
        context="Reviewer is checking iteration ITER-03, story F04.",
        rationale="different wording does not create a second card",
        expected_impact="same expected impact",
        recommendation="same recommendation",
        source="review:ITER-03",
        target_role="reviewer",
    )
    assert created and not duplicate_created
    assert duplicate["id"] == suggestion["id"]

    answered, feedback = store.answer_suggestion(
        suggestion["id"],
        "answer",
        answer="Keep both response shapes until the next major version.",
        phase="review",
        iteration_id="ITER-03",
    )
    assert answered["status"] == "answered"
    assert feedback is not None and feedback["status"] == "received"
    snapshot = store.snapshot()
    assert len(snapshot["suggestions"]) == 1
    assert len(snapshot["feedback"]) == 1
    assert snapshot["feedback"][0]["source"] == f"suggestion:{suggestion['id']}"


def test_accepting_a_scope_suggestion_still_requires_explicit_replan_decision(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = RunConversationStore(repo, "run-4")
    suggestion, _ = store.publish_suggestion(
        kind="suggestion",
        title="Consider adding a hosted deployment tier.",
        context="Reviewer completed ITER-05.",
        rationale="Several users may eventually need shared access.",
        expected_impact="A hosted tier would change product scope and operating costs.",
        recommendation="Plan a separate product discussion before changing the current roadmap.",
        source="review:ITER-05",
        target_role="brain",
        feedback_kind="scope_change",
        requires_decision=True,
    )
    accepted, feedback = store.answer_suggestion(
        suggestion["id"], "accept", phase="review", iteration_id="ITER-05"
    )
    assert accepted["status"] == "accepted"
    assert feedback is not None and feedback["status"] == "needs_decision"
    scheduled = store.decide_feedback(
        feedback["id"], "schedule_replan", active_iteration_id="ITER-05"
    )
    assert scheduled["status"] == "pending"
    assert scheduled["wait_for_iteration"] == "ITER-05"

    rejected, no_feedback = store.publish_suggestion(
        kind="suggestion",
        title="Use a new build service.",
        context="Reviewer completed ITER-05.",
        rationale="An alternative runner may speed builds.",
        expected_impact="Build time may drop.",
        recommendation="Evaluate the service later.",
        source="review:ITER-05",
    )
    assert no_feedback is True
    rejected, feedback = store.answer_suggestion(rejected["id"], "reject")
    assert rejected["status"] == "rejected"
    assert feedback is None


def test_repeated_open_feedback_and_excess_suggestions_are_suppressed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = RunConversationStore(repo, "run-5")
    first, created = store.add_feedback("Avoid changing the public response shape.", idempotency_key="first")
    duplicate, duplicate_created = store.add_feedback(
        "Avoid changing the public response shape.", idempotency_key="second"
    )
    assert created and not duplicate_created and first["id"] == duplicate["id"]

    for index in range(21):
        result, _ = store.publish_suggestion(
            kind="suggestion",
            title=f"Consider a distinct improvement {index}.",
            context="Repeated review suggestions.",
            rationale="A specific follow-up may be useful.",
            expected_impact="The panel should remain focused.",
            recommendation=f"Review improvement {index} later.",
            source="review:ITER-06",
        )
    assert result["status"] == "suppressed"
    snapshot = store.snapshot()
    assert sum(item["status"] == "open" for item in snapshot["suggestions"]) == 20
    assert snapshot["suppressed_suggestions"] == 1
