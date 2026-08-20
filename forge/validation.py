"""Run planner-provided validation commands and preserve complete evidence."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any


def _session_process_groups(session_id: int) -> set[int]:
    """Find every process group in a Linux process session.

    Commands such as GNU ``timeout`` create a nested process group. Killing
    only the original shell's group leaves that subtree alive and can keep
    captured stdout open forever.
    """
    groups: set[int] = set()
    for stat_path in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = stat_path.read_text(encoding="utf-8").rsplit(")", 1)[1].split()
            process_group = int(fields[2])
            process_session = int(fields[3])
        except (FileNotFoundError, IndexError, ValueError):
            continue
        if process_session == session_id:
            groups.add(process_group)
    return groups


def _signal_session(session_id: int, sent_signal: signal.Signals) -> None:
    # Signal nested groups first and the original group last. This prevents a
    # group-making wrapper from surviving long enough to spawn more children.
    groups = sorted(_session_process_groups(session_id), key=lambda group: group == session_id)
    for process_group in groups:
        try:
            os.killpg(process_group, sent_signal)
        except ProcessLookupError:
            pass


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
            _signal_session(process.pid, signal.SIGTERM)
            try:
                output, _ = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                _signal_session(process.pid, signal.SIGKILL)
                output, _ = process.communicate()
        finally:
            # A validation command may background a dev server and let its
            # shell exit successfully. Keep validation hermetic by terminating
            # any descendants that still belong to the command's session.
            _signal_session(process.pid, signal.SIGTERM)
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
