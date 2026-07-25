from __future__ import annotations

import subprocess
from unittest.mock import patch

from forge.config import Config
from forge.orchestrate import (
    _transcript_log_path,
    build_then_test,
    run_tests,
)


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


def test_transcript_logs_live_in_user_cache_not_project(
        tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    cache = tmp_path / "cache"
    project.mkdir()
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))

    path = _transcript_log_path(str(project), 1, "tester")

    assert path.is_relative_to(cache / "forge")
    assert not path.is_relative_to(project)
    assert path.name == "iter-0001-tester.log"


def test_transcript_log_retention_keeps_last_twenty_iterations(
        tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    cache = tmp_path / "cache"
    project.mkdir()
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    for iteration in range(1, 26):
        path = _transcript_log_path(str(project), iteration, "tester")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("log", encoding="utf-8")
        extra = _transcript_log_path(str(project), iteration, "coder")
        extra.write_text("log", encoding="utf-8")

    _transcript_log_path(str(project), 26, "master")
    remaining = sorted(
        int(path.name.split("-")[1])
        for path in _transcript_log_path(
            str(project), 26, "master").parent.glob("iter-*-*.log")
    )

    assert min(remaining) == 7
    assert max(remaining) == 25
    assert set(remaining) == set(range(7, 26))
