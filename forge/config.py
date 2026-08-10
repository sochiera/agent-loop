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
from . import routing as routing_module


TASK_DIFFICULTIES = ("simple", "standard", "complex")
DEFAULT_TASK_DIFFICULTY = "standard"
MODEL_LEVELS = ("economy", "efficient", "balanced", "strong", "max")

# Trudność opisuje zakres zadania, a poziom modelu politykę routingu providera.
ROLE_MODEL_LEVELS: dict[str, dict[str, str]] = {
    "bootstrap": {d: "max" for d in TASK_DIFFICULTIES},
    # Product Owner rozstrzyga, dokąd idzie projekt — myli się raz, a skutek
    # niesie cały dalszy plan. Jego recenzent ma tę samą wagę: to ostatnia
    # bramka przed propagacją błędnego kierunku na wszystkie kolejne zadania.
    "product_owner": {d: "max" for d in TASK_DIFFICULTIES},
    "po_reviewer": {d: "strong" for d in TASK_DIFFICULTIES},
    "bootstrap_reviewer": {d: "max" for d in TASK_DIFFICULTIES},
    "planner": {d: "strong" for d in TASK_DIFFICULTIES},
    "planner_escalation": {d: "max" for d in TASK_DIFFICULTIES},
    "tester": {"simple": "efficient", "standard": "balanced", "complex": "balanced"},
    "coder": {"simple": "economy", "standard": "efficient", "complex": "balanced"},
    "reviewer": {"simple": "efficient", "standard": "balanced", "complex": "strong"},
    "verifier": {d: "strong" for d in TASK_DIFFICULTIES},
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
        "balanced": ("sonnet", "medium"),
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
    # Cienkie role na dwóch najniższych poziomach jadą Luną; GLM-5.2 zostaje
    # dla poziomu balanced. STRONG i MAX idą przez Token Plan do Qwen3.8 Max.
    # Modele wymagają prefiksu providera, bo OpenCode rozwiązuje je w jego
    # przestrzeni nazw.
    "opencode": {
        "economy": ("openai/gpt-5.6-luna", "medium"),
        "efficient": ("openai/gpt-5.6-luna", "high"),
        "balanced": ("zai-coding-plan/glm-5.2", "high"),
        "strong": ("qwencloud-token-plan/qwen3.8-max", "high"),
        "max": ("qwencloud-token-plan/qwen3.8-max", "max"),
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
    # Próg wyzwalacza refill; nie jest limitem jakości backlogu.
    backlog_low_water: int = int(os.environ.get("FORGE_BACKLOG_LOW_WATER", "2"))
    # Miękki sufit dla recenzenta PO; migracja może go legalnie przekroczyć.
    max_backlog_stories: int = int(os.environ.get("FORGE_MAX_BACKLOG_STORIES", "6"))
    # Budżet recenzji bootstrapu i przeglądu kierunku. Wyczerpanie oznacza, że
    # rozbieżności nie da się rozstrzygnąć bez decyzji użytkownika.
    max_bootstrap_reviews: int = int(
        os.environ.get("FORGE_MAX_BOOTSTRAP_REVIEWS", "4"))
    # Mały bezpiecznik: większe zadanie ma zostać ponownie rozplanowane.
    max_tdd_rounds: int = int(os.environ.get("FORGE_MAX_TDD_ROUNDS", "10"))
    # Agent CLI każdej roli nowego modelu. "claude"/"codex" mają wbudowaną
    # obsługę; dowolna inna nazwa → agent generyczny z FORGE_AGENT_<NAME>_CMD
    # (patrz adapters.py). Domyślnie tester i koder to opencode (z.ai).
    tester_agent: str = os.environ.get("FORGE_TESTER_AGENT", "opencode")
    coder_agent: str = os.environ.get("FORGE_CODER_AGENT", "opencode")
    # Model/effort ról. Puste → decyduje polityka poziomu dla danego agenta
    # (MODEL_LEVEL_ROUTING), a gdy jej nie ma — sam agent (codex: config.toml).
    tester_model: str = os.environ.get("FORGE_TESTER_MODEL", "")
    tester_effort: str = os.environ.get("FORGE_TESTER_EFFORT", "")
    coder_model: str = os.environ.get("FORGE_CODER_MODEL", "")
    coder_effort: str = os.environ.get("FORGE_CODER_EFFORT", "")

    # --- Weryfikacja celu (PLAN-3) -------------------------------------------
    # Weryfikator-QA: pusty agent = rola planisty (ocena całości to zadanie
    # mocnego modelu). Jawny agent konfiguruje się jak tester/koder.
    verifier_agent: str = os.environ.get("FORGE_VERIFIER_AGENT", "opencode")
    verifier_model: str = os.environ.get("FORGE_VERIFIER_MODEL", "")
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
    reviewer_model: str = os.environ.get("FORGE_REVIEWER_MODEL", "")
    reviewer_effort: str = os.environ.get("FORGE_REVIEWER_EFFORT", "")
    # Przed rollbackiem przy porażce: branch forge/failed/<id> na HEAD (+ residual commit).
    keep_failed_ref: bool = os.environ.get("FORGE_KEEP_FAILED_REF", "1") != "0"

    # Nadpisania operatora (plik ~/.config/forge/routing.json — patrz routing.py).
    # Wybór z GUI ma być trwały i wspólny z uruchomieniami z CLI, więc czyta go
    # sama konfiguracja, a nie warstwa uruchamiająca.
    routing: routing_module.Routing = field(
        default_factory=lambda: routing_module.load_from_env(
            difficulties=TASK_DIFFICULTIES))

    def __post_init__(self) -> None:
        # Każda trudność osobno: narzędzie mistrza bywa dziś ustawione per slot,
        # więc sprawdzenie samego „standard" przepuściłoby Codeksa na zadaniach
        # prostych albo złożonych.
        for difficulty in TASK_DIFFICULTIES:
            validate_master_agent(self.role("master", difficulty)[0])

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
        """Zwróć ``(agent, model, effort)`` — pierwszy wybór dla roli.

        To nadal polityka rola → poziom → provider; nadpisania operatora z
        ``routing.json`` mają nad nią pierwszeństwo (patrz ``_role_primary``)."""
        return self.role_chain(name, difficulty)[0]

    def role_chain(
        self, name: str, difficulty: str = DEFAULT_TASK_DIFFICULTY
    ) -> list[tuple[str, str, str]]:
        """Pierwszy wybór roli i jej łańcuch zapasowy, w kolejności prób.

        Wpis zapasowy bez modelu oznacza „ten sam poziom, inne narzędzie” — jego
        model wyznacza polityka poziomu dla wskazanego agenta. Duplikaty znikają:
        zapas identyczny z poprzednikiem tylko powtórzyłby tę samą awarię."""
        if difficulty not in TASK_DIFFICULTIES:
            difficulty = DEFAULT_TASK_DIFFICULTY
        primary = self._role_primary(name, difficulty)
        chain = [primary]
        for entry in self.routing.fallbacks(name):
            agent = entry.agent or primary[0]
            # Zapas jest tak samo wiążący jak pierwszy wybór, więc podlega tym
            # samym zakazom — inaczej zakaz obowiązywałby tylko do pierwszej
            # awarii, a mistrz cicho dostałby Codeksa z pełnym harnessem.
            if not routing_module.agent_allowed(name, agent):
                validate_master_agent(agent)
            if entry.model:
                candidate = (agent, entry.model, entry.effort)
            else:
                model, effort = self._level_route(agent, name, difficulty)
                candidate = (agent, model, entry.effort or effort)
            if candidate not in chain:
                chain.append(candidate)
        return chain

    def _level_route(
        self, agent: str, name: str, difficulty: str
    ) -> tuple[str, str]:
        """(model, effort) z tabeli poziomów; ("", "") = niech agent zdecyduje."""
        fixed = MODEL_LEVEL_ROUTING.get(adapters.canonical_agent(agent), {}).get(
            self.model_level(name, difficulty))
        return fixed if fixed is not None else ("", "")

    def _role_primary(
        self, name: str, difficulty: str = DEFAULT_TASK_DIFFICULTY
    ) -> tuple[str, str, str]:
        """Rozwiązanie roli: nadpisania operatora, potem polityka projektu.

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
            "product_owner": (self.planner_agent, self.planner_model, self.planner_effort),
            "po_reviewer": (self.planner_agent, self.planner_model, self.planner_effort),
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
        slot = self.routing.slot(name, difficulty)
        # Slot ma pierwszeństwo przed narzędziem całej roli: wybór modelu
        # przesądza o narzędziu, a model wybiera się per trudność.
        override_agent = slot.agent or self.routing.agent(name)
        if override_agent:
            agent = override_agent
            # Model z pól roli opisuje INNE narzędzie (np. "zai-coding-plan/…" dla
            # OpenCode). Przeniesiony na wybranego agenta byłby nazwą, której on
            # nie zna, więc wybór narzędzia zeruje model i wraca do polityki.
            configured_model, configured_effort = "", ""

        if slot.model:
            return (agent, slot.model, slot.effort)

        # Jawne ustawienie planisty jest intencją operatora; routing trudności
        # dotyczy wykonawców pojedynczego zadania.
        if (name in {"planner", "planner_escalation", "bootstrap",
                     "product_owner", "po_reviewer",
                     "bootstrap_reviewer"}
                and configured_model):
            model, effort = self._role_model_effort(
                agent, configured_model, configured_effort)
            return (agent, model, slot.effort or effort)
        model, effort = self._level_route(agent, name, difficulty)
        if not model:
            model, effort = self._role_model_effort(
                agent, configured_model, configured_effort)
        return (agent, model, slot.effort or effort)

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
        # Łańcuch zapasowy też trzeba sprawdzić: agent, który okaże się
        # nieobecny dopiero w chwili awarii pierwszego wyboru, zamienia
        # zabezpieczenie w drugą awarię.
        names = [
            agent
            for definition in routing_module.ROLE_DEFS
            for difficulty in TASK_DIFFICULTIES
            for agent, _model, _effort in self.role_chain(
                definition.name, difficulty)
        ]
        seen: dict[str, str] = {}
        for name in names:
            seen.setdefault(adapters.canonical_agent(name), name)
        return list(seen.values())

    def opencode_models_in_use(self) -> list[str]:
        """Modele „provider/model", po które sięgnie OpenCode w tym trybie.

        Podstawa preflightu poświadczeń: klucz jest potrzebny do providera,
        którego routing NAPRAWDĘ wskazuje — łącznie z łańcuchem zapasowym, bo
        zapas bez klucza zamienia jedną awarię w dwie."""
        out: list[str] = []
        for definition in routing_module.ROLE_DEFS:
            for difficulty in TASK_DIFFICULTIES:
                for agent, model, _effort in self.role_chain(
                        definition.name, difficulty):
                    if (adapters.canonical_agent(agent) == "opencode"
                            and model and model not in out):
                        out.append(model)
        return out

    def roles_blocked_by(self, providers: set[str]) -> list[str]:
        """Etykiety „rola/trudność", dla których CAŁY łańcuch jest nieużywalny.

        Sam brak klucza do jednego dostawcy nie jest jeszcze awarią przebiegu:
        rola z działającym zapasem po prostu z niego skorzysta. Awarią jest
        dopiero rola, która nie ma już czym wykonać zadania — i tylko taką warto
        zgłosić, zanim pętla ruszy."""
        if not providers:
            return []
        out: list[str] = []
        for definition in routing_module.ROLE_DEFS:
            for difficulty in TASK_DIFFICULTIES:
                chain = self.role_chain(definition.name, difficulty)
                if chain and all(
                        adapters.canonical_agent(agent) == "opencode"
                        and model.partition("/")[0] in providers
                        for agent, model, _effort in chain):
                    out.append(f"{definition.name}/{difficulty}")
        return out

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
