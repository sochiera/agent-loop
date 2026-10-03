"""Shared test fixtures.

Every test gets a hermetic model-policy path so results never depend on Jan's
live ``~/.hermes/state/model-policy.json``. Tests that exercise promotion
states write their own file and pass it explicitly. The central policy module
is replaced by a stub that allowlists the current central catalog, so tests
never import ``~/.hermes/scripts/model_policy.py``.
"""

import pytest

from forge.policy import CENTRAL_POLICY_ENV, POLICY_PATH_ENV

CENTRAL_STUB = '''
HARNESSES = {
    "openai-codex/gpt-6-sol": "codex",
    "openai-codex/gpt-6-luna": "codex",
    "opencode-go/glm-5.3-flash": "opencode",
    "claude-code/claude-opus-5-5": "claude",
    "opencode/space-bunny-free": "opencode",
}


def validate_model(model, harness=None):
    if model not in HARNESSES:
        raise ValueError(f"model is not active or not allowlisted: {model!r}")
    if harness is not None and harness != HARNESSES[model]:
        raise ValueError(f"model {model!r} requires harness {HARNESSES[model]!r}")
    return model


def assert_launchable(model, now=None):
    return model
'''


@pytest.fixture(autouse=True)
def hermetic_model_policy(tmp_path, tmp_path_factory, monkeypatch):
    # Outside tmp_path, so tests that list tmp_path never see the stub.
    central = tmp_path_factory.mktemp("central") / "model_policy.py"
    central.write_text(CENTRAL_STUB, encoding="utf-8")
    monkeypatch.setenv(CENTRAL_POLICY_ENV, str(central))
    monkeypatch.setenv(POLICY_PATH_ENV, str(tmp_path / "absent-model-policy.json"))
    return tmp_path / "absent-model-policy.json"
