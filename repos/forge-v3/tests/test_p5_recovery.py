"""Real Git/state regressions from blocked P5; provider content is explicitly FIXTURE."""
import json
from pathlib import Path

from forge.engine import unblock
from test_engine_content import content_env
from test_gitops import project, hostgit


def test_operator_regression_restart_uses_current_accepted_base(content_env):
    engine, driver, state = content_env
    engine.run_id = 'restart-current'
    with state.transaction() as db:
        db.execute("INSERT INTO runs(id,state,started) VALUES('restart-current','RUNNING',0)")
        db.execute("INSERT INTO batches(id,run,n,state) VALUES('restart-current-batch','restart-current',1,'TASKS')")
    ident = engine.add_tasks('restart-current-batch', [{'id':'blocked-bug','kind':'bug','difficulty':'easy',
        'req_ids':['reject-negative'],'target_paths':['exporter.py','tests/test_export.py'],'depends_on':[]}], 'input')[0]
    engine.step(ident)  # actual selected-base clone; no provider fixture needed
    engine.update(ident, session_id='retained-fixture-sid', session_profile='sol-high')
    engine.metadata(ident, red={'nodes':['tests/test_export.py::test_export']})
    task = engine.task(ident)
    original_base, sid = task['base_commit'], task['session_id']
    work = Path(task['workspace'])
    (work / 'exporter.py').write_text('def export(n):\n if n<0: raise ValueError("preserved-fix")\n return str(n)\n')
    invalid = engine.git.candidate(ident, work, original_base)
    engine.git.save_candidate(state, ident, invalid)
    engine.update(ident, state='GATING')
    engine.step(ident)
    assert engine.task(ident)['state'] == 'BLOCKED'
    # Exactly the P5 sequence: an independent task accepts while this bug is blocked.
    peer = engine.git.export(original_base, state.root / 'ws/peer/src')
    (peer / 'accepted_peer.py').write_text('CSV_FEATURE = "preserve this accepted work"\n')
    (peer / 'tests/test_export.py').write_text('def test_export():\n assert True\ndef test_peer():\n assert 2+2==4\n')
    accepted = engine.git.candidate('peer', peer, original_base)
    assert engine.git.cas(accepted)
    unblock(engine, ident, 'Preserve rejected fix; start a genuine new RED cycle on current accepted main')
    restarted = engine.task(ident)
    assert restarted['base_commit'] == accepted.commit
    assert (work / 'accepted_peer.py').read_text() == (peer / 'accepted_peer.py').read_text()
    assert (work / 'tests/test_export.py').read_bytes() == (peer / 'tests/test_export.py').read_bytes()
    assert 'preserved-fix' not in (work / 'exporter.py').read_text()
    assert restarted['session_id'] == sid and restarted['state'] == 'RED_WRITING'
    saved = json.loads(restarted['metadata'])['operator_red_restart']['candidate']
    assert b'preserved-fix' in engine.git.blob(engine.git.files(saved['commit'])['exporter.py'][1])


def test_plain_clone_head_index_and_files_match_selected_base(project):
    p = project
    peer = p.git.export(p.base, p.state.root / 'ws/peer/src')
    (peer / 'later.txt').write_text('new accepted main, deliberately not the selected base\n')
    accepted = p.git.candidate('peer', peer, p.base)
    assert p.git.cas(accepted)
    clone = p.git.clone(p.base, p.state.root / 'ws/older/src')
    assert hostgit(clone, 'rev-parse', 'HEAD') == p.base
    assert hostgit(clone, 'status', '--porcelain') == ''
    assert not (clone / 'later.txt').exists()
