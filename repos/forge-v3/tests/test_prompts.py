"""Direct prompt byte compatibility and tracked template rendering (SIMULATED runtime)."""
import hashlib

from forge import prompts
from test_loop import Env, writer, review


def test_direct_contracts_match_approved_baseline_bytes():
    assert hashlib.sha256(prompts.WRITER_CONTRACT.encode()).hexdigest() == '0545aca159c0c3b633b180a20c1624dfa9beb4e041850d2eab6d7b55aa8b374f'
    assert hashlib.sha256(prompts.REVIEW_CONTRACT.encode()).hexdigest() == 'ec919330af03fee3fbd46a8c1ba2d877474e62388433d45d77b68824965b555e'


def test_direct_runtime_uses_single_renderer(tmp_path, operator_config, monkeypatch):
    env = Env(tmp_path, operator_config, monkeypatch)
    env.actions(writer(writes={'done.txt': 'done\n'}), review())
    assert env.run() == 0
    calls = env.launches()
    assert calls[0]['prompt'] == prompts.render('writer', [
        '# Goal', env.goal.read_text().strip(), '# Loop state',
        f'Accepted steps: 0. Accepted base commit: {env.base}.'])
    assert calls[1]['prompt'].endswith('# Contract\n\n' + prompts.REVIEW_CONTRACT)


def test_all_templates_render_deterministically():
    for name, contract in prompts.CONTRACTS.items():
        assert prompts.render(name, ['# Context', 'bounded']) == '# Context\n\nbounded\n\n# Contract\n\n' + contract


def test_lead_render_requires_bounded_playable_vertical_slices():
    prompt = ' '.join(prompts.render('lead', ['# Goal', 'Build a playable game.']).split())
    assert 'Choose exactly ONE useful bounded next task' in prompt
    assert 'prefer substantial, coherent playable vertical slices' in prompt
    assert 'end-to-end rules, player-facing UI, tests and relevant docs together in one bounded task per cycle' in prompt
    assert 'Avoid a sequence of tiny rule-only tasks when a cohesive playable outcome fits one task' in prompt
    assert 'Keep each slice feasible for one executor' in prompt
    assert 'justified bug, refactor, cleanup or docs tasks remain valid when they best advance the goal' in prompt
