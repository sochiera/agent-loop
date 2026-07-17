"""Dowody mechaniczne weryfikacji celu (PLAN-3, sekcja 3.2) — zero tokenów.

Orkiestrator sam zbiera materiał dowodowy zanim zawoła agenta-weryfikatora:
dymny bieg produktu, flash + testy na targecie, polling statusu CI z backoffem.
Pełne wyjścia komend lądują w logach cyklu (`.forge/verification/cycle-N/`),
do agenta idą kody wyjścia i ścieżki. Komendy pochodzą wyłącznie z profilu
zadeklarowanego przy bootstrapie (State) — ten moduł niczego nie wymyśla.
"""
from __future__ import annotations

import datetime as _dt
import os
import shlex
import time

from .config import Config
from .shellrun import run_shellfree
from .state import State


def expand_sha(cmd: str, sha: str) -> str:
    """Rozwiń placeholder {sha} w komendzie CI (jedyny placeholder profilu)."""
    return cmd.replace("{sha}", sha)


def _append_log(log_path: str, cmd: str, rc: int | None, output: str) -> None:
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    stamp = _dt.datetime.now().strftime("%H:%M:%S")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"\n===== {stamp} rc={rc} :: {cmd} =====\n{output}")


def _run_logged(project: str, cmd: str, timeout: int, log_path: str) -> int | None:
    """Komenda przez wspólny rdzeń shell-free + pełny zapis wyjścia do loga."""
    rc, out = run_shellfree(project, cmd, timeout)
    _append_log(log_path, cmd, rc, out)
    return rc


def _hardware_evidence(project: str, state: State, cfg: Config, log_path: str) -> int | None:
    """Flash (z darmowymi ponowieniami — USB bywa flaky) → testy na targecie."""
    rc: int | None = None
    for _ in range(cfg.flash_retries + 1):
        rc = _run_logged(project, state.flash_cmd, cfg.verify_timeout_s, log_path)
        if rc == 0:
            break
    if rc != 0:
        return rc
    return _run_logged(project, state.target_cmd, cfg.verify_timeout_s, log_path)


def poll_ci(project: str, state: State, cfg: Config, sha: str, log_path: str,
            *, sleep=time.sleep) -> int | None:
    """Odpytuj status CI dla SHA aż do werdyktu (kontrakt: rc 0/1/2 =
    zielono/czerwono/trwa). Backoff geometryczny do sufitu; przekroczenie
    ci_timeout_s → None (timeout NIE jest zielenią). Przy czerwieni dociąga
    log porażek. Czekanie dzieje się tutaj, za darmo — nie w wywołaniu agenta."""
    status_cmd = expand_sha(state.ci_status_cmd, sha)
    deadline = time.monotonic() + cfg.ci_timeout_s
    delay = float(cfg.ci_poll_start_s)
    while True:
        rc = _run_logged(project, status_cmd, cfg.verify_timeout_s, log_path)
        if rc != 2:  # werdykt (0/1) albo usterka samej komendy (inne rc/None)
            if rc == 1 and state.ci_logs_cmd:
                _run_logged(project, expand_sha(state.ci_logs_cmd, sha),
                            cfg.verify_timeout_s, log_path)
            return rc
        if time.monotonic() >= deadline:
            _append_log(log_path, status_cmd, None,
                        f"CI bez werdyktu w limicie {cfg.ci_timeout_s}s — timeout.\n")
            return None
        sleep(delay)
        delay = min(delay * 2, float(cfg.ci_poll_max_s))


def _one_target(project: str, state: State, cfg: Config, target: str,
                log_path: str, sha: str, sleep) -> int | None:
    if target == "smoke":
        return _run_logged(project, state.smoke_cmd, cfg.verify_timeout_s, log_path)
    if target == "hardware":
        return _hardware_evidence(project, state, cfg, log_path)
    if target == "ci":
        return poll_ci(project, state, cfg, sha, log_path, sleep=sleep)
    _append_log(log_path, target, None, f"nieznany target weryfikacji: {target!r}\n")
    return None


def collect_evidence(project: str, state: State, cfg: Config, cycle_dir: str,
                     *, sha: str, sleep=time.sleep) -> dict:
    """Zbierz dowody dla wszystkich targetów profilu.

    Zwraca {target: {"rc": int|None, "log": ścieżka}}; rc==0 to jedyna zieleń
    (None = nie wystartowało/timeout). Logi per target nadpisywane per zbiórkę
    — stare dowody nie mogą udawać świeżych."""
    results: dict = {}
    for target in state.verify_targets:
        log_path = os.path.join(cycle_dir, f"{target}.log")
        try:
            os.remove(log_path)
        except OSError:
            pass
        rc = _one_target(project, state, cfg, target, log_path, sha, sleep)
        results[target] = {"rc": rc, "log": log_path}
    return results


def confirm_env_issue(project: str, state: State, cfg: Config, target: str,
                      confirm_dir: str, *, sha: str, sleep=time.sleep) -> bool:
    """Mechaniczne potwierdzenie env_issue zgłoszonego przez agenta (PLAN-3,
    sekcja 7): pełna powtórka dowodów wskazanego targetu. True = nadal
    czerwono (potwierdzone — stop); False = zielono (klasyfikacja odrzucona,
    bieg trwa). Dla hardware najpierw probe_cmd — odpiętej płytki nie
    flashujemy."""
    log_path = os.path.join(confirm_dir, f"{target}-confirm.log")
    if target == "hardware" and state.probe_cmd:
        if _run_logged(project, state.probe_cmd, cfg.verify_timeout_s, log_path) != 0:
            return True
    return _one_target(project, state, cfg, target, log_path, sha, sleep) != 0


def run_repro(project: str, repro_cmd: str, timeout: int) -> tuple[bool, str]:
    """Bramka reprodukcji problemu: (zielony?, ogon wyjścia przy czerwieni).

    Kontrakt repro-skryptu: rc≠0 = bug obecny, rc==0 = naprawiony."""
    rc, out = run_shellfree(project, repro_cmd, timeout)
    green = rc == 0
    return green, ("" if green else (out or "")[-1500:])


def verify_script_paths(project: str, state: State) -> list[str]:
    """Ścieżki skryptów użytych w komendach profilu weryfikacji (istniejące
    pliki repo wskazane jako argumenty, np. 'scripts/smoke.sh' w
    'bash scripts/smoke.sh'). Wchodzą do chronionych ścieżek — najtańszą
    "naprawą" czerwonej weryfikacji nie może być edycja jej skryptu."""
    paths: set[str] = set()
    for cmd in (state.smoke_cmd, state.flash_cmd, state.target_cmd,
                state.probe_cmd, state.ci_status_cmd, state.ci_logs_cmd):
        try:
            tokens = shlex.split(cmd or "")
        except ValueError:
            continue
        # tokens[0] też: skrypt bywa wywoływany bezpośrednio ("./scripts/x.sh"),
        # nie tylko przez interpreter — prawdziwa binarka (bash, python3) i tak
        # nie jest plikiem w repo, więc warunek isfile ją odsiewa.
        for tok in tokens:
            if os.path.isfile(os.path.join(project, tok)):
                paths.add(tok.replace("\\", "/"))
    return sorted(paths)
