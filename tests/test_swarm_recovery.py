"""Swarm resume, interruption and autonomous-recovery regressions.

Run 20261003-000921-306c86f2 could not be resumed: ``swarm-resume`` crashed on
a missing ``--policy-path`` flag, its saved planner had been retired, a
cancelled round re-ran finished agents, and one GLM job timed out three times
in a row. These tests pin each of those paths.
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from conftest import CENTRAL_STUB
from forge import cli
from forge import swarm as swarm_module
from forge.agents import (
    AgentPolicyRefused,
    AgentRequest,
    AgentRunner,
    AgentTimeout,
    AgentUsageLimit,
)
from forge.locking import ExecutionLocked, RepositoryExecutionLock
from forge.models import ModelSpec, RunConfig
from forge.policy import (
    CENTRAL_POLICY_ENV,
    CHEAP_CODER_POOL,
    LUNA,
    PROMOTION_ACTIVE,
    SOL,
    PromotionSnapshot,
    load_policy,
)
from forge.swarm import SwarmController, SwarmFailed, SwarmGitError, SwarmTask, SwarmTeam

from test_swarm import (
    CancelMidRound,
    SwarmRunner,
    backlog_json,
    git,
    make_controller,
    repo_and_brief,
    result,
    swarm_task_json,
)


# Not in any catalog: stands in for a planner the policy has since retired.
RETIRED = ModelSpec("codex", "gpt-5-sol", "medium")
LUNA_SPEC = {"provider": LUNA.provider, "model": LUNA.model, "effort": LUNA.effort}


def state_of(repo: Path, run_id: str) -> dict:
    return json.loads((repo / ".forge/runs" / run_id / "swarm/state.json").read_text())


def config_of(repo: Path, run_id: str) -> dict:
    return json.loads((repo / ".forge/runs" / run_id / "config.json").read_text())


def resumed(box: SwarmController, runner: SwarmRunner, tmp_path: Path, **kw) -> SwarmController:
    config = RunConfig.from_dict(json.loads((box.store.root / "config.json").read_text()))
    options = {"teams": 1, "min_backlog": 1}
    options.update(kw)
    return SwarmController(
        config, run_id=box.run_id, runner=runner, state_home=tmp_path / "state",
        resume=True, **options,
    )


def interrupted_run(tmp_path: Path) -> tuple[Path, CancelMidRound, SwarmController]:
    """A run SIGTERM-cancelled while one coder finished and its sibling ran."""
    repo, brief = repo_and_brief(tmp_path)
    runner = CancelMidRound(backlog_json(count=1))
    box = make_controller(repo, brief, runner, tmp_path, min_backlog=1, teams=1)
    runner.box = box
    assert box.run().status == "cancelled"
    return repo, runner, box


def run_bounded(box: SwarmController, seconds: float = 60.0):
    """A stalled loop must fail the test, not hang it."""
    outcome: dict = {}
    thread = threading.Thread(target=lambda: outcome.setdefault("state", box.run()), daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        box.cancel()
        thread.join(10)
        pytest.fail("the swarm loop stalled")
    return outcome["state"]


# ---------------------------------------------------------------------------
# Interrupted rounds resume without repeating finished agents


@pytest.mark.parametrize("legacy_state", [False, True], ids=["tracked", "pre-tracking-state"])
def test_resume_runs_only_the_unfinished_job_of_an_interrupted_round(
    tmp_path: Path, legacy_state: bool
) -> None:
    repo, first, box = interrupted_run(tmp_path)
    saved = state_of(repo, box.run_id)
    [team] = saved["teams"]
    assert len(team["done_jobs"]) == 1
    if legacy_state:
        # State written before done_jobs existed: the event log recovers it.
        del team["done_jobs"]
        (box.store.root / "swarm/state.json").write_text(json.dumps(saved))

    runner = SwarmRunner(backlog_json(count=1))
    state = run_bounded(resumed(box, runner, tmp_path))

    assert state.status == "completed", state.message
    roles = [request.role for request in runner.requests]
    assert roles[0] == "swarm_coder" and roles[1] == "swarm_reviewer", roles
    assert str(runner.requests[0].cwd) != first.first_mode, "the finished coder ran again"
    assert all(task.status == "done" for task in state.tasks)


def winner_team(box: SwarmController, repo: Path, **fields) -> SwarmTeam:
    box.state.tasks = [SwarmTask.from_dict({**swarm_task_json("SW-01", 1), "status": "in_progress"})]
    team = SwarmTeam(
        id=1,
        task_id="SW-01",
        phase="code",
        modes=["tdd", "classic"],
        coders=[
            {"mode": "tdd", "spec": dict(LUNA_SPEC), "display": LUNA.display()},
            {"mode": "classic", "spec": dict(LUNA_SPEC), "display": LUNA.display()},
        ],
        reviewers=[
            {"reviewer": 1, "spec": dict(LUNA_SPEC), "display": LUNA.display()},
            {"reviewer": 2, "spec": dict(LUNA_SPEC), "display": LUNA.display()},
        ],
    )
    box.state.teams = [team]
    box._prepare_worktrees(team)
    for mode, path in team.worktrees.items():
        (Path(path) / f"{mode}.txt").write_text(f"{mode} work\n", encoding="utf-8")
    for key, value in fields.items():
        setattr(team, key, value)
    return team


def test_resume_of_a_checked_winner_delivers_instead_of_failing(tmp_path: Path) -> None:
    # Cancelled after the winner check applied but before the controller
    # advanced: the old loop raised "winner-fix state is inconsistent".
    repo, brief = repo_and_brief(tmp_path)
    box = make_controller(repo, brief, SwarmRunner(backlog_json(count=1)), tmp_path, min_backlog=1)
    approve = {"verdict": "approve", "summary": "ok", "blocking": []}
    winner_team(
        box,
        repo,
        phase="winner-fix",
        winner="tdd",
        fix_round=1,
        selection={"winner": "tdd", "feedback": ["x"], "last_winner_job": "reviewer"},
        versions={
            "tdd": {"summary": "s", "review": approve, "committed": True,
                    "winner_reviews": [approve]},
            "classic": {"summary": "s", "review": approve},
        },
    )
    box.persist("stopped after the winner check")

    runner = SwarmRunner(backlog_json(count=1))
    state = run_bounded(resumed(box, runner, tmp_path))

    assert state.status == "completed", state.message
    assert not runner.requests, "no agent re-runs a finished round"
    assert state.tasks[0].status == "done" and state.tasks[0].commit
    assert (repo / "tdd.txt").read_text() == "tdd work\n"


def test_a_fix_verdict_without_findings_does_not_stall_the_team(tmp_path: Path) -> None:
    # "revise" with no blocking finding has no job: the old loop never
    # advanced such a team and it held its slot forever.
    repo, brief = repo_and_brief(tmp_path)
    box = make_controller(repo, brief, SwarmRunner(backlog_json(count=1)), tmp_path, min_backlog=1)
    vague = {"verdict": "fix", "summary": "meh", "blocking": []}
    winner_team(
        box,
        repo,
        phase="revise",
        review_round=1,
        versions={"tdd": {"summary": "s", "review": vague}, "classic": {"summary": "s", "review": vague}},
        done_jobs=[],
    )
    box.persist("stopped in revise")

    state = run_bounded(resumed(box, SwarmRunner(backlog_json(count=1)), tmp_path))

    assert state.status == "completed", state.message
    assert state.tasks[0].status == "done"


# ---------------------------------------------------------------------------
# Bounded retries, quota stops and the circuit breaker


class TimesOut(SwarmRunner):
    def __init__(self, backlog: str):
        super().__init__(backlog)
        self.coder_calls = 0

    def _code(self, request: AgentRequest):
        with self._lock:
            self.coder_calls += 1
        raise AgentTimeout("swarm_coder timed out after 3600s", raw_output="")


def test_a_job_that_timed_out_twice_is_not_retried_a_third_time(tmp_path: Path) -> None:
    repo, brief = repo_and_brief(tmp_path)
    runner = TimesOut(backlog_json(count=1))
    box = make_controller(repo, brief, runner, tmp_path, min_backlog=1)
    assert box.config.retry_count == 2
    job = {
        "role": "swarm_coder", "spec": LUNA, "prompt": '"id": "SW-01"', "cwd": repo,
        "relative": "swarm/tasks/SW-01/code-tdd", "team_id": 1, "mode": "tdd",
    }
    with pytest.raises(AgentTimeout):
        box._invoke(job)
    assert runner.coder_calls == 2
    kinds = [json.loads(line)["kind"] for line in (box.store.root / "events.jsonl").read_text().splitlines()]
    assert kinds.count("swarm.agent-retry") == 1


def test_consecutive_pair_failures_trip_the_breaker_and_refund_attempts(tmp_path: Path) -> None:
    repo, brief = repo_and_brief(tmp_path)
    runner = SwarmRunner(backlog_json(count=3), fail_coders=True)
    box = make_controller(repo, brief, runner, tmp_path, min_backlog=3)
    state = run_bounded(box)

    assert state.status == "failed"
    assert "circuit breaker" in state.message
    assert all(task.status != "dropped" for task in state.tasks)
    assert sum(task.attempts for task in state.tasks) < 3, "tripping failures are refunded"
    patches = list((box.store.root / "swarm/tasks").glob("*/attempt-*.patch"))
    assert patches, "the failed pairs' patches stay in the run artifacts"


class QuotaHit(SwarmRunner):
    """The first coder call hits the provider quota; its sibling finishes."""

    def __init__(self, backlog: str):
        super().__init__(backlog)
        self.hit = False

    def _code(self, request: AgentRequest):
        with self._lock:
            first, self.hit = not self.hit, True
        if first:
            raise AgentUsageLimit("You've hit your usage limit", raw_output="")
        return super()._code(request)


def test_a_usage_limit_stops_the_swarm_and_keeps_the_team(tmp_path: Path) -> None:
    repo, brief = repo_and_brief(tmp_path)
    runner = QuotaHit(backlog_json(count=1))
    box = make_controller(repo, brief, runner, tmp_path, min_backlog=1, teams=1)
    state = run_bounded(box)

    assert state.status == "failed"
    assert "quota or policy" in state.message
    [team] = state.teams
    assert all(Path(path).is_dir() for path in team.worktrees.values())
    [task] = state.tasks
    assert task.status == "in_progress" and task.attempts == 0


def test_an_unreadable_winner_check_is_never_an_approval(tmp_path: Path) -> None:
    repo, brief = repo_and_brief(tmp_path)
    box = make_controller(repo, brief, SwarmRunner(backlog_json(count=1)), tmp_path, min_backlog=1)
    team = SwarmTeam(
        id=1, task_id="SW-01", phase="winner-fix", modes=["tdd", "classic"],
        coders=[], reviewers=[], winner="tdd",
        versions={"tdd": {"summary": "s"}, "classic": {"summary": "s"}},
    )
    request = AgentRequest(role="swarm_reviewer", model=LUNA, prompt="", cwd=repo)
    garbage = result(request, "LGTM!")
    relative = "swarm/tasks/SW-01/winner-check-1"

    assert box._apply_winner_review(team, garbage, relative) is False, "first: re-asked"
    assert box._apply_winner_review(team, garbage, relative) is True
    [check] = team.versions["tdd"]["winner_reviews"]
    assert check["verdict"] == "fix" and check["blocking"]


# ---------------------------------------------------------------------------
# The real CLI: swarm-resume, explicit migration and status


def retire_planner(repo: Path, run_id: str) -> None:
    path = repo / ".forge/runs" / run_id / "config.json"
    config = json.loads(path.read_text())
    for role in ("brain", "planner"):
        config["models"][role] = {"provider": RETIRED.provider, "model": RETIRED.model,
                                  "effort": RETIRED.effort}
    path.write_text(json.dumps(config))


def test_cli_swarm_resume_parses_and_refuses_a_retired_planner(tmp_path: Path) -> None:
    repo, _, box = interrupted_run(tmp_path)
    retire_planner(repo, box.run_id)
    assert not load_policy(None).allows(RETIRED, "planner")
    before = (box.store.root / "swarm/state.json").read_text()

    # The exact reported command shape, in a real process: it crashed with
    # AttributeError: 'Namespace' object has no attribute 'policy_path'.
    done = subprocess.run(
        [sys.executable, "-m", "forge", "swarm-resume", "--repo", str(repo),
         "--run-id", box.run_id],
        cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True, timeout=60,
    )
    assert done.returncode == 1
    assert "AttributeError" not in done.stderr
    assert "--migrate-models" in done.stderr and "planner" in done.stderr
    assert (box.store.root / "swarm/state.json").read_text() == before, "a refusal changes nothing"


def test_cli_swarm_resume_migrates_explicitly_and_finishes_the_same_run(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo, first, box = interrupted_run(tmp_path)
    retire_planner(repo, box.run_id)
    worktrees = dict(state_of(repo, box.run_id)["teams"][0]["worktrees"])
    runner = SwarmRunner(backlog_json(count=1))
    monkeypatch.setattr("forge.swarm.AgentRunner", lambda **_: runner)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    previous = signal.getsignal(signal.SIGTERM)
    try:
        code = cli.main(["swarm-resume", "--repo", str(repo), "--run-id", box.run_id,
                         "--migrate-models"])
    finally:
        signal.signal(signal.SIGTERM, previous)

    assert code == 0
    config = config_of(repo, box.run_id)
    assert ModelSpec(**config["models"]["planner"]) == SOL
    assert ModelSpec(**config["models"]["brain"]) == SOL
    backups = list((repo / ".forge/runs" / box.run_id).glob("config.pre-migration-*.json"))
    assert len(backups) == 1 and RETIRED.model in backups[0].read_text()
    migrations = (repo / ".forge/runs" / box.run_id / "swarm/migrations.jsonl").read_text()
    assert "planner" in migrations and RETIRED.model in migrations
    assert all(request.model != RETIRED for request in runner.requests)
    # The same run, the same team and worktrees, and only the unfinished job.
    assert str(runner.requests[0].cwd) in worktrees.values()
    assert str(runner.requests[0].cwd) != first.first_mode
    saved = state_of(repo, box.run_id)
    assert saved["status"] == "completed"
    assert [task["status"] for task in saved["tasks"]] == ["done"]
    printed = capsys.readouterr().out
    assert '"kind": "swarm.agent-done"' in printed, "progress events reach stdout"


def test_cli_swarm_status_reports_progress_and_a_dead_controller(tmp_path: Path, capsys) -> None:
    repo, _, box = interrupted_run(tmp_path)
    assert cli.main(["swarm-status", "--repo", str(repo), "--run-id", box.run_id]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["status"] == "cancelled"
    assert status["tasks"] == {"in_progress": 1}
    [team] = status["teams"]
    assert team["phase"] == "code" and len(team["done_jobs"]) == 1
    assert status["controller"]["pid"] and status["inflight"] == []


# ---------------------------------------------------------------------------
# Independent review findings on 9543e34: crash durability, live-policy and
# quota refusals, systemic preparation faults, heartbeat, migration ownership


class SlowSibling(SwarmRunner):
    """The first coder finishes at once; its sibling waits for the test."""

    def __init__(self, backlog: str):
        super().__init__(backlog)
        self.release = threading.Event()
        self.first_mode = ""

    def _code(self, request: AgentRequest):
        with self._lock:
            first = not self.first_mode
            if first:
                self.first_mode = str(request.cwd)
        if not first:
            assert self.release.wait(30)
        return super()._code(request)


def test_a_finished_sibling_is_durable_while_the_other_coder_still_runs(
    tmp_path: Path, monkeypatch
) -> None:
    repo, brief = repo_and_brief(tmp_path)
    runner = SlowSibling(backlog_json(count=1))
    box = make_controller(repo, brief, runner, tmp_path, min_backlog=1, teams=1)
    waits: list[float] = []
    original_wait = SwarmController._wait_for_any

    def counted(in_flight):
        started = time.monotonic()
        original_wait(in_flight)
        waits.append(time.monotonic() - started)

    monkeypatch.setattr(SwarmController, "_wait_for_any", staticmethod(counted))
    outcome: dict = {}
    thread = threading.Thread(target=lambda: outcome.setdefault("state", box.run()), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        durable: list = []
        while time.monotonic() < deadline:
            path = box.store.root / "swarm/state.json"
            teams = json.loads(path.read_text())["teams"] if path.is_file() else []
            durable = teams[0].get("done_jobs") or [] if teams else []
            if durable:
                break
            time.sleep(0.02)
        # What a SIGKILL right now would leave on disk: the finished coder.
        assert len(durable) == 1 and durable[0].startswith("swarm/tasks/SW-01/code-")
        assert not runner.release.is_set()
        count = len(waits)
        time.sleep(0.5)
        # A finished future held beside a running one must not spin the loop.
        assert len(waits) - count <= 5, len(waits) - count
    finally:
        runner.release.set()
        thread.join(30)
    assert outcome["state"].status == "completed", outcome["state"].message
    coders = [request for request in runner.requests if request.role == "swarm_coder"]
    assert [str(request.cwd) for request in coders].count(runner.first_mode) == 2, (
        "the finished coder ran once for code and once for its revision only"
    )


def closed_runner() -> AgentRunner:
    # The real runner's own gate with a central policy that allows nothing.
    return AgentRunner(policy=PromotionSnapshot(state=PROMOTION_ACTIVE, central=frozenset()))


def test_the_runner_policy_refusal_stops_the_swarm_and_keeps_team_and_attempt(
    tmp_path: Path, monkeypatch
) -> None:
    repo, brief = repo_and_brief(tmp_path)
    box = make_controller(repo, brief, SwarmRunner(backlog_json(count=1)), tmp_path,
                          min_backlog=1, teams=1)
    team = winner_team(box, repo, done_jobs=[])
    worktrees = dict(team.worktrees)
    box.runner = closed_runner()
    monkeypatch.setattr(
        "forge.agents.subprocess.Popen",
        lambda *a, **k: pytest.fail("a refused model must not launch"),
    )
    [first, second] = box._round_jobs(team)
    outcomes = []
    for job in (first, second):
        try:
            outcomes.append((job, box._invoke(job)))
        except Exception as exc:  # the real exception type, not a stand-in
            outcomes.append((job, exc))
    assert all(isinstance(outcome, AgentPolicyRefused) for _, outcome in outcomes)

    with pytest.raises(SwarmFailed, match="quota or policy"):
        box._apply_results(outcomes)
    assert box.state.teams == [team] and team.worktrees == worktrees
    [task] = box.state.tasks
    assert task.status == "in_progress" and task.attempts == 0
    assert state_of(repo, box.run_id)["teams"][0]["worktrees"] == worktrees


QUOTA_BLOCKED_STUB = CENTRAL_STUB.replace(
    "def assert_launchable(model, now=None):\n    return model\n",
    "def assert_launchable(model, now=None):\n"
    "    if model.startswith('openai-codex/'):\n"
    "        raise ValueError(f\"{model!r} quota account 'openai' is blocked until \"\n"
    "                         \"2099-01-01T00:00:00+00:00 (quota: weekly)\")\n"
    "    return model\n",
)


@pytest.mark.parametrize(
    ("stub", "expected", "launches"),
    [
        (QUOTA_BLOCKED_STUB, AgentUsageLimit, False),
        (CENTRAL_STUB.split("def assert_launchable")[0], AgentPolicyRefused, False),
        (CENTRAL_STUB, None, True),
    ],
    ids=["quota-blocked", "no-central-quota-gate", "launchable"],
)
def test_the_runner_runs_the_central_quota_preflight_before_launch(
    tmp_path: Path, monkeypatch, stub: str, expected, launches: bool
) -> None:
    central = tmp_path / "central_policy.py"
    central.write_text(stub, encoding="utf-8")
    monkeypatch.setenv(CENTRAL_POLICY_ENV, str(central))
    launched: list = []

    def popen(*args, **kwargs):
        launched.append(args)
        raise OSError("launch reached in test")

    monkeypatch.setattr("forge.agents.subprocess.Popen", popen)
    request = AgentRequest(role="swarm_coder", model=LUNA, prompt="x", cwd=tmp_path / "cwd")
    if expected is None:
        with pytest.raises(OSError, match="launch reached"):
            AgentRunner().run(request)
    else:
        with pytest.raises(expected, match="not launchable"):
            AgentRunner().run(request)
    assert bool(launched) is launches


def test_systemic_worktree_preparation_failures_trip_the_breaker(
    tmp_path: Path, monkeypatch
) -> None:
    repo, brief = repo_and_brief(tmp_path)
    box = make_controller(repo, brief, SwarmRunner(backlog_json(count=3)), tmp_path,
                          min_backlog=3)

    def broken(*_args, **_kwargs):
        raise SwarmGitError("git worktree add failed: No space left on device")

    monkeypatch.setattr(box, "_create_worktree", broken)
    state = run_bounded(box)

    assert state.status == "failed"
    assert "circuit breaker" in state.message
    assert all(task.status != "dropped" for task in state.tasks), "the backlog is kept"
    assert all(task.attempts == 0 for task in state.tasks), "tripping failures are refunded"


def heartbeat_of(box: SwarmController) -> dict:
    return json.loads((box.store.root / "swarm/heartbeat.json").read_text())


class SlowPlanner(SwarmRunner):
    """A planner that blocks the controller thread for a while."""

    def __init__(self, backlog: str, box_ref: dict):
        super().__init__(backlog)
        self.box_ref = box_ref
        self.seen: list[dict] = []
        self.written: set[int] = set()

    def _plan(self, request: AgentRequest):
        box = self.box_ref["box"]
        for _ in range(8):
            self.seen.append(heartbeat_of(box))
            self.written.add((box.store.root / "swarm/heartbeat.json").stat().st_mtime_ns)
            time.sleep(0.1)
        return super()._plan(request)


def test_heartbeat_shows_a_blocking_planner_ticks_and_ends_terminal(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(swarm_module, "HEARTBEAT_SECONDS", 0.05)
    repo, brief = repo_and_brief(tmp_path)
    ref: dict = {}
    runner = SlowPlanner(backlog_json(count=1), ref)
    box = make_controller(repo, brief, runner, tmp_path, min_backlog=1, teams=1)
    ref["box"] = box
    state = run_bounded(box)
    assert state.status == "completed", state.message

    # Visible while it runs, even inside the throttle window, and the
    # heartbeat keeps ticking while the planner blocks the controller.
    assert all(
        any(item["role"] == "planner" for item in beat["inflight"]) for beat in runner.seen
    )
    assert len(runner.written) > 1, "the heartbeat went stale"
    final = heartbeat_of(box)
    assert final["status"] == "completed" and final["inflight"] == []
    assert final["run_id"] == box.run_id and final["controller_id"]
    assert final["pid_start_ticks"] == swarm_module.process_start_ticks(os.getpid())


def test_swarm_status_does_not_trust_a_reused_pid(tmp_path: Path, capsys) -> None:
    repo, _, box = interrupted_run(tmp_path)
    beat = heartbeat_of(box)
    assert beat["status"] == "cancelled" and beat["inflight"] == []
    # This test process is alive, but it is not the recorded controller.
    beat["pid_start_ticks"] = "1"
    (box.store.root / "swarm/heartbeat.json").write_text(json.dumps(beat))
    assert cli.main(["swarm-status", "--repo", str(repo), "--run-id", box.run_id]) == 0
    controller = json.loads(capsys.readouterr().out)["controller"]
    assert controller["pid"] == os.getpid()
    assert controller["alive"] is False and controller["identity_verified"] is False
    assert controller["heartbeat_status"] == "cancelled"


def run_files(box: SwarmController) -> dict[str, bytes]:
    return {
        str(path.relative_to(box.store.root)): path.read_bytes()
        for path in sorted(box.store.root.rglob("*"))
        if path.is_file()
    }


def test_a_resume_migration_writes_nothing_before_it_owns_the_repository(
    tmp_path: Path,
) -> None:
    repo, _, box = interrupted_run(tmp_path)
    retire_planner(repo, box.run_id)
    before = run_files(box)
    runner = SwarmRunner(backlog_json(count=1))
    options = {"runner": runner, "state_home": tmp_path / "state", "teams": 1,
               "min_backlog": 1}

    holder = RepositoryExecutionLock(repo, "main", "other-controller")
    holder.acquire()
    try:
        blocked = SwarmController.resume_existing(
            repo, box.run_id, migrate_models=True, **options
        )
        assert blocked.config.models["planner"] == SOL
        assert run_files(box) == before, "resume_existing wrote before owning the run"
        with pytest.raises(ExecutionLocked):
            blocked.run()
        assert run_files(box) == before, "a refused takeover changed the run"
    finally:
        holder.release()

    state = run_bounded(
        SwarmController.resume_existing(repo, box.run_id, migrate_models=True, **options)
    )
    assert state.status == "completed", state.message
    assert ModelSpec(**config_of(repo, box.run_id)["models"]["planner"]) == SOL
    root = box.store.root
    assert len(list(root.glob("config.pre-migration-*.json"))) == 1
    assert len((root / "swarm/migrations.jsonl").read_text().splitlines()) == 1
    # Repeating the explicit migration is a no-op: no second backup.
    again = SwarmController.resume_existing(repo, box.run_id, migrate_models=True, **options)
    assert again._migration is None
    assert len(list(root.glob("config.pre-migration-*.json"))) == 1


def test_a_controller_refuses_state_another_controller_changed_since_loading(
    tmp_path: Path,
) -> None:
    repo, _, box = interrupted_run(tmp_path)
    stale = SwarmController.resume_existing(
        repo, box.run_id, runner=SwarmRunner(backlog_json(count=1)),
        state_home=tmp_path / "state", teams=1, min_backlog=1,
    )
    # Another controller advanced the run after this one loaded it.
    path = box.store.root / "swarm/state.json"
    newer = json.loads(path.read_text())
    newer["message"] = "written by a newer controller"
    path.write_text(json.dumps(newer))
    before = run_files(box)
    with pytest.raises(SwarmFailed, match="changed after this controller loaded"):
        stale.run()
    assert run_files(box) == before


def test_a_state_write_between_load_and_digest_is_still_refused(
    tmp_path: Path, monkeypatch
) -> None:
    # Another owner writes right after this controller parsed the state: the
    # digest must describe the bytes parsed, not a later read.
    repo, _, box = interrupted_run(tmp_path)
    original = SwarmController._load_swarm_state

    def load_then_newer_owner_writes(self):
        loaded = original(self)
        path = self.store.root / "swarm/state.json"
        newer = json.loads(path.read_text())
        newer["message"] = "a newer owner finished the task"
        newer["tasks"][0]["status"] = "done"
        newer["teams"] = []
        path.write_text(json.dumps(newer))
        return loaded

    monkeypatch.setattr(SwarmController, "_load_swarm_state", load_then_newer_owner_writes)
    stale = SwarmController.resume_existing(
        repo, box.run_id, runner=SwarmRunner(backlog_json(count=1)),
        state_home=tmp_path / "state", teams=1, min_backlog=1,
    )
    before = run_files(box)
    monkeypatch.setattr(stale, "_run_locked", lambda: stale.persist("overwrote newer state"))
    with pytest.raises(SwarmFailed, match="state of run .* changed"):
        stale.run()
    assert run_files(box) == before


def test_a_plain_resume_refuses_a_config_changed_since_loading(
    tmp_path: Path, monkeypatch
) -> None:
    repo, _, box = interrupted_run(tmp_path)
    stale = SwarmController.resume_existing(
        repo, box.run_id, runner=SwarmRunner(backlog_json(count=1)),
        state_home=tmp_path / "state", teams=1, min_backlog=1,
    )
    assert stale._migration is None
    path = box.store.root / "config.json"
    raw = json.loads(path.read_text())
    raw["push"] = True
    raw["agent_timeout_seconds"] = 17
    path.write_text(json.dumps(raw))
    before = run_files(box)
    entered: list = []
    monkeypatch.setattr(stale, "_run_locked", lambda: entered.append(True))
    with pytest.raises(SwarmFailed, match="config of run .* changed"):
        stale.run()
    assert not entered and run_files(box) == before
