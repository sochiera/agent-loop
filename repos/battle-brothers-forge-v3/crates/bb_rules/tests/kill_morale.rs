//! Positive Resolve check of the killer after a melee kill (partial
//! UC-COMBAT-018 / UC-COMBAT-048). The triggers trace to the catalog; the
//! killer-only affected set, the formula chance = min(Resolve, 100) with
//! `next_u64() % 100 < chance`, the zero chance on a map-edge hex and the
//! scheduling are an APPROXIMATION without original-runtime confirmation. The
//! other UC-COMBAT-048 flow, the movement-triggered edge penalty, lives in
//! `map_edge_morale.rs`. Fixtures put the six stock units on fixed hexes and seeds
//! are found by mirroring the SplitMix64 stream. Draw order of a kill: attack roll,
//! damage draw, one draw per surviving victim-side ally in ascending id, then
//! the killer's check.

use bb_rules::hex::{Hex, hex};
use bb_rules::rules::soak;
use bb_rules::*;

const P1: u32 = 0;
const P2: u32 = 1;
const P3: u32 = 2;
const E1: u32 = 3;
const E2: u32 = 4;
const E3: u32 = 5;

const HIT_CHANCE: u32 = 95;
const DMG: u32 = 10;
/// Kill-check draw index with two surviving victim-side allies.
const KILL_IDX: usize = 4;

fn started(unit: u32) -> Event {
    Event::TurnStarted { unit }
}

/// `i`-th raw draw of a fresh stream, reduced like the rules do.
fn draw(seed: u64, i: usize) -> u32 {
    let mut rng = Rng::new(seed);
    let mut v = 0;
    for _ in 0..=i {
        v = rng.next_u64();
    }
    (v % 100) as u32
}

fn rng_after(seed: u64, n: usize) -> Rng {
    let mut rng = Rng::new(seed);
    for _ in 0..n {
        rng.next_u64();
    }
    rng
}

/// First seed whose draws satisfy `f(seed)`.
fn seed_where(f: impl Fn(u64) -> bool) -> u64 {
    (0..100_000u64).find(|&s| f(s)).expect("a suitable seed exists")
}

/// Kill at the forced 95% chance with the kill check at draw `idx`: it passes a
/// 50-Resolve check (`pass`) or fails it.
fn kill_seed(idx: usize, pass: bool) -> u64 {
    seed_where(|s| draw(s, 0) < HIT_CHANCE && (draw(s, idx) < 50) == pass)
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

const START: [(u32, Hex); 6] =
    [(P1, hex(1, 0)), (P2, hex(-3, 2)), (P3, hex(-4, 3)), (E1, hex(2, 0)), (E2, hex(4, -3)), (E3, hex(3, -2))];

/// Representative legal killer/victim placements: one point on each map side
/// plus all six corners. The victim is adjacent and one ring inward.
const EDGE_KILL_POSITIONS: [(Hex, Hex); 12] = [
    (hex(4, -2), hex(3, -2)),
    (hex(-4, 2), hex(-3, 2)),
    (hex(-2, 4), hex(-2, 3)),
    (hex(2, -4), hex(2, -3)),
    (hex(-2, -2), hex(-1, -2)),
    (hex(2, 2), hex(1, 2)),
    (hex(4, 0), hex(3, 0)),
    (hex(4, -4), hex(3, -3)),
    (hex(0, 4), hex(0, 3)),
    (hex(-4, 4), hex(-3, 3)),
    (hex(-4, 0), hex(-3, 0)),
    (hex(0, -4), hex(0, -3)),
];

/// The six stock units on fixed hexes; `active` leads the queue (9 AP, turn
/// started), the others follow in ascending id order.
fn six(active: u32) -> BattleState {
    let mut s = BattleState::new(0);
    for (id, pos) in START {
        s.units[id as usize].pos = pos;
    }
    s.queue = std::iter::once(active).chain((0..6).filter(|&id| id != active)).collect();
    for u in &mut s.units {
        u.turn_started = u.id == active;
    }
    let a = &mut s.units[active as usize];
    a.ap = 9;
    a.fatigue = 0;
    assert_valid_fixture(&s);
    s
}

/// `killer` hits for `DMG` at 95% and `victim` dies to any hit; `seed` is set.
fn arm(s: &mut BattleState, killer: u32, victim: u32, seed: u64) {
    let k = &mut s.units[killer as usize];
    k.stats.melee_skill = 1000;
    k.stats.dmg_min = DMG;
    k.stats.dmg_max = DMG;
    let v = &mut s.units[victim as usize];
    v.stats.hp = 1;
    v.hp = 1;
    v.stats.armour = 0;
    v.armour = 0;
    s.rng = Rng::new(seed);
}

/// Place the attacker and victim on a legal adjacent pair, spreading other
/// present units away from the victim so only the intended attack matters.
fn position_kill_pair(state: &mut BattleState, killer: u32, victim: u32, killer_pos: Hex, victim_pos: Hex) {
    state.units[killer as usize].pos = killer_pos;
    state.units[victim as usize].pos = victim_pos;
    let mut open = bb_rules::hex::map_hexes()
        .into_iter()
        .filter(|h| !h.is_obstacle() && *h != killer_pos && *h != victim_pos && h.distance(victim_pos) > 1);
    for unit in &mut state.units {
        if unit.id != killer && unit.id != victim {
            unit.pos = open.next().expect("the map has room for the remaining units");
        }
    }
    assert_eq!(killer_pos.distance(victim_pos), 1);
    assert_valid_fixture(state);
}

fn edge_kill(killer: u32, victim: u32, edge: Hex, inner: Hex, seed: u64) -> BattleState {
    let mut state = six(killer);
    position_kill_pair(&mut state, killer, victim, edge, inner);
    arm(&mut state, killer, victim, seed);
    state
}

fn remove(s: &mut BattleState, ids: &[u32]) {
    for &id in ids {
        s.units[id as usize].alive = false;
        s.queue.retain(|&q| q != id);
    }
}

fn attacked(attacker: u32, target: u32, seed: u64) -> Event {
    Event::Attacked {
        attacker,
        target,
        hit: true,
        roll: draw(seed, 0),
        chance: HIT_CHANCE,
        dmg: DMG,
        absorbed: 0,
        hp_loss: DMG,
    }
}

/// The `idx`-th ally check (draw `2 + idx`) with the accepted negative-check formula.
fn ally_check(seed: u64, idx: usize, unit: u32, fallen: u32, resolve: u32, before: Morale) -> Event {
    let roll = draw(seed, 2 + idx);
    let chance = resolve.min(100);
    let after = if roll < chance { before } else { before.lowered() };
    Event::AllyDeathMoraleChecked { unit, fallen, roll, chance, before, after }
}

/// The killer's check at draw `draw_idx` with the accepted positive-check formula.
fn kill_check(seed: u64, draw_idx: usize, unit: u32, fallen: u32, resolve: u32, before: Morale) -> Event {
    let roll = draw(seed, draw_idx);
    let chance = resolve.min(100);
    let after = if roll < chance { before.raised() } else { before };
    Event::KillMoraleChecked { unit, fallen, roll, chance, before, after }
}

fn kill_checks(ev: &[Event]) -> usize {
    ev.iter().filter(|e| matches!(e, Event::KillMoraleChecked { .. })).count()
}

fn snapshot(s: &BattleState) -> (u64, Rng, Vec<u32>) {
    (s.state_hash(), s.rng.clone(), s.queue.clone())
}

/// Opportunity kill: `mover` (1 HP) steps away from adjacent `killer`.
fn reaction_kill(mover: u32, killer: u32, to: Hex, seed: u64) -> BattleState {
    let mut s = six(mover);
    arm(&mut s, killer, mover, seed);
    assert_eq!(s.enemy_controllers_at(mover, s.units[mover as usize].pos), vec![killer]);
    assert_eq!(s.preview_move(mover, to).unwrap().first_threat.map(|t| t.controllers), Some(vec![killer]));
    s
}

fn edge_reaction_kill(mover: u32, killer: u32, edge: Hex, inner: Hex, seed: u64) -> (BattleState, Hex) {
    let mut s = six(mover);
    position_kill_pair(&mut s, killer, mover, edge, inner);
    arm(&mut s, killer, mover, seed);
    let to = inner
        .neighbors()
        .into_iter()
        .find(|h| {
            h.in_map()
                && !h.is_obstacle()
                && h.q.abs().max(h.r.abs()).max(h.s().abs()) < bb_rules::hex::RADIUS
                && !s.units.iter().any(|u| u.is_present() && u.pos == *h)
        })
        .expect("the adjacent mover has an open step");
    assert_eq!(s.enemy_controllers_at(mover, inner), vec![killer]);
    assert_eq!(s.preview_move(mover, to).unwrap().first_threat.map(|t| t.controllers), Some(vec![killer]));
    assert!(to.in_map());
    assert!(to.q.abs().max(to.r.abs()).max(to.s().abs()) < bb_rules::hex::RADIUS);
    (s, to)
}

// --- Morale::raised -----------------------------------------------------------------

#[test]
fn raised_maps_every_state_one_step_up() {
    let table = [
        (Morale::Breaking, Morale::Wavering),
        (Morale::Wavering, Morale::Steady),
        (Morale::Steady, Morale::Confident),
        (Morale::Confident, Morale::Confident),
        (Morale::Fleeing, Morale::Fleeing),
    ];
    for (before, after) in table {
        assert_eq!(before.raised(), after, "{before:?}");
    }
    // Pure: it is a const fn of the value and touches nothing else.
    const R: Morale = Morale::Breaking.raised();
    assert_eq!(R, Morale::Wavering);
}

// --- Trigger, order, RNG ------------------------------------------------------------

#[test]
fn ordinary_kill_checks_only_the_killer_after_the_ally_checks_on_both_sides() {
    for pass in [true, false] {
        let seed = kill_seed(KILL_IDX, pass);
        for (killer, victim, allies, own) in [(P1, E1, [E2, E3], [P2, P3]), (E1, P1, [P2, P3], [E2, E3])] {
            let mut s = six(killer);
            arm(&mut s, killer, victim, seed);
            let own_before: Vec<Morale> = own.iter().map(|&id| s.units[id as usize].morale).collect();
            let ev = s.apply(Command::Attack { attacker: killer, target: victim }).unwrap();
            let kill = kill_check(seed, KILL_IDX, killer, victim, 50, Morale::Steady);
            assert_eq!(
                ev,
                vec![
                    attacked(killer, victim, seed),
                    Event::Died { unit: victim },
                    ally_check(seed, 0, allies[0], victim, 50, Morale::Steady),
                    ally_check(seed, 1, allies[1], victim, 50, Morale::Steady),
                    kill.clone(),
                ]
            );
            assert_eq!(s.rng, rng_after(seed, 5), "roll, damage, two ally draws and one kill draw");
            let expected = if pass { Morale::Confident } else { Morale::Steady };
            assert_eq!(s.units[killer as usize].morale, expected);
            assert!(matches!(kill, Event::KillMoraleChecked { after, .. } if after == expected));
            // No other unit of the killer's side changes through this mechanism.
            let own_after: Vec<Morale> = own.iter().map(|&id| s.units[id as usize].morale).collect();
            assert_eq!(own_after, own_before);
            assert_eq!(s.outcome(), None);
        }
    }
}

#[test]
fn edge_killer_checks_have_zero_chance_on_each_side_and_corner_for_both_factions_and_eligible_states() {
    let seed = kill_seed(KILL_IDX, true);
    let eligible = [Morale::Breaking, Morale::Wavering, Morale::Steady, Morale::Confident];
    for (edge, inner) in EDGE_KILL_POSITIONS {
        for (killer, victim) in [(P1, E1), (E1, P1)] {
            for before in eligible {
                let mut state = edge_kill(killer, victim, edge, inner, seed);
                state.units[killer as usize].stats.resolve = 100;
                state.units[killer as usize].morale = before;
                let stats = state.units[killer as usize].stats.clone();
                let allies = if victim < 3 { [P2, P3] } else { [E2, E3] };

                let events = state.apply(Command::Attack { attacker: killer, target: victim }).unwrap();

                assert_eq!(
                    events,
                    vec![
                        attacked(killer, victim, seed),
                        Event::Died { unit: victim },
                        ally_check(seed, 0, allies[0], victim, 50, Morale::Steady),
                        ally_check(seed, 1, allies[1], victim, 50, Morale::Steady),
                        Event::KillMoraleChecked {
                            unit: killer,
                            fallen: victim,
                            roll: draw(seed, KILL_IDX),
                            chance: 0,
                            before,
                            after: before,
                        },
                    ],
                    "edge {edge:?}, {killer}, {before:?}"
                );
                assert_eq!(state.units[killer as usize].morale, before, "edge {edge:?}, {killer}, {before:?}");
                assert_eq!(state.units[killer as usize].stats, stats, "base stats stay unchanged");
                assert_eq!(state.rng, rng_after(seed, 5), "the zero-chance check still consumes one draw");
            }
        }
    }
}

#[test]
fn edge_resolve_100_and_above_fail_without_rewriting_base_resolve() {
    let seed = kill_seed(KILL_IDX, true);
    for (edge, inner) in [EDGE_KILL_POSITIONS[0], EDGE_KILL_POSITIONS[6]] {
        for (killer, victim) in [(P1, E1), (E1, P1)] {
            for resolve in [100, 150] {
                let mut state = edge_kill(killer, victim, edge, inner, seed);
                state.units[killer as usize].stats.resolve = resolve;
                let events = state.apply(Command::Attack { attacker: killer, target: victim }).unwrap();
                assert_eq!(
                    events[4],
                    Event::KillMoraleChecked {
                        unit: killer,
                        fallen: victim,
                        roll: draw(seed, KILL_IDX),
                        chance: 0,
                        before: Morale::Steady,
                        after: Morale::Steady,
                    }
                );
                assert_eq!(state.units[killer as usize].stats.resolve, resolve);
                assert_eq!(state.rng, rng_after(seed, 5));
            }
        }
    }
}

#[test]
fn interior_killer_can_still_improve_when_the_victim_is_on_the_edge() {
    let seed = kill_seed(KILL_IDX, true);
    let (edge, inner) = EDGE_KILL_POSITIONS[0];
    let mut state = six(P1);
    position_kill_pair(&mut state, P1, E1, inner, edge);
    arm(&mut state, P1, E1, seed);
    state.units[P1 as usize].stats.resolve = 100;

    let events = state.apply(Command::Attack { attacker: P1, target: E1 }).unwrap();

    assert_eq!(state.units[P1 as usize].pos, inner);
    assert_eq!(state.units[E1 as usize].pos, edge);
    assert_eq!(
        events[4],
        Event::KillMoraleChecked {
            unit: P1,
            fallen: E1,
            roll: draw(seed, KILL_IDX),
            chance: 100,
            before: Morale::Steady,
            after: Morale::Confident,
        }
    );
    assert_eq!(state.units[P1 as usize].morale, Morale::Confident);
    assert_eq!(state.rng, rng_after(seed, 5));
}

#[test]
fn edge_opportunity_kills_keep_the_zero_chance_check_for_both_factions() {
    let seed = seed_where(|s| draw(s, 0) < HIT_CHANCE);
    for (mover, killer, edge, inner) in [
        (P1, E1, hex(4, -2), hex(3, -2)),
        (E1, P1, hex(-4, 2), hex(-3, 2)),
    ] {
        let (mut state, to) = edge_reaction_kill(mover, killer, edge, inner, seed);
        let allies = if mover < 3 { [P2, P3] } else { [E2, E3] };
        let events = state.apply(Command::Move { unit: mover, to }).unwrap();
        assert_eq!(
            events,
            vec![
                attacked(killer, mover, seed),
                Event::Died { unit: mover },
                ally_check(seed, 0, allies[0], mover, 50, Morale::Steady),
                ally_check(seed, 1, allies[1], mover, 50, Morale::Steady),
                Event::KillMoraleChecked {
                    unit: killer,
                    fallen: mover,
                    roll: draw(seed, KILL_IDX),
                    chance: 0,
                    before: Morale::Steady,
                    after: Morale::Steady,
                },
                started(if mover == P1 { P2 } else { P1 }),
            ],
            "mover {mover} reacting killer {killer}"
        );
        assert_eq!(state.units[killer as usize].pos, edge, "check uses the killer's current position");
        assert_eq!(state.units[killer as usize].morale, Morale::Steady);
        assert_eq!(state.rng, rng_after(seed, 5));
    }
}

#[test]
fn an_edge_final_kill_still_emits_zero_chance_check_before_battle_ended() {
    let seed = seed_where(|s| draw(s, 0) < HIT_CHANCE);
    for (killer, victim, edge, inner) in [
        (P1, E1, hex(4, -2), hex(3, -2)),
        (E1, P1, hex(-4, 2), hex(-3, 2)),
    ] {
        let mut state = edge_kill(killer, victim, edge, inner, seed);
        let others: Vec<u32> = (0..6).filter(|&id| id != killer && id != victim).collect();
        remove(&mut state, &others);
        let outcome = if killer == P1 { Outcome::Victory } else { Outcome::Defeat };
        let events = state.apply(Command::Attack { attacker: killer, target: victim }).unwrap();
        assert_eq!(
            events,
            vec![
                attacked(killer, victim, seed),
                Event::Died { unit: victim },
                Event::KillMoraleChecked {
                    unit: killer,
                    fallen: victim,
                    roll: draw(seed, 2),
                    chance: 0,
                    before: Morale::Steady,
                    after: Morale::Steady,
                },
                Event::BattleEnded { outcome, reason: EndReason::Elimination },
            ]
        );
        assert_eq!(state.rng, rng_after(seed, 3));
        assert_eq!(state.outcome(), Some(outcome));
    }
}

#[test]
fn every_raise_transition_and_a_failure_that_changes_nothing() {
    let seed = kill_seed(KILL_IDX, true);
    let table = [
        (Morale::Breaking, Morale::Wavering),
        (Morale::Wavering, Morale::Steady),
        (Morale::Steady, Morale::Confident),
        (Morale::Confident, Morale::Confident),
    ];
    for (before, after) in table {
        for (resolve, expected) in [(100, after), (0, before)] {
            let mut s = six(P1);
            arm(&mut s, P1, E1, seed);
            s.units[P1 as usize].morale = before;
            s.units[P1 as usize].stats.resolve = resolve;
            let stats = s.units[P1 as usize].stats.clone();
            let ev = s.apply(Command::Attack { attacker: P1, target: E1 }).unwrap();
            // One event and one draw even when the killer is already Confident.
            assert_eq!(ev[4], kill_check(seed, KILL_IDX, P1, E1, resolve, before), "{before:?} resolve {resolve}");
            assert_eq!(ev.len(), 5);
            assert_eq!(kill_checks(&ev), 1);
            assert_eq!(s.rng, rng_after(seed, 5));
            assert_eq!(s.units[P1 as usize].morale, expected, "{before:?} resolve {resolve}");
            assert_eq!(s.units[P1 as usize].stats, stats, "base stats are never modified");
        }
    }
}

#[test]
fn resolve_zero_hundred_above_hundred_and_the_equality_boundary() {
    let seed = seed_where(|s| draw(s, 0) < HIT_CHANCE && (1..=98).contains(&draw(s, KILL_IDX)));
    let r = draw(seed, KILL_IDX);
    // roll < min(resolve, 100) passes; roll == chance fails.
    for (resolve, passes) in [(0, false), (r, false), (r + 1, true), (100, true), (150, true), (u32::MAX, true)] {
        let mut s = six(P1);
        arm(&mut s, P1, E1, seed);
        s.units[P1 as usize].stats.resolve = resolve;
        let ev = s.apply(Command::Attack { attacker: P1, target: E1 }).unwrap();
        assert_eq!(ev[4], kill_check(seed, KILL_IDX, P1, E1, resolve, Morale::Steady), "resolve {resolve}");
        let Event::KillMoraleChecked { chance, roll, .. } = ev[4] else { unreachable!() };
        assert_eq!((chance, roll), (resolve.min(100), r));
        let expected = if passes { Morale::Confident } else { Morale::Steady };
        assert_eq!(s.units[P1 as usize].morale, expected, "resolve {resolve}");
        assert_eq!(s.units[P1 as usize].stats.resolve, resolve, "base Resolve is never modified");
        assert_eq!(s.rng, rng_after(seed, 5));
    }
}

#[test]
fn opportunity_kills_check_the_reaction_killer_before_the_next_turn_starts() {
    for pass in [true, false] {
        let seed = kill_seed(KILL_IDX, pass);
        let expected = if pass { Morale::Confident } else { Morale::Steady };
        // Player mover killed by E1's reaction; then P2 starts.
        let mut s = reaction_kill(P1, E1, hex(0, 1), seed);
        let ev = s.apply(Command::Move { unit: P1, to: hex(0, 1) }).unwrap();
        assert_eq!(
            ev,
            vec![
                attacked(E1, P1, seed),
                Event::Died { unit: P1 },
                ally_check(seed, 0, P2, P1, 50, Morale::Steady),
                ally_check(seed, 1, P3, P1, 50, Morale::Steady),
                kill_check(seed, KILL_IDX, E1, P1, 50, Morale::Steady),
                started(P2),
            ]
        );
        assert_eq!(s.rng, rng_after(seed, 5));
        assert_eq!(s.units[E1 as usize].morale, expected);
        assert_eq!(s.queue, vec![P2, P3, E1, E2, E3]);

        // Enemy mover killed by P1's reaction; then P1 starts.
        let mut s = reaction_kill(E1, P1, hex(3, 0), seed);
        let ev = s.apply(Command::Move { unit: E1, to: hex(3, 0) }).unwrap();
        assert_eq!(
            ev,
            vec![
                attacked(P1, E1, seed),
                Event::Died { unit: E1 },
                ally_check(seed, 0, E2, E1, 50, Morale::Steady),
                ally_check(seed, 1, E3, E1, 50, Morale::Steady),
                kill_check(seed, KILL_IDX, P1, E1, 50, Morale::Steady),
                started(P1),
            ]
        );
        assert_eq!(s.rng, rng_after(seed, 5));
        assert_eq!(s.units[P1 as usize].morale, expected);
        assert_eq!(s.queue, vec![P1, P2, P3, E2, E3]);
    }
}

#[test]
fn fleeing_and_withdrawing_victims_still_give_their_killer_a_check() {
    let seed = kill_seed(KILL_IDX, true);
    for withdraw in [false, true] {
        let mut s = six(P1);
        arm(&mut s, E1, P1, seed);
        assert_eq!(s.enemy_controllers_at(P1, s.units[P1 as usize].pos), vec![E1]);
        let cmd = if withdraw {
            s.units[P1 as usize].retreating = true;
            Command::Withdraw { unit: P1 }
        } else {
            s.units[P1 as usize].morale = Morale::Fleeing;
            Command::Flee { unit: P1 }
        };
        let ev = s.apply(cmd).unwrap();
        assert_eq!(ev[..2], [attacked(E1, P1, seed), Event::Died { unit: P1 }]);
        assert_eq!(ev[4], kill_check(seed, KILL_IDX, E1, P1, 50, Morale::Steady), "{ev:?}");
        assert_eq!(ev[5], started(P2));
        assert_eq!(kill_checks(&ev), 1);
        assert_eq!(s.units[E1 as usize].morale, Morale::Confident);
        assert_eq!(s.rng, rng_after(seed, 5));
        assert!(!s.units[P1 as usize].alive && !s.units[P1 as usize].fled);
    }
}

#[test]
fn a_retreating_reaction_killer_keeps_retreat_intent_its_skip_and_the_queue() {
    let seed = seed_where(|s| draw(s, 0) < HIT_CHANCE);
    let (mut s, to) = edge_reaction_kill(E1, P1, hex(4, -2), hex(3, -2), seed);
    s.units[P1 as usize].retreating = true;
    s.units[P1 as usize].skip_turn = true;
    let stats = s.units[P1 as usize].stats.clone();
    let ev = s.apply(Command::Move { unit: E1, to }).unwrap();
    assert_eq!(kill_checks(&ev), 1);
    assert_eq!(
        ev[4],
        Event::KillMoraleChecked {
            unit: P1,
            fallen: E1,
            roll: draw(seed, KILL_IDX),
            chance: 0,
            before: Morale::Steady,
            after: Morale::Steady,
        }
    );
    let p1 = &s.units[P1 as usize];
    assert_eq!(p1.morale, Morale::Steady);
    assert!(p1.retreating, "edge check never cancels retreat intent");
    assert!(p1.skip_turn, "positive morale never clears an EndRound skip");
    assert_eq!(p1.stats, stats);
    // Only the casualty left the queue, and the automatic turn of the retreating killer starts.
    assert_eq!(s.queue, vec![P1, P2, P3, E2, E3]);
    assert_eq!(ev[5], started(P1));
}

#[test]
fn turn_start_draws_follow_the_kill_draw_with_two_or_one_surviving_allies() {
    let seed = kill_seed(KILL_IDX, true);
    // P2 is Fleeing away from edges and enemies, so it self-rallies at its turn start.
    let mut s = reaction_kill(P1, E1, hex(0, 1), seed);
    s.units[P2 as usize].morale = Morale::Fleeing;
    let ev = s.apply(Command::Move { unit: P1, to: hex(0, 1) }).unwrap();
    let roll = draw(seed, KILL_IDX + 1);
    assert_eq!(
        ev[4..],
        [
            kill_check(seed, KILL_IDX, E1, P1, 50, Morale::Steady),
            started(P2),
            Event::SelfRallyChecked {
                unit: P2,
                roll,
                chance: 50,
                before: Morale::Fleeing,
                after: if roll < 50 { Morale::Breaking } else { Morale::Fleeing }
            },
        ]
    );
    assert_eq!(s.rng, rng_after(seed, 6));

    // One surviving ally: the kill draw moves up one place, the turn-start draw follows it.
    let seed = kill_seed(3, true);
    let mut s = reaction_kill(P1, E1, hex(0, 1), seed);
    remove(&mut s, &[P3]);
    s.units[P2 as usize].morale = Morale::Fleeing;
    let ev = s.apply(Command::Move { unit: P1, to: hex(0, 1) }).unwrap();
    assert_eq!(ev[..4], [
        attacked(E1, P1, seed),
        Event::Died { unit: P1 },
        ally_check(seed, 0, P2, P1, 50, Morale::Fleeing),
        kill_check(seed, 3, E1, P1, 50, Morale::Steady),
    ]);
    assert_eq!(ev[4], started(P2));
    assert!(matches!(ev[5], Event::SelfRallyChecked { unit: P2, roll, .. } if roll == draw(seed, 4)), "{ev:?}");
    assert_eq!(s.rng, rng_after(seed, 5));
}

#[test]
fn zero_surviving_victim_side_allies_leave_one_kill_draw_and_the_kill_decides_the_battle() {
    let seed = kill_seed(2, true);
    // Ordinary final kills, both sides; the dead or escaped allies are not checked.
    for escape in [false, true] {
        for (killer, victim, others) in [(P1, E1, [E2, E3]), (E1, P1, [P2, P3])] {
            let mut s = six(killer);
            for id in others {
                if escape {
                    s.units[id as usize].fled = true;
                    s.queue.retain(|&q| q != id);
                } else {
                    remove(&mut s, &[id]);
                }
            }
            arm(&mut s, killer, victim, seed);
            let ev = s.apply(Command::Attack { attacker: killer, target: victim }).unwrap();
            let victory = killer == P1;
            let outcome = if victory { Outcome::Victory } else if escape { Outcome::Retreat } else { Outcome::Defeat };
            let reason = if escape { EndReason::Escape } else { EndReason::Elimination };
            assert_eq!(
                ev,
                vec![
                    attacked(killer, victim, seed),
                    Event::Died { unit: victim },
                    kill_check(seed, 2, killer, victim, 50, Morale::Steady),
                    Event::BattleEnded { outcome, reason },
                ],
                "the final kill is checked before BattleEnded"
            );
            assert_eq!(s.rng, rng_after(seed, 3));
            assert_eq!(s.units[killer as usize].morale, Morale::Confident);
            assert_eq!(s.outcome(), Some(outcome));
        }
    }
    // Final opposing unit killed by a reaction: no turn starts after BattleEnded.
    for (mover, killer, to, victory) in [(E1, P1, hex(3, 0), true), (P1, E1, hex(0, 1), false)] {
        let mut s = reaction_kill(mover, killer, to, seed);
        let others: Vec<u32> = (0..6).filter(|&id| id != mover && (id < 3) == (mover < 3)).collect();
        remove(&mut s, &others);
        let ev = s.apply(Command::Move { unit: mover, to }).unwrap();
        let outcome = if victory { Outcome::Victory } else { Outcome::Defeat };
        assert_eq!(
            ev,
            vec![
                attacked(killer, mover, seed),
                Event::Died { unit: mover },
                kill_check(seed, 2, killer, mover, 50, Morale::Steady),
                Event::BattleEnded { outcome, reason: EndReason::Elimination },
            ]
        );
        assert_eq!(s.rng, rng_after(seed, 3));
        assert_eq!(s.units[killer as usize].morale, Morale::Confident);
    }
}

// --- Later chances ------------------------------------------------------------------

#[test]
fn the_triggering_chance_is_kept_and_later_chances_use_the_updated_morale() {
    // P1 (skill 60) kills E1, then attacks E2 standing next to it. E2 and E3 hold their ally
    // checks (Resolve 100), so only P1's own morale can change a chance.
    let build = |seed: u64| {
        let mut s = six(P1);
        arm(&mut s, P1, E1, seed);
        s.units[P1 as usize].stats.melee_skill = 60;
        s.units[E2 as usize].pos = hex(2, -1);
        for id in [E2, E3] {
            s.units[id as usize].stats.resolve = 100;
        }
        assert_valid_fixture(&s);
        assert_eq!(s.legal_targets(P1), vec![E1, E2]);
        s
    };
    for pass in [true, false] {
        let seed = seed_where(|s| {
            let chance = build(s).hit_chance(P1, E1);
            draw(s, 0) < chance && (draw(s, KILL_IDX) < 50) == pass
        });
        let mut s = build(seed);
        let stats = s.units[P1 as usize].stats.clone();
        let first_chance = s.hit_chance(P1, E1);
        let preview_before = s.hit_chance(P1, E2);
        assert_eq!((first_chance, preview_before), (55, 55));
        let ev = s.apply(Command::Attack { attacker: P1, target: E1 }).unwrap();
        assert!(matches!(ev[0], Event::Attacked { chance, .. } if chance == first_chance), "{ev:?}");
        assert_eq!(kill_checks(&ev), 1);
        assert!(s.units[E2 as usize].morale == Morale::Steady && s.units[E3 as usize].morale == Morale::Steady);
        let preview_after = s.hit_chance(P1, E2);
        let expected = if pass { 61 } else { 55 }; // 60 * 110% - 5 defense, or unchanged
        assert_eq!(preview_after, expected);
        assert_eq!(s.units[P1 as usize].stats, stats, "base stats stay as they were");
        // The next attack uses the same updated chance.
        let ev = s.apply(Command::Attack { attacker: P1, target: E2 }).unwrap();
        assert!(matches!(ev[0], Event::Attacked { attacker: P1, target: E2, chance, .. } if chance == expected), "{ev:?}");
        assert_eq!(s.units[P2 as usize].morale, Morale::Steady);
        assert_eq!(s.units[P3 as usize].morale, Morale::Steady);
    }
}

// --- Non-triggers -------------------------------------------------------------------

#[test]
fn misses_nonlethal_and_armour_only_hits_add_no_kill_check_or_draw() {
    // Miss: defense 1000 keeps the chance at its 5% floor.
    let seed = seed_where(|s| draw(s, 0) >= 5);
    let mut s = six(P1);
    arm(&mut s, P1, E1, seed);
    s.units[E1 as usize].stats.defense = 1000;
    let ev = s.apply(Command::Attack { attacker: P1, target: E1 }).unwrap();
    assert!(matches!(ev[..], [Event::Attacked { hit: false, .. }]), "{ev:?}");
    assert_eq!(s.rng, rng_after(seed, 1));
    assert_eq!(s.units[P1 as usize].morale, Morale::Steady);

    let seed = kill_seed(KILL_IDX, true);
    // Armour-only hit: 5 damage fully absorbed, so a 1 HP target loses nothing.
    let mut s = six(P1);
    arm(&mut s, P1, E1, seed);
    s.units[P1 as usize].stats.dmg_min = 5;
    s.units[P1 as usize].stats.dmg_max = 5;
    s.units[E1 as usize].stats.armour = 100;
    s.units[E1 as usize].armour = 100;
    assert_eq!(soak(100, 5), (5, 0));
    let ev = s.apply(Command::Attack { attacker: P1, target: E1 }).unwrap();
    assert!(matches!(ev[..], [Event::Attacked { hit: true, hp_loss: 0, absorbed: 5, .. }]), "{ev:?}");
    assert_eq!(s.rng, rng_after(seed, 2));
    assert!(s.units[E1 as usize].alive);

    // Surviving heavy hit: only the existing heavy-hit check, no kill check.
    let mut s = six(P1);
    arm(&mut s, P1, E1, seed);
    s.units[P1 as usize].stats.dmg_min = 20;
    s.units[P1 as usize].stats.dmg_max = 20;
    s.units[E1 as usize].stats.hp = 60;
    s.units[E1 as usize].hp = 60;
    let ev = s.apply(Command::Attack { attacker: P1, target: E1 }).unwrap();
    assert!(matches!(ev[..], [Event::Attacked { .. }, Event::MoraleChecked { unit: E1, .. }]), "{ev:?}");
    assert_eq!(s.rng, rng_after(seed, 3));
    assert_eq!(s.units[P1 as usize].morale, Morale::Steady);
}

#[test]
fn escapes_add_no_kill_check() {
    let mut s = six(P1);
    s.units[P1 as usize].pos = hex(-4, 2);
    s.units[P1 as usize].morale = Morale::Fleeing;
    assert_valid_fixture(&s);
    let rng = s.rng.clone();
    let ev = s.apply(Command::Flee { unit: P1 }).unwrap();
    assert_eq!(ev[0], Event::Fled { unit: P1 });
    assert_eq!(kill_checks(&ev), 0);
    assert_eq!(s.rng, rng, "an escape consumes no draw");
}

#[test]
fn rejected_commands_add_no_kill_check_and_preserve_state_and_rng() {
    let seed = kill_seed(KILL_IDX, true);
    let base = || {
        let mut s = six(P1);
        arm(&mut s, P1, E1, seed);
        s
    };
    let mut cases: Vec<(BattleState, Command, RuleError)> = Vec::new();
    cases.push((base(), Command::Attack { attacker: P1, target: P2 }, RuleError::InvalidTarget));
    cases.push((base(), Command::Attack { attacker: P1, target: E2 }, RuleError::NotAdjacent));
    cases.push((base(), Command::Attack { attacker: E1, target: P1 }, RuleError::NotActiveUnit));
    let mut s = base();
    s.units[P1 as usize].ap = 3;
    cases.push((s, Command::Attack { attacker: P1, target: E1 }, RuleError::NotEnoughAp));
    let mut s = base();
    s.units[P1 as usize].fatigue = 95;
    cases.push((s, Command::Attack { attacker: P1, target: E1 }, RuleError::TooFatigued));
    let mut s = base();
    s.units[P1 as usize].morale = Morale::Fleeing;
    cases.push((s, Command::Attack { attacker: P1, target: E1 }, RuleError::FleeingUnit));
    let mut s = base();
    s.units[P1 as usize].retreating = true;
    cases.push((s, Command::Attack { attacker: P1, target: E1 }, RuleError::RetreatingUnit));
    let mut s = base();
    s.outcome = Some(Outcome::Victory);
    cases.push((s, Command::Attack { attacker: P1, target: E1 }, RuleError::BattleOver));
    for (mut s, cmd, err) in cases {
        let before = snapshot(&s);
        assert_eq!(s.apply(cmd), Err(err), "{cmd:?}");
        assert_eq!(snapshot(&s), before, "{cmd:?}");
    }
}
