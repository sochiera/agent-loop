"""Dziennik procesu — pamięć mistrza.

Jedna krótka linia na zdarzenie (decyzja rundy, plan, porażka, commit).
Plik jest przycinany do ostatnich ``KEEP_LINES`` wpisów, więc pamięć jest
ograniczona ROZMIAREM, a nie cyklem życia: przeżywa restart forge i obejmuje
kilka zadań wstecz razem ze zdarzeniami planisty.

To telemetria — żadna operacja tutaj nie ma prawa wywrócić pętli.
"""
from __future__ import annotations

import datetime as _dt
import os

KEEP_LINES = 80
MAX_ENTRY = 300
MASTER_LINES = 20
MASTER_WIDTH = 120
MASTER_FILES = 160
_FILE = "ledger.md"


def _path(project: str, runtime_dir: str = ".forge") -> str:
    return os.path.join(project, runtime_dir, _FILE)


def _sanitise(line: str) -> str:
    """Jedna linia, zapisywalna w UTF-8, o ograniczonej długości.

    Treść pochodzi z odpowiedzi agenta: poprawny JSON potrafi nieść samotny
    surogat (``\\ud800``), którego zapis UTF-8 by nie przeżył, a wielolinijkowy
    powód rozjechałby format "jeden wpis = jedna linia".
    """
    text = " ".join(str(line).split())
    text = text.encode("utf-8", "replace").decode("utf-8", "replace")
    return text[:MAX_ENTRY]


def append(project: str, line: str, runtime_dir: str = ".forge") -> None:
    """Dopisz zdarzenie i utnij dziennik do ostatnich KEEP_LINES wpisów."""
    entry = f"[{_dt.datetime.now().strftime('%H:%M')}] {_sanitise(line)}"
    path = _path(project, runtime_dir)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lines = tail(project, KEEP_LINES, runtime_dir).splitlines()
        lines.append(entry)
        with open(path, "w", encoding="utf-8", errors="replace") as handle:
            handle.write("\n".join(lines[-KEEP_LINES:]) + "\n")
    except Exception:  # noqa: BLE001 — telemetria nigdy nie blokuje agentów
        pass


def tail(project: str, limit: int = KEEP_LINES, runtime_dir: str = ".forge") -> str:
    """Ostatnie ``limit`` wpisów jako tekst; brak dziennika = pusty string."""
    try:
        with open(_path(project, runtime_dir), "r", encoding="utf-8",
                  errors="replace") as handle:
            lines = handle.read().splitlines()
    except Exception:  # noqa: BLE001 — uszkodzony dziennik nie może nic zatrzymać
        return ""
    return "\n".join(lines[-limit:])


def tail_for_task(project: str, task_id: str, limit: int = 8,
                  runtime_dir: str = ".forge") -> str:
    """Ostatnie wpisy dotyczące dokładnie jednego zadania."""
    prefix = f"{task_id} "
    lines = tail(project, KEEP_LINES, runtime_dir).splitlines()
    matching = [
        line for line in lines
        if line.partition("] ")[2].startswith(prefix)
    ]
    return "\n".join(matching[-limit:])


def _compact_line(line: str, width: int = MASTER_WIDTH) -> str:
    """Przytnij POWÓD, nie listę plików.

    Mistrz wykrywa wzorce po ``pliki=…`` (np. zmianę testu przez kodera),
    a ta lista stoi w linii przed powodem — cięcie na sztywnej szerokości
    gubiło właśnie ją, gdy tura ruszyła kilka plików naraz.
    """
    head, marker, rest = line.partition("pliki=")
    if not marker:
        return line[:width]
    files, colon, reason = rest.partition(": ")
    kept = f"{head}{marker}{files[:MASTER_FILES]}{colon}"
    return kept + reason[:max(0, width - len(kept))]


def compact_tail(project: str, runtime_dir: str = ".forge") -> str:
    """Mały widok dziennika dla często wywoływanej roli mistrza."""
    lines = tail(project, MASTER_LINES, runtime_dir).splitlines()
    return "\n".join(_compact_line(line) for line in lines)


def round_limit_tasks(project: str, runtime_dir: str = ".forge") -> list[str]:
    """Zadania porzucone przez ``round_limit`` w CAŁEJ pamięci dziennika.

    Jedno zadanie idące na limit rund zajmuje więcej linii niż całe okno
    mistrza, więc wzorzec „planista tnie za grubo" jest z ``compact_tail``
    strukturalnie niewidoczny — trzeba mu go policzyć osobno.
    """
    found: list[str] = []
    for line in tail(project, KEEP_LINES, runtime_dir).splitlines():
        task_id, _, rest = line.partition("] ")[2].partition(" ")
        if rest.startswith("PORZUCONE: round_limit") and task_id not in found:
            found.append(task_id)
    return found
