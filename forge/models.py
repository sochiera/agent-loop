"""Small serializable data types shared by the controller and UI."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ROLE_NAMES = (
    "brain",
    "planner",
    "test_author",
    "coder_tdd",
    "coder_explore",
    "coder_classic",
    "reviewer",
    "tester",
)

CODER_ROLES = ("coder_tdd", "coder_explore", "coder_classic")

STAFF_ROLES = ("brain", "planner", "test_author", "reviewer", "tester")

DEFAULT_MODEL_SELECTORS = {
    "brain": "codex:gpt-6-sol:medium",
    "planner": "codex:gpt-6-sol:medium",
    "test_author": "codex:gpt-6-luna:xhigh",
    "coder_tdd": "codex:gpt-6-luna:xhigh",
    "coder_explore": "codex:gpt-6-luna:xhigh",
    "coder_classic": "codex:gpt-6-luna:xhigh",
    "reviewer": "codex:gpt-6-sol:medium",
    "tester": "codex:gpt-6-luna:xhigh",
}


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str = ""
    effort: str = ""

    @classmethod
    def parse(cls, value: str) -> "ModelSpec":
        from .catalog import parse_identity

        parts = value.strip().split(":", 2)
        if not parts or not parts[0]:
            raise ValueError(
                "model must use provider:model[:effort], where provider is "
                "codex, claude, or opencode"
            )
        provider, model = parse_identity(
            parts[0], parts[1] if len(parts) > 1 else ""
        )
        return cls(
            provider=provider,
            model=model,
            effort=parts[2] if len(parts) > 2 else "",
        )

    def display(self) -> str:
        value = f"{self.provider}:{self.model}" if self.model else self.provider
        return f"{value}:{self.effort}" if self.effort else value


@dataclass
class RunConfig:
    repo: str
    brief: str
    branch: str
    models: dict[str, ModelSpec]
    push: bool = True
    agent_timeout_seconds: int = 3600
    retry_count: int = 2
    stalled_turns: int = 3
    backup: ModelSpec | None = None
    max_revision_rounds: int = 8
    shuffle_coders: bool = True
    policy_path: str = ""
    # Slots the coder roles are redrawn from each sprint; empty keeps the
    # pre-pool behaviour of reshuffling the run's original coders.
    coder_pool: list[ModelSpec] = field(default_factory=list)

    def validate(self) -> None:
        from .catalog import validate_spec
        from .policy import load_policy, policy_allows

        repo = Path(self.repo).expanduser().resolve()
        brief = Path(self.brief).expanduser().resolve()
        if not (repo / ".git").exists():
            raise ValueError(f"not a Git repository: {repo}")
        if not brief.is_file():
            raise ValueError(f"brief does not exist: {brief}")
        missing = sorted(set(ROLE_NAMES) - set(self.models))
        if missing:
            raise ValueError(f"missing model selections: {', '.join(missing)}")
        snapshot = load_policy(self.policy_path or None)
        for role, spec in self.models.items():
            validate_spec(spec)
            if not policy_allows(spec, snapshot, role):
                raise ValueError(
                    f"{role} model {spec.display()} is not allowed by the active "
                    f"model policy (promotion_state={snapshot.state})"
                )
        if self.backup is not None:
            validate_spec(self.backup)
            if not policy_allows(self.backup, snapshot, "backup"):
                raise ValueError(
                    f"backup model {self.backup.display()} is not allowed by the "
                    f"active model policy (promotion_state={snapshot.state})"
                )
        for spec in self.coder_pool:
            validate_spec(spec)
            if not policy_allows(spec, snapshot, CODER_ROLES[0]):
                raise ValueError(
                    f"coder pool model {spec.display()} is not allowed by the "
                    f"active model policy (promotion_state={snapshot.state})"
                )
        if not self.branch.strip():
            raise ValueError("branch cannot be empty")
        if self.agent_timeout_seconds < 1:
            raise ValueError("agent timeout must be positive")
        if self.stalled_turns < 1:
            raise ValueError("stalled_turns must be positive")
        if self.max_revision_rounds < 1:
            raise ValueError("max_revision_rounds must be positive")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["models"] = {key: asdict(value) for key, value in self.models.items()}
        data["backup"] = asdict(self.backup) if self.backup is not None else None
        data["coder_pool"] = [asdict(spec) for spec in self.coder_pool]
        return data

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RunConfig":
        backup = value.get("backup")
        raw_models = {
            key: ModelSpec(**spec) for key, spec in value["models"].items()
        }
        if not any(role in raw_models for role in CODER_ROLES):
            legacy = raw_models.get("coder")
            if legacy is None:
                legacy = ModelSpec.parse(DEFAULT_MODEL_SELECTORS["coder_tdd"])
            for role in CODER_ROLES:
                raw_models[role] = legacy
        if "test_author" not in raw_models:
            raw_models["test_author"] = ModelSpec.parse(
                DEFAULT_MODEL_SELECTORS["test_author"]
            )
        models = {
            role: raw_models[role]
            for role in ROLE_NAMES
            if role in raw_models
        }
        return cls(
            repo=str(value["repo"]),
            brief=str(value["brief"]),
            branch=str(value["branch"]),
            models=models,
            push=bool(value.get("push", True)),
            agent_timeout_seconds=int(value.get("agent_timeout_seconds", 3600)),
            retry_count=int(value.get("retry_count", 2)),
            stalled_turns=int(value.get("stalled_turns", 3)),
            backup=ModelSpec(**backup) if isinstance(backup, dict) else None,
            max_revision_rounds=int(value.get("max_revision_rounds", 8)),
            shuffle_coders=bool(value.get("shuffle_coders", True)),
            policy_path=str(value.get("policy_path") or ""),
            coder_pool=[
                ModelSpec(**spec)
                for spec in value.get("coder_pool") or []
                if isinstance(spec, dict)
            ],
        )


@dataclass
class Usage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"total_tokens": self.total_tokens}


@dataclass
class AgentResult:
    text: str
    session_id: str | None
    usage: Usage
    elapsed_seconds: float
    raw_output: str
    tool_calls: int = 0
    return_code: int = 0


@dataclass
class RunState:
    run_id: str
    status: str
    phase: str
    created_at: str
    updated_at: str
    config: dict[str, Any]
    cycle: int = 0
    brain_session_id: str | None = None
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    iterations: list[dict[str, Any]] = field(default_factory=list)
    final_summary: str = ""
    paused: bool = False
    cancel_requested: bool = False
    active_agents: dict[str, dict[str, Any]] = field(default_factory=dict)
    checkpoint: dict[str, Any] = field(default_factory=dict)
    last_red_flags: list[str] = field(default_factory=list)
    original_models: dict[str, dict[str, Any]] = field(default_factory=dict)
    disabled_models: list[str] = field(default_factory=list)
    policy_snapshot: dict[str, Any] = field(default_factory=dict)
    model_migrations: list[dict[str, Any]] = field(default_factory=list)
    schema_version: int = 3
    sprint_number: int = 0
    sprint_iteration: int = 0
    sprint_started_at: str = ""
    backlog_revision: int = 0
    backlog: list[dict[str, Any]] = field(default_factory=list)
    quality_backlog: list[dict[str, Any]] = field(default_factory=list)
    accepted_story_ids: list[str] = field(default_factory=list)
    active_iteration: dict[str, Any] = field(default_factory=dict)
    completed_sprints: list[dict[str, Any]] = field(default_factory=list)
    needs_product_owner: bool = True
    sprint_goal: str = ""
    product_owner_inspected: bool = False
    preflight_probed: bool = False
    stalled_recoverable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RunState":
        allowed = cls.__dataclass_fields__.keys()
        payload = {key: value[key] for key in allowed if key in value}
        if "iterations" not in payload and isinstance(value.get("batches"), list):
            payload["iterations"] = value["batches"]
        if "schema_version" not in value:
            payload["schema_version"] = 1
        return cls(**payload)
