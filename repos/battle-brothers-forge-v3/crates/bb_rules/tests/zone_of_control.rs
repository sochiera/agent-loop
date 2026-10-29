//! Basic melee zones of control and opportunity attacks (APPROXIMATION of
//! UC-COMBAT-007/008). Fixtures park every unit far away, then place the actors
//! on the obstacle-free lane `r = -2`. All stats start at skill 0 / defense 0
//! (5% base chance); tests raise a controller's skill to 200 (95%) to force a hit.
//! SplitMix64 draws for seed 1 are 65, 19, 90, 35, 61, 48 (mod 100), mirrored
//! below with `draws` so expectations stay explicit.

use bb_rules::hex::{bfs_path, hex, map_hexes};
use bb_rules::rules::{LAST_ROUND, MOVE_AP, MOVE_FATIGUE, soak};
use bb_rules::*;

const PARK_PLAYERS: [Hex; 3] = [hex(-4, 2), hex(-4, 3), hex(-4, 4)];
const PARK_ENEMIES: [Hex; 3] = [hex(4, -4), hex(4, -3), hex(4, -2)];

const P1: u32 = 0;
const P2: u32 = 1;
const P3: u32 = 2;
const E1: u32 = 3;
const E2: u32 = 4;
const E3: u32 = 5;

/// All units parked, stats zeroed, `active` first in the queue with full AP.
fn board(seed: u64, active: u32, placements: &[(u32, Hex)]) -> BattleState {
    let mut s = BattleState::new(seed);
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

fn always_hit(s: &mut BattleState, id: u32) {
    s.units[id as usize].stats.melee_skill = 200;
}

fn kill(s: &mut BattleState, ids: &[u32]) {
    for &id in ids {
        s.units[id as usize].alive = false;
        s.queue.retain(|&q| q != id);
    }
}

fn draws(seed: u64, n: usize) -> Vec<u64> {
    let mut r = Rng::new(seed);
    (0..n).map(|_| r.next_u64()).collect()
}

fn rng_after(seed: u64, n: usize) -> Rng {
    let mut r = Rng::new(seed);
    for _ in 0..n {
        r.next_u64();
    }
    r
}

/// (attacker, target, hit, roll, chance) of every Attacked event, in order.
fn attacks(ev: &[Event]) -> Vec<(u32, u32, bool, u32, u32)> {
    ev.iter()
        .filter_map(|e| match e {
            Event::Attacked { attacker, target, hit, roll, chance, .. } => Some((*attacker, *target, *hit, *roll, *chance)),
            _ => None,
        })
        .collect()
}

fn assert_no_empty_moves(ev: &[Event]) {
    for e in ev {
        if let Event::Moved { path, .. } = e {
            assert!(!path.is_empty(), "Moved must never carry an empty path: {ev:?}");
        }
    }
}

fn ap_fatigue(s: &BattleState, id: u32) -> (u32, u32) {
    (s.units[id as usize].ap, s.units[id as usize].fatigue)
}

// ---------------------------------------------------------------- query

#[test]
fn query_returns_living_opposing_adjacent_ids_in_ascending_order_for_both_sides() {
    // P1 sits at (0,-2); its neighbours are (1,-2) (1,-3) (0,-3) (-1,-2) (-1,-1) (0,-1).
    let s = board(
        1,
        P1,
        &[(E3, hex(1, -2)), (E2, hex(0, -3)), (E1, hex(-1, -2)), (P2, hex(1, -3)), (P1, hex(0, -2))],
    );
    // Ascending id order, not neighbour order; the adjacent ally P2 never controls P1.
    assert_eq!(s.enemy_controllers_at(P1, hex(0, -2)), vec![E1, E2, E3]);
    // Symmetric: E1 at (-1,-2) is controlled by the adjacent player P1 only
    // (P2 at (1,-3) is not adjacent to it, and E2/E3 are allies of E1).
    assert_eq!(s.enemy_controllers_at(E1, hex(-1, -2)), vec![P1]);
    // The queried hex need not be the unit's own: E2/E3 touch (1,-3)'s neighbours.
    assert_eq!(s.enemy_controllers_at(P1, hex(1, -3)), vec![E2, E3]);
    // Enemy mover: players adjacent to its hex, in id order.
    assert_eq!(s.enemy_controllers_at(E2, hex(0, -3)), vec![P1, P2]);
    assert_eq!(s.enemy_controllers_at(E2, hex(-1, -2)), vec![P1], "the occupant's own side is excluded");
    // Nothing adjacent, unknown unit.
    assert!(s.enemy_controllers_at(P3, hex(-3, 0)).is_empty());
    assert!(s.enemy_controllers_at(99, hex(0, -2)).is_empty());
}

#[test]
fn dead_units_do_not_control() {
    let mut s = board(1, P1, &[(P1, hex(0, -2)), (E1, hex(1, -3)), (E2, hex(0, -3)), (E3, hex(1, -2))]);
    kill(&mut s, &[E2]);
    assert_eq!(s.enemy_controllers_at(P1, hex(0, -2)), vec![E1, E3]);
    kill(&mut s, &[E1, E3]);
    assert!(s.enemy_controllers_at(P1, hex(0, -2)).is_empty());
}

#[test]
fn query_is_pure_and_consumes_no_rng() {
    let s = board(1, P1, &[(P1, hex(0, -2)), (E1, hex(1, -3)), (E3, hex(1, -2))]);
    let before = (s.state_hash(), s.rng.clone());
    for _ in 0..3 {
        assert_eq!(s.enemy_controllers_at(P1, hex(0, -2)), vec![E1, E3]);
    }
    assert_eq!((s.state_hash(), s.rng.clone()), before);
    assert_eq!(s.rng, Rng::new(1));
}

// ------------------------------------------------------------ movement

#[test]
fn entering_adjacency_is_safe_and_departing_a_miss_continues() {
    // E1 at (1,-3) touches (0,-2) and (1,-2). P1 walks (-2,-2) -> (2,-2) (4 steps).
    let mut s = board(1, P1, &[(P1, hex(-2, -2)), (E1, hex(1, -3))]);
    let ev = s.apply(Command::Move { unit: P1, to: hex(2, -2) }).unwrap();
    // Seed 1 rolls 65 and 19 miss the 5% reactions. Entering (0,-2) and (1,-2)
    // was safe; departing each triggers one attack, even though (1,-2) is still adjacent.
    assert_eq!(
        ev,
        vec![
            Event::Moved { unit: P1, path: vec![hex(-1, -2), hex(0, -2)] },
            Event::Attacked { attacker: E1, target: P1, hit: false, roll: 65, chance: 5, dmg: 0, absorbed: 0, hp_loss: 0 },
            Event::Moved { unit: P1, path: vec![hex(1, -2)] },
            Event::Attacked { attacker: E1, target: P1, hit: false, roll: 19, chance: 5, dmg: 0, absorbed: 0, hp_loss: 0 },
            Event::Moved { unit: P1, path: vec![hex(2, -2)] },
        ]
    );
    assert_eq!(s.units[P1 as usize].pos, hex(2, -2));
    // 4 steps (8 AP, 8 fatigue) plus 2 incoming fatigue per missed reaction.
    assert_eq!(ap_fatigue(&s, P1), (1, 8 + 2 * 2));
    assert_eq!(s.rng, rng_after(1, 2));
}

#[test]
fn departing_hit_stops_at_the_departure_hex_and_charges_the_attempted_step_only() {
    let mut s = board(1, P1, &[(P1, hex(0, -2)), (E1, hex(1, -3))]);
    always_hit(&mut s, E1);
    let ev = s.apply(Command::Move { unit: P1, to: hex(-2, -2) }).unwrap();
    let d = draws(1, 2);
    let dmg = 15 + (d[1] % 16) as u32;
    let (absorbed, hp_loss) = soak(40, dmg);
    assert_eq!(
        ev,
        vec![Event::Attacked { attacker: E1, target: P1, hit: true, roll: 65, chance: 95, dmg, absorbed, hp_loss }]
    );
    let u = &s.units[P1 as usize];
    assert_eq!(u.pos, hex(0, -2), "mover stays at the departure hex");
    assert_eq!(u.armour, 40 - absorbed);
    assert_eq!(u.hp, 60 - hp_loss);
    // One attempted step: 2 AP and 2 fatigue, plus 5 incoming fatigue; the
    // second step of the route is never charged.
    assert_eq!(ap_fatigue(&s, P1), (9 - MOVE_AP, MOVE_FATIGUE + 5));
    assert_eq!(s.rng, rng_after(1, 2));
    assert!(s.units[E1 as usize].alive);
}

#[test]
fn route_that_enters_zoc_stops_at_a_later_departure_with_ordered_events() {
    let mut s = board(1, P1, &[(P1, hex(-2, -2)), (E1, hex(1, -3))]);
    always_hit(&mut s, E1);
    let ev = s.apply(Command::Move { unit: P1, to: hex(2, -2) }).unwrap();
    assert_no_empty_moves(&ev);
    // The two safe steps (the second enters the ZoC) are flushed before the reaction.
    assert!(matches!(&ev[0], Event::Moved { path, .. } if *path == vec![hex(-1, -2), hex(0, -2)]));
    assert!(matches!(&ev[1], Event::Attacked { attacker: 3, target: 0, hit: true, .. }));
    assert_eq!(ev.len(), 2);
    assert_eq!(s.units[P1 as usize].pos, hex(0, -2));
    // Three attempted steps charged (the departing one included), the fourth not.
    assert_eq!(ap_fatigue(&s, P1), (9 - 3 * MOVE_AP, 3 * MOVE_FATIGUE + 5));
}

#[test]
fn enemy_mover_is_stopped_by_a_player_controller() {
    let mut s = board(1, E1, &[(E1, hex(1, -3)), (P1, hex(0, -2))]);
    always_hit(&mut s, P1);
    let ev = s.apply(Command::Move { unit: E1, to: hex(3, -3) }).unwrap();
    assert_eq!(attacks(&ev), vec![(P1, E1, true, 65, 95)]);
    assert!(!ev.iter().any(|e| matches!(e, Event::Moved { .. })));
    assert_eq!(s.units[E1 as usize].pos, hex(1, -3));
    assert_eq!(ap_fatigue(&s, E1), (9 - MOVE_AP, MOVE_FATIGUE + 5));
}

#[test]
fn player_mover_with_enemy_controller_and_enemy_mover_missing_both_continue() {
    for (mover, controller, from, ctrl_at, to) in [
        (P1, E1, hex(0, -2), hex(1, -3), hex(-2, -2)),
        (E1, P1, hex(1, -3), hex(0, -2), hex(3, -3)),
    ] {
        let mut s = board(1, mover, &[(mover, from), (controller, ctrl_at)]);
        let ev = s.apply(Command::Move { unit: mover, to }).unwrap();
        assert_eq!(attacks(&ev), vec![(controller, mover, false, 65, 5)]);
        assert_eq!(s.units[mover as usize].pos, to);
        assert_no_empty_moves(&ev);
    }
}

#[test]
fn dead_and_allied_neighbours_never_react() {
    let mut s = board(1, P1, &[(P1, hex(0, -2)), (E1, hex(1, -3)), (E2, hex(0, -3)), (P2, hex(-1, -2))]);
    always_hit(&mut s, E1);
    always_hit(&mut s, E2);
    always_hit(&mut s, P2);
    kill(&mut s, &[E1, E2]);
    let ev = s.apply(Command::Move { unit: P1, to: hex(0, -1) }).unwrap();
    assert!(attacks(&ev).is_empty());
    assert_eq!(s.rng, Rng::new(1));
    assert_eq!(s.units[P1 as usize].pos, hex(0, -1));
}

#[test]
fn movement_without_reactions_is_one_moved_event_with_the_full_path() {
    let mut s = board(1, P1, &[(P1, hex(-2, -2)), (E1, hex(3, -3))]);
    let path = bfs_path(hex(-2, -2), hex(1, -2), &s.units.iter().map(|u| u.pos).collect::<Vec<_>>()).unwrap();
    let ev = s.apply(Command::Move { unit: P1, to: hex(1, -2) }).unwrap();
    assert_eq!(ev, vec![Event::Moved { unit: P1, path }]);
    assert_eq!(ap_fatigue(&s, P1), (9 - 3 * MOVE_AP, 3 * MOVE_FATIGUE));
    assert_eq!(s.rng, Rng::new(1));
}

// ----------------------------------------------------- multiple controllers

/// P1 at (0,-2) with E3 at (1,-2), E1 at (0,-3), E2 at (1,-3): ids 3, 4, 5 are
/// deliberately out of neighbour order. P1 retreats to (-2,-2) via (-1,-2).
fn three_controllers(seed: u64) -> BattleState {
    board(seed, P1, &[(P1, hex(0, -2)), (E3, hex(1, -2)), (E1, hex(0, -3)), (E2, hex(1, -3))])
}

#[test]
fn every_controller_reacts_in_id_order_after_a_nonlethal_hit_then_the_move_stops() {
    let mut s = three_controllers(1);
    always_hit(&mut s, E1);
    always_hit(&mut s, E3); // E2 keeps skill 0
    let ev = s.apply(Command::Move { unit: P1, to: hex(-2, -2) }).unwrap();
    // Three adjacent attackers: +10 surround. E1 95 (hit), E2 10 (miss), E3 95 (hit).
    assert_eq!(
        attacks(&ev),
        vec![(E1, P1, true, 65, 95), (E2, P1, false, 90, 10), (E3, P1, true, 35, 95)]
    );
    // Draw order: E1 roll, E1 damage, E2 roll, E3 roll, E3 damage.
    let d = draws(1, 5);
    let (a1, l1) = soak(40, 15 + (d[1] % 16) as u32);
    let (a2, l2) = soak(40 - a1, 15 + (d[4] % 16) as u32);
    let u = &s.units[P1 as usize];
    assert_eq!((u.armour, u.hp), (40 - a1 - a2, 60 - l1 - l2));
    assert_eq!(s.rng, rng_after(1, 5));
    assert_eq!(u.pos, hex(0, -2));
    assert!(!ev.iter().any(|e| matches!(e, Event::Moved { .. })));
    // 1 attempted step plus incoming 5 + 2 + 5.
    assert_eq!(ap_fatigue(&s, P1), (9 - MOVE_AP, MOVE_FATIGUE + 12));
}

#[test]
fn reactions_use_the_accepted_surround_chance_and_defender_fatigue() {
    let mut s = three_controllers(1);
    for id in [E1, E2, E3] {
        s.units[id as usize].stats.melee_skill = 50;
        assert_eq!(s.hit_chance(id, P1), 60, "50 + 2 additional adjacent allies * 5");
    }
    let ev = s.apply(Command::Move { unit: P1, to: hex(-2, -2) }).unwrap();
    let shots = attacks(&ev);
    assert_eq!(shots.iter().map(|a| a.0).collect::<Vec<_>>(), vec![E1, E2, E3]);
    let mut gain = 0;
    for &(_, _, hit, roll, chance) in &shots {
        assert_eq!(chance, 60);
        assert_eq!(hit, roll < chance);
        gain += if hit { 5 } else { 2 };
    }
    assert_eq!(s.units[P1 as usize].fatigue, MOVE_FATIGUE + gain);
}

#[test]
fn lethal_reaction_ends_the_reactions_immediately() {
    let mut s = three_controllers(1);
    always_hit(&mut s, E1);
    always_hit(&mut s, E2);
    always_hit(&mut s, E3);
    s.units[P1 as usize].hp = 1;
    s.units[P1 as usize].armour = 0;
    let ev = s.apply(Command::Move { unit: P1, to: hex(-2, -2) }).unwrap();
    assert_eq!(attacks(&ev), vec![(E1, P1, true, 65, 95)], "E2 and E3 never attack a dead mover");
    // Roll and damage of the one attack, one ally-death draw each for P2 and P3,
    // then E1's kill check.
    let rest = without_ally_checks(&ev, P1, &[P2, P3]);
    assert_eq!(rest[1..], [Event::Died { unit: P1 }, kill_check(1, 4, E1, P1), Event::TurnStarted { unit: P2 }]);
    assert_eq!(s.rng, rng_after(1, 5));
    assert!(!s.units[P1 as usize].alive);
    assert!(!s.queue.contains(&P1));
    assert_eq!(s.units[P1 as usize].pos, hex(0, -2));
}

#[test]
fn reaction_attackers_pay_nothing_even_with_no_ap_and_maximum_fatigue() {
    let mut s = three_controllers(1);
    for id in [E1, E2, E3] {
        let u = &mut s.units[id as usize];
        u.ap = 0;
        u.fatigue = u.stats.fatigue_max;
        u.turn_started = true;
    }
    always_hit(&mut s, E2);
    let ev = s.apply(Command::Move { unit: P1, to: hex(-2, -2) }).unwrap();
    assert_eq!(attacks(&ev).len(), 3, "no action budget or active turn is required");
    for id in [E1, E2, E3] {
        assert_eq!(ap_fatigue(&s, id), (0, 100), "controller {id} paid nothing");
    }
    assert_eq!(s.active_unit().unwrap().id, P1, "turn order is untouched");
}

// ------------------------------------------------------- partial execution

#[test]
fn incoming_fatigue_from_reactions_can_make_a_later_step_unaffordable() {
    // P1 holds 94 of 100 fatigue: the full 3-step route is affordable up front.
    let mut s = board(1, P1, &[(P1, hex(1, -2)), (E1, hex(1, -3))]);
    s.units[P1 as usize].fatigue = 94;
    let ev = s.apply(Command::Move { unit: P1, to: hex(-2, -2) }).expect("never an error after the initial check");
    // Departing (1,-2) and (0,-2) are both controlled; each miss adds 2 fatigue,
    // so the third step (departing (-1,-2), uncontrolled) can no longer be paid.
    assert_eq!(
        ev,
        vec![
            Event::Attacked { attacker: E1, target: P1, hit: false, roll: 65, chance: 5, dmg: 0, absorbed: 0, hp_loss: 0 },
            Event::Moved { unit: P1, path: vec![hex(0, -2)] },
            Event::Attacked { attacker: E1, target: P1, hit: false, roll: 19, chance: 5, dmg: 0, absorbed: 0, hp_loss: 0 },
            Event::Moved { unit: P1, path: vec![hex(-1, -2)] },
        ]
    );
    assert_eq!(s.units[P1 as usize].pos, hex(-1, -2));
    // Only two steps were attempted: 4 AP; fatigue saturates at the maximum.
    assert_eq!(ap_fatigue(&s, P1), (9 - 2 * MOVE_AP, 100));
}

#[test]
fn invalid_moves_mutate_neither_state_nor_rng_even_beside_controllers() {
    let mut s = board(1, P1, &[(P1, hex(0, -2)), (E1, hex(1, -3))]);
    always_hit(&mut s, E1);
    let cases: [(u32, u32, Hex, RuleError); 4] = [
        (3, 0, hex(-2, -2), RuleError::NotEnoughAp), // 2 steps, 3 AP
        (9, 99, hex(-2, -2), RuleError::TooFatigued),
        (9, 0, hex(1, -3), RuleError::Unreachable), // occupied by the controller
        (9, 0, hex(0, 0), RuleError::Unreachable),  // obstacle
    ];
    for (ap, fatigue, to, err) in cases {
        s.units[P1 as usize].ap = ap;
        s.units[P1 as usize].fatigue = fatigue;
        let before = (s.state_hash(), s.rng.clone());
        assert_eq!(s.apply(Command::Move { unit: P1, to }), Err(err));
        assert_eq!((s.state_hash(), s.rng.clone()), before);
    }
}

// ----------------------------------------------- death of the active mover

/// `ev` minus the ally-death checks that directly follow `Died`; asserts they
/// are exactly `allies`, in this order, immediately after `Died { fallen }`.
fn without_ally_checks(ev: &[Event], fallen: u32, allies: &[u32]) -> Vec<Event> {
    let died = ev.iter().position(|e| *e == Event::Died { unit: fallen }).expect("fallen unit died");
    let checked: Vec<u32> = ev[died + 1..]
        .iter()
        .map_while(|e| match *e {
            Event::AllyDeathMoraleChecked { unit, fallen: f, .. } if f == fallen => Some(unit),
            _ => None,
        })
        .collect();
    assert_eq!(checked, allies, "{ev:?}");
    ev.iter().filter(|e| !matches!(e, Event::AllyDeathMoraleChecked { .. })).cloned().collect()
}

/// The killer's Steady, Resolve-50 kill check when it is the `idx`-th draw of `seed`
/// (after the attack roll, the damage draw and one draw per ally check).
fn kill_check(seed: u64, idx: usize, unit: u32, fallen: u32) -> Event {
    let roll = (draws(seed, idx + 1)[idx] % 100) as u32;
    let after = if roll < 50 { Morale::Confident } else { Morale::Steady };
    Event::KillMoraleChecked { unit, fallen, roll, chance: 50, before: Morale::Steady, after }
}

fn lethal_board(active: u32, controller: u32, active_at: Hex, ctrl_at: Hex) -> BattleState {
    let mut s = board(1, active, &[(active, active_at), (controller, ctrl_at), (P2, hex(-1, -3))]);
    always_hit(&mut s, controller);
    s.units[active as usize].hp = 1;
    s.units[active as usize].armour = 0;
    s
}

#[test]
fn killed_mover_leaves_occupancy_and_queue_and_the_next_unit_starts() {
    let mut s = lethal_board(P1, E1, hex(0, -2), hex(1, -3));
    assert_eq!(s.queue, vec![P1, P2, P3, E1, E2, E3]);
    let ev = s.apply(Command::Move { unit: P1, to: hex(-2, -2) }).unwrap();
    assert!(matches!(ev[0], Event::Attacked { attacker: 3, target: 0, hit: true, .. }));
    assert_eq!(without_ally_checks(&ev, P1, &[P2, P3])[1..],
        [Event::Died { unit: P1 }, kill_check(1, 4, E1, P1), Event::TurnStarted { unit: P2 }]
    );
    assert_eq!(s.queue, vec![P2, P3, E1, E2, E3]);
    assert_eq!(s.active_unit().unwrap().id, P2);
    assert_eq!(s.units[P2 as usize].ap, 9);
    assert_eq!(s.outcome(), None);
    // The vacated hex is free for others again.
    assert!(s.reachable(P2).iter().any(|(h, _)| *h == hex(0, -2)));
    assert!(s.legal_targets(E1).is_empty());
}

#[test]
fn killed_last_unit_of_the_queue_starts_the_next_round() {
    let mut s = lethal_board(P1, E1, hex(0, -2), hex(1, -3));
    s.queue = vec![P1];
    let ev = s.apply(Command::Move { unit: P1, to: hex(-2, -2) }).unwrap();
    assert_eq!(
        without_ally_checks(&ev, P1, &[P2, P3])[1..],
        [Event::Died { unit: P1 }, kill_check(1, 4, E1, P1), Event::RoundStarted { round: 2 }, Event::TurnStarted { unit: P2 }]
    );
    assert_eq!(s.round, 2);
    assert_eq!(s.queue, vec![P2, P3, E1, E2, E3], "the dead mover is not re-queued");
    assert_eq!(s.outcome(), None);
}

#[test]
fn killed_last_unit_of_the_queue_in_the_final_round_ends_by_time_limit() {
    let mut s = lethal_board(P1, E1, hex(0, -2), hex(1, -3));
    s.queue = vec![P1];
    s.round = LAST_ROUND;
    let ev = s.apply(Command::Move { unit: P1, to: hex(-2, -2) }).unwrap();
    assert_eq!(
        without_ally_checks(&ev, P1, &[P2, P3])[1..],
        [
            Event::Died { unit: P1 },
            kill_check(1, 4, E1, P1),
            Event::BattleEnded { outcome: Outcome::Defeat, reason: EndReason::TimeLimit }
        ]
    );
    assert_eq!((s.outcome(), s.end_reason()), (Some(Outcome::Defeat), Some(EndReason::TimeLimit)));
    assert_eq!(s.round, LAST_ROUND);
    assert_eq!(s.apply(Command::EndTurn { unit: P2 }), Err(RuleError::BattleOver));
}

#[test]
fn killing_the_last_player_unit_is_defeat_without_a_new_turn() {
    let mut s = lethal_board(P1, E1, hex(0, -2), hex(1, -3));
    kill(&mut s, &[P2, P3]);
    let ev = s.apply(Command::Move { unit: P1, to: hex(-2, -2) }).unwrap();
    assert_eq!(
        ev[1..],
        [
            Event::Died { unit: P1 },
            kill_check(1, 2, E1, P1),
            Event::BattleEnded { outcome: Outcome::Defeat, reason: EndReason::Elimination }
        ]
    );
    assert_eq!((s.outcome(), s.end_reason()), (Some(Outcome::Defeat), Some(EndReason::Elimination)));
    assert!(s.active_unit().is_none());
    assert!(!ev.iter().any(|e| matches!(e, Event::TurnStarted { .. } | Event::RoundStarted { .. })));
}

#[test]
fn killing_the_last_enemy_unit_is_victory_without_a_new_turn() {
    let mut s = lethal_board(E1, P1, hex(1, -3), hex(0, -2));
    kill(&mut s, &[E2, E3]);
    let ev = s.apply(Command::Move { unit: E1, to: hex(3, -3) }).unwrap();
    assert_eq!(
        ev[1..],
        [
            Event::Died { unit: E1 },
            kill_check(1, 2, P1, E1),
            Event::BattleEnded { outcome: Outcome::Victory, reason: EndReason::Elimination }
        ]
    );
    assert_eq!((s.outcome(), s.end_reason()), (Some(Outcome::Victory), Some(EndReason::Elimination)));
    assert!(!ev.iter().any(|e| matches!(e, Event::TurnStarted { .. } | Event::RoundStarted { .. })));
    assert_eq!(s.apply(Command::EndTurn { unit: P1 }), Err(RuleError::BattleOver));
}

#[test]
fn step_affordability_check_cannot_overflow_at_a_u32_fatigue_maximum() {
    // Full-path validation passes (MAX - 4 + 2 * 2 == MAX). The first attempted
    // step plus a missed reaction saturates fatigue at MAX, so the second step
    // is unaffordable and must stop the move instead of overflowing.
    let mut s = board(1, P1, &[(P1, hex(0, -2)), (E1, hex(1, -3))]);
    s.units[P1 as usize].stats.fatigue_max = u32::MAX;
    s.units[P1 as usize].fatigue = u32::MAX - 4;
    let ev = s.apply(Command::Move { unit: P1, to: hex(-2, -2) }).expect("never an error after the initial check");
    assert_eq!(attacks(&ev), vec![(E1, P1, false, 65, 5)]);
    assert_no_empty_moves(&ev);
    assert_eq!(s.units[P1 as usize].pos, hex(-1, -2), "one completed step");
    assert_eq!(ap_fatigue(&s, P1), (9 - MOVE_AP, u32::MAX));
}
