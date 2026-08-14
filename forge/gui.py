"""Natywne GUI GTK 4 dla orkiestratora forge.

Uruchomienie:
    python3 -m forge.gui

Panel konfiguracji jest edytorem PROFILI routingu (patrz profiles.py i
routing.py): dla każdej roli wybierasz MODEL RAZEM Z EFFORTEM (a dla ról czułych
na zakres zadania — osobno dla simple/standard/complex) i łańcuch zapasowy.
Model, effort i trasa są jedną pozycją; GPT i Grok zawsze biegną przez OpenCode.
Wybór zapisuje się poza repozytorium, więc obowiązuje też uruchomienia z CLI
i nie wymaga commita przy każdej zmianie dostawcy.

Jedno okno prowadzi KILKA BIEGÓW naraz — każdy ma własny projekt, własny brief,
własny proces, własną zakładkę logu i WŁASNY PROFIL MODELI. Profil jest nazwanym
zestawem nadpisań ról: jeden bieg może chodzić wyłącznie na GPT, a drugi
równolegle mieszać GPT, Claude'a i Groka. Sekcja „Modele ról" edytuje jeden
profil naraz — ten wybrany w jej nagłówku — a wiersz biegu tylko WSKAZUJE profil,
którym ma pracować. Profil wspólny to dotychczasowy ``routing.json``, więc biegi
z linii poleceń niczego nie tracą. Każdy bieg dostaje przy starcie MIGAWKĘ
swojego profilu, więc późniejsze przekręcenie pokrętła nie sięga procesu, który
już pracuje.

Trzy rzeczy, których równoległość nie znosi, mają swoje zamki — wszystkie
rozstrzyga sam orkiestrator, więc obowiązują tak samo drugie okno panelu, jak
i uruchomienie z linii poleceń. Panel tylko w nie ZAGLĄDA, żeby operator dostał
powód przed startem procesu, a nie po nim:

- jeden bieg na katalog projektu (``runlock``),
- kod z migawki (``snapshot``), żeby commit w repozytorium Forge nie wywrócił
  pracującej pętli — patrz ``docs/AWARIE-2026-08-11.md``,
- wyłączność na plikową sesję Claude Code, dopóki nie ma nierotującego tokenu.
  Od czasu profili to pytanie zadaje się PER BIEG: bieg, którego profil nie
  woła Claude'a, nie bierze tego zamku i nie daje się nim zablokować.
"""
from __future__ import annotations

import os
import json
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .config import Config, TASK_DIFFICULTIES, validate_master_agent
from . import catalog
from . import preflight
from . import profiles as profiles_module
from . import report
from . import routing as routing_module
from . import runlock
from . import snapshot
from .adapters import canonical_agent


ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = Path(
    os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
) / "forge" / "gui.json"
MAX_LOG_LINES = 5_000
STOP_TERM_DELAY_S = 8
STOP_KILL_DELAY_S = 5
SESSION_LOG_NAME = "gui_run.log"
ROUTING_RUN_DIR = "routing"
# Zapisy routingu kolejnych biegów tego projektu; starsze są tylko archiwum.
KEEP_ROUTING_SNAPSHOTS = 5
DEFAULT_BRIEF = "game.md"
DEFAULT_PROJECT = "game"
# Dwa biegi to już podwojony rachunek za tokeny i dwa razy szybciej osiągnięty
# limit dostawcy. Sufit jest po to, żeby panel nie zachęcał do skali, której
# ani maszyna, ani konto u dostawcy nie udźwigną.
MAX_RUNS = 4
AGENTS = catalog.AGENTS
MASTER_AGENTS = AGENTS
ROLE_DEFS = routing_module.ROLE_DEFS

DIFFICULTY_LABELS = {
    "simple": "proste",
    "standard": "standardowe",
    "complex": "złożone",
    routing_module.ANY_DIFFICULTY: "wszystkie zadania",
}


def load_settings(path: Path = SETTINGS_PATH) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def save_settings(settings: dict[str, Any], path: Path = SETTINGS_PATH) -> None:
    """Zapisz ustawienia atomowo, aby przerwany zapis nie uszkodził pliku."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def routing_path(environ: dict[str, str] | None = None) -> Path:
    """Plik routingu, który GUI edytuje — ten sam, który czyta orkiestrator.

    Gdy operator wyłączył warstwę (``FORGE_ROUTING_FILE=none``), panel nadal
    musi mieć co edytować i co przekazać uruchamianemu procesowi — inaczej
    wyklikany wybór po cichu nie miałby żadnego skutku. Sięgamy więc po ścieżkę
    domyślną; wyłączenie zostaje w mocy dla uruchomień spoza GUI."""
    environ = os.environ if environ is None else environ
    return (routing_module.configured_path(environ)
            or routing_module.default_path(environ))


def profiles_dir(environ: dict[str, str] | None = None) -> Path:
    """Katalog profili nazwanych — zawsze obok pliku, który panel edytuje."""
    return routing_path(environ).parent / profiles_module.DIRECTORY_NAME


def resolve_path(value: str) -> Path:
    """Ścieżka względna liczona od repozytorium Forge, nie od cwd GUI.

    Historycznie wynikało to z ``cwd=ROOT`` procesu orkiestratora. Proces
    startuje dziś z katalogu MIGAWKI kodu, więc reguła musi być zapisana tutaj
    jawnie, a do orkiestratora idą już ścieżki absolutne — inaczej ``game``
    oznaczałoby katalog wewnątrz migawki."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def resolve_project(project: str) -> Path:
    return resolve_path(project)


def run_settings(settings: dict[str, Any]) -> list[dict[str, str]]:
    """Lista biegów z pliku ustawień; zawsze co najmniej jeden.

    Starszy plik opisywał jeden bieg płaskimi kluczami ``brief``/``project``.
    Migrujemy go do jednoelementowej listy, bo inaczej pierwsze uruchomienie
    nowej wersji wyrzuciłoby operatorowi jego ostatni wybór ścieżek.

    Brak klucza ``profile`` znaczy PROFIL WSPÓLNY — czyli dokładnie to, czym
    pracowały wszystkie biegi, zanim profile powstały."""
    entries = settings.get("runs")
    runs: list[dict[str, str]] = []
    if isinstance(entries, list):
        for entry in entries[:MAX_RUNS]:
            if not isinstance(entry, dict):
                continue
            brief = entry.get("brief")
            project = entry.get("project")
            profile = entry.get("profile")
            runs.append({
                "brief": brief if isinstance(brief, str) else DEFAULT_BRIEF,
                "project": project if isinstance(project, str) else DEFAULT_PROJECT,
                "profile": (profile if isinstance(profile, str)
                            else profiles_module.SHARED_SLUG),
            })
    if runs:
        return runs
    legacy_brief = settings.get("brief")
    legacy_project = settings.get("project")
    return [{
        "brief": legacy_brief if isinstance(legacy_brief, str) else DEFAULT_BRIEF,
        "project": (legacy_project if isinstance(legacy_project, str)
                    else DEFAULT_PROJECT),
        "profile": profiles_module.SHARED_SLUG,
    }]


def validate_routing(routing: routing_module.Routing) -> None:
    """Odrzuć wybór, którego orkiestrator i tak nie wykona.

    Mistrz bez trybu cienkiego traci sens (patrz config.validate_master_agent),
    a wpis zapasowy jest tu równie wiążący jak pierwszy wybór — inaczej zakaz
    obowiązywałby tylko do pierwszej awarii."""
    for definition in ROLE_DEFS:
        entry = routing.roles.get(definition.name)
        if entry is None or definition.allows_codex:
            continue
        for agent in (entry.agent,
                      *(item.agent for item in entry.slots.values()),
                      *(item.agent for item in entry.fallbacks)):
            if agent:
                validate_master_agent(agent)


@dataclass(frozen=True)
class Launch:
    """Wszystko, czego potrzebuje ``Popen``, w jednym miejscu."""

    command: list[str]
    env: dict[str, str]
    cwd: Path


def build_launch(
    brief: str, project: str, routing: routing_module.Routing,
    routing_file: Path | None = None,
    code_root: Path | None = None,
) -> Launch:
    """Zbuduj bezpieczne argv, środowisko i katalog startowy orkiestratora.

    Konfiguracja ról jedzie JEDNYM kanałem — plikiem routingu. Stare zmienne
    ``FORGE_<ROLA>_AGENT/MODEL/EFFORT`` z powłoki są tu czyszczone, żeby wybór
    z GUI nie przegrywał po cichu z zapomnianym exportem.

    ``code_root`` to katalog zawierający pakiet ``forge`` — migawka kodu na czas
    tego biegu. Ścieżki briefu i projektu idą dalej ABSOLUTNE: proces nie
    startuje już z repozytorium, więc ``game`` nie może znaczyć „obok kodu"."""
    if not brief.strip():
        raise ValueError("Wskaż plik z briefem.")
    if not project.strip():
        raise ValueError("Wskaż katalog projektu.")
    validate_routing(routing)

    env = os.environ.copy()
    for definition in ROLE_DEFS:
        for suffix in ("AGENT", "MODEL", "EFFORT"):
            env.pop(f"FORGE_{definition.name.upper()}_{suffix}", None)
    env["FORGE_ROUTING_FILE"] = str(routing_file or routing_path())

    root = Path(code_root) if code_root is not None else ROOT
    # cwd wystarczyłoby do zaimportowania pakietu, ale PYTHONPATH przeżywa też
    # ewentualną zmianę katalogu przez sam proces.
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (f"{root}{os.pathsep}{existing}" if existing
                         else str(root))

    command = [
        sys.executable,
        "-u",
        "-m",
        "forge.orchestrate",
        "--non-interactive",
        "--brief",
        str(resolve_path(brief.strip())),
        "--project",
        str(resolve_project(project.strip())),
    ]
    return Launch(command, env, root)


def line_kind(line: str) -> str:
    upper = line.upper()
    if any(word in upper for word in ("BŁĄD", "NIEPOWODZENIE", "CZERWON", "ODRZUCON", "FAIL")):
        return "error"
    if any(word in upper for word in ("UKOŃCZONE", "ZWERYFIKOWANY", "ZIELON", "PREFLIGHT OK", "PUSH →")):
        return "success"
    if any(word in upper for word in ("UWAGA", "LIMIT", "WZNAWIAM", "PRZEŁĄCZAM")):
        return "warning"
    if "===" in line or "##########" in line or re.search(r"\b(PLAN|TESTER|KODER|RECENZJA)\b", upper):
        return "phase"
    return "normal"


def trim_log_buffer(buffer: Gtk.TextBuffer, max_lines: int = MAX_LOG_LINES) -> None:
    """Usuń najstarsze linie; obsłuż oba warianty API PyGObject."""
    overflow = buffer.get_line_count() - max_lines
    if overflow <= 0:
        return
    result = buffer.get_iter_at_line(overflow)
    cutoff = result[1] if isinstance(result, tuple) else result
    buffer.delete(buffer.get_start_iter(), cutoff)


def prune_routing_snapshots(directory: Path, keep: int = KEEP_ROUTING_SNAPSHOTS,
                            protect: Path | None = None) -> None:
    """Zostaw ostatnie ``keep`` zapisów routingu; resztę usuń (best-effort).

    Kolejność bierzemy z czasu modyfikacji, nie z nazwy: w obrębie jednej
    sekundy nazwy różni już tylko losowy sufiks, więc sortowanie alfabetyczne
    potrafiłoby uznać świeży plik za najstarszy. ``protect`` to plik, który
    właśnie czyta startujący proces — jego nie ruszamy niezależnie od wszystkiego."""
    try:
        files = sorted(directory.glob("run-*.json"),
                       key=lambda path: path.stat().st_mtime_ns)
    except OSError:
        return
    doomed = files[:-keep] if keep else files
    for path in doomed:
        if protect is not None and path == protect:
            continue
        try:
            path.unlink()
        except OSError:
            continue


def format_elapsed(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _string_list(values: list[str]) -> Gtk.StringList:
    return Gtk.StringList.new(values)


def _searchable(dropdown: Gtk.DropDown) -> None:
    """Włącz wyszukiwanie — lista modeli ma kilkadziesiąt pozycji."""
    dropdown.set_expression(
        Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
    dropdown.set_enable_search(True)


@dataclass(frozen=True)
class Choice:
    """Jedna pozycja listy modeli.

    ``kind`` decyduje, co trafi do routingu:
    - ``model`` — konkretny model, effort i narzędzie z wybranej trasy;
    - ``saved``   — starszy model spoza katalogu, zachowany bez możliwości
      tworzenia kolejnych ręcznych wpisów."""

    kind: str
    label: str
    agent: str = ""
    entry: catalog.ModelEntry | None = None
    route: catalog.Route | None = None
    effort: str = ""
    model: str = ""


class ModelChooser(Gtk.Box):
    """Wybór modelu dla jednego slotu (rola × trudność albo wpis zapasowy).

    Operator wybiera MODEL I EFFORT w jednym kroku; narzędzie wynika z modelu.
    GPT i Grok mają tylko trasy OpenCode. Jeśli model ma kilka tras, każda jest
    osobną pozycją tej samej listy — nie ma drugiego pokrętła.

    Lista pochodzi z katalogu. Model spoza katalogu może pojawić się wyłącznie
    przy odtworzeniu istniejącego routingu, aby zapis nie utracił danych."""

    def __init__(self, role: str, difficulty: str, agents: tuple[str, ...],
                 entries: tuple[catalog.ModelEntry, ...],
                 on_change: Callable[[], None] | None = None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._on_change = on_change
        self.role = role
        self.difficulty = difficulty
        self.agents = tuple(agents)
        self._entries = tuple(
            restricted for restricted in
            (entry.restricted(self.agents) for entry in entries)
            if restricted is not None)
        self._choices = self._build_choices()
        self._muted = False

        self.model = Gtk.DropDown(model=_string_list(
            [choice.label for choice in self._choices]))
        self.model.set_hexpand(True)
        _searchable(self.model)
        self.model.connect("notify::selected", self._model_changed)
        self.append(self.model)

    # --- budowa listy -----------------------------------------------------
    def _build_choices(self) -> list[Choice]:
        choices: list[Choice] = []
        for entry in self._entries:
            for route in entry.routes:
                for effort in catalog.configured_efforts(route):
                    label = f"{entry.name}  ·  {self._effort_label(effort)}"
                    if entry.ambiguous:
                        label += f"  ·  {route.provider}"
                    choices.append(Choice(
                        "model", label, entry=entry, route=route, effort=effort))
        return choices

    @staticmethod
    def _effort_label(effort: str) -> str:
        return "effort auto" if not effort else f"effort {effort}"

    def choices(self) -> tuple[Choice, ...]:
        """Pozycje listy w kolejności pokrętła (także dla testów interakcji)."""
        return tuple(self._choices)

    def _choice(self) -> Choice:
        index = self.model.get_selected()
        if index == Gtk.INVALID_LIST_POSITION or not 0 <= index < len(self._choices):
            return self._choices[0]
        return self._choices[index]

    def _selected_agent(self, choice: Choice) -> str:
        if choice.kind == "model" and choice.route is not None:
            return choice.route.agent
        if choice.kind == "saved":
            return choice.agent
        return ""

    # --- stan -------------------------------------------------------------
    def set_value(self, agent: str, model: str, effort: str) -> None:
        """Odtwórz wybór zapisany w routingu (agent, model, effort)."""
        self._muted = True
        # Stare pliki mogły wskazywać natywne CLI. Panel migruje rodzinę
        # modelu do mostu zamiast ponownie wystawiać wyłączoną trasę.
        native = canonical_agent(agent)
        if model and native in catalog.DISABLED_NATIVE_AGENTS:
            provider = "openai" if native == "codex" else "xai"
            agent, model = "opencode", f"{provider}/{model}"
            if effort == "xhigh":
                effort = "max"
        index = self._locate(agent, model, effort)
        self.model.set_selected(index)
        self._muted = False

    def _locate(self, agent: str, model: str, effort: str) -> int:
        """Zwróć pozycję konkretnej trasy, zachowując starszy model."""
        if model:
            found = catalog.lookup(agent, model, self._entries) if agent else None
            if found is None and not agent:
                found = self._by_model_only(model)
            if found is not None:
                entry, route = found
                for index, choice in enumerate(self._choices):
                    if (choice.kind == "model" and choice.route is route
                            and choice.effort == effort):
                        return index
            label = f"{catalog.identity(agent, model)}  ·  {self._effort_label(effort)}"
            self._choices.append(Choice(
                "saved", label, agent=agent, effort=effort, model=model))
            self.model.set_model(_string_list(
                [choice.label for choice in self._choices]))
            return len(self._choices) - 1
        return 0

    def _by_model_only(self, model: str) -> tuple[catalog.ModelEntry, catalog.Route] | None:
        """Model bez agenta (starszy plik): trasa jednoznaczna albo nic."""
        matches = [(entry, route) for entry in self._entries
                   for route in entry.routes if route.model == model]
        return matches[0] if len(matches) == 1 else None

    def value(self) -> tuple[str, str, str]:
        choice = self._choice()
        agent = self._selected_agent(choice)
        if choice.kind == "model" and choice.route is not None:
            model = choice.route.model
        elif choice.kind == "saved":
            model = choice.model
        else:
            model = ""
        return agent, model, choice.effort

    def endpoint(self) -> routing_module.Endpoint:
        agent, model, effort = self.value()
        return routing_module.Endpoint(agent=agent, model=model, effort=effort)

    def set_sensitive_fields(self, enabled: bool) -> None:
        self.model.set_sensitive(enabled)

    # --- zdarzenia --------------------------------------------------------
    def _model_changed(self, *_args: Any) -> None:
        was_muted = self._muted
        self._muted = True
        choice = self._choice()
        self._muted = was_muted
        self._changed()

    def _changed(self) -> None:
        if not self._muted and self._on_change is not None:
            self._on_change()


class FallbackRow(Gtk.Box):
    """Jeden wpis łańcucha zapasowego: model (z dostawcą) + usunięcie."""

    def __init__(self, role: str, agents: tuple[str, ...],
                 entries: tuple[catalog.ModelEntry, ...],
                 on_change: Callable[[], None], on_remove: Callable[["FallbackRow"], None]):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.role = role
        self._on_change = on_change

        self.model = ModelChooser(role, "standard", agents, entries, on_change)
        self.model.set_hexpand(True)
        remove = Gtk.Button(icon_name="user-trash-symbolic",
                            tooltip_text="Usuń ten zapas")
        remove.add_css_class("flat")
        remove.connect("clicked", lambda _button: on_remove(self))

        self.append(self.model)
        self.append(remove)
        self.remove_button = remove

    def values(self) -> tuple[str, str, str]:
        return self.model.value()

    def set_values(self, agent: str, model: str, effort: str) -> None:
        self.model.set_value(agent, model, effort)

    def endpoint(self) -> routing_module.Endpoint:
        return self.model.endpoint()

    def set_sensitive_fields(self, enabled: bool) -> None:
        self.model.set_sensitive_fields(enabled)
        self.remove_button.set_sensitive(enabled)


class RoleCard(Gtk.Expander):
    """Karta jednej roli: modele per trudność i łańcuch zapasowy.

    Narzędzia nie wybiera się osobno — wynika z modelu (patrz ModelChooser),
    więc karta zapisuje je razem z modelem w SLOCIE, a pola ``agent`` całej roli
    nie dotyka. Slot jest wystarczający: rola nieczuła na trudność ma jeden slot
    wspólny, a łańcuch zapasowy i tak dziedziczy narzędzie po pierwszym
    wyborze."""

    def __init__(self, definition: routing_module.RoleDef,
                 defaults: Config, on_change: Callable[[], None],
                 entries: tuple[catalog.ModelEntry, ...] | None = None):
        super().__init__()
        self.definition = definition
        self.role = definition.name
        self.agents = AGENTS if definition.allows_codex else MASTER_AGENTS
        self.entries = catalog.index() if entries is None else entries
        self._on_change = on_change
        self._muted = True
        self.add_css_class("role-card")

        self.summary = Gtk.Label(xalign=0)
        self.summary.set_use_markup(True)
        self.set_label_widget(self.summary)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        body.set_margin_top(10)
        body.set_margin_start(6)
        body.set_margin_bottom(4)

        subtitle = Gtk.Label(label=definition.description, xalign=0, wrap=True)
        subtitle.add_css_class("dim-label")
        subtitle.add_css_class("caption")
        body.append(subtitle)

        self.slots: dict[str, ModelChooser] = {}
        slot_keys = (TASK_DIFFICULTIES if definition.difficulty_aware
                     else (routing_module.ANY_DIFFICULTY,))
        for key in slot_keys:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            caption = Gtk.Label(label=DIFFICULTY_LABELS.get(key, key), xalign=0)
            caption.add_css_class("field-label")
            caption.set_size_request(120, -1)
            difficulty = ("standard" if key == routing_module.ANY_DIFFICULTY
                          else key)
            chooser = ModelChooser(
                self.role, difficulty, self.agents, self.entries, self._changed)
            chooser.set_value(*defaults.role(self.role, difficulty))
            chooser.set_hexpand(True)
            row.append(caption)
            row.append(chooser)
            body.append(row)
            self.slots[key] = chooser

        fallback_caption = Gtk.Label(
            label="Łańcuch zapasowy — próbowany po wyczerpaniu limitu albo "
                  "twardej awarii poprzednika",
            xalign=0, wrap=True)
        fallback_caption.add_css_class("field-label")
        body.append(fallback_caption)
        self.fallback_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.append(self.fallback_box)
        self.add_fallback_button = Gtk.Button(label="+  Dodaj zapas")
        self.add_fallback_button.add_css_class("flat")
        self.add_fallback_button.connect(
            "clicked", lambda _button: self._add_fallback())
        body.append(self.add_fallback_button)

        self.fallbacks: list[FallbackRow] = []
        self.set_child(body)
        self._muted = False
        self._update_summary()

    # --- stan -------------------------------------------------------------
    def apply(self, entry: routing_module.RoleRouting) -> None:
        self._muted = True
        # Starsze GUI zapisywało mistrza w trzech slotach. Po uproszczeniu
        # zachowaj jego ostatnią typową (standardową) wartość jako jeden wybór.
        if (not self.definition.difficulty_aware
                and routing_module.ANY_DIFFICULTY not in entry.slots
                and entry.slots):
            legacy = (entry.slots.get("standard")
                      or entry.slots.get("complex")
                      or entry.slots.get("simple"))
            entry = routing_module.RoleRouting(
                agent=entry.agent,
                slots={routing_module.ANY_DIFFICULTY: legacy},
                fallbacks=entry.fallbacks)
        resolved = Config(routing=routing_module.Routing(
            roles={self.role: entry}))
        for key, chooser in self.slots.items():
            difficulty = ("standard" if key == routing_module.ANY_DIFFICULTY
                          else key)
            chooser.set_value(*resolved.role(self.role, difficulty))
        for row in list(self.fallbacks):
            self._remove_fallback(row, notify=False)
        for endpoint in entry.fallbacks:
            row = self._add_fallback(notify=False)
            row.set_values(endpoint.agent, endpoint.model, endpoint.effort)
        self._muted = False
        self._update_summary()

    def routing_entry(self) -> routing_module.RoleRouting:
        slots: dict[str, routing_module.Endpoint] = {}
        for key, chooser in self.slots.items():
            endpoint = chooser.endpoint()
            if not endpoint.empty:
                slots[key] = endpoint
        fallbacks = tuple(row.endpoint() for row in self.fallbacks
                          if not row.endpoint().empty)
        return routing_module.RoleRouting(slots=slots, fallbacks=fallbacks)

    def set_sensitive_fields(self, enabled: bool) -> None:
        self.add_fallback_button.set_sensitive(enabled)
        for chooser in self.slots.values():
            chooser.set_sensitive_fields(enabled)
        for row in self.fallbacks:
            row.set_sensitive_fields(enabled)

    # --- zdarzenia --------------------------------------------------------
    def _add_fallback(self, notify: bool = True) -> FallbackRow:
        row = FallbackRow(self.role, self.agents, self.entries, self._changed,
                          self._remove_fallback)
        self.fallbacks.append(row)
        self.fallback_box.append(row)
        if notify:
            self._changed()
        return row

    def _remove_fallback(self, row: FallbackRow, notify: bool = True) -> None:
        if row not in self.fallbacks:
            return
        self.fallbacks.remove(row)
        self.fallback_box.remove(row)
        if notify:
            self._changed()

    def _changed(self) -> None:
        self._update_summary()
        if not self._muted:
            self._on_change()

    def _update_summary(self) -> None:
        entry = self.routing_entry()
        chosen = [endpoint.model
                  for endpoint in entry.slots.values()
                  if endpoint.model]
        if not chosen:
            detail = "brak modelu"
        elif len(set(chosen)) == 1 and len(chosen) == len(self.slots):
            detail = chosen[0]
        else:
            detail = f"{len(chosen)}/{len(self.slots)} slotów wybranych"
        if entry.fallbacks:
            detail += f"  •  zapas ×{len(entry.fallbacks)}"
        self.summary.set_markup(
            f"<b>{GLib.markup_escape_text(self.definition.title)}</b>  "
            f"<span alpha='65%'>{GLib.markup_escape_text(detail)}</span>")


class Run:
    """Jeden bieg: własny projekt, własny proces, własny log, własny profil.

    Cały stan przebiegu (proces, czas startu, bufor logu, uchwyt pliku sesji)
    należy do TEGO obiektu, a nie do okna — inaczej drugi bieg nadpisywałby
    pierwszemu wszystko, od czasu startu po plik logu.

    Profil trzymamy jako SLUG, a nie jako pozycję na liście ani gotowy
    ``Routing``: lista zmienia kolejność przy każdym przemianowaniu, a kopia
    routingu rozjechałaby się z tym, co operator właśnie wyklikał w sekcji
    modeli. Rozstrzygający odczyt następuje dopiero przy starcie biegu."""

    def __init__(self, owner: "ForgeWindow", brief: str, project: str,
                 profile: str = profiles_module.SHARED_SLUG):
        self.owner = owner
        self.process: subprocess.Popen[str] | None = None
        self.started_at = 0.0
        self.stop_requested = False
        self._session_log_fh: Any = None
        self.profile_slug = profile

        self.brief = Gtk.Entry(text=brief, placeholder_text=DEFAULT_BRIEF)
        self.project = Gtk.Entry(text=project, placeholder_text=DEFAULT_PROJECT)
        self.path_buttons: list[Gtk.Button] = []
        self.status = Gtk.Label(label="Gotowy")
        self.status.add_css_class("status-pill")
        self.status.add_css_class("status-idle")
        self.elapsed = Gtk.Label(label="Jeszcze nie uruchomiono", xalign=0)
        self.elapsed.add_css_class("dim-label")

        self.log_buffer = Gtk.TextBuffer()
        for name, color, weight in (
            ("normal", "#c8d3e0", 400),
            ("success", "#6fe7a7", 600),
            ("error", "#ff8e91", 600),
            ("warning", "#ffd37a", 500),
            ("phase", "#82b7ff", 700),
        ):
            self.log_buffer.create_tag(name, foreground=color, weight=weight)
        self.log_view = Gtk.TextView(buffer=self.log_buffer, editable=False,
                                     cursor_visible=False)
        self.log_view.set_monospace(True)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.log_view.set_top_margin(14)
        self.log_view.set_bottom_margin(14)
        self.log_view.set_left_margin(14)
        self.log_view.set_right_margin(14)

        self.panel = self._build_panel()
        self.page = self._build_log_page()
        self.tab_label = Gtk.Label(label=self.title())
        self._load_previous_run()

    # --- widgety ----------------------------------------------------------
    def _build_panel(self) -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        panel.add_css_class("run-card")

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.title_label = Gtk.Label(xalign=0)
        self.title_label.set_hexpand(True)
        self.title_label.set_use_markup(True)
        head.append(self.title_label)
        head.append(self.status)
        self.start_button = Gtk.Button(label="▶  Start")
        self.start_button.add_css_class("suggested-action")
        self.start_button.connect("clicked", lambda _button: self.start())
        self.stop_button = Gtk.Button(label="Zatrzymaj")
        self.stop_button.add_css_class("destructive-action")
        self.stop_button.set_sensitive(False)
        self.stop_button.connect("clicked", lambda _button: self.stop())
        self.remove_button = Gtk.Button(icon_name="user-trash-symbolic",
                                        tooltip_text="Usuń ten bieg z panelu")
        self.remove_button.add_css_class("flat")
        self.remove_button.connect(
            "clicked", lambda _button: self.owner.remove_run(self))
        head.append(self.start_button)
        head.append(self.stop_button)
        head.append(self.remove_button)
        panel.append(head)

        paths = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        for label, entry, callback in (
            ("Brief", self.brief, self._choose_brief),
            ("Katalog projektu", self.project, self._choose_project),
        ):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            caption = Gtk.Label(label=label, xalign=0)
            caption.add_css_class("field-label")
            box.append(caption)
            controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            entry.set_hexpand(True)
            entry.connect("changed", self._paths_changed)
            controls.append(entry)
            choose = Gtk.Button(icon_name="folder-open-symbolic",
                                tooltip_text=f"Wybierz: {label}")
            choose.connect("clicked", callback)
            self.path_buttons.append(choose)
            controls.append(choose)
            box.append(controls)
            box.set_hexpand(True)
            paths.append(box)
        panel.append(paths)
        panel.append(self._build_profile_row())
        self._update_title()
        return panel

    def _build_profile_row(self) -> Gtk.Widget:
        """Wiersz wyboru profilu modeli dla tego biegu.

        Sam wybór, bez edycji: gdyby każdy bieg miał własny komplet kart ról,
        panel powtórzyłby jedenaście kart tyle razy, ile jest biegów — a dwa
        biegi na tym samym profilu rozjeżdżałyby się przy pierwszej zmianie."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        caption = Gtk.Label(label="Profil modeli", xalign=0)
        caption.add_css_class("field-label")
        caption.set_size_request(120, -1)
        row.append(caption)

        self._profile_choices: list[tuple[str, str]] = []
        self.profile = Gtk.DropDown(model=_string_list([]))
        self.profile.set_hexpand(True)
        _searchable(self.profile)
        self.profile.connect("notify::selected", self._profile_selected)
        row.append(self.profile)

        self.edit_profile_button = Gtk.Button(
            label="Modele…",
            tooltip_text="Pokaż role tego profilu w sekcji poniżej")
        self.edit_profile_button.add_css_class("flat")
        self.edit_profile_button.connect(
            "clicked", lambda _button: self.owner.edit_profile(self.profile_slug))
        row.append(self.edit_profile_button)
        self.refresh_profiles()
        return row

    def _profile_options(self) -> list[tuple[str, str]]:
        """Pary (slug, etykieta) na pokrętło profilu.

        Wybór wskazujący profil, którego już nie ma (skasowany w drugim oknie,
        usunięty plik), zostaje NA LIŚCIE z adnotacją. Ciche przestawienie na
        profil wspólny byłoby najgorszym z możliwych wyników: bieg ruszyłby
        z modelami, których operator dla tego projektu nie wybrał."""
        options = [(profile.slug, profile.name)
                   for profile in self.owner.profiles.profiles()]
        if all(slug != self.profile_slug for slug, _label in options):
            options.append((self.profile_slug, f"{self.profile_slug} — BRAK"))
        return options

    def refresh_profiles(self) -> None:
        """Odśwież pokrętło po zmianie listy profili, zachowując wybór biegu.

        Model podmieniamy tylko przy realnej zmianie listy — zwolnienie
        poprzedniego ``Gtk.StringList`` pod trwającą emisją sygnału kończy się
        notyfikacją na obiekcie już zwolnionym (patrz
        ``ForgeWindow._refresh_profile_chooser``)."""
        choices = self._profile_options()
        self._profile_muted = True
        # Zaznaczenie ustawiamy ZAWSZE: bieg bywa przestawiany na inny profil
        # przy niezmienionej liście (np. po usunięciu wraca na wspólny).
        if choices != self._profile_choices:
            self._profile_choices = choices
            self.profile.set_model(_string_list(
                [label for _slug, label in self._profile_choices]))
        for index, (slug, _label) in enumerate(self._profile_choices):
            if slug == self.profile_slug:
                self.profile.set_selected(index)
                break
        self._profile_muted = False

    def _profile_selected(self, *_args: Any) -> None:
        if getattr(self, "_profile_muted", False):
            return
        index = self.profile.get_selected()
        if index == Gtk.INVALID_LIST_POSITION or not 0 <= index < len(
                self._profile_choices):
            return
        slug = self._profile_choices[index][0]
        if slug == self.profile_slug:
            return
        self.profile_slug = slug
        self.owner.save_paths()

    def set_profile(self, slug: str) -> None:
        """Przestaw bieg na inny profil (np. po usunięciu poprzedniego)."""
        self.profile_slug = slug
        self.refresh_profiles()

    def _build_log_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.set_margin_top(16)
        page.set_margin_bottom(16)
        page.set_margin_start(16)
        page.set_margin_end(16)
        page.append(self.elapsed)
        log_scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        log_scroll.add_css_class("log-surface")
        log_scroll.set_child(self.log_view)
        page.append(log_scroll)
        return page

    def title(self) -> str:
        """Nazwa biegu w zakładce: katalog projektu mówi więcej niż numer."""
        text = self.project.get_text().strip()
        return Path(text).name if text else "Nowy bieg"

    def _update_title(self) -> None:
        self.title_label.set_markup(
            f"<b>{GLib.markup_escape_text(self.title())}</b>")

    def _paths_changed(self, _entry: Gtk.Entry) -> None:
        self._update_title()
        self.tab_label.set_label(self.title())
        # Same ścieżki, bez routingu: zapis pliku ról przy każdym znaku byłby
        # kilkoma kilobajtami na naciśnięcie klawisza.
        self.owner.save_paths()

    def _choose_brief(self, _button: Gtk.Button) -> None:
        self.owner.open_chooser("Wybierz plik z briefem",
                                Gtk.FileChooserAction.OPEN, self.brief)

    def _choose_project(self, _button: Gtk.Button) -> None:
        self.owner.open_chooser("Wybierz katalog projektu",
                                Gtk.FileChooserAction.SELECT_FOLDER, self.project)

    def settings(self) -> dict[str, str]:
        return {"brief": self.brief.get_text(),
                "project": self.project.get_text(),
                "profile": self.profile_slug}

    def project_path(self) -> Path:
        return resolve_project(self.project.get_text())

    # --- log --------------------------------------------------------------
    def append_log(self, line: str) -> None:
        end = self.log_buffer.get_end_iter()
        self.log_buffer.insert_with_tags_by_name(
            end, line.rstrip() + "\n", line_kind(line))
        trim_log_buffer(self.log_buffer)
        mark = self.log_buffer.create_mark(None, self.log_buffer.get_end_iter(), False)
        self.log_view.scroll_to_mark(mark, 0.05, True, 0.0, 1.0)
        self.log_buffer.delete_mark(mark)
        if self._session_log_fh is not None:
            try:
                self._session_log_fh.write(line.rstrip() + "\n")
                self._session_log_fh.flush()
            except OSError:
                pass

    def _load_previous_run(self) -> None:
        """Po (re)starcie GUI pokaż log i statystyki poprzedniego biegu forge,
        zanim jeszcze cokolwiek uruchomimy — nawet jeśli poprzedni proces
        zginął bez pożegnania (SIGKILL, awaria)."""
        project = self.project_path()
        log_path = project / ".forge" / SESSION_LOG_NAME
        if not log_path.is_file():
            return
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        if not content.strip():
            return
        stamp = time.strftime("%Y-%m-%d %H:%M:%S",
                              time.localtime(log_path.stat().st_mtime))
        self.append_log(
            f"===== Log poprzedniego uruchomienia ({stamp}) — {project} =====")
        for line in content.splitlines():
            self.append_log(line)
        try:
            stats = report.usage_summary(str(project))
        except OSError:
            stats = ""
        if stats:
            self.append_log("")
            self.append_log("===== Zużycie tokenów (łącznie w tym projekcie) =====")
            for line in stats.splitlines():
                self.append_log(line)
        self.append_log("")
        self.append_log("===== Gotowy do nowego uruchomienia =====")

    def _open_session_log(self) -> None:
        self._close_session_log()
        try:
            runtime = self.project_path() / ".forge"
            runtime.mkdir(parents=True, exist_ok=True)
            self._session_log_fh = open(
                runtime / SESSION_LOG_NAME, "w", encoding="utf-8")
        except OSError:
            self._session_log_fh = None

    def _close_session_log(self) -> None:
        if self._session_log_fh is not None:
            try:
                self._session_log_fh.close()
            except OSError:
                pass
            self._session_log_fh = None

    def show_error(self, message: str) -> None:
        self.append_log(f"BŁĄD: {message}")
        self.status.set_label("Błąd")
        self.status.remove_css_class("status-idle")
        self.status.remove_css_class("status-running")
        self.status.add_css_class("status-error")
        self.owner.refresh_state()

    # --- cykl życia -------------------------------------------------------
    def is_running(self) -> bool:
        return self.process is not None

    def _set_running(self, running: bool, label: str | None = None) -> None:
        self.start_button.set_sensitive(not running)
        self.stop_button.set_sensitive(running)
        self.brief.set_sensitive(not running)
        self.project.set_sensitive(not running)
        # Profil pracującego biegu jest już rozstrzygnięty — proces czyta
        # własną migawkę, więc przestawienie pokrętła i tak by nim nie sięgnęło.
        # Pole zamknięte, żeby nie obiecywało zmiany, której nie będzie.
        self.profile.set_sensitive(not running)
        self.remove_button.set_sensitive(not running)
        for button in self.path_buttons:
            button.set_sensitive(not running)
        self.status.set_label(label or ("Pracuje" if running else "Gotowy"))
        self.status.remove_css_class("status-idle")
        self.status.remove_css_class("status-running")
        self.status.remove_css_class("status-error")
        self.status.add_css_class("status-running" if running else "status-idle")
        self.owner.refresh_state()

    def routing_snapshot(self, routing: routing_module.Routing) -> Path:
        """Zapisz routing, którym pracuje TEN bieg, obok jego stanu.

        Plik profilu jest edytowalny w trakcie pracy (drugi bieg trzeba przecież
        skonfigurować, a bywa, że na tym samym profilu), więc bieg dostaje własną
        kopię. Przy okazji zostaje ślad, czym naprawdę pracował — razem z nazwą
        profilu, bo inaczej porównanie kosztu dwóch projektów opiera się na
        pamięci operatora.

        Nazwa jest JEDNORAZOWA. Stała kolidowałaby przy dwóch startach tego
        samego projektu z różnych okien: zapis wyprzedza zamek projektu (a
        ``Config`` czyta plik jeszcze przed jego przejęciem), więc drugi start
        podmieniłby plik pierwszemu i zwycięski proces wystartowałby z cudzym
        routingiem."""
        directory = self.project_path() / ".forge" / ROUTING_RUN_DIR
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = directory / f"run-{stamp}-{uuid.uuid4().hex[:8]}.json"
        label = self.owner.profiles.label(self.profile_slug)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            routing_module.save(routing, path, {"name": label})
        except OSError as exc:
            # Awaryjny powrót do PLIKU TEGO PROFILU, nie do wspólnego: bieg ma
            # ruszyć modelami, które dla niego wybrano, albo nie ruszyć wcale.
            self.append_log(
                f"UWAGA: nie udało się zapisać migawki routingu ({exc}); "
                f"używam pliku profilu „{label}”.")
            return self.owner.profiles.path(self.profile_slug)
        prune_routing_snapshots(directory, protect=path)
        return path

    def start(self) -> None:
        if self.is_running():
            return
        problem = self.owner.blocking_problem(self)
        if problem:
            self.show_error(problem)
            return
        try:
            routing = self.owner.run_routing(self)
            code = snapshot.create()
            launch = build_launch(
                self.brief.get_text(), self.project.get_text(), routing,
                self.routing_snapshot(routing), code.path)
            self.owner.save_settings()
            self.process = subprocess.Popen(
                launch.command,
                cwd=str(launch.cwd),
                env=launch.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            self.show_error(str(exc))
            return

        self.log_buffer.set_text("")
        self._open_session_log()
        self.started_at = time.monotonic()
        self.stop_requested = False
        self._set_running(True)
        self.append_log(
            f"Uruchamiam forge — {code.describe()}, "
            f"profil modeli „{self.owner.profiles.label(self.profile_slug)}”")
        threading.Thread(target=self._read_process, daemon=True).start()
        GLib.timeout_add_seconds(1, self._update_elapsed)

    def _read_process(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            GLib.idle_add(self.append_log, line)
        code = process.wait()
        GLib.idle_add(self._process_finished, code)

    def _process_finished(self, code: int) -> bool:
        elapsed = max(0, time.monotonic() - self.started_at)
        self.process = None
        if self.stop_requested or code == 130:
            self._set_running(False, "Zatrzymano")
            self.append_log(
                f"Proces zatrzymany (actual elapsed: {format_elapsed(elapsed)}).")
        elif code == 0:
            self._set_running(False, "Ukończono")
            self.append_log(
                f"Proces zakończony poprawnie (actual elapsed: {format_elapsed(elapsed)}).")
        else:
            self._set_running(False)
            self.show_error(
                f"Proces zakończył się kodem {code} "
                f"(actual elapsed: {format_elapsed(elapsed)}).")
        self._close_session_log()
        self.owner.run_finished(self)
        return GLib.SOURCE_REMOVE

    def _update_elapsed(self) -> bool:
        if self.process is None:
            return GLib.SOURCE_REMOVE
        self.elapsed.set_label(
            f"Czas biegu  {format_elapsed(time.monotonic() - self.started_at)}")
        return GLib.SOURCE_CONTINUE

    def stop(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        self.stop_button.set_sensitive(False)
        self.status.set_label("Zatrzymywanie…")
        self.stop_requested = True
        self.append_log("Wysyłam bezpieczne przerwanie — stan zostanie zapisany…")
        try:
            os.killpg(self.process.pid, signal.SIGINT)
        except OSError:
            pass
        process = self.process
        GLib.timeout_add_seconds(
            STOP_TERM_DELAY_S, self._escalate_stop, process, signal.SIGTERM)

    def _escalate_stop(
        self, process: subprocess.Popen[str], next_signal: signal.Signals
    ) -> bool:
        if self.process is not process or process.poll() is not None:
            return GLib.SOURCE_REMOVE
        if next_signal == signal.SIGTERM:
            self.append_log("Proces nie odpowiedział — wysyłam SIGTERM…")
            following = signal.SIGKILL
            delay = STOP_KILL_DELAY_S
        else:
            self.append_log("Proces nadal nie odpowiada — wymuszam zakończenie…")
            following = None
            delay = 0
        try:
            os.killpg(process.pid, next_signal)
        except OSError:
            return GLib.SOURCE_REMOVE
        if following is not None:
            GLib.timeout_add_seconds(delay, self._escalate_stop, process, following)
        return GLib.SOURCE_REMOVE


class ForgeWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title="Forge — panel sterowania")
        self.settings = load_settings()
        self.routing_file = routing_path()
        self.profiles = profiles_module.Store.load(
            self.routing_file, profiles_dir(), TASK_DIFFICULTIES)
        # Profil pokazywany w sekcji „Modele ról". Wiersze biegów wskazują
        # profile niezależnie od tego wyboru — edytujemy jeden naraz, bo karty
        # ról są kosztowne (jedenaście ról × do trzech pokręteł na katalogu
        # kilkudziesięciu modeli), a ich powielenie na bieg nic by nie wniosło.
        self.editing_slug = profiles_module.SHARED_SLUG
        window_settings = self.settings.get("window", {})
        if not isinstance(window_settings, dict):
            window_settings = {}
        width = window_settings.get("width", 1180)
        height = window_settings.get("height", 780)
        self.set_default_size(
            width if isinstance(width, int) and width >= 860 else 1180,
            height if isinstance(height, int) and height >= 620 else 780,
        )
        self.set_size_request(860, 620)
        self.runs: list[Run] = []
        self._closing = False
        self._chooser: Gtk.FileChooserNative | None = None
        self._ready = False
        # Podmiana zawartości kart przy przełączaniu profilu nie jest wyborem
        # operatora, więc nie może zostać zapisana jako jego wybór.
        self._applying_profile = False

        header = Adw.HeaderBar()
        title = Adw.WindowTitle(title="Forge", subtitle="Orkiestrator agentów")
        header.set_title_widget(title)

        self.status = Gtk.Label(label="Gotowy")
        self.status.add_css_class("status-pill")
        self.status.add_css_class("status-idle")
        header.pack_start(self.status)

        self.stop_button = Gtk.Button(label="Zatrzymaj wszystkie")
        self.stop_button.add_css_class("destructive-action")
        self.stop_button.set_sensitive(False)
        self.stop_button.connect("clicked", self._stop_all)
        header.pack_end(self.stop_button)

        self.start_button = Gtk.Button(label="▶  Start wszystkie")
        self.start_button.add_css_class("suggested-action")
        self.start_button.add_css_class("start-button")
        self.start_button.connect("clicked", self._start_all)
        header.pack_end(self.start_button)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(self._build_content())
        self.set_content(toolbar)
        self.connect("close-request", self._close_requested)
        self._ready = True
        self.refresh_state()

    def _build_content(self) -> Gtk.Widget:
        split = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        window_settings = self.settings.get("window", {})
        saved_position = (
            window_settings.get("split", 650)
            if isinstance(window_settings, dict)
            else 650
        )
        split.set_position(saved_position if isinstance(saved_position, int) else 650)
        split.set_wide_handle(True)
        self.split = split

        config_scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        config_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        config = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        config.set_margin_top(24)
        config.set_margin_bottom(24)
        config.set_margin_start(24)
        config.set_margin_end(24)

        heading = Gtk.Label(xalign=0)
        heading.set_markup("<span size='x-large' weight='bold'>Konfiguracja biegu</span>")
        info = Gtk.Label(
            label=("Dla każdej roli wybierz jedną pozycję model–effort. "
                   "Zestaw wyborów to PROFIL: każdy bieg wskazuje własny, więc "
                   "jeden projekt może chodzić na samym GPT, a drugi mieszać "
                   "GPT, Claude'a i Groka. Profil wspólny zapisuje się w "
                   + str(self.routing_file) + " i obowiązuje uruchomienia "
                   "z linii poleceń bez dodatkowych zabiegów; nazwane profile "
                   "leżą w " + str(profiles_dir()) + " i wybiera się je przez "
                   "--routing-profile. Każdy bieg dostaje migawkę swojego "
                   "profilu w chwili startu, więc zmiany w trakcie pracy nie "
                   "dotykają biegu, który już ruszył."),
            xalign=0,
            wrap=True,
        )
        info.add_css_class("dim-label")
        config.append(heading)
        config.append(info)

        runs_heading = Gtk.Label(xalign=0)
        runs_heading.set_markup("<span size='large' weight='bold'>Biegi</span>")
        config.append(runs_heading)
        self.runs_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        config.append(self.runs_box)
        self.logs = Gtk.Notebook()
        self.logs.set_scrollable(True)
        self.add_run_button = Gtk.Button(label="+  Dodaj bieg")
        self.add_run_button.add_css_class("flat")
        self.add_run_button.connect("clicked", lambda _button: self.add_run())
        config.append(self.add_run_button)

        for entry in run_settings(self.settings):
            self.add_run(entry["brief"], entry["project"], notify=False,
                         profile=entry["profile"])

        roles_heading = Gtk.Label(xalign=0)
        roles_heading.set_markup(
            "<span size='large' weight='bold'>Modele ról</span>")
        config.append(roles_heading)
        config.append(self._build_profile_bar())

        defaults = Config(routing=routing_module.Routing())
        # Katalog czyta konfigurację OpenCode z dysku — jedno zbudowanie na okno,
        # a nie na każdy z ~25 slotów wszystkich ról.
        entries = catalog.index()
        self.role_cards: dict[str, RoleCard] = {}
        for definition in ROLE_DEFS:
            card = RoleCard(definition, defaults, self._role_changed, entries)
            self.role_cards[definition.name] = card
            config.append(card)
        self._show_profile(self.editing_slug)
        config_scroll.set_child(config)

        log_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        log_box.set_margin_top(24)
        log_box.set_margin_bottom(24)
        log_box.set_margin_start(20)
        log_box.set_margin_end(20)
        log_heading = Gtk.Label(xalign=0)
        log_heading.set_markup("<span size='large' weight='bold'>Status pracy</span>")
        log_box.append(log_heading)
        self.logs.set_vexpand(True)
        self.logs.set_hexpand(True)
        log_box.append(self.logs)

        split.set_start_child(config_scroll)
        split.set_end_child(log_box)
        split.set_resize_start_child(True)
        split.set_shrink_start_child(False)
        split.set_resize_end_child(True)
        split.set_shrink_end_child(False)
        return split

    # --- profile ----------------------------------------------------------
    def _build_profile_bar(self) -> Gtk.Widget:
        """Wybór i zarządzanie profilem, którego role widać poniżej."""
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        caption = Gtk.Label(label="Profil", xalign=0)
        caption.add_css_class("field-label")
        caption.set_size_request(120, -1)
        bar.append(caption)

        self._editor_choices: list[str] = []
        self._editor_labels: list[str] = []
        self._editor_muted = False
        self.profile_chooser = Gtk.DropDown(model=_string_list([]))
        self.profile_chooser.set_hexpand(True)
        _searchable(self.profile_chooser)
        self.profile_chooser.connect("notify::selected", self._editor_selected)
        bar.append(self.profile_chooser)

        self.profile_name = Gtk.Entry(
            placeholder_text="nazwa profilu",
            tooltip_text="Nazwa jest tylko etykietą — biegi wskazują profil "
                         "po nazwie pliku, więc zmiana nie osieroci żadnego "
                         "wiersza")
        self.profile_name.set_hexpand(True)
        self.profile_name.connect("activate", self._rename_profile)
        # Sam Enter nie wystarczy: nazwa wpisana i porzucona kliknięciem gdzie
        # indziej przepadłaby bez śladu, a to najczęstsza droga wyjścia z pola.
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda _controller: self._rename_profile(
            self.profile_name))
        self.profile_name.add_controller(focus)
        bar.append(self.profile_name)

        add = Gtk.Button(label="+  Nowy profil",
                         tooltip_text="Nowy profil jako kopia bieżącego")
        add.add_css_class("flat")
        add.connect("clicked", lambda _button: self._create_profile())
        bar.append(add)

        self.delete_profile_button = Gtk.Button(
            icon_name="user-trash-symbolic", tooltip_text="Usuń ten profil")
        self.delete_profile_button.add_css_class("flat")
        self.delete_profile_button.connect(
            "clicked", lambda _button: self._delete_profile())
        bar.append(self.delete_profile_button)
        return bar

    def _refresh_profile_chooser(self) -> None:
        """Odśwież pasek profilu; listę podmieniaj TYLKO gdy naprawdę się zmieniła.

        Podmiana modelu pokrętła zwalnia poprzedni ``Gtk.StringList`` razem
        z wewnętrznym modelem zaznaczenia. Zrobiona bez potrzeby — a najczęściej
        wołamy to zaraz po tym, jak operator sam przekręcił pokrętło — wyrywa
        obiekt spod trwającej emisji sygnału i kończy się notyfikacją na
        obiekcie już zwolnionym (``G_IS_OBJECT`` failed), a przy otwartym
        popupie naruszeniem ochrony pamięci."""
        labels = [profile.name for profile in self.profiles.profiles()]
        self._editor_choices = self.profiles.slugs()
        self._editor_muted = True
        if labels != self._editor_labels:
            self._editor_labels = labels
            self.profile_chooser.set_model(_string_list(labels))
        if self.editing_slug in self._editor_choices:
            self.profile_chooser.set_selected(
                self._editor_choices.index(self.editing_slug))
        self._editor_muted = False
        shared = self.editing_slug == profiles_module.SHARED_SLUG
        self.profile_name.set_text(self.profiles.label(self.editing_slug))
        self.profile_name.set_sensitive(not shared)
        self.delete_profile_button.set_sensitive(not shared)

    def _show_profile(self, slug: str) -> None:
        """Pokaż w kartach ról zawartość tego profilu.

        Karty są WSPÓLNE dla wszystkich profili, więc rola, której nowy profil
        nie nadpisuje, musi wrócić do polityki domyślnej — inaczej zostałaby na
        ekranie wartość z poprzednio oglądanego profilu i po pierwszej zmianie
        pokrętła zostałaby zapisana jako wybór operatora."""
        self.editing_slug = slug
        routing = self.profiles.routing(slug)
        self._applying_profile = True
        try:
            for role, card in self.role_cards.items():
                card.apply(routing.roles.get(role)
                           or routing_module.RoleRouting())
        finally:
            self._applying_profile = False
        self._refresh_profile_chooser()

    def edit_profile(self, slug: str) -> None:
        """Przełącz sekcję ról na ten profil (przycisk „Modele…" w wierszu)."""
        if self.profiles.has(slug) and slug != self.editing_slug:
            self._show_profile(slug)

    def _editor_selected(self, *_args: Any) -> None:
        """Operator przekręcił pokrętło profilu — przebuduj karty PO emisji.

        Przełączenie profilu podmienia zawartość jedenastu kart, a pokrętło
        modelu potrafi przy okazji przebudować własną listę (model spoza
        katalogu). Wykonane wewnątrz emisji ``notify::selected`` grzebałoby
        w widgetach pod dispatcherem GTK — łącznie z pokrętłem, które ten
        sygnał właśnie wysyła. Bezczynność pętli głównej jest najbliższym
        momentem, w którym emisja jest już zamknięta."""
        if self._editor_muted:
            return
        GLib.idle_add(self._apply_editor_selection)

    def _apply_editor_selection(self) -> bool:
        # Stan czytamy z pokrętła TERAZ, a nie z chwili sygnału: dwa szybkie
        # przekręcenia mają zostawić profil wybrany jako ostatni.
        index = self.profile_chooser.get_selected()
        if index != Gtk.INVALID_LIST_POSITION and 0 <= index < len(
                self._editor_choices):
            slug = self._editor_choices[index]
            if slug != self.editing_slug:
                self._show_profile(slug)
        return GLib.SOURCE_REMOVE

    def _create_profile(self) -> None:
        # Najpierw utrwal to, co widać na ekranie: kopiujemy profil BIEŻĄCY,
        # a jego zapis mógł się wcześniej nie udać (pełny dysk, prawa).
        self.save_settings()
        try:
            profile = self.profiles.create(source=self.editing_slug)
        except (OSError, ValueError) as exc:
            self._warn_runs(f"nie udało się utworzyć profilu: {exc}")
            return
        self._show_profile(profile.slug)
        self._refresh_run_profiles()
        self.profile_name.grab_focus()
        self.profile_name.select_region(0, -1)

    def _rename_profile(self, _entry: Gtk.Entry) -> None:
        current = self.profiles.label(self.editing_slug)
        text = self.profile_name.get_text().strip()
        # Puste pole to porzucona edycja, nie żądanie skasowania nazwy —
        # przywracamy poprzednią zamiast straszyć operatora błędem.
        if self.editing_slug == profiles_module.SHARED_SLUG or not text \
                or text == current:
            self.profile_name.set_text(current)
            return
        try:
            self.profiles.rename(self.editing_slug, text)
        except (OSError, ValueError) as exc:
            self._warn_runs(f"nie udało się zmienić nazwy profilu: {exc}")
        self._refresh_profile_chooser()
        self._refresh_run_profiles()

    def _delete_profile(self) -> None:
        """Usuń profil; biegi, które go wskazywały, wracają na wspólny.

        Bieg PRACUJĄCY blokuje usunięcie. Jego proces czyta już własną migawkę,
        więc technicznie nic by mu się nie stało — ale wiersz w panelu opisywałby
        wtedy inne modele niż te, którymi ten bieg realnie pracuje."""
        slug = self.editing_slug
        if slug == profiles_module.SHARED_SLUG:
            return
        busy = [run.title() for run in self.runs
                if run.is_running() and run.profile_slug == slug]
        if busy:
            self._warn_runs(
                f"profil „{self.profiles.label(slug)}” prowadzi bieg "
                f"{', '.join(busy)} — najpierw go zatrzymaj.")
            return
        moved = [run for run in self.runs if run.profile_slug == slug]
        try:
            self.profiles.delete(slug)
        except ValueError as exc:
            self._warn_runs(str(exc))
            return
        for run in moved:
            run.set_profile(profiles_module.SHARED_SLUG)
            run.append_log(
                "UWAGA: profil modeli tego biegu został usunięty — wracam "
                "na profil wspólny.")
        self._show_profile(profiles_module.SHARED_SLUG)
        self._refresh_run_profiles()
        self.save_paths()

    def _refresh_run_profiles(self) -> None:
        for run in self.runs:
            run.refresh_profiles()

    def _warn_runs(self, message: str) -> None:
        for run in self.runs:
            run.append_log(f"UWAGA: {message}")

    def run_routing(self, run: Run) -> routing_module.Routing:
        """Nadpisania ról, którymi ma ruszyć TEN bieg.

        Dla profilu właśnie edytowanego bierzemy stan KART, a nie pliku: między
        przekręceniem pokrętła a kliknięciem Start leży zapis, który mógł się nie
        udać (pełny dysk, prawa do katalogu), a bieg ma ruszyć tym, co operator
        widzi na ekranie."""
        if run.profile_slug == self.editing_slug:
            return self.current_routing()
        return self.profiles.routing(run.profile_slug)

    # --- biegi ------------------------------------------------------------
    def add_run(self, brief: str = DEFAULT_BRIEF, project: str = DEFAULT_PROJECT,
                notify: bool = True,
                profile: str = profiles_module.SHARED_SLUG) -> Run | None:
        if len(self.runs) >= MAX_RUNS:
            return None
        run = Run(self, brief, project, profile)
        self.runs.append(run)
        self.runs_box.append(run.panel)
        self.logs.append_page(run.page, run.tab_label)
        if notify:
            self.save_settings()
        self.refresh_state()
        return run

    def remove_run(self, run: Run) -> None:
        """Usuń wiersz biegu. Pracującego nie ruszamy — najpierw zatrzymanie."""
        if run.is_running() or len(self.runs) <= 1 or run not in self.runs:
            return
        self.runs.remove(run)
        self.runs_box.remove(run.panel)
        page = self.logs.page_num(run.page)
        if page >= 0:
            self.logs.remove_page(page)
        self.save_settings()
        self.refresh_state()

    def running_runs(self) -> list[Run]:
        return [run for run in self.runs if run.is_running()]

    def blocking_problem(self, run: Run) -> str:
        """Powód, dla którego TEN bieg nie powinien wystartować; ``""`` = wolna droga.

        Wszystkie trzy sprawdzenia dotyczą zasobów współdzielonych między
        biegami — katalogu projektu i sesji Claude Code. Odmawiamy TUTAJ, bo
        każde z nich zauważone później kosztuje albo zniszczony stan projektu,
        albo godziny pracy modelu."""
        project = run.project_path()
        # Porównanie po ``resolve``: ten sam katalog zapisany przez dowiązanie
        # albo z ``..`` w ścieżce jest tym samym drzewem git i tym samym
        # STATE.json, choć teksty w polach się różnią.
        resolved = project.resolve()
        for other in self.runs:
            if other is not run and other.is_running() \
                    and other.project_path().resolve() == resolved:
                return ("Ten katalog projektu prowadzi już inny bieg w tym "
                        "oknie. Dwa procesy na jednym drzewie nadpisują sobie "
                        "STATE.json i commity.")
        busy = runlock.busy_reason(str(project))
        if busy:
            return busy
        try:
            # Routing TEGO biegu, nie okna: od czasu profili odpowiedź na
            # „czy potrzebuję Claude'a" bywa różna dla dwóch wierszy panelu,
            # a zamek na plikową sesję dotyczy tylko tych, które go wołają.
            config = Config(routing=self.run_routing(run))
        except ValueError as exc:
            return str(exc)
        # Pytamy ZAWSZE, nie tylko przy drugim biegu w tym oknie: plikowa sesja
        # Claude Code jest zasobem całej maszyny, więc trzymać ją może równie
        # dobrze drugie okno GUI albo bieg uruchomiony z powłoki.
        return preflight.claude_file_session_busy(config)

    def run_finished(self, _run: Run) -> None:
        self.refresh_state()
        if self._closing and not self.running_runs():
            self.destroy()

    def refresh_state(self) -> None:
        """Nagłówek mówi o CAŁYM oknie; szczegóły są przy każdym biegu."""
        if not hasattr(self, "runs_box"):
            return
        active = len(self.running_runs())
        self.start_button.set_sensitive(active < len(self.runs))
        self.stop_button.set_sensitive(active > 0)
        self.add_run_button.set_sensitive(len(self.runs) < MAX_RUNS)
        for run in self.runs:
            run.remove_button.set_sensitive(
                not run.is_running() and len(self.runs) > 1)
        self.status.set_label(
            "Gotowy" if not active else
            "Pracuje" if active == 1 else f"Pracują {active} biegi")
        self.status.remove_css_class("status-idle")
        self.status.remove_css_class("status-running")
        self.status.add_css_class("status-running" if active else "status-idle")

    def _start_all(self, _button: Gtk.Button) -> None:
        for run in self.runs:
            if not run.is_running():
                run.start()

    def _stop_all(self, _button: Gtk.Button | None = None) -> None:
        for run in self.running_runs():
            run.stop()

    # --- wspólne dla okna -------------------------------------------------
    def open_chooser(
        self, title: str, action: Gtk.FileChooserAction, target: Gtk.Entry
    ) -> None:
        chooser = Gtk.FileChooserNative(
            title=title,
            transient_for=self,
            action=action,
            accept_label="Wybierz",
            cancel_label="Anuluj",
        )
        current = resolve_path(target.get_text())
        try:
            if action == Gtk.FileChooserAction.SELECT_FOLDER:
                initial = current if current.is_dir() else current.parent
                chooser.set_current_folder(Gio.File.new_for_path(str(initial)))
            elif current.exists():
                chooser.set_file(Gio.File.new_for_path(str(current)))
            else:
                chooser.set_current_folder(Gio.File.new_for_path(str(current.parent)))
        except GLib.Error:
            pass

        def selected(dialog: Gtk.FileChooserNative, response: int) -> None:
            if response == Gtk.ResponseType.ACCEPT:
                chosen = dialog.get_file()
                if chosen is not None and chosen.get_path():
                    target.set_text(chosen.get_path())
                    self.save_settings()
            self._chooser = None
            dialog.destroy()

        chooser.connect("response", selected)
        self._chooser = chooser
        chooser.show()

    def current_routing(self) -> routing_module.Routing:
        return routing_module.Routing(roles={
            role: card.routing_entry()
            for role, card in self.role_cards.items()
        })

    def _settings_payload(self) -> dict[str, Any]:
        return {
            "runs": [run.settings() for run in self.runs],
            "window": {
                "width": self.get_width(),
                "height": self.get_height(),
                "split": self.split.get_position(),
            },
        }

    def save_paths(self) -> None:
        """Sam plik ustawień GUI, bez przepisywania routingu ról."""
        if not self._ready:
            return
        try:
            save_settings(self._settings_payload())
        except OSError as exc:
            for run in self.runs:
                run.append_log(f"UWAGA: nie udało się zapisać ustawień GUI: {exc}")

    def _role_changed(self) -> None:
        if self._applying_profile:
            return
        self.save_settings()

    def save_settings(self) -> None:
        """Zapisz ścieżki, geometrię i routing EDYTOWANEGO profilu.

        Wołane przy KAŻDEJ zmianie pokrętła, nie tylko przy starcie — inaczej
        „to ma się zapamiętać" trzymałoby się wyłącznie udanych uruchomień."""
        if not self._ready:
            return
        self.save_paths()
        try:
            self.profiles.set_routing(self.editing_slug, self.current_routing())
        except (OSError, ValueError) as exc:
            self._warn_runs(f"nie udało się zapisać profilu modeli: {exc}")

    def _close_requested(self, _window: Gtk.Window) -> bool:
        self.save_settings()
        if not self.running_runs():
            return False
        self._closing = True
        self._stop_all()
        return True


CSS = b"""
window { background: #10151d; color: #e7edf5; }
headerbar { background: #151c26; border-bottom: 1px solid rgba(255,255,255,.08); }
.role-card {
  background: #18212d;
  border: 1px solid rgba(255,255,255,.08);
  border-radius: 14px;
  padding: 12px 16px;
}
.run-card {
  background: #1a2432;
  border: 1px solid rgba(255,255,255,.10);
  border-radius: 14px;
  padding: 12px 16px;
}
.role-title { font-size: 16px; }
.field-label { color: #aebdce; font-size: 12px; font-weight: 600; }
.dim-label { color: #8fa0b3; }
.log-surface {
  background: #0b1017;
  border: 1px solid rgba(255,255,255,.08);
  border-radius: 14px;
}
textview, textview text { background: #0b1017; color: #c8d3e0; }
.status-pill {
  border-radius: 999px;
  padding: 5px 12px;
  font-weight: 700;
}
.status-idle { background: #263242; color: #c5d0de; }
.status-running { background: #123f32; color: #6fe7a7; }
.status-error { background: #4a2025; color: #ff9ca0; }
.start-button { padding-left: 18px; padding-right: 18px; }
dropdown, entry {
  min-height: 34px;
  border-radius: 9px;
}
"""


class ForgeApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="pl.agentloop.Forge")

    def do_activate(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        window = self.props.active_window or ForgeWindow(self)
        window.present()


def main() -> int:
    try:
        return ForgeApplication().run(sys.argv)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
