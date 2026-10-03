"""The control room shows runs a CLI process owns, without owning them.

A swarm started with ``forge swarm-resume`` was invisible: the UI listed only
runs it had started itself or found in its old ``ui-session.json``. These
tests drive real swarm controllers and real processes against a registry and
the HTTP API, before and after the UI starts.
"""

import base64
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from forge import external as external_module
from forge import swarm as swarm_module
from forge.access import AccessGate
from forge.agents import AgentRequest
from forge.locking import RepositoryExecutionLock
from forge.swarm import SwarmController
from forge.web import ForgeHandler, RunRegistry, restart_payload

from test_swarm import SwarmRunner, backlog_json, make_controller, repo_and_brief
from test_swarm_recovery import interrupted_run, resumed, run_bounded


SECRET = "external-runs-test-secret"


@pytest.fixture(autouse=True)
def fast_observation(monkeypatch):
    monkeypatch.setattr(external_module, "SCAN_TTL_SECONDS", 0.0)
    monkeypatch.setattr(swarm_module, "HEARTBEAT_SECONDS", 0.05)


class HeldCoder(SwarmRunner):
    """The first coder blocks until the test releases it."""

    def __init__(self, backlog: str):
        super().__init__(backlog)
        self.started = threading.Event()
        self.release = threading.Event()

    def _code(self, request: AgentRequest):
        self.started.set()
        assert self.release.wait(30)
        return super()._code(request)


def serve(registry: RunRegistry):
    handler = type(
        "ExternalTestHandler",
        (ForgeHandler,),
        {
            "registry": registry,
            "gate": AccessGate(SECRET.encode()),
            "request_restart": staticmethod(
                lambda confirm: restart_payload(registry.active_count(), confirm)
            ),
        },
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def call(base: str, path: str, *, body: dict | None = None, secret: str | None = SECRET):
    headers = {"Content-Type": "application/json"}
    if secret is not None:
        headers["X-Forge-Access"] = secret
    request = urllib.request.Request(
        base + path,
        data=None if body is None else json.dumps(body).encode(),
        headers=headers,
        method="GET" if body is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def wait_for(predicate, seconds: float = 20.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    pytest.fail("condition was not reached")


def start_in_thread(box: SwarmController) -> tuple[threading.Thread, dict]:
    outcome: dict = {}
    thread = threading.Thread(target=lambda: outcome.setdefault("state", box.run()), daemon=True)
    thread.start()
    return thread, outcome


def run_files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def only(runs: list[dict], run_id: str) -> dict:
    matching = [item for item in runs if item["run_id"] == run_id]
    assert len(matching) == 1, [item["run_id"] for item in runs]
    return matching[0]


# ---------------------------------------------------------------------------
# A live CLI swarm, the UI started before it


def test_ui_started_first_shows_a_live_cli_swarm_and_its_end(tmp_path: Path) -> None:
    repo, brief = repo_and_brief(tmp_path)
    registry = RunRegistry(state_home=tmp_path / "state")
    server, base = serve(registry)
    try:
        assert call(base, "/api/runs") == (200, [])
        runner = HeldCoder(backlog_json(count=1))
        box = make_controller(repo, brief, runner, tmp_path, min_backlog=1, teams=1)
        thread, outcome = start_in_thread(box)
        assert runner.started.wait(20)

        live = wait_for(lambda: [
            item for item in call(base, "/api/runs")[1]
            if item["run_id"] == box.run_id and item["active_agents"]
        ])[0]
        assert live["external"] is True and live["kind"] == "swarm"
        assert live["status"] == "running" and live["alive"] is True
        assert live["liveness"] == "active" and live["orphaned"] is False
        assert live["project"] == repo.name and live["repo"] == str(repo)
        assert live["controllable"] is False and "CLI" in live["control_note"]
        assert live["tasks_total"] == 1 and live["tasks"] == {"in_progress": 1}
        assert live["teams"][0]["task"] == "SW-01" and live["teams"][0]["phase"]
        assert {agent["role"] for agent in live["active_agents"].values()} == {"swarm_coder"}
        assert live["freshness"]["heartbeat_age_seconds"] is not None
        assert live["freshness"]["heartbeat_stale"] is False
        assert live["controller"]["identity_verified"] is True

        status, detail = call(base, f"/api/runs/{box.run_id}")
        assert status == 200
        assert detail["task_list"][0]["id"] == "SW-01"
        assert any("swarm.state" in line for line in detail["events"])

        health = call(base, "/api/health")[1]
        assert health["active_runs"] == 0 and health["external_active_runs"] == 1

        # Controls never reach a run the panel does not own.
        for action in ("pause", "resume", "cancel", "recover"):
            status, value = call(base, f"/api/runs/{box.run_id}/{action}", body={})
            assert status == 409 and value["read_only"] is True, action
        status, value = call(
            base, "/api/runs/recover", body={"repo": str(repo), "run_id": box.run_id}
        )
        assert status == 409 and "swarm-resume" in value["error"]

        runner.release.set()
        thread.join(60)
        assert outcome["state"].status == "completed", outcome["state"].message

        ended = wait_for(lambda: [
            item for item in call(base, "/api/runs")[1]
            if item["run_id"] == box.run_id and item["status"] == "completed"
        ])[0]
        assert ended["alive"] is False and ended["liveness"] == "ended"
        assert ended["active_agents"] == {} and ended["tasks"] == {"done": 1}
        assert call(base, "/api/health")[1]["external_active_runs"] == 0
    finally:
        runner_release = locals().get("runner")
        if runner_release is not None:
            runner_release.release.set()
        server.shutdown()
        server.server_close()


def test_ui_started_after_the_cli_finds_the_live_swarm(tmp_path: Path) -> None:
    repo, brief = repo_and_brief(tmp_path)
    runner = HeldCoder(backlog_json(count=1))
    box = make_controller(repo, brief, runner, tmp_path, min_backlog=1, teams=1)
    thread, outcome = start_in_thread(box)
    try:
        assert runner.started.wait(20)
        registry = RunRegistry(state_home=tmp_path / "state")
        registry.restore_session()
        live = wait_for(lambda: [
            item for item in registry.list()
            if item["run_id"] == box.run_id and item["alive"]
        ])[0]
        assert live["status"] == "running" and live["external"] is True
        assert registry.active_count() == 0
        # A UI restart interrupts only panel runs; the CLI run keeps going.
        registry.interrupt_live()
        runner.release.set()
        thread.join(60)
        assert outcome["state"].status == "completed"
    finally:
        runner.release.set()


def test_reading_an_external_run_writes_nothing_and_leaves_the_lock_free(
    tmp_path: Path,
) -> None:
    repo, _runner, box = interrupted_run(tmp_path)
    before = run_files(box.store.root)
    registry = RunRegistry(state_home=tmp_path / "state")
    listed = only(registry.list(), box.run_id)
    registry.get(box.run_id)
    assert listed["status"] == "cancelled" and listed["alive"] is False
    assert run_files(box.store.root) == before
    # Nothing in the UI holds the repository lock: a controller can take it.
    lock = RepositoryExecutionLock(repo, "main", "probe")
    lock.acquire()
    lock.release()


# ---------------------------------------------------------------------------
# swarm-resume of the same run


def test_swarm_resume_updates_the_same_entry_without_duplicates(tmp_path: Path) -> None:
    repo, _runner, box = interrupted_run(tmp_path)
    registry = RunRegistry(state_home=tmp_path / "state")
    # The repository is also known from the panel's preferences: three
    # discovery sources name the same run.
    registry.save_preferences({"repo": str(repo)})
    cancelled = only(registry.list(), box.run_id)
    assert cancelled["status"] == "cancelled" and cancelled["liveness"] == "ended"

    runner = HeldCoder(backlog_json(count=1))
    again = resumed(box, runner, tmp_path)
    thread, outcome = start_in_thread(again)
    try:
        assert runner.started.wait(20)
        live = wait_for(lambda: [
            item for item in registry.list()
            if item["run_id"] == box.run_id and item["alive"]
        ])[0]
        assert live["status"] == "running"
        assert live["controller"]["controller_id"] == again._controller_id
        only(registry.list(), box.run_id)
        runner.release.set()
        thread.join(60)
        assert outcome["state"].status == "completed", outcome["state"].message
        done = only(registry.list(), box.run_id)
        assert done["status"] == "completed" and done["alive"] is False
    finally:
        runner.release.set()


def test_a_run_started_before_the_run_index_is_found_through_its_worktrees(
    tmp_path: Path,
) -> None:
    repo, brief = repo_and_brief(tmp_path)
    runner = HeldCoder(backlog_json(count=1))
    box = make_controller(repo, brief, runner, tmp_path, min_backlog=1, teams=1)
    thread, outcome = start_in_thread(box)
    try:
        assert runner.started.wait(20)
        index = tmp_path / "state" / external_module.INDEX_DIR / f"{box.run_id}.json"
        wait_for(index.is_file)
        index.unlink()  # an old CLI never wrote one
        registry = RunRegistry(state_home=tmp_path / "state")
        live = wait_for(lambda: [
            item for item in registry.list()
            if item["run_id"] == box.run_id and item["alive"]
        ])[0]
        assert live["repo"] == str(repo)
    finally:
        runner.release.set()
        thread.join(60)
    assert outcome["state"].status == "completed"


# ---------------------------------------------------------------------------
# Process exit and a stale "running" status


HOLDER = """
import json, os, sys, time
from forge.external import process_start_ticks
path, run_id = sys.argv[1], sys.argv[2]
beat = json.load(open(path))
beat.update(pid=os.getpid(), pid_start_ticks=process_start_ticks(os.getpid()), run_id=run_id,
            status="running", updated_at=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()))
open(path, "w").write(json.dumps(beat))
print("ready", flush=True)
time.sleep(60)
"""


def stale_running(box: SwarmController) -> None:
    state_path = box.store.root / "swarm" / "state.json"
    state = json.loads(state_path.read_text())
    state["status"] = "running"
    state_path.write_text(json.dumps(state))


def test_a_killed_controller_turns_its_running_run_orphaned(tmp_path: Path) -> None:
    repo, _runner, box = interrupted_run(tmp_path)
    stale_running(box)
    beat = box.store.root / "swarm" / "heartbeat.json"
    process = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(beat), box.run_id],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == "ready"
        registry = RunRegistry(state_home=tmp_path / "state")
        live = only(registry.list(), box.run_id)
        assert live["alive"] is True and live["liveness"] == "active"
        assert live["controller"]["pid"] == process.pid
    finally:
        process.kill()
        process.wait(10)
    orphaned = only(registry.list(), box.run_id)
    assert orphaned["status"] == "running" and orphaned["alive"] is False
    assert orphaned["orphaned"] is True and orphaned["display_status"] == "orphaned"
    assert orphaned["active_agents"] == {}


def test_a_reused_pid_is_not_a_live_controller(tmp_path: Path) -> None:
    repo, _runner, box = interrupted_run(tmp_path)
    stale_running(box)
    beat_path = box.store.root / "swarm" / "heartbeat.json"
    beat = json.loads(beat_path.read_text())
    beat.update(pid=os.getpid(), pid_start_ticks="1")  # alive, but another process
    beat_path.write_text(json.dumps(beat))
    registry = RunRegistry(state_home=tmp_path / "state")
    value = only(registry.list(), box.run_id)
    assert value["liveness"] == "orphaned" and value["controller"]["identity_verified"] is False


def test_old_finished_runs_stay_hidden_but_a_seen_run_stays_listed(
    tmp_path: Path, monkeypatch
) -> None:
    repo, _runner, box = interrupted_run(tmp_path)
    registry = RunRegistry(state_home=tmp_path / "state")
    only(registry.list(), box.run_id)
    later = time.time() + external_module.RECENT_SECONDS + 60
    registry.external.clock = lambda: later
    # Seen by this UI: an exit is reported, not silently dropped.
    only(registry.list(), box.run_id)
    fresh = RunRegistry(state_home=tmp_path / "state")
    fresh.external.clock = lambda: later
    assert [item for item in fresh.list() if item["run_id"] == box.run_id] == []


# ---------------------------------------------------------------------------
# Legacy state.json runs and the access gate


def test_a_legacy_cli_run_in_a_watched_repo_is_live_only_while_it_holds_the_lock(
    tmp_path: Path,
) -> None:
    repo, _brief = repo_and_brief(tmp_path)
    run_id = "20261003-120000-abcdef12"
    root = repo / ".forge" / "runs" / run_id
    root.mkdir(parents=True)
    (root / "state.json").write_text(json.dumps({
        "run_id": run_id, "status": "running", "phase": "coding",
        "sprint_number": 2, "sprint_iteration": 3, "message": "coding SW-1",
        "created_at": "2026-10-03T10:00:00+00:00", "updated_at": "2026-10-03T10:05:00+00:00",
        "config": {}, "iterations": [],
    }))
    registry = RunRegistry(state_home=tmp_path / "state", watch_repos=[str(repo)])
    lock = RepositoryExecutionLock(repo, "main", run_id)
    lock.acquire()
    try:
        live = only(registry.list(), run_id)
        assert live["kind"] == "forge" and live["alive"] is True
        assert live["phase"] == "coding" and live["sprint_iteration"] == 3
    finally:
        lock.release()
    ended = only(registry.list(), run_id)
    assert ended["alive"] is False and ended["orphaned"] is True
    with pytest.raises(external_module.ExternalRunReadOnly):
        registry.control(run_id, "cancel")


def test_the_access_gate_still_guards_external_runs(tmp_path: Path) -> None:
    repo, _runner, box = interrupted_run(tmp_path)
    server, base = serve(RunRegistry(state_home=tmp_path / "state"))
    try:
        assert call(base, "/api/runs", secret=None)[0] == 401
        assert call(base, f"/api/runs/{box.run_id}", secret="wrong")[0] == 401
        basic = base64.b64encode(f"jan:{SECRET}".encode()).decode()
        request = urllib.request.Request(
            base + f"/api/runs/{box.run_id}", headers={"Authorization": f"Basic {basic}"}
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            assert json.loads(response.read())["run_id"] == box.run_id
    finally:
        server.shutdown()
        server.server_close()


def test_a_control_request_before_any_listing_is_refused_as_read_only(tmp_path: Path) -> None:
    repo, _runner, box = interrupted_run(tmp_path)
    server, base = serve(RunRegistry(state_home=tmp_path / "state"))
    try:
        # No /api/runs call first: the run is not cached by the watcher yet.
        status, value = call(base, f"/api/runs/{box.run_id}/pause", body={})
        assert status == 409 and value["read_only"] is True
        assert call(base, "/api/runs/no-such-run/cancel", body={})[0] == 404
    finally:
        server.shutdown()
        server.server_close()
