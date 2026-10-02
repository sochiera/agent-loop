"""Forge cheap-model swarm: three autonomous teams on isolated worktrees.

The swarm consumes the cheap coder pool (GLM 5.3 Flash through OpenCode Go plus
three slots of native Codex GPT-6 Luna), filtered by the current central model
policy at every claim, instead of the sprint tournament draw. A strong planner lays
out a parallel-friendly backlog, then up to three teams run autonomously, each
on its own task:

1. two cheap coders implement the task independently in isolated worktrees,
   each with a different tactic drawn from two of three modes;
2. two other slots from the cheap pool review one version each; blocking
   findings go back to their coder for a bounded revision;
3. a strong reviewer picks the better version; further requests run through a
   bounded winner-fix loop, closed by the winner coder plus its cheap
   reviewer;
4. on approval the controller merges the winner. A conflict consumes the one
   merge attempt and re-enters the failed work as a fresh backlog task.

The ceiling is six concurrent agents on six worktrees (three teams of two
coders; reviewers reuse the coder worktrees). A failed cheap pair is cheap
work lost: the task re-enters the backlog for another pair, the strong roster
never takes over coding, and when a task drops permanently the swarm moves
on. When 70% of the top-priority backlog cohort is done, a fresh planner
visit adds tasks and reprioritizes everything.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .agents import (
    AgentCancelled,
    AgentConfigurationFailure,
    AgentFailure,
    AgentRequest,
    AgentResult,
    AgentRunner,
)
from .artifacts import ArtifactStore
from .catalog import model_identity
from .contracts import (
    ContractError,
    parse_swarm_backlog,
    parse_swarm_replan,
    parse_swarm_review,
    parse_swarm_selection,
)
from .locking import RepositoryExecutionLock
from .models import ModelSpec, RunConfig
from .policy import GLM, OPUS, SOL, SWARM_CODER_ROLE, SWARM_REVIEWER_ROLE, load_policy
from .prompts import (
    swarm_backlog_prompt,
    swarm_coder_prompt,
    swarm_replan_prompt,
    swarm_reviewer_prompt,
    swarm_selection_prompt,
)
from .sprint import CODER_CANDIDATES
from .validation import run_commands


SWARM_AGENTS_CAP = 6
SWARM_WORKTREE_CAP = 6
SWARM_WORKTREES_PER_TEAM = 2
DEFAULT_SWARM_TEAMS = 3
DEFAULT_MIN_BACKLOG = 15
DEFAULT_READY_THRESHOLD = 0.7
DEFAULT_MAX_PAIR_ATTEMPTS = 2
DEFAULT_MAX_FIX_ROUNDS = 2
SWARM_SCHEMA = 1


class SwarmCancelled(RuntimeError):
    pass


class SwarmFailed(RuntimeError):
    pass


class SwarmGitError(RuntimeError):
    pass


def repository_context(repo: Path) -> str:
    try:
        listed = subprocess.run(
            ["git", "-C", str(repo), "ls-files"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return "The repository could not be inspected."
    if not listed:
        return "The selected branch has no tracked product files."
    preview = "\n".join(f"- {name}" for name in listed[:200])
    suffix = "" if len(listed) <= 200 else f"\n- ... and {len(listed) - 200} more"
    return f"Tracked files ({len(listed)} total):\n{preview}{suffix}"


def environment_context() -> str:
    import shutil

    available = [
        command for command in ("git", "python3", "pytest", "node", "npm", "make", "cargo", "go")
        if shutil.which(command)
    ]
    return f"Available commands: {', '.join(available) or 'none'}."


@dataclass
class SwarmTask:
    id: str
    title: str
    area: str
    description: str
    acceptance_criteria: list[str]
    validation_commands: list[str]
    priority: int
    origin: str = "planner"
    status: str = "pending"
    attempts: int = 0
    summary: str = ""
    commit: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "area": self.area,
            "description": self.description,
            "acceptance_criteria": list(self.acceptance_criteria),
            "validation_commands": list(self.validation_commands),
            "priority": self.priority,
            "origin": self.origin,
            "status": self.status,
            "attempts": self.attempts,
            "summary": self.summary,
            "commit": self.commit,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SwarmTask":
        return cls(
            id=str(value["id"]),
            title=str(value.get("title", "")),
            area=str(value.get("area", "")),
            description=str(value.get("description", "")),
            acceptance_criteria=[str(item) for item in value.get("acceptance_criteria", [])],
            validation_commands=[str(item) for item in value.get("validation_commands", [])],
            priority=int(value["priority"]),
            origin=str(value.get("origin", "planner")),
            status=str(value.get("status", "pending")),
            attempts=int(value.get("attempts", 0)),
            summary=str(value.get("summary", "")),
            commit=str(value.get("commit", "")),
        )


@dataclass
class SwarmTeam:
    id: int
    task_id: str
    phase: str
    modes: list[str]
    coders: list[dict[str, Any]]
    reviewers: list[dict[str, Any]]
    versions: dict[str, dict[str, Any]] = field(default_factory=dict)
    selection: dict[str, Any] = field(default_factory=dict)
    winner: str = ""
    fix_round: int = 0
    review_round: int = 0
    base_sha: str = ""
    worktrees: dict[str, str] = field(default_factory=dict)
    branches: dict[str, str] = field(default_factory=dict)
    sessions: dict[str, str] = field(default_factory=dict)
    arrivals: list[str] = field(default_factory=list)

    def coder(self, mode: str) -> dict[str, Any]:
        for entry in self.coders:
            if entry["mode"] == mode:
                return entry
        raise SwarmFailed(f"team {self.id} has no coder in mode {mode}")

    def reviewer_for(self, mode: str) -> dict[str, Any]:
        return self.reviewers[0] if mode == self.modes[0] else self.reviewers[1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "phase": self.phase,
            "modes": list(self.modes),
            "coders": [dict(entry) for entry in self.coders],
            "reviewers": [dict(entry) for entry in self.reviewers],
            "versions": {mode: dict(data) for mode, data in self.versions.items()},
            "selection": dict(self.selection),
            "winner": self.winner,
            "fix_round": self.fix_round,
            "review_round": self.review_round,
            "base_sha": self.base_sha,
            "worktrees": dict(self.worktrees),
            "branches": dict(self.branches),
            "sessions": dict(self.sessions),
            "arrivals": list(self.arrivals),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SwarmTeam":
        return cls(
            id=int(value["id"]),
            task_id=str(value["task_id"]),
            phase=str(value["phase"]),
            modes=[str(mode) for mode in value["modes"]],
            coders=[dict(entry) for entry in value["coders"]],
            reviewers=[dict(entry) for entry in value["reviewers"]],
            versions={str(mode): dict(data) for mode, data in value.get("versions", {}).items()},
            selection=dict(value.get("selection", {})),
            winner=str(value.get("winner", "")),
            fix_round=int(value.get("fix_round", 0)),
            review_round=int(value.get("review_round", 0)),
            base_sha=str(value.get("base_sha", "")),
            worktrees={str(mode): str(path) for mode, path in value.get("worktrees", {}).items()},
            branches={str(mode): str(branch) for mode, branch in value.get("branches", {}).items()},
            sessions={str(key): str(value) for key, value in value.get("sessions", {}).items()},
            arrivals=[str(item) for item in value.get("arrivals", [])],
        )


class SwarmRunState:
    """Durable swarm state persisted under the run's artifact store."""

    def __init__(
        self,
        *,
        tasks: list[SwarmTask] | None = None,
        teams: list[SwarmTeam] | None = None,
        planner_visits: int = 0,
        replanned_levels: list[int] | None = None,
        status: str = "running",
        message: str = "",
        warnings: list[str] | None = None,
        preserved: list[dict[str, Any]] | None = None,
    ):
        self.tasks = tasks or []
        self.teams = teams or []
        self.planner_visits = planner_visits
        self.replanned_levels = replanned_levels or []
        self.status = status
        self.message = message
        self.warnings = warnings or []
        # Worktrees Forge left in place because they hold unmerged work.
        self.preserved = preserved or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SWARM_SCHEMA,
            "tasks": [task.to_dict() for task in self.tasks],
            "teams": [team.to_dict() for team in self.teams],
            "planner_visits": self.planner_visits,
            "replanned_levels": list(self.replanned_levels),
            "status": self.status,
            "message": self.message,
            "warnings": list(self.warnings),
            "preserved": [dict(entry) for entry in self.preserved],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SwarmRunState":
        if int(value.get("schema", 0)) != SWARM_SCHEMA:
            raise SwarmFailed(
                f"unsupported swarm state schema: {value.get('schema')}; start a new run"
            )
        return cls(
            tasks=[SwarmTask.from_dict(item) for item in value.get("tasks", [])],
            teams=[SwarmTeam.from_dict(item) for item in value.get("teams", [])],
            planner_visits=int(value.get("planner_visits", 0)),
            replanned_levels=[int(item) for item in value.get("replanned_levels", [])],
            status=str(value.get("status", "running")),
            message=str(value.get("message", "")),
            warnings=[str(item) for item in value.get("warnings", [])],
            preserved=[dict(item) for item in value.get("preserved", [])],
        )


class SwarmController:
    """Run the cheap-model swarm against one local repository branch."""

    def __init__(
        self,
        config: RunConfig,
        *,
        run_id: str | None = None,
        runner: AgentRunner | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        state_home: Path | None = None,
        resume: bool = False,
        rng: random.Random | None = None,
        teams: int = DEFAULT_SWARM_TEAMS,
        min_backlog: int = DEFAULT_MIN_BACKLOG,
        ready_threshold: float = DEFAULT_READY_THRESHOLD,
        max_pair_attempts: int = DEFAULT_MAX_PAIR_ATTEMPTS,
        max_fix_rounds: int = DEFAULT_MAX_FIX_ROUNDS,
    ):
        if not resume:
            config.validate()
        self.config = config
        self.repo = Path(config.repo).expanduser().resolve()
        self.brief_path = Path(config.brief).expanduser().resolve()
        self.run_id = run_id or time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        self.runner = runner or AgentRunner(policy_path=config.policy_path or None)
        self.on_event = on_event
        self.store = ArtifactStore(self.repo, self.run_id)
        self.state_home = (
            state_home
            or Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "forge"
        )
        self.worktree_root = (self.state_home / "worktrees" / self.run_id / "swarm").resolve()
        self.rng = rng or random.Random()
        self.teams_limit = teams
        self.min_backlog = min_backlog
        self.ready_threshold = ready_threshold
        self.max_pair_attempts = max_pair_attempts
        self.max_fix_rounds = max_fix_rounds
        self._conflict_counts: dict[str, int] = {}
        self._lock = threading.RLock()
        self._control = threading.Condition()
        self._executor: ThreadPoolExecutor | None = None
        self._paused = False
        self._cancel_requested = False

        self.planner = self._staff_model("planner", allowed={SOL, OPUS, GLM})
        self.strong_reviewer = self._staff_model("reviewer", allowed={SOL, OPUS})
        if len(config.cheap_pool) < 4:
            raise ValueError(
                "the swarm consumes the cheap pool; RunConfig.cheap_pool must "
                "hold at least four slots (two coder slots plus two reviewer slots)"
            )
        if resume:
            self.state = SwarmRunState.from_dict(self._load_swarm_state())
        else:
            self._install_local_excludes()
            self.state = SwarmRunState()

    def _install_local_excludes(self) -> None:
        """Add the .forge artifact store to the local git excludes; do not
        touch the product's .gitignore."""

        exclude = self.repo / ".git" / "info" / "exclude"
        lines = []
        if exclude.is_file():
            lines = exclude.read_text(encoding="utf-8").splitlines()
        wanted = (".forge/",)
        for entry in wanted:
            if entry not in lines:
                lines.append(entry)
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ------------------------------------------------------------------
    # Swarm contract validation

    def _staff_model(self, role: str, *, allowed: set[ModelSpec]) -> ModelSpec:
        spec = self.config.models[role]
        identities = {model_identity(model) for model in allowed}
        if model_identity(spec) not in identities:
            names = ", ".join(sorted(model.display() for model in allowed))
            raise ValueError(
                f"swarm {role} must be a strong model ({names}); got {spec.display()}"
            )
        # A resumed config skips validate(); the current policy still decides.
        if not load_policy(self.config.policy_path or None).allows(spec, role):
            raise ValueError(
                f"swarm {role} {spec.display()} is outside the current model policy"
            )
        return spec

    # ------------------------------------------------------------------
    # Persistence

    def _load_swarm_state(self) -> dict[str, Any]:
        path = self.store.root / "swarm" / "state.json"
        if not path.is_file():
            raise SwarmFailed(f"swarm state does not exist: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def persist(self, message: str = "") -> None:
        with self._lock:
            state = self.state
            state.message = message or state.message
            self.store.write_data("swarm/state.json", state.to_dict())
            if not (self.store.root / "config.json").is_file():
                self.store.write_data("config.json", self.config.to_dict())
        self.store.event(
            "swarm.state", self.state.message, tasks=len(self.state.tasks),
            teams=len(self.state.teams),
        )
        if self.on_event is not None:
            self.on_event(
                {"kind": "swarm.state", "message": self.state.message, "status": self.state.status}
            )

    def store_event(self, kind: str, message: str, **fields: Any) -> None:
        self.store.event(f"swarm.{kind}", message, **fields)

    def _warning(self, message: str) -> None:
        with self._lock:
            self.state.warnings.append(message)
        self.store_event("warning", message)

    # ------------------------------------------------------------------
    # Operator controls

    def pause(self) -> None:
        with self._control:
            self._paused = True

    def resume_run(self) -> None:
        with self._control:
            self._paused = False
            self._control.notify_all()

    def cancel(self) -> None:
        with self._control:
            self._cancel_requested = True
        # Stop in-flight agents too, so a SIGTERM reaches the terminal state
        # before the supervisor escalates to SIGKILL.
        cancel_agents = getattr(self.runner, "cancel", None)
        if callable(cancel_agents):
            cancel_agents()

    def _checkpoint(self) -> None:
        with self._control:
            if self._cancel_requested:
                raise SwarmCancelled()
            while self._paused:
                self._control.wait(timeout=0.2)

    # ------------------------------------------------------------------
    # Git helpers

    def _git(
        self,
        *args: str,
        cwd: Path | None = None,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> str:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=str(cwd or self.repo),
                text=True,
                capture_output=True,
                env=env,
            )
        except OSError as exc:  # e.g. the worktree directory vanished
            if check:
                raise SwarmGitError(f"git {' '.join(args)} failed in {cwd or self.repo}: {exc}")
            return str(exc)
        if result.returncode != 0:
            if check:
                raise SwarmGitError(
                    f"git {' '.join(args)} failed in {cwd or self.repo}: {result.stderr.strip()}"
                )
            return result.stderr.strip()
        return result.stdout.strip()

    def _component(self, task_id: str, mode: str, attempt: int = 0) -> str:
        # A retried pair gets fresh names: an earlier attempt's worktree and
        # branch may be preserved in place.
        suffix = f"-a{attempt + 1}" if attempt else ""
        return f"{task_id}-{mode}{suffix}".replace("/", "_")

    def _candidate_branch(self, task_id: str, mode: str, attempt: int = 0) -> str:
        return f"forge/{self.run_id}/swarm/{self._component(task_id, mode, attempt)}"

    def _worktree_path(self, task_id: str, mode: str, attempt: int = 0) -> Path:
        return self.worktree_root / self._component(task_id, mode, attempt)

    def _create_worktree(self, task_id: str, mode: str, base_sha: str, attempt: int = 0) -> Path:
        path = self._worktree_path(task_id, mode, attempt)
        branch = self._candidate_branch(task_id, mode, attempt)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._git("check-ref-format", "--branch", branch)
        if path.exists():
            raise SwarmGitError(f"worktree path already exists: {path}")
        self._git("worktree", "add", "-b", branch, str(path), base_sha)
        return path

    def _commit_worktree(self, path: Path, task_id: str, mode: str) -> str:
        self._git("add", "-A", cwd=path)
        if self._git("diff", "--cached", "--quiet", cwd=path, check=False):
            return ""
        self._git("commit", "-m", f"Forge swarm {task_id} ({mode})", cwd=path)
        return self._git("rev-parse", "HEAD", cwd=path)

    def _patch_over_base(self, path: Path, base_sha: str) -> str:
        """Everything the worktree holds over the base: commits, edits and
        untracked files (coders leave new files uncommitted). A throwaway
        index keeps the worktree's own index untouched."""

        with tempfile.TemporaryDirectory() as scratch:
            env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}
            self._git("read-tree", "HEAD", cwd=path, env=env)
            self._git("add", "-A", cwd=path, env=env)
            return self._git("diff", "--cached", "--binary", base_sha, cwd=path, env=env)

    def _holds_unmerged_work(self, path: Path, base_sha: str) -> bool:
        """True unless the worktree provably holds nothing over its base; any
        doubt (missing base, git error) counts as work to keep."""

        if not path.exists():
            return False
        if not base_sha:
            return True
        try:
            return bool(self._patch_over_base(path, base_sha))
        except SwarmGitError:
            return True

    def _cleanup_worktrees(self, team: SwarmTeam, *, reason: str, merged: str = "") -> None:
        """Release the team's worktrees. Only the merged winner and worktrees
        with nothing over the base are removed; rejected or unfinished work
        stays in place, recorded in the durable state for the operator."""

        kept: list[dict[str, Any]] = []
        for mode, path in list(team.worktrees.items()):
            branch = team.branches.get(mode) or self._candidate_branch(team.task_id, mode)
            if mode != merged and self._holds_unmerged_work(Path(path), team.base_sha):
                kept.append(
                    {
                        "task": team.task_id,
                        "team": team.id,
                        "mode": mode,
                        "path": path,
                        "branch": branch,
                        "base_sha": team.base_sha,
                        "reason": reason,
                    }
                )
                continue
            self._git("worktree", "remove", "--force", path, check=False)
            self._git("branch", "-D", branch, check=False)
        self._git("worktree", "prune", check=False)
        with self._lock:
            self.state.preserved.extend(kept)
            team.worktrees.clear()
            team.branches.clear()
        for entry in kept:
            self.store_event(
                "worktree-preserved",
                f"kept {entry['path']} with unmerged {entry['mode']} work for "
                f"{entry['task']}: {reason}",
                task=entry["task"],
                mode=entry["mode"],
            )

    def _active_worktree_count(self) -> int:
        with self._lock:
            return sum(len(team.worktrees) for team in self.state.teams)

    # ------------------------------------------------------------------
    # Agent invocation

    def _invoke(self, job: dict[str, Any]) -> AgentResult:
        self._checkpoint()
        role = job["role"]
        spec = job["spec"]
        prompt = job["prompt"]
        cwd = job["cwd"]
        relative = job["relative"]
        attempts = self.config.retry_count + 1
        session = job.get("session_id")
        dropped_session = False
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                result = self.runner.run(
                    AgentRequest(
                        role=role,
                        model=spec,
                        prompt=prompt,
                        cwd=cwd,
                        session_id=session,
                        schema=None,
                        access=job.get("access", "write"),
                        timeout_seconds=self.config.agent_timeout_seconds,
                    )
                )
            except AgentCancelled:
                with self._control:
                    if self._cancel_requested:
                        raise SwarmCancelled() from None
                    raise
            except AgentConfigurationFailure as exc:
                last_error = exc
                self.store.write_text(f"{relative}.failure-{attempt}.log", exc.raw_output)
                if session and not dropped_session:
                    dropped_session = True
                    session = None
                    attempts += 1
                    continue
                break
            except AgentFailure as exc:
                last_error = exc
                self.store.write_text(f"{relative}.failure-{attempt}.log", exc.raw_output)
                if attempt < attempts:
                    prompt = (
                        f"Forge retried this swarm role because the provider process "
                        f"failed: {exc}. Continue from the durable worktree and answer "
                        "with the same requested contract.\n\n" + job["prompt"]
                    )
                    continue
                break
            self.store.write_text(f"{relative}.response.md", result.text.rstrip() + "\n")
            self.store.record_agent_call(
                role=role,
                model=spec,
                result=result,
                cycle=0,
                candidate=f"T{job.get('team_id', '')}:{job.get('mode', role)}",
            )
            return result
        assert last_error is not None
        raise last_error

    def _run_agents(
        self, jobs: list[dict[str, Any]]
    ) -> list[tuple[dict[str, Any], AgentResult | Exception]]:
        if not jobs:
            return []
        executor = self._executor
        assert executor is not None
        futures = [(job, executor.submit(self._invoke, job)) for job in jobs]
        results: list[tuple[dict[str, Any], AgentResult | Exception]] = []
        for job, future in futures:
            try:
                results.append((job, future.result()))
            except Exception as exc:
                results.append((job, exc))
        return results

    # ------------------------------------------------------------------
    # Planner passes

    def _brief_text(self) -> str:
        return self.brief_path.read_text(encoding="utf-8")

    def _plan_backlog(self) -> None:
        self._checkpoint()
        prompt = swarm_backlog_prompt(
            brief=self._brief_text(),
            minimum_tasks=self.min_backlog,
            repository_context=repository_context(self.repo),
            environment_context=environment_context(),
        )
        result = self._invoke(
            {
                "role": "planner",
                "spec": self.planner,
                "prompt": prompt,
                "cwd": self.repo,
                "access": "read",
                "relative": "swarm/planner/backlog",
                "team_id": 0,
            }
        )
        parsed = parse_swarm_backlog(result.text, minimum_tasks=self.min_backlog)
        with self._lock:
            self.state.tasks = [
                SwarmTask(
                    id=item["id"],
                    title=item["title"],
                    area=item["area"],
                    description=item["description"],
                    acceptance_criteria=item["acceptance_criteria"],
                    validation_commands=item["validation_commands"],
                    priority=item["priority"],
                    origin="planner",
                )
                for item in parsed["tasks"]
            ]
            self.state.planner_visits += 1
        self.persist("planner laid the swarm backlog")

    def _reprioritize(self) -> None:
        self._checkpoint()
        unfinished = [
            task.to_dict()
            for task in self.state.tasks
            if task.status in {"pending", "in_progress"} and not self._team_owns(task.id)
        ]
        if not unfinished:
            return
        prompt = swarm_replan_prompt(tasks=unfinished)
        result = self._invoke(
            {
                "role": "planner",
                "spec": self.planner,
                "prompt": prompt,
                "cwd": self.repo,
                "access": "read",
                "relative": "swarm/planner/replan",
                "team_id": 0,
            }
        )
        parsed = parse_swarm_replan(
            result.text, unfinished=tuple(item["id"] for item in unfinished)
        )
        with self._lock:
            for item in parsed["new_tasks"]:
                self.state.tasks.append(
                    SwarmTask(
                        id=item["id"],
                        title=item["title"],
                        area=item["area"],
                        description=item["description"],
                        acceptance_criteria=item["acceptance_criteria"],
                        validation_commands=item["validation_commands"],
                        priority=item["priority"],
                        origin="replan",
                    )
                )
            priorities = parsed["priorities"]
            for task in self.state.tasks:
                if task.id in priorities and task.status in {"pending", "in_progress"}:
                    task.priority = priorities[task.id]
            self.state.planner_visits += 1
        self.persist("planner added tasks and reprioritized the backlog")

    def _team_owns(self, task_id: str) -> bool:
        return any(team.task_id == task_id for team in self.state.teams)

    # ------------------------------------------------------------------
    # Threshold logic

    def _maybe_replan(self) -> bool:
        with self._lock:
            active = [
                task
                for task in self.state.tasks
                if task.status in {"pending", "in_progress", "done"}
            ]
            levels = sorted({task.priority for task in active})
            if not levels:
                return False
            top = levels[0]
            if top in self.state.replanned_levels:
                return False
            cohort = [
                task
                for task in self.state.tasks
                if task.priority == top and task.status in {"pending", "in_progress", "done"}
            ]
            done = sum(task.status == "done" for task in cohort)
            if not cohort or done / len(cohort) < self.ready_threshold:
                return False
        self._reprioritize()
        with self._lock:
            self.state.replanned_levels.append(top)
        return True

    # ------------------------------------------------------------------
    # Team lifecycle

    def _claim_capacity(self) -> bool:
        with self._lock:
            active = len(self.state.teams)
            if active >= self.teams_limit:
                return False
            return active * SWARM_WORKTREES_PER_TEAM + SWARM_WORKTREES_PER_TEAM <= SWARM_WORKTREE_CAP

    def _claim_task(self) -> SwarmTeam | None:
        with self._lock:
            if not self._claim_capacity():
                return None
            candidates = [
                task
                for task in self.state.tasks
                if task.status == "pending" and not self._team_owns(task.id)
            ]
            if not candidates:
                return None
            task = sorted(candidates, key=lambda item: (item.priority, item.id))[0]
            pool = self._allowed_pool()
            task.status = "in_progress"
            modes = self.rng.sample(list(CODER_CANDIDATES), 2)
            slots = self.rng.sample(pool, 4)
            coders = [
                {
                    "mode": mode,
                    "spec": {"provider": slot.provider, "model": slot.model, "effort": slot.effort},
                    "display": slot.display(),
                }
                for mode, slot in zip(modes, slots[:2])
            ]
            reviewers = [
                {
                    "reviewer": index,
                    "spec": {"provider": slot.provider, "model": slot.model, "effort": slot.effort},
                    "display": slot.display(),
                }
                for index, slot in enumerate(slots[2:4], start=1)
            ]
            team = SwarmTeam(
                id=max((t.id for t in self.state.teams), default=0) + 1,
                task_id=task.id,
                phase="code",
                modes=modes,
                coders=coders,
                reviewers=reviewers,
            )
            self.state.teams.append(team)
        self.store_event("swarm.claim", f"team {team.id} claimed {task.id}", task=task.id)
        return team

    def _allowed_pool(self) -> list[ModelSpec]:
        """Cheap slots the current central policy allows; a persisted config
        (resume) can still name models the policy has since removed."""

        snapshot = load_policy(self.config.policy_path or None)
        pool = [
            ModelSpec(spec.provider, spec.model, spec.effort)
            for spec in self.config.cheap_pool
            if snapshot.allows(spec, SWARM_CODER_ROLE)
        ]
        if len(pool) < 4:
            raise SwarmFailed(
                f"the current model policy allows {len(pool)} of the cheap pool slots; "
                "the swarm needs four"
            )
        return pool

    def _restaff_off_policy(self) -> None:
        """Before resuming, swap persisted team members the current policy
        refuses for allowed cheap slots, so recovery never launches them."""

        snapshot = load_policy(self.config.policy_path or None)
        with self._lock:
            teams = list(self.state.teams)
        for team in teams:
            for entry in (*team.coders, *team.reviewers):
                spec = ModelSpec(**entry["spec"])
                if snapshot.allows(spec, SWARM_CODER_ROLE):
                    continue
                slot = self.rng.choice(self._allowed_pool())
                with self._lock:
                    entry["spec"] = {
                        "provider": slot.provider, "model": slot.model, "effort": slot.effort,
                    }
                    entry["display"] = slot.display()
                self._warning(
                    f"team {team.id} ({team.task_id}): {spec.display()} is outside the "
                    f"current model policy; restaffed with {slot.display()}"
                )

    def _release_missing_worktrees(self) -> None:
        """A resumed team whose worktree vanished cannot continue: drop it
        instead of letting an agent run in a bare directory."""

        with self._lock:
            teams = list(self.state.teams)
        for team in teams:
            missing = [
                mode for mode, path in team.worktrees.items() if not (Path(path) / ".git").exists()
            ]
            if not team.worktrees and team.phase not in {"code", "review", "revise"}:
                missing = list(team.modes)
            if missing:
                self._drop_team(team, f"worktree missing on resume: {', '.join(missing)}")

    def _task_of(self, team: SwarmTeam) -> SwarmTask:
        with self._lock:
            for task in self.state.tasks:
                if task.id == team.task_id:
                    return task
        raise SwarmFailed(f"missing task {team.task_id}")

    def _drop_team(self, team: SwarmTeam, reason: str) -> None:
        """Cheap-pair failure: the task re-enters the backlog for another pair."""
        task = self._task_of(team)
        try:
            self._capture_all_patches(team, counter=task.attempts + 1)
        finally:
            self._cleanup_worktrees(team, reason=f"pair dropped: {reason}")
        with self._lock:
            self.state.teams = [t for t in self.state.teams if t.id != team.id]
            task.attempts += 1
            if task.attempts < self.max_pair_attempts:
                task.status = "pending"
            else:
                task.status = "dropped"
        self.store_event(
            "swarm.pair-failed",
            f"task {task.id} re-enters the backlog: {reason}",
            attempt=task.attempts,
        )
        self._warning(f"team for {task.id} failed cheaply, reason: {reason}")
        self.persist()

    def _capture_all_patches(self, team: SwarmTeam, *, counter: int) -> None:
        for mode in team.modes:
            path = team.worktrees.get(mode)
            if not path:
                continue
            try:
                self.store.write_text(
                    f"swarm/tasks/{team.task_id}/attempt-{counter}-{mode}.patch",
                    self._patch_over_base(Path(path), team.base_sha),
                )
            except SwarmGitError:
                pass

    # Worktrees preparation ----------------------------------------------

    def _prepare_worktrees(self, team: SwarmTeam) -> None:
        with self._lock:
            if team.worktrees or team.phase not in {"code", "review", "revise"}:
                return
        task = self._task_of(team)
        if self._active_worktree_count() + SWARM_WORKTREES_PER_TEAM > SWARM_WORKTREE_CAP:
            return
        with self._lock:
            base = self._git("rev-parse", "HEAD")
            team.base_sha = base
            for coder in team.coders:
                mode = coder["mode"]
                path = self._create_worktree(task.id, mode, base, task.attempts)
                team.worktrees[mode] = str(path)
                team.branches[mode] = self._candidate_branch(task.id, mode, task.attempts)
        self.store_event(
            "swarm.worktrees", f"prepared {len(team.worktrees)} worktrees for {task.id}"
        )

    # Jobs ----------------------------------------------------------------

    def _coder_job(
        self,
        team: SwarmTeam,
        mode: str,
        *,
        blocking_findings: list[dict[str, Any]],
        relative: str,
    ) -> dict[str, Any]:
        task = self._task_of(team)
        previous = team.versions.get(mode, {}).get("summary", "") if mode in team.versions else ""
        return {
            "role": SWARM_CODER_ROLE,
            "spec": ModelSpec(**team.coder(mode)["spec"]),
            "prompt": swarm_coder_prompt(
                task={
                    "id": task.id,
                    "title": task.title,
                    "description": task.description,
                    "acceptance_criteria": task.acceptance_criteria,
                    "validation_commands": task.validation_commands,
                },
                mode=mode,
                blocking_findings=blocking_findings,
                previous_summary=previous,
            ),
            "cwd": Path(team.worktrees[mode]),
            "access": "write",
            "relative": relative,
            "mode": mode,
            "team_id": team.id,
        }

    def _reviewer_job(self, team: SwarmTeam, mode: str, relative: str) -> dict[str, Any]:
        task = self._task_of(team)
        data = team.versions.get(mode, {})
        return {
            "role": SWARM_REVIEWER_ROLE,
            "spec": ModelSpec(**team.reviewer_for(mode)["spec"]),
            "prompt": swarm_reviewer_prompt(
                task={
                    "id": task.id,
                    "title": task.title,
                    "description": task.description,
                    "acceptance_criteria": task.acceptance_criteria,
                },
                validation=list(data.get("validation", [])),
            ),
            "cwd": Path(team.worktrees[mode]),
            "access": "read",
            "relative": relative,
            "mode": mode,
            "team_id": team.id,
        }

    def _selection_job(self, team: SwarmTeam) -> dict[str, Any]:
        task = self._task_of(team)
        dossier = [
            {
                "name": mode,
                "model": team.versions.get(mode, {}).get("model", ""),
                "summary": team.versions.get(mode, {}).get("summary", ""),
                "review": team.versions.get(mode, {}).get("review", {}),
                "validation": team.versions.get(mode, {}).get("validation", []),
            }
            for mode in team.modes
        ]
        return {
            "role": "reviewer",
            "spec": self.strong_reviewer,
            "prompt": swarm_selection_prompt(
                task={
                    "id": task.id,
                    "title": task.title,
                    "description": task.description,
                    "acceptance_criteria": task.acceptance_criteria,
                },
                candidates=dossier,
                submitted=tuple(team.modes),
            ),
            "cwd": self.repo,
            "access": "read",
            "relative": f"swarm/tasks/{task.id}/select",
            "team_id": team.id,
        }

    def _round_jobs(self, team: SwarmTeam) -> list[dict[str, Any]]:
        task_id = team.task_id
        phase = team.phase
        jobs: list[dict[str, Any]] = []
        if phase == "code":
            for mode in team.modes:
                jobs.append(
                    self._coder_job(
                        team,
                        mode,
                        blocking_findings=[],
                        relative=f"swarm/tasks/{task_id}/code-{mode}",
                    )
                )
        elif phase == "review":
            for mode in team.modes:
                jobs.append(
                    self._reviewer_job(
                        team, mode, f"swarm/tasks/{task_id}/review-{team.review_round}-{mode}"
                    )
                )
        elif phase == "revise":
            for mode in team.modes:
                review = team.versions[mode].get("review", {})
                findings = list(review.get("blocking", []))
                if not findings:
                    continue
                jobs.append(
                    self._coder_job(
                        team,
                        mode,
                        blocking_findings=findings,
                        relative=f"swarm/tasks/{task_id}/revise-{team.review_round}-{mode}",
                    )
                )
        elif phase == "select":
            team.selection["attempts"] = team.selection.get("attempts", 0) + 1
            jobs.append(self._selection_job(team))
        elif phase == "winner-fix":
            if not team.winner:
                raise SwarmFailed(f"team {team.id} lost its winner before the fix loop")
            last_job = team.selection.get("last_winner_job", "")
            if last_job == "":
                blocking = [
                    {"problem": "strong feedback", "detail": note}
                    for note in team.selection.get("feedback", [])
                ]
                jobs.append(
                    self._coder_job(
                        team,
                        team.winner,
                        blocking_findings=blocking,
                        relative=f"swarm/tasks/{task_id}/winner-fix-{team.fix_round}",
                    )
                )
            elif last_job == "coder":
                jobs.append(
                    self._reviewer_job(
                        team,
                        team.winner,
                        f"swarm/tasks/{task_id}/winner-check-{team.fix_round}",
                    )
                )
            else:
                raise SwarmFailed(f"team {team.id} winner-fix state is inconsistent")
        return jobs

    def _run_validation(self, team: SwarmTeam, mode: str) -> list[dict[str, Any]]:
        task = self._task_of(team)
        commands = tuple(task.validation_commands)
        if not commands:
            return [
                {
                    "command": "(none)",
                    "status": "skipped",
                    "stdout": "the task defines no validation commands",
                }
            ]
        return run_commands(commands, Path(team.worktrees[mode]))

    # Applying results -----------------------------------------------------

    def _apply_code(self, team: SwarmTeam, mode: str, result: AgentResult) -> None:
        with self._lock:
            data = team.versions.setdefault(
                mode,
                {
                    "model": team.coder(mode)["display"],
                    "summary": "",
                    "review": {},
                    "validation": [],
                },
            )
            data["summary"] = result.text.strip()
            data["committed"] = False

    def _apply_review(self, team: SwarmTeam, mode: str, result: AgentResult) -> None:
        try:
            parsed = parse_swarm_review(result.text)
        except ContractError as exc:
            parsed = {
                "verdict": "fix",
                "summary": "Forge could not read the review contract",
                "blocking": [{"problem": "unparseable review", "detail": str(exc)}],
            }
        with self._lock:
            team.versions[mode]["review"] = parsed

    def _apply_winner_review(self, team: SwarmTeam, result: AgentResult) -> None:
        try:
            parsed = parse_swarm_review(result.text)
        except ContractError as exc:
            parsed = {
                "verdict": "approve",
                "summary": f"winner-check contract fallback: {exc}",
                "blocking": [],
            }
        with self._lock:
            team.versions[team.winner].setdefault("winner_reviews", []).append(parsed)
            team.selection["last_winner_job"] = "reviewer"

    def _apply_selection(self, team: SwarmTeam, result: AgentResult) -> None:
        try:
            parsed = parse_swarm_selection(result.text, submitted=tuple(team.modes))
        except ContractError:
            self._warning(f"task {team.task_id}: selection contract rejected; the team retries")
            return
        with self._lock:
            parsed["attempts"] = team.selection.get("attempts", 0)
            team.selection = parsed

    def _apply_results(
        self, results: list[tuple[dict[str, Any], AgentResult | Exception]]
    ) -> None:
        failed: dict[int, str] = {}
        cancelled = False
        for job, outcome in results:
            # _invoke turns a requested cancel into SwarmCancelled. Finished
            # siblings are still applied so their results are not lost.
            if isinstance(outcome, (AgentCancelled, SwarmCancelled)):
                cancelled = True
                self.store_event(
                    "agent-cancelled",
                    f"{job['role']} cancelled {job['relative']}",
                    team=job.get("team_id", 0),
                    mode=job.get("mode", ""),
                )
                continue
            with self._lock:
                team = next((t for t in self.state.teams if t.id == job["team_id"]), None)
            if team is None:
                continue
            if isinstance(outcome, Exception):
                if team.id not in failed:
                    failed[team.id] = f"{job['role']} failed: {outcome}"
                self.store_event(
                    "swarm.agent-failed",
                    f"{job['role']} failed: {outcome}",
                    team=team.id,
                    mode=job.get("mode", ""),
                )
                continue
            self.store_event(
                "agent-done",
                f"{job['role']} finished {job['relative']}",
                team=team.id,
                mode=job.get("mode", ""),
            )
            if job["role"] == SWARM_CODER_ROLE:
                self._apply_code(team, job["mode"], outcome)
                if team.phase == "winner-fix":
                    with self._lock:
                        team.selection["last_winner_job"] = "coder"
            elif job["role"] == SWARM_REVIEWER_ROLE:
                if team.phase == "winner-fix":
                    self._apply_winner_review(team, outcome)
                else:
                    self._apply_review(team, job["mode"], outcome)
            elif job["role"] == "reviewer":
                self._apply_selection(team, outcome)
        # A cancelled round keeps every team and worktree for recovery; a
        # failure may only be the cancellation's side effect.
        if cancelled:
            failed.clear()
        for team_id, reason in failed.items():
            with self._lock:
                team = next((t for t in self.state.teams if t.id == team_id), None)
            if team is not None:
                self._drop_team(team, reason)
        # Every worker result reaches durable state, even when its phase
        # does not advance this round.
        self.persist(f"applied {len(results)} swarm agent results")
        if cancelled:
            raise SwarmCancelled()

    # Phase transitions -----------------------------------------------------

    def _advance(self, team: SwarmTeam) -> None:
        with self._lock:
            phase = team.phase
        if phase == "code":
            for mode in team.modes:
                data = team.versions[mode]
                if not data.get("validation"):
                    data["validation"] = self._run_validation(team, mode)
            team.phase = "review"
            self.persist()
        elif phase == "review":
            verdicts = {team.versions[m]["review"].get("verdict") for m in team.modes}
            needs_fix = "fix" in verdicts
            if needs_fix and team.review_round < self.max_fix_rounds:
                team.review_round += 1
                team.phase = "revise"
            else:
                if needs_fix:
                    self._warning(
                        f"task {team.task_id} submits after {team.review_round} cheap revision "
                        "rounds with open review findings attached"
                    )
                team.phase = "select"
            self.persist()
        elif phase == "revise":
            for mode in team.modes:
                data = team.versions[mode]
                if data.get("review", {}).get("blocking", []):
                    data["validation"] = self._run_validation(team, mode)
            team.phase = "review"
            self.persist()
        elif phase == "select":
            self._advance_selection(team)
        elif phase == "winner-fix":
            self._advance_winner_fix(team)

    def _advance_selection(self, team: SwarmTeam) -> None:
        selection = team.selection
        winner = selection.get("winner", "")
        if not winner:
            if selection.get("attempts", 0) >= 3:
                self._drop_team(team, "the strong reviewer selection contract failed")
                return
            self._warning(f"task {team.task_id}: selection re-asked after contract failure")
            self.persist()
            return
        team.winner = winner
        feedback = list(selection.get("feedback", []))
        if not feedback:
            self._deliver(team)
            return
        team.fix_round = 1
        team.selection["last_winner_job"] = ""
        team.phase = "winner-fix"
        self.persist()

    def _advance_winner_fix(self, team: SwarmTeam) -> None:
        winner = team.winner
        data = team.versions[winner]
        last_job = team.selection.get("last_winner_job", "")
        if not last_job:
            return  # the coder fix job has not been applied yet
        if last_job == "coder":
            # The winner-check reviewer runs next and records "reviewer".
            data["committed"] = True
            data["validation"] = self._run_validation(team, winner)
            self.persist(f"task {team.task_id} winner fix {team.fix_round} awaits its check")
            return
        reviews = data.get("winner_reviews", [])
        if not reviews:
            return
        latest = reviews[-1]
        blocking = list(latest.get("blocking", []))
        if not blocking:
            self._deliver(team)
            return
        if team.fix_round >= self.max_fix_rounds:
            self._warning(
                f"task {team.task_id} merges after {team.fix_round} winner-fix rounds even "
                "with open cheap-review notes"
            )
            self._deliver(team)
            return
        team.fix_round += 1
        team.selection["last_winner_job"] = ""
        self.persist()

    # Delivery ----------------------------------------------------------------

    def _deliver(self, team: SwarmTeam) -> None:
        task = self._task_of(team)
        mode = team.winner
        path = Path(team.worktrees[mode])
        try:
            head = self._commit_worktree(path, task.id, mode)
        except SwarmGitError as exc:
            self._drop_team(team, f"could not commit the winner of {task.id}: {exc}")
            return
        for candidate_mode in team.modes:
            label = "winner" if candidate_mode == mode else "loser"
            try:
                self.store.write_text(
                    f"swarm/tasks/{task.id}/{label}-{candidate_mode}.patch",
                    self._patch_over_base(Path(team.worktrees[candidate_mode]), team.base_sha),
                )
            except SwarmGitError:
                pass
        if not head:
            self._complete(team, task, commit="", note="the winner produced no changes", merged="")
            return
        if self._git("status", "--porcelain"):
            self._conflict(team, task, "the target checkout drifted; refusing an unsafe merge")
            return
        try:
            self._git(
                "merge", "--no-ff", "-m", f"Forge swarm {task.id}: {task.title}", head
            )
        except SwarmGitError:
            self._git("merge", "--abort", check=False)
            self._conflict(team, task, "merge conflict")
            return
        commitment = self._git("rev-parse", "HEAD")
        self._complete(team, task, commit=commitment, note=f"merged the {mode} version", merged=mode)

    def _conflict(self, team: SwarmTeam, task: SwarmTask, reason: str) -> None:
        self._capture_all_patches(team, counter=team.fix_round or 1)
        self._cleanup_worktrees(team, reason=f"conflict: {reason}")
        family = task.id.split("-R")[0]
        with self._lock:
            self.state.teams = [t for t in self.state.teams if t.id != team.id]
            task.status = "conflict"
            family_count = self._conflict_counts.get(family, 0) + 1
            self._conflict_counts[family] = family_count
            replacement_id = ""
            if family_count <= 3:
                index = 1
                ids = {item.id for item in self.state.tasks}
                replacement_id = f"{task.id}-R{index}" if family == task.id else None
                if replacement_id is None:
                    prefix, suffix = task.id.rsplit("-R", 1)
                    replacement_id = f"{prefix}-R{int(suffix) + 1}"
                    while replacement_id in ids:
                        suffix = int(suffix) + 1
                        replacement_id = f"{prefix}-R{suffix}"
                ids.add(replacement_id)
        self.store_event(
            "swarm.conflict",
            (
                f"task {task.id} re-entered the backlog as {replacement_id}: {reason}"
                if replacement_id
                else f"task {task.id} conflicted out without a replacement: {reason}"
            ),
            task=task.id,
        )
        if not replacement_id:
            self._warning(
                f"task family {family} conflicted too often; the task stays closed as conflict"
            )
        else:
            self._append_replacement(task, replacement_id, reason)
        self.persist()

    def _append_replacement(self, task: SwarmTask, replacement_id: str, reason: str) -> None:
        with self._lock:
            self.state.tasks.append(
                SwarmTask(
                    id=replacement_id,
                    title=task.title,
                    area=task.area,
                    description=(
                        task.description
                        + f"\n\nReentered as task {replacement_id} after a merge conflict "
                        f"({reason}); the original patch is preserved in the run artifacts."
                    ),
                    acceptance_criteria=task.acceptance_criteria,
                    validation_commands=task.validation_commands,
                    priority=task.priority,
                    origin="replacement",
                )
            )

    def _complete(
        self, team: SwarmTeam, task: SwarmTask, *, commit: str, note: str, merged: str
    ) -> None:
        self._cleanup_worktrees(team, reason=f"task {task.id} done: {note}", merged=merged)
        with self._lock:
            self.state.teams = [t for t in self.state.teams if t.id != team.id]
            task.status = "done"
            task.commit = commit
            task.summary = note
        self.store_event("swarm.done", f"task {task.id} done: {note}", task=task.id)
        self.persist()

    # Main run loop ------------------------------------------------------------

    def run(self) -> SwarmRunState:
        lock = RepositoryExecutionLock(self.repo, self.config.branch, self.run_id)
        lock.acquire()
        try:
            self._executor = ThreadPoolExecutor(max_workers=SWARM_AGENTS_CAP)
            self._run_locked()
        except (SwarmCancelled, KeyboardInterrupt):
            # Stop in-flight agents so the executor shutdown cannot hang.
            self.cancel()
            with self._lock:
                self.state.status = "cancelled"
            self.persist("cancelled")
        except (SwarmFailed, ContractError, AgentFailure) as exc:
            # AgentFailure reaches here from the planner, e.g. a central policy
            # refusal (exit 78) that no retry can fix.
            with self._lock:
                self.state.status = "failed"
            self.persist(str(exc))
        except Exception as exc:
            # Never leave a dead run marked "running".
            with self._lock:
                self.state.status = "failed"
            self.persist(f"swarm controller crashed: {type(exc).__name__}: {exc}")
            raise
        finally:
            executor, self._executor = self._executor, None
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
            lock.release()
        return self.state

    def _run_locked(self) -> None:
        with self._lock:
            self.state.status = "running"
        self._restaff_off_policy()
        self._release_missing_worktrees()
        self.persist(f"swarm controller running (pid {os.getpid()})")
        planned = False
        while True:
            self._checkpoint()
            if not planned:
                with self._lock:
                    fresh_state = not self.state.tasks
                if fresh_state:
                    self._plan_backlog()
                planned = True
            with self._lock:
                pending = [t for t in self.state.tasks if t.status == "pending"]
                busy = [t for t in self.state.tasks if t.status == "in_progress"]
            if self._maybe_replan():
                continue
            if not pending and not busy:
                with self._lock:
                    self.state.status = "completed"
                self.persist("the swarm finished its backlog")
                return
            self._claim_task()
            teams = self._active_teams()
            jobs: list[dict[str, Any]] = []
            for team in teams:
                try:
                    self._prepare_worktrees(team)
                except SwarmGitError as exc:
                    self._drop_team(team, f"worktree preparation failed: {exc}")
                    continue
                jobs.extend(self._round_jobs(team))
            if jobs:
                results = self._run_agents(jobs)
                self._apply_results(results)
                dispatched = {job["team_id"] for job, _ in results}
                for team in self._active_teams():
                    if team.id in dispatched:
                        self._advance(team)
            working = bool(jobs) or bool(self._active_teams())
            if not working and not pending:
                with self._lock:
                    in_progress = [t for t in self.state.tasks if t.status == "in_progress"]
                if not in_progress and not [t for t in self.state.tasks if t.status == "pending"]:
                    self.state.status = "completed"
                    self.persist("the swarm finished its backlog")
                    return
            time.sleep(0.01)

    def _active_teams(self) -> list[SwarmTeam]:
        with self._lock:
            return [team for team in self.state.teams if team.phase not in ("closed",)]

    def summary(self) -> dict[str, Any]:
        with self._lock:
            counts = {
                status: sum(task.status == status for task in self.state.tasks)
                for status in ("pending", "in_progress", "done", "conflict", "dropped")
            }
            return {
                "status": self.state.status,
                "message": self.state.message,
                "tasks": counts,
                "planner_visits": self.state.planner_visits,
                "warnings": list(self.state.warnings),
            }
