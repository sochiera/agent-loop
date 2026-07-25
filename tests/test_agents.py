from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import Mock, patch

from forge.agents import (
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


def test_thin_opencode_injects_tool_free_agent_and_extracts_text_event(
        tmp_path: Path) -> None:
    stream = json.dumps({
        "type": "text",
        "part": {"text": '{"tester":"","coder":"","planner":""}'},
    })
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

    inline = json.loads(captured["env"]["OPENCODE_CONFIG_CONTENT"])
    agent = inline["agent"]["forge-thin"]
    assert agent["tools"] is False
    assert agent["prompt"] == "stable rules"
    assert "--pure" in captured["argv"]
    assert result == '{"tester":"","coder":"","planner":""}'


def test_isolated_cli_homes_link_auth_but_not_global_instructions(
        tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    config = tmp_path / "config"
    codex_source = home / ".codex"
    claude_source = home / ".claude"
    codex_source.mkdir(parents=True)
    claude_source.mkdir()
    (codex_source / "auth.json").write_text("auth", encoding="utf-8")
    (codex_source / "config.toml").write_text("model='x'", encoding="utf-8")
    (codex_source / "AGENTS.md").write_text("private", encoding="utf-8")
    (claude_source / ".credentials.json").write_text(
        "credentials", encoding="utf-8")
    (claude_source / "CLAUDE.md").write_text("private", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))

    codex_env = _isolated_agent_env("codex")
    claude_env = _isolated_agent_env("claude")

    codex_home = Path(codex_env["CODEX_HOME"])
    claude_home = Path(claude_env["CLAUDE_CONFIG_DIR"])
    assert codex_home == config / "forge" / "codex"
    assert claude_home == config / "forge" / "claude"
    assert (codex_home / "auth.json").is_symlink()
    assert (codex_home / "config.toml").is_symlink()
    assert (claude_home / ".credentials.json").is_symlink()
    assert not (codex_home / "AGENTS.md").exists()
    assert not (claude_home / "CLAUDE.md").exists()


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
