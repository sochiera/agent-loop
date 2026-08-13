"""Budowanie krótkich promptów ról Forge z plików tekstowych."""
from __future__ import annotations

from .render import read_template, render


def verdict_commit(verdict_cmd: str) -> str:
    """Instrukcja zatwierdzania werdyktu skryptem; pusta bez komendy.

    Rola bez narzędzi (tryb cienki) nie ma jak uruchomić skryptu, więc dostaje
    dotychczasowy kontrakt „ostatni blok ```json```" bez martwej instrukcji."""
    return render("verdict-commit.md", VERDICT_CMD=verdict_cmd) if verdict_cmd else ""


def ledger_context(tail_text: str) -> str:
    """Ogon dziennika doklejany na KOŃCU promptu roli; pusty nic nie zmienia.

    Osobna funkcja zamiast slotu w każdym szablonie: dziennik jest treścią
    zmienną, a szablony ról są stabilne — doklejenie na końcu zostawia
    cache'owalny prefiks nienaruszony i pozwala dobrać szerokość okna w
    miejscu wywołania, nie w szablonie.
    """
    return "\n\n" + render("ledger-context.md", LEDGER_TAIL=tail_text) \
        if tail_text.strip() else ""


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
        brief_path: str, test_cmd: str, *, round_number: int = 1,
        budget: int = 1, history: list[str] | None = None) -> str:
    """Prompt recenzji szkieletu; numer rundy i uwagi są częścią kontraktu.

    Świeży recenzent bez historii nie ma jak odróżnić pierwszego spojrzenia od
    czwartego, więc każdą rundę zaczyna od zera i szuka nowego zarzutu. Wtedy
    seria recenzji nie zbiega się do akceptacji, tylko wyczerpuje budżet na
    coraz drobniejszych uwagach — a to kasuje całą pracę bootstrapu.
    """
    notes = [str(note).strip() for note in (history or []) if str(note).strip()]
    return render(
        "bootstrap-architecture-review.md",
        BRIEF_PATH=brief_path,
        TEST_CMD=test_cmd,
        ROUND=round_number,
        BUDGET=budget,
        HISTORY="\n".join(f"- {note}" for note in notes) or "(to pierwsza runda)",
    )


PO_TRIGGERS = ("start", "refill", "brief", "cadence")


def _po_trigger(trigger: str) -> str:
    # `backlog` jest nazwą starego wyzwalacza; przy renderowaniu nowej roli
    # oznacza dokładnie refill.
    trigger = "refill" if trigger == "backlog" else trigger
    if trigger not in PO_TRIGGERS:
        raise ValueError(f"nieznany powód uruchomienia Product Ownera: {trigger!r}")
    return read_template(f"po-trigger-{trigger}.md")


def product_owner_prompt(*, trigger: str, brief_diff: str = "",
                         story_report: str = "", queued_tasks=None,
                         parked: str = "", migration: bool = False,
                         notebook_path: str = "", review_notes=None,
                         max_backlog: int = 6, handoff: str = "") -> str:
    """Prompt PO niesie wyłącznie wejścia, których nie ma w jego plikach."""
    corrections = _corrections("po-corrections.md", review_notes)
    parked_text = render("po-parked.md", PARKED_TEXT=parked) if parked else ""
    handoff_text = render("po-handoff.md", HANDOFF_TEXT=handoff) if handoff.strip() else ""
    migration_text = read_template("po-migration.md") if migration else ""
    notebook = (
        f"Twój notatnik roboczy: `{notebook_path}`. Zwróć wpis w polu `notebook`; "
        "Forge doklei go do pliku."
        if notebook_path else "")
    current = brief_diff or "(brak nowego diffu briefu)"
    report = story_report or "(brak świeżego raportu historyjek)"
    return render(
        "product-owner.md",
        TRIGGER=_po_trigger(trigger),
        BRIEF_CHANGE="Zmiana briefu:\n" + current,
        STORY_REPORT="Raport weryfikatora historyjek:\n" + report,
        QUEUED="; ".join(str(item) for item in (queued_tasks or [])) or "(brak)",
        PARKED=parked_text,
        HANDOFF=handoff_text,
        MIGRATION=migration_text,
        NOTEBOOK=notebook,
        CORRECTIONS=corrections,
        MAX_BACKLOG=max_backlog,
    )


def _story_reasons_block(items) -> str:
    """Lista ``{id, reason}`` dla recenzentki; te deklaracje nie są jeszcze w pliku.

    Forge wykonuje `stories_dropped` i `stories_reopened` dopiero po akceptacji,
    więc na dysku ich w chwili recenzji nie ma. Bez wklejenia ich do promptu
    recenzentka oceniałaby decyzje, których nie widzi.
    """
    lines = [
        f"- {str(item.get('id', '?'))}: {str(item.get('reason', '')).strip()}"
        for item in (items or []) if isinstance(item, dict)
    ]
    return "\n".join(lines) or "(brak)"


def po_review_prompt(result: dict, *, max_backlog: int = 6) -> str:
    return render(
        "po-review.md",
        SUMMARY=str(result.get("summary", "(brak)")),
        GOAL_REACHED="tak" if result.get("goal_reached") else "nie",
        DROPPED=_story_reasons_block(result.get("stories_dropped")),
        REOPENED=_story_reasons_block(result.get("stories_reopened")),
        MAX_BACKLOG=max_backlog,
    )


def po_parse_corrections_prompt(violations: list[str]) -> str:
    return render(
        "po-parse-corrections.md",
        VIOLATIONS="\n".join(f"- {item}" for item in violations),
    )


def verify_stories_prompt(*, stories: str, evidence: str) -> str:
    return render("verify-stories.md", STORIES=stories, EVIDENCE=evidence)


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


# Ile ścieżek zmieszczą „Zmiany od startu zadania". Rola potrzebuje wiedzieć,
# CO ruszyło zadanie, a nie mieć kompletny indeks drzewa: dłuższa lista i tak
# nie jest czytana, za to wchodzi do KAŻDEJ kolejnej tury tego zadania. Bieg,
# w którym ta lista rosła bez ograniczenia, dobił prompt testera do 130 kB.
_CAPSULE_FILE_LIMIT = 30


def _file_list(paths: list[str], limit: int = _CAPSULE_FILE_LIMIT) -> str:
    if len(paths) <= limit:
        return ", ".join(paths)
    return (", ".join(paths[:limit])
            + f" …i {len(paths) - limit} więcej")


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
            # Cykl domykający bez uwag bierze się z zapisu read-only reviewera,
            # nie z sugestii — nazwa tury ma mówić, co rola ma zrobić.
            turn = ("tester / ocena sugestii review" if state.review_notes
                    else "tester / ocena diffu recenzenta i dostawa")
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
        lines.append("Zmiany od startu zadania: " + _file_list(changed_files))
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
        review_notes: list[str] | None = None,
        verdict_cmd: str = "") -> str:
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
        # Cykl domykający bez uwag to zapis read-only reviewera, nie sugestie.
        # Prompt ma opisywać turę, którą rola faktycznie ma odbyć: obietnica
        # „przejrzyj sugestie" bez jednej sugestii to sygnał do szukania czegoś,
        # czego nie ma — a jedna zbędna tura kosztuje tu miliony tokenów.
        template = ("tester-suggestions.md" if review_notes
                    else "tester-review-writeback.md")
        instructions = render(template, FULL_TEST_CMD=full_test_cmd)
    else:
        instructions = render(
            "tester-normal.md", FULL_TEST_CMD=full_test_cmd)
    # `review` znika z cyklu domykającego: recenzja tego diffu już zapadła, a
    # druga tura recenzji potrafi tylko odesłać pracę na kolejne okrążenie.
    statuses = (
        "red|code|finalize|blocked"
        if review_suggestions else "red|code|review|blocked"
    )
    return render(
        "tester.md",
        TASK_FILE=task_file,
        CAPSULE=capsule,
        INSTRUCTIONS=instructions,
        STATUSES=statuses,
        VERDICT=verdict_commit(verdict_cmd),
    )


def coder_task_prompt(
        task_file: str, test_cmd: str, *, decision: dict,
        capsule: str = "", verdict_cmd: str = "") -> str:
    return render(
        "coder.md",
        TASK_FILE=task_file,
        CAPSULE=capsule,
        TEST_CMD=test_cmd,
        VERDICT=verdict_commit(verdict_cmd),
    )


def review_task_prompt_kiss(
        task_file: str, *, start_tag: str, changed: list[str],
        verdict_cmd: str = "") -> str:
    return render(
        "reviewer.md",
        TASK_FILE=task_file,
        START_TAG=start_tag,
        CHANGED=", ".join(changed) or "(brak)",
        VERDICT=verdict_commit(verdict_cmd),
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
        cycle: int, evidence: dict, cycle_dir: str, story_report: str = "",
        **_ignored) -> str:
    return render(
        "verify-goal.md",
        CYCLE=cycle,
        EVIDENCE=evidence,
        CYCLE_DIR=cycle_dir,
        STORY_REPORT=story_report or "(brak)",
    )
