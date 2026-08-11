# Forge

Forge is a non-commercial, MIT-licensed experiment in reliable AI agent orchestration. It turns a project brief into small, verifiable development tasks and coordinates specialized agents to implement them through a transparent TDD workflow.

## How it works

```text
product owner → planner → tester ↔ coder → reviewer
                    ↑                          │
                    └── weryfikator historyjek ┘
```

- Product Owner utrzymuje cel projektu i cienki, uporządkowany backlog historyjek.
- The planner creates small batches of focused tasks.
- The tester and coder iterate using explicit TDD states.
- A fresh, read-only reviewer approves changes or requests corrections.
- A story verifier checks observable outcomes and updates story lifecycle status.
- A ledger and checkpoints keep progress visible and recoverable.

See [docs/PIPELINE.md](docs/PIPELINE.md) for details.

## Choosing models per role

Each role has a default policy (role → level → provider). Per-machine choices —
which concrete model per task difficulty, and a fallback chain used when a
provider hits its limit or fails hard — live in `~/.config/forge/routing.json`
and can be clicked together in the GUI (`python3 -m forge.gui`), so switching
providers needs no commit. The GUI asks for a **model**; the CLI tool follows
from it, and a provider dropdown appears only for models reachable more than one
way (`gpt-5.6-luna` via Codex or the OpenCode bridge, `glm-5.2` via two OpenCode
providers). See [docs/ROUTING-I-FALLBACK.md](docs/ROUTING-I-FALLBACK.md).

Providers configured in `opencode.json` take their keys from `{env:NAME}`, which
resolves against the environment of the Forge process — so a shell started before
the key was exported, a desktop launcher or a systemd unit would otherwise fail
with `401 No API-key provided` in the middle of a role. Preflight closes that gap:
it reads the `*.env` files next to `opencode.json`, fills in whatever the routed
providers need, and aborts up front if a role has no usable endpoint left.
Explicit environment always wins over those files; `FORGE_ENV_FILES` overrides the
search (`none` disables it).

Claude Code is authenticated the same way. By default Forge links the operator's
`~/.claude/.credentials.json` into its isolated home, but that file carries a
single-use refresh token: two Forge instances (or one instance plus the IDE
extension) racing to refresh it kill the session for everyone. Set
`CLAUDE_CODE_OAUTH_TOKEN` — or `FORGE_CLAUDE_OAUTH_TOKEN` to give Forge a token
of its own — from `claude setup-token`, and the shared file is left out of the
picture entirely. Either way preflight reads the session state up front and
refuses to start a run whose roles have no working endpoint left. See
[docs/AWARIE-2026-08-11.md](docs/AWARIE-2026-08-11.md).

## Research direction

The project explores:

- reliable autonomous implementation from requirements,
- efficient context management across long-running tasks,
- quality gates and role isolation,
- routing work between different cloud models,
- hybrid cloud and local LLM workflows,
- reducing token cost while preserving implementation quality.

## Quick start

Requirements: Python 3.10+, Git and configured AI agent CLIs.

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m forge.orchestrate \
  --brief game.md \
  --project game \
  --max-tdd-rounds 8
```

Run the tests:

```bash
python3 -m pytest -q
```

## Community

Forge is developed as a private-time AI passion project by Jan Sochiera and Dominik Kuraś. Findings, practical examples and limitations will be shared with Sii Workers through project updates and a recorded presentation.

Feedback and experiments on non-confidential sample projects are welcome.

## License

[MIT](LICENSE)
