//! Headless Rotation (partial UC-COMBAT-039, bypassing ZoC per UC-COMBAT-008;
//! capability flag, 25 fatigue, target restrictions and edge integration are an
//! APPROXIMATION): validation order, atomic swap, scheduling preservation,
//! derived spatial queries, edge morale and hash bit. Fixtures clear the stock
//! scenario (`alive = false`) and place only the units they need; the first
//! placed unit is the active, started actor (9 AP, no fatigue). Players are
//! ids 0-2, enemies 3-5 (fatigue_max 100).

use bb_rules::hex::{Hex, hex};
use bb_rules::rules::{RotationPreview, ROTATION_AP, ROTATION_FATIGUE};
use bb_rules::scenario::{self, TerrainPreset};
use bb_rules::*;

const P1: u32 = 0;
const P2: u32 = 1;
const P3: u32 = 2;
const E1: u32 = 3;
const E2: u32 = 4;
const E3: u32 = 5;

/// Interior, mutually adjacent pair.
const A: Hex = hex(-3, 3);
const B: Hex = hex(-3, 2);

fn rot(unit: u32, target: u32) -> Command {
    Command::Rotation { unit, target }
}

/// Only `placed` units are present, queued in that order; `capable` units have
/// the Rotation capability.
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
        s.units[id as usize].has_rotation = true;
    }
    s.queue = placed.iter().map(|p| p.0).collect();
    let active = &mut s.units[placed[0].0 as usize];
    active.ap = 9;
    active.fatigue = 0;
    active.turn_started = true;
    s
}

fn pair(actor: u32, target: u32) -> BattleState {
    board(&[(actor, A), (target, B)], &[actor])
}

fn snapshot(s: &BattleState) -> (u64, Rng, Vec<u32>, Vec<Unit>, u32, Option<Outcome>, Option<EndReason>) {
    (s.state_hash(), s.rng.clone(), s.queue.clone(), s.units.clone(), s.round, s.outcome, s.end_reason)
}

fn assert_rejected(s: &mut BattleState, cmd: Command, err: RuleError) {
    let before = snapshot(s);
    let Command::Rotation { unit, target } = cmd else { panic!("expected Rotation") };
    assert_eq!(s.preview_rotation(unit, target), Err(err), "preview and command errors differ");
    assert_eq!(snapshot(s), before, "rejected preview mutated state");
    assert_eq!(s.apply(cmd), Err(err));
    assert_eq!(snapshot(s), before, "rejected command mutated state");
}

fn assert_preview_matches_apply(s: &BattleState, unit: u32, target: u32) -> RotationPreview {
    let preview = s.preview_rotation(unit, target).unwrap();
    let before_units = s.units.clone();
    let mut applied = s.clone();
    let events = applied.apply(rot(unit, target)).unwrap();

    assert_eq!(applied.units[unit as usize].pos, preview.unit_to);
    assert_eq!(applied.units[target as usize].pos, preview.target_to);
    assert_eq!(applied.units[unit as usize].ap, before_units[unit as usize].ap - preview.ap_cost);
    assert_eq!(
        applied.units[unit as usize].fatigue,
        before_units[unit as usize].fatigue + preview.fatigue_cost
    );
    assert_eq!(applied.units[target as usize].ap, before_units[target as usize].ap);
    assert_eq!(applied.units[target as usize].fatigue, before_units[target as usize].fatigue);

    let mut expected_events = vec![Event::Rotated { unit, target }];
    for &(id, at) in &preview.edge_penalties {
        expected_events.push(Event::EdgeMoraleChanged {
            unit: id,
            at,
            before: before_units[id as usize].morale,
            after: Morale::Breaking,
        });
        assert_eq!(applied.units[id as usize].morale, Morale::Breaking);
    }
    assert_eq!(events, expected_events);
    preview
}

#[test]
fn costs_are_the_documented_constants() {
    assert_eq!((ROTATION_AP, ROTATION_FATIGUE), (3, 25));
}

#[test]
fn success_for_both_factions_swaps_atomically_and_changes_nothing_else() {
    for (actor, target) in [(P1, P2), (E1, E2)] {
        let mut s = board(&[(actor, A), (target, B)], &[actor]);
        let (queue, round, rng) = (s.queue.clone(), s.round, s.rng.clone());
        let (actor_before, target_before) = (s.units[actor as usize].clone(), s.units[target as usize].clone());
        let ev = s.apply(rot(actor, target)).unwrap();
        assert_eq!(ev, vec![Event::Rotated { unit: actor, target }]);
        let (a, t) = (&s.units[actor as usize], &s.units[target as usize]);
        assert_eq!((a.pos, t.pos), (B, A));
        assert_eq!((a.ap, a.fatigue), (9 - ROTATION_AP, ROTATION_FATIGUE));
        let expected_actor = Unit { pos: B, ap: 6, fatigue: 25, ..actor_before };
        assert_eq!(*a, expected_actor);
        let expected_target = Unit { pos: A, ..target_before };
        assert_eq!(*t, expected_target);
        assert_eq!(s.queue, queue);
        assert_eq!(s.round, round);
        assert_eq!(s.rng, rng);
        assert!(s.outcome().is_none());
        // No turn advance: the actor still holds the turn and can rotate back.
        assert_eq!(s.active_unit().map(|u| u.id), Some(actor));
        s.apply(rot(actor, target)).unwrap();
        assert_eq!((s.units[actor as usize].pos, s.units[target as usize].pos), (A, B));
        assert_eq!(s.units[actor as usize].fatigue, 2 * ROTATION_FATIGUE);
    }
}

#[test]
fn preview_reports_the_shared_swap_plan_and_fixed_costs() {
    for (actor, target) in [(P1, P2), (E1, E2), (P3, P1)] {
        let actor_pos = A;
        let target_pos = B;
        let mut s = board(&[(actor, actor_pos), (target, target_pos)], &[actor]);
        s.units[actor as usize].ap = ROTATION_AP;
        let preview = s.preview_rotation(actor, target).unwrap();
        assert_eq!(preview, RotationPreview {
            unit: actor,
            target,
            unit_to: target_pos,
            target_to: actor_pos,
            ap_cost: ROTATION_AP,
            fatigue_cost: ROTATION_FATIGUE,
            edge_penalties: vec![],
        });
        assert_preview_matches_apply(&s, actor, target);
    }
}

#[test]
fn target_does_not_need_capability_and_may_be_any_unit_id_order() {
    // Target id lower than actor id.
    let mut s = board(&[(P3, A), (P1, B)], &[P3]);
    assert!(!s.units[P1 as usize].has_rotation);
    let ev = s.apply(rot(P3, P1)).unwrap();
    assert_eq!(ev, vec![Event::Rotated { unit: P3, target: P1 }]);
    assert_eq!((s.units[P3 as usize].pos, s.units[P1 as usize].pos), (B, A));
}

#[test]
fn default_units_lack_the_capability() {
    for u in scenario::units() {
        assert!(!u.has_rotation);
    }
    let mut s = BattleState::new(1);
    let a = s.queue[0];
    assert_rejected(&mut s, rot(a, a ^ 1), RuleError::MissingRotation);
}

#[test]
fn missing_capability_is_rejected_purely_and_before_target_checks() {
    let mut s = board(&[(P1, A), (P2, B)], &[P2]);
    assert_rejected(&mut s, rot(P1, P2), RuleError::MissingRotation);
    // Capability is checked before an invalid target, adjacency and budgets.
    s.units[P1 as usize].ap = 0;
    assert_rejected(&mut s, rot(P1, P1), RuleError::MissingRotation);
    assert_rejected(&mut s, rot(P1, 99), RuleError::MissingRotation);
}

#[test]
fn existing_validation_precedes_the_capability_check() {
    let mut s = board(&[(P1, A), (P2, B)], &[]);
    assert_rejected(&mut s, rot(P2, P1), RuleError::NotActiveUnit);
    assert_rejected(&mut s, rot(99, P1), RuleError::NotActiveUnit);
    s.units[P1 as usize].morale = Morale::Fleeing;
    assert_rejected(&mut s, rot(P1, P2), RuleError::FleeingUnit);
    s.units[P1 as usize].retreating = true;
    assert_rejected(&mut s, rot(P1, P2), RuleError::RetreatingUnit);
    s.outcome = Some(Outcome::Victory);
    assert_rejected(&mut s, rot(P1, P2), RuleError::BattleOver);

    // A stale active queue entry for a missing unit reaches the explicit
    // presence check after the actor-control checks.
    let mut s = board(&[(P1, A), (P2, B)], &[P1]);
    s.queue = vec![P3];
    assert_rejected(&mut s, rot(P3, P2), RuleError::NotActiveUnit);
    s.queue = vec![P1];
    assert_rejected(&mut s, rot(P3, P2), RuleError::NotActiveUnit);
}

#[test]
fn invalid_targets_are_rejected_purely() {
    let mut s = board(&[(P1, A), (P2, B), (P3, hex(-2, 2)), (E1, hex(-2, 3))], &[P1]);
    // Invalid id, self, hostile.
    assert_rejected(&mut s, rot(P1, 99), RuleError::InvalidTarget);
    assert_rejected(&mut s, rot(P1, P1), RuleError::InvalidTarget);
    assert_rejected(&mut s, rot(P1, E1), RuleError::InvalidTarget);
    // Dead.
    s.units[P2 as usize].alive = false;
    assert_rejected(&mut s, rot(P1, P2), RuleError::InvalidTarget);
    s.units[P2 as usize].alive = true;
    // Escaped (alive and fled).
    s.units[P2 as usize].fled = true;
    assert_rejected(&mut s, rot(P1, P2), RuleError::InvalidTarget);
    s.units[P2 as usize].fled = false;
    // Fleeing.
    s.units[P2 as usize].morale = Morale::Fleeing;
    assert_rejected(&mut s, rot(P1, P2), RuleError::InvalidTarget);
    s.units[P2 as usize].morale = Morale::Steady;
    // Retreating.
    s.units[P2 as usize].retreating = true;
    assert_rejected(&mut s, rot(P1, P2), RuleError::InvalidTarget);
    s.units[P2 as usize].retreating = false;
    s.apply(rot(P1, P2)).unwrap();
}

#[test]
fn enemy_side_targets_follow_the_same_restrictions() {
    let mut s = board(&[(E1, A), (E2, B), (P1, hex(-2, 2))], &[E1]);
    assert_rejected(&mut s, rot(E1, P1), RuleError::InvalidTarget);
    s.units[E2 as usize].morale = Morale::Fleeing;
    assert_rejected(&mut s, rot(E1, E2), RuleError::InvalidTarget);
}

#[test]
fn stunned_adjacent_same_side_targets_are_invalid_for_both_factions() {
    for (actor, target) in [(P1, P2), (E1, E2)] {
        let mut s = pair(actor, target);
        assert_eq!(s.apply_stun(target), Ok(vec![Event::StunApplied { unit: target }]));
        assert!(s.units[target as usize].stunned);
        assert_rejected(&mut s, rot(actor, target), RuleError::InvalidTarget);
        assert!(s.legal_rotation_targets(actor).is_empty());
    }
}

#[test]
fn stunned_targets_are_rejected_before_coordinates_and_affordability() {
    let mut s = pair(P1, P2);
    s.apply_stun(P2).unwrap();
    s.units[P2 as usize].pos = hex(0, 0); // obstacle; would otherwise fail passability
    s.units[P1 as usize].ap = 0; // would otherwise fail affordability
    assert_rejected(&mut s, rot(P1, P2), RuleError::InvalidTarget);

    // Actor capability retains its earlier precedence over every target check.
    s.units[P1 as usize].has_rotation = false;
    assert_rejected(&mut s, rot(P1, P2), RuleError::MissingRotation);
}

#[test]
fn stunned_candidates_are_excluded_without_hiding_other_allies_or_reordering() {
    let mut s = board(&[(P3, A), (P2, B), (P1, hex(-2, 2))], &[P3]);
    assert_eq!(s.legal_rotation_targets(P3), vec![P1, P2]);
    s.apply_stun(P1).unwrap();
    assert_eq!(s.legal_rotation_targets(P3), vec![P2]);
    assert!(s.preview_rotation(P3, P1).is_err());
    assert!(s.preview_rotation(P3, P2).is_ok());
    assert_eq!(s.apply(rot(P3, P2)), Ok(vec![Event::Rotated { unit: P3, target: P2 }]));
}

#[test]
fn rotation_target_becomes_eligible_after_its_scheduled_stun_expires() {
    let mut s = board(&[(P1, A), (P2, B), (E1, hex(-2, 3))], &[P1]);
    assert_eq!(s.apply_stun(P2), Ok(vec![Event::StunApplied { unit: P2 }]));
    assert!(s.legal_rotation_targets(P1).is_empty());

    let ev = s.apply(Command::EndTurn { unit: P1 }).unwrap();
    assert!(ev.contains(&Event::StunExpired { unit: P2 }));
    assert!(!s.units[P2 as usize].stunned, "the scheduled entry consumes the stun");
    assert_eq!(s.active_unit().map(|u| u.id), Some(E1));

    s.apply(Command::EndTurn { unit: E1 }).unwrap();
    assert_eq!(s.active_unit().map(|u| u.id), Some(P1));
    assert!(!s.units[P2 as usize].stunned);
    assert_eq!(s.legal_rotation_targets(P1), vec![P2]);
    assert!(s.preview_rotation(P1, P2).is_ok());
}

#[test]
fn legal_targets_are_sorted_and_include_finished_waited_and_end_round_allies() {
    let mut s = board(&[(P1, A), (P3, hex(-2, 2)), (P2, B)], &[P1]);
    // P2 has finished and left the queue; P3 is marked by EndRound.
    s.queue = vec![P1];
    s.units[P2 as usize].turn_started = true;
    s.units[P2 as usize].ap = 0;
    s.units[P3 as usize].skip_turn = true;
    assert_eq!(s.legal_rotation_targets(P1), vec![P2, P3]);

    // A real Wait-deferred target also remains eligible.
    let mut s = board(&[(P2, B), (P1, A), (P3, hex(3, -3))], &[P1]);
    s.apply(Command::Wait { unit: P2 }).unwrap();
    assert!(s.units[P2 as usize].waited && s.units[P2 as usize].turn_started);
    assert_eq!(s.legal_rotation_targets(P1), vec![P2]);

    // Ordering is numeric even when the actor has the largest id and either
    // faction can issue the query.
    let s = board(&[(E3, A), (E1, B), (E2, hex(-2, 2))], &[E3]);
    assert_eq!(s.legal_rotation_targets(E3), vec![E1, E2]);
}

#[test]
fn legal_targets_exclude_every_invalid_target_category() {
    let baseline = || board(&[(P1, A), (P2, B), (P3, hex(-2, 2)), (E1, hex(-2, 3))], &[P1]);
    let mut s = baseline();
    assert_eq!(s.legal_rotation_targets(P1), vec![P2, P3]);
    assert_eq!(s.preview_rotation(P1, 99), Err(RuleError::InvalidTarget));
    assert_eq!(s.preview_rotation(P1, P1), Err(RuleError::InvalidTarget));

    s.units[P2 as usize].alive = false;
    assert_eq!(s.legal_rotation_targets(P1), vec![P3]);
    let mut s = baseline();
    s.units[P2 as usize].fled = true;
    assert_eq!(s.legal_rotation_targets(P1), vec![P3]);
    let mut s = baseline();
    s.units[P2 as usize].morale = Morale::Fleeing;
    assert_eq!(s.legal_rotation_targets(P1), vec![P3]);
    let mut s = baseline();
    s.units[P2 as usize].retreating = true;
    assert_eq!(s.legal_rotation_targets(P1), vec![P3]);
    let mut s = baseline();
    s.units[E1 as usize].side = Side::Enemy;
    assert_eq!(s.legal_rotation_targets(P1), vec![P2, P3]);
    assert_eq!(s.preview_rotation(P1, E1), Err(RuleError::InvalidTarget));

    let mut s = baseline();
    s.units[P2 as usize].pos = hex(0, 0); // obstacle
    assert_eq!(s.legal_rotation_targets(P1), vec![P3]);
    let mut s = baseline();
    s.units[P2 as usize].pos = hex(-5, 3); // off map
    assert_eq!(s.legal_rotation_targets(P1), vec![P3]);
    let mut s = baseline();
    s.units[P2 as usize].pos = hex(3, -3); // in map but not adjacent
    assert_eq!(s.legal_rotation_targets(P1), vec![P3]);
}

#[test]
fn legal_targets_refresh_after_position_budget_capability_morale_and_presence_changes() {
    let mut s = board(&[(P1, A), (P2, B), (P3, hex(-2, 3))], &[P1]);
    assert_eq!(s.legal_rotation_targets(P1), vec![P2, P3]);

    // Swapping with P2 updates the plan positions without caching a target set.
    s.apply(rot(P1, P2)).unwrap();
    assert_eq!(s.preview_rotation(P1, P2).unwrap().unit_to, A);
    assert_eq!(s.legal_rotation_targets(P1), vec![P2]);
    s.units[P3 as usize].pos = hex(3, -3);
    assert_eq!(s.legal_rotation_targets(P1), vec![P2]);
    s.units[P3 as usize].pos = hex(-2, 3);
    assert_eq!(s.legal_rotation_targets(P1), vec![P2]);

    s.units[P1 as usize].ap = ROTATION_AP - 1;
    assert!(s.legal_rotation_targets(P1).is_empty());
    s.units[P1 as usize].ap = ROTATION_AP;
    s.units[P1 as usize].fatigue = 100 - ROTATION_FATIGUE + 1;
    assert!(s.legal_rotation_targets(P1).is_empty());
    s.units[P1 as usize].fatigue = 0;
    s.units[P1 as usize].has_rotation = false;
    assert!(s.legal_rotation_targets(P1).is_empty());
    s.units[P1 as usize].has_rotation = true;

    s.units[P2 as usize].morale = Morale::Fleeing;
    assert!(s.legal_rotation_targets(P1).is_empty());
    s.units[P2 as usize].morale = Morale::Steady;
    assert_eq!(s.legal_rotation_targets(P1), vec![P2]);
    s.units[P2 as usize].alive = false;
    assert!(s.legal_rotation_targets(P1).is_empty());
    s.units[P2 as usize].alive = true;
    s.units[P2 as usize].fled = true;
    assert!(s.legal_rotation_targets(P1).is_empty());
}

#[test]
fn preview_and_target_queries_are_repeatable_and_leave_the_full_snapshot_unchanged() {
    let (edge, interior) = (hex(-4, 3), A);
    let mut s = board(&[(P1, interior), (P2, edge), (P3, hex(3, -3))], &[P1]);
    s.round = 8;
    let before = snapshot(&s);
    let first = s.preview_rotation(P1, P2);
    for _ in 0..3 {
        assert_eq!(s.preview_rotation(P1, P2), first);
        assert_eq!(s.legal_rotation_targets(P1), vec![P2]);
    }
    assert_eq!(first.unwrap().edge_penalties, vec![(P1, edge)]);
    assert_eq!(snapshot(&s), before);
}

#[test]
fn legal_targets_are_empty_when_the_actor_cannot_rotate() {
    let mut s = board(&[(P1, A), (P2, B)], &[P1]);
    assert_eq!(s.legal_rotation_targets(P1), vec![P2]);
    s.units[P1 as usize].has_rotation = false;
    assert!(s.legal_rotation_targets(P1).is_empty());
    s.units[P1 as usize].has_rotation = true;
    s.units[P1 as usize].ap = 0;
    assert!(s.legal_rotation_targets(P1).is_empty());
    s.units[P1 as usize].ap = 9;
    s.units[P1 as usize].morale = Morale::Fleeing;
    assert!(s.legal_rotation_targets(P1).is_empty());
    s.units[P1 as usize].morale = Morale::Steady;
    s.units[P1 as usize].retreating = true;
    assert!(s.legal_rotation_targets(P1).is_empty());
    s.units[P1 as usize].retreating = false;
    s.queue.clear();
    assert!(s.legal_rotation_targets(P1).is_empty());
    s.queue = vec![P1];
    s.outcome = Some(Outcome::Victory);
    assert!(s.legal_rotation_targets(P1).is_empty());
    s.outcome = None;
    s.units[P1 as usize].alive = false;
    s.queue = vec![P1];
    assert!(s.legal_rotation_targets(P1).is_empty());
}

#[test]
fn blocked_or_off_map_positions_are_unreachable_before_adjacency() {
    // Obstacle (0, 0) is adjacent to (1, 0): the target stands on it.
    let mut s = board(&[(P1, hex(1, 0)), (P2, hex(0, 0))], &[P1]);
    assert_rejected(&mut s, rot(P1, P2), RuleError::Unreachable);
    // Actor on an obstacle.
    let mut s = board(&[(P1, hex(0, 0)), (P2, hex(1, 0))], &[P1]);
    assert_rejected(&mut s, rot(P1, P2), RuleError::Unreachable);
    // Off-map target, adjacent to an edge actor.
    let mut s = board(&[(P1, hex(-4, 3)), (P2, hex(-5, 3))], &[P1]);
    assert_rejected(&mut s, rot(P1, P2), RuleError::Unreachable);
    // Off-map actor.
    let mut s = board(&[(P1, hex(-5, 3)), (P2, hex(-4, 3))], &[P1]);
    assert_rejected(&mut s, rot(P1, P2), RuleError::Unreachable);
    // Both blocked and distant: still Unreachable, not NotAdjacent.
    let mut s = board(&[(P1, hex(0, 0)), (P2, hex(2, 1))], &[P1]);
    assert_rejected(&mut s, rot(P1, P2), RuleError::Unreachable);
}

#[test]
fn extreme_coordinates_are_unreachable_without_overflow() {
    let extremes = [
        hex(i32::MIN, 0),
        hex(0, i32::MIN),
        hex(i32::MAX, 0),
        hex(0, i32::MAX),
        hex(i32::MIN, i32::MIN),
        hex(i32::MAX, i32::MAX),
        hex(i32::MIN, i32::MAX),
        hex(i32::MAX, i32::MIN),
    ];
    for far in extremes {
        let mut s = board(&[(P1, far), (P2, hex(-4, 3))], &[P1]);
        assert_rejected(&mut s, rot(P1, P2), RuleError::Unreachable);
        let mut s = board(&[(P1, hex(-4, 3)), (P2, far)], &[P1]);
        assert_rejected(&mut s, rot(P1, P2), RuleError::Unreachable);
        let mut s = board(&[(P1, far), (P2, far)], &[P1]);
        assert_rejected(&mut s, rot(P1, P2), RuleError::Unreachable);
    }
}

#[test]
fn nonadjacent_targets_are_rejected() {
    for far in [hex(-3, 1), hex(-1, 3), hex(-4, 1)] {
        let mut s = board(&[(P1, A), (P2, far)], &[P1]);
        assert_rejected(&mut s, rot(P1, P2), RuleError::NotAdjacent);
    }
}

#[test]
fn ap_and_fatigue_limits_and_precedence() {
    // Exact AP of 3 and exact fatigue cap succeed.
    let mut s = pair(P1, P2);
    s.units[P1 as usize].ap = ROTATION_AP;
    s.units[P1 as usize].fatigue = 100 - ROTATION_FATIGUE;
    let preview = s.preview_rotation(P1, P2).unwrap();
    assert_eq!((preview.ap_cost, preview.fatigue_cost), (ROTATION_AP, ROTATION_FATIGUE));
    assert_preview_matches_apply(&s, P1, P2);
    s.apply(rot(P1, P2)).unwrap();
    let u = &s.units[P1 as usize];
    assert_eq!((u.ap, u.fatigue), (0, 100));
    // Zero AP left does not advance the turn.
    assert_eq!(s.queue, vec![P1, P2]);
    assert_eq!(s.active_unit().map(|u| u.id), Some(P1));

    // One AP short.
    let mut s = pair(P1, P2);
    s.units[P1 as usize].ap = ROTATION_AP - 1;
    assert_rejected(&mut s, rot(P1, P2), RuleError::NotEnoughAp);
    // One fatigue over the cap.
    let mut s = pair(P1, P2);
    s.units[P1 as usize].fatigue = 100 - ROTATION_FATIGUE + 1;
    assert_rejected(&mut s, rot(P1, P2), RuleError::TooFatigued);
    // NotEnoughAp wins over TooFatigued.
    let mut s = pair(P1, P2);
    s.units[P1 as usize].ap = 0;
    s.units[P1 as usize].fatigue = 100;
    assert_rejected(&mut s, rot(P1, P2), RuleError::NotEnoughAp);
    // Adjacency wins over budgets.
    let mut s = board(&[(P1, A), (P2, hex(-1, 3))], &[P1]);
    s.units[P1 as usize].ap = 0;
    assert_rejected(&mut s, rot(P1, P2), RuleError::NotAdjacent);
}

#[test]
fn fatigue_addition_overflow_is_rejected_not_wrapped() {
    for fatigue in [u32::MAX, u32::MAX - ROTATION_FATIGUE + 1, u32::MAX - 1] {
        let mut s = pair(P1, P2);
        s.units[P1 as usize].fatigue = fatigue;
        s.units[P1 as usize].stats.fatigue_max = u32::MAX;
        assert_rejected(&mut s, rot(P1, P2), RuleError::TooFatigued);
    }
    // Exactly reaching u32::MAX is still affordable.
    let mut s = pair(P1, P2);
    s.units[P1 as usize].fatigue = u32::MAX - ROTATION_FATIGUE;
    s.units[P1 as usize].stats.fatigue_max = u32::MAX;
    assert_eq!(s.preview_rotation(P1, P2).unwrap().fatigue_cost, ROTATION_FATIGUE);
    assert_preview_matches_apply(&s, P1, P2);
    s.apply(rot(P1, P2)).unwrap();
    assert_eq!(s.units[P1 as usize].fatigue, u32::MAX);
}

#[test]
fn only_the_actor_pays_and_target_budgets_are_preserved() {
    let mut s = pair(P1, P2);
    s.units[P2 as usize].ap = 4;
    s.units[P2 as usize].fatigue = 60;
    s.apply(rot(P1, P2)).unwrap();
    let t = &s.units[P2 as usize];
    assert_eq!((t.ap, t.fatigue), (4, 60));
}

#[test]
fn targets_with_finished_deferred_or_marked_turns_keep_their_scheduling() {
    // Finished: not in the queue, turn_started set.
    let mut s = board(&[(P1, A), (P2, B), (P3, hex(-2, 2))], &[P1]);
    s.queue = vec![P1, P3];
    s.units[P2 as usize].turn_started = true;
    s.units[P2 as usize].ap = 0;
    let (queue, p2) = (s.queue.clone(), s.units[P2 as usize].clone());
    s.apply(rot(P1, P2)).unwrap();
    assert_eq!(s.queue, queue);
    assert_eq!(s.units[P2 as usize], Unit { pos: A, ..p2 });

    // Deferred by Wait: queued behind with `waited` set and the turn consumed.
    let mut s = board(&[(P2, B), (P1, A), (P3, hex(-2, 2))], &[P1]);
    s.apply(Command::Wait { unit: P2 }).unwrap();
    assert_eq!(s.queue, vec![P1, P3, P2]);
    assert!(s.units[P2 as usize].waited && s.units[P2 as usize].turn_started);
    let (queue, p2) = (s.queue.clone(), s.units[P2 as usize].clone());
    s.apply(rot(P1, P2)).unwrap();
    assert_eq!(s.queue, queue);
    assert_eq!(s.units[P2 as usize], Unit { pos: A, ..p2 });

    // Marked by EndRound: skip_turn stays pending for the target.
    let mut s = board(&[(P1, A), (P2, B)], &[P1]);
    s.units[P2 as usize].skip_turn = true;
    let p2 = s.units[P2 as usize].clone();
    s.apply(rot(P1, P2)).unwrap();
    assert_eq!(s.units[P2 as usize], Unit { pos: A, ..p2 });
    assert!(s.units[P2 as usize].skip_turn);
    assert_eq!(s.queue, vec![P1, P2]);
}

#[test]
fn actor_scheduling_flags_and_retreat_intent_are_preserved() {
    let mut s = pair(P1, P2);
    s.units[P1 as usize].waited = true;
    s.units[P1 as usize].skip_turn = true;
    s.units[P2 as usize].retreating = false;
    let (waited, skip, started) = {
        let u = &s.units[P1 as usize];
        (u.waited, u.skip_turn, u.turn_started)
    };
    s.apply(rot(P1, P2)).unwrap();
    let u = &s.units[P1 as usize];
    assert_eq!((u.waited, u.skip_turn, u.turn_started), (waited, skip, started));
    assert!(!u.retreating && !s.units[P2 as usize].retreating);
}

#[test]
fn rotation_bypasses_zone_of_control_without_attacks_or_rng() {
    // E1 and E2 control both hexes; E3 is far away.
    let mut s = board(
        &[(P1, A), (P2, B), (E1, hex(-2, 2)), (E2, hex(-4, 3)), (E3, hex(3, -3))],
        &[P1],
    );
    assert!(s.enemy_controllers_at(P1, A).len() >= 2 && s.enemy_controllers_at(P2, B).len() >= 2);
    assert_eq!(s.enemy_controllers_at(P1, B), vec![E1, E2]);
    let hp: Vec<u32> = s.units.iter().map(|u| u.hp).collect();
    let rng = s.rng.clone();
    let ev = s.apply(rot(P1, P2)).unwrap();
    assert_eq!(ev, vec![Event::Rotated { unit: P1, target: P2 }]);
    assert_eq!(s.rng, rng);
    assert_eq!(s.units.iter().map(|u| u.hp).collect::<Vec<_>>(), hp);
    // Fatigue of the enemies is untouched (no incoming-attack fatigue).
    assert!(s.units.iter().filter(|u| u.side == Side::Enemy).all(|u| u.fatigue == 0));
}

#[test]
fn fixed_costs_ignore_elevation_and_queries_see_the_new_positions() {
    let mut s = board(&[(P1, A), (P2, B), (E1, hex(-2, 2))], &[P1]);
    s.elevations.set(A, 2).unwrap();
    s.elevations.set(B, 0).unwrap();
    // E1 stands next to both hexes: P1 high, E1 and P2 low.
    assert_eq!(s.hit_chance(P1, E1), 70);
    assert_eq!(s.hit_chance(P2, E1), 60);
    assert_eq!(s.hit_chance(E1, P1), 20);
    assert_eq!(s.hit_chance(E1, P2), 40);
    // Rotating the low unit uphill costs exactly ROTATION_AP / ROTATION_FATIGUE.
    s.units[P1 as usize].ap = 9;
    s.apply(rot(P1, P2)).unwrap();
    let u = &s.units[P1 as usize];
    assert_eq!((u.ap, u.fatigue), (9 - ROTATION_AP, ROTATION_FATIGUE));
    assert_eq!(s.hit_chance(P1, E1), 60);
    assert_eq!(s.hit_chance(P2, E1), 70);
    assert_eq!(s.hit_chance(E1, P1), 40);
    assert_eq!(s.hit_chance(E1, P2), 20);
    // Rotating back, downhill, costs the same.
    s.apply(rot(P1, P2)).unwrap();
    assert_eq!(s.units[P1 as usize].ap, 9 - 2 * ROTATION_AP);
    assert_eq!(s.units[P1 as usize].fatigue, 2 * ROTATION_FATIGUE);
}

#[test]
fn controller_surround_and_occupancy_queries_refresh() {
    // E1 touches only A, E2 only B.
    let mut s = board(&[(P1, A), (P2, B), (E1, hex(-4, 4)), (E2, hex(-2, 1))], &[P1]);
    assert_eq!(s.enemy_controllers_at(P1, s.units[P1 as usize].pos), vec![E1]);
    assert_eq!(s.enemy_controllers_at(P2, s.units[P2 as usize].pos), vec![E2]);
    assert_eq!(s.legal_targets(P1), vec![E1]);
    assert_eq!(s.legal_targets(P2), vec![E2]);
    s.apply(rot(P1, P2)).unwrap();
    assert_eq!(s.enemy_controllers_at(P1, s.units[P1 as usize].pos), vec![E2]);
    assert_eq!(s.enemy_controllers_at(P2, s.units[P2 as usize].pos), vec![E1]);
    assert_eq!(s.legal_targets(P1), vec![E2]);
    assert_eq!(s.legal_targets(P2), vec![E1]);
    // Occupancy: both hexes remain occupied, so neither is a move destination.
    let reachable: Vec<Hex> = s.reachable(P1).into_iter().map(|(h, _)| h).collect();
    assert!(!reachable.contains(&A) && !reachable.contains(&B));
    assert_eq!(s.apply(Command::Move { unit: P1, to: A }), Err(RuleError::Unreachable));
}

#[test]
fn surround_bonus_follows_the_swapped_positions() {
    // E1 touches P1's hex and P3, but not P2's hex.
    let mut s = board(&[(P1, hex(-3, 2)), (P2, hex(-3, 3)), (P3, hex(-2, 2)), (E1, hex(-2, 1))], &[P1]);
    assert_eq!((s.hit_chance(P1, E1), s.hit_chance(P2, E1)), (60, 55));
    s.apply(rot(P1, P2)).unwrap();
    assert_eq!((s.hit_chance(P1, E1), s.hit_chance(P2, E1)), (55, 60));
}

fn edge_board(actor_pos: Hex, target_pos: Hex) -> BattleState {
    board(&[(P1, actor_pos), (P2, target_pos)], &[P1])
}

#[test]
fn arrival_on_an_edge_lowers_morale_for_either_participant() {
    let edge = hex(-4, 3);
    // Actor arrives on the edge.
    let mut s = edge_board(A, edge);
    let preview = s.preview_rotation(P1, P2).unwrap();
    assert_eq!(preview.edge_penalties, vec![(P1, edge)]);
    assert_preview_matches_apply(&s, P1, P2);
    let rng = s.rng.clone();
    let ev = s.apply(rot(P1, P2)).unwrap();
    assert_eq!(
        ev,
        vec![
            Event::Rotated { unit: P1, target: P2 },
            Event::EdgeMoraleChanged { unit: P1, at: edge, before: Morale::Steady, after: Morale::Breaking },
        ]
    );
    assert_eq!((s.units[P1 as usize].morale, s.units[P2 as usize].morale), (Morale::Breaking, Morale::Steady));
    assert_eq!(s.rng, rng);

    // Target arrives on the edge (the actor starts on it and leaves it).
    let mut s = edge_board(edge, A);
    let preview = s.preview_rotation(P1, P2).unwrap();
    assert_eq!(preview.edge_penalties, vec![(P2, edge)]);
    assert_preview_matches_apply(&s, P1, P2);
    let ev = s.apply(rot(P1, P2)).unwrap();
    assert_eq!(
        ev,
        vec![
            Event::Rotated { unit: P1, target: P2 },
            Event::EdgeMoraleChanged { unit: P2, at: edge, before: Morale::Steady, after: Morale::Breaking },
        ]
    );
    assert_eq!((s.units[P1 as usize].morale, s.units[P2 as usize].morale), (Morale::Steady, Morale::Breaking));
}

#[test]
fn both_edge_arrivals_are_ordered_by_unit_id_and_nobody_flees() {
    let (e1, e2) = (hex(-4, 3), hex(-4, 4));
    // Actor has the higher id than the target; ascending order still applies.
    let mut s = board(&[(P2, e1), (P1, e2)], &[P2]);
    s.units[P1 as usize].morale = Morale::Wavering;
    let preview = s.preview_rotation(P2, P1).unwrap();
    assert_eq!(preview.edge_penalties, vec![(P1, e1), (P2, e2)]);
    assert_preview_matches_apply(&s, P2, P1);
    let ev = s.apply(rot(P2, P1)).unwrap();
    assert_eq!(
        ev,
        vec![
            Event::Rotated { unit: P2, target: P1 },
            Event::EdgeMoraleChanged { unit: P1, at: e1, before: Morale::Wavering, after: Morale::Breaking },
            Event::EdgeMoraleChanged { unit: P2, at: e2, before: Morale::Steady, after: Morale::Breaking },
        ]
    );
    assert!(s.units.iter().all(|u| !u.fled) && s.units[P1 as usize].is_present() && s.units[P2 as usize].is_present());
    assert_eq!(s.queue, vec![P2, P1]);
    assert!(s.outcome().is_none());
}

#[test]
fn already_breaking_participants_and_edge_departures_emit_no_edge_event() {
    let edge = hex(-4, 3);
    let mut s = edge_board(A, edge);
    s.units[P1 as usize].morale = Morale::Breaking;
    assert_eq!(s.preview_rotation(P1, P2).unwrap().edge_penalties, vec![]);
    assert_preview_matches_apply(&s, P1, P2);
    let ev = s.apply(rot(P1, P2)).unwrap();
    assert_eq!(ev, vec![Event::Rotated { unit: P1, target: P2 }]);
    assert_eq!(s.units[P1 as usize].morale, Morale::Breaking);
    // Moving off the edge changes nothing either.
    let mut s = edge_board(edge, A);
    s.units[P2 as usize].morale = Morale::Breaking;
    assert_eq!(s.preview_rotation(P1, P2).unwrap().edge_penalties, vec![]);
    assert_preview_matches_apply(&s, P1, P2);
    assert_eq!(s.apply(rot(P1, P2)).unwrap().len(), 1);
    // Interior swap: no edge event.
    assert_eq!(pair(P1, P2).apply(rot(P1, P2)).unwrap().len(), 1);
}

#[test]
fn rotation_coexists_with_pending_adrenaline() {
    let mut s = board(&[(P1, A), (P2, B)], &[P1]);
    s.units[P1 as usize].has_adrenaline = true;
    s.apply(Command::Adrenaline { unit: P1 }).unwrap();
    assert!(s.units[P1 as usize].adrenaline_pending);
    s.units[P2 as usize].has_adrenaline = true;
    s.units[P2 as usize].adrenaline_pending = true;
    s.apply(rot(P1, P2)).unwrap();
    for id in [P1, P2] {
        let u = &s.units[id as usize];
        assert!(u.has_adrenaline && u.adrenaline_pending);
    }
    let u = &s.units[P1 as usize];
    assert_eq!((u.ap, u.fatigue), (9 - 1 - ROTATION_AP, 20 + ROTATION_FATIGUE));
}

#[test]
fn has_rotation_is_an_independent_hash_bit() {
    let base = scenario::battle(1, TerrainPreset::Flat);
    assert_eq!(base.state_hash(), 0x8c00302113cbe668, "false capability must not change hashes");
    let hills = scenario::battle(1, TerrainPreset::Hills);
    assert_eq!(hills.state_hash(), 0xb759683cdff4bfa2);
    let mut rotation = base.clone();
    rotation.units[0].has_rotation = true;
    let mut adrenaline = base.clone();
    adrenaline.units[0].has_adrenaline = true;
    let mut both = rotation.clone();
    both.units[0].has_adrenaline = true;
    let hashes = [base.state_hash(), rotation.state_hash(), adrenaline.state_hash(), both.state_hash()];
    for (i, a) in hashes.iter().enumerate() {
        for b in &hashes[i + 1..] {
            assert_ne!(a, b);
        }
    }
    // Clearing the flag restores the original hash.
    rotation.units[0].has_rotation = false;
    assert_eq!(rotation.state_hash(), base.state_hash());
}
