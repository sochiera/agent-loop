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
                               phase_plan_batch, red_gate_ok, role_paths_ok,
                               run_gate)
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

    # Testy mapy kryteriów żyją w tests/test_plan4.py (ValidateCriteriaMapTest)
    # — od PLAN-4 mapę waliduje validate_criteria_map z powodami odrzucenia.

    def test_tester_may_write_tests_when_globs_missing(self) -> None:
        from forge.orchestrate import tester_path_violations
        # Bez test_globs obowiązuje heurystyka — test w tests/ jest legalny,
        # a wyciek do src/ nadal łapany.
        viol = tester_path_violations(["tests/test_x.py", "src/leak.py"], [])
        self.assertEqual(viol, ["src/leak.py"])
        # Z globami: glob wygrywa nad heurystyką.
        viol = tester_path_violations(["spec/test_x.py", "docs/DESIGN.md"], ["spec/**"])
        self.assertEqual(viol, [])


class SessionParsingTest(unittest.TestCase):
    def test_thread_started_real_format(self) -> None:
        # Dokładny kształt z dokumentacji codex exec --json (2026).
        stream = ('{"type":"thread.started","thread_id":"019bd457-0bfc-7272-9f80-2c709bc6a6bb"}\n'
                  '{"type":"turn.started"}\n'
                  '{"type":"item.completed","item":{"id":"i1","item_type":"agent_message","text":"ok"}}')
        self.assertEqual(extract_session_id(stream),
                         "019bd457-0bfc-7272-9f80-2c709bc6a6bb")

    def test_turn_completed_usage_real_format(self) -> None:
        stream = ('{"type":"thread.started","thread_id":"x"}\n'
                  '{"type":"turn.completed","usage":{"input_tokens":24763,'
                  '"cached_input_tokens":24448,"output_tokens":122}}\n'
                  '{"type":"turn.completed","usage":{"input_tokens":100,'
                  '"cached_input_tokens":0,"output_tokens":10}}')
        usage = extract_codex_usage(stream)
        self.assertEqual(usage["input_tokens"], 24863)   # suma tur
        self.assertEqual(usage["output_tokens"], 132)
        self.assertEqual(usage["cached_input_tokens"], 24448)

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
        green, tail = run_gate("/tmp", "", "python3 -c pass", 10)
        self.assertTrue(green)
        self.assertEqual(tail, "")
        red, tail = run_gate("/tmp", "", "python3 -c \"import sys;sys.exit(1)\"", 10)
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

            def fake_codex(name, prompt, cfg_, proj, log, *, session_id=None, model=None, effort=None):
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

            with patch("forge.orchestrate.run_agent_session", side_effect=fake_codex), \
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

            def fake_codex(name, prompt, cfg_, proj, log, *, session_id=None, model=None, effort=None):
                Path(proj, "tests", "test_a.py").write_text("def test_a():\n    assert True\n",
                                                            encoding="utf-8")
                return '```json\n{"action":"wrote_test","test_files":["tests/test_a.py"]}\n```', "sess-t"

            with patch("forge.orchestrate.run_agent_session", side_effect=fake_codex), \
                 patch("forge.orchestrate.run_gate", return_value=(True, "")):  # test przechodzi od razu
                reached = _run_micro_loop(cfg, project, state, lambda ph: os.path.join(project, "log"))

            self.assertFalse(reached)  # nie osiągnięto DONE — bramka czerwona nie zaszła
            self.assertFalse(Path(project, "tests", "test_a.py").exists())  # test wycofany


class PhaseRoundTripTest(unittest.TestCase):
    def test_log_filenames_map_to_report_groups(self) -> None:
        from forge.agents import _phase_from_log
        from forge.report import normalize_phase
        cases = {
            "iter-0001-plan.log": "plan",
            "iter-0000-bootstrap.log": "bootstrap",
            "task-0001-c01-test.log": "micro-test",
            "task-0001-c12-code0.log": "micro-code",
            "task-0001-review-r0.log": "review",
            "task-0001-review-fix1.log": "review-fix",
        }
        for filename, group in cases.items():
            self.assertEqual(normalize_phase(_phase_from_log(filename)), group,
                             f"{filename} → {_phase_from_log(filename)!r}")


class StaleLastMessageTest(unittest.TestCase):
    def test_previous_verdict_is_not_reread_when_run_writes_nothing(self) -> None:
        from forge.agents import run_codex_session
        with tempfile.TemporaryDirectory() as project:
            cfg = Config()
            stale = Path(project) / cfg.runtime_dir / "codex_last.txt"
            stale.parent.mkdir(parents=True)
            stale.write_text('{"action":"done"}', encoding="utf-8")  # werdykt POPRZEDNIEJ roli

            with patch("forge.agents._run_with_backoff",
                       return_value='{"type":"thread.started","thread_id":"t1"}'):
                out, sid = run_codex_session("prompt", cfg, project, "/tmp/log")

            self.assertEqual(out, "")          # nie werdykt poprzedniej roli
            self.assertEqual(sid, "t1")


class EmptyPlanQueueTest(unittest.TestCase):
    def _state(self) -> State:
        return State(bootstrapped=True, test_cmd="pytest", phase="idle")

    @patch("forge.orchestrate.save_checkpoint")
    @patch("forge.orchestrate.phase_plan_batch")
    def test_empty_queue_without_no_more_tasks_raises(self, plan: Mock, _save: Mock) -> None:
        from forge.agents import AgentError
        from forge.orchestrate import _task_iteration
        plan.return_value = {"no_more_tasks": False}
        with self.assertRaisesRegex(AgentError, "wykonalnego zadania"):
            _task_iteration(Config(), "/tmp/p", self._state())

    @patch("forge.orchestrate.save_checkpoint")
    @patch("forge.orchestrate.phase_plan_batch")
    def test_empty_queue_with_no_more_tasks_finishes_cleanly(self, plan: Mock, _save: Mock) -> None:
        from forge.orchestrate import _task_iteration
        plan.return_value = {"no_more_tasks": True}
        self.assertFalse(_task_iteration(Config(), "/tmp/p", self._state()))


class ProjectKindTest(unittest.TestCase):
    def test_bootstrap_classifies_and_prompts_adapt(self) -> None:
        import forge.prompts as p
        self.assertEqual(p.mvp_phrase("game"), "grywalnego MVP")
        self.assertEqual(p.mvp_phrase("app"), "działającego MVP")
        self.assertIn("grywalnego MVP", p.plan_batch_prompt(5, 1, "game"))
        self.assertIn("działającego MVP", p.plan_batch_prompt(5, 1, "app"))
        # Prompt bootstrapu jest neutralny i prosi o klasyfikację.
        boot = p.bootstrap_prompt("dowolny brief")
        self.assertIn("BRIEF PRODUKTU", boot)
        self.assertIn('"kind"', boot)
        self.assertNotIn("BRIEF GRY", boot)

    @patch("forge.orchestrate.commit_all")
    @patch("forge.orchestrate.build_then_test", return_value=True)
    def test_bootstrap_stores_project_kind(self, _bt: Mock, _commit: Mock) -> None:
        from forge.orchestrate import phase_bootstrap
        with tempfile.TemporaryDirectory() as project:
            brief = Path(project) / "brief.md"
            brief.write_text("gra taktyczna", encoding="utf-8")
            cfg = Config(brief_path=str(brief), agent_timeout_s=5)
            state = State()
            payload = ('{"kind":"game","stack":"Py","test_cmd":"pytest",'
                       '"build_cmd":"","run_cmd":"python g.py"}')
            with patch("forge.orchestrate.run_planner", return_value=payload):
                phase_bootstrap(cfg, project, state, lambda ph: "/tmp/log")
            self.assertEqual(state.project_kind, "game")

    @patch("forge.orchestrate.commit_all")
    @patch("forge.orchestrate.build_then_test", return_value=True)
    def test_bootstrap_defaults_to_app_when_kind_missing(self, _bt: Mock, _commit: Mock) -> None:
        from forge.orchestrate import phase_bootstrap
        with tempfile.TemporaryDirectory() as project:
            brief = Path(project) / "brief.md"
            brief.write_text("narzędzie CLI", encoding="utf-8")
            cfg = Config(brief_path=str(brief), agent_timeout_s=5)
            state = State()
            payload = ('{"stack":"Py","test_cmd":"pytest","build_cmd":"","run_cmd":"x"}')
            with patch("forge.orchestrate.run_planner", return_value=payload):
                phase_bootstrap(cfg, project, state, lambda ph: "/tmp/log")
            self.assertEqual(state.project_kind, "app")


class SessionlessAgentTest(unittest.TestCase):
    def test_non_resumable_agent_gets_journal_and_no_session_id(self) -> None:
        from forge.orchestrate import _session_call, journal_append, journal_reset
        with tempfile.TemporaryDirectory() as project:
            cfg = Config(coder_agent="grok")  # generyczny → bezsesyjny
            state = State()
            journal_reset(project, cfg, "Zadanie")
            journal_append(project, cfg, "cykl 1, tester: wrote_test (walidacja wejścia)")
            seen = {}

            def fake_run_agent(name, prompt, cfg_, proj, log, *, model="", effort=""):
                seen["name"] = name
                seen["prompt"] = prompt
                return "gotowe"

            with patch("forge.orchestrate.run_agent", side_effect=fake_run_agent):
                out = _session_call(cfg, project, state, "coder", "ZRÓB", "/tmp/log")

            self.assertEqual(out, "gotowe")
            self.assertEqual(seen["name"], "grok")
            self.assertIn("DZIENNIKA", seen["prompt"])          # kontekst doklejony
            self.assertIn("walidacja wejścia", seen["prompt"])
            self.assertTrue(seen["prompt"].endswith("ZRÓB"))
            self.assertEqual(state.coder_session, "")            # brak id sesji


class NoTestSmellTest(unittest.TestCase):
    def test_excessive_no_test_forces_review(self) -> None:
        with tempfile.TemporaryDirectory() as project:
            subprocess.run(["git", "init", "-q"], cwd=project, check=True)
            # próg = max(2, 6//3) = 2 → 3. deklaracja no_test wymusza recenzję
            cfg = Config(max_micro_cycles=6, git_push=False)
            state = State(test_cmd="pytest", current_task_title="T",
                          current_task={"file": "f", "criteria": [], "test_globs": ["tests/**"]},
                          phase="micro", micro_sub="test", no_test_count=2)

            with patch("forge.orchestrate._session_call",
                       return_value='```json\n{"action":"no_test","reason":"strukturalny"}\n```'):
                reached = _run_micro_loop(cfg, project, state, lambda ph: "/tmp/log")

            # "smell" (nie "done") — recenzja MUSI sama zgatować drzewo, bo tu
            # bramki nie uruchomiono.
            self.assertEqual(reached, "smell")
            self.assertEqual(state.phase, "review")
            self.assertEqual(state.no_test_count, 3)


class ReviewLoopGateEconomyTest(unittest.TestCase):
    def _state(self) -> State:
        return State(phase="review", current_task={"file": "f"},
                     current_task_title="T", test_cmd="pytest")

    @patch("forge.orchestrate.run_gate")
    @patch("forge.orchestrate.run_agent_session",
           return_value=('```json\n{"verdict":"approve","notes":[]}\n```', None))
    def test_fresh_green_from_done_skips_initial_gate(self, _review: Mock, gate: Mock) -> None:
        from forge.orchestrate import _run_review_loop
        with tempfile.TemporaryDirectory() as project:
            ok = _run_review_loop(Config(), project, self._state(),
                                  lambda ph: "/tmp/log", gate_green=True)
        self.assertTrue(ok)
        gate.assert_not_called()  # DONE chwilę temu potwierdził zieleń

    @patch("forge.orchestrate.commit_all")
    @patch("forge.orchestrate.run_gate", side_effect=[(False, ""), (True, "")])
    @patch("forge.orchestrate.run_agent_session",
           return_value=('```json\n{"verdict":"approve","notes":[]}\n```', None))
    @patch("forge.orchestrate._session_call", side_effect=['```json\n{}\n```'])
    def test_red_gate_keeps_reviewer_notes_and_reuses_fix_gate_result(
        self, call: Mock, _review: Mock, gate: Mock, _commit: Mock
    ) -> None:
        from forge.orchestrate import _run_review_loop
        state = self._state()
        state.review_notes = ["Uwaga recenzenta X"]
        with tempfile.TemporaryDirectory() as project:
            ok = _run_review_loop(Config(), project, state,
                                  lambda ph: "/tmp/log", gate_green=False)
        self.assertTrue(ok)
        # Prompt rundy poprawek zawiera i uwagę recenzenta, i wymóg zieleni.
        fix_prompt = call.call_args_list[0].args[4]
        self.assertIn("Uwaga recenzenta X", fix_prompt)
        self.assertIn("Bramka testów czerwona", fix_prompt)
        # Bramka: raz na wejściu (czerwona) + raz po poprawce (zielona) — wynik
        # po poprawce jest reużyty, bez trzeciego przebiegu przed recenzją.
        self.assertEqual(gate.call_count, 2)


class CalledProcessErrorPathTest(unittest.TestCase):
    @patch("forge.orchestrate.rollback")
    @patch("forge.orchestrate._fail_task")
    @patch("forge.orchestrate._task_iteration",
           side_effect=subprocess.CalledProcessError(128, "git"))
    def test_new_model_git_failure_goes_through_fail_task(
        self, _iter: Mock, fail: Mock, rb: Mock
    ) -> None:
        from forge.orchestrate import one_iteration
        state = State(current_task={"id": "task-001", "title": "Ruch"},
                      current_task_title="Ruch", task_start_tag="forge/task-001-start")
        with self.assertRaises(subprocess.CalledProcessError):
            one_iteration(Config(), "/tmp/p", state)
        fail.assert_called_once()          # reset do taga + failures.md + sprzątnięcie
        rb.assert_not_called()             # NIE legacy rollback do HEAD

    @patch("forge.orchestrate.save_checkpoint")
    @patch("forge.orchestrate._fail_task")
    @patch("forge.orchestrate.rollback")
    @patch("forge.orchestrate._legacy_iteration",
           side_effect=subprocess.CalledProcessError(128, "git"))
    def test_legacy_mode_keeps_old_rollback(
        self, _iter: Mock, rb: Mock, fail: Mock, _save: Mock
    ) -> None:
        from forge.orchestrate import one_iteration
        with self.assertRaises(subprocess.CalledProcessError):
            one_iteration(Config(legacy_mode=True), "/tmp/p", State())
        rb.assert_called_once()
        fail.assert_not_called()


class SmokeDryTest(unittest.TestCase):
    def test_dry_reports_missing_codex_with_nonzero_exit(self) -> None:
        from forge import smoke
        with patch("forge.smoke.shutil.which",
                   side_effect=lambda name: None if "codex" in name else f"/bin/{name}"):
            self.assertEqual(smoke.main(["--dry"]), 1)

    def test_dry_passes_when_binaries_present(self) -> None:
        from forge import smoke
        ver = subprocess.CompletedProcess([], 0, "codex 0.142.3", "")
        with patch("forge.smoke.shutil.which", return_value="/bin/x"), \
             patch("forge.smoke._run", return_value=ver):
            self.assertEqual(smoke.main(["--dry"]), 0)


class SessionLossFallbackTest(unittest.TestCase):
    def test_lost_session_retries_fresh_with_journal_preamble(self) -> None:
        from forge.agents import AgentError
        from forge.orchestrate import _session_call, journal_append, journal_reset
        with tempfile.TemporaryDirectory() as project:
            cfg = Config()
            state = State(tester_session="stary-id")
            journal_reset(project, cfg, "Ruch")
            journal_append(project, cfg, "cykl 1, tester: wrote_test (ruch po heksach)")

            seen = []

            def fake(name, prompt, cfg_, proj, log, *, session_id=None, model=None, effort=None):
                seen.append((prompt, session_id))
                if session_id:  # pierwsza próba: wznowienie znikniętej sesji
                    raise AgentError("error: thread 'stary-id' not found")
                return "wynik", "nowy-id"

            with patch("forge.orchestrate.run_agent_session", side_effect=fake):
                out = _session_call(cfg, project, state, "tester", "PROMPT", "/tmp/log")

            self.assertEqual(out, "wynik")
            self.assertEqual(state.tester_session, "nowy-id")
            self.assertEqual(len(seen), 2)
            self.assertEqual(seen[0][1], "stary-id")          # próba wznowienia
            self.assertIsNone(seen[1][1])                     # świeża sesja
            self.assertIn("DZIENNIKA", seen[1][0])            # z preambułą dziennika
            self.assertIn("ruch po heksach", seen[1][0])
            self.assertTrue(seen[1][0].endswith("PROMPT"))

    def test_unrelated_agent_error_is_not_swallowed(self) -> None:
        from forge.agents import AgentError
        from forge.orchestrate import _session_call
        with tempfile.TemporaryDirectory() as project:
            state = State(coder_session="id")
            with patch("forge.orchestrate.run_agent_session",
                       side_effect=AgentError("agent zwrócił kod 1. Ogon:\nSyntaxError")):
                with self.assertRaisesRegex(AgentError, "SyntaxError"):
                    _session_call(Config(), project, state, "coder", "P", "/tmp/log")
            self.assertEqual(state.coder_session, "id")  # sesja nieskasowana


class AntiWeakeningTest(unittest.TestCase):
    """Snapshot HEAD w worktree: zmodyfikowane testy muszą tam failować."""

    def _repo_with_uncommitted_impl(self, project: str, test_body: str) -> None:
        subprocess.run(["git", "init", "-q"], cwd=project, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=project, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=project, check=True)
        # HEAD: implementacja "sprzed cyklu" (f() == 1, test by na niej padł)
        # + zielona suita (baseline v2 wymaga zieleni na HEAD, jak w realnym
        # projekcie, gdzie commit cyklu zapada tylko przy zielonej bramce).
        Path(project, "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        Path(project, "test_base.py").write_text(
            "import unittest\nclass B(unittest.TestCase):\n"
            "    def test_base(self):\n        self.assertTrue(True)\n",
            encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=project, check=True)
        subprocess.run(["git", "commit", "-qm", "przed cyklem"], cwd=project, check=True)
        # Katalog roboczy: praca kodera (niezacommitowana) + test w bieżącej postaci.
        Path(project, "mod.py").write_text("def f():\n    return 2\n", encoding="utf-8")
        Path(project, "test_mod.py").write_text(test_body, encoding="utf-8")

    def test_honest_test_change_still_fails_on_old_code(self) -> None:
        from forge.orchestrate import anti_weakening_ok
        with tempfile.TemporaryDirectory() as project:
            self._repo_with_uncommitted_impl(
                project,
                "import unittest, mod\n"
                "class T(unittest.TestCase):\n"
                "    def test_f(self):\n"
                "        self.assertEqual(mod.f(), 2)\n")
            self.assertTrue(anti_weakening_ok(
                project, ["test_mod.py"], "",
                "python3 -m unittest discover -q", 60))

    def test_gutted_test_passing_on_old_code_is_flagged(self) -> None:
        from forge.orchestrate import anti_weakening_ok
        with tempfile.TemporaryDirectory() as project:
            self._repo_with_uncommitted_impl(
                project,
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_f(self):\n"
                "        self.assertTrue(True)\n")  # rozwodniony — nic nie specyfikuje
            self.assertFalse(anti_weakening_ok(
                project, ["test_mod.py"], "",
                "python3 -m unittest discover -q", 60))

    def test_no_files_never_blocks(self) -> None:
        from forge.orchestrate import anti_weakening_ok
        self.assertTrue(anti_weakening_ok("/tmp", [], "", "pytest", 5))


class CycleSnapshotRestoreTest(unittest.TestCase):
    def test_cycle_test_is_restored_not_deleted(self) -> None:
        from forge.orchestrate import restore_test_changes, snapshot_cycle_tests
        with tempfile.TemporaryDirectory() as project:
            subprocess.run(["git", "init", "-q"], cwd=project, check=True)
            cfg = Config()
            test = Path(project, "tests", "test_a.py")
            test.parent.mkdir()
            test.write_text("wersja testera", encoding="utf-8")
            snapshot_cycle_tests(project, cfg, ["tests/test_a.py"])
            test.write_text("rozwodnione przez kodera", encoding="utf-8")

            restore_test_changes(project, cfg, ["tests/test_a.py"], ["tests/test_a.py"])

            self.assertEqual(test.read_text(encoding="utf-8"), "wersja testera")


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

            def fake_codex(name, prompt, cfg_, proj, log, *, session_id=None, model=None, effort=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    Path(proj, "tests", "test_a.py").write_text("def test_a():\n    assert 1\n",
                                                                encoding="utf-8")
                    return '```json\n{"action":"wrote_test","test_files":["tests/test_a.py"]}\n```', "s-t"
                if calls["n"] == 2:
                    Path(proj, "src", "a.py").write_text("x = 1\n", encoding="utf-8")
                    return '```json\n{"made_green":true}\n```', "s-c"
                if calls["n"] == 3:
                    # tester orzeka DONE
                    return ('```json\n{"action":"done","criteria_map":['
                            '{"criterion":"c1","test":"tests/test_a.py::test_a",'
                            '"status":"covered"}]}\n```'), "s-t"
                # n == 4: recenzja — idzie przez run_agent_session (session_id=None,
                # świeży kontekst, ale log_usage codeksa zachowany).
                return '```json\n{"verdict":"approve","notes":[]}\n```', None

            gate = iter([(False, "red"), (True, ""), (True, ""), (True, "")])

            with patch("forge.orchestrate.run_planner", side_effect=fake_planner), \
                 patch("forge.orchestrate.run_agent_session", side_effect=fake_codex), \
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
