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
import subprocess
import time
from pathlib import Path

from . import adapters
from . import ledger
from .config import Config, RATE_LIMIT_PATTERNS

_LIMIT_RE = re.compile("|".join(RATE_LIMIT_PATTERNS), re.IGNORECASE)


class LimitExhausted(RuntimeError):
    """Limity subskrypcji wyczerpane po wszystkich ponowieniach — grzeczny stop."""


class AgentError(RuntimeError):
    """Agent zawiódł z powodu innego niż limit (np. crash, timeout)."""


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
        _prepare_isolated_home(
            target,
            ((home / ".claude" / ".credentials.json", ".credentials.json"),),
        )
        env["CLAUDE_CONFIG_DIR"] = str(target)
    return env


def _prepare_isolated_home(
        target: Path, links: tuple[tuple[Path, str], ...]) -> None:
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        target.chmod(0o700)
    except OSError:
        pass
    for source, name in links:
        destination = target / name
        if not source.exists() or destination.exists() or destination.is_symlink():
            continue
        try:
            destination.symlink_to(source)
        except FileExistsError:
            pass


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


def _balanced_objects(text: str) -> list[str]:
    """Zwróć wszystkie zbalansowane obiekty {...} z tekstu (ignorując nawiasy
    w stringach). Kolejność wystąpienia."""
    out, depth, start, in_str, esc = [], 0, -1, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                out.append(text[start:i + 1])
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


def extract_json(text: str) -> dict | None:
    """Wyłuskaj OSTATNI poprawny blok JSON z odpowiedzi agenta.

    Najpierw preferuje ogrodzenie ```json ...```; awaryjnie skanuje zbalansowane
    obiekty {...} i próbuje od ostatniego (agent zwykle kończy werdyktem)."""
    if not text:
        return None
    fences = re.findall(r"```json\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    candidates = list(fences) or _balanced_objects(text)
    for raw in reversed(candidates):
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


def _run_with_backoff(argv: list[str], cwd: str, cfg: Config, log_path: str,
                      stdin_text: str | None = None,
                      env: dict[str, str] | None = None,
                      ledger_project: str = "") -> str:
    """Uruchom komendę; przy limicie backoff i ponów; zwróć (stdout+stderr)."""
    phase = _phase_from_log(log_path)
    delay = cfg.backoff_start_s
    waited = 0
    last_output = ""
    for attempt in range(cfg.max_limit_retries + 1):
        started = time.monotonic()
        log(f"  agent[{phase}] start: {argv[0]} (próba {attempt + 1})")
        try:
            proc = subprocess.run(
                argv, cwd=cwd, input=stdin_text, text=True,
                capture_output=True, timeout=cfg.agent_timeout_s, env=env,
            )
        except subprocess.TimeoutExpired as e:
            log(f"  agent[{phase}] TIMEOUT po {cfg.agent_timeout_s}s")
            raise AgentError(f"timeout po {cfg.agent_timeout_s}s: {' '.join(argv[:2])}") from e

        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
        last_output = output
        _append_log(log_path, argv, output, proc.returncode)
        _record_large_tool_output(ledger_project or cwd, output)
        elapsed = time.monotonic() - started

        if proc.returncode == 0:
            log(f"  agent[{phase}] koniec: rc=0, {elapsed:.0f}s, wyjście {len(output)} znaków")
            return proc.stdout or output

        # Kod != 0 — limit czy realny błąd?
        if _looks_like_limit(output):
            if attempt >= cfg.max_limit_retries:
                log(f"  agent[{phase}] LIMIT wyczerpany po {attempt} ponowieniach.")
                raise LimitExhausted(
                    f"Limit nadal aktywny po {attempt} ponowieniach — zatrzymuję."
                )
            # Budżet dotyczy SUMY oczekiwań, nie pojedynczego snu: bez niego
            # geometryczny wzrost potrafi ciągnąć martwy bieg wiele dni.
            delay = min(delay, cfg.backoff_total_s - waited)
            if delay <= 0:
                log(f"  agent[{phase}] LIMIT: wyczerpany budżet czekania "
                    f"({cfg.backoff_total_s}s) — zatrzymuję.")
                raise LimitExhausted(
                    f"Limit aktywny po {_format_duration(waited)} oczekiwania "
                    f"(budżet {_format_duration(cfg.backoff_total_s)}) — zatrzymuję."
                )
            wake = _dt.datetime.now() + _dt.timedelta(seconds=delay)
            log(f"  agent[{phase}] LIMIT wykryty. Backoff {delay}s "
                f"(przewidywane wznowienie ~{wake.strftime('%H:%M:%S')}), "
                f"próba {attempt + 1}/{cfg.max_limit_retries}, "
                f"zużyty budżet {_format_duration(waited)}/{_format_duration(cfg.backoff_total_s)}.")
            time.sleep(delay)
            waited += delay
            delay = min(int(delay * cfg.backoff_factor), cfg.backoff_max_s)
            continue

        # Realny błąd — nie zapętlaj.
        log(f"  agent[{phase}] BŁĄD: rc={proc.returncode}, {elapsed:.0f}s")
        raise AgentError(f"agent zwrócił kod {proc.returncode}. Ogon:\n{output[-1500:]}")

    raise LimitExhausted(f"Wyczerpano ponowienia. Ostatnie:\n{last_output[-800:]}")


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
    """Uruchom dowolny agent CLI wg szablonu (adapters.GenericSpec).

    Obce CLI nie mają wspólnego formatu liczników, więc zapisujemy sam fakt
    wywołania — inaczej domyślny mistrz (opencode) nie istniałby w raporcie."""
    out_file = _prepare_last_msg_file(project_dir, cfg) if spec.uses_output_file else None
    subs = {
        "prompt": prompt, "system": system_prompt, "schema": json_schema,
        "model": model or "", "effort": effort or "",
        "project": project_dir, "output": out_file or "",
    }
    argv = adapters.expand_template(spec.template, subs)
    if not argv:
        raise AgentError(f"Pusty szablon komendy dla agenta '{spec.name}'.")
    process_env = (
        _opencode_thin_env(system_prompt)
        if thin and spec.name == "opencode" else None)
    kwargs = {}
    if process_env is not None:
        kwargs["env"] = process_env
    if usage_dir:
        kwargs["ledger_project"] = usage_dir
    stream = _run_with_backoff(
        argv, project_dir, cfg, log_path, **kwargs)
    _log_call_without_tokens(usage_dir or project_dir, cfg, log_path,
                             spec.name, model, effort)
    if out_file:
        return _read_last_msg(out_file)
    if thin and spec.name == "opencode":
        return _extract_opencode_text(stream)
    return stream


# Narzędzia opencode wyłączane w roli doradczej. Schemat agenta oczekuje mapy
# nazwa→bool; samo ``false`` nie jest poprawną wartością pola ``tools``.
_OPENCODE_TOOLS = (
    "bash", "edit", "write", "read", "grep", "glob", "list", "patch",
    "todowrite", "todoread", "webfetch", "task",
)


def _opencode_user_config() -> dict:
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


def _opencode_thin_env(system_prompt: str) -> dict[str, str]:
    config = _opencode_user_config()
    agents = dict(config.get("agent") or {})
    agents["forge-thin"] = {
        "description": "Forge tool-free advisory role",
        "mode": "primary",
        "prompt": system_prompt,
        "tools": {name: False for name in _OPENCODE_TOOLS},
    }
    config["agent"] = agents
    env = os.environ.copy()
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


def run_planner(prompt: str, cfg: Config, project_dir: str, log_path: str,
                *, role: str = "planner") -> str:
    """Uruchom rolę planisty/bootstrapu z jej modelem i effort."""
    agent, model, effort = cfg.role(role)
    return run_agent(agent, prompt, cfg, project_dir, log_path,
                     model=model, effort=effort)
