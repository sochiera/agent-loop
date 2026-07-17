"""Trwały stan orkiestratora (STATE.json).

To jest "mózg" pętli między uruchomieniami. Ponieważ limity subskrypcji
zatrzymają pracę, stan MUSI przetrwać restart — dzięki temu rano wznawiamy
dokładnie tam, gdzie skończyliśmy. Reszta pamięci (wiedza o grze) żyje w
plikach repo i historii gita, nie tutaj.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field


@dataclass
class State:
    bootstrapped: bool = False
    iteration: int = 0
    # Komendy ustalone podczas bootstrapu — serce stack-agnostyczności.
    test_cmd: str = ""     # np. "pytest -q" albo "npm test" albo "./run_tests.sh"
    build_cmd: str = ""    # np. "" (interpreter) albo "cmake --build build"
    run_cmd: str = ""      # jak uruchomić produkt ręcznie (do dokumentacji)
    stack: str = ""        # krótki opis stacku, np. "C++/CMake fork Wesnotha"
    # Rodzaj produktu rozpoznany przez bootstrap: "game" | "app" (steruje
    # słownictwem promptów: grywalne vs działające MVP).
    project_kind: str = "app"
    # Historia niepowodzeń zadań — planner czyta, by dzielić/omijać.
    failures: list[str] = field(default_factory=list)
    # Ostatnio ukończone zadanie (dla kontekstu plannera).
    last_done: str = ""
    # Checkpoint bieżącej iteracji. ``phase`` wskazuje następną fazę do wykonania,
    # dzięki czemu limit agenta nie kasuje pracy i restart nie zaczyna od planu.
    # Legacy: idle→plan→implement→review→fix. Nowy model: idle→micro→review→fix_review.
    phase: str = "idle"
    current_task_title: str = ""
    fix_attempt: int = 0
    tests_green: bool = False
    review_notes: list[str] = field(default_factory=list)

    # --- Nowy model: mikro-TDD ping-pong -----------------------------------
    # Kolejka zadań z planowania wsadowego. Każdy element to dict:
    # {"id","title","file","criteria":[...],"test_globs":[...],"code_globs":[...]}.
    task_queue: list[dict] = field(default_factory=list)
    # Bieżące zadanie (zdjęte z kolejki) — ten sam kształt co element kolejki.
    current_task: dict = field(default_factory=dict)
    # Ciągły kontekst per zadanie: id sesji Codeksa-testera i Codeksa-kodera.
    tester_session: str = ""
    coder_session: str = ""
    # Licznik ukończonych mikro-cykli w bieżącym zadaniu (sufit: cfg.max_micro_cycles).
    micro_cycle: int = 0
    # Podfaza mikro-pętli: "test" (kolej Codeksa-testera) lub "code" (Codeksa-kodera).
    micro_sub: str = "test"
    # Pliki testowe dopisane przez testera w bieżącym mikro-cyklu (kontrola diffu kodera).
    cycle_test_files: list[str] = field(default_factory=list)
    # Czy bieżący cykl jest „bez testu" (tester zadeklarował no_test) — koder dostaje inny prompt.
    pending_no_test: bool = False
    # Ile razy tester zadeklarował „brak sensownego testu" w tym zadaniu (smell, jeśli dużo).
    no_test_count: int = 0
    # Tag gita ustawiony na starcie zadania — punkt rollbacku przy porażce zadania.
    task_start_tag: str = ""
    # Ile razy w bieżącym zadaniu uruchomiono repro (sufit chroni sprzęt:
    # repro bywa flashowaniem). Zerowane na starcie zadania.
    repro_runs: int = 0

    # --- PLAN-4: uszczelnienie bramek --------------------------------------
    # Globy toolchainu testowego zadeklarowane przy bootstrapie (uzupełniają
    # wbudowaną heurystykę; jak verify_test_globs — serce stack-agnostyczności).
    test_toolchain_globs: list[str] = field(default_factory=list)
    # Powody odrzucenia ostatniej mapy kryteriów przy DONE — wracają do
    # testera w kolejnym prompcie (bounded-retry nie zgaduje w ciemno).
    done_reject_reasons: list[str] = field(default_factory=list)
    # Kryteria "justified" z przyjętej mapy — recenzent dostaje je jawnie
    # do merytorycznego rozstrzygnięcia. Czyszczone przy zamknięciu zadania.
    justified_criteria: list[dict] = field(default_factory=list)

    # --- Weryfikacja celu (PLAN-3): profil + checkpoint cyklu ---------------
    # Profil deklaruje bootstrap (jak test_cmd — serce stack-agnostyczności).
    # Puste verify_targets = weryfikacja wyłączona (zachowanie sprzed PLAN-3);
    # stare STATE.json migrują na to naturalnie przez wartości domyślne.
    verify_targets: list[str] = field(default_factory=list)  # np. ["ci","hardware","smoke"]
    smoke_cmd: str = ""       # dymny bieg produktu; rc==0 = OK
    flash_cmd: str = ""       # hardware: wgranie na target
    target_cmd: str = ""      # hardware: testy na targecie; stdout = log seriala
    probe_cmd: str = ""       # hardware, opcjonalne: preflight obecności urządzenia
    ci_status_cmd: str = ""   # ci: status checków dla {sha}; rc 0/1/2=zielone/czerwone/trwa
    ci_logs_cmd: str = ""     # ci: log porażek dla {sha} na stdout
    # Testy wykonywane w środowisku weryfikacji (target/CI), nie w lokalnej
    # suicie — chronione przed osłabianiem jak workflow (PLAN-3, sekcja 8).
    verify_test_globs: list[str] = field(default_factory=list)
    # Checkpoint fazy verify_goal: numer cyklu, kolejne cykle bez postępu,
    # SHA badanego HEAD i rejestr problemów z ostatniego werdyktu (wejście
    # odhaczania w cyklu następnym).
    verify_cycle: int = 0
    verify_stall: int = 0
    verify_sha: str = ""
    verify_problems: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, path: str) -> "State":
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Migracja checkpointu ze starszej wersji (phase + current_title).
            if "current_task_title" not in data and "current_title" in data:
                data["current_task_title"] = data["current_title"]
            known = {k: data[k] for k in data if k in cls.__annotations__}
            return cls(**known)
        return cls()

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)  # atomowy zapis
