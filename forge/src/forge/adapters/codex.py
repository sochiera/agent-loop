"""Codex CLI 0.159.x `exec --json` adapter.

Observed real schema (codex 0.159.3): first `thread.started{thread_id}`, `turn.started`,
`item.started|item.completed{item{type: agent_message|command_execution|...}}`,
`turn.completed{usage}`; failures arrive as `turn.failed{error}` or `error{message}`.
The final answer is the text of the last completed `agent_message` before
`turn.completed`. Optional identity/runtime fields are retained only when present
on that native event; absent metadata stays absent, never inferred from the reply.
"""

import shutil

from forge.adapters.base import Adapter, Call, Event, Native, load_json_line
from forge.native_children import child_controls

# codex exec [resume] --help: `--disable <FEATURE>` == `-c features.<name>=false`, per invocation.
NATIVE_METADATA_FIELDS = ('model', 'effort', 'context_window', 'tools')
CHILDREN_OFF = ['--disable', 'multi_agent', '--disable', 'multi_agent_v2']


class CodexAdapter(Adapter):
    harness = 'codex'

    def build_argv(self, call: Call, exe: str, in_dir):
        self._pinned(call)
        images = []
        for i, image in enumerate(call.images):
            name = f'image-{i}{image.suffix.lower()}'
            shutil.copyfile(image, in_dir / name)
            images += ['--image', f'/in/{name}']
        pinned = ['-m', call.model, '-c', f'model_reasoning_effort="{call.effort}"',
                  '--dangerously-bypass-approvals-and-sandbox', '--json', '--skip-git-repo-check',
                  *child_controls(call.children, self.harness, call.model, call.effort, CHILDREN_OFF)]
        if call.resume_sid:
            argv = [exe, 'exec', 'resume', *pinned, *images, call.resume_sid, '-']
        else:
            argv = [exe, 'exec', *pinned, '-C', '/work', *images, '-']
        return argv, call.prompt_text.encode('utf-8')

    def parse_line(self, line):
        event = load_json_line(line)
        if event is None:
            return None
        kind = event.get('type')
        if kind == 'thread.started' and isinstance(event.get('thread_id'), str):
            return Event('sid', {'sid': event['thread_id']})
        item = event.get('item') if isinstance(event.get('item'), dict) else {}
        if kind == 'item.completed' and item.get('type') == 'error':
            return Event('error', item)
        if kind == 'item.completed' and item.get('type') == 'agent_message' and isinstance(item.get('text'), str):
            return Event('message', {'text': item['text']})
        if kind == 'turn.completed':
            return Event('final', {'usage': event.get('usage') or {},
                                   **{key: event[key] for key in NATIVE_METADATA_FIELDS if key in event}})
        if kind in ('turn.failed', 'error'):
            return Event('error', event)
        return Event('other', {'type': kind})

    def finish(self, call, rc, events, stdout, stderr):
        sid = next((e.data['sid'] for e in events if e.kind == 'sid'), None)
        errors = [e.data for e in events if e.kind == 'error']
        finals = [e for e in events if e.kind == 'final']
        text = None
        for e in events:
            if e.kind == 'message':
                text = e.data['text']
        fields = {'stderr': stderr, 'native_errors': errors}
        if errors or (finals and rc != 0):
            status = 'failure'
        elif finals and rc == 0 and text:
            status = 'success'
        else:
            status = 'absent'
        metadata = {key: finals[-1].data[key] for key in NATIVE_METADATA_FIELDS
                    if finals and key in finals[-1].data}
        model = metadata.get('model')
        model = model if isinstance(model, str) and model else None  # only observed metadata, never inferred
        return Native(status, sid, text, model, None if model is None else model == call.model,
                      False, finals[-1].data.get('usage', {}) if finals else {}, fields, metadata)
