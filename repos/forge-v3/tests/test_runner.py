"""Runner tests: REAL bwrap execution of FIXTURE native stand-ins (python scripts mounted at
the /opt/forge-native alias). Event shapes are copied from real captures (codex 0.159.3
exec --json, 2026-10-22; claude/grok json results, 2026-10-21). These prove launch order,
durability, streaming and teardown; they are not live provider evidence.
"""

import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

import pytest

from forge import runner, sandbox
from forge.adapters import adapter_for
from forge.adapters.base import Call
from forge.state import State
from test_sandbox import leaf, make_layout

CODEX_FIXTURE = r'''#!/usr/bin/python3
import json, os, sys, time
sys.stdin.read()
open('/work/argv.json', 'w').write(json.dumps(sys.argv[1:]))
print(json.dumps({"type": "thread.started", "thread_id": "01a16513-fixture"}), flush=True)
while not os.path.exists('/work/go'):
    time.sleep(0.05)
print(json.dumps({"type": "turn.started"}), flush=True)
print(json.dumps({"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "working"}}))
print(json.dumps({"type": "item.completed", "item": {"id": "item_1", "type": "agent_message",
      "text": "Done; no quota or 429 issue.\n```json\n{\"nonce\": \"7\"}\n```"}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 2}}))
'''
CODEX_QUOTA = r'''#!/usr/bin/python3
import json, sys
sys.stdin.read()
print(json.dumps({"type": "thread.started", "thread_id": "t-q"}))
print(json.dumps({"type": "turn.failed", "error": {"message": "usage_limit_reached", "resets_at": 4102444800}}))
sys.exit(1)
'''
SLEEPER = r'''#!/usr/bin/python3
import json, sys, time
print(json.dumps({"type": "thread.started", "thread_id": "t-s"}), flush=True)
time.sleep(300)  # MARKER
'''
CLAUDE_FIXTURE = r'''#!/usr/bin/python3
import json, sys
args = sys.argv[1:]
sid = args[args.index('--session-id') + 1] if '--session-id' in args else args[args.index('--resume') + 1]
stdin = sys.stdin.read()
open('/work/stdin.txt', 'w').write(stdin)
model = args[args.index('--model') + 1]
reported = 'claude-sonnet-5-5' if 'MISMATCH' in stdin else model
print(json.dumps({"type": "system", "subtype": "init", "session_id": sid, "model": model}))
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "session_id": sid,
                  "result": "```json\n{\"ok\": true}\n```", "usage": {"output_tokens": 1},
                  "modelUsage": {reported: {"outputTokens": 1}, "claude-haiku-5-5": {}}}))
'''
GROK_FIXTURE = r'''#!/usr/bin/python3
import json, sys
args = sys.argv[1:]
sid = args[args.index('--session-id') + 1]
prompt = open(args[args.index('--prompt-file') + 1]).read()
print(json.dumps({"text": "```json\n{\"ok\": true}\n```", "stopReason": "end_turn", "sessionId": sid,
                  "usage": {"output_tokens": 1}, "num_turns": 1,
                  "modelUsage": {"grok-4.7-build": {"outputTokens": 1}}}, indent=2))
'''


@pytest.fixture
def env(tmp_path):
    roots, bins = make_layout(tmp_path)
    (roots.state / 'forge.db').unlink()
    state = State(roots.state)
    yield roots, bins, state
    state.close()


def launch(roots, bins, state, harness, script, *, model, effort='high', writable=True, timeout=30,
           preset=None, resume=None, prompt='do it', **kw):
    bins[harness].write_text(script)
    work = leaf(roots, f't-{uuid.uuid4().hex[:6]}')
    call = Call(f'c{uuid.uuid4().hex[:10]}', 'implement', f'{harness}-p', harness, model, effort, work, writable,
                prompt, resume_sid=resume, preset_sid=preset, timeout=timeout, max_turns=5)
    binds = {'codex': ([f'{roots.home}/.codex'], []),
             'claude': ([f'{roots.home}/.claude', f'{roots.home}/.claude.json'], [f'{roots.home}/.local/share/claude']),
             'grok': ([f'{roots.home}/.grok'], [])}[harness]

    def spec_for(args):
        return sandbox.agent_spec(roots, harness=harness, binary=bins[harness], native_args=tuple(args), work=work,
                                  writable=writable, input_dir=roots.state / 'calls' / call.id / 'in',
                                  binds_rw=binds[0], binds_ro=binds[1])
    result = runner.run_call(state, call, adapter_for(harness), kw.pop('spec_for', spec_for),
                             calls_root=roots.state / 'calls', account=harness, **kw)
    return call, work, result


def row(state, call_id):
    return state.db.execute('SELECT * FROM calls WHERE id=?', (call_id,)).fetchone()


def test_sid_persisted_before_final_output_and_streaming_final_text(env):
    roots, bins, state = env
    observed = {}
    work_holder = {}
    original = sandbox.agent_spec

    def watcher():
        db = sqlite3.connect(roots.state / 'forge.db')
        deadline = time.time() + 20
        while time.time() < deadline:
            got = db.execute("SELECT status, session_out, pid, pgid, proc_start FROM calls").fetchone()
            if got and got[1] and 'work' in work_holder:
                stdout = list((roots.state / 'calls').glob('*/stdout'))[0].read_text()
                observed.update(status=got[0], sid=got[1], pid=got[2], pgid=got[3], start=got[4],
                                final_seen='turn.completed' in stdout)
                (work_holder['work'] / 'go').write_text('')
                return
            time.sleep(0.02)

    def capture(roots_, **kw):
        work_holder['work'] = Path(kw['work'])
        return original(roots_, **kw)
    sandbox.agent_spec = capture
    try:
        thread = threading.Thread(target=watcher)
        thread.start()
        call, work, result = launch(roots, bins, state, 'codex', CODEX_FIXTURE, model='gpt-6.1-sol', effort='high')
        thread.join()
    finally:
        sandbox.agent_spec = original
    assert observed['status'] == 'running' and observed['sid'] == '01a16513-fixture'
    assert observed['pid'] == observed['pgid'] and observed['start'] and not observed['final_seen']
    assert result.outcome == 'ok' and result.final_json == {'nonce': '7'}
    assert result.final_text.startswith('Done; no quota')  # last agent_message, not an earlier one
    assert result.actual_model is None and result.model_match is None  # provider did not report a model
    assert result.usage == {'input_tokens': 3, 'output_tokens': 2}
    argv = json.loads((work / 'argv.json').read_text())
    assert argv[:5] == ['exec', '-m', 'gpt-6.1-sol', '-c', 'model_reasoning_effort="high"']
    stored = row(state, call.id)
    assert stored['status'] == 'done' and stored['outcome'] == 'ok' and stored['sandbox'] == 'bwrap'
    saved = json.loads(Path(stored['result_path']).read_text())
    assert saved['sid'] == '01a16513-fixture'
    manifest = json.loads(Path(stored['manifest_path']).read_text())
    assert manifest['argv'][0] == '/usr/bin/bwrap' and '--unshare-all' in manifest['argv']


def test_quota_failure_is_account_wait_with_parsed_reset(env):
    roots, bins, state = env
    _, _, result = launch(roots, bins, state, 'codex', CODEX_QUOTA, model='gpt-6.1-sol')
    assert result.outcome == 'quota_wait' and result.recheck_at == 4102444800
    assert result.effects['account_wait'] and not result.effects['coding_budget']


def test_timeout_kills_whole_group(env, monkeypatch):
    roots, bins, state = env
    monkeypatch.setattr(runner, 'KILL_GRACE', 1.0)
    marker = f'MARKER-{uuid.uuid4().hex}'
    call, _, result = launch(roots, bins, state, 'codex', SLEEPER.replace('MARKER', marker), model='gpt-6.1-sol',
                             timeout=1.5)
    assert result.outcome == 'timeout' and result.effects['coding_budget']
    assert row(state, call.id)['session_out'] == 't-s'
    time.sleep(0.2)
    leftovers = [p for p in Path('/proc').iterdir() if p.name.isdigit() and _cmd_has(p, marker)]
    assert leftovers == [] and not runner.group_alive(row(state, call.id)['pgid'])


def _cmd_has(proc, marker):
    try:
        return marker.encode() in (proc / 'cmdline').read_bytes()
    except OSError:
        return False


def test_cancel_kills_group(env):
    roots, bins, state = env
    call, _, result = launch(roots, bins, state, 'codex', SLEEPER, model='gpt-6.1-sol', should_cancel=lambda: True)
    assert result.outcome == 'cancelled' and not result.effects['coding_budget']
    assert not runner.group_alive(row(state, call.id)['pgid'])


def test_claude_preset_sid_durable_before_launch_and_model_usage(env):
    roots, bins, state = env
    sid = str(uuid.uuid4())
    call, work, result = launch(roots, bins, state, 'claude', CLAUDE_FIXTURE, model='claude-opus-5-5',
                                preset=sid)
    assert result.outcome == 'ok' and result.sid == sid and result.actual_model == 'claude-opus-5-5'
    assert row(state, call.id)['preset_sid'] == sid and row(state, call.id)['session_out'] == sid
    assert (work / 'stdin.txt').read_text() == 'do it'
    _, _, mismatch = launch(roots, bins, state, 'claude', CLAUDE_FIXTURE, model='claude-opus-5-5',
                            preset=str(uuid.uuid4()), prompt='MISMATCH')
    assert mismatch.outcome == 'model_mismatch' and mismatch.effects['disable'] == 'profile'


def test_grok_multiline_json_result_and_preset_sid(env):
    roots, bins, state = env
    sid = str(uuid.uuid4())
    call, _, result = launch(roots, bins, state, 'grok', GROK_FIXTURE, model='grok-4.7', preset=sid)
    assert result.outcome == 'ok' and result.sid == sid and result.model_match is True
    assert result.actual_model == 'grok-4.7-build'
    assert row(state, call.id)['session_out'] == sid


def test_revalidation_failure_never_spawns(env):
    roots, bins, state = env
    work = leaf(roots, 'tr')

    def spec_for(args):
        spec = sandbox.agent_spec(roots, harness='codex', binary=bins['codex'], native_args=tuple(args), work=work,
                                  writable=True, input_dir=next((roots.state / 'calls').glob('*/in')),
                                  binds_rw=[f'{roots.home}/.codex'], binds_ro=[])
        if args:
            work.rename(work.with_name('src-moved'))
            work.mkdir()
        return spec
    call, _, result = launch(roots, bins, state, 'codex', CODEX_FIXTURE, model='gpt-6.1-sol', spec_for=spec_for)
    stored = row(state, call.id)
    assert result.outcome == 'env_block' and result.effects['disable'] == 'sandbox'
    assert stored['pid'] is None and stored['status'] == 'done' and 'replaced' in result.error_fields['sandbox_error']


def test_proc_start_parses_comm_with_spaces():
    assert runner.proc_start(os.getpid()).isdigit()
