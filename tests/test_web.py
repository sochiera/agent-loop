import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from forge.web import ForgeHandler, RunRegistry


def test_web_control_room_serves_ui_and_api(tmp_path):
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
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
