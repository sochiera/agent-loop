from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from forge.report import (format_table, load_records, normalize_phase,
                          summarize, usage_summary)


class PhaseNormalizationTest(unittest.TestCase):
    def test_micro_cycle_phases_collapse_to_stable_groups(self) -> None:
        self.assertEqual(normalize_phase("c01-test"), "micro-test")
        self.assertEqual(normalize_phase("c12-code0"), "micro-code")
        self.assertEqual(normalize_phase("review-r2"), "review")
        self.assertEqual(normalize_phase("review-fix3"), "review-fix")
        self.assertEqual(normalize_phase("plan"), "plan")
        self.assertEqual(normalize_phase("bootstrap"), "bootstrap")
        self.assertEqual(normalize_phase(""), "unknown")


class SummarizeTest(unittest.TestCase):
    def test_sums_tokens_per_agent_and_phase_group(self) -> None:
        records = [
            {"agent": "codex", "phase": "c01-test",
             "usage": {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 10}},
            {"agent": "codex", "phase": "c02-test",
             "usage": {"input_tokens": 50, "cached_input_tokens": 45, "output_tokens": 5}},
            {"agent": "claude", "phase": "plan",
             "usage": {"input_tokens": 900, "cache_read_input_tokens": 800,
                       "output_tokens": 90}},  # klucze Claude'a
        ]
        rows = summarize(records)
        codex = rows[("codex", "micro-test")]
        self.assertEqual(codex["calls"], 2)
        self.assertEqual(codex["in"], 150)
        self.assertEqual(codex["cached"], 85)
        claude = rows[("claude", "plan")]
        self.assertEqual(claude["cached"], 800)

    def test_table_contains_totals_row(self) -> None:
        rows = summarize([{"agent": "codex", "phase": "c01-code0",
                           "usage": {"input_tokens": 7, "output_tokens": 3}}])
        table = format_table(rows)
        self.assertIn("micro-code", table)
        self.assertIn("RAZEM", table)


class EndToEndReportTest(unittest.TestCase):
    def test_reads_jsonl_and_survives_garbage_lines(self) -> None:
        with tempfile.TemporaryDirectory() as project:
            forge_dir = Path(project) / ".forge"
            forge_dir.mkdir()
            lines = [
                json.dumps({"agent": "codex", "phase": "c01-test",
                            "usage": {"input_tokens": 5, "output_tokens": 1}}),
                "to nie jest json",
                json.dumps({"agent": "claude", "phase": "plan",
                            "usage": {"input_tokens": 10, "output_tokens": 2}}),
            ]
            (forge_dir / "usage.jsonl").write_text("\n".join(lines), encoding="utf-8")

            records = load_records(str(forge_dir / "usage.jsonl"))
            self.assertEqual(len(records), 2)
            table = usage_summary(project)
            self.assertIn("plan", table)
            self.assertIn("micro-test", table)

    def test_missing_file_yields_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as project:
            self.assertIn("brak danych", usage_summary(project))


if __name__ == "__main__":
    unittest.main()


class VerifyPhaseGroupTest(unittest.TestCase):
    def test_verify_logs_group_under_verify(self) -> None:
        from forge.report import normalize_phase
        self.assertEqual(normalize_phase("verify-c2-a0"), "verify")
        self.assertEqual(normalize_phase("verify-c11-a1"), "verify")
