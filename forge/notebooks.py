"""Prywatne, plikowe notatniki ról pracujących nad zadaniem."""
from __future__ import annotations

import os
import shutil
from pathlib import Path


# Żadna rola nie pisze tu sam — wpisy dokleja Forge z pola `notebook` jej
# decyzji, więc jedna sekcja opisuje cały możliwy kształt pliku.
_TEMPLATES = {
    "tester": """# Prywatny notatnik testera

## Notatki z rund
""",
    "coder": """# Prywatny notatnik kodera

## Notatki z rund
""",
}

# Nietknięty notatnik po zmianie szablonu roli nadal musi być rozpoznawany jako
# pusty — inaczej archiwum porzuconego zadania dostałoby „diagnostykę”, która
# jest samym nagłówkiem, a wznowione zadanie wnosiłoby te nagłówki do każdej
# tury jako rzekome notatki.
_LEGACY_TEMPLATES = {
    role: (f"""# Prywatny notatnik {name}

## Następna tura

## Ustalenia

## Próby i pułapki
""",)
    for role, name in (("tester", "testera"), ("coder", "kodera"))
}

_MIGRATION_HEADING = "## Poprzedni rekord po migracji"


def task_dir(project: str, runtime_dir: str, task_id: str) -> Path:
    return Path(project, runtime_dir, "notebooks", task_id)


def ensure(project: str, runtime_dir: str, task_id: str) -> Path:
    """Utwórz brakujące template'y, nigdy nie nadpisując treści roli."""
    directory = task_dir(project, runtime_dir, task_id)
    directory.mkdir(parents=True, exist_ok=True)
    for role, template in _TEMPLATES.items():
        path = directory / f"{role}.md"
        if not path.exists():
            path.write_text(template, encoding="utf-8")
    return directory


def _empty_variants(role: str) -> tuple[str, ...]:
    template = _TEMPLATES.get(role, "")
    return (template,) + _LEGACY_TEMPLATES.get(role, ())


def _is_empty(role: str, text: str) -> bool:
    """Porównanie po obu stronach obcięte, bo zwracamy treść obciętą.

    Szablon różniący się jedną pustą linią przestałby uchodzić za pusty i
    wnosiłby same nagłówki do każdej tury sesji.
    """
    stripped = text.strip()
    return not stripped or any(
        stripped == variant.strip() for variant in _empty_variants(role))


def _atomic_write(path: Path, text: str) -> None:
    """Podmień treść atomowo, nie narażając wcześniejszych ustaleń.

    Notatnik może oszczędzić całe następne wywołanie agenta z kontekstem
    narzędzi, więc jego dotychczasowa treść jest znacznie cenniejsza niż koszt
    małego pliku tymczasowego. Awaria przed ``os.replace`` zostawia stary plik
    nietknięty.
    """
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def read(project: str, runtime_dir: str, task_id: str, role: str) -> str:
    """Treść notatnika roli do wklejenia w kapsułę.

    Nietknięty szablon zwracamy jako pusty string: rola ma zobaczyć notatki
    albo nic. Sam nagłówek kosztowałby tokeny w każdej turze sesji i sugerował
    istnienie pamięci, której nie ma.
    """
    path = task_dir(project, runtime_dir, task_id) / f"{role}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # Notatnik jest wygodą, nie źródłem prawdy — uszkodzony plik ma
        # kosztować brak podpowiedzi, a nie przerwaną turę.
        return ""
    return "" if _is_empty(role, text) else text.strip()


def append_entry(
        project: str, runtime_dir: str, task_id: str, role: str,
        round_no: int, text: object) -> str:
    """Dopisz wpis rundy w imieniu roli; zwróć zapisaną treść albo pusty string.

    Wpis normalizujemy do jednej linii, ale nigdy nie skracamy: notatnik żyje
    jedno zadanie i ginie razem z nim, więc nie ma czego rotować, a najstarszy
    wpis jest zwykle najcenniejszy (orientacja w repo po najdroższej turze).

    Idempotencja jest świadomie częściowa. Powtórzony identyczny wpis — tura
    powtórzona po padzie procesu między zapisem a checkpointem — jest pomijany,
    ale przeredagowana notatka z tej samej rundy przejdzie. Duplikat linii w
    pliku o czasie życia jednego zadania jest tańszy niż dwufazowy zapis.
    """
    if not isinstance(text, str):
        return ""
    entry = " ".join(text.split())
    if not entry:
        return ""
    ensure(project, runtime_dir, task_id)
    path = task_dir(project, runtime_dir, task_id) / f"{role}.md"
    line = f"- r{round_no}: {entry}"
    existing = path.read_text(encoding="utf-8")
    if line in existing.splitlines():
        return ""
    _atomic_write(path, existing.rstrip() + f"\n{line}\n")
    return entry


def migrate_records(project: str, runtime_dir: str, task_id: str, state) -> bool:
    """Przenieś stare rekordy do plików i wyczyść je w stanie.

    Nagłówek chroni przed duplikacją, gdy proces padnie między zapisem pliku
    i następnym checkpointem.
    """
    changed = False
    ensure(project, runtime_dir, task_id)
    for role in _TEMPLATES:
        attribute = f"{role}_record"
        record = str(getattr(state, attribute, "") or "").strip()
        if not record:
            continue
        path = task_dir(project, runtime_dir, task_id) / f"{role}.md"
        existing = path.read_text(encoding="utf-8")
        if _MIGRATION_HEADING not in existing:
            _atomic_write(
                path,
                existing.rstrip()
                + f"\n\n{_MIGRATION_HEADING}\n\n{record}\n",
            )
        setattr(state, attribute, "")
        changed = True
    return changed


def remove(project: str, runtime_dir: str, task_id: str) -> None:
    shutil.rmtree(task_dir(project, runtime_dir, task_id), ignore_errors=True)


def move_to_failure(
        project: str, runtime_dir: str, task_id: str, artifact: Path) -> None:
    """Przenieś notatniki bez niszczenia diagnostyki z wcześniejszej próby.

    Pierwsze przeniesienie katalogu jest atomowe. Jeśli proces padł później,
    restart odtworzy aktywne template'y; ponowiona porażka nie może nimi
    nadpisać już zarchiwizowanych notatek. Różną, niepustą treść zachowujemy
    jako kolejny plik ``*-retry-N.md``.
    """
    source = task_dir(project, runtime_dir, task_id)
    if not source.exists():
        return
    target = artifact / "notebooks"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        source.replace(target)
        return
    target.mkdir(parents=True, exist_ok=True)
    for source_file in sorted(source.iterdir()):
        if not source_file.is_file():
            continue
        target_file = target / source_file.name
        if not target_file.exists():
            source_file.replace(target_file)
            continue
        source_bytes = source_file.read_bytes()
        target_bytes = target_file.read_bytes()
        role = source_file.stem
        empty = tuple(
            variant.encode() for variant in _empty_variants(role))
        if source_bytes == target_bytes or source_bytes in empty:
            source_file.unlink()
            continue
        if target_bytes in empty:
            source_file.replace(target_file)
            continue
        suffix = 1
        while True:
            retry = target / f"{source_file.stem}-retry-{suffix}{source_file.suffix}"
            if not retry.exists():
                source_file.replace(retry)
                break
            if retry.read_bytes() == source_bytes:
                source_file.unlink()
                break
            suffix += 1
    try:
        source.rmdir()
    except OSError:
        pass


def prune_orphans(
        project: str, runtime_dir: str, active_task_id: str = "") -> None:
    root = Path(project, runtime_dir, "notebooks")
    if not root.is_dir():
        return
    for directory in root.iterdir():
        if directory.is_dir() and directory.name != active_task_id:
            shutil.rmtree(directory, ignore_errors=True)
    try:
        root.rmdir()
    except OSError:
        pass
