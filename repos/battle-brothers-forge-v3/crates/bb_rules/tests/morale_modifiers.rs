use bb_rules::hex::hex;
use bb_rules::*;

fn melee_pair() -> BattleState {
    let mut state = BattleState::new(1);
    state.units[0].pos = hex(1, 0);
    state.units[3].pos = hex(2, 0);
    state
}

#[test]
fn new_units_are_steady_and_keep_their_base_combat_stats() {
    let state = BattleState::new(1);
    assert!(state.units.iter().all(|unit| unit.morale == Morale::Steady));
    for unit in &state.units {
        assert_eq!(unit.effective_melee_skill(), unit.stats.melee_skill);
        assert_eq!(unit.effective_defense(), unit.stats.defense);
    }
}

#[test]
fn morale_percentages_floor_odd_melee_values_without_rewriting_stats() {
    let mut state = BattleState::new(1);
    let unit = &mut state.units[0];
    unit.stats.melee_skill = 101;
    unit.stats.defense = 99;

    for (morale, percentage, melee_skill, defense) in [
        (Morale::Steady, 100, 101, 99),
        (Morale::Confident, 110, 111, 108),
        (Morale::Wavering, 90, 90, 89),
        (Morale::Breaking, 80, 80, 79),
    ] {
        unit.morale = morale;
        assert_eq!(morale.modifier_percent(), percentage);
        assert_eq!(unit.effective_melee_skill(), melee_skill);
        assert_eq!(unit.effective_defense(), defense);
        assert_eq!((unit.stats.melee_skill, unit.stats.defense), (101, 99));
    }
}

#[test]
fn effective_stats_handle_zero_and_saturate_large_values() {
    let mut state = BattleState::new(1);
    let unit = &mut state.units[0];
    for morale in [Morale::Steady, Morale::Confident, Morale::Wavering, Morale::Breaking] {
        unit.morale = morale;
        unit.stats.melee_skill = 0;
        unit.stats.defense = 0;
        assert_eq!((unit.effective_melee_skill(), unit.effective_defense()), (0, 0));
    }

    unit.stats.melee_skill = u32::MAX;
    unit.stats.defense = u32::MAX;
    for (morale, percentage, expected) in [
        (Morale::Steady, 100u64, u32::MAX),
        (Morale::Confident, 110, u32::MAX),
        (Morale::Wavering, 90, 3_865_470_565),
        (Morale::Breaking, 80, 3_435_973_836),
    ] {
        unit.morale = morale;
        assert_eq!(unit.effective_melee_skill(), expected);
        assert_eq!(unit.effective_defense(), expected);
        assert_eq!(unit.stats.melee_skill, u32::MAX);
        assert_eq!(unit.stats.defense, u32::MAX);
        assert_eq!(
            expected,
            ((u64::from(u32::MAX) * percentage / 100).min(u64::from(u32::MAX))) as u32
        );
    }
}

#[test]
fn attacker_and_defender_morale_scale_hit_chance_for_both_sides() {
    for (attacker, target) in [(0, 3), (3, 0)] {
        let mut state = melee_pair();
        state.units[attacker as usize].stats.melee_skill = 151;
        state.units[target as usize].stats.defense = 91;
        let attacker_stats = state.units[attacker as usize].stats.clone();
        let target_stats = state.units[target as usize].stats.clone();

        assert_eq!(state.hit_chance(attacker, target), 60);
        state.units[attacker as usize].morale = Morale::Confident;
        assert_eq!(state.hit_chance(attacker, target), 75);
        state.units[attacker as usize].morale = Morale::Wavering;
        assert_eq!(state.hit_chance(attacker, target), 44);
        state.units[attacker as usize].morale = Morale::Breaking;
        assert_eq!(state.hit_chance(attacker, target), 29);

        state.units[attacker as usize].morale = Morale::Steady;
        state.units[target as usize].morale = Morale::Confident;
        assert_eq!(state.hit_chance(attacker, target), 51);
        state.units[target as usize].morale = Morale::Wavering;
        assert_eq!(state.hit_chance(attacker, target), 70);
        state.units[target as usize].morale = Morale::Breaking;
        assert_eq!(state.hit_chance(attacker, target), 79);

        state.units[attacker as usize].morale = Morale::Confident;
        state.units[target as usize].morale = Morale::Confident;
        assert_eq!(state.hit_chance(attacker, target), 66);
        assert_eq!(state.units[attacker as usize].stats, attacker_stats);
        assert_eq!(state.units[target as usize].stats, target_stats);
    }
}

#[test]
fn surround_is_added_to_scaled_stats_before_the_existing_clamps() {
    let mut state = melee_pair();
    state.units[1].pos = hex(3, -1);
    state.units[2].pos = hex(1, 1);
    state.units[0].stats.melee_skill = 10;
    state.units[0].morale = Morale::Wavering; // floor(10 * 90 / 100) = 9
    state.units[3].stats.defense = 10;
    state.units[3].morale = Morale::Confident; // floor(10 * 110 / 100) = 11
    assert_eq!(state.hit_chance(0, 3), 8, "9 - 11 + two surround allies * 5");

    state.units[0].stats.melee_skill = 86;
    state.units[0].morale = Morale::Confident;
    state.units[3].stats.defense = 0;
    assert_eq!(state.hit_chance(0, 3), 95, "94 scaled skill plus surround reaches the upper clamp");
    state.units[0].stats.melee_skill = 0;
    state.units[3].stats.defense = u32::MAX;
    assert_eq!(state.hit_chance(0, 3), 5);
}

#[test]
fn adjusted_preview_matches_ordinary_attack_and_opportunity_attack_chances() {
    let mut state = melee_pair();
    state.units[0].morale = Morale::Confident;
    state.units[3].morale = Morale::Wavering;
    let ordinary_preview = state.hit_chance(0, 3);
    assert_eq!(ordinary_preview, 62);
    let events = state.apply(Command::Attack { attacker: 0, target: 3 }).unwrap();
    assert!(events.iter().any(|event| matches!(event,
        Event::Attacked { attacker: 0, target: 3, chance, .. } if *chance == ordinary_preview
    )));

    let mut state = BattleState::new(1);
    state.units[0].pos = hex(0, -2);
    state.units[3].pos = hex(1, -3);
    state.units[0].morale = Morale::Wavering;
    state.units[3].morale = Morale::Confident;
    let reaction_preview = state.hit_chance(3, 0);
    assert_eq!(reaction_preview, 46);
    let events = state.apply(Command::Move { unit: 0, to: hex(-2, -2) }).unwrap();
    assert!(events.iter().any(|event| matches!(event,
        Event::Attacked { attacker: 3, target: 0, chance, .. } if *chance == reaction_preview
    )));
}

#[test]
fn morale_stat_and_hit_chance_queries_are_repeatable_and_pure() {
    let mut state = melee_pair();
    state.units[0].morale = Morale::Breaking;
    state.units[3].morale = Morale::Confident;
    let before = (state.state_hash(), state.rng.clone(), state.units[0].stats.clone());

    for _ in 0..4 {
        assert_eq!(state.units[0].effective_melee_skill(), 48);
        assert_eq!(state.units[3].effective_defense(), 5);
        assert_eq!(state.hit_chance(0, 3), 43);
    }

    assert_eq!((state.state_hash(), state.rng.clone(), state.units[0].stats.clone()), before);
}

#[test]
fn identical_seeded_commands_with_morale_remain_deterministic() {
    let mut a = melee_pair();
    a.units[0].morale = Morale::Confident;
    a.units[3].morale = Morale::Wavering;
    let mut b = a.clone();

    for command in [Command::Attack { attacker: 0, target: 3 }, Command::EndTurn { unit: 0 }] {
        assert_eq!(a.apply(command), b.apply(command));
        assert_eq!(a.state_hash(), b.state_hash());
        assert_eq!(a.rng, b.rng);
    }
}

#[test]
fn attacks_death_and_round_transitions_do_not_change_morale() {
    let mut state = melee_pair();
    let morale = [
        Morale::Confident,
        Morale::Wavering,
        Morale::Breaking,
        Morale::Wavering,
        Morale::Confident,
        Morale::Breaking,
    ];
    for (unit, morale) in state.units.iter_mut().zip(morale) {
        unit.morale = morale;
        // Resolve 100 always passes, so the death's ally-death checks (rules)
        // draw but leave every morale unchanged and this test isolates the rest.
        unit.stats.resolve = 100;
    }
    state.units[0].stats.melee_skill = 200;
    state.units[3].hp = 1;
    state.units[3].armour = 0;
    let events = state.apply(Command::Attack { attacker: 0, target: 3 }).unwrap();
    assert!(events.contains(&Event::Died { unit: 3 }));
    assert!(events.iter().filter(|e| matches!(e, Event::AllyDeathMoraleChecked { before, after, .. } if before == after)).count() == 2);
    assert_eq!(state.units.iter().map(|unit| unit.morale).collect::<Vec<_>>(), morale);

    while state.round == 1 {
        let active = state.active_unit().unwrap().id;
        state.apply(Command::EndTurn { unit: active }).unwrap();
    }
    assert_eq!(state.units.iter().map(|unit| unit.morale).collect::<Vec<_>>(), morale);
}

#[test]
fn morale_hashing_preserves_steady_hashes_and_coexists_with_skip_turn() {
    let steady = BattleState::new(1);
    // Pinned for the hash schema that appends each unit's base Resolve after the
    // flag word (previous schema: 0xd144_cb90_f793_9be8). The flag encoding is unchanged.
    assert_eq!(steady.state_hash(), 0x8c00_3021_13cb_e668);

    let mut skip = steady.clone();
    skip.units[1].skip_turn = true;
    let mut morale = steady.clone();
    morale.units[1].morale = Morale::Confident;
    assert_ne!(steady.state_hash(), morale.state_hash());
    assert_ne!(steady.state_hash(), skip.state_hash());

    let mut both = skip.clone();
    both.units[1].morale = Morale::Confident;
    assert_ne!(skip.state_hash(), both.state_hash());

    // A moraled unit still follows the existing scheduled skip path.
    let mut state = BattleState::new(1);
    state.units[2].skip_turn = true;
    state.units[2].morale = Morale::Breaking;
    state.apply(Command::EndTurn { unit: 0 }).unwrap();
    state.apply(Command::EndTurn { unit: 1 }).unwrap();
    assert_eq!(state.active_unit().unwrap().id, 3);
    assert!(state.units[2].skip_turn);
    assert_eq!(state.units[2].morale, Morale::Breaking);
}
