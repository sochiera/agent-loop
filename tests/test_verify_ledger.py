from __future__ import annotations

import unittest

from forge.verify_ledger import (degrade_design_gaps, for_planner,
                                 ledger_complete, missing_repro,
                                 open_blocking, pass_blockers, progress_made,
                                 validate_problems)


def _p(pid: str, status: str = "new", cls: str = "code_bug", **extra) -> dict:
    return {"id": pid, "status": status, "class": cls,
            "title": f"problem {pid}", **extra}


class ValidateProblemsTest(unittest.TestCase):
    def test_well_formed_problems_pass(self) -> None:
        self.assertEqual(validate_problems([_p("P-001"), _p("P-002", "resolved")]), [])

    def test_missing_fields_and_unknown_values_are_reported(self) -> None:
        errors = validate_problems([
            {"status": "new", "class": "code_bug", "title": "bez id"},
            _p("P-002", status="wontfix"),
            _p("P-003", cls="cosmic_ray"),
            "nie-dict",
        ])
        self.assertEqual(len(errors), 4)

    def test_empty_ledger_is_valid(self) -> None:
        self.assertEqual(validate_problems([]), [])


class LedgerCompleteTest(unittest.TestCase):
    def test_every_open_problem_must_be_accounted_for(self) -> None:
        previous = [_p("P-001"), _p("P-002", "persisting"), _p("P-003", "resolved")]
        current = [_p("P-001", "resolved"), _p("P-004")]
        ok, missing = ledger_complete(previous, current)
        self.assertFalse(ok)
        self.assertEqual(missing, ["P-002"])  # P-003 był już zamknięty

    def test_reopening_old_problem_as_new_does_not_count(self) -> None:
        # Stary problem MUSI wrócić jako resolved|persisting — "new" to błąd agenta.
        ok, missing = ledger_complete([_p("P-001")], [_p("P-001", "new")])
        self.assertFalse(ok)
        self.assertEqual(missing, ["P-001"])

    def test_complete_ledger_passes(self) -> None:
        previous = [_p("P-001"), _p("P-002", "persisting")]
        current = [_p("P-001", "resolved"), _p("P-002", "persisting"), _p("P-005")]
        self.assertEqual(ledger_complete(previous, current), (True, []))

    def test_first_cycle_has_nothing_to_account_for(self) -> None:
        self.assertEqual(ledger_complete([], [_p("P-001")]), (True, []))


class DesignGapDegradationTest(unittest.TestCase):
    DESIGN = "# Design\nGracz może zapisać stan gry w dowolnym momencie.\n"

    def test_gap_with_literal_criterion_is_kept(self) -> None:
        gap = _p("P-001", cls="design_gap",
                 criterion="gracz MOŻE zapisać   stan gry")  # case/whitespace szum
        kept, degraded = degrade_design_gaps([gap], self.DESIGN)
        self.assertEqual(kept, [gap])
        self.assertEqual(degraded, [])

    def test_gap_without_matching_criterion_is_degraded(self) -> None:
        gap = _p("P-001", cls="design_gap", criterion="wymyślone kryterium")
        kept, degraded = degrade_design_gaps([gap], self.DESIGN)
        self.assertEqual(kept, [])
        self.assertEqual(degraded, [gap])

    def test_gap_with_empty_criterion_is_degraded(self) -> None:
        kept, degraded = degrade_design_gaps(
            [_p("P-001", cls="design_gap"), _p("P-002", cls="design_gap", criterion="")],
            self.DESIGN)
        self.assertEqual(kept, [])
        self.assertEqual(len(degraded), 2)

    def test_other_classes_are_untouched(self) -> None:
        bug = _p("P-001")
        kept, degraded = degrade_design_gaps([bug], self.DESIGN)
        self.assertEqual(kept, [bug])
        self.assertEqual(degraded, [])


class BlockingAndProgressTest(unittest.TestCase):
    def test_open_blocking_selects_unresolved_blocking_classes(self) -> None:
        problems = [
            _p("P-001"),                                  # code_bug new → blokuje
            _p("P-002", cls="verify_defect"),             # blokuje
            _p("P-003", cls="design_gap"),                # blokuje (po degradacji zostały ważne)
            _p("P-004", "resolved"),                      # zamknięty → nie
            _p("P-005", cls="env_issue"),                 # osobny tor (stop) → nie
            _p("P-006", cls="flaky"),                     # nowy flaky → darmowa powtórka, nie
            _p("P-007", cls="flaky", status="persisting"),  # nawrót → pełnoprawny
        ]
        self.assertEqual([p["id"] for p in open_blocking(problems)],
                         ["P-001", "P-002", "P-003", "P-007"])

    def test_progress_requires_resolution_or_fewer_open_blockers(self) -> None:
        prev = [_p("P-001"), _p("P-002")]
        # coś rozwiązano → postęp
        self.assertTrue(progress_made(prev, [_p("P-001", "resolved"),
                                             _p("P-002", "persisting")]))
        # nic nie rozwiązano, ale mniej otwartych blokerów → postęp
        self.assertTrue(progress_made(prev, [_p("P-002", "persisting")]))
        # te same problemy dalej otwarte + nowy → brak postępu
        self.assertFalse(progress_made(prev, [_p("P-001", "persisting"),
                                              _p("P-002", "persisting"),
                                              _p("P-003")]))

    def test_first_cycle_with_problems_counts_as_progress(self) -> None:
        # Cykl 1 nie ma poprzednika — samo znalezienie problemów to nie stagnacja.
        self.assertTrue(progress_made([], [_p("P-001")]))


class ReproAndPlannerFilterTest(unittest.TestCase):
    def test_new_code_bug_requires_repro_cmd(self) -> None:
        problems = [
            _p("P-001"),                                          # brak repro → błąd
            _p("P-002", repro_cmd="bash .forge/verification/cycle-1/repro/P-002.sh"),
            _p("P-003", status="persisting"),                     # stary → repro już był
            _p("P-004", cls="design_gap"),                        # nie wymaga repro
        ]
        self.assertEqual(missing_repro(problems), ["P-001"])

    def test_planner_gets_open_problems_without_env_and_fresh_flaky(self) -> None:
        problems = [
            _p("P-001"),
            _p("P-002", "resolved"),
            _p("P-003", cls="env_issue"),
            _p("P-004", cls="flaky"),
            _p("P-005", cls="flaky", status="persisting"),
        ]
        self.assertEqual([p["id"] for p in for_planner(problems)],
                         ["P-001", "P-005"])


class PassGateTest(unittest.TestCase):
    def test_red_target_blocks_pass_even_with_clean_ledger(self) -> None:
        evidence = {"ci": {"rc": 0}, "hardware": {"rc": 1}}
        blockers = pass_blockers(evidence, [])
        self.assertEqual(len(blockers), 1)
        self.assertIn("hardware", blockers[0])

    def test_open_blocking_problem_blocks_pass_despite_green_targets(self) -> None:
        evidence = {"smoke": {"rc": 0}}
        blockers = pass_blockers(evidence, [_p("P-001", "persisting")])
        self.assertEqual(len(blockers), 1)
        self.assertIn("P-001", blockers[0])

    def test_unstarted_target_is_not_green(self) -> None:
        # rc None = komenda nie wystartowała/timeout — to nie jest zieleń.
        blockers = pass_blockers({"ci": {"rc": None}}, [])
        self.assertEqual(len(blockers), 1)

    def test_all_green_and_ledger_clean_allows_pass(self) -> None:
        evidence = {"ci": {"rc": 0}, "smoke": {"rc": 0}}
        self.assertEqual(pass_blockers(evidence, [_p("P-001", "resolved")]), [])


if __name__ == "__main__":
    unittest.main()
