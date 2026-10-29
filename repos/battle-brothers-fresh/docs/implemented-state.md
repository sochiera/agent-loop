# Implemented state

Status of the code against [`docs/source/brief.md`](source/brief.md). The brief is the source of truth;
this file only records what exists. Last updated: slice T2 (strategic campaign loop).

## Implemented (slice T1: tactical encounter)

**Application**
* Native Linux window ("Hireling's Oath", `winit` + `wgpu`; the whole UI is drawn by a small software
  rasteriser with a built-in 5x7 bitmap font, so there are no external asset files).
* Battle, pause menu (Resume / Abandon / Quit), Quick Battle result screen (Victory/Defeat, survivors with HP,
  casualties, rounds, enemies defeated; Return to Menu / Quit).
* Smoke / tooling flags: `--smoke-frames` (counts only frames really presented; bounded retries, presentation errors give exit 1), `--autoplay`, `--seed`, `--headless`, `--screenshot`.

**Battle (rules in `tactics-core`, deterministic from a seed)**
* 12x10 pointy-top hex map, hand-authored: plain, forest, hill, rock, water.
  Move cost: plain 2, forest 4, hill 3 AP; rock/water impassable.
* 4 player units (Swordsman, Shield-bearer, Archer, Halberdier) vs 5 enemies (3 Raiders, Bandit Archer, Brute).
* 9 AP per turn. Cheapest-path movement (Dijkstra) that never enters impassable hexes or hexes occupied by living units.
  Melee attacks cost 4-5 AP, bows 5 AP. Turn order: each round every living unit acts once, highest initiative first.
* Line of sight for ranged attacks (range 2-6): rock and forest between shooter and target block; the endpoints' own hexes do not.
  Adjacent targets are always attackable (archers fall back to a dagger).
* Hit chance = attacker skill + weapon modifier - defender defense - target forest bonus (+15) +/- hill (attacker on hill +10,
  attacking uphill -10) - 3 per hex beyond range 2 for bows, clamped to 5-95%. Seeded PCG32 roll; damage rolled in the weapon's range.
* Armor absorbs damage before HP; each weapon has an armor-piercing percentage that goes straight to HP. At 0 HP a unit dies,
  leaves the turn order and becomes a corpse marker that does not block movement.
* Enemy AI (also used for both sides in autoplay): attack the best available target; else move to the hex from which the best
  attack is possible (ranged units seek a firing position, melee units get adjacent); else advance toward the nearest opponent by
  terrain-aware distance; else end the turn. It only issues commands the core accepts (tested).
* Battle ends when one side is eliminated.

**UI feedback**
* Click to select, auto-select of the acting unit, reachable-hex highlight with AP costs, hover path + cost, hover hit-chance
  tooltip with its breakdown, "target not visible / out of range / not enough AP" messages (with a red blocked sight line),
  pulsing outline on attackable enemies, unit panel (HP, armor, AP pips, defense, initiative, skills, weapons), turn-order strip,
  scrolling combat log (mouse wheel), floating damage / MISS numbers, hit flash, arrow-streak for shots, movement animation,
  death fade, paced enemy turns.

**Tests**: `cargo test --workspace --offline` – core rules (hex maths, pathing, AP, LOS, hit chance, damage, death, AI legality,
headless full-battle reproducibility and bounded length), UI state machine (menu/pause/result flow, click-to-move, rejected clicks,
autoplay to result, all screens render), software rasteriser, and an off-screen GPU blit test (skipped if no wgpu adapter).

## Implemented (slice T2: campaign loop)

**Flow (mouse + keyboard)**: main menu (New Campaign / Quick Battle / Quit; Up/Down+Enter or click) -> overworld
-> town (contract board, recruits, roster, rest) -> travel -> encounter prompt (Fight / Retreat) -> battle ->
campaign result (per-mercenary outcome, Continue) -> return trip -> pay in town -> hire / heal -> next contract;
or the campaign-over screen -> menu. The full loop is playable with the mouse alone (covered by an app test that
clicks through it). Quick Battle, the pause menu and its result screen are unchanged.

**Strategic rules (all in `tactics-core`, deterministic from the campaign seed; the app only renders and sends commands)**
* Company: seeded name, 300 starting gold, day 0, four persistent named mercenaries (Swordsman, Shield-bearer, Archer,
  Halberdier), roster cap 6, at most 5 deployed per battle (player toggles *In party / Reserve*).
* Overworld: fixed region map with the home town Harrowgate and five named sites at 1, 2, 3, 4 and 5 travel days.
  Travel is abstract: town <-> site only, one day per step, the marker animates along the road. Any site may be
  visited; only the accepted contract's site holds an encounter (others say "nothing here").
* Contracts: the board always holds 3 seeded offers (distinct sites where possible): enemy type (bandit gang / archer
  ambush / brute warband), difficulty 1-3 skulls, pay (grows with difficulty and distance), and an arrive-by day. An
  offer or an accepted-but-unstarted contract lapses when the company can no longer arrive in time.
  One contract at a time; it can be cancelled before departure.
* Wages: every day (rest or travel) the sum of all wages is deducted; gold may go negative.
* Encounter generation: enemy count/archetypes scale with difficulty (3 / 4-6 / 7-8 foes from raiders, bandit archers,
  brutes; the mix depends on the enemy type). Enemies are not levelled.
* Battle map generation (`mapgen.rs`): 12x10 map from the contract seed with forests, hills, rocks and lakes;
  player spawns in the west, enemy spawns in the east; any obstacle separating two spawn hexes is carved away, so every
  spawn hex is passable and mutually reachable (verified for 500 seeds in tests; at most 24% of the map is impassable).
* Roster in battle: the battle takes persistent units (name, archetype, level-based stats, current HP); wounded units
  start with reduced HP, armor is repaired for free. The battle reports per-unit outcome (final HP, dead, kills).
  The campaign layer contains no combat rules.
* After battle: the dead are removed for good (and listed as fallen); survivors keep their HP. XP = 10 participation
  + 10 on victory + 12+4*difficulty per kill; level thresholds 40/100/180/280/400/540/700 (cap 8); each level gives
  +5 max HP (and +5 current HP), +2 skill, +1 defense, shown in the roster panel and the result screen. Wages grow by 1
  per level above 1.
* Rewards: a won contract pays only when the company is back in town, once. Retreat and defeat give no pay (a defeated
  or timed-out battle forfeits the contract).
* Healing: resting in town heals 12 HP per day per mercenary, capped at max HP; hiring costs gold (16 x wage),
  needs a free roster slot, and is rejected without enough gold. Recruits are refreshed every 4th day and on return.
* **Game over rule**: the campaign ends when (a) no mercenary is alive, or (b) gold is below zero and no living
  mercenary is fit to work (HP at least half of max), or (c) debt exceeds 300 gold. Checked after every day and
  after every battle; a finished battle's result is shown before the campaign-over screen (days survived, contracts
  completed/failed, battles, gold, the fallen - paged 8 per page with Prev/Next buttons or the mouse wheel, so long lists stay visible above the button), which returns to the menu.

**Tooling**: `--autoplay-campaign` (autopilot plays the campaign, AI on both battle sides), `--shot VIEW=FILE`
(screenshots of named views, e.g. overworld and a campaign battle), `--seed` also seeds the campaign. The core
autopilot (`autopilot.rs`) is also used headlessly (`campaign_sim` example); AI-vs-AI over 300 seeds and 8 battles the
company wins ~78% of battles (75% / 82% / 67% at 1 / 2 / 3 skulls) and about 60% of those campaigns end in game over
within 8 battles - the autopilot and enemy AI are not smart, a human should do better.

**Tests added**: core campaign/mapgen/autopilot tests (`tests/campaign.rs`, 500-seed map validity, 3-contract headless
AI campaign run twice for identical summaries), battle kill/outcome reporting, app state-machine tests (full mouse
journey menu -> ... -> second contract, retreat, empty site, game over, pause, autopilot loop, every screen renders),
CLI parsing and `--shot`.

**Live-window GUI smoke (this host)**: not possible and therefore **not claimed**. There is no display path here:
`Xvfb`, `weston`, `xvfb-run`, `cage` and `sway` are absent; `Xorg` exists but fails to start (no `/dev/dri/card0`, no
`dummy`/`fbdev`/`vesa` driver); `Xwayland` has no compositor to attach to; `DISPLAY` / `WAYLAND_DISPLAY` are unset, so
a windowed run exits with code 3 ("cannot connect to a display") as designed. What *was* run instead: the full UI
rendered by the software rasteriser in `--headless` mode (identical pixels to what the window presents), screenshots
of the overworld, travel, encounter, town, a campaign battle, the campaign result and the payout notice
(`--headless --autoplay-campaign --seed 3 --shot ...`), and the off-screen GPU blit test (passes on llvmpipe).
The windowed path (surface, presentation, input mapping) of the new screens therefore remains unexercised on a real display.

## Approximations and design choices

* Units do not block line of sight (only rock and forest do). No cover, no zone of control, no attacks of opportunity.
* Only the active unit can act; there is no "wait", no per-unit stances, no free choice of weapon (distance decides melee vs ranged).
* Armor is a damage buffer with a piercing percentage, not Battle-Brothers-style armor-by-body-part. No morale, fatigue, wounds or injuries.
* Hill bonus is a flat +10/-10 hit modifier, not true elevation; hills do not grant extra range or sight.
* A battle undecided after 40 rounds is called off and counts as a defeat (safety valve; never reached in 2000 AI-vs-AI runs; covered by a core test and an app test that renders the timeout result screen and returns to the menu).
* Quick Battle seed comes from the clock (or `--seed`); the Quick Battle scenario is a single fixed skirmish. Campaign battles use generated maps.
* Text is uppercase-only bitmap font; no sound; unit art is procedural tokens with a letter badge; the overworld map is procedurally drawn.
* Campaign approximations: abstract travel (town <-> site only, no roaming, no random events on the road), no healing while
  travelling, armor repairs for free, enemies never level, no save/load (abandoning or closing ends the campaign), no
  equipment/market, a single fixed region (contracts vary, the map does not), all mercenaries share one XP curve.
* The window renders a fixed 1400x820 frame that is scaled (letterboxed) to the window size.
* AI-vs-AI balance probe: the player side wins about 65% of 2000 seeded battles when the AI plays both sides.

## Not implemented (relative to the brief)

* Company management beyond hiring/resting: equipment, market, dismissing mercenaries, injuries by body part.
* Progression beyond flat per-level stat gains: perks/skills trees, new unit types beyond the seven archetypes.
* Richer tactics: more attack types/abilities, status effects, morale, zone of control, multiple tilesets, enemy factions with
  their own rules.
* Richer strategic layer: roaming travel with events, several regions, faction/reputation, random world.
* Save/load and settings (resolution, keybindings, volume); audio.
* A verified run on a live display (see above): no display server is available on the build host.
