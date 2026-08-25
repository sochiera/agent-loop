import json
import threading
import time
from pathlib import Path

from forge.agents import (
    AgentCancelled,
    AgentRequest,
    AgentRunner,
    AgentUsageLimit,
    _NON_RETRYABLE_ERRORS,
    _codex_parse,
    _opencode_parse,
    _session_id,
    failure_type_for,
    is_usage_limit,
)
from forge.models import ModelSpec


def test_codex_event_parser_counts_real_tools_only():
    events = [
        {"type": "thread.started", "thread_id": "abc"},
        {"type": "item.started", "item": {"type": "agent_message"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
        {"type": "item.started", "item": {"type": "command_execution"}},
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 12, "cached_input_tokens": 5, "output_tokens": 3},
        },
    ]
    text, usage, tools = _codex_parse(events)
    assert text == "done"
    assert usage.total_tokens == 15
    assert usage.cached_input_tokens == 5
    assert tools == 1


def test_opencode_event_parser_sums_steps():
    text, usage, tools = _opencode_parse(
        [
            {"part": {"type": "text", "text": "answer"}},
            {"part": {"type": "step-finish", "tokens": {"input": 8, "output": 4}}},
        ]
    )
    assert text == "answer"
    assert usage.total_tokens == 12
    assert tools == 0


def test_session_id_trusts_requested_or_top_level_values_only():
    events = [
        {"type": "thread.started", "thread_id": "actual-session"},
        {"tool_output": {"state": {"session_id": "artifact-session"}}},
    ]

    assert _session_id(events) == "actual-session"
    assert _session_id(events, "requested-session") == "requested-session"
    assert _session_id([events[1]]) is None


def test_brain_commands_are_restricted(tmp_path: Path):
    runner = AgentRunner()
    codex = runner._command(
        AgentRequest(
            "brain",
            ModelSpec.parse("codex:gpt-5.6-sol:high"),
            "x",
            tmp_path,
            access="none",
        )
    )
    assert "read-only" in codex
    assert "shell_tool" in codex
    assert "image_generation" in codex
    assert "plugins" in codex
    request = AgentRequest(
        "brain",
        ModelSpec.parse("opencode:gpt-5.6-sol"),
        "x",
        tmp_path,
        access="none",
    )
    command = runner._command(request)
    assert "forge-brain" in command
    assert "OPENCODE_CONFIG_CONTENT" in request.environment
    config = json.loads(request.environment["OPENCODE_CONFIG_CONTENT"])
    assert config["agent"]["forge-brain"]["permission"] == {"*": "deny"}


def test_codex_resume_uses_configured_sandbox_not_unsupported_flag(tmp_path: Path):
    command = AgentRunner()._command(
        AgentRequest(
            "coder",
            ModelSpec.parse("codex:gpt-5.6-luna:medium"),
            "continue",
            tmp_path,
            session_id="session-1",
            access="write",
        )
    )
    assert command[:3] == ["codex", "exec", "resume"]
    assert "--skip-git-repo-check" in command
    assert "--sandbox" not in command
    assert any("sandbox_mode" in item for item in command)
    assert any('sandbox_mode="workspace-write"' == item for item in command)
    assert "openai/gpt-5.6-luna" not in command
    assert "gpt-5.6-luna" in command


def test_codex_coder_uses_lean_cached_tool_surface(tmp_path: Path):
    command = AgentRunner()._command(
        AgentRequest(
            "coder",
            ModelSpec.parse("codex:gpt-5.6-luna:high"),
            "implement",
            tmp_path,
            access="write",
        )
    )
    assert "--ignore-user-config" in command
    assert "plugins" in command
    assert "multi_agent" in command
    assert "shell_tool" not in command


def test_opencode_writer_denies_git_delivery_and_external_paths(tmp_path: Path):
    request = AgentRequest(
        "coder",
        ModelSpec.parse("opencode:gpt-5.6-luna:high"),
        "implement",
        tmp_path,
        access="write",
    )
    command = AgentRunner()._command(request)
    config = json.loads(request.environment["OPENCODE_CONFIG_CONTENT"])
    permission = config["agent"]["forge-writer"]["permission"]

    assert "--auto" not in command
    assert "forge-writer" in command
    assert permission["edit"] == "allow"
    assert permission["external_directory"] == "deny"
    assert permission["bash"] == "deny"


def test_opencode_tester_can_execute_inside_disposable_copy(tmp_path: Path):
    request = AgentRequest(
        "tester",
        ModelSpec.parse("opencode:gpt-5.6-terra:high"),
        "exercise public behavior",
        tmp_path,
        access="test",
    )

    command = AgentRunner()._command(request)
    config = json.loads(request.environment["OPENCODE_CONFIG_CONTENT"])
    permission = config["agent"]["forge-tester"]["permission"]

    assert "forge-tester" in command
    assert permission["bash"] == "allow"
    assert permission["external_directory"] == "deny"


def test_runner_cancel_stops_live_process(tmp_path: Path):
    runner = AgentRunner()
    runner._command = lambda request: ["sleep", "30"]
    request = AgentRequest(
        "planner",
        ModelSpec.parse("opencode:grok-4.6"),
        "x",
        tmp_path,
    )

    def stop() -> None:
        time.sleep(0.2)
        runner.cancel()

    threading.Thread(target=stop, daemon=True).start()
    started = time.monotonic()
    try:
        runner.run(request)
        raise AssertionError("cancelled runner returned")
    except AgentCancelled:
        pass
    assert time.monotonic() - started < 5
    runner.allow()
    assert runner._cancelled.is_set() is False


def test_runner_cancel_during_command_build_prevents_process_start(tmp_path: Path):
    runner = AgentRunner()
    command_started = threading.Event()
    release_command = threading.Event()
    outcome = []

    def blocked_command(_request):
        command_started.set()
        release_command.wait(2)
        return ["sleep", "30"]

    runner._command = blocked_command
    request = AgentRequest(
        "planner",
        ModelSpec.parse("opencode:grok-4.6"),
        "x",
        tmp_path,
    )

    def invoke() -> None:
        try:
            runner.run(request)
        except AgentCancelled:
            outcome.append("cancelled")

    thread = threading.Thread(target=invoke)
    thread.start()
    assert command_started.wait(1)
    runner.cancel()
    release_command.set()
    thread.join(timeout=1)

    assert outcome == ["cancelled"]
    assert runner._processes == []


def test_usage_limit_is_classified_as_non_retryable():
    message = "You've hit your usage limit. Purchase more credits or try again next week."
    assert any(marker in message.lower() for marker in _NON_RETRYABLE_ERRORS)
    assert is_usage_limit(message)
    assert failure_type_for(message) is AgentUsageLimit


def test_kimi_billing_cycle_limit_is_usage_limit():
    message = (
        "You've reached your usage limit for this billing cycle. "
        "Your quota will be refreshed in the next cycle."
    )
    assert is_usage_limit(message)
    assert failure_type_for(message) is AgentUsageLimit
