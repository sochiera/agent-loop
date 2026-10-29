//! Headless Fast Adaptation (partial UC-COMBAT-067). The catalog gives "+10% next
//! attack per miss, reset on a hit"; additive percentage points, the pre-attack
//! counter, the lifecycle across targets, movement, Wait, turns and rounds, and the
//! capability flag are an APPROXIMATION. Fixtures park every unit far away, then
//! place actors on the obstacle-free lane `r = -2`. Every unit has skill 0 /
//! defense 0 and damage 1; `duel` gives the attacker skill 50 (a 50% base chance).
//! `rig` picks an RNG state whose next draws take the requested rolls (a roll of 99
//! always misses, 0 always hits), so tests never depend on a particular seed.

use bb_rules::hex::{hex, map_hexes};
use bb_rules::rules::{FAST_ADAPTATION_BONUS_PER_STACK, soak};
use bb_rules::scenario::{self, TerrainPreset};
use bb_rules::*;

const P1: u32 = 0;
const P2: u32 = 1;
const E1: u32 = 3;
const E2: u32 = 4;

const PARK_PLAYERS: [Hex; 3] = [hex(-4, 2), hex(-4, 3), hex(-4, 4)];
const PARK_ENEMIES: [Hex; 3] = [hex(4, -4), hex(4, -3), hex(4, -2)];

/// Hash of `BattleState::new(1)` before Fast Adaptation existed.
const FLAT_SEED_1_HASH: u64 = 10_088_116_083_934_094_952;
/// Initial `scenario::battle(1, _)` hashes before Fast Adaptation existed.
const FLAT_SCENARIO_HASH: u64 = 0x8c00302113cbe668;
const HILLS_SCENARIO_HASH: u64 = 0xb759683cdff4bfa2;

/// All units parked, skill 0 / defense 0 / damage 1, `active` first with full AP.
fn board(active: u32, placements: &[(u32, Hex)]) -> BattleState {
    let mut s = BattleState::new(1);
    for i in 0..3 {
        s.units[i].pos = PARK_PLAYERS[i];
        s.units[3 + i].pos = PARK_ENEMIES[i];
    }
    for u in &mut s.units {
        u.stats.defense = 0;
        u.stats.melee_skill = 0;
        u.stats.dmg_min = 1;
        u.stats.dmg_max = 1;
        u.turn_started = false;
    }
    for &(id, at) in placements {
        s.units[id as usize].pos = at;
    }
    s.queue.retain(|&q| q != active);
    s.queue.insert(0, active);
    s.units[active as usize].ap = 9;
    s.units[active as usize].turn_started = true;
    assert_fixture_valid(&s);
    s
}

fn assert_fixture_valid(s: &BattleState) {
    let at: Vec<Hex> = s.units.iter().map(|u| u.pos).collect();
    for (i, h) in at.iter().enumerate() {
        assert!(map_hexes().contains(h) && !h.is_obstacle(), "fixture hex must be passable: {h:?}");
        assert!(!at[..i].contains(h), "fixture hexes must be distinct: {h:?}");
    }
}

/// `attacker` (skill 50, capable) beside `target` on the lane; P1 at (0,-2), E1 at (1,-2).
fn duel(attacker: u32) -> BattleState {
    let mut s = board(attacker, &[(P1, hex(0, -2)), (E1, hex(1, -2))]);
    s.units[attacker as usize].stats.melee_skill = 50;
    s.units[attacker as usize].has_fast_adaptation = true;
    s
}

fn stacks(s: &BattleState, id: u32) -> u32 {
    s.units[id as usize].fast_adaptation_stacks
}

fn rng_after_state(state: u64, n: usize) -> Rng {
    let mut r = Rng::new(state);
    for _ in 0..n {
        r.next_u64();
    }
    r
}

/// Set the RNG so the next `rolls.len()` draws (mod 100) equal `rolls` (at most two).
fn rig(s: &mut BattleState, rolls: &[u32]) {
    assert!(rolls.len() <= 2);
    for k in 1u64.. {
        let cand = s.rng.state.wrapping_add(k);
        let mut r = Rng::new(cand);
        if rolls.iter().all(|&want| (r.next_u64() % 100) as u32 == want) {
            s.rng = Rng::new(cand);
            return;
        }
    }
}

/// Make `id` the active unit with fresh AP and fatigue.
fn activate(s: &mut BattleState, id: u32) {
    s.queue.retain(|&q| q != id);
    s.queue.insert(0, id);
    let u = &mut s.units[id as usize];
    u.ap = 9;
    u.fatigue = 0;
    u.turn_started = true;
}

/// One rigged ordinary attack; returns the `Attacked` event fields `(hit, chance)`.
fn swing(s: &mut BattleState, attacker: u32, target: u32, roll: u32) -> (bool, u32) {
    activate(s, attacker);
    rig(s, &[roll]);
    let predicted = s.hit_chance(attacker, target);
    let ev = s.apply(Command::Attack { attacker, target }).unwrap();
    match ev[0] {
        Event::Attacked { attacker: a, target: t, hit, roll: r, chance, .. } => {
            assert_eq!((a, t, r), (attacker, target, roll));
            assert_eq!(chance, predicted, "preview and event must agree");
            (hit, chance)
        }
        ref other => panic!("expected Attacked first, got {other:?}"),
    }
}

const MISS: u32 = 99;
const HIT: u32 = 0;

// ------------------------------------------------------------- defaults

#[test]
fn new_units_and_scenarios_start_without_the_capability() {
    let u = Unit::new(9, "X", Side::Player, scenario::units()[0].stats.clone(), hex(0, -2));
    assert!(!u.has_fast_adaptation);
    assert_eq!(u.fast_adaptation_stacks, 0);
    for s in [BattleState::new(1), scenario::battle(1, TerrainPreset::Flat), scenario::battle(1, TerrainPreset::Hills)] {
        assert!(s.units.iter().all(|u| !u.has_fast_adaptation && u.fast_adaptation_stacks == 0));
    }
    assert_eq!(FAST_ADAPTATION_BONUS_PER_STACK, 10);
}

// ------------------------------------------------------------- core lifecycle

fn three_misses_then_a_hit(attacker: u32, target: u32) {
    let mut s = duel(attacker);
    for (i, expected) in [50, 60, 70, 80].into_iter().enumerate() {
        assert_eq!(stacks(&s, attacker), i as u32);
        let (hit, chance) = swing(&mut s, attacker, target, MISS);
        assert_eq!((hit, chance), (false, expected), "miss {i}");
    }
    assert_eq!(stacks(&s, attacker), 4);
    // The fifth swing uses 90 and a hit resets the counter.
    let (hit, chance) = swing(&mut s, attacker, target, HIT);
    assert_eq!((hit, chance), (true, 90));
    assert_eq!(stacks(&s, attacker), 0);
    assert_eq!(s.hit_chance(attacker, target), 50);
    let (_, chance) = swing(&mut s, attacker, target, MISS);
    assert_eq!(chance, 50);
    assert_eq!(stacks(&s, attacker), 1);
    // The defender never gains stacks.
    assert_eq!(stacks(&s, target), 0);
}

#[test]
fn player_three_misses_raise_successive_chances_and_a_hit_resets() {
    three_misses_then_a_hit(P1, E1);
}

#[test]
fn enemy_three_misses_raise_successive_chances_and_a_hit_resets() {
    three_misses_then_a_hit(E1, P1);
}

#[test]
fn capability_disabled_ignores_and_preserves_a_stored_counter() {
    let mut s = duel(P1);
    s.units[P1 as usize].has_fast_adaptation = false;
    s.units[P1 as usize].fast_adaptation_stacks = 7;
    assert_eq!(s.hit_chance(P1, E1), 50);
    assert_eq!(swing(&mut s, P1, E1, MISS), (false, 50));
    assert_eq!(stacks(&s, P1), 7);
    assert_eq!(swing(&mut s, P1, E1, HIT), (true, 50));
    assert_eq!(stacks(&s, P1), 7);
    // Never-capable attackers stay at zero.
    let mut s = duel(P1);
    s.units[P1 as usize].has_fast_adaptation = false;
    swing(&mut s, P1, E1, MISS);
    swing(&mut s, P1, E1, MISS);
    assert_eq!(stacks(&s, P1), 0);
}

#[test]
fn attacker_counters_are_independent() {
    let mut s = board(P1, &[(P1, hex(0, -2)), (P2, hex(1, -3)), (E1, hex(1, -2))]);
    for id in [P1, P2] {
        s.units[id as usize].stats.melee_skill = 50;
        s.units[id as usize].has_fast_adaptation = true;
    }
    // Two adjacent players: each gets the +5 surround point on top of 50.
    assert_eq!(swing(&mut s, P1, E1, MISS), (false, 55));
    assert_eq!(swing(&mut s, P1, E1, MISS), (false, 65));
    assert_eq!((stacks(&s, P1), stacks(&s, P2)), (2, 0));
    assert_eq!(swing(&mut s, P2, E1, MISS), (false, 55));
    assert_eq!((stacks(&s, P1), stacks(&s, P2)), (2, 1));
    // A hit by P2 resets only P2.
    assert_eq!(swing(&mut s, P2, E1, HIT), (true, 65));
    assert_eq!((stacks(&s, P1), stacks(&s, P2)), (2, 0));
    assert_eq!(s.hit_chance(P1, E1), 75);
}

#[test]
fn stacks_follow_the_attacker_across_target_changes() {
    // P1 at (0,-2) touches E1 at (1,-2) and E2 at (0,-1).
    let mut s = board(P1, &[(P1, hex(0, -2)), (E1, hex(1, -2)), (E2, hex(0, -1))]);
    s.units[P1 as usize].stats.melee_skill = 50;
    s.units[P1 as usize].has_fast_adaptation = true;
    assert_eq!(swing(&mut s, P1, E1, MISS), (false, 50));
    assert_eq!(swing(&mut s, P1, E2, MISS), (false, 60));
    assert_eq!(swing(&mut s, P1, E1, MISS), (false, 70));
    assert_eq!(swing(&mut s, P1, E2, HIT), (true, 80));
    assert_eq!(stacks(&s, P1), 0);
    assert_eq!(s.hit_chance(P1, E1), 50);
}

#[test]
fn armour_only_hit_resets_the_counter() {
    let mut s = duel(P1);
    s.units[P1 as usize].fast_adaptation_stacks = 3;
    s.units[E1 as usize].armour = 40;
    activate(&mut s, P1);
    rig(&mut s, &[HIT]);
    let ev = s.apply(Command::Attack { attacker: P1, target: E1 }).unwrap();
    let (absorbed, hp_loss) = soak(40, 1);
    assert_eq!(hp_loss, 0, "fixture must be an armour-only hit");
    assert!(matches!(ev[0], Event::Attacked { hit: true, chance: 80, absorbed: a, hp_loss: 0, .. } if a == absorbed));
    assert_eq!(s.units[E1 as usize].armour, 39);
    assert_eq!(stacks(&s, P1), 0);
}

#[test]
fn lethal_hit_resets_the_counter() {
    let mut s = duel(E1);
    s.units[E1 as usize].fast_adaptation_stacks = 2;
    s.units[P1 as usize].hp = 1;
    s.units[P1 as usize].armour = 0;
    activate(&mut s, E1);
    rig(&mut s, &[HIT]);
    let ev = s.apply(Command::Attack { attacker: E1, target: P1 }).unwrap();
    assert!(matches!(ev[0], Event::Attacked { hit: true, chance: 70, hp_loss: 1, .. }));
    assert!(ev.contains(&Event::Died { unit: P1 }));
    assert!(!s.units[P1 as usize].alive);
    assert_eq!(stacks(&s, E1), 0);
}

// ------------------------------------------------------------- opportunity attacks

#[test]
fn opportunity_attack_misses_stack_and_later_ordinary_attacks_use_the_counter() {
    // E1 at (1,-3) touches P1 at (0,-2) and (1,-2). P1 steps (0,-2) -> (1,-2),
    // departing E1's zone once; E1's reaction is a free attack (no costs).
    let mut s = board(P1, &[(P1, hex(0, -2)), (E1, hex(1, -3))]);
    s.units[E1 as usize].has_fast_adaptation = true;
    s.units[E1 as usize].fast_adaptation_stacks = 1;
    let (ap, fatigue) = (s.units[E1 as usize].ap, s.units[E1 as usize].fatigue);
    rig(&mut s, &[MISS]);
    let ev = s.apply(Command::Move { unit: P1, to: hex(1, -2) }).unwrap();
    // Skill 0 vs defense 0 plus one stack: 10%, recorded as used.
    assert!(matches!(ev[0], Event::Attacked { attacker: 3, target: 0, hit: false, chance: 10, .. }));
    assert_eq!(stacks(&s, E1), 2);
    assert_eq!((s.units[E1 as usize].ap, s.units[E1 as usize].fatigue), (ap, fatigue), "reactions are free");
    // E1's next ordinary attack uses the updated counter (20%), then hits and resets.
    assert_eq!(swing(&mut s, E1, P1, MISS), (false, 20));
    assert_eq!(swing(&mut s, E1, P1, HIT), (true, 30));
    assert_eq!(stacks(&s, E1), 0);
}

#[test]
fn opportunity_attack_hit_resets_and_ordinary_attack_restarts_from_base() {
    let mut s = board(P1, &[(P1, hex(0, -2)), (E1, hex(1, -3))]);
    s.units[E1 as usize].has_fast_adaptation = true;
    s.units[E1 as usize].fast_adaptation_stacks = 4;
    s.units[E1 as usize].stats.melee_skill = 50;
    rig(&mut s, &[HIT, 0]);
    let ev = s.apply(Command::Move { unit: P1, to: hex(1, -2) }).unwrap();
    assert!(matches!(ev[0], Event::Attacked { attacker: 3, hit: true, chance: 90, .. }));
    assert_eq!(stacks(&s, E1), 0);
    assert_eq!(swing(&mut s, E1, P1, MISS), (false, 50));
    assert_eq!(stacks(&s, E1), 1);
}

#[test]
fn reaction_attacks_by_a_player_use_the_same_bookkeeping() {
    let mut s = board(E1, &[(E1, hex(0, -2)), (P1, hex(1, -3))]);
    s.units[P1 as usize].has_fast_adaptation = true;
    rig(&mut s, &[MISS]);
    s.apply(Command::Move { unit: E1, to: hex(1, -2) }).unwrap();
    assert_eq!(stacks(&s, P1), 1);
}

// ------------------------------------------------------------- persistence

#[test]
fn stacks_persist_through_movement_wait_turns_and_rounds() {
    let mut s = duel(P1);
    swing(&mut s, P1, E1, MISS);
    swing(&mut s, P1, E1, MISS);
    assert_eq!(stacks(&s, P1), 2);

    // Movement: step to (0,-1), still beside E1 (the departure draws a free reaction).
    activate(&mut s, P1);
    rig(&mut s, &[MISS]);
    s.apply(Command::Move { unit: P1, to: hex(0, -1) }).unwrap();
    assert!(!s.units[P1 as usize].pos.is_obstacle());
    assert_eq!(s.units[P1 as usize].pos, hex(0, -1));
    assert_eq!(stacks(&s, P1), 2);
    assert_eq!(s.hit_chance(P1, E1), 70);

    // Wait.
    activate(&mut s, P1);
    s.units[P1 as usize].waited = false;
    s.apply(Command::Wait { unit: P1 }).unwrap();
    assert_eq!(stacks(&s, P1), 2);

    // Turn and round boundaries: end every turn until round 2 starts.
    assert_eq!(s.round, 1);
    for _ in 0..20 {
        if s.round > 1 {
            break;
        }
        let a = s.queue[0];
        s.apply(Command::EndTurn { unit: a }).unwrap();
        assert_eq!(stacks(&s, P1), 2);
    }
    assert!(s.round > 1, "a round boundary must have been crossed");
    assert_eq!(stacks(&s, P1), 2);
    assert_eq!(s.hit_chance(P1, E1), 70);
    // A miss in the new round continues the same streak; a hit then resets it.
    assert_eq!(swing(&mut s, P1, E1, MISS), (false, 70));
    assert_eq!(swing(&mut s, P1, E1, HIT), (true, 80));
    assert_eq!(stacks(&s, P1), 0);
}

// ------------------------------------------------------------- composition and clamps

#[test]
fn bonus_composes_with_morale_surround_and_elevation_before_one_clamp() {
    let mut s = board(P1, &[(P1, hex(0, -2)), (P2, hex(1, -3)), (E1, hex(1, -2))]);
    s.units[P1 as usize].stats.melee_skill = 50;
    s.units[P1 as usize].has_fast_adaptation = true;
    s.units[P1 as usize].fast_adaptation_stacks = 2;
    // 50 + 20 (stacks) + 5 (one extra adjacent ally).
    assert_eq!(s.hit_chance(P1, E1), 75);
    // + 10 attacking downhill.
    s.elevations.set(hex(0, -2), 1).unwrap();
    assert_eq!(s.hit_chance(P1, E1), 85);
    // Confident: skill 55 -> 90; Wavering: skill 45 -> 80.
    s.units[P1 as usize].morale = Morale::Confident;
    assert_eq!(s.hit_chance(P1, E1), 90);
    s.units[P1 as usize].morale = Morale::Wavering;
    assert_eq!(s.hit_chance(P1, E1), 80);
    // Uphill: attacker at 0, target at 2 (-20): 45 + 20 + 5 - 20 = 50 (Wavering).
    s.elevations.set(hex(0, -2), 0).unwrap();
    s.elevations.set(hex(1, -2), 2).unwrap();
    assert_eq!(s.hit_chance(P1, E1), 50);
    // Without the capability none of the stored stacks count.
    s.units[P1 as usize].has_fast_adaptation = false;
    assert_eq!(s.hit_chance(P1, E1), 30);
}

#[test]
fn the_single_clamp_applies_at_both_ends() {
    let mut s = duel(P1);
    s.units[P1 as usize].fast_adaptation_stacks = 4;
    assert_eq!(s.hit_chance(P1, E1), 90);
    s.units[P1 as usize].fast_adaptation_stacks = 5;
    assert_eq!(s.hit_chance(P1, E1), 95);
    s.units[P1 as usize].fast_adaptation_stacks = 50;
    assert_eq!(s.hit_chance(P1, E1), 95);
    // A large defense pushes the sum below zero even with stacks: floor of 5.
    s.units[E1 as usize].stats.defense = 200;
    s.units[P1 as usize].fast_adaptation_stacks = 3;
    assert_eq!(s.hit_chance(P1, E1), 5);
    // The bonus is applied before the clamp, not after it: 50 - 80 + 40 = 10.
    s.units[E1 as usize].stats.defense = 80;
    s.units[P1 as usize].fast_adaptation_stacks = 4;
    assert_eq!(s.hit_chance(P1, E1), 10);
}

#[test]
fn extreme_counters_are_overflow_safe_and_saturate() {
    let mut s = duel(P1);
    s.units[P1 as usize].fast_adaptation_stacks = u32::MAX;
    assert_eq!(s.hit_chance(P1, E1), 95);
    s.units[P1 as usize].stats.melee_skill = u32::MAX;
    s.units[E1 as usize].stats.defense = 0;
    assert_eq!(s.hit_chance(P1, E1), 95);
    s.units[P1 as usize].stats.melee_skill = 0;
    s.units[E1 as usize].stats.defense = u32::MAX;
    assert_eq!(s.hit_chance(P1, E1), 95, "MAX stacks (+42949672950) outweigh MAX defense");
    s.units[P1 as usize].stats.melee_skill = 50;
    s.units[E1 as usize].stats.defense = 0;
    // A miss at u32::MAX saturates instead of wrapping; a hit resets to zero.
    assert_eq!(swing(&mut s, P1, E1, 99), (false, 95));
    assert_eq!(stacks(&s, P1), u32::MAX);
    assert_eq!(swing(&mut s, P1, E1, 0), (true, 95));
    assert_eq!(stacks(&s, P1), 0);
    // Disabled units never wrap their stored counter either.
    s.units[P1 as usize].has_fast_adaptation = false;
    s.units[P1 as usize].fast_adaptation_stacks = u32::MAX;
    swing(&mut s, P1, E1, 99);
    assert_eq!(stacks(&s, P1), u32::MAX);
}

#[test]
fn bonus_applies_only_to_a_present_hostile_adjacent_pair() {
    let mut s = duel(P1);
    s.units[P1 as usize].fast_adaptation_stacks = 3;
    assert_eq!(s.hit_chance(P1, E1), 80);
    // Not adjacent.
    s.units[E1 as usize].pos = hex(3, -2);
    assert_eq!(s.hit_chance(P1, E1), 50);
    // Same side (adjacent).
    s.units[P2 as usize].pos = hex(1, -2);
    assert_eq!(s.hit_chance(P1, P2), 50);
    // Dead target.
    s.units[E1 as usize].pos = hex(1, -3);
    s.units[E1 as usize].alive = false;
    assert_eq!(s.hit_chance(P1, E1), 50);
}

// ------------------------------------------------------------- purity and RNG

#[test]
fn queries_and_rejected_commands_preserve_all_state() {
    let mut s = duel(P1);
    s.units[P1 as usize].fast_adaptation_stacks = 3;
    s.units[E1 as usize].has_fast_adaptation = true;
    s.units[E1 as usize].fast_adaptation_stacks = 2;
    let (units, rng, hash, queue) = (s.units.clone(), s.rng.clone(), s.state_hash(), s.queue.clone());
    let same = |s: &BattleState| {
        assert_eq!(s.units, units);
        assert_eq!(s.rng, rng);
        assert_eq!(s.state_hash(), hash);
        assert_eq!(s.queue, queue);
    };

    for (a, t) in [(P1, E1), (E1, P1), (P1, P2), (P1, 99 % 6)] {
        let _ = s.hit_chance(a, t);
    }
    let _ = (s.legal_targets(P1), s.reachable(P1), s.preview_move(P1, hex(-1, -2)), s.enemy_controllers_at(P1, hex(0, -2)));
    same(&s);

    // Rejections: not active, invalid target, same side, not adjacent, no AP, fatigue.
    assert_eq!(s.apply(Command::Attack { attacker: E1, target: P1 }), Err(RuleError::NotActiveUnit));
    assert_eq!(s.apply(Command::Attack { attacker: P1, target: 77 }), Err(RuleError::InvalidTarget));
    assert_eq!(s.apply(Command::Attack { attacker: P1, target: P2 }), Err(RuleError::InvalidTarget));
    assert_eq!(s.apply(Command::Attack { attacker: P1, target: E2 }), Err(RuleError::NotAdjacent));
    same(&s);
    s.units[P1 as usize].ap = 3;
    let (hash2, units2) = (s.state_hash(), s.units.clone());
    assert_eq!(s.apply(Command::Attack { attacker: P1, target: E1 }), Err(RuleError::NotEnoughAp));
    assert_eq!((s.state_hash(), &s.units), (hash2, &units2));
    s.units[P1 as usize].ap = 9;
    s.units[P1 as usize].fatigue = s.units[P1 as usize].stats.fatigue_max;
    let (hash3, units3) = (s.state_hash(), s.units.clone());
    assert_eq!(s.apply(Command::Attack { attacker: P1, target: E1 }), Err(RuleError::TooFatigued));
    assert_eq!((s.state_hash(), &s.units), (hash3, &units3));
    assert_eq!(stacks(&s, P1), 3);
    assert_eq!(stacks(&s, E1), 2);
}

#[test]
fn rng_consumption_matches_the_existing_hit_and_miss_branches() {
    let mut s = duel(P1);
    rig(&mut s, &[MISS]);
    let start = s.rng.state;
    s.apply(Command::Attack { attacker: P1, target: E1 }).unwrap();
    assert_eq!(s.rng, rng_after_state(start, 1), "a miss draws only the roll");
    assert_eq!(stacks(&s, P1), 1);

    activate(&mut s, P1);
    rig(&mut s, &[HIT]);
    let start = s.rng.state;
    let ev = s.apply(Command::Attack { attacker: P1, target: E1 }).unwrap();
    assert_eq!(ev.len(), 1, "no extra events for the counter");
    assert_eq!(s.rng, rng_after_state(start, 2), "a hit draws the roll and the damage");
    assert_eq!(stacks(&s, P1), 0);

    // A capable and an incapable attacker consume the same draws for the same rolls.
    let mut capable = duel(P1);
    let mut plain = duel(P1);
    plain.units[P1 as usize].has_fast_adaptation = false;
    rig(&mut capable, &[MISS]);
    plain.rng = capable.rng.clone();
    capable.apply(Command::Attack { attacker: P1, target: E1 }).unwrap();
    plain.apply(Command::Attack { attacker: P1, target: E1 }).unwrap();
    assert_eq!(capable.rng, plain.rng);
}

// ------------------------------------------------------------- hashing

/// Independent replica of the documented serialization for an all-Steady, flat,
/// undecided state, including the Fast Adaptation bits.
fn replica_hash(s: &BattleState) -> u64 {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    let mut put = |v: u64| {
        for b in v.to_le_bytes() {
            h ^= u64::from(b);
            h = h.wrapping_mul(0x0000_0100_0000_01b3);
        }
    };
    put(s.rng.state);
    put(u64::from(s.round));
    put(s.queue.len() as u64);
    for &q in &s.queue {
        put(u64::from(q));
    }
    put(0);
    put(0);
    for u in &s.units {
        assert_eq!(u.morale, Morale::Steady);
        put(u64::from(u.id));
        put(u64::from(u.hp));
        put(u64::from(u.armour));
        put(u64::from(u.ap));
        put(u64::from(u.fatigue));
        put(u.pos.q as i64 as u64);
        put(u.pos.r as i64 as u64);
        put(u64::from(u.alive)
            | u64::from(u.waited) << 1
            | u64::from(u.turn_started) << 2
            | u64::from(u.skip_turn) << 3
            | u64::from(u.fled) << 7
            | u64::from(u.retreating) << 8
            | u64::from(u.has_adrenaline) << 9
            | u64::from(u.adrenaline_pending) << 10
            | u64::from(u.has_rotation) << 11
            | u64::from(u.has_footwork) << 12
            | u64::from(u.has_fast_adaptation) << 13
            | u64::from(u.fast_adaptation_stacks) << 14);
        put(u64::from(u.stats.resolve));
    }
    h
}

#[test]
fn hash_is_sensitive_to_both_fields_and_uses_the_documented_bits() {
    let mut s = duel(P1);
    s.units[P1 as usize].has_fast_adaptation = false;
    let base = s.state_hash();
    assert_eq!(base, replica_hash(&s));

    let mut seen = vec![base];
    s.units[P1 as usize].has_fast_adaptation = true;
    let capable = s.state_hash();
    assert_eq!(capable, replica_hash(&s));
    seen.push(capable);
    for n in [1, 2, 3, 1 << 17, u32::MAX] {
        s.units[P1 as usize].fast_adaptation_stacks = n;
        let h = s.state_hash();
        assert_eq!(h, replica_hash(&s), "stacks {n}");
        assert!(!seen.contains(&h), "stacks {n} must hash distinctly");
        seen.push(h);
    }
    // The counter hashes even without the capability, and is restored exactly.
    s.units[P1 as usize].has_fast_adaptation = false;
    assert_ne!(s.state_hash(), base);
    assert_eq!(s.state_hash(), replica_hash(&s));
    s.units[P1 as usize].fast_adaptation_stacks = 0;
    assert_eq!(s.state_hash(), base);
    // The capability bit is distinct from the other skill flags.
    let mut other = duel(P1);
    other.units[P1 as usize].has_fast_adaptation = false;
    other.units[P1 as usize].has_footwork = true;
    s.units[P1 as usize].has_fast_adaptation = true;
    assert_ne!(other.state_hash(), s.state_hash());
}

#[test]
fn default_hashes_are_unchanged() {
    assert_eq!(BattleState::new(1).state_hash(), FLAT_SEED_1_HASH);
    assert_eq!(scenario::battle(1, TerrainPreset::Flat).state_hash(), FLAT_SCENARIO_HASH);
    assert_eq!(scenario::battle(1, TerrainPreset::Hills).state_hash(), HILLS_SCENARIO_HASH);
}
