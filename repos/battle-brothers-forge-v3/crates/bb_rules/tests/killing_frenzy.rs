//! Headless Killing Frenzy (partial UC-COMBAT-055). APPROXIMATION: the catalog supplies only the
//! temporary-buff concept and its Berserk interaction. The 25 % bonus (`floor(base * 125 / 100)`),
//! the duration (rest of the current turn plus the next fresh turn, counter 2, aged once per fresh
//! `begin_turn`), the refresh timing and the paid-kill (`Attack` / `StunningBlow`) trigger
//! restriction are independent slice design. Fixtures park every unit far away (all alive, so no
//! fixture ends the battle), then place actors beside each other at passable, distinct hexes with
//! resources inside the Stats maxima. Every unit has skill 0 / defense 0 / Resolve 100 and base
//! damage 10; `rig` picks RNG states whose draws take requested rolls (0 always hits, 99 always
//! misses), so tests never depend on a particular seed.

use bb_rules::hex::{Hex, hex, map_hexes};
use bb_rules::rules::{
    ATTACK_AP, KILLING_FRENZY_BONUS_PERCENT, KILLING_FRENZY_HASH_TAG, KILLING_FRENZY_TURNS, STUNNING_BLOW_AP,
};
use bb_rules::scenario::{self, TerrainPreset};
use bb_rules::*;

const P1: u32 = 0;
const P2: u32 = 1;
const E1: u32 = 3;
const E2: u32 = 4;
const E3: u32 = 5;

const PARK_PLAYERS: [Hex; 3] = [hex(-4, 2), hex(-4, 3), hex(-4, 4)];
const PARK_ENEMIES: [Hex; 3] = [hex(4, -4), hex(4, -3), hex(4, -2)];

/// Hashes of the accepted initial states; the capability and counter must not move them.
const FLAT_SEED_1_HASH: u64 = 10_088_116_083_934_094_952;
const FLAT_SCENARIO_HASH: u64 = 0x8c00302113cbe668;
const HILLS_SCENARIO_HASH: u64 = 0xb759683cdff4bfa2;

const HIT: u32 = 0;
const MISS: u32 = 99;
const BASE_DAMAGE: u32 = 10;
/// `floor(10 * 125 / 100)`.
const BOOSTED_DAMAGE: u32 = 12;

const ACTOR_AT: Hex = hex(0, -2);
/// Adjacent to `ACTOR_AT`, in the order victims are placed.
const VICTIM_AT: [Hex; 3] = [hex(1, -2), hex(0, -1), hex(-1, -1)];

/// `(actor, its two hostile victims)` for both factions.
const DUELS: [(u32, [u32; 2]); 2] = [(P1, [E1, E2]), (E1, [P1, P2])];

fn board(active: u32, placements: &[(u32, Hex)]) -> BattleState {
    let mut s = BattleState::new(1);
    for i in 0..3 {
        s.units[i].pos = PARK_PLAYERS[i];
        s.units[3 + i].pos = PARK_ENEMIES[i];
    }
    for u in &mut s.units {
        u.stats.defense = 0;
        u.stats.melee_skill = 0;
        u.stats.dmg_min = BASE_DAMAGE;
        u.stats.dmg_max = BASE_DAMAGE;
        u.stats.resolve = 100;
        u.turn_started = false;
    }
    for &(id, at) in placements {
        s.units[id as usize].pos = at;
    }
    s.queue.retain(|&q| q != active);
    s.queue.insert(0, active);
    s.units[active as usize].ap = 9;
    s.units[active as usize].turn_started = true;
    assert_fixture_valid(&s);
    s
}

fn assert_fixture_valid(s: &BattleState) {
    let at: Vec<Hex> = s.units.iter().map(|u| u.pos).collect();
    for (i, h) in at.iter().enumerate() {
        assert!(map_hexes().contains(h) && !h.is_obstacle(), "fixture hex must be passable: {h:?}");
        assert!(!at[..i].contains(h), "fixture hexes must be distinct: {h:?}");
    }
    for u in s.units.iter().filter(|u| u.alive) {
        assert!(u.hp > 0 && u.hp <= u.stats.hp, "{}: hp {} outside 1..={}", u.name, u.hp, u.stats.hp);
        assert!(u.armour <= u.stats.armour && u.ap <= u.stats.ap_max && u.fatigue <= u.stats.fatigue_max, "{}", u.name);
    }
}

/// Frenzy-capable `actor` (active, 9 AP) at `ACTOR_AT` beside two victims. The first victim has
/// 1 HP; the second keeps its full HP so it survives a boosted hit. Both are unarmoured.
fn arena(actor: u32, victims: [u32; 2]) -> BattleState {
    let mut s = board(actor, &[(actor, ACTOR_AT), (victims[0], VICTIM_AT[0]), (victims[1], VICTIM_AT[1])]);
    s.units[actor as usize].has_killing_frenzy = true;
    s.units[actor as usize].has_stunning_blow = true;
    s.units[victims[0] as usize].hp = 1;
    for v in victims {
        s.units[v as usize].armour = 0;
    }
    assert_fixture_valid(&s);
    s
}

/// Set the RNG so draw `i` (mod 100) equals `rolls[i]` where given (`None` = any).
fn rig(s: &mut BattleState, rolls: &[Option<u32>]) {
    for k in 1u64.. {
        let cand = s.rng.state.wrapping_add(k);
        let mut r = Rng::new(cand);
        if rolls.iter().all(|want| {
            let got = (r.next_u64() % 100) as u32;
            want.is_none_or(|w| w == got)
        }) {
            s.rng = Rng::new(cand);
            return;
        }
    }
}

fn paid(blow: bool, attacker: u32, target: u32) -> Command {
    if blow {
        Command::StunningBlow { attacker, target }
    } else {
        Command::Attack { attacker, target }
    }
}

fn strike(s: &mut BattleState, blow: bool, attacker: u32, target: u32, roll: u32) -> Vec<Event> {
    rig(s, &[Some(roll)]);
    s.apply(paid(blow, attacker, target)).unwrap()
}

fn dmg_of(ev: &[Event]) -> u32 {
    ev.iter()
        .find_map(|e| match e {
            Event::Attacked { dmg, .. } => Some(*dmg),
            _ => None,
        })
        .expect("an Attacked event")
}

fn end_turn(s: &mut BattleState) {
    let unit = s.queue[0];
    s.apply(Command::EndTurn { unit }).unwrap();
}

fn counter(s: &BattleState, id: u32) -> u8 {
    s.units[id as usize].killing_frenzy_turns
}

fn snapshot(s: &BattleState) -> (u64, Rng, Vec<Unit>, Vec<u32>, u32, Option<Outcome>) {
    (s.state_hash(), s.rng.clone(), s.units.clone(), s.queue.clone(), s.round, s.outcome())
}

/// Independent mirror of the documented transformation (128-bit, no shared code).
fn boosted(base: u32) -> u32 {
    (u128::from(base) * 125 / 100).min(u128::from(u32::MAX)) as u32
}

// ----------------------------------------------------------------------------- defaults

#[test]
fn defaults_constants_and_initial_hashes() {
    let fresh = Unit::new(9, "X", Side::Enemy, scenario::units()[3].stats.clone(), hex(0, -2));
    assert!(!fresh.has_killing_frenzy);
    assert_eq!(fresh.killing_frenzy_turns, 0);
    assert!(!fresh.is_killing_frenzy_active());
    let s = BattleState::new(1);
    assert!(s.units.iter().all(|u| !u.has_killing_frenzy && u.killing_frenzy_turns == 0));
    assert_eq!(s.state_hash(), FLAT_SEED_1_HASH);
    assert_eq!(scenario::battle(1, TerrainPreset::Flat).state_hash(), FLAT_SCENARIO_HASH);
    assert_eq!(scenario::battle(1, TerrainPreset::Hills).state_hash(), HILLS_SCENARIO_HASH);
    assert_eq!((KILLING_FRENZY_BONUS_PERCENT, KILLING_FRENZY_TURNS), (25, 2));
}

#[test]
fn effective_bonus_requires_capability_counter_and_presence() {
    let mut u = Unit::new(9, "X", Side::Enemy, scenario::units()[3].stats.clone(), hex(0, -2));
    u.killing_frenzy_turns = 1;
    assert!(!u.is_killing_frenzy_active(), "counter without capability");
    u.has_killing_frenzy = true;
    assert!(u.is_killing_frenzy_active());
    u.killing_frenzy_turns = 0;
    assert!(!u.is_killing_frenzy_active(), "capability without counter");
    u.killing_frenzy_turns = 1;
    u.fled = true;
    assert!(!u.is_killing_frenzy_active(), "escaped");
    u.fled = false;
    u.alive = false;
    assert!(!u.is_killing_frenzy_active(), "dead");
}

// ----------------------------------------------------------------------------- activation

#[test]
fn lethal_paid_melee_activates_with_an_unboosted_killing_blow() {
    for (actor, victims) in DUELS {
        for blow in [false, true] {
            let mut s = arena(actor, victims);
            let ev = strike(&mut s, blow, actor, victims[0], HIT);
            assert_eq!(dmg_of(&ev), BASE_DAMAGE, "the killing blow uses the pre-kill modifier");
            assert!(!s.units[victims[0] as usize].alive);
            assert_eq!(counter(&s, actor), 2);
            // The next attack benefits immediately.
            s.units[actor as usize].ap = 9;
            let ev = strike(&mut s, blow, actor, victims[1], HIT);
            assert_eq!(dmg_of(&ev), BOOSTED_DAMAGE, "blow={blow}");
        }
    }
}

#[test]
fn no_event_is_added_and_rng_use_is_identical_with_or_without_the_capability() {
    for (actor, victims) in DUELS {
        let run = |capable: bool| {
            let mut s = arena(actor, victims);
            s.units[actor as usize].has_killing_frenzy = capable;
            rig(&mut s, &[Some(HIT)]);
            let ev = s.apply(Command::Attack { attacker: actor, target: victims[0] }).unwrap();
            (ev, s.rng.clone())
        };
        let (with, rng_with) = run(true);
        let (without, rng_without) = run(false);
        assert_eq!(with, without);
        assert_eq!(rng_with, rng_without);
        // The boosted follow-up draws exactly as the unboosted one does.
        for capable in [true, false] {
            let mut s = arena(actor, victims);
            s.units[actor as usize].has_killing_frenzy = capable;
            strike(&mut s, false, actor, victims[0], HIT);
            s.units[actor as usize].ap = 9;
            rig(&mut s, &[Some(HIT)]);
            let mut expect = s.rng.clone();
            s.apply(Command::Attack { attacker: actor, target: victims[1] }).unwrap();
            expect.next_u64();
            expect.next_u64();
            assert_eq!(s.rng, expect, "hit roll plus one damage draw only");
        }
    }
}

#[test]
fn an_incapable_attacker_does_not_activate() {
    let (actor, victims) = DUELS[0];
    let mut s = arena(actor, victims);
    s.units[actor as usize].has_killing_frenzy = false;
    strike(&mut s, false, actor, victims[0], HIT);
    assert_eq!(counter(&s, actor), 0);
    let ev = strike(&mut s, false, actor, victims[1], HIT);
    assert_eq!(dmg_of(&ev), BASE_DAMAGE);
}

#[test]
fn kill_refreshes_to_two_without_stacking() {
    for (actor, victims) in DUELS {
        for blow in [false, true] {
            let mut s = arena(actor, victims);
            strike(&mut s, blow, actor, victims[0], HIT);
            s.units[actor as usize].ap = 9;
            s.units[victims[1] as usize].hp = 1;
            let ev = strike(&mut s, blow, actor, victims[1], HIT);
            assert_eq!(dmg_of(&ev), BOOSTED_DAMAGE, "a kill while active is itself boosted");
            assert_eq!(counter(&s, actor), 2);
        }
    }
    // A kill with one entry left refreshes back to two.
    let (actor, victims) = DUELS[0];
    let mut s = arena(actor, victims);
    s.units[actor as usize].killing_frenzy_turns = 1;
    strike(&mut s, false, actor, victims[0], HIT);
    assert_eq!(counter(&s, actor), 2);
}

#[test]
fn misses_survivors_and_nine_lives_do_not_activate_or_refresh() {
    for (actor, victims) in DUELS {
        for blow in [false, true] {
            for start in [0u8, 1] {
                // Miss against a 1 HP target.
                let mut s = arena(actor, victims);
                s.units[actor as usize].killing_frenzy_turns = start;
                strike(&mut s, blow, actor, victims[0], MISS);
                assert!(s.units[victims[0] as usize].alive);
                assert_eq!(counter(&s, actor), start);

                // Hit that leaves the target alive.
                let mut s = arena(actor, victims);
                s.units[actor as usize].killing_frenzy_turns = start;
                strike(&mut s, blow, actor, victims[1], HIT);
                assert!(s.units[victims[1] as usize].alive);
                assert_eq!(counter(&s, actor), start);

                // Nine Lives interception of a lethal hit.
                let mut s = arena(actor, victims);
                s.units[actor as usize].killing_frenzy_turns = start;
                s.units[victims[0] as usize].has_nine_lives = true;
                let ev = strike(&mut s, blow, actor, victims[0], HIT);
                assert!(ev.iter().any(|e| matches!(e, Event::NineLivesTriggered { .. })));
                assert!(s.units[victims[0] as usize].alive);
                assert_eq!(counter(&s, actor), start);
            }
        }
    }
}

#[test]
fn opportunity_kills_neither_activate_nor_refresh_and_active_frenzy_modifies_them() {
    for (mover, controller) in [(P1, E1), (E1, P1)] {
        let dest = *ACTOR_AT
            .neighbors()
            .iter()
            .find(|h| map_hexes().contains(h) && !h.is_obstacle() && h.distance(VICTIM_AT[0]) == 2)
            .expect("a retreat hex");
        for start in [0u8, 1] {
            // Lethal opportunity attack.
            let mut s = board(mover, &[(mover, ACTOR_AT), (controller, VICTIM_AT[0])]);
            s.units[controller as usize].has_killing_frenzy = true;
            s.units[controller as usize].killing_frenzy_turns = start;
            s.units[controller as usize].turn_started = true; // its next entry is not fresh
            s.units[mover as usize].hp = 1;
            rig(&mut s, &[Some(HIT)]);
            let ev = s.apply(Command::Move { unit: mover, to: dest }).unwrap();
            assert!(!s.units[mover as usize].alive, "the mover was killed");
            assert!(ev.iter().any(|e| matches!(e, Event::Died { .. })));
            assert_eq!(counter(&s, controller), start);

            // Surviving mover: the damage shows whether the bonus was active.
            let mut s = board(mover, &[(mover, ACTOR_AT), (controller, VICTIM_AT[0])]);
            s.units[controller as usize].has_killing_frenzy = true;
            s.units[controller as usize].killing_frenzy_turns = start;
            s.units[controller as usize].turn_started = true; // its next entry is not fresh
            rig(&mut s, &[Some(HIT)]);
            let ev = s.apply(Command::Move { unit: mover, to: dest }).unwrap();
            assert_eq!(dmg_of(&ev), if start > 0 { BOOSTED_DAMAGE } else { BASE_DAMAGE });
            assert_eq!(counter(&s, controller), start, "not refreshed");
        }
    }
}

#[test]
fn berserk_refund_enables_a_boosted_follow_up_attack() {
    for (actor, victims) in DUELS {
        for blow in [false, true] {
            // Exactly one paid action's AP; the 4 AP refund then pays for an ordinary attack.
            let ap = if blow { STUNNING_BLOW_AP } else { ATTACK_AP };
            let mut s = arena(actor, victims);
            s.units[actor as usize].ap = ap;
            s.units[actor as usize].has_berserk = true;
            let ev = strike(&mut s, blow, actor, victims[0], HIT);
            assert_eq!(dmg_of(&ev), BASE_DAMAGE);
            let pos = |f: fn(&Event) -> bool| ev.iter().position(f).unwrap();
            assert!(pos(|e| matches!(e, Event::Died { .. })) < pos(|e| matches!(e, Event::BerserkTriggered { .. })));
            assert_eq!(counter(&s, actor), 2);
            assert_eq!(s.units[actor as usize].ap, ATTACK_AP, "only the refund remains");
            let ev = strike(&mut s, false, actor, victims[1], HIT);
            assert_eq!(dmg_of(&ev), BOOSTED_DAMAGE);

            // Control: without Berserk the same follow-up is not affordable.
            let mut s = arena(actor, victims);
            s.units[actor as usize].ap = ap;
            strike(&mut s, blow, actor, victims[0], HIT);
            assert_eq!(s.apply(paid(false, actor, victims[1])), Err(RuleError::NotEnoughAp));
        }
    }
}

#[test]
fn killing_a_last_enemy_still_sets_the_counter_before_the_outcome() {
    let mut s = board(P1, &[(P1, ACTOR_AT), (E1, VICTIM_AT[0])]);
    for id in [E2, E3] {
        s.units[id as usize].alive = false;
        s.queue.retain(|&q| q != id);
    }
    s.units[P1 as usize].has_killing_frenzy = true;
    s.units[E1 as usize].hp = 1;
    let ev = strike(&mut s, false, P1, E1, HIT);
    assert!(ev.iter().any(|e| matches!(e, Event::BattleEnded { .. })));
    assert_eq!(counter(&s, P1), 2);
}

// ----------------------------------------------------------------------------- lifecycle

/// P1 (capable, initiative 100) and P2 (initiative 200) with E1 parked and E2 as the victim;
/// P3 and E3 are dead.
fn lifecycle() -> BattleState {
    let mut s = arena(P1, [E2, E3]);
    s.units[P2 as usize].stats.initiative = 200;
    s.units[P1 as usize].stats.initiative = 100;
    for id in [E1, E2, E3] {
        s.units[id as usize].stats.initiative = 50;
    }
    for id in [2, E3] {
        s.units[id as usize].alive = false; // P3 and E3 are not part of the scenario
    }
    s.queue = vec![P1, P2, E1, E2];
    s.units[E2 as usize].hp = 1;
    s
}

#[test]
fn counter_ages_once_per_fresh_entry() {
    let mut s = lifecycle();
    strike(&mut s, false, P1, E2, HIT);
    assert_eq!(counter(&s, P1), 2);
    end_turn(&mut s); // P1 -> P2
    end_turn(&mut s); // P2 -> E1 (other units' turns change nothing)
    assert_eq!(counter(&s, P1), 2);
    end_turn(&mut s); // E1 -> round 2: P2 is the first fresh entry, then P1
    assert_eq!(s.round, 2);
    assert_eq!(s.queue[0], P2);
    assert_eq!(counter(&s, P1), 2, "the round rollover and P2's fresh entry do not age P1");
    end_turn(&mut s); // P2 -> P1's fresh entry
    assert_eq!(s.queue[0], P1);
    assert_eq!(counter(&s, P1), 1, "the next fresh turn still has the bonus");
    assert!(s.units[P1 as usize].is_killing_frenzy_active());
    end_turn(&mut s); // P1 -> E1
    end_turn(&mut s); // E1 -> round 3, P2
    end_turn(&mut s); // P2 -> P1's following fresh entry
    assert_eq!((s.round, s.queue[0]), (3, P1));
    assert_eq!(counter(&s, P1), 0, "expired at the following fresh entry");
    assert!(!s.units[P1 as usize].is_killing_frenzy_active());
}

#[test]
fn wait_returns_do_not_age_the_counter() {
    let mut s = lifecycle();
    strike(&mut s, false, P1, E2, HIT);
    s.apply(Command::Wait { unit: P1 }).unwrap();
    assert_eq!(s.queue, vec![P2, E1, P1]);
    end_turn(&mut s);
    end_turn(&mut s);
    assert_eq!((s.round, s.queue[0]), (1, P1), "P1 returns in the same round");
    assert_eq!(counter(&s, P1), 2);
    // Still boosted on the returned portion of the turn.
    s.units[E1 as usize].pos = VICTIM_AT[2];
    s.units[P1 as usize].ap = 9;
    let ev = strike(&mut s, false, P1, E1, HIT);
    assert_eq!(dmg_of(&ev), BOOSTED_DAMAGE);
}

#[test]
fn a_skipped_fresh_entry_ages_the_counter() {
    let mut s = lifecycle();
    strike(&mut s, false, P1, E2, HIT);
    end_turn(&mut s);
    end_turn(&mut s);
    end_turn(&mut s); // round 2, P2 first
    assert_eq!((s.round, s.queue[0]), (2, P2));
    assert_eq!(counter(&s, P1), 2);
    s.apply(Command::EndRound { unit: P2 }).unwrap(); // marks P1 to be skipped
    assert!(s.units[P1 as usize].turn_started && s.units[P1 as usize].ap == 0);
    assert_eq!(s.queue[0], E1, "P1's entry was consumed");
    assert_eq!(counter(&s, P1), 1);
}

#[test]
fn a_stunned_fresh_entry_ages_the_counter() {
    let mut s = lifecycle();
    strike(&mut s, false, P1, E2, HIT);
    end_turn(&mut s);
    end_turn(&mut s);
    end_turn(&mut s); // round 2, P2 first
    assert_eq!(counter(&s, P1), 2);
    s.apply_stun(P1).unwrap();
    assert_eq!(counter(&s, P1), 2, "stunning alone does not age it");
    end_turn(&mut s); // P2 -> P1's stunned entry is consumed
    assert!(!s.units[P1 as usize].stunned);
    assert_eq!(s.queue[0], E1);
    assert_eq!(counter(&s, P1), 1);
}

#[test]
fn counter_saturates_at_zero_and_ages_without_capability() {
    let mut s = lifecycle();
    s.units[P2 as usize].killing_frenzy_turns = 5; // incapable units still age
    s.units[P1 as usize].killing_frenzy_turns = 0;
    end_turn(&mut s); // P1 -> P2's fresh entry
    assert_eq!(counter(&s, P2), 4);
    end_turn(&mut s);
    end_turn(&mut s);
    end_turn(&mut s); // round 2: P2's fresh entry again
    assert_eq!((s.round, s.queue[0]), (2, P2));
    assert_eq!(counter(&s, P2), 3);
    end_turn(&mut s); // P1's fresh entry
    assert_eq!(counter(&s, P1), 0);
}

// ----------------------------------------------------------------------------- previews

/// The sampled base value of the next hit: the roll draw, then `min + draw % width`.
fn expected_base(rng: &Rng, min: u32, max: u32) -> u32 {
    let mut r = rng.clone();
    r.next_u64();
    let width = u64::from(max) - u64::from(min) + 1;
    (u64::from(min) + r.next_u64() % width) as u32
}

#[test]
fn both_previews_match_actual_damage_for_every_arithmetic_case() {
    let near_limit = (u64::from(u32::MAX) * 4 / 5) as u32; // 125 % lands exactly on u32::MAX
    let ranges: [(u32, u32); 12] = [
        (0, 0),
        (1, 1),
        (3, 3),
        (4, 4),
        (7, 7),
        (10, 10),
        (10, 19),
        (near_limit, near_limit),
        (near_limit + 1, near_limit + 1),
        (u32::MAX, u32::MAX),
        (0, u32::MAX),
        (u32::MAX - 5, u32::MAX),
    ];
    for (actor, victims) in DUELS {
        for blow in [false, true] {
            for active in [false, true] {
                for indomitable in [false, true] {
                    for (min, max) in ranges {
                        let mut s = arena(actor, victims);
                        let target = victims[1];
                        let a = &mut s.units[actor as usize];
                        a.stats.dmg_min = min;
                        a.stats.dmg_max = max;
                        a.killing_frenzy_turns = u8::from(active);
                        let t = &mut s.units[target as usize];
                        t.has_indomitable = indomitable;
                        t.indomitable_active = indomitable;
                        assert_fixture_valid(&s);

                        let reduce = |v: u32| if indomitable { v / 2 } else { v };
                        let tf = |v: u32| reduce(if active { boosted(v) } else { v });
                        let before = snapshot(&s);
                        let preview = if blow { s.preview_stunning_blow(actor, target) } else { s.preview_attack(actor, target) }
                            .unwrap();
                        assert_eq!(snapshot(&s), before, "queries are pure");
                        let _ = s.legal_stunning_blow_targets(actor);
                        let _ = s.legal_targets(actor);
                        assert_eq!(snapshot(&s), before);
                        assert_eq!((preview.damage_min, preview.damage_max), (tf(min), tf(max)), "{min}..={max}");

                        rig(&mut s, &[Some(HIT)]);
                        let base = expected_base(&s.rng, min, max);
                        let dmg = dmg_of(&s.apply(paid(blow, actor, target)).unwrap());
                        assert_eq!(dmg, tf(base), "{min}..={max} base {base}");
                        assert!((preview.damage_min..=preview.damage_max).contains(&dmg));
                    }
                }
            }
        }
    }
}

#[test]
fn rounding_saturation_and_composition_examples() {
    assert_eq!([0, 1, 3, 4, 7, 10].map(boosted), [0, 1, 3, 5, 8, 12]);
    assert_eq!(boosted(u32::MAX), u32::MAX);
    // Frenzy is applied before the Indomitable halving: 5 -> 6 -> 3 (halving first would give 2).
    let (actor, victims) = DUELS[0];
    let mut s = arena(actor, victims);
    s.units[actor as usize].stats.dmg_min = 5;
    s.units[actor as usize].stats.dmg_max = 5;
    s.units[actor as usize].killing_frenzy_turns = 1;
    let t = &mut s.units[victims[1] as usize];
    t.has_indomitable = true;
    t.indomitable_active = true;
    let p = s.preview_attack(actor, victims[1]).unwrap();
    assert_eq!((p.damage_min, p.damage_max), (3, 3));
    // A 100-armour-free target loses exactly the preview damage plus no armour leak.
    let ev = strike(&mut s, false, actor, victims[1], HIT);
    assert_eq!(dmg_of(&ev), 3);
}

#[test]
fn previews_and_rejections_leave_the_state_untouched() {
    let (actor, victims) = DUELS[0];
    let mut s = arena(actor, victims);
    s.units[actor as usize].killing_frenzy_turns = 2;
    // Out of reach target, wrong target and unaffordable commands are rejected unchanged.
    let before = snapshot(&s);
    let far = E3;
    assert_eq!(s.apply(Command::Attack { attacker: actor, target: far }), Err(RuleError::NotAdjacent));
    assert_eq!(s.apply(Command::StunningBlow { attacker: actor, target: far }), Err(RuleError::NotAdjacent));
    assert_eq!(s.apply(Command::Attack { attacker: actor, target: P2 }), Err(RuleError::InvalidTarget));
    assert_eq!(snapshot(&s), before);
    s.units[actor as usize].ap = 0;
    let before = snapshot(&s);
    assert_eq!(s.apply(Command::Attack { attacker: actor, target: victims[0] }), Err(RuleError::NotEnoughAp));
    assert_eq!(s.preview_attack(actor, victims[0]), Err(RuleError::NotEnoughAp));
    assert_eq!(snapshot(&s), before);
}

// ----------------------------------------------------------------------------- hashing

#[test]
fn capability_and_counter_hash_distinctions() {
    let base = BattleState::new(1);
    let mut capable = base.clone();
    capable.units[0].has_killing_frenzy = true;
    assert_ne!(capable.state_hash(), base.state_hash());
    let mut other = base.clone();
    other.units[1].has_killing_frenzy = true;
    assert_ne!(other.state_hash(), capable.state_hash());

    // Every u8 counter value gives a distinct hash, with and without the capability.
    for with_capability in [false, true] {
        let mut seen = std::collections::HashSet::new();
        for c in 0..=u8::MAX {
            let mut s = base.clone();
            s.units[0].has_killing_frenzy = with_capability;
            s.units[0].killing_frenzy_turns = c;
            assert!(seen.insert(s.state_hash()), "counter {c} collides");
        }
        assert!(seen.contains(&{
            let mut s = base.clone();
            s.units[0].has_killing_frenzy = with_capability;
            s.state_hash()
        }));
    }

    // The same counter on different units, and the same unit set in a different place, differ.
    let mut a = base.clone();
    a.units[0].killing_frenzy_turns = 1;
    let mut b = base.clone();
    b.units[1].killing_frenzy_turns = 1;
    assert_ne!(a.state_hash(), b.state_hash());
    let mut both = a.clone();
    both.units[1].killing_frenzy_turns = 1;
    assert_ne!(both.state_hash(), a.state_hash());
}

#[test]
fn counter_suffix_is_tagged_counted_and_appended_last() {
    let mut s = BattleState::new(1);
    s.ground = Default::default();
    let zero = s.state_hash();
    assert_eq!(zero, FLAT_SEED_1_HASH, "all-zero counters append nothing");
    s.units[1].killing_frenzy_turns = 7;
    s.units[4].killing_frenzy_turns = 255;
    // FNV-1a continues from the hash state, so the suffix can be mirrored independently.
    let mut h = zero;
    for v in [KILLING_FRENZY_HASH_TAG, 2, 1, 7, 4, 255] {
        for byte in v.to_le_bytes() {
            h = (h ^ u64::from(byte)).wrapping_mul(0x0000_0100_0000_01b3);
        }
    }
    assert_eq!(s.state_hash(), h);

    // The capability bit alone is bit 57 of the flag word: it must not touch the suffix.
    let mut t = BattleState::new(1);
    t.units[2].has_killing_frenzy = true;
    assert_ne!(t.state_hash(), zero);

    // Terrain and elevation presets keep the suffix last and unchanged when counters are zero.
    let hills = scenario::battle(1, TerrainPreset::Hills);
    assert_eq!(hills.state_hash(), HILLS_SCENARIO_HASH);
    let mut with = hills.clone();
    with.units[0].killing_frenzy_turns = 1;
    assert_ne!(with.state_hash(), HILLS_SCENARIO_HASH);
}
