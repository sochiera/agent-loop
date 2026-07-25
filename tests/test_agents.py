from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import Mock, patch

from forge.agents import (
    _append_log,
    run_claude,
    run_codex,
    run_codex_session,
    run_planner,
)
from forge.config import Config


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


if __name__ == "__main__":
    unittest.main()
