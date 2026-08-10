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
import re

# Kanoniczna gramatyka identyfikatora zadania. Mieszka w dzienniku, bo to
# jedyny moduł-liść czytany przez wszystkich zainteresowanych: orkiestrator
# (numeracja wsadów), bramka mistrza (parser ogona) i ten plik (mianowniki
# raportu). Dwie kopie tego wzorca prędzej czy później by się rozjechały, a
# rozjazd tutaj oznacza cicho pominięte wpisy, nie błąd.
TASK_ID_BODY = r"task-\d+"

# Ile wpisów przeżywa przycięcie pliku. To NIE jest parametr kosztu promptu:
# do mistrza idzie wyłącznie `MASTER_LINES` linii przez `compact_tail`, więc
# ta liczba nie dokłada ani jednego tokena do żadnego wywołania. Płacimy za nią
# wyłącznie miejscem na dysku (≤300 znaków na wpis, czyli ≤300 kB) i
# przepisaniem pliku przy każdym `append` — obie pozycje pomijalne wobec minut,
# które trwa jedna tura agenta.
#
# Za to mianowniki raportu (`$/zadanie`, `rundy/zadanie`, odsiew planisty,
# pushback kodera) czytają CAŁY dziennik. Przy 80 wpisach pokrywały ułamek
# jednego wsadu ośmiu zadań, więc `$/zadanie` liczyło koszt całej historii
# `usage.jsonl` przez garstkę zadań i wychodziły z tego liczby bez sensu.
# 1000 wpisów to z zapasem kilka pełnych przebiegów.
KEEP_LINES = 1000
MAX_ENTRY = 300
# Okno mistrza. TO jest parametr kosztu: idzie do promptu przy każdym wywołaniu
# roli wołanej co rundę. Rośnie tylko z bardzo dobrego powodu. Zostaje wąskie
# także po dołożeniu okien niżej: zadaniem mistrza jest rozpoznanie wzorca w
# świeżym wycinku, a nie rekonstrukcja historii — szersze okno zmieniłoby jego
# rolę, nie tylko rachunek.
MASTER_LINES = 20
# Okna pozostałych ról. Mierzone na realnym dzienniku (609 wpisów, 100 kB,
# średnio 165 znaków na wpis): 200 linii to ~31 kB (~10k tokenów), 400 linii to
# ~62 kB (~20k tokenów). Wobec tury, w której sam wynik narzędzi potrafi mieć
# 0.3 MB, to kilka procent wejścia — a niesie jedyny zapis tego, co robiły inne
# role: kto ruszył BACKLOG.md, które zadania padły na limit rund, co orzekła
# weryfikacja. Role fazowe wołamy rzadko i decydują o kierunku, więc dostają
# szersze okno niż role zadaniowe wołane co rundę.
PHASE_LINES = 400
TASK_LINES = 200
# Horyzont wzorca „planista tnie za grubo". Trzymany osobno od KEEP_LINES
# świadomie: ten warunek ma opisywać porażki ŚWIEŻE. Gdyby jechał na całej
# pamięci dziennika, po jej powiększeniu mistrz wypominałby planiście zadania
# sprzed kilku przebiegów i reguła zmieniłaby się w stały fałszywy alarm.
ROUND_LIMIT_LINES = 80
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


_COMPLETED = re.compile(rf"({TASK_ID_BODY}) UKOŃCZONE po (\d+) rundach")
_ABANDONED = re.compile(rf"({TASK_ID_BODY}) PORZUCONE:")


def abandoned_tasks(project: str, runtime_dir: str = ".forge") -> list[str]:
    """Zadania porzucone z DOWOLNEGO powodu — licznik kosztu, nie mianownik."""
    found: list[str] = []
    for line in tail(project, KEEP_LINES, runtime_dir).splitlines():
        match = _ABANDONED.match(line.partition("] ")[2])
        if match and match.group(1) not in found:
            found.append(match.group(1))
    return found


def completed_tasks(project: str,
                    runtime_dir: str = ".forge") -> list[tuple[str, int]]:
    """``[(identyfikator, liczba rund)]`` dla zadań domkniętych i zacommitowanych.

    Mianownik metryk ``$/zadanie`` i ``$/rundę``. Zadania ``PORZUCONE`` celowo
    NIE wchodzą: spaliły tokeny i nie dowiozły nic, więc doliczenie ich do
    mianownika maskowałoby dokładnie tę stratę, którą chcemy widzieć.

    Wzorzec jest zakotwiczony na początku wpisu (``re.match``, nie ``search``):
    ten sam tekst w POWODZIE cudzej tury nie ma prawa udawać ukończonego
    zadania.
    """
    found: dict[str, int] = {}
    for line in tail(project, KEEP_LINES, runtime_dir).splitlines():
        match = _COMPLETED.match(line.partition("] ")[2])
        if match:
            found[match.group(1)] = int(match.group(2))
    return list(found.items())


def round_limit_tasks(project: str, runtime_dir: str = ".forge") -> list[str]:
    """Zadania porzucone przez ``round_limit`` w ostatnich ``ROUND_LIMIT_LINES``.

    Jedno zadanie idące na limit rund zajmuje więcej linii niż całe okno
    mistrza, więc wzorzec „planista tnie za grubo" jest z ``compact_tail``
    strukturalnie niewidoczny — trzeba mu go policzyć osobno. Horyzont jest
    własny, nie ``KEEP_LINES``: reguła ma opisywać porażki świeże.
    """
    found: list[str] = []
    for line in tail(project, ROUND_LIMIT_LINES, runtime_dir).splitlines():
        task_id, _, rest = line.partition("] ")[2].partition(" ")
        if rest.startswith("PORZUCONE: round_limit") and task_id not in found:
            found.append(task_id)
    return found
