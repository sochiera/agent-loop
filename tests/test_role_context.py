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
