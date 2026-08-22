# Forge

Forge is a local orchestrator for long-running, high-quality software delivery. It keeps one
powerful product brain alive for the entire run, asks that brain for substantial batches of work,
and has three cheaper coding agents implement every batch independently. A reviewer chooses the
best candidate, the winner fixes the review findings, Forge commits and pushes it to the selected
branch, and a black-box tester reports observable product behavior back to the same brain session.

The controller does not decide product scope, quality, or completion. It executes the brain's
decisions, validates message contracts, retries failed processes, records evidence, and performs
the Git operations required to deliver the selected candidate.

## Pipeline

```text
brief.md
   │
   ▼
persistent brain (no repository and no tools)
   │ forge.run_batch(objective, success criteria)
   ▼
planner ──► plan.md with micro-feature checkboxes and validation commands
   │
   ├──────────────┬──────────────────┐
   ▼              ▼                  ▼
TDD coder     exploratory coder   classic coder
own worktree   own worktree        own worktree
same plan      same plan           same plan
   └──────────────┴──────────────────┘
                  │
                  ▼
reviewer compares code, tests, diffs, and validation evidence
                  │
                  ▼
winner fixes findings and may borrow concrete pieces from losers
                   │
                   ▼
commit ──► fast-forward selected branch ──► push
                   │
       ┌───────────┴───────────┐
       ▼                       ▼
white-box reporter      black-box tester
(short + long tests)    (public behavior)
       └───────────┬───────────┘
                   ▼
        compact product report ──► persistent brain
```

The loop ends only when the brain calls `forge.finish`. There is intentionally no independent
final verifier and no mechanical product-completion threshold.

## Design principles

- **One persistent brain session.** Forge always resumes its original provider session. Native
  context compaction may occur, but Forge never silently starts a replacement brain.
- **The brain cannot inspect the repository.** It runs in a separate state directory. OpenCode
  receives an empty/denied tool surface. Codex starts with user configuration ignored,
  shell, unified exec, apps/plugins, browser/computer, multi-agent, image, and web tools disabled,
  a read-only sandbox, and a contract gate that rejects any remaining tool event.
- **Large batches, not a micro-loop.** The brain requests a cohesive feature, refactor, bug batch,
  or product-level test effort. The planner expands it into implementation-sized checkboxes.
- **Real competition.** All three coders start from the same commit and complete the same plan in
  isolated Git worktrees. They do not see each other's work.
- **Uniform goal behavior.** Forge does not depend on provider-specific `/goal` implementations.
  A coder is resumed with the same session and an explicit reason while unchecked tasks remain.
- **Warnings are evidence, not product decisions.** Forge records stalls, timeouts, failed checks,
  and token use. A repeatedly hung or non-progressing coder is left as an incomplete candidate so
  the process can continue; Forge does not reinterpret the final brief.
- **Repository history is the delivery mechanism.** Only the selected candidate is committed. The
  target branch is fast-forwarded and pushed directly to `origin` when push is enabled.

## Requirements

- Linux or macOS with Git and Python 3.12 or newer.
- At least one authenticated supported agent CLI:
  - `codex` (GPT family)
  - `opencode` (GPT family, Grok 4.6, Qwen 3.8 Max, DeepSeek Flash/Pro, GLM 5.3)
- A clean target Git repository. Forge can bootstrap an unborn selected branch in a repository
  with no commits. When push is enabled the repository must have an `origin` remote.

Forge uses existing CLI authentication. It does not require API keys and has no runtime Python
dependencies.

## Install

Run directly from a checkout:

```bash
python3 -m forge ui
```

Or install an editable command in a virtual environment:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/forge ui
```

On Ubuntu, install the matching `python3-venv` package first if `python3 -m venv` reports that
`ensurepip` is unavailable.

The UI listens only on `127.0.0.1` by default and opens `http://127.0.0.1:8787`. It accepts exactly
the operational inputs Forge needs: target repository, delivery branch, product brief, model
selection for every role, a coder model pool, and whether to push. Closing the browser tab does
not stop Forge; use **Restart** in the control room after code changes. A live run requires
confirmation before restart. The control room loads the closed catalog from `/api/catalog` and
offers provider, model, and effort dropdowns instead of free-text selectors.

## Model selection

Every role uses this selector:

```text
provider:model[:effort]
```

Examples:

```text
codex:gpt-5.6-sol:high
opencode:gpt-5.6-luna:high
opencode:grok-4.6
opencode:qwen-3.8-max
opencode:deepseek-v4-flash-0731
opencode:deepseek-v4-pro-0813
opencode:glm-5.3
```

GPT-family models may run on Codex or OpenCode. Grok 4.6, Qwen 3.8 Max, DeepSeek Flash 0731,
DeepSeek Pro 0813, and GLM 5.3 run on OpenCode only. Catalog keys (`gpt-5.6-sol`) and provider
IDs (`openai/gpt-5.6-sol`) are both accepted. The fixed selections are `brain`, `planner`,
`reviewer`, `tester`, and `whitebox`. The three coder tactics still exist as `coder_tdd`,
`coder_explore`, and `coder_classic`, but the control room sends a coder model pool and Forge
draws those three assignments from it.

Before the first start, Forge pings every unique selected model with a one-word, no-tool prompt.
A quota, auth, or API failure stops the run during preflight instead of during the first expensive
role. Recovery does not repeat the probe.

The UI coder pool and the three CLI coder fields default to `codex:gpt-5.6-luna:high`. CLI
flags still override each tactic; `--shuffle-coders` randomly assigns those three models.

Use strong models for the brain and planner, medium models for review, white-box, and black-box
testing, and cheaper coding models in the coder pool. Every third cycle is a housekeeping batch.

## Command-line run

```bash
python3 -m forge run \
  --repo /path/to/product \
  --brief /path/to/brief.md \
  --branch main \
  --brain codex:gpt-5.6-sol:high \
  --planner codex:gpt-5.6-sol:high \
  --coder-tdd opencode:gpt-5.6-luna:high \
  --coder-explore opencode:grok-4.6 \
  --coder-classic opencode:qwen-3.8-max \
  --reviewer codex:gpt-5.6-terra:high \
  --tester codex:gpt-5.6-terra:high \
  --whitebox codex:gpt-5.6-terra:high
```

The three omitted coder selectors use the default `codex:gpt-5.6-luna:high`.

Add `--no-push` for a local-only run. Forge still commits and fast-forwards the selected branch.
Recover a failed run from its last checkpoint without wiping surviving worktrees:

```bash
python3 -m forge resume --repo /path/to/product --run-id 20260820-100909-19c10671
```

If the controller process fails or is interrupted after at least one batch was delivered, resume
the same brain session and run history after fixing the cause:

```bash
python3 -m forge recover --repo /path/to/product --run-id RUN_ID
```

Runs started in the UI expose the same recovery action as **Recover same run** when their
controller has stopped. If coding and validation finished before a review/provider failure, Forge
recreates the candidate worktrees from their binary patches and resumes at review instead of
spending tokens to implement the batch again.

## Artifacts

Every run is fully inspectable under:

```text
TARGET_REPO/.forge/runs/RUN_ID/
├── state.json
├── config.json
├── brief.md
├── events.jsonl
├── usage.jsonl
├── brain/
└── batches/
    └── 001/
        ├── objective.json
        ├── plan.md
        ├── review-bundle.json
        ├── review.json
        ├── black-box.json
        ├── black-box-evidence/
        ├── delivery.json
        ├── candidate-metrics.json
        └── candidates/
            ├── tdd/
            ├── explore/
            └── classic/
```

Each candidate directory contains every prompt and response, raw provider events, checkbox
progress, validation output, Git status, diffstat, a binary-safe patch, and its own `metrics.json`.
The batch-level `candidate-metrics.json` shows completion, review score, validation results, wall
time, warnings, and token use side by side.

Forge adds `.forge/` to the target repository's local `.git/info/exclude`; it does not modify the
product's `.gitignore`. It also locally excludes dependency/cache trees such as `node_modules`,
virtual environments, Python caches, and TypeScript build-info files so a missing project
`.gitignore` cannot turn generated dependencies into a huge review patch or delivery commit.

Provider-reported usage is normalized to input, cached input, output, and reasoning tokens. Some
providers or custom model endpoints may omit usage; Forge then records zero rather than inventing a
number. Subscription-plan limits and monetary cost are provider concerns and cannot always be
derived from tokens.

## Failure behavior

- A transient process crash is retried with an explicit explanation. Deterministic CLI errors,
  provider usage limits, and agent timeouts are not immediately retried.
- An invalid brain, reviewer, tester, or planner response receives contract feedback and is resumed.
- A coder with repeated turns that do not change plan progress is marked `stalled`; its artifacts
  remain available and the other candidates continue.
- If all candidates produce no code, the run fails visibly instead of manufacturing progress.
- Forge refuses to start when a selected model fails the no-tool preflight ping, when the
  repository is dirty, or when delivery would not be a fast-forward. A failed push is reported
  without rewriting history.
- Pause, resume, and cancel take effect at safe phase/agent-call boundaries. They do not kill an
  active provider process in the middle of a filesystem operation.
- Planner commands marked `[winner-only]` are omitted from the three candidate validation passes
  and run once after review. This is intended for live, external, destructive, or long acceptance
  checks. Their recorded result is passed to the winner and black-box tester so neither repeats an
  unchanged expensive timeout.
- A failed run keeps candidate worktrees. `forge resume` continues from the last checkpoint:
  review reuses existing candidates, a single dead coder restarts alone, and the whole cycle is
  reset only when the worktrees are gone and cannot be restored from patches.

## Development

```bash
python3 -m pytest
python3 -m compileall -q forge
```

The test suite performs the complete orchestration flow with deterministic fake agents and real
temporary Git worktrees, commits, and fast-forward delivery. It does not consume model tokens.
