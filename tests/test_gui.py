from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from forge.gui import (
    MASTER_AGENTS,
    ROOT,
    build_launch,
    line_kind,
    load_settings,
    resolve_project,
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


_ROLES = ("planner", "tester", "coder", "reviewer", "verifier", "master")


class GuiLaunchTest(unittest.TestCase):
    def test_launch_uses_argv_and_role_environment(self) -> None:
        roles = {role: {"agent": "codex"} for role in _ROLES}
        roles["master"]["agent"] = "opencode"

        command, env = build_launch("brief.md", "project", roles)

        self.assertIn("--non-interactive", command)
        self.assertEqual(command[-4:], ["--brief", "brief.md", "--project", "project"])
        self.assertEqual(env["FORGE_CODER_AGENT"], "codex")
        self.assertNotIn("FORGE_CODER_MODEL", env)
        self.assertNotIn("FORGE_REVIEWER_EFFORT", env)

    def test_master_is_configurable_from_gui(self) -> None:
        roles = {role: {"agent": "codex"} for role in _ROLES}
        roles["master"]["agent"] = "claude"

        _command, env = build_launch("brief.md", "project", roles)

        self.assertEqual(env["FORGE_MASTER_AGENT"], "claude")

    def test_codex_is_not_available_for_master(self) -> None:
        self.assertNotIn("codex", MASTER_AGENTS)
        roles = {role: {"agent": "claude"} for role in _ROLES}
        roles["master"]["agent"] = "codex"

        with self.assertRaisesRegex(ValueError, "Codex nie jest dostępny"):
            build_launch("brief.md", "project", roles)

    def test_invalid_multiline_value_is_rejected(self) -> None:
        roles = {role: {"agent": "codex"} for role in _ROLES}
        roles["tester"]["agent"] = "bad\nvalue"

        with self.assertRaisesRegex(ValueError, "tester.agent"):
            build_launch("brief.md", "project", roles)


class GuiResolveProjectTest(unittest.TestCase):
    def test_relative_project_resolves_against_root(self) -> None:
        # Podprocess orkiestratora startuje z cwd=ROOT, więc ścieżka względna
        # projektu w GUI musi rozwiązywać się tak samo, inaczej odczyt
        # poprzedniego logu/statystyk trafi w złe miejsce.
        self.assertEqual(resolve_project("game"), ROOT / "game")

    def test_absolute_project_is_unchanged(self) -> None:
        self.assertEqual(resolve_project("/tmp/some-project"), Path("/tmp/some-project"))


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
