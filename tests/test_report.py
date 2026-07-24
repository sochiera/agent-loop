from forge.report import normalize_phase, summarize


def test_kiss_phase_groups() -> None:
    assert normalize_phase("tester") == "tester"
    assert normalize_phase("coder") == "coder"
    assert normalize_phase("corrections") == "corrections"


def test_usage_is_grouped_by_role() -> None:
    rows = summarize([{"agent": "codex", "phase": "tester", "usage": {"input_tokens": 3}}])
    assert rows[("codex", "tester")]["in"] == 3
