//! Headless Footwork (partial UC-COMBAT-039, bypassing ZoC per UC-COMBAT-008;
//! capability flag, 3 AP / 20 fatigue, the one-hex limit and edge integration are
//! an APPROXIMATION): validation order, shared preview, destination queries,
//! reaction bypass, edge morale and hash bit. Fixtures clear the stock scenario
//! (`alive = false`) and place only the units they need; the first placed unit is
//! the active, started actor (9 AP, no fatigue). Players are ids 0-2, enemies 3-5.

use bb_rules::hex::{Hex, hex};
use bb_rules::rules::{FOOTWORK_AP, FOOTWORK_FATIGUE, FootworkPreview};
use bb_rules::scenario::{self, TerrainPreset};
use bb_rules::*;

const P1: u32 = 0;
const P2: u32 = 1;
const P3: u32 = 2;
const E1: u32 = 3;
const E2: u32 = 4;
const E3: u32 = 5;

/// Interior actor hex; its six neighbours in the engine's order are
/// `(-2,3) (-2,2) (-3,2) (-4,3) (-4,4) (-3,4)`, the last three on the map edge.
const A: Hex = hex(-3, 3);
const EAST: Hex = hex(-2, 3);
const NORTH_EAST: Hex = hex(-2, 2);
const NORTH: Hex = hex(-3, 2);
const WEST: Hex = hex(-4, 3);
const SOUTH_WEST: Hex = hex(-4, 4);
const SOUTH: Hex = hex(-3, 4);

fn fw(unit: u32, to: Hex) -> Command {
    Command::Footwork { unit, to }
}

/// Only `placed` units are present, queued in that order; `capable` units have
/// the Footwork capability.
fn board(placed: &[(u32, Hex)], capable: &[u32]) -> BattleState {
    let mut s = BattleState::new(7);
    for u in &mut s.units {
        u.alive = false;
        u.turn_started = false;
    }
    for &(id, pos) in placed {
        let u = &mut s.units[id as usize];
        u.alive = true;
        u.pos = pos;
    }
    for &id in capable {
        s.units[id as usize].has_footwork = true;
    }
    s.queue = placed.iter().map(|p| p.0).collect();
    let active = &mut s.units[placed[0].0 as usize];
    active.ap = 9;
    active.fatigue = 0;
    active.turn_started = true;
    s
}

fn solo(actor: u32) -> BattleState {
    board(&[(actor, A)], &[actor])
}

fn snapshot(s: &BattleState) -> (u64, Rng, Vec<u32>, Vec<Unit>, u32, Option<Outcome>, Option<EndReason>) {
    (s.state_hash(), s.rng.clone(), s.queue.clone(), s.units.clone(), s.round, s.outcome, s.end_reason)
}

fn assert_rejected(s: &mut BattleState, unit: u32, to: Hex, err: RuleError) {
    let before = snapshot(s);
    assert_eq!(s.preview_footwork(unit, to), Err(err), "preview and command errors differ");
    assert_eq!(snapshot(s), before, "rejected preview mutated state");
    assert!(!s.legal_footwork_destinations(unit).contains(&to));
    assert_eq!(snapshot(s), before, "destination query mutated state");
    assert_eq!(s.apply(fw(unit, to)), Err(err));
    assert_eq!(snapshot(s), before, "rejected command mutated state");
}

fn assert_preview_matches_apply(s: &BattleState, unit: u32, to: Hex) -> FootworkPreview {
    let preview = s.preview_footwork(unit, to).unwrap();
    let before = s.units.clone();
    let rng = s.rng.clone();
    let mut applied = s.clone();
    let events = applied.apply(fw(unit, to)).unwrap();

    let (b, a) = (&before[unit as usize], &applied.units[unit as usize]);
    assert_eq!((preview.unit, preview.from, preview.to), (unit, b.pos, to));
    assert_eq!(a.pos, preview.to);
    assert_eq!(a.ap, b.ap - preview.ap_cost);
    assert_eq!(a.fatigue, b.fatigue + preview.fatigue_cost);
    let mut expected = vec![Event::FootworkUsed { unit, from: preview.from, to }];
    if let Some(at) = preview.edge_penalty {
        assert_eq!(at, to);
        expected.push(Event::EdgeMoraleChanged { unit, at, before: b.morale, after: Morale::Breaking });
        assert_eq!(a.morale, Morale::Breaking);
    } else {
        assert_eq!(a.morale, b.morale);
    }
    assert_eq!(events, expected);
    assert_eq!(applied.rng, rng);
    for (i, (x, y)) in before.iter().zip(&applied.units).enumerate() {
        if i != unit as usize {
            assert_eq!(x, y, "bystander {i} changed");
        }
    }
    preview
}

#[test]
fn costs_are_the_documented_constants() {
    assert_eq!((FOOTWORK_AP, FOOTWORK_FATIGUE), (3, 20));
}

#[test]
fn success_for_both_factions_relocates_and_changes_nothing_else() {
    for actor in [P1, E1] {
        let mut s = board(&[(actor, A)], &[actor]);
        let (queue, round, rng) = (s.queue.clone(), s.round, s.rng.clone());
        let before = s.units[actor as usize].clone();
        let ev = s.apply(fw(actor, EAST)).unwrap();
        assert_eq!(ev, vec![Event::FootworkUsed { unit: actor, from: A, to: EAST }]);
        let expected = Unit { pos: EAST, ap: 9 - FOOTWORK_AP, fatigue: FOOTWORK_FATIGUE, ..before };
        assert_eq!(s.units[actor as usize], expected);
        assert_eq!((s.queue.clone(), s.round), (queue, round));
        assert_eq!(s.rng, rng);
        assert!(s.outcome().is_none());
        assert_eq!(s.active_unit().map(|u| u.id), Some(actor));
    }
}

#[test]
fn preview_reports_the_shared_plan_and_agrees_with_apply() {
    for actor in [P1, P3, E1, E3] {
        let mut s = solo(actor);
        s.units[actor as usize].ap = FOOTWORK_AP;
        let preview = s.preview_footwork(actor, EAST).unwrap();
        assert_eq!(
            preview,
            FootworkPreview {
                unit: actor,
                from: A,
                to: EAST,
                ap_cost: FOOTWORK_AP,
                fatigue_cost: FOOTWORK_FATIGUE,
                edge_penalty: None,
            }
        );
        for to in A.neighbors() {
            assert_preview_matches_apply(&s, actor, to);
        }
    }
}

#[test]
fn default_units_lack_the_capability() {
    for u in scenario::units() {
        assert!(!u.has_footwork);
    }
    let mut s = BattleState::new(1);
    let a = s.queue[0];
    let to = s.units[a as usize].pos.neighbors()[0];
    assert_rejected(&mut s, a, to, RuleError::MissingFootwork);
    assert!(s.legal_footwork_destinations(a).is_empty());
}

#[test]
fn missing_capability_is_checked_after_actor_gates_and_before_everything_else() {
    let mut s = board(&[(P1, A), (P2, WEST)], &[P2]);
    assert_rejected(&mut s, P1, EAST, RuleError::MissingFootwork);
    // Capability precedes destination and budget checks.
    s.units[P1 as usize].ap = 0;
    assert_rejected(&mut s, P1, A, RuleError::MissingFootwork);
    assert_rejected(&mut s, P1, hex(i32::MAX, i32::MAX), RuleError::MissingFootwork);
    assert_rejected(&mut s, P1, hex(0, 0), RuleError::MissingFootwork);
    // Other-actor, fleeing, retreating and battle-over gates come first.
    let mut s = board(&[(P1, A), (P2, WEST)], &[]);
    assert_rejected(&mut s, P2, EAST, RuleError::NotActiveUnit);
    assert_rejected(&mut s, 99, EAST, RuleError::NotActiveUnit);
    s.units[P1 as usize].morale = Morale::Fleeing;
    assert_rejected(&mut s, P1, EAST, RuleError::FleeingUnit);
    s.units[P1 as usize].retreating = true;
    assert_rejected(&mut s, P1, EAST, RuleError::RetreatingUnit);
    s.outcome = Some(Outcome::Victory);
    assert_rejected(&mut s, P1, EAST, RuleError::BattleOver);
    // A stale queue head for a non-present unit reaches the presence check.
    let mut s = board(&[(P1, A), (P2, WEST)], &[P1, P3]);
    s.queue = vec![P3];
    assert_rejected(&mut s, P3, EAST, RuleError::NotActiveUnit);
}

#[test]
fn ineligible_actors_have_no_destinations() {
    let mut s = solo(P1);
    assert_eq!(s.legal_footwork_destinations(P1), A.neighbors().to_vec());
    s.units[P1 as usize].has_footwork = false;
    assert!(s.legal_footwork_destinations(P1).is_empty());
    s.units[P1 as usize].has_footwork = true;
    s.units[P1 as usize].ap = FOOTWORK_AP - 1;
    assert!(s.legal_footwork_destinations(P1).is_empty());
    s.units[P1 as usize].ap = 9;
    s.units[P1 as usize].morale = Morale::Fleeing;
    assert!(s.legal_footwork_destinations(P1).is_empty());
    s.units[P1 as usize].morale = Morale::Steady;
    s.units[P1 as usize].retreating = true;
    assert!(s.legal_footwork_destinations(P1).is_empty());
    s.units[P1 as usize].retreating = false;
    assert!(s.legal_footwork_destinations(P2).is_empty()); // not the active unit
    assert!(s.legal_footwork_destinations(99).is_empty()); // no such unit
    s.queue.clear();
    assert!(s.legal_footwork_destinations(P1).is_empty());
    s.queue = vec![P1];
    s.outcome = Some(Outcome::Defeat);
    assert!(s.legal_footwork_destinations(P1).is_empty());
    s.outcome = None;
    s.units[P1 as usize].alive = false;
    assert!(s.legal_footwork_destinations(P1).is_empty());
    s.units[P1 as usize].alive = true;
    s.units[P1 as usize].fled = true;
    assert!(s.legal_footwork_destinations(P1).is_empty());
}

#[test]
fn unchanged_occupied_blocked_and_off_map_destinations_are_unreachable() {
    let mut s = board(&[(P1, A), (P2, EAST), (E1, hex(-2, 2))], &[P1]);
    s.units[P3 as usize].alive = true;
    s.units[P3 as usize].pos = SOUTH;
    assert_rejected(&mut s, P1, A, RuleError::Unreachable); // unchanged
    assert_rejected(&mut s, P1, EAST, RuleError::Unreachable); // occupied by an ally
    assert_rejected(&mut s, P1, NORTH_EAST, RuleError::Unreachable); // occupied by an enemy
    assert_rejected(&mut s, P1, SOUTH, RuleError::Unreachable);
    // Dead and escaped units do not occupy.
    s.units[P2 as usize].alive = false;
    s.units[E1 as usize].fled = true;
    assert!(s.preview_footwork(P1, EAST).is_ok() && s.preview_footwork(P1, NORTH_EAST).is_ok());

    // Obstacle and off-map hexes.
    for (actor_pos, to) in [(hex(0, 1), hex(0, 0)), (hex(1, 0), hex(1, -1)), (hex(-4, 4), hex(-5, 4)), (hex(4, -4), hex(5, -4))] {
        let mut s = board(&[(P1, actor_pos)], &[P1]);
        assert_rejected(&mut s, P1, to, RuleError::Unreachable);
    }
    // Unreachable wins over budget failures.
    let mut s = solo(P1);
    s.units[P1 as usize].ap = 0;
    assert_rejected(&mut s, P1, A, RuleError::Unreachable);
    assert_rejected(&mut s, P1, hex(-5, 3), RuleError::Unreachable);
}

#[test]
fn invalid_actor_coordinates_are_unreachable_and_queries_do_not_panic() {
    for bad in [hex(0, 0), hex(7, 7), hex(i32::MAX, i32::MAX), hex(i32::MIN, i32::MIN), hex(i32::MAX, i32::MIN), hex(i32::MIN, 0)] {
        let mut s = board(&[(P1, bad)], &[P1]);
        for to in [EAST, A, bad, hex(i32::MAX, 0), hex(0, i32::MIN)] {
            assert_rejected(&mut s, P1, to, RuleError::Unreachable);
        }
        assert!(s.legal_footwork_destinations(P1).is_empty());
    }
}

#[test]
fn extreme_destination_coordinates_are_unreachable_without_overflow() {
    let extremes = [
        hex(i32::MAX, i32::MAX),
        hex(i32::MIN, i32::MIN),
        hex(i32::MAX, i32::MIN),
        hex(i32::MIN, i32::MAX),
        hex(i32::MAX, 0),
        hex(0, i32::MIN),
        hex(i32::MIN + 1, 0),
    ];
    let mut s = solo(P1);
    for to in extremes {
        assert_rejected(&mut s, P1, to, RuleError::Unreachable);
    }
}

#[test]
fn nonadjacent_free_destinations_are_not_adjacent() {
    let mut s = solo(P1);
    for to in [hex(-1, 3), hex(-3, 1), hex(-2, 1), hex(3, -3), hex(4, -4), hex(-4, 2)] {
        assert_rejected(&mut s, P1, to, RuleError::NotAdjacent);
    }
    // Adjacency precedes the budget checks.
    s.units[P1 as usize].ap = 0;
    assert_rejected(&mut s, P1, hex(3, -3), RuleError::NotAdjacent);
}

#[test]
fn ap_and_fatigue_limits_and_precedence() {
    let fmax = |s: &BattleState| s.units[P1 as usize].stats.fatigue_max;
    let mut s = solo(P1);
    s.units[P1 as usize].ap = FOOTWORK_AP - 1;
    assert_rejected(&mut s, P1, EAST, RuleError::NotEnoughAp);
    // AP is reported before fatigue.
    s.units[P1 as usize].fatigue = fmax(&s);
    assert_rejected(&mut s, P1, EAST, RuleError::NotEnoughAp);
    // Exact AP cap and exact fatigue cap succeed together.
    s.units[P1 as usize].ap = FOOTWORK_AP;
    s.units[P1 as usize].fatigue = fmax(&s) - FOOTWORK_FATIGUE;
    assert_preview_matches_apply(&s, P1, EAST);
    s.apply(fw(P1, EAST)).unwrap();
    let u = &s.units[P1 as usize];
    assert_eq!((u.ap, u.fatigue), (0, fmax(&s)));
    // One fatigue point over the cap is rejected, with plenty of AP.
    let mut s = solo(P1);
    s.units[P1 as usize].fatigue = fmax(&s) - FOOTWORK_FATIGUE + 1;
    assert_rejected(&mut s, P1, EAST, RuleError::TooFatigued);
}

#[test]
fn fatigue_addition_overflow_is_rejected_not_wrapped() {
    for fatigue in [u32::MAX, u32::MAX - FOOTWORK_FATIGUE + 1] {
        let mut s = solo(P1);
        s.units[P1 as usize].fatigue = fatigue;
        assert_rejected(&mut s, P1, EAST, RuleError::TooFatigued);
    }
    // Also with a stat cap that would admit the wrapped sum.
    let mut s = solo(P1);
    s.units[P1 as usize].fatigue = u32::MAX - 5;
    s.units[P1 as usize].stats.fatigue_max = u32::MAX;
    assert_rejected(&mut s, P1, EAST, RuleError::TooFatigued);
}

#[test]
fn legal_destinations_follow_neighbour_order_and_skip_rejected_hexes() {
    let s = solo(P1);
    assert_eq!(s.legal_footwork_destinations(P1), vec![EAST, NORTH_EAST, NORTH, WEST, SOUTH_WEST, SOUTH]);
    for to in s.legal_footwork_destinations(P1) {
        assert!(s.preview_footwork(P1, to).is_ok());
    }
    // Occupied hexes drop out, order of the rest is preserved.
    let s = board(&[(P1, A), (P2, NORTH_EAST), (E1, WEST)], &[P1]);
    assert_eq!(s.legal_footwork_destinations(P1), vec![EAST, NORTH, SOUTH_WEST, SOUTH]);
    // Obstacle and off-map neighbours drop out (actor on the corner next to hex (0,0)).
    let s = board(&[(P1, hex(1, 0))], &[P1]);
    let all = hex(1, 0).neighbors();
    let expected: Vec<Hex> = all.into_iter().filter(|h| *h != hex(0, 0) && *h != hex(1, -1)).collect();
    assert_eq!(s.legal_footwork_destinations(P1), expected);
    assert_eq!(expected.len(), 4);
    let s = board(&[(P1, hex(4, -4))], &[P1]);
    // Off-map neighbours of a corner drop out.
    assert_eq!(s.legal_footwork_destinations(P1), vec![hex(3, -4), hex(3, -3), hex(4, -3)]);
}

#[test]
fn destinations_and_previews_refresh_after_every_change() {
    let mut s = board(&[(P1, A), (P2, SOUTH)], &[P1]);
    assert_eq!(s.legal_footwork_destinations(P1), vec![EAST, NORTH_EAST, NORTH, WEST, SOUTH_WEST]);
    s.apply(fw(P1, EAST)).unwrap();
    // The vacated hex is now a legal destination; the moved actor's neighbours are new.
    let dests = s.legal_footwork_destinations(P1);
    // P2 still stands on SOUTH, which is also a neighbour of the new hex.
    assert_eq!(dests, EAST.neighbors().into_iter().filter(|h| *h != SOUTH).collect::<Vec<_>>());
    assert!(dests.contains(&A));
    assert_eq!(s.preview_footwork(P1, A).unwrap().from, EAST);
    // Another unit moving onto a destination, or budgets/capability changing, is seen at once.
    s.units[P2 as usize].pos = hex(-1, 3);
    assert!(!s.legal_footwork_destinations(P1).contains(&hex(-1, 3)));
    assert_eq!(s.preview_footwork(P1, hex(-1, 3)), Err(RuleError::Unreachable));
    s.units[P2 as usize].alive = false;
    assert!(s.legal_footwork_destinations(P1).contains(&hex(-1, 3)));
    s.units[P1 as usize].ap = FOOTWORK_AP - 1;
    assert!(s.legal_footwork_destinations(P1).is_empty());
    s.units[P1 as usize].ap = FOOTWORK_AP;
    assert_eq!(s.legal_footwork_destinations(P1).len(), 6);
    s.units[P1 as usize].has_footwork = false;
    assert!(s.legal_footwork_destinations(P1).is_empty());
}

#[test]
fn queries_are_repeatable_and_leave_the_full_snapshot_unchanged() {
    let s = board(&[(P1, A), (P2, WEST), (E1, SOUTH_WEST)], &[P1]);
    let before = snapshot(&s);
    for _ in 0..3 {
        let first = s.legal_footwork_destinations(P1);
        assert_eq!(first, s.legal_footwork_destinations(P1));
        for to in A.neighbors() {
            assert_eq!(s.preview_footwork(P1, to), s.preview_footwork(P1, to));
        }
        assert_eq!(snapshot(&s), before);
    }
}

#[test]
fn footwork_in_enemy_zone_of_control_provokes_nothing() {
    // E1 and E2 control the departure hex and the destination; E3 is far away.
    let mut s = board(&[(P1, A), (E1, NORTH_EAST), (E2, WEST), (E3, hex(3, -3))], &[P1]);
    assert_eq!(s.enemy_controllers_at(P1, A), vec![E1, E2]);
    assert_eq!(s.enemy_controllers_at(P1, NORTH), vec![E1, E2]);
    let hp: Vec<u32> = s.units.iter().map(|u| u.hp).collect();
    let (rng, fatigue) = (s.rng.clone(), s.units.iter().map(|u| u.fatigue).collect::<Vec<_>>());
    let ev = s.apply(fw(P1, NORTH)).unwrap();
    assert_eq!(ev, vec![Event::FootworkUsed { unit: P1, from: A, to: NORTH }]);
    assert_eq!(s.rng, rng);
    assert_eq!(s.units.iter().map(|u| u.hp).collect::<Vec<_>>(), hp);
    for (i, u) in s.units.iter().enumerate().filter(|(i, _)| *i != P1 as usize) {
        assert_eq!(u.fatigue, fatigue[i], "bystander fatigue changed");
    }
    // Enemy-side actors enjoy the same bypass against player controllers.
    let mut s = board(&[(E1, A), (P1, NORTH_EAST), (P2, WEST)], &[E1]);
    let rng = s.rng.clone();
    let ev = s.apply(fw(E1, NORTH)).unwrap();
    assert_eq!(ev, vec![Event::FootworkUsed { unit: E1, from: A, to: NORTH }]);
    assert_eq!(s.rng, rng);
}

#[test]
fn capability_grants_no_immunity_to_ordinary_movement_reactions() {
    let setup = || board(&[(P1, A), (E1, NORTH_EAST), (E2, WEST)], &[P1]);
    // Ordinary Move still provokes from both controllers.
    let mut s = setup();
    let rng = s.rng.clone();
    let ev = s.apply(Command::Move { unit: P1, to: EAST }).unwrap();
    let attackers: Vec<u32> = ev
        .iter()
        .filter_map(|e| if let Event::Attacked { attacker, target, .. } = e { assert_eq!(*target, P1); Some(*attacker) } else { None })
        .collect();
    assert!(!attackers.is_empty() && attackers.iter().all(|a| [E1, E2].contains(a)), "{ev:?}");
    assert_ne!(s.rng, rng, "reactions consume RNG");

    // Automatic escape movement (Flee and Withdraw) still provokes as well.
    for withdraw in [false, true] {
        let mut s = setup();
        if withdraw {
            s.units[P1 as usize].retreating = true;
        } else {
            s.units[P1 as usize].morale = Morale::Fleeing;
        }
        let rng = s.rng.clone();
        let cmd = if withdraw { Command::Withdraw { unit: P1 } } else { Command::Flee { unit: P1 } };
        let ev = s.apply(cmd).unwrap();
        assert!(ev.iter().any(|e| matches!(e, Event::Attacked { target, .. } if *target == P1)), "{ev:?}");
        assert_ne!(s.rng, rng);
        assert!(!ev.iter().any(|e| matches!(e, Event::FootworkUsed { .. })));
    }
}

#[test]
fn heights_never_change_the_fixed_costs_and_queries_see_the_new_hex() {
    // Equal height, uphill (+2) and downhill (-3) destinations all cost exactly 3 AP / 20 fatigue.
    for (from_h, to_h) in [(0u8, 0u8), (0, 2), (1, 3), (3, 0), (2, 1)] {
        let mut s = solo(P1);
        s.elevations.set(A, from_h).unwrap();
        s.elevations.set(EAST, to_h).unwrap();
        assert_preview_matches_apply(&s, P1, EAST);
        s.apply(fw(P1, EAST)).unwrap();
        let u = &s.units[P1 as usize];
        assert_eq!((u.ap, u.fatigue), (9 - FOOTWORK_AP, FOOTWORK_FATIGUE), "heights {from_h}->{to_h}");
    }
    // An uphill step that ordinary movement could not afford is still affordable.
    let mut s = solo(P1);
    s.elevations.set(EAST, 3).unwrap();
    s.units[P1 as usize].ap = FOOTWORK_AP;
    assert_eq!(s.preview_move(P1, EAST), Err(RuleError::NotEnoughAp));
    assert!(s.preview_footwork(P1, EAST).is_ok());
}

#[test]
fn controller_surround_and_elevation_queries_refresh_after_the_step() {
    // E1 touches A and the allies' hexes; EAST is out of its reach.
    let mut s = board(&[(P1, A), (P2, WEST), (E1, SOUTH_WEST)], &[P1]);
    assert_eq!(s.enemy_controllers_at(P1, A), vec![E1]);
    assert_eq!(s.legal_targets(P1), vec![E1]);
    let (p1, p2) = (s.hit_chance(P1, E1), s.hit_chance(P2, E1));
    s.apply(fw(P1, EAST)).unwrap();
    assert!(s.enemy_controllers_at(P1, EAST).is_empty());
    assert!(s.legal_targets(P1).is_empty());
    assert_eq!(s.enemy_controllers_at(P1, A), vec![E1]);
    // P2 lost the additional-ally surround bonus.
    assert_eq!(s.hit_chance(P2, E1), p2 - 5);
    // Stepping back into reach restores the bonus.
    s.apply(fw(P1, A)).unwrap();
    assert_eq!((s.hit_chance(P1, E1), s.hit_chance(P2, E1)), (p1, p2));

    // Elevation: stepping from level ground up beside an enemy adds the downhill bonus.
    let mut s = board(&[(P1, A), (E1, hex(-2, 2))], &[P1]);
    s.elevations.set(NORTH, 2).unwrap();
    let flat = s.hit_chance(P1, E1);
    assert_eq!(s.preview_footwork(P1, NORTH).unwrap().edge_penalty, None);
    s.apply(fw(P1, NORTH)).unwrap();
    assert_eq!(s.hit_chance(P1, E1), flat + 10);
    // Stepping back down to level ground returns to the original chance.
    s.apply(fw(P1, A)).unwrap();
    assert_eq!(s.hit_chance(P1, E1), flat);
    // Occupancy: the other units' destination lists see the vacated hex.
    let mut s = board(&[(P1, A), (P2, NORTH_EAST)], &[P1, P2]);
    s.apply(fw(P1, EAST)).unwrap();
    s.queue = vec![P2];
    s.units[P2 as usize].ap = 9;
    assert!(s.legal_footwork_destinations(P2).contains(&A));
}

#[test]
fn scheduling_flags_retreat_intent_and_base_stats_are_preserved() {
    let mut s = board(&[(P1, A), (P2, WEST), (E1, hex(3, -3))], &[P1]);
    s.units[P1 as usize].waited = true;
    s.units[P1 as usize].skip_turn = true;
    s.units[P1 as usize].morale = Morale::Wavering;
    s.units[P1 as usize].hp -= 3;
    s.units[P1 as usize].armour -= 1;
    let before = s.units.clone();
    let (queue, round) = (s.queue.clone(), s.round);
    s.apply(fw(P1, EAST)).unwrap();
    let expected = Unit { pos: EAST, ap: 9 - FOOTWORK_AP, fatigue: FOOTWORK_FATIGUE, ..before[0].clone() };
    assert_eq!(s.units[0], expected);
    assert_eq!(s.units[1], before[1]);
    assert_eq!(s.units[3], before[3]);
    assert_eq!((s.queue.clone(), s.round), (queue, round));
    assert!(s.units[0].turn_started && s.units[0].waited && s.units[0].skip_turn && !s.units[0].retreating);
}

fn edge_morale_cases() -> [Morale; 3] {
    [Morale::Confident, Morale::Steady, Morale::Wavering]
}

#[test]
fn edge_arrival_lowers_every_eligible_morale_to_breaking() {
    for from in edge_morale_cases() {
        for edge in [WEST, SOUTH_WEST, SOUTH] {
            let mut s = solo(P1);
            s.units[P1 as usize].morale = from;
            let preview = assert_preview_matches_apply(&s, P1, edge);
            assert_eq!(preview.edge_penalty, Some(edge));
            let ev = s.apply(fw(P1, edge)).unwrap();
            assert_eq!(
                ev,
                vec![
                    Event::FootworkUsed { unit: P1, from: A, to: edge },
                    Event::EdgeMoraleChanged { unit: P1, at: edge, before: from, after: Morale::Breaking },
                ]
            );
            let u = &s.units[P1 as usize];
            assert_eq!((u.morale, u.pos), (Morale::Breaking, edge));
            // No extraction: still present, queued and active.
            assert!(u.is_present() && !u.fled);
            assert_eq!(s.queue, vec![P1]);
            assert!(s.outcome().is_none());
        }
    }
    // Enemy actors take it too.
    let mut s = solo(E2);
    assert_eq!(assert_preview_matches_apply(&s, E2, WEST).edge_penalty, Some(WEST));
    let ev = s.apply(fw(E2, WEST)).unwrap();
    assert!(matches!(ev[1], Event::EdgeMoraleChanged { unit: E2, .. }));
}

#[test]
fn already_breaking_and_non_edge_arrivals_emit_no_edge_event() {
    let mut s = solo(P1);
    s.units[P1 as usize].morale = Morale::Breaking;
    let preview = assert_preview_matches_apply(&s, P1, WEST);
    assert_eq!(preview.edge_penalty, None);
    let ev = s.apply(fw(P1, WEST)).unwrap();
    assert_eq!(ev, vec![Event::FootworkUsed { unit: P1, from: A, to: WEST }]);
    assert_eq!(s.units[P1 as usize].morale, Morale::Breaking);
    // Interior arrival: unchanged morale, no event, and the preview says so.
    let s = solo(P1);
    assert_eq!(assert_preview_matches_apply(&s, P1, EAST).edge_penalty, None);
    // Leaving the edge toward the interior, or moving edge to edge.
    let mut s = board(&[(P1, WEST)], &[P1]);
    let ev = s.apply(fw(P1, A)).unwrap();
    assert_eq!(ev, vec![Event::FootworkUsed { unit: P1, from: WEST, to: A }]);
    assert_eq!(s.units[P1 as usize].morale, Morale::Steady);
    let s = board(&[(P1, WEST)], &[P1]);
    assert_eq!(assert_preview_matches_apply(&s, P1, SOUTH_WEST).edge_penalty, Some(SOUTH_WEST));
    // Breaking at the edge may keep stepping along it without events.
    let mut s = board(&[(P1, WEST)], &[P1]);
    s.units[P1 as usize].morale = Morale::Breaking;
    assert_eq!(s.apply(fw(P1, SOUTH_WEST)).unwrap().len(), 1);
}

#[test]
fn a_second_edge_step_after_breaking_is_silent() {
    let mut s = solo(P1);
    assert_eq!(s.apply(fw(P1, WEST)).unwrap().len(), 2);
    assert_eq!(s.apply(fw(P1, SOUTH_WEST)).unwrap().len(), 1);
    assert_eq!(s.units[P1 as usize].morale, Morale::Breaking);
}

#[test]
fn repeated_use_keeps_the_turn_until_budgets_run_out() {
    let mut s = solo(P1);
    let path = [EAST, hex(-1, 3), hex(-1, 2)];
    for (i, to) in path.into_iter().enumerate() {
        s.apply(fw(P1, to)).unwrap();
        let u = &s.units[P1 as usize];
        assert_eq!((u.ap, u.fatigue), (9 - FOOTWORK_AP * (i as u32 + 1), FOOTWORK_FATIGUE * (i as u32 + 1)));
    }
    // Zero AP: the actor still owns the turn, queue and flags are intact.
    assert_eq!(s.units[P1 as usize].ap, 0);
    assert_eq!(s.active_unit().map(|u| u.id), Some(P1));
    assert_eq!(s.queue, vec![P1]);
    assert!(s.units[P1 as usize].turn_started);
    assert!(s.legal_footwork_destinations(P1).is_empty());
    let before = snapshot(&s);
    assert_eq!(s.apply(fw(P1, hex(-1, 3))), Err(RuleError::NotEnoughAp));
    assert_eq!(snapshot(&s), before);
    // The turn is ended explicitly by the usual command only.
    assert!(s.apply(Command::EndTurn { unit: P1 }).is_ok());
}

#[test]
fn fatigue_budget_ends_repeated_use() {
    let mut s = solo(P1);
    s.units[P1 as usize].ap = 90;
    let fmax = s.units[P1 as usize].stats.fatigue_max;
    let uses = fmax / FOOTWORK_FATIGUE;
    let mut at = A;
    for i in 0..uses {
        let to = if i % 2 == 0 { EAST } else { A };
        s.apply(fw(P1, to)).unwrap();
        at = to;
    }
    assert_eq!(s.units[P1 as usize].pos, at);
    let to = at.neighbors().into_iter().find(|h| h.in_map() && !h.is_obstacle()).unwrap();
    assert_eq!(s.apply(fw(P1, to)), Err(RuleError::TooFatigued));
}

#[test]
fn footwork_coexists_with_pending_adrenaline() {
    let mut s = solo(P1);
    s.units[P1 as usize].has_adrenaline = true;
    s.apply(Command::Adrenaline { unit: P1 }).unwrap();
    assert!(s.units[P1 as usize].adrenaline_pending);
    let rng = s.rng.clone();
    s.apply(fw(P1, EAST)).unwrap();
    let u = &s.units[P1 as usize];
    assert!(u.has_adrenaline && u.adrenaline_pending && u.has_footwork);
    assert_eq!((u.ap, u.fatigue), (9 - 1 - FOOTWORK_AP, 20 + FOOTWORK_FATIGUE));
    assert_eq!(s.rng, rng);
    // Adrenaline can be preceded by Footwork too, and stays pending through an edge arrival.
    let mut s = solo(P1);
    s.units[P1 as usize].has_adrenaline = true;
    s.apply(fw(P1, WEST)).unwrap();
    s.apply(Command::Adrenaline { unit: P1 }).unwrap();
    assert!(s.units[P1 as usize].adrenaline_pending);
}

#[test]
fn has_footwork_is_an_independent_hash_bit() {
    let base = scenario::battle(1, TerrainPreset::Flat);
    assert_eq!(base.state_hash(), 0x8c00302113cbe668, "false capability must not change hashes");
    let hills = scenario::battle(1, TerrainPreset::Hills);
    assert_eq!(hills.state_hash(), 0xb759683cdff4bfa2);
    let mut variants = vec![base.clone()];
    for (adrenaline, rotation, footwork) in [
        (true, false, false),
        (false, true, false),
        (false, false, true),
        (true, true, false),
        (true, false, true),
        (false, true, true),
        (true, true, true),
    ] {
        let mut v = base.clone();
        v.units[0].has_adrenaline = adrenaline;
        v.units[0].has_rotation = rotation;
        v.units[0].has_footwork = footwork;
        variants.push(v);
    }
    let hashes: Vec<u64> = variants.iter().map(BattleState::state_hash).collect();
    for (i, a) in hashes.iter().enumerate() {
        for b in &hashes[i + 1..] {
            assert_ne!(a, b);
        }
    }
    // Clearing the flag restores the original hash.
    let mut footwork = base.clone();
    footwork.units[0].has_footwork = true;
    assert_ne!(footwork.state_hash(), base.state_hash());
    footwork.units[0].has_footwork = false;
    assert_eq!(footwork.state_hash(), base.state_hash());
}
