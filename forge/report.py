"""Raport zużycia tokenów i kosztu z .forge/usage.jsonl oraz .forge/ledger.md.

Uruchomienie:
    python3 -m forge.report [katalog-projektu]     # domyślnie: game

Agreguje wiersze zapisane przez agents.log_usage per (agent, faza), wycenia je
cennikiem z ``forge.pricing`` i podaje trzy liczby na przebieg: ``$/przebieg``,
``$/zadanie`` i ``$/rundę``. To jest narzędzie do rozstrzygania pytań "gdzie
idą tokeny i pieniądze" danymi, nie odczuciem (patrz dokumentacja pipeline'u).

Jednostką jest ZADANIE, nie linia kodu — i porównywalne jest tylko dopóki
rozmiar zadania się nie zmienia. Zmiana rozmiaru zadania psuje ``$/zadanie``
z definicji; punktem odniesienia jest wtedy ``$/przebieg`` przy tej samej
zawartości briefu. ``$/rundę`` jest osobną liczbą właśnie po to, żeby dało się
odróżnić "zadania staniały" od "zadania się skurczyły".

Stare wiersze Codexa z ``resumed=true`` (sprzed telemetrycznej migracji) są
pomijane: zawierają skumulowany licznik całej sesji, więc ich zsumowanie
fałszywie wielokrotnie naliczałoby te same tokeny.
"""
from __future__ import annotations

import json
import os
import re
import sys

from . import ledger
from . import pricing

# Kolejność ma znaczenie: pierwszy pasujący wzorzec wygrywa.
_PHASE_GROUPS: list[tuple[str, str]] = [
    (r"^bootstrap", "bootstrap"),
    # Rola diff-bootstrap (przegląd kierunku) jest usunięta z kodu, ale stare
    # transkrypty i wpisy dziennika sprzed migracji na Product Ownera nadal
    # noszą ten prefiks — bez wpisu ich koszt wpadłby do "unknown" i zepsuł
    # historyczne `$/zadanie`.
    (r"^diff-bootstrap-review", "diff-bootstrap-review"),
    (r"^diff-bootstrap", "diff-bootstrap"),
    (r"^product-owner", "product-owner"),
    (r"^po-review", "po-review"),
    (r"^verify-stories", "verify-stories"),
    (r"^plan", "plan"),
    (r"^tester", "tester"),
    (r"^coder", "coder"),
    (r"^corrections", "corrections"),
    (r"^review", "review"),
    (r"^verify", "verify"),
]

# Providerzy liczący ``input_tokens`` jako CAŁOŚĆ wejścia (z cache włącznie).
_CODEX_SEMANTICS = {"codex", "gpt"}


def normalize_phase(phase: str) -> str:
    """Zwiń nazwy faz per-cykl (c03-test, review-r1) do stabilnych grup."""
    phase = (phase or "").strip().lower()
    for pattern, group in _PHASE_GROUPS:
        if re.match(pattern, phase):
            return group
    return phase or "unknown"


def _tokens(usage: dict, agent: str = "") -> tuple[int, int, int, int]:
    """``(wejście nieocache'owane, zapis cache, odczyt cache, wyjście)``.

    To jest jedyne miejsce w raporcie, gdzie łatwo o błąd wart dziesiątek
    procent rachunku, bo providerzy liczą wejście NIEZGODNIE:

    * Claude — ``input_tokens`` to WYŁĄCZNIE tokeny nieocache'owane, a
      ``cache_creation_input_tokens`` (stawka 1,25×) i
      ``cache_read_input_tokens`` (0,1×) stoją obok. Pominięcie zapisu cache'u
      gubi tu najdroższą pozycję.
    * Codex — ``input_tokens`` to CAŁOŚĆ wejścia, a ``cached_input_tokens``
      jest jego PODZBIOREM. Zsumowanie obu policzyłoby te same tokeny dwa razy.

    Rozpoznajemy providera po nazwie agenta, z awaryjnym rozpoznaniem po
    kluczu: agent generyczny podszywający się pod którąkolwiek telemetrię ma
    zostać policzony wg tego, co faktycznie zapisał.
    """
    if not isinstance(usage, dict):
        return 0, 0, 0, 0
    out = int(usage.get("output_tokens") or 0)
    raw_in = int(usage.get("input_tokens") or 0)
    if ((agent or "").strip().lower() in _CODEX_SEMANTICS
            or "cached_input_tokens" in usage):
        cache_read = int(usage.get("cached_input_tokens") or 0)
        return max(0, raw_in - cache_read), 0, cache_read, out
    return (raw_in,
            int(usage.get("cache_creation_input_tokens") or 0),
            int(usage.get("cache_read_input_tokens") or 0),
            out)


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


def _empty_row() -> dict:
    return {"calls": 0, "in": 0, "cache_write": 0, "cached": 0, "out": 0,
            "usd": 0.0, "priced": 0, "unpriced": 0, "blind": 0}


def summarize(records: list[dict]) -> dict:
    """Sumy per (agent, grupa-fazy): wywołania, czwórka tokenów i koszt.

    ``in`` to wejście NIEOCACHE'OWANE, a ``cache_write`` jest osobno — obie
    pozycje mają różne stawki i zlanie ich w jedną kolumnę było źródłem błędu,
    dla którego ta funkcja powstała.
    """
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
        row = rows.setdefault((agent, group), _empty_row())
        row["calls"] += 1
        model = str(rec.get("model") or "")
        usage = rec.get("usage")
        if not isinstance(usage, dict):
            # CLI nie zwróciło liczników. Brakuje TOKENÓW, nie stawki — to inna
            # awaria niż nieznany model i ma własną linię ostrzeżenia. Wyjątkiem
            # jest model o stawce zerowej (lokalny llama.cpp): jego koszt API
            # wynosi 0 niezależnie od liczby tokenów, więc brak telemetrii nic
            # tam nie ukrywa i `—` byłoby fałszywym alarmem.
            rate = pricing.rates(agent, model)
            if rate is not None and not any(rate):
                row["priced"] += 1
            else:
                row["blind"] += 1
            continue
        counts = _tokens(usage, agent)
        row["in"] += counts[0]
        row["cache_write"] += counts[1]
        row["cached"] += counts[2]
        row["out"] += counts[3]
        usd = pricing.cost_usd(agent, model, counts)
        if usd is None:
            row["unpriced"] += 1
        else:
            row["usd"] += usd
            row["priced"] += 1
    return rows


def unpriced_models(records: list[dict]) -> list[str]:
    """Klucze ``agent/model``, dla których nie znamy stawki — do ostrzeżenia."""
    missing: list[str] = []
    for rec in records:
        if not isinstance(rec.get("usage"), dict):
            continue
        agent = str(rec.get("agent") or "?")
        model = str(rec.get("model") or "")
        if pricing.rates(agent, model) is None:
            key = f"{agent}/{model}" if model else agent
            if key not in missing:
                missing.append(key)
    return sorted(missing)


def _partial(row: dict) -> bool:
    """Czy kwota pomija jakieś wywołania — z braku stawki albo z braku tokenów.

    Obie awarie zaniżają kwotę tak samo, więc obie muszą zapalić gwiazdkę.
    Liczba bez ostrzeżenia jest gorsza niż brak liczby.
    """
    return bool(row["unpriced"] or row["blind"])


def _money(row: dict) -> str:
    """``$`` wiersza: ``—`` gdy nic nie wyceniono, ``*`` gdy tylko część."""
    if not row["priced"]:
        return "—"
    return f"{row['usd']:.2f}" + ("*" if _partial(row) else "")


def format_table(rows: dict) -> str:
    if not rows:
        return "(brak danych — usage.jsonl pusty lub nieobecny)"
    header = (f"{'agent':<8} {'faza':<18} {'wywołań':>8} {'wejście':>12} "
              f"{'zapis cache':>12} {'z cache':>12} {'wyjście':>10} {'$':>9}")
    lines = [header, "-" * len(header)]
    totals = _empty_row()
    for (agent, group) in sorted(rows):
        row = rows[(agent, group)]
        lines.append(f"{agent:<8} {group:<18} {row['calls']:>8} "
                     f"{row['in']:>12,} {row['cache_write']:>12,} "
                     f"{row['cached']:>12,} {row['out']:>10,} "
                     f"{_money(row):>9}")
        for key, value in row.items():
            totals[key] += value
    lines.append("-" * len(header))
    lines.append(f"{'RAZEM':<8} {'':<18} {totals['calls']:>8} "
                 f"{totals['in']:>12,} {totals['cache_write']:>12,} "
                 f"{totals['cached']:>12,} {totals['out']:>10,} "
                 f"{_money(totals):>9}")
    return "\n".join(lines)


def totals(rows: dict) -> dict:
    total = _empty_row()
    for row in rows.values():
        for key, value in row.items():
            total[key] += value
    return total


# --- Mianowniki i metryki kontrolne z dziennika ------------------------------
# Dziennik trzyma ostatnie ``ledger.KEEP_LINES`` wpisów, więc te liczby mogą
# obejmować KRÓTSZY okres niż usage.jsonl. Raport mówi o tym wprost zamiast
# udawać, że mianownik pokrywa cały przebieg.

_PLAN_DECLARED = re.compile(r"plan: zadeklarowano (\d+), przyjęto (\d+)")
_PLAN_CREATED = re.compile(r"plan: utworzono (\d+) zadań")
_PLAN_EMPTY = "plan: planista zgłosił brak dalszych zadań"
_CODER_TURN = re.compile(
    rf"{ledger.TASK_ID_BODY} r\d+ koder→(\w+)")
_STORY_TASK = re.compile(rf"({ledger.TASK_ID_BODY}).*?\((US-\d{{3}})\)")


def story_task_groups(ledger_text: str) -> dict[str, list[str]]:
    """Grupuj taski po historyjkach na potrzeby raportu kosztu i postępu."""
    groups: dict[str, list[str]] = {}
    for line in ledger_text.splitlines():
        match = _STORY_TASK.search(line)
        if match:
            groups.setdefault(match.group(2), [])
            if match.group(1) not in groups[match.group(2)]:
                groups[match.group(2)].append(match.group(1))
    return groups


def plan_batches(ledger_text: str) -> list[tuple[int, int]]:
    """``[(zadeklarowane, przyjęte)]`` per wsad planisty.

    Wpis o odsiewie stoi w dzienniku bezpośrednio PRZED linią wsadu i tę linię
    uszczegóławia, więc konsumujemy go razem z nią — inaczej ten sam wsad
    policzyłby się dwa razy.
    """
    batches: list[tuple[int, int]] = []
    pending: int | None = None
    for line in ledger_text.splitlines():
        body = line.partition("] ")[2] or line
        declared = _PLAN_DECLARED.search(body)
        if declared:
            pending = int(declared.group(1))
            continue
        created = _PLAN_CREATED.search(body)
        if created:
            accepted = int(created.group(1))
            batches.append((pending if pending is not None else accepted,
                            accepted))
            pending = None
            continue
        if body.startswith(_PLAN_EMPTY):
            batches.append((pending or 0, 0))
            pending = None
    if pending is not None:
        batches.append((pending, 0))
    return batches


def coder_pushback(ledger_text: str) -> tuple[int, int]:
    """``(odesłania kodera, wszystkie tury kodera)``.

    Jedyna metryka kontrolna szerszej pierwszej bramki (W5): koder, który musi
    zaspokoić kilka asercji naraz, częściej odsyła sprawę testerowi zamiast
    zwrócić ``green``. Wzrost tego odsetka oznacza „bramka za szeroka".
    """
    turns = 0
    pushback = 0
    for line in ledger_text.splitlines():
        match = _CODER_TURN.match(line.partition("] ")[2] or line)
        if not match:
            continue
        turns += 1
        if match.group(1) in {"test_changes_needed", "tester_input_needed"}:
            pushback += 1
    return pushback, turns


def _round_histogram(rounds: list[int]) -> str:
    buckets: dict[int, int] = {}
    for count in rounds:
        buckets[count] = buckets.get(count, 0) + 1
    return " ".join(f"{rnd}×{tasks}" for rnd, tasks in sorted(buckets.items()))


def _ratio(numerator: float, denominator: float) -> str:
    return f"{numerator / denominator:.2f}" if denominator else "—"


def summary_block(rows: dict, records: list[dict], project: str,
                  runtime_dir: str = ".forge") -> str:
    """Trzy liczby na przebieg plus metryki kontrolne W2 i W5."""
    total = totals(rows)
    completed = ledger.completed_tasks(project, runtime_dir)
    abandoned = ledger.abandoned_tasks(project, runtime_dir)
    ledger_text = ledger.tail(project, ledger.KEEP_LINES, runtime_dir)
    tasks = len(completed)
    rounds = sum(count for _, count in completed)
    spent = total["usd"] if total["priced"] else None
    partial = "*" if _partial(total) else ""

    def per(denominator: int) -> str:
        if spent is None or not denominator:
            return "—"
        return f"{spent / denominator:.2f}{partial}"

    lines = [
        f"zadania: {tasks}"
        + (f" (+{len(abandoned)} porzucone)" if abandoned else "")
        + f"   rundy: {rounds}   rundy/zadanie: {_ratio(rounds, tasks)}",
        f"$/przebieg: {f'{spent:.2f}{partial}' if spent is not None else '—'}"
        f"   $/zadanie: {per(tasks)}   $/rundę: {per(rounds)}",
    ]
    if completed:
        lines.append("rozkład rund (rundy×zadania): "
                     + _round_histogram([count for _, count in completed]))
    groups = story_task_groups(ledger_text)
    if groups:
        lines.append("historyjki: " + "; ".join(
            f"{story}={len(tasks)} zadań" for story, tasks in sorted(groups.items())))
        if spent is not None:
            lines.append(f"$/historyjkę: {spent / len(groups):.2f}{partial}")
    batches = plan_batches(ledger_text)
    if batches:
        declared = sum(item[0] for item in batches)
        accepted = sum(item[1] for item in batches)
        sifted = declared - accepted
        share = f" (odsiew {sifted * 100 // declared}%)" if declared else ""
        lines.append(f"wsady planisty: {len(batches)}, zadeklarowane {declared}, "
                     f"przyjęte {accepted}{share}")
    pushback, coder_turns = coder_pushback(ledger_text)
    if coder_turns:
        lines.append(f"pushback kodera: {pushback}/{coder_turns} "
                     f"({pushback * 100 // coder_turns}%)")
    missing = unpriced_models(records)
    if missing:
        lines.append("UWAGA: brak stawki dla " + ", ".join(missing)
                     + " — te wywołania NIE wchodzą do $ (oznaczone `*`).")
    if total["blind"]:
        lines.append(f"UWAGA: {total['blind']} wywołań bez telemetrii tokenów "
                     "(CLI nie raportuje zużycia) — koszt nieznany.")
    lines.append(f"(mianowniki z dziennika: ostatnie {ledger.KEEP_LINES} wpisów, "
                 "usage.jsonl obejmuje całą historię)")
    return "\n".join(lines)


def usage_summary(project: str, runtime_dir: str = ".forge") -> str:
    records = load_records(
        os.path.join(project, runtime_dir, "usage.jsonl"))
    rows = summarize(records)
    return (format_table(rows) + "\n\n"
            + summary_block(rows, records, project, runtime_dir))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    project = args[0] if args else "game"
    print(f"Zużycie tokenów — {os.path.abspath(project)}\n")
    print(usage_summary(project))
    return 0


if __name__ == "__main__":
    sys.exit(main())
