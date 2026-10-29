"""CLI init of a committed empty tree. Real git and the forge entry point."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from forge.docsindex import SKELETON


class EmptyInitCliTests(unittest.TestCase):
    def test_cli_init_committed_empty_tree(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp_path = Path(raw)
            target = tmp_path / 'repo'
            target.mkdir()
            subprocess.check_call(['git', 'init', '--initial-branch=main', str(target)])
            subprocess.check_call(['git', '-C', str(target), '-c', 'user.name=Forge Empty',
                                   '-c', 'user.email=empty@localhost', 'commit', '--allow-empty', '-m', 'empty bootstrap'])
            config_home = tmp_path / 'config'
            config = config_home / 'forge' / 'projects' / 'demo.toml'
            config.parent.mkdir(parents=True)
            config.write_text(f'target_repo = "{target}"\n')
            config.chmod(0o600)
            env = os.environ.copy()
            env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1] / 'src')
            env['PYTHONDONTWRITEBYTECODE'] = '1'
            env['XDG_CONFIG_HOME'] = str(config_home)
            env['XDG_STATE_HOME'] = str(tmp_path / 'state')
            argv = [sys.executable, '-m', 'forge', '--config', str(config), 'init']
            first = subprocess.run(argv, env=env, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(first.stderr, '')
            commit = first.stdout.strip()
            self.assertEqual(subprocess.check_output(['git', '-C', str(target), 'rev-parse', 'refs/heads/main'], text=True).strip(), commit)
            for path in SKELETON:
                subprocess.check_call(['git', '-C', str(target), 'cat-file', '-e', f'{commit}:docs/forge/{path}'])
            self.assertFalse((target / 'docs').exists())
            second = subprocess.run(argv, env=env, capture_output=True, text=True)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(second.stdout.strip(), commit)
