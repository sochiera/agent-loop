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

from .config import Config, RATE_LIMIT_PATTERNS

_LIMIT_RE = re.compile("|".join(RATE_LIMIT_PATTERNS), re.IGNORECASE)


class LimitExhausted(RuntimeError):
    """Limity subskrypcji wyczerpane po wszystkich ponowieniach — grzeczny stop."""


class AgentError(RuntimeError):
    """Agent zawiódł z powodu innego niż limit (np. crash, timeout)."""


def _ts() -> str:
    return _dt.datetime.now().strftime("%H:%M:%S")


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

    Ścieżka główna: sumuj pola `usage` ze zdarzeń `turn.completed` (jedno
    wywołanie exec może mieć wiele tur). Fallback dla starszych formatów:
    generyczny skan znanych kluczy (ostatnia wartość wygrywa)."""
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
            for key, val in obj["usage"].items():
                if isinstance(val, (int, float)):
                    turn_totals[key] = turn_totals.get(key, 0) + val
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
                      stdin_text: str | None = None) -> str:
    """Uruchom komendę; przy limicie backoff i ponów; zwróć (stdout+stderr)."""
    delay = cfg.backoff_start_s
    last_output = ""
    for attempt in range(cfg.max_limit_retries + 1):
        try:
            proc = subprocess.run(
                argv, cwd=cwd, input=stdin_text, text=True,
                capture_output=True, timeout=cfg.agent_timeout_s,
            )
        except subprocess.TimeoutExpired as e:
            raise AgentError(f"timeout po {cfg.agent_timeout_s}s: {' '.join(argv[:2])}") from e

        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
        last_output = output
        _append_log(log_path, argv, output, proc.returncode)

        if proc.returncode == 0:
            return proc.stdout or output

        # Kod != 0 — limit czy realny błąd?
        if _looks_like_limit(output):
            if attempt >= cfg.max_limit_retries:
                raise LimitExhausted(
                    f"Limit nadal aktywny po {attempt} ponowieniach — zatrzymuję."
                )
            wake = _dt.datetime.now() + _dt.timedelta(seconds=delay)
            print(f"  [{_ts()}] LIMIT wykryty. Backoff {delay}s "
                  f"(przewidywane wznowienie ~{wake.strftime('%H:%M:%S')}), "
                  f"próba {attempt + 1}/{cfg.max_limit_retries}.")
            time.sleep(delay)
            delay = min(int(delay * cfg.backoff_factor), cfg.backoff_max_s)
            continue

        # Realny błąd — nie zapętlaj.
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
        f.write(output)


# --- Konkretni agenci -------------------------------------------------------

def run_claude(prompt: str, cfg: Config, project_dir: str, log_path: str) -> str:
    """Claude Code headless. Zwraca końcowy tekst odpowiedzi (pole .result)."""
    argv = [cfg.claude_bin, "-p", prompt]
    if cfg.planner_model:
        argv += ["--model", cfg.planner_model]
    argv += [
        "--effort", cfg.planner_effort,
        "--output-format", "json",
        "--dangerously-skip-permissions",  # pełna autonomia — edytuje pliki bez pytań
    ]
    raw = _run_with_backoff(argv, project_dir, cfg, log_path)
    # --output-format json → obiekt z polem "result".
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and obj.get("is_error"):
            if _looks_like_limit(json.dumps(obj)):
                raise LimitExhausted("Claude zgłosił błąd limitu w JSON.")
            raise AgentError(f"Claude is_error: {obj.get('result') or obj}")
        if isinstance(obj, dict) and isinstance(obj.get("usage"), dict):
            log_usage(project_dir, cfg, {"agent": "claude",
                                         "phase": _phase_from_log(log_path),
                                         "model": cfg.planner_model,
                                         "effort": cfg.planner_effort,
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


def run_codex(prompt: str, cfg: Config, project_dir: str, log_path: str,
              *, model: str | None = None, effort: str | None = None) -> str:
    """Codex exec (non-interactive). Zwraca ostatnią wiadomość agenta."""
    a = cfg.codex()
    if model is not None:
        a.model = model
    if effort is not None:
        a.effort = effort
    last_msg = _prepare_last_msg_file(project_dir, cfg)
    argv = a.argv + ["exec", prompt]
    if a.model:  # pusty → Codex użyje modelu z własnego config.toml
        argv += ["-m", a.model]
    argv += ["-c", f'model_reasoning_effort="{a.effort}"']
    if cfg.codex_sandbox == "danger-full-access":
        # Pełny dostęp: pomiń zatwierdzanie i sandbox (dedykowany przełącznik
        # automatyzacji — pewniejszy w headless niż samo -s).
        argv += ["--dangerously-bypass-approvals-and-sandbox"]
    else:
        argv += ["-s", cfg.codex_sandbox]
    argv += [
        "-C", project_dir,
        "--skip-git-repo-check",
        "-o", last_msg,
        "--color", "never",
    ]
    _run_with_backoff(argv, project_dir, cfg, log_path)
    try:
        with open(last_msg, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def run_codex_session(prompt: str, cfg: Config, project_dir: str, log_path: str,
                      *, session_id: str | None = None, model: str | None = None,
                      effort: str | None = None) -> tuple[str, str | None]:
    """Codex exec w trybie sesyjnym (ciągły kontekst per zadanie).

    Gdy ``session_id`` podany — wznawia sesję (``codex exec resume <id>``);
    inaczej startuje nową i przechwytuje jej id ze strumienia ``--json``.
    Zwraca (ostatnia wiadomość agenta, session_id). Loguje zużycie tokenów."""
    a = cfg.codex()
    if model is not None:
        a.model = model
    if effort is not None:
        a.effort = effort
    last_msg = _prepare_last_msg_file(project_dir, cfg)

    argv = a.argv + ["exec"]
    if session_id:
        argv += ["resume", session_id]
    argv += ["--json"]
    if a.model:  # pusty → Codex użyje modelu z własnego config.toml
        argv += ["-m", a.model]
    argv += ["-c", f'model_reasoning_effort="{a.effort}"']
    if cfg.codex_sandbox == "danger-full-access":
        argv += ["--dangerously-bypass-approvals-and-sandbox"]
    else:
        argv += ["-s", cfg.codex_sandbox]
    argv += ["-C", project_dir, "--skip-git-repo-check", "-o", last_msg,
             "--color", "never", prompt]  # prompt jako ostatni pozycyjny

    stream = _run_with_backoff(argv, project_dir, cfg, log_path)
    sid = session_id or extract_session_id(stream)
    usage = extract_codex_usage(stream)
    if usage:
        log_usage(project_dir, cfg, {"agent": "codex", "phase": _phase_from_log(log_path),
                                     "model": a.model, "effort": a.effort,
                                     "resumed": bool(session_id), "usage": usage})
    try:
        with open(last_msg, "r", encoding="utf-8") as f:
            return f.read(), sid
    except OSError:
        return "", sid


def run_planner(prompt: str, cfg: Config, project_dir: str, log_path: str) -> str:
    """Uruchom wybranego planistę z jego niezależnym modelem i effort."""
    if cfg.planner_agent == "claude":
        return run_claude(prompt, cfg, project_dir, log_path)
    if cfg.planner_agent == "codex":
        return run_codex(prompt, cfg, project_dir, log_path,
                         model=cfg.planner_model, effort=cfg.planner_effort)
    raise AgentError(f"Nieznany agent planujący: {cfg.planner_agent}")
