from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from forge import agents
from forge.agents import LimitExhausted, run_claude
from forge.config import Config


def _limited(_argv, _cwd, _cfg, **_kwargs) -> tuple[int, str, str]:
    return 1, "rate limit reached", ""


def test_total_backoff_never_exceeds_the_budget() -> None:
    """backoff_max_s ogranicza JEDNO oczekiwanie; budżet ogranicza sumę.

    Bez budżetu 20 ponowień z podwajaniem to ok. 10 dni czekania."""
    cfg = Config()
    slept: list[float] = []

    with patch("forge.agents._run_once", side_effect=_limited), \
         patch("forge.agents.time.sleep", side_effect=slept.append), \
         patch("forge.agents._append_log"):
        with pytest.raises(LimitExhausted):
            agents._run_with_backoff(["tool"], "/tmp", cfg, "/tmp/log")

    assert sum(slept) <= cfg.backoff_total_s
    assert sum(slept) > cfg.backoff_total_s / 2  # budżet ma być realnie wykorzystany


def test_backoff_budget_defaults_to_24h() -> None:
    assert Config().backoff_total_s == 24 * 3600


def test_backoff_still_grows_geometrically() -> None:
    cfg = Config()
    slept: list[float] = []

    with patch("forge.agents._run_once", side_effect=_limited), \
         patch("forge.agents.time.sleep", side_effect=slept.append), \
         patch("forge.agents._append_log"):
        with pytest.raises(LimitExhausted):
            agents._run_with_backoff(["tool"], "/tmp", cfg, "/tmp/log")

    assert slept[0] == cfg.backoff_start_s
    assert slept[1] == cfg.backoff_start_s * 2
    # Rośnie geometrycznie aż do ostatniego snu, który jest docinany do reszty
    # budżetu — dzięki temu suma trafia dokładnie w pułap, nie ponad niego.
    growing = slept[:-1]
    assert all(later == earlier * 2 for earlier, later in zip(growing, growing[1:]))
    assert slept[-1] <= growing[-1]
    assert sum(slept) == cfg.backoff_total_s


def _usage_rows(project: str) -> list[dict]:
    path = Path(project, ".forge", "usage.jsonl")
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_generic_agent_records_its_call_even_without_token_counts() -> None:
    """Domyślny mistrz to opencode (ścieżka generyczna) — bez rekordu jego
    koszt byłby całkowicie niewidoczny w raporcie projektu."""
    with tempfile.TemporaryDirectory() as project, \
         tempfile.TemporaryDirectory() as sandbox, \
         patch.dict("os.environ", {"FORGE_AGENT_OPENCODE_CMD": "opencode {prompt}"}), \
         patch("forge.agents._run_with_backoff", return_value="{}"):
        agents.run_agent("opencode", "prompt", Config(), sandbox,
                         "/tmp/iter-0001-master.log", usage_dir=project)

        rows = _usage_rows(project)
        assert rows and rows[0]["phase"] == "master"
        assert rows[0]["agent"] == "opencode"
        assert rows[0]["usage_unavailable"] is True
        assert not Path(sandbox, ".forge", "usage.jsonl").exists()


def test_codex_one_shot_records_its_call() -> None:
    with tempfile.TemporaryDirectory() as project, \
         tempfile.TemporaryDirectory() as sandbox, \
         patch("forge.agents._run_with_backoff", return_value=""):
        agents.run_codex("prompt", Config(), sandbox,
                         "/tmp/iter-0001-master.log", usage_dir=project)

        rows = _usage_rows(project)
        assert rows and rows[0]["agent"] == "codex"
        assert rows[0]["phase"] == "master"
        assert rows[0]["usage_unavailable"] is True


def test_master_usage_lands_in_project_telemetry_not_the_sandbox() -> None:
    """Mistrz pracuje w katalogu tymczasowym, ale jego koszt musi być widoczny."""
    answer = json.dumps({"result": "{}", "usage": {"input_tokens": 5, "output_tokens": 2}})
    with tempfile.TemporaryDirectory() as project, \
         tempfile.TemporaryDirectory() as sandbox, \
         patch("forge.agents._run_with_backoff", return_value=answer):
        run_claude("prompt", Config(), sandbox, "/tmp/iter-0001-master.log",
                   usage_dir=project)

        recorded = Path(project, ".forge", "usage.jsonl")
        assert recorded.is_file()
        assert not Path(sandbox, ".forge", "usage.jsonl").exists()
        row = json.loads(recorded.read_text(encoding="utf-8").splitlines()[0])
        assert row["phase"] == "master"
        assert row["usage"]["input_tokens"] == 5
