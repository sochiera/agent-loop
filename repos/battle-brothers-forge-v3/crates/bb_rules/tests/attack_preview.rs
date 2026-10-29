//! T56 melee AttackPreview. The API and pre-armour range presentation are
//! APPROXIMATIONs; existing hit chance, damage sampling and Indomitable
//! provenance remain as documented in the source catalog and slice notes.

use bb_rules::hex::{hex, map_hexes};
use bb_rules::rules::{ATTACK_AP, ATTACK_FATIGUE, AttackPreview};
use bb_rules::*;

const P1: u32 = 0;
const P2: u32 = 1;
const E1: u32 = 3;
const E2: u32 = 4;
const E3: u32 = 5;
const U32_MAX: u32 = u32::MAX;

const PARK_PLAYERS: [Hex; 3] = [hex(-4, 2), hex(-4, 3), hex(-4, 4)];
const PARK_ENEMIES: [Hex; 3] = [hex(4, -4), hex(4, -3), hex(4, -2)];

fn board(attacker: u32, target: u32) -> BattleState {
    let mut state = BattleState::new(1);
    for index in 0..3 {
        state.units[index].pos = PARK_PLAYERS[index];
        state.units[3 + index].pos = PARK_ENEMIES[index];
    }
    for unit in &mut state.units {
        unit.stats.melee_skill = 0;
        unit.stats.defense = 0;
        unit.stats.dmg_min = 3;
        unit.stats.dmg_max = 9;
        unit.stats.resolve = 0;
        unit.turn_started = false;
    }
    state.units[attacker as usize].pos = hex(0, -2);
    state.units[target as usize].pos = hex(1, -2);
    state.queue.retain(|&unit| unit != attacker);
    state.queue.insert(0, attacker);
    let actor = &mut state.units[attacker as usize];
    actor.ap = actor.stats.ap_max;
    actor.turn_started = true;
    assert_fixture_valid(&state);
    state
}

fn assert_fixture_valid(state: &BattleState) {
    let positions: Vec<_> = state.units.iter().map(|unit| unit.pos).collect();
    for (index, &position) in positions.iter().enumerate() {
        assert!(map_hexes().contains(&position) && !position.is_obstacle());
        assert!(
            !positions[..index].contains(&position),
            "duplicate {position:?}"
        );
    }
    for unit in &state.units {
        assert!(unit.hp <= unit.stats.hp);
        assert!(unit.armour <= unit.stats.armour);
        assert!(unit.ap <= unit.stats.ap_max);
        assert!(unit.fatigue <= unit.stats.fatigue_max);
        assert!(unit.stats.dmg_min <= unit.stats.dmg_max);
    }
}

fn snapshot(
    state: &BattleState,
) -> (
    u64,
    Rng,
    Vec<Unit>,
    Vec<u32>,
    u32,
    Option<Outcome>,
    Option<EndReason>,
    ElevationMap,
    TerrainMap,
) {
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

fn seed_for_roll(chance: u32, want_hit: bool) -> u64 {
    (0..)
        .find(|&seed| {
            let roll = (Rng::new(seed).next_u64() % 100) as u32;
            (roll < chance) == want_hit
        })
        .expect("a SplitMix64 seed with the requested attack result")
}

fn attack(attacker: u32, target: u32) -> Command {
    Command::Attack { attacker, target }
}

fn assert_query_and_command_error(
    state: &mut BattleState,
    attacker: u32,
    target: u32,
    expected: RuleError,
) {
    let before = snapshot(state);
    assert_eq!(state.preview_attack(attacker, target), Err(expected));
    assert_eq!(snapshot(state), before, "a rejected preview is read-only");
    assert_eq!(state.apply(attack(attacker, target)), Err(expected));
    assert_eq!(snapshot(state), before, "a rejected Attack is read-only");
}

fn attacked(events: &[Event]) -> (bool, u32, u32, u32, u32) {
    events
        .iter()
        .find_map(|event| match event {
            Event::Attacked {
                hit,
                roll,
                chance,
                dmg,
                absorbed,
                ..
            } => Some((*hit, *roll, *chance, *dmg, *absorbed)),
            _ => None,
        })
        .expect("the attack emits Attacked")
}

fn assert_same_resolution(left: &BattleState, right: &BattleState) {
    assert_eq!(left.state_hash(), right.state_hash());
    assert_eq!(left.rng, right.rng);
    assert_eq!(left.units, right.units);
    assert_eq!(left.queue, right.queue);
    assert_eq!(left.round, right.round);
    assert_eq!(left.outcome, right.outcome);
    assert_eq!(left.end_reason, right.end_reason);
    assert_eq!(left.elevations, right.elevations);
    assert_eq!(left.ground, right.ground);
}

#[test]
fn both_factions_share_snapshot_costs_and_hit_chance_with_attack_events() {
    for (attacker, target, ally) in [(P1, E1, P2), (E1, P1, E2)] {
        let mut state = board(attacker, target);
        state.units[attacker as usize].stats.melee_skill = 67;
        state.units[target as usize].stats.defense = 31;
        state.units[attacker as usize].morale = Morale::Wavering;
        state.units[target as usize].morale = Morale::Confident;
        state.units[attacker as usize].has_fast_adaptation = true;
        state.units[attacker as usize].fast_adaptation_stacks = 2;
        state.units[target as usize].has_dodge = true;
        state.units[ally as usize].pos = hex(0, -1); // another ally adjacent to target
        state.elevations.set(hex(0, -2), 2).unwrap();
        state.elevations.set(hex(1, -2), 0).unwrap();

        let actor = &mut state.units[attacker as usize];
        actor.ap = ATTACK_AP;
        actor.fatigue = actor.stats.fatigue_max - ATTACK_FATIGUE;
        assert_fixture_valid(&state);

        let preview: AttackPreview = state.preview_attack(attacker, target).unwrap();
        assert_eq!(preview.attacker, attacker);
        assert_eq!(preview.target, target);
        assert_eq!(
            (preview.ap_cost, preview.fatigue_cost),
            (ATTACK_AP, ATTACK_FATIGUE)
        );
        assert_eq!(preview.hit_chance, state.hit_chance(attacker, target));
        assert_eq!((preview.damage_min, preview.damage_max), (3, 9));

        state.rng = Rng::new(seed_for_roll(preview.hit_chance, true));
        let events = state.apply(attack(attacker, target)).unwrap();
        let (hit, roll, chance, damage, _) = attacked(&events);
        assert!(hit);
        assert!(roll < chance);
        assert_eq!(chance, preview.hit_chance);
        assert!((preview.damage_min..=preview.damage_max).contains(&damage));
        assert_eq!(state.units[attacker as usize].ap, 0);
        assert_eq!(
            state.units[attacker as usize].fatigue,
            state.units[attacker as usize].stats.fatigue_max
        );
    }
}

#[test]
fn preview_and_execution_keep_actor_target_adjacency_and_budget_error_order() {
    let mut state = board(P1, E1);
    state.outcome = Some(Outcome::Victory);
    assert_query_and_command_error(&mut state, P1, E1, RuleError::BattleOver);

    let mut state = board(P1, E1);
    state.queue.retain(|&unit| unit != P1);
    state.queue.insert(0, P2);
    assert_query_and_command_error(&mut state, P1, u32::MAX, RuleError::NotActiveUnit);

    let mut state = board(P1, E1);
    state.units[P1 as usize].morale = Morale::Fleeing;
    assert_query_and_command_error(&mut state, P1, u32::MAX, RuleError::FleeingUnit);

    let mut state = board(P1, E1);
    state.units[P1 as usize].retreating = true;
    state.units[P1 as usize].morale = Morale::Fleeing;
    assert_query_and_command_error(&mut state, P1, u32::MAX, RuleError::RetreatingUnit);

    for invalid_target in [u32::MAX, P1, P2, E2, E3] {
        let mut state = board(P1, E1);
        if invalid_target == E2 || invalid_target == E3 {
            state.units[invalid_target as usize].alive = false;
        }
        state.units[P1 as usize].ap = 0;
        state.units[P1 as usize].fatigue = state.units[P1 as usize].stats.fatigue_max;
        assert_query_and_command_error(&mut state, P1, invalid_target, RuleError::InvalidTarget);
    }

    let mut state = board(P1, E1);
    state.units[E1 as usize].fled = true;
    assert_query_and_command_error(&mut state, P1, E1, RuleError::InvalidTarget);

    let mut state = board(P1, E1);
    state.units[E1 as usize].pos = hex(2, -2);
    state.units[P1 as usize].ap = 0;
    state.units[P1 as usize].fatigue = state.units[P1 as usize].stats.fatigue_max;
    assert_query_and_command_error(&mut state, P1, E1, RuleError::NotAdjacent);

    let mut state = board(P1, E1);
    state.units[P1 as usize].ap = ATTACK_AP - 1;
    state.units[P1 as usize].fatigue = state.units[P1 as usize].stats.fatigue_max;
    assert_query_and_command_error(&mut state, P1, E1, RuleError::NotEnoughAp);

    let mut state = board(P1, E1);
    state.units[P1 as usize].ap = ATTACK_AP;
    state.units[P1 as usize].fatigue =
        state.units[P1 as usize].stats.fatigue_max - ATTACK_FATIGUE + 1;
    assert_query_and_command_error(&mut state, P1, E1, RuleError::TooFatigued);
}

#[test]
fn exact_cost_limits_are_accepted_and_fatigue_addition_is_checked() {
    let mut state = board(P1, E1);
    state.units[P1 as usize].ap = ATTACK_AP;
    state.units[P1 as usize].fatigue = state.units[P1 as usize].stats.fatigue_max - ATTACK_FATIGUE;
    let preview = state.preview_attack(P1, E1).unwrap();
    assert_eq!(
        (preview.ap_cost, preview.fatigue_cost),
        (ATTACK_AP, ATTACK_FATIGUE)
    );

    let mut overflow = board(P1, E1);
    overflow.units[P1 as usize].stats.fatigue_max = U32_MAX;
    overflow.units[P1 as usize].fatigue = U32_MAX - ATTACK_FATIGUE;
    overflow.units[P1 as usize].ap = ATTACK_AP;
    assert!(
        overflow.preview_attack(P1, E1).is_ok(),
        "exact u32::MAX fatigue cap is affordable"
    );
    overflow.units[P1 as usize].fatigue = U32_MAX;
    assert_query_and_command_error(&mut overflow, P1, E1, RuleError::TooFatigued);
}

#[test]
fn damage_endpoints_cover_full_ordered_ranges_and_effective_indomitable_flooring() {
    let cases = [
        (0, 0, false, 0, 0),
        (1, 1, true, 0, 0),
        (3, 9, true, 1, 4),
        (u32::MAX, u32::MAX, true, u32::MAX / 2, u32::MAX / 2),
        (0, u32::MAX, true, 0, u32::MAX / 2),
        (0, u32::MAX, false, 0, u32::MAX),
    ];
    for (minimum, maximum, effective, expected_min, expected_max) in cases {
        for (capability, active, morale, is_effective) in [
            (true, true, Morale::Steady, true),
            (false, true, Morale::Steady, false),
            (true, true, Morale::Fleeing, false),
            (true, false, Morale::Steady, false),
        ] {
            if effective != is_effective {
                continue;
            }
            let mut state = board(P1, E1);
            let attacker = &mut state.units[P1 as usize];
            attacker.stats.dmg_min = minimum;
            attacker.stats.dmg_max = maximum;
            let defender = &mut state.units[E1 as usize];
            defender.has_indomitable = capability;
            defender.indomitable_active = active;
            defender.morale = morale;
            let preview = state.preview_attack(P1, E1).unwrap();
            assert_eq!(
                (preview.damage_min, preview.damage_max),
                (expected_min, expected_max),
                "{minimum}..={maximum}, effective={is_effective}"
            );
        }
    }
}

#[test]
fn damage_range_is_conditional_on_a_hit_and_misses_keep_zero_event_damage() {
    for want_hit in [true, false] {
        let mut state = board(P1, E1);
        state.units[P1 as usize].stats.dmg_min = 7;
        state.units[P1 as usize].stats.dmg_max = 13;
        state.units[E1 as usize].has_indomitable = true;
        state.units[E1 as usize].indomitable_active = true;
        let preview = state.preview_attack(P1, E1).unwrap();
        assert_eq!((preview.damage_min, preview.damage_max), (3, 6));
        state.rng = Rng::new(seed_for_roll(preview.hit_chance, want_hit));
        let events = state.apply(attack(P1, E1)).unwrap();
        let (hit, roll, chance, damage, absorbed) = attacked(&events);
        assert_eq!(hit, want_hit);
        assert_eq!(
            (chance, preview.hit_chance),
            (chance, state.hit_chance(P1, E1))
        );
        if hit {
            assert!(roll < chance);
            assert!((preview.damage_min..=preview.damage_max).contains(&damage));
        } else {
            assert!(roll >= chance);
            assert_eq!(
                (damage, absorbed),
                (0, 0),
                "misses do not turn the conditional range into damage"
            );
        }
    }
}

#[test]
fn preview_queries_do_not_change_state_or_passive_and_terrain_details() {
    let mut state = board(P1, E1);
    state.units[P1 as usize].has_fast_adaptation = true;
    state.units[P1 as usize].fast_adaptation_stacks = 7;
    state.units[E1 as usize].has_nine_lives = true;
    state.units[E1 as usize].indomitable_active = true;
    state.units[E1 as usize].has_indomitable = true;
    state.elevations.set(hex(0, -2), 2).unwrap();
    state.ground.set(hex(1, -2), TerrainKind::Swamp).unwrap();
    let before = snapshot(&state);
    let first = state.preview_attack(P1, E1).unwrap();
    for _ in 0..5 {
        assert_eq!(state.preview_attack(P1, E1).unwrap(), first);
        assert_eq!(snapshot(&state), before);
    }
    assert_eq!(state.units[P1 as usize].fast_adaptation_stacks, 7);
    assert!(!state.units[E1 as usize].nine_lives_used);
    assert!(state.units[E1 as usize].indomitable_active);
}

#[test]
fn querying_before_an_attack_preserves_complete_replays_for_hit_kill_and_nine_lives() {
    for encounter in ["ordinary", "lethal", "nine_lives"] {
        let mut base = board(P1, E1);
        base.units[P1 as usize].stats.dmg_min = 18;
        base.units[P1 as usize].stats.dmg_max = 18;
        base.units[E1 as usize].stats.resolve = 0;
        base.units[E1 as usize].stats.armour = 0;
        base.units[E1 as usize].armour = 0;
        if encounter != "ordinary" {
            base.units[E1 as usize].stats.hp = 1;
            base.units[E1 as usize].hp = 1;
        }
        if encounter == "nine_lives" {
            base.units[E1 as usize].has_nine_lives = true;
        }
        let chance = base.hit_chance(P1, E1);
        base.rng = Rng::new(seed_for_roll(chance, true));
        let mut queried = base.clone();
        let preview = queried.preview_attack(P1, E1).unwrap();
        assert_eq!(preview.hit_chance, chance);

        let events_without = base.apply(attack(P1, E1)).unwrap();
        let events_with = queried.apply(attack(P1, E1)).unwrap();
        assert_eq!(
            events_with, events_without,
            "{encounter}: full event stream"
        );
        assert_same_resolution(&queried, &base);
        match encounter {
            "lethal" => assert!(
                events_with
                    .iter()
                    .any(|event| matches!(event, Event::Died { unit: E1 }))
            ),
            "nine_lives" => {
                assert!(
                    events_with
                        .iter()
                        .any(|event| matches!(event, Event::NineLivesTriggered { unit: E1 }))
                );
                assert!(
                    !events_with
                        .iter()
                        .any(|event| matches!(event, Event::Died { unit: E1 }))
                );
            }
            _ => assert!(
                !events_with
                    .iter()
                    .any(|event| matches!(event, Event::Died { unit: E1 }))
            ),
        }
    }
}
