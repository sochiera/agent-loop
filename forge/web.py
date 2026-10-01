"""Dependency-free local control room for Forge runs."""

from __future__ import annotations

import json
import os
import sys
import threading
import traceback
import urllib.parse
import uuid
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .artifacts import atomic_write
from .access import AccessGate
from .catalog import assign_coder_models, catalog_payload, DEFAULTS
from .gitops import list_branches, repository_summary
from .locking import ExecutionLocked
from .models import (
    CODER_ROLES,
    ModelSpec,
    ROLE_NAMES,
    RunConfig,
    STAFF_ROLES,
)
from .orchestrator import ForgeOrchestrator
from .policy import PromotionSnapshot, load_policy


STATIC = Path(__file__).with_name("static")
MAX_BROWSE_ENTRIES = 1000
MAX_PREVIEW_BYTES = 2_000_000
MAX_CODER_PREFERENCES = 12
PREFERENCE_ROLES = STAFF_ROLES
RECOVERABLE_STATUSES = frozenset({"failed", "cancelled", "running", "paused"})


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def read_last_lines(
    path: Path, limit: int = 200, max_bytes: int = 512 * 1024
) -> list[str]:
    if limit <= 0 or max_bytes <= 0 or not path.is_file():
        return []
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        data = b""
        while (
            position > 0
            and len(data) < max_bytes
            and data.count(b"\n") <= limit
        ):
            size = min(64 * 1024, position, max_bytes - len(data))
            position -= size
            handle.seek(position)
            data = handle.read(size) + data
    if position > 0:
        separator = data.find(b"\n")
        data = data[separator + 1 :] if separator >= 0 else b""
    return [line.decode("utf-8", errors="replace") for line in data.splitlines()[-limit:]]


def recovery_hint_from_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    if not is_recoverable_snapshot(state):
        return {}
    active = state.get("active_iteration")
    active = active if isinstance(active, dict) else {}
    return {
        "kind": "recover",
        "action": active.get("phase")
        or ("product_owner" if state.get("needs_product_owner") else "planning"),
        "cycle": _safe_int(state.get("cycle")),
        "sprint": _safe_int(state.get("sprint_number"), 1),
        "slot": _safe_int(
            active.get("slot"), _safe_int(state.get("sprint_iteration")) + 1
        ),
    }


def is_recoverable_snapshot(state: dict[str, Any]) -> bool:
    status = state.get("status")
    return status in RECOVERABLE_STATUSES or (
        status == "stalled" and state.get("stalled_recoverable") is True
    )


def sanitize_preferences(payload: dict[str, Any]) -> dict[str, Any]:
    models_raw = payload.get("models")
    models: dict[str, str] = {}
    if isinstance(models_raw, dict):
        for role in PREFERENCE_ROLES:
            value = str(models_raw.get(role) or "").strip()
            if value:
                models[role] = value
    coder_models: list[str] = []
    if isinstance(payload.get("coder_models"), list):
        for item in payload["coder_models"]:
            value = str(item).strip()
            if value:
                coder_models.append(value)
    coder_models = coder_models[:MAX_CODER_PREFERENCES]
    # Import the old pool entry (or single coder) when no pool was stored.
    if not coder_models:
        legacy_sources = [str(models_raw.get("coder") or "").strip()] if isinstance(models_raw, dict) else []
        legacy_sources += [str(models_raw.get(role) or "").strip() for role in CODER_ROLES] if isinstance(models_raw, dict) else []
        coder_models = [item for item in legacy_sources if item][:1]
    backup = str(payload.get("backup") or "").strip()
    return {
        "repo": str(payload.get("repo") or ""),
        "branch": str(payload.get("branch") or ""),
        "brief_path": str(payload.get("brief_path") or payload.get("briefPath") or ""),
        "brief": str(payload.get("brief") or payload.get("brief_text") or ""),
        "push": payload.get("push") is not False,
        "models": models,
        "coder_models": coder_models,
        "shared_staff_model": payload.get("shared_staff_model") is True,
        "backup": backup,
    }


def preferences_from_config(config: RunConfig) -> dict[str, Any]:
    return {
        "repo": config.repo,
        "branch": config.branch,
        "brief_path": config.brief,
        "brief": "",
        "push": config.push,
        "models": {
            role: config.models[role].display()
            for role in PREFERENCE_ROLES
            if role in config.models
        },
        "coder_models": [
            config.models[role].display()
            for role in CODER_ROLES
            if role in config.models
        ],
        "shared_staff_model": False,
        "backup": config.backup.display() if config.backup is not None else "",
    }


def browse_filesystem(raw_path: str = "") -> dict[str, Any]:
    requested = (raw_path or "").strip()
    if requested:
        current = Path(requested).expanduser()
        try:
            current = current.resolve()
        except OSError as exc:
            raise ValueError(f"cannot resolve path: {requested}") from exc
    else:
        current = Path.home().resolve()
    if current.is_file():
        current = current.parent
    if not current.exists():
        raise ValueError(f"path does not exist: {current}")
    if not current.is_dir():
        raise ValueError(f"not a directory: {current}")
    try:
        children = list(current.iterdir())
    except OSError as exc:
        raise ValueError(f"cannot read directory: {current}") from exc

    def sort_key(path: Path) -> tuple[bool, str]:
        try:
            return (not path.is_dir(), path.name.casefold())
        except OSError:
            return (True, path.name.casefold())

    entries: list[dict[str, Any]] = []
    truncated = len(children) > MAX_BROWSE_ENTRIES
    for child in sorted(children, key=sort_key)[:MAX_BROWSE_ENTRIES]:
        try:
            is_dir = child.is_dir()
            is_file = child.is_file()
        except OSError:
            continue
        if not is_dir and not is_file:
            continue
        entries.append(
            {
                "name": child.name,
                "path": str(child),
                "kind": "dir" if is_dir else "file",
                "is_repo": is_dir and (child / ".git").exists(),
            }
        )
    parent = current.parent
    return {
        "path": str(current),
        "parent": None if parent == current else str(parent),
        "home": str(Path.home().resolve()),
        "entries": entries,
        "truncated": truncated,
    }


def read_text_file(raw_path: str) -> dict[str, Any]:
    requested = (raw_path or "").strip()
    if not requested:
        raise ValueError("path is required")
    path = Path(requested).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"file does not exist: {path}")
    if path.stat().st_size > MAX_PREVIEW_BYTES:
        raise ValueError(f"file is too large: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"file is not valid UTF-8: {path}") from exc
    return {"path": str(path), "text": text}


def restart_payload(active_runs: int, confirm: bool) -> dict[str, Any]:
    if active_runs and not confirm:
        return {"needs_confirm": True, "active_runs": active_runs}
    return {"restarting": True, "active_runs": active_runs}


def models_from_payload(
    payload: dict[str, Any],
    *,
    policy_snapshot: PromotionSnapshot | None = None,
) -> dict[str, ModelSpec]:
    import random

    snapshot = policy_snapshot or load_policy(str(payload.get("policy_path") or "") or None)
    raw_models = payload.get("models")
    if not isinstance(raw_models, dict):
        raise ValueError("models must be an object")
    overrides: dict[str, ModelSpec] = {}
    for role in STAFF_ROLES:
        value = str(raw_models.get(role) or "").strip()
        if value:
            overrides[role] = ModelSpec.parse(value)
    explicit_coders = [
        role for role in CODER_ROLES if str(raw_models.get(role) or "").strip()
    ]
    if explicit_coders:
        for role in CODER_ROLES:
            value = str(raw_models.get(role) or "").strip()
            if value:
                overrides[role] = ModelSpec.parse(value)
    else:
        pool: list[ModelSpec] = []
        raw_pool = payload.get("coder_models")
        if isinstance(raw_pool, list):
            for item in raw_pool:
                value = str(item).strip()
                if value:
                    pool.append(ModelSpec.parse(value))
        if len(pool) > MAX_CODER_PREFERENCES:
            raise ValueError(
                f"the coder pool accepts at most {MAX_CODER_PREFERENCES} models"
            )
        if pool:
            seed = ",".join(spec.display() for spec in pool)
            weights = {
                f"{spec.provider}:{spec.model}": weight
                for spec, weight in snapshot.coder_options()
            }
            overrides.update(
                assign_coder_models(
                    {role: ModelSpec.parse(DEFAULTS[role]) for role in CODER_ROLES},
                    pool,
                    rng=random.Random(seed),
                    weights=weights,
                )
            )
    rng = random.Random(json.dumps(payload, sort_keys=True, default=str))
    models = snapshot.select_new_run_models(rng, overrides)
    return {role: models[role] for role in ROLE_NAMES}


@dataclass
class LiveRun:
    orchestrator: ForgeOrchestrator
    thread: threading.Thread
    error: str = ""
    starting: bool = False


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
        models = models_from_payload(payload)
        backup_raw = str(payload.get("backup") or "").strip()
        config = RunConfig(
            repo=str(repo),
            brief=str(brief_path),
            branch=str(payload.get("branch") or "main"),
            models=models,
            push=bool(payload.get("push", True)),
            agent_timeout_seconds=int(payload.get("agent_timeout_seconds", 3600)),
            backup=ModelSpec.parse(backup_raw) if backup_raw else None,
            policy_path=str(payload.get("policy_path") or ""),
        )
        orchestrator = ForgeOrchestrator(config, state_home=self.state_home)
        live = LiveRun(orchestrator=orchestrator, thread=threading.Thread())
        with self._lock:
            self._runs[orchestrator.run_id] = live
        self.persist_session()
        self.save_preferences(
            {
                **payload,
                "brief": brief_text or payload.get("brief") or "",
                "brief_path": str(brief_path),
            }
        )
        self._launch(live, recover=False)
        return self._describe(live)

    def _launch_locked(self, live: LiveRun, *, recover: bool) -> None:
        if live.starting or live.thread.is_alive():
            raise ValueError("run is still running")

        def target() -> None:
            try:
                if recover:
                    live.orchestrator.recover_failed()
                else:
                    live.orchestrator.run()
            except Exception:
                live.error = traceback.format_exc()

        live.error = ""
        live.starting = True
        live.thread = threading.Thread(
            target=target,
            name=f"forge-{live.orchestrator.run_id}",
            daemon=True,
        )
        try:
            live.thread.start()
        finally:
            live.starting = False

    def _launch(self, live: LiveRun, *, recover: bool) -> None:
        with self._lock:
            self._launch_locked(live, recover=recover)

    def active_count(self) -> int:
        with self._lock:
            return sum(
                1
                for live in self._runs.values()
                if live.starting or live.thread.is_alive()
            )

    def _session_path(self) -> Path:
        root = self.state_home or Path.home() / ".local/state/forge"
        return Path(root) / "ui-session.json"

    def _preferences_path(self) -> Path:
        root = self.state_home or Path.home() / ".local/state/forge"
        return Path(root) / "ui-preferences.json"

    def save_preferences(self, payload: dict[str, Any]) -> dict[str, Any]:
        cleaned = sanitize_preferences(payload)
        path = self._preferences_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(cleaned, indent=2) + "\n")
        return cleaned

    def load_preferences(self) -> dict[str, Any]:
        stored = self._read_preferences()
        if stored.get("models"):
            return stored
        return self._preferences_from_runs() or stored or sanitize_preferences({})

    def _read_preferences(self) -> dict[str, Any]:
        path = self._preferences_path()
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return sanitize_preferences(value) if isinstance(value, dict) else {}

    def _preferences_from_runs(self) -> dict[str, Any]:
        with self._lock:
            lives = list(self._runs.values())
        if not lives:
            return {}
        latest = max(lives, key=lambda item: str(item.orchestrator.state.updated_at))
        return preferences_from_config(latest.orchestrator.config)

    def persist_session(self) -> None:
        with self._lock:
            items = [
                {
                    "repo": live.orchestrator.config.repo,
                    "run_id": live.orchestrator.run_id,
                }
                for live in self._runs.values()
            ]
        path = self._session_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(items, indent=2) + "\n")

    def restore_session(self) -> None:
        path = self._session_path()
        if not path.is_file():
            return
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            repo = str(item.get("repo") or "")
            run_id = str(item.get("run_id") or "")
            if not repo or not run_id:
                continue
            try:
                self._adopt(Path(repo), run_id)
            except Exception:
                continue

    def _adopt(self, repo: Path, run_id: str) -> None:
        with self._lock:
            if run_id in self._runs:
                return
        orchestrator = ForgeOrchestrator.from_existing(
            repo, run_id, state_home=self.state_home
        )
        live = LiveRun(orchestrator=orchestrator, thread=threading.Thread())
        with self._lock:
            if run_id not in self._runs:
                self._runs[run_id] = live

    def interrupt_live(self) -> None:
        with self._lock:
            lives = list(self._runs.values())
        for live in lives:
            try:
                live.orchestrator.mark_interrupted(
                    "Controller restarted. Use Recover same run to continue."
                )
            except ExecutionLocked:
                # This UI may have adopted a run that another controller owns.
                continue
        self.persist_session()

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            values = list(self._runs.values())
        described = [self._describe(value) for value in values]
        described.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return described

    def get(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            live = self._runs.get(run_id)
        if live is None:
            raise KeyError(run_id)
        return self._describe(live, detailed=True)

    def recover(self, payload: dict[str, Any]) -> dict[str, Any]:
        repo = Path(str(payload["repo"])).expanduser().resolve()
        run_id = str(payload["run_id"])
        orchestrator = ForgeOrchestrator.from_existing(repo, run_id, state_home=self.state_home)
        if payload.get("migrate_models"):
            orchestrator.migrate_models()
        live = LiveRun(orchestrator=orchestrator, thread=threading.Thread())
        with self._lock:
            existing = self._runs.get(run_id)
            if existing is not None and (
                existing.starting or existing.thread.is_alive()
            ):
                raise ValueError("run is still running")
            self._runs[run_id] = live
            self._launch_locked(live, recover=True)
        self.persist_session()
        return self._describe(live)

    def recover_live(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            live = self._runs.get(run_id)
            if live is None:
                raise KeyError(run_id)
            self._launch_locked(live, recover=True)
        return self._describe(live)

    def control(self, run_id: str, action: str) -> dict[str, Any]:
        if action == "recover":
            with self._lock:
                live = self._runs.get(run_id)
                if live is None:
                    raise KeyError(run_id)
                if live.starting or live.thread.is_alive():
                    raise ValueError("run process is still active")
                if not is_recoverable_snapshot(live.orchestrator.state.to_dict()):
                    raise ValueError("only a failed or interrupted run can be recovered")
                self._launch_locked(live, recover=True)
            return self._describe(live)
        with self._lock:
            live = self._runs.get(run_id)
        if live is None:
            raise KeyError(run_id)
        {"pause": live.orchestrator.pause, "resume": live.orchestrator.resume, "cancel": live.orchestrator.cancel}[action]()
        return self._describe(live)

    @staticmethod
    def _describe(live: LiveRun, detailed: bool = False) -> dict[str, Any]:
        alive = live.starting or live.thread.is_alive()
        state = live.orchestrator.state
        if not alive:
            try:
                state = live.orchestrator.store.load_state()
            except Exception:
                pass
        snapshot = getattr(live.orchestrator, "state_snapshot", None)
        value = snapshot() if alive and callable(snapshot) else state.to_dict()
        value["alive"] = alive
        value["recoverable"] = not alive and is_recoverable_snapshot(value)
        value["artifact_dir"] = str(live.orchestrator.store.root)
        value["recovery"] = recovery_hint_from_snapshot(value)
        if live.error:
            value["error"] = live.error
        if detailed:
            value["active_agents"] = live.orchestrator.activity_snapshot()
            for name in ("events.jsonl", "usage.jsonl"):
                path = live.orchestrator.store.root / name
                value[name.removesuffix(".jsonl")] = read_last_lines(path)
        return value


class ForgeHandler(BaseHTTPRequestHandler):
    registry: RunRegistry
    request_restart: Callable[[bool], dict[str, Any]] | None = None
    gate: AccessGate | None = None

    def _gate_allowed(self) -> bool:
        gate = type(self).gate
        if gate is None:
            return True
        return gate.allows(self.headers.get("X-Forge-Access"))

    def _reject_gate(self, detail: str, status: HTTPStatus) -> None:
        self._json({"error": detail}, status)

    def do_GET(self) -> None:  # noqa: N802
        if not self._gate_allowed():
            return self._reject_gate("access gate: not authorized", HTTPStatus.FORBIDDEN)
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/health":
            return self._json({"ok": True, "active_runs": self.registry.active_count()})
        if parsed.path == "/api/runs":
            return self._json(self.registry.list())
        if parsed.path.startswith("/api/runs/"):
            run_id = parsed.path.split("/")[3]
            try:
                return self._json(self.registry.get(run_id))
            except KeyError:
                return self._json({"error": "run not found"}, HTTPStatus.NOT_FOUND)
        if parsed.path == "/api/catalog":
            return self._json(catalog_payload(load_policy()))
        if parsed.path == "/api/preferences":
            return self._json(self.registry.load_preferences())
        if parsed.path == "/api/browse":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                return self._json(browse_filesystem(query.get("path", [""])[0]))
            except Exception as exc:
                return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        if parsed.path == "/api/file":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                return self._json(read_text_file(query.get("path", [""])[0]))
            except Exception as exc:
                return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
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
        if not self._gate_allowed():
            return self._reject_gate("access gate: not authorized", HTTPStatus.FORBIDDEN)
        try:
            payload = self._body()
            if self.path == "/api/restart":
                if self.request_restart is None:
                    return self._json({"error": "restart is unavailable"}, HTTPStatus.BAD_REQUEST)
                return self._json(self.request_restart(bool(payload.get("confirm"))))
            if self.path == "/api/preferences":
                return self._json(self.registry.save_preferences(payload))
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


def serve(
    host: str = "127.0.0.1",
    port: int = 8787,
    *,
    open_browser: bool = True,
    gate: AccessGate | None = None,
) -> None:
    registry = RunRegistry()
    registry.restore_session()
    ctl: dict[str, Any] = {"server": None, "pending": False}

    def request_restart(confirm: bool) -> dict[str, Any]:
        result = restart_payload(registry.active_count(), confirm)
        if result.get("restarting"):
            registry.interrupt_live()
            ctl["pending"] = True
            server = ctl["server"]
            if server is not None:
                threading.Thread(target=server.shutdown, daemon=True).start()
        return result

    handler = type(
        "BoundForgeHandler",
        (ForgeHandler,),
        {
            "registry": registry,
            "request_restart": staticmethod(request_restart),
            "gate": gate,
        },
    )
    server = ThreadingHTTPServer((host, port), handler)
    ctl["server"] = server
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
    if ctl["pending"]:
        os.execv(
            sys.executable,
            [
                sys.executable,
                "-m",
                "forge",
                "ui",
                "--host",
                host,
                "--port",
                str(server.server_port),
                "--no-browser",
            ],
        )
