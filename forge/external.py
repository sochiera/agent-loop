"""Read-only view of Forge runs started outside the control room.

``forge swarm-run``, ``forge swarm-resume`` and the legacy CLI commands own
their run in a separate process. The control room only observes them: it
finds them through the run index the CLI writes, the worktrees a run keeps
under the shared state home and the repositories it already knows, then
reads their durable files. It never takes the repository lock, never writes
into a run and never starts a controller for one.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from .artifacts import atomic_write, utc_now
from .locking import RepositoryLockError, git_common_directory


INDEX_DIR = "runs-index"
# Extra repositories (os.pathsep-separated) whose .forge/runs the UI watches.
WATCH_REPOS_ENV = "FORGE_UI_WATCH_REPOS"
# A finished or orphaned run stays listed this long after its last write.
RECENT_SECONDS = 48 * 3600
# A live controller whose heartbeat is older than this is flagged as stale.
STALE_HEARTBEAT_SECONDS = 300
SCAN_TTL_SECONDS = 2.0
MAX_WORKTREE_RUNS = 200
MAX_LISTED_TASKS = 300
CONTROL_NOTE = (
    "Started outside the control room (CLI). The panel only observes this run; "
    "pause, resume, cancel and recover stay with the owning CLI process "
    "(e.g. forge swarm-resume, or SIGTERM to cancel)."
)
TERMINAL_STATUSES = frozenset({"completed", "complete", "failed", "cancelled"})
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ExternalRunReadOnly(ValueError):
    """A control action was sent to a run this control room does not own."""


def default_state_home() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "forge"


def register_external_run(
    repo: Path | str, run_id: str, kind: str, *, state_home: Path | None = None
) -> None:
    """Record a CLI-owned run so a control room can find it. Best effort:
    discovery never decides whether a run may execute."""

    if not RUN_ID.match(run_id):
        return
    root = Path(state_home or default_state_home()) / INDEX_DIR
    entry = {
        "repo": str(Path(repo).expanduser().resolve()),
        "run_id": run_id,
        "kind": kind,
        "pid": os.getpid(),
        "registered_at": utc_now(),
    }
    try:
        root.mkdir(parents=True, exist_ok=True)
        atomic_write(root / f"{run_id}.json", json.dumps(entry, indent=2) + "\n")
    except OSError:
        pass


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _iso(timestamp: float | None) -> str:
    if timestamp is None:
        return ""
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="seconds")


def _parse_iso(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _age(timestamp: float | None, now: float) -> int | None:
    return None if timestamp is None else max(0, int(now - timestamp))


def _created_at(run_id: str, fallback: float | None) -> str:
    try:
        stamp = datetime.strptime(run_id[:15], "%Y%m%d-%H%M%S")
    except ValueError:
        return _iso(fallback)
    # Run ids use the local wall clock of the process that created them.
    return stamp.astimezone(UTC).isoformat(timespec="seconds")


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _cmdline(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8", errors="replace"
        )
    except OSError:
        return ""


def _process_start_ticks(pid: int) -> str:
    """Read /proc start ticks without depending on newer swarm-controller code."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        return fields[19]
    except (OSError, IndexError):
        return ""


def swarm_controller_liveness(heartbeat: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Whether the controller recorded in ``swarm/heartbeat.json`` still runs.

    A live pid alone may be a later process reusing it: the controller is the
    process whose start time the heartbeat recorded.
    """

    pid = _safe_int(heartbeat.get("pid"))
    alive = _pid_alive(pid)
    recorded = str(heartbeat.get("pid_start_ticks") or "")
    verified = bool(alive and recorded and _process_start_ticks(pid) == recorded)
    if recorded and not verified:
        alive = False
    elif alive and not recorded:
        # Heartbeats written before start ticks existed: trust the command line.
        alive = run_id in _cmdline(pid)
    beat_run = str(heartbeat.get("run_id") or run_id)
    if beat_run != run_id:
        alive = verified = False
    # The controller writes a terminal status as its last heartbeat; its
    # process may live on (a test, or a host running several controllers).
    if heartbeat.get("status") in TERMINAL_STATUSES:
        alive = False
    return {"pid": pid, "alive": alive, "identity_verified": verified}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class ExternalRunWatcher:
    """Discover and describe CLI-owned runs from their durable files."""

    def __init__(
        self,
        state_home: Path | None = None,
        *,
        watch_repos: Iterable[Path | str] = (),
        known_repos: Callable[[], Iterable[str]] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.state_home = Path(state_home) if state_home is not None else default_state_home()
        self.watch_repos = [Path(item).expanduser().resolve() for item in watch_repos]
        self.known_repos = known_repos or (lambda: ())
        self.clock = clock
        self._lock = threading.Lock()
        self._cache: tuple[float, frozenset[str], list[dict[str, Any]]] | None = None
        # Runs listed once stay listed, so an exit or crash shows up instead
        # of the run silently disappearing.
        self._seen: dict[str, Path] = {}
        self._common_dirs: dict[Path, Path | None] = {}

    # ------------------------------------------------------------------
    # Discovery

    def _index_candidates(self) -> Iterable[tuple[Path, str]]:
        root = self.state_home / INDEX_DIR
        try:
            entries = sorted(root.glob("*.json"))
        except OSError:
            return
        for path in entries:
            value = _read_json(path)
            if value and value.get("repo") and RUN_ID.match(str(value.get("run_id") or "")):
                yield Path(str(value["repo"])), str(value["run_id"])

    def _worktree_candidates(self) -> Iterable[tuple[Path, str]]:
        """Runs started by a CLI that predates the run index still keep their
        worktrees here; each worktree's ``.git`` file names the repository."""

        root = self.state_home / "worktrees"
        try:
            runs = sorted(
                (path for path in root.iterdir() if path.is_dir() and RUN_ID.match(path.name)),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )[:MAX_WORKTREE_RUNS]
        except OSError:
            return
        for run_dir in runs:
            repo = self._repo_of_worktrees(run_dir)
            if repo is not None:
                yield repo, run_dir.name

    @staticmethod
    def _repo_of_worktrees(run_dir: Path) -> Path | None:
        for parent in (run_dir / "swarm", run_dir):
            try:
                children = [child for child in parent.iterdir() if child.is_dir()]
            except OSError:
                continue
            for child in children:
                try:
                    text = (child / ".git").read_text(encoding="utf-8").strip()
                except OSError:
                    continue
                if not text.startswith("gitdir:"):
                    continue
                gitdir = Path(text.split(":", 1)[1].strip())
                common = gitdir.parent.parent
                if gitdir.parent.name == "worktrees" and common.name == ".git":
                    return common.parent
        return None

    def _repo_candidates(self) -> Iterable[tuple[Path, str]]:
        repos = {*self.watch_repos}
        for item in self.known_repos():
            if item:
                repos.add(Path(item).expanduser().resolve())
        for repo in sorted(repos):
            try:
                runs = [path.name for path in (repo / ".forge" / "runs").iterdir() if path.is_dir()]
            except OSError:
                continue
            for run_id in runs:
                if RUN_ID.match(run_id):
                    yield repo, run_id

    def _candidates(self) -> dict[str, Path]:
        found: dict[str, Path] = dict(self._seen)
        for source in (self._index_candidates, self._worktree_candidates, self._repo_candidates):
            for repo, run_id in source():
                found.setdefault(run_id, repo.expanduser().resolve())
        return found

    # ------------------------------------------------------------------
    # Listing

    def list(self, exclude: Iterable[str] = ()) -> list[dict[str, Any]]:
        excluded = frozenset(exclude)
        now = self.clock()
        with self._lock:
            cached = self._cache
            if cached is not None and now - cached[0] < SCAN_TTL_SECONDS and cached[1] == excluded:
                return [dict(item) for item in cached[2]]
            runs: list[dict[str, Any]] = []
            for run_id, repo in self._candidates().items():
                if run_id in excluded:
                    continue
                value = self._describe(repo, run_id, now, detailed=False)
                if value is None:
                    continue
                recent = (
                    value["freshness"]["updated_age_seconds"] is not None
                    and value["freshness"]["updated_age_seconds"] < RECENT_SECONDS
                )
                if value["alive"] or recent or run_id in self._seen:
                    self._seen[run_id] = repo
                    runs.append(value)
            self._cache = (now, excluded, runs)
            return [dict(item) for item in runs]

    def get(self, run_id: str, exclude: Iterable[str] = ()) -> dict[str, Any]:
        with self._lock:
            repo = self._seen.get(run_id)
        if repo is None:
            self.list(exclude)
            with self._lock:
                repo = self._seen.get(run_id)
        if repo is None:
            raise KeyError(run_id)
        value = self._describe(repo, run_id, self.clock(), detailed=True)
        if value is None:
            raise KeyError(run_id)
        return value

    def owns(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._seen

    def control_note(self, run_id: str) -> str:
        return CONTROL_NOTE

    def active_count(self) -> int:
        return sum(1 for item in self.list() if item["alive"])

    # ------------------------------------------------------------------
    # Description

    def _describe(
        self, repo: Path, run_id: str, now: float, *, detailed: bool
    ) -> dict[str, Any] | None:
        root = repo / ".forge" / "runs" / run_id
        if (root / "swarm" / "state.json").is_file():
            value = self._describe_swarm(repo, root, run_id, now, detailed)
        elif (root / "state.json").is_file():
            value = self._describe_legacy(repo, root, run_id, now)
        else:
            return None
        if value is None:
            return None
        value.update(
            {
                "run_id": run_id,
                "repo": str(repo),
                "project": repo.name,
                "artifact_dir": str(root),
                "external": True,
                "source": "cli",
                "controllable": False,
                "control_note": CONTROL_NOTE,
                "recoverable": False,
                "recovery": {},
            }
        )
        value["orphaned"] = value["status"] in {"running", "paused"} and not value["alive"]
        value["liveness"] = (
            "active" if value["alive"] else "orphaned" if value["orphaned"] else "ended"
        )
        value["display_status"] = "orphaned" if value["orphaned"] else value["status"]
        if detailed:
            from .web import read_last_lines

            for name in ("events.jsonl", "usage.jsonl"):
                value[name.removesuffix(".jsonl")] = read_last_lines(root / name)
        return value

    def _describe_swarm(
        self, repo: Path, root: Path, run_id: str, now: float, detailed: bool
    ) -> dict[str, Any] | None:
        state_path = root / "swarm" / "state.json"
        state = _read_json(state_path)
        if state is None:
            return None
        heartbeat = _read_json(root / "swarm" / "heartbeat.json") or {}
        controller = swarm_controller_liveness(heartbeat, run_id) if heartbeat else {
            "pid": 0, "alive": False, "identity_verified": False
        }
        if not heartbeat:
            controller["alive"] = self._lock_holder_alive(repo, run_id)
        state_mtime = _mtime(state_path)
        beat_at = _parse_iso(heartbeat.get("updated_at"))
        updated = max(item for item in (state_mtime, beat_at, 0.0) if item is not None) or None
        heartbeat_age = _age(beat_at, now)
        tasks = [item for item in state.get("tasks", []) if isinstance(item, dict)]
        counts: dict[str, int] = {}
        for task in tasks:
            status = str(task.get("status") or "pending")
            counts[status] = counts.get(status, 0) + 1
        teams = [
            {
                "team": team.get("id"),
                "task": team.get("task_id"),
                "phase": team.get("phase"),
                "review_round": _safe_int(team.get("review_round")),
                "fix_round": _safe_int(team.get("fix_round")),
                "modes": list(team.get("modes") or []),
                "coders": [str(coder.get("display") or "") for coder in team.get("coders") or [] if isinstance(coder, dict)],
            }
            for team in state.get("teams", [])
            if isinstance(team, dict)
        ]
        titles = {str(task.get("id")): str(task.get("title") or "") for task in tasks}
        for team in teams:
            team["title"] = titles.get(str(team["task"]), "")
        alive = bool(controller["alive"])
        inflight = heartbeat.get("inflight") if alive else []
        active_agents = {
            str(entry.get("relative") or index): {
                "role": entry.get("role"),
                "model": entry.get("model"),
                "started_at": entry.get("started_at"),
                "attempt": entry.get("attempt"),
                "team": entry.get("team"),
                "elapsed_seconds": _age(_parse_iso(entry.get("started_at")), now),
            }
            for index, entry in enumerate(inflight or [])
            if isinstance(entry, dict)
        }
        status = str(state.get("status") or "running")
        value: dict[str, Any] = {
            "kind": "swarm",
            "status": status,
            "phase": f"swarm · {len(teams)} team(s)",
            "message": str(state.get("message") or ""),
            "alive": alive,
            "created_at": _created_at(run_id, _mtime(root / "config.json")),
            "updated_at": _iso(updated),
            "tasks": counts,
            "tasks_total": len(tasks),
            "tasks_done": counts.get("done", 0),
            "teams": teams,
            "active_agents": active_agents,
            "planner_visits": _safe_int(state.get("planner_visits")),
            "warnings": [str(item) for item in state.get("warnings", [])][-20:],
            "controller": {
                **controller,
                "controller_id": str(heartbeat.get("controller_id") or ""),
                "heartbeat_status": heartbeat.get("status"),
            },
            "freshness": {
                "observed_at": _iso(now),
                "state_updated_at": _iso(state_mtime),
                "state_age_seconds": _age(state_mtime, now),
                "heartbeat_updated_at": _iso(beat_at),
                "heartbeat_age_seconds": heartbeat_age,
                "heartbeat_stale": bool(
                    alive and (heartbeat_age is None or heartbeat_age > STALE_HEARTBEAT_SECONDS)
                ),
                "updated_age_seconds": _age(updated, now),
            },
        }
        if detailed:
            value["task_list"] = [
                {
                    "id": task.get("id"),
                    "title": task.get("title"),
                    "status": task.get("status"),
                    "priority": task.get("priority"),
                    "attempts": task.get("attempts"),
                }
                for task in tasks[:MAX_LISTED_TASKS]
            ]
        return value

    def _describe_legacy(
        self, repo: Path, root: Path, run_id: str, now: float
    ) -> dict[str, Any] | None:
        state_path = root / "state.json"
        state = _read_json(state_path)
        if state is None:
            return None
        mtime = _mtime(state_path)
        alive = self._lock_holder_alive(repo, run_id)
        value = {
            key: state.get(key)
            for key in (
                "status", "phase", "message", "cycle", "sprint_number",
                "sprint_iteration", "created_at", "iterations", "config",
            )
            if key in state
        }
        value["warnings"] = [str(item) for item in state.get("warnings", [])][-20:]
        value.update(
            {
                "kind": "forge",
                "status": str(state.get("status") or "running"),
                "alive": alive,
                "updated_at": str(state.get("updated_at") or _iso(mtime)),
                "active_agents": {},
                "freshness": {
                    "observed_at": _iso(now),
                    "state_updated_at": _iso(mtime),
                    "state_age_seconds": _age(mtime, now),
                    "updated_age_seconds": _age(mtime, now),
                },
            }
        )
        value.setdefault("created_at", _created_at(run_id, mtime))
        return value

    def _lock_holder_alive(self, repo: Path, run_id: str) -> bool:
        """Read the repository lock's owner record without taking the lock:
        an observer must never race a controller for it."""

        common = self._common_dirs.get(repo, ...)
        if common is ...:
            try:
                common = git_common_directory(repo)
            except RepositoryLockError:
                common = None
            self._common_dirs[repo] = common
        if common is None:
            return False
        path = common / "forge-locks" / "execution.lock"
        owner = _read_json(path)
        if not owner or str(owner.get("run_id") or "") != run_id:
            return False
        # The owner record survives a release, so it only counts while the
        # kernel still reports a flock on the file.
        return _flock_held(path)


def _flock_held(path: Path) -> bool:
    """True when ``/proc/locks`` lists a FLOCK on ``path``; reading it never
    contends with the lock holder."""

    try:
        stat = path.stat()
        lines = Path("/proc/locks").read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    wanted = (os.major(stat.st_dev), os.minor(stat.st_dev), stat.st_ino)
    for line in lines:
        fields = line.split()
        if "FLOCK" not in fields or "->" in fields:
            continue
        for field in fields:
            parts = field.split(":")
            if len(parts) != 3:
                continue
            try:
                identity = (int(parts[0], 16), int(parts[1], 16), int(parts[2]))
            except ValueError:
                continue
            if identity == wanted:
                return True
    return False
