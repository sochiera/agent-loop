"""Katalog modeli i poziomów namysłu do wyboru w GUI.

GUI wybiera MODEL, a nie narzędzie — bo tak wygląda decyzja operatora: „chcę
glm-5.2", a nie „chcę opencode". Katalog musi więc odpowiadać na pytanie
odwrotne niż konfiguracja: czym da się uruchomić dany model. Jeden model bywa
osiągalny kilkoma TRASAMI (``Route``) i dopiero wtedy GUI pyta o dostawcę:

- ``gpt-5.6-luna`` → Codex CLI albo most OpenCode (``openai/gpt-5.6-luna``);
- ``glm-5.2`` → OpenCode u każdego dostawcy, który go serwuje (dziś
  ``zai-coding-plan``, a po dopisaniu drugiego — obu naraz);
- ``sonnet``, ``grok-4.5``, ``qwen3.8-max`` → dokładnie jedna trasa,
  więc pytanie o dostawcę byłoby pustym klikiem.

Tożsamością modelu jest NAGA nazwa: prefiks ``provider/`` z modeli OpenCode
opisuje dostawcę, nie inny model. Tam, gdzie to samo CLI nazywa model inaczej,
scalenie wymaga wpisu w ``MODEL_ALIASES`` — brak wpisu oznacza dwie osobne
pozycje na liście, czyli degradację do dzisiejszego zachowania, nie błąd.

Źródła tras są dwa i celowo się uzupełniają:

- POLITYKA PROJEKTU (``MODEL_LEVEL_ROUTING`` w config.py) — modele, na których
  Forge realnie działa i które ma opisane w tabeli poziomów;
- KONFIGURACJA OPENCODE użytkownika (``~/.config/opencode/opencode.json``) —
  bo to tam mieszkają jego prywatni providerzy (Qwen Token Plan, z.ai, …),
  o których Forge nie ma prawa nic wiedzieć z góry.

Katalog jest PODPOWIEDZIĄ, nie zamkniętą listą: GUI zawsze pozwala wpisać nazwę
modelu ręcznie, bo nowy model u dostawcy pojawia się wcześniej niż tutaj.
"""
from __future__ import annotations

from dataclasses import dataclass

from .adapters import canonical_agent
from .agents import opencode_user_config
from .config import MODEL_LEVEL_ROUTING

# Kolejność = kolejność tras w GUI. Codex ma alias "gpt", ale w wyborze
# pokazujemy nazwę kanoniczną, żeby jedna binarka nie występowała jako dwa
# narzędzia. Natywne CLI stoją przed mostem OpenCode: mają wbudowaną obsługę
# (telemetria zużycia, wznawianie sesji u Codeksa), więc przy modelu osiągalnym
# obiema drogami domyślna trasa ma być tą bogatszą.
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

# Ten sam model pod inną nazwą w innym CLI. Tabela jest KURATOROWANA, bo
# zgadywanie po podobieństwie nazw scalałoby modele naprawdę różne (kimi-k2.6
# i kimi-k2.7-code to nie to samo). Wpis znaczy: „wybór dostawcy przełącza
# między tymi trasami bez zmiany decyzji o modelu".
MODEL_ALIASES: dict[str, str] = {
    # Kiro nazywa modele Anthropica z numerem wersji, Claude Code aliasem rodziny.
    "sonnet-4.6": "sonnet",
    "opus-4.6": "opus",
}


@dataclass(frozen=True)
class Route:
    """Jedna droga do modelu: czym go uruchomić i pod jaką nazwą."""

    agent: str
    # Dokładny string dla TEGO CLI — u OpenCode z prefiksem dostawcy.
    model: str
    # Etykieta dostawcy w GUI ("codex", "opencode · zai-coding-plan").
    provider: str


@dataclass(frozen=True)
class ModelEntry:
    """Model widziany przez operatora wraz ze wszystkimi trasami do niego."""

    name: str
    routes: tuple[Route, ...]

    @property
    def ambiguous(self) -> bool:
        """True → GUI musi zapytać o dostawcę; False → pytanie byłoby puste."""
        return len(self.routes) > 1

    def route_for(self, agent: str, model: str = "") -> Route | None:
        """Trasa tego agenta (opcjonalnie: o dokładnie tej nazwie modelu)."""
        for route in self.routes:
            if canonical_agent(route.agent) != canonical_agent(agent):
                continue
            if not model or route.model == model:
                return route
        return None

    def restricted(self, agents: tuple[str, ...]) -> "ModelEntry | None":
        """Ten sam model bez tras zakazanych dla roli (patrz RoleDef.allows_codex).

        ``None`` = modelu nie da się uruchomić niczym, co rola dopuszcza, więc
        nie ma po co pokazywać go na jej liście."""
        allowed = {canonical_agent(name) for name in agents}
        kept = tuple(route for route in self.routes
                     if canonical_agent(route.agent) in allowed)
        return ModelEntry(name=self.name, routes=kept) if kept else None


def efforts(agent: str) -> tuple[str, ...]:
    return EFFORTS.get(canonical_agent(agent), DEFAULT_EFFORTS)


def split_model(agent: str, model: str) -> tuple[str, str]:
    """Rozdziel „provider/model" OpenCode na (dostawca, naga nazwa).

    Poza OpenCode ukośnik nie oznacza dostawcy, więc nazwa zostaje w całości —
    inaczej dorobilibyśmy modelowi dostawcę, którego jego CLI nie zna."""
    if canonical_agent(agent) == "opencode":
        provider, separator, bare = model.partition("/")
        if separator and bare:
            return provider, bare
    return "", model


def identity(agent: str, model: str) -> str:
    """Klucz scalania tras w jedną pozycję listy modeli."""
    bare = split_model(agent, model)[1]
    return MODEL_ALIASES.get(bare, bare)


def provider_label(agent: str, model: str) -> str:
    """Nazwa dostawcy pokazywana operatorowi.

    Dostawca OpenCode zostaje wskazany razem z mostem ("opencode · z.ai"),
    bo sama nazwa dostawcy nie mówi, którym CLI to poleci — a od tego zależy
    dostępność effortu i telemetrii."""
    agent = canonical_agent(agent)
    provider = split_model(agent, model)[0]
    return f"{agent} · {provider}" if provider else agent


def _policy_candidates() -> list[tuple[str, str]]:
    """Pary (agent, model) wprost z tabeli poziomów, w kolejności AGENTS."""
    out: list[tuple[str, str]] = []
    for agent in AGENTS:
        for model, _effort in MODEL_LEVEL_ROUTING.get(agent, {}).values():
            if model and (agent, model) not in out:
                out.append((agent, model))
    return out


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


def index(opencode_config: dict | None = None) -> tuple[ModelEntry, ...]:
    """Wszystkie znane modele, każdy z listą tras. Kolejność = kolejność w GUI.

    Najpierw modele znane polityce (na nich Forge jest skalibrowany), potem
    odkryte w konfiguracji OpenCode."""
    groups: dict[str, list[Route]] = {}
    candidates = _policy_candidates()
    candidates += [("opencode", model) for model in opencode_models(opencode_config)]
    for agent, model in candidates:
        route = Route(agent=canonical_agent(agent), model=model,
                      provider=provider_label(agent, model))
        bucket = groups.setdefault(identity(agent, model), [])
        if route not in bucket:
            bucket.append(route)
    return tuple(
        ModelEntry(name=name, routes=tuple(sorted(routes, key=_route_order)))
        for name, routes in groups.items()
    )


def _route_order(route: Route) -> tuple[int, str, str]:
    agent = canonical_agent(route.agent)
    rank = AGENTS.index(agent) if agent in AGENTS else len(AGENTS)
    return (rank, route.provider, route.model)


def lookup(
    agent: str, model: str, entries: tuple[ModelEntry, ...] | None = None
) -> tuple[ModelEntry, Route] | None:
    """Znajdź pozycję katalogu odpowiadającą zapisanemu wyborowi.

    Służy do odtworzenia zaznaczenia z ``routing.json``: plik trzyma parę
    (agent, model), a GUI musi z niej wrócić do „model + dostawca". ``None``
    oznacza model spoza katalogu — GUI pokaże go wtedy jako wpis własny, zamiast
    po cichu podmienić wybór operatora."""
    if not model:
        return None
    for entry in (index() if entries is None else entries):
        route = entry.route_for(agent, model)
        if route is not None:
            return entry, route
    return None
