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
MODEL_LEVELS = ("economy", "efficient", "balanced", "strong", "max")

# Trudność opisuje zakres zadania, a poziom modelu politykę routingu providera.
ROLE_MODEL_LEVELS: dict[str, dict[str, str]] = {
    "bootstrap": {d: "max" for d in TASK_DIFFICULTIES},
    # Synchronizacja briefu nie ma osobnego review, więc rozstrzyga ją
    # najsilniejszy model — myli się raz, a skutek niesie cały dalszy plan.
    "diff_bootstrap": {d: "max" for d in TASK_DIFFICULTIES},
    "planner": {d: "strong" for d in TASK_DIFFICULTIES},
    "planner_escalation": {d: "max" for d in TASK_DIFFICULTIES},
    "tester": {"simple": "efficient", "standard": "balanced", "complex": "balanced"},
    "coder": {"simple": "economy", "standard": "efficient", "complex": "balanced"},
    "reviewer": {"simple": "efficient", "standard": "balanced", "complex": "strong"},
    "verifier": {"simple": "economy", "standard": "efficient", "complex": "balanced"},
    # Mistrz czyta kilkadziesiąt krótkich linii dziennika i nigdy nie czyta
    # kodu — to rozpoznawanie wzorca, nie rozumowanie o implementacji. Wołany
    # co rundę, więc musi być tani, inaczej odtworzyłby problem kosztowy,
    # który ma pomagać wykrywać. Dno drabinki mu wystarcza: jako jedyna rola
    # pracuje bez narzędzi i bez pętli agentowej (tryb cienki w agents.py),
    # więc model nie musi tu nic planować ani czytać drzewa.
    "master": {d: "economy" for d in TASK_DIFFICULTIES},
}

# Użytkownik wybiera narzędzie/agenta dla roli. Konkretny model i effort są
# polityką projektu, nie pokrętłem pojedynczego uruchomienia. Dzięki temu
# wznowienie zadania odtwarza ten sam routing bez zależności od interaktywnej
# konfiguracji. Poziom modelu wynika osobno z roli i trudności zadania.
MODEL_LEVEL_ROUTING: dict[str, dict[str, tuple[str, str]]] = {
    "codex": {
        "economy": ("gpt-5.6-luna", "low"),
        "efficient": ("gpt-5.6-terra", "low"),
        "balanced": ("gpt-5.6-terra", "medium"),
        "strong": ("gpt-5.6-sol", "medium"),
        "max": ("gpt-5.6-sol", "high"),
    },
    "claude": {
        "economy": ("haiku", ""),
        "efficient": ("sonnet", "low"),
        "balanced": ("opus", "low"),
        "strong": ("opus", "medium"),
        "max": ("opus", "high"),
    },
    "grok": {
        "economy": ("grok-4.5", "low"),
        "efficient": ("grok-4.5", "low"),
        "balanced": ("grok-4.5", "medium"),
        "strong": ("grok-4.5", "high"),
        "max": ("grok-4.5", "high"),
    },
    # NeuralWatt (przez opencode). Dwie osie doboru, obie liczą się osobno:
    #   1) rodzina modelu — rośnie z poziomem, bo tego wymaga zadanie;
    #   2) wariant — ZAWSZE najtańszy, który wystarcza:
    #      -flex  = tańsza kolejka; pętla jest wsadowa, więc bierzemy zawsze
    #               gdy istnieje (nie ma jej gemma/qwen);
    #      -fast  = bez rozumowania, czyli bez tokenów myślenia — tam, gdzie
    #               rola i tak nie planuje (mistrz, proste zadania);
    #      -short = mniejsze okno; bierzemy, gdy 1M kontekstu jest zbędne.
    # Blok "cost" w ~/.config/opencode/opencode.json podaje tę samą stawkę dla
    # wszystkich wariantów rodziny — to uproszczenie metadanych, nie rachunek.
    # Prawdziwy koszt schodzi z każdym sufiksem, więc nie optymalizuj po tych
    # liczbach; służą tylko do porównywania RODZIN między sobą.
    #   economy   qwen3.6-35b-fast       mistrz i proste zadania; zmierzone na
    #                                    realnym dzienniku: łapie wzorzec pętli
    #                                    ~100 tokenami wyjścia, gdy kimi-k2.6
    #                                    wypisuje na to samo ~3000
    #   efficient kimi-k2.6-flex         workhorse: 262k, rozumowanie, tania kolejka
    #   balanced  kimi-k2.7-code-flex    specjalista od kodu tam, gdzie koder
    #                                    i tester robią realną robotę
    #   strong    glm-5.2-short-flex     recenzja: szerokość zamiast 1M okna
    #   max       glm-5.2-flex           bootstrap: pełne 1M kontekstu
    # --variant obsługuje tylko rodzina glm-5.2, więc reszta ma pusty effort
    # (adapters.py wycina pusty placeholder razem z flagą).
    "opencode": {
        "economy": ("neuralwatt/qwen3.6-35b-fast", ""),
        "efficient": ("neuralwatt/kimi-k2.6-flex", ""),
        "balanced": ("neuralwatt/kimi-k2.7-code-flex", ""),
        "strong": ("neuralwatt/glm-5.2-short-flex", "medium"),
        "max": ("neuralwatt/glm-5.2-flex", "high"),
    },
    # Kiro użyje tych wartości tylko, gdy jego szablon CLI zawiera {model}/{effort}.
    "kiro": {
        "economy": ("sonnet-4.6", "low"),
        "efficient": ("sonnet-4.6", "medium"),
        "balanced": ("sonnet-4.6", "high"),
        "strong": ("opus-4.6", "medium"),
        "max": ("opus-4.6", "high"),
    },
}


_DEFAULT_PLANNER_AGENT = os.environ.get("FORGE_PLANNER_AGENT", "claude")
_DEFAULT_PLANNER_MODEL = os.environ.get("FORGE_PLANNER_MODEL", "")
_DEFAULT_PLANNER_EFFORT = os.environ.get("FORGE_PLANNER_EFFORT", "")


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

    # Ile zadań planista produkuje jednym wywołaniem (koszt stały planisty ÷ batch).
    batch_size: int = int(os.environ.get("FORGE_BATCH_SIZE", "4"))
    # Mały bezpiecznik: większe zadanie ma zostać ponownie rozplanowane.
    max_tdd_rounds: int = int(os.environ.get("FORGE_MAX_TDD_ROUNDS", "10"))
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
    # Prosty bezpiecznik absolutny cykli końcowej weryfikacji.
    max_verify_cycles: int = int(os.environ.get("FORGE_MAX_VERIFY_CYCLES", "8"))
    # Polling CI: backoff start→sufit; timeout całego oczekiwania na werdykt CI.
    ci_timeout_s: int = int(os.environ.get("FORGE_CI_TIMEOUT", "2700"))
    ci_poll_start_s: int = int(os.environ.get("FORGE_CI_POLL_START", "30"))
    ci_poll_max_s: int = int(os.environ.get("FORGE_CI_POLL_MAX", "300"))
    # Timeout pojedynczej komendy weryfikacji (smoke/flash/target/repro).
    verify_timeout_s: int = int(os.environ.get("FORGE_VERIFY_TIMEOUT", "1800"))
    # Flash bywa flaky z natury (USB) — darmowe ponowienia przed diagnozą.
    flash_retries: int = int(os.environ.get("FORGE_FLASH_RETRIES", "1"))
    # Plik konfiguracji MCP doklejany do claude TYLKO w roli weryfikatora.
    verifier_mcp_config: str = os.environ.get("FORGE_VERIFIER_MCP_CONFIG", "")

    # Mistrz kuźni — nadzorca procesu. Doradczy: jedyny jego efekt to krótka
    # nota doklejana do promptu roli, więc jego awaria nie może nic zatrzymać.
    master_agent: str = os.environ.get("FORGE_MASTER_AGENT", "opencode")
    master_model: str = os.environ.get("FORGE_MASTER_MODEL", "")
    master_effort: str = os.environ.get("FORGE_MASTER_EFFORT", "")

    # Recenzent zadania: pusty agent = agent testera, ale ZAWSZE świeży
    # kontekst (bez sesji i dziennika) — autor nie recenzuje własnej pracy.
    reviewer_agent: str = os.environ.get("FORGE_REVIEWER_AGENT", "opencode")
    reviewer_model: str = os.environ.get("FORGE_REVIEWER_MODEL", "neuralwatt/glm-5.2-flex")
    reviewer_effort: str = os.environ.get("FORGE_REVIEWER_EFFORT", "")

    # Przed rollbackiem przy porażce: branch forge/failed/<id> na HEAD (+ residual commit).
    keep_failed_ref: bool = os.environ.get("FORGE_KEEP_FAILED_REF", "1") != "0"


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
        """Zwróć ``(agent, model, effort)`` z polityki rola → poziom → provider.

        Nieznane/customowe CLI zachowują zgodność wsteczną i korzystają z pól
        ``*_model``/``*_effort``. Brak profilu w starym STATE.json oznacza
        bezpieczne ``standard``.
        """
        if difficulty not in TASK_DIFFICULTIES:
            difficulty = DEFAULT_TASK_DIFFICULTY

        configured: dict[str, tuple[str, str, str]] = {
            "planner": (self.planner_agent, self.planner_model, self.planner_effort),
            "planner_escalation": (self.planner_agent, self.planner_model, self.planner_effort),
            # Bootstrap używa agenta planisty, lecz ma własną politykę poziomu.
            "bootstrap": (self.planner_agent, self.planner_model, self.planner_effort),
            "diff_bootstrap": (self.planner_agent, self.planner_model, self.planner_effort),
            "tester": (self.tester_agent, self.tester_model, self.tester_effort),
            "coder": (self.coder_agent, self.coder_model, self.coder_effort),
            "master": (self.master_agent, self.master_model, self.master_effort),
        }
        if name == "verifier":
            configured[name] = (
                (self.verifier_agent or self.planner_agent),
                self.verifier_model if self.verifier_agent else self.planner_model,
                self.verifier_effort if self.verifier_agent else self.planner_effort,
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

        agent, configured_model, configured_effort = configured[name]
        # Jawne ustawienie planisty jest intencją operatora; routing trudności
        # dotyczy wykonawców pojedynczego zadania.
        if (name in {"planner", "planner_escalation", "bootstrap", "diff_bootstrap"}
                and configured_model):
            return (agent, *self._role_model_effort(agent, configured_model, configured_effort))
        canonical = adapters.canonical_agent(agent)
        level = self.model_level(name, difficulty)
        fixed = MODEL_LEVEL_ROUTING.get(canonical, {}).get(level)
        if fixed is not None:
            return (agent, *fixed)
        return (
            agent,
            *self._role_model_effort(agent, configured_model, configured_effort),
        )

    def model_level(
        self, name: str, difficulty: str = DEFAULT_TASK_DIFFICULTY
    ) -> str:
        """Poziom routingu niezależny od modelu i providera."""
        if difficulty not in TASK_DIFFICULTIES:
            difficulty = DEFAULT_TASK_DIFFICULTY
        try:
            return ROLE_MODEL_LEVELS[name][difficulty]
        except KeyError as exc:
            raise ValueError(f"nieznana rola: {name}") from exc

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
        names = [self.planner_agent, self.tester_agent, self.coder_agent,
                 self.master_agent,
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
    # Domyślnie pełny dostęp: buildy i testy potrafią legalnie pisać poza
    # katalogiem projektu (np. Godot do XDG_DATA_HOME). Zawęź jawnie przez
    # FORGE_CODEX_SANDBOX=workspace-write albo read-only.
    codex_sandbox: str = os.environ.get(
        "FORGE_CODEX_SANDBOX", "danger-full-access")

    # --- Push do zdalnego repo gry -----------------------------------------
    # Po każdym udanym commicie orkiestrator pcha bieżący branch do remote.
    # Wyłącz przez FORGE_GIT_PUSH=0 (np. gdy chcesz najpierw obejrzeć lokalnie).
    git_push: bool = os.environ.get("FORGE_GIT_PUSH", "1") != "0"
    git_remote: str = os.environ.get("FORGE_GIT_REMOTE", "origin")

    # Backoff przy limitach (sekundy): rośnie geometrycznie do sufitu. Sufit to
    # 24h — miesięczny "spend limit" traktujemy jak zwykły limit czasowy: nie
    # ma sensu odpytywać częściej niż raz dziennie, gdy i tak nie zniknie
    # wcześniej (reset limitu albo ręczne podniesienie przez użytkownika).
    backoff_start_s: int = int(os.environ.get("FORGE_BACKOFF_START_S", "60"))
    backoff_max_s: int = int(os.environ.get("FORGE_BACKOFF_MAX_S", str(24 * 3600)))
    backoff_factor: float = float(os.environ.get("FORGE_BACKOFF_FACTOR", "2.0"))
    # Budżet ŁĄCZNEGO czekania na limit. backoff_max_s ogranicza pojedyncze
    # oczekiwanie — bez tego pułapu 20 ponowień z podwajaniem daje ok. 10 dni
    # martwego biegu. Po wyczerpaniu budżetu zatrzymujemy się z checkpointem.
    backoff_total_s: int = int(os.environ.get("FORGE_BACKOFF_TOTAL_S", str(24 * 3600)))
    # Ile razy ponawiać jedną fazę przy limicie zanim uznamy limit za wyczerpany.
    # Twardym ogranicznikiem jest zwykle backoff_total_s; to drugi bezpiecznik.
    max_limit_retries: int = int(os.environ.get("FORGE_MAX_LIMIT_RETRIES", "20"))

    # Timeout pojedynczego wywołania agenta (sekundy). Duże, bo TDD bywa długie.
    agent_timeout_s: int = int(os.environ.get("FORGE_AGENT_TIMEOUT", "3600"))

    # Katalog runtime orkiestratora wewnątrz projektu (logi, bieżące zadanie).
    runtime_dir: str = ".forge"

    def codex(self) -> AgentCmd:
        return AgentCmd(argv=[self.codex_bin], model=self.codex_model,
                        effort=self.codex_effort)
