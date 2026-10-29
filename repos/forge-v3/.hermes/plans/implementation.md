Historical source-generation plan from forge-next `9df17d442b50deeca6a30086735da125519cb6ff`. Not current authority. See `docs/migration-v3.md`. Do not execute launch or deploy steps from this file. An older 34/34 canary is a different fingerprint and is not acceptance of this tree.

VERDICT: PASS (plan readiness only; author assertion, not the independent gate)
Revision: v3, correction cycle 2 of max 2 (final). Narrow residual correction of v2 (sha256 2061e0ca6a15d1f647a2910c98567f0c5fe217ac27ced13e98f2f56175e35487; preserved in scratch/forge-next-planning/plan-v2.md).
Timestamp: 2026-10-22T21:18+02:00
Repo: /home/jan/Sources/forge-next HEAD 677e46d0eed63d0366678c51cc8af154cbe4bdc3 branch main; README.md/.gitignore untouched
Residual correction author: GPT-6/Codex, family openai; exact launch model/effort not exposed here; launcher decision authoritative. Accepted v2 author: claude-code/claude-opus-5-5, anthropic, medium.
Blocking questions: none
Acceptance matrix IDs: AC-01 … AC-36 (section 2). Review closure: PR-01 … PR-07 (section 12)
Authoritative plan path: /home/jan/Sources/forge-next/.hermes/plans/implementation.md
Next gate: independent exact-fingerprint plan confirmation, pkr_worker --role analysis --avoid-family openai, strong-only task-local config; no review launched by this stage.
Scope: plan only. No product code until independent plan PASS.
Precedence: owner-contract.md > recon facts > steering. Choices the contract leaves open are marked DEFAULT.
Prior-stage host facts (2026-10-22 21:05; not re-probed in correction 2): codex 0.159.3, claude 2.1.287, grok 1.0.52 installed (presence only, not auth/quota);
  `bwrap --ro-bind / / --unshare-all --share-net --die-with-parent --new-session --tmpfs $HOME true` exits 0 although
  kernel.apparmor_restrict_unprivileged_userns=1 (the /etc/apparmor.d/bwrap-userns-restrict profile permits /usr/bin/bwrap);
  git 2.53 (has `merge-tree --write-tree`); python3 3.14; pytest installed; uv not on PATH, so python3 -m venv.

# 1. Product summary and key tradeoffs

Forge is a local Python CLI (`forge`) with no game or vendor specifics. It runs continuous PO → architect → per-task
plan/implement/review → gate → local-commit batches against one target git repository. A deterministic controller makes every
control decision. LLMs run only as native CLI subprocesses for content work and never decide scheduling, polling, budgets or
satisfaction.

Tradeoffs:
- **One enforced boundary: every native agent call and every gate command runs inside bubblewrap** (§6). Filtered env or a changed
  HOME is never treated as isolation. A route whose sandbox cannot be set up is disabled and blocks; there is no unisolated fallback.
  Accepted residual: inside an agent call, the agent and its tool-launched commands can read that one harness's own credential
  directory, because the CLI needs it to authenticate. Controller state, other harnesses' credentials, ssh/gpg and the real HOME are
  not mounted.
- **Controller never runs git inside agent-writable directories.** Agents get plain clones. The controller snapshots them through
  its own GIT_DIR + private index. The candidate is a **commit** (fingerprint = its tree id) created before gate/review. If main has
  not moved, that exact commit is the one accepted, so its id is durable before any ref mutation.
- **One coding task at a time (DEFAULT `parallel = 1`).** Blocked or waiting tasks are skipped and independent tasks run.
- **Capability-gated adapters.** A profile is eligible only after a real smoke for its exact (binary version, model, effort) passes
  inside the sandbox. An implementer also needs a verified `resume` capability. Nothing is downgraded silently.
- **Rich-source reading is a recorded stage** (`po_read`), and its coverage of every table row, scenario and image is checked
  deterministically (§8.4).

# 2. Acceptance matrix (P# = phase owner; evidence = planned executable test or artifact; FIXTURE tests never count as live)

| ID | Requirement | Evidence (test / artifact) | P |
|---|---|---|---|
| AC-01 | Standalone general engine. No game/Kiro coupling. Never touches agent-loop, global policy, Trello, remotes. Local commits only. | `test_no_forbidden_refs`; `test_git_ops_allowlist` (wrapper allows only read-tree, add, write-tree, commit-tree, update-ref, merge-tree, archive, clone, fetch-from-local-clone, notes, rev-parse, cat-file, diff, ls-tree; no push/remote add) | P0 |
| AC-02 | Continuous batches, no default total cap. `--max-batches N` only as an explicit validation bound. | `test_run_default_unbounded` (fake work for 7 batches, all run); `test_max_batches_bound` | P3 |
| AC-03 | PO reads extensive Markdown (tables, scenarios) via a recorded `po_read` stage with deterministic coverage. | `test_md_anchor_extraction` (table rows, Given/When/Then, "Scenario" headings); `test_po_read_rejects_missing_anchor`; `test_table_only_constraint_reaches_po` (constraint only in a table row of a doc absent from INDEX.md becomes a req and must appear in the comparison) | P4 |
| AC-04 | PO reads actual image pixels, not names. | `test_po_read_requires_observation_per_attached_image`; `test_image_attached_as_payload` (argv/stdin carries image, not filename); real smoke `image` (§5.4); canary pixel-only column order (§10) | P1/P4/P5 |
| AC-05 | PO compares reference against implemented docs/tests/evidence. | `test_po_comparison_total` (every req id gets implemented/partial/missing/conflict + evidence refs, else schema fail); `test_po_input_contains_implemented_state_and_test_inventory`; `test_req_implemented_only_in_tests_changes_status` (only assertion body changes: same paths/names/docs/summaries; supplied body/blob provenance changes, unsupported implemented claim rejected); `test_po_missing_or_unread_test_evidence_cannot_claim_implemented`; `test_po_content_bound_and_chunk_merge`; `test_test_blob_change_clears_stall` | P3/P4 |
| AC-06 | 0–6 tasks per batch, target 5–6 when justified, no padding, each easy or hard. | `test_po_schema` (>6 rejected; <5 needs `shortfall_reason`; 0 → `NO_JUSTIFIED_WORK`); every task must cite ≥1 req id that is not `implemented` | P4 |
| AC-07 | Strong architect records ADRs/design notes before any worker starts. | `test_architect_before_workers`; ADR commit precedes first task base | P4 |
| AC-08 | Global PO/planner (incl. `po_read`) and architect always strong. | `test_role_tier_table` | P3 |
| AC-09 | Easy: normal short plan, cheap implementer, normal independent reviewer. | `test_easy_routing` | P3 |
| AC-10 | Hard: strong full plan (requirements/design/steps/tests), normal implementer, strong independent reviewer. | `test_hard_routing`; `test_hard_plan_schema` | P3 |
| AC-11 | Same quality bar; concrete clean-code checklist. | `test_review_checklist_required` (every key pass or n/a+reason) | P2 |
| AC-12 | Bugs regression-first: red on unchanged implementation before the fix, then green. | §8.5 tests: `test_red_rejects_missing_node`, `_zero_tests`, `_import_error`, `_setup_error`, `_passing_on_base`; `test_red_requires_test_paths_only`; `test_green_requires_same_red_tests_unchanged`; `test_red_evidence_precedes_fix_call` | P2/P3 |
| AC-13 | Code and affected docs reviewed and committed together. Intent/ADRs cannot be weakened or overridden. | `test_doc_targets_required`; `test_protected_paths_reject`; `test_accepted_commit_tree_eq_tested_eq_reviewed` (includes docs) | P2 |
| AC-14 | Exact runtime roster. Sol in two tiers. Terra present, disabled, never silently eligible. Every call pins model+effort. | `test_roster_default`; `test_terra_never_eligible_unverified`; `test_argv_pins_model_effort` (initial and resume, each adapter) | P1 |
| AC-15 | Haiku `high` only after verification. No effort substitution. | `test_unverified_profile_ineligible`; `test_no_effort_substitution` | P1 |
| AC-16 | Runtime roster separate from strong-only BUILD roster; no global mutation. | `test_runtime_roster_has_all_tiers`; `test_no_pkr_dependency` (product never reads ~/.hermes); build roster only in §9 | P0 |
| AC-17 | Fresh session per task. Same native implementer session for ordinary fixes. Stronger-model switch is a recorded handoff, not resume. | `test_implementer_requires_resume_capability`; `test_fix_resumes_exact_sid`; `test_escalation_is_handoff_fresh_session`; `test_lost_session_blocks_not_fresh` | P3 |
| AC-18 | Finite budgets, one escalation, then detailed BLOCKED (cause, evidence, preserved work, unblock condition). Covers PO/architect/po_read too. | `test_budget_exhaustion_escalates_once_then_blocked`; `test_blocked_record_fields`; `test_non_task_role_attempts_bounded` | P3 |
| AC-19 | Independent tasks continue; dependents wait. | `test_dependents_wait_independents_run` | P3 |
| AC-20 | Total failure classification; waits never consume coding budget; config/env errors are not infinite waits. | `test_classify_table` (one row per §5.3 case incl. 401 vs invalid flag vs 429, rc 0 native failure, quota words in successful text, unknown nonzero) | P1 |
| AC-21 | Shared-subscription quota admission; unknown quota is not capacity; no downgrade past a pool-wide wait. | `test_account_wait_blocks_all_profiles_of_account`; `test_unknown_account_probe_before_long_call`; `test_wait_expiry_readmits`; `test_no_downgrade_on_pool_wait`; `test_all_profiles_disabled_blocks_config`; `test_luna_sol_only_reviewer_blocks`; `test_missing_image_route_blocks`; `test_missing_resume_route_blocks`; `test_different_family_wait_has_recheck`; `test_escalation_family_exclusions_block` | P3 |
| AC-22 | Deterministic locking and atomic durable state. | `test_second_controller_refused`; `test_state_txn_atomic` | P0 |
| AC-23 | Crash recovery with durable process/session ownership. | `test_sigkill_controller_kills_live_writer` (pid-ns teardown) ; `test_recovery_kills_surviving_group`; `test_sid_persisted_before_final_output`; `test_recover_interrupted_fix_resumes_sid` | P3 |
| AC-24 | Cancellation, including cancel racing acceptance. | `test_cancel_kills_group_preserves_workspace`; `test_cancel_before_pending_prevents_accept`; `test_cancel_after_pending_reports_accepted` | P3 |
| AC-25 | Local commit recovery: exactly one accepted commit per task. | crash tests at: after candidate commit before durable id, after durable id before CAS, after CAS before done (§8.7) | P2 |
| AC-26 | External main movement: preserve both sides, re-snapshot, re-gate, fresh re-review; conflict blocks. | `test_main_moved_clean_merge_regates_rereviews`; `test_main_moved_conflict_blocks_preserved` | P2 |
| AC-27 | No LLM polling control loop. | `test_controller_never_calls_llm_for_scheduling`; waits are timer sleeps to `recheck_at` | P3 |
| AC-28 | No repeat of unchanged failed/no-progress work; deterministic stall. | `test_duplicate_blocked_work_key_suppressed`; `test_stall_after_two_zero_progress_same_input`; `test_stall_clears_on_input_change` | P3 |
| AC-29 | User satisfaction never inferred from model verdicts. | `test_satisfied_only_by_operator_cmd`; NO_JUSTIFIED_WORK text "owner satisfaction not established" | P3 |
| AC-30 | AI-friendly docs from the first task: compact index + modular source refs/intent/implemented state/ADRs/task evidence/req digests, stable links. | `test_init_skeleton`; `test_index_generation`; evidence links = repo path + blob sha | P4 |
| AC-31 | Fresh different-family read-only review of the exact candidate commit. | `test_reviewer_family_differs`; `test_review_mount_readonly` (write → EROFS); `test_review_content_unchanged_check`; `test_review_tree_mismatch_invalid` | P2 |
| AC-32 | Full gate: docs, architecture, tests; prose PASS alone never enough. | `test_gate_requires_all_checks`; `test_gate_mutation_of_tracked_file_fails`; `test_gate_ignored_outputs_allowed` | P2 |
| AC-33 | Enforced boundary: target execution (agent tools + gate commands) cannot read controller state, other credentials, real HOME; unsupported → disabled/BLOCK. | adversarial §6.4 tests including accepted HOME-based executable and exact state-leaf mounts; `forge doctor` sandbox probe (real) | P1/P2 |
| AC-34 | Product material is untrusted; only operator-owned trusted commands; repo cannot add commands. | `test_repo_config_cannot_define_commands`; `test_node_arg_validated`; `test_controller_git_ignores_workspace_config` | P2 |
| AC-35 | Real disposable native canary PO → architect → plan → implement → independent review → docs+local commit → next batch, easy+hard, actual vs configured models distinguished. Meaningful recovery/blocked tests. First delivery: CLI, tests, independent code review, local commit. | `forge canary` + `--verify` + evidence bundle + `docs/canary/<date>.md` (§10); full pytest; pkr_worker review | P5 |
| AC-36 | Lean stack: stdlib argparse/sqlite3/subprocess/tomllib + pytest; bwrap and git as system tools. | `pyproject.toml` zero runtime deps | P0 |

# 3. Layout

```
pyproject.toml            # forge-next, requires-python >=3.12, console script forge=forge.cli:main
src/forge/cli.py          # argparse commands (§7)
src/forge/config.py       # operator config (trusted), roster, accounts, budgets
src/forge/state.py        # sqlite schema, transactions, flock, atomic files
src/forge/gitops.py       # clone workspaces, controller-index snapshot, candidate commits, merge-tree, CAS
src/forge/sandbox.py      # bwrap argv builder, bind canonicalization/overlap rejection, probe
src/forge/runner.py       # launch/record/stream/kill native calls (§5.1)
src/forge/adapters/{base,codex,claude,grok}.py   # argv, streaming parse, SID capture, classification inputs
src/forge/classify.py     # §5.3 total classifier
src/forge/doctor.py       # sandbox probe, real smokes, PNG generator
src/forge/ingest.py       # manifest, md anchors, image validation, po_read coverage
src/forge/roles.py        # prompts + JSON schemas
src/forge/engine.py       # states, scheduler, accounts, budgets, waits, stall, cancel, recovery
src/forge/gate.py         # deterministic gate checks, junit parsing, red/green
src/forge/docsindex.py    # docs/forge/INDEX.md generator
src/forge/canary.py       # disposable canary builder + verifier + evidence bundler
tests/                    # pytest; tests/fixtures/native/*.jsonl labelled FIXTURE with date+binary version
docs/                     # forge-next docs: architecture.md, adr/, canary/
```

Runtime locations (roots never mounted; only the exact §6.3 leaf exceptions are mounted):
- Operator config (trusted): `$XDG_CONFIG_HOME/forge/projects/<slug>.toml` (default ~/.config).
- State: `$XDG_STATE_HOME/forge/<slug>/` (default ~/.local/state): `forge.db`, `controller.lock`, `idx/`, `calls/<id>/`, `smoke/`.
- Workspaces: `$XDG_STATE_HOME/forge/<slug>/ws/<task>/src` (agent clone), `ws/<task>/gate-<n>/`, `ws/<task>/review-<n>/`.
  Only the specific `src`/`gate-n`/`review-n` directory is bound into a sandbox; the parent state dir never is.

# 4. Configuration (operator-owned; the target repo may NOT supply commands)

```toml
target_repo = "/abs/path"; branch = "main"
sources = ["docs/source/**/*.md"]
protected = ["docs/source/**", "docs/forge/intent/**", "docs/forge/adr/**"]   # ADRs: new files only, written by Forge
test_paths = ["tests/**"]                       # allowed paths in a red checkpoint
parallel = 1
[commands]   # trusted argv; placeholders {node} and {junit} only, validated
test = ["python3", "-m", "pytest", "-q"]
test_one = ["python3", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--junitxml={junit}", "{node}"]
lint = []
[exec]
gate_network = false
ro_binds = []                                    # extra read-only paths for the gate toolchain (validated §6.3)
[accounts]   # shared-subscription identity; DEFAULT one account per harness
codex = {enabled=true}; claude = {enabled=true}; grok = {enabled=true}
[harness.codex]  binds_rw = ["~/.codex"];  binds_ro = []                       # DEFAULT, verified by smoke
[harness.claude] binds_rw = ["~/.claude", "~/.claude.json"]; binds_ro = ["~/.local/share/claude"]
[harness.grok]   binds_rw = ["~/.grok"];   binds_ro = []
[budgets]    # DEFAULT
easy = {plan_attempts=2, impl_attempts=2, fix_rounds=2}
hard = {plan_attempts=2, impl_attempts=2, fix_rounds=3}
escalation = {fix_rounds=2}                      # exactly one per task
role_attempts = 2                                # po_read / po / architect / adr_review per batch
protocol_retries = 1; profile_strikes = 2; rebases = 2; stall_batches = 2
[timeouts]   # seconds per call; DEFAULT
po_read=1800; po=2400; architect=1800; plan=900; implement=2700; review=1200; probe=120
max_turns = {po_read=40, po=60, architect=40, plan=30, implement=80, review=40, probe=2}
[[profile]]  # runtime roster DEFAULT (exact)
id="luna-xhigh";  harness="codex";  model="gpt-6-luna";        effort="xhigh"; family="openai";    tiers=["cheap"]
id="haiku-high";  harness="claude"; model="claude-haiku-5-5";  effort="high";  family="anthropic"; tiers=["cheap"]
id="sol-high";    harness="codex";  model="gpt-6.1-sol";       effort="high";  family="openai";    tiers=["normal","strong"]
id="sonnet-high"; harness="claude"; model="claude-sonnet-5-5"; effort="high";  family="anthropic"; tiers=["normal"]
id="grok-high";   harness="grok";   model="grok-4.7";          effort="high";  family="xai";       tiers=["normal"]
id="opus-high";   harness="claude"; model="claude-opus-5-5";   effort="high";  family="anthropic"; tiers=["strong"]
id="terra";       harness="codex";  model="TBD";               effort="TBD";   family="openai";    tiers=["strong"]; enabled=false
```

Contract fixes "medium/high"; DEFAULT `high`; operators may add `*-medium` profiles. Compute a **structural route set S(role, task)**
first: enabled profile and account, not disabled for this run, matching tier, required capabilities (`resume` for implement/fix,
`image` for image-bearing reads), all recorded family exclusions, and a passing smoke for the exact
(binary realpath+version, model, effort, harness binds). Fixes additionally require the exact persisted session_profile/SID;
a peer cannot replace it. Apply the existing session-loss/recovery rules (§8.6) before S evaluation; missing SID remains
`session_lost` or its narrowly allowed initial-implementation recovery, not a relabeled route block.
Ignore only temporary account waits when constructing S. **Eligible** = members of S whose account
is not waiting (unknown accounts need the §8.6 admission probe). Empty S is a permanent route block under §5.3, not WAIT. `terra` cannot be enabled while model/effort is `TBD`. If `haiku-high` fails its smoke, cheap tier = `luna-xhigh`
(peer, not a downgrade).

# 5. Native adapter contracts

## 5.1 Interface and runner

```python
class Call:    id, role, profile, workdir, writable: bool, prompt_text, images: list[Path], resume_sid: str|None,
               preset_sid: str|None, timeout, max_turns
class Event:   kind: Literal["sid","final","error","other"], data: dict          # from streaming parse
class Result:  outcome: Outcome (§5.3), sid, actual_model, final_text, error_fields: dict, usage, raw_dir, recheck_at
class Adapter: build_argv(call) -> (argv, stdin_bytes, extra_env); parse_line(line) -> Event|None;
               finish(rc, events, stderr) -> Result; error_fields(events, stderr) -> dict
```

Runner order (each step durable before the next):
1. `INSERT calls(status=launching, preset_sid)`; for claude the controller generates a UUID and passes `--session-id` so SID is
   known before launch.
2. `Popen(bwrap_argv + native_argv, start_new_session=True)`; immediately `UPDATE calls SET pid, pgid, proc_start (from
   /proc/<pid>/stat field 22), status=running`.
3. Stream stdout line by line to `calls/<id>/stdout`; on the first `sid` event, `UPDATE calls SET session_out` at once.
4. On exit: `finish()` → `result.json` (tmp, fsync, rename, dir fsync) → `UPDATE calls SET status=done, outcome`.
5. Timeout or cancel: `killpg(SIGTERM)`, 10 s, `killpg(SIGKILL)`. bwrap runs with `--unshare-pid --die-with-parent`, so the
   whole sandboxed tree dies with bwrap, and bwrap dies with the controller (SIGKILL included).

The final answer must be a fenced JSON block matching the role schema; Forge parses the last such block of `final_text`.

## 5.2 Argv (pinned on every call and resume). All run inside the §6 sandbox, so in-CLI approval/sandbox modes are set to
non-interactive and are not relied on for isolation. Read-only is enforced by the read-only mount.

- codex: `codex exec -m M -c model_reasoning_effort="E" --dangerously-bypass-approvals-and-sandbox --json -C /work [--image P]... -`
  - Resume: `codex exec resume SID -m M -c model_reasoning_effort="E" --dangerously-bypass-approvals-and-sandbox --json [--image P]... -`
  - SID = `thread.started.thread_id` (first JSONL event). Final = `turn.completed`; errors = `error`/`turn.failed` events.
    Actual model from event metadata if present, else `null` ("configured, not provider-confirmed").
- claude: `claude -p --model M --effort E --output-format stream-json --verbose --max-turns T --permission-mode bypassPermissions
  (--session-id UUID | --resume SID)`; never `--bare`.
  - SID = preset UUID, confirmed by the `system/init` event. Final = `result` event `subtype=="success" && is_error==false`;
    `subtype=="error_max_turns"` → turn_limit. Actual model = keys of `modelUsage`, must contain M.
  - Images: `--input-format stream-json` user message with base64 image content blocks. Accepted only if the image smoke passes.
- grok: `grok --prompt-file F --model M --reasoning-effort E --output-format streaming-json --max-turns T --cwd /work --always-approve
  [--resume SID]`. Images via `--prompt-json` content blocks, only after the image smoke passes.
  - Parser is new (differs from pkr_worker): accepts top-level `text`, `stopReason=="end_turn"`, `sessionId`, `usage`, `modelUsage`
    as documented by the PKR skill. Exact event names are confirmed or corrected from the first real smoke; the raw output is kept
    as a dated FIXTURE. If SID is only emitted at the end, the profile gets capability `late_sid` and §8.6 rules apply.
- Prompt file / stdin / images live in `calls/<id>/in/`, bound read-only at `/in` for that call only.

## 5.3 Total failure classification (classify.py; first matching row wins; diagnostic patterns are matched only against
`error_fields` = stderr + native error events/fields, **never** against the final answer text of a successful result)

| # | Case | Outcome | Effect |
|---|---|---|---|
| 1 | Controller cancel | `cancelled` | task → CANCELLED; no budget |
| 2 | Controller crash/recovery found the call unfinished | `interrupted` | §8.6; no budget |
| 3 | Spawn failed (ENOENT, EACCES) or binary realpath/version ≠ smoke record | `env_block` | profile disabled until `forge doctor` passes again; recompute S: use an eligible peer, timed WAIT, or route BLOCK as below |
| 4 | bwrap setup failure (stderr begins `bwrap:`, rc≠0, no native output) | `env_block` | sandbox route disabled; all calls needing it block; unblock = doctor probe passes |
| 5 | Wall timeout | `timeout` | +1 coding attempt (or role attempt) |
| 6 | Structured success, rc 0, schema-valid final JSON, model matches or null | `ok` | |
| 7 | Structured success but actual model present and ≠ pinned | `model_mismatch` | result discarded; profile disabled for run; no budget |
| 8 | error_fields match quota patterns (`429`, `rate.?limit`, `usage limit`, `quota`, codex `usage_limit_reached`, claude `rate_limit_error`) | `quota_wait` | **account** WAIT; `recheck_at` = parsed reset time, else 15 min·2^k (k = consecutive quota waits on account), cap 4 h; no budget |
| 9 | error_fields match auth patterns (`401`, `unauthori[sz]ed`, `not logged in`, `login required`, `invalid.*token`) | `auth_wait` | account WAIT, recheck 1 h, operator notice in status; no budget |
| 10 | error_fields match transient network (`ECONNRESET`, `ENOTFOUND`, `network is unreachable`, `503`, `overloaded`) | `env_wait` | account WAIT, recheck 10 min·2^k cap 2 h; no budget |
| 11 | rc 2 with usage/`unexpected argument`/`invalid value`, or model-not-found/unsupported effort | `config_block` | profile disabled with reason; unblock = operator config fix + doctor; no budget |
| 12 | Native turn limit (claude `error_max_turns`, grok `stopReason=="max_turns"`) | `turn_limit` | +1 coding attempt |
| 13 | rc 0 with no structured success, or unparseable/schema-invalid final JSON | `protocol` | retry once without budget; next consecutive → profile disabled for run (`protocol_disabled`) |
| 14 | Anything else (any rc≠0, or native unsuccessful result with rc 0, without matching rows 3–12) | `unknown_fail` | +1 coding attempt and +1 profile strike; `profile_strikes` consecutive → profile disabled for run |
| 15 | `ok` but gate or review fails | `task_fail` | +1 fix round |

Waits are timers, unbounded by design (owner: no fabricated capacity, no downgrade); they never consume coding/role budget and
are visible in `forge status`. Deterministic errors (rows 3, 4, 7, 11, 13, 14) disable a route instead of waiting, so no
config/flag error can wait forever. At every role admission, recompute S **after** tier/capability/family/session filtering (§4).
Empty S → task `BLOCKED` with outcome `config_block` (missing/disabled/family route) or `capability_block` (image/resume),
role, constraints, excluded profiles with reasons, preserved work/evidence and exact unblock condition (configure and smoke
a compliant route, or explicit session handoff under §8.6). Non-task roles block their batch. No timer is invented for this.
Other independent tasks continue; after runnable work drains, aggregate run status `BLOCKED_CONFIG`, exit 2.
Nonempty S with all accounts waiting → `WAITING(prior_state)` with required role, candidate accounts and concrete
`recheck_at = min(account.recheck_at for S)`; expiry triggers the bounded admission probe. A WAIT without a finite recheck
is an invariant error → block. Never select a lower tier or relax a family/session/capability constraint.

## 5.4 `forge doctor` (real, never fixture; all inside the sandbox of §6)

1. **sandbox probe:** runs §6.4 adversarial checks with `/usr/bin/true`-class commands; fail → sandbox route `env_block`.
2. per enabled profile, **text:** prompt returns `{"nonce": N}`; verifies streaming parse, SID capture time, actual model where
   reported, effort accepted (rc 0, no warning in error_fields).
3. **resume (write mode):** resume SID; prompt (without re-sending N) asks to write `resume-<N>.txt` containing N into `/work` and
   return N. Grants `resume` only if the answer and the file both match, the argv pinned M/E, and SID is unchanged. Proves the
   resumed session keeps the pinned model and write permission in the writable mount.
4. **read-only:** fresh call with `/work` read-only asks to create a file; must complete non-interactively (no prompt, rc 0 or
   native error) and the file must not exist. Proves unattended read-only routes.
5. **image:** 64×64 PNG from stdlib zlib/struct, four quadrants in random distinct colours, random filename; asks top-left and
   bottom-right colours. Grants `image` only if both are correct.

Each writes `smoke/<profile>.json`: binary realpath+version, argv, binds, model, effort, raw output path, SID event timing,
capabilities, timestamp, status. Exit: 0 all pass; 1 some fail; 75 failures are all waits.

# 6. Execution / permission boundary (one supported mode: `bwrap`; fail closed)

## 6.1 What is protected
Controller secrets = state dir (db, calls, smoke, idx), operator config, real HOME contents (ssh, gpg, git credentials, shell
history, ~/.hermes, other projects), credential dirs of harnesses other than the one in use, and the controller's environment.
Target execution = native agent CLIs and every command they launch, plus gate commands.

## 6.2 Sandbox argv (sandbox.py)

```
bwrap --unshare-all [--share-net] --die-with-parent --new-session --clearenv \
      --ro-bind /usr /usr --ro-bind /etc /etc --symlink usr/lib /lib --symlink usr/lib64 /lib64 --symlink usr/bin /bin \
      --proc /proc --dev /dev --tmpfs /tmp --tmpfs /home --dir $HOME \
      <validated binds> --bind|--ro-bind <workdir> /work --ro-bind <calls/id/in> /in --chdir /work \
      --setenv HOME $HOME --setenv PATH /usr/bin:/bin:/opt/forge-native/bin --setenv LANG C.UTF-8 [--setenv TERM dumb] \
      -- <argv>
```

- Agent call: `--share-net`; binds = exact in-use harness leaves + the resolved executable file mounted ro at
  `/opt/forge-native/bin/<harness>` (invoke that absolute alias, not the HOME symlink). If the binary needs sibling runtime
  files, only its operator-vetted release directory may be mounted ro at `/opt/forge-native/runtime/<harness>` and the
  executable invoked there; smoke must pass that exact layout. No mount of `~/.local/bin` or HOME is implied.
  `/work` is writable only for implement/fix (agent clone `ws/<task>/src`); read-only for po_read/PO/architect/plan/review
  (fresh `git archive` export of the relevant commit).
- Gate command: no network unless `gate_network`; binds = `ro_binds` only; `/work` = the gate export (writable); `HOME=/tmp/home`.
- Environment: `--clearenv`, then only the variables above. No API keys or coordinator variables are passed.

## 6.3 Bind validation
Validation distinguishes controller-selected leaves from operator extras. Sources are absolute, `realpath`-canonicalized
(resolving `~` and symlinks); destinations are fixed controller aliases, never supplied by repo/model content. Compare paths
component-wise, not by string prefix. Record canonical source, destination, mode and class in the smoke/call manifest.

| Mount class | Exact authorization / destination | Exceptions to ancestor protection |
|---|---|---|
| work | Controller-selected `state/ws/<task>/src`, `gate-<n>` or `review-<n>` (doctor uses its own disposable workspace); `/work`, role-specific rw/ro | This one selected leaf under state/HOME, never its parent, another task or sibling export |
| input | Controller-created `state/calls/<this-call>/in`; `/in`, ro | This call's input leaf only, never `calls`, call root, stdout, db, idx or another call |
| executable | Exact resolved installed binary file (recon paths under HOME are valid); `/opt/forge-native/bin/<harness>`, ro; optional minimal operator-vetted release directory as §6.2 | HOME ancestry only; may lie inside the exact in-use harness bind (e.g. Codex packages or Grok downloads); exposes no additional credential ancestor |
| harness | Exact operator-approved in-use `binds_rw`/`binds_ro` leaves; same absolute destination in otherwise empty HOME | HOME ancestry and that exact in-use harness root only; exposes the explicitly accepted one-harness credentials (§11 R1), never another harness/root |
| toolchain extra | Exact operator `exec.ro_binds` leaf; same destination, ro (HOME-local venv/toolchain allowed) | HOME ancestry only; no overlap with protected state/config/credential/project paths or the work/input leaves |

Protected roots are state, operator config, ~/.ssh, ~/.gnupg, ~/.hermes, all harness credential roots, target repo and other
project roots; real HOME is protected except for the explicit narrow classes above. `/`, `/home`, real HOME, state/config roots
and their ancestor binds are always forbidden. An exception authorizes **only** its exact selected canonical source and fixed
destination, never a parent, sibling or arbitrary descendant; a work/input exception does not exempt all of state. Each class
must be disjoint from every other protected root except its explicitly stated containing/exact authorized root. Thus a state/config root overlapping a
harness or executable leaf is invalid configuration; don't broaden an exception to make it pass. Parent release dirs must not
contain controller state, config or credential files. Operator-added binds cannot impersonate controller-selected classes.
Only work/input occupy `/work` and `/in`; no other class may overlay them, system mounts or another destination.
The permitted source overlap is the selected executable inside the in-use harness root, mounted at its distinct
`/opt/forge-native` destination; it is not a parent-state/HOME exception. Reject duplicate/overlapping
extra sources. Required system mounts in §6.2 are fixed, not an extra-bind escape hatch.

Check canonical equality to the selected leaf (including inode identity at launch); a symlink to a forbidden ancestor, sibling
or different leaf fails even if its lexical path looks allowed. Symlinks *inside* mounted trees grant no host access: only
sandbox paths resolve; controller reads must still enforce containment. A writable target never gets to choose/replace a
mount root. Revalidate before spawn; mismatch → `env_block`, no host fallback. These are narrow bind rules, not a claim that
clearing env/HOME or prompt-injection warnings isolate execution.

Concrete valid doctor construction for the recon Codex path: select
`~/.local/state/forge/<slug>/ws/doctor/src` → `/work` and `calls/<this-call>/in` → `/in`;
`~/.codex/packages/standalone/releases/0.159.3-x86_64-unknown-linux-musl/bin/codex` → `/opt/forge-native/bin/codex` ro,
plus the exact in-use `~/.codex` harness bind. The state root, sibling calls, real HOME and other credentials remain unmounted.
Only the native executable/one-harness overlap is authorized; source/test gate calls omit both harness and native mounts.

## 6.4 Adversarial tests (unit tests on argv + real probe in doctor; P1/P2)
`test_sandbox_cannot_read_state_db_abs_path`, `test_cannot_read_ssh_or_other_harness_creds`, `test_cannot_write_controller_state`,
`test_bind_ancestor_rejected` (~ and ~/.local), `test_bind_symlink_alias_rejected` (link → state dir), `test_review_write_erofs`,
`test_no_approval_prompt_readonly` (smoke 4), `test_gate_no_network_by_default`, `test_env_has_no_api_keys`; `test_selected_work_and_input_leaves_under_state_accepted`;
`test_home_native_binary_alias_accepted` (all three recon layouts, including binary inside in-use harness root);
`test_home_toolchain_leaf_accepted`; `test_state_root_and_sibling_call_export_rejected`;
`test_mount_wrong_alias_or_replaced_root_rejected`; `test_exec_parent_containing_credentials_rejected`. Any failure in the
real probe disables the route; there is no unisolated mode and no acceptance commit without the sandbox.

## 6.5 Trusted command policy and controller git hygiene
- Only operator config defines argv templates. LLM strings are never executed, except `{node}`: must match
  `^[A-Za-z0-9_./-]+(::[A-Za-z0-9_\[\]./-]+)*$`, not start with `-`, file part inside the export. `{junit}` is controller-chosen.
- Repo files cannot add commands, profiles, binds or protected-path exemptions.
- The controller runs git only with the target's own `.git` as GIT_DIR (operator-trusted config), with
  `-c core.hooksPath=/dev/null -c core.fsmonitor=false`, a private `GIT_INDEX_FILE` under `idx/`, and `--work-tree=<workspace>`.
  It never runs git with the agent clone's `.git` (agent-controlled config). Nested `.git` dirs in a workspace are ignored by git.

# 7. CLI (exit codes: 0 ok/idle, 1 doctor-partial, 2 config/blocked, 3 stalled, 75 all-waiting, 130 cancelled)

| Command | Behaviour |
|---|---|
| `forge init --config PATH` | Validate config and repo HEAD; create state dir/db; commit `docs/forge/` skeleton (INDEX.md, intent/, implemented/, adr/, tasks/, digests/) locally if absent. |
| `forge doctor [--smoke] [--profile ID]` | §5.4. |
| `forge run [--max-batches N] [--until-idle]` | Recover, then loop batches. No default cap. Waits sleep until earliest `recheck_at`; `--until-idle` exits 75 instead. |
| `forge status [--json]` | Batches, tasks, accounts/waits, disabled profiles + reasons, blocked records, eligible profiles, sandbox state. |
| `forge cancel [--task ID \| --all]` | Insert cancel row, SIGUSR1 to controller pid; SIGINT/SIGTERM equal `--all`. |
| `forge unblock ID --note TEXT [--fresh-session]` | Operator only. Resets the budget once; `--fresh-session` explicitly authorizes a recorded handoff after `session_lost`. |
| `forge satisfied --note TEXT` | Operator only; the only path to `OWNER_SATISFIED`. |
| `forge evidence ID` | Task evidence path and summary. |
| `forge canary --workdir DIR [--max-batches 2] [--verify]` | §10. |

# 8. Engine semantics

## 8.1 State (sqlite WAL, `BEGIN IMMEDIATE` per transition)
- `runs(id, state, started, pid, proc_start)`
- `batches(id, n, state, input_fp, accepted, role_attempts json)`
- `tasks(id, batch, kind, difficulty, work_key, req_ids json, depends_on json, state, prior_state, attempts, fix_rounds,
  escalated, rebases, session_id, session_profile, base_commit, red_commit, candidate_commit, candidate_tree, blocked json)`
- `calls(id, task, role, profile, account, argv_sha, preset_sid, session_in, session_out, pid, pgid, proc_start, actual_model,
  status {launching,running,done,interrupted}, outcome, started, ended, raw_dir)`
- `accounts(id, state {available,unknown,waiting}, recheck_at, k, last_ok)`; `profiles_disabled(profile, reason, outcome, at)`
- `cancels(target, requested)`; `commits(task, candidate, tree, old_head, state {pending,done})`; `owner(satisfied_at, note)`
- One controller per project: `flock(LOCK_EX|LOCK_NB)` on `controller.lock`; second controller exits 2.
- Every artifact: tmp → fsync → rename → dir fsync before the db row referencing it.

## 8.2 Task states

```
PROPOSED -(architect)-> READY | SUPPRESSED_DUP
READY -> PLANNING -> PLANNED -> [bug: RED_WRITING -> RED_CHECK] -> IMPLEMENTING -> CANDIDATE -> GATING -> REVIEWING -> INTEGRATING -> COMMITTED
RED_CHECK fail -> RED_WRITING (same session, +1 fix round)
GATING fail | REVIEWING CHANGES -> FIXING (resume exact session_id) -> CANDIDATE
INTEGRATING main moved -> REBASING -> CANDIDATE (new candidate commit; gate + fresh review again) | BLOCKED(base_conflict)
budget exhausted -> ESCALATING (once: strong profile, fresh session, handoff.md = diff + findings + gate logs) -> FIXING (resume the
  escalation session) ... -> BLOCKED
any active -> BLOCKED(config_block | capability_block) when S(role, task) is empty
any active -> WAITING(prior_state, role, recheck_at) only for nonempty S with all accounts waiting -> prior_state after admission
any active -> CANCELLED (resume via unblock) | INTERRUPTED (recovery §8.6) -> prior_state | BLOCKED(session_lost)
READY with deps not COMMITTED -> not schedulable; dependent of BLOCKED -> DEP_WAIT (READY when the dependency commits)
```

BLOCKED record: `{cause, outcome, last_calls[], evidence_paths[], preserved: {ref: refs/forge/tasks/<id>/<n>, tree, workspace},
unblock_condition}`. Non-task roles (po_read, po, architect, adr_review) have `role_attempts` per batch counting timeout,
turn_limit, unknown_fail, schema errors after protocol retry; exhaustion → batch BLOCKED (run exit 2, same record shape).

## 8.3 Role routing (static)

| Role | Tier | Family rule | Session |
|---|---|---|---|
| po_read (per source doc/section) | strong, `image` if images attached | – | fresh |
| PO | strong | – | fresh |
| architect | strong | – | fresh |
| ADR review (only if ADRs added) | strong | ≠ architect | fresh |
| easy plan / hard plan | normal / strong | – | fresh |
| easy implement / hard implement | cheap / normal, needs `resume` | – | fresh, SID persisted |
| red test + fixes | same profile | – | resume exact SID |
| easy review / hard review | normal / strong | ≠ implementer (and ≠ escalation profile family) | fresh |
| escalation | strong, needs `resume` | – | fresh handoff, then resume its own SID |

Configured roster is not proof of route availability. Apply S(role, task) after all filters: no compliant reviewer/image reader/
resume implementer/escalation route → explicit BLOCK; compliant routes temporarily account-deferred → timed WAIT (§5.3).
Review after escalation excludes both original and escalation implementer families. Never self-review or silently hand off fixes.

## 8.4 Batch cycle

1. **Manifest** (deterministic): each source `.md` with sha, title, headings, and **anchors**: every Markdown table data row
   (`path#table<i>.row<j>`), every Given/When/Then block or `Scenario` heading (`path#scenario<k>`), every linked image (sha, magic
   check PNG/JPEG/GIF/WEBP, ≤ 20 MB). Docs > 200 KB are split by top-level headings into sections (deterministic).
2. **po_read** (PO stage, recorded): for each source doc/section whose `(sha, schema version)` has no committed digest in
   `docs/forge/digests/<sha>.json`, one strong read-only call with the **full body** in the prompt and its linked images attached
   as image payloads. Output `{reqs:[{id, text, anchors[], kind}], images:[{sha, observation}]}`. Controller rejects (protocol)
   unless every anchor is covered by ≥1 req and every attached image sha has a non-empty observation. Digests record the call id,
   actual model and image shas; they are committed with the batch architecture commit and reused by sha.
3. **PO** (strong, read-only, `/work` = export of main). Prompt contains: INDEX.md, all current digests, bodies of
   `docs/forge/implemented/*.md` and `docs/forge/intent/*.md`, test inventory (test files + `test_*` names via `ast`), last N=10 task
   evidence summaries, blocked/suppressed list. Inventory is discovery only, never behavioral evidence. Within this PO stage,
   a read-only selection call returns `read_refs:[{req_id, test_paths[], evidence_paths[]}]`: include tests/evidence linked by
   implemented docs/task records, plus additional relevant tests chosen from the inventory. Controller unions mandatory linked
   paths, validates containment in the pinned export (external evidence only controller-known immutable artifacts), and loads
   **actual full test file bodies**, including assertions/helpers, and linked evidence content before comparison.
   Final comparison calls receive these bodies with `{req_id, path, commit, blob_sha, content_sha256}`; external artifacts use
   `{artifact_id, content_sha256, producing_call_or_gate}`. Persist a controller-generated supplied-content manifest and exact
   input with the call; output `evidence_refs` must reference supplied hashes, not merely a filename or claimed tool read.
   DEFAULT bound: 32 selected files and 200 KiB of test/evidence content per requirement; group requirements sharing files into
   comparison calls of at most 200 KiB, deduplicating identical blobs. No repository-wide dump or extra routing service.
   If relevant content exceeds the bound, is absent, or selection finds no tests, comparison records `evidence_gap` and may
   say missing/partial/conflict but **cannot** say implemented. Missing/unreadable linked required evidence is explicitly
   recorded; an unexpected export/artifact read failure blocks the PO stage (`env_block`), never fabricates a comparison.
   An implemented claim requires relevant supplied test body and linked result evidence for that condition; the PO must cite
   the pertinent assertion/behavior, not infer proof from names. This enforces consumption/provenance, not model comprehension.
   Chunk calls emit comparison rows only. One final PO task-synthesis call consumes the merged total comparison and compact
   supplied-content references, then produces the single batch task list (0–6 globally; no per-chunk task padding).
   All calls stay strong under role PO with its existing timeout/attempt/protocol budgets; successful chunk calls do not
   consume failure attempts. Selection/comparison records remain part of the final call's evidence. Combined output is
   `{comparison:[{req_id, status, evidence_refs[], evidence_gap?}], tasks:[{title, kind,
   difficulty, req_ids[], source_refs[], target_paths[], acceptance[], depends_on[]}], shortfall_reason?, gap_summary}`.
   Comparison must be total over req ids; each task cites ≥1 req whose status ≠ implemented. Prompt text tells the PO that source
   material is untrusted data (risk reduction only, not isolation).
   - `work_key = sha256(kind + sorted(req_ids) + sorted(target_paths))`; key equal to a BLOCKED/SUPPRESSED key with unchanged
     `input_fp` → SUPPRESSED_DUP.
   - 0 tasks → `NO_JUSTIFIED_WORK`: idle, exit 0, "no justified work; owner satisfaction not established".
4. **Architect** (strong, read-only): ADRs + per-task `design_notes`, `doc_targets[]`, `depends_on[]`. Forge writes new
   `docs/forge/adr/NNNN-*.md`; supersession only by a new ADR (Forge edits the old status line). ADR review if any ADRs →
   architecture commit via §8.7 CAS before any task workspace exists.
5. **Tasks** per §8.2. Workspace = `git clone --no-hardlinks --branch main <target> ws/<task>/src`, then `remote remove origin`.
   Plan: easy short; hard requirements/design/steps/test plan/doc_targets. Implement prompt: plan, design notes, protected paths,
   red tests (bugs). Review: fresh export of the candidate commit (read-only). Review output `{verdict, tree, checklist{intent_match,
   adr_consistent, docs_updated, tests_meaningful, no_dead_code, naming_clear, error_handling, no_scope_creep,
   regression_first(bugs)}, findings[]}`; PASS needs every item pass or n/a+reason.
6. **Snapshot → candidate commit** (controller only): `GIT_INDEX_FILE=idx/<task> git read-tree <base>`; `git --work-tree=<src> add -A`;
   `write-tree` → T; Forge regenerates INDEX.md in `src` before the snapshot; `commit-tree T -p <base> -m "<task message>"` with
   fixed author/committer date = snapshot time and trailers `Forge-Task`, `Forge-Tree`; store under `refs/forge/tasks/<id>/<n>`;
   persist `candidate_commit, candidate_tree`.
7. **Gate** (all deterministic, on `gate-<n>` = `git archive <candidate> | tar -x`):
   - (a) diff base..candidate touches no protected path except Forge-written new ADRs; (b) every `doc_target` changed;
   - (c) `docs/forge/tasks/<id>.md` has Plan / Changes / Tests / Docs / Evidence; (d) `lint` and `test` exit 0 in the sandbox;
   - (e) bugs: §8.5 green check; (f) **post-gate content check**: re-snapshot `gate-<n>` with a fresh private index; tree must
     equal T (ignored files such as `__pycache__`, `.pytest_cache` allowed per the candidate's own .gitignore; any tracked
     modification, deletion or new non-ignored file → `task_fail` "gate mutated candidate");
   - (g) review PASS, reviewer `tree` == T, family rule held, and a re-snapshot of `review-<n>` equals T.
8. **Integrate**: §8.7. Then `git notes --ref=forge add` (idempotent) with gate/review call ids and sandbox label.
9. **Batch close**: `accepted` = COMMITTED count. `input_fp = sha256(tree(main restricted to sources + docs/forge/intent +
   docs/forge/implemented + configured test_paths + linked evidence blobs) + sorted unblock notes + enabled roster ids)`. If the last `stall_batches` batches have accepted == 0 and
   equal input_fp → `STALLED_NO_PROGRESS`, exit 3; a later `forge run` with unchanged input_fp exits 3 immediately without model
   calls; a changed input_fp clears it. Otherwise the next batch starts immediately.

## 8.5 Regression-first (bug tasks)
1. RED_WRITING: the implementer (fresh session, SID persisted) writes only the regression test(s). Snapshot → `red_commit`.
2. RED_CHECK on an export of `red_commit`: diff base..red touches only `test_paths` (else fail); run `test_one` per declared
   node with `{junit}`. Red is valid only if pytest-style rc == 1 and the junit has ≥1 executed testcase for each node, ≥1
   `<failure>`, zero `<error>` (collection/import/setup) and zero skips for the node. rc 2/4/5, missing node, zero tests, import
   or setup errors are rejected as red proof. Evidence (call id, red_commit, junit, rc) is stored before any fix call.
3. IMPLEMENTING resumes the same SID for the fix. GREEN (gate e): blobs of the red test paths in the candidate are byte-equal to
   red_commit (changing them requires a new RED cycle), and the same nodes pass (testcases pass, no failure/error/skip).
4. Targets whose `test_one` lacks `{junit}` cannot run bug tasks: those tasks are BLOCKED `config_block` with that unblock condition.

## 8.6 Sessions, budgets, waits
- Coding budget = `plan_attempts`, `impl_attempts`, `fix_rounds`; only rows 5, 12, 14, 15 of §5.3 increment them. Waits, cancel,
  interrupt, env/config blocks, protocol (first), model_mismatch never count. Wait time is excluded from coding wall time.
- Ordinary fixes always resume the persisted `session_id` on `session_profile`. There is no fresh-session fallback. Escalation is
  the only handoff: new strong session, recorded `handoff.md`, then fixes resume that session.
- Lost session (resume returns session-not-found, or an interrupted implement/fix has no SID): if no fix round has started and
  this is the initial implement, the workspace diff is saved as `evidence/interrupted-<n>.patch`, the workspace is reset to base and
  the initial implement re-runs fresh (still "fresh session per task", no budget). Otherwise → BLOCKED `session_lost`, work
  preserved; unblock only via `forge unblock ID --fresh-session`, recorded as handoff.
- **Accounts**: quota/auth/env waits are stored per account, so all profiles on that subscription wait together. At wait expiry
  (`now ≥ recheck_at`) the account becomes `unknown`. Before any call on an `unknown` account the controller runs the doctor text
  probe (timeout `probe`, max_turns 2): ok → `available` (last_ok = now), else classified normally. Accounts with no ok call in the
  last 30 min are also `unknown`. "Available" means "last call succeeded", not measured headroom.
- **Scheduler**: compute S for each ready role first; record empty-S blocks and continue independent tasks. Choose the
  lowest-numbered schedulable task with an eligible route (roster order). All S accounts waiting → task WAIT with earliest
  relevant recheck; never choose a lower tier. If no task is runnable, sleep to the minimum concrete recheck among structurally
  valid waiting roles; if none exist, return blocked/idle as appropriate, never timerless WAIT.

## 8.7 Integration, recovery and cancellation ordering
Integration (single transaction boundaries marked ▸):
1. ▸ `BEGIN IMMEDIATE`; if a cancel row targets the task → CANCELLED, stop. Else if main ≠ base → REBASING. Else insert
   `commits(task, candidate C, tree T, old_head=base, pending)`; COMMIT. This is the point of no return.
2. `git update-ref refs/heads/<branch> C base` (CAS).
3. ▸ mark `done`, task COMMITTED.
REBASING: `git merge-tree --write-tree --merge-base=<base> <main> <C>`; conflict → BLOCKED `base_conflict` (candidate ref
preserved, unblock = operator resolves main or `unblock`); clean → new candidate commit on main (new ref n+1), new gate, **fresh
independent review**; `rebases` budget exceeded → BLOCKED.

Recovery at run start (after flock):
- For each call with status launching/running: if `pgid` exists with matching `proc_start`, `killpg(SIGTERM)`, 10 s, `SIGKILL`,
  wait ≤ 5 s; still alive → run refuses with `env_block` "surviving writer pid X" (exit 2). Then mark `interrupted`, task
  INTERRUPTED → prior_state, resume rules of §8.6 (SID present → resume the same SID with "previous turn interrupted").
- `commits.pending`: main == C → mark done; main == old_head → redo step 2 with the same C (no second commit); otherwise → REBASING.
  A cancel arriving after step 1 is reported as "already accepted" and does not undo the commit.
- Candidate commits are created and stored under refs before their ids are referenced; a crash before the db update leaves an
  unreferenced ref that recovery re-links by `Forge-Task` trailer + tree or ignores (it is never integrated without a gate).
- Prune orphan gate/review exports. Each ordering above has a SIGKILL test (AC-23/24/25) killing a child controller at an injected
  `FORGE_TEST_CRASH_AT=<point>` and asserting one writer, preserved work, no acceptance after cancel, exactly one accepted commit.

# 9. Phased handoff (build pool = strong-only Sol/Opus via pkr_worker task-local config; not the runtime roster)

| Phase | Deliverable | Gate (exact) |
|---|---|---|
| P0 | pyproject, cli skeleton, config, state (lock, schema, txn, atomic), docs/architecture.md + ADR-0001 (boundary) | `python3 -m venv .venv && .venv/bin/pip install -e . pytest && .venv/bin/python -m pytest -q tests/test_config.py tests/test_state.py`; AC-01,16,22,36 |
| P1 | sandbox, runner, adapters, classify, doctor (+PNG) | `.venv/bin/python -m pytest -q tests/test_sandbox.py tests/test_runner.py tests/test_adapters.py tests/test_classify.py tests/test_doctor.py`; AC-04(smoke part),14,15,20,33 |
| P2 | gitops, gate, red/green, CAS, rebase | `.venv/bin/python -m pytest -q tests/test_gitops.py tests/test_gate.py tests/test_integrate.py`; AC-11–13,25,26,31,32,34 |
| P3 | engine with FakeAdapter (states, routing, accounts, budgets, sessions, cancel, recovery, stall) | `.venv/bin/python -m pytest -q tests/test_engine*.py tests/test_recovery.py`; AC-02,08–10,17–19,21,23,24,27–29 |
| P4 | ingest/anchors, po_read, PO comparison, roles/schemas, docsindex, init | `.venv/bin/python -m pytest -q tests/test_ingest.py tests/test_roles.py tests/test_docsindex.py`; AC-03–07,30 |
| P5 | real `forge doctor --smoke`, canary, full suite, independent code review, local commit | §10; AC-35 |

Every phase: full suite `.venv/bin/python -m pytest -q` passes; code and its docs in the same local commit; no Co-Authored-By
trailer; no push/remotes. Tests needing real bwrap are marked `@pytest.mark.bwrap` and skipped with an explicit reason only when
the probe fails (the P5 gate requires them to run, not skip).

Build launches (orchestrator side; product never depends on pkr):
```
python3 /home/jan/.hermes/scripts/pkr_worker.py --role code --difficulty hard --repo /home/jan/Sources/forge-next --config <strong-only.json> --prompt <phase-steering.md> --artifact <fresh.md> --run-dir <fresh-dir> --summary '<phase>' --timeout 600
python3 /home/jan/.hermes/scripts/pkr_worker.py --role review --difficulty hard --implementation-decision <code-decision.json> --config <strong-only.json> ... (separate exact-fingerprint checkout)
```
Grok is not used for build gates (pkr_worker parser mismatch). pkr_worker's own lack of isolation applies to the build only.

# 10. Real disposable native canary (P5; actual evidence only)

**Setup.** `forge canary --workdir $TMPDIR/forge-canary-<ts>` creates a fresh git repo `proj/` (no remotes):
- Python package `tally` with pytest tests and one real bug: `remove()` allows negative stock, documented as forbidden.
- `docs/source/spec.md`: a requirements table whose row 4 alone states "export rejects negative quantities", two
  Given/When/Then scenarios, and a missing feature: CSV export with the columns shown in an image. spec.md is not in INDEX.md.
- `docs/source/export-layout.png`: stdlib-generated (5×7 bitmap font) table whose column order exists only in pixels.

**Config.** Default roster restricted to smoke-passed profiles; test command `python3 -m pytest -q`; `test_one` with `{junit}`.

**Run.** `forge doctor --smoke` then `forge run --max-batches 2` (validation bound only; default stays unbounded). Bounds: §4
per-call timeouts; watchdog 4 h of active (non-wait) time; waits resumable by re-running the same command; canary stops and
reports BLOCK on `BLOCKED_CONFIG` or any blocked task.

**PASS (`forge canary --verify`, reads db + git only):**
- doctor smoke records exist for every profile used, with sandbox probe pass.
- Batch 1: ≥1 easy and ≥1 hard task COMMITTED; the bug task has red evidence (assertion failure on base, junit) recorded before
  its fix call, and green on the candidate.
- po_read digest for spec.md covers the row-4 anchor; PO comparison includes that req; the CSV task's evidence cites the image
  sha and the po_read call; implemented column order equals the pixel order (checked against the generator's secret order).
- Each commit: code+docs together, reviewer family ≠ implementer, accepted tree == tested tree == reviewed tree, sandbox label.
- Batch 2 began with a fresh PO call whose input included batch-1 implemented docs; it may end `NO_JUSTIFIED_WORK`.
- Every call record has provider-reported model or the flag "provider did not report model"; configured-but-unused profiles are
  listed separately.

**Evidence.** Bundle state dir (db, calls raw output, smoke records) and `git bundle` of proj to
`~/.hermes/cache/scratch/forge-canary-evidence/<ts>/`; commit summary `docs/canary/<date>.md` in forge-next (actual
models/efforts/elapsed, wait time separately). Fixtures are never cited in it.

**Final delivery gate.** Full suite passes (bwrap tests run), canary PASS, independent different-family pkr_worker review of the
exact fingerprint PASS, then local commit.

# 11. Risks (honest, unresolved by design)
- R1. Inside an agent call, the in-use harness's credential dir is readable by the agent and its tool commands (required to
  authenticate); a hostile target could exfiltrate that one subscription token over the shared network. Not mitigated beyond
  per-harness scoping and local-only commits. Other secrets/state are not mounted.
- R2. Harness bind lists (§4) are DEFAULTs; e.g. claude writing `~/.claude.json` via rename may fail on a file bind. Smoke detects
  this; the profile then stays ineligible until the operator adjusts binds. bwrap depends on the AppArmor bwrap profile staying.
- R3. Model/effort/image/resume/SID-timing behaviour of all three CLIs is unverified until real smokes; Haiku-high, Grok, Claude
  images may stay ineligible, possibly leaving cheap tier = Luna only.
- R4. Codex may not report the actual model; recorded as configured, not provider-confirmed.
- R5. Quota/auth/network patterns are heuristics on error fields only; misclassification falls to `unknown_fail` (finite) or a
  timed wait. Patterns are refined from real captured stderr, kept as FIXTURE.
- R6. Full clones per task cost disk/time on large repos; acceptable for first delivery.
- R7. LLM-chosen req ids/paths can evade dup suppression; the stall rule is the backstop. po_read coverage proves every anchor
  was addressed in output, not that the model understood it; the canary's table-row and pixel checks are the live evidence.
- R8. Canary cost ≈ 12–18 strong/normal calls per batch plus PO selection/content chunks; it respects shared-subscription waits and may span sessions.

# 12. Review closure matrix (original review + full plan-confirmation-1.md; residual correction 2)

| ID | Closure in this plan | Planned evidence |
|---|---|---|
| PR-01 | Single enforced bwrap mode for all agent calls (incl. tool-launched commands) and gate commands; controller state/other creds/real HOME unmounted; read-only roles via ro mount + non-interactive flags; explicit exact work/input state-leaf and HOME executable/harness/toolchain exceptions, fixed aliases, disjoint protected roots and ancestor/sibling/alias rejection (§6.3); host-unisolated mode removed; failure → route disabled, no acceptance. §1, §5.2, §6 | §6.4 exact-leaf acceptance and root/sibling/alias rejection tests; doctor probe + smoke 4 |
| PR-02 | Total ordered classifier over controller outcome/native status/error fields/rc, never model text; config/env errors disable routes instead of waiting; finite unknown_fail/protocol strikes; role_attempts for non-task roles; account-scoped waits; S computed after tier/family/capability/session filters; empty S blocks task/batch with reasons and preserved work; only transient account deferral WAITs with concrete recheck + probe; independent tasks drain before aggregate BLOCKED_CONFIG. §5.3, §8.2, §8.6 | AC-20/21 tests: Luna/Sol-only reviewer, missing image/resume, escalation family exclusions → BLOCK; real family-compatible deferred account → timed re-admission |
| PR-03 | `resume` capability required for implementers; ordinary fixes always resume exact SID; no fresh fallback; escalation only handoff; lost session → BLOCKED unless operator `--fresh-session`; resume smoke checks pinned model and write permission, read-only smoke checks non-interactive denial. §5.4, §8.3, §8.6 | AC-17 tests; smokes 3–4 |
| PR-04 | Durable launch order (row → spawn → pid/pgid/start-time → SID on first event; claude preset SID); pid-namespace teardown on controller death; recovery kills surviving groups or refuses; pending row holds the exact candidate commit id before CAS; cancel/accept race defined. §5.1, §8.7 | AC-23–25 crash-point tests |
| PR-05 | Candidate = commit (tree = fingerprint) made via controller-owned index; gate/review on `git archive` exports; post-gate and post-review re-snapshot equality; same commit accepted; external main move → merge-tree, new candidate, re-gate, fresh review, conflict BLOCKED. §8.4.6–8, §8.7 | AC-25, 26, 31, 32 tests |
| PR-06 | Recorded po_read stage: full bodies + image payloads, deterministic anchor/image coverage, committed digests with provenance; PO inventory discovers relevant tests; required bounded body+linked-evidence delivery with pinned blob/content provenance before total comparison; absent/over-bound evidence cannot support implemented; assertion-only changes reach PO and invalidate unchanged-work suppression. §8.4.1–3 | AC-03–05 tests: identical names/docs/summaries with assertion-only delta changes supplied content; missing evidence rejects implemented; canary row-4 + pixel checks |
| PR-07 | RED_WRITING/RED_CHECK states before the fix; test-paths-only red diff; junit-based assertion-failure proof rejecting missing/zero/import/setup/pass-on-base; red blobs unchanged in green; evidence ordered. §8.5 | AC-12 tests; canary bug task |

PR-01/PR-02/PR-06 are author-closed at plan level by this correction; independent confirmation is still required.
PR-03/PR-04/PR-05/PR-07 retain the prior independent plan closure unchanged. No implementation/live proof is asserted here.
