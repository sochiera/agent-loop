"""Natywne GUI GTK 4 dla orkiestratora forge.

Uruchomienie:
    python3 -m forge.gui

Panel konfiguracji jest edytorem pliku ``routing.json`` (patrz routing.py): dla
każdej roli wybierasz MODEL RAZEM Z EFFORTEM (a dla ról czułych na zakres
zadania — osobno dla simple/standard/complex) i łańcuch zapasowy. Model, effort
i trasa są jedną pozycją; GPT i Grok zawsze biegną przez OpenCode. Wybór zapisuje się
poza repozytorium, więc obowiązuje też
uruchomienia z CLI i nie wymaga commita przy każdej zmianie dostawcy.
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .config import Config, TASK_DIFFICULTIES, validate_master_agent
from . import catalog
from . import report
from . import routing as routing_module
from .adapters import canonical_agent


ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = Path(
    os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
) / "forge" / "gui.json"
MAX_LOG_LINES = 5_000
STOP_TERM_DELAY_S = 8
STOP_KILL_DELAY_S = 5
SESSION_LOG_NAME = "gui_run.log"
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


def resolve_project(project: str) -> Path:
    """Ta sama reguła co w orkiestratorze: proces ma cwd=ROOT, więc ścieżka
    względna projektu jest względna do ROOT, nie do bieżącego katalogu GUI."""
    path = Path(project).expanduser()
    return path if path.is_absolute() else ROOT / path


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


def build_launch(
    brief: str, project: str, routing: routing_module.Routing,
    routing_file: Path | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Zbuduj bezpieczne argv i środowisko procesu orkiestratora.

    Konfiguracja ról jedzie JEDNYM kanałem — plikiem routingu. Stare zmienne
    ``FORGE_<ROLA>_AGENT/MODEL/EFFORT`` z powłoki są tu czyszczone, żeby wybór
    z GUI nie przegrywał po cichu z zapomnianym exportem."""
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

    command = [
        sys.executable,
        "-u",
        "-m",
        "forge.orchestrate",
        "--non-interactive",
        "--brief",
        brief.strip(),
        "--project",
        project.strip(),
    ]
    return command, env


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


class ForgeWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title="Forge — panel sterowania")
        self.settings = load_settings()
        self.routing_file = routing_path()
        self.routing = routing_module.load(self.routing_file, TASK_DIFFICULTIES)
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
        self.process: subprocess.Popen[str] | None = None
        self.started_at = 0.0
        self._closing = False
        self.stop_requested = False
        self._chooser: Gtk.FileChooserNative | None = None
        self._session_log_fh: Any = None
        self._ready = False

        header = Adw.HeaderBar()
        title = Adw.WindowTitle(title="Forge", subtitle="Orkiestrator agentów")
        header.set_title_widget(title)

        self.status = Gtk.Label(label="Gotowy")
        self.status.add_css_class("status-pill")
        self.status.add_css_class("status-idle")
        header.pack_start(self.status)

        self.stop_button = Gtk.Button(label="Zatrzymaj")
        self.stop_button.add_css_class("destructive-action")
        self.stop_button.set_sensitive(False)
        self.stop_button.connect("clicked", self._stop)
        header.pack_end(self.stop_button)

        self.start_button = Gtk.Button(label="▶  Start")
        self.start_button.add_css_class("suggested-action")
        self.start_button.add_css_class("start-button")
        self.start_button.connect("clicked", self._start)
        header.pack_end(self.start_button)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(self._build_content())
        self.set_content(toolbar)
        self.connect("close-request", self._close_requested)
        self._ready = True
        self._load_previous_run()

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
                   "Ostatni wybór jest przywracany przy kolejnym uruchomieniu. "
                   "Routing zapisuje się w "
                   + str(self.routing_file) + " i obowiązuje także uruchomienia "
                   "z linii poleceń."),
            xalign=0,
            wrap=True,
        )
        info.add_css_class("dim-label")
        config.append(heading)
        config.append(info)

        paths = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        saved_brief = self.settings.get("brief", "game.md")
        saved_project = self.settings.get("project", "game")
        self.brief = Gtk.Entry(
            text=saved_brief if isinstance(saved_brief, str) else "game.md",
            placeholder_text="game.md",
        )
        self.project = Gtk.Entry(
            text=saved_project if isinstance(saved_project, str) else "game",
            placeholder_text="game",
        )
        self.path_buttons: list[Gtk.Button] = []
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
            controls.append(entry)
            choose = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text=f"Wybierz: {label}")
            choose.connect("clicked", callback)
            self.path_buttons.append(choose)
            controls.append(choose)
            box.append(controls)
            box.set_hexpand(True)
            paths.append(box)
        config.append(paths)

        roles_heading = Gtk.Label(xalign=0)
        roles_heading.set_markup("<span size='large' weight='bold'>Role</span>")
        config.append(roles_heading)

        defaults = Config(routing=routing_module.Routing())
        # Katalog czyta konfigurację OpenCode z dysku — jedno zbudowanie na okno,
        # a nie na każdy z ~25 slotów wszystkich ról.
        entries = catalog.index()
        self.role_cards: dict[str, RoleCard] = {}
        for definition in ROLE_DEFS:
            card = RoleCard(definition, defaults, self._save_settings, entries)
            saved = self.routing.roles.get(definition.name)
            if saved is not None:
                card.apply(saved)
            self.role_cards[definition.name] = card
            config.append(card)
        config_scroll.set_child(config)

        log_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        log_box.set_margin_top(24)
        log_box.set_margin_bottom(24)
        log_box.set_margin_start(20)
        log_box.set_margin_end(20)
        log_heading = Gtk.Label(xalign=0)
        log_heading.set_markup("<span size='large' weight='bold'>Status pracy</span>")
        self.elapsed = Gtk.Label(label="Jeszcze nie uruchomiono", xalign=0)
        self.elapsed.add_css_class("dim-label")
        log_box.append(log_heading)
        log_box.append(self.elapsed)

        log_scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        log_scroll.add_css_class("log-surface")
        self.log_buffer = Gtk.TextBuffer()
        for name, color, weight in (
            ("normal", "#c8d3e0", 400),
            ("success", "#6fe7a7", 600),
            ("error", "#ff8e91", 600),
            ("warning", "#ffd37a", 500),
            ("phase", "#82b7ff", 700),
        ):
            self.log_buffer.create_tag(name, foreground=color, weight=weight)
        self.log_view = Gtk.TextView(buffer=self.log_buffer, editable=False, cursor_visible=False)
        self.log_view.set_monospace(True)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.log_view.set_top_margin(14)
        self.log_view.set_bottom_margin(14)
        self.log_view.set_left_margin(14)
        self.log_view.set_right_margin(14)
        log_scroll.set_child(self.log_view)
        log_box.append(log_scroll)

        split.set_start_child(config_scroll)
        split.set_end_child(log_box)
        split.set_resize_start_child(True)
        split.set_shrink_start_child(False)
        split.set_resize_end_child(True)
        split.set_shrink_end_child(False)
        return split

    def _choose_brief(self, _button: Gtk.Button) -> None:
        self._open_chooser(
            "Wybierz plik z briefem",
            Gtk.FileChooserAction.OPEN,
            self.brief,
        )

    def _choose_project(self, _button: Gtk.Button) -> None:
        self._open_chooser(
            "Wybierz katalog projektu",
            Gtk.FileChooserAction.SELECT_FOLDER,
            self.project,
        )

    def _open_chooser(
        self, title: str, action: Gtk.FileChooserAction, target: Gtk.Entry
    ) -> None:
        chooser = Gtk.FileChooserNative(
            title=title,
            transient_for=self,
            action=action,
            accept_label="Wybierz",
            cancel_label="Anuluj",
        )
        current = Path(target.get_text()).expanduser()
        if not current.is_absolute():
            current = ROOT / current
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
                    self._save_settings()
            self._chooser = None
            dialog.destroy()

        chooser.connect("response", selected)
        self._chooser = chooser
        chooser.show()

    def _append_log(self, line: str) -> None:
        end = self.log_buffer.get_end_iter()
        self.log_buffer.insert_with_tags_by_name(end, line.rstrip() + "\n", line_kind(line))
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
        project = resolve_project(self.project.get_text())
        log_path = project / ".forge" / SESSION_LOG_NAME
        if not log_path.is_file():
            return
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        if not content.strip():
            return
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(log_path.stat().st_mtime))
        self._append_log(f"===== Log poprzedniego uruchomienia ({stamp}) — {project} =====")
        for line in content.splitlines():
            self._append_log(line)
        try:
            stats = report.usage_summary(str(project))
        except OSError:
            stats = ""
        if stats:
            self._append_log("")
            self._append_log("===== Zużycie tokenów (łącznie w tym projekcie) =====")
            for line in stats.splitlines():
                self._append_log(line)
        self._append_log("")
        self._append_log("===== Gotowy do nowego uruchomienia =====")

    def _open_session_log(self) -> None:
        self._close_session_log()
        project = resolve_project(self.project.get_text())
        try:
            runtime = project / ".forge"
            runtime.mkdir(parents=True, exist_ok=True)
            self._session_log_fh = open(runtime / SESSION_LOG_NAME, "w", encoding="utf-8")
        except OSError:
            self._session_log_fh = None

    def _close_session_log(self) -> None:
        if self._session_log_fh is not None:
            try:
                self._session_log_fh.close()
            except OSError:
                pass
            self._session_log_fh = None

    def _set_running(self, running: bool, label: str | None = None) -> None:
        self.start_button.set_sensitive(not running)
        self.stop_button.set_sensitive(running)
        self.brief.set_sensitive(not running)
        self.project.set_sensitive(not running)
        for button in self.path_buttons:
            button.set_sensitive(not running)
        for card in self.role_cards.values():
            card.set_sensitive_fields(not running)
        self.status.set_label(label or ("Pracuje" if running else "Gotowy"))
        self.status.remove_css_class("status-idle")
        self.status.remove_css_class("status-running")
        self.status.remove_css_class("status-error")
        self.status.add_css_class("status-running" if running else "status-idle")

    def _show_error(self, message: str) -> None:
        self._append_log(f"BŁĄD: {message}")
        self.status.set_label("Błąd")
        self.status.remove_css_class("status-idle")
        self.status.remove_css_class("status-running")
        self.status.add_css_class("status-error")

    def current_routing(self) -> routing_module.Routing:
        return routing_module.Routing(roles={
            role: card.routing_entry()
            for role, card in self.role_cards.items()
        })

    def _start(self, _button: Gtk.Button) -> None:
        try:
            routing = self.current_routing()
            command, env = build_launch(
                self.brief.get_text(), self.project.get_text(), routing,
                self.routing_file)
            self._save_settings()
            self.process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            self._show_error(str(exc))
            return

        self.log_buffer.set_text("")
        self._open_session_log()
        self.started_at = time.monotonic()
        self.stop_requested = False
        self._set_running(True)
        self._append_log("Uruchamiam forge…")
        threading.Thread(target=self._read_process, daemon=True).start()
        GLib.timeout_add_seconds(1, self._update_elapsed)

    def _read_process(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            GLib.idle_add(self._append_log, line)
        code = process.wait()
        GLib.idle_add(self._process_finished, code)

    def _process_finished(self, code: int) -> bool:
        elapsed = max(0, time.monotonic() - self.started_at)
        self.process = None
        if self.stop_requested:
            self._set_running(False, "Zatrzymano")
            self._append_log(f"Proces zatrzymany (actual elapsed: {self._format_elapsed(elapsed)}).")
        elif code == 0:
            self._set_running(False, "Ukończono")
            self._append_log(f"Proces zakończony poprawnie (actual elapsed: {self._format_elapsed(elapsed)}).")
        elif code == 130:
            self._set_running(False, "Zatrzymano")
            self._append_log(f"Proces zatrzymany (actual elapsed: {self._format_elapsed(elapsed)}).")
        else:
            self._set_running(False)
            self._show_error(
                f"Proces zakończył się kodem {code} (actual elapsed: {self._format_elapsed(elapsed)})."
            )
        self._close_session_log()
        if self._closing:
            self.destroy()
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        total = int(seconds)
        hours, rest = divmod(total, 3600)
        minutes, secs = divmod(rest, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def _update_elapsed(self) -> bool:
        if self.process is None:
            return GLib.SOURCE_REMOVE
        self.elapsed.set_label(f"Czas biegu  {self._format_elapsed(time.monotonic() - self.started_at)}")
        return GLib.SOURCE_CONTINUE

    def _stop(self, _button: Gtk.Button | None = None) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        self.stop_button.set_sensitive(False)
        self.status.set_label("Zatrzymywanie…")
        self.stop_requested = True
        self._append_log("Wysyłam bezpieczne przerwanie — stan zostanie zapisany…")
        try:
            os.killpg(self.process.pid, signal.SIGINT)
        except OSError:
            pass
        process = self.process
        GLib.timeout_add_seconds(STOP_TERM_DELAY_S, self._escalate_stop, process, signal.SIGTERM)

    def _escalate_stop(
        self, process: subprocess.Popen[str], next_signal: signal.Signals
    ) -> bool:
        if self.process is not process or process.poll() is not None:
            return GLib.SOURCE_REMOVE
        if next_signal == signal.SIGTERM:
            self._append_log("Proces nie odpowiedział — wysyłam SIGTERM…")
            following = signal.SIGKILL
            delay = STOP_KILL_DELAY_S
        else:
            self._append_log("Proces nadal nie odpowiada — wymuszam zakończenie…")
            following = None
            delay = 0
        try:
            os.killpg(process.pid, next_signal)
        except OSError:
            return GLib.SOURCE_REMOVE
        if following is not None:
            GLib.timeout_add_seconds(delay, self._escalate_stop, process, following)
        return GLib.SOURCE_REMOVE

    def _settings_payload(self) -> dict[str, Any]:
        return {
            "brief": self.brief.get_text(),
            "project": self.project.get_text(),
            "window": {
                "width": self.get_width(),
                "height": self.get_height(),
                "split": self.split.get_position(),
            },
        }

    def _save_settings(self) -> None:
        """Zapisz ścieżki, geometrię i routing ról.

        Wołane przy KAŻDEJ zmianie pokrętła, nie tylko przy starcie — inaczej
        „to ma się zapamiętać" trzymałoby się wyłącznie udanych uruchomień."""
        if not self._ready:
            return
        try:
            save_settings(self._settings_payload())
        except OSError as exc:
            self._append_log(f"UWAGA: nie udało się zapisać ustawień GUI: {exc}")
        try:
            routing_module.save(self.current_routing(), self.routing_file)
        except OSError as exc:
            self._append_log(f"UWAGA: nie udało się zapisać routingu ról: {exc}")

    def _close_requested(self, _window: Gtk.Window) -> bool:
        self._save_settings()
        if self.process is None:
            return False
        self._closing = True
        self._stop()
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
