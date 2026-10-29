# Import manifest

Clone: `git clone --no-hardlinks --branch vertical-slice` from `/home/jan/Sources/battle-brothers-rust` into `/home/jan/Sources/battle-brothers-forge-v3`. `.git` is a real directory. HEAD `ed4127358bb32dfad1379462561f2c929525752f`. Tree at clone `234d9c5303e42b54947ae402ebda2a3b746be3fb`. The clone-created `origin` remote was removed so this repo has no remote. Source index at clone and at the continuation inventory was `c311bc17dbe8a0fa19047835a88dccf80823d95325b0edf630e6764b6c23c506`. Untracked `.hermes/` was not copied.

Review authority: `/home/jan/.hermes/cache/scratch/bb-forge-v3-preparation/import-plan-review-v2.md` VERDICT PASS, bootstrap only, 2026-10-23T16:15:20+02:00, plan SHA256 `9e4cf4ffa0cc976c6097480d4f980a5a230989f3fc63d0e14f8418b456d54f32`. Historical `/home/jan/.hermes/cache/scratch/bb-forge-v3-preparation/import-plan-review.md` remains BLOCK. Plan text: `import-plan-v2.md`. Inventory hashes: `import-plan.md`.

## Layer A included

Re-hashed from `/home/jan/.cache/pkr-worktrees/bb-rust-rule-review-001` immediately before copy. Post-copy bytes match.

| Path | SHA256 |
| --- | --- |
| README.md | `6a727c8fb3993b5099ab16b1032ac179e802ec7e4a07bc3317d791e0bad714f6` |
| crates/bb_game/src/main.rs | `6ab1fb1bd6d4ff6d5eee22810d5d3334b4bb51972a54d986999e6a582027ebf0` |
| crates/bb_rules/src/lib.rs | `cc7ecf83b0deca655c3940c5184d9b5eb54e961cf6b153620fc36d8e4ef83a32` |
| crates/bb_rules/src/rules.rs | `90689195563b59f81ac941d431089b35e9b0d8f7147f0ff05d085317a8905e5e` |
| crates/bb_rules/tests/rules.rs | `bebb9440166e6d42ee630387d0f9526e07268038313f968a74346c4c6039d12a` |
| crates/bb_game/tests/invalid_move_feedback.rs | `e6161fae3c84f090b1c7577d7310fac8fde213455f28d252857532344d7383d5` |

Baseline blobs for the five paths that existed at HEAD matched the plan's baseline column before copy. `invalid_move_feedback.rs` was absent at HEAD.

## Layer B not included

Ten digests re-hashed and deferred. Absolute paths and reasons: [layer-b-deferred.md](layer-b-deferred.md). Company and save improvements are not in this baseline.

## Not imported

`.hermes/` host state, kingdom or Total War briefs, original game assets, and any unreviewed worktree. `bb-rust-loop-impl-001` company.rs and save.rs differ from layer B and were not used.
