"""Continuous sprint orchestration with durable product and quality gates."""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .agents import (
    AgentCancelled,
    AgentConfigurationFailure,
    AgentFailure,
    AgentRequest,
    AgentRunner,
    AgentTimeout,
    AgentUsageLimit,
    is_usage_limit,
)
from .artifacts import ArtifactStore, utc_now
from .catalog import ROLE_TIMEOUTS, model_family, model_identity, spec_with_effort
from .contracts import (
    ITERATION_PLAN_SCHEMA,
    ITERATION_REVIEW_SCHEMA,
    ITERATION_TEST_SCHEMA,
    PRODUCT_OWNER_SCHEMA,
    ContractError,
    parse_iteration_plan,
    parse_iteration_review,
    parse_iteration_test,
    parse_product_owner,
)
from .display import optional_virtual_display
from .gitops import CandidateWorktree, GitError, GitWorkspace, export_revision
from .locking import RepositoryExecutionLock
from .models import AgentResult, ModelSpec, ROLE_NAMES, RunConfig, RunState
from .prompts import (
    implementation_prompt,
    iteration_reviewer_prompt,
    product_owner_prompt,
    sprint_planner_prompt,
    unified_tester_prompt,
)
from .sprint import SPRINT_SCHEDULE, assert_sprint_cursor, compact_iteration, ready_stories, slot_kind
from .validation import run_commands


PROBE_PROMPT = "Reply with the single word ready."
EVENT_QUEUE_LIMIT = 256
EVENT_SHUTDOWN_TIMEOUT_SECONDS = 1.0
_EVENT_STOP = object()
_NO_CALLBACK_CONTEXT = object()


def _product_owner_retry_prompt(error: Exception, *, inspected: bool) -> str:
    if not inspected:
        return (
            "Forge rejected this attempt because no product inspection tool call was observed. "
            "Inspect and exercise the product with tools now, then return the complete corrected "
            f"Product Owner JSON object only. Contract detail: {error}"
        )
    return (
        "Your product inspection remains valid, but Forge rejected the final JSON: "
        f"{error}. Return the complete corrected Product Owner JSON object only."
    )


class RunCancelled(RuntimeError):
    pass


class RunInterrupted(RuntimeError):
    pass


class IterationStalled(RuntimeError):
    def __init__(self, message: str, *, recoverable: bool = False):
        super().__init__(message)
        self.recoverable = recoverable


class ForgeOrchestrator:
    """Drive Product Owner sprints until the operator pauses or cancels the run."""

    def __init__(
        self,
        config: RunConfig,
        *,
        run_id: str | None = None,
        runner: AgentRunner | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        state_home: Path | None = None,
        check_binaries: bool = True,
        resume: bool = False,
    ):
        if not resume:
            config.validate()
        self.config = config
        self.repo = Path(config.repo).expanduser().resolve()
        self.brief_path = Path(config.brief).expanduser().resolve()
        self.run_id = run_id or time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        self.runner = runner or AgentRunner()
        self.store = ArtifactStore(self.repo, self.run_id)
        self.on_event = on_event
        default_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
        self.state_home = (state_home or default_home / "forge").expanduser().resolve()
        self.brain_dir = self.state_home / "brains" / self.run_id
        self.worktree_root = self.state_home / "worktrees" / self.run_id
        self.tester_root = self.state_home / "testers" / self.run_id
        self.check_binaries = check_binaries
        now = utc_now()
        if resume:
            self.state = self.store.load_state()
            self.config = RunConfig.from_dict(self.state.config)
            self.config.repo = str(self.repo)
            self.state.config = self.config.to_dict()
            self.brief_path = Path(self.config.brief).expanduser().resolve()
        else:
            self.state = RunState(
                run_id=self.run_id,
                status="created",
                phase="preflight",
                created_at=now,
                updated_at=now,
                config=config.to_dict(),
                original_models={
                    role: {
                        "provider": spec.provider,
                        "model": spec.model,
                        "effort": spec.effort,
                    }
                    for role, spec in config.models.items()
                    if role in ROLE_NAMES
                },
            )
        self._control = threading.Condition()
        self._state_lock = threading.RLock()
        self._activity_lock = threading.Lock()
        self._roster_lock = threading.Lock()
        self._event_lock = threading.Lock()
        self._event_queue: queue.Queue[dict[str, Any] | object] | None = None
        self._event_thread: threading.Thread | None = None
        self._event_accepting = False
        self._event_generation: int | None = None
        self._execution_generation = 0
        self._active_execution_generation: int | None = None
        self._callback_context = threading.local()
        self._control_revision = 0
        self._last_execution_control_revision = 0
        self._interrupt_requested = False
        self._execution_controls_sealed = True
        self._repository_lock_owned = False
        self._workspace: GitWorkspace | None = None

    @classmethod
    def from_existing(
        cls,
        repo: Path,
        run_id: str,
        *,
        runner: AgentRunner | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        state_home: Path | None = None,
        check_binaries: bool = True,
    ) -> "ForgeOrchestrator":
        store = ArtifactStore(Path(repo), run_id)
        state = store.load_state()
        config = RunConfig.from_dict(state.config)
        config.repo = str(Path(repo).expanduser().resolve())
        return cls(
            config,
            run_id=run_id,
            runner=runner,
            on_event=on_event,
            state_home=state_home,
            check_binaries=check_binaries,
            resume=True,
        )

    # Public controls -------------------------------------------------

    def pause(self) -> None:
        with self._control:
            if self._control_is_from_stale_callback():
                return
            with self._control_persistence():
                with self._state_lock:
                    self._control_revision += 1
                    self.state.paused = True
                    if self.state.status == "running":
                        self.state.status = "paused"
                    self._save("Pause requested; Forge will wait at the next durable boundary.")

    def resume(self) -> None:
        with self._control:
            if self._control_is_from_stale_callback():
                return
            with self._control_persistence():
                with self._state_lock:
                    self._control_revision += 1
                    self.state.paused = False
                    if self.state.status == "paused" and not self.state.cancel_requested:
                        self.state.status = "running"
                    self._control.notify_all()
                    self._save("Run resumed.")

    def cancel(self) -> None:
        with self._control:
            if self._control_is_from_stale_callback():
                return
            with self._control_persistence():
                with self._state_lock:
                    self._control_revision += 1
                    self.state.cancel_requested = True
                    self.state.paused = False
                    self._control.notify_all()
                    self._save("Cancellation requested.")
        self._runner_cancel()

    def mark_interrupted(self, message: str) -> None:
        with self._control:
            if self._control_is_from_stale_callback():
                return
            with self._control_persistence():
                with self._state_lock:
                    self._control_revision += 1
                    self._interrupt_requested = True
                    with self._activity_lock:
                        self.state.active_agents.clear()
                    if self.state.status in {"running", "paused", "created"}:
                        self.state.status = "failed"
                        self.state.paused = False
                        self._control.notify_all()
                        self._save(message)
                        self._seal_execution_controls()
        self._runner_cancel()

    def _control_is_from_stale_callback(self) -> bool:
        callback_generation = getattr(
            self._callback_context, "execution_generation", _NO_CALLBACK_CONTEXT
        )
        return (
            callback_generation is not _NO_CALLBACK_CONTEXT
            and (
                callback_generation != self._active_execution_generation
                or self._execution_controls_sealed
            )
        )

    @contextmanager
    def _control_persistence(self) -> Iterator[None]:
        execution_lock: RepositoryExecutionLock | None = None
        if not (
            self._active_execution_generation is not None
            and self._repository_lock_owned
        ):
            execution_lock = RepositoryExecutionLock(
                self.repo, self.config.branch, self.run_id
            )
            execution_lock.acquire()
            try:
                with self._state_lock:
                    if self.store.state_path.is_file():
                        self._reload_persisted_state()
            except Exception:
                execution_lock.release()
                raise
        try:
            yield
        finally:
            if execution_lock is not None:
                execution_lock.release()

    def _reload_persisted_state(self) -> None:
        state = self.store.load_state()
        config = RunConfig.from_dict(state.config)
        config.repo = str(self.repo)
        state.config = config.to_dict()
        self.state = state
        self.config = config
        self.brief_path = Path(config.brief).expanduser().resolve()

    def _begin_execution(self, *, recover: bool) -> int:
        with self._control:
            with self._state_lock:
                if self._active_execution_generation is not None:
                    raise RuntimeError("this controller already has an active execution")
                has_new_control = (
                    self._control_revision != self._last_execution_control_revision
                )
                if not has_new_control:
                    self._interrupt_requested = False
                    if recover:
                        self.state.cancel_requested = False
                        self.state.paused = False
                self._execution_generation += 1
                self._active_execution_generation = self._execution_generation
                self._execution_controls_sealed = False
                return self._execution_generation

    def _seal_execution_controls(self) -> None:
        with self._control:
            if (
                self._active_execution_generation is not None
                and not self._execution_controls_sealed
            ):
                self._last_execution_control_revision = self._control_revision
                self._execution_controls_sealed = True

    def _finish_execution(self, generation: int) -> None:
        with self._control:
            if self._active_execution_generation == generation:
                self._active_execution_generation = None
                self._execution_controls_sealed = True

    def _acquire_execution(
        self, *, recover: bool, reload_state: bool = False
    ) -> tuple[RepositoryExecutionLock, int]:
        execution_lock = RepositoryExecutionLock(
            self.repo, self.config.branch, self.run_id
        )
        with self._control:
            if self._active_execution_generation is not None:
                raise RuntimeError("this controller already has an active execution")
            execution_lock.acquire()
            self._repository_lock_owned = True
            try:
                with self._state_lock:
                    if reload_state:
                        self._reload_persisted_state()
                    if recover:
                        self._validate_recoverable_state()
                generation = self._begin_execution(recover=recover)
            except Exception:
                self._repository_lock_owned = False
                execution_lock.release()
                raise
        return execution_lock, generation

    def _release_execution(
        self, execution_lock: RepositoryExecutionLock, generation: int
    ) -> None:
        with self._control:
            self._finish_execution(generation)
            self._repository_lock_owned = False
            execution_lock.release()
        self._shutdown_event_dispatcher()

    def run(self) -> RunState:
        if self.state.schema_version != 2:
            raise RuntimeError("legacy Forge runs are read-only and cannot enter sprint mode")
        execution_lock, generation = self._acquire_execution(recover=False)
        try:
            self._runner_allow()
            self._ensure_event_dispatcher(generation)
            self.store.write_data("config.json", self.config.to_dict())
            self.store.write_text("brief.md", self.brief_path.read_text(encoding="utf-8"))
            with self._state_lock:
                if not self._interrupt_requested:
                    self.state.status = "running"
                    self.state.stalled_recoverable = False
                    self._save("Starting continuous Forge sprint run.")
            return self._execute(recover=False)
        finally:
            self._release_execution(execution_lock, generation)

    def recover(self) -> RunState:
        execution_lock, generation = self._acquire_execution(
            recover=True, reload_state=True
        )
        return self._recover_execution(execution_lock, generation)

    def _validate_recoverable_state(self) -> None:
        if self.state.schema_version != 2:
            raise RuntimeError("legacy Forge runs cannot be recovered by the sprint orchestrator")
        if self.state.status not in {"failed", "paused", "cancelled", "running", "stalled"}:
            raise RuntimeError(
                f"run {self.run_id} is {self.state.status}; it is not recoverable"
            )
        if self.state.status == "stalled" and not self.state.stalled_recoverable:
            raise RuntimeError(
                "this run stalled at a deterministic safety limit; start a new run "
                "or change its durable product input instead of retrying the same phase"
            )

    def _recover_execution(
        self, execution_lock: RepositoryExecutionLock, generation: int
    ) -> RunState:
        try:
            self._runner_allow()
            self._ensure_event_dispatcher(generation)
            with self._state_lock:
                self.state.active_agents.clear()
                if not self._interrupt_requested:
                    self.state.status = "running"
                    self.state.stalled_recoverable = False
                    self._save("Recovering the continuous sprint from its durable phase.")
            return self._execute(recover=True)
        finally:
            self._release_execution(execution_lock, generation)

    def recover_failed(self) -> RunState:
        execution_lock, generation = self._acquire_execution(
            recover=True, reload_state=True
        )
        return self._recover_execution(execution_lock, generation)

    def activity_snapshot(self) -> dict[str, dict[str, Any]]:
        with self._state_lock:
            with self._activity_lock:
                return {key: dict(value) for key, value in self.state.active_agents.items()}

    def state_snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            return self.state.to_dict()

    # Run driver ------------------------------------------------------

    def _execute(self, *, recover: bool) -> RunState:
        try:
            self._checkpoint()
            self._preflight()
            if not self.state.preflight_probed:
                self._probe_models()
            result = self._drive()
            self._seal_execution_controls()
            return result
        except RunCancelled:
            with self._control:
                with self._state_lock:
                    self.state.status = "cancelled"
                    self.state.phase = "cancelled"
                    self._save("Run cancelled by the operator; the active sprint is preserved.")
                self._seal_execution_controls()
            return self.state
        except RunInterrupted:
            self._seal_execution_controls()
            return self.state
        except IterationStalled as exc:
            with self._control:
                with self._state_lock:
                    self.state.status = "stalled"
                    self.state.stalled_recoverable = exc.recoverable
                    self._warning(f"Iteration stalled: {exc}")
                    self._save(str(exc))
                self._seal_execution_controls()
            return self.state
        except Exception as exc:
            with self._control:
                with self._state_lock:
                    self.state.status = "failed"
                    self._warning(f"Run failed: {exc}")
                    self._save(str(exc))
                self._seal_execution_controls()
            raise

    def _drive(self) -> RunState:
        self._reconcile_sprint_state()
        while True:
            self._checkpoint()
            assert_sprint_cursor(self.state.sprint_iteration)
            if self.state.active_iteration:
                self._run_iteration()
                continue
            if self.state.needs_product_owner:
                self._run_product_owner()
                continue
            if self.state.sprint_iteration == len(SPRINT_SCHEDULE):
                self._close_sprint()
                continue
            self._run_iteration()

    def _reconcile_sprint_state(self) -> None:
        """Derive cursors from accepted records after any interrupted state write."""

        with self._state_lock:
            changed = False
            if self.state.cycle != len(self.state.iterations):
                self.state.cycle = len(self.state.iterations)
                changed = True
            current = sorted(
                (
                    item
                    for item in self.state.iterations
                    if int(item.get("sprint") or -1) == self.state.sprint_number
                ),
                key=lambda item: int(item.get("slot") or 0),
            )
            slots = [int(item.get("slot") or 0) for item in current]
            if slots != list(range(1, len(current) + 1)):
                raise RuntimeError(
                    f"accepted iteration slots are not contiguous in sprint {self.state.sprint_number}: {slots}"
                )
            kinds = tuple(str(item.get("kind")) for item in current)
            if kinds != SPRINT_SCHEDULE[: len(kinds)]:
                raise RuntimeError(f"accepted iteration schedule drifted: {kinds}")
            completed = any(
                int(item.get("sprint") or -1) == self.state.sprint_number
                for item in self.state.completed_sprints
            )
            expected_cursor = 0 if completed else len(current)
            if self.state.sprint_iteration != expected_cursor:
                self.state.sprint_iteration = expected_cursor
                changed = True
            if completed and not self.state.needs_product_owner:
                self.state.needs_product_owner = True
                changed = True
            if changed:
                self._save("Reconciled sprint cursor from durable accepted iterations.")

    def _preflight(self) -> None:
        self._phase("preflight", "Checking providers and target repository.")
        if self.check_binaries:
            for role in ROLE_NAMES:
                model = self.config.models[role]
                if shutil.which(model.provider) is None:
                    raise ValueError(f"{role} provider executable is unavailable: {model.provider}")
        self.brain_dir.mkdir(parents=True, exist_ok=True)
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        self.tester_root.mkdir(parents=True, exist_ok=True)
        self._workspace = GitWorkspace(
            self.repo,
            self.config.branch,
            self.run_id,
            self.worktree_root,
            local_excludes=self._brief_local_excludes(),
        )
        head = self._workspace.prepare(require_remote=self.config.push)
        self.store.write_data("git.json", {"branch": self.config.branch, "head": head})
        self._save(f"Preflight passed at {head[:12]} on {self.config.branch}.")

    # Product Owner --------------------------------------------------

    def _run_product_owner(self) -> None:
        assert self._workspace is not None
        visit = self.state.backlog_revision + 1
        self._phase("product-owner", f"Product Owner is inspecting the product for sprint {self.state.sprint_number + 1}.")
        visit_root = self.brain_dir / f"visit-{visit:03d}"
        snapshot = visit_root / "product"
        commit = export_revision(self.repo, snapshot)
        evidence = snapshot / ".forge-product-evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        previous_ready = ready_stories(self.state.backlog)
        prompt = product_owner_prompt(
            brief=self._brief_text(),
            commit=commit,
            previous_backlog=previous_ready,
            completed_iterations=[compact_iteration(item) for item in self.state.iterations],
            quality_backlog=[item for item in self.state.quality_backlog if item.get("status") != "resolved"],
            evidence_dir=evidence,
            virtual_display=None,
        )
        session = self.state.brain_session_id
        inspected = self.state.product_owner_inspected
        with optional_virtual_display() as server:
            if server is not None:
                prompt = product_owner_prompt(
                    brief=self._brief_text(),
                    commit=commit,
                    previous_backlog=previous_ready,
                    completed_iterations=[compact_iteration(item) for item in self.state.iterations],
                    quality_backlog=[
                        item for item in self.state.quality_backlog if item.get("status") != "resolved"
                    ],
                    evidence_dir=evidence,
                    virtual_display=server.display,
                )
            environment = {} if server is None else server.environment()
            for attempt in range(1, 4):
                result = self._invoke(
                    role="brain",
                    model=self.config.models["brain"],
                    prompt=prompt,
                    cwd=snapshot,
                    session_id=session,
                    access="inspect",
                    schema=PRODUCT_OWNER_SCHEMA,
                    extra_writable_dirs=(snapshot, evidence),
                    environment=environment,
                    relative=f"product-owner/visit-{visit:03d}/attempt-{attempt}",
                    invocation=attempt,
                )
                session = result.session_id or session
                with self._state_lock:
                    self.state.brain_session_id = session
                    if result.tool_calls:
                        inspected = True
                        self.state.product_owner_inspected = True
                    self.store.save_state(self.state)
                try:
                    if not inspected:
                        raise ContractError(
                            "Product Owner must inspect the product with at least one tool call"
                        )
                    decision = parse_product_owner(
                        result.text,
                        previous_ready=tuple(str(item["id"]) for item in previous_ready),
                        accepted_story_ids=tuple(self.state.accepted_story_ids),
                    )
                except ContractError as exc:
                    self._warning(f"Product Owner contract retry {attempt}: {exc}")
                    prompt = _product_owner_retry_prompt(exc, inspected=inspected)
                    continue
                self.store.write_data(f"product-owner/visit-{visit:03d}/decision.json", decision)
                self.store.write_data(f"backlog/revision-{visit:03d}.json", decision["stories"])
                artifact_evidence = self.store.root / f"product-owner/visit-{visit:03d}/evidence"
                if evidence.is_dir():
                    shutil.copytree(evidence, artifact_evidence, dirs_exist_ok=True)
                with self._state_lock:
                    self.state.backlog_revision = visit
                    self.state.backlog = decision["stories"]
                    self.state.sprint_goal = str(decision["sprint_goal"])
                    self.state.sprint_number += 1
                    self.state.sprint_iteration = 0
                    self.state.sprint_started_at = utc_now()
                    self.state.needs_product_owner = False
                    self.state.brain_session_id = None
                    self.state.product_owner_inspected = False
                    self.state.active_iteration = {}
                    self._save(
                        f"Product Owner prepared {decision['capacity']['stories']} stories "
                        f"({decision['capacity']['estimated_minutes']} estimated minutes)."
                    )
                return
        raise RuntimeError("Product Owner failed its backlog contract three times")

    def _close_sprint(self) -> None:
        with self._state_lock:
            if self.state.sprint_iteration != len(SPRINT_SCHEDULE):
                raise RuntimeError("cannot close an incomplete sprint")
            accepted = sorted(
                (
                    item
                    for item in self.state.iterations
                    if int(item.get("sprint") or -1) == self.state.sprint_number
                ),
                key=lambda item: int(item.get("slot") or 0),
            )
            kinds = tuple(str(item.get("kind")) for item in accepted)
            if kinds != SPRINT_SCHEDULE:
                raise RuntimeError(f"sprint schedule drifted: {kinds}")
            existing = next(
                (
                    item
                    for item in self.state.completed_sprints
                    if int(item.get("sprint") or -1) == self.state.sprint_number
                ),
                None,
            )
            summary = existing or {
                "sprint": self.state.sprint_number,
                "started_at": self.state.sprint_started_at,
                "completed_at": utc_now(),
                "iterations": [str(item.get("id")) for item in accepted],
                "kinds": list(kinds),
            }
            self.store.write_data(
                f"sprints/{self.state.sprint_number:03d}/summary.json", summary
            )
            if existing is None:
                self.state.completed_sprints.append(summary)
            self.state.sprint_iteration = 0
            self.state.sprint_started_at = ""
            self.state.needs_product_owner = True
            self.state.brain_session_id = None
            self.state.product_owner_inspected = False
            self.state.active_iteration = {}
            self._save(
                f"Sprint {self.state.sprint_number} completed; returning to a fresh Product Owner."
            )

    # Iteration state machine ---------------------------------------

    def _run_iteration(self) -> None:
        if not self.state.active_iteration:
            self._start_iteration()
        while self.state.active_iteration:
            self._checkpoint()
            phase = str(self.state.active_iteration.get("phase") or "planning")
            if phase == "planning":
                self._plan_iteration()
            elif phase == "coding":
                self._code_iteration()
            elif phase == "review":
                self._review_iteration()
            elif phase == "testing":
                self._test_iteration()
            elif phase == "delivery":
                self._deliver_iteration()
            elif phase == "finalizing":
                self._finalize_iteration()
            else:
                raise RuntimeError(f"unknown iteration phase: {phase}")

    def _start_iteration(self) -> None:
        assert self._workspace is not None
        slot = self.state.sprint_iteration
        kind = slot_kind(slot)
        if not ready_stories(self.state.backlog, kind):
            raise IterationStalled(
                f"sprint {self.state.sprint_number} slot {slot + 1} has no ready {kind} story"
            )
        iteration_id = f"S{self.state.sprint_number:03d}-I{slot + 1:02d}"
        with self._state_lock:
            self.state.active_iteration = {
                "id": iteration_id,
                "sprint": self.state.sprint_number,
                "slot": slot + 1,
                "kind": kind,
                "phase": "planning",
                "base_sha": self._workspace.target_head(),
                "story_id": "",
                "plan": {},
                "planner_session": None,
                "coder_session": None,
                "reviewer_session": None,
                "tester_session": None,
                "coder_round": 0,
                "review_round": 0,
                "tester_round": 0,
                "unchanged_rounds": 0,
                "coder_inflight": False,
                "coder_start_fingerprint": "",
                "fingerprint": "",
                "tree": "",
                "reviewed_fingerprint": "",
                "reviewed_tree": "",
                "tested_fingerprint": "",
                "tested_tree": "",
                "review_findings": [],
                "test_findings": [],
                "review": {},
                "test": {},
                "validation": [],
                "delivery_commit": "",
                "implementation_summary": "",
                "nits": [],
            }
            self._save(
                f"Started sprint {self.state.sprint_number} slot {slot + 1}/10 ({kind})."
            )

    def _iteration_rel(self) -> str:
        active = self.state.active_iteration
        return (
            f"sprints/{int(active['sprint']):03d}/iterations/"
            f"{int(active['slot']):02d}"
        )

    def _plan_iteration(self) -> None:
        active = self.state.active_iteration
        self._phase(
            "planning",
            f"Planner is preparing {active['kind']} slot {active['slot']}/10.",
        )
        context, _empty = self._planner_context()
        prompt = sprint_planner_prompt(
            brief=self._brief_text(),
            sprint=int(active["sprint"]),
            sprint_goal=self.state.sprint_goal,
            slot=int(active["slot"]),
            kind=str(active["kind"]),
            backlog=ready_stories(self.state.backlog),
            quality_backlog=[item for item in self.state.quality_backlog if item.get("status") != "resolved"],
            repository_context=context,
            environment_context=self._environment_context(),
        )
        session = active.get("planner_session")
        available_nits = tuple(
            str(item["id"])
            for item in self.state.quality_backlog
            if item.get("status") != "resolved"
        )
        for attempt in range(1, 4):
            result = self._invoke(
                role="planner",
                model=self.config.models["planner"],
                prompt=prompt,
                cwd=self.repo,
                session_id=session,
                access="read",
                schema=ITERATION_PLAN_SCHEMA,
                relative=f"{self._iteration_rel()}/planner/attempt-{attempt}",
                invocation=attempt,
            )
            session = result.session_id or session
            with self._state_lock:
                active["planner_session"] = session
                self.store.save_state(self.state)
            try:
                plan = parse_iteration_plan(
                    result.text,
                    backlog=self.state.backlog,
                    required_kind=str(active["kind"]),
                    available_nit_ids=available_nits,
                )
            except ContractError as exc:
                prompt = f"Forge rejected the plan JSON: {exc}. Return the complete corrected JSON only."
                continue
            self.store.write_data(f"{self._iteration_rel()}/plan.json", plan)
            with self._state_lock:
                active["story_id"] = plan["story_id"]
                active["plan"] = plan
                active["phase"] = "coding"
                for story in self.state.backlog:
                    if story.get("id") == plan["story_id"]:
                        story["status"] = "selected"
                        break
                self._save(f"Planner selected {plan['story_id']}: {plan['objective']}")
            return
        raise RuntimeError("planner failed its iteration contract three times")

    def _candidate(self) -> CandidateWorktree:
        assert self._workspace is not None
        return self._workspace.create_or_reattach(
            str(self.state.active_iteration["base_sha"]), recover=True
        )

    def _code_iteration(self) -> None:
        assert self._workspace is not None
        active = self.state.active_iteration
        candidate = self._candidate()
        current_capture = self._workspace.capture(candidate)
        current_fingerprint = str(current_capture["fingerprint"])
        if active.get("coder_inflight"):
            before = str(active.get("coder_start_fingerprint") or "")
            with self._state_lock:
                active["coder_inflight"] = False
                if before and current_fingerprint != before:
                    active["fingerprint"] = current_fingerprint
                    active["tree"] = str(current_capture["tree"])
                    active["phase"] = "review"
                    self._save(
                        "Recovered edits left by an interrupted coder; reviewing instead of replaying them."
                    )
                    return
        if int(active.get("coder_round") or 0) >= self.config.max_revision_rounds:
            raise IterationStalled(
                f"coder exceeded {self.config.max_revision_rounds} rounds for {active['id']}"
            )

        self._phase("coding", f"Coder is implementing {active['story_id']}.")
        with self._state_lock:
            active["coder_round"] = int(active.get("coder_round") or 0) + 1
            active["coder_inflight"] = True
            active["coder_start_fingerprint"] = current_fingerprint
            self.store.save_state(self.state)
            coder_round = int(active["coder_round"])
        prompt = implementation_prompt(
            plan=dict(active["plan"]),
            blocking_findings=list(active.get("review_findings") or []),
            tester_feedback=list(active.get("test_findings") or []),
            previous_summary=str(active.get("implementation_summary") or ""),
        )
        result = self._invoke(
            role="coder",
            model=self.config.models["coder"],
            prompt=prompt,
            cwd=candidate.path,
            session_id=active.get("coder_session"),
            access="write",
            relative=f"{self._iteration_rel()}/coder/round-{coder_round}",
            invocation=coder_round,
            failover_on_timeout=True,
        )
        capture = self._workspace.capture(candidate)
        fingerprint = str(capture["fingerprint"])
        self.store.write_text(
            f"{self._iteration_rel()}/coder/round-{coder_round}.patch",
            capture["patch"],
        )
        self.store.write_data(
            f"{self._iteration_rel()}/coder/round-{coder_round}.json",
            {
                "summary": result.text,
                "fingerprint": fingerprint,
                "status": capture["status"],
                "diffstat": capture["diffstat"],
            },
        )
        with self._state_lock:
            active["coder_session"] = result.session_id or active.get("coder_session")
            active["implementation_summary"] = result.text.strip()
            active["coder_inflight"] = False
            if fingerprint == current_fingerprint:
                active["unchanged_rounds"] = int(active.get("unchanged_rounds") or 0) + 1
            else:
                active["unchanged_rounds"] = 0
            if int(active["unchanged_rounds"]) >= self.config.stalled_turns:
                raise IterationStalled(
                    f"coder made no workspace progress for {active['unchanged_rounds']} rounds"
                )
            active["fingerprint"] = fingerprint
            active["tree"] = str(capture["tree"])
            active["reviewed_fingerprint"] = ""
            active["reviewed_tree"] = ""
            active["tested_fingerprint"] = ""
            active["tested_tree"] = ""
            active["phase"] = "review"
            self._save(f"Coder round {coder_round} is ready for review.")

    def _validation_copy(self, candidate: CandidateWorktree, name: str) -> Path:
        assert self._workspace is not None
        destination = self.tester_root / f"{self.state.active_iteration['id']}-{name}"
        if destination.exists():
            shutil.rmtree(destination)
        return self._workspace.create_disposable_copy(candidate, destination)

    def _review_iteration(self) -> None:
        assert self._workspace is not None
        active = self.state.active_iteration
        if int(active.get("review_round") or 0) >= self.config.max_revision_rounds:
            raise IterationStalled(
                f"reviewer exceeded {self.config.max_revision_rounds} rounds for {active['id']}"
            )
        candidate = self._candidate()
        capture = self._workspace.capture(candidate)
        fingerprint = str(capture["fingerprint"])
        tree = str(capture["tree"])
        validation_copy = self._validation_copy(candidate, "review-validation")
        try:
            validation = run_commands(
                tuple(str(item) for item in active["plan"]["validation_commands"]),
                validation_copy,
            )
        finally:
            shutil.rmtree(validation_copy, ignore_errors=True)
        self._checkpoint()
        with self._state_lock:
            active["fingerprint"] = fingerprint
            active["tree"] = tree
            active["validation"] = validation
            active["review_round"] = int(active.get("review_round") or 0) + 1
            review_round = int(active["review_round"])
            self.state.phase = "review"
            self._save(f"Reviewer is assessing coder round {active['coder_round']}.")
        prompt = iteration_reviewer_prompt(
            plan=dict(active["plan"]),
            fingerprint=fingerprint,
            validation=self._compact_validation(validation),
            previous_findings=list(active.get("review_findings") or []),
        )
        session = active.get("reviewer_session")
        task_ids = tuple(str(item["id"]) for item in active["plan"]["tasks"])
        for attempt in range(1, 4):
            result = self._invoke(
                role="reviewer",
                model=self.config.models["reviewer"],
                prompt=prompt,
                cwd=candidate.path,
                session_id=session,
                access="read",
                schema=ITERATION_REVIEW_SCHEMA,
                relative=f"{self._iteration_rel()}/review/round-{review_round}-attempt-{attempt}",
                invocation=review_round,
            )
            session = result.session_id or session
            with self._state_lock:
                active["reviewer_session"] = session
                self.store.save_state(self.state)
            try:
                review = parse_iteration_review(
                    result.text,
                    task_ids=task_ids,
                    expected_fingerprint=fingerprint,
                    validation_results=validation,
                )
            except ContractError as exc:
                prompt = f"Forge rejected the review JSON: {exc}. Return the complete corrected JSON only."
                continue
            self.store.write_data(
                f"{self._iteration_rel()}/review/round-{review_round}.json",
                review,
            )
            with self._state_lock:
                self._record_nits(review["nits"], source="review")
                active["nits"].extend(
                    item for item in review["nits"] if item not in active["nits"]
                )
                active["review"] = review
                if review["verdict"] == "blocked":
                    active["review_round"] = max(0, review_round - 1)
                    raise IterationStalled(
                        f"reviewer blocked: {review['blocker']}", recoverable=True
                    )
                if review["verdict"] == "reject":
                    active["review_findings"] = review["blocking_findings"]
                    active["test_findings"] = []
                    active["phase"] = "coding"
                    self._save(
                        f"Reviewer rejected round {active['coder_round']} with "
                        f"{len(review['blocking_findings'])} blocker(s)."
                    )
                    return
                active["review_findings"] = []
                active["reviewed_fingerprint"] = fingerprint
                active["reviewed_tree"] = tree
                active["phase"] = "testing"
                self._save("Reviewer accepted the implementation; unified testing may begin.")
                return
        raise RuntimeError("reviewer failed its contract three times")

    def _test_iteration(self) -> None:
        assert self._workspace is not None
        active = self.state.active_iteration
        if int(active.get("tester_round") or 0) >= self.config.max_revision_rounds:
            raise IterationStalled(
                f"tester exceeded {self.config.max_revision_rounds} rounds for {active['id']}"
            )
        candidate = self._candidate()
        capture = self._workspace.capture(candidate)
        fingerprint = str(capture["fingerprint"])
        tree = str(capture["tree"])
        if fingerprint != active.get("reviewed_fingerprint") or tree != active.get(
            "reviewed_tree"
        ):
            with self._state_lock:
                active["phase"] = "review"
                self._save("Implementation changed after review; returning to reviewer.")
            return
        self._checkpoint()
        with self._state_lock:
            active["tester_round"] = int(active.get("tester_round") or 0) + 1
            tester_round = int(active["tester_round"])
            self.state.phase = "testing"
            self._save(f"Unified tester is checking {active['story_id']}.")
        product_copy = self._validation_copy(candidate, "tester")
        evidence = product_copy / ".forge-test-evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        artifact_evidence = (
            self.store.root
            / self._iteration_rel()
            / "tester"
            / f"evidence-round-{tester_round}-{fingerprint[:12]}"
        )
        if artifact_evidence.exists():
            shutil.rmtree(artifact_evidence)
        validation = run_commands(
            tuple(str(item) for item in active["plan"]["validation_commands"]),
            product_copy,
        )
        with self._state_lock:
            active["validation"] = validation
            self.store.save_state(self.state)
        task_ids = tuple(str(item["id"]) for item in active["plan"]["tasks"])
        session = active.get("tester_session")
        try:
            with optional_virtual_display() as server:
                prompt = unified_tester_prompt(
                    plan=dict(active["plan"]),
                    fingerprint=fingerprint,
                    review=dict(active["review"]),
                    validation=self._compact_validation(validation),
                    evidence_dir=evidence,
                    virtual_display=None if server is None else server.display,
                )
                environment = {} if server is None else server.environment()
                for attempt in range(1, 4):
                    result = self._invoke(
                        role="tester",
                        model=self.config.models["tester"],
                        prompt=prompt,
                        cwd=product_copy,
                        session_id=session,
                        access="test",
                        schema=ITERATION_TEST_SCHEMA,
                        environment=environment,
                        relative=f"{self._iteration_rel()}/tester/round-{tester_round}-attempt-{attempt}",
                        invocation=tester_round,
                    )
                    session = result.session_id or session
                    with self._state_lock:
                        active["tester_session"] = session
                        self.store.save_state(self.state)
                    try:
                        report = parse_iteration_test(
                            result.text,
                            task_ids=task_ids,
                            expected_fingerprint=fingerprint,
                            validation_results=validation,
                            public_checks=tuple(
                                str(item) for item in active["plan"]["public_checks"]
                            ),
                        )
                    except ContractError as exc:
                        prompt = (
                            f"Forge rejected the tester JSON: {exc}. "
                            "Return the complete corrected JSON only."
                        )
                        continue
                    self.store.write_data(
                        f"{self._iteration_rel()}/tester/round-{tester_round}.json",
                        report,
                    )
                    with self._state_lock:
                        self._record_nits(report["nits"], source="tester")
                        active["nits"].extend(
                            item for item in report["nits"] if item not in active["nits"]
                        )
                        active["test"] = report
                        if report["verdict"] == "blocked":
                            active["tester_round"] = max(0, tester_round - 1)
                            raise IterationStalled(
                                f"tester blocked: {report['blocker']}", recoverable=True
                            )
                        if report["verdict"] == "reject":
                            active["test_findings"] = report["blocking_findings"]
                            active["review_findings"] = []
                            active["reviewed_fingerprint"] = ""
                            active["reviewed_tree"] = ""
                            active["phase"] = "coding"
                            self._save(
                                f"Tester rejected the implementation with "
                                f"{len(report['blocking_findings'])} blocker(s); returning to coder."
                            )
                            return
                        active["test_findings"] = []
                        active["tested_fingerprint"] = fingerprint
                        active["tested_tree"] = tree
                        active["phase"] = "delivery"
                        self._save("Unified tester accepted the reviewed implementation.")
                        return
                raise RuntimeError("tester failed its contract three times")
        finally:
            if evidence.is_dir():
                shutil.copytree(evidence, artifact_evidence, dirs_exist_ok=True)
            shutil.rmtree(product_copy, ignore_errors=True)

    def _deliver_iteration(self) -> None:
        assert self._workspace is not None
        active = self.state.active_iteration
        candidate = self._candidate()
        capture = self._workspace.capture(candidate)
        fingerprint = str(capture["fingerprint"])
        tree = str(capture["tree"])
        if fingerprint != active.get("tested_fingerprint") or tree != active.get(
            "tested_tree"
        ):
            with self._state_lock:
                active["phase"] = "review"
                self._save("Implementation changed after testing; invalidating acceptance.")
            return
        self._phase("delivery", f"Delivering accepted iteration {active['id']}.")
        intent = {
            "iteration": active["id"],
            "expected_base": active["base_sha"],
            "fingerprint": fingerprint,
            "tree": tree,
            "commit": active.get("delivery_commit") or "",
        }
        self.store.write_data(f"{self._iteration_rel()}/delivery-intent.json", intent)
        commit = str(active.get("delivery_commit") or "")
        if not commit:
            commit = self._workspace.prepare_commit(
                candidate,
                f"Forge {active['kind']} {active['id']}: {active['plan']['objective'][:60]}",
                expected_tree=tree,
            )
            intent["commit"] = commit
            self.store.write_data(f"{self._iteration_rel()}/delivery-intent.json", intent)
            with self._state_lock:
                active["delivery_commit"] = commit
                self.store.save_state(self.state)
        delivered = self._workspace.reconcile_delivery(
            candidate,
            str(active["base_sha"]),
            recorded_commit=commit,
            push=self.config.push,
        )
        self.store.write_data(
            f"{self._iteration_rel()}/delivery.json",
            {"commit": delivered, "branch": self.config.branch, "pushed": self.config.push},
        )
        with self._state_lock:
            active["delivery_commit"] = delivered
            active["phase"] = "finalizing"
            self._save(f"Iteration delivered at {delivered[:12]}.")

    def _finalize_iteration(self) -> None:
        assert self._workspace is not None
        active = self.state.active_iteration
        commit = str(active.get("delivery_commit") or "")
        if not commit or self._workspace.target_head() != commit:
            raise RuntimeError("cannot finalize an iteration that is not on the target branch")
        story_id = str(active["story_id"])
        record = {
            "id": active["id"],
            "sprint": active["sprint"],
            "slot": active["slot"],
            "kind": active["kind"],
            "story_id": story_id,
            "objective": active["plan"]["objective"],
            "commit": commit,
            "review_summary": active["review"].get("summary", ""),
            "test_summary": active["test"].get("summary", ""),
            "nits": list(active.get("nits") or []),
            "coder_rounds": active["coder_round"],
            "review_rounds": active["review_round"],
            "tester_rounds": active["tester_round"],
            "completed_at": utc_now(),
        }
        iteration_rel = self._iteration_rel_from_record(record)
        self.store.write_data(
            f"{iteration_rel}/delivery.json",
            {"commit": commit, "branch": self.config.branch, "pushed": self.config.push},
        )
        self.store.write_data(f"{iteration_rel}/acceptance.json", record)
        self._workspace.cleanup()
        with self._state_lock:
            existing = next(
                (
                    item
                    for item in self.state.iterations
                    if str(item.get("id")) == str(record["id"])
                ),
                None,
            )
            if existing is not None and (
                existing.get("commit") != commit
                or existing.get("story_id") != story_id
                or existing.get("kind") != record["kind"]
            ):
                raise RuntimeError("accepted iteration record conflicts with finalizing delivery")
            for story in self.state.backlog:
                if story.get("id") == story_id:
                    story["status"] = "accepted"
                    story["accepted_commit"] = commit
                    break
            if story_id not in self.state.accepted_story_ids:
                self.state.accepted_story_ids.append(story_id)
            addressed = set(active["plan"].get("addressed_nit_ids") or [])
            for nit in self.state.quality_backlog:
                if nit.get("id") in addressed:
                    nit["status"] = "resolved"
                    nit["resolved_iteration"] = active["id"]
            if existing is None:
                self.state.iterations.append(record)
            self.state.cycle = len(self.state.iterations)
            self.state.sprint_iteration = max(
                self.state.sprint_iteration, int(record["slot"])
            )
            self.state.active_iteration = {}
            self.state.checkpoint = {
                "next": "product-owner"
                if self.state.sprint_iteration == len(SPRINT_SCHEDULE)
                else "planning",
                "sprint": self.state.sprint_number,
                "slot": self.state.sprint_iteration,
            }
            self._save(
                f"Accepted {record['id']}; sprint progress is {self.state.sprint_iteration}/10."
            )

    @staticmethod
    def _iteration_rel_from_record(record: dict[str, Any]) -> str:
        return f"sprints/{int(record['sprint']):03d}/iterations/{int(record['slot']):02d}"

    # Small helpers --------------------------------------------------

    def _record_nits(self, values: list[str], *, source: str) -> None:
        with self._state_lock:
            active = self.state.active_iteration
            existing = {
                str(item.get("text", "")).strip().casefold()
                for item in self.state.quality_backlog
                if item.get("status") != "resolved"
            }
            for text in values:
                normalized = text.strip()
                if not normalized or normalized.casefold() in existing:
                    continue
                nit_id = f"NIT-{len(self.state.quality_backlog) + 1:04d}"
                self.state.quality_backlog.append(
                    {
                        "id": nit_id,
                        "text": normalized,
                        "source": source,
                        "iteration": active.get("id"),
                        "status": "ready",
                    }
                )
                existing.add(normalized.casefold())

    @staticmethod
    def _compact_validation(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        compact: list[dict[str, Any]] = []
        for item in results:
            compact.append(
                {
                    "command": item.get("command"),
                    "kind": item.get("kind"),
                    "return_code": item.get("return_code"),
                    "timed_out": item.get("timed_out"),
                    "elapsed_seconds": item.get("elapsed_seconds"),
                    "output_tail": str(item.get("output") or "")[-6000:],
                }
            )
        return compact

    def _brief_text(self) -> str:
        stored = self.store.root / "brief.md"
        if stored.is_file():
            return stored.read_text(encoding="utf-8")
        return self.brief_path.read_text(encoding="utf-8")

    def _planner_context(self) -> tuple[str, bool]:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=self.repo,
            text=True,
            check=True,
            stdout=subprocess.PIPE,
        )
        files = [line for line in result.stdout.splitlines() if line]
        if not files:
            return ("The selected branch has no tracked product files.", True)
        preview = "\n".join(f"- {name}" for name in files[:200])
        suffix = "" if len(files) <= 200 else f"\n- ... and {len(files) - 200} more"
        return (f"Tracked files ({len(files)} total):\n{preview}{suffix}", False)

    @staticmethod
    def _environment_context() -> str:
        commands = (
            "git",
            "python3",
            "node",
            "npm",
            "pnpm",
            "bun",
            "go",
            "cargo",
            "java",
            "docker",
            "Xvfb",
            "xvfb-run",
            "Xephyr",
        )
        available = [command for command in commands if shutil.which(command)]
        unavailable = [command for command in commands if command not in available]
        return (
            f"Available commands: {', '.join(available) or 'none'}. "
            f"Not detected: {', '.join(unavailable) or 'none'}."
        )

    def _brief_local_excludes(self) -> tuple[str, ...]:
        try:
            relative = self.brief_path.relative_to(self.repo)
        except ValueError:
            return ()
        if not relative.parts or relative.parts[0] == ".git":
            return ()
        value = relative.as_posix()
        for character in "\\*?[]":
            value = value.replace(character, "\\" + character)
        return ("/" + value,)

    # Models and provider calls -------------------------------------

    def _probe_models(self) -> None:
        unique: list[tuple[str, ModelSpec]] = []
        seen: set[str] = set()
        for role in ROLE_NAMES:
            spec = self.config.models[role]
            if model_identity(spec) not in seen:
                unique.append((role, spec))
                seen.add(model_identity(spec))
        if self.config.backup is not None and model_identity(self.config.backup) not in seen:
            unique.append(("backup", self.config.backup))
        self._phase("preflight", f"Probing {len(unique)} unique model(s).")
        failures: dict[str, Exception] = {}

        def probe(item: tuple[str, ModelSpec]) -> tuple[str, Exception | None]:
            role, spec = item
            slug = spec.display().replace("/", "_").replace(":", "_")
            try:
                self._invoke(
                    role="probe",
                    model=spec,
                    prompt=PROBE_PROMPT,
                    cwd=self.state_home / "probes" / self.run_id / slug,
                    access="none",
                    relative=f"preflight/probe-{slug}",
                    candidate=role,
                    allow_failover=False,
                )
            except (RunCancelled, RunInterrupted):
                raise
            except Exception as exc:
                return role, exc
            return role, None

        with ThreadPoolExecutor(max_workers=min(len(unique), 6) or 1) as pool:
            futures = [pool.submit(probe, item) for item in unique]
            for future in as_completed(futures):
                role, error = future.result()
                if error is not None:
                    failures[role] = error
        for role, error in failures.items():
            if role == "backup":
                self._warning(f"backup model probe failed: {error}")
                continue
            current = self.config.models[role]
            replacement = self._replacement_for(role, current)
            if replacement is None:
                raise ValueError(f"model preflight failed for {role}: {error}")
            self.config.models[role] = replacement
            self._warning(f"{role} switched to backup {replacement.display()} after probe failure.")
        self.state.preflight_probed = True
        self._persist_models()
        self._save(f"Model preflight passed with {len(failures)} replacement(s).")

    def _replacement_for(self, role: str, current: ModelSpec) -> ModelSpec | None:
        disabled = set(self.state.disabled_models)
        candidates: list[ModelSpec] = []
        if self.config.backup is not None:
            candidates.append(self.config.backup)
        for other in ROLE_NAMES:
            spec = self.config.models[other]
            if model_identity(spec) != model_identity(current):
                candidates.append(spec)
        healthy = [
            item
            for item in candidates
            if model_identity(item) not in disabled
            and model_identity(item) != model_identity(current)
        ]
        if not healthy:
            return None
        different = [item for item in healthy if model_family(item) != model_family(current)]
        return spec_with_effort((different or healthy)[0], current.effort)

    def _apply_replacement(self, exhausted: ModelSpec, replacement: ModelSpec) -> None:
        with self._roster_lock:
            identity = model_identity(exhausted)
            if identity not in self.state.disabled_models:
                self.state.disabled_models.append(identity)
            for role in ROLE_NAMES:
                spec = self.config.models[role]
                if model_identity(spec) == identity:
                    self.config.models[role] = spec_with_effort(replacement, spec.effort)
            self._persist_models()

    def _invoke(
        self,
        *,
        role: str,
        model: ModelSpec,
        prompt: str,
        cwd: Path,
        session_id: str | None = None,
        access: str = "write",
        schema: dict[str, Any] | None = None,
        extra_writable_dirs: tuple[Path, ...] = (),
        environment: dict[str, str] | None = None,
        relative: str,
        candidate: str = "",
        invocation: int = 1,
        allow_failover: bool = True,
        failover_on_timeout: bool = False,
    ) -> AgentResult:
        current_prompt = prompt
        current_model = model
        current_session = session_id
        failover_used = False
        session_dropped = False
        role_timeout = min(
            ROLE_TIMEOUTS.get(role, self.config.agent_timeout_seconds),
            self.config.agent_timeout_seconds,
        )
        attempt = 0
        while attempt < self.config.retry_count + 1:
            attempt += 1
            self._checkpoint()
            self.store.write_text(f"{relative}.prompt.md", current_prompt)
            activity_key = candidate or role
            self._activity_started(
                activity_key,
                role=role,
                candidate=candidate,
                model=current_model,
                attempt=attempt,
            )
            try:
                result = self.runner.run(
                    AgentRequest(
                        role=role,
                        model=current_model,
                        prompt=current_prompt,
                        cwd=cwd,
                        session_id=current_session,
                        access=access,
                        schema=schema,
                        schema_dir=self.brain_dir / "schemas",
                        extra_writable_dirs=extra_writable_dirs,
                        environment=dict(environment or {}),
                        timeout_seconds=role_timeout,
                    )
                )
            except AgentCancelled:
                raise RunCancelled()
            except AgentUsageLimit as exc:
                self.store.write_text(f"{relative}.failure-{attempt}.log", exc.raw_output)
                self._record_disabled(current_model)
                if allow_failover and not failover_used:
                    replacement = self._replacement_for(role, current_model)
                    if replacement is not None:
                        self._apply_replacement(current_model, replacement)
                        current_model = self.config.models.get(role, replacement)
                        current_session = None
                        failover_used = True
                        attempt = 0
                        self._warning(
                            f"{role} switched to {current_model.display()} after a usage limit."
                        )
                        continue
                raise
            except AgentConfigurationFailure as exc:
                self.store.write_text(f"{relative}.failure-{attempt}.log", exc.raw_output)
                if current_session and not session_dropped:
                    current_session = None
                    session_dropped = True
                    attempt = 0
                    self._warning(f"{role} dropped a stale provider session and will retry.")
                    continue
                raise
            except AgentTimeout as exc:
                self.store.write_text(f"{relative}.failure-{attempt}.log", exc.raw_output)
                if allow_failover and failover_on_timeout and not failover_used:
                    replacement = self._replacement_for(role, current_model)
                    if replacement is not None:
                        self._apply_replacement(current_model, replacement)
                        current_model = self.config.models.get(role, replacement)
                        current_session = None
                        failover_used = True
                        attempt = 0
                        self._warning(f"{role} switched model after timeout.")
                        continue
                raise
            except AgentFailure as exc:
                self.store.write_text(f"{relative}.failure-{attempt}.log", exc.raw_output)
                if attempt > self.config.retry_count:
                    raise
                current_prompt = (
                    f"Forge retried this role because the provider process failed: {exc}. "
                    "Continue from the durable workspace and return the same requested contract.\n\n"
                    + prompt
                )
                continue
            finally:
                self._activity_finished(activity_key)
            self.store.write_text(f"{relative}.raw.jsonl", result.raw_output)
            self.store.write_text(f"{relative}.response.md", result.text.rstrip() + "\n")
            self.store.record_agent_call(
                role=role,
                model=current_model,
                result=result,
                cycle=self.state.cycle,
                candidate=candidate,
                invocation=invocation,
            )
            self.store.event(
                "agent.completed",
                f"{role} completed",
                role=role,
                candidate=candidate,
                elapsed_seconds=result.elapsed_seconds,
                tokens=result.usage.total_tokens,
            )
            return result
        raise AssertionError("unreachable")

    def _record_disabled(self, spec: ModelSpec) -> None:
        identity = model_identity(spec)
        with self._roster_lock:
            with self._state_lock:
                if identity not in self.state.disabled_models:
                    self.state.disabled_models.append(identity)
                    self._persist_models()

    def _persist_models(self) -> None:
        with self._state_lock:
            self.state.config = self.config.to_dict()
            self.store.write_data("config.json", self.config.to_dict())
            self.store.save_state(self.state)

    # State/event plumbing ------------------------------------------

    def _ensure_event_dispatcher(self, execution_generation: int) -> None:
        if self.on_event is None:
            return
        with self._event_lock:
            if self._event_thread is not None:
                if self._event_thread.is_alive():
                    if (
                        self._event_accepting
                        and self._event_generation == execution_generation
                    ):
                        return
                    raise RuntimeError(
                        "the previous event callback is still running; "
                        "use a fresh controller instance or wait before recovery"
                    )
                self._event_thread = None
                self._event_queue = None
                self._event_generation = None
            event_queue: queue.Queue[dict[str, Any] | object] = queue.Queue(
                maxsize=EVENT_QUEUE_LIMIT
            )
            self._event_queue = event_queue
            self._event_accepting = True
            self._event_generation = execution_generation
            self._event_thread = threading.Thread(
                target=self._dispatch_events,
                args=(event_queue, execution_generation),
                name=f"forge-events-{self.run_id}",
                daemon=True,
            )
            self._event_thread.start()

    def _dispatch_events(
        self,
        event_queue: queue.Queue[dict[str, Any] | object],
        execution_generation: int,
    ) -> None:
        while True:
            event = event_queue.get()
            if event is _EVENT_STOP:
                return
            callback = self.on_event
            if callback is None:
                continue
            try:
                assert isinstance(event, dict)
                self._callback_context.execution_generation = execution_generation
                callback(event)
            except Exception:
                # Monitoring callbacks must never break or deadlock delivery.
                continue
            finally:
                try:
                    del self._callback_context.execution_generation
                except AttributeError:
                    pass

    def _queue_event(self, event: dict[str, Any]) -> None:
        if self.on_event is None:
            return
        execution_generation = self._active_execution_generation
        if execution_generation is None:
            return
        with self._event_lock:
            if (
                not self._event_accepting
                and self._event_thread is not None
                and self._event_thread.is_alive()
            ):
                return
        self._ensure_event_dispatcher(execution_generation)
        with self._event_lock:
            event_queue = self._event_queue
            if not self._event_accepting or event_queue is None:
                return
            try:
                event_queue.put_nowait(event)
            except queue.Full:
                try:
                    event_queue.get_nowait()
                except queue.Empty:
                    pass
                event_queue.put_nowait(event)

    def _shutdown_event_dispatcher(self) -> None:
        with self._event_lock:
            event_queue = self._event_queue
            thread = self._event_thread
            self._event_accepting = False
            if event_queue is not None:
                latest: dict[str, Any] | None = None
                while True:
                    try:
                        pending = event_queue.get_nowait()
                    except queue.Empty:
                        break
                    if isinstance(pending, dict):
                        latest = pending
                if latest is not None:
                    event_queue.put_nowait(latest)
                event_queue.put_nowait(_EVENT_STOP)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=EVENT_SHUTDOWN_TIMEOUT_SECONDS)
        with self._event_lock:
            if (
                self._event_thread is thread
                and (thread is None or not thread.is_alive())
            ):
                self._event_thread = None
                self._event_queue = None
                self._event_generation = None

    def _runner_cancel(self) -> None:
        cancel = getattr(self.runner, "cancel", None)
        if callable(cancel):
            cancel()

    def _runner_allow(self) -> None:
        allow = getattr(self.runner, "allow", None)
        if callable(allow):
            allow()

    def _activity_started(
        self,
        key: str,
        *,
        role: str,
        candidate: str,
        model: ModelSpec,
        attempt: int,
    ) -> None:
        with self._state_lock:
            with self._activity_lock:
                self.state.active_agents[key] = {
                    "role": role,
                    "candidate": candidate,
                    "model": model.display(),
                    "attempt": attempt,
                    "started_at": utc_now(),
                }
                self.store.save_state(self.state)

    def _activity_finished(self, key: str) -> None:
        with self._state_lock:
            with self._activity_lock:
                self.state.active_agents.pop(key, None)
                self.store.save_state(self.state)

    def _phase(self, phase: str, message: str) -> None:
        self._checkpoint()
        with self._state_lock:
            self.state.phase = phase
            self._save(message)

    def _warning(self, message: str) -> None:
        with self._state_lock:
            self.state.warnings.append(message)
            self.store.event(
                "warning", message, phase=self.state.phase, status=self.state.status
            )
            if self.on_event is not None:
                self._queue_event(
                    {
                        "kind": "warning",
                        "message": message,
                        "phase": self.state.phase,
                        "status": self.state.status,
                    }
                )

    def _save(self, message: str) -> None:
        with self._state_lock:
            self.state.message = message
            self.state.config = self.config.to_dict()
            self.store.save_state(self.state)
            event = {
                "kind": "state",
                "message": message,
                "phase": self.state.phase,
                "status": self.state.status,
                "sprint": self.state.sprint_number,
                "slot": self.state.sprint_iteration,
            }
            self.store.event(**event)
            if self.on_event is not None:
                self._queue_event(event)

    def _checkpoint(self) -> None:
        with self._control:
            if self._interrupt_requested:
                raise RunInterrupted()
            if self.state.cancel_requested:
                raise RunCancelled()
            while self.state.paused:
                with self._state_lock:
                    self.state.status = "paused"
                    self.store.save_state(self.state)
                self._control.wait(timeout=0.5)
                if self._interrupt_requested:
                    raise RunInterrupted()
                if self.state.cancel_requested:
                    raise RunCancelled()
            if self.state.status == "paused":
                with self._state_lock:
                    self.state.status = "running"
                    self.store.save_state(self.state)
