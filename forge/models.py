"""Small serializable data types shared by the controller and UI."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ROLE_NAMES = (
    "brain",
    "planner",
    "coder_tdd",
    "coder_explore",
    "coder_classic",
    "reviewer",
    "tester",
    "whitebox",
)

CODER_ROLES = ("coder_tdd", "coder_explore", "coder_classic")

STAFF_ROLES = ("brain", "planner", "reviewer", "tester", "whitebox")

DEFAULT_MODEL_SELECTORS = {
    "brain": "codex:gpt-5.6-sol:high",
    "planner": "codex:gpt-5.6-sol:high",
    "coder_tdd": "codex:gpt-5.6-luna:high",
    "coder_explore": "codex:gpt-5.6-luna:high",
    "coder_classic": "codex:gpt-5.6-luna:high",
    "reviewer": "codex:gpt-5.6-terra:high",
    "tester": "codex:gpt-5.6-terra:high",
    "whitebox": "codex:gpt-5.6-terra:high",
}


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str = ""
    effort: str = ""

    @classmethod
    def parse(cls, value: str) -> "ModelSpec":
        from .catalog import resolve_identity

        parts = value.strip().split(":", 2)
        if not parts or not parts[0]:
            raise ValueError(
                "model must use provider:model[:effort], where provider is "
                "codex or opencode"
            )
        provider, model = resolve_identity(
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
    shuffle_coders: bool = False
    backup: ModelSpec | None = None

    def validate(self) -> None:
        from .catalog import validate_spec

        repo = Path(self.repo).expanduser().resolve()
        brief = Path(self.brief).expanduser().resolve()
        if not (repo / ".git").exists():
            raise ValueError(f"not a Git repository: {repo}")
        if not brief.is_file():
            raise ValueError(f"brief does not exist: {brief}")
        missing = sorted(set(ROLE_NAMES) - set(self.models))
        if missing:
            raise ValueError(f"missing model selections: {', '.join(missing)}")
        for spec in self.models.values():
            validate_spec(spec)
        if self.backup is not None:
            validate_spec(self.backup)
        if not self.branch.strip():
            raise ValueError("branch cannot be empty")
        if self.agent_timeout_seconds < 1:
            raise ValueError("agent timeout must be positive")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["models"] = {key: asdict(value) for key, value in self.models.items()}
        data["backup"] = asdict(self.backup) if self.backup is not None else None
        return data

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RunConfig":
        backup = value.get("backup")
        return cls(
            repo=str(value["repo"]),
            brief=str(value["brief"]),
            branch=str(value["branch"]),
            models={key: ModelSpec(**spec) for key, spec in value["models"].items()},
            push=bool(value.get("push", True)),
            agent_timeout_seconds=int(value.get("agent_timeout_seconds", 3600)),
            retry_count=int(value.get("retry_count", 2)),
            stalled_turns=int(value.get("stalled_turns", 3)),
            shuffle_coders=bool(value.get("shuffle_coders", False)),
            backup=ModelSpec(**backup) if isinstance(backup, dict) else None,
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
    batches: list[dict[str, Any]] = field(default_factory=list)
    final_summary: str = ""
    paused: bool = False
    cancel_requested: bool = False
    active_agents: dict[str, dict[str, Any]] = field(default_factory=dict)
    checkpoint: dict[str, Any] = field(default_factory=dict)
    last_red_flags: list[str] = field(default_factory=list)
    original_models: dict[str, dict[str, Any]] = field(default_factory=dict)
    disabled_models: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RunState":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: value[key] for key in allowed if key in value})
