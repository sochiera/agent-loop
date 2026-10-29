//! Deterministic AI Recover choice (APPROXIMATION inspired by UC-COMBAT-020/065,
//! without original-AI parity claims).

use bb_rules::hex::{hex, map_hexes};
use bb_rules::rules::{
    ADRENALINE_AP, ATTACK_AP, ATTACK_FATIGUE, INDOMITABLE_AP, INDOMITABLE_FATIGUE, RECOVER_AP,
};
use bb_rules::scenario::{LaunchArgs, TerrainPreset, launch_battle_for};
use bb_rules::*;

const MAX_REPLAY_COMMANDS: usize = 10_000;
const MAX_COMMANDS_PER_TURN: usize = 8;

const PLAYER: u32 = 0;
const ENEMY: u32 = 3;
const PLAYER_HEX: Hex = hex(0, -2);
const ENEMY_HEX: Hex = hex(1, -2);
const PARKED: [Hex; 6] = [
    hex(-4, 2),
    hex(-4, 3),
    hex(-4, 4),
    hex(4, -4),
    hex(4, -3),
    hex(4, -2),
];

fn adjacent_battle(actor: u32) -> BattleState {
    let mut state = BattleState::new(71);
    for (unit, at) in state.units.iter_mut().zip(PARKED) {
        unit.pos = at;
    }
    state.units[PLAYER as usize].pos = PLAYER_HEX;
    state.units[ENEMY as usize].pos = ENEMY_HEX;
    state.queue.retain(|&id| id != actor);
    state.queue.insert(0, actor);
    let active = &mut state.units[actor as usize];
    active.ap = RECOVER_AP;
    active.stats.ap_max = active.stats.ap_max.max(RECOVER_AP);
    active.turn_started = true;

    let occupied: Vec<_> = state.units.iter().map(|unit| unit.pos).collect();
    for (i, at) in occupied.iter().enumerate() {
        assert!(
            map_hexes().contains(at) && !at.is_obstacle(),
            "passable fixture hex: {at:?}"
        );
        assert!(!occupied[..i].contains(at), "distinct fixture hex: {at:?}");
    }
    let opposing = if actor == PLAYER { ENEMY } else { PLAYER };
    assert_eq!(state.legal_targets(actor), vec![opposing]);
    state
}

fn snapshot(
    state: &BattleState,
) -> (
    u64,
    Vec<Unit>,
    Vec<u32>,
    u32,
    Option<Outcome>,
    Option<EndReason>,
    bb_rules::terrain::ElevationMap,
    bb_rules::terrain::TerrainMap,
    Rng,
) {
    (
        state.state_hash(),
        state.units.clone(),
        state.queue.clone(),
        state.round,
        state.outcome,
        state.end_reason,
        state.elevations.clone(),
        state.ground.clone(),
        state.rng.clone(),
    )
}

fn set_fatigue(state: &mut BattleState, actor: u32, fatigue: u32, maximum: u32) {
    let unit = &mut state.units[actor as usize];
    unit.fatigue = fatigue;
    unit.stats.fatigue_max = maximum;
    assert!(unit.ap <= unit.stats.ap_max);
    assert!(unit.fatigue <= unit.stats.fatigue_max);
}

fn replay_seed_one(args: LaunchArgs) -> (Vec<Command>, Vec<Event>, BattleState, usize, usize) {
    let mut state = launch_battle_for(args);
    let mut commands = Vec::new();
    let mut events = Vec::new();
    let (mut recover_turns, mut explicit_end_turns) = (0, 0);
    let mut commands_this_turn = 0;
    while state.outcome().is_none() {
        let plan = bb_rules::ai::plan_turn(&state);
        assert_eq!(plan.len(), 1, "one AI command per running-battle plan");
        let command = plan[0];
        commands.push(command);
        commands_this_turn += 1;
        assert!(
            commands_this_turn <= MAX_COMMANDS_PER_TURN,
            "{args:?} exceeded the per-turn command bound"
        );
        events.extend(
            state
                .apply(command)
                .unwrap_or_else(|error| panic!("{args:?}: {error:?} from {command:?}")),
        );
        match command {
            Command::Recover { .. } => {
                recover_turns += 1;
                commands_this_turn = 0;
            }
            Command::EndTurn { .. } => {
                explicit_end_turns += 1;
                commands_this_turn = 0;
            }
            Command::Flee { .. } | Command::Withdraw { .. } => commands_this_turn = 0,
            _ => {}
        }
        assert!(
            commands.len() <= MAX_REPLAY_COMMANDS,
            "{args:?} exceeded replay bound"
        );
    }
    (commands, events, state, recover_turns, explicit_end_turns)
}

fn seed_one_args(terrain: TerrainPreset, capability_mask: u16) -> LaunchArgs {
    LaunchArgs {
        seed: 1,
        terrain,
        adrenaline: capability_mask & 1 != 0,
        rotation: capability_mask & 2 != 0,
        footwork: capability_mask & 4 != 0,
        fast_adaptation: capability_mask & 8 != 0,
        dodge: capability_mask & 16 != 0,
        nine_lives: capability_mask & 32 != 0,
        berserk: capability_mask & 64 != 0,
        pathfinder: capability_mask & 128 != 0,
        indomitable: capability_mask & 256 != 0,
        two_hex: false,
        stunning_blow: false, killing_frenzy: false,
    }
}

#[test]
fn both_factions_recover_only_at_nine_ap_when_attack_is_unaffordable_but_halving_restores_it() {
    for actor in [PLAYER, ENEMY] {
        let mut state = adjacent_battle(actor);
        set_fatigue(&mut state, actor, 41, 50);
        assert!(state.recover_availability(actor).is_ok());
        assert!(41u32.checked_add(ATTACK_FATIGUE).unwrap() > 50);
        assert!((41 / 2) + ATTACK_FATIGUE <= 50);

        let before = snapshot(&state);
        let selected = bb_rules::ai::plan_turn(&state);
        assert_eq!(selected, [Command::Recover { unit: actor }]);
        assert_eq!(
            bb_rules::ai::plan_turn(&state),
            selected,
            "planning is deterministic"
        );
        assert_eq!(
            snapshot(&state),
            before,
            "planning reads state and RNG only"
        );
    }

    for actor in [PLAYER, ENEMY] {
        let mut eight_ap = adjacent_battle(actor);
        eight_ap.units[actor as usize].ap = RECOVER_AP - 1;
        set_fatigue(&mut eight_ap, actor, 41, 50);
        assert_eq!(
            bb_rules::ai::plan_turn(&eight_ap),
            [Command::EndTurn { unit: actor }]
        );
    }
}

#[test]
fn exact_attack_capacity_attacks_and_one_over_recovers() {
    let mut exact = adjacent_battle(PLAYER);
    set_fatigue(&mut exact, PLAYER, 40, 50);
    assert_eq!(
        bb_rules::ai::plan_turn(&exact),
        [Command::Attack {
            attacker: PLAYER,
            target: ENEMY
        }]
    );

    let mut one_over = adjacent_battle(PLAYER);
    set_fatigue(&mut one_over, PLAYER, 41, 50);
    assert_eq!(
        bb_rules::ai::plan_turn(&one_over),
        [Command::Recover { unit: PLAYER }]
    );
}

#[test]
fn checked_u32_fatigue_boundaries_keep_attack_and_recover_legal() {
    let mut exact_max = adjacent_battle(PLAYER);
    set_fatigue(&mut exact_max, PLAYER, u32::MAX - ATTACK_FATIGUE, u32::MAX);
    assert_eq!(
        bb_rules::ai::plan_turn(&exact_max),
        [Command::Attack {
            attacker: PLAYER,
            target: ENEMY
        }]
    );

    for fatigue in [u32::MAX - ATTACK_FATIGUE + 1, u32::MAX] {
        let mut overflow = adjacent_battle(PLAYER);
        set_fatigue(&mut overflow, PLAYER, fatigue, u32::MAX);
        assert_eq!(
            fatigue.checked_add(ATTACK_FATIGUE).is_none(),
            true,
            "fixture crosses the checked-add boundary"
        );
        assert_eq!(
            bb_rules::ai::plan_turn(&overflow),
            [Command::Recover { unit: PLAYER }],
            "halving must make the attack budget legal for fatigue {fatigue}"
        );
    }
}

#[test]
fn odd_fatigue_halves_down_and_too_small_a_maximum_does_not_recover() {
    let mut odd = adjacent_battle(PLAYER);
    set_fatigue(&mut odd, PLAYER, 43, 50);
    assert_eq!(
        bb_rules::ai::plan_turn(&odd),
        [Command::Recover { unit: PLAYER }]
    );

    let mut too_small = adjacent_battle(PLAYER);
    set_fatigue(&mut too_small, PLAYER, 11, 14);
    assert!(11 + ATTACK_FATIGUE > 14);
    assert!((11 / 2) + ATTACK_FATIGUE > 14);
    assert_eq!(
        bb_rules::ai::plan_turn(&too_small),
        [Command::EndTurn { unit: PLAYER }]
    );
}

#[test]
fn zero_fatigue_and_no_adjacent_target_never_trigger_recover() {
    let mut zero = adjacent_battle(PLAYER);
    set_fatigue(&mut zero, PLAYER, 0, 100);
    assert_eq!(
        bb_rules::ai::plan_turn(&zero),
        [Command::Attack {
            attacker: PLAYER,
            target: ENEMY
        }]
    );

    let mut zero_too_small = adjacent_battle(PLAYER);
    set_fatigue(&mut zero_too_small, PLAYER, 0, ATTACK_FATIGUE - 1);
    assert_eq!(
        bb_rules::ai::plan_turn(&zero_too_small),
        [Command::EndTurn { unit: PLAYER }]
    );

    let mut distant = adjacent_battle(PLAYER);
    distant.units[ENEMY as usize].pos = hex(3, -3);
    let occupied: Vec<_> = distant.units.iter().map(|unit| unit.pos).collect();
    for (i, at) in occupied.iter().enumerate() {
        assert!(map_hexes().contains(at) && !at.is_obstacle());
        assert!(
            !occupied[..i].contains(at),
            "distant fixture hexes stay distinct"
        );
    }
    assert!(distant.legal_targets(PLAYER).is_empty());
    set_fatigue(&mut distant, PLAYER, 41, 50);
    assert!(matches!(
        bb_rules::ai::plan_turn(&distant).as_slice(),
        [Command::Move { unit: PLAYER, .. }]
    ));
}

#[test]
fn recover_does_not_replace_withdraw_flee_or_existing_skill_and_attack_choices() {
    let mut withdrawing = adjacent_battle(PLAYER);
    withdrawing.units[PLAYER as usize].retreating = true;
    set_fatigue(&mut withdrawing, PLAYER, 41, 50);
    assert_eq!(
        bb_rules::ai::plan_turn(&withdrawing),
        [Command::Withdraw { unit: PLAYER }]
    );

    let mut fleeing = adjacent_battle(PLAYER);
    fleeing.units[PLAYER as usize].morale = Morale::Fleeing;
    set_fatigue(&mut fleeing, PLAYER, 41, 50);
    assert_eq!(
        bb_rules::ai::plan_turn(&fleeing),
        [Command::Flee { unit: PLAYER }]
    );

    let mut attack = adjacent_battle(PLAYER);
    set_fatigue(&mut attack, PLAYER, 10, 20);
    assert_eq!(
        bb_rules::ai::plan_turn(&attack),
        [Command::Attack {
            attacker: PLAYER,
            target: ENEMY
        }]
    );

    let mut indomitable = adjacent_battle(PLAYER);
    set_fatigue(&mut indomitable, PLAYER, 1, 36);
    indomitable.units[PLAYER as usize].hp = 30;
    indomitable.units[PLAYER as usize].has_indomitable = true;
    assert_eq!(
        bb_rules::ai::plan_turn(&indomitable),
        [Command::Indomitable { unit: PLAYER }]
    );
    assert!(INDOMITABLE_AP + ATTACK_AP <= RECOVER_AP);
    assert!(1 + INDOMITABLE_FATIGUE + ATTACK_FATIGUE <= 36);

    let mut adrenaline = adjacent_battle(PLAYER);
    set_fatigue(&mut adrenaline, PLAYER, 1, 31);
    adrenaline.units[PLAYER as usize].has_adrenaline = true;
    assert_eq!(
        bb_rules::ai::plan_turn(&adrenaline),
        [Command::Adrenaline { unit: PLAYER }]
    );
    assert!(ADRENALINE_AP + ATTACK_AP <= RECOVER_AP);
}

#[test]
fn selected_recover_executes_once_and_next_fresh_turn_can_attack_after_regeneration() {
    let mut state = adjacent_battle(PLAYER);
    set_fatigue(&mut state, PLAYER, 61, 70);
    let initial_round = state.round;

    let selected = bb_rules::ai::plan_turn(&state);
    assert_eq!(selected, [Command::Recover { unit: PLAYER }]);
    let events = state
        .apply(selected[0])
        .expect("AI-selected Recover is legal");
    assert_eq!(state.units[PLAYER as usize].fatigue, 30);
    assert!(
        !state.queue.contains(&PLAYER),
        "Recover ends the scheduled turn"
    );
    assert_ne!(state.active_unit().unwrap().id, PLAYER);
    assert!(!events.is_empty());

    let mut advances = 0;
    while !(state.round > initial_round
        && state.active_unit().is_some_and(|unit| unit.id == PLAYER))
    {
        let active = state
            .active_unit()
            .expect("running battle has an active unit")
            .id;
        assert_ne!(
            active, PLAYER,
            "no second command belongs to the recovered turn"
        );
        state.apply(Command::EndTurn { unit: active }).unwrap();
        advances += 1;
        assert!(
            advances <= 12,
            "other actors and one round advance are bounded"
        );
    }

    assert_eq!(
        state.units[PLAYER as usize].ap,
        state.units[PLAYER as usize].stats.ap_max
    );
    assert_eq!(
        state.units[PLAYER as usize].fatigue, 15,
        "fresh-turn regeneration follows halving"
    );
    let next = bb_rules::ai::plan_turn(&state);
    assert_eq!(
        next,
        [Command::Attack {
            attacker: PLAYER,
            target: ENEMY
        }]
    );
    state
        .apply(next[0])
        .expect("the fresh-turn attack is affordable");
}

#[test]
fn seed_one_preset_replays_keep_deterministic_valid_turn_ending_traces() {
    for terrain in [
        TerrainPreset::Flat,
        TerrainPreset::Hills,
        TerrainPreset::Swamp,
    ] {
        for mask in [0, 128, 511] {
            let args = seed_one_args(terrain, mask);
            let (commands_a, events_a, state_a, recovered_a, ended_a) = replay_seed_one(args);
            let (commands_b, events_b, state_b, recovered_b, ended_b) = replay_seed_one(args);
            assert_eq!(commands_a, commands_b, "{args:?}: command trace");
            assert_eq!(events_a, events_b, "{args:?}: event trace");
            assert_eq!((recovered_a, ended_a), (recovered_b, ended_b));
            assert_eq!(
                recovered_a, 0,
                "{args:?}: measured seed-1 traces do not yet exercise Recover"
            );
            assert_eq!(
                state_a.state_hash(),
                state_b.state_hash(),
                "{args:?}: final hash"
            );
            assert_eq!(state_a.rng, state_b.rng, "{args:?}: final RNG");
            assert_eq!(state_a.outcome(), state_b.outcome());
            assert_eq!(state_a.end_reason(), state_b.end_reason());
            assert_eq!(state_a.round, state_b.round);
            assert!(matches!(events_a.last(), Some(Event::BattleEnded { .. })));
            println!(
                "{terrain:?} mask={mask}: Recover turns={recovered_a}, explicit EndTurn turns={ended_a}, outcome={:?}, reason={:?}, rounds={}, hash={:016x}",
                state_a.outcome(),
                state_a.end_reason(),
                state_a.round,
                state_a.state_hash(),
            );
        }
    }
}
