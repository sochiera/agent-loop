import json
from pathlib import Path

from forge.agents import (
    AgentRequest,
    AgentRunner,
    AgentUsageLimit,
    _NON_RETRYABLE_ERRORS,
    _codex_parse,
    _opencode_parse,
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
            "coder_tdd",
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
    assert "openai/gpt-5.6-luna" not in command
    assert "gpt-5.6-luna" in command


def test_codex_coder_uses_lean_cached_tool_surface(tmp_path: Path):
    command = AgentRunner()._command(
        AgentRequest(
            "coder_tdd",
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


def test_usage_limit_is_classified_as_non_retryable():
    message = "You've hit your usage limit. Purchase more credits or try again next week."
    assert any(marker in message.lower() for marker in _NON_RETRYABLE_ERRORS)
    assert is_usage_limit(message)
    assert failure_type_for(message) is AgentUsageLimit
