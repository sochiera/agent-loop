"""Testy PLAN-5: kanon kryteriów, limit DONE-reject, multi-ref, refactor, failed-ref.

TDD: sekcje odpowiadają taskom T5.1–T5.6 z docs/PLAN-5-DONE-KRYTERIA-I-PĘTLE.md.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from forge.config import Config
from forge.state import State


def _init_repo(project: str, files: dict[str, str] | None = None) -> None:
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=project, check=True)
    for rel, body in (files or {}).items():
        path = Path(project, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    if files:
        subprocess.run(["git", "add", "-A"], cwd=project, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=project, check=True)


# =====================================================================
# T5.1: parse_task_criteria + resolve + empty criteria
# =====================================================================

TASK_046_LIKE = """# Zadanie 046: V13.1 — pakiet prezentacji

## Cel
Pierwszy klocek warstwy wizualnej.

## Kryteria akceptacji
- [ ] Istnieje pakiet `src/tbbui/` z `__init__.py`; żaden moduł w `src/tbb/`
      nie importuje `tbbui` (rdzeń bez prezentacji, ARCHITECTURE §1).
- [ ] `tbbui.layout.layout_world(world: WorldMap) -> dict[Region, tuple[int, int]]`
      przypisuje każdemu regionowi mapę dokładnie jedną pozycję `(kolumna, wiersz)`;
      wszystkie pozycje są unikalne.
- [ ] Cały pakiet testów zielony.

## Kontrakt API
- `src/tbbui/layout.py`

## Poza zakresem
Renderowanie SVG.
"""

TASK_033_LIKE = """# Zadanie 033: G10.5b

## Kryteria akceptacji
- [ ] Test w `tests/test_ai.py` na małej mapie **bez osiągalnej wrogiej osady**:
  - po **pierwszym** `take_duchy_turn` osada ma otwarty `Farm` (i brak `Smith`),
  - po **drugim** `take_duchy_turn` osada ma otwarty również `Smith`.
- [ ] Rekrutacja nadal działa w obu turach.
- [ ] Cały pakiet testów zielony.

## Kontrakt API
Bez nowych sygnatur.
"""

TASK_051_LIKE = """# Zadanie 051: R12.1 — refaktor

## Kryteria akceptacji
- [ ] W `src/tbb/ai.py` istnieje prywatny generator `_owned_settlements`.
- [ ] `develop_duchy_settlement` i inne iterują przez `_owned_settlements`.
- [ ] Zachowanie publicznego API bez zmian; pakiet testów zielony.
- [ ] To zadanie refaktoryzacyjne: NIE dodaje nowych testów ani nowych zachowań.

## Poza zakresem
Nowe zachowania.
"""


class ParseTaskCriteriaTest(unittest.TestCase):
    def test_single_line_checkboxes(self) -> None:
        from forge.orchestrate import parse_task_criteria
        md = "## Kryteria akceptacji\n- [ ] alpha\n- [ ] beta\n\n## Inne\n- [ ] ignore\n"
        self.assertEqual(parse_task_criteria(md), ["alpha", "beta"])

    def test_multiline_checkbox_joined(self) -> None:
        from forge.orchestrate import parse_task_criteria
        crits = parse_task_criteria(TASK_046_LIKE)
        self.assertEqual(len(crits), 3)
        self.assertIn("src/tbbui/", crits[0])
        self.assertIn("nie importuje", crits[0])
        self.assertIn("layout_world", crits[1])
        self.assertEqual(crits[2], "Cały pakiet testów zielony.")

    def test_nested_bullets_join_parent(self) -> None:
        from forge.orchestrate import parse_task_criteria
        crits = parse_task_criteria(TASK_033_LIKE)
        self.assertEqual(len(crits), 3)
        self.assertIn("pierwszym", crits[0])
        self.assertIn("drugim", crits[0])
        self.assertIn("Rekrutacja", crits[1])

    def test_checked_and_unchecked(self) -> None:
        from forge.orchestrate import parse_task_criteria
        md = "## Kryteria akceptacji\n- [x] done already\n- [ ] still open\n"
        self.assertEqual(parse_task_criteria(md), ["done already", "still open"])

    def test_missing_section_returns_empty(self) -> None:
        from forge.orchestrate import parse_task_criteria
        self.assertEqual(parse_task_criteria("# Tytuł\n\n## Cel\nfoo\n"), [])

    def test_acceptance_criteria_english_header(self) -> None:
        from forge.orchestrate import parse_task_criteria
        md = "## Acceptance criteria\n- [ ] one criterion\n"
        self.assertEqual(parse_task_criteria(md), ["one criterion"])

    def test_header_with_suffix_still_parses(self) -> None:
        from forge.orchestrate import parse_task_criteria
        md = "## Kryteria akceptacji (MVP)\n- [ ] alpha\n- [ ] beta\n"
        self.assertEqual(parse_task_criteria(md), ["alpha", "beta"])

    def test_r12_style_has_refactor_marker_in_criteria(self) -> None:
        from forge.orchestrate import parse_task_criteria
        crits = parse_task_criteria(TASK_051_LIKE)
        self.assertEqual(len(crits), 4)
        self.assertTrue(any("refaktoryzacyjne" in c for c in crits))


class ResolveTaskCriteriaTest(unittest.TestCase):
    def test_file_wins_over_planner_json(self) -> None:
        from forge.orchestrate import resolve_task_criteria
        with tempfile.TemporaryDirectory() as tmp:
            rel = ".forge/tasks/task-001.md"
            path = Path(tmp, rel)
            path.parent.mkdir(parents=True)
            path.write_text(TASK_051_LIKE, encoding="utf-8")
            task = {
                "file": rel,
                "criteria": ["skrót planisty 1", "skrót planisty 2"],
            }
            canon, source = resolve_task_criteria(tmp, task)
            self.assertEqual(source, "file")
            self.assertEqual(len(canon), 4)
            self.assertTrue(any("_owned_settlements" in c for c in canon))

    def test_planner_fallback_when_no_checkboxes(self) -> None:
        from forge.orchestrate import resolve_task_criteria
        with tempfile.TemporaryDirectory() as tmp:
            rel = ".forge/tasks/task-002.md"
            path = Path(tmp, rel)
            path.parent.mkdir(parents=True)
            path.write_text("# Z\n\n## Cel\nbez kryteriów\n", encoding="utf-8")
            task = {"file": rel, "criteria": ["z JSON planisty"]}
            canon, source = resolve_task_criteria(tmp, task)
            self.assertEqual(source, "planner_fallback")
            self.assertEqual(canon, ["z JSON planisty"])

    def test_empty_when_neither(self) -> None:
        from forge.orchestrate import resolve_task_criteria
        with tempfile.TemporaryDirectory() as tmp:
            rel = ".forge/tasks/task-003.md"
            path = Path(tmp, rel)
            path.parent.mkdir(parents=True)
            path.write_text("# Z\n", encoding="utf-8")
            canon, source = resolve_task_criteria(tmp, {"file": rel, "criteria": []})
            self.assertEqual(source, "empty")
            self.assertEqual(canon, [])


class EmptyCriteriaValidationTest(unittest.TestCase):
    def test_empty_criteria_rejects_even_empty_map(self) -> None:
        from forge.orchestrate import validate_criteria_map
        with tempfile.TemporaryDirectory() as tmp:
            errors = validate_criteria_map([], [], tmp, ["tests/**"])
            self.assertTrue(errors)
            self.assertTrue(any("brak kryteri" in e.lower() or "kanon" in e.lower()
                                for e in errors))

    def test_r12_justified_map_matches_file_canon(self) -> None:
        from forge.orchestrate import (
            parse_task_criteria, validate_criteria_map, _norm_criterion,
        )
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "tests").mkdir()
            (Path(tmp, "tests") / "test_ai.py").write_text(
                "def test_x():\n    assert True\n", encoding="utf-8")
            canon = parse_task_criteria(TASK_051_LIKE)
            cmap = [
                {"criterion": c, "status": "justified",
                 "why": "refaktor strukturalny; regresja w istniejącym pakiecie testów"}
                for c in canon
            ]
            self.assertEqual(validate_criteria_map(canon, cmap, tmp, ["tests/**"]), [])

    def test_short_planner_texts_miss_file_canon(self) -> None:
        from forge.orchestrate import parse_task_criteria, validate_criteria_map
        with tempfile.TemporaryDirectory() as tmp:
            canon = parse_task_criteria(TASK_051_LIKE)
            cmap = [
                {"criterion": "skrót planisty o generatorze", "status": "justified",
                 "why": "x" * 25},
            ]
            errors = validate_criteria_map(canon, cmap, tmp, ["tests/**"])
            self.assertGreaterEqual(len(errors), 3)


class StartTaskResolvesCriteriaTest(unittest.TestCase):
    def test_start_task_sets_criteria_from_file(self) -> None:
        from forge.orchestrate import _start_task
        with tempfile.TemporaryDirectory() as project:
            _init_repo(project)
            rel = ".forge/tasks/task-051.md"
            path = Path(project, rel)
            path.parent.mkdir(parents=True)
            path.write_text(TASK_051_LIKE, encoding="utf-8")
            cfg = Config(git_push=False)
            state = State(
                bootstrapped=True, test_cmd="true", build_cmd="",
                task_queue=[{
                    "id": "task-051", "title": "R12.1", "file": rel,
                    "criteria": ["zły skrót"],
                    "test_globs": ["tests/**"], "code_globs": ["src/**"],
                }],
            )
            _start_task(cfg, project, state)
            self.assertEqual(state.current_task.get("criteria_source"), "file")
            self.assertEqual(len(state.current_task.get("criteria") or []), 4)
            self.assertNotEqual(state.current_task["criteria"][0], "zły skrót")


# =====================================================================
# T5.3: multi-ref + strip [param] (napisane wcześnie — czysta funkcja)
# =====================================================================

class ValidateCriteriaMapMultiRefTest(unittest.TestCase):
    def _project(self, tmp: str) -> str:
        tests = Path(tmp, "tests")
        tests.mkdir()
        (tests / "test_a.py").write_text(
            "def test_move():\n    assert True\n"
            "def test_jump():\n    assert True\n",
            encoding="utf-8")
        return tmp

    def test_list_of_refs(self) -> None:
        from forge.orchestrate import validate_criteria_map
        with tempfile.TemporaryDirectory() as tmp:
            p = self._project(tmp)
            errors = validate_criteria_map(
                ["k1"],
                [{"criterion": "k1", "status": "covered",
                  "test": ["tests/test_a.py::test_move",
                           "tests/test_a.py::test_jump"]}],
                p, ["tests/**"])
            self.assertEqual(errors, [])

    def test_semicolon_split(self) -> None:
        from forge.orchestrate import validate_criteria_map
        with tempfile.TemporaryDirectory() as tmp:
            p = self._project(tmp)
            errors = validate_criteria_map(
                ["k1"],
                [{"criterion": "k1", "status": "covered",
                  "test": ("tests/test_a.py::test_move; "
                           "tests/test_a.py::test_jump")}],
                p, ["tests/**"])
            self.assertEqual(errors, [])

    def test_parametrized_node_id_strips_brackets(self) -> None:
        from forge.orchestrate import validate_criteria_map
        with tempfile.TemporaryDirectory() as tmp:
            p = self._project(tmp)
            errors = validate_criteria_map(
                ["k1"],
                [{"criterion": "k1", "status": "covered",
                  "test": "tests/test_a.py::test_move[BattleResult.WIN]"}],
                p, ["tests/**"])
            self.assertEqual(errors, [])


# =====================================================================
# T5.2: limit DONE reject + escalate + fail_reason
# =====================================================================

class MicroLoopPlan5Base(unittest.TestCase):
    def _state(self, **kw) -> State:
        base = dict(
            bootstrapped=True, test_cmd="true", build_cmd="",
            current_task_title="Zad",
            current_task={"id": "task-001", "title": "Zad",
                          "file": ".forge/tasks/task-001.md",
                          "criteria": ["c1"], "test_globs": ["tests/**"],
                          "code_globs": ["src/**"],
                          "criteria_source": "planner_fallback"},
            phase="micro", micro_sub="test")
        base.update(kw)
        return State(**base)

    def _task_file(self, project: str, body: str | None = None) -> None:
        rel = ".forge/tasks/task-001.md"
        path = Path(project, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        if body is None:
            body = ("# Z\n\n## Kryteria akceptacji\n- [ ] c1\n\n"
                    "## Ścieżki testów\ntests/**\n")
        path.write_text(body, encoding="utf-8")


class DoneRejectEscalationTest(MicroLoopPlan5Base):
    def test_three_rejects_green_gate_escalates_to_review(self) -> None:
        from forge.orchestrate import _run_micro_loop
        with tempfile.TemporaryDirectory() as project:
            _init_repo(project)
            self._task_file(project)
            Path(project, "tests").mkdir()
            (Path(project, "tests") / "test_a.py").write_text(
                "def test_a():\n    pass\n", encoding="utf-8")
            cfg = Config(max_micro_cycles=12, max_done_rejects=3,
                         done_reject_policy="review_if_green",
                         max_green_retries=0, git_push=False, lock_tests=False,
                         tester_agent="codex", coder_agent="codex")
            state = self._state()
            # Zła mapa (nie pasuje do c1) — 3× done
            bad = ('```json\n{"action":"done","criteria_map":['
                   '{"criterion":"INNE","status":"justified","why":"'
                   + ("y" * 25) + '"}]}\n```')

            def fake(name, prompt, cfg_, proj, log, *, session_id=None,
                     model=None, effort=None):
                return bad, "sess"

            with patch("forge.orchestrate.run_agent_session", side_effect=fake), \
                 patch("forge.orchestrate.run_gate", return_value=(True, "")), \
                 patch("forge.orchestrate._task_gate", return_value=(True, "")):
                out = _run_micro_loop(cfg, project, state,
                                      lambda ph: os.path.join(project, "log"))

            self.assertEqual(out, "escalate")
            self.assertEqual(state.phase, "review")
            self.assertGreaterEqual(state.done_reject_count, 3)

    def test_three_rejects_red_gate_fails_with_done_map_reason(self) -> None:
        from forge.orchestrate import _run_micro_loop
        with tempfile.TemporaryDirectory() as project:
            _init_repo(project)
            self._task_file(project)
            cfg = Config(max_micro_cycles=12, max_done_rejects=3,
                         done_reject_policy="review_if_green",
                         max_green_retries=0, git_push=False,
                         tester_agent="codex", coder_agent="codex")
            state = self._state()
            bad = ('```json\n{"action":"done","criteria_map":['
                   '{"criterion":"INNE","status":"justified","why":"'
                   + ("y" * 25) + '"}]}\n```')

            def fake(name, prompt, cfg_, proj, log, *, session_id=None,
                     model=None, effort=None):
                return bad, "sess"

            with patch("forge.orchestrate.run_agent_session", side_effect=fake), \
                 patch("forge.orchestrate.run_gate", return_value=(False, "red")), \
                 patch("forge.orchestrate._task_gate", return_value=(False, "red")):
                out = _run_micro_loop(cfg, project, state,
                                      lambda ph: os.path.join(project, "log"))

            self.assertFalse(out)
            self.assertTrue((state.fail_reason or "").startswith("done_map:"))

    def test_policy_fail_fails_without_review(self) -> None:
        from forge.orchestrate import _run_micro_loop
        with tempfile.TemporaryDirectory() as project:
            _init_repo(project)
            self._task_file(project)
            cfg = Config(max_micro_cycles=12, max_done_rejects=2,
                         done_reject_policy="fail",
                         max_green_retries=0, git_push=False,
                         tester_agent="codex", coder_agent="codex")
            state = self._state()
            bad = ('```json\n{"action":"done","criteria_map":['
                   '{"criterion":"INNE","status":"justified","why":"'
                   + ("y" * 25) + '"}]}\n```')

            def fake(name, prompt, cfg_, proj, log, *, session_id=None,
                     model=None, effort=None):
                return bad, "sess"

            with patch("forge.orchestrate.run_agent_session", side_effect=fake), \
                 patch("forge.orchestrate.run_gate", return_value=(True, "")), \
                 patch("forge.orchestrate._task_gate", return_value=(True, "")):
                out = _run_micro_loop(cfg, project, state,
                                      lambda ph: os.path.join(project, "log"))

            self.assertFalse(out)
            self.assertTrue((state.fail_reason or "").startswith("done_map:"))
            self.assertNotEqual(state.phase, "review")

    def test_wrote_test_resets_done_reject_count(self) -> None:
        from forge.orchestrate import _run_micro_loop
        with tempfile.TemporaryDirectory() as project:
            _init_repo(project)
            self._task_file(project)
            Path(project, "tests").mkdir(exist_ok=True)
            cfg = Config(max_micro_cycles=12, max_done_rejects=5,
                         done_reject_policy="continue",
                         max_green_retries=0, git_push=False, lock_tests=False,
                         tester_agent="codex", coder_agent="codex")
            state = self._state()
            calls = {"n": 0}
            bad = ('```json\n{"action":"done","criteria_map":['
                   '{"criterion":"INNE","status":"justified","why":"'
                   + ("y" * 25) + '"}]}\n```')
            good_done = ('```json\n{"action":"done","criteria_map":['
                         '{"criterion":"c1","test":"tests/test_a.py::test_a",'
                         '"status":"covered"}]}\n```')

            def fake(name, prompt, cfg_, proj, log, *, session_id=None,
                     model=None, effort=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    return bad, "s"
                if calls["n"] == 2:
                    Path(proj, "tests", "test_a.py").write_text(
                        "def test_a():\n    assert move()\n", encoding="utf-8")
                    return '```json\n{"action":"wrote_test","about":"x"}\n```', "s"
                if calls["n"] == 3:
                    Path(proj, "src").mkdir(exist_ok=True)
                    Path(proj, "src", "a.py").write_text(
                        "def move():\n    return True\n", encoding="utf-8")
                    return '```json\n{"notes":"ok"}\n```', "s"
                return good_done, "s"

            # c1 bad done → reject; wrote_test red gate; code green; done green
            gates = iter([
                (True, ""),   # not used for bad done path before validate... 
            ])
            # Actually flow: done reject doesn't call red gate for wrote.
            # 1 done: validate fail, no gate for accept path
            # 2 wrote_test: gate must be red
            # 3 code: gate green
            # 4 done: validate ok, task gate green

            gate_seq = iter([
                (False, "red"),  # after wrote_test
                (True, ""),      # after code (_task_gate)
                (True, ""),      # after done (_task_gate)
            ])

            with patch("forge.orchestrate.run_agent_session", side_effect=fake), \
                 patch("forge.orchestrate.run_gate",
                       side_effect=lambda *a, **k: next(gate_seq)), \
                 patch("forge.orchestrate._task_gate",
                       side_effect=lambda *a, **k: (True, "")):
                out = _run_micro_loop(cfg, project, state,
                                      lambda ph: os.path.join(project, "log"))

            self.assertEqual(out, "done")
            self.assertEqual(state.done_reject_count, 0)


class ReviewPromptEscalationTest(unittest.TestCase):
    def test_review_prompt_includes_escalation_block(self) -> None:
        from forge.prompts import review_task_prompt
        prompt = review_task_prompt(
            ".forge/tasks/t.md", "pytest",
            start_tag="forge/t-start",
            escalation={
                "reject_count": 3,
                "map_errors": ["kryterium bez pokrycia: 'c1'"],
                "criteria": ["c1 full text from file"],
            })
        self.assertIn("ESKALACJA", prompt.upper())
        self.assertIn("c1 full text from file", prompt)
        self.assertIn("kryterium bez pokrycia", prompt)


class WriteTestPromptPlan5Test(unittest.TestCase):
    def test_mentions_file_checkboxes_as_canon(self) -> None:
        from forge.prompts import write_test_prompt
        p = write_test_prompt(".forge/tasks/t.md", "pytest")
        self.assertTrue(
            "checkbox" in p.lower() or "checkboxów" in p.lower()
            or "pliku zadania" in p.lower())

    def test_refactor_variant_prefers_justified(self) -> None:
        from forge.prompts import write_test_prompt
        p = write_test_prompt(".forge/tasks/t.md", "pytest", refactor=True)
        self.assertIn("justified", p.lower())
        self.assertTrue("refaktor" in p.lower() or "refactor" in p.lower()
                        or "nowych testów" in p.lower())

    def test_requires_behavioral_tests_independent_of_implementation(self) -> None:
        from forge.prompts import write_test_prompt
        p = write_test_prompt(".forge/tasks/t.md", "pytest")
        self.assertIn("ŹRÓDŁA PRAWDY", p)
        self.assertIn("prywatnych", p)
        self.assertIn("refaktor", p.lower())


class BootstrapArchitecturePromptTest(unittest.TestCase):
    def test_review_covers_architecture_risks_and_returns_gate_verdict(self) -> None:
        from forge.prompts import bootstrap_architecture_review_prompt
        p = bootstrap_architecture_review_prompt("/tmp/brief.md", "pytest")
        self.assertIn("NIEZALEŻNY RECENZENT ARCHITEKTURY", p)
        self.assertIn("model danych", p)
        self.assertIn('"verdict": "approve"', p)

    def test_repair_prompt_requires_full_updated_bootstrap_contract(self) -> None:
        from forge.prompts import bootstrap_architecture_fix_prompt
        p = bootstrap_architecture_fix_prompt(["zmień stack"], "pytest", "/tmp/brief.md")
        self.assertIn("pełny kontrakt bootstrapu", p)
        self.assertIn('"test_cmd"', p)
        self.assertIn("/tmp/brief.md", p)


class PlanBatchPromptPlan5Test(unittest.TestCase):
    def test_mentions_kind_refactor_and_literal_criteria(self) -> None:
        from forge.prompts import plan_batch_prompt
        p = plan_batch_prompt(5, 1)
        self.assertIn("kind", p)
        self.assertIn("refactor", p)
        self.assertTrue("dosłown" in p.lower() or "checkbox" in p.lower()
                        or "checkboxów" in p.lower())

    def test_anti_monolith_hint(self) -> None:
        from forge.prompts import plan_batch_prompt
        p = plan_batch_prompt(5, 1)
        self.assertTrue("rozbij" in p.lower() or "monolit" in p.lower()
                        or "end-to-end" in p.lower() or "E2E" in p
                        or "drobniej" in p.lower())


class RefactorDetectionTest(unittest.TestCase):
    def test_kind_field(self) -> None:
        from forge.orchestrate import is_refactor_task
        self.assertTrue(is_refactor_task({"kind": "refactor", "criteria": []}, ""))

    def test_marker_in_body(self) -> None:
        from forge.orchestrate import is_refactor_task
        body = "## Poza zakresem\nTo zadanie refaktoryzacyjne: bez nowych testów.\n"
        self.assertTrue(is_refactor_task({"kind": "", "criteria": []}, body))

    def test_nie_dodaje_nowych_testow_in_criteria(self) -> None:
        from forge.orchestrate import is_refactor_task
        self.assertTrue(is_refactor_task({
            "kind": "",
            "criteria": ["To zadanie: NIE dodaje nowych testów ani nowych zachowań."],
        }, ""))

    def test_performance_exclusion_is_not_refactor(self) -> None:
        """„bez nowych testów wydajnościowych" NIE włącza ścieżki refactor (P0)."""
        from forge.orchestrate import is_refactor_task
        body = "## Poza zakresem\nbez nowych testów wydajnościowych\n"
        self.assertFalse(is_refactor_task({"kind": "", "criteria": []}, body))

    def test_ordinary_task(self) -> None:
        from forge.orchestrate import is_refactor_task
        self.assertFalse(is_refactor_task(
            {"kind": "", "criteria": ["zwykłe kryterium feature"]},
            "## Cel\nnowy feature\n"))


class PhasePlanKindTest(unittest.TestCase):
    def test_queue_preserves_kind_and_resolves_criteria(self) -> None:
        """phase_plan path: build task dict like orchestrate does after plan JSON."""
        from forge.orchestrate import build_task_from_plan
        with tempfile.TemporaryDirectory() as project:
            rel = ".forge/tasks/task-010.md"
            path = Path(project, rel)
            path.parent.mkdir(parents=True)
            path.write_text(TASK_051_LIKE, encoding="utf-8")
            raw = {
                "id": "task-010", "title": "R", "file": rel,
                "criteria": ["skrót"], "test_globs": ["tests/**"],
                "code_globs": ["src/**"], "kind": "refactor",
            }
            task = build_task_from_plan(project, raw)
            self.assertEqual(task.get("kind"), "refactor")
            self.assertEqual(task.get("criteria_source"), "file")
            self.assertEqual(len(task.get("criteria") or []), 4)


# =====================================================================
# T5.5: failed-ref
# =====================================================================

class FailTaskArtifactsTest(unittest.TestCase):
    def test_fail_creates_failed_branch_before_reset(self) -> None:
        from forge.orchestrate import _fail_task, _tag, commit_all
        with tempfile.TemporaryDirectory() as project:
            _init_repo(project, {"src/a.py": "x = 1\n"})
            cfg = Config(git_push=False, keep_failed_ref=True, replan_on_failure=False)
            # work after start tag
            Path(project, "src", "a.py").write_text("x = 2\n", encoding="utf-8")
            commit_all(project, "wip green")
            tip = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=project, text=True,
                capture_output=True, check=True).stdout.strip()
            state = State(
                bootstrapped=True, test_cmd="true",
                current_task_title="V13.1",
                current_task={"id": "task-046", "title": "V13.1", "file": "x"},
                task_start_tag="forge/task-046-start",
                fail_reason="done_map: test",
                micro_cycle=3, done_reject_count=3,
            )
            _tag(project, state.task_start_tag)
            # Move start tag to parent of tip so reset goes back
            parent = subprocess.run(
                ["git", "rev-parse", "HEAD~1"], cwd=project, text=True,
                capture_output=True, check=True).stdout.strip()
            subprocess.run(["git", "tag", "-f", state.task_start_tag, parent],
                           cwd=project, check=True)

            _fail_task(cfg, project, state, 1, state.fail_reason)

            # Branch should point at tip (failed work), HEAD reset to start
            br = subprocess.run(
                ["git", "rev-parse", "forge/failed/task-046"], cwd=project,
                text=True, capture_output=True, check=True).stdout.strip()
            self.assertEqual(br, tip)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=project, text=True,
                capture_output=True, check=True).stdout.strip()
            self.assertEqual(head, parent)
            failures = Path(project, ".forge", "failures.md").read_text(encoding="utf-8")
            self.assertIn("done_map:", failures)
            self.assertIn("forge/failed/task-046", failures)


class ConfigPlan5Test(unittest.TestCase):
    def test_defaults(self) -> None:
        cfg = Config()
        self.assertEqual(cfg.max_done_rejects, 3)
        self.assertEqual(cfg.done_reject_policy, "review_if_green")
        self.assertTrue(cfg.keep_failed_ref)
        self.assertFalse(cfg.fail_on_empty_criteria)


class FailOnEmptyCriteriaTest(MicroLoopPlan5Base):
    def test_fail_immediate_when_empty_canon_and_flag(self) -> None:
        from forge.orchestrate import _run_micro_loop, _start_task
        with tempfile.TemporaryDirectory() as project:
            _init_repo(project)
            rel = ".forge/tasks/task-empty.md"
            path = Path(project, rel)
            path.parent.mkdir(parents=True)
            path.write_text("# Z\n\n## Cel\nbez AC\n", encoding="utf-8")
            cfg = Config(git_push=False, fail_on_empty_criteria=True,
                         max_micro_cycles=5)
            state = State(
                bootstrapped=True, test_cmd="true", build_cmd="",
                task_queue=[{
                    "id": "task-empty", "title": "Puste", "file": rel,
                    "criteria": [], "test_globs": ["tests/**"], "code_globs": [],
                }],
            )
            _start_task(cfg, project, state)
            self.assertTrue(state.fail_immediate)
            self.assertTrue((state.fail_reason or "").startswith("done_map:"))
            with patch("forge.orchestrate.run_agent_session") as sess:
                out = _run_micro_loop(cfg, project, state,
                                      lambda ph: os.path.join(project, "log"))
            self.assertFalse(out)
            sess.assert_not_called()


class PolicyContinueTest(MicroLoopPlan5Base):
    def test_continue_does_not_escalate_at_reject_limit(self) -> None:
        from forge.orchestrate import _run_micro_loop
        with tempfile.TemporaryDirectory() as project:
            _init_repo(project)
            self._task_file(project)
            cfg = Config(max_micro_cycles=4, max_done_rejects=2,
                         done_reject_policy="continue",
                         max_green_retries=0, git_push=False,
                         tester_agent="codex", coder_agent="codex")
            state = self._state()
            bad = ('```json\n{"action":"done","criteria_map":['
                   '{"criterion":"INNE","status":"justified","why":"'
                   + ("y" * 25) + '"}]}\n```')

            def fake(name, prompt, cfg_, proj, log, *, session_id=None,
                     model=None, effort=None):
                return bad, "sess"

            with patch("forge.orchestrate.run_agent_session", side_effect=fake), \
                 patch("forge.orchestrate.run_gate", return_value=(True, "")), \
                 patch("forge.orchestrate._task_gate", return_value=(True, "")):
                out = _run_micro_loop(cfg, project, state,
                                      lambda ph: os.path.join(project, "log"))

            # Zużywa cykle do micro_cap, nie escalate/review.
            self.assertFalse(out)
            self.assertNotEqual(state.phase, "review")
            self.assertTrue((state.fail_reason or "").startswith("micro_cap:"))
            self.assertGreaterEqual(state.done_reject_count, 2)


class RejectFeedbackIncludesCanonTest(MicroLoopPlan5Base):
    def test_reject_reasons_include_full_canon_texts(self) -> None:
        from forge.orchestrate import _run_micro_loop
        with tempfile.TemporaryDirectory() as project:
            _init_repo(project)
            self._task_file(project, "# Z\n\n## Kryteria akceptacji\n"
                            "- [ ] pełne kryterium alfa z długim tekstem\n"
                            "- [ ] pełne kryterium beta\n")
            cfg = Config(max_micro_cycles=2, max_done_rejects=5,
                         done_reject_policy="continue",
                         max_green_retries=0, git_push=False,
                         tester_agent="codex", coder_agent="codex")
            state = self._state(current_task={
                "id": "task-001", "title": "Zad",
                "file": ".forge/tasks/task-001.md",
                "criteria": ["skrót"], "test_globs": ["tests/**"],
                "code_globs": ["src/**"],
            })
            bad = ('```json\n{"action":"done","criteria_map":['
                   '{"criterion":"INNE","status":"justified","why":"'
                   + ("y" * 25) + '"}]}\n```')
            seen_prompts = []

            def fake(name, prompt, cfg_, proj, log, *, session_id=None,
                     model=None, effort=None):
                seen_prompts.append(prompt)
                return bad, "sess"

            with patch("forge.orchestrate.run_agent_session", side_effect=fake), \
                 patch("forge.orchestrate.run_gate", return_value=(True, "")), \
                 patch("forge.orchestrate._task_gate", return_value=(True, "")):
                _run_micro_loop(cfg, project, state,
                                lambda ph: os.path.join(project, "log"))

            # Drugi prompt testera powinien dostać pełny kanon z poprzedniego rejectu.
            self.assertGreaterEqual(len(seen_prompts), 2)
            self.assertIn("pełne kryterium alfa", seen_prompts[1])
            self.assertIn("KANON", seen_prompts[1])


class EscalationStateTest(MicroLoopPlan5Base):
    def test_escalate_stores_map_errors_on_state(self) -> None:
        from forge.orchestrate import _run_micro_loop
        with tempfile.TemporaryDirectory() as project:
            _init_repo(project)
            self._task_file(project)
            cfg = Config(max_micro_cycles=12, max_done_rejects=2,
                         done_reject_policy="review_if_green",
                         max_green_retries=0, git_push=False,
                         tester_agent="codex", coder_agent="codex")
            state = self._state()
            bad = ('```json\n{"action":"done","criteria_map":['
                   '{"criterion":"INNE","status":"justified","why":"'
                   + ("y" * 25) + '"}]}\n```')

            def fake(name, prompt, cfg_, proj, log, *, session_id=None,
                     model=None, effort=None):
                return bad, "sess"

            with patch("forge.orchestrate.run_agent_session", side_effect=fake), \
                 patch("forge.orchestrate.run_gate", return_value=(True, "")), \
                 patch("forge.orchestrate._task_gate", return_value=(True, "")):
                out = _run_micro_loop(cfg, project, state,
                                      lambda ph: os.path.join(project, "log"))

            self.assertEqual(out, "escalate")
            self.assertTrue(state.done_escalated)
            self.assertTrue(state.escalation_map_errors)
            self.assertTrue(any("kryterium" in e for e in state.escalation_map_errors))


if __name__ == "__main__":
    unittest.main()
