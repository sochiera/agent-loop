from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from forge.agents import extract_codex_usage, extract_session_id
from forge.config import Config
from forge.orchestrate import (_next_task_index, _path_matches, _run_micro_loop,
                               _task_iteration, coder_test_violations,
                               criteria_fully_mapped, phase_plan_batch,
                               red_gate_ok, role_paths_ok, run_gate)
from forge.state import State


class PureGateTest(unittest.TestCase):
    def test_glob_matches_across_and_within_segments(self) -> None:
        self.assertTrue(_path_matches("tests/test_a.py", "tests/**"))
        self.assertTrue(_path_matches("tests/deep/nested/test_a.py", "tests/**"))
        self.assertTrue(_path_matches("src/a.py", "src/*.py"))
        self.assertFalse(_path_matches("src/sub/a.py", "src/*.py"))  # * nie przez /
        self.assertTrue(_path_matches("game/main.py", "game/main.py"))  # dokładny
        self.assertFalse(_path_matches("src/a.py", "tests/**"))

    def test_red_gate_requires_failing_suite(self) -> None:
        self.assertTrue(red_gate_ok(False))   # czerwona bramka = test failuje = dobrze
        self.assertFalse(red_gate_ok(True))   # test przeszedł od razu = źle

    def test_role_paths_flags_files_outside_allowed_globs(self) -> None:
        ok, offending = role_paths_ok(["tests/test_a.py", "src/leak.py"], ["tests/**"])
        self.assertFalse(ok)
        self.assertEqual(offending, ["src/leak.py"])
        self.assertEqual(role_paths_ok(["anything"], []), (True, []))  # brak globów → nie egzekwuj

    def test_coder_test_violations_respects_cycle_and_declarations(self) -> None:
        changed = ["src/a.py", "tests/test_a.py", "tests/test_b.py"]
        globs = ["tests/**"]
        # test_a należy do bieżącego cyklu (tester), test_b zmieniony przez kodera bez deklaracji
        viol = coder_test_violations(changed, globs, ["tests/test_a.py"], [])
        self.assertEqual(viol, ["tests/test_b.py"])
        # ten sam plik zadeklarowany → brak naruszenia
        self.assertEqual(
            coder_test_violations(changed, globs, ["tests/test_a.py"], ["tests/test_b.py"]),
            [],
        )

    def test_criteria_mapping_completeness(self) -> None:
        criteria = ["kryt1", "kryt2"]
        full = [{"criterion": "kryt1", "test": "t::a", "status": "covered"},
                {"criterion": "kryt2", "status": "justified"}]
        self.assertTrue(criteria_fully_mapped(criteria, full))
        partial = [{"criterion": "kryt1", "test": "t::a", "status": "covered"}]
        self.assertFalse(criteria_fully_mapped(criteria, partial))
        self.assertTrue(criteria_fully_mapped([], []))  # brak kryteriów → trywialnie spełnione
        # "covered" bez testu nie liczy się jako pokrycie
        self.assertFalse(criteria_fully_mapped(["k"], [{"criterion": "k", "status": "covered"}]))


class SessionParsingTest(unittest.TestCase):
    def test_session_id_from_jsonl_event(self) -> None:
        stream = '{"type":"session.created","session_id":"abc-123"}\n{"type":"item"}'
        self.assertEqual(extract_session_id(stream), "abc-123")

    def test_session_id_from_nested_session_object(self) -> None:
        stream = '{"type":"configured","session":{"id":"nested-9"}}'
        self.assertEqual(extract_session_id(stream), "nested-9")

    def test_session_id_regex_fallback_and_absence(self) -> None:
        self.assertEqual(extract_session_id('garbage "session_id": "xy7" tail'), "xy7")
        self.assertIsNone(extract_session_id("no ids here"))

    def test_codex_usage_extraction(self) -> None:
        stream = ('{"type":"turn"}\n'
                  '{"type":"token_count","input_tokens":10,"output_tokens":4}')
        usage = extract_codex_usage(stream)
        self.assertEqual(usage.get("input_tokens"), 10)
        self.assertEqual(usage.get("output_tokens"), 4)


class StateRoundtripTest(unittest.TestCase):
    def test_new_model_fields_survive_save_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "STATE.json"
            state = State(
                phase="micro", micro_sub="code", micro_cycle=3,
                task_queue=[{"id": "task-002", "title": "T2", "file": ".forge/tasks/task-002.md",
                             "criteria": ["c"], "test_globs": ["tests/**"], "code_globs": ["src/**"]}],
                current_task={"id": "task-001", "title": "T1", "file": "f",
                              "criteria": ["c1"], "test_globs": ["tests/**"], "code_globs": []},
                tester_session="s-t", coder_session="s-c",
                cycle_test_files=["tests/test_a.py"], pending_no_test=True,
                no_test_count=2, task_start_tag="forge/task-001-start")

            state.save(str(path))
            restored = State.load(str(path))

        self.assertEqual(restored.phase, "micro")
        self.assertEqual(restored.micro_cycle, 3)
        self.assertEqual(restored.tester_session, "s-t")
        self.assertEqual(restored.current_task["title"], "T1")
        self.assertEqual(len(restored.task_queue), 1)
        self.assertEqual(restored.cycle_test_files, ["tests/test_a.py"])
        self.assertTrue(restored.pending_no_test)
        self.assertEqual(restored.task_start_tag, "forge/task-001-start")


class RunGateTest(unittest.TestCase):
    def test_green_and_red_with_tail(self) -> None:
        green, tail = run_gate("/tmp", "", "python -c pass", 10)
        self.assertTrue(green)
        self.assertEqual(tail, "")
        red, tail = run_gate("/tmp", "", "python -c \"import sys;sys.exit(1)\"", 10)
        self.assertFalse(red)

    def test_missing_test_cmd_is_red(self) -> None:
        green, tail = run_gate("/tmp", "", "", 10)
        self.assertFalse(green)


class NextTaskIndexTest(unittest.TestCase):
    def test_counts_existing_task_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_next_task_index(tmp), 1)
            tasks = Path(tmp) / ".forge" / "tasks"
            tasks.mkdir(parents=True)
            (tasks / "task-001.md").write_text("x", encoding="utf-8")
            (tasks / "task-003.md").write_text("x", encoding="utf-8")
            self.assertEqual(_next_task_index(tmp), 4)


class PlanBatchTest(unittest.TestCase):
    @patch("forge.orchestrate.commit_all")
    def test_queue_keeps_only_tasks_whose_file_exists(self, _commit: Mock) -> None:
        with tempfile.TemporaryDirectory() as project:
            tasks_dir = Path(project) / ".forge" / "tasks"
            tasks_dir.mkdir(parents=True)
            (tasks_dir / "task-001.md").write_text("# Zadanie 001", encoding="utf-8")
            plan_json = (
                '```json\n{"no_more_tasks": false, "tasks": ['
                '{"id":"task-001","title":"Ruch","file":".forge/tasks/task-001.md",'
                '"criteria":["a"],"test_globs":["tests/**"],"code_globs":["src/**"]},'
                '{"id":"task-002","title":"Duch","file":".forge/tasks/task-002.md",'
                '"criteria":["b"],"test_globs":["tests/**"],"code_globs":["src/**"]}'
                ']}\n```')
            cfg = Config()
            state = State()
            with patch("forge.orchestrate.run_planner", return_value=plan_json):
                result = phase_plan_batch(cfg, project, state, lambda ph: os.path.join(project, "log"))

            self.assertFalse(result["no_more_tasks"])
            self.assertEqual(len(state.task_queue), 1)  # task-002 bez pliku odrzucone
            self.assertEqual(state.task_queue[0]["title"], "Ruch")


class MicroLoopTest(unittest.TestCase):
    def _init_repo(self, project: str) -> None:
        subprocess.run(["git", "init", "-q"], cwd=project, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=project, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=project, check=True)
        Path(project, "seed.txt").write_text("seed", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=project, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=project, check=True)

    def test_red_then_green_cycle_reaches_review(self) -> None:
        with tempfile.TemporaryDirectory() as project:
            self._init_repo(project)
            os.makedirs(os.path.join(project, "tests"))
            os.makedirs(os.path.join(project, "src"))
            cfg = Config(max_micro_cycles=5, max_green_retries=1, git_push=False)
            state = State(test_cmd="pytest", build_cmd="",
                          current_task_title="Ruch",
                          current_task={"id": "task-001", "title": "Ruch",
                                        "file": ".forge/tasks/task-001.md",
                                        "criteria": ["c1"], "test_globs": ["tests/**"],
                                        "code_globs": ["src/**"]},
                          phase="micro", micro_sub="test")

            calls = {"n": 0}

            def fake_codex(prompt, cfg_, proj, log, *, session_id=None, model=None, effort=None):
                calls["n"] += 1
                if calls["n"] == 1:  # tester pisze test
                    Path(proj, "tests", "test_a.py").write_text("def test_a():\n    assert move()\n",
                                                                encoding="utf-8")
                    return '```json\n{"action":"wrote_test","test_files":["tests/test_a.py"]}\n```', "sess-t"
                if calls["n"] == 2:  # koder implementuje
                    Path(proj, "src", "a.py").write_text("def move():\n    return True\n",
                                                         encoding="utf-8")
                    return '```json\n{"made_green":true,"refactored":true}\n```', "sess-c"
                # tester orzeka DONE
                return ('```json\n{"action":"done","criteria_map":['
                        '{"criterion":"c1","test":"tests/test_a.py::test_a","status":"covered"}]}\n```'), "sess-t"

            # bramka: po teście czerwona, po kodzie zielona, przy DONE zielona
            gate_results = iter([(False, "brak move()"), (True, ""), (True, "")])

            with patch("forge.orchestrate.run_codex_session", side_effect=fake_codex), \
                 patch("forge.orchestrate.run_gate", side_effect=lambda *a, **k: next(gate_results)):
                reached = _run_micro_loop(cfg, project, state, lambda ph: os.path.join(project, "log"))

            self.assertTrue(reached)
            self.assertEqual(state.phase, "review")
            self.assertEqual(state.tester_session, "sess-t")
            self.assertEqual(state.coder_session, "sess-c")
            log = subprocess.run(["git", "log", "--oneline"], cwd=project,
                                 capture_output=True, text=True).stdout
            self.assertIn("tdd: Ruch (cykl 1)", log)

    def test_test_passing_immediately_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as project:
            self._init_repo(project)
            os.makedirs(os.path.join(project, "tests"))
            cfg = Config(max_micro_cycles=1, max_green_retries=1, git_push=False)
            state = State(test_cmd="pytest", build_cmd="",
                          current_task={"id": "task-001", "title": "Ruch",
                                        "file": ".forge/tasks/task-001.md",
                                        "criteria": ["c1"], "test_globs": ["tests/**"],
                                        "code_globs": ["src/**"]},
                          phase="micro", micro_sub="test")

            def fake_codex(prompt, cfg_, proj, log, *, session_id=None, model=None, effort=None):
                Path(proj, "tests", "test_a.py").write_text("def test_a():\n    assert True\n",
                                                            encoding="utf-8")
                return '```json\n{"action":"wrote_test","test_files":["tests/test_a.py"]}\n```', "sess-t"

            with patch("forge.orchestrate.run_codex_session", side_effect=fake_codex), \
                 patch("forge.orchestrate.run_gate", return_value=(True, "")):  # test przechodzi od razu
                reached = _run_micro_loop(cfg, project, state, lambda ph: os.path.join(project, "log"))

            self.assertFalse(reached)  # nie osiągnięto DONE — bramka czerwona nie zaszła
            self.assertFalse(Path(project, "tests", "test_a.py").exists())  # test wycofany


class TaskIterationEndToEndTest(unittest.TestCase):
    def _init_repo(self, project: str) -> None:
        subprocess.run(["git", "init", "-q"], cwd=project, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=project, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=project, check=True)
        Path(project, "seed.txt").write_text("seed", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=project, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=project, check=True)

    def test_plan_micro_review_finish(self) -> None:
        with tempfile.TemporaryDirectory() as project:
            self._init_repo(project)
            os.makedirs(os.path.join(project, "tests"))
            os.makedirs(os.path.join(project, "src"))
            tasks_dir = Path(project) / ".forge" / "tasks"
            tasks_dir.mkdir(parents=True)
            cfg = Config(max_micro_cycles=5, max_green_retries=1, git_push=False)
            state = State(bootstrapped=True, test_cmd="pytest", build_cmd="", phase="idle")

            def fake_planner(prompt, cfg_, proj, log):
                (Path(proj) / ".forge" / "tasks" / "task-001.md").write_text(
                    "# Zadanie 001", encoding="utf-8")
                return ('```json\n{"no_more_tasks": false, "tasks": [{"id":"task-001",'
                        '"title":"Ruch","file":".forge/tasks/task-001.md",'
                        '"criteria":["c1"],"test_globs":["tests/**"],"code_globs":["src/**"]}]}\n```')

            calls = {"n": 0}

            def fake_codex(prompt, cfg_, proj, log, *, session_id=None, model=None, effort=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    Path(proj, "tests", "test_a.py").write_text("def test_a():\n    assert 1\n",
                                                                encoding="utf-8")
                    return '```json\n{"action":"wrote_test","test_files":["tests/test_a.py"]}\n```', "s-t"
                if calls["n"] == 2:
                    Path(proj, "src", "a.py").write_text("x = 1\n", encoding="utf-8")
                    return '```json\n{"made_green":true}\n```', "s-c"
                if calls["n"] == 3:
                    return ('```json\n{"action":"done","criteria_map":['
                            '{"criterion":"c1","test":"tests/test_a.py::test_a","status":"covered"}]}\n```'), "s-t"
                return '```json\n{"verdict":"approve","notes":[]}\n```', "s-t"

            gate = iter([(False, "red"), (True, ""), (True, ""), (True, "")])

            with patch("forge.orchestrate.run_planner", side_effect=fake_planner), \
                 patch("forge.orchestrate.run_codex_session", side_effect=fake_codex), \
                 patch("forge.orchestrate.run_gate", side_effect=lambda *a, **k: next(gate)):
                cont = _task_iteration(cfg, project, state)

            self.assertTrue(cont)
            self.assertEqual(state.phase, "idle")      # zadanie zamknięte
            self.assertEqual(state.iteration, 1)
            self.assertEqual(state.last_done, "Ruch")
            self.assertEqual(state.task_queue, [])
            log = subprocess.run(["git", "log", "--oneline"], cwd=project,
                                 capture_output=True, text=True).stdout
            self.assertIn("tdd: Ruch (cykl 1)", log)


if __name__ == "__main__":
    unittest.main()
