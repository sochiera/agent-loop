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
class ModelChooserTest(unittest.TestCase):
    """Sedno panelu: wybiera się MODEL, a narzędzie z niego wynika."""

    def _chooser(self, role: str = "coder",
                 agents: tuple[str, ...] = AGENTS) -> ModelChooser:
        return ModelChooser(role, "standard", agents, catalog.index({}))

    def _pick(self, chooser: ModelChooser, name: str) -> None:
        chooser.model.set_selected(next(
            index for index, choice in enumerate(chooser.choices())
            if choice.entry is not None and choice.entry.name == name))

    def _pick_kind(self, chooser: ModelChooser, kind: str, agent: str = "") -> None:
        chooser.model.set_selected(next(
            index for index, choice in enumerate(chooser.choices())
            if choice.kind == kind and (not agent or choice.agent == agent)))

    def test_model_with_one_route_needs_no_provider_question(self) -> None:
        chooser = self._chooser()

        self._pick(chooser, "haiku")

        self.assertFalse(chooser.provider.get_visible())
        self.assertEqual(chooser.value(), ("claude", "haiku", ""))

    def test_model_with_several_routes_asks_which_provider(self) -> None:
        chooser = self._chooser()

        self._pick(chooser, "gpt-5.6-luna")

        self.assertTrue(chooser.provider.get_visible())
        # Natywne CLI przed mostem: telemetria i wznawianie sesji są tam darmowe.
        self.assertEqual(chooser.value(), ("codex", "gpt-5.6-luna", ""))

        chooser.provider.set_selected(1)

        self.assertEqual(chooser.value(),
                         ("opencode", "openai/gpt-5.6-luna", ""))

    def test_effort_list_follows_the_tool_of_the_chosen_route(self) -> None:
        # xhigh istnieje tylko u Codeksa; przeniesione na Claude'a byłoby flagą,
        # której jego CLI nie zna.
        chooser = self._chooser()

        chooser.set_value("codex", "gpt-5.6-luna", "xhigh")
        self.assertEqual(chooser.value(), ("codex", "gpt-5.6-luna", "xhigh"))

        chooser.set_value("claude", "opus", "xhigh")
        self.assertEqual(chooser.value(), ("claude", "opus", ""))

    def test_tool_without_a_model_stays_reachable(self) -> None:
        # „Ten sam poziom, inne CLI" to jedyny sens wpisu bez modelu — po
        # usunięciu pokrętła narzędzia musi mieć swoją pozycję na liście.
        chooser = self._chooser()

        self._pick_kind(chooser, "policy", "grok")

        self.assertEqual(chooser.value(), ("grok", "", ""))

    def test_default_choice_overrides_nothing(self) -> None:
        self.assertEqual(self._chooser().value(), ("", "", ""))

    def test_custom_name_always_asks_for_the_tool(self) -> None:
        # Nazwy spoza katalogu nie da się przypisać do narzędzia — musi wskazać
        # je operator, a pierwsza pozycja zostawia wybór roli.
        chooser = self._chooser()

        self._pick_kind(chooser, "custom")
        chooser.custom.set_text("calkiem/nowy-model")

        self.assertTrue(chooser.provider.get_visible())
        self.assertEqual(chooser.value(), ("", "calkiem/nowy-model", ""))

        chooser.provider.set_selected(AGENTS.index("opencode") + 1)

        self.assertEqual(chooser.value(),
                         ("opencode", "calkiem/nowy-model", ""))

    def test_saved_choices_survive_a_round_trip(self) -> None:
        chooser = self._chooser()
        for saved in (("claude", "opus", "high"),
                      ("opencode", "openai/gpt-5.6-luna", "max"),
                      ("opencode", "calkiem/nowy-model", ""),
                      ("grok", "", ""),
                      ("", "", "low"),
                      ("", "", "")):
            with self.subTest(saved=saved):
                chooser.set_value(*saved)
                self.assertEqual(chooser.value(), saved)

    def test_operator_own_cli_is_not_lost_on_save(self) -> None:
        # Agent z szablonu FORGE_AGENT_<NAZWA>_CMD nie ma pozycji w katalogu.
        chooser = self._chooser()

        chooser.set_value("aider", "", "")

        self.assertEqual(chooser.value(), ("aider", "", ""))

    def test_role_forbidding_codex_offers_only_the_bridge(self) -> None:
        chooser = self._chooser("master", MASTER_AGENTS)

        self._pick(chooser, "gpt-5.6-luna")

        self.assertFalse(chooser.provider.get_visible())
        self.assertEqual(chooser.value()[0], "opencode")
        self.assertNotIn("gpt-5.6-sol",
                         [choice.entry.name for choice in chooser.choices()
                          if choice.entry is not None])


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

        self.assertEqual(card.routing_entry(), entry)

    def test_tool_of_the_whole_role_survives_as_slot_tools(self) -> None:
        # Starszy plik (i ręczna edycja) opisuje narzędzie raz dla całej roli.
        # Panel rozpisuje je na sloty — wynik routingu musi zostać ten sam.
        card = self._card("coder")
        entry = _routing({"roles": {"coder": {
            "agent": "claude",
            "slots": {"simple": {"model": "haiku"}}}}}).roles["coder"]

        card.apply(entry)

        self._resolves_alike("coder", entry, card.routing_entry())

    def test_untouched_card_overrides_nothing(self) -> None:
        # Panel pokazuje wybór polityki; zapisanie go jako nadpisania
        # ZAMROZIŁOBY politykę mimo braku decyzji operatora.
        self.assertEqual(self._card("coder").routing_entry(),
                         routing.RoleRouting())

    def test_chosen_model_writes_its_tool_into_the_slot(self) -> None:
        card = self._card("coder")
        chooser = card.slots["simple"]
        chooser.model.set_selected(next(
            index for index, choice in enumerate(chooser.choices())
            if choice.entry is not None and choice.entry.name == "opus"))

        self.assertEqual(card.routing_entry().slots["simple"],
                         routing.Endpoint(agent="claude", model="opus"))

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
