import json
import re
import subprocess
import threading
from pathlib import Path

import pytest

from forge.agents import AgentFailure, AgentRequest
from forge.models import AgentResult, ModelSpec, ROLE_NAMES, RunConfig, Usage
from forge.policy import CHEAP_CODER_POOL, SOL, load_policy
from forge.prompts import CODER_TACTICS
from forge.swarm import SWARM_AGENTS_CAP, SwarmController, SwarmGitError


CLOSING_STATUS = ("pending", "in_progress", "done", "conflict", "dropped")
POOL_IDENTITIES = {spec.display() for spec in CHEAP_CODER_POOL}


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


def transfer(repo: Path, brief: Path, **changes: object) -> RunConfig:
    models = {role: ModelSpec.parse("codex:gpt-6-sol:medium") for role in ROLE_NAMES}
    return RunConfig(str(repo), str(brief), "main", models, push=False, **changes)


def result(request: AgentRequest, text: str, *, tools: int = 0) -> AgentResult:
    return AgentResult(
        text=text,
        session_id=None,
        usage=Usage(input_tokens=10, output_tokens=5),
        elapsed_seconds=0.01,
        raw_output=text,
        tool_calls=tools,
    )


def swarm_task_json(task_id: str, priority: int) -> dict:
    return {
        "id": task_id,
        "title": task_id.title(),
        "area": f"area-{priority}",
        "description": f"Deliver the observable {task_id} artifact.",
        "acceptance_criteria": [f"{task_id.lower()}.txt exists and is nonempty"],
        "validation_commands": [f"test -s {task_id.lower()}.txt"],
        "priority": priority,
    }


def backlog_json(count: int = 15) -> str:
    tasks = [
        swarm_task_json(f"SW-{index:02d}", (index - 1) % 5 + 1)
        for index in range(1, count + 1)
    ]
    return json.dumps({"summary": "parallel swarm backlog", "tasks": tasks})


def cohort_backlog_json() -> str:
    tasks = [
        swarm_task_json("SW-01", 1),
        swarm_task_json("SW-02", 1),
        swarm_task_json("SW-03", 1),
        swarm_task_json("SW-04", 2),
        swarm_task_json("SW-05", 2),
    ]
    return json.dumps({"summary": "small cohort backlog", "tasks": tasks})


def make_controller(repo: Path, brief: Path, runner: SwarmRunner, tmp_path: Path, **kw: object) -> SwarmController:
    config = transfer(repo, brief, cheap_pool=list(CHEAP_CODER_POOL))
    options = {"teams": 3, "min_backlog": 15, "rng": None}
    options.update(kw)
    return SwarmController(
        config,
        runner=runner,
        state_home=tmp_path / "state",
        **options,  # type: ignore[arg-type]
    )


class SwarmRunner:
    """Deterministic fake swarm agents; records pool use and parallel ceilings."""

    def __init__(self, backlog: str, *, fail_coders: bool = False):
        self.backlog = backlog
        self._lock = threading.Lock()
        self._rounds: dict[str, int] = {}
        self._active = 0
        self.max_active = 0
        self.requests: list[AgentRequest] = []
        self.planner_calls = 0
        self.replan_calls = 0
        self.max_cohort_priority = 0
        self._fail_coders = fail_coders

    def run(self, request: AgentRequest) -> AgentResult:
        # The real runner enforces the role gate; the fake must too.
        assert load_policy(None).allows(request.model, request.role), (
            request.role,
            request.model.display(),
        )
        with self._lock:
            self.requests.append(request)
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        try:
            return self._dispatch(request)
        finally:
            with self._lock:
                self._active -= 1

    def round_for(self, key: str) -> int:
        with self._lock:
            self._rounds[key] = self._rounds.get(key, 0) + 1
            return self._rounds[key]

    def _dispatch(self, request: AgentRequest) -> AgentResult:
        if request.role == "planner":
            return self._plan(request)
        if request.role == "swarm_coder":
            return self._code(request)
        if request.role == "swarm_reviewer":
            return self._review(request)
        if request.role == "reviewer":
            return self._select(request)
        raise AssertionError(request.role)

    def _plan(self, request: AgentRequest) -> AgentResult:
        is_replan = "reprioritize" in request.prompt
        with self._lock:
            self.planner_calls += 1
            if is_replan:
                self.replan_calls += 1
        if is_replan:
            unfinished = list(
                dict.fromkeys(re.findall(r'"id":\s*"(SW[^"]*)"', request.prompt))
            )
            new_tasks = [
                swarm_task_json(f"SW-{200 + index}", 1)
                for index in range(1, 3)
            ]
            priorities = {f"SW-{200 + index}": 1 for index in range(1, 3)}
            for order, task_id in enumerate(unfinished, start=2):
                priorities[task_id] = order
            payload = {
                "summary": "fresh parallel work",
                "new_tasks": new_tasks,
                "priorities": priorities,
            }
            return result(request, json.dumps(payload))
        return result(request, self.backlog, tools=2)

    def _code(self, request: AgentRequest) -> AgentResult:
        if self._fail_coders:
            raise AgentFailure("cheap coder died", raw_output="boom")
        task_id = re.search(r'"id":\s*"(SW[^"]*)"', request.prompt).group(1)
        mode = next(name for name, text in CODER_TACTICS.items() if text in request.prompt)
        (request.cwd / f"{task_id.lower().replace('-', '_')}.txt" if False else request.cwd / f"{task_id.lower()}.txt").write_text(
            f"implemented {task_id} via {mode}\n", encoding="utf-8"
        )
        return result(request, f"Implemented {task_id} as a {mode} candidate.", tools=3)

    def _review(self, request: AgentRequest) -> AgentResult:
        task_id = re.search(r'"id":\s*"(SW[^"]*)"', request.prompt).group(1)
        count = self.round_for(f"review:{request.cwd}")
        if count == 1:
            return result(
                request,
                json.dumps(
                    {
                        "verdict": "fix",
                        "summary": "needs polish",
                        "blocking": [
                            {"problem": "polish", "detail": f"tighten {task_id} output"}
                        ],
                    }
                ),
            )
        return result(
            request,
            json.dumps({"verdict": "approve", "summary": "clean", "blocking": []}),
        )

    def _select(self, request: AgentRequest) -> AgentResult:
        match = re.search(r"SUBMITTED VERSIONS\n(\[.*?\])\n", request.prompt)
        assert match, request.prompt
        submitted = json.loads(match.group(1))
        winner = submitted[0]
        payload = {
            "winner": winner,
            "reason": "cleanest evidence",
            "candidates": {
                name: {"score": 90 if name == winner else 70, "summary": "ok"}
                for name in submitted
            },
            "feedback": [],
        }
        return result(request, json.dumps(payload))


def test_swarm_full_flow_pool_and_cap(tmp_path: Path) -> None:
    repo, brief = repo_and_brief(tmp_path)
    runner = SwarmRunner(backlog_json())
    box = make_controller(repo, brief, runner, tmp_path)
    state = box.run()

    assert state.status == "completed"
    counts = {status: 0 for status in CLOSING_STATUS}
    for task in state.tasks:
        counts[task.status] += 1
    assert counts["done"] == len(state.tasks), "every task ends done, replan extras included"
    assert counts["pending"] == 0
    assert runner.max_active <= SWARM_AGENTS_CAP
    assert runner.max_active > 1, "swarm work must actually overlap in rounds"

    coding_requests = [
        request
        for request in runner.requests
        if request.role in {"swarm_coder", "swarm_reviewer"}
    ]
    assert coding_requests
    for request in coding_requests:
        assert request.model.display() in POOL_IDENTITIES
    strong = [
        request for request in runner.requests if request.role == "reviewer"
    ]
    assert strong and all(request.model.display() not in POOL_IDENTITIES for request in strong)
    assert (box.store.root / "swarm" / "state.json").is_file()
    assert (repo / ".forge" / "runs").exists()


def test_swarm_threshold_triggers_reprioritization(tmp_path: Path) -> None:
    repo, brief = repo_and_brief(tmp_path)
    runner = SwarmRunner(cohort_backlog_json())
    box = make_controller(
        repo, brief, runner, tmp_path, min_backlog=5, ready_threshold=0.7, teams=2
    )
    state = box.run()

    assert state.status == "completed"
    assert state.planner_visits == 2
    assert 1 in state.replanned_levels
    assert any(task.origin == "replan" for task in state.tasks)


def test_swarm_conflict_reenters_task(tmp_path: Path, monkeypatch) -> None:
    repo, brief = repo_and_brief(tmp_path)
    runner = SwarmRunner(backlog_json(count=3))
    box = make_controller(repo, brief, runner, tmp_path, min_backlog=3, teams=2)
    real_git = box._git

    def failing_merge(*args, **kwargs):
        if args[:2] == ("merge", "--no-ff"):
            raise SwarmGitError("merge conflict while another team was in flight")
        return real_git(*args, **kwargs)

    monkeypatch.setattr(box, "_git", failing_merge)
    state = box.run()

    assert state.status == "completed"
    statuses = {task.id: task.status for task in state.tasks}
    assert statuses["SW-01"] == "conflict"
    assert statuses["SW-01-R1"] == "conflict"
    # The conflict family is re-entered three times, then the family is capped.
    conflicted = [
        task.id
        for task in state.tasks
        if task.id == "SW-01" or task.id.startswith("SW-01-")
    ]
    assert len(conflicted) == 4, conflicted
    assert all(task in conflicted and False for task in [])  # no-op guard
    assert all(task.status == "conflict" for task in state.tasks if task.id in conflicted)


def test_swarm_pair_failure_is_cheap_and_task_drops(tmp_path: Path) -> None:
    repo, brief = repo_and_brief(tmp_path)
    runner = SwarmRunner(backlog_json(count=2), fail_coders=True)
    box = make_controller(repo, brief, runner, tmp_path, min_backlog=2)
    state = box.run()

    assert state.status == "completed"
    assert all(task.status == "dropped" for task in state.tasks)
    assert box.state.warnings
    coding = [request for request in runner.requests if request.role == "swarm_coder"]
    assert coding and all(request.model.display() in POOL_IDENTITIES for request in coding)


def test_swarm_backlog_contract_minimum(tmp_path: Path) -> None:
    repo, brief = repo_and_brief(tmp_path)
    runner = SwarmRunner(backlog_json(count=14))
    box = make_controller(repo, brief, runner, tmp_path, min_backlog=15)
    state = box.run()
    assert state.status == "failed"
    assert "at least 15" in state.message


def test_swarm_cli_builds_a_controller_on_the_cheap_pool(tmp_path):
    from forge.cli import _parser, swarm_controller

    repo, brief = repo_and_brief(tmp_path)
    args = _parser().parse_args(
        [
            "swarm-run",
            "--repo", str(repo),
            "--brief", str(brief),
            "--policy-path", str(tmp_path / "absent.json"),
            "--teams", "9",
            "--seed", "1",
        ]
    )
    controller = swarm_controller(args)
    assert controller.config.cheap_pool == list(CHEAP_CODER_POOL)
    assert controller.strong_reviewer == SOL
    assert controller.teams_limit == 3
