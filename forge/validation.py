"""Run planner-provided validation commands and preserve complete evidence."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any


def run_commands(
    commands: tuple[str, ...], cwd: Path, *, timeout_seconds: int = 900
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for command in commands:
        started = time.monotonic()
        process = subprocess.Popen(
            ["bash", "-lc", command],
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        timed_out = False
        try:
            output, _ = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                output, _ = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                output, _ = process.communicate()
        results.append(
            {
                "command": command,
                "return_code": process.returncode,
                "timed_out": timed_out,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "output": output,
            }
        )
    return results
