use bb_rules::hex::{hex, map_hexes};
use bb_rules::rules::{ATTACK_AP, ATTACK_FATIGUE, MOVE_AP, MOVE_FATIGUE};
use bb_rules::*;
use std::panic::{AssertUnwindSafe, catch_unwind};

const PLAYERS: [Hex; 3] = [hex(-4, 2), hex(-4, 3), hex(-4, 4)];
const ENEMIES: [Hex; 3] = [hex(4, -4), hex(4, -3), hex(4, -2)];
const P1: u32 = 0;
const E1: u32 = 3;
const E2: u32 = 4;

/// Builds and validates a normal scenario fixture before callers set boundary fatigue.
fn board(active: u32, placements: &[(u32, Hex)]) -> BattleState {
    let mut state = BattleState::new(1);
    for (i, pos) in PLAYERS.into_iter().enumerate() {
        state.units[i].pos = pos;
    }
    for (i, pos) in ENEMIES.into_iter().enumerate() {
        state.units[3 + i].pos = pos;
    }
    for &(id, pos) in placements {
        state.units[id as usize].pos = pos;
    }
    state.queue.retain(|&id| id != active);
    state.queue.insert(0, active);
    state.units[active as usize].ap = 9;

    let occupied: Vec<_> = state.units.iter().map(|unit| unit.pos).collect();
    for (i, pos) in occupied.iter().enumerate() {
        assert!(
            map_hexes().contains(pos) && !pos.is_obstacle(),
            "fixture hex must be walkable"
        );
        assert!(
            !occupied[..i].contains(pos),
            "fixture positions must be distinct"
        );
    }
    state
}

fn assert_rejected_without_changes(
    state: &BattleState,
    before_hash: u64,
    before_rng: &Rng,
    message: &str,
) {
    assert_eq!(
        (state.state_hash(), &state.rng),
        (before_hash, before_rng),
        "{message}"
    );
}

#[test]
fn upfront_move_overflow_is_rejected_without_panic_or_mutation() {
    let from = hex(0, -2);
    let to = hex(-1, -2);
    let mut state = board(P1, &[(P1, from)]);
    assert_eq!(state.units[P1 as usize].ap, 9);
    assert_eq!(bb_rules::hex::bfs_path(from, to, &[]).unwrap().len(), 1);
    state.units[P1 as usize].stats.fatigue_max = u32::MAX;
    state.units[P1 as usize].fatigue = u32::MAX - 1;
    assert!(state.units[P1 as usize].fatigue <= state.units[P1 as usize].stats.fatigue_max);

    let before_hash = state.state_hash();
    let before_rng = state.rng.clone();
    let result = catch_unwind(AssertUnwindSafe(|| {
        state.apply(Command::Move { unit: P1, to })
    }));

    assert_rejected_without_changes(
        &state,
        before_hash,
        &before_rng,
        "rejected move changed state or RNG",
    );
    assert!(
        matches!(result, Ok(Err(RuleError::TooFatigued))),
        "expected TooFatigued without panic"
    );
}

#[test]
fn ordinary_attack_overflow_is_rejected_without_panic_or_mutation() {
    let mut state = board(P1, &[(P1, hex(0, -2)), (E1, hex(1, -2))]);
    assert!(state.units[P1 as usize].ap >= ATTACK_AP);
    assert_eq!(state.legal_targets(P1), vec![E1]);
    state.units[P1 as usize].stats.fatigue_max = u32::MAX;
    state.units[P1 as usize].fatigue = u32::MAX - 9;
    assert!(state.units[P1 as usize].fatigue <= state.units[P1 as usize].stats.fatigue_max);

    let before_hash = state.state_hash();
    let before_rng = state.rng.clone();
    let result = catch_unwind(AssertUnwindSafe(|| {
        state.apply(Command::Attack {
            attacker: P1,
            target: E1,
        })
    }));

    assert_rejected_without_changes(
        &state,
        before_hash,
        &before_rng,
        "rejected attack changed state or RNG",
    );
    assert!(
        matches!(result, Ok(Err(RuleError::TooFatigued))),
        "expected TooFatigued without panic"
    );
}

#[test]
fn ai_recovers_when_attack_fatigue_addition_overflows() {
    let mut state = board(P1, &[(P1, hex(0, -2)), (E1, hex(1, -2))]);
    assert!(state.units[P1 as usize].ap >= ATTACK_AP);
    assert_eq!(state.legal_targets(P1), vec![E1]);
    state.units[P1 as usize].stats.fatigue_max = u32::MAX;
    state.units[P1 as usize].fatigue = u32::MAX - 9;
    assert!(state.units[P1 as usize].fatigue <= state.units[P1 as usize].stats.fatigue_max);

    let before_hash = state.state_hash();
    let before_rng = state.rng.clone();
    let result = catch_unwind(AssertUnwindSafe(|| bb_rules::ai::plan_turn(&state)));

    assert_rejected_without_changes(
        &state,
        before_hash,
        &before_rng,
        "AI query changed state or RNG",
    );
    let commands = result.expect("AI planning must not panic");
    assert_eq!(
        commands,
        [Command::Recover { unit: P1 }],
        "expected legal Recover without an unaffordable Attack"
    );
    state.apply(commands[0]).expect("selected Recover is legal");
    assert_eq!(state.units[P1 as usize].fatigue, (u32::MAX - 9) / 2);
    assert!(!state.queue.contains(&P1), "Recover ends the scheduled turn");
}

#[test]
fn exact_cap_move_and_attack_remain_legal() {
    let from = hex(0, -2);
    let to = hex(-1, -2);
    let mut mover = board(P1, &[(P1, from)]);
    assert!(mover.units[P1 as usize].ap >= MOVE_AP);
    assert_eq!(bb_rules::hex::bfs_path(from, to, &[]).unwrap().len(), 1);
    mover.units[P1 as usize].stats.fatigue_max = u32::MAX;
    mover.units[P1 as usize].fatigue = u32::MAX - MOVE_FATIGUE;
    mover.apply(Command::Move { unit: P1, to }).unwrap();
    assert_eq!(mover.units[P1 as usize].fatigue, u32::MAX);
    assert_eq!(mover.units[P1 as usize].pos, to);

    let mut attacker = board(P1, &[(P1, hex(0, -2)), (E1, hex(1, -2))]);
    assert!(attacker.units[P1 as usize].ap >= ATTACK_AP);
    assert_eq!(attacker.legal_targets(P1), vec![E1]);
    attacker.units[P1 as usize].stats.fatigue_max = u32::MAX;
    attacker.units[P1 as usize].fatigue = u32::MAX - ATTACK_FATIGUE;
    attacker
        .apply(Command::Attack {
            attacker: P1,
            target: E1,
        })
        .unwrap();
    assert_eq!(attacker.units[P1 as usize].fatigue, u32::MAX);
}

#[test]
fn ai_selects_and_can_execute_an_affordable_exact_cap_attack() {
    let mut state = board(P1, &[(P1, hex(0, -2)), (E1, hex(1, -2)), (E2, hex(0, -3))]);
    assert!(state.units[P1 as usize].ap >= ATTACK_AP);
    assert_eq!(state.legal_targets(P1), vec![E1, E2]);
    state.units[E1 as usize].hp = 30;
    state.units[E2 as usize].hp = 45;
    state.units[P1 as usize].stats.fatigue_max = u32::MAX;
    state.units[P1 as usize].fatigue = u32::MAX - ATTACK_FATIGUE;

    let before_hash = state.state_hash();
    let before_rng = state.rng.clone();
    let command = bb_rules::ai::plan_turn(&state);
    assert_eq!(
        command,
        [Command::Attack {
            attacker: P1,
            target: E1
        }]
    );
    assert_eq!(
        (state.state_hash(), &state.rng),
        (before_hash, &before_rng),
        "AI query must stay pure"
    );

    state.apply(command[0]).unwrap();
    assert_eq!(state.units[P1 as usize].fatigue, u32::MAX);
}
