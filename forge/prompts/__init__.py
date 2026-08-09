"""Budowanie krótkich promptów ról Forge z plików tekstowych."""
from __future__ import annotations

from .render import read_template, render


def _corrections(name: str, review_notes: list[str] | None) -> str:
    """Uwagi recenzenta doklejane do kolejnej próby; brak uwag nic nie zmienia."""
    notes = "; ".join(note for note in (review_notes or []) if str(note).strip())
    return render(name, NOTES=notes) if notes else ""


def bootstrap_prompt(brief: str, *, review_notes: list[str] | None = None) -> str:
    return render(
        "bootstrap.md",
        BRIEF=brief,
        CORRECTIONS=_corrections("bootstrap-corrections.md", review_notes),
    )


def bootstrap_architecture_review_prompt(
        brief_path: str, test_cmd: str) -> str:
    return render(
        "bootstrap-architecture-review.md",
        BRIEF_PATH=brief_path,
        TEST_CMD=test_cmd,
    )


STEERING_TRIGGERS = ("cadence", "brief", "backlog")


def _steering_trigger(trigger: str, batches: int) -> str:
    if trigger == "cadence":
        return render("diff-bootstrap-trigger-cadence.md", BATCHES=batches)
    if trigger not in STEERING_TRIGGERS:
        raise ValueError(f"nieznany powód przeglądu kierunku: {trigger!r}")
    return read_template(f"diff-bootstrap-trigger-{trigger}.md")


def diff_bootstrap_prompt(
        brief_diff: str = "", *, trigger: str = "cadence", batches: int = 0,
        initial: bool = False, queued_tasks: list[str] | None = None,
        recent: str = "", review_notes: list[str] | None = None) -> str:
    """Przegląd kierunku: sam diff briefu i lista commitów, nie pełne dokumenty.

    Rola sama czyta docs/PROJECT.md i BACKLOG.md, więc prompt niesie wyłącznie
    to, czego nie ma na dysku: powód uruchomienia, zmianę briefu i to, co
    powstało od poprzedniego przeglądu.
    """
    return render(
        "diff-bootstrap.md",
        TRIGGER=_steering_trigger(trigger, batches),
        INITIAL=read_template("diff-bootstrap-initial.md") if initial else "",
        BRIEF_CHANGE=(
            brief_diff or read_template("diff-bootstrap-brief-unchanged.md")),
        RECENT=recent or "(brak nowych commitów)",
        QUEUED="; ".join(queued_tasks or []) or "(brak)",
        CORRECTIONS=_corrections(
            "diff-bootstrap-corrections.md", review_notes),
    )


def diff_bootstrap_review_prompt(
        base: str, *, summary: str = "", goal_reached: bool = False) -> str:
    return render(
        "diff-bootstrap-review.md",
        BASE=base,
        SUMMARY=summary or "(brak)",
        GOAL_REACHED="tak" if goal_reached else "nie",
    )


def plan_batch_prompt(
        batch_size: int, start_index: int, kind: str = "app", *,
        verify_feedback_path: str = "", failure_feedback_path: str = "",
        steering_path: str = "", require_debt: bool = False,
        **_ignored) -> str:
    feedback = (
        render(
            "planner-verification-feedback.md",
            VERIFY_FEEDBACK_PATH=verify_feedback_path,
        )
        if verify_feedback_path else ""
    )
    failures = (
        render(
            "planner-failure-feedback.md",
            FAILURE_FEEDBACK_PATH=failure_feedback_path,
        )
        if failure_feedback_path else ""
    )
    debt = (
        read_template("planner-debt-requirement.md")
        if require_debt else ""
    )
    steering = (
        render("planner-steering.md", STEERING_PATH=steering_path)
        if steering_path else ""
    )
    return render(
        "planner.md",
        KIND=kind,
        STEERING=steering,
        FEEDBACK=feedback,
        FAILURES=failures,
        DEBT=debt,
        BATCH_SIZE=batch_size,
        START_INDEX=f"{start_index:03d}",
    )


def context_capsule(
        state, role: str, *, notebook_text: str = "",
        changed_files: list[str] | None = None, handoff: str = "",
        confirmation: bool = False, suite_regression: bool = False,
        review_suggestions: bool = False, tester_gate: str = "") -> str:
    """Renderuj wyłącznie fakty procesowe potrzebne roli w bieżącej turze."""
    task = state.current_task
    task_id = str(task.get("id", ""))
    task_file = str(task.get("file", ""))
    lines = [
        "KAPSUŁA KONTEKSTU",
        f"Zadanie: {task_id}, runda {state.tdd_round + 1}, plik {task_file}",
    ]
    if role == "tester":
        if confirmation:
            turn = "tester / potwierdzenie po green kodera"
        elif suite_regression:
            turn = "tester / ponowna ocena po czerwonej pełnej bramce"
        elif review_suggestions:
            turn = "tester / ocena sugestii review"
        elif state.review_notes:
            turn = "tester / nowy cykl po uwagach review"
        else:
            turn = "tester / projekt testu i decyzja TDD"
        lines.append(f"Tura: {turn}")
        previous = state.tester_decision
        previous_status = str(previous.get("status", "")).strip()
        previous_reason = str(previous.get("reason", "")).strip()
        if previous_status:
            decision_text = previous_status + (
                f" — {previous_reason}" if previous_reason else "")
            lines.append(f"Ostatnia decyzja testera: {decision_text}")
        if handoff:
            lines.append(f"Handoff do testera: {handoff}")
        if state.review_notes:
            lines.append(
                "Aktywne uwagi review: " + "; ".join(state.review_notes))
    else:
        decision = state.tester_decision
        status = str(decision.get("status", ""))
        reason = str(decision.get("reason", "")).strip()
        lines.append(
            "Tura: koder / "
            + ("implementacja po red" if status == "red"
               else "implementacja decyzji testera"))
        decision_text = status + (f" — {reason}" if reason else "")
        if decision_text:
            lines.append(f"Decyzja testera: {decision_text}")
        command = tester_gate.strip() or str(
            decision.get("command", "")).strip()
        if command:
            lines.append(f"Bramka testera: {command}")
    if changed_files:
        lines.append("Zmiany od startu zadania: " + ", ".join(changed_files))
    # Treść notatnika wklejamy zamiast kazać roli sięgnąć po nią narzędziem:
    # jedna tura narzędziowa kosztuje w tej pętli o dwa rzędy wielkości więcej
    # niż te kilkaset tokenów. Żadna rola nie dostaje ścieżki — obie oddają
    # wpisy polem `notebook` swojej decyzji, a plikiem zarządza Forge. Ścieżka
    # byłaby jedynym powodem, by mimo wszystko sięgnąć na dysk.
    if notebook_text:
        lines.append("Twoje notatki z poprzednich rund (komplet):")
        lines.append(notebook_text)
    return "\n".join(lines)


def tester_task_prompt(
        task_file: str, full_test_cmd: str, *, suggested_test_cmd: str = "",
        capsule: str = "",
        confirmation: bool = False, suite_regression: bool = False,
        review_suggestions: bool = False,
        review_notes: list[str] | None = None) -> str:
    # Potwierdzenie ma pierwszeństwo nad przyklejonym sygnałem regresji ze
    # starych checkpointów. W cyklu sugestii dostaje własny wariant.
    if confirmation and review_suggestions:
        suggested = (
            render(
                "tester-suggested-command.md",
                SUGGESTED_TEST_CMD=suggested_test_cmd,
            )
            if suggested_test_cmd else
            read_template("tester-suggested-command-fallback.md")
        )
        instructions = render(
            "tester-suggestions-confirmation.md",
            SUGGESTED=suggested,
            FULL_TEST_CMD=full_test_cmd,
        )
    elif confirmation:
        suggested = (
            render(
                "tester-suggested-command.md",
                SUGGESTED_TEST_CMD=suggested_test_cmd,
            )
            if suggested_test_cmd else
            read_template("tester-suggested-command-fallback.md")
        )
        instructions = render(
            "tester-confirmation.md",
            SUGGESTED=suggested,
            FULL_TEST_CMD=full_test_cmd,
        )
    elif suite_regression:
        instructions = render(
            "tester-suite-regression.md", FULL_TEST_CMD=full_test_cmd)
    elif review_suggestions:
        instructions = render(
            "tester-suggestions.md", FULL_TEST_CMD=full_test_cmd)
    else:
        instructions = render(
            "tester-normal.md", FULL_TEST_CMD=full_test_cmd)
    statuses = (
        "red|code|review|finalize|blocked"
        if review_suggestions else "red|code|review|blocked"
    )
    return render(
        "tester.md",
        TASK_FILE=task_file,
        CAPSULE=capsule,
        INSTRUCTIONS=instructions,
        STATUSES=statuses,
    )


def coder_task_prompt(
        task_file: str, test_cmd: str, *, decision: dict,
        capsule: str = "") -> str:
    return render(
        "coder.md",
        TASK_FILE=task_file,
        CAPSULE=capsule,
        TEST_CMD=test_cmd,
    )


def review_task_prompt_kiss(
        task_file: str, *, start_tag: str, changed: list[str]) -> str:
    return render(
        "reviewer.md",
        TASK_FILE=task_file,
        START_TAG=start_tag,
        CHANGED=", ".join(changed) or "(brak)",
    )


def master_prompt(
        ledger_tail: str,
        round_limit_tasks: list[str] | None = None,
        task_id: str = "",
        next_role: str = "") -> str:
    """Mistrz kuźni: pilnuje procesu, nie kodu. Widzi wyłącznie dziennik."""
    return (
        master_system_prompt()
        + "\n\n"
        + master_ledger_prompt(ledger_tail, round_limit_tasks,
                               task_id=task_id, next_role=next_role)
    )


def master_system_prompt() -> str:
    return render("master-system.md")


def master_position(task_id: str = "", next_role: str = "") -> str:
    """Gdzie stoi pętla w chwili pytania — inaczej mistrz zgaduje.

    Mistrz jest wołany PRZED turą, więc ostatnim wpisem dziennika jest zawsze
    poprzednia tura. Bez tej informacji brak wpisu tury, która dopiero ma
    ruszyć, wygląda jak urwany cykl i produkuje fałszywe alarmy.
    """
    if not task_id:
        return read_template("master-position-planning.md")
    return render("master-position.md", TASK_ID=task_id,
                  NEXT_ROLE=next_role or "tester")


def master_ledger_prompt(
        ledger_tail: str,
        round_limit_tasks: list[str] | None = None,
        task_id: str = "",
        next_role: str = "",
        plan_sift_streak: int = 0) -> str:
    """Dziennik plus wzorce, których z jego okna nie da się odczytać.

    ``round_limit_tasks`` i ``plan_sift_streak`` są liczone osobno z tego samego
    powodu: jedno zadanie idące na limit rund zajmuje więcej linii niż całe okno
    mistrza, a jeden wsad ośmiu zadań — więcej niż cała pamięć dziennika. Oba
    wzorce są więc z ``compact_tail`` strukturalnie niewidoczne i trzeba mu je
    policzyć, zamiast liczyć na to, że je wypatrzy.
    """
    failures = ", ".join(round_limit_tasks or []) or "(brak)"
    return render(
        "master-ledger.md",
        POSITION=master_position(task_id, next_role),
        FAILURES=failures,
        SIFT_STREAK=str(max(0, int(plan_sift_streak))),
        LEDGER_TAIL=ledger_tail or "(pusty)",
    )


def master_json_schema() -> str:
    return read_template("master-schema.json")


def master_note_suffix(note: str) -> str:
    """Nota mistrza doklejana do promptu roli; pusta nota nic nie zmienia."""
    if not note.strip():
        return ""
    return "\n\n" + render("master-note.md", NOTE=note.strip())


def no_change_rounds_suffix(rounds: int) -> str:
    if rounds < 2:
        return ""
    return "\n\n" + render("no-change-rounds.md", ROUNDS=rounds)


def verify_goal_prompt(
        cycle: int, evidence: dict, cycle_dir: str, **_ignored) -> str:
    return render(
        "verify-goal.md",
        CYCLE=cycle,
        EVIDENCE=evidence,
        CYCLE_DIR=cycle_dir,
    )
