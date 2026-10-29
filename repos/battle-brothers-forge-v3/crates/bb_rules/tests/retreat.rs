//! Party retreat order (partial UC-COMBAT-022, APPROXIMATION): `Command::Retreat`
//! marks every present player, units already on a map edge leave at once, and
//! the rest withdraw automatically through the ordinary movement, reaction and
//! escape rules shared with `Flee`. Fixtures clear the stock scenario
//! (`alive = false`) and place only the units they need; reaction seeds are
//! found by mirroring the first SplitMix64 draw.

use bb_rules::hex::{Hex, hex};
use bb_rules::*;

const P1: u32 = 0;
const P2: u32 = 1;
const P3: u32 = 2;
const E1: u32 = 3;
const E2: u32 = 4;
const E3: u32 = 5;

fn started(unit: u32) -> Event {
    Event::TurnStarted { unit }
}

/// Only `placed` units are present; the queue follows the given order, so the
/// first entry is the active unit (9 AP, no fatigue, turn already started).
fn board(seed: u64, placed: &[(u32, Hex)]) -> BattleState {
    let mut s = BattleState::new(seed);
    for u in &mut s.units {
        u.alive = false;
    }
    for &(id, pos) in placed {
        let u = &mut s.units[id as usize];
        u.alive = true;
        u.pos = pos;
    }
    s.queue = placed.iter().map(|p| p.0).collect();
    let active = &mut s.units[placed[0].0 as usize];
    active.ap = 9;
    active.fatigue = 0;
    active.turn_started = true;
    assert_valid_fixture(&s);
    s
}

/// Present units stand on distinct, in-map, passable hexes.
fn assert_valid_fixture(state: &BattleState) {
    let present: Vec<&Unit> = state.units.iter().filter(|u| u.is_present()).collect();
    for (i, u) in present.iter().enumerate() {
        assert!(u.pos.in_map() && !u.pos.is_obstacle(), "unit {} on invalid hex {:?}", u.id, u.pos);
        assert!(present[..i].iter().all(|o| o.pos != u.pos), "unit {} shares a hex", u.id);
    }
}

fn first_draw(seed: u64) -> u64 {
    Rng::new(seed).next_u64() % 100
}

/// First seed whose first roll is below (`hit`) or at/above `chance`.
fn seed_with_first_roll(chance: u32, hit: bool) -> u64 {
    (0..10_000u64).find(|&s| (first_draw(s) < u64::from(chance)) == hit).unwrap()
}

fn snapshot(s: &BattleState) -> (u64, Rng, Vec<u32>, Vec<Unit>) {
    (s.state_hash(), s.rng.clone(), s.queue.clone(), s.units.clone())
}

fn retreating(s: &mut BattleState, ids: &[u32]) {
    for &id in ids {
        s.units[id as usize].retreating = true;
    }
}

fn is_edge(h: Hex) -> bool {
    h.q.abs().max(h.r.abs()).max(h.s().abs()) == hex::RADIUS
}

// --- Request eligibility ---------------------------------------------------------

#[test]
fn retreat_is_accepted_from_the_present_controllable_active_player() {
    let mut s = board(7, &[(P1, hex(-3, 2)), (E1, hex(3, -2))]);
    assert!(!s.units[P1 as usize].retreating, "retreating starts false");
    let ev = s.apply(Command::Retreat { unit: P1 }).unwrap();
    assert_eq!(ev, vec![Event::RetreatOrdered { unit: P1 }]);
    assert!(s.units[P1 as usize].retreating);
}

#[test]
fn retreat_rejections_are_pure_and_keep_error_precedence() {
    // Not the active unit.
    let mut s = board(7, &[(P1, hex(-3, 2)), (P2, hex(-2, 2)), (E1, hex(3, -2))]);
    let before = snapshot(&s);
    assert_eq!(s.apply(Command::Retreat { unit: P2 }), Err(RuleError::NotActiveUnit));
    assert_eq!(s.apply(Command::Retreat { unit: E1 }), Err(RuleError::NotActiveUnit));
    assert_eq!(snapshot(&s), before);

    // Battle over wins over NotActiveUnit and every other check.
    s.outcome = Some(Outcome::Victory);
    let over = snapshot(&s);
    for unit in [P1, P2, E1] {
        assert_eq!(s.apply(Command::Retreat { unit }), Err(RuleError::BattleOver));
        assert_eq!(s.apply(Command::Withdraw { unit }), Err(RuleError::BattleOver));
    }
    assert_eq!(snapshot(&s), over);

    // Enemy issuer.
    let mut s = board(7, &[(E1, hex(3, -2)), (P1, hex(-3, 2))]);
    let before = snapshot(&s);
    assert_eq!(s.apply(Command::Retreat { unit: E1 }), Err(RuleError::NotPlayerUnit));
    assert_eq!(snapshot(&s), before);

    // Fleeing issuer.
    let mut s = board(7, &[(P1, hex(-3, 2)), (P2, hex(-2, 2)), (E1, hex(3, -2))]);
    s.units[P1 as usize].morale = Morale::Fleeing;
    let before = snapshot(&s);
    assert_eq!(s.apply(Command::Retreat { unit: P1 }), Err(RuleError::FleeingUnit));
    assert_eq!(snapshot(&s), before);

    // Already retreating, with and without Fleeing morale.
    for fleeing in [false, true] {
        let mut s = board(7, &[(P1, hex(-3, 2)), (P2, hex(-2, 2)), (E1, hex(3, -2))]);
        retreating(&mut s, &[P1]);
        if fleeing {
            s.units[P1 as usize].morale = Morale::Fleeing;
        }
        let before = snapshot(&s);
        assert_eq!(s.apply(Command::Retreat { unit: P1 }), Err(RuleError::RetreatingUnit));
        assert_eq!(snapshot(&s), before);
        assert!(!s.units[P2 as usize].retreating);
    }

    // An active unit that is no longer present (stale queue head).
    let mut s = board(7, &[(P1, hex(-3, 2)), (P2, hex(-2, 2)), (E1, hex(3, -2))]);
    s.units[P1 as usize].alive = false;
    let before = snapshot(&s);
    assert_eq!(s.apply(Command::Retreat { unit: P1 }), Err(RuleError::NotActiveUnit));
    assert_eq!(snapshot(&s), before);
}

#[test]
fn retreat_consumes_no_rng_and_no_ap() {
    let mut s = board(7, &[(P1, hex(-3, 2)), (E1, hex(-2, 2))]);
    s.units[P1 as usize].ap = 5;
    s.units[P1 as usize].fatigue = 12;
    let rng = s.rng.clone();
    s.apply(Command::Retreat { unit: P1 }).unwrap();
    assert_eq!(s.rng, rng);
    assert_eq!((s.units[P1 as usize].ap, s.units[P1 as usize].fatigue), (5, 12));
}

// --- Marking ----------------------------------------------------------------------

#[test]
fn every_present_player_is_marked_and_pending_skips_are_cleared() {
    // P1 active, P2 Fleeing and queued, P3 finished its turn with a pending skip.
    let mut s = board(7, &[(P1, hex(-3, 2)), (P2, hex(-2, 2)), (E1, hex(3, -2)), (E2, hex(3, -3))]);
    s.units[P2 as usize].morale = Morale::Fleeing;
    s.units[P2 as usize].skip_turn = true;
    s.units[P3 as usize].alive = true;
    s.units[P3 as usize].pos = hex(-2, 1);
    s.units[P3 as usize].skip_turn = true;
    s.units[P3 as usize].turn_started = true;
    s.queue = vec![P1, E1, P2, E2];
    let ev = s.apply(Command::Retreat { unit: P1 }).unwrap();
    assert_eq!(ev, vec![Event::RetreatOrdered { unit: P1 }]);
    for id in [P1, P2, P3] {
        let u = &s.units[id as usize];
        assert!(u.retreating, "P{}", id + 1);
        assert!(!u.skip_turn, "P{} skip cleared", id + 1);
    }
    assert_eq!(s.units[P2 as usize].morale, Morale::Fleeing, "morale is untouched");
    assert!(s.units[E1 as usize..=E2 as usize].iter().all(|u| !u.retreating));
    assert_eq!(s.queue, vec![P1, E1, P2, E2], "no queue change");
    assert_eq!(s.active_unit().map(|u| u.id), Some(P1));
}

#[test]
fn enemies_casualties_and_escapees_are_left_unchanged() {
    let mut s = board(7, &[(P1, hex(-3, 2)), (E1, hex(3, -2)), (E2, hex(3, -3))]);
    s.units[P2 as usize].fled = true; // escaped, alive
    s.units[P2 as usize].alive = true;
    s.units[E1 as usize].skip_turn = true;
    let (p2, p3, e1, e2, e3) = (
        s.units[P2 as usize].clone(),
        s.units[P3 as usize].clone(),
        s.units[E1 as usize].clone(),
        s.units[E2 as usize].clone(),
        s.units[E3 as usize].clone(),
    );
    s.apply(Command::Retreat { unit: P1 }).unwrap();
    assert_eq!(s.units[P2 as usize], p2, "escapee");
    assert_eq!(s.units[P3 as usize], p3, "casualty");
    assert_eq!(s.units[E1 as usize], e1, "enemy with a pending skip");
    assert_eq!(s.units[E2 as usize], e2);
    assert_eq!(s.units[E3 as usize], e3);
}

// --- Immediate edge exits ------------------------------------------------------------

#[test]
fn edge_players_leave_at_once_in_ascending_id_order_without_reactions_or_advancing() {
    // P1 active and interior; P2 finished its turn on an edge next to E1; P3 is
    // queued on an edge. Queue order differs from id order.
    let mut s = board(7, &[(P1, hex(-3, 2)), (P3, hex(-4, 2)), (E1, hex(-3, 3)), (E2, hex(3, -3))]);
    s.units[P2 as usize].alive = true;
    s.units[P2 as usize].pos = hex(-4, 3);
    s.units[P2 as usize].turn_started = true;
    s.units[P1 as usize].ap = 5;
    s.units[P1 as usize].fatigue = 12;
    let rng = s.rng.clone();
    let (hp2, ar2) = (s.units[P2 as usize].hp, s.units[P2 as usize].armour);
    let ev = s.apply(Command::Retreat { unit: P1 }).unwrap();
    assert_eq!(ev, vec![Event::RetreatOrdered { unit: P1 }, Event::Fled { unit: P2 }, Event::Fled { unit: P3 }]);
    assert_eq!(s.rng, rng, "no reactions, no draws");
    for id in [P2, P3] {
        let u = &s.units[id as usize];
        assert!(u.alive && u.fled && !u.is_present(), "P{} escaped alive", id + 1);
        assert!(u.retreating);
    }
    assert_eq!((s.units[P2 as usize].hp, s.units[P2 as usize].armour), (hp2, ar2));
    assert_eq!(s.queue, vec![P1, E1, E2], "P3 left the queue, nobody else moved");
    // The interior active unit keeps its turn untouched.
    let p1 = &s.units[P1 as usize];
    assert_eq!((p1.ap, p1.fatigue, p1.pos), (5, 12, hex(-3, 2)));
    assert_eq!(s.round, 1);
    assert!(s.outcome().is_none());
}

#[test]
fn an_escaped_issuer_advances_exactly_once_to_a_refreshed_next_unit() {
    let mut s = board(7, &[(P1, hex(-4, 3)), (E1, hex(3, -2)), (P2, hex(-3, 2))]);
    s.units[E1 as usize].fatigue = 20;
    s.units[E1 as usize].ap = 0;
    let ev = s.apply(Command::Retreat { unit: P1 }).unwrap();
    assert_eq!(ev, vec![Event::RetreatOrdered { unit: P1 }, Event::Fled { unit: P1 }, started(E1)]);
    assert_eq!(s.queue, vec![E1, P2]);
    assert_eq!((s.units[E1 as usize].ap, s.units[E1 as usize].fatigue), (9, 5), "refresh happened once");
}

#[test]
fn escaped_issuer_with_the_round_exhausted_starts_the_next_round() {
    let mut s = board(7, &[(P1, hex(-4, 3)), (P2, hex(-3, 2)), (E1, hex(3, -2))]);
    s.queue = vec![P1];
    let ev = s.apply(Command::Retreat { unit: P1 }).unwrap();
    assert_eq!(ev[..2], [Event::RetreatOrdered { unit: P1 }, Event::Fled { unit: P1 }]);
    assert_eq!(ev.iter().filter(|e| matches!(e, Event::RoundStarted { .. })).count(), 1);
    assert_eq!(s.round, 2);
    assert!(s.units[P2 as usize].retreating && s.units[P2 as usize].is_present());
}

#[test]
fn all_players_on_edges_end_the_battle_as_retreat_without_another_turn() {
    let mut s = board(7, &[(P1, hex(-4, 3)), (E1, hex(3, -2)), (P2, hex(-4, 2))]);
    let ev = s.apply(Command::Retreat { unit: P1 }).unwrap();
    assert_eq!(
        ev,
        vec![
            Event::RetreatOrdered { unit: P1 },
            Event::Fled { unit: P1 },
            Event::Fled { unit: P2 },
            Event::BattleEnded { outcome: Outcome::Retreat, reason: EndReason::Escape },
        ]
    );
    assert_eq!((s.outcome(), s.end_reason()), (Some(Outcome::Retreat), Some(EndReason::Escape)));
    assert_eq!(s.apply(Command::Withdraw { unit: P1 }), Err(RuleError::BattleOver));
    assert_eq!(s.apply(Command::EndTurn { unit: E1 }), Err(RuleError::BattleOver));
}

#[test]
fn interior_issuer_with_no_other_edge_player_changes_nothing_but_the_flag() {
    let mut s = board(7, &[(P1, hex(-3, 2)), (E1, hex(3, -2))]);
    let mut expected = s.clone();
    s.apply(Command::Retreat { unit: P1 }).unwrap();
    expected.units[P1 as usize].retreating = true;
    assert_eq!(s.units, expected.units);
    assert_eq!(s.queue, expected.queue);
    assert_eq!(s.round, expected.round);
}

// --- Command and preview rejection ------------------------------------------------------

#[test]
fn ordinary_commands_and_previews_are_rejected_for_a_retreating_actor_without_side_effects() {
    let mut s = board(7, &[(P1, hex(-3, 2)), (E1, hex(-2, 2)), (P2, hex(2, -3))]);
    retreating(&mut s, &[P1]);
    let before = snapshot(&s);
    let cmds = [
        Command::Move { unit: P1, to: hex(-3, 3) },
        Command::Move { unit: P1, to: hex(9, 9) },
        Command::Attack { attacker: P1, target: E1 },
        Command::Recover { unit: P1 },
        Command::Wait { unit: P1 },
        Command::EndTurn { unit: P1 },
        Command::EndRound { unit: P1 },
        Command::Retreat { unit: P1 },
    ];
    for cmd in cmds {
        assert_eq!(s.apply(cmd), Err(RuleError::RetreatingUnit), "{cmd:?}");
        assert_eq!(snapshot(&s), before, "{cmd:?}");
    }
    for to in [hex(-3, 3), hex(9, 9), hex(-2, 2)] {
        assert_eq!(s.preview_move(P1, to), Err(RuleError::RetreatingUnit));
    }
    // Retreating and Fleeing: still RetreatingUnit for ordinary commands.
    s.units[P1 as usize].morale = Morale::Fleeing;
    let before = snapshot(&s);
    assert_eq!(s.apply(Command::EndTurn { unit: P1 }), Err(RuleError::RetreatingUnit));
    assert_eq!(s.preview_move(P1, hex(-3, 3)), Err(RuleError::RetreatingUnit));
    assert_eq!(snapshot(&s), before);
}

#[test]
fn withdraw_requires_retreat_intent_and_keeps_older_error_precedence() {
    let mut s = board(7, &[(P1, hex(-3, 2)), (E1, hex(3, -2)), (P2, hex(-2, 2))]);
    let before = snapshot(&s);
    assert_eq!(s.apply(Command::Withdraw { unit: P1 }), Err(RuleError::NotRetreating));
    // Fleeing alone is not retreat intent.
    s.units[P1 as usize].morale = Morale::Fleeing;
    let fleeing = snapshot(&s);
    assert_eq!(s.apply(Command::Withdraw { unit: P1 }), Err(RuleError::NotRetreating));
    assert_eq!(snapshot(&s), fleeing);
    s.units[P1 as usize].morale = Morale::Steady;
    assert_eq!(snapshot(&s), before);
    // Not the active unit wins, also for a retreating non-active unit.
    retreating(&mut s, &[P2]);
    assert_eq!(s.apply(Command::Withdraw { unit: P2 }), Err(RuleError::NotActiveUnit));
    assert_eq!(s.apply(Command::Withdraw { unit: E1 }), Err(RuleError::NotActiveUnit));
}

#[test]
fn flee_without_retreat_intent_still_works_as_before() {
    let mut s = board(7, &[(P1, hex(-2, 2)), (E1, hex(3, -2))]);
    s.units[P1 as usize].morale = Morale::Fleeing;
    let ev = s.apply(Command::Flee { unit: P1 }).unwrap();
    assert_eq!(ev.len(), 1, "{ev:?}");
    assert!(!s.units[P1 as usize].retreating);
}

// --- Withdraw: shared one-step automatic movement -----------------------------------------

#[test]
fn withdraw_and_flee_take_the_same_step_with_the_same_costs() {
    let placed = [(P1, hex(-2, 2)), (E1, hex(3, -2)), (P2, hex(2, -3))];
    let mut withdrawing = board(7, &placed);
    retreating(&mut withdrawing, &[P1]);
    let mut fleeing = board(7, &placed);
    fleeing.units[P1 as usize].morale = Morale::Fleeing;

    let w = withdrawing.apply(Command::Withdraw { unit: P1 }).unwrap();
    let f = fleeing.apply(Command::Flee { unit: P1 }).unwrap();
    assert_eq!(w, f);
    assert!(matches!(w.as_slice(), [Event::Moved { path, .. }] if path.len() == 1));
    let (a, b) = (&withdrawing.units[P1 as usize], &fleeing.units[P1 as usize]);
    assert_eq!((a.pos, a.ap, a.fatigue), (b.pos, b.ap, b.fatigue));
    assert_eq!((a.ap, a.fatigue), (7, 2), "ordinary AP and fatigue costs");
    assert_eq!(withdrawing.queue, vec![P1, E1, P2], "still P1's turn");
    assert_eq!(withdrawing.rng, fleeing.rng);
}

#[test]
fn repeated_withdraws_reach_the_edge_escape_and_hand_the_turn_on() {
    let mut s = board(7, &[(P1, hex(-2, 2)), (E1, hex(3, -2)), (P2, hex(2, -3))]);
    retreating(&mut s, &[P1]);
    let mut events = Vec::new();
    for _ in 0..3 {
        if !s.units[P1 as usize].is_present() {
            break;
        }
        events.extend(s.apply(Command::Withdraw { unit: P1 }).unwrap());
    }
    let u = &s.units[P1 as usize];
    assert!(u.alive && u.fled);
    assert_eq!(events[events.len() - 2], Event::Fled { unit: P1 });
    assert_eq!(events[events.len() - 1], started(E1));
    assert_eq!(u.hp, u.stats.hp);
    assert_eq!(s.rng, Rng::new(7), "no reactions, no draws, no route randomness");
}

#[test]
fn withdraw_on_an_edge_escapes_even_without_ap() {
    let mut s = board(7, &[(P1, hex(-4, 3)), (E1, hex(3, -2)), (P2, hex(-2, 2))]);
    retreating(&mut s, &[P1]);
    s.units[P1 as usize].ap = 0;
    let ev = s.apply(Command::Withdraw { unit: P1 }).unwrap();
    assert_eq!(ev, vec![Event::Fled { unit: P1 }, started(E1)]);
}

#[test]
fn an_occupied_shortest_exit_is_routed_around() {
    let mut s = board(7, &[(P1, hex(-3, 2)), (P2, hex(-4, 2)), (P3, hex(-4, 3)), (E1, hex(3, -2))]);
    retreating(&mut s, &[P1]);
    let ev = s.apply(Command::Withdraw { unit: P1 }).unwrap();
    let Event::Moved { path, .. } = &ev[0] else { panic!("{ev:?}") };
    assert_eq!(path.len(), 1);
    let to = s.units[P1 as usize].pos;
    assert_eq!(path[0], to);
    assert!(to != hex(-4, 2) && to != hex(-4, 3));
    assert!(!is_edge(to), "the nearest free exits are two steps away");
}

#[test]
fn a_unit_with_no_free_route_ends_its_turn_without_moving() {
    // P1 at (-1,0): obstacles on two sides, the four open neighbours are occupied.
    let mut s = board(
        7,
        &[(P1, hex(-1, 0)), (E1, hex(0, -1)), (E2, hex(-1, -1)), (P2, hex(-2, 0)), (P3, hex(-2, 1))],
    );
    retreating(&mut s, &[P1]);
    s.units[E1 as usize].ap = 0;
    let rng = s.rng.clone();
    let ev = s.apply(Command::Withdraw { unit: P1 }).unwrap();
    assert_eq!(ev, vec![started(E1)]);
    assert_eq!(s.units[P1 as usize].pos, hex(-1, 0));
    assert_eq!(s.units[P1 as usize].ap, 9, "no step attempted, no cost");
    assert_eq!(s.queue, vec![E1, E2, P2, P3]);
    assert_eq!(s.rng, rng);
}

#[test]
fn insufficient_ap_or_fatigue_ends_the_turn_without_moving() {
    for (ap, fatigue) in [(1, 0), (9, 99), (0, 0)] {
        let mut s = board(7, &[(P1, hex(-2, 2)), (E1, hex(3, -2)), (P2, hex(2, -3))]);
        retreating(&mut s, &[P1]);
        s.units[P1 as usize].ap = ap;
        s.units[P1 as usize].fatigue = fatigue;
        s.units[E1 as usize].ap = 0;
        let ev = s.apply(Command::Withdraw { unit: P1 }).unwrap();
        assert_eq!(ev, vec![started(E1)], "ap {ap} fatigue {fatigue}");
        let u = &s.units[P1 as usize];
        assert_eq!((u.pos, u.ap, u.fatigue), (hex(-2, 2), ap, fatigue));
        assert_eq!(s.queue, vec![E1, P2]);
    }
}

// --- Withdraw: reactions ---------------------------------------------------------------------

/// P1 retreating two steps from the edge at (-2,2), next to E1 at (-1,2), with P2 as a second player.
fn engaged(seed: u64) -> BattleState {
    let mut s = board(seed, &[(P1, hex(-2, 2)), (E1, hex(-1, 2)), (P2, hex(-3, 1))]);
    retreating(&mut s, &[P1, P2]);
    s
}

#[test]
fn a_missed_reaction_lets_the_withdrawal_step_complete() {
    let chance = engaged(1).hit_chance(E1, P1);
    let mut s = engaged(seed_with_first_roll(chance, false));
    let ev = s.apply(Command::Withdraw { unit: P1 }).unwrap();
    assert!(matches!(ev[0], Event::Attacked { attacker: E1, target: P1, hit: false, .. }), "{ev:?}");
    assert!(matches!(&ev[1], Event::Moved { unit: P1, path } if path.len() == 1), "{ev:?}");
    assert_eq!(ev.len(), 2, "{ev:?}");
    let u = &s.units[P1 as usize];
    assert_ne!(u.pos, hex(-2, 2));
    assert_eq!((u.ap, u.fatigue), (7, 2 + 2), "step cost plus the miss's defender fatigue");
    assert_eq!(s.queue[0], P1);
}

#[test]
fn a_stopping_hit_keeps_the_unit_in_place_with_the_step_paid() {
    let chance = engaged(1).hit_chance(E1, P1);
    let mut s = engaged(seed_with_first_roll(chance, true));
    s.units[E1 as usize].stats.dmg_min = 1;
    s.units[E1 as usize].stats.dmg_max = 1;
    let ev = s.apply(Command::Withdraw { unit: P1 }).unwrap();
    assert!(matches!(ev[0], Event::Attacked { attacker: E1, target: P1, hit: true, .. }), "{ev:?}");
    assert!(!ev.iter().any(|e| matches!(e, Event::Moved { .. })), "{ev:?}");
    let u = &s.units[P1 as usize];
    assert_eq!(u.pos, hex(-2, 2));
    assert_eq!(u.ap, 7, "the attempted step was paid");
    assert_eq!(s.queue[0], P1, "the stopped unit keeps its turn");
    assert!(u.retreating);
}

#[test]
fn a_reaction_death_advances_exactly_once() {
    let mut s = engaged(0);
    s.units[P1 as usize].hp = 1;
    s.units[P1 as usize].armour = 0;
    s.units[E1 as usize].stats.melee_skill = 1000;
    s.units[E1 as usize].ap = 0;
    s.units[P2 as usize].stats.resolve = 100;
    s.rng = Rng::new(seed_with_first_roll(95, true));
    let ev = s.apply(Command::Withdraw { unit: P1 }).unwrap();
    assert!(ev.contains(&Event::Died { unit: P1 }), "{ev:?}");
    assert_eq!(ev.iter().filter(|e| matches!(e, Event::TurnStarted { .. })).count(), 1, "{ev:?}");
    assert_eq!(ev.iter().filter(|e| matches!(e, Event::RoundStarted { .. })).count(), 0);
    assert_eq!(s.queue, vec![E1, P2]);
    assert!(!s.units[P1 as usize].alive && !s.units[P1 as usize].fled);
    assert!(s.outcome().is_none());
}

#[test]
fn a_miss_then_arrival_on_the_edge_escapes_alive() {
    // P1 one step from three edge hexes, next to E1.
    let mut s = board(0, &[(P1, hex(-3, 3)), (E1, hex(-2, 3)), (P2, hex(-2, 2))]);
    retreating(&mut s, &[P1, P2]);
    s.rng = Rng::new(seed_with_first_roll(s.hit_chance(E1, P1), false));
    let ev = s.apply(Command::Withdraw { unit: P1 }).unwrap();
    assert!(matches!(ev[0], Event::Attacked { hit: false, .. }), "{ev:?}");
    assert!(matches!(&ev[1], Event::Moved { unit: P1, path } if path.len() == 1 && is_edge(path[0])), "{ev:?}");
    assert_eq!(ev[2], Event::Fled { unit: P1 });
    assert_eq!(ev[3], started(E1));
    assert_eq!(ev.len(), 4);
    let u = &s.units[P1 as usize];
    assert!(u.alive && u.fled);
    assert_eq!(u.hp, u.stats.hp);
}

#[test]
fn a_stopping_hit_one_step_from_the_edge_prevents_the_escape() {
    let mut s = board(0, &[(P1, hex(-3, 3)), (E1, hex(-2, 3)), (P2, hex(-2, 2))]);
    retreating(&mut s, &[P1, P2]);
    s.units[E1 as usize].stats.dmg_min = 1;
    s.units[E1 as usize].stats.dmg_max = 1;
    s.rng = Rng::new(seed_with_first_roll(s.hit_chance(E1, P1), true));
    let ev = s.apply(Command::Withdraw { unit: P1 }).unwrap();
    assert!(!ev.iter().any(|e| matches!(e, Event::Fled { .. })), "{ev:?}");
    assert!(s.units[P1 as usize].is_present());
    assert_eq!(s.queue[0], P1);
}

// --- Terminal outcomes -------------------------------------------------------------------

#[test]
fn survivors_of_a_costly_retreat_end_as_retreat_but_total_loss_is_defeat() {
    // P2 already escaped; P1 dies in a reaction => Retreat (a player escaped alive).
    let mut s = engaged(0);
    s.units[P2 as usize].fled = true;
    s.units[P1 as usize].hp = 1;
    s.units[P1 as usize].armour = 0;
    s.units[E1 as usize].stats.melee_skill = 1000;
    s.rng = Rng::new(seed_with_first_roll(95, true));
    let ev = s.apply(Command::Withdraw { unit: P1 }).unwrap();
    assert!(ev.contains(&Event::Died { unit: P1 }));
    assert_eq!(*ev.last().unwrap(), Event::BattleEnded { outcome: Outcome::Retreat, reason: EndReason::Escape });
    assert!(!ev.iter().any(|e| matches!(e, Event::TurnStarted { .. })), "no turn after the end: {ev:?}");

    // Nobody escaped => Defeat by elimination.
    let mut s = engaged(0);
    s.units[P2 as usize].alive = false;
    s.queue = vec![P1, E1];
    s.units[P1 as usize].hp = 1;
    s.units[P1 as usize].armour = 0;
    s.units[E1 as usize].stats.melee_skill = 1000;
    s.rng = Rng::new(seed_with_first_roll(95, true));
    let ev = s.apply(Command::Withdraw { unit: P1 }).unwrap();
    assert_eq!(*ev.last().unwrap(), Event::BattleEnded { outcome: Outcome::Defeat, reason: EndReason::Elimination });
    assert_eq!((s.outcome(), s.end_reason()), (Some(Outcome::Defeat), Some(EndReason::Elimination)));
}

// --- Persistence, morale and EndRound ---------------------------------------------------------

#[test]
fn retreat_intent_survives_round_boundaries() {
    // One AP per turn: the withdrawing players cannot reach an edge in round 1.
    let mut s = board(3, &[(P1, hex(-1, 0)), (P2, hex(-1, -1)), (E1, hex(3, -3))]);
    for id in [P1, P2] {
        s.units[id as usize].stats.ap_max = 1;
    }
    s.units[P1 as usize].ap = 1;
    s.units[P1 as usize].skip_turn = true; // would be passed over without the order
    s.units[P2 as usize].skip_turn = true;
    s.apply(Command::Retreat { unit: P1 }).unwrap();
    let mut guard = 0;
    while s.round < 3 && s.outcome().is_none() {
        guard += 1;
        assert!(guard < 200);
        let cmd = ai::plan_turn(&s)[0];
        s.apply(cmd).unwrap();
    }
    assert_eq!(s.round, 3, "two round boundaries passed");
    for id in [P1, P2] {
        let u = &s.units[id as usize];
        assert!(u.is_present() && u.retreating, "P{} keeps its intent", id + 1);
        assert!(!u.skip_turn);
    }
    assert!(!s.units[E1 as usize].retreating);
}

#[test]
fn successful_and_failed_self_rally_keep_the_withdrawal() {
    for resolve in [100, 0] {
        // P1 retreats with 1 AP and ends its turn; Fleeing P2 starts next, off the edge.
        let mut s = board(5, &[(P1, hex(-3, 2)), (P2, hex(-1, 2)), (E1, hex(3, -2))]);
        s.units[P1 as usize].ap = 1;
        s.units[P2 as usize].morale = Morale::Fleeing;
        s.units[P2 as usize].stats.resolve = resolve;
        s.apply(Command::Retreat { unit: P1 }).unwrap();
        assert!(s.units[P2 as usize].retreating);
        let ev = s.apply(Command::Withdraw { unit: P1 }).unwrap();
        assert!(ev.iter().any(|e| matches!(e, Event::SelfRallyChecked { unit: P2, .. })), "{ev:?}");
        let p2 = &s.units[P2 as usize];
        assert_eq!(p2.morale, if resolve == 100 { Morale::Breaking } else { Morale::Fleeing });
        assert!(p2.retreating, "resolve {resolve}");
        assert_eq!(s.active_unit().map(|u| u.id), Some(P2), "P2's turn started after P1's");
    }
}

#[test]
fn a_rallied_player_is_still_driven_by_withdraw_not_ordinary_policy() {
    let mut s = board(5, &[(P2, hex(-1, 2)), (E1, hex(3, -2))]);
    s.units[P2 as usize].morale = Morale::Breaking;
    retreating(&mut s, &[P2]);
    assert_eq!(ai::plan_turn(&s), vec![Command::Withdraw { unit: P2 }]);
    assert_eq!(s.apply(Command::EndTurn { unit: P2 }), Err(RuleError::RetreatingUnit));
    assert!(s.apply(Command::Withdraw { unit: P2 }).is_ok());
}

#[test]
fn a_withdrawing_unit_whose_morale_collapses_still_gets_withdraw_first() {
    // P1 dies in a reaction; retreating Breaking P2 (Resolve 0) fails its ally-death check.
    let mut s = engaged(0);
    s.units[P2 as usize].morale = Morale::Breaking;
    s.units[P2 as usize].stats.resolve = 0;
    s.units[P1 as usize].hp = 1;
    s.units[P1 as usize].armour = 0;
    s.units[E1 as usize].stats.melee_skill = 1000;
    s.units[E1 as usize].ap = 0;
    s.queue = vec![P1, P2, E1];
    s.rng = Rng::new(seed_with_first_roll(95, true));
    s.apply(Command::Withdraw { unit: P1 }).unwrap();
    let p2 = &s.units[P2 as usize];
    assert_eq!(p2.morale, Morale::Fleeing);
    assert!(p2.retreating);
    assert_eq!(s.active_unit().map(|u| u.id), Some(P2));
    assert_eq!(ai::plan_turn(&s), vec![Command::Withdraw { unit: P2 }], "retreat before morale-fleeing policy");
    // Retreating and Fleeing: still eligible for Withdraw.
    assert!(s.apply(Command::Withdraw { unit: P2 }).is_ok());
}

#[test]
fn plan_turn_withdraws_before_attacking() {
    let mut s = board(7, &[(P1, hex(-3, 2)), (E1, hex(-2, 2)), (P2, hex(-3, 1))]);
    assert_eq!(ai::plan_turn(&s), vec![Command::Attack { attacker: P1, target: E1 }]);
    retreating(&mut s, &[P1]);
    assert_eq!(ai::plan_turn(&s), vec![Command::Withdraw { unit: P1 }]);
}

#[test]
fn end_round_does_not_mark_withdrawing_units_and_a_pending_skip_cannot_suppress_them() {
    let mut s = board(7, &[(P1, hex(-3, 2)), (P2, hex(-3, 1)), (P3, hex(-2, 2)), (E1, hex(3, -2))]);
    retreating(&mut s, &[P2]);
    s.units[P2 as usize].skip_turn = true; // stale pending skip, must not be honoured
    let ev = s.apply(Command::EndRound { unit: P1 }).unwrap();
    assert!(s.units[P3 as usize].skip_turn, "ordinary queued players are marked");
    // P2's turn starts (refresh) and it stays active instead of being passed over.
    assert_eq!(ev, vec![started(P2)]);
    assert_eq!(s.active_unit().map(|u| u.id), Some(P2));
    assert_eq!(s.units[P2 as usize].ap, 9);

    // A clean retreating unit without a stale skip is never marked either.
    let mut s = board(7, &[(P1, hex(-3, 2)), (P2, hex(-3, 1)), (E1, hex(3, -2))]);
    retreating(&mut s, &[P2]);
    s.apply(Command::EndRound { unit: P1 }).unwrap();
    assert!(!s.units[P2 as usize].skip_turn);
    assert_eq!(s.active_unit().map(|u| u.id), Some(P2));
}

#[test]
fn end_round_turn_start_processing_stays_intact_for_withdrawing_units() {
    // Refresh and fatigue regeneration still happen when the turn starts.
    let mut s = board(7, &[(P1, hex(-3, 2)), (P2, hex(-3, 1)), (E1, hex(3, -2))]);
    retreating(&mut s, &[P2]);
    s.units[P2 as usize].fatigue = 40;
    s.units[P2 as usize].ap = 0;
    s.apply(Command::EndRound { unit: P1 }).unwrap();
    let p2 = &s.units[P2 as usize];
    assert_eq!((p2.ap, p2.fatigue), (9, 25));
}

// --- Replay, hash --------------------------------------------------------------------------------

fn retreat_replay(seed: u64) -> (Option<Outcome>, Option<EndReason>, u32, u64, usize, Vec<Event>) {
    let mut s = BattleState::new(seed);
    let mut log = s.apply(Command::Retreat { unit: P1 }).unwrap();
    let mut commands = 0;
    while s.outcome().is_none() {
        commands += 1;
        assert!(commands < 3000, "seed {seed}: replay is unbounded");
        let plan = ai::plan_turn(&s);
        assert_eq!(plan.len(), 1);
        log.extend(s.apply(plan[0]).unwrap_or_else(|e| panic!("seed {seed}: {:?} -> {e:?}", plan[0])));
    }
    (s.outcome(), s.end_reason(), s.round, s.state_hash(), commands, log)
}

#[test]
fn automatic_replay_after_a_retreat_order_is_deterministic_and_bounded() {
    for seed in 1..=12 {
        let a = retreat_replay(seed);
        let b = retreat_replay(seed);
        assert_eq!(a, b, "seed {seed}");
        assert!(a.0.is_some());
        // Exactly one terminal event, and it is the last one.
        let ended: Vec<usize> =
            a.5.iter().enumerate().filter(|(_, e)| matches!(e, Event::BattleEnded { .. })).map(|(i, _)| i).collect();
        assert_eq!(ended, vec![a.5.len() - 1], "seed {seed}");
    }
}

#[test]
fn retreating_flag_has_its_own_hash_bit_and_defaults_to_the_old_hash() {
    let base = BattleState::new(1);
    assert!(base.units.iter().all(|u| !u.retreating));
    let mut flagged = base.clone();
    flagged.units[P1 as usize].retreating = true;
    assert_ne!(flagged.state_hash(), base.state_hash());
    let mut hashes = vec![base.state_hash(), flagged.state_hash()];
    for edit in [
        |s: &mut BattleState| s.units[0].skip_turn = true,
        |s: &mut BattleState| s.units[0].fled = true,
        |s: &mut BattleState| s.units[0].waited = true,
        |s: &mut BattleState| s.units[0].morale = Morale::Fleeing,
        |s: &mut BattleState| s.units[1].retreating = true,
    ] {
        let mut s = base.clone();
        edit(&mut s);
        hashes.push(s.state_hash());
    }
    let mut unique = hashes.clone();
    unique.sort_unstable();
    unique.dedup();
    assert_eq!(unique.len(), hashes.len(), "{hashes:x?}");
    // Setting and clearing the flag restores the old hash.
    flagged.units[P1 as usize].retreating = false;
    assert_eq!(flagged.state_hash(), base.state_hash());
}

#[test]
fn a_new_battle_resets_retreat_intent_through_unit_new() {
    let mut s = BattleState::new(1);
    s.apply(Command::Retreat { unit: P1 }).unwrap();
    assert!(s.units.iter().any(|u| u.retreating));
    let fresh = BattleState::new(1);
    assert!(fresh.units.iter().all(|u| !u.retreating));
    assert_eq!(fresh.state_hash(), BattleState::new(1).state_hash());
}
