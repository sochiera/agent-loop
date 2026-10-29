# Product brief: independent mercenary tactics game

## Target and boundaries

Build a new, playable, native Linux/x86-64 desktop game **from zero** for the current PC. Rust is preferred, but choose a working technology if a different one demonstrably serves the game better. Do not import source code, binaries, art, Cargo manifests/lockfiles, tests, or Forge task state from `/home/jan/Sources/battle-brothers-forge-v3` or any older Battle Brothers project. Historical work may inform a question, not become this project's implementation. Use original names, assets and UI; do not copy proprietary code or art. Keep dependencies installable/reproducible on this PC. Local-only delivery: no push, deployment or account changes.

## Player experience

The player commands a small mercenary company on a strategic map: travel to a contract, encounter an enemy party, enter a tactical turn-based hex-grid battle, use distinct units and attacks with movement/AP/terrain/line of sight, see clear consequences (damage, wounds/deaths, victory/defeat), collect rewards, return to the strategic layer and continue. Make a usable mouse/keyboard UI with readable state, feedback, menus and a quit path. Add progression and additional mechanics iteratively, keeping an end-to-end playable loop at every accepted milestone. Do not declare the entire game complete after one minimal encounter.

## Execution requirements

The Technical Lead must select **substantial, coherent vertical slices**: one bounded task should normally take a player-visible journey across UI, rules, persistence/state, tests and needed docs, rather than splitting rules and UI into separate tiny tasks. The first slice must launch a real window on this Linux PC and offer a genuinely playable combat encounter from start to finish, even if the strategic layer initially uses a simple menu. Later slices extend the company/contract/overworld loop while preserving playability. Prefer a few meaningful tasks to a long queue of microtasks; scope each task so one executor and an independent reviewer can complete and validate it. Bugs and necessary refactors can be separate tasks when justified.

Each accepted slice must include automated tests for deterministic mechanics, a project-native build/test command, accurate build/run instructions, and a real GUI smoke on this machine where feasible. Identify approximations and missing mechanics honestly. Reviewer checks the exact code and the user-visible flow. Forge may not mark DONE based only on tests or a task count; verify the full player experience and native window. Keep source of truth and implemented-state docs distinct.
