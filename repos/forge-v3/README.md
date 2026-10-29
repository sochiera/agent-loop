# Forge v3

Local deterministic product-development controller. Distribution name
`forge-agent-orchestrator`, version 3.0.0. The importable module and console
script are both `forge`. This tree is the engine. It does not contain an
operator project, a game, or a deployment.

The controller schedules work. Native CLIs (Codex, Claude, Grok) do content
work only inside bubblewrap. There is no host fallback. Commands below are the
supported surface. There is no `ui`, `swarm-run`, `swarm-resume`, or legacy
policy command.

## Provenance

Engine sources were imported from the pinned forge-next commit
`9df17d442b50deeca6a30086735da125519cb6ff` (author Jan Sochiera). Delivery
fingerprint `1c618df6b02303a265c381658e6b6c40de79dab3f9ced7bddd68e9b271218aa1`.
Capsule: `/home/jan/.hermes/cache/scratch/forge-next-grok-init/capsule.md`.
That delivery had no standalone license file and no license metadata. This
repository keeps the existing MIT `LICENSE` (Copyright (c) 2026 Jan Sochiera)
and ships it as package metadata. `AGENTS.md` is unchanged repository guidance.

Parent history is agent-loop `fb00771aecc700f765b0b2f3049a5d35282fc54f`. The
previous root `forge/` package, its tests, `deploy/`, and `briefs/` remain in
that history only. Current authority for this migration is
[docs/migration-v3.md](docs/migration-v3.md). Imported plans under
`.hermes/plans/` and reports under `docs/canary/` are historical source
generation records, including an older 34/34 canary that is not this tree.

## Goal loop

The runtime path is the bounded KISS goal loop in `src/forge/loop.py`. See
[docs/loop.md](docs/loop.md). A trusted wrapper calls
`forge.loop.run_loop(..., admit=...)` in the same process. Quota admission is
required, and the loop accepts commits only on a dedicated, preexisting,
unchecked-out ref configured in `[loop]`. `forge loop-status --config PATH --goal FILE` is a read-only
view. There is no mutating loop CLI. The batch lifecycle commands below are
legacy. They are isolated and frozen, and the loop does not use them.

## Commands

`forge --version` and `python3 -m forge --version` print `forge 3.0.0`.
`forge --help` lists:

- `loop-status --goal FILE` — read-only goal-loop checkpoint (no state is created)
- `config-check` — parse one operator TOML and report `runtime_verified: false`
- `init` — require an existing committed branch and a real `.git` directory, then write the docs skeleton as a local commit
- `doctor` — bubblewrap probe; `--smoke` runs native capability calls
- `run` — legacy controller batches; `--max-batches N` bounds validation; `--until-idle` returns 75 instead of sleeping through waits
- `status` — optional `--json`
- `cancel` — `--task ID` or `--all`
- `unblock ID --note TEXT` — optional `--fresh-session`
- `satisfied --note TEXT` — owner satisfaction only; an empty queue is not satisfaction
- `evidence ID`
- `canary --workdir PATH` — optional `--max-batches` and `--verify`; creates an isolated config and is not a read-only probe

Project commands require `--config PATH` before or after the subcommand. Help
and version do not create project state. This repository does not author an
operator config. Do not target this engine worktree: `init` rejects a linked
worktree (`.git` file), a bare repository, and a symlinked `.git`. The product
must be a separate ordinary repository with a real `.git` directory.

## Operator config

Accepted file: one regular, owner-owned, not group- or world-writable TOML
directly under `$XDG_CONFIG_HOME/forge/projects/<slug>.toml` (default
`~/.config`). Unknown keys are rejected. State defaults to
`$XDG_STATE_HOME/forge/<slug>` (default `~/.local/state`). Use a dedicated
slug. Do not reuse a legacy state directory.

Required key: `target_repo`, an existing absolute directory. Optional root
keys: `branch` (default `main`), `sources` (default `docs/source/**/*.md`),
`protected`, `test_paths`, `parallel` (only 1), `commands`, `exec`,
`accounts`, `harness`, `budgets`, `timeouts`, `max_turns`, and `[[profile]]`.
Source Markdown and images must already be committed in the target. Commands
are trusted argv arrays: `test`, `test_one`, `lint`. Only `test_one` accepts
`{node}` and `{junit}`. Default tests are pytest. A supplied `[[profile]]`
array replaces the defaults; enumerate the full roster and leave Terra
disabled unless it has a real model and effort.

Default profiles stay Luna xhigh and Haiku high (cheap), Sol high (normal and
strong), Sonnet high and Grok high (normal), Opus high (strong), Terra
disabled. Override Sol, Sonnet, Grok, and Opus to medium in operator config
when that roster is required. This engine does not read global model policy.

## Runtime and toolchain binds

Develop and test this engine with `/usr/bin/python3` (3.12 or newer; 3.14.4 on
the integration host), Git 2.53.0, and bubblewrap 0.11.1. `PATH`'s `python3`
may be a different interpreter. Runtime dependencies are none. Python `>=3.12`.

Every native call and every gate command uses `/usr/bin/bwrap` with a clean
environment and sandbox `PATH=/usr/bin:/bin:/opt/forge-native/bin`. Gate
network is off unless `exec.gate_network` is set. Host `PATH` and an activated
venv are not visible inside the sandbox. Vetted toolchains outside `/usr` need
explicit `exec.ro_binds`.

Default auth binds, mounted only for the harness in use:

- Codex: `~/.codex` read-write
- Claude: `~/.claude` and `~/.claude.json` read-write, `~/.local/share/claude` read-only
- Grok: `~/.grok` read-write

Observed integration-host binaries, recorded as path labels, not as a launch
authorization:

- Codex: `~/.codex/packages/standalone/releases/0.159.3-x86_64-unknown-linux-musl/bin/codex`
- Codex sibling helper in that same directory: `codex-code-mode-host`
- Claude: `~/.local/share/claude/versions/2.1.287`
- Grok: `~/.grok/downloads/grok-1.0.52-linux-x86_64`
- Node: `~/.local/share/forge/toolchains/node-v22.22.2-linux-x64/bin/node`

`harness.codex.runtime_dir` must be that vetted release directory, not the
`codex` executable alone. Mounting only the executable omits
`codex-code-mode-host`. The same rule applies to any harness whose binary
needs sibling files: set `runtime_dir` to the directory that contains the
executable. Rust or other gate toolchains that are not on the sandbox `PATH`
need their own `exec.ro_binds` entries. Replacing the default pytest argv does
not make a non-pytest stack a bug RED/GREEN parser; that parser expects
pytest-like `.py` node selectors and an executed assertion-failure JUnit file.

`doctor` and `doctor --smoke` write state and may call native CLIs. They are
not part of engine packaging verification. Model availability and quota are a
separate gate.

## Layout

Package code lives in `src/forge`. Tests live in `tests/`. Install from a
wheel or an editable install of this tree so `forge` resolves to the src
layout, not a legacy root package.

Optional Technical Lead mode keeps product planning in one combined Lead and
adds bounded task execution, same-session repairs and different-family review
to the existing loop. Direct mode stays the default. See [the Lead config,
role/model matrix, quota accounting and proof gates](docs/loop.md#optional-technical-lead).
`loop-status --metrics --json` is additive and read-only. Native children and
Terra remain disabled until their separate, actual capability proofs; passing
fixture tests does not complete native canary or independent code-review gates.
