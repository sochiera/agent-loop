//! Recover action (partial UC-COMBAT-065 coverage). Fatigue halving is
//! RECONSTRUCTED from static evidence; the 9 AP cost and whole-turn action are
//! catalog-stated without original-runtime confirmation. Rounding, surplus AP
//! forfeiture, zero-fatigue acceptance and ungated availability are
//! APPROXIMATION choices.

use bb_rules::hex::hex;
use bb_rules::rules::RECOVER_AP;
use bb_rules::*;

const PLAYERS: [u32; 2] = [0, 3];

fn snapshot(
    s: &BattleState,
) -> (
    u64,
    Vec<Unit>,
    Rng,
    u32,
    Vec<u32>,
    Option<Outcome>,
    Option<EndReason>,
) {
    (
        s.state_hash(),
        s.units.clone(),
        s.rng.clone(),
        s.round,
        s.queue.clone(),
        s.outcome,
        s.end_reason,
    )
}

fn activate(s: &mut BattleState, unit: u32, ap: u32) {
    s.queue.retain(|&id| id != unit);
    s.queue.insert(0, unit);
    let u = &mut s.units[unit as usize];
    u.ap = ap;
    // Surplus-AP fixtures raise the maximum so resources stay legal.
    u.stats.ap_max = u.stats.ap_max.max(ap);
    u.turn_started = true;
}

fn assert_within_maxima(s: &BattleState, unit: u32) {
    let u = &s.units[unit as usize];
    assert!(u.ap <= u.stats.ap_max, "AP {} > max {}", u.ap, u.stats.ap_max);
    assert!(u.fatigue <= u.stats.fatigue_max, "fatigue {} > max {}", u.fatigue, u.stats.fatigue_max);
}

#[test]
fn recover_costs_nine_ap_and_completes_the_active_turn_for_both_sides() {
    assert_eq!(RECOVER_AP, 9);
    for unit in PLAYERS {
        let mut s = BattleState::new(11);
        activate(&mut s, unit, RECOVER_AP);
        s.units[unit as usize].fatigue = 73;
        s.units[unit as usize].stats.fatigue_max = 100;
        let rng = s.rng.clone();

        s.apply(Command::Recover { unit }).unwrap();

        assert_eq!(s.units[unit as usize].fatigue, 36);
        assert_eq!(s.units[unit as usize].ap, 0);
        assert!(!s.queue.contains(&unit));
        assert_ne!(s.active_unit().unwrap().id, unit);
        assert_eq!(s.rng, rng, "Recover itself draws no random numbers");
        let after = snapshot(&s);
        assert_eq!(
            s.apply(Command::EndTurn { unit }),
            Err(RuleError::NotActiveUnit)
        );
        assert_eq!(snapshot(&s), after);
    }
}

#[test]
fn recover_rejects_zero_through_eight_ap_without_mutation_for_both_sides() {
    for unit in PLAYERS {
        for ap in 0..RECOVER_AP {
            let mut s = BattleState::new(12);
            activate(&mut s, unit, ap);
            s.units[unit as usize].fatigue = 57;
            let before = snapshot(&s);

            assert_eq!(
                s.apply(Command::Recover { unit }),
                Err(RuleError::NotEnoughAp),
                "AP {ap}, unit {unit}"
            );
            assert_eq!(snapshot(&s), before, "AP {ap}, unit {unit}");
        }
    }
}

#[test]
fn recover_forfeits_surplus_ap_without_overflow_for_both_sides() {
    for unit in PLAYERS {
        for ap in [RECOVER_AP + 1, u32::MAX] {
            let mut s = BattleState::new(13);
            activate(&mut s, unit, ap);
            s.units[unit as usize].fatigue = 2;
            s.units[unit as usize].stats.fatigue_max = 2;
            assert_within_maxima(&s, unit);

            s.apply(Command::Recover { unit }).unwrap();

            assert_eq!(s.units[unit as usize].ap, 0, "AP {ap}, unit {unit}");
            assert_eq!(s.units[unit as usize].fatigue, 1, "AP {ap}, unit {unit}");
        }
    }
}

#[test]
fn recover_halves_zero_one_even_odd_and_max_fatigue_by_integer_division() {
    for unit in PLAYERS {
        for fatigue in [0, 1, 40, 41, u32::MAX] {
            let mut s = BattleState::new(14);
            activate(&mut s, unit, RECOVER_AP);
            s.units[unit as usize].fatigue = fatigue;
            s.units[unit as usize].stats.fatigue_max = fatigue;

            s.apply(Command::Recover { unit }).unwrap();

            assert_eq!(
                s.units[unit as usize].fatigue,
                fatigue / 2,
                "fatigue {fatigue}, unit {unit}"
            );
        }
    }
}

#[test]
fn recover_matches_manual_halving_then_end_turn_with_remaining_queue() {
    let mut start = BattleState::new(15);
    let unit = start.active_unit().unwrap().id;
    start.units[unit as usize].ap = 15;
    start.units[unit as usize].fatigue = 57;
    start.units[unit as usize].stats.fatigue_max = 100;
    start.units[unit as usize].waited = true;
    start.units[unit as usize].skip_turn = true;
    start.units[unit as usize].berserk_used = true;
    start.units[unit as usize].indomitable_active = true;

    let mut recovered = start.clone();
    let mut reference = start;
    let recovered_events = recovered.apply(Command::Recover { unit }).unwrap();
    reference.units[unit as usize].fatigue /= 2;
    reference.units[unit as usize].ap = 0;
    let reference_events = reference.apply(Command::EndTurn { unit }).unwrap();

    assert_eq!(recovered_events, reference_events);
    assert_eq!(snapshot(&recovered), snapshot(&reference));
    assert_eq!(recovered.units[unit as usize].fatigue, 28);
    assert_eq!(recovered.units[unit as usize].ap, 0);
    assert_eq!(recovered.units[unit as usize].waited, true);
    assert_eq!(recovered.units[unit as usize].skip_turn, true);
    assert_eq!(recovered.units[unit as usize].berserk_used, true);
    assert_eq!(recovered.units[unit as usize].indomitable_active, true);
}

#[test]
fn recover_uses_normal_advancement_for_a_skipped_next_actor() {
    let mut start = BattleState::new(23);
    let unit = start.active_unit().unwrap().id;
    let skipped = start.queue[1];
    start.units[unit as usize].fatigue = 31;
    start.units[skipped as usize].skip_turn = true;

    let mut recovered = start.clone();
    let mut reference = start;
    let recovered_events = recovered.apply(Command::Recover { unit }).unwrap();
    reference.units[unit as usize].fatigue /= 2;
    reference.units[unit as usize].ap = 0;
    let reference_events = reference.apply(Command::EndTurn { unit }).unwrap();

    assert_eq!(recovered_events, reference_events);
    assert_eq!(snapshot(&recovered), snapshot(&reference));
    assert!(!recovered.queue.contains(&skipped));
    assert_eq!(recovered.units[skipped as usize].ap, 0);
    assert_ne!(recovered.active_unit().unwrap().id, skipped);
}

#[test]
fn recover_matches_end_turn_and_normal_refresh_on_round_rollover() {
    let mut start = BattleState::new(16);
    let unit = start.active_unit().unwrap().id;
    for other in &mut start.units {
        if other.id != unit {
            other.alive = false;
        }
    }
    start.queue = vec![unit];
    start.units[unit as usize].ap = 12;
    start.units[unit as usize].fatigue = 41;
    start.units[unit as usize].stats.fatigue_max = 100;

    let mut recovered = start.clone();
    let mut reference = start;
    let recovered_events = recovered.apply(Command::Recover { unit }).unwrap();
    reference.units[unit as usize].fatigue /= 2;
    reference.units[unit as usize].ap = 0;
    let reference_events = reference.apply(Command::EndTurn { unit }).unwrap();

    assert_eq!(recovered_events, reference_events);
    assert_eq!(snapshot(&recovered), snapshot(&reference));
    assert_eq!(recovered.round, 2);
    assert_eq!(recovered.active_unit().unwrap().id, unit);
    assert_eq!(
        recovered.units[unit as usize].ap,
        recovered.units[unit as usize].stats.ap_max
    );
    assert_eq!(
        recovered.units[unit as usize].fatigue, 5,
        "halve 41 to 20 before the normal 15-point regen"
    );
}

#[test]
fn recover_advances_through_normal_next_turn_self_rally_and_rng_processing() {
    let mut start = BattleState::new(17);
    let unit = start.active_unit().unwrap().id;
    let rallying = start.queue[1];
    start.units[rallying as usize].morale = Morale::Fleeing;
    start.units[rallying as usize].stats.resolve = 100;
    start.units[rallying as usize].pos = hex(0, 0);
    start.units[3].pos = hex(3, -3);

    let mut recovered = start.clone();
    let mut reference = start;
    let recovered_events = recovered.apply(Command::Recover { unit }).unwrap();
    reference.units[unit as usize].fatigue /= 2;
    reference.units[unit as usize].ap = 0;
    let reference_events = reference.apply(Command::EndTurn { unit }).unwrap();

    assert_eq!(recovered_events, reference_events);
    assert!(
        recovered_events.iter().any(
            |event| matches!(event, Event::SelfRallyChecked { unit: id, .. } if *id == rallying)
        )
    );
    assert_eq!(snapshot(&recovered), snapshot(&reference));
}

#[test]
fn waited_unit_can_recover_on_its_return_without_an_extra_turn_refresh() {
    let mut s = BattleState::new(18);
    let unit = s.active_unit().unwrap().id;
    s.units[unit as usize].ap = RECOVER_AP;
    s.units[unit as usize].fatigue = 40;
    s.apply(Command::Wait { unit }).unwrap();

    while s.active_unit().unwrap().id != unit {
        let next = s.active_unit().unwrap().id;
        s.apply(Command::EndTurn { unit: next }).unwrap();
    }
    assert_eq!(s.round, 1);
    assert_eq!(s.units[unit as usize].ap, RECOVER_AP);
    assert_eq!(
        s.units[unit as usize].fatigue, 40,
        "Wait return does not refresh again"
    );

    s.apply(Command::Recover { unit }).unwrap();

    assert_eq!(s.round, 2);
    assert_ne!(s.active_unit().unwrap().id, unit);
    assert_eq!(s.units[unit as usize].ap, 0);
    assert_eq!(s.units[unit as usize].fatigue, 20);
}

#[test]
fn recover_preserves_actor_gate_precedence_and_rejected_state_and_rng() {
    // Inactive actor is rejected before its AP is considered.
    let mut inactive = BattleState::new(19);
    let actor = inactive.queue[1];
    inactive.units[actor as usize].ap = 0;
    assert_rejected_unchanged(&mut inactive, actor, RuleError::NotActiveUnit);

    // A stale queue entry for a dead or escaped actor passes the actor/control
    // checks, then fails the Recover presence gate before its AP check.
    for escaped in [false, true] {
        let mut absent = BattleState::new(20);
        let actor = absent.active_unit().unwrap().id;
        absent.units[actor as usize].ap = 0;
        if escaped {
            absent.units[actor as usize].fled = true;
        } else {
            absent.units[actor as usize].alive = false;
        }
        assert_rejected_unchanged(&mut absent, actor, RuleError::NotActiveUnit);
    }

    for (fleeing, retreating, expected) in [
        (true, false, RuleError::FleeingUnit),
        (false, true, RuleError::RetreatingUnit),
        (true, true, RuleError::RetreatingUnit),
    ] {
        let mut s = BattleState::new(21);
        let actor = s.active_unit().unwrap().id;
        if fleeing {
            s.units[actor as usize].morale = Morale::Fleeing;
        }
        s.units[actor as usize].retreating = retreating;
        assert_rejected_unchanged(&mut s, actor, expected);
    }

    let mut over = BattleState::new(22);
    let actor = over.active_unit().unwrap().id;
    over.units[actor as usize].ap = 0;
    over.outcome = Some(Outcome::Victory);
    over.end_reason = Some(EndReason::Elimination);
    assert_rejected_unchanged(&mut over, actor, RuleError::BattleOver);
}

fn assert_rejected_unchanged(s: &mut BattleState, unit: u32, expected: RuleError) {
    let before = snapshot(s);
    assert_eq!(s.recover_availability(unit), Err(expected));
    assert_eq!(snapshot(s), before, "the query is pure");
    assert_eq!(s.apply(Command::Recover { unit }), Err(expected));
    assert_eq!(snapshot(s), before);
}

#[test]
fn recover_availability_is_pure_and_agrees_with_execution_on_success() {
    for unit in PLAYERS {
        for (ap, fatigue) in [(RECOVER_AP, 0), (RECOVER_AP, 1), (RECOVER_AP + 1, 41), (u32::MAX, 100)] {
            let mut s = BattleState::new(24);
            activate(&mut s, unit, ap);
            s.units[unit as usize].fatigue = fatigue;
            s.units[unit as usize].stats.fatigue_max = 100;
            let before = snapshot(&s);

            assert_within_maxima(&s, unit);
            assert_eq!(s.recover_availability(unit), Ok(()), "AP {ap}, fatigue {fatigue}");
            assert_eq!(s.recover_availability(unit), Ok(()), "repeatable");
            assert_eq!(snapshot(&s), before, "the query is pure");
            assert!(s.apply(Command::Recover { unit }).is_ok(), "AP {ap}, fatigue {fatigue}");
            assert_eq!(s.units[unit as usize].fatigue, fatigue / 2);
        }
    }
}

#[test]
fn recover_availability_agrees_with_execution_for_insufficient_ap_and_inactive_or_unknown_units() {
    for unit in PLAYERS {
        for ap in 0..RECOVER_AP {
            let mut s = BattleState::new(25);
            activate(&mut s, unit, ap);
            assert_rejected_unchanged(&mut s, unit, RuleError::NotEnoughAp);
        }
    }
    let mut s = BattleState::new(26);
    let unknown = s.units.len() as u32;
    assert_eq!(s.recover_availability(unknown), Err(RuleError::NotActiveUnit));
    assert_eq!(s.recover_availability(u32::MAX), Err(RuleError::NotActiveUnit));
    // Controllability is reported before AP: a Fleeing actor with no AP is Fleeing.
    let actor = s.active_unit().unwrap().id;
    s.units[actor as usize].ap = 0;
    s.units[actor as usize].morale = Morale::Fleeing;
    assert_rejected_unchanged(&mut s, actor, RuleError::FleeingUnit);
}
