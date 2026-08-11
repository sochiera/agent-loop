"""Deterministyczne czynności wykonywane przed główną pętlą Forge.

Moduł ma własne, małe pomocniki gitowe zamiast importować je z
``orchestrate``. To świadomie usuwa cykl importów: ``orchestrate`` importuje
preflight, a preflight nie zależy od orkiestratora ani od jego faz.
"""
from __future__ import annotations

import datetime as _dt
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from . import adapters, agents, backlog, ledger, provider_env, runlock, snapshot
from .agents import AgentError, opencode_user_config

if TYPE_CHECKING:
    from .config import Config
    from .state import State


@dataclass(frozen=True)
class PreflightResult:
    parked_branch: str = ""
    parked_paths: list[str] = field(default_factory=list)
    dropped_tags: list[str] = field(default_factory=list)
    legacy_backlog: bool = False
    loaded_env_vars: list[str] = field(default_factory=list)
    claude_session: str = ""


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


def ensure_provider_credentials(project: str, cfg: Config) -> list[str]:
    """Dobierz brakujące klucze providerów OpenCode; przerwij, jeśli się nie da.

    Odpowiedź ``401 No API-key provided`` przychodzi w środku pracy roli, więc
    kosztuje pełną turę agenta i przerywa przebieg. Ten sam błąd wykryty tutaj
    kosztuje odczyt jednego JSON-a.

    Brak klucza sam w sobie nie zatrzymuje przebiegu — dopiero rola, której CAŁY
    łańcuch (wybór i zapasy) prowadzi do dostawcy bez klucza, jest niewykonalna
    i tylko ona uzasadnia przerwanie."""
    models = cfg.opencode_models_in_use()
    if not models:
        return []
    loaded, absent = provider_env.resolve(models, opencode_user_config())
    if loaded:
        ledger.append(
            project,
            "preflight: uzupełniono klucze providerów ze środowiska plikowego: "
            + ", ".join(loaded))
    if not absent:
        return loaded
    detail = "; ".join(f"{provider} wymaga {name}" for provider, name in absent)
    hint = (f"Ustaw te zmienne w środowisku albo w pliku *.env w "
            f"{provider_env.config_dir()}.")
    blocked = cfg.roles_blocked_by({provider for provider, _name in absent})
    if blocked:
        # Pełna lista potrafi mieć kilkadziesiąt pozycji i zasłonić samą
        # przyczynę; do decyzji operatora wystarczy próbka i skala problemu.
        shown = ", ".join(blocked[:5])
        if len(blocked) > 5:
            shown += f" (+{len(blocked) - 5} innych)"
        raise AgentError(
            "preflight: brak kluczy API providerów OpenCode "
            f"({detail}); bez nich nie ma czym wykonać {len(blocked)} "
            f"kombinacji rola/trudność: {shown}. " + hint)
    ledger.append(
        project,
        f"preflight: brak kluczy API ({detail}) — role mają działające zapasy, "
        "ale wybrany dostawca odpadnie. " + hint)
    return loaded


CLAUDE_SESSION_HINT = (
    "Ustaw CLAUDE_CODE_OAUTH_TOKEN (token z `claude setup-token` nie rotuje, "
    "więc znosi równoległe instancje) albo zaloguj się ponownie w Claude Code.")


def ensure_claude_session(project: str, cfg: Config) -> str:
    """Sprawdź sesję Claude Code, zanim zapłacimy za pierwszą rolę.

    ``OAuth session expired and could not be refreshed`` przychodzi w środku
    tury: kosztuje pełne wywołanie agenta, przerywa przebieg i zostawia po sobie
    checkpoint do ręcznego wznowienia. Ten sam stan widać tutaj po odczycie
    jednego JSON-a.

    Jak przy kluczach providerów: niesprawna sesja zatrzymuje przebieg tylko
    wtedy, gdy jakaś rola nie ma już czym pracować."""
    if not any(adapters.canonical_agent(name) == "claude"
               for name in cfg.agents_in_use()):
        return ""
    # Token bywa w tym samym pliku *.env, co klucze providerów — proces
    # uruchomiony z crona albo z launchera desktopowego nie ma go w środowisku.
    provider_env.load_missing(set(agents.CLAUDE_TOKEN_VARS))
    problem = agents.claude_session_problem()
    if not problem:
        return ""
    blocked = cfg.roles_requiring_agent("claude")
    if blocked:
        shown = ", ".join(blocked[:5])
        if len(blocked) > 5:
            shown += f" (+{len(blocked) - 5} innych)"
        raise AgentError(
            f"preflight: sesja Claude Code jest nieużywalna ({problem}); bez "
            f"niej nie ma czym wykonać {len(blocked)} kombinacji rola/trudność: "
            f"{shown}. " + CLAUDE_SESSION_HINT)
    ledger.append(
        project,
        f"preflight: sesja Claude Code jest nieużywalna ({problem}) — role mają "
        "działające zapasy, ale Claude z nich wypadnie. " + CLAUDE_SESSION_HINT)
    return problem


SHARED_CLAUDE_OVERRIDE = "FORGE_ALLOW_SHARED_CLAUDE"
CLAUDE_SESSION_LOCK_NAME = "claude-file-session.lock"


def claude_file_session_lock_path(
    environ: dict[str, str] | None = None
) -> Path:
    """Jeden zamek na maszynę — plik sesji Claude Code jest zasobem globalnym."""
    return snapshot.cache_root(environ) / CLAUDE_SESSION_LOCK_NAME


def _claude_busy_message(holder: str) -> str:
    detail = f" ({holder})" if holder else ""
    return (
        f"Inny bieg Forge{detail} pracuje już na WSPÓŁDZIELONYM pliku sesji "
        "Claude Code. Refresh token jest jednorazowy, więc drugi proces "
        "unieważni sesję obu biegów i interaktywną sesję operatora (patrz "
        "docs/AWARIE-2026-08-11.md). Uruchom `claude setup-token` i ustaw "
        "CLAUDE_CODE_OAUTH_TOKEN — nierotujący token znosi równoległość — albo "
        "poczekaj na tamten bieg, albo wskaż modele innego narzędzia. "
        f"Świadome ominięcie: {SHARED_CLAUDE_OVERRIDE}=1.")


def claude_file_session_lock(
    cfg: Config, environ: dict[str, str] | None = None
) -> "runlock.RunLock | None":
    """Wyłączność na PLIKOWY tryb sesji Claude Code; ``None`` = nie dotyczy.

    W trybie plikowym wszystkie instancje trzymają ten sam JEDNORAZOWY refresh
    token: pierwsze odświeżenie unieważnia kopie pozostałych, a użycie zużytego
    tokenu kasuje sesję po stronie serwera — razem z interaktywną sesją
    operatora (``docs/AWARIE-2026-08-11.md``, awaria A).

    Zamek jest procesowy i leży poza projektem, bo chroniony zasób jest
    globalny: obowiązuje tak samo drugie okno GUI, jak i uruchomienie z linii
    poleceń. Nie dotyczy biegów z nierotującym tokenem — te mogą chodzić
    równolegle w dowolnej liczbie i żadnego zamku nie biorą.

    Podnosi ``runlock.RunLocked``, gdy sesję trzyma już inny proces."""
    environ = os.environ if environ is None else environ
    override = (environ.get(SHARED_CLAUDE_OVERRIDE) or "").strip().lower()
    if override in {"1", "true", "yes", "tak"}:
        return None
    if not any(adapters.canonical_agent(name) == "claude"
               for name in cfg.agents_in_use()):
        return None
    # Ta sama ścieżka, co w ``ensure_claude_session``: token bywa w pliku
    # ``*.env``, więc powłoka bez eksportu nie jest jeszcze powodem do odmowy.
    provider_env.load_missing(set(agents.CLAUDE_TOKEN_VARS), environ)
    if agents.claude_oauth_token(environ):
        return None
    return runlock.RunLock(claude_file_session_lock_path(environ),
                           _claude_busy_message).acquire()


def claude_file_session_busy(
    cfg: Config, environ: dict[str, str] | None = None
) -> str:
    """Podgląd dla warstwy uruchamiającej: ``""`` = można startować.

    Rozstrzyga zamek brany przez sam orkiestrator — tutaj tylko zaglądamy, żeby
    operator zobaczył powód przed startem procesu, a nie po nim."""
    try:
        lock = claude_file_session_lock(cfg, environ)
    except runlock.RunLocked as exc:
        return str(exc)
    except OSError:
        return ""
    if lock is not None:
        lock.release()
    return ""


def run(project: str, cfg: Config, state: State) -> PreflightResult:
    parked_branch, parked_paths = park_dirty_tree(project, cfg, state)
    dropped_tags = drop_stale_task_tags(project, state)
    legacy = detect_legacy_backlog(project, state)
    loaded = ensure_provider_credentials(project, cfg)
    claude_session = ensure_claude_session(project, cfg)
    return PreflightResult(
        parked_branch, parked_paths, dropped_tags, legacy, loaded,
        claude_session)
