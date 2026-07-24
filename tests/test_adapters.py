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

    def test_gpt_alias_resolves_to_codex(self) -> None:
        self.assertEqual(adapters.canonical_agent("gpt"), "codex")
        self.assertEqual(adapters.canonical_agent("chatgpt"), "codex")
        self.assertEqual(adapters.canonical_agent("claude"), "claude")
        self.assertTrue(adapters.is_builtin("gpt"))
        self.assertTrue(adapters.supports_resume("gpt"))

    def test_grok_and_kiro_have_known_default_templates_without_env(self) -> None:
        grok_spec = adapters.generic_spec("grok", {})
        self.assertIsNotNone(grok_spec)
        self.assertEqual(adapters.generic_bin(grok_spec), "grok")
        kiro_spec = adapters.generic_spec("kiro", {})
        self.assertIsNotNone(kiro_spec)
        self.assertEqual(adapters.generic_bin(kiro_spec), "kiro-cli")

    def test_opencode_has_known_default_template_without_env(self) -> None:
        spec = adapters.generic_spec("opencode", {})
        self.assertIsNotNone(spec)
        self.assertEqual(adapters.generic_bin(spec), "opencode")
        # Model NeuralWatt bez --variant (effort pusty) — flaga znika w całości.
        argv = adapters.expand_template(
            spec.template,
            {"prompt": "zrob X", "model": "neuralwatt/glm-5.2", "effort": "",
             "project": "", "output": ""},
        )
        self.assertEqual(
            argv, ["opencode", "run", "zrob X", "-m", "neuralwatt/glm-5.2", "--auto"])
        # Model z effortem (GLM-5.2 wspiera --variant) — flaga zostaje.
        argv_effort = adapters.expand_template(
            spec.template,
            {"prompt": "zrob X", "model": "neuralwatt/glm-5.2", "effort": "high",
             "project": "", "output": ""},
        )
        self.assertEqual(
            argv_effort,
            ["opencode", "run", "zrob X", "-m", "neuralwatt/glm-5.2", "--variant", "high", "--auto"])

    def test_env_template_overrides_known_default(self) -> None:
        env = {"FORGE_AGENT_GROK_CMD": "moj-grok {prompt}"}
        spec = adapters.generic_spec("grok", env)
        self.assertEqual(adapters.generic_bin(spec), "moj-grok")


class ConfigRoleResolutionTest(unittest.TestCase):
    def test_codex_roles_follow_difficulty_matrix(self) -> None:
        cfg = Config(tester_agent="codex", coder_agent="codex",
                     tester_model="", coder_model="",
                     codex_model="gpt-x", codex_effort="high")
        self.assertEqual(cfg.role("tester", "simple"),
                         ("codex", "gpt-5.6-terra", "medium"))
        self.assertEqual(cfg.role("tester", "complex"),
                         ("codex", "gpt-5.6-sol", "medium"))
        self.assertEqual(cfg.role("coder", "simple"),
                         ("codex", "gpt-5.6-luna", "medium"))
        self.assertEqual(cfg.role("coder", "standard"),
                         ("codex", "gpt-5.6-terra", "low"))

    def test_codex_planner_is_always_strong(self) -> None:
        cfg = Config(planner_agent="codex", planner_model="", planner_effort="",
                     codex_model="gpt-x", codex_effort="high")
        self.assertEqual(cfg.role("planner"), ("codex", "gpt-5.6-sol", "high"))

    def test_known_generic_role_uses_fixed_matrix(self) -> None:
        cfg = Config(coder_agent="grok", coder_model="", codex_model="gpt-x")
        agent, model, effort = cfg.role("coder")
        self.assertEqual(agent, "grok")
        self.assertEqual(model, "grok-4.5")
        self.assertEqual(effort, "medium")

    def test_unknown_generic_role_keeps_legacy_fields(self) -> None:
        cfg = Config(coder_agent="my-cli", coder_model="my-model",
                     coder_effort="cheap", codex_model="gpt-x")
        self.assertEqual(cfg.role("coder"), ("my-cli", "my-model", "cheap"))

    def test_agents_in_use_reflects_mode(self) -> None:
        cfg = Config(planner_agent="claude", tester_agent="codex", coder_agent="grok",
                     reviewer_agent="", verifier_agent="")
        self.assertEqual(set(cfg.agents_in_use()), {"claude", "codex", "grok"})
        self.assertEqual(set(Config(legacy_mode=True).agents_in_use()), {"claude", "codex"})

    def test_gpt_alias_uses_codex_matrix(self) -> None:
        cfg = Config(tester_agent="gpt", tester_model="", codex_model="gpt-x", codex_effort="high")
        self.assertEqual(cfg.role("tester"), ("gpt", "gpt-5.6-terra", "medium"))

    def test_reviewer_alias_uses_reviewer_matrix(self) -> None:
        cfg = Config(tester_agent="codex", tester_model="custom-m", tester_effort="high",
                     reviewer_agent="gpt", reviewer_model="",
                     codex_model="fallback-m", codex_effort="low")
        self.assertEqual(cfg.role("reviewer"), ("gpt", "gpt-5.6-sol", "medium"))

    def test_agents_in_use_dedups_aliases(self) -> None:
        # planner=gpt i tester=codex to jedna binarka — preflight nie może jej
        # liczyć dwa razy (ani dublować komunikatu o braku).
        cfg = Config(planner_agent="gpt", tester_agent="codex", coder_agent="codex",
                     reviewer_agent="", verifier_agent="")
        canon = [adapters.canonical_agent(a) for a in cfg.agents_in_use()]
        self.assertEqual(len(canon), len(set(canon)))
        self.assertEqual(set(canon), {"codex"})


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
