from __future__ import annotations

import subprocess
from unittest.mock import patch

from forge.config import Config
from forge.orchestrate import _run_boundary, build_then_test, run_tests
from forge.state import State


def test_commands_run_without_shell() -> None:
    with patch("forge.shellrun.subprocess.Popen") as popen:
        popen.return_value.communicate.return_value = ("", "")
        popen.return_value.returncode = 0
        assert run_tests("/tmp", "python -m pytest", 10)
        assert popen.call_args.kwargs["shell"] is False
        assert popen.call_args.kwargs["start_new_session"] is True


def test_build_failure_skips_test() -> None:
    with patch("forge.shellrun.subprocess.Popen") as popen, patch("forge.orchestrate.run_tests") as tests:
        popen.return_value.communicate.return_value = ("", "")
        popen.return_value.returncode = 1
        assert not build_then_test("/tmp", "make", "pytest", 10)
        tests.assert_not_called()


def test_timeout_terminates_whole_process_group() -> None:
    timeout = subprocess.TimeoutExpired(["tool"], 1)
    with patch("forge.shellrun.subprocess.Popen") as popen, \
         patch("forge.shellrun.os.killpg") as kill_group:
        popen.return_value.pid = 123
        popen.return_value.communicate.side_effect = [timeout, ("", "")]
        assert not run_tests("/tmp", "tool", 1)
    kill_group.assert_called_once_with(123, __import__("signal").SIGTERM)


def test_kiss_config_has_only_tdd_limit() -> None:
    cfg = Config()
    assert cfg.max_tdd_rounds == 10
    assert not hasattr(cfg, "legacy_mode")


def test_review_boundary_runs_build_targeted_full_and_repro_once() -> None:
    state = State(build_cmd="make", test_cmd="pytest -q")
    task = {
        "targeted_test_cmd": "pytest -q tests/test_one.py",
        "repro_cmd": "python repro.py",
    }
    with patch("forge.orchestrate.run_shellfree", return_value=(0, "ok")) as run:
        green, results = _run_boundary("/tmp", state, task, Config())

    assert green
    assert [call.args[1] for call in run.call_args_list] == [
        "make",
        "pytest -q tests/test_one.py",
        "pytest -q",
        "python repro.py",
    ]
    assert len(results) == 4
