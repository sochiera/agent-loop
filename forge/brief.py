"""Śledzenie zmian głównego briefu między bootstrapem a synchronizacją.

Brief jest źródłem intencji, ale po bootstrapie przestawał uczestniczyć w
procesie. Ten moduł daje Forge deterministyczną odpowiedź na pytanie „czy brief
się zmienił" oraz materiał dla roli diff-bootstrapu: zwarty diff zamiast dwóch
pełnych dokumentów w promptcie.

Snapshot leży w repozytorium projektu, a nie w ``.forge/`` — dzięki temu
historia zmian briefu jest w gicie widoczna razem z wynikającą z niej zmianą
backlogu i opisu projektu.
"""
from __future__ import annotations

import difflib
import hashlib
from pathlib import Path

SNAPSHOT_PATH = "docs/BRIEF-SNAPSHOT.md"
PROJECT_DOC_PATH = "docs/PROJECT.md"
BACKLOG_PATH = "BACKLOG.md"

# Jedyne pliki, które wolno zmienić synchronizacji briefu. Snapshot zapisuje sam
# Forge dopiero po walidacji zakresu, więc rola nigdy go nie dotyka.
SYNC_WRITABLE = (BACKLOG_PATH, PROJECT_DOC_PATH)

# Diff bywa jedyną dużą częścią promptu synchronizacji; pełne przepisanie briefu
# nie może zamienić taniej operacji w wysyłkę całej historii dokumentu.
DIFF_LIMIT = 20_000
# Sufit na awaryjne wysłanie całego briefu, gdy diff się nie mieści.
FULL_LIMIT = 120_000


class TooLargeToSync(ValueError):
    """Zmiany briefu nie da się przekazać w całości — lepiej stanąć niż zgadywać."""


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read(brief_path: str) -> str | None:
    """Treść briefu albo ``None``, gdy pliku nie da się odczytać.

    Rozróżnienie jest istotne: pusty string to poprawny (pusty) brief, a błąd
    odczytu nie może udawać, że użytkownik skasował wszystkie wymagania —
    zapisany na tej podstawie snapshot skasowałby punkt odniesienia.
    """
    try:
        return Path(brief_path).read_text(encoding="utf-8")
    except OSError:
        return None


def snapshot(project: str) -> str:
    path = Path(project, SNAPSHOT_PATH)
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def write_snapshot(project: str, text: str) -> None:
    path = Path(project, SNAPSHOT_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def changed(project: str, recorded_digest: str, current: str | None) -> bool:
    """Czy brief różni się od ostatniej zaakceptowanej wersji.

    Snapshot jest podstawą rozstrzygnięcia, bo to on służy potem za bazę diffu.
    Skrót ze stanu odpowiada tylko wtedy, gdy snapshotu nie ma — czyli w
    projekcie zbootstrapowanym przed tym mechanizmem, który wymaga pierwszej,
    jednorazowej synchronizacji. Nieczytelny brief nie jest żadną zmianą.
    """
    if current is None:
        return False
    previous = snapshot(project)
    if previous:
        return digest(previous) != digest(current)
    return recorded_digest != digest(current)


def diff(previous: str, current: str, limit: int | None = None,
         full_limit: int | None = None) -> str:
    """Zwarty diff briefu; pusta poprzednia wersja daje cały brief jako nowy.

    Diff NIGDY nie jest po cichu obcinany. Po udanej synchronizacji snapshotem
    staje się cały nowy brief, więc nieprzeczytany ogon zmian nie wróciłby już
    do żadnego przeglądu — wymaganie zniknęłoby bez śladu. Zbyt duży diff
    zastępujemy więc pełną bieżącą treścią briefu, a gdy i ona nie mieści się w
    promptcie, przerywamy zamiast zgadywać.
    """
    limit = DIFF_LIMIT if limit is None else limit
    full_limit = FULL_LIMIT if full_limit is None else full_limit
    lines = difflib.unified_diff(
        previous.splitlines(), current.splitlines(),
        fromfile="brief-poprzedni", tofile="brief-biezacy", lineterm="")
    text = "\n".join(lines)
    if len(text) <= limit:
        return text or "(brak różnic tekstowych)"
    if len(current) > full_limit:
        raise TooLargeToSync(
            f"diff ma {len(text)} znaków, a cały brief {len(current)} "
            f"(limit {full_limit}). Podziel brief na mniejsze dokumenty.")
    return ("(diff przekroczył limit — poniżej PEŁNA bieżąca treść briefu; "
            "uzgodnij z nią backlog i opis projektu)\n\n" + current)


def out_of_scope(paths: list[str]) -> list[str]:
    """Ścieżki poza zakresem zapisu synchronizacji briefu."""
    return sorted(name for name in paths if name not in SYNC_WRITABLE)
