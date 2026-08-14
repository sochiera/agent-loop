import os
import subprocess
import sys
from pathlib import Path

import pytest

from forge import orchestrate, profiles, routing, runlock, snapshot
from forge.config import TASK_DIFFICULTIES
from forge.orchestrate import main

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


def _profile(tmp_path: Path, name: str, model: str) -> str:
    """Załóż nazwany profil w prywatnej konfiguracji i zwróć jego slug."""
    environ = {"XDG_CONFIG_HOME": str(tmp_path)}
    store = profiles.Store.load(profiles.shared_path(environ),
                                profiles.directory(environ), TASK_DIFFICULTIES)
    profile = store.create(name)
    store.set_routing(profile.slug, routing.parse(
        {"roles": {"coder": {"slots": {
            "standard": {"agent": "opencode", "model": model}}}}},
        TASK_DIFFICULTIES))
    return profile.slug


def _config_of_a_run(argv: list[str], tmp_path: Path,
                     monkeypatch: pytest.MonkeyPatch):
    """Konfiguracja, z którą ruszyłby bieg — bez wołania choćby jednej roli."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("FORGE_ROUTING_FILE", raising=False)
    project = tmp_path / "projekt"
    project.mkdir(exist_ok=True)
    seen: list = []
    monkeypatch.setattr(orchestrate, "_run",
                        lambda _args, cfg, _parser: seen.append(cfg) or 0)

    assert main(["--non-interactive", "--project", str(project), *argv]) == 0
    return seen[0]


def test_a_named_profile_decides_the_models_of_the_run(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bieg z powłoki musi umieć wskazać ten sam profil, co wiersz w panelu."""
    slug = _profile(tmp_path, "Tylko GPT", "openai/gpt-5.6-luna")

    cfg = _config_of_a_run(["--routing-profile", slug], tmp_path, monkeypatch)

    assert cfg.role("coder", "standard")[:2] == ("opencode", "openai/gpt-5.6-luna")


def test_a_profile_can_be_named_by_its_label(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """W wierszu poleceń naturalne jest przepisanie nazwy widzianej w panelu."""
    _profile(tmp_path, "Tylko GPT", "openai/gpt-5.6-luna")

    cfg = _config_of_a_run(["--routing-profile", "Tylko GPT"], tmp_path,
                           monkeypatch)

    assert cfg.role("coder", "standard")[1] == "openai/gpt-5.6-luna"


def test_an_unknown_profile_stops_the_run_before_it_costs_anything(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cicha praca na polityce domyślnej kosztowałaby cały bieg."""
    _profile(tmp_path, "Tylko GPT", "openai/gpt-5.6-luna")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("FORGE_ROUTING_FILE", raising=False)

    with pytest.raises(SystemExit) as stop:
        main(["--non-interactive", "--project", str(tmp_path),
              "--routing-profile", "nie-ma-takiego"])

    assert stop.value.code == 2


def test_without_the_flag_the_shared_profile_is_used(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Dotychczasowe skróty i jednostki systemd nie mogą niczego stracić."""
    _profile(tmp_path, "Tylko GPT", "openai/gpt-5.6-luna")
    routing.save(
        routing.parse({"roles": {"coder": {"slots": {"standard": {
            "agent": "claude", "model": "opus"}}}}}, TASK_DIFFICULTIES),
        profiles.shared_path({"XDG_CONFIG_HOME": str(tmp_path)}))

    cfg = _config_of_a_run([], tmp_path, monkeypatch)

    assert cfg.role("coder", "standard")[:2] == ("claude", "opus")


def test_the_environment_can_select_a_profile_too(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Launcher desktopowy i jednostka systemd nie mają wiersza poleceń."""
    slug = _profile(tmp_path, "Tylko GPT", "openai/gpt-5.6-luna")
    monkeypatch.setenv(profiles.PROFILE_ENV, slug)

    cfg = _config_of_a_run([], tmp_path, monkeypatch)

    assert cfg.role("coder", "standard")[1] == "openai/gpt-5.6-luna"


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
