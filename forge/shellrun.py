"""Wspólny rdzeń uruchamiania komend projektu: pojedyncza komenda bez shella.

Jedno miejsce prawdy dla semantyki subprocess wszystkich bramek (testy, build,
weryfikacja celu) — run_tests, run_gate i dowody weryfikacji nie mogą się
rozjechać. Wydzielone z orchestrate, bo moduł weryfikacji też tego potrzebuje,
a nie może importować orkiestratora (cykl importów).
"""
from __future__ import annotations

import os
import shlex
import signal
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
        proc = subprocess.Popen(
            argv, cwd=project, shell=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True)
    except OSError as exc:
        return None, f"nie udało się uruchomić ({exc})"
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate()
        return None, "TIMEOUT"
    return proc.returncode, (stdout or "") + (stderr or "")
