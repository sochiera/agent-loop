from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from forge import verdict
from forge.config import Config
from forge.task_pipeline import InvalidDecision, parse_tester_decision


def _run(tmp_path: Path, role: str, payload: str, *, stdin_is_file: bool = False):
    """Uruchom skrypt tak, jak zrobi to rola: przez kopię w runtime projektu."""
    script = verdict.install(str(tmp_path), ".forge")
    argv = [role]
    if stdin_is_file:
        source = tmp_path / "payload.json"
        source.write_text(payload, encoding="utf-8")
        argv.append(str(source))
    out, err = io.StringIO(), io.StringIO()
    # Skrypt wyznacza ścieżki względem SIEBIE, więc test musi go zaimportować
    # spod kopii — inaczej sprawdzałby runtime repozytorium Forge, nie projektu.
    module = _load_copy(script)
    code = module.main(argv, stdin=io.StringIO(payload), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def _load_copy(script: Path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("forge_verdict_copy", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_valid_verdict_is_accepted_and_persisted(tmp_path: Path) -> None:
    verdict.prepare(str(tmp_path), ".forge", "tester")
    code, out, err = _run(
        tmp_path, "tester",
        '{"status":"red","command":"  pytest tests/test_x.py  ","reason":"x"}')
    assert code == 0, err
    assert "OK" in out and "status='red'" in out
    stored = verdict.read(str(tmp_path), ".forge", "tester")
    assert stored["command"] == "pytest tests/test_x.py"


def test_verdict_may_be_passed_as_a_file(tmp_path: Path) -> None:
    verdict.prepare(str(tmp_path), ".forge", "review")
    code, _out, err = _run(
        tmp_path, "review",
        '{"verdict":"suggestions","notes":["uprość nazwę"]}',
        stdin_is_file=True)
    assert code == 0, err
    assert verdict.read(str(tmp_path), ".forge", "review")["notes"] == ["uprość nazwę"]


def test_rejected_verdict_explains_the_contract_and_leaves_no_file(
        tmp_path: Path) -> None:
    verdict.prepare(str(tmp_path), ".forge", "tester")
    code, _out, err = _run(tmp_path, "tester", '{"status":"reviev"}')
    assert code == 1
    assert "'reviev'" in err and "red|code|review" in err
    # Kluczowe: rola dostaje kształt, w którym ma poprawić, bez zgadywania.
    assert "notebook" in err
    assert verdict.read(str(tmp_path), ".forge", "tester") is None


def test_broken_json_reports_position_and_context(tmp_path: Path) -> None:
    verdict.prepare(str(tmp_path), ".forge", "coder")
    code, _out, err = _run(
        tmp_path, "coder", '{"status":"green","summary":"zrobione",}')
    assert code == 1
    assert "poprawny JSON" in err and "kolumna" in err
    assert verdict.read(str(tmp_path), ".forge", "coder") is None


def test_empty_input_is_rejected_with_usage(tmp_path: Path) -> None:
    verdict.prepare(str(tmp_path), ".forge", "tester")
    code, _out, err = _run(tmp_path, "tester", "   \n")
    assert code == 1
    assert "pusty werdykt" in err and "heredoc" in err


def test_turn_contract_narrows_the_allowed_statuses(tmp_path: Path) -> None:
    """Skrypt musi mówić dokładnie to, co powie parser tury — inaczej jego
    „OK" byłoby obietnicą bez pokrycia i tura ginęłaby mimo walidacji."""
    verdict.prepare(str(tmp_path), ".forge", "tester",
                    statuses=[s for s in verdict.TESTER_STATUSES if s != "finalize"])
    code, _out, err = _run(
        tmp_path, "tester", '{"status":"finalize","reason":"gotowe"}')
    assert code == 1
    assert "finalize" in err

    verdict.prepare(str(tmp_path), ".forge", "tester",
                    statuses=verdict.TESTER_STATUSES)
    code, _out, err = _run(
        tmp_path, "tester", '{"status":"finalize","reason":"gotowe"}')
    assert code == 0, err


def test_prepare_clears_a_verdict_left_by_the_previous_turn(tmp_path: Path) -> None:
    verdict.prepare(str(tmp_path), ".forge", "tester")
    _run(tmp_path, "tester", '{"status":"review"}')
    assert verdict.read(str(tmp_path), ".forge", "tester") is not None
    verdict.prepare(str(tmp_path), ".forge", "tester")
    assert verdict.read(str(tmp_path), ".forge", "tester") is None


def test_installed_copy_runs_without_the_forge_package(tmp_path: Path) -> None:
    """Kopia trafia do cudzego repozytorium: bez PYTHONPATH i bez instalacji
    Forge. Import czegokolwiek z pakietu zabiłby ją u agenta, nie tutaj."""
    script = verdict.install(str(tmp_path), ".forge")
    assert "from forge" not in script.read_text(encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, ".forge/verdict.py", "tester"],
        cwd=tmp_path, input='{"status":"review"}', text=True,
        capture_output=True, env={"PATH": "/usr/bin:/bin"})
    assert proc.returncode == 0, proc.stderr
    assert json.loads(
        (tmp_path / ".forge" / "verdict" / "tester.json").read_text(
            encoding="utf-8"))["status"] == "review"


def test_install_is_idempotent_and_tracks_the_source(tmp_path: Path) -> None:
    first = verdict.install(str(tmp_path), ".forge")
    stamp = first.stat().st_mtime_ns
    verdict.install(str(tmp_path), ".forge")
    assert first.stat().st_mtime_ns == stamp
    assert first.read_bytes() == Path(verdict.__file__).read_bytes()


def test_unknown_role_is_refused(tmp_path: Path) -> None:
    code, _out, err = _run(tmp_path, "planner", '{"status":"review"}')
    assert code == 1
    assert "nieznana rola" in err


# --- Wybór kandydata świadomy kontraktu ------------------------------------

def test_verdict_survives_an_object_appended_after_it() -> None:
    """Regresja z 2026-08-10: tester dokleił po werdykcie drugi blok ```json```
    z samą poprawką notatnika. Wybór „ostatni blok wygrywa" skasował poprawną,
    40-minutową turę i wymusił jej powtórzenie aż do timeoutu."""
    text = (
        'Bramka zielona.\n\n```json\n'
        '{"status":"review","command":"pytest tests/test_x.py","reason":"ok"}\n'
        '```\n\nUps, poprawiam notatnik:\n\n```json\n'
        '{"notebook":"poprawiona notatka"}\n```')
    result = parse_tester_decision(text, allow_finalize=False)
    assert result.status == "review"
    assert result.data["command"] == "pytest tests/test_x.py"


def test_candidate_choice_respects_the_review_cycle() -> None:
    text = ('```json\n{"status":"review"}\n```\n'
            '```json\n{"status":"finalize","reason":"gotowe"}\n```')
    # Poza cyklem sugestii `finalize` jest obejściem review, więc nie może
    # wygrać wyboru mimo tego, że stoi na końcu odpowiedzi.
    assert parse_tester_decision(text, allow_finalize=False).status == "review"
    assert parse_tester_decision(text, allow_finalize=True).status == "finalize"


def test_all_candidates_invalid_reports_every_reason() -> None:
    text = ('```json\n{"status":"red"}\n```\n```json\n{"notebook":"x"}\n```')
    with pytest.raises(InvalidDecision) as exc:
        parse_tester_decision(text)
    assert "niepustego `command`" in str(exc.value)
    assert "None" in str(exc.value)


# --- Integracja z turą roli -------------------------------------------------

def test_committed_verdict_wins_over_the_turn_text(tmp_path: Path) -> None:
    from forge import orchestrate

    cfg = Config()

    def call() -> str:
        _run(tmp_path, "tester", '{"status":"review","reason":"zatwierdzone"}')
        return "…długa proza bez werdyktu…"

    output = orchestrate._verdict_turn(cfg, str(tmp_path), "tester", call)
    assert parse_tester_decision(output).data["reason"] == "zatwierdzone"


def test_turn_without_a_committed_verdict_falls_back_to_text(tmp_path: Path) -> None:
    from forge import orchestrate

    cfg = Config()
    output = orchestrate._verdict_turn(
        cfg, str(tmp_path), "tester", lambda: '{"status":"review"}')
    assert parse_tester_decision(output).status == "review"


def test_role_that_only_runs_the_script_still_drives_the_loop(tmp_path: Path) -> None:
    """Kontrakt całej ścieżki: rola zatwierdza werdykt skryptem i nie wypisuje
    żadnego JSON-a w odpowiedzi, a pętla TDD i tak dowozi zadanie do commita."""
    from unittest.mock import patch

    from forge import orchestrate
    from tests.test_task_flow import _git, _task_repo

    _task, state, cfg = _task_repo(tmp_path)
    tester_verdicts = iter((
        '{"status":"red","command":"python3 -m pytest -q tests/test_app.py"}',
        '{"status":"review"}',
    ))

    def commit_verdict(project: str, role: str, payload: str) -> str:
        proc = subprocess.run(
            [sys.executable, ".forge/verdict.py", role],
            cwd=project, input=payload, text=True, capture_output=True,
            env={"PATH": "/usr/bin:/bin"})
        assert proc.returncode == 0, proc.stderr
        return "Werdykt zatwierdzony skryptem; poniżej same notatki robocze."

    def session(role, _prompt, _cfg, project, _log, **_kwargs):
        if role == "tester":
            payload = next(tester_verdicts)
            if '"red"' in payload:
                path = Path(project, "tests", "test_app.py")
                path.write_text(
                    path.read_text(encoding="utf-8")
                    + "\ndef test_new_value():\n    assert VALUE == 1\n",
                    encoding="utf-8")
            return commit_verdict(project, "tester", payload), None
        Path(project, "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        return commit_verdict(
            project, "coder",
            '{"status":"green","summary":"ustawiono VALUE=1"}'), None

    with patch("forge.orchestrate.run_role_session", side_effect=session), \
         patch("forge.orchestrate._master_notes", return_value={}), \
         patch("forge.agents.run_agent", return_value='{"verdict":"approve"}'):
        assert orchestrate.run_task(cfg, str(tmp_path), state, lambda phase: phase)

    assert _git(tmp_path, "log", "-1", "--pretty=%s").stdout.strip() == (
        "feat: Zmiana wartości")
    # Skrypt naprawdę był jedynym kanałem werdyktu w tym biegu.
    assert next(tester_verdicts, None) is None
    assert (tmp_path / ".forge" / "verdict" / "tester.json").exists()


def test_roles_outside_the_tdd_loop_share_the_contract_aware_choice() -> None:
    """Ten sam błąd zabijał tury planisty, PO i weryfikatora — one też mają
    kontrakt, więc też mogą go użyć do wskazania werdyktu."""
    from forge import orchestrate

    text = ('```json\n{"summary":"kierunek bez zmian","replan":false,'
            '"changes":[]}\n```\nJeszcze notatka:\n```json\n{"notebook":"x"}\n```')
    data = orchestrate._parse_steering_decision(text)
    assert data["summary"] == "kierunek bez zmian"
    assert data["replan"] is False


def test_command_survives_a_runtime_dir_with_a_space(tmp_path: Path) -> None:
    """`runtime_dir` to dowolny string konfiguracji, a komenda idzie wprost do
    powłoki agenta — bez cytowania spacja rozbija ją na dwa argumenty."""
    import shlex

    runtime = "forge runtime"
    verdict.install(str(tmp_path), runtime)
    argv = shlex.split(verdict.command(runtime, "tester"))
    assert argv == ["python3", f"{runtime}/verdict.py", "tester"]
    proc = subprocess.run(
        [sys.executable, *argv[1:]], cwd=tmp_path, input='{"status":"review"}',
        text=True, capture_output=True, env={"PATH": "/usr/bin:/bin"})
    assert proc.returncode == 0, proc.stderr
    assert verdict.read(str(tmp_path), runtime, "tester")["status"] == "review"


def test_default_runtime_dir_keeps_the_command_unquoted() -> None:
    """Cytowanie nie może zmienić promptu domyślnej konfiguracji — inaczej
    każdy projekt płaciłby za tę poprawkę utratą trafień cache."""
    assert verdict.command(".forge", "tester") == "python3 .forge/verdict.py tester"
