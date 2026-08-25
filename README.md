# Forge

Forge is a dependency-free local controller for continuous, Product Owner-led software delivery.
It turns a product brief into durable ten-iteration sprints, keeps product decisions inside agent
roles, and owns only execution, retries, persistence, contract checks, and safe Git delivery.

Forge does not declare a product finished. It keeps running sprints until an operator pauses or
cancels it, or until a concrete blocker puts the run in `stalled` or `failed` state.

## Sprint loop

```text
committed product snapshot
          │
          ▼
fresh Product Owner ── inspect public behavior ── create deep backlog
          │
          ▼
┌──────────────── one accepted iteration ────────────────┐
│ planner ──► coder ──► reviewer ──► unified tester      │
│               ▲          │                │             │
│               └── reject ┴────────────────┘             │
│                                      │ accept           │
│                                      ▼                  │
│                       exact-tree commit and delivery    │
└─────────────────────────────────────────────────────────┘
          │
          ▼
F F F F C F F F F C ──► fresh Product Owner ──► ...
```

`F` is a feature iteration and `C` is cleanup-only. A slot advances only after the reviewer and
tester accept the same implementation fingerprint and Forge safely delivers it. Rejected work
returns to the same coder context, then passes through review again before any retest.

After ten accepted iterations Forge discards all role contexts and starts a fresh Product Owner
visit against the newly delivered product.

## Roles and gates

- **Product Owner** works from a disposable committed snapshot with inspection tools. It launches
  public workflows, may capture screenshots or other evidence, ignores internal code quality, and
  creates at least twelve stories representing at least one hour of work. The backlog must cover
  all eight feature slots and both cleanup slots, with reserve work left over.
- **Planner** selects the highest-priority ready story of the controller-required type and converts
  it into one immutable task plan. It must preserve every Product Owner acceptance criterion
  verbatim and provide bounded validation commands and public checks.
- **Coder** owns implementation and correction rounds in one recoverable Git worktree. It cannot
  commit, push, switch branches, alter refs, or access another worktree.
- **Reviewer** is a pragmatic release gate. Serious correctness, completeness, security,
  regression, and test defects block. Taste and small polish issues become non-blocking nits for a
  later cleanup iteration.
- **Unified tester** combines white-box and black-box acceptance in a disposable copy. It receives
  the accepted review and mechanical validation, exercises every public check, and must provide
  task, white-box, and public evidence.

Review and test reports must cover every planned task exactly once and name the exact prospective
Git tree fingerprint. Empty evidence, stale fingerprints, failed validation, skipped public checks,
or unresolved blockers cannot pass the controller contracts.

Cleanup iterations may map accumulated nits to explicit tasks. A nit is resolved only after that
cleanup plan passes the same review and test gates; nits never block a feature iteration.

## Durability and Git safety

Forge uses one implementation branch and worktree per run. It fingerprints the complete prospective
tree, including staged, unstaged, untracked, binary, and already committed changes. Preparing the
delivery commit does not change that identity. Before delivery Forge verifies all of the following:

- reviewer and tester accepted the current tree;
- the commit tree equals the accepted tree;
- the implementation is exactly one non-empty commit over its recorded base;
- the target checkout is clean and has not drifted;
- delivery is a fast-forward.

An OS-backed advisory lock in the canonical Git common directory gives one Forge process exclusive
ownership of the repository for the complete run or recovery call. A second CLI process, duplicate
web recovery request, or another run targeting the same repository fails before mutating run state,
worktrees, or refs.

Delivery is idempotent. Recovery reconciles crashes before commit recording, after commit creation,
after target integration, and during finalization without replaying an accepted iteration or making
a duplicate commit.

Every role transition is persisted. A process restart resumes Product Owner correction, planning,
coding, review, testing, delivery, or finalization at its durable phase. Edits left by an interrupted
coder are fingerprinted and reviewed instead of being overwritten. Repeated no-progress rounds,
exhausted revision budgets, unavailable required stories, and external blockers stop as `stalled`
instead of creating an unbounded agent loop.

Runs created by the former competitive architecture remain visible as schema-version-1 artifacts,
but cannot be resumed by the sprint controller.

## Requirements

- Linux or macOS, Git, and Python 3.12 or newer.
- At least one authenticated supported agent CLI:
  - `codex` for GPT-family catalog models;
  - `opencode` for GPT, Grok, Qwen, DeepSeek, Gemini, Kimi, and GLM catalog models.
- A clean target Git repository. Forge can initialize an unborn selected branch. Push-enabled runs
  also require an `origin` remote.

Forge uses existing CLI authentication, has no runtime Python dependencies, and does not require
API keys in its configuration.

## Run the control room

```bash
python3 -m forge ui
```

Or install it in a virtual environment:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/forge ui
```

The UI listens on `127.0.0.1:8787` by default. It configures the repository, branch, brief, one
model per role, an optional failover model, and push behavior. Closing the browser does not stop a
run. Pause, resume, cancel, and same-run recovery are available from the run detail view.

External reviewer or tester blockers may be retried in place after the external condition changes.
Deterministic stalls such as exhausted revision limits, repeated no-progress rounds, or a missing
required story are intentionally not recoverable in place; change the product input or start a new
run rather than repeating the same bounded phase.

Forge intentionally uses exactly one coder. The previous three-coder tournament has been removed;
the selected coder keeps its session across correction rounds for one iteration, then Forge resets
that context before the next accepted slot.

## Command-line run

All model flags have catalog defaults and may be overridden independently:

```bash
python3 -m forge run \
  --repo /path/to/product \
  --brief /path/to/brief.md \
  --branch main \
  --brain codex:gpt-5.6-sol:high \
  --planner codex:gpt-5.6-sol:high \
  --coder codex:gpt-5.6-luna:high \
  --reviewer codex:gpt-5.6-terra:high \
  --tester codex:gpt-5.6-terra:high
```

Use `--no-push` to deliver only to the local branch. Recover an interrupted run in place:

```bash
python3 -m forge resume --repo /path/to/product --run-id RUN_ID
```

`forge recover` is a compatibility alias that reloads the same durable run artifacts. Foreground
commands exit with status `0` after an operator pause or cancellation and `1` after `failed` or
`stalled`; continuous runs otherwise keep executing.

Selectors use `provider:model[:effort]`. The control room exposes the closed catalog, including
Codex/OpenCode GPT models and OpenCode-only Grok, Qwen, DeepSeek, Gemini, Kimi, and GLM models. Before
the first sprint Forge probes each unique model once. A usage-limit failure can move a role to the
configured backup or another healthy selected model without changing the sprint contract.

## Artifacts

Durable evidence lives under `TARGET_REPO/.forge/runs/RUN_ID/`:

```text
state.json
config.json
brief.md
events.jsonl
usage.jsonl
product-owner/visit-001/{attempt-*,decision.json,evidence/}
backlog/revision-001.json
sprints/001/summary.json
sprints/001/iterations/01/
  plan.json
  planner/attempt-*
  coder/round-*.{json,patch}
  review/round-*.json
  tester/round-*.json
  tester/evidence-round-*-*/
  delivery-intent.json
  delivery.json
  acceptance.json
```

Disposable snapshots and worktrees live in the user state directory, outside the target checkout.
Forge adds only local exclusions to `.git/info/exclude`; it does not modify the product's
`.gitignore`.

## Development

```bash
python3 -m pytest
python3 -m compileall -q forge
```

The tests use deterministic fake agents and real temporary Git repositories. They cover the fixed
sprint schedule, role gates, correction loops, evidence contracts, no-progress stalls, context
reset, complete-tree fingerprints, and recovery across agent and delivery crash windows without
consuming model tokens.
