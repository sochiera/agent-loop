from __future__ import annotations

import unittest

from forge.config import Config


class VerifierConfigTest(unittest.TestCase):
    def test_verifier_defaults_to_planner_role(self) -> None:
        cfg = Config()
        cfg.planner_agent, cfg.planner_model, cfg.planner_effort = "claude", "opus", "high"
        cfg.verifier_agent = ""

        self.assertEqual(cfg.role("verifier"), ("claude", "opus", "high"))

    def test_explicit_verifier_does_not_inherit_planner_model(self) -> None:
        cfg = Config()
        cfg.planner_agent, cfg.planner_model = "claude", "opus"
        cfg.verifier_agent, cfg.verifier_model, cfg.verifier_effort = "codex", "", "low"
        cfg.codex_model = "gpt-test"

        # codex z pustym modelem dziedziczy codex_model (jak tester/koder), nie opusa.
        self.assertEqual(cfg.role("verifier"), ("codex", "gpt-test", "low"))

    def test_agents_in_use_includes_explicit_verifier(self) -> None:
        cfg = Config()
        cfg.legacy_mode = False
        cfg.planner_agent = "claude"
        cfg.tester_agent = cfg.coder_agent = "codex"
        cfg.verifier_agent = "grok"

        self.assertEqual(cfg.agents_in_use(), ["claude", "codex", "grok"])

    def test_agents_in_use_deduplicates_default_verifier(self) -> None:
        cfg = Config()
        cfg.legacy_mode = False
        cfg.planner_agent = "claude"
        cfg.tester_agent = cfg.coder_agent = "codex"
        cfg.verifier_agent = ""

        self.assertEqual(cfg.agents_in_use(), ["claude", "codex"])

    def test_targets_override_none_disables_and_csv_replaces(self) -> None:
        cfg = Config()
        cfg.verify_targets_override = "none"
        self.assertEqual(cfg.effective_verify_targets(["ci", "smoke"]), [])

        cfg.verify_targets_override = "ci, hardware"
        self.assertEqual(cfg.effective_verify_targets(["smoke"]), ["ci", "hardware"])

        cfg.verify_targets_override = ""
        self.assertEqual(cfg.effective_verify_targets(["smoke"]), ["smoke"])

    def test_verify_knobs_have_sane_defaults(self) -> None:
        cfg = Config()
        self.assertGreaterEqual(cfg.max_verify_cycles, cfg.max_stall_cycles)
        self.assertGreater(cfg.ci_timeout_s, cfg.ci_poll_max_s)
        self.assertGreaterEqual(cfg.flash_retries, 1)
        self.assertGreater(cfg.max_repro_runs_per_task, 1)
        self.assertTrue(cfg.ci_early_warn)


if __name__ == "__main__":
    unittest.main()
