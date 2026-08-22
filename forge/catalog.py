"""Closed catalog of models Forge may run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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


CATALOG: tuple[CatalogEntry, ...] = (
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
        key="glm-5.3",
        label="GLM 5.3",
        family="glm",
        providers=("opencode",),
        ids={"opencode": "zai-coding-plan/glm-5.3"},
    ),
)

DEFAULTS = {
    "brain": "codex:gpt-5.6-sol:high",
    "planner": "codex:gpt-5.6-sol:high",
    "coder_tdd": "codex:gpt-5.6-luna:high",
    "coder_explore": "codex:gpt-5.6-luna:high",
    "coder_classic": "codex:gpt-5.6-luna:high",
    "reviewer": "codex:gpt-5.6-terra:high",
    "tester": "codex:gpt-5.6-terra:high",
    "whitebox": "codex:gpt-5.6-terra:high",
}

ROLE_TIMEOUTS = {
    "brain": 180,
    "planner": 900,
    "coder_tdd": 3600,
    "coder_explore": 3600,
    "coder_classic": 3600,
    "reviewer": 600,
    "tester": 1800,
    "whitebox": 1800,
    "probe": 60,
}


def find_entry(provider: str, model: str) -> CatalogEntry | None:
    needle = model.strip()
    if not needle:
        return None
    for entry in CATALOG:
        if needle in {entry.key, *entry.ids.values()}:
            if provider in entry.providers:
                return entry
            return None
    return None


def resolve_identity(provider: str, model: str) -> tuple[str, str]:
    if provider not in PROVIDERS:
        raise ValueError(
            "model must use provider:model[:effort], where provider is codex or opencode"
        )
    entry = find_entry(provider, model)
    if entry is None:
        raise ValueError(
            f"unsupported model {provider}:{model or '(empty)'}; "
            "choose a catalog model (GPT family, Grok 4.6, Qwen, DeepSeek, Gemini, GLM)"
        )
    return provider, entry.id_for(provider)


def validate_spec(spec: Any) -> None:
    resolve_identity(spec.provider, spec.model)


def assign_coder_models(
    models: dict[str, Any],
    pool: list[Any] | None = None,
    *,
    rng: Any = None,
) -> dict[str, Any]:
    import random

    from .models import CODER_ROLES

    source = list(pool) if pool is not None else [models[role] for role in CODER_ROLES]
    if not source:
        raise ValueError("at least one coder model is required")
    picker = rng or random.Random()
    needed = len(CODER_ROLES)
    if len(source) >= needed:
        chosen = picker.sample(source, needed)
    else:
        chosen = list(source)
        while len(chosen) < needed:
            chosen.append(picker.choice(source))
        picker.shuffle(chosen)
    updated = dict(models)
    for role, spec in zip(CODER_ROLES, chosen):
        updated[role] = spec
    return updated


def shuffle_coder_models(
    models: dict[str, Any], *, rng: Any = None
) -> dict[str, Any]:
    return assign_coder_models(models, rng=rng)


def catalog_payload() -> dict[str, Any]:
    return {
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
