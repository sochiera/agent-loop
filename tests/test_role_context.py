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
    assert "zweryfikuj" in prompt
    assert "uruchomieniem" in prompt
    assert "wynik" in prompt


def test_confirmation_prompt_only_checks_suite_and_untested_criteria() -> None:
    prompt = prompts.tester_task_prompt(
        "task.md", "pytest -q", confirmation=True,
        coder_summary="implemented")

    assert "TURA POTWIERDZAJĄCA" in prompt
    assert "czy pakiet jest zielony" in prompt
    assert "czy pozostały nieprzetestowane kryteria akceptacji" in prompt
    assert "Nie oceniaj jakości implementacji" in prompt
    assert "świeżego reviewera" in prompt


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
