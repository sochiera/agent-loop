"""Testy PLAN-4: uszczelnienie bramek i higiena kontekstu.

E1: czyste bramki (toolchain, anty-osłabianie v2, walidator mapy kryteriów).
E2: przewiązanie pętli (recenzent w świeżym kontekście, rotacja sesji,
    unieważnianie wsadu, blokada testów, dziennik).
E3: prompty i konfiguracja.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from forge.config import Config
from forge.state import State


def _init_repo(project: str) -> None:
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=project, check=True)


def _commit_all(project: str, msg: str = "commit") -> None:
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-qm", msg], cwd=project, check=True)


# =====================================================================
# E1.1: heurystyka toolchainu i jej relacja z heurystyką testów
# =====================================================================

class ToolchainHeuristicTest(unittest.TestCase):
    def test_known_toolchain_files_are_recognized(self) -> None:
        from forge.orchestrate import _looks_like_toolchain
        for path in ("package.json", "sub/package.json", "pytest.ini", "tox.ini",
                     "pyproject.toml", "setup.cfg", "Makefile", "CMakeLists.txt",
                     "jest.config.js", "vitest.config.ts", "build.gradle",
                     "pom.xml", "Cargo.toml", "noxfile.py"):
            self.assertTrue(_looks_like_toolchain(path), path)

    def test_ordinary_code_and_tests_are_not_toolchain(self) -> None:
        from forge.orchestrate import _looks_like_toolchain
        for path in ("src/app.py", "tests/test_a.py", "conftest.py",
                     "docs/DESIGN.md", "package-lock.json"):
            self.assertFalse(_looks_like_toolchain(path), path)

    def test_extra_globs_extend_heuristic(self) -> None:
        from forge.orchestrate import is_toolchain_path
        self.assertTrue(is_toolchain_path("scripts/run_tests.sh", ["scripts/**"]))
        self.assertFalse(is_toolchain_path("src/app.py", ["scripts/**"]))
        # Heurystyka działa też bez dodatkowych globów.
        self.assertTrue(is_toolchain_path("package.json", []))

    def test_effective_globs_merge_state_and_env_csv(self) -> None:
        from forge.orchestrate import effective_toolchain_globs
        cfg = Config(toolchain_globs_extra="ci/**, tools/runner.py")
        state = State(test_toolchain_globs=["scripts/test*.sh"])
        globs = effective_toolchain_globs(cfg, state)
        self.assertIn("scripts/test*.sh", globs)
        self.assertIn("ci/**", globs)
        self.assertIn("tools/runner.py", globs)


class TestHeuristicExcludesToolchainTest(unittest.TestCase):
    def test_pytest_ini_is_no_longer_a_test_file(self) -> None:
        # "pytest.ini" zawiera "test" w nazwie — dawna heurystyka uznawała go
        # za plik testowy (własność testera). Toolchain wygrywa.
        from forge.orchestrate import _looks_like_test
        self.assertFalse(_looks_like_test("pytest.ini"))
        self.assertTrue(_looks_like_test("tests/test_a.py"))
        self.assertTrue(_looks_like_test("conftest.py"))  # fixture'y = specyfikacja

    def test_tester_may_not_touch_toolchain_without_globs(self) -> None:
        from forge.orchestrate import tester_path_violations
        viol = tester_path_violations(["tests/test_a.py", "pytest.ini"], [])
        self.assertEqual(viol, ["pytest.ini"])

    def test_explicit_task_globs_still_win(self) -> None:
        # Jawny glob zadania to świadoma decyzja planisty — wygrywa z heurystyką.
        from forge.orchestrate import _is_test_path
        self.assertTrue(_is_test_path("pytest.ini", ["pytest.ini"]))
        self.assertFalse(_is_test_path("pytest.ini", ["tests/**"]))


class WeakeningCandidatesTest(unittest.TestCase):
    def test_candidates_are_changed_tests_and_toolchain(self) -> None:
        from forge.orchestrate import weakening_candidates
        changed = ["src/a.py", "tests/test_a.py", "package.json", "docs/D.md"]
        got = weakening_candidates(changed, ["tests/**"], [])
        self.assertEqual(got, ["package.json", "tests/test_a.py"])

    def test_extra_toolchain_globs_are_respected(self) -> None:
        from forge.orchestrate import weakening_candidates
        changed = ["scripts/run_tests.sh", "src/a.py"]
        self.assertEqual(weakening_candidates(changed, ["tests/**"], ["scripts/**"]),
                         ["scripts/run_tests.sh"])


# =====================================================================
# E1.2: anty-osłabianie v2 — baseline + kopiowanie toolchainu
# =====================================================================

class AntiWeakeningToolchainTest(unittest.TestCase):
    """HEAD: uczciwy runner + zielona suita. Nerf runnera musi być złapany."""

    RUNNER = ("import subprocess, sys\n"
              "sys.exit(subprocess.call("
              "[sys.executable, '-m', 'unittest', 'discover', '-q']))\n")

    def _repo(self, project: str) -> None:
        _init_repo(project)
        Path(project, "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        Path(project, "run_tests.py").write_text(self.RUNNER, encoding="utf-8")
        Path(project, "test_base.py").write_text(
            "import unittest\nclass B(unittest.TestCase):\n"
            "    def test_base(self):\n        self.assertTrue(True)\n",
            encoding="utf-8")
        _commit_all(project, "przed cyklem")
        # Praca cyklu (niezacommitowana): koder podbił f(), tester dodał test.
        Path(project, "mod.py").write_text("def f():\n    return 2\n", encoding="utf-8")
        Path(project, "test_mod.py").write_text(
            "import unittest, mod\nclass T(unittest.TestCase):\n"
            "    def test_f(self):\n        self.assertEqual(mod.f(), 2)\n",
            encoding="utf-8")

    def test_nerfed_runner_is_flagged(self) -> None:
        from forge.orchestrate import anti_weakening_ok
        with tempfile.TemporaryDirectory() as project:
            self._repo(project)
            # Nerf: runner przestaje uruchamiać testy — bramka "zielona" za darmo.
            Path(project, "run_tests.py").write_text("import sys\nsys.exit(0)\n",
                                                     encoding="utf-8")
            self.assertFalse(anti_weakening_ok(
                project, ["test_mod.py", "run_tests.py"], "",
                "python3 run_tests.py", 60))

    def test_honest_cycle_with_untouched_runner_passes(self) -> None:
        from forge.orchestrate import anti_weakening_ok
        with tempfile.TemporaryDirectory() as project:
            self._repo(project)
            self.assertTrue(anti_weakening_ok(
                project, ["test_mod.py"], "", "python3 run_tests.py", 60))

    def test_red_baseline_skips_check_fail_open(self) -> None:
        # Suita na HEAD czerwona ze względów środowiskowych → pomiar
        # niemiarodajny; check pomija się jawnie (fail-open z logiem),
        # zamiast po cichu przepuszczać dowolny wynik.
        from forge.orchestrate import anti_weakening_ok
        with tempfile.TemporaryDirectory() as project:
            _init_repo(project)
            Path(project, "test_base.py").write_text(
                "import unittest\nclass B(unittest.TestCase):\n"
                "    def test_base(self):\n        self.assertTrue(False)\n",
                encoding="utf-8")
            _commit_all(project, "czerwony HEAD")
            Path(project, "test_mod.py").write_text(
                "import unittest\nclass T(unittest.TestCase):\n"
                "    def test_f(self):\n        self.assertTrue(True)\n",
                encoding="utf-8")
            self.assertTrue(anti_weakening_ok(
                project, ["test_mod.py"], "",
                "python3 -m unittest discover -q", 60))


# =====================================================================
# E1.3: walidator mapy kryteriów (koniec samocertyfikacji DONE)
# =====================================================================

class ValidateCriteriaMapTest(unittest.TestCase):
    def _project(self, tmp: str) -> str:
        tests = Path(tmp, "tests")
        tests.mkdir()
        (tests / "test_a.py").write_text(
            "def test_move():\n    assert True\n", encoding="utf-8")
        return tmp

    def _validate(self, criteria, cmap, project, globs=("tests/**",)):
        from forge.orchestrate import validate_criteria_map
        return validate_criteria_map(criteria, cmap, project, list(globs))

    def test_valid_map_returns_no_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(tmp)
            errors = self._validate(
                ["k1", "k2"],
                [{"criterion": "k1", "test": "tests/test_a.py::test_move",
                  "status": "covered"},
                 {"criterion": "k2", "status": "justified",
                  "why": "kryterium niesprawdzalne testem jednostkowym"}],
                project)
            self.assertEqual(errors, [])

    def test_missing_criterion_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(tmp)
            errors = self._validate(
                ["k1", "k2"],
                [{"criterion": "k1", "test": "tests/test_a.py", "status": "covered"}],
                project)
            self.assertTrue(any("k2" in e for e in errors))

    def test_covered_requires_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(tmp)
            errors = self._validate(
                ["k1"],
                [{"criterion": "k1", "test": "tests/test_ghost.py::test_x",
                  "status": "covered"}],
                project)
            self.assertTrue(any("test_ghost.py" in e for e in errors))

    def test_covered_requires_test_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(tmp)
            Path(project, "src").mkdir()
            Path(project, "src", "a.py").write_text("x = 1\n", encoding="utf-8")
            errors = self._validate(
                ["k1"],
                [{"criterion": "k1", "test": "src/a.py", "status": "covered"}],
                project)
            self.assertTrue(any("src/a.py" in e for e in errors))

    def test_covered_test_name_must_exist_in_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(tmp)
            errors = self._validate(
                ["k1"],
                [{"criterion": "k1", "test": "tests/test_a.py::test_wymyslony",
                  "status": "covered"}],
                project)
            self.assertTrue(any("test_wymyslony" in e for e in errors))

    def test_justified_requires_substantive_why(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(tmp)
            for why in ("", "bo tak"):
                errors = self._validate(
                    ["k1"], [{"criterion": "k1", "status": "justified", "why": why}],
                    project)
                self.assertTrue(errors, f"why={why!r} nie powinno przejść")

    def test_duplicates_and_invented_criteria_do_not_substitute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(tmp)
            dup = [{"criterion": "k1", "test": "tests/test_a.py", "status": "covered"},
                   {"criterion": "k1", "test": "tests/test_a.py", "status": "covered"}]
            self.assertTrue(self._validate(["k1", "k2"], dup, project))
            invented = [{"criterion": "k1", "test": "tests/test_a.py", "status": "covered"},
                        {"criterion": "ZMYŚLONE", "test": "tests/test_a.py",
                         "status": "covered"}]
            self.assertTrue(self._validate(["k1", "k2"], invented, project))

    def test_criteria_matching_tolerates_case_and_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(tmp)
            errors = self._validate(
                ["Gracz może  ruszyć jednostkę"],
                [{"criterion": "gracz może ruszyć jednostkę",
                  "test": "tests/test_a.py::test_move", "status": "covered"}],
                project)
            self.assertEqual(errors, [])

    def test_no_criteria_is_trivially_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._validate([], [], tmp), [])


if __name__ == "__main__":
    unittest.main()
