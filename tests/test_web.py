import json
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from pathlib import Path

from forge.access import AccessGate, GateMisconfigured, gate_from_env, read_expected_value
from forge.models import CODER_ROLES, ROLE_NAMES, STAFF_ROLES, RunState
from forge.web import (
    ForgeHandler,
    LiveRun,
    RunRegistry,
    browse_filesystem,
    is_recoverable_snapshot,
    read_text_file,
    read_last_lines,
    recovery_hint_from_snapshot,
    restart_payload,
    sanitize_preferences,
)


def test_recovery_hint_uses_same_state_snapshot() -> None:
    hint = recovery_hint_from_snapshot(
        {
            "status": "failed",
            "cycle": 7,
            "sprint_number": 3,
            "sprint_iteration": 4,
            "needs_product_owner": False,
            "active_iteration": {"phase": "testing", "slot": 5},
        }
    )

    assert hint == {
        "kind": "recover",
        "action": "testing",
        "cycle": 7,
        "sprint": 3,
        "slot": 5,
    }


def test_recovery_hint_tolerates_malformed_numeric_state() -> None:
    hint = recovery_hint_from_snapshot(
        {
            "status": "failed",
            "cycle": "broken",
            "sprint_number": None,
            "sprint_iteration": "also-broken",
            "active_iteration": {"slot": "invalid"},
        }
    )

    assert hint["cycle"] == 0
    assert hint["sprint"] == 1
    assert hint["slot"] == 1


def test_only_external_stalls_are_advertised_as_recoverable() -> None:
    assert is_recoverable_snapshot(
        {"status": "stalled", "stalled_recoverable": True}
    )
    assert not is_recoverable_snapshot(
        {"status": "stalled", "stalled_recoverable": False}
    )


def test_read_last_lines_reads_only_requested_tail(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text("".join(f"line-{index}\n" for index in range(500)), encoding="utf-8")

    lines = read_last_lines(path, 200)

    assert len(lines) == 200
    assert lines[0] == "line-300"
    assert lines[-1] == "line-499"

    oversized = tmp_path / "oversized.jsonl"
    oversized.write_bytes(b"x" * (2 * 1024 * 1024))
    assert read_last_lines(oversized, max_bytes=1024) == []


def test_web_control_room_serves_ui_and_api(tmp_path):
    repo = tmp_path / "empty-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, stdout=subprocess.PIPE)
    (repo / "goal.md").write_text("# Build it\n", encoding="utf-8")
    registry = RunRegistry(state_home=tmp_path)
    handler = type(
        "TestForgeHandler",
        (ForgeHandler,),
        {
            "registry": registry,
            "request_restart": staticmethod(
                lambda confirm: restart_payload(registry.active_count(), confirm)
            ),
        },
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        html = urllib.request.urlopen(base + "/", timeout=2).read().decode()
        assert "Forge Control Room" in html
        assert "model-provider" in html
        assert "Coder model pool" in html
        assert 'id="shared-staff"' in html
        assert 'id="enable-backup"' in html
        assert 'id="add-coder"' in html
        assert "model-remove" in html
        assert 'id="restart"' in html
        assert 'id="browse-repo"' in html
        assert 'id="browse-brief"' in html
        assert 'id="fs-explorer"' in html
        script = urllib.request.urlopen(base + "/app.js", timeout=2).read().decode()
        assert "/api/catalog" in script
        assert 'data-action="recover" ${!run.recoverable ? "disabled" : ""}' in script
        assert "/api/browse" in script
        assert "/api/file" in script
        assert "/api/preferences" in script
        runs = json.loads(urllib.request.urlopen(base + "/api/runs", timeout=2).read())
        assert runs == []
        encoded_repo = urllib.parse.quote(str(repo))
        summary = json.loads(
            urllib.request.urlopen(base + f"/api/repository?repo={encoded_repo}", timeout=2).read()
        )
        assert summary["branches"] == ["main"]
        assert summary["has_head"] is False
        assert summary["brief_path"] == str(repo / "goal.md")
        assert summary["brief_text"] == "# Build it\n"
        catalog = json.loads(urllib.request.urlopen(base + "/api/catalog", timeout=2).read())
        by_key = {item["key"]: item for item in catalog["models"]}
        assert set(by_key) == {
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "gpt-5.6-luna",
            "glm-5.3-flash",
            "deepseek-v4.1-flash",
            "mimo-v2.6-flash",
        }
        assert by_key["gpt-5.6-sol"]["providers"] == ["codex"]
        assert by_key["gpt-5.6-luna"]["providers"] == ["codex"]
        assert by_key["glm-5.3-flash"]["family"] == "glm"
        assert by_key["glm-5.3-flash"]["ids"]["opencode"] == "opencode-go/glm-5.3-flash"
        assert by_key["deepseek-v4.1-flash"]["ids"]["opencode"] == (
            "opencode-go/deepseek-v4.1-flash"
        )
        assert by_key["mimo-v2.6-flash"]["family"] == "mimo"
        assert "grok-4.6" not in by_key
        assert "kimi-k3" not in by_key
        assert catalog["defaults"]["coder_tdd"] == "codex:gpt-5.6-luna:high"
        assert catalog["defaults"]["coder_explore"] == "codex:gpt-5.6-luna:high"
        assert catalog["defaults"]["coder_classic"] == "codex:gpt-5.6-luna:high"
        assert catalog["defaults"]["test_author"] == "codex:gpt-5.6-luna:high"
        assert catalog["defaults"]["reviewer"] == "codex:gpt-5.6-terra:high"
        assert catalog["policy"]["promotion_state"] == "unknown"
        assert "model-effort" in html
        assert 'class="model-effort" required' not in html
        assert "Coder draw" in urllib.request.urlopen(base + "/app.js", timeout=2).read().decode()
        assert "opencode" in catalog["providers"]
        assert "claude" not in catalog["providers"]
        listing = json.loads(
            urllib.request.urlopen(
                base + f"/api/browse?path={encoded_repo}", timeout=2
            ).read()
        )
        assert listing["path"] == str(repo)
        names = {item["name"]: item for item in listing["entries"]}
        assert names["goal.md"]["kind"] == "file"
        preview = json.loads(
            urllib.request.urlopen(
                base + f"/api/file?path={urllib.parse.quote(str(repo / 'goal.md'))}",
                timeout=2,
            ).read()
        )
        assert preview["text"] == "# Build it\n"
        empty_prefs = json.loads(urllib.request.urlopen(base + "/api/preferences", timeout=2).read())
        assert empty_prefs["models"] == {}
        saved_prefs = json.loads(
            urllib.request.urlopen(
                urllib.request.Request(
                    base + "/api/preferences",
                    data=json.dumps(
                        {
                            "models": {"brain": "opencode:grok-4.6:high"},
                            "coder_models": ["opencode:kimi-k3:high"],
                            "push": False,
                        }
                    ).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                ),
                timeout=2,
            ).read()
        )
        assert saved_prefs["models"]["brain"] == "opencode:grok-4.6:high"
        loaded_prefs = json.loads(urllib.request.urlopen(base + "/api/preferences", timeout=2).read())
        assert loaded_prefs["coder_models"] == ["opencode:kimi-k3:high"]
        health = json.loads(urllib.request.urlopen(base + "/api/health", timeout=2).read())
        assert health == {"ok": True, "active_runs": 0}
        restart = json.loads(
            urllib.request.urlopen(
                urllib.request.Request(
                    base + "/api/restart",
                    data=b'{"confirm": false}',
                    headers={"Content-Type": "application/json"},
                    method="POST",
                ),
                timeout=2,
            ).read()
        )
        assert restart == {"restarting": True, "active_runs": 0}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_post_runs_draws_the_coder_pool(tmp_path, monkeypatch):
    repo = tmp_path / "empty-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, stdout=subprocess.PIPE)
    (repo / "goal.md").write_text("# Build it\n", encoding="utf-8")

    class DummyOrchestrator:
        def __init__(self, config, state_home=None):
            self.config = config
            self.run_id = "pool-run"
            self.state = RunState(
                run_id=self.run_id,
                status="created",
                phase="preflight",
                created_at="now",
                updated_at="now",
                config=config.to_dict(),
            )
            self.store = type("Store", (), {"root": tmp_path / "artifacts"})()

        def run(self) -> None:
            self.state.status = "running"

        def activity_snapshot(self) -> dict:
            return {}

    monkeypatch.setattr("forge.web.ForgeOrchestrator", DummyOrchestrator)
    registry = RunRegistry(state_home=tmp_path)
    payload = {
        "repo": str(repo),
        "brief_path": str(repo / "goal.md"),
        "push": False,
        "models": {
            role: "codex:gpt-5.6-sol:high" for role in STAFF_ROLES
        },
        "coder_models": ["opencode:glm-5.3-flash"],
    }
    created = registry.start(payload)
    models = created["config"]["models"]
    for role in CODER_ROLES:
        assert models[role]["model"] == "opencode-go/glm-5.3-flash"
    assert models["brain"]["model"] == "gpt-5.6-sol"
    listed = registry.list()
    assert listed[0]["run_id"] == "pool-run"

    handler = type(
        "TestForgeHandler",
        (ForgeHandler,),
        {"registry": registry, "request_restart": None},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        request = urllib.request.Request(
            base + "/api/runs",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        posted = json.loads(urllib.request.urlopen(request, timeout=2).read())
        assert posted["config"]["models"]["coder_tdd"]["model"] == (
            "opencode-go/glm-5.3-flash"
        )
        bad = urllib.request.Request(
            base + "/api/runs",
            data=json.dumps(
                {**payload, "models": {**payload["models"], "brain": "invalid"}}
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(bad, timeout=2)
        assert error.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_browse_filesystem_lists_dirs_and_files(tmp_path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "zeta.md").write_text("hello\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    listing = browse_filesystem(str(tmp_path))
    assert listing["path"] == str(tmp_path.resolve())
    assert listing["parent"] == str(tmp_path.resolve().parent)
    assert listing["home"] == str(Path.home().resolve())
    assert listing["truncated"] is False
    assert [item["name"] for item in listing["entries"]] == ["alpha", "project", "zeta.md"]
    by_name = {item["name"]: item for item in listing["entries"]}
    assert by_name["alpha"] == {
        "name": "alpha",
        "path": str((tmp_path / "alpha").resolve()),
        "kind": "dir",
        "is_repo": False,
    }
    assert by_name["project"]["kind"] == "dir"
    assert by_name["project"]["is_repo"] is True
    assert by_name["zeta.md"]["kind"] == "file"
    nested = browse_filesystem(str(tmp_path / "zeta.md"))
    assert nested["path"] == str(tmp_path.resolve())
    home = browse_filesystem("")
    assert home["path"] == str(Path.home().resolve())


def test_read_text_file_rejects_missing_and_binary(tmp_path):
    path = tmp_path / "goal.md"
    path.write_text("# Build it\n", encoding="utf-8")
    assert read_text_file(str(path)) == {"path": str(path.resolve()), "text": "# Build it\n"}
    with pytest.raises(ValueError, match="path is required"):
        read_text_file("")
    with pytest.raises(ValueError, match="file does not exist"):
        read_text_file(str(tmp_path / "missing.md"))
    binary = tmp_path / "blob.bin"
    binary.write_bytes(b"\xff\xfe")
    with pytest.raises(ValueError, match="not valid UTF-8"):
        read_text_file(str(binary))
    with pytest.raises(ValueError, match="path does not exist"):
        browse_filesystem(str(tmp_path / "missing"))


def test_preferences_round_trip_and_run_fallback(tmp_path, monkeypatch):
    assert sanitize_preferences(
        {
            "repo": "/tmp/repo",
            "briefPath": "/tmp/goal.md",
            "models": {"brain": "opencode:grok-4.6:high", "coder_tdd": "ignored"},
            "coder_models": ["opencode:kimi-k3:high", "", "codex:gpt-5.6-luna:high"],
            "push": False,
            "ignore": True,
        }
    ) == {
        "repo": "/tmp/repo",
        "branch": "",
        "brief_path": "/tmp/goal.md",
        "brief": "",
        "push": False,
        "models": {
            "brain": "opencode:grok-4.6:high",
        },
        "coder_models": ["opencode:kimi-k3:high", "codex:gpt-5.6-luna:high"],
        "shared_staff_model": False,
        "backup": "",
    }

    repo = tmp_path / "empty-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, stdout=subprocess.PIPE)
    (repo / "goal.md").write_text("# Build it\n", encoding="utf-8")

    class DummyOrchestrator:
        def __init__(self, config, state_home=None):
            self.config = config
            self.run_id = "prefs-run"
            self.state = RunState(
                run_id=self.run_id,
                status="created",
                phase="preflight",
                created_at="now",
                updated_at="now",
                config=config.to_dict(),
            )
            self.store = type("Store", (), {"root": tmp_path / "artifacts"})()

        def run(self) -> None:
            self.state.status = "running"

        def activity_snapshot(self) -> dict:
            return {}

    monkeypatch.setattr("forge.web.ForgeOrchestrator", DummyOrchestrator)
    registry = RunRegistry(state_home=tmp_path)
    assert registry.load_preferences() == {
        "repo": "",
        "branch": "",
        "brief_path": "",
        "brief": "",
        "push": True,
        "models": {},
        "coder_models": [],
        "shared_staff_model": False,
        "backup": "",
    }
    saved = registry.save_preferences(
        {
            "models": {
                "brain": "opencode:grok-4.6:high",
                "coder": "opencode:kimi-k3:high",
            },
        }
    )
    assert saved["models"]["brain"] == "opencode:grok-4.6:high"
    assert registry.load_preferences()["coder_models"] == ["opencode:kimi-k3:high"]

    registry.start(
        {
            "repo": str(repo),
            "brief_path": str(repo / "goal.md"),
            "push": False,
            "models": {
                role: "codex:gpt-5.6-sol:high" for role in STAFF_ROLES
            },
            "coder_models": ["opencode:glm-5.3-flash"],
        }
    )
    (tmp_path / "ui-preferences.json").unlink()
    fallback = registry.load_preferences()
    assert fallback["models"]["brain"] == "codex:gpt-5.6-sol:high"
    assert fallback["coder_models"] == ["opencode:opencode-go/glm-5.3-flash"] * 3


def test_concurrent_recover_live_launches_exactly_one_thread(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    class BlockingOrchestrator:
        run_id = "recover-race"

        def __init__(self):
            self.state = RunState(
                run_id=self.run_id,
                status="failed",
                phase="review",
                created_at="now",
                updated_at="now",
                config={},
            )
            self.store = type("Store", (), {"root": tmp_path / "artifacts"})()

        def recover_failed(self) -> None:
            entered.set()
            release.wait(2)

        def activity_snapshot(self) -> dict:
            return {}

        def recovery_hint(self) -> dict:
            return {"kind": "recover", "action": "review"}

    registry = RunRegistry(state_home=tmp_path)
    live = LiveRun(BlockingOrchestrator(), threading.Thread())
    registry._runs[live.orchestrator.run_id] = live
    callers = threading.Barrier(3)
    outcomes: list[str] = []

    def recover() -> None:
        callers.wait()
        try:
            registry.recover_live(live.orchestrator.run_id)
        except ValueError:
            outcomes.append("blocked")
        else:
            outcomes.append("started")

    threads = [threading.Thread(target=recover) for _ in range(2)]
    for thread in threads:
        thread.start()
    callers.wait()
    assert entered.wait(1)
    for thread in threads:
        thread.join(timeout=1)
    release.set()
    live.thread.join(timeout=1)

    assert sorted(outcomes) == ["blocked", "started"]


def test_restore_session_exposes_dead_running_run_as_recoverable(
    tmp_path, monkeypatch
):
    recovered = threading.Event()
    release = threading.Event()
    repo = tmp_path / "repo"
    repo.mkdir()

    class DeadRunningOrchestrator:
        run_id = "dead-running"

        def __init__(self):
            self.config = type("Config", (), {"repo": str(repo)})()
            self.state = RunState(
                run_id=self.run_id,
                status="running",
                phase="coding",
                created_at="now",
                updated_at="now",
                config={},
            )
            self.store = type(
                "Store",
                (),
                {
                    "root": tmp_path / "artifacts",
                    "load_state": lambda store: self.state,
                },
            )()

        @classmethod
        def from_existing(cls, _repo, _run_id, **_kwargs):
            return cls()

        def recover_failed(self) -> None:
            recovered.set()
            release.wait(2)

        def recovery_hint(self) -> dict:
            return {"kind": "recover", "action": "coding"}

        def activity_snapshot(self) -> dict:
            return {}

    monkeypatch.setattr("forge.web.ForgeOrchestrator", DeadRunningOrchestrator)
    (tmp_path / "ui-session.json").write_text(
        json.dumps([{"repo": str(repo), "run_id": "dead-running"}]),
        encoding="utf-8",
    )
    registry = RunRegistry(state_home=tmp_path)

    registry.restore_session()

    restored = registry.get("dead-running")
    assert restored["status"] == "running"
    assert restored["alive"] is False
    assert restored["recoverable"] is True
    launched = registry.control("dead-running", "recover")
    assert recovered.wait(1)
    assert launched["alive"] is True
    assert launched["recoverable"] is False
    release.set()
    registry._runs["dead-running"].thread.join(timeout=1)


def _gated_server(registry: RunRegistry, gate, tmp_path: Path):
    handler = type(
        "GatedForgeHandler",
        (ForgeHandler,),
        {
            "registry": registry,
            "gate": gate,
            "request_restart": staticmethod(
                lambda confirm: restart_payload(registry.active_count(), confirm)
            ),
        },
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    return server, thread


def test_access_gate_requires_the_entrance_secret(tmp_path: Path):
    registry = RunRegistry(state_home=tmp_path)
    gate = AccessGate(b"gate-secret")
    server, thread = _gated_server(registry, gate, tmp_path)
    base = f"http://127.0.0.1:{server.server_port}"
    requester = urllib.request.build_opener(urllib.request.HTTPErrorProcessor())

    def fetch(path: str, headers: dict[str, str] | None = None):
        request = urllib.request.Request(base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode()

    try:
        status, _ = fetch("/api/health")
        assert status == 403
        status, body = fetch("/", headers={"X-Forge-Access": "wrong"})
        assert status == 403
        status, _ = fetch("/api/health", headers={"X-Forge-Access": "gate-secret"})
        assert status == 200
        status, _ = fetch("/", headers={"X-Forge-Access": "gate-secret"})
        assert status == 200
        status, body = fetch(
            "/api/preference",
            headers={"X-Forge-Access": "gate-secret"},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_access_gate_post_is_rejected_before_auth(tmp_path: Path):
    registry = RunRegistry(state_home=tmp_path)
    gate = AccessGate(b"gate-secret")
    server, thread = _gated_server(registry, gate, tmp_path)
    base = f"http://127.0.0.1:{server.server_port}"
    request = urllib.request.Request(
        base + "/api/preferences",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        try:
            urllib.request.urlopen(request, timeout=2)
            raise AssertionError("expected 403")
        except urllib.error.HTTPError as error:
            assert error.code == 403
        assert registry.load_preferences()["repo"] == ""
        assert registry._preferences_path().exists() is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_access_gate_helper_rejects_bad_config(tmp_path: Path):
    with pytest.raises(GateMisconfigured):
        read_expected_value("")
    with pytest.raises(GateMisconfigured):
        read_expected_value("relative/path")
    with pytest.raises(GateMisconfigured):
        read_expected_value(str(tmp_path / "missing"))
    loose = tmp_path / "loose"
    loose.write_bytes(b"secret")
    loose.chmod(0o644)
    with pytest.raises(GateMisconfigured):
        read_expected_value(str(loose))
    empty = tmp_path / "empty"
    empty.write_bytes(b"   \n")
    empty.chmod(0o600)
    with pytest.raises(GateMisconfigured):
        read_expected_value(str(empty))
    secret = tmp_path / "secret"
    secret.write_bytes(b"  gate-secret  \n")
    secret.chmod(0o600)
    gate = gate_from_env({"FORGE_UI_PASSWORD_FILE": str(secret)})
    assert gate is not None
    assert gate.allows("gate-secret")
    assert not gate.allows("gate-secretx")
    assert not gate.allows("")
    assert gate is None or AccessGate(b"gate-secret").allows("gate-secret")
