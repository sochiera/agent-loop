import time
from pathlib import Path

from forge.validation import classify_command, run_commands


def test_validation_command_timeout_classification():
    assert classify_command("python3 -m pytest -q") == "short"
    assert classify_command("npm run test:e2e") == "long"
    assert classify_command("curl https://example.test/health") == "long"


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


def test_red_exit_codes_map_to_a_preferred_red_outcome():
    from forge.validation import classify_red_exit_code

    assert classify_red_exit_code(1) == "red"
    assert classify_red_exit_code(0) == "passing"
    assert classify_red_exit_code(5) == "empty"
    assert classify_red_exit_code(2) == "error"
    assert classify_red_exit_code(4) == "error"


def _write_unexecutable_script(path: Path) -> None:
    path.write_text("#!/usr/bin/env bash\necho fallback-ran\n", encoding="utf-8")
    path.chmod(0o644)


def test_validation_retries_unexecutable_script_through_bash(tmp_path: Path):
    _write_unexecutable_script(tmp_path / "run.sh")

    results = run_commands(("timeout 300 ./run.sh --install-only",), tmp_path)

    entry = results[0]
    assert entry["return_code"] == 0
    assert entry["command"] == "timeout 300 ./run.sh --install-only"
    assert entry["mode_fallback"] == {
        "executed_command": "timeout 300 bash ./run.sh --install-only",
        "original_return_code": 126,
    }
    assert "fallback-ran" in entry["output"]


def test_validation_does_not_fallback_when_script_is_executable(tmp_path: Path):
    script = tmp_path / "run.sh"
    _write_unexecutable_script(script)
    script.chmod(0o755)

    results = run_commands(("./run.sh",), tmp_path)

    assert results[0]["return_code"] == 0
    assert "mode_fallback" not in results[0]


def test_validation_does_not_fallback_for_other_failures(tmp_path: Path):
    _write_unexecutable_script(tmp_path / "run.sh")
    (tmp_path / "plain.sh").write_text("#!/usr/bin/env bash\nexit 3\n", encoding="utf-8")
    (tmp_path / "plain.sh").chmod(0o644)

    results = run_commands(("exit 1", "./plain.sh"), tmp_path)

    assert results[0]["return_code"] == 1
    assert "mode_fallback" not in results[0]
    # The fallback reruns the unexecutable script honestly; its real exit code wins.
    assert results[1]["return_code"] == 3
    assert results[1]["mode_fallback"]["original_return_code"] == 126
