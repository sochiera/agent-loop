"""Wspólny rdzeń uruchamiania komend projektu: pojedyncza komenda bez shella.

Jedno miejsce prawdy dla semantyki subprocess wszystkich bramek (testy, build,
weryfikacja celu) — run_tests, run_gate i dowody weryfikacji nie mogą się
rozjechać. Wydzielone z orchestrate, bo moduł weryfikacji też tego potrzebuje,
a nie może importować orkiestratora (cykl importów).
"""
from __future__ import annotations

import shlex
import subprocess


def run_shellfree(project: str, cmd: str, timeout: int) -> tuple[int | None, str]:
    """Uruchom komendę w katalogu projektu bez shella.

    Zwraca (returncode, wyjście) albo (None, diagnoza), gdy komenda w ogóle
    nie wystartowała (składnia/pusta/OSError/timeout)."""
    try:
        argv = shlex.split(cmd)
    except ValueError as exc:
        return None, f"niepoprawna składnia komendy ({exc})"
    if not argv:
        return None, "pusta komenda"
    try:
        proc = subprocess.run(argv, cwd=project, shell=False, text=True,
                              capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "TIMEOUT"
    except OSError as exc:
        return None, f"nie udało się uruchomić ({exc})"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
