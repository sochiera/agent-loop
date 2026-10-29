import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from forge.state import ControllerLocked, State, StateError, atomic_json, atomic_write, controller_lock


def test_second_controller_refused(tmp_path):
    script = '''
import sys
from pathlib import Path
from forge.state import controller_lock, ControllerLocked
try:
    with controller_lock(Path(sys.argv[1])):
        print('acquired')
except ControllerLocked:
    raise SystemExit(2)
'''
    with controller_lock(tmp_path):
        child = subprocess.run([sys.executable, '-c', script, str(tmp_path)], capture_output=True, text=True)
        assert child.returncode == 2, child.stderr
        with pytest.raises(ControllerLocked):
            with controller_lock(tmp_path):
                pytest.fail('second lock acquired')
    child = subprocess.run([sys.executable, '-c', script, str(tmp_path)], capture_output=True, text=True)
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == 'acquired'


def test_lock_released_after_exception(tmp_path):
    with pytest.raises(RuntimeError):
        with controller_lock(tmp_path):
            raise RuntimeError('abort')
    with controller_lock(tmp_path):
        assert (tmp_path / 'controller.lock').exists()


def test_state_txn_atomic(tmp_path):
    with State(tmp_path) as state:
        assert state.db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        assert state.db.execute('PRAGMA synchronous').fetchone()[0] == 2
        with pytest.raises(RuntimeError):
            with state.transaction() as db:
                db.execute("INSERT INTO runs(id,state,started) VALUES('rollback','RUNNING',1)")
                db.execute("UPDATE owner SET note='must rollback' WHERE id=1")
                raise RuntimeError('abort transition')
        assert state.db.execute('SELECT count(*) FROM runs').fetchone()[0] == 0
        assert state.db.execute('SELECT note FROM owner').fetchone()[0] is None
        with state.transaction() as db:
            db.execute("INSERT INTO runs(id,state,started,pid,proc_start) VALUES('r','RUNNING',1,123,'start')")
    with State(tmp_path) as state:
        assert state.db.execute('SELECT pid FROM runs').fetchone()[0] == 123
        with pytest.raises(StateError, match='nested'):
            with state.transaction():
                with state.transaction():
                    pass
        assert not state.db.in_transaction


def test_schema_supports_later_phases_without_executing_them(tmp_path):
    with State(tmp_path) as state, state.transaction() as db:
        db.execute("INSERT INTO runs VALUES('r','RUNNING',1,123,'start',NULL,NULL)")
        db.execute("INSERT INTO batches(id,run,n,state,role_attempts) VALUES('b','r',1,'OPEN','{\"po\":1}')")
        db.execute("INSERT INTO accounts(id,state,recheck_at) VALUES('codex','waiting',900)")
        db.execute("""INSERT INTO tasks(id,batch,kind,difficulty,work_key,state,session_id,session_profile,
                   base_commit,red_commit,candidate_commit,candidate_tree,blocked)
                   VALUES('t','b','bug','hard','key','BLOCKED','sid','sol-high','base','red','candidate','tree',?)""",
                   (json.dumps({'cause': 'session_lost', 'evidence_paths': ['calls/c/result.json'],
                                'preserved': {'workspace': 'ws/t/src'}, 'unblock_condition': 'explicit handoff'}),))
        db.execute("""INSERT INTO calls(id,task,role,profile,account,status,started,pid,pgid,proc_start,
                   preset_sid,session_in,session_out,model,effort,actual_model)
                   VALUES('c','t','implement','sol-high','codex','running',2,123,123,'start','preset','in','out',
                          'gpt-6.1-sol','high',NULL)""")
        db.execute("INSERT INTO commits(task,candidate,tree,old_head,state) VALUES('t','candidate','tree','base','pending')")
        db.execute("INSERT INTO cancels(target,requested) VALUES('t',3)")
        db.execute("UPDATE owner SET satisfied_at=4,note='operator explicitly approved' WHERE id=1")
    with State(tmp_path) as state:
        assert state.db.execute('SELECT session_out FROM calls').fetchone()[0] == 'out'
        assert state.db.execute('SELECT state FROM commits').fetchone()[0] == 'pending'
        assert state.db.execute('SELECT satisfied_at FROM owner').fetchone()[0] == 4
        assert state.db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert state.db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


@pytest.mark.parametrize('sql', [
    "INSERT INTO accounts(id,state) VALUES('bad','waiting')",
    "INSERT INTO calls(id,role,profile,status,started) VALUES('c','po','sol','running',1)",
    "INSERT INTO batches(id,run,n,state) VALUES('b','missing',1,'OPEN')",
    "UPDATE owner SET satisfied_at=1 WHERE id=1",
    "INSERT INTO owner(id) VALUES(2)",
])
def test_schema_rejects_invalid_durable_records(tmp_path, sql):
    with State(tmp_path) as state:
        with pytest.raises(sqlite3.IntegrityError):
            with state.transaction() as db:
                db.execute(sql)


def test_unknown_schema_refused_without_replacement(tmp_path):
    with State(tmp_path) as state:
        state.db.execute('PRAGMA user_version=99')
    with pytest.raises(StateError, match='unsupported schema version 99'):
        State(tmp_path)
    with sqlite3.connect(tmp_path / 'forge.db') as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 99


def test_atomic_artifact_replacement_and_permissions(tmp_path):
    path = tmp_path / 'result.json'
    atomic_json(path, {'sid': 'first'})
    atomic_json(path, {'sid': 'resumed'})
    assert json.loads(path.read_text()) == {'sid': 'resumed'}
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.iterdir()) == [path]


def test_atomic_write_failure_preserves_old_artifact(tmp_path, monkeypatch):
    path = tmp_path / 'result.json'
    atomic_write(path, b'old')

    def fail(*args):
        raise OSError('simulated rename failure')

    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(OSError, match='rename failure'):
        atomic_write(path, b'new')
    assert path.read_bytes() == b'old'
    assert list(tmp_path.iterdir()) == [path]


def test_atomic_fsync_order(tmp_path, monkeypatch):
    events = []
    fsync, replace = os.fsync, os.replace

    def sync(fd):
        events.append('fsync')
        fsync(fd)

    def rename(src, dst):
        events.append('rename')
        replace(src, dst)

    monkeypatch.setattr(os, 'fsync', sync)
    monkeypatch.setattr(os, 'replace', rename)
    atomic_write(tmp_path / 'artifact', b'complete')
    assert events == ['fsync', 'rename', 'fsync']


METRICS_TABLES = {'quota_observation', 'quota_events', 'quota_episodes', 'quota_spans'}


def _tables(path):
    with sqlite3.connect(path) as db:
        return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}, \
            db.execute('PRAGMA user_version').fetchone()[0]


def test_fresh_install_has_engine_and_metrics_tables(tmp_path):
    State(tmp_path, metrics=True).close()
    tables, version = _tables(tmp_path / 'forge.db')
    assert version == 3 and METRICS_TABLES | {'runs', 'call_effects', 'profile_counters'} <= tables


def test_legacy_open_keeps_core_v2_and_accepts_v3(tmp_path):
    State(tmp_path).close()
    tables, version = _tables(tmp_path / 'forge.db')
    assert version == 2 and not METRICS_TABLES & tables
    State(tmp_path, metrics=True).close()
    State(tmp_path).close()  # a legacy reader accepts v3 and never downgrades it
    assert _tables(tmp_path / 'forge.db')[1] == 3


@pytest.mark.parametrize('legacy', [1, 2])
def test_explicit_migration_to_v3_preserves_rows(tmp_path, legacy):
    from forge.state import ENGINE_SCHEMA, SCHEMA
    with sqlite3.connect(tmp_path / 'forge.db') as db:
        db.executescript(SCHEMA + (ENGINE_SCHEMA if legacy == 2 else ''))
        db.execute("INSERT INTO runs(id,state,started) VALUES('r','DONE',1)")
        db.execute(f'PRAGMA user_version={legacy}')
    with State(tmp_path, metrics=True) as state:
        assert state.db.execute('SELECT id FROM runs').fetchone()[0] == 'r'
    tables, version = _tables(tmp_path / 'forge.db')
    assert version == 3 and METRICS_TABLES | {'call_effects', 'profile_counters'} <= tables
    State(tmp_path).close()  # v3 reopen is a no-op


def test_newer_schema_rejected_without_metrics_mutation(tmp_path):
    with sqlite3.connect(tmp_path / 'forge.db') as db:
        db.execute('CREATE TABLE future(x)')
        db.execute('PRAGMA user_version=4')
    with pytest.raises(StateError, match='unsupported schema version 4'):
        State(tmp_path)
    tables, version = _tables(tmp_path / 'forge.db')
    assert version == 4 and tables == {'future'}
