import json
import threading
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from forge.agents import AgentFailure, AgentRequest
from forge.artifacts import ArtifactStore
from forge.cli import main as cli_main
from forge.contracts import parse_iteration_review
from forge.feedback import MAX_SUGGESTION_LENGTH, RunConversationStore
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


def test_valid_json_with_invalid_conversation_shape_is_reported_as_store_error(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = RunConversationStore(repo, "run-invalid-shape")
    store.root.mkdir(parents=True)
    store.path.write_text(json.dumps({"feedback": {}, "suggestions": []}), encoding="utf-8")

    with pytest.raises(RuntimeError, match="feedback must be a list"):
        store.snapshot()


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
    assert added["feedback"]["iteration_received"] == ""

    assert cli_main(
        [
            "feedback", "add", "--repo", str(repo), "--run-id", "cli-run",
            "--message", "Keep this coder fix scoped to the next iteration.",
            "--target-role", "coder_tdd",
        ]
    ) == 0
    coder_feedback = json.loads(capsys.readouterr().out)["feedback"]
    assert coder_feedback["iteration_received"] == ""
    store = RunConversationStore(repo, "cli-run")
    queued = store.prepare_feedback(
        role="coder_tdd",
        phase="coding",
        iteration_id="ITER-NEXT",
        relative="coder/fix-round-1",
        winner_role="coder_tdd",
    )
    assert [item["id"] for item in queued] == [coder_feedback["id"]]
    assert store.snapshot()["feedback"][1]["iteration_received"] == "ITER-NEXT"

    assert cli_main(["feedback", "list", "--repo", str(repo), "--run-id", "cli-run"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert len(listed["feedback"]) == 2


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


def test_targeted_losing_coder_feedback_routes_to_reviewer_after_selection(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = RunConversationStore(repo, "run-coder-target")
    item, _ = store.add_feedback(
        "Keep the selected implementation on the established response shape.",
        target_role="coder_explore",
        phase="coding",
        iteration_id="ITER-01",
    )
    assert store.prepare_feedback(
        role="coder_explore",
        phase="coding",
        iteration_id="ITER-01",
        relative="coder/explore",
        active_roles={"coder_tdd"},
    ) == []
    assert store.prepare_feedback(
        role="reviewer",
        phase="selection",
        iteration_id="ITER-01",
        relative="selection/attempt-1",
        active_roles={"coder_tdd"},
    ) == []
    selected = store.prepare_feedback(
        role="reviewer",
        phase="review",
        iteration_id="ITER-01",
        relative="review/attempt-1",
        winner_role="coder_tdd",
    )
    assert [entry["id"] for entry in selected] == [item["id"]]


def test_feedback_for_selected_coder_reaches_the_single_writer_fix_round(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = RunConversationStore(repo, "run-coder-fix")
    item, _ = store.add_feedback(
        "Preserve the existing cursor semantics in the fix.",
        target_role="coder_tdd",
        phase="coding",
        iteration_id="ITER-02",
    )
    ready = store.prepare_feedback(
        role="coder_tdd",
        phase="coding",
        iteration_id="ITER-02",
        relative="coder/fix-round-1",
        code_started=False,
        winner_role="coder_tdd",
    )
    assert [entry["id"] for entry in ready] == [item["id"]]


def test_product_owner_gap_coder_feedback_binds_to_next_iteration(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = RunConversationStore(repo, "run-product-owner-gap")
    item, _ = store.add_feedback(
        "Keep the existing response envelope.",
        phase="product-owner",
        target_role="coder_tdd",
        iteration_id="",
    )

    assert store.prepare_feedback(
        role="brain",
        phase="product-owner",
        iteration_id="",
        relative="brain/product-owner",
    ) == []
    assert store.prepare_feedback(
        role="planner",
        phase="planning",
        iteration_id="ITER-02",
        relative="planner/iteration-02",
    ) == []
    waiting = store.snapshot()["feedback"][0]
    assert waiting["id"] == item["id"]
    assert waiting["iteration_received"] == "ITER-02"
    assert any("Associated with ITER-02" in event["explanation"] for event in waiting["history"])
    assert store.mark_unapplied_coder_feedback("ITER-02") == 1
    assert store.snapshot()["feedback"][0]["status"] == "not_applied"


def test_stale_coder_feedback_from_stalled_iteration_is_not_replayed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = RunConversationStore(repo, "run-stalled-coder")
    item, _ = store.add_feedback(
        "Do not change the response envelope.",
        target_role="coder_tdd",
        phase="coding",
        iteration_id="ITER-01",
    )

    assert store.prepare_feedback(
        role="coder_tdd",
        phase="coding",
        iteration_id="ITER-02",
        relative="coder/iteration-02",
        winner_role="coder_tdd",
    ) == []
    settled = store.snapshot()["feedback"][0]
    assert settled["id"] == item["id"]
    assert settled["status"] == "not_applied"
    assert "bound to ITER-01" in settled["explanation"]


def test_coder_feedback_reaches_post_selection_reviewer_without_a_fix_round(tmp_path: Path) -> None:
    runner = RecordingRunner()
    orchestrator = _orchestrator(tmp_path, runner)
    orchestrator.state.active_iteration.update(
        {
            "winner": "tdd",
            "candidates": {
                "tdd": {"status": "complete"},
                "explore": {"status": "complete"},
                "classic": {"status": "failed"},
            },
        }
    )
    item, _ = orchestrator.conversation.add_feedback(
        "Keep the current cursor contract in the selected fix.",
        target_role="coder_tdd",
        phase="review",
        iteration_id="ITER-01",
    )
    orchestrator.state.phase = "review"
    orchestrator._invoke(
        role="reviewer",
        model=orchestrator.config.models["reviewer"],
        prompt="Review the selected candidate.",
        cwd=tmp_path,
        relative="iteration/review/round-1",
    )
    assert item["id"] in runner.requests[-1].prompt
    assert '"target_role": "coder_tdd"' in runner.requests[-1].prompt
    reviewed = orchestrator.conversation.snapshot()["feedback"][0]
    assert reviewed["status"] == "applied"
    assert reviewed["deliveries"][0]["role"] == "reviewer"

    orchestrator.state.phase = "coding"
    orchestrator._invoke(
        role="coder_tdd",
        model=orchestrator.config.models["coder_tdd"],
        prompt="Fix the selected candidate.",
        cwd=tmp_path,
        relative="iteration/coder/round-1",
    )
    assert item["id"] not in runner.requests[-1].prompt
    record = orchestrator.conversation.snapshot()["feedback"][0]
    assert record["status"] == "applied"
    assert record["deliveries"][0]["role"] == "reviewer"


def test_coder_feedback_received_after_review_reaches_tester(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = RunConversationStore(repo, "run-coder-feedback-testing")
    item, _ = store.add_feedback(
        "Check that the selected candidate keeps the old empty-input response.",
        target_role="coder_tdd",
        phase="testing",
        iteration_id="ITER-03",
    )

    ready = store.prepare_feedback(
        role="tester",
        phase="testing",
        iteration_id="ITER-03",
        relative="tester/iteration-03",
        winner_role="coder_tdd",
    )
    assert [entry["id"] for entry in ready] == [item["id"]]
    assert ready[0]["target_role"] == "coder_tdd"


def test_finalizing_settles_unapplied_and_unconfirmed_coder_feedback(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path, RecordingRunner())
    iteration_id = "ITER-01"
    item, _ = orchestrator.conversation.add_feedback(
        "Keep the established response shape in the selected candidate.",
        target_role="coder_explore",
        phase="review",
        iteration_id=iteration_id,
    )
    commit = "a" * 40

    class Workspace:
        @staticmethod
        def target_head() -> str:
            return commit

        @staticmethod
        def cleanup() -> None:
            return None

    orchestrator._workspace = Workspace()
    orchestrator.state.active_iteration.update(
        {
            "id": iteration_id,
            "sprint": 1,
            "slot": 1,
            "kind": "feature",
            "story_id": "F01",
            "plan": {"objective": "Preserve the existing response shape.", "addressed_nit_ids": []},
            "delivery_commit": commit,
            "winner": "tdd",
            "candidates": {
                "tdd": {"status": "complete"},
                "explore": {"status": "complete"},
                "classic": {"status": "failed"},
            },
            "review": {"summary": "Accepted."},
            "test": {"summary": "Passed."},
            "coder_round": 1,
            "review_round": 1,
            "tester_round": 1,
            "nits": [],
        }
    )

    prepared_item, _ = orchestrator.conversation.add_feedback(
        "Preserve the established response shape after recovery.",
        target_role="coder_tdd",
        phase="review",
        iteration_id=iteration_id,
    )
    prepared = orchestrator.conversation.prepare_feedback(
        role="coder_tdd",
        phase="coding",
        iteration_id=iteration_id,
        relative="iteration/coder/fix-round-1",
        winner_role="coder_tdd",
    )
    assert [entry["id"] for entry in prepared] == [prepared_item["id"]]

    settling = threading.Event()
    release_settlement = threading.Event()
    original_settle = orchestrator.conversation.mark_unapplied_coder_feedback

    def pause_settlement(active_iteration_id: str) -> int:
        settling.set()
        assert release_settlement.wait(3)
        return original_settle(active_iteration_id)

    orchestrator.conversation.mark_unapplied_coder_feedback = pause_settlement
    finalizer_errors: list[BaseException] = []

    def finalize() -> None:
        try:
            orchestrator._finalize_iteration()
        except BaseException as exc:
            finalizer_errors.append(exc)

    finalizer = threading.Thread(target=finalize)
    finalizer.start()
    assert settling.wait(1)
    submitted: list[tuple[dict[str, Any], bool]] = []

    def submit_at_boundary() -> None:
        submitted.append(
            orchestrator.add_feedback(
                "Retain this for the next iteration.", target_role="coder_tdd"
            )
        )

    sender = threading.Thread(target=submit_at_boundary)
    sender.start()
    sender.join(timeout=0.05)
    assert sender.is_alive(), "feedback must wait while finalization settles the active iteration"
    try:
        release_settlement.set()
        finalizer.join(timeout=3)
        sender.join(timeout=3)
    finally:
        release_settlement.set()
    assert not finalizer.is_alive()
    assert not sender.is_alive()
    assert finalizer_errors == []
    assert len(submitted) == 1

    records = {entry["id"]: entry for entry in orchestrator.conversation.snapshot()["feedback"]}
    record = records[item["id"]]
    recovered_record = records[prepared_item["id"]]
    assert record["status"] == "not_applied"
    assert "without confirmed application" in record["explanation"]
    assert recovered_record["status"] == "not_applied"
    assert "could not confirm a prepared delivery" in recovered_record["explanation"]
    late_item, created = submitted[0]
    assert created
    assert late_item["iteration_received"] == ""
    assert late_item["status"] == "received"
    assert orchestrator.state.active_iteration == {}


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
    closed_duplicate, closed_duplicate_created = store.publish_suggestion(
        kind="question",
        title="Which compatibility target should remain supported?",
        context="Reviewer is checking iteration ITER-03, story F04.",
        rationale="The implementation exposes two existing response shapes.",
        expected_impact="The answer will determine whether the review can accept the API change.",
        recommendation="Keep both response shapes until a later migration.",
        source="review:ITER-03",
        target_role="reviewer",
    )
    assert not closed_duplicate_created and closed_duplicate["id"] == suggestion["id"]
    later_iteration_duplicate, later_iteration_created = store.publish_suggestion(
        kind="question",
        title="Which compatibility target should remain supported?",
        context="Reviewer is checking iteration ITER-04, story F08.",
        rationale="The new implementation still exposes two response shapes.",
        expected_impact="The answer would affect client compatibility.",
        recommendation="Keep both response shapes until a later migration.",
        source="review:ITER-04",
        target_role="reviewer",
    )
    assert not later_iteration_created
    assert later_iteration_duplicate["id"] == suggestion["id"]


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
    accepted_again, feedback_again = store.answer_suggestion(
        suggestion["id"], "accept", phase="review", iteration_id="ITER-05"
    )
    assert accepted_again["id"] == accepted["id"]
    assert feedback_again is not None and feedback_again["id"] == feedback["id"]
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


def test_answering_a_scope_suggestion_still_requires_explicit_replan_decision(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = RunConversationStore(repo, "run-scope-answer")
    suggestion, _ = store.publish_suggestion(
        kind="question",
        title="Should this run add organization-wide access?",
        context="Reviewer completed ITER-05.",
        rationale="That would affect account ownership and authorization.",
        expected_impact="A yes answer changes product scope and requires a new plan.",
        recommendation="Discuss organization access in a separate product decision.",
        source="review:ITER-05",
        target_role="brain",
        feedback_kind="scope_change",
        requires_decision=True,
    )
    answered, feedback = store.answer_suggestion(
        suggestion["id"], "answer", answer="Yes, but plan it for a later sprint."
    )
    assert answered["status"] == "answered"
    assert feedback is not None and feedback["status"] == "needs_decision"
    assert feedback["kind"] == "scope_change"
    assert store.prepare_feedback(
        role="brain", phase="product-owner", iteration_id="ITER-06", relative="brain/1"
    ) == []
    scheduled = store.decide_feedback(
        feedback["id"], "schedule_replan", active_iteration_id="ITER-05"
    )
    assert scheduled["status"] == "pending"
    assert store.prepare_feedback(
        role="brain", phase="product-owner", iteration_id="ITER-05", relative="brain/2"
    ) == []
    assert [entry["id"] for entry in store.prepare_feedback(
        role="brain", phase="product-owner", iteration_id="ITER-06", relative="brain/3"
    )] == [feedback["id"]]


def test_deferred_suggestion_can_be_answered_later(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = RunConversationStore(repo, "run-deferred")
    suggestion, _ = store.publish_suggestion(
        kind="question",
        title="Which paging token should stay stable?",
        context="The reviewer found two supported token formats.",
        rationale="Both formats have existing callers.",
        expected_impact="The answer determines compatibility expectations.",
        recommendation="Keep accepting both token formats.",
        source="review:ITER-02",
    )
    with pytest.raises(ValueError, match="exceeds 8000 characters"):
        store.answer_suggestion(suggestion["id"], "accept", answer="x" * 8001)
    deferred, no_feedback = store.answer_suggestion(suggestion["id"], "defer")
    assert deferred["status"] == "deferred" and no_feedback is None
    answered, feedback = store.answer_suggestion(
        suggestion["id"], "answer", answer="Keep both formats for this release."
    )
    assert answered["status"] == "answered"
    assert feedback is not None and feedback["status"] == "received"


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
    repeated_suppressed, _ = store.publish_suggestion(
        kind="suggestion",
        title="Consider a distinct improvement 20.",
        context="Repeated review suggestions.",
        rationale="A specific follow-up may be useful.",
        expected_impact="The panel should remain focused.",
        recommendation="Review improvement 20 later.",
        source="review:ITER-06",
    )
    assert repeated_suppressed["status"] == "suppressed"
    question, question_created = store.publish_suggestion(
        kind="question",
        title="Does this API need to remain backward compatible?",
        context="Review is blocked on compatibility expectations.",
        rationale="The current contract could change client behavior.",
        expected_impact="The answer unblocks a safe review decision.",
        recommendation="Confirm whether old callers must keep working.",
        source="review:ITER-06",
    )
    blocker, blocker_created = store.publish_suggestion(
        kind="blocker",
        title="Decision needed: reviewer is blocked",
        context="Reviewer cannot verify an ambiguous authorization rule.",
        rationale="The authorization behavior is not specified.",
        expected_impact="Review cannot safely complete until this is clarified.",
        recommendation="Clarify the access rule.",
        source="review-blocker:ITER-06",
    )
    assert question_created and question["status"] == "open"
    assert blocker_created and blocker["status"] == "open"
    snapshot = store.snapshot()
    assert sum(
        item["status"] == "open" and item["kind"] == "suggestion"
        for item in snapshot["suggestions"]
    ) == 20
    assert sum(item["kind"] in {"question", "blocker"} for item in snapshot["suggestions"]) == 2
    assert snapshot["suppressed_suggestions"] == 1


def test_content_dedupe_remembers_retry_keys_after_feedback_is_applied(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = RunConversationStore(repo, "run-idempotency-alias")
    first, created = store.add_feedback(
        "Keep the old response code.", target_role="reviewer", idempotency_key="request-one"
    )
    duplicate, duplicate_created = store.add_feedback(
        "Keep the old response code.", target_role="reviewer", idempotency_key="request-two"
    )
    assert created and not duplicate_created
    assert duplicate["id"] == first["id"]
    assert "request-two" in duplicate["idempotency_keys"]

    ready = store.prepare_feedback(
        role="reviewer", phase="review", iteration_id="", relative="review/round-1"
    )
    store.complete_delivery(
        [first["id"]], relative="review/round-1", response_path="review/round-1.response.md"
    )
    retried, retry_created = store.add_feedback(
        "Keep the old response code.", target_role="reviewer", idempotency_key="request-two"
    )
    assert len(ready) == 1
    assert not retry_created
    assert retried["id"] == first["id"]
    assert len(store.snapshot()["feedback"]) == 1


def test_suggestion_accept_retry_returns_feedback_when_text_was_already_queued(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = RunConversationStore(repo, "run-accepted-suggestion-alias")
    queued, _ = store.add_feedback(
        "Keep the current response envelope.", target_role="planner"
    )
    suggestion, _ = store.publish_suggestion(
        kind="suggestion",
        title="Keep the old response envelope",
        context="The review found a compatibility risk.",
        rationale="Existing callers may use the current response shape.",
        expected_impact="Clients remain compatible.",
        recommendation="Keep the current response envelope.",
        source="review:ITER-01",
        target_role="planner",
    )

    accepted, feedback = store.answer_suggestion(suggestion["id"], "accept")
    assert accepted["status"] == "accepted"
    assert feedback is not None and feedback["id"] == queued["id"]
    key = f"suggestion:{suggestion['id']}:accept"
    assert key in feedback["idempotency_keys"]
    repeated, repeated_feedback = store.answer_suggestion(suggestion["id"], "accept")
    assert repeated["status"] == "accepted"
    assert repeated_feedback is not None and repeated_feedback["id"] == queued["id"]
    assert len(store.snapshot()["feedback"]) == 1


def test_questions_have_a_per_run_cap_and_suppression_counter(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("forge.feedback.MAX_QUESTIONS_PER_RUN", 3)
    repo = tmp_path / "repo"
    repo.mkdir()
    store = RunConversationStore(repo, "run-question-cap")

    for index in range(3):
        question, created = store.publish_suggestion(
            kind="question",
            title=f"Clarify compatibility detail {index}.",
            context="Reviewer is checking the public contract.",
            rationale="The current response may differ from the established contract.",
            expected_impact="The answer guides a safe decision.",
            recommendation=f"Keep compatibility detail {index} stable.",
            source=f"review:ITER-{index}",
        )
        assert created and question["status"] == "open"
        store.answer_suggestion(question["id"], "reject")

    excess, created = store.publish_suggestion(
        kind="question",
        title="Clarify one more compatibility detail.",
        context="Reviewer is checking the public contract.",
        rationale="The current response may differ from the established contract.",
        expected_impact="The answer guides a safe decision.",
        recommendation="Keep the final detail stable.",
        source="review:ITER-04",
    )
    repeated, repeated_created = store.publish_suggestion(
        kind="question",
        title="Clarify one more compatibility detail.",
        context="A later reviewer asks the same question.",
        rationale="Same question, next iteration.",
        expected_impact="Avoid asking again.",
        recommendation="Keep the final detail stable.",
        source="review:ITER-05",
    )
    blocker, blocker_created = store.publish_suggestion(
        kind="blocker",
        title="Decision needed: a required access rule is missing",
        context="Reviewer cannot safely complete the gate.",
        rationale="Authorization behavior is unspecified.",
        expected_impact="The review cannot progress without clarification.",
        recommendation="Clarify the required access rule.",
        source="review-blocker:ITER-05",
    )
    assert not created and excess["status"] == "suppressed"
    assert not repeated_created and repeated["status"] == "suppressed"
    assert blocker_created and blocker["status"] == "open"
    assert store.snapshot()["suppressed_suggestions"] == 1


def test_resolved_blocker_with_same_title_can_be_published_again(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = RunConversationStore(repo, "run-repeat-blocker")
    values = {
        "kind": "blocker",
        "title": "Decision needed: reviewer is blocked",
        "context": "Reviewer is blocked in the current iteration.",
        "rationale": "A specific rule is missing.",
        "expected_impact": "Review cannot complete.",
        "recommendation": "Clarify the rule.",
        "source": "review-blocker:ITER-01",
    }
    first, created = store.publish_suggestion(**values)
    assert created
    store.answer_suggestion(first["id"], "defer")
    changed_reason, changed_reason_created = store.publish_suggestion(
        **{**values, "rationale": "A different authorization rule is also unspecified."}
    )
    assert changed_reason_created and changed_reason["id"] != first["id"]
    assert store.snapshot()["suggestions"][0]["status"] == "superseded"
    store.answer_suggestion(changed_reason["id"], "reject")
    repeated_blocker, repeated_blocker_created = store.publish_suggestion(
        **{**values, "rationale": "A different authorization rule is also unspecified."}
    )
    assert repeated_blocker_created and repeated_blocker["id"] != changed_reason["id"]
    later, later_created = store.publish_suggestion(
        **{**values, "context": "Reviewer is blocked again in a later iteration.", "source": "review-blocker:ITER-03"}
    )
    assert later_created and later["id"] != first["id"]


def test_long_blocker_text_is_truncated_and_disclosed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = RunConversationStore(repo, "run-long-blocker")
    blocker, created = store.publish_suggestion(
        kind="blocker",
        title="Decision needed: reviewer is blocked",
        context="Reviewer is blocked while checking an authorization rule.",
        rationale="Missing rule. " * 500,
        expected_impact="The review cannot complete until this is clarified.",
        recommendation="Clarify the access rule.",
        source="review-blocker:ITER-09",
    )
    assert created and blocker["status"] == "open"
    assert len(blocker["rationale"]) == MAX_SUGGESTION_LENGTH
    assert "rationale" in blocker["truncated_fields"]


def test_unreadable_feedback_store_does_not_discard_agent_response(tmp_path: Path) -> None:
    runner = RecordingRunner()
    orchestrator = _orchestrator(tmp_path, runner)
    orchestrator.conversation.path.write_text("{malformed", encoding="utf-8")

    for round_number in range(1, 3):
        orchestrator._invoke(
            role="reviewer",
            model=orchestrator.config.models["reviewer"],
            prompt="Review the implementation.",
            cwd=tmp_path,
            relative=f"iteration/review/round-{round_number}",
        )

    response = orchestrator.store.root / "iteration/review/round-2.response.md"
    assert response.read_text(encoding="utf-8") == "acknowledged\n"
    assert len(runner.requests) == 2
    assert len(orchestrator.state.warnings) == 1
    warning = orchestrator.state.warnings[0]
    assert "Run conversation storage unavailable" in warning
    assert "count=2" in warning
    assert str(orchestrator.conversation.path) not in warning

    suggestions = [
        {
            "kind": "suggestion",
            "title": "Review the old response envelope.",
            "context": "Reviewer assessed the public response.",
            "rationale": "The old shape may still be used by clients.",
            "expected_impact": "Compatibility remains stable.",
            "recommendation": "Keep the existing response envelope.",
            "target_role": "planner",
        }
    ]
    for _ in range(2):
        orchestrator._publish_agent_suggestions(
            role="reviewer",
            source="review",
            summary="The review passed.",
            suggestions=suggestions,
            blocker="",
        )
    assert len(orchestrator.state.warnings) == 2
    assert all("count=2" in item for item in orchestrator.state.warnings)
    assert all(str(orchestrator.conversation.path) not in item for item in orchestrator.state.warnings)


def test_feedback_delivery_status_write_failure_preserves_agent_result(tmp_path: Path) -> None:
    runner = RecordingRunner()
    orchestrator = _orchestrator(tmp_path, runner)
    item, _ = orchestrator.conversation.add_feedback(
        "Keep the existing response envelope.", target_role="reviewer", phase="coding"
    )

    def fail_delivery(*_args, **_kwargs):
        raise OSError("simulated feedback storage failure")

    orchestrator.conversation.complete_delivery = fail_delivery
    orchestrator._invoke(
        role="reviewer",
        model=orchestrator.config.models["reviewer"],
        prompt="Review the implementation.",
        cwd=tmp_path,
        relative="iteration/review/round-1",
    )

    response = orchestrator.store.root / "iteration/review/round-1.response.md"
    assert response.read_text(encoding="utf-8") == "acknowledged\n"
    assert item["id"] in runner.requests[0].prompt
    assert "response is preserved" in orchestrator.state.warnings[0]
    assert str(orchestrator.conversation.path) not in orchestrator.state.warnings[0]
