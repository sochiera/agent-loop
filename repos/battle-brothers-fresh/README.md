# Hireling's Oath

An original, turn-based mercenary tactics game for Linux desktops, written in Rust.
Product intent lives in [`docs/source/brief.md`](docs/source/brief.md); what is actually built
(and what is not) is tracked in [`docs/implemented-state.md`](docs/implemented-state.md).

The game has two modes:

* **New Campaign** - run a mercenary company: take a contract from the town's board, travel to the site
  (days pass, wages are paid), fight a tactical hex battle on a procedurally generated map against an enemy
  party scaled to the contract, collect the pay back in town, level up, heal, hire, and take the next contract.
  Lose everyone, or go bankrupt, and the campaign ends with a summary.
* **Quick Battle** - the original one-off skirmish (four mercenaries vs five bandits on a hand-made map).

## Prerequisites

* Linux x86-64 with an X11 or Wayland session (`DISPLAY` or `WAYLAND_DISPLAY` set) and a Vulkan or
  OpenGL driver (Mesa/NVIDIA; software Vulkan "lavapipe" also works).
* Rust toolchain (`cargo`, edition 2021; developed on 1.96).
* No `-dev` packages: windowing and GPU libraries (X11, Wayland, Vulkan/EGL) are loaded at run time.
* Crates are resolved from the local cargo registry; use `--offline` if you have no network.

## Build, test, run

```sh
cargo build --release --offline          # binary: target/release/hirelings-oath
cargo test --workspace --offline         # rules + UI-logic + rendering tests (no display needed)
cargo run -p app --release --offline     # opens the game window
```

(`cargo run -p app` works too; the debug profile is optimised so it is playable.)

## How to play a campaign (mouse only is enough)

1. Menu: **New Campaign** (or Up/Down + Enter). You get a company of four named mercenaries, 300 gold, day 0.
2. **Overworld**: the map shows your home town (Harrowgate) and five named sites 1-5 travel days away, the party
   marker, and on the right the company panel (name, day, gold, wages per day, roster with HP / level / XP and the
   stat bonuses earned from levels). The bottom line states the current **goal**.
3. Click **Enter Town**: the **contract board** lists 3 seeded contracts (site, enemy type, 1-3 skulls of difficulty,
   pay, travel days, arrive-by day). **Accept** one. The **Recruits** list shows 2-3 mercenaries for hire (archetype,
   level, cost, wage; the roster holds at most 6, at most 5 go into battle - click *In party / Reserve* to choose).
   **Rest a day** heals every mercenary 12 HP (wages are still due). **Depart** starts the journey (or leave town and
   click the contract's site on the map, then *Go*).
4. **Travel**: the marker walks the road; each day deducts the whole company's wages. On arrival an **encounter
   prompt** describes the enemy party: **Fight** (Enter) or **Retreat to town** (Esc, no pay).
5. **Battle**: the normal battle screen with your roster (wounded mercenaries start with reduced HP, level bonuses
   apply) on a map generated from the contract's seed.
6. **Result**: per-mercenary outcome (HP left / killed, kills, XP gained, level-up and its stat gains). **Continue**
   sends the company home. On victory the contract pays **when you are back in town**; after a defeat there is no
   pay. Dead mercenaries are gone for good.
7. Back in town: collect the pay, heal, hire, accept the next contract.
8. If nobody is left alive, or the company is bankrupt (see below), the **campaign-over screen** shows days survived,
   contracts completed and the fallen (paged with Prev/Next or the mouse wheel when long); it returns to the menu. There is no save/load yet.

**Rules in short** (all in `tactics-core`, see `src/campaign.rs`): one day passes per rest day or travel day; every day
costs the sum of all wages (gold may go negative); XP = 10 for taking part + 10 for a win + 16/20/24 per kill
(1/2/3 skulls); level thresholds 40/100/180/280/... XP; each level gives +5 max HP, +2 skill and +1 defense.
**Game over** when no mercenary is alive, or when gold is below zero and no living mercenary is fit to work
(HP at least half of max), or when the debt exceeds 300 gold.

## Controls

| Input | Action |
|---|---|
| Menu: **Up/Down + Enter** or click | New Campaign / Quick Battle / Quit |
| Menu: **Esc** | quit |
| Overworld: click a site, then *Go*; click the town or press **Enter** | pick a destination / open the town |
| Overworld: **Esc** | pause menu (Resume / Abandon / Quit); a campaign is not saved |
| Encounter prompt: **Enter** / **Esc** | Fight / Retreat to town |
| Town: buttons; **Esc** leaves town, **Enter** departs | accept, hire, rest, party selection |
| Campaign result / game over: **Enter** | Continue / return to the menu |
| Battle: mouse hover on a hex | path and AP cost (reachable hexes), or hit chance (enemy); terrain info in the side panel; hovering a unit shows its stats |
| Battle: left click on a highlighted hex | move the active unit along the cheapest path |
| Battle: left click on an enemy | attack it (melee if adjacent, ranged otherwise); if impossible you are told why ("target not visible", "out of range", "not enough AP") |
| Battle: left click on any unit | select it for inspection (the active unit is auto-selected each turn) |
| Battle: **Space** / **Enter** / *End Turn* button | end the active unit's turn |
| Battle: mouse wheel over the window | scroll the combat log |
| Battle: **Esc** | pause menu (Resume / Abandon / Quit); Esc again resumes |
| Quick Battle result: **Enter** / *Return to Menu* | back to the menu (the next battle starts fresh with a new seed) |

How to read the battle screen: the top strip is the turn order (current unit ringed in gold, then the rest of
the round, then **NEXT** round). Blue units are yours, red are the enemy. Under each unit are its HP
(green) and armor (grey-blue) bars. White-tinted hexes are reachable this turn, with their AP cost; pulsing
red outlines mark enemies you can attack right now. Forest hexes and rock block ranged attacks passing
through them; hills give +10 to hit when attacking from them and -10 when attacking uphill; forest gives +15 defense.

## Command-line flags

```
hirelings-oath [--smoke-frames N] [--autoplay | --autoplay-campaign] [--seed S] [--headless]
               [--screenshot FILE.png] [--shot VIEW=FILE.png]...
```

* `--smoke-frames N` - open the real window, run at a fixed 1/60 s step until N frames have actually been
  *presented* to the display, then exit 0 (non-interactive check). Skipped frames (timeout, occluded,
  surface lost) do not count; ~120 consecutive skips, a GPU error, or closing the window early end the run
  with exit 1. Exit codes: 0 ok, 1 runtime/presentation error (e.g. no GPU adapter), 2 bad
  arguments, 3 no display server reachable.
* `--autoplay` - the AI controls **both** sides; a quick battle starts automatically (at an accelerated
  pace). Example: `target/release/hirelings-oath --smoke-frames 3000 --autoplay`.
* `--autoplay-campaign` - an autopilot plays a whole **campaign** through the same screens: opens the town, hires,
  accepts contracts, travels, fights (AI on both sides), reads the result, collects pay, heals, repeats until the
  company is lost. Example: `target/release/hirelings-oath --smoke-frames 6000 --autoplay-campaign --seed 3`.
* `--seed S` - seed for the campaign / dice (default: clock). Same seed -> same contracts, maps and enemy parties.
* `--headless` - no window: run the frames with the software renderer only (works without a display).
* `--screenshot FILE.png` - write the last rendered frame as PNG (works with or without `--headless`).
* `--shot VIEW=FILE.png` (repeatable) - write a PNG the first time that view has been on screen for a moment.
  VIEW is one of `menu overworld travel encounter notice town battle result gameover pause`. With
  `--autoplay-campaign` this captures the overworld and a campaign battle from one run:
  `target/release/hirelings-oath --headless --autoplay-campaign --seed 3 --smoke-frames 5000 --shot overworld=o.png --shot battle=b.png`
  (drop `--headless` to capture from the real window when a display is available).

## Layout

* `crates/tactics-core` – all rules, no graphics dependency, deterministic given a seed: hex
  grid, terrain, pathfinding, AP, line of sight, hit chance and damage, turn order, AI decisions,
  win/loss, the hand-authored skirmish scenario. `cargo run --release -p tactics-core --example sim -- 1000`
  plays AI-vs-AI battles and prints win rates (balance probe).
* `crates/app` – the desktop front end (`winit` window, software-rendered UI uploaded through `wgpu`).
  It only renders core state and turns input into core commands.
