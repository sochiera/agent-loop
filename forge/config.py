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


TASK_DIFFICULTIES = ("simple", "standard", "complex")
DEFAULT_TASK_DIFFICULTY = "standard"

# Użytkownik wybiera narzędzie/agenta dla roli. Konkretny model i effort są
# polityką projektu, nie pokrętłem pojedynczego uruchomienia. Dzięki temu
# wznowienie zadania odtwarza ten sam routing bez zależności od interaktywnej
# konfiguracji. Planner i verifier celowo nie tanieją wraz z profilem zadania:
# ich odpowiedzialność obejmuje odpowiednio cały plan i cały produkt.
ROLE_ROUTING: dict[str, dict[str, dict[str, tuple[str, str]]]] = {
    "codex": {
        "planner": {d: ("gpt-5.6-sol", "high") for d in TASK_DIFFICULTIES},
        "tester": {
            "simple": ("gpt-5.6-terra", "medium"),
            "standard": ("gpt-5.6-terra", "medium"),
            "complex": ("gpt-5.6-sol", "medium"),
        },
        "coder": {
            "simple": ("gpt-5.6-luna", "medium"),
            "standard": ("gpt-5.6-terra", "low"),
            "complex": ("gpt-5.6-terra", "medium"),
        },
        "reviewer": {d: ("gpt-5.6-sol", "medium") for d in TASK_DIFFICULTIES},
        "verifier": {d: ("gpt-5.6-terra", "medium") for d in TASK_DIFFICULTIES},
    },
    "claude": {
        "planner": {d: ("opus", "high") for d in TASK_DIFFICULTIES},
        "tester": {
            "simple": ("sonnet", "medium"),
            "standard": ("sonnet", "high"),
            "complex": ("opus", "high"),
        },
        "coder": {
            "simple": ("sonnet", "low"),
            "standard": ("sonnet", "medium"),
            "complex": ("opus", "medium"),
        },
        "reviewer": {
            "simple": ("sonnet", "high"),
            "standard": ("sonnet", "high"),
            "complex": ("opus", "high"),
        },
        "verifier": {d: ("sonnet", "high") for d in TASK_DIFFICULTIES},
    },
    "grok": {
        "planner": {d: ("grok-4.5", "high") for d in TASK_DIFFICULTIES},
        "tester": {
            "simple": ("grok-4.5", "low"),
            "standard": ("grok-4.5", "medium"),
            "complex": ("grok-4.5", "high"),
        },
        "coder": {
            "simple": ("grok-4.5", "low"),
            "standard": ("grok-4.5", "medium"),
            "complex": ("grok-4.5", "high"),
        },
        "reviewer": {
            "simple": ("grok-4.5", "medium"),
            "standard": ("grok-4.5", "high"),
            "complex": ("grok-4.5", "high"),
        },
        "verifier": {d: ("grok-4.5", "high") for d in TASK_DIFFICULTIES},
    },
    "opencode": {
        "planner": {
            d: ("neuralwatt/qwen3.5-397b", "") for d in TASK_DIFFICULTIES
        },
        "tester": {
            "simple": ("neuralwatt/glm-5.2-short-fast-flex", "low"),
            "standard": ("neuralwatt/glm-5.2-flex", "medium"),
            "complex": ("neuralwatt/glm-5.2-flex", "high"),
        },
        "coder": {
            "simple": ("neuralwatt/kimi-k2.7-code-flex", ""),
            "standard": ("neuralwatt/kimi-k2.7-code-flex", ""),
            "complex": ("neuralwatt/kimi-k2.7-code-flex", ""),
        },
        "reviewer": {
            "simple": ("neuralwatt/glm-5.2-flex", "medium"),
            "standard": ("neuralwatt/glm-5.2-flex", "high"),
            "complex": ("neuralwatt/glm-5.2-flex", "high"),
        },
        "verifier": {
            d: ("neuralwatt/qwen3.5-397b", "") for d in TASK_DIFFICULTIES
        },
    },
    # Kiro sam zarządza wyborem modelu; puste wartości nie dodają flag CLI.
    "kiro": {
        role: {d: ("", "") for d in TASK_DIFFICULTIES}
        for role in ("planner", "tester", "coder", "reviewer", "verifier")
    },
}


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
    # (patrz adapters.py). Domyślnie tester i koder to opencode (NeuralWatt).
    tester_agent: str = os.environ.get("FORGE_TESTER_AGENT", "opencode")
    coder_agent: str = os.environ.get("FORGE_CODER_AGENT", "opencode")
    # Model/effort ról. Puste → agent użyje swojego domyślnego (codex: config.toml).
    tester_model: str = os.environ.get("FORGE_TESTER_MODEL", "neuralwatt/glm-5.2-short-fast-flex")
    tester_effort: str = os.environ.get("FORGE_TESTER_EFFORT", "")
    coder_model: str = os.environ.get("FORGE_CODER_MODEL", "neuralwatt/kimi-k2.7-code-flex")
    coder_effort: str = os.environ.get("FORGE_CODER_EFFORT", "")

    # --- Weryfikacja celu (PLAN-3) -------------------------------------------
    # Weryfikator-QA: pusty agent = rola planisty (ocena całości to zadanie
    # mocnego modelu). Jawny agent konfiguruje się jak tester/koder.
    verifier_agent: str = os.environ.get("FORGE_VERIFIER_AGENT", "opencode")
    verifier_model: str = os.environ.get("FORGE_VERIFIER_MODEL", "neuralwatt/qwen3.5-397b")
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
    reviewer_agent: str = os.environ.get("FORGE_REVIEWER_AGENT", "opencode")
    reviewer_model: str = os.environ.get("FORGE_REVIEWER_MODEL", "neuralwatt/glm-5.2-flex")
    reviewer_effort: str = os.environ.get("FORGE_REVIEWER_EFFORT", "")
    # Rotacja sesji ról co K ukończonych mikro-cykli (0 = wyłączona) —
    # higiena kontekstu: świeża sesja z dziennikiem zamiast spuchniętej.
    session_rotate_cycles: int = int(os.environ.get("FORGE_SESSION_ROTATE_CYCLES", "4"))
    # Sufit skrótu dziennika doklejanego do promptu (pełny dziennik na dysku).
    journal_tail_chars: int = int(os.environ.get("FORGE_JOURNAL_TAIL_CHARS", "8000"))
    # Blokada zapisu (chmod a-w) testów cyklu na czas tury kodera — tani
    # deterrent, NIE bariera; właściwą bramką pozostaje kontrola diffu.
    lock_tests: bool = os.environ.get("FORGE_LOCK_TESTS", "1") != "0"
    # Porażka zadania czyści resztę kolejki wsadu (plan był budowany przy
    # założeniu sukcesu) — planista przeplanowuje z failures.md.
    replan_on_failure: bool = os.environ.get("FORGE_REPLAN_ON_FAILURE", "1") != "0"

    # --- PLAN-5: bramka DONE / kanon kryteriów / failed-ref -------------------
    # Po tylu kolejnych odrzuceniach mapy kryteriów przy DONE — eskalacja
    # (policy), zamiast palić cały max_micro_cycles na poprawianie JSON-a.
    max_done_rejects: int = int(os.environ.get("FORGE_MAX_DONE_REJECTS", "3"))
    # review_if_green | fail | continue — patrz docs/PLAN-5-DONE-KRYTERIA-I-PĘTLE.md.
    done_reject_policy: str = os.environ.get(
        "FORGE_DONE_REJECT_POLICY", "review_if_green").strip().lower() or "review_if_green"
    # Przed rollbackiem przy porażce: branch forge/failed/<id> na HEAD (+ residual commit).
    keep_failed_ref: bool = os.environ.get("FORGE_KEEP_FAILED_REF", "1") != "0"
    # Fail zadania już na starcie, gdy nie ma kryteriów w pliku ani w JSON planisty.
    fail_on_empty_criteria: bool = os.environ.get("FORGE_FAIL_ON_EMPTY_CRITERIA", "0") == "1"

    # --- Higiena docs/DESIGN.md: kompaktowanie zamiast okresowego refaktoru --
    # Próg rozmiaru DESIGN.md (bajty) — po przekroczeniu PLAN WSADOWY (nowy
    # model, phase_plan_batch) dostaje polecenie wstawienia jednego zadania
    # kompaktującego (ROZSTRZYGNIĘTE → docs/DECISIONS.md, DESIGN.md zostaje
    # opisem stanu obecnego). 0 = wyłączone.
    # UWAGA: dotyczy WYŁĄCZNIE legacy_mode=False — stary przebieg (phase_plan)
    # nie sprawdza tego progu i DESIGN.md może tam rosnąć bez ograniczeń.
    design_compact_bytes: int = int(os.environ.get("FORGE_DESIGN_COMPACT_BYTES", "40000"))

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

    def role(
        self, name: str, difficulty: str = DEFAULT_TASK_DIFFICULTY
    ) -> tuple[str, str, str]:
        """Zwróć ``(agent, model, effort)`` z ustalonej polityki routingu.

        Nieznane/customowe CLI zachowują zgodność wsteczną i korzystają z pól
        ``*_model``/``*_effort``. Brak profilu w starym STATE.json oznacza
        bezpieczne ``standard``.
        """
        if difficulty not in TASK_DIFFICULTIES:
            difficulty = DEFAULT_TASK_DIFFICULTY

        configured: dict[str, tuple[str, str, str]] = {
            "planner": (self.planner_agent, self.planner_model, self.planner_effort),
            "tester": (self.tester_agent, self.tester_model, self.tester_effort),
            "coder": (self.coder_agent, self.coder_model, self.coder_effort),
        }
        if name == "verifier":
            if not self.verifier_agent:  # domyślnie rola planisty w całości
                return self.role("planner", difficulty)
            configured[name] = (
                self.verifier_agent, self.verifier_model, self.verifier_effort
            )
        elif name == "reviewer":
            agent = self.reviewer_agent or self.tester_agent
            t_agent, t_model, t_effort = self.role("tester", difficulty)
            same_tool = adapters.canonical_agent(agent) == adapters.canonical_agent(t_agent)
            model = self.reviewer_model or (t_model if same_tool else "")
            effort = self.reviewer_effort or (t_effort if same_tool else "")
            configured[name] = (agent, model, effort)

        if name not in configured:
            raise ValueError(f"nieznana rola: {name}")

        agent, legacy_model, legacy_effort = configured[name]
        canonical = adapters.canonical_agent(agent)
        fixed = ROLE_ROUTING.get(canonical, {}).get(name, {}).get(difficulty)
        if fixed is not None:
            return (agent, *fixed)
        return (
            agent,
            *self._role_model_effort(agent, legacy_model, legacy_effort),
        )

    def tester(self) -> tuple[str, str]:
        """(model, effort) testera — zgodność wsteczna; patrz role('tester')."""
        return self.role("tester")[1:]

    def coder(self) -> tuple[str, str]:
        """(model, effort) kodera — zgodność wsteczna; patrz role('coder')."""
        return self.role("coder")[1:]

    def agents_in_use(self) -> list[str]:
        """Agenci CLI faktycznie używani w bieżącym trybie (do preflightu).

        Deduplikacja po nazwie KANONICZNEJ — 'gpt' i 'codex' to ta sama binarka,
        więc preflight nie sprawdza jej dwa razy (i nie dubluje komunikatu o
        braku). Zachowujemy pierwszą napotkaną nazwę wyświetlaną (dla logów)."""
        if self.legacy_mode:
            names = [self.planner_agent, "codex"]
        else:
            names = [self.planner_agent, self.tester_agent, self.coder_agent,
                     self.role("verifier")[0], self.role("reviewer")[0]]
        seen: dict[str, str] = {}
        for name in names:
            seen.setdefault(adapters.canonical_agent(name), name)
        return list(seen.values())

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
