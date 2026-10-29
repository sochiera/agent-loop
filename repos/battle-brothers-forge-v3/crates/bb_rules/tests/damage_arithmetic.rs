//! Regression coverage for overflow boundaries in the shared melee damage path.

use bb_rules::hex::{hex, map_hexes};
use bb_rules::rules::{
    DEFENDER_HIT_FATIGUE_GAIN, MORALE_CHECK_HP_LOSS_THRESHOLD, MOVE_AP, MOVE_FATIGUE, soak,
};
use bb_rules::*;

const P1: u32 = 0;
const E1: u32 = 3;
const E2: u32 = 4;
const E3: u32 = 5;
const U32_MAX: u32 = u32::MAX;

fn adjacent_battle() -> BattleState {
    let mut s = BattleState::new(1);
    s.units[P1 as usize].pos = hex(0, -2);
    s.units[E1 as usize].pos = hex(1, -2);
    assert_fixture_valid(&s);
    s
}

fn assert_fixture_valid(s: &BattleState) {
    for (i, unit) in s.units.iter().enumerate() {
        assert!(map_hexes().contains(&unit.pos) && !unit.pos.is_obstacle());
        assert!(!s.units[..i].iter().any(|other| other.pos == unit.pos));
        assert!(unit.hp <= unit.stats.hp);
        assert!(unit.armour <= unit.stats.armour);
        assert!(unit.ap <= unit.stats.ap_max);
        assert!(unit.fatigue <= unit.stats.fatigue_max);
    }
}

#[test]
fn soak_preserves_zero_full_and_ordinary_partial_cases_and_saturates_overflow() {
    assert_eq!(soak(0, 0), (0, 0));
    assert_eq!(soak(0, 37), (0, 37));
    assert_eq!(soak(50, 50), (50, 5));
    assert_eq!(soak(20, 100), (20, 90));
    assert_eq!(soak(1, U32_MAX), (1, U32_MAX));
}

fn rig_hit_sample(
    s: &mut BattleState,
    dmg_min: u32,
    dmg_max: u32,
    wanted_damage: impl Fn(u32) -> bool,
) -> u32 {
    let width = u64::from(dmg_max) - u64::from(dmg_min) + 1;
    for seed in 0.. {
        let mut rng = Rng::new(seed);
        let roll = (rng.next_u64() % 100) as u32;
        let draw = rng.next_u64();
        let damage = (u64::from(dmg_min) + draw % width) as u32;
        if roll == 0 && wanted_damage(damage) {
            s.rng = Rng::new(seed);
            return damage;
        }
    }
    unreachable!("there are hit streams matching each test range")
}

fn rng_after(rng: &Rng, draws: usize) -> Rng {
    let mut expected = rng.clone();
    for _ in 0..draws {
        expected.next_u64();
    }
    expected
}

fn attacked(events: &[Event], attacker: u32, target: u32) -> (u32, u32, u32, u32, u32) {
    events
        .iter()
        .find_map(|event| match event {
            Event::Attacked {
                attacker: a,
                target: t,
                hit,
                roll,
                chance,
                dmg,
                absorbed,
                hp_loss,
            } if *a == attacker && *t == target && *hit => {
                Some((*roll, *chance, *dmg, *absorbed, *hp_loss))
            }
            _ => None,
        })
        .expect("successful attack event")
}

fn max_hp_target(s: &mut BattleState, id: u32, armour: u32) {
    let target = &mut s.units[id as usize];
    target.stats.hp = U32_MAX;
    target.hp = U32_MAX;
    target.armour = armour;
    assert_fixture_valid(s);
}

#[test]
fn full_width_damage_range_samples_the_draw_low_word() {
    let mut s = adjacent_battle();
    let attacker = &mut s.units[P1 as usize];
    attacker.stats.dmg_min = 0;
    attacker.stats.dmg_max = U32_MAX;
    max_hp_target(&mut s, E1, 0);

    let expected_damage = rig_hit_sample(&mut s, 0, U32_MAX, |damage| damage < U32_MAX);
    let rng_before = s.rng.clone();

    let events = s
        .apply(Command::Attack {
            attacker: P1,
            target: E1,
        })
        .unwrap();
    let (roll, _, damage, absorbed, hp_loss) = attacked(&events, P1, E1);
    assert_eq!(roll, 0);
    assert_eq!(damage, expected_damage);
    assert_eq!(damage, rng_after(&rng_before, 1).next_u64() as u32);
    assert_eq!(absorbed, 0);
    assert_eq!(hp_loss, damage);
    assert_eq!(s.units[E1 as usize].hp, U32_MAX - damage);
    let expected_draws = 2 + usize::from(damage >= MORALE_CHECK_HP_LOSS_THRESHOLD);
    assert_eq!(
        s.rng,
        rng_after(&rng_before, expected_draws),
        "one hit draw plus one damage draw"
    );
    assert_eq!(
        s.units[E1 as usize].fatigue,
        DEFENDER_HIT_FATIGUE_GAIN.min(s.units[E1 as usize].stats.fatigue_max)
    );
}

#[test]
fn singleton_zero_damage_range_still_uses_one_damage_draw() {
    let mut s = adjacent_battle();
    s.units[P1 as usize].stats.dmg_min = 0;
    s.units[P1 as usize].stats.dmg_max = 0;
    rig_hit_sample(&mut s, 0, 0, |_| true);
    let rng_before = s.rng.clone();
    let events = s
        .apply(Command::Attack {
            attacker: P1,
            target: E1,
        })
        .unwrap();
    assert_eq!(attacked(&events, P1, E1), (0, 55, 0, 0, 0));
    assert_eq!(s.rng, rng_after(&rng_before, 2));
}

#[test]
fn singleton_maximum_damage_with_one_armour_kills_a_max_hp_defender() {
    let mut s = adjacent_battle();
    s.units[P1 as usize].stats.dmg_min = U32_MAX;
    s.units[P1 as usize].stats.dmg_max = U32_MAX;
    max_hp_target(&mut s, E1, 1);
    rig_hit_sample(&mut s, U32_MAX, U32_MAX, |damage| damage == U32_MAX);
    let rng_before = s.rng.clone();

    let events = s
        .apply(Command::Attack {
            attacker: P1,
            target: E1,
        })
        .unwrap();

    assert_eq!(attacked(&events, P1, E1), (0, 55, U32_MAX, 1, U32_MAX));
    assert_eq!(s.units[E1 as usize].hp, 0);
    assert!(!s.units[E1 as usize].alive);
    assert_eq!(events.len(), 5);
    assert!(matches!(events[0], Event::Attacked { .. }));
    assert!(matches!(events[1], Event::Died { unit: E1 }));
    assert!(matches!(
        events[2],
        Event::AllyDeathMoraleChecked {
            unit: E2,
            fallen: E1,
            ..
        }
    ));
    assert!(matches!(
        events[3],
        Event::AllyDeathMoraleChecked {
            unit: E3,
            fallen: E1,
            ..
        }
    ));
    assert!(matches!(
        events[4],
        Event::KillMoraleChecked {
            unit: P1,
            fallen: E1,
            ..
        }
    ));
    assert_eq!(s.rng, rng_after(&rng_before, 5));
}

#[test]
fn narrow_range_ending_at_max_samples_the_inclusive_upper_bound() {
    let mut s = adjacent_battle();
    let min = U32_MAX - 6;
    s.units[P1 as usize].stats.dmg_min = min;
    s.units[P1 as usize].stats.dmg_max = U32_MAX;
    max_hp_target(&mut s, E1, 0);
    let damage = rig_hit_sample(&mut s, min, U32_MAX, |damage| damage == U32_MAX);
    assert_eq!(damage, U32_MAX);
    let rng_before = s.rng.clone();

    let events = s
        .apply(Command::Attack {
            attacker: P1,
            target: E1,
        })
        .unwrap();

    assert_eq!(attacked(&events, P1, E1), (0, 55, U32_MAX, 0, U32_MAX));
    assert!(!s.units[E1 as usize].alive);
    assert_eq!(s.rng, rng_after(&rng_before, 5));
}

#[test]
fn opportunity_attacks_share_full_width_damage_sampling_and_draw_count() {
    let mut s = adjacent_battle();
    s.units[P1 as usize].pos = hex(0, -2);
    s.units[E1 as usize].pos = hex(1, -2);
    s.units[E1 as usize].stats.dmg_min = 0;
    s.units[E1 as usize].stats.dmg_max = U32_MAX;
    max_hp_target(&mut s, P1, 0);
    let expected_damage = rig_hit_sample(&mut s, 0, U32_MAX, |damage| damage < U32_MAX);
    let rng_before = s.rng.clone();
    assert!(map_hexes().contains(&hex(0, -3)) && !hex(0, -3).is_obstacle());

    let events = s
        .apply(Command::Move {
            unit: P1,
            to: hex(0, -3),
        })
        .unwrap();

    let (roll, _, damage, absorbed, hp_loss) = attacked(&events, E1, P1);
    assert_eq!(roll, 0);
    assert_eq!(damage, expected_damage);
    assert_eq!(absorbed, 0);
    assert_eq!(hp_loss, damage);
    assert_eq!(
        s.units[P1 as usize].pos,
        hex(0, -2),
        "a successful reaction stops movement"
    );
    assert_eq!(s.units[P1 as usize].hp, U32_MAX - damage);
    assert_eq!(
        s.units[P1 as usize].fatigue,
        MOVE_FATIGUE + DEFENDER_HIT_FATIGUE_GAIN
    );
    assert_eq!(s.units[P1 as usize].ap, 9 - MOVE_AP);
    let expected_draws = 2 + usize::from(damage >= MORALE_CHECK_HP_LOSS_THRESHOLD);
    assert_eq!(
        s.rng,
        rng_after(&rng_before, expected_draws),
        "shared resolver uses one damage draw"
    );
}

#[test]
fn nine_lives_intercepts_saturated_lethal_loss_and_reports_actual_hp_lost() {
    let mut s = adjacent_battle();
    s.units[P1 as usize].stats.dmg_min = U32_MAX;
    s.units[P1 as usize].stats.dmg_max = U32_MAX;
    max_hp_target(&mut s, E1, 1);
    rig_hit_sample(&mut s, U32_MAX, U32_MAX, |damage| damage == U32_MAX);
    s.units[E1 as usize].has_nine_lives = true;
    s.units[E1 as usize].stats.resolve = 0;
    let rng_before = s.rng.clone();

    let events = s
        .apply(Command::Attack {
            attacker: P1,
            target: E1,
        })
        .unwrap();

    assert_eq!(attacked(&events, P1, E1), (0, 55, U32_MAX, 1, U32_MAX - 1));
    assert_eq!(s.units[E1 as usize].hp, 1);
    assert!(s.units[E1 as usize].alive && s.units[E1 as usize].nine_lives_used);
    assert!(matches!(
        events.get(1),
        Some(Event::NineLivesTriggered { unit: E1 })
    ));
    assert!(matches!(
        events.get(2),
        Some(Event::MoraleChecked { unit: E1, .. })
    ));
    assert!(
        !events
            .iter()
            .any(|event| matches!(event, Event::Died { unit: E1 }))
    );
    assert_eq!(s.rng, rng_after(&rng_before, 3));
}
