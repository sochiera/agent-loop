//! Headless Ground/Swamp terrain with destination-dependent walking costs. The 4-AP
//! swamp step is catalog-stated in UC-COMBAT-005 ("Murky swamp water 4 AP"), not
//! runtime-confirmed; the fatigue values (Ground 2, Swamp 4, halved by Pathfinder to
//! 1 and 2), destination charging, Pathfinder interaction, additive composition with
//! the uphill surcharge and the storage/hash design are an APPROXIMATION. Fixtures
//! clear the stock scenario (`alive = false`) and place only the units they need on
//! distinct, passable hexes, mostly on the obstacle-free lane `r = -2`; resources stay
//! within Stats maxima. Skill and defense are 0 and every hit does 1 damage unless a
//! test says otherwise; reaction seeds mirror the first SplitMix64 draw.

use bb_rules::ai::plan_turn;
use bb_rules::hex::{Hex, bfs_distances, bfs_path, hex, map_hexes};
use bb_rules::rules::{
    self, ATTACK_AP, ATTACK_FATIGUE, DEFENDER_HIT_FATIGUE_GAIN, DEFENDER_MISS_FATIGUE_GAIN, ELEVATION_HASH_TAG,
    FOOTWORK_AP, FOOTWORK_FATIGUE, MOVE_AP, MOVE_FATIGUE, PATHFINDER_MOVE_FATIGUE, ROTATION_AP, ROTATION_FATIGUE,
    SWAMP_FATIGUE_FACTOR, SWAMP_MOVE_AP, TERRAIN_HASH_TAG,
};
use bb_rules::scenario::{self, TerrainPreset};
use bb_rules::terrain::MAX_ELEVATION;
use bb_rules::*;

const P1: u32 = 0;
const P2: u32 = 1;
const E1: u32 = 3;

const FMAX: u32 = 100;

const G: TerrainKind = TerrainKind::Ground;
const S: TerrainKind = TerrainKind::Swamp;

// --------------------------------------------------------------------- fixtures

/// Only `placed` units are present; the queue follows the given order, so the
/// first entry is the active, started unit (9 AP, no fatigue).
fn board(placed: &[(u32, Hex)]) -> BattleState {
    let mut s = BattleState::new(1);
    for u in &mut s.units {
        u.alive = false;
        u.turn_started = false;
        u.stats.melee_skill = 0;
        u.stats.defense = 0;
        u.stats.dmg_min = 1;
        u.stats.dmg_max = 1;
    }
    for &(id, pos) in placed {
        s.units[id as usize].alive = true;
        s.units[id as usize].pos = pos;
    }
    s.queue = placed.iter().map(|p| p.0).collect();
    let active = &mut s.units[placed[0].0 as usize];
    active.ap = 9;
    active.fatigue = 0;
    active.turn_started = true;
    assert_valid_fixture(&s);
    s
}

/// Present units stand on distinct, in-map, passable hexes within their Stats maxima.
fn assert_valid_fixture(s: &BattleState) {
    let present: Vec<&Unit> = s.units.iter().filter(|u| u.is_present()).collect();
    for (i, u) in present.iter().enumerate() {
        assert!(u.pos.in_map() && !u.pos.is_obstacle(), "unit {} on invalid hex {:?}", u.id, u.pos);
        assert!(u.hp <= u.stats.hp && u.armour <= u.stats.armour && u.fatigue <= u.stats.fatigue_max);
        assert!(u.ap <= u.stats.ap_max, "unit {} ap above its maximum", u.id);
        assert!(present[..i].iter().all(|o| o.pos != u.pos), "unit {} shares a hex", u.id);
    }
}

fn set_k(s: &mut BattleState, at: Hex, kind: TerrainKind) {
    s.ground.set(at, kind).unwrap();
}

fn set_h(s: &mut BattleState, at: Hex, height: u8) {
    s.elevations.set(at, height).unwrap();
}

fn snapshot(s: &BattleState) -> (u64, Rng, Vec<u32>, Vec<Unit>) {
    (s.state_hash(), s.rng.clone(), s.queue.clone(), s.units.clone())
}

fn first_draw(seed: u64) -> u64 {
    Rng::new(seed).next_u64() % 100
}

/// First seed whose first roll is below (`hit`) or at/above `chance`.
fn seed_with_first_roll(chance: u32, hit: bool) -> u64 {
    (0..10_000u64).find(|&s| (first_draw(s) < u64::from(chance)) == hit).unwrap()
}

/// Rig the next draw of `s` so the `attacker` hits (or misses) `target` at the current chance.
fn rig(s: &mut BattleState, attacker: u32, target: u32, hit: bool) {
    s.rng = Rng::new(seed_with_first_roll(s.hit_chance(attacker, target), hit));
}

fn moved_path(ev: &[Event]) -> Vec<Hex> {
    ev.iter()
        .filter_map(|e| match e {
            Event::Moved { path, .. } => Some(path.clone()),
            _ => None,
        })
        .flatten()
        .collect()
}

fn started(unit: u32) -> Event {
    Event::TurnStarted { unit }
}

/// Continue the FNV-1a 64 state `h` with little-endian words.
fn fnv_continue(mut h: u64, words: &[u64]) -> u64 {
    for w in words {
        for b in w.to_le_bytes() {
            h ^= u64::from(b);
            h = h.wrapping_mul(0x0000_0100_0000_01b3);
        }
    }
    h
}

/// Expected `(AP, fatigue)` of entering a hex: terrain base, uphill surcharge, unit capability.
fn step_cost(kind: TerrainKind, h0: u8, h1: u8, pathfinder: bool) -> (u32, u32) {
    let base_ap = if kind == S { 4 } else { 2 };
    let base_fatigue = if kind == S { 4 } else { 2 };
    (base_ap + u32::from(h1.saturating_sub(h0)), if pathfinder { base_fatigue / 2 } else { base_fatigue })
}

/// The mover and its foe at opposite ends of the lane `r = -2`; the destination is
/// three steps along `path` and never adjacent to the foe.
fn lane(actor: u32) -> (BattleState, [Hex; 3]) {
    if actor == P1 {
        (board(&[(P1, hex(-2, -2)), (E1, hex(4, -2))]), [hex(-1, -2), hex(0, -2), hex(1, -2)])
    } else {
        (board(&[(E1, hex(4, -2)), (P1, hex(-2, -2))]), [hex(3, -2), hex(2, -2), hex(1, -2)])
    }
}

// ------------------------------------------------------------------ map API

#[test]
fn default_map_is_all_ground_and_new_battles_are_all_ground() {
    let map = TerrainMap::default();
    assert!(map.is_all_ground());
    assert_eq!(map.entries().count(), 0);
    for h in map_hexes() {
        assert_eq!(map.get(h), Some(G));
    }
    assert_eq!(map.get(hex(5, 0)), None);
    assert_eq!(map.get(hex(-3, -3)), None);
    let s = BattleState::new(7);
    assert_eq!(s.ground, TerrainMap::default());
    for preset in [TerrainPreset::Flat, TerrainPreset::Hills] {
        assert!(scenario::battle(3, preset).ground.is_all_ground());
    }
}

#[test]
fn storage_is_canonical_and_ground_assignment_resets() {
    let hexes = [hex(2, -3), hex(-3, 1), hex(0, -2), hex(-3, 0), hex(3, 0)];
    let mut forward = TerrainMap::default();
    let mut backward = TerrainMap::default();
    for &h in &hexes {
        assert_eq!(forward.set(h, S), Ok(()));
    }
    for &h in hexes.iter().rev() {
        assert_eq!(backward.set(h, S), Ok(()));
    }
    assert_eq!(forward, backward, "insertion order does not matter");
    let listed: Vec<(Hex, TerrainKind)> = forward.entries().collect();
    let mut sorted = hexes.to_vec();
    sorted.sort_by_key(|h| (h.q, h.r));
    assert_eq!(listed, sorted.iter().map(|&h| (h, S)).collect::<Vec<_>>(), "axial order, Swamp only");
    assert!(!forward.is_all_ground());
    for &h in &hexes {
        assert_eq!(forward.get(h), Some(S));
    }
    // Repeating an assignment is idempotent.
    forward.set(hexes[0], S).unwrap();
    assert_eq!(forward, backward);
    // Ground removes the entry, so the canonical form returns to the default.
    for &h in &hexes {
        assert_eq!(forward.set(h, G), Ok(()));
        assert_eq!(forward.get(h), Some(G));
    }
    assert!(forward.is_all_ground());
    assert_eq!(forward, TerrainMap::default());
    // Ground on an untouched hex stores nothing.
    forward.set(hex(1, 1), G).unwrap();
    assert!(forward.is_all_ground());
}

#[test]
fn off_map_get_is_none_and_set_rejects_without_mutation_including_extreme_coordinates() {
    let off = [
        hex(5, 0),
        hex(0, -5),
        hex(4, 1),
        hex(-4, -1),
        hex(i32::MAX, 0),
        hex(0, i32::MAX),
        hex(i32::MIN, 0),
        hex(0, i32::MIN),
        hex(i32::MAX, i32::MAX),
        hex(i32::MIN, i32::MIN),
        hex(i32::MAX, i32::MIN),
        hex(i32::MIN, i32::MAX),
    ];
    let mut map = TerrainMap::default();
    map.set(hex(1, -2), S).unwrap();
    let before = map.clone();
    for at in off {
        assert_eq!(map.get(at), None, "{at:?}");
        for kind in [G, S] {
            assert_eq!(map.set(at, kind), Err(TerrainError::OffMap), "{at:?} {kind:?}");
            assert_eq!(map, before, "{at:?} {kind:?}");
        }
    }
    let mut empty = TerrainMap::default();
    assert_eq!(empty.set(hex(i32::MIN, i32::MIN), S), Err(TerrainError::OffMap));
    assert!(empty.is_all_ground());
}

#[test]
fn obstacles_may_carry_terrain_but_stay_impassable() {
    let obstacle = hex(0, 0);
    assert!(obstacle.is_obstacle());
    let mut s = board(&[(P1, hex(-1, 0)), (E1, hex(4, -4))]);
    let ground_hash = s.state_hash();
    let ground_routes: Vec<_> = map_hexes().iter().map(|&h| s.preview_move(P1, h)).collect();
    let ground_reach = s.reachable(P1);
    s.ground.set(obstacle, S).unwrap();
    assert_eq!(s.ground.get(obstacle), Some(S));
    assert_ne!(s.state_hash(), ground_hash, "the annotation is part of the state");
    assert_eq!(s.preview_move(P1, obstacle), Err(RuleError::Unreachable));
    assert_eq!(s.apply(Command::Move { unit: P1, to: obstacle }), Err(RuleError::Unreachable));
    assert!(!s.reachable(P1).iter().any(|&(h, _)| h == obstacle));
    // Routes and affordability elsewhere are untouched by the annotation.
    let routes: Vec<_> = map_hexes().iter().map(|&h| s.preview_move(P1, h)).collect();
    assert_eq!(routes, ground_routes);
    assert_eq!(s.reachable(P1), ground_reach);
}

// ------------------------------------------------------------------ hash

#[test]
fn all_ground_hashes_are_unchanged_and_terrain_uses_a_tagged_suffix_after_elevation() {
    assert_eq!(scenario::battle(1, TerrainPreset::Flat).state_hash(), 0x8c00302113cbe668);
    assert_eq!(scenario::battle(1, TerrainPreset::Hills).state_hash(), 0xb759683cdff4bfa2);
    assert_eq!(TERRAIN_HASH_TAG, 0x5445_5252);
    assert_ne!(TERRAIN_HASH_TAG, ELEVATION_HASH_TAG);

    let base = scenario::battle(1, TerrainPreset::Flat);
    let mut swampy = base.clone();
    for h in [hex(2, -3), hex(-3, 1), hex(0, 0), hex(-3, 0)] {
        set_k(&mut swampy, h, S);
    }
    // Entries are sorted by (q, r); Swamp is code 1; words use the existing u64 encoding.
    let suffix = [
        u64::from(TERRAIN_HASH_TAG),
        4,
        -3i64 as u64,
        0,
        1,
        -3i64 as u64,
        1,
        1,
        0,
        0,
        1,
        2,
        -3i64 as u64,
        1,
    ];
    assert_eq!(swampy.state_hash(), fnv_continue(base.state_hash(), &suffix));
    assert_ne!(swampy.state_hash(), base.state_hash());

    // The suffix follows the elevation suffix.
    let hills = scenario::battle(1, TerrainPreset::Hills);
    let mut hilly_swamp = hills.clone();
    set_k(&mut hilly_swamp, hex(2, -3), S);
    let expected = fnv_continue(hills.state_hash(), &[TERRAIN_HASH_TAG, 1, 2, -3i64 as u64, 1]);
    assert_eq!(hilly_swamp.state_hash(), expected);

    // Resetting to Ground restores the all-Ground hash exactly.
    let mut reset = swampy.clone();
    for h in [hex(2, -3), hex(-3, 1), hex(0, 0), hex(-3, 0)] {
        set_k(&mut reset, h, G);
    }
    assert_eq!(reset.state_hash(), base.state_hash());
    assert_eq!(reset.ground, base.ground);
}

#[test]
fn hash_is_order_independent_and_sensitive_to_hex_and_neighbouring_state() {
    let base = board(&[(P1, hex(-2, -2)), (E1, hex(4, -2))]);
    let hexes = [hex(0, -2), hex(1, -3), hex(-1, 2)];
    let mut a = base.clone();
    let mut b = base.clone();
    for &h in &hexes {
        set_k(&mut a, h, S);
    }
    for &h in hexes.iter().rev() {
        set_k(&mut b, h, S);
    }
    assert_eq!(a.state_hash(), b.state_hash());
    assert_ne!(a.state_hash(), base.state_hash());

    let mut seen = vec![a.state_hash(), base.state_hash()];
    for &h in &hexes {
        let mut one = base.clone();
        set_k(&mut one, h, S);
        let mut fewer = a.clone();
        set_k(&mut fewer, h, G);
        for hash in [one.state_hash(), fewer.state_hash()] {
            assert!(!seen.contains(&hash), "{h:?} must give a distinct hash");
            seen.push(hash);
        }
    }
    // Swamp and a height on the same hex are different states.
    let mut elevated = base.clone();
    set_h(&mut elevated, hexes[0], 1);
    let mut swamp = base.clone();
    set_k(&mut swamp, hexes[0], S);
    assert_ne!(elevated.state_hash(), swamp.state_hash());
    // Configuring terrain consumes no RNG.
    assert_eq!(a.rng, base.rng);
}

// ------------------------------------------------------------------ cost table

#[test]
fn cost_constants_derive_terrain_fatigue_from_the_ground_baseline() {
    assert_eq!((MOVE_AP, SWAMP_MOVE_AP, SWAMP_FATIGUE_FACTOR), (2, 4, 2));
    assert_eq!((MOVE_FATIGUE, PATHFINDER_MOVE_FATIGUE), (2, 1));
    let mut u = BattleState::new(1).units[P1 as usize].clone();
    for pathfinder in [false, true] {
        u.has_pathfinder = pathfinder;
        let ground = rules::move_fatigue(&u);
        assert_eq!(ground, if pathfinder { 1 } else { 2 });
        assert_eq!(rules::terrain_move_fatigue(&u, G), ground);
        assert_eq!(rules::terrain_move_fatigue(&u, S), 2 * ground);
    }
    assert_eq!((rules::terrain_move_ap(G), rules::terrain_move_ap(S)), (2, 4));
}

#[test]
fn transitions_cost_the_destination_with_uphill_and_pathfinder_for_both_factions() {
    let (from, to) = (hex(0, -2), hex(1, -2));
    for (actor, other, other_pos) in [(P1, E1, hex(4, -4)), (E1, P1, hex(-4, 4))] {
        for pathfinder in [false, true] {
            for k0 in [G, S] {
                for k1 in [G, S] {
                    for h0 in 0..=MAX_ELEVATION {
                        for h1 in 0..=MAX_ELEVATION {
                            let mut s = board(&[(actor, from), (other, other_pos)]);
                            s.units[actor as usize].has_pathfinder = pathfinder;
                            set_k(&mut s, from, k0);
                            set_k(&mut s, to, k1);
                            set_h(&mut s, from, h0);
                            set_h(&mut s, to, h1);
                            let (ap, fatigue) = step_cost(k1, h0, h1, pathfinder);
                            let ctx = format!("actor {actor} pf {pathfinder} {k0:?}@{h0}->{k1:?}@{h1}");
                            let before = snapshot(&s);
                            let p = s.preview_move(actor, to).unwrap();
                            assert_eq!((p.path.clone(), p.ap_cost, p.fatigue_cost), (vec![to], ap, fatigue), "{ctx}");
                            assert_eq!(p.ap_remaining, 9 - ap, "{ctx}");
                            assert!(s.reachable(actor).contains(&(to, ap)), "{ctx}");
                            assert_eq!(snapshot(&s), before, "previews are pure: {ctx}");
                            let ev = s.apply(Command::Move { unit: actor, to }).unwrap();
                            assert_eq!(ev, vec![Event::Moved { unit: actor, path: vec![to] }], "{ctx}");
                            let u = &s.units[actor as usize];
                            assert_eq!((u.ap, u.fatigue, u.pos), (9 - ap, fatigue, to), "{ctx}");
                            assert_eq!(s.rng, before.1, "{ctx}");
                        }
                    }
                }
            }
        }
    }
}

#[test]
fn the_starting_terrain_adds_no_departure_charge() {
    for pathfinder in [false, true] {
        let mut s = board(&[(P1, hex(0, -2)), (E1, hex(4, -4))]);
        s.units[P1 as usize].has_pathfinder = pathfinder;
        set_k(&mut s, hex(0, -2), S);
        let ground_step = s.preview_move(P1, hex(-1, -2)).unwrap();
        assert_eq!(ground_step.ap_cost, MOVE_AP);
        assert_eq!(ground_step.fatigue_cost, if pathfinder { 1 } else { 2 });
        // Also not on the second step of a route that starts in and passes through swamp.
        set_k(&mut s, hex(-1, -2), S);
        let two = s.preview_move(P1, hex(-2, -2)).unwrap();
        assert_eq!(two.ap_cost, SWAMP_MOVE_AP + MOVE_AP);
        assert_eq!(two.fatigue_cost, if pathfinder { 2 + 1 } else { 4 + 2 });
    }
}

#[test]
fn mixed_routes_agree_between_preview_reachable_and_execution() {
    for actor in [P1, E1] {
        for pathfinder in [false, true] {
            // (swamp flags, heights, cumulative AP, cumulative plain fatigue)
            let cases: [([bool; 3], [u8; 3], [u32; 3], [u32; 3]); 4] = [
                ([false, true, false], [0, 0, 0], [2, 6, 8], [2, 6, 8]),
                ([false, true, false], [1, 1, 0], [3, 7, 9], [2, 6, 8]),
                ([true, true, false], [0, 0, 0], [4, 8, 10], [4, 8, 10]),
                ([false, false, true], [0, 2, 2], [2, 6, 10], [2, 4, 8]),
            ];
            for (swamp, heights, cum_ap, cum_fatigue) in cases {
                let (mut s, path) = lane(actor);
                s.units[actor as usize].has_pathfinder = pathfinder;
                s.units[actor as usize].ap = 9;
                for i in 0..3 {
                    if swamp[i] {
                        set_k(&mut s, path[i], S);
                    }
                    set_h(&mut s, path[i], heights[i]);
                }
                let start = s.units[actor as usize].pos;
                let occupied: Vec<Hex> = s.units.iter().filter(|u| u.is_present()).map(|u| u.pos).collect();
                let ctx = format!("actor {actor} pf {pathfinder} {swamp:?} {heights:?}");
                for k in 0..3 {
                    let fatigue = if pathfinder { cum_fatigue[k] / 2 } else { cum_fatigue[k] };
                    let mut run = s.clone();
                    match run.preview_move(actor, path[k]) {
                        Ok(p) => {
                            assert!(cum_ap[k] <= 9, "{ctx} step {k}");
                            assert_eq!(p.path, bfs_path(start, path[k], &occupied).unwrap(), "{ctx}");
                            assert_eq!((p.path.len(), p.ap_cost, p.fatigue_cost), (k + 1, cum_ap[k], fatigue), "{ctx} step {k}");
                            assert!(s.reachable(actor).contains(&(path[k], cum_ap[k])), "{ctx} step {k}");
                            let ev = run.apply(Command::Move { unit: actor, to: path[k] }).unwrap();
                            assert_eq!(moved_path(&ev), p.path, "{ctx}");
                            let u = &run.units[actor as usize];
                            assert_eq!((u.ap, u.fatigue, u.pos), (9 - cum_ap[k], fatigue, path[k]), "{ctx} step {k}");
                        }
                        Err(e) => {
                            assert!(cum_ap[k] > 9, "{ctx} step {k}: unexpected {e:?}");
                            assert_eq!(e, RuleError::NotEnoughAp);
                            assert!(!s.reachable(actor).iter().any(|&(h, _)| h == path[k]));
                            assert_eq!(run.apply(Command::Move { unit: actor, to: path[k] }), Err(e));
                        }
                    }
                }
            }
        }
    }
}

/// Swamp on every third diagonal and a bumpy height field.
fn patchwork(s: &mut BattleState) {
    for h in map_hexes() {
        if (h.q * 2 + h.r * 3).rem_euclid(3) == 0 {
            set_k(s, h, S);
        }
        set_h(s, h, (h.q * 3 + h.r * 5).rem_euclid(4) as u8);
    }
}

#[test]
fn reachable_matches_preview_and_execution_over_a_patchwork_map() {
    let start = hex(-2, 2);
    for pathfinder in [false, true] {
        for (ap, fatigue) in [(9, 0), (9, 90), (9, 95), (9, 97), (9, 99), (5, 0), (4, 98), (3, 0), (2, 100), (0, 0)] {
            let mut base = board(&[(P1, start), (E1, hex(4, -4))]);
            patchwork(&mut base);
            base.units[P1 as usize].has_pathfinder = pathfinder;
            base.units[P1 as usize].ap = ap;
            base.units[P1 as usize].fatigue = fatigue;
            assert_valid_fixture(&base);
            let before = snapshot(&base);
            let listed = base.reachable(P1);
            let occupied = [start, hex(4, -4)];
            let expected: Vec<(Hex, u32)> = bfs_distances(start, &occupied)
                .into_iter()
                .filter(|&(_, d)| d >= 1)
                .filter_map(|(h, _)| base.preview_move(P1, h).ok().map(|p| (h, p.ap_cost)))
                .collect();
            assert_eq!(listed, expected, "pf {pathfinder} ap {ap} fatigue {fatigue}");
            for (h, _) in bfs_distances(start, &occupied).into_iter().filter(|&(_, d)| d >= 1) {
                let mut s = base.clone();
                match base.preview_move(P1, h) {
                    Ok(p) => {
                        assert_eq!(p.path, bfs_path(start, h, &occupied).unwrap(), "routes stay BFS routes");
                        assert_eq!(p.first_threat, None);
                        let ev = s.apply(Command::Move { unit: P1, to: h }).unwrap();
                        assert_eq!(moved_path(&ev), p.path);
                        let u = &s.units[P1 as usize];
                        assert_eq!((u.ap, u.fatigue, u.pos), (ap - p.ap_cost, fatigue + p.fatigue_cost, h));
                        assert_eq!(s.rng, base.rng);
                    }
                    Err(e) => {
                        assert!(matches!(e, RuleError::NotEnoughAp | RuleError::TooFatigued));
                        assert!(!listed.iter().any(|&(x, _)| x == h));
                        assert_eq!(s.apply(Command::Move { unit: P1, to: h }), Err(e));
                        assert_eq!(snapshot(&s), before);
                    }
                }
            }
            assert_eq!(snapshot(&base), before, "previews and reachable are pure");
        }
    }
    // The swamp really changes the answer relative to an otherwise identical map.
    let mut hills_only = board(&[(P1, start), (E1, hex(4, -4))]);
    let mut full = hills_only.clone();
    patchwork(&mut full);
    for h in map_hexes() {
        set_h(&mut hills_only, h, (h.q * 3 + h.r * 5).rem_euclid(4) as u8);
    }
    assert_ne!(full.reachable(P1), hills_only.reachable(P1));
}

// ------------------------------------------------------------------ budgets

#[test]
fn exact_cap_succeeds_and_one_short_is_rejected_without_changes() {
    let to = hex(1, -2);
    for pathfinder in [false, true] {
        let swamp_fatigue = if pathfinder { 2 } else { 4 };
        let make = |ap: u32, fatigue: u32| {
            let mut s = board(&[(P1, hex(0, -2)), (E1, hex(4, -4))]);
            s.units[P1 as usize].has_pathfinder = pathfinder;
            s.units[P1 as usize].ap = ap;
            s.units[P1 as usize].fatigue = fatigue;
            set_k(&mut s, to, S);
            s
        };
        // AP: 4 is exact; 3 is one short.
        let mut s = make(SWAMP_MOVE_AP, 0);
        assert!(s.reachable(P1).contains(&(to, SWAMP_MOVE_AP)));
        s.apply(Command::Move { unit: P1, to }).unwrap();
        assert_eq!((s.units[P1 as usize].ap, s.units[P1 as usize].fatigue), (0, swamp_fatigue));
        let mut s = make(SWAMP_MOVE_AP - 1, 0);
        let before = snapshot(&s);
        assert_eq!(s.preview_move(P1, to), Err(RuleError::NotEnoughAp));
        assert_eq!(s.apply(Command::Move { unit: P1, to }), Err(RuleError::NotEnoughAp));
        assert!(!s.reachable(P1).iter().any(|&(h, _)| h == to));
        assert_eq!(snapshot(&s), before);
        // Ground at the same AP would have been fine: the swamp is what costs.
        assert!(s.preview_move(P1, hex(-1, -2)).is_ok());

        // Fatigue: reaching the cap exactly is allowed; one more point is not.
        let mut s = make(9, FMAX - swamp_fatigue);
        s.apply(Command::Move { unit: P1, to }).unwrap();
        assert_eq!(s.units[P1 as usize].fatigue, FMAX);
        let mut s = make(9, FMAX - swamp_fatigue + 1);
        let before = snapshot(&s);
        assert_eq!(s.preview_move(P1, to), Err(RuleError::TooFatigued));
        assert_eq!(s.apply(Command::Move { unit: P1, to }), Err(RuleError::TooFatigued));
        assert!(!s.reachable(P1).iter().any(|&(h, _)| h == to));
        assert_eq!(snapshot(&s), before);

        // Both short: the AP error wins, as for every other move.
        let mut s = make(SWAMP_MOVE_AP - 1, FMAX);
        assert_eq!(s.apply(Command::Move { unit: P1, to }), Err(RuleError::NotEnoughAp));
    }
}

#[test]
fn u32_max_fatigue_uses_checked_sums() {
    let to = hex(1, -2);
    for (pathfinder, swamp, accepted, rejected) in [
        (false, true, u32::MAX - 4, u32::MAX - 3),
        (true, true, u32::MAX - 2, u32::MAX - 1),
        (false, false, u32::MAX - 2, u32::MAX - 1),
        (true, false, u32::MAX - 1, u32::MAX),
    ] {
        let make = |fatigue: u32| {
            let mut s = board(&[(P1, hex(0, -2)), (E1, hex(4, -4))]);
            let u = &mut s.units[P1 as usize];
            u.has_pathfinder = pathfinder;
            u.stats.fatigue_max = u32::MAX;
            u.fatigue = fatigue;
            if swamp {
                set_k(&mut s, to, S);
            }
            s
        };
        let ctx = format!("pf {pathfinder} swamp {swamp}");
        let mut s = make(accepted);
        let p = s.preview_move(P1, to).unwrap();
        assert!(!p.attack_budget_available, "{ctx}: the attack sum overflows and is not available");
        assert!(s.reachable(P1).iter().any(|&(h, _)| h == to), "{ctx}");
        s.apply(Command::Move { unit: P1, to }).unwrap();
        assert_eq!(s.units[P1 as usize].fatigue, u32::MAX, "{ctx}");
        let mut s = make(rejected);
        let before = snapshot(&s);
        assert_eq!(s.preview_move(P1, to), Err(RuleError::TooFatigued), "{ctx}");
        assert_eq!(s.apply(Command::Move { unit: P1, to }), Err(RuleError::TooFatigued), "{ctx}");
        assert!(!s.reachable(P1).iter().any(|&(h, _)| h == to), "{ctx}");
        assert_eq!(snapshot(&s), before, "{ctx}");
    }
}

#[test]
fn preview_attack_budget_follows_the_terrain_cost() {
    let to = hex(1, -2);
    // (swamp, uphill, pathfinder, ap, fatigue, expected attack budget)
    let cases = [
        (false, 0, false, 6, 0, true),
        (true, 0, false, 6, 0, false),
        (true, 0, false, 8, 0, true),
        (false, 0, false, 9, 88, true),
        (false, 0, false, 9, 89, false),
        (true, 0, false, 9, 86, true),
        (true, 0, false, 9, 87, false),
        (true, 0, true, 9, 88, true),
        (true, 0, true, 9, 89, false),
        (true, 1, false, 8, 0, false),
        (true, 1, false, 9, 0, true),
        (false, 1, false, 6, 0, false),
    ];
    for (swamp, uphill, pathfinder, ap, fatigue, expected) in cases {
        let mut s = board(&[(P1, hex(0, -2)), (E1, hex(4, -4))]);
        let u = &mut s.units[P1 as usize];
        (u.has_pathfinder, u.ap, u.fatigue) = (pathfinder, ap, fatigue);
        if swamp {
            set_k(&mut s, to, S);
        }
        set_h(&mut s, to, uphill);
        let (ap_cost, fatigue_cost) = step_cost(if swamp { S } else { G }, 0, uphill, pathfinder);
        let p = s.preview_move(P1, to).unwrap();
        let ctx = format!("swamp {swamp} uphill {uphill} pf {pathfinder} ap {ap} fatigue {fatigue}");
        assert_eq!((p.ap_cost, p.fatigue_cost, p.ap_remaining), (ap_cost, fatigue_cost, ap - ap_cost), "{ctx}");
        assert_eq!(p.attack_budget_available, expected, "{ctx}");
        let manual = ap - ap_cost >= ATTACK_AP && fatigue + fatigue_cost + ATTACK_FATIGUE <= FMAX;
        assert_eq!(manual, expected, "{ctx}: the table agrees with the arithmetic");
    }
}

// ------------------------------------------------------------------ reactions

/// The mover starts beside its foe; the first step leaves its zone of control, the
/// second never enters it. Returns the board and the two steps.
fn reaction_board(mover: u32) -> (BattleState, Hex, Hex) {
    let (a, b) = (hex(1, -2), hex(2, -2));
    let s = if mover == P1 { board(&[(P1, a), (E1, b)]) } else { board(&[(E1, a), (P1, b)]) };
    (s, hex(0, -2), hex(-1, -2))
}

#[test]
fn missed_reaction_then_continued_movement_pays_each_destination() {
    for mover in [P1, E1] {
        for pathfinder in [false, true] {
            let foe = if mover == P1 { E1 } else { P1 };
            let (mut s, first, last) = reaction_board(mover);
            s.units[foe as usize].stats.melee_skill = 50;
            s.units[mover as usize].has_pathfinder = pathfinder;
            set_k(&mut s, first, S);
            rig(&mut s, foe, mover, false);
            let ctx = format!("mover {mover} pf {pathfinder}");
            let p = s.preview_move(mover, last).unwrap();
            let (swamp, ground) = (if pathfinder { 2 } else { 4 }, if pathfinder { 1 } else { 2 });
            assert_eq!((p.ap_cost, p.fatigue_cost), (SWAMP_MOVE_AP + MOVE_AP, swamp + ground), "{ctx}");
            let ev = s.apply(Command::Move { unit: mover, to: last }).unwrap();
            assert!(matches!(ev[0], Event::Attacked { hit: false, .. }), "{ctx}: {ev:?}");
            assert_eq!(moved_path(&ev), vec![first, last], "{ctx}");
            let u = &s.units[mover as usize];
            assert_eq!(
                (u.pos, u.ap, u.fatigue),
                (last, 9 - SWAMP_MOVE_AP - MOVE_AP, swamp + DEFENDER_MISS_FATIGUE_GAIN + ground),
                "{ctx}"
            );
        }
    }
}

#[test]
fn a_stopping_hit_still_charges_the_attempted_destination() {
    for mover in [P1, E1] {
        for pathfinder in [false, true] {
            let foe = if mover == P1 { E1 } else { P1 };
            let (mut s, first, last) = reaction_board(mover);
            s.units[foe as usize].stats.melee_skill = 50;
            s.units[mover as usize].has_pathfinder = pathfinder;
            set_k(&mut s, first, S);
            rig(&mut s, foe, mover, true);
            let ctx = format!("mover {mover} pf {pathfinder}");
            let rng_before = s.rng.clone();
            let ev = s.apply(Command::Move { unit: mover, to: last }).unwrap();
            assert!(matches!(ev[..], [Event::Attacked { hit: true, .. }]), "{ctx}: {ev:?}");
            assert_ne!(s.rng, rng_before);
            let swamp = if pathfinder { 2 } else { 4 };
            let u = &s.units[mover as usize];
            assert_eq!(
                (u.pos, u.ap, u.fatigue),
                (hex(1, -2), 9 - SWAMP_MOVE_AP, swamp + DEFENDER_HIT_FATIGUE_GAIN),
                "{ctx}: the unit stays put but the swamp attempt is paid"
            );
        }
    }
}

#[test]
fn incoming_fatigue_after_a_reaction_prevents_a_later_step() {
    for mover in [P1, E1] {
        for pathfinder in [false, true] {
            let foe = if mover == P1 { E1 } else { P1 };
            // (first step swamp, first-step fatigue, second-step fatigue)
            let shapes = [
                (true, if pathfinder { 2 } else { 4 }, if pathfinder { 1 } else { 2 }),
                (false, if pathfinder { 1 } else { 2 }, if pathfinder { 2 } else { 4 }),
            ];
            for (first_swamp, f1, f2) in shapes {
                let after_first = f1 + DEFENDER_MISS_FATIGUE_GAIN;
                let (base, first, last) = reaction_board(mover);
                let m = mover as usize;
                let mut base = base;
                base.units[foe as usize].stats.melee_skill = 50;
                base.units[m].has_pathfinder = pathfinder;
                set_k(&mut base, if first_swamp { first } else { last }, S);
                let ctx = format!("mover {mover} pf {pathfinder} first swamp {first_swamp}");

                // Planning passes (f1 + f2 fits) but the miss leaves no room for step two.
                let mut tight = base.clone();
                tight.units[m].stats.fatigue_max = after_first + f2 - 1;
                rig(&mut tight, foe, mover, false);
                assert_eq!(tight.preview_move(mover, last).unwrap().fatigue_cost, f1 + f2, "{ctx}");
                let ev = tight.apply(Command::Move { unit: mover, to: last }).unwrap();
                assert_eq!(moved_path(&ev), vec![first], "{ctx}: {ev:?}");
                let u = &tight.units[m];
                assert_eq!((u.pos, u.fatigue), (first, after_first), "{ctx}");

                // One more point of headroom and the exact cap is reached.
                let mut exact = base.clone();
                exact.units[m].stats.fatigue_max = after_first + f2;
                rig(&mut exact, foe, mover, false);
                let ev = exact.apply(Command::Move { unit: mover, to: last }).unwrap();
                assert_eq!(moved_path(&ev), vec![first, last], "{ctx}");
                assert_eq!(exact.units[m].fatigue, exact.units[m].stats.fatigue_max, "{ctx}");
            }
        }
    }
}

// ------------------------------------------------------------------ automatic steps

#[derive(Clone, Copy, Debug)]
enum Auto {
    Flee,
    Withdraw,
}

impl Auto {
    fn cmd(self, unit: u32) -> Command {
        match self {
            Auto::Flee => Command::Flee { unit },
            Auto::Withdraw => Command::Withdraw { unit },
        }
    }
}

/// P1 (Fleeing or retreating, pathfinder or not) first in the queue, plus the given foes/allies.
fn escape_board(kind: Auto, pathfinder: bool, p1: Hex, others: &[(u32, Hex)]) -> BattleState {
    let mut placed = vec![(P1, p1)];
    placed.extend_from_slice(others);
    let mut s = board(&placed);
    match kind {
        Auto::Flee => s.units[P1 as usize].morale = Morale::Fleeing,
        Auto::Withdraw => s.units[P1 as usize].retreating = true,
    }
    s.units[P1 as usize].has_pathfinder = pathfinder;
    s
}

const FAR: [(u32, Hex); 2] = [(E1, hex(3, -3)), (P2, hex(3, -1))];

#[test]
fn automatic_steps_charge_and_check_the_swamp_cost() {
    for kind in [Auto::Flee, Auto::Withdraw] {
        // (ap, fatigue, plain moves, pathfinder moves); the first exit step is (-2,2) -> (-3,2).
        let cases = [
            (4, FMAX - 4, true, true),
            (3, FMAX - 4, false, false),
            (4, FMAX - 3, false, true),
            (4, FMAX - 2, false, true),
            (4, FMAX - 1, false, false),
            (9, FMAX, false, false),
            (3, 0, false, false),
        ];
        for (ap, fatigue, plain_moves, pf_moves) in cases {
            for (pathfinder, moves) in [(false, plain_moves), (true, pf_moves)] {
                let mut s = escape_board(kind, pathfinder, hex(-2, 2), &FAR);
                set_k(&mut s, hex(-3, 2), S);
                s.units[P1 as usize].ap = ap;
                s.units[P1 as usize].fatigue = fatigue;
                let rng = s.rng.clone();
                let ev = s.apply(kind.cmd(P1)).unwrap();
                let u = &s.units[P1 as usize];
                let ctx = format!("{kind:?} ap {ap} fatigue {fatigue} pathfinder {pathfinder}");
                if moves {
                    assert_eq!(ev, vec![Event::Moved { unit: P1, path: vec![hex(-3, 2)] }], "{ctx}");
                    let step = if pathfinder { 2 } else { 4 };
                    assert_eq!((u.pos, u.ap, u.fatigue), (hex(-3, 2), ap - SWAMP_MOVE_AP, fatigue + step), "{ctx}");
                } else {
                    assert_eq!(ev, vec![started(E1)], "{ctx}");
                    assert_eq!((u.pos, u.ap, u.fatigue), (hex(-2, 2), ap, fatigue), "{ctx}");
                    assert_eq!(s.queue, vec![E1, P2], "{ctx}");
                }
                assert_eq!(s.rng, rng, "{ctx}");
            }
        }
    }
}

#[test]
fn automatic_steps_charge_the_destination_not_the_starting_swamp() {
    for kind in [Auto::Flee, Auto::Withdraw] {
        let mut s = escape_board(kind, false, hex(-2, 2), &FAR);
        set_k(&mut s, hex(-2, 2), S);
        s.units[P1 as usize].ap = MOVE_AP;
        s.units[P1 as usize].fatigue = FMAX - MOVE_FATIGUE;
        let ev = s.apply(kind.cmd(P1)).unwrap();
        assert_eq!(ev, vec![Event::Moved { unit: P1, path: vec![hex(-3, 2)] }], "{kind:?}");
        let u = &s.units[P1 as usize];
        assert_eq!((u.ap, u.fatigue), (0, FMAX), "{kind:?}");
    }
}

#[test]
fn automatic_escape_pays_a_swamp_edge_step_and_an_occupied_edge_escape_stays_free() {
    for kind in [Auto::Flee, Auto::Withdraw] {
        for pathfinder in [false, true] {
            // Stepping onto a swamp edge hex is paid, then the unit escapes.
            let mut s = escape_board(kind, pathfinder, hex(-3, 3), &FAR);
            set_k(&mut s, hex(-4, 3), S);
            let ev = s.apply(kind.cmd(P1)).unwrap();
            assert_eq!(
                ev,
                vec![Event::Moved { unit: P1, path: vec![hex(-4, 3)] }, Event::Fled { unit: P1 }, started(E1)],
                "{kind:?}"
            );
            let u = &s.units[P1 as usize];
            assert_eq!((u.ap, u.fatigue, u.fled), (9 - SWAMP_MOVE_AP, if pathfinder { 2 } else { 4 }, true));

            // Already on a swamp edge hex: the escape is free whatever the budget.
            for (ap, fatigue, fmax) in [(0, FMAX, FMAX), (0, 0, FMAX), (9, u32::MAX, u32::MAX)] {
                let mut s = escape_board(kind, pathfinder, hex(-4, 3), &FAR);
                set_k(&mut s, hex(-4, 3), S);
                let u = &mut s.units[P1 as usize];
                (u.ap, u.fatigue, u.stats.fatigue_max) = (ap, fatigue, fmax);
                let rng = s.rng.clone();
                let ev = s.apply(kind.cmd(P1)).unwrap();
                assert_eq!(ev, vec![Event::Fled { unit: P1 }, started(E1)], "{kind:?}");
                assert_eq!((s.units[P1 as usize].ap, s.units[P1 as usize].fatigue), (ap, fatigue));
                assert_eq!(s.rng, rng);
            }
        }
    }
}

// ------------------------------------------------------------------ AI

/// The active unit stands at one end of the lane, its foe at the other; the BFS
/// destination is the foe's neighbour three steps along `path`.
fn ai_board(actor: u32) -> (BattleState, [Hex; 3]) {
    let (west, east) = (hex(-1, -2), hex(3, -2));
    if actor == P1 {
        (board(&[(P1, west), (E1, east)]), [hex(0, -2), hex(1, -2), hex(2, -2)])
    } else {
        (board(&[(E1, east), (P1, west)]), [hex(2, -2), hex(1, -2), hex(0, -2)])
    }
}

#[test]
fn ai_walks_the_longest_affordable_prefix_on_mixed_terrain_and_the_command_validates() {
    let max = u32::MAX;
    // (swamp flags, heights, ap, fatigue, fatigue_max, steps plain, steps pathfinder)
    let cases: [([bool; 3], [u8; 3], u32, u32, u32, usize, usize); 17] = [
        ([true, false, false], [0, 0, 0], 9, 0, FMAX, 3, 3),
        ([true, false, false], [0, 0, 0], 5, 0, FMAX, 1, 1),
        ([true, false, false], [0, 0, 0], 3, 0, FMAX, 0, 0),
        ([true, false, false], [0, 0, 0], 9, 96, FMAX, 1, 3),
        ([true, false, false], [0, 0, 0], 9, 97, FMAX, 0, 2),
        ([false, true, false], [0, 0, 0], 7, 0, FMAX, 2, 2),
        ([false, true, false], [0, 0, 0], 9, 94, FMAX, 2, 3),
        ([true, true, true], [0, 0, 0], 9, 0, FMAX, 2, 2),
        ([true, true, true], [0, 0, 0], 7, 0, FMAX, 1, 1),
        ([true, true, true], [0, 0, 0], 9, 97, FMAX, 0, 1),
        ([true, false, false], [1, 0, 0], 4, 0, FMAX, 0, 0),
        ([true, false, false], [1, 0, 0], 5, 0, FMAX, 1, 1),
        ([true, false, false], [1, 0, 0], 7, 0, FMAX, 2, 2),
        ([true, false, false], [1, 0, 0], 6, 0, FMAX, 1, 1),
        ([true, false, false], [0, 0, 0], 9, max - 4, max, 1, 3),
        ([true, false, false], [0, 0, 0], 9, max - 3, max, 0, 2),
        ([false, false, true], [0, 0, 0], 9, 0, FMAX, 3, 3),
    ];
    for actor in [P1, E1] {
        for (swamp, heights, ap, fatigue, fmax, plain_steps, pf_steps) in cases {
            let (mut twin, path) = ai_board(actor);
            for i in 0..3 {
                if swamp[i] {
                    set_k(&mut twin, path[i], S);
                }
                set_h(&mut twin, path[i], heights[i]);
            }
            let u = &mut twin.units[actor as usize];
            (u.ap, u.fatigue, u.stats.fatigue_max) = (ap, fatigue, fmax);
            let ctx = format!("actor {actor} {swamp:?} {heights:?} ap {ap} fatigue {fatigue}");
            for (pathfinder, steps) in [(false, plain_steps), (true, pf_steps)] {
                let mut s = twin.clone();
                s.units[actor as usize].has_pathfinder = pathfinder;
                let before = snapshot(&s);
                let cmd = plan_turn(&s);
                assert_eq!(snapshot(&s), before, "planning is pure: {ctx}");
                if steps == 0 {
                    assert_eq!(cmd, vec![Command::EndTurn { unit: actor }], "{ctx} pf {pathfinder}");
                    s.apply(cmd[0]).unwrap();
                    continue;
                }
                assert_eq!(cmd, vec![Command::Move { unit: actor, to: path[steps - 1] }], "{ctx} pf {pathfinder}");
                let preview = s.preview_move(actor, path[steps - 1]).unwrap();
                assert_eq!(preview.path, path[..steps].to_vec(), "{ctx}");
                let mut from = s.units[actor as usize].pos;
                let (mut want_ap, mut want_fatigue) = (0, 0);
                for i in 0..steps {
                    let kind = if swamp[i] { S } else { G };
                    let h0 = if i == 0 { 0 } else { heights[i - 1] };
                    let (a, f) = step_cost(kind, h0, heights[i], pathfinder);
                    (want_ap, want_fatigue) = (want_ap + a, want_fatigue + f);
                    from = path[i];
                }
                assert_eq!((preview.ap_cost, preview.fatigue_cost), (want_ap, want_fatigue), "{ctx}");
                s.apply(cmd[0]).unwrap();
                let u = &s.units[actor as usize];
                assert_eq!((u.pos, u.ap, u.fatigue), (from, ap - want_ap, fatigue + want_fatigue), "{ctx} pf {pathfinder}");
                assert_eq!(s.rng, Rng::new(1), "no zone of control on the lane");
            }
        }
    }
}

#[test]
fn the_ai_gate_is_not_fooled_by_a_swamp_it_cannot_pay_for() {
    // Exactly 2 AP passes the AI's cheap gate but cannot pay for a swamp step: plain EndTurn.
    let (mut s, path) = ai_board(P1);
    set_k(&mut s, path[0], S);
    s.units[P1 as usize].ap = MOVE_AP;
    assert_eq!(plan_turn(&s), vec![Command::EndTurn { unit: P1 }]);
    set_k(&mut s, path[0], G);
    assert_eq!(plan_turn(&s), vec![Command::Move { unit: P1, to: path[0] }]);
}

// ------------------------------------------------------------------ fixed skill costs

#[test]
fn rotation_and_footwork_keep_their_fixed_costs_on_swamp() {
    for hexes in [[hex(-3, 3), hex(-3, 2), hex(-2, 3)], [hex(0, -2), hex(1, -2), hex(0, -3)]] {
        for pathfinder in [false, true] {
            let mut s = board(&[(P1, hexes[0]), (P2, hexes[1]), (E1, hex(3, -3))]);
            let u = &mut s.units[P1 as usize];
            (u.has_rotation, u.has_footwork, u.has_pathfinder) = (true, true, pathfinder);
            for h in hexes {
                set_k(&mut s, h, S);
            }
            let ctx = format!("{hexes:?} pf {pathfinder}");

            let mut rot = s.clone();
            let ev = rot.apply(Command::Rotation { unit: P1, target: P2 }).unwrap();
            assert!(matches!(ev[0], Event::Rotated { .. }), "{ctx}");
            let u = &rot.units[P1 as usize];
            assert_eq!((u.ap, u.fatigue, u.pos), (9 - ROTATION_AP, ROTATION_FATIGUE, hexes[1]), "{ctx}");

            let mut foot = s.clone();
            let p = foot.preview_footwork(P1, hexes[2]).unwrap();
            let ev = foot.apply(Command::Footwork { unit: P1, to: hexes[2] }).unwrap();
            assert!(matches!(ev[0], Event::FootworkUsed { .. }), "{ctx}");
            let u = &foot.units[P1 as usize];
            assert_eq!((u.ap, u.fatigue, u.pos), (9 - FOOTWORK_AP, FOOTWORK_FATIGUE, hexes[2]), "{ctx}");
            assert_eq!(p.ap_cost, FOOTWORK_AP, "{ctx}");
            assert_eq!(p.fatigue_cost, FOOTWORK_FATIGUE, "{ctx}");
        }
    }
}
