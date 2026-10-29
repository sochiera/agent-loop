//! Headless `Command::StunningBlow`. APPROXIMATION: the skill name, capability flag, costs
//! (6 AP / 25 fatigue), reuse of the generic Stats damage range and hit chance, adjacent-only
//! targeting and the event timing are independent slice design. A guaranteed stun conditional on
//! a hit is inspired by the catalog's mastered-mace behaviour (UC-COMBAT-050/068); no mastery or
//! original-runtime parity is implemented. Fixtures keep only the units they need present (the
//! rest are dead) at distinct passable hexes with resources inside the Stats maxima, unless a test
//! deliberately probes an out-of-range value; the first queued unit is the started active unit.

use bb_rules::hex::{Hex, hex, map_hexes};
use bb_rules::rules::{
    ATTACK_AP, ATTACK_FATIGUE, BERSERK_AP_REFUND, DEFENDER_HIT_FATIGUE_GAIN, DEFENDER_MISS_FATIGUE_GAIN,
    STUNNING_BLOW_AP, STUNNING_BLOW_FATIGUE,
};
use bb_rules::*;

const P1: u32 = 0;
const P2: u32 = 1;
const P3: u32 = 2;
const E1: u32 = 3;
const E2: u32 = 4;
const E3: u32 = 5;

/// Hash of `BattleState::new(1)` before the skill capability existed.
const FLAT_SEED_1_HASH: u64 = 10_088_116_083_934_094_952;

const A_AT: Hex = hex(0, 2);
const T_AT: Hex = hex(1, 2);

fn started(unit: u32) -> Event {
    Event::TurnStarted { unit }
}

fn board(placed: &[(u32, Hex)]) -> BattleState {
    let mut s = BattleState::new(1);
    for u in &mut s.units {
        u.alive = false;
        u.turn_started = false;
    }
    for &(id, pos) in placed {
        let u = &mut s.units[id as usize];
        u.alive = true;
        u.pos = pos;
        u.ap = 0;
        u.fatigue = 0;
    }
    s.queue = placed.iter().map(|p| p.0).collect();
    let active = &mut s.units[placed[0].0 as usize];
    active.ap = 9;
    active.turn_started = true;
    assert_fixture_valid(&s);
    s
}

fn assert_fixture_valid(s: &BattleState) {
    let present: Vec<&Unit> = s.units.iter().filter(|u| u.is_present()).collect();
    for (i, u) in present.iter().enumerate() {
        assert!(map_hexes().contains(&u.pos) && !u.pos.is_obstacle(), "{}: bad hex", u.name);
        assert!(present[..i].iter().all(|o| o.pos != u.pos), "{}: shared hex", u.name);
        assert!(u.hp > 0 && u.hp <= u.stats.hp && u.armour <= u.stats.armour, "{}", u.name);
        assert!(u.ap <= u.stats.ap_max && u.fatigue <= u.stats.fatigue_max, "{}", u.name);
    }
}

fn snapshot(s: &BattleState) -> (u64, Rng, Vec<Unit>, Vec<u32>, u32, Option<Outcome>) {
    (s.state_hash(), s.rng.clone(), s.units.clone(), s.queue.clone(), s.round, s.outcome())
}

fn blow(attacker: u32, target: u32) -> Command {
    Command::StunningBlow { attacker, target }
}

fn other_side(id: u32) -> (u32, u32, u32) {
    // (a far unit of the attacker's side, a far unit of the target's side, unused)
    if id < 3 { (P3, E3, 0) } else { (E3, P3, 0) }
}

/// Attacker `a` beside target `t`, a far bystander of the target's side, and a far ally of the
/// attacker. The attacker is capable and its hit chance is the 95 % cap; the damage is fixed.
fn duel(a: u32, t: u32) -> BattleState {
    let (ally, bystander, _) = other_side(a);
    let far = |id: u32| if id < 3 { hex(-3, 3) } else { hex(3, -3) };
    let mut s = board(&[(a, A_AT), (t, T_AT), (bystander, far(bystander)), (ally, far(ally))]);
    // The ally must not share a hex with the bystander on the other side of the map.
    assert_ne!(far(ally), far(bystander));
    let u = &mut s.units[a as usize];
    u.has_stunning_blow = true;
    u.stats.melee_skill = 200;
    u.stats.dmg_min = 20;
    u.stats.dmg_max = 20;
    s.units[t as usize].stats.defense = 0;
    s.units[t as usize].armour = 0;
    s
}

const DUELS: [(u32, u32); 2] = [(P1, E1), (E1, P1)];

fn first_draw(seed: u64) -> u32 {
    (Rng::new(seed).next_u64() % 100) as u32
}

/// A seed whose first draw hits (or misses) against `chance`.
fn seed_for(hit: bool, chance: u32) -> u64 {
    (0u64..).find(|&seed| (first_draw(seed) < chance) == hit).unwrap()
}

fn rig(s: &mut BattleState, a: u32, t: u32, hit: bool) {
    assert_fixture_valid(s); // setup mutations must stay within the Stats maxima
    let chance = s.preview_stunning_blow(a, t).unwrap().hit_chance;
    s.rng = Rng::new(seed_for(hit, chance));
}

fn stuns(ev: &[Event]) -> Vec<&Event> {
    ev.iter()
        .filter(|e| matches!(e, Event::StunApplied { .. } | Event::StunResisted { .. } | Event::StunExpired { .. }))
        .collect()
}

// ------------------------------------------------------------- defaults, constants, hash

/// Independent FNV-1a mirror of the documented serialization (flat, all-Ground map).
fn reference_hash(s: &BattleState) -> u64 {
    assert!(s.elevations.is_flat() && s.ground.is_all_ground());
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    let mut put = |v: u64| {
        for b in v.to_le_bytes() {
            h = (h ^ u64::from(b)).wrapping_mul(0x0000_0100_0000_01b3);
        }
    };
    put(s.rng.state);
    put(u64::from(s.round));
    put(s.queue.len() as u64);
    s.queue.iter().for_each(|&q| put(u64::from(q)));
    put(match s.outcome {
        None => 0,
        Some(Outcome::Victory) => 1,
        Some(Outcome::Defeat) => 2,
        Some(Outcome::Retreat) => 3,
    });
    put(match s.end_reason {
        None => 0,
        Some(EndReason::Elimination) => 1,
        Some(EndReason::TimeLimit) => 2,
        Some(EndReason::Escape) => 3,
    });
    for u in &s.units {
        let morale = match u.morale {
            Morale::Steady => 0u64,
            Morale::Confident => 1,
            Morale::Wavering => 2,
            Morale::Breaking => 3,
            Morale::Fleeing => 4,
        };
        let bit = |v: bool, n: u32| u64::from(v) << n;
        put(u64::from(u.id));
        put(u64::from(u.hp));
        put(u64::from(u.armour));
        put(u64::from(u.ap));
        put(u64::from(u.fatigue));
        put(u.pos.q as i64 as u64);
        put(u.pos.r as i64 as u64);
        put(bit(u.alive, 0)
            | bit(u.waited, 1)
            | bit(u.turn_started, 2)
            | bit(u.skip_turn, 3)
            | morale << 4
            | bit(u.fled, 7)
            | bit(u.retreating, 8)
            | bit(u.has_adrenaline, 9)
            | bit(u.adrenaline_pending, 10)
            | bit(u.has_rotation, 11)
            | bit(u.has_footwork, 12)
            | bit(u.has_fast_adaptation, 13)
            | u64::from(u.fast_adaptation_stacks) << 14
            | bit(u.has_dodge, 46)
            | bit(u.has_nine_lives, 47)
            | bit(u.nine_lives_used, 48)
            | bit(u.has_berserk, 49)
            | bit(u.berserk_used, 50)
            | bit(u.has_pathfinder, 51)
            | bit(u.has_indomitable, 52)
            | bit(u.indomitable_active, 53)
            | bit(u.melee_reach == MeleeReach::TwoHex, 54)
            | bit(u.stunned, 55)
            | bit(u.has_stunning_blow, 56));
        put(u64::from(u.stats.resolve));
    }
    h
}

#[test]
fn units_default_without_the_capability_and_keep_their_hash() {
    let fresh = Unit::new(9, "X", Side::Enemy, scenario::units()[3].stats.clone(), hex(0, -2));
    assert!(!fresh.has_stunning_blow);
    let s = BattleState::new(1);
    assert!(s.units.iter().all(|u| !u.has_stunning_blow));
    assert_eq!(s.state_hash(), FLAT_SEED_1_HASH);
    assert_eq!(reference_hash(&s), FLAT_SEED_1_HASH);
    assert_eq!((STUNNING_BLOW_AP, STUNNING_BLOW_FATIGUE), (6, 25));
}

#[test]
fn capability_is_hash_bit_56_for_each_unit() {
    let base = BattleState::new(1);
    let mut seen = vec![base.state_hash()];
    for id in 0..base.units.len() {
        let mut s = base.clone();
        s.units[id].has_stunning_blow = true;
        let h = s.state_hash();
        assert_eq!(h, reference_hash(&s), "unit {id}");
        assert!(!seen.contains(&h), "unit {id} must hash distinctly");
        seen.push(h);
    }
}

// ------------------------------------------------------------- validation

fn assert_rejected(s: &BattleState, a: u32, t: u32, want: RuleError) {
    assert_fixture_valid(s);
    assert_rejected_probe(s, a, t, want);
}

/// Like `assert_rejected` but without the Stats-maxima check, for the deliberate `u32::MAX`
/// fatigue overflow probes.
fn assert_rejected_probe(s: &BattleState, a: u32, t: u32, want: RuleError) {
    let before = snapshot(s);
    assert_eq!(s.preview_stunning_blow(a, t).unwrap_err(), want);
    assert!(!s.legal_stunning_blow_targets(a).contains(&t), "a rejected pair is not a legal target");
    let mut c = s.clone();
    assert_eq!(c.apply(blow(a, t)).unwrap_err(), want);
    assert_eq!(snapshot(&c), before, "a rejected command changes nothing");
    assert_eq!(snapshot(s), before, "queries are pure");
}

#[test]
fn error_precedence_runs_battle_actor_capability_target_reach_ap_fatigue() {
    for (a, t) in DUELS {
        let (ally, _, _) = other_side(a);
        // Everything wrong at once except battle/actor state, so each fix reveals the next error.
        let mut s = duel(a, t);
        s.units[a as usize].has_stunning_blow = false;
        s.units[a as usize].ap = 0;
        s.units[a as usize].fatigue = 100;
        s.units[t as usize].pos = hex(3, 0);
        assert_rejected(&s, a, t, RuleError::MissingStunningBlow);
        s.units[a as usize].has_stunning_blow = true;
        assert_rejected(&s, a, ally, RuleError::InvalidTarget);
        assert_rejected(&s, a, a, RuleError::InvalidTarget);
        assert_rejected(&s, a, 99, RuleError::InvalidTarget);
        assert_rejected(&s, a, t, RuleError::NotAdjacent);
        s.units[t as usize].pos = T_AT;
        assert_rejected(&s, a, t, RuleError::NotEnoughAp);
        s.units[a as usize].ap = 9;
        assert_rejected(&s, a, t, RuleError::TooFatigued);
        s.units[a as usize].fatigue = 0;
        assert!(s.preview_stunning_blow(a, t).is_ok());
        assert_eq!(s.legal_stunning_blow_targets(a), vec![t]);
    }
}

#[test]
fn actor_gates_precede_capability_and_target_checks() {
    for (a, t) in DUELS {
        let mut s = duel(a, t);
        s.units[a as usize].has_stunning_blow = false;
        // Not the active unit (including an unknown id).
        assert_rejected(&s, t, a, RuleError::NotActiveUnit);
        assert_rejected(&s, 99, t, RuleError::NotActiveUnit);
        // Retreat intent precedes Fleeing, both precede capability.
        let mut r = s.clone();
        r.units[a as usize].morale = Morale::Fleeing;
        assert_rejected(&r, a, t, RuleError::FleeingUnit);
        r.units[a as usize].retreating = true;
        assert_rejected(&r, a, t, RuleError::RetreatingUnit);
        // An active but not present attacker.
        let mut gone = s.clone();
        gone.units[a as usize].alive = false;
        assert_rejected(&gone, a, t, RuleError::NotActiveUnit);
        let mut fled = s.clone();
        fled.units[a as usize].fled = true;
        assert_rejected(&fled, a, t, RuleError::NotActiveUnit);
        // A finished battle wins over everything.
        let mut over = s.clone();
        over.outcome = Some(Outcome::Defeat);
        assert_rejected(&over, a, t, RuleError::BattleOver);
    }
}

#[test]
fn dead_and_escaped_targets_are_invalid() {
    for (a, t) in DUELS {
        let mut dead = duel(a, t);
        dead.units[t as usize].alive = false;
        assert_rejected(&dead, a, t, RuleError::InvalidTarget);
        let mut fled = duel(a, t);
        fled.units[t as usize].fled = true;
        assert_rejected(&fled, a, t, RuleError::InvalidTarget);
    }
}

#[test]
fn exact_ap_and_fatigue_limits() {
    for (a, t) in DUELS {
        let mut s = duel(a, t);
        s.units[a as usize].ap = STUNNING_BLOW_AP - 1;
        assert_rejected(&s, a, t, RuleError::NotEnoughAp);
        s.units[a as usize].ap = STUNNING_BLOW_AP;
        s.units[a as usize].fatigue = s.units[a as usize].stats.fatigue_max - STUNNING_BLOW_FATIGUE + 1;
        assert_rejected(&s, a, t, RuleError::TooFatigued);
        assert!(s.legal_stunning_blow_targets(a).is_empty());
        s.units[a as usize].fatigue = s.units[a as usize].stats.fatigue_max - STUNNING_BLOW_FATIGUE;
        assert!(s.preview_stunning_blow(a, t).is_ok());
        s.apply(blow(a, t)).unwrap();
        let u = &s.units[a as usize];
        assert_eq!((u.ap, u.fatigue), (0, u.stats.fatigue_max));
    }
}

#[test]
fn checked_fatigue_overflow_is_rejected_without_panic() {
    for (a, t) in DUELS {
        let mut s = duel(a, t);
        s.units[a as usize].fatigue = u32::MAX;
        assert_rejected_probe(&s, a, t, RuleError::TooFatigued);
        s.units[a as usize].fatigue = u32::MAX - STUNNING_BLOW_FATIGUE + 1;
        assert_rejected_probe(&s, a, t, RuleError::TooFatigued);
    }
}

#[test]
fn skill_is_adjacent_only_even_with_two_hex_ordinary_reach() {
    for (a, t) in DUELS {
        let mut s = duel(a, t);
        s.units[a as usize].melee_reach = MeleeReach::TwoHex;
        s.units[t as usize].pos = hex(2, 2);
        assert_eq!(Hex::distance(A_AT, hex(2, 2)), 2);
        assert!(s.preview_attack(a, t).is_ok(), "ordinary attack reaches distance two");
        assert_rejected(&s, a, t, RuleError::NotAdjacent);
        assert!(s.legal_stunning_blow_targets(a).is_empty());
        s.units[t as usize].pos = T_AT;
        assert!(s.preview_stunning_blow(a, t).is_ok());
        assert_eq!(s.legal_stunning_blow_targets(a), vec![t]);
    }
}

#[test]
fn target_query_is_stable_and_matches_previews() {
    for (a, t) in DUELS {
        let mut s = board(&[(a, A_AT), (t, T_AT), (if a == P1 { E3 } else { P3 }, hex(0, 1)), (if a == P1 { E2 } else { P2 }, hex(-1, 2))]);
        let side_peers: Vec<u32> = s.units.iter().filter(|u| u.is_present() && u.side != s.units[a as usize].side).map(|u| u.id).collect();
        s.units[a as usize].has_stunning_blow = true;
        let targets = s.legal_stunning_blow_targets(a);
        assert_eq!(targets, side_peers, "ascending id order, all adjacent hostiles");
        assert_eq!(targets, s.legal_stunning_blow_targets(a));
        for &x in &targets {
            assert!(s.preview_stunning_blow(a, x).is_ok());
        }
        let mut sorted = targets.clone();
        sorted.sort_unstable();
        assert_eq!(targets, sorted);
        // Resource or actor failure yields no skill targets.
        s.units[a as usize].ap = 5;
        assert!(s.legal_stunning_blow_targets(a).is_empty());
        assert!(s.legal_stunning_blow_targets(99).is_empty());
        let other = side_peers[0];
        assert!(s.legal_stunning_blow_targets(other).is_empty());
    }
}

// ------------------------------------------------------------- preview and execution agree

#[test]
fn preview_shares_chance_and_damage_with_the_ordinary_attack_but_has_its_own_costs() {
    for (a, t) in DUELS {
        let mut s = duel(a, t);
        s.units[a as usize].stats.dmg_min = 11;
        s.units[a as usize].stats.dmg_max = 29;
        s.units[a as usize].stats.melee_skill = 70;
        let skill = s.preview_stunning_blow(a, t).unwrap();
        let plain = s.preview_attack(a, t).unwrap();
        assert_eq!((skill.attacker, skill.target), (a, t));
        assert_eq!((skill.ap_cost, skill.fatigue_cost), (STUNNING_BLOW_AP, STUNNING_BLOW_FATIGUE));
        assert_eq!((plain.ap_cost, plain.fatigue_cost), (ATTACK_AP, ATTACK_FATIGUE));
        assert_eq!((skill.hit_chance, skill.damage_min, skill.damage_max), (plain.hit_chance, plain.damage_min, plain.damage_max));
        assert_eq!((skill.damage_min, skill.damage_max), (11, 29));
        assert_eq!(skill.hit_chance, s.hit_chance(a, t));
    }
}

#[test]
fn execution_reports_the_previewed_chance_and_charges_the_planned_costs() {
    for (a, t) in DUELS {
        let mut s = duel(a, t);
        s.units[a as usize].fatigue = 7;
        let plan = s.preview_stunning_blow(a, t).unwrap();
        rig(&mut s, a, t, true);
        let ev = s.apply(blow(a, t)).unwrap();
        assert!(matches!(ev[0], Event::Attacked { attacker, target, hit: true, chance, .. } if attacker == a && target == t && chance == plan.hit_chance), "{ev:?}");
        let u = &s.units[a as usize];
        assert_eq!((u.ap, u.fatigue), (9 - plan.ap_cost, 7 + plan.fatigue_cost));
        assert_eq!(s.queue[0], a, "the actor's turn stays active");
    }
}

#[test]
fn previewed_and_unpreviewed_clones_execute_identically_and_queries_are_pure() {
    for (a, t) in DUELS {
        for hit in [true, false] {
            let mut s = duel(a, t);
            rig(&mut s, a, t, hit);
            let before = snapshot(&s);
            let mut plain = s.clone();
            let mut previewed = s.clone();
            let _ = previewed.preview_stunning_blow(a, t).unwrap();
            let _ = previewed.preview_attack(a, t);
            let _ = previewed.legal_stunning_blow_targets(a);
            assert_eq!(snapshot(&previewed), before);
            let e1 = plain.apply(blow(a, t)).unwrap();
            let e2 = previewed.apply(blow(a, t)).unwrap();
            assert_eq!(e1, e2);
            assert_eq!(snapshot(&plain), snapshot(&previewed));
        }
    }
}

// ------------------------------------------------------------- hit and miss streams

#[test]
fn guaranteed_hit_stuns_with_ordinary_damage_and_rng_draws() {
    for (a, t) in DUELS {
        let mut s = duel(a, t);
        s.units[a as usize].stats.dmg_min = 20;
        s.units[a as usize].stats.dmg_max = 24; // forces a damage draw
        s.units[t as usize].fatigue = 10;
        rig(&mut s, a, t, true);
        let mut plain = s.clone();
        plain.units[a as usize].ap = 9;
        let ev = s.apply(blow(a, t)).unwrap();
        let ordinary = plain.apply(Command::Attack { attacker: a, target: t }).unwrap();
        // Same Attacked payload, then the stun, and the same two RNG draws.
        let mut expected = ordinary.clone();
        expected.insert(1, Event::StunApplied { unit: t });
        assert_eq!(ev, expected, "ordinary stream plus the stun right after Attacked");
        assert_eq!(s.rng, plain.rng, "no extra stun draw");
        let target = &s.units[t as usize];
        assert!(target.stunned);
        assert_eq!(target.hp, plain.units[t as usize].hp);
        assert_eq!(target.fatigue, 10 + DEFENDER_HIT_FATIGUE_GAIN);
        assert!(!plain.units[t as usize].stunned, "the ordinary attack never stuns");
        assert_eq!(s.units[a as usize].fatigue, STUNNING_BLOW_FATIGUE);
        assert_eq!(plain.units[a as usize].fatigue, ATTACK_FATIGUE);
    }
}

#[test]
fn miss_pays_costs_adds_miss_fatigue_and_attempts_no_stun() {
    for (a, t) in DUELS {
        let mut s = duel(a, t);
        s.units[a as usize].stats.melee_skill = 50;
        s.units[t as usize].fatigue = 10;
        rig(&mut s, a, t, false);
        let mut plain = s.clone();
        let ev = s.apply(blow(a, t)).unwrap();
        let ordinary = plain.apply(Command::Attack { attacker: a, target: t }).unwrap();
        assert_eq!(ev, ordinary, "a miss is the ordinary miss");
        assert!(matches!(ev.as_slice(), [Event::Attacked { hit: false, .. }]));
        assert_eq!(s.rng, plain.rng, "one draw, no stun draw");
        let (u, d) = (&s.units[a as usize], &s.units[t as usize]);
        assert_eq!((u.ap, u.fatigue), (9 - STUNNING_BLOW_AP, STUNNING_BLOW_FATIGUE));
        assert_eq!(d.fatigue, 10 + DEFENDER_MISS_FATIGUE_GAIN);
        assert!(!d.stunned);
        assert_eq!(d.hp, s.units[t as usize].stats.hp);
        assert_eq!(s.queue[0], a);
    }
}

#[test]
fn fast_adaptation_bookkeeping_follows_the_skill_like_an_ordinary_attack() {
    for (a, t) in DUELS {
        let mut s = duel(a, t);
        s.units[a as usize].has_fast_adaptation = true;
        s.units[a as usize].stats.melee_skill = 50;
        s.units[a as usize].stats.ap_max = 20;
        s.units[a as usize].ap = 20;
        s.units[a as usize].stats.fatigue_max = 200;
        rig(&mut s, a, t, false);
        s.apply(blow(a, t)).unwrap();
        assert_eq!(s.units[a as usize].fast_adaptation_stacks, 1);
        let chance_with_stack = s.preview_stunning_blow(a, t).unwrap().hit_chance;
        assert_eq!(chance_with_stack, s.preview_attack(a, t).unwrap().hit_chance);
        rig(&mut s, a, t, true);
        s.apply(blow(a, t)).unwrap();
        assert_eq!(s.units[a as usize].fast_adaptation_stacks, 0, "any hit resets the stacks");
    }
}

// ------------------------------------------------------------- damage outcomes

#[test]
fn armour_only_and_zero_damage_hits_still_stun() {
    for (a, t) in DUELS {
        for (dmg, armour) in [(5u32, 50u32), (0, 0), (0, 40)] {
            let mut s = duel(a, t);
            s.units[a as usize].stats.dmg_min = dmg;
            s.units[a as usize].stats.dmg_max = dmg;
            s.units[t as usize].stats.armour = armour;
            s.units[t as usize].armour = armour;
            let hp = s.units[t as usize].hp;
            rig(&mut s, a, t, true);
            let ev = s.apply(blow(a, t)).unwrap();
            assert!(matches!(ev[0], Event::Attacked { hit: true, hp_loss: 0, .. }), "{ev:?}");
            assert_eq!(ev[1], Event::StunApplied { unit: t });
            assert_eq!(ev.len(), 2);
            assert_eq!(s.units[t as usize].hp, hp);
            assert!(s.units[t as usize].stunned);
        }
    }
}

#[test]
fn lethal_hit_applies_no_stun_and_uses_death_handling() {
    for (a, t) in DUELS {
        let (_, bystander, _) = other_side(a);
        let mut s = duel(a, t);
        s.units[t as usize].hp = 5;
        rig(&mut s, a, t, true);
        let ev = s.apply(blow(a, t)).unwrap();
        assert!(stuns(&ev).is_empty(), "{ev:?}");
        assert!(matches!(ev[0], Event::Attacked { hit: true, .. }));
        assert_eq!(ev[1], Event::Died { unit: t });
        assert!(matches!(ev[2], Event::AllyDeathMoraleChecked { unit, fallen, .. } if unit == bystander && fallen == t));
        assert!(matches!(ev.last(), Some(Event::KillMoraleChecked { unit, fallen, .. }) if *unit == a && *fallen == t));
        let dead = &s.units[t as usize];
        assert!(!dead.alive && !dead.stunned);
        assert!(!s.queue.contains(&t));
        assert_eq!(s.queue[0], a);
    }
}

#[test]
fn nine_lives_survival_is_stunned_after_its_event_and_before_the_morale_check() {
    for (a, t) in DUELS {
        let mut s = duel(a, t);
        s.units[a as usize].stats.dmg_min = 60;
        s.units[a as usize].stats.dmg_max = 60;
        s.units[t as usize].hp = 40;
        s.units[t as usize].has_nine_lives = true;
        rig(&mut s, a, t, true);
        let ev = s.apply(blow(a, t)).unwrap();
        assert!(matches!(ev[0], Event::Attacked { hit: true, hp_loss: 39, .. }), "{ev:?}");
        assert_eq!(ev[1], Event::NineLivesTriggered { unit: t });
        assert_eq!(ev[2], Event::StunApplied { unit: t });
        assert!(matches!(ev[3], Event::MoraleChecked { unit, .. } if unit == t));
        assert_eq!(ev.len(), 4);
        let target = &s.units[t as usize];
        assert!((target.hp, target.alive, target.stunned) == (1, true, true));
    }
}

#[test]
fn surviving_heavy_hit_orders_stun_before_the_morale_check() {
    for (a, t) in DUELS {
        let mut s = duel(a, t);
        s.units[a as usize].stats.dmg_min = 30;
        s.units[a as usize].stats.dmg_max = 30;
        rig(&mut s, a, t, true);
        let ev = s.apply(blow(a, t)).unwrap();
        assert!(matches!(ev[0], Event::Attacked { hit: true, hp_loss: 30, .. }));
        assert_eq!(ev[1], Event::StunApplied { unit: t });
        assert!(matches!(ev[2], Event::MoraleChecked { .. }));
        assert_eq!(ev.len(), 3, "{ev:?}");
    }
}

// ------------------------------------------------------------- immunity and idempotence

#[test]
fn already_stunned_target_is_unchanged_by_a_second_hit() {
    for (a, t) in DUELS {
        let mut s = duel(a, t);
        s.units[t as usize].stunned = true;
        rig(&mut s, a, t, true);
        let ev = s.apply(blow(a, t)).unwrap();
        assert!(stuns(&ev).is_empty(), "{ev:?}");
        assert!(s.units[t as usize].stunned);
    }
}

#[test]
fn effective_indomitable_resists_the_stun() {
    for (a, t) in DUELS {
        let mut s = duel(a, t);
        s.units[t as usize].has_indomitable = true;
        s.units[t as usize].indomitable_active = true;
        rig(&mut s, a, t, true);
        let ev = s.apply(blow(a, t)).unwrap();
        assert!(matches!(ev[0], Event::Attacked { hit: true, dmg: 10, .. }), "damage is halved: {ev:?}");
        assert_eq!(ev[1], Event::StunResisted { unit: t });
        assert!(!s.units[t as usize].stunned);
        // Ineffective stances (inactive flag, missing capability) do not resist.
        for (cap, active) in [(true, false), (false, true)] {
            let mut s = duel(a, t);
            s.units[t as usize].has_indomitable = cap;
            s.units[t as usize].indomitable_active = active;
            rig(&mut s, a, t, true);
            let ev = s.apply(blow(a, t)).unwrap();
            assert!(ev.contains(&Event::StunApplied { unit: t }), "{ev:?}");
        }
    }
}

#[test]
fn indomitable_is_judged_at_impact_before_the_same_hits_morale_check() {
    for (a, t) in DUELS {
        let mut s = duel(a, t);
        let d = &mut s.units[t as usize];
        d.has_indomitable = true;
        d.indomitable_active = true;
        d.morale = Morale::Breaking;
        d.stats.resolve = 0; // the heavy-hit check fails for certain
        s.units[a as usize].stats.dmg_min = 40;
        s.units[a as usize].stats.dmg_max = 40; // 20 after the stance: a heavy hit
        assert!(s.units[t as usize].is_indomitable_active());
        rig(&mut s, a, t, true);
        let ev = s.apply(blow(a, t)).unwrap();
        assert!(matches!(ev[0], Event::Attacked { hit: true, dmg: 20, hp_loss: 20, .. }), "{ev:?}");
        assert_eq!(ev[1], Event::StunResisted { unit: t });
        assert!(matches!(
            ev[2],
            Event::MoraleChecked { unit, before: Morale::Breaking, after: Morale::Fleeing, .. } if unit == t
        ));
        assert_eq!(ev.len(), 3);
        let d = &s.units[t as usize];
        assert!(!d.stunned && !d.indomitable_active && d.morale == Morale::Fleeing);
        // Control: without the stance the same hit stuns first, then the check runs.
        let mut c = duel(a, t);
        c.units[t as usize].morale = Morale::Breaking;
        c.units[t as usize].stats.resolve = 0;
        c.units[a as usize].stats.dmg_min = 40;
        c.units[a as usize].stats.dmg_max = 40;
        rig(&mut c, a, t, true);
        let ev = c.apply(blow(a, t)).unwrap();
        assert_eq!(ev[1], Event::StunApplied { unit: t });
        assert!(matches!(ev[2], Event::MoraleChecked { after: Morale::Fleeing, .. }));
        assert!(c.units[t as usize].stunned);
    }
}

// ------------------------------------------------------------- stun lifecycle

#[test]
fn stun_removes_zoc_at_once_and_forfeits_the_targets_next_entry_without_ending_the_attackers_turn() {
    for (a, t) in DUELS {
        let (ally, _, _) = other_side(a);
        let mut s = board(&[(a, A_AT), (t, T_AT), (ally, if a == P1 { hex(-3, 3) } else { hex(3, -3) })]);
        let u = &mut s.units[a as usize];
        u.has_stunning_blow = true;
        u.stats.melee_skill = 200;
        u.stats.dmg_min = 5;
        u.stats.dmg_max = 5;
        s.units[t as usize].stats.armour = 50;
        s.units[t as usize].armour = 50;
        let probe = hex(2, 1); // adjacent to the target, away from the attacker's reach
        assert_eq!(s.enemy_controllers_at(a, probe), vec![t]);
        rig(&mut s, a, t, true);
        let ev = s.apply(blow(a, t)).unwrap();
        assert!(ev.contains(&Event::StunApplied { unit: t }));
        assert_eq!(s.enemy_controllers_at(a, probe), Vec::<u32>::new(), "no ZoC immediately");
        assert_eq!(s.queue, vec![a, t, ally], "the attacker's turn is not advanced");
        assert_eq!(s.units[a as usize].ap, 9 - STUNNING_BLOW_AP);
        let ev = s.apply(Command::EndTurn { unit: a }).unwrap();
        assert_eq!(ev, vec![started(t), Event::StunExpired { unit: t }, started(ally)]);
        assert!(!s.units[t as usize].stunned);
        assert_eq!(s.queue, vec![ally]);
    }
}

#[test]
fn a_second_blow_in_the_same_turn_is_possible_with_enough_resources() {
    let mut s = duel(P1, E1);
    s.units[P1 as usize].stats.ap_max = 12;
    s.units[P1 as usize].ap = 12;
    rig(&mut s, P1, E1, true);
    s.apply(blow(P1, E1)).unwrap();
    assert_eq!(s.legal_stunning_blow_targets(P1), vec![E1]);
    s.units[P1 as usize].stats.melee_skill = 200;
    let ev = s.apply(blow(P1, E1)).unwrap();
    assert!(matches!(ev[0], Event::Attacked { .. }));
    assert_eq!(s.units[P1 as usize].ap, 0);
    assert_eq!(s.units[P1 as usize].fatigue, 2 * STUNNING_BLOW_FATIGUE);
}

// ------------------------------------------------------------- Berserk

fn berserk_duel(a: u32, t: u32) -> BattleState {
    let mut s = duel(a, t);
    s.units[a as usize].has_berserk = true;
    s.units[a as usize].stats.ap_max = 20;
    s.units[a as usize].ap = 12;
    s.units[a as usize].stats.fatigue_max = 200;
    s
}

#[test]
fn lethal_skill_kill_triggers_berserk_once_after_death_events() {
    for (a, t) in DUELS {
        let second = if a == P1 { E2 } else { P2 };
        let mut s = berserk_duel(a, t);
        // A second adjacent victim for the same turn.
        s.units[second as usize].alive = true;
        s.units[second as usize].pos = hex(0, 1);
        s.units[second as usize].hp = 5;
        s.units[second as usize].armour = 0;
        s.units[t as usize].hp = 5;
        assert_fixture_valid(&s);
        rig(&mut s, a, t, true);
        let ev = s.apply(blow(a, t)).unwrap();
        let died = ev.iter().position(|e| *e == Event::Died { unit: t }).unwrap();
        let kill_check = ev.iter().position(|e| matches!(e, Event::KillMoraleChecked { .. })).unwrap();
        let berserk = ev.iter().position(|e| matches!(e, Event::BerserkTriggered { .. })).unwrap();
        assert!(died < kill_check && kill_check < berserk && berserk == ev.len() - 1, "{ev:?}");
        assert_eq!(ev[berserk], Event::BerserkTriggered { unit: a, ap_gained: BERSERK_AP_REFUND });
        let u = &s.units[a as usize];
        assert_eq!(u.ap, 12 - STUNNING_BLOW_AP + BERSERK_AP_REFUND);
        assert!(u.berserk_used);
        // The second kill in the same turn is not refunded.
        s.queue.retain(|&q| q != t);
        rig(&mut s, a, second, true);
        let ev = s.apply(blow(a, second)).unwrap();
        assert!(ev.contains(&Event::Died { unit: second }));
        assert!(!ev.iter().any(|e| matches!(e, Event::BerserkTriggered { .. })), "{ev:?}");
    }
}

#[test]
fn berserk_is_not_triggered_by_misses_survivors_or_nine_lives() {
    for (a, t) in DUELS {
        let mut miss = berserk_duel(a, t);
        miss.units[a as usize].stats.melee_skill = 50;
        rig(&mut miss, a, t, false);
        let mut survive = berserk_duel(a, t);
        rig(&mut survive, a, t, true);
        let mut nine = berserk_duel(a, t);
        nine.units[t as usize].hp = 5;
        nine.units[t as usize].has_nine_lives = true;
        rig(&mut nine, a, t, true);
        for mut s in [miss, survive, nine] {
            let ev = s.apply(blow(a, t)).unwrap();
            assert!(!ev.iter().any(|e| matches!(e, Event::BerserkTriggered { .. })), "{ev:?}");
            assert!(!s.units[a as usize].berserk_used);
            assert!(s.units[t as usize].alive);
        }
    }
}

#[test]
fn lethal_skill_kill_that_ends_the_battle_reports_berserk_before_the_outcome() {
    let mut s = berserk_duel(P1, E1);
    for id in [E2, E3] {
        s.units[id as usize].alive = false;
    }
    s.queue.retain(|&q| q != E2 && q != E3);
    s.units[E1 as usize].hp = 5;
    rig(&mut s, P1, E1, true);
    let ev = s.apply(blow(P1, E1)).unwrap();
    let n = ev.len();
    assert!(matches!(ev[n - 2], Event::BerserkTriggered { .. }), "{ev:?}");
    assert!(matches!(ev[n - 1], Event::BattleEnded { outcome: Outcome::Victory, .. }));
}

// ------------------------------------------------------------- ordinary and opportunity attacks

#[test]
fn ordinary_attacks_by_a_capable_unit_never_stun() {
    for (a, t) in DUELS {
        let mut s = duel(a, t);
        rig(&mut s, a, t, true);
        let ev = s.apply(Command::Attack { attacker: a, target: t }).unwrap();
        assert!(stuns(&ev).is_empty() && !s.units[t as usize].stunned, "{ev:?}");
        assert_eq!(s.units[a as usize].fatigue, ATTACK_FATIGUE);
    }
}

#[test]
fn opportunity_attacks_by_a_capable_unit_never_stun() {
    for (mover, ctrl) in [(P1, E1), (E1, P1)] {
        let (mover_at, ctrl_at, dest) =
            if mover == P1 { (hex(1, -2), hex(2, -2), hex(-1, -2)) } else { (hex(0, -2), hex(-1, -2), hex(2, -2)) };
        let far = if mover == P1 { (E2, hex(2, 2)) } else { (P2, hex(-2, 2)) };
        let mut s = board(&[(mover, mover_at), (ctrl, ctrl_at), far]);
        s.units[ctrl as usize].has_stunning_blow = true;
        s.units[ctrl as usize].stats.melee_skill = 200;
        s.units[ctrl as usize].stats.dmg_min = 5;
        s.units[ctrl as usize].stats.dmg_max = 5;
        s.units[mover as usize].stats.armour = 50;
        s.units[mover as usize].armour = 50;
        assert_fixture_valid(&s);
        s.rng = Rng::new(seed_for(true, 95));
        let ev = s.apply(Command::Move { unit: mover, to: dest }).unwrap();
        assert!(ev.iter().any(|e| matches!(e, Event::Attacked { attacker, hit: true, .. } if *attacker == ctrl)), "{ev:?}");
        assert!(stuns(&ev).is_empty(), "{ev:?}");
        assert!(!s.units[mover as usize].stunned);
    }
}

#[test]
fn prior_game_scenarios_are_untouched() {
    let s = BattleState::new(1);
    assert!(s.legal_stunning_blow_targets(P1).is_empty());
    assert_eq!(s.preview_stunning_blow(P1, E1).unwrap_err(), RuleError::MissingStunningBlow);
    assert_eq!(s.state_hash(), FLAT_SEED_1_HASH);
}
