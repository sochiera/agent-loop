import time
from pathlib import Path

from forge.validation import run_commands


def _is_running(pid: int) -> bool:
    stat = Path(f"/proc/{pid}/stat")
    if not stat.exists():
        return False
    return stat.read_text(encoding="utf-8").split()[2] != "Z"


def test_validation_cleans_up_background_processes(tmp_path: Path):
    results = run_commands(
        ("sleep 30 >/dev/null 2>&1 & echo $! > child.pid",),
        tmp_path,
    )
    pid = int((tmp_path / "child.pid").read_text(encoding="utf-8"))

    for _ in range(50):
        if not _is_running(pid):
            break
        time.sleep(0.02)

    assert results[0]["return_code"] == 0
    assert not _is_running(pid)


def test_validation_cleans_up_nested_process_groups(tmp_path: Path):
    results = run_commands(
        (
            "timeout 30s bash -lc 'echo $$ > inner.pid; sleep 30' "
            ">/dev/null 2>&1 & echo $! > wrapper.pid; "
            "while ! test -s inner.pid; do sleep 0.01; done",
        ),
        tmp_path,
    )
    wrapper = int((tmp_path / "wrapper.pid").read_text(encoding="utf-8"))
    inner = int((tmp_path / "inner.pid").read_text(encoding="utf-8"))

    for _ in range(50):
        if not _is_running(wrapper) and not _is_running(inner):
            break
        time.sleep(0.02)

    assert results[0]["return_code"] == 0
    assert not _is_running(wrapper)
    assert not _is_running(inner)


def test_validation_timeout_terminates_nested_process_group(tmp_path: Path):
    started = time.monotonic()
    results = run_commands(
        ("timeout 30s bash -lc 'echo $$ > inner.pid; sleep 30'",),
        tmp_path,
        timeout_seconds=1,
    )
    inner = int((tmp_path / "inner.pid").read_text(encoding="utf-8"))

    assert results[0]["timed_out"] is True
    assert time.monotonic() - started < 3
    assert not _is_running(inner)
