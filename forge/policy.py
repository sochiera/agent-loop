"""Jan's model routing policy for Forge runs.

The policy is fail-closed. When the promotion state cannot be read, only the
native Codex GPT models and the OpenCode Go GLM Flash reviewer stay available;
Forge never guesses DeepSeek or MiMo. Every decision here is deterministic for a
given RNG, so tests can seed it without touching Jan's home directory.
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .catalog import model_identity
from .models import CODER_ROLES, ROLE_NAMES, ModelSpec

DEFAULT_POLICY_PATH = Path("/home/jan/.hermes/state/model-policy.json")
POLICY_PATH_ENV = "FORGE_MODEL_POLICY_PATH"

PROMOTION_ACTIVE = "active"
PROMOTION_INACTIVE = "inactive"
PROMOTION_UNKNOWN = "unknown"

OPENAI_FAMILY = "gpt"
GLM_FAMILY = "glm"

SOL = ModelSpec("codex", "gpt-5.6-sol", "high")
TERRA = ModelSpec("codex", "gpt-5.6-terra", "high")
LUNA = ModelSpec("codex", "gpt-5.6-luna", "high")
GLM = ModelSpec("opencode", "opencode-go/glm-5.3-flash", "high")
DEEPSEEK = ModelSpec("opencode", "opencode-go/deepseek-v4.1-flash", "high")
MIMO = ModelSpec("opencode", "opencode-go/mimo-v2.6-flash", "high")

# Weighted pools. ``cheap`` is replaced by the promotion-state coder.
STRONG_WEIGHTS: tuple[tuple[ModelSpec, int], ...] = ((SOL, 45), (TERRA, 55))
REVIEW_WEIGHTS: tuple[tuple[ModelSpec, int], ...] = ((TERRA, 55), (SOL, 45))
CODER_WEIGHTS: tuple[tuple[ModelSpec, int], ...] = ((LUNA, 45), (GLM, 15))
TEST_WEIGHTS: tuple[tuple[ModelSpec, int], ...] = ((LUNA, 35), (GLM, 35))
REVIEW_DIVERSITY_WEIGHTS: tuple[tuple[ModelSpec, int], ...] = ((GLM, 55),)

CHEAP_CODER_WEIGHT = 40
CHEAP_TEST_WEIGHT = 30
CHEAP_REVIEW_WEIGHT = 45

ALWAYS_AVAILABLE: tuple[ModelSpec, ...] = (SOL, TERRA, LUNA, GLM)

_WORKER_ROLES = frozenset({*CODER_ROLES, "test_author", "tester"})


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
    """Immutable view of the active promotion state and derived policy."""

    state: str = PROMOTION_UNKNOWN
    path: str = ""
    source: str = "default"
    cheap_coder: ModelSpec | None = None

    # Catalog ---------------------------------------------------------

    def allowed_models(self) -> tuple[ModelSpec, ...]:
        models = list(ALWAYS_AVAILABLE)
        if self.cheap_coder is not None:
            models.append(self.cheap_coder)
        return tuple(models)

    def allows(self, spec: ModelSpec) -> bool:
        identity = model_identity(spec)
        return any(identity == model_identity(item) for item in self.allowed_models())

    def identity_allowed(self, identity: str) -> bool:
        return any(identity == model_identity(item) for item in self.allowed_models())

    # Weighted pools --------------------------------------------------

    def coder_options(self) -> list[tuple[ModelSpec, int]]:
        options = list(CODER_WEIGHTS)
        if self.cheap_coder is not None:
            options.append((self.cheap_coder, CHEAP_CODER_WEIGHT))
        return options

    def test_options(self) -> list[tuple[ModelSpec, int]]:
        options = list(TEST_WEIGHTS)
        if self.cheap_coder is not None:
            options.append((self.cheap_coder, CHEAP_TEST_WEIGHT))
        return options

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
        options = self.coder_options()
        needed = len(CODER_ROLES)
        chosen: list[ModelSpec] = []
        pool = list(options)
        while pool and len(chosen) < needed:
            pick = _weighted_pick(pool, rng)
            chosen.append(pick)
            pool = [
                (spec, weight)
                for spec, weight in pool
                if model_identity(spec) != model_identity(pick)
            ]
        while len(chosen) < needed:
            chosen.append(_weighted_pick(options, rng))
        rng.shuffle(chosen)
        return dict(zip(CODER_ROLES, chosen))

    def select_new_run_models(
        self, rng: Any, overrides: Mapping[str, ModelSpec] | None = None
    ) -> dict[str, ModelSpec]:
        overrides = overrides or {}
        models: dict[str, ModelSpec] = {}
        coders = self.select_coder_roster(rng)
        for role in ROLE_NAMES:
            if role in overrides:
                models[role] = overrides[role]
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
            "reviewer": TERRA,
            "tester": TERRA,
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
        if winner_family == OPENAI_FAMILY:
            options = list(REVIEW_DIVERSITY_WEIGHTS)
            if self.cheap_coder is not None:
                options.append((self.cheap_coder, CHEAP_REVIEW_WEIGHT))
        else:
            options = list(REVIEW_WEIGHTS)
            if winner_family != GLM_FAMILY:
                options.append((GLM, 20))
        healthy = [
            (spec, weight)
            for spec, weight in options
            if model_identity(spec) not in disabled_ids
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
            "cheap_coder": self.cheap_coder.display() if self.cheap_coder else "",
            "allowed_models": [spec.display() for spec in self.allowed_models()],
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


def cheap_coder_for(state: str) -> ModelSpec | None:
    if state == PROMOTION_ACTIVE:
        return DEEPSEEK
    if state == PROMOTION_INACTIVE:
        return MIMO
    return None


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
        cheap_coder=cheap_coder_for(state),
    )


def snapshot_from_dict(data: Mapping[str, Any]) -> PromotionSnapshot:
    state = str(data.get("promotion_state") or PROMOTION_UNKNOWN)
    if state not in {PROMOTION_ACTIVE, PROMOTION_INACTIVE}:
        state = PROMOTION_UNKNOWN
    return PromotionSnapshot(
        state=state,
        path=str(data.get("path") or ""),
        source=str(data.get("source") or "default"),
        cheap_coder=cheap_coder_for(state),
    )


def policy_allows(spec: ModelSpec, snapshot: PromotionSnapshot) -> bool:
    return snapshot.allows(spec)
