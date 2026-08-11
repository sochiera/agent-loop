import os
import subprocess
import sys
from pathlib import Path

from forge import runlock, snapshot

REPO = Path(__file__).parents[1]


def test_module_entrypoint_rejects_unknown_option() -> None:
    result = subprocess.run([sys.executable, "-m", "forge.orchestrate", "--unknown"], text=True, capture_output=True)
    assert result.returncode == 2
    assert "usage:" in result.stderr


def _run_on_a_locked_project(project: Path, environ: dict[str, str]
                             ) -> subprocess.CompletedProcess:
    """Uruchom orkiestrator na zajętym projekcie — zatrzyma się na zamku.

    Ta ścieżka nie woła żadnej roli, więc nadaje się do sprawdzania tego, co
    dzieje się PRZED pętlą: przeniesienia biegu na migawkę kodu."""
    brief = project.parent / "brief.md"
    brief.write_text("cel\n", encoding="utf-8")
    with runlock.acquire(str(project)):
        return subprocess.run(
            [sys.executable, "-m", "forge.orchestrate", "--non-interactive",
             "--brief", str(brief), "--project", str(project)],
            text=True, capture_output=True, cwd=str(REPO), env=environ,
            timeout=180)


def test_a_command_line_run_moves_itself_onto_a_code_snapshot(
        tmp_path: Path) -> None:
    """Gwarancja z AWARIE-2026-08-11 nie może kończyć się na GUI."""
    project = tmp_path / "projekt"
    project.mkdir()
    cache = tmp_path / "cache"
    environ = dict(os.environ) | {"XDG_CACHE_HOME": str(cache)}

    result = _run_on_a_locked_project(project, environ)

    assert result.returncode == 4
    assert "migawki kodu" in result.stdout
    assert "nie udało się" not in result.stdout
    copies = list((cache / "forge" / "code").glob("forge-*"))
    assert copies, "bieg z powłoki nie zrobił własnej kopii kodu"
    assert (copies[0] / "forge" / "orchestrate.py").is_file()
    assert (copies[0] / "forge" / "prompts" / "templates").is_dir()


def test_the_snapshot_beats_the_working_tree_on_the_import_path(
        tmp_path: Path) -> None:
    """Sedno przenosin: przy ``-m`` Python stawia CWD na początku ``sys.path``.

    Bez ``PYTHONSAFEPATH`` bieg uruchomiony z repozytorium wczytałby pakiet
    stamtąd mimo ``PYTHONPATH`` — i przenosiny zapętliłyby się w kółko."""
    code = snapshot.create(environ={"XDG_CACHE_HOME": str(tmp_path)})
    environ = dict(os.environ) | {"PYTHONSAFEPATH": "1",
                                  "PYTHONPATH": str(code.path)}

    result = subprocess.run(
        [sys.executable, "-c",
         "import forge.orchestrate as module; print(module.__file__)"],
        text=True, capture_output=True, cwd=str(REPO), env=environ, timeout=60)

    assert result.stdout.strip() == str(code.path / "forge" / "orchestrate.py")


def test_the_snapshot_can_be_switched_off(tmp_path: Path) -> None:
    """Świadome łatanie kodu pod pętlą (debugger) musi zostać możliwe."""
    project = tmp_path / "projekt"
    project.mkdir()
    cache = tmp_path / "cache"
    environ = dict(os.environ) | {"XDG_CACHE_HOME": str(cache),
                                  "FORGE_CODE_SNAPSHOT": "0"}

    result = _run_on_a_locked_project(project, environ)

    assert result.returncode == 4
    assert "migawki kodu" not in result.stdout
    assert not (cache / "forge" / "code").exists()
