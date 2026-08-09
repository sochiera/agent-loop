from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import pytest

from forge import routing
from forge.config import TASK_DIFFICULTIES
from forge.gui import (
    MASTER_AGENTS,
    ROLE_DEFS,
    ROOT,
    RoleCard,
    build_launch,
    level_hint,
    line_kind,
    load_settings,
    resolve_project,
    routing_path,
    save_settings,
    trim_log_buffer,
)
from forge.gui import Gtk

# Widgety wymagają zainicjowanego GTK; bez sesji graficznej testy logiki
# (ustawienia, argv, kolorowanie logu) nadal muszą się wykonać.
HAS_GTK = bool(Gtk.init_check())
needs_gtk = pytest.mark.skipif(not HAS_GTK, reason="brak sesji GTK")


def _routing(payload: dict) -> routing.Routing:
    return routing.parse(payload, TASK_DIFFICULTIES)


class GuiSettingsTest(unittest.TestCase):
    def test_settings_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "forge" / "gui.json"
            expected = {"brief": "/tmp/brief.md", "project": "/tmp/project"}

            save_settings(expected, path)

            self.assertEqual(load_settings(path), expected)
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_broken_settings_fall_back_to_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gui.json"
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(load_settings(path), {})


class GuiRoutingPathTest(unittest.TestCase):
    def test_environment_overrides_the_default_file(self) -> None:
        environ = {"FORGE_ROUTING_FILE": "/tmp/moje-routing.json"}
        self.assertEqual(routing_path(environ), Path("/tmp/moje-routing.json"))

    def test_disabled_routing_still_gives_gui_a_file_to_edit(self) -> None:
        # "none" wyłącza warstwę w orkiestratorze; GUI musi mimo to wiedzieć,
        # gdzie zapisać wybór, inaczej klikanie nie zostawiałoby śladu.
        environ = {"FORGE_ROUTING_FILE": "none", "XDG_CONFIG_HOME": "/tmp/cfg"}
        self.assertEqual(routing_path(environ),
                         Path("/tmp/cfg/forge/routing.json"))


class GuiLaunchTest(unittest.TestCase):
    def test_launch_points_the_orchestrator_at_the_routing_file(self) -> None:
        command, env = build_launch(
            "brief.md", "project", _routing({"roles": {"coder": {"agent": "codex"}}}),
            Path("/tmp/routing.json"))

        self.assertIn("--non-interactive", command)
        self.assertEqual(command[-4:], ["--brief", "brief.md", "--project", "project"])
        self.assertEqual(env["FORGE_ROUTING_FILE"], "/tmp/routing.json")

    def test_stale_role_variables_cannot_beat_the_gui(self) -> None:
        with patch.dict(
                os.environ, {"FORGE_CODER_AGENT": "grok",
                             "FORGE_TESTER_MODEL": "coś-starego"}):
            _command, env = build_launch(
                "brief.md", "project", _routing({"roles": {}}),
                Path("/tmp/routing.json"))

        self.assertNotIn("FORGE_CODER_AGENT", env)
        self.assertNotIn("FORGE_TESTER_MODEL", env)

    def test_codex_is_not_available_for_master(self) -> None:
        self.assertNotIn("codex", MASTER_AGENTS)

        with self.assertRaisesRegex(ValueError, "Codex nie jest dostępny"):
            build_launch("brief.md", "project",
                         _routing({"roles": {"master": {"agent": "codex"}}}))

    def test_master_cannot_smuggle_codex_through_the_fallback_chain(self) -> None:
        with self.assertRaisesRegex(ValueError, "Codex nie jest dostępny"):
            build_launch("brief.md", "project", _routing({"roles": {"master": {
                "agent": "opencode", "fallbacks": [{"agent": "codex"}]}}}))

    def test_missing_paths_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "brief"):
            build_launch("", "project", _routing({"roles": {}}))
        with self.assertRaisesRegex(ValueError, "katalog"):
            build_launch("brief.md", "  ", _routing({"roles": {}}))


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

    def test_switching_to_a_fallback_reads_as_a_warning(self) -> None:
        self.assertEqual(
            line_kind("  rola[coder]: opencode/x — limit; przełączam na zapas claude"),
            "warning")

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


class GuiHintTest(unittest.TestCase):
    def test_hint_shows_what_the_policy_would_pick(self) -> None:
        self.assertIn("sonnet", level_hint("coder", "complex", "claude"))

    def test_hint_admits_when_the_tool_decides_on_its_own(self) -> None:
        self.assertIn("decyduje sam", level_hint("coder", "simple", "aider"))


@needs_gtk
class RoleCardTest(unittest.TestCase):
    def _card(self, role: str = "coder") -> RoleCard:
        definition = next(item for item in ROLE_DEFS if item.name == role)
        return RoleCard(definition, "opencode", lambda: None)

    def test_product_owner_is_present_in_the_panel(self) -> None:
        self.assertIn("product_owner", {item.name for item in ROLE_DEFS})

    def test_difficulty_aware_role_offers_three_slots(self) -> None:
        self.assertEqual(set(self._card("coder").slots), set(TASK_DIFFICULTIES))

    def test_role_blind_to_difficulty_offers_one_slot(self) -> None:
        self.assertEqual(set(self._card("product_owner").slots),
                         {routing.ANY_DIFFICULTY})

    def test_choices_survive_a_round_trip(self) -> None:
        card = self._card("coder")
        entry = _routing({"roles": {"coder": {
            "agent": "claude",
            "slots": {"simple": {"model": "haiku", "effort": "low"},
                      "complex": {"model": "opus", "effort": "high"}},
            "fallbacks": [{"agent": "opencode",
                           "model": "neuralwatt/glm-5.2", "effort": "high"}],
        }}}).roles["coder"]

        card.apply(entry)

        self.assertEqual(card.routing_entry(), entry)

    def test_untouched_card_writes_no_agent_override(self) -> None:
        # Pokrętło pokazuje domyślnego agenta polityki. Zapisanie tej wartości
        # zamroziłoby politykę mimo braku decyzji operatora.
        self.assertEqual(self._card("coder").routing_entry().agent, "")

    def test_chosen_agent_is_written(self) -> None:
        card = self._card("coder")
        card.agent.set_selected(card.agents.index("claude"))

        self.assertEqual(card.routing_entry().agent, "claude")

    def test_model_outside_the_catalogue_is_kept(self) -> None:
        # Nowy model u dostawcy pojawia się wcześniej niż w naszym katalogu.
        card = self._card("tester")
        entry = _routing({"roles": {"tester": {
            "agent": "opencode",
            "slots": {"standard": {"model": "całkiem/nowy-model"}}}}}).roles["tester"]

        card.apply(entry)

        self.assertEqual(card.routing_entry().slots["standard"].model,
                         "całkiem/nowy-model")


if __name__ == "__main__":
    unittest.main()
