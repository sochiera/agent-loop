"""Jan's model routing policy for Forge runs.

The smart roster mirrors ~/.hermes/scripts/model_policy.py and is closed:
four models, each pinned to one harness and one reasoning effort. Forge adds a
cheap pool of four slots (GLM Flash through OpenCode Go plus three slots of
native Codex Luna) that only the cheap-model swarm consumes: its coders and
cheap reviewers draw from it. The sprint tournament keeps drawing its coders
from the smart worker roster. DeepSeek and MiMo stay in the catalog for
identity resolution but are outside the central allowlist, so they are never
allowed. The mirror alone can drift, so ``load_policy`` also asks the central
module which models it allows right now, on the harness each one requires; a
missing or broken central module allows nothing. The legacy GPT-5.6
Sol/Terra/Luna roster is retired and never selected. The promotion state is
still read for audit display, but it no longer changes which models are
eligible. Every decision here is deterministic for a given RNG, so tests can
seed it without touching Jan's home directory.
"""

from __future__ import annotations

import importlib.util
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
DEFAULT_CENTRAL_POLICY_SCRIPT = Path("/home/jan/.hermes/scripts/model_policy.py")
CENTRAL_POLICY_ENV = "FORGE_CENTRAL_POLICY_SCRIPT"

PROMOTION_ACTIVE = "active"
PROMOTION_INACTIVE = "inactive"
PROMOTION_UNKNOWN = "unknown"

SOL = ModelSpec("codex", "gpt-6-sol", "medium")
LUNA = ModelSpec("codex", "gpt-6-luna", "xhigh")
OPUS = ModelSpec("claude", "claude-opus-5-5", "medium")
GLM = ModelSpec("opencode", "opencode-go/glm-5.3-flash", "xhigh")
DEEPSEEK = ModelSpec("opencode", "opencode-go/deepseek-v4.1-flash", "xhigh")
MIMO = ModelSpec("opencode", "opencode-go/mimo-v2.6-flash", "xhigh")

ALLOWED_MODELS: tuple[ModelSpec, ...] = (SOL, LUNA, OPUS, GLM)

# Forge-only cheap models allowed for the swarm's cheap roles alone. Empty while
# the central policy allowlists no such model.
CODER_ONLY_MODELS: tuple[ModelSpec, ...] = ()

# The cheap pool is a list of slots; Luna's large limits earn it three. Only the
# swarm consumes it: each team draws two coder and two reviewer slots.
CHEAP_CODER_POOL: tuple[ModelSpec, ...] = (GLM, LUNA, LUNA, LUNA)

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
CODER_WEIGHTS: tuple[tuple[ModelSpec, int], ...] = ((OPUS, 25), (LUNA, 50), (GLM, 25))
TEST_WEIGHTS: tuple[tuple[ModelSpec, int], ...] = ((OPUS, 20), (LUNA, 55), (GLM, 25))

_PINNED_EFFORTS = {model_identity(spec): spec.effort for spec in ALLOWED_MODELS}
_WORKER_ROLES = frozenset({*CODER_ROLES, "test_author", "tester"})
_CODER_ONLY = frozenset(model_identity(spec) for spec in CODER_ONLY_MODELS)
# The swarm's cheap roles; their models come from the cheap pool alone.
SWARM_CODER_ROLE = "swarm_coder"
SWARM_REVIEWER_ROLE = "swarm_reviewer"
# Roles that may run a coder-only model: the swarm's cheap roles and the
# preflight health check. Tournament coders may not.
_CODER_ONLY_ROLES = frozenset({SWARM_CODER_ROLE, SWARM_REVIEWER_ROLE, "probe"})


def _weighted_pick(
    options: Sequence[tuple[ModelSpec, int]], rng: Any
) -> ModelSpec:
    if not options:
        raise ValueError("the central model policy allows no model for this role")
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
    # Forge identities the central policy allows on their required harness;
    # ``None`` means the central module was not consulted (hermetic snapshots).
    central: frozenset[str] | None = None

    # Catalog ---------------------------------------------------------

    def allowed_models(self) -> tuple[ModelSpec, ...]:
        return ALLOWED_MODELS

    def allows(self, spec: ModelSpec, role: str | None = None) -> bool:
        """Allow only a roster model at its pinned effort (empty means pinned).

        A model must also be in the central allowlist when it was loaded. With
        a role, coder-only models are refused outside the swarm roles; ``None``
        checks catalog membership alone.
        """

        identity = model_identity(spec)
        pinned = _PINNED_EFFORTS.get(identity)
        if pinned is None or spec.effort not in {"", pinned}:
            return False
        if not self._centrally_allowed(spec):
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

    def _centrally_allowed(self, spec: ModelSpec) -> bool:
        return self.central is None or model_identity(spec) in self.central

    def _weighted(
        self, options: Iterable[tuple[ModelSpec, int]]
    ) -> list[tuple[ModelSpec, int]]:
        return [(spec, weight) for spec, weight in options if self._centrally_allowed(spec)]

    def cheap_pool(self) -> tuple[ModelSpec, ...]:
        """The cheap slots the central policy allows; only the swarm draws
        from them."""

        return tuple(spec for spec in CHEAP_CODER_POOL if self._centrally_allowed(spec))

    def coder_options(self) -> list[tuple[ModelSpec, int]]:
        return self._weighted(CODER_WEIGHTS)

    def test_options(self) -> list[tuple[ModelSpec, int]]:
        return self._weighted(TEST_WEIGHTS)

    def strong_options(self) -> list[tuple[ModelSpec, int]]:
        return self._weighted(STRONG_WEIGHTS)

    def review_options(self) -> list[tuple[ModelSpec, int]]:
        return self._weighted(REVIEW_WEIGHTS)

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
            "cheap_pool": [spec.display() for spec in self.cheap_pool()],
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


def _central_module(script: str | Path | None = None) -> Any | None:
    """Load the central policy module; ``None`` when it cannot be loaded."""

    if script:
        target = Path(script).expanduser()
    else:
        override = os.environ.get(CENTRAL_POLICY_ENV)
        target = Path(override).expanduser() if override else DEFAULT_CENTRAL_POLICY_SCRIPT
    try:
        module_spec = importlib.util.spec_from_file_location("_forge_central_policy", target)
        if module_spec is None or module_spec.loader is None:
            return None
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
    except Exception:
        return None
    return module


def central_allowed_identities(script: str | Path | None = None) -> frozenset[str]:
    """Forge identities the central policy allows now, on their own harness.

    Each candidate goes through the central ``validate_model(id, harness)``
    gate, the same one the harness wrappers run before launch. Any failure to
    load the module allows nothing (fail closed).
    """

    module = _central_module(script)
    if module is None:
        return frozenset()
    allowed: set[str] = set()
    for identity, policy_id in POLICY_IDS.items():
        harness = identity.split(":", 1)[0]
        try:
            module.validate_model(policy_id, harness)
        except Exception:
            continue
        allowed.add(identity)
    return frozenset(allowed)


def central_launch_refusal(
    spec: ModelSpec, script: str | Path | None = None
) -> tuple[str, str] | None:
    """Ask the central ``assert_launchable`` whether ``spec`` may start now.

    Returns ``None`` when it may, else ``(kind, reason)`` with kind ``quota``
    (its subscription is hard-blocked) or ``policy`` (the central module, its
    quota gate or the model's central id is unavailable: fail closed). This is
    the quota preflight the harness wrappers run; Forge launches native
    harness binaries, so it must run it itself.
    """

    policy_id = POLICY_IDS.get(model_identity(spec))
    if policy_id is None:
        return "policy", f"{spec.display()} has no central policy id"
    module = _central_module(script)
    gate = getattr(module, "assert_launchable", None) if module is not None else None
    if not callable(gate):
        return "policy", "the central policy quota gate (assert_launchable) is unavailable"
    try:
        gate(policy_id)
    except Exception as exc:  # the central PolicyError, or a broken gate
        kind = "quota" if "quota" in str(exc).lower() else "policy"
        return kind, f"{policy_id}: {exc}"
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
        central=central_allowed_identities(),
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
