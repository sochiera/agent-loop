import json
import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from forge.agents import AgentCancelled, AgentConfigurationFailure, AgentRequest
from forge.catalog import model_family, model_identity
from forge.models import AgentResult, ModelSpec, ROLE_NAMES, RunConfig, RunState, Usage
from forge.gitops import GitWorkspace
from forge.locking import ExecutionLocked, RepositoryExecutionLock
from forge.orchestrator import (
    EVENT_QUEUE_LIMIT,
    ForgeOrchestrator,
    IterationStalled,
    _product_owner_retry_prompt,
    _snapshot_escape,
)
from forge.policy import CHEAP_CODER_POOL, DEEPSEEK, GLM, LUNA, MIMO, load_policy
from forge.sprint import CODER_CANDIDATES, SPRINT_SCHEDULE


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, text=True, check=True, stdout=subprocess.PIPE
    ).stdout.strip()


def repo_and_brief(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "target"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "forge@example.test")
    git(repo, "config", "user.name", "Forge Test")
    (repo / "README.md").write_text("# Product\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    brief = tmp_path / "brief.md"
    brief.write_text("Build a useful product continuously.\n", encoding="utf-8")
    return repo, brief


def config(repo: Path, brief: Path, **changes) -> RunConfig:
    models = {role: ModelSpec.parse("codex:gpt-6-luna:xhigh") for role in ROLE_NAMES}
    return RunConfig(str(repo), str(brief), "main", models, push=False, **changes)


def result(request: AgentRequest, text: str, *, tools: int = 0) -> AgentResult:
    return AgentResult(
        text=text,
        session_id=request.session_id or f"{request.role}-session",
        usage=Usage(input_tokens=10, output_tokens=5),
        elapsed_seconds=0.01,
        raw_output=text,
        tool_calls=tools,
    )


def is_candidate_selection(request: AgentRequest) -> bool:
    return request.role == "reviewer" and "winner" in request.schema.get("properties", {})


def backlog() -> list[dict]:
    stories = []
    for kind, count in (("feature", 8), ("cleanup", 4)):
        prefix = "F" if kind == "feature" else "C"
        for index in range(1, count + 1):
            story_id = f"{prefix}{index:02d}"
            stories.append(
                {
                    "id": story_id,
                    "kind": kind,
                    "title": f"{kind.title()} {index}",
                    "user_story": f"As a user I want {story_id} so that the product improves.",
                    "acceptance_criteria": [f"{story_id} is observable"],
                    "priority": index,
                    "estimated_minutes": 10,
                }
            )
    return stories


def product_owner_json() -> str:
    return json.dumps(
        {
            "assessment": {
                "summary": "The product was launched and inspected.",
                "working": ["base entry point"],
                "problems": ["the backlog remains substantial"],
                "evidence": ["README and public smoke"],
            },
            "sprint_goal": "Deliver a balanced product sprint.",
            "stories": backlog(),
            "retired_story_ids": [],
        }
    )


def fingerprint(prompt: str) -> str:
    matches = re.findall(r"\b[0-9a-f]{64}\b", prompt)
    assert matches
    return matches[0]


def story_from_prompt(prompt: str) -> str:
    match = re.search(r'"story_id":\s*"([FC]\d+)"', prompt)
    assert match, prompt
    return match.group(1)


def test_product_owner_retry_prompt_requires_missing_inspection() -> None:
    missing = _product_owner_retry_prompt(ValueError("no tools"), inspected=False)
    corrected = _product_owner_retry_prompt(ValueError("bad JSON"), inspected=True)

    assert "no product inspection tool call" in missing
    assert "Inspect and exercise the product with tools now" in missing
    assert "inspection remains valid" not in missing
    assert "inspection remains valid" in corrected


class SprintRunner:
    def __init__(self, *, stop_after: int = 10):
        self.stop_after = stop_after
        self.product_owner_calls = 0
        self.accepted = 0
        self.feature_index = 0
        self.cleanup_index = 0
        self.requests: list[AgentRequest] = []
        self.last_story = ""
        self.last_fingerprint = ""
        self._lock = threading.Lock()

    def _record(self, request: AgentRequest) -> None:
        with self._lock:
            self.requests.append(request)

    def run(self, request: AgentRequest) -> AgentResult:
        self._record(request)
        if request.role == "probe":
            return result(request, "ready")
        if request.role == "brain":
            self.product_owner_calls += 1
            if self.product_owner_calls > 1:
                raise AgentCancelled("stop after one complete sprint")
            assert request.access == "inspect"
            assert not (request.cwd / ".git").exists()
            return result(request, product_owner_json(), tools=3)
        if request.role == "planner":
            if self.accepted >= self.stop_after:
                raise AgentCancelled("requested test boundary reached")
            kind = re.search(r"REQUIRED ITERATION TYPE: (feature|cleanup)", request.prompt)
            assert kind
            if kind.group(1) == "feature":
                self.feature_index += 1
                story = f"F{self.feature_index:02d}"
            else:
                self.cleanup_index += 1
                story = f"C{self.cleanup_index:02d}"
            text = json.dumps(
                {
                    "story_id": story,
                    "objective": f"Deliver {story}",
                    "tasks": [
                        {
                            "id": "TASK-001",
                            "title": f"Implement {story}",
                            "description": f"Create an observable {story} artifact.",
                            "acceptance_criteria": [
                                f"{story} is observable",
                                f"{story.lower()}.txt exists",
                            ],
                        }
                    ],
                    "validation_commands": [f"test -s {story.lower()}.txt"],
                    "public_checks": [f"Read {story.lower()}.txt through the public workspace"],
                    "addressed_nit_ids": [],
                }
            )
            return result(request, text)
        if request.role == "test_author":
            return self._author(request)
        if request.role in {"coder_tdd", "coder_explore", "coder_classic"}:
            return self._code(request)
        if request.role == "reviewer":
            if is_candidate_selection(request):
                return self._select(request)
            return self._review(request)
        if request.role == "tester":
            return self._test(request)
        raise AssertionError(request.role)

    def _author(self, request: AgentRequest) -> AgentResult:
        story = story_from_prompt(request.prompt)
        root = request.cwd / "tests" / "blackbox"
        root.mkdir(parents=True, exist_ok=True)
        (root / f"test_{story.lower()}.py").write_text(
            "from pathlib import Path\n\n\n"
            f"def test_{story.lower()}_artifact_is_observable():\n"
            f"    path = Path({story.lower()!r} + '.txt')\n"
            f"    assert path.is_file(), 'artifact is missing'\n"
            f"    assert 'implemented {story}' in path.read_text()\n",
            encoding="utf-8",
        )
        return result(
            request,
            json.dumps(
                {
                    "tests_root": "tests/blackbox",
                    "summary": f"black-box coverage for {story}",
                    "covered": [f"{story} is observable"],
                    "xfails": [],
                }
            ),
            tools=2,
        )

    def _code(self, request: AgentRequest) -> AgentResult:
        story = story_from_prompt(request.prompt)
        self.last_story = story
        name = request.role.removeprefix("coder_")
        others = [item for item in CODER_CANDIDATES if item != name]
        assert all(
            not (request.cwd / f"marker-{story.lower()}-{item}.txt").exists()
            for item in others
        ), "candidate worktrees must stay isolated"
        with (request.cwd / f"{story.lower()}.txt").open("a", encoding="utf-8") as handle:
            handle.write(f"implemented {story}\n")
        (request.cwd / f"marker-{story.lower()}-{name}.txt").write_text(
            name + "\n", encoding="utf-8"
        )
        return result(request, f"Implemented {story} and ran its focused check.", tools=2)

    def _select(self, request: AgentRequest) -> AgentResult:
        match = re.search(r"ELIGIBLE CANDIDATES\n(\[.*?\])\n", request.prompt)
        assert match, request.prompt
        eligible = json.loads(match.group(1))
        submitted = list(
            dict.fromkeys(re.findall(r'"name":\s*"(tdd|explore|classic)"', request.prompt))
        )
        assert set(eligible) <= set(submitted)
        winner = "tdd" if "tdd" in eligible else eligible[0]
        return result(
            request,
            json.dumps(
                {
                    "winner": winner,
                    "reason": "cleanest evidence and smallest diff",
                    "candidates": {
                        name: {
                            "score": 90 if name == winner else 50,
                            "summary": f"{name} assessed",
                            "strengths": ["complete"],
                            "problems": [],
                        }
                        for name in submitted
                    },
                    "borrow": [],
                    "feedback": [],
                }
            ),
        )

    def _review(self, request: AgentRequest) -> AgentResult:
        match = re.search(r'"story_id":\s*"([FC]\d+)"', request.prompt)
        story = match.group(1) if match else self.last_story
        self.last_story = story
        hashes = re.findall(r"\b[0-9a-f]{64}\b", request.prompt)
        if hashes:
            self.last_fingerprint = hashes[0]
        assert (request.cwd / f"{story.lower()}.txt").is_file()
        text = json.dumps(
            {
                "verdict": "accept",
                "summary": "All planned behavior is present.",
                "implementation_fingerprint": self.last_fingerprint,
                "task_results": [
                    {"task_id": "TASK-001", "verdict": "accept", "evidence": ["artifact exists"]}
                ],
                "blocking_findings": [],
                "nits": [],
                "blocker": "",
            }
        )
        return result(request, text)

    def _test(self, request: AgentRequest) -> AgentResult:
        story = story_from_prompt(request.prompt)
        hashes = re.findall(r"\b[0-9a-f]{64}\b", request.prompt)
        if hashes:
            self.last_fingerprint = hashes[0]
        assert (request.cwd / f"{story.lower()}.txt").is_file()
        self.accepted += 1
        text = json.dumps(
            {
                "verdict": "accept",
                "summary": "Automated and public checks pass.",
                "implementation_fingerprint": self.last_fingerprint,
                "task_results": [
                    {"task_id": "TASK-001", "verdict": "accept", "evidence": ["public check"]}
                ],
                "whitebox": {"summary": "green", "checks": ["validation passed"], "observations": []},
                "blackbox": {
                    "summary": "happy path works",
                    "happy_path": "exercised",
                    "scenarios": [f"Read {story.lower()}.txt through the public workspace"],
                    "evidence": [f"{story.lower()}.txt"],
                    "observations": [],
                },
                "blocking_findings": [],
                "nits": [],
                "blocker": "",
            }
        )
        return result(request, text)


def make_orchestrator(tmp_path: Path, runner, **config_changes) -> ForgeOrchestrator:
    repo, brief = repo_and_brief(tmp_path)
    return ForgeOrchestrator(
        config(repo, brief, **config_changes),
        run_id="sprint-run",
        runner=runner,
        state_home=tmp_path / "state",
        check_binaries=False,
    )


def test_full_sprint_uses_fixed_schedule_and_returns_to_fresh_product_owner(tmp_path: Path):
    runner = SprintRunner()
    orchestrator = make_orchestrator(tmp_path, runner)

    state = orchestrator.run()

    assert state.status == "cancelled"
    assert state.cycle == 10
    assert [item["kind"] for item in state.iterations] == list(SPRINT_SCHEDULE)
    assert len(state.completed_sprints) == 1
    assert state.needs_product_owner is True
    assert state.sprint_iteration == 0
    assert runner.product_owner_calls == 2
    assert git(orchestrator.repo, "rev-list", "--count", "HEAD") == "11"
    assert git(orchestrator.repo, "status", "--porcelain") == ""
    assert {item["winner"] for item in state.iterations} == {"tdd"}

    roles = [request.role for request in runner.requests if request.role != "probe"]
    assert roles[0] == "brain"
    assert roles[-1] == "brain"
    counts = {role: roles.count(role) for role in set(roles)}
    assert counts["planner"] == 10
    assert counts["test_author"] == 10
    assert counts["tester"] == 10
    assert counts["reviewer"] == 20
    for role in ("coder_tdd", "coder_explore", "coder_classic"):
        assert counts[role] == 10
    for role in ("planner", "test_author", "coder_tdd", "coder_explore", "coder_classic"):
        calls = [request for request in runner.requests if request.role == role]
        assert all(request.session_id is None for request in calls)
    selections = [
        request
        for request in runner.requests
        if request.role == "reviewer" and is_candidate_selection(request)
    ]
    reviews = [
        request
        for request in runner.requests
        if request.role == "reviewer" and not is_candidate_selection(request)
    ]
    assert len(selections) == 10
    assert all(request.session_id is None for request in selections)
    assert len(reviews) == 10
    # A diversity switch to an independent reviewer drops the stale session once;
    # every later review reuses the reviewer session normally.
    assert all(request.session_id in (None, "reviewer-session") for request in reviews)
    assert sum(1 for request in reviews if request.session_id == "reviewer-session") >= 9


def test_shuffle_coders_redraws_the_pool_at_each_sprint(tmp_path: Path):
    runner = SprintRunner(stop_after=1)
    repo, brief = repo_and_brief(tmp_path)
    pool = [
        "codex:gpt-6-sol:medium",
        "claude:claude-opus-5-5:medium",
        "codex:gpt-6-luna:xhigh",
    ]
    models = {role: ModelSpec.parse("codex:gpt-6-luna:xhigh") for role in ROLE_NAMES}
    for role, selector in zip(CODER_CANDIDATES, pool):
        models[f"coder_{role}"] = ModelSpec.parse(selector)
    orchestrator = ForgeOrchestrator(
        RunConfig(
            str(repo), str(brief), "main", models, push=False, shuffle_coders=True
        ),
        run_id="sprint-run",
        runner=runner,
        state_home=tmp_path / "state",
        check_binaries=False,
    )

    state = orchestrator.run()

    assert state.status == "cancelled"
    drawn = sorted(
        orchestrator.config.models[f"coder_{name}"].display()
        for name in CODER_CANDIDATES
    )
    assert drawn == sorted(pool)
    assert any("shuffled coder pool" in item for item in state.warnings)


def test_shuffle_coders_redraws_three_slots_from_the_cheap_pool(tmp_path: Path):
    runner = SprintRunner(stop_after=1)
    orchestrator = make_orchestrator(
        tmp_path, runner, coder_pool=list(CHEAP_CODER_POOL)
    )

    state = orchestrator.run()

    assert state.status == "cancelled"
    drawn = [orchestrator.config.models[f"coder_{name}"] for name in CODER_CANDIDATES]
    for spec in set(drawn):
        assert drawn.count(spec) <= CHEAP_CODER_POOL.count(spec)
    assert any("shuffled coder pool" in item for item in state.warnings)
    for role in ("brain", "planner", "reviewer", "tester", "test_author"):
        assert orchestrator.config.models[role] not in (DEEPSEEK, MIMO)


def test_shuffle_coders_covers_the_pool_and_skips_disabled_models(tmp_path: Path):
    orchestrator = make_orchestrator(
        tmp_path, SprintRunner(stop_after=0), coder_pool=list(CHEAP_CODER_POOL)
    )
    seen = set()
    luna_counts = set()
    for sprint in range(1, 40):
        orchestrator.state.sprint_number = sprint
        orchestrator._shuffle_coder_pool()
        drawn = [orchestrator.config.models[f"coder_{name}"] for name in CODER_CANDIDATES]
        seen.update(drawn)
        luna_counts.add(drawn.count(LUNA))
    assert seen == {DEEPSEEK, MIMO, GLM, LUNA}
    assert max(luna_counts) >= 2

    orchestrator.state.disabled_models = [model_identity(LUNA)]
    for sprint in range(40, 60):
        orchestrator.state.sprint_number = sprint
        orchestrator._shuffle_coder_pool()
        drawn = {orchestrator.config.models[f"coder_{name}"] for name in CODER_CANDIDATES}
        assert drawn == {DEEPSEEK, MIMO, GLM}


def test_coder_only_replacement_never_staffs_a_shared_role(tmp_path: Path):
    orchestrator = make_orchestrator(tmp_path, SprintRunner(stop_after=0))
    for role in ("coder_tdd", "tester", "reviewer"):
        orchestrator.config.models[role] = GLM

    orchestrator._apply_replacement(GLM, DEEPSEEK)

    assert orchestrator.config.models["coder_tdd"] == DEEPSEEK
    for role in ("tester", "reviewer"):
        replaced = orchestrator.config.models[role]
        assert replaced not in (GLM, DEEPSEEK, MIMO)
        assert orchestrator.policy.allows(replaced, role)


def test_coder_only_model_on_a_staff_role_needs_migration(tmp_path: Path):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text('{"promotion_state": "active"}', encoding="utf-8")
    orchestrator = policy_run_orchestrator(
        tmp_path, policy_run_config(tmp_path, policy_file)
    )
    persist_retired_model(orchestrator, "reviewer", "opencode:mimo-v2.6-flash:xhigh")

    with pytest.raises(RuntimeError, match="off-policy"):
        orchestrator._acquire_execution(recover=True, reload_state=True)

    changed = orchestrator.migrate_models()
    assert list(changed) == ["reviewer"]
    assert orchestrator.config.models["reviewer"] not in (DEEPSEEK, MIMO)


def test_reviewer_switches_to_an_independent_family_after_an_openai_winner(tmp_path: Path):
    runner = SprintRunner(stop_after=1)
    orchestrator = make_orchestrator(tmp_path, runner)

    state = orchestrator.run()

    assert state.status == "cancelled"
    assert model_family(orchestrator.config.models["reviewer"]) != "gpt"
    assert any("reviewer switched" in item for item in state.warnings)


def test_replacement_prefers_an_independent_active_family(tmp_path: Path):
    orchestrator = make_orchestrator(tmp_path, SprintRunner(stop_after=0))
    current = ModelSpec.parse("codex:gpt-6-luna:xhigh")

    replacement = orchestrator._replacement_for("coder_tdd", current)
    assert replacement is not None
    assert model_family(replacement) != "gpt"

    orchestrator.state.disabled_models = [model_identity(replacement)]
    fallback = orchestrator._replacement_for("coder_tdd", current)
    assert fallback is None or model_identity(fallback) != model_identity(replacement)


def test_replacement_uses_the_roster_at_its_pinned_effort(tmp_path: Path):
    orchestrator = make_orchestrator(tmp_path, SprintRunner(stop_after=0))
    policy_file = tmp_path / "inactive-policy.json"
    policy_file.write_text('{"promotion_state": "inactive"}', encoding="utf-8")
    orchestrator.policy = load_policy(str(policy_file))

    replacement = orchestrator._replacement_for(
        "coder_tdd", ModelSpec.parse("opencode:glm-5.3-flash:xhigh")
    )
    assert replacement is not None
    assert replacement in orchestrator.policy.allowed_models()
    assert model_family(replacement) != "glm"


def test_new_run_persists_the_roster_and_policy_snapshot(tmp_path: Path):
    runner = SprintRunner(stop_after=1)
    orchestrator = make_orchestrator(tmp_path, runner)

    orchestrator.run()

    snapshot = orchestrator.state.policy_snapshot
    assert snapshot["promotion_state"] in {"active", "inactive", "unknown"}
    assert snapshot["allowed_models"]
    blob = json.dumps(snapshot).lower()
    assert "token" not in blob and "secret" not in blob and "api_key" not in blob
    config = json.loads((orchestrator.store.root / "config.json").read_text())
    assert set(config["models"]) == set(ROLE_NAMES)


class ProductOwnerCorrectionRunner(SprintRunner):
    def __init__(self):
        super().__init__(stop_after=0)

    def run(self, request: AgentRequest) -> AgentResult:
        if request.role == "brain":
            self.requests.append(request)
            self.product_owner_calls += 1
            if self.product_owner_calls == 1:
                return result(request, '{"assessment":"invalid"}', tools=2)
            return result(request, product_owner_json(), tools=0)
        return super().run(request)


def test_product_owner_tool_inspection_survives_json_contract_correction(tmp_path: Path):
    runner = ProductOwnerCorrectionRunner()
    orchestrator = make_orchestrator(tmp_path, runner)

    state = orchestrator.run()

    assert state.status == "cancelled"
    assert state.backlog_revision == 1
    assert state.sprint_goal == "Deliver a balanced product sprint."
    assert state.product_owner_inspected is False
    assert runner.product_owner_calls == 2


class ReviewRejectRunner(SprintRunner):
    def __init__(self):
        super().__init__(stop_after=1)
        self.review_calls = 0

    def run(self, request: AgentRequest) -> AgentResult:
        if request.role == "reviewer" and not is_candidate_selection(request):
            self._record(request)
            self.review_calls += 1
            rejected = self.review_calls == 1
            text = json.dumps(
                {
                    "verdict": "reject" if rejected else "accept",
                    "summary": "A serious gap remains." if rejected else "The gap is fixed.",
                    "implementation_fingerprint": fingerprint(request.prompt),
                    "task_results": [
                        {
                            "task_id": "TASK-001",
                            "verdict": "reject" if rejected else "accept",
                            "evidence": ["first review" if rejected else "fixed artifact"],
                        }
                    ],
                    "blocking_findings": (
                        [
                            {
                                "id": "REV-001",
                                "summary": "Missing reviewed marker",
                                "evidence": "f01.txt",
                                "suggested_fix": "Append a reviewed marker",
                                "task_ids": ["TASK-001"],
                            }
                        ]
                        if rejected
                        else []
                    ),
                    "nits": ["A tiny naming preference"] if rejected else [],
                    "blocker": "",
                }
            )
            return result(request, text)
        return super().run(request)


def test_reviewer_rejection_returns_to_same_coder_context_and_nits_do_not_block(tmp_path: Path):
    runner = ReviewRejectRunner()
    orchestrator = make_orchestrator(tmp_path, runner)

    state = orchestrator.run()

    assert state.status == "cancelled"
    assert state.cycle == 1
    record = state.iterations[0]
    assert record["winner"] == "tdd"
    assert record["coder_rounds"] == 1
    assert record["review_rounds"] == 2
    assert record["tester_rounds"] == 1
    coder_requests = [item for item in runner.requests if item.role == "coder_tdd"]
    assert len(coder_requests) == 2
    assert coder_requests[0].session_id is None
    assert coder_requests[1].session_id == "coder_tdd-session"
    assert len([item for item in runner.requests if item.role == "tester"]) == 1
    assert any(item["text"] == "A tiny naming preference" for item in state.quality_backlog)


class QARejectRunner(SprintRunner):
    def __init__(self):
        super().__init__(stop_after=1)
        self.test_calls = 0

    def run(self, request: AgentRequest) -> AgentResult:
        if request.role == "tester":
            self._record(request)
            self.test_calls += 1
            rejected = self.test_calls == 1
            if not rejected:
                self.accepted += 1
            text = json.dumps(
                {
                    "verdict": "reject" if rejected else "accept",
                    "summary": "Public failure" if rejected else "Retest passed",
                    "implementation_fingerprint": fingerprint(request.prompt),
                    "task_results": [
                        {
                            "task_id": "TASK-001",
                            "verdict": "reject" if rejected else "accept",
                            "evidence": ["public scenario"],
                        }
                    ],
                    "whitebox": {"summary": "green", "checks": ["validation"], "observations": []},
                    "blackbox": {
                        "summary": "failed" if rejected else "works",
                        "happy_path": "missing" if rejected else "exercised",
                        "scenarios": [f"Read {self.last_story.lower()}.txt through the public workspace"],
                        "evidence": ["public scenario output"] if not rejected else [],
                        "observations": [],
                    },
                    "blocking_findings": (
                        [
                            {
                                "id": "TEST-001",
                                "summary": "Public output is incomplete",
                                "evidence": "scenario output",
                                "suggested_fix": "Complete the output",
                                "task_ids": ["TASK-001"],
                            }
                        ]
                        if rejected
                        else []
                    ),
                    "nits": [],
                    "blocker": "",
                }
            )
            return result(request, text)
        return super().run(request)


def test_tester_rejection_requires_coder_and_reviewer_before_retest(tmp_path: Path):
    runner = QARejectRunner()
    orchestrator = make_orchestrator(tmp_path, runner)

    state = orchestrator.run()

    assert state.status == "cancelled"
    roles = [request.role for request in runner.requests if request.role != "probe"]
    assert roles[:3] == ["brain", "planner", "test_author"]
    assert sorted(roles[3:6]) == ["coder_classic", "coder_explore", "coder_tdd"]
    assert roles[6:9] == ["reviewer", "reviewer", "tester"]
    assert roles[9:-1] == ["coder_tdd", "reviewer", "tester"]
    assert roles[-1] == "planner"
    assert state.iterations[0]["coder_rounds"] == 1
    assert state.iterations[0]["review_rounds"] == 2
    assert state.iterations[0]["tester_rounds"] == 2


class NoProgressRunner(SprintRunner):
    def run(self, request: AgentRequest) -> AgentResult:
        if request.role in {"coder_tdd", "coder_explore", "coder_classic"}:
            self._record(request)
            self.last_story = story_from_prompt(request.prompt)
            return result(request, "No changes were necessary.")
        if request.role == "reviewer" and not is_candidate_selection(request):
            self._record(request)
            text = json.dumps(
                {
                    "verdict": "reject",
                    "summary": "The task is not implemented.",
                    "implementation_fingerprint": fingerprint(request.prompt),
                    "task_results": [
                        {"task_id": "TASK-001", "verdict": "reject", "evidence": ["missing artifact"]}
                    ],
                    "blocking_findings": [
                        {
                            "id": "REV-NO-PROGRESS",
                            "summary": "No implementation exists",
                            "evidence": "missing f01.txt",
                            "suggested_fix": "Implement the planned artifact",
                            "task_ids": ["TASK-001"],
                        }
                    ],
                    "nits": [],
                    "blocker": "",
                }
            )
            return result(request, text)
        return super().run(request)


def test_repeated_unchanged_coder_rounds_stall_without_delivery(tmp_path: Path):
    runner = NoProgressRunner(stop_after=1)
    orchestrator = make_orchestrator(tmp_path, runner, stalled_turns=2)
    base = git(orchestrator.repo, "rev-parse", "HEAD")

    state = orchestrator.run()

    assert state.status == "stalled"
    assert state.cycle == 0
    assert state.active_iteration["phase"] == "fixing"
    assert git(orchestrator.repo, "rev-parse", "HEAD") == base
    assert "no workspace progress" in state.message
    assert "no tool calls" in state.message
    assert state.stalled_recoverable is False
    with pytest.raises(RuntimeError, match="deterministic safety limit"):
        orchestrator.recover()


class ExternallyBlockedReviewRunner(SprintRunner):
    def __init__(self):
        super().__init__(stop_after=1)
        self.blocked_once = False

    def run(self, request: AgentRequest) -> AgentResult:
        if (
            request.role == "reviewer"
            and not is_candidate_selection(request)
            and not self.blocked_once
        ):
            self.blocked_once = True
            self._record(request)
            return result(
                request,
                json.dumps(
                    {
                        "verdict": "blocked",
                        "summary": "The external preview service is unavailable.",
                        "implementation_fingerprint": fingerprint(request.prompt),
                        "task_results": [
                            {
                                "task_id": "TASK-001",
                                "verdict": "accept",
                                "evidence": ["implementation inspected"],
                            }
                        ],
                        "blocking_findings": [],
                        "nits": [],
                        "blocker": "Preview service outage",
                    }
                ),
            )
        return super().run(request)


def test_external_review_blocker_can_recover_without_consuming_round(tmp_path: Path):
    runner = ExternallyBlockedReviewRunner()
    orchestrator = make_orchestrator(tmp_path, runner)

    stalled = orchestrator.run()

    assert stalled.status == "stalled"
    assert stalled.stalled_recoverable is True
    assert stalled.active_iteration["phase"] == "review"
    assert stalled.active_iteration["review_round"] == 0

    recovered = orchestrator.recover()

    assert recovered.status == "cancelled"
    assert recovered.cycle == 1


class ReviewCrashRunner(SprintRunner):
    def run(self, request: AgentRequest) -> AgentResult:
        if request.role == "reviewer" and not is_candidate_selection(request):
            self._record(request)
            raise AgentConfigurationFailure("review process crashed", raw_output="crash")
        return super().run(request)


class ResumeAtReviewRunner(SprintRunner):
    def __init__(self):
        super().__init__(stop_after=1)
        self.accepted = 0

    def run(self, request: AgentRequest) -> AgentResult:
        if request.role == "planner":
            raise AgentCancelled("stop after recovered iteration")
        return super().run(request)


def test_recovery_resumes_review_without_replaying_completed_coder(tmp_path: Path):
    first = ReviewCrashRunner(stop_after=1)
    orchestrator = make_orchestrator(tmp_path, first)

    with pytest.raises(AgentConfigurationFailure):
        orchestrator.run()
    assert orchestrator.state.status == "failed"
    assert orchestrator.state.active_iteration["phase"] == "review"

    resumed_runner = ResumeAtReviewRunner()
    recovered = ForgeOrchestrator.from_existing(
        orchestrator.repo,
        orchestrator.run_id,
        runner=resumed_runner,
        state_home=tmp_path / "state",
        check_binaries=False,
    ).recover()

    assert recovered.status == "cancelled"
    assert recovered.cycle == 1
    assert not any(
        request.role.startswith("coder_") for request in resumed_runner.requests
    )
    assert not any(request.role == "probe" for request in resumed_runner.requests)


class CrashOnceRunner(SprintRunner):
    def __init__(self, target_role: str):
        super().__init__(stop_after=1)
        self.target_role = target_role
        self.crashed = False

    def run(self, request: AgentRequest) -> AgentResult:
        if request.role == self.target_role and not self.crashed:
            self._record(request)
            self.crashed = True
            raise AgentConfigurationFailure(f"{request.role} crashed", raw_output="crash")
        return super().run(request)


@pytest.mark.parametrize("role", ["brain", "planner", "test_author", "tester"])
def test_recovery_continues_from_each_agent_phase_without_losing_iteration(
    tmp_path: Path, role: str
):
    first = CrashOnceRunner(role)
    orchestrator = make_orchestrator(tmp_path, first)

    with pytest.raises(AgentConfigurationFailure, match="crashed"):
        orchestrator.run()
    assert orchestrator.state.status == "failed"

    resumed_runner = SprintRunner(stop_after=1)
    recovered = ForgeOrchestrator.from_existing(
        orchestrator.repo,
        orchestrator.run_id,
        runner=resumed_runner,
        state_home=tmp_path / "state",
        check_binaries=False,
    ).recover()

    assert recovered.status == "cancelled"
    assert recovered.cycle == 1
    assert len(recovered.iterations) == 1
    assert git(orchestrator.repo, "rev-list", "--count", "HEAD") == "2"


def test_tournament_continues_when_one_candidate_dies(tmp_path: Path):
    runner = CrashOnceRunner("coder_tdd")
    orchestrator = make_orchestrator(tmp_path, runner)

    state = orchestrator.run()

    assert state.status == "cancelled"
    assert state.cycle == 1
    record = state.iterations[0]
    assert record["winner"] == "explore"
    assert record["candidates"]["tdd"]["status"] == "failed"
    assert record["candidates"]["explore"]["status"] == "complete"
    assert record["candidates"]["classic"]["status"] == "complete"
    assert any("coder tdd left the tournament" in item for item in state.warnings)
    assert (orchestrator.repo / "marker-f01-explore.txt").is_file()


class CrashingDirtyCoderRunner(SprintRunner):
    def run(self, request: AgentRequest) -> AgentResult:
        if request.role == "coder_classic":
            self._record(request)
            story = story_from_prompt(request.prompt)
            (request.cwd / f"marker-{story.lower()}-classic.txt").write_text(
                "dirty edit before crash\n", encoding="utf-8"
            )
            raise AgentConfigurationFailure("coder classic exploded", raw_output="boom")
        return super().run(request)


def test_failed_dirty_candidate_keeps_its_patch_before_worktrees_are_deleted(
    tmp_path: Path,
):
    runner = CrashingDirtyCoderRunner(stop_after=1)
    orchestrator = make_orchestrator(tmp_path, runner)

    state = orchestrator.run()

    assert state.status == "cancelled"
    assert state.cycle == 1
    assert state.iterations[0]["candidates"]["classic"]["status"] == "failed"
    patch = (
        orchestrator.store.root
        / "sprints/001/iterations/01/candidates/classic/round-1.patch"
    )
    assert patch.is_file()
    assert "marker-f01-classic.txt" in patch.read_text(encoding="utf-8")


class InterruptedDirtyCoderRunner(SprintRunner):
    def __init__(self):
        super().__init__(stop_after=1)
        self.exploded = False

    def run(self, request: AgentRequest) -> AgentResult:
        if request.role == "coder_explore" and not self.exploded:
            self._record(request)
            self.exploded = True
            story = story_from_prompt(request.prompt)
            (request.cwd / f"marker-{story.lower()}-explore.txt").write_text(
                "recovered edits\n", encoding="utf-8"
            )
            raise AgentCancelled("stop mid-tournament")
        return super().run(request)


def test_recovered_interrupted_candidate_keeps_its_patch(tmp_path: Path):
    first = InterruptedDirtyCoderRunner()
    orchestrator = make_orchestrator(tmp_path, first)

    state = orchestrator.run()
    assert state.status == "cancelled"
    assert (
        orchestrator.state.active_iteration["candidates"]["explore"]["status"]
        == "running"
    )

    resumed = SprintRunner(stop_after=1)
    recovered = ForgeOrchestrator.from_existing(
        orchestrator.repo,
        orchestrator.run_id,
        runner=resumed,
        state_home=tmp_path / "state",
        check_binaries=False,
    ).recover()

    assert recovered.status == "cancelled"
    assert recovered.cycle == 1
    assert recovered.iterations[0]["candidates"]["explore"]["status"] == "complete"
    assert not any(
        request.role == "coder_explore" for request in resumed.requests
    )
    patch = (
        orchestrator.store.root
        / "sprints/001/iterations/01/candidates/explore/round-1.patch"
    )
    assert patch.is_file()
    assert "marker-f01-explore.txt" in patch.read_text(encoding="utf-8")


class ConstrainedSelectionRunner(SprintRunner):
    """Mimic constrained decoding that emits every candidate key regardless of deaths."""

    def _select(self, request: AgentRequest) -> AgentResult:
        base = super()._select(request)
        payload = json.loads(base.text)
        for name in CODER_CANDIDATES:
            payload["candidates"].setdefault(
                name,
                {
                    "score": 5,
                    "summary": f"{name} assessed",
                    "strengths": [],
                    "problems": [],
                },
            )
        return result(request, json.dumps(payload))


def test_selection_survives_schema_shaped_assessments_when_a_candidate_dies(
    tmp_path: Path,
):
    class ConstrainedDeadCoderRunner(ConstrainedSelectionRunner):
        def __init__(self):
            super().__init__(stop_after=1)
            self.crashed = False

        def run(self, request: AgentRequest) -> AgentResult:
            if request.role == "coder_tdd" and not self.crashed:
                self._record(request)
                self.crashed = True
                raise AgentConfigurationFailure("coder tdd crashed", raw_output="crash")
            return super().run(request)

    runner = ConstrainedDeadCoderRunner()
    orchestrator = make_orchestrator(tmp_path, runner)

    state = orchestrator.run()

    assert state.status == "cancelled"
    assert state.cycle == 1
    record = state.iterations[0]
    assert record["winner"] in {"explore", "classic"}
    assert record["candidates"]["tdd"]["status"] == "failed"


def test_blackbox_tests_root_must_stay_inside_the_snapshot(tmp_path: Path):
    snapshot = tmp_path / "snapshot"
    outside = tmp_path / "outside"
    (snapshot / "tests" / "blackbox").mkdir(parents=True)
    outside.mkdir()
    (snapshot / "tests" / "blackbox" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (snapshot / "tests" / "blackbox" / "escape").symlink_to(outside)
    (snapshot / "tests" / "linked").symlink_to(outside)

    assert _snapshot_escape(snapshot, "tests/blackbox") == "tests/blackbox/escape"
    assert _snapshot_escape(snapshot, "tests/linked") == "tests/linked"

    (snapshot / "tests" / "blackbox" / "escape").unlink()
    assert _snapshot_escape(snapshot, "tests/blackbox") is None


class EscapingSuiteAuthor(SprintRunner):
    def _author(self, request: AgentRequest) -> AgentResult:
        match = re.search(r'"story_id":\s*"([FC]\d+)"', request.prompt)
        story = match.group(1) if match else self.last_story
        root = request.cwd / "tests" / "blackbox"
        root.mkdir(parents=True, exist_ok=True)
        (root / f"test_{story.lower()}.py").write_text(
            "from pathlib import Path\n\n\ndef test_missing_artifact():\n"
            f"    assert Path({story.lower()!r} + '.txt').is_file()\n",
            encoding="utf-8",
        )
        secret = request.cwd.parent / f"secret-{story.lower()}.md"
        secret.write_text("outside the snapshot\n", encoding="utf-8")
        link = root / "leak"
        if not link.is_symlink():
            link.symlink_to(secret)
        return result(
            request,
            json.dumps(
                {
                    "tests_root": "tests/blackbox",
                    "summary": f"coverage for {story} with an escaping symlink",
                    "covered": [f"{story} is observable"],
                    "xfails": [],
                }
            ),
            tools=1,
        )


def test_test_author_symlink_escape_is_rejected_and_never_copied(tmp_path: Path):
    runner = EscapingSuiteAuthor(stop_after=1)
    orchestrator = make_orchestrator(tmp_path, runner)

    state = orchestrator.run()

    assert state.status == "cancelled"
    assert state.cycle == 1
    assert any("resolves outside the product snapshot" in item for item in state.warnings)
    stored = orchestrator.store.root / "sprints/001/iterations/01/blackbox-tests"
    assert not stored.exists()
    for path in orchestrator.store.root.rglob("*"):
        if path.is_file():
            assert "outside the snapshot" not in path.read_text(
                encoding="utf-8", errors="ignore"
            )


class LateRedAuthor(SprintRunner):
    """Writes a passing suite twice and a RED suite on the third attempt."""

    def __init__(self):
        super().__init__(stop_after=1)
        self.author_calls = 0

    def _author(self, request: AgentRequest) -> AgentResult:
        match = re.search(r'"story_id":\s*"([FC]\d+)"', request.prompt)
        story = match.group(1) if match else self.last_story
        self.last_story = story
        self.author_calls += 1
        root = request.cwd / "tests" / "blackbox"
        root.mkdir(parents=True, exist_ok=True)
        if self.author_calls < 3:
            (root / f"test_{story.lower()}.py").write_text(
                "def test_always_green():\n    assert True\n", encoding="utf-8"
            )
        else:
            (root / f"test_{story.lower()}.py").write_text(
                "from pathlib import Path\n\n\n"
                "def test_missing_artifact():\n"
                f"    assert Path({story.lower()!r} + '.txt').is_file()\n",
                encoding="utf-8",
            )
        return result(
            request,
            json.dumps(
                {
                    "tests_root": "tests/blackbox",
                    "summary": f"black-box coverage for {story}",
                    "covered": [f"{story} is observable"],
                    "xfails": [],
                }
            ),
            tools=1,
        )


def test_interrupted_final_test_author_attempt_is_reclassified_not_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runner = LateRedAuthor()
    orchestrator = make_orchestrator(tmp_path, runner)
    original = ForgeOrchestrator._red_classification
    classifications = {"count": 0}

    def crash_on_third(self, snapshot: Path, tests_root: str):
        classifications["count"] += 1
        if classifications["count"] == 3:
            raise AgentConfigurationFailure(
                "crash while running the suite", raw_output="boom"
            )
        return original(self, snapshot, tests_root)

    monkeypatch.setattr(ForgeOrchestrator, "_red_classification", crash_on_third)
    with pytest.raises(AgentConfigurationFailure, match="crash while running"):
        orchestrator.run()
    author = orchestrator.state.active_iteration["test_author"]
    assert author["attempts"] == 3
    assert author["status"] == "pending"

    resumed = SprintRunner(stop_after=1)
    recovered = ForgeOrchestrator.from_existing(
        orchestrator.repo,
        orchestrator.run_id,
        runner=resumed,
        state_home=tmp_path / "state",
        check_binaries=False,
    ).recover()

    assert recovered.status == "cancelled"
    assert recovered.cycle == 1
    suite = json.loads(
        (
            orchestrator.store.root
            / "sprints/001/iterations/01/test-author/suite.json"
        ).read_text(encoding="utf-8")
    )
    assert suite["status"] == "red"
    assert not [request for request in resumed.requests if request.role == "test_author"]
    assert any(
        "Recovered the interrupted test-author attempt 3" in item
        for item in recovered.warnings
    )


class InflightEditCrashRunner(SprintRunner):
    def __init__(self):
        super().__init__(stop_after=1)
        self.review_calls = 0

    def run(self, request: AgentRequest) -> AgentResult:
        if request.role == "reviewer" and not is_candidate_selection(request):
            self.review_calls += 1
            if self.review_calls == 1:
                self._record(request)
                return result(
                    request,
                    json.dumps(
                        {
                            "verdict": "reject",
                            "summary": "A serious gap remains.",
                            "implementation_fingerprint": fingerprint(request.prompt),
                            "task_results": [
                                {
                                    "task_id": "TASK-001",
                                    "verdict": "reject",
                                    "evidence": ["first review"],
                                }
                            ],
                            "blocking_findings": [
                                {
                                    "id": "REV-001",
                                    "summary": "Missing marker",
                                    "evidence": "f01.txt",
                                    "suggested_fix": "Append a marker",
                                    "task_ids": ["TASK-001"],
                                }
                            ],
                            "nits": [],
                            "blocker": "",
                        }
                    ),
                )
        if request.role == "coder_tdd" and '"REV-001"' in request.prompt:
            self._record(request)
            story = story_from_prompt(request.prompt)
            (request.cwd / f"{story.lower()}.txt").write_text(
                "edit completed before process crash\n", encoding="utf-8"
            )
            raise AgentConfigurationFailure("coder crashed after editing", raw_output="crash")
        return super().run(request)


def test_final_inflight_coder_round_recovers_edits_before_enforcing_round_limit(tmp_path: Path):
    first = InflightEditCrashRunner()
    orchestrator = make_orchestrator(tmp_path, first, max_revision_rounds=2)

    with pytest.raises(AgentConfigurationFailure, match="after editing"):
        orchestrator.run()
    assert orchestrator.state.active_iteration["coder_inflight"] is True
    assert orchestrator.state.active_iteration["coder_round"] == 1
    orchestrator.state.active_iteration["coder_round"] = 2
    orchestrator.store.save_state(orchestrator.state)

    resumed_runner = SprintRunner(stop_after=1)
    recovered = ForgeOrchestrator.from_existing(
        orchestrator.repo,
        orchestrator.run_id,
        runner=resumed_runner,
        state_home=tmp_path / "state",
        check_binaries=False,
    ).recover()

    assert recovered.status == "cancelled"
    assert recovered.cycle == 1
    assert not any(
        request.role.startswith("coder_") for request in resumed_runner.requests
    )


@pytest.mark.parametrize("crash_method", ["prepare_commit", "reconcile_delivery", "cleanup"])
def test_delivery_crash_windows_reconcile_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crash_method: str
):
    runner = SprintRunner(stop_after=1)
    orchestrator = make_orchestrator(tmp_path, runner)
    original = getattr(GitWorkspace, crash_method)
    crashed = False

    def fail_after_success(workspace, *args, **kwargs):
        nonlocal crashed
        value = original(workspace, *args, **kwargs)
        if not crashed:
            crashed = True
            raise AgentConfigurationFailure(
                f"crash after {crash_method}", raw_output="delivery crash"
            )
        return value

    monkeypatch.setattr(GitWorkspace, crash_method, fail_after_success)
    with pytest.raises(AgentConfigurationFailure, match=crash_method):
        orchestrator.run()
    monkeypatch.setattr(GitWorkspace, crash_method, original)

    resumed_runner = SprintRunner(stop_after=0)
    recovered = ForgeOrchestrator.from_existing(
        orchestrator.repo,
        orchestrator.run_id,
        runner=resumed_runner,
        state_home=tmp_path / "state",
        check_binaries=False,
    ).recover()

    assert recovered.status == "cancelled"
    assert recovered.cycle == 1
    assert len(recovered.iterations) == 1
    assert git(orchestrator.repo, "rev-list", "--count", "HEAD") == "2"
    assert git(orchestrator.repo, "status", "--porcelain") == ""


class FalseValidationRunner(SprintRunner):
    def run(self, request: AgentRequest) -> AgentResult:
        if request.role == "planner":
            self.requests.append(request)
            text = json.dumps(
                {
                    "story_id": "F01",
                    "objective": "Fail validation honestly",
                    "tasks": [
                        {
                            "id": "TASK-001",
                            "title": "Implement",
                            "description": "Create the artifact",
                            "acceptance_criteria": ["F01 is observable", "artifact exists"],
                        }
                    ],
                    "validation_commands": ["false"],
                    "public_checks": ["read artifact"],
                    "addressed_nit_ids": [],
                }
            )
            return result(request, text)
        return super().run(request)


def test_review_cannot_accept_failed_mechanical_validation(tmp_path: Path):
    runner = FalseValidationRunner(stop_after=1)
    orchestrator = make_orchestrator(tmp_path, runner)
    base = git(orchestrator.repo, "rev-parse", "HEAD")

    with pytest.raises(RuntimeError, match="reviewer failed its contract"):
        orchestrator.run()

    assert orchestrator.state.status == "failed"
    assert orchestrator.state.cycle == 0
    assert git(orchestrator.repo, "rev-parse", "HEAD") == base


class PassingAuthorRunner(SprintRunner):
    def __init__(self):
        super().__init__(stop_after=1)
        self.author_calls = 0

    def run(self, request: AgentRequest) -> AgentResult:
        if request.role == "test_author":
            self._record(request)
            self.author_calls += 1
            root = request.cwd / "tests" / "blackbox"
            root.mkdir(parents=True, exist_ok=True)
            (root / "test_always_green.py").write_text(
                "def test_always_green():\n    assert True\n",
                encoding="utf-8",
            )
            return result(
                request,
                json.dumps(
                    {
                        "tests_root": "tests/blackbox",
                        "summary": "a suite that cannot fail",
                        "covered": ["nothing meaningful"],
                        "xfails": [],
                    }
                ),
                tools=1,
            )
        return super().run(request)


def test_test_author_without_red_proceeds_with_warning_not_stall(tmp_path: Path):
    runner = PassingAuthorRunner()
    orchestrator = make_orchestrator(tmp_path, runner)

    state = orchestrator.run()

    assert state.status == "cancelled"
    assert state.cycle == 1
    assert runner.author_calls == 3
    assert any("valid RED suite" in item for item in state.warnings)
    suite = json.loads(
        (
            orchestrator.store.root / "sprints/001/iterations/01/test-author/suite.json"
        ).read_text(encoding="utf-8")
    )
    assert suite["status"] == "warned"
    assert "no RED" in suite["gap"]
    assert git(orchestrator.repo, "rev-list", "--count", "HEAD") == "2"


def test_tournament_delivers_only_the_winner_and_deletes_loser_worktrees(tmp_path: Path):
    runner = SprintRunner(stop_after=1)
    orchestrator = make_orchestrator(tmp_path, runner)

    state = orchestrator.run()

    assert state.status == "cancelled"
    assert state.cycle == 1
    repo = orchestrator.repo
    assert "implemented F01" in (repo / "f01.txt").read_text(encoding="utf-8")
    assert (repo / "marker-f01-tdd.txt").is_file()
    assert not (repo / "marker-f01-explore.txt").exists()
    assert not (repo / "marker-f01-classic.txt").exists()
    assert git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert git(repo, "for-each-ref", "refs/heads/forge/sprint-run") == ""
    selection = json.loads(
        (
            orchestrator.store.root / "sprints/001/iterations/01/selection/selection.json"
        ).read_text(encoding="utf-8")
    )
    assert selection["winner"] == "tdd"
    for name in CODER_CANDIDATES:
        patch = (
            orchestrator.store.root
            / f"sprints/001/iterations/01/candidates/{name}/round-1.patch"
        )
        assert patch.is_file()
        assert f"marker-f01-{name}.txt" in patch.read_text(encoding="utf-8")


class TamperingRunner(SprintRunner):
    def run(self, request: AgentRequest) -> AgentResult:
        if request.role == "coder_explore":
            self._record(request)
            story = story_from_prompt(request.prompt)
            with (request.cwd / f"{story.lower()}.txt").open("a", encoding="utf-8") as handle:
                handle.write(f"implemented {story}\n")
            extra = request.cwd / "tests" / "blackbox" / "test_helper.py"
            extra.write_text("def test_helper():\n    assert True\n", encoding="utf-8")
            return result(request, "Implemented, plus a friendly helper test.", tools=2)
        return super().run(request)


def test_coder_tampering_with_the_blackbox_suite_is_disqualified(tmp_path: Path):
    runner = TamperingRunner(stop_after=1)
    orchestrator = make_orchestrator(tmp_path, runner)

    state = orchestrator.run()

    assert state.status == "cancelled"
    assert state.cycle == 1
    record = state.iterations[0]
    assert record["winner"] == "tdd"
    assert record["candidates"]["explore"]["disqualified"] is True
    assert any("tampered with the black-box suite" in item for item in state.warnings)


class AllTamperRunner(SprintRunner):
    def run(self, request: AgentRequest) -> AgentResult:
        if request.role in {"coder_tdd", "coder_explore", "coder_classic"}:
            self._record(request)
            story = story_from_prompt(request.prompt)
            with (request.cwd / f"{story.lower()}.txt").open("a", encoding="utf-8") as handle:
                handle.write(f"implemented {story}\n")
            tamper = (
                request.cwd
                / "tests"
                / "blackbox"
                / f"tamper-{request.role.removeprefix('coder_')}.py"
            )
            tamper.write_text("def test_tamper():\n    assert True\n", encoding="utf-8")
            return result(request, f"Implemented {story} and helped the suite.", tools=2)
        return super().run(request)


def test_fully_disqualified_tournament_is_a_deterministic_dead_end(tmp_path: Path):
    runner = AllTamperRunner(stop_after=1)
    orchestrator = make_orchestrator(tmp_path, runner)

    state = orchestrator.run()

    assert state.status == "stalled"
    assert state.stalled_recoverable is False
    candidates = state.active_iteration["candidates"]
    assert all(candidates[name]["disqualified"] for name in CODER_CANDIDATES)
    assert "no eligible candidate" in state.message
    with pytest.raises(RuntimeError, match="deterministic safety limit"):
        orchestrator.recover()


def test_test_author_records_xfails_with_repair_notes(tmp_path: Path):
    class XfailAuthorRunner(SprintRunner):
        def run(self, request: AgentRequest) -> AgentResult:
            if request.role == "test_author":
                self._record(request)
                story = story_from_prompt(request.prompt)
                root = request.cwd / "tests" / "blackbox"
                root.mkdir(parents=True, exist_ok=True)
                (root / f"test_{story.lower()}.py").write_text(
                    "from pathlib import Path\n\n\n"
                    f"def test_{story.lower()}_artifact():\n"
                    f"    path = Path({story.lower()!r} + '.txt')\n"
                    f"    assert path.is_file() and 'implemented {story}' in path.read_text()\n",
                    encoding="utf-8",
                )
                return result(
                    request,
                    json.dumps(
                        {
                            "tests_root": "tests/blackbox",
                            "summary": "coverage with one stuck expectation",
                            "covered": [f"{story} is observable"],
                            "xfails": [
                                {
                                    "nodeid": f"tests/blackbox/test_{story.lower()}.py::test_{story.lower()}_artifact",
                                    "reason": "the harness cannot observe color yet",
                                    "repair_notes": "Add a color probe to the public CLI, then remove this xfail.",
                                }
                            ],
                        }
                    ),
                    tools=1,
                )
            return super().run(request)

    runner = XfailAuthorRunner(stop_after=1)
    orchestrator = make_orchestrator(tmp_path, runner)

    state = orchestrator.run()

    assert state.status == "cancelled"
    coder_prompts = [
        request.prompt
        for request in runner.requests
        if request.role.startswith("coder_") and request.session_id is None
    ]
    assert len(coder_prompts) == 3
    for prompt in coder_prompts:
        assert "KNOWN XFAILS" in prompt
        assert "Add a color probe" in prompt
        assert "Never modify, rename, delete" in prompt


@pytest.mark.parametrize("schema_version", [1, 2])
def test_legacy_state_is_visible_but_not_recoverable(
    tmp_path: Path, schema_version: int
):
    repo, brief = repo_and_brief(tmp_path)
    models = {role: ModelSpec.parse("codex:gpt-6-luna:xhigh") for role in ROLE_NAMES}
    run_id = "legacy"
    root = repo / ".forge/runs" / run_id
    root.mkdir(parents=True)
    state = RunState(
        run_id=run_id,
        status="failed",
        phase="brain",
        created_at="now",
        updated_at="now",
        config=RunConfig(str(repo), str(brief), "main", models, push=False).to_dict(),
        schema_version=schema_version,
    )
    (root / "state.json").write_text(json.dumps(state.to_dict()), encoding="utf-8")

    orchestrator = ForgeOrchestrator.from_existing(
        repo, run_id, runner=SprintRunner(), state_home=tmp_path / "state", check_binaries=False
    )
    with pytest.raises(RuntimeError, match="legacy Forge runs"):
        orchestrator.recover()


@pytest.mark.parametrize("action", ["pause", "cancel", "interrupt"])
def test_control_save_waits_for_atomic_state_transition(tmp_path: Path, action: str):
    orchestrator = make_orchestrator(tmp_path, SprintRunner(stop_after=0))
    started = threading.Event()
    finished = threading.Event()

    def request_control() -> None:
        started.set()
        if action == "interrupt":
            orchestrator.mark_interrupted("simulated controller interruption")
        else:
            getattr(orchestrator, action)()
        finished.set()

    with orchestrator._state_lock:
        thread = threading.Thread(target=request_control)
        thread.start()
        assert started.wait(1)
        assert finished.wait(0.05) is False
    thread.join(timeout=1)

    assert finished.is_set()
    if action == "pause":
        assert orchestrator.state.paused is True
    elif action == "cancel":
        assert orchestrator.state.cancel_requested is True
    else:
        assert orchestrator.state.status == "failed"


def test_cancel_during_startup_stops_before_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    orchestrator = make_orchestrator(tmp_path, SprintRunner(stop_after=0))
    startup_blocked = threading.Event()
    release_startup = threading.Event()
    preflight_called = threading.Event()
    original_dispatcher = orchestrator._ensure_event_dispatcher
    original_preflight = orchestrator._preflight

    def blocked_dispatcher(generation: int) -> None:
        startup_blocked.set()
        release_startup.wait(2)
        original_dispatcher(generation)

    def observed_preflight() -> None:
        preflight_called.set()
        original_preflight()

    monkeypatch.setattr(orchestrator, "_ensure_event_dispatcher", blocked_dispatcher)
    monkeypatch.setattr(orchestrator, "_preflight", observed_preflight)
    run_thread = threading.Thread(target=orchestrator.run)
    run_thread.start()
    assert startup_blocked.wait(1)

    orchestrator.cancel()
    release_startup.set()
    run_thread.join(timeout=2)

    assert run_thread.is_alive() is False
    assert preflight_called.is_set() is False
    assert orchestrator.state.status == "cancelled"
    assert orchestrator.state.cancel_requested is True


def test_agent_cancellation_during_model_probe_cancels_run(tmp_path: Path):
    class CancelledProbeRunner:
        def allow(self) -> None:
            pass

        def run(self, request: AgentRequest) -> AgentResult:
            assert request.role == "probe"
            raise AgentCancelled("probe cancelled")

    orchestrator = make_orchestrator(tmp_path, CancelledProbeRunner())

    state = orchestrator.run()

    assert state.status == "cancelled"
    assert state.preflight_probed is False


def test_cancel_during_recovery_reload_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    orchestrator = make_orchestrator(tmp_path, SprintRunner(stop_after=0))
    orchestrator.state.status = "failed"
    orchestrator.store.save_state(orchestrator.state)
    load_started = threading.Event()
    release_load = threading.Event()
    cancel_finished = threading.Event()
    preflight_called = threading.Event()
    original_load = orchestrator.store.load_state
    original_dispatcher = orchestrator._ensure_event_dispatcher

    def blocked_load() -> RunState:
        stale_state = original_load()
        load_started.set()
        release_load.wait(2)
        return stale_state

    def dispatcher_after_cancel(generation: int) -> None:
        assert cancel_finished.wait(1)
        original_dispatcher(generation)

    def observed_preflight() -> None:
        preflight_called.set()

    monkeypatch.setattr(orchestrator.store, "load_state", blocked_load)
    monkeypatch.setattr(orchestrator, "_ensure_event_dispatcher", dispatcher_after_cancel)
    monkeypatch.setattr(orchestrator, "_preflight", observed_preflight)
    recovery_thread = threading.Thread(target=orchestrator.recover_failed)
    recovery_thread.start()
    assert load_started.wait(1)

    def request_cancel() -> None:
        orchestrator.cancel()
        cancel_finished.set()

    cancel_thread = threading.Thread(target=request_cancel)
    cancel_thread.start()
    assert cancel_finished.wait(0.05) is False
    release_load.set()
    cancel_thread.join(timeout=1)
    recovery_thread.join(timeout=2)

    assert cancel_finished.is_set()
    assert recovery_thread.is_alive() is False
    assert preflight_called.is_set() is False
    assert orchestrator.state.status == "cancelled"
    assert orchestrator.state.cancel_requested is True


def test_recovery_reloads_config_without_restoring_old_repository_path(tmp_path: Path):
    orchestrator = make_orchestrator(tmp_path, SprintRunner(stop_after=0))
    orchestrator.state.status = "failed"
    persisted = RunConfig.from_dict(orchestrator.state.config)
    persisted.models["brain"] = ModelSpec.parse("opencode:glm-5.3-flash:xhigh")
    orchestrator.state.config = persisted.to_dict()
    orchestrator.store.save_state(orchestrator.state)

    execution_lock, generation = orchestrator._acquire_execution(
        recover=True, reload_state=True
    )
    try:
        assert orchestrator.config.models["brain"].model == "opencode-go/glm-5.3-flash"
        assert orchestrator.config.repo == str(orchestrator.repo)
        assert orchestrator.state.config == orchestrator.config.to_dict()
    finally:
        orchestrator._release_execution(execution_lock, generation)


def test_off_policy_recovery_is_gated_until_explicit_migration(tmp_path: Path):
    orchestrator = make_orchestrator(tmp_path, SprintRunner(stop_after=0))
    orchestrator.state.status = "failed"
    persisted = RunConfig.from_dict(orchestrator.state.config)
    persisted.models["brain"] = ModelSpec.parse("opencode:grok-4.6:high")
    orchestrator.state.config = persisted.to_dict()
    orchestrator.store.save_state(orchestrator.state)

    with pytest.raises(RuntimeError, match="off-policy"):
        orchestrator._acquire_execution(recover=True, reload_state=True)

    migrated = orchestrator.migrate_models()
    assert "brain" in migrated
    assert orchestrator.config.models["brain"].display() in {
        "codex:gpt-6-sol:medium",
        "opencode:opencode-go/glm-5.3-flash:xhigh",
    }
    assert orchestrator.state.model_migrations
    execution_lock, generation = orchestrator._acquire_execution(
        recover=True, reload_state=True
    )
    try:
        assert orchestrator.config.models["brain"] != ModelSpec.parse(
            "opencode:grok-4.6:high"
        )
    finally:
        orchestrator._release_execution(execution_lock, generation)


def policy_run_config(tmp_path: Path, policy_file: Path, **changes) -> RunConfig:
    repo, brief = repo_and_brief(tmp_path)
    models = {role: ModelSpec.parse("codex:gpt-6-luna:xhigh") for role in ROLE_NAMES}
    models.update(changes.pop("models", {}))
    return RunConfig(
        repo=str(repo),
        brief=str(brief),
        branch="main",
        models=models,
        push=False,
        policy_path=str(policy_file),
        **changes,
    )


def policy_run_orchestrator(tmp_path: Path, config: RunConfig) -> ForgeOrchestrator:
    return ForgeOrchestrator(
        config,
        run_id="policy-run",
        runner=SprintRunner(stop_after=0),
        state_home=tmp_path / "state",
        check_binaries=False,
    )


RETIRED_SELECTORS = (
    "codex:gpt-5.6-sol:high",
    "codex:gpt-5.6-terra:high",
    "codex:gpt-5.6-luna:high",
    "opencode:deepseek-v4.1-flash:high",
    "opencode:mimo-v2.6-flash:high",
)


def persist_retired_model(orchestrator: ForgeOrchestrator, role: str, selector: str) -> None:
    orchestrator.state.status = "failed"
    persisted = RunConfig.from_dict(orchestrator.state.config)
    persisted.models[role] = ModelSpec.parse(selector)
    orchestrator.state.config = persisted.to_dict()
    # An old run's audit snapshot still lists the retired roster as allowed.
    orchestrator.state.policy_snapshot = {
        "promotion_state": "active",
        "allowed_models": list(RETIRED_SELECTORS),
    }
    orchestrator.store.save_state(orchestrator.state)


@pytest.mark.parametrize("retired", RETIRED_SELECTORS)
def test_new_run_rejects_the_retired_roster(tmp_path: Path, retired: str):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text('{"promotion_state": "active"}', encoding="utf-8")
    config = policy_run_config(
        tmp_path, policy_file, models={"coder_tdd": ModelSpec.parse(retired)}
    )
    with pytest.raises(ValueError, match="unsupported model|not allowed"):
        policy_run_orchestrator(tmp_path, config)


@pytest.mark.parametrize("retired", RETIRED_SELECTORS)
def test_recovery_rejects_the_retired_roster_until_migrated(tmp_path: Path, retired: str):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text('{"promotion_state": "active"}', encoding="utf-8")
    orchestrator = policy_run_orchestrator(
        tmp_path, policy_run_config(tmp_path, policy_file)
    )
    persist_retired_model(orchestrator, "coder_tdd", retired)

    with pytest.raises(RuntimeError, match="off-policy"):
        orchestrator._acquire_execution(recover=True, reload_state=True)

    changed = orchestrator.migrate_models()
    assert "coder_tdd" in changed
    assert orchestrator.config.models["coder_tdd"] in orchestrator.policy.allowed_models()


@pytest.mark.parametrize("corruption", ["missing", "invalid"])
def test_recovery_rejects_retired_models_when_current_state_is_unusable(
    tmp_path: Path, corruption: str
):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text('{"promotion_state": "inactive"}', encoding="utf-8")
    orchestrator = policy_run_orchestrator(
        tmp_path, policy_run_config(tmp_path, policy_file)
    )
    persist_retired_model(orchestrator, "coder_tdd", "opencode:mimo-v2.6-flash:high")
    if corruption == "missing":
        policy_file.unlink()
    else:
        policy_file.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(RuntimeError, match="off-policy"):
        orchestrator._acquire_execution(recover=True, reload_state=True)

    changed = orchestrator.migrate_models()
    assert "coder_tdd" in changed
    assert orchestrator.config.models["coder_tdd"].model in {
        "gpt-6-luna",
        "claude-opus-5-5",
        "opencode-go/glm-5.3-flash",
    }


def test_failover_never_returns_a_retired_model(tmp_path: Path):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text('{"promotion_state": "active"}', encoding="utf-8")
    orchestrator = policy_run_orchestrator(
        tmp_path, policy_run_config(tmp_path, policy_file)
    )
    orchestrator.config.backup = ModelSpec.parse("opencode:deepseek-v4.1-flash:high")

    replacement = orchestrator._replacement_for(
        "coder_tdd", ModelSpec.parse("opencode:glm-5.3-flash:xhigh")
    )

    assert replacement is not None
    assert replacement in orchestrator.policy.allowed_models()


def test_from_existing_uses_repository_containing_copied_run(tmp_path: Path):
    orchestrator = make_orchestrator(tmp_path, SprintRunner(stop_after=0))
    orchestrator.state.status = "failed"
    orchestrator.store.save_state(orchestrator.state)
    copied_repo = tmp_path / "copied"
    shutil.copytree(orchestrator.repo, copied_repo)

    restored = ForgeOrchestrator.from_existing(
        copied_repo,
        orchestrator.run_id,
        runner=SprintRunner(stop_after=0),
        state_home=tmp_path / "copied-state",
        check_binaries=False,
    )

    assert restored.repo == copied_repo.resolve()
    assert restored.config.repo == str(copied_repo.resolve())
    assert restored.store.root == copied_repo / ".forge" / "runs" / orchestrator.run_id


def test_recover_failed_rejects_active_execution_before_replacing_state(tmp_path: Path):
    orchestrator = make_orchestrator(tmp_path, SprintRunner(stop_after=0))
    orchestrator.state.status = "failed"
    orchestrator.store.save_state(orchestrator.state)
    original_state = orchestrator.state
    execution_lock, generation = orchestrator._acquire_execution(recover=True)
    try:
        with pytest.raises(RuntimeError, match="already has an active execution"):
            orchestrator.recover_failed()
        assert orchestrator.state is original_state
    finally:
        orchestrator._release_execution(execution_lock, generation)


def test_control_after_execution_seal_remains_pending_for_recovery(tmp_path: Path):
    orchestrator = make_orchestrator(tmp_path, SprintRunner(stop_after=0))
    generation = orchestrator._begin_execution(recover=False)
    orchestrator._seal_execution_controls()

    orchestrator.cancel()
    orchestrator._finish_execution(generation)
    next_generation = orchestrator._begin_execution(recover=True)
    try:
        assert orchestrator.state.cancel_requested is True
    finally:
        orchestrator._finish_execution(next_generation)


def test_control_arriving_during_terminal_seal_remains_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    orchestrator = make_orchestrator(tmp_path, SprintRunner(stop_after=0))
    orchestrator.state.preflight_probed = True
    generation = orchestrator._begin_execution(recover=False)
    seal_entered = threading.Event()
    release_seal = threading.Event()
    cancel_finished = threading.Event()
    original_seal = orchestrator._seal_execution_controls

    monkeypatch.setattr(orchestrator, "_preflight", lambda: None)

    def stall() -> RunState:
        raise IterationStalled("terminal boundary")

    def blocked_seal() -> None:
        seal_entered.set()
        release_seal.wait(2)
        original_seal()

    monkeypatch.setattr(orchestrator, "_drive", stall)
    monkeypatch.setattr(orchestrator, "_seal_execution_controls", blocked_seal)
    execution_thread = threading.Thread(
        target=lambda: orchestrator._execute(recover=False)
    )
    execution_thread.start()
    assert seal_entered.wait(1)

    def request_cancel() -> None:
        orchestrator.cancel()
        cancel_finished.set()

    cancel_thread = threading.Thread(target=request_cancel)
    cancel_thread.start()
    assert cancel_finished.wait(0.05) is False
    release_seal.set()
    execution_thread.join(timeout=1)
    cancel_thread.join(timeout=1)
    orchestrator._finish_execution(generation)

    assert cancel_finished.is_set()
    next_generation = orchestrator._begin_execution(recover=True)
    try:
        assert orchestrator.state.cancel_requested is True
    finally:
        orchestrator._finish_execution(next_generation)


def test_stale_controller_cannot_overwrite_cross_process_owner_state(tmp_path: Path):
    orchestrator = make_orchestrator(tmp_path, SprintRunner(stop_after=0))
    orchestrator.state.status = "running"
    orchestrator.store.save_state(orchestrator.state)
    stale = ForgeOrchestrator.from_existing(
        orchestrator.repo,
        orchestrator.run_id,
        runner=SprintRunner(stop_after=0),
        state_home=tmp_path / "stale-state",
        check_binaries=False,
    )
    owner_lock = RepositoryExecutionLock(orchestrator.repo, "main", "owner")

    with owner_lock:
        orchestrator.state.cycle = 7
        orchestrator.store.save_state(orchestrator.state)
        with pytest.raises(ExecutionLocked, match="another Forge execution"):
            stale.cancel()

    persisted = orchestrator.store.load_state()
    assert persisted.cycle == 7
    assert persisted.cancel_requested is False

    stale.cancel()
    persisted = orchestrator.store.load_state()
    assert persisted.cycle == 7
    assert persisted.cancel_requested is True


def test_event_callback_can_request_control_without_lock_inversion(tmp_path: Path):
    repo, brief = repo_and_brief(tmp_path)
    holder = {}
    callback_started = threading.Event()
    callback_finished = threading.Event()

    def callback(event: dict) -> None:
        if event.get("message") != "callback-control":
            return
        callback_started.set()
        holder["orchestrator"].cancel()
        callback_finished.set()

    orchestrator = ForgeOrchestrator(
        config(repo, brief),
        run_id="callback-run",
        runner=SprintRunner(stop_after=0),
        on_event=callback,
        state_home=tmp_path / "state",
        check_binaries=False,
    )
    holder["orchestrator"] = orchestrator
    generation = orchestrator._begin_execution(recover=False)
    control_held = threading.Event()

    def overlapping_pause() -> None:
        with orchestrator._control:
            control_held.set()
            orchestrator.pause()

    with orchestrator._state_lock:
        pause_thread = threading.Thread(target=overlapping_pause)
        pause_thread.start()
        assert control_held.wait(1)
        orchestrator._save("callback-control")
        assert callback_started.wait(1)
        assert callback_finished.is_set() is False

    pause_thread.join(timeout=1)
    assert pause_thread.is_alive() is False
    assert callback_finished.wait(1)
    assert orchestrator.state.cancel_requested is True
    orchestrator._finish_execution(generation)
    orchestrator._shutdown_event_dispatcher()


def test_event_dispatch_is_bounded_and_drains_on_shutdown(tmp_path: Path):
    repo, brief = repo_and_brief(tmp_path)
    callback_started = threading.Event()
    release_callback = threading.Event()
    seen: list[int] = []

    def slow_callback(event: dict) -> None:
        callback_started.set()
        release_callback.wait(2)
        seen.append(int(event["sequence"]))

    orchestrator = ForgeOrchestrator(
        config(repo, brief),
        run_id="bounded-events",
        runner=SprintRunner(stop_after=0),
        on_event=slow_callback,
        state_home=tmp_path / "state",
        check_binaries=False,
    )
    generation = orchestrator._begin_execution(recover=False)
    orchestrator._queue_event({"sequence": 0})
    assert callback_started.wait(1)
    for sequence in range(1, EVENT_QUEUE_LIMIT * 2):
        orchestrator._queue_event({"sequence": sequence})
    with orchestrator._event_lock:
        assert orchestrator._event_queue is not None
        assert orchestrator._event_queue.qsize() <= EVENT_QUEUE_LIMIT

    release_callback.set()
    orchestrator._finish_execution(generation)
    orchestrator._shutdown_event_dispatcher()

    assert EVENT_QUEUE_LIMIT * 2 - 1 in seen
    assert orchestrator._event_thread is None
    assert orchestrator._event_queue is None


def test_blocked_event_callback_quarantines_same_object_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo, brief = repo_and_brief(tmp_path)
    callback_started = threading.Event()
    release_callback = threading.Event()
    holder = {}

    def blocked_callback(_event: dict) -> None:
        callback_started.set()
        release_callback.wait(2)
        holder["orchestrator"].cancel()

    orchestrator = ForgeOrchestrator(
        config(repo, brief),
        run_id="blocked-callback",
        runner=SprintRunner(stop_after=0),
        on_event=blocked_callback,
        state_home=tmp_path / "state",
        check_binaries=False,
    )
    holder["orchestrator"] = orchestrator
    generation = orchestrator._begin_execution(recover=False)
    monkeypatch.setattr("forge.orchestrator.EVENT_SHUTDOWN_TIMEOUT_SECONDS", 0.05)
    orchestrator._queue_event({"kind": "state", "message": "old execution"})
    assert callback_started.wait(1)

    orchestrator._finish_execution(generation)
    orchestrator._shutdown_event_dispatcher()

    assert orchestrator._event_thread is not None
    assert orchestrator._event_thread.is_alive()
    orchestrator.state.status = "failed"
    orchestrator.store.save_state(orchestrator.state)
    with pytest.raises(RuntimeError, match="previous event callback"):
        orchestrator.recover()

    release_callback.set()
    orchestrator._event_thread.join(timeout=1)
    assert orchestrator.state.cancel_requested is False
    new_generation = orchestrator._begin_execution(recover=False)
    orchestrator._ensure_event_dispatcher(new_generation)
    assert orchestrator._event_thread is not None
    assert orchestrator._event_thread.is_alive()
    orchestrator._finish_execution(new_generation)
    orchestrator._shutdown_event_dispatcher()


def test_recovery_deduplicates_partially_persisted_finalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runner = SprintRunner(stop_after=1)
    orchestrator = make_orchestrator(tmp_path, runner)
    original = GitWorkspace.cleanup

    def crash_after_cleanup(workspace):
        original(workspace)
        raise AgentConfigurationFailure("crash during finalization", raw_output="crash")

    monkeypatch.setattr(GitWorkspace, "cleanup", crash_after_cleanup)
    with pytest.raises(AgentConfigurationFailure, match="finalization"):
        orchestrator.run()
    monkeypatch.setattr(GitWorkspace, "cleanup", original)

    acceptance_path = (
        orchestrator.store.root / "sprints/001/iterations/01/acceptance.json"
    )
    record = json.loads(acceptance_path.read_text(encoding="utf-8"))
    orchestrator.state.iterations.append(record)
    orchestrator.state.cycle = 1
    orchestrator.state.sprint_iteration = 1
    orchestrator.store.save_state(orchestrator.state)

    recovered = ForgeOrchestrator.from_existing(
        orchestrator.repo,
        orchestrator.run_id,
        runner=SprintRunner(stop_after=0),
        state_home=tmp_path / "state",
        check_binaries=False,
    ).recover()

    assert recovered.status == "cancelled"
    assert recovered.cycle == 1
    assert len(recovered.iterations) == 1
    assert recovered.sprint_iteration == 1


class CancelProductOwnerRunner:
    def run(self, request: AgentRequest) -> AgentResult:
        assert request.role == "brain"
        raise AgentCancelled("stop before reassessment")


def test_recovery_reconstructs_partial_sprint_close_before_starting_another_slot(
    tmp_path: Path,
):
    orchestrator = make_orchestrator(tmp_path, SprintRunner())
    state = orchestrator.run()
    assert len(state.iterations) == 10
    assert len(state.completed_sprints) == 1

    state.completed_sprints = []
    state.sprint_iteration = 0
    state.needs_product_owner = False
    state.status = "failed"
    state.phase = "planning"
    orchestrator.store.save_state(state)

    recovered = ForgeOrchestrator.from_existing(
        orchestrator.repo,
        orchestrator.run_id,
        runner=CancelProductOwnerRunner(),
        state_home=tmp_path / "state",
        check_binaries=False,
    ).recover()

    assert recovered.status == "cancelled"
    assert len(recovered.iterations) == 10
    assert len(recovered.completed_sprints) == 1
    assert recovered.sprint_iteration == 0
    assert recovered.needs_product_owner is True

