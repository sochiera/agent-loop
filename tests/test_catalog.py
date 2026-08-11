from __future__ import annotations

from forge import catalog


def _entry(entries: tuple[catalog.ModelEntry, ...], name: str) -> catalog.ModelEntry:
    return next(item for item in entries if item.name == name)


def test_policy_models_come_first_and_without_duplicates() -> None:
    # Modele, na których Forge realnie działa, mają być na wierzchu listy.
    names = [entry.name for entry in
             catalog.index({"provider": {"p": {"models": {"aaa": {}}}}})]

    assert names[:3] == ["haiku", "sonnet", "opus"]
    assert names[-1] == "aaa"
    assert len(names) == len(set(names))


def test_opencode_models_are_prefixed_with_their_provider() -> None:
    # OpenCode rozwiązuje modele w przestrzeni nazw providera — sama nazwa
    # modelu jest dla niego bezużyteczna.
    config = {"provider": {"lokalny": {"models": {"maly": {}, "duzy": {}}}}}

    assert catalog.opencode_models(config) == ["lokalny/duzy", "lokalny/maly"]


def test_whitelist_narrows_and_extends_the_provider_list() -> None:
    config = {"provider": {"p": {
        "models": {"widoczny": {}, "ukryty": {}},
        "whitelist": ["widoczny", "tylko-na-liscie"],
    }}}

    assert catalog.opencode_models(config) == ["p/tylko-na-liscie", "p/widoczny"]


def test_broken_configuration_is_not_fatal() -> None:
    assert catalog.opencode_models({}) == []
    assert catalog.opencode_models({"provider": "nonsens"}) == []


def test_unknown_agent_gets_an_empty_catalogue_not_an_error() -> None:
    # Własne CLI operatora Forge zna tylko z szablonu komendy — model wpisuje
    # się wtedy ręcznie.
    assert catalog.efforts("aider") == catalog.DEFAULT_EFFORTS
    assert all(entry.restricted(("aider",)) is None
               for entry in catalog.index({}))


def test_model_offers_only_curated_efforts_not_every_cli_variant() -> None:
    entries = catalog.index({})
    luna = _entry(entries, "gpt-5.6-luna")
    sol = _entry(entries, "gpt-5.6-sol")

    assert catalog.configured_efforts(luna.routes[0]) == ("medium", "high", "max")
    assert catalog.configured_efforts(sol.routes[0]) == ("high",)


class TestIndex:
    def test_gpt_is_exposed_only_through_opencode(self) -> None:
        entry = _entry(catalog.index({}), "gpt-5.6-luna")

        assert [(route.agent, route.model) for route in entry.routes] == [
            ("opencode", "openai/gpt-5.6-luna"),
        ]

    def test_grok_is_exposed_only_through_opencode(self) -> None:
        entry = _entry(catalog.index({}), "grok-4.5")

        assert [(route.agent, route.model) for route in entry.routes] == [
            ("opencode", "xai/grok-4.5"),
        ]

    def test_two_opencode_providers_of_one_model_are_two_routes(self) -> None:
        config = {"provider": {"tani": {"models": {"glm-5.2": {}}},
                               "szybki": {"models": {"glm-5.2": {}}}}}

        entry = _entry(catalog.index(config), "glm-5.2")

        assert [route.provider for route in entry.routes] == [
            "opencode · szybki", "opencode · tani", "opencode · zai-coding-plan"]

    def test_model_with_one_route_is_not_ambiguous(self) -> None:
        # Pytanie o dostawcę byłoby wtedy pustym klikiem.
        assert not _entry(catalog.index({}), "grok-4.5").ambiguous
        assert not _entry(catalog.index({}), "haiku").ambiguous

    def test_curated_alias_merges_names_differing_between_clis(self) -> None:
        entry = _entry(catalog.index({}), "sonnet")

        assert [(route.agent, route.model) for route in entry.routes] == [
            ("claude", "sonnet"), ("kiro", "sonnet-4.6")]

    def test_unknown_name_stays_its_own_entry(self) -> None:
        # Brak wpisu w MODEL_ALIASES ma dawać dwie pozycje, a nie zgadywanie.
        names = [entry.name for entry in
                 catalog.index({"provider": {"p": {"models": {"cos-nowego": {}}}}})]

        assert "cos-nowego" in names


class TestLookup:
    def test_saved_choice_is_resolved_back_to_model_and_route(self) -> None:
        entries = catalog.index({})

        found = catalog.lookup("opencode", "openai/gpt-5.6-luna", entries)

        assert found is not None
        entry, route = found
        assert entry.name == "gpt-5.6-luna"
        assert route.provider == "opencode · openai"

    def test_disabled_codex_route_is_not_resolved(self) -> None:
        assert catalog.lookup("gpt", "gpt-5.6-luna", catalog.index({})) is None

    def test_model_outside_the_catalogue_is_not_invented(self) -> None:
        assert catalog.lookup("opencode", "calkiem/nowy", catalog.index({})) is None
        assert catalog.lookup("claude", "", catalog.index({})) is None

    def test_route_of_another_agent_does_not_match(self) -> None:
        # Ten sam string modelu u innego CLI to inna trasa, nie ta sama.
        assert catalog.lookup("grok", "sonnet", catalog.index({})) is None


class TestRestriction:
    def test_role_that_forbids_codex_keeps_the_bridge_route(self) -> None:
        entry = _entry(catalog.index({}), "gpt-5.6-luna")

        restricted = entry.restricted(("claude", "opencode", "grok", "kiro"))

        assert restricted is not None
        assert [route.agent for route in restricted.routes] == ["opencode"]
        assert not restricted.ambiguous

    def test_sol_is_reachable_through_opencode(self) -> None:
        entry = _entry(catalog.index({}), "gpt-5.6-sol")

        assert entry.restricted(("claude", "opencode")) is not None
