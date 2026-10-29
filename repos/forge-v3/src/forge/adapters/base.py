"""Native adapter contract (§5.1). Adapters build pinned argv and parse real native output.

Parsers only recognise schemas observed in real captured output or documented native
formats; anything else yields no structured success, never an assumed success.
"""

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import TYPE_CHECKING, Literal, Mapping

if TYPE_CHECKING:
    from forge.native_children import ChildPolicy

FENCED_JSON = re.compile(r'```(?:json)?[ \t]*\r?\n')


@dataclass(frozen=True)
class Call:
    id: str
    role: str
    profile: str
    harness: str
    model: str
    effort: str
    workdir: Path
    writable: bool
    prompt_text: str
    images: tuple[Path, ...] = ()
    resume_sid: str | None = None
    preset_sid: str | None = None
    timeout: float = 600
    max_turns: int = 40
    children: 'ChildPolicy | None' = None  # None => legacy argv, no child controls
    observation: Mapping | None = None     # loop metrics payload, opaque to adapters
    child_capability: Mapping | None = None


@dataclass
class Event:
    kind: Literal['sid', 'message', 'final', 'error', 'other']
    data: dict


@dataclass
class Native:
    """Adapter view of a finished process, consumed by classify.py."""
    status: Literal['success', 'failure', 'absent']
    sid: str | None = None
    final_text: str | None = None
    actual_model: str | None = None
    model_match: bool | None = None   # None: provider did not report a model
    turn_limit: bool = False
    usage: dict = field(default_factory=dict)
    error_fields: dict = field(default_factory=dict)
    native_metadata: dict = field(default_factory=dict)  # observed native event fields only


class Adapter:
    harness = ''

    def build_argv(self, call: Call, exe: str, in_dir: Path) -> tuple[list[str], bytes]:
        """Return in-sandbox argv (exe is the fixed /opt/forge-native alias) and stdin bytes.

        Files written to in_dir are visible read-only at /in.
        """
        raise NotImplementedError

    def parse_line(self, line: str) -> Event | None:
        raise NotImplementedError

    def finish(self, call: Call, rc: int, events: list[Event], stdout: bytes, stderr: str) -> Native:
        raise NotImplementedError

    @staticmethod
    def _pinned(call: Call):
        if not call.model or not call.effort or 'TBD' in (call.model, call.effort):
            raise ValueError('every native call must pin an exact model and effort')


def load_json_line(line: str):
    line = line.strip()
    if not line.startswith('{'):
        return None
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def final_json(text: str | None):
    """Last fenced JSON object block of the final answer, else None."""
    if not text:
        return None
    for opening in reversed(list(FENCED_JSON.finditer(text))):
        body = text[opening.end():].lstrip()
        try:
            value, end = json.JSONDecoder().raw_decode(body)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and body[end:].lstrip().startswith('```'):
            return value
    return None


def image_media_type(path: Path) -> str:
    head = Path(path).read_bytes()[:12]
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if head.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if head[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp'
    raise ValueError(f'unsupported image payload: {path}')
