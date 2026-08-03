"""Adaptery agentów CLI: wbudowane claude/codex + generyczny (dowolny CLI).

Rdzeń pętli jest agnostyczny wobec konkretnego narzędzia. claude i codex mają
wbudowaną obsługę (znamy ich flagi i format wyjścia). DOWOLNE inne narzędzie CLI
(grok, Kiro, aider, ...) wpina się bez zmian w kodzie — przez szablon komendy
w zmiennej środowiskowej:

    export FORGE_AGENT_GROK_CMD='grok --model {model} --exec {prompt} --out {output}'
    python3 -m forge.orchestrate --coder-agent grok

Placeholdery szablonu: {prompt} {prompt_file} {system} {schema} {model} {effort}
{project} {output}. {prompt_file} jest obsługiwany przez Forge dla CLI, które
czytają prompt z pliku, i pozwala uniknąć ujawniania treści promptu w argv.
- Jeśli szablon zawiera {output}, wynik czytamy z TEGO pliku; inaczej ze stdout.
- Token, który jest czystym placeholderem i rozwinie się do pustego stringa
  (np. {model} przy nieustawionym modelu), jest pomijany — nie zostawiamy pustych
  argumentów.

Poza codeksem NIE wznawiamy sesji — i jest to decyzja wydajnościowa, nie brak.
Technicznie dałoby się: claude i grok mają `--resume`, a oba przyjmują też
`--session-id`, więc id sesji można NADAĆ przed startem, nie wyłuskiwać z
wyjścia. Nie robimy tego, bo pomiary na .forge/usage.jsonl (832 tury claude,
lipiec 2026) mówią, że to by podrożyło:
- tura bezsesyjna i tak ma ~92% wejścia z cache — jedno `claude -p` to kilkanaście
  wewnętrznych wywołań API i tylko pierwsze go nie trafia,
- wznowienie nie zmniejsza kontekstu, tylko pozwala mu narastać: u codeksa, przy
  tej samej skuteczności cache, tury wznowione mają 2,8× większe wejście od
  nowych (w pojedynczych fazach nawet 7×).
Ciągłość kontekstu per zadanie zapewnia agentowi bezsesyjnemu dziennik zadania
i prywatny rekord roli — orchestrate dokleja je do promptu (patrz _call_role).

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
# Tylko codex wznawia sesje (codex exec resume). Reszta jedzie na dzienniku —
# świadomie, patrz docstring modułu. To nie jest luka do „naprawienia".
RESUMABLE_AGENTS = ("codex",)

_PLACEHOLDERS = (
    "prompt", "prompt_file", "system", "schema", "model", "effort",
    "project", "output",
)

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
    # xAI Grok Build CLI. Prompt goes through a file rather than argv: Forge
    # prompts contain shell snippets and must not become visible to broad
    # process-name filters such as `pkill -f`.
    # Verified against the installed Grok 0.2.106 `grok --help`:
    # --prompt-file and --always-approve are supported options.
    "grok": "grok --prompt-file {prompt_file} -m {model} --effort {effort} --always-approve",
    # Kiro CLI (AWS): headless mode nie ma dziś flag wyboru modelu ani effortu.
    # Routing Kiro jest więc tylko metadanymi dla własnego szablonu użytkownika;
    # ten domyślny szablon zawsze korzysta z ustawień ~/.kiro/settings/cli.json.
    "kiro": "kiro-cli chat --no-interactive --trust-all-tools {prompt}",
    # OpenCode CLI (opencode.ai/docs/cli) jako most do dowolnego dostawcy
    # OpenAI-compatible skonfigurowanego w ~/.config/opencode/opencode.json —
    # np. NeuralWatt (api.neuralwatt.com/v1): model = "neuralwatt/<id>".
    # --variant obsługuje tylko część modeli (capabilities.reasoning_effort,
    # np. rodzina glm-5.2); dla reszty zostaw effort pusty — {effort} sam
    # zniknie z komendy (patrz reguła pomijania pustych placeholderów wyżej).
    # --dir {project} jest KONIECZNE: `opencode run` nie dziedziczy cwd procesu
    # (subprocess cwd=project to za mało) — bez tej flagi agent operuje na
    # jakimś swoim domyślnym/ostatnio używanym katalogu, nie na projekcie.
    "opencode": "opencode run {prompt} -m {model} --variant {effort} --auto --dir {project}",
}

# Tryb cienki jest potrzebą roli doradczej. Claude ma obsługę wbudowaną
# (zachowuje własne parsowanie JSON i telemetrykę), a znane generyczne CLI
# dostają osobny szablon. Brak wpisu oznacza bezpieczny fallback do normalnego
# wywołania, nie błąd. W Groku główny prompt jest plikiem; krótki system prompt
# i schema pozostają inline, bo te opcje przyjmują wartości tekstowe.
THIN_TEMPLATES: dict[str, str] = {
    "grok": (
        "grok --prompt-file {prompt_file} -m {model} --effort {effort} "
        "--system-prompt-override {system} --tools \"\" --no-subagents "
        "--no-memory --disable-web-search --max-turns 1 --json-schema {schema}"
    ),
    "opencode": (
        "opencode run {prompt} -m {model} --variant {effort} "
        "--agent forge-thin --pure --format json --dir {project}"
    ),
}


@dataclass
class GenericSpec:
    """Opis generycznego agenta CLI zbudowany z szablonu komendy."""
    name: str
    template: list[str]        # tokeny argv z placeholderami
    uses_output_file: bool     # True → wynik z pliku {output}; False → ze stdout
    uses_prompt_file: bool     # True → prompt z pliku {prompt_file}, nie argv


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


def thin_env_key(name: str) -> str:
    return f"FORGE_AGENT_{name.upper()}_THIN_CMD"


def _spec(name: str, template: str) -> GenericSpec | None:
    try:
        tokens = shlex.split(template)
    except ValueError:
        return None
    if not tokens:
        return None
    return GenericSpec(
        name=name,
        template=tokens,
        uses_output_file=any("{output}" in token for token in tokens),
        uses_prompt_file=any("{prompt_file}" in token for token in tokens),
    )


def generic_spec(name: str, environ: dict | None = None) -> GenericSpec | None:
    """Zbuduj GenericSpec z FORGE_AGENT_<NAME>_CMD; brak → domyślny szablon
    znanego CLI (KNOWN_TEMPLATES), jeśli istnieje; inaczej None."""
    environ = os.environ if environ is None else environ
    template = environ.get(env_key(name), "").strip() or KNOWN_TEMPLATES.get(name, "")
    if not template:
        return None
    return _spec(name, template)


def thin_spec(name: str, environ: dict | None = None) -> GenericSpec | None:
    """Szablon trybu cienkiego lub ``None``, gdy adapter go nie wspiera."""
    environ = os.environ if environ is None else environ
    template = (
        environ.get(thin_env_key(name), "").strip()
        or THIN_TEMPLATES.get(name, "")
    )
    return _spec(name, template) if template else None


def generic_bin(spec: GenericSpec) -> str:
    """Nazwa binarki generycznego agenta (pierwszy token bez placeholderów)."""
    argv = expand_template(spec.template, {k: "" for k in _PLACEHOLDERS})
    return argv[0] if argv else spec.name


def is_builtin(name: str) -> bool:
    return canonical_agent(name) in BUILTIN_AGENTS


def supports_resume(name: str) -> bool:
    return canonical_agent(name) in RESUMABLE_AGENTS
