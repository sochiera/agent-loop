//! T58 two-hex melee reach foundation (partial UC-COMBAT-051). The two-hex reach
//! and the adjacent ZoC are catalog-stated; the `MeleeReach` enum, hash bit,
//! generic cost reuse, absent obstruction checks and modifier composition are
//! APPROXIMATIONs. Equipment, polearm penalties and mastery remain deferred.

use bb_rules::ai::plan_turn;
use bb_rules::hex::{hex, map_hexes};
use bb_rules::rules::{ATTACK_AP, ATTACK_FATIGUE};
use bb_rules::scenario::{TerrainPreset, battle};
use bb_rules::*;

const P1: u32 = 0;
const P2: u32 = 1;
const P3: u32 = 2;
const E1: u32 = 3;
const E2: u32 = 4;
const E3: u32 = 5;

/// A non-edge hex next to `at(0)`, used as the departure destination.
const AWAY: Hex = hex(-1, -1);

const PARK: [Hex; 6] = [
    hex(-4, 2),
    hex(-4, 3),
    hex(-4, 4),
    hex(4, -4),
    hex(4, -3),
    hex(4, -2),
];

/// Row r = -2: the actor stands at its west end, `at(d)` is `d` hexes east.
fn at(distance: i32) -> Hex {
    hex(-1 + distance, -2)
}

fn off(from: Hex, dq: i32, dr: i32) -> Hex {
    hex(from.q + dq, from.r + dr)
}

/// All units parked, `actor` active at `at(0)` with a fresh turn, and `target`
/// at `at(distance)`. Melee skill/defense/resolve are zeroed so chances sit at
/// the clamp unless a test sets them.
fn board(actor: u32, target: u32, distance: i32, reach: MeleeReach) -> BattleState {
    let mut state = BattleState::new(1);
    for (unit, spot) in state.units.iter_mut().zip(PARK) {
        unit.pos = spot;
        unit.stats.melee_skill = 0;
        unit.stats.defense = 0;
        unit.stats.resolve = 0;
        unit.stats.dmg_min = 3;
        unit.stats.dmg_max = 9;
    }
    state.units[actor as usize].pos = at(0);
    state.units[target as usize].pos = at(distance);
    state.units[actor as usize].melee_reach = reach;
    state.queue.retain(|&id| id != actor);
    state.queue.insert(0, actor);
    let unit = &mut state.units[actor as usize];
    unit.ap = unit.stats.ap_max;
    unit.turn_started = true;
    assert_fixture_valid(&state);
    state
}

fn assert_fixture_valid(state: &BattleState) {
    let spots: Vec<_> = state.units.iter().map(|u| u.pos).collect();
    for (i, spot) in spots.iter().enumerate() {
        assert!(map_hexes().contains(spot) && !spot.is_obstacle(), "{spot:?}");
        assert!(!spots[..i].contains(spot), "duplicate {spot:?}");
    }
    for u in &state.units {
        assert!(u.hp <= u.stats.hp && u.armour <= u.stats.armour);
        assert!(u.ap <= u.stats.ap_max && u.fatigue <= u.stats.fatigue_max);
    }
}

type Snapshot = (u64, Rng, Vec<Unit>, Vec<u32>, u32, Option<Outcome>);

fn snapshot(s: &BattleState) -> Snapshot {
    (s.state_hash(), s.rng.clone(), s.units.clone(), s.queue.clone(), s.round, s.outcome)
}

fn attack(attacker: u32, target: u32) -> Command {
    Command::Attack { attacker, target }
}

fn attacked_chance(events: &[Event]) -> u32 {
    events
        .iter()
        .find_map(|e| match e {
            Event::Attacked { chance, .. } => Some(*chance),
            _ => None,
        })
        .expect("Attacked event")
}

fn assert_error_everywhere(s: &mut BattleState, a: u32, t: u32, expected: RuleError) {
    let before = snapshot(s);
    assert_eq!(s.preview_attack(a, t), Err(expected));
    assert_eq!(snapshot(s), before);
    assert_eq!(s.apply(attack(a, t)), Err(expected));
    assert_eq!(snapshot(s), before, "a rejected Attack changes nothing");
}

#[test]
fn defaults_and_geometry() {
    assert_eq!(MeleeReach::default(), MeleeReach::Adjacent);
    for u in BattleState::new(1).units.iter() {
        assert_eq!(u.melee_reach, MeleeReach::Adjacent);
    }
    let mut unit = Unit::new(0, "U", Side::Player, BattleState::new(1).units[0].stats.clone(), at(0));
    assert_eq!(unit.melee_reach, MeleeReach::Adjacent);
    let copy = unit.melee_reach; // Copy
    assert_eq!(copy, MeleeReach::Adjacent);

    for (reach, accepted) in [(MeleeReach::Adjacent, [false, true, false, false]), (MeleeReach::TwoHex, [false, true, true, false])] {
        unit.melee_reach = reach;
        for (distance, expected) in accepted.into_iter().enumerate() {
            let to = at(distance as i32);
            assert_eq!(reach.reaches(unit.pos, to), expected, "{reach:?} reaches at {distance}");
            assert_eq!(unit.melee_reaches(to), expected, "{reach:?} unit query at {distance}");
        }
    }
    // Every hex on a ring of radius 1 and 2 in every direction, not just one row.
    unit.melee_reach = MeleeReach::TwoHex;
    for h in map_hexes() {
        let d = unit.pos.distance(h);
        let expected = (1..=2).contains(&d);
        assert_eq!(MeleeReach::TwoHex.reaches(unit.pos, h), expected, "geometry {h:?}");
        assert_eq!(unit.melee_reaches(h), expected, "unit query {h:?}");
    }
    // Extreme coordinates are plain geometry, never an overflow panic.
    let extremes = [hex(i32::MAX, i32::MAX), hex(i32::MIN, i32::MIN), hex(i32::MAX, i32::MIN)];
    for reach in [MeleeReach::Adjacent, MeleeReach::TwoHex] {
        unit.melee_reach = reach;
        for extreme in extremes {
            assert!(!reach.reaches(unit.pos, extreme));
            assert!(!unit.melee_reaches(extreme));
        }
    }
    unit.pos = hex(i32::MIN, i32::MAX);
    for reach in [MeleeReach::Adjacent, MeleeReach::TwoHex] {
        unit.melee_reach = reach;
        let extreme = hex(i32::MAX, i32::MIN);
        assert!(!reach.reaches(unit.pos, extreme));
        assert!(!unit.melee_reaches(extreme));
    }
}

#[test]
fn both_factions_default_and_two_hex_query_preview_and_execution_agree() {
    for (actor, target) in [(P1, E1), (E1, P1)] {
        for reach in [MeleeReach::Adjacent, MeleeReach::TwoHex] {
            for distance in 1..=3 {
                let mut s = board(actor, target, distance, reach);
                let reaches = distance == 1 || (distance == 2 && reach == MeleeReach::TwoHex);
                assert_eq!(s.legal_targets(actor), if reaches { vec![target] } else { vec![] });
                let preview = s.preview_attack(actor, target);
                if reaches {
                    let preview = preview.unwrap();
                    assert_eq!((preview.ap_cost, preview.fatigue_cost), (ATTACK_AP, ATTACK_FATIGUE));
                    assert_eq!(preview.hit_chance, s.hit_chance(actor, target));
                    let ev = s.apply(attack(actor, target)).unwrap();
                    assert_eq!(attacked_chance(&ev), preview.hit_chance);
                    let u = &s.units[actor as usize];
                    assert_eq!((u.ap, u.fatigue), (u.stats.ap_max - ATTACK_AP, ATTACK_FATIGUE));
                } else {
                    assert_error_everywhere(&mut s, actor, target, RuleError::NotAdjacent);
                }
            }
        }
    }
}

#[test]
fn legal_targets_keep_id_order_and_skip_unreachable_or_unusable_units() {
    let mut s = board(P1, E1, 2, MeleeReach::TwoHex);
    s.units[E2 as usize].pos = off(at(0), 1, -1); // adjacent to the actor
    s.units[E3 as usize].pos = at(3);
    s.units[P2 as usize].pos = at(1); // allied, in reach
    assert_fixture_valid(&s);
    assert_eq!(s.legal_targets(P1), vec![E1, E2]);
    s.units[E2 as usize].alive = false;
    assert_eq!(s.legal_targets(P1), vec![E1]);
    s.units[E1 as usize].fled = true;
    assert_eq!(s.legal_targets(P1), Vec::<u32>::new());
    for dead_or_gone in [E1, E2] {
        assert_error_everywhere(&mut s, P1, dead_or_gone, RuleError::InvalidTarget);
    }
    assert_error_everywhere(&mut s, P1, P2, RuleError::InvalidTarget);
    assert_error_everywhere(&mut s, P1, P1, RuleError::InvalidTarget);
    // Absent (out-of-range id) target is the existing InvalidTarget, not a panic.
    let before = snapshot(&s);
    assert_eq!(s.preview_attack(P1, 99), Err(RuleError::InvalidTarget));
    assert_eq!(s.apply(attack(P1, 99)), Err(RuleError::InvalidTarget));
    assert_eq!(snapshot(&s), before);
}

#[test]
fn error_precedence_and_exact_budgets_hold_at_distance_two() {
    for (actor, target, ally) in [(P1, E1, P2), (E1, P1, E2)] {
        // Target validity before range: an allied unit at distance 3 is InvalidTarget.
        let mut s = board(actor, target, 3, MeleeReach::TwoHex);
        s.units[ally as usize].pos = at(4);
        s.units[actor as usize].ap = 0;
        s.units[actor as usize].fatigue = s.units[actor as usize].stats.fatigue_max;
        assert_error_everywhere(&mut s, actor, ally, RuleError::InvalidTarget);
        // Range before budget: unreachable + no AP is NotAdjacent.
        assert_error_everywhere(&mut s, actor, target, RuleError::NotAdjacent);
        // In reach: AP before fatigue.
        s.units[target as usize].pos = at(2);
        assert_error_everywhere(&mut s, actor, target, RuleError::NotEnoughAp);
        s.units[actor as usize].ap = ATTACK_AP - 1;
        s.units[actor as usize].fatigue = 0;
        assert_error_everywhere(&mut s, actor, target, RuleError::NotEnoughAp);
        s.units[actor as usize].ap = ATTACK_AP;
        let max = s.units[actor as usize].stats.fatigue_max;
        s.units[actor as usize].fatigue = max - ATTACK_FATIGUE + 1;
        assert_error_everywhere(&mut s, actor, target, RuleError::TooFatigued);
        // Checked overflow with the largest representable maximum and fatigue.
        s.units[actor as usize].stats.fatigue_max = u32::MAX;
        s.units[actor as usize].fatigue = u32::MAX;
        assert_error_everywhere(&mut s, actor, target, RuleError::TooFatigued);
        // Exact budget is accepted and spent in full.
        s.units[actor as usize].stats.fatigue_max = max;
        s.units[actor as usize].fatigue = max - ATTACK_FATIGUE;
        assert!(s.preview_attack(actor, target).is_ok());
        s.apply(attack(actor, target)).unwrap();
        let u = &s.units[actor as usize];
        assert_eq!((u.ap, u.fatigue), (0, max));
    }
}

#[test]
fn attacks_pass_over_an_intervening_ally_and_an_intervening_obstacle() {
    // Pike behind a shield: P1 reaches E1 over the allied P2 standing between.
    let mut s = board(P1, E1, 2, MeleeReach::TwoHex);
    s.units[P2 as usize].pos = at(1);
    assert_fixture_valid(&s);
    assert_eq!(s.legal_targets(P1), vec![E1]);
    let chance = s.preview_attack(P1, E1).unwrap().hit_chance;
    let ev = s.apply(attack(P1, E1)).unwrap();
    assert_eq!(attacked_chance(&ev), chance);
    assert!(ev.iter().any(|e| matches!(e, Event::Attacked { attacker: P1, target: E1, .. })));

    // APPROXIMATION: no obstruction check, so the obstacle at (1,-1) does not block.
    let mut s = board(E1, P1, 2, MeleeReach::TwoHex);
    s.units[E1 as usize].pos = hex(0, -1);
    s.units[P1 as usize].pos = hex(2, -1);
    assert_eq!(s.units[E1 as usize].pos.distance(s.units[P1 as usize].pos), 2);
    assert!(hex(1, -1).is_obstacle());
    assert_fixture_valid(&s);
    assert_eq!(s.legal_targets(E1), vec![P1]);
    s.apply(attack(E1, P1)).unwrap();
    // An adjacent attack gets no new penalty: identical chance to a plain Adjacent unit.
    let two = board(P1, E1, 1, MeleeReach::TwoHex);
    let one = board(P1, E1, 1, MeleeReach::Adjacent);
    assert_eq!(two.hit_chance(P1, E1), one.hit_chance(P1, E1));
    assert_eq!(two.preview_attack(P1, E1), one.preview_attack(P1, E1));
}

#[test]
fn hit_chance_composes_elevation_fast_adaptation_and_surround_at_distance_two() {
    for (actor, target, allies) in [(P1, E1, [P2, P3]), (E1, P1, [E2, E3])] {
        let mut s = board(actor, target, 2, MeleeReach::TwoHex);
        s.units[actor as usize].stats.melee_skill = 60;
        s.units[target as usize].stats.defense = 20;
        let base: u32 = 40;
        assert_eq!(s.hit_chance(actor, target), base as u32);

        // Surround: the two-hex attacker is not itself adjacent. Allies on two of
        // the defender's neighbours count; the first is free.
        let neighbours = [off(at(2), -1, 0), off(at(2), 0, -1)];
        for (count, expected) in [(0, base), (1, base), (2, base + 5)] {
            for (index, ally) in allies.into_iter().enumerate() {
                s.units[ally as usize].pos = if index < count { neighbours[index] } else { PARK[ally as usize] };
            }
            assert_fixture_valid(&s);
            assert_eq!(s.hit_chance(actor, target), expected, "{count} adjacent allies");
            let preview = s.preview_attack(actor, target).unwrap();
            let mut run = s.clone();
            let ev = run.apply(attack(actor, target)).unwrap();
            assert_eq!(preview.hit_chance, expected);
            assert_eq!(attacked_chance(&ev), expected);
        }

        // Elevation and Fast Adaptation apply at distance two.
        for (a_height, t_height, delta) in [(2u8, 0u8, 10i32), (0, 1, -10), (0, 3, -30), (1, 1, 0)] {
            let mut e = board(actor, target, 2, MeleeReach::TwoHex);
            e.units[actor as usize].stats.melee_skill = 60;
            e.units[target as usize].stats.defense = 20;
            e.elevations.set(at(0), a_height).unwrap();
            e.elevations.set(at(2), t_height).unwrap();
            assert_eq!(e.hit_chance(actor, target), (base as i32 + delta) as u32);
            assert_eq!(attacked_chance(&e.clone().apply(attack(actor, target)).unwrap()), (base as i32 + delta) as u32);
        }
        let mut f = board(actor, target, 2, MeleeReach::TwoHex);
        f.units[actor as usize].stats.melee_skill = 60;
        f.units[target as usize].stats.defense = 20;
        f.units[actor as usize].has_fast_adaptation = true;
        f.units[actor as usize].fast_adaptation_stacks = 3;
        f.elevations.set(at(0), 1).unwrap();
        assert_eq!(f.hit_chance(actor, target), 40 + 30 + 10);
        assert_eq!(f.preview_attack(actor, target).unwrap().hit_chance, 80);
        assert_eq!(attacked_chance(&f.clone().apply(attack(actor, target)).unwrap()), 80);

        // Distance one with one other adjacent ally: attacker and ally count, so +5.
        let mut near = board(actor, target, 1, MeleeReach::TwoHex);
        near.units[actor as usize].stats.melee_skill = 60;
        near.units[target as usize].stats.defense = 20;
        assert_eq!(near.hit_chance(actor, target), base);
        near.units[allies[0] as usize].pos = off(at(1), 0, -1);
        assert_eq!(near.hit_chance(actor, target), base + 5);

        // Out of reach: no engaged modifiers at all, just the clamped skill gap.
        let mut far = board(actor, target, 3, MeleeReach::TwoHex);
        far.units[actor as usize].stats.melee_skill = 60;
        far.units[target as usize].stats.defense = 20;
        far.units[actor as usize].has_fast_adaptation = true;
        far.units[actor as usize].fast_adaptation_stacks = 3;
        far.elevations.set(at(0), 3).unwrap();
        assert_eq!(far.hit_chance(actor, target), base);
    }
}

#[test]
fn reach_never_extends_zones_of_control_or_opportunity_attacks() {
    for (mover, controller) in [(P1, E1), (E1, P1)] {
        // Controller two hexes away: no controllers, no threat, no reaction.
        let mut s = board(mover, controller, 2, MeleeReach::Adjacent);
        s.units[controller as usize].melee_reach = MeleeReach::TwoHex;
        assert!(s.enemy_controllers_at(mover, at(0)).is_empty());
        let preview = s.preview_move(mover, AWAY).unwrap();
        assert_eq!(preview.first_threat, None);
        let events = s.apply(Command::Move { unit: mover, to: AWAY }).unwrap();
        assert!(!events.iter().any(|e| matches!(e, Event::Attacked { .. })));
        assert_eq!(s.units[mover as usize].pos, AWAY);

        // Controller at distance one: the departure reaction is unchanged by reach.
        let run = |reach| {
            let mut s = board(mover, controller, 1, MeleeReach::Adjacent);
            s.units[controller as usize].melee_reach = reach;
            s.units[controller as usize].stats.dmg_min = 1;
            s.units[controller as usize].stats.dmg_max = 1;
            assert_eq!(s.enemy_controllers_at(mover, at(0)), vec![controller]);
            let threat = s.preview_move(mover, AWAY).unwrap().first_threat.unwrap();
            assert_eq!((threat.departure, threat.controllers.clone()), (at(0), vec![controller]));
            let events = s.apply(Command::Move { unit: mover, to: AWAY }).unwrap();
            (events, snapshot(&s))
        };
        let (adjacent_events, mut adjacent_state) = run(MeleeReach::Adjacent);
        let (two_events, two_state) = run(MeleeReach::TwoHex);
        assert!(adjacent_events.iter().any(|e| matches!(e, Event::Attacked { attacker, .. } if *attacker == controller)));
        assert_eq!(adjacent_events, two_events);
        // Only the controller's reach bit (hence the hash) differs.
        assert_ne!(adjacent_state.0, two_state.0);
        adjacent_state.2[controller as usize].melee_reach = MeleeReach::TwoHex;
        adjacent_state.0 = two_state.0;
        assert_eq!(adjacent_state, two_state);
    }
}

#[test]
fn passive_effects_still_follow_an_adjacent_reaction_trigger() {
    // A Nine Lives mover hit by a two-hex controller's departure reaction behaves as before.
    let mut s = board(P1, E1, 1, MeleeReach::Adjacent);
    s.units[E1 as usize].melee_reach = MeleeReach::TwoHex;
    s.units[E1 as usize].stats.dmg_min = 500;
    s.units[E1 as usize].stats.dmg_max = 500;
    s.units[P1 as usize].has_nine_lives = true;
    s.units[E1 as usize].stats.melee_skill = 500; // clamped to a 95% hit chance
    s.rng = Rng::new((0..).find(|&seed| (Rng::new(seed).next_u64() % 100) < 95).unwrap());
    let events = s.apply(Command::Move { unit: P1, to: AWAY }).unwrap();
    assert!(events.iter().any(|e| matches!(e, Event::NineLivesTriggered { unit: P1 })));
    assert!(s.units[P1 as usize].alive);
}

fn ai_board(actor: u32, targets: &[(u32, i32)], reach: MeleeReach) -> BattleState {
    let (first, rest) = targets.split_first().unwrap();
    let mut s = board(actor, first.0, first.1, reach);
    for &(target, distance) in rest {
        s.units[target as usize].pos = if distance == 1 { off(at(1), 0, -1) } else { at(distance) };
    }
    assert_fixture_valid(&s);
    s
}

#[test]
fn ai_attacks_an_affordable_distance_two_target_before_approaching() {
    for (actor, target) in [(E1, P1), (P1, E1)] {
        let mut s = ai_board(actor, &[(target, 2)], MeleeReach::TwoHex);
        let cmd = plan_turn(&s);
        assert_eq!(cmd, vec![attack(actor, target)]);
        let preview = s.preview_attack(actor, target).unwrap();
        let ev = s.apply(cmd[0]).unwrap();
        assert_eq!(attacked_chance(&ev), preview.hit_chance);
        assert_eq!(s.units[actor as usize].pos, at(0), "no movement first");

        // Default reach keeps the old behaviour: approach to an adjacent hex.
        let s = ai_board(actor, &[(target, 2)], MeleeReach::Adjacent);
        assert_eq!(plan_turn(&s), vec![Command::Move { unit: actor, to: at(1) }]);
    }
}

#[test]
fn ai_unaffordable_two_hex_actor_stays_in_reach_while_adjacent_actor_still_approaches() {
    let mut s = ai_board(E1, &[(P1, 2)], MeleeReach::TwoHex);
    s.units[E1 as usize].ap = ATTACK_AP - 1; // 3 AP: a move (2) but not an attack
    let cmd = plan_turn(&s);
    assert_eq!(cmd, vec![Command::EndTurn { unit: E1 }]);
    s.apply(cmd[0]).expect("end turn is legal");
    assert_eq!(s.units[E1 as usize].pos.distance(s.units[P1 as usize].pos), 2);

    let mut adjacent = ai_board(E1, &[(P1, 2)], MeleeReach::Adjacent);
    adjacent.units[E1 as usize].ap = ATTACK_AP - 1;
    let cmd = plan_turn(&adjacent);
    assert_eq!(cmd, vec![Command::Move { unit: E1, to: at(1) }]);
    adjacent.apply(cmd[0]).expect("adjacent approach remains legal");
    assert_eq!(adjacent.units[E1 as usize].pos.distance(adjacent.units[P1 as usize].pos), 1);
}

#[test]
fn ai_keeps_weakest_target_and_withdraw_flee_priorities_with_reach() {
    // Weakest hit points win across distances one and two; ties go to the lower id.
    let mut s = ai_board(E1, &[(P1, 2), (P2, 1)], MeleeReach::TwoHex);
    s.units[P1 as usize].hp = 10;
    s.units[P2 as usize].hp = 40;
    assert_eq!(s.legal_targets(E1), vec![P1, P2]);
    assert_eq!(plan_turn(&s), vec![attack(E1, P1)]);
    s.units[P1 as usize].hp = 40;
    assert_eq!(plan_turn(&s), vec![attack(E1, P1)], "equal hp: lower id");
    s.units[P2 as usize].hp = 5;
    assert_eq!(plan_turn(&s), vec![attack(E1, P2)]);
    s.apply(plan_turn(&s)[0]).unwrap();

    let mut s = ai_board(E1, &[(P1, 2)], MeleeReach::TwoHex);
    s.units[E1 as usize].retreating = true;
    assert_eq!(plan_turn(&s), vec![Command::Withdraw { unit: E1 }]);
    s.units[E1 as usize].retreating = false;
    s.units[E1 as usize].morale = Morale::Fleeing;
    assert_eq!(plan_turn(&s), vec![Command::Flee { unit: E1 }]);
}

#[test]
fn previewing_does_not_change_events_or_rng_and_queries_are_pure() {
    for (actor, target) in [(P1, E1), (E1, P1)] {
        let mut previewed = board(actor, target, 2, MeleeReach::TwoHex);
        let mut plain = previewed.clone();
        let before = snapshot(&previewed);
        for _ in 0..3 {
            assert_eq!(previewed.legal_targets(actor), vec![target]);
            previewed.preview_attack(actor, target).unwrap();
            previewed.hit_chance(actor, target);
            previewed.enemy_controllers_at(actor, previewed.units[actor as usize].pos);
        }
        assert_eq!(snapshot(&previewed), before);
        let a = previewed.apply(attack(actor, target)).unwrap();
        let b = plain.apply(attack(actor, target)).unwrap();
        assert_eq!(a, b);
        assert_eq!(snapshot(&previewed), snapshot(&plain));
    }
}

#[test]
fn reach_changes_the_hash_and_defaults_stay_pinned() {
    let mut s = BattleState::new(1);
    let flat = s.state_hash();
    assert_eq!(flat, 0x8c00_3021_13cb_e668);
    s.units[0].melee_reach = MeleeReach::TwoHex;
    let one = s.state_hash();
    assert_ne!(one, flat);
    s.units[1].melee_reach = MeleeReach::TwoHex;
    let two = s.state_hash();
    assert!(two != one && two != flat);
    s.units[1].melee_reach = MeleeReach::Adjacent;
    assert_eq!(s.state_hash(), one);
    s.units[0].melee_reach = MeleeReach::Adjacent;
    assert_eq!(s.state_hash(), flat);
    // Reach is independent of the other flags: it differs from every earlier bit.
    let mut other = BattleState::new(1);
    other.units[0].indomitable_active = true;
    assert_ne!(other.state_hash(), one);

    assert_eq!(battle(1, TerrainPreset::Flat).state_hash(), 0x8c00_3021_13cb_e668);
    assert_eq!(battle(1, TerrainPreset::Hills).state_hash(), 0xb759_683c_dff4_bfa2);
    assert_eq!(battle(1, TerrainPreset::Swamp).state_hash(), SWAMP_PIN);
}

/// Captured with the reach bit present; Adjacent contributes zero, so it equals the
/// pre-T58 value by construction (Flat and Hills pins above predate T58).
const SWAMP_PIN: u64 = 0x4acb_fb79_42a8_cdc8;
