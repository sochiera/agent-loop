# Implemented state

This is not a requirement document. Source rows live under `docs/source/`.

The committed baseline plus layer A is a 3-versus-3 hex skirmish: deterministic rules in `crates/bb_rules` and a winit/wgpu window in `crates/bb_game`. README at the layer A digest states the slice limits. Recover has a RECONSTRUCTED fatigue-halving effect and catalog-stated 9 AP cost/whole-turn behavior, with no original-runtime confirmation (see "Recover action"). Movement, attack, incoming fatigue, regen, hit chance, armour, initiative, Wait, and EndTurn are slice behavior. Zones of control and opportunity attacks are basic slice behavior (see below). A non-fleeing morale state foundation modifies melee skill and defense. Scenario units start Steady and can now deteriorate through a Resolve check after surviving a 15+ HP melee hit (see below); a failed check at Breaking makes a unit Fleeing, which removes player control and ends in an automatic escape (see "Fleeing and escape"); a side-panel label shows present units' current state and melee-stat scaling percentage. A Fleeing unit away from map edges and enemies can recover to Breaking by a turn-start self-rally check (see "Self-rally at turn start"). A melee death triggers approximate Resolve checks for the fallen unit's present allies (see "Ally-death morale checks"). A party-retreat order sends edge units out at once and the rest on automatic withdrawing turns; the native window launches it through a Retreat button and the `V` key behind a mandatory confirmation (see "Party retreat order"). The killer of a hostile unit in melee takes an approximate positive Resolve check that can raise its morale one step; at the map edge its effective chance is zero (see "Kill morale checks"). An ordinary move onto an outermost-ring hex lowers a non-Fleeing, non-retreating mover directly to Breaking without extracting it (see "Map-edge movement penalty"). Other morale triggers, Rally the Troops and other positive transitions, tooltips and map indicators are absent. There is no campaign, company roster, inventory, settlement, or save system in this tree.

## Basic melee surround — partial UC-COMBAT-009 coverage

`BattleState::hit_chance` in `crates/bb_rules/src/rules.rs` adds
`SURROUND_BONUS_PER_ADDITIONAL_ALLY` (5 percentage points) for each living ally after the first
living unit on the attacker's side adjacent to the target. It applies only when the attacker is
living and adjacent to a living hostile target. The bonus is computed from current positions and
alive flags on each query, then added before the existing 5–95 clamp:

`clamp(effective_melee_skill - effective_defense + 5 * (living same-side units adjacent to target - 1), 5, 95)`

*(Qualification, later task: for a capable attacker `hit_chance` now also adds the optional Fast Adaptation bonus before the same clamp; see "Fast Adaptation" below. Without the capability this formula is unchanged.)*

Effective melee skill and defense equal the base values while a unit is Steady (for defense, only while the
unit lacks the optional Dodge capability, whose bonus is added on top; see "Dodge"). For the existing
Steady player/enemy stats, valid one-, two- and three-attacker fixtures yield 55%, 60% and 65%. Enemy attackers use the same rule. `Command::Attack` and the existing preview both use
`hit_chance`; previews do not mutate state or consume RNG. This is **APPROXIMATION** based on the
[catalog description of UC-COMBAT-009](../../source/10-tactical.md), without original-runtime
confirmation. It is partial coverage only. Backstabber doubling, Underdog behavior, creature-specific
surround values and ranged exceptions are explicitly deferred.

Focused headless tests in [`crates/bb_rules/tests/rules.rs`](../../../crates/bb_rules/tests/rules.rs)
cover one/two/three attackers, both sides, direction independence, excluded units, nonadjacent
attackers, both clamp ends (including a negative base chance), movement/death refresh, preview
purity and `Event::Attacked.chance` agreement.

Validation when basic surround was added: `cargo test --workspace --locked --offline` passed, with 30 tests
passed and 0 failed (2 `bb_rules` unit, 26 `bb_rules` integration, 1 `bb_game` unit and 1 `bb_game`
integration test).

## Melee elevation — partial UC-COMBAT-010 coverage (opt-in hills skirmish in the native window)

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

The catalog text of [UC-COMBAT-010](../../source/10-tactical.md) states the 0–3 range, +10 downhill and
-10 per level uphill; this is not runtime-confirmed. The storage, error type, the "any downward
difference is +10" reading, the adjacency gating and the hash encoding are **APPROXIMATION**.
**Limits:** the default scenario is flat. Nonzero heights are configured only through the opt-in `hills` preset
(see "Hills skirmish and height overlay"); there is no terrain editor, procedural generation, camera
elevation or per-terrain art. Climbing costs extra AP (see "Uphill movement AP cost"); ranged-attack
effects and extra vision are deferred.

Tests: [`crates/bb_rules/tests/elevation.rs`](../../../crates/bb_rules/tests/elevation.rs) (22) cover flat defaults,
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

Provenance: the catalog rows [UC-COMBAT-005 and UC-COMBAT-010](../../source/10-tactical.md) name climbing as an AP cost. The
uphill-only application, the surcharge of one AP per level climbed, unchanged fatigue, charging the
attempted step before reactions, and retaining BFS routing are **APPROXIMATION** slice choices without
original-runtime confirmation. The default scenario stays flat (its costs, events and the seed 1 smoke
result are unchanged); the opt-in hills preset makes these costs visible in the native window (see
"Hills skirmish and height overlay").

Tests: [`crates/bb_rules/tests/elevation_movement.rs`](../../../crates/bb_rules/tests/elevation_movement.rs) (18) cover all 16 height pairs for both sides,
multi-step ascents and descents, exact-budget and one-short rejection, preview/`reachable`/execution
agreement and ordering over a hilly map, the retained BFS route, checked fatigue boundaries (exact cap,
`u32::MAX`) and precedence, missed, stopping, lethal and fatigue-interrupted reactions, `Flee` and
`Withdraw` (sufficient/insufficient budget, immediate arrival, zero-AP edge escape, stopped step), AI
prefix and no-step fallback with command validation, and state/RNG purity. In `elevation.rs` only the
opportunity-attack test's AP expectation changed (a height-3 destination now costs 5 AP).

### Hills skirmish and height overlay (T23)

Implemented:

- `scenario::TerrainPreset::{Flat, Hills}` (`parse`, `height_at`) and `scenario::battle(seed, preset)`
  were introduced at T23. T50 added `Swamp`; T52 added its lowercase CLI spelling.
  `Flat` equals `BattleState::new(seed)` (units, RNG, queue, hash). `Hills` changes only elevations, set via
  `ElevationMap::set` on all 61 map hexes, obstacles included, passability unchanged, no RNG: height 2 where
  `q == 0`, 1 where `q` is -1 or 1, 0 elsewhere, and (0,-1), (0,1) overridden to 3.
- At T23, `scenario::parse_args` was the shared `--seed N` / `--terrain flat|hills` parser of
  `headless_smoke` and `bb_game` (defaults seed 1, flat; unknown arguments ignored; last repeat wins). Its
  then-current missing/invalid terrain diagnostic was `--terrain needs flat or hills`; T52 changes it to
  `--terrain needs flat, hills or swamp` while retaining status 2 and seed validation.
- `App` stores `terrain` and `height_overlay`. The current shared launch configuration rebuilds on `R` with
  the same seed and on `N` with `seed + 1`; pending confirmation, log, error and click guard are cleared as
  before. `H`/`h` toggles `height_overlay` (initially on for Hills, off for Flat and Swamp; kept across
  `R`/`N`; independent of `Z`; works on
  enemy turns and after the battle; consumed while either confirmation is open). `App::new(seed)` remains
  the flat constructor (test-only), `App::with_terrain` the real one.
- `render::Scene.height_overlay`: each map hex gets a 7x9 px badge (digit at scale 1) at offset (-28.25,
  -16.25) from its centre, read from `state.elevations`. The box is inside the hex outline, more than 22 px
  from the centre (unit rings), left of the AP-cost label and name text, above the health/armour bars and
  clear of the ZoC marker; it is drawn with the hex, before tokens and labels, and the modal stays last.
  A top-left legend (`H HEIGHT NUMBERS ON/OFF`, `BADGE = HEIGHT 0-3`, `UPHILL +1 AP PER LEVEL`) sits above
  the map's top row; the bottom shortcut hint is unchanged (no room left). Movement costs (`reachable`,
  `preview_move`) and `HIT n%` (`hit_chance`) are drawn straight from the rules; the client has no
  elevation arithmetic and never mutates `BattleState` or draws RNG.
- The outcome banner (VICTORY/DEFEAT/RETREAT, reason, restart hint; text unchanged, APPROXIMATION layout)
  moved from the map centre to the preview-box strip below the map (16,584,860x72), which is free after the
  battle (no preview), so no height badge is covered once the battle is over. A renderer test checks that
  nothing drawn after any badge overlaps it, for every outcome/reason.

Provenance: elevation rules cite UC-COMBAT-010 (catalog-stated, not runtime-confirmed); the badge and
legend loosely follow the UC-UI-003/006 HUD/overlay rows without claiming original-UI parity. The
hand-authored hills layout, the `H` shortcut, colours, badge geometry and legend wording are
**APPROXIMATION**.

Not implemented: terrain editor, procedural generation, camera elevation, height-specific art, in-window
terrain selection (restart the program with `--terrain`), weighted pathfinding (BFS routing still ignores AP).

Tests: `crates/bb_rules/tests/terrain_presets.rs` (9: exact heights on all 61 hexes, range, obstacles,
canonical Flat equivalence, unchanged units and RNG, determinism and hash sensitivity, Hills AI replays for
five seeds twice with identical events/state and termination within the smoke bound, argument parsing, the
`headless_smoke` binary output and exit status); `bb_game` `main.rs` App tests (preset selection, initial
preference, H purity, independence from Z, `R`/`N` persistence, modal isolation, enemy turn / battle over,
draw purity) and `render.rs` tests (heights 0-3, live changes, every hex including occupied/blocked, off/on
visibility, enemy turn/battle over/modal, purity, supported glyphs, legend placement, badge geometry
against the hex outline, the 22 px ring, AP labels, name text, bars and ZoC markers on all 61 hexes, edge
hexes with both overlays, a Hills fixture with rules-derived uphill costs and elevation-adjusted HIT
percentages).

Validation: `cargo test --workspace --locked --offline` passed (410 tests; 0 failed); `cargo build
--workspace --locked --offline` succeeded; `headless_smoke --seed 1` (flat) printed
`OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=dfce9b8d74bcaf93` (unchanged) and `--seed 1 --terrain hills`
printed `OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=d0b704f0294173be`. Visual inspection of the native
Hills view was unavailable (no `DISPLAY`/`WAYLAND_DISPLAY`, no Xvfb); presentation is verified by
renderer geometry tests only.

## Fatigue from incoming melee attacks — partial UC-COMBAT-006 coverage

After a valid `Command::Attack` resolves its hit or miss, only the target receives incoming
fatigue: `DEFENDER_HIT_FATIGUE_GAIN` (5) when `Event::Attacked.hit` is true, or
`DEFENDER_MISS_FATIGUE_GAIN` (2) otherwise. The value is added with saturating arithmetic and
capped at the target's `fatigue_max`. It is applied after hit damage is resolved but before the
existing death handling, so a lethal hit (or a hit intercepted by Nine Lives) also adds fatigue. Armour absorption does not change the
hit result or gain. Defender AP, available fatigue capacity and active-turn status are not checked;
a target at maximum fatigue remains attackable. No extra RNG is consumed and attacker validation,
costs, hit chance, damage and turn handling are unchanged. The updated value uses the existing
fatigue field, so later action eligibility, initiative ordering and turn-start regeneration read
it through the existing rules.

This is an **APPROXIMATION** based on the [UC-COMBAT-006 catalog row and scenario](../../source/10-tactical.md),
which states +5 for being hit and +2 for dodge/block. There is no original-runtime confirmation.
The slice has no separate dodge and block outcomes, so it maps every generic melee miss to the
catalog's dodge/block fatigue gain. Undead fatigue immunity and distinctions or modifiers from
equipment, shields, statuses and perks are deferred.

Focused headless tests in
[`crates/bb_rules/tests/incoming_attack_fatigue.rs`](../../../crates/bb_rules/tests/incoming_attack_fatigue.rs)
cover hit/miss gains for both defending sides (including defenders whose turn has passed),
attacker costs and unchanged RNG draws, armour-absorbed and lethal hits, near-cap and maximum
fatigue, action eligibility after accumulating fatigue, updated turn-start regeneration, invalid
attack state/RNG purity, and next-round initiative ordering.

Current validation for incoming fatigue: `cargo test --workspace --locked --offline` passed, with
38 tests passed and 0 failed (2 `bb_rules` unit, 34 `bb_rules` integration, 1 `bb_game` unit and 1
`bb_game` integration test). This remains partial catalog coverage; other UC-COMBAT-006 behavior
and the broader source catalog are not implemented.

## Melee zones of control and opportunity attacks — partial UC-COMBAT-007/008 coverage

Implemented in `crates/bb_rules/src/rules.rs`. Every living unit exerts ZoC on adjacent hexes;
dead units and allies never control a mover. `enemy_controllers_at(unit, hex)` returns living
opposing adjacent unit ids, ascending, read-only and RNG-free.

`Command::Move` keeps its deterministic BFS route and the up-front full-path AP/fatigue check
(errors leave state and RNG untouched), then executes step by step. Per step: (1) the step's
full AP cost (the destination terrain's AP, `MOVE_AP` on Ground, plus any uphill surcharge, see "Uphill movement AP cost" and "Ground, Swamp and Snow terrain (T48/T71)") and the destination's fatigue (`rules::terrain_move_fatigue`: Ground `MOVE_FATIGUE`, or `PATHFINDER_MOVE_FATIGUE` for a Pathfinder unit, see "Pathfinder"; Swamp and Snow twice that, see "Ground, Swamp and Snow terrain (T48/T71)") are charged (a later step that incoming fatigue/AP can no longer pay stops
the move with `Ok`, retaining completed movement); (2) controllers of the *current* hex attack in
ascending id order via the shared `resolve_attack` helper (the same hit chance incl. surround, RNG
draws, damage, armour, incoming defender fatigue and death handling as `Command::Attack`), paying
no AP or fatigue and ignoring turn/budget; (3) if any reaction hit, the mover stays on the
departure hex, otherwise the step completes. Entering adjacency alone triggers nothing; departing
triggers even if the destination stays adjacent. A lethal reaction (not a Nine Lives interception, which survives but still interrupts the move and lets the remaining controllers react) stops the remaining controllers,
removes the mover from occupancy and queue, checks elimination, and otherwise advances through the
shared `advance_turn` (next unit, next round, or the time-limit defeat, with no turn start after
battle termination). `Event::Moved` carries only completed steps and is flushed before later
reactions and before any map-edge morale event (see "Map-edge movement penalty"); no empty path is ever
emitted; a move without reactions or edge-morale changes is unchanged (one `Moved` with the full path, same
costs). Existing `Attacked`/`Died` events are reused.

`reachable` is still a pure geometric/budget preview (AP costs include uphill surcharges) that ignores ZoC; reactions can prevent
arrival. Risk-aware routing is not implemented.

Provenance: **APPROXIMATION** from the [UC-COMBAT-007/008 catalog rows](../../source/10-tactical.md);
no original-runtime confirmation. Slice choices: all living units count as melee-capable, reactions
resolve in ascending id order, and departure means leaving any enemy-controlled hex. Fleeing units no
longer exert ZoC (see "Fleeing and escape"), and since T61 neither do stunned units (see "Stun effect"). Deferred:
ranged/asleep/entangled exemptions, smoke, net/root, Alp/Hex and other creature
exceptions, Lunge, Spearwall (Rotation has an opt-in native control: see "Rotation"; Indomitable has an opt-in native button, marker and guarded headless activation policy; its stun immunity is honoured by the T61 `apply_stun` effect API while the stance is effective (see "Stun effect"), while knock-back/grab immunities and broader stance tactics remain deferred (the earlier "no immunities" wording is historical): see "Indomitable"; Footwork is an opt-in one-hex step with native destination targeting and no passive ZoC immunity: see "Footwork"), equipment/status/perk effects, path-risk UI,
and voluntary retreat (superseded: see "Party retreat order", which now has a native control).

### Enemy control overlay — partial UC-COMBAT-007 and UC-UI-006 coverage

The native client has an initially disabled `Z` preference. While enabled on a living active
player unit's turn, each non-obstacle map hex with a nonempty
`BattleState::enemy_controllers_at(selected, hex)` result receives one corner `!` marker. The
selected unit's current hex is included when controlled. The rule query remains the authority;
the client does not copy its eligibility rules. The markers hide on enemy turns, after battle
termination, and without the matching active player selection. The preference remains enabled
through restart and new-seed actions, so markers resume on the next player turn.

The marker uses a small orange corner badge and dark `!` glyph, with `Z ZOC OVERLAY ON/OFF` and a
short legend in the side panel. Leaving a marked hex may trigger free attacks. Markers are warnings,
not blocked-tile fills: entering enemy adjacency remains legal and `reachable` remains the existing
geometric/budget preview. The complete marker geometry stays inside the hex outline and more than
22 pixels from its centre, clearing the active-unit ring; it also stays away from centered tokens
and AP labels. This is a slice UI **APPROXIMATION** based on
[UC-COMBAT-007](../../source/10-tactical.md) and [UC-UI-006](../../source/60-ui.md); it does not
claim original-UI parity.

Headless tests in [`crates/bb_game/src/main.rs`](../../../crates/bb_game/src/main.rs) cover the
default-off preference, `Z` toggling, state/RNG purity and persistence across restart and new seed.
Tests in [`crates/bb_game/src/render.rs`](../../../crates/bb_game/src/render.rs) cover controlled
hex selection (including the selected tile), dead-enemy/allied exclusion, overlapping controllers,
obstacles and map-edge tiles, visibility gates, and corner-marker geometry when enabled versus
disabled. A geometry regression checks the marker against the hex outline and unit-ring radius; the
scene test also checks that reachable green fills and AP-label vertices remain present and
unchanged. These tests build the CPU scene without a window or GPU.

Current validation for the overlay: `cargo test --workspace --locked --offline` passed (71 tests,
0 failed), and `cargo build --workspace --locked --offline` passed. Native visual inspection was not
performed because this environment has no display session.

Focused tests: [`crates/bb_rules/tests/zone_of_control.rs`](../../../crates/bb_rules/tests/zone_of_control.rs)
(22 tests, fixed seed 1 unless stated; rolls mirrored from the SplitMix64 stream). The accepted
supporting-ally surround test in [`tests/rules.rs`](../../../crates/bb_rules/tests/rules.rs) was
adapted to seed 3, where both reactions of its P2 move miss, because its move now departs a
controlled hex; its purpose is unchanged.

Current validation: `cargo test --workspace --locked --offline` passed with 60 tests passed and 0
failed (2 `bb_rules` unit, 56 `bb_rules` integration, 1 `bb_game` unit, 1 `bb_game` integration);
`cargo run --locked --offline -p bb_rules --bin headless_smoke -- --seed 1` printed
`OUTCOME=Victory REASON=Elimination ROUNDS=6 HASH=892ea5d6c8f754c6`. `cargo fmt --all --check`
remains failing on pre-existing unformatted files and was not applied. Partial catalog coverage only.

### Movement preview — partial UC-COMBAT-005/007/008 coverage

`BattleState::preview_move(unit, destination) -> Result<MovePreview, RuleError>` is read-only (no
RNG, no mutation). `Command::Move` and the preview share the private `ensure_active` (battle over,
then active unit) and `plan_move` (BFS route with its tie-breaking, `Unreachable`, `NotEnoughAp`,
then checked-fatigue `TooFatigued`), so routes, costs and errors cannot drift; execution still
uses `walk`. `MovePreview` contains `path`, `ap_cost`, `fatigue_cost`, `ap_remaining`,
`attack_budget_available`, `first_threat` and `first_edge_penalty`: the path excludes the start and
includes the destination; costs describe the whole planned BFS route before reaction effects (uphill
surcharges included, see "Uphill movement AP cost"); `ap_remaining` is current AP minus that full
route cost. The budget flag is true when remaining AP covers `ATTACK_AP` and current fatigue plus
planned movement fatigue plus `ATTACK_FATIGUE` fits `fatigue_max`, using checked arithmetic. It
describes the nominal budget for one basic melee attack before reactions; it does not require or
establish an adjacent target and does not predict whether an attack will be possible after movement
resolves. UC-COMBAT-005 names the remaining attack AP indicator; the combined AP/fatigue budget and
its presentation are **APPROXIMATION**s because the slice attack costs are not confirmed original
rules.
`first_threat: Option<MoveThreat { departure, controllers }>` is the first departure among the
starting hex and all path hexes except the destination with a nonempty `enemy_controllers_at`
(ascending ids). Entering adjacency only at the destination yields no threat.
`first_edge_penalty: Option<Hex>` is the first arrival hex on the path (intermediate hexes included;
the start only if it reappears as an arrival) where the mover's current morale would take the map-edge
penalty if reached (see "Map-edge movement penalty"); `None` when no hex qualifies or the mover is
already Breaking. It is a conditional route consequence from current state, not a prediction of
arrival or of reaction outcomes: reactions are not simulated and an earlier threat does not hide it.
`walk` and `preview_move` share the private `edge_penalty_applies` helper (edge hex, present, no
retreat intent, Confident/Steady/Wavering); execution evaluates actual state at arrival, the preview
current state. Route, costs, errors and `first_threat` are unchanged.

Semantics: the preview names potential reactions only. It does not predict hits, damage, survival
or arrival. Arrival is conditional: reactions can stop the walk on a shorter route, add defender
fatigue, or kill the mover. Planned costs exclude those effects; an uncontested move spends exactly
the previewed path and costs. Confidence labels are unchanged (**APPROXIMATION** of
[UC-COMBAT-005/007/008](../../source/10-tactical.md)). Risk-aware routing, detours, probabilities
and move confirmation remain deferred.

Client: hovering a destination accepted by `preview_move` on the active player's turn draws a box
(below the map, above the error bar, left of the side panel) with `MOVE AP n FAT n LEFT n MELEE BUDGET YES/NO`;
all values and the budget state come directly from the rules preview. The compact first row preserves
the planned costs while making room for remaining AP and the melee budget.
With a first threat it adds `! WARNING: LEAVING (q,r) - n CONTROLLER(S)` and a note that reaction
attacks may stop the move and add fatigue; a last line says planned costs exclude reaction effects.
With `first_edge_penalty` it adds `! IF REACHED: EDGE (q,r) LOWERS MORALE TO BREAKING, NOT EXTRACTED.`
read from the rules field (no edge detection in `bb_game`); with both warnings the two note rows merge
into `REACTIONS MAY STOP MOVE, ADD FATIGUE, EFFECTS EXCLUDED FROM COSTS.` so the four-row box holds
both warnings and the reaction-effects qualification. The budget indicator is an **APPROXIMATION**
of one basic melee attack's AP/fatigue budget: it excludes reaction effects and does not establish
target availability. It does not depend on the `Z` overlay and is hidden for invalid destinations, attack targets,
missing/inactive/enemy/dead selections, enemy turns and completed battles. The visual presentation
is an **APPROXIMATION**, as is the edge warning (based on the accepted UC-COMBAT-048 behaviour);
no original-UI parity is claimed.

Focused tests: [`crates/bb_rules/tests/movement_preview.rs`](../../../crates/bb_rules/tests/movement_preview.rs)
(10 original tests plus budget tests for flat/uphill routes below, at and above `ATTACK_AP`, insufficient
fatigue, exact fatigue cap, attack-fatigue overflow, no-target previews, nominal threatened previews,
and post-move Attack acceptance/rejection; plus 7 edge tests (both sides, all morale states, initial edge placement alone,
edge-to-edge and intermediate arrivals, agreement with the first executed `EdgeMoraleChanged`, a
stopped or killed threatened move still previewing the edge, unchanged route/costs/threat/errors,
exempt automatic movement); the original 10: the lane fixture mover (-2,-2), enemy (1,-3), destination (2,-2) first threatening
departure (0,-2); repeatability and hash/RNG purity; start-hex threat; safe final-step entry; threat
after safe steps; multiple, dead and allied controllers; refresh after enemy movement or death;
`BattleOver`/`NotActiveUnit`/`Unreachable`/`NotEnoughAp`/`TooFatigued` parity with `Move` on clones
including `u32::MAX` overflow and exact-cap; uncontested and threatened execution). Renderer tests
in [`crates/bb_game/src/render.rs`](../../../crates/bb_game/src/render.rs) (`preview_*`) compare
emitted text geometry for costs, remaining AP, both budget states, a Hills cost fixture, warning count
and notes, the visibility gates, Z independence, purity, and box bounds against the map, error bar,
controls and side panel; the `edge_*`/combination tests add all four warning combinations,
conditional wording and coordinates, measured combined-warning geometry (negative coordinates,
three controllers) and a width sweep over every map hex using maximum `u32` costs, plus a check that
every preview-row character has a supported font glyph.

Edge-warning task validation: `cargo test --workspace --locked --offline` passed (341 tests, 0 failed),
`cargo build --workspace --locked --offline` succeeded, seed-1 headless smoke unchanged
(`OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=dfce9b8d74bcaf93`). Native visual inspection was **not**
performed (`DISPLAY`/`WAYLAND_DISPLAY` unset); layout is covered by the headless geometry tests only.
T24 validation: `cargo test --workspace --locked --offline --quiet` passed (414 tests, 0 failed), and
`cargo build --workspace --locked --offline` succeeded. Native visual inspection was not performed;
the four-row layout, bounds and supported glyphs are covered by renderer tests.

Historical validation (before the edge warning): `cargo test --workspace --locked --offline` passed (85 tests, 0 failed);
`cargo run --locked --offline -p bb_rules --bin headless_smoke -- --seed 1` printed
`OUTCOME=Victory REASON=Elimination ROUNDS=6 HASH=892ea5d6c8f754c6`. Native visual inspection was not
performed (no display session). Partial catalog coverage only; the mission is not complete.

## Overflow-safe fatigue affordability

Upfront Move validation, ordinary Attack validation and AI attack selection use checked addition
for fatigue affordability. Overflow means the action is unaffordable; equality with
`fatigue_max` remains legal. A rejected Move or Attack returns `TooFatigued` before changing state
or consuming RNG. AI skips an unaffordable Attack and follows its existing approach and EndTurn
policy. The previously accepted per-step movement `checked_add` guard remains unchanged.

Failing-first evidence: before production edits,
`cargo test --locked --offline -p bb_rules --test fatigue_affordability` exited 101 in debug
(3 failed, 2 passed). The Move regression panicked at `rules.rs:240:20`, ordinary Attack at
`rules.rs:257:20`, and AI selection at `ai.rs:19:52`, each with `attempt to add with overflow`.
The two exact-cap checks passed before the fix. The regression tests compare `state_hash` and RNG
for all three rejected-query cases. They also verify exact-cap Move and Attack succeed and end at
`u32::MAX`, and AI selects the lowest-HP adjacent target and executes an exact-cap Attack.

Focused tests: [`crates/bb_rules/tests/fatigue_affordability.rs`](../../../crates/bb_rules/tests/fatigue_affordability.rs)
(`upfront_move_overflow_is_rejected_without_panic_or_mutation`,
`ordinary_attack_overflow_is_rejected_without_panic_or_mutation`,
`ai_does_not_select_an_attack_when_fatigue_addition_overflows`,
`exact_cap_move_and_attack_remain_legal`,
`ai_selects_and_can_execute_an_affordable_exact_cap_attack`). After correction,
`cargo test --locked --offline -p bb_rules --test fatigue_affordability` passed (5/5),
`cargo test --release --locked --offline -p bb_rules --test fatigue_affordability` passed (5/5),
and `cargo test --workspace --locked --offline` passed (65 tests, 0 failed), including the
existing `step_affordability_check_cannot_overflow_at_a_u32_fatigue_maximum` regression.

This fixes slice validation robustness only. It does not confirm original-game behavior, promote
source fidelity, or establish coverage of the broader source catalog.

Historical accepted-base host checks, before this task: `cargo test --workspace --locked --offline`
exit 0 (22 tests across the non-empty suites) and `cargo build --workspace --locked --offline` exit
0. `cargo fmt --all --check` exit 1. `cargo clippy --workspace --locked --offline --all-targets -- -D warnings`
exit 101 with three inherited diagnostics in `ai.rs` and `hex.rs`. Details:
`docs/evidence/host-cargo.md`.

Headless and windowed runtime notes, if produced, stay in that evidence file. A rendered window is the slice runtime, not the original game.

## End Round (rules + native confirmation) — partial UC-COMBAT-017 / UC-UI-007 coverage

Implemented: `Command::EndRound { unit }` (player-only, `RuleError::NotPlayerUnit`) and the per-unit
`skip_turn` flag, in `crates/bb_rules`. Scheduled skipping semantics, labelled APPROXIMATION based on
UC-COMBAT-017 (catalog text, not runtime-confirmed):

- Precedence `BattleOver`, `NotActiveUnit`, `NotPlayerUnit`; rejections leave state and RNG unchanged.
- Marks living player units still in the queue (active unit and Wait-deferred players included; not
  finished players, not enemies), forfeits the active unit's AP, removes it and advances.
- At a marked player's own turn: the existing once-per-round refresh (with `TurnStarted`; none after a
  Wait), then AP 0 and automatic advance. Fatigue regeneration therefore uses the value at that moment.
  `EndTurn`, `Wait` and active-mover death all go through the same advancing helper.
- Enemies keep their order and ordinary commands; `EndRound` runs no AI and draws no RNG. Skipped players
  stay alive, attackable and keep opportunity attacks.
- Flags clear when the next round begins (before a new active unit is exposed). At the round limit the
  battle ends with `BattleEnded` and no new turn. `state_hash` uses bit 3 of the flag word; all-false
  states hash as before.

Native client (`crates/bb_game`, APPROXIMATION of the catalogued confirmation; look and wording are
slice placeholders with no original-UI parity claim): an **End Round** button in the side panel
(below the log) and the `X` key open a modal for a living active player unit in a running battle;
the button is drawn disabled otherwise. Opening, cancelling or ignoring the dialog never changes
`BattleState` or the RNG. The dialog stores actor, round and `state_hash`, shows the number of living
player units still queued (active and Wait-deferred included) and states that confirming forfeits the
remaining player actions this round while enemies still take their turns. `Confirm`/`Enter` confirms,
`Cancel`/`Esc` cancels (Esc does not quit while open). Confirm is placed away from the launch button;
repeated `X` or launch clicks cannot confirm. The modal consumes map clicks and all other shortcuts,
the AI is held while it is open, and clicks within 350 ms after it closes are swallowed (double-click
protection). Confirmation clears the intent, revalidates actor/round/hash/player control and submits
exactly one `Command::EndRound` through `App::run`; stale intent is discarded with an error message.
Restart and new seed clear the intent and keep the `Z` preference. Button/dialog rectangles are shared
between `render.rs` drawing and `layout.rs` hit testing, and UI clicks are routed before hex clicks.
Controls hint and README controls list `X END ROUND`. Dialog is opaque (no alpha blending in the pipeline).

The pending-intent mechanism was later generalized to carry Retreat as well (see "Party retreat order"). Still
not implemented: other confirmation flows of UC-UI-007 (dismiss, delete save, ...). A native
visual inspection of the dialog was not performed (no display in the validation environment). This is
partial coverage; the mission is incomplete.

Focused tests: [`crates/bb_rules/tests/end_round.rs`](../../../crates/bb_rules/tests/end_round.rs) (19 tests);
client: App tests in [`crates/bb_game/src/main.rs`](../../../crates/bb_game/src/main.rs), renderer tests in
`render.rs`, layout tests in `layout.rs` (headless, no window/GPU).
Validation: `cargo test --workspace --locked --offline` passed (123 tests, 0 failed; 104 before the native
confirmation task);
`cargo run --locked --offline -p bb_rules --bin headless_smoke -- --seed 1` printed
`OUTCOME=Victory REASON=Elimination ROUNDS=6 HASH=892ea5d6c8f754c6` (unchanged).

## Morale state modifiers — partial UC-COMBAT-018 coverage

`bb_rules` exports `Morale::{Steady, Confident, Wavering, Breaking}` (`Fleeing` was added later, see "Fleeing and escape") and stores it on each unit.
Effective melee skill and defense come from unchanged base `Stats` and one shared percentage mapping:
Steady 100%, Confident 110%, Wavering 90%, Breaking 80%. That percentage is morale scaling of the base
values, not every source of final defense: a Dodge-capable unit's `effective_defense` adds the (unscaled)
Dodge bonus afterwards (see "Dodge"). The calculation is
`floor(base * percentage / 100)` with widened integer arithmetic and a `u32::MAX` cap. Flooring and
saturation are slice **APPROXIMATION** choices without runtime confirmation.

`BattleState::hit_chance` subtracts the target's effective defense (including its optional Dodge bonus) from the attacker's effective melee
skill, then adds the existing surround bonus and applies the existing 5–95 clamp. *(Later: the optional Fast Adaptation bonus of a capable attacker is added in the same sum before that clamp; see "Fast Adaptation".)* Ordinary attacks,
opportunity attacks and the existing native hit preview share this method. Morale queries do not
mutate state or consume RNG. The state hash stores explicit stable morale codes (Steady 0, Confident
1, Wavering 2, Breaking 3) in flag bits above the existing alive/waited/turn-started/skip-turn bits;
all-Steady hashes remain unchanged.

This is partial **APPROXIMATION** coverage based on the [UC-COMBAT-018 catalog row](../../source/10-tactical.md),
with no original-runtime confirmation. When this section was written the live scenario was entirely
Steady and nothing changed morale automatically; the next section supersedes that, and "Ally-death
morale checks" later implements the ally-death trigger (the "ally-death deferred" wording of this
section is historical; "Kill morale checks" later adds the killer's positive check). Mood, surrounding, fear and propagation checks, Unbreakable, ranged
effects, tooltips and map indicators remain deferred. The partial native morale panel label is documented below.

Focused tests in [`crates/bb_rules/tests/morale_modifiers.rs`](../../../crates/bb_rules/tests/morale_modifiers.rs)
cover default Steady state, all four values and odd-value flooring, both-side modifiers, base-stat
preservation, zero and large values, surround/clamp order, shared attack/reaction chance, query purity,
determinism, persistence, hash compatibility and `skip_turn` coexistence. Its lethal-hit fixture now
sets every unit to Resolve 100, so the ally-death checks a death draws always pass and morale stays
unchanged (original purpose preserved).

Validation: `cargo test --workspace --locked --offline` passed (133 tests, 0 failed), and
`cargo run --locked --offline -p bb_rules --bin headless_smoke -- --seed 1` printed
`OUTCOME=Victory REASON=Elimination ROUNDS=6 HASH=892ea5d6c8f754c6` (unchanged at that time).

## Resolve-based morale checks — partial UC-COMBAT-018 coverage

Implemented in `crates/bb_rules/src/rules.rs` (`check_morale`, called from the shared `resolve_attack`),
`unit.rs` (`Stats.resolve`, `Morale::lowered`), `scenario.rs` (`DEFAULT_RESOLVE`) and `lib.rs`
(`Event::MoraleChecked`). Provenance stays separate: the 15 HP threshold is traced to the
UC-COMBAT-018 catalog text ("15+ HP damage"); everything numeric below is **APPROXIMATION**.

- **Resolve:** `Stats.resolve`, unmodified base attribute; `DEFAULT_RESOLVE = 50` for all scenario
  units (invented value).
- **Trigger:** `MORALE_CHECK_HP_LOSS_THRESHOLD = 15`. One check when an attack hits, the defender
  survives and the HP actually lost is at least 15. None for misses, subthreshold loss or armour damage
  alone; a lethal hit gives the dead victim no heavy-hit check (`Attacked`, `Died`; a Nine Lives interception is not lethal and is checked on its actual HP loss, see "Nine Lives"; its surviving allies
  are checked by "Ally-death morale checks"). Ordinary and opportunity attacks share the code.
- **Formula (approximate):** `chance = min(resolve, 100)`; `roll = next_u64() % 100`; success =
  `roll < chance`. One extra draw after damage resolution per qualifying check, also at Breaking.
- **Result:** success keeps morale. Failure: Confident → Steady → Wavering → Breaking. *(Historical
  wording: Breaking then stayed Breaking as a temporary lower boundary. It now lowers to Fleeing and
  Fleeing stays Fleeing; see "Fleeing and escape". At this stage no positive morale was implemented; the
  later turn-start self-rally of a Fleeing unit and the killer's kill check are the positive transitions now,
  see "Self-rally at turn start" and "Kill morale checks". This heavy-hit check itself never raises morale.)*
- **Event order:** `Attacked`, then `MoraleChecked { unit, roll, chance, before, after }`, before any
  later reaction or turn transition. Later attacks and reactions use the updated effective attributes;
  the triggering attack keeps the chance computed before its check.
- **Log:** `bb_game` maps the event to a short line with the state change first
  (`E1 Steady>Wavering r60>=50`, or `E1 holds Steady r12<50` on success; truncated at 31 characters).
  Input handling is unchanged; the native morale status panel is covered in the following section.
- **Hash schema change:** `state_hash` appends each unit's base Resolve after the flag word. All hashes
  therefore differ from the earlier schema (Steady seed-1 opening `d144cb90f7939be8` →
  `8c00302113cbe668`); the flag/morale encoding is unchanged. Hashes quoted in older sections above are
  historical.
- **Deferred:** other morale triggers (surrounding, fear, propagation; the ally-death trigger and the
  killer's check were added later, see "Ally-death morale checks" and "Kill morale checks"), Fearsome's
  1+ threshold, mood, Rally the Troops and other rally sources (the turn-start self-rally of a Fleeing
  unit is implemented, see "Self-rally at turn start"), Unbreakable, perks/immunities, ranged effects, tooltips and map
  indicators. The partial panel label is documented below.

Tests: [`crates/bb_rules/tests/morale_checks.rs`](../../../crates/bb_rules/tests/morale_checks.rs) (13) and a
headless log test pair in `crates/bb_game/src/main.rs`. Existing tests needed no RNG-expectation
changes; only the pinned Steady opening hash in `morale_modifiers.rs` was updated for the new hash schema.

Validation: `cargo test --workspace --locked --offline` passed (148 tests, 0 failed), and
`cargo run --locked --offline -p bb_rules --bin headless_smoke -- --seed 1` printed
`OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=f152803a61acc9a3` (newly observed; the earlier
`ROUNDS=6 HASH=892ea5d6c8f754c6` predates the morale rolls and hash schema change).

## Native morale status panel — partial UC-COMBAT-018 / UC-UI-003 coverage

Each living unit's existing side-panel name row now shows a separate `MORALE <STATE> <PERCENT>%`
label, for example `MORALE WAVERING 90%`. The state is read directly from `Unit.morale` for every
scene build; the percentage comes from `Morale::modifier_percent()`. Steady is white/neutral, Confident
green, Wavering yellow and Breaking red, with the state name always visible. The percentage describes
the existing melee skill and defense scaling. It is not a direct multiplier on final hit chance. The
underlying values and rules remain **APPROXIMATION** as described in the two morale sections above.

Dead rows retain their existing `DEAD` indication and have no live morale label. Unit names remain
separate from labels; active-unit name highlighting and the HP/armour and AP/fatigue rows keep their
existing presentation. The label stays in the name row, inside the side panel. Previews, overlay
markers, log and the modal remain intact. Scene generation is read-only: it changes neither
`BattleState` nor RNG and submits no commands. Label wording, colors and placement are UI
**APPROXIMATION**, not original-UI parity. Coverage is partial; other morale checks/transitions,
tooltips and map indicators remain deferred. The Fleeing label and FLED rows are documented in
"Fleeing and escape".

Headless scene tests in [`crates/bb_game/src/render.rs`](../../../crates/bb_game/src/render.rs) cover
all four state labels and colors for player and enemy units, the rules-derived percentages, a fixed-seed
surviving 15-HP attack that produces `MoraleChecked` from Steady to Wavering and appears in the next
scene, scene state/RNG purity, dead-row handling, active-unit name highlighting, and longest-label
geometry against panel bounds and adjacent resource rows (`morale_panel_renders_all_state_labels_for_both_sides`,
`morale_panel_reflects_resolve_check_on_next_scene_without_mutation`,
`morale_panel_keeps_dead_indication_and_active_name_highlighting`,
`longest_morale_label_fits_name_row_inside_panel_and_clears_resource_rows`).

Validation: `cargo test --workspace --locked --offline` passed (152 tests, 0 failed);
`cargo build --workspace --locked --offline` passed. Native visual inspection was unavailable because no
display session was present. The source catalog remains incomplete.

## Fleeing and escape — partial UC-COMBAT-003 / 007 / 018 / 019 / 023 coverage

Implemented in `unit.rs` (`Morale::Fleeing`, `Unit.fled`, `Unit::is_present`), `lib.rs` (`Command::Flee`,
`Outcome::Retreat`, `EndReason::Escape`, `Event::Fled`, `RuleError::{NotFleeing, FleeingUnit}`),
`rules.rs` (`flee`, `escape`, presence-based occupancy/queue/targets/surround/outcome, hash), `ai.rs`
and the native `main.rs` / `render.rs`.

Provenance, kept separate:
- **Catalog-stated (not runtime-confirmed):** Fleeing x0.7 melee scaling, loss of control, no ZoC,
  -1000 initiative at round start, running to the map edge, individual retreat from the edge, "player all
  fled: retreat, not win", victory when all enemies are dead or fled (UC-COMBAT-003/007/018/019/023).
- **APPROXIMATION (slice choices):** the exact route policy, one step per automatic command, the
  `Retreat` / `Escape` naming and result rules, the hash codes and every UI detail.
- **Route policy (APPROXIMATION, no safe-route guarantee, may cross enemy ZoC):** shortest existing-BFS
  route to a passable, unoccupied map-edge hex; ties by greatest distance from the nearest present enemy
  at the endpoint, then lowest axial (q, r); the first step of the existing BFS path (fewest steps, not least AP;
  an uphill first step needs its full AP cost, else the turn ends). No RNG is consumed
  by route choice.

Behaviour:
- Failed Resolve check: Breaking → Fleeing, Fleeing stays Fleeing; the extra draw is consumed in all
  states; entering Fleeing clears a pending `skip_turn`. Fleeing has no ZoC from that moment on. The
  -1000 initiative (`FLEEING_INITIATIVE_PENALTY`, signed `i64`) applies at the next round's sort only.
- Presence = `alive && !fled` for occupancy, `reachable`, queues, targets, surround, outcome, AI, tokens.
  Escape keeps `alive`, HP, armour and stats; it emits `Fled`, never `Died`.
- `Command::Flee`: `BattleOver` and `NotActiveUnit` keep precedence; then `NotFleeing` (non-fleeing
  actor) or, for any ordinary command and `preview_move` of a Fleeing actor, `FleeingUnit` (before
  `NotPlayerUnit`). Rejections preserve state and RNG.
- One attempted step per command through `walk` (ordinary costs, reactions, stops, deaths). Each command
  moves, spends the attempted step's budget, escapes, or ends the turn through `advance_turn` (no edge
  route / unaffordable step). On an edge: escape without cost (even with 0 AP). Arrival on an edge:
  immediate escape. A stopped or killed mover does not escape or advance twice. Outcome is checked before
  advancing; no turn starts after termination.
- Outcomes: no player present → `Retreat` if a player escaped alive, else `Defeat`; no enemy present with
  players present → `Victory`; players are judged first. `EndReason::Escape` for results involving a
  surviving escapee (either side); `Elimination` and `TimeLimit` unchanged.
- `EndRound` neither marks Fleeing players nor suppresses their automatic turns. `ai::plan_turn` returns
  exactly one `Flee` for a Fleeing active unit of either side; the rest of the policy is unchanged.
- State hash: flag bits alive 0, waited 1, turn_started 2, skip_turn 3, morale code bits 4-6 (Fleeing 4),
  `fled` bit 7; `Retreat`/`Escape` hash as 3. Unchanged states keep their earlier hashes.
- Native: Fleeing player units are driven by `ai_step` at the existing cadence with no human actions,
  no End Round dialog and no place in the dialog's forfeiture count; panel `MORALE FLEEING 70%`; `P1 FLED`
  rows distinct from `DEAD`; log line `P1 escapes`; `RETREAT` banner (plus `UNITS ESCAPED` for Escape
  results); escapees have no token and are not hover targets.

Tests: `crates/bb_rules/tests/fleeing.rs` (32), new headless tests in `crates/bb_game/src/main.rs` and
`render.rs`. The saturated-Breaking expectations in `morale_checks.rs` were replaced by explicit
Breaking → Fleeing → Fleeing coverage; other tests are unchanged.

Validation: `cargo test --workspace --locked --offline` passed (192 tests, 0 failed); `cargo run --locked
--offline -p bb_rules --bin headless_smoke -- --seed 1` printed
`OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=f152803a61acc9a3` (actual output, unchanged because no
unit reaches Fleeing in that battle; not forced). No native window inspection (no display session).

Deferred explicitly: Rally the Troops and every rally source other than the turn-start self-rally (implemented, see
the section below) and the killer's kill check (see "Kill morale checks"), Unbreakable, other morale triggers and immunities,
any "trying to leave" interpretation of UC-COMBAT-048 beyond ordinary movement (its kill-check and movement
statements are partially covered, see "Kill morale checks" and "Map-edge movement penalty"), stance cancelling, defense bonuses,
renown, XP, loot and all campaign consequences. Coverage of the listed catalog rows is partial.

## Self-rally at turn start — partial UC-COMBAT-018 / 019 coverage

Implemented in `rules.rs` (`begin_turn`, `self_rally`), `lib.rs` (`Event::SelfRallyChecked`) and
`main.rs` (log). **APPROXIMATION** based on UC-COMBAT-018/019 text; no recovered original rule is
claimed. The Resolve formula, the Breaking destination, the timing and the edge precedence are slice
choices.

- **Timing:** after the ordinary AP refresh and fatigue regeneration of the once-per-round `begin_turn`,
  directly after `TurnStarted`. The existing `turn_started` guard gives at most one check per unit per
  round (no re-check after `Wait`, repeated `Flee`, or becoming Fleeing mid-turn). No new flag; hash
  codes and schema unchanged.
- **Eligibility:** present, Fleeing, not on a map-edge hex, and no adjacent present enemy (checked by
  presence and side directly: an adjacent fleeing enemy blocks although it exerts no ZoC; dead and
  escaped enemies and allies do not). Skipping edge hexes keeps the accepted immediate zero-cost edge
  escape (**edge precedence is an APPROXIMATION**).
- **Formula:** `chance = min(base Resolve, 100)`; `roll = next_u64() % 100`; success = `roll < chance`;
  one draw per eligible check, none otherwise.
- **Result:** success Fleeing -> Breaking and clears any pending `skip_turn`; failure keeps Fleeing. No
  other morale change and no additional AP or fatigue. The queue is not reordered; the unit immediately
  has ordinary command eligibility and ZoC; next round's initiative uses its current morale.
- **Event/RNG order:** `TurnStarted`, then `SelfRallyChecked { unit, roll, chance, before, after }`
  (distinct from `MoraleChecked`, which stays heavy-hit only). Queries and rejected commands remain
  state/RNG-pure.
- **Native:** a recovered player is handed to the human (`ai_step` stops driving); a failed unit keeps
  being driven by `Flee`; enemies follow current morale through the unchanged AI policy. Log lines put
  the outcome first: `P2 rallies r12<50`, `P2 keeps fleeing r88>=50` (31-character cut).
- **Deferred:** Rally the Troops, perks, mood, Unbreakable and other positive morale transitions (the killer's
  check of "Kill morale checks" exists), other triggers, route changes (voluntary retreat is superseded by "Party retreat order"). Catalog coverage remains partial.

Tests: `crates/bb_rules/tests/self_rally.rs` (23) and two headless tests in `crates/bb_game/src/main.rs`.
One existing test, `fleeing.rs::end_round_neither_marks_fleeing_players_nor_suppresses_their_turns`,
was narrowly updated (its fleeing P3 has Resolve 0 and the expected events include the failed
`SelfRallyChecked`); its purpose is unchanged. Historical evidence above is preserved (older "no rally"
wording describes the state at that time).

Validation: `cargo test --workspace --locked --offline` passed (217 tests, 0 failed); `cargo run
--locked --offline -p bb_rules --bin headless_smoke -- --seed 1` printed
`OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=f152803a61acc9a3` (actual output, unchanged: no unit
reaches Fleeing in that battle). No native window inspection (no display session).

## Ally-death morale checks — partial UC-COMBAT-018 coverage

Implemented in `rules.rs` (`check_ally_death_morale` from `resolve_attack`; private
`negative_morale_check` shared with `check_morale`), `lib.rs` (`Event::AllyDeathMoraleChecked`) and the
native log in `main.rs`. The catalog names the trigger; the affected-unit policy is an **APPROXIMATION**.

- **Trigger:** any melee death (ordinary or opportunity attack) after `alive = false`, queue removal and
  `Died`. Not for misses, surviving damage (including a Nine Lives interception), `Fled` or self-rally.
- **Affected (APPROXIMATION):** every present same-side ally at any distance, ascending unit id,
  regardless of queue status (finished, Wait-deferred, EndRound-marked, already Fleeing). Excluded: the
  casualty, enemies, dead units, escapees.
- **Check:** `chance = min(base Resolve, 100)`, one `next_u64() % 100` draw, pass when `roll < chance`,
  else `Morale::lowered()`; a failure leaving the unit Fleeing clears `skip_turn`. One check per ally
  per death (Fleeing allies included); no recursion or propagation; queue order is not changed.
- **Event/RNG order:** `Attacked`, `Died`, then `AllyDeathMoraleChecked { unit, fallen, roll, chance,
  before, after }` per ally, before later reactions, `BattleEnded` or turn advancement. No eligible ally:
  previous sequence and RNG usage. `MoraleChecked` stays heavy-hit only; `SelfRallyChecked` turn-start only.
- **Native log:** `E2 Steady>Wavering, E1 died` / `E2 holds Steady, E1 died` / `E2 still Fleeing, E1 died`,
  with `rNN<chance` or `rNN>=chance` appended only if the line stays within 31 characters.
- **Deferred:** propagation of morale drops, party-wide kill bonuses (the killer's own check is described in
  "Kill morale checks"), surrounding and fear checks, morale effects of
  escape, rally changes, perks and immunities.

Tests: `crates/bb_rules/tests/ally_death_morale.rs` (16) and one headless test in
`crates/bb_game/src/main.rs`. Narrowly updated existing tests for the new draws: `morale_checks.rs`
(lethal-hit test now distinguishes the dead victim's absent check from its allies' checks; purpose
unchanged), `morale_modifiers.rs`, `zone_of_control.rs`, `fleeing.rs` (incl. the seed-1 hash pin),
`end_round.rs`. Historical evidence above is preserved.

Validation: `cargo test --workspace --locked --offline` passed (234 tests, 0 failed); `cargo run
--locked --offline -p bb_rules --bin headless_smoke -- --seed 1` printed
`OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=ccd911452772673b` (actual output: same outcome and
rounds, hash changed from `f152803a61acc9a3` by the new draws; not forced). No native window inspection
(no display session).

## Kill morale checks — partial UC-COMBAT-018 / UC-COMBAT-048 coverage

Implemented in `unit.rs` (`Morale::raised`), `rules.rs` (`check_kill_morale` from `resolve_attack`),
`lib.rs` (`Event::KillMoraleChecked`) and the native log in `main.rs`. **Catalog-stated authority, not
runtime-confirmed:** [UC-COMBAT-018](../../source/10-tactical.md) covers morale checks on kills;
[UC-COMBAT-048](../../source/10-tactical.md) states that a positive morale check on the edge fails and
that moving a non-Fleeing brother onto the tactical edge forces morale toward Breaking. The killer-only
affected set, formula, scheduling and which edge statement is implemented are **APPROXIMATION** choices.

- **`Morale::raised()`:** Breaking → Wavering → Steady → Confident; Confident stays Confident; Fleeing stays
  Fleeing. Pure `const fn`, no check or mutation.
- **Trigger/eligibility:** after a hostile target dies in melee: one positive check for the killer if it is
  present and not Fleeing. Both sides; ordinary and opportunity attacks, including reactions against
  fleeing or withdrawing movers. Applying it to both factions is an **APPROXIMATION** scope choice; the
  catalog actor is described as a brother. A retreating killer is eligible and keeps its retreat intent. No check
  or draw for misses, surviving hits (heavy or armour-only), escapes or rejected commands. Only the killer
  is checked; no other unit changes through this mechanism.
- **Check:** based on the killer's current position, `chance = 0` on the outermost map ring and
  `min(base Resolve, 100)` elsewhere. The victim's position and movement destination do not decide the
  chance. One `next_u64() % 100` draw is consumed, success is `roll < chance` (equality fails), and
  success applies `raised()`. At the edge, zero chance leaves morale unchanged. Draw and event still occur
  at chance zero and when the killer is already Confident; base Resolve is preserved.
- **Event/RNG order:** `Attacked`, `Died`, existing `AllyDeathMoraleChecked` events, then
  `KillMoraleChecked { unit, fallen, roll, chance, before, after }`, then outcome or turn-transition events
  (the final kill is checked before `BattleEnded`). The draw follows the attack roll, damage draw and one
  draw per surviving victim-side ally, and precedes turn-start draws such as a self-rally.
- **Consequences:** the triggering attack keeps its previously calculated chance; later previews and attacks
  use the updated morale through the existing effective-stat calculation. Base stats, retreat intent,
  `skip_turn` and the current queue are unchanged; no hash field was added.
- **Native log:** improvement `P1 Steady>Confident r12<50`; unchanged morale `P1 stays Steady r88>=50` or
  `P1 stays Confident r12<50` (31-character limit; a kill with two allies logs five lines, within the six
  visible entries). Panel labels are unchanged.
- **Deferred:** party-wide kill bonuses, morale propagation, mood, perks, Unbreakable, ranged combat and
  campaign systems. UC-COMBAT-048 is partially covered here (failed positive check) and by "Map-edge
  movement penalty"; complete edge-morale coverage is not claimed.

Tests: `crates/bb_rules/tests/kill_morale.rs` (18: edge positions on all six sides and corners, both factions,
each eligible morale state, Resolve 100 and above, ordinary and opportunity kills, a retreating reaction
killer, an interior killer versus an edge victim, and an edge final kill before `BattleEnded`) and one
headless test in `crates/bb_game/src/main.rs`.
Existing exact-event and RNG expectations were extended with the new event and draw without dropping
assertions: `ally_death_morale.rs`, `morale_checks.rs`, `zone_of_control.rs`, `end_round.rs`, `fleeing.rs`
(incl. the seed-1 hash pin) and the `bb_game` ally-death log test. Historical evidence above is preserved.

Validation: `cargo test --workspace --locked --offline` passed (310 tests, 0 failed); `cargo run --locked
--offline -p bb_rules --bin headless_smoke -- --seed 1` printed
`OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=3fefd4ead00e829f` (actual output: same outcome and
rounds, hash changed from `ccd911452772673b` by the new draws; not forced). No native window inspection
(no display session).

Task T18 validation: `cargo test --workspace --locked --offline` passed (315 tests, 0 failed), and
`cargo run --locked --offline -p bb_rules --bin headless_smoke -- --seed 1` printed
`OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=3fefd4ead00e829f`. The hash matches the preceding
kill-check validation: edge checks still consume their one normal draw, while this task changes only
the effective chance. No native window inspection was done.

## Map-edge movement penalty — partial UC-COMBAT-048 coverage

**Catalog-stated authority, not runtime-confirmed:** [UC-COMBAT-048](../../source/10-tactical.md) states that
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

Tests: [`crates/bb_rules/tests/map_edge_morale.rs`](../../../crates/bb_rules/tests/map_edge_morale.rs) (10 tests:
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

Implemented in `unit.rs` (`Unit.retreating`), `lib.rs` (`Command::{Retreat, Withdraw}`,
`Event::RetreatOrdered`, `RuleError::{RetreatingUnit, NotRetreating}`), `rules.rs` (`retreat`,
`leave_battle`, `automatic_step` shared with `Flee`, eligibility, `EndRound`, hash), `ai.rs` and the native
`main.rs` / `render.rs` / `layout.rs`. Headless rules, automatic native driving and (added later) a native launch
control with mandatory confirmation (see "Native launch control" below).

Provenance, kept separate:
- **Catalog-stated (not runtime-confirmed):** edge units escape instantly, others auto-path to the nearest
  edge, deaths on the run are real, retreat is never a clean teleport (UC-COMBAT-022).
- **APPROXIMATION (slice choices):** scheduling (the order is not a turn action; withdrawing units act
  in their normal queue slots), route integration (the existing Flee route policy and one step per
  command), persistent intent, error names and precedence, event shape, hash bit, native log text.

Behaviour:
- `Unit.retreating` starts false, is reset only by `Unit::new` (new battle) and survives rounds and
  self-rally. It is separate from morale.
- `Retreat`: `BattleOver`, `NotActiveUnit` (also for a non-present active unit), then `RetreatingUnit`
  (already retreating) / `FleeingUnit` (Fleeing issuer), then `NotPlayerUnit` (enemy). Pure rejections.
  Valid order: mark every present player (finished, deferred, Fleeing, active), clear their `skip_turn`,
  emit `RetreatOrdered { unit: issuer }`, escape all marked edge players in ascending id order (`Fled`, no
  cost, no reactions, no RNG, no advancement between exits), check the outcome once, then keep the
  present issuer's turn unrefreshed or advance exactly once if the issuer left. Enemies, casualties and
  escapees are untouched.
- `Withdraw`: `NotRetreating` without intent; a retreating-and-Fleeing unit is eligible. It runs the same
  `automatic_step` as `Flee`: edge escape without cost, otherwise the first BFS step through `walk`
  (ordinary AP/fatigue, opportunity attacks, stops, deaths) or a turn ending when no route or no
  affordable step exists. A killed mover is advanced by `walk` only once.
- Ordinary commands and `preview_move` for a retreating actor: `RetreatingUnit` (before `FleeingUnit`).
  `plan_turn` returns `Withdraw` before ordinary and Fleeing policy. `EndRound` skips retreating units
  and `advance_turn` never passes over them for a pending skip.
- Outcomes reuse `Retreat`/`Defeat`/`Victory`/`Escape`; no turn follows a terminal event.
- State hash: `retreating` is flag bit 8; false hashes as before (seed-1 smoke hash unchanged).
- Native: `human_unit`, `selected_active_player`, hit labels and `queued_player_units` exclude retreating
  players; `ai_step` drives them at the existing cadence; log line `retreat ordered`. (The launch control is
  described under "Native launch control"; the earlier "controls unchanged" statement is superseded.)

### Native launch control (APPROXIMATION of UC-COMBAT-022 / UC-UI-007)

Based on catalog-stated behavior only; the `V` shortcut, wording, colours and layout are slice placeholders
with no original-runtime observation or UI parity claim.
- **Retreat (V)** button (`layout::RETREAT_BUTTON`, same row as End Round, right of it) and `V`/`v`. Enabled
  exactly as End Round: present, controllable active player, unfinished battle; disabled (no error) for enemy
  turn, finished battle, dead/escaped, Fleeing and retreating actors. Shortcut hint now reads
  `E END TURN  W WAIT  X END ROUND  V RETREAT  R RESTART  N SEED  ESC QUIT`.
- `PendingConfirm { action: EndRound | Retreat, actor, round, state_hash }` replaces the End Round-only intent.
  At most one exists; a second launch cannot switch or confirm it. Enter/Confirm submit, Esc/Cancel close with
  no command; the modal consumes map clicks and other shortcuts and pauses `ai_step`. Opening, cancelling and
  rendering draw no RNG and leave `BattleState` unchanged.
- Dialog text: all present player units will withdraw, edge units escape immediately, the rest move
  automatically and may die, the order cannot be cancelled after confirmation. The count (present players)
  includes Fleeing units and finished turns, excludes dead and escaped. No renown or defense-bonus claims.
- Confirmation removes the intent, revalidates actor, round, hash and eligibility, then submits exactly one
  `Command::Retreat` via `App::run`; stale intent shows `RETREAT DISCARDED: BATTLE CHANGED`. Restart/new seed
  clear it and keep the `Z` preference; the 350 ms click guard covers both actions. Edge exits, outcome,
  logging and `Withdraw` turns are the unchanged rules behavior.

Tests: `crates/bb_rules/tests/retreat.rs` (35) and six headless tests in `crates/bb_game/src/main.rs` /
`render.rs`. No existing test was edited at that point. Native launch control: further App, renderer and layout
tests in `main.rs`, `render.rs`, `layout.rs`; the End Round tests were updated for the renamed intent type
and the new hint text with assertions kept. Historical evidence above is preserved.

Validation: `cargo test --workspace --locked --offline` passed (275 tests, 0 failed); `cargo run --locked
--offline -p bb_rules --bin headless_smoke -- --seed 1` printed
`OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=ccd911452772673b` (actual output, unchanged). No native
window inspection (no display session).

Validation of the native launch control: `cargo test --workspace --locked --offline` passed (296 tests, 0
failed); `cargo build --workspace --locked --offline` succeeded. Visual inspection was unavailable (no display
session, no Xvfb); geometry is covered by layout/render tests only.

Deferred explicitly (the native Retreat button, shortcut and confirmation are no longer deferred): difficulty-scaled defense bonuses
in ZoC, renown, injuries, loot, XP and campaign consequences, the unconfirmed deserter first-action
observation. Coverage of UC-COMBAT-022 is partial.

## Adrenaline — partial UC-COMBAT-066 / UC-COMBAT-003 coverage

Implemented in `unit.rs` (`Unit.has_adrenaline`, `Unit.adrenaline_pending`), `lib.rs`
(`Command::Adrenaline`, `Event::AdrenalineUsed`, `RuleError::{MissingAdrenaline, AdrenalinePending}`),
`rules.rs` (`ADRENALINE_AP`, `ADRENALINE_FATIGUE`, `ADRENALINE_INITIATIVE_BONUS`, the public pure
`adrenaline_availability(&Unit)` that `apply` reuses, `begin_round`, hash), `scenario.rs`
(`adrenaline_battle`, `launch_battle`, `LaunchArgs`, `--adrenaline` in `parse_args`), `headless_smoke.rs`,
the `ai.rs` command-bound comment and, in `bb_game`, `main.rs` (App loadout, button/shortcut routing, event
logger), `layout.rs` (`ADRENALINE_BUTTON`) and `render.rs` (button, pending marker).

Provenance, kept separate:
- **Catalog-stated (not runtime-confirmed):** UC-COMBAT-066 (Adrenaline, 1 AP, 20 fatigue, next-round first
  act, +2000 initiative for order only) and UC-COMBAT-003 (initiative ordering, Fleeing -1000 "unless
  Adrenaline").
- **APPROXIMATION (slice choices):** the explicit capability flag instead of a perk system, availability for
  both sides, duplicate-use rejection while pending, the pending-effect lifecycle (consumed at the next
  `begin_round` for every unit), error names and precedence, the event shape, hash bits and log text.

Behaviour:
- Both flags are false in `Unit::new`; no default scenario unit has Adrenaline, so Flat and Hills are
  unchanged unless `--adrenaline` is given.
- Validation: `BattleOver`, `NotActiveUnit` (also for a non-present actor), `RetreatingUnit`, `FleeingUnit`,
  then `MissingAdrenaline`, `AdrenalinePending`, `NotEnoughAp` (needs 1), `TooFatigued` (checked add of 20
  within `fatigue_max`). Rejections are pure.
- Success: -1 AP, +20 fatigue, `adrenaline_pending = true`, exactly one `AdrenalineUsed`; queue position,
  remaining turn, base `Stats` and the RNG are untouched; no advancement. No AP grant or refresh.
- `begin_round` adds 2000 to the key of present pending units (combined with fatigue and the Fleeing
  penalty, id tie break kept), builds the queue, then clears the pending flag on all units. The current
  round is never reordered; `Wait` does not consume the effect. Fleeing or retreat intent keeps it; automatic
  turns and turn-start processing run after ordering as usual.
- State hash: `has_adrenaline` is flag bit 9, `adrenaline_pending` bit 10; false values hash as before.
- Native: `AdrenalineUsed` logs `<unit> adrenaline: next round` (ASCII, within the log width).
- **APPROXIMATION (opt-in player availability, UC-COMBAT-066 and UC-UI-003/008):** `--adrenaline` on either
  binary (boolean, default off, combinable with `--terrain` and `--seed`) starts from `scenario::battle` and
  sets `has_adrenaline` on player units only (nothing pending, no RNG, budgets/queue/RNG stream equal to the
  default scenario; only the hashed capability bit differs). `App` stores the flag so `R`/`N` rebuild the
  selected terrain and loadout, keeping confirmation clearing and the Z/H preferences.
- **APPROXIMATION (button, shortcut, marker presentation):** `ADRENALINE (1)` button in the top-right strip
  above the unit rows (`layout::ADRENALINE_BUTTON`, now x 684-828, y 3-43, the left of the four 144x40 skill buttons
  since the T46 reflow; shared by drawing and hit testing; scale-1.25 title over two scale-1 rows) with
  `COST 1 AP 20 FAT` and `NEXT ROUND INIT +2000` taken from the rules constants; key `1`. Enabled only for the present,
  controllable active player unit of a running battle whose `adrenaline_availability` is `Ok`; a disabled
  activation is a silent no-op, an enabled one submits exactly one `Command::Adrenaline` via `App::run`.
  Skill-button clicks are routed after the modal and click guard but before map clicks; the confirmation
  modals consume the button and key; repeated key events remain dropped (`key_press_accepted`). A pending
  Adrenaline shows an amber `ADRENALINE: NEXT RD` tag on the AP/fatigue line of every present unit with
  `adrenaline_pending` (also finished turns), gone after the next-round consumption, omitted for dead or
  escaped rows. Wording, placement and colours are placeholders, not original-UI parity.
- **APPROXIMATION inspired by UC-COMBAT-020 and UC-COMBAT-066, not original AI parity:** inside the existing
  adjacent-attack branch, a capable actor of either side selects Adrenaline only when
  `adrenaline_availability` succeeds, `AP >= ADRENALINE_AP + ATTACK_AP`, and checked
  `fatigue + ADRENALINE_FATIGUE + ATTACK_FATIGUE <= fatigue_max`. If selected, pending validation prevents
  another activation; the next planning call uses the existing weakest-adjacent-target attack policy if its
  budget remains available. No adjacent legal target, a one-short combined budget, missing capability or a
  pending effect keeps the prior attack/approach/fallback policy. Withdraw and Flee retain priority. Planning
  stays pure, deterministic, one-command-at-a-time and RNG-free; it does not simulate attacks. Capability
  recipients remain unchanged: the opt-in scenario sets the flag on the three player units only.

Tests: `crates/bb_rules/tests/adrenaline.rs` (16: both sides, costs, purity, precedence, exact cap and
`u32::MAX` overflow, duplicate rejection after `Wait`, next-round ordering via `EndTurn`/`EndRound`,
multiple boosted units, ties, large initiative, refresh and expiry, Fleeing/retreat, dead/escaped units,
RNG, hash bits and pinned Flat `0x8c00302113cbe668` / Hills `0xb759683cdff4bfa2` initial hashes, measured on
the pre-change encoding as well), `crates/bb_rules/tests/adrenaline_ai.rs` (both factions, fixture geometry,
exact and one-short combined AP/fatigue budgets, `u32::MAX`, missing/pending capability, no target,
Withdraw/Flee precedence, planning purity, no-repeat activation and resource-exhaustion progress) and
`adrenaline_is_logged_briefly_as_next_round` in `bb_game`.

Additional tests: availability query purity and agreement with `apply` over a grid of capability, pending,
AP, fatigue and `fatigue_max` values including `u32::MAX` overflow and the error precedence
(`adrenaline.rs`); flag parsing, composition with Flat/Hills, player-only capability, unchanged budgets and
RNG, default equivalence and smoke runs (`terrain_presets.rs`); App tests for button/shortcut activation
equal to a direct command, disabled no-ops without map fall-through, repeated input, modal isolation, R/N
persistence and synchronization; renderer tests for costs and effect text, enabled/disabled fills, the
shared hit rectangle, geometry against the window, top bar, unit rows and other controls, supported glyphs,
pending markers and their disappearance, and scene purity.

T33 corrected the AI test fixture triangle to actor `(0,-2)` and targets `(1,-2)` then `(0,-1)` for both
faction pairings, preserving adjacency, target IDs and 20/10 HP ordering. Its local validity guard checks
that each present unit is on-map, not on an obstacle and at a distinct position; dead or escaped units are
excluded. Failing-first evidence: before the correction, the new geometry regression failed because unit 0
occupied obstacle `(0,0)`, while the six existing Adrenaline AI tests passed. After correction,
`cargo test -p bb_rules --test adrenaline_ai --locked --offline` passed (7 tests, 0 failed), and
`cargo test --workspace --locked --offline` passed (606 tests, 0 failed); `git diff --check` passed. This
validates test setup only and claims no additional `docs/source/**` coverage. The historical validation and
smoke records below remain unchanged.

Historical pre-T32 validation: `cargo test --workspace --locked --offline` passed (459 tests, 0 failed) and
`cargo build --workspace --locked --offline` succeeded. Before the AI selection task, seed-1 `headless_smoke`
printed default Flat `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=dfce9b8d74bcaf93` and default Hills
`OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=d0b704f0294173be`; the old `--adrenaline` outputs were Flat
`OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=fb858e080255c87d` and Hills
`OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=7a71a36ccd992df4` (capability-only hash change). That evidence is
retained and superseded below.

Current T32 seed-1 smoke outputs (each smoke run replays twice and compares outcome, reason, round and final hash):
- default Flat `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=dfce9b8d74bcaf93` and default Hills
  `OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=d0b704f0294173be` (both unchanged);
- `--adrenaline` Flat `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=d38ca185d725df1b` and Hills
  `OUTCOME=Victory REASON=Elimination ROUNDS=7 HASH=406c2bf145855863`;
- `--rotation` Flat `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=eca98bcb3e525dfb` and Hills
  `OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=17dd25afecfd72b6`;
- `--footwork` Flat `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=26f421838e407143` and Hills
  `OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=9f83d1d40f1a2f6e`;
- all three flags `--adrenaline --rotation --footwork`: Flat
  `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=fdc85eae43dd0ba3`, Hills
  `OUTCOME=Victory REASON=Elimination ROUNDS=7 HASH=93baae9abf5c755b`.

The seed-1 Flat/Hills Adrenaline replays also compare the full command and event streams twice and require
valid commands, termination within `MAX_COMMANDS`, and at least one `AdrenalineUsed` event. The representative
Flat result keeps its outcome and round count; Hills changes outcome, rounds and hash. Rotation-only and
Footwork-only loadouts, as well as defaults, retain their accepted outcomes and hashes.

Current T32 validation: `cargo test --workspace --locked --offline` passed (605 tests, 0 failed), and
`cargo build --workspace --locked --offline` succeeded.

Visual inspection limitation: the environment has no display (`DISPLAY` and `WAYLAND_DISPLAY` empty), so
the native opt-in window was not opened; button and marker layout is verified by geometry tests only.

Deferred explicitly: skill learning or perks, original AI parity and the catalog's increased AI attention,
Rotation/Footwork AI, other skills and a generic skill framework. Adrenaline selection is limited to the
conservative combined-budget heuristic above. Coverage of UC-COMBAT-066 is partial.

## Rotation — partial UC-COMBAT-039 coverage (rules, opt-in loadout and native ally targeting)

Implemented in `unit.rs` (`Unit.has_rotation`), `lib.rs` (`Command::Rotation`, `Event::Rotated`,
`RuleError::MissingRotation`), `rules.rs` (`ROTATION_AP`, `ROTATION_FATIGUE`, `RotationPreview`,
`BattleState::preview_rotation`, `BattleState::legal_rotation_targets`, the shared validated execution plan,
hash bit 11),
`scenario.rs` (`rotation_battle`, `launch_battle_with`, `LaunchArgs.rotation`, `--rotation`) and, in `bb_game`,
the `main.rs` event logger, the ally-targeting mode in `App`, `layout.rs` (`ROTATION_BUTTON`, `UiHit::Rotation`)
and `render.rs` (button, markers, targeting preview rows). The rules and queries are headless-first and
unchanged by the native work. No AI casting.

Provenance, kept separate:
- **Catalog-stated (not runtime-confirmed):** UC-COMBAT-039 (swap with an adjacent non-hostile, 3 AP + a
  fatigue cost, not into a blocked hex, and neither participant stunned or rooted) and UC-COMBAT-008
  (opportunity attacks when leaving ZoC, bypassed here); UC-COMBAT-048 for the map-edge penalty itself.
  T70 implements the catalog's stunned-target restriction in this slice; that is runtime validation of this
  rebuild, not confirmation against the original game.
- **APPROXIMATION (native availability and presentation, UC-COMBAT-039 and UC-UI-003/008):** the opt-in
  `--rotation` availability, the `2` shortcut, the ally-targeting workflow and every wording, position and
  colour below are slice choices, not original-UI parity.
- **APPROXIMATION (slice choices):** the capability flag, 25 fatigue (the catalog gives no number),
  target restrictions beyond the catalog's stunned/rooted clause (same side, present, not Fleeing or
  retreating, no capability or turn needed), the error names and precedence, extension of the edge penalty
  to Rotation arrivals, event shape, hash bit and log text. Rooted status is not modelled.

Behaviour:
- `has_rotation` is false in `Unit::new`; existing scenarios, loadouts and seed-1 results are unchanged.
- Validation (complete before mutation, pure, no RNG): `BattleOver`, `NotActiveUnit` (also non-present),
  `RetreatingUnit`, `FleeingUnit`, `MissingRotation`, `InvalidTarget` (invalid id, self, hostile, dead,
  escaped, stunned, Fleeing, retreating), `Unreachable` (either occupied hex off-map, including extreme coordinates checked without overflow, or an obstacle), `NotAdjacent`,
  `NotEnoughAp` (needs 3), `TooFatigued` (checked add of 25 within `fatigue_max`).
- A stunned target reaches the existing `RuleError::InvalidTarget` branch with the other target-validity
  exclusions, before coordinate and affordability checks. Rejections and both pure queries leave resources,
  positions, status, queue, hash and RNG unchanged.
- Queries: `preview_rotation(unit, target)` returns a `RotationPreview` with both destinations, the fixed
  skill costs, and the sorted `(unit_id, arrival_hex)` list of participants whose current morale would
  change to Breaking. It returns the same errors in the same precedence as `Command::Rotation`. Already-
  Breaking participants are omitted. `legal_rotation_targets(unit)` returns accepted preview targets in
  ascending id order; finished, Wait-deferred and EndRound-marked allies remain valid. Both queries reflect
  the active turn and battle state plus current positions, budgets, capability, morale and presence on every
  call. They mutate no state, emit no events and consume no RNG.
- A preview is a description of its current state snapshot, not a reservation; command submission revalidates
  it. Preview and execution share the validated plan for actor gates, target restrictions (including stun), overflow-safe
  passability, costs, destinations and selected edge consequences. Rotation remains deterministic and
  provokes no reactions.
- Success: -3 AP and +25 fatigue for the actor only (fixed skill cost, no uphill surcharge); positions swap
  atomically; `Rotated` first. `walk` is not used, so there are no reactions, even with several enemy
  controllers on either departure hex. Queue, round, `turn_started`, `waited`, `skip_turn`, retreat intent,
  Adrenaline flags, target budgets and `Stats` are unchanged; no extraction and no turn advance at 0 AP.
- Occupancy, `legal_targets`, controllers, surround and elevation hit modifiers reflect the new positions.
- Edge morale (**APPROXIMATION**): each participant in ascending id order takes the existing edge penalty
  at its arrival hex (same predicate as `walk`); `EdgeMoraleChanged` only for real changes, after `Rotated`;
  already-Breaking participants emit nothing; no `Fled`; no RNG.
- State hash: `has_rotation` is flag bit 11; false values hash as before.
- Native: `Rotated` logs `<unit> swaps with <unit>`.
- Opt-in loadout: `--rotation` on either binary (boolean, default off, composable with `--adrenaline`,
  `--terrain` and `--seed`) builds `scenario::launch_battle_with(seed, terrain, adrenaline, rotation)`;
  `rotation_battle` is `battle` plus `has_rotation` on player units only (no state change otherwise, no RNG,
  budgets, queue and RNG stream equal the default scenario; only the hashed capability bit differs).
  `launch_battle(seed, terrain, adrenaline)` and `adrenaline_battle` are unchanged; without the flag all
  accepted scenarios and loadouts keep their hashes. `App` stores the flag so `R`/`N` rebuild the selected
  terrain and loadout. The AI never issues Rotation; Rotation-only loadouts keep the existing policy, while
  combinations with Adrenaline also use the separate T32 Adrenaline heuristic.
- Button and shortcut: **ROTATION (2)** (`layout::ROTATION_BUTTON`, now x 834-978, y 3-43 after the T46 reflow) shares the top-right
  skill strip with Adrenaline (drawing and hit testing share the rectangle) and shows `COST 3 AP 25 FAT` and
  `SWAP ADJACENT ALLY` (shortened in T46) from the rules constants. Enabled only for the present, controllable active
  player unit with a nonempty `legal_rotation_targets`; stunned allies do not count, so if all adjacent
  allies are stunned the button and shortcut remain disabled. A disabled activation is a silent no-op. Activation
  (button or `2`) captures actor, round and `state_hash` as an action-tagged `PendingTargeting` (shared with Footwork) and submits no command; a
  second activation cancels; repeated key events remain dropped.
- Targeting: only legal, unstunned allies are marked (hex fill, brighter on hover, plus a scale-1 `SWAP` label on the lower half of the ally's own token that clears neighbouring tokens, names and bars); hovering a stunned ally leaves the instruction rows and shows no swap preview. Ordinary movement
  highlights, costs, movement preview and attack labels are suppressed; `H`/`Z` toggle and their overlays are
  kept. The preview-box area shows instructions, or for a hovered legal ally the `RotationPreview`: the
  actor-only cost, both destinations, `NO OPPORTUNITY ATTACKS`, the cancel hint and one warning naming each
  participant of every predicted edge penalty (zero, one or two, at most four rows, widths and glyphs
  tested). The client duplicates no eligibility or edge arithmetic.
- Selection and cancellation: a map click on a legal ally revalidates actor, round, hash and the current
  preview, clears targeting first, then submits exactly one `Command::Rotation` through `App::run`. Clicking
  a stunned ally is an invalid-target click: it submits nothing, keeps targeting and shows the existing
  `NOT A SWAP TARGET: CLICK A MARKED ALLY OR PRESS ESC` feedback. Other clicks behave the same;
  nothing falls through to Move or Attack. Stale intent is discarded with `ROTATION DISCARDED: BATTLE
  CHANGED` before anything executes and only valid intent is rendered. `Esc`, a second `2` or the button
  cancel without quitting (`Esc` outside targeting and confirmations still quits). `E`, `W`, `X`, `V`, `1`,
  `R`, `N` and the End Round, Retreat and Adrenaline buttons cancel it before their normal handling (all of them, `R`/`N` after rebuilding the battle, also arm the click guard when targeting was open);
  opening a confirmation clears it and the modals consume the Rotation inputs. Restart, new seed and every
  submitted command clear it, and a stale intent never blocks automatic turns. Completing or cancelling arms
  the existing timed click guard (`MODAL_CLICK_GUARD`) so a follow-up click cannot reach the map.

Tests: `crates/bb_rules/tests/rotation.rs` (35: both factions, exact costs, atomic swap, preservation,
validation precedence and purity, extreme-coordinate `Unreachable`, exact caps and `u32::MAX` overflow, finished/Wait/EndRound-marked
targets, query/apply error agreement, preview cost/destination/event agreement, zero/one/two edge penalties,
already-Breaking participants, sorted/refreshed legal targets and query purity, multi-controller ZoC bypass
without attacks or RNG, elevation and refreshed spatial queries, edge arrivals and ordering, Adrenaline
coexistence, stunned-target rejection for both sides, validation precedence, mixed eligible targets,
re-eligibility after scheduled stun expiration, independent hash bit with the pinned Flat and Hills initial hashes) and
`rotation_is_logged_naming_both_participants` in `bb_game`.

Native tests: `terrain_presets.rs` (flag parsing and composition, all terrain/loadout combinations with
player-only capability, unchanged budgets/RNG, preserved default and Adrenaline-only hashes, smoke);
`bb_game` App tests (activation by button and key without a command, disabled states including all adjacent
allies stunned, legal selection equal to the direct command, stunned-target invalid-click and stale-intent
isolation, exactly-once dispatch, Esc/2/button cancel, modal priority,
other-action cancellation, H/Z independence, click guard with injected instants, R/N persistence, stale actor,
round, hash and eligibility rejection, cleanup after submission, continued automatic turns); renderer tests
(marker set, suppressed ordinary previews, costs, destinations, zero/one/two warnings, overlay preservation,
scene purity, button looks); layout and supported-glyph tests for both skill buttons, markers and every
targeting row, with the existing badge, resource, modal and banner geometry tests retained. The existing
invalid-move feedback integration test is unchanged.

Historical pre-T32 validation: `cargo test --workspace --locked --offline` passed (528 tests, 0 failed) and
`cargo build --workspace --locked --offline` succeeded. Seed-1 default outputs were Flat
`dfce9b8d74bcaf93`, Hills `d0b704f0294173be`; prior Adrenaline outputs were Flat
`fb858e080255c87d`, Hills `7a71a36ccd992df4`. Those outputs are retained in the Adrenaline section's
historical record and superseded for current AI runs below.

Current T32 seed-1 `headless_smoke` results: Rotation-only runs retain the accepted behavior and hashes, and
the Adrenaline combinations below include actual AI activations on the replay:
- `--rotation` Flat `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=eca98bcb3e525dfb`, Hills
  `OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=17dd25afecfd72b6`;
- `--rotation --adrenaline` Flat `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=6b94625d3560a3b3`, Hills
  `OUTCOME=Victory REASON=Elimination ROUNDS=7 HASH=d91435785603942b`.

Historical pre-T32 combined hashes were Flat `c3321d175f709c65` and Hills `a5e6fc215f86523c`; they remain
recorded as prior validation, when AI never cast Adrenaline, and are superseded by the current outputs.

Visual inspection limitation: the environment has no display (`DISPLAY` and `WAYLAND_DISPLAY` empty), so
the native targeting window was not opened; layout is verified by geometry, glyph and scene tests only.

Historical note: before the native-control task Rotation was headless-only (no activation UI, loadout or
CLI flag); that evidence above (rules tests, query tests) still stands.

T70 validation: the new rules regression first failed because stunned adjacent allies still produced a valid
preview (and coordinate checks could win with an invalid stunned target); after the shared-plan filter was
added, `cargo test -p bb_rules --test rotation --locked --offline` passed all 35 tests. Focused `bb_game`
App and renderer tests also passed for all-stunned disabled activation, mixed-target highlighting, invalid
click feedback and stale intent after stun changes the captured hash. Actual validation then passed
`cargo test --workspace --locked --offline` (13.6 s), `cargo build --workspace --locked --offline` (0.08 s),
and `git diff --check`.

Deferred explicitly: rooted Rotation targets, stunned/rooted Footwork targets, skill learning, equipment and
AI casting (Footwork is covered separately below). The headless stun effect is documented separately under
"Stun effect". Coverage of UC-COMBAT-039 remains partial; the stunned Rotation-target rule does not establish
complete coverage or original-runtime parity.


## Footwork — partial UC-COMBAT-039 coverage (rules, opt-in loadout and native destination targeting)

Implemented in `unit.rs` (`Unit.has_footwork`), `lib.rs` (`Command::Footwork`, `Event::FootworkUsed`,
`RuleError::MissingFootwork`), `rules.rs` (`FOOTWORK_AP`, `FOOTWORK_FATIGUE`, `FootworkPreview`,
`BattleState::preview_footwork`, `BattleState::legal_footwork_destinations`, the shared validated plan, hash
bit 12), `scenario.rs` (`footwork_battle`, `launch_battle_for`, `LaunchArgs.footwork`, `--footwork`) and, in
`bb_game`, the `main.rs` event logger, the action-tagged targeting intent shared with Rotation in `App`,
`layout.rs` (`FOOTWORK_BUTTON`, `UiHit::Footwork`, the reflowed three-button strip) and `render.rs` (button,
`STEP` markers, targeting preview rows). The rules and queries are unchanged by the native work. No AI casting;
headless usage remains `BattleState::apply(Command::Footwork { unit, to })` with the queries above.

Provenance, kept separate:
- **Catalog-stated (not runtime-confirmed):** UC-COMBAT-039 (Footwork: step out of ZoC without opportunity
  attacks; shared precondition 3 AP + a fatigue cost) and UC-COMBAT-008 (opportunity attacks when leaving
  ZoC, bypassed here); UC-COMBAT-048 for the map-edge penalty itself.
- **APPROXIMATION (slice choices):** the capability flag, 20 fatigue (the catalog gives no number), the
  one-hex limit, fixed costs without movement or uphill surcharges, height ignored for legality, the error
  names and precedence, extension of the edge penalty to Footwork arrivals, event shape, hash bit and log
  text.
- **Deferred:** status effects other than the T61 headless stun (rooted and similar), Spearwall interaction (UNCONFIRMED in the
  catalog), skill learning, equipment and AI casting.

Behaviour:
- `has_footwork` is false in `Unit::new`; existing scenarios, loadouts and seed-1 results are unchanged.
- Validation (complete before mutation, pure, no RNG): `BattleOver`, `NotActiveUnit` (also non-present),
  `RetreatingUnit`, `FleeingUnit`, `MissingFootwork`, `Unreachable` (actor or destination off-map or an
  obstacle, checked without overflow before any distance arithmetic; unchanged or occupied destination),
  `NotAdjacent` (distinct, free, passable destination beyond one hex), `NotEnoughAp` (needs 3), then
  `TooFatigued` (checked add of 20 within `fatigue_max`).
- Queries: `preview_footwork(unit, to)` returns `FootworkPreview { unit, from, to, ap_cost, fatigue_cost,
  edge_penalty: Option<Hex> }` or the same error as the command (one shared plan). `edge_penalty` is the
  destination when arrival would change the actor's current morale to Breaking. `legal_footwork_destinations
  (unit)` lists accepted destinations in `NEIGHBOR_DIRS` order, empty for any ineligible actor (including
  invalid actor coordinates; no panic). Recomputed on every call; no state change, events or RNG. A preview
  is a snapshot, not a reservation.
- Success: -3 AP, +20 fatigue, relocation, `FootworkUsed { unit, from, to }`. `walk` is not used, so no
  opportunity attacks regardless of controllers at departure or arrival. Queue, round, `turn_started`,
  `waited`, `skip_turn`, retreat intent, Adrenaline flags and `Stats` are unchanged; no turn advance at 0 AP;
  repeated use is allowed within the ordinary eligibility and budgets.
- Occupancy, `legal_targets`, controllers, surround and elevation hit modifiers reflect the new position.
- Edge morale (**APPROXIMATION**): the existing predicate at the arrival hex; `EdgeMoraleChanged` follows
  `FootworkUsed` only for a real change; already-Breaking emits nothing; no `Fled`/extraction; no RNG.
- No passive immunity: `Move`, `Flee` and `Withdraw` still provoke reactions for units with the flag.
- State hash: `has_footwork` is flag bit 12; false values hash as before.
- Native: `FootworkUsed` logs `<unit> footwork`.
- Opt-in loadout (**APPROXIMATION**, a launch option, not a perk or skill system; UC-COMBAT-039 is the catalog
  authority for the skill itself): `--footwork` on either binary (boolean, default off, composable with
  `--adrenaline`, `--rotation`, `--terrain` and `--seed`) sets `LaunchArgs.footwork`. Both binaries build
  the battle through the shared `scenario::launch_battle_for(LaunchArgs)`, which sets each selected
  capability on player units only; `footwork_battle` is `battle` plus `has_footwork` on the three player units.
  Budgets, pending effects, placement, queue and the RNG stream are unchanged; only the hashed capability bit
  differs. `launch_battle_with`, `launch_battle`, `adrenaline_battle` and `rotation_battle` keep their results,
  and without the flag all accepted scenarios and loadouts keep their hashes. `App` stores the flag so `R`/`N`
  rebuild the selected terrain and loadout. The AI never issues Footwork; without `--adrenaline`, Footwork-only
  smoke remains unchanged. If both are enabled, the smoke also exercises the T32 Adrenaline heuristic.
- Button and shortcut (**APPROXIMATION** of UC-COMBAT-039 and UC-UI-003/008; availability, shortcut, strip
  reflow and wording are slice choices, not original-UI parity): the top-right skill strip held three equal
  186x40 buttons at y 3 (*historical; the T46 four-button reflow replaced these coordinates, see "Indomitable"*): `ADRENALINE_BUTTON` x 708, `ROTATION_BUTTON` x 900 and the new **FOOTWORK (3)**
  (`layout::FOOTWORK_BUTTON`, x 1092-1278) with visible gaps. The strip extends left of the side panel into the
  free band right of the top-bar text (ends near x 674 for the longest seed); only the strip was reflowed and
  each skill keeps its label, cost row and effect row (`COST 3 AP 20 FAT`, `STEP TO ADJACENT HEX` from the
  rules constants). Enabled only for the present, controllable active player unit with a nonempty
  `legal_footwork_destinations`; a disabled activation is a silent no-op. Button or `3` captures actor, round
  and `state_hash` and submits no command.
- Shared intent: the Rotation-only intent became one `PendingTargeting { action, actor, round, state_hash }`
  with `action` Rotation or Footwork; at most one mode exists, exclusive with confirmations. A second
  activation of the open skill cancels; the other targeted skill switches directly (fresh intent, no guard
  change) when available, and an unavailable one leaves the mode unchanged.
- Targeting: only legal destinations are marked (violet fill plus a scale-1 `STEP` label, brighter on hover);
  movement highlights, AP labels, movement preview, attack labels and Rotation markers are suppressed; `H`/`Z`
  and their overlays are kept. The preview box shows instructions or, for a hovered legal hex, the
  `FootworkPreview`: actor-only cost, departure and destination coordinates, `NO OPPORTUNITY ATTACKS`,
  `ESC OR 3 CANCELS` and the rules-predicted edge-morale warning when present (at most four rows, widths and
  glyphs tested; nothing implies extraction). The client duplicates no eligibility, cost or edge arithmetic.
- Selection and cancellation: a click on a legal destination revalidates actor, round, hash and the current
  preview, clears targeting first, then submits exactly one `Command::Footwork` through `App::run` (equal to
  the direct command, including under enemy control and on edge arrival). Other clicks submit nothing, keep
  targeting and show `NOT A STEP HEX: CLICK A MARKED HEX OR PRESS ESC`, never falling through to Move, Attack
  or Rotation. Stale intent is discarded with `FOOTWORK DISCARDED: BATTLE CHANGED`, is not rendered and never
  blocks automatic turns. `Esc`, a second `3` or the button cancel without quitting; `H`/`Z` preserve it;
  `E`, `W`, `X`, `V`, `1`, `R`, `N` and the End Round, Retreat and Adrenaline buttons cancel it before their
  normal handling; confirmations clear it and consume all targeting inputs; submitted commands clear it.
  Completing or cancelling either mode, and `R`/`N` after rebuilding (when targeting was open), arm the
  existing `MODAL_CLICK_GUARD`; switching does not. Repeated key events stay dropped.
- Native tests: `terrain_presets.rs` (`--footwork` parsing and composition, all terrain/skill-flag
  combinations with player-only capability, unchanged budgets/RNG, existing-loadout equivalence, smoke);
  `bb_game` App tests (activation, disabled states, selection equal to the direct command including edge
  arrival and enemy control, invalid clicks, exactly-once dispatch, Rotation/Footwork switching and
  unavailable-mode activation, cancel, modal priority, immediate actions, H/Z, click guard and R/N with
  injected instants, stale actor/round/hash/eligibility, automatic turns, purity) and renderer/layout tests
  (three-button strip bounds, labels, rows and glyphs, STEP markers, hover previews, edge warnings,
  suppression, scene purity) with the existing badge, resource, modal and banner coverage retained; the
  invalid-move feedback integration test is unchanged. Existing Rotation tests were updated for the tagged
  representation without weakening their assertions.

Tests: `crates/bb_rules/tests/footwork.rs` (27) and `footwork_is_logged_concisely_and_edge_arrival_follows`
in `bb_game` (see README "Footwork" for the coverage list).

Historical pre-T32 Footwork validation: `cargo test --workspace --locked --offline` passed (598 tests, 0
failed) and `cargo build --workspace --locked --offline` succeeded. The pinned initial hashes in
`footwork.rs` and `rotation.rs` still hold. Before T32 the all-skill smoke did not cast Adrenaline; these old
combined hashes are retained below as historical evidence and superseded by current results.

Current T32 seed-1 smoke results: default, Rotation-only, Footwork-only and Rotation+Footwork loadouts keep
their accepted hashes; Adrenaline combinations follow the changed AI policy:
- `--footwork` Flat `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=26f421838e407143`, Hills
  `OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=9f83d1d40f1a2f6e`;
- `--adrenaline --footwork` Flat `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=dcd3fff2deaf550b`, Hills
  `OUTCOME=Victory REASON=Elimination ROUNDS=7 HASH=4cea2c6e5d5bd5d3`;
- `--adrenaline --rotation --footwork` Flat `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=fdc85eae43dd0ba3`,
  Hills `OUTCOME=Victory REASON=Elimination ROUNDS=7 HASH=93baae9abf5c755b`.

Historical pre-T32 all-skill hashes were Flat `b471278bb8f72c95`, Hills `be7e9bdca604752c`; Adrenaline was
capability-only then, and these values are superseded by the current outputs.

Visual inspection limitation: the environment has no display (`DISPLAY` and `WAYLAND_DISPLAY` empty, no
Xvfb), so the native targeting window was not opened; layout is verified by geometry, glyph and scene tests
only.

Deferred explicitly (unchanged apart from the T61 headless stun, see "Stun effect"): status effects (rooted and similar), Spearwall interaction (UNCONFIRMED in the
catalog), skill learning, equipment and AI use.

## Fast Adaptation — partial UC-COMBAT-067 coverage (headless rules, `--fast-adaptation` launch option, native stack indicator)

Catalog statements ([UC-COMBAT-067](../../source/10-tactical.md), public catalog copy, not runtime-confirmed):
each miss against an opponent gives +10% on the next attack ("wiki; older guides 8%"), reset on a hit
(including a blocking tile or scatter); the percentage is to be locked in data; three consecutive misses
show a growing hit % on the fourth swing, then reset on connect.

Implemented (`unit.rs`, `rules.rs`): `Unit.has_fast_adaptation: bool` and `Unit.fast_adaptation_stacks: u32`
(default `false` / `0`; the default scenario has no capable unit, and since T35 `--fast-adaptation` grants
it to player units only, below). `FAST_ADAPTATION_BONUS_PER_STACK = 10`.

- `hit_chance` for a present, hostile, adjacent melee pair adds `stacks * 10` for a capable attacker, in the
  same widened (`i128`) sum as morale-adjusted stats, surround and elevation, before the single 5–95 clamp:
  `clamp(skill - defense + surround + elevation + 10 * stacks, 5, 95)`. Other queries are unchanged.
- The shared `resolve_attack` (ordinary and opportunity attacks alike) computes the chance from the
  pre-attack counter, records it in `Event::Attacked.chance`, and then a capable attacker gains one stack on a
  miss (`saturating_add`) or resets to zero on any hit (armour-only and lethal included). The bookkeeping uses
  no RNG and emits no event; AP/fatigue costs, validation order, event order and morale processing are untouched.
- Stacks belong to the attacker. They persist across target changes, movement, Wait, turn and round
  boundaries until a hit resets them. An attacker without the capability ignores its stored counter and does
  not modify it.
- `state_hash`: flag bit 13 is the capability and bits 14..=45 the `u32` counter (existing bits unchanged).
  False/zero defaults leave all hashes unchanged (`BattleState::new(1)` and the Flat/Hills scenario pins).

**APPROXIMATION** (not catalog-stated, no original-runtime confirmation): reading +10% as additive percentage
points on the final hit chance; the choice of the 10 rather than 8 variant; counting only the attacker's
own consecutive misses, regardless of target; no expiry on turn, round or movement; applying the same rule to
opportunity attacks and to enemy-side attackers; the capability being a plain headless flag.

Validation: [`crates/bb_rules/tests/fast_adaptation.rs`](../../../crates/bb_rules/tests/fast_adaptation.rs)
(20 tests) covers both factions' three-miss/hit cycle, disabled capability, independent attacker counters, target
changes, armour-only and lethal hits, opportunity-attack misses and hits followed by ordinary attacks,
persistence through movement/Wait/turns/a round boundary, composition with morale, surround and elevation, both
clamps, `u32::MAX` saturation and overflow-safe arithmetic, query and rejected-command purity,
preview/`Event::Attacked.chance` agreement, RNG consumption equal to the existing hit/miss branches, hash
sensitivity with an independent replica of the bit layout, and the unchanged default hash pins. Fixtures assert
distinct, passable hexes.

### Launch option and native indicator (T35)

- `LaunchArgs.fast_adaptation` (default `false`) is parsed from the value-less `--fast-adaptation` by the
  shared `parse_args` and composes independently with `--seed`, `--terrain` and the other capability flags.
  `launch_battle_for` sets `has_fast_adaptation` on player units only, stacks `0`; no RNG is consumed and only
  the selected flags change, so runs without the option keep their pinned hashes. **APPROXIMATION**: the
  player-only assignment and the launch option (not a perk system).
- `App.fast_adaptation` is kept across **R** and **N**; the rebuilt battle has zero stacks, and the terrain,
  the other capability options and the **H**/**Z** preferences are retained.
- Indicator (**APPROXIMATION**, presentation): for every present capable unit of either faction, including
  zero stacks, `render.rs` draws a scale-1 `ADAPT <stacks>` right aligned (right edge 8 px inside the window)
  on the HP/armour line of its panel row, separate from the pending-Adrenaline marker on the AP/fatigue line.
  Dead, escaped and incapable units have none. `ADAPT 4294967295` (95 px, x 1177) fits and clears the stock
  HP/armour text, morale labels, adjacent rows and the pending marker; only supported font glyphs are used.
  The hover `HIT n%` remains `BattleState::hit_chance`. Rendering is read-only; modal, targeting, click-guard
  and overlay behaviour is untouched.
- Passive, not a skill: no command, button, shortcut, targeting mode or AI policy. Headless AI attacks of capable
  units automatically receive the accepted effect when enabled.
- Seed-1 headless smoke (actual): `--fast-adaptation` Flat `OUTCOME=Victory REASON=Escape ROUNDS=3
  HASH=03cd5060271badf3`, Hills `OUTCOME=Victory REASON=Elimination ROUNDS=5 HASH=9ad76a636259c440`; with
  `--adrenaline --rotation --footwork --fast-adaptation` Flat `OUTCOME=Victory REASON=Escape ROUNDS=3
  HASH=f119e88eac2122c3`, Hills `OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=7a501923872d8823`. The
  three-skill outputs without the option (Flat `fdc85eae43dd0ba3`, Hills `93baae9abf5c755b`) and the default
  outputs are unchanged. Command/event replays are deterministic, valid and bounded, and include real
  misses that raise counters.
- Tests: `terrain_presets.rs` (parsing, all 16 flag subsets, player-only/zero stacks/unchanged construction,
  smoke pins, tracked replay), `bb_game` App tests (initial configuration, R/N persistence and stack reset) and
  render scene tests (zero/nonzero/max counters, both factions, excluded rows, live miss→hit transition with
  `HIT` agreement, geometry, glyphs, purity). No native window was opened for visual inspection; only headless
  scene geometry was verified.
- Statements above that no scenario or native client uses Fast Adaptation describe the pre-T35 state and are
  retained as historical validation evidence.

Deferred explicitly: scenario-authored (non-launch-option) capability assignments, AI selection of the passive
(none needed), perk learning or a generic perk framework, ranged attacks, scatter and blocking-tile hits, and
the 8% variant. Partial coverage only; no parity claim.


## Dodge — partial UC-COMBAT-058 coverage (headless melee defense, `--dodge` launch option, native defense breakdown)

Catalog statements ([UC-COMBAT-058](../../source/10-tactical.md), public catalog copy, not runtime-confirmed):
+15% of current initiative to melee and ranged defence; fatigue that tanks initiative also tanks Dodge;
Relentless helps keep initiative when fatigued; the defence tooltip includes a Dodge line that shrinks as
fatigue rises.

Implemented (`unit.rs`, `rules.rs`): `Unit.has_dodge: bool` (default `false`) and
`unit::DODGE_DEFENSE_PERCENT = 15`.

- `Unit::current_initiative() -> i64` is `i64(stats.initiative) - i64(fatigue / 2)`: integer division before
  the subtraction, signed, unclamped. `begin_round`'s ordering key is now `current_initiative()` minus the
  existing Fleeing penalty plus the existing pending-Adrenaline bonus; descending order, ID tie-breaks and the
  absence of clamping are preserved.
- `Unit::dodge_defense_bonus() -> u32`: `0` when `!has_dodge`, otherwise
  `floor(max(current_initiative, 0) * 15 / 100)` using widened (`u128`) arithmetic. Fleeing and pending
  Adrenaline adjustments are queue-only and excluded.
- `Unit::effective_defense()` = morale-scaled base defense (`floor(base * percent / 100)`, `u32::MAX` cap)
  `.saturating_add(dodge_defense_bonus())`. Morale does not scale the bonus; `Stats` are unchanged;
  `effective_melee_skill` is unchanged. The morale percentage in the sections above describes morale scaling of
  the base value, not every source of final defense.
- `hit_chance` (ordinary attacks, opportunity attacks, native preview) needs no change beyond calling
  `effective_defense`; the bonus is recomputed from live fatigue on each query (no round-start cache), so move,
  attack, incoming hit (+5) / miss (+2) fatigue and turn-start regeneration all take effect immediately. For
  an opportunity attack the mover has already paid the step's fatigue, so the reaction uses that value.
  `Event::Attacked.chance` equals the immediately preceding query. No command, event, RNG draw or cost is added.
- `state_hash`: flag bit 46 is `has_dodge` (existing assignments, including Fast Adaptation counter bits
  14..=45, unchanged). `false` leaves prior hashes unchanged; `BattleState::new(1)` and the Flat/Hills scenario
  pins are unchanged. Before T37 no scenario, launch option or AI used the capability (historical); the
  default scenario still grants it to nobody and the AI is unchanged, so runs without `--dodge` keep their
  smoke outputs.

Provenance, kept separate:
- **Catalog-stated:** the 15% figure, dependence on current initiative, and fatigue lowering it.
- **APPROXIMATION (slice choices, no runtime confirmation):** the fatigue-to-initiative formula
  `initiative - fatigue/2` (the slice's existing queue formula), flooring, the zero floor, the post-morale
  addition, the exclusion of sorting-only Fleeing/Adrenaline adjustments, application to both factions and to
  opportunity attacks, and the plain capability flag.

Validation (actual, this tree): [`crates/bb_rules/tests/dodge.rs`](../../../crates/bb_rules/tests/dodge.rs)
(31 tests) covers disabled capability, 15% floor boundaries, odd fatigue, zero and negative initiative,
`u32::MAX` arithmetic and defense saturation, all morale states including Fleeing, base `Stats` and melee skill
unchanged, pending Adrenaline not inflating the bonus, both factions, composition with surround, elevation,
Fast Adaptation and both hit-chance clamps, live fatigue through real movement, attacking, incoming hit and
miss fatigue and turn-start regeneration, ordinary and opportunity `Event::Attacked.chance` agreement with exact
RNG consumption, query and rejected-command purity, flag-sensitive hashing (including a full Fast Adaptation
counter) with the default hash pins, and round ordering (ties, odd fatigue, negative keys, Fleeing, Adrenaline,
Dodge flags irrelevant). Fixtures use distinct, passable hexes.
`cargo test -p bb_rules --test dodge --locked --offline` (31 passed), `cargo test --workspace --locked
--offline` (all passed) and `git diff --check` (clean). Historical statements elsewhere about defense
describe earlier validation evidence and remain accurate for capability-free units.

### Launch option and native defense breakdown (T37)

- `LaunchArgs.dodge` (default `false`) is parsed from the value-less `--dodge` by the shared `parse_args`;
  repeats are harmless and it composes independently of seed, terrain and the four other capability flags
  (all 32 subsets tested). `launch_battle_for` sets `has_dodge` on **player units only**; construction consumes
  no RNG and changes only the selected flags (`terrain_presets.rs` compares every unit field, queue, RNG,
  elevations and hash against `battle(seed, terrain)`). `launch_battle_with`/`launch_battle`/`adrenaline_battle`
  keep their signatures and results (they supply `dodge: false`).
- The native `App` stores `dodge`; **R** and **N** rebuild with it and keep terrain, all capability selections
  and the **H**/**Z** preferences, restoring fresh AP/fatigue and zero Fast Adaptation stacks.
- Side panel: for every present Dodge-capable unit (either faction, Fleeing and retreating included, dead,
  escaped and incapable omitted) `DODGE +<dodge_defense_bonus()> DEF <effective_defense()>` is drawn at scale 1
  at `PANEL_X`, row top + 51 px. `DEF` is the total effective melee defense (not a percentage, not the Dodge
  part alone); zero bonuses are drawn. Values are read from live state each frame, so they follow
  fatigue-producing actions and turn-start regeneration. Geometry: 7 px tall (row +51..+58) inside the 60 px
  row, below the AP/fatigue line (ends +50) and the pending-Adrenaline box (ends +51), below the Fast
  Adaptation marker, above the next row and the `QUEUE` heading (y 420); the widest line
  `DODGE +644245094 DEF 4294967295` is 185 px (x 900..1085). Only glyphs the bitmap font supports are used.
- `HIT n%` previews remain `BattleState::hit_chance`; a scripted-enemy fixture shows the drawn `DEF`, the
  drawn preview (`skill - DEF`) and the resolved `Event::Attacked.chance` agreeing, and a second fixture shows
  the same for an enemy attacking a launch-granted player.
- Dodge is a passive: no command, button, shortcut, targeting mode or AI policy. Headless AI attacks against
  capable defenders use the accepted behavior automatically (comments in `headless_smoke.rs` updated).
- **APPROXIMATION:** the player-only assignment, the launch option and the panel line's presentation (the
  catalog states only that the defence tooltip shows a Dodge line).

Seed-1 headless smoke (actual, this tree; deterministic over two runs and an in-process command/event replay,
all commands valid, far below the 10 000-command bound; replays include attacks on defenders with a nonzero
Dodge bonus):

| options | Flat | Hills |
| --- | --- | --- |
| none (unchanged) | `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=dfce9b8d74bcaf93` | `OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=d0b704f0294173be` |
| `--dodge` | `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=e55edadd8f630253` | `OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=dce3433c8ab331fe` |
| all four earlier options (unchanged) | `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=f119e88eac2122c3` | `OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=7a501923872d8823` |
| all five options | `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=57f7c67846b45003` | `OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=ac49aa482cb73309` |

Statements above that no scenario, launch option or native client uses or shows Dodge describe the pre-T37
state and are retained as historical validation evidence. No native window was opened for visual inspection;
the evidence is headless scene geometry tests, not a screenshot.

Deferred explicitly: perk learning or a generic perk framework, scenario-authored (non-launch-option)
assignments, equipment encumbrance, Relentless and ranged defense. Partial coverage only; no parity claim.


## Nine Lives — partial UC-COMBAT-059 coverage (headless melee survival)

Catalog statements ([UC-COMBAT-059](../../source/10-tactical.md), public catalog copy, not runtime-confirmed):
a unit that would die with Nine Lives unused this fight survives at low HP; the perk is on cooldown until the
next battle; an injury may still apply; a second lethal hit in the same fight kills; the observable result is a
would-be-fatal blow leaving "a sliver of HP" with the perk greyed.

Implemented (`unit.rs`, `lib.rs`, `rules.rs`, `bb_game/src/main.rs`): `Unit.has_nine_lives` and
`Unit.nine_lives_used` (both `false` by default), `rules::NINE_LIVES_SURVIVAL_HP = 1` and
`Event::NineLivesTriggered { unit }`.

- Shared `resolve_attack` (ordinary and opportunity attacks, both factions, Fleeing and retreating defenders):
  after armour absorption and the would-be HP loss, a hit on a present, positive-HP defender with
  `has_nine_lives && !nine_lives_used` whose loss would reduce HP to zero leaves it alive at 1 HP and marks
  the protection used. A unit already at 1 HP may consume unused protection and stay at 1 HP. Misses,
  nonlethal and armour-only hits do not consume it. Only constructing a fresh unit resets the flag; `Wait`,
  turns, rounds, morale changes and retreat do not.
- Event order: `Attacked` (rolled `dmg`, or the Indomitable-reduced value when that stance is effective, and `absorbed` retained, `hp_loss` = actual HP lost = previous HP - 1;
  other attacks keep the existing unclamped would-be `hp_loss`), then `NineLivesTriggered { unit }`, then, only
  if the actual loss is at least `MORALE_CHECK_HP_LOSS_THRESHOLD` (15), the usual `MoraleChecked`. No `Died`,
  `AllyDeathMoraleChecked`, `KillMoraleChecked`, queue removal or elimination check follows; victory or defeat
  waits for actual death.
- Invariants: no RNG draw is added; AP, fatigue and armour are not refreshed. The survived hit still counts as a
  hit for Fast Adaptation (attacker stacks reset), incoming hit fatigue (+5) and movement interruption: the
  first protected reaction stops the mover's route, the remaining controllers still react in accepted order
  while the mover lives, and a later lethal hit in the same command follows normal death handling.
- `state_hash`: flag bit 47 is `has_nine_lives` and bit 48 is `nine_lives_used` (bits 0..=46 unchanged).
  `false`/`false` leaves prior hashes unchanged; the `BattleState::new(1)` and Flat/Hills scenario pins hold.
- Native log: `P1 nine lives: survives` (`Event::NineLivesTriggered`, within the 31-character limit). No
  activation control. (Launch option and indicator: see "Launch option and indicator (T39)" below; earlier
  statements that none exist are historical.)

Provenance, kept separate:
- **Catalog-stated:** survival of a would-be-fatal blow at low HP, once per battle, death on a second lethal hit.
- **APPROXIMATION (slice choices, no runtime confirmation):** the exact 1 HP (the catalog supplies no value),
  melee-only interception, the actual-HP-loss `hp_loss` reporting and the morale check based on it, the plain
  capability flag, application to both factions, and the log wording.

Historical statements elsewhere that describe a lethal hit as killing the defender were true before this task and
remain accurate for units without the capability or with it used; they are qualified where needed.

Validation (actual, this tree): [`crates/bb_rules/tests/nine_lives.rs`](../../../crates/bb_rules/tests/nine_lives.rs)
(18 tests) covers both factions, exact-lethal and overkill damage, survival from 1 HP, disabled and used
protection, nonlethal hits, misses, armour-only and armour-absorbed lethal hits, first-hit survival followed by
death (two opportunity attackers in one move, and a survived reaction followed by a miss), queue retention,
movement interruption, incoming fatigue, Fast Adaptation reset, Fleeing and retreating defenders, absence of
death-triggered events, the 14-versus-15 actual-loss morale boundary with event order and RNG consumption,
last-unit outcome delay, no replenishment across `Wait` and a round boundary, persistence through retreat and
morale changes, query and rejected-command purity, and hash sensitivity with unchanged pins. An App test in
`crates/bb_game/src/main.rs` covers the log line. `cargo test -p bb_rules --test nine_lives --locked --offline`,
`cargo test --workspace --locked --offline` and `git diff --check` pass.

### Launch option and indicator (T39, APPROXIMATION)

- `LaunchArgs.nine_lives` (default `false`) is parsed from the value-less `--nine-lives` by the shared
  `parse_args`; repeats are harmless and it composes independently with seed, terrain and the five other
  capability options (all 64 subsets tested). `launch_battle_for` sets `has_nine_lives` on **player units
  only** with `nine_lives_used = false`; construction consumes no RNG and changes only selected capability flags.
  `launch_battle_with`, `adrenaline_battle`, `rotation_battle`, `footwork_battle` and `launch_battle` keep their
  signatures and results (they supply `false`). Without the option, event streams and pinned hashes are unchanged.
- The native `App` stores `nine_lives`; **R** and **N** rebuild with it, restoring unused protection, fresh
  resources and zero Fast Adaptation stacks while keeping terrain, the other capability selections and the
  **H**/**Z** preferences.
- Side panel: for every present capable unit (either faction, Fleeing and retreating included; dead, escaped and
  incapable omitted) `NINE LIVES READY` (rose) or `NINE LIVES USED` (dim gray) is drawn at scale 1, right
  aligned 8 px inside the window (longest label 95 px, x 1177..1272), row top + 51 px, sharing the line with the
  left-aligned Dodge breakdown (ends at x 1085 at most) without overlap. State is read from the live `Unit`; a
  real protected hit flips READY to USED and a later lethal hit removes it with the row. It clears the resource
  text, pending-Adrenaline box, Fast Adaptation indicator, the next row and the queue for all six stock rows
  with maximum Dodge, stacks and pending Adrenaline at once; only supported glyphs; rendering stays pure.
  No command, button, shortcut, targeting mode or AI change; survival stays in `bb_rules`.
- Seed-1 smoke at T39 (actual): `--nine-lives` Flat `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=e7d351c8b0dc9913`, Hills
  `OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=64286fc0a709803e`; all six then-available options (before
  `--berserk` was added) Flat
  `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=cb45f871a3bb0b83`, Hills
  `OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=6812901431975589`. Earlier smoke pins are retained
  (the five-option and default runs are unchanged). Stock smoke need not trigger protection; a scripted legal
  encounter from the launch configuration shows `NineLivesTriggered` and then normal death on a second lethal hit.
- Tests: `crates/bb_rules/tests/terrain_presets.rs` (parser, 64 subsets, replays on both terrains, scripted
  encounter), App tests and scene tests in `crates/bb_game/src` as listed in the README. Native visual
  inspection of a window was not performed. Actual results: `cargo test --workspace --locked --offline` all passed
  (220 `bb_game` tests, 38 in `terrain_presets`), `cargo build --workspace --locked --offline` and `git diff --check` clean.
- **APPROXIMATION:** the launch option, player-only assignment and the indicator's text, colours and placement.
  The 1-HP survival approximation above is unchanged.

Deferred explicitly: enemy or scenario assignments, perk learning or a generic perk framework, injuries,
post-battle survival and temporary protection buffs, ranged attacks, status damage and other damage sources,
and a tooltip or greyed-button presentation closer to the catalog. Partial coverage only; no parity claim.

## Berserk — partial UC-COMBAT-038 coverage (passive melee AP refund and player launch option)

Catalog statements ([UC-COMBAT-038](../../source/10-tactical.md), public catalog copy, not runtime-confirmed):
the killer regains 4 AP immediately, once per turn; fatigue is not refreshed; it can chain if AP and fatigue
allow; the observable result is a brother killing and attacking again in the same turn.

Implemented in `unit.rs`, `lib.rs`, `rules.rs`, the shared launch parser and `bb_game/src/main.rs`:
`Unit.has_berserk` and `Unit.berserk_used` (both `false` by default), `rules::BERSERK_AP_REFUND = 4`,
`Event::BerserkTriggered { unit, ap_gained }` and the value-less `--berserk` option. `LaunchArgs.berserk`
defaults to false. `launch_battle_for` assigns the selected capability to player units only; use starts false.
The option and player-only assignment are **APPROXIMATION**s. Configuration changes only selected capability
flags and consumes no RNG.

- Trigger: in `apply`, after the ordinary `Command::Attack` has resolved (AP and fatigue paid first), a target
  that actually died and a present, capable attacker with `berserk_used == false` give
  `ap_gained = min(4, ap_max.saturating_sub(ap))`; `ap += ap_gained` (no overflow possible) and `berserk_used`
  is set. The turn is not advanced or refreshed. With `ap <= ap_max` the gain is always 4 (the attack just freed
  4 AP), so the cap truncates only in states outside the contract (a test pins this defensively). A zero gain
  still emits the event and consumes the use (**APPROXIMATION**).
- Not triggers: opportunity attacks (`resolve_attack` with `pay_costs = false` never reaches the refund; they
  neither refund nor consume the use), misses, nonlethal and armour-only hits, and Nine Lives survival (the
  target stays alive). Further ordinary kills in the same scheduled turn give no second refund.
- Lifecycle: `begin_turn` clears `berserk_used` only inside the existing once-per-round `turn_started` guard,
  right with the AP/fatigue refresh. `Wait` returning the unit, another unit's action and the actor's own
  `EndTurn`/`EndRound` never clear it; an `EndRound` pass-over performs a fresh refresh and does clear it; the
  next round restores eligibility. Wait-separated portions count as one scheduled turn (**APPROXIMATION**).
- Event order: `Attacked`, `Died`, `AllyDeathMoraleChecked*`, `KillMoraleChecked`, `BerserkTriggered`, then any
  `BattleEnded`. All earlier events keep their relative order.
- Invariants: no RNG draw is added; fatigue, HP, armour and morale are unchanged by the refund. Without
  `--berserk`, existing event streams and pinned hashes are unchanged. With it, construction changes only
  selected capability flags and leaves Berserk unused.
- `state_hash`: flag bit 49 is `has_berserk` and bit 50 is `berserk_used` (bits 0..=48 unchanged). `false`/`false`
  leaves prior hashes unchanged; the `BattleState::new(1)` (`10088116083934094952`) and Flat/Hills scenario
  pins hold. All 64 combinations of the neighbouring flag bits and a maximal stack field hash differently.
- AI (`ai.rs` explanation only; `has_berserk` is never read by the T55 or T66 policies): a normal 9-AP turn
  with one refund has at most 13 AP to spend, at most one Adrenaline activation (1 AP) and so at most seven
  commands that leave the turn active under the existing minimum costs (1 + 12 / 2). T66 adds Stunning Blow
  at 6 AP, above the 2-AP continuing-command minimum, so the bound remains seven; the capability-free bound
  (five commands that leave the turn active for 9 AP) is retained. Before T55, those fixtures ended with explicit
  EndTurn or battle completion; T55 adds Recover as a distinct terminal command, described below.
- Native feedback: `App` retains the selection through **R** and **N**. Rebuilding restores unused Berserk,
  fresh resources, zero Fast Adaptation stacks and unused Nine Lives while preserving terrain, other capability
  selections and H/Z preferences. The log shows `P1 berserk: +4 AP` (within 31 characters), and the refund is
  visible through existing AP values and the event log. Berserk is passive: it needs no activation control or
  AI selection policy. Native capability indicators are deferred.

Provenance, kept separate:
- **Catalog-stated:** 4 AP, once per turn, fatigue unchanged.
- **APPROXIMATION (slice choices, no runtime confirmation):** ordinary-melee-only triggering, the `ap_max` cap,
  the plain capability flag applied to both factions, the player-only launch option and assignment, the event
  slot, treating Wait-separated portions as one scheduled turn, the zero-gain event and the log wording.

Focused rules coverage is in `crates/bb_rules/tests/berserk.rs` (20 tests): both factions, first and second kill,
disabled/used capability, exact arithmetic including `u32::MAX` maxima, unchanged fatigue/resources/RNG against a
capability-free twin, misses/nonlethal/armour-only, Nine Lives survival then death, final-enemy event order before
`BattleEnded`, Wait/EndTurn/EndRound lifecycle without duplicate refresh, opportunity attacks, query and
rejected-command purity, hash sensitivity and pins, and the AI progress bound. `terrain_presets.rs` covers parser
defaults/repeats, all 128 capability subsets, player-only assignment, unused state, construction purity, an
actual two-kill refund that funds the second affordable attack without refunding fatigue, and deterministic valid
command/event replays for Berserk alone and all seven options on Flat and Hills. App tests call the kill through
`App::run`, check AP/log feedback, then consume and restore Berserk through both restart and new-seed actions.

Historical validation before T41 recorded `cargo test --workspace --locked --offline` (735 passed, 0 failed),
the seed-1 default smoke `OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=dfce9b8d74bcaf93`, and that no launch option
or native configuration could enable Berserk at that time. T41 validation: `cargo test --workspace --locked
--offline` passed (741 tests, 0 failed), `cargo build --workspace --locked --offline` passed, and
`git diff --check` was clean. Current seed-1 smoke outcomes:

| Configuration | Flat | Hills |
| --- | --- | --- |
| `--berserk` | `Victory / Escape`, 3 rounds, `b90d0af968bae726` | `Victory / Elimination`, 4 rounds, `ae18c48440cd1618` |
| All seven capability options | `Victory / Escape`, 3 rounds, `17373b790c6aaffb` | `Victory / Elimination`, 4 rounds, `f471788f11714fdd` |

Each headless smoke run repeats internally. Replay tests also compare complete deterministic command and event
streams, require successful command application, and bound termination. Native visual inspection of a window was
not performed.

Deferred explicitly: refunds from reaction or opportunity attacks and other damage sources, Killing Frenzy (T67 later
adds a headless approximation, see "Killing Frenzy"; this deferral is retained as historical evidence) and
area attacks, enemy assignment beyond the current player-only launch option, native capability indicators, and
perk learning or a generic perk framework. The passive needs no activation control or AI selection policy; its
refund is visible through existing AP values and the event log. Partial coverage only; no parity claim. Earlier
statements that no `Unit` flag or event exists are historical.


## Pathfinder — partial UC-COMBAT-057 coverage (player launch option and native preview)

**Catalog statement** ([UC-COMBAT-057](../../source/10-tactical.md), public catalog copy, not runtime-confirmed):
moving costs less fatigue (the wiki example is often 50% less); it grants no extra AP pool. The catalog also says
terrain AP may ease, "lock to vanilla numbers"; no vanilla numbers are confirmed here.

`Unit.has_pathfinder` (default `false`) and `rules::PATHFINDER_MOVE_FATIGUE = 1` (beside `MOVE_FATIGUE = 2`)
apply the existing headless reduction to each attempted movement step. The single pure helper
`rules::move_fatigue(&Unit) -> u32` owns the capability branch. `plan_move` supplies the discounted
`MovePreview.fatigue_cost` and nominal post-move attack budget; `reachable`, execution, automatic
`Flee`/`Withdraw` movement and AI affordability use the same rule query. Route selection, movement AP and
AI action selection are unchanged.

The shared `scenario::parse_args` accepts the value-less `--pathfinder` option (default false), independently
with `--seed`, `--terrain` and the seven existing capability options. Repeats are harmless. `LaunchArgs.pathfinder`
selects `has_pathfinder` on player units only through `launch_battle_for`; compatibility constructors pass false
and preserve their prior results. Construction consumes no RNG and changes only selected capability flags.
The player-only assignment and launch option are **APPROXIMATION**s. The accepted 2-to-1 per-step
fatigue approximation remains in `bb_rules` (uniform for Ground steps; T48 applies the same halving to the Swamp cost, 4 to 2); the client does not reproduce its formula.

`App` stores the selection. **R** rebuilds the same seed and terrain; **N** uses the next seed. Both retain all
capability selections and the **H**/**Z** preferences, while restoring fresh battle resources, zero Fast
Adaptation stacks and unused Nine Lives/Berserk state. Pathfinder is passive. The existing movement preview
shows its discounted planned FAT value, and the existing resource row shows current fatigue. No activation
control, dedicated capability indicator or AI selection policy is needed.

- **Reactions.** The discounted fatigue of an attempted step (destination-dependent since T48/T71: Swamp and Snow are twice the Ground value) is charged (together with its AP) before
  opportunity attacks, including an attempt that is then stopped or lethal. Affordability of later steps is
  rechecked against the current fatigue after reactions (incoming hit/miss fatigue), so a miss can still
  strand a multistep move. Additions stay checked and the cap at `u32::MAX` is exact.
- **Not discounted.** AP costs (including uphill surcharges), attack and incoming-attack fatigue, Footwork,
  Rotation, Adrenaline and fatigue regeneration. An already-on-edge automatic escape remains free.
- **Invariants.** Construction consumes no RNG and changes only selected capability flags. Without
  `--pathfinder`, earlier command/event streams and hashes stay unchanged (`BattleState::new(1)`, Flat/Hills
  scenario and seed-1 smoke `Victory / Escape`, 3 rounds, `dfce9b8d74bcaf93`). A launch-built no-reaction
  encounter compares a capable unit with its capability-free twin: route and AP expenditure match, discounted
  preview fatigue equals actual expenditure, and RNG is unchanged.
- **Hash.** `has_pathfinder` is flag bit 51 (bits 0..=50 unchanged); `false` leaves earlier hashes unchanged.

Provenance, kept separate:
- **Catalog-stated:** a fatigue reduction on movement (often 50%), no extra AP pool.
- **APPROXIMATION (slice choices, no runtime confirmation):** the 2-to-1 per-step cost (uniform at T43; T48 extends it to Swamp and T71 to Snow) (no confirmed
  vanilla rounding or terrain costs), application to both sides, coverage of automatic Flee/Withdraw movement,
  AI affordability, and the plain capability flag.

The test `crates/bb_rules/tests/pathfinder.rs` (18 tests) covers both factions, flat/uphill/downhill multistep
routes and unchanged AP against a capability-free twin, preview/`reachable`/execution agreement, exact fatigue
limits and one-short rejection, `u32::MAX` boundaries, validation precedence and rejection/query purity, the
attack-budget case the discount makes affordable, missed/stopping/lethal reactions and a continuation that
depends on incoming fatigue, Flee and Withdraw affordability/escape/free edge escape, AI longest affordable
prefix with valid commands, unchanged attack/Footwork/Rotation/incoming/regeneration costs, discounted
movement changing Dodge at once, and hash sensitivity without bit overlap plus unchanged pins. T43 adds shared
parser and launch construction checks for all 256 capability subsets, including mask 255; a launch-built move
comparison; an App movement through `App::run` followed by R/N persistence and fresh-resource checks; and a
headless scene test that verifies the discounted FAT line, execution agreement and the MELEE BUDGET NO-to-YES
fatigue boundary. Production rendering and layout are unchanged.

Seed-1 headless outcomes recorded after T43 (Flat / Hills): Pathfinder alone, `Victory / Escape`, 3 rounds,
`9cca6ef915788869` / `Victory / Escape`, 4 rounds, `6c00a08d1c11386d`; all eight capability options,
`Victory / Escape`, 3 rounds, `96886e87d9af4633` / `Victory / Elimination`, 4 rounds, `409a61a4ec43cbb5`.
Replay coverage compares complete commands and events from two runs, requires successful command application,
and bounds termination at 10,000 commands. T42 validation remains historical evidence: `cargo test -p bb_rules
--test pathfinder --locked --offline` (18 passed), `cargo test --workspace --locked --offline` (759 passed,
0 failed), and a clean `git diff --check`. T43 validation: `cargo test --workspace --locked --offline` passed
(764 tests, 0 failed), `cargo build --workspace --locked --offline` passed, and `git diff --check` was clean.
Native visual inspection was not performed; the scene behavior is verified with headless renderer tests.

Deferred explicitly: scenario-authored assignments, Pathfinder AP discounts, perk
learning, dedicated capability indicators, and any parity claim. Partial coverage only. The prior T42 statement
that there was no playable assignment is retained as historical pre-T43 evidence. Statements that every
attempted step costs `MOVE_FATIGUE` describe capability-free units on Ground only (the T43-era state); since T48 a Swamp destination costs `terrain_move_fatigue` (twice the Ground value), so they are no longer accurate there.

## Damage arithmetic bounds (T44)

`resolve_attack` now computes the inclusive width of every ordered damage range in `u64`:
`u64(dmg_max) - u64(dmg_min) + 1`. It adds the existing modulo sample to `dmg_min` in `u64` and converts only
the final in-range value to `u32`. This supports `0..=0`, `u32::MAX..=u32::MAX`, the full width `0..=u32::MAX`
(`2^32` possible values), and narrow ranges ending at `u32::MAX`. Every successful hit still consumes exactly
one damage draw and uses the existing modulo sampling without retries. For inputs whose arithmetic previously
fit, damage values, event streams, RNG consumption and hashes are unchanged. Reversed ranges are not validated
by this task.

`soak` retains `absorbed = min(armour, damage)` and the existing conditional `damage / 10` leak whenever
absorption is positive. It sums post-armour loss and leak in `u64`, preserving the mathematical result when it
fits and saturating returned `hp_loss` to `u32::MAX` when it exceeds the representation. Rolled damage and
absorbed armour are not clamped. This HP-loss saturation is a representation choice inside the existing armour
model **APPROXIMATION**; it makes no original-fidelity claim and does not change the armour provenance above.

Both ordinary `Command::Attack` and opportunity attacks call the shared corrected resolver. A hit for
`u32::MAX` against a defender at `u32::MAX` HP with one armour point now reports saturated would-be loss
`u32::MAX` and kills through the existing death path rather than wrapping to a small nonlethal loss. With
unused Nine Lives, that same saturated would-be loss enters the existing survival path: `Event::Attacked`
reports actual HP lost (`u32::MAX - 1`), the defender remains alive at 1 HP, and the regular post-survival
morale handling continues. Event ordering and the one hit draw plus one damage draw remain unchanged; later
morale/death processing still consumes its established draws.

Since T45 an effective Indomitable stance halves the sampled damage after this sampling and before `soak` (see "Indomitable"); the T44 statements describe stance-free attacks.

Failing-first evidence was captured before either production arithmetic change. The initial debug command
`cargo test -p bb_rules --test damage_arithmetic --locked --offline` ran the soak and full-width attack
regressions: **0 passed, 2 failed**. `soak_saturates_overflowing_partial_absorption` panicked at
`crates/bb_rules/src/rules.rs:125:19` with `attempt to add with overflow`; `full_width_damage_range_samples_the_draw_low_word`
panicked at `crates/bb_rules/src/rules.rs:365:62` with the same debug overflow message. An expanded pre-fix run
had five failures: those two cases plus the max-singleton lethal hit, Nine Lives max hit and opportunity
full-width hit, all at the corresponding shared arithmetic sites. This is retained as historical failure
evidence alongside the passing validation below.

Captured initial debug output (thread identifiers omitted):

```text
running 2 tests
test soak_saturates_overflowing_partial_absorption ... FAILED
test full_width_damage_range_samples_the_draw_low_word ... FAILED

---- soak_saturates_overflowing_partial_absorption stdout ----
panicked at crates/bb_rules/src/rules.rs:125:19:
attempt to add with overflow

---- full_width_damage_range_samples_the_draw_low_word stdout ----
panicked at crates/bb_rules/src/rules.rs:365:62:
attempt to add with overflow

test result: FAILED. 0 passed; 2 failed
```

Focused tests in [`crates/bb_rules/tests/damage_arithmetic.rs`](../../../crates/bb_rules/tests/damage_arithmetic.rs)
cover zero damage, zero armour, full absorption, ordinary and overflowing partial absorption; full-width,
zero/max singleton and narrow upper-bound damage samples; shared ordinary/opportunity resolution; exact event
values and RNG advancement; max-HP lethal handling with one armour; and Nine Lives actual-loss reporting and
survival. Fixtures use distinct passable map hexes and keep current HP, armour, AP and fatigue within their
Stats maxima.

Validation (actual, T44):
`cargo test -p bb_rules --test damage_arithmetic --locked --offline` passed (7 tests);
`cargo test -p bb_rules --test damage_arithmetic --release --locked --offline` passed (7 tests);
`cargo test --workspace --locked --offline` passed (771 tests, 0 failed); `git diff --check` was clean. No
`docs/source/**` fidelity or behavior catalog was changed.


## Indomitable (headless stance, partial UC-COMBAT-016 coverage)

**Catalog statement** ([UC-COMBAT-016](../../source/10-tactical.md), public catalog copy, not runtime-confirmed):
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
- Focused legal encounters in [`crates/bb_rules/tests/indomitable_ai.rs`](../../../crates/bb_rules/tests/indomitable_ai.rs)
  demonstrate real AI activation and its following attack for both factions. The scripted launch encounter in
  `terrain_presets.rs` separately verifies actual halving (a 20-damage hit becomes 10 for the stance holder; the
  twin without it takes 20) on both terrains. Stock seed-1 smoke did not activate the stance; its repeated runs
  still validate deterministic capability assignment and bounded play.
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

Tests: `crates/bb_rules/tests/indomitable.rs` (25) plus one `App` log
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
- **Catalog-stated:** the 4-AP swamp step ("Murky swamp water 4 AP") in [UC-COMBAT-005](../../source/10-tactical.md#uc-combat-005--movement-ap-and-fatigue-costs),
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
in [UC-COMBAT-005](../../source/10-tactical.md#uc-combat-005--movement-ap-and-fatigue-costs); the light-snow
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

`scenario::TerrainPreset::Swamp` is available through `scenario::battle(seed, preset)` and the existing
launch constructors. `battle` configures both layers using `ElevationMap::set` and `TerrainMap::set`, with
no RNG draws. Its exact hand-authored layout is an **APPROXIMATION**: elevation is zero on all 61 map hexes;
each passable hex whose `q` is -1, 0 or 1 is Swamp; every other hex is Ground, including obstacles. Units,
stats, placements, obstacle behavior and initial RNG remain identical across presets. Flat and Hills
construction and their pinned hashes remain unchanged. The Swamp terrain map contributes through the
existing terrain hash encoding.

Movement example: P1's initial move from `(-3,3)` to `(-1,3)` follows `(-2,3)` Ground then `(-1,3)` Swamp.
The shared movement planner previews 6 AP (Ground 2 + Swamp 4), 6 fatigue, 3 AP remaining and no remaining
basic-attack budget. With Pathfinder, the same route and 6 AP leave 3 fatigue. Applying the command spends
the previewed AP and fatigue in either case. The Swamp destination's 4-AP cost is catalog-stated in
[UC-COMBAT-005](../../source/10-tactical.md#uc-combat-005--movement-ap-and-fatigue-costs), but original-runtime
parity remains unconfirmed. The layout, fatigue accounting, destination charging and Pathfinder interaction
are **APPROXIMATION**; the example verifies the accepted slice rules only.

Historical T50 behavior: `TerrainPreset::parse` and `scenario::parse_args` accepted only `flat` and `hills`,
and the parser and `headless_smoke` assertions recorded `--terrain swamp` rejection with status 2 and the
then-current diagnostic. T52 replaces those rejection assertions with positive parsed-launch checks. At T50
native Swamp rendering, overlay and legend were also deferred; T51 implements them. T49's proposed launch and
renderer integration was rejected; its review case was open at T50: a hovered enemy at `(1,-4)` can place
`HIT` text over a legend at `y=80`. T51 addresses and tests that collision.

Tests in [`crates/bb_rules/tests/swamp_preset.rs`](../../../crates/bb_rules/tests/swamp_preset.rs) cover the
exact layers, deterministic construction and initial RNG equality, unchanged legal placements and Flat/Hills
hash pins, case-sensitive Swamp parsing, last-value-wins and smoke acceptance. T52 replaced T50's parser-rejection
assertions with positive parsed-launch checks. `parsed_swamp_smoke_runs_match_in_process_replays_for_launch_capabilities`
compares the CLI with replays for no capabilities, Pathfinder and all nine capabilities. The file also covers
all existing launch constructors including all nine capabilities, preview and execution agreement for the
mixed-terrain move with and without Pathfinder, and complete bounded AI replays for seeds 1, 7 and 23 without
capabilities and with Pathfinder. Each replay validates commands through `BattleState::apply`, compares complete
command and event streams, final hashes and RNG, and checks terrain stability throughout. T50 was an intermediate
programmatic rules milestone; T52 adds CLI launch support. This does not complete the source catalog.

Actual T50 validation: `cargo test -p bb_rules --test swamp_preset --locked --offline` passed (5 tests);
`cargo test --workspace --locked --offline` passed (854 tests, 0 failed); `git diff --check` was clean.

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

Focused validation: [`crates/bb_rules/tests/recover_action.rs`](../../../crates/bb_rules/tests/recover_action.rs)
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

Validation: [`recover_action.rs`](../../../crates/bb_rules/tests/recover_action.rs) gains query purity and execution
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

### AI Recover policy (T55)

The deterministic AI considers Recover only inside its existing nonempty-adjacent-target branch. This is
the T55 policy snapshot: Withdraw remains first, then Flee; both keep priority over ordinary actions. For an
ordinary actor the T55 order was Indomitable, Adrenaline, then an affordable Attack. T66 inserts eligible
Stunning Blow selection before that branch; if no eligible target exists, the previous Indomitable, Adrenaline,
Attack, Recover and approach policy runs unchanged. The Recover rule below remains current.

- current fatigue is positive;
- checked `fatigue + ATTACK_FATIGUE` overflows or exceeds `fatigue_max`, so the adjacent attack is
  unaffordable;
- the shared `recover_availability(actor)` query succeeds, including the existing active, present,
  controllable, running-battle and 9-AP checks; and
- checked `(fatigue / 2) + ATTACK_FATIGUE` fits `fatigue_max`.

If any check fails, planning falls through to the existing approach choice and then explicit `EndTurn`.
No adjacent target, 8 AP, zero fatigue, or a fatigue maximum too small for an attack after halving does
not select Recover. No percentage threshold, planning simulation, battle clone or RNG draw was added.
Planning remains deterministic and pure and returns exactly one command during a running battle.

Recover is a command that ends the turn. Applying it uses the existing rule execution, removes the actor from
the current queue and advances the schedule; that actor receives no additional command in the same
plan. Standard fatigue regeneration can make an affordable Attack available on a later fresh turn.
The progress bound treats Recover and explicit `EndTurn` as two distinct final commands: at most seven
other commands leave the turn active before one final command under the existing 9-AP plus Berserk-refund
bound. Recover is not counted as a continuing action followed by EndTurn.

**Provenance and limits.** This AI selection policy is an **APPROXIMATION inspired by
UC-COMBAT-020/065**, without original-AI parity claims. The command's RECONSTRUCTED fatigue-halving
effect and catalog-stated 9 AP/whole-turn behavior retain the T53/T54 limits above. Recover perk/
capability gating and recovery during approach movement remain deferred. This is an intermediate
milestone, not completion of `docs/source/**`.

Focused validation: [`recover_ai.rs`](../../../crates/bb_rules/tests/recover_ai.rs) covers both factions,
9 AP versus 8, exact attack-fatigue capacity versus one over, checked `u32::MAX` boundaries, odd fatigue,
zero fatigue, insufficient post-halving capacity, no adjacent target, Withdraw/Flee and existing
Indomitable/Adrenaline/Attack priority. Repeated planning is checked for purity across units, queue,
round, outcome, terrain, state hash and RNG. A selected Recover is applied through turn completion, then
the actor's affordable Attack is confirmed on its next fresh turn after standard regeneration. The
existing `indomitable_ai.rs` overflow regression now expects Recover when halving restores
the attack budget. The seed-1 test checks deterministic complete command/event traces, command validity
and a per-turn bound for Flat, Hills and Swamp with no capabilities, Pathfinder and all nine capabilities.
Per-turn bounds count Recover and explicit EndTurn as distinct final choices.

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

Actual T55 focused validation: `cargo test -p bb_rules --test recover_ai --locked --offline` passed
(8 tests); `cargo test -p bb_rules --test fatigue_affordability --locked --offline` passed (5 tests);
`cargo test -p bb_rules --test indomitable_ai --locked --offline` passed (7 tests);
`cargo test -p bb_rules --test terrain_presets --locked --offline` passed (51 tests); and
`cargo test -p bb_rules --test swamp_preset --locked --offline` passed (6 tests).
`cargo test --workspace --locked --offline` passed (901 tests, 0 failed); `git diff --check` was clean.
`cargo build --workspace --locked --offline` succeeded.

## Melee attack preview (T56; headless UC-COMBAT-011 / UC-UI-001 foundation)

`rules::AttackPreview` derives `Clone`, `Debug`, `PartialEq` and `Eq` and exposes
`attacker`, `target`, `ap_cost`, `fatigue_cost`, `hit_chance`, `damage_min` and `damage_max`.
`BattleState::preview_attack(attacker, target) -> Result<AttackPreview, RuleError>` describes an
ordinary melee attack that is legal in the current snapshot. The costs are the accepted
`ATTACK_AP` / `ATTACK_FATIGUE` constants. The chance is `BattleState::hit_chance` from the current
state, before costs, incoming fatigue or attack-triggered passive/morale transitions.

**Shared validation and snapshot.** One private `plan_attack` validates both the query and ordinary
`Command::Attack`. Its order stays: battle/active actor, retreating/Fleeing control gates, target
validity (present hostile unit), adjacency, AP, then checked `u32` fatigue affordability. It adds no
error variant or special policy for malformed coordinates, queues or inverted damage stats. Attack
execution uses the plan's costs and chance while the shared resolver retains sampling and downstream
effects; opportunity attacks still bypass ordinary validation and costs. Preview emits no events,
consumes no RNG and mutates no battle, unit, terrain, passive or stance state. It is not a reservation:
later `Attack` submission revalidates current state.

**Conditional pre-armour range.** `damage_min..=damage_max` is the inclusive value range supplied to
armour soak **conditional on a hit**, before armour processing. The endpoints begin with the
attacker's ordered `Stats.dmg_min` / `Stats.dmg_max`. The pure `damage_after_indomitable` helper
transforms both preview endpoints and each sampled hit value. An effective defender Indomitable stance
halves with integer flooring; zero is possible, and the hit still counts. This preserves the sampled-
damage interpretation, ordinary attack behavior and the opportunity-attack resolver path. Armour
soak, armour loss, Nine Lives, actual HP loss and later morale/passive transitions are outside this
preview contract. The query and range presentation are **APPROXIMATION**. The existing chance,
damage and Indomitable provenance is retained; none gains an original-runtime confirmation from this
API. At T56, native display of the range and HP-loss/armour-loss/survival/morale prediction remained
deferred; T57 adds the conditional range to the hover panel, while those loss and survival predictions
remain deferred.

**Focused validation.** `crates/bb_rules/tests/attack_preview.rs` covers both factions and event
agreement using the existing chance calculation with representative morale, surround, elevation,
Dodge and Fast Adaptation states; query/command errors for battle over, inactive, Fleeing and retreating
actors, invalid/self/allied/dead/escaped targets, nonadjacency and resource failures; simultaneous
failures pinning precedence; exact AP/fatigue limits and checked `u32::MAX` overflow; fixed, variable,
zero, odd, maximum and full ordered damage ranges with effective and ineffective Indomitable; hit and
miss event values; repeated query purity including passive/stance/terrain state; and full event, unit,
queue, outcome, hash and RNG equality for ordinary, lethal and Nine Lives encounters.

Actual T56 validation: `cargo test -p bb_rules --test attack_preview --locked --offline` passed
(7 tests); `cargo test --workspace --locked --offline` passed (908 tests, 0 failed); `git diff --check`
was clean. This remains a headless foundation and an intermediate milestone, not completion of
`docs/source/**`.

## Native attack hover information (T57; partial UC-COMBAT-011 / UC-UI-001 presentation)

On Flat, Hills, Swamp and independently configured mixed terrain, the native scene offers attack information only
for a hovered adjacent present enemy of the selected active controllable player in a running battle, while no
targeting mode is open. A present enemy occupies its hex, so the movement preview query rejects that hover and
cannot share `PREVIEW_BOX` with the attack rows. Rotation and Footwork targeting retain their existing rows;
completed battles continue to use the outcome banner.

The renderer calls `BattleState::preview_attack` for the hovered attacker/target pair. A valid result produces
four scale-2 lines over `PREVIEW_BG`, at the existing 16-pixel row spacing:

1. `ATTACK <name>: HIT <chance>%` in yellow;
2. `COST <ap_cost> AP <fatigue_cost> FAT` in `PREVIEW_COST`;
3. `DAMAGE ON HIT <damage_min>-<damage_max>` in `PREVIEW_COST`;
4. `BEFORE ARMOUR. MISSES DEAL 0.` in `PREVIEW_NOTE`.

The valid chance, costs and endpoints come from `AttackPreview`; the renderer duplicates no combat formula. For
`NotEnoughAp` or `TooFatigued`, it retains the target and current `hit_chance` row and uses `PREVIEW_WARN` for
`UNAVAILABLE: NOT ENOUGH AP` or `UNAVAILABLE: TOO FATIGUED`, respectively. These invalid plans omit both cost and
damage rows. Other query errors suppress the attack strip. The preview is a display query only and changes no
rules state or RNG.

The damage band is conditional on a hit and shows the current rules endpoints after effective Indomitable
integer flooring, including a possible zero, before armour processing. It does not claim HP loss; misses deal
zero. Armour loss, HP loss, survival and Nine Lives are not forecast. The range presentation is an
**APPROXIMATION** with no original-UI parity or new original-runtime confirmation. Source provenance for chance,
damage and Indomitable remains as previously documented.

On maps without visible Swamp, the existing above-target `HIT n%` text remains in place while the full attack
panel is also shown. On visible-Swamp maps the above-target text stays absent, including at the rejected T49
collision coordinate `(1,-4)`. The four rows fit within `PREVIEW_BOX` and remain disjoint from terrain legends,
height/Swamp badges, the panel, error area and controls. These positions and wording are **APPROXIMATION**. No
layout, input, rules, event, hash or RNG changes are part of T57.

Focused headless scene coverage in `render.rs` checks row values against `preview_attack` on Flat, Hills, Swamp and
independently configured mixed terrain; both resource warnings, exact budgets and checked fatigue overflow;
live Indomitable changes, odd endpoints, zero and `0..=u32::MAX`; allied, dead, escaped and nonadjacent targets;
inactive selections, enemy turns, Fleeing/retreating actors, targeting modes and completed battles; coexistence
with the retained all-Ground `HIT` label, suppression on visible Swamp, T49 collision clearance, row dimensions,
supported glyphs, maximum numeric widths, H/Z coverage, mutual exclusion with movement/targeting/outcome rows,
and repeat-render state/RNG purity.

Actual T57 validation: `cargo test -p bb_game --bin bb_game --locked --offline` passed (272 tests);
`cargo test --workspace --locked --offline` passed; `cargo build --workspace --locked --offline` succeeded;
`git diff --check` was clean. Native visual inspection was unavailable because neither `DISPLAY` nor
`WAYLAND_DISPLAY` was set; headless scene tests provide renderer coverage.
This is partial UC-COMBAT-011/UC-UI-001 presentation and an intermediate milestone, not completion of
`docs/source/**`.

## Two-hex melee reach foundation (T58; partial UC-COMBAT-051 coverage)

`unit::MeleeReach` (re-exported at the crate root; `Clone`, `Copy`, `Debug`, `Default`, `PartialEq`, `Eq`) has
`Adjacent` (the default) and `TwoHex`. `Unit.melee_reach` starts `Adjacent` for every unit; T58 itself added no
scenario, launch option or native control granting `TwoHex` (T59 adds the approximate player-only `--two-hex` launch
option, see below), so existing scenarios, events, RNG streams and hash pins are unchanged.

**One geometry query.** `Unit::melee_reaches(at: Hex) -> bool` is pure: `Adjacent` accepts distance 1, `TwoHex`
accepts distances 1 and 2, and distance zero is excluded. It uses widened arithmetic, so extreme coordinates are
plain geometry rather than an overflow. Presence, hostility, zones of control and resources stay with the caller.

**Query/execution agreement.** `BattleState::legal_targets` (present enemies in id order) and the shared private
`plan_attack` behind `preview_attack` and `Command::Attack` both call it, so the target list, preview and execution
cannot drift. Validation order is unchanged: actor/control gates, target validity, reach, AP, checked fatigue. The
out-of-reach error stays `RuleError::NotAdjacent`; for ordinary Attack it now means "outside `melee_reaches`".
Rotation and Footwork still require distance exactly 1 under the same variant. Attack costs (`ATTACK_AP`,
`ATTACK_FATIGUE`), damage resolution, passives and the opportunity-attack path are unchanged.

**Hit modifiers.** `hit_chance` uses the same predicate for its engaged-pair gate. For a present hostile pair in reach
the existing formulas apply unchanged: 5 points per present same-side unit adjacent to the defender beyond the first,
plus elevation and Fast Adaptation. A two-hex attacker is not at distance 1, so it is not counted as an adjacent
ally: at distance two, zero or one adjacent allies add 0 and two add 5, whereas an adjacent attacker counts itself.
Out of reach, no engaged modifier applies.

**Zones of control are separate.** `enemy_controllers_at`, the movement preview's `first_threat` and the departure
reactions still use distance 1 only, whatever the controller's reach. A two-hex controller at distance two produces
no controller, warning or reaction; one at distance one reacts exactly as an `Adjacent` controller would (same events,
costs, RNG and passive handling).

**AI (T58/T59 behavior).** `ai::plan_turn` already selects via `legal_targets`, so an actor with `TwoHex` reach and
an affordable attack attacks a distance-two target without moving, keeping the weakest-hp/lowest-id order and the
Withdraw/Flee priorities. At T58/T59, if the attack was not affordable, approach planning still sought hexes adjacent
to an enemy; T60 updates that branch below. Every AI attack passes ordinary command validation.

**State hash.** `TwoHex` is flag bit 54 in `state_hash`; `Adjacent` contributes zero. Earlier bit assignments and all
default hashes (Flat, Hills, Swamp) are unchanged.

**Provenance.** OBSERVED/catalog-stated (UC-COMBAT-051, not runtime-confirmed): a polearm can attack two hexes away
and a melee-weapon unit still exerts adjacent ZoC. **APPROXIMATION** (slice choices): the two-value enum, the per-unit
capability instead of weapon profiles, the hash bit, reuse of the generic Attack costs, no obstruction, line-of-sight
or height restriction (a TwoHex attack passes over allies and obstacles), and composition with the existing surround,
elevation and Fast Adaptation modifiers. **Deferred at T58:** equipment and weapon profiles, polearm adjacent-attack
penalties and mastery, the spear Thrust +20%, equipment-based loadouts and reach-aware AI approach (T59 adds only
the approximate `--two-hex` launch option and `REACH 2` tag; T60 adds the limited approach policy below).
This is a range foundation, not a polearm or equipment system, and an intermediate milestone, not completion of
`docs/source/**`.

**Focused validation.** `crates/bb_rules/tests/melee_reach.rs` (13 tests) covers defaults and geometry (all map hexes,
extreme coordinates); both factions at distances 1-3 for both reaches with query, preview and execution agreement;
id ordering and absent/allied/dead/escaped targets; error precedence, exact AP/fatigue budgets and checked `u32::MAX`
overflow; attacking over an intervening ally and over an obstacle; elevation, Fast Adaptation and zero/one/two-ally
surround at distance two against preview and `Attacked` chances; ZoC distance two versus distance one (identical
events) and a Nine Lives passive after a reaction; AI attack-before-approach, unaffordable approach, weakest-target
and Withdraw/Flee priority; query purity and previewed-versus-unpreviewed equality; and hash behaviour with the Flat,
Hills and Swamp pins. The Swamp pin was captured with the reach bit present; since `Adjacent` contributes zero it is
unchanged by construction (Flat and Hills pins predate T58).

Actual T58 validation: `cargo test -p bb_rules --test melee_reach --locked --offline` passed (13 tests);
`cargo test --workspace --locked --offline` passed (925 tests, 0 failed); `git diff --check` was clean.

## Two-hex launch loadout (T59; partial UC-COMBAT-051 coverage)

**Launch.** `LaunchArgs.two_hex` (default false) is parsed by the shared value-less `--two-hex` option of
`scenario::parse_args`, so both binaries accept it. Repeats are harmless, argument order is free, and it composes
independently with `--seed`, the three terrain presets and the nine capability options (all 512 subsets). Every
existing `LaunchArgs` literal and compatibility constructor supplies `two_hex: false`.

**Assignment.** `launch_battle_for` sets `MeleeReach::TwoHex` on all player units when selected; enemies keep
`Adjacent`. Stats, resources, positions, the queue, terrain layers and the initial RNG are those of
`battle(seed, terrain)`; the only state difference is the reach (flag bit 54 of `state_hash`), so with the flag
unset all scenarios, events, RNG streams and hash pins are unchanged. Scenario construction and rendering stay
RNG-pure. No reach, hashing, cost or AI rule changed: the client uses `legal_targets`/`preview_attack` through the
existing input path and duplicates no range or combat rule.

**Native `App`.** `App.two_hex` is stored from `LaunchArgs`, reproduced by `launch_args` and therefore by `R`/`N`.
Restart restores initial resources and temporary state (any scripted reach is rebuilt from the launch arguments)
while preserving reach, terrain, the other flags and the `H`/`Z` preferences.

**Marker.** `render::Painter::reach_marker` draws a scale-1 `REACH 2` tag for every present unit with
`MeleeReach::TwoHex`, of either faction, right aligned eight pixels inside the window (x 1231, 41 px wide) on the
name/morale line at row top + 3, in the dedicated colour `[0.55, 0.78, 1.0]`. Adjacent, dead and escaped units draw no
tag. The tag starts at x 1231; the widest stock morale label (`MORALE CONFIDENT 110%` after a two-character name)
ends at x 1182, and no other indicator uses that line (ADAPT y+21, pending y+36..51, DODGE/NINE LIVES/INDOMITABLE
y+51). No existing panel content or button moved.

**Input.** Hover on an enemy at distance two from the active player, even behind an intervening ally, shows the
`attack_preview_lines` rows derived from `preview_attack`; the existing click handler submits `Command::Attack`, which
the rules accept without moving first, producing the same state, RNG and events as the direct command. The
adjacent-only twin shows no attack information and its click leaves the state unchanged with a rules error;
distance-three targets stay out of reach. Resource-unavailable rows (`NOT ENOUGH AP`, `TOO FATIGUED`), modal isolation,
the click guard and targeting precedence are unchanged and covered at distance two.

**Provenance.** The two-hex reach is catalog-stated (UC-COMBAT-051, not runtime-confirmed). **APPROXIMATION:** the
`--two-hex` flag, the player-only assignment and the `REACH 2` marker. All T58 limits remain: generic costs, no
obstruction/line-of-sight/height restriction, adjacent attacks unchanged (no polearm adjacent penalty), ZoC and
opportunity attacks at distance 1 only, reach-unaware AI approach as of T59 (updated by T60 below), and no weapon,
equipment, mastery or Thrust system.
This is an approximate playable loadout and an intermediate milestone, not completion of `docs/source/**`.

**Measured seed-1 headless results** (binary `headless_smoke`; identical to the in-process replays):

| Terrain | `--two-hex` alone | `--two-hex` + all nine capabilities |
| --- | --- | --- |
| Flat | Victory/Escape, 2 rounds, `2202abb4c514f685` | Victory/Escape, 2, `214421f5a7ff225f` |
| Hills | Victory/Escape, 3, `d523f58695a2ad96` | Victory/Elimination, 3, `ec982429bed34670` |
| Swamp | Victory/Escape, 4, `1b928b9fc5eb363e` | Victory/Elimination, 4, `dec7c86aa2062269` |

For comparison the unchanged defaults are Flat `dfce9b8d74bcaf93` (3 rounds), Hills `d0b704f0294173be` (4) and Swamp
`423c077697a25dac` (4); all nine capabilities without `--two-hex` give Flat `bd7679eafc17ec03`, Hills
`87444e820db722a5` and Swamp `6ad3be781e358f1e`.

**Focused validation.** `crates/bb_rules/tests/two_hex_loadout.rs` (9 tests): parsing defaults, repeats, ordering,
near-miss spellings and composition with seed, terrain, each flag, all 512 subsets and the combined loadout; player-only
assignment with unchanged stats, resources, positions, queue, terrain and RNG; default pins and compatibility
constructors; complete repeated AI replays on all three presets (alone and combined) with matching commands, events,
state and RNG, validity and a command bound; the measured table pinned and compared with the real binary output; and a
scripted behind-an-ally encounter where the AI executes a distance-two attack without moving, the adjacent-only twin is
rejected, and distance three stays out of reach. `bb_game` tests: App launch/parse, R/N persistence with fresh
resources and preferences, real hover and click equal to the direct command, twin and distance-three rejection,
unavailable rows, modal/guard/targeting precedence, and frame purity; scene tests for the tag on both factions,
excluded rows, active highlighting, all five morale states, combined indicators, text fit, supported glyphs, that
only the tag geometry is added, and purity.

Actual T59 validation: `cargo test --workspace --locked --offline` passed (949 tests, 0 failed; `bb_game` 287);
`cargo build --workspace --locked --offline` succeeded; `git diff --check` was clean. Native visual inspection was
unavailable because neither `DISPLAY` nor `WAYLAND_DISPLAY` was set.

## TwoHex approach policy (T60; partial UC-COMBAT-020/051 coverage)

**Shared geometry.** `MeleeReach::reaches(from, to)` is public and pure. It uses widened coordinate arithmetic and
excludes distance zero; `Unit::melee_reaches(at)` delegates to it, preserving both reaches and extreme-coordinate
safety. The AI uses this same predicate for hypothetical positions.

**Candidate selection.** Adjacent actors keep their existing enemy-adjacent candidate generation and ordering.
TwoHex actors use the existing BFS distance list rooted at the actor. For each present hostile in `state.units`
iteration order, every listed position from which TwoHex reaches that enemy is a candidate. The BFS origin is
eligible despite being occupied, so standing in reach contributes a zero-step candidate. The AI selects the smallest
BFS step distance and replaces its current choice only for a strictly smaller distance. Equal distances therefore
keep the first hostile in unit iteration order, then the first candidate in stable BFS discovery order. Occupied
positions other than the origin, obstacles and off-map positions do not appear in the BFS list.

**Affordability and priorities.** Withdraw, Flee, the existing Attack/Indomitable/Adrenaline choices and Recover
remain ahead of approach. If an actor is already in TwoHex range but cannot afford an Attack and has no applicable
skill or Recover choice, its zero-step origin candidate leads to EndTurn; it does not move into adjacency merely
because the current attack is unaffordable. For another candidate, the AI uses the existing deterministic BFS path
and `affordable_prefix`, emitting the longest prefix accepted by the shared step AP, terrain and checked fatigue
costs (including Pathfinder). If no step is affordable, it emits EndTurn. It does not rank candidates by terrain
cost, reserve AP or fatigue for a following attack, or score tactical safety.

**Provenance and limits.** Candidate ordering and shortest-step selection are an **APPROXIMATION** inspired by
UC-COMBAT-020's catalog statement that the AI spends AP to approach and UC-COMBAT-051's catalog-stated two-hex reach.
The catalog does not establish this exact policy; both rows are not runtime-confirmed. The `--two-hex` flag and
player-only loadout remain approximations too. Reach-unaware approach describes the historical T58/T59 behavior;
T60 adds this limited TwoHex branch. Terrain-cost optimization, tactical safety, weapon profiles, attack-budget
reservation and broader tactics remain deferred. This is not original-AI parity or product completion.

**Focused validation.** `crates/bb_rules/tests/melee_reach_ai.rs` (6 tests) verifies both factions approach from
distance three to two and select an affordable Attack on the next plan through an intervening ally; an in-range
unaffordable actor ends its turn while legal skill and Recover choices remain; occupied candidates, unreachable
destinations, enemy and BFS tie order, and no-target fallback; longest affordable prefixes; uphill, Swamp, Pathfinder
and checked fatigue boundaries; accepted movement previews/execution and repeated-plan purity across units, queue,
round, terrain, outcome, state hash and RNG. `crates/bb_rules/tests/melee_reach.rs` verifies the extracted geometry
against the Unit query over map hexes and extreme coordinates, preserves the Adjacent unaffordable-approach comparison,
and retains Withdraw/Flee/attack priorities. `two_hex_loadout.rs` checks complete repeated replays and compares
in-process results with the actual `headless_smoke` binary.

**Current measured seed-1 results** (actual headless binary and in-process replay agree):

| Terrain | `--two-hex` alone | `--two-hex` + all nine capabilities |
| --- | --- | --- |
| Flat | Victory/Escape, 2 rounds, `2202abb4c514f685` | Victory/Escape, 2, `4fd6ba9dd1767299` |
| Hills | Victory/Escape, 3, `d523f58695a2ad96` | Victory/Elimination, 3, `ec982429bed34670` |
| Swamp | Victory/Escape, 4, `1b928b9fc5eb363e` | Victory/Elimination, 4, `dec7c86aa2062269` |

The Flat + all-nine TwoHex replay is the only observed replay changed from T59. The historical T59 table above is
preserved as captured evidence. Initial launch state, default/no-`--two-hex` hashes and non-TwoHex replay pins are
unchanged.

    cargo test -p bb_rules --test melee_reach_ai --locked --offline
    cargo test -p bb_rules --test melee_reach --locked --offline
    cargo test -p bb_rules --test two_hex_loadout --locked --offline
    cargo test --workspace --locked --offline

Actual T60 validation: the focused tests and workspace passed; the current measured values are pinned by the replay
test. This task remains an intermediate milestone, not completion of `docs/source/**`.

## Stun effect (T61; headless foundation, partial UC-COMBAT-028 / UC-COMBAT-016 coverage)

`BattleState::apply_stun(unit: u32) -> Result<Vec<Event>, RuleError>` is a **system-effect API**, not a player
`Command`, and (as of T61) no skill, weapon, AI policy or native control inflicts stun yet (T62 qualifies this: only the headless `Command::StunningBlow` skill does; weapons and AI policy still do not. T64 adds native controls that expose the skill for programmatically granted capability, and T66 later adds bounded AI selection). It is the first status effect and is
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

Provenance. Catalog-stated ([UC-COMBAT-028 and the UC-COMBAT-007 ZoC text](../../source/10-tactical.md), not runtime-confirmed):
a stunned unit skips its turn and exerts no ZoC; ([UC-COMBAT-016](../../source/10-tactical.md)): Indomitable grants
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
a hit is only *inspired by* the catalog's mastered-mace behaviour ([UC-COMBAT-050 / 068](../../source/10-tactical.md)); no
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
limited AI selection below). Tooltips, equipment, mastery, stun chances and opportunity-attack stun remain deferred.

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

At T65, `ai::plan_turn` did not cast Stunning Blow. The historical T65 replays below prove repeatability and loadout
composition for that task only; T66 adds limited deterministic AI selection, documented below.

Seed-1 measured headless outcomes `(outcome, reason, rounds, hash)` for Stunning-Blow-only and combined (all existing
flags, including `--two-hex`) loadouts:

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
show the boosted range while Frenzy is effective, and return to base damage after expiration. No new renderer behavior,
indicator or combat-log message was added at T68; T69 adds the native row marker below, while combat-log text remains
deferred.

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
