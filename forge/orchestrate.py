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
import shlex
import shutil
import subprocess
import sys

from . import prompts
from .agents import (AgentError, LimitExhausted, extract_json, run_claude,
                     run_codex)
from .config import Config
from .state import State


def ts() -> str:
    return _dt.datetime.now().strftime("%H:%M:%S")


def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", flush=True)


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
    """Wypchnij bieżący branch do remote. Niekrytyczne — błąd (sieć/auth) tylko
    loguje, nie wywala pętli. Wypchnięta historia nigdy nie jest przepisywana
    (rollback dotyka tylko niezacommitowanych zmian)."""
    if not git(project, "remote", check=False).stdout.strip():
        return  # brak remote — nic nie pushujemy
    branch = git(project, "rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip()
    if not branch or branch == "HEAD":
        branch = "main"
    res = git(project, "push", "-u", cfg.git_remote, branch, check=False)
    if res.returncode != 0:
        log(f"PUSH nieudany (niekrytyczne): {(res.stderr or '').strip()[:200]}")
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
    if shutil.which(cfg.claude_bin) is None:
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
    out = run_claude(prompts.bootstrap_prompt(brief), cfg, project, logf("bootstrap"))
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
    log("--- PLAN (Claude) ---")
    current_task = os.path.join(project, cfg.runtime_dir, "current_task.md")
    try:
        os.remove(current_task)
    except FileNotFoundError:
        pass
    out = run_claude(prompts.plan_prompt(), cfg, project, logf("plan"))
    commit_all(project, "docs: aktualizacja planu i backlogu", cfg)  # plan może dotknąć docs/backlog
    return extract_json(out) or {"task_title": "(nieznane)", "no_more_tasks": False}


def phase_implement(cfg: Config, project: str, test_cmd: str, logf) -> dict:
    log("--- IMPLEMENT (Codex, TDD) ---")
    out = run_codex(prompts.implement_prompt(test_cmd), cfg, project, logf("implement"))
    return extract_json(out) or {}


def phase_review(cfg: Config, project: str, test_cmd: str, green: bool, logf) -> dict:
    log("--- REVIEW (Claude) ---")
    out = run_claude(prompts.review_prompt(test_cmd, green), cfg, project, logf("review"))
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

def _one_iteration(cfg: Config, project: str, state: State) -> bool:
    """Jedna iteracja. Zwraca False gdy nie ma już zadań (koniec MVP)."""
    n = state.iteration + 1
    log(f"########## ITERACJA {n} ##########")

    def logf(phase: str) -> str:
        return os.path.join(project, cfg.runtime_dir, "logs", f"iter-{n:04d}-{phase}.log")

    plan = phase_plan(cfg, project, logf)
    if plan.get("no_more_tasks"):
        log("PLAN: brak dalszych zadań — MVP ukończone. 🎉")
        return False
    title = plan.get("task_title", "(zadanie)")
    log(f"Zadanie: {title}")

    phase_implement(cfg, project, state.test_cmd, logf)
    green = build_then_test(project, state.build_cmd, state.test_cmd, cfg.agent_timeout_s)

    approved = False
    for attempt in range(cfg.max_fix_attempts + 1):
        review = phase_review(cfg, project, state.test_cmd, green, logf)
        if review.get("verdict") == "approve" and green:
            approved = True
            break
        if attempt >= cfg.max_fix_attempts:
            break
        notes = review.get("notes") or []
        log(f"REVIEW: changes ({len(notes)} uwag) — runda poprawek {attempt + 1}/{cfg.max_fix_attempts}")
        phase_fix(cfg, project, notes, state.test_cmd, logf)
        green = build_then_test(project, state.build_cmd, state.test_cmd, cfg.agent_timeout_s)

    if approved:
        commit_all(project, f"feat: {title}", cfg)
        state.last_done = title
    else:
        reason = "testy czerwone" if not green else "review nie zaakceptował po limicie poprawek"
        log(f"NIEPOWODZENIE zadania '{title}': {reason}")
        record_failure(project, cfg, state, title, reason)
        rollback(project)

    state.iteration = n
    return True


def one_iteration(cfg: Config, project: str, state: State) -> bool:
    """Wykonaj iterację transakcyjnie względem ostatniego dobrego commita."""
    try:
        return _one_iteration(cfg, project, state)
    except (AgentError, LimitExhausted, subprocess.CalledProcessError, KeyboardInterrupt):
        rollback(project)
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Orkiestrator agentów budujących grę.")
    ap.add_argument("--brief", default="game_brief.md", help="Plik z opisem gry.")
    ap.add_argument("--project", default="game", help="Katalog projektu gry.")
    ap.add_argument("--max-iters", type=int, default=None, help="Limit iteracji (0=bez).")
    ap.add_argument("--claude-model", default=None)
    ap.add_argument("--codex-model", default=None)
    ap.add_argument("--check", action="store_true", help="Tylko preflight i wyjście.")
    args = ap.parse_args(argv)

    cfg = Config()
    cfg.brief_path = args.brief
    cfg.project_dir = args.project
    if args.max_iters is not None:
        cfg.max_iterations = args.max_iters
    if args.claude_model:
        cfg.claude_model = args.claude_model
    if args.codex_model:
        cfg.codex_model = args.codex_model

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
    if args.check:
        return 0

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
