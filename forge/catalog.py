"""Katalog modeli i poziomów namysłu do wyboru w GUI.

GUI musi zaproponować konkretne modele, a nie kazać ich przepisywać z pamięci.
Źródła są dwa i celowo się uzupełniają:

- POLITYKA PROJEKTU (``MODEL_LEVEL_ROUTING`` w config.py) — modele, na których
  Forge realnie działa i które ma opisane w tabeli poziomów;
- KONFIGURACJA OPENCODE użytkownika (``~/.config/opencode/opencode.json``) —
  bo to tam mieszkają jego prywatni providerzy (lokalna llama.cpp, NeuralWatt,
  z.ai, …), o których Forge nie ma prawa nic wiedzieć z góry.

Katalog jest PODPOWIEDZIĄ, nie zamkniętą listą: GUI zawsze pozwala wpisać nazwę
modelu ręcznie, bo nowy model u dostawcy pojawia się wcześniej niż tutaj.
"""
from __future__ import annotations

from .adapters import canonical_agent
from .agents import opencode_user_config
from .config import MODEL_LEVEL_ROUTING

# Kolejność = kolejność w GUI. Codex ma alias "gpt", ale w wyborze pokazujemy
# nazwę kanoniczną, żeby jedna binarka nie występowała jako dwa narzędzia.
AGENTS: tuple[str, ...] = ("claude", "codex", "opencode", "grok", "kiro")

# Poziomy namysłu przyjmowane przez CLI danego agenta. Pusty string = „nie
# przekazuj flagi", czyli decyduje sam agent (u OpenCode dotyczy to modeli bez
# capabilities.reasoning_effort — dla nich --variant nie istnieje).
EFFORTS: dict[str, tuple[str, ...]] = {
    "claude": ("", "low", "medium", "high"),
    "codex": ("", "low", "medium", "high", "xhigh"),
    "opencode": ("", "low", "medium", "high", "max"),
    "grok": ("", "low", "medium", "high"),
    "kiro": ("", "low", "medium", "high"),
}
DEFAULT_EFFORTS: tuple[str, ...] = ("", "low", "medium", "high", "max")


def efforts(agent: str) -> tuple[str, ...]:
    return EFFORTS.get(canonical_agent(agent), DEFAULT_EFFORTS)


def _policy_models(agent: str) -> list[str]:
    """Modele, które polityka poziomów sama wybiera dla tego agenta."""
    seen: list[str] = []
    for model, _effort in MODEL_LEVEL_ROUTING.get(
            canonical_agent(agent), {}).values():
        if model and model not in seen:
            seen.append(model)
    return seen


def opencode_models(config: dict | None = None) -> list[str]:
    """Modele „provider/model" widoczne w konfiguracji OpenCode użytkownika.

    ``whitelist`` providera zawęża listę, jeśli jest ustawiona — dokładnie tak,
    jak robi to samo OpenCode; bez niej bierzemy wszystkie zadeklarowane modele."""
    data = opencode_user_config() if config is None else config
    providers = data.get("provider")
    if not isinstance(providers, dict):
        return []
    out: list[str] = []
    for provider, definition in sorted(providers.items()):
        if not isinstance(definition, dict):
            continue
        models = definition.get("models")
        names = list(models.keys()) if isinstance(models, dict) else []
        whitelist = definition.get("whitelist")
        if isinstance(whitelist, list) and whitelist:
            allowed = {name for name in whitelist if isinstance(name, str)}
            names = [name for name in names if name in allowed]
            # Model bywa tylko na whiteliście (provider zna go „z góry",
            # bez lokalnej definicji) — to nadal legalny wybór.
            names += [name for name in whitelist
                      if isinstance(name, str) and name not in names]
        for name in sorted(names):
            candidate = f"{provider}/{name}"
            if candidate not in out:
                out.append(candidate)
    return out


def models(agent: str, opencode_config: dict | None = None) -> list[str]:
    """Podpowiedzi modeli dla agenta: najpierw znane polityce, potem odkryte."""
    out = _policy_models(agent)
    if canonical_agent(agent) == "opencode":
        for name in opencode_models(opencode_config):
            if name not in out:
                out.append(name)
    return out
