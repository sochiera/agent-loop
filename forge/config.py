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


@dataclass
class Config:
    # Katalog projektu gry (tam powstaje kod, docs, git repo).
    project_dir: str = "game"
    # Plik z briefem gry (wejście od użytkownika).
    brief_path: str = "game_brief.md"

    # --- Modele -------------------------------------------------------------
    # Opus do wszystkiego po stronie Claude (decyzja użytkownika). Zmień na
    # "sonnet" gdy limit Pro zacznie boleć.
    claude_model: str = os.environ.get("FORGE_CLAUDE_MODEL", "opus")
    # Pusty = użyj modelu skonfigurowanego w ~/.codex/config.toml (Twój: gpt-5.6-sol).
    # Nadpisz tylko jeśli chcesz świadomie zmienić model dla tej pętli.
    codex_model: str = os.environ.get("FORGE_CODEX_MODEL", "")

    # --- Komendy bazowe CLI (bez shella) ------------------------------------
    # Claude Code headless. Jeśli 'claude' nie jest na PATH, ustaw FORGE_CLAUDE_BIN.
    claude_bin: str = os.environ.get("FORGE_CLAUDE_BIN", "claude")
    # Codex CLI.
    codex_bin: str = os.environ.get("FORGE_CODEX_BIN", "codex")

    # Tryb sandboxa Codeksa: read-only | workspace-write | danger-full-access.
    # workspace-write pozwala pisać w katalogu projektu; jeśli instalacja
    # zależności wymaga sieci, może być potrzebny danger-full-access.
    codex_sandbox: str = os.environ.get("FORGE_CODEX_SANDBOX", "workspace-write")

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

    def claude(self) -> AgentCmd:
        return AgentCmd(argv=[self.claude_bin], model=self.claude_model)

    def codex(self) -> AgentCmd:
        return AgentCmd(argv=[self.codex_bin], model=self.codex_model)
