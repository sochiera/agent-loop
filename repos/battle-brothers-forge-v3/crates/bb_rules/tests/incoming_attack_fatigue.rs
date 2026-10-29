use bb_rules::hex::hex;
use bb_rules::rules::{ATTACK_AP, ATTACK_FATIGUE};
use bb_rules::*;

fn attack_fixture(seed: u64, attacker: u32, target: u32) -> BattleState {
    let mut state = BattleState::new(seed);
    let (attacker_pos, target_pos) = if state.units[attacker as usize].side == Side::Player {
        (hex(1, 0), hex(2, 0))
    } else {
        (hex(2, 0), hex(1, 0))
    };
    state.units[attacker as usize].pos = attacker_pos;
    state.units[target as usize].pos = target_pos;
    state.queue.retain(|&id| id != attacker);
    state.queue.insert(0, attacker);
    state.units[attacker as usize].ap = 9;
    state
}

fn attacked(events: &[Event], attacker: u32, target: u32) -> (bool, u32, u32, u32) {
    events
        .iter()
        .find_map(|event| match event {
            Event::Attacked { attacker: a, target: t, hit, roll, dmg, absorbed, .. }
                if *a == attacker && *t == target => Some((*hit, *roll, *dmg, *absorbed)),
            _ => None,
        })
        .expect("valid attack emits Event::Attacked")
}

#[test]
fn hit_and_miss_add_fatigue_to_player_and_enemy_defenders() {
    // SplitMix64 seeds 2 and 1 produce rolls that hit and miss these fixtures.
    for (defender, attacker) in [(3, 0), (0, 3)] {
        for (seed, expected_hit, fatigue_gain) in [(2, true, 5), (1, false, 2)] {
            let mut state = attack_fixture(seed, attacker, defender);
            state.units[defender as usize].fatigue = 30;
            // A unit whose turn has already passed remains a valid defender.
            state.units[defender as usize].turn_started = true;
            state.queue.retain(|&id| id != defender);
            let starting_attacker_ap = state.units[attacker as usize].ap;
            let starting_attacker_fatigue = state.units[attacker as usize].fatigue;

            let mut expected_rng = Rng::new(seed);
            let expected_roll = (expected_rng.next_u64() % 100) as u32;
            if expected_hit {
                expected_rng.next_u64(); // The hit's damage roll remains unchanged.
            }
            let events = state.apply(Command::Attack { attacker, target: defender }).unwrap();

            let (hit, roll, _, _) = attacked(&events, attacker, defender);
            assert_eq!(hit, expected_hit);
            assert_eq!(roll, expected_roll);
            assert_eq!(state.rng, expected_rng, "incoming fatigue consumes no RNG");
            assert_eq!(state.units[defender as usize].fatigue, 30 + fatigue_gain);
            assert_eq!(state.units[attacker as usize].ap, starting_attacker_ap - ATTACK_AP);
            assert_eq!(
                state.units[attacker as usize].fatigue,
                starting_attacker_fatigue + ATTACK_FATIGUE
            );
        }
    }
}

#[test]
fn armour_absorbed_hit_still_adds_five_fatigue() {
    let (attacker, defender) = (3, 0);
    let mut state = attack_fixture(2, attacker, defender);
    state.units[defender as usize].armour = 100;
    state.units[defender as usize].fatigue = 10;

    let events = state.apply(Command::Attack { attacker, target: defender }).unwrap();

    let (hit, _, damage, absorbed) = attacked(&events, attacker, defender);
    assert!(hit);
    assert_eq!(absorbed, damage);
    assert_eq!(state.units[defender as usize].fatigue, 15);
}

#[test]
fn incoming_fatigue_saturates_at_the_cap_without_rejecting_an_attack() {
    let (attacker, defender) = (0, 3);
    let mut near_cap = attack_fixture(2, attacker, defender);
    near_cap.units[defender as usize].fatigue = 99;
    near_cap.units[defender as usize].stats.fatigue_max = 100;
    near_cap.apply(Command::Attack { attacker, target: defender }).unwrap();
    assert_eq!(near_cap.units[defender as usize].fatigue, 100);

    let mut integer_limit = attack_fixture(2, attacker, defender);
    integer_limit.units[defender as usize].fatigue = u32::MAX - 1;
    integer_limit.units[defender as usize].stats.fatigue_max = u32::MAX;
    integer_limit.apply(Command::Attack { attacker, target: defender }).unwrap();
    assert_eq!(integer_limit.units[defender as usize].fatigue, u32::MAX);

    let mut at_max = attack_fixture(1, attacker, defender);
    at_max.units[defender as usize].fatigue = 100;
    at_max.units[defender as usize].stats.fatigue_max = 100;
    let events = at_max.apply(Command::Attack { attacker, target: defender });
    assert!(events.is_ok(), "maximum defender fatigue does not block an attack");
    assert!(!attacked(&events.unwrap(), attacker, defender).0);
    assert_eq!(at_max.units[defender as usize].fatigue, 100);
}

#[test]
fn lethal_hit_adds_fatigue_before_death_handling() {
    let (attacker, defender) = (0, 3);
    let mut state = attack_fixture(2, attacker, defender);
    state.units[defender as usize].hp = 1;
    state.units[defender as usize].armour = 0;
    state.units[defender as usize].fatigue = 20;

    let events = state.apply(Command::Attack { attacker, target: defender }).unwrap();

    assert!(attacked(&events, attacker, defender).0);
    assert_eq!(state.units[defender as usize].fatigue, 25);
    assert!(!state.units[defender as usize].alive);
    let attack_index = events.iter().position(|event| matches!(event, Event::Attacked { .. })).unwrap();
    let death_index = events.iter().position(|event| matches!(event, Event::Died { unit } if *unit == defender)).unwrap();
    assert!(attack_index < death_index);
}

#[test]
fn incoming_fatigue_can_make_the_defenders_next_attack_too_fatigued() {
    let (attacker, defender) = (0, 3);
    let mut state = attack_fixture(2, attacker, defender);
    state.units[defender as usize].fatigue = 86;
    state.units[defender as usize].ap = 9;

    state.apply(Command::Attack { attacker, target: defender }).unwrap();
    assert_eq!(state.units[defender as usize].fatigue, 91);

    state.queue.retain(|&id| id != defender);
    state.queue.insert(0, defender);
    let before = state.state_hash();
    assert_eq!(
        state.apply(Command::Attack { attacker: defender, target: attacker }),
        Err(RuleError::TooFatigued)
    );
    assert_eq!(state.state_hash(), before);
}

#[test]
fn turn_start_regenerates_fifteen_from_updated_fatigue() {
    let (attacker, defender) = (0, 3);
    let mut state = attack_fixture(2, attacker, defender);
    state.units[defender as usize].fatigue = 20;
    state.queue.retain(|&id| id != defender);
    state.queue.insert(1, defender);

    state.apply(Command::Attack { attacker, target: defender }).unwrap();
    assert_eq!(state.units[defender as usize].fatigue, 25);
    state.apply(Command::EndTurn { unit: attacker }).unwrap();

    assert_eq!(state.active_unit().unwrap().id, defender);
    assert_eq!(state.units[defender as usize].fatigue, 10);
}

#[test]
fn invalid_attack_keeps_rng_state_and_defender_fatigue_unchanged() {
    let (attacker, defender) = (0, 3);
    let mut state = attack_fixture(2, attacker, defender);
    state.units[defender as usize].fatigue = 37;
    state.units[defender as usize].pos = hex(3, -3);
    let before = state.state_hash();

    assert_eq!(
        state.apply(Command::Attack { attacker, target: defender }),
        Err(RuleError::NotAdjacent)
    );

    assert_eq!(state.units[defender as usize].fatigue, 37);
    assert_eq!(state.state_hash(), before);
}

#[test]
fn incoming_fatigue_affects_initiative_at_the_next_round() {
    let (attacker, defender) = (0, 3);
    let mut state = attack_fixture(2, attacker, defender);
    // This defender has already acted, so the incoming fatigue remains through
    // round end and participates in the next initiative sort.
    state.units[defender as usize].turn_started = true;
    state.queue.retain(|&id| id != defender);

    state.apply(Command::Attack { attacker, target: defender }).unwrap();
    assert_eq!(state.units[defender as usize].fatigue, 5);
    while state.round == 1 {
        let active = state.active_unit().unwrap().id;
        state.apply(Command::EndTurn { unit: active }).unwrap();
    }

    assert_eq!(state.round, 2);
    let defender_order = state.queue.iter().position(|&id| id == defender).unwrap();
    let peer_enemy_order = state.queue.iter().position(|&id| id == 4).unwrap();
    assert!(peer_enemy_order < defender_order);
}
