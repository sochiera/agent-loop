"""DESIGN.md nie rośnie bez końca: próg rozmiaru wstrzykuje zadanie
kompaktujące do wsadu planisty (ROZSTRZYGNIĘTE → docs/DECISIONS.md),
a otwarte design_gap chronią swoje dosłowne kryteria przed usunięciem."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from forge import prompts
from forge.config import Config
from forge.state import State


class DesignCompactNoticeTest(unittest.TestCase):
    def test_plan_batch_prompt_has_no_notice_by_default(self) -> None:
        prompt = prompts.plan_batch_prompt(5, 1, "app")
        self.assertNotIn("UWAGA: docs/DESIGN.md", prompt)

    def test_plan_batch_prompt_includes_notice_when_passed(self) -> None:
        notice = prompts.design_compact_notice()
        prompt = prompts.plan_batch_prompt(5, 1, "app", design_compact=notice)
        self.assertIn("UWAGA: docs/DESIGN.md", prompt)
        self.assertIn("docs/DECISIONS.md", prompt)

    def test_notice_lists_protected_design_gap_criteria(self) -> None:
        notice = prompts.design_compact_notice(["Wzór trafienia: baza 50, limit 5-95"])
        self.assertIn("Wzór trafienia: baza 50, limit 5-95", notice)
        self.assertIn("NIE WOLNO", notice)

    def test_notice_without_criteria_has_no_protected_block(self) -> None:
        notice = prompts.design_compact_notice([])
        self.assertNotIn("NIE WOLNO USUNĄĆ ANI PRZEFORMUŁOWAĆ", notice)


class SharedPrinciplesDecisionsTest(unittest.TestCase):
    def test_shared_principles_route_decisions_to_decisions_md(self) -> None:
        self.assertIn("docs/DECISIONS.md", prompts.SHARED_PRINCIPLES)

    def test_bootstrap_creates_decisions_file(self) -> None:
        boot = prompts.bootstrap_prompt("brief")
        self.assertIn("docs/DECISIONS.md", boot)


class PhasePlanBatchCompactTriggerTest(unittest.TestCase):
    def _project_with_design(self, size_bytes: int) -> str:
        tmp = tempfile.mkdtemp()
        docs = Path(tmp) / "docs"
        docs.mkdir()
        (docs / "DESIGN.md").write_text("x" * size_bytes, encoding="utf-8")
        return tmp

    def _capture_planner_prompt(self, project: str, cfg: Config, state: State) -> str:
        from forge.orchestrate import phase_plan_batch
        captured: dict = {}

        def fake_planner(prompt, *_a, **_k):
            captured["prompt"] = prompt
            return '```json\n{"no_more_tasks": true, "tasks": []}\n```'

        with patch("forge.orchestrate.commit_all"), \
             patch("forge.orchestrate.run_planner", side_effect=fake_planner):
            phase_plan_batch(cfg, project, state, lambda ph: os.path.join(project, "log"))
        return captured["prompt"]

    def test_below_threshold_no_notice_in_prompt(self) -> None:
        project = self._project_with_design(100)
        prompt = self._capture_planner_prompt(
            project, Config(design_compact_bytes=1000), State())
        self.assertNotIn("UWAGA: docs/DESIGN.md", prompt)

    def test_above_threshold_injects_compaction_notice(self) -> None:
        project = self._project_with_design(2000)
        state = State(verify_problems=[
            {"id": "P-001", "class": "design_gap", "status": "new",
             "criterion": "Szansa trafienia liczona jako całkowity procent"},
        ])
        prompt = self._capture_planner_prompt(
            project, Config(design_compact_bytes=1000), state)
        self.assertIn("UWAGA: docs/DESIGN.md", prompt)
        self.assertIn("Szansa trafienia liczona jako całkowity procent", prompt)

    def test_zero_threshold_disables_feature(self) -> None:
        project = self._project_with_design(999999)
        prompt = self._capture_planner_prompt(
            project, Config(design_compact_bytes=0), State())
        self.assertNotIn("UWAGA: docs/DESIGN.md", prompt)

    def test_resolved_design_gap_criteria_are_not_protected(self) -> None:
        project = self._project_with_design(2000)
        state = State(verify_problems=[
            {"id": "P-001", "class": "design_gap", "status": "resolved",
             "criterion": "Stara, już naprawiona usterka"},
        ])
        prompt = self._capture_planner_prompt(
            project, Config(design_compact_bytes=1000), state)
        self.assertIn("UWAGA: docs/DESIGN.md", prompt)
        self.assertNotIn("Stara, już naprawiona usterka", prompt)


class DesignCompactNoticeCapTest(unittest.TestCase):
    def test_long_criteria_list_is_capped_with_overflow_note(self) -> None:
        criteria = [f"Kryterium {i}" for i in range(25)]
        notice = prompts.design_compact_notice(criteria)
        self.assertIn("Kryterium 0", notice)
        self.assertIn("Kryterium 19", notice)
        self.assertNotIn("Kryterium 20", notice)
        self.assertIn("jeszcze 5", notice)

    def test_short_criteria_list_has_no_overflow_note(self) -> None:
        notice = prompts.design_compact_notice(["a", "b"])
        self.assertNotIn("obcięcie", notice.lower())


class DesignCompactEscalationTest(unittest.TestCase):
    def test_no_escalation_when_stalls_zero(self) -> None:
        notice = prompts.design_compact_notice(stalls=0)
        self.assertNotIn("ZIGNOROWANO", notice.upper())

    def test_escalation_text_when_stalls_positive(self) -> None:
        notice = prompts.design_compact_notice(stalls=2)
        self.assertIn("MUSI się znaleźć", notice)


class PlanBatchStallTrackingTest(unittest.TestCase):
    """Bez śledzenia ignorowań notatka byłaby bezzębna: planista mógłby ją
    pomijać w nieskończoność bez żadnej reakcji pętli (znalezisko z review)."""

    def _project_with_design(self, size_bytes: int) -> str:
        tmp = tempfile.mkdtemp()
        docs = Path(tmp) / "docs"
        docs.mkdir()
        (docs / "DESIGN.md").write_text("x" * size_bytes, encoding="utf-8")
        return tmp

    def _run_plan_batch(self, project: str, cfg: Config, state: State, planner_json: str) -> str:
        from forge.orchestrate import phase_plan_batch
        captured: dict = {}

        def fake_planner(prompt, *_a, **_k):
            captured["prompt"] = prompt
            return planner_json

        with patch("forge.orchestrate.commit_all"), \
             patch("forge.orchestrate.run_planner", side_effect=fake_planner):
            phase_plan_batch(cfg, project, state, lambda ph: os.path.join(project, "log"))
        return captured["prompt"]

    def test_ignoring_notice_increments_stall_counter(self) -> None:
        project = self._project_with_design(2000)
        cfg = Config(design_compact_bytes=1000)
        state = State()
        no_refactor = '```json\n{"no_more_tasks": false, "tasks": []}\n```'

        self._run_plan_batch(project, cfg, state, no_refactor)
        self.assertEqual(state.design_compact_stalls, 1)

        self._run_plan_batch(project, cfg, state, no_refactor)
        self.assertEqual(state.design_compact_stalls, 2)

    def test_second_prompt_escalates_after_first_stall(self) -> None:
        project = self._project_with_design(2000)
        cfg = Config(design_compact_bytes=1000)
        state = State()
        no_refactor = '```json\n{"no_more_tasks": false, "tasks": []}\n```'

        self._run_plan_batch(project, cfg, state, no_refactor)
        self.assertEqual(state.design_compact_stalls, 1)

        second_prompt = self._run_plan_batch(project, cfg, state, no_refactor)
        self.assertIn("MUSI się znaleźć", second_prompt)

    def test_refactor_task_present_resets_stall_counter(self) -> None:
        project = self._project_with_design(2000)
        tasks_dir = Path(project) / ".forge" / "tasks"
        tasks_dir.mkdir(parents=True)
        (tasks_dir / "task-001.md").write_text("# Zadanie 001", encoding="utf-8")
        cfg = Config(design_compact_bytes=1000)
        state = State(design_compact_stalls=3)
        with_refactor = ('```json\n{"no_more_tasks": false, "tasks": ['
                         '{"id":"task-001","title":"Kompaktuj DESIGN.md",'
                         '"file":".forge/tasks/task-001.md","criteria":[],'
                         '"test_globs":[],"code_globs":[],"kind":"refactor"}'
                         ']}\n```')

        self._run_plan_batch(project, cfg, state, with_refactor)
        self.assertEqual(state.design_compact_stalls, 0)


if __name__ == "__main__":
    unittest.main()
