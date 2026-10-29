//! Per-hex elevation and the melee elevation hit modifier (APPROXIMATION of
//! UC-COMBAT-010, catalog-stated: heights 0–3, +10 attacking down, -10 per level
//! attacking up). Fixtures park every unit far away, then place actors on the
//! obstacle-free lane `r = -2`. The first SplitMix64 draw for seed 1 is 65
//! (mod 100), so any chance of 65 or less misses.

use bb_rules::hex::{hex, map_hexes};
use bb_rules::rules::{ATTACK_AP, ATTACK_FATIGUE, DEFENDER_MISS_FATIGUE_GAIN, ELEVATION_HASH_TAG, MOVE_AP, MOVE_FATIGUE};
use bb_rules::terrain::MAX_ELEVATION;
use bb_rules::*;

const P1: u32 = 0;
const P2: u32 = 1;
const E1: u32 = 3;
const E2: u32 = 4;

const PARK_PLAYERS: [Hex; 3] = [hex(-4, 2), hex(-4, 3), hex(-4, 4)];
const PARK_ENEMIES: [Hex; 3] = [hex(4, -4), hex(4, -3), hex(4, -2)];

/// Hash of `BattleState::new(1)` before elevation existed (flat map).
const FLAT_SEED_1_HASH: u64 = 10_088_116_083_934_094_952;

/// All units parked with skill 0 / defense 0, `active` first with full AP.
fn board(active: u32, placements: &[(u32, Hex)]) -> BattleState {
    let mut s = BattleState::new(1);
    for i in 0..3 {
        s.units[i].pos = PARK_PLAYERS[i];
        s.units[3 + i].pos = PARK_ENEMIES[i];
    }
    for u in &mut s.units {
        u.stats.defense = 0;
        u.stats.melee_skill = 0;
        u.turn_started = false;
    }
    for &(id, at) in placements {
        s.units[id as usize].pos = at;
    }
    s.queue.retain(|&q| q != active);
    s.queue.insert(0, active);
    s.units[active as usize].ap = 9;
    s.units[active as usize].turn_started = true;
    let at: Vec<Hex> = s.units.iter().map(|u| u.pos).collect();
    for (i, h) in at.iter().enumerate() {
        assert!(map_hexes().contains(h) && !h.is_obstacle(), "fixture hex must be walkable");
        assert!(!at[..i].contains(h), "fixture hexes must be distinct");
    }
    s
}

/// P1 at (0,-2) beside E1 at (1,-2), attacker skill 50 against defense 0.
fn duel(attacker: u32) -> BattleState {
    let mut s = board(attacker, &[(P1, hex(0, -2)), (E1, hex(1, -2))]);
    s.units[attacker as usize].stats.melee_skill = 50;
    s
}

fn elevate(s: &mut BattleState, at: Hex, height: u8) {
    s.elevations.set(at, height).unwrap();
}

fn rng_after(seed: u64, n: usize) -> Rng {
    let mut r = Rng::new(seed);
    for _ in 0..n {
        r.next_u64();
    }
    r
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

// ------------------------------------------------------------- map API

#[test]
fn default_map_is_flat_and_new_battles_are_flat() {
    let map = ElevationMap::default();
    assert!(map.is_flat());
    for h in map_hexes() {
        assert_eq!(map.get(h), Some(0));
    }
    assert_eq!(map.get(hex(5, 0)), None);
    assert_eq!(map.get(hex(-3, -3)), None);
    let s = BattleState::new(7);
    assert_eq!(s.elevations, ElevationMap::default());
    assert!(s.elevations.is_flat());
}

#[test]
fn every_valid_height_round_trips_on_every_hex_including_obstacles() {
    let mut map = ElevationMap::default();
    for height in 0..=MAX_ELEVATION {
        for h in map_hexes() {
            assert_eq!(map.set(h, height), Ok(()));
            assert_eq!(map.get(h), Some(height));
        }
    }
    assert!(map_hexes().iter().any(|h| h.is_obstacle()));
    assert!(!map.is_flat(), "the last pass set height 3 everywhere");
    for h in map_hexes() {
        assert_eq!(map.set(h, 0), Ok(()));
    }
    assert!(map.is_flat());
    assert_eq!(map, ElevationMap::default());
    assert_eq!(MAX_ELEVATION, 3);
}

#[test]
fn rejected_input_returns_the_error_and_changes_nothing() {
    let mut s = BattleState::new(3);
    s.elevations.set(hex(1, -2), 2).unwrap();
    let (map, hash, rng) = (s.elevations.clone(), s.state_hash(), s.rng.clone());
    assert_eq!(s.elevations.set(hex(5, 0), 1), Err(ElevationError::OffMap));
    assert_eq!(s.elevations.set(hex(5, 0), 0), Err(ElevationError::OffMap));
    assert_eq!(s.elevations.set(hex(-4, -4), 2), Err(ElevationError::OffMap));
    assert_eq!(s.elevations.set(hex(1, -2), 4), Err(ElevationError::InvalidHeight));
    assert_eq!(s.elevations.set(hex(1, -2), 255), Err(ElevationError::InvalidHeight));
    assert_eq!(s.elevations.set(hex(2, -2), 4), Err(ElevationError::InvalidHeight));
    // Off-map is reported first when both are wrong.
    assert_eq!(s.elevations.set(hex(9, 9), 200), Err(ElevationError::OffMap));
    assert_eq!((s.elevations.clone(), s.state_hash(), s.rng.clone()), (map, hash, rng));
    assert_eq!(s.elevations.get(hex(1, -2)), Some(2));
    assert_eq!(s.elevations.get(hex(2, -2)), Some(0));
}

#[test]
fn extreme_coordinates_are_off_map_without_panic_or_mutation() {
    let mut map = ElevationMap::default();
    map.set(hex(1, -2), 2).unwrap();
    let before = map.clone();
    let extremes = [i32::MIN, i32::MIN + 1, -1_000_000, 1_000_000, i32::MAX - 1, i32::MAX];
    for &q in &extremes {
        for &r in &[i32::MIN, 0, i32::MAX, q.wrapping_neg().wrapping_sub(1)] {
            assert_eq!(map.get(hex(q, r)), None, "({q},{r})");
            assert_eq!(map.set(hex(q, r), 1), Err(ElevationError::OffMap), "({q},{r})");
            assert_eq!(map.set(hex(q, r), 9), Err(ElevationError::OffMap), "({q},{r})");
        }
        for &r in &extremes {
            assert_eq!(map.get(hex(0, r)), None);
            assert_eq!(map.set(hex(0, r), 1), Err(ElevationError::OffMap));
        }
    }
    assert_eq!(map, before);
    // The widened check agrees with `Hex::in_map` on every ordinary coordinate.
    for q in -8..=8 {
        for r in -8..=8 {
            assert_eq!(map.get(hex(q, r)).is_some(), hex(q, r).in_map(), "({q},{r})");
        }
    }
}

#[test]
fn elevation_does_not_change_obstacles_or_passability() {
    let mut s = board(P1, &[(P1, hex(-2, -2))]);
    let reachable = s.reachable(P1);
    let obstacle_hexes: Vec<Hex> = map_hexes().into_iter().filter(|h| h.is_obstacle()).collect();
    assert!(!obstacle_hexes.is_empty());
    for (i, &h) in obstacle_hexes.iter().enumerate() {
        elevate(&mut s, h, 1 + (i % 3) as u8);
        assert!(h.is_obstacle());
        assert_eq!(s.elevations.get(h), Some(1 + (i % 3) as u8));
    }
    for h in map_hexes() {
        elevate(&mut s, h, 3);
    }
    assert_eq!(s.reachable(P1), reachable);
    assert!(s.apply(Command::Move { unit: P1, to: hex(0, 0) }).is_err());
    assert_eq!(s.units[P1 as usize].pos, hex(-2, -2));
}

#[test]
fn clones_are_independent_and_zero_restores_the_default() {
    let mut a = ElevationMap::default();
    a.set(hex(2, -2), 3).unwrap();
    let b = a.clone();
    assert_eq!(a, b);
    a.set(hex(2, -2), 1).unwrap();
    assert_eq!(b.get(hex(2, -2)), Some(3));
    assert_ne!(a, b);
    a.set(hex(2, -2), 0).unwrap();
    assert_eq!(a, ElevationMap::default());
    assert!(a.is_flat());
    assert_eq!(a.entries().count(), 0);

    let mut s = BattleState::new(1);
    let t = s.clone();
    elevate(&mut s, hex(1, 1), 2);
    assert!(t.elevations.is_flat());
    assert_eq!(t.elevations.get(hex(1, 1)), Some(0));
}

// ------------------------------------------------------------ modifier

#[test]
fn all_height_pairs_give_a_fixed_downhill_bonus_and_a_per_level_uphill_penalty() {
    for attacker_side in [Side::Player, Side::Enemy] {
        let (attacker, target) = if attacker_side == Side::Player { (P1, E1) } else { (E1, P1) };
        for ah in 0..=3u8 {
            for th in 0..=3u8 {
                let mut s = duel(attacker);
                elevate(&mut s, hex(0, -2), if attacker == P1 { ah } else { th });
                elevate(&mut s, hex(1, -2), if attacker == P1 { th } else { ah });
                let expected = match ah.cmp(&th) {
                    std::cmp::Ordering::Greater => 60,
                    std::cmp::Ordering::Equal => 50,
                    std::cmp::Ordering::Less => 50 - 10 * u32::from(th - ah),
                };
                assert_eq!(s.hit_chance(attacker, target), expected, "{attacker_side:?} {ah}->{th}");
            }
        }
    }
}

#[test]
fn flat_and_equal_height_maps_match_the_unmodified_chance() {
    let flat = duel(P1);
    assert_eq!(flat.hit_chance(P1, E1), 50);
    let mut raised = duel(P1);
    elevate(&mut raised, hex(0, -2), 2);
    elevate(&mut raised, hex(1, -2), 2);
    assert_eq!(raised.hit_chance(P1, E1), 50);
}

#[test]
fn modifier_applies_to_morale_adjusted_attributes() {
    // Breaking attacker: 100 * 80% = 80. Confident target: 40 * 110% = 44.
    let mut s = duel(P1);
    s.units[P1 as usize].stats.melee_skill = 100;
    s.units[P1 as usize].morale = Morale::Breaking;
    s.units[E1 as usize].stats.defense = 40;
    s.units[E1 as usize].morale = Morale::Confident;
    assert_eq!(s.hit_chance(P1, E1), 36);
    elevate(&mut s, hex(0, -2), 1);
    assert_eq!(s.hit_chance(P1, E1), 46);
    elevate(&mut s, hex(0, -2), 0);
    elevate(&mut s, hex(1, -2), 2);
    assert_eq!(s.hit_chance(P1, E1), 16);
    assert_eq!((s.units[P1 as usize].stats.melee_skill, s.units[E1 as usize].stats.defense), (100, 40));
}

#[test]
fn modifier_stacks_with_surround_before_the_clamp() {
    // P1 and P2 both touch E1 at (2,-2); elevation is per attacker hex.
    let mut s = board(P1, &[(P1, hex(1, -2)), (P2, hex(2, -3)), (E1, hex(2, -2))]);
    s.units[P1 as usize].stats.melee_skill = 40;
    s.units[P2 as usize].stats.melee_skill = 40;
    assert_eq!((s.hit_chance(P1, E1), s.hit_chance(P2, E1)), (45, 45));
    elevate(&mut s, hex(1, -2), 2);
    elevate(&mut s, hex(2, -3), 0);
    elevate(&mut s, hex(2, -2), 1);
    // P1 downhill (+10), P2 uphill one level (-10).
    assert_eq!((s.hit_chance(P1, E1), s.hit_chance(P2, E1)), (55, 35));
    // Enemy attacker with an ally, from the opposite side.
    let mut e = board(E1, &[(P1, hex(1, -2)), (E1, hex(2, -2)), (E2, hex(2, -3))]);
    e.units[E1 as usize].stats.melee_skill = 40;
    elevate(&mut e, hex(2, -2), 3);
    assert_eq!(e.hit_chance(E1, P1), 40 + 5 + 10);
}

#[test]
fn both_clamps_apply_after_the_elevation_sum() {
    let mut s = board(P1, &[(P1, hex(1, -2)), (P2, hex(2, -3)), (E1, hex(2, -2))]);
    // High end: 100 + 5 surround - 10 uphill = 95 (clamping first would give 85).
    s.units[P1 as usize].stats.melee_skill = 100;
    elevate(&mut s, hex(2, -2), 1);
    assert_eq!(s.hit_chance(P1, E1), 95);
    // 100 + 5 + 10 = 115 clamps to 95.
    elevate(&mut s, hex(2, -2), 0);
    elevate(&mut s, hex(1, -2), 1);
    assert_eq!(s.hit_chance(P1, E1), 95);
    // Low end: 0 + 5 + 10 = 15 for the downhill attacker; 0 + 5 - 30 clamps to 5.
    let mut l = board(P1, &[(P1, hex(0, -2)), (E1, hex(1, -2))]);
    elevate(&mut l, hex(0, -2), 1);
    assert_eq!(l.hit_chance(P1, E1), 10);
    elevate(&mut l, hex(0, -2), 0);
    elevate(&mut l, hex(1, -2), 3);
    assert_eq!(l.hit_chance(P1, E1), 5);
    // Skill 25 uphill by 3 levels is -5 before the clamp.
    l.units[P1 as usize].stats.melee_skill = 25;
    assert_eq!(l.hit_chance(P1, E1), 5);
}

#[test]
fn only_present_hostile_adjacent_pairs_receive_the_modifier() {
    let mut s = board(P1, &[(P1, hex(0, -2)), (E1, hex(1, -2)), (E2, hex(-2, -2))]);
    s.units[P1 as usize].stats.melee_skill = 50;
    elevate(&mut s, hex(0, -2), 3);
    // Not adjacent (distance 2): flat chance.
    assert_eq!(s.hit_chance(P1, E2), 50);
    // Same side: flat chance.
    s.units[P2 as usize].pos = hex(-1, -2);
    assert_eq!(s.hit_chance(P1, P2), 50);
    // Dead target or attacker: flat chance.
    assert_eq!(s.hit_chance(P1, E1), 60);
    s.units[E1 as usize].alive = false;
    assert_eq!(s.hit_chance(P1, E1), 50);
    s.units[E1 as usize].alive = true;
    s.units[P1 as usize].alive = false;
    assert_eq!(s.hit_chance(P1, E1), 50);
}

#[test]
fn height_follows_the_units_current_position() {
    let mut s = duel(P1);
    elevate(&mut s, hex(0, -2), 2);
    elevate(&mut s, hex(1, -2), 1);
    elevate(&mut s, hex(1, -3), 3);
    elevate(&mut s, hex(0, -1), 0);
    assert_eq!(s.hit_chance(P1, E1), 60);
    // The target steps to a taller hex that is still adjacent to P1.
    s.units[E1 as usize].pos = hex(1, -3);
    assert_eq!(s.hit_chance(P1, E1), 40);
    // The attacker steps to a hex level with the target's new tile.
    s.units[P1 as usize].pos = hex(0, -3);
    elevate(&mut s, hex(0, -3), 3);
    assert_eq!(s.hit_chance(P1, E1), 50);
    // Heights are map state: the units carry none.
    assert_eq!(s.elevations.get(hex(0, -2)), Some(2));
}

#[test]
fn moving_a_unit_by_command_changes_which_tile_counts() {
    let mut s = board(P1, &[(P1, hex(-1, -2)), (E1, hex(1, -2))]);
    s.units[P1 as usize].stats.melee_skill = 50;
    elevate(&mut s, hex(0, -2), 1);
    assert_eq!(s.apply(Command::Move { unit: P1, to: hex(0, -2) }).map(|ev| ev.len()), Ok(1));
    assert_eq!(s.hit_chance(P1, E1), 60);
}

// ------------------------------------------------ attacks and previews

#[test]
fn ordinary_attack_reports_the_preview_chance_without_extra_rng_draws() {
    for (height_a, height_t, chance) in [(2u8, 0u8, 60u32), (0, 0, 50), (0, 2, 30), (1, 3, 30)] {
        let mut s = duel(P1);
        elevate(&mut s, hex(0, -2), height_a);
        elevate(&mut s, hex(1, -2), height_t);
        let preview = s.hit_chance(P1, E1);
        assert_eq!(preview, chance);
        let before = s.clone();
        let ev = s.apply(Command::Attack { attacker: P1, target: E1 }).unwrap();
        // Roll 65 misses every chance here, so exactly one draw is consumed.
        assert_eq!(
            ev,
            vec![Event::Attacked { attacker: P1, target: E1, hit: false, roll: 65, chance: preview, dmg: 0, absorbed: 0, hp_loss: 0 }]
        );
        assert_eq!(s.rng, rng_after(1, 1));
        let (a, b) = (&s.units[P1 as usize], &before.units[P1 as usize]);
        assert_eq!((a.ap, a.fatigue), (b.ap - ATTACK_AP, b.fatigue + ATTACK_FATIGUE));
        assert_eq!(s.units[E1 as usize].fatigue, before.units[E1 as usize].fatigue + DEFENDER_MISS_FATIGUE_GAIN);
        assert_eq!(s.units[E1 as usize].hp, before.units[E1 as usize].hp);
    }
}

#[test]
fn hit_attack_draws_damage_the_same_way_with_elevation() {
    // Chance 95 downhill from skill 90 hits on roll 65; a hit draws one damage roll.
    let mut s = duel(P1);
    s.units[P1 as usize].stats.melee_skill = 90;
    elevate(&mut s, hex(0, -2), 1);
    let ev = s.apply(Command::Attack { attacker: P1, target: E1 }).unwrap();
    assert!(matches!(ev[0], Event::Attacked { hit: true, roll: 65, chance: 95, .. }));
    assert!(s.rng == rng_after(1, 2));
}

#[test]
fn opportunity_attacks_use_the_departure_tile_and_the_shared_chance() {
    // E1 at (1,-3) controls (0,-2); it stands on height 2, the mover on 0.
    let mut s = board(P1, &[(P1, hex(0, -2)), (E1, hex(1, -3))]);
    s.units[E1 as usize].stats.melee_skill = 40;
    elevate(&mut s, hex(1, -3), 2);
    // Destination height must not matter for the reaction (it only adds the uphill AP surcharge).
    elevate(&mut s, hex(-1, -2), 3);
    assert_eq!(s.hit_chance(E1, P1), 50);
    let ev = s.apply(Command::Move { unit: P1, to: hex(-1, -2) }).unwrap();
    assert_eq!(
        ev[0],
        Event::Attacked { attacker: E1, target: P1, hit: false, roll: 65, chance: 50, dmg: 0, absorbed: 0, hp_loss: 0 }
    );
    assert_eq!(ev[1], Event::Moved { unit: P1, path: vec![hex(-1, -2)] });
    assert_eq!(ev.len(), 2);
    assert_eq!(s.rng, rng_after(1, 1));
    let u = &s.units[P1 as usize];
    assert_eq!((u.ap, u.fatigue), (9 - MOVE_AP - 3, MOVE_FATIGUE + DEFENDER_MISS_FATIGUE_GAIN));
    // The reactor pays nothing.
    assert_eq!(s.units[E1 as usize].fatigue, 0);

    // Mover on a taller departure tile: the reactor attacks uphill.
    let mut s = board(P1, &[(P1, hex(0, -2)), (E1, hex(1, -3))]);
    s.units[E1 as usize].stats.melee_skill = 40;
    elevate(&mut s, hex(0, -2), 3);
    assert_eq!(s.hit_chance(E1, P1), 10);
    let ev = s.apply(Command::Move { unit: P1, to: hex(-1, -2) }).unwrap();
    assert!(matches!(ev[0], Event::Attacked { attacker: E1, target: P1, hit: false, roll: 65, chance: 10, .. }));
    assert_eq!(s.rng, rng_after(1, 1));
}

#[test]
fn queries_and_configuration_are_read_only_and_consume_no_rng() {
    let mut s = duel(P1);
    elevate(&mut s, hex(0, -2), 1);
    let before = (s.state_hash(), s.rng.clone(), s.units.clone());
    for _ in 0..3 {
        assert_eq!(s.hit_chance(P1, E1), 60);
        assert_eq!(s.hit_chance(E1, P1), 5);
        assert_eq!(s.elevations.get(hex(0, -2)), Some(1));
        assert_eq!(s.elevations.get(hex(9, 9)), None);
    }
    assert_eq!((s.state_hash(), s.rng.clone(), s.units.clone()), before);
    elevate(&mut s, hex(3, -1), 2);
    let _ = s.elevations.set(hex(9, 9), 1);
    assert_eq!(s.rng, before.1);
    assert_eq!(s.units, before.2);
}

// ---------------------------------------------------------------- hash

#[test]
fn flat_map_hashes_are_unchanged() {
    let s = BattleState::new(1);
    assert_eq!(s.state_hash(), FLAT_SEED_1_HASH);
    // Setting and clearing a height leaves the canonical flat hash.
    let mut t = s.clone();
    elevate(&mut t, hex(2, -2), 3);
    assert_ne!(t.state_hash(), s.state_hash());
    elevate(&mut t, hex(2, -2), 0);
    assert_eq!(t.state_hash(), s.state_hash());
    // Explicit zeros never create an entry.
    elevate(&mut t, hex(-3, 1), 0);
    assert_eq!(t.state_hash(), s.state_hash());
}

#[test]
fn nonzero_elevations_append_a_tagged_sorted_suffix() {
    let flat = BattleState::new(5);
    let mut s = flat.clone();
    // Inserted out of order on purpose; the obstacle at (0,0) is allowed.
    elevate(&mut s, hex(2, -1), 3);
    elevate(&mut s, hex(-1, 3), 1);
    elevate(&mut s, hex(0, 0), 2);
    elevate(&mut s, hex(-1, -2), 1);
    let sorted = [(-1, -2, 1u64), (-1, 3, 1), (0, 0, 2), (2, -1, 3)];
    let mut words = vec![ELEVATION_HASH_TAG, sorted.len() as u64];
    for (q, r, h) in sorted {
        words.extend([q as i64 as u64, r as i64 as u64, h]);
    }
    assert_eq!(s.state_hash(), fnv_continue(flat.state_hash(), &words));
}

#[test]
fn construction_order_does_not_change_queries_or_hashes() {
    let entries = [(hex(2, -2), 3u8), (hex(0, 0), 1), (hex(-3, 1), 2), (hex(1, -2), 3), (hex(-4, 0), 1)];
    let mut forward = duel(P1);
    let mut backward = duel(P1);
    for &(at, h) in &entries {
        elevate(&mut forward, at, h);
    }
    for &(at, h) in entries.iter().rev() {
        elevate(&mut backward, at, h);
    }
    // Extra detour: set something else and clear it again.
    elevate(&mut backward, hex(3, -3), 2);
    elevate(&mut backward, hex(3, -3), 0);
    assert_eq!(forward.elevations, backward.elevations);
    assert_eq!(forward.state_hash(), backward.state_hash());
    for h in map_hexes() {
        assert_eq!(forward.elevations.get(h), backward.elevations.get(h));
    }
    assert_eq!(forward.hit_chance(P1, E1), backward.hit_chance(P1, E1));
    assert_eq!(forward.hit_chance(E1, P1), backward.hit_chance(E1, P1));
}

#[test]
fn hash_is_sensitive_to_height_and_place_and_resets_to_the_original() {
    let base = duel(P1);
    let original = base.state_hash();
    let mut hashes = Vec::new();
    for height in 1..=3u8 {
        let mut s = base.clone();
        elevate(&mut s, hex(2, -2), height);
        hashes.push(s.state_hash());
    }
    let mut elsewhere = base.clone();
    elevate(&mut elsewhere, hex(2, -3), 1);
    hashes.push(elsewhere.state_hash());
    let mut two = base.clone();
    elevate(&mut two, hex(2, -2), 1);
    elevate(&mut two, hex(2, -3), 1);
    hashes.push(two.state_hash());
    hashes.push(original);
    let mut unique = hashes.clone();
    unique.sort_unstable();
    unique.dedup();
    assert_eq!(unique.len(), hashes.len());

    let mut s = base.clone();
    for (i, h) in map_hexes().into_iter().enumerate() {
        elevate(&mut s, h, 1 + (i % 3) as u8);
    }
    assert_ne!(s.state_hash(), original);
    for h in map_hexes() {
        elevate(&mut s, h, 0);
    }
    assert_eq!(s.state_hash(), original);
    assert_eq!(s.elevations, ElevationMap::default());
}
