//! Resolve-based morale checks after a surviving 15+ HP melee hit
//! (partial UC-COMBAT-018, APPROXIMATION). Seeds are found by mirroring the
//! SplitMix64 stream, so every fixture states the draws it relies on.

use bb_rules::hex::hex;
use bb_rules::rules::{MORALE_CHECK_HP_LOSS_THRESHOLD, soak};
use bb_rules::scenario::DEFAULT_RESOLVE;
use bb_rules::*;

const ALL: [Morale; 4] = [Morale::Confident, Morale::Steady, Morale::Wavering, Morale::Breaking];

/// The first `n` raw draws of a fresh stream. A fixture attack consumes the hit
/// roll, the damage draw and (when it qualifies) the morale draw, in that order.
fn raw_draws(seed: u64, n: usize) -> Vec<u64> {
    let mut rng = Rng::new(seed);
    (0..n).map(|_| rng.next_u64()).collect()
}

/// RNG state after `n` draws from `seed` (SplitMix64 adds a constant per draw).
fn rng_after(seed: u64, n: usize) -> u64 {
    let mut rng = Rng::new(seed);
    for _ in 0..n {
        rng.next_u64();
    }
    rng.state
}

/// `attacker` (id) attacks `target` (id) from adjacent hexes with fixed damage.
/// The defender gets `armour`/`hp` (also as its Stats maxima, so current values
/// never exceed them); Resolve and morale are left to the caller.
fn duel(seed: u64, attacker: u32, target: u32, dmg: u32, armour: u32, hp: u32) -> BattleState {
    let mut state = BattleState::new(seed);
    let (apos, tpos) = if state.units[attacker as usize].side == Side::Player {
        (hex(1, 0), hex(2, 0))
    } else {
        (hex(2, 0), hex(1, 0))
    };
    state.units[attacker as usize].pos = apos;
    state.units[target as usize].pos = tpos;
    state.queue.retain(|&id| id != attacker);
    state.queue.insert(0, attacker);
    let a = &mut state.units[attacker as usize];
    a.ap = 9;
    a.stats.dmg_min = dmg;
    a.stats.dmg_max = dmg;
    let t = &mut state.units[target as usize];
    t.stats.armour = armour;
    t.armour = armour;
    t.stats.hp = hp;
    t.hp = hp;
    assert_valid_fixture(&state);
    state
}

/// Living units stand on distinct, in-map, passable hexes and hold at most
/// their Stats maxima.
fn assert_valid_fixture(state: &BattleState) {
    let living: Vec<&Unit> = state.units.iter().filter(|u| u.alive).collect();
    for (i, u) in living.iter().enumerate() {
        assert!(u.pos.in_map() && !u.pos.is_obstacle(), "unit {} on invalid hex {:?}", u.id, u.pos);
        assert!(u.hp <= u.stats.hp && u.armour <= u.stats.armour, "unit {} exceeds its maxima", u.id);
        assert!(living[..i].iter().all(|o| o.pos != u.pos), "unit {} shares a hex", u.id);
    }
}

/// First seed whose attack roll hits (`want_hit`) the duel's chance.
fn seed_with_roll(attacker: u32, target: u32, want_hit: bool) -> u64 {
    (0..1000)
        .find(|&seed| {
            let chance = duel(seed, attacker, target, 20, 0, 60).hit_chance(attacker, target);
            ((raw_draws(seed, 1)[0] % 100) < u64::from(chance)) == want_hit
        })
        .expect("a suitable seed exists")
}

fn attack(state: &mut BattleState, attacker: u32, target: u32) -> Vec<Event> {
    state.apply(Command::Attack { attacker, target }).unwrap()
}

fn checks(events: &[Event]) -> Vec<&Event> {
    events.iter().filter(|e| matches!(e, Event::MoraleChecked { .. })).collect()
}

#[test]
fn resolve_is_an_unmodified_base_stat_defaulting_to_the_named_constant() {
    assert_eq!(DEFAULT_RESOLVE, 50);
    assert_eq!(MORALE_CHECK_HP_LOSS_THRESHOLD, 15);
    let mut state = BattleState::new(1);
    for unit in &state.units {
        assert_eq!((unit.stats.resolve, unit.morale), (DEFAULT_RESOLVE, Morale::Steady));
    }
    for morale in ALL {
        state.units[0].morale = morale;
        assert_eq!(state.units[0].stats.resolve, DEFAULT_RESOLVE);
    }
}

#[test]
fn exactly_fifteen_hp_lost_triggers_one_check_and_fourteen_does_not() {
    for (attacker, target) in [(0, 3), (3, 0)] {
        let seed = seed_with_roll(attacker, target, true);
        let draws = raw_draws(seed, 3);

        let mut below = duel(seed, attacker, target, 14, 0, 60);
        let events = attack(&mut below, attacker, target);
        assert!(matches!(events[..], [Event::Attacked { hit: true, hp_loss: 14, .. }]));
        assert_eq!(below.rng.state, rng_after(seed, 2));
        assert_eq!(below.units[target as usize].morale, Morale::Steady);

        let mut at = duel(seed, attacker, target, 15, 0, 60);
        at.units[target as usize].stats.resolve = 0;
        let chance = at.hit_chance(attacker, target);
        let events = attack(&mut at, attacker, target);
        assert_eq!(
            events,
            vec![
                Event::Attacked { attacker, target, hit: true, roll: (draws[0] % 100) as u32, chance, dmg: 15, absorbed: 0, hp_loss: 15 },
                Event::MoraleChecked {
                    unit: target,
                    roll: (draws[2] % 100) as u32,
                    chance: 0,
                    before: Morale::Steady,
                    after: Morale::Wavering,
                },
            ]
        );
        assert_eq!(at.units[target as usize].hp, 45);
        assert_eq!(at.rng.state, rng_after(seed, 3));
    }
}

#[test]
fn armour_absorption_decides_the_actual_hp_loss_used_by_the_threshold() {
    let seed = seed_with_roll(0, 3, true);
    // armour 20, dmg 30: 10 + 30/10 = 13 HP lost (subthreshold despite 30 damage).
    // armour 20, dmg 40: 20 + 4 = 24 HP lost. armour 40, dmg 30: all absorbed, 3 HP lost.
    assert_eq!(soak(20, 30), (20, 13));
    assert_eq!(soak(20, 40), (20, 24));
    for (armour, dmg, expect_check) in [(20, 30, false), (20, 40, true), (40, 30, false), (0, 40, true)] {
        let mut state = duel(seed, 0, 3, dmg, armour, 60);
        state.units[3].stats.resolve = 0;
        let events = attack(&mut state, 0, 3);
        assert_eq!(checks(&events).len(), usize::from(expect_check), "armour {armour} dmg {dmg}");
        let drawn: usize = if expect_check { 3 } else { 2 };
        assert_eq!(state.rng.state, rng_after(seed, drawn));
    }
    // Armour damage alone: dmg 9 is fully absorbed (9 / 10 = 0 bonus), so no HP is lost.
    assert_eq!(soak(20, 9), (9, 0));
    let mut state = duel(seed, 0, 3, 9, 20, 60);
    state.units[3].stats.resolve = 0;
    let events = attack(&mut state, 0, 3);
    assert!(matches!(events[..], [Event::Attacked { hit: true, dmg: 9, absorbed: 9, hp_loss: 0, .. }]));
    assert_eq!((state.units[3].hp, state.units[3].armour), (60, 11));
    assert_eq!(state.units[3].morale, Morale::Steady);
    assert!(checks(&events).is_empty());
    assert_eq!(state.rng.state, rng_after(seed, 2));
}

#[test]
fn misses_trigger_no_check_and_consume_only_the_hit_roll() {
    for (attacker, target) in [(0, 3), (3, 0)] {
        let seed = seed_with_roll(attacker, target, false);
        let mut state = duel(seed, attacker, target, 40, 0, 60);
        state.units[target as usize].stats.resolve = 0;
        let events = attack(&mut state, attacker, target);
        assert!(matches!(events[..], [Event::Attacked { hit: false, hp_loss: 0, .. }]));
        assert_eq!(state.units[target as usize].morale, Morale::Steady);
        assert_eq!(state.rng.state, rng_after(seed, 1));
    }
}

#[test]
fn lethal_hits_give_the_dead_victim_no_check_but_survivors_take_ally_checks() {
    for (attacker, target) in [(0, 3), (3, 0)] {
        let seed = seed_with_roll(attacker, target, true);
        // Exactly lethal (hp == damage) and overkill.
        for (dmg, hp) in [(15, 15), (30, 20)] {
            let mut state = duel(seed, attacker, target, dmg, 0, hp);
            state.units[target as usize].stats.resolve = 0;
            let events = attack(&mut state, attacker, target);
            assert!(matches!(events[..2], [Event::Attacked { hit: true, .. }, Event::Died { unit }] if unit == target));
            // The victim gets no heavy-hit check; its two surviving allies each get one
            // ally-death check (ascending ids), then the killer's positive kill check, so
            // the RNG advances by 2 + 2 + 1 draws.
            assert!(checks(&events).is_empty());
            assert!(
                matches!(events[4], Event::KillMoraleChecked { unit, fallen, .. } if unit == attacker && fallen == target),
                "{events:?}"
            );
            assert_eq!(events.len(), 5);
            let allies: Vec<u32> = events[2..4]
                .iter()
                .map(|e| match *e {
                    Event::AllyDeathMoraleChecked { unit, fallen, .. } if fallen == target => unit,
                    ref e => panic!("expected an ally-death check, got {e:?}"),
                })
                .collect();
            let expected: Vec<u32> = (0..6).filter(|&id| id != target && (id < 3) == (target < 3)).collect();
            assert_eq!(allies, expected);
            assert_eq!(state.units[target as usize].morale, Morale::Steady);
            assert_eq!(state.rng.state, rng_after(seed, 5));
        }
        // Surviving with 1 HP left still checks.
        let mut state = duel(seed, attacker, target, 15, 0, 16);
        let events = attack(&mut state, attacker, target);
        assert_eq!(checks(&events).len(), 1);
    }
}

#[test]
fn resolve_maps_to_chance_with_the_roll_equals_chance_failure_boundary() {
    let seed = seed_with_roll(0, 3, true);
    let roll = (raw_draws(seed, 3)[2] % 100) as u32;
    assert!(roll > 0 && roll < 99, "fixture roll must allow neighbouring values");
    let outcome = |resolve: u32| {
        let mut state = duel(seed, 0, 3, 20, 0, 60);
        state.units[3].stats.resolve = resolve;
        match attack(&mut state, 0, 3)[1] {
            Event::MoraleChecked { roll, chance, before, after, .. } => (roll, chance, before, after),
            ref e => panic!("expected MoraleChecked, got {e:?}"),
        }
    };
    // roll == chance fails, chance == roll + 1 passes, chance == roll - 1 fails.
    assert_eq!(outcome(roll), (roll, roll, Morale::Steady, Morale::Wavering));
    assert_eq!(outcome(roll + 1), (roll, roll + 1, Morale::Steady, Morale::Steady));
    assert_eq!(outcome(roll - 1), (roll, roll - 1, Morale::Steady, Morale::Wavering));
    // Resolve 0 always fails; 100 and above cap at chance 100 and always pass.
    assert_eq!(outcome(0), (roll, 0, Morale::Steady, Morale::Wavering));
    for resolve in [100, 101, 5_000, u32::MAX] {
        assert_eq!(outcome(resolve), (roll, 100, Morale::Steady, Morale::Steady));
    }
}

#[test]
fn every_failure_transition_and_unchanged_success_and_saturated_fleeing() {
    let seed = seed_with_roll(0, 3, true);
    let expected_fail = [
        (Morale::Confident, Morale::Steady),
        (Morale::Steady, Morale::Wavering),
        (Morale::Wavering, Morale::Breaking),
        (Morale::Breaking, Morale::Fleeing),
        (Morale::Fleeing, Morale::Fleeing),
    ];
    for (before, after) in expected_fail {
        for (resolve, passes) in [(0, false), (100, true)] {
            let mut state = duel(seed, 0, 3, 20, 0, 60);
            state.units[3].morale = before;
            state.units[3].stats.resolve = resolve;
            // Fleeing defenders still consume the extra draw.
            let events = attack(&mut state, 0, 3);
            let [Event::Attacked { .. }, Event::MoraleChecked { unit: 3, before: b, after: a, .. }] = events[..] else {
                panic!("unexpected events {events:?}");
            };
            assert_eq!((b, a), (before, if passes { before } else { after }));
            assert_eq!(state.units[3].morale, a);
            assert_eq!(state.units[3].stats.resolve, resolve, "Resolve is never modified by morale");
            assert_eq!(state.rng.state, rng_after(seed, 3));
        }
    }
}

#[test]
fn repeated_failures_walk_down_to_fleeing_and_stay_there() {
    let mut state = duel(seed_with_roll(0, 3, true), 0, 3, 15, 0, 1000);
    state.units[3].stats.resolve = 0;
    state.units[3].morale = Morale::Confident;
    let mut seen = vec![Morale::Confident];
    for _ in 0..40 {
        if state.units[0].ap < 4 {
            state.units[0].ap = 9;
            state.units[0].fatigue = 0;
        }
        let events = attack(&mut state, 0, 3);
        for e in &events {
            if let Event::MoraleChecked { before, after, .. } = e {
                assert_eq!(*before, *seen.last().unwrap());
                seen.push(*after);
            }
        }
    }
    assert_eq!(&seen[..5], &[Morale::Confident, Morale::Steady, Morale::Wavering, Morale::Breaking, Morale::Fleeing]);
    assert!(seen[4..].iter().all(|&m| m == Morale::Fleeing));
    assert!(seen.len() > 6, "checks at Fleeing still happen");
}

#[test]
fn opportunity_attacks_run_the_same_check_in_event_order_with_exact_rng_use() {
    // P1 (id 0) leaves hex (2,0), controlled by E1 (id 3, at (3,0)) and E2 (id 4, at (3,-1)).
    let build = |seed: u64| {
        let mut state = BattleState::new(seed);
        state.units[0].pos = hex(2, 0);
        state.units[0].armour = 0;
        state.units[0].stats.hp = 200;
        state.units[0].hp = 200;
        state.units[3].pos = hex(3, 0);
        state.units[4].pos = hex(3, -1);
        assert_valid_fixture(&state);
        for id in [3, 4] {
            state.units[id].stats.dmg_min = 20;
            state.units[id].stats.dmg_max = 20;
        }
        state
    };
    // Both reactions must hit: with seed-mirrored draws E1 uses d0,d1,d2 and E2 d3,d4,d5.
    let seed = (0..100_000u64)
        .find(|&seed| {
            let s = build(seed);
            let d = raw_draws(seed, 6);
            (d[0] % 100) < u64::from(s.hit_chance(3, 0)) && (d[3] % 100) < 46
        })
        .unwrap();
    let mut state = build(seed);
    state.units[0].stats.resolve = 0;
    let e1_chance = state.hit_chance(3, 0);
    // 50 skill - 10 defense + 5 surround (E1 and E2 both adjacent to P1).
    assert_eq!(e1_chance, 45);
    let events = state.apply(Command::Move { unit: 0, to: hex(1, 0) }).unwrap();
    let d = raw_draws(seed, 6);
    assert!(matches!(
        events[..],
        [
            Event::Attacked { attacker: 3, target: 0, hit: true, chance: 45, hp_loss: 20, .. },
            Event::MoraleChecked { unit: 0, before: Morale::Steady, after: Morale::Wavering, chance: 0, .. },
            Event::Attacked { attacker: 4, target: 0, hit: true, .. },
            Event::MoraleChecked { unit: 0, before: Morale::Wavering, after: Morale::Breaking, .. },
            ..
        ]
    ), "{events:?}");
    let Event::MoraleChecked { roll, .. } = events[1] else { unreachable!() };
    assert_eq!(u64::from(roll), d[2] % 100);
    // The second reaction's chance already uses Wavering defense floor(10 * 0.9) = 9.
    let Event::Attacked { chance: e2_chance, .. } = events[2] else { unreachable!() };
    assert_eq!(e2_chance, 46);
    assert_eq!(state.units[0].morale, Morale::Breaking);
    let after_reactions = rng_after(seed, 6);
    assert_eq!(state.rng.state, after_reactions);
}

#[test]
fn morale_change_changes_the_next_attack_but_not_the_triggering_chance() {
    let seed = seed_with_roll(0, 3, true);
    let mut state = duel(seed, 0, 3, 20, 0, 60);
    state.units[3].stats.resolve = 0;
    let before = state.hit_chance(0, 3);
    let events = attack(&mut state, 0, 3);
    let Event::Attacked { chance, .. } = events[0] else { panic!() };
    assert_eq!(chance, before);
    assert_eq!(state.units[3].morale, Morale::Wavering);
    // Wavering defense floor(5 * 0.9) = 4 is used from now on.
    assert_eq!(state.hit_chance(0, 3), before + 1);
    state.units[3].morale = Morale::Steady;
    assert_eq!(state.hit_chance(0, 3), before);
}

#[test]
fn rejected_commands_and_previews_consume_no_rng_and_change_nothing() {
    let mut state = duel(seed_with_roll(0, 3, true), 0, 3, 40, 0, 60);
    state.units[3].stats.resolve = 0;
    let (hash, rng) = (state.state_hash(), state.rng.clone());
    let rejected = [
        (Command::Attack { attacker: 3, target: 0 }, RuleError::NotActiveUnit),
        (Command::Attack { attacker: 0, target: 1 }, RuleError::InvalidTarget),
        (Command::Attack { attacker: 0, target: 0 }, RuleError::InvalidTarget),
        (Command::Attack { attacker: 0, target: 5 }, RuleError::NotAdjacent),
    ];
    for (cmd, err) in rejected {
        assert_eq!(state.apply(cmd), Err(err));
        assert_eq!((state.state_hash(), &state.rng), (hash, &rng));
    }
    state.units[0].ap = 3;
    assert_eq!(state.apply(Command::Attack { attacker: 0, target: 3 }), Err(RuleError::NotEnoughAp));
    state.units[0].ap = 9;
    state.units[0].fatigue = 95;
    assert_eq!(state.apply(Command::Attack { attacker: 0, target: 3 }), Err(RuleError::TooFatigued));
    state.units[0].fatigue = 0;
    let hash = state.state_hash();
    let _ = state.hit_chance(0, 3);
    let _ = state.preview_move(0, hex(0, 0));
    let _ = state.enemy_controllers_at(0, hex(1, 0));
    let _ = state.reachable(0);
    let _ = state.legal_targets(0);
    assert_eq!((state.state_hash(), &state.rng), (hash, &rng));
    assert!(state.units.iter().all(|u| u.morale == Morale::Steady));
}

#[test]
fn replay_is_deterministic_and_ai_battles_include_morale_checks() {
    let run = |seed: u64| {
        let mut state = BattleState::new(seed);
        let mut log = Vec::new();
        for _ in 0..10_000 {
            let Some(&cmd) = ai::plan_turn(&state).first() else { break };
            log.extend(state.apply(cmd).unwrap());
        }
        (log, state.state_hash())
    };
    let mut checked = 0;
    for seed in 1..=20 {
        let (log, hash) = run(seed);
        assert_eq!(run(seed), (log.clone(), hash));
        // Every check directly follows the Attacked event that caused it.
        for (i, e) in log.iter().enumerate() {
            if let Event::MoraleChecked { unit, .. } = e {
                checked += 1;
                assert!(matches!(log[i - 1], Event::Attacked { target, hit: true, hp_loss, .. } if target == *unit && hp_loss >= 15));
            }
        }
    }
    assert!(checked > 0, "the default scenario must reach heavy surviving hits");
}

#[test]
fn state_hash_distinguishes_resolve_values() {
    let a = BattleState::new(1);
    for id in 0..6 {
        let mut b = a.clone();
        b.units[id].stats.resolve += 1;
        assert_ne!(a.state_hash(), b.state_hash(), "unit {id}");
    }
    let mut b = a.clone();
    b.units[0].stats.resolve = 0;
    let mut c = a.clone();
    c.units[1].stats.resolve = 0;
    assert_ne!(b.state_hash(), c.state_hash());
}
