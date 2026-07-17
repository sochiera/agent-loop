from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from forge import adapters
from forge.config import Config


class TemplateExpansionTest(unittest.TestCase):
    def test_placeholders_substituted_and_empty_dropped(self) -> None:
        tmpl = ["grok", "--model", "{model}", "--exec", "{prompt}", "--out", "{output}"]
        argv = adapters.expand_template(
            tmpl, {"model": "grok-2", "prompt": "zrób X", "output": "/t/out",
                   "effort": "", "project": "/p"})
        self.assertEqual(argv, ["grok", "--model", "grok-2", "--exec", "zrób X",
                                "--out", "/t/out"])

    def test_pure_empty_placeholder_token_is_removed(self) -> None:
        # {model} nieustawiony → token znika (bez pustego argumentu w argv).
        argv = adapters.expand_template(["cli", "{model}", "{prompt}"],
                                        {"model": "", "prompt": "hej"})
        self.assertEqual(argv, ["cli", "hej"])

    def test_empty_placeholder_drops_preceding_option_flag(self) -> None:
        # 'grok --model {model} --exec {prompt}' bez modelu → flaga --model też znika,
        # inaczej --exec stałby się wartością --model.
        argv = adapters.expand_template(
            ["grok", "--model", "{model}", "--exec", "{prompt}"],
            {"model": "", "prompt": "zrób X"})
        self.assertEqual(argv, ["grok", "--exec", "zrób X"])

    def test_empty_placeholder_keeps_binary_when_no_flag(self) -> None:
        # Poprzednik nie jest flagą (nie zaczyna się od '-') → nic nie usuwamy poza placeholderem.
        argv = adapters.expand_template(["cli", "{model}", "{prompt}"],
                                        {"model": "", "prompt": "p"})
        self.assertEqual(argv, ["cli", "p"])

    def test_prompt_with_spaces_stays_single_arg(self) -> None:
        argv = adapters.expand_template(["cli", "{prompt}"],
                                        {"prompt": "wiele słów tu"})
        self.assertEqual(argv, ["cli", "wiele słów tu"])

    def test_placeholder_in_prompt_value_is_not_re_expanded(self) -> None:
        # Jeden przebieg: literalne "{model}" w treści promptu MUSI przetrwać.
        argv = adapters.expand_template(["cli", "{prompt}"],
                                        {"prompt": "zrób {model} rzecz", "model": "X"})
        self.assertEqual(argv, ["cli", "zrób {model} rzecz"])

    def test_unknown_placeholder_left_intact(self) -> None:
        argv = adapters.expand_template(["cli", "{nieznany}", "{prompt}"],
                                        {"prompt": "p"})
        self.assertEqual(argv, ["cli", "{nieznany}", "p"])


class GenericSpecTest(unittest.TestCase):
    def test_spec_from_env_detects_output_file(self) -> None:
        env = {"FORGE_AGENT_GROK_CMD": "grok --exec {prompt} --out {output}"}
        spec = adapters.generic_spec("grok", env)
        self.assertIsNotNone(spec)
        self.assertTrue(spec.uses_output_file)
        self.assertEqual(adapters.generic_bin(spec), "grok")

    def test_spec_without_output_uses_stdout(self) -> None:
        env = {"FORGE_AGENT_KIRO_CMD": "kiro run {prompt}"}
        spec = adapters.generic_spec("kiro", env)
        self.assertFalse(spec.uses_output_file)

    def test_missing_template_returns_none(self) -> None:
        self.assertIsNone(adapters.generic_spec("nieznany", {}))

    def test_builtin_and_resume_classification(self) -> None:
        self.assertTrue(adapters.is_builtin("codex"))
        self.assertFalse(adapters.is_builtin("grok"))
        self.assertTrue(adapters.supports_resume("codex"))
        self.assertFalse(adapters.supports_resume("claude"))
        self.assertFalse(adapters.supports_resume("grok"))


class ConfigRoleResolutionTest(unittest.TestCase):
    def test_roles_default_to_codex_with_inheritance(self) -> None:
        cfg = Config(codex_model="gpt-x", codex_effort="high")
        self.assertEqual(cfg.role("tester"), ("codex", "gpt-x", "high"))
        self.assertEqual(cfg.role("coder"), ("codex", "gpt-x", "high"))

    def test_codex_planner_backfills_empty_effort(self) -> None:
        # Pusty planner_effort dla codeksa dziedziczy codex_effort — inaczej
        # -c model_reasoning_effort="" wywala codeksa.
        cfg = Config(planner_agent="codex", planner_model="", planner_effort="",
                     codex_model="gpt-x", codex_effort="high")
        self.assertEqual(cfg.role("planner"), ("codex", "gpt-x", "high"))

    def test_generic_role_does_not_inherit_codex_model(self) -> None:
        cfg = Config(coder_agent="grok", codex_model="gpt-x")
        agent, model, effort = cfg.role("coder")
        self.assertEqual(agent, "grok")
        self.assertEqual(model, "")   # generic → nie dziedziczy modelu codeksa
        self.assertEqual(effort, "")

    def test_agents_in_use_reflects_mode(self) -> None:
        cfg = Config(planner_agent="claude", tester_agent="codex", coder_agent="grok")
        self.assertEqual(set(cfg.agents_in_use()), {"claude", "codex", "grok"})
        self.assertEqual(set(Config(legacy_mode=True).agents_in_use()), {"claude", "codex"})


class RunGenericAgentTest(unittest.TestCase):
    @patch.dict(os.environ, {"FORGE_AGENT_GROK_CMD": "grok --exec {prompt} --out {output}"})
    def test_generic_agent_output_file_path(self) -> None:
        from forge.agents import run_agent
        with tempfile.TemporaryDirectory() as project:
            captured = {}

            def fake_backoff(argv, cwd, cfg_, log, stdin_text=None):
                captured["argv"] = argv
                out_idx = argv.index("--out") + 1  # agent zapisuje wynik do {output}
                Path(argv[out_idx]).write_text("WYNIK GROKA", encoding="utf-8")
                return "stdout-ignored"

            with patch("forge.agents._run_with_backoff", side_effect=fake_backoff):
                out = run_agent("grok", "zrób X", Config(), project, "/tmp/log")

            self.assertEqual(out, "WYNIK GROKA")
            self.assertIn("grok", captured["argv"])
            self.assertIn("zrób X", captured["argv"])

    @patch.dict(os.environ, {"FORGE_AGENT_KIRO_CMD": "kiro run {prompt}"})
    def test_generic_agent_stdout_when_no_output_placeholder(self) -> None:
        from forge.agents import run_agent
        with tempfile.TemporaryDirectory() as project:
            with patch("forge.agents._run_with_backoff", return_value="odpowiedź na stdout"):
                out = run_agent("kiro", "hej", Config(), project, "/tmp/log")
            self.assertEqual(out, "odpowiedź na stdout")

    def test_unknown_agent_without_template_raises(self) -> None:
        from forge.agents import AgentError, run_agent
        with patch("forge.adapters.generic_spec", return_value=None):
            with self.assertRaisesRegex(AgentError, "FORGE_AGENT_NIEMA_CMD"):
                run_agent("niema", "p", Config(), "/tmp/p", "/tmp/log")


if __name__ == "__main__":
    unittest.main()
