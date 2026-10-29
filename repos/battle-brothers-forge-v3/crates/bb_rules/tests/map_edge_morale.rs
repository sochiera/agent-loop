//! Movement-triggered map-edge morale penalty (UC-COMBAT-048). The catalog states
//! that moving a non-fleeing brother onto the tactical edge forces morale toward
//! Breaking; the direct assignment to Breaking, the application to both sides and
//! the scheduling (at actual arrival, after the `Moved` segment, before later
//! departure reactions) are an APPROXIMATION without original-runtime
//! confirmation. Fixtures clear the stock scenario (`alive = false`) and place only
//! the units they need; reaction seeds are found by mirroring the SplitMix64 stream.

use bb_rules::hex::{hex, map_hexes, Hex};
use bb_rules::rules::{
    ATTACK_FATIGUE, DEFENDER_HIT_FATIGUE_GAIN, DEFENDER_MISS_FATIGUE_GAIN, MOVE_AP, MOVE_FATIGUE, MoveThreat,
};
use bb_rules::*;

const P1: u32 = 0;
const P2: u32 = 1;
const E1: u32 = 3;
const E2: u32 = 4;

/// One legal (edge hex, adjacent inner-ring hex) pair on each map side plus all six corners.
const EDGE_PAIRS: [(Hex, Hex); 12] = [
    (hex(4, -2), hex(3, -2)),
    (hex(-4, 2), hex(-3, 2)),
    (hex(-2, 4), hex(-2, 3)),
    (hex(2, -4), hex(2, -3)),
    (hex(-2, -2), hex(-1, -2)),
    (hex(2, 2), hex(1, 2)),
    (hex(4, 0), hex(3, 0)),
    (hex(4, -4), hex(3, -3)),
    (hex(0, 4), hex(0, 3)),
    (hex(-4, 4), hex(-3, 3)),
    (hex(-4, 0), hex(-3, 0)),
    (hex(0, -4), hex(0, -3)),
];

const ELIGIBLE: [Morale; 3] = [Morale::Confident, Morale::Steady, Morale::Wavering];

/// Only `placed` units are present; the queue follows the given order, so the
/// first entry is the active unit (9 AP, no fatigue, turn started).
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

/// Present units stand on distinct, in-map, passable hexes within their Stats maxima.
fn assert_valid_fixture(state: &BattleState) {
    let present: Vec<&Unit> = state.units.iter().filter(|u| u.is_present()).collect();
    for (i, u) in present.iter().enumerate() {
        assert!(u.pos.in_map() && !u.pos.is_obstacle(), "unit {} on invalid hex {:?}", u.id, u.pos);
        assert!(u.hp <= u.stats.hp && u.armour <= u.stats.armour, "unit {} exceeds its maxima", u.id);
        assert!(present[..i].iter().all(|o| o.pos != u.pos), "unit {} shares a hex", u.id);
    }
}

/// A passable hex at distance 3 or more from both `a` and `b`, for the idle opponent.
fn far_from(a: Hex, b: Hex) -> Hex {
    map_hexes().into_iter().find(|h| !h.is_obstacle() && h.distance(a) >= 3 && h.distance(b) >= 3).unwrap()
}

fn first_draw(seed: u64) -> u32 {
    (Rng::new(seed).next_u64() % 100) as u32
}

/// First seed whose first roll is below (`hit`) or at/above `chance`.
fn seed_with_first_roll(chance: u32, hit: bool) -> u64 {
    (0..10_000u64).find(|&s| (first_draw(s) < chance) == hit).unwrap()
}

fn snapshot(s: &BattleState) -> (u64, Rng, Vec<u32>, Vec<Morale>) {
    (s.state_hash(), s.rng.clone(), s.queue.clone(), s.units.iter().map(|u| u.morale).collect())
}

fn moved(unit: u32, path: &[Hex]) -> Event {
    Event::Moved { unit, path: path.to_vec() }
}

fn edge_changed(unit: u32, at: Hex, before: Morale) -> Event {
    Event::EdgeMoraleChanged { unit, at, before, after: Morale::Breaking }
}

fn has_edge_event(ev: &[Event]) -> bool {
    ev.iter().any(|e| matches!(e, Event::EdgeMoraleChanged { .. }))
}

// --- Single-step arrival ----------------------------------------------------------

#[test]
fn arrival_on_every_side_and_corner_drops_eligible_states_to_breaking_for_both_factions_and_any_resolve() {
    for (edge, inner) in EDGE_PAIRS {
        let far = far_from(edge, inner);
        for (mover, other) in [(P1, E1), (E1, P1)] {
            for resolve in [0, 100] {
                for before in [Morale::Confident, Morale::Steady, Morale::Wavering, Morale::Breaking] {
                    let mut s = board(7, &[(mover, inner), (other, far)]);
                    s.units[mover as usize].stats.resolve = resolve;
                    s.units[mover as usize].morale = before;
                    let rng = s.rng.clone();
                    let queue = s.queue.clone();
                    let ctx = format!("{mover} at {edge:?} from {before:?}, resolve {resolve}");

                    let ev = s.apply(Command::Move { unit: mover, to: edge }).unwrap();
                    let mut expected = vec![moved(mover, &[edge])];
                    if before != Morale::Breaking {
                        expected.push(edge_changed(mover, edge, before));
                    }
                    assert_eq!(ev, expected, "{ctx}");

                    let u = &s.units[mover as usize];
                    assert_eq!(u.morale, Morale::Breaking, "{ctx}");
                    assert_eq!(u.stats.resolve, resolve, "{ctx}: base Resolve is never rewritten");
                    assert!(u.is_present() && u.pos == edge, "{ctx}");
                    assert_eq!((u.ap, u.fatigue), (9 - MOVE_AP, MOVE_FATIGUE), "{ctx}: ordinary costs only");
                    assert_eq!(s.rng, rng, "{ctx}: no RNG");
                    assert_eq!(s.queue, queue, "{ctx}: queue position kept");
                    assert_eq!(s.outcome(), None, "{ctx}: reaching the edge does not end the battle");

                    // Nothing but the morale differs from the same move made at Breaking.
                    let mut reference = board(7, &[(mover, inner), (other, far)]);
                    reference.units[mover as usize].stats.resolve = resolve;
                    reference.units[mover as usize].morale = Morale::Breaking;
                    reference.apply(Command::Move { unit: mover, to: edge }).unwrap();
                    assert_eq!(s.state_hash(), reference.state_hash(), "{ctx}");
                }
            }
        }
    }
}

#[test]
fn the_unit_stays_controllable_at_breaking_with_its_remaining_budget() {
    let (edge, inner) = EDGE_PAIRS[0];
    let mut s = board(3, &[(P1, inner), (E1, far_from(edge, inner))]);
    s.apply(Command::Move { unit: P1, to: edge }).unwrap();
    assert_eq!(s.active_unit().map(|u| u.id), Some(P1));
    assert_eq!(s.units[P1 as usize].morale, Morale::Breaking);
    // Ordinary commands remain available: previews, moves and the turn end.
    assert!(s.preview_move(P1, inner).is_ok());
    s.apply(Command::Move { unit: P1, to: inner }).unwrap();
    assert_eq!(s.units[P1 as usize].ap, 9 - 2 * MOVE_AP);
    let ev = s.apply(Command::EndTurn { unit: P1 }).unwrap();
    assert!(!ev.iter().any(|e| matches!(e, Event::Fled { .. } | Event::BattleEnded { .. })), "{ev:?}");
    assert!(s.units[P1 as usize].is_present());
}

// --- Multi-step routes and later reactions ----------------------------------------

/// P1 walks (4,-3) -> (4,-2) -> (4,-1) along the edge; E1 on (3,-1) touches only the
/// middle hex, so its reaction happens when P1 departs the edge hex it just reached.
/// E1 hits at 70 - defense; P1's defense of 50 is 50 at Steady and 40 at Breaking.
const A: Hex = hex(4, -3);
const B: Hex = hex(4, -2);
const C: Hex = hex(4, -1);
const REACTOR: Hex = hex(3, -1);

fn edge_route_board(seed: u64) -> BattleState {
    let mut s = board(seed, &[(P1, A), (E1, REACTOR)]);
    s.units[P1 as usize].morale = Morale::Steady;
    let p = &mut s.units[P1 as usize];
    p.stats.defense = 50;
    p.stats.hp = 100;
    p.hp = 100;
    p.stats.armour = 40;
    p.armour = 40;
    let e = &mut s.units[E1 as usize];
    e.stats.melee_skill = 70;
    e.stats.dmg_min = 10;
    e.stats.dmg_max = 10;
    s
}

#[test]
fn edge_arrival_precedes_the_next_departure_reaction_which_uses_breaking_stats() {
    let s = edge_route_board(0);
    let preview = s.preview_move(P1, C).unwrap();
    assert_eq!(preview.path, vec![B, C]);
    assert_eq!(preview.first_threat, Some(MoveThreat { departure: B, controllers: vec![E1] }));
    let (steady_chance, breaking_chance) = (20, 30);
    assert_eq!(s.hit_chance(E1, P1), steady_chance, "Steady defense 50 against skill 70");

    // Miss: the walk continues to C with the single shared tail segment.
    let miss = seed_with_first_roll(breaking_chance, false);
    let mut s = edge_route_board(miss);
    let ev = s.apply(Command::Move { unit: P1, to: C }).unwrap();
    let roll = first_draw(miss);
    assert_eq!(
        ev,
        vec![
            moved(P1, &[B]),
            edge_changed(P1, B, Morale::Steady),
            Event::Attacked { attacker: E1, target: P1, hit: false, roll, chance: breaking_chance, dmg: 0, absorbed: 0, hp_loss: 0 },
            moved(P1, &[C]),
        ]
    );
    let p = &s.units[P1 as usize];
    assert_eq!((p.pos, p.morale, p.ap), (C, Morale::Breaking, 9 - 2 * MOVE_AP));
    assert_eq!(p.fatigue, 2 * MOVE_FATIGUE + DEFENDER_MISS_FATIGUE_GAIN);

    // A roll between the Steady and Breaking chances misses at Steady but hits at Breaking.
    let between = (0..10_000u64).find(|&sd| (steady_chance..breaking_chance).contains(&first_draw(sd))).unwrap();
    let mut s = edge_route_board(between);
    let ev = s.apply(Command::Move { unit: P1, to: C }).unwrap();
    assert!(matches!(ev[2], Event::Attacked { hit: true, chance: 30, .. }), "{ev:?}");

    // Hit: the walk stops on the edge hex; the penalty stays and the step is still paid.
    let mut s = edge_route_board(seed_with_first_roll(steady_chance, true));
    let ev = s.apply(Command::Move { unit: P1, to: C }).unwrap();
    assert_eq!(&ev[..2], &[moved(P1, &[B]), edge_changed(P1, B, Morale::Steady)]);
    assert!(matches!(ev[2], Event::Attacked { hit: true, chance: 30, .. }), "{ev:?}");
    assert_eq!(ev.len(), 3, "stopped on the edge hex: {ev:?}");
    let p = &s.units[P1 as usize];
    assert_eq!((p.pos, p.morale, p.ap), (B, Morale::Breaking, 9 - 2 * MOVE_AP));
    assert_eq!(p.fatigue, 2 * MOVE_FATIGUE + DEFENDER_HIT_FATIGUE_GAIN);
}

#[test]
fn a_mover_killed_after_arriving_keeps_the_event_and_the_dead_unit_is_not_extracted() {
    let mut s = edge_route_board(seed_with_first_roll(20, true));
    let p = &mut s.units[P1 as usize];
    p.hp = 1;
    p.armour = 0;
    s.units[P2 as usize].alive = true;
    s.units[P2 as usize].pos = far_from(A, B);
    s.queue.push(P2);
    let ev = s.apply(Command::Move { unit: P1, to: C }).unwrap();
    assert_eq!(&ev[..2], &[moved(P1, &[B]), edge_changed(P1, B, Morale::Steady)]);
    assert!(matches!(ev[2], Event::Attacked { hit: true, .. }));
    assert_eq!(ev[3], Event::Died { unit: P1 });
    assert!(!ev.iter().any(|e| matches!(e, Event::Fled { .. })));
    assert!(!s.units[P1 as usize].fled);
}

// --- Failed arrival ----------------------------------------------------------------

/// P1 on the inner hex (3,-2) heads for the edge hex (4,-2) with E1 adjacent to the start.
fn blocked_board(seed: u64) -> BattleState {
    let mut s = board(seed, &[(P1, hex(3, -2)), (E1, hex(2, -1)), (P2, hex(-3, 3))]);
    s.units[E1 as usize].stats.melee_skill = 1000;
    s
}

#[test]
fn a_stopped_mover_gets_no_penalty_for_the_edge_hex_it_did_not_reach() {
    let seed = seed_with_first_roll(95, true);
    let mut s = blocked_board(seed);
    s.units[E1 as usize].stats.dmg_min = 10;
    s.units[E1 as usize].stats.dmg_max = 10;
    s.units[P1 as usize].stats.armour = 40;
    s.units[P1 as usize].armour = 40;
    let ev = s.apply(Command::Move { unit: P1, to: hex(4, -2) }).unwrap();
    assert!(matches!(ev[0], Event::Attacked { hit: true, .. }), "{ev:?}");
    assert!(!has_edge_event(&ev) && !ev.iter().any(|e| matches!(e, Event::Moved { .. })), "{ev:?}");
    assert_eq!((s.units[P1 as usize].pos, s.units[P1 as usize].morale), (hex(3, -2), Morale::Steady));
}

#[test]
fn a_killed_mover_gets_no_penalty_for_the_edge_hex_it_did_not_reach() {
    let mut s = blocked_board(seed_with_first_roll(95, true));
    let p = &mut s.units[P1 as usize];
    p.hp = 1;
    p.armour = 0;
    let ev = s.apply(Command::Move { unit: P1, to: hex(4, -2) }).unwrap();
    assert!(ev.contains(&Event::Died { unit: P1 }), "{ev:?}");
    assert!(!has_edge_event(&ev) && !ev.iter().any(|e| matches!(e, Event::Moved { .. })), "{ev:?}");
    assert_eq!(s.units[P1 as usize].morale, Morale::Steady);
    assert_eq!(s.units[P1 as usize].pos, hex(3, -2));
}

// --- Rejections and previews -------------------------------------------------------

#[test]
fn rejected_moves_and_previews_mutate_neither_morale_nor_rng() {
    let (edge, inner) = EDGE_PAIRS[0];
    let far = far_from(edge, inner);

    let low_ap = {
        let mut s = board(5, &[(P1, inner), (E1, far)]);
        s.units[P1 as usize].ap = 1;
        s
    };
    let tired = {
        let mut s = board(5, &[(P1, inner), (E1, far)]);
        s.units[P1 as usize].fatigue = s.units[P1 as usize].stats.fatigue_max - 1;
        s
    };
    let over = {
        let mut s = board(5, &[(P1, inner), (E1, far)]);
        s.outcome = Some(Outcome::Defeat);
        s
    };
    let cases: [(BattleState, u32, Hex, RuleError); 6] = [
        (low_ap, P1, edge, RuleError::NotEnoughAp),
        (tired, P1, edge, RuleError::TooFatigued),
        (over, P1, edge, RuleError::BattleOver),
        (board(5, &[(P1, inner), (E1, far)]), E1, far, RuleError::NotActiveUnit),
        (board(5, &[(P1, inner), (E1, edge)]), P1, edge, RuleError::Unreachable),
        (board(5, &[(P1, inner), (E1, far)]), P1, hex(0, 0), RuleError::Unreachable),
    ];
    for (mut s, unit, to, err) in cases {
        let before = snapshot(&s);
        assert_eq!(s.preview_move(unit, to).unwrap_err(), err);
        assert_eq!(s.apply(Command::Move { unit, to }).unwrap_err(), err);
        assert_eq!(snapshot(&s), before, "{err:?}");
    }

    // A fleeing or retreating mover cannot take an ordinary move, so it never gets the penalty.
    for (fleeing, retreating, err) in [(true, false, RuleError::FleeingUnit), (false, true, RuleError::RetreatingUnit)] {
        let mut s = board(5, &[(P1, inner), (E1, far)]);
        if fleeing {
            s.units[P1 as usize].morale = Morale::Fleeing;
        }
        s.units[P1 as usize].retreating = retreating;
        let before = snapshot(&s);
        assert_eq!(s.preview_move(P1, edge).unwrap_err(), err);
        assert_eq!(s.apply(Command::Move { unit: P1, to: edge }).unwrap_err(), err);
        assert_eq!(snapshot(&s), before);
    }

    // A successful preview onto the edge is pure.
    let s = board(5, &[(P1, inner), (E1, far)]);
    let before = snapshot(&s);
    let preview = s.preview_move(P1, edge).unwrap();
    assert_eq!(preview.path, vec![edge]);
    assert_eq!(snapshot(&s), before);
}

// --- Triggers that must not fire ---------------------------------------------------

#[test]
fn initial_edge_placement_turn_refresh_and_non_movement_actions_do_not_trigger_the_penalty() {
    // The stock scenario has E2 on the edge from the start.
    let fresh = BattleState::new(1);
    assert_eq!(fresh.units[E2 as usize].pos, hex(4, -3));
    assert!(fresh.units.iter().all(|u| u.morale == Morale::Steady));

    // P1 acts first, then E1 on the edge starts its turn (refresh) and acts.
    let mut s = board(2, &[(P1, hex(1, 2)), (E1, hex(4, -2)), (P2, hex(-3, 3))]);
    s.units[E1 as usize].turn_started = false;
    s.units[E1 as usize].fatigue = 30;
    let ev = s.apply(Command::EndTurn { unit: P1 }).unwrap();
    assert_eq!(ev, vec![Event::TurnStarted { unit: E1 }]);
    assert_eq!(s.units[E1 as usize].morale, Morale::Steady);
    let mut recovered = s.clone();
    recovered.units[E1 as usize].ap = 9;
    let ev = recovered.apply(Command::Recover { unit: E1 }).unwrap();
    assert!(!has_edge_event(&ev), "Recover: {ev:?}");
    assert_eq!(recovered.units[E1 as usize].morale, Morale::Steady);

    let mut waited = s.clone();
    let queue_before_wait = waited.queue.clone();
    let ev = waited.apply(Command::Wait { unit: E1 }).unwrap();
    assert!(!has_edge_event(&ev), "Wait: {ev:?}");
    assert_eq!(waited.units[E1 as usize].morale, Morale::Steady);
    assert_ne!(waited.queue, queue_before_wait);

    // Attacks from and against an edge hex, and EndRound for a player on the edge.
    let mut s = board(9, &[(P1, hex(4, -2)), (E1, hex(3, -2)), (P2, hex(-3, 3))]);
    s.units[E1 as usize].stats.hp = 100;
    s.units[E1 as usize].hp = 100;
    s.units[E1 as usize].stats.armour = 40;
    s.units[E1 as usize].armour = 40;
    let ev = s.apply(Command::Attack { attacker: P1, target: E1 }).unwrap();
    assert!(!has_edge_event(&ev), "{ev:?}");
    assert_eq!(s.units[P1 as usize].fatigue, ATTACK_FATIGUE);
    assert_eq!(s.units[P1 as usize].morale, Morale::Steady);
    let ev = s.apply(Command::EndRound { unit: P1 }).unwrap();
    assert!(!has_edge_event(&ev), "{ev:?}");
    assert!(s.units.iter().all(|u| u.morale == Morale::Steady));
}

#[test]
fn moving_between_edge_hexes_triggers_once_and_leaving_the_edge_does_not_restore_morale() {
    // Edge to edge from a Steady start: the penalty applies at the first arrival only.
    let mut s = board(4, &[(P1, A), (E1, far_from(A, C))]);
    assert_eq!(s.preview_move(P1, C).unwrap().path, vec![B, C]);
    let ev = s.apply(Command::Move { unit: P1, to: C }).unwrap();
    assert_eq!(ev, vec![moved(P1, &[B]), edge_changed(P1, B, Morale::Steady), moved(P1, &[C])]);
    assert_eq!(s.units[P1 as usize].morale, Morale::Breaking);

    // Repeated edge movement while Breaking changes nothing and emits nothing.
    let ev = s.apply(Command::Move { unit: P1, to: B }).unwrap();
    assert_eq!(ev, vec![moved(P1, &[B])]);

    // Back into the interior: no event and no recovery.
    let rng = s.rng.clone();
    let ev = s.apply(Command::Move { unit: P1, to: hex(3, -2) }).unwrap();
    assert_eq!(ev, vec![moved(P1, &[hex(3, -2)])]);
    assert_eq!(s.units[P1 as usize].morale, Morale::Breaking);
    assert_eq!(s.rng, rng);
}

// --- Automatic movement stays exempt -----------------------------------------------

#[test]
fn flee_and_withdraw_keep_their_immediate_edge_escape_without_the_penalty() {
    let (edge, inner) = EDGE_PAIRS[0];
    let far = far_from(edge, inner);

    let mut s = board(6, &[(P1, inner), (E1, far)]);
    s.units[P1 as usize].morale = Morale::Fleeing;
    let ev = s.apply(Command::Flee { unit: P1 }).unwrap();
    assert!(matches!(ev[0], Event::Moved { unit: P1, .. }), "{ev:?}");
    assert!(ev.contains(&Event::Fled { unit: P1 }), "{ev:?}");
    assert!(!has_edge_event(&ev));
    assert_eq!(s.units[P1 as usize].morale, Morale::Fleeing);
    assert!(s.units[P1 as usize].fled);

    for before in ELIGIBLE {
        let mut s = board(6, &[(P1, inner), (E1, far)]);
        s.units[P1 as usize].retreating = true;
        s.units[P1 as usize].morale = before;
        let ev = s.apply(Command::Withdraw { unit: P1 }).unwrap();
        assert!(matches!(ev[0], Event::Moved { unit: P1, .. }), "{ev:?}");
        assert!(ev.contains(&Event::Fled { unit: P1 }), "{ev:?}");
        assert!(!has_edge_event(&ev), "{before:?}: {ev:?}");
        assert_eq!(s.units[P1 as usize].morale, before);
    }

    // Already on the edge: an immediate escape, with no cost and no morale change.
    let mut s = board(6, &[(P1, edge), (E1, far), (P2, far_from(far, edge))]);
    s.units[P1 as usize].retreating = true;
    let ev = s.apply(Command::Withdraw { unit: P1 }).unwrap();
    assert_eq!(ev[0], Event::Fled { unit: P1 });
    assert!(!has_edge_event(&ev), "{ev:?}");
    assert_eq!(s.outcome(), None);
    assert_eq!(s.units[P1 as usize].morale, Morale::Steady);
}

// --- Preview agreement ---------------------------------------------------------------

#[test]
fn preview_first_edge_penalty_agrees_with_the_executed_edge_event_on_every_side_and_corner() {
    for (edge, inner) in EDGE_PAIRS {
        let far = far_from(edge, inner);
        for (mover, other) in [(P1, E1), (E1, P1)] {
            for before in ELIGIBLE.into_iter().chain([Morale::Breaking]) {
                let mut s = board(7, &[(mover, inner), (other, far)]);
                s.units[mover as usize].morale = before;
                let p = s.preview_move(mover, edge).unwrap();
                let ev = s.clone().apply(Command::Move { unit: mover, to: edge }).unwrap();
                let fired = ev.iter().find_map(|e| match e {
                    Event::EdgeMoraleChanged { at, .. } => Some(*at),
                    _ => None,
                });
                assert_eq!(p.first_edge_penalty, fired, "{mover} {before:?} at {edge:?}");
            }
        }
    }
}

#[test]
fn a_reaction_before_the_edge_does_not_hide_the_preview_and_the_edge_is_the_first_arrival() {
    let s = edge_route_board(0);
    let p = s.preview_move(P1, C).unwrap();
    assert_eq!(p.first_edge_penalty, Some(B), "B is reached first; C is not reported");
    // The blocked fixtures never reach the edge, yet still preview it conditionally.
    let s = blocked_board(1);
    let p = s.preview_move(P1, hex(4, -2)).unwrap();
    assert_eq!(p.first_threat.map(|t| t.departure), Some(hex(3, -2)));
    assert_eq!(p.first_edge_penalty, Some(hex(4, -2)));
}
