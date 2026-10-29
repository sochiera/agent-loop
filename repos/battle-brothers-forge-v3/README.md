# battle-brothers-rust

Empty owned worktree for an independent native Linux tactical game.
Not a clone and not a continuation of any older project. No proprietary assets.
Initialized 2026-10-21 so a bounded vertical-slice plan can land here.

## Vertical slice

One 3-vs-3 melee battle on a 61-hex map: `crates/bb_rules` (std-only deterministic rules core,
headless smoke) and `crates/bb_game` (winit + wgpu window, placeholder graphics drawn in code).

Most rules and numbers are independent slice design. Only the concept names ActionPoints, Fatigue,
Initiative, Morale, apCost, EndTurn and WaitTurn were seen in the original data
(OBSERVED-NAME-ONLY). Recover's fatigue-halving effect is RECONSTRUCTED from static bytecode
evidence; original-runtime parity has not been tested. UC-COMBAT-065 also catalogues a 9 AP cost
and spending the whole turn, without runtime confirmation; T53 implements those catalog statements.
Earlier layer-A notes that called Recover's cost and turn effect unspecified predate that distinction.
Integer rounding, surplus-AP forfeiture, zero-fatigue acceptance and ungated availability remain
**APPROXIMATION** choices. Turn-start fatigue reduction, movement and attack fatigue costs, fatigue
maxima, and initiative ordering remain slice approximations. The five morale states (Steady, Confident,
Wavering, Breaking, Fleeing) and their melee-skill/defense modifiers are a partial rules foundation
described below. Scenario units start Steady but can deteriorate through a Resolve check after
surviving a heavy melee hit; a failed check at Breaking now makes a unit Fleeing, which removes
player control and ends in an automatic escape from the battlefield (see "Fleeing and escape"
below). A Fleeing unit that is not on a map edge and has no adjacent present enemy may recover to Breaking
by an automatic self-rally check at its turn start (see "Self-rally at turn start"). A melee death also
triggers an approximate Resolve check for every present ally of the fallen unit (see "Ally-death morale
checks"). A party-retreat order (`Command::Retreat`) lets edge units escape at once and sends the
rest on automatic withdrawing turns; the native window offers it through a **Retreat** button and the `V`
key behind a mandatory confirmation (see "Party retreat order"). The killer of a hostile unit in
melee takes a positive Resolve check that can raise its morale one step; an eligible killer currently
on the map edge has zero effective chance (see "Kill morale checks"). An ordinary move onto an
outermost-ring hex lowers a non-Fleeing, non-retreating mover directly to Breaking without extracting it
(see "Map-edge movement penalty").
Other morale triggers, Rally the Troops and other positive transitions, tooltips and map
indicators remain deferred. A side-panel label shows each present unit's current morale state and melee-stat scaling
percentage; its visual presentation is an **APPROXIMATION** described below.

Build and check (Linux, Rust 1.96 stable):

    cargo test -p bb_rules
    cargo build --release
    cargo run --release -p bb_rules --bin headless_smoke -- --seed 1
    cargo run --release -p bb_game -- --seed 1
    cargo run --release -p bb_rules --bin headless_smoke -- --seed 1 --terrain hills
    cargo run --release -p bb_game -- --seed 1 --terrain hills
    cargo run --release -p bb_rules --bin headless_smoke -- --seed 1 --adrenaline
    cargo run --release -p bb_game -- --seed 1 --terrain hills --adrenaline
    cargo run --release -p bb_rules --bin headless_smoke -- --seed 1 --rotation
    cargo run --release -p bb_game -- --seed 1 --terrain hills --rotation --adrenaline
    cargo run --release -p bb_rules --bin headless_smoke -- --seed 1 --footwork
    cargo run --release -p bb_game -- --seed 1 --terrain hills --adrenaline --rotation --footwork
    cargo run --release -p bb_rules --bin headless_smoke -- --seed 1 --fast-adaptation
    cargo run --release -p bb_game -- --seed 1 --terrain hills --adrenaline --rotation --footwork --fast-adaptation
    cargo run --release -p bb_rules --bin headless_smoke -- --seed 1 --dodge
    cargo run --release -p bb_game -- --seed 1 --terrain hills --adrenaline --rotation --footwork --fast-adaptation --dodge
    cargo run --release -p bb_rules --bin headless_smoke -- --seed 1 --nine-lives
    cargo run --release -p bb_game -- --seed 1 --terrain hills --adrenaline --rotation --footwork --fast-adaptation --dodge --nine-lives
    cargo run --release -p bb_rules --bin headless_smoke -- --seed 1 --berserk
    cargo run --release -p bb_game -- --seed 1 --terrain hills --adrenaline --rotation --footwork --fast-adaptation --dodge --nine-lives --berserk
    cargo run --release -p bb_rules --bin headless_smoke -- --seed 1 --pathfinder
    cargo run --release -p bb_game -- --seed 1 --pathfinder
    cargo run --release -p bb_rules --bin headless_smoke -- --seed 1 --terrain hills --adrenaline --rotation --footwork --fast-adaptation --dodge --nine-lives --berserk --pathfinder
    cargo run --release -p bb_rules --bin headless_smoke -- --seed 1 --indomitable
    cargo run --release -p bb_game -- --seed 1 --terrain hills --indomitable
    cargo run --release -p bb_rules --bin headless_smoke -- --seed 1 --terrain hills --adrenaline --rotation --footwork --fast-adaptation --dodge --nine-lives --berserk --pathfinder --indomitable
    cargo run --release -p bb_game -- --seed 1 --terrain hills --adrenaline --rotation --footwork --fast-adaptation --dodge --nine-lives --berserk --pathfinder --indomitable
    cargo run --release -p bb_rules --bin headless_smoke -- --seed 1 --terrain flat --stunning-blow
    cargo run --release -p bb_game -- --seed 1 --terrain hills --stunning-blow --adrenaline
    cargo run --release -p bb_rules --bin headless_smoke -- --seed 1 --terrain hills --killing-frenzy
    cargo run --release -p bb_game -- --seed 1 --terrain hills --killing-frenzy --berserk

`--terrain flat|hills|swamp` (default `flat`) selects the terrain preset of both programs; a missing or invalid
value prints `--terrain needs flat, hills or swamp` and exits with status 2 before any window opens. See "Hills
skirmish and height overlay" below. `--adrenaline` (boolean, default off, combinable with any terrain and
`--seed`) gives the three player units the Adrenaline capability; see "Adrenaline".
`--rotation` (boolean, default off, combinable with any terrain, with `--adrenaline` and with
`--seed`) gives the three player units the Rotation capability; see "Rotation". `--footwork` (boolean,
default off, combinable with any terrain, `--adrenaline`, `--rotation` and `--seed`) gives the three
player units the Footwork capability; see "Footwork". `--fast-adaptation` (boolean, default off,
combinable with everything above) gives the three player units the passive Fast Adaptation capability with
zero stacks; see "Fast Adaptation". `--dodge` (boolean, default off, combinable with everything above)
gives the three player units the passive melee Dodge capability; see "Dodge". `--nine-lives` (boolean,
default off, combinable with everything above) gives the three player units unused Nine Lives protection and
shows a `NINE LIVES READY` / `NINE LIVES USED` indicator per unit row; see "Nine Lives". `--berserk` (boolean,
default off, combinable with everything above) gives the three player units the passive Berserk capability;
this player-only launch assignment is an **APPROXIMATION**. It needs no activation control or AI selection
policy: a kill refund appears in the existing AP values and event log; see "Berserk".
`--pathfinder` (boolean, default off, combinable with everything above) gives the three player units the passive
Pathfinder movement-fatigue capability; this player-only launch assignment is an **APPROXIMATION**. The existing
movement preview shows its discounted FAT cost, and the resource row shows current fatigue, so it needs no
activation control or AI selection policy; see "Pathfinder".
`--indomitable` (boolean, default off, combinable with everything above, the ninth capability option; all 512
subsets compose) gives the three player units the Indomitable stance capability with the stance inactive; this
player-only launch assignment is an **APPROXIMATION**. It adds the **INDOMITABLE (4)** button and key `4` and
an `INDOMITABLE` unit-row marker while the stance is effective; see "Indomitable". Headless AI has a guarded
wounded-and-threatened activation policy.
`--two-hex` (boolean, default off, combinable with everything above, repeats harmless) gives the three player units
`MeleeReach::TwoHex`; enemies keep `Adjacent`. This is an **APPROXIMATION** launch loadout, not a polearm or
equipment system; it adds a `REACH 2` unit-row tag, see "Two-hex launch loadout (T59)".
`--stunning-blow` (boolean, default off, repeats harmless and combinable with seed, every terrain and all other flags)
grants the Stunning Blow capability to player units only. This player-only launch assignment is an
**APPROXIMATION**, not skill learning or a weapon system. It uses the existing **STUN BLOW (6)** control; `R` and `N`
restore the selected capability in a fresh, unstunned battle. When granted the capability, headless AI can cast
Stunning Blow under the bounded T66 policy; see "Stunning Blow AI policy (T66)" and "Stunning Blow launch option (T65)".
`--killing-frenzy` (boolean, default off, repeats harmless and combinable with seed, every terrain and all other flags)
grants the passive capability to player units only, with zero initial Frenzy counters. This launch assignment is an
**APPROXIMATION**; it is not perk learning or an equipment system. A paid melee kill activates the existing T67
effect, Berserk can refund AP for a boosted follow-up, and the existing Attack and Stunning Blow previews display the
boosted range while it is active. A native `FRENZY` row marker appears while the effect is effective (T69 below).
`R` and `N` retain the selection and rebuild fresh units with zero counters. AI receives the passive through accepted
melee execution without a new selection policy; combat-log text, equipment and other kill sources remain deferred.
See "Killing Frenzy launch option (T68)".

Controls: left click a green hex to move, left click an adjacent enemy to attack, `E` end turn,
`W` wait, `X` or the **End Round** side-panel button open the End Round confirmation (see below),
`V` or the **Retreat** side-panel button open the Retreat confirmation (see "Party retreat order"; `v` and `V`
both work),
`1` or the **ADRENALINE (1)** button in the top-right strip use Adrenaline (enabled only under `--adrenaline`,
see "Adrenaline"),
`2` or the **ROTATION (2)** button next to it open Rotation ally targeting (enabled only under `--rotation`
while a legal ally is adjacent, see "Rotation"; a second `2` or `Esc` cancels),
`3` or the **FOOTWORK (3)** button, the third in the strip, open Footwork destination targeting (enabled only
under `--footwork` while a legal adjacent hex exists, see "Footwork"; a second `3` or `Esc` cancels; `2`/`3`
or the other button switch directly between the two targeted skills when the other one is available),
`4` or the **INDOMITABLE (4)** button, the fourth in the strip, activate the Indomitable stance (enabled only
under `--indomitable` while `indomitable_availability` succeeds, see "Indomitable"; it cancels open
targeting first like `1`),
`5` or the **RECOVER (5)** button below the strip (724, 48) halve fatigue and end the turn (enabled for the
present, active, controllable player with at least 9 AP; zero fatigue is eligible; see "Recover action"),
`6` or the **STUN BLOW (6)** button at (16, 100) open Stunning Blow enemy targeting (enabled with `--stunning-blow`
and an adjacent legal target; `TwoHex` does not extend this skill's range; see "Native Stunning Blow targeting (T64)"
and "Stunning Blow launch option (T65)"; a second `6` or `Esc` cancels; `2`/`3`/`6` switch directly between the targeted skills),
`Z` toggle the enemy control overlay (initially off), `H` toggle the map height numbers (off on flat, on on
hills; see below), `R` restart, `N` new seed (seed+1),
`Esc` quit (inside either confirmation dialog, and while Rotation, Footwork or Stunning Blow targeting is open, `Esc` cancels instead).
Only one confirmation can be open. Enemy turns play out one command
per 250 ms.

## Basic melee surround (partial catalog coverage)

`bb_rules::BattleState::hit_chance` applies this melee-only **APPROXIMATION**, based on the
[UC-COMBAT-009 catalog text](docs/source/10-tactical.md): when a living attacker is adjacent to a
living hostile target, each additional living unit on the attacker's side adjacent to that target
adds 5 percentage points. The attacker's own adjacency is the first unit and adds no points:

`clamp(effective_melee_skill - effective_defense + 5 * (living adjacent allies - 1), 5, 95)`

*(Qualification, later task: for a capable attacker `hit_chance` now also adds the optional Fast Adaptation bonus before the same clamp; see "Fast Adaptation" below. Without the capability this formula is unchanged.)*

Effective melee skill and defense equal the base values while a unit is Steady (see the morale
section below) and, for defense, while the unit lacks the optional Dodge capability (see "Dodge" below). For the slice's existing Steady stats, one, two and three adjacent attackers preview
55%, 60% and 65%.
The same calculation feeds the preview and `Event::Attacked.chance`; previewing is read-only and
does not consume RNG. This is partial UC-COMBAT-009 coverage reconstructed from catalog text, with
no original-runtime confirmation. Backstabber, Underdog, creature-specific surround values and
ranged attacks are deferred.

Focused headless coverage is in [`crates/bb_rules/tests/rules.rs`](crates/bb_rules/tests/rules.rs):
`surround_bonus_adds_five_points_per_additional_adjacent_ally`,
`enemy_attackers_receive_the_same_surround_bonus`,
`surround_bonus_is_direction_independent`,
`surround_ignores_dead_distant_and_target_side_units`,
`nonadjacent_or_dead_attacker_gets_no_surround_bonus`,
`surround_is_added_before_the_existing_hit_chance_clamp`,
`moving_or_killing_a_supporting_ally_immediately_removes_its_bonus`, and
`hit_chance_preview_is_pure_and_attack_reports_the_same_chance`.
Validation: `cargo test --workspace --locked --offline` passed (30 tests; 0 failed).

## Melee elevation (partial UC-COMBAT-010 coverage; opt-in hills skirmish in the native window)

`BattleState.elevations` is a `terrain::ElevationMap` (re-exported as `bb_rules::ElevationMap`) that
gives every in-map hex a height. `ElevationMap::default()` is height 0 everywhere and
`BattleState::new` starts flat. API: `get(Hex) -> Option<u8>` (`None` off the map),
`set(Hex, u8) -> Result<(), ElevationError>` with `ElevationError::OffMap` (checked first) and
`ElevationError::InvalidHeight` (above `terrain::MAX_ELEVATION` = 3), plus `is_flat()` and
`entries()`. A rejected `set` mutates nothing and nothing here consumes RNG. Obstacle hexes may carry a
height; passability is unchanged. Setting 0 removes the entry, so equal maps are equal whatever the
construction order. Height belongs to the hex and follows a unit's current position; units store none.

`hit_chance` adds one more term for a present, hostile, adjacent melee pair (the surround condition):

`clamp(effective_melee_skill - effective_defense + surround + elevation, 5, 95)`, with
`elevation = +10` when the attacker's hex is higher than the target's (any difference),
`-10 * (target height - attacker height)` when it is lower, and `0` when equal. The sum is signed and
clamped once. Ordinary attacks, opportunity attacks and the hit-percentage preview all go through
`hit_chance`, so they share it. Other queries (non-adjacent, same side, dead) are unchanged.

*(Qualification, later task: a capable attacker's optional Fast Adaptation bonus is a further term of this same sum, clamped once; see "Fast Adaptation" below.)*

`state_hash` appends, only when some height is nonzero, `ELEVATION_HASH_TAG` (`0x454c4556`), the entry
count, then `q`, `r`, height per entry (each a little-endian `u64`, coordinates as `i64`), sorted by
`q` then `r`. A flat map appends nothing, so flat-map hashes (and the `headless_smoke` seed 1 result
`HASH=dfce9b8d74bcaf93`) are unchanged.

The catalog text of [UC-COMBAT-010](docs/source/10-tactical.md) states the 0–3 range, +10 downhill and
-10 per level uphill; this is not runtime-confirmed. The storage, error type, the "any downward
difference is +10" reading, the adjacency gating and the hash encoding are **APPROXIMATION**.
**Limits:** the default scenario is flat. Heights are configured only through the opt-in `hills` preset
(see "Hills skirmish and height overlay"); there is no terrain editor, procedural generation, camera
elevation or per-terrain art. Climbing costs extra AP (see "Uphill movement AP cost"); ranged-attack
effects and extra vision are deferred.

Tests: [`crates/bb_rules/tests/elevation.rs`](crates/bb_rules/tests/elevation.rs) (22) cover flat defaults,
all heights, rejection purity (including i32-extreme coordinates, checked without overflow), obstacle independence, cloning/reset, all 16 height pairs for both
sides, morale-adjusted stats, surround, both clamps, ordinary/opportunity attack agreement and RNG
draws, position-following, read-only queries, and hash suffix, ordering, canonical zero and
flat-hash stability. Validation: `cargo test --workspace --locked --offline` passed (363 tests; 0 failed);
`headless_smoke --seed 1` printed `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=dfce9b8d74bcaf93`.

### Uphill movement AP cost — partial UC-COMBAT-005/010 coverage

A step between adjacent hexes costs `MOVE_AP + max(destination_height - departure_height, 0)` AP using
`BattleState.elevations`; level and downhill steps keep the base cost of `MOVE_AP` (2), so a flat map is
unchanged. Fatigue stays `MOVE_FATIGUE` per attempted step whatever the height (a `has_pathfinder` unit pays `PATHFINDER_MOVE_FATIGUE` instead, see "Pathfinder"). T48 qualifies this: the base AP and fatigue above are the Ground
destination costs; Swamp and Snow destinations use the shared terrain costs, see "Ground, Swamp and Snow terrain (T48/T71)". One crate-private
calculation (`step_ap_cost`, with `step_costs`, `route_costs` and `affordable_prefix` built on it since T48) owns the height
arithmetic; every consumer uses it:

- `Command::Move` and `preview_move` share `plan_move`, which sums the step costs over the existing BFS
  route. Validation precedence is unchanged: `Unreachable`, then `NotEnoughAp`, then `TooFatigued`
  (checked, overflow-safe fatigue sum). `MovePreview.ap_cost` is that full-route total; `path`,
  `first_threat` and `first_edge_penalty` are untouched.
- `reachable` keeps its stable BFS candidate order, judges each destination on its own BFS route with
  the same AP and fatigue totals, and lists that route's AP cost. For a valid active controllable actor
  every listed hex agrees with `preview_move`, and every other BFS hex is rejected by it.
- `walk` checks and pays the intended step's full cost before the opportunity attacks of that step. A
  reaction that stops the move still leaves that attempted step's surcharge spent; if the reactions'
  incoming fatigue makes the next step unaffordable, the walk stops without charging it.
- `Flee` and `Withdraw` keep their route selection (exit by BFS distance and tie-breaks, first step of the
  BFS path) but need the full next-step cost to move; otherwise the turn ends with no movement. A unit
  already standing on an edge still escapes at once for zero AP.
- Ordinary AI approach planning keeps its chosen destination and BFS path and emits a `Move` to the end
  of the longest prefix that the shared costs and checked fatigue allow, or `EndTurn` when not even the
  first step is affordable. Every emitted `Move` therefore passes upfront validation; planning draws no RNG.

**Routing limit:** BFS still minimizes geometric steps, not AP. A cheaper alternative route (for example
around a hill) is never selected, so a rejection may reflect the selected BFS route's cost even when a
longer route would have fit. Weighted pathfinding is not implemented (T48 terrain costs do not change this).

Provenance: the catalog rows [UC-COMBAT-005 and UC-COMBAT-010](docs/source/10-tactical.md) name climbing as an AP cost. The
uphill-only application, the surcharge of one AP per level climbed, unchanged fatigue, charging the
attempted step before reactions, and retaining BFS routing are **APPROXIMATION** slice choices without
original-runtime confirmation. The default scenario stays flat (its costs, events and the seed 1 smoke
result are unchanged); the opt-in hills preset makes these costs visible in the native window (see
"Hills skirmish and height overlay").

Tests: [`crates/bb_rules/tests/elevation_movement.rs`](crates/bb_rules/tests/elevation_movement.rs) (18) cover all 16 height pairs for both sides,
multi-step ascents and descents, exact-budget and one-short rejection, preview/`reachable`/execution
agreement and ordering over a hilly map, the retained BFS route, checked fatigue boundaries (exact cap,
`u32::MAX`) and precedence, missed, stopping, lethal and fatigue-interrupted reactions, `Flee` and
`Withdraw` (sufficient/insufficient budget, immediate arrival, zero-AP edge escape, stopped step), AI
prefix and no-step fallback with command validation, and state/RNG purity. In `elevation.rs` only the
opportunity-attack test's AP expectation changed (a height-3 destination now costs 5 AP).

### Hills skirmish and height overlay

`scenario::TerrainPreset::{Flat, Hills, Swamp}` and `scenario::battle(seed, preset)` build the scenario battle.
`Flat` is exactly `BattleState::new(seed)`. `Hills` keeps the units, stats, placement and RNG stream and
only sets heights, through the `ElevationMap` API, on all 61 map hexes (obstacles included, passability
unchanged, no RNG): height 2 where `q == 0`, 1 where `q` is -1 or 1, 0 elsewhere, and hexes (0,-1) and
(0,1) are 3. The T23 parser supported `--terrain flat|hills`; the current shared parser also accepts
`--terrain swamp` (default remains `flat`, all terrain values combine with `--seed`). `scenario::parse_args`
serves both `headless_smoke` and `bb_game`. Swamp keeps height zero and applies the hand-authored ground
layout described in "Swamp scenario preset (T50; CLI launch added in T52)". `headless_smoke --seed 1 --terrain hills` printed
`OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=d0b704f0294173be`; the flat result stays
`OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=dfce9b8d74bcaf93`.

The native client keeps the preset in `App`: `R` rebuilds the same preset and seed, `N` the same preset with
the next seed (a pending confirmation is still cleared). `H` (or `h`) toggles an independent height-number
overlay, which starts off for Flat and Swamp and on for Hills and survives `R`/`N`; it is separate from `Z` and works
during enemy turns and after the battle, but is consumed while a confirmation dialog is open. When on, every
map hex (occupied and blocked ones too) shows its current `ElevationMap` height as a small pale badge in the
hex's upper-left corner pocket, clear of unit rings, AP-cost labels, health bars and ZoC markers. A legend in
the top-left corner shows `H HEIGHT NUMBERS ON/OFF`, `BADGE = HEIGHT 0-3` and `UPHILL +1 AP PER LEVEL`.
Reachable-hex AP costs and `HIT n%` labels still come straight from the rules queries (`reachable`,
`hit_chance`); the client does no elevation arithmetic. Rendering and the toggle never mutate `BattleState`
or draw RNG. The outcome banner no longer sits over the map: it uses the strip below the map (the
movement-preview box area, free once the battle is over) so every height badge stays visible after the
battle ends.

Provenance: heights, uphill cost and hit modifiers cite UC-COMBAT-010 (see above); the badge and legend
are an **APPROXIMATION** of the UC-UI-003/006 tactical HUD / overlay rows, not original-UI parity. The
hand-authored hills layout, the `H` shortcut and the visual presentation are **APPROXIMATION**s. Limits:
no terrain editor, procedural generation, camera elevation, height-specific art or sound, and the default
scenario is flat. Tests: `crates/bb_rules/tests/terrain_presets.rs` (9) plus App and renderer tests in
`crates/bb_game/src/{main,render}.rs`. Visual inspection of the native window was unavailable (no display
session); badge geometry is checked by renderer tests only.

## Fatigue from incoming melee attacks (partial UC-COMBAT-006 coverage)

`Command::Attack` adds `DEFENDER_HIT_FATIGUE_GAIN` (5) to the target on a hit and
`DEFENDER_MISS_FATIGUE_GAIN` (2) on a miss, capped at the target's fatigue maximum with
overflow-safe arithmetic. Hits add fatigue even when armour absorbs the damage, and lethal hits
apply it before death handling (a hit intercepted by Nine Lives counts as a hit for this fatigue). Attacks do not check the defender's AP, fatigue capacity or turn
status, so a unit at maximum fatigue can still be attacked. The updated fatigue feeds the slice's
existing later action eligibility, initiative ordering and turn-start regeneration. Incoming
fatigue adds no RNG draws and leaves attacker costs and the current roll and damage calculation
unchanged.

This is an **APPROXIMATION** based on the [UC-COMBAT-006 catalog text](docs/source/10-tactical.md),
with no original-runtime confirmation. The catalog distinguishes a hit (+5) from dodge/block (+2),
but the slice has no separate dodge or block outcomes, so every generic melee miss maps to the
catalog's dodge/block fatigue gain. Undead fatigue immunity and equipment, shield, status and perk
exceptions are deferred.

Focused headless coverage is in
[`crates/bb_rules/tests/incoming_attack_fatigue.rs`](crates/bb_rules/tests/incoming_attack_fatigue.rs):
`hit_and_miss_add_fatigue_to_player_and_enemy_defenders`,
`armour_absorbed_hit_still_adds_five_fatigue`,
`incoming_fatigue_saturates_at_the_cap_without_rejecting_an_attack`,
`lethal_hit_adds_fatigue_before_death_handling`,
`incoming_fatigue_can_make_the_defenders_next_attack_too_fatigued`,
`turn_start_regenerates_fifteen_from_updated_fatigue`,
`invalid_attack_keeps_rng_state_and_defender_fatigue_unchanged`, and
`incoming_fatigue_affects_initiative_at_the_next_round`.
Validation: `cargo test --workspace --locked --offline` passed (38 tests; 0 failed).

## Melee zones of control and opportunity attacks (partial UC-COMBAT-007/008 coverage)

Every living unit exerts zone of control (ZoC) on its six adjacent hexes; dead units and allies never
control a mover. `BattleState::enemy_controllers_at(unit, hex)` returns the living opposing units
adjacent to `hex` in ascending id order and is read-only (no RNG).

An accepted `Command::Move` still validates the BFS route and the whole path's AP/fatigue up front
(rejections mutate nothing and draw no RNG), but is then executed one step at a time. Entering an
adjacent hex is safe. Before each departure the controllers of the mover's current hex are found;
the step's full AP cost (the destination terrain's AP, `MOVE_AP` on Ground, plus any uphill surcharge, see "Uphill movement AP cost" and "Ground, Swamp and Snow terrain (T48/T71)") and the destination's fatigue (`rules::terrain_move_fatigue`: Ground `MOVE_FATIGUE`, or `PATHFINDER_MOVE_FATIGUE` for a Pathfinder unit, see "Pathfinder"; Swamp and Snow twice that, see "Ground, Swamp and Snow terrain (T48/T71)") are charged first, then each controller attacks in ascending id
order with the normal hit chance (including surround), RNG draws, damage, armour and incoming
defender fatigue, but pays no AP or fatigue and needs no turn or action budget. Departing a
controlled hex triggers reactions even when the destination is still adjacent to that controller.
If any reaction hits, the mover stays on the departure hex after all controllers have reacted;
if all miss, the step completes. A reaction that kills the mover stops further reactions, removes
the mover from occupancy and queue, checks elimination and passes the turn on (next unit, next
round or the time limit; no turn start after the battle ends). Only attempted steps are charged,
and if incoming fatigue makes a later step unaffordable the move just stops there, returning `Ok`
with the completed movement and effects. `Event::Moved` lists only completed steps, flushed before
later reactions and before any map-edge morale event (a move without reactions or edge-morale changes is still
one `Moved` with the full path; see "Map-edge movement penalty"); `Attacked` and
`Died` are reused for reactions.

`BattleState::reachable` remains a pure geometric and budget preview (AP costs include uphill surcharges): it ignores ZoC, so a listed
hex can still be denied by a reaction. Risk-aware routing is not implemented.

The native client adds a `Z` toggle for a living active player unit's turn. It marks every
non-obstacle hex controlled by one or more living enemies, including the selected unit's hex when
controlled. A corner `!` marker and the panel legend explain that leaving a marked hex may trigger
free attacks. The markers do not make a hex impassable: entering adjacency remains legal, and the
existing reachable highlights stay geometric budget previews. The preference starts off and
survives restart and new-seed actions. This visual is a slice UI **APPROXIMATION** based on
[UC-COMBAT-007](docs/source/10-tactical.md) and [UC-UI-006](docs/source/60-ui.md); it makes no
original-UI parity claim. Other ZoC rule exceptions and route-risk UI remain deferred.

Headless overlay tests are in [`crates/bb_game/src/main.rs`](crates/bb_game/src/main.rs) and
[`crates/bb_game/src/render.rs`](crates/bb_game/src/render.rs). They cover the toggle and preference
persistence, controlled-tile selection and exclusions, marker geometry, visibility gates, and
state/RNG purity.

### Movement preview — partial UC-COMBAT-005/007/008 coverage

`BattleState::preview_move(unit, destination) -> Result<MovePreview, RuleError>` is a read-only
query (no RNG, no state change). It uses the same battle-over, active-unit, route (BFS tie-breaking),
AP and checked-fatigue validation as `Command::Move`: both call the same private `ensure_active` and
`plan_move`, so paths, costs and errors cannot drift, and execution still goes through `walk`.
`MovePreview` holds `path` (excludes the start, includes the destination), `ap_cost` and
`fatigue_cost` for the full planned route before any reaction effects (`ap_cost` includes uphill surcharges, see "Uphill movement AP cost"),
`ap_remaining` (current AP minus that full route cost), `attack_budget_available`, and `first_threat:
Option<MoveThreat { departure, controllers }>`. The attack-budget field is true when remaining AP covers
`ATTACK_AP` and current fatigue plus planned movement fatigue (the route's destination-dependent per-step `rules::terrain_move_fatigue`: Ground `move_fatigue`, Swamp and Snow twice that, discounted for Pathfinder units) plus `ATTACK_FATIGUE` fits
`fatigue_max`, using checked arithmetic. It describes the nominal budget for one basic melee attack
before reactions; it does not require or establish an adjacent target and does not predict whether an
attack will be possible after movement resolves. UC-COMBAT-005 names the remaining attack AP indicator;
the combined AP/fatigue budget and its presentation are **APPROXIMATION**s because the slice attack
costs are not confirmed original rules. The first threat is the first departure, checking
the starting hex and then every path hex except the destination, where `enemy_controllers_at` is
non-empty; controllers keep that query's ascending-id order. Entering enemy adjacency only at the
destination is no threat.

`MovePreview` also holds `first_edge_penalty: Option<Hex>`, the first arrival hex on the planned path
(intermediate hexes included; the start only if it reappears as an arrival) where the mover's
*current* morale would take the map-edge penalty if reached (see "Map-edge movement penalty"). It is
a conditional route consequence from current state, not a prediction of arrival or of reaction
outcomes: reactions are not simulated, so a threat before the edge hex does not suppress it. `None`
when no path hex qualifies or the mover is already Breaking. `walk` and `preview_move` share one
private eligibility helper (`edge_penalty_applies`: edge hex, present, no retreat intent, Confident,
Steady or Wavering); execution evaluates the actual state at each arrival, the preview the current
state. Route, costs, errors and `first_threat` are unchanged.

The preview reports *potential* reactions only. It predicts no hits, damage, survival or arrival:
a threatened move may stop on a shorter route, add incoming defender fatigue, or kill the mover, so
arrival is conditional. Planned costs are not adjusted for those effects (an uncontested move
matches its preview exactly). Rule confidence labels are unchanged (APPROXIMATION); risk-aware
routing and automatic detours stay deferred.

In the native client, hovering a movement destination that `preview_move` accepts on the living
active player's turn shows a box below the map with `MOVE AP n FAT n LEFT n MELEE BUDGET YES/NO`;
all values and the budget state come directly from the rules preview. With a first threat it adds
`! WARNING: LEAVING (q,r) - n CONTROLLER(S)` and says reaction attacks may stop the
move and add fatigue. A closing line states planned costs do not include reaction effects. With
`first_edge_penalty` it adds `! IF REACHED: EDGE (q,r) LOWERS MORALE TO BREAKING, NOT EXTRACTED.`
(read from the rules field; the client duplicates no edge detection). With both warnings the two note
rows are combined into one (`REACTIONS MAY STOP MOVE, ADD FATIGUE, EFFECTS EXCLUDED FROM COSTS.`), so the
box never exceeds its four rows and preserves both warnings and the reaction-effects qualification.
The compact first row preserves planned costs while making room for remaining AP and the melee
budget. This indicator is an **APPROXIMATION** of one basic melee attack's AP/fatigue budget: it
excludes reaction effects and does not establish target availability. It is independent of the `Z`
overlay and hidden for invalid destinations, attack targets, missing or
inactive selections, enemy turns and finished battles. The presentation (layout, colours, wording)
is a slice UI **APPROXIMATION** with no original-UI parity claim; the edge warning is an
**APPROXIMATION** of the accepted UC-COMBAT-048 behaviour (see "Map-edge movement penalty").

Focused tests: [`crates/bb_rules/tests/movement_preview.rs`](crates/bb_rules/tests/movement_preview.rs)
(the original 10 tests: exact lane path/costs, repeatability and purity, start-hex threat, safe final-step entry,
threat after safe steps, multiple/dead/allied controllers, refresh after enemy move or death, error
parity with `Move` including overflow and exact-cap cases, execution of uncontested and threatened
previews; added budget coverage checks flat/uphill routes below, at and above `ATTACK_AP`, insufficient
fatigue, exact fatigue cap, attack-fatigue overflow, no-target previews, nominal threatened previews,
and post-move Attack acceptance/rejection; plus 7 edge tests: both sides, Confident/Steady/Wavering/Breaking, initial edge placement
alone, edge-to-edge and intermediate edge arrival, agreement with the first executed
`EdgeMoraleChanged` on a clone, a stopped or killed threatened move still previewing the edge, unchanged
route/costs/threat/errors, exempt automatic movement) and the `preview_*` and `edge_*` tests in
[`crates/bb_game/src/render.rs`](crates/bb_game/src/render.rs) (text content, all four warning
combinations, conditional wording and coordinates, remaining AP and both budget states, a Hills cost
fixture, visibility gates, Z independence, purity, measured text geometry for the combined case with
negative coordinates and three controllers, a width sweep over every map hex using maximum `u32` costs,
and a check that every preview-row character has a supported font glyph), plus box geometry clear of
other UI. This remains partial UC-COMBAT-005/007/008 coverage.
Historical validation (before the edge warning): `cargo test --workspace --locked --offline` passed (85 tests, 0 failed) and the seed-1
headless smoke printed `OUTCOME=Victory REASON=Elimination ROUNDS=6 HASH=892ea5d6c8f754c6`. No native
window was inspected (no display session here).
Edge-warning task validation: `cargo test --workspace --locked --offline` passed (341 tests, 0 failed),
`cargo build --workspace --locked --offline` succeeded and the seed-1 headless smoke is unchanged
(`OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=dfce9b8d74bcaf93`). Visual inspection of the native
window was **not** performed (`DISPLAY` and `WAYLAND_DISPLAY` are unset); the layout is covered only by
the measured headless geometry tests.
T24 validation: `cargo test --workspace --locked --offline --quiet` passed (414 tests, 0 failed), and
`cargo build --workspace --locked --offline` succeeded. Native visual inspection was not performed;
the four-row layout, bounds and supported glyphs are covered by renderer tests.

This is an **APPROXIMATION** based on the [UC-COMBAT-007/008 catalog text](docs/source/10-tactical.md),
with no original-runtime confirmation. The slice-specific choices are: every living unit counts as
melee-capable, reactions run in ascending controller id, and "departure" means leaving any hex
controlled by an enemy. A Fleeing unit no longer exerts ZoC (see "Fleeing and escape"). Deferred:
ranged/asleep/entangled exemptions (a stunned unit now exerts no ZoC: see "Stun effect"), smoke, net/root,
creature (Alp, Hex) exceptions, Lunge, Spearwall and equipment/status/perk effects,
and path-risk UI. Footwork exists only as an opt-in one-hex bypass with native destination targeting (see
"Footwork"); passive ZoC immunity, Spearwall interaction and AI use are not modelled.

Focused headless coverage is in [`crates/bb_rules/tests/zone_of_control.rs`](crates/bb_rules/tests/zone_of_control.rs)
(22 tests: query ordering/purity/exclusions, entering vs departing, hit/miss/multi-controller
outcomes, surround and defender fatigue, cost-free reactors, partial-event ordering, fatigue
exhaustion (including a `u32::MAX` fatigue-cap overflow regression), invalid-move purity, and active-mover death with next unit, next round, time limit and
elimination for both sides). The supporting-ally surround test in `tests/rules.rs` now uses seed 3 so
both reactions miss.
Validation: `cargo test --workspace --locked --offline` passed (60 tests; 0 failed) and
`cargo run --locked --offline -p bb_rules --bin headless_smoke -- --seed 1` printed
`OUTCOME=Victory REASON=Elimination ROUNDS=6 HASH=892ea5d6c8f754c6`.

## Overflow-safe fatigue affordability

Upfront `Move` and ordinary `Attack` validation, plus AI attack selection, now use checked fatigue
addition. An arithmetic overflow is unaffordable; a cost that exactly reaches `fatigue_max` remains
legal. Rejected commands return `TooFatigued` without changing state or RNG. When an AI attack is
unaffordable, it continues through its existing approach policy and ends the turn when no movement
is useful. The existing checked per-step movement guard remains in place.

Failing-first validation, before the production fix: `cargo test --locked --offline -p bb_rules
--test fatigue_affordability` exited 101 in debug, with 3 failures and 2 exact-cap passes. The
upfront Move check (`rules.rs:240`), Attack check (`rules.rs:257`) and AI attack selection
(`ai.rs:19`) each panicked with `attempt to add with overflow`. The three overflow regressions now
check that Move and Attack return `TooFatigued`, AI returns `EndTurn`, and state/RNG stay unchanged.
Exact-cap tests check successful movement and attack ending at `u32::MAX`, and that AI still picks
the lowest-HP adjacent target and can execute that exact-cap attack.

Focused tests: [`crates/bb_rules/tests/fatigue_affordability.rs`](crates/bb_rules/tests/fatigue_affordability.rs)
(`upfront_move_overflow_is_rejected_without_panic_or_mutation`,
`ordinary_attack_overflow_is_rejected_without_panic_or_mutation`,
`ai_does_not_select_an_attack_when_fatigue_addition_overflows`,
`exact_cap_move_and_attack_remain_legal`, and
`ai_selects_and_can_execute_an_affordable_exact_cap_attack`). Validation after the fix:
`cargo test --locked --offline -p bb_rules --test fatigue_affordability` passed (5/5),
`cargo test --release --locked --offline -p bb_rules --test fatigue_affordability` passed (5/5),
and `cargo test --workspace --locked --offline` passed (65 tests; 0 failed), including the
existing `step_affordability_check_cannot_overflow_at_a_u32_fatigue_maximum` regression.
This is a slice robustness correction, not original-game fidelity evidence or broader catalog coverage.

## End Round — command and native confirmation (partial UC-COMBAT-017 / UC-UI-007 coverage)

`Command::EndRound { unit }` is a player-only command. The native window exposes it through an
**End Round** button (bottom of the side panel, below the log) and the `X` key, always behind a
mandatory confirmation dialog. The catalog (UC-COMBAT-017) says an End Round button, after a
confirmation dialog, makes all remaining player units skip the round while enemies who have not gone
still play; UC-UI-007 asks for a modal confirm. Rules and UI below are an APPROXIMATION based on that
catalog text, without runtime confirmation (the original runtime was not executed); the visual
presentation (button, dialog, colours, wording) is an APPROXIMATION placeholder with no original-UI parity claim.

- Validation keeps the existing precedence: `BattleOver`, then `NotActiveUnit`, then the new
  `NotPlayerUnit` for an active enemy. Rejected commands change neither state nor RNG.
- It sets the new per-unit `skip_turn` flag on every living player unit still in the queue (the active
  unit and players deferred by `Wait` included; finished players and enemies are not marked), then
  forfeits the active unit's AP, removes it from the queue and advances.
- Marked players keep their queue positions. At a marked player's scheduled turn the usual
  once-per-round refresh runs (a `TurnStarted` event, fatigue regeneration from the fatigue at that
  moment, so fatigue gained from enemy attacks in between counts; a waited unit gets no second
  refresh), then AP becomes 0 and the turn advances automatically. All advancement paths (`EndTurn`,
  `Wait`, active-mover death from a reaction) honor pending skips.
- Enemy turns are untouched: no AI runs inside `EndRound`, each enemy still needs its ordinary
  commands. Skipped players stay alive, attackable and keep opportunity attacks.
- `skip_turn` is cleared when the next round begins. It occupies bit 3 of the per-unit flag word of
  `state_hash`, so states with every flag false hash as before. No RNG is drawn.
- At the round limit an exhausted queue ends the battle (time-limit defeat) with no further turn.

### Native button, shortcut and confirmation dialog

- The button is drawn enabled only for a living active player unit in a running battle; otherwise it
  is drawn disabled and clicking it does nothing. `X` follows the same gate. Opening the dialog
  executes nothing and changes neither `BattleState` nor the RNG.
- The dialog stores the pending actor, round and `state_hash`, shows how many living player units are
  still queued (active unit and Wait-deferred players included), and explains that confirming forfeits
  the remaining player actions this round while enemies still take their turns.
- `Confirm` button or `Enter` confirms; `Cancel` button or `Esc` cancels (Esc does not quit while the
  dialog is open). Confirm sits in the dialog centre-left, far from the launch button, so repeated `X`
  presses or repeated clicks on the launch button never confirm. Map clicks after the dialog closes are
  ignored for 350 ms so a double click on Confirm/Cancel cannot fall through to the map.
- While the dialog is open it consumes map clicks and every other shortcut (`E`, `W`, `Z`, `R`, `N`,
  repeated `X`); clicks outside its two buttons do nothing, and the AI issues no commands.
- Confirmation clears the pending intent first, then re-checks the stored actor, round and hash and
  that the actor is still the living active player unit; only then does it submit exactly one
  `Command::EndRound` through `App::run` (the client duplicates none of the skipping logic). Stale
  intent is discarded with an error message and no command. Restart (`R`) and new seed (`N`) clear any
  pending intent and keep the `Z` overlay preference.
- Button and dialog rectangles live in `crates/bb_game/src/layout.rs` and are shared by drawing and hit
  testing; UI clicks are routed before hex clicks. The dialog is opaque (the renderer does not enable
  alpha blending, so no dimming layer is drawn) and is emitted last, above the scene.

Tests: `crates/bb_game/src/main.rs` (App tests: open via `X`/button, cancel via `Esc`/Cancel, modal
isolation, one-shot confirmation compared with a direct `EndRound` on a cloned state, second-input and
new-round cases, enemy-turn/battle-over/dead-actor gates, stale actor/round/hash rejection, restart
cleanup, AI gating, queued counts), `render.rs` (shared button geometry, enabled/disabled states,
dialog text and counts, geometry inside the window, button clear of the log and legend) and `layout.rs`
(rectangle disjointness and hit testing). All run headlessly without a window or GPU.

The same pending-intent mechanism also carries the Retreat confirmation (see "Party retreat order"); the
dialog, modal isolation, click guard and stale-intent rules above apply to both actions.

Not covered: a native visual inspection of the dialog (no display was available in the validation
environment), other confirmation flows (dismiss, delete save, ...; Retreat is covered below), and anything beyond the
catalog sentence (AI, morale checks/transitions/fleeing, statuses). Partial coverage only; the mission
is not complete.

Focused tests: [`crates/bb_rules/tests/end_round.rs`](crates/bb_rules/tests/end_round.rs) (19 tests:
mixed and interleaved queues, consecutive marks, Wait-deferred and finished players, scheduled
regeneration after an enemy attack, no second refresh, enemy Wait and active-enemy death from a
reaction, opportunity attacks, next-round transition and flag reset, final-round exhaustion and
terminal rejection, rejection purity, RNG stability, hash sensitivity).
Validation: `cargo test --workspace --locked --offline` passed (104 tests before the native confirmation task; 123 tests, 0 failed after it) and
`cargo run --locked --offline -p bb_rules --bin headless_smoke -- --seed 1` printed
`OUTCOME=Victory REASON=Elimination ROUNDS=6 HASH=892ea5d6c8f754c6` (unchanged).

## Morale state modifiers — partial UC-COMBAT-018 coverage

The rules core exports `Morale::{Steady, Confident, Wavering, Breaking}` (plus `Fleeing`, added later:
see "Fleeing and escape") and stores the state on each unit. Effective melee skill and defense are derived from unchanged base `Stats` using one shared
percentage mapping: Steady 100%, Confident 110%, Wavering 90%, Breaking 80%. The effective value is
`floor(base * percentage / 100)`, calculated with widened integer arithmetic and capped at
`u32::MAX`. This percentage describes morale scaling of the base values only, not every source of final
defense: a Dodge-capable unit's `effective_defense` adds its Dodge bonus after this scaling (see "Dodge"). Flooring and saturation are slice **APPROXIMATION** choices without runtime confirmation.

`BattleState::hit_chance` uses the attacker's effective melee skill and target's effective defense (which
includes the target's optional Dodge bonus, see "Dodge"),
then adds the existing surround bonus and applies its existing 5–95 clamp. *(Later: a capable attacker's optional Fast Adaptation bonus joins the same sum before the clamp; see "Fast Adaptation".)* Ordinary attacks,
opportunity attacks and the native preview all use this same calculation. Queries consume no RNG and
change no state. Morale is encoded in unused state-hash flag bits with stable codes Steady 0,
Confident 1, Wavering 2 and Breaking 3; all-Steady hashes retain their previous values.

This is partial **APPROXIMATION** coverage based on the [UC-COMBAT-018 catalog text](docs/source/10-tactical.md),
not original-runtime confirmation. Scenario units start Steady and can now deteriorate; this section's
modifiers are applied to live combat by the check below and by the later ally-death checks (see
"Ally-death morale checks"; this section's original "ally deaths deferred" wording is superseded).
Other morale triggers (surrounding, fear, morale propagation), mood, Fearsome, Rally the Troops and other positive morale (except the later turn-start self-rally and killer's kill check, see "Kill morale checks"), Unbreakable,
ranged effects, tooltips and map indicators are deferred. The partial native panel label is described
below.

Focused tests: [`crates/bb_rules/tests/morale_modifiers.rs`](crates/bb_rules/tests/morale_modifiers.rs)
covers default state, all multipliers and odd-value flooring, base-stat preservation, zero and
`u32::MAX` arithmetic, both-side hit chance, surround/clamp order, ordinary and opportunity attack
chance agreement, query purity, deterministic commands, the pinned Steady hash and `skip_turn`
coexistence, and that attacks, a lethal hit and round changes cause no morale changes of their own. *(The
lethal-hit fixture of that test now gives every unit Resolve 100: since the ally-death rule a death
draws checks for the victim's allies, and Resolve 100 always passes, so morale stays unchanged and the
original no-direct-transition purpose is preserved.)*

Validation: `cargo test --workspace --locked --offline` passed (133 tests, 0 failed), and
`cargo run --locked --offline -p bb_rules --bin headless_smoke -- --seed 1` printed
`OUTCOME=Victory REASON=Elimination ROUNDS=6 HASH=892ea5d6c8f754c6` (unchanged at that time; superseded
by the Resolve-based morale checks below).

## Resolve-based morale checks — partial UC-COMBAT-018 coverage

`Stats.resolve` is a new unmodified base attribute (morale never changes it). Every scenario unit gets
`scenario::DEFAULT_RESOLVE = 50`, an invented **APPROXIMATION** value with no catalog or runtime source.

**Trigger** (`MORALE_CHECK_HP_LOSS_THRESHOLD = 15`, traced to the "15+ HP damage" item of UC-COMBAT-018):
exactly one check when a melee attack hits, the defender survives, and the HP actually lost from that
attack is at least 15. Misses, subthreshold HP loss and armour damage alone do no check, and a lethal
hit gives the dead victim no heavy-hit check (`Attacked` then `Died`; a Nine Lives interception is not lethal and uses its actual HP loss, see "Nine Lives"; its surviving allies are checked
by the ally-death rule, see "Ally-death morale checks"). Ordinary and opportunity attacks share `resolve_attack`, so both defending
sides and both attack kinds use one implementation. Fearsome's lower threshold is not modeled.

**Formula** (explicitly approximate, not recovered): `chance = min(resolve, 100)`,
`roll = next_u64() % 100`, success = `roll < chance`. One extra RNG draw follows damage resolution for
every qualifying check, including checks at Breaking. Resolve 0 always fails; Resolve 100 or more
always passes; `roll == chance` fails.

**Result:** success leaves morale unchanged. Failure lowers Confident to Steady, Steady to Wavering,
or Wavering to Breaking. *(Historical wording: at the time of this section Breaking stayed Breaking as a
temporary lower boundary. Breaking now lowers to Fleeing and Fleeing stays Fleeing; see "Fleeing and
escape".)* *(Historical: at this stage nothing granted positive morale. The positive transitions now are the
turn-start self-rally of a Fleeing unit, see "Self-rally at turn start", and the killer's kill check, see
"Kill morale checks"; the morale check described here never raises morale.)*

**Events and order:** `Event::MoraleChecked { unit, roll, chance, before, after }` is emitted right after
`Attacked` (and before the next reaction, the `Moved` flush or any turn transition). The new morale
applies immediately to later attacks and reactions through the effective stats; the triggering attack
keeps the chance computed before its check. The native log shows one short line, state change first:
`E1 Steady>Wavering r60>=50` on failure, `E1 holds Steady r12<50` on success (the log is cut at 31
characters). Input handling is unchanged; the native morale status panel is covered below.

**Hash-schema change:** `state_hash` now appends each unit's base Resolve after its flag word, so every
state hash differs from earlier schemas and states differing only in Resolve are distinguishable. The
flag encoding (alive/waited/turn-started/skip-turn bits and morale codes) is unchanged. The Steady seed-1
opening hash moved from `d144cb90f7939be8` to `8c00302113cbe668`; historical hashes quoted above are
from the earlier schema.

Tests: [`crates/bb_rules/tests/morale_checks.rs`](crates/bb_rules/tests/morale_checks.rs) (13 tests: 14
versus 15 HP loss, armour absorption, misses, lethal hits, Resolve 0/100/above 100, roll-equals-chance
boundary, every transition and saturated Breaking, both defending sides, opportunity attacks with event
order and exact RNG draws, later-reaction chance versus the triggering chance, invalid-command and
preview purity, deterministic AI replay, hash sensitivity to Resolve) and a headless `bb_game` test
that the event reaches the log. The existing tests passed without RNG-expectation changes, except that the pinned Steady
opening hash in `morale_modifiers.rs` was updated for the new hash schema.

Validation: `cargo test --workspace --locked --offline` passed (148 tests, 0 failed), and
`cargo run --locked --offline -p bb_rules --bin headless_smoke -- --seed 1` printed
`OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=f152803a61acc9a3` (a newly observed result: the new
morale rolls and hash schema change the previous `ROUNDS=6 HASH=892ea5d6c8f754c6`). No native window
inspection was done.

## Native morale status panel — partial UC-COMBAT-018 / UC-UI-003 coverage

Each living unit's existing side-panel name row now shows a separate `MORALE <STATE> <PERCENT>%`
label, for example `MORALE WAVERING 90%`. The state comes directly from `Unit.morale`; the percentage
comes from `Morale::modifier_percent()` on every scene build. Steady is white/neutral, Confident green,
Wavering yellow and Breaking red, with the state name always visible. This percentage describes the
existing melee skill and defense scaling; it is not a direct multiplier on final hit chance. These
values and their rules remain **APPROXIMATION** as described above.

Dead rows retain `DEAD` and receive no live morale label. The name remains separately readable, and
the existing active-unit name highlight, HP/armour row, AP/fatigue row, previews, overlay markers, log
and End Round modal are preserved. Scene generation only reads current battle state and submits no
commands. The label wording, colors and placement are a UI **APPROXIMATION** with no original-UI
parity claim. This is partial coverage only; other checks and transitions, tooltips and map
morale indicators remain deferred. The Fleeing label and the FLED rows are covered in "Fleeing and
escape".

Headless scene tests in [`crates/bb_game/src/render.rs`](crates/bb_game/src/render.rs) cover all four
labels and colors on both sides, rules-derived percentages, a fixed-seed surviving 15-HP hit that
changes Steady to Wavering and appears in the next scene, state/RNG purity, dead-row handling, active
name highlighting, and longest-label geometry against the panel and resource rows.

Validation: `cargo test --workspace --locked --offline` passed (152 tests, 0 failed);
`cargo build --workspace --locked --offline` passed. Native visual inspection was unavailable because no
display session was present. The source catalog remains incomplete.

## Fleeing and escape — partial UC-COMBAT-003 / 007 / 018 / 019 / 023 coverage

Implemented in `unit.rs`, `lib.rs`, `rules.rs`, `ai.rs` and the native `main.rs` / `render.rs`. Provenance:
the Fleeing state, its 70% melee scaling, loss of control, loss of ZoC, the -1000 initiative, the
automatic run to a map edge, the retreat rule ("player all fled: retreat, not win") and "victory when all
enemies are dead or fled" come from the [UC-COMBAT-003/018/019/023 catalog text](docs/source/10-tactical.md)
(catalog-stated, not runtime-confirmed). The concrete numbers and the route policy below are slice
**APPROXIMATION**s; the original runtime was not executed.

**Rules.**
- `Morale::Fleeing` (70% scaling of base melee skill and defense, hash code 4; a Dodge bonus is not scaled). A failed Resolve check lowers Breaking to
  Fleeing; Fleeing stays Fleeing. Check draws are unchanged (the extra draw is consumed in every state).
  Entering Fleeing clears a pending `skip_turn`. *(Historical: at this stage no positive transition existed; the positive transitions now are the turn-start self-rally of "Self-rally at turn start" and the killer's check of "Kill morale checks".)*
- `Unit.fled` (initially false) and `Unit::is_present()` (`alive && !fled`). Occupancy, `reachable`, the
  queue, attack targets, `legal_targets`, surround participants, outcome checks, the AI and native tokens
  all use presence. An escapee keeps `alive`, HP, armour and stats: escape is not death (`Died` is only
  for deaths; escapes emit `Event::Fled`).
- A Fleeing unit stops exerting ZoC immediately (`enemy_controllers_at`). Its -1000 initiative
  (`FLEEING_INITIATIVE_PENALTY`, `i64` arithmetic) applies at the *next* round's existing queue sort; the
  current queue is not reordered.
- `Command::Flee { unit }` is the automatic fleeing turn. Precedence: `BattleOver`, `NotActiveUnit`, then
  `RuleError::NotFleeing` (Flee for a non-fleeing actor) or `RuleError::FleeingUnit` (every ordinary
  command - Move, Attack, Recover, Wait, EndTurn, EndRound - for a Fleeing actor; checked before
  `NotPlayerUnit`). `preview_move` returns the same `FleeingUnit` error. Rejections change neither state
  nor RNG.
- `Flee` makes at most one neighbouring step. **Route policy (APPROXIMATION, no catalog source):** the
  target is a passable, unoccupied map-edge hex at the smallest existing-BFS distance (obstacles and
  occupied hexes block); ties go to the endpoint with the greatest distance from its nearest present
  enemy, then to the lowest axial coordinate (q, then r); the path and its first step come from the
  existing BFS neighbour order (fewest steps, not least AP; an uphill first step needs its full AP cost, else the turn ends). The route offers no safe-route guarantee and may cross enemy ZoC. Route
  choice consumes no RNG.
- The step is made through the existing `walk`, so costs, opportunity attacks, stops and deaths are the
  ordinary ones. Every `Flee` command moves, spends the attempted step's AP/fatigue (also when a hit stops
  the mover), escapes, or ends the turn through the normal advancement (no edge route, or the next step is
  unaffordable). A unit already on an edge escapes without cost, even with 0 AP. A successful step onto an
  edge escapes immediately; a stopped or killed mover never escapes or advances twice.
- Escape sets `fled`, removes the unit from the queue and occupancy, emits `Fled`, then checks the
  outcome before advancing; no turn starts after termination.
- Outcomes: no player present -> `Outcome::Retreat` if a player escaped alive, otherwise `Defeat`; no
  enemy present while players remain -> `Victory`. `EndReason::Escape` marks results involving a surviving
  escapee of either side; ordinary elimination and the time limit are unchanged. Players are judged first,
  so "all players fled, enemies also gone" is a Retreat.
- `EndRound` neither marks Fleeing players nor suppresses their automatic turns.
- `ai::plan_turn` returns exactly one `Flee` for a Fleeing active unit of either side; the normal policy
  is untouched (it ignores escapees).
- State hash: flag bits alive 0, waited 1, turn_started 2, skip_turn 3, morale code at bits 4-6, `fled`
  bit 7; `Retreat` and `Escape` hash as 3. States without Fleeing, fled units or the new terminal values
  keep their previous hashes.

**Native client.** A Fleeing player unit offers no human actions (no click, `E`, `W`, `X`, End Round
button or dialog) and is driven by the existing AI cadence (`ai_step`, 250 ms). The End Round
confirmation count excludes Fleeing units. The panel shows `MORALE FLEEING 70%`, escapees get a distinct
`FLED` row (not `DEAD`; HP/armour rows stay), the log shows a short `P1 escapes`, a Retreat shows a
`RETREAT` banner (and `UNITS ESCAPED` for any result with the Escape reason). Escapees have no map token
and are not hover targets. Colours, wording and placement are UI **APPROXIMATION**s.

**Deferred explicitly:** Rally the Troops and every rally source other than the turn-start self-rally of
"Self-rally at turn start" and the killer's check of "Kill morale checks" (both implemented), Unbreakable, other morale triggers and immunities,
any "trying to leave" interpretation of UC-COMBAT-048 beyond ordinary movement (its kill-check and movement
statements are partially covered, see "Kill morale checks" and "Map-edge movement penalty"), stance cancelling, defense
bonuses, renown, XP, loot and every campaign consequence of fleeing or retreating, ranged/Fearsome
behaviour, and original-UI parity. This is partial catalog coverage.

**Tests:** [`crates/bb_rules/tests/fleeing.rs`](crates/bb_rules/tests/fleeing.rs) (32 tests: transitions
and 70% modifiers, entering Fleeing with ZoC removal and unchanged queue, next-round penalty with extreme
initiative, hash separation and the retained seed-1 pre-fleeing hash, command rejection and preview
purity, error precedence, edge routing with tie-breaks, obstacles, occupied exits, a shortest-route
oracle over every start hex, no route, insufficient AP/fatigue, zero-AP edge escape, missed/stopping
reactions, death in flight, survival versus death, occupancy/queue/targets/surround, last-enemy and
last-player escape, mixed dead/escaped players, round and time-limit transitions after an escape,
EndRound interaction, AI plans, and resolve-0 AI battles that replay identically and end in Retreat,
escaped Victory and so on) plus headless native tests in `bb_game` (disabled human controls, automatic
driving and log, retreat stop, confirmation count, labels, FLED rows, tokens, hover and banner). The
saturated-Breaking expectations in `morale_checks.rs` were replaced by explicit Breaking -> Fleeing ->
Fleeing coverage; all other earlier tests are unchanged.

Validation: `cargo test --workspace --locked --offline` passed (192 tests, 0 failed), and
`cargo run --locked --offline -p bb_rules --bin headless_smoke -- --seed 1` printed
`OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=f152803a61acc9a3` (the actual output; it equals the
previous result because no unit reaches Fleeing in the seed-1 battle - not forced). No native window
inspection was done (no display session).

## Self-rally at turn start — partial UC-COMBAT-018 / 019 coverage

**APPROXIMATION.** The catalog (UC-COMBAT-018/019) mentions a Fleeing unit recovering, but no recovered
original rule is claimed: the Resolve formula, the Breaking destination, the timing and the edge
precedence below are slice choices. Implemented in `rules.rs` (`begin_turn`, `self_rally`), `lib.rs`
(`Event::SelfRallyChecked`) and the native log in `main.rs`.

- **Timing:** inside the existing once-per-round `begin_turn`, after the normal AP refresh and fatigue
  regeneration and right after `TurnStarted`. The `turn_started` guard makes it at most one check per
  unit per round: `Wait`, repeated `Flee` commands and returning to the front of the queue never re-check,
  and a unit that becomes Fleeing after its turn started gets no immediate extra check (it is checked at
  its next round's turn start). No new persistent flag; hash codes and schema are unchanged.
- **Eligibility:** the unit is present and Fleeing, is not on a map-edge hex, and no present enemy is
  adjacent. Presence and side are checked directly: an adjacent *fleeing* enemy still prevents the check
  even though it exerts no ZoC; dead and escaped enemies and adjacent allies do not. **Edge precedence
  (APPROXIMATION):** edge units are skipped, preserving the accepted immediate zero-cost edge escape.
- **Formula:** `chance = min(base Resolve, 100)`, `roll = next_u64() % 100`, success = `roll < chance`.
  Exactly one draw per eligible check; ineligible units consume none.
- **Result:** success changes Fleeing to Breaking (the destination is an APPROXIMATION) and clears any
  pending `skip_turn`. Failure keeps Fleeing. Nothing else changes: no other morale change, no extra AP
  or fatigue. Queue order is not changed after recovery.
- **Event:** `Event::SelfRallyChecked { unit, roll, chance, before, after }` directly after
  `TurnStarted`; `MoraleChecked` stays reserved for the heavy-hit check.
- **Consequences:** a recovered unit regains ordinary command eligibility and ZoC immediately (a recovered
  player is handed to the human, a recovered enemy follows ordinary AI policy); a failed unit stays
  `FleeingUnit`-restricted and is driven by `Flee`. The next round's initiative uses the current morale.
- **Native log:** `P2 rallies r12<50` on success and `P2 keeps fleeing r88>=50` on failure; the outcome
  words come first because the log is cut at 31 characters.
- **Deferred:** Rally the Troops, perks, mood, Unbreakable and every other positive morale transition
  (the killer's check of "Kill morale checks" exists), other morale triggers and route changes (the voluntary Retreat control now exists, see "Party retreat order"). Catalog coverage remains partial.

Tests: [`crates/bb_rules/tests/self_rally.rs`](crates/bb_rules/tests/self_rally.rs) (23 tests: both sides,
Resolve 0/100/above 100, roll-equals-chance boundary, adjacent ordinary and fleeing enemies, distant,
dead and escaped enemies, edge units with zero-AP escape, event/RNG ordering and ordinary refresh,
restored commands and ZoC, retained `FleeingUnit` rejections, unchanged queue, at-most-once across
`Wait`/`Flee`/rounds/mid-turn collapse, EndRound interaction and skip clearing, next-round initiative,
purity of queries and rejections), two headless `bb_game` tests (player hand-off and logs, continued
automatic flight) and a narrow update to `fleeing.rs::end_round_neither_marks_fleeing_players_nor_suppresses_their_turns`
(its fleeing P3 now has Resolve 0 and the expected events include the failed `SelfRallyChecked`).

Validation: `cargo test --workspace --locked --offline` passed (217 tests, 0 failed), and
`cargo run --locked --offline -p bb_rules --bin headless_smoke -- --seed 1` printed
`OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=f152803a61acc9a3` (actual output, unchanged because no
unit reaches Fleeing in that battle). No native window inspection was done (no display session).

## Ally-death morale checks — partial UC-COMBAT-018 coverage

**APPROXIMATION.** The catalog (UC-COMBAT-018) names an ally death as a morale trigger but does not
establish who is affected. This slice affects **every present same-side ally of the fallen unit,
regardless of distance** (an invented radius policy, not a recovered rule). Implemented in `rules.rs`
(`check_ally_death_morale`, called from the shared `resolve_attack`, plus the private
`negative_morale_check` that `check_morale` now shares) and `lib.rs` (`Event::AllyDeathMoraleChecked`).

- **Trigger:** a melee death, ordinary or opportunity attack, after `alive = false`, queue removal and
  `Died`. Misses, surviving hits (heavy or not, including a Nine Lives interception), `Fled` events and self-rally trigger nothing.
- **Affected units:** present units on the fallen unit's side in ascending unit-id order, whatever their
  queue status: finished turns, `Wait`-deferred, `EndRound`-marked and already-Fleeing allies are all
  checked. The casualty, enemies, dead units and escapees are not.
- **Check:** the accepted negative-check formula and effects: `chance = min(base Resolve, 100)`, one
  `next_u64() % 100` draw, success when `roll < chance`, otherwise `Morale::lowered()`. A failure that
  leaves the unit Fleeing clears its pending `skip_turn` as before. Exactly one check per ally per
  death, also for Fleeing allies (they stay Fleeing). A morale change never triggers further checks
  (propagation is deferred) and the current queue is not reordered; the new morale applies at once to
  effective attributes, ZoC and automatic control.
- **Events/RNG order:** `Attacked`, `Died`, then one `AllyDeathMoraleChecked { unit, fallen, roll, chance,
  before, after }` per ally (draws follow the attack roll and damage draw), all before any later
  reaction, outcome event or turn advancement. With no eligible ally the previous event sequence and RNG
  usage are unchanged. `MoraleChecked` stays heavy-hit only and `SelfRallyChecked` turn-start only.
- **Native log:** result and fallen ally first, numbers only if they fit 31 characters:
  `E2 Steady>Wavering, E1 died`, `E2 holds Steady, E1 died`, `E2 still Fleeing, E1 died`.
- **Deferred:** morale-drop propagation, party-wide kill bonuses (the killer's own check is described in
  "Kill morale checks"), surrounding and fear checks, morale effects from
  escape, rally changes, perks and immunities. Catalog coverage remains partial.

Tests: [`crates/bb_rules/tests/ally_death_morale.rs`](crates/bb_rules/tests/ally_death_morale.rs) (16 tests:
both sides, ordinary and opportunity kills, id order versus queue order, exact RNG advancement,
exclusion of casualty/enemies/dead/escaped, distance- and queue-status-independence, Resolve
0/100/above 100 and the equality failure boundary, all failure transitions and Fleeing allies, no
propagation, immediate attribute/ZoC effect, marked ally entering Fleeing and keeping its automatic turn,
Died-then-check ordering before turn advancement, round change and time limit, final casualty without
allies, death of the active fleeing mover, non-triggers, rejection/preview purity, deterministic AI
replay) and a headless `bb_game` test for the log. Existing tests changed narrowly for the new draws:
`morale_checks.rs` (the lethal-hit test now separates the dead victim's missing check from its
allies' checks), `morale_modifiers.rs` (Resolve 100 so the death keeps morale unchanged), `zone_of_control.rs`
and `fleeing.rs` (event/RNG expectations), `end_round.rs` (reaction death events) and the pinned seed-1
hash in `fleeing.rs`.

Validation: `cargo test --workspace --locked --offline` passed (234 tests, 0 failed), and
`cargo run --locked --offline -p bb_rules --bin headless_smoke -- --seed 1` printed
`OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=ccd911452772673b` (actual output: still Victory by
Elimination in 4 rounds, hash changed from `f152803a61acc9a3` because the new ally-death draws alter the
RNG stream; not forced). Earlier hashes quoted above are historical. No native window inspection was done.

## Kill morale checks — partial UC-COMBAT-018 / UC-COMBAT-048 coverage

**Catalog-stated authority, not runtime-confirmed:** [UC-COMBAT-018](docs/source/10-tactical.md) covers
morale checks on kills. [UC-COMBAT-048](docs/source/10-tactical.md) states that a positive morale check
on the edge fails and that moving a non-Fleeing brother onto the tactical edge forces morale toward
Breaking. The killer-only affected set, formula, scheduling and which edge statement is implemented
are **APPROXIMATION** choices without original-runtime confirmation. Implemented in `unit.rs`
(`Morale::raised`), `rules.rs` (`check_kill_morale`, called from the shared `resolve_attack`) and
`lib.rs` (`Event::KillMoraleChecked`).

- **`Morale::raised()`:** Breaking to Wavering, Wavering to Steady, Steady to Confident, Confident stays
  Confident, Fleeing stays Fleeing. A pure `const fn`: it performs no check and mutates nothing.
- **Trigger and eligibility:** after a hostile target dies in melee, exactly one positive Resolve check
  for its killer if the killer is present and not Fleeing. Both sides, ordinary attacks and opportunity
  attacks, including reactions against fleeing or withdrawing movers. A retreating killer stays eligible
  and keeps its retreat intent. Applying this check to both factions is an **APPROXIMATION** scope choice;
  the catalog actor is described as a brother. Misses, surviving hits (heavy or armour-only), escapes and rejected
  commands produce no check and no draw. Only the killer is checked: no other unit's morale changes
  through this mechanism, there is no propagation and no party-wide bonus.
- **Check:** using the killer's current position, `chance = 0` on the outermost map ring and
  `min(base Resolve, 100)` elsewhere. The victim's position and movement destination do not decide the
  chance. One `next_u64() % 100` draw is consumed, success is `roll < chance` (roll equal to chance
  fails), and success applies `raised()`. At the edge, the zero chance leaves morale unchanged. The
  draw and event still happen at chance zero and when the killer is already Confident; base Resolve is
  preserved.
- **Events/RNG order:** `Attacked`, `Died`, the existing `AllyDeathMoraleChecked` events in their
  existing order, then `Event::KillMoraleChecked { unit, fallen, roll, chance, before, after }`, then any
  `BattleEnded`, round or turn-transition events. The final kill is checked before `BattleEnded`. The kill
  draw follows the attack roll, damage draw and one draw per surviving victim-side ally, and precedes
  turn-start draws such as a later self-rally.
- **Consequences:** the triggering attack keeps its previously calculated chance; later previews and
  attacks use the killer's updated morale through the existing effective-stat calculation. Base `Stats`,
  retreat intent, `skip_turn` flags and the current queue order are untouched, and the state hash
  gained no field (morale already has its hash code).
- **Native log:** an improvement shows the step, `P1 Steady>Confident r12<50`; unchanged morale (failed
  check or already Confident) reads `P1 stays Steady r88>=50` / `P1 stays Confident r12<50`. Each line fits
  the 31-character log limit and the five lines of a kill with two allies fit the six visible entries; the
  panel's existing morale labels need no change.
- **Deferred:** party-wide kill bonuses, morale propagation, mood, perks, Unbreakable, ranged combat and
  campaign systems. UC-COMBAT-048 is partially covered here (failed positive check) and by "Map-edge
  movement penalty"; complete edge-morale coverage is not claimed.

Tests: [`crates/bb_rules/tests/kill_morale.rs`](crates/bb_rules/tests/kill_morale.rs) (18 tests: every
`raised()` mapping, both sides, ordinary and opportunity kills, successful and failed checks, Resolve
0/100/above 100 and the equality boundary; edge killers at all six side representatives and corners,
both factions and every eligible morale state; exact zero-chance draws for Resolve 100 and above; an
interior killer versus an edge victim; edge opportunity kills including a retreating reaction killer;
an edge final kill before `BattleEnded`; zero, one and two surviving allies with exact event order and
RNG advancement including a following self-rally; non-triggers and rejected commands preserving state and
RNG) and a headless `bb_game` log test. Existing exact-event and RNG expectations were extended with the
new event and draw, keeping all earlier assertions: `ally_death_morale.rs`, `morale_checks.rs`,
`zone_of_control.rs`, `end_round.rs`, `fleeing.rs` (including the pinned seed-1 hash) and the `bb_game`
ally-death log test.

Validation: `cargo test --workspace --locked --offline` passed (310 tests, 0 failed), and
`cargo run --locked --offline -p bb_rules --bin headless_smoke -- --seed 1` printed
`OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=3fefd4ead00e829f` (actual output: still Victory by
Elimination in 4 rounds, hash changed from `ccd911452772673b` because the new kill draws alter the RNG
stream; not forced). Earlier hashes quoted above are historical. No native window inspection was done.

Task T18 validation: `cargo test --workspace --locked --offline` passed (315 tests, 0 failed), and
`cargo run --locked --offline -p bb_rules --bin headless_smoke -- --seed 1` printed
`OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=3fefd4ead00e829f`. The hash matches the preceding
kill-check validation: edge checks still consume their one normal draw, while this task changes only
the effective chance. No native window inspection was done.

## Map-edge movement penalty — partial UC-COMBAT-048 coverage

**Catalog-stated authority, not runtime-confirmed:** [UC-COMBAT-048](docs/source/10-tactical.md) states that
moving a non-Fleeing brother onto the tactical edge forces morale toward Breaking. The **direct
assignment to Breaking**, the **application to both sides** and the **scheduling** below are
**APPROXIMATION** choices without original-runtime confirmation; complete original-runtime parity is not
claimed. Implemented in `rules.rs` (`walk`, flagged for ordinary movement only) and `lib.rs`
(`Event::EdgeMoraleChanged { unit, at, before, after }`); the native log is in `main.rs`.

- **Trigger/timing:** after each successful ordinary `Command::Move` step whose arrival hex is on the
  outermost ring, including intermediate route hexes and movement between edge hexes, a present,
  non-Fleeing, non-retreating mover at Confident, Steady or Wavering is set directly to Breaking. Breaking
  stays Breaking and emits nothing. It consumes no RNG, ignores Resolve (0 and 100 behave identically,
  base Resolve is untouched) and adds no AP or fatigue cost.
- **Event order:** the completed movement segment is flushed as `Moved` first, then
  `EdgeMoraleChanged` (only when morale changes), and the change is applied before any later departure
  reaction on the same route. A later opportunity attack therefore uses the Breaking effective stats
  (80%); the walk continues normally and later `Moved` segments follow. Leaving the edge never restores
  morale.
- **Preview:** `MovePreview::first_edge_penalty` reports the first arrival hex that would trigger this
  for the mover's current morale (shared `edge_penalty_applies` helper; Breaking gives `None`). It is
  conditional: a reaction before the edge can still stop or kill the mover, which the preview does not
  simulate. The native hover box shows it as `IF REACHED` (APPROXIMATION presentation); see "Movement
  preview".
- **Not triggered:** a stopped or killed mover gets nothing for an edge hex it did not reach; rejected
  moves and `preview_move` mutate neither morale nor RNG; initial placement on an edge, turn refresh,
  Recover, Wait, EndTurn, EndRound and attacks do not trigger it.
- **Automatic movement is exempt:** `Flee` and `Withdraw` share `walk` with the flag off, so their edge
  arrival escapes immediately with `Fled` and no morale change. Ordinary moves are rejected for Fleeing and
  retreating units, so those never receive the penalty.
- **Ordinary arrival does not extract:** the unit stays present, alive and controllable at Breaking with
  its remaining budget and queue position; no `Fled` and no `BattleEnded` merely for reaching the edge.
- **Consequences:** no hash field was added (the morale code already encodes the result). The kill-check
  edge restriction ("Kill morale checks") and its RNG consumption are unchanged.
- **Native log:** `P1 edge Steady>Breaking` (at most 25 characters, within the 31-character limit) after
  the `P1 moves N` line; the panel's existing morale display is unchanged.
- **Behaviour change in the scenario:** AI units that walk onto the edge now become Breaking, so the
  seed-1 AI replay changed (see Validation); the AI policy itself is unchanged.
- **Deferred:** every other morale trigger (including any "trying to leave" interpretation beyond ordinary
  movement), Rally the Troops, Unbreakable, mood and perks. Whether the original lowers morale more
  gradually or exempts some units is unknown.

Tests: [`crates/bb_rules/tests/map_edge_morale.rs`](crates/bb_rules/tests/map_edge_morale.rs) (10 tests:
all six sides and corners, both factions, every eligible starting state plus Breaking, Resolve 0 and 100,
exact event order, unchanged RNG, ordinary costs, presence and queue position; a multi-step route where
the edge arrival precedes a departure reaction that uses Breaking stats (hit, miss and lethal); stopped and
killed movers before arrival; rejected moves and preview purity; initial placement and non-movement
actions; edge-to-edge, interior and repeated movement; unchanged Flee/Withdraw escape) and a headless
`bb_game` log test. Exact-event and replay expectations in `ai.rs` (`b1b_replay_to_e2`, now two `Moved`
segments around the new event) and `fleeing.rs` (the seed-1 replay test, renamed
`seed_one_ai_battle_replay_reaches_fleeing_after_edge_morale`) were updated; no earlier assertion was
dropped. Two further tests check that `first_edge_penalty` agrees with the executed
`EdgeMoraleChanged` on every side and corner and survives a preceding reaction.

Validation: `cargo test --workspace --locked --offline` passed (326 tests, 0 failed), and
`cargo run --locked --offline -p bb_rules --bin headless_smoke -- --seed 1` printed
`OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=dfce9b8d74bcaf93` (actual output; the previous
`OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=3fefd4ead00e829f` is historical evidence from before this
task). No native window inspection was done.

## Party retreat order — partial UC-COMBAT-022 coverage

Rules are headless (`bb_rules`); the native window launches them through a **Retreat** button and the `V`
key behind a mandatory confirmation (see "Native Retreat button, shortcut and confirmation" below).
Provenance: the catalog ([UC-COMBAT-022](docs/source/10-tactical.md)) states that units already
on a map edge escape instantly, the others auto-path to the nearest edge, deaths during the run are real
and a retreat is never a clean teleport. Everything below about scheduling, route integration, intent
persistence and naming is a slice **APPROXIMATION**; the original runtime was not executed.

- `Unit.retreating` (initially false, reset only by `Unit::new` for a new battle, never at a round
  boundary) is retreat intent, separate from morale. `Command::Retreat { unit }` is the party order and
  `Command::Withdraw { unit }` is one automatic withdrawing action. New errors: `RuleError::RetreatingUnit`
  and `RuleError::NotRetreating`; new event `Event::RetreatOrdered { unit }` (the issuer).
- **Retreat** is accepted only from the present, controllable active player. `BattleOver` and
  `NotActiveUnit` keep precedence; then `RetreatingUnit` / `FleeingUnit` (already-retreating or Fleeing
  issuer, checked first as for every ordinary command), then `NotPlayerUnit` (enemy issuer). Rejections
  change neither state nor RNG. It costs no AP or fatigue and consumes no RNG.
- **On a valid order** every present player (turn finished, deferred, Fleeing or active) is marked
  retreating and loses a pending `skip_turn`; enemies, casualties and escapees are untouched. `RetreatOrdered`
  is emitted, then all marked players already on a map edge escape immediately in ascending id order
  (`Fled` each, alive with HP and equipment kept, no movement cost, no reactions, no advancement between
  exits). The outcome is checked once: a terminal result (`Retreat`/`Escape`, existing semantics) ends
  the battle with no further turn; otherwise an issuer that is still present keeps its turn without a
  refresh (AP, fatigue and queue position unchanged), and an issuer that escaped advances the turn exactly once.
- **Withdraw** requires retreat intent (`NotRetreating` otherwise) and shares the existing one-step
  automatic movement with `Flee` (`rules.rs::automatic_step`): same deterministic edge-route policy
  (see "Fleeing and escape"), ordinary AP/fatigue costs, opportunity attacks, stopping hits, deaths, edge
  escape (also with 0 AP when standing on an edge) and the turn-ending cases (no route, unaffordable
  step). A retreating unit that is also Fleeing remains eligible. Route choice consumes no RNG.
- Ordinary commands and `preview_move` for a retreating actor return `RetreatingUnit` (reported before
  `FleeingUnit` when both apply). `ai::plan_turn` returns exactly one `Withdraw` for an active retreating
  unit before any ordinary or morale-fleeing policy. `EndRound` does not mark retreating units, and a
  pending skip never suppresses their automatic turns (their other turn-start processing, refresh and
  fatigue regeneration, is intact).
- Intent persists across rounds and self-rally: a successful rally may change morale (Fleeing to
  Breaking) but never cancels the ordered withdrawal, and a withdrawing unit whose morale later becomes
  Fleeing still gets `Withdraw`.
- State hash: `retreating` is flag bit 8; states with no retreating unit hash exactly as before.
- Native: retreating player turns are driven by `ai_step` at the existing cadence; they get no human
  control, no selection-dependent previews, hit labels or control overlay, no End Round or Retreat
  availability and no place in the End Round forfeiture count. `RetreatOrdered` logs `retreat ordered`.

### Native Retreat button, shortcut and confirmation

Labelled **APPROXIMATION** based on the catalog-stated behavior of UC-COMBAT-022 (a retreat order) and
UC-UI-007 (modal confirmation). The `V` shortcut, wording, colours and layout are slice placeholders; none
of them was observed in the original runtime, and no original-UI parity is claimed.

- **Retreat (V)** button sits in the side panel on the End Round row, right of that button; the shortcut hint
  lists `V RETREAT`. The button and `V` (either case) are enabled exactly when End Round is: a present,
  controllable active player in an unfinished battle. They are disabled (button drawn dim, click and key do
  nothing, no error) for an enemy turn, a finished battle, a dead or escaped actor, and Fleeing or retreating
  actors.
- One action-tagged pending intent (`EndRound` or `Retreat`) holds actor, round and `state_hash`. At most one
  exists: while it is open, a second launch (button or key, either action) cannot switch or confirm it.
- The dialog states that all present player units will withdraw, units on a map edge escape immediately, the
  remaining units move automatically and may die, and that the order cannot be cancelled after confirmation.
  It shows the number of present player units, which includes Fleeing units and units whose turns have
  finished and excludes dead and escaped ones. It advertises no renown penalty or defense bonus (neither is
  implemented).
- `Enter` or `Confirm` submit; `Esc` or `Cancel` close without a command. While either dialog is open map
  clicks and unrelated shortcuts are consumed and automatic commands (AI and withdrawing turns) are paused.
  Repeated launch inputs never confirm. Opening, cancelling and rendering touch neither `BattleState` nor RNG.
- Confirmation removes the pending intent, revalidates actor, round, `state_hash` and current eligibility, and
  then submits exactly one `Command::Retreat` through `App::run`; stale intent yields a
  `RETREAT DISCARDED: BATTLE CHANGED` message and no command. Edge exits, outcome handling, logging and the
  automatic `Withdraw` turns afterwards are the unchanged rules-driven behavior described above.
- Restart and new seed clear any pending confirmation and keep the `Z` overlay preference. The existing 350 ms
  post-close click guard applies to both actions. Both launch buttons and the modal use shared rectangles in
  `layout.rs` for drawing and hit testing; they are disjoint from each other, the log, the shortcut hint and
  the Confirm/Cancel buttons. The opaque modal is drawn last.

**Deferred explicitly:** the difficulty-scaled
defense bonus while moving through ZoC, renown, injuries, loot, XP and every campaign consequence, the
"deserter" first-action observation (UNCONFIRMED) and ZoC changes for retreating units. Coverage of
UC-COMBAT-022 is partial.

Tests: [`crates/bb_rules/tests/retreat.rs`](crates/bb_rules/tests/retreat.rs) (35 tests: eligibility and
rejection purity, all-player marking and skip clearing, multi-unit and off-turn edge exits with event
ordering, issuer advancement and terminal exits, shared Flee/Withdraw step, occupied/blocked routes,
insufficient budgets, reaction misses, stopping hits, death and escape survival, `Retreat` versus
`Defeat`, persistence across rounds and self-rally, morale collapse, `EndRound`, bounded deterministic
replay, hash bit) and six headless tests in `bb_game` (`main.rs`, `render.rs`) for automatic driving,
disabled human actions, End Round counts and the log line (no existing test was changed at that point). The
native Retreat control adds App tests in `main.rs` (launch by button/`V`, eligibility rejection, cancellation
purity, modal isolation, paused automatic commands, comparison with a direct `Command::Retreat` including an
edge escape and a terminal retreat, End Round/Retreat dispatch separation, stale intent, repeated inputs,
click guard with injected `Instant`s, restart clearing), `render.rs` tests (wording, button states, party
counts, purity, modal ordering) and `layout.rs` tests (disjointness, bounds, label fit). The generalization
renamed the pending-intent field/struct and the scene flag and updated the existing End Round tests
accordingly; their assertions were kept, apart from the controls hint (now `V RETREAT`, `N SEED`).

Validation: `cargo test --workspace --locked --offline` passed (275 tests, 0 failed), and `cargo run
--locked --offline -p bb_rules --bin headless_smoke -- --seed 1` printed
`OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=ccd911452772673b` (actual output, unchanged because the
seed-1 AI battle never issues `Retreat`). No native window inspection was done (no display session).

Validation of the native Retreat control: `cargo test --workspace --locked --offline` passed (296 tests, 0
failed) and `cargo build --workspace --locked --offline` succeeded. Visual inspection of the native window
was **unavailable** (no display session: `DISPLAY` and `WAYLAND_DISPLAY` unset, no Xvfb), so the layout is
verified by geometry tests only.

## Adrenaline (opt-in loadout, partial UC-COMBAT-066 coverage)

`Command::Adrenaline { unit }` lets a present, active, controllable unit of either side add initiative to
its place in the **next** round's order. Catalog authority (catalog-stated, not runtime-confirmed):
[UC-COMBAT-066](docs/source/10-tactical.md) (1 AP, 20 fatigue, next-round first act) and UC-COMBAT-003
(initiative ordering). Everything below is an **APPROXIMATION**:

- Availability is the explicit capability flag `Unit.has_adrenaline` (false in `Unit::new`; the default
  Flat and Hills scenarios have no capable unit). It is not a perk system. Either side may use it through
  scripted commands.
- Opt-in loadout: `--adrenaline` (both binaries) gives the three **player** units `has_adrenaline = true`
  (`scenario::adrenaline_battle` is `scenario::battle` plus that flag). It sets nothing pending, consumes no
  RNG and keeps the units, budgets, queue and RNG stream of the default scenario; only the hashed capability
  bit differs. Both binaries now construct the battle through the shared
  `scenario::launch_battle_for(LaunchArgs)`, and `scenario::parse_args` returns
  `LaunchArgs { seed, terrain, adrenaline, rotation, footwork, fast_adaptation, dodge, nine_lives, berserk, pathfinder, indomitable }` (the capability fields are described under "Rotation", "Footwork", "Fast Adaptation", "Dodge", "Nine Lives", "Berserk", "Pathfinder" and "Indomitable"). `scenario::launch_battle_with(seed, terrain, adrenaline,
  rotation)`, `scenario::adrenaline_battle` and `scenario::launch_battle(seed, terrain, adrenaline)` remain
  as compatibility APIs with unchanged results. Without the flag nothing changes (the default seed-1
  results are the same as before).
- Cost: `ADRENALINE_AP` = 1 AP and `ADRENALINE_FATIGUE` = 20 fatigue (checked addition, capped at
  `fatigue_max`). It grants or refreshes no AP, changes no `Stats`, emits one `Event::AdrenalineUsed`, does
  not end the turn or move the unit in the queue, and consumes no RNG (also when rejected).
- Validation order: `BattleOver`, `NotActiveUnit`, `RetreatingUnit`/`FleeingUnit`, then
  `MissingAdrenaline`, `AdrenalinePending`, `NotEnoughAp`, `TooFatigued`. Rejections change nothing. The
  last four are the public pure query `rules::adrenaline_availability(&Unit) -> Result<(), RuleError>`, which
  `apply` itself calls, so the native UI and the rules cannot disagree.
- Headless AI selection is an **APPROXIMATION inspired by UC-COMBAT-020 and UC-COMBAT-066**, not a claim of
  original AI parity. Inside the existing affordable-adjacent-attack branch, either side's actor selects
  `Command::Adrenaline` only if `adrenaline_availability` succeeds and it can still afford a basic attack:
  `AP >= ADRENALINE_AP + ATTACK_AP`, and checked
  `fatigue + ADRENALINE_FATIGUE + ATTACK_FATIGUE <= fatigue_max`. After activation, pending validation
  prevents another activation; the next planning call follows the existing weakest-adjacent-target attack
  policy if its budget remains available. Without an adjacent legal target, with either combined budget
  short, with missing capability or while pending, the existing attack, approach and fallback policy remains
  in force. Withdraw and Flee keep priority. Planning returns one command, reads no RNG and does not simulate
  the attack. The opt-in scenario still grants capability only to the three player units.
- `Unit.adrenaline_pending` is set on success. A second use is rejected while pending, including after
  `Wait` returns the actor to control. At the next `begin_round`, present pending units get
  `ADRENALINE_INITIATIVE_BONUS` (+2000) added to the signed order key (`initiative - fatigue / 2`, minus the
  Fleeing penalty), ties still by unit id; then every pending flag is cleared. The bonus lasts one queue
  construction, never changes base initiative and cannot bring back dead or escaped units. Becoming Fleeing
  or receiving retreat intent does not cancel it.
- `state_hash` flag bits 9 (`has_adrenaline`) and 10 (`adrenaline_pending`); false values hash as before
  (Flat and Hills seed-1 default results unchanged).
- Native button and shortcut (**APPROXIMATION** of the UC-COMBAT-066 skill use and the UC-UI-003/008 skill
  bar; wording, position and colours are slice placeholders): an **ADRENALINE (1)** button fills the unused
  strip above the unit rows (`layout::ADRENALINE_BUTTON`, x 684-828, y 3-43 since the T46 four-button reflow, the left of the four 144x40 skill buttons; the scale-1.25 title sits
  over two scale-1 rows) and shows `COST 1 AP 20 FAT` and `NEXT ROUND INIT +2000` from the rules constants. Key `1` does the same. Both are enabled only for the
  present, controllable (not Fleeing, not retreating) active player unit of a running battle whose
  `adrenaline_availability` succeeds, so without `--adrenaline` the button is always drawn disabled. A
  disabled activation is a silent no-op; an enabled one submits exactly one `Command::Adrenaline` through
  `App::run` (log `<unit> adrenaline: next round`). Button clicks are routed before map clicks from the
  shared rectangle; the End Round / Retreat dialogs consume the button and `1`, the click guard after a
  dialog still swallows it and repeated key events stay ignored. `R` and `N` rebuild the selected terrain
  **and** loadout and keep the overlay preferences.
- Pending marker: every present unit whose `adrenaline_pending` is set (also after its turn ended or while
  waiting) gets an amber `ADRENALINE: NEXT RD` tag at the right of its AP/fatigue line in the unit panel. It
  follows live state, disappears when the next round consumes the flag and is omitted for dead or escaped
  rows.
- Still deferred: skill learning or perks, Rotation/Footwork AI, broader skill attention and matching the
  original game's AI priorities. The Adrenaline choice above is deliberately limited to one affordable attack
  setup. Flat/Hills, seed 1, deterministic replay measurements after this task are recorded below; Flat keeps
  its outcome and round count, while Hills changes outcome, rounds and hash when Adrenaline is enabled. The
  native opt-in view was not inspected visually: the environment has no display. Layout is checked by
  geometry tests only.

Tests: [`crates/bb_rules/tests/adrenaline.rs`](crates/bb_rules/tests/adrenaline.rs) (rules and the
availability query), [`crates/bb_rules/tests/adrenaline_ai.rs`](crates/bb_rules/tests/adrenaline_ai.rs)
(both sides, legal fixture geometry, exact and one-short combined budgets, overflow boundary,
pending/capability/target fallbacks, retreat/Flee priority, purity and no-repeat progress),
[`crates/bb_rules/tests/terrain_presets.rs`](crates/bb_rules/tests/terrain_presets.rs) (flag parsing,
loadout, deterministic Flat/Hills command/event replays, actual Adrenaline events and smoke outputs) and
`bb_game` App, layout and renderer tests. The strip holds four buttons
(Adrenaline, Rotation, Footwork, Indomitable; see "Rotation", "Footwork" and "Indomitable"); they share the strip and none changes
another's behaviour.

The Adrenaline AI fixture triangle uses actor `(0,-2)` and targets `(1,-2)` then `(0,-1)` for both faction
pairings, preserving adjacency, target IDs and the 20/10 HP ordering. A local fixture guard checks every
present unit is on-map, off obstacles and at a distinct position; dead or escaped units are excluded.
Failing-first evidence for T33: before correcting the coordinates, the new geometry regression failed with
`present unit 0 occupies obstacle hex Hex { q: 0, r: 0 }` while the six existing AI tests passed. After the
correction, `cargo test -p bb_rules --test adrenaline_ai --locked --offline` passed (7 tests, 0 failed), and
`cargo test --workspace --locked --offline` passed (606 tests, 0 failed). `git diff --check` also passed.

Seed-1 `headless_smoke` outputs (current T32 policy; each run is repeated internally for determinism):

| Loadout | Flat | Hills |
| --- | --- | --- |
| Default | `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=dfce9b8d74bcaf93` | `OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=d0b704f0294173be` |
| `--adrenaline` | `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=d38ca185d725df1b` | `OUTCOME=Victory REASON=Elimination ROUNDS=7 HASH=406c2bf145855863` |
| `--rotation` | `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=eca98bcb3e525dfb` | `OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=17dd25afecfd72b6` |
| `--footwork` | `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=26f421838e407143` | `OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=9f83d1d40f1a2f6e` |
| `--adrenaline --rotation --footwork` | `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=fdc85eae43dd0ba3` | `OUTCOME=Victory REASON=Elimination ROUNDS=7 HASH=93baae9abf5c755b` |

Historical pre-T32 validation (before AI Adrenaline selection): `--adrenaline` printed Flat
`OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=fb858e080255c87d` and Hills
`OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=7a71a36ccd992df4`; those results are retained as historical
evidence and are superseded by the current rows above.

## Rotation (opt-in loadout and native ally targeting, partial UC-COMBAT-039 coverage)

`Command::Rotation { unit, target }` lets a present, active, controllable unit of either side swap hexes
with an adjacent ally in one action, without opportunity attacks. Catalog authority (catalog-stated, not
runtime-confirmed): [UC-COMBAT-039](docs/source/10-tactical.md) (swap with an adjacent non-hostile, 3 AP plus
a fatigue cost, no blocked hex) and UC-COMBAT-008 (leaving ZoC normally provokes; Rotation bypasses it).
UC-COMBAT-039 also states that neither participant is stunned or rooted. T70 implements the stunned-target
restriction in this slice; the catalog remains unconfirmed against the original runtime, and rooted status
is not modelled.
The rules are headless-first (`bb_rules`); the opt-in `--rotation` loadout and the native ally-targeting
mode below make them playable. There is **no AI casting**: Rotation is issued by the human player, scripted
commands and tests only.

- Capability: `Unit.has_rotation` (false in `Unit::new`, so every existing scenario and loadout is
  unchanged). **APPROXIMATION:** an explicit flag instead of a learned perk.
- Cost: `ROTATION_AP` = 3 (follows the catalog) and `ROTATION_FATIGUE` = 25 (**APPROXIMATION**: the catalog
  gives no number; 25 is a slice choice, not a recovered value). Both are fixed skill costs: no per-step
  movement cost and no uphill surcharge, whatever the heights. Only the actor pays; the target's AP,
  fatigue, turn and flags are untouched.
- Validation order (everything is checked before any mutation; rejections change nothing and consume no
  RNG): `BattleOver`, `NotActiveUnit` (also a non-present actor), `RetreatingUnit`/`FleeingUnit`, then
  `MissingRotation`; `InvalidTarget` unless the target is a present unit other than the actor on the
  actor's side that is neither stunned, Fleeing nor retreating (it need not have Rotation or a remaining turn);
  stunned targets use the existing `RuleError::InvalidTarget`, before coordinate and affordability checks;
  `Unreachable` unless both occupied hexes are in the map and not obstacles; `NotAdjacent`; `NotEnoughAp`
  (needs 3); `TooFatigued` (checked add of 25 within `fatigue_max`, so `u32::MAX` overflow is rejected).
  The stunned-target restriction follows the catalog but is not confirmed against the original runtime.
  Remaining target restrictions, capability gate, error names and precedence are **APPROXIMATION**.
- Query API: `BattleState::preview_rotation(unit, target)` returns
  `rules::RotationPreview { unit, target, unit_to, target_to, ap_cost, fatigue_cost, edge_penalties }`
  or the exact `RuleError` that `Command::Rotation` would return, in the same precedence. Its edge list
  predicts only current-state participants whose arrival would change morale to Breaking, paired with
  their arrival hexes and sorted by unit id; already-Breaking participants are omitted. The query and
  `BattleState::legal_rotation_targets(unit)` mutate no state, emit no events and consume no RNG. Legal
  targets are accepted preview targets in ascending id order, including finished, Wait-deferred and
  EndRound-marked allies.
- A preview describes the current state snapshot, not a reservation. A later Rotation command revalidates
  against the state at submission. The validated plan is shared by preview and execution for actor checks,
  target restrictions (including stun), passability, costs, swap destinations and edge effects; Rotation remains
  deterministic and provokes no reactions.
- Success: the two positions are exchanged atomically, `Event::Rotated { unit, target }` is emitted first.
  `walk` is not called and no reactions are resolved, even when either departure hex has several enemy
  controllers. Queue order, round, `turn_started`, `waited`, `skip_turn`, retreat intent, Adrenaline
  flags and base stats are preserved; neither participant is extracted from the queue and the turn does not
  advance when the actor reaches 0 AP.
- Derived queries (occupancy, `legal_targets`, `enemy_controllers_at`, surround and elevation hit
  modifiers) read the new positions immediately.
- Edge effect (**APPROXIMATION**: UC-COMBAT-048 is stated for trying to leave the map; extending it to
  Rotation is a slice choice): after the swap each participant, in ascending unit-id order, takes the
  existing map-edge penalty at its arrival hex under the existing eligibility predicate (present, not
  retreating, Confident/Steady/Wavering drop directly to Breaking). `EdgeMoraleChanged` follows `Rotated`
  and is emitted only for an actual change; nobody escapes (`Fled` is never emitted) and no RNG is used.
- State hash: `has_rotation` is unit flag bit 11; false preserves existing hashes and all other encoding is
  unchanged.
- Native: the event logger prints `<unit> swaps with <unit>` (ASCII, within the log width).
- Opt-in loadout (**APPROXIMATION**, a launch option, not a perk or skill system): `--rotation` (both
  binaries; combinable with `--adrenaline`, `--terrain` and `--seed`) builds the battle with
  `scenario::launch_battle_with(seed, terrain, adrenaline, rotation)`; `scenario::rotation_battle` is
  `scenario::battle` plus `has_rotation = true` on the three **player** units only. Nothing else changes: no
  AP, fatigue, stat or RNG difference (budgets and the RNG stream equal the default scenario's), only the
  hashed capability bit differs. `scenario::launch_battle(seed, terrain, adrenaline)` and
  `scenario::adrenaline_battle` are unchanged, and without the flag every scenario and loadout keeps its
  hash. `LaunchArgs` gained `rotation`. `R` and `N` rebuild the selected terrain **and** loadout. The AI never
  issues `Command::Rotation`, so `headless_smoke --rotation` without `--adrenaline` changes only the final
  hash. Combinations with `--adrenaline` also use the T32 Adrenaline policy.
- Native activation (**APPROXIMATION** of UC-COMBAT-039 and the UC-UI-003/008 skill bar: availability,
  shortcut `2`, the targeting workflow and every wording, position and colour are slice choices, not
  original-UI parity): a **ROTATION (2)** button sits right of Adrenaline in the top-right strip
  (`layout::ROTATION_BUTTON`, x 834-978, y 3-43 since the T46 four-button reflow; the drawn and hit rectangles are shared) and shows
  `COST 3 AP 25 FAT` and `SWAP ADJACENT ALLY` (shortened in T46 to fit the narrower button) from the rules constants. Button and `2` are enabled only
  for the present, controllable (not Fleeing, not retreating) active player unit of a running battle for
  which `legal_rotation_targets` is nonempty; stunned allies do not count, so if all adjacent allies are
  stunned the button and shortcut remain disabled. A disabled button or shortcut is a silent no-op. Activation
  captures the actor, round and `state_hash`, enters targeting and submits **no** command. The client keeps
  no eligibility or edge-penalty arithmetic: `legal_rotation_targets` and `preview_rotation` are the authority.
- Targeting mode: only the legal, unstunned allies are marked (teal hex plus a scale-1 `SWAP` label on the lower half of the ally's own token, clear of neighbouring tokens, names and bars; the hovered one brighter),
  the button turns green, and the ordinary movement highlights, AP costs, movement preview and attack `HIT`
  labels are hidden so exactly one action is displayed. `H` and `Z` still toggle and their overlays stay.
  The existing preview box lists at most four rows: the instructions (`ROTATION: P1 PICKS A MARKED ALLY ...`,
  cost, hover hint) until a legal ally is hovered, then that target's `RotationPreview`: the actor-only cost
  (`SWAP P1 WITH P2  COST AP 3 FAT 25 (P1 ONLY)`), both destination coordinates, `NO OPPORTUNITY ATTACKS`, the
  `ESC OR 2 CANCELS` hint and one `! P1 ARRIVES AT EDGE (x,y): MORALE BECOMES BREAKING` warning per
  predicted edge penalty (zero, one or two, each naming its participant). Hovering a stunned ally keeps the
  instruction rows and shows no swap highlight or preview.
- Selection: a map click on a legal ally revalidates the captured actor, round, hash and the current
  preview, clears targeting first, then submits exactly one `Command::Rotation` through `App::run`. Clicking
  a stunned ally is an invalid-target click: it submits nothing, keeps targeting open and shows the existing
  `NOT A SWAP TARGET: CLICK A MARKED ALLY OR PRESS ESC` feedback. Other invalid clicks behave the same; no
  click falls through to Move or
  Attack. A stale intent (actor, round, hash or eligibility no longer matching) is discarded with
  `ROTATION DISCARDED: BATTLE CHANGED` and no command; only a currently valid intent is rendered.
- Cancel and interplay: `Esc`, a second `2` or a second button click cancel targeting without quitting
  (`Esc` outside targeting and confirmations still quits). `H`, `Z` and keys that are no action keep
  targeting; `E`, `W`, `X`, `V`, `1`, `R`, `N` and the End Round, Retreat and Adrenaline buttons cancel it
  before their normal handling and arm the same click guard (so the follow-up click cannot Move; `R`/`N` arm it after rebuilding the battle, and only when targeting was open); opening a
  confirmation clears it and the modals consume `2`, the button and `Esc` as before. Restart, new seed and every submitted command (including automatic turns) clear it, and
  a stale intent never blocks the automatic driver. Completing or cancelling targeting arms the existing
  350 ms click guard so a double click cannot reach the map. Repeated key events stay ignored.
  Rendering, targeting and cancelling consume no RNG and do not mutate the battle. Since the Footwork task
  the intent is one action-tagged `PendingTargeting { action, actor, round, state_hash }` shared with
  Footwork (at most one mode, exclusive with the confirmation dialogs); see "Footwork" for switching.
- Deferred: rooted Rotation targets, stunned/rooted Footwork targets, skill learning (Footwork is a separate
  skill with its own native targeting: see "Footwork"), equipment and AI use. UC-COMBAT-039 states the
  Rotation restriction against stunned/rooted allies; the stunned-target restriction is implemented in T70,
  while the original runtime remains unconfirmed. No other statuses are modelled; the headless stun effect
  is documented separately (see "Stun effect"). The native targeting view was not inspected visually: the environment has
  no display, so layout is checked by geometry, glyph and scene tests only.

Tests: [`crates/bb_rules/tests/rotation.rs`](crates/bb_rules/tests/rotation.rs) (35 tests: all existing
Rotation behavior plus preview/apply error agreement, exact costs and destinations, zero/one/two predicted
edge penalties and event order, already-Breaking units, sorted legal targets, scheduling-variant allies,
live query refresh and complete query purity, stunned-target rejection for both sides, invalid-target
precedence, mixed eligible allies and re-eligibility after scheduled stun expiration) and
`rotation_is_logged_naming_both_participants` in `bb_game`; flag parsing, the terrain/loadout combinations
and smoke in [`crates/bb_rules/tests/terrain_presets.rs`](crates/bb_rules/tests/terrain_presets.rs); the
`bb_game` App tests (activation, disabled states including all adjacent allies stunned, selection equal to a
direct command, stunned-target invalid click and stale-intent discard, other invalid clicks, cancel, modals,
click guard, R/N persistence, automatic turns) and the renderer, layout and glyph tests
for the two-button strip, markers and every targeting row. Seed-1 default and Rotation-only smoke results
remain unchanged (Flat `dfce9b8d74bcaf93` / `eca98bcb3e525dfb`; Hills `d0b704f0294173be` /
`17dd25afecfd72b6`). With `--rotation --adrenaline`, current T32 outputs are Flat
`OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=6b94625d3560a3b3`, Hills
`OUTCOME=Victory REASON=Elimination ROUNDS=7 HASH=d91435785603942b`. Historical pre-T32 combination hashes
were Flat `c3321d175f709c65` and Hills `a5e6fc215f86523c` before AI Adrenaline selection; they are retained
as prior validation evidence and superseded by the current outputs.

T70 validation: the new rules regression first failed because stunned adjacent allies still produced a valid
preview and a later coordinate check won in a second fixture. After adding the stunned-target filter to the
shared Rotation plan, all 35 `rotation.rs` tests passed. Focused App and renderer tests passed for disabled
activation when all allies are stunned, mixed-target markers/previews, invalid-click feedback and stale-intent
discard. `cargo test --workspace --locked --offline` passed (13.6 s), `cargo build --workspace --locked
--offline` succeeded (0.08 s), and `git diff --check` passed.

## Footwork (opt-in loadout and native destination targeting, partial UC-COMBAT-039 coverage)

`Command::Footwork { unit, to }` lets a present, active, controllable unit of either side with the
capability step to one adjacent hex without opportunity attacks. Catalog authority (catalog-stated, not
runtime-confirmed): [UC-COMBAT-039](docs/source/10-tactical.md) (Footwork: step out of ZoC without
opportunity attacks; shared precondition "3 AP + fatigue cost") and UC-COMBAT-008 (leaving ZoC normally
provokes; Footwork bypasses it). The rules are headless-first (`bb_rules`); the opt-in `--footwork` loadout
and the native destination-targeting mode below make them playable. There is **no AI casting**: Footwork is
issued by the human player or by scripted commands and tests.

- Capability: `Unit.has_footwork` (false in `Unit::new`, so every existing scenario and loadout is
  unchanged). **APPROXIMATION:** an explicit flag instead of a learned perk.
- Cost: `FOOTWORK_AP` = 3 (follows the catalog's shared precondition) and `FOOTWORK_FATIGUE` = 20
  (**APPROXIMATION**: the catalog gives no number; 20 is a slice choice, not a recovered value). Both are
  fixed skill costs: no ordinary movement cost and no uphill surcharge, whatever the heights.
- One-hex limit (**APPROXIMATION**: the catalog says "step"; the range is a slice choice): the destination
  must be a neighbour of the actor. Height is ignored for legality and cost.
- Validation order (everything is checked before any mutation; rejections change nothing and consume no
  RNG): `BattleOver`, `NotActiveUnit` (also a non-present actor), `RetreatingUnit`/`FleeingUnit`, then
  `MissingFootwork`; `Unreachable` unless actor and destination are in the map and not obstacles (checked
  with the overflow-safe passability helper before any distance arithmetic, so extreme `i32` coordinates
  are safe), the destination is not the actor's own hex and no present unit stands on it; `NotAdjacent` for
  a distinct, free, passable destination farther than one hex; `NotEnoughAp` (needs 3) before `TooFatigued`
  (checked add of 20 within `fatigue_max`, so `u32::MAX` overflow is rejected).
- Query API: `BattleState::preview_footwork(unit, to)` returns
  `rules::FootworkPreview { unit, from, to, ap_cost, fatigue_cost, edge_penalty: Option<Hex> }` or the exact
  `RuleError` that `Command::Footwork` would return; both use one validated plan.
  `BattleState::legal_footwork_destinations(unit)` returns the accepted destinations in the existing
  neighbour order and an empty list for any ineligible actor (including invalid actor coordinates, without
  panic). Both are recomputed on every call, mutate nothing, emit no events and consume no RNG. A preview
  is a snapshot, not a reservation; the command revalidates.
- Success: -3 AP, +20 fatigue, the actor moves, `Event::FootworkUsed { unit, from, to }` is emitted. `walk`
  is not called and no opportunity attacks are resolved, whatever controllers stand at the departure or
  arrival hex. Queue position, round, `turn_started`, `waited`, `skip_turn`, retreat intent, Adrenaline
  flags and base stats are preserved; the turn does not advance at 0 AP and repeated use is allowed while
  ordinary eligibility and budgets permit. Occupancy, `legal_targets`, `enemy_controllers_at`, surround and
  elevation hit modifiers read the new position immediately.
- Edge effect (**APPROXIMATION**: UC-COMBAT-048 is stated for trying to leave the map; extending it to
  Footwork is a slice choice): the existing edge predicate is evaluated at arrival (present, not retreating,
  Confident/Steady/Wavering drop directly to Breaking). `EdgeMoraleChanged` follows `FootworkUsed` only when
  morale changes; `edge_penalty` previews exactly that destination. Reaching an edge does not extract the
  actor (`Fled` is never emitted) and no RNG is used.
- No passive immunity: having the capability does not stop reactions during `Move`, `Flee` or `Withdraw`.
- State hash: `has_footwork` is unit flag bit 12; false preserves existing hashes.
- Native: the event logger prints `<unit> footwork` (ASCII, within the log width).
- Opt-in loadout (**APPROXIMATION**, a launch option, not a perk or skill system): `--footwork` (both
  binaries; combinable with `--adrenaline`, `--rotation`, `--terrain` and `--seed`) sets `LaunchArgs.footwork`.
  Both binaries build the battle with the shared `scenario::launch_battle_for(LaunchArgs)`, which sets each
  selected capability on the three **player** units only (`scenario::footwork_battle` is `battle` plus
  `has_footwork`). Starting budgets, pending effects, placement and the RNG stream are unchanged; only the
  hashed capability bit differs, and without the flag every accepted scenario and loadout keeps its hash.
  `launch_battle_with`, `launch_battle`, `adrenaline_battle` and `rotation_battle` stay valid. `R`/`N` rebuild
  the selected terrain **and** loadout. The AI never issues `Command::Footwork`, so `headless_smoke --footwork`
  without `--adrenaline` changes only the final hash. Combinations with `--adrenaline` also use the T32
  Adrenaline policy.
- Native activation (**APPROXIMATION** of UC-COMBAT-039 and the UC-UI-003/008 skill bar: availability,
  shortcut `3`, the targeting workflow and every wording, position and colour are slice choices, not
  original-UI parity): the skill strip held three equal buttons with visible gaps, each 186x40 at y 3
  (`ADRENALINE_BUTTON` x 708, `ROTATION_BUTTON` x 900, `FOOTWORK_BUTTON` x 1092; *historical: the T46 four-button
  reflow replaced these coordinates, see "Indomitable"*). The strip extends left of
  the side panel into the free band right of the top-bar text (which ends near x 674 for the longest seed);
  only this strip was reflowed and all three skills keep their label, cost row and effect row (*superseded by the
  T46 four-button reflow: 144x40 buttons at x 684/834/984/1134, see "Indomitable"*). The
  **FOOTWORK (3)** button shows `COST 3 AP 20 FAT` and `STEP TO ADJACENT HEX` from the rules constants.
  Button and `3` are enabled only for the present, controllable (not Fleeing, not retreating) active player
  unit of a running battle for which `legal_footwork_destinations` is nonempty; a disabled activation is a
  silent no-op. Activation captures the actor, round and `state_hash`, enters targeting and submits **no**
  command. The client keeps no eligibility, cost or edge arithmetic: `legal_footwork_destinations` and
  `preview_footwork` are the authority.
- Targeting mode: only legal destination hexes are marked (violet fill with a scale-1 `STEP` label in the
  empty hex's middle; the hovered one brighter, distinct from the Rotation teal), the button turns green, and
  ordinary movement highlights, AP labels, movement preview, attack `HIT` labels and Rotation markers are
  hidden so exactly one action is displayed. `H` and `Z` still toggle and their overlays stay. The existing
  preview box lists at most four rows: instructions (`FOOTWORK: P1 PICKS A MARKED HEX TO STEP TO. ESC OR 3
  CANCELS.`, cost, hover hint) until a legal hex is hovered, then that hex's `FootworkPreview`: the
  actor-only cost (`STEP P1  COST AP 3 FAT 20 (P1 ONLY)`), departure and destination coordinates,
  `NO OPPORTUNITY ATTACKS`, the `ESC OR 3 CANCELS` hint and, when the rules predict it, one
  `! P1 ARRIVES AT EDGE (x,y): MORALE BECOMES BREAKING` warning. Nothing implies extraction.
- Selection: a map click on a legal destination revalidates the captured actor, round, hash and the current
  preview, clears targeting first, then submits exactly one `Command::Footwork` through `App::run`. A click
  elsewhere (an occupied hex, a non-adjacent hex, the actor, the panel) submits nothing, keeps targeting open
  and shows `NOT A STEP HEX: CLICK A MARKED HEX OR PRESS ESC`; it never falls through to Move, Attack or
  Rotation. A stale intent is discarded with `FOOTWORK DISCARDED: BATTLE CHANGED` and no command; only a
  currently valid intent is rendered and a stale one never blocks automatic turns.
- Shared targeting and switching: Rotation and Footwork share one action-tagged intent, so at most one mode
  is open, exclusive with confirmation dialogs. A second activation of the open skill cancels; activating the
  other targeted skill (`2`/`3` or its button) switches directly with a fresh intent when that skill is
  available, without closing targeting or arming a click guard; an unavailable other skill leaves the current
  mode unchanged. `Esc` cancels without quitting; `H`/`Z` preserve targeting; `E`, `W`, `X`, `V`, `1`, `R`,
  `N` and the End Round, Retreat and Adrenaline buttons cancel it before their normal handling; opening a
  confirmation clears it and modals consume every targeting input. Completing or cancelling either mode, and
  `R`/`N` after rebuilding the battle (when targeting was open), arm the existing 350 ms click guard; repeated
  key events stay ignored. Rendering, targeting, switching and cancelling consume no RNG and do not mutate
  the battle.
- Deferred (unchanged apart from the T61 headless stun effect, see "Stun effect"): other status effects (rooted and similar), Spearwall interaction (UNCONFIRMED in
  the catalog), skill learning, equipment and AI use. This is partial UC-COMBAT-039 coverage and claims no
  original-runtime parity or complete skill coverage. The native targeting view was not inspected visually:
  the environment has no display (`DISPLAY` and `WAYLAND_DISPLAY` empty, no Xvfb), so layout is checked by
  geometry, glyph and scene tests only.

Tests: [`crates/bb_rules/tests/footwork.rs`](crates/bb_rules/tests/footwork.rs) (27 tests: both sides, exact
costs and relocation, event ordering, preserved queue/flags/stats/RNG, capability and actor precedence,
unchanged/occupied/blocked/off-map/extreme-coordinate destinations, nonadjacency, budget precedence, exact
caps and overflow, preview/apply agreement, ordered and refreshed destinations, query purity, multi-controller
bypass with no attacks or RNG, ordinary Move/Flee/Withdraw still provoking, equal/uphill/downhill fixed costs
and refreshed spatial queries, edge arrival from each eligible morale and already-Breaking, no extraction,
repeated use and zero AP, pending Adrenaline, independent hash bit and restoration),
`footwork_is_logged_concisely_and_edge_arrival_follows` in `bb_game`, the `--footwork` parsing, all
terrain/skill-flag combinations (player-only capabilities, unchanged budgets/RNG, existing-loadout equivalence)
and smoke in [`terrain_presets.rs`](crates/bb_rules/tests/terrain_presets.rs), the `bb_game` App tests
(activation, disabled states, selection equal to a direct command including edge arrival and enemy control,
invalid clicks, exactly-once dispatch, Rotation/Footwork switching and unavailable-mode activation, cancel,
modals, H/Z, immediate actions, click guard and R/N with injected instants, stale actor/round/hash/eligibility,
automatic turns) and renderer/layout tests (three-button strip bounds and glyphs, STEP markers, hover previews,
edge warnings, suppression, purity). Seed-1 `headless_smoke` outputs without the flag are unchanged (Flat
`dfce9b8d74bcaf93`, Hills `d0b704f0294173be`; Footwork-only remains Flat
`OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=26f421838e407143`, Hills
`OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=9f83d1d40f1a2f6e`. With `--adrenaline --footwork`, current T32
outputs are Flat `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=dcd3fff2deaf550b`, Hills
`OUTCOME=Victory REASON=Elimination ROUNDS=7 HASH=4cea2c6e5d5bd5d3`. With all three skill flags, current T32
outputs are Flat `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=fdc85eae43dd0ba3`, Hills
`OUTCOME=Victory REASON=Elimination ROUNDS=7 HASH=93baae9abf5c755b`. Historical pre-T32 all-skill outputs
were Flat `b471278bb8f72c95` and Hills `be7e9bdca604752c`; they are retained as prior validation evidence
and superseded by the current results.

## Fast Adaptation (headless rules, `--fast-adaptation` launch option and native stack indicator; partial UC-COMBAT-067 coverage)

Catalog statements ([UC-COMBAT-067](docs/source/10-tactical.md), public catalog copy, not runtime-confirmed):
each miss against an opponent gives +10% ("wiki; older guides 8%") on the next attack, reset on a hit;
three consecutive misses show a growing hit % on the fourth swing.

`Unit.has_fast_adaptation` (default `false`) and `Unit.fast_adaptation_stacks: u32` (default `0`) add this
headlessly; `FAST_ADAPTATION_BONUS_PER_STACK = 10`. For a present, hostile, adjacent melee pair a capable
attacker's `hit_chance` adds `10 * stacks` to the same widened sum as morale, surround and elevation, before
the single 5–95 clamp. `resolve_attack` (ordinary and opportunity attacks) uses the pre-attack counter for
the chance it rolls and records in `Event::Attacked.chance`; afterwards a capable attacker gains one stack on
a miss (saturating) or resets to zero on any hit, armour-only and lethal included. No RNG, no new events, and
costs, validation, event order and morale are unchanged. Stacks belong to the attacker and persist across
target changes, movement, Wait, turns and rounds until a hit; an incapable unit ignores and never changes its
counter. `state_hash` uses flag bit 13 (capability) and bits 14..=45 (counter); default hashes are unchanged.

**APPROXIMATION** (no original-runtime confirmation): additive percentage points on the final chance, the
10 (not 8) variant, the per-attacker target-independent counter, no expiry, and the same rule for
opportunity attacks and both factions.

**Launch option and indicator (T35, APPROXIMATION).** `--fast-adaptation` (shared parser, value-less, repeats
harmless; `LaunchArgs.fast_adaptation`, default `false`) is composed independently of seed, terrain and the
other three capability flags. `launch_battle_for` sets `has_fast_adaptation` on **player units only**, with
zero initial stacks; construction consumes no RNG and changes only the selected flags, so every run without
the option keeps its accepted hash. The native client stores the option; **R** (restart) and **N** (new seed)
rebuild with it, reset accumulated stacks to zero and keep the terrain, the other capability options and the
**H**/**Z** overlay preferences. For every present capable unit (either faction; zero included) the side panel
draws a scale-1 `ADAPT <stacks>` marker, right aligned on the HP/armour line and separate from the pending
Adrenaline marker on the AP/fatigue line; dead and escaped rows and incapable units have none. The longest
label, `ADAPT 4294967295`, is 95 px wide and starts at x 1177, clear of the stock HP/armour text, the morale
label and the next row. The hover `HIT n%` preview is still `BattleState::hit_chance`, so a real miss adds
one stack (saturating), which contributes 10 percentage points before the existing 5–95 clamp, and a hit resets
the stacks and their bonus. The `ADAPT` counter therefore rises by 1, while the displayed `HIT` chance may rise
by less than 10 where clamping applies. Rendering is read-only; the player-only
assignment, the launch option and the marker's presentation are APPROXIMATIONS (no catalog or runtime source).

Fast Adaptation is a **passive, not an active skill**: there is no command, button, shortcut, targeting mode
or AI selection policy for it. In headless runs the ordinary AI attacks of capable player units simply receive
the accepted effect automatically (the AI itself is unchanged and enemies stay incapable).

Seed-1 headless smoke replays (actual, this tree; deterministic across two runs and between the `headless_smoke`
binary and an in-process command/event replay, all commands valid, terminate far below the 10 000-command bound):

| options | Flat | Hills |
| --- | --- | --- |
| none (unchanged) | `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=dfce9b8d74bcaf93` | `OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=d0b704f0294173be` |
| `--fast-adaptation` | `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=03cd5060271badf3` | `OUTCOME=Victory REASON=Elimination ROUNDS=5 HASH=9ad76a636259c440` |
| `--adrenaline --rotation --footwork` (unchanged) | `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=fdc85eae43dd0ba3` | `OUTCOME=Victory REASON=Elimination ROUNDS=7 HASH=93baae9abf5c755b` |
| all four options | `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=f119e88eac2122c3` | `OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=7a501923872d8823` |

The replays include actual player misses that raise counters (asserted in `terrain_presets.rs`). Earlier
statements that no scenario or native client used Fast Adaptation are historical (pre-T35 validation evidence).
No native window was opened for visual inspection of the marker in this environment; the evidence is the
headless scene geometry tests, not a screenshot.

Tests: [`crates/bb_rules/tests/fast_adaptation.rs`](crates/bb_rules/tests/fast_adaptation.rs) (20 tests; see
`docs/forge/implemented/slice-state.md` for the coverage list). Validation:
`cargo test -p bb_rules --test fast_adaptation --locked --offline` and
`cargo test --workspace --locked --offline` pass.
Deferred: scenario-authored (non-launch-option) assignments, AI selection of the passive (none is needed),
perk learning or a generic perk framework, ranged attacks, scatter and blocking-tile hits.


## Dodge (headless melee defense, partial UC-COMBAT-058 coverage)

**Catalog statement** ([UC-COMBAT-058](docs/source/10-tactical.md), public catalog copy, not runtime-confirmed):
"+15% of current initiative to melee and ranged defence"; heavy armour or fatigue that tanks initiative also
tanks Dodge; the defence tooltip should show a Dodge line that shrinks as fatigue rises.

`Unit.has_dodge` (default `false`) and `unit::DODGE_DEFENSE_PERCENT = 15` add this headlessly:

- `Unit::current_initiative() -> i64 = i64(base initiative) - i64(fatigue / 2)` (integer division first;
  signed, never clamped). `begin_round` uses it as the base of its ordering key, then applies the existing
  Fleeing penalty (-1000) and pending-Adrenaline bonus (+2000) separately, so ordering, ID tie-breaks and
  signed arithmetic are unchanged.
- `Unit::dodge_defense_bonus() -> u32`: `0` without the capability, otherwise
  `floor(max(current_initiative, 0) * 15 / 100)` in widened arithmetic. The queue-only Fleeing and Adrenaline
  adjustments never enter it.
- `effective_defense = floor(base defense * morale % / 100) + dodge_defense_bonus`, saturating at `u32::MAX`.
  The added bonus is not scaled by morale and base `Stats` are never modified; `effective_melee_skill` is
  unchanged. The morale percentage therefore describes morale scaling only, not every source of final defense.
- Ordinary attacks, opportunity attacks and `hit_chance` queries (so the native hit preview) all go through
  `effective_defense`, and it is computed from live fatigue on every query: movement, attacks, incoming hit/miss
  fatigue and turn-start regeneration change it immediately, with no round-start cache. `Event::Attacked.chance`
  equals the preceding query. Dodge adds no command, event, RNG draw or resource cost.
- `state_hash` flag bit 46 holds `has_dodge` (Fast Adaptation's counter keeps bits 14..=45); `false` leaves all
  hashes unchanged. The scenario grants the capability to nobody; since T37 only the `--dodge` launch option does
  (see below).

**Provenance.** Catalog-stated: the 15%, the dependence on *current* initiative and that fatigue lowers it.
**APPROXIMATION** (no original-runtime confirmation): the fatigue-to-initiative formula `initiative - fatigue/2`
(the slice's existing queue formula), flooring, the zero floor for negative initiative, adding after morale
scaling, excluding the sorting-only Fleeing/Adrenaline adjustments, applying the bonus to both factions and to
opportunity attacks, and the plain capability flag (not a perk system).

**Launch option and defense breakdown (T37, APPROXIMATION).** `--dodge` (shared parser, value-less, repeats
harmless; `LaunchArgs.dodge`, default `false`) composes independently of seed, terrain and the four other
capability flags (all 32 subsets are tested). `launch_battle_for` sets `has_dodge` on **player units only**;
construction consumes no RNG and changes only the selected flags, so every run without the option keeps its
accepted hash. The native client stores the option; **R** (restart) and **N** (new seed) rebuild with it and
keep the terrain, the other capability options and the **H**/**Z** overlay preferences, while battle
resources (AP, fatigue) and Fast Adaptation stacks restart fresh.

For every present Dodge-capable unit (either faction; Fleeing and retreating units included; dead and escaped
rows and incapable units have none) the side panel draws one scale-1 line, `DODGE +<bonus> DEF <total>`, at
`PANEL_X` and row top + 51 px, inside the 60 px unit row. `<bonus>` is `Unit::dodge_defense_bonus()` (zero
drawn as `+0`) and `<total>` is `Unit::effective_defense()`, i.e. the *total effective melee defense*, not a
percentage and not the Dodge contribution alone. The numbers are read from the rules every frame, so they
change immediately after fatigue-producing actions (move, attack, incoming hit/miss) and at turn-start
regeneration. The longest line, `DODGE +644245094 DEF 4294967295`, is 185 px wide; it sits below the
AP/fatigue text and the pending-Adrenaline box (which ends at row top + 51), below the Fast Adaptation
`ADAPT` marker, above the next row and above the queue heading, and uses only supported glyphs. The hover
`HIT n%` preview is still `BattleState::hit_chance`; against a Dodge-capable defender it equals the
attacker's skill minus the displayed `DEF` (before surround, elevation and the 5–95 clamp), and the resolved
`Event::Attacked.chance` equals it. Rendering is read-only. The player-only assignment, the launch option and
the panel line's presentation are APPROXIMATIONS (no catalog or runtime source; the catalog only says the
defence tooltip shows a Dodge line).

Dodge is a **passive, not an active skill**: no command, button, shortcut, targeting mode or AI selection
policy exists. In headless runs every attack against a capable (player) defender automatically uses the
accepted Dodge defense; the AI itself is unchanged and enemies stay incapable.

Seed-1 headless smoke replays (actual, this tree; deterministic across two runs and between the `headless_smoke`
binary and an in-process command/event replay, all commands valid, terminate far below the 10 000-command bound):

| options | Flat | Hills |
| --- | --- | --- |
| none (unchanged) | `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=dfce9b8d74bcaf93` | `OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=d0b704f0294173be` |
| `--dodge` | `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=e55edadd8f630253` | `OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=dce3433c8ab331fe` |
| all four earlier options (unchanged) | `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=f119e88eac2122c3` | `OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=7a501923872d8823` |
| all five options | `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=57f7c67846b45003` | `OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=ac49aa482cb73309` |

The replays include attacks against defenders with a nonzero Dodge contribution (asserted in
`terrain_presets.rs`), and every recorded attack chance equals the pre-command `hit_chance`. Earlier statements
that no scenario, launch option or native client grants or shows Dodge are historical (pre-T37 validation
evidence). No native window was opened for visual inspection of the line in this environment; the evidence is
the headless scene geometry tests, not a screenshot.

**Deferred explicitly:** perk learning or a generic perk framework, scenario-authored (non-launch-option)
assignments, equipment encumbrance, Relentless, and ranged defense (the slice has no ranged attacks).
Partial coverage only; no parity claim.

Tests: [`crates/bb_rules/tests/dodge.rs`](crates/bb_rules/tests/dodge.rs) (31 tests); T37 adds launch/parser,
replay and smoke tests to `crates/bb_rules/tests/terrain_presets.rs`, App tests in `crates/bb_game/src/main.rs`
and scene tests in `crates/bb_game/src/render.rs`. Validation (actual, this tree):
`cargo test -p bb_rules --test dodge --locked --offline` (31 passed),
`cargo test --workspace --locked --offline` (all passed) and `git diff --check` (clean).


## Nine Lives (headless melee survival, partial UC-COMBAT-059 coverage)

**Catalog statement** ([UC-COMBAT-059](docs/source/10-tactical.md), public catalog copy, not runtime-confirmed):
a unit that would die and has Nine Lives unused this fight survives at low HP, the perk is on cooldown until
the next battle, it may still apply an injury, and a second lethal hit in the same fight kills. The observable
result is a would-be-fatal blow leaving the brother at "a sliver of HP" with the perk greyed.

`Unit.has_nine_lives` and `Unit.nine_lives_used` (both default `false`), `rules::NINE_LIVES_SURVIVAL_HP = 1`,
and `Event::NineLivesTriggered { unit }` add this headlessly:

- **Interception.** In the shared `resolve_attack` (ordinary and opportunity attacks, either faction, Fleeing
  and retreating defenders included), after armour absorption and the would-be HP loss are known, a hit on a
  present, positive-HP defender with `has_nine_lives && !nine_lives_used` whose would-be loss reaches zero HP
  leaves the defender alive at 1 HP and sets `nine_lives_used`. A unit already at 1 HP may consume unused
  protection and stay at 1 HP. Misses, nonlethal hits and armour-only hits never consume it. The used flag is
  cleared only by constructing a fresh unit; `Wait`, turns, rounds, morale changes and retreat leave it alone.
- **Reporting and order.** `Event::Attacked` keeps the rolled `dmg` (the Indomitable-reduced value when that stance is effective, see "Indomitable") and the armour `absorbed`, but its
  `hp_loss` is the *actual* HP lost (previous HP minus 1); every other attack keeps the existing semantics
  (the unclamped would-be loss). `NineLivesTriggered { unit }` follows `Attacked` immediately. No `Died`,
  ally-death check, kill-morale check, queue removal or elimination follows. If the actual HP loss reaches
  the existing heavy-hit threshold (15), the usual `MoraleChecked` comes after `NineLivesTriggered`.
- **Everything else is unchanged.** No RNG draw is added, and AP, fatigue and armour are not refreshed.
  The survived hit is still a hit: the defender gains the +5 hit fatigue, an attacker's Fast Adaptation
  stacks reset, and an opportunity attack that was protected against still interrupts the mover's route.
  The remaining controllers then react in their accepted order while the mover lives; a later lethal hit,
  even in the same movement command, follows normal death handling. A last unit's survival delays
  `Victory`/`Defeat` until its actual death.
- `state_hash` flag bits 47 (`has_nine_lives`) and 48 (`nine_lives_used`); all earlier assignments are kept
  and `false`/`false` leaves prior hashes unchanged.
- Native log: `P1 nine lives: survives` (fits the 31-character limit). No activation control was added; the
  launch option and indicator below (T39) came later, so the pre-T39 statements that no scenario, launch
  flag or indicator exists are historical.

**Provenance, kept separate.** *Catalog-stated:* survival of a would-be-fatal blow at low HP, once per
battle, and death on a second lethal hit. **APPROXIMATION** (no original-runtime confirmation): the exact
survival value of 1 HP (the catalog gives none), interception for melee hits only, reporting and basing the
heavy-hit morale check on the actual HP loss, the plain capability flag (not a perk system), applying it to
both factions, and the log wording.

**Validation (actual, this tree):** [`crates/bb_rules/tests/nine_lives.rs`](crates/bb_rules/tests/nine_lives.rs)
(18 tests) covers both factions, exact-lethal and overkill damage, survival from 1 HP, disabled and used
protection, nonlethal hits, misses, armour-only and armour-absorbed lethal hits, first-hit survival followed by
death (including two opportunity attackers in one move and a survived reaction followed by a miss), queue
retention, movement interruption, incoming fatigue, Fast Adaptation reset, Fleeing and retreating defenders,
no death-triggered events on survival, the 14-versus-15 actual-loss morale boundary with event order and RNG
use, last-unit outcome delay, no replenishment across `Wait` and a round boundary, persistence through retreat
and morale changes, query and rejected-command purity, and hash sensitivity with the existing pins; an App
test in `crates/bb_game/src/main.rs` covers the log line. `cargo test -p bb_rules --test nine_lives --locked
--offline`, `cargo test --workspace --locked --offline` and `git diff --check` pass.

**Launch option and indicator (T39, APPROXIMATION).** `--nine-lives` (shared parser, value-less, repeats
harmless; `LaunchArgs.nine_lives`, default `false`) composes independently of seed, terrain and the five other
capability flags (all 64 subsets are tested). `launch_battle_for` sets `has_nine_lives` on **player units
only**, with `nine_lives_used = false`; enemies stay incapable and the compatibility constructors
(`launch_battle_with`, `adrenaline_battle`, `rotation_battle`, `footwork_battle`, `launch_battle`) supply
`false`. Construction consumes no RNG and changes only the selected capability flags; without the option every
earlier configuration keeps its event stream and pinned hashes. The native `App` stores the choice; **R** and
**N** rebuild with it, so a consumed protection is unused again (fresh resources, zero Fast Adaptation stacks)
while terrain, the other capability selections and the **H**/**Z** preferences are kept.

For every present capable unit (either faction; Fleeing and retreating units included; dead and escaped rows
and incapable units have none) the side panel draws one scale-1 line, right aligned 8 px inside the window at
row top + 51 px (the line of the Dodge breakdown): `NINE LIVES READY` in a rose colour while
`nine_lives_used` is false, `NINE LIVES USED` in dim gray once true. Capability and usage are read from the live
`Unit` every frame, so a real protected hit (see the existing `P1 nine lives: survives` log line) turns READY
into USED and a later lethal hit removes the indicator together with the dead row. The longest label is 95 px
wide (x 1177..1272); the longest Dodge breakdown ends at x 1085, so the two never overlap, and the indicator
clears the resource text, the pending-Adrenaline box, the Fast Adaptation indicator, the next row and the
queue heading for every stock row with maximum Dodge values, maximum stacks and pending Adrenaline at once.
Only glyphs the bitmap font supports are used and rendering stays pure. Survival itself is entirely
`bb_rules`; the option adds no command, button, shortcut, targeting mode or AI change.

Deterministic seed-1 smoke for the six pre-T41 options (actual, this tree; `--nine-lives` alone and with all
six then available capability options, before `--berserk` was added):

| options | Flat | Hills |
| --- | --- | --- |
| none (unchanged) | `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=dfce9b8d74bcaf93` | `OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=d0b704f0294173be` |
| `--nine-lives` | `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=e7d351c8b0dc9913` | `OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=64286fc0a709803e` |
| all six then available | `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=cb45f871a3bb0b83` | `OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=6812901431975589` |

Hash differences from the earlier runs come from the hashed capability bit; the replays' command and event
streams are compared in `crates/bb_rules/tests/terrain_presets.rs` (identical across two runs, every command
valid, bounded termination). Stock seed-1 play need not (and the tests do not require it to) trigger
protection; the protection path is covered by a legal scripted encounter built from the launch configuration:
one protected hit (`NineLivesTriggered`, 1 HP) followed by normal death (`Died`) on the second lethal hit. Other
tests: `terrain_presets.rs` (parser defaults and repeats, all 64 subsets, player-only unused assignment, RNG and
non-capability state unchanged), App tests in `crates/bb_game/src/main.rs` (consumed protection restored by
R/N with other selections and H/Z preserved) and scene tests in `crates/bb_game/src/render.rs`
(READY/USED, both factions, excluded rows, Fleeing/retreating, survival then death, combined geometry, glyphs,
purity). Native visual inspection of a window was not performed. Commands run: `cargo test --workspace
--locked --offline` (all passed: 220 `bb_game` tests, 38 in `terrain_presets`), `cargo build --workspace
--locked --offline` (no warnings) and `git diff --check` (clean).

Deferred explicitly: enemy or scenario assignments of the capability, perk learning or a generic perk
framework, injuries, post-battle survival and temporary protection buffs, ranged attacks, status damage and
other damage sources, and a tooltip or greyed-button presentation closer to the catalog's description. Partial
coverage only; no parity claim.


## Berserk (passive melee AP refund and launch selection, partial UC-COMBAT-038 coverage)

**Catalog statement** ([UC-COMBAT-038](docs/source/10-tactical.md), public catalog copy, not runtime-confirmed):
a killer with the Berserk perk immediately regains 4 AP once per turn; fatigue is not refreshed; it can chain
if AP and fatigue allow. The observable result is a two-handed brother killing and attacking again in the same
turn.

`Unit.has_berserk` and `Unit.berserk_used` (both default `false`), `rules::BERSERK_AP_REFUND = 4` and
`Event::BerserkTriggered { unit, ap_gained }` implement the passive headlessly. The shared `--berserk`
launch option enables it for player units. Construction starts Berserk unused and changes only selected
capability flags; it consumes no RNG. The launch option and player-only assignment are **APPROXIMATION**s,
not a perk system.

- **Trigger.** After an ordinary `Command::Attack` has been fully resolved (attack costs paid first), if the
  target actually died and the attacker is present, `has_berserk` and not `berserk_used`, the attacker regains
  `min(4, ap_max - ap)` AP (saturating arithmetic, overflow-safe up to `u32::MAX` maxima) and `berserk_used`
  is set. The turn is neither advanced nor refreshed. In legal play (`ap <= ap_max`) paying 4 AP leaves room
  for exactly 4, so the cap only truncates in states outside that contract (pinned defensively by a test).
- **Not a trigger.** Opportunity attacks (they pay no costs and never reach the refund) neither refund nor
  consume the use, so an actor that killed with an opportunity attack still earns its ordinary-turn refund.
  Misses, nonlethal hits, armour-only hits and Nine Lives survival (not a kill) leave `berserk_used`
  unchanged, so a later real kill still refunds. A second ordinary kill in the same scheduled turn gives
  nothing more.
- **Lifecycle.** `berserk_used` is cleared only by `begin_turn`'s once-per-round fresh refresh (the existing
  `turn_started` guard): `Wait` returning the unit to control does not clear it, neither does another unit's
  action, `EndTurn` or `EndRound` by the actor. A skipped (`EndRound`) unit's pass-over is still a fresh
  refresh and clears it; the next round's refresh restores eligibility. No duplicate refresh occurs.
- **Event order.** `Attacked`, `Died`, the ally-death checks, `KillMoraleChecked`, then
  `BerserkTriggered { unit, ap_gained }` (the *actual* gain), then any `BattleEnded`. Relative order of all
  existing events is unchanged and nothing is inserted earlier.
- **Invariants.** No RNG draw is added; fatigue, HP, armour and morale are untouched by the refund. Without
  `--berserk`, existing event streams and pinned hashes are unchanged. With it, construction changes only the
  player units' selected capability bit and leaves `berserk_used` false.
- **Hash.** `has_berserk` is flag bit 49 and `berserk_used` bit 50 (earlier assignments, bits 0..=48, are
  unchanged); `false`/`false` leaves prior hashes unchanged and the `BattleState::new(1)` and Flat/Hills
  scenario pins hold.
- **AI.** `plan_turn` never reads `has_berserk`; the T55 Recover choice and T66 Stunning Blow choice do
  not change that. Progress explanation: a normal 9-AP turn with at most one Berserk refund has at most
  13 AP to spend and at most one Adrenaline activation (1 AP). T66's Stunning Blow costs 6 AP, above
  the existing 2-AP minimum for continuing commands, so the conservative bound remains at most seven
  commands that leave the turn active (1 + 12 / 2). The capability-free bound of five (9 AP) is
  retained. Repeated
  `plan_turn`/`apply` on legal capable-actor fixtures
  with and without Adrenaline was tested before T55 to end in explicit `EndTurn` or battle completion within
  that bound. T55 adds Recover as another command that ends the turn; the current bound counts either
  final command once (see "AI Recover policy").
- **Native client.** `App` stores the option, and `R`/`N` restore it unused with fresh resources and zero Fast
  Adaptation stacks while retaining terrain, other selections and H/Z preferences. The log shows
  `P1 berserk: +4 AP` (`Event::BerserkTriggered`, within the 31-character limit), and the refund is visible in
  existing AP values. This passive needs no activation control or AI selection policy. Native capability
  indicators are deferred.

Provenance, kept separate:
- **Catalog-stated:** 4 AP regained on a kill, once per turn, fatigue not refreshed.
- **APPROXIMATION (slice choices, no runtime confirmation):** triggering only on ordinary melee kills, the
  `ap_max` cap, the plain capability flag and its application to both factions, the player-only launch option
  and assignment, the event slot, treating Wait-separated portions of a turn as one scheduled turn, emitting
  the event even for a zero gain, and the log wording.

Tests: `crates/bb_rules/tests/berserk.rs` (20 rules tests: both factions, first and second kill, disabled and
used capability, exact arithmetic including `u32::MAX`, unchanged fatigue/resources/RNG against a
capability-free twin, misses/nonlethal/armour-only, Nine Lives then death, final-enemy ordering before
`BattleEnded`, Wait/EndTurn/EndRound lifecycle, opportunity attacks, query and rejected-command purity, hash
sensitivity and pins, AI progress bound), plus shared parser and 128-subset launch coverage, deterministic
command/event replays, a two-kill refund encounter and App consumption/restart tests in
`crates/bb_rules/tests/terrain_presets.rs` and `crates/bb_game/src/main.rs`.

Historical validation before the launch option recorded `cargo test --workspace --locked --offline` (735
passed, 0 failed) and unchanged seed-1 default smoke (`OUTCOME=Victory REASON=Escape ROUNDS=3
HASH=dfce9b8d74bcaf93`). T41 validation: `cargo test --workspace --locked --offline` passed (741 tests,
0 failed), `cargo build --workspace --locked --offline` passed, and `git diff --check` was clean.

Seed-1 smoke replays recorded after T41 (each `headless_smoke` invocation repeats internally; replay tests also
compare the complete command and event streams, enforce valid commands and bounded termination):

| Configuration | Flat | Hills |
| --- | --- | --- |
| `--berserk` | `Victory / Escape`, 3 rounds, `b90d0af968bae726` | `Victory / Elimination`, 4 rounds, `ae18c48440cd1618` |
| All seven capability options | `Victory / Escape`, 3 rounds, `17373b790c6aaffb` | `Victory / Elimination`, 4 rounds, `f471788f11714fdd` |

Deferred explicitly: refunds from reaction or opportunity attacks and other damage sources, Killing Frenzy (T67 later
adds a headless approximation, see "Killing Frenzy"; this deferral is retained as historical evidence) and
area attacks, enemy assignment beyond the current player-only launch option, native capability indicators,
perk learning or a generic perk framework. Partial coverage only; no parity claim. Earlier statements that no
`Unit` flag or event exists, or that native play could not enable Berserk, describe the pre-T41 state and are
retained here as historical evidence.


## Pathfinder (player launch option and native preview; partial UC-COMBAT-057 coverage)

**Catalog statement** ([UC-COMBAT-057](docs/source/10-tactical.md), public catalog copy, not runtime-confirmed):
moving costs less fatigue (the wiki example is often 50% less); it grants no extra AP pool. The catalog also says
terrain AP may ease, "lock to vanilla numbers"; no vanilla numbers are confirmed here.

`Unit.has_pathfinder` (default `false`) and `rules::PATHFINDER_MOVE_FATIGUE = 1` (beside `MOVE_FATIGUE = 2`)
apply the existing headless reduction to each attempted movement step. The single pure helper
`rules::move_fatigue(&Unit) -> u32` owns the capability branch. `plan_move` supplies the discounted
`MovePreview.fatigue_cost` and the nominal post-move attack budget; `reachable`, execution, automatic
`Flee`/`Withdraw` movement and AI affordability use the same rule query. Route selection, movement AP and
AI action selection are unchanged.

The shared `scenario::parse_args` accepts value-less `--pathfinder` (default false), independently with
`--seed`, `--terrain` and all seven existing capability options. Repeats are harmless. `LaunchArgs.pathfinder`
selects `has_pathfinder` on player units only through `launch_battle_for`; compatibility constructors provide
false and keep their results unchanged. Construction consumes no RNG and changes only selected capability flags.
The player-only assignment and launch option are **APPROXIMATION**s; the accepted 2-to-1 movement
fatigue approximation remains in `bb_rules` (uniform for Ground steps; T48 applies the same halving to Swamp and T71 to Snow, each 4 to 2).

`App` stores the selection. **R** rebuilds the same seed and terrain; **N** uses the next seed. Both preserve all
capability selections plus the **H**/**Z** preferences and restore fresh battle resources, zero Fast Adaptation
stacks and unused Nine Lives/Berserk state. Pathfinder is passive: the existing movement preview shows its lower
planned FAT value and the existing unit resource row shows current FAT. No activation control, dedicated
capability indicator or AI selection policy is needed; the client does not reproduce cost formulas.

- **Reactions.** The discounted fatigue of an attempted step (destination-dependent since T48/T71: Swamp and Snow are twice the Ground value) is charged (together with its AP) before
  opportunity attacks, including an attempt that is then stopped or lethal. Affordability of later steps is
  rechecked against the current fatigue after reactions (incoming hit/miss fatigue), so a miss can still
  strand a multistep move. Additions stay checked and the cap at `u32::MAX` is exact.
- **Not discounted.** AP costs (including uphill surcharges), attack and incoming-attack fatigue, Footwork,
  Rotation, Adrenaline and fatigue regeneration. An already-on-edge automatic escape remains free.
- **Invariants.** Construction consumes no RNG and changes only selected capability flags. Without
  `--pathfinder`, existing event streams and earlier hashes remain unchanged (`BattleState::new(1)`, Flat/Hills
  scenario and seed-1 smoke `Victory / Escape`, 3 rounds, `dfce9b8d74bcaf93`). A launch-built no-reaction
  encounter checks the same route and AP expenditure, discounted preview fatigue equal to actual expenditure,
  and unchanged RNG against its capability-free twin.
- **Hash.** `has_pathfinder` is flag bit 51 (bits 0..=50 unchanged); `false` leaves earlier hashes unchanged.

Provenance, kept separate:
- **Catalog-stated:** a fatigue reduction on movement (often 50%), no extra AP pool.
- **APPROXIMATION (slice choices, no runtime confirmation):** the 2-to-1 per-step cost (uniform at T43; T48 extends it to Swamp and T71 to Snow) (no confirmed
  vanilla rounding or terrain costs), application to both sides, coverage of automatic Flee/Withdraw movement,
  AI affordability, and the plain capability flag.

Tests: `crates/bb_rules/tests/pathfinder.rs` (18 tests): both factions, flat/uphill/downhill multistep
routes and unchanged AP against a capability-free twin, preview/`reachable`/execution agreement, exact fatigue
limits and one-short rejection, `u32::MAX` boundaries, validation precedence and rejection/query purity, the
attack-budget case the discount makes affordable, missed/stopping/lethal reactions and a continuation that
depends on incoming fatigue, Flee and Withdraw affordability/escape/free edge escape, AI longest affordable
prefix with valid commands, unchanged attack/Footwork/Rotation/incoming/regeneration costs, discounted
movement changing Dodge at once, and hash sensitivity without bit overlap plus unchanged pins. T43 adds shared
parser and construction checks for all 256 capability subsets (including mask 255), the launch-built move
comparison above, App execution and R/N persistence checks, and a headless scene test for discounted FAT plus
the MELEE BUDGET NO-to-YES fatigue boundary. Rendering code and layout are unchanged.

Seed-1 headless outcomes recorded for this tree (Flat / Hills): Pathfinder alone, `Victory / Escape`, 3 rounds,
`9cca6ef915788869` / `Victory / Escape`, 4 rounds, `6c00a08d1c11386d`; all eight capability options,
`Victory / Escape`, 3 rounds, `96886e87d9af4633` / `Victory / Elimination`, 4 rounds, `409a61a4ec43cbb5`.
Replay tests compare complete command and event streams twice, require each command to apply successfully and
termination within 10,000 commands. Earlier smoke pins remain checked. T43 validation: `cargo test --workspace
--locked --offline` passed (764 tests, 0 failed), `cargo build --workspace --locked --offline` passed, and
`git diff --check` was clean. Native visual inspection was not performed; scene output is verified headlessly.

Deferred explicitly: Pathfinder AP discounts, perk learning, dedicated capability
indicators, and any parity claim. Partial coverage only. The earlier T42 statement that the capability had no
playable assignment describes the pre-T43 state and is retained as historical validation evidence. Statements
that every attempted step costs `MOVE_FATIGUE` describe capability-free units on Ground only (the T43-era state); since T48 a Swamp destination costs `terrain_move_fatigue` (twice the Ground value), so they are no longer accurate there.

## Damage arithmetic bounds (T44)

`resolve_attack` samples every ordered inclusive damage range using a `u64` width and a `u64` sum with the
lower bound, then converts the in-range result to `u32`. This supports `0..=0`, `u32::MAX..=u32::MAX`,
`0..=u32::MAX` (width `2^32`), and narrow ranges ending at `u32::MAX`. A successful hit still consumes one
damage draw and uses the existing modulo sample without retries. For ranges whose arithmetic did not overflow,
damage, event streams, RNG consumption and hashes are unchanged.

Armour still absorbs `min(armour, damage)`, and when any armour is absorbed the existing `damage / 10` leak
still applies. `soak` computes the post-armour loss plus that leak in `u64`; if the mathematical sum exceeds
the `u32` event/state representation, returned `hp_loss` saturates at `u32::MAX`. Rolled damage and absorbed
armour are not clamped. This saturation is a representation choice inside the existing armour model's
**APPROXIMATION**; it adds no original-fidelity claim.

Both ordinary and opportunity attacks use the same corrected resolver. For a max-damage hit against a
`u32::MAX`-HP defender with one armour point, the saturated loss is still lethal under normal death handling.
Unused Nine Lives still intercepts that would-be-lethal hit, reports actual HP lost (`u32::MAX - 1`) in
`Event::Attacked`, leaves 1 HP, then continues through its existing morale handling. It does not bypass
survival processing.

Since T45 an effective Indomitable stance halves the sampled damage after this sampling and before `soak` (see "Indomitable"); the T44 statements describe stance-free attacks.

Failing-first debug evidence, before the production edit (`cargo test -p bb_rules --test damage_arithmetic
--locked --offline`): both initial regressions failed (0 passed, 2 failed). `soak_saturates_overflowing_partial_absorption`
panicked at `rules.rs:125:19` with `attempt to add with overflow`; `full_width_damage_range_samples_the_draw_low_word`
panicked at `rules.rs:365:62` with the same message. The expanded pre-fix suite also failed the ordinary max-hit,
Nine Lives max-hit and opportunity full-width cases at those shared arithmetic sites. This pre-fix output is
retained as historical evidence; the passing validation for T44 is recorded in the implemented-state document.

T44 tests: [`crates/bb_rules/tests/damage_arithmetic.rs`](crates/bb_rules/tests/damage_arithmetic.rs) covers
zero damage, zero armour, full and partial absorption, overflowing partial absorption, full-width, zero and
maximum singleton ranges, a narrow range ending at the maximum, ordinary and opportunity attacks, event values
and RNG draws, maximum-HP lethal handling, and Nine Lives actual-loss reporting. See
[`docs/forge/implemented/slice-state.md`](docs/forge/implemented/slice-state.md#damage-arithmetic-bounds-t44)
for the failing-first output and detailed validation. Actual T44 validation: focused debug and release tests
passed (7 each), `cargo test --workspace --locked --offline` passed (771 tests, 0 failed), and `git diff --check`
was clean.


## Indomitable (headless stance, partial UC-COMBAT-016 coverage)

**Catalog statement** ([UC-COMBAT-016](docs/source/10-tactical.md), public catalog copy, not runtime-confirmed):
"Indomitable: 5 AP 25 fat, 50% damage reduction and immunity to stun/knock/grab for the turn; less AI attention";
stances are cancelled immediately when Fleeing. The fidelity row for UC-COMBAT-016 remains **unconfirmed**; this
is partial coverage of the Indomitable item only, and no parity claim is made.

**Activation.** `Command::Indomitable { unit }` is accepted for a running battle and a present, active,
controllable actor of either faction with `Unit.has_indomitable` (default `false`). It charges
`INDOMITABLE_AP = 5` and `INDOMITABLE_FATIGUE = 25`, sets `Unit.indomitable_active`, and emits exactly
`Event::IndomitableUsed { unit }`. It does not advance the turn, reorder the queue or consume RNG. The pure
`rules::indomitable_availability(&Unit)` checks, in order: `MissingIndomitable`, `IndomitableActive`,
`NotEnoughAp`, then checked fatigue affordability (`TooFatigued`, no wrap at `u32::MAX`); callers supply the
battle, active-unit, controllability and presence gates (`apply` order: `BattleOver`, `NotActiveUnit`,
`RetreatingUnit`, `FleeingUnit`, absent actor `NotActiveUnit`, then the unit-level errors). Invalid commands
mutate nothing. `Unit::is_indomitable_active()` is the effective-stance query: capability, active flag,
battlefield presence and non-Fleeing morale.

**Damage.** In the shared `resolve_attack` (ordinary and opportunity attacks), after the accepted widened damage
sampling, an effective defender stance halves the sampled damage with integer division *before* `soak`; there is
no minimum of one, so a rolled 1 becomes 0. `Event::Attacked.dmg` reports this reduced value (the one supplied
to `soak`); without an effective stance it is the sampled value as before. A mitigated attack is still a hit:
incoming fatigue, Fast Adaptation reset and movement interruption are unchanged, even at zero damage. Armour,
Nine Lives, heavy-hit morale (thresholded on the resulting HP loss), death and Berserk all receive the reduced
damage. Draw consumption is unchanged (one hit draw, one damage draw).

**Lifecycle.** The flag is cleared only in the once-per-round fresh `begin_turn` refresh, before `TurnStarted`
(so at that unit's next fresh turn), or immediately when a rule-driven negative morale check (heavy hit or
ally death) makes the unit Fleeing. Wait returns, `EndTurn`, `EndRound`, a round-number change, movement,
Footwork, Rotation, Retreat intent and map-edge morale drops do not cancel it; self-rally never restores it.
Hash: `has_indomitable` is flag bit 52 and `indomitable_active` bit 53 (earlier assignments and false/false
hashes unchanged). The native log shows `P1 indomitable` on activation.

**Native loadout, button and marker (T46, all **APPROXIMATION**: loadout assignment, controls and presentation are
slice choices, not original-UI parity; UC-COMBAT-016 stays unconfirmed).**
- Launch: `--indomitable` (value-less, repeats harmless, both binaries) sets `LaunchArgs.indomitable` (default
  `false`) and composes independently with `--seed`, `--terrain` and the eight other capability options (all 512
  subsets are tested). `scenario::launch_battle_for` sets `has_indomitable` on the three player units only, with
  `indomitable_active` false, consuming no RNG; compatibility constructors (`launch_battle_with`,
  `adrenaline_battle`, `launch_battle`, ...) supply `false` and are unchanged. `App` stores the option, so `R`/`N`
  rebuild the battle with the capability, an inactive stance, fresh resources and reset passive state while keeping
  terrain, the other selections and the `H`/`Z` preferences.
- Skill strip: four equal 144x40 buttons at y 3 and x 684 (Adrenaline), 834 (Rotation), 984 (Footwork) and 1134
  (**INDOMITABLE (4)**), 6 px apart and ending 2 px inside the window; drawing and hit testing share the rectangles.
  Titles keep their full names and shortcuts and use scale 1.25; cost/effect rows stay at scale 1 (Rotation's effect
  row is now `SWAP ADJACENT ALLY`). The Indomitable button shows `COST 5 AP 25 FAT` and `HALF MELEE DAMAGE` from
  `INDOMITABLE_AP`/`INDOMITABLE_FATIGUE`. Panel, map, preview-box and dialog coordinates are unchanged.
- Input: the button and `4` are enabled only for the present, controllable (not Fleeing, not retreating) active
  player unit whose `rules::indomitable_availability` succeeds and submit exactly one `Command::Indomitable`
  through `App::run`; any unavailable activation (including a repeat while active) is a silent no-op. The selected
  controllable active player with an effective stance draws the active (green) look. Like Adrenaline, it first
  cancels open Rotation/Footwork targeting and arms the click guard, then attempts the activation; button clicks
  are routed before map clicks (also when disabled); confirmation dialogs consume `4` and button clicks; the click
  guard and the repeated-key filter apply.
- Marker: for every unit whose `is_indomitable_active()` is true (either faction, derived only from that rules
  query) a scale-1 `INDOMITABLE` tag is drawn at x 1092 on the row-top+51 line (65 px, between the widest Dodge
  breakdown, which ends at x 1085, and `NINE LIVES READY`, which starts at x 1177). Expiry at the unit's next fresh
  turn, Fleeing, death and escape remove it; retained retreat protection stays visible.
- **T47 headless AI policy — APPROXIMATION inspired by UC-COMBAT-020/016, without original-AI parity claims.**
  Inside the existing nonempty-adjacent-target branch, before Adrenaline selection, Indomitable is considered
  only when the actor has positive HP and widened `2 * hp <= stats.hp`, and `enemy_controllers_at(actor, actor.pos)`
  is nonempty. That query requires a present, non-Fleeing enemy; adjacent Fleeing enemies alone do not activate
  the stance. The actor must also pass `indomitable_availability`, have `AP >= INDOMITABLE_AP + ATTACK_AP`,
  and pass checked `fatigue + INDOMITABLE_FATIGUE + ATTACK_FATIGUE <= fatigue_max`. When all gates pass,
  `plan_turn` returns exactly `Command::Indomitable`. This policy has priority over Adrenaline. On the normal
  9-AP fixture the activation leaves 4 AP, so the next call selects the existing weakest-target attack; the
  active-stance availability check prevents a repeat. If a gate fails, existing Adrenaline, weakest-target
  attack, approach and fallback behavior remains. Withdraw and Flee retain priority. Planning remains pure,
  RNG-free, one-command-at-a-time and does not simulate attacks.
- Focused legal encounters in `indomitable_ai.rs` demonstrate real AI activation and its following attack for
  both factions. The scripted launch encounter in `terrain_presets.rs` separately verifies actual halving (a
  20-damage hit becomes 10 for the stance holder; the twin without it takes 20) on both terrains. Stock seed-1
  smoke did not activate the stance; its repeated runs still validate deterministic capability assignment and
  bounded play.
- Actual T47 seed-1 `headless_smoke` outputs (each run repeats internally):

| Loadout | Flat | Hills |
| --- | --- | --- |
| `--indomitable` | `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=f4049791f860bb83` | `OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=fc9e225218a8006e` |
| all nine options | `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=bd7679eafc17ec03` | `OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=87444e820db722a5` |

Historical T46 seed-1 outputs before the AI policy: Indomitable alone was Flat `f4049791f860bb83` / Hills
`fc9e225218a8006e`; all nine options were Flat `bd7679eafc17ec03` / Hills `87444e820db722a5`. T47 replay tests
compare enabled replays with each other and compare against capability-disabled streams conditionally: equality
remains required when no stance was cast, while a cast must produce its matching `IndomitableUsed` event.


**Provenance.** Catalog-listed: the 5 AP / 25 fatigue costs and the 50% reduction. **APPROXIMATION**: pre-armour
application, flooring, duration, movement persistence, cancellation scheduling, the capability flags and the
`Attacked.dmg` interpretation, (T46) the player-only loadout assignment, shortcut `4`, button layout and marker
presentation, and (T47) the health threshold, current-threat requirement, attack-budget reservation and priority
over Adrenaline. **Deferred**: knock-back and grab immunities, AI attention and broader stance tactics,
perk learning, other stances (Shieldwall, Spearwall, Riposte), ranged and status damage.
(T61 update: stun immunity is now honoured by the headless `apply_stun` effect API while the stance is effective; see "Stun effect". No skill inflicts stun yet and the earlier text is historical evidence; T62 qualifies this: only the headless Stunning Blow skill inflicts it, see "Stunning Blow".)

Tests: [`crates/bb_rules/tests/indomitable.rs`](crates/bb_rules/tests/indomitable.rs) (25) plus one `App` log
test in `crates/bb_game/src/main.rs`. Actual T45 validation: `cargo test -p bb_rules --test indomitable --locked
--offline` passed (25 tests), `cargo test --workspace --locked --offline` passed (797 tests, 0 failed), and
`git diff --check` was clean. Pinned `BattleState::new(1)` and Flat/Hills scenario hashes are unchanged.

T46 tests: `crates/bb_rules/tests/terrain_presets.rs` (512-subset parsing and construction, scripted launch
encounter with actual mitigation, smoke pins and pre-policy replay equality with the Indomitable-off streams;
subset masks widened to `u16`) and the `bb_game` App, layout and scene tests (four-button geometry, label fit, shortcut `4`,
disabled/active looks, targeting cancellation, modal isolation, click guards, `R`/`N` persistence, marker
lifecycle and maximum-width indicator clearance, rendering purity). Actual T46 validation: `cargo test
--workspace --locked --offline` passed (819 tests, 0 failed), `cargo build --workspace --locked --offline` passed
and `git diff --check` was clean. The native window was not inspected visually: the environment has no display, so
layout is verified by geometry tests only.

T47 adds `crates/bb_rules/tests/indomitable_ai.rs` for both factions, exact/one-short AP and fatigue budgets,
checked `u32::MAX` boundaries, even/odd/max HP thresholds, controller exclusions, priority/fallback, state/RNG
purity, valid activation followed by attack and the seven-command bound with Adrenaline and one Berserk refund.
`terrain_presets.rs` retains deterministic complete replay comparisons and valid-command/termination checks,
allowing Indomitable-on streams to differ only when the stance is actually cast. Actual T47 validation:
`cargo test -p bb_rules --test indomitable_ai --locked --offline` passed (7 tests);
`cargo test --workspace --locked --offline` passed (826 tests, 0 failed); `git diff --check` was clean.

## Ground and Swamp terrain — headless destination-dependent movement costs (T48, partial UC-COMBAT-005/033/057 coverage)

`terrain::{TerrainKind, TerrainMap, TerrainError}` (re-exported at the crate root) and the public field
`BattleState.ground: TerrainMap` add a second per-hex layer beside `elevations`. At T48, `TerrainKind` was
`Ground` (default) or `Swamp`, and only Swamp entries were stored. T71 adds `Snow`; the current map stores
all non-Ground entries in canonical axial order (`q`, then `r`), so equal maps compare and hash identically
whatever order they were configured in. API: `get` (`None` off the map),
`set` (`Err(TerrainError::OffMap)` off the map, with no mutation, including extreme `i32` coordinates; the
overflow-safe map-membership check is shared with `ElevationMap`), `entries` (non-`Ground` entries in order) and
`is_all_ground`. Assigning `Ground` removes the entry. Obstacle hexes may carry a kind but stay impassable.
At T48 terrain was configured headlessly only; T50 added the Swamp scenario, T51 native presentation, and T52
CLI launch through the shared scenario path. Configuration consumes no RNG.

Cost table (per step, charged for the **destination** hex; the starting terrain adds no departure charge):

| Destination | AP | Fatigue | Fatigue with `has_pathfinder` |
|---|---|---|---|
| Ground | `MOVE_AP` = 2 | `MOVE_FATIGUE` = 2 | `PATHFINDER_MOVE_FATIGUE` = 1 |
| Swamp | `SWAMP_MOVE_AP` = 4 | 2 x `move_fatigue` = 4 | 2 |
| Snow (T71) | `SNOW_MOVE_AP` = 3 | 2 x `move_fatigue` = 4 | 2 |

The existing uphill surcharge (`max(destination_height - departure_height, 0)` AP) is added to any
destination's AP cost; fatigue is not affected by height. Pathfinder never reduces AP. Crate-private
`step_costs` (AP via `step_ap_cost`, fatigue via the public `rules::terrain_move_fatigue`, which derives from the
retained `rules::move_fatigue(&Unit)` Ground baseline so the capability branch lives in one place), `route_costs`
and `affordable_prefix` are the single source of costs. `plan_move` (and so `Command::Move` and `preview_move`,
including `ap_remaining` and `attack_budget_available`), `reachable`, `walk`, the automatic `Flee`/`Withdraw`
step and the ordinary AI's longest-affordable-prefix `Move` all use them, with checked fatigue sums.

Unchanged: BFS routes, neighbour order and escape-route tie-breakers (terrain affects affordability, not route
selection or passability; weighted pathfinding stays out of scope); the intended step is charged before
opportunity attacks, including when a reaction stops it, and affordability is rechecked before every later step;
an already-on-edge escape is free; Rotation, Footwork, Adrenaline, Indomitable and attacks keep their fixed
costs; queries and rejected commands are state- and RNG-pure.

Hash: an all-`Ground` map appends nothing, so earlier hashes (including the Flat and Hills pins) are unchanged.
Otherwise a suffix follows the elevation suffix: `TERRAIN_HASH_TAG` (`0x54455252`, ASCII "TERR"), the entry
count, then per entry `q`, `r` and the kind code (Swamp = 1, Snow = 2) sorted by (`q`, `r`), in the existing
u64 encoding. Maps containing only the prior Ground/Swamp values keep the same suffix and hashes.

Provenance, kept separate:
- **Catalog-stated:** the 4-AP swamp step ("Murky swamp water 4 AP") in [UC-COMBAT-005](docs/source/10-tactical.md#uc-combat-005--movement-ap-and-fatigue-costs),
  public catalog copy, not runtime-confirmed. The catalog also notes extra fatigue per move and a Pathfinder
  fatigue reduction (UC-COMBAT-057, often 50%), without swamp numbers.
- **Catalog-stated (T71):** typical snow costs 3 AP in UC-COMBAT-005; the catalog's light-snow 2-AP exception
  is explicitly excluded. This statement, like the Swamp cost, is not runtime-confirmed.
- **APPROXIMATION (slice choices, no runtime confirmation):** the fatigue values (Ground 2, Swamp 4, Snow 4), charging the
  destination, 2-to-1 Pathfinder halving on both non-Ground kinds (4 to 2) with AP untouched, additive composition
  with the uphill surcharge, and the `TerrainMap` storage and hash design.

At T48, native Swamp presentation and CLI launch were deferred. T51 added Swamp rendering, overlay and
legend; T52 added shared CLI launch support in both binaries. Snow is configurable through `TerrainMap::set`
only; it has no preset, CLI option or native marking. Still deferred are procedural generation, terrain kinds
beyond Snow and Swamp, terrain combat modifiers, line of sight, weather, weighted pathfinding and AI targeting
changes. `bb_game` was untouched at T48; renderer support arrived in T51 (see "Swamp terrain presentation
and attack-strip placement (T51)"). This does not complete the source catalog.

Tests: `crates/bb_rules/tests/swamp_movement.rs` (23 tests): default and canonical storage, insertion-order
independence, reset to Ground, obstacle annotations, extreme-coordinate rejection, exact hash suffix and
sensitivity with unchanged all-Ground pins; all Ground/Swamp transitions across heights 0..=3 for both factions
and Pathfinder states; mixed multi-step routes and a patchwork map where preview, `reachable` and execution agree;
exact-cap and one-short AP/fatigue budgets, checked `u32::MAX` rejection and purity; preview attack-budget changes;
missed reaction then continued movement, a stopping hit that still charges the attempted swamp destination and
incoming fatigue preventing a later step; automatic Flee/Withdraw affordability, swamp edge step and free escape
from an occupied swamp edge; AI longest affordable prefix with valid commands; unchanged Rotation/Footwork costs.
Actual T48 validation: `cargo test -p bb_rules --test swamp_movement --locked --offline` passed (23 tests);
`cargo test --workspace --locked --offline` passed (849 tests, 0 failed); `git diff --check` was clean.

## Snow terrain API and shared movement costs (T71; partial UC-COMBAT-005/033/057 coverage)

`TerrainKind::Snow` is configurable only through `TerrainMap::set`; there is no Snow preset, CLI option or
scenario layout. Snow entries use the existing canonical non-Ground map storage and the existing destination
cost functions, so `preview_move`, `Command::Move`, `reachable`, `walk`, AI affordable prefixes, and automatic
Flee/Withdraw steps use the same calculation. The existing BFS routes, reaction order and fixed Rotation/
Footwork costs are unchanged.

Entering Snow costs `SNOW_MOVE_AP = 3` AP plus the existing uphill surcharge and `SNOW_FATIGUE_FACTOR = 2`
times `move_fatigue(unit)`: 4 fatigue normally or 2 with Pathfinder. The catalog states typical snow at 3 AP
in [UC-COMBAT-005](docs/source/10-tactical.md#uc-combat-005--movement-ap-and-fatigue-costs); the light-snow
2-AP exception is explicitly excluded. The 3-AP statement is catalog-stated, not runtime-confirmed. Snow
fatigue, charging the destination, Pathfinder composition and additive uphill composition are **APPROXIMATION** choices.
Ground (2 AP, 2/1 fatigue) and Swamp (4 AP, 4/2 fatigue) costs are unchanged.

The existing `TERRAIN_HASH_TAG` suffix encodes Snow with explicit kind code 2 (Ground remains omitted as 0,
Swamp remains 1), sorted with other entries by axial coordinate. Maps containing only prior Ground/Swamp entries
retain their hash encoding. Terrain configuration and pure movement queries consume no RNG; rejected commands
remain immutable. Snow does not add terrain combat modifiers, line of sight, weather or weighted pathfinding.

Snow has no native marking or launch preset. The existing presets remain Flat, Hills and Swamp; Snow maps must
be configured by a caller using `TerrainMap::set`. Native Snow rendering and controls are deferred. This is
partial catalog coverage and an intermediate milestone, not completion of `docs/source/**`.

`crates/bb_rules/tests/snow_movement.rs` (13 tests) covers canonical Snow storage/replacement/reset, obstacle
annotations, extreme off-map rejection, explicit hash code 2 and old scenario hash pins, both factions and
Pathfinder states across Snow entry/departure and heights 0..=3, mixed Ground/Snow/Swamp preview/reachability/
execution agreement, exact and insufficient AP/fatigue, attack budget and query purity, a stopping opportunity
hit charging a Snow entry step and a move that starts on Snow, AI prefixes, Flee/Withdraw affordability and free edge escape, and fixed
Rotation/Footwork costs on Snow. Actual T71 validation: `cargo test -p bb_rules --test snow_movement --locked
--offline` passed (13 tests); `cargo test --workspace --locked --offline` passed; `cargo build --workspace --locked
--offline` succeeded; `git diff --check` was clean. No native Snow presentation was inspected because Snow
markings are deferred; this is API/rules coverage only.

## Swamp scenario preset (T50; CLI launch added in T52)

Swamp is now selectable with `--terrain swamp` in both `headless_smoke` and `bb_game`; this section records
the original T50 programmatic preset and its provenance.

`scenario::TerrainPreset::Swamp` is available to callers of `scenario::battle` and the existing launch
constructors. It configures both layers through `ElevationMap::set` and `TerrainMap::set`, without RNG draws:
elevation is zero on all 61 hexes; passable hexes with `q == -1`, `0` or `1` are Swamp; every other hex is
Ground, including obstacles. Units, stats, placements and initial RNG match Flat and Hills. Flat remains the
default, Hills is unchanged, and the state hash includes the terrain map.

Example: P1's initial move from `(-3,3)` to `(-1,3)` follows `(-2,3)` Ground then `(-1,3)` Swamp. The
accepted movement rules preview 6 AP and 6 fatigue, leaving 3 AP and no attack budget; Pathfinder leaves AP
unchanged and previews 3 fatigue. Execution spends the same previewed values. The 4-AP Swamp step is
catalog-stated in UC-COMBAT-005, but runtime parity remains unconfirmed. The Swamp layout, fatigue values,
destination charging and Pathfinder application remain **APPROXIMATION**; this example demonstrates the
accepted slice rules, not a confirmed original-runtime result.

Historical T50 behavior: `TerrainPreset::parse` and `scenario::parse_args` accepted only `flat` and `hills`,
and the parser and `headless_smoke` assertions recorded `--terrain swamp` rejection with status 2 and the
then-current diagnostic. T52 replaces those rejection assertions with positive parsed-launch checks. At T50
native Swamp rendering, overlay and legend were also deferred; T51 implements them. T49's proposed
launch/renderer integration was rejected. Its review case was open at T50: a hovered enemy at `(1,-4)` can
place `HIT` text over a legend at `y=80`; T51 addresses and tests that collision.

Tests: [`crates/bb_rules/tests/swamp_preset.rs`](crates/bb_rules/tests/swamp_preset.rs) covers the exact
layout and elevation, unchanged Flat/Hills construction and hash pins, unit placement and initial RNG,
case-sensitive Swamp parsing, last-value-wins and smoke acceptance. T52 replaced T50's parser-rejection
assertions with positive parsed-launch checks. `parsed_swamp_smoke_runs_match_in_process_replays_for_launch_capabilities`
compares the CLI with replays for no capabilities, Pathfinder and all nine capabilities. The file also covers
every existing launch constructor and the nine-capability loadout, preview/execution costs with and without
Pathfinder, and deterministic bounded AI replays for seeds 1, 7 and 23 with no capabilities and with Pathfinder.
Actual T50 validation: `cargo test -p bb_rules --test swamp_preset --locked --offline` passed (5 tests);
`cargo test --workspace --locked --offline` passed (854 tests, 0 failed); `git diff --check` was clean. T50 was
an intermediate programmatic rules milestone; T52 adds CLI launch support. This does not complete the source catalog.

## Swamp terrain presentation and attack-strip placement (T51)

`bb_game`'s renderer (`crates/bb_game/src/render.rs`) now draws passable Swamp read from `BattleState.ground`
(never from a preset name or coordinates). Rules, event streams, hashes and RNG are untouched; scene
construction is state- and RNG-pure.

- **Fill.** A passable Swamp hex has its own dark-olive base fill. Movement (green), attack (red), Rotation
  (teal) and Footwork (violet) highlights keep their precedence over it; obstacles keep the brown obstacle
  look even if their ground kind is Swamp, and get no badge.
- **Badge.** Every passable Swamp hex carries a persistent scale-1 `S` badge (7x9 px at hex-centre offset
  (21.25, 7.25), the lower-right pocket mirroring the height badge). It is drawn whatever the H/Z preferences,
  highlights, occupants or battle state, and stays inside the hex outline and clear of the 22 px unit ring,
  the name, AP-cost label, resource bars, height badge, ZoC marker and SWAP/STEP labels.
- **Legend.** When any passable Swamp exists, a scale-2 legend sits at x=340, y=44/62/80: `S = SWAMP`,
  `BASE MOVE <SWAMP_MOVE_AP> AP` (the accepted `rules::SWAMP_MOVE_AP`, 4) and `FATIGUE IN MOVE PREVIEW`. The
  height legend at x=16 is preserved. Maps with no visible Swamp (including all-Ground maps and maps whose
  Swamp hexes are all obstacles) draw no Swamp legend or badges. Snow remains unmarked (T71).
- **Attack information at T51 (historical behavior):** the strip row was offered only when visible Swamp moved
  the chance label away from a terrain collision. T57 supersedes that terrain gate and adds costs and a
  conditional damage range on every terrain; see “Native attack hover information (T57)” below. The original
  `HIT n%` label remains above the target on maps without visible Swamp.
- **T49 collision.** The rejected T49 case (a hovered enemy at `(1,-4)` placing `HIT` text over the legend at
  `y=80`) is addressed by this relocation and verified on rendered geometry: with P1 at `(0,-3)` and E1 at
  `(1,-4)` the old label rectangle overlaps the legend (asserted), the label is absent, and T57 verifies all
  four attack rows clear both legend rows and every terrain badge.
- **Provenance.** Fill and badge colours, badge/legend/attack-row placement and wording are an
  **APPROXIMATION** (slice presentation, not original-UI parity). The T51 one-row placement was also approximate;
  T57's four-row display remains approximate. The 4-AP step it quotes stays catalog-stated
  (UC-COMBAT-005) and unconfirmed at runtime; the fatigue wording only points at the move preview, whose
  numbers remain the T48 rules approximation.

Historical T51 state: `--terrain swamp` and native launch integration were deferred, so scenes were reachable
only programmatically and in tests. T52 adds case-sensitive `swamp` parsing to the shared launch path used by
both binaries and builds the parsed configuration through the existing `App::from_args` and restart code.
This does not complete the source catalog.

Tests (in `render.rs`, 11 new): terrain-driven fills on the Swamp preset and an independently configured mixed
map (including Swamp obstacles and a reset-ground check), badge coverage, a software-rasterised visibility check
of every badge under movement/attack/Rotation/Footwork highlights, occupied hexes, all four H/Z combinations
and completed battles, badge geometry against the hex outline and own/neighbour rings, labels, bars, height
badges and ZoC markers, exact legend text/bounds/glyph support/separation (pixel-equal to the legend alone),
the all-Ground regression (Flat, Hills, default, obstacle-only Swamp), the `(1,-4)` regression, every enemy and
the 5/95 hit-chance clamps with ally/enemy-turn negatives, the strip gating and per-state non-overlap, and
purity plus the vertex budget.

Actual T51 validation: `cargo test --workspace --locked --offline` passed (865 tests, 0 failed; 253 in
`bb_game` unit tests); `cargo build --workspace --locked --offline` succeeded; `git diff --check` was clean.
Native visual inspection was not available (no DISPLAY or WAYLAND_DISPLAY in the validation environment), so
the evidence is headless geometry and raster tests only.

## Swamp CLI launch integration (T52)

The shared, case-sensitive parser accepts `--terrain flat`, `--terrain hills` and `--terrain swamp` in both
`headless_smoke` and `bb_game`; Flat remains the default, unknown arguments are ignored, and repeated terrain
options remain last-value-wins. A missing or invalid value exits before the game loop with
`--terrain needs flat, hills or swamp`. The native client uses its existing `App::from_args` construction and
restart path. Swamp starts with height numbers off and Z off. `R` rebuilds the same seed; `N` rebuilds the next
seed. Both preserve Swamp, selected capabilities and H/Z preferences while restoring initial resources and
clearing temporary battle/UI state.

Native launch examples:

    cargo run --locked --offline -p bb_game -- --seed 1 --terrain swamp
    cargo run --locked --offline -p bb_game -- --seed 1 --terrain swamp --pathfinder
    cargo run --locked --offline -p bb_game -- --seed 1 --terrain swamp --adrenaline --rotation --footwork --fast-adaptation --dodge --nine-lives --berserk --pathfinder --indomitable

Headless examples and measured seed-1 results (outcome, reason, rounds and final hash):

    cargo run --locked --offline -p bb_rules --bin headless_smoke -- --seed 1 --terrain swamp
    OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=423c077697a25dac
    cargo run --locked --offline -p bb_rules --bin headless_smoke -- --seed 1 --terrain swamp --pathfinder
    OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=dd3178f57784e78a
    cargo run --locked --offline -p bb_rules --bin headless_smoke -- --seed 1 --terrain swamp --adrenaline --rotation --footwork --fast-adaptation --dodge --nine-lives --berserk --pathfinder --indomitable
    OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=6ad3be781e358f1e

The three measured results are compared in tests against in-process replays of the parsed launch arguments;
the test derives the values from each replay instead of pinning them. P1's parsed Swamp move from `(-3,3)` to `(-1,3)` previews and spends
6 AP and 6 fatigue, or 3 fatigue with Pathfinder. The renderer builds the post-move scene without changing
battle state or RNG. The 4-AP Swamp destination cost remains catalog-stated in UC-COMBAT-005 and is not
runtime-confirmed; fatigue, Pathfinder application and the layout/presentation remain **APPROXIMATION**.

Remaining terrain limits: the map layout is hand-authored, Swamp is an opt-in fixed preset, and there is no
procedural generation, terrain editor, other ground kind, terrain combat modifier, terrain-driven line of
sight, weather or weighted pathfinding. Badge, fill, legend and attack-strip layout remain **APPROXIMATION**,
not original-UI parity. T52 is an intermediate milestone, not completion of `docs/source/**`.

Actual T52 validation: `cargo test --workspace --locked --offline` passed (867 tests, 0 failed);
`cargo build --workspace --locked --offline` succeeded; `git diff --check` was clean. Native visual inspection was unavailable because neither
`DISPLAY` nor `WAYLAND_DISPLAY` was set; the App scene-build and renderer tests provide headless coverage.

## Recover action — partial UC-COMBAT-065 coverage (T53)

`Command::Recover` is available to a present, active, controllable actor with at least
`RECOVER_AP` (9 AP). Existing actor, battle, Fleeing and retreating gates keep their precedence;
Recover then checks presence (`NotActiveUnit`) before AP (`NotEnoughAp`). No fatigue-capacity check
is made, and zero fatigue is accepted. No Recover perk/capability gate exists in this slice.

On success, current fatigue is divided by two using Rust's unsigned integer division (rounding down),
the actor's AP becomes zero, its queue entry is removed, and the existing `advance_turn` path runs once.
AP above 9 is forfeited. Recover itself draws no random numbers, does not reset stance or passive
flags and does not reorder units; the ordinary turn advancement it triggers still handles remaining
actors, Wait returns, skipped turns and round rollover, and that advancement (for example a next
actor's self-rally check or a fresh round) may draw RNG exactly as after `EndTurn`. Fatigue is halved
first. If advancement immediately starts a fresh turn, normal AP refresh and fatigue regeneration
happen afterward.

**Provenance and limits.** Static bytecode evidence for `getFatigue() / 2` supports the fatigue effect,
which is **RECONSTRUCTED**. UC-COMBAT-065 states 9 AP and “spend the whole turn”; both are
catalog-stated with no original-runtime confirmation. Integer rounding, forfeiting surplus AP,
accepting zero fatigue and availability without the catalogued Recover perk are **APPROXIMATION**
choices. The earlier layer-A summary said the cost and turn effect were unspecified; that historical
statement described what the old implementation had recorded, while the catalog text already supplied
those claims. Runtime type, perk gating and original behavior remain unverified. This is partial
catalog coverage, not completion of `docs/source/**`.

Focused validation: [`crates/bb_rules/tests/recover_action.rs`](crates/bb_rules/tests/recover_action.rs)
covers both factions, 9 AP and rejected 0–8 AP, surplus-AP forfeiture including `u32::MAX`, fatigue
values 0, 1, even, odd and `u32::MAX`, unchanged rejected state/RNG, former-actor rejection, Wait
returns, skipped actors, round rollover and self-rally/RNG equivalence with manual halving followed
by `EndTurn`. The existing self-rally and map-edge tests retain their assertions on independent
eligible states after Recover gained turn completion.

Actual T53 validation: `cargo test -p bb_rules --test recover_action --locked --offline` passed
(10 tests); `cargo test --workspace --locked --offline` passed with no failures; `git diff --check`
was clean.

### Native controls and shared availability (T54)

`BattleState::recover_availability(unit) -> Result<(), RuleError>` is a pure query (state- and
RNG-pure) that reports exactly what `Command::Recover` would return now: battle over, not the active
unit, retreating, Fleeing, absent, then too little AP, in the T53 precedence. Zero fatigue is eligible.
`apply` runs this same query, so execution, native activation and renderer enablement cannot diverge.

The native window offers **RECOVER (5)**, a 144x40 button at (724, 48) (`layout::RECOVER_BUTTON`;
drawing and hit testing share the rectangle) and key `5`. It sits below the unchanged four-button
skill strip, right of both terrain legends (Swamp legend ends near x 616), below the top bar, above
the map (top row from y 97) and left of the side panel (background from x 888, unit rows from y 52).
It uses the existing skill-button styling: enabled/disabled fill, a scale-1.25 title and two scale-1
rows, `COST 9 AP` (from `RECOVER_AP`) and `HALVE FAT, END TURN`. The layout and control are an
**APPROXIMATION** (slice presentation, not original-UI parity).

- **Availability:** the button and shortcut are enabled only for the present, active, controllable
  (not Fleeing, not retreating) player unit of a running battle whose `recover_availability` succeeds.
  An unavailable activation is a silent no-op, and a click on the disabled button is consumed, never
  reaching the map.
- **Input:** button clicks are routed before map clicks. `5` and the button first cancel open
  Rotation/Footwork targeting through the existing cancellation path (which arms the click guard), then
  submit exactly one `Command::Recover` through `App::run` when eligible. Confirmation dialogs consume
  `5` and Recover-button clicks. Recover adds no confirmation dialog and no new UI state; the existing
  AP/fatigue rows and the active-unit change are the feedback. The existing repeated-key filter
  (`key_press_accepted`) still drops auto-repeat before `5` is seen.
- **Double-click protection:** after a successful Recover the existing 350 ms click guard is armed from
  the input event's own `Instant`, so a follow-up click within the window cannot Recover or move the
  newly active unit; clicks at guard expiry work normally.
- **Provenance:** the fatigue halving remains **RECONSTRUCTED** from static evidence; the 9 AP cost and
  whole-turn behavior remain catalog-stated and runtime-unconfirmed. The button position, label text,
  shortcut `5` and guard reuse are **APPROXIMATION**.
- **Remaining limits at T54:** the AI did not yet select Recover. T55 adds a narrow policy described
  below. Recover perk/capability gating, launch options and recovery during approach remain deferred.
  No new events or log lines exist (a successful Recover logs nothing of its own). This is partial
  catalog coverage, not completion of `docs/source/**`.

Validation: [`recover_action.rs`](crates/bb_rules/tests/recover_action.rs) gains query purity and execution
agreement (success, zero fatigue, insufficient AP, inactive/unknown units, error precedence); `bb_game`
tests cover button/shortcut equivalence with one direct rules Recover (even, odd and zero fatigue),
unavailable states (AP 0 and 8, enemy turn, Fleeing, retreating, battle over, absent actor),
targeting cancellation, modal isolation, disabled-click consumption, injected-`Instant` guard
boundaries, layout bounds and hit testing, enabled/disabled appearance, text fit and glyph support, and
clearance on Flat, Hills and Swamp with H/Z combinations plus scene purity. Native visual inspection was not
possible (see below).

Actual T54 validation: `cargo test --workspace --locked --offline` passed (893 tests, 0 failed);
`cargo build --workspace --locked --offline` succeeded; `git diff --check` was clean. Native visual
inspection was unavailable because neither `DISPLAY` nor `WAYLAND_DISPLAY` was set; the App,
scene-build and rasterised-clearance tests provide headless coverage.

## AI Recover policy — partial UC-COMBAT-020/065 coverage (T55)

The deterministic AI considers Recover only inside its existing nonempty-adjacent-target branch.
Withdraw remains first, then Flee; both keep priority over ordinary actions. For an ordinary actor the
T55 order was Indomitable, Adrenaline, then an affordable Attack. T66 inserts an eligible Stunning Blow
selection before that branch; when no eligible target is available, the previous Indomitable, Adrenaline,
Attack, Recover and approach policy runs unchanged. This section records the T55 Recover rule; the
current T66 branch is documented in "Stunning Blow AI policy" below.

- current fatigue is positive;
- checked `fatigue + ATTACK_FATIGUE` overflows or exceeds `fatigue_max`, so the adjacent attack is
  unaffordable;
- the shared `recover_availability(actor)` query succeeds (including the existing active, present,
  controllable, running-battle and 9-AP checks); and
- checked `(fatigue / 2) + ATTACK_FATIGUE` fits `fatigue_max`.

If one check fails, planning falls through to the existing approach choice and then explicit `EndTurn`.
No adjacent target, 8 AP, zero fatigue, or a fatigue maximum that cannot fund the attack after halving
does not select Recover. There is no percentage threshold, planning simulation, battle clone or RNG draw.
Planning remains pure and returns one command while the battle runs.

Recover ends the scheduled turn: execution uses the existing rule path, removes that actor from the
current queue and advances the schedule. The same actor receives no follow-up command in that plan;
ordinary fatigue regeneration applies on a later fresh turn. Per-turn progress counts either Recover
or explicit `EndTurn` as the final command, distinct from the preceding commands that leave the turn active.

**Provenance and limits.** This selection policy is an **APPROXIMATION inspired by UC-COMBAT-020/065**,
with no original-AI parity claim. The command's RECONSTRUCTED fatigue-halving effect and catalog-stated
9 AP/whole-turn behavior retain the T53/T54 limits above. Recover perk/capability gating and recovery
during approach movement remain deferred. This is an intermediate milestone, not completion of
`docs/source/**`.

Focused validation: [`recover_ai.rs`](crates/bb_rules/tests/recover_ai.rs) covers both factions, 9 AP
versus 8, exact attack-fatigue capacity versus one over, `u32::MAX` checked-add boundaries, odd fatigue,
zero fatigue, insufficient post-halving capacity, no adjacent target, Withdraw/Flee and existing skill/
attack priority. It checks repeated-planning purity across units, queue, round, outcome, terrain, hash
and RNG, executes a selected Recover through turn completion, then confirms an affordable Attack on the
actor's next fresh turn after normal regeneration. The existing `indomitable_ai.rs` overflow regression
now expects Recover when halving restores the attack budget. The seed-1 replay test checks
deterministic complete command/event traces, valid commands and a per-turn bound for Flat, Hills and
Swamp with no capabilities, Pathfinder, and all nine capabilities. The per-turn check treats Recover and
explicit EndTurn as separate final choices.

Measured seed-1 replay results after T55 (all nine configurations selected Recover zero times; existing
smoke pins therefore remain unchanged):

| Terrain | Loadout | Outcome / reason / rounds | Final hash |
| --- | --- | --- | --- |
| Flat | none | Victory / Escape / 3 | `dfce9b8d74bcaf93` |
| Flat | Pathfinder | Victory / Escape / 3 | `9cca6ef915788869` |
| Flat | all nine | Victory / Escape / 3 | `bd7679eafc17ec03` |
| Hills | none | Victory / Escape / 4 | `d0b704f0294173be` |
| Hills | Pathfinder | Victory / Escape / 4 | `6c00a08d1c11386d` |
| Hills | all nine | Victory / Elimination / 4 | `87444e820db722a5` |
| Swamp | none | Victory / Elimination / 4 | `423c077697a25dac` |
| Swamp | Pathfinder | Victory / Elimination / 4 | `dd3178f57784e78a` |
| Swamp | all nine | Victory / Elimination / 4 | `6ad3be781e358f1e` |

Actual T55 validation: `cargo test -p bb_rules --test recover_ai --locked --offline` passed (8 tests),
`cargo test -p bb_rules --test fatigue_affordability --locked --offline` passed (5 tests),
`cargo test -p bb_rules --test indomitable_ai --locked --offline` passed (7 tests),
`cargo test -p bb_rules --test terrain_presets --locked --offline` passed (51 tests), and
`cargo test -p bb_rules --test swamp_preset --locked --offline` passed (6 tests).
`cargo test --workspace --locked --offline` passed (901 tests, 0 failed); `git diff --check` was clean.
`cargo build --workspace --locked --offline` succeeded.

## Melee attack preview (T56; headless UC-COMBAT-011 / UC-UI-001 foundation)

`rules::AttackPreview` and `BattleState::preview_attack(attacker, target)` expose a validated
ordinary melee attack plan for the current battle snapshot. The result contains `attacker`, `target`,
`ap_cost`, `fatigue_cost`, `hit_chance`, `damage_min` and `damage_max`. The costs use
`ATTACK_AP` / `ATTACK_FATIGUE`; chance comes from the existing `BattleState::hit_chance`, before
attack costs, incoming defender fatigue or attack-triggered passives and morale changes.

The query and `Command::Attack` share one private attack plan and preserve the command's validation
order: active/control gates, target validity, adjacency, AP, then checked fatigue affordability. The
query emits no events, consumes no RNG and mutates no battle, unit or terrain state. It describes only
the current snapshot; it does not reserve legality or resources. A later `Attack` revalidates against
the state at submission time.

`damage_min..=damage_max` is the inclusive range supplied to armour soak **if the attack hits**,
before armour processing. It starts at the attacker's ordered `Stats` endpoints. An effective defender
Indomitable stance applies the same integer-halving helper as sampled attack resolution to both
endpoints; flooring can produce zero. The preview does not predict armour loss, actual HP loss, Nine
Lives, or later morale/passive transitions. This API and its range presentation are **APPROXIMATION**;
the existing provenance of hit chance, scenario damage values and Indomitable is unchanged and does
not claim original-runtime confirmation. At T56, native damage-range presentation and HP-loss prediction
were deferred; T57 adds the range to the hover panel, while HP-loss prediction remains deferred.

Focused validation: [`attack_preview.rs`](crates/bb_rules/tests/attack_preview.rs) checks both attack
sides, shared chance/costs and event results with morale, surround, elevation, Dodge and Fast
Adaptation; error agreement and precedence; exact and overflowing fatigue limits; zero, odd, maximum
and full-width damage endpoints with effective and ineffective Indomitable; hit/miss event semantics;
query purity; and complete replay equality for ordinary, lethal and Nine Lives attacks. Run:

    cargo test -p bb_rules --test attack_preview --locked --offline
    cargo test --workspace --locked --offline

Actual T56 validation: the focused test passed (7 tests); the workspace passed (908 tests, 0 failed).
`git diff --check` was clean.

## Native attack hover information (T57; partial UC-COMBAT-011 / UC-UI-001 presentation)

On Flat, Hills, Swamp and independently configured mixed maps, hovering an adjacent present enemy while its
selected attacker is the active controllable player in a running battle shows four scale-2 rows in the existing
`PREVIEW_BOX`: `ATTACK <name>: HIT <chance>%`; `COST <ap_cost> AP <fatigue_cost> FAT`; `DAMAGE ON HIT
<damage_min>-<damage_max>`; and `BEFORE ARMOUR. MISSES DEAL 0.` The first row is yellow, the cost and damage rows
use `PREVIEW_COST`, and the explanation uses `PREVIEW_NOTE`. Valid-plan values come directly from
`BattleState::preview_attack`; the renderer adds no combat formulas.

If that query returns `NotEnoughAp` or `TooFatigued`, the target/chance row remains and a `PREVIEW_WARN` row says
`UNAVAILABLE: NOT ENOUGH AP` or `UNAVAILABLE: TOO FATIGUED`; costs and damage are omitted. Other query errors
suppress attack information. An occupied enemy hex has no movement preview, so the strip shows only one action
state. Rotation and Footwork keep their targeting rows, targeting mode hides ordinary attack information, and
completed battles keep the outcome banner. The T51 above-target `HIT n%` placement remains on maps without
visible Swamp and stays absent on visible-Swamp maps, including the rejected T49 `(1,-4)` collision case.

The damage band is conditional on a hit and is the current rules result after effective Indomitable flooring,
including possible zero damage, before armour processing. It does not mean HP loss; misses deal zero. Armour/HP
loss, survival and Nine Lives are not forecast. Rendering and repeated scene construction leave rules, events,
hashes and RNG unchanged. The four rows fit the existing strip and stay clear of legends, terrain badges and
adjacent UI. This presentation is an **APPROXIMATION**, not original-UI parity or runtime confirmation of the
underlying preview semantics; catalog and source-fidelity provenance remain separate.

Focused scene tests in `crates/bb_game/src/render.rs` compare displayed rows with `preview_attack` on Flat,
Hills, Swamp and mixed terrain; cover live Indomitable changes, odd/zero/full-width endpoints, exact and
unavailable resource budgets, allied/dead/escaped/nonadjacent targets, selection/turn/targeting/outcome gates,
T49 collision clearance, maximum-width glyphs, H/Z overlays and scene purity. T57 remains a partial milestone,
not completion of `docs/source/**`.

Actual T57 validation: `cargo test -p bb_game --bin bb_game --locked --offline` passed (272 tests);
`cargo test --workspace --locked --offline` passed; `cargo build --workspace --locked --offline` succeeded;
`git diff --check` was clean. Native visual inspection was unavailable because neither `DISPLAY` nor
`WAYLAND_DISPLAY` was set; the headless scene tests provide renderer coverage.

## Two-hex melee reach foundation (T58; partial UC-COMBAT-051 coverage)

Headless foundation (T58; the `--two-hex` launch option arrived afterwards in T59, see below).
`MeleeReach::{Adjacent, TwoHex}` (default `Adjacent`) is stored in `Unit.melee_reach`; T58 itself added no scenario,
launch flag or native control that sets `TwoHex`, so default play, events, RNG and hashes are unchanged.
`Unit::melee_reaches(at)` is the single pure geometry query: `Adjacent` accepts distance 1, `TwoHex` distances 1 and 2,
never distance 0. `legal_targets`, `preview_attack` and `Command::Attack` share it (the out-of-reach error stays
`NotAdjacent`, now meaning "outside reach"), as does the engaged-pair gate of `hit_chance`. Validation order, generic
Attack costs, damage and passives are unchanged. Hit modifiers keep their formulas; a two-hex attacker is not counted
as an adjacent ally for the surround bonus. Zones of control, `first_threat` and opportunity attacks stay at distance 1
whatever the reach (catalog: a melee-weapon unit keeps adjacent ZoC). The AI attacks an affordable distance-two target
through `legal_targets` before moving; at T58/T59 its approach planning still sought adjacent hexes (T60 updates the
policy below). `TwoHex` is flag bit 54 of
`state_hash` (`Adjacent` contributes zero).

Provenance: the two-hex reach and adjacent ZoC are catalog-stated (UC-COMBAT-051, not runtime-confirmed). The enum,
hash bit, reuse of generic costs, absence of obstruction/line-of-sight checks (attacks pass over allies and
obstacles) and the modifier composition are **APPROXIMATION**. Equipment and weapon profiles, polearm adjacent
penalties, mastery, spear Thrust, equipment-based loadouts and reach-aware approach were deferred at T58 (T59 adds
only the approximate player-only `--two-hex` launch option; T60 adds the limited approach policy below); this is a
range foundation, not a polearm system. Details: `docs/forge/implemented/slice-state.md`.

    cargo test -p bb_rules --test melee_reach --locked --offline
    cargo test --workspace --locked --offline

Actual T58 validation: the focused test passed (13 tests); the workspace passed (925 tests, 0 failed).
`git diff --check` was clean.

## Two-hex launch loadout (T59; partial UC-COMBAT-051 coverage)

Launch: `--two-hex` is a value-less option of the shared `scenario::parse_args` (`LaunchArgs.two_hex`, default false,
repeats harmless, any argument order). It composes independently with `--seed`, all three `--terrain` presets and the
nine capability options.

    cargo run --release -p bb_rules --bin headless_smoke -- --seed 1 --two-hex
    cargo run --release -p bb_game -- --seed 1 --terrain swamp --two-hex
    cargo run --release -p bb_game -- --seed 1 --terrain hills --two-hex --adrenaline --rotation --footwork --fast-adaptation --dodge --nine-lives --berserk --pathfinder --indomitable

Assignment: `launch_battle_for` gives all three player units `MeleeReach::TwoHex`; enemies keep `Adjacent`. Stats,
resources, positions, queue and the initial RNG are those of `battle(seed, terrain)`; the compatibility constructors
(`launch_battle`, `launch_battle_with`, and every `LaunchArgs` literal that supplies `false`) are unchanged, so
without the flag all scenarios, events, RNG streams and hash pins are unchanged. The flag is stored in the native
`App`, reproduced by `launch_args`, and kept by `R` and `N`: restart restores initial resources and temporary state
while preserving the chosen reach, terrain, other flags and the `H`/`Z` preferences.

Native indicator: every present `TwoHex` unit of either faction (scripted enemies too) draws a scale-1 `REACH 2` tag,
right aligned eight pixels inside the window on the name/morale row at row top + 3, in a dedicated colour. Adjacent,
dead and escaped units draw none. It clears every morale label and the existing ADAPT, ADRENALINE, DODGE, NINE LIVES
and INDOMITABLE indicators; no existing panel content or button moved and no control was added.

Input: no client rules. Hovering an enemy at distance two from the active player (for example behind an intervening
ally) shows the attack information from `preview_attack`, and the existing left click submits the same
`Command::Attack` as a direct command, without moving first. An adjacent-only twin shows no attack information and its
click is rejected with no state change; distance-three targets stay outside reach. Resource-unavailable rows, modal
isolation, the click guard and targeting precedence are unchanged.

Measured seed-1 `headless_smoke` results (each run is played twice by the binary and must agree). The in-process
replays in `crates/bb_rules/tests/two_hex_loadout.rs` pin the two `--two-hex` columns, and a test runs the real binary
and compares its printed line with the replay. The first column is the unchanged accepted default for comparison;
the last column is the nine capabilities without `--two-hex`.

| Terrain | default | `--two-hex` alone | `--two-hex` + all nine | all nine, no `--two-hex` |
| --- | --- | --- | --- | --- |
| Flat | Victory/Escape, 3 rounds, `dfce9b8d74bcaf93` | Victory/Escape, 2, `2202abb4c514f685` | Victory/Escape, 2, `214421f5a7ff225f` | Victory/Escape, 3, `bd7679eafc17ec03` |
| Hills | Victory/Escape, 4, `d0b704f0294173be` | Victory/Escape, 3, `d523f58695a2ad96` | Victory/Elimination, 3, `ec982429bed34670` | Victory/Elimination, 4, `87444e820db722a5` |
| Swamp | Victory/Elimination, 4, `423c077697a25dac` | Victory/Escape, 4, `1b928b9fc5eb363e` | Victory/Elimination, 4, `dec7c86aa2062269` | Victory/Elimination, 4, `6ad3be781e358f1e` |

Provenance and limits: the two-hex reach itself is catalog-stated (UC-COMBAT-051, not runtime-confirmed). The
`--two-hex` flag, the player-only assignment and the `REACH 2` marker are **APPROXIMATION**. All T58 limits remain:
generic Attack costs, no obstruction, line-of-sight or height restriction (attacks pass over allies and obstacles),
adjacent attacks unchanged (no polearm adjacent penalty), ZoC and opportunity attacks at distance 1 only, and
reach-unaware AI approach as of T59 (updated by T60 below). There is no weapon, equipment, mastery or Thrust system;
enemies cannot be given reach by a launch option. This is an intermediate milestone, not completion of `docs/source/**`.

    cargo test -p bb_rules --test two_hex_loadout --locked --offline
    cargo test -p bb_game --bin bb_game --locked --offline
    cargo test --workspace --locked --offline

Actual T59 validation: the focused test passed (9 tests); `bb_game` passed (287 tests); the workspace passed
(949 tests, 0 failed); `cargo build --workspace --locked --offline` succeeded; `git diff --check` was clean. Native
visual inspection was unavailable because neither `DISPLAY` nor `WAYLAND_DISPLAY` was set; the headless scene and
App tests provide renderer and input coverage.

## TwoHex approach policy (T60; partial UC-COMBAT-020/051 coverage)

`MeleeReach::reaches(from, to)` is the public, widened-coordinate geometry query; `Unit::melee_reaches` delegates to
it. TwoHex approach uses the actor-rooted BFS distance list as candidate positions for each present hostile in
`state.units` iteration order. A candidate qualifies when `MeleeReach::TwoHex.reaches(candidate, enemy.pos)` holds.
The BFS origin is eligible even though occupied, so an actor already in reach has a zero-step candidate. The policy
selects the smallest BFS step distance and replaces the current choice only when strictly closer. Ties therefore
keep enemy iteration order first, then BFS discovery order. Occupied non-origin positions, obstacles and off-map
positions are absent from BFS. Adjacent actors keep their prior candidate generation and ordering unchanged.

Existing Withdraw, Flee, attack, skill and Recover choices run first. If a TwoHex actor is in reach but has no
affordable Attack or applicable skill/Recover choice, the origin candidate yields EndTurn; it does not advance into
adjacency just because the current attack is unaffordable. For another selected destination, AI follows the existing
deterministic BFS path and emits the longest prefix affordable under the shared AP, terrain, Pathfinder and checked
fatigue rules. If no step is affordable, it ends the turn. Candidate choice does not rank terrain costs, reserve AP
or fatigue for a later attack, or score tactical safety.

This deterministic nearest-step selection is an **APPROXIMATION** inspired by UC-COMBAT-020's catalog statement
that AI spends AP to approach and UC-COMBAT-051's two-hex reach. Neither catalog row specifies this candidate order
or tie-breaking, and neither is runtime-confirmed. Reach-unaware approach was the T58/T59 state; T60 implements only
this bounded TwoHex behavior. Terrain-cost optimization, tactical safety, weapon profiles and broader tactics remain
deferred. It is not original-AI parity or product completion.

Focused validation: `crates/bb_rules/tests/melee_reach_ai.rs` (6 tests) covers both factions moving from distance
three to two and attacking on the next planning call through an intervening ally, in-range EndTurn plus retained
skills/Recover, occupied and unreachable candidates, enemy/BFS tie order, no target, partial prefixes, uphill and
Swamp costs, Pathfinder, checked fatigue boundaries, preview/apply agreement and planner purity. `melee_reach.rs`
also verifies delegated geometry and extreme-coordinate safety and retains the Adjacent unaffordable-approach
comparison. `two_hex_loadout.rs` repeats complete deterministic replays and compares the actual headless binary.

Current measured seed-1 `headless_smoke` results (binary and in-process replay agree):

| Terrain | `--two-hex` alone | `--two-hex` + all nine capabilities |
| --- | --- | --- |
| Flat | Victory/Escape, 2 rounds, `2202abb4c514f685` | Victory/Escape, 2, `4fd6ba9dd1767299` |
| Hills | Victory/Escape, 3, `d523f58695a2ad96` | Victory/Elimination, 3, `ec982429bed34670` |
| Swamp | Victory/Escape, 4, `1b928b9fc5eb363e` | Victory/Elimination, 4, `dec7c86aa2062269` |

Only the observed Flat + all-nine TwoHex replay changed from the T59 table; the T59 table remains historical evidence.
Default/no-`--two-hex` replay pins and initial-state construction are unchanged.

    cargo test -p bb_rules --test melee_reach_ai --locked --offline
    cargo test -p bb_rules --test melee_reach --locked --offline
    cargo test -p bb_rules --test two_hex_loadout --locked --offline
    cargo test --workspace --locked --offline

Actual T60 validation: the focused tests and workspace passed; replay measurements are pinned by the focused replay
test. This remains an intermediate milestone, not completion of `docs/source/**`.

## Stun effect (T61; headless foundation, partial UC-COMBAT-028 / UC-COMBAT-016 coverage)

`BattleState::apply_stun(unit: u32) -> Result<Vec<Event>, RuleError>` is a **system-effect API**, not a player
`Command`, and (as of T61) no skill, weapon, AI policy or native control inflicts stun yet (T62 qualifies this: only the headless `Command::StunningBlow` skill does; weapons and AI policy still do not. T64 adds native controls for programmatically granted capability; T66 later adds bounded AI selection). It is the first status effect and is
not a complete status system. `Unit::stunned` (default `false`) is the whole state.

Application, in order: a finished battle is `BattleOver`; an unknown, dead or escaped target is `InvalidTarget`
(rejections change nothing and draw no RNG). If `Unit::is_indomitable_active()` holds the only result is
`StunResisted { unit }` (status, resources, queue and RNG untouched, even for an already stunned or the active unit).
Otherwise an already stunned target is an idempotent success with no events and no duration stacking. A new stun
sets the flag and emits `StunApplied { unit }`. An inactive target keeps its queue position and resources. When the
target is the active unit, normal advancement runs at once, so its current entry is forfeited:
`StunApplied`, `StunExpired`, then the next unit's events. Application itself consumes no RNG.

Lifetime: a stunned unit exerts **no zone of control**, immediately, whatever its melee reach: it is excluded from
`enemy_controllers_at`, so movement previews, `first_threat` and opportunity attacks ignore it. It keeps its hex
(occupancy), stays targetable and damageable, and its stats are unchanged. In `advance_turn` the existing
`begin_turn` processing (once-per-round refresh, regeneration, passive resets, self-rally) runs first; then, before the
`EndRound` skip handling, a stunned actor's entry is consumed: AP to zero, `stunned` cleared, `StunExpired { unit }`,
the entry removed and advancement continues. This applies to a fresh visit and to a Wait return (which gets no second
refresh), and it forfeits Flee/Withdraw turns, including an otherwise free edge escape (the unit escapes on a later
visit). `begin_round` does not clear a pending stun, so a stun applied after a unit finished its round survives to its
next scheduled entry. A stun combined with an `EndRound` skip consumes one entry and causes no extra lost turn. The
`Retreat` order's immediate edge exits are not turns and ignore stun. At T61/T62 the new events were ignored by the
native event match and had no presentation; T63 supersedes that presentation snapshot with a partial marker and log
mapping (see "Native stun marker and event log (T63)"). Native skill controls remain deferred at T61-T63 (T64 later adds the Stunning Blow button and targeting for programmatically granted capability; see "Native Stunning Blow targeting (T64)"). `state_hash` flag bit 55
encodes `stunned`; all earlier assignments and all non-stunned hashes are unchanged.

Provenance. Catalog-stated ([UC-COMBAT-028 and the UC-COMBAT-007 ZoC text](docs/source/10-tactical.md), not runtime-confirmed):
a stunned unit skips its turn and exerts no ZoC; ([UC-COMBAT-016](docs/source/10-tactical.md)): Indomitable grants
stun immunity. **APPROXIMATION**: boolean storage, the next-queue-entry duration (no round or turn counter), the
active-target scheduling, the Wait / EndRound / Fleeing / retreating interactions, event names and order, the
Retreat-exit exemption and the hash encoding. Deferred: stun skills, chances, weapon damage, mastery, equipment,
other immunities (knock-back, grab), AI awareness, native activation controls and all other statuses. The T61/T62
statement that native indicators and logs were absent is historical; T63 adds only the partial marker and event text.

Focused validation: `crates/bb_rules/tests/stun.rs` (26 tests) covers defaults and hash pins (including an
independent hash mirror for bit 55), application on both factions, idempotence, rejections, effective and ineffective
Indomitable, ZoC loss with two-hex reach and absent opportunity attacks, targeting and damage against stunned units,
fresh and active-target event order, Wait returns, application after a finished turn and round rollover, consecutive
and all-queued stunned actors, time-limit termination, EndRound combination, Fleeing (one self-rally draw), edge
escape delay, retreating units, and regeneration/passive resets once.

    cargo test -p bb_rules --test stun --locked --offline
    cargo test --workspace --locked --offline

Actual T61 validation: both commands and `git diff --check` passed. This remains an intermediate milestone, not
completion of `docs/source/**`.

## Stunning Blow (T62; headless paid melee skill, APPROXIMATION connecting the T61 stun)

`Command::StunningBlow { attacker, target }` is the first skill that calls `apply_stun`. **APPROXIMATION throughout:**
the independent skill name, the capability flag, the 6 AP / 25 fatigue costs, the reuse of the generic damage
range and hit chance, adjacent-only targeting and the event timing are slice design. A guaranteed stun conditional on
a hit is only *inspired by* the catalog's mastered-mace behaviour ([UC-COMBAT-050 / 068](docs/source/10-tactical.md)); no
mastery, weapon profile or original-runtime parity is implemented. This is not a complete mace or mastery system.

Contract. `Unit.has_stunning_blow` (default `false`; `state_hash` flag bit 56, all earlier bits and every default hash
and replay pin unchanged) is the capability. `STUNNING_BLOW_AP = 6`, `STUNNING_BLOW_FATIGUE = 25`. Validation order:
`BattleOver`, the active/controllable actor gates (`NotActiveUnit`, `RetreatingUnit`, `FleeingUnit`), a present
attacker (`NotActiveUnit`), `MissingStunningBlow`, a valid hostile present target (`InvalidTarget`), distance exactly
one (`NotAdjacent`, via `MeleeReach::Adjacent.reaches` even for a `TwoHex` attacker, who still reaches distance two with
the ordinary `Attack`), `NotEnoughAp`, then checked fatigue (`TooFatigued`, including `u32::MAX` without overflow).
`preview_stunning_blow(attacker, target) -> Result<AttackPreview, RuleError>` returns the same plan as execution
(the ordinary Stats damage range and `hit_chance`, with the skill's own costs; the stun is conditional and not part of
the preview). `legal_stunning_blow_targets(attacker) -> Vec<u32>` lists the accepted targets in ascending id order and
is empty when the actor, capability or resources fail. Both are state- and RNG-pure. They share one private validated
plan (`plan_melee`) with the ordinary attack preview and `Command::Attack`, which keep their validation, costs and effects.

Resolution reuses the ordinary melee resolver with the skill's planned costs (same roll, damage draw, Indomitable
halving, armour soak, incoming fatigue, Fast Adaptation bookkeeping, Nine Lives, death and morale handling), so RNG
draw counts equal those of an equivalent ordinary attack; the stun adds no draw. A miss consumes the costs, adds the
usual miss fatigue and attempts no stun. On a **hit against a surviving target** (including armour-only, zero-damage
and Nine Lives survival) `apply_stun` runs after `Attacked` and any `NineLivesTriggered`, but before the surviving
heavy-hit morale check. So an initially effective Indomitable defender resists at impact (`StunResisted`) even if that
same hit's morale check then makes it Fleeing and cancels the stance; an already stunned target is the accepted
idempotent no-op. A dead target is never stunned. The attacker's turn stays active (no advancement); a stunned target
loses its ZoC at once and forfeits its next queue entry through normal advancement. A lethal skill kill uses the
existing death / ally-death / kill-morale handling, then the once-per-turn Berserk refund, then the outcome check, as
for an ordinary paid kill; misses, survivors and Nine Lives interception never trigger Berserk. Opportunity attacks,
even by a capable unit, never stun. No new event type: the existing stun events are consumed by T63's native log.

Deferred at T62: native activation, targeting, status indicators and log text, launch options, AI selection, equipment,
weapon profiles, mastery, stun chances, and opportunity-attack stun. T63 adds only a partial status marker and log
mapping; skill activation, targeting, launch loadouts, tooltips and AI casting remained deferred at T63 (T64 qualifies
native activation and targeting for programmatically granted capability; T65 adds player launch loadouts; T66 adds
limited AI selection below). Tooltips, weapon profiles, mastery, stun chances and opportunity-attack stun remain deferred.

Focused validation: `crates/bb_rules/tests/stunning_blow.rs` (30 tests, both factions) covers defaults and an
independent hash mirror for bit 56, error precedence and exact AP / fatigue limits, `u32::MAX` fatigue, distance two
despite `TwoHex`, preview / query / execution agreement and purity (previewed vs unpreviewed clones), guaranteed hit and
miss streams against equivalent ordinary attacks (resources, incoming fatigue, Fast Adaptation, RNG state), armour-only,
zero-damage, lethal, Nine Lives and already-stunned cases, Indomitable resistance and the Breaking-to-Fleeing ordering
case, ZoC loss and next-entry forfeiture without advancing the attacker, Berserk once / never, and ordinary and
opportunity attacks not stunning.

    cargo test -p bb_rules --test stunning_blow --locked --offline
    cargo test --workspace --locked --offline

Actual T62 validation: both commands and `git diff --check` passed. This remains an intermediate milestone, not
completion of `docs/source/**`.

## Native stun marker and event log (T63; APPROXIMATION)

The native unit panel reads stun state directly from `Unit.stunned` and battlefield presence. Each present stunned unit
of either faction gets a scale-1 `STUN` text tag at `row_y + 3`; dead, escaped and unstunned units get none, even if
capability flags are set. The tag's measured right edge is four pixels before the existing `REACH 2` slot, whether or
not that unit has TwoHex reach. `Painter::text_width` places it after the widest morale label. The dedicated status color
and the word `STUN` provide both color and text cues. Existing names, morale labels, REACH 2 and passive/stance
indicators keep their positions. Scene construction reads state only and consumes no RNG.

`App::run` maps each received event in sequence: `StunApplied` to `<name> stunned`, `StunExpired` to
`<name> skips turn: stun ends`, and `StunResisted` to `<name> resists stun`; `TurnStarted` remains ignored. Reapplying
stun is idempotent in the rules and emits no event, so it adds no log line. The new stock-name lines fit the existing
31-character display limit and use supported font glyphs.

Headless App evidence uses a deterministic successful `StunningBlow`: the hit logs before `E1 stunned`, the marker
appears while E1 is pending, then P1's ordinary `EndTurn` consumes E1's queued entry, removes the marker and logs
`E1 skips turn: stun ends`. A real hit against an effective Indomitable stance logs `E1 resists stun` and draws no
marker. Miss and lethal-hit paths produce no false stun line. These are native presentation choices marked
**APPROXIMATION**, not catalog-confirmed wording or UI parity.

Deferred at T63 (historical; T64 adds native Stunning Blow activation and targeting for programmatically granted
capability, T65 adds the player launch loadout, and T66 adds bounded AI casting): tooltips, weapon profiles, mastery,
stun chances, opportunity-attack stun and the broader status system. This remains partial status presentation, not
completion of `docs/source/**`.

Focused coverage: `bb_game` App tests exercise hit, miss, lethal-hit, effective Indomitable resistance, idempotent
reapplication, ordered logs and the marker lifecycle. Renderer tests cover both factions, presence exclusions, all
morale states, both reach modes, every existing panel indicator, geometry and scene purity. Native visual inspection
was unavailable in the headless environment; these layout claims are checked by CPU geometry tests.

Actual T63 validation: `cargo test --workspace --locked --offline` passed (1,020 tests, 0 failed),
`cargo build --workspace --locked --offline` succeeded, and `git diff --check` was clean. Native inspection was
unavailable (`DISPLAY` and `WAYLAND_DISPLAY` were unset and `Xvfb` was not installed).

## Native Stunning Blow targeting (T64; APPROXIMATION)

T64 added native controls for the T62 `Command::StunningBlow`, reusing the Rotation/Footwork targeting lifecycle. Its
programmatic-only state is historical: at T64 no shipped loadout, scenario or CLI flag granted
`Unit.has_stunning_blow`, so the real-binary button stayed disabled and shortcut `6` was a silent no-op. T65 adds the
`--stunning-blow` launch option and carries that selection through `R`/`N`; a capability manually added in code still
disappears on restart when the selected launch configuration omits the option. All presentation below is
**APPROXIMATION**: slice wording, colours and
layout, not original-UI parity; the stun itself, its costs (6 AP, 25 fatigue) and the damage range remain the T62
APPROXIMATION rules, untouched.

- Button: **STUN BLOW (6)**, a 144x40 skill-styled button at (16, 100), below the three-row height legend (which ends at
  y 94) and left of the map (no map hex, with its outline, overlaps it). Title at scale 1.25 over two scale-1 rows,
  `COST 6 AP 25 FAT` (from `STUNNING_BLOW_AP` / `STUNNING_BLOW_FATIGUE`) and `HIT MAY STUN TARGET`. It uses the existing
  disabled / enabled / active fills; drawing and hit testing share `layout::STUNNING_BLOW_BUTTON` and the existing
  button-before-map click routing applies.
- Activation: `6` or the button, enabled for the present, controllable (not Fleeing, not retreating) active player when
  `legal_stunning_blow_targets(actor)` is non-empty (capability, AP, fatigue and adjacency are decided by that rules
  query; a `TwoHex` attacker still only gets adjacent targets). Otherwise a silent no-op. `TargetAction` gained
  `StunningBlow`; the same `PendingTargeting` captures actor, round and state hash.
- Display: legal enemy hexes get a rose fill (the hovered one brighter) and a scale-1 `STUN` label on the token; movement
  highlights, AP labels, movement preview, ordinary attack hover (red fill, `HIT n%` label and attack rows) are
  suppressed while the mode is open. Terrain badges, legends, ZoC markers, token names, bars and the unit panel are drawn
  as before.
- Preview strip (four rows at most): instructions (`STUN BLOW: P1 PICKS A MARKED ENEMY TO STRIKE. ESC OR 6 CANCELS.`, cost,
  hint) until a legal target is hovered, then rows from `preview_stunning_blow`: `STUN BLOW E1: HIT n%`, the cost row, the
  conditional `DAMAGE ON HIT min-max BEFORE ARMOUR. MISSES DEAL 0.` and one stun row. The stun row never promises an
  outcome: `A SURVIVING HIT ATTEMPTS TO STUN. MISSES DO NOT.`; for an effective Indomitable target `INDOMITABLE: A SURVIVING
  HIT IS RESISTED, NO STUN.` (the previewed damage is already the rules' halved range; resistance wins over an existing
  stun, as in `apply_stun`); for an already stunned target `ALREADY STUNNED: A SURVIVING HIT CHANGES NOTHING.` Every row
  uses supported glyphs and fits the 844 px text width, including `u32::MAX` damage endpoints.
- Selection: a click on a legal target revalidates the captured actor, round and state hash and the current preview, clears
  targeting, arms the 350 ms click guard and submits exactly one `Command::StunningBlow` through `App::run` (so the ordered
  log, `E1 stunned` / `E1 resists stun` lines and the `STUN` marker are the accepted T63 behaviour). Any other click keeps
  targeting open with `NOT A STUN TARGET: CLICK A MARKED ENEMY OR PRESS ESC` and never falls through to Move or Attack. A
  stale intent is discarded with `STUN BLOW DISCARDED: BATTLE CHANGED` and no command.
- Cancel and switching: a second `6`, `Esc` or the button cancels and arms the guard (`Esc` outside targeting and dialogs still
  quits; auto-repeat key events remain ignored). Rotation (`2`), Footwork (`3`) and Stunning Blow (`6`) switch directly
  when the target mode is available; an unavailable switch leaves the current mode unchanged. `E`, `W`, `X`, `V`, `1`, `4`,
  `5`, `R`, `N` and the End Round, Retreat and Adrenaline buttons cancel it first; confirmation dialogs consume every
  targeting input. Rendering and inspection consume no RNG and mutate no battle state.

At T64, and still after T65, AI casting was deferred; T66 now adds the bounded AI policy below. Other out-of-scope
items still deferred after T65 include equipment, mastery,
tooltips, additional statuses and combat/stun formula changes (`bb_rules` is untouched).

Focused coverage: `bb_game` App tests (granted-capability activation by button and shortcut, 15 disabled-state cases and
resource boundaries, adjacent-only targets with `TwoHex` reach, selection compared with a direct `StunningBlow` for hit,
miss, Indomitable, already-stunned and lethal fixtures, invalid-click isolation, stale intent table, all six switching
pairs by key and button, unavailable switches, cancel, repeat-key filtering, modal isolation, other actions, R/N, guard
expiry and hover preview agreement), layout tests (rectangle, clearances including every map hex, labels) and renderer
tests (button looks, highlights and hover, suppression, preview agreement, immunity / already-stunned wording, glyphs and
bounds at `u32::MAX`, legend / badge / overlay / token preservation on Flat, Hills and Swamp, purity).

Actual T64 validation: `cargo test --workspace --locked --offline` passed (1,041 tests, 0 failed),
`cargo build --workspace --locked --offline` succeeded and `git diff --check` was clean. Native visual inspection was not
performed (`DISPLAY` and `WAYLAND_DISPLAY` unset, `Xvfb` not installed); layout claims rest on CPU geometry and scene tests.

## Stunning Blow launch option (T65; APPROXIMATION assignment)

`LaunchArgs.stunning_blow` defaults to `false`; the shared parser accepts the value-less, repeat-safe
`--stunning-blow` in both binaries with seed, Flat/Hills/Swamp and every existing flag. `launch_battle_for` assigns only
`has_stunning_blow` to player units. It does not change enemies, stats, resources, positions, terrain, RNG or initial
statuses. The existing T64 button and shortcut `6` now work in a parsed opt-in launch; targeting still comes from the
T62 rules query, so only an adjacent enemy is legal even when the attacker has `TwoHex` reach. This launch assignment is
an **APPROXIMATION**, not skill learning, mastery, weapon equipment or original-runtime parity. The T62 skill's accepted
costs, damage and stun limitations remain as documented above.

`App` stores the option in its launch configuration. `R` restores the same seed and selected loadout; `N` advances the
seed and restores the same loadout. Both construct a fresh battle with no initial stun effects and reset unit resources;
an open Stunning Blow target selection is cancelled with the existing click guard. Restart also clears a pending
confirmation through the existing rebuild path. A manually granted capability in a session started without the option
does not persist. The targeting cancellation, confirmation cleanup, click guard and confirmation isolation continue to
use the accepted T64 lifecycle.

Runnable examples:

    cargo run --locked --offline -p bb_rules --bin headless_smoke -- --seed 1 --terrain flat --stunning-blow
    cargo run --locked --offline -p bb_game -- --seed 1 --terrain hills --stunning-blow --adrenaline

At T65, `ai::plan_turn` did not cast Stunning Blow. The historical replay results below prove repeatability
and loadout composition for that task only; T66 adds limited deterministic AI selection, described below.

Historical T65 seed-1 headless outcomes `(outcome, reason, rounds, hash)` for Stunning-Blow-only and combined (all
existing flags, including `--two-hex`) loadouts:

| Terrain | Stunning Blow only | Combined loadout |
| --- | --- | --- |
| Flat | Victory, Escape, 3, `22ce433d9d5fff10` | Victory, Escape, 2, `f01993db257addfa` |
| Hills | Victory, Escape, 4, `b567c3583f1bfd41` | Victory, Elimination, 3, `08da7892abec7a0f` |
| Swamp | Victory, Elimination, 4, `d20f555042788393` | Victory, Elimination, 4, `9b849cd3fd61b5ea` |

`crates/bb_rules/tests/stunning_blow_loadout.rs` covers parsing and error compatibility, player-only assignment and
construction invariants, default-disabled compatibility constructors, twice-run bounded AI replays and exact agreement
with `headless_smoke`. `bb_game` App tests start from parsed opted-in arguments, exercise both existing activation
controls against a legal adjacent fixture and compare selection with the direct T62 command; they also check `R`/`N`
persistence, fresh unstunned state, targeting cancellation and pending-confirmation cleanup. At T65 AI casting remained
deferred; T66 supersedes that status only for the bounded policy below.

Actual T65 validation: `cargo test --workspace --locked --offline` passed; `cargo build --workspace --locked --offline`
succeeded and `git diff --check` was clean.

## Stunning Blow AI policy (T66; APPROXIMATION)

After the unchanged Withdraw and Flee branches, `plan_turn` asks
`legal_stunning_blow_targets(actor)` for targets accepted by the shared rules validation, including capability,
adjacency, AP and checked fatigue affordability. It excludes targets whose `stunned` flag is set or whose
`is_indomitable_active()` stance is effective, then selects the lowest current HP and lowest unit ID on a tie. The
first eligible target yields exactly one `Command::StunningBlow` before Indomitable, Adrenaline, ordinary Attack,
Recover or approach. The policy applies to either faction; a faction needs the existing capability flag to cast.
When no eligible target remains, the previous planner runs unchanged. In particular, a stunned or effectively
Indomitable defender remains eligible for the ordinary Attack policy, and a TwoHex actor still needs an adjacent
target for Stunning Blow while its ordinary Attack keeps distance-two reach.

Target filtering, HP ordering and priority are **APPROXIMATION** choices inspired by the catalog, not recovered
original-AI behavior. The skill remains the T62 headless approximation: 6 AP and 25 fatigue, ordinary melee hit
chance and damage, with stun applied to a surviving hit and no stun on a miss. Existing skill-fidelity limitations,
including no mastery or weapon model, remain. The policy reads no RNG, and `plan_turn` remains pure and returns one
command while the battle runs. Since the 6-AP skill exceeds the 2-AP minimum used by the structural progress bound,
normal 9-AP turns with at most one 4-AP Berserk refund retain the conservative maximum of seven continuing commands
before Recover or EndTurn.

Focused tests in `crates/bb_rules/tests/stunning_blow_ai.rs` cover both factions, HP and ID order, status filtering,
shared-query affordability limits and overflow, old-policy fallbacks, Withdraw/Flee priority, Indomitable/Adrenaline
priority, TwoHex distance-two fallback, pure repeated planning, hit and miss execution, immediate ZoC suppression,
replanning after a stun and scheduled-turn completion with one Berserk refund. `stunning_blow_loadout.rs` keeps twice-run
replay equality and actual `headless_smoke` agreement for Stunning-Blow-only and combined loadouts on Flat, Hills and
Swamp; it now pins skill-use counts as well as measured outcomes. The focused encounter proves actual AI casting.

The T65 table above is historical evidence from before AI selection. Newly measured T66 seed-1 outcomes
`(outcome, reason, rounds, hash, Stunning Blow commands)` are:

| Terrain | Stunning Blow only | Combined loadout |
| --- | --- | --- |
| Flat | Victory, Elimination, 4, `f5ac285bcd8e73a7`, 6 | Victory, Elimination, 3, `10c9c26524465fb3`, 3 |
| Hills | Victory, Elimination, 5, `c8a111dbba69e14c`, 8 | Victory, Escape, 5, `4d028e67e2923a58`, 7 |
| Swamp | Victory, Elimination, 5, `8ea1532833f3856b`, 4 | Victory, Escape, 5, `89dc5a1d5aeda147`, 6 |

Actual T66 validation: `cargo test -p bb_rules --test stunning_blow_ai --locked --offline` passed (7 tests);
`cargo test -p bb_rules --test stunning_blow_loadout --locked --offline` passed (4 tests);
`cargo test --workspace --locked --offline` passed (1,055 tests, 0 failed);
`cargo build --workspace --locked --offline` succeeded; and `git diff --check` was clean. This is partial
UC-COMBAT-020/028 coverage and remains an intermediate milestone, not completion of `docs/source/**`.


## Killing Frenzy (T67; headless passive melee damage buff, APPROXIMATION, partial UC-COMBAT-055 coverage)

**Provenance.** UC-COMBAT-055 (public catalog copy, not runtime-confirmed) supplies only the *concept* of a
temporary damage buff gained by killing and its interaction with Berserk. Everything numeric or temporal below is an
explicit **APPROXIMATION** chosen for this slice, not a recovered value: the 25% bonus, the duration, the refresh
timing and the restriction of the trigger to paid melee kills. No original-runtime parity is claimed.

**State.** `Unit.has_killing_frenzy` (capability, default `false`) and `Unit.killing_frenzy_turns: u8` (counter, default
`0`). The bonus is effective (`Unit::is_killing_frenzy_active`) only for a present, capable attacker with a nonzero
counter. At T67, no scenario, launch option, AI policy or native control assigned or showed the capability; tests
granted it programmatically. T68 adds an opt-in player launch assignment and native restart persistence below.
Default constructors and scenarios still start without the capability and with a zero counter, so accepted default
scenarios and replay hashes remain unchanged.

**Trigger.** After a lethal paid `Command::Attack` or `Command::StunningBlow` has been resolved (death, ally-death and
kill morale, and the Berserk refund included) and before the outcome check, a present, capable attacker's counter is
set to `rules::KILLING_FRENZY_TURNS = 2`. This refreshes an existing effect to 2; it never stacks. The triggering
attack itself used the pre-kill modifier. Misses, surviving targets and Nine Lives interceptions do not activate or
refresh it. Opportunity-attack kills do not activate or refresh it in this bounded implementation (they never reach
the trigger), although an already active Frenzy still modifies an opportunity attack's damage. No event, command or
RNG draw is added.

**Lifecycle.** Inside `begin_turn`'s existing `turn_started` guard, the counter is saturating-decremented once before
`TurnStarted`. `Wait` returns and `begin_round` alone do not decrement it, nor do other units' actions. A kill therefore
grants the remainder of the current turn plus the next fresh turn, and the effect expires at the following fresh
entry. Stunned entries and `EndRound`-skipped entries are fresh entries and age it too. Units without the capability
age a stray counter the same way.

**Arithmetic.** `rules::KILLING_FRENZY_BONUS_PERCENT = 25`. A base damage value `b` becomes
`floor(b * 125 / 100)`, computed in widened (`u64`) arithmetic and saturating at `u32::MAX`; then the existing
Indomitable halving and armour processing apply (so base 5 gives 6, then 3 against an effective Indomitable stance).
The base range `dmg_min..=dmg_max` is sampled unmodified (same RNG draws as before) and the sampled value is
transformed. `plan_melee` applies the same helper to both preview endpoints, so `preview_attack` and
`preview_stunning_blow` match the `Attacked.dmg` of the executed command. Zero stays zero; base 1 and 3 stay 1 and 3.

**Hash.** `has_killing_frenzy` is flag bit 57 (all earlier bits stay in place). Nonzero counters are appended last, after
the elevation and terrain suffixes, as `KILLING_FRENZY_HASH_TAG` (`0x4652_4e5a`), the entry count, then per unit with a
nonzero counter its unit ID and counter, in ascending unit order (lossless over the whole `u8` range). When every
counter is zero nothing is appended, so existing hashes are unchanged.

**T67 deferrals, qualified by T68 and T69.** The original milestone deferred launch assignment and AI use; T68 adds the
`--killing-frenzy` player loadout, and accepted AI melee commands receive the passive through the shared rules
execution. AI selection or activation policy remains absent. The indicator and log were both deferred at T67; T69 adds
the row marker below, while combat-log text, default scenario assignment, kill sources other than paid melee, equipment
and perk learning, and other damage sources remain deferred.

**Tests.** `crates/bb_rules/tests/killing_frenzy.rs` (20 tests) covers: defaults and unchanged accepted hashes; activation
by both paid melee commands for both factions with an unboosted killing blow and an immediately boosted follow-up;
refresh without stacking; no activation on misses, survivors, Nine Lives and opportunity kills; a real Berserk refund
enabling the boosted follow-up; duration across fresh turns, Wait returns, round rollover, other units' actions,
`EndRound`-skipped and stunned entries; both preview APIs against actual `Attacked.dmg` across rounding, zero damage,
full-width `u32` ranges, saturation and Indomitable composition; an opportunity attack boosted without refresh; query
purity, rejected-command immutability and identical RNG consumption; and capability/counter hash distinctions
(every `u8` counter value) with an independent FNV mirror of the suffix.

**Actual validation.** `cargo test --workspace --locked --offline` passed (1,075 tests, 0 failed);
`cargo test --release --locked --offline -p bb_rules --test killing_frenzy` passed (20 tests); `git diff --check`
was clean; `Cargo.lock` is unchanged. Partial UC-COMBAT-055 coverage only: an intermediate milestone, not completion of
`docs/source/**`.

## Killing Frenzy launch option (T68; APPROXIMATION)

`LaunchArgs.killing_frenzy` defaults to `false`. The shared parser accepts the value-less, repeat-safe
`--killing-frenzy` in both binaries with `--seed`, Flat/Hills/Swamp and every existing option. `launch_battle_for`
assigns only `has_killing_frenzy` to player units; all counters start at zero, enemies remain unchanged, and the
terrain, RNG, queue, resources and other unit fields match the otherwise identical launch. This player-only launch
assignment is an **APPROXIMATION**, not perk learning or equipment. Compatibility constructors explicitly pass false.

The native `App` stores the selection and reproduces it through `launch_args`. `R` restarts the same seed and `N`
increments the seed; both use the existing restart path, preserve the loadout and clear the active counter by building
fresh units. The restart path also clears the log, error, confirmation and targeting state and keeps its existing
targeting click guard.

Examples accepted by both binaries:

    cargo run --offline -p bb_rules --bin headless_smoke -- --seed 1 --terrain hills --killing-frenzy
    cargo run --offline -p bb_game -- --seed 1 --terrain hills --killing-frenzy --berserk

A real paid melee kill activates the accepted T67 effect without manually setting its capability or counter. With
`--berserk`, a kill can refund the four AP spent by an ordinary attack and leave enough AP for a boosted follow-up.
AI receives the same passive automatically when its accepted melee command resolves; T68 adds no AI activation or
selection policy. The existing ordinary Attack damage row and Stunning Blow damage row use the shared rules previews,
show the boosted range while Frenzy is effective, and return to base damage after expiration. T68 added no new renderer
behavior or combat-log message; T69 adds the marker below, while combat-log text remains deferred.

`crates/bb_rules/tests/killing_frenzy_loadout.rs` covers parser defaults, repeats, order, all terrain presets and each
existing option; player-only assignment and zero counters; a parsed launch executing a real lethal attack and Berserk
refund followed by a boosted attack; automatic activation from an AI-planned attack; and repeatable bounded seed-1
replays. `crates/bb_game/src/main.rs` verifies App propagation and both restart keys after real activation.
`crates/bb_game/src/render.rs` checks the existing Attack and Stunning Blow rows against rules previews before the kill,
while active, and after the real fresh-turn expiration path, including scene purity.

Seed-1 `headless_smoke` results (each executable run internally repeats and checks determinism):

| Loadout | Flat | Hills | Swamp |
| --- | --- | --- | --- |
| `--killing-frenzy` | Victory / Escape, 3 rounds, `fff5a4e0df8dd8cc` | Victory / Escape, 4 rounds, `77541ad9bf3e9fe7` | Victory / Elimination, 4 rounds, `8d539a2492468f5a` |
| All 12 capability options | Victory / Elimination, 3 rounds, `619454641aa36990` | Victory / Escape, 5 rounds, `40f4469b553e7548` | Victory / Escape, 5 rounds, `85d88af3d3380288` |

At the T68 snapshot, the dedicated indicator and log text were deferred. T69 adds the marker below; combat-log text,
default scenario assignment, equipment, perk learning and kill sources other than paid Attack or Stunning Blow remain
deferred. This is partial UC-COMBAT-055 coverage and an intermediate milestone, not completion of `docs/source/**`.

T68 validation: the new rules loadout suite passed (6 tests), including six executions of the real `headless_smoke` binary;
the App restart test and scene-preview test each passed;
`cargo test --workspace --locked --offline`, `cargo build --workspace --locked --offline` and `git diff --check` passed.
The binary output matched the six seed-1 in-process replay measurements above; each replay repeat also matched its full command and event sequences.
`Cargo.lock` and `docs/source/**` are unchanged.

## Killing Frenzy native row marker (T69; APPROXIMATION)

Every effectively buffed unit of either faction shows `FRENZY`, using only
`Unit::is_killing_frenzy_active()` as the visibility predicate. The scale-1 label starts at x=1134 and 21 px below
the unit-row top, on the stock HP/armour line. Its 35 px text ends at x=1169, eight pixels before the widest
`ADAPT 4294967295` label starts at x=1177. This uses the free gap after stock HP/armour text without changing any
existing row content or indicator positions. The label never displays the internal duration counter.

The marker is independent of selection and active turn and stays visible during targeting and after battle completion
as long as the rules predicate remains true. A zero counter, missing capability, death or escape suppresses it,
including a stale counter in any of those states. It adds only read-only scene text and consumes no RNG. Marker wording,
colour and placement are **APPROXIMATION** presentation; the UC-COMBAT-055 buff itself remains partial and is not
original-runtime-confirmed. Combat-log text, other kill sources, default assignment, equipment and perk learning remain
deferred.

Renderer tests cover both factions and all six rows, counters 0/1/2/255, active and inactive selection, dead/escaped/
incapable exclusions, supported glyphs and exact rendered geometry, maximum stock HP/armour resources, maximum ADAPT,
simultaneous existing indicators, real-kill activation at counters 2 and 1 and fresh-turn expiration, ordinary and
targeting scenes, completed battles, Flat/Hills/Swamp, and state/queue/hash/RNG purity. Native visual inspection was not
performed; validation was headless.

Actual T69 validation: `cargo test --workspace --locked --offline` passed (1,087 tests, 0 failed; about 11 seconds);
`cargo build --workspace --locked --offline` succeeded; `git diff --check` was clean. Native visual inspection was not
performed because neither `DISPLAY` nor `WAYLAND_DISPLAY` was set. `Cargo.lock` and `docs/source/**` are unchanged.
