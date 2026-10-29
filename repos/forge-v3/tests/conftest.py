"""P0 BUILD tests; temporary writes stay inside the authorized project venv."""

from pathlib import Path
import pytest


def pytest_configure(config):
    if config.option.basetemp is None:
        config.option.basetemp = str(Path(__file__).resolve().parents[1] / '.venv' / 'pytest-tmp')


@pytest.fixture
def operator_config(tmp_path, monkeypatch):
    config_home = tmp_path / 'config'
    root = config_home / 'forge/projects'
    root.mkdir(parents=True)
    repo = tmp_path / 'target'
    repo.mkdir()
    monkeypatch.setenv('XDG_CONFIG_HOME', str(config_home))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    path = root / 'demo.toml'

    def write(extra=''):
        path.write_text(f'target_repo = "{repo}"\n' + extra)
        path.chmod(0o600)
        return path

    return write
