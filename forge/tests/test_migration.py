"""Boundary checks for the Forge v3 engine migration."""

import hashlib
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LICENSE_SHA256 = "bb5f4fa7fa40f50a6c5a056a4e26c7f08a3eb9132f4e30bd3ce1cc5499fa4760"
AGENTS_SHA256 = "6e760e76195a0337f8be3098e40731bc5bb63e279d516cc67f1ee7ef5261cb0d"
RETIRED = ("swarm-run", "swarm-resume", "ui", "policy")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_legacy_root_surfaces_absent():
    assert not (ROOT / "forge").exists()
    assert not (ROOT / "deploy").exists()
    assert not (ROOT / "briefs").exists()
    assert (ROOT / "src" / "forge" / "__init__.py").is_file()
    assert (ROOT / "docs" / "migration-v3.md").is_file()


def test_license_and_agents_bytes():
    assert _sha256(ROOT / "LICENSE") == LICENSE_SHA256
    assert _sha256(ROOT / "AGENTS.md") == AGENTS_SHA256


def test_src_discovery_and_no_runtime_deps():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    setuptools = project["tool"]["setuptools"]
    assert setuptools["packages"]["find"]["where"] == ["src"]
    assert project["project"]["dependencies"] == []
    assert "package-data" not in setuptools
    assert setuptools["packages"] != ["forge"]


def test_retired_commands_absent_from_help():
    result = subprocess.run(
        [sys.executable, "-m", "forge", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    text = result.stdout
    for name in ("config-check", "init", "doctor", "run", "status", "cancel",
                 "unblock", "satisfied", "evidence", "canary"):
        assert name in text
    for retired in RETIRED:
        assert retired not in text.split()
