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

from . import adapters


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

    # --- Nowy model: mikro-TDD ping-pong (Codex-tester ↔ Codex-koder) -------
    # legacy_mode=False → pętla docelowa (plan wsadowy → mikro-TDD → review Codeksa).
    # legacy_mode=True  → stary przebieg plan→implement→review(Claude)→fix.
    legacy_mode: bool = os.environ.get("FORGE_LEGACY_MODE", "0") == "1"
    # Ile zadań planista produkuje jednym wywołaniem (koszt stały planisty ÷ batch).
    batch_size: int = int(os.environ.get("FORGE_BATCH_SIZE", "5"))
    # Twardy sufit mikro-cykli (test→kod→refaktor) na jedno zadanie: chroni budżet
    # i rozmiar rosnącej sesji. Po przekroczeniu — porażka zadania i podział przez planistę.
    max_micro_cycles: int = int(os.environ.get("FORGE_MAX_MICRO_CYCLES", "12"))
    # Ile dogrywek „zazielenienia" w obrębie jednego mikro-cyklu, zanim zadanie padnie.
    max_green_retries: int = int(os.environ.get("FORGE_MAX_GREEN_RETRIES", "2"))
    # Agent CLI każdej roli nowego modelu. "claude"/"codex" mają wbudowaną
    # obsługę; dowolna inna nazwa → agent generyczny z FORGE_AGENT_<NAME>_CMD
    # (patrz adapters.py). Domyślnie tester i koder to codex.
    tester_agent: str = os.environ.get("FORGE_TESTER_AGENT", "codex")
    coder_agent: str = os.environ.get("FORGE_CODER_AGENT", "codex")
    # Model/effort ról. Puste → agent użyje swojego domyślnego (codex: config.toml).
    tester_model: str = os.environ.get("FORGE_TESTER_MODEL", "")
    tester_effort: str = os.environ.get("FORGE_TESTER_EFFORT", "")
    coder_model: str = os.environ.get("FORGE_CODER_MODEL", "")
    coder_effort: str = os.environ.get("FORGE_CODER_EFFORT", "")

    # --- Weryfikacja celu (PLAN-3) -------------------------------------------
    # Weryfikator-QA: pusty agent = rola planisty (ocena całości to zadanie
    # mocnego modelu). Jawny agent konfiguruje się jak tester/koder.
    verifier_agent: str = os.environ.get("FORGE_VERIFIER_AGENT", "")
    verifier_model: str = os.environ.get("FORGE_VERIFIER_MODEL", "")
    verifier_effort: str = os.environ.get("FORGE_VERIFIER_EFFORT", "")
    # Nadpisanie targetów z bootstrapu: "" = decyduje bootstrap, "none" =
    # weryfikacja wyłączona, "ci,hardware" = dokładnie te targety.
    verify_targets_override: str = os.environ.get("FORGE_VERIFY_TARGETS", "")
    # Bezpiecznik absolutny cykli; głównym mechanizmem stopu jest stall
    # (kolejne cykle bez postępu wg verify_ledger.progress_made).
    max_verify_cycles: int = int(os.environ.get("FORGE_MAX_VERIFY_CYCLES", "8"))
    max_stall_cycles: int = int(os.environ.get("FORGE_MAX_STALL_CYCLES", "2"))
    # Polling CI: backoff start→sufit; timeout całego oczekiwania na werdykt CI.
    ci_timeout_s: int = int(os.environ.get("FORGE_CI_TIMEOUT", "2700"))
    ci_poll_start_s: int = int(os.environ.get("FORGE_CI_POLL_START", "30"))
    ci_poll_max_s: int = int(os.environ.get("FORGE_CI_POLL_MAX", "300"))
    # Timeout pojedynczej komendy weryfikacji (smoke/flash/target/repro).
    verify_timeout_s: int = int(os.environ.get("FORGE_VERIFY_TIMEOUT", "1800"))
    # Flash bywa flaky z natury (USB) — darmowe ponowienia przed diagnozą.
    flash_retries: int = int(os.environ.get("FORGE_FLASH_RETRIES", "1"))
    # Sufit uruchomień repro w jednym zadaniu naprawczym (chroni sprzęt i czas).
    max_repro_runs_per_task: int = int(os.environ.get("FORGE_MAX_REPRO_RUNS", "6"))
    # Tani ci_status_cmd HEAD przy każdym planowaniu (ostrzeżenie w prompcie).
    ci_early_warn: bool = os.environ.get("FORGE_CI_EARLY_WARN", "1") != "0"
    # Plik konfiguracji MCP doklejany do claude TYLKO w roli weryfikatora.
    verifier_mcp_config: str = os.environ.get("FORGE_VERIFIER_MCP_CONFIG", "")

    # --- PLAN-4: uszczelnienie bramek i higiena kontekstu --------------------
    # Dodatkowe globy toolchainu testowego (CSV), doklejane do heurystyki
    # wbudowanej i deklaracji bootstrapu (State.test_toolchain_globs).
    toolchain_globs_extra: str = os.environ.get("FORGE_TOOLCHAIN_GLOBS", "")
    # Recenzent zadania: pusty agent = agent testera, ale ZAWSZE świeży
    # kontekst (bez sesji i dziennika) — autor nie recenzuje własnej pracy.
    reviewer_agent: str = os.environ.get("FORGE_REVIEWER_AGENT", "")
    reviewer_model: str = os.environ.get("FORGE_REVIEWER_MODEL", "")
    reviewer_effort: str = os.environ.get("FORGE_REVIEWER_EFFORT", "")
    # Rotacja sesji ról co K ukończonych mikro-cykli (0 = wyłączona) —
    # higiena kontekstu: świeża sesja z dziennikiem zamiast spuchniętej.
    session_rotate_cycles: int = int(os.environ.get("FORGE_SESSION_ROTATE_CYCLES", "6"))
    # Sufit skrótu dziennika doklejanego do promptu (pełny dziennik na dysku).
    journal_tail_chars: int = int(os.environ.get("FORGE_JOURNAL_TAIL_CHARS", "8000"))
    # Blokada zapisu (chmod a-w) testów cyklu na czas tury kodera — tani
    # deterrent, NIE bariera; właściwą bramką pozostaje kontrola diffu.
    lock_tests: bool = os.environ.get("FORGE_LOCK_TESTS", "1") != "0"
    # Porażka zadania czyści resztę kolejki wsadu (plan był budowany przy
    # założeniu sukcesu) — planista przeplanowuje z failures.md.
    replan_on_failure: bool = os.environ.get("FORGE_REPLAN_ON_FAILURE", "1") != "0"

    def effective_verify_targets(self, declared: list[str]) -> list[str]:
        """Targety po nadpisaniu użytkownika ("" = deklaracja bootstrapu)."""
        override = self.verify_targets_override.strip().lower()
        if override == "none":
            return []
        if override:
            return [t.strip() for t in override.split(",") if t.strip()]
        return declared

    def _role_model_effort(self, agent: str, model: str, effort: str) -> tuple[str, str]:
        # Dla codeksa (i aliasu "gpt") puste pola dziedziczą globalne
        # codex_model/effort (jego naturalny default); dla innych agentów
        # puste = niech agent sam wybierze.
        if adapters.canonical_agent(agent) == "codex":
            return (model or self.codex_model, effort or self.codex_effort)
        return (model, effort)

    def role(self, name: str) -> tuple[str, str, str]:
        """(agent, model, effort) dla roli: 'planner' | 'tester' | 'coder'."""
        if name == "planner":
            return (self.planner_agent,
                    *self._role_model_effort(self.planner_agent, self.planner_model, self.planner_effort))
        if name == "tester":
            return (self.tester_agent,
                    *self._role_model_effort(self.tester_agent, self.tester_model, self.tester_effort))
        if name == "coder":
            return (self.coder_agent,
                    *self._role_model_effort(self.coder_agent, self.coder_model, self.coder_effort))
        if name == "verifier":
            if not self.verifier_agent:  # domyślnie rola planisty w całości
                return self.role("planner")
            return (self.verifier_agent,
                    *self._role_model_effort(self.verifier_agent,
                                             self.verifier_model, self.verifier_effort))
        if name == "reviewer":
            # Domyślnie agent testera, ALE świeży kontekst. reviewer_model/effort
            # muszą działać nawet bez jawnego reviewer_agent — inaczej
            # FORGE_REVIEWER_MODEL/EFFORT z README są po cichu ignorowane.
            agent = self.reviewer_agent or self.tester_agent
            t_agent, t_model, t_effort = self.role("tester")
            model = self.reviewer_model or (t_model if agent == t_agent else "")
            effort = self.reviewer_effort or (t_effort if agent == t_agent else "")
            return (agent, *self._role_model_effort(agent, model, effort))
        raise ValueError(f"nieznana rola: {name}")

    def tester(self) -> tuple[str, str]:
        """(model, effort) testera — zgodność wsteczna; patrz role('tester')."""
        return self.role("tester")[1:]

    def coder(self) -> tuple[str, str]:
        """(model, effort) kodera — zgodność wsteczna; patrz role('coder')."""
        return self.role("coder")[1:]

    def agents_in_use(self) -> list[str]:
        """Agenci CLI faktycznie używani w bieżącym trybie (do preflightu)."""
        if self.legacy_mode:
            return list(dict.fromkeys([self.planner_agent, "codex"]))
        return list(dict.fromkeys([self.planner_agent, self.tester_agent,
                                   self.coder_agent, self.role("verifier")[0],
                                   self.role("reviewer")[0]]))

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
