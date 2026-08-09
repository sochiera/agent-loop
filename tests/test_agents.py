from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from forge import agents
from forge.adapters import GenericSpec
from forge.agents import (
    AgentError,
    _aggregated_output_chars,
    _append_log,
    _isolated_agent_env,
    _run_with_backoff,
    run_agent,
    run_claude,
    run_codex,
    run_codex_session,
    run_planner,
)
from forge.config import Config
from forge import ledger


class AgentArgumentsTest(unittest.TestCase):
    @patch("forge.agents._run_with_backoff", return_value='{"result":"ok"}')
    def test_claude_receives_selected_model_and_effort(self, run: Mock) -> None:
        cfg = Config(planner_agent="claude", planner_model="opus",
                     planner_effort="xhigh")

        self.assertEqual(run_claude("prompt", cfg, "/tmp", "/tmp/log"), "ok")

        argv = run.call_args.args[0]
        self.assertIn("opus", argv)
        self.assertEqual(argv[argv.index("--effort") + 1], "xhigh")

    @patch("forge.agents.run_codex", return_value="planned")
    def test_codex_can_be_used_as_planner(self, run: Mock) -> None:
        cfg = Config(planner_agent="codex", planner_model="gpt-5.6-sol",
                     planner_effort="high")

        result = run_planner("prompt", cfg, "/tmp", "/tmp/log")

        self.assertEqual(result, "planned")
        run.assert_called_once_with("prompt", cfg, "/tmp", "/tmp/log",
                                    model="gpt-5.6-sol", effort="high",
                                    usage_dir="")

    @patch("forge.agents._run_with_backoff", return_value="")
    def test_codex_receives_selected_model_and_effort(self, run: Mock) -> None:
        with tempfile.TemporaryDirectory() as project:
            cfg = Config(codex_model="gpt-test", codex_effort="high")
            run_codex("prompt", cfg, project, "/tmp/log")

        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index("-m") + 1], "gpt-test")
        self.assertIn('model_reasoning_effort="high"', argv)

    @patch("forge.agents._run_with_backoff", return_value="")
    def test_codex_resume_puts_global_options_before_exec(self, run: Mock) -> None:
        with tempfile.TemporaryDirectory() as project:
            cfg = Config(codex_model="gpt-test", codex_effort="high")
            run_codex_session("continue", cfg, project, "/tmp/log",
                              session_id="session-123")

        argv = run.call_args.args[0]
        exec_index = argv.index("exec")
        resume_index = argv.index("resume")
        self.assertLess(argv.index("-C"), exec_index)
        self.assertEqual(argv[exec_index:resume_index + 1], ["exec", "resume"])
        self.assertNotIn("--color", argv)
        self.assertLess(argv.index("--json"), argv.index("session-123"))
        self.assertEqual(argv[-2:], ["session-123", "continue"])

    def test_resumed_codex_usage_logs_only_increment_since_previous_turn(self) -> None:
        first = ('{"type":"thread.started","thread_id":"usage-test"}\n'
                 '{"type":"turn.completed","usage":{"input_tokens":100,'
                 '"cached_input_tokens":80,"output_tokens":10}}')
        second = ('{"type":"turn.completed","usage":{"input_tokens":160,'
                  '"cached_input_tokens":125,"output_tokens":16}}')
        with tempfile.TemporaryDirectory() as project, \
             patch("forge.agents._run_with_backoff", side_effect=[first, second]):
            cfg = Config()
            run_codex_session("first", cfg, project, "/tmp/first.log")
            run_codex_session("second", cfg, project, "/tmp/second.log",
                              session_id="usage-test")

            rows = [json.loads(line) for line in
                    Path(project, cfg.runtime_dir, "usage.jsonl").read_text().splitlines()]

        self.assertEqual(rows[0]["usage"]["input_tokens"], 100)
        self.assertEqual(rows[1]["usage"]["input_tokens"], 60)
        self.assertEqual(rows[1]["usage"]["cached_input_tokens"], 45)
        self.assertEqual(rows[1]["usage"]["output_tokens"], 6)
        self.assertEqual(rows[1]["usage_cumulative"]["input_tokens"], 160)


def test_append_log_truncates_aggregated_output_in_jsonl(tmp_path: Path) -> None:
    log_path = tmp_path / "agent.log"
    huge = "H" * 9000 + "M" * 5000 + "T" * 3000
    stream = json.dumps(
        {"type": "item.completed", "aggregated_output": huge},
        ensure_ascii=False,
    )

    _append_log(str(log_path), ["agent"], stream, 0)

    event = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
    saved = event["aggregated_output"]
    assert saved.startswith("H" * 8000)
    assert saved.endswith("T" * 2000)
    assert "obcięto 7000 znaków" in saved
    assert len(saved) < len(huge)
    assert len(huge) == 17000  # zapis nie może zmieniać strumienia w pamięci


def test_append_log_preserves_non_json_output(tmp_path: Path) -> None:
    log_path = tmp_path / "agent.log"

    _append_log(str(log_path), ["agent"], "zwykłe wyjście\n", 0)

    assert log_path.read_text(encoding="utf-8").endswith("zwykłe wyjście\n")


def test_thin_claude_replaces_system_prompt_and_disables_tools(
        tmp_path: Path) -> None:
    cfg = Config()
    raw = '{"result":"{\\"tester\\":\\"\\",\\"coder\\":\\"\\",\\"planner\\":\\"\\"}"}'
    with patch("forge.agents._run_with_backoff", return_value=raw) as run:
        run_agent(
            "claude", "journal", cfg, str(tmp_path), str(tmp_path / "log"),
            thin=True, system_prompt="stable rules", json_schema="{}")

    argv = run.call_args.args[0]
    assert argv[argv.index("--system-prompt") + 1] == "stable rules"
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("-p") + 1] == "journal"


def test_thin_codex_falls_back_to_normal_call_with_complete_prompt(
        tmp_path: Path) -> None:
    with patch("forge.agents.run_codex", return_value="{}") as run:
        run_agent(
            "codex", "journal", Config(), str(tmp_path), str(tmp_path / "log"),
            thin=True, system_prompt="stable rules", json_schema="{}")

    prompt = run.call_args.args[0]
    assert "stable rules" in prompt
    assert "journal" in prompt


def _run_thin_opencode(tmp_path: Path, monkeypatch, stream: str) -> tuple:
    """Tryb cienki opencode z podstawioną konfiguracją użytkownika."""
    config_home = tmp_path / "xdg"
    (config_home / "opencode").mkdir(parents=True)
    (config_home / "opencode" / "opencode.json").write_text(
        json.dumps({"provider": {"neuralwatt": {"npm": "@ai-sdk/openai"}}}),
        encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.delenv("OPENCODE_CONFIG", raising=False)
    captured = {}

    def backoff(argv, cwd, cfg, log_path, stdin_text=None, env=None):
        captured["argv"] = argv
        captured["env"] = env
        return stream

    with patch("forge.agents._run_with_backoff", side_effect=backoff):
        result = run_agent(
            "opencode", "journal", Config(), str(tmp_path),
            str(tmp_path / "log"), thin=True, system_prompt="stable rules",
            json_schema="{}")
    return captured, result


def test_thin_opencode_injects_tool_free_agent_and_extracts_text_event(
        tmp_path: Path, monkeypatch) -> None:
    stream = json.dumps({
        "type": "text",
        "part": {"text": '{"tester":"","coder":"","planner":""}'},
    })
    captured, result = _run_thin_opencode(tmp_path, monkeypatch, stream)

    inline = json.loads(captured["env"]["OPENCODE_CONFIG_CONTENT"])
    agent = inline["agent"]["forge-thin"]
    # Schemat agenta oczekuje mapy nazwa→bool, nie samego ``false``.
    assert agent["tools"] and all(
        value is False for value in agent["tools"].values())
    assert agent["prompt"] == "stable rules"
    assert "--pure" in captured["argv"]
    assert result == '{"tester":"","coder":"","planner":""}'


def test_thin_opencode_keeps_user_provider_config(
        tmp_path: Path, monkeypatch) -> None:
    """Podstawienie treści konfiguracji nie może skasować bloku ``provider`` —
    bez niego ``-m neuralwatt/...`` nie rozwiąże się na dostawcę."""
    captured, _ = _run_thin_opencode(
        tmp_path, monkeypatch,
        json.dumps({"type": "text", "part": {"text": "{}"}}))

    inline = json.loads(captured["env"]["OPENCODE_CONFIG_CONTENT"])
    assert inline["provider"] == {"neuralwatt": {"npm": "@ai-sdk/openai"}}
    assert "forge-thin" in inline["agent"]


def test_thin_opencode_deduplicates_streamed_part_updates(
        tmp_path: Path, monkeypatch) -> None:
    """Strumień emituje tę samą część w miarę jak rośnie; sklejenie wszystkich
    wystąpień dałoby wielokrotnie powtórzoną odpowiedź."""
    growing = ['{"tester":', '{"tester":"",', '{"tester":"","coder":""}']
    stream = "\n".join(
        json.dumps({"part": {"id": "prt_1", "type": "text", "text": text}})
        for text in growing)
    _, result = _run_thin_opencode(tmp_path, monkeypatch, stream)

    assert result == '{"tester":"","coder":""}'


def test_isolated_cli_homes_link_auth_but_not_global_instructions(
        tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    config = tmp_path / "config"
    codex_source = home / ".codex"
    claude_source = home / ".claude"
    grok_source = home / ".grok"
    codex_source.mkdir(parents=True)
    claude_source.mkdir()
    grok_source.mkdir()
    (codex_source / "auth.json").write_text("auth", encoding="utf-8")
    (codex_source / "config.toml").write_text("model='x'", encoding="utf-8")
    (codex_source / "AGENTS.md").write_text("private", encoding="utf-8")
    (claude_source / ".credentials.json").write_text(
        "credentials", encoding="utf-8")
    (claude_source / "CLAUDE.md").write_text("private", encoding="utf-8")
    (grok_source / "auth.json").write_text("auth", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))

    codex_env = _isolated_agent_env("codex")
    claude_env = _isolated_agent_env("claude")
    grok_env = _isolated_agent_env("grok")

    codex_home = Path(codex_env["CODEX_HOME"])
    claude_home = Path(claude_env["CLAUDE_CONFIG_DIR"])
    grok_home = Path(grok_env["GROK_HOME"])
    assert codex_home == config / "forge" / "codex"
    assert claude_home == config / "forge" / "claude"
    assert (codex_home / "auth.json").is_symlink()
    assert (codex_home / "config.toml").is_symlink()
    assert (claude_home / ".credentials.json").is_symlink()
    assert not (codex_home / "AGENTS.md").exists()
    assert not (claude_home / "CLAUDE.md").exists()
    assert grok_home == config / "forge" / "grok"
    assert (grok_home / "auth.json").is_symlink()
    assert "agents = false" in (grok_home / "config.toml").read_text()
    assert not (grok_home / "CLAUDE.md").exists()


def test_isolated_claude_home_replaces_stale_credential_copy(
        tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    config = tmp_path / "config"
    source = home / ".claude" / ".credentials.json"
    destination = config / "forge" / "claude" / ".credentials.json"
    source.parent.mkdir(parents=True)
    destination.parent.mkdir(parents=True)
    source.write_text("fresh", encoding="utf-8")
    # This is the state produced by an older Forge run: a stale copy that
    # cannot observe an OAuth refresh done by the normal Claude CLI.
    destination.write_text("expired", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))

    _isolated_agent_env("claude")

    assert destination.is_symlink()
    assert destination.resolve() == source
    assert destination.read_text(encoding="utf-8") == "fresh"


def test_builtin_agents_receive_isolated_environment(tmp_path: Path) -> None:
    claude_raw = '{"result":"ok"}'
    with patch("forge.agents._isolated_agent_env",
               side_effect=lambda name: {f"{name.upper()}_ISOLATED": "1"}), \
         patch("forge.agents._run_with_backoff",
               return_value=claude_raw) as claude_run:
        run_claude("prompt", Config(), str(tmp_path), str(tmp_path / "c.log"))
    assert claude_run.call_args.kwargs["env"] == {"CLAUDE_ISOLATED": "1"}

    with patch("forge.agents._isolated_agent_env",
               side_effect=lambda name: {f"{name.upper()}_ISOLATED": "1"}), \
         patch("forge.agents._run_with_backoff",
               return_value="") as codex_run:
        run_codex("prompt", Config(), str(tmp_path), str(tmp_path / "x.log"))
    assert codex_run.call_args.kwargs["env"] == {"CODEX_ISOLATED": "1"}


def test_generic_grok_receives_isolated_environment(tmp_path: Path) -> None:
    with patch("forge.agents._isolated_agent_env",
               return_value={"GROK_HOME": "/isolated/grok"}), \
         patch("forge.agents._run_with_backoff", return_value="ok") as run:
        run_agent("grok", "journal", Config(), str(tmp_path),
                  str(tmp_path / "log"))
    assert run.call_args.kwargs["env"] == {"GROK_HOME": "/isolated/grok"}


def test_opencode_usage_sums_last_version_of_each_message() -> None:
    def event(message_id: str, input_tokens: int) -> str:
        return json.dumps({"type": "message.updated.1", "properties": {"info": {
            "id": message_id, "role": "assistant", "tokens": {
                "input": input_tokens, "output": input_tokens // 10,
                "reasoning": input_tokens // 20, "cache": {"read": 4, "write": 2},
            },
        }}})

    usage = agents.extract_opencode_usage("\n".join((
        event("first", 10), event("first", 30), event("first", 50), event("second", 7),
    )))

    assert usage == {
        "input_tokens": 57, "cached_input_tokens": 8,
        "cache_creation_input_tokens": 4, "output_tokens": 5,
        "reasoning_output_tokens": 2,
    }


def test_opencode_usage_maps_cache_read_and_write() -> None:
    stream = json.dumps({"type": "unknown-future-event", "info": {
        "id": "msg", "role": "assistant", "tokens": {
            "input": 12, "output": 3, "reasoning": 2,
            "cache": {"read": 9, "write": 5},
        },
    }})

    assert agents.extract_opencode_usage(stream) == {
        "input_tokens": 12, "cached_input_tokens": 9,
        "cache_creation_input_tokens": 5, "output_tokens": 3,
        "reasoning_output_tokens": 2,
    }


def test_opencode_usage_empty_stream_returns_empty() -> None:
    assert agents.extract_opencode_usage("not json\n{}") == {}


def test_opencode_usage_skips_messages_without_stable_id() -> None:
    stream = "\n".join(json.dumps({"info": {
        "role": "assistant", "tokens": {"input": input_tokens},
    }}) for input_tokens in (10, 20))

    assert agents.extract_opencode_usage(stream) == {}


def test_generic_opencode_extracts_text_without_thin_and_logs_usage(
        tmp_path: Path) -> None:
    stream = "\n".join((
        json.dumps({"part": {"id": "answer", "type": "text", "text": "część"}}),
        json.dumps({"part": {"id": "answer", "type": "text", "text": "czysty werdykt"}}),
        json.dumps({"type": "message.updated", "info": {
            "id": "msg", "role": "assistant", "tokens": {
                "input": 20, "output": 4, "reasoning": 1,
                "cache": {"read": 15, "write": 3},
            },
        }}),
    ))

    with patch("forge.agents._run_with_backoff", return_value=stream):
        result = run_agent("opencode", "prompt", Config(), str(tmp_path),
                           str(tmp_path / "review.log"))

    assert result == "czysty werdykt"
    records = [json.loads(line) for line in (
        tmp_path / ".forge" / "usage.jsonl").read_text(encoding="utf-8").splitlines()]
    assert records[-1]["usage"] == {
        "input_tokens": 20, "cached_input_tokens": 15,
        "cache_creation_input_tokens": 3, "output_tokens": 4,
        "reasoning_output_tokens": 1,
    }


def test_opencode_usage_reads_step_finish_cumulative_total() -> None:
    def step(message_id: str, total_input: int) -> str:
        return json.dumps({"type": "step_finish", "part": {
            "messageID": message_id, "type": "step-finish",
            "tokens": {"total": total_input + 10, "input": total_input,
                       "output": 5, "reasoning": 1,
                       "cache": {"read": 3, "write": 2}},
        }})

    # Kolejne zdarzenia niosą NARASTAJĄCY licznik całego wywołania — tylko
    # ostatnie ma się liczyć, sumowanie zawyżyłoby rachunek.
    usage = agents.extract_opencode_usage("\n".join((
        step("msg-1", 100), step("msg-2", 250), step("msg-3", 400),
    )))

    assert usage == {
        "input_tokens": 400, "cache_read_input_tokens": 3,
        "cache_creation_input_tokens": 2, "output_tokens": 5,
        "reasoning_output_tokens": 1,
    }


def test_opencode_usage_prefers_step_finish_over_legacy_format() -> None:
    stream = "\n".join((
        json.dumps({"type": "message.updated", "info": {
            "id": "msg", "role": "assistant", "tokens": {"input": 999},
        }}),
        json.dumps({"type": "step_finish", "part": {
            "tokens": {"input": 12, "output": 3, "reasoning": 0,
                       "cache": {"read": 1, "write": 0}},
        }}),
    ))

    usage = agents.extract_opencode_usage(stream)

    assert usage["input_tokens"] == 12


def test_generic_opencode_warns_when_stream_has_no_usage(tmp_path: Path) -> None:
    with patch("forge.agents._run_with_backoff", return_value='{"type":"future"}'), \
         patch("forge.agents.log") as warning:
        run_agent("opencode", "prompt", Config(), str(tmp_path),
                  str(tmp_path / "review.log"))

    assert any("bez liczników tokenów" in str(call.args[0])
               for call in warning.call_args_list)


def test_grok_usage_reads_last_turn_completed_from_session_file(
        tmp_path: Path) -> None:
    from urllib.parse import quote
    cwd = str(tmp_path / "project")
    Path(cwd).mkdir()
    session_dir = (tmp_path / "grok-home" / "sessions"
                   / quote(str(Path(cwd).resolve()), safe="") / "sess-1")
    session_dir.mkdir(parents=True)
    updates = [
        {"params": {"update": {"sessionUpdate": "turn_completed", "usage": {
            "inputTokens": 100, "outputTokens": 10, "cachedReadTokens": 20,
            "reasoningTokens": 5,
        }}}},
        {"params": {"update": {"sessionUpdate": "turn_completed", "usage": {
            "inputTokens": 300, "outputTokens": 30, "cachedReadTokens": 60,
            "reasoningTokens": 15,
        }}}},
    ]
    (session_dir / "updates.jsonl").write_text(
        "\n".join(json.dumps(u) for u in updates), encoding="utf-8")

    usage = agents.extract_grok_usage(str(tmp_path / "grok-home"), cwd, "sess-1")

    assert usage == {
        "input_tokens": 300, "cached_input_tokens": 60,
        "output_tokens": 30, "reasoning_output_tokens": 15,
    }


def test_grok_usage_missing_session_file_returns_empty(tmp_path: Path) -> None:
    assert agents.extract_grok_usage(str(tmp_path / "nope"), str(tmp_path), "x") == {}


def test_grok_usage_without_home_or_session_id_returns_empty() -> None:
    assert agents.extract_grok_usage("", "/tmp", "sess") == {}
    assert agents.extract_grok_usage("/tmp/home", "/tmp", "") == {}


def test_generic_grok_passes_session_id_and_logs_usage(tmp_path: Path) -> None:
    from urllib.parse import quote
    project = str(tmp_path / "project")
    Path(project).mkdir()
    grok_home = str(tmp_path / "grok-home")
    captured = {}

    def fake_backoff(argv, cwd, cfg_, log, stdin_text=None, env=None):
        captured["argv"] = argv
        session_id = argv[argv.index("--session-id") + 1]
        session_dir = (Path(grok_home) / "sessions"
                       / quote(str(Path(project).resolve()), safe="") / session_id)
        session_dir.mkdir(parents=True)
        (session_dir / "updates.jsonl").write_text(json.dumps({
            "params": {"update": {"sessionUpdate": "turn_completed", "usage": {
                "inputTokens": 50, "outputTokens": 4, "cachedReadTokens": 1,
                "reasoningTokens": 0,
            }}},
        }), encoding="utf-8")
        return "odpowiedź groka"

    with patch("forge.agents._isolated_agent_env",
               return_value={"GROK_HOME": grok_home}), \
         patch("forge.agents._run_with_backoff", side_effect=fake_backoff):
        out = run_agent("grok", "prompt", Config(), project, str(tmp_path / "log"))

    assert out == "odpowiedź groka"
    assert "--session-id" in captured["argv"]
    records = [json.loads(line) for line in (
        Path(project) / ".forge" / "usage.jsonl").read_text(encoding="utf-8").splitlines()]
    assert records[-1]["agent"] == "grok"
    assert records[-1]["usage"] == {
        "input_tokens": 50, "cached_input_tokens": 1,
        "output_tokens": 4, "reasoning_output_tokens": 0,
    }


def test_prompt_file_is_cleaned_when_template_expands_to_empty_argv(
        tmp_path: Path) -> None:
    spec = GenericSpec("grok", ["{prompt_file}"], False, True)
    seen: dict[str, str] = {}

    def expand(_template, subs):
        seen["path"] = subs["prompt_file"]
        return []

    with patch("forge.agents.adapters.expand_template", side_effect=expand):
        with pytest.raises(AgentError, match="Pusty szablon"):
            agents._run_generic(
                spec, "sekretny prompt", Config(), str(tmp_path),
                str(tmp_path / "log"), model="", effort="")

    assert seen["path"]
    assert not Path(seen["path"]).exists()


def test_aggregated_output_counter_walks_jsonl_events() -> None:
    stream = "\n".join((
        json.dumps({"aggregated_output": "x" * 150_000}),
        json.dumps({"nested": [{"aggregated_output": "y" * 60_001}]}),
        json.dumps({"other_output": "z" * 500_000}),
        "not json",
    ))

    assert _aggregated_output_chars(stream) == 210_001


def test_large_tool_output_is_reported_to_project_ledger(
        tmp_path: Path) -> None:
    stream = json.dumps({"aggregated_output": "x" * 210_000})
    process = __import__("subprocess").CompletedProcess(
        ["agent"], 0, stdout=stream, stderr="")

    with patch("forge.agents.subprocess.run", return_value=process):
        returned = _run_with_backoff(
            ["agent"], str(tmp_path), Config(max_limit_retries=0),
            str(tmp_path / "agent.log"))

    assert returned == stream
    warning = ledger.tail(str(tmp_path))
    assert "UWAGA: tura wciągnęła" in warning
    assert "0.2 MB wyjścia narzędzi" in warning


if __name__ == "__main__":
    unittest.main()


def test_failed_codex_resume_falls_back_to_a_new_session(tmp_path: Path) -> None:
    """Przeniesienie CODEX_HOME (izolacja instrukcji) unieważnia zapisane id
    wątków. Nieudany resume nie może zatrzymywać całej pętli."""
    calls: list[list[str]] = []

    def backoff(argv, cwd, cfg, log_path, stdin_text=None, env=None,
                ledger_project=""):
        calls.append(argv)
        if "resume" in argv:
            raise AgentError("agent zwrócił kod 1. Ogon:\nsession not found")
        return json.dumps({"type": "thread.started", "thread_id": "new-id"})

    with patch("forge.agents._run_with_backoff", side_effect=backoff):
        _out, sid = agents.run_codex_session(
            "prompt", Config(), str(tmp_path), str(tmp_path / "log"),
            session_id="stale-id")

    assert len(calls) == 2
    assert "resume" in calls[0] and "stale-id" in calls[0]
    assert "resume" not in calls[1]
    assert sid == "new-id"


def test_failed_first_codex_session_still_raises(tmp_path: Path) -> None:
    with patch("forge.agents._run_with_backoff",
               side_effect=AgentError("crash")):
        with pytest.raises(AgentError):
            agents.run_codex_session(
                "prompt", Config(), str(tmp_path), str(tmp_path / "log"))


def test_claude_thin_template_never_bypasses_native_handling(
        tmp_path: Path, monkeypatch) -> None:
    """Ścieżka generyczna gubi wykrywanie limitów, `is_error` i telemetrię
    zużycia, a claude ma natywny tryb cienki — więc natywny musi wygrać."""
    monkeypatch.setenv("FORGE_AGENT_CLAUDE_THIN_CMD", "custom-claude {prompt}")

    with patch("forge.agents.run_claude", return_value="{}") as native, \
         patch("forge.agents._run_generic") as generic:
        run_agent("claude", "journal", Config(), str(tmp_path),
                  str(tmp_path / "log"), thin=True, system_prompt="rules")

    native.assert_called_once()
    assert native.call_args.kwargs["thin"] is True
    assert native.call_args.kwargs["system_prompt"] == "rules"
    generic.assert_not_called()


def test_codex_sandbox_defaults_to_full_access(
        tmp_path: Path) -> None:
    with patch("forge.agents._run_with_backoff", return_value="") as run:
        run_codex("prompt", Config(), str(tmp_path), str(tmp_path / "log"))

    argv = run.call_args.args[0]
    assert "--dangerously-bypass-approvals-and-sandbox" in argv
    assert "-s" not in argv


def test_workspace_sandbox_stays_available_as_an_opt_in_and_keeps_network(
        tmp_path: Path) -> None:
    cfg = Config(codex_sandbox="workspace-write")
    with patch("forge.agents._run_with_backoff", return_value="") as run:
        run_codex("prompt", cfg, str(tmp_path), str(tmp_path / "log"))

    argv = run.call_args.args[0]
    assert argv[argv.index("-s") + 1] == "workspace-write"
    assert "sandbox_workspace_write.network_access=true" in argv
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv


def test_extract_json_reads_verdict_behind_unpaired_quote_in_prose() -> None:
    """Polskie „…" domknięte ASCII-`"` rozjeżdżało ręczny skaner stanu stringów
    i gubiło POPRAWNY werdykt agenta — cała tura szła do kosza."""
    text = ('a „wybór osady dla rozkazów wojskowych" jest poza zakresem\n'
            '{"verdict":"request_changes","notes":["konkret"]}')

    assert agents.extract_json(text) == {
        "verdict": "request_changes", "notes": ["konkret"]}


def test_extract_json_falls_back_to_scan_when_fence_is_broken() -> None:
    text = '```json\n{niepoprawny}\n```\n{"verdict":"approve","notes":[]}'

    assert agents.extract_json(text) == {"verdict": "approve", "notes": []}


def test_extract_json_prefers_last_fence_over_trailing_prose_object() -> None:
    text = ('```json\n{"verdict":"approve","notes":[]}\n```\n'
            'przykład formatu: {"verdict":"request_changes"}')

    assert agents.extract_json(text) == {"verdict": "approve", "notes": []}


def test_extract_json_rejects_truncated_batch_instead_of_returning_subtask() -> None:
    """Urwana partia planisty musi dać None: zwrócenie ostatniego DOMKNIĘTEGO
    podzadania podaje wywołującemu „poprawny" dict bez pola `tasks`, więc
    korekta formatu nigdy nie startuje, a bieg umiera na mylącym błędzie."""
    truncated = ('{"tasks":[{"id":"task-001","title":"a"},'
                 '{"id":"task-002","title":"b"},{"id":"task-003","tit')

    assert agents.extract_json(truncated) is None


def test_extract_json_rejects_malformed_object_instead_of_nested_value() -> None:
    assert agents.extract_json(
        '{"verdict":"request_changes","notes":["a",],"meta":{"x":1}}') is None


def test_extract_json_repairs_ascii_quote_closing_polish_opener() -> None:
    text = ('```json\n'
            '{"summary":"K117 w ramieniu „zaplanowany". Kolejka",'
            '"replan":false}\n```')

    assert agents.extract_json(text) == {
        "summary": "K117 w ramieniu „zaplanowany\". Kolejka",
        "replan": False,
    }
    assert agents._extract_json_detail(text).repaired is True


def test_extract_json_repairs_invalid_escape_before_typographic_quote() -> None:
    text = '{"summary":"można „grać patrząc\\” na mapę","replan":false}'

    assert agents.extract_json(text) == {
        "summary": "można „grać patrząc” na mapę", "replan": False}


def test_extract_json_repairs_unfenced_nested_planner_verdict() -> None:
    text = ('{"no_more_tasks":false,"tasks":[{"id":"task-001",'
            '"title":"a „b" c"}]}')

    detail = agents._extract_json_detail(text)

    assert detail.repaired is True
    assert detail.data == {
        "no_more_tasks": False,
        "tasks": [{"id": "task-001", "title": "a „b\" c"}],
    }


def test_extract_json_repair_keeps_valid_json_untouched() -> None:
    text = '{"summary":"wariant „pełny”","replan":false}'

    detail = agents._extract_json_detail(text)

    assert detail.data == {"summary": "wariant „pełny”", "replan": False}
    assert detail.repaired is False


def test_extract_json_repair_returns_none_when_ambiguous() -> None:
    text = '{"summary":"wariant „zaplanowany", ale dalej","replan":false}'

    assert agents.extract_json(text) is None


def test_extract_json_detail_reports_position_and_context() -> None:
    text = '```json\n{"summary":"wariant „zaplanowany". dalej","replan":}\n```'

    detail = agents._extract_json_detail(text)

    assert detail.data is None
    assert "linia" in detail.error and "kolumna" in detail.error
    assert "zaplanowany" in detail.error


def test_extract_json_keeps_whole_batch_when_planner_output_is_valid() -> None:
    data = agents.extract_json(
        '{"tasks":[{"id":"task-001"},{"id":"task-002"}]}')

    assert [task["id"] for task in data["tasks"]] == ["task-001", "task-002"]


@pytest.mark.parametrize("prose", [
    "Podsumowując,\n",                      # przecinek + nowa linia to zdanie
    "Werdykt: ",                            # dwukropek prozy, nie klucza
    "kod `if (x) {` psuje naiwny skan\n",   # niesparowana klamra w prozie
    "",
])
def test_extract_json_treats_prose_before_verdict_as_prose(prose: str) -> None:
    assert agents.extract_json(
        prose + '{"verdict":"approve","notes":[]}') == {
            "verdict": "approve", "notes": []}


def test_extract_json_returns_none_without_any_object() -> None:
    assert agents.extract_json("bez JSON-a") is None
    assert agents.extract_json("") is None
    assert agents.extract_json('[{"verdict":"approve"}]') is None


# --- Łańcuch zapasowy ról ---------------------------------------------------
# Przełączenie ma zachodzić po WYCZERPANIU limitu (czyli po całym backoffie) i
# po twardej awarii; jedno i drugie znaczy dla biegu to samo — tą drogą pracy
# nie będzie.

def _chain_config(**fallbacks) -> Config:
    from forge import routing

    return Config(routing=routing.parse({"roles": {"coder": {
        "agent": "opencode",
        "slots": {"standard": {"model": "pierwszy"}},
        "fallbacks": list(fallbacks.get("entries", [])),
    }}}, ("simple", "standard", "complex")))


@pytest.mark.parametrize("failure", [
    agents.LimitExhausted("limit"),
    agents.AgentError("crash"),
])
def test_role_falls_back_after_limit_and_after_hard_failure(failure) -> None:
    cfg = _chain_config(entries=[{"model": "zapasowy"}])
    calls: list[str] = []

    def run(_name, _prompt, _cfg, _project, _log, *, model="", **_kwargs):
        calls.append(model)
        if model == "pierwszy":
            raise failure
        return "ok"

    with patch("forge.agents.run_agent", side_effect=run):
        result = agents.run_role("coder", "prompt", cfg, "/tmp", "/tmp/log")

    assert result == "ok"
    assert calls == ["pierwszy", "zapasowy"]


def test_exhausted_chain_reports_the_last_failure() -> None:
    cfg = _chain_config(entries=[{"model": "zapasowy"}])

    with patch("forge.agents.run_agent",
               side_effect=agents.AgentError("ostatni")):
        with pytest.raises(AgentError, match="ostatni"):
            agents.run_role("coder", "prompt", cfg, "/tmp", "/tmp/log")


def test_interruption_is_not_swallowed_by_the_chain() -> None:
    # Fallback broni przed awarią dostawcy, a nie przed decyzją użytkownika.
    cfg = _chain_config(entries=[{"model": "zapasowy"}])
    calls: list[str] = []

    def run(_name, _prompt, _cfg, _project, _log, *, model="", **_kwargs):
        calls.append(model)
        raise KeyboardInterrupt

    with patch("forge.agents.run_agent", side_effect=run):
        with pytest.raises(KeyboardInterrupt):
            agents.run_role("coder", "prompt", cfg, "/tmp", "/tmp/log")

    assert calls == ["pierwszy"]


def test_role_without_a_chain_behaves_exactly_as_before() -> None:
    cfg = _chain_config()

    with patch("forge.agents.run_agent",
               side_effect=agents.LimitExhausted("limit")):
        with pytest.raises(agents.LimitExhausted):
            agents.run_role("coder", "prompt", cfg, "/tmp", "/tmp/log")
