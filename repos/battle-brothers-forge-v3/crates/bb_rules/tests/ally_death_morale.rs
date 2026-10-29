//! Ally-death Resolve checks after a melee death (partial UC-COMBAT-018,
//! APPROXIMATION: the catalog names the trigger, the "every present same-side
//! ally at any distance" policy is invented). Fixtures place the six stock
//! units on fixed hexes; seeds are found by mirroring the SplitMix64 stream, so
//! each test states the draws it relies on: attack roll, damage draw, one
//! draw per ally check in ascending ally id, then the killer's positive check.

use bb_rules::hex::{Hex, hex};
use bb_rules::rules::LAST_ROUND;
use bb_rules::*;

const P1: u32 = 0;
const P2: u32 = 1;
const P3: u32 = 2;
const E1: u32 = 3;
const E2: u32 = 4;
const E3: u32 = 5;

const HIT_CHANCE: u32 = 95;
const DMG: u32 = 10;

fn started(unit: u32) -> Event {
    Event::TurnStarted { unit }
}

/// `i`-th raw draw of a fresh stream, reduced like the rules do.
fn draw(seed: u64, i: usize) -> u32 {
    let mut rng = Rng::new(seed);
    let mut v = 0;
    for _ in 0..=i {
        v = rng.next_u64();
    }
    (v % 100) as u32
}

fn rng_after(seed: u64, n: usize) -> Rng {
    let mut rng = Rng::new(seed);
    for _ in 0..n {
        rng.next_u64();
    }
    rng
}

/// First seed whose first draw hits the forced 95% chance.
fn lethal_seed() -> u64 {
    (0..10_000u64).find(|&s| draw(s, 0) < HIT_CHANCE).unwrap()
}

/// Present units stand on distinct, in-map, passable hexes within their Stats maxima.
fn assert_valid_fixture(state: &BattleState) {
    let present: Vec<&Unit> = state.units.iter().filter(|u| u.is_present()).collect();
    for (i, u) in present.iter().enumerate() {
        assert!(u.pos.in_map() && !u.pos.is_obstacle(), "unit {} on invalid hex {:?}", u.id, u.pos);
        assert!(u.hp <= u.stats.hp && u.armour <= u.stats.armour, "unit {} exceeds its maxima", u.id);
        assert!(present[..i].iter().all(|o| o.pos != u.pos), "unit {} shares a hex", u.id);
    }
}

const START: [(u32, Hex); 6] =
    [(P1, hex(1, 0)), (P2, hex(-3, 2)), (P3, hex(-4, 3)), (E1, hex(2, 0)), (E2, hex(4, -3)), (E3, hex(3, -2))];

/// The six stock units on fixed hexes; `active` leads the queue (9 AP, turn
/// started), the others follow in ascending id order.
fn six(active: u32) -> BattleState {
    let mut s = BattleState::new(0);
    for (id, pos) in START {
        s.units[id as usize].pos = pos;
    }
    s.queue = std::iter::once(active).chain((0..6).filter(|&id| id != active)).collect();
    for u in &mut s.units {
        u.turn_started = u.id == active;
    }
    let a = &mut s.units[active as usize];
    a.ap = 9;
    a.fatigue = 0;
    assert_valid_fixture(&s);
    s
}

/// `killer` hits for `DMG` at 95% and `victim` dies to any hit; `seed` is set.
fn arm(s: &mut BattleState, killer: u32, victim: u32, seed: u64) {
    let k = &mut s.units[killer as usize];
    k.stats.melee_skill = 1000;
    k.stats.dmg_min = DMG;
    k.stats.dmg_max = DMG;
    let v = &mut s.units[victim as usize];
    v.stats.hp = 1;
    v.hp = 1;
    v.stats.armour = 0;
    v.armour = 0;
    s.rng = Rng::new(seed);
}

/// Remove a unit from the fixture (dead, out of the queue).
fn remove(s: &mut BattleState, ids: &[u32]) {
    for &id in ids {
        s.units[id as usize].alive = false;
        s.queue.retain(|&q| q != id);
    }
}

fn attacked(attacker: u32, target: u32, seed: u64) -> Event {
    Event::Attacked {
        attacker,
        target,
        hit: true,
        roll: draw(seed, 0),
        chance: HIT_CHANCE,
        dmg: DMG,
        absorbed: 0,
        hp_loss: DMG,
    }
}

/// The `idx`-th ally check (draw `2 + idx`) with the accepted negative-check formula.
fn ally_check(seed: u64, idx: usize, unit: u32, fallen: u32, resolve: u32, before: Morale) -> Event {
    let roll = draw(seed, 2 + idx);
    let chance = resolve.min(100);
    let after = if roll < chance { before } else { before.lowered() };
    Event::AllyDeathMoraleChecked { unit, fallen, roll, chance, before, after }
}

/// The killer's positive check (draw `2 + idx`, after the `idx` ally checks).
fn kill_check(seed: u64, idx: usize, unit: u32, fallen: u32, resolve: u32, before: Morale) -> Event {
    let roll = draw(seed, 2 + idx);
    let chance = resolve.min(100);
    let after = if roll < chance { before.raised() } else { before };
    Event::KillMoraleChecked { unit, fallen, roll, chance, before, after }
}

fn is_ally_check(e: &Event) -> bool {
    matches!(e, Event::AllyDeathMoraleChecked { .. })
}

fn snapshot(s: &BattleState) -> (u64, Rng, Vec<u32>) {
    (s.state_hash(), s.rng.clone(), s.queue.clone())
}

/// Opportunity kill: `mover` (1 HP) steps away from adjacent `killer`.
fn reaction_kill(mover: u32, killer: u32, to: Hex, seed: u64) -> BattleState {
    let mut s = six(mover);
    arm(&mut s, killer, mover, seed);
    assert_eq!(s.enemy_controllers_at(mover, s.units[mover as usize].pos), vec![killer]);
    assert_eq!(s.preview_move(mover, to).unwrap().first_threat.map(|t| t.controllers), Some(vec![killer]));
    s
}

// --- Trigger, order, RNG ----------------------------------------------------------

#[test]
fn ordinary_kill_checks_each_surviving_ally_of_either_side_in_id_order() {
    let seed = lethal_seed();
    for (killer, victim, allies) in [(P1, E1, [E2, E3]), (E1, P1, [P2, P3])] {
        let mut s = six(killer);
        arm(&mut s, killer, victim, seed);
        let ev = s.apply(Command::Attack { attacker: killer, target: victim }).unwrap();
        assert_eq!(
            ev,
            vec![
                attacked(killer, victim, seed),
                Event::Died { unit: victim },
                ally_check(seed, 0, allies[0], victim, 50, Morale::Steady),
                ally_check(seed, 1, allies[1], victim, 50, Morale::Steady),
                kill_check(seed, 2, killer, victim, 50, Morale::Steady),
            ]
        );
        assert_eq!(s.rng, rng_after(seed, 5), "roll, damage, one draw per ally and the kill check");
        assert!(!ev.iter().any(|e| matches!(e, Event::MoraleChecked { .. })), "the lethal victim gets no heavy-hit check");
        assert_eq!(s.units[victim as usize].morale, Morale::Steady);
        assert_eq!(s.outcome(), None);
        // Only the killer can change on the killer's side, by its own kill check.
        let killer_side = if killer == P1 { [P1, P2, P3] } else { [E1, E2, E3] };
        let Event::KillMoraleChecked { after, .. } = ev[4] else { unreachable!() };
        assert_eq!(s.units[killer as usize].morale, after);
        assert!(killer_side.iter().filter(|&&id| id != killer).all(|&id| s.units[id as usize].morale == Morale::Steady));
    }
}

#[test]
fn opportunity_kill_shares_the_handling_and_then_advances_normally() {
    let seed = lethal_seed();
    // Player mover killed by E1's reaction; then P2 starts.
    let mut s = reaction_kill(P1, E1, hex(0, 1), seed);
    let ev = s.apply(Command::Move { unit: P1, to: hex(0, 1) }).unwrap();
    assert_eq!(
        ev,
        vec![
            attacked(E1, P1, seed),
            Event::Died { unit: P1 },
            ally_check(seed, 0, P2, P1, 50, Morale::Steady),
            ally_check(seed, 1, P3, P1, 50, Morale::Steady),
            kill_check(seed, 2, E1, P1, 50, Morale::Steady),
            started(P2),
        ]
    );
    assert_eq!(s.rng, rng_after(seed, 5));
    assert_eq!(s.queue, vec![P2, P3, E1, E2, E3]);

    // Enemy mover killed by P1's reaction; then P1 starts.
    let mut s = reaction_kill(E1, P1, hex(3, 0), seed);
    let ev = s.apply(Command::Move { unit: E1, to: hex(3, 0) }).unwrap();
    assert_eq!(
        ev,
        vec![
            attacked(P1, E1, seed),
            Event::Died { unit: E1 },
            ally_check(seed, 0, E2, E1, 50, Morale::Steady),
            ally_check(seed, 1, E3, E1, 50, Morale::Steady),
            kill_check(seed, 2, P1, E1, 50, Morale::Steady),
            started(P1),
        ]
    );
    assert_eq!(s.rng, rng_after(seed, 5));
    assert_eq!(s.queue, vec![P1, P2, P3, E2, E3]);
}

#[test]
fn check_order_follows_unit_id_not_queue_order_and_the_queue_is_not_reordered() {
    let seed = lethal_seed();
    let mut s = six(E1);
    s.queue = vec![E1, P3, P2, E3, E2, P1];
    arm(&mut s, E1, P1, seed);
    let ev = s.apply(Command::Attack { attacker: E1, target: P1 }).unwrap();
    assert_eq!(
        ev[2..],
        [
            ally_check(seed, 0, P2, P1, 50, Morale::Steady),
            ally_check(seed, 1, P3, P1, 50, Morale::Steady),
            kill_check(seed, 2, E1, P1, 50, Morale::Steady),
        ]
    );
    assert_eq!(s.queue, vec![E1, P3, P2, E3, E2], "only the casualty leaves the queue");
}

#[test]
fn eligibility_ignores_distance_and_turn_status_but_not_side_death_or_escape() {
    let seed = lethal_seed();
    // P2: far, turn already finished (not queued). P3: adjacent to the casualty,
    // Wait-deferred and EndRound-marked. Both are checked.
    let mut s = six(E1);
    s.units[P3 as usize].pos = hex(0, 1);
    s.units[P3 as usize].waited = true;
    s.units[P3 as usize].skip_turn = true;
    s.units[P2 as usize].pos = hex(-4, 3);
    s.queue.retain(|&id| id != P2);
    assert_valid_fixture(&s);
    assert!(s.units[P2 as usize].pos.distance(s.units[P1 as usize].pos) > 4);
    assert_eq!(s.units[P3 as usize].pos.distance(s.units[P1 as usize].pos), 1);
    arm(&mut s, E1, P1, seed);
    let ev = s.apply(Command::Attack { attacker: E1, target: P1 }).unwrap();
    assert_eq!(
        ev[2..],
        [
            ally_check(seed, 0, P2, P1, 50, Morale::Steady),
            ally_check(seed, 1, P3, P1, 50, Morale::Steady),
            kill_check(seed, 2, E1, P1, 50, Morale::Steady),
        ]
    );

    // A dead ally and an escaped ally are skipped; enemies never are checked.
    for escape in [false, true] {
        let mut s = six(E1);
        if escape {
            s.units[P3 as usize].fled = true;
            s.queue.retain(|&id| id != P3);
        } else {
            remove(&mut s, &[P3]);
        }
        let p3_before = s.units[P3 as usize].morale;
        arm(&mut s, E1, P1, seed);
        let ev = s.apply(Command::Attack { attacker: E1, target: P1 }).unwrap();
        assert_eq!(
            ev,
            vec![
                attacked(E1, P1, seed),
                Event::Died { unit: P1 },
                ally_check(seed, 0, P2, P1, 50, Morale::Steady),
                kill_check(seed, 1, E1, P1, 50, Morale::Steady),
            ]
        );
        assert_eq!(s.rng, rng_after(seed, 4));
        assert_eq!(s.units[P3 as usize].morale, p3_before);
        assert!([E2, E3].iter().all(|&id| s.units[id as usize].morale == Morale::Steady));
    }
}

// --- Check formula ----------------------------------------------------------------

/// Only P2 survives next to the casualty, so its check is draw 2.
fn lone_ally(seed: u64) -> BattleState {
    let mut s = six(E1);
    remove(&mut s, &[P3]);
    arm(&mut s, E1, P1, seed);
    s
}

#[test]
fn resolve_zero_hundred_above_hundred_and_the_equality_boundary() {
    let seed = (0..10_000u64).find(|&s| draw(s, 0) < HIT_CHANCE && (1..=98).contains(&draw(s, 2))).unwrap();
    let r = draw(seed, 2);
    // (resolve, passes): roll < min(resolve, 100) passes; roll == chance fails.
    for (resolve, passes) in [(0, false), (r, false), (r + 1, true), (100, true), (150, true), (u32::MAX, true)] {
        let mut s = lone_ally(seed);
        s.units[P2 as usize].stats.resolve = resolve;
        let ev = s.apply(Command::Attack { attacker: E1, target: P1 }).unwrap();
        let expected = ally_check(seed, 0, P2, P1, resolve, Morale::Steady);
        assert_eq!(ev[2], expected, "resolve {resolve}");
        let Event::AllyDeathMoraleChecked { chance, after, .. } = ev[2] else { unreachable!() };
        assert_eq!(chance, resolve.min(100));
        assert_eq!(after, if passes { Morale::Steady } else { Morale::Wavering }, "resolve {resolve}");
        assert_eq!(s.units[P2 as usize].morale, after);
        assert_eq!(s.units[P2 as usize].stats.resolve, resolve, "base Resolve is never modified");
    }
}

#[test]
fn every_failure_transition_including_an_already_fleeing_ally() {
    let seed = lethal_seed();
    let table = [
        (Morale::Confident, Morale::Steady),
        (Morale::Steady, Morale::Wavering),
        (Morale::Wavering, Morale::Breaking),
        (Morale::Breaking, Morale::Fleeing),
        (Morale::Fleeing, Morale::Fleeing),
    ];
    for (before, after) in table {
        // Resolve 0 always fails; one draw and one event even for a Fleeing ally.
        let mut s = lone_ally(seed);
        s.units[P2 as usize].morale = before;
        s.units[P2 as usize].stats.resolve = 0;
        s.units[P2 as usize].skip_turn = true;
        let ev = s.apply(Command::Attack { attacker: E1, target: P1 }).unwrap();
        assert_eq!(
            ev[2],
            Event::AllyDeathMoraleChecked {
                unit: P2,
                fallen: P1,
                roll: draw(seed, 2),
                chance: 0,
                before,
                after
            }
        );
        assert_eq!(ev[3], kill_check(seed, 1, E1, P1, 50, Morale::Steady));
        assert_eq!(ev.len(), 4);
        assert_eq!(s.rng, rng_after(seed, 4));
        assert_eq!(s.units[P2 as usize].morale, after);
        // A failure that leaves the unit Fleeing clears a pending skip, as in `check_morale`.
        assert_eq!(s.units[P2 as usize].skip_turn, after != Morale::Fleeing, "{before:?}");

        // Resolve 100 always passes and leaves every state alone.
        let mut s = lone_ally(seed);
        s.units[P2 as usize].morale = before;
        s.units[P2 as usize].stats.resolve = 100;
        let ev = s.apply(Command::Attack { attacker: E1, target: P1 }).unwrap();
        assert!(matches!(ev[2], Event::AllyDeathMoraleChecked { before: b, after: a, chance: 100, .. } if b == before && a == before));
        assert_eq!(s.rng, rng_after(seed, 4));
    }
}

#[test]
fn a_morale_change_does_not_propagate_into_more_checks() {
    let seed = lethal_seed();
    let mut s = six(E1);
    arm(&mut s, E1, P1, seed);
    for id in [P2, P3] {
        s.units[id as usize].morale = Morale::Breaking;
        s.units[id as usize].stats.resolve = 0;
    }
    let ev = s.apply(Command::Attack { attacker: E1, target: P1 }).unwrap();
    assert_eq!(ev.iter().filter(|e| is_ally_check(e)).count(), 2, "{ev:?}");
    assert_eq!(s.rng, rng_after(seed, 5));
    assert!([P2, P3].iter().all(|&id| s.units[id as usize].morale == Morale::Fleeing));
}

#[test]
fn changed_morale_immediately_affects_attributes_and_zone_of_control() {
    let seed = lethal_seed();
    let mut s = six(E1);
    arm(&mut s, E1, P1, seed);
    s.units[P2 as usize].morale = Morale::Breaking;
    s.units[P2 as usize].stats.resolve = 0;
    s.units[P3 as usize].stats.resolve = 100;
    let next_to_p2 = hex(-2, 2);
    assert_eq!(s.units[P2 as usize].pos.distance(next_to_p2), 1);
    assert_eq!(s.enemy_controllers_at(E2, next_to_p2), vec![P2]);
    let defense = s.units[P2 as usize].stats.defense;
    assert_eq!(s.units[P2 as usize].effective_defense(), defense * 80 / 100);
    let queue = s.queue.clone();
    s.apply(Command::Attack { attacker: E1, target: P1 }).unwrap();
    assert_eq!(s.units[P2 as usize].morale, Morale::Fleeing);
    assert_eq!(s.units[P2 as usize].effective_defense(), defense * 70 / 100);
    assert!(s.enemy_controllers_at(E2, next_to_p2).is_empty(), "a Fleeing unit exerts no ZoC");
    assert_eq!(s.queue, queue.into_iter().filter(|&id| id != P1).collect::<Vec<_>>());
}

// --- Scheduled skips and automatic control ----------------------------------------

/// P1 ends the round (marking P2/P3); E1 then kills P3 next to it.
fn marked_board(p2_resolve: u32) -> (BattleState, u64) {
    let seed = lethal_seed();
    let mut s = six(P1);
    s.units[P1 as usize].pos = hex(-3, 3);
    s.units[P3 as usize].pos = hex(1, 0);
    s.queue = vec![P1, E1, P2, P3, E2, E3];
    assert_valid_fixture(&s);
    arm(&mut s, E1, P3, seed);
    s.units[P2 as usize].morale = Morale::Breaking;
    s.units[P2 as usize].stats.resolve = p2_resolve;
    let ev = s.apply(Command::Attack { attacker: P1, target: E1 });
    assert_eq!(ev, Err(RuleError::NotAdjacent), "P1 is far from E1; the rejected command changed nothing");
    assert_eq!(s.rng, Rng::new(seed));
    let ev = s.apply(Command::EndRound { unit: P1 }).unwrap();
    assert_eq!(ev, vec![started(E1)]);
    assert!(s.units[P2 as usize].skip_turn && s.units[P3 as usize].skip_turn);
    (s, seed)
}

#[test]
fn a_marked_ally_entering_fleeing_loses_its_skip_and_keeps_its_automatic_turn() {
    let (mut s, seed) = marked_board(0);
    let ev = s.apply(Command::Attack { attacker: E1, target: P3 }).unwrap();
    // P1 finished its turn (EndRound) and is still checked; ascending ids P1, P2.
    assert_eq!(
        ev,
        vec![
            attacked(E1, P3, seed),
            Event::Died { unit: P3 },
            ally_check(seed, 0, P1, P3, 50, Morale::Steady),
            ally_check(seed, 1, P2, P3, 0, Morale::Breaking),
            kill_check(seed, 2, E1, P3, 50, Morale::Steady),
        ]
    );
    assert_eq!(s.units[P2 as usize].morale, Morale::Fleeing);
    assert!(!s.units[P2 as usize].skip_turn);
    assert_eq!(s.queue, vec![E1, P2, E2, E3]);

    let ev = s.apply(Command::EndTurn { unit: E1 }).unwrap();
    assert!(matches!(
        ev[..],
        [Event::TurnStarted { unit: P2 }, Event::SelfRallyChecked { unit: P2, chance: 0, before: Morale::Fleeing, after: Morale::Fleeing, .. }]
    ), "{ev:?}");
    assert_eq!(s.queue[0], P2, "the Fleeing unit keeps its turn instead of being passed over");
    assert_eq!(s.units[P2 as usize].ap, 9);
    assert!(s.apply(Command::Flee { unit: P2 }).is_ok());
}

#[test]
fn a_marked_ally_that_passes_still_forfeits_its_turn() {
    let (mut s, _) = marked_board(100);
    s.apply(Command::Attack { attacker: E1, target: P3 }).unwrap();
    assert_eq!(s.units[P2 as usize].morale, Morale::Breaking);
    assert!(s.units[P2 as usize].skip_turn);
    let ev = s.apply(Command::EndTurn { unit: E1 }).unwrap();
    assert_eq!(ev, vec![started(P2), started(E2)]);
    assert_eq!(s.queue, vec![E2, E3]);
}

// --- Terminal events and turn advancement -----------------------------------------

#[test]
fn round_and_time_limit_transitions_follow_the_checks() {
    let seed = lethal_seed();
    let checks = [
        ally_check(seed, 0, P2, P1, 50, Morale::Steady),
        ally_check(seed, 1, P3, P1, 50, Morale::Steady),
        kill_check(seed, 2, E1, P1, 50, Morale::Steady),
    ];
    let mut s = reaction_kill(P1, E1, hex(0, 1), seed);
    s.queue = vec![P1];
    let ev = s.apply(Command::Move { unit: P1, to: hex(0, 1) }).unwrap();
    assert_eq!(
        ev[..5],
        [attacked(E1, P1, seed), Event::Died { unit: P1 }, checks[0].clone(), checks[1].clone(), checks[2].clone()]
    );
    assert_eq!(ev[5], Event::RoundStarted { round: 2 });
    assert!(matches!(ev[6], Event::TurnStarted { .. }) && ev.len() == 7, "{ev:?}");
    assert_eq!(s.round, 2);

    let mut s = reaction_kill(P1, E1, hex(0, 1), seed);
    s.queue = vec![P1];
    s.round = LAST_ROUND;
    let ev = s.apply(Command::Move { unit: P1, to: hex(0, 1) }).unwrap();
    assert_eq!(
        ev[..5],
        [attacked(E1, P1, seed), Event::Died { unit: P1 }, checks[0].clone(), checks[1].clone(), checks[2].clone()]
    );
    assert_eq!(ev[5..], [Event::BattleEnded { outcome: Outcome::Defeat, reason: EndReason::TimeLimit }]);
    assert_eq!(s.apply(Command::EndTurn { unit: P2 }), Err(RuleError::BattleOver));
}

#[test]
fn a_final_casualty_without_present_allies_adds_no_draws() {
    let seed = lethal_seed();
    // Last player unit: the others are dead or escaped.
    for escape in [false, true] {
        let mut s = six(E1);
        if escape {
            for id in [P2, P3] {
                s.units[id as usize].fled = true;
                s.queue.retain(|&q| q != id);
            }
        } else {
            remove(&mut s, &[P2, P3]);
        }
        arm(&mut s, E1, P1, seed);
        let ev = s.apply(Command::Attack { attacker: E1, target: P1 }).unwrap();
        let outcome = if escape { Outcome::Retreat } else { Outcome::Defeat };
        let reason = if escape { EndReason::Escape } else { EndReason::Elimination };
        assert_eq!(
            ev,
            vec![
                attacked(E1, P1, seed),
                Event::Died { unit: P1 },
                kill_check(seed, 0, E1, P1, 50, Morale::Steady),
                Event::BattleEnded { outcome, reason }
            ],
            "the final kill is checked before BattleEnded"
        );
        assert_eq!(s.rng, rng_after(seed, 3), "no ally draws, one kill draw");
    }
    // Last enemy: one escaped, one dead.
    let mut s = six(P1);
    s.units[E2 as usize].fled = true;
    remove(&mut s, &[E3]);
    s.queue.retain(|&q| q != E2);
    arm(&mut s, P1, E1, seed);
    let ev = s.apply(Command::Attack { attacker: P1, target: E1 }).unwrap();
    assert_eq!(
        ev,
        vec![
            attacked(P1, E1, seed),
            Event::Died { unit: E1 },
            kill_check(seed, 0, P1, E1, 50, Morale::Steady),
            Event::BattleEnded { outcome: Outcome::Victory, reason: EndReason::Escape }
        ]
    );
    assert_eq!(s.rng, rng_after(seed, 3));
}

#[test]
fn the_active_fleeing_mover_can_die_with_one_check_per_ally_and_one_advance() {
    let seed = lethal_seed();
    let mut s = BattleState::new(0);
    for u in &mut s.units {
        u.alive = false;
    }
    for (id, pos) in [(P1, hex(-3, 3)), (E1, hex(-2, 3)), (P2, hex(2, -3))] {
        let u = &mut s.units[id as usize];
        u.alive = true;
        u.pos = pos;
    }
    s.queue = vec![P1, E1, P2];
    s.units[P1 as usize].ap = 9;
    s.units[P1 as usize].turn_started = true;
    s.units[P1 as usize].morale = Morale::Fleeing;
    s.units[P2 as usize].morale = Morale::Fleeing; // already Fleeing: still checked
    assert_valid_fixture(&s);
    arm(&mut s, E1, P1, seed);
    let ev = s.apply(Command::Flee { unit: P1 }).unwrap();
    assert_eq!(
        ev,
        vec![
            attacked(E1, P1, seed),
            Event::Died { unit: P1 },
            ally_check(seed, 0, P2, P1, 50, Morale::Fleeing),
            kill_check(seed, 1, E1, P1, 50, Morale::Steady),
            started(E1),
        ]
    );
    assert_eq!(s.queue, vec![E1, P2]);
    assert_eq!(s.rng, rng_after(seed, 4));
    assert!(!s.units[P1 as usize].alive && !s.units[P1 as usize].fled);
}

// --- Non-triggers -----------------------------------------------------------------

#[test]
fn misses_surviving_hits_and_escapes_cause_no_ally_checks() {
    let seed = (0..10_000u64).find(|&s| draw(s, 0) >= 5).unwrap();
    // Miss: defense 1000 keeps the chance at its 5% floor.
    let mut s = six(P1);
    arm(&mut s, P1, E1, seed);
    s.units[E1 as usize].stats.defense = 1000;
    let ev = s.apply(Command::Attack { attacker: P1, target: E1 }).unwrap();
    assert!(matches!(ev[..], [Event::Attacked { hit: false, .. }]), "{ev:?}");
    assert_eq!(s.rng, rng_after(seed, 1));

    let lethal = lethal_seed();
    // Surviving heavy hit: exactly the heavy-hit check, no ally checks.
    let mut s = six(P1);
    arm(&mut s, P1, E1, lethal);
    s.units[P1 as usize].stats.dmg_min = 20;
    s.units[P1 as usize].stats.dmg_max = 20;
    s.units[E1 as usize].stats.hp = 100;
    s.units[E1 as usize].hp = 100;
    let ev = s.apply(Command::Attack { attacker: P1, target: E1 }).unwrap();
    assert!(matches!(ev[..], [Event::Attacked { hit: true, hp_loss: 20, .. }, Event::MoraleChecked { unit: E1, .. }]), "{ev:?}");
    assert_eq!(s.rng, rng_after(lethal, 3));

    // Surviving light hit: nothing else.
    let mut s = six(P1);
    arm(&mut s, P1, E1, lethal);
    s.units[P1 as usize].stats.dmg_min = 5;
    s.units[P1 as usize].stats.dmg_max = 5;
    s.units[E1 as usize].stats.hp = 100;
    s.units[E1 as usize].hp = 100;
    let ev = s.apply(Command::Attack { attacker: P1, target: E1 }).unwrap();
    assert!(matches!(ev[..], [Event::Attacked { hit: true, hp_loss: 5, .. }]), "{ev:?}");

    // Escape of a Fleeing unit on a map edge.
    let mut s = six(P1);
    s.units[P1 as usize].pos = hex(-4, 4);
    s.units[P1 as usize].morale = Morale::Fleeing;
    s.rng = Rng::new(lethal);
    assert_valid_fixture(&s);
    let ev = s.apply(Command::Flee { unit: P1 }).unwrap();
    assert_eq!(ev[0], Event::Fled { unit: P1 });
    assert!(!ev.iter().any(is_ally_check), "{ev:?}");
    assert_eq!(s.units[P2 as usize].morale, Morale::Steady);
}

#[test]
fn rejected_commands_and_previews_stay_state_and_rng_pure() {
    let seed = lethal_seed();
    let mut s = six(P1);
    arm(&mut s, P1, E1, seed);
    let before = snapshot(&s);
    // E2 is not adjacent and the 3 AP attack is unaffordable: both are rejected untouched.
    assert_eq!(s.apply(Command::Attack { attacker: P1, target: E2 }), Err(RuleError::NotAdjacent));
    s.units[P1 as usize].ap = 3;
    let before_ap = snapshot(&s);
    assert_eq!(s.apply(Command::Attack { attacker: P1, target: E1 }), Err(RuleError::NotEnoughAp));
    assert_eq!(snapshot(&s), before_ap);
    assert_eq!(s.apply(Command::Attack { attacker: E1, target: P1 }), Err(RuleError::NotActiveUnit));
    assert!(s.preview_move(P1, hex(0, 1)).is_ok());
    assert_eq!(s.preview_move(E1, hex(3, 0)), Err(RuleError::NotActiveUnit));
    assert_eq!(snapshot(&s).1, before.1);
    assert_eq!(snapshot(&s), before_ap);
}

// --- Deterministic AI replay ------------------------------------------------------

fn replay(seed: u64) -> (Vec<Event>, u64, BattleState) {
    let mut s = BattleState::new(seed);
    let mut log = Vec::new();
    for _ in 0..5_000 {
        if s.outcome().is_some() {
            break;
        }
        let cmd = ai::plan_turn(&s)[0];
        let ev = s.apply(cmd).unwrap();
        // Every death is directly followed by one check per present same-side ally.
        for (i, e) in ev.iter().enumerate() {
            if let Event::Died { unit } = *e {
                let side = s.units[unit as usize].side;
                let expected: Vec<u32> =
                    s.units.iter().filter(|u| u.is_present() && u.side == side).map(|u| u.id).collect();
                let got: Vec<u32> = ev[i + 1..]
                    .iter()
                    .map_while(|e| match *e {
                        Event::AllyDeathMoraleChecked { unit: u, fallen, .. } if fallen == unit => Some(u),
                        _ => None,
                    })
                    .collect();
                assert_eq!(got, expected, "{ev:?}");
            }
        }
        log.extend(ev);
    }
    assert!(s.outcome().is_some(), "seed {seed} must finish");
    let hash = s.state_hash();
    (log, hash, s)
}

#[test]
fn ai_battles_replay_identically_and_exercise_ally_death_checks() {
    let mut seen_check = false;
    for seed in 1..=25u64 {
        let (a, ha, sa) = replay(seed);
        let (b, hb, sb) = replay(seed);
        assert_eq!((&a, ha, sa.outcome(), sa.round), (&b, hb, sb.outcome(), sb.round), "seed {seed}");
        seen_check |= a.iter().any(is_ally_check);
    }
    assert!(seen_check, "some AI battle must reach an ally-death check");
}
