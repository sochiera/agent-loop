# Toolchain and operator bindings

No global PATH or config was changed. No separate wrapper directory was added. The reviewed candidate runs cargo through `/usr/bin/python3 -c`, which sets `PATH`, `RUSTUP_HOME`, `CARGO_HOME`, and `CARGO_TARGET_DIR=/tmp/cargo-target`, then execs the stable toolchain cargo. `exec.ro_binds` is only the toolchain directory and `/home/jan/.cargo/registry`. `gate_network` is false. Lint in the operator file is an empty array because clippy is not a proven sandbox gate. Host clippy and rustfmt do exist; their host results are in `docs/evidence/host-cargo.md`.

| Binding | Path |
| --- | --- |
| Rust toolchain | `/home/jan/.rustup/toolchains/stable-x86_64-unknown-linux-gnu` (cargo, rustc, rustfmt, cargo-clippy present on that prefix) |
| Cargo home | `/home/jan/.cargo` |
| Host test target | `/home/jan/Sources/battle-brothers-forge-v3/target` |
| Codex runtime_dir | `/home/jan/.codex/packages/standalone/releases/0.159.3-x86_64-unknown-linux-musl` |
| Codex binary | `/home/jan/.codex/packages/standalone/releases/0.159.3-x86_64-unknown-linux-musl/bin/codex` |
| Claude binary | `/home/jan/.local/share/claude/versions/2.1.287` |
| Grok binary | `/home/jan/.grok/downloads/grok-1.0.52-linux-x86_64` |
| Interpreter for forge CLI | `/usr/bin/python3` with `PYTHONDONTWRITEBYTECODE=1` |

Codex release file hashes (not a smoke, not `actual_model`):

| Relative | Absolute | SHA256 |
| --- | --- | --- |
| `bin/codex` | `/home/jan/.codex/packages/standalone/releases/0.159.3-x86_64-unknown-linux-musl/bin/codex` | `8bf204b36a2f6dd0dab73aa2f639892e67ef9ac8befccb4a05b1496ebf25c479` |
| `bin/codex-code-mode-host` | `/home/jan/.codex/packages/standalone/releases/0.159.3-x86_64-unknown-linux-musl/bin/codex-code-mode-host` | `fe64bd08c7844ca1fca91d8cb825e62012a03a05ae8040c635bbb61e5be0b4c5` |
| `codex-path/rg` | `/home/jan/.codex/packages/standalone/releases/0.159.3-x86_64-unknown-linux-musl/codex-path/rg` | `e62198eb19b136b88c330af83647b5a962cb99b6b1f066758568f12de1974849` |
| `codex-resources/bwrap` | `/home/jan/.codex/packages/standalone/releases/0.159.3-x86_64-unknown-linux-musl/codex-resources/bwrap` | `77360cb751ccedc5971391444ac86a8a33c15b04d6b4a6fe45f5d25496e62c4c` |

Claude binary SHA256 `3920489a5109cff5786a1a392c25277408ff22bc796d5edb9c16a60e5a1718f0`. Grok binary SHA256 `1668414b047e1050a96d25fe17dfc3efe63190fbf66d8cc1c3de8633caf50d11`.

Reviewed candidate copied unchanged to `docs/provenance/operator/battle-brothers-forge-v3.toml`, SHA256 `1260d1affb84ab6d7f63b199dbc23caf752b529e9caf74bfc5bd9ffb9b1011b9`. Installed copy, when present, is mode 0600 at `/home/jan/.config/forge/projects/battle-brothers-forge-v3.toml`.

Forge state for the bootstrap checks uses `XDG_STATE_HOME=/home/jan/.hermes/cache/scratch/bb-forge-v3-preparation/runtime-state`, so the engine state root is `runtime-state/forge/battle-brothers-forge-v3`. That variable is process-local. The login environment was not edited.

Schema read from forge-next `9df17d442b50deeca6a30086735da125519cb6ff` `src/forge/config.py`. `agent-loop-forge-v3` reports version `3.0.0` and its `config.py` and `doctor.py` matched forge-next at preparation time; `cli.py` differs only by the version string. Routing code was the same unimplemented admission. Bootstrap doctor uses the v3 sources only if that readback still says `3.0.0` at the check. A reviewed pre-write admission change is still pending for the parent. This file does not claim Codex or Grok smoke capabilities.
