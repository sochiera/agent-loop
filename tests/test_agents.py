from __future__ import annotations

import tempfile
import unittest
from unittest.mock import Mock, patch

from forge.agents import run_claude, run_codex, run_planner
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
                                    model="gpt-5.6-sol", effort="high")

    @patch("forge.agents._run_with_backoff", return_value="")
    def test_codex_receives_selected_model_and_effort(self, run: Mock) -> None:
        with tempfile.TemporaryDirectory() as project:
            cfg = Config(codex_model="gpt-test", codex_effort="high")
            run_codex("prompt", cfg, project, "/tmp/log")

        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index("-m") + 1], "gpt-test")
        self.assertIn('model_reasoning_effort="high"', argv)


if __name__ == "__main__":
    unittest.main()
