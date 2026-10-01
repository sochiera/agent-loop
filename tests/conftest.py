"""Shared test fixtures.

Every test gets a hermetic model-policy path so results never depend on Jan's
live ``~/.hermes/state/model-policy.json``. Tests that exercise promotion
states write their own file and pass it explicitly.
"""

import pytest

from forge.policy import POLICY_PATH_ENV


@pytest.fixture(autouse=True)
def hermetic_model_policy(tmp_path, monkeypatch):
    monkeypatch.setenv(POLICY_PATH_ENV, str(tmp_path / "absent-model-policy.json"))
    return tmp_path / "absent-model-policy.json"
