"""Adaptery agentów CLI: wbudowane claude/codex + generyczny (dowolny CLI).

Rdzeń pętli jest agnostyczny wobec konkretnego narzędzia. claude i codex mają
wbudowaną obsługę (znamy ich flagi i format wyjścia). DOWOLNE inne narzędzie CLI
(grok, Kiro, aider, ...) wpina się bez zmian w kodzie — przez szablon komendy
w zmiennej środowiskowej:

    export FORGE_AGENT_GROK_CMD='grok --model {model} --exec {prompt} --out {output}'
    python3 -m forge.orchestrate --coder-agent grok

Placeholdery szablonu: {prompt} {model} {effort} {project} {output}.
- Jeśli szablon zawiera {output}, wynik czytamy z TEGO pliku; inaczej ze stdout.
- Token, który jest czystym placeholderem i rozwinie się do pustego stringa
  (np. {model} przy nieustawionym modelu), jest pomijany — nie zostawiamy pustych
  argumentów.

Generyczny agent NIE wznawia sesji (nie znamy formatu jego wyjścia, więc nie
przechwycimy id sesji). Ciągłość kontekstu per zadanie zapewnia mu dziennik
zadania — orchestrate dokleja go do promptu (patrz _session_call). To ten sam
mechanizm, którego claude/generic używają zamiast resume.
"""
from __future__ import annotations

import os
import shlex
from dataclasses import dataclass

# Agenci z wbudowaną, przetestowaną obsługą (flagi + parsowanie wyjścia).
BUILTIN_AGENTS = ("claude", "codex")
# Tylko codex wznawia sesje (codex exec resume). Reszta jedzie na dzienniku.
RESUMABLE_AGENTS = ("codex",)

_PLACEHOLDERS = ("prompt", "model", "effort", "project", "output")


@dataclass
class GenericSpec:
    """Opis generycznego agenta CLI zbudowany z szablonu komendy."""
    name: str
    template: list[str]        # tokeny argv z placeholderami
    uses_output_file: bool     # True → wynik z pliku {output}; False → ze stdout


def expand_template(template: list[str], subs: dict[str, str]) -> list[str]:
    """Podstaw placeholdery w każdym tokenie; pomiń tokeny puste po rozwinięciu."""
    out: list[str] = []
    for tok in template:
        new = tok
        for key, val in subs.items():
            new = new.replace("{" + key + "}", val)
        if new == "" and tok != "":
            continue  # czysty placeholder rozwinięty do pusta — nie zostawiaj ""
        out.append(new)
    return out


def env_key(name: str) -> str:
    return f"FORGE_AGENT_{name.upper()}_CMD"


def generic_spec(name: str, environ: dict | None = None) -> GenericSpec | None:
    """Zbuduj GenericSpec z FORGE_AGENT_<NAME>_CMD; None gdy brak szablonu."""
    environ = os.environ if environ is None else environ
    template = environ.get(env_key(name), "").strip()
    if not template:
        return None
    try:
        tokens = shlex.split(template)
    except ValueError:
        return None
    if not tokens:
        return None
    return GenericSpec(name=name, template=tokens,
                       uses_output_file=any("{output}" in t for t in tokens))


def generic_bin(spec: GenericSpec) -> str:
    """Nazwa binarki generycznego agenta (pierwszy token bez placeholderów)."""
    argv = expand_template(spec.template, {k: "" for k in _PLACEHOLDERS})
    return argv[0] if argv else spec.name


def is_builtin(name: str) -> bool:
    return name in BUILTIN_AGENTS


def supports_resume(name: str) -> bool:
    return name in RESUMABLE_AGENTS
