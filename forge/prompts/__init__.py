"""Budowanie krótkich promptów ról Forge z plików tekstowych."""
from __future__ import annotations

from .render import read_template, render


def bootstrap_prompt(brief: str) -> str:
    return render("bootstrap.md", BRIEF=brief)


def bootstrap_architecture_review_prompt(
        brief_path: str, test_cmd: str) -> str:
    return render(
        "bootstrap-architecture-review.md",
        BRIEF_PATH=brief_path,
        TEST_CMD=test_cmd,
    )


def diff_bootstrap_prompt(
        brief_diff: str, *, initial: bool = False,
        queued_tasks: list[str] | None = None) -> str:
    """Synchronizacja zmiany briefu: sam diff, nie dwa pełne dokumenty."""
    return render(
        "diff-bootstrap.md",
        INITIAL=read_template("diff-bootstrap-initial.md") if initial else "",
        DIFF=brief_diff,
        QUEUED="; ".join(queued_tasks or []) or "(brak)",
    )


def plan_batch_prompt(
        batch_size: int, start_index: int, kind: str = "app", *,
        verify_feedback_path: str = "", failure_feedback_path: str = "",
        brief_change_path: str = "", require_debt: bool = False,
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
    brief_change = (
        render(
            "planner-brief-change.md",
            BRIEF_CHANGE_PATH=brief_change_path,
        )
        if brief_change_path else ""
    )
    return render(
        "planner.md",
        KIND=kind,
        BRIEF_CHANGE=brief_change,
        FEEDBACK=feedback,
        FAILURES=failures,
        DEBT=debt,
        BATCH_SIZE=batch_size,
        START_INDEX=f"{start_index:03d}",
    )


def tester_task_prompt(
        task_file: str, full_test_cmd: str, *, suggested_test_cmd: str = "",
        handoff: str = "", previous_decision: dict | None = None,
        coder_summary: str = "", changed_files: list[str] | None = None,
        task_ledger: str = "", resume: bool = False,
        confirmation: bool = False, suite_regression: bool = False,
        review_suggestions: bool = False,
        review_notes: list[str] | None = None) -> str:
    previous = previous_decision or {}
    previous_text = (
        f"{previous.get('status', '(brak)')} — "
        f"{previous.get('reason', '(brak powodu)')}"
    )
    changed_text = ", ".join(changed_files or []) or "(brak)"
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
        SESSION=read_template(
            "session-resume.md" if resume else "session-new.md"),
        TASK_FILE=task_file,
        PREVIOUS_TEXT=previous_text,
        CODER_SUMMARY=coder_summary or "(brak)",
        CHANGED_TEXT=changed_text,
        TASK_LEDGER=task_ledger or "(brak)",
        REVIEW_NOTES=(
            "; ".join(review_notes or [])
            if review_suggestions else "(brak)"
        ),
        INSTRUCTIONS=instructions,
        HANDOFF=handoff or "(brak)",
        STATUSES=statuses,
    )


def coder_task_prompt(
        task_file: str, test_cmd: str, *, decision: dict,
        resume: bool = False) -> str:
    return render(
        "coder.md",
        SESSION=read_template(
            "session-resume.md" if resume else "session-new.md"),
        TASK_FILE=task_file,
        DECISION_STATUS=decision.get("status"),
        DECISION_REASON=decision.get("reason", ""),
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
        round_limit_tasks: list[str] | None = None) -> str:
    """Mistrz kuźni: pilnuje procesu, nie kodu. Widzi wyłącznie dziennik."""
    return (
        master_system_prompt()
        + "\n\n"
        + master_ledger_prompt(ledger_tail, round_limit_tasks)
    )


def master_system_prompt() -> str:
    return read_template("master-system.md")


def master_ledger_prompt(
        ledger_tail: str,
        round_limit_tasks: list[str] | None = None) -> str:
    failures = ", ".join(round_limit_tasks or []) or "(brak)"
    return render(
        "master-ledger.md",
        FAILURES=failures,
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
