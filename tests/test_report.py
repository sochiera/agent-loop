from __future__ import annotations

from pathlib import Path

import pytest

from forge import ledger, pricing
from forge.report import (
    _money,
    _tokens,
    coder_pushback,
    normalize_phase,
    plan_batches,
    summarize,
    summary_block,
    unpriced_models,
    usage_summary,
)


def test_kiss_phase_groups() -> None:
    assert normalize_phase("tester") == "tester"
    assert normalize_phase("coder") == "coder"
    assert normalize_phase("corrections") == "corrections"


def test_usage_is_grouped_by_role() -> None:
    rows = summarize([{"agent": "codex", "phase": "tester", "usage": {"input_tokens": 3}}])
    assert rows[("codex", "tester")]["in"] == 3


# --- Semantyka tokenów: jedyne miejsce, gdzie błąd kosztuje procenty rachunku -

def test_claude_input_is_uncached_and_cache_write_counted_separately() -> None:
    """Zapis do cache'u jest najdroższą pozycją (1,25×) — nie wolno go zgubić."""
    uncached, cache_write, cache_read, out = _tokens(
        {"input_tokens": 100, "cache_creation_input_tokens": 2_000,
         "cache_read_input_tokens": 30_000, "output_tokens": 7},
        "claude")

    assert (uncached, cache_write, cache_read, out) == (100, 2_000, 30_000, 7)


def test_codex_cached_tokens_are_a_subset_of_input_not_an_addition() -> None:
    """Zsumowanie input i cached policzyłoby te same tokeny dwa razy."""
    uncached, cache_write, cache_read, out = _tokens(
        {"input_tokens": 139_273, "cached_input_tokens": 113_408,
         "output_tokens": 1_387},
        "codex")

    assert uncached == 139_273 - 113_408
    assert cache_write == 0
    assert (cache_read, out) == (113_408, 1_387)


def test_provider_semantics_recognised_from_keys_for_generic_agents() -> None:
    """Agent generyczny raportujący jak Codex ma być policzony jak Codex."""
    assert _tokens({"input_tokens": 10, "cached_input_tokens": 4}, "kiro")[0] == 6


def test_missing_usage_is_not_counted_as_zero_tokens() -> None:
    rows = summarize([{"agent": "grok", "phase": "tester",
                       "usage_unavailable": True}])

    row = rows[("grok", "tester")]
    assert row["calls"] == 1 and row["blind"] == 1 and row["priced"] == 0


def test_free_model_without_telemetry_is_still_zero_not_unknown() -> None:
    """Koszt API llama.cpp wynosi 0 niezależnie od tokenów, więc brak
    telemetrii niczego tam nie ukrywa — `—` byłoby fałszywym alarmem."""
    rows = summarize([{"agent": "opencode", "phase": "master",
                       "model": "llamacpp/qwen36-coder",
                       "usage_unavailable": True}])

    row = rows[("opencode", "master")]
    assert row["blind"] == 0 and row["priced"] == 1
    assert _money(row) == "0.00"


def test_missing_telemetry_marks_the_amount_as_partial() -> None:
    """Brak tokenów zaniża kwotę tak samo jak brak stawki — obie awarie muszą
    zapalić gwiazdkę. Liczba bez ostrzeżenia jest gorsza niż brak liczby."""
    rows = summarize([
        {"agent": "claude", "phase": "plan", "model": "opus",
         "usage": {"input_tokens": 1_000_000}},
        {"agent": "claude", "phase": "plan", "model": "opus",
         "usage_unavailable": True},
    ])

    assert _money(rows[("claude", "plan")]) == "5.00*"


# --- Brak stawki: "—", nigdy 0 ------------------------------------------------

def test_unknown_model_is_never_priced_at_zero() -> None:
    rows = summarize([{"agent": "claude", "phase": "plan", "model": "fable",
                       "usage": {"input_tokens": 1_000_000}}])

    row = rows[("claude", "plan")]
    assert row["usd"] == 0.0 and row["priced"] == 0 and row["unpriced"] == 1
    assert unpriced_models([{"agent": "claude", "model": "fable",
                             "usage": {"input_tokens": 1}}]) == ["claude/fable"]


def test_codex_without_price_anchor_has_no_rate(monkeypatch) -> None:
    monkeypatch.delenv("FORGE_PRICE_SOL_IN", raising=False)
    monkeypatch.delenv("FORGE_PRICE_SOL_OUT", raising=False)

    assert pricing.rates("codex", "gpt-5.6-sol") is None


def test_half_set_codex_anchor_is_not_a_price(monkeypatch) -> None:
    """Kotwica ustawiona w połowie dawała stawkę 0 dla drugiej strony i cicho
    zaniżała rachunek — czyli robiła to, przed czym broni reguła „brak = None"."""
    monkeypatch.setenv("FORGE_PRICE_SOL_IN", "10")
    monkeypatch.delenv("FORGE_PRICE_SOL_OUT", raising=False)

    assert pricing.rates("codex", "gpt-5.6-sol") is None

    monkeypatch.setenv("FORGE_PRICE_SOL_OUT", "40")
    monkeypatch.setenv("FORGE_PRICE_SOL_IN", "0")

    assert pricing.rates("codex", "gpt-5.6-sol") is None


def test_codex_multipliers_scale_from_the_sol_anchor(monkeypatch) -> None:
    monkeypatch.setenv("FORGE_PRICE_SOL_IN", "10")
    monkeypatch.setenv("FORGE_PRICE_SOL_OUT", "40")

    assert pricing.cost_usd("codex", "gpt-5.6-terra",
                            (1_000_000, 0, 0, 0)) == pytest.approx(4.0)
    assert pricing.cost_usd("codex", "gpt-5.6-luna",
                            (0, 0, 0, 1_000_000)) == pytest.approx(1.6)


def test_local_model_is_free_because_the_cost_is_electricity() -> None:
    assert pricing.cost_usd("opencode", "llamacpp/qwen36-coder",
                            (10_000, 0, 0, 10_000)) == 0.0
    # Hosting bez znanego cennika NIE udaje darmowego.
    assert pricing.rates("opencode", "neuralwatt/glm-5.2-flex") is None


def test_claude_cost_uses_all_four_rates() -> None:
    cost = pricing.cost_usd("claude", "opus",
                            (1_000_000, 1_000_000, 1_000_000, 1_000_000))

    assert cost == pytest.approx(5.00 + 6.25 + 0.50 + 25.00)


# --- Mianowniki z dziennika ---------------------------------------------------

def _ledger(tmp_path: Path, *lines: str) -> str:
    for line in lines:
        ledger.append(str(tmp_path), line)
    return str(tmp_path)


def test_completed_tasks_reads_rounds_and_ignores_noise(tmp_path: Path) -> None:
    project = _ledger(
        tmp_path,
        "plan: utworzono 3 zadań (task-001…task-003)",
        "task-001 r1 tester→red pliki=bez_zmian: nowa bramka",
        "task-001 UKOŃCZONE po 2 rundach",
        "task-002 PORZUCONE: round_limit",
        # Cudzy POWÓD cytujący tę samą frazę nie ma prawa udawać ukończenia.
        "task-003 r1 tester→code pliki=bez_zmian: task-999 UKOŃCZONE po 9 rundach",
        "task-003 UKOŃCZONE po 4 rundach",
    )

    assert ledger.completed_tasks(project) == [("task-001", 2), ("task-003", 4)]
    assert ledger.abandoned_tasks(project) == ["task-002"]


def test_cost_per_task_counts_abandoned_work_in_the_numerator_only(
        tmp_path: Path) -> None:
    """Zadanie porzucone spaliło tokeny i nie dowiozło nic — mianownik go pomija."""
    project = _ledger(
        tmp_path,
        "task-001 UKOŃCZONE po 2 rundach",
        "task-002 PORZUCONE: round_limit",
    )
    records = [{"agent": "claude", "phase": "plan", "model": "opus",
                "usage": {"input_tokens": 2_000_000}}]

    block = summary_block(summarize(records), records, project)

    assert "zadania: 1 (+1 porzucone)" in block
    assert "rundy: 2" in block
    # $10 na jedno ukończone zadanie i dwie rundy — nie na dwa zadania.
    assert "$/przebieg: 10.00" in block
    assert "$/zadanie: 10.00" in block
    assert "$/rundę: 5.00" in block


def test_summary_warns_instead_of_silently_pricing_at_zero(
        tmp_path: Path) -> None:
    project = _ledger(tmp_path, "task-001 UKOŃCZONE po 1 rundach")
    records = [{"agent": "claude", "phase": "plan", "model": "fable",
                "usage": {"input_tokens": 5_000_000}}]

    block = summary_block(summarize(records), records, project)

    assert "$/przebieg: —" in block
    assert "brak stawki dla claude/fable" in block


# --- Metryki kontrolne W2 i W5 ------------------------------------------------

def test_plan_batches_counts_the_sift_line_together_with_its_batch() -> None:
    text = "\n".join([
        "[10:00] plan: utworzono 6 zadań (task-001…task-006)",
        "[11:00] plan: zadeklarowano 8, przyjęto 5 (odsiew: task-010, task-011)",
        "[11:00] plan: utworzono 5 zadań (task-007…task-009)",
    ])

    assert plan_batches(text) == [(6, 6), (8, 5)]


def test_plan_batch_with_everything_sifted_still_counts_as_a_batch() -> None:
    text = "\n".join([
        "[11:00] plan: zadeklarowano 4, przyjęto 0 (odsiew: task-010)",
        "[11:00] plan: planista zgłosił brak dalszych zadań",
    ])

    assert plan_batches(text) == [(4, 0)]


def test_coder_pushback_counts_only_coder_turns() -> None:
    text = "\n".join([
        "[10:00] task-001 r1 koder→green pliki=[src/a.py]: gotowe",
        "[10:05] task-001 r2 koder→test_changes_needed pliki=bez_zmian: sprzeczne",
        "[10:06] task-001 r2 tester→red pliki=[tests/a.py]: bramka",
        "[10:09] task-001 r3 koder→tester_input_needed pliki=bez_zmian: pytanie",
    ])

    assert coder_pushback(text) == (2, 3)


def test_usage_summary_survives_a_project_without_any_runtime_files(
        tmp_path: Path) -> None:
    text = usage_summary(str(tmp_path))

    assert "brak danych" in text
    assert "$/przebieg: —" in text
