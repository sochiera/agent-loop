"""Swarm resume, interruption and autonomous-recovery regressions.

Run 20261003-000921-306c86f2 could not be resumed: ``swarm-resume`` crashed on
a missing ``--policy-path`` flag, its saved planner had been retired, a
cancelled round re-ran finished agents, and one GLM job timed out three times
in a row. These tests pin each of those paths.
"""

import json
import signal
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from forge import cli
from forge.agents import AgentRequest, AgentTimeout, AgentUsageLimit
from forge.models import ModelSpec, RunConfig
from forge.policy import CHEAP_CODER_POOL, LUNA, SOL, load_policy
from forge.swarm import SwarmController, SwarmTask, SwarmTeam

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
