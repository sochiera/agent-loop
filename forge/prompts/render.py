"""Minimalny renderer promptów bez zależności i bez interpretowania JSON-a."""
from __future__ import annotations

import re
from pathlib import Path


_TEMPLATES = Path(__file__).with_name("templates")
_SLOT = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")


def read_template(name: str) -> str:
    """Wczytaj szablon i usuń wyłącznie końcowe puste linie."""
    return (_TEMPLATES / name).read_text(encoding="utf-8").rstrip()


def render(name: str, **values) -> str:
    """Podstaw jawne sloty ``{{NAME}}``; zgłoś brak zamiast wysłać śmieci."""
    template = read_template(name)
    required = set(_SLOT.findall(template))
    missing = sorted(required - values.keys())
    if missing:
        raise ValueError(
            f"brak wartości promptu {name}: {', '.join(missing)}")
    return _SLOT.sub(lambda match: str(values[match.group(1)]), template)
