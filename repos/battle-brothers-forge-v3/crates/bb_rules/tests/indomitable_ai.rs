//! Conservative deterministic AI activation of Indomitable before an affordable adjacent attack.
//! The health threshold, current-threat gate, attack-budget reservation and priority over
//! Adrenaline are **APPROXIMATION** inspired by UC-COMBAT-020/016, not original-AI parity.

use bb_rules::ai;
use bb_rules::hex::{hex, map_hexes};
use bb_rules::rules::{
    ATTACK_AP, ATTACK_FATIGUE, BERSERK_AP_REFUND, INDOMITABLE_AP, INDOMITABLE_FATIGUE,
    adrenaline_availability, indomitable_availability,
};
use bb_rules::*;

const POSITIONS: [Hex; 3] = [hex(0, -2), hex(1, -2), hex(0, -1)];

fn fixture(actor: u32) -> BattleState {
    let targets = if actor < 3 { [3, 4] } else { [0, 1] };
    let mut state = BattleState::new(41);
    for unit in &mut state.units {
        unit.alive = false;
        unit.fled = false;
        unit.retreating = false;
        unit.indomitable_active = false;
    }
    state.outcome = None;
    state.end_reason = None;
    state.round = 1;
    state.queue = vec![actor];

    let active = &mut state.units[actor as usize];
    active.alive = true;
    active.pos = POSITIONS[0];
    active.hp = active.stats.hp / 2;
    active.ap = active.stats.ap_max;
    active.fatigue = 0;
    active.turn_started = true;
    active.has_indomitable = true;

    for (i, target_id) in targets.into_iter().enumerate() {
        let target = &mut state.units[target_id as usize];
        target.alive = true;
        target.pos = POSITIONS[i + 1];
        target.hp = if i == 0 { 20 } else { 10 };
    }
    assert_fixture_valid(&state);
    state
}

fn assert_fixture_valid(state: &BattleState) {
    let present: Vec<&Unit> = state.units.iter().filter(|u| u.is_present()).collect();
    let mut occupied = Vec::new();
    for unit in present {
        assert!(
            map_hexes().contains(&unit.pos) && !unit.pos.is_obstacle(),
            "{} at {:?}",
            unit.name,
            unit.pos
        );
        assert!(
            !occupied.contains(&unit.pos),
            "duplicate present position {:?}",
            unit.pos
        );
        occupied.push(unit.pos);
        assert!(
            unit.hp > 0 && unit.hp <= unit.stats.hp,
            "{} HP {}/{}",
            unit.name,
            unit.hp,
            unit.stats.hp
        );
        assert!(unit.armour <= unit.stats.armour, "{} armour", unit.name);
        assert!(unit.ap <= unit.stats.ap_max, "{} AP", unit.name);
        assert!(
            unit.fatigue <= unit.stats.fatigue_max,
            "{} fatigue",
            unit.name
        );
    }
}

fn targets_for(actor: u32) -> [u32; 2] {
    if actor < 3 { [3, 4] } else { [0, 1] }
}

fn attack(actor: u32, target: u32) -> Command {
    Command::Attack {
        attacker: actor,
        target,
    }
}

fn snapshot(
    state: &BattleState,
) -> (
    Vec<Unit>,
    Rng,
    Vec<u32>,
    u32,
    Option<Outcome>,
    Option<EndReason>,
    ElevationMap,
) {
    (
        state.units.clone(),
        state.rng.clone(),
        state.queue.clone(),
        state.round,
        state.outcome,
        state.end_reason,
        state.elevations.clone(),
    )
}

#[test]
fn both_factions_activate_then_attack_the_weakest_adjacent_target() {
    for actor in [0, 3] {
        let targets = targets_for(actor);
        let mut state = fixture(actor);
        state.units[actor as usize].has_adrenaline = true;
        assert_eq!(
            state.units[actor as usize].hp * 2,
            state.units[actor as usize].stats.hp
        );
        assert!(
            state
                .enemy_controllers_at(actor, state.units[actor as usize].pos)
                .len()
                > 0
        );
        assert_fixture_valid(&state);

        let before = snapshot(&state);
        let expected = Command::Indomitable { unit: actor };
        assert_eq!(
            ai::plan_turn(&state),
            vec![expected],
            "Indomitable wins over Adrenaline for {actor}"
        );
        assert_eq!(
            ai::plan_turn(&state),
            vec![expected],
            "planning is repeatable"
        );
        assert_eq!(snapshot(&state), before, "planning preserves state and RNG");

        assert_eq!(
            state.apply(expected),
            Ok(vec![Event::IndomitableUsed { unit: actor }])
        );
        assert_eq!(
            (
                state.units[actor as usize].ap,
                state.units[actor as usize].fatigue
            ),
            (9 - INDOMITABLE_AP, INDOMITABLE_FATIGUE)
        );
        assert!(state.units[actor as usize].is_indomitable_active());
        assert_eq!(
            indomitable_availability(&state.units[actor as usize]),
            Err(RuleError::IndomitableActive)
        );

        let next = attack(actor, targets[1]);
        assert_eq!(
            ai::plan_turn(&state),
            vec![next],
            "the remaining normal-turn budget buys the weakest-target attack"
        );
        assert!(
            state.apply(next).is_ok(),
            "AI's generated attack is accepted"
        );

        // Even if resources are restored by a test harness, the active stance blocks a duplicate cast.
        state.units[actor as usize].ap = state.units[actor as usize].stats.ap_max;
        state.units[actor as usize].fatigue = 0;
        state.units[actor as usize].has_adrenaline = false;
        let weakest = state
            .legal_targets(actor)
            .into_iter()
            .min_by_key(|&target| (state.units[target as usize].hp, target))
            .unwrap();
        assert_eq!(ai::plan_turn(&state), vec![attack(actor, weakest)]);
    }
}

#[test]
fn health_threshold_includes_half_and_handles_odd_and_maximum_hp() {
    let mut state = fixture(0);
    state.units[0].stats.hp = 100;
    state.units[0].hp = 50;
    assert_eq!(
        ai::plan_turn(&state),
        vec![Command::Indomitable { unit: 0 }],
        "exact half is included"
    );
    state.units[0].hp = 51;
    assert_eq!(
        ai::plan_turn(&state),
        vec![attack(0, 4)],
        "one HP above half is excluded"
    );

    state.units[0].stats.hp = 101;
    state.units[0].hp = 50;
    assert_eq!(
        ai::plan_turn(&state),
        vec![Command::Indomitable { unit: 0 }],
        "2*50 <= odd maximum 101"
    );
    state.units[0].hp = 51;
    assert_eq!(
        ai::plan_turn(&state),
        vec![attack(0, 4)],
        "2*51 > odd maximum 101"
    );

    state.units[0].stats.hp = u32::MAX;
    state.units[0].hp = u32::MAX / 2;
    assert_eq!(
        ai::plan_turn(&state),
        vec![Command::Indomitable { unit: 0 }],
        "u32::MAX maximum without multiplication overflow"
    );
    state.units[0].hp = u32::MAX / 2 + 1;
    assert_eq!(
        ai::plan_turn(&state),
        vec![attack(0, 4)],
        "one above the widened half threshold"
    );

    state.units[0].stats.hp = 100;
    state.units[0].hp = 0;
    assert_eq!(
        ai::plan_turn(&state),
        vec![attack(0, 4)],
        "activation requires positive HP"
    );
}

#[test]
fn activation_requires_a_current_non_fleeing_enemy_controller() {
    for excluded in ["fleeing only", "dead controller", "escaped controller"] {
        let mut state = fixture(0);
        match excluded {
            "fleeing only" => {
                state.units[3].morale = Morale::Fleeing;
                state.units[4].morale = Morale::Fleeing;
            }
            "dead controller" => {
                state.units[3].morale = Morale::Fleeing;
                state.units[4].alive = false;
            }
            "escaped controller" => {
                state.units[3].morale = Morale::Fleeing;
                state.units[4].fled = true;
            }
            _ => unreachable!(),
        }
        assert!(
            !state
                .enemy_controllers_at(0, state.units[0].pos)
                .contains(&3)
        );
        assert!(
            !state
                .enemy_controllers_at(0, state.units[0].pos)
                .contains(&4)
        );
        assert!(
            !state.legal_targets(0).is_empty(),
            "the adjacent Fleeing unit remains a legal target"
        );
        let weakest = state
            .legal_targets(0)
            .into_iter()
            .min_by_key(|&target| (state.units[target as usize].hp, target))
            .unwrap();
        assert_eq!(
            ai::plan_turn(&state),
            vec![attack(0, weakest)],
            "{excluded} must retain attack policy"
        );
    }

    let mut no_capability = fixture(0);
    no_capability.units[0].has_indomitable = false;
    assert_eq!(ai::plan_turn(&no_capability), vec![attack(0, 4)]);
}

#[test]
fn exact_combined_ap_and_fatigue_budgets_and_one_short_fallbacks() {
    let required_ap = INDOMITABLE_AP + ATTACK_AP;
    let required_fatigue = INDOMITABLE_FATIGUE + ATTACK_FATIGUE;

    let mut exact = fixture(0);
    exact.units[0].ap = required_ap;
    exact.units[0].stats.fatigue_max = required_fatigue;
    assert_eq!(
        ai::plan_turn(&exact),
        vec![Command::Indomitable { unit: 0 }],
        "exact combined budgets"
    );
    exact.apply(Command::Indomitable { unit: 0 }).unwrap();
    let next = attack(0, 4);
    assert_eq!(ai::plan_turn(&exact), vec![next]);
    exact.apply(next).unwrap();
    assert_eq!(
        (exact.units[0].ap, exact.units[0].fatigue),
        (0, required_fatigue)
    );

    let mut one_ap_short = fixture(0);
    one_ap_short.units[0].ap = required_ap - 1;
    assert_eq!(ai::plan_turn(&one_ap_short), vec![attack(0, 4)]);

    let mut one_fatigue_short = fixture(0);
    one_fatigue_short.units[0].stats.fatigue_max = required_fatigue;
    one_fatigue_short.units[0].fatigue = 1;
    assert_eq!(ai::plan_turn(&one_fatigue_short), vec![attack(0, 4)]);
}

#[test]
fn combined_fatigue_arithmetic_is_checked_at_u32_max() {
    let required = INDOMITABLE_FATIGUE + ATTACK_FATIGUE;
    let mut exact = fixture(0);
    exact.units[0].stats.fatigue_max = u32::MAX;
    exact.units[0].fatigue = u32::MAX - required;
    assert_eq!(
        ai::plan_turn(&exact),
        vec![Command::Indomitable { unit: 0 }],
        "exact u32::MAX sum"
    );

    exact.units[0].fatigue = u32::MAX - required + 1;
    assert_eq!(
        ai::plan_turn(&exact),
        vec![attack(0, 4)],
        "one over u32::MAX must not wrap"
    );

    exact.units[0].fatigue = u32::MAX;
    assert_eq!(
        ai::plan_turn(&exact),
        vec![Command::Recover { unit: 0 }],
        "the attack sum overflows, but halving restores its budget"
    );
}

#[test]
fn indomitable_priority_fallback_adrenaline_withdraw_and_flee_are_preserved() {
    let mut adrenaline_fallback = fixture(0);
    adrenaline_fallback.units[0].has_adrenaline = true;
    adrenaline_fallback.units[0].hp = adrenaline_fallback.units[0].stats.hp / 2 + 1;
    assert_eq!(
        adrenaline_availability(&adrenaline_fallback.units[0]),
        Ok(())
    );
    assert_eq!(
        ai::plan_turn(&adrenaline_fallback),
        vec![Command::Adrenaline { unit: 0 }]
    );
    adrenaline_fallback
        .apply(Command::Adrenaline { unit: 0 })
        .unwrap();
    assert_eq!(ai::plan_turn(&adrenaline_fallback), vec![attack(0, 4)]);

    let mut withdrawing = fixture(0);
    withdrawing.units[0].retreating = true;
    assert_eq!(
        ai::plan_turn(&withdrawing),
        vec![Command::Withdraw { unit: 0 }]
    );

    let mut fleeing = fixture(0);
    fleeing.units[0].morale = Morale::Fleeing;
    assert_eq!(ai::plan_turn(&fleeing), vec![Command::Flee { unit: 0 }]);
}

#[test]
fn indomitable_adrenaline_and_one_berserk_refund_terminate_within_turn_bound() {
    let mut state = fixture(0);
    state.units[0].has_adrenaline = true;
    state.units[0].has_berserk = true;
    state.units[0].stats.dmg_min = 100;
    state.units[0].stats.dmg_max = 100;
    state.units[3].hp = 1;
    state.units[4].hp = 1;
    assert_fixture_valid(&state);

    let mut commands = Vec::new();
    let mut events = Vec::new();
    while state.outcome().is_none() && state.queue.first() == Some(&0) {
        let command = *ai::plan_turn(&state).first().unwrap();
        commands.push(command);
        events.extend(state.apply(command).unwrap());
        assert!(
            commands.len() <= 7,
            "normal 9-AP turn with a Berserk refund stays within the seven-command bound"
        );
    }
    assert_eq!(commands.first(), Some(&Command::Indomitable { unit: 0 }));
    assert_eq!(commands.get(1), Some(&attack(0, 3)));
    assert!(events.iter().any(|event| matches!(event, Event::BerserkTriggered { unit: 0, ap_gained } if *ap_gained == BERSERK_AP_REFUND)));
    assert!(matches!(
        events.last(),
        Some(Event::BattleEnded {
            outcome: Outcome::Victory,
            ..
        })
    ));
    assert_eq!(
        commands.len(),
        3,
        "stance, first attack, Berserk-funded attack end the encounter"
    );
    assert!(state.units[0].indomitable_active);
    assert!(
        !state.units[0].adrenaline_pending,
        "Indomitable priority left Adrenaline unused"
    );
}
