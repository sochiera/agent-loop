//! Uphill movement AP surcharge (APPROXIMATION of UC-COMBAT-005/010 climbing
//! cost): a step costs `MOVE_AP + max(to - from, 0)` AP and `MOVE_FATIGUE`
//! fatigue, summed over the BFS route (which minimizes steps, not AP). Fixtures
//! clear the stock scenario and place only the units they need, mostly on the
//! obstacle-free lane `r = -2`. The first SplitMix64 draw for seed 1 is 65
//! (mod 100): a reaction with chance 5 misses and one with chance 95 hits.

use bb_rules::ai::plan_turn;
use bb_rules::hex::{bfs_distances, hex, map_hexes, Hex};
use bb_rules::rules::{DEFENDER_HIT_FATIGUE_GAIN, DEFENDER_MISS_FATIGUE_GAIN, MOVE_AP, MOVE_FATIGUE};
use bb_rules::terrain::MAX_ELEVATION;
use bb_rules::*;

const P1: u32 = 0;
const P2: u32 = 1;
const E1: u32 = 3;

/// Only `placed` units are present; the queue follows the given order, so the
/// first entry is the active unit (9 AP, no fatigue). Skill and defense are 0
/// and every hit does exactly 1 damage, so reactions are harmless unless a test
/// raises them.
fn board(placed: &[(u32, Hex)]) -> BattleState {
    let mut s = BattleState::new(1);
    for u in &mut s.units {
        u.alive = false;
        u.stats.melee_skill = 0;
        u.stats.defense = 0;
        u.stats.dmg_min = 1;
        u.stats.dmg_max = 1;
    }
    for &(id, pos) in placed {
        assert!(pos.in_map() && !pos.is_obstacle(), "fixture hex {pos:?} must be walkable");
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

fn set_h(s: &mut BattleState, at: Hex, height: u8) {
    s.elevations.set(at, height).unwrap();
}

fn snapshot(s: &BattleState) -> (u64, Rng, Vec<u32>) {
    (s.state_hash(), s.rng.clone(), s.queue.clone())
}

fn rng_after(seed: u64, n: usize) -> Rng {
    let mut r = Rng::new(seed);
    for _ in 0..n {
        r.next_u64();
    }
    r
}

fn moved_path(ev: &[Event]) -> Vec<Hex> {
    ev.iter()
        .filter_map(|e| match e {
            Event::Moved { path, .. } => Some(path.clone()),
            _ => None,
        })
        .flatten()
        .collect()
}

// ------------------------------------------------------------ step costs

#[test]
fn every_height_pair_costs_base_plus_uphill_for_both_factions() {
    let (from, to) = (hex(0, -2), hex(1, -2));
    for (actor, other, other_pos) in [(P1, E1, hex(4, -4)), (E1, P1, hex(-4, 4))] {
        for h0 in 0..=MAX_ELEVATION {
            for h1 in 0..=MAX_ELEVATION {
                let mut s = board(&[(actor, from), (other, other_pos)]);
                set_h(&mut s, from, h0);
                set_h(&mut s, to, h1);
                let cost = MOVE_AP + u32::from(h1.saturating_sub(h0));
                let p = s.preview_move(actor, to).unwrap();
                assert_eq!((p.path.clone(), p.ap_cost, p.fatigue_cost), (vec![to], cost, MOVE_FATIGUE));
                assert!(s.reachable(actor).contains(&(to, cost)), "actor {actor} {h0}->{h1}");
                let ev = s.apply(Command::Move { unit: actor, to }).unwrap();
                assert_eq!(ev, vec![Event::Moved { unit: actor, path: vec![to] }]);
                let u = &s.units[actor as usize];
                assert_eq!((u.ap, u.fatigue, u.pos), (9 - cost, MOVE_FATIGUE, to), "actor {actor} {h0}->{h1}");
            }
        }
    }
}

#[test]
fn routes_sum_ascents_and_descents_and_the_budget_is_exact() {
    // Lane (-1,-2) .. (3,-2) at heights 0,2,1,3,0: costs 4, 2, 4, 2.
    let lane = [hex(-1, -2), hex(0, -2), hex(1, -2), hex(2, -2), hex(3, -2)];
    let heights = [0, 2, 1, 3, 0];
    let make = |ap: u32| {
        let mut s = board(&[(P1, lane[0]), (E1, hex(4, -4))]);
        for (h, &at) in heights.iter().zip(&lane) {
            set_h(&mut s, at, *h);
        }
        s.units[P1 as usize].ap = ap;
        s
    };
    let s = make(12);
    for (i, expected) in [4, 6, 10, 12].into_iter().enumerate() {
        let p = s.preview_move(P1, lane[i + 1]).unwrap();
        assert_eq!((p.path.len(), p.ap_cost, p.fatigue_cost), (i + 1, expected, MOVE_FATIGUE * (i as u32 + 1)));
    }
    // Exact budget succeeds and leaves nothing.
    let mut s = make(12);
    let ev = s.apply(Command::Move { unit: P1, to: lane[4] }).unwrap();
    assert_eq!(moved_path(&ev), lane[1..].to_vec());
    let u = &s.units[P1 as usize];
    assert_eq!((u.ap, u.fatigue), (0, 8));
    // One AP short is rejected up front, identically by preview, apply and reachable.
    let mut s = make(11);
    let before = snapshot(&s);
    assert_eq!(s.preview_move(P1, lane[4]), Err(RuleError::NotEnoughAp));
    assert_eq!(s.apply(Command::Move { unit: P1, to: lane[4] }), Err(RuleError::NotEnoughAp));
    assert!(!s.reachable(P1).iter().any(|&(h, _)| h == lane[4]));
    assert!(s.reachable(P1).contains(&(lane[3], 10)));
    assert_eq!(snapshot(&s), before);
    // Descending only keeps the base cost whatever the drop.
    let mut s = board(&[(P1, lane[0]), (E1, hex(4, -4))]);
    for (h, &at) in [3, 2, 0, 0, 0].iter().zip(&lane) {
        set_h(&mut s, at, *h);
    }
    assert_eq!(s.preview_move(P1, lane[3]).unwrap().ap_cost, 3 * MOVE_AP);
}

#[test]
fn bfs_route_is_kept_even_when_a_cheaper_detour_exists() {
    // Straight lane to (2,-2) costs 5 + 2 + 2 = 9 over a height-3 hill; the
    // four-step detour along r = -3 costs 8. Routing minimizes steps, not AP.
    let mut s = board(&[(P1, hex(-1, -2)), (E1, hex(4, -4))]);
    set_h(&mut s, hex(0, -2), 3);
    s.units[P1 as usize].ap = 8;
    assert_eq!(s.preview_move(P1, hex(2, -2)), Err(RuleError::NotEnoughAp));
    assert_eq!(s.apply(Command::Move { unit: P1, to: hex(2, -2) }), Err(RuleError::NotEnoughAp));
    assert!(!s.reachable(P1).iter().any(|&(h, _)| h == hex(2, -2)));
    s.units[P1 as usize].ap = 9;
    let p = s.preview_move(P1, hex(2, -2)).unwrap();
    assert_eq!((p.path, p.ap_cost), (vec![hex(0, -2), hex(1, -2), hex(2, -2)], 9));
}

// ----------------------------------------- preview / reachable / execution

fn hilly(s: &mut BattleState) {
    for h in map_hexes() {
        set_h(s, h, (h.q * 3 + h.r * 5).rem_euclid(4) as u8);
    }
}

#[test]
fn preview_reachable_and_uncontested_execution_agree() {
    let start = hex(-2, 2);
    for (ap, fatigue) in [(9, 0), (9, 90), (9, 97), (5, 0), (3, 0), (2, 100), (1, 0), (0, 0)] {
        let mut base = board(&[(P1, start), (E1, hex(4, -4))]);
        hilly(&mut base);
        base.units[P1 as usize].ap = ap;
        base.units[P1 as usize].fatigue = fatigue;
        let before = snapshot(&base);
        let listed = base.reachable(P1);
        // Stable BFS order: exactly the preview-valid destinations in BFS order.
        let occupied = [start, hex(4, -4)];
        let expected: Vec<(Hex, u32)> = bfs_distances(start, &occupied)
            .into_iter()
            .filter(|&(_, d)| d >= 1)
            .filter_map(|(h, _)| base.preview_move(P1, h).ok().map(|p| (h, p.ap_cost)))
            .collect();
        assert_eq!(listed, expected, "ap {ap} fatigue {fatigue}");
        for (h, _) in bfs_distances(start, &occupied).into_iter().filter(|&(_, d)| d >= 1) {
            let mut s = base.clone();
            match base.preview_move(P1, h) {
                Ok(p) => {
                    assert!(listed.contains(&(h, p.ap_cost)));
                    assert_eq!(p.first_threat, None);
                    let ev = s.apply(Command::Move { unit: P1, to: h }).unwrap();
                    assert_eq!(moved_path(&ev), p.path);
                    let u = &s.units[P1 as usize];
                    assert_eq!((u.ap, u.fatigue, u.pos), (ap - p.ap_cost, fatigue + p.fatigue_cost, h));
                    assert_eq!(s.rng, base.rng);
                }
                Err(e) => {
                    assert!(matches!(e, RuleError::NotEnoughAp | RuleError::TooFatigued));
                    assert!(!listed.iter().any(|&(x, _)| x == h));
                    assert_eq!(s.apply(Command::Move { unit: P1, to: h }), Err(e));
                    assert_eq!(snapshot(&s), before);
                }
            }
        }
        assert_eq!(snapshot(&base), before, "previews and reachable are pure");
    }
    // The hills really change the answer relative to a flat map.
    let flat = board(&[(P1, start), (E1, hex(4, -4))]);
    let mut hill = flat.clone();
    hilly(&mut hill);
    assert_ne!(flat.reachable(P1), hill.reachable(P1));
}

#[test]
fn flat_maps_keep_the_flat_cost_formula() {
    let s = BattleState::new(1);
    let u = s.active_unit().unwrap();
    let occupied: Vec<Hex> = s.units.iter().map(|u| u.pos).collect();
    let expected: Vec<(Hex, u32)> = bfs_distances(u.pos, &occupied)
        .into_iter()
        .filter(|&(_, d)| d >= 1 && d <= u.ap / MOVE_AP)
        .map(|(h, d)| (h, d * MOVE_AP))
        .collect();
    assert_eq!(s.reachable(u.id), expected);
    assert!(!expected.is_empty());
}

// ------------------------------------------------------------- fatigue

#[test]
fn fatigue_is_unchanged_by_height_and_checked_without_overflow() {
    let (a, b, c) = (hex(-1, -2), hex(0, -2), hex(1, -2));
    let make = |fatigue: u32, ap: u32| {
        let mut s = board(&[(P1, a), (E1, hex(4, -4))]);
        set_h(&mut s, b, 1);
        set_h(&mut s, c, 1);
        s.units[P1 as usize].fatigue = fatigue;
        s.units[P1 as usize].ap = ap;
        s
    };
    // Exact cap: 96 + 2 steps * 2 = 100 succeeds, 97 does not.
    let mut s = make(96, 9);
    s.apply(Command::Move { unit: P1, to: c }).unwrap();
    assert_eq!((s.units[P1 as usize].fatigue, s.units[P1 as usize].ap), (100, 4));
    // One step with the exact remaining fatigue also succeeds.
    let mut s = make(98, 9);
    assert!(s.reachable(P1).contains(&(b, 3)) && !s.reachable(P1).iter().any(|&(h, _)| h == c));
    s.apply(Command::Move { unit: P1, to: b }).unwrap();
    assert_eq!(s.units[P1 as usize].fatigue, 100);
    for fatigue in [97, 99, 100, u32::MAX - 3, u32::MAX - 1, u32::MAX] {
        let mut s = make(fatigue, 9);
        let before = snapshot(&s);
        assert_eq!(s.preview_move(P1, c), Err(RuleError::TooFatigued), "fatigue {fatigue}");
        assert_eq!(s.apply(Command::Move { unit: P1, to: c }), Err(RuleError::TooFatigued));
        if fatigue >= 99 {
            assert!(s.reachable(P1).is_empty());
        }
        assert_eq!(snapshot(&s), before);
    }
    // Precedence: Unreachable, then NotEnoughAp, then TooFatigued.
    let mut s = make(u32::MAX, 3);
    assert_eq!(s.preview_move(P1, c), Err(RuleError::NotEnoughAp));
    assert_eq!(s.apply(Command::Move { unit: P1, to: c }), Err(RuleError::NotEnoughAp));
    s.units[P1 as usize].ap = 0;
    for to in [hex(0, 0), a, hex(9, 9)] {
        assert_eq!(s.preview_move(P1, to), Err(RuleError::Unreachable));
        assert_eq!(s.apply(Command::Move { unit: P1, to }), Err(RuleError::Unreachable));
    }
}

// --------------------------------------------------- opportunity attacks

/// P1 at (-1,-2) next to E1 at (0,-3), walking south to (-1,0) via (-1,-1)
/// (heights 2 and 3: step costs 4 and 3). Only the start is in E1's reach.
fn reaction_board(e1_skill: u32) -> BattleState {
    let mut s = board(&[(P1, hex(-1, -2)), (E1, hex(0, -3)), (P2, hex(-4, 4))]);
    s.units[E1 as usize].stats.melee_skill = e1_skill;
    set_h(&mut s, hex(-1, -1), 2);
    set_h(&mut s, hex(-1, 0), 3);
    s
}

#[test]
fn a_missed_reaction_leaves_the_walk_paying_every_surcharge() {
    let mut s = reaction_board(0);
    let preview = s.preview_move(P1, hex(-1, 0)).unwrap();
    assert_eq!((preview.ap_cost, preview.fatigue_cost), (7, 4));
    assert_eq!(preview.first_threat.map(|t| t.departure), Some(hex(-1, -2)));
    let ev = s.apply(Command::Move { unit: P1, to: hex(-1, 0) }).unwrap();
    assert!(matches!(ev[0], Event::Attacked { attacker: E1, target: P1, hit: false, roll: 65, .. }));
    assert_eq!(ev[1], Event::Moved { unit: P1, path: vec![hex(-1, -1), hex(-1, 0)] });
    let u = &s.units[P1 as usize];
    assert_eq!((u.ap, u.fatigue, u.pos), (2, 4 + DEFENDER_MISS_FATIGUE_GAIN, hex(-1, 0)));
    assert_eq!(s.rng, rng_after(1, 1));
}

#[test]
fn a_stopping_reaction_still_spends_the_attempted_steps_surcharge() {
    let mut s = reaction_board(100);
    let ev = s.apply(Command::Move { unit: P1, to: hex(-1, 0) }).unwrap();
    assert!(matches!(ev[0], Event::Attacked { attacker: E1, target: P1, hit: true, roll: 65, .. }));
    assert!(moved_path(&ev).is_empty());
    let u = &s.units[P1 as usize];
    assert_eq!((u.ap, u.fatigue, u.pos), (9 - 4, MOVE_FATIGUE + DEFENDER_HIT_FATIGUE_GAIN, hex(-1, -2)));
    assert_eq!(s.queue[0], P1, "the turn goes on");
}

#[test]
fn a_lethal_reaction_keeps_the_charge_and_the_normal_death_handling() {
    let mut s = reaction_board(100);
    s.units[E1 as usize].stats.dmg_min = 10;
    s.units[E1 as usize].stats.dmg_max = 10;
    s.units[P1 as usize].hp = 1;
    s.units[P1 as usize].armour = 0;
    let ev = s.apply(Command::Move { unit: P1, to: hex(-1, 0) }).unwrap();
    assert!(ev.contains(&Event::Died { unit: P1 }));
    assert!(moved_path(&ev).is_empty());
    let u = &s.units[P1 as usize];
    assert_eq!((u.alive, u.ap, u.pos), (false, 9 - 4, hex(-1, -2)));
    assert!(!s.queue.contains(&P1));
    assert_eq!(s.outcome(), None);
    assert_ne!(s.queue.first(), Some(&P1));
}

#[test]
fn an_interrupted_walk_stops_without_charging_a_step_that_became_unaffordable() {
    // Fatigue 95: the two-step plan (99 total) is valid, but the missed reaction
    // adds 2 defender fatigue after step one, so step two (needs 101) is not paid.
    let mut s = reaction_board(0);
    s.units[P1 as usize].fatigue = 95;
    assert!(s.preview_move(P1, hex(-1, 0)).is_ok());
    let ev = s.apply(Command::Move { unit: P1, to: hex(-1, 0) }).unwrap();
    assert!(matches!(ev[0], Event::Attacked { hit: false, .. }));
    assert_eq!(ev[1], Event::Moved { unit: P1, path: vec![hex(-1, -1)] });
    assert_eq!(ev.len(), 2);
    let u = &s.units[P1 as usize];
    assert_eq!((u.ap, u.fatigue, u.pos), (9 - 4, 95 + 2 + DEFENDER_MISS_FATIGUE_GAIN, hex(-1, -1)));
}

// ------------------------------------------------------- Flee / Withdraw

#[derive(Clone, Copy, Debug)]
enum Auto {
    Flee,
    Withdraw,
}

impl Auto {
    fn cmd(self, unit: u32) -> Command {
        match self {
            Auto::Flee => Command::Flee { unit },
            Auto::Withdraw => Command::Withdraw { unit },
        }
    }
}

/// P1 acts as a Fleeing or retreating unit; E1 and P2 keep the battle going.
fn escape_board(kind: Auto, p1: Hex, e1: Hex, ap: u32) -> BattleState {
    let mut s = board(&[(P1, p1), (E1, e1), (P2, hex(3, -1))]);
    match kind {
        Auto::Flee => s.units[P1 as usize].morale = Morale::Fleeing,
        Auto::Withdraw => s.units[P1 as usize].retreating = true,
    }
    s.units[P1 as usize].ap = ap;
    s
}

#[test]
fn flee_and_withdraw_pay_the_full_uphill_step_and_escape_on_arrival() {
    for kind in [Auto::Flee, Auto::Withdraw] {
        // (-2,2) -> (-3,2) -> (-4,2): costs 4 then 3.
        let mut s = escape_board(kind, hex(-2, 2), hex(3, -3), 9);
        set_h(&mut s, hex(-3, 2), 2);
        set_h(&mut s, hex(-4, 2), 3);
        let rng = s.rng.clone();
        let ev = s.apply(kind.cmd(P1)).unwrap();
        assert_eq!(ev, vec![Event::Moved { unit: P1, path: vec![hex(-3, 2)] }], "{kind:?}");
        let u = &s.units[P1 as usize];
        assert_eq!((u.ap, u.fatigue, u.fled), (5, 2, false));
        let ev = s.apply(kind.cmd(P1)).unwrap();
        assert_eq!(ev[0], Event::Moved { unit: P1, path: vec![hex(-4, 2)] });
        assert_eq!(ev[1], Event::Fled { unit: P1 });
        let u = &s.units[P1 as usize];
        assert_eq!((u.ap, u.fatigue, u.fled), (2, 4, true), "{kind:?}");
        assert_eq!(s.rng, rng, "an uncontested escape draws nothing");
    }
}

#[test]
fn flee_and_withdraw_end_the_turn_without_moving_when_the_full_step_is_unaffordable() {
    for kind in [Auto::Flee, Auto::Withdraw] {
        // First step costs 4: AP 3 only covers the base cost.
        let mut s = escape_board(kind, hex(-2, 2), hex(3, -3), 3);
        set_h(&mut s, hex(-3, 2), 2);
        let before_rng = s.rng.clone();
        let ev = s.apply(kind.cmd(P1)).unwrap();
        assert!(moved_path(&ev).is_empty() && !ev.iter().any(|e| matches!(e, Event::Fled { .. })), "{kind:?}");
        let u = &s.units[P1 as usize];
        assert_eq!((u.pos, u.ap, u.fatigue, u.fled), (hex(-2, 2), 3, 0, false));
        assert!(!s.queue.contains(&P1));
        assert_eq!(s.rng, before_rng);

        // Exactly 4 AP moves; the second step (3 AP) then lacks budget after step one (AP 0).
        let mut s = escape_board(kind, hex(-2, 2), hex(3, -3), 4);
        set_h(&mut s, hex(-3, 2), 2);
        set_h(&mut s, hex(-4, 2), 3);
        s.apply(kind.cmd(P1)).unwrap();
        assert_eq!((s.units[P1 as usize].pos, s.units[P1 as usize].ap), (hex(-3, 2), 0));
        s.apply(kind.cmd(P1)).unwrap();
        assert_eq!(s.units[P1 as usize].pos, hex(-3, 2));
        assert!(!s.queue.contains(&P1));
        assert!(!s.units[P1 as usize].fled);

        // Second step one AP short (needs 3, has 2).
        let mut s = escape_board(kind, hex(-2, 2), hex(3, -3), 6);
        set_h(&mut s, hex(-3, 2), 2);
        set_h(&mut s, hex(-4, 2), 3);
        s.apply(kind.cmd(P1)).unwrap();
        assert_eq!(s.units[P1 as usize].ap, 2);
        s.apply(kind.cmd(P1)).unwrap();
        assert_eq!((s.units[P1 as usize].pos, s.units[P1 as usize].fled), (hex(-3, 2), false));
        assert!(!s.queue.contains(&P1));
    }
}

#[test]
fn immediate_arrival_escape_needs_the_full_uphill_cost_and_keeps_the_route_choice() {
    for kind in [Auto::Flee, Auto::Withdraw] {
        // Corner (-3,3): the lowest-coordinate exit (-4,3) is chosen even though it is a
        // height-3 climb (5 AP) and its neighbours (-4,4), (-3,4) are flat (2 AP).
        let mut s = escape_board(kind, hex(-3, 3), hex(3, -3), 5);
        set_h(&mut s, hex(-4, 3), 3);
        let ev = s.apply(kind.cmd(P1)).unwrap();
        assert_eq!(ev[..2], [Event::Moved { unit: P1, path: vec![hex(-4, 3)] }, Event::Fled { unit: P1 }]);
        assert_eq!((s.units[P1 as usize].ap, s.units[P1 as usize].fled), (0, true));

        let mut s = escape_board(kind, hex(-3, 3), hex(3, -3), 4);
        set_h(&mut s, hex(-4, 3), 3);
        let ev = s.apply(kind.cmd(P1)).unwrap();
        assert!(moved_path(&ev).is_empty() && !s.units[P1 as usize].fled, "{kind:?}");
        assert_eq!((s.units[P1 as usize].pos, s.units[P1 as usize].ap), (hex(-3, 3), 4));
    }
}

#[test]
fn standing_on_an_edge_still_escapes_for_free() {
    for kind in [Auto::Flee, Auto::Withdraw] {
        for (ap, fatigue) in [(0, 0), (0, 100), (1, u32::MAX)] {
            let mut s = escape_board(kind, hex(-4, 3), hex(3, -3), ap);
            set_h(&mut s, hex(-4, 3), 3);
            s.units[P1 as usize].fatigue = fatigue;
            let ev = s.apply(kind.cmd(P1)).unwrap();
            assert_eq!(ev[0], Event::Fled { unit: P1 }, "{kind:?}");
            assert!(moved_path(&ev).is_empty());
            let u = &s.units[P1 as usize];
            assert_eq!((u.ap, u.fatigue, u.fled), (ap, fatigue, true));
        }
    }
}

#[test]
fn a_stopped_flee_step_still_pays_the_surcharge() {
    // E1 at (-2,2) touches the corner; all exits tie on distance, (-4,3) wins by coordinate.
    let mut s = escape_board(Auto::Flee, hex(-3, 3), hex(-2, 2), 9);
    s.units[E1 as usize].stats.melee_skill = 100;
    set_h(&mut s, hex(-4, 3), 2);
    let ev = s.apply(Command::Flee { unit: P1 }).unwrap();
    assert!(matches!(ev[0], Event::Attacked { attacker: E1, target: P1, hit: true, .. }));
    assert!(moved_path(&ev).is_empty());
    let u = &s.units[P1 as usize];
    assert_eq!((u.pos, u.ap, u.fled), (hex(-3, 3), 9 - 4, false));
}

// ------------------------------------------------------------------- AI

/// The active unit stands at one end of the lane, its foe at the other; the BFS
/// destination is the foe's neighbour three steps along `path`.
fn ai_board(actor: u32) -> (BattleState, [Hex; 3]) {
    let (west, east) = (hex(-1, -2), hex(3, -2));
    if actor == P1 {
        (board(&[(P1, west), (E1, east)]), [hex(0, -2), hex(1, -2), hex(2, -2)])
    } else {
        (board(&[(E1, east), (P1, west)]), [hex(2, -2), hex(1, -2), hex(0, -2)])
    }
}

#[test]
fn ai_walks_the_longest_affordable_prefix_of_its_bfs_path_and_the_command_validates() {
    // (heights along the path, ap, fatigue, affordable steps)
    let cases: [([u8; 3], u32, u32, usize); 9] = [
        ([0, 0, 0], 9, 0, 3),
        ([1, 0, 2], 9, 0, 3),
        ([1, 0, 3], 9, 0, 2),
        ([2, 3, 3], 9, 0, 3),
        ([2, 3, 3], 8, 0, 2),
        ([3, 0, 0], 4, 0, 0),
        ([0, 0, 0], 9, 95, 2),
        ([0, 0, 0], 9, u32::MAX, 0),
        ([0, 0, 0], 9, u32::MAX - 1, 0),
    ];
    for actor in [P1, E1] {
        for (heights, ap, fatigue, steps) in cases {
            let (mut s, path) = ai_board(actor);
            for (h, &at) in heights.iter().zip(&path) {
                set_h(&mut s, at, *h);
            }
            s.units[actor as usize].ap = ap;
            s.units[actor as usize].fatigue = fatigue;
            let before = snapshot(&s);
            let cmd = plan_turn(&s);
            assert_eq!(snapshot(&s), before, "planning draws nothing");
            let start = s.units[actor as usize].pos;
            if steps == 0 {
                assert_eq!(cmd, vec![Command::EndTurn { unit: actor }], "actor {actor} {heights:?}");
                s.apply(cmd[0]).unwrap();
                continue;
            }
            assert_eq!(cmd, vec![Command::Move { unit: actor, to: path[steps - 1] }], "actor {actor} {heights:?}");
            let preview = s.preview_move(actor, path[steps - 1]).unwrap();
            assert_eq!(preview.path, path[..steps].to_vec());
            let mut cost = 0;
            let mut from = start;
            for &to in &path[..steps] {
                cost += MOVE_AP + u32::from(s.elevations.get(to).unwrap().saturating_sub(s.elevations.get(from).unwrap()));
                from = to;
            }
            assert_eq!(preview.ap_cost, cost);
            s.apply(cmd[0]).unwrap();
            let u = &s.units[actor as usize];
            assert_eq!((u.pos, u.ap, u.fatigue), (path[steps - 1], ap - cost, fatigue + MOVE_FATIGUE * steps as u32));
            assert_eq!(s.rng, rng_after(1, 0), "no zone of control on the lane");
        }
    }
}

#[test]
fn ai_does_not_approach_when_not_even_the_first_step_is_affordable() {
    // 2 AP would buy a flat step, but the first hex is a height-2 climb costing 4.
    let (mut s, path) = ai_board(P1);
    set_h(&mut s, path[0], 2);
    s.units[P1 as usize].ap = 3;
    assert_eq!(plan_turn(&s), vec![Command::EndTurn { unit: P1 }]);
    set_h(&mut s, path[0], 0);
    assert_eq!(plan_turn(&s), vec![Command::Move { unit: P1, to: path[0] }]);
}

// ------------------------------------------------------------- purity

#[test]
fn rejections_and_previews_stay_state_and_rng_pure() {
    let mut s = reaction_board(100);
    s.units[P1 as usize].ap = 6; // route costs 7
    let before = snapshot(&s);
    assert_eq!(s.apply(Command::Move { unit: P1, to: hex(-1, 0) }), Err(RuleError::NotEnoughAp));
    assert_eq!(s.preview_move(P1, hex(-1, 0)), Err(RuleError::NotEnoughAp));
    assert_eq!(s.preview_move(E1, hex(-1, 0)), Err(RuleError::NotActiveUnit));
    s.units[P1 as usize].morale = Morale::Fleeing;
    assert_eq!(s.preview_move(P1, hex(-1, 0)), Err(RuleError::FleeingUnit));
    s.units[P1 as usize].morale = Morale::Steady;
    s.units[P1 as usize].retreating = true;
    assert_eq!(s.preview_move(P1, hex(-1, 0)), Err(RuleError::RetreatingUnit));
    s.units[P1 as usize].retreating = false;
    s.outcome = Some(Outcome::Victory);
    assert_eq!(s.preview_move(P1, hex(-1, 0)), Err(RuleError::BattleOver));
    s.outcome = None;
    assert_eq!(snapshot(&s), before);
    assert!(s.preview_move(P1, hex(-1, -1)).is_ok());
    let _ = s.reachable(P1);
    assert_eq!(snapshot(&s), before);
}
