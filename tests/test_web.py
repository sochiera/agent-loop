import json
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from pathlib import Path

from forge.models import ROLE_NAMES, RunState
from forge.web import (
    ForgeHandler,
    RunRegistry,
    browse_filesystem,
    read_text_file,
    restart_payload,
    sanitize_preferences,
)


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
        assert 'id="restart"' in html
        assert 'id="browse-repo"' in html
        assert 'id="browse-brief"' in html
        assert 'id="fs-explorer"' in html
        script = urllib.request.urlopen(base + "/app.js", timeout=2).read().decode()
        assert "/api/catalog" in script
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
        assert "gpt-5.6-sol" in by_key
        assert by_key["deepseek-v4-flash-0731"]["family"] == "deepseek"
        assert by_key["deepseek-v4-pro-0813"]["family"] == "deepseek"
        assert by_key["or-deepseek-v4-flash-0731"]["ids"]["opencode"] == (
            "openrouter/deepseek/deepseek-v4-flash-0731"
        )
        assert "or-deepseek-v3.2" not in by_key
        assert by_key["or-gemini-3.7-flash"]["ids"]["opencode"] == (
            "openrouter/google/gemini-3.7-flash"
        )
        assert catalog["defaults"]["coder_tdd"] == "codex:gpt-5.6-luna:high"
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
                            "coder_models": ["opencode:glm-5.3:high"],
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
        assert loaded_prefs["coder_models"] == ["opencode:glm-5.3:high"]
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


def test_post_runs_assigns_coder_models(tmp_path, monkeypatch):
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
        "models": {role: "codex:gpt-5.6-sol:high" for role in ROLE_NAMES},
        "coder_models": ["opencode:grok-4.6"],
    }
    created = registry.start(payload)
    models = created["config"]["models"]
    assert models["coder_tdd"]["model"] == "xai/grok-4.6"
    assert models["coder_explore"]["model"] == "xai/grok-4.6"
    assert models["coder_classic"]["model"] == "xai/grok-4.6"
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
        assert posted["config"]["models"]["coder_tdd"]["model"] == "xai/grok-4.6"
        bad = urllib.request.Request(
            base + "/api/runs",
            data=json.dumps({**payload, "coder_models": []}).encode(),
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
            "coder_models": ["opencode:glm-5.3:high", "", "codex:gpt-5.6-luna:high"],
            "push": False,
            "ignore": True,
        }
    ) == {
        "repo": "/tmp/repo",
        "branch": "",
        "brief_path": "/tmp/goal.md",
        "brief": "",
        "push": False,
        "models": {"brain": "opencode:grok-4.6:high"},
        "coder_models": ["opencode:glm-5.3:high", "codex:gpt-5.6-luna:high"],
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
            "models": {"brain": "opencode:grok-4.6:high"},
            "coder_models": ["opencode:glm-5.3:high"],
        }
    )
    assert saved["models"]["brain"] == "opencode:grok-4.6:high"
    assert registry.load_preferences()["coder_models"] == ["opencode:glm-5.3:high"]

    registry.start(
        {
            "repo": str(repo),
            "brief_path": str(repo / "goal.md"),
            "push": False,
            "models": {role: "codex:gpt-5.6-sol:high" for role in ROLE_NAMES},
            "coder_models": ["opencode:grok-4.6"],
        }
    )
    (tmp_path / "ui-preferences.json").unlink()
    fallback = registry.load_preferences()
    assert fallback["models"]["brain"] == "codex:gpt-5.6-sol:high"
    assert fallback["coder_models"] == [
        "opencode:xai/grok-4.6",
        "opencode:xai/grok-4.6",
        "opencode:xai/grok-4.6",
    ]
