"""Wywoływanie agentów CLI (Claude Code i Codex) w trybie headless.

Odpowiada za:
- zbudowanie właściwej komendy argv (bez shella → brak problemów z escapingiem),
- uruchomienie w katalogu projektu z pełną autonomią (bypass promptów),
- wykrycie wyczerpanych limitów subskrypcji i backoff z logowaniem czasów,
- wyłuskanie końcowego bloku ```json z odpowiedzi agenta.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import selectors
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from . import adapters
from . import ledger
from .config import Config, DEFAULT_TASK_DIFFICULTY, RATE_LIMIT_PATTERNS

_LIMIT_RE = re.compile("|".join(RATE_LIMIT_PATTERNS), re.IGNORECASE)


class LimitExhausted(RuntimeError):
    """Limity subskrypcji wyczerpane po wszystkich ponowieniach — grzeczny stop."""


class AgentError(RuntimeError):
    """Agent zawiódł z powodu innego niż limit (np. crash, timeout)."""


# Zmienna czytana przez samo Claude Code. FORGE_* pozwala dać Forge INNY token
# niż ten, którego operator używa we własnej powłoce, bez zmiany drugiej nazwy.
CLAUDE_TOKEN_VAR = "CLAUDE_CODE_OAUTH_TOKEN"
CLAUDE_TOKEN_VARS = ("FORGE_CLAUDE_OAUTH_TOKEN", CLAUDE_TOKEN_VAR)


def claude_oauth_token(environ: dict[str, str] | None = None) -> str:
    """Długożyciowy token OAuth dla Claude Code; pusty = tryb plikowy.

    Token z ``claude setup-token`` nie rotuje, więc dowolnie wiele procesów może
    używać go równolegle. Plik ``~/.claude/.credentials.json`` tego nie znosi:
    refresh token jest jednorazowy, więc proces, który wygra wyścig o
    odświeżenie, unieważnia sesję pozostałym — łącznie z interaktywnym CLI
    operatora."""
    environ = os.environ if environ is None else environ
    for name in CLAUDE_TOKEN_VARS:
        value = (environ.get(name) or "").strip()
        if value:
            return value
    return ""


def claude_credentials_path(environ: dict[str, str] | None = None) -> Path:
    """Plik sesji CLI operatora — źródło dowiązania w trybie plikowym."""
    environ = os.environ if environ is None else environ
    explicit = (environ.get("CLAUDE_CONFIG_DIR") or "").strip()
    home = Path(environ.get("HOME", str(Path.home())))
    return Path(explicit or home / ".claude") / ".credentials.json"


def claude_session_problem(environ: dict[str, str] | None = None) -> str:
    """Powód, dla którego sesja Claude Code nie dożyje pierwszej roli; ``""`` = OK.

    Sam wygasły ``accessToken`` nie jest problemem — CLI odnowi go w locie.
    Rozpoznajemy tylko stany, z których nie ma wyjścia bez udziału operatora, bo
    tylko one zamieniają start przebiegu w stracone godziny: brak pliku,
    wyzerowane tokeny (tak CLI zapisuje NIEUDANE odświeżenie) i martwy refresh
    token."""
    environ = os.environ if environ is None else environ
    if claude_oauth_token(environ):
        return ""
    path = claude_credentials_path(environ)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return f"brak pliku sesji {path}"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return f"nieczytelny plik sesji {path}: {exc}"
    session = data.get("claudeAiOauth") if isinstance(data, dict) else None
    if not isinstance(session, dict):
        return f"plik sesji {path} nie zawiera sesji claude.ai"
    if not str(session.get("accessToken") or "").strip() \
            or not str(session.get("refreshToken") or "").strip():
        return f"sesja w {path} jest wyczyszczona (pusty token)"
    expires = session.get("expiresAt")
    if isinstance(expires, (int, float)) and not isinstance(expires, bool) \
            and expires <= 0:
        return f"sesja w {path} jest wyczyszczona (expiresAt=0)"
    refresh_expiry = session.get("refreshTokenExpiresAt")
    if isinstance(refresh_expiry, (int, float)) \
            and not isinstance(refresh_expiry, bool) \
            and 0 < refresh_expiry <= time.time() * 1000:
        return f"refresh token w {path} wygasł"
    return ""


def _disable_shared_credentials(destination: Path) -> None:
    """Odsuń plik sesji z izolowanego domu, gdy pracujemy na tokenie.

    Zostawiony obok tokenu bywa STARSZY niż sesja operatora: CLI zapisuje
    poświadczenia atomowo, więc każdy zapis podmienia dowiązanie na zwykły plik,
    który dalej już nie widzi odświeżeń. Przenosimy, a nie kasujemy — plik bywa
    jedynym nośnikiem tokenów OAuth serwerów MCP i należy do CLI, nie do Forge."""
    try:
        if not destination.is_symlink() and not destination.exists():
            return
        os.replace(destination, destination.with_name(destination.name + ".disabled"))
    except OSError:
        # Nieudane odsunięcie nie jest powodem do zatrzymania przebiegu: token
        # ze środowiska i tak ma pierwszeństwo przed plikiem.
        pass


def _isolated_agent_env(name: str) -> dict[str, str]:
    """Środowisko CLI bez globalnych plików instrukcji użytkownika."""
    env = os.environ.copy()
    name = adapters.canonical_agent(name)
    home = Path(env.get("HOME", str(Path.home())))
    config_root = Path(env.get(
        "XDG_CONFIG_HOME", str(home / ".config"))) / "forge"
    if name == "codex":
        target = config_root / "codex"
        _prepare_isolated_home(
            target,
            ((home / ".codex" / "auth.json", "auth.json"),
             (home / ".codex" / "config.toml", "config.toml")),
        )
        env["CODEX_HOME"] = str(target)
    elif name == "claude":
        target = config_root / "claude"
        token = claude_oauth_token(env)
        # W trybie tokenu pliku sesji NIE podpinamy: to jedyny sposób, żeby
        # równoległe instancje Forge i CLI operatora przestały walczyć o ten sam
        # rotujący refresh token.
        _prepare_isolated_home(
            target,
            () if token else
            ((home / ".claude" / ".credentials.json", ".credentials.json"),),
        )
        if token:
            _disable_shared_credentials(target / ".credentials.json")
            env[CLAUDE_TOKEN_VAR] = token
        env["CLAUDE_CONFIG_DIR"] = str(target)
    elif name == "grok":
        target = config_root / "grok"
        _prepare_isolated_home(
            target,
            ((home / ".grok" / "auth.json", "auth.json"),),
        )
        # Grok's Claude-compatibility scanner otherwise loads the user's
        # ~/.claude/CLAUDE.md even when GROK_HOME is isolated. This config is
        # Forge-owned, entirely generated on every invocation, and intentionally
        # contains no personal rules; user edits to this file are overwritten.
        _write_atomic(
            target / "config.toml",
            "[compat.claude]\n"
            "skills = false\n"
            "rules = false\n"
            "agents = false\n"
            "mcps = false\n"
            "hooks = false\n"
            "sessions = false\n",
        )
        env["GROK_HOME"] = str(target)
    return env


def _write_atomic(destination: Path, content: str) -> None:
    """Zapis widoczny dla czytelnika w całości albo wcale.

    Dwa równoległe biegi wołają ``_isolated_agent_env`` przy KAŻDEJ turze na tym
    samym izolowanym domu, więc zwykły ``write_text`` daje drugiemu procesowi
    szansę odczytać plik w połowie zapisu — czyli konfigurację, która nie jest
    już żadną z dwóch prawidłowych wersji."""
    temporary = destination.with_name(
        f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, destination)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _prepare_isolated_home(
        target: Path, links: tuple[tuple[Path, str], ...]) -> None:
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        target.chmod(0o700)
    except OSError:
        pass
    for source, name in links:
        destination = target / name
        if not source.exists():
            continue
        try:
            if destination.is_symlink() and destination.resolve() == source.resolve():
                continue
        except OSError:
            # A dangling or unreadable link must be replaced below.
            pass
        temporary = target / f".{name}.{uuid.uuid4().hex}.tmp"
        try:
            temporary.symlink_to(source)
            # Older Forge releases left a copied credential here.  Replacing it
            # atomically keeps the isolated home pointed at the session that
            # the user's CLI refreshes in its normal configuration directory.
            os.replace(temporary, destination)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise


def _ts() -> str:
    return _dt.datetime.now().strftime("%H:%M:%S")


def log(msg: str) -> None:
    """Log operacyjny na stdout (GUI/konsola czytają go na żywo) — zawsze flush."""
    print(f"[{_ts()}] {msg}", flush=True)


def _format_duration(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    return f"{hours}h{rest // 60:02d}m" if hours else f"{rest // 60}m{rest % 60:02d}s"


def _looks_like_limit(text: str) -> bool:
    return bool(_LIMIT_RE.search(text or ""))


def _is_nested_value(text: str, start: int) -> bool:
    """Czy `{` w `start` jest wartością wewnątrz większej struktury JSON?

    Dowodem jest `,` albo `[` przylegające w TEJ SAMEJ linii (agent pisze
    werdykt zwarcie: `[{`, `},{`) albo `:` domykające klucz — czyli poprzedzone
    cudzysłowem, jak w `"meta":{`. Przełamanie linii zostawiamy prozie, bo
    „Podsumowując,\\n{…}" to zdanie, a nie zagnieżdżenie; z tego samego powodu
    samo `:` nie wystarcza — „Werdykt: {…}" też jest zdaniem."""
    i = start - 1
    while i >= 0 and text[i] in " \t":
        i -= 1
    if i < 0:
        return False
    if text[i] in ",[":
        return True
    return text[i] == ":" and i > 0 and text[i - 1] == '"'


def _scan_json_objects(text: str) -> list[dict]:
    """Znajdź obiekty JSON najwyższego poziomu, próbując dekodować od każdego
    `{`. Odporne na niesparowane cudzysłowy w otaczającej prozie (np. polskie
    „…" domknięte ASCII-`"`), bo nie modeluje stanu string/nie-string poza
    samym parserem JSON — po prostu pyta go o każdą pozycję.

    Nieudany kandydat przesuwa skan do miejsca, w którym parser się wyłożył
    (`e.pos`), a nie o jeden znak. Bez tego urwana partia planisty
    (`{"tasks":[{…},{…},{"id":"task-003","tit`) zwracałaby ostatnie DOMKNIĘTE
    podzadanie zamiast `None`: wywołujący dostawał „poprawny" dict bez pola,
    którego szuka, więc korekta formatu nigdy nie startowała, a bieg umierał
    później na mylącym błędzie."""
    decoder = json.JSONDecoder()
    out: list[dict] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        try:
            obj, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError as exc:
            i = max(exc.pos, i + 1)
            continue
        if not _is_nested_value(text, i):
            out.append(obj)
        i = max(end, i + 1)
    return out


# Format strumienia `codex exec --json` (potwierdzony w dokumentacji, 2026-07):
#   {"type":"thread.started","thread_id":"<uuid>"}
#   {"type":"turn.completed","usage":{"input_tokens":N,"cached_input_tokens":N,"output_tokens":N}}
#   {"type":"turn.failed","error":{"message":"..."}} / {"type":"error","message":"..."}
# Wznowienie: `codex exec resume <THREAD_ID> "<prompt>"`.
# Starsze wersje CLI emitowały session_id — parser rozumie oba warianty.
_SESSION_ID_RE = re.compile(r'"(?:session[_-]?id|thread[_-]?id)"\s*:\s*"([^"]+)"')


def _find_session_id(obj) -> str | None:
    """Zejdź rekurencyjnie po sparsowanym evencie i znajdź pierwsze id wątku/sesji."""
    if isinstance(obj, dict):
        for key in ("thread_id", "threadId", "session_id", "sessionId"):
            val = obj.get(key)
            if isinstance(val, str) and val:
                return val
        for parent in ("thread", "session"):
            sub = obj.get(parent)
            if isinstance(sub, dict):
                val = sub.get("id")
                if isinstance(val, str) and val:
                    return val
        for val in obj.values():
            found = _find_session_id(val)
            if found:
                return found
    elif isinstance(obj, list):
        for val in obj:
            found = _find_session_id(val)
            if found:
                return found
    return None


def extract_session_id(stream: str) -> str | None:
    """Wyłuskaj id wątku/sesji Codeksa ze strumienia zdarzeń `codex exec --json`.

    Preferuje zdarzenie `thread.started` (thread_id); rozumie też starszy wariant
    session_id. Awaryjnie skanuje regexem — odporne na drobne zmiany formatu."""
    for line in (stream or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        found = _find_session_id(obj)
        if found:
            return found
    match = _SESSION_ID_RE.search(stream or "")
    return match.group(1) if match else None


def extract_codex_usage(stream: str) -> dict:
    """Zużycie tokenów z JSONL Codeksa.

    ``turn.completed`` w obecnym Codex CLI zawiera licznik sesji; jego różnicę
    względem poprzedniego wywołania oblicza ``run_codex_session``. Tutaj
    zachowujemy pełny licznik z danego wywołania. Fallback dla starszych
    formatów: generyczny skan znanych kluczy (ostatnia wartość wygrywa)."""
    turn_totals: dict = {}
    fallback: dict = {}
    for line in (stream or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "turn.completed" and isinstance(obj.get("usage"), dict):
            # Ostatni event jest najbardziej kompletnym licznikiem tej sesji.
            # Sumowanie wielu eventów dublowałoby wcześniejsze wartości.
            turn_totals = {
                key: val for key, val in obj["usage"].items()
                if isinstance(val, (int, float))
            }
            continue
        for key in ("input_tokens", "output_tokens", "cached_input_tokens",
                    "total_tokens", "reasoning_output_tokens"):
            val = _find_number(obj, key)
            if val is not None:
                fallback[key] = val  # ostatnia (skumulowana) wartość zdarzenia
    return turn_totals or fallback


def extract_opencode_usage(stream: str) -> dict:
    """Zużycie tokenów z JSONL OpenCode — nowy format, z awaryjnym starym.

    Instalacja z sierpnia 2026 emituje ``step_finish`` z ``part.tokens``, a
    licznik w KAŻDYM kolejnym zdarzeniu tej rodziny jest NARASTAJĄCY dla
    całego wywołania (nie per wiadomość) — ostatnie zdarzenie w strumieniu
    niesie już pełną sumę, więc bierzemy TYLKO jego licznik. Starszy format
    (``message.updated`` / ``info.tokens`` per rola asystenta) jest
    zachowany jako fallback dla instalacji, które go jeszcze zwracają."""
    new = _extract_opencode_usage_step_finish(stream)
    if new:
        return new
    return _extract_opencode_usage_legacy(stream)


def _extract_opencode_usage_step_finish(stream: str) -> dict:
    last_tokens: dict | None = None
    for line in (stream or "").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "step_finish":
            continue
        part = event.get("part")
        tokens = part.get("tokens") if isinstance(part, dict) else None
        if isinstance(tokens, dict):
            last_tokens = tokens  # ostatnie zdarzenie niesie sumę całego wywołania
    if not last_tokens:
        return {}
    cache = last_tokens.get("cache") if isinstance(last_tokens.get("cache"), dict) else {}
    return {
        "input_tokens": int(last_tokens.get("input") or 0),
        "cache_read_input_tokens": int(cache.get("read") or 0),
        "cache_creation_input_tokens": int(cache.get("write") or 0),
        "output_tokens": int(last_tokens.get("output") or 0),
        "reasoning_output_tokens": int(last_tokens.get("reasoning") or 0),
    }


def _extract_opencode_usage_legacy(stream: str) -> dict:
    """Format sprzed sierpnia 2026: ``message.updated`` pojawia się
    wielokrotnie dla tej samej, narastającej wiadomości. Dlatego ostatnia
    wersja każdego id zastępuje poprzednią; ich sumowanie zawyżyłoby
    rachunek o wszystkie stany pośrednie."""
    messages: dict[str, dict] = {}
    for line in (stream or "").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        properties = event.get("properties")
        candidates = (
            event.get("info"), event.get("message"),
            properties.get("info") if isinstance(properties, dict) else None,
        )
        info = next((item for item in candidates if isinstance(item, dict)), None)
        if not info or info.get("role") != "assistant" or not isinstance(info.get("tokens"), dict):
            continue
        message_id = str(info.get("id") or "")
        if not message_id:
            # Bez stabilnego id nie wiemy, czy kolejne zdarzenie jest nową
            # wiadomością, czy narastającą wersją poprzedniej. Pominięcie
            # zachowuje uczciwy fallback zamiast wielokrotnie zawyżać koszt.
            continue
        messages[message_id] = info["tokens"]
    if not messages:
        return {}

    totals = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
    }
    for tokens in messages.values():
        cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
        fields = (
            ("input_tokens", tokens.get("input")),
            ("cached_input_tokens", cache.get("read")),
            ("cache_creation_input_tokens", cache.get("write")),
            ("output_tokens", tokens.get("output")),
            ("reasoning_output_tokens", tokens.get("reasoning")),
        )
        for target, value in fields:
            if isinstance(value, (int, float)):
                totals[target] += int(value)
    return totals


def _grok_session_updates_path(grok_home: str, cwd: str, session_id: str) -> Path:
    escaped_cwd = quote(str(Path(cwd).resolve()), safe="")
    return Path(grok_home) / "sessions" / escaped_cwd / session_id / "updates.jsonl"


def extract_grok_usage(grok_home: str, cwd: str, session_id: str) -> dict:
    """Zużycie tokenów Grok z lokalnego pliku sesji.

    Domyślny szablon Grok trzyma stdout w formacie 'plain' — zmiana na JSON
    zmieniłaby sposób odczytu odpowiedzi dla wszystkich ról. Zamiast tego
    Forge nadaje sesji własny ``--session-id`` i po zakończeniu procesu czyta
    ``updates.jsonl``, który Grok i tak zapisuje lokalnie niezależnie od
    formatu stdout. Zdarzenie ``turn_completed`` niesie licznik NARASTAJĄCY
    dla całej sesji, więc liczy się tylko ostatnie wystąpienie."""
    if not grok_home or not session_id:
        return {}
    try:
        lines = _grok_session_updates_path(
            grok_home, cwd, session_id).read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    usage: dict | None = None
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        update = (event.get("params") or {}).get("update")
        if not isinstance(update, dict) or update.get("sessionUpdate") != "turn_completed":
            continue
        candidate = update.get("usage")
        if isinstance(candidate, dict):
            usage = candidate
    if not usage:
        return {}
    return {
        "input_tokens": int(usage.get("inputTokens") or 0),
        "cached_input_tokens": int(usage.get("cachedReadTokens") or 0),
        "output_tokens": int(usage.get("outputTokens") or 0),
        "reasoning_output_tokens": int(usage.get("reasoningTokens") or 0),
    }


def _find_number(obj, key: str):
    if isinstance(obj, dict):
        if isinstance(obj.get(key), (int, float)):
            return obj[key]
        for val in obj.values():
            found = _find_number(val, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for val in obj:
            found = _find_number(val, key)
            if found is not None:
                return found
    return None


def log_usage(project_dir: str, cfg: Config, record: dict) -> None:
    """Dopisz jeden wiersz pomiaru zużycia do .forge/usage.jsonl (best-effort)."""
    try:
        path = os.path.join(project_dir, cfg.runtime_dir, "usage.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        record = {"ts": _dt.datetime.now().isoformat(timespec="seconds"), **record}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass  # pomiar nigdy nie wywraca pętli


_CODEX_USAGE_KEYS = (
    "input_tokens", "cached_input_tokens", "output_tokens",
    "reasoning_output_tokens", "total_tokens",
)
_codex_usage_baselines: dict[str, dict] = {}


def _last_codex_usage_baseline(project_dir: str, cfg: Config, session_id: str) -> dict | None:
    """Odczytaj ostatni licznik skumulowany sesji, także po restarcie forge.

    Codex CLI 0.144 raportuje w ``turn.completed`` licznik od początku wątku,
    nie przyrost bieżącego ``exec resume``. Wiersz z poprzedniego uruchomienia
    jest więc potrzebny, aby raport nie liczył tej samej historii ponownie.
    """
    if session_id in _codex_usage_baselines:
        return _codex_usage_baselines[session_id]
    path = os.path.join(project_dir, cfg.runtime_dir, "usage.jsonl")
    try:
        with open(path, "r", encoding="utf-8") as f:
            rows = f.readlines()
    except OSError:
        return None
    for line in reversed(rows):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("agent") != "codex" or row.get("session_id") != session_id:
            continue
        cumulative = row.get("usage_cumulative")
        if isinstance(cumulative, dict):
            _codex_usage_baselines[session_id] = cumulative
            return cumulative
    return None


def _usage_delta(cumulative: dict, baseline: dict | None) -> dict | None:
    """Zamień licznik skumulowany Codeksa w przyrost albo oznacz brak bazowej.

    Zmniejszenie licznika oznacza zmianę formatu CLI albo niepasującą bazę;
    wtedy nie zgadujemy i nie zanieczyszczamy raportu.
    """
    if baseline is None:
        return None
    delta: dict = {}
    for key in _CODEX_USAGE_KEYS:
        current = cumulative.get(key)
        previous = baseline.get(key, 0)
        if not isinstance(current, (int, float)):
            continue
        if not isinstance(previous, (int, float)) or current < previous:
            return None
        delta[key] = current - previous
    return delta


@dataclass(frozen=True)
class JsonCandidate:
    """Jeden obiekt, który MOŻE być werdyktem roli, wraz z jego pochodzeniem."""

    data: dict
    repaired: bool = False


@dataclass(frozen=True)
class JsonExtraction:
    """Wynik wydobywania werdyktu wraz z diagnozą dla pojedynczego retry.

    ``candidates`` niesie WSZYSTKICH kandydatów w kolejności preferencji, bo
    sam tekst nie rozstrzyga, który obiekt jest werdyktem. Rola bywa gadatliwa
    PO werdykcie: 2026-08-10 tester dokleił drugi blok ```json``` z samą
    poprawką notatnika, a wybór „ostatni blok wygrywa" skasował 40-minutową,
    poprawną turę. Dopiero walidacja kontraktem roli wie, który to werdykt."""

    data: dict | None = None
    repaired: bool = False
    error: str = ""
    candidates: tuple[JsonCandidate, ...] = ()


def _repair_json_text(raw: str) -> str:
    """Napraw dwa częste, lokalne błędy w wartościach stringowych JSON.

    Nie jest to tolerancyjny parser: niepewne przypadki pozostawia parserowi
    JSON. Dzięki temu nie zwracamy wiarygodnie wyglądającego pół-wyniku. To
    świadoma zmiana dawnej polityki odrzucania nieeskejpowanego cudzysłowu:
    utrata gotowej pracy przez jeden zły werdykt kosztuje więcej niż retry,
    a każde odzyskanie pozostaje widoczne w logu i ledgerze.
    """
    repaired: list[str] = []
    in_string = False
    index = 0
    while index < len(raw):
        char = raw[index]
        if not in_string:
            repaired.append(char)
            if char == '"':
                in_string = True
            index += 1
            continue
        if char == "\\":
            if index + 1 < len(raw):
                escaped = raw[index + 1]
                if escaped in '"\\/bfnrtu':
                    repaired.extend((char, escaped))
                else:
                    repaired.append(escaped)
                index += 2
            else:
                # Samotny backslash nadal jest błędem JSON; nie udajemy, że
                # wiemy, jaką treść agent chciał nim zapisać.
                repaired.append(char)
                index += 1
            continue
        if char == '"':
            lookahead = index + 1
            while lookahead < len(raw) and raw[lookahead].isspace():
                lookahead += 1
            if lookahead == len(raw) or raw[lookahead] in ",}]:":
                repaired.append(char)
                in_string = False
            else:
                repaired.append('\\"')
            index += 1
            continue
        repaired.append(char)
        index += 1
    return "".join(repaired)


def _json_error_detail(raw: str, error: json.JSONDecodeError, *, fenced: bool) -> str:
    context = raw[max(0, error.pos - 60):error.pos + 60].replace("\n", " ")
    source = "blok ```json```" if fenced else "kandydat JSON"
    return (f"{source} nie parsuje: {error.msg} "
            f"(linia {error.lineno}, kolumna {error.colno}); kontekst: …{context}…")


def _unfenced_repair_candidates(text: str) -> list[str]:
    """Zwróć kandydatów najwyższego poziomu, od ostatniego do pierwszego.

    Kandydat z uszkodzonym stringiem nie może być wybierany przez ``rfind('{')``:
    w decyzji planisty ostatni nawias otwiera zwykle zagnieżdżone zadanie, nie
    cały werdykt. Skanujemy starty tą samą strategią co ``_scan_json_objects``
    i zachowujemy tylko obiekty, które nie są wartością większej struktury.
    """
    decoder = json.JSONDecoder()
    starts: list[int] = []
    index, length = 0, len(text)
    while index < length:
        start = text.find("{", index)
        if start < 0:
            break
        if not _is_nested_value(text, start):
            starts.append(start)
        try:
            _obj, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError as exc:
            index = max(exc.pos, start + 1)
        else:
            index = max(end, start + 1)
    end = text.rfind("}")
    if end < 0:
        return []
    return [text[start:end + 1] for start in reversed(starts) if start <= end]


def _add_candidate(candidates: list[JsonCandidate], seen: set[str],
                   obj, *, repaired: bool) -> None:
    """Dołóż kandydata, pomijając powtórzenia (blok ```json``` widzi też skan)."""
    if not isinstance(obj, dict):
        return
    key = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    if key in seen:
        return
    seen.add(key)
    candidates.append(JsonCandidate(data=obj, repaired=repaired))


def _extract_json_detail(text: str) -> JsonExtraction:
    """Wydobądź kandydatów na werdykt i zachowaj diagnozę dekodera.

    Kolejność preferencji jest ta sama, co w poprzedniej wersji zwracającej
    jeden obiekt: ostatni poprawny blok ```json```, potem ostatni obiekt ze
    skanu całego tekstu, na końcu wynik warstwy naprawczej. Nowe jest to, że
    dalsi kandydaci nie znikają — walidator kontraktu roli może sięgnąć głębiej,
    zamiast kasować turę przez przypadkowy obiekt doklejony po werdykcie."""
    if not text:
        return JsonExtraction()
    fences = re.findall(r"```json\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    last_error: tuple[str, json.JSONDecodeError, bool] | None = None
    candidates: list[JsonCandidate] = []
    seen: set[str] = set()
    broken_fences: list[str] = []
    for raw in reversed(fences):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            broken_fences.append(raw)
            if last_error is None:
                last_error = (raw, exc, True)
            continue
        _add_candidate(candidates, seen, obj, repaired=False)

    for obj in reversed(_scan_json_objects(text)):
        _add_candidate(candidates, seen, obj, repaired=False)

    repair_sources = broken_fences
    if not repair_sources and not candidates:
        repair_sources = _unfenced_repair_candidates(text)
        for raw in repair_sources:
            try:
                json.loads(raw)
            except json.JSONDecodeError as exc:
                last_error = (raw, exc, False)
                break
        else:
            repair_sources = []
    for raw in repair_sources:
        repaired_raw = _repair_json_text(raw)
        try:
            obj = json.loads(repaired_raw)
        except json.JSONDecodeError as exc:
            if last_error is None:
                last_error = (repaired_raw, exc, bool(fences))
            continue
        _add_candidate(candidates, seen, obj, repaired=True)

    if candidates:
        first = candidates[0]
        return JsonExtraction(data=first.data, repaired=first.repaired,
                              candidates=tuple(candidates))
    if last_error:
        raw, exc, fenced = last_error
        return JsonExtraction(error=_json_error_detail(raw, exc, fenced=fenced))
    return JsonExtraction()


def extract_json(text: str) -> dict | None:
    """Wyłuskaj OSTATNI poprawny blok JSON z odpowiedzi agenta.

    Najpierw próbuje ogrodzeń ```json ...``` (od ostatniego, agent zwykle
    kończy werdyktem); dopiero gdy żadne nie da poprawnego obiektu, skanuje
    cały tekst — fence, który się nie sparsował, nie może ukryć poprawnego
    obiektu leżącego poza nim."""
    return _extract_json_detail(text).data


# Flagi, po których poznajemy agenta wypisującego POSTĘP na bieżąco: codex
# `exec --json` i opencode `run --format json` emitują zdarzenia linia po linii,
# więc cisza na obu strumieniach naprawdę oznacza bezruch. Claude jedzie na
# `--output-format json` (JEDEN obiekt na końcu) — tam cisza jest normalna przez
# całą turę i watchdog bezczynności musi pozostać wyłączony, inaczej ubijałby
# pracujących agentów. Porównanie jest po CAŁYM tokenie: "--output-format" to
# świadomie NIE to samo co "--format".
_STREAMING_FLAGS = frozenset({"--json", "--format"})


def _idle_timeout_for(argv: list[str], cfg: Config) -> int:
    """Ile sekund ciszy uznajemy za zawis TEGO wywołania (0 = watchdog off)."""
    if not cfg.agent_idle_timeout_s:
        return 0
    if not _STREAMING_FLAGS.intersection(argv):
        return 0
    # Watchdog nigdy nie może wyprzedzić zegara ściennego — inaczej przy ciasnym
    # FORGE_AGENT_TIMEOUT zgłaszałby zawis zamiast zwykłego timeoutu.
    return min(cfg.agent_idle_timeout_s, cfg.agent_timeout_s)


class AgentStalled(AgentError):
    """Proces agenta żyje, ale przestał dawać znaki życia (cisza na wyjściu)."""

    def __init__(self, message: str, output: str = "") -> None:
        super().__init__(message)
        self.output = output


def _timeout_partial(exc: subprocess.TimeoutExpired) -> str:
    """Sklej to, co agent zdążył wypisać przed timeoutem, do zapisu w logu."""
    return (exc.output or "") + "\n" + (exc.stderr or "")


def _kill_process_group(proc: subprocess.Popen) -> None:
    """Ubij CAŁE drzewo procesów agenta, nie tylko bezpośrednie dziecko.

    Agenci CLI odpalają własne podprocesy (powłoka tool-a bash, watchery, LSP).
    Zabicie samego rodzica zostawia je osierocone — i trzymające końce naszych
    pipe'ów, przez co odczyt potrafi nie skończyć się nigdy. Proces startuje z
    ``start_new_session=True``, więc jego pid jest pgid całej grupy.

    Po odebraniu statusu dziecka wychodzi od razu: pid bywa poddany recyklingowi
    przez system, więc killpg na zebranym procesie mógłby trafić w cudzą grupę."""
    if proc.returncode is not None:
        return
    for sig, grace in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            break
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue
    # Grupa zniknęła sama albo nie daje się ubić — i tak odbierz status dziecka,
    # inaczej zostaje zombie na cały bieg pętli.
    try:
        proc.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        pass


def _run_once(argv: list[str], cwd: str, cfg: Config,
              stdin_text: str | None = None,
              env: dict[str, str] | None = None,
              idle_timeout: int = 0) -> tuple[int, str, str]:
    """Uruchom agenta, czytając jego wyjście STRUMIENIOWO; zwróć (rc, out, err).

    Strumieniowo, bo tylko tak da się odróżnić agenta pracującego od śpiącego:
    ``capture_output`` oddaje wszystko dopiero po wyjściu procesu, więc do
    momentu zabicia po godzinie nie mieliśmy o turze ŻADNEJ informacji — ani
    sygnału postępu, ani transkryptu do diagnozy.

    Rzuca AgentStalled po ``idle_timeout`` sekundach bez jednego bajtu (0 =
    watchdog wyłączony) i subprocess.TimeoutExpired po ``cfg.agent_timeout_s``.
    W obu wypadkach ubija grupę procesów i niesie zebrane dotąd wyjście."""
    popen_stdin = subprocess.PIPE if stdin_text is not None else None
    proc = subprocess.Popen(
        argv, cwd=cwd, env=env, stdin=popen_stdin,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True)

    if stdin_text is not None:
        # W wątku: duże wejście nie zmieści się w buforze pipe'u, a my musimy
        # w tym czasie czytać wyjście, żeby nie zakleszczyć się z agentem.
        threading.Thread(target=_feed_stdin, args=(proc, stdin_text),
                         daemon=True).start()

    # Deskryptory zapamiętane z góry: po zamknięciu pipe'ów `proc.stdout.fileno()`
    # już nie odpowie, a zebrane wyjście musi przeżyć nawet ubicie procesu.
    out_fd, err_fd = proc.stdout.fileno(), proc.stderr.fileno()
    chunks: dict[int, list[bytes]] = {out_fd: [], err_fd: []}
    started = time.monotonic()
    last_progress = started
    deadline = started + cfg.agent_timeout_s

    def collected() -> tuple[str, str]:
        return (b"".join(chunks[out_fd]).decode("utf-8", "replace"),
                b"".join(chunks[err_fd]).decode("utf-8", "replace"))

    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    selector.register(proc.stderr, selectors.EVENT_READ)
    try:
        try:
            while selector.get_map():
                now = time.monotonic()
                if now >= deadline:
                    _kill_process_group(proc)
                    out, err = collected()
                    raise subprocess.TimeoutExpired(
                        argv, cfg.agent_timeout_s, output=out, stderr=err)
                idle_left = (last_progress + idle_timeout - now
                             if idle_timeout else float("inf"))
                if idle_left <= 0:
                    _kill_process_group(proc)
                    out, err = collected()
                    raise AgentStalled(
                        f"brak wyjścia przez {idle_timeout}s",
                        output=out + "\n" + err)
                for key, _ in selector.select(min(deadline - now, idle_left)):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:          # EOF na tym strumieniu
                        selector.unregister(key.fileobj)
                        continue
                    chunks[key.fd].append(chunk)
                    last_progress = time.monotonic()
        finally:
            selector.close()
            proc.stdout.close()
            proc.stderr.close()

        # Oba strumienie zamknięte — proces właściwie już wyszedł, ale reszta
        # budżetu czasu należy mu się na sprzątanie.
        try:
            proc.wait(timeout=max(1.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)
            out, err = collected()
            raise subprocess.TimeoutExpired(
                argv, cfg.agent_timeout_s, output=out, stderr=err) from None
    except BaseException:
        # Cokolwiek przerwie odczyt, NIE MOŻE zostawić działającego agenta.
        # Dotyczy to przede wszystkim SIGINT-u ze „Stop" w GUI: agent ma teraz
        # własną sesję, więc sygnał wysłany do grupy Forge'a już go nie dosięga,
        # a stary ``subprocess.run`` ubijał dziecko przy każdym wyjątku. Bez
        # tego po zatrzymaniu biegu w projekcie zostaje agent z
        # ``--dangerously-skip-permissions`` edytujący pliki bez nadzoru.
        # Ubicia z gałęzi wyżej są tu no-opem (dziecko już odebrane).
        _kill_process_group(proc)
        raise
    out, err = collected()
    return proc.returncode, out, err


def _feed_stdin(proc: subprocess.Popen, text: str) -> None:
    """Podaj wejście agentowi i zamknij pipe; zerwany pipe to nie jest błąd."""
    try:
        proc.stdin.write(text.encode("utf-8"))
    except (ValueError, OSError):   # BrokenPipeError to podklasa OSError
        pass
    finally:
        # Zamknięcie musi się wykonać TAKŻE po zerwanym pipie (ubity agent),
        # inaczej deskryptor wisi do najbliższego GC — a tur w biegu są setki.
        try:
            proc.stdin.close()
        except (ValueError, OSError):
            pass


def _run_with_backoff(argv: list[str], cwd: str, cfg: Config, log_path: str,
                      stdin_text: str | None = None,
                      env: dict[str, str] | None = None,
                      ledger_project: str = "") -> str:
    """Uruchom komendę; przy limicie backoff i ponów; zwróć (stdout+stderr)."""
    phase = _phase_from_log(log_path)
    idle_timeout = _idle_timeout_for(argv, cfg)
    delay = cfg.backoff_start_s
    waited = 0
    attempt = 0
    limit_retries = 0
    stalls = 0

    def wait_before_retry(headline: str, retry_no: int, budget_label: str) -> None:
        """Uśpij pętlę przed ponowieniem; wyczerpany budżet → grzeczny stop.

        Budżet dotyczy SUMY oczekiwań, nie pojedynczego snu: bez niego
        geometryczny wzrost potrafi ciągnąć martwy bieg wiele dni. Limit i
        zawis dzielą jeden budżet — obie sytuacje to „dostawca nie odpowiada",
        więc zawis po limicie nie ma prawa resetować odstępu."""
        nonlocal delay, waited
        delay = min(delay, cfg.backoff_total_s - waited)
        if delay <= 0:
            log(f"  agent[{phase}] {headline}: wyczerpany budżet czekania "
                f"({cfg.backoff_total_s}s) — zatrzymuję.")
            raise LimitExhausted(
                f"{headline} nadal po {_format_duration(waited)} oczekiwania "
                f"(budżet {_format_duration(cfg.backoff_total_s)}) — zatrzymuję."
            )
        wake = _dt.datetime.now() + _dt.timedelta(seconds=delay)
        log(f"  agent[{phase}] {headline}. Backoff {delay}s "
            f"(przewidywane wznowienie ~{wake.strftime('%H:%M:%S')}), "
            f"próba {retry_no}/{budget_label}, "
            f"zużyty budżet {_format_duration(waited)}/{_format_duration(cfg.backoff_total_s)}.")
        time.sleep(delay)
        waited += delay
        delay = min(int(delay * cfg.backoff_factor), cfg.backoff_max_s)

    while True:
        attempt += 1
        started = time.monotonic()
        log(f"  agent[{phase}] start: {argv[0]} (próba {attempt})")
        try:
            code, out, err = _run_once(
                argv, cwd, cfg, stdin_text=stdin_text, env=env,
                idle_timeout=idle_timeout)
        except AgentStalled as exc:
            # Proces żyje, ale od idle_timeout nie wydał ani bajtu. Sam się nie
            # podda (patrz komentarz przy Config.agent_idle_timeout_s), więc to
            # MY decydujemy, kiedy przestać płacić za jego drzemkę.
            partial = exc.output
            _append_log(log_path, argv, partial, -1)
            stalls += 1
            log(f"  agent[{phase}] ZAWIS: brak wyjścia przez {idle_timeout}s "
                f"(łącznie {time.monotonic() - started:.0f}s), proces ubity.")
            if stalls > cfg.max_stall_retries:
                raise AgentError(
                    f"agent zawiesił się {stalls} raz(y) pod rząd "
                    f"(brak wyjścia przez {idle_timeout}s). Ogon:\n{partial[-1500:]}"
                ) from exc
            headline = ("ZAWIS (wyjście wygląda na limit dostawcy)"
                        if _looks_like_limit(partial) else "ZAWIS")
            wait_before_retry(headline, stalls, str(cfg.max_stall_retries))
            continue
        except subprocess.TimeoutExpired as e:
            # Zegar ścienny: tura realnie przepracowała cały budżet czasu.
            # Ponowienie kosztowałoby drugie tyle, więc kończymy — ale z
            # transkryptem, bo bez niego nie ma z czego postawić diagnozy.
            _append_log(log_path, argv, _timeout_partial(e), -1)
            log(f"  agent[{phase}] TIMEOUT po {cfg.agent_timeout_s}s")
            raise AgentError(f"timeout po {cfg.agent_timeout_s}s: {' '.join(argv[:2])}") from e

        output = out + "\n" + err
        _append_log(log_path, argv, output, code)
        _record_large_tool_output(ledger_project or cwd, output)
        elapsed = time.monotonic() - started

        if code == 0:
            log(f"  agent[{phase}] koniec: rc=0, {elapsed:.0f}s, wyjście {len(output)} znaków")
            return out or output

        # Kod != 0 — limit czy realny błąd?
        if _looks_like_limit(output):
            if limit_retries >= cfg.max_limit_retries:
                log(f"  agent[{phase}] LIMIT wyczerpany po {limit_retries} ponowieniach.")
                raise LimitExhausted(
                    f"Limit nadal aktywny po {limit_retries} ponowieniach — zatrzymuję."
                )
            limit_retries += 1
            wait_before_retry("LIMIT wykryty", limit_retries,
                              str(cfg.max_limit_retries))
            continue

        # Realny błąd — nie zapętlaj.
        log(f"  agent[{phase}] BŁĄD: rc={code}, {elapsed:.0f}s")
        raise AgentError(f"agent zwrócił kod {code}. Ogon:\n{output[-1500:]}")


def _phase_from_log(log_path: str) -> str:
    """Wyłuskaj nazwę fazy ze ścieżki logu, zdejmując tylko prefiks iteracji:
    'iter-0001-plan.log' → 'plan', 'task-0003-c01-test.log' → 'c01-test'.

    Pełna nazwa fazy musi przetrwać, bo report.normalize_phase grupuje po
    wzorcach typu '^c\\d+-test' i '^review-fix'."""
    base = os.path.basename(log_path or "")
    stem = base[:-4] if base.endswith(".log") else base
    stem = re.sub(r"^(?:iter|task)-\d+-", "", stem)
    return stem or "unknown"


def _append_log(log_path: str, argv: list[str], output: str, code: int) -> None:
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"\n===== {_ts()} rc={code} :: {' '.join(argv)} =====\n")
        f.write(_trim_log_stream(output))


_LOG_OUTPUT_HEAD = 8_000
_LOG_OUTPUT_TAIL = 2_000
_LARGE_TOOL_OUTPUT = 200_000


def _trim_log_stream(stream: str) -> str:
    """Przytnij duże pola JSONL wyłącznie w kopii zapisywanej na dysku."""
    had_final_newline = stream.endswith("\n")
    saved: list[str] = []
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            saved.append(line)
            continue
        if not isinstance(event, (dict, list)):
            saved.append(line)
            continue
        _trim_aggregated_output(event)
        saved.append(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
    result = "\n".join(saved)
    return result + "\n" if had_final_newline else result


def _trim_aggregated_output(value) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "aggregated_output" and isinstance(item, str):
                kept = _LOG_OUTPUT_HEAD + _LOG_OUTPUT_TAIL
                if len(item) > kept:
                    removed = len(item) - kept
                    value[key] = (
                        item[:_LOG_OUTPUT_HEAD]
                        + f"\n…[obcięto {removed} znaków]…\n"
                        + item[-_LOG_OUTPUT_TAIL:]
                    )
            else:
                _trim_aggregated_output(item)
    elif isinstance(value, list):
        for item in value:
            _trim_aggregated_output(item)


def _aggregated_output_chars(stream: str) -> int:
    total = 0
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        total += _sum_aggregated_output(event)
    return total


def _sum_aggregated_output(value) -> int:
    if isinstance(value, dict):
        return sum(
            len(item) if key == "aggregated_output" and isinstance(item, str)
            else _sum_aggregated_output(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return sum(_sum_aggregated_output(item) for item in value)
    return 0


def _record_large_tool_output(project: str, stream: str) -> None:
    size = _aggregated_output_chars(stream)
    if size > _LARGE_TOOL_OUTPUT:
        ledger.append(
            project,
            f"UWAGA: tura wciągnęła {size / 1_000_000:.1f} MB "
            "wyjścia narzędzi",
        )


# --- Konkretni agenci -------------------------------------------------------

def run_claude(prompt: str, cfg: Config, project_dir: str, log_path: str,
               *, model: str | None = None, effort: str | None = None,
               mcp_config: str = "", usage_dir: str = "", thin: bool = False,
               system_prompt: str = "") -> str:
    """Claude Code headless. Zwraca końcowy tekst odpowiedzi (pole .result).

    model/effort None → wartości planisty (zgodność wsteczna). Puste → Claude
    użyje swojego domyślnego modelu; effort domyślnie 'medium'. mcp_config
    (ścieżka pliku konfiguracji MCP) — dziś tylko rola weryfikatora, do
    debugowania CI narzędziami MCP."""
    model = cfg.planner_model if model is None else model
    effort = (cfg.planner_effort if effort is None else effort) or "medium"
    argv = [cfg.claude_bin, "-p", prompt]
    if thin:
        argv += ["--system-prompt", system_prompt, "--tools", ""]
    if model:
        argv += ["--model", model]
    if mcp_config:
        argv += ["--mcp-config", mcp_config]
    argv += [
        "--effort", effort,
        "--output-format", "json",
        "--dangerously-skip-permissions",  # pełna autonomia — edytuje pliki bez pytań
    ]
    kwargs = {"env": _isolated_agent_env("claude")}
    if usage_dir:
        kwargs["ledger_project"] = usage_dir
    raw = _run_with_backoff(
        argv, project_dir, cfg, log_path, **kwargs)
    # --output-format json → obiekt z polem "result".
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and obj.get("is_error"):
            if _looks_like_limit(json.dumps(obj)):
                raise LimitExhausted("Claude zgłosił błąd limitu w JSON.")
            raise AgentError(f"Claude is_error: {obj.get('result') or obj}")
        if isinstance(obj, dict) and isinstance(obj.get("usage"), dict):
            # usage_dir ≠ project_dir dla ról pracujących w sandboxie (mistrz):
            # katalog roboczy znika, telemetria musi zostać przy projekcie.
            log_usage(usage_dir or project_dir, cfg,
                      {"agent": "claude",
                       "phase": _phase_from_log(log_path),
                       "model": model, "effort": effort,
                       "usage": obj["usage"]})
        return obj.get("result", raw) if isinstance(obj, dict) else raw
    except json.JSONDecodeError:
        return raw  # awaryjnie surowy tekst


def _prepare_last_msg_file(project_dir: str, cfg: Config) -> str:
    """Ścieżka pliku -o na ostatnią wiadomość agenta, wyczyszczona przed startem.

    Plik jest współdzielony między wywołaniami (i rolami), więc stara zawartość
    MUSI zniknąć przed uruchomieniem — inaczej run, który nic nie zapisze,
    podsunąłby werdykt poprzedniego agenta jako swój."""
    last_msg = os.path.join(project_dir, cfg.runtime_dir, "codex_last.txt")
    os.makedirs(os.path.dirname(last_msg), exist_ok=True)
    try:
        os.remove(last_msg)
    except OSError:
        pass
    return last_msg


def _codex_agent(cfg: Config, model: str | None, effort: str | None):
    a = cfg.codex()
    if model is not None:
        a.model = model
    if effort is not None:
        a.effort = effort
    return a


def _codex_argv(a, cfg: Config, project_dir: str, last_msg: str, prompt: str,
                *, json_stream: bool = False, resume_id: str | None = None) -> list[str]:
    """JEDNO miejsce prawdy o kontrakcie CLI Codeksa (flagi, sandbox, prompt).

    Rozjazd między wywołaniem jednorazowym a sesyjnym oznaczałby różny posture
    sandboxa dla różnych ról — dlatego różnice ograniczają się do --json
    i podkomendy resume."""
    # `codex exec resume` ma węższy zestaw opcji niż zwykłe `codex exec`.
    # W szczególności Codex CLI 0.144 nie przyjmuje po `resume` flag globalnych
    # takich jak -C/-s ani flagi `exec` --color. Dla wznowienia ustawiamy więc
    # opcje wspólne przed `exec`, a opcje obsługiwane przez resume przed id sesji.
    argv = list(a.argv)
    common: list[str] = []
    if a.model:  # pusty → Codex użyje modelu z własnego config.toml
        common += ["-m", a.model]
    common += ["-c", f'model_reasoning_effort="{a.effort}"']
    if cfg.codex_sandbox == "danger-full-access":
        # Pełny dostęp: pomiń zatwierdzanie i sandbox (dedykowany przełącznik
        # automatyzacji — pewniejszy w headless niż samo -s).
        common += ["--dangerously-bypass-approvals-and-sandbox"]
    else:
        common += ["-s", cfg.codex_sandbox]
        if cfg.codex_sandbox == "workspace-write":
            # Zawężamy ZASIĘG PLIKÓW, nie możliwości agenta: bez tej linii
            # workspace-write odcina też sieć i psuje buildy pobierające
            # zależności. Celem jest brak wyjścia do sąsiednich repozytoriów.
            common += ["-c", "sandbox_workspace_write.network_access=true"]

    if resume_id:
        argv += common + ["-C", project_dir, "exec", "resume"]
    else:
        argv += ["exec"]
    if json_stream:
        argv += ["--json"]
    if not resume_id:
        argv += common + ["-C", project_dir]
    argv += ["--skip-git-repo-check", "-o", last_msg]
    if resume_id:
        argv += [resume_id, prompt]
    else:
        argv += ["--color", "never", prompt]
    return argv


def _read_last_msg(last_msg: str) -> str:
    try:
        with open(last_msg, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _log_call_without_tokens(project_dir: str, cfg: Config, log_path: str,
                             agent: str, model: str, effort: str) -> None:
    """Odnotuj samo wywołanie, gdy CLI nie raportuje zużycia tokenów.

    Bez tego rola wołana często (mistrz na opencode/codex-exec) byłaby w
    raporcie całkowicie niewidoczna — lepiej znać liczbę wywołań niż nic."""
    log_usage(project_dir, cfg, {"agent": agent, "phase": _phase_from_log(log_path),
                                 "model": model, "effort": effort,
                                 "usage_unavailable": True})


def run_codex(prompt: str, cfg: Config, project_dir: str, log_path: str,
              *, model: str | None = None, effort: str | None = None,
              usage_dir: str = "") -> str:
    """Codex exec (non-interactive). Zwraca ostatnią wiadomość agenta.

    Bez ``--json`` strumień nie niesie liczników, więc zapisujemy sam fakt
    wywołania (patrz ``run_codex_session`` dla pełnej telemetrii)."""
    a = _codex_agent(cfg, model, effort)
    last_msg = _prepare_last_msg_file(project_dir, cfg)
    argv = _codex_argv(a, cfg, project_dir, last_msg, prompt)
    kwargs = {"env": _isolated_agent_env("codex")}
    if usage_dir:
        kwargs["ledger_project"] = usage_dir
    _run_with_backoff(argv, project_dir, cfg, log_path, **kwargs)
    _log_call_without_tokens(usage_dir or project_dir, cfg, log_path,
                             "codex", a.model, a.effort)
    return _read_last_msg(last_msg)


def run_codex_session(prompt: str, cfg: Config, project_dir: str, log_path: str,
                      *, session_id: str | None = None, model: str | None = None,
                      effort: str | None = None) -> tuple[str, str | None]:
    """Codex exec w trybie sesyjnym (ciągły kontekst per zadanie).

    Gdy ``session_id`` podany — wznawia sesję (``codex exec resume <id>``);
    inaczej startuje nową i przechwytuje jej id ze strumienia ``--json``.
    Zwraca (ostatnia wiadomość agenta, session_id). Loguje przyrost zużycia
    tokenów, nie licznik skumulowany zwracany przez ``exec resume``."""
    a = _codex_agent(cfg, model, effort)
    last_msg = _prepare_last_msg_file(project_dir, cfg)
    argv = _codex_argv(a, cfg, project_dir, last_msg, prompt,
                       json_stream=True, resume_id=session_id)
    env = _isolated_agent_env("codex")
    try:
        stream = _run_with_backoff(argv, project_dir, cfg, log_path, env=env)
    except AgentError:
        if not session_id:
            raise
        # Wątek może zniknąć: czyszczenie po stronie CLI albo przeniesienie
        # CODEX_HOME (izolacja instrukcji) unieważnia stare id. Utrata historii
        # jest tańsza niż zatrzymanie pętli — prompt roli niesie handoff.
        log(f"  agent[{_phase_from_log(log_path)}] resume nieudany "
            f"(sesja {session_id}) — startuję nową sesję")
        session_id = None
        last_msg = _prepare_last_msg_file(project_dir, cfg)
        argv = _codex_argv(a, cfg, project_dir, last_msg, prompt,
                           json_stream=True, resume_id=None)
        stream = _run_with_backoff(argv, project_dir, cfg, log_path, env=env)
    sid = session_id or extract_session_id(stream)
    cumulative = extract_codex_usage(stream)
    if cumulative and sid:
        baseline = _last_codex_usage_baseline(project_dir, cfg, sid)
        # Nowa sesja ma licznik zaczynający się od zera, więc jej pierwszy
        # raport jest dokładnym przyrostem. Dla starej sesji bez zapisanej bazy
        # nie zapisujemy fałszywie całej historii jako nowej tury.
        usage = cumulative if session_id is None else _usage_delta(cumulative, baseline)
        _codex_usage_baselines[sid] = cumulative
        record = {"agent": "codex", "phase": _phase_from_log(log_path),
                  "model": a.model, "effort": a.effort, "resumed": bool(session_id),
                  "session_id": sid, "usage_cumulative": cumulative}
        if usage is not None:
            record["usage"] = usage
        else:
            record["usage_unavailable"] = True
        log_usage(project_dir, cfg, record)
    return _read_last_msg(last_msg), sid


def _run_generic(spec, prompt: str, cfg: Config, project_dir: str, log_path: str,
                 *, model: str, effort: str, usage_dir: str = "",
                 thin: bool = False, system_prompt: str = "",
                 json_schema: str = "") -> str:
    """Uruchom dowolny agent CLI wg szablonu (adapters.GenericSpec)."""
    out_file = _prepare_last_msg_file(project_dir, cfg) if spec.uses_output_file else None
    prompt_file = None
    try:
        if spec.uses_prompt_file:
            with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", prefix="forge-prompt-",
                    suffix=".md", delete=False) as handle:
                handle.write(prompt)
                prompt_file = handle.name
        # Grok forbids reusing an id, więc świeży UUID zawsze nazywa NOWĄ
        # konwersację (Forge nigdy nie wznawia sesji Groka) — po zakończeniu
        # procesu ten sam id lokalizuje plik sesji z licznikami tokenów.
        grok_session_id = str(uuid.uuid4()) if spec.name == "grok" else ""
        subs = {
            "prompt": prompt, "prompt_file": prompt_file or "",
            "system": system_prompt, "schema": json_schema,
            "model": model or "", "effort": effort or "",
            "project": project_dir, "output": out_file or "",
            "session_id": grok_session_id,
        }
        argv = adapters.expand_template(spec.template, subs)
        if not argv:
            raise AgentError(f"Pusty szablon komendy dla agenta '{spec.name}'.")
        # This is the complete environment mapping, not a delta: subprocess
        # callers must retain the user's PATH and other inherited variables.
        base_env = _isolated_agent_env(spec.name)
        process_env = (
            _opencode_thin_env(system_prompt, base_env)
            if thin and spec.name == "opencode" else base_env)
        kwargs = {"env": process_env}
        if usage_dir:
            kwargs["ledger_project"] = usage_dir
        stream = _run_with_backoff(
            argv, project_dir, cfg, log_path, **kwargs)
    finally:
        if prompt_file:
            try:
                os.unlink(prompt_file)
            except OSError:
                pass
    if spec.name == "opencode":
        usage = extract_opencode_usage(stream)
    elif spec.name == "grok" and grok_session_id:
        usage = extract_grok_usage(
            process_env.get("GROK_HOME", ""), project_dir, grok_session_id)
    else:
        usage = {}
    if usage:
        log_usage(usage_dir or project_dir, cfg, {
            "agent": spec.name, "phase": _phase_from_log(log_path),
            "model": model, "effort": effort, "usage": usage,
        })
    else:
        if spec.name == "opencode" and stream.strip():
            log("  UWAGA: OpenCode zwrócił strumień bez liczników tokenów; "
                "zapisuję usage_unavailable (sprawdź format zdarzeń CLI).")
        elif spec.name == "grok" and grok_session_id and stream.strip():
            log("  UWAGA: Grok nie zapisał liczników tokenów w pliku sesji; "
                "zapisuję usage_unavailable (sprawdź --session-id i GROK_HOME).")
        _log_call_without_tokens(usage_dir or project_dir, cfg, log_path,
                                 spec.name, model, effort)
    if out_file:
        return _read_last_msg(out_file)
    if spec.name == "opencode":
        return _extract_opencode_text(stream)
    return stream


# Narzędzia opencode wyłączane w roli doradczej. Schemat agenta oczekuje mapy
# nazwa→bool; samo ``false`` nie jest poprawną wartością pola ``tools``.
_OPENCODE_TOOLS = (
    "bash", "edit", "write", "read", "grep", "glob", "list", "patch",
    "todowrite", "todoread", "webfetch", "task",
)


def opencode_user_config() -> dict:
    """Konfiguracja użytkownika, którą tryb cienki ROZSZERZA, nie zastępuje.

    ``OPENCODE_CONFIG_CONTENT`` podstawia całą konfigurację, więc wysłanie
    samej definicji agenta skasowałoby blok ``provider`` — a to on mapuje
    ``-m <provider>/<model>`` na realnego dostawcę."""
    candidates = []
    explicit = os.environ.get("OPENCODE_CONFIG", "").strip()
    if explicit:
        candidates.append(explicit)
    config_home = (os.environ.get("XDG_CONFIG_HOME")
                   or os.path.join(os.path.expanduser("~"), ".config"))
    candidates.append(os.path.join(config_home, "opencode", "opencode.json"))
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def _opencode_thin_env(
        system_prompt: str, base_env: dict[str, str] | None = None
        ) -> dict[str, str]:
    config = opencode_user_config()
    agents = dict(config.get("agent") or {})
    agents["forge-thin"] = {
        "description": "Forge tool-free advisory role",
        "mode": "primary",
        "prompt": system_prompt,
        "tools": {name: False for name in _OPENCODE_TOOLS},
    }
    config["agent"] = agents
    env = dict(base_env or os.environ)
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config, ensure_ascii=False)
    return env


def _extract_opencode_text(stream: str) -> str:
    """Złóż tekst odpowiedzi z surowych zdarzeń ``--format json``.

    Strumień emituje TĘ SAMĄ część wielokrotnie, w miarę jak rośnie, więc
    sklejenie wszystkich wystąpień dałoby tekst powtórzony kilkadziesiąt razy.
    Trzymamy ostatnią wersję każdej części w kolejności pierwszego wystąpienia;
    część bez identyfikatora trafia do wspólnego kubełka (wygrywa ostatnia)."""
    parts: dict[str, str] = {}
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        inner = event.get("part")
        part = inner if isinstance(inner, dict) else event
        # Typ bywa na części albo na obejmującym ją zdarzeniu — sprawdzamy oba,
        # żeby nie brać za tekst bloków `reasoning`, `tool` czy `step-start`.
        if (part.get("type") or event.get("type")) != "text":
            continue
        text = part.get("text")
        if not isinstance(text, str):
            continue
        parts[str(part.get("id") or event.get("id") or "")] = text
    return "".join(parts.values()) or stream


def _complete_prompt(system_prompt: str, prompt: str) -> str:
    return f"{system_prompt}\n\n{prompt}" if system_prompt else prompt


def run_agent(name: str, prompt: str, cfg: Config, project_dir: str, log_path: str,
              *, model: str = "", effort: str = "", mcp_config: str = "",
              usage_dir: str = "", thin: bool = False,
              system_prompt: str = "", json_schema: str = "") -> str:
    """Jedno-strzałowe wywołanie dowolnego agenta CLI (any-CLI dyspozytor).

    mcp_config wspiera tylko claude (inni agenci konfigurują MCP po swojemu,
    np. codex w ~/.codex/config.toml) — dla nich jest ignorowany. usage_dir
    kieruje telemetrię poza katalog roboczy (rola pracująca w sandboxie)."""
    name = adapters.canonical_agent(name)
    thin_adapter = adapters.thin_spec(name) if thin else None
    if name == "claude":
        # Natywny tryb cienki (--system-prompt/--tools "") wygrywa z szablonem:
        # ścieżka generyczna gubi wykrywanie limitów, `is_error` i telemetrię
        # zużycia, a claude potrafi zrobić dokładnie to samo bez tych strat.
        return run_claude(prompt, cfg, project_dir, log_path, model=model,
                          effort=effort, mcp_config=mcp_config, usage_dir=usage_dir,
                          thin=thin, system_prompt=system_prompt)
    if name == "codex":
        # Codex nie ma flag trybu cienkiego, więc własny szablon operatora jest
        # tu jedyną drogą do oszczędności — i dlatego ma pierwszeństwo.
        if thin_adapter is not None:
            return _run_generic(
                thin_adapter, prompt, cfg, project_dir, log_path,
                model=model, effort=effort, usage_dir=usage_dir, thin=True,
                system_prompt=system_prompt, json_schema=json_schema)
        return run_codex(_complete_prompt(system_prompt, prompt) if thin else prompt,
                         cfg, project_dir, log_path, model=model,
                         effort=effort, usage_dir=usage_dir)
    spec = thin_adapter or adapters.generic_spec(name)
    if spec is None:
        raise AgentError(
            f"Nieznany agent '{name}'. Wbudowane: claude, codex. Dla innego CLI "
            f"ustaw {adapters.env_key(name)} z szablonem komendy.")
    effective_prompt = (
        prompt if thin_adapter is not None
        else _complete_prompt(system_prompt, prompt) if thin else prompt
    )
    return _run_generic(
        spec, effective_prompt, cfg, project_dir, log_path,
        model=model, effort=effort, usage_dir=usage_dir,
        thin=thin_adapter is not None, system_prompt=system_prompt,
        json_schema=json_schema)


def run_agent_session(name: str, prompt: str, cfg: Config, project_dir: str,
                      log_path: str, *, session_id: str | None = None,
                      model: str = "", effort: str = "") -> tuple[str, str | None]:
    """Wywołanie agenta z ciągłością sesji, gdy ją wspiera.

    codex → sesja z resume (zwraca id). Pozostali agenci są bezsesyjni: jedno
    wywołanie, id=None; ciągłość zapewnia im dziennik zadania (patrz orchestrate)."""
    name = adapters.canonical_agent(name)
    if name == "codex":
        return run_codex_session(prompt, cfg, project_dir, log_path,
                                 session_id=session_id, model=model, effort=effort)
    return run_agent(name, prompt, cfg, project_dir, log_path,
                     model=model, effort=effort), None


def agent_supports_resume(name: str) -> bool:
    return adapters.supports_resume(name)


def describe_endpoint(agent: str, model: str) -> str:
    return f"{agent}/{model}" if model else agent


def with_fallback(chain, role: str, call):
    """Wywołaj kolejne punkty łańcucha, aż któryś zwróci wynik.

    Przełączamy się po WYCZERPANIU limitu (po całym backoffie) i po twardej
    awarii — bo obie znaczą to samo dla biegu: tą drogą pracy nie będzie.
    Ostatni punkt łańcucha rzuca oryginalny wyjątek, więc bieg kończy się dokładnie
    tak, jak bez łańcucha; zmienia się tylko to, ile dróg spróbowano najpierw.
    Wyjątki spoza tej dwójki (np. przerwanie użytkownika) idą wyżej od razu."""
    for index, (agent, model, effort) in enumerate(chain):
        try:
            return call(agent, model, effort)
        except (LimitExhausted, AgentError) as exc:
            remaining = chain[index + 1:]
            if not remaining:
                raise
            reason = "limit" if isinstance(exc, LimitExhausted) else "błąd"
            log(f"  rola[{role}]: {describe_endpoint(agent, model)} — {reason}; "
                f"przełączam na zapas {describe_endpoint(*remaining[0][:2])} "
                f"({index + 1}/{len(chain) - 1}).")
    raise AgentError(f"pusty łańcuch routingu dla roli '{role}'")


def run_role(role: str, prompt: str, cfg: Config, project_dir: str,
             log_path: str, *, difficulty: str = DEFAULT_TASK_DIFFICULTY,
             **kwargs) -> str:
    """Wywołaj rolę jej własnym routingiem, z łańcuchem zapasowym.

    Jedyne miejsce, w którym rola zamienia się w konkretne (agent, model,
    effort) — dzięki temu każdy punkt wywołania dostaje fallback za darmo."""
    chain = cfg.role_chain(role, difficulty)
    return with_fallback(
        chain, role,
        lambda agent, model, effort: run_agent(
            agent, prompt, cfg, project_dir, log_path,
            model=model, effort=effort, **kwargs),
    )


def run_role_session(role: str, prompt: str, cfg: Config, project_dir: str,
                     log_path: str, *, difficulty: str = DEFAULT_TASK_DIFFICULTY,
                     session_id: str | None = None) -> tuple[str, str | None]:
    """Wariant sesyjny ``run_role`` (dla ról z ciągłością u codeksa)."""
    chain = cfg.role_chain(role, difficulty)
    return with_fallback(
        chain, role,
        lambda agent, model, effort: run_agent_session(
            agent, prompt, cfg, project_dir, log_path,
            session_id=session_id, model=model, effort=effort),
    )


def run_planner(prompt: str, cfg: Config, project_dir: str, log_path: str,
                *, role: str = "planner") -> str:
    """Uruchom rolę planisty/bootstrapu z jej modelem i effort."""
    return run_role(role, prompt, cfg, project_dir, log_path)
