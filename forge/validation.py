"""Run planner-provided validation commands and preserve complete evidence."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

_LONG = re.compile(
    r"(?:^|[\s/_:-])(live|e2e|scrape|integration)(?:$|[\s/_:-])"
    r"|import:(?:full|incremental|resume)"
    r"|full[-_]?scan"
    r"|validate:full",
    re.IGNORECASE,
)


def classify_command(command: str) -> str:
    if _LONG.search(command) or "http://" in command or "https://" in command:
        return "long"
    return "short"


def classify_red_exit_code(return_code: int) -> str:
    """Classify a pytest run over a freshly written black-box suite."""

    if return_code == 0:
        return "passing"
    if return_code == 1:
        return "red"
    if return_code == 5:
        return "empty"
    return "error"


_SCRIPT_INVOCATION = re.compile(r"^(timeout\s+\d+(?:\.\d+)?[a-z]*\s+)?(\./[^\s]+)(.*)$")


def _bash_fallback_command(command: str, cwd: Path) -> str | None:
    """Rewrite an unexecutable ``./script`` invocation through ``bash`` once."""

    match = _SCRIPT_INVOCATION.match(command.strip())
    if match is None:
        return None
    wrapper, script, rest = match.groups()
    path = cwd / script
    if not path.is_file() or os.access(path, os.X_OK):
        return None
    try:
        first_line = path.open("rb").readline().decode("utf-8", errors="replace")
    except OSError:
        return None
    if not first_line.startswith("#!"):
        return None
    return f"{wrapper or ''}bash {script}{rest}".strip()


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
    commands: tuple[str, ...],
    cwd: Path,
    *,
    timeout_seconds: int | None = None,
    short_timeout_seconds: int = 120,
    long_timeout_seconds: int = 900,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for command in commands:
        kind = classify_command(command)
        limit = timeout_seconds
        if limit is None:
            limit = short_timeout_seconds if kind == "short" else long_timeout_seconds
        entry = _run_one(command, kind, limit, cwd)
        if entry["return_code"] == 126:
            fallback = _bash_fallback_command(command, cwd)
            if fallback is not None:
                fallback_kind = classify_command(fallback)
                retried = _run_one(fallback, fallback_kind, limit, cwd)
                retried.update(
                    command=command,
                    mode_fallback={
                        "executed_command": fallback,
                        "original_return_code": 126,
                    },
                )
                entry = retried
        results.append(entry)
    return results


def _run_one(
    command: str, kind: str, limit: int, cwd: Path
) -> dict[str, Any]:
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
        output, _ = process.communicate(timeout=limit)
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
    return {
        "command": command,
        "kind": kind,
        "return_code": process.returncode,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "output": output,
    }
