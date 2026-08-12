"""Watchdog bezczynności: wykrywanie agenta, który żyje, ale nic nie robi.

Regresja z 2026-08-12: opencode przy awarii dostawcy ponawiał zapytanie w ciszy
z podwajanym odstępem (2, 4, 8 … 1024 s, bez sufitu). Jedyną obroną Forge'a był
godzinny zegar ścienny, więc tura, która po ponowieniu zajmowała 26 s,
skasowała bieg po pełnej godzinie — i bez transkryptu, bo log powstawał dopiero
po wyjściu procesu.
"""
from __future__ import annotations

import os
import selectors
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from forge import agents
from forge.agents import AgentError, AgentStalled, _idle_timeout_for, _run_once
from forge.config import Config


def _python(script: str) -> list[str]:
    return [sys.executable, "-u", "-c", script]


# --- kwalifikacja: komu w ogóle wolno postawić watchdoga -------------------

def test_watchdog_is_on_for_agents_that_stream_progress() -> None:
    cfg = Config(agent_idle_timeout_s=600, agent_timeout_s=3600)

    opencode = ["opencode", "run", "prompt", "--format", "json", "--dir", "/p"]
    codex = ["codex", "exec", "--json", "prompt"]

    assert _idle_timeout_for(opencode, cfg) == 600
    assert _idle_timeout_for(codex, cfg) == 600


def test_watchdog_is_off_for_agents_that_answer_only_at_the_end() -> None:
    """Claude wypisuje JEDEN obiekt na końcu tury — cisza jest tam normalna.

    Gdyby watchdog obejmował i jego, ubijałby agentów w trakcie pracy; token
    '--output-format' celowo nie liczy się jako '--format'."""
    cfg = Config(agent_idle_timeout_s=600)
    claude = ["claude", "-p", "prompt", "--output-format", "json"]

    assert _idle_timeout_for(claude, cfg) == 0


def test_watchdog_can_be_disabled_and_never_outruns_the_wall_clock() -> None:
    streaming = ["opencode", "run", "p", "--format", "json"]

    assert _idle_timeout_for(streaming, Config(agent_idle_timeout_s=0)) == 0
    # Ciasny FORGE_AGENT_TIMEOUT musi kończyć się timeoutem, nie „zawisem".
    tight = Config(agent_idle_timeout_s=600, agent_timeout_s=120)
    assert _idle_timeout_for(streaming, tight) == 120


# --- _run_once: strumień, cisza, grupa procesów ---------------------------

def test_run_once_returns_streams_and_code_separately() -> None:
    argv = _python("import sys; print('odpowiedź'); print('szum', file=sys.stderr);"
                   " sys.exit(3)")

    code, out, err = _run_once(argv, os.getcwd(), Config(agent_timeout_s=30))

    assert code == 3
    assert out.strip() == "odpowiedź"
    assert err.strip() == "szum"


def test_silence_raises_stall_and_carries_what_was_printed_before() -> None:
    argv = _python("import time; print('zaczynam'); time.sleep(30)")
    started = time.monotonic()

    with pytest.raises(AgentStalled) as caught:
        _run_once(argv, os.getcwd(), Config(agent_timeout_s=30), idle_timeout=1)

    assert time.monotonic() - started < 10  # ubity po ciszy, nie po zegarze
    assert "zaczynam" in caught.value.output


def test_steady_output_keeps_the_agent_alive() -> None:
    """Watchdog mierzy POSTĘP, nie czas trwania — pracujący agent ma żyć."""
    argv = _python("import time\n"
                   "for _ in range(10):\n"
                   "    print('tik')\n"
                   "    time.sleep(0.2)\n")

    code, out, _ = _run_once(argv, os.getcwd(), Config(agent_timeout_s=30),
                             idle_timeout=1)

    assert code == 0
    assert out.count("tik") == 10


def test_stall_kills_the_whole_process_group_not_just_the_child() -> None:
    """Osierocony wnuk trzyma nasz pipe i potrafi przeżyć cały bieg Forge'a."""
    argv = _python(
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(child.pid)\n"
        "time.sleep(60)\n")

    with pytest.raises(AgentStalled) as caught:
        _run_once(argv, os.getcwd(), Config(agent_timeout_s=30), idle_timeout=1)

    grandchild = int(caught.value.output.strip().splitlines()[0])
    for _ in range(40):
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(grandchild, 9)
        pytest.fail(f"wnuk {grandchild} przeżył ubicie grupy")


def test_interrupted_run_never_leaves_the_agent_running() -> None:
    """„Stop" w GUI to SIGINT do grupy Forge'a — agenta w WŁASNEJ sesji już nie
    dosięga. Gdyby przerwany odczyt go nie ubijał, po zatrzymaniu biegu zostawał
    w projekcie agent z --dangerously-skip-permissions edytujący pliki."""
    argv = _python("import time; print('zaczynam'); time.sleep(60)")
    spawned: list[subprocess.Popen] = []
    real_popen = subprocess.Popen

    class Interrupting(selectors.DefaultSelector):
        def select(self, timeout=None):
            raise KeyboardInterrupt

    def spy(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        spawned.append(proc)
        return proc

    with patch("forge.agents.subprocess.Popen", side_effect=spy), \
         patch("forge.agents.selectors.DefaultSelector", Interrupting):
        with pytest.raises(KeyboardInterrupt):
            _run_once(argv, os.getcwd(), Config(agent_timeout_s=60))

    assert spawned[0].returncode is not None, "agent przeżył przerwanie biegu"


def test_wall_clock_still_bounds_a_chatty_agent() -> None:
    """Gadatliwa pętla nigdy nie odpali watchdoga — od tego jest zegar ścienny."""
    argv = _python("import time\n"
                   "while True:\n"
                   "    print('tik')\n"
                   "    time.sleep(0.05)\n")

    with pytest.raises(subprocess.TimeoutExpired) as caught:
        _run_once(argv, os.getcwd(), Config(agent_timeout_s=1), idle_timeout=30)

    assert "tik" in (caught.value.output or "")


# --- _run_with_backoff: reakcja pętli na zawis ----------------------------

def _stall(output: str = "") -> AgentStalled:
    return AgentStalled("brak wyjścia przez 1s", output=output)


def test_stalled_turn_is_retried_instead_of_killing_the_run(tmp_path: Path) -> None:
    """Sedno regresji: chwilowa awaria dostawcy kosztuje turę, nie cały bieg."""
    cfg = Config(agent_idle_timeout_s=600, max_stall_retries=3)
    slept: list[float] = []
    calls = [_stall("Our servers are currently overloaded"), (0, "gotowe", "")]

    def once(*_args, **_kwargs):
        result = calls.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    with patch("forge.agents._run_once", side_effect=once), \
         patch("forge.agents.time.sleep", side_effect=slept.append):
        returned = agents._run_with_backoff(
            ["opencode", "run", "p", "--format", "json"], str(tmp_path), cfg,
            str(tmp_path / "iter-0001-po-review.log"))

    assert returned == "gotowe"
    assert slept == [cfg.backoff_start_s]
    assert not calls


def test_persistent_stalls_stop_the_run_after_the_retry_budget(
        tmp_path: Path) -> None:
    cfg = Config(agent_idle_timeout_s=600, max_stall_retries=2)

    with patch("forge.agents._run_once", side_effect=_stall("ogon")), \
         patch("forge.agents.time.sleep"):
        with pytest.raises(AgentError, match="zawiesił się 3 raz"):
            agents._run_with_backoff(
                ["opencode", "run", "p", "--format", "json"], str(tmp_path), cfg,
                str(tmp_path / "iter-0001-po-review.log"))


def test_stall_leaves_a_transcript_to_diagnose_from(tmp_path: Path) -> None:
    """Bez tego po zawisie zostawał wyłącznie log samego CLI agenta."""
    log_path = tmp_path / "iter-0001-po-review.log"
    cfg = Config(agent_idle_timeout_s=600, max_stall_retries=0)

    with patch("forge.agents._run_once", side_effect=_stall("połowa odpowiedzi")), \
         patch("forge.agents.time.sleep"):
        with pytest.raises(AgentError):
            agents._run_with_backoff(
                ["opencode", "run", "p", "--format", "json"], str(tmp_path), cfg,
                str(log_path))

    assert "połowa odpowiedzi" in log_path.read_text(encoding="utf-8")


def test_wall_timeout_also_leaves_a_transcript(tmp_path: Path) -> None:
    log_path = tmp_path / "iter-0001-coder.log"
    expired = subprocess.TimeoutExpired(
        ["opencode"], 3600, output="zdążyłem tyle", stderr="i tyle")

    with patch("forge.agents._run_once", side_effect=expired):
        with pytest.raises(AgentError, match="timeout po"):
            agents._run_with_backoff(["opencode", "run", "p", "--format", "json"],
                                     str(tmp_path), Config(), str(log_path))

    saved = log_path.read_text(encoding="utf-8")
    assert "zdążyłem tyle" in saved and "i tyle" in saved
