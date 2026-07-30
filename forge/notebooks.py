"""Prywatne, plikowe notatniki ról pracujących nad zadaniem."""
from __future__ import annotations

import shutil
from pathlib import Path


_TEMPLATES = {
    "tester": """# Prywatny notatnik testera

## Następna tura

## Ustalenia

## Próby i pułapki
""",
    "coder": """# Prywatny notatnik kodera

## Następna tura

## Ustalenia

## Próby i pułapki
""",
}

_MIGRATION_HEADING = "## Poprzedni rekord po migracji"


def task_dir(project: str, runtime_dir: str, task_id: str) -> Path:
    return Path(project, runtime_dir, "notebooks", task_id)


def relative_path(runtime_dir: str, task_id: str, role: str) -> str:
    return (Path(runtime_dir) / "notebooks" / task_id / f"{role}.md").as_posix()


def ensure(project: str, runtime_dir: str, task_id: str) -> Path:
    """Utwórz brakujące template'y, nigdy nie nadpisując treści roli."""
    directory = task_dir(project, runtime_dir, task_id)
    directory.mkdir(parents=True, exist_ok=True)
    for role, template in _TEMPLATES.items():
        path = directory / f"{role}.md"
        if not path.exists():
            path.write_text(template, encoding="utf-8")
    return directory


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
            path.write_text(
                existing.rstrip()
                + f"\n\n{_MIGRATION_HEADING}\n\n{record}\n",
                encoding="utf-8",
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
        template = _TEMPLATES.get(role, "").encode()
        if source_bytes == target_bytes or source_bytes == template:
            source_file.unlink()
            continue
        if target_bytes == template:
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
