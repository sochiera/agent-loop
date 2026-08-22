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
        ids={"opencode": "qwencloud-token-plan/qwen3.8-max"},
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
    "coder_tdd": "opencode:gpt-5.6-luna:high",
    "coder_explore": "opencode:gpt-5.6-luna:high",
    "coder_classic": "opencode:gpt-5.6-luna:high",
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
            "choose a catalog model (GPT family, Grok 4.6, Qwen 3.8 Max, GLM 5.3)"
        )
    return provider, entry.id_for(provider)


def validate_spec(spec: Any) -> None:
    resolve_identity(spec.provider, spec.model)


def shuffle_coder_models(
    models: dict[str, Any], *, rng: Any = None
) -> dict[str, Any]:
    import random

    from .models import CODER_ROLES

    pool = rng or random.Random()
    assigned = [models[role] for role in CODER_ROLES]
    pool.shuffle(assigned)
    updated = dict(models)
    for role, spec in zip(CODER_ROLES, assigned):
        updated[role] = spec
    return updated


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
