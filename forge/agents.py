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


def _append_log(log_path: str, argv: list[str], output: str, code: int) -> None:
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"\n===== {_ts()} rc={code} :: {' '.join(argv)} =====\n")
        f.write(output)


# --- Konkretni agenci -------------------------------------------------------

def run_claude(prompt: str, cfg: Config, project_dir: str, log_path: str) -> str:
    """Claude Code headless. Zwraca końcowy tekst odpowiedzi (pole .result)."""
    a = cfg.claude()
    argv = a.argv + [
        "-p", prompt,
        "--model", a.model,
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
        return obj.get("result", raw) if isinstance(obj, dict) else raw
    except json.JSONDecodeError:
        return raw  # awaryjnie surowy tekst


def run_codex(prompt: str, cfg: Config, project_dir: str, log_path: str) -> str:
    """Codex exec (non-interactive). Zwraca ostatnią wiadomość agenta."""
    a = cfg.codex()
    last_msg = os.path.join(project_dir, cfg.runtime_dir, "codex_last.txt")
    os.makedirs(os.path.dirname(last_msg), exist_ok=True)
    argv = a.argv + ["exec", prompt]
    if a.model:  # pusty → Codex użyje modelu z własnego config.toml
        argv += ["-m", a.model]
    argv += [
        "-C", project_dir,
        "-s", cfg.codex_sandbox,
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
