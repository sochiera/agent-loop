"""Natywne GUI GTK 4 dla orkiestratora forge.

Uruchomienie:
    python3 -m forge.gui
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
from pathlib import Path
from typing import Any

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .config import Config


ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = Path(
    os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
) / "forge" / "gui.json"
MAX_LOG_LINES = 5_000
STOP_TERM_DELAY_S = 8
STOP_KILL_DELAY_S = 5
AGENTS = ("claude", "codex", "opencode", "grok", "kiro")
ROLE_DEFS = (
    ("planner", "Planista", "Tworzy plan i dzieli pracę na zadania"),
    ("tester", "Tester", "Pisze testy i pilnuje czerwonej bramki"),
    ("coder", "Koder", "Implementuje rozwiązanie i zazielenia testy"),
    ("reviewer", "Recenzent", "Sprawdza ukończone zadanie w świeżym kontekście"),
    ("verifier", "Weryfikator", "Ocenia, czy cały cel został osiągnięty"),
)
ENV_FIELDS = {
    role: {"agent": f"FORGE_{role.upper()}_AGENT"}
    for role, _title, _description in ROLE_DEFS
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


def build_launch(
    brief: str, project: str, roles: dict[str, dict[str, str]]
) -> tuple[list[str], dict[str, str]]:
    """Zbuduj bezpieczne argv i środowisko procesu orkiestratora."""
    if not brief.strip():
        raise ValueError("Wskaż plik z briefem.")
    if not project.strip():
        raise ValueError("Wskaż katalog projektu.")

    env = os.environ.copy()
    for role, _title, _description in ROLE_DEFS:
        values = roles[role]
        value = values["agent"].strip()
        if "\0" in value or "\n" in value or len(value) > 300:
            raise ValueError(f"Niepoprawna wartość pola {role}.agent.")
        env[ENV_FIELDS[role]["agent"]] = value
        # Stare ustawienia powłoki/GUI nie mogą po cichu obejść nowej polityki.
        env.pop(f"FORGE_{role.upper()}_MODEL", None)
        env.pop(f"FORGE_{role.upper()}_EFFORT", None)

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
    if any(word in upper for word in ("UWAGA", "LIMIT", "WZNAWIAM", "SMELL", "ROLLBACK")):
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


def _string_list(values: tuple[str, ...], empty_label: str = "Domyślny") -> Gtk.StringList:
    return Gtk.StringList.new([empty_label if value == "" else value for value in values])


class RoleCard(Gtk.Box):
    def __init__(self, role: str, title: str, description: str, default_agent: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.role = role
        self.add_css_class("role-card")

        heading = Gtk.Label(xalign=0)
        heading.set_markup(f"<b>{GLib.markup_escape_text(title)}</b>")
        heading.add_css_class("role-title")
        subtitle = Gtk.Label(label=description, xalign=0, wrap=True)
        subtitle.add_css_class("dim-label")
        self.append(heading)
        self.append(subtitle)

        self.agent = Gtk.DropDown(model=_string_list(AGENTS, "Agent"))
        self.agent.set_hexpand(True)
        selected = default_agent if default_agent in AGENTS else AGENTS[0]
        self.agent.set_selected(AGENTS.index(selected))

        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        caption = Gtk.Label(label="Agent", xalign=0)
        caption.add_css_class("field-label")
        row.append(caption)
        row.append(self.agent)
        self.append(row)

    def values(self) -> dict[str, str]:
        return {"agent": AGENTS[self.agent.get_selected()]}

    def set_sensitive_fields(self, enabled: bool) -> None:
        self.agent.set_sensitive(enabled)


class ForgeWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title="Forge — panel sterowania")
        self.settings = load_settings()
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
        config = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        config.set_margin_top(24)
        config.set_margin_bottom(24)
        config.set_margin_start(24)
        config.set_margin_end(24)

        heading = Gtk.Label(xalign=0)
        heading.set_markup("<span size='x-large' weight='bold'>Konfiguracja biegu</span>")
        info = Gtk.Label(
            label=("Wybierz agenta dla każdej roli. Model i poziom namysłu "
                   "dobierze stała mapa na podstawie trudności zadania."),
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

        defaults = Config()
        roles_box = Gtk.FlowBox()
        roles_box.set_selection_mode(Gtk.SelectionMode.NONE)
        roles_box.set_column_spacing(14)
        roles_box.set_row_spacing(14)
        roles_box.set_min_children_per_line(1)
        roles_box.set_max_children_per_line(2)
        self.role_cards: dict[str, RoleCard] = {}
        saved_roles = self.settings.get("roles", {})
        if not isinstance(saved_roles, dict):
            saved_roles = {}
        for role, title, description in ROLE_DEFS:
            default_agent = defaults.role(role)[0]
            saved_role = saved_roles.get(role, {})
            if isinstance(saved_role, dict):
                saved_agent = saved_role.get("agent")
                if isinstance(saved_agent, str):
                    default_agent = saved_agent
            card = RoleCard(role, title, description, default_agent)
            card.set_size_request(270, -1)
            self.role_cards[role] = card
            roles_box.append(card)
        config.append(roles_box)
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

    def _start(self, _button: Gtk.Button) -> None:
        roles = {role: card.values() for role, card in self.role_cards.items()}
        try:
            command, env = build_launch(self.brief.get_text(), self.project.get_text(), roles)
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
            "roles": {
                role: card.values()
                for role, card in self.role_cards.items()
            },
            "window": {
                "width": self.get_width(),
                "height": self.get_height(),
                "split": self.split.get_position(),
            },
        }

    def _save_settings(self) -> None:
        try:
            save_settings(self._settings_payload())
        except OSError as exc:
            self._append_log(f"UWAGA: nie udało się zapisać ustawień GUI: {exc}")

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
  padding: 16px;
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
  min-height: 38px;
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
