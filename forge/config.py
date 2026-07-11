"""Konfiguracja orkiestratora.

Wszystkie pokrętła w jednym miejscu — modele, komendy CLI, tryb sandboxa,
limity iteracji i wzorce wykrywania wyczerpanych limitów subskrypcji.

Zasada: narzędzie jest STACK-AGNOSTYCZNE. Nie zna Pythona ani Wesnotha.
Komendy build/test gry ustala agent podczas bootstrapu i zapisuje je w
STATE.json (patrz state.py), a nie tutaj.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


_DEFAULT_PLANNER_AGENT = os.environ.get("FORGE_PLANNER_AGENT", "claude")
_DEFAULT_PLANNER_MODEL = os.environ.get(
    "FORGE_PLANNER_MODEL",
    os.environ.get("FORGE_CLAUDE_MODEL", "opus")
    if _DEFAULT_PLANNER_AGENT == "claude"
    else os.environ.get("FORGE_CODEX_MODEL", ""),
)
_DEFAULT_PLANNER_EFFORT = os.environ.get(
    "FORGE_PLANNER_EFFORT",
    os.environ.get("FORGE_CLAUDE_EFFORT", "high")
    if _DEFAULT_PLANNER_AGENT == "claude"
    else os.environ.get("FORGE_CODEX_EFFORT", "medium"),
)


# --- Wykrywanie wyczerpanych limitów / błędów przejściowych -----------------
# Gdy trafimy na którykolwiek z tych wzorców w wyjściu CLI (przy niezerowym
# kodzie wyjścia), traktujemy to jako "limit/błąd przejściowy" i robimy backoff
# zamiast wywalać pętlę.
RATE_LIMIT_PATTERNS = [
    r"usage limit",
    r"rate limit",
    r"rate[_-]?limited",
    r"quota",
    r"too many requests",
    r"\b429\b",
    r"overloaded",
    r"please try again",
    r"temporarily unavailable",
    r"service unavailable",
    r"\b503\b",
    r"resets? at",
]


@dataclass
class AgentCmd:
    """Opis jak wywołać jeden CLI-agent."""

    # Bazowa komenda (lista argv, bez shella). Nadpisywalna zmienną środowiskową.
    argv: list[str]
    # Model dla tego agenta (nazwa przekazywana do -m/--model danego CLI).
    model: str
    # Poziom namysłu przekazywany jawnie do CLI.
    effort: str


@dataclass
class Config:
    # Katalog projektu gry (tam powstaje kod, docs, git repo).
    project_dir: str = "game"
    # Plik z briefem gry (wejście od użytkownika).
    brief_path: str = "game.md"

    # --- Modele -------------------------------------------------------------
    # Planista obsługuje bootstrap, planowanie i review; może nim być Claude
    # albo Codex, niezależnie od Codex-implementatora.
    planner_agent: str = _DEFAULT_PLANNER_AGENT
    planner_model: str = _DEFAULT_PLANNER_MODEL
    planner_effort: str = _DEFAULT_PLANNER_EFFORT
    # Pusty = użyj modelu skonfigurowanego w ~/.codex/config.toml (Twój: gpt-5.6-sol).
    # Nadpisz tylko jeśli chcesz świadomie zmienić model dla tej pętli.
    codex_model: str = os.environ.get("FORGE_CODEX_MODEL", "")
    codex_effort: str = os.environ.get("FORGE_CODEX_EFFORT", "medium")

    # --- Komendy bazowe CLI (bez shella) ------------------------------------
    # Claude Code headless. Jeśli 'claude' nie jest na PATH, ustaw FORGE_CLAUDE_BIN.
    claude_bin: str = os.environ.get("FORGE_CLAUDE_BIN", "claude")
    # Codex CLI.
    codex_bin: str = os.environ.get("FORGE_CODEX_BIN", "codex")

    # Tryb sandboxa Codeksa: read-only | workspace-write | danger-full-access.
    # Domyślnie PEŁNY DOSTĘP (cały FS + sieć, bez zatwierdzania) — pod parę z
    # Claude'em na --dangerously-skip-permissions. Zawęź przez FORGE_CODEX_SANDBOX
    # (np. workspace-write), jeśli chcesz ograniczyć agenta do katalogu projektu.
    codex_sandbox: str = os.environ.get("FORGE_CODEX_SANDBOX", "danger-full-access")

    # --- Push do zdalnego repo gry -----------------------------------------
    # Po każdym udanym commicie orkiestrator pcha bieżący branch do remote.
    # Wyłącz przez FORGE_GIT_PUSH=0 (np. gdy chcesz najpierw obejrzeć lokalnie).
    git_push: bool = os.environ.get("FORGE_GIT_PUSH", "1") != "0"
    git_remote: str = os.environ.get("FORGE_GIT_REMOTE", "origin")

    # --- Sterowanie pętlą ---------------------------------------------------
    max_iterations: int = int(os.environ.get("FORGE_MAX_ITERS", "0"))  # 0 = bez limitu
    max_fix_attempts: int = 3          # ile rund review→fix na jedno zadanie
    # Backoff przy limitach (sekundy): rośnie geometrycznie do sufitu.
    backoff_start_s: int = 60
    backoff_max_s: int = 3600
    backoff_factor: float = 2.0
    # Ile razy ponawiać jedną fazę przy limicie zanim uznamy limit za wyczerpany.
    max_limit_retries: int = 6

    # Timeout pojedynczego wywołania agenta (sekundy). Duże, bo TDD bywa długie.
    agent_timeout_s: int = int(os.environ.get("FORGE_AGENT_TIMEOUT", "3600"))

    # Nazwa pliku-stopu: utwórz go w project_dir, by grzecznie zatrzymać pętlę.
    stop_file: str = "STOP"

    # Katalog runtime orkiestratora wewnątrz projektu (logi, bieżące zadanie).
    runtime_dir: str = ".forge"

    def codex(self) -> AgentCmd:
        return AgentCmd(argv=[self.codex_bin], model=self.codex_model,
                        effort=self.codex_effort)
