//! Headless Nine Lives (partial UC-COMBAT-059). The catalog states that a would-be
//! fatal blow leaves the brother at a sliver of HP, once per fight, and that a second
//! lethal hit kills. The exact 1 HP (`NINE_LIVES_SURVIVAL_HP`), melee-only
//! interception, the capability flag, the reported actual HP loss and the morale check
//! on actual loss are an APPROXIMATION. Fixtures park every unit far away, then place
//! actors on the obstacle-free lane `r = -2`. Every unit has skill 0 / defense 0 and a
//! fixed damage; `rig` picks RNG states whose draws take requested rolls (0 always
//! hits, 99 always misses), so tests never depend on a particular seed.

use bb_rules::hex::{hex, map_hexes};
use bb_rules::rules::{
    ATTACK_AP, ATTACK_FATIGUE, DEFENDER_HIT_FATIGUE_GAIN, DEFENDER_MISS_FATIGUE_GAIN, MORALE_CHECK_HP_LOSS_THRESHOLD, MOVE_AP,
    MOVE_FATIGUE, NINE_LIVES_SURVIVAL_HP,
};
use bb_rules::scenario::{self, TerrainPreset};
use bb_rules::*;

const P1: u32 = 0;
const P2: u32 = 1;
const P3: u32 = 2;
const E1: u32 = 3;
const E2: u32 = 4;
const E3: u32 = 5;

const PARK_PLAYERS: [Hex; 3] = [hex(-4, 2), hex(-4, 3), hex(-4, 4)];
const PARK_ENEMIES: [Hex; 3] = [hex(4, -4), hex(4, -3), hex(4, -2)];

/// Hash of `BattleState::new(1)` before Nine Lives existed.
const FLAT_SEED_1_HASH: u64 = 10_088_116_083_934_094_952;
/// Initial `scenario::battle(1, _)` hashes before Nine Lives existed.
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

/// `attacker` beside `defender` on the lane; the attacker deals exactly `dmg`, the
/// defender is unarmoured, capable of Nine Lives and at `hp`. Resolve 0 makes any
/// morale check fail, so a check always shows as a state change.
fn duel(attacker: u32, defender: u32, hp: u32, dmg: u32) -> BattleState {
    let (p, e) = if attacker < 3 { (attacker, defender) } else { (defender, attacker) };
    let mut s = board(attacker, &[(p, hex(0, -2)), (e, hex(1, -2))]);
    let a = &mut s.units[attacker as usize];
    a.stats.dmg_min = dmg;
    a.stats.dmg_max = dmg;
    let d = &mut s.units[defender as usize];
    d.hp = hp;
    d.armour = 0;
    d.has_nine_lives = true;
    d.stats.resolve = 0;
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

fn kill(s: &mut BattleState, ids: &[u32]) {
    for &id in ids {
        s.units[id as usize].alive = false;
        s.queue.retain(|&q| q != id);
    }
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

fn attacks(ev: &[Event]) -> Vec<(u32, u32, bool)> {
    ev.iter()
        .filter_map(|e| match e {
            Event::Attacked { attacker, target, hit, .. } => Some((*attacker, *target, *hit)),
            _ => None,
        })
        .collect()
}

fn no_death_events(ev: &[Event]) -> bool {
    !ev.iter().any(|e| {
        matches!(e, Event::Died { .. } | Event::AllyDeathMoraleChecked { .. } | Event::KillMoraleChecked { .. } | Event::BattleEnded { .. })
    })
}

/// One rigged ordinary attack through `Command::Attack`.
fn swing(s: &mut BattleState, attacker: u32, target: u32, roll: u32) -> Vec<Event> {
    activate(s, attacker);
    rig(s, &[Some(roll)]);
    s.apply(Command::Attack { attacker, target }).unwrap()
}

// ------------------------------------------------------------- defaults and constants

#[test]
fn units_and_scenarios_start_without_capability_or_used_flag() {
    assert_eq!(NINE_LIVES_SURVIVAL_HP, 1);
    let fresh = Unit::new(9, "X", Side::Enemy, scenario::units()[3].stats.clone(), hex(0, -2));
    assert!(!fresh.has_nine_lives && !fresh.nine_lives_used);
    for s in [BattleState::new(1), scenario::battle(1, TerrainPreset::Flat), scenario::battle(1, TerrainPreset::Hills)] {
        assert!(s.units.iter().all(|u| !u.has_nine_lives && !u.nine_lives_used));
    }
}

// ------------------------------------------------------------- survival

#[test]
fn exact_lethal_and_overkill_hits_leave_one_hp_for_both_factions() {
    for (attacker, defender) in [(E1, P1), (P1, E1)] {
        for dmg in [10, 11, 500] {
            let mut s = duel(attacker, defender, 10, dmg);
            let queue = s.queue.clone();
            rig(&mut s, &[Some(HIT)]);
            let before = s.rng.clone();
            let chance = s.hit_chance(attacker, defender);
            let ev = s.apply(Command::Attack { attacker, target: defender }).unwrap();
            assert_eq!(
                ev[..2],
                [
                    Event::Attacked { attacker, target: defender, hit: true, roll: HIT, chance, dmg, absorbed: 0, hp_loss: 9 },
                    Event::NineLivesTriggered { unit: defender },
                ],
                "{attacker}->{defender} dmg {dmg}"
            );
            assert_eq!(ev.len(), 2);
            let d = &s.units[defender as usize];
            assert_eq!((d.hp, d.alive, d.nine_lives_used), (NINE_LIVES_SURVIVAL_HP, true, true));
            assert_eq!(s.queue, queue);
            assert_eq!(s.rng, rng_after(&before, 2), "no draw beyond roll and damage");
            assert_eq!(s.outcome(), None);
        }
    }
}

#[test]
fn a_unit_at_one_hp_consumes_protection_and_stays_at_one_hp() {
    let mut s = duel(E1, P1, 1, 7);
    let ev = swing(&mut s, E1, P1, HIT);
    assert!(matches!(ev[0], Event::Attacked { hit: true, dmg: 7, hp_loss: 0, .. }), "{ev:?}");
    assert_eq!(ev[1], Event::NineLivesTriggered { unit: P1 });
    assert!(no_death_events(&ev));
    assert_eq!((s.units[0].hp, s.units[0].alive, s.units[0].nine_lives_used), (1, true, true));
    // The protection is gone: the next lethal hit kills.
    let ev = swing(&mut s, E1, P1, HIT);
    assert!(ev.contains(&Event::Died { unit: P1 }));
    assert!(!ev.iter().any(|e| matches!(e, Event::NineLivesTriggered { .. })));
}

#[test]
fn disabled_or_used_protection_lets_a_lethal_hit_kill_with_unchanged_reporting() {
    for used in [false, true] {
        // Disabled: incapable and unused. Used: capable and already consumed.
        let mut s = duel(E1, P1, 10, 25);
        s.units[0].has_nine_lives = used;
        s.units[0].nine_lives_used = used;
        let chance = s.hit_chance(E1, P1);
        let ev = swing(&mut s, E1, P1, HIT);
        // Without interception hp_loss is the unclamped would-be loss.
        assert_eq!(ev[0], Event::Attacked { attacker: E1, target: P1, hit: true, roll: HIT, chance, dmg: 25, absorbed: 0, hp_loss: 25 });
        assert_eq!(ev[1], Event::Died { unit: P1 });
        assert!(!ev.iter().any(|e| matches!(e, Event::NineLivesTriggered { .. })));
        assert!(!s.units[0].alive && !s.queue.contains(&P1));
        assert_eq!(s.units[0].nine_lives_used, used, "a failed interception never changes the flag");
    }
}

#[test]
fn nonlethal_hits_misses_and_armour_only_hits_keep_the_protection() {
    // Nonlethal: exactly leaves 1 HP without help from the perk.
    let mut s = duel(E1, P1, 10, 9);
    let ev = swing(&mut s, E1, P1, HIT);
    assert!(matches!(ev[0], Event::Attacked { hp_loss: 9, .. }));
    assert_eq!(ev.len(), 1);
    assert_eq!((s.units[0].hp, s.units[0].nine_lives_used), (1, false));
    // Miss.
    let mut s = duel(E1, P1, 10, 50);
    let ev = swing(&mut s, E1, P1, MISS);
    assert!(matches!(ev[0], Event::Attacked { hit: false, hp_loss: 0, .. }));
    assert_eq!(ev.len(), 1);
    assert_eq!((s.units[0].hp, s.units[0].nine_lives_used), (10, false));
    // Armour-only: 5 damage fully absorbed (soak adds nothing below 10 damage).
    let mut s = duel(E1, P1, 1, 5);
    s.units[0].armour = 20;
    let ev = swing(&mut s, E1, P1, HIT);
    assert!(matches!(ev[0], Event::Attacked { hit: true, absorbed: 5, hp_loss: 0, .. }));
    assert_eq!(ev.len(), 1);
    assert_eq!((s.units[0].hp, s.units[0].armour, s.units[0].nine_lives_used), (1, 15, false));
}

#[test]
fn armour_absorption_is_kept_and_reported_while_the_loss_is_the_actual_one() {
    let mut s = duel(E1, P1, 5, 30);
    s.units[0].armour = 10;
    let chance = s.hit_chance(E1, P1);
    let ev = swing(&mut s, E1, P1, HIT);
    // Soak: absorbed 10, would-be loss 20 + 3 = 23; actual loss 5 - 1 = 4.
    assert_eq!(ev[0], Event::Attacked { attacker: E1, target: P1, hit: true, roll: HIT, chance, dmg: 30, absorbed: 10, hp_loss: 4 });
    assert_eq!(ev[1], Event::NineLivesTriggered { unit: P1 });
    assert_eq!((s.units[0].hp, s.units[0].armour), (1, 0));
}

#[test]
fn survival_then_a_later_lethal_hit_follows_normal_death_handling() {
    let mut s = duel(E1, P1, 10, 30);
    activate(&mut s, E1);
    rig(&mut s, &[Some(HIT)]);
    let ev = s.apply(Command::Attack { attacker: E1, target: P1 }).unwrap();
    assert_eq!(ev.len(), 2);
    rig(&mut s, &[Some(HIT)]);
    let ev = s.apply(Command::Attack { attacker: E1, target: P1 }).unwrap();
    assert!(matches!(ev[0], Event::Attacked { hit: true, hp_loss: 30, .. }));
    assert_eq!(ev[1], Event::Died { unit: P1 });
    assert!(matches!(ev[2], Event::AllyDeathMoraleChecked { unit: 1, fallen: 0, .. }));
    assert!(matches!(ev[3], Event::AllyDeathMoraleChecked { unit: 2, fallen: 0, .. }));
    assert!(matches!(ev[4], Event::KillMoraleChecked { unit: 3, fallen: 0, .. }));
    assert!(!s.units[0].alive && !s.queue.contains(&P1));
}

#[test]
fn survival_has_no_death_side_effects_and_does_not_refresh_other_resources() {
    let mut s = duel(E1, P1, 10, 30);
    s.units[0].fatigue = 7;
    s.units[0].ap = 3;
    s.units[0].waited = true;
    let queue = s.queue.clone();
    let (ap, fatigue, morale) = (s.units[0].ap, s.units[0].fatigue, s.units[0].morale);
    activate(&mut s, E1);
    rig(&mut s, &[Some(HIT)]);
    let start = s.rng.clone();
    let ev = s.apply(Command::Attack { attacker: E1, target: P1 }).unwrap();
    assert!(no_death_events(&ev), "{ev:?}");
    assert!(!ev.iter().any(|e| matches!(e, Event::MoraleChecked { .. })), "9 lost is below the threshold");
    assert_eq!(s.rng, rng_after(&start, 2));
    let u = &s.units[0];
    assert_eq!((u.ap, u.armour, u.morale), (ap, 0, morale));
    assert_eq!(u.fatigue, fatigue + DEFENDER_HIT_FATIGUE_GAIN, "a survived hit is a successful hit for incoming fatigue");
    assert_eq!(s.queue[0], E1, "the attacker keeps the turn");
    let mut expected = queue;
    expected.retain(|&q| q != E1);
    expected.insert(0, E1);
    assert_eq!(s.queue, expected);
}

#[test]
fn survival_resets_fast_adaptation_like_any_other_hit() {
    let mut s = duel(E1, P1, 10, 30);
    s.units[E1 as usize].has_fast_adaptation = true;
    s.units[E1 as usize].fast_adaptation_stacks = 3;
    let ev = swing(&mut s, E1, P1, HIT);
    assert_eq!(ev[1], Event::NineLivesTriggered { unit: P1 });
    assert_eq!(s.units[E1 as usize].fast_adaptation_stacks, 0);
    // The defender's own counter is not an attacker's: left untouched.
    let mut s = duel(E1, P1, 10, 30);
    s.units[0].has_fast_adaptation = true;
    s.units[0].fast_adaptation_stacks = 2;
    swing(&mut s, E1, P1, HIT);
    assert_eq!(s.units[0].fast_adaptation_stacks, 2);
}

#[test]
fn fleeing_and_retreating_defenders_are_protected_too() {
    for (attacker, defender) in [(E1, P1), (P1, E1)] {
        for flavour in 0..2 {
            let mut s = duel(attacker, defender, 10, 30);
            let d = &mut s.units[defender as usize];
            if flavour == 0 {
                d.morale = Morale::Fleeing;
            } else {
                d.retreating = true;
            }
            let ev = swing(&mut s, attacker, defender, HIT);
            assert_eq!(ev[1], Event::NineLivesTriggered { unit: defender }, "{attacker}->{defender} flavour {flavour}");
            assert!(no_death_events(&ev));
            let d = &s.units[defender as usize];
            assert_eq!((d.hp, d.alive, d.nine_lives_used), (1, true, true));
        }
    }
}

// ------------------------------------------------------------- morale on actual loss

#[test]
fn actual_hp_loss_morale_boundary_is_fourteen_versus_fifteen() {
    assert_eq!(MORALE_CHECK_HP_LOSS_THRESHOLD, 15);
    // 14 actually lost although 100 damage was rolled: no check, two draws.
    let mut s = duel(E1, P1, 15, 100);
    let ev = swing(&mut s, E1, P1, HIT);
    assert!(matches!(ev[0], Event::Attacked { dmg: 100, hp_loss: 14, .. }));
    assert_eq!(ev[1], Event::NineLivesTriggered { unit: P1 });
    assert_eq!(ev.len(), 2, "{ev:?}");
    assert_eq!(s.units[0].morale, Morale::Steady);

    // 15 actually lost: the existing check follows NineLivesTriggered, one more draw.
    let mut s = duel(E1, P1, 16, 100);
    activate(&mut s, E1);
    rig(&mut s, &[Some(HIT)]);
    let start = s.rng.clone();
    let mut r = start.clone();
    let draws: Vec<u64> = (0..3).map(|_| r.next_u64()).collect();
    let ev = s.apply(Command::Attack { attacker: E1, target: P1 }).unwrap();
    assert!(matches!(ev[0], Event::Attacked { hp_loss: 15, .. }));
    assert_eq!(ev[1], Event::NineLivesTriggered { unit: P1 });
    assert_eq!(
        ev[2],
        Event::MoraleChecked { unit: P1, roll: (draws[2] % 100) as u32, chance: 0, before: Morale::Steady, after: Morale::Wavering }
    );
    assert_eq!(ev.len(), 3, "{ev:?}");
    assert_eq!(s.rng, rng_after(&start, 3));
    assert_eq!((s.units[0].hp, s.units[0].morale), (1, Morale::Wavering));
}

// ------------------------------------------------------------- outcome

#[test]
fn last_unit_survival_delays_the_outcome_until_actual_death() {
    for (attacker, defender, others, winner) in [(P1, E1, [E2, E3], Outcome::Victory), (E1, P1, [P2, P3], Outcome::Defeat)] {
        let mut s = duel(attacker, defender, 5, 30);
        kill(&mut s, &others);
        let ev = swing(&mut s, attacker, defender, HIT);
        assert_eq!(ev[1], Event::NineLivesTriggered { unit: defender });
        assert_eq!(ev.len(), 2, "{ev:?}");
        assert_eq!((s.outcome(), s.end_reason()), (None, None));
        let ev = swing(&mut s, attacker, defender, HIT);
        assert!(ev.contains(&Event::Died { unit: defender }));
        assert_eq!(ev.last(), Some(&Event::BattleEnded { outcome: winner, reason: EndReason::Elimination }));
        assert_eq!(s.outcome(), Some(winner));
    }
}

#[test]
fn protection_does_not_replenish_across_wait_turns_or_rounds() {
    let mut s = duel(E1, P1, 10, 30);
    let ev = swing(&mut s, E1, P1, HIT);
    assert_eq!(ev[1], Event::NineLivesTriggered { unit: P1 });
    let round = s.round;
    let mut waited = false;
    while s.round == round {
        let id = s.active_unit().unwrap().id;
        if id == P1 && !waited {
            waited = true;
            s.apply(Command::Wait { unit: P1 }).unwrap();
        } else {
            s.apply(Command::EndTurn { unit: id }).unwrap();
        }
        assert!(s.units[0].nine_lives_used && s.units[0].has_nine_lives);
    }
    assert!(waited && s.round == round + 1);
    assert_eq!(s.units[0].hp, 1);
    let ev = swing(&mut s, E1, P1, HIT);
    assert_eq!(ev[1], Event::Died { unit: P1 });
}

#[test]
fn used_flag_survives_retreat_and_morale_changes() {
    let mut s = duel(P1, E1, 10, 1);
    s.units[0].has_nine_lives = true;
    s.units[0].nine_lives_used = true;
    s.units[0].armour = 0;
    s.units[0].hp = 40;
    s.units[0].stats.resolve = 0;
    s.units[0].morale = Morale::Breaking;
    s.units[E1 as usize].stats.dmg_min = 20;
    s.units[E1 as usize].stats.dmg_max = 20;
    swing(&mut s, E1, P1, HIT); // 20 lost: failed heavy-hit check, Breaking -> Fleeing
    assert_eq!(s.units[0].morale, Morale::Fleeing);
    assert!(s.units[0].nine_lives_used);
    let mut s = duel(P1, E1, 10, 1);
    s.units[0].nine_lives_used = true;
    s.units[0].has_nine_lives = true;
    s.apply(Command::Retreat { unit: P1 }).unwrap();
    assert!(s.units[0].retreating && s.units[0].nine_lives_used && s.units[0].has_nine_lives);
}

// ------------------------------------------------------------- opportunity attacks

#[test]
fn opportunity_attack_survival_still_interrupts_and_a_later_miss_continues_the_reactions() {
    for (mover, ctrl1, ctrl2, c1_at, c2_at) in [(P1, E1, E2, hex(0, -3), hex(1, -3)), (E1, P1, P2, hex(0, -3), hex(1, -3))] {
        let mut s = board(mover, &[(mover, hex(0, -2)), (ctrl1, c1_at), (ctrl2, c2_at)]);
        for c in [ctrl1, ctrl2] {
            s.units[c as usize].stats.dmg_min = 30;
            s.units[c as usize].stats.dmg_max = 30;
        }
        let m = &mut s.units[mover as usize];
        m.hp = 10;
        m.armour = 0;
        m.has_nine_lives = true;
        assert_fixture_valid(&s);
        let queue = s.queue.clone();
        rig(&mut s, &[Some(HIT), None, Some(MISS)]);
        let start = s.rng.clone();
        let ev = s.apply(Command::Move { unit: mover, to: hex(-2, -2) }).unwrap();
        assert_eq!(attacks(&ev), vec![(ctrl1, mover, true), (ctrl2, mover, false)]);
        assert!(matches!(ev[0], Event::Attacked { hp_loss: 9, .. }));
        assert_eq!(ev[1], Event::NineLivesTriggered { unit: mover });
        assert!(matches!(ev[2], Event::Attacked { hit: false, .. }));
        assert_eq!(ev.len(), 3, "no Moved: the interrupted move never leaves the departure hex: {ev:?}");
        // Roll+damage of the hit, roll of the miss; surround makes no difference to draw count.
        assert_eq!(s.rng, rng_after(&start, 3));
        let m = &s.units[mover as usize];
        assert_eq!((m.pos, m.hp, m.alive, m.nine_lives_used), (hex(0, -2), 1, true, true));
        assert_eq!(m.ap, 9 - MOVE_AP);
        assert_eq!(m.fatigue, MOVE_FATIGUE + DEFENDER_HIT_FATIGUE_GAIN + DEFENDER_MISS_FATIGUE_GAIN);
        assert_eq!(s.queue, queue, "the mover keeps the turn");
        assert_eq!(s.outcome(), None);
    }
}

#[test]
fn two_opportunity_attackers_first_is_survived_second_kills_the_mover() {
    for (mover, ctrl1, ctrl2, allies) in [(P1, E1, E2, [P2, P3]), (E1, P1, P2, [E2, E3])] {
        let mut s = board(mover, &[(mover, hex(0, -2)), (ctrl1, hex(0, -3)), (ctrl2, hex(1, -3))]);
        for c in [ctrl1, ctrl2] {
            s.units[c as usize].stats.dmg_min = 30;
            s.units[c as usize].stats.dmg_max = 30;
        }
        let m = &mut s.units[mover as usize];
        m.hp = 10;
        m.armour = 0;
        m.has_nine_lives = true;
        assert_fixture_valid(&s);
        rig(&mut s, &[Some(HIT), None, Some(HIT)]);
        let ev = s.apply(Command::Move { unit: mover, to: hex(-2, -2) }).unwrap();
        assert_eq!(attacks(&ev), vec![(ctrl1, mover, true), (ctrl2, mover, true)]);
        assert!(matches!(ev[0], Event::Attacked { hp_loss: 9, .. }));
        assert_eq!(ev[1], Event::NineLivesTriggered { unit: mover });
        assert!(matches!(ev[2], Event::Attacked { hp_loss: 30, .. }));
        assert_eq!(ev[3], Event::Died { unit: mover });
        // Normal death handling: both allies check, then the killer's kill check.
        assert!(matches!(ev[4], Event::AllyDeathMoraleChecked { unit, fallen, .. } if unit == allies[0] && fallen == mover));
        assert!(matches!(ev[5], Event::AllyDeathMoraleChecked { unit, fallen, .. } if unit == allies[1] && fallen == mover));
        assert!(matches!(ev[6], Event::KillMoraleChecked { unit, fallen, .. } if unit == ctrl2 && fallen == mover));
        assert!(!ev.iter().any(|e| matches!(e, Event::Moved { .. })));
        let m = &s.units[mover as usize];
        assert!(!m.alive && !s.queue.contains(&mover));
        assert_eq!(m.pos, hex(0, -2));
    }
}

// ------------------------------------------------------------- purity

#[test]
fn queries_and_rejected_commands_preserve_all_state() {
    let mut s = duel(E1, P1, 10, 30);
    s.units[0].nine_lives_used = true;
    let snap = snapshot(&s);
    let _ = s.hit_chance(E1, P1);
    let _ = s.enemy_controllers_at(P1, hex(0, -2));
    let _ = s.preview_move(E1, hex(2, -2));
    assert_eq!(snapshot(&s), snap);
    // Rejected: not adjacent, wrong unit, out of AP.
    assert_eq!(s.apply(Command::Attack { attacker: E1, target: E2 }), Err(RuleError::InvalidTarget));
    assert_eq!(s.apply(Command::Attack { attacker: P1, target: E1 }), Err(RuleError::NotActiveUnit));
    s.units[E1 as usize].ap = ATTACK_AP - 1;
    let snap = snapshot(&s);
    assert_eq!(s.apply(Command::Attack { attacker: E1, target: P1 }), Err(RuleError::NotEnoughAp));
    assert_eq!(snapshot(&s), snap);
    s.units[E1 as usize].ap = 9;
    s.units[E1 as usize].fatigue = s.units[E1 as usize].stats.fatigue_max - ATTACK_FATIGUE + 1;
    let snap = snapshot(&s);
    assert_eq!(s.apply(Command::Attack { attacker: E1, target: P1 }), Err(RuleError::TooFatigued));
    assert_eq!(snapshot(&s), snap);
}

// ------------------------------------------------------------- hashing

#[test]
fn hash_reacts_to_each_flag_on_its_own_bit_and_old_pins_hold() {
    assert_eq!(BattleState::new(1).state_hash(), FLAT_SEED_1_HASH);
    assert_eq!(scenario::battle(1, TerrainPreset::Flat).state_hash(), FLAT_SCENARIO_HASH);
    assert_eq!(scenario::battle(1, TerrainPreset::Hills).state_hash(), HILLS_SCENARIO_HASH);
    let hash = |cap: bool, used: bool, dodge: bool| {
        let mut s = BattleState::new(1);
        s.units[0].has_nine_lives = cap;
        s.units[0].nine_lives_used = used;
        s.units[0].has_dodge = dodge;
        s.state_hash()
    };
    let all = [hash(false, false, false), hash(true, false, false), hash(false, true, false), hash(true, true, false), hash(false, false, true)];
    for (i, a) in all.iter().enumerate() {
        for b in &all[i + 1..] {
            assert_ne!(a, b);
        }
    }
    assert_eq!(all[0], FLAT_SEED_1_HASH);
    assert_ne!(hash(true, true, true), hash(true, true, false));
    // Another unit carrying the flag hashes differently from the first.
    let mut a = BattleState::new(1);
    let mut b = BattleState::new(1);
    a.units[0].nine_lives_used = true;
    b.units[1].nine_lives_used = true;
    assert_ne!(a.state_hash(), b.state_hash());
}
