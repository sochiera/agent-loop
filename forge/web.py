"""Dependency-free local control room for Forge runs."""

from __future__ import annotations

import json
import threading
import traceback
import urllib.parse
import uuid
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .artifacts import atomic_write
from .catalog import catalog_payload, DEFAULTS
from .gitops import list_branches, repository_summary
from .models import ModelSpec, ROLE_NAMES, RunConfig
from .orchestrator import ForgeOrchestrator


STATIC = Path(__file__).with_name("static")


@dataclass
class LiveRun:
    orchestrator: ForgeOrchestrator
    thread: threading.Thread
    error: str = ""


class RunRegistry:
    def __init__(self, state_home: Path | None = None):
        self.state_home = state_home
        self._runs: dict[str, LiveRun] = {}
        self._lock = threading.Lock()

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        repo = Path(str(payload["repo"])).expanduser().resolve()
        brief_path_value = str(payload.get("brief_path") or "").strip()
        brief_text = str(payload.get("brief_text") or "").strip()
        if brief_path_value:
            brief_path = Path(brief_path_value).expanduser().resolve()
        elif brief_text:
            input_root = (self.state_home or Path.home() / ".local/state/forge") / "inputs"
            input_root.mkdir(parents=True, exist_ok=True)
            brief_path = input_root / f"brief-{uuid.uuid4().hex}.md"
            atomic_write(brief_path, brief_text + "\n")
        else:
            raise ValueError("brief_path or brief_text is required")
        raw_models = payload.get("models")
        if not isinstance(raw_models, dict):
            raise ValueError("models must be an object")
        models = {
            role: ModelSpec.parse(str(raw_models.get(role) or DEFAULTS[role]))
            for role in ROLE_NAMES
        }
        config = RunConfig(
            repo=str(repo),
            brief=str(brief_path),
            branch=str(payload.get("branch") or "main"),
            models=models,
            push=bool(payload.get("push", True)),
            agent_timeout_seconds=int(payload.get("agent_timeout_seconds", 3600)),
            shuffle_coders=bool(payload.get("shuffle_coders", False)),
        )
        orchestrator = ForgeOrchestrator(config, state_home=self.state_home)
        live = LiveRun(orchestrator=orchestrator, thread=threading.Thread())
        with self._lock:
            self._runs[orchestrator.run_id] = live
        self._launch(live, recover=False)
        return orchestrator.state.to_dict()

    @staticmethod
    def _launch(live: LiveRun, *, recover: bool) -> None:
        def target() -> None:
            try:
                if recover:
                    live.orchestrator.recover_failed()
                else:
                    live.orchestrator.run()
            except Exception:
                live.error = traceback.format_exc()

        live.error = ""
        live.thread = threading.Thread(
            target=target,
            name=f"forge-{live.orchestrator.run_id}",
            daemon=True,
        )
        live.thread.start()

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            values = list(self._runs.values())
        return [self._describe(value) for value in values]

    def get(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            live = self._runs.get(run_id)
        if live is None:
            raise KeyError(run_id)
        return self._describe(live, detailed=True)

    def recover(self, payload: dict[str, Any]) -> dict[str, Any]:
        repo = Path(str(payload["repo"])).expanduser().resolve()
        run_id = str(payload["run_id"])
        with self._lock:
            existing = self._runs.get(run_id)
        if existing is not None and existing.thread.is_alive():
            raise ValueError("run is still running")
        if existing is not None and existing.orchestrator.state.status == "complete":
            raise ValueError("run is already complete")
        orchestrator = ForgeOrchestrator.from_existing(repo, run_id, state_home=self.state_home)
        if orchestrator.state.status == "complete":
            raise ValueError("run is already complete")
        live = LiveRun(orchestrator=orchestrator, thread=threading.Thread())

        def target() -> None:
            try:
                orchestrator.recover()
            except Exception:
                live.error = traceback.format_exc()

        live.thread = threading.Thread(
            target=target, name=f"forge-recover-{run_id}", daemon=True
        )
        with self._lock:
            self._runs[run_id] = live
        live.thread.start()
        return orchestrator.state.to_dict()

    def recover_live(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            live = self._runs.get(run_id)
        if live is None:
            raise KeyError(run_id)
        if live.thread.is_alive():
            raise ValueError("run is still running")
        live.error = ""

        def target() -> None:
            try:
                live.orchestrator.recover()
            except Exception:
                live.error = traceback.format_exc()

        live.thread = threading.Thread(
            target=target, name=f"forge-recover-{run_id}", daemon=True
        )
        live.thread.start()
        return self._describe(live)

    def control(self, run_id: str, action: str) -> dict[str, Any]:
        with self._lock:
            live = self._runs.get(run_id)
        if live is None:
            raise KeyError(run_id)
        if action == "recover":
            if live.thread.is_alive():
                raise ValueError("run process is still active")
            if live.orchestrator.state.status not in {"failed", "cancelled", "running"}:
                raise ValueError("only a failed or interrupted run can be recovered")
            self._launch(live, recover=True)
            return self._describe(live)
        {"pause": live.orchestrator.pause, "resume": live.orchestrator.resume, "cancel": live.orchestrator.cancel}[action]()
        return self._describe(live)

    @staticmethod
    def _describe(live: LiveRun, detailed: bool = False) -> dict[str, Any]:
        value = live.orchestrator.state.to_dict()
        value["alive"] = live.thread.is_alive()
        value["artifact_dir"] = str(live.orchestrator.store.root)
        if live.error:
            value["error"] = live.error
        if detailed:
            value["active_agents"] = live.orchestrator.activity_snapshot()
            for name in ("events.jsonl", "usage.jsonl"):
                path = live.orchestrator.store.root / name
                value[name.removesuffix(".jsonl")] = (
                    path.read_text(encoding="utf-8").splitlines()[-200:] if path.exists() else []
                )
        return value


class ForgeHandler(BaseHTTPRequestHandler):
    registry: RunRegistry

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/runs":
            return self._json(self.registry.list())
        if parsed.path.startswith("/api/runs/"):
            run_id = parsed.path.split("/")[3]
            try:
                return self._json(self.registry.get(run_id))
            except KeyError:
                return self._json({"error": "run not found"}, HTTPStatus.NOT_FOUND)
        if parsed.path == "/api/catalog":
            return self._json(catalog_payload())
        if parsed.path == "/api/branches":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                repo = Path(query.get("repo", [""])[0]).expanduser().resolve()
                return self._json({"branches": list_branches(repo)})
            except Exception as exc:
                return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        if parsed.path == "/api/repository":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                repo = Path(query.get("repo", [""])[0]).expanduser().resolve()
                value = repository_summary(repo)
                for filename in ("goal.md", "brief.md"):
                    brief = repo / filename
                    if brief.is_file() and brief.stat().st_size <= 2_000_000:
                        value["brief_path"] = str(brief)
                        value["brief_text"] = brief.read_text(encoding="utf-8")
                        break
                return self._json(value)
            except Exception as exc:
                return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        return self._static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        try:
            payload = self._body()
            if self.path == "/api/runs":
                return self._json(self.registry.start(payload), HTTPStatus.CREATED)
            if self.path == "/api/runs/recover":
                return self._json(self.registry.recover(payload), HTTPStatus.CREATED)
            parts = self.path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "recover":
                return self._json(self.registry.recover_live(parts[2]))
            if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] in {
                "pause",
                "resume",
                "cancel",
                "recover",
            }:
                return self._json(self.registry.control(parts[2], parts[3]))
            return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except KeyError as exc:
            return self._json({"error": f"missing field: {exc}"}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 2_000_000:
            raise ValueError("request body is too large")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def _static(self, path: str) -> None:
        names = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
        name = names.get(path)
        if name is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        file = STATIC / name
        content = file.read_bytes()
        mime = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}[file.suffix]
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{mime}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = json.dumps(value, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve(host: str = "127.0.0.1", port: int = 8787, *, open_browser: bool = True) -> None:
    registry = RunRegistry()
    handler = type("BoundForgeHandler", (ForgeHandler,), {"registry": registry})
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{server.server_port}"
    print(f"Forge UI: {url}", flush=True)
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
