"""Deterministyczna bramka przed mistrzem — na sztucznych ogonach dziennika.

Bramka jest funkcją czystą z konstrukcji: te testy nie tworzą repozytorium,
nie wołają ``git`` i nie dotykają LLM-a.
"""
from __future__ import annotations

from forge import ledger, master_gate
from forge.master_gate import looks_like_test_path, trigger


def _tail(*lines: str) -> str:
    return "\n".join(f"[10:0{index}] {line}" for index, line in enumerate(lines))


def _for_task(tail: str, task_id: str = "task-001", role: str = "tester") -> str:
    return trigger(tail, [], task_id=task_id, next_role=role)


# --- Warunek 1: ta sama rola, ta sama decyzja, bez zmian ----------------------

def test_two_identical_tester_turns_without_changes_trigger() -> None:
    tail = _tail(
        "task-001 r1 tester→red pliki=bez_zmian: bramka na kontrakt",
        "task-001 r2 tester→red pliki=bez_zmian: bramka na kontrakt",
    )

    assert _for_task(tail) == master_gate.TRIGGER_REPEATED


def test_two_different_roles_without_changes_do_not_trigger() -> None:
    """Warunek mówi „tej samej roli" — naprzemienne tury to rozmowa, nie pętla."""
    tail = _tail(
        "task-001 r1 tester→code pliki=bez_zmian: bez nowej bramki",
        "task-001 r1 koder→tester_input_needed pliki=bez_zmian: pytanie",
    )

    assert _for_task(tail) == ""


def test_same_role_with_a_real_change_does_not_trigger() -> None:
    tail = _tail(
        "task-001 r1 tester→red pliki=bez_zmian: bramka",
        "task-001 r2 tester→red pliki=[tests/test_x.py]: szersza bramka",
    )

    assert _for_task(tail) == ""


# --- Warunek 2: koder ruszył plik testowy ------------------------------------

def test_coder_touching_a_test_file_triggers() -> None:
    tail = _tail("task-001 r1 koder→green pliki=[tests/test_x.py]: zielono")

    assert _for_task(tail) == master_gate.TRIGGER_CODER_TOUCHED_TEST


def test_coder_touching_only_source_does_not_trigger() -> None:
    tail = _tail("task-001 r1 koder→green pliki=[src/x.py]: zielono")

    assert _for_task(tail) == ""


def test_test_path_heuristic_covers_names_and_directories() -> None:
    assert looks_like_test_path("tests/x.py")
    assert looks_like_test_path("src/test_protocol.py")
    assert looks_like_test_path("src/protocol_spec.ts")
    assert looks_like_test_path("app/__tests__/x.tsx")
    assert not looks_like_test_path("src/protocol.py")
    assert not looks_like_test_path("")


# --- Warunek 3: pętla recenzji -----------------------------------------------

def test_two_request_changes_without_a_change_between_them_trigger() -> None:
    tail = _tail(
        "task-001 recenzja→request_changes pliki=bez_zmian: popraw kontrakt",
        "task-001 r2 tester→code pliki=bez_zmian: nie widzę problemu",
        "task-001 recenzja→request_changes pliki=bez_zmian: popraw kontrakt",
    )

    assert _for_task(tail) == master_gate.TRIGGER_REVIEW_LOOP


def test_real_change_between_two_request_changes_breaks_the_loop() -> None:
    tail = _tail(
        "task-001 recenzja→request_changes pliki=bez_zmian: popraw kontrakt",
        "task-001 r2 koder→green pliki=[src/x.py]: poprawione",
        "task-001 recenzja→request_changes pliki=bez_zmian: nadal nie",
    )

    assert _for_task(tail) == ""


# --- Warunek 4: round_limit --------------------------------------------------

def test_round_limit_triggers_only_for_the_planner() -> None:
    tasks = ["task-001", "task-002"]

    assert trigger("", tasks, task_id="", next_role="planner") \
        == master_gate.TRIGGER_ROUND_LIMIT
    assert trigger("", tasks, task_id="") == master_gate.TRIGGER_ROUND_LIMIT
    assert trigger("", tasks, task_id="task-003", next_role="tester") == ""


def test_single_round_limit_task_is_not_enough() -> None:
    assert trigger("", ["task-001"], task_id="", next_role="planner") == ""


# --- Warunek 5: powtórzony odsiew planisty -----------------------------------

def test_repeated_plan_sift_triggers_for_the_planner() -> None:
    """Piąty warunek promptu mistrza musi mieć odpowiednik w bramce — inaczej
    tryb `on` wycisza go dokładnie tam, gdzie W2 wymaga interwencji."""
    assert trigger("", [], task_id="", next_role="planner",
                   plan_sift_streak=2) == master_gate.TRIGGER_PLAN_SIFT


def test_single_plan_sift_is_noise_not_a_trigger() -> None:
    assert trigger("", [], task_id="", next_role="planner",
                   plan_sift_streak=1) == ""


def test_plan_sift_does_not_trigger_mid_task() -> None:
    """Uwaga dotyczy planisty, a w środku zadania nie ma on adresata."""
    assert trigger("", [], task_id="task-001", next_role="tester",
                   plan_sift_streak=5) == ""


def test_every_prompt_rule_has_a_gate_predicate() -> None:
    """Warunek obecny w promptcie, a nieobecny w bramce, jest w trybie `on`
    wyciszany po cichu. Ten test pilnuje odwzorowania 1:1."""
    from forge import prompts

    block = prompts.master_system_prompt().partition(
        "Interweniuj tylko")[2].partition("\n\n")[0]
    rules = [rule for rule in block.split("\n- ")[1:]]
    triggers = {name for name in dir(master_gate) if name.startswith("TRIGGER_")}

    assert len(rules) == len(triggers)


# --- Zakres i odporność ------------------------------------------------------

def test_foreign_task_entries_never_trigger_for_the_active_task() -> None:
    tail = _tail(
        "task-777 r1 tester→red pliki=bez_zmian: cudza bramka",
        "task-777 r2 tester→red pliki=bez_zmian: cudza bramka",
    )

    assert _for_task(tail, task_id="task-001") == ""


def test_empty_ledger_yields_no_trigger_and_no_exception() -> None:
    assert _for_task("") == ""
    assert trigger("", None, task_id="task-001", next_role="tester") == ""


def test_non_turn_entries_are_ignored() -> None:
    tail = _tail(
        "plan: utworzono 6 zadań (task-001…task-006)",
        "task-001 start: Tytuł (standard)",
        "task-001 bramka przed commitem CZERWONA, powrót do testera; ogon: x",
        "task-001 UKOŃCZONE po 2 rundach",
    )

    assert master_gate.parse_tail(tail) == []
    assert _for_task(tail) == ""


def test_compact_tail_keeps_pliki_so_the_gate_still_sees_the_change(
        tmp_path) -> None:
    """Test regresyjny na własność ``ledger._compact_line``: bramka polega na
    tym, że cięcie chroni ``pliki=``, a tnie POWÓD."""
    long_reason = "x" * 400
    paths = ", ".join(f"tests/test_{index}.py" for index in range(4))
    ledger.append(str(tmp_path),
                  f"task-001 r1 koder→green pliki=[{paths}]: {long_reason}")

    compacted = ledger.compact_tail(str(tmp_path))

    assert "pliki=[tests/test_0.py" in compacted
    assert trigger(compacted, [], task_id="task-001", next_role="tester") \
        == master_gate.TRIGGER_CODER_TOUCHED_TEST


def test_truncated_file_list_still_counts_as_a_change() -> None:
    """``_compact_line`` może uciąć zamykający nawias — wpis nadal NIE jest
    `bez_zmian` i bramka nie ma prawa uznać go za turę bez zmian."""
    tail = _tail(
        "task-001 r1 tester→red pliki=bez_zmian: bramka",
        "task-001 r2 tester→red pliki=[src/a.py, src/b.py",
    )

    assert _for_task(tail) == ""
