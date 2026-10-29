//! `BattleState::preview_move`: deterministic route and cost plan shared with
//! `Command::Move`, plus the first departure where enemies could react
//! (APPROXIMATION of UC-COMBAT-005/007/008). Fixtures park every unit far away
//! and place actors on the obstacle-free lane `r = -2`. All stats start at
//! skill 0 / defense 0 (5% base chance); tests raise a controller's skill to
//! 200 (95%) to force reaction hits.

use bb_rules::hex::{bfs_path, hex, map_hexes};
use bb_rules::rules::{self, ATTACK_AP, ATTACK_FATIGUE, MOVE_AP, MOVE_FATIGUE, MovePreview};
use bb_rules::*;

const PARK_PLAYERS: [Hex; 3] = [hex(-4, 2), hex(-4, 3), hex(-4, 4)];
const PARK_ENEMIES: [Hex; 3] = [hex(4, -4), hex(4, -3), hex(4, -2)];

const P1: u32 = 0;
const P2: u32 = 1;
const E1: u32 = 3;
const E2: u32 = 4;
const E3: u32 = 5;

/// All units parked, stats zeroed, `active` first in the queue with 9 AP.
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
        assert!(
            map_hexes().contains(h) && !h.is_obstacle(),
            "fixture hex must be walkable"
        );
        assert!(!at[..i].contains(h), "fixture hexes must be distinct");
    }
    s
}

/// The task's lane fixture: mover (-2,-2), enemy (1,-3), destination (2,-2).
fn lane() -> BattleState {
    board(1, P1, &[(P1, hex(-2, -2)), (E1, hex(1, -3))])
}

fn preview(s: &BattleState, to: Hex) -> MovePreview {
    s.preview_move(P1, to).expect("preview should succeed")
}

fn kill(s: &mut BattleState, ids: &[u32]) {
    for &id in ids {
        s.units[id as usize].alive = false;
        s.queue.retain(|&q| q != id);
    }
}

fn departure(p: &MovePreview) -> Option<(Hex, Vec<u32>)> {
    p.first_threat
        .as_ref()
        .map(|t| (t.departure, t.controllers.clone()))
}

#[test]
fn lane_route_reports_first_threat_at_the_fixed_departure() {
    let s = lane();
    let p = preview(&s, hex(2, -2));
    assert_eq!(
        p.path,
        vec![hex(-1, -2), hex(0, -2), hex(1, -2), hex(2, -2)]
    );
    let occupied: Vec<Hex> = s.units.iter().map(|u| u.pos).collect();
    assert_eq!(
        Some(p.path.clone()),
        bfs_path(hex(-2, -2), hex(2, -2), &occupied),
        "same route as Move uses"
    );
    assert_eq!((p.ap_cost, p.fatigue_cost), (4 * MOVE_AP, 4 * MOVE_FATIGUE));
    // (0,-2) is the first path hex adjacent to (1,-3); the later (1,-2) departure is not reported.
    assert_eq!(departure(&p), Some((hex(0, -2), vec![E1])));
}

#[test]
fn preview_reports_remaining_ap_below_at_and_above_attack_cost_on_flat_and_uphill_routes() {
    for (start, destination, rise) in [(hex(0, -2), hex(1, -2), 0u8), (hex(-2, 0), hex(-1, 0), 1u8)] {
        let route_cost = MOVE_AP + u32::from(rise);
        for remaining in [ATTACK_AP - 1, ATTACK_AP, ATTACK_AP + 1] {
            let mut s = board(1, P1, &[(P1, start)]);
            s.elevations.set(destination, rise).unwrap();
            s.units[P1 as usize].ap = route_cost + remaining;
            let p = preview(&s, destination);
            assert_eq!(p.path, vec![destination]);
            assert_eq!(p.ap_cost, route_cost);
            assert_eq!(p.ap_remaining, remaining);
            assert_eq!(p.attack_budget_available, remaining >= ATTACK_AP, "rise {rise}, remaining {remaining}");
        }
    }
}

#[test]
fn melee_budget_checks_fatigue_cap_and_attack_fatigue_overflow() {
    let destination = hex(-1, -2);
    let mut exact = board(1, P1, &[(P1, hex(-2, -2))]);
    exact.units[P1 as usize].ap = MOVE_AP + ATTACK_AP;
    exact.units[P1 as usize].stats.fatigue_max = MOVE_FATIGUE + ATTACK_FATIGUE;
    let p = preview(&exact, destination);
    assert_eq!(p.ap_remaining, ATTACK_AP);
    assert!(p.attack_budget_available, "exact fatigue-cap affordability is accepted");

    let mut short_fatigue = exact.clone();
    short_fatigue.units[P1 as usize].stats.fatigue_max -= 1;
    let p = preview(&short_fatigue, destination);
    assert_eq!(p.ap_remaining, ATTACK_AP);
    assert!(!p.attack_budget_available, "sufficient AP cannot hide an insufficient fatigue cap");

    let mut overflow = exact;
    overflow.units[P1 as usize].fatigue = u32::MAX - MOVE_FATIGUE;
    overflow.units[P1 as usize].stats.fatigue_max = u32::MAX;
    let p = preview(&overflow, destination);
    assert_eq!(p.fatigue_cost, MOVE_FATIGUE);
    assert!(!p.attack_budget_available, "adding attack fatigue to the post-move maximum must not overflow");
}

#[test]
fn melee_budget_needs_no_adjacent_target_and_is_nominal_before_reactions() {
    // A one-step move leaves exactly ATTACK_AP with no target adjacent to arrival.
    let mut no_target = board(1, P1, &[(P1, hex(0, -2))]);
    no_target.units[P1 as usize].ap = MOVE_AP + ATTACK_AP;
    let plan = preview(&no_target, hex(1, -2));
    assert!(plan.attack_budget_available);
    assert!(plan.first_threat.is_none());
    let mut arrived = no_target.clone();
    arrived.apply(Command::Move { unit: P1, to: hex(1, -2) }).unwrap();
    assert!(arrived.legal_targets(P1).is_empty(), "budget is not conditional on a target");

    // Seed 1's 95% reaction hits while leaving the nominal full-route budget at
    // the exact fatigue cap. Its added fatigue makes a later attack unaffordable.
    let mut threatened = lane();
    threatened.units[P1 as usize].ap = 12;
    threatened.units[P1 as usize].stats.fatigue_max = 2 * 4 + ATTACK_FATIGUE;
    threatened.units[E1 as usize].stats.melee_skill = 200;
    let destination = hex(2, -2);
    let plan = preview(&threatened, destination);
    assert!(plan.first_threat.is_some());
    assert_eq!(plan.ap_remaining, ATTACK_AP);
    assert!(plan.attack_budget_available, "the full planned route fits before reaction effects");

    let mut resolved = threatened.clone();
    let events = resolved.apply(Command::Move { unit: P1, to: destination }).unwrap();
    assert!(events.iter().any(|e| matches!(e, Event::Attacked { attacker: E1, target: P1, hit: true, .. })));
    let moved: Vec<_> = events.iter().filter_map(|e| match e {
        Event::Moved { unit: P1, path } => Some(path.clone()),
        _ => None,
    }).flatten().collect();
    assert!(moved.len() < plan.path.len(), "the reaction prevents nominal arrival");
    assert_eq!(
        resolved.apply(Command::Attack { attacker: P1, target: E1 }).unwrap_err(),
        RuleError::TooFatigued,
        "reaction fatigue is outside the nominal preview budget"
    );
}

#[test]
fn uncontested_move_attack_budget_matches_attack_acceptance_and_rejection() {
    let destination = hex(1, -2);
    for (ap, fatigue_max, expected_error) in [
        (10, 3 * MOVE_FATIGUE + ATTACK_FATIGUE, None),
        (9, 3 * MOVE_FATIGUE + ATTACK_FATIGUE, Some(RuleError::NotEnoughAp)),
        (10, 3 * MOVE_FATIGUE + ATTACK_FATIGUE - 1, Some(RuleError::TooFatigued)),
    ] {
        let mut s = board(2, P1, &[(P1, hex(-2, -2)), (E1, hex(2, -2))]);
        s.units[P1 as usize].ap = ap;
        s.units[P1 as usize].stats.fatigue_max = fatigue_max;
        let p = preview(&s, destination);
        assert_eq!(p.ap_cost, 3 * MOVE_AP);
        assert_eq!(p.fatigue_cost, 3 * MOVE_FATIGUE);
        assert!(p.first_threat.is_none(), "the enemy is only adjacent at the destination");
        assert_eq!(p.attack_budget_available, expected_error.is_none());

        let mut resolved = s;
        resolved.apply(Command::Move { unit: P1, to: destination }).unwrap();
        assert_eq!(resolved.legal_targets(P1), vec![E1]);
        let result = resolved.apply(Command::Attack { attacker: P1, target: E1 });
        match expected_error {
            None => assert!(result.is_ok(), "previewed melee budget should be accepted"),
            Some(error) => assert_eq!(result.unwrap_err(), error),
        }
    }
}

#[test]
fn preview_is_repeatable_and_leaves_state_and_rng_untouched() {
    let s = lane();
    let (hash, rng) = (s.state_hash(), s.rng.clone());
    let first = preview(&s, hex(2, -2));
    for _ in 0..5 {
        assert_eq!(preview(&s, hex(2, -2)), first);
        let _ = s.preview_move(P1, hex(0, 0));
        let _ = s.preview_move(E1, hex(2, -2));
    }
    assert_eq!(s.state_hash(), hash);
    assert_eq!(s.rng, rng);
    assert_eq!(s.units[P1 as usize].ap, 9);
    assert_eq!(s.units[P1 as usize].pos, hex(-2, -2));
}

#[test]
fn start_hex_control_is_the_first_threat_departure() {
    // Mover starts adjacent to the enemy and walks away.
    let s = board(1, P1, &[(P1, hex(0, -2)), (E1, hex(1, -3))]);
    let p = preview(&s, hex(-2, -2));
    assert_eq!(p.path.len(), 2);
    assert_eq!(departure(&p), Some((hex(0, -2), vec![E1])));
}

#[test]
fn entering_enemy_adjacency_only_at_the_destination_is_safe() {
    let s = lane();
    let p = preview(&s, hex(-1, -2));
    assert_eq!(p.path, vec![hex(-1, -2)]);
    assert!(p.first_threat.is_none());
    // (0,-2) is adjacent to the enemy: reaching it as the last step is still safe.
    let p = preview(&s, hex(0, -2));
    assert_eq!(p.path, vec![hex(-1, -2), hex(0, -2)]);
    assert_eq!((p.ap_cost, p.fatigue_cost), (2 * MOVE_AP, 2 * MOVE_FATIGUE));
    assert!(p.first_threat.is_none());
}

#[test]
fn threat_after_earlier_safe_steps_names_the_later_departure() {
    let s = board(1, P1, &[(P1, hex(-2, -2)), (E1, hex(2, -3))]);
    let p = preview(&s, hex(2, -2));
    // Control starts at (1,-2); (-2,-2), (-1,-2) and (0,-2) are safe departures.
    assert_eq!(
        p.path,
        vec![hex(-1, -2), hex(0, -2), hex(1, -2), hex(2, -2)]
    );
    assert_eq!(departure(&p), Some((hex(1, -2), vec![E1])));
}

#[test]
fn multiple_controllers_keep_id_order_and_dead_or_allied_units_are_ignored() {
    let mut s = board(
        1,
        P1,
        &[
            (P1, hex(0, -2)),
            (E1, hex(1, -3)),
            (E2, hex(0, -3)),
            (E3, hex(-1, -1)),
            (P2, hex(1, -2)),
        ],
    );
    let p = preview(&s, hex(-2, -2));
    // E1 (1,-3), E2 (0,-3) and E3 (-1,-1) all touch (0,-2); the ally at (1,-2) does not control.
    assert_eq!(departure(&p), Some((hex(0, -2), vec![E1, E2, E3])));
    assert_eq!(s.enemy_controllers_at(P1, hex(0, -2)), vec![E1, E2, E3]);

    kill(&mut s, &[E2]);
    assert_eq!(
        departure(&preview(&s, hex(-2, -2))),
        Some((hex(0, -2), vec![E1, E3]))
    );
    kill(&mut s, &[E1, E3]);
    assert!(preview(&s, hex(-2, -2)).first_threat.is_none());
}

#[test]
fn preview_refreshes_after_enemy_movement_or_death() {
    let mut s = lane();
    assert_eq!(
        departure(&preview(&s, hex(2, -2))),
        Some((hex(0, -2), vec![E1]))
    );

    s.units[E1 as usize].pos = hex(2, -3);
    assert_eq!(
        departure(&preview(&s, hex(2, -2))),
        Some((hex(1, -2), vec![E1]))
    );

    s.units[E1 as usize].pos = hex(4, -4);
    assert!(preview(&s, hex(2, -2)).first_threat.is_none());

    s.units[E1 as usize].pos = hex(1, -3);
    kill(&mut s, &[E1]);
    assert!(preview(&s, hex(2, -2)).first_threat.is_none());
}

/// Runs the same destination through both APIs on clones and compares errors.
fn assert_error_parity(s: &BattleState, unit: u32, to: Hex, expected: RuleError) {
    let preview_err = s.preview_move(unit, to).expect_err("preview must fail");
    let apply_err = s
        .clone()
        .apply(Command::Move { unit, to })
        .expect_err("move must fail");
    assert_eq!(preview_err, expected);
    assert_eq!(apply_err, expected);
}

#[test]
fn preview_errors_match_move_errors() {
    let far = hex(2, -2);

    let mut over = lane();
    over.outcome = Some(Outcome::Victory);
    assert_error_parity(&over, P1, far, RuleError::BattleOver);
    assert_error_parity(&over, E1, far, RuleError::BattleOver);

    let s = lane();
    assert_error_parity(&s, P2, far, RuleError::NotActiveUnit);
    assert_error_parity(&s, E1, far, RuleError::NotActiveUnit);
    assert_error_parity(&s, 99, far, RuleError::NotActiveUnit);

    // Unreachable: obstacle, own hex, occupied hex, off-map.
    for to in [hex(0, 0), hex(-2, -2), hex(1, -3), hex(9, 9)] {
        assert_error_parity(&s, P1, to, RuleError::Unreachable);
    }

    let mut low_ap = lane();
    low_ap.units[0].ap = 4 * MOVE_AP - 1;
    assert_error_parity(&low_ap, P1, far, RuleError::NotEnoughAp);
    low_ap.units[0].ap = 4 * MOVE_AP;
    assert!(low_ap.preview_move(P1, far).is_ok());
    assert!(
        low_ap
            .clone()
            .apply(Command::Move { unit: P1, to: far })
            .is_ok()
    );

    let fmax = lane().units[0].stats.fatigue_max;
    let mut tired = lane();
    tired.units[0].fatigue = fmax - 4 * MOVE_FATIGUE + 1;
    assert_error_parity(&tired, P1, far, RuleError::TooFatigued);
    tired.units[0].fatigue = fmax - 4 * MOVE_FATIGUE; // exact cap is affordable
    let p = tired.preview_move(P1, far).expect("exact cap preview");
    assert_eq!(p.fatigue_cost, 4 * MOVE_FATIGUE);
    assert!(
        tired
            .clone()
            .apply(Command::Move { unit: P1, to: far })
            .is_ok()
    );

    let mut overflow = lane();
    overflow.units[0].fatigue = u32::MAX;
    assert_error_parity(&overflow, P1, far, RuleError::TooFatigued);
    overflow.units[0].fatigue = u32::MAX - 1;
    assert_error_parity(&overflow, P1, far, RuleError::TooFatigued);

    // Precedence is preserved: Unreachable, then AP, then fatigue.
    let mut both = lane();
    both.units[0].ap = 0;
    both.units[0].fatigue = u32::MAX;
    assert_error_parity(&both, P1, far, RuleError::NotEnoughAp);
    assert_error_parity(&both, P1, hex(0, 0), RuleError::Unreachable);
}

#[test]
fn uncontested_preview_matches_execution() {
    let s = board(1, P1, &[(P1, hex(-2, -2)), (E1, hex(1, -4))]);
    let to = hex(1, -2);
    let p = preview(&s, to);
    assert!(p.first_threat.is_none());

    let mut run = s.clone();
    let ev = run.apply(Command::Move { unit: P1, to }).unwrap();
    assert_eq!(
        ev,
        vec![Event::Moved {
            unit: P1,
            path: p.path.clone()
        }]
    );
    let (before, after) = (&s.units[0], &run.units[0]);
    assert_eq!(before.ap - after.ap, p.ap_cost);
    assert_eq!(after.fatigue - before.fatigue, p.fatigue_cost);
    assert_eq!(after.pos, to);
    assert_eq!(run.rng, s.rng, "an uncontested move consumes no RNG");
}

#[test]
fn threatened_preview_can_resolve_to_a_shorter_move() {
    let mut s = lane();
    s.units[E1 as usize].stats.melee_skill = 200; // reaction hit is near-certain (95%)
    let to = hex(2, -2);
    let p = preview(&s, to);
    assert_eq!(departure(&p), Some((hex(0, -2), vec![E1])));

    let mut run = s.clone();
    let ev = run.apply(Command::Move { unit: P1, to }).unwrap();
    let moved: Vec<Hex> = ev
        .iter()
        .filter_map(|e| match e {
            Event::Moved { path, .. } => Some(path.clone()),
            _ => None,
        })
        .flatten()
        .collect();
    // Seed 1's first draw is 65 < 95: the reaction at departure (0,-2) hits and stops the walk.
    assert!(
        matches!(ev[1], Event::Attacked { attacker, target, hit: true, .. } if attacker == E1 && target == P1)
    );
    assert_eq!(moved, vec![hex(-1, -2), hex(0, -2)]);
    assert!(
        moved.len() < p.path.len(),
        "the planned arrival is conditional"
    );
    let (before, after) = (&s.units[0], &run.units[0]);
    assert_eq!(after.pos, hex(0, -2));
    // Three steps were paid (the third was stopped), plus the defender fatigue gain.
    assert_eq!(before.ap - after.ap, 3 * MOVE_AP);
    assert_eq!(
        after.fatigue - before.fatigue,
        3 * MOVE_FATIGUE + rules::DEFENDER_HIT_FATIGUE_GAIN
    );
    assert_ne!(run.rng, s.rng, "reactions consume RNG, previews never do");
    assert_eq!(
        s.preview_move(P1, to).unwrap(),
        p,
        "the original state is unchanged"
    );
}

// --- Map-edge penalty warning (UC-COMBAT-048, APPROXIMATION) ------------------------
//
// `first_edge_penalty` is a conditional route consequence from the mover's current
// morale: the first arrival hex that would trigger the penalty if reached. It is
// not a prediction of arrival, and reactions are not simulated.

/// (inner hex, edge hex) pairs on several map sides.
const EDGE_STEPS: [(Hex, Hex); 5] = [
    (hex(3, -2), hex(4, -2)),
    (hex(-3, 0), hex(-4, 0)),
    (hex(0, 3), hex(0, 4)),
    (hex(0, -3), hex(0, -4)),
    (hex(-2, 3), hex(-2, 4)),
];

/// `board` with the parked enemies E2/E3 moved clear of every edge fixture route.
fn edge_board(mover: u32, start: Hex) -> BattleState {
    let s = board(1, mover, &[(mover, start), (E2, hex(3, 1)), (E3, hex(2, 2))]);
    s
}

fn edge_preview(s: &BattleState, mover: u32, to: Hex) -> MovePreview {
    s.preview_move(mover, to).expect("preview should succeed")
}

fn edge_event_hexes(ev: &[Event]) -> Vec<Hex> {
    ev.iter()
        .filter_map(|e| match e {
            Event::EdgeMoraleChanged { at, .. } => Some(*at),
            _ => None,
        })
        .collect()
}

#[test]
fn edge_destination_is_reported_for_both_sides_and_eligible_morales_only() {
    for mover in [P1, E1] {
        for (inner, edge) in EDGE_STEPS {
            for morale in [Morale::Confident, Morale::Steady, Morale::Wavering, Morale::Breaking] {
                let mut s = edge_board(mover, inner);
                s.units[mover as usize].morale = morale;
                let (hash, rng) = (s.state_hash(), s.rng.clone());
                let p = edge_preview(&s, mover, edge);
                assert_eq!(p.path, vec![edge], "{mover} {morale:?} {edge:?}");
                let want = (morale != Morale::Breaking).then_some(edge);
                assert_eq!(p.first_edge_penalty, want, "{mover} {morale:?} {edge:?}");
                assert_eq!((s.state_hash(), s.rng.clone()), (hash, rng), "preview is pure");
            }
        }
    }
}

#[test]
fn interior_routes_and_initial_edge_placement_alone_report_nothing() {
    // Interior route on the lane: the first threat is unrelated to the edge.
    let s = lane();
    assert_eq!(preview(&s, hex(2, -2)).first_edge_penalty, None);
    assert!(preview(&s, hex(2, -2)).first_threat.is_some());

    // Starting on an edge hex and walking inwards: the start is not an arrival.
    let s = edge_board(P1, hex(-2, -2));
    assert!(hex(-2, -2).distance(hex(0, 0)) == 4, "fixture start is an edge hex");
    let p = edge_preview(&s, P1, hex(1, -2));
    assert_eq!(p.path[0], hex(-1, -2));
    assert_eq!(p.first_edge_penalty, None);
}

#[test]
fn edge_to_edge_moves_report_the_first_arrival_including_intermediate_hexes() {
    let start = hex(-2, -2);
    let dest = hex(0, -4);
    let s = edge_board(P1, start);
    let p = edge_preview(&s, P1, dest);
    // The route runs along the edge; the intermediate arrival comes first, not the destination.
    assert_eq!(p.path, vec![hex(-1, -3), dest]);
    assert_eq!(p.first_edge_penalty, Some(hex(-1, -3)));

    // Already Breaking: nothing to warn about.
    let mut b = s.clone();
    b.units[P1 as usize].morale = Morale::Breaking;
    assert_eq!(edge_preview(&b, P1, dest).first_edge_penalty, None);

    // Single edge-to-edge step reports the destination, never the start.
    let p = edge_preview(&s, P1, hex(-1, -3));
    assert_eq!(p.first_edge_penalty, Some(hex(-1, -3)));
}

#[test]
fn uncontested_preview_matches_the_first_edge_event_on_a_clone() {
    let routes = [
        (hex(-2, -2), hex(0, -4)),  // intermediate edge arrival, edge destination
        (hex(3, -2), hex(4, -2)),   // edge destination
        (hex(-2, -2), hex(1, -2)),  // starts on the edge, interior route
        (hex(-1, -2), hex(2, -2)),  // interior route
    ];
    for mover in [P1, E1] {
        for morale in [Morale::Confident, Morale::Steady, Morale::Wavering, Morale::Breaking] {
            for (start, to) in routes {
                let mut s = edge_board(mover, start);
                s.units[mover as usize].morale = morale;
                let p = edge_preview(&s, mover, to);
                assert!(p.first_threat.is_none(), "fixture is uncontested");
                let ev = s.clone().apply(Command::Move { unit: mover, to }).unwrap();
                let hexes = edge_event_hexes(&ev);
                assert!(hexes.len() <= 1, "only the first arrival changes morale");
                assert_eq!(p.first_edge_penalty, hexes.first().copied(), "{mover} {morale:?} {start:?}->{to:?}");
            }
        }
    }
}

#[test]
fn threatened_preview_reports_the_edge_even_when_execution_never_arrives() {
    // P1 on (3,-2) heads for the edge hex (4,-2); E1 touches the start and hits at 95%.
    let setup = || {
        let mut s = board(1, P1, &[(P1, hex(3, -2)), (E1, hex(2, -1)), (E2, hex(3, 1)), (E3, hex(2, 2))]);
        s.units[E1 as usize].stats.melee_skill = 1000;
        s.units[P1 as usize].morale = Morale::Steady;
        s
    };
    let to = hex(4, -2);

    // Stopped by the reaction: no edge event, yet the preview still warns.
    let s = setup();
    let (hash, rng) = (s.state_hash(), s.rng.clone());
    let p = edge_preview(&s, P1, to);
    assert_eq!(departure(&p), Some((hex(3, -2), vec![E1])));
    assert_eq!(p.first_edge_penalty, Some(to), "an earlier reaction must not hide the warning");
    assert_eq!((s.state_hash(), s.rng.clone()), (hash, rng));
    let mut run = s.clone();
    let ev = run.apply(Command::Move { unit: P1, to }).unwrap();
    assert!(matches!(ev[0], Event::Attacked { hit: true, .. }), "{ev:?}");
    assert!(edge_event_hexes(&ev).is_empty());
    assert_eq!(run.units[P1 as usize].pos, hex(3, -2));
    assert_eq!(run.units[P1 as usize].morale, Morale::Steady);

    // Killed by the reaction.
    let mut s = setup();
    s.units[P1 as usize].hp = 1;
    s.units[P1 as usize].armour = 0;
    let p = edge_preview(&s, P1, to);
    assert_eq!(p.first_edge_penalty, Some(to));
    let mut run = s.clone();
    let ev = run.apply(Command::Move { unit: P1, to }).unwrap();
    assert!(ev.contains(&Event::Died { unit: P1 }), "{ev:?}");
    assert!(edge_event_hexes(&ev).is_empty());
    assert_eq!(s.preview_move(P1, to).unwrap(), p, "original state is unchanged");
}

#[test]
fn edge_warning_does_not_change_route_costs_or_threat_or_errors() {
    // Same fixture with and without eligibility: only the new field differs.
    let mut eligible = edge_board(P1, hex(3, -2));
    eligible.units[E2 as usize].pos = hex(3, -1);
    let mut breaking = eligible.clone();
    breaking.units[P1 as usize].morale = Morale::Breaking;
    let (a, b) = (edge_preview(&eligible, P1, hex(4, -2)), edge_preview(&breaking, P1, hex(4, -2)));
    assert_eq!((&a.path, a.ap_cost, a.fatigue_cost, &a.first_threat), (&b.path, b.ap_cost, b.fatigue_cost, &b.first_threat));
    assert!(a.first_threat.is_some());
    assert_eq!((a.first_edge_penalty, b.first_edge_penalty), (Some(hex(4, -2)), None));

    // Errors keep winning over the warning.
    let mut s = eligible.clone();
    s.units[P1 as usize].ap = 0;
    assert_eq!(s.preview_move(P1, hex(4, -2)).unwrap_err(), RuleError::NotEnoughAp);
    s.units[P1 as usize].ap = 9;
    s.units[P1 as usize].retreating = true;
    assert_eq!(s.preview_move(P1, hex(4, -2)).unwrap_err(), RuleError::RetreatingUnit);
}

#[test]
fn automatic_movement_stays_exempt_from_the_edge_penalty() {
    // Flee and Withdraw walk onto the edge without the penalty; the preview API
    // refuses those units, so the field only ever describes ordinary moves.
    let mut s = edge_board(P1, hex(3, -2));
    s.units[P1 as usize].morale = Morale::Fleeing;
    assert_eq!(s.preview_move(P1, hex(4, -2)).unwrap_err(), RuleError::FleeingUnit);
    let ev = s.apply(Command::Flee { unit: P1 }).unwrap();
    assert!(edge_event_hexes(&ev).is_empty());
}
