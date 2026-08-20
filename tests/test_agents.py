import json
from pathlib import Path

from forge.agents import (
    AgentRequest,
    AgentRunner,
    _claude_parse,
    _codex_parse,
    _opencode_parse,
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


def test_claude_event_parser_prefers_structured_output():
    text, usage, tools = _claude_parse(
        [
            {
                "type": "result",
                "structured_output": {"ok": True},
                "usage": {"input_tokens": 7, "output_tokens": 2},
                "total_cost_usd": 0.01,
            }
        ]
    )
    assert json.loads(text) == {"ok": True}
    assert usage.cost_usd == 0.01
    assert tools == 0


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
        AgentRequest("brain", ModelSpec("codex", "gpt", "high"), "x", tmp_path, access="none")
    )
    assert "read-only" in codex
    assert "shell_tool" in codex
    assert "image_generation" in codex
    assert "plugins" in codex
    assert "agents.enabled=false" in codex
    claude = runner._command(
        AgentRequest("brain", ModelSpec("claude", "sonnet", ""), "x", tmp_path, access="none")
    )
    assert claude[claude.index("--tools") + 1] == ""
    assert "--safe-mode" in claude
    assert "--strict-mcp-config" in claude
    request = AgentRequest(
        "brain", ModelSpec("opencode", "provider/model", ""), "x", tmp_path, access="none"
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
            ModelSpec("codex", "gpt", "medium"),
            "continue",
            tmp_path,
            session_id="session-1",
            access="write",
        )
    )
    assert command[:3] == ["codex", "exec", "resume"]
    assert "--sandbox" not in command
    assert any("sandbox_mode" in item for item in command)
