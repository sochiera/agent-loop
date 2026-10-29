# ADR 0003: controller-owned durable lifecycle

Status: accepted for P3 implementation under the approved plan.

The controller owns scheduling, tier/family/session admission, account timers,
finite budgets, exact-candidate integration and owner commands. Native agents
supply content through a role driver whose production transport is the existing
sandboxed runner. P4 supplies prompts, schemas, ingestion and docs generation.
No production simulator fills an unavailable content role.

Each role operation has a durable call ID before launch. A completed result can
be consumed again after a crash without launching a second writer. Shared account
and profile effects have an idempotency receipt; failure budgets and operation
removal commit together. Handoff intent is durable before new-session work.
Ordinary fixes cannot select a peer or silently replace a lost session.

One task runs until it commits or becomes blocked/waiting. Independent tasks may
then continue. Empty structural routes terminate explicitly; valid deferred routes
carry a finite account recheck. Unknown accounts require a bounded native probe.
There is no downgrade, model-controlled polling, or inferred owner satisfaction.

PID/start identity and process groups govern recovery. Bwrap's PID namespace and
parent death teardown cover controller SIGKILL; ambiguous surviving groups block.
Cancellation is serialized with the P2 acceptance point. Disposable exports may
be pruned, but workspaces, immutable results and preservation refs remain.

The unchanged-input key includes actual test assertions and linked result bytes.
Failed-call logs alone cannot defeat duplicate suppression or stall detection.
P3 lifecycle tests label provider content as FakeAdapter fixtures and execute
runner/bwrap/git/process-kill behavior for real. Live native reasoning remains P5.
