import json
import threading
import time
from pathlib import Path

import pytest

from forge.agents import (
    AgentCancelled,
    AgentConfigurationFailure,
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
            ModelSpec.parse("codex:gpt-6-sol:medium"),
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
        ModelSpec.parse("opencode:glm-5.3-flash"),
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
            ModelSpec.parse("codex:gpt-6-luna:xhigh"),
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
    assert "gpt-6-luna" in command
    assert 'model_reasoning_effort="xhigh"' in command


def test_codex_coder_uses_lean_cached_tool_surface(tmp_path: Path):
    command = AgentRunner()._command(
        AgentRequest(
            "coder",
            ModelSpec.parse("codex:gpt-6-luna:xhigh"),
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
        ModelSpec.parse("opencode:glm-5.3-flash:xhigh"),
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
        ModelSpec.parse("opencode:glm-5.3-flash:xhigh"),
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
        ModelSpec.parse("opencode:glm-5.3-flash"),
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
        ModelSpec.parse("opencode:glm-5.3-flash"),
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


RETIRED_SELECTORS = (
    "codex:gpt-5.6-sol:high",
    "codex:gpt-5.6-terra:high",
    "codex:gpt-5.6-luna:high",
    "opencode:deepseek-v4.1-flash:high",
    "opencode:mimo-v2.6-flash:high",
    "opencode:grok-4.6",
    "opencode:kimi-k3",
)


@pytest.mark.parametrize("state", ["active", "inactive"])
def test_runner_rejects_off_policy_models_at_the_command_boundary(
    tmp_path, monkeypatch, state
):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(f'{{"promotion_state": "{state}"}}', encoding="utf-8")
    monkeypatch.setenv("FORGE_MODEL_POLICY_PATH", str(policy_file))
    runner = AgentRunner()

    # Swarm-only models are refused for the tournament coders as well.
    for selector in (
        *RETIRED_SELECTORS,
        "codex:gpt-6-sol:high",
        "opencode:deepseek-v4.1-flash:xhigh",
        "opencode:mimo-v2.6-flash:xhigh",
    ):
        request = AgentRequest(
            "coder_tdd", ModelSpec.parse(selector), "x", tmp_path
        )
        with pytest.raises(AgentConfigurationFailure):
            runner._command(request)
        with pytest.raises(AgentConfigurationFailure):
            runner.run(request)

    for selector, binary in (
        ("codex:gpt-6-sol:medium", "codex"),
        ("codex:gpt-6-luna:xhigh", "codex"),
        ("claude:claude-opus-5-5:medium", "claude"),
        ("opencode:glm-5.3-flash:xhigh", "opencode"),
    ):
        allowed = AgentRequest("coder_tdd", ModelSpec.parse(selector), "x", tmp_path)
        assert runner._command(allowed)[0] == binary


@pytest.mark.parametrize(
    "selector", ["opencode:deepseek-v4.1-flash", "opencode:mimo-v2.6-flash:xhigh"]
)
def test_runner_keeps_swarm_only_models_in_the_swarm_roles(tmp_path, selector):
    from forge.policy import PROMOTION_ACTIVE, PromotionSnapshot

    runner = AgentRunner(policy=PromotionSnapshot(state=PROMOTION_ACTIVE))
    for role in (
        "brain", "planner", "test_author", "reviewer", "tester",
        "coder_tdd", "coder_explore", "coder_classic",
    ):
        request = AgentRequest(role, ModelSpec.parse(selector), "x", tmp_path)
        with pytest.raises(AgentConfigurationFailure):
            runner._command(request)
    for role in ("swarm_coder", "swarm_reviewer", "probe"):
        request = AgentRequest(role, ModelSpec.parse(selector), "x", tmp_path)
        command = runner._command(request)
        assert command[0] == "opencode"
        assert command[command.index("--variant") + 1] == "xhigh"


def test_runner_fails_closed_when_the_policy_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_MODEL_POLICY_PATH", str(tmp_path / "absent.json"))
    runner = AgentRunner()
    for selector in RETIRED_SELECTORS:
        request = AgentRequest(
            "coder_tdd", ModelSpec.parse(selector), "x", tmp_path
        )
        with pytest.raises(AgentConfigurationFailure):
            runner._command(request)


def test_runner_pins_an_empty_effort_to_the_policy_effort(tmp_path):
    from forge.policy import PROMOTION_ACTIVE, PromotionSnapshot

    runner = AgentRunner(policy=PromotionSnapshot(state=PROMOTION_ACTIVE))
    request = AgentRequest(
        "coder_tdd", ModelSpec.parse("opencode:glm-5.3-flash"), "x", tmp_path
    )
    command = runner._command(request)
    assert command[command.index("--variant") + 1] == "xhigh"
    assert request.model.effort == "xhigh"


def test_claude_command_uses_explicit_opus_slug_and_restricted_tools(tmp_path):
    runner = AgentRunner()
    brain = runner._command(
        AgentRequest(
            "brain",
            ModelSpec.parse("claude:claude-opus-5-5"),
            "x",
            tmp_path,
            access="none",
            schema={"type": "object"},
        )
    )
    assert brain[:2] == ["claude", "--print"]
    assert brain[brain.index("--model") + 1] == "claude-opus-5-5"
    assert brain[brain.index("--effort") + 1] == "medium"
    assert brain[brain.index("--permission-mode") + 1] == "dontAsk"
    assert brain[brain.index("--tools") + 1] == ""
    assert "--allowedTools" not in brain
    assert "--safe-mode" in brain
    assert "--json-schema" in brain
    assert "--session-id" in brain

    coder = runner._command(
        AgentRequest(
            "coder_tdd",
            ModelSpec.parse("claude:claude-opus-5-5:medium"),
            "x",
            tmp_path,
            session_id="abc",
            access="write",
        )
    )
    assert coder[coder.index("--resume") + 1] == "abc"
    assert coder[coder.index("--allowedTools") + 1] == "Read,Edit,Write,Glob,Grep,Bash"
    reader = runner._command(
        AgentRequest(
            "reviewer",
            ModelSpec.parse("claude:claude-opus-5-5:medium"),
            "x",
            tmp_path,
            access="read",
        )
    )
    assert reader[reader.index("--tools") + 1] == "Read,Glob,Grep"


def test_claude_stream_json_is_parsed():
    from forge.agents import _claude_parse

    events = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1"}]}},
        {
            "type": "result",
            "result": "done",
            "session_id": "s1",
            "usage": {"input_tokens": 10, "cache_read_input_tokens": 4, "output_tokens": 3},
            "total_cost_usd": 0.5,
        },
    ]
    text, usage, tools = _claude_parse(events)
    assert text == "done"
    assert (usage.input_tokens, usage.cached_input_tokens, usage.output_tokens) == (10, 4, 3)
    assert usage.cost_usd == 0.5
    assert tools == 1
