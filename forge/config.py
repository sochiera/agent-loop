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
    # Przegląd kierunku rozstrzyga, dokąd idzie projekt — myli się raz, a skutek
    # niesie cały dalszy plan. Jego recenzent ma tę samą wagę: to ostatnia
    # bramka przed propagacją błędnego kierunku na wszystkie kolejne zadania.
    "diff_bootstrap": {d: "max" for d in TASK_DIFFICULTIES},
    "bootstrap_reviewer": {d: "max" for d in TASK_DIFFICULTIES},
    "planner": {d: "strong" for d in TASK_DIFFICULTIES},
    "planner_escalation": {d: "max" for d in TASK_DIFFICULTIES},
    "tester": {"simple": "efficient", "standard": "balanced", "complex": "balanced"},
    "coder": {"simple": "economy", "standard": "efficient", "complex": "balanced"},
    "reviewer": {"simple": "efficient", "standard": "balanced", "complex": "strong"},
    "verifier": {"simple": "economy", "standard": "efficient", "complex": "balanced"},
    # Mistrz czyta kilkadziesiąt krótkich linii dziennika i nigdy nie czyta
    # kodu — to rozpoznawanie wzorca, nie rozumowanie o implementacji. Wołany
    # co rundę, więc dla prostych i standardowych zadań dostaje efficient,
    # a tylko dla trudnych balanced. Jako jedyna rola pracuje bez narzędzi
    # i bez pętli agentowej (tryb cienki w agents.py), więc nie musi planować
    # ani czytać drzewa.
    "master": {"simple": "efficient", "standard": "efficient", "complex": "balanced"},
}

# Użytkownik wybiera narzędzie/agenta dla roli. Konkretny model i effort są
# polityką projektu, nie pokrętłem pojedynczego uruchomienia. Dzięki temu
# wznowienie zadania odtwarza ten sam routing bez zależności od interaktywnej
# konfiguracji. Poziom modelu wynika osobno z roli i trudności zadania.
MODEL_LEVEL_ROUTING: dict[str, dict[str, tuple[str, str]]] = {
    "codex": {
        "economy": ("gpt-5.6-luna", "medium"),
        "efficient": ("gpt-5.6-luna", "high"),
        "balanced": ("gpt-5.6-luna", "xhigh"),
        "strong": ("gpt-5.6-terra", "high"),
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
    # OpenCode jako most do dwóch dostawców: dwa niższe poziomy idą na tanią
    # Lunę (reasoning_options: none/low/medium/high/xhigh/max), a trzy górne na
    # GLM-5.2 z planu kodowego z.ai — ten model wystawia TYLKO dwa poziomy
    # wysiłku, "high" i "max", więc nie ma tu czego zejść niżej.
    "opencode": {
        "economy": ("openai/gpt-5.6-luna", "medium"),
        "efficient": ("openai/gpt-5.6-luna", "high"),
        "balanced": ("zai-coding-plan/glm-5.2", "high"),
        "strong": ("zai-coding-plan/glm-5.2", "high"),
        "max": ("zai-coding-plan/glm-5.2", "max"),
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


def validate_master_agent(agent: str) -> None:
    """Mistrz musi mieć prawdziwy tryb cienki, a Codex CLI go nie udostępnia.

    Doklejenie promptu mistrza do zwykłego ``codex exec`` nadal ładuje pełny
    agentowy harness i narzędzia. To przeczy celowi tej często wołanej,
    jednoturowej roli, więc odrzucamy także aliasy Codeksa.
    """
    if adapters.canonical_agent(agent) == "codex":
        raise ValueError(
            "Codex nie jest dostępny dla roli mistrza: Codex CLI nie potrafi "
            "zastąpić systemowego harnessu ani wyłączyć narzędzi. "
            "Wybierz claude, opencode albo grok."
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
    #
    # Wywołanie planisty ma duży koszt STAŁY: czyta repo od zera (~900 tys.
    # tokenów wejścia na wywołanie, niezależnie od rozmiaru wsadu) i dopiero
    # potem myśli. W pomiarach na .forge/usage.jsonl 64% jego rachunku to samo
    # to czytanie, a 36% właściwe rozumowanie. Większy wsad amortyzuje tę stałą
    # bez dotykania modelu ani effortu — czyli bez osłabiania planowania, w
    # którym błąd kumuluje się na cały projekt.
    #
    # Górna granica jest jakościowa, nie kosztowa: dalsze zadania wsadu planuje
    # się na coraz starszym stanie repo. Stąd 8: przy wsadzie 6 planista
    # przypada 0,167 raza na zadanie, przy 8 — 0,125, czyli ~25% mniej jego
    # (najdroższego stałego) kosztu. 10 dopiero po pomiarze odsiewu planisty
    # (wpis `plan: zadeklarowano N, przyjęto M`) i zadań padłych na round_limit.
    batch_size: int = int(os.environ.get("FORGE_BATCH_SIZE", "8"))
    # Co ile wsadów planisty przegląd kierunku ocenia, dokąd idzie projekt.
    # Backlog jest z założenia krótki, więc to ten przegląd, a nie bootstrap,
    # odpowiada za rozwój zakresu projektu w czasie.
    #
    # Liczy WSADY, nie zadania, więc iloczyn z batch_size to dziś 2×8 = 16, a
    # nie dawne ~12. Reguła „iloczyn ~12" była kalibrowana pod BŁĘDEM, który
    # naprawia warunek pustej kolejki w `_steering_trigger`: przegląd trafiający
    # w pełną kolejkę kosztował wtedy cały wsad planisty, więc ciasna kadencja
    # była tanim ubezpieczeniem. Po naprawie przegląd zawsze ląduje na granicy
    # wsadów z pustą kolejką, a jedynym kosztem luźniejszej kadencji jest
    # opóźniona korekta kursu. Zejście do 1 kupowałoby ją za +50% wywołań roli
    # chodzącej na poziomie `max` — czyli za oszczędność, dla której podnieśliśmy
    # wsad. Wyzwalacz odwrotu: wzrost odsetka przeglądów z `replan=true` albo
    # zauważalny dryf kierunku → FORGE_STEERING_BATCHES=1.
    steering_batches: int = int(os.environ.get("FORGE_STEERING_BATCHES", "2"))
    # Budżet recenzji bootstrapu i przeglądu kierunku. Wyczerpanie oznacza, że
    # rozbieżności nie da się rozstrzygnąć bez decyzji użytkownika.
    max_bootstrap_reviews: int = int(
        os.environ.get("FORGE_MAX_BOOTSTRAP_REVIEWS", "4"))
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
    # Deterministyczna bramka przed mistrzem (patrz master_gate.py):
    #   off    — mistrz wołany co rundę (DOMYŚLNIE);
    #   shadow — bramka liczy się i loguje, ale mistrz jest wołany normalnie;
    #   on     — pusty trigger wycisza wywołanie.
    #
    # Domyślnie `off` decyzją, nie przez ostrożność. Bilans jest jednoznacznie
    # zły: mistrz to ~2,7% tokenów Claude'a i ~5% rachunku, więc wyciszenie go
    # oszczędza kilka procent. Po drugiej stronie stoi pojedyncza pominięta
    # interwencja — pętla, której nikt nie przerwał, kosztuje rundy po ~500 tys.
    # tokenów wejścia każda, a przy `max_tdd_rounds` kończy się `git reset
    # --hard` i utratą CAŁEJ pracy nad zadaniem. Kilkuprocentowa oszczędność
    # przeciw ryzyku straty rzędu setek procent kosztu zadania to zakład
    # asymetryczny w złą stronę.
    #
    # `shadow` zostaje dostępne: nigdy nie wycisza mistrza, kosztuje jedną
    # linię logu na wywołanie i pozwala zmierzyć bramkę, gdyby temat wrócił.
    master_gate: str = os.environ.get("FORGE_MASTER_GATE", "off")

    # Recenzent zadania: pusty agent = agent testera, ale ZAWSZE świeży
    # kontekst (bez sesji i dziennika) — autor nie recenzuje własnej pracy.
    reviewer_agent: str = os.environ.get("FORGE_REVIEWER_AGENT", "opencode")
    reviewer_model: str = os.environ.get("FORGE_REVIEWER_MODEL", "neuralwatt/glm-5.2-flex")
    reviewer_effort: str = os.environ.get("FORGE_REVIEWER_EFFORT", "")

    # Przed rollbackiem przy porażce: branch forge/failed/<id> na HEAD (+ residual commit).
    keep_failed_ref: bool = os.environ.get("FORGE_KEEP_FAILED_REF", "1") != "0"

    def __post_init__(self) -> None:
        validate_master_agent(self.master_agent)

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
            "bootstrap_reviewer": (self.planner_agent, self.planner_model, self.planner_effort),
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
        if (name in {"planner", "planner_escalation", "bootstrap",
                     "diff_bootstrap", "bootstrap_reviewer"}
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
