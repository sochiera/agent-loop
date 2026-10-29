"""Version and distribution identity for Forge v3."""

import subprocess
import sys
import tomllib
from pathlib import Path

from forge import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_module_cli_and_metadata_are_3_0_0():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert __version__ == "3.0.0"
    assert project["name"] == "forge-agent-orchestrator"
    assert project["version"] == "3.0.0"
    assert project["license"] == "MIT"
    assert project["scripts"] == {"forge": "forge.cli:main"}
    assert project["requires-python"] == ">=3.12"
    assert project["dependencies"] == []
    result = subprocess.run(
        [sys.executable, "-m", "forge", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout == "forge 3.0.0\n"
