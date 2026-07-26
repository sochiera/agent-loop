from pathlib import Path

from forge import prompts


def test_private_role_prompts_only_allow_short_handoff() -> None:
    tester = prompts.tester_task_prompt(
        "task.md", "pytest",
        handoff="coder reason",
        previous_decision={"status": "red", "reason": "brakuje walidacji"},
        coder_summary="dodano walidację",
        changed_files=["app.py", "tests/test_app.py"],
        task_ledger="[12:00] task-001 r1 tester→red",
    )
    coder = prompts.coder_task_prompt("task.md", "pytest", decision={"status": "red", "reason": "missing"})
    assert "coder reason" in tester
    assert "red" in tester and "brakuje walidacji" in tester
    assert "dodano walidację" in tester
    assert "app.py" in tester and "tests/test_app.py" in tester
    assert "task-001 r1 tester→red" in tester
    assert "zachować, poprawić albo przywrócić" in tester
    assert "Uwagi review rozpoczynają nowy cykl TDD" in tester
    assert "kolekcjonuje się" in tester
    assert "pada na asercji kontraktu" in tester
    assert "błędzie składni/importu/nazwy" in tester
    assert "napraw natychmiast" in tester
    assert "SKIEROWANY DO CIEBIE" in tester
    assert "blocked" in tester
    assert "Decyzja testera" in coder and "transcript" not in coder.lower()
    assert "summary" in coder and "testerowi" in coder
    assert "tester_input_needed" in coder


def test_bootstrap_creates_informational_project_instructions() -> None:
    prompt = prompts.bootstrap_prompt("brief")

    assert "AGENTS.md" in prompt
    assert "CLAUDE.md" in prompt
    assert ".forge/" in prompt
    assert "runtime orkiestratora" in prompt
    assert "kontekst dostajesz w promptcie" in prompt


def test_bootstrap_creates_indexed_documentation_layout() -> None:
    prompt = prompts.bootstrap_prompt("brief")

    assert "docs/ARCHITECTURE/00-INDEX.md" in prompt
    assert "docs/DESIGN/00-INDEX.md" in prompt
    assert "docs/DECISIONS/" in prompt


def test_planner_declares_explicit_task_dependencies() -> None:
    prompt = prompts.plan_batch_prompt(4, 1)

    assert "depends_on" in prompt
    assert "identyfikator" in prompt


def test_planner_contract_owns_outcomes_not_test_design() -> None:
    prompt = prompts.plan_batch_prompt(4, 1)

    assert "Kryteria akceptacji" in prompt
    assert "Publiczny kontrakt" in prompt
    assert "Ścieżki testów" not in prompt
    assert "Test ukierunkowany" not in prompt
    assert "test_globs" not in prompt
    assert "code_globs" not in prompt
    assert '"criteria"' not in prompt
    assert "najwęższy wiarygodny dobór należy do testera" in prompt
    assert "prywatnych helperów" in prompt
    assert "unikalnego ryzyka na granicy systemów" in prompt
    assert "zweryfikuj" in prompt
    assert "uruchomieniem" in prompt
    assert "wynik" in prompt


def test_confirmation_prompt_checks_targeted_gate_criteria_and_test_quality() -> None:
    prompt = prompts.tester_task_prompt(
        "task.md", "pytest -q", confirmation=True,
        suggested_test_cmd="pytest -q tests/test_app.py",
        coder_summary="implemented")

    assert "TURA POTWIERDZAJĄCA" in prompt
    assert "pytest -q tests/test_app.py" in prompt
    assert "czy jest zielona" in prompt
    assert "pozostały nieprzetestowane kryteria akceptacji" in prompt
    assert "mały refaktor bez osłabiania pokrycia" in prompt
    assert "należy do Forge przed commitem" in prompt
    assert "Nie oceniaj jakości implementacji" in prompt
    assert "świeżego reviewera" in prompt


def test_confirmation_wins_over_sticky_regression_from_legacy_checkpoint() -> None:
    prompt = prompts.tester_task_prompt(
        "task.md", "pytest -q", confirmation=True, suite_regression=True,
        coder_summary="naprawiono regresję")

    assert "TURA POTWIERDZAJĄCA" in prompt
    assert "PEŁNA BRAMKA wykryła regresję" not in prompt


def test_tester_owns_targeted_command_and_test_refactor() -> None:
    prompt = prompts.tester_task_prompt("task.md", "pytest -q")

    assert "Samodzielnie wybierz" in prompt
    assert "najwęższą wiarygodną komendę" in prompt
    assert "fallbackiem, nie domyślną komendą" in prompt
    assert "realistyczny defekt" in prompt
    assert "change-detectorów" in prompt
    assert "refaktorować testy i ich wspólną infrastrukturę" in prompt


def test_reviewer_prompt_is_plain_code_review() -> None:
    prompt = prompts.review_task_prompt_kiss(
        "task.md", start_tag="forge/task-start", changed=["app.py"])

    for expected in (
        "błędów zachowania", "SOLID/KISS", "design smells", "duplikacji",
        "nazw, które nie opisują", "testy sprawdzają wartościowe zachowanie",
        "Nie streszczaj diffu",
    ):
        assert expected in prompt
    assert "macierz" not in prompt
    assert "Zwróć wyłącznie JSON" in prompt
    assert '"verdict":"approve"' in prompt
    assert '"verdict":"suggestions"' in prompt
    assert '"verdict":"request_changes"' in prompt
    assert "można bezpiecznie zacommitować" in prompt


def test_suggestions_prompt_can_finalize_or_escalate() -> None:
    prompt = prompts.tester_task_prompt(
        "task.md",
        "pytest -q",
        handoff="uprość helper",
        review_suggestions=True,
        review_notes=["usuń duplikację", "skróć nazwę"],
    )

    assert "zaakceptował bieżący diff z sugestiami" in prompt.lower()
    assert "zastosuj" in prompt and "odrzuć" in prompt
    assert "usuń duplikację; skróć nazwę" in prompt
    assert "finalize" in prompt
    assert "bez ponownego review" in prompt
    assert "świadoma eskalacja" in prompt


def test_agent_prompt_bodies_live_in_separate_files() -> None:
    template_dir = (
        Path(prompts.__file__).parent / "templates"
    )

    expected = {
        "bootstrap.md",
        "planner.md",
        "tester.md",
        "coder.md",
        "reviewer.md",
        "master-system.md",
        "verify-goal.md",
    }
    assert expected <= {path.name for path in template_dir.iterdir()}


def test_planner_reads_small_indexes_and_archive_only_on_demand() -> None:
    prompt = prompts.plan_batch_prompt(4, 1)

    assert "docs/ARCHITECTURE/00-INDEX.md" in prompt
    assert "docs/DESIGN/00-INDEX.md" in prompt
    assert "BACKLOG-ARCHIVE.md" in prompt
    assert "tylko do wglądu na żądanie" in prompt


def test_coder_updates_documentation_through_indexes() -> None:
    prompt = prompts.coder_task_prompt(
        "task.md", "pytest", decision={"status": "red", "reason": "missing"})

    assert "docs/ARCHITECTURE/00-INDEX.md" in prompt
    assert "właściwego pliku wskazanego przez indeks" in prompt
    assert "nowy plik" in prompt and "wpisem w indeksie" in prompt
    assert "bramkę testera" in prompt
    assert "pełną suitę przed commitem uruchamia Forge" in prompt
    assert "tautologiczny" in prompt


def test_every_fifth_batch_requires_one_technical_debt_task() -> None:
    normal = prompts.plan_batch_prompt(4, 1, require_debt=False)
    fifth = prompts.plan_batch_prompt(4, 1, require_debt=True)

    assert "jedno zadanie w tym wsadzie ma być zadaniem długu technicznego" \
        not in normal
    assert "jedno zadanie w tym wsadzie ma być zadaniem długu technicznego" \
        in fifth
