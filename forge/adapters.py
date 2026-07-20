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

KONTRAKT generycznego agenta (nie wykryjemy tego za Ciebie — CLI bywają różne):
- przy PORAŻCE wyjdź kodem != 0 (wtedy orkiestrator zgłosi błąd/backoff);
  agent, który zgłasza błąd „w treści" a wychodzi 0, zostanie uznany za sukces,
- finalną odpowiedź (blok ```json wymagany przez rolę) wypisz na STDOUT albo do
  pliku {output}; komunikaty diagnostyczne kieruj na STDERR,
- nie znamy zużycia tokenów generyka — nie trafia do .forge/usage.jsonl.
"""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass

_TOKEN_RE = re.compile(r"\{(\w+)\}")

# Agenci z wbudowaną, przetestowaną obsługą (flagi + parsowanie wyjścia).
BUILTIN_AGENTS = ("claude", "codex")
# Tylko codex wznawia sesje (codex exec resume). Reszta jedzie na dzienniku.
RESUMABLE_AGENTS = ("codex",)

_PLACEHOLDERS = ("prompt", "model", "effort", "project", "output")

# Aliasy nazw agentów — "gpt"/"chatgpt" to po prostu Codex CLI (agent OpenAI
# napędzany modelami GPT); zamiast osobnej, mniej przetestowanej integracji
# korzystamy z gotowej obsługi Codeksa (sesje, usage, backoff) pod przyjazną
# nazwą roli. Rozwiązywane wszędzie tam, gdzie nazwa agenta wpada do dyspozytora.
AGENT_ALIASES = {"gpt": "codex", "chatgpt": "codex"}


def canonical_agent(name: str) -> str:
    """Rozwiąż alias (np. 'gpt' → 'codex') do kanonicznej nazwy agenta."""
    return AGENT_ALIASES.get(name, name)


# Domyślne szablony komend dla znanych, ale nie-wbudowanych agentów CLI.
# Używane TYLKO gdy FORGE_AGENT_<NAZWA>_CMD nie jest ustawione — wygodny
# punkt startowy zgodny z oficjalną dokumentacją (stan: 2026-07), nie
# gwarancja zgodności z Twoją zainstalowaną wersją CLI. Nadpisz swoim
# szablonem, jeśli flagi się zmieniły albo używasz forka/innej wersji.
KNOWN_TEMPLATES: dict[str, str] = {
    # xAI Grok Build CLI: `grok -p "<prompt>" -m <model> --effort low|medium|high`
    # (docs.x.ai/build/cli/reference). --always-approve — pełna autonomia,
    # spójnie z pozostałymi agentami forge.
    "grok": "grok -p {prompt} -m {model} --effort {effort} --always-approve",
    # Kiro CLI (AWS): headless mode nie ma dziś flagi wyboru modelu — model
    # ustawiasz w ~/.kiro/settings/cli.json (kiro.dev/docs/cli/headless).
    "kiro": "kiro-cli chat --no-interactive --trust-all-tools {prompt}",
}


@dataclass
class GenericSpec:
    """Opis generycznego agenta CLI zbudowany z szablonu komendy."""
    name: str
    template: list[str]        # tokeny argv z placeholderami
    uses_output_file: bool     # True → wynik z pliku {output}; False → ze stdout


def expand_template(template: list[str], subs: dict[str, str]) -> list[str]:
    """Rozwiń szablon argv, podstawiając placeholdery znanych kluczy.

    Zasady:
    - Jeden przebieg (regex): podstawiona wartość NIE jest ponownie skanowana, więc
      prompt zawierający literalnie np. "{model}" nie zostaje uszkodzony.
    - Token będący SAMYM placeholderem znanego klucza o pustej wartości (np.
      "{model}" przy nieustawionym modelu) jest pomijany RAZEM z bezpośrednio
      poprzedzającą go flagą opcji (token zaczynający się od "-"). Dzięki temu
      'cli --model {model}' bez modelu daje 'cli', a nie 'cli --model <następny>'.
    - Nieznane placeholdery zostają bez zmian."""
    out: list[str] = []
    for tok in template:
        pure = _TOKEN_RE.fullmatch(tok)
        if pure and pure.group(1) in subs and subs[pure.group(1)] == "":
            if out and out[-1].startswith("-"):
                out.pop()  # osierocona flaga opcji — usuń parę flaga+placeholder
            continue
        out.append(_TOKEN_RE.sub(
            lambda m: subs[m.group(1)] if m.group(1) in subs else m.group(0), tok))
    return out


def env_key(name: str) -> str:
    return f"FORGE_AGENT_{name.upper()}_CMD"


def generic_spec(name: str, environ: dict | None = None) -> GenericSpec | None:
    """Zbuduj GenericSpec z FORGE_AGENT_<NAME>_CMD; brak → domyślny szablon
    znanego CLI (KNOWN_TEMPLATES), jeśli istnieje; inaczej None."""
    environ = os.environ if environ is None else environ
    template = environ.get(env_key(name), "").strip() or KNOWN_TEMPLATES.get(name, "")
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
    return canonical_agent(name) in BUILTIN_AGENTS


def supports_resume(name: str) -> bool:
    return canonical_agent(name) in RESUMABLE_AGENTS
