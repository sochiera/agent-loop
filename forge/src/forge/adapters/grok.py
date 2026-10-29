"""Grok CLI 1.0.x headless adapter (`--output-format json`).

Real captured schema (one JSON object, possibly pretty-printed, at exit): `text`,
`stopReason` (`end_turn` on success), `sessionId`, `usage`, `modelUsage`{model key},
`num_turns`. The model key observed for pinned `grok-4.7` is `grok-4.7-build`; exactly
that suffix is accepted as the same model. The plan's `streaming-json` (ACP updates)
format has no real capture yet, so it is not parsed. SID is preset with `--session-id`
(new sessions) and is therefore durable before launch, not late.
"""

import base64
import json

from forge.adapters.base import Adapter, Call, Event, Native, image_media_type
from forge.native_children import child_controls

MAX_PROMPT_JSON = 100 * 1024  # stays below Linux MAX_ARG_STRLEN (128 KiB per argv string)
MODEL_ALIASES = ('', '-build')


class GrokAdapter(Adapter):
    harness = 'grok'

    def build_argv(self, call: Call, exe: str, in_dir):
        self._pinned(call)
        if bool(call.resume_sid) == bool(call.preset_sid):
            raise ValueError('grok call needs exactly one of preset_sid or resume_sid')
        argv = [exe]
        if call.images:
            blocks = [{'type': 'text', 'text': call.prompt_text}]
            for image in call.images:
                blocks.append({'type': 'image', 'mimeType': image_media_type(image),
                               'data': base64.b64encode(image.read_bytes()).decode()})
            payload = json.dumps(blocks)
            if len(payload) > MAX_PROMPT_JSON:
                raise ValueError('grok image payload exceeds the argv limit')
            argv += ['--prompt-json', payload]
        else:
            (in_dir / 'prompt.md').write_text(call.prompt_text, encoding='utf-8')
            argv += ['--prompt-file', '/in/prompt.md']
        argv += ['--model', call.model, '--reasoning-effort', call.effort, '--output-format', 'json',
                 '--max-turns', str(call.max_turns), '--cwd', '/work', '--always-approve',
                 *child_controls(call.children, self.harness, call.model, call.effort, ['--no-subagents'])]
        argv += ['--resume', call.resume_sid] if call.resume_sid else ['--session-id', call.preset_sid]
        return argv, b''

    def parse_line(self, line):
        return None  # single (possibly multi-line) object; parsed in finish()

    def finish(self, call, rc, events, stdout, stderr):
        fields = {'stderr': stderr}
        try:
            result = json.loads(stdout.decode('utf-8', 'replace'))
        except json.JSONDecodeError:
            result = None
        if not isinstance(result, dict) or 'stopReason' not in result:
            return Native('absent', error_fields=fields)
        sid = result.get('sessionId')
        expected = call.resume_sid or call.preset_sid
        if sid != expected:
            fields['sid_mismatch'] = {'expected': expected, 'observed': sid}
            return Native('absent', sid, error_fields=fields)
        keys = list((result.get('modelUsage') or {}).keys())
        match = None if not keys else any(k in (call.model + a for a in MODEL_ALIASES) for k in keys)
        stop = result.get('stopReason')
        ok = rc == 0 and stop == 'end_turn' and isinstance(result.get('text'), str)
        if not ok:
            fields['native_result'] = {'stopReason': stop, 'error': result.get('error')}
        return Native('success' if ok else 'failure', sid, result.get('text') if ok else None,
                      ','.join(keys) or None, match, stop == 'max_turns', result.get('usage') or {}, fields)
