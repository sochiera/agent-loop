"""Closed catalog of models Forge may run.

Only the exact identities in :data:`CATALOG` are active. Everything else is
retained solely so old run state, old UI preferences, and old CLI selectors can
still be *parsed*; off-policy identities are rejected for a new run and are
never selected as a fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

PROVIDERS = ("codex", "opencode")


@dataclass(frozen=True)
class CatalogEntry:
    key: str
    label: str
    family: str
    providers: tuple[str, ...]
    ids: dict[str, str]
    efforts: tuple[str, ...] = ("", "low", "medium", "high")

    def id_for(self, provider: str) -> str:
        return self.ids[provider]


# The active Forge catalog. Luna is native Codex only; every OpenCode Go model
# uses its exact ``opencode-go/...`` identifier.
CATALOG: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        key="gpt-5.6-sol",
        label="GPT-5.6 Sol",
        family="gpt",
        providers=("codex",),
        ids={"codex": "gpt-5.6-sol"},
    ),
    CatalogEntry(
        key="gpt-5.6-terra",
        label="GPT-5.6 Terra",
        family="gpt",
        providers=("codex",),
        ids={"codex": "gpt-5.6-terra"},
    ),
    CatalogEntry(
        key="gpt-5.6-luna",
        label="GPT-5.6 Luna",
        family="gpt",
        providers=("codex",),
        ids={"codex": "gpt-5.6-luna"},
    ),
    CatalogEntry(
        key="glm-5.3-flash",
        label="GLM 5.3 Flash",
        family="glm",
        providers=("opencode",),
        ids={"opencode": "opencode-go/glm-5.3-flash"},
    ),
    CatalogEntry(
        key="deepseek-v4.1-flash",
        label="DeepSeek V4.1 Flash",
        family="deepseek",
        providers=("opencode",),
        ids={"opencode": "opencode-go/deepseek-v4.1-flash"},
    ),
    CatalogEntry(
        key="mimo-v2.6-flash",
        label="MiMo V2.6 Flash",
        family="mimo",
        providers=("opencode",),
        ids={"opencode": "opencode-go/mimo-v2.6-flash"},
    ),
)

# Parse-only history. These identities load old artifacts and preferences but
# are never part of the active catalog and never become a fallback.
LEGACY_CATALOG: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        key="gpt-5.6-sol",
        label="GPT-5.6 Sol",
        family="gpt",
        providers=("codex", "opencode"),
        ids={"codex": "gpt-5.6-sol", "opencode": "openai/gpt-5.6-sol"},
    ),
    CatalogEntry(
        key="gpt-5.6-terra",
        label="GPT-5.6 Terra",
        family="gpt",
        providers=("codex", "opencode"),
        ids={"codex": "gpt-5.6-terra", "opencode": "openai/gpt-5.6-terra"},
    ),
    CatalogEntry(
        key="gpt-5.6-luna",
        label="GPT-5.6 Luna",
        family="gpt",
        providers=("codex", "opencode"),
        ids={"codex": "gpt-5.6-luna", "opencode": "openai/gpt-5.6-luna"},
    ),
    CatalogEntry(
        key="gpt-5.5",
        label="GPT-5.5",
        family="gpt",
        providers=("opencode",),
        ids={"opencode": "openai/gpt-5.5"},
    ),
    CatalogEntry(
        key="gpt-5.4",
        label="GPT-5.4",
        family="gpt",
        providers=("opencode",),
        ids={"opencode": "openai/gpt-5.4"},
    ),
    CatalogEntry(
        key="grok-4.6",
        label="Grok 4.6",
        family="grok",
        providers=("opencode",),
        ids={"opencode": "xai/grok-4.6"},
    ),
    CatalogEntry(
        key="qwen-3.8-max",
        label="Qwen 3.8 Max",
        family="qwen",
        providers=("opencode",),
        ids={"opencode": "alibaba-token-plan/qwen3.8-max"},
    ),
    CatalogEntry(
        key="deepseek-v4-flash-0731",
        label="DeepSeek Flash 0731",
        family="deepseek",
        providers=("opencode",),
        ids={"opencode": "alibaba-token-plan/deepseek-v4-flash-0731"},
    ),
    CatalogEntry(
        key="deepseek-v4-pro-0813",
        label="DeepSeek Pro 0813",
        family="deepseek",
        providers=("opencode",),
        ids={"opencode": "alibaba-token-plan/deepseek-v4-pro-0813"},
    ),
    CatalogEntry(
        key="or-gemini-3.7-flash",
        label="Gemini 3.7 Flash OR",
        family="gemini",
        providers=("opencode",),
        ids={"opencode": "openrouter/google/gemini-3.7-flash"},
    ),
    CatalogEntry(
        key="or-gpt-5.6-luna",
        label="GPT-5.6 Luna OR",
        family="gpt",
        providers=("opencode",),
        ids={"opencode": "openrouter/openai/gpt-5.6-luna"},
    ),
    CatalogEntry(
        key="or-deepseek-v4-flash-0731",
        label="DeepSeek Flash 0731 OR",
        family="deepseek",
        providers=("opencode",),
        ids={"opencode": "openrouter/deepseek/deepseek-v4-flash-0731"},
    ),
    CatalogEntry(
        key="or-deepseek-v4-pro",
        label="DeepSeek V4 Pro OR",
        family="deepseek",
        providers=("opencode",),
        ids={"opencode": "openrouter/deepseek/deepseek-v4-pro"},
    ),
    CatalogEntry(
        key="or-deepseek-v4-pro-0813",
        label="DeepSeek V4 Pro 0813 OR",
        family="deepseek",
        providers=("opencode",),
        ids={"opencode": "openrouter/deepseek/deepseek-v4-pro-0813"},
    ),
    CatalogEntry(
        key="kimi-k3",
        label="Kimi K3",
        family="kimi",
        providers=("opencode",),
        ids={"opencode": "kimi-for-coding/k3"},
    ),
    CatalogEntry(
        key="glm-5.3",
        label="GLM 5.3",
        family="glm",
        providers=("opencode",),
        ids={"opencode": "zai-coding-plan/glm-5.3"},
    ),
)

# Representative, fail-closed defaults. Real new runs draw their roster from
# ``forge.policy``; these keep CLI/UI selectors valid when no policy is present.
DEFAULTS = {
    "brain": "codex:gpt-5.6-sol:high",
    "planner": "codex:gpt-5.6-sol:high",
    "test_author": "codex:gpt-5.6-luna:high",
    "coder_tdd": "codex:gpt-5.6-luna:high",
    "coder_explore": "codex:gpt-5.6-luna:high",
    "coder_classic": "codex:gpt-5.6-luna:high",
    "reviewer": "codex:gpt-5.6-terra:high",
    "tester": "codex:gpt-5.6-terra:high",
}

ROLE_TIMEOUTS = {
    "brain": 1800,
    "planner": 900,
    "test_author": 900,
    "coder_tdd": 3600,
    "coder_explore": 3600,
    "coder_classic": 3600,
    "reviewer": 1800,
    "tester": 1800,
    "probe": 60,
}


def _match(
    entries: Sequence[CatalogEntry], provider: str, model: str
) -> CatalogEntry | None:
    needle = model.strip()
    if not needle:
        return None
    for entry in entries:
        if needle in {entry.key, *entry.ids.values()}:
            return entry if provider in entry.providers else None
    return None


def find_active_entry(provider: str, model: str) -> CatalogEntry | None:
    return _match(CATALOG, provider, model)


def find_legacy_entry(provider: str, model: str) -> CatalogEntry | None:
    return _match(LEGACY_CATALOG, provider, model)


def find_entry(provider: str, model: str) -> CatalogEntry | None:
    return find_active_entry(provider, model) or find_legacy_entry(provider, model)


def resolve_identity(provider: str, model: str) -> tuple[str, str]:
    """Resolve an *active* identity, rejecting legacy/off-policy selectors."""

    if provider not in PROVIDERS:
        raise ValueError(
            "model must use provider:model[:effort], where provider is codex or opencode"
        )
    entry = find_active_entry(provider, model)
    if entry is None:
        raise ValueError(
            f"unsupported model {provider}:{model or '(empty)'}; "
            "choose an active policy model "
            "(Codex GPT-5.6 Sol/Terra/Luna or OpenCode Go GLM/DeepSeek/MiMo Flash)"
        )
    return provider, entry.id_for(provider)


def parse_identity(provider: str, model: str) -> tuple[str, str]:
    """Resolve an active or legacy identity so old state can still be read."""

    if provider not in PROVIDERS:
        raise ValueError(
            "model must use provider:model[:effort], where provider is codex or opencode"
        )
    entry = find_entry(provider, model)
    if entry is None:
        raise ValueError(
            f"unsupported model {provider}:{model or '(empty)'}; "
            "choose a catalog model"
        )
    return provider, entry.id_for(provider)


def validate_spec(spec: Any) -> None:
    resolve_identity(spec.provider, spec.model)


def model_identity(spec: Any) -> str:
    return f"{spec.provider}:{spec.model}"


def model_family(spec: Any) -> str:
    entry = find_entry(spec.provider, spec.model)
    return entry.family if entry is not None else spec.provider


def spec_with_effort(spec: Any, effort: str = "") -> Any:
    from .models import ModelSpec

    return ModelSpec(spec.provider, spec.model, effort or spec.effort)


def _weighted_sample_without_replacement(
    options: Sequence[tuple[Any, int]], needed: int, picker: Any
) -> list[Any]:
    pool = [(spec, max(1, int(weight))) for spec, weight in options]
    chosen: list[Any] = []
    while pool and len(chosen) < needed:
        specs = [spec for spec, _ in pool]
        weights = [weight for _, weight in pool]
        pick = picker.choices(specs, weights=weights, k=1)[0]
        chosen.append(pick)
        pool = [
            (spec, weight)
            for spec, weight in pool
            if model_identity(spec) != model_identity(pick)
        ]
    return chosen


def assign_coder_models(
    models: dict[str, Any],
    pool: list[Any] | None = None,
    *,
    rng: Any = None,
    weights: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Draw the three coder roles from a pool, weighted and family-diverse.

    The pool is de-duplicated by identity so distinct families are preferred;
    when it is too small the remaining roles reuse the available models.
    """

    import random

    from .models import CODER_ROLES

    source = list(pool) if pool is not None else [models[role] for role in CODER_ROLES]
    unique: list[Any] = []
    seen: set[str] = set()
    for spec in source:
        identity = model_identity(spec)
        if identity not in seen:
            unique.append(spec)
            seen.add(identity)
    if not unique:
        raise ValueError("at least one coder model is required")
    picker = rng or random.Random()
    weight_map = weights or {}
    options = [
        (spec, int(weight_map.get(model_identity(spec), 1))) for spec in unique
    ]
    needed = len(CODER_ROLES)
    if len(options) >= needed:
        chosen = _weighted_sample_without_replacement(options, needed, picker)
    else:
        chosen = list(unique)
        while len(chosen) < needed:
            chosen.append(picker.choices(
                [spec for spec, _ in options],
                weights=[weight for _, weight in options],
                k=1,
            )[0])
        picker.shuffle(chosen)
    updated = dict(models)
    for role, spec in zip(CODER_ROLES, chosen):
        updated[role] = spec
    return updated


def shuffle_coder_models(
    models: dict[str, Any], *, rng: Any = None, weights: Mapping[str, int] | None = None
) -> dict[str, Any]:
    return assign_coder_models(models, rng=rng, weights=weights)


def catalog_payload(policy_snapshot: Any = None) -> dict[str, Any]:
    payload = {
        "providers": list(PROVIDERS),
        "defaults": dict(DEFAULTS),
        "models": [
            {
                "key": entry.key,
                "label": entry.label,
                "family": entry.family,
                "providers": list(entry.providers),
                "ids": dict(entry.ids),
                "efforts": [item for item in entry.efforts],
            }
            for entry in CATALOG
        ],
    }
    if policy_snapshot is not None:
        payload["policy"] = policy_snapshot.to_dict()
        payload["defaults"] = policy_snapshot.representative_defaults()
    return payload
