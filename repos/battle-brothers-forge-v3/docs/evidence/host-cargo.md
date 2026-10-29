# Host cargo evidence

Work directory: `/home/jan/Sources/battle-brothers-forge-v3`. Toolchain PATH prefix: `/home/jan/.rustup/toolchains/stable-x86_64-unknown-linux-gnu/bin`. `CARGO_TARGET_DIR` was the repo `target/`. Flags: `--locked --offline`. No source file was edited to change the result. Log: `/tmp/bb-forge-v3-bootstrap/cargo.log`. Start 16:19:53, end 16:20:21 local.

| Command | Exit |
| --- | --- |
| `cargo test --workspace --locked --offline` | 0 |
| `cargo build --workspace --locked --offline` | 0 |
| `cargo fmt --all --check` | 1 |
| `cargo clippy --workspace --locked --offline --all-targets -- -D warnings` | 101 |

Test results: `bb_game` unittests 1 passed; `invalid_move_feedback` 1 passed; `bb_rules` lib 2 passed; `headless_smoke` bin 0 tests; `rules` integration 18 passed; one further suite 0 tests. No test failed.

Fmt exit 1 is inherited layout drift in `main.rs`, `render.rs`, `headless_smoke.rs`, `ai.rs`, `lib.rs`, `rules.rs`, `scenario.rs`, and `tests/rules.rs`. Not corrected.

Clippy, denied by `-D warnings`, three errors, not corrected:

- `crates/bb_rules/src/ai.rs:37` collapsible if
- `crates/bb_rules/src/ai.rs:53` collapsible if
- `crates/bb_rules/src/hex.rs:73` useless `vec!`

The operator lint command stays empty. These host diagnostics are not a sandbox cargo proof and not a game-fidelity result.

## Authentic image open

On 2026-10-23 the bootstrap session opened `docs/source/reference-images/steam-ss-0.jpg` (SHA256 `9818ee141010b66cc5a096b03e239a07d45e048082f0bf944fad629e7b1c91e7`, 1920x1080). The frame shows a hex field after a fight: casualties, a turn queue, an injury log naming a fleeing brigand, and a selected fighter HUD with bars and the word Confident. That is store-reference inspection, not an original-runtime capture and not the doctor synthetic image probe.
