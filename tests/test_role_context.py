from forge import prompts


def test_private_role_prompts_only_allow_short_handoff() -> None:
    tester = prompts.tester_task_prompt("task.md", "pytest", handoff="coder reason")
    coder = prompts.coder_task_prompt("task.md", "pytest", decision={"status": "red", "reason": "missing"})
    assert "coder reason" in tester
    assert "Decyzja testera" in coder and "transcript" not in coder.lower()
