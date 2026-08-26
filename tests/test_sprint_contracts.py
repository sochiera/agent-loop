import json
from pathlib import Path

import pytest

from forge.contracts import (
    ContractError,
    parse_candidate_selection,
    parse_iteration_plan,
    parse_iteration_review,
    parse_iteration_test,
    parse_product_owner,
    parse_test_author,
)
from forge.sprint import (
    SPRINT_SCHEDULE,
    assert_sprint_cursor,
    backlog_capacity,
    compact_iteration,
    slot_kind,
)
from forge.prompts import (
    candidate_selection_prompt,
    implementation_prompt,
    iteration_reviewer_prompt,
    product_owner_prompt,
    sprint_planner_prompt,
    test_author_prompt as _test_author_prompt,
    unified_tester_prompt,
)


def story(story_id: str, kind: str, priority: int, criterion: str) -> dict:
    return {
        "id": story_id,
        "kind": kind,
        "title": story_id,
        "user_story": f"As a user I want {story_id} so that it helps.",
        "acceptance_criteria": [criterion],
        "priority": priority,
        "estimated_minutes": 10,
        "status": "ready",
    }


def test_sprint_helpers_enforce_bounds_and_compact_product_history():
    assert tuple(slot_kind(index) for index in range(10)) == SPRINT_SCHEDULE
    assert_sprint_cursor(0)
    assert_sprint_cursor(10)
    for invalid in (-1, 11):
        with pytest.raises(ValueError):
            assert_sprint_cursor(invalid)
    with pytest.raises(ValueError):
        slot_kind(10)

    stories = [story("F01", "feature", 1, "works"), story("C01", "cleanup", 2, "clean")]
    stories[1].pop("estimated_minutes")
    assert backlog_capacity(stories) == {
        "stories": 2,
        "feature": 1,
        "cleanup": 1,
        "estimated_minutes": 10,
    }
    compact = compact_iteration({"id": "I-1", "commit": "abc", "internal": "drop"})
    assert compact["id"] == "I-1"
    assert compact["commit"] == "abc"
    assert "internal" not in compact


def test_role_prompts_preserve_controller_contract_boundaries():
    owner = product_owner_prompt(
        brief="Build it",
        commit="abc",
        previous_backlog=[],
        completed_iterations=[],
        quality_backlog=[],
        evidence_dir=Path("evidence"),
        virtual_display=None,
    )
    planner = sprint_planner_prompt(
        brief="Build it",
        sprint=1,
        sprint_goal="Improve",
        slot=1,
        kind="feature",
        backlog=[],
        quality_backlog=[],
        repository_context="clean",
        environment_context="python3",
    )
    implementation = implementation_prompt(
        plan={"story_id": "F01"}, blocking_findings=[], tester_feedback=[]
    )
    reviewer = iteration_reviewer_prompt(
        plan={"tasks": []}, fingerprint="fingerprint", validation=[], previous_findings=[]
    )
    tester = unified_tester_prompt(
        plan={"public_checks": ["launch"]},
        fingerprint="fingerprint",
        review={"verdict": "accept"},
        validation=[],
        evidence_dir=Path("evidence"),
        virtual_display=None,
    )

    assert "Use your tools extensively" in owner
    assert "BACKLOG CAPACITY CONTRACT" in owner
    assert "ten-iteration sprint" in owner
    assert "numeric priority is lowest" in planner
    assert "acceptance criterion" in planner
    assert "Only the Forge controller may commit" in implementation
    assert "Nits are deliberately absent" in implementation
    assert "will be considered during a cleanup iteration" in reviewer
    assert "Every task result needs concrete evidence" in reviewer
    assert "exercise every public check" in tester
    assert "require another review" in tester


def test_tournament_prompts_carry_clean_code_and_blackbox_boundaries():
    implementation = implementation_prompt(
        plan={"story_id": "F01"},
        blocking_findings=[],
        tester_feedback=[],
        tactic="tdd",
        tests_root="tests/blackbox",
        xfails=[{"nodeid": "tests/blackbox/test_a.py::test_b", "reason": "stuck", "repair_notes": "fix the probe"}],
        borrow=[{"from": "explore", "what": "a parser idea"}],
    )
    assert "CLEAN CODE" in implementation
    assert "test-first" in implementation
    assert "Never modify, rename, delete" in implementation
    assert "tampering disqualifies" in implementation
    assert "KNOWN XFAILS" in implementation
    assert "REVIEWER BORROW GUIDANCE" in implementation

    reviewer = iteration_reviewer_prompt(
        plan={"tasks": []}, fingerprint="fingerprint", validation=[], previous_findings=[]
    )
    assert "CLEAN CODE" in reviewer
    assert "god objects" in reviewer
    assert "nits" in reviewer

    author = _test_author_prompt(
        brief="Build it",
        plan={"public_checks": ["launch"]},
        repository_context="clean",
        environment_context="python3",
    )
    assert "black box" in author
    assert "MUST fail" in author
    assert "xfail" in author
    assert "never its internals" in author

    selection = candidate_selection_prompt(
        plan={"story_id": "F01"},
        candidates=[{"name": "tdd"}, {"name": "explore"}, {"name": "classic"}],
        eligible=("tdd", "explore"),
        tests_root="tests/blackbox",
    )
    assert "Choose exactly one winner" in selection
    assert "guidance only" in selection
    assert "CLEAN CODE" in selection


def author_contract(**overrides) -> dict:
    payload = {
        "tests_root": "tests/blackbox",
        "summary": "covers the public flow",
        "covered": ["the flow works"],
        "xfails": [],
    }
    payload.update(overrides)
    return payload


def test_test_author_contract_requires_a_relative_suite_root():
    parsed = parse_test_author(json.dumps(author_contract()))
    assert parsed["tests_root"] == "tests/blackbox"
    with pytest.raises(ContractError, match="relative path"):
        parse_test_author(json.dumps(author_contract(tests_root="/abs/tests")))
    with pytest.raises(ContractError, match="starting with tests/"):
        parse_test_author(json.dumps(author_contract(tests_root="spec/x")))
    with pytest.raises(ContractError, match="relative path"):
        parse_test_author(json.dumps(author_contract(tests_root="tests/../app")))


def test_test_author_xfails_require_precise_unique_repair_notes():
    xfail = {
        "nodeid": "tests/blackbox/test_a.py::test_b",
        "reason": "harness limitation",
        "repair_notes": "Expose the public flag, then unmark.",
    }
    parsed = parse_test_author(json.dumps(author_contract(xfails=[xfail])))
    assert parsed["xfails"] == [xfail]
    with pytest.raises(ContractError, match="duplicate xfail"):
        parse_test_author(json.dumps(author_contract(xfails=[xfail, xfail])))
    broken = {key: value for key, value in xfail.items() if key != "repair_notes"}
    with pytest.raises(ContractError):
        parse_test_author(json.dumps(author_contract(xfails=[broken])))


def _assessment(score: float = 80) -> dict:
    return {"score": score, "summary": "assessed", "strengths": [], "problems": []}


def selection_contract(**overrides) -> dict:
    payload = {
        "winner": "tdd",
        "reason": "cleanest",
        "candidates": {
            "tdd": _assessment(90),
            "explore": _assessment(50),
            "classic": _assessment(40),
        },
        "borrow": [{"from": "explore", "what": "nicer parser"}],
        "feedback": ["tighten the CLI errors"],
    }
    payload.update(overrides)
    return payload


def test_candidate_selection_contract_bounds_winner_and_assessments():
    submitted = ("tdd", "explore", "classic")
    parsed = parse_candidate_selection(
        json.dumps(selection_contract()), submitted=submitted, eligible=submitted
    )
    assert parsed["winner"] == "tdd"
    assert parsed["borrow"] == [{"from": "explore", "what": "nicer parser"}]

    with pytest.raises(ContractError, match="not a submitted candidate"):
        parse_candidate_selection(
            json.dumps(selection_contract(winner="ghost")),
            submitted=submitted,
            eligible=submitted,
        )
    with pytest.raises(ContractError, match="not an eligible candidate"):
        parse_candidate_selection(
            json.dumps(selection_contract(winner="explore")),
            submitted=submitted,
            eligible=("tdd", "classic"),
        )
    incomplete = selection_contract()
    incomplete["candidates"].pop("classic")
    with pytest.raises(ContractError):
        parse_candidate_selection(
            json.dumps(incomplete), submitted=submitted, eligible=submitted
        )
    with pytest.raises(ContractError, match="0..100"):
        parse_candidate_selection(
            json.dumps(selection_contract(candidates={"tdd": _assessment(140), "explore": _assessment(), "classic": _assessment()})),
            submitted=submitted,
            eligible=submitted,
        )
    with pytest.raises(ContractError, match="unknown candidate"):
        parse_candidate_selection(
            json.dumps(selection_contract(borrow=[{"from": "ghost", "what": "x"}])),
            submitted=submitted,
            eligible=submitted,
        )


def plan(story_id: str, criterion: str, *, nits: list[str] | None = None) -> dict:
    return {
        "story_id": story_id,
        "objective": f"Deliver {story_id}",
        "tasks": [
            {
                "id": "TASK-001",
                "title": "Implement behavior",
                "description": "Implement the selected story",
                "acceptance_criteria": [criterion],
            }
        ],
        "validation_commands": ["python3 -m pytest -q"],
        "public_checks": ["Launch and exercise the primary workflow"],
        "addressed_nit_ids": nits or [],
    }


def test_plan_must_follow_product_owner_priority_and_preserve_scope():
    backlog = [
        story("F01", "feature", 1, "The primary workflow is observable"),
        story("F02", "feature", 2, "The secondary workflow is observable"),
    ]

    with pytest.raises(ContractError, match="priority 1"):
        parse_iteration_plan(
            json.dumps(plan("F02", "The secondary workflow is observable")),
            backlog=backlog,
            required_kind="feature",
        )
    with pytest.raises(ContractError, match="preserve every Product Owner"):
        parse_iteration_plan(
            json.dumps(plan("F01", "A narrower replacement criterion")),
            backlog=backlog,
            required_kind="feature",
        )

    parsed = parse_iteration_plan(
        json.dumps(plan("F01", "The primary workflow is observable")),
        backlog=backlog,
        required_kind="feature",
    )
    assert parsed["story"]["id"] == "F01"


def test_cleanup_plan_can_resolve_only_nits_mapped_to_tasks():
    backlog = [story("C01", "cleanup", 1, "The cleanup is verified")]
    payload = plan("C01", "The cleanup is verified", nits=["NIT-001"])

    with pytest.raises(ContractError, match="mapped by id"):
        parse_iteration_plan(
            json.dumps(payload),
            backlog=backlog,
            required_kind="cleanup",
            available_nit_ids=("NIT-001",),
        )
    payload["tasks"][0]["description"] += " and address NIT-001"
    assert parse_iteration_plan(
        json.dumps(payload),
        backlog=backlog,
        required_kind="cleanup",
        available_nit_ids=("NIT-001",),
    )["addressed_nit_ids"] == ["NIT-001"]


def review_payload(*, verdict: str = "accept", evidence: list[str] | None = None) -> dict:
    return {
        "verdict": verdict,
        "summary": "Reviewed the exact task.",
        "implementation_fingerprint": "fingerprint",
        "task_results": [
            {"task_id": "TASK-001", "verdict": verdict, "evidence": evidence or []}
        ],
        "blocking_findings": [],
        "nits": [],
        "blocker": "",
    }


def test_review_acceptance_requires_evidence_and_green_validation():
    with pytest.raises(ContractError, match="must not be empty"):
        parse_iteration_review(
            json.dumps(review_payload()),
            task_ids=("TASK-001",),
            expected_fingerprint="fingerprint",
        )
    with pytest.raises(ContractError, match="green validation"):
        parse_iteration_review(
            json.dumps(review_payload(evidence=["file.py:10"])),
            task_ids=("TASK-001",),
            expected_fingerprint="fingerprint",
            validation_results=[{"return_code": 1, "timed_out": False}],
        )
    parsed = parse_iteration_review(
        json.dumps(review_payload(evidence=["file.py:10"])),
        task_ids=("TASK-001",),
        expected_fingerprint="fingerprint",
        validation_results=[{"return_code": 0, "timed_out": False}],
    )
    assert parsed["verdict"] == "accept"


def _tester_payload() -> dict:
    return {
        "verdict": "accept",
        "summary": "Acceptance passed.",
        "implementation_fingerprint": "fingerprint",
        "task_results": [
            {"task_id": "TASK-001", "verdict": "accept", "evidence": ["test output"]}
        ],
        "whitebox": {"summary": "green", "checks": ["focused test"], "observations": []},
        "blackbox": {
            "summary": "workflow works",
            "happy_path": "exercised",
            "scenarios": ["Launch and exercise the primary workflow"],
            "evidence": ["workflow transcript"],
            "observations": [],
        },
        "blocking_findings": [],
        "nits": [],
        "blocker": "",
    }


def test_unified_tester_must_cover_every_public_check_with_evidence():
    payload = _tester_payload()
    payload["blackbox"]["scenarios"] = ["A different scenario"]
    with pytest.raises(ContractError, match="every public scenario"):
        parse_iteration_test(
            json.dumps(payload),
            task_ids=("TASK-001",),
            expected_fingerprint="fingerprint",
            validation_results=[{"return_code": 0, "timed_out": False}],
            public_checks=("Launch and exercise the primary workflow",),
        )

    parsed = parse_iteration_test(
        json.dumps(_tester_payload()),
        task_ids=("TASK-001",),
        expected_fingerprint="fingerprint",
        validation_results=[{"return_code": 0, "timed_out": False}],
        public_checks=("Launch and exercise the primary workflow",),
    )
    assert parsed["verdict"] == "accept"


def test_rejections_require_actionable_findings():
    review = review_payload(verdict="reject", evidence=["failure output"])
    with pytest.raises(ContractError, match="actionable blocking finding"):
        parse_iteration_review(
            json.dumps(review),
            task_ids=("TASK-001",),
            expected_fingerprint="fingerprint",
        )

    tester = _tester_payload()
    tester["verdict"] = "reject"
    tester["task_results"][0]["verdict"] = "reject"
    with pytest.raises(ContractError, match="actionable blocking finding"):
        parse_iteration_test(
            json.dumps(tester),
            task_ids=("TASK-001",),
            expected_fingerprint="fingerprint",
            validation_results=[],
        )


def test_product_owner_cannot_reuse_an_accepted_story_id():
    stories = [story(f"F{index:02d}", "feature", index, f"F{index:02d} works") for index in range(1, 9)]
    stories += [story(f"C{index:02d}", "cleanup", index, f"C{index:02d} works") for index in range(1, 5)]
    for item in stories:
        item.pop("status")
    payload = {
        "assessment": {"summary": "inspected", "working": [], "problems": [], "evidence": []},
        "sprint_goal": "Improve the product",
        "stories": stories,
        "retired_story_ids": [],
    }
    with pytest.raises(ContractError, match="cannot be reused"):
        parse_product_owner(json.dumps(payload), accepted_story_ids=("F01",))


def test_product_owner_must_carry_or_retire_only_known_ready_stories():
    stories = [story(f"F{index:02d}", "feature", index, f"F{index:02d} works") for index in range(1, 9)]
    stories += [story(f"C{index:02d}", "cleanup", index, f"C{index:02d} works") for index in range(1, 5)]
    for item in stories:
        item.pop("status")
    payload = {
        "assessment": {"summary": "inspected", "working": [], "problems": [], "evidence": []},
        "sprint_goal": "Improve the product",
        "stories": stories,
        "retired_story_ids": [],
    }

    with pytest.raises(ContractError, match="must be carried or retired"):
        parse_product_owner(json.dumps(payload), previous_ready=("OLD-1",))
    payload["retired_story_ids"] = ["UNKNOWN"]
    with pytest.raises(ContractError, match="only existing ready stories"):
        parse_product_owner(json.dumps(payload), previous_ready=("OLD-1",))


def test_product_owner_backlog_capacity_minimums_are_enforced():
    stories = [story(f"F{index:02d}", "feature", index, "works") for index in range(1, 9)]
    stories += [story(f"C{index:02d}", "cleanup", index, "clean") for index in range(1, 4)]
    for item in stories:
        item.pop("status")
    payload = {
        "assessment": {"summary": "inspected", "working": [], "problems": [], "evidence": []},
        "sprint_goal": "Improve the product",
        "stories": stories,
        "retired_story_ids": [],
    }

    with pytest.raises(ContractError, match="at least 12"):
        parse_product_owner(json.dumps(payload))
