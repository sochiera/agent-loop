//! Headless Pathfinder movement-fatigue reduction (partial UC-COMBAT-057). The
//! catalog states a fatigue reduction (often 50%); the uniform 2-to-1 per-step cost
//! (`PATHFINDER_MOVE_FATIGUE` instead of `MOVE_FATIGUE`), both-side applicability,
//! the automatic Flee/Withdraw coverage and the capability flag are an
//! APPROXIMATION. Fixtures clear the stock scenario (`alive = false`) and place only
//! the units they need on distinct passable hexes, mostly on the obstacle-free lane
//! `r = -2`. Skill and defense are 0 and every hit does 1 damage unless a test says
//! otherwise; reaction seeds are found by mirroring the first SplitMix64 draw.
//! A capability-free "twin" (same state, `has_pathfinder = false`) is the oracle for
//! everything that must not change.

use bb_rules::ai::plan_turn;
use bb_rules::hex::{Hex, hex, map_hexes};
use bb_rules::rules::{
    self, ATTACK_AP, ATTACK_FATIGUE, DEFENDER_HIT_FATIGUE_GAIN, DEFENDER_MISS_FATIGUE_GAIN, FATIGUE_REGEN, FOOTWORK_AP,
    FOOTWORK_FATIGUE, MOVE_AP, MOVE_FATIGUE, PATHFINDER_MOVE_FATIGUE, ROTATION_AP, ROTATION_FATIGUE,
};
use bb_rules::scenario::{self, TerrainPreset};
use bb_rules::*;

const P1: u32 = 0;
const P2: u32 = 1;
const E1: u32 = 3;

const FMAX: u32 = 100;

// --------------------------------------------------------------------- fixtures

/// Only `placed` units are present; the queue follows the given order, so the
/// first entry is the active, started unit (9 AP, no fatigue).
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
    assert_valid_fixture(&s);
    s
}

/// Present units stand on distinct, in-map, passable hexes within their Stats maxima.
fn assert_valid_fixture(s: &BattleState) {
    let present: Vec<&Unit> = s.units.iter().filter(|u| u.is_present()).collect();
    for (i, u) in present.iter().enumerate() {
        assert!(u.pos.in_map() && !u.pos.is_obstacle(), "unit {} on invalid hex {:?}", u.id, u.pos);
        assert!(u.hp <= u.stats.hp && u.armour <= u.stats.armour && u.fatigue <= u.stats.fatigue_max);
        assert!(present[..i].iter().all(|o| o.pos != u.pos), "unit {} shares a hex", u.id);
    }
}

fn with_pathfinder(mut s: BattleState, id: u32) -> BattleState {
    s.units[id as usize].has_pathfinder = true;
    s
}

fn set_h(s: &mut BattleState, at: Hex, height: u8) {
    s.elevations.set(at, height).unwrap();
}

fn snapshot(s: &BattleState) -> (u64, Rng, Vec<u32>, Vec<Unit>) {
    (s.state_hash(), s.rng.clone(), s.queue.clone(), s.units.clone())
}

fn first_draw(seed: u64) -> u64 {
    Rng::new(seed).next_u64() % 100
}

/// First seed whose first roll is below (`hit`) or at/above `chance`.
fn seed_with_first_roll(chance: u32, hit: bool) -> u64 {
    (0..10_000u64).find(|&s| (first_draw(s) < u64::from(chance)) == hit).unwrap()
}

/// Rig the next draw of `s` so the `attacker` hits (or misses) `target` at the current chance.
fn rig(s: &mut BattleState, attacker: u32, target: u32, hit: bool) {
    s.rng = Rng::new(seed_with_first_roll(s.hit_chance(attacker, target), hit));
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

fn started(unit: u32) -> Event {
    Event::TurnStarted { unit }
}

/// The mover and its foe at opposite ends of the lane `r = -2`; the destination is
/// three steps along `path` and never adjacent to the foe.
fn lane(actor: u32) -> (BattleState, [Hex; 3]) {
    if actor == P1 {
        (board(&[(P1, hex(-2, -2)), (E1, hex(4, -2))]), [hex(-1, -2), hex(0, -2), hex(1, -2)])
    } else {
        (board(&[(E1, hex(4, -2)), (P1, hex(-2, -2))]), [hex(3, -2), hex(2, -2), hex(1, -2)])
    }
}

// ------------------------------------------------------------------ constants/default

#[test]
fn constants_and_default_flag() {
    assert_eq!((MOVE_FATIGUE, PATHFINDER_MOVE_FATIGUE), (2, 1));
    let s = BattleState::new(1);
    assert!(s.units.iter().all(|u| !u.has_pathfinder));
    for preset in [TerrainPreset::Flat, TerrainPreset::Hills] {
        assert!(scenario::battle(1, preset).units.iter().all(|u| !u.has_pathfinder));
    }
    let mut u = s.units[0].clone();
    assert_eq!(rules::move_fatigue(&u), MOVE_FATIGUE);
    u.has_pathfinder = true;
    assert_eq!(rules::move_fatigue(&u), PATHFINDER_MOVE_FATIGUE);
}

// ------------------------------------------------------------------ step costs

#[test]
fn route_costs_halve_fatigue_and_keep_ap_for_both_factions_and_every_height_shape() {
    let shapes: [[u8; 3]; 5] = [[0, 0, 0], [1, 0, 1], [2, 1, 0], [0, 3, 3], [1, 2, 3]];
    for actor in [P1, E1] {
        for heights in shapes {
            let (mut twin, path) = lane(actor);
            for (h, &at) in heights.iter().zip(&path) {
                set_h(&mut twin, at, *h);
            }
            let mut s = with_pathfinder(twin.clone(), actor);
            let dest = path[2];
            let ctx = format!("actor {actor} {heights:?}");

            let (pt, pp) = (twin.preview_move(actor, dest).unwrap(), s.preview_move(actor, dest).unwrap());
            assert_eq!(pp.path, path.to_vec(), "{ctx}");
            assert_eq!((pt.fatigue_cost, pp.fatigue_cost), (3 * MOVE_FATIGUE, 3 * PATHFINDER_MOVE_FATIGUE), "{ctx}");
            assert_eq!(
                (pp.path.clone(), pp.ap_cost, pp.ap_remaining, pp.first_threat.clone(), pp.first_edge_penalty),
                (pt.path.clone(), pt.ap_cost, pt.ap_remaining, pt.first_threat.clone(), pt.first_edge_penalty),
                "{ctx}"
            );
            let expected_ap = 3 * MOVE_AP + u32::from(heights[0]) + heights.windows(2).map(|w| u32::from(w[1].saturating_sub(w[0]))).sum::<u32>();
            assert_eq!(pp.ap_cost, expected_ap, "{ctx}");

            let (ev_s, ev_t) = (s.apply(Command::Move { unit: actor, to: dest }).unwrap(), {
                let mut t = twin.clone();
                let ev = t.apply(Command::Move { unit: actor, to: dest }).unwrap();
                assert_eq!(t.units[actor as usize].fatigue, 3 * MOVE_FATIGUE);
                assert_eq!(t.units[actor as usize].ap, 9 - expected_ap);
                ev
            });
            assert_eq!(ev_s, ev_t, "{ctx}: same events");
            let u = &s.units[actor as usize];
            assert_eq!((u.pos, u.ap, u.fatigue), (dest, 9 - expected_ap, 3 * PATHFINDER_MOVE_FATIGUE), "{ctx}");
            assert_eq!(s.rng, twin.rng, "{ctx}: no RNG draw");
        }
    }
}

#[test]
fn reachable_matches_preview_and_execution_at_the_fatigue_limit() {
    for actor in [P1, E1] {
        let (mut twin, _) = lane(actor);
        twin.units[actor as usize].fatigue = FMAX - 2;
        let s = with_pathfinder(twin.clone(), actor);
        let reach = s.reachable(actor);
        let twin_reach = twin.reachable(actor);
        assert!(!reach.is_empty() && twin_reach.len() < reach.len(), "the discount widens the reachable set");
        assert!(twin_reach.iter().all(|r| reach.contains(r)));
        let start = s.units[actor as usize].pos;
        for h in map_hexes() {
            if h == start || h.is_obstacle() || s.units.iter().any(|u| u.is_present() && u.pos == h) {
                continue;
            }
            let listed = reach.iter().find(|r| r.0 == h);
            match s.preview_move(actor, h) {
                Ok(p) => {
                    assert_eq!(listed, Some(&(h, p.ap_cost)), "{h:?}");
                    assert!(p.path.len() <= 2);
                    let mut run = s.clone();
                    run.apply(Command::Move { unit: actor, to: h }).unwrap();
                    assert_eq!(run.units[actor as usize].fatigue, FMAX - 2 + p.path.len() as u32);
                }
                Err(_) => assert_eq!(listed, None, "{h:?}"),
            }
        }
    }
}

#[test]
fn exact_limit_is_accepted_and_one_short_is_rejected_without_changes() {
    for actor in [P1, E1] {
        let (base, path) = lane(actor);
        let dest = path[2];
        let mut ok = with_pathfinder(base.clone(), actor);
        ok.units[actor as usize].fatigue = FMAX - 3;
        assert_eq!(ok.preview_move(actor, dest).unwrap().fatigue_cost, 3);
        assert!(ok.reachable(actor).iter().any(|r| r.0 == dest));
        ok.apply(Command::Move { unit: actor, to: dest }).unwrap();
        assert_eq!(ok.units[actor as usize].fatigue, FMAX);

        let mut short = with_pathfinder(base.clone(), actor);
        short.units[actor as usize].fatigue = FMAX - 2;
        assert!(!short.reachable(actor).iter().any(|r| r.0 == dest));
        let before = snapshot(&short);
        assert_eq!(short.preview_move(actor, dest), Err(RuleError::TooFatigued));
        assert_eq!(short.apply(Command::Move { unit: actor, to: dest }), Err(RuleError::TooFatigued));
        assert_eq!(snapshot(&short), before);

        // The capability-free twin needs six and is rejected at the discounted exact limit.
        let mut twin = base;
        twin.units[actor as usize].fatigue = FMAX - 3;
        assert_eq!(twin.preview_move(actor, dest), Err(RuleError::TooFatigued));
    }
}

#[test]
fn u32_max_boundaries_use_checked_discounted_sums() {
    for actor in [P1, E1] {
        let (base, path) = lane(actor);
        let mut s = with_pathfinder(base.clone(), actor);
        s.units[actor as usize].stats.fatigue_max = u32::MAX;
        s.units[actor as usize].fatigue = u32::MAX - 1;
        // One discounted step lands exactly on the cap; two overflow it.
        assert_eq!(s.preview_move(actor, path[1]), Err(RuleError::TooFatigued));
        assert_eq!(s.preview_move(actor, path[0]).unwrap().fatigue_cost, 1);
        assert!(!s.reachable(actor).is_empty());
        assert!(s.reachable(actor).iter().all(|r| r.0.distance(s.units[actor as usize].pos) == 1));
        let before = snapshot(&s);
        assert_eq!(s.apply(Command::Move { unit: actor, to: path[1] }), Err(RuleError::TooFatigued));
        assert_eq!(snapshot(&s), before);
        let mut run = s.clone();
        run.apply(Command::Move { unit: actor, to: path[0] }).unwrap();
        assert_eq!(run.units[actor as usize].fatigue, u32::MAX);
        assert_eq!(run.units[actor as usize].pos, path[0]);

        // Already at the cap: nothing is affordable.
        s.units[actor as usize].fatigue = u32::MAX;
        assert_eq!(s.preview_move(actor, path[0]), Err(RuleError::TooFatigued));
        assert!(s.reachable(actor).is_empty());
        assert_eq!(s.apply(Command::Move { unit: actor, to: path[0] }), Err(RuleError::TooFatigued));

        // The undiscounted twin cannot take even one step from MAX - 1.
        let mut twin = base;
        twin.units[actor as usize].stats.fatigue_max = u32::MAX;
        twin.units[actor as usize].fatigue = u32::MAX - 1;
        assert_eq!(twin.preview_move(actor, path[0]), Err(RuleError::TooFatigued));
        assert!(twin.reachable(actor).is_empty());
    }
}

#[test]
fn validation_precedence_is_unchanged_and_rejections_are_pure() {
    let (base, path) = lane(P1);
    let mut s = with_pathfinder(base, P1);
    s.units[P2 as usize].alive = true;
    s.units[P2 as usize].pos = hex(-4, 4);
    let tired = FMAX - 2; // 3 steps need 3
    let cases: [(u32, u32, Command, RuleError); 8] = [
        // Not the active unit: before everything else.
        (9, tired, Command::Move { unit: P2, to: path[2] }, RuleError::NotActiveUnit),
        // Unreachable (occupied, obstacle-free off-map) beats both budget errors.
        (0, FMAX, Command::Move { unit: P1, to: hex(4, -2) }, RuleError::Unreachable),
        (0, FMAX, Command::Move { unit: P1, to: hex(9, 9) }, RuleError::Unreachable),
        // AP is reported before fatigue.
        (0, FMAX, Command::Move { unit: P1, to: path[0] }, RuleError::NotEnoughAp),
        (5, tired, Command::Move { unit: P1, to: path[2] }, RuleError::NotEnoughAp),
        (9, tired, Command::Move { unit: P1, to: path[2] }, RuleError::TooFatigued),
        (9, FMAX, Command::Move { unit: P1, to: path[0] }, RuleError::TooFatigued),
        (1, 0, Command::Move { unit: P1, to: path[0] }, RuleError::NotEnoughAp),
    ];
    for (ap, fatigue, cmd, expected) in cases {
        let mut c = s.clone();
        c.units[P1 as usize].ap = ap;
        c.units[P1 as usize].fatigue = fatigue;
        let before = snapshot(&c);
        assert_eq!(c.apply(cmd), Err(expected), "{cmd:?} ap {ap} fatigue {fatigue}");
        assert_eq!(snapshot(&c), before, "{cmd:?}");
        if let Command::Move { unit, to } = cmd {
            if unit == P1 {
                assert_eq!(c.preview_move(unit, to), Err(expected), "{cmd:?}");
            }
            assert_eq!(snapshot(&c), before);
        }
    }
    // Loss of control outranks the budget checks as before.
    for (fleeing, retreating) in [(true, false), (false, true)] {
        let mut c = s.clone();
        c.units[P1 as usize].ap = 0;
        c.units[P1 as usize].fatigue = FMAX;
        if fleeing {
            c.units[P1 as usize].morale = Morale::Fleeing;
        }
        c.units[P1 as usize].retreating = retreating;
        let expected = if fleeing { RuleError::FleeingUnit } else { RuleError::RetreatingUnit };
        let before = snapshot(&c);
        assert_eq!(c.apply(Command::Move { unit: P1, to: path[2] }), Err(expected));
        assert_eq!(c.preview_move(P1, path[2]), Err(expected));
        assert_eq!(snapshot(&c), before);
    }
    // Queries are pure.
    let before = snapshot(&s);
    for _ in 0..3 {
        let _ = (s.preview_move(P1, path[2]), s.reachable(P1), s.state_hash());
    }
    assert_eq!(snapshot(&s), before);
}

// ------------------------------------------------------------------ attack budget

#[test]
fn nominal_attack_budget_uses_the_discounted_route_fatigue() {
    for actor in [P1, E1] {
        let (base, path) = lane(actor);
        let dest = path[1]; // two steps: 4 AP spent, 5 AP left >= ATTACK_AP
        assert!(9 - 2 * MOVE_AP >= ATTACK_AP);
        for (fmax, expected_pf, expected_twin) in [
            (2 * PATHFINDER_MOVE_FATIGUE + ATTACK_FATIGUE, true, false),
            (2 * PATHFINDER_MOVE_FATIGUE + ATTACK_FATIGUE - 1, false, false),
            (2 * MOVE_FATIGUE + ATTACK_FATIGUE, true, true),
        ] {
            let mut twin = base.clone();
            twin.units[actor as usize].stats.fatigue_max = fmax;
            let s = with_pathfinder(twin.clone(), actor);
            let (p, t) = (s.preview_move(actor, dest).unwrap(), twin.preview_move(actor, dest).unwrap());
            assert_eq!(p.attack_budget_available, expected_pf, "actor {actor} fmax {fmax}");
            assert_eq!(t.attack_budget_available, expected_twin, "actor {actor} fmax {fmax}");
        }
        // AP still limits the budget exactly as before.
        let mut s = with_pathfinder(base.clone(), actor);
        s.units[actor as usize].ap = 2 * MOVE_AP + ATTACK_AP - 1;
        assert!(!s.preview_move(actor, dest).unwrap().attack_budget_available);
        s.units[actor as usize].ap = 2 * MOVE_AP + ATTACK_AP;
        assert!(s.preview_move(actor, dest).unwrap().attack_budget_available);
        // The checked sum cannot overflow.
        let mut s = with_pathfinder(base, actor);
        s.units[actor as usize].stats.fatigue_max = u32::MAX;
        s.units[actor as usize].fatigue = u32::MAX - 1;
        let p = s.preview_move(actor, path[0]).unwrap();
        assert!(!p.attack_budget_available);
        assert_eq!(p.fatigue_cost, 1);
    }
}

// ------------------------------------------------------------------ reactions

/// The mover leaves a hex touching its foe: P1 at (0,-2), E1 at (1,-2); the route west
/// is (-1,-2), (-2,-2). Only the first departure is in the foe's zone of control.
fn reaction_board(mover: u32) -> (BattleState, Hex, Hex) {
    let (a, b) = (hex(0, -2), hex(1, -2));
    let s = if mover == P1 { board(&[(P1, a), (E1, b)]) } else { board(&[(E1, a), (P1, b)]) };
    (s, hex(-1, -2), hex(-2, -2))
}

#[test]
fn missed_stopping_and_lethal_reactions_see_the_discounted_step_and_change_nothing_else() {
    for mover in [P1, E1] {
        let foe = if mover == P1 { E1 } else { P1 };
        let (mut base, first, last) = reaction_board(mover);
        base.units[foe as usize].stats.melee_skill = 50;
        let m = mover as usize;

        // Missed: both steps happen; only the walking fatigue differs from the twin.
        let mut twin = base.clone();
        rig(&mut twin, foe, mover, false);
        let mut s = with_pathfinder(twin.clone(), mover);
        let (et, es) = (
            twin.apply(Command::Move { unit: mover, to: last }).unwrap(),
            s.apply(Command::Move { unit: mover, to: last }).unwrap(),
        );
        assert_eq!(es, et, "mover {mover}");
        assert!(matches!(es[0], Event::Attacked { hit: false, .. }));
        assert_eq!(moved_path(&es), vec![first, last]);
        assert_eq!(s.rng, twin.rng);
        assert_eq!(s.units[m].ap, twin.units[m].ap);
        assert_eq!(s.units[m].fatigue, 2 * PATHFINDER_MOVE_FATIGUE + DEFENDER_MISS_FATIGUE_GAIN);
        assert_eq!(twin.units[m].fatigue, 2 * MOVE_FATIGUE + DEFENDER_MISS_FATIGUE_GAIN);

        // Stopping hit: the attempted step is paid at the discount, the unit stays put.
        let mut twin = base.clone();
        rig(&mut twin, foe, mover, true);
        let mut s = with_pathfinder(twin.clone(), mover);
        let (et, es) = (
            twin.apply(Command::Move { unit: mover, to: last }).unwrap(),
            s.apply(Command::Move { unit: mover, to: last }).unwrap(),
        );
        assert_eq!(es, et, "mover {mover}");
        assert!(matches!(es[..], [Event::Attacked { hit: true, .. }]), "{es:?}");
        let u = &s.units[m];
        assert_eq!((u.pos, u.ap, u.fatigue), (hex(0, -2), 9 - MOVE_AP, PATHFINDER_MOVE_FATIGUE + DEFENDER_HIT_FATIGUE_GAIN));
        assert_eq!(twin.units[m].fatigue, MOVE_FATIGUE + DEFENDER_HIT_FATIGUE_GAIN);
        assert_eq!(s.rng, twin.rng);

        // Lethal hit: the attempt was paid before the reaction killed the mover.
        let mut twin = base.clone();
        twin.units[m].hp = 1;
        twin.units[m].armour = 0;
        rig(&mut twin, foe, mover, true);
        let mut s = with_pathfinder(twin.clone(), mover);
        let (et, es) = (
            twin.apply(Command::Move { unit: mover, to: last }).unwrap(),
            s.apply(Command::Move { unit: mover, to: last }).unwrap(),
        );
        assert_eq!(es, et, "mover {mover}");
        assert!(es.iter().any(|e| matches!(e, Event::Died { unit } if *unit == mover)), "{es:?}");
        let u = &s.units[m];
        assert!(!u.alive);
        assert_eq!((u.ap, u.fatigue), (9 - MOVE_AP, PATHFINDER_MOVE_FATIGUE + DEFENDER_HIT_FATIGUE_GAIN));
        assert_eq!(twin.units[m].fatigue, MOVE_FATIGUE + DEFENDER_HIT_FATIGUE_GAIN);
    }
}

#[test]
fn multistep_continuation_is_rechecked_against_fatigue_after_the_reaction() {
    for mover in [P1, E1] {
        let foe = if mover == P1 { E1 } else { P1 };
        let (mut base, first, last) = reaction_board(mover);
        base.units[foe as usize].stats.melee_skill = 50;
        let m = mover as usize;
        let after_first = PATHFINDER_MOVE_FATIGUE + DEFENDER_MISS_FATIGUE_GAIN;

        // Planning (cost 2) passes, but the miss leaves no room for the second step.
        let mut tight = with_pathfinder(base.clone(), mover);
        tight.units[m].stats.fatigue_max = after_first;
        rig(&mut tight, foe, mover, false);
        assert_eq!(tight.preview_move(mover, last).unwrap().fatigue_cost, 2);
        let ev = tight.apply(Command::Move { unit: mover, to: last }).unwrap();
        assert_eq!(moved_path(&ev), vec![first], "{ev:?}");
        let u = &tight.units[m];
        assert_eq!((u.pos, u.ap, u.fatigue), (first, 9 - MOVE_AP, after_first));

        // One more point of headroom and the exact cap is reached.
        let mut exact = with_pathfinder(base.clone(), mover);
        exact.units[m].stats.fatigue_max = after_first + PATHFINDER_MOVE_FATIGUE;
        rig(&mut exact, foe, mover, false);
        let ev = exact.apply(Command::Move { unit: mover, to: last }).unwrap();
        assert_eq!(moved_path(&ev), vec![first, last]);
        assert_eq!(exact.units[m].fatigue, exact.units[m].stats.fatigue_max);

        // The discount is what lets this budget pass the second step: the twin stops.
        let mut twin = base.clone();
        twin.units[m].stats.fatigue_max = 2 * MOVE_FATIGUE;
        rig(&mut twin, foe, mover, false);
        let mut pf = with_pathfinder(twin.clone(), mover);
        let ev_twin = twin.apply(Command::Move { unit: mover, to: last }).unwrap();
        let ev_pf = pf.apply(Command::Move { unit: mover, to: last }).unwrap();
        assert_eq!(moved_path(&ev_twin), vec![first]);
        assert_eq!(moved_path(&ev_pf), vec![first, last]);
    }
}

#[test]
fn a_reaction_at_the_u32_max_cap_saturates_without_panic() {
    for hit in [false, true] {
        let (mut s, first, _) = reaction_board(P1);
        s.units[E1 as usize].stats.melee_skill = 50;
        s.units[P1 as usize].has_pathfinder = true;
        s.units[P1 as usize].stats.fatigue_max = u32::MAX;
        s.units[P1 as usize].fatigue = u32::MAX - 1;
        rig(&mut s, E1, P1, hit);
        s.apply(Command::Move { unit: P1, to: first }).unwrap();
        assert_eq!(s.units[P1 as usize].fatigue, u32::MAX);
    }
}

// ------------------------------------------------------------------ Flee and Withdraw

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

/// P1 (Fleeing or retreating, pathfinder or not) first in the queue, plus the given foes/allies.
fn escape_board(kind: Auto, pathfinder: bool, p1: Hex, others: &[(u32, Hex)]) -> BattleState {
    let mut placed = vec![(P1, p1)];
    placed.extend_from_slice(others);
    let mut s = board(&placed);
    match kind {
        Auto::Flee => s.units[P1 as usize].morale = Morale::Fleeing,
        Auto::Withdraw => s.units[P1 as usize].retreating = true,
    }
    s.units[P1 as usize].has_pathfinder = pathfinder;
    s
}

const FAR: [(u32, Hex); 2] = [(E1, hex(3, -3)), (P2, hex(3, -1))];

#[test]
fn automatic_steps_charge_the_discount_and_use_it_for_affordability() {
    for kind in [Auto::Flee, Auto::Withdraw] {
        // (ap, fatigue, pathfinder moves, twin moves)
        let cases = [(2, FMAX - 1, true, false), (2, FMAX - 2, true, true), (2, FMAX, false, false), (1, 0, false, false)];
        for (ap, fatigue, pf_moves, twin_moves) in cases {
            for (pathfinder, moves) in [(true, pf_moves), (false, twin_moves)] {
                let mut s = escape_board(kind, pathfinder, hex(-2, 2), &FAR);
                s.units[P1 as usize].ap = ap;
                s.units[P1 as usize].fatigue = fatigue;
                let rng = s.rng.clone();
                let ev = s.apply(kind.cmd(P1)).unwrap();
                let u = &s.units[P1 as usize];
                let ctx = format!("{kind:?} ap {ap} fatigue {fatigue} pathfinder {pathfinder}");
                if moves {
                    assert_eq!(ev, vec![Event::Moved { unit: P1, path: vec![hex(-3, 2)] }], "{ctx}");
                    let step = if pathfinder { PATHFINDER_MOVE_FATIGUE } else { MOVE_FATIGUE };
                    assert_eq!((u.pos, u.ap, u.fatigue), (hex(-3, 2), ap - MOVE_AP, fatigue + step), "{ctx}");
                } else {
                    assert_eq!(ev, vec![started(E1)], "{ctx}");
                    assert_eq!((u.pos, u.ap, u.fatigue), (hex(-2, 2), ap, fatigue), "{ctx}");
                    assert_eq!(s.queue, vec![E1, P2], "{ctx}");
                }
                assert_eq!(s.rng, rng, "{ctx}");
            }
        }
    }
}

#[test]
fn automatic_steps_respect_the_u32_max_cap() {
    for kind in [Auto::Flee, Auto::Withdraw] {
        let mut s = escape_board(kind, true, hex(-2, 2), &FAR);
        s.units[P1 as usize].stats.fatigue_max = u32::MAX;
        s.units[P1 as usize].fatigue = u32::MAX - 1;
        let ev = s.apply(kind.cmd(P1)).unwrap();
        assert_eq!(ev, vec![Event::Moved { unit: P1, path: vec![hex(-3, 2)] }]);
        assert_eq!(s.units[P1 as usize].fatigue, u32::MAX);
        // At the cap the next attempt ends the turn without panicking.
        let ev = s.apply(kind.cmd(P1)).unwrap();
        assert_eq!(ev, vec![started(E1)]);
        assert_eq!(s.units[P1 as usize].fatigue, u32::MAX);
    }
}

#[test]
fn automatic_escape_pays_the_discount_and_an_edge_escape_stays_free() {
    for kind in [Auto::Flee, Auto::Withdraw] {
        let mut s = escape_board(kind, true, hex(-3, 3), &FAR);
        let ev = s.apply(kind.cmd(P1)).unwrap();
        assert_eq!(ev, vec![Event::Moved { unit: P1, path: vec![hex(-4, 3)] }, Event::Fled { unit: P1 }, started(E1)]);
        let u = &s.units[P1 as usize];
        assert_eq!((u.ap, u.fatigue, u.fled), (9 - MOVE_AP, PATHFINDER_MOVE_FATIGUE, true));

        for fatigue in [FMAX, 0] {
            let mut s = escape_board(kind, true, hex(-4, 3), &FAR);
            s.units[P1 as usize].ap = 0;
            s.units[P1 as usize].fatigue = fatigue;
            let rng = s.rng.clone();
            let ev = s.apply(kind.cmd(P1)).unwrap();
            assert_eq!(ev, vec![Event::Fled { unit: P1 }, started(E1)]);
            assert_eq!((s.units[P1 as usize].ap, s.units[P1 as usize].fatigue), (0, fatigue));
            assert_eq!(s.rng, rng);
        }
        let mut s = escape_board(kind, true, hex(-4, 3), &FAR);
        s.units[P1 as usize].stats.fatigue_max = u32::MAX;
        s.units[P1 as usize].fatigue = u32::MAX;
        s.apply(kind.cmd(P1)).unwrap();
        assert_eq!(s.units[P1 as usize].fatigue, u32::MAX);
    }
}

#[test]
fn automatic_steps_with_reactions_pay_before_the_attack_for_the_discount() {
    for kind in [Auto::Flee, Auto::Withdraw] {
        // P1 leaves (-3,3) beside E1 at (-2,3); the first exit is (-4,3).
        let make = |hit: bool, hp: u32| {
            let mut s = escape_board(kind, false, hex(-3, 3), &[(E1, hex(-2, 3)), (P2, hex(2, -3))]);
            let u = &mut s.units[P1 as usize];
            u.armour = 0;
            u.stats.armour = 0;
            u.stats.hp = 200;
            u.hp = hp;
            s.units[E1 as usize].stats.melee_skill = 50;
            rig(&mut s, E1, P1, hit);
            s
        };
        // Missed: the unit still reaches the edge and escapes.
        let (mut twin, mut s) = (make(false, 100), with_pathfinder(make(false, 100), P1));
        let (et, es) = (twin.apply(kind.cmd(P1)).unwrap(), s.apply(kind.cmd(P1)).unwrap());
        assert_eq!(es, et, "{kind:?}");
        assert!(matches!(es[..], [Event::Attacked { hit: false, .. }, Event::Moved { .. }, Event::Fled { .. }, Event::TurnStarted { .. }]));
        assert_eq!(s.units[P1 as usize].fatigue, PATHFINDER_MOVE_FATIGUE + DEFENDER_MISS_FATIGUE_GAIN);
        assert_eq!(twin.units[P1 as usize].fatigue, MOVE_FATIGUE + DEFENDER_MISS_FATIGUE_GAIN);

        // Stopping hit: the attempt is paid, the unit does not escape, and a second
        // attempt is judged on the current fatigue.
        let (mut twin, mut s) = (make(true, 100), with_pathfinder(make(true, 100), P1));
        let (et, es) = (twin.apply(kind.cmd(P1)).unwrap(), s.apply(kind.cmd(P1)).unwrap());
        assert_eq!(es, et, "{kind:?}");
        let u = &s.units[P1 as usize];
        assert_eq!((u.pos, u.ap, u.fatigue, u.fled), (hex(-3, 3), 9 - MOVE_AP, PATHFINDER_MOVE_FATIGUE + DEFENDER_HIT_FATIGUE_GAIN, false));
        assert_eq!(s.queue[0], P1);
        s.units[P1 as usize].stats.fatigue_max = PATHFINDER_MOVE_FATIGUE + DEFENDER_HIT_FATIGUE_GAIN;
        s.units[P1 as usize].fatigue = s.units[P1 as usize].stats.fatigue_max;
        let ev = s.apply(kind.cmd(P1)).unwrap();
        assert_eq!(ev, vec![started(E1)], "no headroom left: the turn ends");

        // Lethal hit: a normal death after the attempt was paid.
        let (mut twin, mut s) = (make(true, 1), with_pathfinder(make(true, 1), P1));
        let (et, es) = (twin.apply(kind.cmd(P1)).unwrap(), s.apply(kind.cmd(P1)).unwrap());
        assert_eq!(es, et, "{kind:?}");
        assert!(es.iter().any(|e| matches!(e, Event::Died { unit: 0 })), "{es:?}");
        let u = &s.units[P1 as usize];
        assert!(!u.alive && !u.fled);
        assert_eq!((u.ap, u.fatigue), (9 - MOVE_AP, PATHFINDER_MOVE_FATIGUE + DEFENDER_HIT_FATIGUE_GAIN));
    }
}

// ------------------------------------------------------------------ AI

#[test]
fn ai_walks_the_longest_prefix_affordable_with_the_discount_and_the_command_validates() {
    // (heights along the path, ap, fatigue, fatigue_max, steps with the discount, steps without)
    let cases: [([u8; 3], u32, u32, u32, usize, usize); 10] = [
        ([0, 0, 0], 9, 0, FMAX, 3, 3),
        ([0, 0, 0], 9, 95, FMAX, 3, 2),
        ([0, 0, 0], 9, 98, FMAX, 2, 1),
        ([0, 0, 0], 9, 99, FMAX, 1, 0),
        ([0, 0, 0], 9, 100, FMAX, 0, 0),
        ([1, 0, 3], 9, 0, FMAX, 2, 2),
        ([3, 0, 0], 4, 0, FMAX, 0, 0),
        ([0, 0, 0], 9, u32::MAX - 1, u32::MAX, 1, 0),
        ([0, 0, 0], 9, u32::MAX, u32::MAX, 0, 0),
        ([1, 0, 1], 8, 98, FMAX, 2, 1),
    ];
    for actor in [P1, E1] {
        for (heights, ap, fatigue, fmax, pf_steps, twin_steps) in cases {
            let (mut twin, path) = ai_board(actor);
            for (h, &at) in heights.iter().zip(&path) {
                set_h(&mut twin, at, *h);
            }
            let u = &mut twin.units[actor as usize];
            (u.ap, u.fatigue, u.stats.fatigue_max) = (ap, fatigue, fmax);
            let ctx = format!("actor {actor} {heights:?} ap {ap} fatigue {fatigue}");
            for (pathfinder, steps) in [(false, twin_steps), (true, pf_steps)] {
                let mut s = twin.clone();
                s.units[actor as usize].has_pathfinder = pathfinder;
                let before = snapshot(&s);
                let cmd = plan_turn(&s);
                assert_eq!(snapshot(&s), before, "planning is pure: {ctx}");
                if steps == 0 {
                    assert_eq!(cmd, vec![Command::EndTurn { unit: actor }], "{ctx}");
                    s.apply(cmd[0]).unwrap();
                    continue;
                }
                assert_eq!(cmd, vec![Command::Move { unit: actor, to: path[steps - 1] }], "{ctx} pathfinder {pathfinder}");
                let preview = s.preview_move(actor, path[steps - 1]).unwrap();
                assert_eq!(preview.path, path[..steps].to_vec());
                let per_step = if pathfinder { PATHFINDER_MOVE_FATIGUE } else { MOVE_FATIGUE };
                assert_eq!(preview.fatigue_cost, per_step * steps as u32);
                s.apply(cmd[0]).unwrap();
                let u = &s.units[actor as usize];
                assert_eq!((u.pos, u.fatigue), (path[steps - 1], fatigue + per_step * steps as u32), "{ctx}");
                assert_eq!(u.ap, ap - preview.ap_cost, "{ctx}");
                assert_eq!(s.rng, Rng::new(1), "no zone of control on the lane");
            }
        }
    }
}

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

// ------------------------------------------------------------------ unaffected costs

#[test]
fn attacks_skills_incoming_fatigue_and_regeneration_are_not_discounted() {
    // Attack: fixed AP and fatigue, identical events and RNG.
    let mut twin = board(&[(P1, hex(0, -2)), (E1, hex(1, -2))]);
    rig(&mut twin, P1, E1, true);
    let mut s = with_pathfinder(twin.clone(), P1);
    let (et, es) = (
        twin.apply(Command::Attack { attacker: P1, target: E1 }).unwrap(),
        s.apply(Command::Attack { attacker: P1, target: E1 }).unwrap(),
    );
    assert_eq!(es, et);
    assert_eq!((s.units[P1 as usize].ap, s.units[P1 as usize].fatigue), (9 - ATTACK_AP, ATTACK_FATIGUE));
    assert_eq!(snapshot(&s).3, snapshot(&twin).3.iter().cloned().map(|mut u| {
        if u.id == P1 {
            u.has_pathfinder = true;
        }
        u
    }).collect::<Vec<_>>());

    // The foe's incoming fatigue on a pathfinder defender is the ordinary gain.
    let mut s = with_pathfinder(board(&[(E1, hex(1, -2)), (P1, hex(0, -2))]), P1);
    s.units[E1 as usize].stats.melee_skill = 50;
    rig(&mut s, E1, P1, false);
    s.apply(Command::Attack { attacker: E1, target: P1 }).unwrap();
    assert_eq!(s.units[P1 as usize].fatigue, DEFENDER_MISS_FATIGUE_GAIN);

    // Rotation and Footwork keep their fixed costs.
    let mut s = board(&[(P1, hex(-3, 3)), (P2, hex(-3, 2)), (E1, hex(3, -3))]);
    s.units[P1 as usize].has_pathfinder = true;
    s.units[P1 as usize].has_rotation = true;
    s.units[P1 as usize].has_footwork = true;
    let mut rot = s.clone();
    rot.apply(Command::Rotation { unit: P1, target: P2 }).unwrap();
    assert_eq!((rot.units[P1 as usize].ap, rot.units[P1 as usize].fatigue), (9 - ROTATION_AP, ROTATION_FATIGUE));
    let mut foot = s.clone();
    foot.apply(Command::Footwork { unit: P1, to: hex(-2, 3) }).unwrap();
    assert_eq!((foot.units[P1 as usize].ap, foot.units[P1 as usize].fatigue), (9 - FOOTWORK_AP, FOOTWORK_FATIGUE));

    // Regeneration at the next turn start is the same with and without the flag.
    for pathfinder in [false, true] {
        let mut s = board(&[(E1, hex(3, -3)), (P1, hex(-3, 3))]);
        s.units[P1 as usize].has_pathfinder = pathfinder;
        s.units[P1 as usize].fatigue = 40;
        s.apply(Command::EndTurn { unit: E1 }).unwrap();
        assert_eq!(s.units[P1 as usize].fatigue, 40 - FATIGUE_REGEN, "pathfinder {pathfinder}");
    }
}

#[test]
fn discounted_movement_immediately_changes_accepted_dodge_calculations() {
    // Dodge bonus is floor(15% of initiative - fatigue / 2): 20 -> 3, 19..=14 -> 2, 13 -> 1.
    // (initiative, steps, start chance, discounted chance, undiscounted chance)
    for (initiative, steps, start, pf_chance, twin_chance) in [(20, 1usize, 47, 47, 48), (15, 2, 48, 48, 49)] {
        let mut twin = board(&[(P1, hex(-2, -2)), (E1, hex(4, -2))]);
        twin.units[E1 as usize].stats.melee_skill = 50;
        twin.units[P1 as usize].stats.initiative = initiative;
        twin.units[P1 as usize].has_dodge = true;
        assert_eq!(twin.hit_chance(E1, P1), start);
        let mut s = with_pathfinder(twin.clone(), P1);
        let dest = [hex(-1, -2), hex(0, -2)][steps - 1];
        s.apply(Command::Move { unit: P1, to: dest }).unwrap();
        twin.apply(Command::Move { unit: P1, to: dest }).unwrap();
        assert_eq!(s.units[P1 as usize].fatigue, steps as u32 * PATHFINDER_MOVE_FATIGUE);
        assert_eq!(s.hit_chance(E1, P1), pf_chance, "initiative {initiative} steps {steps}");
        assert_eq!(twin.hit_chance(E1, P1), twin_chance, "initiative {initiative} steps {steps}");
    }
}

// ------------------------------------------------------------------ hash

#[test]
fn hash_flag_is_distinct_from_every_earlier_flag_and_false_changes_nothing() {
    let base = || board(&[(P1, hex(-2, -2)), (E1, hex(4, -2))]);
    let plain = base().state_hash();
    // Pins from before the flag existed.
    assert_eq!(BattleState::new(1).state_hash(), 10_088_116_083_934_094_952);
    assert_eq!(scenario::battle(1, TerrainPreset::Flat).state_hash(), 0x8c00302113cbe668);
    assert_eq!(scenario::battle(1, TerrainPreset::Hills).state_hash(), 0xb759683cdff4bfa2);

    let toggles: [fn(&mut Unit); 18] = [
        |u| u.has_pathfinder = true,
        |u| u.waited = true,
        |u| u.skip_turn = true,
        |u| u.fled = true,
        |u| u.retreating = true,
        |u| u.has_adrenaline = true,
        |u| u.adrenaline_pending = true,
        |u| u.has_rotation = true,
        |u| u.has_footwork = true,
        |u| u.has_fast_adaptation = true,
        |u| u.fast_adaptation_stacks = u32::MAX,
        |u| u.has_dodge = true,
        |u| u.has_nine_lives = true,
        |u| u.nine_lives_used = true,
        |u| u.has_berserk = true,
        |u| u.berserk_used = true,
        |u| u.turn_started = !u.turn_started,
        |u| u.morale = Morale::Fleeing,
    ];
    let mut seen = vec![plain];
    for toggle in toggles {
        let mut s = base();
        toggle(&mut s.units[P1 as usize]);
        seen.push(s.state_hash());
    }
    let mut unique = seen.clone();
    unique.sort_unstable();
    unique.dedup();
    assert_eq!(unique.len(), seen.len(), "{seen:x?}");

    // With every other bit set, the flag still moves the hash, and it is per unit.
    let mut all = base();
    for toggle in &toggles[1..] {
        toggle(&mut all.units[P1 as usize]);
    }
    let mut all_pf = all.clone();
    all_pf.units[P1 as usize].has_pathfinder = true;
    assert_ne!(all.state_hash(), all_pf.state_hash());
    let mut other = base();
    other.units[E1 as usize].has_pathfinder = true;
    assert_ne!(other.state_hash(), seen[1]);
    let mut roundtrip = all_pf.clone();
    roundtrip.units[P1 as usize].has_pathfinder = false;
    assert_eq!(roundtrip.state_hash(), all.state_hash());
}
