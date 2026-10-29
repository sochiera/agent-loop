"""Adapter argv pinning and parsers. Parser inputs labelled FIXTURE are copied from real
captures (codex 0.159.3 `exec --json` 2026-10-22; claude 2.1.x `--output-format json`
result and grok `--output-format json` object 2026-10-21, grok version not recorded in
that capture). They are not live evidence for this build."""

import base64
import json

import pytest

from forge.adapters import adapter_for, final_json
from forge.adapters.base import Call
from forge.config import DEFAULT_PROFILES
from forge.doctor import quadrant_png

CODEX_LINES = [  # FIXTURE: real codex 0.159.3 event shapes (texts shortened)
    '{"type":"thread.started","thread_id":"01a16513-dd0a-7883-be16-dcc13f5da380"}',
    '{"type":"turn.started"}',
    '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"Sprawdzę stan."}}',
    '{"type":"item.started","item":{"id":"item_1","type":"command_execution","command":"/bin/bash -lc pwd"}}',
    '{"type":"item.completed","item":{"id":"item_18","type":"agent_message","text":"VERDICT: PASS"}}',
    '{"type":"turn.completed","usage":{"input_tokens":835478,"cached_input_tokens":792064,"output_tokens":19111}}',
]
CLAUDE_RESULT = {  # FIXTURE: real claude json result keys (subset)
    "type": "result", "subtype": "success", "is_error": False, "session_id": "b247c013-eaa0-4e6c-9fe8-626667a3329e",
    "result": "```json\n{\"a\": 1}\n```", "stop_reason": "end_turn", "num_turns": 7, "api_error_status": None,
    "usage": {"input_tokens": 14}, "modelUsage": {"claude-opus-5-5": {"inputTokens": 14}}, "terminal_reason": "completed"}
GROK_RESULT = {  # FIXTURE: real grok json object keys (subset)
    "text": "```json\n{\"a\": 1}\n```", "stopReason": "end_turn", "sessionId": "01a15fe6-7f15-7bf1-9b8c-9aa943023fbf",
    "requestId": "765b5a61", "usage": {"input_tokens": 67169}, "num_turns": 14,
    "modelUsage": {"grok-4.7-build": {"inputTokens": 67169}}}


def call(harness, model, effort, tmp_path, **kw):
    return Call('c1', 'implement', 'p', harness, model, effort, tmp_path, True, 'prompt', max_turns=9, **kw)


@pytest.mark.parametrize('profile', [p for p in DEFAULT_PROFILES if p.enabled], ids=lambda p: p.id)
def test_argv_pins_model_effort_initial_and_resume(profile, tmp_path):
    adapter = adapter_for(profile.harness)
    preset = '11111111-2222-3333-4444-555555555555' if profile.harness != 'codex' else None
    for kwargs in ({'preset_sid': preset}, {'resume_sid': 'SID-9'}):
        argv, _ = adapter.build_argv(call(profile.harness, profile.model, profile.effort, tmp_path, **kwargs),
                                     f'/opt/forge-native/bin/{profile.harness}', tmp_path)
        assert argv[0] == f'/opt/forge-native/bin/{profile.harness}'
        joined = ' '.join(argv)
        assert profile.model in argv
        assert (f'model_reasoning_effort="{profile.effort}"' in argv) or profile.effort in argv
        if 'resume_sid' in kwargs:
            assert 'SID-9' in argv and ('resume' in argv or '--resume' in argv)
        else:
            assert 'resume' not in joined.split()
        assert '--bare' not in argv


def test_codex_argv_shape(tmp_path):
    argv, stdin = adapter_for('codex').build_argv(call('codex', 'gpt-6-luna', 'xhigh', tmp_path), '/x/codex', tmp_path)
    assert argv == ['/x/codex', 'exec', '-m', 'gpt-6-luna', '-c', 'model_reasoning_effort="xhigh"',
                    '--dangerously-bypass-approvals-and-sandbox', '--json', '--skip-git-repo-check', '-C', '/work', '-']
    assert stdin == b'prompt'
    argv, _ = adapter_for('codex').build_argv(call('codex', 'gpt-6-luna', 'xhigh', tmp_path, resume_sid='S'),
                                              '/x/codex', tmp_path)
    assert argv[:3] == ['/x/codex', 'exec', 'resume'] and argv[-2:] == ['S', '-']


def test_unpinned_or_placeholder_rejected(tmp_path):
    for model, effort in (('TBD', 'TBD'), ('gpt-6.1-sol', ''), ('', 'high')):
        with pytest.raises(ValueError):
            adapter_for('codex').build_argv(call('codex', model, effort, tmp_path), '/x', tmp_path)
    with pytest.raises(ValueError):  # claude needs exactly one SID source
        adapter_for('claude').build_argv(call('claude', 'claude-opus-5-5', 'high', tmp_path), '/x', tmp_path)


def test_image_attached_as_payload(tmp_path):
    image = tmp_path / 'secret-name.png'
    image.write_bytes(quadrant_png(['red', 'green', 'blue', 'yellow']))
    encoded = base64.b64encode(image.read_bytes()).decode()
    argv, _ = adapter_for('codex').build_argv(call('codex', 'gpt-6.1-sol', 'high', tmp_path, images=(image,)),
                                              '/x', tmp_path)
    assert argv[argv.index('--image') + 1] == '/in/image-0.png' and (tmp_path / 'image-0.png').read_bytes() == image.read_bytes()
    argv, stdin = adapter_for('claude').build_argv(
        call('claude', 'claude-opus-5-5', 'high', tmp_path, images=(image,), preset_sid='u'), '/x', tmp_path)
    message = json.loads(stdin)
    block = message['message']['content'][1]
    assert '--input-format' in argv and block['source']['data'] == encoded and 'secret-name' not in stdin.decode()
    argv, _ = adapter_for('grok').build_argv(
        call('grok', 'grok-4.7', 'high', tmp_path, images=(image,), preset_sid='u'), '/x', tmp_path)
    blocks = json.loads(argv[argv.index('--prompt-json') + 1])
    assert blocks[1]['data'] == encoded and 'secret-name' not in ' '.join(argv)


def test_codex_parser_real_schema(tmp_path):
    adapter = adapter_for('codex')
    events = [e for e in map(adapter.parse_line, CODEX_LINES) if e]
    assert events[0].kind == 'sid' and events[0].data['sid'] == '01a16513-dd0a-7883-be16-dcc13f5da380'
    native = adapter.finish(call('codex', 'gpt-6.1-sol', 'high', tmp_path), 0, events, b'', '')
    assert native.status == 'success' and native.final_text == 'VERDICT: PASS' and native.model_match is None
    assert native.usage['output_tokens'] == 19111
    # A stream that ends without turn.completed is not a success; a turn.failed is a native failure.
    assert adapter.finish(call('codex', 'm', 'high', tmp_path), 0, events[:-1], b'', '').status == 'absent'
    failed = events + [adapter.parse_line('{"type":"turn.failed","error":{"message":"boom"}}')]
    assert adapter.finish(call('codex', 'm', 'high', tmp_path), 0, failed, b'', '').status == 'failure'


def test_claude_parser_result_and_turn_limit(tmp_path):
    adapter = adapter_for('claude')
    c = call('claude', 'claude-opus-5-5', 'high', tmp_path, preset_sid=CLAUDE_RESULT['session_id'])
    events = [adapter.parse_line(json.dumps(CLAUDE_RESULT))]
    native = adapter.finish(c, 0, events, b'', '')
    assert native.status == 'success' and native.model_match is True and final_json(native.final_text) == {'a': 1}
    limit = dict(CLAUDE_RESULT, subtype='error_max_turns', is_error=True)
    native = adapter.finish(c, 1, [adapter.parse_line(json.dumps(limit))], b'', '')
    assert native.status == 'failure' and native.turn_limit
    other = call('claude', 'claude-opus-5-5', 'high', tmp_path, preset_sid='different')
    assert adapter.finish(other, 0, events, b'', '').status == 'absent'  # SID must match preset


def test_grok_parser_real_schema(tmp_path):
    adapter = adapter_for('grok')
    c = call('grok', 'grok-4.7', 'high', tmp_path, preset_sid=GROK_RESULT['sessionId'])
    native = adapter.finish(c, 0, [], json.dumps(GROK_RESULT, indent=2).encode(), '')
    assert native.status == 'success' and native.model_match is True and native.actual_model == 'grok-4.7-build'
    wrong = adapter.finish(call('grok', 'grok-4.5', 'high', tmp_path, preset_sid=GROK_RESULT['sessionId']), 0, [],
                           json.dumps(GROK_RESULT).encode(), '')
    assert wrong.model_match is False
    limit = adapter.finish(c, 0, [], json.dumps(dict(GROK_RESULT, stopReason='max_turns')).encode(), '')
    assert limit.status == 'failure' and limit.turn_limit
    assert adapter.finish(c, 0, [], b'not json', '').status == 'absent'


def test_final_json_takes_last_fenced_object():
    assert final_json('a\n```json\n{"x": 1}\n```\nb\n```json\n{"x": 2}\n```') == {'x': 2}
    assert final_json('{"x": 1}') is None and final_json(None) is None


def test_real_compact_native_closing_fence_keeps_strict_json_object_schema():
    """Observed Luna output: valid fenced JSON with closing fence adjacent to }."""
    value = {'summary':'fixed','tests':['actual pytest result'],'docs':['docs/implemented.md']}
    assert final_json('```json\n' + json.dumps(value) + '```') == value
    assert final_json('```json\n{"text":"literal ``` in JSON"}```') == {'text':'literal ``` in JSON'}
    assert final_json('```json\n[]```') is None
    assert final_json('```json\n{"bad":}```') is None
    assert final_json(json.dumps(value)) is None


# SIMULATED: native child controls below are argv/parser checks only, not live child evidence.
LEGACY_ARGV = {  # exact pre-children argv; children=None must keep it byte-identical
    'codex': ['/x', 'exec', '-m', 'm', '-c', 'model_reasoning_effort="high"', '--dangerously-bypass-approvals-and-sandbox',
              '--json', '--skip-git-repo-check', '-C', '/work', '-'],
    'claude': ['/x', '-p', '--model', 'm', '--effort', 'high', '--output-format', 'stream-json', '--verbose',
               '--max-turns', '9', '--permission-mode', 'bypassPermissions', '--session-id', 'u'],
    'grok': ['/x', '--prompt-file', '/in/prompt.md', '--model', 'm', '--reasoning-effort', 'high', '--output-format',
             'json', '--max-turns', '9', '--cwd', '/work', '--always-approve', '--session-id', 'u'],
}
CHILDREN_OFF = {'codex': ['--disable', 'multi_agent', '--disable', 'multi_agent_v2'],
                'claude': ['--disallowedTools', 'Agent,Task'], 'grok': ['--no-subagents']}


def _children_argv(harness, tmp_path, **kw):
    preset = {} if harness == 'codex' else {'preset_sid': 'u'}
    return adapter_for(harness).build_argv(call(harness, 'm', 'high', tmp_path, **preset, **kw), '/x', tmp_path)


@pytest.mark.parametrize('harness', sorted(LEGACY_ARGV))
def test_children_none_keeps_legacy_argv_and_off_adds_disable(harness, tmp_path):
    from forge.native_children import ChildPolicy
    argv, stdin = _children_argv(harness, tmp_path, observation={'opaque': 1})
    assert argv == LEGACY_ARGV[harness] and stdin == (b'' if harness == 'grok' else b'prompt')
    off, _ = _children_argv(harness, tmp_path, children=ChildPolicy('off', harness, 'm', 'high'))
    flags = CHILDREN_OFF[harness]
    assert [a for a in off if a not in LEGACY_ARGV[harness]] == flags
    assert any(off[i:i + len(flags)] == flags for i in range(len(off)))
    with pytest.raises(ValueError):  # verified on an unverified route fails closed
        _children_argv(harness, tmp_path, children=ChildPolicy('verified', harness, 'm', 'high'))
    with pytest.raises(ValueError):  # policy must pin the exact parent model/effort
        _children_argv(harness, tmp_path, children=ChildPolicy('off', harness, 'm', 'low'))


def test_codex_children_off_on_resume(tmp_path):
    from forge.native_children import ChildPolicy
    argv, _ = _children_argv('codex', tmp_path, resume_sid='S', children=ChildPolicy('off', 'codex', 'm', 'high'))
    assert argv[:3] == ['/x', 'exec', 'resume'] and argv[-2:] == ['S', '-'] and '--disable' in argv


def test_claude_mixed_model_usage_rejected_only_with_child_policy(tmp_path):
    from forge.native_children import ChildPolicy
    adapter = adapter_for('claude')
    mixed = dict(CLAUDE_RESULT, modelUsage={'claude-opus-5-5': {}, 'claude-haiku-5-5': {}})  # SIMULATED
    child = '{"type":"assistant","parent_tool_use_id":"toolu_1","message":{"content":[]}}'  # SIMULATED
    spawn = '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Agent","input":{}}]}}'  # SIMULATED
    events = [adapter.parse_line(child), adapter.parse_line(spawn), adapter.parse_line(json.dumps(mixed))]
    legacy = call('claude', 'claude-opus-5-5', 'high', tmp_path, preset_sid=CLAUDE_RESULT['session_id'])
    assert adapter.finish(legacy, 0, events, b'', '').model_match is True  # direct mode unchanged
    assert 'children' not in adapter.finish(legacy, 0, events, b'', '').usage
    policed = call('claude', 'claude-opus-5-5', 'high', tmp_path, preset_sid=CLAUDE_RESULT['session_id'],
                   children=ChildPolicy('off', 'claude', 'claude-opus-5-5', 'high'))
    native = adapter.finish(policed, 0, events, b'', '')
    assert native.model_match is False and native.actual_model == 'claude-opus-5-5,claude-haiku-5-5'
    assert native.usage['children'] == {'model_usage_keys': ['claude-opus-5-5', 'claude-haiku-5-5'],
                                        'child_events': 1, 'child_spawns': 1}
    exact = adapter.finish(policed, 0, [adapter.parse_line(json.dumps(CLAUDE_RESULT))], b'', '')
    assert exact.model_match is True and exact.usage['children']['child_events'] == 0


def test_codex_actual_model_only_when_observed(tmp_path):
    adapter = adapter_for('codex')
    events = [e for e in map(adapter.parse_line, CODEX_LINES) if e]
    assert adapter.finish(call('codex', 'm', 'high', tmp_path), 0, events, b'', '').actual_model is None
    seen = events[:-1] + [adapter.parse_line('{"type":"turn.completed","usage":{},"model":"other"}')]  # SIMULATED
    native = adapter.finish(call('codex', 'm', 'high', tmp_path), 0, seen, b'', '')
    assert native.actual_model == 'other' and native.model_match is False


def test_codex_native_event_metadata_is_preserved_without_inference(tmp_path):
    from forge.adapters.codex import CodexAdapter
    from forge.adapters.base import Call
    adapter = CodexAdapter()
    call = Call('metadata', 'probe', 'terra', 'codex', 'gpt-6.1-terra', 'low', tmp_path, False, 'challenge')
    observed = {'model': 'gpt-6.1-terra', 'effort': 'low', 'context_window': 400000, 'tools': ['shell']}
    events = [adapter.parse_line(json.dumps({'type': 'turn.completed', 'usage': {}, **observed}))]
    result = adapter.finish(call, 0, events, b'', '')
    assert getattr(result, 'native_metadata', None) == observed
    absent = adapter.finish(call, 0, [
        adapter.parse_line(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message',
            'text': '```json\n' + json.dumps(observed) + '\n```'}})),
        adapter.parse_line('{"type":"turn.completed"}')], b'', '')
    assert absent.native_metadata == {} and absent.actual_model is None
