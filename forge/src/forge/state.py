"""SQLite durability, per-project advisory lock and durable artifact replacement.

Only controller processes use this module. Target execution must never see its root.
"""

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from forge.quota_metrics import METRICS_SCHEMA

SCHEMA_VERSION = 3
CORE_VERSION = 2  # legacy engine schema; metrics (v3) is an explicit opt-in migration
ENGINE_SCHEMA = """
CREATE TABLE call_effects (call TEXT PRIMARY KEY REFERENCES calls(id), applied REAL NOT NULL);
CREATE TABLE profile_counters (
 profile TEXT NOT NULL, run TEXT NOT NULL, protocol INTEGER NOT NULL DEFAULT 0,
 unknown_fail INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(profile,run)
);
"""
SCHEMA = """
CREATE TABLE runs (
 id TEXT PRIMARY KEY, state TEXT NOT NULL, started REAL NOT NULL,
 pid INTEGER, proc_start TEXT, ended REAL, blocked TEXT CHECK(blocked IS NULL OR json_valid(blocked))
);
CREATE TABLE batches (
 id TEXT PRIMARY KEY, run TEXT REFERENCES runs(id), n INTEGER NOT NULL,
 state TEXT NOT NULL, input_fp TEXT, accepted INTEGER NOT NULL DEFAULT 0 CHECK(accepted >= 0),
 role_attempts TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(role_attempts)),
 blocked TEXT CHECK(blocked IS NULL OR json_valid(blocked)), UNIQUE(run,n)
);
CREATE TABLE tasks (
 id TEXT PRIMARY KEY, batch TEXT NOT NULL REFERENCES batches(id), kind TEXT NOT NULL,
 difficulty TEXT NOT NULL CHECK(difficulty IN ('easy','hard')), work_key TEXT NOT NULL,
 req_ids TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(req_ids)),
 depends_on TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(depends_on)),
 state TEXT NOT NULL, prior_state TEXT,
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
 plan_attempts INTEGER NOT NULL DEFAULT 0 CHECK(plan_attempts >= 0),
 impl_attempts INTEGER NOT NULL DEFAULT 0 CHECK(impl_attempts >= 0),
 fix_rounds INTEGER NOT NULL DEFAULT 0 CHECK(fix_rounds >= 0),
 escalated INTEGER NOT NULL DEFAULT 0 CHECK(escalated IN (0,1)),
 rebases INTEGER NOT NULL DEFAULT 0 CHECK(rebases >= 0),
 session_id TEXT, session_profile TEXT, base_commit TEXT, red_commit TEXT,
 candidate_commit TEXT, candidate_tree TEXT, candidate_ref TEXT, workspace TEXT,
 blocked TEXT CHECK(blocked IS NULL OR json_valid(blocked)),
 waiting_role TEXT, recheck_at REAL, waiting_accounts TEXT CHECK(waiting_accounts IS NULL OR json_valid(waiting_accounts)),
 input_fp TEXT, coding_seconds REAL NOT NULL DEFAULT 0 CHECK(coding_seconds >= 0),
 unblock_count INTEGER NOT NULL DEFAULT 0 CHECK(unblock_count >= 0),
 metadata TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata)),
 CHECK(state != 'WAITING' OR (recheck_at IS NOT NULL AND waiting_role IS NOT NULL))
);
CREATE TABLE accounts (
 id TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'unknown' CHECK(state IN ('available','unknown','waiting')),
 recheck_at REAL, k INTEGER NOT NULL DEFAULT 0 CHECK(k >= 0), last_ok REAL, outcome TEXT, reason TEXT,
 CHECK(state != 'waiting' OR recheck_at IS NOT NULL)
);
CREATE TABLE calls (
 id TEXT PRIMARY KEY, run TEXT REFERENCES runs(id), batch TEXT REFERENCES batches(id), task TEXT REFERENCES tasks(id),
 role TEXT NOT NULL, profile TEXT NOT NULL, account TEXT REFERENCES accounts(id),
 argv_sha TEXT, model TEXT, effort TEXT, binary_path TEXT, binary_version TEXT,
 preset_sid TEXT, session_in TEXT, session_out TEXT, pid INTEGER, pgid INTEGER, proc_start TEXT,
 actual_model TEXT, status TEXT NOT NULL CHECK(status IN ('launching','running','done','interrupted')),
 outcome TEXT, started REAL NOT NULL, ended REAL, raw_dir TEXT,
 result_path TEXT, recheck_at REAL, usage TEXT CHECK(usage IS NULL OR json_valid(usage)),
 sandbox TEXT, manifest_path TEXT,
 CHECK(status != 'running' OR (pid IS NOT NULL AND pgid IS NOT NULL AND proc_start IS NOT NULL))
);
CREATE TABLE profiles_disabled (
 profile TEXT PRIMARY KEY, run TEXT REFERENCES runs(id), reason TEXT NOT NULL, outcome TEXT NOT NULL, at REAL NOT NULL
);
CREATE TABLE cancels (id INTEGER PRIMARY KEY, target TEXT NOT NULL, requested REAL NOT NULL, handled REAL);
CREATE TABLE commits (
 task TEXT PRIMARY KEY REFERENCES tasks(id), candidate TEXT NOT NULL UNIQUE, tree TEXT NOT NULL,
 old_head TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','done')),
 gate_call TEXT REFERENCES calls(id), review_call TEXT REFERENCES calls(id), accepted_at REAL
);
CREATE TABLE owner (id INTEGER PRIMARY KEY CHECK(id=1), satisfied_at REAL, note TEXT,
 CHECK(satisfied_at IS NULL OR (note IS NOT NULL AND length(trim(note)) > 0)));
INSERT INTO owner(id) VALUES(1);
CREATE TABLE handoffs (
 id INTEGER PRIMARY KEY, task TEXT NOT NULL REFERENCES tasks(id), at REAL NOT NULL,
 old_profile TEXT, new_profile TEXT, old_sid TEXT, new_sid TEXT, reason TEXT NOT NULL, artifact_path TEXT NOT NULL
);
CREATE TABLE unblocks (
 id INTEGER PRIMARY KEY, task TEXT NOT NULL REFERENCES tasks(id), at REAL NOT NULL,
 note TEXT NOT NULL, fresh_session INTEGER NOT NULL DEFAULT 0 CHECK(fresh_session IN (0,1))
);
CREATE TABLE artifacts (
 path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, call TEXT REFERENCES calls(id), task TEXT REFERENCES tasks(id),
 kind TEXT NOT NULL, created REAL NOT NULL
);
CREATE TABLE smoke (
 profile TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, status TEXT NOT NULL, at REAL NOT NULL,
 capabilities TEXT NOT NULL CHECK(json_valid(capabilities)), manifest_path TEXT NOT NULL
);
CREATE INDEX tasks_work_key ON tasks(work_key,input_fp);
CREATE INDEX calls_unfinished ON calls(status);
CREATE INDEX tasks_batch_state ON tasks(batch,state);
"""


class StateError(RuntimeError):
    """Unsupported schema or invalid storage operation."""


class ControllerLocked(StateError):
    """Another controller owns this project."""


@contextmanager
def controller_lock(root: Path):
    """Hold a non-inheritable flock for the entire controller lifetime.

    Never unlink the lock file: replacing its inode would allow two owners.
    Operator requests later use their own short SQLite transactions, not this lock.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(root / 'controller.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    acquired = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ControllerLocked('another controller owns this project') from exc
        acquired = True
        yield
    finally:
        if acquired:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


class State:
    """Versioned SQLite state. Transitions explicitly use transaction().

    Schema initialization serializes through BEGIN IMMEDIATE. Future migrations
    must be explicit; a newer/unknown version is rejected rather than overwritten.
    """

    def __init__(self, root: Path, metrics: bool = False):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        db_path = self.root / 'forge.db'
        if db_path.is_symlink():
            raise StateError('database must not be a symlink')
        self.db = sqlite3.connect(db_path, isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute('PRAGMA foreign_keys=ON')
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA synchronous=FULL')
            with self.transaction() as db:
                version = db.execute('PRAGMA user_version').fetchone()[0]
                if version == 0:
                    if db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                        raise StateError('unversioned nonempty database')
                    _apply(db, SCHEMA + ENGINE_SCHEMA + (METRICS_SCHEMA if metrics else ''), metrics)
                elif version == 1:
                    _apply(db, ENGINE_SCHEMA + (METRICS_SCHEMA if metrics else ''), metrics)
                elif version == CORE_VERSION and metrics:
                    _apply(db, METRICS_SCHEMA, metrics)
                elif version not in (CORE_VERSION, SCHEMA_VERSION):
                    raise StateError(f'unsupported schema version {version}')
        except BaseException:
            self.db.close()
            raise

    @contextmanager
    def transaction(self):
        """A complete transition commits once or rolls back, including on interrupt."""
        if self.db.in_transaction:
            raise StateError('nested transactions are not supported')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield self.db
            self.db.execute('COMMIT')
        except BaseException:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _apply(db, ddl, metrics):
    """Explicit migration step inside the caller's BEGIN IMMEDIATE; bumps the version last."""
    for statement in ddl.split(';'):
        if statement.strip():
            db.execute(statement)
    db.execute(f'PRAGMA user_version={SCHEMA_VERSION if metrics else CORE_VERSION}')


def atomic_write(path: Path, content: bytes) -> None:
    """Replace a controller-selected artifact: file fsync → rename → dir fsync.

    Parent must already exist and be controller-owned. A failure before rename
    leaves the old artifact intact. Callers may reference it in SQLite only after
    this function returns; an orphan after a crash is safe to recover/ignore.
    """
    path = Path(path)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            os.unlink(temporary)


def atomic_json(path: Path, value) -> None:
    atomic_write(path, (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8'))
