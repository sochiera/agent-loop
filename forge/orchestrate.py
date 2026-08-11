"""CLI, bootstrap, planowanie i wyłącznie pipeline KISS."""
from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from .agents import (AgentError, LimitExhausted, _extract_json_detail, extract_json,
                     log, run_agent, run_planner, run_role, run_role_session)
from .config import Config, DEFAULT_TASK_DIFFICULTY, TASK_DIFFICULTIES
from . import brief
from . import backlog
from . import ledger
from . import master_gate
from . import notebooks
from . import prompts
from . import preflight
from . import runlock
from . import snapshot
from . import verify
from . import verdict as verdict_contract
from .shellrun import run_shellfree
from .state import State
from .task_pipeline import (InvalidDecision, parse_coder_decision, parse_review_decision,
                            parse_tester_decision, run_tdd_loop, select_decision)

_JSON_RETRY = """

Poprzednia odpowiedź nie spełniła kontraktu. Nie wykonuj dalszych zmian.
W wartościach JSON nie używaj cudzysłowów: ani typograficznych („ ” « »), ani
prostego ". Cytuj apostrofami albo pisz bez cudzysłowu.
Zwróć teraz wyłącznie jeden poprawny obiekt JSON w formacie podanym wyżej.
"""

_TASK_STATE_FIELDS = (
    "current_task", "task_phase", "tdd_round",
    "tester_session", "coder_session", "tester_decision", "tester_handoff",
    "coder_summary", "no_change_rounds", "round_changed", "suite_regression",
    "tester_record", "coder_record", "review_notes",
    "review_suggestions_pending", "corrections_done", "corrections_tree_hash",
    "task_start_tag", "coder_tree_hash",
)


def git(project: str, *args: str, check: bool = True,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=project, text=True, capture_output=True,
                          check=check, env=env)


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
    dirty = git(project, "status", "--porcelain").stdout.strip()
    if dirty:
        raise AgentError(
            f"niezmiennik Forge złamany: drzewo robocze nie jest czyste przed "
            f"{phase}; preflight powinien był je obsłużyć:\n{dirty[:2000]}")


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


# Kanoniczny identyfikator zadania. Nie jest to konwencja kosmetyczna: z tego
# formatu `_next_task_index` i porządkowanie archiwum wyliczają numer następnego
# wsadu, więc identyfikator poza nim nie zostałby policzony i kolejny wsad
# nadpisałby pliki zadań. `_TASK_ID_MENTION` musi opisywać tę samą gramatykę —
# stąd jeden wzorzec, a nie dwa, które mogłyby się rozjechać. Sam wzorzec
# mieszka w `ledger`, bo czyta go też bramka mistrza i mianowniki raportu, a
# `ledger` jest jedynym modułem-liściem widocznym dla wszystkich trzech.
_TASK_ID_BODY = ledger.TASK_ID_BODY
_TASK_ID = re.compile(_TASK_ID_BODY)


def valid_task_id(task_id: str) -> bool:
    return bool(_TASK_ID.fullmatch(task_id))


def build_task_from_plan(project: str, raw: dict) -> dict:
    difficulty = raw.get("difficulty", DEFAULT_TASK_DIFFICULTY)
    if difficulty not in TASK_DIFFICULTIES:
        difficulty = DEFAULT_TASK_DIFFICULTY
    dependencies = raw.get("depends_on", [])
    if not isinstance(dependencies, (list, tuple)):
        dependencies = [dependencies]
    return {"id": str(raw.get("id", "")), "title": raw.get("title", "(bez tytułu)"),
            "file": raw.get("file", ""),
            "difficulty": difficulty,
            "story": str(raw.get("story", "") or "").strip(),
            "depends_on": [str(item) for item in dependencies if str(item)]}


def _warn_unknown_story(project: str, task: dict) -> None:
    story = str(task.get("story", "")).strip()
    if not story:
        return
    known, _orphans = backlog.load(project)
    if story not in {item.id for item in known}:
        message = (f"plan: ostrzeżenie — {task.get('id', '?')} wskazuje "
                   f"nieznaną historyjkę {story}")
        log(message)
        ledger.append(project, message)


def _set_story_status(project: str, story_id: str, status: str,
                      reason: str = "") -> bool:
    if not story_id:
        return False
    path = Path(project, "BACKLOG.md")
    if not path.is_file():
        return False
    try:
        old = path.read_text(encoding="utf-8")
        new = backlog.set_status(old, story_id, status, reason)
    except KeyError:
        ledger.append(project, f"story: ostrzeżenie — nieznana historyjka {story_id}")
        return False
    if new != old:
        path.write_text(new, encoding="utf-8")
        ledger.append(project, f"story: {story_id} → {status}")
        return True
    return False


def _coerce_backlog_statuses(project: str, before: list[backlog.Story]) -> None:
    """Przywróć kolumnę statusu do prawdy Forge po turze Product Ownera.

    Leczy również skażenie spoza tej fazy — status wpisany ręcznie albo przez
    turę roli zadaniowej znika przy najbliższym przeglądzie, bez osobnej
    maszynerii pilnującej BACKLOG.md przy każdej turze.
    """
    path = Path(project, "BACKLOG.md")
    if not path.is_file():
        return
    text, changes = backlog.coerce_statuses(
        path.read_text(encoding="utf-8"), before)
    if changes:
        path.write_text(text, encoding="utf-8")
        ledger.append(project, "po: statusy przywrócone przez Forge — "
                      + "; ".join(changes)[:200])


def _ledger_context(project: str, lines: int) -> str:
    """Ogon dziennika doklejany na końcu promptu roli."""
    return prompts.ledger_context(ledger.tail(project, lines))


def _story_status(project: str, story_id: str) -> str:
    stories, _ = backlog.load(project)
    for story in stories:
        if story.id == story_id:
            return story.status
    return ""


def stories_touched_since(project: str, sha: str = "") -> set[str]:
    """Historyjki z ukończonych tasków; SHA jest markerem świeżości raportu."""
    del sha  # ledger jest runtime'em i nie jest commitowany razem ze zmianą taska.
    found: set[str] = set()
    pattern = re.compile(r"task-\d+ UKOŃCZONE(?:\s+\((US-\d{3})\))?")
    for line in ledger.tail(project, ledger.KEEP_LINES).splitlines():
        match = pattern.search(line)
        if match and match.group(1):
            found.add(match.group(1))
    return found


def phase_plan_batch(cfg: Config, project: str, state: State, logf) -> dict:
    _housekeeping(cfg, project)
    feedback = Path(project, cfg.runtime_dir, "verification", "latest-feedback.md")
    failures = Path(project, cfg.runtime_dir, "failures.md")
    steering = _steering_path(cfg, project)
    start_index = _next_task_index(project)
    next_batch = state.plan_batches + 1
    log(f"Planowanie: proszę planistę o maks. {cfg.batch_size} zadań od task-{start_index:03d}…")
    plan_prompt = prompts.plan_batch_prompt(
        cfg.batch_size, start_index, state.project_kind,
        verify_feedback_path=str(feedback) if feedback.exists() else "",
        failure_feedback_path=str(failures) if failures.exists() else "",
        steering_path=str(steering) if steering.exists() else "",
        require_debt=next_batch % 5 == 0)
    plan_prompt += _ledger_context(project, ledger.PHASE_LINES)
    # Mistrz widzi w dzienniku również historię wsadów — serię zadań ginących
    # na round_limit potrafi skomentować zanim planista utnie kolejny za grubo.
    plan_prompt += prompts.master_note_suffix(
        _master_notes(cfg, project, logf,
                      plan_sift_streak=state.plan_sift_streak).get("planner", ""))
    data = _decision_with_retry(
        plan_prompt,
        lambda value: run_planner(value, cfg, project, logf("plan")),
        lambda text: _parse_json_object(text, project=project))
    tasks = []
    # Odsiew musi być POLICZALNY, nie tylko widoczny w logu: to jedyny sygnał,
    # że pokrętło `batch_size` przekroczyło zdolność planisty do domykania
    # wsadu. Bez niego "utworzono 5 zadań" po prośbie o 8 nie odróżnia "planista
    # uznał, że więcej nie trzeba" od "planista się urwał w połowie".
    declared = 0
    sifted: list[str] = []
    for raw in data.get("tasks", []):
        declared += 1
        task = build_task_from_plan(project, raw)
        if not valid_task_id(task["id"]):
            # Odrzucamy zamiast renumerować: zgadnięty numer mógłby wskazać
            # cudzy, istniejący plik zadania. Gdy nie zostanie żadne zadanie,
            # faza kończy się jawnym błędem i checkpointem.
            log(f"Planowanie: pomijam zadanie o identyfikatorze {task['id']!r} "
                "— wymagany format task-NNN.")
            ledger.append(project, "plan: pominięto zadanie o niepoprawnym "
                                   f"identyfikatorze {task['id']!r}")
            sifted.append(task["id"] or "(bez identyfikatora)")
            continue
        # Dwie różne awarie planisty, więc dwa różne komunikaty: brak pola
        # `file` znaczy "nie zadeklarował opisu", a brak pliku na dysku —
        # "zadeklarował i nie zapisał".
        if not task["file"]:
            log(f"Planowanie: odsiewam {task['id']} — planista nie podał pliku "
                "opisu zadania.")
            sifted.append(task["id"])
            continue
        if not Path(project, task["file"]).is_file():
            log(f"Planowanie: odsiewam {task['id']} — opis {task['file']!r} "
                "nie istnieje na dysku.")
            sifted.append(task["id"])
            continue
        tasks.append(task)
        _warn_unknown_story(project, task)
    # Seria liczy się przez WSADY, a nie przez wpisy dziennika: jeden odsiew to
    # szum (planista bywa ucięty przez limit), dopiero powtórzony znaczy, że
    # `batch_size` przekroczył jego zdolność domykania wsadu.
    state.plan_sift_streak = state.plan_sift_streak + 1 if sifted else 0
    if sifted:
        # Jeden wpis zbiorczy, nie jeden na zadanie: dziennik jest wejściem
        # mistrza, a trzy linie o tym samym zdarzeniu rozmyłyby jego słownik
        # wzorców. Stoi PRZED linią wsadu, którą uszczegóławia — raport czyta
        # obie razem (patrz report.plan_batches). Zapisujemy przed jawnym
        # błędem poniżej: wsad odsiany w całości jest najmocniejszym sygnałem,
        # jaki mistrz może dostać, i musi przeżyć checkpoint.
        ledger.append(project, f"plan: zadeklarowano {declared}, przyjęto "
                               f"{len(tasks)} (odsiew: {', '.join(sifted)})")
    if not tasks and not data.get("no_more_tasks"):
        raise AgentError("planista nie utworzył żadnego poprawnego zadania")
    state.plan_batches = next_batch
    if tasks:
        log(f"Planowanie: utworzono {len(tasks)} zadań: {', '.join(t['id'] for t in tasks)}")
        ledger.append(project, f"plan: utworzono {len(tasks)} zadań "
                               f"({tasks[0]['id']}…{tasks[-1]['id']})")
        state.empty_plans = 0
    elif data.get("no_more_tasks"):
        log("Planowanie: planista zgłosił brak dalszych zadań.")
        ledger.append(project, "plan: planista zgłosił brak dalszych zadań")
        state.empty_plans += 1
    state.task_queue = tasks
    for task in tasks:
        story = str(task.get("story", "")).strip()
        if story and _story_status(project, story) == "do weryfikacji":
            # Nowy wsad świadomie dokłada pracę do wcześniej zamkniętej
            # historyjki; to normalny powrót do stanu `w toku`, nie regres.
            _set_story_status(project, story, "w toku")
    # Wsad wyraźnie krótszy od zamówionego jest jedynym sygnałem drenażu,
    # który mamy bez czytania BACKLOG.md przed następnym planowaniem.
    state.batch_drained = len(tasks) * 2 < cfg.batch_size
    # Notatka przeglądu kierunku jest jednorazowym wejściem do planowania. Gdyby
    # została, każdy kolejny wsad płaciłby za czytanie już rozliczonej treści.
    steering.unlink(missing_ok=True)
    commit_all(project, "docs: plan wsadowy i backlog", cfg)
    return {"no_more_tasks": bool(data.get("no_more_tasks")) and not tasks}


def _reviewed_bootstrap(cfg: Config, project: str, logf, *, label: str,
                        attempt, review_prompt, log_phase: str,
                        base_sha: str = "", reviewer_role: str = "bootstrap_reviewer") -> dict:
    """Buduj i recenzuj, aż recenzent zaakceptuje albo skończy się budżet.

    Bootstrap i przegląd kierunku wyznaczają kierunek całej dalszej pracy, więc
    błąd propaguje się na każde kolejne zadanie. Jedna recenzja bez prawa do
    poprawki marnowałaby całą pracę przy pierwszej drobnej uwadze, a nieskończona
    pętla paliłaby najsilniejszy model. ``max_bootstrap_reviews`` godzi oba
    ryzyka: po wyczerpaniu budżetu decyzja należy do użytkownika.

    ``attempt(notes)`` wykonuje (kolejne) podejście i zwraca jego JSON;
    ``review_prompt(data)`` buduje prompt recenzji dla tego wyniku.

    Recenzentowi wolno eksperymentować w drzewie — postawienie mocnej tezy o
    kierunku często wymaga uruchomienia kodu i podmiany jednej linii, a zakaz
    zapisu kupowałby czystość drzewa za cenę płytszej recenzji. Werdykt liczy
    się jako jedyny wynik jego tury: cokolwiek zostawił w drzewie i w historii,
    wraca do stanu, który sam oglądał.
    """
    notes: list[str] = []
    for round_number in range(1, cfg.max_bootstrap_reviews + 1):
        data = attempt(notes)
        before = _tree_manifest(project)
        snapshot = _snapshot_tree(project)
        log(f"{label}: recenzja {round_number}/{cfg.max_bootstrap_reviews} "
            "(świeży recenzent)…")
        verdict = _decision_with_retry(
            review_prompt(data),
            lambda value: run_role(
                reviewer_role, value, cfg, project, logf(log_phase)),
            lambda text: parse_review_decision(text, project=project))
        # Sam commit nie rusza plików, więc odcisk drzewa by go nie zauważył —
        # a przesunięty HEAD unieważnia bazę zarówno recenzji, jak i cofania.
        _restore_head(project, base_sha, f"{label} (recenzent)")
        restored = _restore_snapshot(project, snapshot, before)
        if restored:
            log(f"{label}: cofnięto zmiany recenzenta: "
                + _describe_turn_changes(restored))
            ledger.append(project, f"{label}: cofnięto zmiany recenzenta "
                                   + _describe_turn_changes(restored))
        notes = [str(note) for note in verdict.data.get("notes", [])]
        ledger.append(project, f"{label} recenzja {round_number}"
                               f"→{verdict.status}: {'; '.join(notes)[:160]}")
        if verdict.status == "approve":
            log(f"{label}: recenzja zaakceptowana.")
            return data
        log(f"{label}: recenzja wymaga zmian: {'; '.join(notes)[:300]}")
    raise AgentError(
        f"{label}: recenzent odrzucił wynik {cfg.max_bootstrap_reviews} razy "
        f"({'; '.join(notes)[:300]}). Potrzebna decyzja użytkownika.")


def phase_bootstrap(cfg: Config, project: str, state: State, logf) -> None:
    log("Bootstrap: analiza briefu i budowa szkieletu projektu…")
    brief_text = Path(cfg.brief_path).read_text(encoding="utf-8")
    base_sha = git(project, "rev-parse", "HEAD", check=False).stdout.strip()
    before = _tree_manifest(project)

    def attempt(notes: list[str]) -> dict:
        if notes:
            log("Bootstrap: poprawiam szkielet po uwagach recenzenta…")
        data = _decision_with_retry(
            prompts.bootstrap_prompt(brief_text, review_notes=notes),
            lambda value: run_planner(
                value, cfg, project, logf("bootstrap"), role="bootstrap"),
            lambda text: _parse_json_object(text, project=project))
        if not data.get("test_cmd"):
            raise AgentError("bootstrap nie zwrócił poprawnego JSON-a")
        if not Path(project, brief.PROJECT_DOC_PATH).is_file():
            # Bez tego pliku planista straciłby kierunek projektu razem z
            # briefem, a przegląd kierunku nie miałby czego aktualizować.
            raise AgentError(f"bootstrap nie utworzył {brief.PROJECT_DOC_PATH}")
        log(f"Bootstrap: test_cmd={data['test_cmd']!r} "
            f"build_cmd={data.get('build_cmd', '')!r} kind={data.get('kind', 'app')!r}")
        suite_ok, suite_output = build_then_test_result(
            project, data.get("build_cmd", ""), data["test_cmd"],
            cfg.agent_timeout_s)
        if not suite_ok:
            detail = (suite_output or "").strip()
            suffix = f": {detail[-2000:]}" if detail else ""
            raise AgentError(f"testy bootstrapu nie przeszły{suffix}")
        log("Bootstrap: testy początkowe zielone.")
        return data

    try:
        data = _reviewed_bootstrap(
            cfg, project, logf, label="Bootstrap", attempt=attempt,
            review_prompt=lambda result: prompts.bootstrap_architecture_review_prompt(
                cfg.brief_path, result["test_cmd"]),
            log_phase="bootstrap-review", base_sha=base_sha)
    except Exception as exc:  # noqa: BLE001 — bootstrap nie może zostawić pół-szkieletu
        changed = _turn_changes(before, _tree_manifest(project))
        dest = _dump_phase_work(project, cfg, "Bootstrap", base_sha, changed, exc)
        if dest:
            log(f"Bootstrap: praca przed rewertem zachowana w {dest}")
            ledger.append(project, f"bootstrap: praca zachowana w {dest}")
        _restore_head(project, base_sha, "Bootstrap")
        _revert_paths(project, base_sha, changed)
        raise
    state.test_cmd = data["test_cmd"]
    state.build_cmd = data.get("build_cmd", "")
    state.project_kind = data.get("kind", "app")
    profile = data.get("verify") or {}
    state.verify_targets = list(profile.get("targets") or [])
    for key in ("smoke_cmd", "flash_cmd", "target_cmd", "ci_status_cmd", "ci_logs_cmd"):
        setattr(state, key, str(profile.get(key, "")))
    state.bootstrapped = True
    # Snapshot i skrót zapisujemy dopiero po zaakceptowanej recenzji: awaria
    # wcześniej ma zostawić brief jako niezsynchronizowany, nie jako rozliczony.
    brief.write_snapshot(project, brief_text)
    state.brief_digest = brief.digest(brief_text)
    log("Bootstrap: recenzja zaakceptowana, commituję.")
    commit_all(project, "chore: bootstrap projektu", cfg)
    # Pierwszy przegląd kierunku ma zobaczyć wyłącznie pracę wykonaną PO
    # bootstrapie, a nie sam commit szkieletu.
    state.steered_at_sha = git(project, "rev-parse", "HEAD", check=False).stdout.strip()


def _steering_path(cfg: Config, project: str) -> Path:
    return Path(project, cfg.runtime_dir, "steering.md")


def _po_trigger(cfg: Config, project: str, state: State) -> str:
    """Wyzwalacze Product Ownera: brief, refill, potem kadencja."""
    if brief.changed(project, state.brief_digest, brief.read(cfg.brief_path)):
        return "brief"
    if state.current_task or state.task_queue:
        return ""
    stories, _orphans = backlog.load(project)
    if not state.backlog_migrated:
        return "refill"
    if (backlog.count_open(stories) < cfg.backlog_low_water
            and state.po_refill_batch != state.plan_batches):
        return "refill"
    if state.steering_due:
        return "refill"
    if state.plan_batches - state.steered_at_batch >= cfg.steering_batches:
        return "cadence"
    if state.batch_drained:
        return "cadence"
    return ""


def _restore_head(project: str, base_sha: str, label: str) -> bool:
    """Cofnij commity roli, zostawiając jej zmiany w drzewie roboczym.

    Bez tego cała bramka zakresu byłaby do obejścia jednym `git commit`:
    przywracanie „z HEAD" odtwarzałoby wtedy wersję już zmienioną przez rolę, a
    recenzent oglądałby pusty diff. Kotwicą jest SHA sprzed fazy, nie HEAD.
    """
    if not base_sha:
        return False
    if git(project, "rev-parse", "HEAD", check=False).stdout.strip() == base_sha:
        return False
    git(project, "reset", "--mixed", base_sha, check=False)
    log(f"{label}: rola commitowała mimo zakazu — HEAD cofnięty na {base_sha[:8]}.")
    ledger.append(project, f"{label}: cofnięto commit roli (HEAD → {base_sha[:8]})")
    return True


def _revert_paths(project: str, base_sha: str, paths: list[str]) -> list[str]:
    """Przywróć ścieżki do stanu z ``base_sha``; pliki nowe usuń."""
    if not paths:
        return []
    untracked = set(_untracked(project))
    tracked = [name for name in paths if name not in untracked]
    if tracked:
        git(project, "checkout", base_sha or "HEAD", "--", *tracked, check=False)
    for name in paths:
        if name in untracked:
            Path(project, name).unlink(missing_ok=True)
    return paths


def _revert_out_of_scope(project: str, base_sha: str,
                         paths: list[str]) -> list[str]:
    """Cofnij zmiany poza zakresem przeglądu kierunku.

    Zakres jest sprawdzany deterministycznie, a nie tylko opisany w promptcie.
    Cofnięcie zamiast porzucenia całej operacji jest bezpieczne, bo dozwolone
    pliki to wyłącznie backlog i opis projektu — pozostają spójne bez zmian,
    których nie wolno było wykonać.
    """
    return _revert_paths(project, base_sha, brief.out_of_scope(paths))


def _write_steering_note(cfg: Config, project: str, data: dict,
                         dropped: list[dict]) -> None:
    """Jednorazowa notatka dla planisty; konsumuje ją najbliższy wsad."""
    lines = ["# Przegląd kierunku — kontekst planowania", "",
             str(data.get("summary", "")).strip() or "(bez podsumowania)", ""]
    changes = [str(item).strip() for item in data.get("changes", []) if str(item).strip()]
    if changes:
        lines.append("Zmiany przeniesione do backlogu i docs/PROJECT.md:")
        lines += [f"- {item}" for item in changes]
        lines.append("")
    reopened = data.get("stories_reopened", [])
    if reopened:
        # Powód wznowienia jest jedyną treścią, którą PO wnosi do historyjki
        # już raz zamkniętej — bez niego planista zaplanowałby ją od zera.
        lines.append("Historyjki cofnięte do kolejki (co w nich nie działa):")
        lines += [f"- {item['id']}: {item['reason']}" for item in reopened]
        lines.append("")
    if dropped:
        lines.append("Zadania wycofane z kolejki do ponownego zaplanowania "
                     "(ich założenia zmienił przegląd kierunku):")
        lines += [f"- {task.get('id', '?')}: {task.get('title', '')}"
                  for task in dropped]
        lines.append("")
    path = _steering_path(cfg, project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _append_product_owner_note(project: str, text: str) -> None:
    if not text.strip():
        return
    notebooks.ensure_project(project, ".forge")
    path = notebooks.project_path(project, ".forge", "product-owner")
    existing = path.read_text(encoding="utf-8")
    entry = "- " + " ".join(text.split())
    if entry not in existing.splitlines():
        path.write_text(existing.rstrip() + "\n" + entry + "\n", encoding="utf-8")


def phase_product_owner(cfg: Config, project: str, state: State, logf,
                        trigger: str = "cadence", story_report: str = "") -> None:
    """Jedna, recenzowana tura Product Ownera z parserem przed recenzją."""
    from . import backlog

    if trigger == "backlog":
        trigger = "refill"
    current = brief.read(cfg.brief_path)
    previous = brief.snapshot(project)
    try:
        change = brief.diff(previous, current) if trigger == "brief" and current is not None else ""
    except brief.TooLargeToSync as exc:
        raise AgentError(f"brief zbyt duży do synchronizacji: {exc}") from exc

    base_sha = git(project, "rev-parse", "HEAD", check=False).stdout.strip()
    before_manifest = _tree_manifest(project)
    before_stories, before_orphans = backlog.load(project)
    if before_orphans:
        # Stary backlog przechodzi przez migrację PO; dla już kanonicznego
        # pliku nieparsowalność jest błędem wejściowym, nie opinią recenzenta.
        ledger.append(project, "po: backlog przed turą zawiera sieroty")
    queued = [f"{task.get('id', '?')}: {task.get('title', '')}"
              for task in state.task_queue]
    parked_path = Path(project, ".forge", "parked.md")
    parked = parked_path.read_text(encoding="utf-8") if parked_path.exists() else ""
    notebook_path = str(Path(cfg.runtime_dir, "notebooks", "product-owner.md"))
    migration = not state.backlog_migrated
    parser_corrections: list[str] = []
    previous_violations: list[str] = []
    review_corrections: list[str] = []
    data: dict = {}
    approved = False
    last_notes: list[str] = []
    try:
        for round_number in range(1, cfg.max_bootstrap_reviews + 1):
            prompt = prompts.product_owner_prompt(
                trigger=trigger, brief_diff=change, story_report=story_report,
                queued_tasks=queued, parked=parked, migration=migration,
                notebook_path=notebook_path, review_notes=review_corrections,
                max_backlog=cfg.max_backlog_stories)
            prompt += _ledger_context(project, ledger.PHASE_LINES)
            if parser_corrections:
                prompt += "\n\n" + prompts.po_parse_corrections_prompt(parser_corrections)
            data = _decision_with_retry(
                prompt,
                lambda value: run_role(
                    "product_owner", value, cfg, project, logf("product-owner")),
                lambda text: _parse_product_owner_decision(text, project=project),
            )
            _restore_head(project, base_sha, "Product Owner")
            reverted = _revert_out_of_scope(
                project, base_sha, _turn_changes(before_manifest, _tree_manifest(project)))
            if reverted:
                ledger.append(project, "po: cofnięto zmiany poza zakresem "
                               + _describe_turn_changes(reverted))

            _coerce_backlog_statuses(project, before_stories)
            after_stories, orphans = backlog.load(project)
            violations = backlog.validate_hard(
                before_stories, after_stories, data["stories_dropped"], orphans,
                data["stories_reopened"])
            if violations:
                if violations == previous_violations:
                    # Ten sam zestaw naruszeń po pełnej turze z tymi uwagami
                    # znaczy, że kolejna tura go nie usunie. Budżet korekt jest
                    # tu wart tyle, co cztery tury najdroższego modelu.
                    raise AgentError(
                        "Product Owner: parser zgłasza te same naruszenia drugi "
                        "raz z rzędu, więc kolejne tury ich nie usuną: "
                        + "; ".join(violations)[:300])
                previous_violations = violations
                parser_corrections = violations
                review_corrections = []
                ledger.append(project, "po parser: " + "; ".join(violations)[:240])
                log("Product Owner: korekta strukturalna "
                    f"{round_number}/{cfg.max_bootstrap_reviews}")
                continue
            # Poprawna struktura przerywa serię: to samo naruszenie po turze,
            # która go NIE miała, jest nawrotem po cudzej uwadze, a nie dowodem
            # na to, że PO nie umie go usunąć. Bez zerowania odsiew ucinałby
            # przebieg mimo realnego postępu między jednym a drugim wystąpieniem.
            previous_violations = []

            review_before = _tree_manifest(project)
            review_snapshot = _snapshot_tree(project)
            verdict = _decision_with_retry(
                prompts.po_review_prompt(data, max_backlog=cfg.max_backlog_stories),
                lambda value: run_role(
                    "po_reviewer", value, cfg, project, logf("po-review")),
                lambda text: parse_review_decision(text, project=project),
            )
            # Recenzentowi wolno eksperymentować w drzewie jak wszędzie indziej
            # (_reviewed_bootstrap) — werdykt liczy się jako jedyny wynik jego
            # tury, cokolwiek zostawił wraca do stanu sprzed recenzji.
            _restore_head(project, base_sha, "Product Owner (recenzent)")
            reviewer_reverted = _restore_snapshot(project, review_snapshot, review_before)
            if reviewer_reverted:
                ledger.append(project, "po: cofnięto zmiany recenzenta "
                               + _describe_turn_changes(reviewer_reverted))
            last_notes = [str(note) for note in verdict.data.get("notes", [])]
            ledger.append(project, f"po recenzja {round_number}→{verdict.status}: "
                           + "; ".join(last_notes)[:200])
            if verdict.status == "approve":
                approved = True
                break
            review_corrections = last_notes
            parser_corrections = []
            # Recenzent ocenia semantykę; następna tura dostaje uwagi przez
            # jawny slot korekt zamiast mieszać je z błędami parsera.
            prompt_notes = review_corrections
            # Zachowaj je w lokalnym buforze i dołącz przy następnym obrocie.
            # `corrections` jest rozróżnialne po prefiksie w ledgerze, ale dla
            # modelu wystarczy wspólny mechanizm uwag.
            if prompt_notes:
                ledger.append(project, "po: request_changes — " + "; ".join(prompt_notes)[:200])
        if not approved:
            raise AgentError(
                "Product Owner: wyczerpano budżet korekt/recenzji; "
                + "; ".join(last_notes or parser_corrections)[:300])
    except Exception as exc:  # noqa: BLE001 — faza nie zostawia pół-zmian
        changed = _turn_changes(before_manifest, _tree_manifest(project))
        dest = _dump_phase_work(project, cfg, "Product Owner", base_sha, changed, exc)
        if dest:
            ledger.append(project, f"po: praca zachowana w {dest}")
        _restore_head(project, base_sha, "Product Owner")
        _revert_paths(project, base_sha, changed)
        raise

    # Dopiero zaakceptowana tura może poprosić Forge o deterministyczne fakty.
    for item in data["stories_dropped"]:
        try:
            path = Path(project, "BACKLOG.md")
            path.write_text(
                backlog.set_status(path.read_text(encoding="utf-8"), item["id"],
                                   "porzucona", item["reason"]),
                encoding="utf-8")
            ledger.append(project, f"po: {item['id']} porzucona ({item['reason']})")
        except KeyError:
            ledger.append(project, f"po: ostrzeżenie — nieznane stories_dropped {item['id']}")

    # Jedyna droga, którą PO może cofnąć historyjkę do kolejki: deklaracja z
    # powodem, wykonywana przez Forge. Bez tego kanału jego jedynym sposobem na
    # powiedzenie „to nie działa" było skasowanie historyjki jako porzuconej.
    #
    # Ogłaszamy wyłącznie wznowienia, które faktycznie trafiły do pliku. Wpis w
    # dzienniku i linia w notatce kierunku są dla dalszego procesu faktem: ID
    # bez pokrycia w backlogu (walidator już je odsiewa, ale to ostatnia
    # instancja przed ogłoszeniem) dałoby planiście pracę bez historyjki.
    applied_reopened: list[dict[str, str]] = []
    for item in data["stories_reopened"]:
        changed = _set_story_status(project, item["id"], "nowa")
        if not changed and not _story_status(project, item["id"]):
            ledger.append(
                project, f"po: ostrzeżenie — nieznane stories_reopened {item['id']}")
            continue
        ledger.append(project, f"po: {item['id']} wraca do kolejki — {item['reason']}")
        applied_reopened.append(item)
    data["stories_reopened"] = applied_reopened

    _append_product_owner_note(project, data.get("notebook", ""))
    _write_steering_note(cfg, project, data, state.task_queue if data["replan"] else [])
    if data["replan"]:
        state.task_queue = []
        state.task_phase = ""
    if current is not None:
        brief.write_snapshot(project, current)
        state.brief_digest = brief.digest(current)
    state.steered_at_batch = state.plan_batches
    state.steering_due = False
    state.batch_drained = False
    if trigger == "refill":
        state.po_refill_batch = state.plan_batches
    state.backlog_migrated = True
    state.goal_confirmed = bool(data["goal_reached"])
    if state.goal_confirmed:
        state.task_queue = []
        state.task_phase = "verify_goal"
    parked_path.unlink(missing_ok=True)
    ledger.append(
        project,
        f"po ({trigger}): +{len(data['stories_added'])} historyjek, "
        f"-{len(data['stories_dropped'])} porzuconych, "
        f"~{len(data['stories_reopened'])} wznowionych, "
        f"replan={data['replan']}, goal={state.goal_confirmed}",
    )
    commit_all(project, "docs: przegląd Product Ownera", cfg)
    state.steered_at_sha = git(project, "rev-parse", "HEAD", check=False).stdout.strip()


def _write_current_task(project: str, task: dict) -> None:
    source, target = Path(project, task["file"]), Path(project, ".forge", "current_task.md")
    target.parent.mkdir(parents=True, exist_ok=True)
    body = source.read_text(encoding="utf-8")
    story = str(task.get("story", "")).strip()
    target.write_text((f"Historyjka: {story}\n\n" if story else "") + body,
                      encoding="utf-8")


def _append_review_nits(cfg: Config, project: str, task_id: str,
                        nits: list[str]) -> None:
    """Zachowaj kosmetyczne uwagi poza pętlą TDD.

    To celowo trwały, nieograniczony ślad audytowy, a nie stan zadania ani
    handoff: Forge nie podaje go automatycznie agentom. Ponowne podanie nita
    testerowi lub koderowi wywołałoby kosztowną rundę, której ten kanał ma
    zapobiegać.
    """
    if not nits:
        return
    path = Path(project, cfg.runtime_dir, "nits.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as target:
        for nit in nits:
            target.write(f"- {task_id}: {nit}\n")


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


def _snapshot_tree(project: str) -> str:
    """Obiekt drzewa ze stanem worktree — bez ruszania indeksu i HEAD.

    Kotwicą dla tury recenzenta nie może być SHA sprzed fazy: leży tam stan
    sprzed pracy autora przeglądu, więc przywracanie z niego kasowałoby też jego
    zmiany, gdyby recenzent dotknął tego samego pliku. Osobny indeks w katalogu
    tymczasowym pozwala zapisać dokładnie to, co recenzent zobaczy.
    """
    index = Path(tempfile.mkdtemp(prefix="forge-snapshot-")) / "index"
    env = {**os.environ, "GIT_INDEX_FILE": str(index)}
    try:
        git(project, "add", "-A", env=env, check=False)
        snapshot = git(project, "write-tree", env=env, check=False).stdout.strip()
    finally:
        shutil.rmtree(index.parent, ignore_errors=True)
    # Bez kotwicy nie ma czego przywracać, a cicha pustka wpuściłaby
    # eksperymenty recenzenta do zaakceptowanego wyniku.
    if not snapshot:
        raise AgentError("nie udało się zapisać snapshotu drzewa przed recenzją")
    return snapshot


def _restore_snapshot(project: str, snapshot: str,
                      before: dict[str, str]) -> list[str]:
    """Przywróć drzewo do stanu ``snapshot``; zwróć cofnięte ścieżki."""
    changed = _turn_changes(before, _tree_manifest(project))
    if not changed:
        return []
    known = [name for name in changed if name in before]
    if known:
        # `checkout <tree> -- ścieżki` zapisuje TAKŻE indeks: plik nieśledzony,
        # którego dotknął recenzent, zostałby zastagowany i przestał być
        # nieśledzony — a wtedy sprzątanie fazy (`_revert_paths`) nie umie go
        # już usunąć. `restore --worktree` rusza wyłącznie drzewo robocze.
        git(project, "restore", "--source", snapshot, "--worktree", "--",
            *known, check=False)
    for name in changed:
        if name not in before:
            Path(project, name).unlink(missing_ok=True)
    return changed


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


def _tester_statuses(state: State) -> tuple[str, ...]:
    """Statusy legalne w TEJ turze testera — dokładnie jak w jego promptcie."""
    if state.review_suggestions_pending:
        return verdict_contract.TESTER_STATUSES
    return tuple(name for name in verdict_contract.TESTER_STATUSES
                 if name != "finalize")


def _verdict_turn(cfg: Config, project: str, role: str, call, *, statuses=None) -> str:
    """Tura roli z walidacją werdyktu w czasie rzeczywistym.

    Werdykt zatwierdzony skryptem WYGRYWA z tekstem tury: przeszedł ten sam
    kontrakt, a rola dostała szansę poprawić go bez powtarzania tury. Brak
    pliku znaczy „rola nie użyła skryptu" — wtedy jedziemy tekstem, jak dotąd."""
    verdict_contract.prepare(project, cfg.runtime_dir, role, statuses=statuses)
    output = call()
    committed = verdict_contract.read(project, cfg.runtime_dir, role)
    if committed is None:
        return output
    log(f"  rola[{role}]: werdykt zatwierdzony przez "
        f"{cfg.runtime_dir}/verdict.py")
    return json.dumps(committed, ensure_ascii=False)


def _call_role(cfg: Config, project: str, state: State, role: str, prompt: str, log: str) -> str:
    attr = "tester_session" if role == "tester" else "coder_session"
    # Każda tura jest świeża. Kontrolowaną ciągłość zapewniają wyłącznie
    # Context Capsule i prywatny notatnik, również dla Codexa.
    setattr(state, attr, "")
    statuses = (_tester_statuses(state) if role == "tester"
                else verdict_contract.CODER_STATUSES)
    return _verdict_turn(
        cfg, project, role,
        lambda: run_role_session(
            role, prompt, cfg, project, log, session_id=None,
            difficulty=state.current_task.get(
                "difficulty", DEFAULT_TASK_DIFFICULTY))[0],
        statuses=statuses)


# Notatek nie przycinamy — sygnalizujemy tylko, że kontrakt „jedna linia”
# przestał obowiązywać. Drugi próg pilnuje strony naprawdę drogiej: notatnik
# wchodzi w całości do każdej kolejnej tury roli aż do końca zadania, więc
# liczy się suma, a nie pojedynczy wpis.
_LONG_NOTEBOOK_ENTRY = 600
_LARGE_NOTEBOOK = 4000

_MASTER_ROLES = ("tester", "coder", "planner")
# Wzmianka o zadaniu, a nie segment ścieżki: uwaga mistrza cytuje wpisy z
# `pliki=[…]`, więc `tests/task-002.py` w poprawnej uwadze o task-001 nie może
# udawać obcego identyfikatora. Kropki na końcu NIE wykluczamy — zdanie
# „dotyczy task-465." jest częstsze niż plik o takiej nazwie, a przeoczona
# wzmianka jest gorsza od nadmiarowo odrzuconej uwagi (patrz niżej).
_TASK_ID_MENTION = re.compile(r"(?<![\w/])" + _TASK_ID_BODY)


def _scoped_master_notes(notes: dict[str, str], task_id: str) -> dict[str, str]:
    """Uwagi dla testera i kodera muszą dotyczyć AKTYWNEGO zadania.

    Mistrz widzi okno dziennika obejmujące kilka zadań wstecz i regularnie
    adresuje radę o zadaniu już zamkniętym do roli pracującej nad następnym.
    Prompt tego zabrania, ale zakaz w promptcie nie jest gwarancją — ta bramka
    jest. Uwaga dla planisty przechodzi: jego reguła `round_limit` z definicji
    mówi o cudzych, porzuconych zadaniach.

    Heurystyka wzmianki jest świadomie asymetryczna. Mistrz jest doradczy, więc
    nadmiarowo odrzucona uwaga kosztuje tyle, ile jego milczenie — czyli
    zachowanie bazowe — i dodatkowo trafia do logu. Przeoczona wzmianka wpuszcza
    do promptu radę o zamkniętym zadaniu, czyli dokładnie ten defekt, przed
    którym ta bramka broni.
    """
    kept: dict[str, str] = {}
    for role, note in notes.items():
        if role == "planner":
            kept[role] = note
            continue
        if not task_id:
            log(f"  mistrz: odrzucono uwagę dla {role} — żadne zadanie nie "
                "jest aktywne.")
            continue
        foreign = sorted({found for found in _TASK_ID_MENTION.findall(note)
                          if found != task_id})
        if foreign:
            log(f"  mistrz: odrzucono uwagę dla {role} — dotyczy "
                f"{', '.join(foreign)}, a aktywne jest {task_id}.")
            continue
        kept[role] = note
    return kept


def _ask_master(cfg: Config, project: str, logf, prompt: str,
                task_id: str) -> dict[str, str]:
    """Jedno wywołanie mistrza; każda jego awaria daje po prostu brak notatek."""
    # Rola doradcza nie ma prawa przespać godzin backoffu przed realną pracą.
    advisory = replace(cfg, max_limit_retries=0)
    try:
        with tempfile.TemporaryDirectory(prefix="forge-master-") as sandbox:
            # Sandbox jest katalogiem roboczym, ale koszt roli wołanej co rundę
            # musi trafić do telemetrii projektu, a nie zniknąć razem z nim.
            raw = run_role("master", prompt, advisory, sandbox, logf("master"),
                            usage_dir=project, thin=True,
                            system_prompt=prompts.master_system_prompt(),
                            json_schema=prompts.master_json_schema())
        data = extract_json(raw)
    except Exception:  # noqa: BLE001 — rola doradcza nie ma prawa niczego zatrzymać
        return {}
    if not isinstance(data, dict):
        return {}
    notes = _scoped_master_notes(
        {role: value.strip() for role, value in data.items()
         if role in _MASTER_ROLES and isinstance(value, str) and value.strip()},
        task_id)
    for role, note in notes.items():
        log(f"  mistrz → {role}: {note}")
    return notes


def _master_gate_mode(value: str) -> str:
    """`off` / `shadow` / `on` z tolerancją na potoczne zapisy włączenia.

    Dokumentacja i nawyk podpowiadają `=1`, więc rozpoznanie wyłącznie `on`
    dawało po cichu tryb `off` — czyli przeciwieństwo intencji operatora.
    Wartość nierozpoznana celowo znaczy `off`: pokrętło o niepewnym znaczeniu
    nie ma prawa wyciszyć nadzoru.
    """
    mode = (value or "").strip().lower()
    if mode in {"on", "1", "true", "yes", "tak"}:
        return "on"
    return "shadow" if mode == "shadow" else "off"


def _master_notes(cfg: Config, project: str, logf, *, task_id: str = "",
                  next_role: str = "", plan_sift_streak: int = 0) -> dict[str, str]:
    """Notatki mistrza per rola — nadzór procesu, nie merytoryki.

    Mistrz jest doradczy Z KONSTRUKCJI, więc każda jego awaria (błąd, limit,
    śmieciowa odpowiedź) daje brak notatek i pipeline zachowuje się dokładnie
    tak jak bez niego. Widzi wyłącznie dziennik przekazany w promptcie i
    pracuje w katalogu tymczasowym — fizycznie nie ma dostępu do repozytorium,
    więc nie może zmienić drzewa ani wyjść poza swoją rolę.

    ``task_id`` i ``next_role`` mówią mu, gdzie stoi pętla: bez tego brak wpisu
    tury, która dopiero ma ruszyć, wyglądał jak urwany cykl.
    ``plan_sift_streak`` niesie wzorzec, którego z okna dziennika nie da się
    odczytać — patrz ``prompts.master_ledger_prompt``.

    Deterministyczna bramka (``master_gate``) potrafi sprawdzić jego warunki
    interwencji w Pythonie i pominąć całe wywołanie, ale DOMYŚLNIE JEST
    WYŁĄCZONA: oszczędza kilka procent rachunku, a jedna pominięta interwencja
    kosztuje rundy albo całe zadanie (patrz komentarz przy ``Config.master_gate``).
    """
    # Bramka MUSI liczyć się z tych samych dwóch wejść co prompt mistrza —
    # inaczej po restarcie albo po zmianie zapisu wpisu obie widziałyby co
    # innego, a bramka wyciszałaby go tam, gdzie on by zareagował.
    compact_tail = ledger.compact_tail(project)
    round_limits = ledger.round_limit_tasks(project)
    gate = _master_gate_mode(cfg.master_gate)
    fired = ""
    if gate in {"shadow", "on"}:
        fired = master_gate.trigger(compact_tail, round_limits,
                                    task_id=task_id, next_role=next_role,
                                    plan_sift_streak=plan_sift_streak)
        if gate == "on" and not fired:
            return {}
    notes = _ask_master(cfg, project, logf, prompts.master_ledger_prompt(
        compact_tail, round_limits, task_id=task_id, next_role=next_role,
        plan_sift_streak=plan_sift_streak),
        task_id)
    if gate == "shadow":
        # Rozbieżność, której szukamy, to `trigger="" ∧ nota=TAK`: dokładnie te
        # przypadki bramka wyciszyłaby w trybie `on`. Zero z nich w całym
        # przebiegu jest warunkiem przejścia na `on`.
        log(f"  mistrz-bramka: trigger={fired or '\"\"'} → nota: "
            + ("TAK" if notes else "NIE"))
    return notes


def _decision_with_retry(prompt: str, invoke, parser, *, on_reject=None):
    """Jedna tania korekta formatu, potem jawny błąd zamiast ukrytej pętli.

    Powtórzenie tury jest OSTATNIĄ deską ratunku, nie normalnym kanałem korekty
    — role zatwierdzają werdykt skryptem i poprawiają się w trakcie sesji
    (patrz forge/verdict.py). Dlatego odrzucenie zostawia ślad w logu od razu:
    bez niego drugi start roli wyglądał w logu na niewyjaśniony, a gdy retry
    padał z innego powodu (timeout), surowe wyjście pierwszej próby ginęło."""
    first_raw = invoke(prompt)
    try:
        return parser(first_raw)
    except InvalidDecision as exc:
        reason = str(exc)[:800]
        log(f"  UWAGA: werdykt odrzucony ({reason[:300]}) — ponawiam całą turę")
        if on_reject is not None:
            exc.raw_attempts = [first_raw]
            on_reject(exc)
        retry_prompt = (
            prompt + _JSON_RETRY + f"\nPowód odrzucenia: {reason}\n"
        )
        retry_raw = invoke(retry_prompt)
        try:
            return parser(retry_raw)
        except InvalidDecision as retry_exc:
            retry_exc.raw_attempts = [first_raw, retry_raw]
            raise


def _dump_invalid_decision(project: str, cfg: Config, state: State, exc: InvalidDecision) -> None:
    """Zachowaj surowe wyjście agenta, które ubiło bieg: bez tego diagnoza
    formatu po fakcie wymaga surowej telemetrii spoza projektu (jeśli w ogóle
    przeżyła), zamiast jednego pliku obok checkpointu.

    Zrzut jest best-effort i biegnie w handlerze ostatniej szansy: awaria
    zapisu (pełny albo read-only dysk, plik w miejscu katalogu) nie może zjeść
    komunikatu o bezpiecznym zatrzymaniu ani kontraktu kodów wyjścia."""
    raw_attempts = getattr(exc, "raw_attempts", None)
    if not raw_attempts:
        return
    task_id = (state.current_task or {}).get("id") or "_bez_zadania"
    phase = state.task_phase or "nieznana"
    dest = Path(project, cfg.runtime_dir, "failed", task_id, "invalid_json")
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    body = f"faza={phase}\npowód={exc}\n\n"
    for i, raw in enumerate(raw_attempts, start=1):
        body += f"===== próba {i}/{len(raw_attempts)} =====\n{raw}\n\n"
    try:
        dest.mkdir(parents=True, exist_ok=True)
        dest.joinpath(f"{stamp}.txt").write_text(body, encoding="utf-8")
    except OSError as io_exc:
        log(f"  UWAGA: nie zapisano surowego wyjścia agenta ({io_exc})")
        return
    log(f"  Surowe wyjście agenta zapisane w {dest}/{stamp}.txt")


def _dump_phase_work(project: str, cfg: Config, label: str, base_sha: str,
                     paths: list[str], reason: Exception) -> str:
    """Zachowaj pracę fazy przed rewertem, nie zakłócając obsługi awarii."""
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    phase = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "phase"
    dest = Path(project, cfg.runtime_dir, "failed", f"_{phase}", stamp)
    try:
        dest.mkdir(parents=True, exist_ok=True)
        diff_args = ("diff", "--no-ext-diff", base_sha, "--", *paths) if base_sha else (
            "diff", "--no-ext-diff", "--", *paths)
        dest.joinpath("diff.patch").write_text(
            git(project, *diff_args, check=False).stdout, encoding="utf-8")
        dest.joinpath("changed.txt").write_text(
            "\n".join(paths) + ("\n" if paths else ""), encoding="utf-8")
        dest.joinpath("reason.txt").write_text(str(reason) + "\n", encoding="utf-8")
        untracked = set(_untracked(project))
        for rel in paths:
            source = Path(project, rel)
            if rel in untracked and source.is_file():
                target = dest / "untracked" / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
    except OSError as exc:
        log(f"  UWAGA: nie zachowano pracy {label} przed rewertem ({exc})")
        return ""
    return str(dest)


def _parse_json_object(text: str, *, project: str = "", require=None) -> dict:
    """Werdykt roli spoza pętli TDD, wybrany tym samym selektorem co decyzje.

    ``require`` niesie kontrakt roli, jeśli jakiś ma: dopiero on odróżnia
    werdykt od obiektu doklejonego po nim. Bez niego wygrywa pierwszy
    kandydat, czyli dokładnie to, co ta funkcja robiła zawsze."""
    def validate(data: dict) -> dict:
        if require is not None:
            require(data)
        return data

    return select_decision(text, validate, project=project)


def _parse_steering_decision(text: str, *, project: str = "") -> dict:
    """Werdykt przeglądu kierunku o sprawdzonych typach pól sterujących.

    Te pola sterują pętlą, a nie tylko treścią promptu: `"false"` jako tekst
    dałoby ``bool("false") is True`` i zakończyło projekt wbrew intencji roli, a
    `changes` podane stringiem rozsypałoby się w notatce na pojedyncze znaki.
    Niezgodność typu wraca do agenta przez jedną tanią prośbę o korektę.
    """
    return _parse_json_object(text, project=project, require=_require_steering)


def _require_steering(data: dict) -> None:
    for key, default in (("replan", True), ("goal_reached", False)):
        value = data.get(key, default)
        if not isinstance(value, bool):
            raise InvalidDecision(
                f"pole `{key}` musi być typu bool (true/false bez cudzysłowów), "
                f"a jest {type(value).__name__}")
        data[key] = value
    changes = data.get("changes", [])
    if not isinstance(changes, (list, tuple)):
        raise InvalidDecision("pole `changes` musi być listą krótkich opisów")
    data["changes"] = [str(item).strip() for item in changes if str(item).strip()]
    summary = data.get("summary", "")
    if not isinstance(summary, str) or not summary.strip():
        raise InvalidDecision("pole `summary` musi być niepustym tekstem")
    data["summary"] = summary.strip()


def _parse_product_owner_decision(text: str, *, project: str = "") -> dict:
    """Parsuj kontrakt PO, nie nadając statusom znaczenia modelowego."""
    return _parse_json_object(text, project=project, require=_require_product_owner)


def _story_reasons(data: dict, key: str) -> list[dict[str, str]]:
    """Znormalizuj listę ``{id, reason}``; brak powodu jest błędem kontraktu.

    Wspólne dla `stories_dropped` i `stories_reopened`: obie deklaracje każą
    Forge zmienić status historyjki, więc obie muszą nieść uzasadnienie, które
    przeżyje w dzienniku i w notatce dla planisty.
    """
    items = data.get(key, [])
    if not isinstance(items, (list, tuple)):
        raise InvalidDecision(f"pole `{key}` musi być listą obiektów")
    normalized: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            raise InvalidDecision(f"element {key} musi być obiektem")
        story_id = str(item.get("id", "")).strip()
        reason = str(item.get("reason", "")).strip()
        if not story_id or not reason:
            raise InvalidDecision(f"{key} wymaga niepustego id i reason")
        normalized.append({"id": story_id, "reason": reason})
    return normalized


def _require_product_owner(data: dict) -> None:
    _require_steering(data)
    added = data.get("stories_added", [])
    if isinstance(added, str) or not isinstance(added, (list, tuple)):
        raise InvalidDecision("pole `stories_added` musi być listą ID")
    dropped = _story_reasons(data, "stories_dropped")
    reopened = _story_reasons(data, "stories_reopened")
    notebook = data.get("notebook", "")
    if not isinstance(notebook, str):
        raise InvalidDecision("pole `notebook` musi być tekstem")
    data["stories_added"] = [str(item).strip() for item in added if str(item).strip()]
    data["stories_dropped"] = dropped
    data["stories_reopened"] = reopened
    data["notebook"] = notebook.strip()


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
    # Także porażka wznowiona bez przejścia przez ``run_task`` zachowuje
    # komplet template'ów i ewentualne rekordy ze starego checkpointu.
    notebooks.ensure(project, cfg.runtime_dir, task_id)
    notebooks.migrate_records(
        project, cfg.runtime_dir, task_id, state)
    notebooks.move_to_failure(
        project, cfg.runtime_dir, task_id, artifact)
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
        _require_clean(project, "startem zadania")
        task = state.task_queue.pop(0)
        state.current_task = task
        state.task_start_tag = f"forge/{task['id']}-start"
        git(project, "tag", state.task_start_tag)
        _write_current_task(project, task)
        story = str(task.get("story", "")).strip()
        if story and _story_status(project, story) == "nowa":
            _set_story_status(project, story, "w toku")
        state.task_phase = "tester"
        state.tester_handoff = str(task.get("batch_handoff", "")).strip()
        state.no_change_rounds = 0
        state.round_changed = False
        state.suite_regression = False
        state.review_suggestions_pending = False
        notebooks.ensure(project, cfg.runtime_dir, task["id"])
        _checkpoint(project, state, "tester")
        log(f"Zadanie {task['id']} — {task['title']} (trudność: {task['difficulty']})")
        story_suffix = f" ({task['story']})" if task.get("story") else ""
        ledger.append(project, f"{task['id']} start: {task['title']} "
                       f"({task['difficulty']}){story_suffix}")
    else:
        # Wznowienie oraz migracja checkpointu sprzed Context Capsule.
        notebooks.ensure(project, cfg.runtime_dir, task["id"])
    had_legacy_sessions = bool(state.tester_session or state.coder_session)
    state.tester_session = ""
    state.coder_session = ""
    if (notebooks.migrate_records(
            project, cfg.runtime_dir, task["id"], state)
            or had_legacy_sessions):
        _checkpoint(project, state, state.task_phase)
    if state.task_phase in {"", "tester", "coder"}:
        # Mistrz jest wołany raz na rundę; ta sama notatka obsługuje kodera w
        # tej samej rundzie. Zmienna lokalna wystarcza — po restarcie kolejna
        # runda i tak zapyta go od nowa.
        notes: dict[str, str] = {}
        consulted = False

        def ensure_notes(new_round: bool, next_role: str) -> None:
            """Nowa runda pyta mistrza zawsze; wznowienie prosto w koderze
            pyta, bo inaczej rada dla kodera przepadłaby. Milczenie mistrza
            (częsty przypadek) nie może kosztować drugiego wywołania."""
            nonlocal consulted
            if new_round or not consulted:
                notes.clear()
                notes.update(_master_notes(
                    cfg, project, logf,
                    task_id=str(task.get("id", "")), next_role=next_role))
                consulted = True

        def notebook_for(role: str) -> str:
            text = notebooks.read(
                project, cfg.runtime_dir, task["id"], role)
            if len(text) > _LARGE_NOTEBOOK:
                log(f"  UWAGA: notatnik roli {role} ma {len(text)} znaków "
                    "i wchodzi w całości do każdej kolejnej tury")
            return text

        def record_notebook(role: str, data: dict) -> None:
            written = notebooks.append_entry(
                project, cfg.runtime_dir, task["id"], role,
                state.tdd_round + 1, data.get("notebook", ""))
            if len(written) > _LONG_NOTEBOOK_ENTRY:
                log(f"  UWAGA: nowy wpis notatnika roli {role} ma "
                    f"{len(written)} znaków zamiast jednej linii")

        def run_turn(role: str, prompt: str, parser):
            """Jedna tura roli: nota mistrza, decyzja, log i wpis do dziennika
            wraz z listą plików zmienionych w tej konkretnej turze."""
            prompt += prompts.no_change_rounds_suffix(state.no_change_rounds)
            prompt += prompts.master_note_suffix(notes.get(role, ""))
            before = _tree_manifest(project)
            result = _decision_with_retry(
                prompt,
                lambda value: _call_role(
                    cfg, project, state, role, value, logf(role)),
                parser,
                on_reject=lambda exc: _dump_invalid_decision(
                    project, cfg, state, exc))
            # Nazwy plików pozwalają Mistrzowi zauważyć np. zmianę testu przez
            # kodera i poprosić testera o ocenę bez mechanicznego blokowania.
            changed_paths = _turn_changes(before, _tree_manifest(project))
            if changed_paths:
                state.round_changed = True
            changed = _describe_turn_changes(changed_paths)
            reason = str(result.data.get("reason", ""))
            label = "tester" if role == "tester" else "koder"
            log(f"  [{task['id']}] runda {state.tdd_round + 1}: {label} → {result.status}"
                + (f" ({reason[:200]})" if reason else ""))
            ledger.append(project, f"{task['id']} r{state.tdd_round + 1} "
                                   f"{label}→{result.status} pliki={changed}: {reason[:160]}")
            return result

        def run_tester(handoff: str):
            ensure_notes(new_round=True, next_role="tester")
            state.round_changed = False
            confirmation = bool(
                state.coder_summary and handoff == state.coder_summary)
            suggested_test_cmd = str(
                state.tester_decision.get("command", "")).strip()
            using_suite_regression = state.suite_regression

            changed_files = _changed(project, state.task_start_tag)
            capsule = prompts.context_capsule(
                state, "tester",
                notebook_text=notebook_for("tester"),
                changed_files=changed_files,
                handoff=handoff,
                confirmation=confirmation,
                suite_regression=using_suite_regression,
                review_suggestions=state.review_suggestions_pending,
            )
            result = run_turn("tester", prompts.tester_task_prompt(
                task["file"], state.test_cmd,
                suggested_test_cmd=suggested_test_cmd,
                capsule=capsule,
                confirmation=confirmation,
                suite_regression=using_suite_regression,
                review_suggestions=state.review_suggestions_pending,
                review_notes=state.review_notes,
                verdict_cmd=verdict_contract.command(cfg.runtime_dir, "tester"))
                + _ledger_context(project, ledger.TASK_LINES),
                lambda text: parse_tester_decision(
                    text, project=project,
                    allow_finalize=state.review_suggestions_pending))
            record_notebook("tester", result.data)
            # To jednorazowy sygnał kierujący najbliższą turę testera na pełną
            # bramkę. Czyścimy go dopiero po poprawnie sparsowanej odpowiedzi:
            # checkpoint sprzed tury nadal umożliwia bezpieczne wznowienie.
            if using_suite_regression:
                state.suite_regression = False
            return result

        def run_coder(decision):
            ensure_notes(new_round=False, next_role="coder")
            # Nowe decyzje red/code zawsze mają command. Fallback obsługuje
            # wyłącznie wznowienie starego checkpointu fazy coder.
            test_cmd = str(decision.data.get("command", "")).strip() or state.test_cmd
            capsule = prompts.context_capsule(
                state, "coder",
                notebook_text=notebook_for("coder"),
                changed_files=_changed(project, state.task_start_tag),
                tester_gate=test_cmd,
            )
            result = run_turn("coder", prompts.coder_task_prompt(
                task["file"], test_cmd,
                decision=decision.data,
                capsule=capsule,
                verdict_cmd=verdict_contract.command(cfg.runtime_dir, "coder"))
                + _ledger_context(project, ledger.TASK_LINES),
                lambda text: parse_coder_decision(text, project=project))
            # Zapis notatki kanałem, którym decyzja i tak wraca. Tura
            # narzędziowa kosztowałaby tu dziesiątki tysięcy tokenów wejścia i
            # — jak pokazuje historia pustych notatników — bywa pomijana.
            record_notebook("coder", result.data)
            state.no_change_rounds = (
                0 if state.round_changed else state.no_change_rounds + 1)
            return result

        outcome = run_tdd_loop(
            state=state, max_rounds=cfg.max_tdd_rounds,
            run_tester=run_tester,
            run_coder=run_coder,
            checkpoint=lambda phase: _checkpoint(project, state, phase),
            worktree_fingerprint=lambda: _tree_fingerprint(project),
        )
        if outcome == "finalize":
            # Najpierw ustaw następną legalną fazę. Jeśli SIGINT nadejdzie
            # podczas logowania poniżej, handler zapisze już wznawialny commit,
            # a nie fazę testera pozbawioną informacji o sugestiach.
            state.task_phase = "commit"
            state.review_suggestions_pending = False
            state.review_notes = []
            _checkpoint(project, state, "commit")
            log(
                f"Zadanie {task['id']}: sugestie rozliczone, "
                "pomijam ponowne review."
            )
            ledger.append(
                project,
                f"{task['id']} sugestie→finalize: "
                f"{state.tester_decision.get('reason', '')[:160]}",
            )
        elif outcome != "review":
            _fail_task(cfg, project, state, outcome)
            return True
        else:
            # `review` w cyklu sugestii jest świadomą eskalacją. Kolejny
            # reviewer widzi cały finalny diff; sugestie przestają być
            # przepustką do commita bez recenzji.
            state.task_phase = "review"
            state.review_suggestions_pending = False
            state.review_notes = []
            _checkpoint(project, state, "review")
            log(
                f"Zadanie {task['id']}: pętla TDD zakończona, "
                "przekazuję do review."
            )
    if state.task_phase == "review":
        before_review = _tree_manifest(project)
        review_prompt = prompts.review_task_prompt_kiss(
            task["file"], start_tag=state.task_start_tag,
            changed=_changed(project, state.task_start_tag),
            verdict_cmd=verdict_contract.command(cfg.runtime_dir, "review"))
        log(f"Zadanie {task['id']}: recenzja (świeży kontekst)…")
        review = _decision_with_retry(
            review_prompt,
            lambda value: _verdict_turn(
                cfg, project, "review",
                lambda: run_role(
                    "reviewer", value, cfg, project, logf("review"),
                    difficulty=task["difficulty"])),
            lambda text: parse_review_decision(text, project=project),
            on_reject=lambda exc: _dump_invalid_decision(project, cfg, state, exc))
        log(f"Zadanie {task['id']}: recenzja → {review.status}")
        review_changes = _describe_turn_changes(
            _turn_changes(before_review, _tree_manifest(project)))
        ledger.append(project, f"{task['id']} recenzja→{review.status} "
                               f"pliki={review_changes}: "
                               f"{'; '.join(review.data.get('notes', []))[:160]}")
        review_notes = list(review.data.get("notes", []))
        nits = list(review.data.get("nits", []))
        if nits:
            _append_review_nits(cfg, project, task["id"], nits)
            ledger.append(project, f"{task['id']} review-nits: "
                                   f"{'; '.join(nits)[:160]}")
        if review.status == "request_changes":
            state.review_suggestions_pending = False
            state.review_notes = review_notes
            state.tester_handoff = (
                "Reviewer zażądał poprawek. Rozpocznij nowy cykl TDD "
                "i oceń aktywne uwagi review.")
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
                "Reviewer mimo roli read-only zmienił pliki "
                f"{review_changes}. Oceń pozostawiony diff; zachowaj, popraw albo "
                "przywróć te zmiany, a następnie wybierz dalszy krok.")
            state.review_suggestions_pending = False
            log(f"Zadanie {task['id']}: reviewer mimo roli read-only zmienił pliki "
                f"{review_changes} — wracam do testera po ocenę tego diffu.")
            ledger.append(project, f"{task['id']} review-zapis {review_changes}: "
                                   "powrót do testera po ocenę diffu recenzenta")
            _checkpoint(project, state, "tester")
            return True
        if review.status == "suggestions":
            state.review_notes = review_notes
            state.review_suggestions_pending = True
            state.tester_handoff = (
                "Reviewer zaakceptował bieżący diff z opcjonalnymi sugestiami. "
                "Oceń każdą aktywną uwagę review, zastosuj albo odrzuć z powodem."
            )
            _checkpoint(project, state, "tester")
            return True
        state.review_notes = []
        state.review_suggestions_pending = False
        _checkpoint(project, state, "commit")
    if state.task_phase == "corrections":
        # Zgodność ze starymi checkpointami. Osobna, jednorazowa tura kodera
        # została usunięta: każda uwaga review wraca teraz przez testera.
        state.tester_handoff = (
            "Wznowiono stary checkpoint poprawek po review. Rozpocznij nowy cykl "
            "TDD i oceń aktywne uwagi review.")
        state.corrections_done = False
        state.corrections_tree_hash = ""
        state.review_suggestions_pending = False
        _checkpoint(project, state, "tester")
        return True
    if state.task_phase != "commit":
        return True
    log(f"Zadanie {task['id']}: bramka przed commitem — pełny pakiet "
        f"`{state.test_cmd}`…")
    suite_green, suite_output = build_then_test_result(
        project, state.build_cmd, state.test_cmd, cfg.agent_timeout_s)
    if not suite_green:
        state.suite_regression = True
        # Cisza w tym miejscu wyglądała jak zwis albo pętla: zadanie wracało do
        # testera bez śladu w logu. Mistrz też widzi tylko dziennik, więc bez
        # wpisu dostawał niewyjaśnioną lukę między `finalize` i kolejną turą.
        log(f"Zadanie {task['id']}: bramka przed commitem CZERWONA — "
            "wracam do testera z pełnym pakietem zamiast commitować.")
        # Bez strzałki: `pliki=`/`rola→decyzja` to słownik wzorców mistrza i
        # nowy pseudo-label tylko by go rozmywał.
        ledger.append(project, f"{task['id']} bramka przed commitem CZERWONA, "
                               f"powrót do testera; ogon: {suite_output[-160:]}")
        state.tester_handoff = (
            "Deterministyczna bramka przed commitem wykazała, że pełny pakiet "
            "jest czerwony po tym zadaniu. Pracuj na komendzie pełnej bramki "
            "wskazanej niżej, oceń zachowany ogon wyniku, napraw albo zwróć "
            f"`blocked` z konkretnym powodem:\n{suite_output[-2000:]}"
        )
        _checkpoint(project, state, "tester")
        return True
    story = str(task.get("story", "")).strip()
    if story and not any(str(item.get("story", "")).strip() == story
                         for item in state.task_queue):
        _set_story_status(project, story, "do weryfikacji")
    commit_all(project, f"feat: {task['title']}", cfg)
    notebooks.remove(project, cfg.runtime_dir, task["id"])
    git(project, "tag", "-d", state.task_start_tag, check=False)
    log(f"Zadanie {task['id']} UKOŃCZONE i zacommitowane: {task['title']}")
    story_suffix = f" ({story})" if story else ""
    ledger.append(project, f"{task['id']} UKOŃCZONE{story_suffix} po "
                   f"{state.tdd_round} rundach")
    _clear_task_state(state)
    _checkpoint(project, state, "")
    return True

def _story_report_path(project: str) -> Path:
    return Path(project, ".forge", "verification", "stories-latest.md")


def _fresh_story_report(project: str, state: State) -> str:
    """Raport jest ważny tylko przy zgodnych polach stanu i nagłówka."""
    path = _story_report_path(project)
    if not path.is_file() or not state.stories_verified_sha:
        return ""
    text = path.read_text(encoding="utf-8")
    batch = re.search(r"verified_at_batch:\s*(\d+)", text)
    sha = re.search(r"verified_sha:\s*([^\s]+)", text)
    if (not batch or not sha
            or int(batch.group(1)) != state.stories_verified_at_batch
            or sha.group(1) != state.stories_verified_sha):
        return ""
    return text


def _parse_story_verification(text: str, *, project: str = "") -> dict:
    return _parse_json_object(text, project=project, require=_require_story_verification)


def _require_story_verification(data: dict) -> None:
    entries = data.get("stories")
    if not isinstance(entries, list):
        raise InvalidDecision("weryfikator historyjek musi zwrócić listę stories")
    normalized = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("status") not in {
                "potwierdzona", "niepotwierdzona", "częściowa"}:
            raise InvalidDecision("każdy wpis stories wymaga poprawnego statusu")
        normalized.append({
            "id": str(entry.get("id", "")).strip(),
            "status": entry["status"],
            "evidence": str(entry.get("evidence", "")).strip(),
        })
    data["stories"] = normalized
    data["notes"] = [str(item) for item in data.get("notes", [])]


def phase_verify_stories(cfg: Config, project: str, state: State, logf) -> bool:
    """Zweryfikuj historyjki oczekujące na dowód; pusty zbiór nic nie kosztuje."""
    candidates, _ = backlog.load(project)
    touched = stories_touched_since(project, state.stories_verified_sha)
    if not state.stories_verified_sha:
        # Pierwsza inwentaryzacja migracji nie może udawać, że nic nie
        # zrobiono tylko dlatego, że stare wpisy mają status `nowa`.
        selected = [story for story in candidates if story.status != "porzucona"][:
            cfg.max_backlog_stories]
    else:
        selected = [story for story in candidates
                    if story.status == "do weryfikacji" or story.id in touched]
    if not selected:
        return False

    before = _tree_manifest(project)
    snapshot = _snapshot_tree(project)
    sha_before = git(project, "rev-parse", "HEAD", check=False).stdout.strip()
    cycle_dir = str(Path(project, cfg.runtime_dir, "verification", "stories-cycle"))
    evidence = verify.collect_evidence(
        project, state, cfg, cycle_dir, sha=sha_before,
        targets=cfg.effective_verify_targets(state.verify_targets))
    stories_text = "\n".join(
        f"- {story.id}: {story.check} (dlaczego: {story.why_now})"
        for story in selected)
    evidence_text = "\n".join(
        f"- {name}: rc={item.get('rc')}, log={item.get('log', '')}"
        for name, item in evidence.items()) or "(brak targetów mechanicznych)"
    try:
        data = _decision_with_retry(
            prompts.verify_stories_prompt(stories=stories_text, evidence=evidence_text)
            + _ledger_context(project, ledger.PHASE_LINES),
            lambda value: run_role(
                "verifier", value, cfg, project, logf("verify-stories")),
            lambda text: _parse_story_verification(text, project=project),
        )
    finally:
        _restore_head(project, sha_before, "Weryfikator historyjek")
        restored = _restore_snapshot(project, snapshot, before)
        if restored:
            ledger.append(project, "verify-stories: cofnięto zmiany weryfikatora "
                           + _describe_turn_changes(restored))

    verdicts = {item["id"]: item for item in data["stories"]}
    for story in selected:
        result = verdicts.get(story.id)
        if not result:
            continue
        target_status = "zrobiona" if result["status"] == "potwierdzona" else "w toku"
        _set_story_status(project, story.id, target_status)
        ledger.append(project, f"verify-story {story.id}: {result['status']} "
                       f"{result['evidence'][:160]}")

    commit_all(project, "docs: weryfikacja historyjek", cfg)
    verified_sha = git(project, "rev-parse", "HEAD", check=False).stdout.strip()
    report_path = _story_report_path(project)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        f"<!-- verified_at_batch: {state.plan_batches} -->\n"
        f"<!-- verified_sha: {verified_sha} -->\n\n"
        + str(data.get("verdict", "changes")) + "\n\n"
        + "\n".join(
            f"- {item['id']}: {item['status']} — {item['evidence']}"
            for item in data["stories"]),
        encoding="utf-8")
    state.stories_verified_at_batch = state.plan_batches
    state.stories_verified_sha = verified_sha
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
        # Dowód mówi, że cel NIE jest osiągnięty; utrzymana flaga odsyłałaby
        # każdy pusty wsad prosto tutaj, zamiast do przeglądu kierunku.
        state.goal_confirmed = False
        log("Weryfikacja celu: czerwony dowód — wracam do planowania z feedbackiem.")
        return True
    verify_prompt = prompts.verify_goal_prompt(
        state.verify_cycle, evidence, cycle_dir,
        story_report=_fresh_story_report(project, state))
    verify_prompt += _ledger_context(project, ledger.PHASE_LINES)
    try:
        data = _decision_with_retry(
            verify_prompt,
            lambda value: run_role(
                "verifier", value, cfg, project, logf("verify"),
                mcp_config=cfg.verifier_mcp_config),
            lambda text: _parse_json_object(text, project=project))
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
    state.goal_confirmed = False
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
    # Granica między zadaniami jest jedynym bezpiecznym momentem na zmianę
    # kierunku: aktywne zadanie ma dokończyć swój cykl na założeniach, z którymi
    # ruszyło. Weryfikacja starego celu po zmianie kierunku byłaby stratą, więc
    # przegląd wyprzedza także ją.
    trigger = "" if state.current_task else _po_trigger(cfg, project, state)
    if trigger:
        _require_clean(project, "przeglądem kierunku")
        report = ""
        if trigger == "cadence":
            phase_verify_stories(cfg, project, state, logf)
        elif trigger == "brief":
            if (state.stories_verified_sha and
                    state.plan_batches - state.stories_verified_at_batch
                    < cfg.steering_batches):
                report = _fresh_story_report(project, state)
        phase_product_owner(cfg, project, state, logf, trigger,
                            story_report=report)
        return True
    if state.task_phase == "verify_goal":
        phase_verify_stories(cfg, project, state, logf)
        return phase_verify_goal(cfg, project, state, logf)
    if not state.current_task and not state.task_queue:
        _require_clean(project, "planowaniem")
        planned = phase_plan_batch(cfg, project, state, logf)
        if planned["no_more_tasks"]:
            # Pusty backlog nie kończy projektu: zakres rozwija przegląd
            # kierunku, więc dopiero jego werdykt `goal_reached` przepuszcza do
            # końcowej weryfikacji. Bezpiecznik na dwa jałowe wsady z rzędu
            # chroni przed pętlą planista↔przegląd na najsilniejszym modelu.
            if state.goal_confirmed or state.empty_plans >= 2:
                return phase_verify_goal(cfg, project, state, logf)
            log("Planowanie: backlog wyczerpany — proszę o przegląd kierunku.")
            state.steering_due = True
            return True
    state.iteration += 1
    return run_task(cfg, project, state, logf)


_TRANSCRIPT_KEEP_ITERATIONS = 20


def _transcript_log_path(project: str, iteration: int, phase: str) -> Path:
    """Ścieżka surowej telemetrii poza drzewem projektu."""
    log_dir = _transcript_log_dir(project)
    _prune_transcript_logs(log_dir, iteration)
    return log_dir / f"iter-{iteration:04d}-{phase}.log"


def _transcript_log_dir(project: str) -> Path:
    return snapshot.cache_root() / snapshot.project_key(project) / "logs"


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
_DOC_INDEX_SIZE_LIMIT = 2_000

# Pliki instrukcji czytane samoczynnie przez agentów CLI (codex → AGENTS.md,
# claude → CLAUDE.md). Zasiewamy je deterministycznie, bo projekty
# zbootstrapowane przed tą zasadą ich nie mają, a bez tej notki tester i koder
# greppują runtime orkiestratora w poszukiwaniu kontekstu, który i tak dostają
# w promptcie — jedna taka tura potrafi wciągnąć megabajt cudzych transkryptów.
_AGENT_NOTE_FILES = ("AGENTS.md", "CLAUDE.md")


def _agent_instruction_note(runtime_dir: str) -> str:
    return f"""# Notatka dla agentów

`{runtime_dir}/` to runtime orkiestratora Forge. Nie przeglądaj go w
poszukiwaniu ogólnego kontekstu: plik zadania i kapsułę dostajesz w promptcie.
Twój prywatny notatnik też jest w kapsule — nie czytaj go z dysku i nie
zapisuj sam; wpisy oddajesz polem `notebook` swojej decyzji, a plikiem
zarządza Forge.

Wyjątkiem jest `{runtime_dir}/verdict.py`: tym skryptem zatwierdzasz werdykt
swojej tury i to jedyny plik runtime, który masz uruchamiać. Sprawdza kontrakt
od razu, więc błąd poprawisz w tej samej turze zamiast tracić całą pracę.

Zwłaszcza `{runtime_dir}/tasks/archive/` zawiera zamknięte zadania; czytanie
tego archiwum zapycha kontekst i nic nie wnosi. To wyjaśnienie, nie zakaz.
"""


def _superseded_agent_notes(runtime_dir: str) -> tuple[str, ...]:
    """Każda poprzednia treść Forge, wyłącznie do migracji bajt-w-bajt.

    Notka zostawiona w wersji sprzed pola `notebook` licencjonowałaby czytanie
    i zapisywanie notatnika z dysku — czyli dokładnie tę turę narzędziową,
    której pozbywa się kapsuła.
    """
    return (
        _oldest_agent_instruction_note(runtime_dir),
        f"""# Notatka dla agentów

`{runtime_dir}/` to runtime orkiestratora Forge. Nie przeglądaj go w
poszukiwaniu ogólnego kontekstu: plik zadania i kapsułę dostajesz w promptcie.
Wyjątkiem jest dokładnie jeden prywatny notatnik roli wskazany w kapsule —
możesz go czytać i aktualizować. Nie czytaj notatników innych ról.

Zwłaszcza `{runtime_dir}/tasks/archive/` zawiera zamknięte zadania; czytanie
tego archiwum zapycha kontekst i nic nie wnosi. To wyjaśnienie, nie zakaz.
""",
        # Wersja sprzed skryptu werdyktu: zostawiona bez migracji kazałaby
        # roli omijać jedyny plik runtime, który ma uruchamiać.
        f"""# Notatka dla agentów

`{runtime_dir}/` to runtime orkiestratora Forge. Nie przeglądaj go w
poszukiwaniu ogólnego kontekstu: plik zadania i kapsułę dostajesz w promptcie.
Twój prywatny notatnik też jest w kapsule — nie czytaj go z dysku i nie
zapisuj sam; wpisy oddajesz polem `notebook` swojej decyzji, a plikiem
zarządza Forge.

Zwłaszcza `{runtime_dir}/tasks/archive/` zawiera zamknięte zadania; czytanie
tego archiwum zapycha kontekst i nic nie wnosi. To wyjaśnienie, nie zakaz.
""",
    )


def _oldest_agent_instruction_note(runtime_dir: str) -> str:
    """Dokładna treść Forge sprzed notatników, wyłącznie do migracji."""
    return f"""# Notatka dla agentów

`{runtime_dir}/` to runtime orkiestratora Forge. Plik twojego zadania i cały
potrzebny kontekst dostajesz w promptcie, więc nie ma tam nic, czego
potrzebujesz. Dotyczy to zwłaszcza `{runtime_dir}/tasks/archive/` (zamknięte
zadania) — czytanie tego zapycha kontekst i nic nie wnosi.

To wyjaśnienie, nie zakaz.
"""


def _housekeeping(cfg: Config, project: str) -> None:
    """Deterministyczne sprzątanie przed planowaniem, bez udziału agenta."""
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

    # Archiwum zadań rośnie w nieskończoność, a agenci listują `.forge`.
    # Kolejność NUMERYCZNA, nie leksykograficzna: przy przejściu task-999 →
    # task-1000 sortowanie po nazwie skasowałoby najnowsze zadanie, a
    # `_next_task_index` liczy `max(ids)` — numery zaczęłyby się powtarzać.
    def _archive_order(path: Path) -> tuple[int, str]:
        match = re.match(r"task-(\d+)", path.stem)
        return (int(match.group(1)) if match else 0, path.name)

    archived = sorted(archive.glob("task-*.md"), key=_archive_order)
    for stale in archived[:-_RUNTIME_KEEP_ITEMS]:
        stale.unlink(missing_ok=True)

    # Transkrypty żyją poza drzewem projektu; katalog sprzed tej migracji
    # zostawał w repo i był realnym magnesem na megabajtowe grepy agentów.
    shutil.rmtree(runtime / "logs", ignore_errors=True)

    for name in _AGENT_NOTE_FILES:
        note = Path(project, name)
        if not note.exists():
            note.write_text(
                _agent_instruction_note(cfg.runtime_dir), encoding="utf-8")
        elif note.read_text(encoding="utf-8") in _superseded_agent_notes(
                cfg.runtime_dir):
            # Migrujemy tylko bajt-w-bajt własne starsze notki. Każda inna
            # treść należy do użytkownika i pozostaje nietknięta.
            note.write_text(
                _agent_instruction_note(cfg.runtime_dir), encoding="utf-8")

    failed = runtime / "failed"
    artifacts = sorted(
        (path for path in failed.iterdir() if path.is_dir()),
        key=lambda path: path.name,
    ) if failed.is_dir() else []
    for artifact in artifacts[:-_RUNTIME_KEEP_ITEMS]:
        shutil.rmtree(artifact, ignore_errors=True)

    state_path = runtime / "STATE.json"
    active_task_id = ""
    checkpoint_readable = True
    if state_path.exists():
        try:
            active_task_id = str(
                State.load(str(state_path)).current_task.get("id", ""))
        except (OSError, ValueError):
            # Uszkodzony checkpoint nie daje prawa usuwać potencjalnie
            # potrzebnej pamięci aktywnego zadania.
            checkpoint_readable = False
    if checkpoint_readable:
        notebooks.prune_orphans(project, cfg.runtime_dir, active_task_id)

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
        # Snapshot briefu jest wierną kopią cudzego dokumentu i bazą diffu —
        # jego rozmiar nie jest długiem dokumentacji do podziału.
        if path.relative_to(project).as_posix() == brief.SNAPSHOT_PATH:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        is_index = path.name == "00-INDEX.md"
        limit = _DOC_INDEX_SIZE_LIMIT if is_index else _DOC_SIZE_LIMIT
        if size > limit:
            oversized.append((
                path.relative_to(project).as_posix(), size, is_index))
    if not oversized:
        return
    backlog = Path(project, "BACKLOG.md")
    existing = backlog.read_text(encoding="utf-8") if backlog.exists() else ""
    additions = []
    for name, size, is_index in oversized:
        # Deduplikacja po PEŁNYM markerze wpisu, nie po samej ścieżce: backlog
        # wspomina pliki dokumentacji w zwykłej prozie („Szczegóły w `docs/…`"),
        # więc luźniejszy warunek uznawał dług za już zgłoszony i bramka nigdy
        # nie dopisała ani jednej linii.
        marker = (f"Dług dokumentacji: indeks `{name}`" if is_index
                  else f"Dług dokumentacji: `{name}`")
        if marker in existing:
            continue
        if is_index:
            additions.append(
                f"- {marker} ma {size // 1000} KB "
                "i przekroczył limit 2 KB — skróć mapę obszarów.\n")
        else:
            additions.append(
                f"- {marker} ma {size // 1000} KB "
                "— zaplanuj podział pliku.\n")
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


SNAPSHOT_ENV = "FORGE_CODE_SNAPSHOT"
_SNAPSHOT_REEXEC = "FORGE_CODE_SNAPSHOT_REEXEC"


def _reexec_from_snapshot() -> None:
    """Przenieś bieg na własną kopię kodu i wystartuj go od nowa.

    Bez tego gwarancja z ``docs/AWARIE-2026-08-11.md`` kończyłaby się na GUI,
    a awaria B dotyczyła procesu uruchomionego z linii poleceń: kod ``.py``
    wczytuje się raz, przy starcie, a szablony promptów przy każdym renderze,
    więc commit pod pracującą pętlą rozjeżdża jedno z drugim.

    ``PYTHONSAFEPATH`` jest tu warunkiem poprawności, nie ozdobą: przy ``-m``
    Python wkłada bieżący katalog na POCZĄTEK ``sys.path``, więc uruchomienie
    z repozytorium wczytałoby pakiet stamtąd mimo ``PYTHONPATH`` — i zapętliło
    przenosiny. Znacznik w środowisku jest drugim bezpiecznikiem tej samej
    rzeczy. ``FORGE_CODE_SNAPSHOT=0`` wyłącza mechanizm (praca z debuggerem,
    świadome łatanie kodu w trakcie biegu)."""
    if (os.environ.get(SNAPSHOT_ENV) or "").strip().lower() in {
            "0", "off", "none", "false", "nie"}:
        return
    if os.environ.get(_SNAPSHOT_REEXEC) == "1":
        return
    package = Path(__file__).resolve().parent
    if snapshot.is_snapshot(package.parent):
        return
    try:
        code = snapshot.create(package)
    except OSError as exc:
        log(f"UWAGA: nie udało się zrobić migawki kodu ({exc}); "
            "bieg pracuje na drzewie roboczym.")
        return
    env = os.environ.copy()
    env[_SNAPSHOT_REEXEC] = "1"
    env["PYTHONSAFEPATH"] = "1"
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (f"{code.path}{os.pathsep}{existing}" if existing
                         else str(code.path))
    log(f"Bieg startuje z migawki kodu: {code.describe()} "
        f"({SNAPSHOT_ENV}=0 wyłącza).")
    try:
        os.execve(sys.executable,
                  [sys.executable, "-u", "-m", "forge.orchestrate",
                   *sys.argv[1:]], env)
    except OSError as exc:
        log(f"UWAGA: nie udało się przejść na migawkę ({exc}); "
            "bieg pracuje na drzewie roboczym.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Forge KISS: tester → coder → review; max_tdd_rounds chroni małe zadania.")
    parser.add_argument("--brief", default="game.md"); parser.add_argument("--project", default="game")
    parser.add_argument("--non-interactive", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--discard-legacy-task", action="store_true", help="Porzuć aktywny stary task i zachowaj stabilny stan.")
    parser.add_argument("--max-iters", type=int, default=0); parser.add_argument("--batch-size", type=int); parser.add_argument("--max-tdd-rounds", type=int)
    parser.add_argument("--tester-agent"); parser.add_argument("--coder-agent"); parser.add_argument("--reviewer-agent"); parser.add_argument("--planner-agent")
    args = parser.parse_args(argv)
    # Tylko dla PRAWDZIWEGO uruchomienia z powłoki: wywołanie programowe
    # (testy, osadzenie) dostało argumenty w ``argv`` i nie ma czego ponawiać.
    if argv is None:
        _reexec_from_snapshot()
    try:
        cfg = Config(brief_path=args.brief)
    except ValueError as exc:
        parser.error(str(exc))
    for key in ("batch_size", "max_tdd_rounds", "tester_agent", "coder_agent", "reviewer_agent", "planner_agent"):
        value = getattr(args, key)
        if value is not None: setattr(cfg, key, value)
    with contextlib.ExitStack() as stack:
        # Dzierżawa migawki: dopóki bieg trwa, sprzątacz nie może wyjąć spod
        # niego kodu, choćby pętla pracowała dłużej niż okres przechowywania.
        lease = snapshot.hold()
        if lease is not None:
            stack.enter_context(lease)
        # Zamek projektu bierzemy PRZED czymkolwiek, co dotyka drzewa — łącznie
        # z porzuceniem starego zadania, bo ono również przepisuje STATE.json.
        try:
            stack.enter_context(runlock.acquire(args.project, cfg.runtime_dir))
            # Sesja Claude Code w trybie plikowym jest zasobem GLOBALNYM, więc
            # jej wyłączność rozstrzyga się tutaj, a nie w warstwie
            # uruchamiającej: inaczej drugie okno GUI albo zwykłe wywołanie
            # z powłoki nadal unieważniałoby sesję wszystkim.
            session = preflight.claude_file_session_lock(cfg)
        except runlock.RunLocked as exc:
            print(str(exc), file=sys.stderr, flush=True)
            return 4
        except OSError as exc:
            # Niezapisywalny cache nie jest powodem do wywrócenia biegu
            # tracebackiem: zamek jest ochroną, a nie warunkiem poprawności.
            log(f"UWAGA: nie udało się wziąć zamku sesji Claude Code ({exc}).")
            session = None
        if session is not None:
            stack.enter_context(session)
        return _run(args, cfg, parser)


def _run(args: argparse.Namespace, cfg: Config,
         parser: argparse.ArgumentParser) -> int:
    """Bieg na projekcie, którego zamek trzyma już wołający."""
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
    # Preflight jest operacją procesu, nie iteracji: musi zobaczyć zastane
    # zmiany zanim pierwsza faza zacznie wymagać czystego drzewa.
    ensure_repo(args.project)
    preflight.run(args.project, cfg, state)
    state.save(str(path))
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
        if isinstance(exc, InvalidDecision):
            _dump_invalid_decision(args.project, cfg, state, exc)
        print(f"Forge zatrzymany bezpiecznie: {exc}. Checkpoint zapisano w {path}.",
              file=__import__("sys").stderr, flush=True)
        return 3 if isinstance(exc, LimitExhausted) else 1
    except KeyboardInterrupt:
        # GUI obiecuje „stan zostanie zapisany" i wysyła SIGINT — bez tej
        # gałęzi obietnica kończyła się tracebackiem zamiast checkpointu.
        state.save(str(path))
        print(f"Forge przerwany. Checkpoint zapisano w {path}.",
              file=__import__("sys").stderr, flush=True)
        return 130
    log("Forge: pętla zakończona (brak dalszej pracy lub limit iteracji).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
