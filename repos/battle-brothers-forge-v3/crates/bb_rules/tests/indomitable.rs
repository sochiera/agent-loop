//! Headless Indomitable (partial UC-COMBAT-016). The catalog lists the costs and a 50%
//! incoming-damage reduction, but its fidelity row stays unconfirmed. Everything else is
//! an APPROXIMATION: the 5 AP / 25 fatigue values, the reduction being applied to the
//! sampled melee damage before armour, integer flooring (no minimum of one), the
//! duration until the unit's next fresh turn, persistence through movement, the
//! cancellation when a rule-driven morale check makes the unit Fleeing, and the
//! capability flag. Fixtures park every unit far away, then place actors on the
//! obstacle-free lane `r = -2`. Units have skill 0 / defense 0, so a rigged roll of 0
//! always hits (chance clamps to 5) and 99 always misses; tests never depend on a seed.

use bb_rules::hex::{hex, map_hexes};
use bb_rules::rules::{
    ATTACK_AP, DEFENDER_HIT_FATIGUE_GAIN, INDOMITABLE_AP, INDOMITABLE_FATIGUE, MORALE_CHECK_HP_LOSS_THRESHOLD, MOVE_AP,
    MOVE_FATIGUE, indomitable_availability, soak,
};
use bb_rules::scenario::{self, TerrainPreset};
use bb_rules::*;

const P1: u32 = 0;
const P2: u32 = 1;
const E1: u32 = 3;
const E2: u32 = 4;

const PARK_PLAYERS: [Hex; 3] = [hex(-4, 2), hex(-4, 3), hex(-4, 4)];
const PARK_ENEMIES: [Hex; 3] = [hex(4, -4), hex(4, -3), hex(4, -2)];

/// Hash of `BattleState::new(1)` before Indomitable existed.
const FLAT_SEED_1_HASH: u64 = 10_088_116_083_934_094_952;
/// Initial `scenario::battle(1, _)` hashes before Indomitable existed.
const FLAT_SCENARIO_HASH: u64 = 0x8c00302113cbe668;
const HILLS_SCENARIO_HASH: u64 = 0xb759683cdff4bfa2;

const MISS: u32 = 99;
const HIT: u32 = 0;

fn indo(unit: u32) -> Command {
    Command::Indomitable { unit }
}

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

/// Distinct passable positions and living units with positive HP within the Stats maxima.
fn assert_fixture_valid(s: &BattleState) {
    let at: Vec<Hex> = s.units.iter().map(|u| u.pos).collect();
    for (i, h) in at.iter().enumerate() {
        assert!(map_hexes().contains(h) && !h.is_obstacle(), "fixture hex must be passable: {h:?}");
        assert!(!at[..i].contains(h), "fixture hexes must be distinct: {h:?}");
    }
    for u in s.units.iter().filter(|u| u.alive) {
        assert!(u.hp > 0 && u.hp <= u.stats.hp, "{}: hp {} outside 1..={}", u.name, u.hp, u.stats.hp);
        assert!(u.armour <= u.stats.armour && u.ap <= u.stats.ap_max && u.fatigue <= u.stats.fatigue_max, "{}", u.name);
    }
}

/// `attacker` beside `defender` on the lane; the attacker deals exactly `dmg`; the
/// defender is unarmoured, has the capability with the stance active, and `hp` HP.
fn duel(attacker: u32, defender: u32, hp: u32, dmg: u32) -> BattleState {
    let (p, e) = if attacker < 3 { (attacker, defender) } else { (defender, attacker) };
    let mut s = board(attacker, &[(p, hex(0, -2)), (e, hex(1, -2))]);
    let a = &mut s.units[attacker as usize];
    a.stats.dmg_min = dmg;
    a.stats.dmg_max = dmg;
    let d = &mut s.units[defender as usize];
    d.stats.hp = d.stats.hp.max(hp);
    d.hp = hp;
    d.armour = 0;
    d.stats.armour = 0;
    d.has_indomitable = true;
    d.indomitable_active = true;
    assert_fixture_valid(&s);
    s
}

/// Set the RNG so draw `i` (mod 100) equals `rolls[i]` where given (`None` = any).
fn rig(s: &mut BattleState, rolls: &[Option<u32>]) {
    for k in 1u64.. {
        let cand = s.rng.state.wrapping_add(k);
        let mut r = Rng::new(cand);
        if rolls.iter().all(|want| {
            let got = (r.next_u64() % 100) as u32;
            want.is_none_or(|w| w == got)
        }) {
            s.rng = Rng::new(cand);
            return;
        }
    }
}

fn rng_after(rng: &Rng, n: usize) -> Rng {
    let mut r = rng.clone();
    for _ in 0..n {
        r.next_u64();
    }
    r
}

fn activate(s: &mut BattleState, id: u32) {
    s.queue.retain(|&q| q != id);
    s.queue.insert(0, id);
    let u = &mut s.units[id as usize];
    u.ap = 9;
    u.fatigue = 0;
    u.turn_started = true;
}

/// Everything observable that a pure query or rejected command must not change.
fn snapshot(s: &BattleState) -> (u64, Rng, Vec<Unit>, Vec<u32>, u32, Option<Outcome>) {
    (s.state_hash(), s.rng.clone(), s.units.clone(), s.queue.clone(), s.round, s.outcome())
}

fn assert_rejected(s: &mut BattleState, cmd: Command, err: RuleError) {
    let before = snapshot(s);
    assert_eq!(s.apply(cmd), Err(err), "{cmd:?}");
    assert_eq!(snapshot(s), before, "{cmd:?} must mutate nothing");
}

/// One rigged ordinary attack through `Command::Attack`.
fn swing(s: &mut BattleState, attacker: u32, target: u32, roll: u32) -> Vec<Event> {
    activate(s, attacker);
    rig(s, &[Some(roll)]);
    s.apply(Command::Attack { attacker, target }).unwrap()
}

fn attacked(ev: &[Event]) -> (bool, u32, u32, u32) {
    ev.iter()
        .find_map(|e| match e {
            Event::Attacked { hit, dmg, absorbed, hp_loss, .. } => Some((*hit, *dmg, *absorbed, *hp_loss)),
            _ => None,
        })
        .expect("an Attacked event")
}

// ------------------------------------------------------------- defaults and constants

#[test]
fn units_and_scenarios_start_without_capability_or_active_stance() {
    assert_eq!((INDOMITABLE_AP, INDOMITABLE_FATIGUE), (5, 25));
    let fresh = Unit::new(9, "X", Side::Enemy, scenario::units()[3].stats.clone(), hex(0, -2));
    assert!(!fresh.has_indomitable && !fresh.indomitable_active && !fresh.is_indomitable_active());
    for s in [BattleState::new(1), scenario::battle(1, TerrainPreset::Flat), scenario::battle(1, TerrainPreset::Hills)] {
        assert!(s.units.iter().all(|u| !u.has_indomitable && !u.indomitable_active));
    }
}

#[test]
fn effective_stance_needs_capability_flag_presence_and_non_fleeing_morale() {
    let mut s = board(P1, &[(P1, hex(0, -2))]);
    let u = &mut s.units[P1 as usize];
    assert!(!u.is_indomitable_active());
    u.has_indomitable = true;
    assert!(!u.is_indomitable_active(), "capability alone is not a stance");
    u.indomitable_active = true;
    assert!(u.is_indomitable_active());
    for m in [Morale::Confident, Morale::Steady, Morale::Wavering, Morale::Breaking] {
        u.morale = m;
        assert!(u.is_indomitable_active(), "{m:?}");
    }
    u.morale = Morale::Fleeing;
    assert!(!u.is_indomitable_active());
    u.morale = Morale::Steady;
    u.fled = true;
    assert!(!u.is_indomitable_active());
    u.fled = false;
    u.alive = false;
    assert!(!u.is_indomitable_active());
    u.alive = true;
    u.has_indomitable = false;
    assert!(!u.is_indomitable_active(), "the flag without the capability is inert");
}

// ------------------------------------------------------------- activation

#[test]
fn success_for_both_sides_costs_exactly_and_changes_nothing_else() {
    for actor in [P1, E1] {
        let mut s = board(actor, &[]);
        s.units[actor as usize].has_indomitable = true;
        s.units[actor as usize].fatigue = 7;
        let (queue, round, rng, others) = (s.queue.clone(), s.round, s.rng.clone(), s.units.clone());
        assert_eq!(indomitable_availability(&s.units[actor as usize]), Ok(()));
        let ev = s.apply(indo(actor)).unwrap();
        assert_eq!(ev, vec![Event::IndomitableUsed { unit: actor }]);
        let u = &s.units[actor as usize];
        assert_eq!((u.ap, u.fatigue), (9 - INDOMITABLE_AP, 7 + INDOMITABLE_FATIGUE));
        assert!(u.indomitable_active && u.is_indomitable_active());
        assert_eq!((s.queue.clone(), s.round, s.outcome()), (queue, round, None), "no turn advance");
        assert_eq!(s.rng, rng, "no RNG");
        for (i, (now, then)) in s.units.iter().zip(&others).enumerate() {
            if i as u32 != actor {
                assert_eq!(now, then, "unit {i}");
            }
        }
        let mut expected = others[actor as usize].clone();
        expected.ap -= INDOMITABLE_AP;
        expected.fatigue += INDOMITABLE_FATIGUE;
        expected.indomitable_active = true;
        assert_eq!(s.units[actor as usize], expected, "only AP, fatigue and the flag change");
    }
}

#[test]
fn ap_and_fatigue_limits_including_the_exact_cap() {
    let mut s = board(P1, &[]);
    s.units[P1 as usize].has_indomitable = true;
    s.units[P1 as usize].ap = INDOMITABLE_AP - 1;
    assert_rejected(&mut s, indo(P1), RuleError::NotEnoughAp);
    s.units[P1 as usize].ap = INDOMITABLE_AP;
    let max = s.units[P1 as usize].stats.fatigue_max;
    s.units[P1 as usize].fatigue = max - INDOMITABLE_FATIGUE + 1;
    assert_rejected(&mut s, indo(P1), RuleError::TooFatigued);
    s.units[P1 as usize].fatigue = max - INDOMITABLE_FATIGUE;
    assert!(s.apply(indo(P1)).is_ok());
    let u = &s.units[P1 as usize];
    assert_eq!((u.ap, u.fatigue), (0, max), "exact AP and exact fatigue cap are accepted");
}

#[test]
fn availability_is_pure_ordered_and_checked_against_u32_max() {
    let base = scenario::units()[0].clone();
    let mut u = base.clone();
    u.ap = 9;
    assert_eq!(indomitable_availability(&u), Err(RuleError::MissingIndomitable));
    u.has_indomitable = true;
    u.indomitable_active = true;
    u.ap = 0;
    u.fatigue = u.stats.fatigue_max;
    assert_eq!(indomitable_availability(&u), Err(RuleError::IndomitableActive));
    u.indomitable_active = false;
    assert_eq!(indomitable_availability(&u), Err(RuleError::NotEnoughAp));
    u.ap = INDOMITABLE_AP;
    assert_eq!(indomitable_availability(&u), Err(RuleError::TooFatigued));
    u.fatigue = 0;
    let copy = u.clone();
    assert_eq!(indomitable_availability(&u), Ok(()));
    assert_eq!(u, copy, "the query mutates nothing");

    // The sum is checked: it neither wraps into an affordable value nor panics.
    u.stats.fatigue_max = u32::MAX;
    u.fatigue = u32::MAX - INDOMITABLE_FATIGUE + 1;
    assert_eq!(indomitable_availability(&u), Err(RuleError::TooFatigued));
    u.fatigue = u32::MAX;
    assert_eq!(indomitable_availability(&u), Err(RuleError::TooFatigued));
    u.fatigue = u32::MAX - INDOMITABLE_FATIGUE;
    assert_eq!(indomitable_availability(&u), Ok(()), "lands exactly on u32::MAX");
    u.stats.ap_max = u32::MAX;
    u.ap = u32::MAX;
    assert_eq!(indomitable_availability(&u), Ok(()));
}

#[test]
fn availability_agrees_with_apply_on_every_unit_level_case() {
    for bits in 0..16u32 {
        let mut s = board(P1, &[]);
        let u = &mut s.units[P1 as usize];
        u.has_indomitable = bits & 1 != 0;
        u.indomitable_active = bits & 2 != 0;
        u.ap = if bits & 4 != 0 { INDOMITABLE_AP } else { INDOMITABLE_AP - 1 };
        u.fatigue = if bits & 8 != 0 { u.stats.fatigue_max - INDOMITABLE_FATIGUE } else { u.stats.fatigue_max };
        match indomitable_availability(&s.units[P1 as usize]) {
            Ok(()) => assert!(s.apply(indo(P1)).is_ok(), "{bits:#06b}"),
            Err(e) => assert_rejected(&mut s, indo(P1), e),
        }
    }
}

#[test]
fn validation_precedence_and_pure_rejections() {
    // Battle over comes before everything, even for an inactive unit without the capability.
    let mut s = board(P1, &[]);
    s.outcome = Some(Outcome::Victory);
    assert_rejected(&mut s, indo(E2), RuleError::BattleOver);

    // Inactive actor.
    let mut s = board(P1, &[]);
    s.units[E1 as usize].has_indomitable = true;
    assert_rejected(&mut s, indo(E1), RuleError::NotActiveUnit);

    // Retreat intent beats Fleeing beats everything unit-level.
    let mut s = board(P1, &[]);
    s.units[P1 as usize].retreating = true;
    s.units[P1 as usize].morale = Morale::Fleeing;
    assert_rejected(&mut s, indo(P1), RuleError::RetreatingUnit);
    s.units[P1 as usize].retreating = false;
    assert_rejected(&mut s, indo(P1), RuleError::FleeingUnit);

    // An active unit that is no longer present is rejected before the capability check.
    let mut s = board(P1, &[]);
    s.units[P1 as usize].fled = true;
    assert_rejected(&mut s, indo(P1), RuleError::NotActiveUnit);

    // Capability, then active, then AP, then fatigue.
    let mut s = board(P1, &[]);
    s.units[P1 as usize].indomitable_active = true;
    s.units[P1 as usize].ap = 0;
    s.units[P1 as usize].fatigue = s.units[P1 as usize].stats.fatigue_max;
    assert_rejected(&mut s, indo(P1), RuleError::MissingIndomitable);
    s.units[P1 as usize].has_indomitable = true;
    assert_rejected(&mut s, indo(P1), RuleError::IndomitableActive);
    s.units[P1 as usize].indomitable_active = false;
    assert_rejected(&mut s, indo(P1), RuleError::NotEnoughAp);
    s.units[P1 as usize].ap = INDOMITABLE_AP;
    assert_rejected(&mut s, indo(P1), RuleError::TooFatigued);
}

#[test]
fn repeated_activation_is_rejected_without_extra_cost_even_after_wait() {
    let mut s = board(P1, &[]);
    s.units[P1 as usize].has_indomitable = true;
    s.apply(indo(P1)).unwrap();
    assert_rejected(&mut s, indo(P1), RuleError::IndomitableActive);
    s.apply(Command::Wait { unit: P1 }).unwrap();
    while s.queue[0] != P1 {
        let a = s.queue[0];
        s.apply(Command::EndTurn { unit: a }).unwrap();
    }
    s.units[P1 as usize].ap = 9;
    assert_rejected(&mut s, indo(P1), RuleError::IndomitableActive);
    assert_eq!(s.units[P1 as usize].fatigue, INDOMITABLE_FATIGUE);
}

// ------------------------------------------------------------- damage

#[test]
fn sampled_damage_is_halved_with_floor_before_armour_for_both_factions() {
    let max = u32::MAX;
    let table = [(0, 0), (1, 0), (2, 1), (3, 1), (4, 2), (29, 14), (30, 15), (max, max / 2)];
    for (attacker, defender) in [(E1, P1), (P1, E1)] {
        for armour in [0, 3] {
            for (sampled, reduced) in table {
                let mut s = duel(attacker, defender, 100, sampled);
                let d = &mut s.units[defender as usize];
                d.stats.hp = max;
                d.hp = max;
                d.stats.armour = armour;
                d.armour = armour;
                let mut plain = s.clone();
                plain.units[defender as usize].indomitable_active = false;

                rig(&mut s, &[Some(HIT)]);
                plain.rng = s.rng.clone();
                let before = s.rng.clone();
                let ev = swing_keep_rng(&mut s, attacker, defender);
                let (absorbed, hp_loss) = soak(armour, reduced);
                assert_eq!(attacked(&ev), (true, reduced, absorbed, hp_loss), "{attacker}->{defender} {sampled} a{armour}");
                let d = &s.units[defender as usize];
                assert_eq!((d.armour, d.hp), (armour - absorbed, max - hp_loss));
                let draws = 2 + usize::from(hp_loss >= MORALE_CHECK_HP_LOSS_THRESHOLD);
                assert_eq!(s.rng, rng_after(&before, draws), "no draw changes: roll, damage, maybe a morale check");

                let ev = swing_keep_rng(&mut plain, attacker, defender);
                let (absorbed, hp_loss) = soak(armour, sampled);
                assert_eq!(attacked(&ev), (true, sampled, absorbed, hp_loss), "unchanged without the stance");
            }
        }
    }
}

/// `Command::Attack` from the already-active attacker without touching the RNG first.
fn swing_keep_rng(s: &mut BattleState, attacker: u32, target: u32) -> Vec<Event> {
    s.units[attacker as usize].ap = 9;
    s.apply(Command::Attack { attacker, target }).unwrap()
}

#[test]
fn reduction_leaves_damage_sampling_untouched_over_a_range() {
    let mut hits = 0;
    for seed in 0..1000u64 {
        let mut stanced = duel(E1, P1, 100, 1);
        stanced.units[E1 as usize].stats.dmg_max = 7;
        let mut plain = stanced.clone();
        plain.units[P1 as usize].indomitable_active = false;
        stanced.rng = Rng::new(seed);
        plain.rng = Rng::new(seed);
        let a = swing_keep_rng(&mut stanced, E1, P1);
        let b = swing_keep_rng(&mut plain, E1, P1);
        let ((hit_a, dmg_a, _, _), (hit_b, dmg_b, _, _)) = (attacked(&a), attacked(&b));
        assert_eq!(hit_a, hit_b);
        if hit_a {
            hits += 1;
            assert_eq!(dmg_a, dmg_b / 2, "seed {seed}");
            assert!((1..=7).contains(&dmg_b));
        }
        assert_eq!(stanced.rng, plain.rng, "same draws consumed (seed {seed})");
    }
    assert!(hits > 20, "fixture must exercise hits");
}

#[test]
fn a_miss_is_unchanged_and_an_ineffective_stance_does_not_reduce() {
    let mut s = duel(E1, P1, 100, 8);
    let ev = swing(&mut s, E1, P1, MISS);
    assert_eq!(attacked(&ev), (false, 0, 0, 0));

    // Each missing condition leaves the full damage.
    for variant in 0..4 {
        let mut s = duel(E1, P1, 100, 8);
        let u = &mut s.units[P1 as usize];
        match variant {
            0 => u.has_indomitable = false,
            1 => u.indomitable_active = false,
            2 => u.morale = Morale::Fleeing,
            _ => {}
        }
        let ev = swing(&mut s, E1, P1, HIT);
        let expect = if variant == 3 { 4 } else { 8 };
        assert_eq!(attacked(&ev).1, expect, "variant {variant}");
    }

    // The stance protects only its owner: an active attacker deals full damage.
    let mut s = duel(E1, P1, 100, 8);
    s.units[E1 as usize].has_indomitable = true;
    s.units[E1 as usize].indomitable_active = true;
    s.units[P1 as usize].stats.dmg_min = 8;
    s.units[P1 as usize].stats.dmg_max = 8;
    let ev = swing(&mut s, P1, E1, HIT);
    assert_eq!(attacked(&ev).1, 4, "E1's own stance halves the hit it receives");
    let ev = swing(&mut s, E1, P1, HIT);
    assert_eq!(attacked(&ev).1, 4);
}

#[test]
fn zero_damage_hit_still_counts_as_a_hit_for_fatigue_and_fast_adaptation() {
    let mut s = duel(E1, P1, 40, 1);
    s.units[E1 as usize].has_fast_adaptation = true;
    s.units[E1 as usize].fast_adaptation_stacks = 3;
    let fatigue = s.units[P1 as usize].fatigue;
    let ev = swing(&mut s, E1, P1, HIT);
    assert_eq!(ev.len(), 1, "no morale check or other event: {ev:?}");
    assert!(matches!(ev[0], Event::Attacked { hit: true, dmg: 0, absorbed: 0, hp_loss: 0, .. }), "{ev:?}");
    let p = &s.units[P1 as usize];
    assert_eq!((p.hp, p.armour, p.fatigue), (40, 0, fatigue + DEFENDER_HIT_FATIGUE_GAIN));
    assert_eq!(s.units[E1 as usize].fast_adaptation_stacks, 0, "any hit resets the stacks");
    assert_eq!(s.units[E1 as usize].ap, 9 - ATTACK_AP);
}

#[test]
fn zero_damage_opportunity_attack_still_interrupts_movement() {
    let mut s = duel(E1, P1, 40, 1);
    activate(&mut s, P1);
    s.units[E1 as usize].has_fast_adaptation = true;
    s.units[E1 as usize].fast_adaptation_stacks = 2;
    rig(&mut s, &[Some(HIT)]);
    let before = s.rng.clone();
    let ev = s.apply(Command::Move { unit: P1, to: hex(-1, -2) }).unwrap();
    assert_eq!(ev.len(), 1, "{ev:?}");
    assert!(
        matches!(ev[0], Event::Attacked { attacker: E1, target: P1, hit: true, dmg: 0, hp_loss: 0, .. }),
        "{ev:?}"
    );
    let p = &s.units[P1 as usize];
    assert_eq!(p.pos, hex(0, -2), "the mitigated hit still stops the mover");
    assert_eq!((p.hp, p.ap), (40, 9 - MOVE_AP));
    assert_eq!(p.fatigue, MOVE_FATIGUE + DEFENDER_HIT_FATIGUE_GAIN);
    assert_eq!(s.units[E1 as usize].fast_adaptation_stacks, 0);
    assert_eq!(s.units[E1 as usize].ap, 9, "an opportunity attack costs the attacker nothing");
    assert_eq!(s.rng, rng_after(&before, 2));

    // An opportunity attack with real damage is halved too.
    let mut s = duel(E1, P1, 40, 9);
    activate(&mut s, P1);
    rig(&mut s, &[Some(HIT)]);
    let ev = s.apply(Command::Move { unit: P1, to: hex(-1, -2) }).unwrap();
    assert_eq!(attacked(&ev), (true, 4, 0, 4));
    assert_eq!(s.units[P1 as usize].hp, 36);
}

#[test]
fn reduction_can_prevent_a_lethal_hit_and_still_kills_when_the_reduced_loss_is_lethal() {
    // 19 -> 9 leaves one HP; 20 -> 10 is still lethal.
    let mut s = duel(E1, P1, 10, 19);
    let ev = swing(&mut s, E1, P1, HIT);
    assert_eq!(attacked(&ev), (true, 9, 0, 9));
    assert!(s.units[P1 as usize].alive && s.units[P1 as usize].hp == 1);
    assert!(!ev.iter().any(|e| matches!(e, Event::Died { .. })));
    let mut s = duel(E1, P1, 10, 20);
    let ev = swing(&mut s, E1, P1, HIT);
    assert_eq!(attacked(&ev), (true, 10, 0, 10));
    assert!(!s.units[P1 as usize].alive && ev.contains(&Event::Died { unit: P1 }));

    // Armour first absorbs the reduced damage: 12 -> 6, armour 5 leaves 1 + 6/10 = 1.
    let mut s = duel(E1, P1, 10, 12);
    s.units[P1 as usize].stats.armour = 5;
    s.units[P1 as usize].armour = 5;
    let ev = swing(&mut s, E1, P1, HIT);
    assert_eq!(attacked(&ev), (true, 6, 5, 1));
    assert_eq!((s.units[P1 as usize].armour, s.units[P1 as usize].hp), (0, 9));
}

#[test]
fn nine_lives_sees_the_reduced_loss() {
    // Reduced 9 < hp 10: no lethal hit, so the protection is not spent.
    let mut s = duel(E1, P1, 10, 19);
    s.units[P1 as usize].has_nine_lives = true;
    let ev = swing(&mut s, E1, P1, HIT);
    assert_eq!(attacked(&ev), (true, 9, 0, 9));
    assert!(!ev.contains(&Event::NineLivesTriggered { unit: P1 }));
    assert!(!s.units[P1 as usize].nine_lives_used);

    // Reduced 15 >= hp 10: still lethal, Nine Lives triggers and reports the actual loss.
    let mut s = duel(E1, P1, 10, 30);
    s.units[P1 as usize].has_nine_lives = true;
    let ev = swing(&mut s, E1, P1, HIT);
    assert_eq!(attacked(&ev), (true, 15, 0, 9));
    assert_eq!(ev[1], Event::NineLivesTriggered { unit: P1 });
    assert!(s.units[P1 as usize].alive && s.units[P1 as usize].hp == 1 && s.units[P1 as usize].nine_lives_used);
}

// ------------------------------------------------------------- morale

#[test]
fn heavy_hit_threshold_uses_the_resulting_hp_loss() {
    // Unreduced 29 would trigger a check; reduced 14 must not.
    let mut s = duel(E1, P1, 200, 29);
    s.units[P1 as usize].stats.resolve = 0;
    s.units[P1 as usize].morale = Morale::Breaking;
    let ev = swing(&mut s, E1, P1, HIT);
    assert_eq!(attacked(&ev), (true, 14, 0, 14));
    assert_eq!(ev.len(), 1, "{ev:?}");
    assert_eq!(s.units[P1 as usize].morale, Morale::Breaking);
    assert!(s.units[P1 as usize].indomitable_active);

    // Reduced 15 reaches the threshold exactly.
    let mut s = duel(E1, P1, 200, 30);
    s.units[P1 as usize].stats.resolve = 100;
    let ev = swing(&mut s, E1, P1, HIT);
    assert_eq!(attacked(&ev), (true, 15, 0, 15));
    assert!(matches!(ev[1], Event::MoraleChecked { unit: P1, .. }), "{ev:?}");
    assert!(s.units[P1 as usize].indomitable_active, "a passed check keeps the stance");
}

#[test]
fn heavy_hit_check_that_makes_the_unit_fleeing_cancels_the_stance() {
    for (attacker, defender) in [(E1, P1), (P1, E1)] {
        let mut s = duel(attacker, defender, 200, 40);
        s.units[defender as usize].stats.resolve = 0;
        s.units[defender as usize].morale = Morale::Wavering;
        let ev = swing(&mut s, attacker, defender, HIT);
        assert_eq!(attacked(&ev).1, 20);
        let d = &s.units[defender as usize];
        assert_eq!(d.morale, Morale::Breaking, "one step only");
        assert!(d.indomitable_active, "Breaking keeps the stance");

        let ev = swing(&mut s, attacker, defender, HIT);
        assert_eq!(attacked(&ev).1, 20);
        let d = &s.units[defender as usize];
        assert_eq!(d.morale, Morale::Fleeing);
        assert!(!d.indomitable_active && d.has_indomitable && !d.is_indomitable_active());

        // The very next hit lands at full strength.
        let ev = swing(&mut s, attacker, defender, HIT);
        assert_eq!(attacked(&ev).1, 40);
    }
}

#[test]
fn ally_death_check_that_makes_the_unit_fleeing_cancels_the_stance() {
    // P2 dies to E1; P1 (stance, Breaking, Resolve 0) fails the ally-death check.
    let mut s = board(E1, &[(P2, hex(0, -2)), (E1, hex(1, -2))]);
    s.units[P2 as usize].hp = 1;
    s.units[P2 as usize].armour = 0;
    let p1 = &mut s.units[P1 as usize];
    p1.has_indomitable = true;
    p1.indomitable_active = true;
    p1.morale = Morale::Breaking;
    p1.stats.resolve = 0;
    let ev = swing(&mut s, E1, P2, HIT);
    assert!(ev.contains(&Event::Died { unit: P2 }), "{ev:?}");
    assert!(
        ev.iter().any(|e| matches!(e, Event::AllyDeathMoraleChecked { unit: P1, before: Morale::Breaking, after: Morale::Fleeing, .. })),
        "{ev:?}"
    );
    let p1 = &s.units[P1 as usize];
    assert_eq!(p1.morale, Morale::Fleeing);
    assert!(!p1.indomitable_active && p1.has_indomitable);

    // One step above Breaking: the lowered morale keeps the stance.
    let mut s = board(E1, &[(P2, hex(0, -2)), (E1, hex(1, -2))]);
    s.units[P2 as usize].hp = 1;
    s.units[P2 as usize].armour = 0;
    let p1 = &mut s.units[P1 as usize];
    p1.has_indomitable = true;
    p1.indomitable_active = true;
    p1.morale = Morale::Wavering;
    p1.stats.resolve = 0;
    swing(&mut s, E1, P2, HIT);
    assert_eq!(s.units[P1 as usize].morale, Morale::Breaking);
    assert!(s.units[P1 as usize].is_indomitable_active());
}

#[test]
fn self_rally_does_not_restore_a_cancelled_stance() {
    let mut s = duel(E1, P1, 200, 40);
    s.units[P1 as usize].stats.resolve = 0; // the heavy-hit check fails
    s.units[P1 as usize].morale = Morale::Breaking;
    swing(&mut s, E1, P1, HIT);
    assert_eq!(s.units[P1 as usize].morale, Morale::Fleeing);
    assert!(!s.units[P1 as usize].indomitable_active);

    s.units[P1 as usize].stats.resolve = 100;
    s.units[E1 as usize].pos = PARK_ENEMIES[0];
    let mut rallied = false;
    for _ in 0..8 {
        let a = s.queue[0];
        if a == P1 {
            break;
        }
        let ev = s.apply(Command::EndTurn { unit: a }).unwrap();
        rallied |= ev.iter().any(|e| matches!(e, Event::SelfRallyChecked { unit: P1, after: Morale::Breaking, .. }));
    }
    assert_eq!(s.queue[0], P1);
    assert!(rallied, "the self-rally check ran at P1's fresh turn");
    let p1 = &s.units[P1 as usize];
    assert_eq!(p1.morale, Morale::Breaking);
    assert!(!p1.indomitable_active && !p1.is_indomitable_active());
}

// ------------------------------------------------------------- lifecycle

/// Board with P1 the lowest-initiative unit (so it acts last each round) and capable.
fn lifecycle_board() -> BattleState {
    let mut s = board(P1, &[]);
    for u in &mut s.units {
        u.stats.initiative = 50;
    }
    s.units[P1 as usize].stats.initiative = 0;
    s.units[P1 as usize].has_indomitable = true;
    s
}

#[test]
fn stance_survives_wait_and_intervening_turns_without_a_refresh() {
    let mut s = lifecycle_board();
    s.apply(indo(P1)).unwrap();
    s.apply(Command::Wait { unit: P1 }).unwrap();
    assert!(s.units[P1 as usize].indomitable_active, "Wait does not expire it");
    while s.queue[0] != P1 {
        let a = s.queue[0];
        assert!(s.units[P1 as usize].indomitable_active);
        s.apply(Command::EndTurn { unit: a }).unwrap();
    }
    assert_eq!(s.round, 1);
    let p1 = &s.units[P1 as usize];
    assert!(p1.indomitable_active, "the Wait return is not a fresh turn");
    assert_eq!(p1.ap, 9 - INDOMITABLE_AP, "and nothing was refreshed");
}

#[test]
fn end_turn_and_a_round_change_alone_do_not_expire_it_but_the_next_fresh_turn_does() {
    let mut s = lifecycle_board();
    s.apply(indo(P1)).unwrap();
    s.apply(Command::EndTurn { unit: P1 }).unwrap();
    assert!(s.units[P1 as usize].indomitable_active, "EndTurn keeps it");
    // Play the rest of round 1 and enter round 2; P1 acts last so its turn has not begun.
    while s.round == 1 {
        let a = s.queue[0];
        s.apply(Command::EndTurn { unit: a }).unwrap();
    }
    assert_eq!(s.round, 2);
    assert_ne!(s.queue[0], P1);
    assert!(s.units[P1 as usize].indomitable_active, "the round number changed, the stance stayed");
    loop {
        let a = s.queue[0];
        let ev = s.apply(Command::EndTurn { unit: a }).unwrap();
        if s.queue[0] == P1 {
            assert!(ev.contains(&Event::TurnStarted { unit: P1 }), "{ev:?}");
            break;
        }
        assert!(s.units[P1 as usize].indomitable_active);
    }
    let p1 = &s.units[P1 as usize];
    assert!(!p1.indomitable_active && p1.has_indomitable, "cleared by the fresh turn refresh");
    assert_eq!(p1.ap, 9);
    // The stance can be used again.
    assert!(s.apply(indo(P1)).is_ok());
}

#[test]
fn end_round_keeps_the_issuers_stance_and_a_passed_over_unit_loses_its_own() {
    let mut s = lifecycle_board();
    s.units[P2 as usize].stats.initiative = 10; // passed over after the enemies, before P1
    s.units[P2 as usize].has_indomitable = true;
    s.units[P2 as usize].indomitable_active = true;
    s.apply(indo(P1)).unwrap();
    s.apply(Command::EndRound { unit: P1 }).unwrap();
    assert!(s.units[P1 as usize].indomitable_active, "EndRound does not expire the issuer's stance");
    let mut passed_over = false;
    while s.round == 1 {
        let a = s.queue[0];
        let ev = s.apply(Command::EndTurn { unit: a }).unwrap();
        if s.round == 1 && ev.contains(&Event::TurnStarted { unit: P2 }) {
            passed_over = true;
            assert!(!s.units[P2 as usize].indomitable_active, "the skipped unit's refresh expired it");
        }
        if s.round == 1 {
            assert!(s.units[P1 as usize].indomitable_active);
        }
    }
    assert!(passed_over || !s.units[P2 as usize].indomitable_active);
    assert!(s.units[P1 as usize].indomitable_active, "round 2 began; P1's fresh turn is still ahead");
}

#[test]
fn movement_footwork_rotation_retreat_and_edge_arrival_do_not_cancel_it() {
    // Move.
    let mut s = board(P1, &[(P1, hex(0, -2))]);
    s.units[P1 as usize].has_indomitable = true;
    s.apply(indo(P1)).unwrap();
    s.apply(Command::Move { unit: P1, to: hex(-1, -2) }).unwrap();
    assert!(s.units[P1 as usize].is_indomitable_active());

    // Footwork.
    let mut s = board(P1, &[(P1, hex(0, -2))]);
    s.units[P1 as usize].has_indomitable = true;
    s.units[P1 as usize].has_footwork = true;
    s.apply(indo(P1)).unwrap();
    s.apply(Command::Footwork { unit: P1, to: hex(-1, -2) }).unwrap();
    assert!(s.units[P1 as usize].is_indomitable_active());

    // Rotation.
    let mut s = board(P1, &[(P1, hex(0, -2)), (P2, hex(-1, -2))]);
    s.units[P1 as usize].has_indomitable = true;
    s.units[P1 as usize].has_rotation = true;
    s.apply(indo(P1)).unwrap();
    s.apply(Command::Rotation { unit: P1, target: P2 }).unwrap();
    assert_eq!(s.units[P1 as usize].pos, hex(-1, -2));
    assert!(s.units[P1 as usize].is_indomitable_active());

    // Retreat intent.
    let mut s = board(P1, &[(P1, hex(0, -2))]);
    s.units[P1 as usize].has_indomitable = true;
    s.apply(indo(P1)).unwrap();
    s.apply(Command::Retreat { unit: P1 }).unwrap();
    assert!(s.units[P1 as usize].retreating && s.units[P1 as usize].is_indomitable_active());

    // Arriving on a map edge drops morale to Breaking, which is not Fleeing.
    let mut s = board(P1, &[(P1, hex(-3, 2))]);
    s.units[P1 as usize].has_indomitable = true;
    s.apply(indo(P1)).unwrap();
    s.apply(Command::Move { unit: P1, to: hex(-4, 2) }).unwrap();
    assert_eq!(s.units[P1 as usize].morale, Morale::Breaking);
    assert!(s.units[P1 as usize].is_indomitable_active());
}

// ------------------------------------------------------------- hashing

#[test]
fn hash_bits_are_independent_and_prior_hashes_are_unchanged() {
    assert_eq!(BattleState::new(1).state_hash(), FLAT_SEED_1_HASH);
    assert_eq!(scenario::battle(1, TerrainPreset::Flat).state_hash(), FLAT_SCENARIO_HASH);
    assert_eq!(scenario::battle(1, TerrainPreset::Hills).state_hash(), HILLS_SCENARIO_HASH);

    // Every combination of the new bits with their neighbours (and the widest stack
    // field below them) yields a distinct hash, so bits 52 and 53 overlap nothing.
    let mut seen = Vec::new();
    for bits in 0..128u32 {
        let mut s = BattleState::new(1);
        let u = &mut s.units[1];
        u.has_berserk = bits & 1 != 0;
        u.berserk_used = bits & 2 != 0;
        u.has_pathfinder = bits & 4 != 0;
        u.has_indomitable = bits & 8 != 0;
        u.indomitable_active = bits & 16 != 0;
        u.has_nine_lives = bits & 32 != 0;
        u.fast_adaptation_stacks = if bits & 64 != 0 { u32::MAX } else { 0 };
        let h = s.state_hash();
        assert!(!seen.contains(&h), "hash collision at flag combination {bits:#09b}");
        seen.push(h);
    }
    let base = BattleState::new(1);
    let mut cap = base.clone();
    cap.units[0].has_indomitable = true;
    let mut active = base.clone();
    active.units[0].indomitable_active = true;
    assert_ne!(cap.state_hash(), base.state_hash());
    assert_ne!(active.state_hash(), base.state_hash());
    assert_ne!(cap.state_hash(), active.state_hash());
}

#[test]
fn rejected_and_accepted_activation_consume_no_rng() {
    let mut s = board(P1, &[]);
    let rng = s.rng.clone();
    assert!(s.apply(indo(P1)).is_err());
    s.units[P1 as usize].has_indomitable = true;
    s.apply(indo(P1)).unwrap();
    assert_eq!(s.rng, rng);
}
