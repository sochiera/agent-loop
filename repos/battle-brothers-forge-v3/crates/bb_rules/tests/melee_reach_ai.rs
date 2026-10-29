//! TwoHex AI approach policy (APPROXIMATION inspired by UC-COMBAT-020/051).

use bb_rules::ai::plan_turn;
use bb_rules::hex::{bfs_distances, bfs_path, hex, map_hexes};
use bb_rules::rules::{ATTACK_AP, MOVE_AP, PATHFINDER_MOVE_FATIGUE};
use bb_rules::*;

const P1: u32 = 0;
const P2: u32 = 1;
const P3: u32 = 2;
const E1: u32 = 3;
const E2: u32 = 4;
const E3: u32 = 5;
const PARK: [Hex; 6] = [
    hex(-4, 2),
    hex(-4, 3),
    hex(-4, 4),
    hex(4, -4),
    hex(4, -3),
    hex(4, -2),
];

fn board(actor: u32, reach: MeleeReach) -> BattleState {
    let mut state = BattleState::new(1);
    for (unit, pos) in state.units.iter_mut().zip(PARK) {
        unit.pos = pos;
        unit.stats.resolve = 0;
    }
    state.units[actor as usize].melee_reach = reach;
    state.queue.retain(|&id| id != actor);
    state.queue.insert(0, actor);
    let active = &mut state.units[actor as usize];
    active.ap = active.stats.ap_max;
    active.turn_started = true;
    assert_fixture(&state);
    state
}

fn assert_fixture(state: &BattleState) {
    let positions: Vec<_> = state.units.iter().map(|unit| unit.pos).collect();
    for (i, pos) in positions.iter().enumerate() {
        assert!(map_hexes().contains(pos) && !pos.is_obstacle(), "{pos:?}");
        assert!(!positions[..i].contains(pos), "duplicate {pos:?}");
    }
    for unit in &state.units {
        assert!(unit.hp <= unit.stats.hp && unit.armour <= unit.stats.armour);
        assert!(unit.ap <= unit.stats.ap_max && unit.fatigue <= unit.stats.fatigue_max);
    }
}

fn actor_and_enemy(actor: u32) -> (u32, u32, u32) {
    if actor < 3 {
        (actor, E1, P2)
    } else {
        (actor, P1, E2)
    }
}

type Snapshot = (
    u64,
    Rng,
    Vec<Unit>,
    Vec<u32>,
    u32,
    Option<Outcome>,
    Option<EndReason>,
    ElevationMap,
    TerrainMap,
);

fn snapshot(state: &BattleState) -> Snapshot {
    (
        state.state_hash(),
        state.rng.clone(),
        state.units.clone(),
        state.queue.clone(),
        state.round,
        state.outcome,
        state.end_reason,
        state.elevations.clone(),
        state.ground.clone(),
    )
}

fn approach_board(actor: u32, from: Hex, target: Hex) -> BattleState {
    let (_, enemy, _) = actor_and_enemy(actor);
    let mut state = board(actor, MeleeReach::TwoHex);
    state.units[actor as usize].pos = from;
    state.units[enemy as usize].pos = target;
    state.units[E2 as usize].alive = false;
    state.units[E3 as usize].alive = false;
    assert_fixture(&state);
    state
}

fn chosen_candidate(state: &BattleState, actor: u32, enemy: u32) -> (Hex, Vec<Hex>) {
    let occupied: Vec<_> = state
        .units
        .iter()
        .filter(|unit| unit.is_present())
        .map(|unit| unit.pos)
        .collect();
    let start = state.units[actor as usize].pos;
    let target = state.units[enemy as usize].pos;
    let (destination, _) = bfs_distances(start, &occupied)
        .into_iter()
        .find(|&(candidate, _)| MeleeReach::TwoHex.reaches(candidate, target))
        .expect("reachable firing candidate");
    let path = bfs_path(start, destination, &occupied).expect("candidate has a deterministic path");
    (destination, path)
}

/// A three-hex approach with an ally between the attacker and enemy after the move.
#[test]
fn two_hex_actor_moves_from_three_to_two_then_attacks_through_an_ally() {
    for actor in [P1, E1] {
        let (actor, enemy, ally) = actor_and_enemy(actor);
        let mut state = board(actor, MeleeReach::TwoHex);
        state.units[actor as usize].pos = hex(-2, -2);
        state.units[ally as usize].pos = hex(0, -2);
        state.units[enemy as usize].pos = hex(1, -2);
        assert_fixture(&state);
        assert_eq!(
            state.units[actor as usize]
                .pos
                .distance(state.units[enemy as usize].pos),
            3
        );

        let move_plan = plan_turn(&state);
        assert_eq!(
            move_plan,
            vec![Command::Move {
                unit: actor,
                to: hex(-1, -2)
            }]
        );
        let before = snapshot(&state);
        assert_eq!(plan_turn(&state), move_plan);
        assert_eq!(plan_turn(&state), move_plan);
        assert_eq!(snapshot(&state), before, "planning is state and RNG pure");
        let preview = state
            .preview_move(actor, hex(-1, -2))
            .expect("planned move previews");
        assert_eq!(preview.path, vec![hex(-1, -2)]);
        state.apply(move_plan[0]).expect("planned move applies");
        assert_eq!(
            state.units[actor as usize]
                .pos
                .distance(state.units[enemy as usize].pos),
            2
        );
        assert_eq!(state.units[ally as usize].pos, hex(0, -2));

        let attack_plan = plan_turn(&state);
        assert_eq!(
            attack_plan,
            vec![Command::Attack {
                attacker: actor,
                target: enemy
            }]
        );
        assert!(state.legal_targets(actor).contains(&enemy));
        state
            .apply(attack_plan[0])
            .expect("planned attack through ally applies");
        assert_eq!(
            state.units[actor as usize].pos,
            hex(-1, -2),
            "no extra move"
        );
    }
}

#[test]
fn already_in_reach_keeps_skill_and_recover_choices_then_ends_without_an_affordable_action() {
    let mut state = approach_board(P1, hex(-1, -2), hex(1, -2));
    state.units[P1 as usize].has_adrenaline = true;
    assert_eq!(plan_turn(&state), vec![Command::Adrenaline { unit: P1 }]);

    state.units[P1 as usize].has_adrenaline = false;
    state.units[P1 as usize].has_indomitable = true;
    state.units[P1 as usize].hp = state.units[P1 as usize].stats.hp / 2;
    state.units[E2 as usize].alive = true;
    state.units[E2 as usize].pos = hex(-1, -1); // adjacent controller; legal TwoHex target
    assert_fixture(&state);
    assert_eq!(plan_turn(&state), vec![Command::Indomitable { unit: P1 }]);

    state.units[P1 as usize].has_indomitable = false;
    state.units[P1 as usize].hp = state.units[P1 as usize].stats.hp;
    state.units[P1 as usize].ap = state.units[P1 as usize].stats.ap_max;
    state.units[P1 as usize].fatigue = state.units[P1 as usize].stats.fatigue_max;
    assert_eq!(plan_turn(&state), vec![Command::Recover { unit: P1 }]);

    state.units[P1 as usize].fatigue = 0;
    state.units[P1 as usize].ap = ATTACK_AP - 1;
    assert_eq!(plan_turn(&state), vec![Command::EndTurn { unit: P1 }]);
    assert_eq!(
        state.units[P1 as usize].pos,
        hex(-1, -2),
        "do not move into adjacency"
    );
}

#[test]
fn two_hex_candidate_ties_keep_enemy_order_then_bfs_discovery_order() {
    // Two equally close enemies: lower unit iteration order selects the east candidate.
    let mut state = board(P1, MeleeReach::TwoHex);
    state.units[P1 as usize].pos = hex(0, 1);
    state.units[E1 as usize].pos = hex(3, 1);
    state.units[E2 as usize].pos = hex(0, 4);
    state.units[E3 as usize].alive = false;
    assert_fixture(&state);
    assert_eq!(
        plan_turn(&state),
        vec![Command::Move {
            unit: P1,
            to: hex(1, 1)
        }],
        "E1 wins the equal-distance tie even though E2's candidate is later in BFS order"
    );

    // One enemy has two one-step firing positions. The fixed neighbor/BFS order picks (1,1).
    let state = approach_board(P1, hex(0, 1), hex(2, 2));
    assert_eq!(
        plan_turn(&state),
        vec![Command::Move {
            unit: P1,
            to: hex(1, 1)
        }],
        "the first matching BFS candidate wins"
    );
}

#[test]
fn occupied_candidates_are_skipped_and_a_sealed_or_targetless_actor_ends_turn() {
    let mut state = approach_board(P1, hex(0, 1), hex(2, 2));
    state.units[P2 as usize].pos = hex(1, 1); // first BFS candidate
    assert_fixture(&state);
    assert_eq!(
        plan_turn(&state),
        vec![Command::Move {
            unit: P1,
            to: hex(0, 2)
        }],
        "occupied destinations are absent from the BFS list"
    );

    // At this map corner all exits are occupied, so the enemy's firing positions are unreachable.
    let mut sealed = board(P1, MeleeReach::TwoHex);
    sealed.units[P1 as usize].pos = hex(-4, 0);
    sealed.units[P2 as usize].pos = hex(-3, 0);
    sealed.units[P3 as usize].pos = hex(-3, -1);
    sealed.units.push(Unit::new(
        6,
        "Blocker",
        Side::Player,
        sealed.units[P1 as usize].stats.clone(),
        hex(-4, 1),
    ));
    sealed.units[E1 as usize].pos = hex(4, 0);
    sealed.units[E2 as usize].pos = hex(4, -4);
    sealed.units[E3 as usize].pos = hex(4, -3);
    assert_fixture(&sealed);
    let occupied: Vec<_> = sealed
        .units
        .iter()
        .filter(|u| u.is_present())
        .map(|u| u.pos)
        .collect();
    assert_eq!(
        bfs_distances(sealed.units[P1 as usize].pos, &occupied),
        vec![(hex(-4, 0), 0)]
    );
    assert!(bfs_path(hex(-4, 0), hex(3, 0), &occupied).is_none());
    assert_eq!(plan_turn(&sealed), vec![Command::EndTurn { unit: P1 }]);

    let mut no_enemy = approach_board(P1, hex(-1, -2), hex(1, -2));
    for enemy in [E1, E2, E3] {
        no_enemy.units[enemy as usize].alive = false;
    }
    assert_eq!(plan_turn(&no_enemy), vec![Command::EndTurn { unit: P1 }]);
}

#[test]
fn movement_uses_the_longest_affordable_prefix_and_agrees_with_previews() {
    let mut state = approach_board(P1, hex(-3, 0), hex(3, 0));
    let (_, path) = chosen_candidate(&state, P1, E1);
    assert!(path.len() > 1, "fixture needs a multi-step route: {path:?}");
    state.units[P1 as usize].ap = MOVE_AP + 1; // one ground step, with one AP left over
    let expected = Command::Move {
        unit: P1,
        to: path[0],
    };
    assert_eq!(plan_turn(&state), vec![expected]);
    let first = state
        .preview_move(P1, path[0])
        .expect("first affordable prefix previews");
    assert_eq!(first.path, vec![path[0]]);
    assert_eq!(first.ap_cost, MOVE_AP);
    assert_eq!(state.preview_move(P1, path[1]), Err(RuleError::NotEnoughAp));
    state
        .apply(expected)
        .expect("previewed partial move executes");
    assert_eq!(state.units[P1 as usize].pos, path[0]);

    let mut no_step = approach_board(P1, hex(0, 1), hex(2, 2));
    no_step.units[P1 as usize].ap = MOVE_AP - 1;
    assert_eq!(plan_turn(&no_step), vec![Command::EndTurn { unit: P1 }]);
}

#[test]
fn uphill_swamp_pathfinder_and_checked_fatigue_costs_bound_the_move_prefix() {
    let from = hex(0, 1);
    let target = hex(2, 2);
    let chosen = hex(1, 1);

    // The earliest BFS candidate remains selected even when a later equal-step candidate is cheaper.
    let mut too_steep = approach_board(P1, from, target);
    too_steep.elevations.set(chosen, 2).unwrap();
    too_steep.units[P1 as usize].ap = MOVE_AP;
    assert_eq!(
        too_steep.preview_move(P1, chosen),
        Err(RuleError::NotEnoughAp)
    );
    assert_eq!(
        too_steep.preview_move(P1, hex(0, 2)).unwrap().ap_cost,
        MOVE_AP
    );
    assert_eq!(plan_turn(&too_steep), vec![Command::EndTurn { unit: P1 }]);

    let mut uphill = approach_board(P1, from, target);
    uphill.elevations.set(chosen, 2).unwrap();
    uphill.units[P1 as usize].ap = MOVE_AP + 2;
    uphill.units[P1 as usize].stats.fatigue_max = 2;
    let preview = uphill
        .preview_move(P1, chosen)
        .expect("exact uphill AP and fatigue");
    assert_eq!((preview.ap_cost, preview.fatigue_cost), (MOVE_AP + 2, 2));
    let command = Command::Move {
        unit: P1,
        to: chosen,
    };
    assert_eq!(plan_turn(&uphill), vec![command]);
    uphill
        .apply(command)
        .expect("uphill previewed move executes");

    let mut swamp = approach_board(P1, from, target);
    swamp.ground.set(chosen, TerrainKind::Swamp).unwrap();
    swamp.units[P1 as usize].ap = 4;
    swamp.units[P1 as usize].stats.fatigue_max = 4;
    let preview = swamp
        .preview_move(P1, chosen)
        .expect("exact Swamp AP and fatigue");
    assert_eq!((preview.ap_cost, preview.fatigue_cost), (4, 4));
    let command = Command::Move {
        unit: P1,
        to: chosen,
    };
    assert_eq!(plan_turn(&swamp), vec![command]);
    swamp.apply(command).expect("Swamp previewed move executes");

    let mut pathfinder = approach_board(P1, from, target);
    pathfinder.units[P1 as usize].ap = MOVE_AP;
    pathfinder.units[P1 as usize].stats.fatigue_max = PATHFINDER_MOVE_FATIGUE;
    pathfinder.units[P1 as usize].has_pathfinder = true;
    let preview = pathfinder
        .preview_move(P1, chosen)
        .expect("Pathfinder exact fatigue cap");
    assert_eq!(preview.fatigue_cost, PATHFINDER_MOVE_FATIGUE);
    let command = Command::Move {
        unit: P1,
        to: chosen,
    };
    assert_eq!(plan_turn(&pathfinder), vec![command]);
    pathfinder
        .apply(command)
        .expect("Pathfinder previewed move executes");

    let mut overflow = approach_board(P1, from, target);
    overflow.units[P1 as usize].stats.fatigue_max = u32::MAX;
    overflow.units[P1 as usize].fatigue = u32::MAX;
    assert_eq!(
        overflow.preview_move(P1, chosen),
        Err(RuleError::TooFatigued)
    );
    assert_eq!(plan_turn(&overflow), vec![Command::EndTurn { unit: P1 }]);
}
