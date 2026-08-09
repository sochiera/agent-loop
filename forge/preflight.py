"""Deterministyczne czynności wykonywane przed główną pętlą Forge.

Moduł ma własne, małe pomocniki gitowe zamiast importować je z
``orchestrate``. To świadomie usuwa cykl importów: ``orchestrate`` importuje
preflight, a preflight nie zależy od orkiestratora ani od jego faz.
"""
from __future__ import annotations

import datetime as _dt
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from . import backlog, ledger
from .agents import AgentError

if TYPE_CHECKING:
    from .config import Config
    from .state import State


@dataclass(frozen=True)
class PreflightResult:
    parked_branch: str = ""
    parked_paths: list[str] = field(default_factory=list)
    dropped_tags: list[str] = field(default_factory=list)
    legacy_backlog: bool = False


def git(project: str, *args: str, check: bool = True,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=project, text=True, capture_output=True,
        check=check, env=env)


def _checked(project: str, *args: str) -> subprocess.CompletedProcess:
    result = git(project, *args, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "błąd git").strip()
        raise AgentError(f"preflight: git {' '.join(args)}: {detail}")
    return result


def has_changes(project: str) -> bool:
    return bool(git(project, "status", "--porcelain", check=False).stdout.strip())


def commit_all(project: str, message: str, cfg: Config | None = None) -> None:
    """Commituj parking lokalnie; preflight nie wypycha pracy na remote."""
    if has_changes(project):
        _checked(project, "add", "-A")
        _checked(project, "commit", "-m", message)


def head_state(project: str) -> tuple[str, str]:
    """Zwróć ``("branch", nazwa)`` | ``("detached", sha)`` | unborn."""
    # Na repozytorium po ``git init`` symbolic-ref HEAD istnieje, mimo że nie
    # ma commita. Najpierw rozróżniamy więc unborn od istniejącego HEAD.
    head = git(project, "rev-parse", "--verify", "HEAD", check=False)
    if head.returncode != 0:
        return "unborn", ""
    branch = git(project, "symbolic-ref", "--short", "HEAD", check=False)
    if branch.returncode == 0 and branch.stdout.strip():
        return "branch", branch.stdout.strip()
    return "detached", head.stdout.strip()


def _dirty_paths(project: str) -> list[str]:
    paths: list[str] = []
    for line in git(project, "status", "--porcelain", check=False).stdout.splitlines():
        if len(line) < 4:
            continue
        value = line[3:]
        # Rename has ``old -> new``; oba końce są ważnymi śladami parkingu.
        if " -> " in value:
            paths.extend(part.strip() for part in value.split(" -> "))
        else:
            paths.append(value)
    return paths


def _switch_back(project: str, kind: str, point: str) -> None:
    if kind == "branch":
        _checked(project, "switch", point)
    elif kind == "detached":
        _checked(project, "switch", "--detach", point)


def park_dirty_tree(project: str, cfg: Config, state: State) -> tuple[str, list[str]]:
    """Odłóż zastane zmiany tylko wtedy, gdy nie należą do aktywnego taska."""
    if not has_changes(project):
        return "", []
    paths = _dirty_paths(project)
    if state.current_task:
        ledger.append(project, "preflight: zastane zmiany należą do aktywnego zadania")
        return "", []

    kind, point = head_state(project)
    if kind == "unborn":
        ledger.append(
            project,
            "preflight: repo bez commita bazowego, zmiany zostają dla bootstrapu",
        )
        return "", []

    parked = "forge/parked/" + _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    created = False
    try:
        _checked(project, "switch", "-c", parked)
        created = True
        commit_all(project, "wip: zaparkowana praca sprzed startu Forge", cfg)
        if has_changes(project):
            raise AgentError("preflight: parking nie wyczyścił drzewa")
        _switch_back(project, kind, point)
        if has_changes(project):
            raise AgentError("preflight: po powrocie drzewo nie jest czyste")
    except Exception as exc:  # noqa: BLE001 — zamykamy transakcję parkingu
        # Najlepszy możliwy rollback: wróć po zapamiętanej nazwie/SHA, a gałąź
        # parkingowa nie może zostać aktywnym, cichym stanem procesu.
        try:
            if created:
                _switch_back(project, kind, point)
            if created:
                git(project, "branch", "-D", parked, check=False)
        except Exception as rollback_exc:  # noqa: BLE001
            raise AgentError(
                f"preflight: parking nie powiódł się ({exc}); "
                f"rollback także nie powiódł się ({rollback_exc})") from exc
        if isinstance(exc, AgentError):
            raise
        raise AgentError(f"preflight: parking nie powiódł się: {exc}") from exc

    note = Path(project, ".forge", "parked.md")
    note.parent.mkdir(parents=True, exist_ok=True)
    date = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    return_point = point
    note.write_text(
        "# Zaparkowana praca\n\n"
        f"- gałąź: {parked}\n"
        f"- punkt powrotu: {return_point}\n"
        f"- data: {date}\n"
        f"- pliki: {', '.join(paths) or '(nieznane)'}\n\n"
        f"Powrót: `git switch {parked}`\n",
        encoding="utf-8",
    )
    ledger.append(
        project,
        f"preflight: zaparkowano pracę na {parked} ({', '.join(paths)[:160]})",
    )
    return parked, paths


def drop_stale_task_tags(project: str, state: State) -> list[str]:
    """Usuń tagi startowe, których nie reprezentuje aktywny task."""
    active = str((state.current_task or {}).get("id", ""))
    result = git(project, "tag", "--list", "forge/task-*-start", check=False)
    dropped: list[str] = []
    for tag in result.stdout.splitlines():
        tag = tag.strip()
        if not tag or tag == f"forge/{active}-start":
            continue
        _checked(project, "tag", "-d", tag)
        dropped.append(tag)
    if dropped:
        ledger.append(project, "preflight: usunięto osierocone tagi " + ", ".join(dropped))
    return dropped


def detect_legacy_backlog(project: str, state: State) -> bool:
    """Ustaw flagę migracji i zwróć, czy backlog ma stary format."""
    path = Path(project, "BACKLOG.md")
    if not path.is_file():
        state.backlog_migrated = True
        return False
    legacy = backlog.is_legacy(path.read_text(encoding="utf-8"))
    state.backlog_migrated = not legacy
    if legacy:
        ledger.append(project, "preflight: wykryto stary format BACKLOG.md")
    return legacy


def run(project: str, cfg: Config, state: State) -> PreflightResult:
    parked_branch, parked_paths = park_dirty_tree(project, cfg, state)
    dropped_tags = drop_stale_task_tags(project, state)
    legacy = detect_legacy_backlog(project, state)
    return PreflightResult(parked_branch, parked_paths, dropped_tags, legacy)
