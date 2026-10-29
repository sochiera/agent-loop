//! Deterministic Stunning Blow selection policy (APPROXIMATION inspired by the
//! catalog; exact filters, HP ordering and priority are slice choices).

use bb_rules::ai;
use bb_rules::hex::{Hex, hex, map_hexes};
use bb_rules::rules::{BERSERK_AP_REFUND, STUNNING_BLOW_AP, STUNNING_BLOW_FATIGUE};
use bb_rules::{BattleState, Command, Event, Morale, Outcome, Rng, Side, Unit};

const PLAYER: u32 = 0;
const ENEMY: u32 = 3;
const ACTOR_AT: Hex = hex(0, 2);
const TARGET_AT: [Hex; 3] = [hex(1, 2), hex(0, 3), hex(-1, 2)];

fn blow(attacker: u32, target: u32) -> Command {
    Command::StunningBlow { attacker, target }
}

fn fixture(actor: u32, targets: &[(u32, Hex, u32)]) -> BattleState {
    let mut state = BattleState::new(123);
    for unit in &mut state.units {
        unit.alive = false;
        unit.turn_started = false;
    }
    let actor_side = if actor < 3 { Side::Player } else { Side::Enemy };
    let active = &mut state.units[actor as usize];
    active.alive = true;
    active.pos = ACTOR_AT;
    active.ap = 9;
    active.turn_started = true;
    active.has_stunning_blow = true;
    active.stats.melee_skill = 200;
    active.stats.dmg_min = 5;
    active.stats.dmg_max = 5;

    for &(id, pos, hp) in targets {
        let target = &mut state.units[id as usize];
        target.alive = true;
        target.pos = pos;
        target.hp = hp;
        target.armour = 0;
        target.stats.defense = 0;
        target.stats.armour = 0;
    }
    state.queue = std::iter::once(actor)
        .chain(targets.iter().map(|(id, _, _)| *id))
        .collect();
    assert!(state.units[actor as usize].side == actor_side);
    assert!(map_hexes().contains(&ACTOR_AT) && !ACTOR_AT.is_obstacle());
    let present: Vec<&Unit> = state.units.iter().filter(|u| u.is_present()).collect();
    for (i, unit) in present.iter().enumerate() {
        assert!(
            map_hexes().contains(&unit.pos) && !unit.pos.is_obstacle(),
            "{}: {:?}",
            unit.name,
            unit.pos
        );
        assert!(
            present[..i].iter().all(|other| other.pos != unit.pos),
            "occupied {:?}",
            unit.pos
        );
        assert!(unit.hp > 0 && unit.hp <= unit.stats.hp);
        assert!(unit.ap <= unit.stats.ap_max && unit.fatigue <= unit.stats.fatigue_max);
    }
    state
}

fn adjacent_duel(actor: u32) -> BattleState {
    let target = if actor == PLAYER { ENEMY } else { PLAYER };
    fixture(actor, &[(target, TARGET_AT[0], 40)])
}

fn snapshot(state: &BattleState) -> (u64, Rng, Vec<Unit>, Vec<u32>, u32, Option<Outcome>) {
    (
        state.state_hash(),
        state.rng.clone(),
        state.units.clone(),
        state.queue.clone(),
        state.round,
        state.outcome(),
    )
}

fn seed_for(chance: u32, hit: bool) -> u64 {
    (0u64..)
        .find(|&seed| (((Rng::new(seed).next_u64() % 100) as u32) < chance) == hit)
        .unwrap()
}

#[test]
fn both_factions_choose_lowest_hp_then_lowest_id_and_skip_stunned_or_indomitable() {
    for actor in [PLAYER, ENEMY] {
        let ids = if actor == PLAYER {
            [3, 4, 5]
        } else {
            [0, 1, 2]
        };
        let mut state = fixture(
            actor,
            &[
                (ids[0], TARGET_AT[0], 30),
                (ids[1], TARGET_AT[1], 10),
                (ids[2], TARGET_AT[2], 10),
            ],
        );
        assert_eq!(
            ai::plan_turn(&state),
            vec![blow(actor, ids[1])],
            "lowest HP tie uses id"
        );

        state.units[ids[1] as usize].stunned = true;
        assert_eq!(
            ai::plan_turn(&state),
            vec![blow(actor, ids[2])],
            "skip an already-stunned defender"
        );

        let mut immune = fixture(
            actor,
            &[
                (ids[0], TARGET_AT[0], 10),
                (ids[1], TARGET_AT[1], 20),
                (ids[2], TARGET_AT[2], 30),
            ],
        );
        immune.units[ids[0] as usize].has_indomitable = true;
        immune.units[ids[0] as usize].indomitable_active = true;
        assert!(immune.units[ids[0] as usize].is_indomitable_active());
        assert_eq!(
            ai::plan_turn(&immune),
            vec![blow(actor, ids[1])],
            "skip an effectively Indomitable defender"
        );
    }
}

#[test]
fn capability_and_shared_ap_fatigue_query_gate_skill_then_old_policy_stays_legal() {
    let mut state = adjacent_duel(PLAYER);
    state.units[PLAYER as usize].has_stunning_blow = false;
    assert!(state.legal_stunning_blow_targets(PLAYER).is_empty());
    let command = ai::plan_turn(&state)[0];
    assert_eq!(
        command,
        Command::Attack {
            attacker: PLAYER,
            target: ENEMY
        }
    );
    state.apply(command).unwrap();

    let mut state = adjacent_duel(PLAYER);
    state.units[PLAYER as usize].ap = STUNNING_BLOW_AP - 1;
    assert!(state.legal_stunning_blow_targets(PLAYER).is_empty());
    let command = ai::plan_turn(&state)[0];
    assert_eq!(
        command,
        Command::Attack {
            attacker: PLAYER,
            target: ENEMY
        }
    );
    state.apply(command).unwrap();

    let mut state = adjacent_duel(PLAYER);
    state.units[PLAYER as usize].ap = STUNNING_BLOW_AP;
    assert_eq!(state.legal_stunning_blow_targets(PLAYER), vec![ENEMY]);
    assert_eq!(ai::plan_turn(&state), vec![blow(PLAYER, ENEMY)]);
    state.apply(ai::plan_turn(&state)[0]).unwrap();
    assert_eq!(
        state.units[PLAYER as usize].ap, 0,
        "no ordinary attack budget is reserved after the skill"
    );

    let mut state = adjacent_duel(PLAYER);
    state.units[PLAYER as usize].stats.fatigue_max = 100;
    state.units[PLAYER as usize].fatigue = 100 - STUNNING_BLOW_FATIGUE;
    assert_eq!(
        state.legal_stunning_blow_targets(PLAYER),
        vec![ENEMY],
        "exact fatigue capacity"
    );
    assert_eq!(ai::plan_turn(&state), vec![blow(PLAYER, ENEMY)]);
    state.apply(ai::plan_turn(&state)[0]).unwrap();
    assert_eq!(state.units[PLAYER as usize].fatigue, 100);

    let mut state = adjacent_duel(PLAYER);
    state.units[PLAYER as usize].stats.fatigue_max = 100;
    state.units[PLAYER as usize].fatigue = 90;
    assert!(state.legal_stunning_blow_targets(PLAYER).is_empty());
    let command = ai::plan_turn(&state)[0];
    assert_eq!(
        command,
        Command::Attack {
            attacker: PLAYER,
            target: ENEMY
        },
        "ordinary attack remains affordable"
    );
    state.apply(command).unwrap();

    let mut state = adjacent_duel(PLAYER);
    state.units[PLAYER as usize].stats.fatigue_max = 100;
    state.units[PLAYER as usize].fatigue = 91;
    assert!(state.legal_stunning_blow_targets(PLAYER).is_empty());
    let command = ai::plan_turn(&state)[0];
    assert_eq!(
        command,
        Command::Recover { unit: PLAYER },
        "unaffordable attack recovers as before"
    );
    state.apply(command).unwrap();

    let mut overflow = adjacent_duel(PLAYER);
    overflow.units[PLAYER as usize].stats.fatigue_max = u32::MAX;
    overflow.units[PLAYER as usize].fatigue = u32::MAX;
    assert!(
        overflow.legal_stunning_blow_targets(PLAYER).is_empty(),
        "shared checked-add rejects overflow"
    );
    let command = ai::plan_turn(&overflow)[0];
    assert_eq!(command, Command::Recover { unit: PLAYER });
    overflow.apply(command).unwrap();
}

#[test]
fn immune_or_stunned_enemies_fall_back_to_the_ordinary_attack_policy() {
    let mut state = fixture(PLAYER, &[(3, TARGET_AT[0], 10), (4, TARGET_AT[1], 20)]);
    state.units[3].stunned = true;
    state.units[4].has_indomitable = true;
    state.units[4].indomitable_active = true;
    assert_eq!(
        state.legal_stunning_blow_targets(PLAYER),
        vec![3, 4],
        "the shared query checks legality; planner filters statuses"
    );
    let command = ai::plan_turn(&state)[0];
    assert_eq!(
        command,
        Command::Attack {
            attacker: PLAYER,
            target: 3
        }
    );
    state.apply(command).unwrap();

    let mut immune = adjacent_duel(PLAYER);
    immune.units[PLAYER as usize].hp = immune.units[PLAYER as usize].stats.hp / 2;
    immune.units[PLAYER as usize].has_indomitable = true;
    immune.units[3].has_indomitable = true;
    immune.units[3].indomitable_active = true;
    assert_eq!(
        ai::plan_turn(&immune),
        vec![Command::Indomitable { unit: PLAYER }]
    );

    let mut stunned = adjacent_duel(PLAYER);
    stunned.units[3].stunned = true;
    stunned.units[PLAYER as usize].has_adrenaline = true;
    assert_eq!(
        ai::plan_turn(&stunned),
        vec![Command::Adrenaline { unit: PLAYER }]
    );
    stunned.apply(ai::plan_turn(&stunned)[0]).unwrap();

    let mut approach = adjacent_duel(PLAYER);
    approach.units[ENEMY as usize].pos = hex(3, 2);
    assert!(approach.legal_stunning_blow_targets(PLAYER).is_empty());
    let command = ai::plan_turn(&approach)[0];
    assert!(
        matches!(command, Command::Move { unit: PLAYER, .. }),
        "old approach policy resumes: {command:?}"
    );
    approach.apply(command).unwrap();
}

#[test]
fn stunning_blow_precedes_indomitable_and_adrenaline_but_not_withdraw_or_flee() {
    for actor_id in [PLAYER, ENEMY] {
        let mut state = adjacent_duel(actor_id);
        {
            let actor = &mut state.units[actor_id as usize];
            actor.hp = actor.stats.hp / 2;
            actor.has_indomitable = true;
            actor.has_adrenaline = true;
        }
        let target = if actor_id == PLAYER { ENEMY } else { PLAYER };
        assert_eq!(ai::plan_turn(&state), vec![blow(actor_id, target)]);

        state.units[actor_id as usize].retreating = true;
        assert_eq!(
            ai::plan_turn(&state),
            vec![Command::Withdraw { unit: actor_id }]
        );
        state.units[actor_id as usize].retreating = false;
        state.units[actor_id as usize].morale = Morale::Fleeing;
        assert_eq!(
            ai::plan_turn(&state),
            vec![Command::Flee { unit: actor_id }]
        );
    }
}

#[test]
fn two_hex_distance_two_uses_the_ordinary_attack_not_stunning_blow() {
    let mut state = adjacent_duel(PLAYER);
    state.units[ENEMY as usize].pos = hex(2, 2);
    state.units[PLAYER as usize].melee_reach = bb_rules::MeleeReach::TwoHex;
    assert!(state.legal_stunning_blow_targets(PLAYER).is_empty());
    assert_eq!(state.legal_targets(PLAYER), vec![ENEMY]);
    let command = ai::plan_turn(&state)[0];
    assert_eq!(
        command,
        Command::Attack {
            attacker: PLAYER,
            target: ENEMY
        }
    );
    state.apply(command).unwrap();
}

#[test]
fn planned_hit_and_miss_keep_rule_costs_status_zoc_and_replanning_consistent() {
    for actor in [PLAYER, ENEMY] {
        for hit in [true, false] {
            let target = if actor == PLAYER { ENEMY } else { PLAYER };
            let mut state = adjacent_duel(actor);
            state.units[actor as usize].stats.ap_max = 12;
            state.units[actor as usize].ap = 12;
            state.units[actor as usize].stats.fatigue_max = 100;
            state.units[actor as usize].stats.dmg_min = 1;
            state.units[actor as usize].stats.dmg_max = 1;
            let probe = hex(2, 2);
            assert_eq!(state.enemy_controllers_at(actor, probe), vec![target]);
            let chance = state
                .preview_stunning_blow(actor, target)
                .unwrap()
                .hit_chance;
            state.rng = Rng::new(seed_for(chance, hit));
            let before = snapshot(&state);
            for _ in 0..3 {
                assert_eq!(ai::plan_turn(&state), vec![blow(actor, target)]);
            }
            assert_eq!(
                snapshot(&state),
                before,
                "planning must preserve state and RNG"
            );
            let command = ai::plan_turn(&state)[0];
            let events = state.apply(command).unwrap();
            assert_eq!(state.units[actor as usize].ap, 12 - STUNNING_BLOW_AP);
            assert_eq!(state.units[actor as usize].fatigue, STUNNING_BLOW_FATIGUE);
            if hit {
                assert!(events.contains(&Event::StunApplied { unit: target }));
                assert!(state.units[target as usize].stunned);
                assert!(
                    state.enemy_controllers_at(actor, probe).is_empty(),
                    "stun suppresses ZoC immediately"
                );
                assert_eq!(
                    ai::plan_turn(&state),
                    vec![Command::Attack {
                        attacker: actor,
                        target
                    }]
                );
                let mut unstunned = state.clone();
                unstunned.units[target as usize].stunned = false;
                assert_eq!(
                    ai::plan_turn(&unstunned),
                    vec![blow(actor, target)],
                    "stun filter drives replanning"
                );
            } else {
                assert!(
                    !events
                        .iter()
                        .any(|event| matches!(event, Event::StunApplied { .. }))
                );
                assert!(!state.units[target as usize].stunned);
                assert_eq!(state.units[target as usize].fatigue, 2);
                assert_eq!(
                    ai::plan_turn(&state),
                    vec![blow(actor, target)],
                    "a miss leaves the target eligible"
                );
            }
        }
    }
}

#[test]
fn repeated_plan_apply_completes_a_turn_including_one_legal_berserk_refund() {
    for actor in [PLAYER, ENEMY] {
        let ids = if actor == PLAYER { [3, 4] } else { [0, 1] };
        let mut state = fixture(
            actor,
            &[(ids[0], TARGET_AT[0], 5), (ids[1], TARGET_AT[1], 50)],
        );
        state.units[actor as usize].has_berserk = true;
        state.units[actor as usize].stats.dmg_min = 10;
        state.units[actor as usize].stats.dmg_max = 10;
        let chance = state
            .preview_stunning_blow(actor, ids[0])
            .unwrap()
            .hit_chance;
        state.rng = Rng::new(seed_for(chance, true));
        let mut commands = Vec::new();
        while state.outcome().is_none() && state.active_unit().map(|unit| unit.id) == Some(actor) {
            let plan = ai::plan_turn(&state);
            assert_eq!(plan.len(), 1);
            let command = plan[0];
            commands.push(command);
            let events = state
                .apply(command)
                .unwrap_or_else(|error| panic!("{command:?}: {error:?}"));
            if commands.len() == 1 {
                assert_eq!(command, blow(actor, ids[0]));
                assert!(events.contains(&Event::BerserkTriggered {
                    unit: actor,
                    ap_gained: BERSERK_AP_REFUND
                }));
                assert_eq!(state.units[actor as usize].ap, 7);
            }
            assert!(commands.len() <= 4);
        }
        assert_eq!(
            commands,
            vec![
                blow(actor, ids[0]),
                blow(actor, ids[1]),
                Command::EndTurn { unit: actor }
            ]
        );
        assert_eq!(state.units[actor as usize].ap, 1);
        assert!(state.units[actor as usize].berserk_used);
    }
}
