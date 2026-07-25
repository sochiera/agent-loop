from __future__ import annotations

import subprocess
from unittest.mock import patch

from forge.config import Config
from forge.orchestrate import build_then_test, run_tests


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
