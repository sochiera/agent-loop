import subprocess
import sys


def test_module_entrypoint_rejects_unknown_option() -> None:
    result = subprocess.run([sys.executable, "-m", "forge.orchestrate", "--unknown"], text=True, capture_output=True)
    assert result.returncode == 2
    assert "usage:" in result.stderr
