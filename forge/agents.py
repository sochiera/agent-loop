"""Headless CLI adapters with one normalized result and usage format."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .artifacts import atomic_write
from .models import AgentResult, ModelSpec, Usage
from .policy import PromotionSnapshot, load_policy
from .validation import _signal_session


class AgentFailure(RuntimeError):
    def __init__(self, message: str, *, raw_output: str = ""):
        super().__init__(message)
        self.raw_output = raw_output


class AgentTimeout(AgentFailure):
    pass


class AgentConfigurationFailure(AgentFailure):
    """A deterministic CLI/configuration error that retrying cannot fix."""


class AgentUsageLimit(AgentConfigurationFailure):
    """Provider quota/usage limit; retrying the same model will not help."""


class AgentPolicyRefused(AgentConfigurationFailure):
    """The harness wrapper's central policy gate refused the model (exit 78)."""


class AgentCancelled(AgentFailure):
    """Operator cancelled the run while this process was still working."""


# EX_CONFIG: the Hermes harness wrappers exit 78 when the central model policy
# refuses the model (not allowlisted, wrong harness, or quota-blocked).
POLICY_REFUSED_EXIT = 78


_USAGE_LIMIT_MARKERS = (
    "you've hit your usage limit",
    "you've reached your usage limit",
    "purchase more credits",
    "insufficient_quota",
    "quota exceeded",
    "quota will be refreshed",
    "billing cycle",
    "usage limit",
)

_NON_RETRYABLE_ERRORS = (
    "error loading config.toml",
    "unexpected argument",
    "unrecognized option",
    "unknown option",
    "invalid value",
    "invalid_json_schema",
    "invalid schema for response_format",
    "not inside a trusted directory",
    *_USAGE_LIMIT_MARKERS,
)


def is_usage_limit(message: str, raw_output: str = "") -> bool:
    text = f"{message}\n{raw_output}".lower()
    return any(marker in text for marker in _USAGE_LIMIT_MARKERS)


def failure_type_for(raw: str) -> type[AgentFailure]:
    lowered = raw.lower()
    if any(marker in lowered for marker in _USAGE_LIMIT_MARKERS):
        return AgentUsageLimit
    if any(marker in lowered for marker in _NON_RETRYABLE_ERRORS):
        return AgentConfigurationFailure
    return AgentFailure

_CODEX_LEAN_FEATURES = (
    "apps",
    "browser_use",
    "computer_use",
    "image_generation",
    "in_app_browser",
    "multi_agent",
    "plugins",
    "remote_plugin",
)


@dataclass
class AgentRequest:
    role: str
    model: ModelSpec
    prompt: str
    cwd: Path
    session_id: str | None = None
    access: str = "write"  # none, read, inspect, or write
    schema: dict[str, Any] | None = None
    schema_dir: Path | None = None
    extra_writable_dirs: tuple[Path, ...] = ()
    timeout_seconds: int = 3600
    environment: dict[str, str] = field(default_factory=dict)

    @property
    def allow_tools(self) -> bool:
        return self.access != "none"


def _json_lines(raw: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for line in raw.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            values.append(value)
    return values


def _session_id(
    events: list[dict[str, Any]], requested_session_id: str | None = None
) -> str | None:
    if requested_session_id:
        return requested_session_id
    keys = ("thread_id", "threadId", "session_id", "sessionId", "sessionID")
    for event in events:
        for key in keys:
            value = event.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _normalized_usage(value: dict[str, Any], *, cost: Any = None) -> Usage:
    cache = value.get("cache_read_input_tokens", value.get("cached_input_tokens", 0))
    if isinstance(value.get("cache"), dict):
        cache = value["cache"].get("read", cache)
    return Usage(
        input_tokens=_int(value.get("input_tokens", value.get("input", 0))),
        cached_input_tokens=_int(cache),
        output_tokens=_int(value.get("output_tokens", value.get("output", 0))),
        reasoning_tokens=_int(
            value.get("reasoning_output_tokens", value.get("reasoning_tokens", value.get("reasoning", 0)))
        ),
        cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
    )


def _codex_parse(events: list[dict[str, Any]]) -> tuple[str, Usage, int]:
    text = ""
    usage = Usage()
    tool_calls = 0
    for event in events:
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            usage = _normalized_usage(event["usage"])
        item = event.get("item")
        if isinstance(item, dict):
            item_type = item.get("type")
            if item_type in {"agent_message", "message"} and isinstance(item.get("text"), str):
                text = item["text"]
            elif item_type not in {None, "reasoning", "agent_message", "message"} and event.get("type") in {
                "item.started",
                "item.completed",
            }:
                if event.get("type") == "item.started":
                    tool_calls += 1
    return text, usage, tool_calls


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _claude_parse(events: list[dict[str, Any]]) -> tuple[str, Usage, int]:
    text = ""
    usage = Usage()
    tool_ids: set[str] = set()
    for event in events:
        if event.get("type") == "result":
            structured = event.get("structured_output")
            if isinstance(structured, dict):
                text = json.dumps(structured)
            elif isinstance(event.get("result"), str):
                text = event["result"]
            if isinstance(event.get("usage"), dict):
                usage = _normalized_usage(event["usage"], cost=event.get("total_cost_usd"))
        for item in _walk(event):
            if isinstance(item, dict) and item.get("type") == "tool_use":
                tool_ids.add(str(item.get("id") or id(item)))
    return text, usage, len(tool_ids)


def _opencode_parse(events: list[dict[str, Any]]) -> tuple[str, Usage, int]:
    texts: list[str] = []
    usage = Usage()
    tool_ids: set[str] = set()
    for event in events:
        part = event.get("part")
        if isinstance(part, dict):
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
            if part.get("type") in {"tool", "tool_use"}:
                tool_ids.add(str(part.get("id") or part.get("callID") or id(part)))
            tokens = part.get("tokens")
            if isinstance(tokens, dict):
                current = _normalized_usage(tokens, cost=part.get("cost"))
                usage.input_tokens += current.input_tokens
                usage.cached_input_tokens += current.cached_input_tokens
                usage.output_tokens += current.output_tokens
                usage.reasoning_tokens += current.reasoning_tokens
                if current.cost_usd is not None:
                    usage.cost_usd = (usage.cost_usd or 0.0) + current.cost_usd
        if event.get("type") in {"text", "message"} and isinstance(event.get("text"), str):
            texts.append(event["text"])
    return (texts[-1] if texts else ""), usage, len(tool_ids)


class AgentRunner:
    """Invoke Codex, Claude Code, or OpenCode without a shell.

    The runner is the final execution boundary. It refuses any model the active
    policy does not allow, so a direct ``AgentRunner`` call cannot bypass the
    catalog and promotion gate even when ``RunConfig``/CLI/UI validation is
    skipped. Pass an explicit ``policy`` for hermetic tests; otherwise the
    current policy is loaded fail-closed on every call.
    """

    def __init__(
        self,
        *,
        policy: PromotionSnapshot | None = None,
        policy_path: str | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._processes: list[subprocess.Popen[str]] = []
        self._cancelled = threading.Event()
        self._policy = policy
        self._policy_path = policy_path

    def _current_policy(self) -> PromotionSnapshot:
        if self._policy is not None:
            return self._policy
        return load_policy(self._policy_path)

    def _ensure_policy(self, request: AgentRequest) -> None:
        snapshot = self._current_policy()
        if not snapshot.allows(request.model, request.role):
            raise AgentConfigurationFailure(
                f"{request.role} model {request.model.display()} is not allowed by "
                f"the active model policy (promotion_state={snapshot.state})"
            )
        request.model = snapshot.pin(request.model)

    def cancel(self) -> None:
        with self._lock:
            self._cancelled.set()
            processes = list(self._processes)
        for process in processes:
            if process.poll() is None:
                _signal_session(process.pid, signal.SIGTERM)
        deadline = time.monotonic() + 2
        while any(process.poll() is None for process in processes):
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        for process in processes:
            # The session can still contain grandchildren after its leader exits.
            _signal_session(process.pid, signal.SIGKILL)
        for process in processes:
            if process.poll() is None:
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass

    def allow(self) -> None:
        with self._lock:
            self._cancelled.clear()

    def run(self, request: AgentRequest) -> AgentResult:
        if self._cancelled.is_set():
            raise AgentCancelled(f"{request.role} cancelled")
        if request.access not in {"none", "read", "inspect", "write", "test"}:
            raise ValueError(f"unsupported agent access profile: {request.access}")
        self._ensure_policy(request)
        request.cwd.mkdir(parents=True, exist_ok=True)
        command = self._command(request)
        environment = os.environ.copy()
        environment.update(request.environment)
        started = time.monotonic()
        with self._lock:
            if self._cancelled.is_set():
                raise AgentCancelled(f"{request.role} cancelled")
            process = subprocess.Popen(
                command,
                cwd=request.cwd,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            self._processes.append(process)
        try:
            raw, _ = process.communicate(request.prompt, timeout=request.timeout_seconds)
        except subprocess.TimeoutExpired:
            _signal_session(process.pid, signal.SIGTERM)
            try:
                raw, _ = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                _signal_session(process.pid, signal.SIGKILL)
                raw, _ = process.communicate()
            raise AgentTimeout(
                f"{request.role} timed out after {request.timeout_seconds}s", raw_output=raw
            )
        except BaseException:
            # KeyboardInterrupt and process-level shutdown must not orphan a
            # model CLI or any tool subprocess group that it created.
            _signal_session(process.pid, signal.SIGTERM)
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                _signal_session(process.pid, signal.SIGKILL)
                process.communicate()
            raise
        finally:
            with self._lock:
                if process in self._processes:
                    self._processes.remove(process)
        if self._cancelled.is_set():
            raise AgentCancelled(f"{request.role} cancelled", raw_output=raw)
        elapsed = time.monotonic() - started
        if process.returncode == POLICY_REFUSED_EXIT:
            raise AgentPolicyRefused(
                f"{request.role} model {request.model.display()} was refused by the "
                f"central model policy (exit {POLICY_REFUSED_EXIT})",
                raw_output=raw,
            )
        if process.returncode != 0:
            raise failure_type_for(raw)(
                f"{request.role} exited with code {process.returncode}", raw_output=raw
            )
        events = _json_lines(raw)
        parser = {
            "codex": _codex_parse,
            "claude": _claude_parse,
            "opencode": _opencode_parse,
        }[request.model.provider]
        text, usage, tool_calls = parser(events)
        session_id = _session_id(events, request.session_id)
        if not text.strip():
            # A custom wrapper or future CLI version may print plain text.
            non_json = [line for line in raw.splitlines() if not line.lstrip().startswith("{")]
            text = "\n".join(non_json).strip()
        if not text.strip():
            raise AgentFailure(f"{request.role} returned no final response", raw_output=raw)
        return AgentResult(
            text=text,
            session_id=session_id,
            usage=usage,
            elapsed_seconds=elapsed,
            raw_output=raw,
            tool_calls=tool_calls,
            return_code=process.returncode,
        )

    def _command(self, request: AgentRequest) -> list[str]:
        self._ensure_policy(request)
        if request.model.provider == "codex":
            return self._codex_command(request)
        if request.model.provider == "claude":
            return self._claude_command(request)
        if request.model.provider == "opencode":
            return self._opencode_command(request)
        raise ValueError(f"unsupported provider: {request.model.provider}")

    def _schema_file(self, request: AgentRequest) -> Path | None:
        if request.schema is None:
            return None
        root = request.schema_dir or request.cwd
        root.mkdir(parents=True, exist_ok=True)
        path = root / f".forge-{request.role}-schema.json"
        atomic_write(path, json.dumps(request.schema, sort_keys=True))
        return path

    def _codex_command(self, request: AgentRequest) -> list[str]:
        if request.session_id:
            command = ["codex", "exec", "resume", "--json", "--skip-git-repo-check"]
        else:
            command = ["codex", "exec", "--json", "--skip-git-repo-check"]
        if request.model.model:
            command += ["--model", request.model.model]
        if request.model.effort:
            command += ["--config", f'model_reasoning_effort="{request.model.effort}"']
        command += ["--config", 'approval_policy="never"']
        sandbox = {
            "write": "workspace-write",
            "test": "workspace-write",
            "inspect": "workspace-write",
            "read": "read-only",
            "none": "read-only",
        }[request.access]
        if request.session_id:
            command += ["--config", f'sandbox_mode="{sandbox}"']
        else:
            command += ["--sandbox", sandbox]
        if request.role != "tester":
            # Product roles need Codex's native filesystem/shell tools, not the
            # user's potentially large plugin/app/browser catalog. A stable,
            # smaller tool surface improves prompt-cache reuse on every tool turn.
            command += ["--ignore-user-config"]
            for feature in _CODEX_LEAN_FEATURES:
                command += ["--disable", feature]
        if request.access == "none":
            # Codex has no single "--tools none" switch. Build the equivalent
            # from documented feature/config controls and reject any remaining
            # tool event at the Forge contract boundary.
            command += [
                "--disable",
                "shell_tool",
                "--disable",
                "unified_exec",
                "--config",
                "tools.web_search=false",
                "--config",
                "tools.view_image=false",
            ]
        elif request.access == "inspect":
            command += [
                "--config",
                "tools.web_search=false",
                "--config",
                "tools.view_image=true",
            ]
        schema = self._schema_file(request)
        if schema:
            command += ["--output-schema", str(schema)]
        for path in request.extra_writable_dirs:
            if not request.session_id:
                command += ["--add-dir", str(path)]
        if request.session_id:
            command += [request.session_id, "-"]
        else:
            command += ["-"]
        return command

    def _claude_command(self, request: AgentRequest) -> list[str]:
        command = [
            "claude",
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            # Tools are pre-approved below; anything else is denied, never asked.
            "--permission-mode",
            "dontAsk",
        ]
        if request.session_id:
            command += ["--resume", request.session_id]
        else:
            command += ["--session-id", str(uuid.uuid4())]
        if request.model.model:
            command += ["--model", request.model.model]
        if request.model.effort:
            command += ["--effort", request.model.effort]
        if request.role != "tester":
            # Same lean surface as Codex: no user plugins, skills, hooks or MCP.
            command += [
                "--safe-mode",
                "--disable-slash-commands",
                "--strict-mcp-config",
                "--mcp-config",
                '{"mcpServers":{}}',
            ]
        tools = {
            "none": "",
            "read": "Read,Glob,Grep",
            "inspect": "Read,Glob,Grep,Bash,WebFetch",
            "write": "Read,Edit,Write,Glob,Grep,Bash",
            "test": "Read,Edit,Write,Glob,Grep,Bash,WebFetch",
        }[request.access]
        command += ["--tools", tools]
        if tools:
            command += ["--allowedTools", tools]
        for path in request.extra_writable_dirs:
            command += ["--add-dir", str(path)]
        if request.schema is not None:
            command += ["--json-schema", json.dumps(request.schema, separators=(",", ":"))]
        return command

    def _opencode_command(self, request: AgentRequest) -> list[str]:
        command = [
            "opencode",
            "run",
            "--format",
            "json",
            "--dir",
            str(request.cwd),
            "--pure",
        ]
        if request.model.model:
            command += ["--model", request.model.model]
        if request.model.effort:
            command += ["--variant", request.model.effort]
        if request.session_id:
            command += ["--session", request.session_id]
        request.environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(
            self._opencode_restricted_config()
        )
        agent = {
            "none": "forge-brain",
            "read": "forge-readonly",
            "inspect": "forge-product-owner",
            "write": "forge-writer",
            "test": "forge-tester",
        }[request.access]
        command += ["--agent", agent]
        return command

    @staticmethod
    def _opencode_restricted_config() -> dict[str, Any]:
        return {
            "$schema": "https://opencode.ai/config.json",
            "agent": {
                "forge-brain": {
                    "mode": "primary",
                    "prompt": "Return only the requested Forge tool-call JSON. Never use tools.",
                    "permission": {"*": "deny"},
                },
                "forge-readonly": {
                    "mode": "primary",
                    "prompt": "Inspect and report. Never modify the repository.",
                    "permission": {
                        "*": "deny",
                        "read": "allow",
                        "glob": "allow",
                        "grep": "allow",
                        "list": "allow",
                        "lsp": "allow",
                    },
                },
                "forge-product-owner": {
                    "mode": "primary",
                    "prompt": (
                        "Inspect and exercise the disposable product snapshot. "
                        "Never commit, push, or alter Git refs. Return the requested JSON."
                    ),
                    "permission": {
                        "*": "deny",
                        "read": "allow",
                        "glob": "allow",
                        "grep": "allow",
                        "list": "allow",
                        "lsp": "allow",
                        "bash": "allow",
                        "webfetch": "allow",
                    },
                },
                "forge-writer": {
                    "mode": "primary",
                    "prompt": (
                        "Work only in the current Forge workspace. Never commit, push, "
                        "switch branches, alter Git refs, or access another worktree."
                    ),
                    "permission": {
                        "*": "deny",
                        "read": "allow",
                        "edit": "allow",
                        "glob": "allow",
                        "grep": "allow",
                        "list": "allow",
                        "lsp": "allow",
                        "bash": "deny",
                        "external_directory": "deny",
                    },
                },
                "forge-tester": {
                    "mode": "primary",
                    "prompt": (
                        "Exercise the disposable product copy and record evidence. "
                        "Never access another directory or claim unexecuted checks."
                    ),
                    "permission": {
                        "*": "deny",
                        "read": "allow",
                        "edit": "allow",
                        "glob": "allow",
                        "grep": "allow",
                        "list": "allow",
                        "lsp": "allow",
                        "bash": "allow",
                        "webfetch": "allow",
                        "external_directory": "deny",
                    },
                },
            },
        }
