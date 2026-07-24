"""CLI, bootstrap, planowanie i wyłącznie pipeline KISS."""
from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from .agents import (AgentError, LimitExhausted, agent_supports_resume, extract_json,
                     run_agent, run_agent_session, run_planner)
from .config import Config, DEFAULT_TASK_DIFFICULTY, TASK_DIFFICULTIES
from . import prompts
from . import verify
from .shellrun import run_shellfree
from .state import State
from .task_pipeline import (InvalidDecision, parse_coder_decision, parse_review_decision,
                            parse_tester_decision, run_tdd_loop,
                            test_fingerprint)

_JSON_RETRY = """

Poprzednia odpowiedź nie spełniła kontraktu. Nie wykonuj dalszych zmian.
Zwróć teraz wyłącznie jeden poprawny obiekt JSON w formacie podanym wyżej.
"""

_TASK_STATE_FIELDS = (
    "current_task", "task_phase", "tdd_round",
    "tester_session", "coder_session", "tester_decision", "tester_handoff",
    "tester_record", "coder_record", "review_notes", "corrections_done",
    "corrections_tree_hash", "task_start_tag", "coder_test_hash",
    "coder_tree_hash",
)


def git(project: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=project, text=True, capture_output=True, check=check)


def ensure_repo(project: str) -> None:
    Path(project).mkdir(parents=True, exist_ok=True)
    new_repo = not (Path(project) / ".git").exists()
    if new_repo:
        git(project, "init")
    ignore = Path(project, ".gitignore")
    existing = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    if new_repo and ".forge/" not in existing.splitlines():
        ignore.write_text(existing.rstrip() + "\n.forge/\n", encoding="utf-8")
    exclude = Path(project, ".git", "info", "exclude")
    excluded = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if ".forge/" not in excluded.splitlines():
        exclude.write_text(excluded.rstrip() + "\n.forge/\n", encoding="utf-8")
    if not git(project, "config", "user.email", check=False).stdout.strip():
        git(project, "config", "user.email", "forge@local")
    if not git(project, "config", "user.name", check=False).stdout.strip():
        git(project, "config", "user.name", "Forge")


def has_changes(project: str) -> bool:
    return bool(git(project, "status", "--porcelain").stdout.strip())


def _require_clean(project: str, phase: str) -> None:
    if has_changes(project):
        raise AgentError(f"drzewo robocze nie jest czyste przed {phase}; zatwierdź lub odłóż własne zmiany")


def commit_all(project: str, message: str, cfg: Config | None = None) -> None:
    if has_changes(project):
        git(project, "add", "-A")
        git(project, "commit", "-m", message)
    if cfg and cfg.git_push:
        push(project, cfg)


def push(project: str, cfg: Config) -> None:
    remotes = git(project, "remote", check=False).stdout.split()
    if cfg.git_remote in remotes:
        git(project, "push", cfg.git_remote, "HEAD")


def run_tests(project: str, test_cmd: str, timeout: int) -> bool:
    rc, _ = run_shellfree(project, test_cmd, timeout)
    return rc == 0


def build_then_test(project: str, build_cmd: str, test_cmd: str, timeout: int) -> bool:
    if build_cmd:
        rc, _ = run_shellfree(project, build_cmd, timeout)
        if rc != 0:
            return False
    return run_tests(project, test_cmd, timeout)


def _tail(output: str, size: int = 1200) -> str:
    return output[-size:].strip()


def _run_boundary(project: str, state: State, task: dict, cfg: Config) -> tuple[bool, list[str]]:
    commands = []
    if state.build_cmd:
        commands.append(state.build_cmd)
    targeted = task.get("targeted_test_cmd", "").strip()
    if targeted:
        commands.append(targeted)
    if state.test_cmd and state.test_cmd not in commands:
        commands.append(state.test_cmd)
    repro = task.get("repro_cmd", "").strip()
    if repro and repro not in commands:
        commands.append(repro)
    if not commands:
        return False, ["brak komendy testowej"]
    results = []
    for command in commands:
        rc, output = run_shellfree(project, command, cfg.agent_timeout_s)
        results.append(f"{command}: rc={rc}; {_tail(output)}")
        if rc != 0:
            return False, results
    return True, results


def _next_task_index(project: str) -> int:
    files = Path(project, ".forge", "tasks").glob("task-*.md")
    ids = [int(p.stem.split("-")[-1]) for p in files if p.stem.split("-")[-1].isdigit()]
    return max(ids, default=0) + 1


def build_task_from_plan(project: str, raw: dict) -> dict:
    difficulty = raw.get("difficulty", DEFAULT_TASK_DIFFICULTY)
    if difficulty not in TASK_DIFFICULTIES:
        difficulty = DEFAULT_TASK_DIFFICULTY
    return {"id": raw.get("id", "task"), "title": raw.get("title", "(bez tytułu)"),
            "file": raw.get("file", ""), "criteria": raw.get("criteria", []),
            "test_globs": raw.get("test_globs", []), "code_globs": raw.get("code_globs", []),
            "targeted_test_cmd": raw.get("targeted_test_cmd", raw.get("test_cmd", "")),
            "repro_cmd": raw.get("repro_cmd", ""), "difficulty": difficulty}


def phase_plan_batch(cfg: Config, project: str, state: State, logf) -> dict:
    feedback = Path(project, cfg.runtime_dir, "verification", "latest-feedback.md")
    failures = Path(project, cfg.runtime_dir, "failures.md")
    plan_prompt = prompts.plan_batch_prompt(
        cfg.batch_size, _next_task_index(project), state.project_kind,
        verify_feedback_path=str(feedback) if feedback.exists() else "",
        failure_feedback_path=str(failures) if failures.exists() else "")
    data = _decision_with_retry(
        plan_prompt,
        lambda value: run_planner(value, cfg, project, logf("plan")),
        _parse_json_object)
    tasks = []
    for raw in data.get("tasks", []):
        task = build_task_from_plan(project, raw)
        if task["file"] and Path(project, task["file"]).is_file():
            tasks.append(task)
    if not tasks and not data.get("no_more_tasks"):
        raise AgentError("planista nie utworzył żadnego poprawnego zadania")
    state.task_queue = tasks
    commit_all(project, "docs: plan wsadowy i backlog", cfg)
    return {"no_more_tasks": bool(data.get("no_more_tasks")) and not tasks}


def phase_bootstrap(cfg: Config, project: str, state: State, logf) -> None:
    brief = Path(cfg.brief_path).read_text(encoding="utf-8")
    bootstrap = prompts.bootstrap_prompt(brief)
    data = _decision_with_retry(
        bootstrap,
        lambda value: run_planner(
            value, cfg, project, logf("bootstrap"), role="bootstrap"),
        _parse_json_object)
    if not data.get("test_cmd"):
        raise AgentError("bootstrap nie zwrócił poprawnego JSON-a")
    state.test_cmd = data["test_cmd"]
    state.build_cmd = data.get("build_cmd", "")
    state.project_kind = data.get("kind", "app")
    profile = data.get("verify") or {}
    state.verify_targets = list(profile.get("targets") or [])
    for key in ("smoke_cmd", "flash_cmd", "target_cmd", "ci_status_cmd", "ci_logs_cmd"):
        setattr(state, key, str(profile.get(key, "")))
    if not build_then_test(project, state.build_cmd, state.test_cmd, cfg.agent_timeout_s):
        raise AgentError("testy bootstrapu nie przeszły")
    reviewer, model, effort = cfg.role("reviewer", "complex")
    tree = _tree_fingerprint(project)
    review_prompt = prompts.bootstrap_architecture_review_prompt(
        cfg.brief_path, state.test_cmd)
    verdict = _decision_with_retry(
        review_prompt,
        lambda value: run_agent(
            reviewer, value, cfg, project, logf("bootstrap-review"),
            model=model, effort=effort),
        parse_review_decision)
    if _tree_fingerprint(project) != tree:
        raise AgentError("reviewer architektury bootstrapu zmienił drzewo")
    if verdict.status != "approve":
        raise AgentError("recenzja architektury bootstrapu wymaga zmian")
    state.bootstrapped = True
    commit_all(project, "chore: bootstrap projektu", cfg)


def _write_current_task(project: str, task: dict) -> None:
    source, target = Path(project, task["file"]), Path(project, ".forge", "current_task.md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def _changed(project: str, tag: str) -> list[str]:
    tracked = git(project, "diff", "--name-only", tag).stdout.splitlines()
    return sorted({*tracked, *_untracked(project)})


def _untracked(project: str) -> list[str]:
    names = git(project, "ls-files", "--others", "--exclude-standard").stdout.splitlines()
    return [name for name in names if not name.startswith(".forge/")]


def _is_volatile_artifact(name: str) -> bool:
    """Cache narzędzi zmienia bajty między uruchomieniami testów, więc nie
    świadczy o edycji drzewa przez read-only reviewera — niezależnie od tego,
    czy bootstrap dopisał go do .gitignore."""
    parts = name.split("/")
    if any(part in {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                    ".tox", "node_modules", ".gradle", ".idea"} for part in parts):
        return True
    base = parts[-1]
    return base.startswith(".coverage") or base.endswith((".pyc", ".pyo", ".orig"))


def _tree_fingerprint(project: str) -> str:
    """Śledzone i nieignorowane pliki worktree; cache buildów nie ma znaczenia."""
    import hashlib
    root, digest = Path(project), hashlib.sha256()
    names = git(project, "ls-files", "--cached", "--others", "--exclude-standard").stdout.splitlines()
    for name in sorted(set(names)):
        path = root / name
        if not path.is_file() or name.startswith(".forge/") or _is_volatile_artifact(name):
            continue
        digest.update(str(path.relative_to(root)).encode()); digest.update(b"\0")
        digest.update(path.read_bytes()); digest.update(b"\0")
    return digest.hexdigest()


def _checkpoint(project: str, state: State, phase: str) -> None:
    state.task_phase = phase
    state.save(str(Path(project, ".forge", "STATE.json")))


def _call_role(cfg: Config, project: str, state: State, role: str, prompt: str, log: str) -> str:
    agent, model, effort = cfg.role(role, state.current_task.get("difficulty", DEFAULT_TASK_DIFFICULTY))
    attr = "tester_session" if role == "tester" else "coder_session"
    previous = getattr(state, attr)
    record_attr = "tester_record" if role == "tester" else "coder_record"
    if not agent_supports_resume(agent) and getattr(state, record_attr):
        prompt += "\n\nPrywatny, ograniczony zapis poprzednich działań tej samej roli:\n" + getattr(state, record_attr)[-4000:]
    output, session = run_agent_session(agent, prompt, cfg, project, log, session_id=previous or None, model=model, effort=effort)
    if session:
        setattr(state, attr, session)
    if not agent_supports_resume(agent):
        setattr(state, record_attr, (getattr(state, record_attr) + "\n" + output)[-8000:])
    return output


def _decision_with_retry(prompt: str, invoke, parser):
    """Jedna tania korekta formatu, potem jawny błąd zamiast ukrytej pętli."""
    try:
        return parser(invoke(prompt))
    except InvalidDecision:
        return parser(invoke(prompt + _JSON_RETRY))


def _parse_json_object(text: str) -> dict:
    data = extract_json(text)
    if not isinstance(data, dict):
        raise InvalidDecision("agent nie zwrócił obiektu JSON")
    return data


def _clear_task_state(state: State) -> None:
    defaults = State()
    for field in _TASK_STATE_FIELDS:
        setattr(state, field, getattr(defaults, field))


def _fail_task(cfg: Config, project: str, state: State, reason: str) -> None:
    task_id = state.current_task.get("id", "task")
    artifact = Path(project, cfg.runtime_dir, "failed", task_id)
    artifact.mkdir(parents=True, exist_ok=True)
    artifact.joinpath("reason.txt").write_text(reason + "\n", encoding="utf-8")
    artifact.joinpath("diff.patch").write_text(git(project, "diff", "--no-ext-diff", state.task_start_tag, check=False).stdout, encoding="utf-8")
    for rel in _untracked(project):
        source = Path(project, rel)
        if source.is_file():
            target = artifact / "untracked" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
    if state.task_start_tag:
        if cfg.keep_failed_ref:
            original = git(project, "branch", "--show-current").stdout.strip()
            branch = f"forge/failed/{task_id}"
            git(project, "switch", "-C", branch, state.task_start_tag)
            git(project, "add", "-A", "--", ".")
            git(project, "reset", "--", ".forge", check=False)
            if has_changes(project):
                git(project, "commit", "-m", f"forge: failed task {task_id}")
            # Detached HEAD nie ma nazwy branchu — wracamy po SHA, żeby obsługa
            # porażki nie zamieniła się w twardy crash na `git switch ""`.
            git(project, "switch", original) if original else git(project, "switch", "--detach", state.task_start_tag)
        git(project, "reset", "--hard", state.task_start_tag)
        for rel in _changed(project, state.task_start_tag):
            candidate = Path(project, rel)
            if candidate.is_file():
                candidate.unlink()
        git(project, "tag", "-d", state.task_start_tag, check=False)
    failures = Path(project, cfg.runtime_dir, "failures.md")
    failures.parent.mkdir(parents=True, exist_ok=True)
    with failures.open("a", encoding="utf-8") as target:
        target.write(f"- {task_id}: {reason}; artefakt: {artifact}\n")
    state.task_queue.clear()
    _clear_task_state(state)
    _checkpoint(project, state, "")


def run_task(cfg: Config, project: str, state: State, logf) -> bool:
    task = state.current_task
    if not task:
        if has_changes(project):
            raise AgentError("drzewo robocze nie jest czyste przed startem zadania; zatwierdź lub odłóż własne zmiany")
        task = state.task_queue.pop(0)
        state.current_task = task
        state.task_start_tag = f"forge/{task['id']}-start"
        git(project, "tag", state.task_start_tag)
        _write_current_task(project, task)
        state.task_phase = "tester"
        _checkpoint(project, state, "tester")
    if state.task_phase in {"", "tester", "coder"}:
        def run_tester(handoff: str):
            prompt = prompts.tester_task_prompt(
                task["file"], state.test_cmd, handoff=handoff,
                resume=bool(state.tester_session))
            return _decision_with_retry(
                prompt,
                lambda value: _call_role(
                    cfg, project, state, "tester", value, logf("tester")),
                parse_tester_decision)

        def run_coder(decision):
            prompt = prompts.coder_task_prompt(
                task["file"], decision.data.get("command") or state.test_cmd,
                decision=decision.data, resume=bool(state.coder_session))
            return _decision_with_retry(
                prompt,
                lambda value: _call_role(
                    cfg, project, state, "coder", value, logf("coder")),
                parse_coder_decision)

        outcome = run_tdd_loop(
            state=state, max_rounds=cfg.max_tdd_rounds,
            run_tester=run_tester,
            run_coder=run_coder,
            checkpoint=lambda phase: _checkpoint(project, state, phase),
            fingerprint=lambda: test_fingerprint(project, task.get("test_globs", [])),
            worktree_fingerprint=lambda: _tree_fingerprint(project),
        )
        if outcome != "review":
            _fail_task(cfg, project, state, outcome)
            return True
        _checkpoint(project, state, "review")
    if state.task_phase == "review":
        green, results = _run_boundary(project, state, task, cfg)
        if not green:
            state.tester_handoff = "Granica przed review jest czerwona: " + " | ".join(results)
            state.tdd_round += 1
            if state.tdd_round >= cfg.max_tdd_rounds:
                _fail_task(cfg, project, state, f"round_limit: zadanie wymaga podziału (limit {cfg.max_tdd_rounds})"); return True
            _checkpoint(project, state, "tester")
            return True
        tree = _tree_fingerprint(project)
        reviewer, model, effort = cfg.role("reviewer", task["difficulty"])
        review_prompt = prompts.review_task_prompt_kiss(
            task["file"], start_tag=state.task_start_tag,
            changed=_changed(project, state.task_start_tag), test_results=results)
        review = _decision_with_retry(
            review_prompt,
            lambda value: run_agent(
                reviewer, value, cfg, project, logf("review"),
                model=model, effort=effort),
            parse_review_decision)
        if _tree_fingerprint(project) != tree:
            _fail_task(cfg, project, state, "blocked: reviewer zmienił drzewo"); return True
        if review.status == "changes":
            state.review_notes = list(review.data.get("notes", []))
            state.corrections_tree_hash = _tree_fingerprint(project)
            _checkpoint(project, state, "corrections")
        else:
            _checkpoint(project, state, "commit")
    if state.task_phase == "corrections":
        if not state.corrections_done:
            if not state.corrections_tree_hash:
                state.corrections_tree_hash = _tree_fingerprint(project)
                _checkpoint(project, state, "corrections")
            if state.corrections_tree_hash == _tree_fingerprint(project):
                correction_prompt = prompts.corrections_prompt(
                    task["file"], state.review_notes, state.test_cmd,
                    targeted_test_cmd=task.get("targeted_test_cmd", ""),
                    start_tag=state.task_start_tag,
                    changed=_changed(project, state.task_start_tag),
                    resume=bool(state.coder_session))
                correction = _decision_with_retry(
                    correction_prompt,
                    lambda value: _call_role(
                        cfg, project, state, "coder", value, logf("corrections")),
                    parse_coder_decision)
                if correction.status != "green":
                    _fail_task(cfg, project, state, "poprawki review nie zostały wykonane"); return True
            state.corrections_done = True
            state.corrections_tree_hash = ""
            _checkpoint(project, state, "corrections")
        green, _ = _run_boundary(project, state, task, cfg)
        if not green:
            _fail_task(cfg, project, state, "czerwone testy po poprawkach review"); return True
        _checkpoint(project, state, "commit")
    if state.task_phase != "commit":
        return True
    commit_all(project, f"feat: {task['title']}", cfg)
    git(project, "tag", "-d", state.task_start_tag, check=False)
    _clear_task_state(state)
    _checkpoint(project, state, "")
    return True


def phase_verify_goal(cfg: Config, project: str, state: State, logf) -> bool:
    """Końcowa weryfikacja celu zostaje poza pipeline'em pojedynczego zadania."""
    targets = cfg.effective_verify_targets(state.verify_targets)
    if not targets:
        state.task_phase = ""
        return False
    if state.verify_cycle >= cfg.max_verify_cycles:
        raise AgentError(f"weryfikacja celu przekroczyła limit {cfg.max_verify_cycles} cykli")
    state.task_phase = "verify_goal"
    state.verify_cycle += 1
    cycle_dir = str(Path(project, cfg.runtime_dir, "verification", f"cycle-{state.verify_cycle}"))
    evidence = verify.collect_evidence(project, state, cfg, cycle_dir, sha=git(project, "rev-parse", "HEAD").stdout.strip(), targets=targets)
    if any(item.get("rc") != 0 for item in evidence.values()):
        feedback = Path(project, cfg.runtime_dir, "verification", "latest-feedback.md")
        feedback.parent.mkdir(parents=True, exist_ok=True)
        feedback.write_text("Weryfikacja celu: czerwony dowód\n" + "\n".join(f"- {name}: rc={item.get('rc')}" for name, item in evidence.items()), encoding="utf-8")
        state.task_phase = ""
        return True
    agent, model, effort = cfg.role("verifier", DEFAULT_TASK_DIFFICULTY)
    verify_prompt = prompts.verify_goal_prompt(
        state.verify_cycle, evidence, cycle_dir)
    try:
        data = _decision_with_retry(
            verify_prompt,
            lambda value: run_agent(
                agent, value, cfg, project, logf("verify"),
                model=model, effort=effort,
                mcp_config=cfg.verifier_mcp_config),
            _parse_json_object)
    except InvalidDecision:
        # Weryfikacja celu jest tolerancyjna: nieparsowalny werdykt po korekcie
        # nie ubija całej pętli, tylko wraca do planowania jak zwykłe "changes".
        data = {"verdict": "changes", "notes": ["weryfikator nie zwrócił poprawnego werdyktu"]}
    if data.get("verdict") in {"complete", "pass", "approve"}:
        state.task_phase = ""
        return False
    feedback = Path(project, cfg.runtime_dir, "verification", "latest-feedback.md")
    feedback.parent.mkdir(parents=True, exist_ok=True)
    notes = data.get("notes", [])
    feedback.write_text("Weryfikacja celu wymaga zmian:\n" + "\n".join(f"- {note}" for note in notes), encoding="utf-8")
    state.task_phase = ""
    return True


def one_iteration(cfg: Config, project: str, state: State) -> bool:
    ensure_repo(project)
    def logf(phase: str) -> str:
        path = Path(project, cfg.runtime_dir, "logs", f"iter-{state.iteration + 1:04d}-{phase}.log"); path.parent.mkdir(parents=True, exist_ok=True); return str(path)
    if not state.bootstrapped:
        if git(project, "rev-parse", "--verify", "HEAD", check=False).returncode == 0:
            _require_clean(project, "bootstrapem")
        phase_bootstrap(cfg, project, state, logf); state.save(str(Path(project, cfg.runtime_dir, "STATE.json"))); return True
    if state.task_phase == "verify_goal":
        return phase_verify_goal(cfg, project, state, logf)
    if not state.current_task and not state.task_queue:
        _require_clean(project, "planowaniem")
        planned = phase_plan_batch(cfg, project, state, logf)
        if planned["no_more_tasks"]:
            return phase_verify_goal(cfg, project, state, logf)
    state.iteration += 1
    return run_task(cfg, project, state, logf)


def _load_state_path(project: str, cfg: Config) -> Path:
    """Obsłuż bezpiecznie dawną lokalizację STATE.json przed każdym agentem."""
    modern = Path(project, cfg.runtime_dir, "STATE.json")
    old = Path(project, "STATE.json")
    if modern.exists():
        return modern
    if old.exists():
        # State.load celowo odrzuci aktywne fazy poprzedniego automatu.
        return old
    return modern


def discard_legacy_task(project: str, cfg: Config) -> Path:
    """Porzuć wyłącznie aktywny stary task, zachowując trwałą konfigurację Forge."""
    import json
    old, modern = Path(project, "STATE.json"), Path(project, cfg.runtime_dir, "STATE.json")
    if not old.exists():
        raise AgentError("brak rootowego STATE.json do migracji")
    raw = json.loads(old.read_text(encoding="utf-8"))
    stable = {key: raw[key] for key in State.__annotations__ if key in raw}
    for key in _TASK_STATE_FIELDS:
        stable.pop(key, None)
    # Pozostała kolejka mogła zakładać sukces porzuconego zadania.
    stable["task_queue"] = []
    migrated = State(**stable)
    migrated.save(str(modern))
    backup = Path(project, cfg.runtime_dir, "STATE.legacy-discarded.json")
    backup.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return modern


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Forge KISS: tester → coder → review; max_tdd_rounds chroni małe zadania.")
    parser.add_argument("--brief", default="game.md"); parser.add_argument("--project", default="game")
    parser.add_argument("--non-interactive", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--discard-legacy-task", action="store_true", help="Porzuć aktywny stary task i zachowaj stabilny stan.")
    parser.add_argument("--max-iters", type=int, default=0); parser.add_argument("--batch-size", type=int); parser.add_argument("--max-tdd-rounds", type=int)
    parser.add_argument("--tester-agent"); parser.add_argument("--coder-agent"); parser.add_argument("--reviewer-agent"); parser.add_argument("--planner-agent")
    args = parser.parse_args(argv); cfg = Config(brief_path=args.brief)
    for key in ("batch_size", "max_tdd_rounds", "tester_agent", "coder_agent", "reviewer_agent", "planner_agent"):
        value = getattr(args, key)
        if value is not None: setattr(cfg, key, value)
    if args.discard_legacy_task:
        try:
            discard_legacy_task(args.project, cfg)
        except (AgentError, OSError, ValueError) as exc:
            parser.error(str(exc))
        print("Porzucono aktywne stare zadanie; zachowano stabilny stan w .forge/STATE.json.")
        return 0
    path = _load_state_path(args.project, cfg)
    try:
        state = State.load(str(path))
    except ValueError as exc:
        parser.error(str(exc) + ". Zarchiwizuj STATE.json po świadomym porzuceniu starego zadania.")
    # Bezczynny stary stan jest jednorazowo kopiowany do nowej lokalizacji.
    runtime_path = Path(args.project, cfg.runtime_dir, "STATE.json")
    if path != runtime_path:
        state.save(str(runtime_path)); path = runtime_path
    count = 0
    try:
        while not args.max_iters or count < args.max_iters:
            if not one_iteration(cfg, args.project, state): break
            state.save(str(path)); count += 1
    except (AgentError, InvalidDecision, LimitExhausted) as exc:
        state.save(str(path))
        print(f"Forge zatrzymany bezpiecznie: {exc}. Checkpoint zapisano w {path}.", file=__import__("sys").stderr)
        return 3 if isinstance(exc, LimitExhausted) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
