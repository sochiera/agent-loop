"""Claude Code 2.1.x `-p --output-format stream-json --verbose` adapter.

Session id is preset by the controller (`--session-id UUID`) so it is durable before
launch; `system/init.session_id` must confirm it. The terminal `result` event shape
(type, subtype, is_error, result, session_id, usage, modelUsage) is confirmed from real
captured `--output-format json` results; init/assistant event names follow the
documented stream-json format and are re-confirmed by the P5 smoke. Images use
`--input-format stream-json` with base64 image content blocks.
"""

import base64
import json

from forge.adapters.base import Adapter, Call, Event, Native, image_media_type, load_json_line
from forge.native_children import child_controls

# claude --help: `--disallowedTools <tools...>` "list of tool names to deny"; Agent (Task alias) spawns subagents.
CHILD_TOOLS = ('Agent', 'Task')
CHILDREN_OFF = ['--disallowedTools', ','.join(CHILD_TOOLS)]


class ClaudeAdapter(Adapter):
    harness = 'claude'

    def build_argv(self, call: Call, exe: str, in_dir):
        self._pinned(call)
        if bool(call.resume_sid) == bool(call.preset_sid):
            raise ValueError('claude call needs exactly one of preset_sid or resume_sid')
        argv = [exe, '-p', '--model', call.model, '--effort', call.effort, '--output-format', 'stream-json',
                '--verbose', '--max-turns', str(call.max_turns), '--permission-mode', 'bypassPermissions',
                *child_controls(call.children, self.harness, call.model, call.effort, CHILDREN_OFF)]
        argv += ['--resume', call.resume_sid] if call.resume_sid else ['--session-id', call.preset_sid]
        if not call.images:
            return argv, call.prompt_text.encode('utf-8')
        content = [{'type': 'text', 'text': call.prompt_text}]
        for image in call.images:
            content.append({'type': 'image', 'source': {'type': 'base64', 'media_type': image_media_type(image),
                                                        'data': base64.b64encode(image.read_bytes()).decode()}})
        message = {'type': 'user', 'message': {'role': 'user', 'content': content}}
        return argv + ['--input-format', 'stream-json'], (json.dumps(message) + '\n').encode()

    def parse_line(self, line):
        event = load_json_line(line)
        if event is None:
            return None
        kind = event.get('type')
        if kind == 'system' and event.get('subtype') == 'init' and isinstance(event.get('session_id'), str):
            return Event('sid', {'sid': event['session_id'], 'model': event.get('model')})
        if kind == 'result':
            return Event('final', event)
        data = {'type': kind}
        if isinstance(event.get('parent_tool_use_id'), str):  # documented subagent stream marker
            data['parent_tool_use_id'] = event['parent_tool_use_id']
        message = event.get('message')
        content = message.get('content') if isinstance(message, dict) else None
        spawns = sum(isinstance(b, dict) and b.get('type') == 'tool_use' and b.get('name') in CHILD_TOOLS
                     for b in (content if isinstance(content, list) else ()))
        if spawns:
            data['child_spawns'] = spawns
        return Event('other', data)

    def finish(self, call, rc, events, stdout, stderr):
        expected = call.resume_sid or call.preset_sid
        sid = next((e.data['sid'] for e in events if e.kind == 'sid'), None)
        results = [e.data for e in events if e.kind == 'final']
        fields = {'stderr': stderr}
        if not results:
            return Native('absent', sid, error_fields=fields)
        result = results[-1]
        sid = result.get('session_id') or sid
        if sid != expected:
            fields['sid_mismatch'] = {'expected': expected, 'observed': sid}
            return Native('absent', sid, error_fields=fields)
        usage_models = list((result.get('modelUsage') or {}).keys())
        match = None if not usage_models else call.model in usage_models
        usage = result.get('usage') or {}
        if call.children is not None:  # child policy: every reported model must be the exact parent model
            match = None if not usage_models else set(usage_models) == {call.model}
            others = [e.data for e in events if e.kind == 'other']
            usage = dict(usage, children={
                'model_usage_keys': usage_models,
                'child_events': sum('parent_tool_use_id' in d for d in others),
                'child_spawns': sum(d.get('child_spawns', 0) for d in others)})
        actual = call.model if match else (','.join(usage_models) or None)
        ok = result.get('subtype') == 'success' and result.get('is_error') is False and rc == 0
        if not ok:
            fields['native_result'] = {k: result.get(k) for k in ('subtype', 'is_error', 'api_error_status', 'result',
                                                                  'terminal_reason')}
        return Native('success' if ok else 'failure', sid, result.get('result') if ok else None, actual, match,
                      result.get('subtype') == 'error_max_turns', usage, fields)
