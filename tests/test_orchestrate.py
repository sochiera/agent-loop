from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, Mock, patch

from forge.agents import AgentError, LimitExhausted
from forge.config import Config
from forge.orchestrate import (_ask_model, build_then_test, commit_all,
                                one_iteration, parse_start_delay,
                                phase_bootstrap, prompt_agent_settings,
                                run_tests, wait_before_start)
from forge.state import State


class RunTestsTest(unittest.TestCase):
    @patch("forge.shellrun.subprocess.run")
    def test_missing_command_fails_closed(self, run: Mock) -> None:
        self.assertFalse(run_tests("/tmp/project", "", 10))
        run.assert_not_called()

    @patch("forge.shellrun.subprocess.run")
    def test_command_is_not_passed_through_a_shell(self, run: Mock) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, "", "")

        self.assertTrue(run_tests("/tmp/project", "python -m unittest -q", 10))

        run.assert_called_once_with(
            ["python", "-m", "unittest", "-q"],
            cwd="/tmp/project",
            shell=False,
            text=True,
            capture_output=True,
            timeout=10,
        )

    @patch("forge.shellrun.subprocess.run")
    def test_malformed_command_fails_closed(self, run: Mock) -> None:
        self.assertFalse(run_tests("/tmp/project", "python 'unterminated", 10))
        run.assert_not_called()


class DelayedStartTest(unittest.TestCase):
    def test_duration_units_are_converted_to_seconds(self) -> None:
        self.assertEqual(parse_start_delay("30"), 30)
        self.assertEqual(parse_start_delay("1.5m"), 90)
        self.assertEqual(parse_start_delay("2h"), 7200)

    def test_invalid_duration_is_rejected(self) -> None:
        with self.assertRaisesRegex(Exception, "format"):
            parse_start_delay("tomorrow")

    @patch("forge.orchestrate.log")
    @patch("forge.orchestrate.time.monotonic", side_effect=[100.0, 165.0])
    @patch("forge.orchestrate.time.sleep")
    def test_wait_uses_requested_delay_and_reports_elapsed_time(
        self, sleep: Mock, _monotonic: Mock, log: Mock
    ) -> None:
        wait_before_start(60)

        sleep.assert_called_once_with(60)
        self.assertIn("actual elapsed: 1m05s", log.call_args_list[-1].args[0])

class AskModelTest(unittest.TestCase):
    @patch("builtins.input", return_value="4")
    def test_digit_picks_suggestion_by_position(self, _input: Mock) -> None:
        self.assertEqual(_ask_model("Model", "", "claude"), "fable")

    @patch("builtins.input", return_value="grok-3-mini")
    def test_free_text_passes_through_unvalidated(self, _input: Mock) -> None:
        self.assertEqual(_ask_model("Model", "", "grok"), "grok-3-mini")

    @patch("builtins.input", return_value="")
    def test_blank_keeps_default(self, _input: Mock) -> None:
        self.assertEqual(_ask_model("Model", "sonnet", "claude"), "sonnet")

    @patch("builtins.input", return_value="99")
    def test_out_of_range_digit_passes_through_as_literal(self, _input: Mock) -> None:
        self.assertEqual(_ask_model("Model", "", "claude"), "99")

    @patch("builtins.input", return_value="whatever")
    def test_unknown_agent_has_no_menu(self, _input: Mock) -> None:
        self.assertEqual(_ask_model("Model", "", "kiro"), "whatever")

    @patch("builtins.input", return_value="1")
    def test_opencode_digit_picks_neuralwatt_suggestion(self, _input: Mock) -> None:
        self.assertEqual(_ask_model("Model", "", "opencode"), "neuralwatt/glm-5.2")


class AgentSettingsTest(unittest.TestCase):
    @patch("builtins.input", side_effect=[
        "claude", "opus", "high",             # planista
        "grok", "grok-4.5", "high",           # tester
        "grok", "grok-4.5", "medium",         # koder
        "", "", "",                           # recenzent (dziedziczy testera → grok)
        "", "", "",                           # weryfikator (dziedziczy planistę → claude)
    ])
    def test_prompts_for_every_role_and_skips_codex_when_unused(self, _input: Mock) -> None:
        cfg = Config()

        prompt_agent_settings(cfg)

        self.assertEqual(cfg.planner_agent, "claude")
        self.assertEqual(cfg.planner_model, "opus")
        self.assertEqual(cfg.planner_effort, "high")
        self.assertEqual(cfg.tester_agent, "grok")
        self.assertEqual(cfg.tester_model, "grok-4.5")
        self.assertEqual(cfg.tester_effort, "high")
        self.assertEqual(cfg.coder_agent, "grok")
        self.assertEqual(cfg.coder_model, "grok-4.5")
        self.assertEqual(cfg.coder_effort, "medium")
        self.assertEqual(cfg.reviewer_agent, "")
        self.assertEqual(cfg.verifier_agent, "")
        # Żadna rola nie używa Codeksa → pytanie o niego nie powinno paść
        # (gdyby padło, side_effect wyczerpałby się i input rzuciłby StopIteration).
        self.assertEqual(_input.call_count, 15)

    @patch("builtins.input", side_effect=[
        "claude", "opus", "high",             # planista
        "", "", "",                           # tester → domyślnie codex
        "", "", "",                           # koder → domyślnie codex
        "", "", "",                           # recenzent
        "", "", "",                           # weryfikator
        "gpt-test", "xhigh",                  # Codeks w użyciu → pytanie pada
    ])
    def test_asks_for_codex_defaults_when_a_role_uses_it(self, _input: Mock) -> None:
        cfg = Config()

        prompt_agent_settings(cfg)

        self.assertEqual(cfg.tester_agent, "codex")
        self.assertEqual(cfg.coder_agent, "codex")
        self.assertEqual(cfg.codex_model, "gpt-test")
        self.assertEqual(cfg.codex_effort, "xhigh")

    @patch("builtins.input", side_effect=["", "", "wrong", "medium", "", "high"])
    def test_enter_uses_defaults_and_invalid_effort_is_retried(self, _input: Mock) -> None:
        cfg = Config(planner_agent="claude", planner_model="sonnet", planner_effort="high",
                     codex_model="", codex_effort="medium", legacy_mode=True)

        prompt_agent_settings(cfg)

        self.assertEqual(cfg.planner_model, "sonnet")
        self.assertEqual(cfg.planner_effort, "medium")
        self.assertEqual(cfg.codex_model, "")
        self.assertEqual(cfg.codex_effort, "high")

    @patch("builtins.input", side_effect=["", "", "", "", ""])
    def test_legacy_mode_skips_role_prompts(self, _input: Mock) -> None:
        cfg = Config(legacy_mode=True)

        prompt_agent_settings(cfg)

        self.assertEqual(_input.call_count, 5)  # planista (3) + Codex (2), bez ról

class BuildGateTest(unittest.TestCase):
    @patch("forge.orchestrate.run_tests")
    @patch("forge.shellrun.subprocess.run")
    def test_failed_build_is_red_and_skips_tests(self, run: Mock, tests: Mock) -> None:
        run.return_value = subprocess.CompletedProcess([], 1, "", "boom")
        self.assertFalse(build_then_test("/tmp/p", "make", "pytest", 10))
        tests.assert_not_called()  # nie testujemy, gdy build padł

    @patch("forge.orchestrate.run_tests", return_value=True)
    @patch("forge.shellrun.subprocess.run")
    def test_successful_build_then_runs_tests(self, run: Mock, tests: Mock) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        self.assertTrue(build_then_test("/tmp/p", "make", "pytest", 10))
        run.assert_called_once_with(["make"], cwd="/tmp/p", shell=False,
                                    text=True, capture_output=True, timeout=10)
        tests.assert_called_once_with("/tmp/p", "pytest", 10)

    @patch("forge.orchestrate.run_tests", return_value=True)
    @patch("forge.shellrun.subprocess.run")
    def test_empty_build_cmd_goes_straight_to_tests(self, run: Mock, tests: Mock) -> None:
        self.assertTrue(build_then_test("/tmp/p", "", "pytest", 10))
        run.assert_not_called()
        tests.assert_called_once_with("/tmp/p", "pytest", 10)


class BootstrapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.brief = self.root / "brief.md"
        self.brief.write_text("brief", encoding="utf-8")
        self.cfg = Config(brief_path=str(self.brief), agent_timeout_s=10)
        self.state = State()

    @patch("forge.orchestrate.run_planner", return_value="bez werdyktu")
    def test_invalid_agent_result_does_not_mark_bootstrap_complete(self, _run: Mock) -> None:
        with self.assertRaisesRegex(AgentError, "JSON"):
            phase_bootstrap(self.cfg, self.tmp.name, self.state, lambda _: "agent.log")

        self.assertFalse(self.state.bootstrapped)

    @patch("forge.orchestrate.commit_all")
    @patch("forge.orchestrate.run_tests", return_value=False)
    @patch(
        "forge.orchestrate.run_planner",
        return_value='{"stack":"Python","test_cmd":"python -m unittest",'
                     '"build_cmd":"","run_cmd":"python game.py"}',
    )
    def test_red_bootstrap_tests_prevent_commit(
        self, _run: Mock, _tests: Mock, commit: Mock
    ) -> None:
        with self.assertRaisesRegex(AgentError, "nie przeszły"):
            phase_bootstrap(self.cfg, self.tmp.name, self.state, lambda _: "agent.log")

        commit.assert_not_called()
        self.assertFalse(self.state.bootstrapped)


class CommitTest(unittest.TestCase):
    @patch("forge.orchestrate.git")
    def test_commit_failure_is_not_suppressed(self, git: Mock) -> None:
        staged = subprocess.CompletedProcess([], 1, "", "")
        git.side_effect = [Mock(), staged, subprocess.CalledProcessError(1, "git")]

        with self.assertRaises(subprocess.CalledProcessError):
            commit_all("/tmp/project", "message")

    @patch("forge.orchestrate.git")
    def test_commit_pushes_current_branch_when_enabled(self, git: Mock) -> None:
        git.side_effect = [
            Mock(),                                              # add -A
            subprocess.CompletedProcess([], 1, "", ""),         # diff --cached (są zmiany)
            Mock(),                                              # commit
            subprocess.CompletedProcess([], 0, "origin\n", ""), # remote
            subprocess.CompletedProcess([], 0, "main\n", ""),   # rev-parse HEAD
            subprocess.CompletedProcess([], 0, "", ""),         # push
        ]
        commit_all("/tmp/project", "message", Config(git_push=True))
        self.assertEqual(git.call_args_list[-1].args,
                         ("/tmp/project", "push", "-u", "origin", "main"))

    @patch("forge.orchestrate.git")
    def test_no_push_without_remote(self, git: Mock) -> None:
        git.side_effect = [
            Mock(),                                             # add -A
            subprocess.CompletedProcess([], 1, "", ""),        # diff --cached
            Mock(),                                             # commit
            subprocess.CompletedProcess([], 0, "", ""),        # remote (pusty → brak push)
        ]
        commit_all("/tmp/project", "message", Config(git_push=True))
        self.assertEqual(git.call_count, 4)

    @patch("forge.orchestrate.git")
    def test_no_push_when_disabled(self, git: Mock) -> None:
        git.side_effect = [
            Mock(),                                             # add -A
            subprocess.CompletedProcess([], 1, "", ""),        # diff --cached
            Mock(),                                             # commit
        ]
        commit_all("/tmp/project", "message", Config(git_push=False))
        self.assertEqual(git.call_count, 3)


class IterationTest(unittest.TestCase):
    @patch("forge.orchestrate.rollback")
    @patch("forge.orchestrate._one_iteration", side_effect=AgentError("awaria"))
    @patch("forge.orchestrate.save_checkpoint")
    def test_agent_failure_keeps_phase_for_retry(
        self, save: Mock, _iteration: Mock, rollback: Mock
    ) -> None:
        state = State(phase="implement", current_task_title="Walka")
        with self.assertRaisesRegex(AgentError, "awaria"):
            one_iteration(Config(), "/tmp/project", state)

        rollback.assert_not_called()
        save.assert_called_once_with("/tmp/project", state)
        self.assertEqual(state.phase, "implement")

    @patch("forge.orchestrate.rollback")
    @patch("forge.orchestrate._one_iteration", side_effect=LimitExhausted("limit"))
    @patch("forge.orchestrate.save_checkpoint")
    def test_limit_keeps_checkpoint_without_rollback(
        self, save: Mock, _iteration: Mock, rollback: Mock
    ) -> None:
        state = State(phase="review", current_task_title="Walka")

        with self.assertRaisesRegex(LimitExhausted, "limit"):
            one_iteration(Config(), "/tmp/project", state)

        save.assert_called_once_with("/tmp/project", state)
        rollback.assert_not_called()
        self.assertEqual(state.phase, "review")

    @patch("forge.orchestrate.commit_all")
    @patch("forge.orchestrate.phase_review", return_value={"verdict": "approve"})
    @patch("forge.orchestrate.build_then_test", return_value=True)
    @patch("forge.orchestrate.phase_implement")
    @patch("forge.orchestrate.phase_plan")
    def test_resume_at_review_skips_plan_and_implementation(
        self, plan: Mock, implement: Mock, _tests: Mock, review: Mock, commit: Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as project:
            state = State(bootstrapped=True, iteration=4, test_cmd="pytest",
                          phase="review", current_task_title="Walka",
                          tests_green=True)

            self.assertTrue(one_iteration(Config(legacy_mode=True), project, state))

        plan.assert_not_called()
        implement.assert_not_called()
        review.assert_called_once()
        commit.assert_called_once_with(project, "feat: Walka", ANY)
        self.assertEqual(state.iteration, 5)
        self.assertEqual(state.phase, "idle")


class PhaseResumeTest(unittest.TestCase):
    @patch("forge.orchestrate.rollback")
    @patch("forge.orchestrate.save_checkpoint")
    @patch("forge.orchestrate.phase_implement", side_effect=AgentError("codex padł"))
    @patch("forge.orchestrate.phase_plan",
           return_value={"task_title": "C1.1", "no_more_tasks": False})
    def test_codex_crash_leaves_phase_implement_for_restart(
        self, plan: Mock, impl: Mock, save: Mock, rb: Mock
    ) -> None:
        state = State(bootstrapped=True, phase="plan", test_cmd="pytest")
        with self.assertRaises(AgentError):
            one_iteration(Config(legacy_mode=True), "/tmp/p", state)
        # Kto następny po restarcie? Codex (implement), nie Claude (plan).
        self.assertEqual(state.phase, "implement")
        self.assertEqual(state.current_task_title, "C1.1")
        self.assertGreaterEqual(save.call_count, 2)
        rb.assert_not_called()

    @patch("forge.orchestrate.rollback")
    @patch("forge.orchestrate.commit_all")
    @patch("forge.orchestrate.build_then_test", return_value=True)
    @patch("forge.orchestrate.phase_review",
           return_value={"verdict": "approve", "notes": []})
    @patch("forge.orchestrate.phase_implement", return_value={})
    @patch("forge.orchestrate.phase_plan")
    def test_resume_at_implement_skips_planning(
        self, plan: Mock, impl: Mock, review: Mock, bt: Mock, commit: Mock, rb: Mock
    ) -> None:
        state = State(bootstrapped=True, phase="implement",
                      current_task_title="C1.1", test_cmd="pytest")
        cont = one_iteration(Config(legacy_mode=True), "/tmp/p", state)
        self.assertTrue(cont)
        plan.assert_not_called()            # Claude NIE planuje ponownie
        impl.assert_called_once()           # Codex wznawia implementację
        self.assertEqual(state.phase, "idle")   # po sukcesie następna iteracja od nowa


if __name__ == "__main__":
    unittest.main()
