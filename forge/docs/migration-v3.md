# Forge v3 migration

This document is the migration authority for the engine tree. It does not
authorize product bootstrap, operator config, native canary, deploy, or
service changes.

## What landed

Pinned source commit `9df17d442b50deeca6a30086735da125519cb6ff` on
`/home/jan/Sources/forge-next` was imported as committed tracked files only.
Delivery fingerprint
`1c618df6b02303a265c381658e6b6c40de79dab3f9ced7bddd68e9b271218aa1`.
Capsule path `/home/jan/.hermes/cache/scratch/forge-next-grok-init/capsule.md`.
Author of the delivered source: Jan Sochiera.

Parent commit, preserved as history and as the legacy checkout:
`fb00771aecc700f765b0b2f3049a5d35282fc54f`.

Distribution identity is `forge-agent-orchestrator` 3.0.0, MIT, src layout,
entrypoint `forge.cli:main`, Python `>=3.12`, no runtime dependencies.
`forge --version` prints `forge 3.0.0`.

## Retired in this tree, kept in parent history

Removed from the new worktree only:

- root package `forge/`, including `forge/static/`
- legacy `tests/`
- `deploy/` (`README.md`, `RUNBOOK.md`, `forge-tunnel.service`, `nginx-forge.conf`, `tunnelforge.sh`)
- `briefs/total-battle-brothers.md`

Those paths are not v3 instructions. Do not restore them as active scripts.
`LICENSE` and `AGENTS.md` stay byte-for-byte.

## Historical records

`.hermes/plans/implementation.md` and `docs/canary/` describe the forge-next
source generation. An older native canary scored 34/34 against a different
fingerprint and is not acceptance of this tree. The imported 2026-10-23 canary
reports record their own BLOCK results. Do not execute launch or deploy steps
from those files.

## KISS goal loop

The runtime path is now `forge.loop` ([loop.md](loop.md)). The multi-stage
PO/architect/ingest/RED-GREEN lifecycle (`engine.py`, `roles.py`, `ingest.py`,
`docsindex.py`, `canary.py`, `gate.py` and the legacy CLI commands) stays in the
tree unchanged, but it is isolated: importing `forge.loop` does not import
`forge.engine`, `forge.roles`, `forge.ingest`, `forge.docsindex` or
`forge.canary`. The loop never dispatches the legacy lifecycle. It reuses only
the runner, classify, State schema v2, the atomic writers, the controller lock,
the sandbox, the adapters, the Git primitives, `gate.matches` and the smoke
fingerprint check. Old mission databases are never opened by the loop. A new
mission uses a fresh state root. This delivery deletes nothing.

## Not this gate

No operator TOML is created here. The product target must be a separate
ordinary Git repository with a real `.git` directory. This engine worktree is
not a valid `init` target. Codex `runtime_dir` must bind the vetted release
directory that contains both `codex` and `codex-code-mode-host`.
