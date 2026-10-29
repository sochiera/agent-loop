//! Basic fleeing lifecycle (partial UC-COMBAT-003/007/018/019/023, APPROXIMATION):
//! morale collapse to Fleeing, loss of control and ZoC, automatic `Flee` turns
//! toward a map edge, and escape that is distinct from death. Fixtures clear
//! the stock scenario (`alive = false`) and place only the units they need;
//! reaction seeds are found by mirroring the first SplitMix64 draw.

use bb_rules::hex::{hex, map_hexes, Hex};
use bb_rules::rules::FLEEING_INITIATIVE_PENALTY;
use bb_rules::*;

const P1: u32 = 0;
const P2: u32 = 1;
const P3: u32 = 2;
const E1: u32 = 3;
const E2: u32 = 4;
const E3: u32 = 5;

fn started(unit: u32) -> Event {
    Event::TurnStarted { unit }
}

/// Only `placed` units are present; the queue follows the given order, so the
/// first entry is the active unit (9 AP, no fatigue). Units listed in `fleeing`
/// are Fleeing.
fn board(seed: u64, placed: &[(u32, Hex)], fleeing: &[u32]) -> BattleState {
    let mut s = BattleState::new(seed);
    for u in &mut s.units {
        u.alive = false;
    }
    for &(id, pos) in placed {
        let u = &mut s.units[id as usize];
        u.alive = true;
        u.pos = pos;
    }
    for &id in fleeing {
        s.units[id as usize].morale = Morale::Fleeing;
    }
    s.queue = placed.iter().map(|p| p.0).collect();
    let active = &mut s.units[placed[0].0 as usize];
    active.ap = 9;
    active.fatigue = 0;
    active.turn_started = true;
    assert_valid_fixture(&s);
    s
}

/// Present units stand on distinct, in-map, passable hexes within their Stats maxima.
fn assert_valid_fixture(state: &BattleState) {
    let present: Vec<&Unit> = state.units.iter().filter(|u| u.is_present()).collect();
    for (i, u) in present.iter().enumerate() {
        assert!(u.pos.in_map() && !u.pos.is_obstacle(), "unit {} on invalid hex {:?}", u.id, u.pos);
        assert!(u.hp <= u.stats.hp && u.armour <= u.stats.armour, "unit {} exceeds its maxima", u.id);
        assert!(present[..i].iter().all(|o| o.pos != u.pos), "unit {} shares a hex", u.id);
    }
}

fn first_draw(seed: u64) -> u64 {
    Rng::new(seed).next_u64() % 100
}

/// First seed whose first roll is below (`hit`) or at/above `chance`.
fn seed_with_first_roll(chance: u32, hit: bool) -> u64 {
    (0..10_000u64).find(|&s| (first_draw(s) < u64::from(chance)) == hit).unwrap()
}

fn snapshot(s: &BattleState) -> (u64, Rng, Vec<u32>) {
    (s.state_hash(), s.rng.clone(), s.queue.clone())
}

// --- Morale state --------------------------------------------------------------

#[test]
fn breaking_lowers_to_fleeing_and_fleeing_stays_fleeing() {
    assert_eq!(Morale::Confident.lowered(), Morale::Steady);
    assert_eq!(Morale::Steady.lowered(), Morale::Wavering);
    assert_eq!(Morale::Wavering.lowered(), Morale::Breaking);
    assert_eq!(Morale::Breaking.lowered(), Morale::Fleeing);
    assert_eq!(Morale::Fleeing.lowered(), Morale::Fleeing);
}

#[test]
fn fleeing_scales_melee_attributes_to_seventy_percent_without_touching_base_stats() {
    assert_eq!(Morale::Fleeing.modifier_percent(), 70);
    let mut s = BattleState::new(1);
    s.units[0].morale = Morale::Fleeing;
    let u = &s.units[0];
    assert_eq!((u.stats.melee_skill, u.stats.defense), (60, 10));
    assert_eq!((u.effective_melee_skill(), u.effective_defense()), (42, 7));
    s.units[0].stats.melee_skill = u32::MAX;
    assert_eq!(s.units[0].effective_melee_skill(), (u64::from(u32::MAX) * 70 / 100) as u32);
    // E1 (skill 50) against the fleeing P1: 50 - 7 = 43, not 50 - 10.
    s.units[0].stats.melee_skill = 60;
    s.units[3].pos = hex(-2, 3);
    assert_eq!(s.hit_chance(3, 0), 43);
}

#[test]
fn failed_check_at_breaking_enters_fleeing_clears_skip_and_keeps_queue_and_draws() {
    let mut s = board(1, &[(E1, hex(2, 0)), (P1, hex(1, 0)), (P2, hex(-3, 2))], &[]);
    s.units[P1 as usize].morale = Morale::Breaking;
    s.units[P1 as usize].stats.resolve = 0;
    s.units[P1 as usize].skip_turn = true;
    for id in [P1, E1] {
        let u = &mut s.units[id as usize];
        u.armour = 0;
        u.stats.armour = 0;
        u.stats.hp = 200;
        u.hp = 200;
    }
    s.units[E1 as usize].stats.dmg_min = 20;
    s.units[E1 as usize].stats.dmg_max = 20;
    let chance = s.hit_chance(E1, P1);
    let seed = (0..10_000u64).find(|&seed| first_draw(seed) < u64::from(chance)).unwrap();
    s.rng = Rng::new(seed);
    // Before: P1 (Breaking) exerts ZoC on a hex next to it.
    assert_eq!(s.enemy_controllers_at(E1, hex(0, 0)), vec![P1]);
    let queue_before = s.queue.clone();
    let events = s.apply(Command::Attack { attacker: E1, target: P1 }).unwrap();
    assert!(matches!(
        events[..],
        [
            Event::Attacked { hit: true, hp_loss: 20, .. },
            Event::MoraleChecked { unit: 0, before: Morale::Breaking, after: Morale::Fleeing, chance: 0, .. }
        ]
    ), "{events:?}");
    assert_eq!(s.rng.state, { let mut r = Rng::new(seed); (0..3).for_each(|_| { r.next_u64(); }); r.state });
    assert_eq!(s.units[P1 as usize].morale, Morale::Fleeing);
    assert!(!s.units[P1 as usize].skip_turn, "pending skip is cleared when entering Fleeing");
    assert_eq!(s.queue, queue_before, "the current queue is not reordered immediately");
    // Immediate loss of ZoC.
    assert!(s.enemy_controllers_at(E1, hex(0, 0)).is_empty());
    assert!(s.enemy_controllers_at(E1, hex(2, 0)).is_empty());
}

#[test]
fn fleeing_check_still_consumes_its_draw() {
    let mut s = board(1, &[(E1, hex(2, 0)), (P1, hex(1, 0)), (P2, hex(-3, 2))], &[P1]);
    let u = &mut s.units[P1 as usize];
    u.armour = 0;
    u.stats.armour = 0;
    u.stats.hp = 200;
    u.hp = 200;
    u.stats.resolve = 0;
    s.units[E1 as usize].stats.dmg_min = 20;
    s.units[E1 as usize].stats.dmg_max = 20;
    let chance = s.hit_chance(E1, P1);
    let seed = (0..10_000u64).find(|&seed| first_draw(seed) < u64::from(chance)).unwrap();
    s.rng = Rng::new(seed);
    let events = s.apply(Command::Attack { attacker: E1, target: P1 }).unwrap();
    assert!(matches!(events[1], Event::MoraleChecked { before: Morale::Fleeing, after: Morale::Fleeing, .. }));
    let mut r = Rng::new(seed);
    (0..3).for_each(|_| { r.next_u64(); });
    assert_eq!(s.rng.state, r.state);
}

#[test]
fn fleeing_units_are_ordered_last_at_the_next_round_sort_with_safe_arithmetic() {
    assert_eq!(FLEEING_INITIATIVE_PENALTY, 1000);
    let mut s = BattleState::new(1);
    s.units[P3 as usize].morale = Morale::Fleeing;
    s.queue = vec![P1];
    s.apply(Command::EndTurn { unit: P1 }).unwrap();
    assert_eq!(s.round, 2);
    // P2/P1 (initiative 100), then the enemies (90); the fleeing P3 drops to the end.
    assert_eq!(s.queue, vec![0, 1, 3, 4, 5, 2]);

    // Extreme initiative neither overflows nor panics; the penalty is still applied.
    let mut s = BattleState::new(1);
    s.units[P3 as usize].morale = Morale::Fleeing;
    s.units[P3 as usize].stats.initiative = u32::MAX;
    s.units[E1 as usize].stats.initiative = 0;
    s.units[E1 as usize].fatigue = 100;
    s.queue = vec![P1];
    s.apply(Command::EndTurn { unit: P1 }).unwrap();
    assert_eq!(s.queue[0], P3, "u32::MAX - 1000 still outranks everything");
    assert_eq!(*s.queue.last().unwrap(), E1);
}

#[test]
fn hash_codes_do_not_collide_and_unchanged_states_keep_their_hash() {
    let base = || board(1, &[(P1, hex(-3, 3)), (E1, hex(3, -3))], &[]);
    let mut seen = Vec::new();
    for morale in [Morale::Steady, Morale::Confident, Morale::Wavering, Morale::Breaking, Morale::Fleeing] {
        let mut s = base();
        s.units[0].morale = morale;
        seen.push(s.state_hash());
        s.units[0].fled = true;
        seen.push(s.state_hash());
    }
    let mut dead = base();
    dead.units[0].alive = false;
    seen.push(dead.state_hash());
    for flag in 0..3 {
        let mut s = base();
        match flag {
            0 => s.units[0].waited = true,
            1 => s.units[0].turn_started = !s.units[0].turn_started,
            _ => s.units[0].skip_turn = true,
        }
        seen.push(s.state_hash());
    }
    let mut unique = seen.clone();
    unique.sort_unstable();
    unique.dedup();
    assert_eq!(unique.len(), seen.len(), "{seen:x?}");

    // The terminal values hash differently from each other and from "running".
    let mut hashes = Vec::new();
    for (o, r) in [
        (None, None),
        (Some(Outcome::Victory), Some(EndReason::Elimination)),
        (Some(Outcome::Defeat), Some(EndReason::Elimination)),
        (Some(Outcome::Defeat), Some(EndReason::TimeLimit)),
        (Some(Outcome::Retreat), Some(EndReason::Escape)),
        (Some(Outcome::Victory), Some(EndReason::Escape)),
    ] {
        let mut s = base();
        (s.outcome, s.end_reason) = (o, r);
        hashes.push(s.state_hash());
    }
    let mut unique = hashes.clone();
    unique.sort_unstable();
    unique.dedup();
    assert_eq!(unique.len(), hashes.len());
}

/// Seed-1 AI battle replay. Through T17 it never reached Fleeing or an escape:
/// its final hash was 0xf152_803a_61ac_c9a3 before ally-death morale checks (T14),
/// 0xccd9_1145_2772_673b before kill morale checks (T17) and 0x3fef_d4ea_d00e_829f
/// before the movement-triggered map-edge morale penalty (T19), each Victory by
/// Elimination in round 4. Since T19 AI movement onto the edge lowers units to
/// Breaking, so a later failed check can make one Fleeing and the battle ends
/// Victory by Escape in round 3. The AI policy is unchanged; the value below is the
/// actual deterministic replay.
#[test]
fn seed_one_ai_battle_replay_reaches_fleeing_after_edge_morale() {
    let mut s = BattleState::new(1);
    while s.outcome().is_none() {
        let cmd = ai::plan_turn(&s)[0];
        s.apply(cmd).unwrap();
    }
    assert!(s.units.iter().any(|u| u.fled));
    assert_eq!((s.outcome(), s.end_reason(), s.round), (Some(Outcome::Victory), Some(EndReason::Escape), 3));
    assert_eq!(s.state_hash(), 0xdfce_9b8d_74bc_af93);
}

// --- Commands, errors and previews ----------------------------------------------

/// P1 Fleeing and active at a non-edge hex next to E1; P2 keeps the battle alive.
fn fleeing_p1() -> BattleState {
    board(7, &[(P1, hex(-3, 2)), (E1, hex(-2, 2)), (P2, hex(2, -3))], &[P1])
}

#[test]
fn ordinary_commands_and_previews_are_rejected_for_a_fleeing_actor_without_side_effects() {
    let mut s = fleeing_p1();
    let before = snapshot(&s);
    let cmds = [
        Command::Move { unit: P1, to: hex(-3, 3) },
        Command::Move { unit: P1, to: hex(9, 9) },
        Command::Attack { attacker: P1, target: E1 },
        Command::Recover { unit: P1 },
        Command::Wait { unit: P1 },
        Command::EndTurn { unit: P1 },
        Command::EndRound { unit: P1 },
    ];
    for cmd in cmds {
        assert_eq!(s.apply(cmd), Err(RuleError::FleeingUnit), "{cmd:?}");
        assert_eq!(snapshot(&s), before, "{cmd:?}");
    }
    // The preview agrees with the ordinary Move rejection and is pure.
    for to in [hex(-3, 3), hex(9, 9), hex(-2, 2)] {
        assert_eq!(s.preview_move(P1, to), Err(RuleError::FleeingUnit));
        assert_eq!(s.apply(Command::Move { unit: P1, to }), Err(RuleError::FleeingUnit));
    }
    assert_eq!(snapshot(&s), before);
}

#[test]
fn fleeing_enemy_actor_is_rejected_the_same_way() {
    let mut s = board(7, &[(E1, hex(3, -2)), (P1, hex(-3, 2))], &[E1]);
    let before = snapshot(&s);
    assert_eq!(s.apply(Command::Move { unit: E1, to: hex(3, -1) }), Err(RuleError::FleeingUnit));
    assert_eq!(s.apply(Command::EndTurn { unit: E1 }), Err(RuleError::FleeingUnit));
    assert_eq!(snapshot(&s), before);
}

#[test]
fn flee_is_rejected_for_non_fleeing_actors_and_keeps_precedence_of_older_errors() {
    let mut s = board(7, &[(P1, hex(-3, 2)), (E1, hex(3, -2)), (P2, hex(-4, 3))], &[]);
    let before = snapshot(&s);
    assert_eq!(s.apply(Command::Flee { unit: P1 }), Err(RuleError::NotFleeing));
    assert_eq!(snapshot(&s), before);
    // Not the active unit wins over NotFleeing, also for a fleeing non-active unit.
    assert_eq!(s.apply(Command::Flee { unit: E1 }), Err(RuleError::NotActiveUnit));
    s.units[P2 as usize].morale = Morale::Fleeing;
    assert_eq!(s.apply(Command::Flee { unit: P2 }), Err(RuleError::NotActiveUnit));
    assert_eq!(s.apply(Command::Flee { unit: 99 }), Err(RuleError::NotActiveUnit));
    // Battle over wins over everything.
    s.outcome = Some(Outcome::Retreat);
    s.units[P1 as usize].morale = Morale::Fleeing;
    for cmd in [
        Command::Flee { unit: P1 },
        Command::Flee { unit: E1 },
        Command::Move { unit: P1, to: hex(-3, 3) },
        Command::EndRound { unit: P1 },
    ] {
        assert_eq!(s.apply(cmd), Err(RuleError::BattleOver), "{cmd:?}");
    }
    assert_eq!(s.preview_move(P1, hex(-3, 3)), Err(RuleError::BattleOver));
    assert_eq!(s.preview_move(E1, hex(3, -1)), Err(RuleError::BattleOver));
}

#[test]
fn non_fleeing_move_and_preview_are_unchanged() {
    let mut s = board(7, &[(P1, hex(-3, 2)), (E1, hex(3, -2))], &[]);
    let plan = s.preview_move(P1, hex(-3, 3)).unwrap();
    assert_eq!((plan.path.clone(), plan.ap_cost, plan.fatigue_cost), (vec![hex(-3, 3)], 2, 2));
    let ev = s.apply(Command::Move { unit: P1, to: hex(-3, 3) }).unwrap();
    assert_eq!(ev, vec![Event::Moved { unit: P1, path: vec![hex(-3, 3)] }]);
}

// --- Routing -------------------------------------------------------------------

fn flee_once(s: &mut BattleState, unit: u32) -> Vec<Event> {
    s.apply(Command::Flee { unit }).unwrap()
}

/// P1 at (-3,3) has three adjacent edge hexes: (-4,3), (-4,4), (-3,4).
fn corner(enemy: Hex) -> BattleState {
    board(3, &[(P1, hex(-3, 3)), (E1, enemy), (P2, hex(3, -1))], &[P1])
}

#[test]
fn equal_distance_exits_prefer_the_endpoint_farthest_from_the_nearest_enemy() {
    // Enemy at (-4,0): distances 3, 4, 5 to (-4,3), (-4,4), (-3,4).
    let mut s = corner(hex(-4, 0));
    let ev = flee_once(&mut s, P1);
    assert_eq!(s.units[P1 as usize].pos, hex(-3, 4));
    assert_eq!(ev[0], Event::Moved { unit: P1, path: vec![hex(-3, 4)] });
    // Enemy at (0,4): distances 5, 4, 3 to (-4,3), (-4,4), (-3,4).
    let mut s = corner(hex(0, 4));
    flee_once(&mut s, P1);
    assert_eq!(s.units[P1 as usize].pos, hex(-4, 3));
}

#[test]
fn remaining_ties_use_axial_coordinate_order_and_the_nearest_enemy_counts() {
    // Enemy far away: all three exits are 7 away, the lowest (q, r) wins.
    let mut s = corner(hex(3, -3));
    flee_once(&mut s, P1);
    assert_eq!(s.units[P1 as usize].pos, hex(-4, 3));

    // The distance is to the nearest *present* enemy: a second, closer enemy decides.
    let mut s = board(3, &[(P1, hex(-3, 3)), (E1, hex(3, -3)), (E2, hex(-4, 0)), (P2, hex(3, -1))], &[P1]);
    flee_once(&mut s, P1);
    assert_eq!(s.units[P1 as usize].pos, hex(-3, 4));
    // ... and an absent (escaped or dead) enemy is ignored.
    let mut s = board(3, &[(P1, hex(-3, 3)), (E1, hex(3, -3)), (E2, hex(-4, 0)), (P2, hex(3, -1))], &[P1]);
    s.units[E2 as usize].fled = true;
    flee_once(&mut s, P1);
    assert_eq!(s.units[P1 as usize].pos, hex(-4, 3));
}

#[test]
fn flee_takes_one_step_per_command_along_a_shortest_route_and_escapes_on_arrival() {
    // (-2,2) is two steps from the edge; the lowest-coordinate exit is (-4,2) via (-3,2).
    let mut s = board(3, &[(P1, hex(-2, 2)), (E1, hex(3, -3)), (P2, hex(3, -1))], &[P1]);
    let rng = s.rng.clone();
    let ev = flee_once(&mut s, P1);
    assert_eq!(ev, vec![Event::Moved { unit: P1, path: vec![hex(-3, 2)] }]);
    let u = &s.units[P1 as usize];
    assert_eq!((u.pos, u.ap, u.fatigue, u.fled), (hex(-3, 2), 7, 2, false));
    assert_eq!(s.queue[0], P1, "the turn continues");
    let ev = flee_once(&mut s, P1);
    assert_eq!(
        ev,
        vec![Event::Moved { unit: P1, path: vec![hex(-4, 2)] }, Event::Fled { unit: P1 }, started(E1)]
    );
    assert_eq!(s.rng, rng, "route choice and unreacted steps consume no RNG");
    let u = &s.units[P1 as usize];
    assert!(u.fled && u.alive && !u.is_present());
    assert_eq!((u.pos, u.ap, u.fatigue), (hex(-4, 2), 5, 4));
    assert_eq!(s.queue, vec![E1, P2]);
}

#[test]
fn route_is_a_shortest_edge_route_from_every_start_hex() {
    // Independent oracle: plain BFS distance to any free edge hex.
    fn edge(h: Hex) -> bool {
        h.q.abs().max(h.r.abs()).max(h.s().abs()) == 4
    }
    fn dist_to_edge(from: Hex, blocked: &[Hex]) -> Option<u32> {
        let mut seen = vec![from];
        let mut frontier = vec![from];
        let mut d = 0;
        while !frontier.is_empty() {
            if frontier.iter().any(|&h| edge(h)) {
                return Some(d);
            }
            let mut next = Vec::new();
            for h in frontier {
                for n in h.neighbors() {
                    if n.in_map() && !n.is_obstacle() && !blocked.contains(&n) && !seen.contains(&n) {
                        seen.push(n);
                        next.push(n);
                    }
                }
            }
            frontier = next;
            d += 1;
        }
        None
    }
    // The enemy is Fleeing too, so it exerts no ZoC and no reaction can interfere.
    let (e1, p2) = (hex(3, -3), hex(-1, -3));
    let mut checked = 0;
    for start in map_hexes() {
        if start.is_obstacle() || start == e1 || start == p2 {
            continue;
        }
        let mut s = board(5, &[(P1, start), (E1, e1), (P2, p2)], &[P1, E1]);
        let blocked = [e1, p2];
        let d = dist_to_edge(start, &blocked).expect("every start has a route in this fixture");
        let ev = flee_once(&mut s, P1);
        let u = &s.units[P1 as usize];
        if d == 0 {
            assert!(u.fled && u.pos == start && u.ap == 9 && u.fatigue == 0, "{start:?}: escape from an edge is free");
            assert_eq!(ev[0], Event::Fled { unit: P1 });
        } else {
            assert_eq!(ev[0], Event::Moved { unit: P1, path: vec![u.pos] }, "{start:?}");
            assert_eq!(u.pos.distance(start), 1);
            assert_eq!(dist_to_edge(u.pos, &blocked), Some(d - 1), "{start:?} -> {:?} is not on a shortest route", u.pos);
            assert_eq!(u.fled, edge(u.pos), "{start:?}: escape exactly when an edge was reached");
        }
        checked += 1;
    }
    assert!(checked > 50);
}

#[test]
fn obstacles_and_occupied_exits_shape_the_route() {
    // (-1,2) sits next to the obstacle (-1,1); the route must go around it.
    let mut s = board(3, &[(P1, hex(-1, 0)), (E1, hex(3, -3)), (P2, hex(3, -1))], &[P1]);
    flee_once(&mut s, P1);
    let p = s.units[P1 as usize].pos;
    assert!(!p.is_obstacle() && p.distance(hex(-1, 0)) == 1);

    // Occupied exit hexes are skipped: with (-4,3) taken the next-best exit is (-4,4).
    let mut s = board(3, &[(P1, hex(-3, 3)), (P2, hex(-4, 3)), (E1, hex(3, -3))], &[P1]);
    let ev = flee_once(&mut s, P1);
    assert_eq!(ev[0], Event::Moved { unit: P1, path: vec![hex(-4, 4)] });
    assert!(s.units[P1 as usize].fled);
    // Enemy-occupied exits are skipped as well; a fled unit no longer blocks.
    let mut s = board(3, &[(P1, hex(-3, 3)), (E1, hex(-4, 3)), (E2, hex(-4, 4)), (P2, hex(2, -3))], &[P1, E1, E2]);
    flee_once(&mut s, P1);
    assert_eq!(s.units[P1 as usize].pos, hex(-3, 4));
}

#[test]
fn no_route_or_unaffordable_step_ends_the_turn_through_normal_advancement() {
    // Fully enclosed: obstacles on two sides, units on the other four.
    let ring = [hex(0, -1), hex(-1, -1), hex(-2, 0), hex(-2, 1)];
    let mut s = board(
        3,
        &[(P1, hex(-1, 0)), (P2, ring[0]), (P3, ring[1]), (E1, ring[2]), (E2, ring[3])],
        &[P1, E1, E2],
    );
    let rng = s.rng.clone();
    let ev = flee_once(&mut s, P1);
    assert_eq!(ev, vec![started(P2)]);
    assert_eq!(s.queue, vec![P2, P3, E1, E2]);
    assert_eq!((s.units[P1 as usize].pos, s.units[P1 as usize].ap), (hex(-1, 0), 9));
    assert!(!s.units[P1 as usize].fled);
    assert_eq!(s.rng, rng);

    // Too little AP (1) or no fatigue headroom: the turn ends without moving.
    for (ap, fatigue) in [(1, 0), (0, 0), (9, 100), (9, 99)] {
        let mut s = board(3, &[(P1, hex(-2, 2)), (E1, hex(3, -3)), (P2, hex(3, -1))], &[P1]);
        s.units[P1 as usize].ap = ap;
        s.units[P1 as usize].fatigue = fatigue;
        let rng = s.rng.clone();
        let ev = flee_once(&mut s, P1);
        assert_eq!(ev, vec![started(E1)], "ap {ap} fatigue {fatigue}");
        assert_eq!((s.units[P1 as usize].pos, s.units[P1 as usize].ap), (hex(-2, 2), ap));
        assert_eq!(s.queue, vec![E1, P2]);
        assert_eq!(s.rng, rng);
    }
    // Exactly enough for one step still moves.
    let mut s = board(3, &[(P1, hex(-2, 2)), (E1, hex(3, -3)), (P2, hex(3, -1))], &[P1]);
    s.units[P1 as usize].ap = 2;
    s.units[P1 as usize].fatigue = 98;
    let ev = flee_once(&mut s, P1);
    assert_eq!(ev, vec![Event::Moved { unit: P1, path: vec![hex(-3, 2)] }]);
}

#[test]
fn a_unit_on_an_edge_escapes_without_cost_even_with_no_ap() {
    let mut s = board(3, &[(P1, hex(-4, 3)), (E1, hex(-3, 3)), (P2, hex(3, -1))], &[P1]);
    s.units[P1 as usize].ap = 0;
    s.units[P1 as usize].fatigue = 100;
    let rng = s.rng.clone();
    let hp = (s.units[P1 as usize].hp, s.units[P1 as usize].armour);
    // The adjacent enemy would have reacted to a step, but no step is taken.
    let ev = flee_once(&mut s, P1);
    assert_eq!(ev, vec![Event::Fled { unit: P1 }, started(E1)]);
    let u = &s.units[P1 as usize];
    assert_eq!((u.ap, u.fatigue, u.pos, (u.hp, u.armour)), (0, 100, hex(-4, 3), hp));
    assert!(u.alive && u.fled);
    assert_eq!(s.rng, rng);
}

// --- Reactions during flight -----------------------------------------------------

/// P1 (Fleeing, armour 0, large HP) leaves (-3,3) next to E1 at (-2,3); the first
/// exit is (-4,3) (E1 is 2 away from both (-4,3) and (-4,4), 1 from (-3,4)).
fn flight(seed: u64, hp: u32) -> BattleState {
    let mut s = board(seed, &[(P1, hex(-3, 3)), (E1, hex(-2, 3)), (P2, hex(2, -3))], &[P1]);
    let u = &mut s.units[P1 as usize];
    u.armour = 0;
    u.stats.armour = 0;
    u.stats.hp = hp.max(200);
    u.hp = hp;
    s.units[E1 as usize].stats.dmg_min = 10;
    s.units[E1 as usize].stats.dmg_max = 10;
    s
}

#[test]
fn a_missed_reaction_lets_the_fleeing_unit_reach_the_edge_and_escape() {
    let probe = flight(0, 100);
    let chance = probe.hit_chance(E1, P1);
    assert_eq!(chance, 43, "50 skill - 7 defense (Fleeing 70%)");
    let seed = seed_with_first_roll(chance, false);
    let mut s = flight(seed, 100);
    let ev = flee_once(&mut s, P1);
    assert!(matches!(
        ev[..],
        [
            Event::Attacked { attacker: 3, target: 0, hit: false, .. },
            Event::Moved { unit: 0, .. },
            Event::Fled { unit: 0 },
            Event::TurnStarted { unit: 3 }
        ]
    ), "{ev:?}");
    assert!(s.units[P1 as usize].fled);
    assert_eq!(s.units[P1 as usize].pos, hex(-4, 3));
}

#[test]
fn a_stopping_hit_ends_the_step_without_escape_or_double_advance() {
    let seed = seed_with_first_roll(43, true);
    let mut s = flight(seed, 100);
    let ev = flee_once(&mut s, P1);
    assert!(matches!(ev[..], [Event::Attacked { attacker: 3, target: 0, hit: true, hp_loss: 10, .. }]), "{ev:?}");
    let u = &s.units[P1 as usize];
    assert_eq!((u.pos, u.fled, u.hp), (hex(-3, 3), false, 90));
    assert_eq!((u.ap, u.fatigue), (7, 2 + 5), "the attempted step is paid, plus incoming hit fatigue");
    assert_eq!(s.queue[0], P1, "no advance: the stopped unit keeps its turn");
    assert_eq!(s.round, 1);
    // The next automatic command makes another attempt (and spends more budget).
    let ev = flee_once(&mut s, P1);
    assert!(matches!(ev.first(), Some(Event::Attacked { attacker: 3, target: 0, .. })), "{ev:?}");
    assert_eq!(s.units[P1 as usize].ap, 5);
}

#[test]
fn stopped_every_time_the_turn_still_ends_by_budget() {
    // Hits are forced by an always-hitting reactor; the unit never leaves.
    let mut s = flight(1, 190);
    s.units[E1 as usize].stats.melee_skill = 1000;
    let mut commands = 0;
    while s.queue[0] == P1 {
        commands += 1;
        assert!(commands <= 5, "every Flee command must spend budget or end the turn");
        flee_once(&mut s, P1);
    }
    assert_eq!(commands, 5, "four attempted steps (9 AP / 2) and then the unaffordable one ends the turn");
    assert!(!s.units[P1 as usize].fled && s.units[P1 as usize].alive);
}

#[test]
fn death_during_flight_is_a_normal_death_not_an_escape() {
    let seed = seed_with_first_roll(43, true);
    let mut s = flight(seed, 5);
    let ev = flee_once(&mut s, P1);
    assert!(matches!(
        ev[..],
        [
            Event::Attacked { hit: true, .. },
            Event::Died { unit: 0 },
            Event::AllyDeathMoraleChecked { unit: 1, fallen: 0, .. },
            Event::KillMoraleChecked { unit: 3, fallen: 0, .. },
            Event::TurnStarted { unit: 3 },
        ]
    ), "{ev:?}");
    let u = &s.units[P1 as usize];
    assert!(!u.alive && !u.fled);
    assert_eq!(u.pos, hex(-3, 3));
    assert_eq!(s.queue, vec![E1, P2]);
    assert_eq!(s.outcome(), None);

    // Dying as the last player ends the battle as an ordinary Defeat, no turn start after.
    let mut s = board(seed, &[(P1, hex(-3, 3)), (E1, hex(-2, 3))], &[P1]);
    s.units[P1 as usize].armour = 0;
    s.units[P1 as usize].hp = 5;
    s.units[E1 as usize].stats.dmg_min = 10;
    s.units[E1 as usize].stats.dmg_max = 10;
    let ev = flee_once(&mut s, P1);
    assert_eq!(
        ev.last(),
        Some(&Event::BattleEnded { outcome: Outcome::Defeat, reason: EndReason::Elimination })
    );
    assert!(!ev.iter().any(|e| matches!(e, Event::TurnStarted { .. } | Event::Fled { .. })), "{ev:?}");
    let n = ev.len();
    assert!(matches!(ev[n - 2], Event::KillMoraleChecked { unit: 3, fallen: 0, .. }), "kill check precedes BattleEnded: {ev:?}");
}

// --- Presence ---------------------------------------------------------------------

#[test]
fn an_escapee_survives_but_leaves_occupancy_queue_targets_and_surround() {
    let mut s = board(3, &[(P1, hex(-4, 3)), (P2, hex(-3, 2)), (E1, hex(-3, 3)), (E2, hex(3, -3))], &[P1]);
    s.units[P1 as usize].hp = 33;
    s.units[P1 as usize].armour = 17;
    s.units[P1 as usize].morale = Morale::Fleeing;
    let (hp, armour, stats) = (33, 17, s.units[P1 as usize].stats.clone());
    s.units[E1 as usize].stats.dmg_min = 1;
    let attacker_chance_with = s.hit_chance(E1, P2);
    // E1 is adjacent to P2 (-3,2) and P1 (-4,3); P1's presence adds a surround ally for ... E1's enemies.
    let before_surround = s.hit_chance(P2, E1);
    flee_once(&mut s, P1);
    let u = &s.units[P1 as usize];
    assert!(u.alive && u.fled && !u.is_present());
    assert_eq!((u.hp, u.armour), (hp, armour));
    assert_eq!(u.stats, stats);
    assert!(!s.queue.contains(&P1));
    // Occupancy: the vacated edge hex is free for others and reachable.
    s.units[P2 as usize].ap = 9;
    assert!(s.reachable(P2).iter().all(|&(h, _)| h != hex(-3, 3)));
    assert!(!s.queue.is_empty());
    // Targets: E1 no longer sees P1 adjacent, and Attack on it is invalid.
    assert_eq!(s.legal_targets(E1), vec![P2]);
    s.queue = vec![E1];
    s.units[E1 as usize].ap = 9;
    assert_eq!(s.apply(Command::Attack { attacker: E1, target: P1 }), Err(RuleError::InvalidTarget));
    // Surround: a fled ally adjacent to E1 no longer adds +5 to P2's hit chance.
    assert_eq!(s.hit_chance(P2, E1), before_surround - 5);
    let _ = attacker_chance_with;
    // Move onto the escapee's hex works (it is not occupied any more).
    s.units[P2 as usize].pos = hex(-3, 4);
    s.queue = vec![P2];
    assert!(s.preview_move(P2, hex(-4, 4)).is_ok());
    assert!(s.preview_move(P2, hex(-4, 3)).is_ok(), "an escapee does not block its former hex");
}

#[test]
fn a_dead_unit_and_an_escapee_are_distinct_states() {
    let mut s = board(3, &[(P1, hex(-4, 3)), (P2, hex(-3, 2)), (E1, hex(3, -3))], &[P1]);
    flee_once(&mut s, P1);
    s.units[P2 as usize].alive = false;
    assert!(s.units[P1 as usize].alive && s.units[P1 as usize].fled);
    assert!(!s.units[P2 as usize].alive && !s.units[P2 as usize].fled);
}

// --- Outcomes -----------------------------------------------------------------------

#[test]
fn the_last_enemy_escaping_is_a_victory_with_the_escape_reason() {
    let mut s = board(3, &[(E1, hex(4, -3)), (P1, hex(-3, 3))], &[E1]);
    let rng = s.rng.clone();
    let ev = flee_once(&mut s, E1);
    assert_eq!(
        ev,
        vec![Event::Fled { unit: E1 }, Event::BattleEnded { outcome: Outcome::Victory, reason: EndReason::Escape }]
    );
    assert_eq!((s.outcome(), s.end_reason()), (Some(Outcome::Victory), Some(EndReason::Escape)));
    assert_eq!(s.queue, vec![P1], "only the unit that never acted remains queued");
    assert!(s.active_unit().is_none(), "no turn starts after termination");
    assert_eq!(s.rng, rng);
    let after = snapshot(&s);
    assert_eq!(s.apply(Command::Flee { unit: E1 }), Err(RuleError::BattleOver));
    assert_eq!(s.apply(Command::EndTurn { unit: P1 }), Err(RuleError::BattleOver));
    assert_eq!(snapshot(&s), after);
}

#[test]
fn the_last_player_escaping_is_a_retreat_and_never_a_victory() {
    let mut s = board(3, &[(P1, hex(-4, 3)), (E1, hex(3, -3))], &[P1]);
    let ev = flee_once(&mut s, P1);
    assert_eq!(
        ev,
        vec![Event::Fled { unit: P1 }, Event::BattleEnded { outcome: Outcome::Retreat, reason: EndReason::Escape }]
    );
    assert_eq!((s.outcome(), s.end_reason()), (Some(Outcome::Retreat), Some(EndReason::Escape)));

    // Mixed dead and escaped players: still a retreat.
    let mut s = board(3, &[(P2, hex(-4, 3)), (E1, hex(3, -3))], &[P2]);
    s.units[P1 as usize].alive = false;
    s.units[P3 as usize].alive = false;
    let ev = flee_once(&mut s, P2);
    assert_eq!(ev.last(), Some(&Event::BattleEnded { outcome: Outcome::Retreat, reason: EndReason::Escape }));

    // Even if the enemies already left: players are judged first.
    let mut s = board(3, &[(P1, hex(-4, 3)), (E1, hex(3, -3))], &[P1]);
    s.units[E1 as usize].fled = true;
    let ev = flee_once(&mut s, P1);
    assert_eq!(ev.last(), Some(&Event::BattleEnded { outcome: Outcome::Retreat, reason: EndReason::Escape }));
}

#[test]
fn all_players_dead_stays_a_defeat_by_elimination_and_the_battle_continues_otherwise() {
    let mut s = board(3, &[(P1, hex(-3, 3)), (E1, hex(3, -3))], &[]);
    s.units[P1 as usize].hp = 1;
    s.units[P1 as usize].armour = 0;
    s.units[E1 as usize].stats.melee_skill = 1000;
    s.queue = vec![E1, P1];
    s.units[E1 as usize].pos = hex(-2, 3);
    s.units[E1 as usize].ap = 9;
    let ev = s.apply(Command::Attack { attacker: E1, target: P1 }).unwrap();
    assert_eq!(
        ev.last(),
        Some(&Event::BattleEnded { outcome: Outcome::Defeat, reason: EndReason::Elimination })
    );

    // One escapee with units still present: no terminal event, the next turn starts.
    let mut s = board(3, &[(P1, hex(-4, 3)), (P2, hex(-3, 2)), (E1, hex(3, -3))], &[P1]);
    let ev = flee_once(&mut s, P1);
    assert_eq!(ev, vec![Event::Fled { unit: P1 }, started(P2)]);
    assert_eq!(s.outcome(), None);
    assert_eq!(s.active_unit().unwrap().id, P2);
}

#[test]
fn escaping_as_the_last_queued_unit_starts_the_next_round_and_keeps_the_time_limit() {
    let mut s = board(3, &[(P1, hex(-4, 3)), (P2, hex(-3, 2)), (E1, hex(3, -3))], &[P1]);
    s.queue = vec![P1];
    let ev = flee_once(&mut s, P1);
    assert_eq!(ev[0], Event::Fled { unit: P1 });
    assert_eq!(ev[1], Event::RoundStarted { round: 2 });
    assert!(!s.queue.contains(&P1), "escapees are not queued in the new round");
    assert_eq!(s.queue, vec![P2, E1]);

    let mut s = board(3, &[(P1, hex(-4, 3)), (P2, hex(-3, 2)), (E1, hex(3, -3))], &[P1]);
    s.queue = vec![P1];
    s.round = rules_last_round();
    let ev = flee_once(&mut s, P1);
    assert_eq!(
        ev,
        vec![
            Event::Fled { unit: P1 },
            Event::BattleEnded { outcome: Outcome::Defeat, reason: EndReason::TimeLimit }
        ]
    );
}

fn rules_last_round() -> u32 {
    bb_rules::rules::LAST_ROUND
}

// --- EndRound, AI and full battles ------------------------------------------------------

#[test]
fn end_round_neither_marks_fleeing_players_nor_suppresses_their_turns() {
    let mut s = BattleState::new(1);
    s.units[P3 as usize].morale = Morale::Fleeing;
    s.units[P3 as usize].stats.resolve = 0; // the turn-start self-rally check always fails
    let ev = s.apply(Command::EndRound { unit: P1 }).unwrap();
    let failed = Event::SelfRallyChecked {
        unit: P3,
        roll: first_draw(1) as u32,
        chance: 0,
        before: Morale::Fleeing,
        after: Morale::Fleeing,
    };
    assert_eq!(ev, vec![started(P2), started(P3), failed]);
    assert!(s.units[P2 as usize].skip_turn);
    assert!(!s.units[P3 as usize].skip_turn);
    assert_eq!(s.active_unit().unwrap().id, P3);
    assert_eq!(ai::plan_turn(&s), vec![Command::Flee { unit: P3 }]);
    assert_eq!(s.queue, vec![P3, E1, E2, E3]);

    // A leftover skip mark on a fleeing unit cannot suppress its turn either.
    let mut s = BattleState::new(1);
    s.units[P2 as usize].morale = Morale::Fleeing;
    s.units[P2 as usize].skip_turn = true;
    s.apply(Command::EndTurn { unit: P1 }).unwrap();
    assert_eq!(s.active_unit().unwrap().id, P2);
}

#[test]
fn plan_turn_returns_exactly_one_flee_for_either_side_and_leaves_normal_policy_alone() {
    // Adjacent to an enemy with full AP: a normal unit attacks, a fleeing one flees.
    let normal = board(3, &[(P1, hex(-3, 2)), (E1, hex(-2, 2))], &[]);
    assert_eq!(ai::plan_turn(&normal), vec![Command::Attack { attacker: P1, target: E1 }]);
    for (active, other) in [(P1, E1), (E1, P1)] {
        let (a_pos, o_pos) = if active == P1 { (hex(-3, 2), hex(-2, 2)) } else { (hex(-2, 2), hex(-3, 2)) };
        let s = board(3, &[(active, a_pos), (other, o_pos)], &[active]);
        assert_eq!(ai::plan_turn(&s), vec![Command::Flee { unit: active }]);
        // Also with no AP at all.
        let mut s = s;
        s.units[active as usize].ap = 0;
        assert_eq!(ai::plan_turn(&s), vec![Command::Flee { unit: active }]);
    }
    // Non-active fleeing units do not change the active unit's plan.
    let mut s = board(3, &[(P1, hex(-3, 2)), (E1, hex(-2, 2))], &[]);
    s.units[E1 as usize].morale = Morale::Fleeing;
    assert_eq!(ai::plan_turn(&s), vec![Command::Attack { attacker: P1, target: E1 }]);
}

#[test]
fn ai_ignores_escapees_when_choosing_targets_and_blocking_hexes() {
    let mut s = board(3, &[(P1, hex(-3, 2)), (E1, hex(3, -3)), (E2, hex(-2, 2))], &[]);
    s.units[E2 as usize].fled = true;
    // Only E1 is present: P1 approaches it, not the escapee next to it.
    assert!(matches!(ai::plan_turn(&s)[..], [Command::Move { unit: 0, .. }]));
    let Command::Move { to, .. } = ai::plan_turn(&s)[0] else { unreachable!() };
    assert!(to.distance(hex(3, -3)) < hex(-3, 2).distance(hex(3, -3)));
}

#[test]
fn all_fleeing_battles_are_deterministic_terminate_and_keep_escapees_alive() {
    let run = |seed: u64| {
        let mut s = BattleState::new(seed);
        // Unarmoured, sturdy, Resolve-0 units: each heavy hit lowers morale a step and
        // survives long enough to reach Fleeing.
        for u in &mut s.units {
            u.stats.resolve = 0;
            u.stats.armour = 0;
            u.armour = 0;
            u.stats.hp = 300;
            u.hp = 300;
        }
        let mut commands = 0;
        let mut log = Vec::new();
        while s.outcome().is_none() {
            let cmd = ai::plan_turn(&s)[0];
            commands += 1;
            assert!(commands < 10_000, "seed {seed}: runaway battle");
            let ev = s.apply(cmd).unwrap_or_else(|e| panic!("seed {seed}: {cmd:?} -> {e:?}"));
            for e in &ev {
                if let Event::Fled { unit } = e {
                    let u = &s.units[*unit as usize];
                    assert!(u.alive && u.fled && u.hp > 0);
                }
            }
            log.extend(ev);
            for id in &s.queue {
                assert!(s.units[*id as usize].is_present());
            }
            assert_valid_fixture(&s);
        }
        (s.outcome(), s.end_reason(), s.round, s.state_hash(), log)
    };
    let (mut escapes, mut retreats, mut escaped_wins) = (0, 0, 0);
    for seed in 1..=40u64 {
        let a = run(seed);
        assert_eq!(a, run(seed), "seed {seed} replays identically");
        if a.4.iter().any(|e| matches!(e, Event::Fled { .. })) {
            escapes += 1;
            assert_eq!(a.4.iter().filter(|e| matches!(e, Event::BattleEnded { .. })).count(), 1);
            assert!(matches!(a.4.last(), Some(Event::BattleEnded { .. })), "seed {seed}: nothing follows the end");
        }
        match a.0 {
            Some(Outcome::Retreat) => {
                retreats += 1;
                assert_eq!(a.1, Some(EndReason::Escape));
            }
            Some(Outcome::Victory) if a.1 == Some(EndReason::Escape) => escaped_wins += 1,
            _ => {}
        }
    }
    assert!(escapes > 0 && retreats > 0 && escaped_wins > 0, "{escapes} {retreats} {escaped_wins}");
}
