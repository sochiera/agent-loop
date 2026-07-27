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


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read(brief_path: str) -> str:
    """Treść briefu; brak pliku oznacza brak podstawy do synchronizacji."""
    try:
        return Path(brief_path).read_text(encoding="utf-8")
    except OSError:
        return ""


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


def changed(project: str, recorded_digest: str, current: str) -> bool:
    """Czy brief różni się od ostatniej zaakceptowanej wersji.

    Snapshot jest podstawą rozstrzygnięcia, bo to on służy potem za bazę diffu.
    Skrót ze stanu odpowiada tylko wtedy, gdy snapshotu nie ma — czyli w
    projekcie zbootstrapowanym przed tym mechanizmem, który wymaga pierwszej,
    jednorazowej synchronizacji.
    """
    if not current:
        return False
    previous = snapshot(project)
    if previous:
        return digest(previous) != digest(current)
    return recorded_digest != digest(current)


def diff(previous: str, current: str, limit: int = DIFF_LIMIT) -> str:
    """Zwarty diff briefu; pusta poprzednia wersja daje cały brief jako nowy."""
    lines = difflib.unified_diff(
        previous.splitlines(), current.splitlines(),
        fromfile="brief-poprzedni", tofile="brief-biezacy", lineterm="")
    text = "\n".join(lines)
    if len(text) > limit:
        text = text[:limit] + f"\n…[obcięto {len(text) - limit} znaków diffu]…"
    return text or "(brak różnic tekstowych)"


def out_of_scope(paths: list[str]) -> list[str]:
    """Ścieżki poza zakresem zapisu synchronizacji briefu."""
    return sorted(name for name in paths if name not in SYNC_WRITABLE)
