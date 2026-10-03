import base64
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
            "gpt-6-sol",
            "gpt-6-luna",
            "claude-opus-5-5",
            "glm-5.3-flash",
            "deepseek-v4.1-flash",
            "mimo-v2.6-flash",
        }
        assert by_key["deepseek-v4.1-flash"]["ids"]["opencode"] == "opencode-go/deepseek-v4.1-flash"
        assert by_key["mimo-v2.6-flash"]["ids"]["opencode"] == "opencode-go/mimo-v2.6-flash"
        assert [key for key, item in by_key.items() if item["coder_only"]] == [
            "deepseek-v4.1-flash",
            "mimo-v2.6-flash",
        ]
        assert by_key["gpt-6-sol"]["providers"] == ["codex"]
        assert by_key["gpt-6-sol"]["efforts"] == ["medium"]
        assert by_key["gpt-6-luna"]["providers"] == ["codex"]
        assert by_key["gpt-6-luna"]["efforts"] == ["xhigh"]
        assert by_key["claude-opus-5-5"]["providers"] == ["claude"]
        assert by_key["claude-opus-5-5"]["ids"]["claude"] == "claude-opus-5-5"
        assert by_key["glm-5.3-flash"]["family"] == "glm"
        assert by_key["glm-5.3-flash"]["ids"]["opencode"] == "opencode-go/glm-5.3-flash"
        assert by_key["glm-5.3-flash"]["efforts"] == ["xhigh"]
        for retired in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna",
                        "grok-4.6", "kimi-k3"):
            assert retired not in by_key
        assert catalog["policy"]["allowed_models"] == [
            "codex:gpt-6-sol:medium",
            "codex:gpt-6-luna:xhigh",
            "claude:claude-opus-5-5:medium",
            "opencode:opencode-go/glm-5.3-flash:xhigh",
        ]
        assert catalog["policy"]["policy_ids"] == [
            "openai-codex/gpt-6-sol",
            "openai-codex/gpt-6-luna",
            "claude-code/claude-opus-5-5",
            "opencode-go/glm-5.3-flash",
        ]
        assert catalog["policy"]["cheap_pool"] == [
            "opencode:opencode-go/glm-5.3-flash:xhigh",
            *["codex:gpt-6-luna:xhigh"] * 3,
        ]
        # The cheap pool is the swarm's; the tournament UI never offers it.
        assert "opencode:opencode-go/deepseek-v4.1-flash:xhigh" not in script
        assert "opencode:opencode-go/mimo-v2.6-flash:xhigh" not in script
        assert "!entry.coder_only" in script
        assert catalog["defaults"]["coder_tdd"] == "codex:gpt-6-luna:xhigh"
        assert catalog["defaults"]["coder_explore"] == "codex:gpt-6-luna:xhigh"
        assert catalog["defaults"]["coder_classic"] == "codex:gpt-6-luna:xhigh"
        assert catalog["defaults"]["test_author"] == "codex:gpt-6-luna:xhigh"
        assert catalog["defaults"]["reviewer"] == "codex:gpt-6-sol:medium"
        assert catalog["policy"]["promotion_state"] == "unknown"
        assert "model-effort" in html
        assert 'class="model-effort" required' not in html
        assert "Coder draw" in urllib.request.urlopen(base + "/app.js", timeout=2).read().decode()
        assert "opencode" in catalog["providers"]
        assert catalog["providers"] == ["codex", "claude", "opencode"]
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
        assert health == {"ok": True, "active_runs": 0, "external_active_runs": 0}
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
            role: "codex:gpt-6-sol:medium" for role in STAFF_ROLES
        },
        "coder_models": ["opencode:glm-5.3-flash"],
    }
    created = registry.start(payload)
    models = created["config"]["models"]
    for role in CODER_ROLES:
        assert models[role]["model"] == "opencode-go/glm-5.3-flash"
    assert models["brain"]["model"] == "gpt-6-sol"
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
                role: "codex:gpt-6-sol:medium" for role in STAFF_ROLES
            },
            "coder_models": ["opencode:glm-5.3-flash"],
        }
    )
    (tmp_path / "ui-preferences.json").unlink()
    fallback = registry.load_preferences()
    assert fallback["models"]["brain"] == "codex:gpt-6-sol:medium"
    # Tournament runs keep no cheap pool; the fallback restores the drawn coders.
    assert fallback["coder_models"] == ["opencode:opencode-go/glm-5.3-flash:xhigh"] * 3


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
        assert status == 401
        status, body = fetch("/", headers={"X-Forge-Access": "wrong"})
        assert status == 401
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


def test_access_gate_accepts_browser_basic_credentials(tmp_path: Path):
    registry = RunRegistry(state_home=tmp_path)
    gate = AccessGate(b"gate-secret")
    server, thread = _gated_server(registry, gate, tmp_path)
    base = f"http://127.0.0.1:{server.server_port}"

    authorized = "Basic " + base64.b64encode(b"jan:gate-secret").decode()

    def request(path: str, auth: str | None = None, method: str = "GET", data=None):
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = auth
        return urllib.request.Request(
            base + path, data=data, headers=headers, method=method
        )

    def open_request(request):
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, response.read().decode(), response.headers
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode(), error.headers

    try:
        status, body, headers = open_request(request("/api/health"))
        assert status == 401
        assert headers["WWW-Authenticate"] == (
            'Basic realm="Forge Control Room", charset="UTF-8"'
        )
        assert "access gate: not authorized" in body
        status, _, _ = open_request(request("/api/health", "Basic !!!not-b64!!!"))
        assert status == 401
        wrong = "Basic " + base64.b64encode(b"jan:wrong-password").decode()
        status, _, _ = open_request(request("/api/health", wrong))
        assert status == 401
        status, body, _ = open_request(request("/", authorized))
        assert status == 200
        assert "Forge Control Room" in body
        status, health, _ = open_request(request("/api/health", authorized))
        assert status == 200
        assert json.loads(health)["ok"] is True
        saved, saved_body, _ = open_request(
            request(
                "/api/preferences",
                authorized,
                method="POST",
                data=json.dumps({"push": False}).encode(),
            )
        )
        assert saved == 200
        assert saved_body is not None
        assert json.loads(saved_body)["push"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_access_gate_still_backs_the_header_channel():
    gate = AccessGate(b"gate-secret")
    assert gate.allows("gate-secret") is True
    assert gate.allows("wrong") is False
    assert gate.allows("") is False

    encoded = base64.b64encode(b"whatever-user:gate-secret").decode()
    assert gate.allows_basic_auth(f"Basic {encoded}") is True
    assert gate.allows_basic_auth(f"basic {encoded}") is True
    assert gate.allows_basic_auth(f"Basic {base64.b64encode(b'u:nope').decode()}") is False
    assert gate.allows_basic_auth(f"Bearer {encoded}") is False
    assert gate.allows_basic_auth("") is False
    assert gate.allows_basic_auth(None) is False
    assert gate.allows_basic_auth("Basic !!!broken") is False
    assert gate.allows_basic_auth("Basic") is False


def test_relative_ui_paths_keep_working_behind_a_prefix_proxy(tmp_path: Path):
    """The /forge/ proxy strips its prefix, so UI targets must not be root-absolute."""

    registry = RunRegistry(state_home=tmp_path)
    gate = AccessGate(b"gate-secret")
    server, thread = _gated_server(registry, gate, tmp_path)
    base = f"http://127.0.0.1:{server.server_port}"
    authorized = "Basic " + base64.b64encode(b"jan:gate-secret").decode()

    def fetch(path: str, headers: dict[str, str] | None = None):
        request = urllib.request.Request(base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode()

    try:
        status, html = fetch("/", headers={"X-Forge-Access": "gate-secret"})
        assert status == 200
        assert 'href="/style.css"' not in html
        assert 'src="/app.js"' not in html
        assert 'href="style.css"' in html
        assert 'src="app.js"' in html

        status, script = fetch("/app.js", headers={"X-Forge-Access": "gate-secret"})
        assert status == 200
        assert 'fetch("/api' not in script and "fetch(`/api" not in script
        assert 'path.startsWith("/") ? path.slice(1) : path' in script
        status, health = fetch("/api/health", headers={"X-Forge-Access": "gate-secret"})
        assert json.loads(health)["ok"] is True
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
            raise AssertionError("expected an unauthorized response")
        except urllib.error.HTTPError as error:
            assert error.code == 401
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
