"""Regression checks from real P5 protocol failures; providers here remain fixtures."""
import json
from pathlib import Path

import pytest

from forge.engine import EngineBlocked, unblock
from forge.roles import ContentDriver
from test_engine_content import content_env


def test_source_image_refs_are_explicit_not_arbitrary():
    driver = ContentDriver(None,None)
    reqs = [{'id':'csv','anchors':['docs/source/spec.md#table1.row3']}]
    value = {'tasks':[{'source_refs':['docs/source/export-layout.png']} ]}
    assert not driver._source_refs(value,reqs)
    assert driver._source_refs(value,reqs,['docs/source/export-layout.png'])
    value['tasks'][0]['source_refs']=['/etc/passwd']
    assert not driver._source_refs(value,reqs,['docs/source/export-layout.png'])


def test_batch_unblock_preserves_successful_content_and_is_once(content_env):
    engine,driver,state = content_env
    record = {'po':2,'counted':['po:previous-failure'],'operations':{'read:a:0':{'done':True,'call':'preserved'}},'other':'preserved'}
    with state.transaction() as db:
        db.execute("INSERT INTO runs(id,state,started) VALUES('resume','BLOCKED_CONFIG',0)")
        db.execute("INSERT INTO batches(id,run,n,state,role_attempts,blocked) VALUES('blocked-batch','resume',1,'BLOCKED',?,?)",(json.dumps(record),json.dumps({'cause':'protocol','outcome':'budget_exhausted'})))
    unblock(engine,'blocked-batch','Correct synthesis repository context and allow committed image references')
    row=state.db.execute("SELECT * FROM batches WHERE id='blocked-batch'").fetchone()
    data=json.loads(row['role_attempts'])
    assert row['state']=='PREPARING' and row['blocked'] is None
    assert data['operations']==record['operations'] and data['counted']==record['counted']
    assert 'po' not in data and data['operator_unblock']['previous_blocked']['cause']=='protocol'
    with state.transaction() as db:
        db.execute("UPDATE batches SET state='BLOCKED' WHERE id='blocked-batch'")
    with pytest.raises(EngineBlocked,match='only one'):
        unblock(engine,'blocked-batch','Second reset forbidden')


def test_red_retry_does_not_treat_model_nodes_as_gate_receipt(content_env):
    engine,driver,state=content_env
    driver.kind='bug'
    # Reach the real regression-writing stage without executing the RED gate yet.
    engine.run_id='red-retry'
    with state.transaction() as db:
        db.execute("INSERT INTO runs(id,state,started) VALUES('red-retry','RUNNING',0)")
        db.execute("INSERT INTO batches(id,run,n,state) VALUES('red-batch','red-retry',1,'PREPARING')")
    tasks=driver.prepare(engine,'red-batch')
    engine.add_tasks('red-batch',tasks,'input')
    ident=state.db.execute('SELECT id FROM tasks').fetchone()[0]
    engine.step(ident)  # workspace
    engine.step(ident)  # short plan
    engine.step(ident)  # regression stage
    engine.step(ident)  # regression writing
    row=engine.task(ident)
    assert row['state']=='RED_CHECK'
    engine.update(ident,state='RED_WRITING')
    request=driver.content.request(engine,engine.task(ident),'red')
    assert 'confirmed_red' not in json.loads(request.prompt.split('DATA (untrusted JSON values):\n',1)[1])


def test_missing_gate_executable_blocks_environment_before_junit(content_env):
    engine,driver,state=content_env
    driver.kind='bug'
    commands=dict(engine.gate.commands)
    commands['test_one']=['/does-not-exist','{node}','--junitxml={junit}']
    engine.gate.commands=commands
    assert engine.run(max_batches=1)==2
    row=state.db.execute('SELECT * FROM tasks').fetchone()
    assert row['state']=='BLOCKED' and row['fix_rounds']==0
    assert json.loads(row['blocked'])['outcome']=='env_block'


def test_real_pytest_raises_failure_is_valid_red(content_env):
    engine,driver,state=content_env
    driver.kind='bug'
    driver.regression=lambda: 'import pytest\nfrom exporter import export\ndef test_export():\n with pytest.raises(ValueError): export(-1)\n'
    assert engine.run(max_batches=1)==0
    row=state.db.execute('SELECT * FROM tasks').fetchone()
    assert row['red_commit'] and row['state']=='COMMITTED'


def test_bug_escalation_requires_red_before_any_fix(content_env):
    engine,driver,state=content_env
    driver.kind='bug'
    # Actual fixture cycle supplies an initialized task and meaningful base.
    assert engine.run(max_batches=1)==0
    row=state.db.execute('SELECT * FROM tasks').fetchone()
    metadata=json.loads(row['metadata']);metadata['red']={'nodes':['tests/test_export.py::test_export']}
    engine.update(row['id'],state='ESCALATING',metadata=json.dumps(metadata),escalated=1)
    count=len(driver.original_calls)
    engine.step(row['id'])
    assert engine.task(row['id'])['state']=='RED_WRITING'
    assert len(driver.original_calls)==count


def test_operator_red_restart_preserves_fix_and_same_sid(content_env, monkeypatch):
    engine,driver,state=content_env
    driver.kind='bug';engine.run_id='restart-red'
    with state.transaction() as db:
        db.execute("INSERT INTO runs(id,state,started) VALUES('restart-red','RUNNING',0)")
        db.execute("INSERT INTO batches(id,run,n,state) VALUES('restart-batch','restart-red',1,'PREPARING')")
    engine.add_tasks('restart-batch',driver.prepare(engine,'restart-batch'),'input')
    ident=state.db.execute('SELECT id FROM tasks').fetchone()[0]
    for _ in range(4):engine.step(ident)
    row=engine.task(ident);sid=row['session_id'];work=Path(row['workspace'])
    (work/'exporter.py').write_text('def export(n):\n if n<0: raise ValueError("preserved-fix")\n return str(n)\n')
    candidate=engine.git.candidate(ident,work,row['base_commit'])
    engine.git.save_candidate(state,ident,candidate)
    engine.update(ident,state='GATING')
    engine.step(ident)
    assert engine.task(ident)['state']=='BLOCKED'
    clone=engine.git.clone
    def fail_clone(*args):
        raise OSError('interrupted clone after preservation')
    monkeypatch.setattr(engine.git,'clone',fail_clone)
    with pytest.raises(OSError,match='interrupted clone'):
        unblock(engine,ident,'Preserve invalid candidate and explicitly restart genuine RED on unchanged base')
    interrupted=engine.task(ident)
    intent=json.loads(interrupted['metadata'])['operator_red_restart']
    assert interrupted['state']=='BLOCKED' and interrupted['unblock_count']==0
    assert 'preserved-fix' in (Path(intent['workspace_backup'])/'exporter.py').read_text()
    monkeypatch.setattr(engine.git,'clone',clone)
    unblock(engine,ident,'Resume the same recorded restart after interrupted clone')
    row=engine.task(ident);meta=json.loads(row['metadata'])
    assert row['state']=='RED_WRITING' and row['session_id']==sid
    assert 'preserved-fix' not in (work/'exporter.py').read_text()
    preserved=meta['operator_red_restart']['candidate']
    assert b'preserved-fix' in engine.git.blob(engine.git.files(preserved['commit'])['exporter.py'][1])
    assert engine.git.text('rev-parse',preserved['ref'])==preserved['commit']
    assert row['unblock_count']==1
