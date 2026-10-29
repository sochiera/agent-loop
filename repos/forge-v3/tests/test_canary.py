"""Canary construction and fail-closed verification; no fixture is live evidence."""
import json
from pathlib import Path
import subprocess

import pytest

from forge.canary import environment, layout_png, setup, verify
from forge.config import DEFAULT_PROFILES, load_config
from forge.cli import main
from test_doctor import decode_png


@pytest.fixture
def tmp_path(tmp_path_factory):
    return tmp_path_factory.mktemp('canary')


def test_disposable_has_real_bug_pixel_only_order_and_hidden_row4(tmp_path):
    data = setup(tmp_path / 'canary')
    repo = Path(data['repo'])
    assert setup(tmp_path / 'canary') == data
    assert 'spec.md' not in (repo/'docs/source/INDEX.md').read_text()
    rows = [l for l in (repo/'docs/source/spec.md').read_text().splitlines() if l.startswith('|')]
    assert 'negative' in rows[5] and all('negative' not in r for r in rows[:5])
    image = (repo/'docs/source/export-layout.png').read_bytes()
    assert image == layout_png([v.upper() for v in data['secret_order']])
    assert all(v.encode() not in image for v in data['secret_order'])
    w,h,pixel = decode_png(image)
    assert (w,h)==(601,110) and pixel(0,0)==(60,60,60)
    assert subprocess.check_output(['git','-C',str(repo),'remote'],text=True)==''
    result = subprocess.run(['python3','-c','from tally import remove; s={"a":2}; assert remove(s,"a",3)==-1'],cwd=repo)
    assert result.returncode == 0  # actual initial bug, not a repaired seed
    with environment(tmp_path/'canary'):
        config = load_config(data['config'])
        assert config.profiles == DEFAULT_PROFILES
        assert config.state_dir.is_relative_to(tmp_path/'canary')
        assert str(config.commands['test'][0]).endswith('/python3')


def test_existing_foreign_workdir_refused(tmp_path):
    (tmp_path/'foreign').write_text('preserve')
    with pytest.raises(ValueError,match='empty'):
        setup(tmp_path)
    assert (tmp_path/'foreign').read_text()=='preserve'


def test_verifier_rejects_unexecuted_project(tmp_path):
    data = setup(tmp_path/'canary')
    result = verify(data)
    assert result['verdict']=='BLOCK'
    assert {'batch1_easy_hard','native_calls','bug_red','row4_digest','fresh_batch2','pixel_csv_order'} <= set(result['failures'])
    assert json.loads((Path(data['root'])/'verification.json').read_text())==result


def test_cli_canary_verify_cannot_turn_empty_evidence_into_pass(tmp_path,capsys):
    setup(tmp_path/'canary')
    assert main(['canary','--workdir',str(tmp_path/'canary'),'--verify'])==2
    assert 'BLOCK' in capsys.readouterr().out
