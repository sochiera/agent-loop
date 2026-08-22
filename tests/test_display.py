import os
from pathlib import Path

from forge.display import VIRTUAL_DISPLAY_ENV, start_virtual_display


def _install_fake_xvfb(directory: Path, script: str) -> Path:
    path = directory / "Xvfb"
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)
    return path


def _path_with(directory: Path) -> str:
    return f"{directory}{os.pathsep}{os.environ.get('PATH', '')}"


def test_start_virtual_display_returns_none_without_xvfb(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert start_virtual_display() is None


def test_start_virtual_display_uses_displayfd(monkeypatch, tmp_path: Path):
    _install_fake_xvfb(
        tmp_path,
        """#!/usr/bin/python3
import os, sys, time
args = sys.argv[1:]
fd = int(args[args.index("-displayfd") + 1])
os.write(fd, b"99\\n")
os.close(fd)
time.sleep(30)
""",
    )
    monkeypatch.setenv("PATH", _path_with(tmp_path))
    server = start_virtual_display()
    assert server is not None
    try:
        assert server.display == ":99"
        assert server.environment() == {VIRTUAL_DISPLAY_ENV: ":99"}
        assert "DISPLAY" not in server.environment()
        assert server.process.poll() is None
    finally:
        server.close()
    assert server.process.poll() is not None


def test_start_virtual_display_falls_back_to_numbered_slot(monkeypatch, tmp_path: Path):
    _install_fake_xvfb(
        tmp_path,
        """#!/usr/bin/python3
import sys, time
if sys.argv[1].startswith(":"):
    time.sleep(30)
    raise SystemExit(0)
raise SystemExit(2)
""",
    )
    monkeypatch.setenv("PATH", _path_with(tmp_path))
    server = start_virtual_display()
    assert server is not None
    try:
        assert server.display.startswith(":")
        assert server.display[1:].isdigit()
        assert server.process.poll() is None
    finally:
        server.close()
    assert server.process.poll() is not None
