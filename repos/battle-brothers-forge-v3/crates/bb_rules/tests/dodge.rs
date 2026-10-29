//! Headless melee Dodge (partial UC-COMBAT-058). The catalog states "+15% of
//! current initiative to melee and ranged defence" and that fatigue which tanks
//! initiative also tanks Dodge. The fatigue-to-initiative formula, flooring, the
//! zero floor, the post-morale addition, the sorting-only Fleeing/Adrenaline
//! adjustments, both-side applicability and the capability flag are an
//! APPROXIMATION. Fixtures park every unit far away, then place actors on the
//! obstacle-free lane `r = -2`. Every unit has skill 0 / defense 0 / damage 1;
//! `rig` picks an RNG state whose next draw takes the requested roll (99 always
//! misses, 0 always hits), so tests never depend on a particular seed.

use bb_rules::hex::{hex, map_hexes};
use bb_rules::rules::{ATTACK_AP, ATTACK_FATIGUE, DEFENDER_HIT_FATIGUE_GAIN, DEFENDER_MISS_FATIGUE_GAIN, FATIGUE_REGEN, MOVE_FATIGUE};
use bb_rules::scenario::{self, TerrainPreset};
use bb_rules::unit::DODGE_DEFENSE_PERCENT;
use bb_rules::*;

const P1: u32 = 0;
const P2: u32 = 1;
const E1: u32 = 3;
const E2: u32 = 4;

const PARK_PLAYERS: [Hex; 3] = [hex(-4, 2), hex(-4, 3), hex(-4, 4)];
const PARK_ENEMIES: [Hex; 3] = [hex(4, -4), hex(4, -3), hex(4, -2)];

/// Hash of `BattleState::new(1)` before Dodge existed.
const FLAT_SEED_1_HASH: u64 = 10_088_116_083_934_094_952;
/// Initial `scenario::battle(1, _)` hashes before Dodge existed.
const FLAT_SCENARIO_HASH: u64 = 0x8c00302113cbe668;
const HILLS_SCENARIO_HASH: u64 = 0xb759683cdff4bfa2;

const MISS: u32 = 99;
const HIT: u32 = 0;

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

/// A standalone unit with the given base initiative, defense and fatigue.
fn unit(initiative: u32, defense: u32, fatigue: u32, dodge: bool) -> Unit {
    let mut stats = scenario::units()[0].stats.clone();
    stats.initiative = initiative;
    stats.defense = defense;
    let mut u = Unit::new(9, "X", Side::Player, stats, hex(0, -2));
    u.fatigue = fatigue;
    u.has_dodge = dodge;
    u
}

fn rig(s: &mut BattleState, roll: u32) {
    for k in 1u64.. {
        let cand = s.rng.state.wrapping_add(k);
        if (Rng::new(cand).next_u64() % 100) as u32 == roll {
            s.rng = Rng::new(cand);
            return;
        }
    }
}

fn rng_after(state: u64, n: usize) -> Rng {
    let mut r = Rng::new(state);
    for _ in 0..n {
        r.next_u64();
    }
    r
}

/// Everything observable that a pure query or rejected command must not change.
fn snapshot(s: &BattleState) -> (u64, Rng, Vec<Unit>, Vec<u32>, u32) {
    (s.state_hash(), s.rng.clone(), s.units.clone(), s.queue.clone(), s.round)
}

fn attacked(ev: &Event) -> (u32, u32, bool, u32, u32) {
    match *ev {
        Event::Attacked { attacker, target, hit, roll, chance, .. } => (attacker, target, hit, roll, chance),
        ref other => panic!("expected Attacked, got {other:?}"),
    }
}

// ------------------------------------------------------------- defaults and constants

#[test]
fn units_and_scenarios_start_incapable_and_the_constant_is_fifteen() {
    assert_eq!(DODGE_DEFENSE_PERCENT, 15);
    assert!(!unit(100, 10, 0, false).has_dodge);
    let fresh = Unit::new(9, "X", Side::Enemy, scenario::units()[3].stats.clone(), hex(0, -2));
    assert!(!fresh.has_dodge);
    for s in [BattleState::new(1), scenario::battle(1, TerrainPreset::Flat), scenario::battle(1, TerrainPreset::Hills)] {
        assert!(s.units.iter().all(|u| !u.has_dodge && u.dodge_defense_bonus() == 0));
        assert!(s.units.iter().all(|u| u.effective_defense() == u.stats.defense));
    }
}

#[test]
fn default_hashes_are_unchanged() {
    assert_eq!(BattleState::new(1).state_hash(), FLAT_SEED_1_HASH);
    assert_eq!(scenario::battle(1, TerrainPreset::Flat).state_hash(), FLAT_SCENARIO_HASH);
    assert_eq!(scenario::battle(1, TerrainPreset::Hills).state_hash(), HILLS_SCENARIO_HASH);
}

// ------------------------------------------------------------- current initiative

#[test]
fn current_initiative_divides_fatigue_before_subtracting() {
    for (base, fatigue, expected) in [(100, 0, 100), (100, 1, 100), (100, 2, 99), (100, 3, 99), (100, 5, 98), (10, 5, 8), (10, 21, 0)] {
        assert_eq!(unit(base, 0, fatigue, false).current_initiative(), expected, "{base} / {fatigue}");
    }
}

#[test]
fn current_initiative_is_signed_and_unclamped() {
    assert_eq!(unit(10, 0, 100, false).current_initiative(), -40);
    assert_eq!(unit(0, 0, 1, false).current_initiative(), 0);
    assert_eq!(unit(0, 0, 3, false).current_initiative(), -1);
    assert_eq!(unit(0, 0, u32::MAX, false).current_initiative(), -2_147_483_647);
}

#[test]
fn current_initiative_handles_u32_extremes() {
    assert_eq!(unit(u32::MAX, 0, 0, false).current_initiative(), 4_294_967_295);
    assert_eq!(unit(u32::MAX, 0, u32::MAX, false).current_initiative(), 2_147_483_648);
}

#[test]
fn queue_only_adjustments_do_not_change_current_initiative() {
    let mut u = unit(100, 0, 0, true);
    u.morale = Morale::Fleeing;
    u.adrenaline_pending = true;
    assert_eq!(u.current_initiative(), 100);
}

// ------------------------------------------------------------- bonus arithmetic

#[test]
fn incapable_unit_gets_no_bonus_at_any_initiative() {
    for init in [0, 1, 100, u32::MAX] {
        let u = unit(init, 7, 0, false);
        assert_eq!(u.dodge_defense_bonus(), 0);
        assert_eq!(u.effective_defense(), 7);
    }
}

#[test]
fn bonus_floors_fifteen_percent_at_the_boundaries() {
    // floor(i * 15 / 100): 6 -> 0, 7 -> 1, 13 -> 1, 14 -> 2, 19 -> 2, 20 -> 3, 99 -> 14, 100 -> 15.
    for (init, expected) in [(0, 0), (1, 0), (6, 0), (7, 1), (13, 1), (14, 2), (19, 2), (20, 3), (66, 9), (67, 10), (99, 14), (100, 15), (101, 15)] {
        let u = unit(init, 0, 0, true);
        assert_eq!(u.dodge_defense_bonus(), expected, "initiative {init}");
        assert_eq!(u.effective_defense(), expected);
    }
}

#[test]
fn odd_fatigue_floors_before_the_percentage() {
    // Initiative 14: fatigue 0 and 1 leave 14 (bonus 2); fatigue 2 and 3 leave 13 (bonus 1).
    for (fatigue, init, bonus) in [(0, 14, 2), (1, 14, 2), (2, 13, 1), (3, 13, 1), (4, 12, 1), (14, 7, 1), (15, 7, 1), (16, 6, 0)] {
        let u = unit(14, 0, fatigue, true);
        assert_eq!((u.current_initiative(), u.dodge_defense_bonus()), (init, bonus), "fatigue {fatigue}");
    }
}

#[test]
fn zero_and_negative_current_initiative_give_no_bonus() {
    for fatigue in [20, 21, 40, 100] {
        let u = unit(10, 12, fatigue, true);
        assert!(u.current_initiative() <= 0);
        assert_eq!(u.dodge_defense_bonus(), 0);
        assert_eq!(u.effective_defense(), 12);
    }
    assert_eq!(unit(10, 0, 100, true).current_initiative(), -40);
}

#[test]
fn widened_arithmetic_survives_u32_max() {
    // 4_294_967_295 * 15 / 100 = 644_245_094 (floored); 2_147_483_648 * 15 / 100 = 322_122_547.
    assert_eq!(unit(u32::MAX, 0, 0, true).dodge_defense_bonus(), 644_245_094);
    assert_eq!(unit(u32::MAX, 0, u32::MAX, true).dodge_defense_bonus(), 322_122_547);
}

#[test]
fn effective_defense_saturates_at_u32_max() {
    assert_eq!(unit(100, u32::MAX, 0, true).effective_defense(), u32::MAX);
    assert_eq!(unit(100, u32::MAX - 14, 0, true).effective_defense(), u32::MAX);
    assert_eq!(unit(100, u32::MAX - 15, 0, true).effective_defense(), u32::MAX);
    assert_eq!(unit(100, u32::MAX - 16, 0, true).effective_defense(), u32::MAX - 1);
    let mut u = unit(u32::MAX, u32::MAX, 0, true);
    u.morale = Morale::Confident;
    assert_eq!(u.effective_defense(), u32::MAX);
}

// ------------------------------------------------------------- morale and base stats

#[test]
fn bonus_is_added_after_morale_and_not_scaled_by_it() {
    // Base defense 40, initiative 100 -> bonus 15.
    for (morale, scaled) in [
        (Morale::Steady, 40),
        (Morale::Confident, 44),
        (Morale::Wavering, 36),
        (Morale::Breaking, 32),
        (Morale::Fleeing, 28),
    ] {
        let mut u = unit(100, 40, 0, true);
        u.morale = morale;
        assert_eq!(u.dodge_defense_bonus(), 15, "{morale:?}");
        assert_eq!(u.effective_defense(), scaled + 15, "{morale:?}");
        let mut plain = u.clone();
        plain.has_dodge = false;
        assert_eq!(plain.effective_defense(), scaled);
    }
}

#[test]
fn base_stats_and_melee_skill_are_unchanged() {
    let mut u = unit(100, 40, 30, true);
    u.stats.melee_skill = 60;
    for morale in [Morale::Steady, Morale::Confident, Morale::Fleeing] {
        u.morale = morale;
        let stats = u.stats.clone();
        let skill = u.effective_melee_skill();
        let _ = (u.effective_defense(), u.dodge_defense_bonus(), u.current_initiative());
        assert_eq!(u.stats, stats);
        assert_eq!((u.stats.initiative, u.stats.defense, u.stats.melee_skill), (100, 40, 60));
        let mut plain = u.clone();
        plain.has_dodge = false;
        assert_eq!(skill, plain.effective_melee_skill());
        assert_eq!(skill, u.effective_melee_skill());
    }
}

#[test]
fn pending_adrenaline_and_fleeing_do_not_inflate_the_bonus() {
    let mut u = unit(100, 0, 0, true);
    let plain = u.effective_defense();
    u.adrenaline_pending = true;
    assert_eq!(u.dodge_defense_bonus(), 15);
    assert_eq!(u.effective_defense(), plain);
    u.morale = Morale::Fleeing;
    assert_eq!(u.dodge_defense_bonus(), 15);
    assert_eq!(u.effective_defense(), 15);
}

// ------------------------------------------------------------- hit chance

/// `attacker` (skill 50) beside `target` on the lane; the target is Dodge-capable at initiative 100.
fn duel(attacker: u32, target: u32) -> BattleState {
    let (ah, th) = if attacker < 3 { (hex(0, -2), hex(1, -2)) } else { (hex(1, -2), hex(0, -2)) };
    let mut s = board(attacker, &[(attacker, ah), (target, th)]);
    s.units[attacker as usize].stats.melee_skill = 50;
    s.units[target as usize].stats.initiative = 100;
    s.units[target as usize].has_dodge = true;
    s
}

#[test]
fn dodge_lowers_the_chance_against_either_faction() {
    for (attacker, target) in [(P1, E1), (E1, P1)] {
        let mut s = duel(attacker, target);
        assert_eq!(s.hit_chance(attacker, target), 35);
        s.units[target as usize].has_dodge = false;
        assert_eq!(s.hit_chance(attacker, target), 50);
        // The attacker's own capability never helps its offense.
        s.units[attacker as usize].has_dodge = true;
        assert_eq!(s.hit_chance(attacker, target), 50);
    }
}

#[test]
fn dodge_applies_to_a_non_engaged_query_too() {
    let mut s = board(P1, &[(P1, hex(-2, -2)), (E1, hex(3, -2))]);
    s.units[P1 as usize].stats.melee_skill = 50;
    s.units[E1 as usize].stats.initiative = 100;
    s.units[E1 as usize].has_dodge = true;
    assert_eq!(s.hit_chance(P1, E1), 35);
}

#[test]
fn dodge_composes_with_surround() {
    let mut s = duel(P1, E1);
    s.units[P2 as usize].pos = hex(1, -3);
    assert_fixture_valid(&s);
    s.units[P2 as usize].stats.melee_skill = 50;
    // The second ally adjacent to E1 (hex(1,-3) touches hex(1,-2)) adds 5.
    assert_eq!(s.hit_chance(P1, E1), 50 - 15 + 5);
}

#[test]
fn dodge_composes_with_elevation() {
    let mut s = duel(P1, E1);
    s.elevations.set(hex(0, -2), 1).unwrap();
    assert_eq!(s.hit_chance(P1, E1), 50 - 15 + 10);
    s.elevations.set(hex(0, -2), 0).unwrap();
    s.elevations.set(hex(1, -2), 2).unwrap();
    assert_eq!(s.hit_chance(P1, E1), 50 - 15 - 20);
}

#[test]
fn dodge_composes_with_fast_adaptation() {
    let mut s = duel(P1, E1);
    s.units[P1 as usize].has_fast_adaptation = true;
    s.units[P1 as usize].fast_adaptation_stacks = 2;
    assert_eq!(s.hit_chance(P1, E1), 50 - 15 + 20);
}

#[test]
fn hit_chance_clamps_still_apply() {
    let mut s = duel(P1, E1);
    s.units[P1 as usize].stats.melee_skill = 20;
    assert_eq!(s.hit_chance(P1, E1), 5); // exactly 20 - 15
    s.units[P1 as usize].stats.melee_skill = 10;
    assert_eq!(s.hit_chance(P1, E1), 5); // below the floor
    s.units[P1 as usize].stats.melee_skill = 110;
    assert_eq!(s.hit_chance(P1, E1), 95); // exactly 110 - 15
    s.units[P1 as usize].stats.melee_skill = 200;
    assert_eq!(s.hit_chance(P1, E1), 95);
    s.units[E1 as usize].stats.defense = u32::MAX;
    assert_eq!(s.hit_chance(P1, E1), 5);
}

// ------------------------------------------------------------- live fatigue

#[test]
fn defense_follows_movement_fatigue_without_a_cache() {
    // Initiative 20: bonus 3 at fatigue 0, 2 at fatigue 2 (current initiative 19).
    let mut s = board(P1, &[(P1, hex(-2, -2)), (E1, hex(3, -2))]);
    s.units[E1 as usize].stats.melee_skill = 50;
    s.units[P1 as usize].stats.initiative = 20;
    s.units[P1 as usize].has_dodge = true;
    assert_eq!(s.hit_chance(E1, P1), 47);
    s.apply(Command::Move { unit: P1, to: hex(-1, -2) }).unwrap();
    assert_eq!(s.units[P1 as usize].fatigue, MOVE_FATIGUE);
    assert_eq!(s.hit_chance(E1, P1), 48);
}

#[test]
fn attacking_lowers_the_attackers_own_defense() {
    // Initiative 20: bonus 3 at fatigue 0, 2 at fatigue 10 (current initiative 15).
    let mut s = duel(P1, E1);
    s.units[P1 as usize].stats.initiative = 20;
    s.units[P1 as usize].has_dodge = true;
    s.units[E1 as usize].has_dodge = false;
    s.units[E1 as usize].stats.melee_skill = 50;
    assert_eq!(s.hit_chance(E1, P1), 47);
    rig(&mut s, MISS);
    s.apply(Command::Attack { attacker: P1, target: E1 }).unwrap();
    assert_eq!(s.units[P1 as usize].fatigue, ATTACK_FATIGUE);
    assert_eq!(s.hit_chance(E1, P1), 48);
}

#[test]
fn incoming_hit_and_miss_fatigue_change_the_defenders_defense() {
    // Initiative 20 at fatigue 0 -> 3. A miss gives 2 fatigue (initiative 19 -> 2); a hit gives 5 (18 -> 2).
    for (roll, gain, bonus) in [(MISS, DEFENDER_MISS_FATIGUE_GAIN, 2), (HIT, DEFENDER_HIT_FATIGUE_GAIN, 2)] {
        let mut s = duel(E1, P1);
        s.units[P1 as usize].stats.initiative = 20;
        s.units[P1 as usize].has_dodge = true;
        s.units[E1 as usize].stats.melee_skill = 50;
        assert_eq!(s.units[P1 as usize].dodge_defense_bonus(), 3);
        let predicted = s.hit_chance(E1, P1);
        assert_eq!(predicted, 47);
        rig(&mut s, roll);
        let ev = s.apply(Command::Attack { attacker: E1, target: P1 }).unwrap();
        assert_eq!(attacked(&ev[0]), (E1, P1, roll == HIT, roll, predicted));
        assert_eq!(s.units[P1 as usize].fatigue, gain);
        assert_eq!(s.units[P1 as usize].dodge_defense_bonus(), bonus);
        assert_eq!(s.hit_chance(E1, P1), 50 - bonus);
    }
}

#[test]
fn turn_start_regeneration_raises_the_bonus_again() {
    // Initiative 20, fatigue 30 -> current 5, bonus 0; after regeneration fatigue 15 -> 13, bonus 1.
    let mut s = board(E1, &[(P1, hex(0, -2)), (E1, hex(1, -2))]);
    s.queue = vec![E1, P1];
    s.units[P1 as usize].stats.initiative = 20;
    s.units[P1 as usize].has_dodge = true;
    s.units[P1 as usize].fatigue = 30;
    s.units[E1 as usize].stats.melee_skill = 50;
    assert_eq!((s.units[P1 as usize].dodge_defense_bonus(), s.hit_chance(E1, P1)), (0, 50));
    s.apply(Command::EndTurn { unit: E1 }).unwrap();
    assert_eq!(s.queue.first(), Some(&P1));
    assert_eq!(s.units[P1 as usize].fatigue, 30 - FATIGUE_REGEN);
    assert_eq!((s.units[P1 as usize].dodge_defense_bonus(), s.hit_chance(E1, P1)), (1, 49));
}

// ------------------------------------------------------------- events and rng

#[test]
fn ordinary_attack_event_agrees_with_the_query_and_draws_no_extra_rng() {
    for (roll, draws) in [(MISS, 1), (HIT, 2)] {
        let mut s = duel(P1, E1);
        let mut twin = s.clone();
        twin.units[E1 as usize].has_dodge = false;
        rig(&mut s, roll);
        twin.rng = s.rng.clone();
        let before = s.rng.state;
        let predicted = s.hit_chance(P1, E1);
        let ev = s.apply(Command::Attack { attacker: P1, target: E1 }).unwrap();
        assert_eq!(attacked(&ev[0]), (P1, E1, roll == HIT, roll, predicted));
        assert_eq!(predicted, 35);
        assert_eq!(s.rng, rng_after(before, draws));
        // The incapable twin differs only in the event's chance.
        let twin_ev = twin.apply(Command::Attack { attacker: P1, target: E1 }).unwrap();
        assert_eq!(attacked(&twin_ev[0]).4, 50);
        assert_eq!(twin.rng, s.rng);
    }
}

#[test]
fn opportunity_attack_event_uses_the_live_fatigue_defense() {
    // The mover pays the step's fatigue before the reaction: initiative 20 at fatigue 2 -> bonus 2.
    for (mover, controller, from, to) in [(P1, E1, hex(0, -2), hex(-1, -2)), (E1, P1, hex(1, -2), hex(2, -2))] {
        let (mover_pos, controller_pos) = if mover == P1 { (from, hex(1, -2)) } else { (from, hex(0, -2)) };
        let mut s = board(mover, &[(mover, mover_pos), (controller, controller_pos)]);
        s.units[controller as usize].stats.melee_skill = 50;
        s.units[mover as usize].stats.initiative = 20;
        s.units[mover as usize].has_dodge = true;
        assert_eq!(s.hit_chance(controller, mover), 47);
        let mut preview = s.clone();
        preview.units[mover as usize].fatigue = MOVE_FATIGUE;
        let predicted = preview.hit_chance(controller, mover);
        assert_eq!(predicted, 48);
        rig(&mut s, MISS);
        let before = s.rng.state;
        let ev = s.apply(Command::Move { unit: mover, to }).unwrap();
        assert_eq!(attacked(&ev[0]), (controller, mover, false, MISS, predicted));
        assert_eq!(s.rng, rng_after(before, 1));
    }
}

// ------------------------------------------------------------- purity

#[test]
fn queries_and_rejected_commands_leave_everything_unchanged() {
    let mut s = duel(P1, E1);
    s.units[P1 as usize].has_dodge = true;
    s.units[P1 as usize].fatigue = 7;
    let before = snapshot(&s);
    for _ in 0..3 {
        for id in [P1, E1] {
            let u = &s.units[id as usize];
            let _ = (u.current_initiative(), u.dodge_defense_bonus(), u.effective_defense());
        }
        let _ = (s.hit_chance(P1, E1), s.hit_chance(E1, P1), s.legal_targets(P1), s.reachable(P1));
    }
    assert_eq!(snapshot(&s), before);
    // Rejected commands: attacking an out-of-range unit, and a unit that is not active.
    assert_eq!(s.apply(Command::Attack { attacker: P1, target: E2 }), Err(RuleError::NotAdjacent));
    assert!(s.apply(Command::Attack { attacker: E1, target: P1 }).is_err());
    assert_eq!(snapshot(&s), before);
    // Too little AP is rejected the same way.
    s.units[P1 as usize].ap = ATTACK_AP - 1;
    let before = snapshot(&s);
    assert_eq!(s.apply(Command::Attack { attacker: P1, target: E1 }), Err(RuleError::NotEnoughAp));
    assert_eq!(snapshot(&s), before);
}

// ------------------------------------------------------------- hashing

#[test]
fn the_flag_changes_the_hash_in_its_own_bit() {
    let base = BattleState::new(1);
    let mut on = base.clone();
    on.units[P1 as usize].has_dodge = true;
    assert_ne!(on.state_hash(), base.state_hash());
    let mut other = base.clone();
    other.units[E1 as usize].has_dodge = true;
    assert_ne!(other.state_hash(), on.state_hash());
    assert_ne!(other.state_hash(), base.state_hash());
    // Bit 46 does not overlap Fast Adaptation's counter (bits 14..=45): even a full counter
    // leaves the flag visible.
    let mut full = base.clone();
    full.units[P1 as usize].has_fast_adaptation = true;
    full.units[P1 as usize].fast_adaptation_stacks = u32::MAX;
    let mut full_dodge = full.clone();
    full_dodge.units[P1 as usize].has_dodge = true;
    assert_ne!(full.state_hash(), full_dodge.state_hash());
    // Toggling back restores the old hash.
    on.units[P1 as usize].has_dodge = false;
    assert_eq!(on.state_hash(), base.state_hash());
}

// ------------------------------------------------------------- round ordering

/// Expected queue: key `initiative - fatigue / 2 - 1000 (Fleeing) + 2000 (pending)`
/// descending, ties by id.
fn expected_queue(s: &BattleState) -> Vec<u32> {
    let key = |u: &Unit| {
        let mut k = i64::from(u.stats.initiative) - i64::from(u.fatigue / 2);
        if u.morale == Morale::Fleeing {
            k -= 1000;
        }
        if u.adrenaline_pending {
            k += 2000;
        }
        k
    };
    let mut ids: Vec<u32> = s.units.iter().filter(|u| u.is_present()).map(|u| u.id).collect();
    ids.sort_by(|&a, &b| key(&s.units[b as usize]).cmp(&key(&s.units[a as usize])).then(a.cmp(&b)));
    ids
}

/// Ends the only queued turn so the next round's queue is built from current values.
fn next_round_queue(mut s: BattleState) -> (Vec<u32>, BattleState) {
    let active = s.queue[0];
    s.queue = vec![active];
    s.apply(Command::EndTurn { unit: active }).unwrap();
    assert_eq!(s.round, 2);
    (s.queue.clone(), s)
}

#[test]
fn round_ordering_keeps_signed_keys_ties_and_queue_only_adjustments() {
    let mut s = board(P1, &[(P1, hex(0, -2)), (E1, hex(1, -2))]);
    // (initiative, fatigue) per unit: keys 49, 49 (tie), -40, 0, 100 and 100 (tie via odd fatigue).
    for (id, init, fatigue) in [(0, 50, 3), (1, 49, 0), (2, 10, 100), (3, 0, 0), (4, 101, 2), (5, 100, 0)] {
        s.units[id].stats.initiative = init;
        s.units[id].fatigue = fatigue;
    }
    let (plain, _) = next_round_queue(s.clone());
    assert_eq!(plain, vec![4, 5, 0, 1, 3, 2]);
    assert_eq!(plain, expected_queue(&s));

    // Dodge flags never enter ordering.
    let mut dodgers = s.clone();
    for u in &mut dodgers.units {
        u.has_dodge = true;
    }
    assert_eq!(next_round_queue(dodgers).0, plain);

    // Fleeing penalties keep their order below every non-Fleeing key (unclamped: -900 vs -950),
    // and pending Adrenaline outranks everyone and is then cleared.
    let mut mixed = s.clone();
    mixed.units[2].morale = Morale::Fleeing; // 10 - 50 - 1000 = -1040
    mixed.units[5].morale = Morale::Fleeing; // 100 - 1000 = -900
    mixed.units[3].adrenaline_pending = true; // 0 + 2000
    let expected = expected_queue(&mixed);
    assert_eq!(expected, vec![3, 4, 0, 1, 5, 2]);
    let (queue, after) = next_round_queue(mixed);
    assert_eq!(queue, expected);
    assert!(after.units.iter().all(|u| !u.adrenaline_pending));
}

#[test]
fn equal_current_initiatives_break_ties_by_id() {
    let mut s = board(P1, &[(P1, hex(0, -2)), (E1, hex(1, -2))]);
    for u in &mut s.units {
        u.stats.initiative = 60;
        u.fatigue = 0;
        u.has_dodge = true;
    }
    assert_eq!(next_round_queue(s).0, vec![0, 1, 2, 3, 4, 5]);
}
