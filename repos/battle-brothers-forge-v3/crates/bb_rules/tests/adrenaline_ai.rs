//! Conservative deterministic AI use of Adrenaline before an affordable adjacent attack.

use bb_rules::ai;
use bb_rules::hex::hex;
use bb_rules::rules::{ADRENALINE_AP, ADRENALINE_FATIGUE, ATTACK_AP, ATTACK_FATIGUE};
use bb_rules::*;

fn fixture(actor_id: u32, targets: &[u32], adrenaline: bool) -> BattleState {
    let mut state = BattleState::new(41);
    for unit in &mut state.units {
        unit.alive = false;
        unit.fled = false;
        unit.retreating = false;
        unit.adrenaline_pending = false;
    }

    state.outcome = None;
    state.end_reason = None;
    state.round = 1;
    state.queue = vec![actor_id];

    let actor = &mut state.units[actor_id as usize];
    actor.alive = true;
    actor.pos = hex(0, -2);
    actor.ap = actor.stats.ap_max;
    actor.fatigue = 0;
    actor.turn_started = true;
    actor.has_adrenaline = adrenaline;

    for (i, &target_id) in targets.iter().enumerate() {
        let target = &mut state.units[target_id as usize];
        target.alive = true;
        target.pos = [hex(1, -2), hex(0, -1)][i];
        target.hp = if i == 0 { 20 } else { 10 };
    }
    assert_fixture_positions_are_valid(&state);
    state
}

fn assert_fixture_positions_are_valid(state: &BattleState) {
    let mut occupied = Vec::new();
    for unit in state.units.iter().filter(|unit| unit.is_present()) {
        assert!(
            unit.pos.in_map(),
            "present unit {} is off-map at {:?}",
            unit.id,
            unit.pos
        );
        assert!(
            !unit.pos.is_obstacle(),
            "present unit {} occupies obstacle hex {:?}",
            unit.id,
            unit.pos
        );
        assert!(
            !occupied.contains(&unit.pos),
            "present unit {} shares hex {:?}",
            unit.id,
            unit.pos
        );
        occupied.push(unit.pos);
    }
}

#[test]
fn actor_and_target_fixture_geometry_is_legal_for_both_factions() {
    for (actor, targets) in [(0, [3, 4]), (3, [0, 1])] {
        let state = fixture(actor, &targets, true);
        assert_eq!(state.units[actor as usize].pos, hex(0, -2));
        assert_eq!(state.units[targets[0] as usize].pos, hex(1, -2));
        assert_eq!(state.units[targets[1] as usize].pos, hex(0, -1));
        assert_eq!(state.units[targets[0] as usize].hp, 20);
        assert_eq!(state.units[targets[1] as usize].hp, 10);
        assert!(targets.iter().all(|&target| {
            state.units[actor as usize]
                .pos
                .distance(state.units[target as usize].pos)
                == 1
        }));
    }
}

fn snapshot(
    state: &BattleState,
) -> (
    Vec<Unit>,
    Rng,
    u32,
    Vec<u32>,
    Option<Outcome>,
    Option<EndReason>,
    ElevationMap,
) {
    (
        state.units.clone(),
        state.rng.clone(),
        state.round,
        state.queue.clone(),
        state.outcome,
        state.end_reason,
        state.elevations.clone(),
    )
}

#[test]
fn either_side_uses_adrenaline_then_attacks_the_weakest_adjacent_target() {
    for (actor, targets) in [(0, [3, 4]), (3, [0, 1])] {
        let mut state = fixture(actor, &targets, true);
        let expected_adrenaline = vec![Command::Adrenaline { unit: actor }];
        assert_eq!(ai::plan_turn(&state), expected_adrenaline, "actor {actor}");
        let activation = state.apply(expected_adrenaline[0]).unwrap();
        assert_eq!(activation, vec![Event::AdrenalineUsed { unit: actor }]);

        let expected_attack = vec![Command::Attack {
            attacker: actor,
            target: targets[1],
        }];
        assert_eq!(
            ai::plan_turn(&state),
            expected_attack,
            "pending Adrenaline must not repeat"
        );
        let attack = state.apply(expected_attack[0]).unwrap();
        assert!(
            matches!(attack.first(), Some(Event::Attacked { attacker, target, .. }) if *attacker == actor && *target == targets[1])
        );
    }
}

#[test]
fn combined_ap_and_fatigue_budgets_use_exact_limits_and_fall_back_one_short() {
    let total_ap = ADRENALINE_AP + ATTACK_AP;
    let total_fatigue = ADRENALINE_FATIGUE + ATTACK_FATIGUE;

    let mut state = fixture(0, &[3, 4], true);
    state.units[0].ap = total_ap;
    state.units[0].stats.fatigue_max = total_fatigue;
    state.units[0].fatigue = 0;
    assert_eq!(
        ai::plan_turn(&state),
        vec![Command::Adrenaline { unit: 0 }],
        "exact combined AP and fatigue budgets"
    );

    let mut state = fixture(0, &[3, 4], true);
    state.units[0].ap = total_ap - 1;
    assert_eq!(
        ai::plan_turn(&state),
        vec![Command::Attack {
            attacker: 0,
            target: 4
        }],
        "one AP short preserves the attack policy"
    );

    let mut state = fixture(0, &[3, 4], true);
    state.units[0].stats.fatigue_max = total_fatigue;
    state.units[0].fatigue = 1;
    assert_eq!(
        ai::plan_turn(&state),
        vec![Command::Attack {
            attacker: 0,
            target: 4
        }],
        "one fatigue over the combined cap preserves the attack policy"
    );
}

#[test]
fn combined_fatigue_budget_is_checked_at_u32_max() {
    let total_fatigue = ADRENALINE_FATIGUE + ATTACK_FATIGUE;
    let mut state = fixture(0, &[3, 4], true);
    state.units[0].stats.fatigue_max = u32::MAX;
    state.units[0].fatigue = u32::MAX - total_fatigue;
    assert_eq!(
        ai::plan_turn(&state),
        vec![Command::Adrenaline { unit: 0 }],
        "exact u32::MAX boundary"
    );

    state.units[0].fatigue += 1;
    assert_eq!(
        ai::plan_turn(&state),
        vec![Command::Attack {
            attacker: 0,
            target: 4
        }],
        "one over u32::MAX fatigue cap must not wrap"
    );
}

#[test]
fn missing_capability_pending_effect_and_no_target_keep_existing_policy() {
    let mut state = fixture(0, &[3, 4], false);
    assert_eq!(
        ai::plan_turn(&state),
        vec![Command::Attack {
            attacker: 0,
            target: 4
        }]
    );

    state.units[0].has_adrenaline = true;
    state.units[0].adrenaline_pending = true;
    assert_eq!(
        ai::plan_turn(&state),
        vec![Command::Attack {
            attacker: 0,
            target: 4
        }]
    );

    state.units[3].alive = false;
    state.units[4].alive = false;
    assert_eq!(ai::plan_turn(&state), vec![Command::EndTurn { unit: 0 }]);
}

#[test]
fn retreat_and_fleeing_keep_priority_over_adrenaline() {
    let mut state = fixture(0, &[3, 4], true);
    state.units[0].retreating = true;
    assert_eq!(ai::plan_turn(&state), vec![Command::Withdraw { unit: 0 }]);

    state.units[0].retreating = false;
    state.units[0].morale = Morale::Fleeing;
    assert_eq!(ai::plan_turn(&state), vec![Command::Flee { unit: 0 }]);
}

#[test]
fn repeated_planning_is_pure_and_pending_prevents_reactivation_after_budget_runs_out() {
    let mut state = fixture(0, &[3, 4], true);
    let before = snapshot(&state);
    let first = ai::plan_turn(&state);
    let second = ai::plan_turn(&state);
    assert_eq!(first, second);
    assert_eq!(first, vec![Command::Adrenaline { unit: 0 }]);
    assert_eq!(
        snapshot(&state),
        before,
        "planning preserves the complete state and RNG"
    );

    state.units[0].ap = ADRENALINE_AP + ATTACK_AP;
    state.units[0].stats.fatigue_max = ADRENALINE_FATIGUE + ATTACK_FATIGUE;
    state.apply(Command::Adrenaline { unit: 0 }).unwrap();
    let attack = ai::plan_turn(&state);
    assert_eq!(
        attack,
        vec![Command::Attack {
            attacker: 0,
            target: 4
        }]
    );
    state.apply(attack[0]).unwrap();
    assert_eq!(state.units[0].ap, 0);
    assert!(state.units[0].adrenaline_pending);
    assert_eq!(
        ai::plan_turn(&state),
        vec![Command::EndTurn { unit: 0 }],
        "pending plus exhausted resources cannot loop into another activation"
    );
}
