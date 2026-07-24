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
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

from . import adapters, prompts, verify, verify_ledger
from .agents import (AgentError, LimitExhausted, agent_supports_resume,
                     extract_json, run_agent, run_agent_session, run_codex,
                     run_planner)
from .config import Config, DEFAULT_TASK_DIFFICULTY, TASK_DIFFICULTIES
from .shellrun import run_shellfree as _run_shellfree
from .state import State


def ts() -> str:
    return _dt.datetime.now().strftime("%H:%M:%S")


def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", flush=True)


CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")
CODEX_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")
# xAI Grok Build CLI: `--effort low|medium|high` (docs.x.ai/build/cli/reference).
GROK_EFFORTS = ("low", "medium", "high")
KNOWN_AGENT_EFFORTS = {"claude": CLAUDE_EFFORTS, "codex": CODEX_EFFORTS, "grok": GROK_EFFORTS}
# Podpowiedzi modeli — TYLKO dla wygody (unikanie literówek przy ręcznym wpisywaniu,
# np. "fable" zamiast poprawnego aliasu Claude Code). Lista wybieralna numerem, ale
# każde inne wpisane hasło jest akceptowane bez walidacji — modele wychodzą
# częściej niż zdążymy zaktualizować tę listę, więc to podpowiedź, nie ograniczenie.
KNOWN_AGENT_MODELS: dict[str, tuple[str, ...]] = {
    # Aliasy Claude Code CLI — zawsze wskazują na najnowszy model danej rodziny.
    "claude": ("opus", "sonnet", "haiku", "fable"),
    # xAI Grok Build (docs.x.ai/developers/models, stan: 2026-07) — sprawdź
    # `grok models list` jeśli lista zdążyła się zmienić.
    "grok": ("grok-4.5", "grok-4.3", "grok-build-0.1"),
    # OpenCode CLI → NeuralWatt (api.neuralwatt.com/v1, stan: 2026-07) —
    # sprawdź `opencode models neuralwatt`, jeśli katalog dostawcy się zmienił.
    "opencode": (
        "neuralwatt/glm-5.2", "neuralwatt/glm-5.2-fast", "neuralwatt/glm-5.2-flex",
        "neuralwatt/glm-5.2-short", "neuralwatt/glm-5.2-short-fast",
        "neuralwatt/glm-5.2-short-flex", "neuralwatt/glm-5.2-short-fast-flex",
        "neuralwatt/kimi-k2.6", "neuralwatt/kimi-k2.6-fast", "neuralwatt/kimi-k2.6-flex",
        "neuralwatt/kimi-k2.7-code", "neuralwatt/kimi-k2.7-code-flex",
        "neuralwatt/qwen3.6-35b", "neuralwatt/qwen3.6-35b-fast",
        "neuralwatt/qwen3.5-397b", "neuralwatt/qwen3.5-397b-fast",
        "neuralwatt/gemma-4-31b",
    ),
}
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


def _ask_model(label: str, default: str, canon: str, *, display_default: str | None = None) -> str:
    """Jak _ask_value, ale dla znanego agenta dopisuje numerowane podpowiedzi
    modeli (KNOWN_AGENT_MODELS). Wpisanie numeru wybiera podpowiedź; dowolny
    inny tekst przechodzi bez zmian — to podpowiedź, nie walidacja."""
    choices = KNOWN_AGENT_MODELS.get(canon)
    if not choices:
        return _ask_value(label, default, display_default=display_default)
    menu = ", ".join(f"{i}={m}" for i, m in enumerate(choices, start=1))
    value = _ask_value(f"{label} — {menu}", default, display_default=display_default)
    if value.isdigit():
        idx = int(value) - 1
        if 0 <= idx < len(choices):
            return choices[idx]
    return value


def _ask_effort(label: str, default: str, allowed: tuple[str, ...]) -> str:
    choices = "/".join(allowed)
    while True:
        value = _ask_value(f"{label} ({choices})", default).lower()
        if value in allowed:
            return value
        print(f"Niepoprawny effort: {value!r}. Wybierz jedną z: {choices}.")


def _ask_role_agent(cfg: Config, agent_attr: str, title: str,
                    *, allow_blank_agent: str = "") -> None:
    """Zapytaj tylko o agenta roli; model/effort dobiera stała macierz."""
    current_agent = getattr(cfg, agent_attr)
    hint = f"claude/codex/gpt/grok/kiro/opencode/inny{', puste = ' + allow_blank_agent if allow_blank_agent else ''}"
    new_agent = _ask_value(f"Agent {title} ({hint})", current_agent,
                          display_default=current_agent or allow_blank_agent or "codex")
    setattr(cfg, agent_attr, new_agent)

def prompt_agent_settings(cfg: Config) -> None:
    """Pobierz wyłącznie agenta każdej roli; routing modelu jest stały."""
    print("\nKonfiguracja agentów (model i effort dobiera profil trudności):")
    cfg.planner_agent = _ask_value("Agent do planowania (claude/codex/gpt/grok/kiro/opencode/inny)",
                                   cfg.planner_agent)

    if cfg.legacy_mode:
        # Tryb legacy: implement/fix zawsze na Codeksie — role tester/koder/
        # recenzent/weryfikator nie istnieją w tym przebiegu, więc Codeks jest
        # zawsze w użyciu i pytamy o niego bez warunku.
        cfg.codex_model = _ask_value(
            "Model do implementacji (Codex)", cfg.codex_model,
            display_default=cfg.codex_model or "z config.toml",
        )
        cfg.codex_effort = _ask_effort("Effort implementacji", cfg.codex_effort, CODEX_EFFORTS)
        print()
        return

    _ask_role_agent(cfg, "tester_agent", "testera")
    _ask_role_agent(cfg, "coder_agent", "kodera")
    _ask_role_agent(cfg, "reviewer_agent", "recenzenta",
                    allow_blank_agent="agent testera")
    _ask_role_agent(cfg, "verifier_agent", "weryfikatora",
                    allow_blank_agent="agent planisty")
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


def rollback(project: str, ref: str = "HEAD") -> None:
    """Wycofaj nieudaną pracę do wskazanego punktu (domyślnie ostatni commit)."""
    git(project, "reset", "--hard", ref, check=False)
    git(project, "clean", "-fd", check=False)  # usuwa nowe pliki (poza .gitignore)
    log(f"ROLLBACK: przywrócono stan '{ref}'.")


# --- Bramka testów -----------------------------------------------------------

def run_tests(project: str, test_cmd: str, timeout: int) -> bool:
    if not test_cmd:
        log("Testy: brak test_cmd → czerwone.")
        return False
    log(f"Bramka testów: {test_cmd}")
    rc, out = _run_shellfree(project, test_cmd, timeout)
    if rc is None:
        log(f"Testy: {out} → czerwone.")
        return False
    green = rc == 0
    log(f"Testy: {'ZIELONE' if green else 'CZERWONE (rc=%d)' % rc}")
    if not green:
        print(out[-1200:])
    return green


def build_then_test(project: str, build_cmd: str, test_cmd: str, timeout: int) -> bool:
    """Bramka: najpierw build (jeśli podany), potem testy. Obie komendy shell-free.

    Dla stacków kompilowanych (np. C++/CMake) bez tego kroku testy nie mają jak
    przejść. Build padnie → bramka czerwona (jak nieudane testy)."""
    if build_cmd:
        log(f"Build: {build_cmd}")
        rc, out = _run_shellfree(project, build_cmd, timeout)
        if rc is None:
            log(f"Build: {out} → czerwony.")
            return False
        if rc != 0:
            log(f"Build: CZERWONY (rc={rc})")
            print(out[-1200:])
            return False
        log("Build: OK")
    return run_tests(project, test_cmd, timeout)


# --- Preflight ---------------------------------------------------------------

def _agent_bin_problem(cfg: Config, name: str) -> str | None:
    """Sprawdź dostępność binarki agenta CLI danej nazwy; None gdy OK."""
    name = adapters.canonical_agent(name)
    if name == "claude":
        if shutil.which(cfg.claude_bin) is None:
            return (f"Nie znaleziono Claude CLI ('{cfg.claude_bin}'). Zainstaluj Claude "
                    "Code jako standalone CLI albo ustaw FORGE_CLAUDE_BIN na pełną ścieżkę.")
        return None
    if name == "codex":
        if shutil.which(cfg.codex_bin) is None:
            return f"Nie znaleziono Codex CLI ('{cfg.codex_bin}')."
        return None
    spec = adapters.generic_spec(name)
    if spec is None:
        return (f"Agent '{name}' nie jest wbudowany, a brak jego szablonu komendy — "
                f"ustaw {adapters.env_key(name)} (patrz README).")
    if shutil.which(adapters.generic_bin(spec)) is None:
        return f"Nie znaleziono binarki agenta '{name}' ('{adapters.generic_bin(spec)}') na PATH."
    return None


def preflight(cfg: Config) -> list[str]:
    problems = []
    if shutil.which("git") is None:
        problems.append("Brak 'git' na PATH.")
    for name in cfg.agents_in_use():
        problem = _agent_bin_problem(cfg, name)
        if problem:
            problems.append(problem)
    if not os.path.exists(cfg.brief_path):
        problems.append(f"Brak pliku briefu: {cfg.brief_path}")
    return problems


# --- Fazy --------------------------------------------------------------------

# Wymagane komendy per target weryfikacji (PLAN-3, sekcja 2). Target
# zadeklarowany bez swoich komend to usterka bootstrapu, nie "jakoś to będzie".
_VERIFY_TARGET_CMDS = {
    "smoke": ("smoke_cmd",),
    "hardware": ("flash_cmd", "target_cmd"),
    "ci": ("ci_status_cmd", "ci_logs_cmd"),
}
_VERIFY_CMD_FIELDS = ("smoke_cmd", "flash_cmd", "target_cmd", "probe_cmd",
                      "ci_status_cmd", "ci_logs_cmd")


def parse_verify_profile(verify, cfg: Config) -> dict:
    """Zwaliduj obiekt "verify" z bootstrapu → pola profilu dla State.

    Brak obiektu = weryfikacja wyłączona (kompatybilność ze starszymi
    promptami/STATE). Nadpisanie użytkownika (FORGE_VERIFY_TARGETS) wygrywa
    z deklaracją bootstrapu."""
    verify = verify if isinstance(verify, dict) else {}
    declared = [str(t).strip() for t in (verify.get("targets") or [])
                if str(t).strip()]
    targets = cfg.effective_verify_targets(declared)
    unknown = [t for t in dict.fromkeys(declared + targets)
               if t not in _VERIFY_TARGET_CMDS]
    if unknown:
        raise AgentError(
            f"Nieznane targety weryfikacji: {', '.join(unknown)} "
            f"(dozwolone: {', '.join(_VERIFY_TARGET_CMDS)}).")
    fields = {key: str(verify.get(key) or "").strip() for key in _VERIFY_CMD_FIELDS}
    missing = [f"{target}: brak {cmd}" for target in targets
               for cmd in _VERIFY_TARGET_CMDS[target] if not fields[cmd]]
    if missing:
        raise AgentError("Profil weryfikacji niekompletny — " + "; ".join(missing) + ".")
    globs = [str(g).strip() for g in (verify.get("verify_test_globs") or [])
             if str(g).strip()]
    return {"verify_targets": targets, "verify_test_globs": globs, **fields}


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
    profile = parse_verify_profile(data.get("verify"), cfg)
    # Deklaracja toolchainu testowego (PLAN-4, Z1) — jak verify_test_globs:
    # opcjonalna, uzupełnia wbudowaną heurystykę plików konfiguracji testów.
    toolchain_globs = [str(g).strip() for g in (data.get("test_toolchain_globs") or [])
                       if str(g).strip()]
    if not build_then_test(project, data["build_cmd"], data["test_cmd"], cfg.agent_timeout_s):
        raise AgentError("Build/testy szkieletu po bootstrapie nie przeszły.")
    for key, value in profile.items():
        setattr(state, key, value)
    state.test_toolchain_globs = toolchain_globs
    state.stack = data.get("stack", "")
    state.test_cmd = data.get("test_cmd", "")
    state.build_cmd = data.get("build_cmd", "")
    state.run_cmd = data.get("run_cmd", "")
    kind = str(data.get("kind", "")).strip().lower()
    state.project_kind = "game" if kind == "game" else "app"
    state.bootstrapped = True
    log(f"Rodzaj: {state.project_kind} | stack: {state.stack or '(nieokreślony)'} "
        f"| test_cmd: {state.test_cmd or '(brak!)'} "
        f"| weryfikacja: {', '.join(state.verify_targets) or '(wyłączona)'}")
    commit_all(project, "chore: bootstrap projektu (design, architektura, backlog, szkielet)", cfg)


def phase_plan(cfg: Config, project: str, state: State, logf) -> dict:
    log(f"--- PLAN ({cfg.planner_agent}) ---")
    current_task = os.path.join(project, cfg.runtime_dir, "current_task.md")
    try:
        os.remove(current_task)
    except FileNotFoundError:
        pass
    out = run_planner(prompts.plan_prompt(state.project_kind), cfg, project, logf("plan"))
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


def _append_line(path: str, line: str) -> None:
    """Best-effort dopisanie linii (makedirs + append). Zapisy diagnostyczne
    nigdy nie wywracają pętli — błąd IO jest połykany świadomie i wszędzie
    tak samo (failures.md, dziennik zadania)."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def record_failure(project: str, cfg: Config, state: State, title: str, reason: str) -> None:
    state.failures.append(f"{title}: {reason}")
    _append_line(os.path.join(project, cfg.runtime_dir, "failures.md"),
                 f"- [{ts()}] {title} — {reason}\n")


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
        plan = phase_plan(cfg, project, state, logf)
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


# Łańcuch narzędzi testowych (PLAN-4, Z1): pliki konfigurujące, CO i JAK
# uruchamia test_cmd. Nie są własnością testera (to nie specyfikacja), a ich
# zmiany przez kodera przechodzą bramkę anty-osłabiania — najtańszym "sposobem
# na zieleń" nie może być wykastrowanie runnera w package.json/Makefile.
_TOOLCHAIN_BASENAMES = frozenset({
    "package.json", "pyproject.toml", "setup.cfg", "pytest.ini", "tox.ini",
    "makefile", "cmakelists.txt", "pom.xml", "cargo.toml", "noxfile.py",
})
_TOOLCHAIN_PREFIXES = ("jest.config.", "vitest.config.", "karma.conf.",
                       "build.gradle")


def _looks_like_toolchain(path: str) -> bool:
    """Heurystyka pliku toolchainu testowego (wbudowana, uzupełniana globami)."""
    base = os.path.basename(path.replace("\\", "/")).lower()
    return (base in _TOOLCHAIN_BASENAMES
            or any(base.startswith(p) for p in _TOOLCHAIN_PREFIXES))


def is_toolchain_path(path: str, extra_globs: list[str]) -> bool:
    """Czy ścieżka to plik toolchainu: heurystyka LUB dodatkowe globy
    (deklaracja bootstrapu + FORGE_TOOLCHAIN_GLOBS)."""
    return _looks_like_toolchain(path) or _match_any(path, extra_globs)


def effective_toolchain_globs(cfg: Config, state: State) -> list[str]:
    """Globy toolchainu spoza heurystyki: deklaracja bootstrapu (State)
    + CSV użytkownika (Config)."""
    extra = [g.strip() for g in cfg.toolchain_globs_extra.split(",") if g.strip()]
    return list(state.test_toolchain_globs) + extra


def _looks_like_test(path: str) -> bool:
    """Heurystyka pliku testowego, gdy zadanie nie podało test_globs.

    Pliki toolchainu są wykluczone jawnie — "pytest.ini" zawiera "test"
    w nazwie, ale to konfiguracja runnera, nie specyfikacja (własność
    testera obejmuje testy i fixture'y typu conftest.py, nie toolchain)."""
    if _looks_like_toolchain(path):
        return False
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


def _is_test_path(path: str, test_globs: list[str]) -> bool:
    """Czy ścieżka jest plikiem testowym zadania: wg globów, a bez globów —
    wg heurystyki nazwy. Jedno miejsce prawdy dla wszystkich bramek."""
    return _match_any(path, test_globs) if test_globs else _looks_like_test(path)


def tester_path_violations(changed: list[str], test_globs: list[str]) -> list[str]:
    """Pliki, których tester nie miał prawa dotknąć (nie-test i nie-współdzielone).

    Gdy zadanie nie deklaruje test_globs, obowiązuje heurystyka _looks_like_test —
    brak globów NIE może oznaczać zakazu pisania testów."""
    return [p for p in changed
            if not (_is_test_path(p, test_globs) or _match_any(p, _SHARED_WRITABLE))]


def coder_test_violations(changed: list[str], test_globs: list[str],
                          cycle_test_files: list[str], declared: list[str]) -> list[str]:
    """Pliki testowe zmienione przez kodera niedozwolenie (nie z tego cyklu, niezadeklarowane)."""
    allowed = set(cycle_test_files) | {d for d in declared if d}
    return [p for p in changed if _is_test_path(p, test_globs) and p not in allowed]


def weakening_candidates(changed: list[str], test_globs: list[str],
                         toolchain_globs: list[str]) -> list[str]:
    """Zbiór wejściowy bramki anty-osłabiania, liczony MECHANICZNIE z diffu
    (nie z deklaracji kodera): zmienione pliki testowe ∪ zmieniony toolchain."""
    return sorted(p for p in changed
                  if _is_test_path(p, test_globs)
                  or is_toolchain_path(p, toolchain_globs))


def _norm_criterion(text: str) -> str:
    """Normalizacja do porównań: LLM-y przekręcają wielkość liter i whitespace."""
    return " ".join((text or "").split()).casefold()


def verify_protected_violations(changed: list[str], protected_globs: list[str],
                                allowed: bool, exempt: set[str] | frozenset = frozenset(),
                                ) -> list[str]:
    """Pliki weryfikacji dotknięte bez uprawnienia (PLAN-3, sekcja 8).

    ``allowed=True`` tylko dla zadania naprawiającego problem klasy
    verify_defect — wtedy edycja chronionych ścieżek jest legalna.
    ``exempt`` to pliki wyjęte spod ochrony (testy bieżącego cyklu i nowe
    testy targetowe) — ochrona blokuje OSŁABIANIE istniejącej weryfikacji,
    nie tworzenie specyfikacji."""
    if allowed:
        return []
    return [p for p in changed if p not in exempt and _match_any(p, protected_globs)]


def _protected_exempt(project: str, state: State) -> set[str]:
    """Wyjątki od ochrony ścieżek weryfikacji: pliki testowe bieżącego
    mikro-cyklu oraz NOWE (nieśledzone) pliki pasujące do verify_test_globs —
    tak się tworzy testy targetowe w zwykłych zadaniach. Konfiguracja CI
    i skrypty profilu chronione są także jako nowe pliki."""
    untracked = git(project, "ls-files", "--others", "--exclude-standard",
                    check=False).stdout.splitlines()
    exempt = {p.strip() for p in untracked
              if p.strip() and _match_any(p.strip(), state.verify_test_globs)}
    return exempt | set(state.cycle_test_files)


def _task_may_touch_verify(state: State, task: dict) -> bool:
    """Odblokowanie chronionych ścieżek przez REJESTR, nie przez planistę:
    pole 'fixes' zadania musi wskazywać problem klasy verify_defect."""
    pid = (task or {}).get("fixes", "")
    if not pid:
        return False
    return any(p.get("id") == pid and p.get("class") == "verify_defect"
               for p in state.verify_problems)


# Konfiguracje CI chronione niezależnie od profilu (odpowiedniki workflow).
_CI_CONFIG_GLOBS = [".github/workflows/**", ".gitlab-ci.yml"]


def _verify_protected_globs(project: str, cfg: Config, state: State) -> list[str]:
    """Chronione ścieżki weryfikacji: workflow CI, testy targetowe/CI
    (verify_test_globs) i skrypty komend profilu. Najtańszą "naprawą"
    czerwonej weryfikacji nie może być jej wyłączenie."""
    return (_CI_CONFIG_GLOBS + list(state.verify_test_globs)
            + verify.verify_script_paths(project, state))


_MIN_JUSTIFIED_WHY = 20  # znaków — "bo tak" nie jest uzasadnieniem

# Nagłówki sekcji kryteriów (poziom 2): prefiks, nie exact — planista bywa
# dopisywał „(MVP)", „— TDD" itd.
_CHECKBOX_RE = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+(.*)$")
# Markery refaktoru: unikamy gołego „bez nowych testów …wydajnościowych".
# „bez nowych testów" tylko gdy zaraz koniec / interpunkcja / „ani …".
_REFACTOR_MARKER_RE = re.compile(
    r"(?:zadanie\s+refaktoryzacyjne|"
    r"nie\s+dodaje\s+nowych\s+testów|"
    r"bez\s+nowych\s+testów(?=\s*(?:ani\b|[.;:]|$)))",
    re.IGNORECASE,
)


def _is_criteria_header(title: str) -> bool:
    """Czy nagłówek ## to sekcja kryteriów (z ewentualnym dopiskiem)."""
    t = (title or "").strip().casefold()
    if not t:
        return False
    if t.startswith("kryteria akceptacji") or t.startswith("acceptance criteria"):
        return True
    # Samo „Kryteria" / „Kryteria:” — bez „Kryteria jakości kodu" w Celu.
    if t == "kryteria" or t.startswith("kryteria ") or t.startswith("kryteria:"):
        return True
    return False


def parse_task_criteria(markdown: str) -> list[str]:
    """Wyciągnij teksty checkboxów z sekcji Kryteria akceptacji pliku zadania.

    Wieloliniowe i zagnieżdżone bulletu bez ``[ ]`` doklejane są do bieżącego
    kryterium. Checkboxy poza sekcją są ignorowane."""
    if not markdown:
        return []
    lines = markdown.splitlines()
    in_section = False
    criteria: list[str] = []
    current: list[str] = []

    def flush() -> None:
        nonlocal current
        if current:
            text = " ".join(" ".join(current).split())
            if text:
                criteria.append(text)
        current = []

    for line in lines:
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            title = heading.group(1).strip()
            if _is_criteria_header(title):
                flush()
                in_section = True
                continue
            if in_section:
                flush()
                in_section = False
            continue
        if not in_section:
            continue
        m = _CHECKBOX_RE.match(line)
        if m:
            flush()
            current = [m.group(2).strip()]
            continue
        if current and line.strip():
            current.append(line.strip())
    flush()
    return criteria


def resolve_task_criteria(project: str, task: dict) -> tuple[list[str], str]:
    """Kanon kryteriów: plik zadania > JSON planisty > empty.

    Zwraca ``(criteria, source)`` gdzie source ∈
    ``file`` | ``planner_fallback`` | ``empty``."""
    rel = (task or {}).get("file", "") or ""
    body = ""
    if rel:
        path = os.path.join(project, rel)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                body = f.read()
        except OSError:
            body = ""
    file_crit = parse_task_criteria(body) if body else []
    json_crit = [c for c in ((task or {}).get("criteria") or []) if str(c).strip()]
    if file_crit:
        if json_crit:
            fn = {_norm_criterion(c) for c in file_crit}
            jn = {_norm_criterion(c) for c in json_crit}
            if fn != jn:
                log("TASK: kryteria JSON ≠ plik — używam pliku")
        return file_crit, "file"
    if json_crit:
        log("TASK: brak checkboxów w pliku — fallback na criteria JSON planisty")
        return list(json_crit), "planner_fallback"
    return [], "empty"


def is_refactor_task(task: dict, body: str = "") -> bool:
    """Czy zadanie jest refaktoryzacyjne (kind lub markery w criteria/body).

    Markery są wąskie (regex), żeby „bez nowych testów wydajnościowych"
    w Poza zakresem nie włączało ścieżki refactor."""
    if (task or {}).get("kind") == "refactor":
        return True
    parts = [body or ""]
    for c in (task or {}).get("criteria") or []:
        parts.append(str(c))
    blob = " ".join(parts)
    return bool(_REFACTOR_MARKER_RE.search(blob))


_COMPLEX_RISK_FLAGS = {
    "architecture", "ci", "concurrency", "data_migration", "hardware",
    "migration", "multi_module", "previous_failure", "public_api", "security", "toolchain",
    "verification",
}


def _normalized_risk_flags(raw: dict) -> list[str]:
    value = raw.get("risk_flags") or []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    return [
        str(flag).strip().lower().replace("-", "_").replace(" ", "_")
        for flag in value
        if str(flag).strip()
    ]


def resolve_task_difficulty(raw: dict) -> tuple[str, bool, list[str]]:
    """Znormalizuj propozycję planisty i mechanicznie eskaluj jawne ryzyko.

    Zwraca ``(effective, escalated, reasons)``. Orkiestrator nigdy nie obniża
    profilu. Starsze zadania bez pola ``difficulty`` trafiają do ``standard``.
    """
    original = str(raw.get("difficulty") or "").strip().lower()
    if not original:
        return DEFAULT_TASK_DIFFICULTY, False, ["legacy_default"]
    requested = original
    reasons: list[str] = []
    if requested not in TASK_DIFFICULTIES:
        requested = DEFAULT_TASK_DIFFICULTY
        reasons.append("invalid_difficulty")

    effective = requested
    flags = set(_normalized_risk_flags(raw))
    hard_flags = sorted(flags & _COMPLEX_RISK_FLAGS)
    if (hard_flags or raw.get("fixes") or raw.get("repro_cmd")
            or str(raw.get("kind") or "").strip().lower() == "refactor"):
        if effective != "complex":
            effective = "complex"
            reasons.extend(hard_flags or ["repair_or_refactor"])
    elif effective == "simple":
        # Kilka powierzchni plikowych zwykle wymaga koordynacji, nawet gdy
        # planista ocenił samą zmianę jako lokalną.
        code_globs = {str(x).strip() for x in raw.get("code_globs") or [] if str(x).strip()}
        test_globs = {str(x).strip() for x in raw.get("test_globs") or [] if str(x).strip()}
        if len(code_globs) > 1 or len(test_globs) > 1:
            effective = "standard"
            reasons.append("multiple_surfaces")
    return effective, effective != original, reasons


def build_task_from_plan(project: str, raw: dict) -> dict:
    """Zbuduj element kolejki z JSON planisty: kind, resolve criteria, globs."""
    rel = raw.get("file", "")
    difficulty, escalated, escalation_reasons = resolve_task_difficulty(raw)
    requested = str(raw.get("difficulty") or DEFAULT_TASK_DIFFICULTY).strip().lower()
    task = {
        "id": raw.get("id") or os.path.splitext(os.path.basename(rel))[0],
        "title": raw.get("title", "(zadanie)"),
        "file": rel,
        "criteria": list(raw.get("criteria") or []),
        "test_globs": list(raw.get("test_globs") or []),
        "code_globs": list(raw.get("code_globs") or []),
        "fixes": str(raw.get("fixes") or ""),
        "repro_cmd": str(raw.get("repro_cmd") or ""),
        "kind": str(raw.get("kind") or ""),
        "difficulty": difficulty,
        "difficulty_requested": requested,
        "difficulty_escalated": escalated,
        "difficulty_escalation_reasons": escalation_reasons,
        "risk_flags": _normalized_risk_flags(raw),
        "routing_reason": str(raw.get("routing_reason") or "").strip(),
    }
    canon, source = resolve_task_criteria(project, task)
    task["criteria"] = canon
    task["criteria_source"] = source
    return task


def _strip_pytest_param(seg: str) -> str:
    """``test_foo[param]`` → ``test_foo`` (parametry pytest na końcu segmentu)."""
    return re.sub(r"\[[^\]]*\]$", "", seg or "")


def _split_test_refs(test_field) -> list[str]:
    """Pole ``test`` z mapy: string, lista, lub string ze ``;`` / newline."""
    if test_field is None:
        return []
    if isinstance(test_field, list):
        return [str(x).strip() for x in test_field if str(x).strip()]
    text = str(test_field).strip()
    if not text:
        return []
    if ";" in text or "\n" in text:
        parts = re.split(r"[;\n]+", text)
        return [p.strip() for p in parts if p.strip()]
    return [text]


def _validate_one_test_ref(ref: str, project: str, test_globs: list[str]) -> str | None:
    """None jeśli OK, inaczej komunikat błędu dla jednego refu path[::name]."""
    path, _, name = ref.partition("::")
    path = path.strip().replace("\\", "/")
    full = os.path.join(project, path)
    if not os.path.isfile(full):
        return f"'{ref}': plik {path} nie istnieje w projekcie"
    if not _is_test_path(path, test_globs):
        return f"'{ref}': {path} nie jest ścieżką testową zadania"
    name = name.strip()
    if not name:
        return None
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        content = ""
    missing = []
    for seg in name.split("::"):
        if not seg:
            continue
        bare = _strip_pytest_param(seg)
        if bare and bare not in content:
            missing.append(seg)
    if missing:
        return f"'{ref}': nazwa '{name}' nie występuje w {path}"
    return None


def validate_criteria_map(criteria: list[str], criteria_map: list[dict],
                          project: str, test_globs: list[str]) -> list[str]:
    """Walidacja mapy kryterium→test przy DONE (PLAN-4 Z2 + PLAN-5).

    Pusty kanon kryteriów → zawsze błąd (DONE niemożliwe).
    ``test``: string, lista refów, lub string ze średnikami (PLAN-5).
    Segmenty nazwy: strip parametrów pytest ``[…]`` przed szukaniem w pliku.
    """
    if not criteria:
        return ["brak kryteriów akceptacji w zadaniu (kanon pusty)"]
    errors: list[str] = []
    satisfied: set[str] = set()
    for entry in criteria_map:
        if not isinstance(entry, dict):
            continue
        crit_norm = _norm_criterion(entry.get("criterion", ""))
        status = entry.get("status")
        if status == "covered":
            refs = _split_test_refs(entry.get("test"))
            if not refs:
                errors.append(f"kryterium {entry.get('criterion', '?')!r}: "
                              "'covered' bez pola 'test'")
                continue
            ref_ok = True
            for ref in refs:
                err = _validate_one_test_ref(ref, project, test_globs)
                if err:
                    errors.append(err)
                    ref_ok = False
            if ref_ok:
                satisfied.add(crit_norm)
        elif status == "justified":
            why = str(entry.get("why") or "").strip()
            if len(why) < _MIN_JUSTIFIED_WHY:
                errors.append(f"kryterium {entry.get('criterion', '?')!r}: "
                              "'justified' wymaga merytorycznego 'why' "
                              f"(≥ {_MIN_JUSTIFIED_WHY} znaków)")
                continue
            satisfied.add(crit_norm)
    for c in criteria:
        if _norm_criterion(c) and _norm_criterion(c) not in satisfied:
            short = c if len(c) <= 60 else c[:57] + "…"
            errors.append(f"kryterium bez ważnego pokrycia/uzasadnienia: {short!r}")
    return errors


# --- Git: tag/rollback/diff per zadanie -------------------------------------

def _tag(project: str, tag: str) -> None:
    if tag:
        git(project, "tag", "-f", tag, check=False)


def _delete_tag(project: str, tag: str) -> None:
    if tag:
        git(project, "tag", "-d", tag, check=False)


def _reset_to_tag(project: str, tag: str) -> None:
    rollback(project, tag or "HEAD")


def _is_runtime_artifact(path: str, *, runtime_dir: str = ".forge",
                         stop_file: str = "STOP") -> bool:
    """Artefakty runtime orkiestratora — nigdy nie podlegają kontroli podziału
    ról ani rollbackowi plikowemu (w prod są w .gitignore, filtrujemy też jawnie)."""
    p = path.replace("\\", "/")
    return (p in {"STATE.json", "STATE.json.tmp", stop_file}
            or p.startswith(runtime_dir.rstrip("/") + "/"))


def changed_files(project: str, ref: str = "HEAD", *,
                  runtime_dir: str = ".forge", stop_file: str = "STOP") -> list[str]:
    """Pliki zmienione względem ref (śledzone) plus nowe nieśledzone.

    Artefakty runtime (STATE.json, plik stopu, katalog runtime) są pomijane —
    nie są częścią pracy agenta, więc nie mogą naruszać podziału ról ani być
    wycofane. Nazwy pochodzą z konfiguracji, nie z literałów."""
    tracked = git(project, "diff", "--name-only", ref, check=False).stdout.splitlines()
    untracked = git(project, "ls-files", "--others", "--exclude-standard",
                    check=False).stdout.splitlines()
    return sorted({ln.strip() for ln in (tracked + untracked)
                   if ln.strip() and not _is_runtime_artifact(
                       ln.strip(), runtime_dir=runtime_dir, stop_file=stop_file)})


def revert_paths(project: str, paths: list[str]) -> None:
    """Wycofaj wskazane pliki: śledzone → checkout, nieśledzone → usuń."""
    for p in paths:
        if git(project, "checkout", "--", p, check=False).returncode != 0:
            try:
                os.remove(os.path.join(project, p))
            except OSError:
                pass


def run_gate(project: str, build_cmd: str, test_cmd: str, timeout: int) -> tuple[bool, str]:
    """Bramka build+test zwracająca (zielona?, ogon wyjścia przy czerwieni).

    Cicha wersja build_then_test (bez logowania) — dla pętli mikro-TDD, która
    ogon czerwieni przekazuje agentowi zamiast na konsolę."""
    if build_cmd:
        rc, out = _run_shellfree(project, build_cmd, timeout)
        if rc is None:
            return False, f"build: {out}"
        if rc != 0:
            return False, out[-1500:]
    if not test_cmd:
        return False, "brak test_cmd"
    rc, out = _run_shellfree(project, test_cmd, timeout)
    if rc is None:
        return False, f"test: {out}"
    return (rc == 0), ("" if rc == 0 else out[-1500:])


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
    _append_line(_journal_path(project, cfg), f"- [{ts()}] {text}\n")


def journal_tail(project: str, cfg: Config, max_chars: int | None = None) -> str:
    if max_chars is None:
        max_chars = cfg.journal_tail_chars
    try:
        with open(_journal_path(project, cfg), "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return ""
    if len(text) <= max_chars:
        return text
    tail = text[-max_chars:]
    nl = tail.find("\n")  # utnij urwaną pierwszą linię, zacznij od pełnego wpisu
    return tail[nl + 1:] if nl != -1 else tail


def _with_journal(project: str, cfg: Config, prompt: str, *, lost: bool = False) -> str:
    """Doklej skrót dziennika zadania jako kontekst dla agenta bez sesji."""
    tail = journal_tail(project, cfg)
    if not tail:
        return prompt
    intro = ("KONTEKST ODTWORZONY Z DZIENNIKA (Twoja poprzednia sesja przepadła; "
             if lost else
             "KONTEKST Z DZIENNIKA ZADANIA (Twoje wywołania są bezstanowe; ")
    return (f"{intro}to skrót dotychczasowego przebiegu zadania):\n"
            f"{tail}\n--- KONIEC DZIENNIKA ---\n\n{prompt}")


def _session_call(cfg: Config, project: str, state: State, role: str,
                  prompt: str, log_path: str) -> str:
    """Wywołaj rolę (tester/coder) jej agentem CLI, utrzymując ciągłość kontekstu.

    Agent wznawialny (codex) → sesja z resume; po utracie sesji świeża sesja
    z dziennikiem. Agent bezsesyjny (claude/generic) → jedno wywołanie z
    dziennikiem zadania jako kontekstem. Aktualizuje id sesji w stanie."""
    attr = "tester_session" if role == "tester" else "coder_session"
    difficulty = (state.current_task or {}).get(
        "difficulty", DEFAULT_TASK_DIFFICULTY
    )
    agent, model, effort = cfg.role(role, difficulty)

    if not agent_supports_resume(agent):
        return run_agent(agent, _with_journal(project, cfg, prompt),
                         cfg, project, log_path, model=model, effort=effort)

    sid = getattr(state, attr) or None
    # Świeża sesja w TOKU zadania (rotacja, restart) → kontekst z dziennika;
    # na starcie zadania (cykl 0, bez poprawek) świeża sesja jest naturalna.
    first_prompt = prompt
    if sid is None and (state.micro_cycle > 0 or state.fix_attempt > 0):
        first_prompt = _with_journal(project, cfg, prompt)
    try:
        out, new_sid = run_agent_session(agent, first_prompt, cfg, project, log_path,
                                         session_id=sid, model=model, effort=effort)
    except AgentError as exc:
        if not (sid and _looks_like_session_loss(str(exc))):
            raise
        log(f"Sesja roli '{role}' nieodtwarzalna — świeża sesja z dziennikiem zadania.")
        setattr(state, attr, "")
        save_checkpoint(project, state)
        out, new_sid = run_agent_session(
            agent, _with_journal(project, cfg, prompt, lost=True),
            cfg, project, log_path, session_id=None, model=model, effort=effort)
    if new_sid:
        setattr(state, attr, new_sid)
    return out


def _cycle_snapshot_dir(project: str, cfg: Config) -> str:
    return os.path.join(project, cfg.runtime_dir, "cycle_tests")


def _snapshot_identical(project: str, cfg: Config, rel: str) -> bool:
    """Czy plik cyklu jest bajt w bajt zgodny ze snapshotem testera —
    nietknięte testy cyklu nie wymagają ponownego pomiaru anty-osłabiania."""
    snap = os.path.join(_cycle_snapshot_dir(project, cfg), rel)
    try:
        with open(snap, "rb") as a, open(os.path.join(project, rel), "rb") as b:
            return a.read() == b.read()
    except OSError:
        return False


def _set_writable(project: str, files: list[str], writable: bool) -> None:
    """Best-effort chmod testów cyklu na turę kodera (PLAN-4, Z1). To
    DETERRENT odruchowych edycji, nie bariera — agent może zdjąć atrybut;
    właściwą bramką pozostaje kontrola diffu."""
    for rel in files:
        path = os.path.join(project, rel)
        try:
            mode = os.stat(path).st_mode
            os.chmod(path, (mode | 0o200) if writable else (mode & ~0o222))
        except OSError:
            pass


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


def anti_weakening_ok(project: str, files: list[str], build_cmd: str,
                      test_cmd: str, timeout: int) -> bool:
    """Bramka anty-osłabiania v2: testy cyklu (i toolchain w bieżącej postaci)
    MUSZĄ failować na kodzie sprzed cyklu (HEAD).

    Do tymczasowego worktree na HEAD (kod bez pracy kodera) kopiowane są
    bieżące wersje zmienionych testów ORAZ plików toolchainu, potem rusza
    bramka. Zielona = testy przechodzą bez implementacji = rozwodnione testy
    albo znerfowany runner → False.

    Baseline: zanim cokolwiek skopiujemy, bramka w czystym worktree musi być
    ZIELONA — czerwień (np. brak node_modules/artefaktów builda w worktree)
    czyni pomiar środowiskowo niemiarodajnym, więc check jest jawnie pomijany
    (fail-open Z LOGIEM; drugą linią zostaje recenzja). Brak plików /
    worktree niedostępny → True (nie blokuj — rozstrzygnie recenzja)."""
    files = [p for p in files if p]
    if not files:
        return True
    tmp = tempfile.mkdtemp(prefix="forge-antiweak-")
    wt = os.path.join(tmp, "wt")
    try:
        if git(project, "worktree", "add", "--detach", wt, "HEAD",
               check=False).returncode != 0:
            return True
        base_green, _ = run_gate(wt, build_cmd, test_cmd, timeout)
        if not base_green:
            log("ANTY-OSŁABIANIE: baseline na HEAD nie jest zielony w worktree "
                "— pomiar niemiarodajny, pomijam check (fail-open).")
            return True
        copied = False
        for rel in files:
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


def _verify_feedback_path(project: str, cfg: Config, state: State) -> str:
    """Ścieżka raportu ostatniej nieudanej weryfikacji — jeśli są otwarte
    problemy do naprawy i raport fizycznie istnieje (pamięć w repo)."""
    if not verify_ledger.for_planner(state.verify_problems):
        return ""
    path = os.path.join(project, cfg.runtime_dir, "verification",
                        f"cycle-{state.verify_cycle}", "feedback.md")
    return path if os.path.exists(path) else ""


_BRIEF_NOTICE_CHAR_CAP = 4000  # jak design_compact_notice — prompt tani tokenowo


def _read_brief(cfg: Config) -> str | None:
    if not cfg.brief_path or not os.path.exists(cfg.brief_path):
        return None
    with open(cfg.brief_path, "r", encoding="utf-8") as f:
        return f.read()


def _brief_snapshot_paths(project: str, cfg: Config) -> tuple[str, str]:
    """Dwa pliki: treść ostatnio uzgodnionego briefu + ścieżka źródłowa, z
    której pochodzi (żeby zmiana --brief/CWD między uruchomieniami nie
    porównała ze sobą dwóch niepowiązanych dokumentów)."""
    base = os.path.join(project, cfg.runtime_dir, "brief.snapshot")
    return base + ".md", base + ".path"


def _previous_brief_snapshot(project: str, cfg: Config) -> str | None:
    content_path, path_path = _brief_snapshot_paths(project, cfg)
    if not os.path.exists(content_path) or not os.path.exists(path_path):
        return None
    with open(path_path, "r", encoding="utf-8") as f:
        recorded_path = f.read().strip()
    if recorded_path != os.path.abspath(cfg.brief_path):
        return None  # inny plik źródłowy — traktuj jak pierwszą synchronizację
    with open(content_path, "r", encoding="utf-8") as f:
        return f.read()


def _save_brief_snapshot(cfg: Config, project: str, current: str) -> None:
    """Zapisz DOKŁADNIE tekst pokazany planiście — nie odczytuj pliku ponownie
    z dysku (mógł się zmienić w trakcie długiego run_planner; TOCTOU)."""
    content_path, path_path = _brief_snapshot_paths(project, cfg)
    os.makedirs(os.path.dirname(content_path), exist_ok=True)
    with open(content_path, "w", encoding="utf-8") as f:
        f.write(current)
    with open(path_path, "w", encoding="utf-8") as f:
        f.write(os.path.abspath(cfg.brief_path))


def _cap_notice_text(text: str) -> str:
    if len(text) <= _BRIEF_NOTICE_CHAR_CAP:
        return text
    overflow = len(text) - _BRIEF_NOTICE_CHAR_CAP
    return (text[:_BRIEF_NOTICE_CHAR_CAP]
            + f"\n… [obcięto {overflow} znaków — pełna treść w pliku briefu na dysku]")


def _brief_amendment_notice(cfg: Config, project: str, current: str, *, stalls: int = 0) -> str:
    """Notatka dryfu briefu dla planu wsadowego: brief bywa dopisywany/zmieniany
    długo po bootstrapie (jedynym miejscu, gdzie był dotąd czytany), więc taka
    zmiana inaczej nigdy nie dotrze do planisty. Snapshot żyje w .forge/ (nie
    w STATE.json) — funkcja jest w pełni addytywna, stare projekty bez śladu
    poprzedniej synchronizacji dostają pełny brief przy najbliższym planowaniu.

    ``current`` jest przekazywany przez wołającego (odczytany RAZ na początku
    fazy planowania), nie czytany tu ponownie — inaczej zmiana pliku w trakcie
    run_planner mogłaby zostać po cichu oznaczona jako 'uzgodniona', mimo że
    planista jej nie widział. ``stalls`` eskaluje ton, gdy poprzednie wsady
    nie zwróciły niczego, co dałoby się zapisać jako uzgodnione (patrz
    _update_brief_amend_stalls) — bez tego nagabywanie byłoby bezzębne."""
    escalation = ""
    if stalls > 0:
        escalation = (
            f"\nTO JUŻ {stalls}. WSAD BEZ REAKCJI na tę zmianę briefu — TYM RAZEM "
            "zadanie ją uwzględniające MUSI się znaleźć w tej kolejce.\n"
        )
    prev = _previous_brief_snapshot(project, cfg)
    if prev is None:
        return (
            "BRIEF ŚLEDZONY PIERWSZY RAZ — poniżej pełna treść pliku briefu "
            f"({cfg.brief_path}). Skonfrontuj z docs/DESIGN.md i BACKLOG.md: to, "
            "co już zrealizowane, zostaw bez zmian; nowe lub zmienione wymagania "
            "i priorytety wpisz jako zadania na czoło kolejki — jeśli unieważniają "
            "zaplanowane, nieukończone pozycje BACKLOG.md, jawnie je "
            f"wstrzymaj/usuń zamiast po cichu pomijać.\n{escalation}\n"
            f"--- BRIEF ---\n{_cap_notice_text(current)}"
        )
    if prev == current:
        return ""
    diff = "\n".join(difflib.unified_diff(
        prev.splitlines(), current.splitlines(),
        fromfile="brief (poprzednio uzgodniony)", tofile="brief (teraz)", lineterm=""))
    return (
        "BRIEF ZMIENIŁ SIĘ od ostatniego uzgodnienia — to rozkaz zmiany "
        "zakresu/priorytetów, nie sugestia. Zaktualizuj docs/DESIGN.md i "
        "BACKLOG.md zgodnie z różnicą poniżej; jeśli unieważnia to zaplanowane, "
        "nieukończone pozycje BACKLOG.md, jawnie je wstrzymaj/usuń zamiast po "
        f"cichu ignorować.\n{escalation}\n"
        f"--- DIFF BRIEFU (unified) ---\n{_cap_notice_text(diff)}"
    )


def _update_brief_amend_stalls(state: State, cfg: Config, project: str, brief_delta: str,
                               current_brief: str | None, tasks: list[dict], data: dict) -> None:
    """Zapisz snapshot TYLKO gdy planista faktycznie zwrócił używalny wsad
    (zadania albo jawne no_more_tasks) — inaczej zepsuty JSON planisty cicho
    'skonsumowałby' zmianę briefu bez żadnego realnego uzgodnienia (review,
    znalezisko #1). ``brief_delta`` puste = notatki nie było w tym wsadzie —
    nic do śledzenia."""
    if not brief_delta:
        return
    engaged = bool(tasks) or bool(data.get("no_more_tasks"))
    if engaged:
        _save_brief_snapshot(cfg, project, current_brief or "")
        if state.brief_amend_stalls:
            log("PLAN: zmiana briefu w końcu uwzględniona — reset licznika ignorowań.")
        state.brief_amend_stalls = 0
    else:
        state.brief_amend_stalls += 1
        log(f"PLAN: planista NIE zwrócił używalnego wsadu mimo zmiany briefu "
            f"({state.brief_amend_stalls}. raz z rzędu) — notatka wróci w kolejnym wsadzie.")


def _design_compact_notice(cfg: Config, project: str, state: State) -> str:
    """Notatka kompaktowania DESIGN.md dla planu wsadowego, gdy plik przerósł
    Config.design_compact_bytes (0 = wyłączone). Kryteria otwartych design_gap
    (jeszcze nie 'resolved') są przekazane jako chronione — patrz
    prompts.design_compact_notice."""
    if cfg.design_compact_bytes <= 0:
        return ""
    try:
        size = os.path.getsize(os.path.join(project, "docs", "DESIGN.md"))
    except OSError:
        return ""
    if size <= cfg.design_compact_bytes:
        return ""
    protected = sorted({
        str(p.get("criterion") or "").strip()
        for p in state.verify_problems
        if p.get("class") == "design_gap" and p.get("status") != "resolved"
        and str(p.get("criterion") or "").strip()
    })
    log(f"PLAN: docs/DESIGN.md {size} B > próg {cfg.design_compact_bytes} B — "
        "proszę o zadanie kompaktujące.")
    return prompts.design_compact_notice(protected, stalls=state.design_compact_stalls)


def _update_design_compact_stalls(state: State, design_compact: str, tasks: list[dict]) -> None:
    """Śledź, czy planista zastosował się do notatki o kompaktowaniu DESIGN.md.

    Bez tego licznika nagabywanie byłoby bezzębne: planista mógłby ignorować
    prośbę w nieskończoność, a pętla by tego nie zauważyła (znalezisko z
    review). ``design_compact`` puste = notatki nie było w tym wsadzie —
    nic do śledzenia."""
    if not design_compact:
        return
    if any(t.get("kind") == "refactor" for t in tasks):
        if state.design_compact_stalls:
            log("PLAN: zadanie kompaktujące DESIGN.md w końcu wstawione — "
                "reset licznika ignorowań.")
        state.design_compact_stalls = 0
    else:
        state.design_compact_stalls += 1
        log(f"PLAN: planista ZIGNOROWAŁ prośbę o kompaktowanie DESIGN.md "
            f"({state.design_compact_stalls}. raz z rzędu).")


def phase_plan_batch(cfg: Config, project: str, state: State, logf) -> dict:
    log(f"--- PLAN WSADOWY ({cfg.planner_agent}) ---")
    start = _next_task_index(project)
    feedback_path = _verify_feedback_path(project, cfg, state)
    if feedback_path:
        log(f"PLAN: przekazuję feedback weryfikacji: {feedback_path}")
    design_compact = _design_compact_notice(cfg, project, state)
    current_brief = _read_brief(cfg)
    brief_delta = (_brief_amendment_notice(cfg, project, current_brief,
                                           stalls=state.brief_amend_stalls)
                  if current_brief is not None else "")
    if brief_delta:
        log("PLAN: brief zmienił się od ostatniego uzgodnienia — przekazuję różnicę planiście.")
    ci_warning = ""
    if (cfg.ci_early_warn and state.ci_status_cmd
            and "ci" in _active_verify_targets(cfg, state)):
        # Wczesne ostrzeganie (PLAN-3, sekcja 9): push jest per zadanie, więc
        # CI i tak mieli — jeden tani odczyt statusu HEAD chroni przed
        # budowaniem dziesiątek zadań na złamanym CI. rc==2 (trwa) jest OK.
        rc, _ = _run_shellfree(project,
                               verify.expand_sha(state.ci_status_cmd, _head_sha(project)),
                               120)
        if rc == 1:
            ci_warning = ("CI dla bieżącego HEAD jest CZERWONE — rozważ zadanie "
                          "naprawcze, zanim dobudujesz kolejne funkcje.")
            log("PLAN: " + ci_warning)
    out = run_planner(prompts.plan_batch_prompt(cfg.batch_size, start, state.project_kind,
                                                verify_feedback_path=feedback_path,
                                                ci_warning=ci_warning,
                                                design_compact=design_compact,
                                                brief_delta=brief_delta),
                      cfg, project, logf("plan"))
    commit_all(project, "docs: plan wsadowy i backlog", cfg)  # pliki zadań, docs, backlog
    data = extract_json(out) or {}
    tasks = []
    for t in (data.get("tasks") or []):
        rel = t.get("file", "")
        if rel and os.path.exists(os.path.join(project, rel)):
            task = build_task_from_plan(project, t)
            tasks.append(task)
            if task["difficulty_escalated"]:
                log(
                    f"PLAN: profil {task['id']} podniesiony "
                    f"{task['difficulty_requested']} → {task['difficulty']} "
                    f"({', '.join(task['difficulty_escalation_reasons'])})"
                )
        else:
            log(f"PLAN: pomijam zadanie bez pliku na dysku: {t.get('id') or rel!r}")
    state.task_queue = tasks
    log(f"PLAN: kolejka {len(tasks)} zadań.")
    _update_design_compact_stalls(state, design_compact, tasks)
    _update_brief_amend_stalls(state, cfg, project, brief_delta, current_brief, tasks, data)
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
    if task.get("difficulty") not in TASK_DIFFICULTIES:
        task["difficulty"] = DEFAULT_TASK_DIFFICULTY
    canon, source = resolve_task_criteria(project, task)
    task["criteria"] = canon
    task["criteria_source"] = source
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
    state.gate_not_red_count = 0
    state.last_gate_not_red_attempt = 0
    state.repro_runs = 0
    state.review_notes = []
    state.fix_attempt = 0
    state.done_reject_count = 0
    state.fail_reason = ""
    state.escalation_notes = []
    state.escalation_map_errors = []
    state.done_escalated = False
    state.gate_not_red_escalated = False
    state.phase = "micro"
    _write_current_task_pointer(project, task)
    journal_reset(project, cfg, state.current_task_title)
    difficulty = task["difficulty"]
    routes = ", ".join(
        f"{role}={agent}/{model or 'default'}/{effort or 'default'}"
        for role in ("tester", "coder", "reviewer")
        for agent, model, effort in [cfg.role(role, difficulty)]
    )
    log(f"START zadania: {state.current_task_title} (tag {state.task_start_tag}; "
        f"difficulty={difficulty}, criteria_source={source}, n={len(canon)})")
    log(f"ROUTING [{difficulty}]: {routes}")
    if source == "empty":
        log("UWAGA: brak kryteriów akceptacji (plik bez checkboxów i pusty JSON).")
        if cfg.fail_on_empty_criteria:
            state.fail_reason = (
                "done_map: brak kryteriów akceptacji na starcie zadania "
                "(kanon pusty)")
            state.fail_immediate = True


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
    state.gate_not_red_count = 0
    state.last_gate_not_red_attempt = 0
    state.repro_runs = 0
    state.task_start_tag = ""
    state.review_notes = []
    state.fix_attempt = 0
    state.tests_green = False
    state.done_reject_reasons = []
    state.justified_criteria = []
    state.done_reject_count = 0
    state.fail_reason = ""
    state.fail_immediate = False
    state.escalation_notes = []
    state.escalation_map_errors = []
    state.done_escalated = False
    state.gate_not_red_escalated = False


def _task_gate(cfg: Config, project: str, state: State) -> tuple[bool, str]:
    """Bramka ZIELENI zadania: lokalna suita + (dla zadań naprawczych
    weryfikacji) repro problemu. Zieleń zadania z repro_cmd wymaga OBU.

    Sufit uruchomień repro per zadanie chroni sprzęt i czas (repro bywa
    flashowaniem targetu); wyczerpany = czerwień, zadanie pada, planista
    tnie inaczej. Bramka CZERWIENI testera pozostaje na samej suicie —
    repro jest czerwone z definicji, więc mieszanie go tam zaślepiłoby
    kontrolę "nowy test musi failować"."""
    green, tail = run_gate(project, state.build_cmd, state.test_cmd, cfg.agent_timeout_s)
    repro = (state.current_task or {}).get("repro_cmd", "")
    if not (green and repro):
        return green, tail
    if state.repro_runs >= cfg.max_repro_runs_per_task:
        return False, (f"limit uruchomień repro w zadaniu "
                       f"({cfg.max_repro_runs_per_task}) wyczerpany")
    state.repro_runs += 1
    repro_green, repro_tail = verify.run_repro(project, repro, cfg.verify_timeout_s)
    if not repro_green:
        return False, "REPRO problemu nadal czerwone:\n" + repro_tail
    return True, ""


def _apply_done_reject_policy(cfg: Config, project: str, state: State,
                              map_errors: list[str], canon: list[str], c: int):
    """Po limicie kolejnych rejectów mapy: escalate | False (fail). None = kontynuuj."""
    policy = (cfg.done_reject_policy or "review_if_green").strip().lower()
    if policy == "continue":
        return None
    if policy == "fail":
        state.fail_reason = (
            f"done_map: DONE odrzucone {state.done_reject_count} razy: "
            + "; ".join(map_errors)[:300])
        log(state.fail_reason)
        return False
    # review_if_green (default)
    green, tail = _task_gate(cfg, project, state)
    if not green:
        state.fail_reason = (
            f"done_map: mapa DONE ×{state.done_reject_count} + bramka czerwona: "
            + (tail or "")[:200])
        log(state.fail_reason)
        return False
    notes = [f"odrzuceń mapy: {state.done_reject_count}"]
    notes.extend(map_errors[:12])
    notes.append("KANON kryteriów:")
    notes.extend(f"- {crit[:100]}" for crit in canon[:20])
    state.escalation_notes = notes
    state.escalation_map_errors = list(map_errors)
    state.done_escalated = True
    state.justified_criteria = []  # nie udajemy zaakceptowanej mapy
    journal_append(
        project, cfg,
        f"DONE_ESCALATE after {state.done_reject_count} rejects; "
        f"criteria_source={state.current_task.get('criteria_source', '?')}; gate=green")
    log(f"ESKALACJA DONE → review (po {state.done_reject_count} rejectach mapy, "
        "bramka zielona).")
    state.phase = "review"
    state.micro_cycle = c
    save_checkpoint(project, state)
    return "escalate"


def _run_micro_loop(cfg: Config, project: str, state: State, logf):
    """Pętla b/c: tester dyktuje jeden test, koder zazielenia i refaktoryzuje.

    Zwraca: "done" gdy tester orzekł DONE po zielonej bramce; "escalate" gdy
    limit rejectów mapy + policy review_if_green (bramka zmierzona zielona);
    "smell" gdy recenzję wymusił nadmiar 'no_test' (bramka NIE odpalona) albo
    serii "gate nie poczerwieniała" (bramka odpalana za każdym razem, tylko
    że wciąż zielona — ostatni wynik i tak NIE liczy się jako fresh_gate,
    review i tak zmierzy ją sam);
    False gdy zadanie padło. ``done``/``escalate`` → gate_green w review."""
    if state.fail_immediate:
        return False
    task = state.current_task
    task_file = task.get("file", "")
    test_globs = task.get("test_globs") or []
    task_body = ""
    try:
        with open(os.path.join(project, task_file), "r", encoding="utf-8",
                  errors="replace") as f:
            task_body = f.read()
    except OSError:
        pass
    refactor = is_refactor_task(task, task_body)

    def gate() -> tuple[bool, str]:
        return run_gate(project, state.build_cmd, state.test_cmd, cfg.agent_timeout_s)

    while True:
        if state.micro_cycle >= cfg.max_micro_cycles:
            log(f"Limit mikro-cykli ({cfg.max_micro_cycles}) — zadanie nieukończone.")
            state.fail_reason = (
                state.fail_reason
                or f"micro_cap: mikro-TDD nieukończone (cykli={state.micro_cycle})")
            return False
        c = state.micro_cycle + 1

        if state.micro_sub == "test":
            log(f"[cykl {c}] TESTER pisze test / ocenia ukończenie")
            out = _session_call(cfg, project, state, "tester",
                                prompts.write_test_prompt(
                                    task_file, state.test_cmd,
                                    reject_reasons=state.done_reject_reasons,
                                    refactor=refactor,
                                    gate_not_red_count=state.last_gate_not_red_attempt,
                                    resume=bool(state.tester_session)),
                                logf(f"c{c:02d}-test"))
            verdict = extract_json(out) or {"action": "no_test", "reason": "brak werdyktu JSON"}
            state.done_reject_reasons = []  # skonsumowane w prompcie powyżej
            state.last_gate_not_red_attempt = 0  # skonsumowane w prompcie powyżej
            action = verdict.get("action")

            if action == "done":
                # Odśwież kanon z dysku (PLAN-5: plik > JSON).
                canon, source = resolve_task_criteria(project, task)
                task["criteria"] = canon
                task["criteria_source"] = source
                state.current_task = task
                map_errors = validate_criteria_map(
                    canon, verdict.get("criteria_map") or [],
                    project, test_globs)
                if map_errors:
                    state.done_reject_count += 1
                    log("DONE odrzucony: " + "; ".join(map_errors)[:400]
                        + f" — k={state.done_reject_count}.")
                    # Pełny kanon w feedbacku (skróty w errorach nie wystarczają do 1:1).
                    state.done_reject_reasons = list(map_errors) + [
                        "KANON (pełne teksty checkboxów — przepisz criterion dosłownie):",
                        *[f"  • {crit}" for crit in canon],
                    ]
                    state.micro_cycle = c  # zużyj cykl, by nie zapętlić w nieskończoność
                    state.micro_sub = "test"
                    journal_append(
                        project, cfg,
                        f"cykl {c}, tester: done ODRZUCONY "
                        f"(k={state.done_reject_count}): "
                        + "; ".join(map_errors)[:200])
                    if state.done_reject_count >= cfg.max_done_rejects:
                        outcome = _apply_done_reject_policy(
                            cfg, project, state, map_errors, canon, c)
                        if outcome is not None:
                            return outcome
                    save_checkpoint(project, state)
                    continue
                journal_append(project, cfg,
                               f"cykl {c}, tester: done "
                               f"({verdict.get('about') or ''})".rstrip())
                # Kryteria "justified" przeszły walidację formy — merytorykę
                # rozstrzygnie recenzent (dostaje je jawnie w prompcie).
                state.justified_criteria = [
                    {"criterion": e.get("criterion", ""), "why": e.get("why", "")}
                    for e in (verdict.get("criteria_map") or [])
                    if isinstance(e, dict) and e.get("status") == "justified"]
                state.done_reject_reasons = []
                state.done_reject_count = 0
                state.done_escalated = False
                state.gate_not_red_escalated = False
                state.escalation_notes = []
                state.escalation_map_errors = []
                green, _ = _task_gate(cfg, project, state)
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
                return "done"

            journal_append(project, cfg,
                           f"cykl {c}, tester: {action} "
                           f"({verdict.get('about') or verdict.get('reason') or ''})".rstrip())

            if action == "wrote_test":
                state.done_reject_count = 0

            if action == "no_test":
                if task.get("repro_cmd"):
                    # Zadanie naprawcze weryfikacji: specyfikacją jest repro,
                    # więc brak lokalnego testu nie jest smellem (PLAN-3, 5.2).
                    log(f"[cykl {c}] TESTER: brak testu (zadanie z repro — bez smellu).")
                    state.pending_no_test = True
                    state.cycle_test_files = []
                    state.micro_sub = "code"
                    save_checkpoint(project, state)
                    continue
                state.no_test_count += 1
                threshold = max(2, cfg.max_micro_cycles // 3)
                if state.no_test_count > threshold:
                    # Tester nie potrafi już specyfikować testami — dryf „bez
                    # specyfikacji" ograniczamy, oddając całość recenzentowi.
                    log(f"SMELL: {state.no_test_count}× 'no_test' (> {threshold}) — wymuszam recenzję.")
                    # Deklarując 'no_test' tester nie powinien nic pisać; gdyby
                    # zostawił zmiany, cofnij je — recenzja ma oceniać ostatni
                    # zielony commit, nie niezweryfikowane resztki.
                    stray = changed_files(project, "HEAD", runtime_dir=cfg.runtime_dir,
                                          stop_file=cfg.stop_file)
                    if stray:
                        revert_paths(project, stray)
                    journal_append(project, cfg,
                                   f"smell no_test ({state.no_test_count}) → wymuszona recenzja")
                    state.phase = "review"
                    save_checkpoint(project, state)
                    return "smell"
                log(f"[cykl {c}] TESTER: brak sensownego testu — krok bez testu.")
                state.pending_no_test = True
                state.cycle_test_files = []
                state.micro_sub = "code"
                save_checkpoint(project, state)
                continue

            # action == "wrote_test"
            changed = changed_files(project, "HEAD", runtime_dir=cfg.runtime_dir,
                                    stop_file=cfg.stop_file)
            offending = tester_path_violations(changed, test_globs)
            if offending:
                log(f"TESTER poza ścieżkami testów: {offending} — wycofuję.")
                revert_paths(project, offending)
                changed = changed_files(project, "HEAD", runtime_dir=cfg.runtime_dir,
                                        stop_file=cfg.stop_file)
            tests_here = [p for p in changed if _is_test_path(p, test_globs)]
            green, _ = gate()
            if red_gate_ok(green):
                state.cycle_test_files = tests_here
                snapshot_cycle_tests(project, cfg, tests_here)  # do przywracania po koderze
                state.pending_no_test = False
                state.micro_sub = "code"
                save_checkpoint(project, state)
                continue
            log("Bramka NIE zczerwieniała: nowy test przechodzi od razu → odrzucam.")
            revert_paths(project, tests_here)
            state.gate_not_red_count += 1
            threshold = max(2, cfg.max_micro_cycles // 3)
            if state.gate_not_red_count > threshold:
                # Seria "test przechodzi od razu" ma DWIE możliwe przyczyny —
                # tester źle celuje w test ALBO kod jest już wystarczająco
                # ogólny i kryterium jest naprawdę spełnione (to nie defekt).
                # Nie rozstrzygamy tego mechanicznie: jak przy smellu no_test,
                # oddajemy ocenę recenzentowi zamiast cicho zużyć resztę
                # sufitu mikro-cykli do porażki zadania.
                log(f"SMELL: {state.gate_not_red_count}× 'gate nie poczerwieniała' "
                    f"(> {threshold}) — wymuszam recenzję.")
                journal_append(project, cfg,
                               f"smell gate_not_red ({state.gate_not_red_count}) "
                               "→ wymuszona recenzja")
                state.gate_not_red_escalated = True
                state.phase = "review"
                save_checkpoint(project, state)
                return "smell"
            state.last_gate_not_red_attempt = state.gate_not_red_count
            state.micro_cycle = c  # zużyj cykl (bounded retry)
            state.micro_sub = "test"
            save_checkpoint(project, state)
            continue

        # state.micro_sub == "code"
        no_test = state.pending_no_test
        log(f"[cykl {c}] KODER {'(krok bez testu)' if no_test else 'zazielenia test'} + refaktor")
        green, tail = False, ""
        verdict: dict = {}
        locked = state.cycle_test_files if cfg.lock_tests else []
        _set_writable(project, locked, False)  # testy read-only na turę kodera
        try:
            for attempt in range(cfg.max_green_retries + 1):
                out = _session_call(cfg, project, state, "coder",
                                    prompts.code_and_refactor_prompt(
                                        task_file, state.test_cmd, no_test, tail,
                                        resume=bool(state.coder_session)),
                                    logf(f"c{c:02d}-code{attempt}"))
                verdict = extract_json(out) or {}
                green, tail = _task_gate(cfg, project, state)
                if green:
                    break
                log(f"[cykl {c}] bramka CZERWONA (próba {attempt + 1}/{cfg.max_green_retries + 1})")
        finally:
            _set_writable(project, locked, True)
        journal_append(project, cfg,
                       f"cykl {c}, koder: {'zielony' if green else 'czerwony'} "
                       f"({verdict.get('notes') or ''})".rstrip())
        if not green:
            log("Koder nie zazielenił bramki w limicie prób — porażka zadania.")
            state.fail_reason = (
                f"coder_red: koder nie zazielenił bramki "
                f"(cykl {c}, próby={cfg.max_green_retries + 1})")
            return False

        declared = [tc.get("file", "") for tc in (verdict.get("test_changes") or [])
                    if isinstance(tc, dict)]
        changed = changed_files(project, "HEAD", runtime_dir=cfg.runtime_dir,
                                stop_file=cfg.stop_file)
        violations = coder_test_violations(changed, test_globs,
                                           state.cycle_test_files, declared)
        if violations:
            log(f"KODER zmienił testy niezadeklarowanie: {violations} — wycofuję, ponawiam bramkę.")
            revert_paths(project, violations)
            changed = [p for p in changed if p not in violations]
            green, _ = _task_gate(cfg, project, state)
            if not green:
                log("Po wycofaniu niedozwolonych zmian testów bramka czerwona — porażka.")
                state.fail_reason = "coder_red: po wycofaniu niedozwolonych zmian testów bramka czerwona"
                return False

        protected = _verify_protected_globs(project, cfg, state)
        if protected:
            offending = verify_protected_violations(
                changed, protected, _task_may_touch_verify(state, task),
                _protected_exempt(project, state))
            if offending:
                log(f"KODER dotknął chronionych ścieżek weryfikacji: {offending} "
                    "— wycofuję (dozwolone tylko w zadaniu verify_defect).")
                revert_paths(project, offending)
                changed = [p for p in changed if p not in offending]
                green, _ = _task_gate(cfg, project, state)
                if not green:
                    log("Po wycofaniu zmian w plikach weryfikacji bramka czerwona — porażka.")
                    state.fail_reason = (
                        "coder_red: po wycofaniu chronionych ścieżek weryfikacji "
                        "bramka czerwona")
                    return False

        tc_globs = effective_toolchain_globs(cfg, state)
        code_globs = task.get("code_globs") or []
        if no_test and code_globs:
            # Krok bez testu nie ma czerwonego testu, więc pomiar anty-osłabiania
            # nie ma czego failować — toolchain wolno ruszyć tylko, gdy planista
            # przewidział to w "Ścieżkach kodu" zadania (decyzja widoczna w recenzji).
            # Puste code_globs = planista nic nie zadeklarował = nic do
            # wyegzekwowania (konwencja jak w role_paths_ok) — NIE "zakaż
            # wszystkiego", bo to blokowałoby nawet legalne dodanie zależności.
            offending = [p for p in changed
                         if is_toolchain_path(p, tc_globs)
                         and not _match_any(p, code_globs)]
            if offending:
                log(f"KODER dotknął toolchainu testów w kroku bez testu: {offending} "
                    "— wycofuję (dozwolone tylko w 'Ścieżkach kodu' zadania).")
                revert_paths(project, offending)
                changed = [p for p in changed if p not in offending]
                green, _ = _task_gate(cfg, project, state)
                if not green:
                    log("Po wycofaniu zmian toolchainu bramka czerwona — porażka.")
                    state.fail_reason = (
                        "coder_red: po wycofaniu niedozwolonego toolchainu "
                        "bramka czerwona")
                    return False

        # Anty-osłabianie v2: zbiór wejściowy MECHANICZNIE z diffu (deklaracja
        # kodera zostaje tylko kontekstem recenzji). Nietknięte testy cyklu nie
        # wymagają pomiaru same w sobie, ale przy zmianach toolchainu wchodzą
        # do kopii — bez nich pomiar nerfu nie miałby czego failować.
        candidates = weakening_candidates(changed, test_globs, tc_globs)
        tool_part = [p for p in candidates if not _is_test_path(p, test_globs)]
        test_part = [p for p in candidates if _is_test_path(p, test_globs)]
        if tool_part:
            test_part = sorted(set(test_part) | set(state.cycle_test_files))
        else:
            test_part = [p for p in test_part
                         if not (p in state.cycle_test_files
                                 and _snapshot_identical(project, cfg, p))]
        measured = sorted(set(test_part) | set(tool_part))
        if test_part and not anti_weakening_ok(project, measured, state.build_cmd,
                                               state.test_cmd, cfg.agent_timeout_s):
            log(f"ANTY-OSŁABIANIE: {measured} przechodzą na kodzie sprzed cyklu "
                "— rozwodnione testy albo znerfowany toolchain. Przywracam.")
            restore_test_changes(project, cfg, test_part, state.cycle_test_files)
            revert_paths(project, tool_part)
            green, _ = _task_gate(cfg, project, state)
            if not green:
                log("Po przywróceniu testów/toolchainu bramka czerwona — porażka zadania.")
                state.fail_reason = (
                    "coder_red: po przywróceniu testów/toolchainu (anty-osłabianie) "
                    "bramka czerwona")
                return False

        final_changed = changed_files(project, "HEAD", runtime_dir=cfg.runtime_dir,
                                      stop_file=cfg.stop_file)
        journal_append(project, cfg,
                       f"cykl {c}, commit; pliki: {', '.join(final_changed[:12])}")
        commit_all(project, f"tdd: {state.current_task_title} (cykl {c})")
        state.micro_cycle = c
        state.micro_sub = "test"
        state.cycle_test_files = []
        state.pending_no_test = False
        state.done_reject_count = 0  # udany code-cycle — seria rejectów mapy przerwana
        if cfg.session_rotate_cycles and c % cfg.session_rotate_cycles == 0:
            log(f"Rotacja sesji ról po cyklu {c} — świeży kontekst z dziennikiem "
                "(higiena kontekstu).")
            state.tester_session = ""
            state.coder_session = ""
        save_checkpoint(project, state)


_GATE_RED_NOTE = "Bramka testów czerwona — przywróć zieleń."


def _run_review_loop(cfg: Config, project: str, state: State, logf, *,
                     gate_green: bool = False) -> bool:
    """Faza d): recenzja całości przez Codeksa-testera + poprawki kodera.

    ``gate_green=True`` gdy wołający ma świeży, zielony wynik bramki na
    niezmienionym drzewie (prosto z DONE mikro-pętli) — wtedy nie odpalamy
    jej ponownie. Zwraca True gdy approve, False gdy limit poprawek."""
    task = state.current_task
    task_file = task.get("file", "")
    def gate() -> tuple[bool, str]:
        return _task_gate(cfg, project, state)  # suita + repro zadania naprawczego

    # None = wynik nieznany (trzeba odpalić bramkę); ustawiany, gdy drzewo się
    # nie zmieniło od ostatniego pomiaru. Świadomie NIE trwały w STATE.json —
    # po restarcie wynikowi sprzed restartu nie ufamy.
    known_green: bool | None = True if (gate_green and state.phase == "review") else None

    while True:
        if state.phase == "fix_review":
            log(f"--- POPRAWKI PO RECENZJI (koder) runda {state.fix_attempt + 1} ---")
            out = _session_call(cfg, project, state, "coder",
                                prompts.fix_review_prompt(
                                    state.review_notes, state.test_cmd,
                                    resume=bool(state.coder_session)),
                                logf(f"review-fix{state.fix_attempt + 1}"))
            protected = _verify_protected_globs(project, cfg, state)
            if protected:
                changed = changed_files(project, "HEAD", runtime_dir=cfg.runtime_dir,
                                        stop_file=cfg.stop_file)
                offending = verify_protected_violations(
                    changed, protected, _task_may_touch_verify(state, task),
                    _protected_exempt(project, state))
                if offending:
                    log(f"Poprawki dotknęły chronionych ścieżek weryfikacji: {offending} "
                        "— wycofuję.")
                    revert_paths(project, offending)
            green, _ = gate()
            if green:
                commit_all(project, f"fix: {state.current_task_title} (recenzja {state.fix_attempt + 1})")
            elif state.fix_attempt + 1 >= cfg.max_fix_attempts:
                log("Poprawki recenzji nie zazieleniły bramki w limicie — porażka.")
                return False
            state.fix_attempt += 1
            state.phase = "review"
            save_checkpoint(project, state)
            known_green = green  # drzewo bez zmian od pomiaru — nie mierz drugi raz

        if known_green is None:
            green, _ = gate()
        else:
            green, known_green = known_green, None
        if not green:
            if state.fix_attempt >= cfg.max_fix_attempts:
                return False
            # Zachowaj merytoryczne uwagi recenzenta — czerwona bramka ich nie
            # unieważnia; dopisz tylko wymóg przywrócenia zieleni.
            if _GATE_RED_NOTE not in state.review_notes:
                state.review_notes = list(state.review_notes) + [_GATE_RED_NOTE]
            state.phase = "fix_review"
            save_checkpoint(project, state)
            continue

        # Recenzent w ŚWIEŻYM kontekście (PLAN-4, Z2): bez sesji autorów i bez
        # dziennika (narracja testera/kodera to wektor sugestii). Kontekst
        # buduje mechanicznie orkiestrator: diff od taga startu, zmiany
        # toolchainu, kryteria justified do rozstrzygnięcia.
        log("--- RECENZJA CAŁOŚCI (świeży kontekst) ---")
        difficulty = (state.current_task or {}).get(
            "difficulty", DEFAULT_TASK_DIFFICULTY
        )
        agent, model, effort = cfg.role("reviewer", difficulty)
        changed = changed_files(project, state.task_start_tag or "HEAD",
                                runtime_dir=cfg.runtime_dir, stop_file=cfg.stop_file)
        toolchain_changed = [p for p in changed
                             if is_toolchain_path(p, effective_toolchain_globs(cfg, state))]
        # run_agent_session (nie gołe run_agent): dla codeksa to jedyna droga,
        # która woła log_usage (run_codex_session), więc tokeny recenzji nie
        # znikają z .forge/usage.jsonl. session_id=None za każdym razem i
        # zwrócone id ŚWIADOMIE odrzucane — recenzent nigdy nie dziedziczy
        # pamięci poprzedniej rundy (fresh-context, PLAN-4 Z2).
        escalation = None
        if state.done_escalated:
            escalation = {
                "reason": "done_reject",
                "reject_count": state.done_reject_count,
                "map_errors": list(state.escalation_map_errors
                                  or state.done_reject_reasons or []),
                "criteria": list((state.current_task or {}).get("criteria") or []),
            }
        elif state.gate_not_red_escalated:
            escalation = {
                "reason": "gate_not_red",
                "attempts": state.gate_not_red_count,
                "criteria": list((state.current_task or {}).get("criteria") or []),
            }
        out, _reviewer_sid = run_agent_session(
            agent,
            prompts.review_task_prompt(
                task_file, state.test_cmd,
                start_tag=state.task_start_tag,
                changed=changed, toolchain_changes=toolchain_changed,
                justified=state.justified_criteria,
                escalation=escalation),
            cfg, project, logf(f"review-r{state.fix_attempt}"),
            session_id=None, model=model, effort=effort)
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
    commit_all(project, f"feat: {state.current_task_title}")  # residuum (np. docs)
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
    metrics = (f" cykli={state.micro_cycle} done_reject={state.done_reject_count} "
               f"sub={state.micro_sub} "
               f"criteria_source={state.current_task.get('criteria_source', '?')}")
    log(f"NIEPOWODZENIE zadania '{title}': {detail}{metrics}")
    # PLAN-5: zachowaj HEAD (i residual WIP) na branchu przed rollbackiem.
    failed_ref = ""
    short_sha = ""
    task_id = (state.current_task or {}).get("id") or "task"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(task_id)).strip("-") or "task"
    if cfg.keep_failed_ref:
        try:
            if has_changes(project):
                commit_all(project, f"wip: failed {title}")
            branch = f"forge/failed/{slug}"
            git(project, "branch", "-f", branch, "HEAD", check=False)
            failed_ref = branch
            short_sha = git(project, "rev-parse", "--short", "HEAD",
                            check=False).stdout.strip()
            log(f"Artefakt porażki: {branch} @ {short_sha}")
        except Exception as exc:  # best-effort — fail task i tak idzie dalej
            log(f"Nie udało się zapisać forge/failed/*: {exc}")
    fail_line = detail
    if failed_ref:
        fail_line = f"{detail} | ref={failed_ref} sha={short_sha}"
    # Najpierw reset: `git clean -fd` usunąłby nieignorowane `.forge/` jeśli
    # failures.md zapisalibyśmy przed clean (testy bez ensure_repo/.gitignore).
    _reset_to_tag(project, state.task_start_tag)  # lokalnie — nic nie było pushowane
    _delete_tag(project, state.task_start_tag)
    record_failure(project, cfg, state, title, fail_line)
    if cfg.replan_on_failure and state.task_queue:
        # Wsad był planowany przy założeniu sukcesu tego zadania — porażka
        # falsyfikuje wejście zadań następnych (PLAN-4, Z3). Planista
        # przeplanowuje tanio: failures.md + pliki zadań zostają na dysku.
        dropped = [t.get("id") or t.get("title", "?") for t in state.task_queue]
        log(f"Porzucam pozostały wsad ({len(dropped)} zadań) — planowanie od nowa.")
        _append_line(os.path.join(project, cfg.runtime_dir, "failures.md"),
                     f"- [{ts()}] porzucono wsad po porażce '{title}': "
                     f"{', '.join(dropped)}\n")
        state.task_queue = []
    state.iteration = n
    _clear_task(state)
    save_checkpoint(project, state)


# --- Weryfikacja celu (PLAN-3): cykl po wyczerpaniu backlogu -----------------

def _active_verify_targets(cfg: Config, state: State) -> list[str]:
    """Targety weryfikacji PO nadpisaniu użytkownika. Profil w STATE.json to
    deklaracja bootstrapu; FORGE_VERIFY_TARGETS musi działać także na już
    zbootstrapowanym projekcie (np. 'none', gdy płytka odpięta), więc
    nadpisanie stosujemy w miejscu użycia, nie tylko przy deklaracji."""
    return cfg.effective_verify_targets(state.verify_targets)


class VerificationStop(RuntimeError):
    """Twardy, mechaniczny stop pętli przez weryfikację celu: potwierdzony
    env_issue (kod 4) albo brak postępu / sufit cykli (kod 5). Odróżnialny
    w main() od porażek merytorycznych."""

    def __init__(self, message: str, exit_code: int = 4):
        super().__init__(message)
        self.exit_code = exit_code


def _head_sha(project: str) -> str:
    return git(project, "rev-parse", "HEAD", check=False).stdout.strip()


def _cycle_dir(project: str, cfg: Config, cycle: int) -> str:
    return os.path.join(project, cfg.runtime_dir, "verification", f"cycle-{cycle}")


def _read_design(project: str) -> str:
    try:
        with open(os.path.join(project, "docs", "DESIGN.md"), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _note_problems(project: str, notes: list[dict], cycle: int) -> None:
    """Nieblokujące znaleziska weryfikacji → propozycje w BACKLOG.md.

    To ujście zdegradowanych design_gapów i odrzuconych repro: nie blokują
    PASS, ale nie giną — człowiek/planista może je awansować."""
    for p in notes:
        _append_line(os.path.join(project, "BACKLOG.md"),
                     f"- [ ] (weryfikacja c{cycle}, {p.get('id')}, nieblokujące) "
                     f"{p.get('title', '')}\n")


def _accept_verdict(cfg: Config, project: str, state: State, evidence: dict,
                    cycle_dir: str, logf) -> list[dict]:
    """Wywołaj weryfikatora (świeży kontekst) i wyegzekwuj poprawny rejestr.

    Odrzucenie (zły JSON, nieodhaczone problemy z N-1, nowy code_bug bez
    repro_cmd) daje JEDNO ponowienie z listą powodów; potem AgentError —
    checkpoint zostaje, człowiek widzi log."""
    agent, model, effort = cfg.role("verifier")
    prev_path = (os.path.join(project, cfg.runtime_dir, "verification",
                              f"cycle-{state.verify_cycle - 1}", "problems.json")
                 if state.verify_problems else "")
    base_prompt = prompts.verify_goal_prompt(
        cycle=state.verify_cycle, evidence=evidence, cycle_dir=cycle_dir,
        prev_problems_path=prev_path, run_cmd=state.run_cmd)
    prompt, errors = base_prompt, ["(nie wywołano)"]
    for attempt in range(2):
        out = run_agent(agent, prompt, cfg, project,
                        logf(f"verify-c{state.verify_cycle}-a{attempt}"),
                        model=model, effort=effort,
                        mcp_config=cfg.verifier_mcp_config)
        verdict = extract_json(out)
        problems = (verdict or {}).get("problems")
        errors = []
        if not isinstance(problems, list):
            problems, errors = [], ["brak werdyktu JSON z listą 'problems'"]
        else:
            errors += verify_ledger.validate_problems(problems)
            ok, missing = verify_ledger.ledger_complete(state.verify_problems, problems)
            if not ok:
                errors.append("nieodhaczone problemy z poprzedniego cyklu: "
                              + ", ".join(missing))
            need = verify_ledger.missing_repro(problems)
            if need:
                errors.append("brak repro_cmd dla nowych code_bug: " + ", ".join(need))
        if not errors:
            return problems
        log(f"Werdykt weryfikatora odrzucony: {'; '.join(errors)}")
        prompt = (base_prompt + "\n\nPOPRZEDNI WERDYKT ODRZUCONY z powodów: "
                  + "; ".join(errors) + "\nPopraw rejestr i zwróć werdykt ponownie.")
    raise AgentError("Weryfikator nie dostarczył poprawnego rejestru problemów: "
                     + "; ".join(errors))


def _confirm_env_issues(cfg: Config, project: str, state: State,
                        problems: list[dict], cycle_dir: str,
                        targets: list[str]) -> None:
    """env_issue na słowo agenta nie zatrzymuje biegu — najpierw mechaniczna
    powtórka dowodów targetu. Potwierdzony → ENV-ISSUE.md + VerificationStop
    (sprawa człowieka). Niepotwierdzony → reklasyfikacja na code_bug."""
    for p in problems:
        if p.get("class") != "env_issue" or p.get("status") == "resolved":
            continue
        target = p.get("target", "")
        if target in targets and verify.confirm_env_issue(
                project, state, cfg, target, cycle_dir, sha=state.verify_sha):
            path = os.path.join(cycle_dir, "ENV-ISSUE.md")
            _append_line(path,
                         f"# Problem środowiska: {p.get('id')} — {p.get('title', '')}\n\n"
                         f"Target: {target}\nDowód: {p.get('evidence', '')}\n\n"
                         "Potwierdzone mechanicznie (powtórka dowodów nadal czerwona).\n"
                         "Sprawdź sekrety CI / podłączenie sprzętu / toolchain "
                         "i uruchom pętlę ponownie.\n")
            save_checkpoint(project, state)
            raise VerificationStop(
                f"env_issue potwierdzony ({p.get('id')}: {p.get('title', '')}) "
                f"— raport: {path}", exit_code=4)
        log(f"env_issue {p.get('id')} NIEPOTWIERDZONY mechanicznie — "
            "reklasyfikacja na code_bug.")
        p["class"] = "code_bug"
        p["reclassified"] = "env_issue"


def _drop_green_repros(cfg: Config, project: str, problems: list[dict]) -> list[dict]:
    """Repro nowego code_buga MUSI być czerwony przy odbiorze ("czerwone
    najpierw", jak test testera). Zielony = dowód nie odtwarza usterki →
    problem zdegradowany do notatki. Zwraca odrzucone wpisy."""
    dropped = []
    for p in problems:
        if (p.get("class") == "code_bug" and p.get("status") == "new"
                and p.get("repro_cmd") and not p.get("reclassified")):
            green, _ = verify.run_repro(project, p["repro_cmd"], cfg.verify_timeout_s)
            if green:
                log(f"Repro {p.get('id')} ZIELONY przy odbiorze — problem odrzucony.")
                p["degraded"] = "repro zielony przy odbiorze"
                dropped.append(p)
    return dropped


def phase_verify_goal(cfg: Config, project: str, state: State, logf) -> bool:
    """Weryfikacja celu (backlog wyczerpany). Zwraca False = koniec pętli
    (PASS), True = cykl naprawczy (planista dostanie feedback). Twarde stopy
    (env_issue, stall, sufit) lecą jako VerificationStop."""
    if state.phase != "verify_goal":
        state.phase = "verify_goal"
        state.verify_cycle += 1
        state.verify_sha = _head_sha(project)
        save_checkpoint(project, state)
    else:
        log(f"WZNAWIAM weryfikację celu (cykl {state.verify_cycle}).")
    n = state.verify_cycle
    cdir = _cycle_dir(project, cfg, n)
    targets = _active_verify_targets(cfg, state)
    log(f"=== WERYFIKACJA CELU (cykl {n}; targety: {', '.join(targets)}) ===")

    evidence = verify.collect_evidence(project, state, cfg, cdir,
                                       sha=state.verify_sha, targets=targets)
    for target, res in sorted(evidence.items()):
        log(f"  dowód {target}: rc={res.get('rc')} → {res.get('log')}")

    problems = _accept_verdict(cfg, project, state, evidence, cdir, logf)

    # Kontrola diffu weryfikatora — jedyna rola bez niej byłaby dziurą:
    # "naprawiony przy okazji" kod poszedłby na remote niezrecenzowany, pod
    # komunikatem docs:. Dozwolone tylko wspólne ścieżki (docs, BACKLOG);
    # artefakty w .forge/verification są odfiltrowane jako runtime.
    stray = [p for p in changed_files(project, "HEAD", runtime_dir=cfg.runtime_dir,
                                      stop_file=cfg.stop_file)
             if not _match_any(p, _SHARED_WRITABLE)]
    if stray:
        log(f"WERYFIKATOR zmienił pliki poza docs/BACKLOG: {stray} — wycofuję "
            "(weryfikator nie pisze kodu produkcyjnego).")
        revert_paths(project, stray)

    _confirm_env_issues(cfg, project, state, problems, cdir, targets)
    dropped = _drop_green_repros(cfg, project, problems)
    active = [p for p in problems if p not in dropped]
    kept, degraded = verify_ledger.degrade_design_gaps(active, _read_design(project))
    blockers = verify_ledger.pass_blockers(evidence, kept)
    notes = degraded + dropped

    # Postęp mierzymy na SUROWYCH statusach agenta — ZANIM degradacje staną
    # się terminalne, żeby "resolved" dopisane przez orkiestrator nie udawało
    # naprawy (fałszywy postęp maskowałby stagnację przed stall-licznikiem).
    progressed = verify_ledger.progress_made(state.verify_problems, problems)
    for p in notes:
        # Terminalnie: zdegradowany wpis nie może zostać otwartym problemem —
        # wymuszałby odhaczanie nie-problemu w cyklu N+1, liczył się jako
        # bloker w porównaniach postępu i dublował notatki w BACKLOG.
        p["status"] = "resolved"
        p.setdefault("resolution",
                     p.get("degraded") or "design_gap bez kryterium z DESIGN.md "
                     "— zdegradowany do notatki")

    if not blockers:
        _note_problems(project, notes, n)
        commit_all(project, f"docs: weryfikacja celu — cykl {n}: PASS", cfg)
        state.verify_problems = problems
        state.verify_stall = 0
        state.phase = "idle"
        save_checkpoint(project, state)
        log("CEL ZWERYFIKOWANY" + (" (nieblokujące notatki w BACKLOG)" if notes else "")
            + " 🎉")
        return False

    # FAIL: utrwal rejestr i raport (pamięć w repo), zmierz postęp, wróć do planisty.
    os.makedirs(cdir, exist_ok=True)
    with open(os.path.join(cdir, "problems.json"), "w", encoding="utf-8") as f:
        json.dump(problems, f, indent=2, ensure_ascii=False)
    feedback = os.path.join(cdir, "feedback.md")
    if not os.path.exists(feedback):  # fallback — agent nie zapisał raportu
        _append_line(feedback,
                     f"# Weryfikacja celu — cykl {n}: FAIL\n\n## Blokery\n"
                     + "".join(f"- {b}\n" for b in blockers)
                     + "\n## Problemy\n"
                     + "".join(f"- {p.get('id')} [{p.get('class')}] {p.get('title', '')}"
                               f" (dowód: {p.get('evidence', '')})\n" for p in kept))
    _note_problems(project, notes, n)
    commit_all(project, f"docs: weryfikacja celu — cykl {n} nieudany (feedback)", cfg)

    state.verify_stall = 0 if progressed else state.verify_stall + 1
    state.verify_problems = problems
    state.phase = "idle"
    save_checkpoint(project, state)
    log(f"WERYFIKACJA: FAIL (blokery: {len(blockers)}, "
        f"postęp: {'tak' if progressed else 'NIE'}) — feedback: {feedback}")
    if state.verify_stall >= cfg.max_stall_cycles:
        raise VerificationStop(
            f"{state.verify_stall} cykle weryfikacji bez postępu — stop. "
            f"Raport: {feedback}", exit_code=5)
    if state.verify_cycle >= cfg.max_verify_cycles:
        raise VerificationStop(
            f"osiągnięto sufit {cfg.max_verify_cycles} cykli weryfikacji — stop. "
            f"Raport: {feedback}", exit_code=5)
    return True


def _task_iteration(cfg: Config, project: str, state: State) -> bool:
    """Jedno zadanie nowego modelu (plan wsadowy → mikro-TDD → recenzja). Wznawialne."""
    n = state.iteration + 1
    log(f"########## ZADANIE (iter {n}) ##########")

    def logf(phase: str) -> str:
        return os.path.join(project, cfg.runtime_dir, "logs", f"task-{n:04d}-{phase}.log")

    if state.phase == "verify_goal":
        if _active_verify_targets(cfg, state):
            return phase_verify_goal(cfg, project, state, logf)  # wznowienie po restarcie
        log("Weryfikacja wyłączona nadpisaniem użytkownika — porzucam fazę verify_goal.")
        _clear_task(state)
        save_checkpoint(project, state)

    if state.phase != "idle" and not state.current_task:
        log("Faza zaawansowana bez bieżącego zadania — reset do planowania.")
        _clear_task(state)

    if state.phase == "idle":
        if not state.task_queue:
            plan = phase_plan_batch(cfg, project, state, logf)
            save_checkpoint(project, state)
            if not state.task_queue:
                if plan.get("no_more_tasks"):
                    if _active_verify_targets(cfg, state):
                        # Backlog pusty ≠ koniec: cel musi przejść weryfikację
                        # w środowisku docelowym (PLAN-3).
                        return phase_verify_goal(cfg, project, state, logf)
                    log("PLAN: brak dalszych zadań — MVP ukończone. 🎉")
                    return False
                # Pusta kolejka BEZ deklaracji końca = usterka planisty (zły JSON,
                # rozjechane ścieżki plików). To błąd do zgłoszenia, nie sukces:
                # cichy return False zakończyłby nocny bieg z kodem 0.
                raise AgentError(
                    "Planista nie zwrócił żadnego wykonalnego zadania "
                    "(pusta kolejka bez no_more_tasks) — sprawdź log fazy plan.")
        _start_task(cfg, project, state)
        save_checkpoint(project, state)
        repro = state.current_task.get("repro_cmd", "")
        if repro:
            # Zadanie naprawcze: repro MUSI być czerwone na starcie ("czerwone
            # najpierw"). Zielone = problem już nie występuje — zadanie jest
            # bezprzedmiotowe; potwierdzi to pełna weryfikacja następnego cyklu.
            green, _ = verify.run_repro(project, repro, cfg.verify_timeout_s)
            state.repro_runs = 1
            if green:
                log(f"Repro zadania '{state.current_task_title}' już ZIELONE na "
                    "starcie — zamykam jako bezprzedmiotowe.")
                journal_append(project, cfg, "repro zielone na starcie — zadanie bezprzedmiotowe")
                _finish_task(cfg, project, state, n)
                return True
    else:
        log(f"WZNAWIAM fazę '{state.phase}': {state.current_task_title}")

    fresh_gate = False
    if state.phase == "micro":
        outcome = _run_micro_loop(cfg, project, state, logf)
        if not outcome:
            reason = (state.fail_reason
                      or f"micro_cap: mikro-TDD nieukończone (cykli={state.micro_cycle})")
            state.fail_reason = ""
            _fail_task(cfg, project, state, n, reason)
            return True
        # Świeża zieleń bramki: zaakceptowane DONE albo eskalacja po limicie mapy
        # (gate zmierzona w policy). Smell no_test nie gatował drzewa.
        fresh_gate = outcome in {"done", "escalate"}

    if state.phase in {"review", "fix_review"}:
        if _run_review_loop(cfg, project, state, logf, gate_green=fresh_gate):
            _finish_task(cfg, project, state, n)
        else:
            _fail_task(cfg, project, state, n,
                       "review: recenzja nie zaakceptowała / bramka czerwona")
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
    except subprocess.CalledProcessError as exc:
        if cfg.legacy_mode or not state.current_task:
            rollback(project)
            clear_checkpoint(state)
            save_checkpoint(project, state)
        else:
            # Nowy model: porażka MUSI wrócić do taga startu zadania — rollback
            # do HEAD zostawiłby niezrecenzowane commity cykli, które poszłyby
            # na remote przy pushu następnego zadania. _fail_task sprząta tag,
            # zapisuje failures.md i czyści stan zadania.
            _fail_task(cfg, project, state, state.iteration + 1,
                       f"błąd polecenia zewnętrznego: {exc}")
        raise


def _print_usage(project: str, runtime_dir: str = ".forge") -> None:
    """Podsumowanie zużycia tokenów na koniec biegu (best-effort)."""
    try:
        from .report import usage_summary
        print("\nZużycie tokenów w tym projekcie:\n"
              + usage_summary(project, runtime_dir))
    except Exception:  # raport nigdy nie psuje kodu wyjścia pętli
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Orkiestrator agentów CLI budujących oprogramowanie (grę lub inny program).")
    ap.add_argument("--brief", default="game.md", help="Plik z briefem produktu.")
    ap.add_argument("--project", default="game", help="Katalog projektu.")
    ap.add_argument("--max-iters", type=int, default=None, help="Limit iteracji (0=bez).")
    ap.add_argument("--sleep", type=parse_start_delay, default=0.0, metavar="CZAS",
                    help="Opóźnij start, np. 30, 30s, 5m albo 2h.")
    ap.add_argument("--planner-agent", default=None,
                    help="Agent planisty: claude, codex lub dowolny (FORGE_AGENT_<NAME>_CMD).")
    # Zachowane, ale ukryte dla zgodności customowych agentów i trybu legacy.
    # Dla znanych agentów nowego modelu stała macierz ma pierwszeństwo.
    ap.add_argument("--planner-model", "--claude-model", dest="planner_model",
                    default=None, help=argparse.SUPPRESS)
    ap.add_argument("--planner-effort", "--claude-effort", dest="planner_effort",
                    default=None, help=argparse.SUPPRESS)
    ap.add_argument("--codex-model", default=None,
                    help="Model implementatora Codeks w trybie --legacy.")
    ap.add_argument("--codex-effort", default=None, choices=CODEX_EFFORTS,
                    help="Effort implementatora Codeks w trybie --legacy.")
    ap.add_argument("--legacy", action="store_true", default=None,
                    help="Stary przebieg plan→implement→review(Claude)→fix.")
    ap.add_argument("--batch-size", type=int, default=None,
                    help="Ile zadań planista przygotowuje jednym wywołaniem (nowy model).")
    ap.add_argument("--max-micro-cycles", type=int, default=None,
                    help="Sufit mikro-cykli TDD na zadanie (nowy model).")
    ap.add_argument("--tester-agent", default=None,
                    help="Agent testera: claude, codex lub dowolny (FORGE_AGENT_<NAME>_CMD).")
    ap.add_argument("--tester-model", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--tester-effort", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--coder-agent", default=None,
                    help="Agent kodera: claude, codex lub dowolny (FORGE_AGENT_<NAME>_CMD).")
    ap.add_argument("--coder-model", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--coder-effort", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--reviewer-agent", default=None,
                    help="Agent recenzenta zadania: claude, codex/gpt, grok, kiro, opencode "
                         "lub dowolny (FORGE_AGENT_<NAME>_CMD). Domyślnie agent testera.")
    ap.add_argument("--reviewer-model", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--reviewer-effort", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--verifier-agent", default=None,
                    help="Agent weryfikatora celu: claude, codex/gpt, grok, kiro, opencode "
                         "lub dowolny (FORGE_AGENT_<NAME>_CMD). Domyślnie agent planisty.")
    ap.add_argument("--verifier-model", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--verifier-effort", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--non-interactive", action="store_true",
                    help="Nie pytaj o agentów; użyj flag/env/dom wartości.")
    ap.add_argument("--check", action="store_true", help="Tylko preflight i wyjście.")
    args = ap.parse_args(argv)

    cfg = Config()
    cfg.brief_path = args.brief
    cfg.project_dir = args.project
    if args.max_iters is not None:
        cfg.max_iterations = args.max_iters
    if args.planner_agent and args.planner_agent != cfg.planner_agent:
        cfg.planner_agent = args.planner_agent
        canon = adapters.canonical_agent(args.planner_agent)
        if not args.planner_model:
            cfg.planner_model = {"claude": "opus", "codex": cfg.codex_model}.get(
                canon, "")
        if not args.planner_effort:
            cfg.planner_effort = {"claude": "high", "codex": cfg.codex_effort}.get(
                canon, "medium")
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
    if args.tester_agent:
        cfg.tester_agent = args.tester_agent
    if args.tester_model:
        cfg.tester_model = args.tester_model
    if args.tester_effort:
        cfg.tester_effort = args.tester_effort
    if args.coder_agent:
        cfg.coder_agent = args.coder_agent
    if args.coder_model:
        cfg.coder_model = args.coder_model
    if args.coder_effort:
        cfg.coder_effort = args.coder_effort
    if args.reviewer_agent:
        cfg.reviewer_agent = args.reviewer_agent
    if args.reviewer_model:
        cfg.reviewer_model = args.reviewer_model
    if args.reviewer_effort:
        cfg.reviewer_effort = args.reviewer_effort
    if args.verifier_agent:
        cfg.verifier_agent = args.verifier_agent
    if args.verifier_model:
        cfg.verifier_model = args.verifier_model
    if args.verifier_effort:
        cfg.verifier_effort = args.verifier_effort

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
        # Każdy agent w agents_in_use() jest wymagany przez którąś rolę — brak
        # jego binarki to twardy błąd (inaczej pętla padnie dopiero w środku
        # zadania, po zmarnowaniu bootstrapu/planowania).
        return 2
    else:
        log("PREFLIGHT OK.")
    if cfg.legacy_mode:
        log("TRYB: legacy (plan → implement → review[Claude] → fix).")
    else:
        log(f"TRYB: mikro-TDD (plan wsadowy {cfg.batch_size} → tester↔koder → recenzja); "
            f"agenci: planista={cfg.planner_agent}, tester={cfg.tester_agent}, "
            f"koder={cfg.coder_agent}; sufit mikro-cykli={cfg.max_micro_cycles}.")
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
        _print_usage(project, cfg.runtime_dir)
        return 3
    except VerificationStop as e:
        log(f"WERYFIKACJA ZATRZYMAŁA BIEG: {e}")
        log("Stan zapisany — po naprawie środowiska/przeglądzie raportu uruchom ponownie.")
        state.save(state_path)
        _print_usage(project, cfg.runtime_dir)
        return e.exit_code
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
    _print_usage(project, cfg.runtime_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
