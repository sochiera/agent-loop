from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolate_operator_routing() -> None:
    """Testy opisują POLITYKĘ PROJEKTU, nie prywatne nadpisania maszyny.

    ``Config`` czyta ``~/.config/forge/routing.json`` (patrz routing.py), więc
    bez tego bezpiecznika wynik testów routingu zależałby od tego, co operator
    wyklikał ostatnio w GUI."""
    os.environ["FORGE_ROUTING_FILE"] = "none"


@pytest.fixture(autouse=True, scope="session")
def _isolate_operator_opencode_config(tmp_path_factory) -> None:
    """Ten sam bezpiecznik dla ``~/.config/opencode/opencode.json``.

    Preflight sprawdza w niej klucze API providerów, a testy nie mogą przechodzić
    tylko dlatego, że akurat ta maszyna ma wyeksportowany klucz — ani padać na
    maszynie, która go nie ma."""
    # Pusta, ale ISTNIEJĄCA konfiguracja: przy nieczytelnym pliku
    # ``opencode_user_config`` spada na domyślną ścieżkę XDG, czyli wprost na
    # prywatny plik operatora — a to jest dokładnie to, przed czym izolujemy.
    empty = tmp_path_factory.mktemp("opencode") / "opencode.json"
    empty.write_text("{}", encoding="utf-8")
    os.environ["OPENCODE_CONFIG"] = str(empty)
    os.environ["FORGE_ENV_FILES"] = "none"
