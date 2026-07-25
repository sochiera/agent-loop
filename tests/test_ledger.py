from __future__ import annotations

from pathlib import Path

from forge import ledger


def test_append_then_tail_returns_entries_in_order(tmp_path: Path) -> None:
    ledger.append(str(tmp_path), "plan: utworzono 4 zadania")
    ledger.append(str(tmp_path), "task-001 r1 tester→red")

    text = ledger.tail(str(tmp_path))

    assert "plan: utworzono 4 zadania" in text
    assert text.index("plan:") < text.index("task-001")


def test_entries_are_timestamped(tmp_path: Path) -> None:
    ledger.append(str(tmp_path), "task-001 start")

    line = ledger.tail(str(tmp_path)).splitlines()[0]

    # [HH:MM] prefiks — mistrz musi widzieć tempo procesu, nie tylko kolejność.
    assert line.startswith("[") and "]" in line
    assert line.endswith("task-001 start")


def test_ledger_keeps_only_most_recent_entries(tmp_path: Path) -> None:
    for index in range(ledger.KEEP_LINES + 25):
        ledger.append(str(tmp_path), f"wpis {index}")

    lines = ledger.tail(str(tmp_path)).splitlines()

    assert len(lines) == ledger.KEEP_LINES
    assert "wpis 24" not in lines[0]
    assert lines[-1].endswith(f"wpis {ledger.KEEP_LINES + 24}")


def test_tail_limit_returns_only_last_entries(tmp_path: Path) -> None:
    for index in range(10):
        ledger.append(str(tmp_path), f"wpis {index}")

    lines = ledger.tail(str(tmp_path), limit=3).splitlines()

    assert len(lines) == 3
    assert lines[-1].endswith("wpis 9")


def test_tail_for_task_filters_before_applying_limit(tmp_path: Path) -> None:
    for index in range(10):
        ledger.append(str(tmp_path), f"task-001 r{index} tester→red")
        ledger.append(str(tmp_path), f"task-002 r{index} koder→green")

    lines = ledger.tail_for_task(str(tmp_path), "task-001", limit=5).splitlines()

    assert len(lines) == 5
    assert all("task-001" in line for line in lines)
    assert lines[-1].endswith("task-001 r9 tester→red")


def test_tail_for_task_does_not_match_longer_task_id(tmp_path: Path) -> None:
    ledger.append(str(tmp_path), "task-001 start")
    ledger.append(str(tmp_path), "task-0010 start")

    assert "task-0010" not in ledger.tail_for_task(
        str(tmp_path), "task-001", limit=8)


def test_missing_ledger_reads_as_empty(tmp_path: Path) -> None:
    assert ledger.tail(str(tmp_path)) == ""


def test_lone_surrogate_from_agent_does_not_crash_the_pipeline(tmp_path: Path) -> None:
    """Poprawny JSON agenta potrafi nieść samotny surogat — zapis UTF-8 by
    na nim wybuchł, a dziennik jest tylko telemetrią."""
    ledger.append(str(tmp_path), "task-001 powód: a\ud800b")

    text = ledger.tail(str(tmp_path))
    assert "task-001" in text
    assert "\ud800" not in text


def test_entry_is_flattened_to_a_single_line(tmp_path: Path) -> None:
    ledger.append(str(tmp_path), "task-001 r1 tester→red: linia\ndruga\r\ntrzecia")
    ledger.append(str(tmp_path), "task-002 start")

    lines = ledger.tail(str(tmp_path)).splitlines()

    assert len(lines) == 2
    assert "linia druga trzecia" in lines[0]


def test_entry_length_is_capped(tmp_path: Path) -> None:
    ledger.append(str(tmp_path), "x" * 5000)

    line = ledger.tail(str(tmp_path)).splitlines()[0]

    assert len(line) <= ledger.MAX_ENTRY + 16  # + prefiks [HH:MM]


def test_tail_survives_a_corrupted_ledger_file(tmp_path: Path) -> None:
    runtime = tmp_path / ".forge"
    runtime.mkdir()
    (runtime / "ledger.md").write_bytes(b"[10:00] ok\n\xff\xfe niepoprawne bajty\n")

    assert "ok" in ledger.tail(str(tmp_path))


def test_append_never_raises_on_unwritable_project(tmp_path: Path) -> None:
    # Dziennik jest telemetrią procesu — nigdy nie może wywrócić pętli.
    blocked = tmp_path / "plik-nie-katalog"
    blocked.write_text("x", encoding="utf-8")

    ledger.append(str(blocked), "cokolwiek")

    assert ledger.tail(str(blocked)) == ""
