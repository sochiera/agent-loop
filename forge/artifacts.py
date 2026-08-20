"""Durable, atomic run state and append-only measurements."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import AgentResult, ModelSpec, RunState


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


class ArtifactStore:
    def __init__(self, repo: Path, run_id: str):
        self.root = repo / ".forge" / "runs" / run_id
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    def save_state(self, state: RunState) -> None:
        state.updated_at = utc_now()
        with self._lock:
            write_json(self.state_path, state.to_dict())

    def load_state(self) -> RunState:
        return RunState.from_dict(json.loads(self.state_path.read_text(encoding="utf-8")))

    def write_text(self, relative: str, text: str) -> Path:
        path = self.root / relative
        atomic_write(path, text)
        return path

    def write_data(self, relative: str, value: Any) -> Path:
        path = self.root / relative
        write_json(path, value)
        return path

    def append_jsonl(self, relative: str, value: dict[str, Any]) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(value, sort_keys=True) + "\n"
        with self._lock, path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()

    def record_agent_call(
        self,
        *,
        role: str,
        model: ModelSpec,
        result: AgentResult,
        cycle: int,
        candidate: str = "",
        invocation: int = 1,
    ) -> None:
        self.append_jsonl(
            "usage.jsonl",
            {
                "at": utc_now(),
                "cycle": cycle,
                "role": role,
                "candidate": candidate,
                "provider": model.provider,
                "model": model.model,
                "effort": model.effort,
                "invocation": invocation,
                "session_id": result.session_id,
                "elapsed_seconds": round(result.elapsed_seconds, 3),
                "usage": result.usage.to_dict(),
                "return_code": result.return_code,
                "tool_calls": result.tool_calls,
            },
        )

    def event(self, kind: str, message: str, **fields: Any) -> None:
        self.append_jsonl(
            "events.jsonl", {"at": utc_now(), "kind": kind, "message": message, **fields}
        )
