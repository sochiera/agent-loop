//! Headless Berserk (partial UC-COMBAT-038). The catalog states: the killer regains
//! 4 AP, once per turn, fatigue is not refreshed. The ordinary-melee-only trigger, the
//! `ap_max` cap, both-side capability, the event slot (after the death's morale events,
//! before `BattleEnded`) and treating Wait-separated portions as one scheduled turn are
//! an APPROXIMATION. Fixtures park every unit far away, then place actors on the
//! obstacle-free neighbourhood of `(0, -2)`. Every unit has skill 0 / defense 0 / Resolve
//! 100 and damage 1; `rig` picks RNG states whose draws take requested rolls (0 always
//! hits, 99 always misses), so tests never depend on a particular seed.

use bb_rules::ai;
use bb_rules::hex::{hex, map_hexes};
use bb_rules::rules::{ADRENALINE_AP, ATTACK_AP, ATTACK_FATIGUE, BERSERK_AP_REFUND, MOVE_AP, MOVE_FATIGUE};
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

/// Hash of `BattleState::new(1)` and of the initial scenarios before any capability bit
/// above 46 existed; they must not move.
const FLAT_SEED_1_HASH: u64 = 10_088_116_083_934_094_952;
const FLAT_SCENARIO_HASH: u64 = 0x8c00302113cbe668;
const HILLS_SCENARIO_HASH: u64 = 0xb759683cdff4bfa2;

const MISS: u32 = 99;
const HIT: u32 = 0;

const ACTOR_AT: Hex = hex(0, -2);
/// Adjacent to `ACTOR_AT`, in the order victims are placed.
const VICTIM_AT: [Hex; 3] = [hex(1, -2), hex(0, -1), hex(-1, -1)];

fn opponents(id: u32) -> [u32; 3] {
    if id < 3 { [E1, E2, E3] } else { [P1, P2, P3] }
}

/// All units parked, skill 0 / defense 0 / Resolve 100 / damage 1, `active` first with
/// full AP and its turn already started.
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
        u.stats.resolve = 100;
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

/// `actor` (Berserk-capable, active, 9 AP) at `ACTOR_AT` beside the first `n` hostile
/// units, each unarmoured with 1 HP. The other hostile units stay parked and alive.
fn arena(actor: u32, n: usize) -> BattleState {
    let placements: Vec<(u32, Hex)> =
        std::iter::once((actor, ACTOR_AT)).chain((0..n).map(|i| (opponents(actor)[i], VICTIM_AT[i]))).collect();
    let mut s = board(actor, &placements);
    s.units[actor as usize].has_berserk = true;
    for i in 0..n {
        let v = &mut s.units[opponents(actor)[i] as usize];
        v.hp = 1;
        v.armour = 0;
    }
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

fn kill(s: &mut BattleState, ids: &[u32]) {
    for &id in ids {
        s.units[id as usize].alive = false;
        s.queue.retain(|&q| q != id);
    }
}

/// One rigged ordinary attack; the attacker must already be active.
fn strike(s: &mut BattleState, attacker: u32, target: u32, roll: u32) -> Vec<Event> {
    rig(s, &[Some(roll)]);
    s.apply(Command::Attack { attacker, target }).unwrap()
}

/// Everything observable that a pure query or rejected command must not change.
fn snapshot(s: &BattleState) -> (u64, Rng, Vec<Unit>, Vec<u32>, u32, Option<Outcome>) {
    (s.state_hash(), s.rng.clone(), s.units.clone(), s.queue.clone(), s.round, s.outcome())
}

fn berserk_events(ev: &[Event]) -> Vec<(u32, u32)> {
    ev.iter()
        .filter_map(|e| match e {
            Event::BerserkTriggered { unit, ap_gained } => Some((*unit, *ap_gained)),
            _ => None,
        })
        .collect()
}

fn turn_starts(ev: &[Event], unit: u32) -> usize {
    ev.iter().filter(|e| matches!(e, Event::TurnStarted { unit: u } if *u == unit)).count()
}

/// `EndTurn` for whichever unit is active until `done` holds or the battle ends.
fn pass_until(s: &mut BattleState, done: impl Fn(&BattleState) -> bool) -> Vec<Event> {
    let mut all = Vec::new();
    for _ in 0..200 {
        if s.outcome().is_some() || done(s) {
            return all;
        }
        let id = s.active_unit().unwrap().id;
        all.extend(s.apply(Command::EndTurn { unit: id }).unwrap());
    }
    panic!("condition never reached");
}

fn is_active(id: u32) -> impl Fn(&BattleState) -> bool {
    move |s| s.active_unit().is_some_and(|u| u.id == id)
}

// ------------------------------------------------------------- defaults and constants

#[test]
fn units_and_scenarios_start_without_capability_or_used_flag() {
    assert_eq!(BERSERK_AP_REFUND, 4);
    let fresh = Unit::new(9, "X", Side::Enemy, scenario::units()[3].stats.clone(), hex(0, -2));
    assert!(!fresh.has_berserk && !fresh.berserk_used);
    for s in [BattleState::new(1), scenario::battle(1, TerrainPreset::Flat), scenario::battle(1, TerrainPreset::Hills)] {
        assert!(s.units.iter().all(|u| !u.has_berserk && !u.berserk_used));
    }
}

// ------------------------------------------------------------- first kill refund

#[test]
fn first_kill_refunds_ap_for_either_side_after_the_death_events_and_keeps_the_turn() {
    for actor in [P1, E1] {
        let mut s = arena(actor, 2);
        let [v1, v2, v3] = opponents(actor);
        let mut queue = s.queue.clone();
        queue.retain(|&q| q != v1);
        let ev = strike(&mut s, actor, v1, HIT);
        // Death, the two surviving allies' checks (ascending id), the killer's check, then Berserk.
        assert!(matches!(ev[0], Event::Attacked { attacker, target, hit: true, .. } if attacker == actor && target == v1), "{ev:?}");
        assert_eq!(ev[1], Event::Died { unit: v1 });
        assert!(matches!(ev[2], Event::AllyDeathMoraleChecked { unit, fallen, .. } if unit == v2 && fallen == v1));
        assert!(matches!(ev[3], Event::AllyDeathMoraleChecked { unit, fallen, .. } if unit == v3 && fallen == v1));
        assert!(matches!(ev[4], Event::KillMoraleChecked { unit, fallen, .. } if unit == actor && fallen == v1));
        assert_eq!(ev[5], Event::BerserkTriggered { unit: actor, ap_gained: BERSERK_AP_REFUND });
        assert_eq!(ev.len(), 6, "{ev:?}");

        let a = &s.units[actor as usize];
        assert_eq!(a.ap, 9 - ATTACK_AP + BERSERK_AP_REFUND, "{}", a.name);
        assert!(a.berserk_used && a.has_berserk);
        assert_eq!(a.fatigue, ATTACK_FATIGUE, "fatigue is paid, never refunded");
        assert_eq!(s.queue, queue, "the actor keeps the turn; only the victim left the queue");
        assert_eq!(s.round, 1);
        assert!(a.turn_started);
    }
}

#[test]
fn berserk_changes_only_ap_the_flag_and_one_event_against_a_twin_without_the_capability() {
    for actor in [P1, E1] {
        let mut with = arena(actor, 2);
        let mut without = with.clone();
        without.units[actor as usize].has_berserk = false;
        let [v1, ..] = opponents(actor);
        rig(&mut with, &[Some(HIT)]);
        // The twin starts from the very same RNG state and takes the same rigged roll.
        without.rng = with.rng.clone();
        let ev_with = with.apply(Command::Attack { attacker: actor, target: v1 }).unwrap();
        let ev_without = without.apply(Command::Attack { attacker: actor, target: v1 }).unwrap();

        // Same events in the same relative order, plus exactly one trailing Berserk event.
        let (last, rest) = ev_with.split_last().unwrap();
        assert_eq!(rest, ev_without.as_slice());
        assert_eq!(*last, Event::BerserkTriggered { unit: actor, ap_gained: 4 });
        assert_eq!(with.rng, without.rng, "Berserk adds no RNG draws");

        let (a, b) = (&with.units[actor as usize], &without.units[actor as usize]);
        assert_eq!(a.ap, b.ap + BERSERK_AP_REFUND);
        assert!(a.berserk_used && !b.berserk_used);
        for (x, y) in with.units.iter().zip(&without.units) {
            assert_eq!((x.hp, x.armour, x.fatigue, x.morale, x.pos, x.alive), (y.hp, y.armour, y.fatigue, y.morale, y.pos, y.alive));
        }
        assert_eq!(with.queue, without.queue);
    }
}

#[test]
fn a_second_ordinary_kill_in_the_same_turn_gives_no_second_refund() {
    for actor in [P1, E1] {
        let mut s = arena(actor, 2);
        let [v1, v2, _] = opponents(actor);
        let first = strike(&mut s, actor, v1, HIT);
        assert_eq!(berserk_events(&first), vec![(actor, 4)]);
        let ap_between = s.units[actor as usize].ap;
        assert_eq!(ap_between, 9);
        let second = strike(&mut s, actor, v2, HIT);
        assert!(second.iter().any(|e| matches!(e, Event::Died { unit } if *unit == v2)));
        assert!(berserk_events(&second).is_empty(), "{second:?}");
        let a = &s.units[actor as usize];
        assert_eq!(a.ap, ap_between - ATTACK_AP);
        assert!(a.berserk_used);
        assert_eq!(a.fatigue, 2 * ATTACK_FATIGUE);
    }
}

#[test]
fn a_disabled_or_already_used_capability_never_refunds() {
    for actor in [P1, E1] {
        let [v1, ..] = opponents(actor);
        let mut off = arena(actor, 1);
        off.units[actor as usize].has_berserk = false;
        let ev = strike(&mut off, actor, v1, HIT);
        assert!(ev.iter().any(|e| matches!(e, Event::Died { .. })));
        assert!(berserk_events(&ev).is_empty());
        assert_eq!(off.units[actor as usize].ap, 9 - ATTACK_AP);
        assert!(!off.units[actor as usize].berserk_used);

        let mut used = arena(actor, 1);
        used.units[actor as usize].berserk_used = true;
        let ev = strike(&mut used, actor, v1, HIT);
        assert!(ev.iter().any(|e| matches!(e, Event::Died { .. })));
        assert!(berserk_events(&ev).is_empty());
        assert_eq!(used.units[actor as usize].ap, 9 - ATTACK_AP);
        assert!(used.units[actor as usize].berserk_used, "stays used");
    }
}

// ------------------------------------------------------------- AP cap arithmetic

#[test]
fn the_refund_is_exact_at_valid_ap_maxima_including_u32_max() {
    for (ap_max, ap) in [(ATTACK_AP, ATTACK_AP), (13, 13), (13, 6), (u32::MAX, u32::MAX), (u32::MAX, u32::MAX - 1), (u32::MAX, ATTACK_AP)] {
        for actor in [P1, E1] {
            let mut s = arena(actor, 1);
            let a = &mut s.units[actor as usize];
            a.stats.ap_max = ap_max;
            a.ap = ap;
            assert_fixture_valid(&s);
            let [v1, ..] = opponents(actor);
            let ev = strike(&mut s, actor, v1, HIT);
            // Paying 4 then regaining 4 always lands back on the old AP, never above ap_max.
            assert_eq!(berserk_events(&ev), vec![(actor, 4)], "ap_max {ap_max} ap {ap}");
            assert_eq!(s.units[actor as usize].ap, ap);
            assert!(s.units[actor as usize].ap <= ap_max);
        }
    }
}

#[test]
fn the_cap_truncates_defensively_when_ap_already_exceeds_ap_max() {
    // Outside the valid-fixture contract (ap > ap_max cannot arise in legal play); this
    // pins the saturating arithmetic: the gain is the room below ap_max, never negative.
    for (ap_max, ap, gained) in [(6, 9, 1), (5, 9, 0), (0, 9, 0), (4, 9, 0)] {
        let mut s = arena(P1, 1);
        s.units[0].stats.ap_max = ap_max;
        s.units[0].ap = ap;
        let ev = strike(&mut s, P1, E1, HIT);
        assert_eq!(berserk_events(&ev), vec![(P1, gained)], "ap_max {ap_max}");
        assert_eq!(s.units[0].ap, ap - ATTACK_AP + gained);
        assert!(s.units[0].berserk_used, "a zero gain still consumes the turn's use");
    }
}

// ------------------------------------------------------------- non-triggering outcomes

#[test]
fn misses_nonlethal_and_armour_only_hits_leave_the_use_untouched() {
    for actor in [P1, E1] {
        let [v1, v2, _] = opponents(actor);
        let mut s = arena(actor, 2);
        // Miss.
        let miss = strike(&mut s, actor, v1, MISS);
        assert!(matches!(miss[0], Event::Attacked { hit: false, .. }));
        assert!(berserk_events(&miss).is_empty() && !s.units[actor as usize].berserk_used);
        // Nonlethal hit.
        s.units[v1 as usize].hp = 5;
        s.units[v1 as usize].stats.hp = 5;
        s.units[v1 as usize].armour = 0;
        let hurt = strike(&mut s, actor, v1, HIT);
        assert!(matches!(hurt[0], Event::Attacked { hit: true, hp_loss: 1, .. }));
        assert_eq!(s.units[v1 as usize].hp, 4);
        assert!(berserk_events(&hurt).is_empty() && !s.units[actor as usize].berserk_used);
        // Armour-only hit on a 1-HP target.
        s.units[v2 as usize].stats.armour = 50;
        s.units[v2 as usize].armour = 50;
        s.units[actor as usize].ap = 9;
        let soaked = strike(&mut s, actor, v2, HIT);
        assert!(matches!(soaked[0], Event::Attacked { hit: true, absorbed: 1, hp_loss: 0, .. }), "{soaked:?}");
        assert_eq!(s.units[v2 as usize].hp, 1);
        assert!(berserk_events(&soaked).is_empty() && !s.units[actor as usize].berserk_used);
        assert_eq!(s.units[actor as usize].ap, 9 - ATTACK_AP, "no refund anywhere");
        // The first real kill still refunds afterwards.
        s.units[v1 as usize].hp = 1;
        let kill_ev = strike(&mut s, actor, v1, HIT);
        assert_eq!(berserk_events(&kill_ev), vec![(actor, 4)]);
    }
}

#[test]
fn nine_lives_survival_is_not_a_kill_but_the_following_death_is() {
    for actor in [P1, E1] {
        let [v1, ..] = opponents(actor);
        let mut s = arena(actor, 1);
        s.units[v1 as usize].has_nine_lives = true;
        let saved = strike(&mut s, actor, v1, HIT);
        assert!(saved.iter().any(|e| matches!(e, Event::NineLivesTriggered { unit } if *unit == v1)));
        assert!(!saved.iter().any(|e| matches!(e, Event::Died { .. })));
        assert!(berserk_events(&saved).is_empty());
        assert!(s.units[v1 as usize].alive && s.units[v1 as usize].hp == 1);
        assert!(!s.units[actor as usize].berserk_used);
        assert_eq!(s.units[actor as usize].ap, 9 - ATTACK_AP);

        let dead = strike(&mut s, actor, v1, HIT);
        assert!(dead.iter().any(|e| matches!(e, Event::Died { unit } if *unit == v1)));
        assert_eq!(berserk_events(&dead), vec![(actor, 4)]);
        assert_eq!(s.units[actor as usize].ap, 9 - 2 * ATTACK_AP + BERSERK_AP_REFUND);
    }
}

// ------------------------------------------------------------- battle end ordering

#[test]
fn killing_the_final_hostile_unit_emits_berserk_before_battle_ended() {
    for (actor, outcome) in [(P1, Outcome::Victory), (E1, Outcome::Defeat)] {
        let mut s = arena(actor, 1);
        let [v1, v2, v3] = opponents(actor);
        kill(&mut s, &[v2, v3]);
        let ev = strike(&mut s, actor, v1, HIT);
        assert!(matches!(ev[0], Event::Attacked { hit: true, .. }));
        assert_eq!(ev[1], Event::Died { unit: v1 });
        assert!(matches!(ev[2], Event::KillMoraleChecked { unit, .. } if unit == actor));
        assert_eq!(ev[3], Event::BerserkTriggered { unit: actor, ap_gained: 4 });
        assert_eq!(ev[4], Event::BattleEnded { outcome, reason: EndReason::Elimination });
        assert_eq!(ev.len(), 5, "{ev:?}");
        assert_eq!(s.outcome(), Some(outcome));
        assert_eq!(s.units[actor as usize].ap, 9);
    }
}

// ------------------------------------------------------------- turn lifecycle

#[test]
fn wait_preserves_usage_and_a_first_use_after_waiting_is_still_available() {
    for actor in [P1, E1] {
        let [v1, v2, _] = opponents(actor);
        // Use, then Wait: the returning portion of the same turn has no second refund.
        let mut s = arena(actor, 2);
        assert_eq!(berserk_events(&strike(&mut s, actor, v1, HIT)), vec![(actor, 4)]);
        let waited = s.apply(Command::Wait { unit: actor }).unwrap();
        assert_eq!(turn_starts(&waited, actor), 0);
        let ev = pass_until(&mut s, is_active(actor));
        assert_eq!(turn_starts(&ev, actor), 0, "no fresh refresh when Wait returns the unit");
        assert_eq!(s.round, 1);
        let a = &s.units[actor as usize];
        assert!(a.berserk_used && a.turn_started);
        assert_eq!(a.ap, 9, "Wait keeps the remaining AP, no refresh");
        let again = strike(&mut s, actor, v2, HIT);
        assert!(again.iter().any(|e| matches!(e, Event::Died { unit } if *unit == v2)));
        assert!(berserk_events(&again).is_empty());
        assert_eq!(s.units[actor as usize].ap, 9 - ATTACK_AP);

        // Wait first, then the first kill refunds once.
        let mut s = arena(actor, 2);
        s.apply(Command::Wait { unit: actor }).unwrap();
        let ev = pass_until(&mut s, is_active(actor));
        assert_eq!(turn_starts(&ev, actor), 0);
        assert!(!s.units[actor as usize].berserk_used);
        assert_eq!(berserk_events(&strike(&mut s, actor, v1, HIT)), vec![(actor, 4)]);
        assert!(berserk_events(&strike(&mut s, actor, v2, HIT)).is_empty());
    }
}

#[test]
fn another_units_action_does_not_reset_usage_and_the_next_fresh_turn_restores_it() {
    for actor in [P1, E1] {
        let [v1, v2, _] = opponents(actor);
        let mut s = arena(actor, 2);
        assert_eq!(berserk_events(&strike(&mut s, actor, v1, HIT)), vec![(actor, 4)]);
        let ended = s.apply(Command::EndTurn { unit: actor }).unwrap();
        assert_eq!(turn_starts(&ended, actor), 0);
        assert!(s.units[actor as usize].berserk_used, "ending the turn does not reset it");

        // Every other unit acts, through the round change; the flag is stable until the
        // actor's own fresh turn.
        let mut seen = Vec::new();
        for _ in 0..50 {
            let active = s.active_unit().unwrap().id;
            if active == actor && s.round == 2 {
                break;
            }
            assert_ne!(active, actor, "the actor cannot act twice in round 1");
            assert!(s.units[actor as usize].berserk_used, "flag reset by another unit's turn");
            seen.extend(s.apply(Command::EndTurn { unit: active }).unwrap());
        }
        assert_eq!(s.round, 2);
        assert_eq!(turn_starts(&seen, actor), 1, "exactly one fresh refresh for the actor");
        let a = &s.units[actor as usize];
        assert!(!a.berserk_used && a.turn_started);
        assert_eq!(a.ap, a.stats.ap_max);
        assert_eq!(berserk_events(&strike(&mut s, actor, v2, HIT)), vec![(actor, 4)]);
    }
}

#[test]
fn end_round_does_not_refresh_the_actor_and_a_skipped_unit_is_reset_by_its_pass_over() {
    let mut s = arena(P1, 1);
    s.queue = vec![P1, P2, P3, E1, E2, E3];
    s.units[P2 as usize].has_berserk = true;
    s.units[P2 as usize].berserk_used = true;
    assert_eq!(berserk_events(&strike(&mut s, P1, E1, HIT)), vec![(P1, 4)]);
    s.queue.retain(|&q| q != E1);
    let ev = s.apply(Command::EndRound { unit: P1 }).unwrap();
    assert_eq!(turn_starts(&ev, P1), 0);
    assert!(s.units[P1 as usize].berserk_used);
    // P2 was passed over: its once-per-round refresh ran (the only place the reset happens).
    assert_eq!(turn_starts(&ev, P2), 1);
    assert_eq!(turn_starts(&ev, P3), 1);
    assert!(!s.units[P2 as usize].berserk_used);
    assert_eq!(s.units[P2 as usize].ap, 0);

    let later = pass_until(&mut s, |s| s.round == 2 && is_active(P1)(s));
    assert_eq!(turn_starts(&later, P1), 1);
    assert_eq!(turn_starts(&ev, P2) + turn_starts(&later, P2), 2, "one refresh per round, no duplicates");
    assert!(!s.units[P1 as usize].berserk_used);
    assert_eq!(s.units[P1 as usize].ap, 9);
}

// ------------------------------------------------------------- opportunity attacks

#[test]
fn opportunity_attack_kills_neither_refund_nor_consume_but_the_later_ordinary_kill_does() {
    for (controller, mover) in [(P1, E1), (E1, P1)] {
        let [m1, m2, _] = opponents(controller);
        assert_eq!(m1, mover);
        // The mover starts beside the controller; its ally m2 also stands beside the controller.
        let mut s = arena(mover, 0);
        s.units[controller as usize].pos = ACTOR_AT;
        s.units[mover as usize].pos = VICTIM_AT[0];
        s.units[m2 as usize].pos = VICTIM_AT[1];
        s.units[controller as usize].has_berserk = true;
        s.units[mover as usize].has_berserk = false;
        for id in [mover, m2] {
            s.units[id as usize].hp = 1;
            s.units[id as usize].armour = 0;
        }
        assert_fixture_valid(&s);
        s.units[controller as usize].ap = 0;
        // The controller's own turn comes last, so the opportunity kill precedes it.
        s.queue.retain(|&q| q != controller);
        s.queue.push(controller);

        rig(&mut s, &[Some(HIT)]);
        let ev = s.apply(Command::Move { unit: mover, to: hex(2, -2) }).unwrap();
        assert!(matches!(ev[0], Event::Attacked { attacker, target, hit: true, .. } if attacker == controller && target == mover), "{ev:?}");
        assert!(ev.iter().any(|e| matches!(e, Event::Died { unit } if *unit == mover)));
        assert!(berserk_events(&ev).is_empty(), "{ev:?}");
        let c = &s.units[controller as usize];
        assert!(!c.berserk_used, "the opportunity kill must not consume the use");
        assert_eq!(c.ap, 0, "nor refund AP");
        assert!(s.units[mover as usize].fatigue == MOVE_FATIGUE + 5 && s.units[mover as usize].ap == 9 - MOVE_AP);

        // Its own scheduled turn: a fresh refresh, then the ordinary kill earns the refund.
        let ev = pass_until(&mut s, is_active(controller));
        assert_eq!(turn_starts(&ev, controller), 1);
        assert!(!s.units[controller as usize].berserk_used);
        let kill_ev = strike(&mut s, controller, m2, HIT);
        assert_eq!(berserk_events(&kill_ev), vec![(controller, 4)]);
    }
}

#[test]
fn an_opportunity_kill_leaves_an_already_used_flag_set_and_never_refunds() {
    let mut s = arena(E1, 0);
    s.units[P1 as usize].pos = ACTOR_AT;
    s.units[E1 as usize].pos = VICTIM_AT[0];
    s.units[E1 as usize].hp = 1;
    s.units[E1 as usize].armour = 0;
    s.units[P1 as usize].has_berserk = true;
    s.units[P1 as usize].berserk_used = true;
    s.units[P1 as usize].ap = 2;
    s.queue.retain(|&q| q != P1);
    s.queue.push(P1);
    assert_fixture_valid(&s);
    rig(&mut s, &[Some(HIT)]);
    let ev = s.apply(Command::Move { unit: E1, to: hex(2, -2) }).unwrap();
    assert!(ev.iter().any(|e| matches!(e, Event::Died { unit } if *unit == E1)));
    assert!(berserk_events(&ev).is_empty());
    assert!(s.units[P1 as usize].berserk_used);
    assert_eq!(s.units[P1 as usize].ap, 2);
}

// ------------------------------------------------------------- purity

#[test]
fn queries_and_rejected_commands_do_not_touch_state() {
    for actor in [P1, E1] {
        let [v1, v2, _] = opponents(actor);
        let mut s = arena(actor, 1);
        let before = snapshot(&s);
        let _ = s.preview_move(actor, hex(0, -3));
        let _ = s.legal_targets(actor);
        let _ = s.hit_chance(actor, v1);
        let _ = s.reachable(actor);
        let _ = ai::plan_turn(&s);
        assert_eq!(snapshot(&s), before, "queries");

        // Not adjacent / invalid targets / not the active unit.
        assert_eq!(s.apply(Command::Attack { attacker: actor, target: v2 }), Err(RuleError::NotAdjacent));
        assert_eq!(s.apply(Command::Attack { attacker: actor, target: actor }), Err(RuleError::InvalidTarget));
        let ally = if actor < 3 { P2 } else { E2 };
        assert_eq!(s.apply(Command::Attack { attacker: actor, target: ally }), Err(RuleError::InvalidTarget));
        assert_eq!(s.apply(Command::Attack { attacker: v1, target: actor }), Err(RuleError::NotActiveUnit));
        // A capable actor still needs the ordinary budgets; the refund only follows a kill.
        s.units[actor as usize].ap = ATTACK_AP - 1;
        assert_eq!(s.apply(Command::Attack { attacker: actor, target: v1 }), Err(RuleError::NotEnoughAp));
        s.units[actor as usize].ap = 9;
        s.units[actor as usize].fatigue = s.units[actor as usize].stats.fatigue_max - ATTACK_FATIGUE + 1;
        assert_eq!(s.apply(Command::Attack { attacker: actor, target: v1 }), Err(RuleError::TooFatigued));
        s.units[actor as usize].fatigue = 0;
        assert!(!s.units[actor as usize].berserk_used);
        assert!(s.units[v1 as usize].alive && s.units[actor as usize].ap == 9);
    }
}

#[test]
fn rejected_attacks_leave_the_full_snapshot_unchanged() {
    let mut s = arena(P1, 1);
    s.units[0].ap = ATTACK_AP - 1;
    let before = snapshot(&s);
    assert_eq!(s.apply(Command::Attack { attacker: P1, target: E1 }), Err(RuleError::NotEnoughAp));
    assert_eq!(snapshot(&s), before);
    s.units[0].ap = 9;
    s.units[0].fatigue = 100;
    let before = snapshot(&s);
    assert_eq!(s.apply(Command::Attack { attacker: P1, target: E1 }), Err(RuleError::TooFatigued));
    assert_eq!(snapshot(&s), before);
}

// ------------------------------------------------------------- hashing

#[test]
fn hash_flags_are_sensitive_without_overlap_and_prior_hashes_are_unchanged() {
    assert_eq!(BattleState::new(1).state_hash(), FLAT_SEED_1_HASH);
    assert_eq!(scenario::battle(1, TerrainPreset::Flat).state_hash(), FLAT_SCENARIO_HASH);
    assert_eq!(scenario::battle(1, TerrainPreset::Hills).state_hash(), HILLS_SCENARIO_HASH);

    // Every combination of the neighbouring flag bits (and the widest stack field below
    // them) yields a distinct hash, so bits 49 and 50 overlap nothing.
    let mut seen = Vec::new();
    for bits in 0..64u32 {
        let mut s = BattleState::new(1);
        let u = &mut s.units[1];
        u.has_dodge = bits & 1 != 0;
        u.has_nine_lives = bits & 2 != 0;
        u.nine_lives_used = bits & 4 != 0;
        u.has_berserk = bits & 8 != 0;
        u.berserk_used = bits & 16 != 0;
        u.fast_adaptation_stacks = if bits & 32 != 0 { u32::MAX } else { 0 };
        let h = s.state_hash();
        assert!(!seen.contains(&h), "hash collision at flag combination {bits:#08b}");
        seen.push(h);
    }
    let base = BattleState::new(1);
    let mut cap = base.clone();
    cap.units[0].has_berserk = true;
    let mut used = base.clone();
    used.units[0].berserk_used = true;
    assert_ne!(cap.state_hash(), base.state_hash());
    assert_ne!(used.state_hash(), base.state_hash());
    assert_ne!(cap.state_hash(), used.state_hash());
}

// ------------------------------------------------------------- AI progress bound

/// Run the actor's scheduled turn through `plan_turn`/`apply`; returns the non-EndTurn
/// command count and whether the turn ended in `EndTurn` or the battle ended.
fn drive_turn(s: &mut BattleState, actor: u32, events: &mut Vec<Event>) -> usize {
    let mut commands = 0;
    for _ in 0..=8 {
        if s.outcome().is_some() || s.active_unit().is_none_or(|u| u.id != actor) {
            return commands;
        }
        let plan = ai::plan_turn(s);
        assert_eq!(plan.len(), 1);
        if matches!(plan[0], Command::EndTurn { .. }) {
            events.extend(s.apply(plan[0]).expect("EndTurn is legal"));
            return commands;
        }
        commands += 1;
        events.extend(s.apply(plan[0]).unwrap_or_else(|e| panic!("{:?} rejected: {e:?}", plan[0])));
    }
    panic!("turn did not finish within the documented bound");
}

/// Capable actor beside three 30-HP hostiles that it hits 95% of the time for 10.
fn ai_fixture(actor: u32, seed: u64, adrenaline: bool, berserk: bool, hostile_hp: u32) -> BattleState {
    let mut s = arena(actor, 3);
    s.rng = Rng::new(seed);
    let a = &mut s.units[actor as usize];
    a.has_berserk = berserk;
    a.has_adrenaline = adrenaline;
    a.stats.melee_skill = 200;
    a.stats.dmg_min = 10;
    a.stats.dmg_max = 10;
    for v in opponents(actor) {
        let v = &mut s.units[v as usize];
        v.stats.hp = hostile_hp;
        v.hp = hostile_hp;
        v.stats.armour = 0;
        v.armour = 0;
    }
    assert_fixture_valid(&s);
    s
}

#[test]
fn ai_turns_stay_within_the_documented_command_bound_with_and_without_adrenaline() {
    let (mut triggers, mut ended) = (0, 0);
    for actor in [P1, E1] {
        for adrenaline in [false, true] {
            for seed in 1..=40u64 {
                for hostile_hp in [30, 10] {
                    let mut s = ai_fixture(actor, seed, adrenaline, true, hostile_hp);
                    let mut ev = Vec::new();
                    let n = drive_turn(&mut s, actor, &mut ev);
                    // 13 AP at most: one 1-AP Adrenaline plus six commands of at least 2 AP.
                    let bound = if adrenaline { 1 + (9 + BERSERK_AP_REFUND - ADRENALINE_AP) / 2 } else { (9 + BERSERK_AP_REFUND) / 2 };
                    assert!(n <= 7 && n as u32 <= bound, "actor {actor} seed {seed}: {n} commands");
                    assert!(berserk_events(&ev).len() <= 1, "at most one refund per scheduled turn");
                    triggers += berserk_events(&ev).len();
                    ended += usize::from(s.outcome().is_some());
                    for e in &ev {
                        if let Event::BerserkTriggered { unit, ap_gained } = e {
                            assert_eq!((*unit, *ap_gained), (actor, 4));
                        }
                    }
                    // Control: the same fixture without Berserk keeps the capability-free bound.
                    let mut plain = ai_fixture(actor, seed, adrenaline, false, hostile_hp);
                    let plain_n = drive_turn(&mut plain, actor, &mut Vec::new());
                    let plain_bound = if adrenaline { 1 + (9 - ADRENALINE_AP) / 2 } else { 9 / 2 };
                    assert!(plain_n <= 5 && plain_n as u32 <= plain_bound, "capability-free: {plain_n}");
                }
            }
        }
    }
    assert!(triggers > 0, "the fixtures must actually exercise a refund");
    let _ = ended;
}

#[test]
fn ai_turn_can_finish_the_battle_with_the_refund_before_battle_ended() {
    let mut finished = 0;
    for actor in [P1, E1] {
        for adrenaline in [false, true] {
            for seed in 1..=40u64 {
                let mut s = ai_fixture(actor, seed, adrenaline, true, 10);
                let [_, v2, v3] = opponents(actor);
                kill(&mut s, &[v2, v3]);
                let mut ev = Vec::new();
                let n = drive_turn(&mut s, actor, &mut ev);
                assert!(n <= 7);
                if s.outcome().is_some() {
                    finished += 1;
                    let last = ev.len() - 1;
                    assert!(matches!(ev[last], Event::BattleEnded { .. }));
                    assert!(matches!(ev[last - 1], Event::BerserkTriggered { .. }), "{ev:?}");
                }
            }
        }
    }
    assert!(finished > 0);
}
