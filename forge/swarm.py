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

import hashlib
import json
import os
import random
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .agents import (
    AgentCancelled,
    AgentConfigurationFailure,
    AgentFailure,
    AgentPolicyRefused,
    AgentRequest,
    AgentResult,
    AgentRunner,
    AgentTimeout,
    AgentUsageLimit,
)
from .artifacts import ArtifactStore, utc_now
from .catalog import model_identity
from .contracts import (
    ContractError,
    parse_swarm_backlog,
    parse_swarm_replan,
    parse_swarm_review,
    parse_swarm_selection,
)
from .external import register_external_run
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
# A job whose agent timed out twice ends there: a third full timeout rarely
# succeeds and only holds the team (run 20261003-000921: 3 x 3600s).
MAX_TIMEOUTS_PER_JOB = 2
# Consecutive cheap pairs lost to agent failures, with no agent success in
# between, before the swarm stops instead of burning the backlog's attempts.
DEFAULT_FAILURE_BREAKER = 3
# Cheap reviewer answers Forge cannot read get one re-ask before a fallback.
MAX_UNREADABLE_REVIEWS = 2
HEARTBEAT_SECONDS = 30.0
SWARM_SCHEMA = 1

# Strong staff roles of the swarm, in migration preference order (the first
# allowed model replaces an off-policy one).
SWARM_STAFF = {"planner": (SOL, OPUS, GLM), "reviewer": (SOL, OPUS)}


class SwarmCancelled(RuntimeError):
    pass


class SwarmFailed(RuntimeError):
    pass


class SwarmGitError(RuntimeError):
    pass


def process_start_ticks(pid: int) -> str:
    """The kernel start time of ``pid`` (``/proc/<pid>/stat`` field 22), so
    an observer can tell the controller from a later process reusing its pid;
    empty when unknown."""

    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return ""
    fields = stat.rsplit(")", 1)[-1].split()
    return fields[19] if len(fields) > 19 else ""


def _file_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _digest(path: Path) -> str:
    data = _file_bytes(path)
    return "" if data is None else hashlib.sha256(data).hexdigest()


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


def off_policy_staff(config: RunConfig, snapshot: Any) -> list[str]:
    """Roster roles the current policy (or the swarm's strong-staff rule)
    refuses, plus ``cheap_pool`` when any cheap slot is refused."""

    roles = []
    for role, spec in config.models.items():
        strong = SWARM_STAFF.get(role)
        if not snapshot.allows(spec, role) or (
            strong and model_identity(spec) not in {model_identity(m) for m in strong}
        ):
            roles.append(role)
    if any(not snapshot.allows(spec, SWARM_CODER_ROLE) for spec in config.cheap_pool):
        roles.append("cheap_pool")
    return roles


def migrate_swarm_config(config: RunConfig, run_id: str) -> dict[str, str]:
    """Explicitly move a persisted swarm config onto the current policy.

    Only refused entries change: a strong role takes the first allowed model
    of its swarm preference (Sol, then Opus), any other role a deterministic
    policy draw, and a refused cheap pool the current central pool. Returns
    ``{role: "old -> new"}``; raises when the policy leaves no replacement.
    """

    snapshot = load_policy(config.policy_path or None)
    changes: dict[str, str] = {}
    for role in off_policy_staff(config, snapshot):
        if role == "cheap_pool":
            pool = list(snapshot.cheap_pool())
            if len(pool) < 4:
                raise SwarmFailed(
                    f"the current model policy allows {len(pool)} cheap pool slots; "
                    "the swarm needs four"
                )
            changes[role] = (
                f"{', '.join(spec.display() for spec in config.cheap_pool)} -> "
                f"{', '.join(spec.display() for spec in pool)}"
            )
            config.cheap_pool = pool
            continue
        previous = config.models[role]
        preferred = SWARM_STAFF.get(role) or (
            (SOL, OPUS) if role == "brain" else ()
        )
        options = [spec for spec in preferred if snapshot.allows(spec, role)]
        if options:
            replacement = options[0]
        elif role in SWARM_STAFF:
            raise SwarmFailed(f"the current model policy allows no swarm {role}")
        else:
            replacement = snapshot.select_role(role, random.Random(f"{run_id}:migrate:{role}"))
        config.models[role] = replacement
        changes[role] = f"{previous.display()} -> {replacement.display()}"
    return changes


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
    # Artifact names of this phase's jobs whose results are applied. A resume
    # dispatches only the rest; ``None`` marks state saved before tracking.
    done_jobs: list[str] | None = None

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
            **({} if self.done_jobs is None else {"done_jobs": list(self.done_jobs)}),
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
            done_jobs=(
                None
                if value.get("done_jobs") is None
                else [str(item) for item in value["done_jobs"]]
            ),
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
        failure_breaker: int = DEFAULT_FAILURE_BREAKER,
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
        self.failure_breaker = failure_breaker
        self._conflict_counts: dict[str, int] = {}
        # Tasks whose pairs failed since the last agent success (the breaker).
        self._failure_streak: list[str] = []
        self._inflight: dict[str, dict[str, Any]] = {}
        self._heartbeat_at = 0.0
        self._heartbeat_write = threading.Lock()
        # Set when an agent starts or ends: the heartbeat thread writes now.
        self._heartbeat_wake = threading.Event()
        self._lock = threading.RLock()
        self._control = threading.Condition()
        self._executor: ThreadPoolExecutor | None = None
        self._paused = False
        self._cancel_requested = False
        self._controller_id = uuid.uuid4().hex
        self._pid_start_ticks = process_start_ticks(os.getpid())
        # The durable state this controller loaded or last wrote; run()
        # refuses to take over when another controller changed it since.
        self._state_digest = ""
        # The config bytes a resume loaded; run() refuses to take over when
        # another controller changed them since.
        self._config_original: bytes | None = None
        # A resume migration, written only once run() owns the repository.
        self._migration: dict[str, Any] | None = None

        self.planner = self._staff_model("planner", allowed=set(SWARM_STAFF["planner"]))
        self.strong_reviewer = self._staff_model("reviewer", allowed=set(SWARM_STAFF["reviewer"]))
        if len(config.cheap_pool) < 4:
            raise ValueError(
                "the swarm consumes the cheap pool; RunConfig.cheap_pool must "
                "hold at least four slots (two coder slots plus two reviewer slots)"
            )
        if resume:
            self.state = SwarmRunState.from_dict(self._load_swarm_state())
            # The conflict cap per task family survives a restart.
            for task in self.state.tasks:
                if task.status == "conflict":
                    family = task.id.split("-R")[0]
                    self._conflict_counts[family] = self._conflict_counts.get(family, 0) + 1
        else:
            self._install_local_excludes()
            self.state = SwarmRunState()

    @classmethod
    def resume_existing(
        cls,
        repo: Path | str,
        run_id: str,
        *,
        migrate_models: bool = False,
        policy_path: str = "",
        **options: Any,
    ) -> "SwarmController":
        """Rebuild a persisted swarm for ``run()``.

        An off-policy roster (e.g. a retired planner) is refused unless
        ``migrate_models`` is set; the migration rewrites only the refused
        entries, backs up the old config and is recorded in the run. Tasks,
        teams, worktrees and patches are untouched. Nothing is written here:
        ``run()`` applies the migration once it owns the repository.
        """

        repo = Path(repo).expanduser().resolve()
        root = repo / ".forge" / "runs" / run_id
        config_path = root / "config.json"
        if not (root / "swarm" / "state.json").is_file() or not config_path.is_file():
            raise SwarmFailed(f"no swarm run {run_id} under {repo}")
        original = config_path.read_bytes()
        raw = json.loads(original.decode("utf-8"))
        config = RunConfig.from_dict(raw)
        config.repo = str(repo)
        if policy_path:
            config.policy_path = policy_path
        changes: dict[str, str] = {}
        if migrate_models:
            changes = migrate_swarm_config(config, run_id)
        else:
            refused = off_policy_staff(config, load_policy(config.policy_path or None))
            if refused:
                raise SwarmFailed(
                    f"run {run_id} names models the current policy refuses for "
                    f"{', '.join(refused)}; resume with --migrate-models to move "
                    "them onto the current policy"
                )
        controller = cls(config, run_id=run_id, resume=True, **options)
        controller._config_original = original
        if changes or config.policy_path != str(raw.get("policy_path") or ""):
            controller._migration = {"changes": changes}
        return controller

    def _take_ownership(self) -> None:
        """Under the repository lock: refuse a state another controller changed
        since this one loaded it, then write the pending resume migration."""

        state_path = self.store.root / "swarm" / "state.json"
        if self._state_digest and _digest(state_path) != self._state_digest:
            raise SwarmFailed(
                f"swarm state of run {self.run_id} changed after this controller loaded "
                "it (another controller ran meanwhile); start swarm-resume again"
            )
        original = self._config_original
        config_path = self.store.root / "config.json"
        # Every resume, migrating or not: the run executes the config it loaded.
        if original is not None and _file_bytes(config_path) != original:
            raise SwarmFailed(
                f"config of run {self.run_id} changed after this controller loaded it; "
                "start swarm-resume again"
            )
        migration, self._migration = self._migration, None
        if migration is None or original is None:
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        (self.store.root / f"config.pre-migration-{stamp}.json").write_bytes(original)
        self.store.write_data("config.json", self.config.to_dict())
        changes = migration["changes"]
        if changes:
            self.store.append_jsonl(
                "swarm/migrations.jsonl", {"at": utc_now(), "changes": changes}
            )
            for role, change in changes.items():
                self._warning(f"model migration on resume: {role} {change}")
            self.persist("migrated the roster to the current model policy")

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
        # One read: the digest describes exactly the bytes parsed, so a write
        # by another owner after this read is still detected under the lock.
        data = path.read_bytes()
        self._state_digest = hashlib.sha256(data).hexdigest()
        return json.loads(data.decode("utf-8"))

    def persist(self, message: str = "") -> None:
        with self._lock:
            state = self.state
            state.message = message or state.message
            path = self.store.write_data("swarm/state.json", state.to_dict())
            self._state_digest = _digest(path)
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
        if self.on_event is not None:
            self.on_event({"kind": f"swarm.{kind}", "message": message, **fields})

    def heartbeat(self, *, force: bool = False) -> None:
        """Write ``swarm/heartbeat.json``: the live pid and every running agent
        with its start time, so an observer can tell a long agent from a
        dead controller without reading worker logs."""

        # Writers serialize on their own lock, so a slow disk never holds the
        # state lock and the newest snapshot is always the last one written.
        with self._heartbeat_write:
            now = time.monotonic()
            with self._lock:
                if not force and now - self._heartbeat_at < HEARTBEAT_SECONDS:
                    return
                self._heartbeat_at = now
                payload = self._heartbeat_payload()
            self.store.write_data("swarm/heartbeat.json", payload)

    def _heartbeat_payload(self) -> dict[str, Any]:
        with self._lock:
            return {
                "pid": os.getpid(),
                # With the pid start time, tells this controller from a later
                # process that reuses its pid.
                "pid_start_ticks": self._pid_start_ticks,
                "controller_id": self._controller_id,
                "run_id": self.run_id,
                "updated_at": utc_now(),
                "status": self.state.status,
                "agent_timeout_seconds": self.config.agent_timeout_seconds,
                "inflight": [dict(entry) for entry in self._inflight.values()],
                "teams": [
                    {"team": team.id, "task": team.task_id, "phase": team.phase,
                     "review_round": team.review_round, "fix_round": team.fix_round}
                    for team in self.state.teams
                ],
            }

    def _heartbeat_loop(self, stop: threading.Event) -> None:
        """Tick while the controller blocks in a planner call, validation or
        a pause, where the dispatch loop writes no heartbeat."""

        while True:
            self._heartbeat_wake.wait(HEARTBEAT_SECONDS)
            if stop.is_set():
                return
            self._heartbeat_wake.clear()
            try:
                self.heartbeat(force=True)
            except OSError:
                continue

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
        timeouts = 0
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            with self._lock:
                self._inflight[relative] = {
                    "relative": relative,
                    "role": role,
                    "model": spec.display(),
                    "team": job.get("team_id", 0),
                    "attempt": attempt,
                    "started_at": utc_now(),
                }
            self._job_heartbeat(job)
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
                if isinstance(exc, AgentTimeout):
                    timeouts += 1
                if attempt < attempts and timeouts < MAX_TIMEOUTS_PER_JOB:
                    # A retry can hold a team for another full agent timeout;
                    # make it visible instead of silent.
                    self.store_event(
                        "agent-retry",
                        f"{role} attempt {attempt}/{attempts} failed on {relative}: {exc}",
                        team=job.get("team_id", 0),
                        mode=job.get("mode", ""),
                    )
                    prompt = (
                        f"Forge retried this swarm role because the provider process "
                        f"failed: {exc}. Continue from the durable worktree and answer "
                        "with the same requested contract.\n\n" + job["prompt"]
                    )
                    continue
                break
            finally:
                with self._lock:
                    self._inflight.pop(relative, None)
                self._job_heartbeat(job)
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

    def _job_heartbeat(self, job: dict[str, Any]) -> None:
        """Make an agent's start and end visible at once, inside the throttle
        window too. A planner blocks the controller thread, so it writes
        itself; team agents only wake the heartbeat thread, so their start
        never waits for a disk write."""
        if job.get("team_id", 0) == 0:
            self.heartbeat(force=True)
        else:
            self._heartbeat_wake.set()

    def _dispatch(
        self, jobs: list[dict[str, Any]]
    ) -> list[tuple[dict[str, Any], Future[AgentResult]]]:
        executor = self._executor
        assert executor is not None
        return [(job, executor.submit(self._invoke, job)) for job in jobs]

    @staticmethod
    def _outcomes(
        futures: list[tuple[dict[str, Any], Future[AgentResult]]]
    ) -> list[tuple[dict[str, Any], AgentResult | Exception]]:
        results: list[tuple[dict[str, Any], AgentResult | Exception]] = []
        for job, future in futures:
            try:
                results.append((job, future.result()))
            except Exception as exc:
                results.append((job, exc))
        return results

    @staticmethod
    def _wait_for_any(
        in_flight: dict[int, list[tuple[dict[str, Any], Future[AgentResult]]]]
    ) -> None:
        # Only unfinished futures: a finished one held beside a running sibling
        # would end every wait at once and spin the controller. The short
        # timeout keeps operator pause and cancel responsive.
        futures = [
            future for jobs in in_flight.values() for _, future in jobs if not future.done()
        ]
        if futures:
            wait(futures, timeout=0.2, return_when=FIRST_COMPLETED)

    def _collect_finished(
        self, in_flight: dict[int, list[tuple[dict[str, Any], Future[AgentResult]]]]
    ) -> None:
        """Apply and persist every finished agent result as soon as it exists,
        so a controller crash never pays for it twice. A failure waits for its
        team's other agents: dropping the pair must not pull a worktree from
        under a running sibling."""
        for team_id in list(in_flight):
            jobs = in_flight[team_id]
            if all(future.done() for _, future in jobs):
                # The main loop advances the team once no job of its phase remains.
                self._apply_results(self._outcomes(in_flight.pop(team_id)))
                continue
            succeeded = [
                (job, future)
                for job, future in jobs
                if future.done() and not future.cancelled() and future.exception() is None
            ]
            if not succeeded:
                continue
            in_flight[team_id] = [item for item in jobs if item not in succeeded]
            self._apply_results(self._outcomes(succeeded))

    def _drain(
        self, in_flight: dict[int, list[tuple[dict[str, Any], Future[AgentResult]]]]
    ) -> None:
        """After a cancel, keep every finished agent result durable."""
        results = [
            outcome
            for team_id in list(in_flight)
            for outcome in self._outcomes(in_flight.pop(team_id))
        ]
        if not results:
            return
        try:
            self._apply_results(results)
        except (SwarmCancelled, SwarmFailed):
            pass

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

    def _phase_jobs(self, team: SwarmTeam) -> list[dict[str, Any]]:
        """Every job the team's current phase consists of; no side effects."""

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
            elif last_job == "coder" and team.versions[team.winner].get("committed"):
                # The fix is applied and validated; its cheap check runs next.
                jobs.append(
                    self._reviewer_job(
                        team,
                        team.winner,
                        f"swarm/tasks/{task_id}/winner-check-{team.fix_round}",
                    )
                )
            # "coder" before its validation, or "reviewer": the round is done
            # and only the controller's advance remains.
        return jobs

    def _pending_jobs(self, team: SwarmTeam) -> list[dict[str, Any]]:
        """The phase's jobs whose results are not applied yet. Empty means the
        round is complete and the team advances without another agent."""

        done = set(team.done_jobs or [])
        return [job for job in self._phase_jobs(team) if job["relative"] not in done]

    def _round_jobs(self, team: SwarmTeam) -> list[dict[str, Any]]:
        jobs = self._pending_jobs(team)
        if any(job["role"] == "reviewer" for job in jobs):
            team.selection["attempts"] = team.selection.get("attempts", 0) + 1
        return jobs

    def _backfill_done_jobs(self) -> None:
        """State saved before ``done_jobs`` existed: recover which of the
        current phase's jobs finished from the durable event log, so a resume
        does not pay for them again. Only events the state already persisted
        (before the last ``swarm.state`` event) count."""

        with self._lock:
            legacy = [team for team in self.state.teams if team.done_jobs is None]
        if not legacy:
            return
        path = self.store.root / "events.jsonl"
        events: list[dict[str, Any]] = []
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
        last_state = max(
            (index for index, event in enumerate(events) if event.get("kind") == "swarm.state"),
            default=-1,
        )
        events = events[: last_state + 1]
        for team in legacy:
            if not team.worktrees:
                # Nothing ran without worktrees; the team starts its phase.
                with self._lock:
                    team.done_jobs = []
                continue
            claim = f"team {team.id} claimed {team.task_id}"
            start = max(
                (index for index, event in enumerate(events) if event.get("message") == claim),
                default=None,
            )
            finished: set[str] = set()
            if start is not None:
                for event in events[start:]:
                    if event.get("kind") == "swarm.agent-done" and event.get("team") == team.id:
                        finished.add(str(event.get("message", "")).rsplit(" ", 1)[-1])
            done = [
                job["relative"]
                for job in self._phase_jobs(team)
                if job["relative"] in finished and self._result_recorded(team, job)
            ]
            with self._lock:
                team.done_jobs = done
            if done:
                self.store_event(
                    "resume-reuse",
                    f"team {team.id} ({team.task_id}) keeps {len(done)} finished "
                    f"{team.phase} result(s): {', '.join(done)}",
                    team=team.id,
                )

    @staticmethod
    def _result_recorded(team: SwarmTeam, job: dict[str, Any]) -> bool:
        data = team.versions.get(job.get("mode", ""), {})
        if job["role"] == SWARM_CODER_ROLE:
            return bool(data.get("summary"))
        if job["role"] == SWARM_REVIEWER_ROLE:
            return bool(data.get("review"))
        return bool(team.selection)

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

    def _unreadable(self, team: SwarmTeam, mode: str, relative: str, exc: Exception) -> bool:
        """Record an unreadable cheap review; True while it still gets a re-ask."""

        with self._lock:
            seen = team.versions[mode].setdefault("unreadable_reviews", [])
            seen.append(relative)
            count = seen.count(relative)
        if count < MAX_UNREADABLE_REVIEWS:
            self._warning(f"task {team.task_id}: unreadable review {relative} ({exc}); re-asked")
            return True
        return False

    def _apply_review(
        self, team: SwarmTeam, mode: str, result: AgentResult, relative: str = ""
    ) -> bool:
        try:
            parsed = parse_swarm_review(result.text)
        except ContractError as exc:
            if self._unreadable(team, mode, relative, exc):
                return False
            parsed = {
                "verdict": "fix",
                "summary": "Forge could not read the review contract",
                "blocking": [{"problem": "unparseable review", "detail": str(exc)}],
            }
        with self._lock:
            team.versions[mode]["review"] = parsed
        return True

    def _apply_winner_review(
        self, team: SwarmTeam, result: AgentResult, relative: str = ""
    ) -> bool:
        try:
            parsed = parse_swarm_review(result.text)
        except ContractError as exc:
            if self._unreadable(team, team.winner, relative, exc):
                return False
            # Never an approval Forge did not read: the open check runs
            # through the bounded fix loop and its explicit merge warning.
            parsed = {
                "verdict": "fix",
                "summary": f"winner-check contract unreadable: {exc}",
                "blocking": [{"problem": "unreadable winner check", "detail": str(exc)}],
            }
        with self._lock:
            team.versions[team.winner].setdefault("winner_reviews", []).append(parsed)
            team.selection["last_winner_job"] = "reviewer"
        return True

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
        halt = ""
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
            if isinstance(outcome, (AgentUsageLimit, AgentPolicyRefused)):
                # Quota or central policy: no other cheap pair fares better
                # now, so keep the team intact and stop the swarm instead of
                # charging the task an attempt.
                halt = halt or f"{job['role']} {job['spec'].display()}: {outcome}"
                self.store_event(
                    "agent-halted",
                    f"{job['role']} stopped the swarm: {outcome}",
                    team=team.id,
                    mode=job.get("mode", ""),
                )
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
            with self._lock:
                self._failure_streak.clear()
            accepted = True
            if job["role"] == SWARM_CODER_ROLE:
                self._apply_code(team, job["mode"], outcome)
                if team.phase == "winner-fix":
                    with self._lock:
                        team.selection["last_winner_job"] = "coder"
            elif job["role"] == SWARM_REVIEWER_ROLE:
                if team.phase == "winner-fix":
                    accepted = self._apply_winner_review(team, outcome, job["relative"])
                else:
                    accepted = self._apply_review(team, job["mode"], outcome, job["relative"])
            elif job["role"] == "reviewer":
                self._apply_selection(team, outcome)
            if accepted:
                with self._lock:
                    if team.done_jobs is None:
                        team.done_jobs = []
                    team.done_jobs.append(job["relative"])
        # A cancelled or halted round keeps every team and worktree for
        # recovery; a failure may only be the stop's side effect.
        if cancelled or halt:
            failed.clear()
        for team_id, reason in failed.items():
            with self._lock:
                team = next((t for t in self.state.teams if t.id == team_id), None)
            if team is not None:
                self._drop_team(team, reason)
                with self._lock:
                    self._failure_streak.append(team.task_id)
        tripped = self._trip_breaker() if not (cancelled or halt) else ""
        # Every worker result reaches durable state, even when its phase
        # does not advance this round.
        self.persist(f"applied {len(results)} swarm agent results")
        if cancelled:
            raise SwarmCancelled()
        if halt:
            raise SwarmFailed(
                f"swarm stopped on a provider quota or policy refusal ({halt}); "
                "every team and worktree is kept; resume with swarm-resume once it clears"
            )
        if tripped:
            raise SwarmFailed(tripped)

    def _trip_breaker(self) -> str:
        """After ``failure_breaker`` consecutive pair failures with no agent
        success, refund those tasks' attempts and stop: a systemic fault (a
        broken provider, a full disk) must not drop the whole backlog."""

        with self._lock:
            streak = list(self._failure_streak)
            if len(streak) < self.failure_breaker:
                return ""
            self._failure_streak.clear()
            for task in self.state.tasks:
                if task.id in streak:
                    task.attempts = max(0, task.attempts - streak.count(task.id))
                    if task.status == "dropped":
                        task.status = "pending"
        message = (
            f"circuit breaker: {len(streak)} consecutive cheap pairs failed with no agent "
            f"success ({', '.join(streak)}); their attempts are refunded and patches kept; "
            "check provider health, then resume with swarm-resume"
        )
        self._warning(message)
        return message

    # Phase transitions -----------------------------------------------------

    def _advance(self, team: SwarmTeam) -> None:
        with self._lock:
            phase = team.phase
            # Every transition starts a fresh set of jobs.
            team.done_jobs = []
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
            # Nothing durable changes before this: a refusal leaves the run
            # exactly as the other controller wrote it.
            self._take_ownership()
        except BaseException:
            lock.release()
            raise
        # Lets a control room started before or after this process show it.
        register_external_run(self.repo, self.run_id, "swarm", state_home=self.state_home)
        stop_ticker = threading.Event()
        ticker = threading.Thread(
            target=self._heartbeat_loop, args=(stop_ticker,), name="forge-swarm-heartbeat",
            daemon=True,
        )
        ticker.start()
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
            stop_ticker.set()
            self._heartbeat_wake.set()
            ticker.join()
            try:
                # Terminal: the final status and no agent left in flight.
                self.heartbeat(force=True)
            except OSError:
                pass
            lock.release()
        return self.state

    def _run_locked(self) -> None:
        with self._lock:
            self.state.status = "running"
        self._restaff_off_policy()
        self._release_missing_worktrees()
        self._backfill_done_jobs()
        with self._lock:
            # The swarm never resumes a provider session: a stale session id
            # must not survive a restart or a model migration.
            for team in self.state.teams:
                team.sessions.clear()
        self.persist(f"swarm controller running (pid {os.getpid()})")
        self.heartbeat(force=True)
        planned = False
        # Each team advances as soon as its own agents finish: one slow or
        # retried agent must not hold every other team at a shared barrier.
        in_flight: dict[int, list[tuple[dict[str, Any], Future[AgentResult]]]] = {}
        try:
            while True:
                self._collect_finished(in_flight)
                self.heartbeat()
                with self._control:
                    cancel_requested = self._cancel_requested
                    paused = self._paused
                if cancel_requested:
                    raise SwarmCancelled()
                if paused and in_flight:
                    # Paused: dispatch nothing new, but keep applying results.
                    self._wait_for_any(in_flight)
                    continue
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
                if not pending and not busy and not in_flight:
                    with self._lock:
                        self.state.status = "completed"
                    self.persist("the swarm finished its backlog")
                    return
                claimed = self._claim_task()
                ready: dict[int, list[dict[str, Any]]] = {}
                for team in self._active_teams():
                    if team.id in in_flight:
                        continue
                    try:
                        self._prepare_worktrees(team)
                    except SwarmGitError as exc:
                        self._drop_team(team, f"worktree preparation failed: {exc}")
                        # A systemic fault (full disk, broken repository)
                        # fails every pair here; stop before the backlog drops.
                        with self._lock:
                            self._failure_streak.append(team.task_id)
                        tripped = self._trip_breaker()
                        if tripped:
                            raise SwarmFailed(tripped)
                        continue
                    jobs = self._round_jobs(team)
                    if jobs:
                        ready[team.id] = jobs
                    else:
                        self._advance_idle(team)
                if claimed is not None or ready:
                    # Claims and worktrees reach durable state before agents
                    # that may run for a full timeout.
                    count = sum(len(jobs) for jobs in ready.values())
                    self.persist(f"dispatching {count} swarm agents")
                for team_id, jobs in ready.items():
                    in_flight[team_id] = self._dispatch(jobs)
                if in_flight:
                    self._wait_for_any(in_flight)
                    continue
                working = bool(self._active_teams())
                if not working and not pending:
                    with self._lock:
                        in_progress = [t for t in self.state.tasks if t.status == "in_progress"]
                    if not in_progress and not [
                        t for t in self.state.tasks if t.status == "pending"
                    ]:
                        self.state.status = "completed"
                        self.persist("the swarm finished its backlog")
                        return
                time.sleep(0.01)
        except BaseException as exc:
            if in_flight:
                # Stop in-flight agents so the executor shutdown cannot hang
                # for a full agent timeout.
                self.cancel()
                if isinstance(exc, (SwarmCancelled, SwarmFailed, KeyboardInterrupt)):
                    self._drain(in_flight)
            raise

    def _advance_idle(self, team: SwarmTeam) -> None:
        """Advance a team whose phase has no job left to run. A transition that
        changes nothing would spin forever: drop that pair instead."""

        with self._lock:
            before = json.dumps(team.to_dict(), sort_keys=True)
        self._advance(team)
        with self._lock:
            active = any(item is team for item in self.state.teams)
            after = json.dumps(team.to_dict(), sort_keys=True)
        if active and after == before:
            self._drop_team(team, f"no progress possible in phase {team.phase}")

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
                "teams": [
                    {"team": team.id, "task": team.task_id, "phase": team.phase}
                    for team in self.state.teams
                ],
            }
