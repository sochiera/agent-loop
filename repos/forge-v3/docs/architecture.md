Forge v3 engine notes. Migration authority is `docs/migration-v3.md`. Reports under `docs/canary/` are historical source-generation evidence.

# Forge architecture

Forge is a standalone local Python CLI with zero runtime Python dependencies.
P0 implements configuration validation and persistence primitives. P1 adds the
enforced bubblewrap boundary, sandboxed native runner, Codex/Claude/Grok adapters,
the total failure classifier and `forge doctor`. P2 adds immutable candidate
commits, deterministic gates and recoverable local CAS integration. Engine
scheduling and lifecycle are implemented by P3; P4 implements rich source
content, native role contracts and initialization. P5 adds disposable native acceptance tooling;
its observed BLOCK is recorded in docs/canary/2026-10-23.md. No configured profile is evidence
of model availability; a live native smoke record (P5) is required for eligibility.

`forge --help`, `forge --version`, `forge config-check`, `forge doctor`,
`forge run`, `forge status`, `forge cancel`, `forge unblock`, `forge satisfied`
and `forge evidence` dispatch to their implementations. Project commands require explicit trusted `--config PATH`; `canary` creates
its isolated operator configuration beneath the supplied workdir. `run` acquires the project lock,
recovers unfinished work, and executes the scheduler with native content roles.
`init` records the initial docs skeleton through a local CAS commit. `canary`
dispatches the disposable runner and existing-evidence verifier. `run` has no default batch cap; an explicit
`--max-batches N` is a validation bound. `python3 -m forge` uses the same CLI.

## Trusted configuration

Only an explicit regular operator-owned TOML file directly under
`$XDG_CONFIG_HOME/forge/projects/<slug>.toml` (default `~/.config`) is accepted.
Group/world writable files are rejected. The canonical file must be outside the
canonical target directory. Current-directory and target config discovery do not
exist. Unknown keys, wrong types, invalid counters, unsafe relative source/test
patterns, duplicate profiles and placeholder mistakes are rejected. Immutable
configuration includes argv arrays; there is no shell command execution in P0.
The operator must protect the config directory and its ancestors against replacement.

Minimal file: `target_repo = "/absolute/path/to/existing/repository"`.
HEAD validation belongs to init in P4. Config validation checks the directory only.
Defaults include protected source/intent/ADR paths, pytest commands, parallel=1,
easy budgets (2 plan, 2 implement, 2 fixes), hard (2, 2, 3), one escalation with
2 fixes, 2 role attempts, 1 protocol retry, 2 profile strikes/rebases/stall batches.
Timeouts and max_turns follow the approved configuration; nested
`[timeouts.max_turns]` or root `[max_turns]` is accepted, never both.
`{node}` and `{junit}` are allowed only in `commands.test_one`; later gates must
validate substituted node containment and choose junit paths themselves.
A missing junit placeholder disables bug work in the later engine.

Runtime defaults in order: Luna xhigh and Haiku high (cheap), Sol high
(normal and strong), Sonnet high and Grok high (normal), Opus high (strong),
Terra TBD disabled (strong). Accounts are shared per harness by default.
Effort is independent from tier; explicit medium profiles are supported except
Haiku requires high. A supplied profile array replaces defaults; it never implies
smoke eligibility. Terra cannot be enabled with a TBD model/effort. Actual routing
requires exact binary/model/effort/bind smoke and capability checks in P1/P3.
Build author policy is external metadata and is never loaded by the product.

## Durable state contract

`State(root)` creates `forge.db` using WAL, foreign keys and synchronous=FULL.
Schema version 2 adds idempotent call effects and per-run profile counters;
version 1 migrates transactionally with existing rows preserved; unknown versions and nonempty unversioned DBs
are rejected. A transition uses `with state.transaction() as db:` to acquire
BEGIN IMMEDIATE and commit once; any exception rolls back. Nested transactions
are rejected. SQL is available for later phases without introducing an ORM.

The schema includes runs and pid/start ownership, batches/role attempts, tasks
with separate budgets, dependencies, original/escalated sessions, RED and candidate
commits/trees, preserved work, block records and timed waits; calls record account,
pid/pgid/start, preset/input/output SID, configured versus reported model, effort,
raw artifacts and outcomes. Additional tables hold shared-account waits,
run-disabled profiles, cancellation requests, exact pending/done CAS candidates,
explicit owner satisfaction, smoke fingerprints/capabilities, handoffs, unblocks
and artifact provenance. Flexible metadata/JSON carries later role/gate evidence.
The schema stores these contracts; P0 does not implement their transitions.

One controller holds `controller_lock(root)` for its entire lifetime. Nonblocking
flock refuses another controller; its fd is non-inheritable and the lock inode is
never unlinked. Short operator request transactions later need not take this lock.
State construction alone does not claim controller ownership.

`atomic_write`/`atomic_json` write a same-directory mode-0600 temporary, fsync it,
replace the destination, then fsync its directory. Parents must already exist and
be controller-owned; callers choose paths and cannot pass target-controlled roots.
Only after success may a transaction reference the artifact. A post-rename failure
may leave a complete orphan, which later recovery must ignore/reconcile.

## Execution boundary (P1, `sandbox.py`)

One mode only: every native call and gate command runs under `/usr/bin/bwrap
--unshare-all [--share-net] --die-with-parent --new-session --clearenv` with fixed
ro `/usr`, `/etc`, usr-symlinked `/lib`, `/lib64`, `/bin`, `/sbin`, fresh
`/proc`, `/dev`, tmpfs `/tmp` and tmpfs `/home` plus an empty `$HOME`. Only
HOME/PATH/LANG/TERM are set; extra env names that look like keys/tokens are refused.
Network calls additionally ro-bind `/run/systemd/resolve` because this host's
`/etc/resolv.conf` links there (host layout fact, no secrets in it). Gate calls get
no network unless `exec.gate_network`, and HOME=`/tmp/home`.

Mount classes, each validated on canonical (`realpath`, strict) sources with
component-wise overlap tests, never string prefixes:
- work: exactly `state/ws/<task>/(src|gate-N|review-N)` → `/work` (rw only when
  the role writes); input: exactly `state/calls/<id>/in` → `/in` ro.
- executable: the resolved native file → `/opt/forge-native/bin/<harness>` ro, or an
  operator `harness.<h>.runtime_dir` → `/opt/forge-native/runtime/<harness>`; the
  runtime dir may not contain/overlap protected roots or credential-named files.
  The binary may lie inside the in-use harness root (the single allowed overlap).
- harness: only the operator's in-use `binds_rw`/`binds_ro` leaves, at the same
  path in the empty HOME; other harnesses' leaves stay protected.
- toolchain: `exec.ro_binds` for gates, ro, disjoint from every protected root and
  from work/input.
Protected roots: state, operator config root, target repo, every harness credential
leaf, every hidden top-level HOME entry (`~/.ssh`, `~/.gnupg`, the coordinator and
cloud credential dirs, …) except the `.local` container, plus `~/.local/state` and
`~/.local/share/keyrings`. `/`, `/home`, HOME, state and config roots and their
ancestors are always rejected. Destinations may not overlay fixed system paths,
`/work`, `/in` or `/opt/forge-native`, or each other. `revalidate()` re-resolves
every source and compares (dev, inode) immediately before spawn; any mismatch is
`SandboxError` → `env_block`. There is no host fallback anywhere.

`forge doctor` runs a real adversarial probe in bwrap for read-only agent, writable
agent and gate layouts: protected paths (state db, calls, config, every protected
root, real HOME secrets) must be absent, no PID outside the namespace may be visible,
env must be clean, `/in` unwritable, `/work` rw/ro as requested, and network down
when not shared. The record is `state/smoke/sandbox.json`; failure → exit 2.

## Native calls (P1, `runner.py`, `adapters/`, `classify.py`)

`run_call` order, each durable before the next: calls row `launching` (preset SID)
→ manifest + argv sha → `Popen(bwrap…, start_new_session=True)` → pid/pgid/
`/proc/<pid>/stat` starttime, `running` → stdout streamed to `calls/<id>/stdout`,
`session_out` written on the first SID event (or before launch when preset) →
`result.json` atomically → row `done` with outcome. Timeout/cancel: `killpg`
TERM, 10 s, KILL. Controller death kills bwrap (`--die-with-parent`), which tears
down the whole PID namespace (tested with SIGKILL).

Adapters pin model and effort on every initial and resumed argv:
- Codex 0.159.x: `exec -m M -c model_reasoning_effort="E"
  --dangerously-bypass-approvals-and-sandbox --json --skip-git-repo-check -C /work
  [--image /in/…] -` (resume: `exec resume … SID -`). Real schema: SID =
  `thread.started.thread_id`; final text = last `item.completed` `agent_message`;
  success needs `turn.completed` (usage). Model is not reported → “configured, not
  provider-confirmed”. `--skip-git-repo-check` is needed because read-only roles get
  plain `git archive` exports.
- Claude 2.1.x: `-p --model M --effort E --output-format stream-json --verbose
  --max-turns T --permission-mode bypassPermissions (--session-id UUID | --resume
  SID)`; prompt on stdin; images as base64 content blocks with
  `--input-format stream-json`. The `result` event shape is confirmed from real
  captures; init must confirm the preset SID; `modelUsage` must contain M.
- Grok 1.0.x: `--prompt-file /in/prompt.md | --prompt-json BLOCKS`, `--model M
  --reasoning-effort E --output-format json --max-turns T --cwd /work
  --always-approve (--session-id UUID | --resume SID)`. The real JSON object
  (`text`, `stopReason`, `sessionId`, `usage`, `modelUsage`) is parsed; observed
  model key `grok-4.7-build` counts as `grok-4.7`. The SID is preset (not late).
  `streaming-json` (ACP) has no real capture and is deliberately unused.
  Image `--prompt-json` payloads above 100 KiB are refused (argv limit).

`classify()` implements §5.3 rows 1–14 in order (`task_fail()` is row 15).
Diagnostics are matched only on stderr and native error fields, never on a
successful answer; quota waits use a parsed reset time or 15 min·2^k (≤ 4 h),
auth 1 h, network 10 min·2^k (≤ 2 h). Deterministic failures disable a profile or
the sandbox route instead of waiting.

`forge doctor --smoke` (live; first real run is P5) runs text, resume-with-write,
read-only and 64×64 quadrant-PNG image smokes per enabled profile, each in bwrap,
and stores `smoke/<profile>.json` plus the `smoke` table row pinning binary
realpath/version/sha256, model, effort, binds, runtime dir, per-step outcome/SID/
model and granted capabilities. Doctor exit codes: 0 pass, 1 partial, 75 all
failures are waits, 2 sandbox route blocked.

P1 tests run the boundary for real (bwrap reads/writes, EROFS, no network, env,
SIGKILL PID-namespace teardown, timeout/cancel group kill). Native stand-ins in
tests are FIXTURE scripts mounted at the native alias and reproducing captured
event shapes; they verify plumbing, never model capability.

## Phase boundaries and verification

P1 adds sandbox/runner/adapters/classifier/doctor. P2 adds controller git operations,
gates and CAS. P3 adds deterministic scheduling, sessions, waits, cancellation and
recovery. P4 adds rich ingestion/roles/docs/index/init. P5 supplies real sandbox
and native easy/hard canary evidence plus independent review and final delivery.
No scheduling or satisfaction decision belongs to an LLM. Independent review must
use a fresh different-family session and the exact tested candidate tree.

P0 tests directly check AC-01 (standalone/no forbidden integrations), AC-16 (all
runtime tiers and no build-policy dependency), AC-22 (separate-process flock,
rollback/reopen durability, atomic file ordering), AC-36 (zero runtime deps).
They also exercise schema ownership/session/CAS fields. Git operation allowlisting
and target isolation cannot be proved until those modules exist in P1/P2.
P0 tests are BUILD verification on developer-authored code, not an isolated runtime
controller running untrusted worker tests. See [ADR-0001](adr/0001-execution-boundary.md).


## P2: immutable candidate and local acceptance

`gitops.Git` owns the operator-trusted target GIT_DIR and a fresh private index
per snapshot. Every git subprocess has a fixed cwd outside the workspace, clean
environment, disabled hooks/fsmonitor and an operation allowlist. Snapshot and
merge attributes come from an empty controller tree, so agent attributes cannot
launch clean/merge drivers from trusted git config. Active target `info/attributes`
or `info/exclude` rules block configuration; global attributes/excludes and
automatic newline conversions are disabled for exact content snapshots. No controller
command executes an agent clone's git. Workspace snapshots copy content to a
controller leaf while excluding every `.git` entry before `read-tree/add/write-tree`;
this also prevents nested repository metadata from being inspected. Gitlinks and
archive attributes that alter exported content fail closed. Plain local clones use
`clone --no-hardlinks --no-checkout` and a controller-written minimal config with
no origin; files are populated from the trusted export. No checkout, push or
remote mutation occurs in the target.

A `Candidate` identifies commit, tree, base and preservation ref. Commit identity
and snapshot timestamps are fixed before gate/review. `save_candidate` persists
the ref and object IDs; orphan refs after a crash are safe to ignore and never
accepted automatically. Target INDEX and working files are not refreshed when
CAS moves the branch: accepted state is the branch commit, not its host checkout.

`gate.Gate` exports the candidate to a new `gate-N` leaf, checks protected sources,
intent and ADRs, requires every affected documentation target to change, and
requires Plan/Changes/Tests/Docs/Evidence in the changed task document. An ADR
exception requires a new path and the exact controller-approved blob ID; it never
permits rewriting an old ADR. Sources outside the defaults must be passed using
`Gate(..., sources=config.sources, protected=config.protected)`.
Operator command argv arrays are the only executable commands. Lint (when
configured) and tests run in the P1 bwrap boundary without credentials or network
by default. Command manifests, stdout/stderr, exit code and elapsed time are
persisted under calls, with durable launching/running/done rows and process
identity for subsequent recovery; there is no unsandboxed fallback. Tests may create ignored
outputs, but a fresh index snapshot must still equal the candidate tree.

For bugs, `red` requires a test-only checkpoint and changed test files for each
declared node. Each trusted `test_one` must contain both node and JUnit
placeholders. It executes the checkpoint against the unchanged base implementation
and accepts only rc 1 with a matching executed assertion-failure testcase, no
errors and no skips. Evidence is durable before `require_red_before_fix` permits
the next engine fix launch (P3 wires that ordering). GREEN requires byte-identical
RED test blobs and the same nodes passing with rc 0 and no failure/error/skip.
A missing node, zero tests, collection/import/setup failure or test already
passing on the checkpoint cannot establish RED.

Review material is a separate fresh export. P3 supplies it to the native runner
with `writable=False`. `review` checks a successful fresh recorded review call,
the bwrap read-only work mount, a new SID, different trusted profile family from
all implementers, exact tree, all nine concrete checklist items, no unresolved
findings and unchanged exported content. A checklist item is `pass` or an object
with `status="n/a"` and a nonempty reason. Family identities and doc targets are
controller inputs, never values accepted from model output. Native admission,
role routing and actual model capability remain P3/P5 responsibilities.

Gate and review receipts live under controller-only evidence with SHA256 entries
in SQLite. `Integrator.accept` requires both exact-candidate receipts. Under the
project controller lock, BEGIN IMMEDIATE serializes cancellation with the pending
commit row. A cancel before pending prevents acceptance; after pending the exact
commit is already accepted and recovery completes it. CAS uses the recorded old
head. The same commit/tree is accepted when main is unchanged. Git notes record
the sandbox and gate/review receipt paths and hashes idempotently.

Main movement uses `merge-tree --write-tree` with the recorded base, preserving
both branch and candidate refs. Conflicts or exhausted rebase budget write a
BLOCKED record. A clean merge creates a new preservation ref and commit on moved
main and requires new gate and fresh independent review receipts. No old receipt
can authorize the new tree. The same transaction records a durable rebase intent.
A pending row stays the acceptance point, but recovery never replays its
superseded candidate; only the re-gated merged candidate replaces it at accept.
The controller then replaces the task
workspace with a fresh clone of the merged commit, moving the complete old
workspace (ignored files included) to a preserved sibling, verifies the clone
snapshots to the merged tree and sets GATING, so a later fix round edits both
sides; recovery replays an interrupted intent first and never re-rebases or
spends rebase budget twice. Pending recovery retries the same CAS or marks done
when the commit is already in main history, including an external descendant
created after CAS. Other movement requires reconciliation and renewed evidence.
SIGKILL tests cover candidate ref/durable ID, pending row and CAS boundaries.
Controller writer/process recovery remains P3.

The P2 suite uses real temporary repositories, actual pytest/JUnit commands and
real bwrap enforcement. Review records are explicitly labelled FIXTURE; they
exercise validation and integration without claiming live native review. The
first live native cycle and independent product review remain P5.


## P3: deterministic scheduler and durable operations

`engine.Engine` owns transitions, routes, accounts, budgets, dependency order,
stall detection, cancellation and recovery. `NativeRoleDriver` uses the existing
`runner.run_call` with validated project bwrap mounts and pinned model/effort.
Its content hooks (`prepare`, `request`, `validate`, `finalize`) are implemented
by the P4 `roles.ContentDriver`.
Batch content uses `engine.role(role, request, batch=..., key=...)`; stable keys
identify PO selection/comparison chunks, while all failures share the role's
finite batch budget. Task content returns data; the controller alone snapshots,
runs RED/GREEN and gates, validates review receipts, and integrates through P2.
There is no production FakeAdapter or successful fallback for absent roles.

Routes use exactly easy/hard and the approved static tier matrix. Structural
filtering includes enabled profile/account, run disables, exact smoke fingerprint,
capabilities, every writer family and exact SID/profile for fixes. Binary version,
content hash, resolved path, model, effort, harness binds and runtime layout must
match the live smoke. Empty structural routes block with exclusion reasons;
temporary account deferral produces a finite timer. Shared accounts store waits
across every profile. Unknown/expired/stale accounts get a bounded native nonce
probe before content work; recent success means last call succeeded, not measured
quota headroom. Waits do not spend coding or role budgets and cannot lower tiers.

Parallelism is exactly one coding task. The first runnable task completes or
waits/blocks before another starts. Dependencies require COMMITTED; independent
work continues after a block/wait. The controller sleeps directly to the earliest
relevant recheck, or `--until-idle` exits 75. It never asks a model to poll.
All task and non-task failure budgets are finite. Ordinary fixes resume the exact
SID/profile; one escalation creates a durable handoff containing preserved diff,
prior session, findings and gate-log paths, then starts a strong fresh session.
Both original and escalation writer families remain excluded from review.

Every content operation records its stable call ID before launch. Successful
results are replayed after a crash rather than repeating a writer. Call effects
(account backoff, disable and profile strikes) apply once, via `call_effects`.
Failure counters and removal of their operation are one transaction. A durable
handoff intent survives a crash before handoff creation. Runner crash injection
covers launching, spawn, process identity, streaming SID, result file and done;
engine boundaries cover operation insertion, result consumption, effects and
operation completion. Raw output/result/manifest evidence is retained.

Recovery first identifies/kills surviving recorded process groups by PID plus
starttime, refusing ambiguous live ownership; zombies cannot write. Bwrap's PID
namespace and die-with-parent tear down a live writer after controller SIGKILL.
Interrupted writers with SID resume that session. Only initial non-bug implement
without a SID and without a fix round can save an interruption patch, reset its
workspace to base and retry fresh; later session loss blocks until explicit
operator `unblock --fresh-session`. Disposable gate/review exports are pruned;
workspaces, raw calls, receipts and candidate refs survive. Pending local commits
recover through P2; moved main requires renewed gates and a fresh review.

Operator cancellation is a durable row; SIGUSR1 wakes the running controller,
and SIGINT/SIGTERM request all-task cancellation. Runner and gate cancellation
terminate their process groups and preserve work without spending budgets.
The P2 pending insertion remains the acceptance point: earlier cancellation stops
acceptance, later cancellation reports already accepted. Handled cancellation
rows cannot prevent an explicitly unblocked task. Unblock requires a note, resets
budgets once, and records any authorized fresh handoff. Only the operator
`satisfied --note` command writes owner satisfaction.

The input fingerprint includes actual source/intent/implemented/test blobs,
linked result evidence bytes, operator unblock notes and enabled roster. Test
assertion-only changes clear unchanged-input stalls. Unlinked failure logs cannot
manufacture input changes. Work keys suppress unchanged blocked proposals; two
zero-progress batches with identical input stall before further model calls.
Zero justified work prints “owner satisfaction not established”.

P3 tests explicitly use FakeAdapter/provider content with real SQLite, git,
runner and bwrap. They cover seven accepted continuous batches, exact-session
fixes, actual regression RED before fix, independent review/gates/local commits,
account routes, bounded attempts, stall and operator commands. Real child SIGKILL
tests cover each call durability boundary and an active sandbox writer. These
are lifecycle evidence, not real model capability or P5 native canary evidence.
See [ADR-0003](adr/0003-durable-lifecycle.md).

## P4: rich content and native engine wiring

`ingest.manifest` enumerates committed Markdown matching trusted source patterns,
independently of INDEX. Each manifest pins the commit, blob and content hashes,
headings, table data rows and scenario anchors. Large documents are split at
headings and line boundaries into sections of at most 200 KiB. A single line
larger than that bound blocks explicitly. Images must be local regular committed
files, pass PNG/JPEG/GIF/WEBP magic checks and fit 20 MiB. Their bytes are attached
through the existing native adapters; filenames alone cannot satisfy a read.
Every anchor needs a requirement and every attached image a nonempty observation.

`roles.ContentDriver` pins each batch to one commit before preparing content.
Every `po_read`, selection, comparison, synthesis, architect and ADR-review call
uses `engine.role`, inheriting strong routing, sandbox enforcement, finite failure
budgets, account admission, durable operation IDs and replay. Contextual schemas
run before the runner classifies native success. Malformed JSON types become
protocol failures. The runner persists exact prompt text and image content hashes
in each call's `input.json`, regardless of adapter stdin/file conventions.

Committed source digests contain source/blob/image hashes, schema version, call
IDs and provider-reported model or an explicit absence flag. Reuse requires a
matching committed digest and successful recorded read results. A pixel change
invalidates its cache key even when Markdown stays unchanged. Digest paths are
protected from task writers. This records consumption and provenance; it does
not prove model comprehension. Real pixel and table reasoning remains P5.

PO receives implemented and intent bodies, digests, AST-only test discovery,
recent task documents and blocked work. A selection call chooses full tests and
result evidence. The controller unions linked mandatory content and committed
task gate receipts, checks path containment, and supplies actual complete files
with commit/blob/content hashes. Only registered immutable controller artifacts
may be read outside the export, and their hashes are verified. Missing content,
no tests/results, more than 32 files or more than 200 KiB per requirement creates
an evidence gap. Unexpected reads block. Implemented claims must cite supplied
test and result hashes plus concrete assertion/behavior descriptions; discovery
names and unconsumed files cannot authorize them.

Comparison calls deduplicate content and stay within 200 KiB of file bodies.
They return rows only. A single synthesis consumes the total merged comparison
and emits globally 0–6 easy/hard tasks, each linked to a nonimplemented requirement.
Fewer than five requires a reason; zero never means owner satisfaction. Actual
test blobs already participate in P3 stall fingerprints.

The strong architect supplies append-only ADRs, design notes, affected doc targets
and acyclic task dependencies. ADR additions receive a fresh strong review from
a different family on a read-only exact candidate export. The controller records
the reviewed architecture candidate and acceptance intent before CAS. A crash
replays that candidate; moved main preserves it and blocks. Source digests, docs
skeleton and reviewed ADRs are committed before the first task clone exists.
No model controls ref updates or creates a workspace.

Plans enforce short easy or full hard requirements/design/steps/tests/doc-target
contracts. Writer prompts include the plan, architecture, protected paths, actual
RED receipts and handoff/findings as applicable. Review receives the exact tree
and gate evidence and must supply every clean-code checklist entry. Existing P2
checks enforce read-only mount, family/session independence, candidate equality,
full commands and local acceptance. Controller finalization records native
content-result provenance in task docs and regenerates INDEX before snapshot.

`forge init` validates HEAD, creates persistent state and CAS-commits the compact
source/intent/implemented/ADR/task/digest skeleton without changing the target's
host checkout. It is idempotent. Index links carry repo paths and Git blob IDs.
`run` also ensures the skeleton in its architecture commit. Sources, intent,
implementation evidence and decisions retain separate meanings. Tests of this
pipeline use labelled provider fixtures with real subprocesses, git, SQLite,
bwrap and gates. CLI smoke uses a disposable repository and records honest
capability blocking when no smoke-passed native route exists. Live provider
availability, image reasoning, easy/hard canary and independent product review
remain P5; P4 does not claim final delivery. See ADR-0004.

## P5: disposable native acceptance

`forge canary --workdir DIR` creates an isolated Tally repository, operator
configuration, state and evidence beneath DIR. It seeds an actual negative-stock
bug and a missing CSV exporter. Source table row four alone requires rejection
of negative export quantities. A stdlib bitmap-font PNG supplies the column order;
the controller's expected order stays outside every target mount. All runtime
tiers remain configured, Terra remains disabled, and production admission selects
only profiles with the exact real smoke fingerprint and required capabilities.
The generated Codex configuration mounts its minimal binary release directory
when the installed CLI requires the sibling code-mode host.

Doctor records native text, same-SID writable resume, unattended read-only and
random quadrant-image outcomes separately. Missing capabilities remain missing.
A quota/auth/network wait stops further smokes on that shared account until its
concrete recheck time. Native completed-item errors cannot become successful
Codex content simply because a later completion event exists. Failed setup is
persisted as an ineligible smoke record; there is no unisolated fallback.

Canary runs the real CLI with at most two batches and `--until-idle`: account
waits preserve durable state and return without polling. Each active subprocess
has the approved four-hour watchdog; ordinary controller cancellation and process
namespace teardown still apply. Re-running resumes the existing project and
subtracts completed batches from the validation bound. Source read provenance,
including image observations and producing call IDs, reaches task prompts and
is appended by the controller to the reviewed task document.

A deterministic golden acceptance command runs in the gate sandbox and checks
negative-stock protection, negative-export rejection, CSV quoting and the secret
pixel-derived header order. Its command, exact commit/tree, return code, unchanged
content, stdout/stderr and hashed registered receipt remain outside target mounts.
`forge canary --workdir DIR --verify` on an existing canary reads SQLite in
read-only mode, Git objects and existing receipts. It launches neither models
nor target code. It checks easy+hard acceptance, RED ordering/JUnit/unchanged
regression blobs, independent exact-tree review and gate receipts, row-four
coverage, image provenance, and fresh batch-two implemented-content input.
Missing evidence gives BLOCK. Raw calls and state stay in DIR; `project.bundle`
contains only the disposable project's local history. This mechanism is not
itself live acceptance: the dated canary report records the actual observed result.
Independent review of forge-next and its delivery commit remain parent gates.

P5's live protocol captures exposed two content-input gaps. Task synthesis now
receives committed repository paths, implemented documentation and discovery-only
test inventory, plus an explicit list of valid source anchors/IDs and committed
source/image paths. Source references remain contained in that known set; arbitrary
paths are rejected. This allows the PO to choose concrete targets without inventing
paths or losing valid pixel-source citations. A blocked preparation batch supports
one explicit operator `unblock ID --note ...`: it preserves successful read and
comparison operations and historical accounting, records the original block and
operator note, and resets failed role budgets once. It cannot grant a fresh writer
session or silently repeat budget resets.

The canary gate uses `/usr/bin/python3` and its pytest installation. It does not
mount the BUILD venv: on this host that venv resolves into protected coordinator
tools. A bwrap execution failure is classified before checking for JUnit output
and blocks the environment without consuming a coding retry. RED accepts both
ordinary assertion tracebacks and pytest's executed `DID NOT RAISE` expectation
failure, while still rejecting setup/import errors, missing nodes and skips.
Model-returned RED nodes are distinct from registered RED receipts.

A bug escalation without a valid RED receipt returns to regression writing before
any fix call. A legacy candidate already produced without RED is preserved and
blocked. One explicit operator unblock can restart its regression cycle: persist
an immutable candidate and restart intent, move the complete old workspace to a
preserved sibling, recreate the unchanged base, write real RED, then resume the
same implementer SID for the fix. An interrupted base clone can resume from the
recorded intent; it never retroactively approves the invalid candidate. Native
attempts and rejected candidates remain visible in the evidence.

## P5 recovery: selected bases and native response compatibility

A newly created plain clone now has its branch, index and files pinned to the
same selected commit before any agent receives it. The controller seeds these
through its trusted Git directory and a private index; it still never executes
Git against agent-writable configuration. This avoids misleading staged deletions
and a HEAD that refers to a different version from the visible files.

An explicit operator RED restart pins the current accepted main in its durable
restart intent, preserving the rejected candidate, complete old workspace and
exact implementer SID. Restore/replay uses that pinned restart basis and clears
obsolete candidate/RED identifiers. An independent task accepted while the bug
was blocked is therefore retained before the next genuine RED cycle. External
movement after that pin still requires the existing rebase/gate/fresh-review path;
conflicts are never silently resolved or accepted.

Provider diagnostics reporting that the selected model is at capacity classify
as an account environment wait, without coding-budget or strike consumption.
Actual historical failures are retained. Native responses still require a fenced
JSON object and exact role schema; closing-fence whitespace may be compact or
multiline. Decoding the complete JSON object before recognizing its closing fence
also preserves literal backticks inside JSON strings. Bare JSON, nonobjects and
malformed JSON remain invalid.

The disposable CSV workload now includes real failure-safe file publication,
separate from string serialization: one-shot iterators, same-directory temporary
files, flush/fsync, atomic replacement, cleanup and failure/concurrency tests.
The original bug, row-four-only export constraint, scenarios, image-only column
order, easy+hard acceptance and next-batch assertions remain mandatory. Workload
complexity supplies evidence for the PO's own classification; no task difficulty
is overwritten by the controller.

PO comparison prompts explicitly mirror the acceptance rule for `implemented`: every
requirement, including constraints and documentation requirements, needs cited supplied
test content and a result. Any supplied evidence gap forbids that status. Task records,
review prose and filenames alone cannot supply the missing proof; the PO must report
partial, missing or conflict instead. The deterministic comparison validator remains
the authority and rejects unsupported claims. This instruction is trusted prompt text
before the untrusted DATA block, rather than an instruction embedded in source material.

## Optional Lead loop and quota observation

The current `forge.loop` path supports direct mode and optional Lead mode; it
does not revive the legacy PO/architect ingestion stages described above.
`forge.prompts` contains tracked runtime contracts. Lead owns task choice and
cheap/normal tier decisions; controller validates the bounded contract and
retains execution, retries, persistence, sandbox, gate, family/SID and CAS
checks. A task/spec/base receipt binds the exact candidate and independent
review. Every Lead-mode acceptance requires gate rc0/PASS, including progress;
Lead completion is independently reviewed on the unchanged accepted tree.
Task boundaries clear executor/reviewer pins and retain the Lead SID. Repair
and one reconciliation are finite and durable across recovery.

SQLite schema v3 adds quota observations, events, episodes and active spans.
The active loop opts into migration; legacy state users retain v2 behavior.
Metrics are an observation of existing admission/result/process seams, not a
second scheduler or execution journal. Event IDs deduplicate replay; proposed
process start alone never closes a quota episode. Active-span intersection and
interval union prevent STOPPED gaps and overlapping roles/accounts from
inflating downtime. Read-only metrics status never migrates state.

`native_children` produces an explicit per-call off policy until static pinning
and actual native spawn/result evidence exist for the individual route. Policy
is recorded with the call's sandbox/argv evidence; no home settings change.
The Forge-only Terra helper is single-use and separate from ordinary smoke.
Catalog discovery, proof inference and reviewed activation are distinct gates;
missing native metadata keeps it disabled. See [operational details](loop.md).
