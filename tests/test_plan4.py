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

    def test_covered_accepts_class_qualified_pytest_node_id(self) -> None:
        # Standardowy node id pytest/unittest dla testu w klasie:
        # "plik.py::Klasa::metoda" — cały string po "::" nie występuje NIGDY
        # dosłownie w pliku (plik ma "class Klasa" i "def metoda" osobno).
        # Walidator musi sprawdzać segmenty, nie cały ogon jako jeden literał.
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(tmp)
            Path(project, "tests", "test_cls.py").write_text(
                "import unittest\n"
                "class TestBar(unittest.TestCase):\n"
                "    def test_baz(self):\n"
                "        self.assertTrue(True)\n",
                encoding="utf-8")
            errors = self._validate(
                ["k1"],
                [{"criterion": "k1", "test": "tests/test_cls.py::TestBar::test_baz",
                  "status": "covered"}],
                project)
            self.assertEqual(errors, [])

    def test_covered_class_qualified_still_requires_each_segment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(tmp)
            Path(project, "tests", "test_cls.py").write_text(
                "import unittest\n"
                "class TestBar(unittest.TestCase):\n"
                "    def test_baz(self):\n"
                "        self.assertTrue(True)\n",
                encoding="utf-8")
            errors = self._validate(
                ["k1"],
                [{"criterion": "k1",
                  "test": "tests/test_cls.py::TestBar::test_wymyslona",
                  "status": "covered"}],
                project)
            self.assertTrue(any("test_wymyslona" in e for e in errors))

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

    def test_no_criteria_is_rejected_as_empty_canon(self) -> None:
        # PLAN-5: pusty kanon ≠ „wszystko pokryte" (wcześniej trivially valid).
        with tempfile.TemporaryDirectory() as tmp:
            errors = self._validate([], [], tmp)
            self.assertTrue(errors)
            self.assertTrue(any("brak kryteri" in e.lower() or "kanon" in e.lower()
                                for e in errors))


# =====================================================================
# E2.1: rola recenzenta w Config
# =====================================================================

class ReviewerRoleConfigTest(unittest.TestCase):
    def test_blank_reviewer_agent_inherits_tester_role(self) -> None:
        # reviewer_agent="" (jawnie, bo domyślny Config() to teraz opencode)
        # nadal dziedziczy w całości rolę testera — mechanizm inheritance
        # istnieje niezależnie od tego, co jest domyślnym agentem.
        cfg = Config(tester_agent="codex", tester_model="m1", tester_effort="high",
                     reviewer_agent="", reviewer_model="")
        self.assertEqual(cfg.role("reviewer"), cfg.role("tester"))

    def test_explicit_reviewer_agent_wins(self) -> None:
        cfg = Config(reviewer_agent="claude", reviewer_model="sonnet",
                     reviewer_effort="medium")
        self.assertEqual(cfg.role("reviewer"), ("claude", "sonnet", "medium"))

    def test_agents_in_use_includes_reviewer(self) -> None:
        cfg = Config(reviewer_agent="grok")
        self.assertIn("grok", cfg.agents_in_use())

    def test_reviewer_model_override_applies_without_explicit_reviewer_agent(self) -> None:
        # Dokumentowane w README pokrętło FORGE_REVIEWER_MODEL musi działać
        # także gdy FORGE_REVIEWER_AGENT nie jest ustawiony (default = agent
        # testera) — dziś jest po cichu ignorowane, bo role('reviewer') zwraca
        # role('tester') w całości zamiast pozwolić na częściowe nadpisanie.
        cfg = Config(tester_agent="codex", tester_model="m1", tester_effort="high",
                     reviewer_agent="", reviewer_model="o3")
        self.assertEqual(cfg.role("reviewer"), ("codex", "o3", "high"))

    def test_reviewer_effort_override_applies_without_explicit_reviewer_agent(self) -> None:
        cfg = Config(tester_agent="codex", tester_model="m1", tester_effort="high",
                     reviewer_agent="", reviewer_model="", reviewer_effort="low")
        self.assertEqual(cfg.role("reviewer"), ("codex", "m1", "low"))


# =====================================================================
# E2.2: recenzja w świeżym kontekście (bez sesji testera, bez dziennika)
# =====================================================================

class ReviewFreshContextTest(unittest.TestCase):
    @patch("forge.orchestrate._session_call")
    @patch("forge.orchestrate.run_agent_session",
           return_value=('```json\n{"verdict":"approve","notes":[]}\n```', "discarded-sid"))
    def test_review_uses_fresh_agent_with_mechanical_context(
        self, run_agent_session: Mock, session_call: Mock
    ) -> None:
        from forge.orchestrate import _run_review_loop
        state = State(phase="review", current_task={"file": "f"},
                      current_task_title="T", test_cmd="pytest",
                      task_start_tag="forge/task-001-start",
                      justified_criteria=[{"criterion": "k2",
                                           "why": "nie da się zautomatyzować bo X"}])
        with tempfile.TemporaryDirectory() as project:
            ok = _run_review_loop(Config(), project, state,
                                  lambda ph: "/tmp/log", gate_green=True)
        self.assertTrue(ok)
        session_call.assert_not_called()          # recenzent NIE dziedziczy sesji
        run_agent_session.assert_called_once()
        # session_id=None → recenzent zawsze startuje bez pamięci poprzednich
        # rund; a run_agent_session (nie gołe run_agent) jest tym, co dla
        # codeksa zachowuje log_usage (run_codex_session go woła, run_codex nie).
        self.assertIsNone(run_agent_session.call_args.kwargs.get("session_id"))
        prompt = run_agent_session.call_args.args[1]
        self.assertIn("forge/task-001-start", prompt)   # jawny ref do diffu
        self.assertIn("k2", prompt)                     # justified do rozstrzygnięcia
        self.assertIn("nie da się zautomatyzować", prompt)
        # Zwrócone id sesji recenzenta NIE jest utrwalane — świeży kontekst
        # w KAŻDEJ rundzie, nawet gdy agentem recenzenta jest codex.
        self.assertEqual(state.tester_session, "")
        self.assertEqual(state.coder_session, "")


# =====================================================================
# E2.3: przewiązanie mikro-pętli (rotacja, kandydaci mechaniczni,
#        toolchain w kroku bez testu, lock testów, dziennik)
# =====================================================================

class MicroLoopWiringBase(unittest.TestCase):
    def _init_repo(self, project: str, extra: dict | None = None) -> None:
        _init_repo(project)
        Path(project, "seed.txt").write_text("seed", encoding="utf-8")
        for rel, body in (extra or {}).items():
            path = Path(project, rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        _commit_all(project, "init")
        os.makedirs(os.path.join(project, "tests"), exist_ok=True)
        os.makedirs(os.path.join(project, "src"), exist_ok=True)

    def _state(self, **kw) -> State:
        base = dict(test_cmd="pytest", build_cmd="", current_task_title="Ruch",
                    current_task={"id": "task-001", "title": "Ruch",
                                  "file": ".forge/tasks/task-001.md",
                                  "criteria": ["c1"], "test_globs": ["tests/**"],
                                  "code_globs": ["src/**"]},
                    phase="micro", micro_sub="test")
        base.update(kw)
        return State(**base)


class SessionRotationTest(MicroLoopWiringBase):
    def test_sessions_rotate_after_k_cycles_and_fresh_call_gets_journal(self) -> None:
        from forge.orchestrate import _run_micro_loop
        with tempfile.TemporaryDirectory() as project:
            self._init_repo(project)
            cfg = Config(max_micro_cycles=5, max_green_retries=0,
                         session_rotate_cycles=1, git_push=False,
                         tester_agent="codex", coder_agent="codex")
            state = self._state()
            seen = []

            def fake(name, prompt, cfg_, proj, log, *, session_id=None,
                     model=None, effort=None):
                seen.append((session_id, prompt))
                n = len(seen)
                if n == 1:
                    Path(proj, "tests", "test_a.py").write_text(
                        "def test_a():\n    assert move()\n", encoding="utf-8")
                    return '```json\n{"action":"wrote_test"}\n```', "sess-t"
                if n == 2:
                    Path(proj, "src", "a.py").write_text(
                        "def move():\n    return True\n", encoding="utf-8")
                    return '```json\n{"notes":"ok"}\n```', "sess-c"
                return ('```json\n{"action":"done","criteria_map":['
                        '{"criterion":"c1","test":"tests/test_a.py::test_a",'
                        '"status":"covered"}]}\n```'), "sess-t2"

            gates = iter([(False, "red"), (True, ""), (True, "")])
            with patch("forge.orchestrate.run_agent_session", side_effect=fake), \
                 patch("forge.orchestrate.run_gate",
                       side_effect=lambda *a, **k: next(gates)):
                out = _run_micro_loop(cfg, project, state,
                                      lambda ph: os.path.join(project, "log"))

            self.assertEqual(out, "done")
            # Po cyklu 1 rotacja wyzerowała sesje → wywołanie DONE startuje
            # świeżą sesję (session_id None) z dziennikiem zadania.
            self.assertIsNone(seen[2][0])
            self.assertIn("DZIENNIKA", seen[2][1])


class MechanicalWeakeningWiringTest(MicroLoopWiringBase):
    PKG = '{"scripts": {"test": "uczciwy runner"}}\n'

    def test_undeclared_toolchain_nerf_is_reverted(self) -> None:
        from forge.orchestrate import _run_micro_loop
        with tempfile.TemporaryDirectory() as project:
            self._init_repo(project, {"package.json": self.PKG})
            cfg = Config(max_micro_cycles=1, max_green_retries=0, git_push=False,
                         lock_tests=False, tester_agent="codex", coder_agent="codex")
            state = self._state()
            calls = {"n": 0}

            def fake(name, prompt, cfg_, proj, log, *, session_id=None,
                     model=None, effort=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    Path(proj, "tests", "test_a.py").write_text(
                        "def test_a():\n    assert move()\n", encoding="utf-8")
                    return '```json\n{"action":"wrote_test"}\n```', "s-t"
                Path(proj, "src", "a.py").write_text("def move():\n    return True\n",
                                                     encoding="utf-8")
                Path(proj, "package.json").write_text('{"scripts": {"test": "true"}}\n',
                                                      encoding="utf-8")  # NERF
                return '```json\n{"notes":"zielono"}\n```', "s-c"

            gates = iter([(False, "red"), (True, ""), (True, "")])
            weakening = Mock(return_value=False)  # pomiar: osłabiono
            with patch("forge.orchestrate.run_agent_session", side_effect=fake), \
                 patch("forge.orchestrate.run_gate",
                       side_effect=lambda *a, **k: next(gates)), \
                 patch("forge.orchestrate.anti_weakening_ok", weakening):
                _run_micro_loop(cfg, project, state,
                                lambda ph: os.path.join(project, "log"))

            weakening.assert_called_once()
            files = weakening.call_args.args[1]
            self.assertIn("package.json", files)        # toolchain w pomiarze
            self.assertIn("tests/test_a.py", files)     # testy cyklu dla sensu pomiaru
            # Nerf wycofany do wersji z HEAD.
            self.assertEqual(Path(project, "package.json").read_text(encoding="utf-8"),
                             self.PKG)

    def test_untouched_tests_and_toolchain_skip_measurement(self) -> None:
        from forge.orchestrate import _run_micro_loop
        with tempfile.TemporaryDirectory() as project:
            self._init_repo(project)
            cfg = Config(max_micro_cycles=1, max_green_retries=0, git_push=False,
                         lock_tests=False, tester_agent="codex", coder_agent="codex")
            state = self._state()
            calls = {"n": 0}

            def fake(name, prompt, cfg_, proj, log, *, session_id=None,
                     model=None, effort=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    Path(proj, "tests", "test_a.py").write_text(
                        "def test_a():\n    assert move()\n", encoding="utf-8")
                    return '```json\n{"action":"wrote_test"}\n```', "s-t"
                Path(proj, "src", "a.py").write_text("def move():\n    return True\n",
                                                     encoding="utf-8")
                return '```json\n{"notes":"ok"}\n```', "s-c"

            gates = iter([(False, "red"), (True, "")])
            weakening = Mock(return_value=True)
            with patch("forge.orchestrate.run_agent_session", side_effect=fake), \
                 patch("forge.orchestrate.run_gate",
                       side_effect=lambda *a, **k: next(gates)), \
                 patch("forge.orchestrate.anti_weakening_ok", weakening):
                _run_micro_loop(cfg, project, state,
                                lambda ph: os.path.join(project, "log"))

            weakening.assert_not_called()  # nic podejrzanego → zero kosztu


class NoTestToolchainRevertTest(MicroLoopWiringBase):
    PKG = '{"scripts": {"test": "uczciwy runner"}}\n'

    def test_toolchain_change_outside_code_globs_is_reverted(self) -> None:
        from forge.orchestrate import _run_micro_loop
        with tempfile.TemporaryDirectory() as project:
            self._init_repo(project, {"package.json": self.PKG})
            cfg = Config(max_micro_cycles=1, max_green_retries=0, git_push=False,
                         lock_tests=False, tester_agent="codex", coder_agent="codex")
            state = self._state()
            calls = {"n": 0}

            def fake(name, prompt, cfg_, proj, log, *, session_id=None,
                     model=None, effort=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    return '```json\n{"action":"no_test","reason":"strukturalny"}\n```', "s-t"
                Path(proj, "src", "a.py").write_text("x = 1\n", encoding="utf-8")
                Path(proj, "package.json").write_text('{"scripts": {"test": "true"}}\n',
                                                      encoding="utf-8")  # NERF
                return '```json\n{"notes":"krok bez testu"}\n```', "s-c"

            gates = iter([(True, ""), (True, "")])
            with patch("forge.orchestrate.run_agent_session", side_effect=fake), \
                 patch("forge.orchestrate.run_gate",
                       side_effect=lambda *a, **k: next(gates)):
                _run_micro_loop(cfg, project, state,
                                lambda ph: os.path.join(project, "log"))

            # W kroku bez testu nie ma mechanicznego pomiaru → toolchain spoza
            # code_globs wraca do wersji z HEAD; kod kodera zostaje.
            self.assertEqual(Path(project, "package.json").read_text(encoding="utf-8"),
                             self.PKG)
            self.assertTrue(Path(project, "src", "a.py").exists())

    def test_empty_code_globs_means_nothing_enforced_not_everything_forbidden(self) -> None:
        # Konwencja całej reszty bramek (np. role_paths_ok): PUSTE globy = brak
        # deklaracji = nic do wyegzekwowania — nie "zakaż wszystkiego". Zadanie
        # bez zadeklarowanych code_globs (planista pominął pole) musi wciąż
        # pozwalać koderowi dodać zależność w kroku bez testu — prompt kodera
        # jawnie to dopuszcza ("Dodanie zależności jest OK").
        from forge.orchestrate import _run_micro_loop
        with tempfile.TemporaryDirectory() as project:
            self._init_repo(project, {"package.json": self.PKG})
            cfg = Config(max_micro_cycles=1, max_green_retries=0, git_push=False,
                         lock_tests=False, tester_agent="codex", coder_agent="codex")
            task = dict(self._state().current_task)
            task["code_globs"] = []  # planista nie zadeklarował — nie "zakaż"
            state = self._state(current_task=task)
            calls = {"n": 0}
            NEW_PKG = '{"scripts": {"test": "uczciwy runner"}, "dependencies": {"x": "1.0"}}\n'

            def fake(name, prompt, cfg_, proj, log, *, session_id=None,
                     model=None, effort=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    return '```json\n{"action":"no_test","reason":"strukturalny"}\n```', "s-t"
                Path(proj, "src", "a.py").write_text("x = 1\n", encoding="utf-8")
                Path(proj, "package.json").write_text(NEW_PKG, encoding="utf-8")  # legalna zależność
                return '```json\n{"notes":"dependency add"}\n```', "s-c"

            gates = iter([(True, ""), (True, "")])
            with patch("forge.orchestrate.run_agent_session", side_effect=fake), \
                 patch("forge.orchestrate.run_gate",
                       side_effect=lambda *a, **k: next(gates)):
                _run_micro_loop(cfg, project, state,
                                lambda ph: os.path.join(project, "log"))

            self.assertEqual(Path(project, "package.json").read_text(encoding="utf-8"),
                             NEW_PKG)  # NIE wycofane


class LockTestsDuringCoderTest(MicroLoopWiringBase):
    def test_cycle_tests_are_readonly_for_coder_and_restored_after(self) -> None:
        from forge.orchestrate import _run_micro_loop
        with tempfile.TemporaryDirectory() as project:
            self._init_repo(project)
            cfg = Config(max_micro_cycles=5, max_green_retries=0, git_push=False,
                         lock_tests=True, tester_agent="codex", coder_agent="codex")
            state = self._state()
            seen = {}
            calls = {"n": 0}

            def fake(name, prompt, cfg_, proj, log, *, session_id=None,
                     model=None, effort=None):
                calls["n"] += 1
                test_path = os.path.join(proj, "tests", "test_a.py")
                if calls["n"] == 1:
                    Path(test_path).write_text("def test_a():\n    assert move()\n",
                                               encoding="utf-8")
                    return '```json\n{"action":"wrote_test"}\n```', "s-t"
                # Bity trybu, nie os.access — root pisze mimo chmod a-w,
                # a mechanizm ma zdejmować bity zapisu.
                if calls["n"] == 2:
                    seen["coder_can_write"] = bool(os.stat(test_path).st_mode & 0o222)
                    Path(proj, "src", "a.py").write_text(
                        "def move():\n    return True\n", encoding="utf-8")
                    return '```json\n{"notes":"ok"}\n```', "s-c"
                seen["after_can_write"] = bool(os.stat(test_path).st_mode & 0o200)
                return ('```json\n{"action":"done","criteria_map":['
                        '{"criterion":"c1","test":"tests/test_a.py::test_a",'
                        '"status":"covered"}]}\n```'), "s-t"

            gates = iter([(False, "red"), (True, ""), (True, "")])
            with patch("forge.orchestrate.run_agent_session", side_effect=fake), \
                 patch("forge.orchestrate.run_gate",
                       side_effect=lambda *a, **k: next(gates)):
                out = _run_micro_loop(cfg, project, state,
                                      lambda ph: os.path.join(project, "log"))

            self.assertEqual(out, "done")
            self.assertFalse(seen["coder_can_write"])   # tura kodera: read-only
            self.assertTrue(seen["after_can_write"])    # po turze: przywrócone


class JournalEnrichmentTest(MicroLoopWiringBase):
    def test_cycle_commit_appends_changed_files_to_journal(self) -> None:
        from forge.orchestrate import _journal_path, _run_micro_loop, journal_reset
        with tempfile.TemporaryDirectory() as project:
            self._init_repo(project)
            cfg = Config(max_micro_cycles=1, max_green_retries=0, git_push=False,
                         lock_tests=False, tester_agent="codex", coder_agent="codex")
            state = self._state()
            journal_reset(project, cfg, "Ruch")
            calls = {"n": 0}

            def fake(name, prompt, cfg_, proj, log, *, session_id=None,
                     model=None, effort=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    Path(proj, "tests", "test_a.py").write_text(
                        "def test_a():\n    assert 1\n", encoding="utf-8")
                    return '```json\n{"action":"wrote_test"}\n```', "s-t"
                Path(proj, "src", "a.py").write_text("x = 1\n", encoding="utf-8")
                return '```json\n{"notes":"ok"}\n```', "s-c"

            gates = iter([(False, "red"), (True, "")])
            with patch("forge.orchestrate.run_agent_session", side_effect=fake), \
                 patch("forge.orchestrate.run_gate",
                       side_effect=lambda *a, **k: next(gates)):
                _run_micro_loop(cfg, project, state,
                                lambda ph: os.path.join(project, "log"))

            journal = Path(_journal_path(project, cfg)).read_text(encoding="utf-8")
            self.assertIn("pliki:", journal)
            self.assertIn("src/a.py", journal)


# =====================================================================
# E2.4: dziennik przy świeżej sesji w toku zadania + sufit z konfiguracji
# =====================================================================

class SessionCallJournalMidTaskTest(unittest.TestCase):
    def _call(self, state: State) -> tuple[str, object]:
        from forge.orchestrate import _session_call, journal_append, journal_reset
        with tempfile.TemporaryDirectory() as project:
            cfg = Config(tester_agent="codex")  # codex → agent wznawialny
            journal_reset(project, cfg, "Ruch")
            journal_append(project, cfg, "cykl 1, tester: wrote_test (ruch po heksach)")
            seen = {}

            def fake(name, prompt, cfg_, proj, log, *, session_id=None,
                     model=None, effort=None):
                seen["prompt"], seen["sid"] = prompt, session_id
                return "ok", "nowy-id"

            with patch("forge.orchestrate.run_agent_session", side_effect=fake):
                _session_call(cfg, project, state, "tester", "PROMPT", "/tmp/log")
            return seen["prompt"], seen["sid"]

    def test_fresh_session_mid_task_gets_journal(self) -> None:
        prompt, sid = self._call(State(micro_cycle=2))
        self.assertIsNone(sid)
        self.assertIn("DZIENNIKA", prompt)
        self.assertIn("ruch po heksach", prompt)

    def test_task_start_stays_journal_free(self) -> None:
        prompt, _sid = self._call(State(micro_cycle=0, fix_attempt=0))
        self.assertEqual(prompt, "PROMPT")


class JournalTailConfigTest(unittest.TestCase):
    def test_tail_limit_comes_from_config(self) -> None:
        from forge.orchestrate import journal_append, journal_reset, journal_tail
        with tempfile.TemporaryDirectory() as project:
            cfg = Config(journal_tail_chars=120)
            journal_reset(project, cfg, "T")
            for i in range(30):
                journal_append(project, cfg, f"wpis numer {i:02d} o stałej długości")
            tail = journal_tail(project, cfg)
            self.assertLessEqual(len(tail), 120)
            self.assertIn("wpis numer 29", tail)   # ogon, nie początek
            # Wyższy sufit → więcej kontekstu.
            self.assertGreater(len(journal_tail(project, Config(journal_tail_chars=2000))),
                               len(tail))


# =====================================================================
# E2.5: porażka zadania unieważnia resztę wsadu
# =====================================================================

class FailTaskReplanTest(unittest.TestCase):
    def _fail(self, cfg: Config) -> tuple[State, str]:
        from forge.orchestrate import _fail_task
        with tempfile.TemporaryDirectory() as project:
            state = State(current_task_title="T", task_start_tag="",
                          justified_criteria=[{"criterion": "k", "why": "w" * 20}],
                          task_queue=[{"id": "task-002", "title": "Nast", "file": "f"}])
            _fail_task(cfg, project, state, 1, "mikro-TDD nieukończone")
            failures = Path(project, cfg.runtime_dir, "failures.md")
            body = failures.read_text(encoding="utf-8") if failures.exists() else ""
            return state, body

    def test_failure_drops_batch_and_records_it(self) -> None:
        state, failures = self._fail(Config(replan_on_failure=True))
        self.assertEqual(state.task_queue, [])
        self.assertIn("task-002", failures)
        self.assertEqual(state.justified_criteria, [])  # sprzątnięte z zadaniem

    def test_opt_out_keeps_queue(self) -> None:
        state, _failures = self._fail(Config(replan_on_failure=False))
        self.assertEqual(len(state.task_queue), 1)


# =====================================================================
# E3: prompty i deklaracja toolchainu w bootstrapie
# =====================================================================

class PromptsPlan4Test(unittest.TestCase):
    def test_bootstrap_prompt_asks_for_toolchain_globs(self) -> None:
        import forge.prompts as p
        boot = p.bootstrap_prompt("brief")
        self.assertIn("test_toolchain_globs", boot)

    def test_write_test_prompt_carries_done_rejection_feedback(self) -> None:
        import forge.prompts as p
        plain = p.write_test_prompt("f.md", "pytest")
        self.assertNotIn("ODRZUCONA", plain)
        with_reasons = p.write_test_prompt(
            "f.md", "pytest",
            reject_reasons=["kryterium bez ważnego pokrycia: 'k2'"])
        self.assertIn("ODRZUCONA", with_reasons)
        self.assertIn("k2", with_reasons)

    def test_coder_prompt_forbids_toolchain_nerf(self) -> None:
        import forge.prompts as p
        prompt = p.code_and_refactor_prompt("f.md", "pytest", False)
        self.assertIn("toolchain", prompt.lower())

    def test_review_prompt_surfaces_mechanical_context(self) -> None:
        import forge.prompts as p
        prompt = p.review_task_prompt(
            "f.md", "pytest", start_tag="forge/task-007-start",
            changed=["src/a.py", "package.json"],
            toolchain_changes=["package.json"],
            justified=[{"criterion": "k9", "why": "wymaga ręcznej inspekcji UI"}])
        self.assertIn("git diff forge/task-007-start", prompt)
        self.assertIn("package.json", prompt)
        self.assertIn("KONFIGURACJĘ URUCHAMIANIA TESTÓW", prompt)
        self.assertIn("k9", prompt)
        self.assertIn("wymaga ręcznej inspekcji UI", prompt)


class BootstrapToolchainGlobsTest(unittest.TestCase):
    @patch("forge.orchestrate.commit_all")
    @patch("forge.orchestrate.build_then_test", return_value=True)
    def test_bootstrap_stores_declared_toolchain_globs(self, _bt: Mock,
                                                       _commit: Mock) -> None:
        from forge.orchestrate import phase_bootstrap
        with tempfile.TemporaryDirectory() as project:
            brief = Path(project) / "brief.md"
            brief.write_text("gra", encoding="utf-8")
            cfg = Config(brief_path=str(brief), agent_timeout_s=5)
            state = State()
            payload = ('{"kind":"app","stack":"Py","test_cmd":"pytest",'
                       '"build_cmd":"","run_cmd":"x",'
                       '"test_toolchain_globs":["scripts/test*.sh", "  ", 42]}')
            with patch("forge.orchestrate.run_planner", return_value=payload):
                phase_bootstrap(cfg, project, state, lambda ph: "/tmp/log")
            # Puste/nie-stringi odfiltrowane, deklaracja zapisana w State.
            self.assertEqual(state.test_toolchain_globs, ["scripts/test*.sh", "42"])

    @patch("forge.orchestrate.commit_all")
    @patch("forge.orchestrate.build_then_test", return_value=True)
    def test_missing_declaration_defaults_to_empty(self, _bt: Mock,
                                                   _commit: Mock) -> None:
        from forge.orchestrate import phase_bootstrap
        with tempfile.TemporaryDirectory() as project:
            brief = Path(project) / "brief.md"
            brief.write_text("gra", encoding="utf-8")
            cfg = Config(brief_path=str(brief), agent_timeout_s=5)
            state = State()
            payload = ('{"kind":"app","stack":"Py","test_cmd":"pytest",'
                       '"build_cmd":"","run_cmd":"x"}')
            with patch("forge.orchestrate.run_planner", return_value=payload):
                phase_bootstrap(cfg, project, state, lambda ph: "/tmp/log")
            self.assertEqual(state.test_toolchain_globs, [])


if __name__ == "__main__":
    unittest.main()
