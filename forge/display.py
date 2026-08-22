"""Optional private X server for agents that want GUI isolation."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .validation import _signal_session

VIRTUAL_DISPLAY_ENV = "FORGE_VIRTUAL_DISPLAY"
_DISPLAYFD_TIMEOUT_SECONDS = 5.0
_NUMBERED_READY_SECONDS = 0.4
_NUMBERED_RANGE = range(90, 110)


@dataclass
class VirtualDisplay:
    display: str
    process: subprocess.Popen[bytes]

    def environment(self) -> dict[str, str]:
        return {VIRTUAL_DISPLAY_ENV: self.display}

    def close(self) -> None:
        if self.process.poll() is not None:
            return
        _signal_session(self.process.pid, signal.SIGTERM)
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _signal_session(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=3)


@contextmanager
def optional_virtual_display(
    *, width: int = 1280, height: int = 800, depth: int = 24
) -> Iterator[VirtualDisplay | None]:
    server = start_virtual_display(width=width, height=height, depth=depth)
    try:
        yield server
    finally:
        if server is not None:
            server.close()


def start_virtual_display(
    *, width: int = 1280, height: int = 800, depth: int = 24
) -> VirtualDisplay | None:
    binary = shutil.which("Xvfb")
    if not binary:
        return None
    geometry = f"{width}x{height}x{depth}"
    return _start_displayfd(binary, geometry) or _start_numbered(binary, geometry)


def _spawn(command: list[str], *, pass_fds: tuple[int, ...] = ()) -> subprocess.Popen[bytes] | None:
    try:
        return subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=pass_fds,
            start_new_session=True,
        )
    except OSError:
        return None


def _start_displayfd(binary: str, geometry: str) -> VirtualDisplay | None:
    read_fd, write_fd = os.pipe()
    try:
        process = _spawn(
            [
                binary,
                "-displayfd",
                str(write_fd),
                "-screen",
                "0",
                geometry,
                "-nolisten",
                "tcp",
            ],
            pass_fds=(write_fd,),
        )
    except Exception:
        os.close(read_fd)
        os.close(write_fd)
        return None
    os.close(write_fd)
    if process is None:
        os.close(read_fd)
        return None
    try:
        number = _read_display_number(read_fd, process)
    finally:
        os.close(read_fd)
    if number is None:
        _stop(process)
        return None
    return VirtualDisplay(f":{number}", process)


def _read_display_number(read_fd: int, process: subprocess.Popen[bytes]) -> str | None:
    os.set_blocking(read_fd, False)
    buf = b""
    deadline = time.monotonic() + _DISPLAYFD_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return None
        try:
            chunk = os.read(read_fd, 64)
        except BlockingIOError:
            time.sleep(0.05)
            continue
        if not chunk:
            return None
        buf += chunk
        if b"\n" in buf or buf.strip().isdigit():
            text = buf.decode("ascii", "replace").strip()
            return text if text.isdigit() else None
    return None


def _start_numbered(binary: str, geometry: str) -> VirtualDisplay | None:
    for number in _NUMBERED_RANGE:
        if _display_taken(number):
            continue
        process = _spawn(
            [binary, f":{number}", "-screen", "0", geometry, "-nolisten", "tcp"]
        )
        if process is None:
            return None
        if _wait_alive(process, _NUMBERED_READY_SECONDS):
            return VirtualDisplay(f":{number}", process)
    return None


def _display_taken(number: int) -> bool:
    return Path(f"/tmp/.X{number}-lock").exists() or Path(f"/tmp/.X11-unix/X{number}").exists()


def _wait_alive(process: subprocess.Popen[bytes], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        time.sleep(0.05)
    return process.poll() is None


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    _signal_session(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        _signal_session(process.pid, signal.SIGKILL)
        process.wait(timeout=2)
