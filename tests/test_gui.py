from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import pytest

from forge import catalog, gui, profiles, routing, runlock
from forge.config import Config, TASK_DIFFICULTIES
from forge.profiles import SHARED_SLUG as SHARED
from forge.gui import (
    AGENTS,
    DEFAULT_BRIEF,
    DEFAULT_PROJECT,
    MASTER_AGENTS,
    MAX_RUNS,
    ModelChooser,
    ROLE_DEFS,
    ROOT,
    RoleCard,
    build_launch,
    line_kind,
    load_settings,
    resolve_project,
    routing_path,
    run_settings,
    save_settings,
    trim_log_buffer,
)
from forge.gui import Adw, Gtk

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


class GuiRunSettingsTest(unittest.TestCase):
    def test_a_single_run_is_migrated_from_the_flat_file(self) -> None:
        # Stary plik opisywał jeden bieg; utrata tego wyboru przy pierwszym
        # uruchomieniu nowej wersji byłaby regresją, nie „czystym startem".
        self.assertEqual(
            run_settings({"brief": "/tmp/b.md", "project": "/tmp/p"}),
            [{"brief": "/tmp/b.md", "project": "/tmp/p", "profile": SHARED}])

    def test_empty_settings_give_one_default_run(self) -> None:
        self.assertEqual(run_settings({}),
                         [{"brief": DEFAULT_BRIEF, "project": DEFAULT_PROJECT,
                           "profile": SHARED}])

    def test_two_runs_survive_a_round_trip(self) -> None:
        runs = [{"brief": "a.md", "project": "/tmp/a", "profile": "tylko-gpt"},
                {"brief": "b.md", "project": "/tmp/b", "profile": SHARED}]
        self.assertEqual(run_settings({"runs": runs}), runs)

    def test_more_runs_than_the_panel_allows_are_cut(self) -> None:
        runs = [{"brief": f"{i}.md", "project": f"/tmp/{i}"}
                for i in range(MAX_RUNS + 3)]
        self.assertEqual(len(run_settings({"runs": runs})), MAX_RUNS)

    def test_damaged_entries_fall_back_to_defaults(self) -> None:
        self.assertEqual(
            run_settings({"runs": [{"brief": 7}, "śmieć"]}),
            [{"brief": DEFAULT_BRIEF, "project": DEFAULT_PROJECT,
              "profile": SHARED}])

    def test_a_run_without_a_profile_keeps_the_shared_one(self) -> None:
        # Plik sprzed profili opisuje biegi bez tego klucza. Każda inna wartość
        # niż wspólny znaczyłaby, że aktualizacja Forge po cichu zmieniła
        # modele, którymi pracuje projekt.
        self.assertEqual(
            run_settings({"runs": [{"brief": "a.md", "project": "/tmp/a"}]}),
            [{"brief": "a.md", "project": "/tmp/a", "profile": SHARED}])

    def test_a_damaged_profile_value_is_ignored(self) -> None:
        self.assertEqual(
            run_settings({"runs": [{"brief": "a.md", "project": "/tmp/a",
                                    "profile": 7}]})[0]["profile"], SHARED)


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
        launch = build_launch(
            "brief.md", "project", _routing({"roles": {"coder": {"agent": "codex"}}}),
            Path("/tmp/routing.json"))

        self.assertIn("--non-interactive", launch.command)
        self.assertEqual(launch.env["FORGE_ROUTING_FILE"], "/tmp/routing.json")

    def test_paths_reach_the_orchestrator_absolute(self) -> None:
        # Proces startuje z katalogu MIGAWKI kodu, więc „game" przekazane
        # dosłownie wskazywałoby katalog wewnątrz migawki, a nie projekt.
        launch = build_launch("brief.md", "project", _routing({"roles": {}}))

        self.assertEqual(launch.command[-4:],
                         ["--brief", str(ROOT / "brief.md"),
                          "--project", str(ROOT / "project")])

    def test_absolute_paths_are_passed_through(self) -> None:
        launch = build_launch("/tmp/b.md", "/tmp/p", _routing({"roles": {}}))

        self.assertEqual(launch.command[-4:],
                         ["--brief", "/tmp/b.md", "--project", "/tmp/p"])

    def test_run_starts_from_the_code_snapshot(self) -> None:
        launch = build_launch("brief.md", "project", _routing({"roles": {}}),
                              None, Path("/tmp/kod-biegu"))

        self.assertEqual(launch.cwd, Path("/tmp/kod-biegu"))
        self.assertEqual(launch.env["PYTHONPATH"].split(os.pathsep)[0],
                         "/tmp/kod-biegu")

    def test_snapshot_wins_over_an_inherited_pythonpath(self) -> None:
        with patch.dict(os.environ, {"PYTHONPATH": "/opt/cudze"}):
            launch = build_launch("brief.md", "project", _routing({"roles": {}}),
                                  None, Path("/tmp/kod-biegu"))

        self.assertEqual(launch.env["PYTHONPATH"],
                         f"/tmp/kod-biegu{os.pathsep}/opt/cudze")

    def test_without_a_snapshot_the_repository_is_used(self) -> None:
        launch = build_launch("brief.md", "project", _routing({"roles": {}}))

        self.assertEqual(launch.cwd, ROOT)

    def test_stale_role_variables_cannot_beat_the_gui(self) -> None:
        with patch.dict(
                os.environ, {"FORGE_CODER_AGENT": "grok",
                             "FORGE_TESTER_MODEL": "coś-starego"}):
            launch = build_launch(
                "brief.md", "project", _routing({"roles": {}}),
                Path("/tmp/routing.json"))

        self.assertNotIn("FORGE_CODER_AGENT", launch.env)
        self.assertNotIn("FORGE_TESTER_MODEL", launch.env)

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

        # Pierwsza pozycja modelu to jego najtańszy effort — lista rośnie
        # od `low`, tak samo jak dla rodziny Claude.
        self.assertEqual(chooser.value(),
                         ("opencode", "openai/gpt-5.6-luna", "low"))

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


class _FakeProcess:
    """Proces, który „pracuje" — tyle, ile widzi z niego panel."""

    pid = -1

    def poll(self) -> None:
        return None


@needs_gtk
class ParallelRunsTest(unittest.TestCase):
    """Dwa projekty w jednym oknie: własny log, własny stan, wspólne strażniki."""

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.first = self.root / "alfa"
        self.second = self.root / "beta"
        for project in (self.first, self.second):
            project.mkdir()
        self.saved: list[dict] = []
        self.settings = {"runs": [
            {"brief": "a.md", "project": str(self.first), "profile": SHARED},
            {"brief": "b.md", "project": str(self.second), "profile": SHARED},
        ]}
        patches = (
            patch("forge.gui.load_settings", return_value=self.settings),
            patch("forge.gui.save_settings",
                  side_effect=lambda payload, *_a, **_k: self.saved.append(payload)),
            patch("forge.gui.routing_path", return_value=self.root / "routing.json"),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self._directory.cleanup)
        self.window = gui.ForgeWindow(Adw.Application(
            application_id="pl.agentloop.ForgeTest"))
        self.addCleanup(self.window.destroy)

    @staticmethod
    def _log(run: gui.Run) -> str:
        buffer = run.log_buffer
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)

    def test_the_panel_opens_both_saved_runs(self) -> None:
        self.assertEqual([run.project.get_text() for run in self.window.runs],
                         [str(self.first), str(self.second)])
        self.assertEqual([run.title() for run in self.window.runs],
                         ["alfa", "beta"])

    def test_each_run_writes_to_its_own_log(self) -> None:
        self.window.runs[0].append_log("tylko dla alfy")

        self.assertIn("tylko dla alfy", self._log(self.window.runs[0]))
        self.assertEqual(self._log(self.window.runs[1]), "")

    def test_stopping_one_run_leaves_the_other_alone(self) -> None:
        working, idle = self.window.runs
        working.process = _FakeProcess()

        idle.stop()

        self.assertTrue(working.is_running())
        self.assertFalse(working.stop_requested)

    def test_the_same_project_twice_is_refused(self) -> None:
        first, second = self.window.runs
        first.process = _FakeProcess()
        second.project.set_text(str(self.first))

        self.assertIn("prowadzi już inny bieg",
                      self.window.blocking_problem(second))

    def test_a_project_locked_by_another_process_is_refused(self) -> None:
        with runlock.acquire(str(self.second)):
            problem = self.window.blocking_problem(self.window.runs[1])

        self.assertIn("prowadzi już bieg Forge", problem)

    def test_a_session_held_elsewhere_blocks_even_the_first_run(self) -> None:
        # Plikową sesję Claude Code może trzymać drugie okno albo bieg
        # z powłoki, więc pytamy zawsze — nie tylko o drugi bieg w tym oknie.
        with patch("forge.gui.preflight.claude_file_session_busy",
                   return_value="sesję trzyma inny bieg"):
            problem = self.window.blocking_problem(self.window.runs[0])

        self.assertEqual(problem, "sesję trzyma inny bieg")

    def test_the_routing_snapshot_lands_next_to_the_project(self) -> None:
        chosen = _routing({"roles": {"coder": {
            "slots": {"standard": {"agent": "claude", "model": "opus"}}}}})

        path = self.window.runs[0].routing_snapshot(chosen)

        self.assertEqual(path.parent, self.first / ".forge" / "routing")
        self.assertEqual(
            routing.load(path, TASK_DIFFICULTIES).slot("coder", "standard"),
            routing.Endpoint(agent="claude", model="opus"))

    def test_two_starts_never_share_one_routing_file(self) -> None:
        # Stała nazwa dawała wyścig: zapis wyprzedza zamek projektu, więc drugi
        # start podmieniłby plik pierwszemu, zanim tamten zdążył go przeczytać.
        run = self.window.runs[0]
        empty = _routing({"roles": {}})

        first = run.routing_snapshot(empty)
        second = run.routing_snapshot(empty)

        self.assertNotEqual(first, second)
        self.assertTrue(first.exists() and second.exists())

    def test_old_routing_snapshots_do_not_pile_up(self) -> None:
        run = self.window.runs[0]
        empty = _routing({"roles": {}})

        for _ in range(gui.KEEP_ROUTING_SNAPSHOTS + 4):
            newest = run.routing_snapshot(empty)

        kept = sorted((self.first / ".forge" / "routing").glob("run-*.json"))
        self.assertEqual(len(kept), gui.KEEP_ROUTING_SNAPSHOTS)
        self.assertIn(newest, kept)

    def test_a_run_shows_only_its_own_previous_log(self) -> None:
        for project, note in ((self.first, "poprzednio w alfie"),
                              (self.second, "poprzednio w becie")):
            runtime = project / ".forge"
            runtime.mkdir(exist_ok=True)
            (runtime / "gui_run.log").write_text(note + "\n", encoding="utf-8")

        window = gui.ForgeWindow(Adw.Application(
            application_id="pl.agentloop.ForgeTest2"))
        self.addCleanup(window.destroy)

        self.assertIn("poprzednio w alfie", self._log(window.runs[0]))
        self.assertNotIn("poprzednio w becie", self._log(window.runs[0]))
        self.assertIn("poprzednio w becie", self._log(window.runs[1]))

    def test_runs_can_be_added_up_to_the_ceiling(self) -> None:
        while len(self.window.runs) < MAX_RUNS:
            self.assertIsNotNone(self.window.add_run())

        self.assertIsNone(self.window.add_run())
        self.assertFalse(self.window.add_run_button.get_sensitive())

    def test_a_working_run_cannot_be_removed_from_the_panel(self) -> None:
        working = self.window.runs[0]
        working.process = _FakeProcess()

        self.window.remove_run(working)

        self.assertIn(working, self.window.runs)

    def test_the_last_run_stays_in_the_panel(self) -> None:
        self.window.remove_run(self.window.runs[1])
        self.window.remove_run(self.window.runs[0])

        self.assertEqual(len(self.window.runs), 1)

    def test_saved_settings_describe_every_run(self) -> None:
        self.window.save_paths()

        self.assertEqual(self.saved[-1]["runs"], self.settings["runs"])


@needs_gtk
class RunProfilesTest(unittest.TestCase):
    """Osobne modele dla osobnych biegów — sedno tej przebudowy."""

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.first = self.root / "alfa"
        self.second = self.root / "beta"
        for project in (self.first, self.second):
            project.mkdir()
        self.saved: list[dict] = []
        self.settings = {"runs": [
            {"brief": "a.md", "project": str(self.first), "profile": SHARED},
            {"brief": "b.md", "project": str(self.second), "profile": SHARED},
        ]}
        patches = (
            patch("forge.gui.load_settings", return_value=self.settings),
            patch("forge.gui.save_settings",
                  side_effect=lambda payload, *_a, **_k: self.saved.append(payload)),
            patch("forge.gui.routing_path", return_value=self.root / "routing.json"),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self._directory.cleanup)
        self.window = gui.ForgeWindow(Adw.Application(
            application_id="pl.agentloop.ForgeProfiles"))
        self.addCleanup(self.window.destroy)

    def _pick(self, role: str, difficulty: str, model: str) -> None:
        """Wybierz model w karcie roli tak, jak zrobiłby to operator."""
        chooser = self.window.role_cards[role].slots[difficulty]
        chooser.model.set_selected(next(
            index for index, choice in enumerate(chooser.choices())
            if choice.entry is not None and choice.entry.name == model))

    def _agents(self, routing_value: routing.Routing) -> set[str]:
        config = Config(routing=routing_value)
        return {agent for agent, _model, _effort
                in config.role_chain("coder", "standard")}

    def test_the_panel_starts_on_the_shared_profile(self) -> None:
        self.assertEqual(self.window.editing_slug, SHARED)
        self.assertEqual([run.profile_slug for run in self.window.runs],
                         [SHARED, SHARED])

    def test_two_runs_can_work_on_different_models(self) -> None:
        alfa, beta = self.window.runs
        self._pick("coder", "standard", "opus")

        gpt = self.window.profiles.create("Tylko GPT")
        self.window.edit_profile(gpt.slug)
        self._pick("coder", "standard", "gpt-5.6-luna")
        beta.set_profile(gpt.slug)

        self.assertEqual(self._agents(self.window.run_routing(alfa)), {"claude"})
        self.assertEqual(self._agents(self.window.run_routing(beta)),
                         {"opencode"})

    def test_editing_one_profile_leaves_the_other_alone(self) -> None:
        self._pick("coder", "standard", "opus")
        gpt = self.window.profiles.create("Tylko GPT")

        self.window.edit_profile(gpt.slug)
        self._pick("coder", "standard", "gpt-5.6-luna")
        self.window.edit_profile(SHARED)

        self.assertEqual(
            self.window.profiles.routing(SHARED).slot("coder", "standard").model,
            "opus")
        self.assertEqual(
            self.window.profiles.routing(gpt.slug).slot("coder", "standard").model,
            "openai/gpt-5.6-luna")

    def test_switching_the_editor_shows_the_other_profile_in_the_cards(self) -> None:
        self._pick("coder", "standard", "opus")
        gpt = self.window.profiles.create("Tylko GPT")
        self.window.edit_profile(gpt.slug)
        self._pick("coder", "standard", "gpt-5.6-luna")

        self.window.edit_profile(SHARED)

        self.assertEqual(self.window.current_routing()
                         .slot("coder", "standard").model, "opus")

    def test_a_role_untouched_by_the_new_profile_returns_to_policy(self) -> None:
        # Karty są wspólne dla profili: gdyby nie czyściły się przy zmianie,
        # na ekranie zostałby wybór z poprzedniego profilu i pierwszy klik
        # zapisałby go jako wybór operatora dla tego.
        self._pick("coder", "simple", "opus")
        clean = self.window.profiles.create("Czysty")
        self.window.profiles.set_routing(clean.slug, routing.Routing())

        self.window.edit_profile(clean.slug)

        policy = Config(routing=routing.Routing()).role("coder", "simple")
        self.assertEqual(
            self.window.current_routing().slot("coder", "simple"),
            routing.Endpoint(agent=policy[0], model=policy[1], effort=policy[2]))

    def test_the_run_remembers_its_profile_between_sessions(self) -> None:
        gpt = self.window.profiles.create("Tylko GPT")
        self.window.runs[1].set_profile(gpt.slug)

        self.window.save_paths()

        self.assertEqual([entry["profile"] for entry in self.saved[-1]["runs"]],
                         [SHARED, gpt.slug])

    def test_the_snapshot_carries_the_models_of_that_run(self) -> None:
        beta = self.window.runs[1]
        gpt = self.window.profiles.create("Tylko GPT")
        self.window.edit_profile(gpt.slug)
        self._pick("coder", "standard", "gpt-5.6-luna")
        beta.set_profile(gpt.slug)

        path = beta.routing_snapshot(self.window.run_routing(beta))

        self.assertEqual(
            routing.load(path, TASK_DIFFICULTIES).slot("coder", "standard").model,
            "openai/gpt-5.6-luna")

    def test_only_a_run_that_uses_claude_asks_about_its_session(self) -> None:
        # Zamek na PLIKOWĄ sesję Claude Code jest zasobem globalnym, ale bieg
        # bez Claude'a w routingu nie ma powodu się o niego rozbijać.
        alfa, beta = self.window.runs
        for role in self.window.role_cards:
            for difficulty in self.window.role_cards[role].slots:
                self._pick(role, difficulty, "gpt-5.6-luna")
        claude = self.window.profiles.create("Z Claude'em")
        self.window.edit_profile(claude.slug)
        self._pick("coder", "standard", "opus")
        beta.set_profile(claude.slug)

        seen: list[bool] = []

        def busy(config: Config, *_args: object) -> str:
            uses = any(name == "claude" for name in config.agents_in_use())
            seen.append(uses)
            return "sesję trzyma inny bieg" if uses else ""

        with patch("forge.gui.preflight.claude_file_session_busy",
                   side_effect=busy):
            self.assertEqual(self.window.blocking_problem(alfa), "")
            self.assertIn("sesję trzyma inny bieg",
                          self.window.blocking_problem(beta))
        self.assertEqual(seen, [False, True])

    def test_a_run_pointing_at_a_deleted_profile_is_refused(self) -> None:
        # Cichy powrót na profil wspólny oznaczałby bieg wykonany modelami,
        # których dla tego projektu nikt nie wybrał.
        beta = self.window.runs[1]
        gpt = self.window.profiles.create("Tylko GPT")
        beta.set_profile(gpt.slug)
        self.window.profiles.delete(gpt.slug)

        self.assertIn("profilu", self.window.blocking_problem(beta))

    def test_deleting_a_profile_moves_idle_runs_to_the_shared_one(self) -> None:
        beta = self.window.runs[1]
        gpt = self.window.profiles.create("Tylko GPT")
        beta.set_profile(gpt.slug)
        self.window.edit_profile(gpt.slug)

        self.window._delete_profile()

        self.assertEqual(beta.profile_slug, SHARED)
        self.assertEqual(self.window.editing_slug, SHARED)
        self.assertFalse(self.window.profiles.has(gpt.slug))

    def test_a_profile_running_a_run_cannot_be_deleted(self) -> None:
        beta = self.window.runs[1]
        gpt = self.window.profiles.create("Tylko GPT")
        beta.set_profile(gpt.slug)
        beta.process = _FakeProcess()
        self.window.edit_profile(gpt.slug)

        self.window._delete_profile()

        self.assertTrue(self.window.profiles.has(gpt.slug))
        self.assertEqual(beta.profile_slug, gpt.slug)

    def test_renaming_keeps_the_run_attached(self) -> None:
        beta = self.window.runs[1]
        gpt = self.window.profiles.create("Tylko GPT")
        beta.set_profile(gpt.slug)
        self.window.edit_profile(gpt.slug)

        self.window.profile_name.set_text("GPT i nic więcej")
        self.window._rename_profile(self.window.profile_name)

        self.assertEqual(beta.profile_slug, gpt.slug)
        self.assertEqual(self.window.profiles.label(gpt.slug),
                         "GPT i nic więcej")

    def test_an_abandoned_name_restores_the_previous_one(self) -> None:
        # Pole nazwy rozlicza się także przy wyjściu ogniskiem, więc puste musi
        # znaczyć „nic nie zmieniam", a nie „skasuj nazwę".
        gpt = self.window.profiles.create("Tylko GPT")
        self.window.edit_profile(gpt.slug)

        self.window.profile_name.set_text("   ")
        self.window._rename_profile(self.window.profile_name)

        self.assertEqual(self.window.profiles.label(gpt.slug), "Tylko GPT")
        self.assertEqual(self.window.profile_name.get_text(), "Tylko GPT")

    def test_the_shared_profile_keeps_its_name(self) -> None:
        self.window.profile_name.set_text("Coś innego")
        self.window._rename_profile(self.window.profile_name)

        self.assertEqual(self.window.profiles.label(SHARED),
                         profiles.SHARED_LABEL)

    def test_a_run_can_use_gpt_while_another_mixes_three_tools(self) -> None:
        # Dokładnie przypadek, dla którego powstały profile.
        alfa, beta = self.window.runs
        for difficulty in TASK_DIFFICULTIES:
            self._pick("coder", difficulty, "gpt-5.6-luna")
            self._pick("tester", difficulty, "gpt-5.6-luna")

        mixed = self.window.profiles.create("Trzy narzędzia")
        self.window.edit_profile(mixed.slug)
        self._pick("coder", "complex", "opus")
        self._pick("tester", "standard", "grok-4.5")
        beta.set_profile(mixed.slug)

        alfa_models = {self.window.run_routing(alfa).slot(role, difficulty).model
                       for role in ("coder", "tester")
                       for difficulty in TASK_DIFFICULTIES}
        beta_routing = self.window.run_routing(beta)

        self.assertEqual(alfa_models, {"openai/gpt-5.6-luna"})
        self.assertEqual(beta_routing.slot("coder", "complex").agent, "claude")
        self.assertEqual(beta_routing.slot("tester", "standard").model,
                         "xai/grok-4.5")
        self.assertEqual(beta_routing.slot("coder", "simple").model,
                         "openai/gpt-5.6-luna")

    def test_the_run_dropdown_shows_a_missing_profile_instead_of_hiding_it(
            self) -> None:
        beta = self.window.runs[1]
        beta.profile_slug = "znikniety"
        beta.refresh_profiles()

        self.assertIn("BRAK", " ".join(
            label for _slug, label in beta._profile_choices))
        self.assertEqual(beta.profile_slug, "znikniety")


if __name__ == "__main__":
    unittest.main()
