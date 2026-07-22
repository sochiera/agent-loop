"""Śledzenie dryfu briefu: brief czytany jest nie tylko przy bootstrapie.

Orkiestrator porównuje aktualną treść ``cfg.brief_path`` ze snapshotem z
ostatniego uzgodnienia (``.forge/brief.snapshot.*``, nie STATE.json — więc
funkcja jest w pełni addytywna i nie wymaga migracji starych projektów).
Różnica trafia do planisty jako ``brief_delta`` w ``plan_batch_prompt``,
dokładnie tym samym wzorcem co istniejąca notatka ``design_compact``.

Naprawione po review (4 znaleziska):
1. Snapshot zapisuje się TYLKO gdy planista faktycznie zwrócił używalny wsad
   (zadania albo no_more_tasks) — inaczej zepsuty JSON cicho gubiłby zmianę
   briefu na zawsze. Licznik ``State.brief_amend_stalls`` śledzi ignorowanie
   i eskaluje ton notatki, jak ``design_compact_stalls``.
2. Snapshot zapisuje DOKŁADNIE tekst pokazany planiście (przekazywany jako
   parametr), a nie ponowny odczyt z dysku po run_planner — bez okna TOCTOU,
   w którym edycja briefu w trakcie długiego wywołania agenta ginie bez śladu.
3. Notatka (pełny brief przy pierwszym sync i diff przy każdym kolejnym) jest
   ucinana do rozsądnego rozmiaru, jak ``design_compact_notice``.
4. Snapshot jest przypisany do konkretnej ścieżki briefu (``cfg.brief_path``),
   nie tylko do projektu — zmiana ścieżki/CWD między uruchomieniami nie miesza
   treści dwóch niepowiązanych dokumentów w jeden bezsensowny diff.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from forge import prompts
from forge.config import Config
from forge.state import State


class BriefDeltaPromptTest(unittest.TestCase):
    def test_plan_batch_prompt_has_no_brief_notice_by_default(self) -> None:
        prompt = prompts.plan_batch_prompt(5, 1, "app")
        self.assertNotIn("BRIEF ZMIENIŁ SIĘ", prompt)
        self.assertNotIn("BRIEF ŚLEDZONY PIERWSZY RAZ", prompt)

    def test_plan_batch_prompt_includes_brief_delta_when_passed(self) -> None:
        prompt = prompts.plan_batch_prompt(5, 1, "app", brief_delta="BRIEF ZMIENIŁ SIĘ: xyz")
        self.assertIn("BRIEF ZMIENIŁ SIĘ: xyz", prompt)


class ReadBriefTest(unittest.TestCase):
    def test_none_when_brief_path_missing(self) -> None:
        from forge.orchestrate import _read_brief
        cfg = Config(brief_path=os.path.join(tempfile.mkdtemp(), "nope.md"))
        self.assertIsNone(_read_brief(cfg))

    def test_returns_content_when_brief_exists(self) -> None:
        from forge.orchestrate import _read_brief
        brief = Path(tempfile.mkdtemp()) / "brief.md"
        brief.write_text("Treść.", encoding="utf-8")
        cfg = Config(brief_path=str(brief))
        self.assertEqual(_read_brief(cfg), "Treść.")


class BriefAmendmentNoticeTest(unittest.TestCase):
    """Testy jednostkowe ``_brief_amendment_notice`` — operuje na przekazanym
    tekście (nie czyta pliku sama), więc nie ma tu TOCTOU do symulowania."""

    def _project(self) -> str:
        return tempfile.mkdtemp()

    def test_full_brief_notice_on_first_ever_sync(self) -> None:
        from forge.orchestrate import _brief_amendment_notice
        project = self._project()
        cfg = Config(brief_path=os.path.join(project, "brief.md"))

        notice = _brief_amendment_notice(cfg, project, "Gra o rycerzach.")
        self.assertIn("PIERWSZY RAZ", notice)
        self.assertIn("Gra o rycerzach.", notice)

    def test_no_notice_when_brief_unchanged_since_snapshot(self) -> None:
        from forge.orchestrate import _brief_amendment_notice, _save_brief_snapshot
        project = self._project()
        cfg = Config(brief_path=os.path.join(project, "brief.md"))
        _save_brief_snapshot(cfg, project, "Gra o rycerzach.")

        self.assertEqual(_brief_amendment_notice(cfg, project, "Gra o rycerzach."), "")

    def test_diff_notice_when_brief_changed_since_snapshot(self) -> None:
        from forge.orchestrate import _brief_amendment_notice, _save_brief_snapshot
        project = self._project()
        cfg = Config(brief_path=os.path.join(project, "brief.md"))
        _save_brief_snapshot(cfg, project, "Gra o rycerzach.\nBez magii.")

        notice = _brief_amendment_notice(
            cfg, project, "Gra o rycerzach.\nKlient Godot na Linux.")

        self.assertIn("BRIEF ZMIENIŁ SIĘ", notice)
        self.assertIn("Klient Godot na Linux.", notice)
        self.assertNotIn("+Gra o rycerzach.", notice)  # diff, nie pełny dump

    def test_escalation_text_when_stalls_positive(self) -> None:
        from forge.orchestrate import _brief_amendment_notice
        project = self._project()
        cfg = Config(brief_path=os.path.join(project, "brief.md"))

        notice = _brief_amendment_notice(cfg, project, "Treść.", stalls=2)
        self.assertIn("2", notice)
        self.assertIn("MUSI się znaleźć", notice)

    def test_no_escalation_when_stalls_zero(self) -> None:
        from forge.orchestrate import _brief_amendment_notice
        project = self._project()
        cfg = Config(brief_path=os.path.join(project, "brief.md"))

        notice = _brief_amendment_notice(cfg, project, "Treść.", stalls=0)
        self.assertNotIn("MUSI się znaleźć", notice)


class BriefNoticeCapTest(unittest.TestCase):
    """Znalezisko #3: brak limitu rozmiaru — pierwszy sync i diff muszą być ucięte."""

    def test_full_brief_dump_is_capped(self) -> None:
        from forge.orchestrate import _brief_amendment_notice, _BRIEF_NOTICE_CHAR_CAP
        project = tempfile.mkdtemp()
        cfg = Config(brief_path=os.path.join(project, "brief.md"))
        huge = "x" * (_BRIEF_NOTICE_CHAR_CAP * 3)

        notice = _brief_amendment_notice(cfg, project, huge)

        self.assertLess(len(notice), len(huge))
        self.assertIn("obcięto", notice)

    def test_diff_is_capped(self) -> None:
        from forge.orchestrate import (_brief_amendment_notice, _save_brief_snapshot,
                                       _BRIEF_NOTICE_CHAR_CAP)
        project = tempfile.mkdtemp()
        cfg = Config(brief_path=os.path.join(project, "brief.md"))
        _save_brief_snapshot(cfg, project, "linia bazowa")
        huge_new = "\n".join(f"nowa linia {i}" for i in range(_BRIEF_NOTICE_CHAR_CAP))

        notice = _brief_amendment_notice(cfg, project, huge_new)

        self.assertLess(len(notice), len(huge_new))
        self.assertIn("obcięto", notice)

    def test_short_brief_has_no_truncation_note(self) -> None:
        from forge.orchestrate import _brief_amendment_notice
        project = tempfile.mkdtemp()
        cfg = Config(brief_path=os.path.join(project, "brief.md"))
        self.assertNotIn("obcięto", _brief_amendment_notice(cfg, project, "krótki brief"))


class SnapshotTocTouTest(unittest.TestCase):
    """Znalezisko #2: snapshot musi zapisać dokładnie przekazany tekst, nie
    ponownie czytać pliku (który mógł się zmienić w trakcie run_planner)."""

    def test_save_snapshot_writes_given_text_not_disk_content(self) -> None:
        from forge.orchestrate import _save_brief_snapshot, _brief_amendment_notice
        project = tempfile.mkdtemp()
        brief = Path(project) / "brief.md"
        brief.write_text("TEKST NA DYSKU (zmieniony w trakcie run_planner)",
                         encoding="utf-8")
        cfg = Config(brief_path=str(brief))

        _save_brief_snapshot(cfg, project, "tekst pokazany planiście")

        # Kolejne porównanie z tym, co planista faktycznie widział, nie z dyskiem:
        notice = _brief_amendment_notice(cfg, project, "tekst pokazany planiście")
        self.assertEqual(notice, "")


class SnapshotPathIdentityTest(unittest.TestCase):
    """Znalezisko #4: snapshot musi być przypisany do konkretnego brief_path."""

    def test_different_brief_path_is_treated_as_first_sync(self) -> None:
        from forge.orchestrate import _brief_amendment_notice, _save_brief_snapshot
        project = tempfile.mkdtemp()
        cfg_a = Config(brief_path=os.path.join(project, "brief_a.md"))
        _save_brief_snapshot(cfg_a, project, "Treść A.")

        cfg_b = Config(brief_path=os.path.join(project, "brief_b.md"))
        notice = _brief_amendment_notice(cfg_b, project, "Zupełnie inna treść B.")

        self.assertIn("PIERWSZY RAZ", notice)
        self.assertIn("Zupełnie inna treść B.", notice)
        self.assertNotIn("Treść A.", notice)

    def test_same_brief_path_reuses_snapshot(self) -> None:
        from forge.orchestrate import _brief_amendment_notice, _save_brief_snapshot
        project = tempfile.mkdtemp()
        cfg = Config(brief_path=os.path.join(project, "brief.md"))
        _save_brief_snapshot(cfg, project, "Treść A.")

        self.assertEqual(_brief_amendment_notice(cfg, project, "Treść A."), "")


class PhasePlanBatchBriefSyncTest(unittest.TestCase):
    """Integracja: ``phase_plan_batch`` wstrzykuje notatkę, a snapshot i
    licznik stalli reagują na to, czy planista faktycznie coś zwrócił."""

    def _project_with_docs(self) -> str:
        tmp = tempfile.mkdtemp()
        (Path(tmp) / "docs").mkdir()
        (Path(tmp) / "docs" / "DESIGN.md").write_text("x", encoding="utf-8")
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

    def _ok_json(self) -> str:
        return '```json\n{"no_more_tasks": true, "tasks": []}\n```'

    def test_first_run_shows_full_brief_and_writes_snapshot(self) -> None:
        from forge.orchestrate import _brief_snapshot_paths
        project = self._project_with_docs()
        brief = Path(project) / "brief.md"
        brief.write_text("Wymaganie A.", encoding="utf-8")
        cfg = Config(brief_path=str(brief))

        prompt = self._run_plan_batch(project, cfg, State(), self._ok_json())

        self.assertIn("PIERWSZY RAZ", prompt)
        self.assertIn("Wymaganie A.", prompt)
        content_path, path_path = _brief_snapshot_paths(project, cfg)
        self.assertEqual(Path(content_path).read_text(encoding="utf-8"), "Wymaganie A.")
        self.assertEqual(Path(path_path).read_text(encoding="utf-8"), str(brief))

    def test_unchanged_brief_produces_no_notice_on_next_run(self) -> None:
        project = self._project_with_docs()
        brief = Path(project) / "brief.md"
        brief.write_text("Wymaganie A.", encoding="utf-8")
        cfg = Config(brief_path=str(brief))

        self._run_plan_batch(project, cfg, State(), self._ok_json())
        second_prompt = self._run_plan_batch(project, cfg, State(), self._ok_json())

        self.assertNotIn("BRIEF ZMIENIŁ SIĘ", second_prompt)
        self.assertNotIn("PIERWSZY RAZ", second_prompt)

    def test_changed_brief_between_runs_surfaces_diff_notice(self) -> None:
        project = self._project_with_docs()
        brief = Path(project) / "brief.md"
        brief.write_text("Wymaganie A.", encoding="utf-8")
        cfg = Config(brief_path=str(brief))

        self._run_plan_batch(project, cfg, State(), self._ok_json())
        brief.write_text("Wymaganie A.\nNowe wymaganie B (Godot).", encoding="utf-8")
        second_prompt = self._run_plan_batch(project, cfg, State(), self._ok_json())

        self.assertIn("BRIEF ZMIENIŁ SIĘ", second_prompt)
        self.assertIn("Nowe wymaganie B (Godot).", second_prompt)

    def test_unusable_planner_output_does_not_save_snapshot_and_bumps_stall(self) -> None:
        """Znalezisko #1: zepsuty JSON planisty nie może cicho 'skonsumować' zmiany briefu."""
        from forge.orchestrate import _brief_snapshot_paths
        project = self._project_with_docs()
        brief = Path(project) / "brief.md"
        brief.write_text("Wymaganie A.", encoding="utf-8")
        cfg = Config(brief_path=str(brief))
        state = State()

        garbage = "nie JSON w ogóle"
        self._run_plan_batch(project, cfg, state, garbage)

        content_path, _ = _brief_snapshot_paths(project, cfg)
        self.assertFalse(os.path.exists(content_path))
        self.assertEqual(state.brief_amend_stalls, 1)

    def test_unusable_output_keeps_showing_notice_next_run(self) -> None:
        project = self._project_with_docs()
        brief = Path(project) / "brief.md"
        brief.write_text("Wymaganie A.", encoding="utf-8")
        cfg = Config(brief_path=str(brief))
        state = State()
        garbage = "nie JSON w ogóle"

        self._run_plan_batch(project, cfg, state, garbage)
        second_prompt = self._run_plan_batch(project, cfg, state, garbage)

        self.assertIn("BRIEF ŚLEDZONY PIERWSZY RAZ", second_prompt)
        self.assertEqual(state.brief_amend_stalls, 2)

    def test_usable_output_saves_snapshot_and_resets_stall(self) -> None:
        from forge.orchestrate import _brief_snapshot_paths
        project = self._project_with_docs()
        tasks_dir = Path(project) / ".forge" / "tasks"
        tasks_dir.mkdir(parents=True)
        (tasks_dir / "task-001.md").write_text("# Zadanie 001", encoding="utf-8")
        brief = Path(project) / "brief.md"
        brief.write_text("Wymaganie A.", encoding="utf-8")
        cfg = Config(brief_path=str(brief))
        state = State(brief_amend_stalls=2)
        with_task = ('```json\n{"no_more_tasks": false, "tasks": ['
                     '{"id":"task-001","title":"Zrealizuj wymaganie A",'
                     '"file":".forge/tasks/task-001.md","criteria":[],'
                     '"test_globs":[],"code_globs":[]}'
                     ']}\n```')

        self._run_plan_batch(project, cfg, state, with_task)

        content_path, _ = _brief_snapshot_paths(project, cfg)
        self.assertTrue(os.path.exists(content_path))
        self.assertEqual(state.brief_amend_stalls, 0)


if __name__ == "__main__":
    unittest.main()
