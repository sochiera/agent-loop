import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from forge import runlock


def test_lock_lives_in_the_project_runtime_directory(tmp_path: Path) -> None:
    with runlock.acquire(str(tmp_path)):
        assert (tmp_path / ".forge" / "run.lock").is_file()


def test_second_acquire_on_the_same_project_is_refused(tmp_path: Path) -> None:
    with runlock.acquire(str(tmp_path)):
        with pytest.raises(runlock.RunLocked) as failure:
            runlock.acquire(str(tmp_path))
    assert f"PID {os.getpid()}" in str(failure.value)
    assert str(tmp_path) in str(failure.value)


def test_different_projects_do_not_collide(tmp_path: Path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    with runlock.acquire(str(first)), runlock.acquire(str(second)):
        assert (first / ".forge" / "run.lock").is_file()
        assert (second / ".forge" / "run.lock").is_file()


def test_released_lock_can_be_taken_again(tmp_path: Path) -> None:
    runlock.acquire(str(tmp_path)).release()
    with runlock.acquire(str(tmp_path)):
        pass


def test_holder_details_survive_for_the_message(tmp_path: Path) -> None:
    with runlock.acquire(str(tmp_path)):
        payload = json.loads(
            (tmp_path / ".forge" / "run.lock").read_text(encoding="utf-8"))
    assert payload["pid"] == os.getpid()
    assert payload["started_at"] > 0


def test_a_killed_process_leaves_no_orphaned_lock(tmp_path: Path) -> None:
    """SIGKILL nie zostawia zamku — zwalnia go jądro, nie kod Forge."""
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time\n"
         "sys.path.insert(0, sys.argv[1])\n"
         "from forge import runlock\n"
         "runlock.acquire(sys.argv[2])\n"
         "print('locked', flush=True)\n"
         "time.sleep(60)\n",
         str(Path(__file__).parents[1]), str(tmp_path)],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"
        assert runlock.busy_reason(str(tmp_path))
    finally:
        holder.kill()
        holder.wait(timeout=10)
    assert runlock.busy_reason(str(tmp_path)) == ""


def test_busy_reason_is_empty_for_a_free_project(tmp_path: Path) -> None:
    assert runlock.busy_reason(str(tmp_path)) == ""


def test_busy_reason_does_not_keep_the_lock(tmp_path: Path) -> None:
    runlock.busy_reason(str(tmp_path))
    with runlock.acquire(str(tmp_path)):
        pass


def test_orchestrator_refuses_to_start_on_a_locked_project(tmp_path: Path) -> None:
    brief = tmp_path / "brief.md"
    brief.write_text("cel\n", encoding="utf-8")
    with runlock.acquire(str(tmp_path)):
        result = subprocess.run(
            [sys.executable, "-m", "forge.orchestrate",
             "--non-interactive", "--brief", str(brief),
             "--project", str(tmp_path)],
            text=True, capture_output=True,
            cwd=str(Path(__file__).parents[1]))
    assert result.returncode == 4
    assert "prowadzi już bieg Forge" in result.stderr
