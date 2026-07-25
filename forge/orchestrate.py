"""CLI, bootstrap, planowanie i wyłącznie pipeline KISS."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

from .agents import (AgentError, LimitExhausted, agent_supports_resume, extract_json,
                     log, run_agent, run_agent_session, run_planner)
from .config import Config, DEFAULT_TASK_DIFFICULTY, TASK_DIFFICULTIES
from . import ledger
from . import prompts
from . import verify
from .shellrun import run_shellfree
from .state import State
from .task_pipeline import (InvalidDecision, parse_coder_decision, parse_review_decision,
                            parse_tester_decision, run_tdd_loop)

_JSON_RETRY = """

Poprzednia odpowiedź nie spełniła kontraktu. Nie wykonuj dalszych zmian.
Zwróć teraz wyłącznie jeden poprawny obiekt JSON w formacie podanym wyżej.
"""

_TASK_STATE_FIELDS = (
    "current_task", "task_phase", "tdd_round",
    "tester_session", "coder_session", "tester_decision", "tester_handoff",
    "coder_summary", "tester_record", "coder_record", "review_notes", "corrections_done",
    "corrections_tree_hash", "task_start_tag", "coder_tree_hash",
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
    return build_then_test_result(project, build_cmd, test_cmd, timeout)[0]


def build_then_test_result(
        project: str, build_cmd: str, test_cmd: str,
        timeout: int) -> tuple[bool, str]:
    """Uruchom build i pełne testy, zachowując output dla handoffu."""
    if build_cmd:
        rc, output = run_shellfree(project, build_cmd, timeout)
        if rc != 0:
            return False, output
    rc, output = run_shellfree(project, test_cmd, timeout)
    return rc == 0, output


def _next_task_index(project: str) -> int:
    files = Path(project, ".forge", "tasks").rglob("task-*.md")
    ids = [
        int(match.group(1))
        for path in files
        if (match := re.match(r"task-(\d+)", path.stem))
    ]
    return max(ids, default=0) + 1


def build_task_from_plan(project: str, raw: dict) -> dict:
    difficulty = raw.get("difficulty", DEFAULT_TASK_DIFFICULTY)
    if difficulty not in TASK_DIFFICULTIES:
        difficulty = DEFAULT_TASK_DIFFICULTY
    dependencies = raw.get("depends_on", [])
    if not isinstance(dependencies, (list, tuple)):
        dependencies = [dependencies]
    return {"id": raw.get("id", "task"), "title": raw.get("title", "(bez tytułu)"),
            "file": raw.get("file", ""), "criteria": raw.get("criteria", []),
            "test_globs": raw.get("test_globs", []), "code_globs": raw.get("code_globs", []),
            "targeted_test_cmd": raw.get("targeted_test_cmd", raw.get("test_cmd", "")),
            "repro_cmd": raw.get("repro_cmd", ""), "difficulty": difficulty,
            "depends_on": [str(item) for item in dependencies if str(item)]}


def phase_plan_batch(cfg: Config, project: str, state: State, logf) -> dict:
    _housekeeping(cfg, project)
    feedback = Path(project, cfg.runtime_dir, "verification", "latest-feedback.md")
    failures = Path(project, cfg.runtime_dir, "failures.md")
    start_index = _next_task_index(project)
    log(f"Planowanie: proszę planistę o maks. {cfg.batch_size} zadań od task-{start_index:03d}…")
    plan_prompt = prompts.plan_batch_prompt(
        cfg.batch_size, start_index, state.project_kind,
        verify_feedback_path=str(feedback) if feedback.exists() else "",
        failure_feedback_path=str(failures) if failures.exists() else "")
    # Mistrz widzi w dzienniku również historię wsadów — serię zadań ginących
    # na round_limit potrafi skomentować zanim planista utnie kolejny za grubo.
    plan_prompt += prompts.master_note_suffix(
        _master_notes(cfg, project, logf).get("planner", ""))
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
    if tasks:
        log(f"Planowanie: utworzono {len(tasks)} zadań: {', '.join(t['id'] for t in tasks)}")
        ledger.append(project, f"plan: utworzono {len(tasks)} zadań "
                               f"({tasks[0]['id']}…{tasks[-1]['id']})")
    elif data.get("no_more_tasks"):
        log("Planowanie: planista zgłosił brak dalszych zadań.")
        ledger.append(project, "plan: planista zgłosił brak dalszych zadań")
    state.task_queue = tasks
    commit_all(project, "docs: plan wsadowy i backlog", cfg)
    return {"no_more_tasks": bool(data.get("no_more_tasks")) and not tasks}


def phase_bootstrap(cfg: Config, project: str, state: State, logf) -> None:
    log("Bootstrap: analiza briefu i budowa szkieletu projektu…")
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
    log(f"Bootstrap: test_cmd={state.test_cmd!r} build_cmd={state.build_cmd!r} kind={state.project_kind!r}")
    if not build_then_test(project, state.build_cmd, state.test_cmd, cfg.agent_timeout_s):
        raise AgentError("testy bootstrapu nie przeszły")
    log("Bootstrap: testy początkowe zielone.")
    reviewer, model, effort = cfg.role("reviewer", "complex")
    tree = _tree_fingerprint(project)
    review_prompt = prompts.bootstrap_architecture_review_prompt(
        cfg.brief_path, state.test_cmd)
    log("Bootstrap: recenzja architektury (świeży, read-only recenzent)…")
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
    log("Bootstrap: recenzja zaakceptowana, commituję.")
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


def _tree_manifest(project: str) -> dict[str, str]:
    """Hash per plik, aby wznowienia i Mistrz widzieli ten sam stan drzewa."""
    import hashlib
    root = Path(project)
    manifest: dict[str, str] = {}
    names = git(project, "ls-files", "--cached", "--others", "--exclude-standard").stdout.splitlines()
    for name in sorted(set(names)):
        path = root / name
        if not path.is_file() or name.startswith(".forge/") or _is_volatile_artifact(name):
            continue
        manifest[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return manifest


def _tree_fingerprint(project: str) -> str:
    """Śledzone i nieignorowane pliki worktree; cache buildów nie ma znaczenia."""
    import hashlib
    digest = hashlib.sha256()
    for name, file_hash in _tree_manifest(project).items():
        digest.update(name.encode()); digest.update(b"\0")
        digest.update(file_hash.encode()); digest.update(b"\0")
    return digest.hexdigest()


def _turn_changes(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(name for name in before.keys() | after.keys()
                  if before.get(name) != after.get(name))


def _describe_turn_changes(paths: list[str], limit: int = 8) -> str:
    if not paths:
        return "bez_zmian"
    visible = ", ".join(paths[:limit])
    suffix = f", +{len(paths) - limit}" if len(paths) > limit else ""
    return f"[{visible}{suffix}]"


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


_MASTER_ROLES = ("tester", "coder", "planner")


def _master_notes(cfg: Config, project: str, logf) -> dict[str, str]:
    """Notatki mistrza per rola — nadzór procesu, nie merytoryki.

    Mistrz jest doradczy Z KONSTRUKCJI, więc każda jego awaria (błąd, limit,
    śmieciowa odpowiedź) daje brak notatek i pipeline zachowuje się dokładnie
    tak jak bez niego. Widzi wyłącznie dziennik przekazany w promptcie i
    pracuje w katalogu tymczasowym — fizycznie nie ma dostępu do repozytorium,
    więc nie może zmienić drzewa ani wyjść poza swoją rolę.
    """
    agent, model, effort = cfg.role("master")
    # Rola doradcza nie ma prawa przespać godzin backoffu przed realną pracą.
    advisory = replace(cfg, max_limit_retries=0)
    prompt = prompts.master_ledger_prompt(ledger.compact_tail(project))
    try:
        with tempfile.TemporaryDirectory(prefix="forge-master-") as sandbox:
            # Sandbox jest katalogiem roboczym, ale koszt roli wołanej co rundę
            # musi trafić do telemetrii projektu, a nie zniknąć razem z nim.
            raw = run_agent(agent, prompt, advisory, sandbox, logf("master"),
                            model=model, effort=effort, usage_dir=project,
                            thin=True,
                            system_prompt=prompts.master_system_prompt(),
                            json_schema=prompts.master_json_schema())
        data = extract_json(raw)
    except Exception:  # noqa: BLE001 — rola doradcza nie ma prawa niczego zatrzymać
        return {}
    if not isinstance(data, dict):
        return {}
    notes = {role: value.strip() for role, value in data.items()
             if role in _MASTER_ROLES and isinstance(value, str) and value.strip()}
    for role, note in notes.items():
        log(f"  mistrz → {role}: {note}")
    return notes


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
    log(f"Zadanie {task_id} PORZUCONE: {reason}")
    ledger.append(project, f"{task_id} PORZUCONE: {reason[:200]}")
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
    blocked = _dependent_task_ids(state.task_queue, task_id)
    notice = (
        f"{task_id} z tego wsadu został porzucony (powód: {reason}). "
        "Jeśli twoje zadanie mimo braku jawnej zależności na nim polegało, "
        "zwróć `blocked`."
    )
    remaining = []
    for queued in state.task_queue:
        if queued.get("id") in blocked:
            continue
        task = dict(queued)
        previous = str(task.get("batch_handoff", "")).strip()
        task["batch_handoff"] = f"{previous}\n{notice}".strip()
        remaining.append(task)
    state.task_queue = remaining
    _clear_task_state(state)
    _checkpoint(project, state, "")


def _dependent_task_ids(tasks: list[dict], failed_id: str) -> set[str]:
    """Tranzytywne domknięcie zadań zależnych od porzuconego zadania."""
    blocked = {failed_id}
    changed = True
    while changed:
        changed = False
        for task in tasks:
            task_id = str(task.get("id", ""))
            dependencies = task.get("depends_on", [])
            if isinstance(dependencies, str):
                dependencies = [dependencies]
            if task_id not in blocked and any(
                    str(dependency) in blocked for dependency in dependencies):
                blocked.add(task_id)
                changed = True
    return blocked


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
        state.tester_handoff = str(task.get("batch_handoff", "")).strip()
        _checkpoint(project, state, "tester")
        log(f"Zadanie {task['id']} — {task['title']} (trudność: {task['difficulty']})")
        ledger.append(project, f"{task['id']} start: {task['title']} ({task['difficulty']})")
    if state.task_phase in {"", "tester", "coder"}:
        # Mistrz jest wołany raz na rundę; ta sama notatka obsługuje kodera w
        # tej samej rundzie. Zmienna lokalna wystarcza — po restarcie kolejna
        # runda i tak zapyta go od nowa.
        notes: dict[str, str] = {}
        consulted = False

        def ensure_notes(new_round: bool) -> None:
            """Nowa runda pyta mistrza zawsze; wznowienie prosto w koderze
            pyta, bo inaczej rada dla kodera przepadłaby. Milczenie mistrza
            (częsty przypadek) nie może kosztować drugiego wywołania."""
            nonlocal consulted
            if new_round or not consulted:
                notes.clear()
                notes.update(_master_notes(cfg, project, logf))
                consulted = True

        def run_turn(role: str, prompt: str, parser):
            """Jedna tura roli: nota mistrza, decyzja, log i wpis do dziennika
            wraz z listą plików zmienionych w tej konkretnej turze."""
            prompt += prompts.master_note_suffix(notes.get(role, ""))
            before = _tree_manifest(project)
            result = _decision_with_retry(
                prompt,
                lambda value: _call_role(
                    cfg, project, state, role, value, logf(role)),
                parser)
            # Nazwy plików pozwalają Mistrzowi zauważyć np. zmianę testu przez
            # kodera i poprosić testera o ocenę bez mechanicznego blokowania.
            changed = _describe_turn_changes(
                _turn_changes(before, _tree_manifest(project)))
            reason = str(result.data.get("reason", ""))
            label = "tester" if role == "tester" else "koder"
            log(f"  [{task['id']}] runda {state.tdd_round + 1}: {label} → {result.status}"
                + (f" ({reason[:200]})" if reason else ""))
            ledger.append(project, f"{task['id']} r{state.tdd_round + 1} "
                                   f"{label}→{result.status} pliki={changed}: {reason[:160]}")
            return result

        def run_tester(handoff: str):
            ensure_notes(new_round=True)
            return run_turn("tester", prompts.tester_task_prompt(
                task["file"], state.test_cmd, handoff=handoff,
                previous_decision=state.tester_decision,
                coder_summary=state.coder_summary,
                changed_files=_changed(project, state.task_start_tag),
                task_ledger=ledger.tail_for_task(project, task["id"], limit=8),
                resume=bool(state.tester_session)), parse_tester_decision)

        def run_coder(decision):
            ensure_notes(new_round=False)
            return run_turn("coder", prompts.coder_task_prompt(
                task["file"], decision.data.get("command") or state.test_cmd,
                decision=decision.data, resume=bool(state.coder_session)),
                parse_coder_decision)

        outcome = run_tdd_loop(
            state=state, max_rounds=cfg.max_tdd_rounds,
            run_tester=run_tester,
            run_coder=run_coder,
            checkpoint=lambda phase: _checkpoint(project, state, phase),
            worktree_fingerprint=lambda: _tree_fingerprint(project),
        )
        if outcome != "review":
            _fail_task(cfg, project, state, outcome)
            return True
        log(f"Zadanie {task['id']}: pętla TDD zakończona, przekazuję do review.")
        _checkpoint(project, state, "review")
    if state.task_phase == "review":
        before_review = _tree_manifest(project)
        reviewer, model, effort = cfg.role("reviewer", task["difficulty"])
        review_prompt = prompts.review_task_prompt_kiss(
            task["file"], start_tag=state.task_start_tag,
            changed=_changed(project, state.task_start_tag))
        log(f"Zadanie {task['id']}: recenzja (świeży kontekst)…")
        review = _decision_with_retry(
            review_prompt,
            lambda value: run_agent(
                reviewer, value, cfg, project, logf("review"),
                model=model, effort=effort),
            parse_review_decision)
        log(f"Zadanie {task['id']}: recenzja → {review.status}")
        review_changes = _describe_turn_changes(
            _turn_changes(before_review, _tree_manifest(project)))
        ledger.append(project, f"{task['id']} recenzja→{review.status} "
                               f"pliki={review_changes}: "
                               f"{'; '.join(review.data.get('notes', []))[:160]}")
        review_notes = list(review.data.get("notes", []))
        if review.status == "changes":
            state.review_notes = review_notes
            state.tester_handoff = (
                "Reviewer nie zaakceptował zadania. Rozpocznij nowy cykl TDD "
                f"i oceń uwagi: {'; '.join(review_notes) or '(brak konkretów)'}.")
            if review_changes != "bez_zmian":
                state.tester_handoff += (
                    " Reviewer zmienił też pliki "
                    f"{review_changes}; oceń te zmiany, zachowaj, popraw albo przywróć.")
            _checkpoint(project, state, "tester")
            return True
        if review_changes != "bez_zmian":
            # Reviewer ma pozostać read-only, lecz przypadkowy zapis nie jest
            # powodem do porzucenia całego zadania. Tester ocenia pozostawiony diff.
            state.tester_handoff = (
                "Reviewer zaakceptował wynik, ale mimo roli read-only zmienił pliki "
                f"{review_changes}. Oceń pozostawiony diff; zachowaj, popraw albo "
                "przywróć te zmiany, a następnie wybierz dalszy krok.")
            _checkpoint(project, state, "tester")
            return True
        _checkpoint(project, state, "commit")
    if state.task_phase == "corrections":
        # Zgodność ze starymi checkpointami. Osobna, jednorazowa tura kodera
        # została usunięta: każda uwaga review wraca teraz przez testera.
        state.tester_handoff = (
            "Wznowiono stary checkpoint poprawek po review. Rozpocznij nowy cykl "
            f"TDD i oceń uwagi: {'; '.join(state.review_notes) or '(brak konkretów)'}.")
        state.corrections_done = False
        state.corrections_tree_hash = ""
        _checkpoint(project, state, "tester")
        return True
    if state.task_phase != "commit":
        return True
    suite_green, suite_output = build_then_test_result(
        project, state.build_cmd, state.test_cmd, cfg.agent_timeout_s)
    if not suite_green:
        state.tester_handoff = (
            "Deterministyczna bramka przed commitem wykazała, że pełny pakiet "
            "jest czerwony po tym zadaniu. Oceń ogon wyniku, napraw albo zwróć "
            f"`blocked` z konkretnym powodem:\n{suite_output[-2000:]}"
        )
        _checkpoint(project, state, "tester")
        return True
    commit_all(project, f"feat: {task['title']}", cfg)
    git(project, "tag", "-d", state.task_start_tag, check=False)
    log(f"Zadanie {task['id']} UKOŃCZONE i zacommitowane: {task['title']}")
    ledger.append(project, f"{task['id']} UKOŃCZONE po {state.tdd_round} rundach")
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
    log(f"Weryfikacja celu: cykl {state.verify_cycle}/{cfg.max_verify_cycles}, targety={targets}")
    cycle_dir = str(Path(project, cfg.runtime_dir, "verification", f"cycle-{state.verify_cycle}"))
    evidence = verify.collect_evidence(project, state, cfg, cycle_dir, sha=git(project, "rev-parse", "HEAD").stdout.strip(), targets=targets)
    log("Weryfikacja celu: dowody → " + ", ".join(f"{name}=rc{item.get('rc')}" for name, item in evidence.items()))
    if any(item.get("rc") != 0 for item in evidence.values()):
        feedback = Path(project, cfg.runtime_dir, "verification", "latest-feedback.md")
        feedback.parent.mkdir(parents=True, exist_ok=True)
        feedback.write_text("Weryfikacja celu: czerwony dowód\n" + "\n".join(f"- {name}: rc={item.get('rc')}" for name, item in evidence.items()), encoding="utf-8")
        state.task_phase = ""
        log("Weryfikacja celu: czerwony dowód — wracam do planowania z feedbackiem.")
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
    log(f"Weryfikacja celu: werdykt={data.get('verdict')}")
    if data.get("verdict") in {"complete", "pass", "approve"}:
        state.task_phase = ""
        log("Weryfikacja celu: CEL OSIĄGNIĘTY — kończę pętlę.")
        return False
    feedback = Path(project, cfg.runtime_dir, "verification", "latest-feedback.md")
    feedback.parent.mkdir(parents=True, exist_ok=True)
    notes = data.get("notes", [])
    feedback.write_text("Weryfikacja celu wymaga zmian:\n" + "\n".join(f"- {note}" for note in notes), encoding="utf-8")
    state.task_phase = ""
    log("Weryfikacja celu: wymaga zmian — wracam do planowania.")
    return True


def one_iteration(cfg: Config, project: str, state: State) -> bool:
    ensure_repo(project)
    def logf(phase: str) -> str:
        path = _transcript_log_path(project, state.iteration + 1, phase)
        path.parent.mkdir(parents=True, exist_ok=True)
        return str(path)
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


_TRANSCRIPT_KEEP_ITERATIONS = 20


def _transcript_log_path(project: str, iteration: int, phase: str) -> Path:
    """Ścieżka surowej telemetrii poza drzewem projektu."""
    log_dir = _transcript_log_dir(project)
    _prune_transcript_logs(log_dir, iteration)
    return log_dir / f"iter-{iteration:04d}-{phase}.log"


def _transcript_log_dir(project: str) -> Path:
    import hashlib
    root = Path(project).resolve()
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:10]
    project_key = f"{root.name or 'project'}-{digest}"
    cache = Path(os.environ.get(
        "XDG_CACHE_HOME", str(Path.home() / ".cache")))
    return cache / "forge" / project_key / "logs"


def _prune_transcript_logs(log_dir: Path, current_iteration: int) -> None:
    """Best-effort: zachowaj logi bieżącej i 19 poprzednich iteracji."""
    oldest = current_iteration - _TRANSCRIPT_KEEP_ITERATIONS + 1
    try:
        for path in log_dir.glob("iter-*-*.log"):
            match = re.match(r"iter-(\d+)-", path.name)
            if match and int(match.group(1)) < oldest:
                path.unlink()
    except OSError:
        pass


_RUNTIME_KEEP_ITEMS = 20
_DOC_SIZE_LIMIT = 20_000


def _housekeeping(cfg: Config, project: str) -> None:
    """Deterministyczne sprzątanie przed planowaniem, bez udziału agenta."""
    import shutil

    runtime = Path(project, cfg.runtime_dir)
    tasks = runtime / "tasks"
    archive = tasks / "archive"
    for source in sorted(tasks.glob("task-*.md")):
        archive.mkdir(parents=True, exist_ok=True)
        target = archive / source.name
        if target.exists():
            suffix = 1
            while (archive / f"{source.stem}-{suffix}{source.suffix}").exists():
                suffix += 1
            target = archive / f"{source.stem}-{suffix}{source.suffix}"
        source.replace(target)

    failed = runtime / "failed"
    artifacts = sorted(
        (path for path in failed.iterdir() if path.is_dir()),
        key=lambda path: path.name,
    ) if failed.is_dir() else []
    for artifact in artifacts[:-_RUNTIME_KEEP_ITEMS]:
        shutil.rmtree(artifact, ignore_errors=True)

    log_dir = _transcript_log_dir(project)
    iterations = []
    for path in log_dir.glob("iter-*-*.log"):
        try:
            iterations.append(int(path.name.split("-")[1]))
        except (IndexError, ValueError):
            continue
    if iterations:
        _prune_transcript_logs(log_dir, max(iterations))

    _flag_oversized_docs(project)


def _flag_oversized_docs(project: str) -> None:
    oversized = []
    docs = Path(project, "docs")
    for path in docs.rglob("*.md") if docs.is_dir() else []:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > _DOC_SIZE_LIMIT:
            oversized.append((path.relative_to(project).as_posix(), size))
    if not oversized:
        return
    backlog = Path(project, "BACKLOG.md")
    existing = backlog.read_text(encoding="utf-8") if backlog.exists() else ""
    additions = [
        f"- Dług dokumentacji: `{name}` ma {size // 1000} KB "
        "— zaplanuj podział pliku.\n"
        for name, size in oversized if f"`{name}`" not in existing
    ]
    if additions:
        with backlog.open("a", encoding="utf-8") as target:
            target.writelines(additions)


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
    log(f"Start Forge — project={args.project} brief={args.brief} "
        f"batch_size={cfg.batch_size} max_tdd_rounds={cfg.max_tdd_rounds}")
    count = 0
    try:
        while not args.max_iters or count < args.max_iters:
            log(f"--- iteracja {count + 1}"
                + (f"/{args.max_iters}" if args.max_iters else "") + " ---")
            if not one_iteration(cfg, args.project, state): break
            state.save(str(path)); count += 1
    except (AgentError, InvalidDecision, LimitExhausted) as exc:
        state.save(str(path))
        print(f"Forge zatrzymany bezpiecznie: {exc}. Checkpoint zapisano w {path}.",
              file=__import__("sys").stderr, flush=True)
        return 3 if isinstance(exc, LimitExhausted) else 1
    log("Forge: pętla zakończona (brak dalszej pracy lub limit iteracji).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
