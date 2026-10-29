# KISS goal loop (`forge.loop`)

The goal loop is the Forge v3 runtime path. It is an ordinary bounded loop:

goal → native writable agent → trusted gate and independent review → CAS commit
on a dedicated ref → durable checkpoint → next step.

It stops when a goal is accepted as DONE, when there is a concrete blocker, when
it must wait for quota, or when the step budget runs out. The agent owns product
decisions and runs the project's own build and test commands (for example
`cargo test`) inside its sandbox. The controller owns admission, execution,
retries, persistence, protected-path checks, sandboxed verification and
acceptance. There is no language-specific parser and no JUnit contract.

## Entry points

- `forge.loop.run_loop(config, goal_path, *, max_steps=1, admit, should_cancel=..., clock=...) -> int`
  is the only mutating entry point. `admit(account, profile, stage, now)` is
  required. It returns `ALLOW`, `wait(reason, recheck_at)` or `block(reason)`,
  and it is called in the same process immediately before every native launch,
  including resumes and reviewers. A missing or invalid verdict is BLOCKED and
  never counts as permission. A genuine `block(reason)` is written to the
  checkpoint as a terminal BLOCKED. Later invocations make no calls until a
  parent-authorized state action clears it.
- `forge.loop.run_with_capsule(config, goal_path, capsule_path, ...)` is the
  core of the trusted wrapper. It refuses a missing, unreadable or STOPPED
  capsule. It never takes the controller lock. After a run it re-reads the
  capsule and changes only `status` and `recheck_at`. A STOPPED status, a
  `stopped: true` flag or an existing `stopped_marker` file always wins.
- `forge loop-status --config PATH --goal FILE` prints the checkpoint and is
  read-only. It does not create state. There is deliberately no mutating
  `forge loop` CLI.

Exit codes: `0` DONE, `2` BLOCKED, `3` step budget used without DONE, `4`
LOCKED (controller-lock contention only; no checkpoint, capsule, admission,
probe or native call), `75` WAITING with a finite `recheck_at`, `130`
cancelled or STOPPED.

## Configuration

```toml
[loop]
writers = ["opus-medium", "sol-medium"]     # ordered, enabled profiles
reviewers = ["sol-medium", "opus-medium"]   # first one from a different family than the writer is used
branch = "forge/kiss-loop"                   # required; must already exist; must not be checked out anywhere
step_timeout = 2700
max_failures = 3
env = { CARGO_TARGET_DIR = "/work/target" }  # filtered; no HOME or secret-like names
```

The loop ref is never created or defaulted, and it cannot be the project
branch. A missing ref is BLOCKED before any call. So is a symbolic alias, a ref
checked out in `.git/HEAD` or in any `.git/worktrees/*/HEAD`, or a linked, bare
or unreadable Git layout. These ownership checks repeat before every writer
launch, every reviewer launch and every CAS, together with the expected-base
check. `exec.ro_binds` and `loop.env` go to the writer sandbox and to the
gate. The reviewer sees only a read-only export of the exact candidate.

## Durable state

The loop uses a fresh state root (State schema v3 for active loop invocations). Files:

- `loop/<goal-id>/checkpoint.json`: one current operation (`active`, saved
  before the runner starts) and at most one acceptance (`pending`, saved before
  CAS). It also holds the pinned writer and reviewer profile/SID, the expected
  accepted base, receipts and bounded history.
- `loop/<goal-id>/result.md`: a compact summary rebuilt from the checkpoint
  every time.
- `ws/<goal-id>/src`: the writer workspace. `gate-<n>` and `review-<n>` are
  immutable candidate exports. `rejected-<n>` keeps workspaces rejected for
  protected-path changes.

The goal file identity (SHA-256), the config file identity, the target and the
ref are pinned. If any of them changes, the run is BLOCKED and nothing is
written.

## Recovery rules

- A completed durable result is replayed, never launched again. Before any
  effect, its shape, its role schema, and its call row and input identity
  (profile, account, model, effort, session and the complete input, including
  empty images) are checked against the checkpoint. A returned SID must agree
  with the preset/resume SID, the row and any SIDs native stdout streams (grok
  streams none) before any effect.
  A corrupt or conflicting result is a durable BLOCKED. If the result
  file exists while the row is still running, the row is completed exactly as
  the runner would complete it. Account and profile effects apply once per call
  (`call_effects`).
- A prepared `active` call with no call row is launched once. A launching or
  running row with no result counts as possibly executed. The loop recovers it
  only by resuming the same profile and SID in a new linked call. The SID must
  be proven by the native session events in that call's own stdout, and it must
  agree with the call row. A preset session id alone is not enough. Without
  that evidence the run is BLOCKED (`resume_unproven`). The loop never falls
  back to a fresh session.
- A changed DONE candidate goes through the same pending → CAS → finalize path
  as progress. Exit 0 happens only after the ref holds that exact tested and
  reviewed commit. Before CAS and before finalizing, the gate log and the
reviewer result file must still exist, match their recorded hashes and link to
the recorded call and tree. Otherwise the pending acceptance is kept and the
run is BLOCKED. An unchanged DONE names the already-accepted commit. If CAS
  fails, or the ref matches neither the old head nor the candidate, the run
  never finishes as DONE.
- The ref must equal the expected accepted base before every writer launch,
  every reviewer launch and every CAS. Drift is BLOCKED and is never adopted.

## Acceptance

The workspace is snapshotted with trusted Git. Changes to protected paths are
rejected, including content, deletion and mode changes. In direct mode a rejection resets the
workspace from the accepted base in a fresh session and keeps the rejected
copy. The configured `commands.test` runs in the gate sandbox without secrets and
always without network. The legacy `[exec] gate_network` option is ignored on
this path. Only its real exit code counts. A compile error, a missing tool, a
timeout, or a "no tests" exit that is not zero all fail. A changed progress
step is accepted as a progress commit even if the gate fails, and it is not
DONE. The failing rc and the gate output tail go to the next writer prompt,
including after crash recovery. DONE requires gate rc 0 and an independent PASS from a different model
family on the same tree.

## Evidence classes

The tests in `tests/test_loop.py` use fixture native programs, injected
admission and injected clocks. They are SIMULATED. They run real bwrap, the
real runner, real Git and real SQLite, but they are not live provider evidence.
A native canary is a separate gate.

Before CAS and recovered finalization, gate/review receipts must bind the same
candidate commit/tree. Review input and result hashes bind the exact call and
PASS verdict. Missing, corrupt or unsupported receipts (including older receipts
without the input hash) durably BLOCK with pending evidence preserved; no repair
or receipt reconstruction is attempted.

## Optional Technical Lead

`loop.mode = "direct"` remains the default, including its historical progress
acceptance rules and writer/reviewer prompt bytes. `mode = "lead"` adds one
combined Technical Lead, followed by a bounded executor and an independent
reviewer in the same loop. The controller chooses no product work.

```toml
[loop]
mode = "lead"
leads = ["sonnet-high", "sol-high"]
writers = ["luna-xhigh", "haiku-high", "sonnet-high", "sol-high"]
reviewers = ["sol-high", "sonnet-high", "opus-high"]
branch = "forge/lead-scratch"  # provision an unchecked-out direct ref first
step_timeout = 2700
max_failures = 3
fix_rounds = 2                 # integer 0..2; separate from transport failures
native_children = "verified" # unverified routes still force helpers off
# For a Rust target, also configure vetted Cargo/rustup exec.ro_binds.
env = { CARGO_TARGET_DIR = "/work/target" }
```

The example uses default enabled profiles. `[loop]` is optional; `leads` and
`fix_rounds` require Lead mode. Unknown keys, booleans in integer fields,
strong-only executors, cheap Leads/reviewers and writers without a possible
other-family reviewer are rejected. Runtime admission and smoke checks still
apply to every call, including resumes. In Lead mode `max_steps` counts every
native parent launch; direct mode retains its writer-step budget.

| Eligibility | Runtime profiles | Roles |
|---|---|---|
| cheap | Luna, Haiku | executor |
| normal | Sonnet, Sol; Grok only when account admitted; Terra only after proof | executor, Lead, reviewer subject to capabilities |
| strong | Sol, Opus | Lead, reviewer |

This matrix describes eligibility, not a benchmark or a change to build-worker
selection policy. Sol retains both normal and strong eligibility. Grok's
existing account restriction remains in force.

Tracked contracts in `forge.prompts` are rendered by the actual preparation
path. Lead reads the current accepted export and docs, goal, constraints, last
task/test/review/commit receipt and unresolved handoff. It returns one bounded
`task`, one concrete `blocked` question, or an evidence-backed `done` decision.
A task specifies objective, in/out scope, invariants, acceptance, native tests,
docs impact and cheap/normal execution tier with rationale. Absolute paths,
traversal and escaping changed symlinks are blocked. A whole-repository scope
requires justification. Executor reads local docs, reproduces bugs with a
failing regression first, executes only that task and reports tests/checkpoint
or a concrete blocker.

Each task starts a fresh executor context. A gate failure or review FAIL
resumes the same proven executor SID, profile, model and effort, with at most
`fix_rounds` correction launches. The correction ordinal is durable before
launch. Quota waits consume no correction. Exhaustion returns once to the same
Lead with preserved rejected work; a smaller replacement starts fresh. Another
exhaustion blocks. Protocol/transport and unchanged-work budgets are also finite.
Scope/protected-path violations preserve the workspace and block.

Every accepted task, including executor `progress`, requires a passing native
gate and fresh other-family review of the plan, exact candidate, tests,
security, architecture and docs. Receipt hashes pin task/spec/base/commit/tree
and exact review input/result before CAS and recovered finalization. Accepted
tasks clear executor/reviewer pins; the Lead SID persists, with a fresh export
and rendered accepted-state receipt on resume. Lead `done` goes through a gate
and independent full-goal review of the unchanged accepted tree. It creates no
duplicate commit. Technical PASS is not owner/product acceptance.

## Quota metrics

Active loop invocations explicitly migrate SQLite v1/v2 to v3. Legacy callers
may retain schema v2. Observation begins after lock, identity, ref and active
authorization checks. There is no historical backfill or telemetry service.
`quota_events` and episode transitions share a transaction. Admission attempts
and call-derived event IDs support replay; missing metrics are repaired even
when ordinary `call_effects` already exist.

Admission WAIT and account cooldown record prestart deferral; provider/monitor
waits record midflight interruption. Pinned waits and monitor cancellation add
a fallback-avoided facet to the same episode. Explicit 5h, weekly and model
windows remain separate; generic quota denial is `quota_unspecified`.
Reader 429/missing windows are `unknown_reader`, never inferred exhaustion.
Auth, infrastructure and policy are separate, and policy creates no wait episode.
A process-start event is only a proposal. Matching episodes close on confirmed
successful native continuation, never on ALLOW, a reset estimate, an unrelated
role/account or a failed sandbox spawn.

Wait seconds intersect confirmed active spans; their union supplies total
quota walltime without counting overlaps twice. A crash or STOPPED gap is
capped at the last durable active observation. Status therefore reports partial
coverage and uncovered seconds rather than inventing mission-wide downtime.
Production uses an anchored monotonic clock within the invocation, with
persisted timestamp clamping across invocations and explicit clock uncertainty.

```bash
PYTHONPATH=src /usr/bin/python3 -m forge loop-status --config /operator/projects/demo.toml --goal /operator/goal.md --metrics --json
```

Default status JSON is still the checkpoint. `--metrics` adds counts, per-key
wait seconds, union time, observation period, active coverage and frequency
(distinct 5h episodes / confirmed admitted calls, plus episodes per covered
hour). Zero denominators are null. `--capsule PATH` adds read-only authorization
status. Status uses SQLite `mode=ro`; missing/old state returns an unavailable
reason without creating or migrating anything. STOPPED and lock contention do
not open observations.

REAL read-only usage check, 2026-10-26 22:09:57+01:00: Codex reported
`weekly_left=85% weekly_used=15% 5h_left=14% 5h_used=86%` (reader rc0).
This is account usage evidence, not runtime admission, wait history, or a live
Lead/child/Terra canary. No mission was launched to produce it.

SIMULATED accounting example: authorized span [0,100], account A's 5h episode
[10,100], account B's weekly episode [20,100]. Per-key waits are 90s and 80s;
quota walltime is their 90s union. Three repeated checks of A remain one episode.
One confirmed parent continuation gives a 5h frequency of 1/1; without one,
`per_call` is null. Test fixtures never constitute live provider proof.

## Native helpers and Terra gates

Every loop call carries a static, task-local native child policy, persisted in
active state and the call manifest. Unconfigured adapter calls retain legacy
argv. Unverified routes force helpers off: Codex disables both `multi_agent`
and `multi_agent_v2`, Claude denies `Agent`/`Task`, and Grok uses
`--no-subagents`. Home configuration is never rewritten. Mixed Claude modelUsage
keys fail model validation whenever the explicit policy applies.

No route is registered as child-verified in this implementation. Exact model,
effort, fanout=1, depth=1 and read-only enforcement must be proved from the
installed schema before any child probe; prompt requests alone grant nothing.
If arbitrary child overrides cannot be prevented, the route stays off. A later
quota-gated sandbox probe must demonstrate real spawn, separate context and
result-return with binary/settings/binds/native-schema fingerprints. Outcomes
are recorded separately per harness; Grok remains admission-blocked. Internal
help never replaces the outer independent reviewer.

The default Terra profile remains disabled `TBD/TBD/strong`. A separately
reviewed operator roster may register only exact `terra/codex/gpt-6.1-terra/low`,
family OpenAI, account Codex, tier normal, initially disabled. A `[[profile]]`
array replaces all defaults: retain every other profile the loop uses and never
add disabled Terra to role lists.

Terra state is currently `TERRA_DISCOVERED_REGISTRATION_PENDING`. Authenticated
catalog discovery is not honored-model inference. Before one live proof, an
independent other-family review must bind the helper/config diff and safety
tests. `doctor.terra_proof` uses existing runner/sandbox, a fresh trusted quota
callback, an exclusive durable attempt marker, one read-only call, children off,
a timeout at most 120s. Before consuming an attempt, it builds the real Codex
argv and checks exact model/effort pins, both child-disable flags, and an installed
turn-control option capped at four. No supported installed turn control means
pending with no marker or inference; current Codex has no such option. The runner
reuses exactly the checked argv. Observed model, effort, context and tools must
survive into the hashed result and match the proof; in-memory metadata alone
cannot verify it. Independent helper review and explicit admission remain required;
there is no authorization here to run a probe. An uncertain attempt is consumed,
not auto-retried. Ordinary multi-call Terra smoke is rejected.

A proof without actual native model/effort/context/tools remains pending and
cannot enable Terra. Current Codex output does not establish all those fields;
missing metadata is recorded as UNKNOWN, never derived from model text. Only
`TERRA_VERIFIED_NORMAL` with matching controller-owned evidence hashes,
discovery, account, binary/settings/sandbox pins and fresh proof can activate a
reviewed operator profile in a fresh state identity. Proofs expire after 24h or
fingerprint drift; selection, launch and recovery recheck them. A single-call
proof grants text/readonly only, never resume/image/children; Lead/executor
admission still requires separately proved resume. Actual provider denial is
`TERRA_UNAVAILABLE_EVIDENCE`; policy registration refusal remains pending.
No central model policy, worker pool, game, cron or mission is changed.
