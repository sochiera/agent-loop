from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import pytest

from forge import catalog, routing
from forge.config import Config, TASK_DIFFICULTIES
from forge.gui import (
    AGENTS,
    MASTER_AGENTS,
    ModelChooser,
    ROLE_DEFS,
    ROOT,
    RoleCard,
    build_launch,
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


@needs_gtk
class ModelChooserTest(unittest.TestCase):
    """Sedno panelu: wybiera się MODEL, a narzędzie z niego wynika."""

    def _chooser(self, role: str = "coder",
                 agents: tuple[str, ...] = AGENTS) -> ModelChooser:
        return ModelChooser(role, "standard", agents, catalog.index({}))

    def _pick(self, chooser: ModelChooser, name: str) -> None:
        chooser.model.set_selected(next(
            index for index, choice in enumerate(chooser.choices())
            if choice.entry is not None and choice.entry.name == name))

    def test_there_is_only_one_dropdown(self) -> None:
        chooser = self._chooser()

        self._pick(chooser, "haiku")

        self.assertEqual(chooser.value(), ("claude", "haiku", ""))
        self.assertFalse(hasattr(chooser, "provider"))

    def test_native_codex_and_grok_are_not_offered(self) -> None:
        self.assertNotIn("codex", AGENTS)
        self.assertNotIn("grok", AGENTS)

    def test_gpt_uses_only_opencode(self) -> None:
        chooser = self._chooser()

        self._pick(chooser, "gpt-5.6-luna")

        self.assertEqual(chooser.value(),
                         ("opencode", "openai/gpt-5.6-luna", "medium"))

    def test_model_and_effort_are_one_choice(self) -> None:
        chooser = self._chooser()

        chooser.set_value("opencode", "openai/gpt-5.6-luna", "max")

        self.assertEqual(chooser.value(),
                         ("opencode", "openai/gpt-5.6-luna", "max"))
        self.assertFalse(hasattr(chooser, "effort"))

    def test_only_concrete_model_effort_choices_are_offered(self) -> None:
        chooser = self._chooser()

        self.assertEqual({choice.kind for choice in chooser.choices()}, {"model"})
        self.assertTrue(all(choice.entry is not None for choice in chooser.choices()))

    def test_custom_entry_is_not_offered(self) -> None:
        chooser = self._chooser()

        self.assertNotIn("custom", {choice.kind for choice in chooser.choices()})
        self.assertNotIn("wpisz własny", " ".join(
            choice.label for choice in chooser.choices()).lower())

    def test_saved_choices_survive_a_round_trip(self) -> None:
        chooser = self._chooser()
        for saved in (("claude", "opus", "high"),
                      ("opencode", "openai/gpt-5.6-luna", "max"),
                      ("opencode", "calkiem/nowy-model", "")):
            with self.subTest(saved=saved):
                chooser.set_value(*saved)
                self.assertEqual(chooser.value(), saved)

    def test_role_forbidding_codex_offers_only_the_bridge(self) -> None:
        chooser = self._chooser("master", MASTER_AGENTS)

        self._pick(chooser, "gpt-5.6-luna")

        self.assertEqual(chooser.value()[0], "opencode")
        self.assertIn("gpt-5.6-sol",
                      [choice.entry.name for choice in chooser.choices()
                       if choice.entry is not None])

    def test_saved_native_routes_are_migrated_to_opencode(self) -> None:
        chooser = self._chooser()

        chooser.set_value("codex", "gpt-5.6-sol", "xhigh")
        self.assertEqual(chooser.value(),
                         ("opencode", "openai/gpt-5.6-sol", "max"))

        chooser.set_value("grok", "grok-4.5", "high")
        self.assertEqual(chooser.value(),
                         ("opencode", "xai/grok-4.5", "high"))


@needs_gtk
class RoleCardTest(unittest.TestCase):
    def _card(self, role: str = "coder") -> RoleCard:
        definition = next(item for item in ROLE_DEFS if item.name == role)
        return RoleCard(definition, Config(routing=routing.Routing()),
                        lambda: None)

    def _resolves_alike(self, role: str, first: routing.RoleRouting,
                        second: routing.RoleRouting) -> None:
        before = Config(routing=routing.Routing(roles={role: first}))
        after = Config(routing=routing.Routing(roles={role: second}))
        for difficulty in TASK_DIFFICULTIES:
            self.assertEqual(after.role_chain(role, difficulty),
                             before.role_chain(role, difficulty))

    def test_product_owner_is_present_in_the_panel(self) -> None:
        self.assertIn("product_owner", {item.name for item in ROLE_DEFS})

    def test_difficulty_aware_role_offers_three_slots(self) -> None:
        self.assertEqual(set(self._card("coder").slots), set(TASK_DIFFICULTIES))

    def test_role_blind_to_difficulty_offers_one_slot(self) -> None:
        self.assertEqual(set(self._card("product_owner").slots),
                         {routing.ANY_DIFFICULTY})

    def test_master_offers_exactly_one_slot(self) -> None:
        self.assertEqual(set(self._card("master").slots),
                         {routing.ANY_DIFFICULTY})

    def test_master_migrates_old_three_level_selection(self) -> None:
        card = self._card("master")
        entry = _routing({"roles": {"master": {"slots": {
            "simple": {"agent": "opencode", "model": "openai/gpt-5.6-luna",
                       "effort": "medium"},
            "standard": {"agent": "opencode", "model": "openai/gpt-5.6-sol",
                         "effort": "high"},
            "complex": {"agent": "opencode", "model": "zai-coding-plan/glm-5.2",
                        "effort": "high"},
        }}}}).roles["master"]

        card.apply(entry)

        self.assertEqual(card.routing_entry().slots[routing.ANY_DIFFICULTY],
                         routing.Endpoint(agent="opencode",
                                          model="openai/gpt-5.6-sol",
                                          effort="high"))

    def test_choices_survive_a_round_trip(self) -> None:
        card = self._card("coder")
        entry = _routing({"roles": {"coder": {
            "slots": {"simple": {"agent": "claude", "model": "haiku",
                                 "effort": "low"},
                      "complex": {"agent": "claude", "model": "opus",
                                  "effort": "high"}},
            "fallbacks": [{"agent": "opencode",
                           "model": "zai-coding-plan/glm-5.2", "effort": "high"}],
        }}}).roles["coder"]

        card.apply(entry)

        restored = card.routing_entry()
        self.assertEqual(restored.slots["simple"], entry.slots["simple"])
        self.assertEqual(restored.slots["complex"], entry.slots["complex"])
        self.assertEqual(restored.fallbacks, entry.fallbacks)
        self.assertIn("standard", restored.slots)
        self._resolves_alike("coder", entry, restored)

    def test_tool_of_the_whole_role_survives_as_slot_tools(self) -> None:
        # Starszy plik (i ręczna edycja) opisuje narzędzie raz dla całej roli.
        # Panel rozpisuje je na sloty — wynik routingu musi zostać ten sam.
        card = self._card("coder")
        entry = _routing({"roles": {"coder": {
            "agent": "claude",
            "slots": {"simple": {"model": "haiku"}}}}}).roles["coder"]

        card.apply(entry)

        self._resolves_alike("coder", entry, card.routing_entry())

    def test_new_card_starts_with_concrete_configured_models(self) -> None:
        entry = self._card("coder").routing_entry()

        self.assertEqual(entry.slots["simple"], routing.Endpoint(
            agent="opencode", model="openai/gpt-5.6-luna", effort="medium"))
        self.assertTrue(all(endpoint.model for endpoint in entry.slots.values()))

    def test_chosen_model_writes_its_tool_into_the_slot(self) -> None:
        card = self._card("coder")
        chooser = card.slots["simple"]
        chooser.model.set_selected(next(
            index for index, choice in enumerate(chooser.choices())
            if choice.entry is not None and choice.entry.name == "opus"))

        self.assertEqual(card.routing_entry().slots["simple"],
                         routing.Endpoint(agent="claude", model="opus",
                                          effort="medium"))

    def test_model_outside_the_catalogue_is_kept(self) -> None:
        # Nowy model u dostawcy pojawia się wcześniej niż w naszym katalogu.
        card = self._card("tester")
        entry = _routing({"roles": {"tester": {
            "agent": "opencode",
            "slots": {"standard": {"model": "całkiem/nowy-model"}}}}}).roles["tester"]

        card.apply(entry)

        self.assertEqual(card.routing_entry().slots["standard"],
                         routing.Endpoint(agent="opencode",
                                          model="całkiem/nowy-model"))


if __name__ == "__main__":
    unittest.main()
