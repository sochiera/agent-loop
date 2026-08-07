"""Deterministyczna bramka przed wywołaniem mistrza.

Mistrz jest wołany raz na rundę, a interweniuje w kilkunastu procentach
przypadków. Wszystkie cztery warunki jego interwencji z ``master-system.md`` są
mechanicznie sprawdzalne, więc sprawdza je Python, a model dostaje pytanie
dopiero przy trafieniu. Reguły zostają identyczne — zmienia się wyłącznie to,
KTO sprawdza warunek wyzwolenia. Sformułowanie noty zostaje przy modelu: nota
cytuje ``reason`` i powtarzany wpis dziennika, a tego regułą się nie zrobi.

**Zasada nadrzędna: bramka widzi dokładnie to, co widzi mistrz.** Wejściem są
te same dwie rzeczy, które trafiają do jego promptu — ``ledger.compact_tail``
i ``ledger.round_limit_tasks`` — a nie ``State``. Powód jest praktyczny, nie
estetyczny: gdyby bramka liczyła się ze stanu, a mistrz z dziennika, obie
mogłyby się rozjechać po restarcie albo po zmianie sposobu zapisu wpisu, a
bramka wyciszałaby mistrza tam, gdzie on by zareagował. Efekt uboczny wspólnego
wejścia: bramka jest funkcją czystą, testowalną bez repozytorium, bez ``git``
i bez LLM-a.

Bramka polega na tym, że ``ledger._compact_line`` chroni ``pliki=`` przy
cięciu. To jest własność, nie przypadek — i pilnuje jej test regresyjny.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

from .ledger import TASK_ID_BODY

_TASK_ID = re.compile(TASK_ID_BODY)

# Nazwy triggerów są stabilne: trafiają do logu trybu cieni i to po nich liczy
# się rozbieżności między bramką a mistrzem.
TRIGGER_REPEATED = "powtórzona-decyzja"
TRIGGER_CODER_TOUCHED_TEST = "koder-ruszył-test"
TRIGGER_REVIEW_LOOP = "pętla-recenzji"
TRIGGER_ROUND_LIMIT = "round-limit"
TRIGGER_PLAN_SIFT = "odsiew-planisty"

# Próg serii odsiewów: jeden bywa ucięciem planisty przez limit, dopiero drugi
# pod rząd znaczy, że wsad przekracza jego zdolność domykania.
SIFT_STREAK_THRESHOLD = 2

_NO_CHANGES = "bez_zmian"
_TEST_DIR_NAMES = {"test", "tests", "spec", "specs", "testing", "__tests__"}


def looks_like_test_path(path: str) -> bool:
    """Czy ścieżka wygląda na plik testowy.

    To JEDYNA heurystyka w całym module i jedyny warunek, który może dać
    fałszywy negatyw: Forge jest stack-agnostyczny, więc nie zna konwencji
    testów projektu — bootstrap ustala komendę testową, nie układ katalogów.
    Uściślić tego nie da się bez wprowadzenia wiedzy o stacku, której cała
    reszta narzędzia świadomie nie ma. Projekt o nietypowej konwencji nazw
    sprawi, że mistrz nie zostanie zawołany tam, gdzie dziś by zareagował —
    dlatego bramka wchodzi przez tryb cieni, a ta funkcja stoi osobno, żeby
    rozszerzenie jej było jedną linią.

    Dopasowanie jest świadomie ZBYT SZEROKIE (``latest.py`` je przejdzie).
    Asymetria kosztów jest jednoznaczna: fałszywy pozytyw kosztuje jedno
    wywołanie mistrza, czyli dokładnie to, co dzieje się dziś, a fałszywy
    negatyw wycisza go tam, gdzie miał zareagować.
    """
    path = str(path).strip().replace("\\", "/")
    if not path:
        return False
    name = posixpath.basename(path).lower()
    if "test" in name or "spec" in name:
        return True
    return any(part.lower() in _TEST_DIR_NAMES
               for part in posixpath.dirname(path).split("/"))


@dataclass(frozen=True)
class Entry:
    """Jeden wpis tury z dziennika: ``{id} r{N} {rola}→{status} pliki={…}: …``."""

    task_id: str
    role: str
    status: str
    files: str

    @property
    def changed(self) -> bool:
        return self.files.strip() != _NO_CHANGES

    def paths(self) -> list[str]:
        if not self.changed:
            return []
        body = self.files.strip().strip("[]")
        return [item.strip() for item in body.split(",") if item.strip()]


def _split_files(rest: str) -> str:
    """Wytnij wartość ``pliki=`` sprzed dwukropka otwierającego POWÓD.

    Lista plików sama zawiera przecinki i bywa przycięta przez
    ``ledger._compact_line`` razem z zamykającym nawiasem, więc rozdzielamy po
    strukturze, a nie po pierwszym dwukropku.
    """
    rest = rest.strip()
    if rest.startswith("["):
        end = rest.find("]")
        return rest[:end + 1] if end != -1 else rest
    return rest.partition(":")[0].strip()


def parse_entry(line: str) -> Entry | None:
    """Wpis tury albo ``None`` dla linii innego rodzaju (start, plan, commit)."""
    body = line.partition("] ")[2] or line
    task_id, _, rest = body.strip().partition(" ")
    if not _TASK_ID.fullmatch(task_id):
        return None
    head, marker, tail = rest.partition(" pliki=")
    if not marker:
        return None
    role, arrow, status = head.strip().rpartition("→")
    if not arrow:
        return None
    # `r3 koder` → `koder`; wpis recenzji nie ma numeru rundy.
    role = role.strip().rpartition(" ")[2]
    return Entry(task_id=task_id, role=role, status=status.strip(),
                 files=_split_files(tail))


def parse_tail(ledger_tail: str) -> list[Entry]:
    entries = (parse_entry(line) for line in (ledger_tail or "").splitlines())
    return [entry for entry in entries if entry is not None]


def repeated_decision(entries: list[Entry]) -> bool:
    """Dwie ostatnie tury tej samej roli, ta sama decyzja, obie bez zmian.

    Warunek mówi „tej samej roli" — naprzemienne tury testera i kodera bez
    zmian to normalna rozmowa, nie pętla.
    """
    if len(entries) < 2:
        return False
    last, previous = entries[-1], entries[-2]
    return (last.role == previous.role
            and last.status == previous.status
            and not last.changed and not previous.changed)


def coder_touched_test(entries: list[Entry]) -> bool:
    """Ostatnia tura kodera ruszyła plik wyglądający na testowy."""
    for entry in reversed(entries):
        if entry.role == "koder":
            return any(looks_like_test_path(path) for path in entry.paths())
    return False


def review_loop(entries: list[Entry]) -> bool:
    """Kolejne ``recenzja→request_changes`` bez żadnej zmiany plików pomiędzy."""
    marks = [index for index, entry in enumerate(entries)
             if entry.role == "recenzja" and entry.status == "request_changes"]
    if len(marks) < 2:
        return False
    start, end = marks[-2], marks[-1]
    return not any(entry.changed for entry in entries[start + 1:end])


def trigger(ledger_tail: str, round_limit_tasks: list[str] | None = None, *,
            task_id: str = "", next_role: str = "",
            plan_sift_streak: int = 0) -> str:
    """Nazwa spełnionego warunku interwencji albo pusty string.

    Brak ``task_id`` oznacza planowanie wsadu (tak samo jak w
    ``prompts.master_position``): tester i koder nie zostaną wtedy wywołani,
    więc sensowne są wyłącznie dwa warunki dotyczące planisty.

    Warunki muszą pokrywać się 1:1 z listą w ``master-system.md``. Każdy warunek
    obecny w promptcie, a nieobecny tutaj, jest w trybie ``on`` po cichu
    wyciszany — dlatego ten moduł zmienia się ZAWSZE razem z tamtym plikiem.
    """
    if not task_id or next_role == "planner":
        if len(round_limit_tasks or []) >= 2:
            return TRIGGER_ROUND_LIMIT
        if plan_sift_streak >= SIFT_STREAK_THRESHOLD:
            return TRIGGER_PLAN_SIFT
        return ""
    entries = [entry for entry in parse_tail(ledger_tail)
               if entry.task_id == task_id]
    if repeated_decision(entries):
        return TRIGGER_REPEATED
    if coder_touched_test(entries):
        return TRIGGER_CODER_TOUCHED_TEST
    if review_loop(entries):
        return TRIGGER_REVIEW_LOOP
    return ""
