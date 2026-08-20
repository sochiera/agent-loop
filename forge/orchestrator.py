"""The deliberately small Forge state machine."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .agents import (
    AgentConfigurationFailure,
    AgentFailure,
    AgentRequest,
    AgentResult,
    AgentRunner,
    AgentTimeout,
)
from .artifacts import ArtifactStore, atomic_write, utc_now
from .contracts import (
    BRAIN_SCHEMA,
    REVIEW_SCHEMA,
    TEST_SCHEMA,
    BrainDecision,
    ContractError,
    parse_brain,
    parse_review,
    parse_test,
)
from .gitops import CandidateWorktree, GitCompetition, GitError
from .models import ModelSpec, RunConfig, RunState
from .plans import (
    PlanProgress,
    candidate_validation_commands,
    progress,
    validate_plan,
    validation_commands,
)
from .prompts import (
    brain_feedback,
    brain_initial,
    coder_continuation,
    coder_initial,
    contract_feedback,
    planner_prompt,
    reviewer_prompt,
    tester_prompt,
    winner_fix_prompt,
)
from .validation import run_commands


class RunCancelled(RuntimeError):
    pass


@dataclass
class CandidateOutcome:
    name: str
    worktree: CandidateWorktree
    session_id: str | None
    status: str
    plan: PlanProgress
    turns: int
    validation: list[dict[str, Any]]
    summary: str
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "worktree": str(self.worktree.path),
            "branch": self.worktree.branch,
            "session_id": self.session_id,
            "status": self.status,
            "tasks": {"completed": self.plan.completed, "total": self.plan.total},
            "turns": self.turns,
            "validation": self.validation,
            "summary": self.summary,
            "warnings": self.warnings,
        }


class ForgeOrchestrator:
    def __init__(
        self,
        config: RunConfig,
        *,
        run_id: str | None = None,
        runner: AgentRunner | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        state_home: Path | None = None,
        check_binaries: bool = True,
    ):
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
        self.check_binaries = check_binaries
        now = utc_now()
        self.state = RunState(
            run_id=self.run_id,
            status="created",
            phase="preflight",
            created_at=now,
            updated_at=now,
            config=config.to_dict(),
        )
        self._control = threading.Condition()
        self._activity_lock = threading.Lock()
        self._competition: GitCompetition | None = None

    def pause(self) -> None:
        with self._control:
            self.state.paused = True
            self._save("Pause requested; Forge will pause at the next phase boundary.")

    def resume(self) -> None:
        with self._control:
            self.state.paused = False
            self._control.notify_all()
            self._save("Run resumed.")

    def cancel(self) -> None:
        with self._control:
            self.state.cancel_requested = True
            self.state.paused = False
            self._control.notify_all()
            self._save("Cancellation requested.")

    def run(self) -> RunState:
        self.store.write_data("config.json", self.config.to_dict())
        self.store.write_text("brief.md", self.brief_path.read_text(encoding="utf-8"))
        self.state.status = "running"
        self._save("Starting Forge run.")
        try:
            self._preflight()
            decision = self._brain_decision(
                brain_initial(self.brief_path.read_text(encoding="utf-8"))
            )
            return self._run_from_decision(decision)
        except RunCancelled:
            self.state.status = "cancelled"
            self.state.phase = "cancelled"
            self._save("Run cancelled by the operator.")
            return self.state
        except Exception as exc:
            self.state.status = "failed"
            self.state.phase = "failed"
            self._warning(f"Run failed: {exc}")
            self._save(str(exc))
            raise
        finally:
            if self._competition is not None:
                self._competition.cleanup()

    def recover_failed(self) -> RunState:
        """Continue a failed or interrupted run after its latest delivered batch."""
        self.state = self.store.load_state()
        recoverable = {"failed", "cancelled", "running"}
        if self.state.status not in recoverable:
            raise RuntimeError(
                f"only failed or interrupted runs can be recovered (status: {self.state.status})"
            )
        if self.state.status == "running" and self.state.active_agents:
            raise RuntimeError("interrupted run still records active agents; stop them before recovery")
        if not self.state.brain_session_id:
            raise RuntimeError("failed run has no persistent brain session to resume")
        if not self.state.batches:
            raise RuntimeError("failed run has no delivered batch to resume from")
        latest = self.state.batches[-1]
        latest_cycle = int(latest["cycle"])
        pending_cycle = int(self.state.cycle)
        resume_captured = (
            pending_cycle > latest_cycle and self._has_captured_batch(pending_cycle)
        )
        # Planning increments the cycle before delivery. Preserve it only when
        # all candidate patches/outcomes were captured; otherwise retry from
        # the last batch that actually reached main.
        self.state.cycle = pending_cycle if resume_captured else latest_cycle
        self.state.status = "running"
        self.state.cancel_requested = False
        self.state.paused = False
        self.state.active_agents.clear()
        recovery_point = (
            f"captured batch {self.state.cycle} review"
            if resume_captured
            else f"delivered batch {self.state.cycle}"
        )
        self._save(f"Recovering run from {recovery_point}.")
        try:
            self._preflight()
            if resume_captured:
                batch = self._resume_captured_batch(self.state.cycle)
                self.state.batches.append(batch)
                self._save(f"Batch {self.state.cycle} delivered and black-box tested.")
                decision = self._brain_decision(
                    brain_feedback(
                        cycle=self.state.cycle,
                        objective=str(batch["objective"]),
                        review=batch["review"],
                        test=batch["black_box"],
                        metrics=batch["candidate_metrics"],
                    )
                )
                return self._run_from_decision(decision)
            decision = self._brain_decision(
                brain_feedback(
                    cycle=self.state.cycle,
                    objective=str(latest["objective"]),
                    review=latest["review"],
                    test=latest["black_box"],
                    metrics=latest["candidate_metrics"],
                )
            )
            return self._run_from_decision(decision)
        except RunCancelled:
            self.state.status = "cancelled"
            self.state.phase = "cancelled"
            self._save("Run cancelled by the operator.")
            return self.state
        except Exception as exc:
            self.state.status = "failed"
            self.state.phase = "failed"
            self._warning(f"Run recovery failed: {exc}")
            self._save(str(exc))
            raise
        finally:
            if self._competition is not None:
                self._competition.cleanup()

    def _has_captured_batch(self, cycle: int) -> bool:
        root = self.store.root / "batches" / f"{cycle:03d}"
        required = [root / "objective.json", root / "plan.md", root / "review-bundle.json"]
        for name in ("tdd", "explore", "classic"):
            required.extend(
                [
                    root / "candidates" / name / "candidate.patch",
                    root / "candidates" / name / "outcome.json",
                ]
            )
        return all(path.is_file() for path in required) and not (root / "delivery.json").exists()

    def _resume_captured_batch(self, cycle: int) -> dict[str, Any]:
        assert self._competition is not None
        batch_rel = f"batches/{cycle:03d}"
        root = self.store.root / batch_rel
        objective = json.loads((root / "objective.json").read_text(encoding="utf-8"))
        decision = BrainDecision(
            "forge.run_batch",
            str(objective.get("brain_reason", "Resume captured batch review.")),
            str(objective["objective"]),
            tuple(str(item) for item in objective["success_criteria"]),
        )
        plan = (root / "plan.md").read_text(encoding="utf-8")
        patches = {
            name: (root / "candidates" / name / "candidate.patch").read_text(
                encoding="utf-8"
            )
            for name in ("tdd", "explore", "classic")
        }
        worktrees = self._competition.restore_candidates(patches)
        outcomes: dict[str, CandidateOutcome] = {}
        for name, worktree in worktrees.items():
            plan_path = worktree.path / ".forge" / "plan.md"
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(plan_path, plan)
            value = json.loads(
                (root / "candidates" / name / "outcome.json").read_text(encoding="utf-8")
            )
            tasks = value.get("tasks", {})
            completed = int(tasks.get("completed", 0))
            total = int(tasks.get("total", 0))
            outcomes[name] = CandidateOutcome(
                name=name,
                worktree=worktree,
                session_id=value.get("session_id"),
                status=str(value.get("status", "failed")),
                plan=PlanProgress(completed, total, ()),
                turns=int(value.get("turns", 0)),
                validation=list(value.get("validation", [])),
                summary=str(value.get("summary", "")),
                warnings=[str(item) for item in value.get("warnings", [])],
            )
        bundle = self._capture_candidates(outcomes, batch_rel)
        self._save(f"Restored captured candidates for batch {cycle}; resuming review.")
        return self._review_deliver_and_test(
            decision=decision,
            commands=validation_commands(plan),
            outcomes=outcomes,
            bundle=bundle,
            batch_rel=batch_rel,
            cycle=cycle,
        )

    def _run_from_decision(self, decision: BrainDecision) -> RunState:
        while decision.tool != "forge.finish":
            self._checkpoint()
            batch = self._run_batch(decision)
            self.state.batches.append(batch)
            self._save(f"Batch {self.state.cycle} delivered and black-box tested.")
            decision = self._brain_decision(
                brain_feedback(
                    cycle=self.state.cycle,
                    objective=decision.objective,
                    review=batch["review"],
                    test=batch["black_box"],
                    metrics=batch["candidate_metrics"],
                )
            )
        self.state.status = "complete"
        self.state.phase = "complete"
        self.state.final_summary = decision.summary
        self._save(decision.reason)
        self.store.write_text("final-summary.md", decision.summary + "\n")
        return self.state

    def _preflight(self) -> None:
        self._phase("preflight", "Checking providers and target repository.")
        if self.check_binaries:
            for role, model in self.config.models.items():
                if shutil.which(model.provider) is None:
                    raise ValueError(f"{role} provider executable is unavailable: {model.provider}")
        self.brain_dir.mkdir(parents=True, exist_ok=True)
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        self._competition = GitCompetition(
            self.repo,
            self.config.branch,
            self.run_id,
            self.worktree_root,
            local_excludes=self._brief_local_excludes(),
        )
        base = self._competition.prepare(require_remote=self.config.push)
        self.store.write_data("git.json", {"branch": self.config.branch, "base_sha": base})
        self._save(f"Preflight passed at {base[:12]} on {self.config.branch}.")

    def _brain_decision(self, prompt: str) -> BrainDecision:
        self._phase("brain", "Persistent brain is choosing the next Forge action.")
        current = prompt
        for contract_attempt in range(1, 4):
            result = self._invoke(
                role="brain",
                model=self.config.models["brain"],
                prompt=current,
                cwd=self.brain_dir,
                session_id=self.state.brain_session_id,
                access="none",
                schema=BRAIN_SCHEMA,
                relative=f"brain/decision-{self.state.cycle:03d}-{contract_attempt}",
            )
            if self.state.brain_session_id and result.session_id != self.state.brain_session_id:
                raise RuntimeError("brain provider did not preserve the original session")
            if not result.session_id:
                raise RuntimeError("brain provider did not return a resumable session id")
            self.state.brain_session_id = result.session_id
            try:
                if result.tool_calls:
                    raise ContractError(
                        f"brain attempted {result.tool_calls} provider tool call(s); only Forge JSON is allowed"
                    )
                decision = parse_brain(result.text)
            except ContractError as exc:
                self._warning(f"Brain contract retry {contract_attempt}: {exc}")
                current = contract_feedback(str(exc))
                continue
            self.store.write_data(
                f"brain/decision-{self.state.cycle:03d}.json",
                {
                    "tool": decision.tool,
                    "reason": decision.reason,
                    "objective": decision.objective,
                    "success_criteria": list(decision.success_criteria),
                    "summary": decision.summary,
                },
            )
            self._save(f"Brain selected {decision.tool}: {decision.reason}")
            return decision
        raise RuntimeError("brain failed the Forge tool-call contract three times")

    def _run_batch(self, decision: BrainDecision) -> dict[str, Any]:
        assert self._competition is not None
        self.state.cycle += 1
        cycle = self.state.cycle
        batch_rel = f"batches/{cycle:03d}"
        self.store.write_data(
            f"{batch_rel}/objective.json",
            {
                "objective": decision.objective,
                "success_criteria": list(decision.success_criteria),
                "brain_reason": decision.reason,
            },
        )

        self._phase("planning", f"Planning batch {cycle}.")
        plan = self._make_plan(decision, batch_rel)
        commands = validation_commands(plan)
        candidate_commands = candidate_validation_commands(plan)

        self._phase("coding", f"Three coders are competing on batch {cycle}.")
        worktrees = self._competition.create_candidates()
        for candidate in worktrees.values():
            plan_path = candidate.path / ".forge" / "plan.md"
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(plan_path, plan)

        outcomes: dict[str, CandidateOutcome] = {}
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="forge-coder") as pool:
            futures = {
                pool.submit(
                    self._run_coder,
                    candidate,
                    decision,
                    candidate_commands,
                    batch_rel,
                ): name
                for name, candidate in worktrees.items()
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    outcomes[name] = future.result()
                except RunCancelled:
                    raise
                except Exception as exc:
                    self._warning(f"Candidate {name} failed: {exc}")
                    plan_state = progress(
                        (worktrees[name].path / ".forge" / "plan.md").read_text(
                            encoding="utf-8", errors="replace"
                        )
                    )
                    outcomes[name] = CandidateOutcome(
                        name=name,
                        worktree=worktrees[name],
                        session_id=None,
                        status="failed",
                        plan=plan_state,
                        turns=0,
                        validation=[],
                        summary=str(exc),
                        warnings=[str(exc)],
                    )

        bundle = self._capture_candidates(outcomes, batch_rel)
        if not any(item["git"]["patch"].strip() for item in bundle.values()):
            raise RuntimeError("all three candidates produced no code changes")

        return self._review_deliver_and_test(
            decision=decision,
            commands=commands,
            outcomes=outcomes,
            bundle=bundle,
            batch_rel=batch_rel,
            cycle=cycle,
        )

    def _review_deliver_and_test(
        self,
        *,
        decision: BrainDecision,
        commands: tuple[str, ...],
        outcomes: dict[str, CandidateOutcome],
        bundle: dict[str, dict[str, Any]],
        batch_rel: str,
        cycle: int,
    ) -> dict[str, Any]:
        assert self._competition is not None

        self._phase("review", f"Reviewing all candidates for batch {cycle}.")
        review = self._review(decision, outcomes, bundle, batch_rel)
        winner_name = review["winner"]
        if outcomes[winner_name].status == "failed" or not bundle[winner_name]["git"]["patch"].strip():
            raise RuntimeError(f"reviewer selected unusable candidate: {winner_name}")

        self._phase("winner-fix", f"The winning coder is applying review feedback.")
        winner = outcomes[winner_name]
        fix_result = self._invoke(
            role=f"coder_{winner_name}",
            model=self.config.models[f"coder_{winner_name}"],
            prompt=winner_fix_prompt(review["feedback"], winner.validation),
            cwd=winner.worktree.path,
            session_id=winner.session_id,
            access="write",
            relative=f"{batch_rel}/candidates/{winner_name}/winner-fix",
            candidate=winner_name,
        )
        if not winner.session_id or fix_result.session_id != winner.session_id:
            raise RuntimeError("winning coder provider did not preserve its original session")
        final_validation = run_commands(commands, winner.worktree.path)
        failed_final_checks = [
            item
            for item in final_validation
            if item["return_code"] != 0 or item["timed_out"]
        ]
        if failed_final_checks:
            warning = (
                f"Winner {winner_name} has {len(failed_final_checks)} failing or timed-out "
                "post-review validation check(s); recorded for the brain without blocking delivery."
            )
            winner.warnings.append(warning)
            self._warning(warning)
        winner.validation = final_validation
        winner.summary = fix_result.text
        self.store.write_data(
            f"{batch_rel}/candidates/{winner_name}/final-validation.json", final_validation
        )
        final_capture = self._competition.capture(winner.worktree)
        self.store.write_text(
            f"{batch_rel}/candidates/{winner_name}/final.patch", final_capture["patch"]
        )

        self._phase("delivery", f"Committing and delivering the batch {cycle} winner.")
        commit = self._competition.commit_and_deliver(
            winner.worktree,
            f"Forge batch {cycle}: {decision.objective[:60]}",
            push=self.config.push,
        )
        self.store.write_data(
            f"{batch_rel}/delivery.json",
            {"winner": winner_name, "commit": commit, "branch": self.config.branch, "pushed": self.config.push},
        )

        self._competition.cleanup()
        self._competition = GitCompetition(
            self.repo,
            self.config.branch,
            self.run_id,
            self.worktree_root,
            local_excludes=self._brief_local_excludes(),
        )
        self._competition.prepare(require_remote=self.config.push)

        self._phase("black-box", f"Testing delivered batch {cycle} through public interfaces.")
        black_box_worktree = self._competition.create_detached("black-box")
        try:
            try:
                black_box = self._black_box(
                    decision, commands, final_validation, batch_rel, black_box_worktree
                )
            except RunCancelled:
                raise
            except Exception as exc:
                warning = f"Black-box tester did not complete after retries: {exc}"
                self._warning(warning)
                black_box = {
                    "summary": "Black-box testing did not complete.",
                    "working": [],
                    "missing": ["No black-box verdict is available for this delivered batch."],
                    "observations": [warning],
                    "evidence": [],
                }
                self.store.write_data(f"{batch_rel}/black-box.json", black_box)
        finally:
            self._competition.remove_detached(black_box_worktree)
        metrics = self._candidate_metrics(outcomes, review, cycle)
        self.store.write_data(f"{batch_rel}/candidate-metrics.json", metrics)
        for name, candidate_metrics in metrics.items():
            self.store.write_data(
                f"{batch_rel}/candidates/{name}/metrics.json", candidate_metrics
            )
        return {
            "cycle": cycle,
            "objective": decision.objective,
            "commit": commit,
            "winner": winner_name,
            "review": review,
            "black_box": black_box,
            "candidate_metrics": metrics,
        }

    def _make_plan(self, decision: BrainDecision, batch_rel: str) -> str:
        repository_context, repository_is_empty = self._planner_context()
        prompt = planner_prompt(
            decision.objective,
            decision.success_criteria,
            Path("plan.md"),
            repository_context=repository_context,
            environment_context=self._environment_context(),
        )
        session: str | None = None
        for attempt in range(1, 4):
            result = self._invoke(
                role="planner",
                model=self.config.models["planner"],
                prompt=prompt,
                cwd=self.repo,
                session_id=session,
                access="none" if repository_is_empty else "read",
                relative=f"{batch_rel}/planner/attempt-{attempt}",
            )
            session = result.session_id
            try:
                validate_plan(result.text)
            except ValueError as exc:
                prompt = (
                    "Forge rejected the previous Markdown plan: "
                    f"{exc}. Return a corrected complete plan using the required headings, "
                    "checkbox tasks, and backtick validation-command bullets."
                )
                continue
            self.store.write_text(f"{batch_rel}/plan.md", result.text.rstrip() + "\n")
            return result.text.rstrip() + "\n"
        raise RuntimeError("planner failed to produce a valid Markdown plan")

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
            return (
                "The selected branch has no tracked product files. It contains only Forge-local "
                "inputs/artifacts, so repository inspection would return no useful information.",
                True,
            )
        preview = "\n".join(f"- {name}" for name in files[:200])
        suffix = "" if len(files) <= 200 else f"\n- ... and {len(files) - 200} more tracked files"
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

    def _run_coder(
        self,
        candidate: CandidateWorktree,
        decision: BrainDecision,
        commands: tuple[str, ...],
        batch_rel: str,
    ) -> CandidateOutcome:
        model = self.config.models[f"coder_{candidate.name}"]
        plan_path = candidate.path / ".forge" / "plan.md"
        prompt = coder_initial(
            candidate=candidate.name,
            objective=decision.objective,
            criteria=decision.success_criteria,
            plan_path=Path(".forge/plan.md"),
        )
        session: str | None = None
        previous_completed = -1
        stalled = 0
        warnings: list[str] = []
        turns = 0
        final_text = ""
        while True:
            self._checkpoint()
            turns += 1
            result = self._invoke(
                role=f"coder_{candidate.name}",
                model=model,
                prompt=prompt,
                cwd=candidate.path,
                session_id=session,
                access="write",
                relative=f"{batch_rel}/candidates/{candidate.name}/turn-{turns}",
                candidate=candidate.name,
                invocation=turns,
            )
            if not result.session_id:
                raise RuntimeError(
                    f"coder {candidate.name} provider did not return a resumable session id"
                )
            if session and result.session_id != session:
                raise RuntimeError(f"coder {candidate.name} provider changed its session id")
            session = result.session_id
            final_text = result.text
            if not plan_path.exists():
                raise RuntimeError("coder removed the Markdown goal plan")
            state = progress(plan_path.read_text(encoding="utf-8"))
            self.store.write_data(
                f"{batch_rel}/candidates/{candidate.name}/progress-{turns}.json",
                {"completed": state.completed, "total": state.total, "remaining": list(state.remaining)},
            )
            if state.done:
                validation = run_commands(commands, candidate.path)
                self.store.write_data(
                    f"{batch_rel}/candidates/{candidate.name}/validation.json", validation
                )
                return CandidateOutcome(
                    candidate.name,
                    candidate,
                    session,
                    "complete",
                    state,
                    turns,
                    validation,
                    final_text,
                    warnings,
                )
            if state.completed <= previous_completed:
                stalled += 1
                warning = (
                    f"Coder {candidate.name} made no checkbox progress on turn {turns} "
                    f"({state.completed}/{state.total})."
                )
                warnings.append(warning)
                self._warning(warning)
            else:
                stalled = 0
            previous_completed = state.completed
            if stalled >= self.config.stalled_turns:
                warning = f"Coder {candidate.name} stalled repeatedly; moving on with its current artifact."
                warnings.append(warning)
                self._warning(warning)
                validation = run_commands(commands, candidate.path)
                self.store.write_data(
                    f"{batch_rel}/candidates/{candidate.name}/validation.json", validation
                )
                return CandidateOutcome(
                    candidate.name,
                    candidate,
                    session,
                    "stalled",
                    state,
                    turns,
                    validation,
                    final_text,
                    warnings,
                )
            prompt = coder_continuation(
                completed=state.completed,
                total=state.total,
                remaining=state.remaining,
                reason="unchecked tasks remain in .forge/plan.md",
            )

    def _capture_candidates(
        self, outcomes: dict[str, CandidateOutcome], batch_rel: str
    ) -> dict[str, dict[str, Any]]:
        assert self._competition is not None
        bundle: dict[str, dict[str, Any]] = {}
        for name, outcome in outcomes.items():
            git = self._competition.capture(outcome.worktree)
            self.store.write_text(f"{batch_rel}/candidates/{name}/candidate.patch", git["patch"])
            self.store.write_text(
                f"{batch_rel}/candidates/{name}/review.patch", git["review_patch"]
            )
            self.store.write_text(f"{batch_rel}/candidates/{name}/diffstat.txt", git["diffstat"])
            self.store.write_text(f"{batch_rel}/candidates/{name}/status.txt", git["status"])
            self.store.write_data(f"{batch_rel}/candidates/{name}/outcome.json", outcome.to_dict())
            bundle[name] = {"outcome": outcome.to_dict(), "git": git}
        review_bundle = {
            name: {
                "outcome": self._review_outcome(value["outcome"], batch_rel, name),
                "git": {
                    "status": value["git"]["status"],
                    "diffstat": value["git"]["diffstat"],
                    "patch_path": str(
                        self.store.root / batch_rel / "candidates" / name / "review.patch"
                    ),
                    "review_patch_truncated": value["git"]["review_patch_truncated"],
                },
            }
            for name, value in bundle.items()
        }
        self.store.write_data(f"{batch_rel}/review-bundle.json", review_bundle)
        return bundle

    def _review_outcome(
        self, outcome: dict[str, Any], batch_rel: str, candidate: str
    ) -> dict[str, Any]:
        compact = dict(outcome)
        compact_validation: list[dict[str, Any]] = []
        for index, validation in enumerate(outcome.get("validation", []), start=1):
            item = dict(validation)
            output = str(item.get("output", ""))
            if len(output) > 12_000:
                item["output"] = output[-12_000:]
                item["output_truncated"] = True
                item["full_result_artifact"] = str(
                    self.store.root
                    / batch_rel
                    / "candidates"
                    / candidate
                    / "validation.json"
                )
                item["validation_index"] = index
            compact_validation.append(item)
        compact["validation"] = compact_validation
        return compact

    def _review(
        self,
        decision: BrainDecision,
        outcomes: dict[str, CandidateOutcome],
        bundle: dict[str, dict[str, Any]],
        batch_rel: str,
    ) -> dict[str, Any]:
        source_bundle = self.store.root / batch_rel / "review-bundle.json"
        review_dir = self.worktree_root / "review-evidence"
        review_dir.mkdir(parents=True, exist_ok=True)
        local_bundle = json.loads(source_bundle.read_text(encoding="utf-8"))
        for name in outcomes:
            source_patch = self.store.root / batch_rel / "candidates" / name / "review.patch"
            local_patch = review_dir / f"{name}.patch"
            atomic_write(local_patch, source_patch.read_text(encoding="utf-8"))
            local_bundle[name]["git"]["patch_path"] = str(local_patch)
        bundle_path = review_dir / "bundle.json"
        atomic_write(bundle_path, json.dumps(local_bundle, indent=2, sort_keys=True) + "\n")
        prompt = reviewer_prompt(
            objective=decision.objective,
            criteria=decision.success_criteria,
            candidates={name: outcome.worktree.path for name, outcome in outcomes.items()},
            bundle=bundle_path,
        )
        session: str | None = None
        try:
            for attempt in range(1, 4):
                result = self._invoke(
                    role="reviewer",
                    model=self.config.models["reviewer"],
                    prompt=prompt,
                    cwd=self.worktree_root,
                    session_id=session,
                    access="read",
                    schema=REVIEW_SCHEMA,
                    relative=f"{batch_rel}/review/attempt-{attempt}",
                )
                session = result.session_id
                try:
                    review = parse_review(result.text)
                    winner = review["winner"]
                    if (
                        outcomes[winner].status == "failed"
                        or not bundle[winner]["git"]["patch"].strip()
                    ):
                        raise ContractError(f"selected candidate {winner} is unusable")
                except ContractError as exc:
                    prompt = contract_feedback(str(exc)).replace(
                        "forge.run_batch or forge.finish", "the required review JSON"
                    )
                    continue
                self.store.write_data(f"{batch_rel}/review.json", review)
                return review
            raise RuntimeError("reviewer failed the competition contract")
        finally:
            shutil.rmtree(review_dir, ignore_errors=True)

    def _black_box(
        self,
        decision: BrainDecision,
        commands: tuple[str, ...],
        validation: list[dict[str, Any]],
        batch_rel: str,
        product_worktree: Path,
    ) -> dict[str, Any]:
        evidence = self.store.root / batch_rel / "black-box-evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        prompt = tester_prompt(
            objective=decision.objective,
            criteria=decision.success_criteria,
            commands=commands,
            validation=validation,
            evidence_dir=evidence,
        )
        session: str | None = None
        for attempt in range(1, 4):
            result = self._invoke(
                role="tester",
                model=self.config.models["tester"],
                prompt=prompt,
                cwd=product_worktree,
                session_id=session,
                access="write",
                schema=TEST_SCHEMA,
                extra_writable_dirs=(evidence,),
                relative=f"{batch_rel}/black-box/attempt-{attempt}",
            )
            session = result.session_id
            try:
                report = parse_test(result.text)
            except ContractError as exc:
                prompt = contract_feedback(str(exc)).replace(
                    "forge.run_batch or forge.finish", "the required black-box report JSON"
                )
                continue
            self.store.write_data(f"{batch_rel}/black-box.json", report)
            return report
        raise RuntimeError("black-box tester failed its report contract")

    def _candidate_metrics(
        self, outcomes: dict[str, CandidateOutcome], review: dict[str, Any], cycle: int
    ) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        usage_path = self.store.root / "usage.jsonl"
        if usage_path.exists():
            for line in usage_path.read_text(encoding="utf-8").splitlines():
                value = json.loads(line)
                if value.get("cycle") == cycle and value.get("candidate"):
                    rows.append(value)
        metrics: dict[str, Any] = {}
        for name, outcome in outcomes.items():
            candidate_rows = [row for row in rows if row.get("candidate") == name]
            metrics[name] = {
                "status": outcome.status,
                "turns": outcome.turns,
                "tasks_completed": outcome.plan.completed,
                "tasks_total": outcome.plan.total,
                "review_score": review["candidates"][name]["score"],
                "validation_passed": sum(
                    result["return_code"] == 0 and not result["timed_out"]
                    for result in outcome.validation
                ),
                "validation_total": len(outcome.validation),
                "input_tokens": sum(row["usage"]["input_tokens"] for row in candidate_rows),
                "cached_input_tokens": sum(
                    row["usage"]["cached_input_tokens"] for row in candidate_rows
                ),
                "output_tokens": sum(row["usage"]["output_tokens"] for row in candidate_rows),
                "total_tokens": sum(row["usage"]["total_tokens"] for row in candidate_rows),
                "elapsed_seconds": round(sum(row["elapsed_seconds"] for row in candidate_rows), 3),
                "warnings": outcome.warnings,
                "selected": review["winner"] == name,
            }
        return metrics

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
        relative: str,
        candidate: str = "",
        invocation: int = 1,
    ) -> AgentResult:
        current_prompt = prompt
        for attempt in range(1, self.config.retry_count + 2):
            self._checkpoint()
            self.store.write_text(f"{relative}.prompt.md", current_prompt)
            activity_key = candidate or role
            self._activity_started(
                activity_key,
                role=role,
                candidate=candidate,
                model=model,
                attempt=attempt,
            )
            try:
                result = self.runner.run(
                    AgentRequest(
                        role=role,
                        model=model,
                        prompt=current_prompt,
                        cwd=cwd,
                        session_id=session_id,
                        access=access,
                        schema=schema,
                        extra_writable_dirs=extra_writable_dirs,
                        timeout_seconds=self.config.agent_timeout_seconds,
                    )
                )
            except AgentConfigurationFailure as exc:
                self.store.write_text(f"{relative}.failure-{attempt}.log", exc.raw_output)
                self._warning(f"{role} has a non-retryable provider/CLI error: {exc}")
                raise
            except AgentTimeout as exc:
                self.store.write_text(f"{relative}.failure-{attempt}.log", exc.raw_output)
                self._warning(
                    f"{role} reached its {self.config.agent_timeout_seconds}s limit; "
                    "the candidate will continue without another costly agent process"
                )
                raise
            except AgentFailure as exc:
                self.store.write_text(f"{relative}.failure-{attempt}.log", exc.raw_output)
                self._warning(f"{role} attempt {attempt} failed: {exc}")
                if attempt > self.config.retry_count:
                    raise
                current_prompt = (
                    f"Forge retried this role because the previous process failed: {exc}. "
                    "Continue the same assigned objective from the current workspace state.\n\n"
                    + prompt
                )
                continue
            finally:
                # Do not leave a phantom live card behind when the runner raises
                # an unexpected process or filesystem exception.
                self._activity_finished(activity_key)
            self.store.write_text(f"{relative}.raw.jsonl", result.raw_output)
            self.store.write_text(f"{relative}.response.md", result.text.rstrip() + "\n")
            self.store.record_agent_call(
                role=role,
                model=model,
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

    def _activity_started(
        self,
        key: str,
        *,
        role: str,
        candidate: str,
        model: ModelSpec,
        attempt: int,
    ) -> None:
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
        with self._activity_lock:
            self.state.active_agents.pop(key, None)
            self.store.save_state(self.state)

    def activity_snapshot(self) -> dict[str, dict[str, Any]]:
        with self._activity_lock:
            snapshot = {key: dict(value) for key, value in self.state.active_agents.items()}
        competition = self._competition
        if competition is None:
            return snapshot
        for value in snapshot.values():
            candidate_name = value.get("candidate")
            candidate = competition.candidates.get(str(candidate_name))
            if candidate is None:
                continue
            plan_path = candidate.path / ".forge" / "plan.md"
            if plan_path.is_file():
                state = progress(plan_path.read_text(encoding="utf-8", errors="replace"))
                value["tasks_completed"] = state.completed
                value["tasks_total"] = state.total
            status = subprocess.run(
                ["git", "status", "--short"],
                cwd=candidate.path,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            if status.returncode == 0:
                value["changed_files"] = len(status.stdout.splitlines())
        return snapshot

    def _checkpoint(self) -> None:
        with self._control:
            if self.state.cancel_requested:
                raise RunCancelled()
            while self.state.paused:
                self.state.status = "paused"
                self.store.save_state(self.state)
                self._control.wait(timeout=1)
                if self.state.cancel_requested:
                    raise RunCancelled()
            if self.state.status == "paused":
                self.state.status = "running"
                self.store.save_state(self.state)

    def _warning(self, message: str) -> None:
        self.state.warnings.append(message)
        self.store.event("warning", message)
        self._emit({"kind": "warning", "message": message})

    def _phase(self, phase: str, message: str) -> None:
        self.state.phase = phase
        self._save(message)

    def _save(self, message: str) -> None:
        self.state.message = message
        self.store.save_state(self.state)
        self.store.event("state", message, status=self.state.status, phase=self.state.phase)
        self._emit(
            {
                "kind": "state",
                "message": message,
                "status": self.state.status,
                "phase": self.state.phase,
                "run_id": self.run_id,
            }
        )

    def _emit(self, event: dict[str, Any]) -> None:
        if self.on_event:
            self.on_event(event)
