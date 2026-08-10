"""Trwałe nadpisania routingu ról: agent, model/effort i łańcuch zapasowy.

Domyślna polityka Forge (rola → poziom → provider, patrz ``config.py``) zostaje
punktem wyjścia i nadal obowiązuje wszędzie tam, gdzie użytkownik niczego nie
wybrał. Ten moduł dokłada nad nią warstwę WYBORU OPERATORA, która:

- trzyma się w JEDNYM pliku poza repozytorium (``~/.config/forge/routing.json``),
  więc zmiana modelu dla roli nie wymaga commita ani edycji kodu;
- działa per rola, a dla ról wrażliwych na zakres zadania — per trudność;
- pozwala opisać ŁAŃCUCH ZAPASOWY: kolejne (agent, model, effort) próbowane,
  gdy poprzedni wpis wyczerpie limit albo twardo padnie.

Plik jest wspólny dla GUI i uruchomień z CLI: GUI go zapisuje, a każde
uruchomienie orkiestratora czyta go samo. Ścieżkę nadpisuje ``FORGE_ROUTING_FILE``;
wartość ``none``/``off`` wyłącza warstwę i przywraca czystą politykę projektu.

Parsowanie jest CELOWO pobłażliwe. Plik bywa edytowany ręcznie, a rozsypanie
całego biegu przez jedną literówkę w nazwie roli byłoby gorsze niż cicha praca
na polityce domyślnej: nieznane role, nieznane trudności i wartości niemożliwe
do przekazania w argv są po prostu pomijane.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import adapters

SCHEMA_VERSION = 1

# Klucz slotu ról, dla których trudność zadania nic nie zmienia (planista widzi
# cały wsad, Product Owner cały projekt — nie ma tam „jednego zadania”).
ANY_DIFFICULTY = "all"

_MAX_VALUE_LEN = 300


@dataclass(frozen=True)
class RoleDef:
    """Opis roli na potrzeby konfiguracji (GUI i walidacji pliku)."""

    name: str
    title: str
    description: str
    # True → rola dostaje osobny model dla simple/standard/complex.
    difficulty_aware: bool = False
    # Mistrz nie może być Codeksem (patrz config.validate_master_agent).
    allows_codex: bool = True


ROLE_DEFS: tuple[RoleDef, ...] = (
    RoleDef("product_owner", "Product Owner",
            "Utrzymuje cel projektu i cienki backlog historyjek"),
    RoleDef("po_reviewer", "Recenzent PO",
            "Ostatnia bramka przed propagacją błędnego kierunku"),
    RoleDef("bootstrap", "Bootstrap",
            "Pierwsze rozpoznanie repozytorium i komend build/test"),
    RoleDef("bootstrap_reviewer", "Recenzent bootstrapu",
            "Sprawdza ustalenia bootstrapu w świeżym kontekście"),
    RoleDef("planner", "Planista",
            "Tworzy plan i dzieli pracę na zadania"),
    RoleDef("planner_escalation", "Planista (eskalacja)",
            "Powtórne planowanie po odrzuceniu wsadu"),
    RoleDef("tester", "Tester",
            "Pisze testy i pilnuje czerwonej bramki", difficulty_aware=True),
    RoleDef("coder", "Koder",
            "Implementuje rozwiązanie i zazielenia testy", difficulty_aware=True),
    RoleDef("reviewer", "Recenzent",
            "Sprawdza ukończone zadanie w świeżym kontekście", difficulty_aware=True),
    RoleDef("verifier", "Weryfikator",
            "Ocenia, czy cały cel i historyjki zostały osiągnięte"),
    RoleDef("master", "Mistrz",
            "Pilnuje procesu i wykrywa pętle; tylko doradza",
            difficulty_aware=True, allows_codex=False),
)

ROLE_BY_NAME: dict[str, RoleDef] = {role.name: role for role in ROLE_DEFS}


def default_path(environ: dict[str, str] | None = None) -> Path:
    environ = os.environ if environ is None else environ
    base = environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "forge" / "routing.json"


def configured_path(environ: dict[str, str] | None = None) -> Path | None:
    """Plik routingu wskazany przez ``FORGE_ROUTING_FILE``.

    ``None`` = operator wyłączył warstwę (``none``/``off``/``0``); brak zmiennej
    oznacza ścieżkę domyślną. Jedno miejsce na tę regułę, bo czytają ją zarówno
    orkiestrator, jak i GUI — rozjazd oznaczałby edycję innego pliku, niż ten,
    z którego korzysta bieg."""
    environ = os.environ if environ is None else environ
    configured = (environ.get("FORGE_ROUTING_FILE") or "").strip()
    if configured.lower() in {"none", "off", "0"}:
        return None
    return Path(configured) if configured else default_path(environ)


def agent_allowed(role: str, agent: str) -> bool:
    """Czy ta rola może zostać wykonana tym narzędziem.

    Zakaz Codeksa dla mistrza (patrz ``config.validate_master_agent``) musi
    obowiązywać także wpisy z ręcznie edytowanego pliku — i to zarówno pierwszy
    wybór, jak i zapas, bo inaczej zakaz kończyłby się na pierwszej awarii."""
    definition = ROLE_BY_NAME.get(role)
    if definition is None or definition.allows_codex or not agent:
        return True
    return adapters.canonical_agent(agent) != "codex"


def _clean(value: Any) -> str:
    """Wartość nadająca się do przekazania w argv, albo pusty string."""
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or len(value) > _MAX_VALUE_LEN:
        return ""
    if "\0" in value or "\n" in value or "\r" in value:
        return ""
    return value


@dataclass(frozen=True)
class Endpoint:
    """Jeden punkt routingu: czym i jakim modelem wykonać rolę.

    Puste pole = „zostaw decyzję polityce”: pusty ``agent`` oznacza agenta roli,
    pusty ``model`` — model wynikający z poziomu roli dla danego agenta."""

    agent: str = ""
    model: str = ""
    effort: str = ""

    @property
    def empty(self) -> bool:
        return not (self.agent or self.model or self.effort)

    def as_dict(self) -> dict[str, str]:
        return {"agent": self.agent, "model": self.model, "effort": self.effort}


def _endpoint(data: Any) -> Endpoint:
    if not isinstance(data, dict):
        return Endpoint()
    return Endpoint(
        agent=_clean(data.get("agent")),
        model=_clean(data.get("model")),
        effort=_clean(data.get("effort")),
    )


@dataclass(frozen=True)
class RoleRouting:
    # Narzędzie całej roli. Slot może je nadpisać dla swojej trudności, bo wybór
    # modelu przesądza o narzędziu: koder na zadaniu prostym bywa modelem
    # lokalnym przez OpenCode, a na złożonym Opusem przez Claude Code.
    agent: str = ""
    # Klucz: nazwa trudności albo ANY_DIFFICULTY.
    slots: dict[str, Endpoint] = field(default_factory=dict)
    fallbacks: tuple[Endpoint, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "slots": {key: value.as_dict() for key, value in self.slots.items()},
            "fallbacks": [entry.as_dict() for entry in self.fallbacks],
        }


@dataclass(frozen=True)
class Routing:
    """Nadpisania operatora; pusta instancja = czysta polityka projektu."""

    roles: dict[str, RoleRouting] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.roles)

    def agent(self, role: str) -> str:
        entry = self.roles.get(role)
        return entry.agent if entry else ""

    def slot(self, role: str, difficulty: str) -> Endpoint:
        """Wybór modelu dla roli i trudności (pusty Endpoint = brak wyboru).

        Rola nieczuła na trudność ma jeden slot ``all``; dla ról czułych slot
        ``all`` zostaje wspólnym ustawieniem, gdy konkretna trudność jest pusta."""
        entry = self.roles.get(role)
        if entry is None:
            return Endpoint()
        definition = ROLE_BY_NAME.get(role)
        if definition is not None and not definition.difficulty_aware:
            return entry.slots.get(ANY_DIFFICULTY, Endpoint())
        specific = entry.slots.get(difficulty, Endpoint())
        if not specific.empty:
            return specific
        return entry.slots.get(ANY_DIFFICULTY, Endpoint())

    def fallbacks(self, role: str) -> tuple[Endpoint, ...]:
        entry = self.roles.get(role)
        return entry.fallbacks if entry else ()

    def agents_in_use(self) -> list[str]:
        """Wszyscy agenci wymienieni w nadpisaniach — także w slotach i łańcuchach."""
        names: list[str] = []
        for entry in self.roles.values():
            for candidate in (entry.agent,
                              *(item.agent for item in entry.slots.values()),
                              *(item.agent for item in entry.fallbacks)):
                if candidate and candidate not in names:
                    names.append(candidate)
        return names

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "roles": {name: entry.as_dict() for name, entry in self.roles.items()},
        }


def parse(data: Any, difficulties: tuple[str, ...] = ()) -> Routing:
    """Zbuduj ``Routing`` z surowego JSON-a, pomijając to, czego nie rozumiemy."""
    if not isinstance(data, dict):
        return Routing()
    raw_roles = data.get("roles")
    if not isinstance(raw_roles, dict):
        return Routing()

    allowed_slots = set(difficulties) | {ANY_DIFFICULTY}
    roles: dict[str, RoleRouting] = {}
    for name, raw in raw_roles.items():
        if name not in ROLE_BY_NAME or not isinstance(raw, dict):
            continue
        slots: dict[str, Endpoint] = {}
        raw_slots = raw.get("slots")
        if isinstance(raw_slots, dict):
            for key, value in raw_slots.items():
                if key not in allowed_slots:
                    continue
                endpoint = _endpoint(value)
                if not endpoint.empty:
                    slots[key] = endpoint
        fallbacks: list[Endpoint] = []
        raw_fallbacks = raw.get("fallbacks")
        if isinstance(raw_fallbacks, list):
            for value in raw_fallbacks:
                endpoint = _endpoint(value)
                if not endpoint.empty:
                    fallbacks.append(endpoint)
        entry = RoleRouting(
            agent=_clean(raw.get("agent")),
            slots=slots,
            fallbacks=tuple(fallbacks),
        )
        if entry.agent or entry.slots or entry.fallbacks:
            roles[name] = entry
    return Routing(roles=roles)


def load(path: Path, difficulties: tuple[str, ...] = ()) -> Routing:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return Routing()
    return parse(raw, difficulties)


def load_from_env(
    environ: dict[str, str] | None = None, difficulties: tuple[str, ...] = ()
) -> Routing:
    """Wczytaj plik wskazany przez ``FORGE_ROUTING_FILE`` albo domyślny.

    Brak pliku nie jest błędem — to po prostu praca na polityce projektu."""
    path = configured_path(environ)
    return load(path, difficulties) if path is not None else Routing()


def save(routing: Routing, path: Path) -> None:
    """Zapisz atomowo — przerwany zapis nie może zostawić uszkodzonego pliku."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(routing.as_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
