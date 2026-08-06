from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from forge import orchestrate
from forge.agents import AgentError
from forge.config import Config
from forge.orchestrate import (
    _housekeeping,
    _next_task_index,
    _transcript_log_path,
    build_then_test,
    build_then_test_result,
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


def test_build_then_test_result_returns_failed_command_output() -> None:
    with patch(
            "forge.orchestrate.run_shellfree",
            return_value=(1, "compiler exploded")):
        ok, output = build_then_test_result(
            "/tmp", "make", "pytest", 10)

    assert not ok
    assert output == "compiler exploded"


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


def test_interrupt_saves_checkpoint_instead_of_raising(tmp_path) -> None:
    state_path = tmp_path / ".forge" / "STATE.json"
    State(bootstrapped=True, iteration=7).save(str(state_path))

    with patch("forge.orchestrate.one_iteration", side_effect=KeyboardInterrupt):
        code = orchestrate.main(["--project", str(tmp_path), "--max-iters", "1"])

    assert code == 130
    assert State.load(str(state_path)).bootstrapped is True


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


def test_task_with_non_canonical_id_is_rejected_not_renumbered(tmp_path) -> None:
    """`_next_task_index` liczy numer następnego wsadu z formatu `task-NNN`.
    Identyfikator poza tym formatem nie zostałby policzony, więc kolejny wsad
    nadpisałby cudze pliki zadań — zgadywanie numeru byłoby gorsze niż odmowa.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "tests@example.test"],
                   cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Forge Tests"],
                   cwd=tmp_path, check=True)
    tasks = tmp_path / ".forge" / "tasks"
    tasks.mkdir(parents=True)
    for name in ("task-001.md", "task-alpha.md"):
        (tasks / name).write_text("Cel: cokolwiek\n", encoding="utf-8")
    plan = (
        '{"tasks":['
        '{"id":"task-alpha","title":"Wariant","file":".forge/tasks/task-alpha.md"},'
        '{"id":"task-001","title":"Poprawne","file":".forge/tasks/task-001.md"}'
        ']}'
    )
    state = State(bootstrapped=True)

    with patch("forge.orchestrate._housekeeping"), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate.run_planner", return_value=plan):
        orchestrate.phase_plan_batch(
            Config(git_push=False), str(tmp_path), state, lambda phase: phase)

    assert [task["id"] for task in state.task_queue] == ["task-001"]


def test_all_tasks_rejected_stops_the_batch_explicitly(tmp_path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "tests@example.test"],
                   cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Forge Tests"],
                   cwd=tmp_path, check=True)
    tasks = tmp_path / ".forge" / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "G82.md").write_text("Cel: cokolwiek\n", encoding="utf-8")
    plan = '{"tasks":[{"id":"G82.1a","title":"X","file":".forge/tasks/G82.md"}]}'

    with patch("forge.orchestrate._housekeeping"), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate.run_planner", return_value=plan), \
         pytest.raises(AgentError, match="żadnego poprawnego zadania"):
        orchestrate.phase_plan_batch(
            Config(git_push=False), str(tmp_path), State(bootstrapped=True),
            lambda phase: phase)


def test_housekeeping_prunes_task_archive_and_stale_runtime_logs(
        tmp_path) -> None:
    project = tmp_path / "project"
    runtime = project / ".forge"
    tasks = runtime / "tasks"
    archive = tasks / "archive"
    archive.mkdir(parents=True)
    for index in range(1, 26):
        (archive / f"task-{index:03d}.md").write_text("old", encoding="utf-8")
    (tasks / "task-026.md").write_text("current", encoding="utf-8")
    stale_logs = runtime / "logs"
    stale_logs.mkdir()
    (stale_logs / "task-0001-c01-test.log").write_text("{}", encoding="utf-8")

    _housekeeping(Config(), str(project))

    archived = sorted(path.name for path in archive.glob("task-*.md"))
    assert len(archived) == 20
    assert archived[0] == "task-007.md"      # najstarsze skasowane
    assert archived[-1] == "task-026.md"     # bieżące zadanie zachowane
    assert not stale_logs.exists()


def test_task_archive_pruning_never_lowers_next_index(tmp_path) -> None:
    """Przejście przez tysiąc: sort po nazwie skasowałby task-1000."""
    archive = tmp_path / ".forge" / "tasks" / "archive"
    archive.mkdir(parents=True)
    for index in list(range(980, 1000)) + [1000]:
        (archive / f"task-{index}.md").write_text("done", encoding="utf-8")

    _housekeeping(Config(), str(tmp_path))

    assert (archive / "task-1000.md").exists()
    assert not (archive / "task-980.md").exists()
    assert _next_task_index(str(tmp_path)) == 1001


def test_housekeeping_seeds_agent_instruction_files_without_overwriting(
        tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text("własna treść", encoding="utf-8")

    _housekeeping(Config(), str(tmp_path))

    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "własna treść"
    seeded = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert ".forge/" in seeded
    assert "nie czytaj go z dysku i nie\nzapisuj sam" in seeded
    assert "wyjaśnienie, nie zakaz" in seeded


@pytest.mark.parametrize("superseded", range(2))
def test_housekeeping_migrates_only_exact_older_forge_instructions(
        tmp_path, superseded: int) -> None:
    # Notka sprzed pola `notebook` licencjonowała czytanie notatnika z dysku,
    # więc pozostawiona w projekcie kupowałaby z powrotem usuniętą turę.
    old = orchestrate._superseded_agent_notes(".forge")[superseded]
    (tmp_path / "AGENTS.md").write_text(old, encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text(
        old + "\nwłasny dopisek\n", encoding="utf-8")

    _housekeeping(Config(), str(tmp_path))

    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "wpisy oddajesz polem `notebook`" in agents
    assert (tmp_path / "CLAUDE.md").read_text(
        encoding="utf-8").endswith("własny dopisek\n")


def test_housekeeping_flags_oversized_doc_mentioned_in_backlog_prose(
        tmp_path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "ARCHITECTURE.md").write_text("x" * 116_000, encoding="utf-8")
    # Planista rutynowo cytuje ścieżki dokumentacji w opisach zadań — to nie
    # jest zgłoszony dług i nie może wyciszać bramki.
    (tmp_path / "BACKLOG.md").write_text(
        "- [ ] K76 Coś tam. Szczegóły w `docs/ARCHITECTURE.md`.\n",
        encoding="utf-8")

    _housekeeping(Config(), str(tmp_path))

    backlog = (tmp_path / "BACKLOG.md").read_text(encoding="utf-8")
    assert "Dług dokumentacji: `docs/ARCHITECTURE.md` ma 116 KB" in backlog

    _housekeeping(Config(), str(tmp_path))  # drugi przebieg nie duplikuje

    assert (tmp_path / "BACKLOG.md").read_text(
        encoding="utf-8").count("Dług dokumentacji") == 1


def test_housekeeping_flags_oversized_documentation_index(tmp_path) -> None:
    index = tmp_path / "docs" / "ARCHITECTURE" / "00-INDEX.md"
    index.parent.mkdir(parents=True)
    index.write_text("x" * 2_001, encoding="utf-8")

    _housekeeping(Config(), str(tmp_path))

    backlog = (tmp_path / "BACKLOG.md").read_text(encoding="utf-8")
    assert "docs/ARCHITECTURE/00-INDEX.md" in backlog
    assert "indeks" in backlog
    assert "2 KB" in backlog


def test_fifth_planning_batch_requests_technical_debt_task(tmp_path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "tests@example.test"],
                   cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Forge Tests"],
                   cwd=tmp_path, check=True)
    state = State(bootstrapped=True, plan_batches=4)
    prompts_seen: list[str] = []

    def planner(prompt, *_args, **_kwargs):
        prompts_seen.append(prompt)
        return '{"no_more_tasks":true,"tasks":[]}'

    with patch("forge.orchestrate._housekeeping"), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.orchestrate.run_planner", side_effect=planner):
        orchestrate.phase_plan_batch(
            Config(git_push=False), str(tmp_path), state,
            lambda phase: phase)

    assert state.plan_batches == 5
    assert "zadaniem długu technicznego" in prompts_seen[0]
