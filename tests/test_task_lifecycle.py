from pathlib import Path
from unittest.mock import patch
import subprocess

from forge.config import Config
from forge import orchestrate
from forge.state import State


def test_minimal_state_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "STATE.json"
    state = State(task_phase="coder", tdd_round=2, tester_session="t", coder_session="c", task_start_tag="tag")
    state.save(str(path))
    assert State.load(str(path)).coder_session == "c"


def test_worktree_fingerprint_detects_untracked_file(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    before = orchestrate._tree_fingerprint(str(tmp_path))
    (tmp_path / "created.py").write_text("x = 1", encoding="utf-8")
    assert orchestrate._tree_fingerprint(str(tmp_path)) != before


def test_role_call_does_not_inject_or_update_legacy_record(tmp_path: Path) -> None:
    state = State(
        current_task={"difficulty": "simple"},
        tester_record="tester history",
        tester_session="stale-session",
    )
    with patch(
            "forge.orchestrate.run_agent_session",
            return_value=("new tester action", "ignored-session")) as call:
        orchestrate._call_role(Config(), str(tmp_path), state, "tester", "prompt", "log")
    assert call.call_args.args[1] == "prompt"
    assert call.call_args.kwargs["session_id"] is None
    assert state.tester_record == "tester history"
    assert state.tester_session == ""


def test_failure_creates_ref_artifact_and_removes_new_file(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "seed").write_text("seed", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True); subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    subprocess.run(["git", "tag", "forge/task-start"], cwd=tmp_path, check=True)
    (tmp_path / "new.py").write_text("new", encoding="utf-8")
    state = State(current_task={"id": "task"}, task_start_tag="forge/task-start")
    orchestrate._fail_task(Config(git_push=False), str(tmp_path), state, "boom")
    assert not (tmp_path / "new.py").exists()
    assert (tmp_path / ".forge" / "failed" / "task" / "untracked" / "new.py").exists()
    assert subprocess.run(["git", "show-ref", "--verify", "--quiet", "refs/heads/forge/failed/task"], cwd=tmp_path).returncode == 0


def test_failure_keeps_independent_tasks_and_drops_transitive_dependants(
        tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"],
                   cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"],
                   cwd=tmp_path, check=True)
    (tmp_path / "seed").write_text("seed", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".forge/\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    subprocess.run(["git", "tag", "forge/task-001-start"],
                   cwd=tmp_path, check=True)
    state = State(
        current_task={"id": "task-001"},
        task_start_tag="forge/task-001-start",
        task_queue=[
            {"id": "task-002", "depends_on": ["task-001"]},
            {"id": "task-003", "depends_on": ["task-002"]},
            {"id": "task-004", "depends_on": []},
            {"id": "task-005", "depends_on": ["task-999"]},
        ],
    )

    orchestrate._fail_task(
        Config(git_push=False), str(tmp_path), state, "kontrakt niemożliwy")

    assert [task["id"] for task in state.task_queue] == [
        "task-004", "task-005",
    ]
    for task in state.task_queue:
        assert "task-001" in task["batch_handoff"]
        assert "kontrakt niemożliwy" in task["batch_handoff"]


def test_plan_task_normalises_dependencies() -> None:
    task = orchestrate.build_task_from_plan("/tmp", {
        "id": "task-003",
        "depends_on": ["task-001", 2, ""],
        "criteria": ["dead"],
        "test_globs": ["dead"],
        "code_globs": ["dead"],
        "repro_cmd": "dead",
    })

    assert task["depends_on"] == ["task-001", "2"]
    for dead in ("criteria", "test_globs", "code_globs", "repro_cmd"):
        assert dead not in task


def test_fail_task_survives_detached_head(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "seed").write_text("seed", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    subprocess.run(["git", "tag", "forge/task-start"], cwd=tmp_path, check=True)
    # Odłącz HEAD: `git branch --show-current` zwróci pusty string.
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, text=True, capture_output=True).stdout.strip()
    subprocess.run(["git", "checkout", "-q", head], cwd=tmp_path, check=True)
    (tmp_path / "new.py").write_text("boom", encoding="utf-8")
    state = State(current_task={"id": "task"}, task_start_tag="forge/task-start")
    # Nie może rzucić CalledProcessError na `git switch ""`.
    orchestrate._fail_task(Config(git_push=False), str(tmp_path), state, "boom")
    assert not (tmp_path / "new.py").exists()
    assert subprocess.run(["git", "show-ref", "--verify", "--quiet", "refs/heads/forge/failed/task"], cwd=tmp_path).returncode == 0


def test_tree_fingerprint_ignores_volatile_artifacts(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "app.py").write_text("x = 1", encoding="utf-8")
    before = orchestrate._tree_fingerprint(str(tmp_path))
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "app.cpython-311.pyc").write_bytes(b"\x00volatile")
    (tmp_path / ".coverage").write_text("cov", encoding="utf-8")
    (tmp_path / "app.pyc").write_bytes(b"\x01")
    assert orchestrate._tree_fingerprint(str(tmp_path)) == before


def test_old_active_root_state_is_rejected_before_migration(tmp_path: Path) -> None:
    old = tmp_path / "STATE.json"
    old.write_text('{"phase":"micro","current_task_title":"G71.1a2b3"}', encoding="utf-8")
    with __import__("pytest").raises(ValueError, match="starej fazy"):
        State.load(str(old))


def test_discard_legacy_task_preserves_bootstrap_data(tmp_path: Path) -> None:
    (tmp_path / "STATE.json").write_text('{"phase":"micro","bootstrapped":true,"test_cmd":"pytest","build_cmd":"make","iteration":7}', encoding="utf-8")
    modern = orchestrate.discard_legacy_task(str(tmp_path), Config())
    state = State.load(str(modern))
    assert state.bootstrapped and state.test_cmd == "pytest" and state.iteration == 7
    assert state.task_phase == "" and state.task_queue == []


def test_commit_restart_does_not_enter_tdd_loop(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "task.md").write_text("task", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True); subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    subprocess.run(["git", "tag", "forge/task-start"], cwd=tmp_path, check=True)
    state = State(current_task={"id": "task", "title": "Task", "file": "task.md", "difficulty": "simple"}, task_phase="commit", task_start_tag="forge/task-start", tester_decision={"status": "review"}, tester_handoff="old")
    with patch("forge.orchestrate.run_tdd_loop",
               side_effect=AssertionError("TDD must not run")), \
         patch("forge.orchestrate.build_then_test_result",
               return_value=(True, "ok")) as gate, \
         patch("forge.orchestrate.commit_all"):
        assert orchestrate.run_task(Config(git_push=False), str(tmp_path), state, lambda _: "log")
    gate.assert_called_once()
    assert state.current_task == {}
    assert state.tester_decision == {} and state.tester_handoff == ""
