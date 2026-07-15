"""Orkiestrator: pętla plan → implement → test → review → fix → commit/rollback.

Uruchomienie:
    python -m forge.orchestrate --brief game_brief.md --project game

Pętla jest wznawialna: stan trzyma STATE.json, a cała wiedza o grze żyje w
repo (docs/, kod, historia gita). Po wyczerpaniu limitów zatrzymuje się czysto
i przy kolejnym uruchomieniu kontynuuje.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

from . import prompts
from .agents import (AgentError, LimitExhausted, extract_json, run_codex,
                     run_codex_session, run_planner)
from .config import Config
from .state import State


def ts() -> str:
    return _dt.datetime.now().strftime("%H:%M:%S")


def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", flush=True)


CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")
CODEX_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")
PLANNER_AGENTS = ("claude", "codex")
_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)([smh]?)$", re.IGNORECASE)


def parse_start_delay(value: str) -> float:
    """Zamień np. 30, 30s, 5m lub 2h na sekundy."""
    match = _DURATION_RE.fullmatch(value.strip())
    if not match:
        raise argparse.ArgumentTypeError(
            "czas musi mieć format liczby z opcjonalnym sufiksem s, m lub h"
        )
    amount = float(match.group(1))
    multiplier = {"": 1, "s": 1, "m": 60, "h": 3600}[match.group(2).lower()]
    return amount * multiplier


def _format_elapsed(seconds: float) -> str:
    total = max(0, round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{seconds:.1f}s"


def wait_before_start(delay_s: float) -> None:
    """Odczekaj przed startem, raportując planowany i rzeczywisty czas."""
    if delay_s <= 0:
        return
    started_at = _dt.datetime.now()
    expected_at = started_at + _dt.timedelta(seconds=delay_s)
    log(f"Opóźniony start: początek {started_at.strftime('%Y-%m-%d %H:%M:%S')}, "
        f"czekam {_format_elapsed(delay_s)}, "
        f"oczekiwany start ~{expected_at.strftime('%Y-%m-%d %H:%M:%S')}.")
    started = time.monotonic()
    completed = False
    try:
        time.sleep(delay_s)
        completed = True
    finally:
        elapsed = time.monotonic() - started
        status = "zakończone" if completed else "przerwane"
        log(f"Oczekiwanie {status}: faktyczny czas {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}, "
            f"actual elapsed: {_format_elapsed(elapsed)}.")


def _ask_value(label: str, default: str, *, display_default: str | None = None) -> str:
    shown = display_default if display_default is not None else default
    return input(f"{label} [{shown}]: ").strip() or default


def _ask_effort(label: str, default: str, allowed: tuple[str, ...]) -> str:
    choices = "/".join(allowed)
    while True:
        value = _ask_value(f"{label} ({choices})", default).lower()
        if value in allowed:
            return value
        print(f"Niepoprawny effort: {value!r}. Wybierz jedną z: {choices}.")


def prompt_agent_settings(cfg: Config) -> None:
    """Pobierz modele i effort obu ról przed uruchomieniem pętli."""
    print("\nKonfiguracja agentów (Enter zachowuje wartość domyślną):")
    previous_agent = cfg.planner_agent
    cfg.planner_agent = _ask_effort(
        "Agent do planowania", cfg.planner_agent, PLANNER_AGENTS
    )
    if cfg.planner_agent != previous_agent:
        if cfg.planner_agent == "claude":
            cfg.planner_model, cfg.planner_effort = "opus", "high"
        else:
            cfg.planner_model, cfg.planner_effort = cfg.codex_model, cfg.codex_effort
    cfg.planner_model = _ask_value(
        f"Model do planowania ({cfg.planner_agent})", cfg.planner_model,
        display_default=cfg.planner_model or "z konfiguracji CLI",
    )
    planner_efforts = (CLAUDE_EFFORTS if cfg.planner_agent == "claude"
                       else CODEX_EFFORTS)
    cfg.planner_effort = _ask_effort(
        "Effort planowania", cfg.planner_effort, planner_efforts
    )
    cfg.codex_model = _ask_value(
        "Model do implementacji (Codex)", cfg.codex_model,
        display_default=cfg.codex_model or "z config.toml",
    )
    cfg.codex_effort = _ask_effort(
        "Effort implementacji", cfg.codex_effort, CODEX_EFFORTS
    )
    print()


# --- Git ---------------------------------------------------------------------

def git(project: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=project, text=True,
                          capture_output=True, check=check)


def ensure_repo(project: str) -> None:
    if not os.path.isdir(os.path.join(project, ".git")):
        git(project, "init", "-q")
        git(project, "config", "user.name", "forge-bot", check=False)
        git(project, "config", "user.email", "forge@localhost", check=False)
        log("Zainicjowano repozytorium git.")
    gi = os.path.join(project, ".gitignore")
    needed = [".forge/", "STOP", "STATE.json", "__pycache__/", "*.pyc"]
    existing = ""
    if os.path.exists(gi):
        with open(gi, "r", encoding="utf-8") as f:
            existing = f.read()
    add = [p for p in needed if p not in existing]
    if add:
        with open(gi, "a", encoding="utf-8") as f:
            f.write(("\n" if existing and not existing.endswith("\n") else "")
                    + "\n".join(add) + "\n")


def has_changes(project: str) -> bool:
    out = git(project, "status", "--porcelain").stdout.strip()
    return bool(out)


def commit_all(project: str, message: str, cfg: "Config | None" = None) -> None:
    git(project, "add", "-A")
    if git(project, "diff", "--cached", "--quiet", check=False).returncode != 0:
        # Bez trailera Co-Authored-By (zgodnie z preferencją użytkownika).
        git(project, "commit", "-q", "-m", message)
        log(f"Commit: {message}")
        if cfg and cfg.git_push:
            push(project, cfg)


def push(project: str, cfg: Config) -> None:
    """Wypchnij bieżący branch; błąd remote jest niekrytyczny dla pętli."""
    if not git(project, "remote", check=False).stdout.strip():
        return
    branch = git(project, "rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip()
    if not branch or branch == "HEAD":
        branch = "main"
    result = git(project, "push", "-u", cfg.git_remote, branch, check=False)
    if result.returncode != 0:
        log(f"PUSH nieudany (niekrytyczne): {(result.stderr or '').strip()[:200]}")
    else:
        log(f"Push → {cfg.git_remote}/{branch}")


def rollback(project: str) -> None:
    """Wycofaj nieudaną iterację do ostatniego dobrego commita."""
    git(project, "reset", "--hard", "HEAD", check=False)
    git(project, "clean", "-fd", check=False)  # usuwa nowe pliki (poza .gitignore)
    log("ROLLBACK: przywrócono stan z ostatniego commita.")


# --- Bramka testów -----------------------------------------------------------

def run_tests(project: str, test_cmd: str, timeout: int) -> bool:
    if not test_cmd:
        log("Testy: brak test_cmd → czerwone.")
        return False
    log(f"Bramka testów: {test_cmd}")
    try:
        argv = shlex.split(test_cmd)
        if not argv:
            log("Testy: pusta komenda → czerwone.")
            return False
        proc = subprocess.run(argv, cwd=project, shell=False, text=True,
                              capture_output=True, timeout=timeout)
    except ValueError as exc:
        log(f"Testy: niepoprawna składnia komendy ({exc}) → czerwone.")
        return False
    except OSError as exc:
        log(f"Testy: nie udało się uruchomić komendy ({exc}) → czerwone.")
        return False
    except subprocess.TimeoutExpired:
        log("Testy: TIMEOUT → czerwone.")
        return False
    green = proc.returncode == 0
    log(f"Testy: {'ZIELONE' if green else 'CZERWONE (rc=%d)' % proc.returncode}")
    if not green:
        tail = (proc.stdout or "") + (proc.stderr or "")
        print(tail[-1200:])
    return green


def build_then_test(project: str, build_cmd: str, test_cmd: str, timeout: int) -> bool:
    """Bramka: najpierw build (jeśli podany), potem testy. Obie komendy shell-free.

    Dla stacków kompilowanych (np. C++/CMake) bez tego kroku testy nie mają jak
    przejść. Build padnie → bramka czerwona (jak nieudane testy)."""
    if build_cmd:
        log(f"Build: {build_cmd}")
        try:
            argv = shlex.split(build_cmd)
        except ValueError as exc:
            log(f"Build: niepoprawna składnia ({exc}) → czerwony.")
            return False
        if argv:
            try:
                proc = subprocess.run(argv, cwd=project, shell=False, text=True,
                                      capture_output=True, timeout=timeout)
            except (OSError, subprocess.TimeoutExpired) as exc:
                log(f"Build: nie udało się uruchomić ({exc}) → czerwony.")
                return False
            if proc.returncode != 0:
                log(f"Build: CZERWONY (rc={proc.returncode})")
                print(((proc.stdout or "") + (proc.stderr or ""))[-1200:])
                return False
            log("Build: OK")
    return run_tests(project, test_cmd, timeout)


# --- Preflight ---------------------------------------------------------------

def preflight(cfg: Config) -> list[str]:
    problems = []
    if shutil.which("git") is None:
        problems.append("Brak 'git' na PATH.")
    if cfg.planner_agent == "claude" and shutil.which(cfg.claude_bin) is None:
        problems.append(
            f"Nie znaleziono Claude CLI ('{cfg.claude_bin}'). Zainstaluj Claude Code "
            "jako standalone CLI albo ustaw FORGE_CLAUDE_BIN na pełną ścieżkę.")
    if shutil.which(cfg.codex_bin) is None:
        problems.append(f"Nie znaleziono Codex CLI ('{cfg.codex_bin}').")
    if not os.path.exists(cfg.brief_path):
        problems.append(f"Brak pliku briefu: {cfg.brief_path}")
    return problems


# --- Fazy --------------------------------------------------------------------

def phase_bootstrap(cfg: Config, project: str, state: State, logf) -> None:
    log("=== BOOTSTRAP ===")
    with open(cfg.brief_path, "r", encoding="utf-8") as f:
        brief = f.read()
    out = run_planner(prompts.bootstrap_prompt(brief), cfg, project, logf("bootstrap"))
    data = extract_json(out)
    if not data:
        raise AgentError("Bootstrap nie zwrócił poprawnego obiektu JSON.")
    required = ("stack", "test_cmd", "build_cmd", "run_cmd")
    invalid = [key for key in required if not isinstance(data.get(key), str)]
    if invalid:
        raise AgentError(f"Bootstrap zwrócił niepoprawne pola: {', '.join(invalid)}.")
    if not data["stack"].strip() or not data["test_cmd"].strip() or not data["run_cmd"].strip():
        raise AgentError("Bootstrap musi określić stack oraz niepuste komendy test i run.")
    if not build_then_test(project, data["build_cmd"], data["test_cmd"], cfg.agent_timeout_s):
        raise AgentError("Build/testy szkieletu po bootstrapie nie przeszły.")
    state.stack = data.get("stack", "")
    state.test_cmd = data.get("test_cmd", "")
    state.build_cmd = data.get("build_cmd", "")
    state.run_cmd = data.get("run_cmd", "")
    state.bootstrapped = True
    log(f"Stack: {state.stack or '(nieokreślony)'} | test_cmd: {state.test_cmd or '(brak!)'}")
    commit_all(project, "chore: bootstrap projektu (design, architektura, backlog, szkielet)", cfg)


def phase_plan(cfg: Config, project: str, logf) -> dict:
    log(f"--- PLAN ({cfg.planner_agent}) ---")
    current_task = os.path.join(project, cfg.runtime_dir, "current_task.md")
    try:
        os.remove(current_task)
    except FileNotFoundError:
        pass
    out = run_planner(prompts.plan_prompt(), cfg, project, logf("plan"))
    commit_all(project, "docs: aktualizacja planu i backlogu", cfg)  # plan może dotknąć docs/backlog
    return extract_json(out) or {"task_title": "(nieznane)", "no_more_tasks": False}


def phase_implement(cfg: Config, project: str, test_cmd: str, logf) -> dict:
    log("--- IMPLEMENT (Codex, TDD) ---")
    out = run_codex(prompts.implement_prompt(test_cmd), cfg, project, logf("implement"))
    return extract_json(out) or {}


def phase_review(cfg: Config, project: str, test_cmd: str, green: bool, logf) -> dict:
    log(f"--- REVIEW ({cfg.planner_agent}) ---")
    out = run_planner(prompts.review_prompt(test_cmd, green), cfg, project, logf("review"))
    return extract_json(out) or {"verdict": "changes", "notes": ["Brak werdyktu JSON — wymagam poprawek."]}


def phase_fix(cfg: Config, project: str, notes: list[str], test_cmd: str, logf) -> dict:
    log("--- FIX (Codex) ---")
    out = run_codex(prompts.fix_prompt(notes, test_cmd), cfg, project, logf("fix"))
    return extract_json(out) or {}


def record_failure(project: str, cfg: Config, state: State, title: str, reason: str) -> None:
    state.failures.append(f"{title}: {reason}")
    path = os.path.join(project, cfg.runtime_dir, "failures.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"- [{ts()}] {title} — {reason}\n")


# --- Pętla główna ------------------------------------------------------------

def save_checkpoint(project: str, state: State) -> None:
    state.save(os.path.join(project, "STATE.json"))


def clear_checkpoint(state: State) -> None:
    state.phase = "idle"
    state.current_task_title = ""
    state.fix_attempt = 0
    state.tests_green = False
    state.review_notes = []


def _legacy_iteration(cfg: Config, project: str, state: State) -> bool:
    """Jedna wznawialna iteracja. Zwraca False po ukończeniu MVP."""
    n = state.iteration + 1
    log(f"########## ITERACJA {n} ##########")

    def logf(phase: str) -> str:
        return os.path.join(project, cfg.runtime_dir, "logs", f"iter-{n:04d}-{phase}.log")

    starting_phase = state.phase
    if starting_phase == "idle":
        state.phase = "plan"
        save_checkpoint(project, state)
    else:
        log(f"WZNAWIAM fazę '{starting_phase}' zadania: "
            f"{state.current_task_title or '(jeszcze bez tytułu)'}")

    if state.phase == "plan":
        plan = phase_plan(cfg, project, logf)
        if plan.get("no_more_tasks"):
            clear_checkpoint(state)
            save_checkpoint(project, state)
            log("PLAN: brak dalszych zadań — MVP ukończone. 🎉")
            return False
        state.current_task_title = plan.get("task_title", "(zadanie)")
        state.phase = "implement"
        save_checkpoint(project, state)

    title = state.current_task_title or "(zadanie)"
    log(f"Zadanie: {title}")

    if state.phase == "implement":
        phase_implement(cfg, project, state.test_cmd, logf)
        state.tests_green = build_then_test(
            project, state.build_cmd, state.test_cmd, cfg.agent_timeout_s
        )
        state.phase = "review"
        save_checkpoint(project, state)
    elif state.phase == "review":
        # Po restarcie nie ufamy staremu wynikowi: kod lub środowisko mogły się
        # zmienić, więc przed ponownym review odtwarzamy bramkę build/test.
        state.tests_green = build_then_test(
            project, state.build_cmd, state.test_cmd, cfg.agent_timeout_s
        )
        save_checkpoint(project, state)

    approved = False
    while state.phase in {"review", "fix"}:
        if state.phase == "fix":
            phase_fix(cfg, project, state.review_notes, state.test_cmd, logf)
            state.tests_green = build_then_test(
                project, state.build_cmd, state.test_cmd, cfg.agent_timeout_s
            )
            state.fix_attempt += 1
            state.review_notes = []
            state.phase = "review"
            save_checkpoint(project, state)

        review = phase_review(cfg, project, state.test_cmd, state.tests_green, logf)
        if review.get("verdict") == "approve" and state.tests_green:
            approved = True
            break
        if state.fix_attempt >= cfg.max_fix_attempts:
            break
        state.review_notes = review.get("notes") or []
        log(f"REVIEW: changes ({len(state.review_notes)} uwag) — runda poprawek "
            f"{state.fix_attempt + 1}/{cfg.max_fix_attempts}")
        state.phase = "fix"
        save_checkpoint(project, state)

    if approved:
        state.last_done = title
        state.iteration = n
        clear_checkpoint(state)
        save_checkpoint(project, state)
        commit_all(project, f"feat: {title}", cfg)
    else:
        reason = ("testy czerwone" if not state.tests_green
                  else "review nie zaakceptował po limicie poprawek")
        log(f"NIEPOWODZENIE zadania '{title}': {reason}")
        record_failure(project, cfg, state, title, reason)
        rollback(project)
        state.iteration = n
        clear_checkpoint(state)
        save_checkpoint(project, state)

    return True


# =====================================================================
# NOWY MODEL: mikro-TDD ping-pong (plan wsadowy → tester ↔ koder → recenzja).
# =====================================================================

# Ścieżki, które KAŻDA rola może dotknąć bez naruszenia podziału (dokumentacja,
# backlog, metadane orkiestratora). Reszta jest pilnowana per rola.
_SHARED_WRITABLE = ["docs/**", "BACKLOG.md", "BACKLOG-ARCHIVE.md", ".forge/**"]


# --- Czyste bramki (testowalne bez agentów) ---------------------------------

def _path_matches(path: str, glob: str) -> bool:
    """Dopasuj ścieżkę do globa z obsługą ** (przez separatory) i * (w segmencie)."""
    path = path.replace("\\", "/").lstrip("./")
    glob = glob.replace("\\", "/").lstrip("./")
    if not any(ch in glob for ch in "*?["):
        return path == glob or path.startswith(glob.rstrip("/") + "/")
    out, i = [], 0
    while i < len(glob):
        ch = glob[i]
        if ch == "*":
            if glob[i:i + 2] == "**":
                out.append(".*")
                i += 2
                if glob[i:i + 1] == "/":
                    i += 1  # wchłoń ukośnik po **
                continue
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.fullmatch("".join(out), path) is not None


def _match_any(path: str, globs: list[str]) -> bool:
    return any(_path_matches(path, g) for g in globs)


def _looks_like_test(path: str) -> bool:
    """Heurystyka pliku testowego, gdy zadanie nie podało test_globs."""
    low = path.replace("\\", "/").lower()
    base = os.path.basename(low)
    return ("test" in base or "spec" in base or low.startswith("tests/")
            or "/tests/" in low)


def red_gate_ok(suite_green_after_test: bool) -> bool:
    """Bramka czerwona: po dopisaniu testu pakiet MUSI być czerwony (test failuje)."""
    return not suite_green_after_test


def role_paths_ok(changed: list[str], allowed_globs: list[str]) -> tuple[bool, list[str]]:
    """Czy wszystkie zmienione pliki mieszczą się w dozwolonych globach roli.

    Puste globy → nie ma czego egzekwować (brak deklaracji ścieżek)."""
    if not allowed_globs:
        return True, []
    offending = [p for p in changed if not _match_any(p, allowed_globs)]
    return (not offending), offending


def coder_test_violations(changed: list[str], test_globs: list[str],
                          cycle_test_files: list[str], declared: list[str]) -> list[str]:
    """Pliki testowe zmienione przez kodera niedozwolenie (nie z tego cyklu, niezadeklarowane)."""
    allowed = set(cycle_test_files) | {d for d in declared if d}
    is_test = (lambda p: _match_any(p, test_globs)) if test_globs else _looks_like_test
    return [p for p in changed if is_test(p) and p not in allowed]


def criteria_fully_mapped(criteria: list[str], criteria_map: list[dict]) -> bool:
    """DONE tylko gdy każde kryterium ma pokrycie (test) albo jawne uzasadnienie."""
    if not criteria:
        return True
    valid = [m for m in criteria_map if isinstance(m, dict) and m.get("criterion")
             and ((m.get("status") == "covered" and m.get("test"))
                  or m.get("status") == "justified")]
    return len(valid) >= len(criteria)


# --- Git: tag/rollback/diff per zadanie -------------------------------------

def _tag(project: str, tag: str) -> None:
    if tag:
        git(project, "tag", "-f", tag, check=False)


def _delete_tag(project: str, tag: str) -> None:
    if tag:
        git(project, "tag", "-d", tag, check=False)


def _reset_to_tag(project: str, tag: str) -> None:
    if tag:
        git(project, "reset", "--hard", tag, check=False)
    git(project, "clean", "-fd", check=False)


# Artefakty runtime orkiestratora — nigdy nie podlegają kontroli podziału ról
# ani rollbackowi plikowemu (w prod są w .gitignore, ale filtrujemy też jawnie).
_RUNTIME_ARTIFACTS = ("STATE.json", "STATE.json.tmp", "STOP")


def _is_runtime_artifact(path: str) -> bool:
    p = path.replace("\\", "/")
    return p in _RUNTIME_ARTIFACTS or p.startswith(".forge/")


def changed_files(project: str, ref: str = "HEAD") -> list[str]:
    """Pliki zmienione względem ref (śledzone) plus nowe nieśledzone.

    Artefakty runtime (STATE.json, STOP, .forge/) są pomijane — nie są częścią
    pracy agenta, więc nie mogą naruszać podziału ról ani być wycofane."""
    tracked = git(project, "diff", "--name-only", ref, check=False).stdout.splitlines()
    untracked = git(project, "ls-files", "--others", "--exclude-standard",
                    check=False).stdout.splitlines()
    return sorted({ln.strip() for ln in (tracked + untracked)
                   if ln.strip() and not _is_runtime_artifact(ln.strip())})


def revert_paths(project: str, paths: list[str]) -> None:
    """Wycofaj wskazane pliki: śledzone → checkout, nieśledzone → usuń."""
    for p in paths:
        if git(project, "checkout", "--", p, check=False).returncode != 0:
            try:
                os.remove(os.path.join(project, p))
            except OSError:
                pass


def _commit_cycle(project: str, msg: str) -> bool:
    """Commit lokalny (BEZ push) — push całego zadania następuje dopiero po ukończeniu."""
    git(project, "add", "-A")
    if git(project, "diff", "--cached", "--quiet", check=False).returncode != 0:
        git(project, "commit", "-q", "-m", msg)
        log(f"Commit: {msg}")
        return True
    return False


def run_gate(project: str, build_cmd: str, test_cmd: str, timeout: int) -> tuple[bool, str]:
    """Bramka build+test zwracająca (zielona?, ogon wyjścia przy czerwieni)."""
    if build_cmd:
        try:
            argv = shlex.split(build_cmd)
        except ValueError as exc:
            return False, f"build: niepoprawna składnia ({exc})"
        if argv:
            try:
                proc = subprocess.run(argv, cwd=project, shell=False, text=True,
                                      capture_output=True, timeout=timeout)
            except (OSError, subprocess.TimeoutExpired) as exc:
                return False, f"build: nie uruchomiono ({exc})"
            if proc.returncode != 0:
                return False, ((proc.stdout or "") + (proc.stderr or ""))[-1500:]
    if not test_cmd:
        return False, "brak test_cmd"
    try:
        argv = shlex.split(test_cmd)
    except ValueError as exc:
        return False, f"test: niepoprawna składnia ({exc})"
    if not argv:
        return False, "pusta komenda testowa"
    try:
        proc = subprocess.run(argv, cwd=project, shell=False, text=True,
                              capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "test: TIMEOUT"
    except OSError as exc:
        return False, f"test: nie uruchomiono ({exc})"
    green = proc.returncode == 0
    return green, "" if green else ((proc.stdout or "") + (proc.stderr or ""))[-1500:]


# --- Dziennik zadania i odzyskiwanie sesji ----------------------------------
# Ciągły kontekst per zadanie żyje w sesjach Codeksa (~/.codex/sessions). Gdy
# sesji nie da się wznowić (świeży kontener, sprzątnięte sesje), fallbackiem
# jest dziennik zadania: zwięzły zapis przebiegu, doklejany do promptu świeżej
# sesji zamiast utraconego kontekstu.

_SESSION_LOSS_RE = re.compile(
    r"(session|thread|conversation|rollout)[^\n]{0,80}"
    r"(not found|no such|does not exist|doesn't exist|missing|unknown|"
    r"failed to (load|read|resume))",
    re.IGNORECASE)


def _looks_like_session_loss(text: str) -> bool:
    return bool(_SESSION_LOSS_RE.search(text or ""))


def _journal_path(project: str, cfg: Config) -> str:
    return os.path.join(project, cfg.runtime_dir, "task_journal.md")


def journal_reset(project: str, cfg: Config, title: str) -> None:
    path = _journal_path(project, cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Dziennik zadania: {title}\n")


def journal_append(project: str, cfg: Config, text: str) -> None:
    try:
        path = _journal_path(project, cfg)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"- [{ts()}] {text}\n")
    except OSError:
        pass  # dziennik jest best-effort


def journal_tail(project: str, cfg: Config, max_chars: int = 3000) -> str:
    try:
        with open(_journal_path(project, cfg), "r", encoding="utf-8") as f:
            return f.read()[-max_chars:]
    except OSError:
        return ""


def _session_call(cfg: Config, project: str, state: State, role: str,
                  prompt: str, log_path: str) -> str:
    """Wywołaj rolę Codeksa z jej sesją; po utracie sesji — świeża + dziennik.

    role: "tester" | "coder". Aktualizuje id sesji w stanie."""
    attr = "tester_session" if role == "tester" else "coder_session"
    model, effort = cfg.tester() if role == "tester" else cfg.coder()
    sid = getattr(state, attr) or None
    try:
        out, new_sid = run_codex_session(prompt, cfg, project, log_path,
                                         session_id=sid, model=model, effort=effort)
    except AgentError as exc:
        if not (sid and _looks_like_session_loss(str(exc))):
            raise
        log(f"Sesja roli '{role}' nieodtwarzalna — świeża sesja z dziennikiem zadania.")
        setattr(state, attr, "")
        save_checkpoint(project, state)
        tail = journal_tail(project, cfg)
        preamble = ("KONTEKST ODTWORZONY Z DZIENNIKA (Twoja poprzednia sesja przepadła; "
                    "to skrót dotychczasowego przebiegu zadania):\n"
                    f"{tail}\n--- KONIEC DZIENNIKA ---\n\n") if tail else ""
        out, new_sid = run_codex_session(preamble + prompt, cfg, project, log_path,
                                         session_id=None, model=model, effort=effort)
    if new_sid:
        setattr(state, attr, new_sid)
    return out


def _cycle_snapshot_dir(project: str, cfg: Config) -> str:
    return os.path.join(project, cfg.runtime_dir, "cycle_tests")


def snapshot_cycle_tests(project: str, cfg: Config, files: list[str]) -> None:
    """Zachowaj wersje testów testera z chwili bramki czerwonej.

    Nowe testy cyklu nie mają jeszcze wersji w HEAD, więc to jedyny sposób, by
    po niedozwolonej edycji kodera przywrócić TEST, a nie skasować go."""
    root = _cycle_snapshot_dir(project, cfg)
    shutil.rmtree(root, ignore_errors=True)
    for rel in files:
        src = os.path.join(project, rel)
        if not os.path.exists(src):
            continue
        dst = os.path.join(root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)


def restore_test_changes(project: str, cfg: Config, files: list[str],
                         cycle_files: list[str]) -> None:
    """Cofnij zmiany kodera w testach: pliki cyklu → ze snapshotu testera,
    pozostałe → z HEAD (checkout; nowe pliki kodera są usuwane)."""
    snapshot = set(cycle_files)
    root = _cycle_snapshot_dir(project, cfg)
    for rel in files:
        if rel in snapshot:
            saved = os.path.join(root, rel)
            if os.path.exists(saved):
                dst = os.path.join(project, rel)
                os.makedirs(os.path.dirname(dst) or project, exist_ok=True)
                shutil.copy2(saved, dst)
                continue
        revert_paths(project, [rel])


def anti_weakening_ok(project: str, test_files: list[str], build_cmd: str,
                      test_cmd: str, timeout: int) -> bool:
    """Bramka anty-osłabiania: testy cyklu w bieżącej postaci MUSZĄ failować
    na kodzie sprzed cyklu (HEAD).

    Koder zadeklarował zmiany w testach — sprawdzamy mechanicznie, czy po tych
    zmianach testy nadal cokolwiek specyfikują: kopiujemy ich bieżące wersje do
    tymczasowego worktree na HEAD (kod bez pracy kodera) i odpalamy bramkę.
    Zielona = testy przechodzą bez implementacji = zostały rozwodnione → False.
    Brak plików / worktree niedostępny → True (nie blokuj — rozstrzygnie recenzja)."""
    test_files = [p for p in test_files if p]
    if not test_files:
        return True
    tmp = tempfile.mkdtemp(prefix="forge-antiweak-")
    wt = os.path.join(tmp, "wt")
    try:
        if git(project, "worktree", "add", "--detach", wt, "HEAD",
               check=False).returncode != 0:
            return True
        copied = False
        for rel in test_files:
            src = os.path.join(project, rel)
            if not os.path.exists(src):
                continue
            dst = os.path.join(wt, rel)
            os.makedirs(os.path.dirname(dst) or wt, exist_ok=True)
            shutil.copy2(src, dst)
            copied = True
        if not copied:
            return True
        green, _ = run_gate(wt, build_cmd, test_cmd, timeout)
        return not green
    finally:
        git(project, "worktree", "remove", "--force", wt, check=False)
        shutil.rmtree(tmp, ignore_errors=True)


# --- Fazy nowego modelu ------------------------------------------------------

def _next_task_index(project: str) -> int:
    """Kolejny numer zadania na podstawie istniejących .forge/tasks/task-*.md."""
    tasks_dir = os.path.join(project, ".forge", "tasks")
    top = 0
    if os.path.isdir(tasks_dir):
        for name in os.listdir(tasks_dir):
            m = re.match(r"task-(\d+)\.md$", name)
            if m:
                top = max(top, int(m.group(1)))
    return top + 1


def phase_plan_batch(cfg: Config, project: str, state: State, logf) -> dict:
    log(f"--- PLAN WSADOWY ({cfg.planner_agent}) ---")
    start = _next_task_index(project)
    out = run_planner(prompts.plan_batch_prompt(cfg.batch_size, start), cfg, project, logf("plan"))
    commit_all(project, "docs: plan wsadowy i backlog", cfg)  # pliki zadań, docs, backlog
    data = extract_json(out) or {}
    tasks = []
    for t in (data.get("tasks") or []):
        rel = t.get("file", "")
        if rel and os.path.exists(os.path.join(project, rel)):
            tasks.append({
                "id": t.get("id") or os.path.splitext(os.path.basename(rel))[0],
                "title": t.get("title", "(zadanie)"), "file": rel,
                "criteria": t.get("criteria") or [],
                "test_globs": t.get("test_globs") or [],
                "code_globs": t.get("code_globs") or [],
            })
        else:
            log(f"PLAN: pomijam zadanie bez pliku na dysku: {t.get('id') or rel!r}")
    state.task_queue = tasks
    log(f"PLAN: kolejka {len(tasks)} zadań.")
    return {"no_more_tasks": bool(data.get("no_more_tasks")) and not tasks}


def _write_current_task_pointer(project: str, task: dict) -> None:
    """Skopiuj plik bieżącego zadania do .forge/current_task.md (zgodność z SHARED_PRINCIPLES)."""
    src = os.path.join(project, task.get("file", ""))
    dst = os.path.join(project, ".forge", "current_task.md")
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(src, "r", encoding="utf-8") as f:
            body = f.read()
        with open(dst, "w", encoding="utf-8") as f:
            f.write(body)
    except OSError:
        pass


def _start_task(cfg: Config, project: str, state: State) -> None:
    task = state.task_queue.pop(0)
    state.current_task = task
    state.current_task_title = task.get("title", "(zadanie)")
    state.task_start_tag = f"forge/{task.get('id', 'task')}-start"
    _tag(project, state.task_start_tag)
    state.tester_session = ""
    state.coder_session = ""
    state.micro_cycle = 0
    state.micro_sub = "test"
    state.cycle_test_files = []
    state.pending_no_test = False
    state.no_test_count = 0
    state.review_notes = []
    state.fix_attempt = 0
    state.phase = "micro"
    _write_current_task_pointer(project, task)
    journal_reset(project, cfg, state.current_task_title)
    log(f"START zadania: {state.current_task_title} (tag {state.task_start_tag})")


def _clear_task(state: State) -> None:
    state.phase = "idle"
    state.current_task = {}
    state.current_task_title = ""
    state.tester_session = ""
    state.coder_session = ""
    state.micro_cycle = 0
    state.micro_sub = "test"
    state.cycle_test_files = []
    state.pending_no_test = False
    state.no_test_count = 0
    state.task_start_tag = ""
    state.review_notes = []
    state.fix_attempt = 0
    state.tests_green = False


def _run_micro_loop(cfg: Config, project: str, state: State, logf) -> bool:
    """Pętla b/c: tester dyktuje jeden test, koder zazielenia i refaktoryzuje.

    Zwraca True gdy tester orzekł DONE (faza→review), False gdy zadanie padło."""
    task = state.current_task
    task_file = task.get("file", "")
    test_globs = task.get("test_globs") or []

    while True:
        if state.micro_cycle >= cfg.max_micro_cycles:
            log(f"Limit mikro-cykli ({cfg.max_micro_cycles}) — zadanie nieukończone.")
            return False
        c = state.micro_cycle + 1

        if state.micro_sub == "test":
            log(f"[cykl {c}] TESTER pisze test / ocenia ukończenie")
            out = _session_call(cfg, project, state, "tester",
                                prompts.write_test_prompt(task_file, state.test_cmd),
                                logf(f"c{c:02d}-test"))
            verdict = extract_json(out) or {"action": "no_test", "reason": "brak werdyktu JSON"}
            action = verdict.get("action")
            journal_append(project, cfg,
                           f"cykl {c}, tester: {action} "
                           f"({verdict.get('about') or verdict.get('reason') or ''})".rstrip())

            if action == "done":
                if not criteria_fully_mapped(task.get("criteria") or [],
                                             verdict.get("criteria_map") or []):
                    log("DONE odrzucony: niekompletna mapa kryterium→test — kolejny cykl.")
                    state.micro_cycle = c  # zużyj cykl, by nie zapętlić w nieskończoność
                    state.micro_sub = "test"
                    save_checkpoint(project, state)
                    continue
                green, _ = run_gate(project, state.build_cmd, state.test_cmd, cfg.agent_timeout_s)
                if not green:
                    log("DONE zgłoszony przy CZERWONEJ bramce — naprawa kodem.")
                    state.pending_no_test = True
                    state.cycle_test_files = []
                    state.micro_sub = "code"
                    save_checkpoint(project, state)
                    continue
                log("TESTER: DONE — kryteria pokryte, bramka zielona.")
                state.phase = "review"
                save_checkpoint(project, state)
                return True

            if action == "no_test":
                state.no_test_count += 1
                if state.no_test_count > max(2, cfg.max_micro_cycles // 3):
                    log(f"UWAGA: dużo 'no_test' ({state.no_test_count}) — możliwy smell testera.")
                log(f"[cykl {c}] TESTER: brak sensownego testu — krok bez testu.")
                state.pending_no_test = True
                state.cycle_test_files = []
                state.micro_sub = "code"
                save_checkpoint(project, state)
                continue

            # action == "wrote_test"
            changed = changed_files(project, "HEAD")
            ok, offending = role_paths_ok(changed, test_globs + _SHARED_WRITABLE)
            if not ok:
                log(f"TESTER poza ścieżkami testów: {offending} — wycofuję.")
                revert_paths(project, offending)
                changed = changed_files(project, "HEAD")
            green, _ = run_gate(project, state.build_cmd, state.test_cmd, cfg.agent_timeout_s)
            if red_gate_ok(green):
                tests_here = [p for p in changed if _match_any(p, test_globs)] if test_globs \
                    else [p for p in changed if _looks_like_test(p)]
                state.cycle_test_files = tests_here
                snapshot_cycle_tests(project, cfg, tests_here)  # do przywracania po koderze
                state.pending_no_test = False
                state.micro_sub = "code"
                save_checkpoint(project, state)
                continue
            log("Bramka NIE zczerwieniała: nowy test przechodzi od razu → odrzucam.")
            tests_here = [p for p in changed if _match_any(p, test_globs)] if test_globs \
                else [p for p in changed if _looks_like_test(p)]
            revert_paths(project, tests_here)
            state.micro_cycle = c  # zużyj cykl (bounded retry)
            state.micro_sub = "test"
            save_checkpoint(project, state)
            continue

        # state.micro_sub == "code"
        no_test = state.pending_no_test
        log(f"[cykl {c}] KODER {'(krok bez testu)' if no_test else 'zazielenia test'} + refaktor")
        green, tail = False, ""
        verdict: dict = {}
        for attempt in range(cfg.max_green_retries + 1):
            out = _session_call(cfg, project, state, "coder",
                                prompts.code_and_refactor_prompt(
                                    task_file, state.test_cmd, no_test, tail),
                                logf(f"c{c:02d}-code{attempt}"))
            verdict = extract_json(out) or {}
            green, tail = run_gate(project, state.build_cmd, state.test_cmd, cfg.agent_timeout_s)
            if green:
                break
            log(f"[cykl {c}] bramka CZERWONA (próba {attempt + 1}/{cfg.max_green_retries + 1})")
        journal_append(project, cfg,
                       f"cykl {c}, koder: {'zielony' if green else 'czerwony'} "
                       f"({verdict.get('notes') or ''})".rstrip())
        if not green:
            log("Koder nie zazielenił bramki w limicie prób — porażka zadania.")
            return False

        declared = [tc.get("file", "") for tc in (verdict.get("test_changes") or [])
                    if isinstance(tc, dict)]
        changed = changed_files(project, "HEAD")
        violations = coder_test_violations(changed, test_globs,
                                           state.cycle_test_files, declared)
        if violations:
            log(f"KODER zmienił testy niezadeklarowanie: {violations} — wycofuję, ponawiam bramkę.")
            revert_paths(project, violations)
            green, _ = run_gate(project, state.build_cmd, state.test_cmd, cfg.agent_timeout_s)
            if not green:
                log("Po wycofaniu niedozwolonych zmian testów bramka czerwona — porażka.")
                return False

        if declared and not anti_weakening_ok(project, declared, state.build_cmd,
                                              state.test_cmd, cfg.agent_timeout_s):
            log(f"ANTY-OSŁABIANIE: zadeklarowane testy {declared} przechodzą na kodzie "
                "sprzed cyklu — zmiana je rozwodniła. Przywracam wersje testów.")
            restore_test_changes(project, cfg, declared, state.cycle_test_files)
            green, _ = run_gate(project, state.build_cmd, state.test_cmd, cfg.agent_timeout_s)
            if not green:
                log("Po przywróceniu testów bramka czerwona — porażka zadania.")
                return False

        _commit_cycle(project, f"tdd: {state.current_task_title} (cykl {c})")
        state.micro_cycle = c
        state.micro_sub = "test"
        state.cycle_test_files = []
        state.pending_no_test = False
        save_checkpoint(project, state)


def _run_review_loop(cfg: Config, project: str, state: State, logf) -> bool:
    """Faza d): recenzja całości przez Codeksa-testera + poprawki kodera.

    Zwraca True gdy approve, False gdy limit poprawek / trwała czerwień."""
    task = state.current_task
    task_file = task.get("file", "")

    while True:
        if state.phase == "fix_review":
            log(f"--- POPRAWKI PO RECENZJI (koder) runda {state.fix_attempt + 1} ---")
            out = _session_call(cfg, project, state, "coder",
                                prompts.fix_review_prompt(state.review_notes, state.test_cmd),
                                logf(f"review-fix{state.fix_attempt + 1}"))
            green, _ = run_gate(project, state.build_cmd, state.test_cmd, cfg.agent_timeout_s)
            if green:
                _commit_cycle(project, f"fix: {state.current_task_title} (recenzja {state.fix_attempt + 1})")
            elif state.fix_attempt + 1 >= cfg.max_fix_attempts:
                log("Poprawki recenzji nie zazieleniły bramki w limicie — porażka.")
                return False
            state.review_notes = []
            state.fix_attempt += 1
            state.phase = "review"
            save_checkpoint(project, state)

        green, _ = run_gate(project, state.build_cmd, state.test_cmd, cfg.agent_timeout_s)
        if not green:
            if state.fix_attempt >= cfg.max_fix_attempts:
                return False
            state.review_notes = ["Bramka testów czerwona — przywróć zieleń."]
            state.phase = "fix_review"
            save_checkpoint(project, state)
            continue

        log("--- RECENZJA CAŁOŚCI (Codex-tester) ---")
        out = _session_call(cfg, project, state, "tester",
                            prompts.review_task_prompt(task_file, state.test_cmd),
                            logf(f"review-r{state.fix_attempt}"))
        review = extract_json(out) or {"verdict": "changes", "notes": ["Brak werdyktu JSON."]}
        journal_append(project, cfg,
                       f"recenzja: {review.get('verdict')} ({len(review.get('notes') or [])} uwag)")
        if review.get("verdict") == "approve":
            log("RECENZJA: approve.")
            return True
        if state.fix_attempt >= cfg.max_fix_attempts:
            log("RECENZJA: changes, ale limit poprawek wyczerpany — porażka.")
            return False
        state.review_notes = review.get("notes") or []
        log(f"RECENZJA: changes ({len(state.review_notes)} uwag) → runda {state.fix_attempt + 1}")
        state.phase = "fix_review"
        save_checkpoint(project, state)


def _finish_task(cfg: Config, project: str, state: State, n: int) -> None:
    _commit_cycle(project, f"feat: {state.current_task_title}")  # residuum (np. docs)
    if cfg.git_push:
        push(project, cfg)  # pojedynczy push całego, zielonego zadania
    _delete_tag(project, state.task_start_tag)
    state.last_done = state.current_task_title
    state.iteration = n
    log(f"ZADANIE UKOŃCZONE: {state.last_done} 🎉")
    _clear_task(state)
    save_checkpoint(project, state)


def _fail_task(cfg: Config, project: str, state: State, n: int, reason: str) -> None:
    title = state.current_task_title or "(zadanie)"
    detail = reason
    if state.review_notes:
        detail += " | uwagi: " + "; ".join(state.review_notes[:5])
    log(f"NIEPOWODZENIE zadania '{title}': {detail}")
    record_failure(project, cfg, state, title, detail)
    _reset_to_tag(project, state.task_start_tag)  # lokalnie — nic nie było pushowane
    _delete_tag(project, state.task_start_tag)
    state.iteration = n
    _clear_task(state)
    save_checkpoint(project, state)


def _task_iteration(cfg: Config, project: str, state: State) -> bool:
    """Jedno zadanie nowego modelu (plan wsadowy → mikro-TDD → recenzja). Wznawialne."""
    n = state.iteration + 1
    log(f"########## ZADANIE (iter {n}) ##########")

    def logf(phase: str) -> str:
        return os.path.join(project, cfg.runtime_dir, "logs", f"task-{n:04d}-{phase}.log")

    if state.phase != "idle" and not state.current_task:
        log("Faza zaawansowana bez bieżącego zadania — reset do planowania.")
        _clear_task(state)

    if state.phase == "idle":
        if not state.task_queue:
            plan = phase_plan_batch(cfg, project, state, logf)
            save_checkpoint(project, state)
            if not state.task_queue:
                log("PLAN: brak dalszych zadań — MVP ukończone. 🎉" if plan.get("no_more_tasks")
                    else "PLAN: planista nie zwrócił zadań — zatrzymuję.")
                return False
        _start_task(cfg, project, state)
        save_checkpoint(project, state)
    else:
        log(f"WZNAWIAM fazę '{state.phase}': {state.current_task_title}")

    if state.phase == "micro":
        if not _run_micro_loop(cfg, project, state, logf):
            _fail_task(cfg, project, state, n, f"mikro-TDD nieukończone (cykli={state.micro_cycle})")
            return True

    if state.phase in {"review", "fix_review"}:
        if _run_review_loop(cfg, project, state, logf):
            _finish_task(cfg, project, state, n)
        else:
            _fail_task(cfg, project, state, n, "recenzja nie zaakceptowała / bramka czerwona")
    return True


def _one_iteration(cfg: Config, project: str, state: State) -> bool:
    """Dyspozytor: nowy model (mikro-TDD) albo stary przebieg za flagą legacy_mode."""
    if cfg.legacy_mode:
        return _legacy_iteration(cfg, project, state)
    return _task_iteration(cfg, project, state)


def one_iteration(cfg: Config, project: str, state: State) -> bool:
    """Wykonaj iterację transakcyjnie względem ostatniego dobrego commita."""
    try:
        return _one_iteration(cfg, project, state)
    except (AgentError, LimitExhausted, KeyboardInterrupt):
        log(f"Checkpoint zachowany: faza '{state.phase}'.")
        save_checkpoint(project, state)
        raise
    except subprocess.CalledProcessError:
        rollback(project)
        clear_checkpoint(state)
        save_checkpoint(project, state)
        raise


def _print_usage(project: str) -> None:
    """Podsumowanie zużycia tokenów na koniec biegu (best-effort)."""
    try:
        from .report import usage_summary
        print("\nZużycie tokenów w tym projekcie:\n" + usage_summary(project))
    except Exception:  # raport nigdy nie psuje kodu wyjścia pętli
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Orkiestrator agentów budujących grę.")
    ap.add_argument("--brief", default="game.md", help="Plik z opisem gry.")
    ap.add_argument("--project", default="game", help="Katalog projektu gry.")
    ap.add_argument("--max-iters", type=int, default=None, help="Limit iteracji (0=bez).")
    ap.add_argument("--sleep", type=parse_start_delay, default=0.0, metavar="CZAS",
                    help="Opóźnij start, np. 30, 30s, 5m albo 2h.")
    ap.add_argument("--planner-agent", choices=PLANNER_AGENTS, default=None)
    ap.add_argument("--planner-model", "--claude-model", dest="planner_model", default=None)
    ap.add_argument("--planner-effort", "--claude-effort", dest="planner_effort", default=None)
    ap.add_argument("--codex-model", default=None)
    ap.add_argument("--codex-effort", default=None, choices=CODEX_EFFORTS)
    ap.add_argument("--legacy", action="store_true", default=None,
                    help="Stary przebieg plan→implement→review(Claude)→fix.")
    ap.add_argument("--batch-size", type=int, default=None,
                    help="Ile zadań planista przygotowuje jednym wywołaniem (nowy model).")
    ap.add_argument("--max-micro-cycles", type=int, default=None,
                    help="Sufit mikro-cykli TDD na zadanie (nowy model).")
    ap.add_argument("--tester-model", default=None, help="Model Codeksa-testera (nowy model).")
    ap.add_argument("--tester-effort", default=None, choices=CODEX_EFFORTS)
    ap.add_argument("--coder-model", default=None, help="Model Codeksa-kodera (nowy model).")
    ap.add_argument("--coder-effort", default=None, choices=CODEX_EFFORTS)
    ap.add_argument("--non-interactive", action="store_true",
                    help="Nie pytaj o modele i effort; użyj flag/env/dom wartości.")
    ap.add_argument("--check", action="store_true", help="Tylko preflight i wyjście.")
    args = ap.parse_args(argv)

    cfg = Config()
    cfg.brief_path = args.brief
    cfg.project_dir = args.project
    if args.max_iters is not None:
        cfg.max_iterations = args.max_iters
    if args.planner_agent and args.planner_agent != cfg.planner_agent:
        cfg.planner_agent = args.planner_agent
        if not args.planner_model:
            cfg.planner_model = "opus" if args.planner_agent == "claude" else cfg.codex_model
        if not args.planner_effort:
            cfg.planner_effort = "high" if args.planner_agent == "claude" else cfg.codex_effort
    if args.planner_model:
        cfg.planner_model = args.planner_model
    if args.planner_effort:
        cfg.planner_effort = args.planner_effort
    if args.codex_model:
        cfg.codex_model = args.codex_model
    if args.codex_effort:
        cfg.codex_effort = args.codex_effort
    if args.legacy:
        cfg.legacy_mode = True
    if args.batch_size is not None:
        cfg.batch_size = args.batch_size
    if args.max_micro_cycles is not None:
        cfg.max_micro_cycles = args.max_micro_cycles
    if args.tester_model:
        cfg.tester_model = args.tester_model
    if args.tester_effort:
        cfg.tester_effort = args.tester_effort
    if args.coder_model:
        cfg.coder_model = args.coder_model
    if args.coder_effort:
        cfg.coder_effort = args.coder_effort

    allowed_planner_efforts = (CLAUDE_EFFORTS if cfg.planner_agent == "claude"
                               else CODEX_EFFORTS)
    if cfg.planner_effort not in allowed_planner_efforts:
        ap.error(f"effort {cfg.planner_effort!r} nie jest obsługiwany przez {cfg.planner_agent}")

    if not args.check and not args.non_interactive:
        if sys.stdin.isatty():
            prompt_agent_settings(cfg)
        else:
            log("Brak interaktywnego terminala — używam konfiguracji z flag/env/defaultów.")

    problems = preflight(cfg)
    if problems:
        log("PREFLIGHT — problemy:")
        for p in problems:
            print("  ✗ " + p)
        if args.check or any("git" in p or "Codex" in p or "briefu" in p for p in problems):
            return 2
        log("Kontynuuję mimo ostrzeżeń (ustaw FORGE_CLAUDE_BIN, jeśli Claude nie ruszy).")
    else:
        log("PREFLIGHT OK.")
    if cfg.legacy_mode:
        log("TRYB: legacy (plan → implement → review[Claude] → fix).")
    else:
        log(f"TRYB: mikro-TDD (plan wsadowy {cfg.batch_size} → tester↔koder → recenzja Codeksa); "
            f"sufit mikro-cykli={cfg.max_micro_cycles}.")
    if args.check:
        return 0

    try:
        wait_before_start(args.sleep)
    except KeyboardInterrupt:
        log("Przerwano oczekiwanie przed startem.")
        return 130

    project = os.path.abspath(cfg.project_dir)
    os.makedirs(project, exist_ok=True)
    ensure_repo(project)

    state_path = os.path.join(project, "STATE.json")
    state = State.load(state_path)

    try:
        if not state.bootstrapped:
            phase_bootstrap(cfg, project, state, lambda ph: os.path.join(project, cfg.runtime_dir, "logs", f"iter-0000-{ph}.log"))
            state.save(state_path)

        while True:
            if os.path.exists(os.path.join(project, cfg.stop_file)):
                log("Wykryto plik STOP — zatrzymuję grzecznie.")
                break
            if cfg.max_iterations and state.iteration >= cfg.max_iterations:
                log(f"Osiągnięto limit iteracji ({cfg.max_iterations}).")
                break
            cont = one_iteration(cfg, project, state)
            state.save(state_path)
            if not cont:
                break

    except LimitExhausted as e:
        log(f"LIMITY WYCZERPANE: {e}")
        log("Stan zapisany — uruchom ponownie później, by kontynuować.")
        state.save(state_path)
        _print_usage(project)
        return 3
    except KeyboardInterrupt:
        log("Przerwano ręcznie. Stan zapisany.")
        state.save(state_path)
        return 130
    except AgentError as e:
        log(f"BŁĄD AGENTA: {e}")
        state.save(state_path)
        return 1

    state.save(state_path)
    log("Koniec pracy.")
    _print_usage(project)
    return 0


if __name__ == "__main__":
    sys.exit(main())
