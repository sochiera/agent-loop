from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge import profiles, routing
from forge.config import TASK_DIFFICULTIES

SHARED = profiles.SHARED_SLUG


def _routing(payload: dict) -> routing.Routing:
    return routing.parse(payload, TASK_DIFFICULTIES)


def _claude() -> routing.Routing:
    return _routing({"roles": {"coder": {"slots": {
        "standard": {"agent": "claude", "model": "opus", "effort": "high"}}}}})


def _gpt() -> routing.Routing:
    return _routing({"roles": {"coder": {"slots": {
        "standard": {"agent": "opencode", "model": "openai/gpt-5.6-luna",
                     "effort": "high"}}}}})


@pytest.fixture
def store(tmp_path: Path) -> profiles.Store:
    return profiles.Store.load(
        tmp_path / "routing.json", tmp_path / "profiles", TASK_DIFFICULTIES)


class TestSlugify:
    def test_polish_names_become_file_safe_slugs(self) -> None:
        assert profiles.slugify("Tylko GPT") == "tylko-gpt"
        assert profiles.slugify("Zażółć gęślą jaźń") == "zazolc-gesla-jazn"

    def test_a_name_without_usable_characters_still_gives_a_file(self) -> None:
        # Pusty slug znaczyłby plik ".json" — ukryty i nie do wskazania.
        assert profiles.slugify("///") == "profil"
        assert profiles.slugify("") == "profil"

    def test_path_traversal_never_reaches_the_filesystem(self) -> None:
        assert not profiles.valid_slug("../../.ssh/config")
        with pytest.raises(profiles.UnknownProfile):
            profiles.path_for("../wyjscie", {"XDG_CONFIG_HOME": "/tmp/cfg"})


class TestStore:
    def test_the_shared_profile_is_the_old_routing_file(
            self, store: profiles.Store, tmp_path: Path) -> None:
        # Cała zgodność wsteczna wisi na tym jednym zdaniu: bieg z CLI bez
        # żadnej zmiennej ma dalej czytać ten sam plik, co przed profilami.
        assert store.path(SHARED) == tmp_path / "routing.json"
        assert store.slugs() == [SHARED]

        store.set_routing(SHARED, _claude())

        assert routing.load(tmp_path / "routing.json",
                            TASK_DIFFICULTIES) == _claude()

    def test_a_named_profile_is_an_ordinary_routing_file(
            self, store: profiles.Store, tmp_path: Path) -> None:
        # Dzięki temu FORGE_ROUTING_FILE wskazujący profil działa bez zmian
        # w orkiestratorze, a migawka biegu zostaje zwykłym plikiem routingu.
        profile = store.create("Tylko GPT")
        store.set_routing(profile.slug, _gpt())

        path = tmp_path / "profiles" / "tylko-gpt.json"
        assert routing.load(path, TASK_DIFFICULTIES) == _gpt()
        assert json.loads(path.read_text(encoding="utf-8"))["name"] == "Tylko GPT"

    def test_profiles_do_not_leak_into_each_other(
            self, store: profiles.Store) -> None:
        gpt = store.create("Tylko GPT")
        mixed = store.create("Wszystko")

        store.set_routing(gpt.slug, _gpt())
        store.set_routing(mixed.slug, _claude())

        assert store.routing(gpt.slug) == _gpt()
        assert store.routing(mixed.slug) == _claude()
        assert store.routing(SHARED) == routing.Routing()

    def test_a_new_profile_starts_as_a_copy_of_its_source(
            self, store: profiles.Store) -> None:
        store.set_routing(SHARED, _claude())

        assert store.routing(store.create("Kopia").slug) == _claude()

    def test_a_reloaded_store_sees_the_same_profiles(
            self, store: profiles.Store, tmp_path: Path) -> None:
        store.set_routing(store.create("Tylko GPT").slug, _gpt())

        again = profiles.Store.load(tmp_path / "routing.json",
                                    tmp_path / "profiles", TASK_DIFFICULTIES)

        assert [profile.name for profile in again.profiles()] == [
            profiles.SHARED_LABEL, "Tylko GPT"]
        assert again.routing("tylko-gpt") == _gpt()

    def test_names_that_collide_get_separate_files(
            self, store: profiles.Store) -> None:
        first = store.create("Tylko GPT")
        second = store.create("Tylko GPT")

        assert first.slug != second.slug
        assert first.name != second.name
        assert store.path(first.slug) != store.path(second.slug)

    def test_renaming_keeps_the_slug_so_runs_stay_attached(
            self, store: profiles.Store) -> None:
        # Wiersz biegu trzyma slug. Gdyby przemianowanie zmieniało plik, każda
        # poprawka literówki osierocałaby konfigurację biegu.
        profile = store.create("Tylko GPT")
        store.set_routing(profile.slug, _gpt())

        store.rename(profile.slug, "GPT i nic więcej")

        assert store.get(profile.slug).name == "GPT i nic więcej"
        assert store.routing(profile.slug) == _gpt()
        assert profiles.read_label(store.path(profile.slug),
                                   profile.slug) == "GPT i nic więcej"

    def test_the_shared_profile_cannot_be_renamed_or_deleted(
            self, store: profiles.Store) -> None:
        with pytest.raises(ValueError):
            store.rename(SHARED, "Coś innego")
        with pytest.raises(ValueError):
            store.delete(SHARED)

    def test_deleting_removes_the_file(
            self, store: profiles.Store) -> None:
        profile = store.create("Tymczasowy")
        path = store.path(profile.slug)

        store.delete(profile.slug)

        assert not path.exists()
        assert not store.has(profile.slug)

    def test_an_unknown_profile_is_an_error_not_a_silent_default(
            self, store: profiles.Store) -> None:
        # Cicha podmiana na politykę domyślną kosztowałaby cały bieg wykonany
        # nie tymi modelami, o które prosił operator.
        with pytest.raises(profiles.UnknownProfile):
            store.routing("nie-ma-takiego")

    def test_a_profile_file_edited_by_hand_keeps_its_routing(
            self, tmp_path: Path) -> None:
        folder = tmp_path / "profiles"
        folder.mkdir()
        (folder / "reczny.json").write_text(json.dumps(
            {"version": 1, "roles": {"coder": {"agent": "claude"}}}),
            encoding="utf-8")

        store = profiles.Store.load(tmp_path / "routing.json", folder,
                                    TASK_DIFFICULTIES)

        assert store.routing("reczny").agent("coder") == "claude"
        # Bez pola ``name`` etykietą zostaje slug — plik nadal jest użyteczny.
        assert store.get("reczny").name == "reczny"


class TestResolution:
    @pytest.fixture(autouse=True)
    def _config(self, tmp_path: Path) -> None:
        self.environ = {"XDG_CONFIG_HOME": str(tmp_path)}
        self.store = profiles.Store.load(
            profiles.shared_path(self.environ), profiles.directory(self.environ),
            TASK_DIFFICULTIES)

    def test_a_profile_is_found_by_slug_and_by_label(self) -> None:
        self.store.set_routing(self.store.create("Tylko GPT").slug, _gpt())

        for name in ("tylko-gpt", "Tylko GPT", "tylko gpt"):
            assert profiles.resolve(name, self.environ) == "tylko-gpt"
        assert profiles.load_named("tylko-gpt", TASK_DIFFICULTIES,
                                   self.environ) == _gpt()

    def test_an_empty_name_means_the_shared_profile(self) -> None:
        assert profiles.resolve("", self.environ) == SHARED

    def test_an_unknown_name_names_what_is_available(self) -> None:
        self.store.create("Tylko GPT")

        with pytest.raises(profiles.UnknownProfile, match="tylko-gpt"):
            profiles.resolve("wszystko", self.environ)

    def test_the_routing_file_variable_still_wins(self, tmp_path: Path) -> None:
        # Tym kanałem panel podaje biegowi jego MIGAWKĘ; pytanie o profil
        # byłoby pytaniem o coś już rozstrzygniętego.
        snapshot = tmp_path / "migawka.json"
        routing.save(_claude(), snapshot)
        self.store.set_routing(self.store.create("Tylko GPT").slug, _gpt())

        result = profiles.load_from_env(
            self.environ | {"FORGE_ROUTING_FILE": str(snapshot),
                            "FORGE_ROUTING_PROFILE": "tylko-gpt"},
            TASK_DIFFICULTIES)

        assert result == _claude()

    def test_the_profile_variable_selects_the_models(self) -> None:
        self.store.set_routing(self.store.create("Tylko GPT").slug, _gpt())

        result = profiles.load_from_env(
            self.environ | {"FORGE_ROUTING_PROFILE": "tylko-gpt"},
            TASK_DIFFICULTIES)

        assert result == _gpt()

    def test_no_variables_at_all_means_the_shared_profile(self) -> None:
        self.store.set_routing(SHARED, _claude())

        assert profiles.load_from_env(self.environ, TASK_DIFFICULTIES) == _claude()

    def test_a_missing_profile_in_the_environment_stops_the_run(self) -> None:
        with pytest.raises(profiles.UnknownProfile):
            profiles.load_from_env(
                self.environ | {"FORGE_ROUTING_PROFILE": "nie-ma"},
                TASK_DIFFICULTIES)
