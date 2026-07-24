from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from forge.gui import (
    build_launch,
    line_kind,
    load_settings,
    save_settings,
    trim_log_buffer,
)
from forge.gui import Gtk


class GuiSettingsTest(unittest.TestCase):
    def test_settings_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "forge" / "gui.json"
            expected = {
                "brief": "/tmp/brief.md",
                "project": "/tmp/project",
                "roles": {"planner": {"agent": "codex"}},
            }

            save_settings(expected, path)

            self.assertEqual(load_settings(path), expected)
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_broken_settings_fall_back_to_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gui.json"
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(load_settings(path), {})


class GuiLaunchTest(unittest.TestCase):
    def test_launch_uses_argv_and_role_environment(self) -> None:
        roles = {
            role: {"agent": "codex"}
            for role in ("planner", "tester", "coder", "reviewer", "verifier")
        }

        command, env = build_launch("brief.md", "project", roles)

        self.assertIn("--non-interactive", command)
        self.assertEqual(command[-4:], ["--brief", "brief.md", "--project", "project"])
        self.assertEqual(env["FORGE_CODER_AGENT"], "codex")
        self.assertNotIn("FORGE_CODER_MODEL", env)
        self.assertNotIn("FORGE_REVIEWER_EFFORT", env)

    def test_invalid_multiline_value_is_rejected(self) -> None:
        roles = {
            role: {"agent": "codex"}
            for role in ("planner", "tester", "coder", "reviewer", "verifier")
        }
        roles["tester"]["agent"] = "bad\nvalue"

        with self.assertRaisesRegex(ValueError, "tester.agent"):
            build_launch("brief.md", "project", roles)


class GuiStatusTest(unittest.TestCase):
    def test_log_lines_get_semantic_colours(self) -> None:
        self.assertEqual(line_kind("Testy: ZIELONE"), "success")
        self.assertEqual(line_kind("BŁĄD AGENTA: awaria"), "error")
        self.assertEqual(line_kind("--- PLAN WSADOWY ---"), "phase")

    def test_log_buffer_discards_oldest_lines(self) -> None:
        buffer = Gtk.TextBuffer()
        buffer.set_text("one\ntwo\nthree\nfour\n")

        trim_log_buffer(buffer, max_lines=3)

        text = buffer.get_text(
            buffer.get_start_iter(), buffer.get_end_iter(), False
        )
        self.assertNotIn("one", text)
        self.assertIn("four", text)
        self.assertLessEqual(buffer.get_line_count(), 3)


if __name__ == "__main__":
    unittest.main()
