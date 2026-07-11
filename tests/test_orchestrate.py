from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from forge.agents import AgentError
from forge.config import Config
from forge.orchestrate import (build_then_test, commit_all, one_iteration,
                                phase_bootstrap, run_tests)
from forge.state import State


class RunTestsTest(unittest.TestCase):
    @patch("forge.orchestrate.subprocess.run")
    def test_missing_command_fails_closed(self, run: Mock) -> None:
        self.assertFalse(run_tests("/tmp/project", "", 10))
        run.assert_not_called()

    @patch("forge.orchestrate.subprocess.run")
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

    @patch("forge.orchestrate.subprocess.run")
    def test_malformed_command_fails_closed(self, run: Mock) -> None:
        self.assertFalse(run_tests("/tmp/project", "python 'unterminated", 10))
        run.assert_not_called()


class BuildGateTest(unittest.TestCase):
    @patch("forge.orchestrate.run_tests")
    @patch("forge.orchestrate.subprocess.run")
    def test_failed_build_is_red_and_skips_tests(self, run: Mock, tests: Mock) -> None:
        run.return_value = subprocess.CompletedProcess([], 1, "", "boom")
        self.assertFalse(build_then_test("/tmp/p", "make", "pytest", 10))
        tests.assert_not_called()  # nie testujemy, gdy build padł

    @patch("forge.orchestrate.run_tests", return_value=True)
    @patch("forge.orchestrate.subprocess.run")
    def test_successful_build_then_runs_tests(self, run: Mock, tests: Mock) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        self.assertTrue(build_then_test("/tmp/p", "make", "pytest", 10))
        run.assert_called_once_with(["make"], cwd="/tmp/p", shell=False,
                                    text=True, capture_output=True, timeout=10)
        tests.assert_called_once_with("/tmp/p", "pytest", 10)

    @patch("forge.orchestrate.run_tests", return_value=True)
    @patch("forge.orchestrate.subprocess.run")
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

    @patch("forge.orchestrate.run_claude", return_value="bez werdyktu")
    def test_invalid_agent_result_does_not_mark_bootstrap_complete(self, _run: Mock) -> None:
        with self.assertRaisesRegex(AgentError, "JSON"):
            phase_bootstrap(self.cfg, self.tmp.name, self.state, lambda _: "agent.log")

        self.assertFalse(self.state.bootstrapped)

    @patch("forge.orchestrate.commit_all")
    @patch("forge.orchestrate.run_tests", return_value=False)
    @patch(
        "forge.orchestrate.run_claude",
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


class IterationTest(unittest.TestCase):
    @patch("forge.orchestrate.rollback")
    @patch("forge.orchestrate._one_iteration", side_effect=AgentError("awaria"))
    def test_agent_failure_rolls_back_partial_iteration(
        self, _iteration: Mock, rollback: Mock
    ) -> None:
        with self.assertRaisesRegex(AgentError, "awaria"):
            one_iteration(Config(), "/tmp/project", State())

        rollback.assert_called_once_with("/tmp/project")


if __name__ == "__main__":
    unittest.main()
