"""REAL child-controller SIGKILL and bwrap teardown; FIXTURE provider content.

Host bwrap is required here: an unavailable host is a gate failure, not a skip.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from forge.engine import Engine, Request, request_cancel, unblock
from forge.runner import proc_start, recover_calls
from forge.state import controller_lock
from test_engine import FAKE_PROGRAM, FakeDriver, env, add

CHILD = '''
import sys
from forge.config import load_config
from forge.engine import Engine
from forge.state import State,controller_lock
from test_engine import FakeDriver
config=load_config(sys.argv[1])
with State(config.state_dir) as s, controller_lock(s.root):
 d=FakeDriver(config,s)
 if len(sys.argv)>2:
  d.binary.write_text(open(sys.argv[2]).read())
 e=Engine(config,s,d,clock=lambda:1000)
 t=e.task('one')
 role='fix' if t['state']=='FIXING' else 'implement'
 e.role(role,d.request(e,t,role),task='one',batch='b')
'''
SLEEPER = '''#!/usr/bin/python3
import json,time
from pathlib import Path
print(json.dumps({'sid':'durable-fixture-sid'}),flush=True)
Path('/work/preserved').write_text('unfinished work')
while True:
 Path('/work/heartbeat').write_text(str(time.time_ns()))
 time.sleep(.05)
'''


def child(engine, tmp_path, *, crash=None, script=None):
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).parent.resolve()) + os.pathsep + str(Path('src').resolve()))
    if crash:
        environment['FORGE_TEST_CRASH_AT'] = crash
    args = [sys.executable, '-c', CHILD, str(engine.config.path)]
    if script:
        path = tmp_path / 'fake-sleeper'
        path.write_text(script)
        args.append(str(path))
    return subprocess.Popen(args, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def wait_for(check, timeout=10):
    # Synchronization with actual process events, bounded by a test deadline.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(.02)
    raise AssertionError('child event deadline expired')


def prepare(engine, state):
    add(engine)
    engine.step('one')
    engine.update('one', state='IMPLEMENTING')
    with state.transaction() as db:
        db.execute("UPDATE accounts SET state='available',last_ok=1000")


@pytest.mark.parametrize('point', ['engine_before_launch', 'runner_after_launching', 'runner_after_spawn',
                                  'runner_after_pid', 'runner_after_sid', 'runner_after_result_file',
                                  'runner_after_done', 'engine_after_result', 'engine_after_effect', 'engine_after_operation'])
def test_real_sigkill_every_call_crash_boundary(env, tmp_path, point):
    engine, driver, state = env
    prepare(engine, state)
    process = child(engine, tmp_path, crash=point)
    stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == -signal.SIGKILL, (stdout, stderr)
    with controller_lock(state.root):
        engine.recover()
        assert state.db.execute("SELECT count(*) FROM calls WHERE status IN ('launching','running')").fetchone()[0] == 0
        task = engine.task('one')
        result = engine.role('implement', driver.request(engine, task, 'implement'), task='one', batch='b')
        assert result.outcome == 'ok'
    assert engine.task('one')['attempts'] == 0
    # A fully durable result is replayed rather than launching another writer.
    if point in ('runner_after_done', 'engine_after_result', 'engine_after_effect', 'engine_after_operation'):
        assert state.db.execute("SELECT count(*) FROM calls WHERE role='implement'").fetchone()[0] == 1


@pytest.mark.parametrize('role', ['implement', 'fix'])
def test_sigkill_controller_kills_live_writer_and_recovery_resumes_sid(env, tmp_path, role):
    engine, driver, state = env
    prepare(engine, state)
    if role == 'fix':
        engine.update('one', state='FIXING', session_id='durable-fixture-sid', session_profile='luna-xhigh', fix_rounds=1)
    process = child(engine, tmp_path, script=SLEEPER)
    try:
        call = wait_for(lambda: state.db.execute("SELECT * FROM calls WHERE status='running' AND session_out IS NOT NULL").fetchone())
        work = Path(engine.task('one')['workspace'])
        wait_for(lambda: (work / 'heartbeat').exists())
        assert not list((state.root / 'calls').glob('*/result.json'))
        process.kill()
        process.communicate(timeout=10)
        assert process.returncode == -signal.SIGKILL
        # PID namespace teardown is asynchronous; wait for group to stop writing.
        def gone():
            try:
                return proc_start(call['pid']) != call['proc_start']
            except OSError:
                return True
        wait_for(gone)
        before = (work / 'heartbeat').read_text()
        with controller_lock(state.root):
            engine.recover()
            task = engine.task('one')
            assert task['session_id'] == 'durable-fixture-sid'
            assert (work / 'preserved').read_text() == 'unfinished work'
            assert (work / 'heartbeat').read_text() == before
            driver.binary.write_text(FAKE_PROGRAM)
            result = engine.role(role, driver.request(engine, task, role), task='one', batch='b')
            assert result.sid == 'durable-fixture-sid'
        calls = [c for c in driver.invocations if c.role == role]
        assert len(calls) == 1 and calls[0].resume_sid == 'durable-fixture-sid'
    finally:
        if process.poll() is None:
            process.kill(); process.communicate(timeout=10)


def test_recovery_kills_surviving_group_and_never_signals_reused_pid(env, tmp_path):
    engine, driver, state = env
    # A genuine unsandboxed orphan simulates a surviving recorded group; only
    # test infrastructure launches it. The production transport always bwraps.
    process = subprocess.Popen(['/usr/bin/sleep', '300'], start_new_session=True)
    try:
        with state.transaction() as db:
            db.execute("INSERT INTO calls(id,role,profile,status,started,pid,pgid,proc_start) VALUES('orphan','implement','FIXTURE','running',1,?,?,?)",
                       (process.pid, process.pid, proc_start(process.pid)))
        recover_calls(state, grace=.2)
        process.wait(timeout=5)
        assert process.returncode < 0
        assert state.db.execute("SELECT status FROM calls WHERE id='orphan'").fetchone()[0] == 'interrupted'
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL); process.wait()
    unrelated = subprocess.Popen(['/usr/bin/sleep', '300'], start_new_session=True)
    try:
        with state.transaction() as db:
            db.execute("INSERT INTO calls(id,role,profile,status,started,pid,pgid,proc_start) VALUES('reused','implement','FIXTURE','running',1,?,?,'wrong')", (unrelated.pid, unrelated.pid))
        with pytest.raises(RuntimeError, match='cannot identify'):
            recover_calls(state, grace=.2)
        assert unrelated.poll() is None
    finally:
        os.killpg(unrelated.pid, signal.SIGKILL); unrelated.wait()


@pytest.mark.parametrize('role,fix_rounds,expect', [('implement', 0, 'IMPLEMENTING'), ('fix', 1, 'BLOCKED'), ('red', 0, 'BLOCKED')])
def test_missing_sid_recovery_reset_only_initial_implement(env, tmp_path, role, fix_rounds, expect):
    engine, driver, state = env
    prepare(engine, state)
    work = Path(engine.task('one')['workspace'])
    (work / 'preserved').write_text('interrupted diff')
    engine.update('one', fix_rounds=fix_rounds)
    with state.transaction() as db:
        db.execute("INSERT INTO calls(id,task,role,profile,status,started) VALUES('missing','one',?,'luna-xhigh','launching',1)", (role,))
    with controller_lock(state.root):
        engine.recover()
    assert engine.task('one')['state'] == expect
    if expect != 'BLOCKED':
        assert not (work / 'preserved').exists()
        assert 'interrupted diff' in (state.root / 'evidence/one/interrupted-missing.patch').read_text()
    else:
        assert (work / 'preserved').read_text() == 'interrupted diff'
        unblock(engine, 'one', 'operator handoff', fresh_session=True)
        assert engine.task('one')['state'] == 'ESCALATING'
        assert state.db.execute('SELECT reason FROM handoffs').fetchone()[0].startswith('operator authorized')


def test_cancel_live_group_preserves_workspace(env, tmp_path):
    engine, driver, state = env
    prepare(engine, state)
    process = child(engine, tmp_path, script=SLEEPER)
    try:
        call = wait_for(lambda: state.db.execute("SELECT * FROM calls WHERE status='running' AND session_out IS NOT NULL").fetchone())
        work = Path(engine.task('one')['workspace'])
        wait_for(lambda: (work / 'preserved').exists())
        assert request_cancel(state, 'one') == 'cancellation requested'
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, (stdout, stderr)
        assert engine.task('one')['state'] == 'CANCELLED'
        assert (work / 'preserved').exists()
        assert engine.task('one')['attempts'] == 0
        assert state.db.execute('SELECT count(*) FROM commits').fetchone()[0] == 0
    finally:
        if process.poll() is None:
            process.kill(); process.communicate(timeout=10)
