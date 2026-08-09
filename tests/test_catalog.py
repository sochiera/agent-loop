from __future__ import annotations

from forge import catalog


def test_policy_models_come_first_and_without_duplicates() -> None:
    # Modele, na których Forge realnie działa, mają być na wierzchu listy.
    models = catalog.models("claude")

    assert models[:3] == ["haiku", "sonnet", "opus"]
    assert len(models) == len(set(models))


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
    assert catalog.models("aider") == []
    assert catalog.efforts("aider") == catalog.DEFAULT_EFFORTS
