"""Raport zużycia tokenów z .forge/usage.jsonl.

Uruchomienie:
    python3 -m forge.report [katalog-projektu]     # domyślnie: game

Agreguje wiersze zapisane przez agents.log_usage per (agent, faza) i podaje
sumy — to jest narzędzie do rozstrzygania pytań "gdzie idą tokeny" danymi,
nie odczuciem (patrz docs/ANALIZA-TOKENY-I-NOWY-MODEL.md, sekcja 2.1).

Stare wiersze Codexa z ``resumed=true`` (sprzed telemetrycznej migracji) są
pomijane: zawierają skumulowany licznik całej sesji, więc ich zsumowanie
fałszywie wielokrotnie naliczałoby te same tokeny.
"""
from __future__ import annotations

import json
import os
import re
import sys

# Kolejność ma znaczenie: pierwszy pasujący wzorzec wygrywa.
_PHASE_GROUPS: list[tuple[str, str]] = [
    (r"^bootstrap", "bootstrap"),
    (r"^plan", "plan"),
    (r"^c\d+-test", "micro-test"),
    (r"^c\d+-code", "micro-code"),
    (r"^review-fix", "review-fix"),
    (r"^review", "review"),
    (r"^verify", "verify"),
    (r"^implement", "implement (legacy)"),
    (r"^fix", "fix (legacy)"),
]


def normalize_phase(phase: str) -> str:
    """Zwiń nazwy faz per-cykl (c03-test, review-r1) do stabilnych grup."""
    phase = (phase or "").strip().lower()
    for pattern, group in _PHASE_GROUPS:
        if re.match(pattern, phase):
            return group
    return phase or "unknown"


def _tokens(usage: dict) -> tuple[int, int, int]:
    """(wejście, z-cache, wyjście) z wiersza usage — Claude i Codex mają różne klucze."""
    if not isinstance(usage, dict):
        return 0, 0, 0
    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    cached = int(usage.get("cached_input_tokens")            # Codex
               or usage.get("cache_read_input_tokens") or 0)  # Claude
    return inp, cached, out


def load_records(path: str) -> list[dict]:
    records = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    records.append(obj)
    except OSError:
        pass
    return records


def summarize(records: list[dict]) -> dict:
    """Zbierz sumy per (agent, grupa-fazy): calls / in / cached / out."""
    rows: dict[tuple[str, str], dict] = {}
    for rec in records:
        agent = str(rec.get("agent") or "?")
        if (agent == "codex" and rec.get("resumed") is True
                and "usage_cumulative" not in rec):
            # Legacy: przed poprawką run_codex_session zapisywał pełny licznik
            # sesji jako koszt pojedynczego resume. Pierwsze wywołania (False)
            # pozostają użyteczne, a wznowienia bezpiecznie odrzucamy.
            continue
        group = normalize_phase(str(rec.get("phase") or ""))
        inp, cached, out = _tokens(rec.get("usage") or {})
        row = rows.setdefault((agent, group),
                              {"calls": 0, "in": 0, "cached": 0, "out": 0})
        row["calls"] += 1
        row["in"] += inp
        row["cached"] += cached
        row["out"] += out
    return rows


def format_table(rows: dict) -> str:
    if not rows:
        return "(brak danych — usage.jsonl pusty lub nieobecny)"
    header = f"{'agent':<8} {'faza':<18} {'wywołań':>8} {'wejście':>12} {'z cache':>12} {'wyjście':>10}"
    lines = [header, "-" * len(header)]
    totals = {"calls": 0, "in": 0, "cached": 0, "out": 0}
    for (agent, group) in sorted(rows):
        row = rows[(agent, group)]
        lines.append(f"{agent:<8} {group:<18} {row['calls']:>8} "
                     f"{row['in']:>12,} {row['cached']:>12,} {row['out']:>10,}")
        for key in totals:
            totals[key] += row[key]
    lines.append("-" * len(header))
    lines.append(f"{'RAZEM':<8} {'':<18} {totals['calls']:>8} "
                 f"{totals['in']:>12,} {totals['cached']:>12,} {totals['out']:>10,}")
    return "\n".join(lines)


def usage_summary(project: str, runtime_dir: str = ".forge") -> str:
    return format_table(summarize(load_records(
        os.path.join(project, runtime_dir, "usage.jsonl"))))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    project = args[0] if args else "game"
    print(f"Zużycie tokenów — {os.path.abspath(project)}\n")
    print(usage_summary(project))
    return 0


if __name__ == "__main__":
    sys.exit(main())
