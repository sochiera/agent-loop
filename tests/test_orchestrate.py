from __future__ import annotations

import subprocess
from unittest.mock import patch

from forge import orchestrate
from forge.config import Config
from forge.orchestrate import (
    _housekeeping,
    _next_task_index,
    _transcript_log_path,
    build_then_test,
    run_tests,
)
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


def test_housekeeping_archives_tasks_prunes_runtime_and_flags_large_docs(
        tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    tasks = project / ".forge" / "tasks"
    failed = project / ".forge" / "failed"
    docs = project / "docs"
    tasks.mkdir(parents=True)
    failed.mkdir()
    docs.mkdir()
    for index in range(1, 4):
        (tasks / f"task-{index:03d}.md").write_text("done", encoding="utf-8")
    for index in range(1, 26):
        artifact = failed / f"task-{index:03d}"
        artifact.mkdir()
        (artifact / "reason.txt").write_text("x", encoding="utf-8")
    (docs / "ARCHITECTURE.md").write_text("x" * 20_001, encoding="utf-8")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    for iteration in range(1, 26):
        log = _transcript_log_path(str(project), iteration, "tester")
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("log", encoding="utf-8")

    _housekeeping(Config(), str(project))

    assert not list(tasks.glob("task-*.md"))
    assert len(list((tasks / "archive").glob("task-*.md"))) == 3
    assert _next_task_index(str(project)) == 4
    assert len(list(failed.iterdir())) == 20
    assert not (failed / "task-001").exists()
    remaining_logs = list(
        _transcript_log_path(str(project), 26, "tester").parent.glob(
            "iter-*-*.log"))
    assert len({path.name.split("-")[1] for path in remaining_logs}) <= 20
    backlog = (project / "BACKLOG.md").read_text(encoding="utf-8")
    assert "docs/ARCHITECTURE.md" in backlog
    assert "20" in backlog


def test_housekeeping_runs_before_planner(tmp_path) -> None:
    events: list[str] = []
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@example.test"],
        cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Forge Tests"],
        cwd=tmp_path, check=True)

    def planner(*_args, **_kwargs):
        events.append("planner")
        return '{"no_more_tasks":true,"tasks":[]}'

    with patch("forge.orchestrate._housekeeping",
               side_effect=lambda *_args: events.append("housekeeping")), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate.run_planner", side_effect=planner):
        orchestrate.phase_plan_batch(
            Config(git_push=False), str(tmp_path), State(bootstrapped=True),
            lambda phase: phase)

    assert events == ["housekeeping", "planner"]
