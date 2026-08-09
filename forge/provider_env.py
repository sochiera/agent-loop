"""Klucze API providerów OpenCode: skąd je wziąć i jak sprawdzić przed startem.

W ``opencode.json`` klucze zapisuje się wzorcem ``{env:NAZWA}``, a OpenCode
podstawia je ze środowiska SWOJEGO PROCESU — czyli tego, które odziedziczył po
Forge. To środowisko bywa niekompletne z powodów całkowicie niezwiązanych z
konfiguracją: shell otwarty zanim klucz trafił do ``~/.bashrc``, launcher
desktopowy, usługa ``systemd --user``, cron. Wtedy OpenCode wysyła żądanie z
pustym nagłówkiem, a dostawca odpowiada ``401 No API-key provided`` dopiero
w środku zadania — po zapłaceniu za kontekst i utracie tury.

Moduł zamyka tę dziurę z dwóch stron:

- ODTWARZA brakujące zmienne z plików ``*.env`` leżących obok ``opencode.json``
  (to te same pliki, które sourceuje shell — Forge przestaje więc zależeć od
  tego, JAK został uruchomiony);
- RAPORTUJE komplet braków, żeby preflight mógł przerwać w zero sekund zamiast
  po starcie pierwszej roli.

Sprawdzamy tylko providerów faktycznie wskazanych przez routing: brak klucza do
dostawcy, do którego ten przebieg i tak nie zadzwoni, nie jest błędem.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# Składnia podstawienia OpenCode. Nazwa zmiennej celowo wąska (POSIX), żeby
# przypadkowy tekst w JSON-ie nie udawał wymaganego klucza.
_ENV_REF = re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}")

# `export NAZWA=wartość`, `NAZWA=wartość`, z opcjonalnym cudzysłowem.
_ASSIGNMENT = re.compile(
    r"""^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$""")


def _refs(value: object) -> set[str]:
    """Nazwy zmiennych, od których zależy dowolnie zagnieżdżony fragment JSON."""
    if isinstance(value, str):
        return set(_ENV_REF.findall(value))
    if isinstance(value, dict):
        return set().union(*(_refs(item) for item in value.values())) \
            if value else set()
    if isinstance(value, list):
        return set().union(*(_refs(item) for item in value)) if value else set()
    return set()


def provider_vars(config: dict) -> dict[str, set[str]]:
    """Provider → zmienne środowiskowe, bez których nie da się go użyć."""
    providers = config.get("provider")
    if not isinstance(providers, dict):
        return {}
    out: dict[str, set[str]] = {}
    for name, definition in providers.items():
        # Skanujemy całą definicję, nie samo `options.apiKey`: przez {env:...}
        # bywa podawany także baseURL czy nagłówek autoryzacyjny.
        needed = _refs(definition)
        if needed:
            out[name] = needed
    return out


def providers_of(models: list[str] | tuple[str, ...]) -> list[str]:
    """Prefiksy „provider/" z listy modeli, w kolejności pierwszego wystąpienia."""
    out: list[str] = []
    for model in models:
        provider, separator, _rest = (model or "").partition("/")
        if separator and provider and provider not in out:
            out.append(provider)
    return out


def config_dir(environ: dict[str, str] | None = None) -> Path:
    """Katalog konfiguracji OpenCode — tam, gdzie szukamy plików ``*.env``."""
    environ = os.environ if environ is None else environ
    explicit = (environ.get("OPENCODE_CONFIG") or "").strip()
    if explicit:
        return Path(explicit).parent
    base = (environ.get("XDG_CONFIG_HOME")
            or str(Path(environ.get("HOME", str(Path.home()))) / ".config"))
    return Path(base) / "opencode"


def env_files(environ: dict[str, str] | None = None) -> list[Path]:
    """Pliki, z których wolno dobrać brakujące klucze.

    ``FORGE_ENV_FILES`` (lista rozdzielona ``os.pathsep``) nadpisuje domyślne
    poszukiwanie; ``none``/``off``/``0`` wyłącza dobieranie całkowicie, gdy
    operator woli wstrzykiwać środowisko wyłącznie z zewnątrz."""
    environ = os.environ if environ is None else environ
    configured = (environ.get("FORGE_ENV_FILES") or "").strip()
    if configured:
        if configured.lower() in {"none", "off", "0"}:
            return []
        return [Path(part).expanduser()
                for part in configured.split(os.pathsep) if part.strip()]
    return sorted(config_dir(environ).glob("*.env"))


def parse_env_file(path: Path) -> dict[str, str]:
    """Przypisania z pliku ``.env``. Plik nieczytelny = brak przypisań."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _ASSIGNMENT.match(line)
        if not match:
            continue
        value = match.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        # Wartość z podstawieniem shella (`$INNA`, `$(cmd)`) nie jest stałą —
        # udawanie, że jest, wstawiłoby do środowiska śmieć zamiast klucza.
        if "$" in value or "`" in value:
            continue
        out[match.group(1)] = value
    return out


def load_missing(
    names: set[str] | frozenset[str],
    environ: dict[str, str] | None = None,
) -> list[str]:
    """Uzupełnij w ``environ`` te ``names``, których tam nie ma. Zwróć dobrane.

    Zmienna już ustawiona ma pierwszeństwo — jawne środowisko procesu zawsze
    wygrywa z plikiem, inaczej Forge cicho ignorowałby ``VAR=... forge ...``."""
    environ = os.environ if environ is None else environ
    wanted = {name for name in names if not (environ.get(name) or "").strip()}
    if not wanted:
        return []
    loaded: list[str] = []
    for path in env_files(environ):
        if not wanted:
            break
        for name, value in parse_env_file(path).items():
            if name in wanted and value:
                environ[name] = value
                loaded.append(name)
                wanted.discard(name)
    return sorted(loaded)


def missing(
    models: list[str] | tuple[str, ...],
    config: dict,
    environ: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Pary (provider, zmienna) wymagane przez ``models``, a nieustawione."""
    environ = os.environ if environ is None else environ
    required = provider_vars(config)
    out: list[tuple[str, str]] = []
    for provider in providers_of(models):
        for name in sorted(required.get(provider, ())):
            if not (environ.get(name) or "").strip():
                out.append((provider, name))
    return out


def resolve(
    models: list[str] | tuple[str, ...],
    config: dict,
    environ: dict[str, str] | None = None,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Dobierz klucze z plików i zwróć ``(dobrane, wciąż brakujące)``."""
    environ = os.environ if environ is None else environ
    required = provider_vars(config)
    needed: set[str] = set()
    for provider in providers_of(models):
        needed |= required.get(provider, set())
    loaded = load_missing(needed, environ)
    return loaded, missing(models, config, environ)
