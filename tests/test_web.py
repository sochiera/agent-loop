import json
import subprocess
import threading
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

from forge.web import ForgeHandler, RunRegistry


def test_web_control_room_serves_ui_and_api(tmp_path):
    repo = tmp_path / "empty-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, stdout=subprocess.PIPE)
    (repo / "goal.md").write_text("# Build it\n", encoding="utf-8")
    registry = RunRegistry(state_home=tmp_path)
    handler = type("TestForgeHandler", (ForgeHandler,), {"registry": registry})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        html = urllib.request.urlopen(base + "/", timeout=2).read().decode()
        assert "Forge Control Room" in html
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
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
