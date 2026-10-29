"""Real disposable init and deterministic index navigation."""
from pathlib import Path
import subprocess

import pytest

from forge.config import load_config
from forge.docsindex import SKELETON, generate, initialize
from forge.ingest import IngestError
from forge.state import State


def test_index_generation(tmp_path):
    (tmp_path / 'docs/source').mkdir(parents=True)
    source = tmp_path / 'docs/source/s.md'; source.write_text('# Source')
    (tmp_path / 'docs/forge/implemented').mkdir(parents=True)
    target = tmp_path / 'docs/forge/implemented/behavior.md'; target.write_text('# Implemented')
    generate(tmp_path, ['docs/source/s.md'])
    result = (tmp_path / 'docs/forge/INDEX.md').read_text()
    assert '../../docs/source/s.md' in result and '../../docs/forge/implemented/behavior.md' in result
    expected = subprocess.check_output(['git', 'hash-object', target], text=True).strip()
    assert expected in result
    generate(tmp_path, ['docs/source/s.md'])
    assert (tmp_path / 'docs/forge/INDEX.md').read_text() == result


def test_index_refuses_symlink_write(tmp_path):
    (tmp_path / 'docs').mkdir()
    (tmp_path / 'docs/forge').symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(IngestError):
        generate(tmp_path)


def test_init_skeleton(operator_config):
    config = load_config(operator_config())
    repo = config.target_repo
    subprocess.run(['git', 'init', '-qb', 'main', repo], check=True)
    (repo / 'seed').write_text('seed')
    subprocess.run(['git', '-C', repo, 'add', '.'], check=True)
    subprocess.run(['git', '-C', repo, '-c', 'user.name=Fixture', '-c', 'user.email=f@local', 'commit', '-qm', 'base'], check=True)
    with State(config.state_dir) as state:
        commit = initialize(config, state)
        for path in SKELETON:
            assert subprocess.check_output(['git', '-C', repo, 'show', commit + ':docs/forge/' + path])
        assert initialize(config, state) == commit
        assert subprocess.check_output(['git', '-C', repo, 'remote'], text=True) == ''
        assert not (repo / 'docs').exists()  # Host checkout preserved; branch is authoritative.
