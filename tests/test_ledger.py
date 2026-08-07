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


def test_master_window_does_not_grow_with_ledger_memory(tmp_path: Path) -> None:
    """`KEEP_LINES` to miejsce na dysku, `MASTER_LINES` to tokeny w KAŻDYM
    wywołaniu roli wołanej co rundę. Powiększanie pamięci dziennika nie ma
    prawa dołożyć ani jednej linii do promptu mistrza."""
    for index in range(ledger.KEEP_LINES):
        ledger.append(str(tmp_path), f"task-001 r{index} tester→red pliki=bez_zmian: x")

    assert len(ledger.compact_tail(str(tmp_path)).splitlines()) == ledger.MASTER_LINES


def test_round_limit_horizon_stays_recent_despite_a_long_ledger(
        tmp_path: Path) -> None:
    """Reguła „planista tnie za grubo" ma opisywać porażki ŚWIEŻE. Na całej
    pamięci dziennika mistrz wypominałby zadania sprzed kilku przebiegów."""
    ledger.append(str(tmp_path), "task-001 PORZUCONE: round_limit: limit 10")
    for index in range(ledger.ROUND_LIMIT_LINES):
        ledger.append(str(tmp_path), f"task-002 r{index} tester→red pliki=bez_zmian: x")
    ledger.append(str(tmp_path), "task-002 PORZUCONE: round_limit: limit 10")

    # Stara porażka nadal jest w pliku — ale poza horyzontem reguły.
    assert "task-001 PORZUCONE" in ledger.tail(str(tmp_path))
    assert ledger.round_limit_tasks(str(tmp_path)) == ["task-002"]


def test_report_denominators_see_further_than_the_master(tmp_path: Path) -> None:
    """To jest właściwy zysk z większej pamięci: mianowniki `$/zadanie` mają
    pokrywać cały przebieg, a nie ułamek jednego wsadu."""
    ledger.append(str(tmp_path), "task-001 UKOŃCZONE po 2 rundach")
    for index in range(ledger.MASTER_LINES * 5):
        ledger.append(str(tmp_path), f"task-002 r{index} koder→green pliki=[a.py]: x")
    ledger.append(str(tmp_path), "task-002 UKOŃCZONE po 3 rundach")

    assert ledger.completed_tasks(str(tmp_path)) == [
        ("task-001", 2), ("task-002", 3)]


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


def test_compact_tail_for_master_limits_lines_and_width(tmp_path: Path) -> None:
    for index in range(30):
        ledger.append(
            str(tmp_path),
            f"task-{index:03d} wpis mistrza " + "x" * 250,
        )

    lines = ledger.compact_tail(str(tmp_path)).splitlines()

    assert len(lines) == 20
    assert all(len(line) <= 120 for line in lines)
    assert "task-010" in lines[0]
    assert "task-029" in lines[-1]


def test_compact_tail_keeps_file_list_and_cuts_the_reason(tmp_path: Path) -> None:
    """Lista plików to jedyny sygnał postępu, jaki mistrz ma — powód jest
    dla niego dodatkiem, więc to powód ma ustąpić przy cięciu."""
    ledger.append(
        str(tmp_path),
        "task-001 r3 koder→green pliki=[app/core.py, app/model.py, "
        "tests/test_core.py]: " + "powód " * 40,
    )

    line = ledger.compact_tail(str(tmp_path)).splitlines()[0]

    assert "tests/test_core.py]" in line
    assert len(line) < 200


def test_round_limit_tasks_are_visible_beyond_the_master_window(
        tmp_path: Path) -> None:
    """Zadanie idące na limit rund zajmuje więcej linii niż całe okno mistrza,
    więc bez osobnego licznika reguła o zbyt grubych zadaniach jest martwa."""
    for task in ("task-001", "task-002"):
        for round_no in range(1, 11):
            ledger.append(str(tmp_path), f"{task} r{round_no} tester→red pliki=bez_zmian: x")
            ledger.append(str(tmp_path), f"{task} r{round_no} koder→green pliki=[a.py]: x")
        ledger.append(
            str(tmp_path),
            f"{task} PORZUCONE: round_limit: zadanie wymaga podziału (limit 10)")

    assert "task-001" not in ledger.compact_tail(str(tmp_path))
    assert ledger.round_limit_tasks(str(tmp_path)) == ["task-001", "task-002"]


def test_round_limit_tasks_ignores_other_failures(tmp_path: Path) -> None:
    ledger.append(str(tmp_path), "task-001 PORZUCONE: tester zwrócił blocked")
    ledger.append(str(tmp_path), "task-002 UKOŃCZONE po 3 rundach")

    assert ledger.round_limit_tasks(str(tmp_path)) == []


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
