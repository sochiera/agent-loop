"""Jan's model routing policy for Forge runs.

The smart roster mirrors ~/.hermes/scripts/model_policy.py and is closed:
four models, each pinned to one harness and one reasoning effort. Forge adds a
cheap coder pool of six slots (DeepSeek, MiMo and GLM Flash through OpenCode Go
plus three slots of native Codex Luna); the tournament coders draw three slots
from it without replacement. DeepSeek and MiMo are coder-only and never staff a
planner, reviewer, or any other role. The legacy GPT-5.6 Sol/Terra/Luna roster
is retired and never selected. The promotion state is still read for audit
display, but it no longer changes which models are eligible. Every decision
here is deterministic for a given RNG, so tests can seed it without touching
Jan's home directory.
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .catalog import model_family, model_identity
from .models import CODER_ROLES, ROLE_NAMES, ModelSpec

DEFAULT_POLICY_PATH = Path("/home/jan/.hermes/state/model-policy.json")
POLICY_PATH_ENV = "FORGE_MODEL_POLICY_PATH"

PROMOTION_ACTIVE = "active"
PROMOTION_INACTIVE = "inactive"
PROMOTION_UNKNOWN = "unknown"

SOL = ModelSpec("codex", "gpt-6-sol", "medium")
LUNA = ModelSpec("codex", "gpt-6-luna", "xhigh")
OPUS = ModelSpec("claude", "claude-opus-5-5", "medium")
GLM = ModelSpec("opencode", "opencode-go/glm-5.3-flash", "xhigh")
DEEPSEEK = ModelSpec("opencode", "opencode-go/deepseek-v4.1-flash", "xhigh")
MIMO = ModelSpec("opencode", "opencode-go/mimo-v2.6-flash", "xhigh")

ALLOWED_MODELS: tuple[ModelSpec, ...] = (SOL, LUNA, OPUS, GLM, DEEPSEEK, MIMO)

# Forge-only cheap models: allowed for the tournament coders, nowhere else.
CODER_ONLY_MODELS: tuple[ModelSpec, ...] = (DEEPSEEK, MIMO)

# The cheap coder pool is a list of slots; Luna's large limits earn it three.
# Each sprint draws len(CODER_ROLES) slots without replacement.
CHEAP_CODER_POOL: tuple[ModelSpec, ...] = (DEEPSEEK, MIMO, GLM, LUNA, LUNA, LUNA)

# Exact identifiers used by the central Hermes policy.
POLICY_IDS = {
    model_identity(SOL): "openai-codex/gpt-6-sol",
    model_identity(LUNA): "openai-codex/gpt-6-luna",
    model_identity(OPUS): "claude-code/claude-opus-5-5",
    model_identity(GLM): "opencode-go/glm-5.3-flash",
    model_identity(DEEPSEEK): "opencode-go/deepseek-v4.1-flash",
    model_identity(MIMO): "opencode-go/mimo-v2.6-flash",
}

# Weighted pools, copied from the central policy's role weights.
STRONG_WEIGHTS: tuple[tuple[ModelSpec, int], ...] = ((SOL, 50), (GLM, 50))
REVIEW_WEIGHTS: tuple[tuple[ModelSpec, int], ...] = ((SOL, 50), (GLM, 50))
TEST_WEIGHTS: tuple[tuple[ModelSpec, int], ...] = ((OPUS, 20), (LUNA, 55), (GLM, 25))

_PINNED_EFFORTS = {model_identity(spec): spec.effort for spec in ALLOWED_MODELS}
_WORKER_ROLES = frozenset({*CODER_ROLES, "test_author", "tester"})
_CODER_ONLY = frozenset(model_identity(spec) for spec in CODER_ONLY_MODELS)
# Roles that may run a coder-only model; "probe" is the preflight health check.
_CODER_ONLY_ROLES = frozenset({*CODER_ROLES, "probe"})


def _weighted_pick(
    options: Sequence[tuple[ModelSpec, int]], rng: Any
) -> ModelSpec:
    specs = [spec for spec, _ in options]
    weights = [max(1, int(weight)) for _, weight in options]
    return rng.choices(specs, weights=weights, k=1)[0]


def _unique(
    options: Iterable[tuple[ModelSpec, int]]
) -> list[tuple[ModelSpec, int]]:
    seen: set[str] = set()
    result: list[tuple[ModelSpec, int]] = []
    for spec, weight in options:
        identity = model_identity(spec)
        if identity in seen:
            continue
        seen.add(identity)
        result.append((spec, weight))
    return result


@dataclass(frozen=True)
class PromotionSnapshot:
    """Immutable view of the promotion state and the closed model roster."""

    state: str = PROMOTION_UNKNOWN
    path: str = ""
    source: str = "default"

    # Catalog ---------------------------------------------------------

    def allowed_models(self) -> tuple[ModelSpec, ...]:
        return ALLOWED_MODELS

    def allows(self, spec: ModelSpec, role: str | None = None) -> bool:
        """Allow only a roster model at its pinned effort (empty means pinned).

        With a role, coder-only models are refused outside the coder roles;
        ``None`` checks catalog membership alone.
        """

        identity = model_identity(spec)
        pinned = _PINNED_EFFORTS.get(identity)
        if pinned is None or spec.effort not in {"", pinned}:
            return False
        return role is None or identity not in _CODER_ONLY or role in _CODER_ONLY_ROLES

    def identity_allowed(self, identity: str) -> bool:
        return identity in _PINNED_EFFORTS

    def pin(self, spec: ModelSpec) -> ModelSpec:
        """Fill an empty effort with the policy effort for a roster model."""

        pinned = _PINNED_EFFORTS.get(model_identity(spec))
        if pinned is None or spec.effort:
            return spec
        return ModelSpec(spec.provider, spec.model, pinned)

    # Weighted pools --------------------------------------------------

    def coder_pool(self) -> tuple[ModelSpec, ...]:
        return CHEAP_CODER_POOL

    def coder_options(self) -> list[tuple[ModelSpec, int]]:
        """The cheap pool as weighted options: one weight per slot."""

        weights: dict[ModelSpec, int] = {}
        for spec in CHEAP_CODER_POOL:
            weights[spec] = weights.get(spec, 0) + 1
        return list(weights.items())

    def test_options(self) -> list[tuple[ModelSpec, int]]:
        return list(TEST_WEIGHTS)

    def strong_options(self) -> list[tuple[ModelSpec, int]]:
        return list(STRONG_WEIGHTS)

    def review_options(self) -> list[tuple[ModelSpec, int]]:
        return list(REVIEW_WEIGHTS)

    def role_options(self, role: str) -> list[tuple[ModelSpec, int]]:
        if role in {"brain", "planner"}:
            return self.strong_options()
        if role == "reviewer":
            return self.review_options()
        if role in CODER_ROLES:
            return self.coder_options()
        return self.test_options()

    # Selection -------------------------------------------------------

    def select_role(self, role: str, rng: Any) -> ModelSpec:
        return _weighted_pick(self.role_options(role), rng)

    def select_coder_roster(self, rng: Any) -> dict[str, ModelSpec]:
        chosen = rng.sample(list(CHEAP_CODER_POOL), len(CODER_ROLES))
        return dict(zip(CODER_ROLES, chosen))

    def select_new_run_models(
        self, rng: Any, overrides: Mapping[str, ModelSpec] | None = None
    ) -> dict[str, ModelSpec]:
        overrides = overrides or {}
        models: dict[str, ModelSpec] = {}
        coders = self.select_coder_roster(rng)
        for role in ROLE_NAMES:
            if role in overrides:
                models[role] = self.pin(overrides[role])
            elif role in CODER_ROLES:
                models[role] = coders[role]
            else:
                models[role] = self.select_role(role, rng)
        return models

    def representative_defaults(self) -> dict[str, str]:
        defaults = {
            "brain": SOL,
            "planner": SOL,
            "test_author": LUNA,
            "reviewer": SOL,
            "tester": LUNA,
        }
        for role in CODER_ROLES:
            defaults[role] = LUNA
        return {role: defaults[role].display() for role in ROLE_NAMES}

    # Failover and review diversity -----------------------------------

    def failover_options(self, role: str) -> tuple[ModelSpec, ...]:
        if role in _WORKER_ROLES:
            ordered = self.role_options(role) + self.strong_options()
        else:
            ordered = self.strong_options() + self.test_options()
        return tuple(spec for spec, _ in _unique(ordered))

    def independent_reviewer(
        self, winner_family: str, *, disabled: Sequence[str] = (), rng: Any = None
    ) -> tuple[ModelSpec | None, str]:
        disabled_ids = {str(item) for item in disabled}
        healthy = [
            (spec, weight)
            for spec, weight in self.review_options()
            if model_family(spec) != winner_family
            and model_identity(spec) not in disabled_ids
        ]
        if not healthy:
            return (
                None,
                f"no independent active family is healthy to review a {winner_family} winner",
            )
        return _weighted_pick(healthy, rng or random.Random()), ""

    # Persistence -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "promotion_state": self.state,
            "path": self.path,
            "source": self.source,
            "allowed_models": [spec.display() for spec in self.allowed_models()],
            "policy_ids": [
                POLICY_IDS[model_identity(spec)] for spec in self.allowed_models()
            ],
            "coder_only_models": [spec.display() for spec in CODER_ONLY_MODELS],
            "coder_pool": [spec.display() for spec in self.coder_pool()],
        }


def _read_promotion_state(path: Path) -> tuple[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return PROMOTION_UNKNOWN, "default"
    value: Any = raw
    if isinstance(raw, dict):
        value = raw.get("promotion_state")
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {PROMOTION_ACTIVE, PROMOTION_INACTIVE}:
            return normalized, "file"
    return PROMOTION_UNKNOWN, "default"


def load_policy(path: str | Path | None = None) -> PromotionSnapshot:
    if path:
        target = Path(path).expanduser()
    else:
        override = os.environ.get(POLICY_PATH_ENV)
        target = Path(override).expanduser() if override else DEFAULT_POLICY_PATH
    state, source = _read_promotion_state(target)
    return PromotionSnapshot(
        state=state,
        path=str(target),
        source=source,
    )


def snapshot_from_dict(data: Mapping[str, Any]) -> PromotionSnapshot:
    """Rebuild a snapshot for audit/display only.

    Never use this to authorize execution: persisted snapshots can be stale, so
    recovery, failover and migration must reload the current policy instead.
    """

    state = str(data.get("promotion_state") or PROMOTION_UNKNOWN)
    if state not in {PROMOTION_ACTIVE, PROMOTION_INACTIVE}:
        state = PROMOTION_UNKNOWN
    return PromotionSnapshot(
        state=state,
        path=str(data.get("path") or ""),
        source=str(data.get("source") or "default"),
    )


def policy_allows(
    spec: ModelSpec, snapshot: PromotionSnapshot, role: str | None = None
) -> bool:
    return snapshot.allows(spec, role)
