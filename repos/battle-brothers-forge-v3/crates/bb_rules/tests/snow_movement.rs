//! API-only Snow terrain over the shared destination-cost movement pipeline.
//! Typical snow's 3 AP is catalog-stated by UC-COMBAT-005, not runtime-confirmed;
//! the light-snow 2-AP exception is excluded. Snow fatigue (4 normally, 2 with
//! Pathfinder), destination charging and composition are APPROXIMATIONs.

use bb_rules::ai::plan_turn;
use bb_rules::hex::{Hex, hex, map_hexes};
use bb_rules::rules::{
    self, ATTACK_AP, ATTACK_FATIGUE, DEFENDER_HIT_FATIGUE_GAIN, FOOTWORK_AP, FOOTWORK_FATIGUE,
    MOVE_AP, MOVE_FATIGUE, PATHFINDER_MOVE_FATIGUE, ROTATION_AP, ROTATION_FATIGUE,
    SNOW_FATIGUE_FACTOR, SNOW_MOVE_AP, SWAMP_FATIGUE_FACTOR, SWAMP_MOVE_AP, TERRAIN_HASH_TAG,
};
use bb_rules::scenario::{self, TerrainPreset};
use bb_rules::terrain::MAX_ELEVATION;
use bb_rules::*;

const P1: u32 = 0;
const P2: u32 = 1;
const E1: u32 = 3;
const FMAX: u32 = 100;
const G: TerrainKind = TerrainKind::Ground;
const W: TerrainKind = TerrainKind::Swamp;
const N: TerrainKind = TerrainKind::Snow;

/// Only listed units are present. The first is active, with a started turn.
fn board(placed: &[(u32, Hex)]) -> BattleState {
    let mut s = BattleState::new(1);
    for u in &mut s.units {
        u.alive = false;
        u.turn_started = false;
        u.stats.melee_skill = 0;
        u.stats.defense = 0;
        u.stats.dmg_min = 1;
        u.stats.dmg_max = 1;
    }
    for &(id, pos) in placed {
        s.units[id as usize].alive = true;
        s.units[id as usize].pos = pos;
    }
    s.queue = placed.iter().map(|p| p.0).collect();
    let active = &mut s.units[placed[0].0 as usize];
    active.ap = 9;
    active.fatigue = 0;
    active.turn_started = true;
    s
}

fn set_kind(s: &mut BattleState, at: Hex, kind: TerrainKind) {
    s.ground.set(at, kind).unwrap();
}

fn set_height(s: &mut BattleState, at: Hex, height: u8) {
    s.elevations.set(at, height).unwrap();
}

fn snapshot(s: &BattleState) -> (u64, Rng, Vec<u32>, Vec<Unit>) {
    (
        s.state_hash(),
        s.rng.clone(),
        s.queue.clone(),
        s.units.clone(),
    )
}

fn fnv_continue(mut h: u64, words: &[u64]) -> u64 {
    for w in words {
        for b in w.to_le_bytes() {
            h ^= u64::from(b);
            h = h.wrapping_mul(0x0000_0100_0000_01b3);
        }
    }
    h
}

fn move_path(events: &[Event]) -> Vec<Hex> {
    events
        .iter()
        .filter_map(|e| match e {
            Event::Moved { path, .. } => Some(path.clone()),
            _ => None,
        })
        .flatten()
        .collect()
}

fn first_draw(seed: u64) -> u64 {
    Rng::new(seed).next_u64() % 100
}

fn seed_with_first_roll(chance: u32, hit: bool) -> u64 {
    (0..10_000u64)
        .find(|&seed| (first_draw(seed) < u64::from(chance)) == hit)
        .unwrap()
}

fn rig(s: &mut BattleState, attacker: u32, target: u32, hit: bool) {
    s.rng = Rng::new(seed_with_first_roll(s.hit_chance(attacker, target), hit));
}

#[test]
fn snow_map_is_canonical_replaceable_and_resets_to_ground() {
    let hexes = [hex(2, -3), hex(-3, 1), hex(0, -2), hex(-3, 0), hex(3, 0)];
    let mut forward = TerrainMap::default();
    let mut backward = TerrainMap::default();
    for &at in &hexes {
        forward.set(at, N).unwrap();
    }
    for &at in hexes.iter().rev() {
        backward.set(at, N).unwrap();
    }
    assert_eq!(forward, backward);
    let mut sorted = hexes.to_vec();
    sorted.sort_by_key(|h| (h.q, h.r));
    assert_eq!(
        forward.entries().collect::<Vec<_>>(),
        sorted.iter().map(|&at| (at, N)).collect::<Vec<_>>()
    );
    assert!(!forward.is_all_ground());
    for &at in &hexes {
        assert_eq!(forward.get(at), Some(N));
    }

    let replaced = hexes[0];
    forward.set(replaced, W).unwrap();
    assert_eq!(forward.get(replaced), Some(W));
    forward.set(replaced, N).unwrap();
    assert_eq!(forward.get(replaced), Some(N));
    for &at in &hexes {
        forward.set(at, G).unwrap();
        assert_eq!(forward.get(at), Some(G));
    }
    assert!(forward.is_all_ground());
    assert_eq!(forward, TerrainMap::default());
    forward.set(hex(1, 1), G).unwrap();
    assert!(forward.entries().next().is_none());
}

#[test]
fn snow_can_annotate_obstacles_without_changing_passability() {
    let obstacle = hex(0, 0);
    assert!(obstacle.is_obstacle());
    let mut s = board(&[(P1, hex(-1, 0)), (E1, hex(4, -4))]);
    let old_hash = s.state_hash();
    let routes: Vec<_> = map_hexes().iter().map(|&h| s.preview_move(P1, h)).collect();
    let reach = s.reachable(P1);
    set_kind(&mut s, obstacle, N);
    assert_eq!(s.ground.get(obstacle), Some(N));
    assert_ne!(s.state_hash(), old_hash);
    assert_eq!(s.preview_move(P1, obstacle), Err(RuleError::Unreachable));
    assert_eq!(
        s.apply(Command::Move {
            unit: P1,
            to: obstacle
        }),
        Err(RuleError::Unreachable)
    );
    assert!(!s.reachable(P1).iter().any(|&(at, _)| at == obstacle));
    assert_eq!(
        map_hexes()
            .iter()
            .map(|&h| s.preview_move(P1, h))
            .collect::<Vec<_>>(),
        routes
    );
    assert_eq!(s.reachable(P1), reach);
}

#[test]
fn snow_off_map_access_is_rejected_without_mutation_even_at_extreme_coordinates() {
    let off = [
        hex(5, 0),
        hex(0, -5),
        hex(4, 1),
        hex(-4, -1),
        hex(i32::MAX, 0),
        hex(0, i32::MAX),
        hex(i32::MIN, 0),
        hex(0, i32::MIN),
        hex(i32::MAX, i32::MAX),
        hex(i32::MIN, i32::MIN),
        hex(i32::MAX, i32::MIN),
        hex(i32::MIN, i32::MAX),
    ];
    let mut map = TerrainMap::default();
    map.set(hex(1, -2), N).unwrap();
    let before = map.clone();
    for at in off {
        assert_eq!(map.get(at), None, "{at:?}");
        for kind in [G, W, N] {
            assert_eq!(
                map.set(at, kind),
                Err(TerrainError::OffMap),
                "{at:?} {kind:?}"
            );
            assert_eq!(map, before, "{at:?} {kind:?}");
        }
    }
}

#[test]
fn snow_hash_uses_code_two_and_leaves_old_scenario_hashes_unchanged() {
    let flat = scenario::battle(1, TerrainPreset::Flat);
    let hills = scenario::battle(1, TerrainPreset::Hills);
    assert_eq!(flat.state_hash(), 0x8c00302113cbe668);
    assert_eq!(hills.state_hash(), 0xb759683cdff4bfa2);

    let at = hex(2, -3);
    let mut snow = flat.clone();
    set_kind(&mut snow, at, N);
    assert_eq!(snow.rng, flat.rng, "terrain configuration consumes no RNG");
    let suffix = [u64::from(TERRAIN_HASH_TAG), 1, 2, -3i64 as u64, 2];
    assert_eq!(snow.state_hash(), fnv_continue(flat.state_hash(), &suffix));
    assert_ne!(snow.state_hash(), flat.state_hash());

    let mut swamp = flat.clone();
    set_kind(&mut swamp, at, W);
    assert_ne!(
        snow.state_hash(),
        swamp.state_hash(),
        "Snow code 2 differs from Swamp code 1"
    );

    let other = hex(-1, 2);
    let mut ordered = flat.clone();
    let mut reversed = flat.clone();
    set_kind(&mut ordered, at, N);
    set_kind(&mut ordered, other, W);
    set_kind(&mut reversed, other, W);
    set_kind(&mut reversed, at, N);
    assert_eq!(ordered.state_hash(), reversed.state_hash());
    set_kind(&mut ordered, at, G);
    set_kind(&mut ordered, other, G);
    assert_eq!(ordered.state_hash(), flat.state_hash());
}

#[test]
fn snow_cost_table_uses_the_shared_pathfinder_fatigue_baseline() {
    assert_eq!((MOVE_AP, SWAMP_MOVE_AP, SNOW_MOVE_AP), (2, 4, 3));
    assert_eq!((SWAMP_FATIGUE_FACTOR, SNOW_FATIGUE_FACTOR), (2, 2));
    assert_eq!((MOVE_FATIGUE, PATHFINDER_MOVE_FATIGUE), (2, 1));
    let mut u = BattleState::new(1).units[P1 as usize].clone();
    for pathfinder in [false, true] {
        u.has_pathfinder = pathfinder;
        let baseline = rules::move_fatigue(&u);
        assert_eq!(rules::terrain_move_fatigue(&u, G), baseline);
        assert_eq!(rules::terrain_move_fatigue(&u, W), 2 * baseline);
        assert_eq!(rules::terrain_move_fatigue(&u, N), 2 * baseline);
    }
    assert_eq!(
        (
            rules::terrain_move_ap(G),
            rules::terrain_move_ap(W),
            rules::terrain_move_ap(N)
        ),
        (2, 4, 3)
    );
}

#[test]
fn snow_entry_departure_and_uphill_costs_share_the_move_pipeline_for_both_factions() {
    let from = hex(0, -2);
    let to = hex(1, -2);
    for (actor, other, other_pos) in [(P1, E1, hex(4, -4)), (E1, P1, hex(-4, 4))] {
        for pathfinder in [false, true] {
            for (origin, destination, base_ap, base_fatigue) in [
                (G, N, SNOW_MOVE_AP, 4),
                (N, G, MOVE_AP, 2),
                (W, N, SNOW_MOVE_AP, 4),
                (N, W, SWAMP_MOVE_AP, 4),
            ] {
                for h0 in 0..=MAX_ELEVATION {
                    for h1 in 0..=MAX_ELEVATION {
                        let mut s = board(&[(actor, from), (other, other_pos)]);
                        s.units[actor as usize].has_pathfinder = pathfinder;
                        set_kind(&mut s, from, origin);
                        set_kind(&mut s, to, destination);
                        set_height(&mut s, from, h0);
                        set_height(&mut s, to, h1);
                        let ap = base_ap + u32::from(h1.saturating_sub(h0));
                        let fatigue = if pathfinder {
                            base_fatigue / 2
                        } else {
                            base_fatigue
                        };
                        let before = snapshot(&s);
                        let preview = s.preview_move(actor, to).unwrap();
                        assert_eq!(
                            (preview.path, preview.ap_cost, preview.fatigue_cost),
                            (vec![to], ap, fatigue)
                        );
                        assert_eq!(preview.ap_remaining, 9 - ap);
                        assert_eq!(s.reachable(actor).contains(&(to, ap)), ap <= 9);
                        assert_eq!(snapshot(&s), before, "queries are pure");
                        s.apply(Command::Move { unit: actor, to }).unwrap();
                        let u = &s.units[actor as usize];
                        assert_eq!((u.ap, u.fatigue, u.pos), (9 - ap, fatigue, to));
                        assert_eq!(s.rng, before.1);
                    }
                }
            }
        }
    }
}

#[test]
fn mixed_ground_snow_swamp_route_agrees_across_preview_reachability_and_execution() {
    for actor in [P1, E1] {
        for pathfinder in [false, true] {
            let (start, foe, path) = if actor == P1 {
                (
                    hex(-2, -2),
                    hex(4, -2),
                    [hex(-1, -2), hex(0, -2), hex(1, -2)],
                )
            } else {
                (
                    hex(4, -2),
                    hex(-2, -2),
                    [hex(3, -2), hex(2, -2), hex(1, -2)],
                )
            };
            let mut s = board(&[(actor, start), (if actor == P1 { E1 } else { P1 }, foe)]);
            s.units[actor as usize].has_pathfinder = pathfinder;
            set_kind(&mut s, path[0], G);
            set_kind(&mut s, path[1], N);
            set_kind(&mut s, path[2], W);
            let before = snapshot(&s);
            let preview = s.preview_move(actor, path[2]).unwrap();
            let fatigue = if pathfinder { 1 + 2 + 2 } else { 2 + 4 + 4 };
            assert_eq!(preview.path, path);
            assert_eq!(
                (preview.ap_cost, preview.fatigue_cost, preview.ap_remaining),
                (9, fatigue, 0)
            );
            assert!(!preview.attack_budget_available);
            assert!(s.reachable(actor).contains(&(path[2], 9)));
            assert_eq!(snapshot(&s), before);
            let events = s
                .apply(Command::Move {
                    unit: actor,
                    to: path[2],
                })
                .unwrap();
            assert_eq!(move_path(&events), path);
            let u = &s.units[actor as usize];
            assert_eq!((u.pos, u.ap, u.fatigue), (path[2], 0, fatigue));
            assert_eq!(s.rng, before.1);
        }
    }
}

#[test]
fn snow_exact_ap_and_fatigue_caps_succeed_and_short_budgets_reject_purely() {
    let to = hex(1, -2);
    for pathfinder in [false, true] {
        let step_fatigue = if pathfinder { 2 } else { 4 };
        let make = |ap: u32, fatigue: u32| {
            let mut s = board(&[(P1, hex(0, -2)), (E1, hex(4, -4))]);
            s.units[P1 as usize].has_pathfinder = pathfinder;
            s.units[P1 as usize].ap = ap;
            s.units[P1 as usize].fatigue = fatigue;
            set_kind(&mut s, to, N);
            s
        };

        let mut exact_ap = make(SNOW_MOVE_AP, 0);
        let preview = exact_ap.preview_move(P1, to).unwrap();
        assert_eq!(
            (preview.ap_cost, preview.fatigue_cost, preview.ap_remaining),
            (3, step_fatigue, 0)
        );
        assert!(!preview.attack_budget_available);
        assert!(exact_ap.reachable(P1).contains(&(to, SNOW_MOVE_AP)));
        exact_ap.apply(Command::Move { unit: P1, to }).unwrap();
        assert_eq!(
            (
                exact_ap.units[P1 as usize].ap,
                exact_ap.units[P1 as usize].fatigue
            ),
            (0, step_fatigue)
        );

        let mut short_ap = make(SNOW_MOVE_AP - 1, 0);
        let before = snapshot(&short_ap);
        assert_eq!(short_ap.preview_move(P1, to), Err(RuleError::NotEnoughAp));
        assert_eq!(
            short_ap.apply(Command::Move { unit: P1, to }),
            Err(RuleError::NotEnoughAp)
        );
        assert!(!short_ap.reachable(P1).iter().any(|&(h, _)| h == to));
        assert_eq!(snapshot(&short_ap), before);

        let mut exact_fatigue = make(9, FMAX - step_fatigue);
        let preview = exact_fatigue.preview_move(P1, to).unwrap();
        assert_eq!(preview.fatigue_cost, step_fatigue);
        exact_fatigue.apply(Command::Move { unit: P1, to }).unwrap();
        assert_eq!(exact_fatigue.units[P1 as usize].fatigue, FMAX);

        let mut short_fatigue = make(9, FMAX - step_fatigue + 1);
        let before = snapshot(&short_fatigue);
        assert_eq!(
            short_fatigue.preview_move(P1, to),
            Err(RuleError::TooFatigued)
        );
        assert_eq!(
            short_fatigue.apply(Command::Move { unit: P1, to }),
            Err(RuleError::TooFatigued)
        );
        assert!(!short_fatigue.reachable(P1).iter().any(|&(h, _)| h == to));
        assert_eq!(snapshot(&short_fatigue), before);

        let mut both_short = make(SNOW_MOVE_AP - 1, FMAX);
        assert_eq!(
            both_short.apply(Command::Move { unit: P1, to }),
            Err(RuleError::NotEnoughAp)
        );
    }
}

#[test]
fn snow_move_preview_keeps_the_remaining_attack_budget_calculation() {
    for (ap, fatigue, remaining, expected_attack) in [
        (7, 0, 4, true),
        (6, 0, 3, false),
        (9, 86, 6, true),
        (9, 87, 6, false),
    ] {
        let mut s = board(&[(P1, hex(0, -2)), (E1, hex(4, -4))]);
        s.units[P1 as usize].ap = ap;
        s.units[P1 as usize].fatigue = fatigue;
        set_kind(&mut s, hex(1, -2), N);
        let before = snapshot(&s);
        let p = s.preview_move(P1, hex(1, -2)).unwrap();
        assert_eq!((p.ap_cost, p.ap_remaining), (SNOW_MOVE_AP, remaining));
        assert_eq!(p.attack_budget_available, expected_attack);
        assert_eq!(
            p.attack_budget_available,
            remaining >= ATTACK_AP && fatigue + 4 + ATTACK_FATIGUE <= FMAX
        );
        assert_eq!(snapshot(&s), before);
    }
}

#[test]
fn stopping_opportunity_hit_pays_the_attempted_destination_before_reaction() {
    for mover in [P1, E1] {
        for pathfinder in [false, true] {
            let foe = if mover == P1 { E1 } else { P1 };
            let (start, adjacent, snow_to, last) = if mover == P1 {
                (hex(1, -2), hex(2, -2), hex(0, -2), hex(-1, -2))
            } else {
                (hex(2, -2), hex(1, -2), hex(3, -2), hex(4, -2))
            };
            for snow_on_start in [false, true] {
                let mut step = board(&[(mover, start), (foe, adjacent)]);
                step.units[foe as usize].stats.melee_skill = 50;
                step.units[mover as usize].has_pathfinder = pathfinder;
                set_kind(&mut step, if snow_on_start { start } else { snow_to }, N);
                rig(&mut step, foe, mover, true);
                let first_ap = if snow_on_start { MOVE_AP } else { SNOW_MOVE_AP };
                let first_fatigue = match (snow_on_start, pathfinder) {
                    (true, false) => 2,
                    (true, true) => 1,
                    (false, false) => 4,
                    (false, true) => 2,
                };
                let second_fatigue = if pathfinder { 1 } else { 2 };
                let p = step.preview_move(mover, last).unwrap();
                assert_eq!(
                    (p.ap_cost, p.fatigue_cost),
                    (first_ap + MOVE_AP, first_fatigue + second_fatigue)
                );
                let events = step
                    .apply(Command::Move {
                        unit: mover,
                        to: last,
                    })
                    .unwrap();
                assert!(matches!(
                    events.as_slice(),
                    [Event::Attacked { hit: true, .. }]
                ));
                let u = &step.units[mover as usize];
                assert_eq!(
                    (u.pos, u.ap, u.fatigue),
                    (
                        start,
                        9 - first_ap,
                        first_fatigue + DEFENDER_HIT_FATIGUE_GAIN
                    ),
                    "Snow on start: {snow_on_start}, mover: {mover}, Pathfinder: {pathfinder}"
                );
            }
        }
    }
}

#[test]
fn ai_uses_snow_affordable_prefixes_at_ap_and_fatigue_boundaries() {
    for actor in [P1, E1] {
        let (start, foe, path) = if actor == P1 {
            (
                hex(-1, -2),
                hex(3, -2),
                [hex(0, -2), hex(1, -2), hex(2, -2)],
            )
        } else {
            (
                hex(3, -2),
                hex(-1, -2),
                [hex(2, -2), hex(1, -2), hex(0, -2)],
            )
        };
        for (pathfinder, ap, fatigue, prefix) in [
            (false, 5, 0, 2),
            (true, 5, 0, 2),
            (false, 9, FMAX - 5, 1),
            (true, 9, FMAX - 5, 3),
        ] {
            let mut s = board(&[(actor, start), (if actor == P1 { E1 } else { P1 }, foe)]);
            for at in path {
                set_kind(&mut s, at, if at == path[1] { G } else { N });
            }
            let u = &mut s.units[actor as usize];
            (u.has_pathfinder, u.ap, u.fatigue) = (pathfinder, ap, fatigue);
            let before = snapshot(&s);
            let command = plan_turn(&s);
            assert_eq!(snapshot(&s), before, "AI planning is pure");
            let expected = if prefix == 0 {
                Command::EndTurn { unit: actor }
            } else {
                Command::Move {
                    unit: actor,
                    to: path[prefix - 1],
                }
            };
            assert_eq!(command, vec![expected]);
            if prefix > 0 {
                let p = s.preview_move(actor, path[prefix - 1]).unwrap();
                assert_eq!(p.path, path[..prefix]);
                s.apply(command[0]).unwrap();
                assert_eq!(s.units[actor as usize].pos, path[prefix - 1]);
                assert_eq!(s.units[actor as usize].ap, ap - p.ap_cost);
                assert_eq!(s.units[actor as usize].fatigue, fatigue + p.fatigue_cost);
            }
        }
    }
}

#[derive(Clone, Copy, Debug)]
enum AutoMove {
    Flee,
    Withdraw,
}

impl AutoMove {
    fn command(self) -> Command {
        match self {
            Self::Flee => Command::Flee { unit: P1 },
            Self::Withdraw => Command::Withdraw { unit: P1 },
        }
    }
}

fn escape_board(kind: AutoMove, pathfinder: bool, pos: Hex) -> BattleState {
    let mut s = board(&[(P1, pos), (E1, hex(3, -3)), (P2, hex(3, -1))]);
    match kind {
        AutoMove::Flee => s.units[P1 as usize].morale = Morale::Fleeing,
        AutoMove::Withdraw => s.units[P1 as usize].retreating = true,
    }
    s.units[P1 as usize].has_pathfinder = pathfinder;
    s
}

#[test]
fn flee_and_withdraw_snow_steps_obey_exact_ap_and_fatigue_boundaries() {
    for kind in [AutoMove::Flee, AutoMove::Withdraw] {
        for pathfinder in [false, true] {
            let step_fatigue = if pathfinder { 2 } else { 4 };
            for (ap, fatigue, moves) in [
                (SNOW_MOVE_AP, FMAX - step_fatigue, true),
                (SNOW_MOVE_AP - 1, FMAX - step_fatigue, false),
                (SNOW_MOVE_AP, FMAX - step_fatigue + 1, false),
            ] {
                let mut s = escape_board(kind, pathfinder, hex(-2, 2));
                set_kind(&mut s, hex(-3, 2), N);
                (s.units[P1 as usize].ap, s.units[P1 as usize].fatigue) = (ap, fatigue);
                let events = s.apply(kind.command()).unwrap();
                let u = &s.units[P1 as usize];
                if moves {
                    assert!(
                        matches!(events.first(), Some(Event::Moved { path, .. }) if path == &vec![hex(-3, 2)])
                    );
                    assert_eq!(
                        (u.pos, u.ap, u.fatigue),
                        (hex(-3, 2), ap - SNOW_MOVE_AP, FMAX)
                    );
                } else {
                    assert!(!events.iter().any(|e| matches!(e, Event::Moved { .. })));
                    assert_eq!((u.pos, u.ap, u.fatigue), (hex(-2, 2), ap, fatigue));
                }
            }

            // The edge escape itself stays free even with no AP and a full fatigue bar.
            let mut edge = escape_board(kind, pathfinder, hex(-4, 3));
            set_kind(&mut edge, hex(-4, 3), N);
            edge.units[P1 as usize].ap = 0;
            edge.units[P1 as usize].fatigue = FMAX;
            let events = edge.apply(kind.command()).unwrap();
            assert!(events.iter().any(|e| matches!(e, Event::Fled { unit: P1 })));
            assert!(!events.iter().any(|e| matches!(e, Event::Moved { .. })));
            assert_eq!(
                (edge.units[P1 as usize].ap, edge.units[P1 as usize].fatigue),
                (0, FMAX)
            );
        }
    }
}

#[test]
fn rotation_and_footwork_keep_fixed_costs_when_the_hexes_are_snow() {
    let positions = [hex(-3, 3), hex(-3, 2), hex(-2, 3)];
    let mut base = board(&[(P1, positions[0]), (P2, positions[1]), (E1, hex(3, -3))]);
    let u = &mut base.units[P1 as usize];
    (u.has_rotation, u.has_footwork) = (true, true);
    for at in positions {
        set_kind(&mut base, at, N);
    }

    let mut rotation = base.clone();
    rotation
        .apply(Command::Rotation {
            unit: P1,
            target: P2,
        })
        .unwrap();
    assert_eq!(
        (
            rotation.units[P1 as usize].ap,
            rotation.units[P1 as usize].fatigue
        ),
        (9 - ROTATION_AP, ROTATION_FATIGUE)
    );

    let mut footwork = base;
    let preview = footwork.preview_footwork(P1, positions[2]).unwrap();
    assert_eq!(
        (preview.ap_cost, preview.fatigue_cost),
        (FOOTWORK_AP, FOOTWORK_FATIGUE)
    );
    footwork
        .apply(Command::Footwork {
            unit: P1,
            to: positions[2],
        })
        .unwrap();
    assert_eq!(
        (
            footwork.units[P1 as usize].ap,
            footwork.units[P1 as usize].fatigue
        ),
        (9 - FOOTWORK_AP, FOOTWORK_FATIGUE)
    );
}
