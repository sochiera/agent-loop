"""Rejestr problemów weryfikacji celu — czysta logika, bez subprocessów.

Werdykt weryfikatora to nie "pass/fail + esej", tylko parsowalny rejestr
problemów z trwałymi id i statusami. Na tym rejestrze orkiestrator egzekwuje
mechanicznie: kompletność odhaczania między cyklami, ważność zastrzeżeń
behawioralnych (design_gap musi cytować kryterium z DESIGN.md), postęp
(stall-licznik) i bramkę PASS. Patrz docs/PLAN-3-WERYFIKACJA.md, sekcje 4 i 6.
"""
from __future__ import annotations

# Klasy problemów i ich skutki mechaniczne (sekcja 4 planu):
# code_bug      → zadanie naprawcze (wymaga repro_cmd przy zgłoszeniu),
# verify_defect → jedyna klasa odblokowująca edycję chronionych ścieżek,
# env_issue     → świat fizyczny; nigdy nie idzie do planisty (osobny tor: stop),
# flaky         → darmowa powtórka; nawrót (persisting) = problem pełnoprawny,
# design_gap    → blokuje tylko z literalnym kryterium z DESIGN.md.
KNOWN_CLASSES = ("code_bug", "verify_defect", "env_issue", "flaky", "design_gap")
KNOWN_STATUSES = ("new", "persisting", "resolved")
_BLOCKING_CLASSES = ("code_bug", "verify_defect", "design_gap")


def _norm(text: str) -> str:
    """Normalizacja do porównań tekstu (LLM-y przekręcają case i whitespace)."""
    return " ".join((text or "").split()).casefold()


def _is_open(problem: dict) -> bool:
    return problem.get("status") != "resolved"


def validate_problems(problems: list) -> list[str]:
    """Błędy strukturalne rejestru; pusta lista = rejestr poprawny."""
    errors = []
    for i, p in enumerate(problems):
        if not isinstance(p, dict):
            errors.append(f"wpis #{i} nie jest obiektem")
            continue
        if not str(p.get("id") or "").strip():
            errors.append(f"wpis #{i}: brak id")
        if p.get("status") not in KNOWN_STATUSES:
            errors.append(f"{p.get('id') or f'wpis #{i}'}: nieznany status {p.get('status')!r}")
        if p.get("class") not in KNOWN_CLASSES:
            errors.append(f"{p.get('id') or f'wpis #{i}'}: nieznana klasa {p.get('class')!r}")
    return errors


def ledger_complete(previous: list[dict], current: list[dict]) -> tuple[bool, list[str]]:
    """Każdy OTWARTY problem z cyklu N-1 musi wrócić jako resolved|persisting.

    Powrót ze statusem "new" nie liczy się — to błąd odhaczania (agent zgubił
    historię problemu). Zwraca (ok, lista nieodhaczonych id)."""
    accounted = {p.get("id") for p in current
                 if isinstance(p, dict) and p.get("status") in ("resolved", "persisting")}
    missing = [p["id"] for p in previous
               if isinstance(p, dict) and _is_open(p) and p.get("id") not in accounted]
    return (not missing), missing


def degrade_design_gaps(problems: list[dict], design_text: str) -> tuple[list[dict], list[dict]]:
    """Podziel na (ważne, zdegradowane): design_gap bez literalnego kryterium
    obecnego w DESIGN.md (podciąg po normalizacji) nie blokuje PASS — staje się
    nieblokującą notatką. Zamyka to "ruchome bramki" świeżego agenta."""
    design = _norm(design_text)
    kept, degraded = [], []
    for p in problems:
        if p.get("class") != "design_gap":
            kept.append(p)
            continue
        criterion = _norm(p.get("criterion", ""))
        (kept if criterion and criterion in design else degraded).append(p)
    return kept, degraded


def open_blocking(problems: list[dict]) -> list[dict]:
    """Problemy blokujące PASS: otwarte code_bug/verify_defect/design_gap
    (design_gap już po degradacji) oraz nawracający flaky (persisting)."""
    return [p for p in problems if _is_open(p)
            and (p.get("class") in _BLOCKING_CLASSES
                 or (p.get("class") == "flaky" and p.get("status") == "persisting"))]


def progress_made(previous: list[dict], current: list[dict]) -> bool:
    """Cykl postępowy: coś rozwiązano ALBO ubyło otwartych blokerów.

    Pierwszy cykl (bez poprzednika) zawsze liczy się jako postęp — samo
    znalezienie problemów to nie stagnacja."""
    if not previous:
        return True
    if any(p.get("status") == "resolved" for p in current if isinstance(p, dict)):
        return True
    return len(open_blocking(current)) < len(open_blocking(previous))


def missing_repro(problems: list[dict]) -> list[str]:
    """Id NOWYCH code_bugów bez repro_cmd — naprawialny bug musi mieć
    wykonywalną reprodukcję (bramkę przyszłego zadania naprawczego)."""
    return [p["id"] for p in problems
            if p.get("class") == "code_bug" and p.get("status") == "new"
            and not str(p.get("repro_cmd") or "").strip()]


def for_planner(problems: list[dict]) -> list[dict]:
    """Problemy przekazywane planiście jako materiał na zadania naprawcze:
    otwarte, bez env_issue (osobny tor) i bez świeżych flaky (darmowa powtórka
    zanim staną się zadaniem)."""
    return [p for p in problems if _is_open(p)
            and p.get("class") != "env_issue"
            and not (p.get("class") == "flaky" and p.get("status") != "persisting")]


def pass_blockers(evidence: dict, problems: list[dict]) -> list[str]:
    """Powody, dla których PASS jest niedozwolony; pusta lista = PASS możliwy.

    Bramka anty-"ogłaszaniu zwycięstwa": agent nie może przegłosować czerwonego
    rc targetu; rc None (nie wystartowało/timeout) też nie jest zielenią."""
    reasons = [f"target {name}: rc={res.get('rc')}"
               for name, res in sorted(evidence.items()) if res.get("rc") != 0]
    reasons += [f"otwarty problem {p.get('id')} ({p.get('class')})"
                for p in open_blocking(problems)]
    return reasons
