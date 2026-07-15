"""Żywy test dymny integracji z Codex CLI — do uruchomienia, gdy masz dostęp.

Weryfikuje na PRODUKCYJNEJ ścieżce kodu (agents.run_codex_session) wszystko,
czego nie da się sprawdzić offline:
  1. binarki i wsparcie `codex exec resume` w Twojej wersji CLI,
  2. przechwycenie id wątku ze strumienia `codex exec --json`,
  3. ciągłość kontekstu po `codex exec resume <id>` (test PONG),
  4. zapis pomiaru zużycia do .forge/usage.jsonl.

Uruchomienie (koszt pełnego testu: dwa najkrótsze wywołania Codeksa):
    python3 -m forge.smoke --dry    # tylko binarki/wersje, zero tokenów
    python3 -m forge.smoke          # pełny test w katalogu tymczasowym

Kod wyjścia 0 = wszystko OK. Przy FAIL kroku 2: wklej wypisane pierwsze linie
JSONL do issue/agentowi — parser (agents.extract_session_id) trzeba dopasować.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

from .agents import run_codex_session
from .config import Config


def _result(name: str, ok: bool, detail: str = "") -> bool:
    mark = "✓ PASS" if ok else "✗ FAIL"
    print(f"  {mark}  {name}" + (f" — {detail}" if detail else ""))
    return ok


def _run(argv: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(argv, text=True, capture_output=True, timeout=timeout)


def check_binaries(cfg: Config) -> bool:
    ok = True
    ok &= _result("git na PATH", shutil.which("git") is not None)
    codex = shutil.which(cfg.codex_bin)
    ok &= _result(f"codex na PATH ({cfg.codex_bin})", codex is not None)
    if codex:
        ver = _run([cfg.codex_bin, "--version"])
        ok &= _result("codex --version", ver.returncode == 0,
                      (ver.stdout or ver.stderr).strip()[:60])
        resume = _run([cfg.codex_bin, "exec", "resume", "--help"])
        ok &= _result("wsparcie `codex exec resume`", resume.returncode == 0,
                      "" if resume.returncode == 0 else
                      "brak — nowy model wymaga nowszej wersji Codex CLI")
    claude = shutil.which(cfg.claude_bin)
    _result(f"claude na PATH ({cfg.claude_bin})", claude is not None,
            "" if claude else "opcjonalne do smoke; wymagane dla planisty-Claude'a")
    return bool(ok)


def _first_jsonl_lines(project: str, cfg: Config, n: int = 8) -> str:
    """Pierwsze linie surowego strumienia z loga — do diagnozy parsera."""
    logs = os.path.join(project, cfg.runtime_dir, "logs")
    try:
        names = sorted(os.listdir(logs))
        with open(os.path.join(logs, names[0]), "r", encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip().startswith("{")]
        return "\n".join(lines[:n])
    except (OSError, IndexError):
        return "(brak loga)"


def live_test(cfg: Config) -> bool:
    project = tempfile.mkdtemp(prefix="forge-smoke-")
    print(f"\nKatalog testowy: {project}")
    try:
        subprocess.run(["git", "init", "-q"], cwd=project, check=False)
        logf = os.path.join(project, cfg.runtime_dir, "logs", "smoke-s1.log")

        # Krok 1: nowa sesja + przechwycenie id wątku.
        out1, sid = run_codex_session(
            "Reply with exactly one word: PONG. Do not run any commands.",
            cfg, project, logf)
        ok = _result("odpowiedź pierwszej sesji zawiera PONG", "PONG" in (out1 or "").upper(),
                     repr((out1 or "").strip()[:60]))
        got_sid = _result("id wątku przechwycone ze strumienia --json", bool(sid),
                          sid or "parser nie znalazł thread_id/session_id")
        ok &= got_sid
        if not got_sid:
            print("\nPierwsze linie JSONL do diagnozy parsera:")
            print(_first_jsonl_lines(project, cfg))
            return False

        # Krok 2: wznowienie sesji — ciągłość kontekstu.
        out2, _ = run_codex_session(
            "Repeat the exact single word I asked you to reply with previously. "
            "Reply with only that word.",
            cfg, project,
            os.path.join(project, cfg.runtime_dir, "logs", "smoke-s2.log"),
            session_id=sid)
        ok &= _result("resume utrzymał kontekst (pamięta PONG)",
                      "PONG" in (out2 or "").upper(), repr((out2 or "").strip()[:60]))

        # Krok 3: pomiar zużycia.
        usage_path = os.path.join(project, cfg.runtime_dir, "usage.jsonl")
        rows = []
        try:
            with open(usage_path, "r", encoding="utf-8") as f:
                rows = [json.loads(ln) for ln in f if ln.strip()]
        except OSError:
            pass
        ok &= _result("usage.jsonl zapisany z tokenami", bool(rows) and any(
            (r.get("usage") or {}).get("input_tokens") for r in rows),
            f"{len(rows)} wierszy")
        return bool(ok)
    finally:
        shutil.rmtree(project, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Test dymny integracji forge↔Codex CLI.")
    ap.add_argument("--dry", action="store_true",
                    help="Tylko binarki/wersje (zero tokenów).")
    args = ap.parse_args(argv)

    cfg = Config()
    # Smoke nie potrzebuje pełnego dostępu — zapis ograniczony do katalogu temp.
    cfg.codex_sandbox = os.environ.get("FORGE_SMOKE_SANDBOX", "workspace-write")
    cfg.codex_effort = os.environ.get("FORGE_SMOKE_EFFORT", "minimal")
    cfg.agent_timeout_s = int(os.environ.get("FORGE_SMOKE_TIMEOUT", "300"))
    cfg.max_limit_retries = 1

    print("== Krok 0: binarki i wersje ==")
    ok = check_binaries(cfg)
    if args.dry:
        print("\n--dry: pomijam test na żywo.")
        return 0 if ok else 1
    if not ok:
        print("\nPrzerwano: napraw binarki zanim odpalisz test na żywo.")
        return 1

    print("\n== Kroki 1–3: sesja, resume, pomiar (na żywo) ==")
    ok = live_test(cfg)
    print("\n" + ("SMOKE: OK — nowy model gotowy do pierwszego biegu." if ok
                  else "SMOKE: FAIL — szczegóły wyżej."))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
