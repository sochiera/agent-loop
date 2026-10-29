"""Project-local verified native adapters."""

from forge.adapters.base import Adapter, Call, Event, Native, final_json
from forge.adapters.claude import ClaudeAdapter
from forge.adapters.codex import CodexAdapter
from forge.adapters.grok import GrokAdapter

ADAPTERS = {'codex': CodexAdapter, 'claude': ClaudeAdapter, 'grok': GrokAdapter}


def adapter_for(harness: str) -> Adapter:
    return ADAPTERS[harness]()


__all__ = ['Adapter', 'Call', 'Event', 'Native', 'final_json', 'adapter_for', 'ADAPTERS']
