//! Headless stun effect lifecycle (partial UC-COMBAT-028/016). Catalog-stated: a stunned
//! unit skips its turn and exerts no zone of control, and Indomitable grants stun
//! immunity. APPROXIMATION: boolean storage, the next-queue-entry duration, scheduling of
//! an active target, the Wait / EndRound / Fleeing / retreating interactions, event order
//! and the hash encoding (flag bit 55). Fixtures keep only the units they need present
//! (the rest are dead), at distinct passable hexes, with resources inside the Stats maxima;
//! the first queued unit is the already started active unit. No skill inflicts stun yet,
//! so every test drives `BattleState::apply_stun` directly.

use bb_rules::hex::{Hex, hex, map_hexes};
use bb_rules::rules::{FATIGUE_REGEN, LAST_ROUND};
use bb_rules::scenario::{self, TerrainPreset};
use bb_rules::*;

const P1: u32 = 0;
const P2: u32 = 1;
const E1: u32 = 3;
const E2: u32 = 4;

/// Hash of `BattleState::new(1)` before stun existed.
const FLAT_SEED_1_HASH: u64 = 10_088_116_083_934_094_952;

/// P1 active, then P2, E1, E2, all distant except the P2/E1 pair, which is adjacent.
const QUAD: [(u32, Hex); 4] = [(P1, hex(-3, 3)), (P2, hex(0, 2)), (E1, hex(1, 2)), (E2, hex(3, -3))];

fn started(unit: u32) -> Event {
    Event::TurnStarted { unit }
}

fn board(placed: &[(u32, Hex)]) -> BattleState {
    let mut s = BattleState::new(1);
    for u in &mut s.units {
        u.alive = false;
        u.turn_started = false; // `BattleState::new` already started unit 0
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

/// Everything a rejected or no-op call must leave alone.
fn snapshot(s: &BattleState) -> (u64, Rng, Vec<Unit>, Vec<u32>, u32, Option<Outcome>) {
    (s.state_hash(), s.rng.clone(), s.units.clone(), s.queue.clone(), s.round, s.outcome())
}

fn end_turn(s: &mut BattleState) -> Vec<Event> {
    let unit = s.active_unit().expect("an active unit").id;
    s.apply(Command::EndTurn { unit }).unwrap()
}

fn activate(s: &mut BattleState, id: u32) {
    s.queue.retain(|&q| q != id);
    s.queue.insert(0, id);
    let u = &mut s.units[id as usize];
    u.ap = 9;
    u.turn_started = true;
}

fn active_id(s: &BattleState) -> Option<u32> {
    s.active_unit().map(|u| u.id)
}

fn first_draw(seed: u64) -> u32 {
    (Rng::new(seed).next_u64() % 100) as u32
}

fn rng_after_one_draw(seed: u64) -> Rng {
    let mut r = Rng::new(seed);
    r.next_u64();
    r
}

// ------------------------------------------------------------- defaults and hash

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
            | bit(u.stunned, 55));
        put(u64::from(u.stats.resolve));
    }
    h
}

#[test]
fn units_and_scenarios_start_unstunned_and_keep_their_hash() {
    let fresh = Unit::new(9, "X", Side::Enemy, scenario::units()[3].stats.clone(), hex(0, -2));
    assert!(!fresh.stunned);
    for preset in [TerrainPreset::Flat, TerrainPreset::Hills, TerrainPreset::Swamp] {
        assert!(scenario::battle(1, preset).units.iter().all(|u| !u.stunned));
    }
    let s = BattleState::new(1);
    assert!(s.units.iter().all(|u| !u.stunned));
    assert_eq!(s.state_hash(), FLAT_SEED_1_HASH);
    assert_eq!(reference_hash(&s), FLAT_SEED_1_HASH);
}

#[test]
fn stunned_flag_is_hash_bit_55_for_each_unit() {
    let base = BattleState::new(1);
    let mut seen = vec![base.state_hash()];
    for id in 0..base.units.len() {
        let mut s = base.clone();
        s.units[id].stunned = true;
        let h = s.state_hash();
        assert_eq!(h, reference_hash(&s), "unit {id}");
        assert!(!seen.contains(&h), "unit {id} must hash distinctly");
        seen.push(h);
        s.units[id].stunned = false;
        assert_eq!(s.state_hash(), FLAT_SEED_1_HASH);
    }
}

// ------------------------------------------------------------- application and rejection

#[test]
fn inactive_target_of_either_faction_is_stunned_without_other_change() {
    for target in [P2, E1] {
        let mut s = board(&QUAD);
        s.units[target as usize].fatigue = 33;
        s.units[target as usize].hp -= 7;
        let mut expected = s.clone();
        let rng_before = s.rng.clone();
        let ev = s.apply_stun(target).unwrap();
        assert_eq!(ev, vec![Event::StunApplied { unit: target }]);
        expected.units[target as usize].stunned = true;
        assert_eq!(s.units, expected.units, "only the flag changes");
        assert_eq!((s.queue.clone(), s.round, s.rng.clone()), (expected.queue.clone(), 1, rng_before));
        assert_eq!(active_id(&s), Some(P1), "an inactive target keeps its pending position");
        assert_eq!(s.state_hash(), expected.state_hash());
        assert_ne!(s.state_hash(), board(&QUAD).state_hash());
    }
}

#[test]
fn reapplying_to_a_stunned_target_is_an_idempotent_success() {
    for target in [P2, E1] {
        let mut s = board(&QUAD);
        s.apply_stun(target).unwrap();
        let before = snapshot(&s);
        assert_eq!(s.apply_stun(target), Ok(vec![]));
        assert_eq!(snapshot(&s), before, "no new events, no duration stacking");
        // A single entry is consumed: the stun is gone after one visit.
        let mut expired = 0;
        while s.round == 1 {
            expired += end_turn(&mut s).iter().filter(|e| **e == Event::StunExpired { unit: target }).count();
        }
        assert_eq!(expired, 1);
        assert!(!s.units[target as usize].stunned);
    }
}

#[test]
fn rejections_leave_state_and_rng_unchanged() {
    let mut s = board(&QUAD);
    s.units[E2 as usize].alive = false;
    s.units[P2 as usize].fled = true;
    for bad in [99, E2, P2, u32::MAX] {
        let before = snapshot(&s);
        assert_eq!(s.apply_stun(bad), Err(RuleError::InvalidTarget), "{bad}");
        assert_eq!(snapshot(&s), before);
    }
    s.outcome = Some(Outcome::Victory);
    s.end_reason = Some(EndReason::Elimination);
    for target in [P1, E1, E2, 99] {
        let before = snapshot(&s);
        assert_eq!(s.apply_stun(target), Err(RuleError::BattleOver), "BattleOver comes first for {target}");
        assert_eq!(snapshot(&s), before);
    }
}

// ------------------------------------------------------------- Indomitable immunity

fn with_stance(s: &mut BattleState, id: u32, capability: bool, active: bool) {
    let u = &mut s.units[id as usize];
    u.has_indomitable = capability;
    u.indomitable_active = active;
}

#[test]
fn effective_indomitable_resists_for_active_and_inactive_targets() {
    for target in [P1, P2, E1] {
        let mut s = board(&QUAD);
        with_stance(&mut s, target, true, true);
        assert!(s.units[target as usize].is_indomitable_active());
        let before = snapshot(&s);
        assert_eq!(s.apply_stun(target), Ok(vec![Event::StunResisted { unit: target }]));
        assert_eq!(snapshot(&s), before, "no status, resource, queue (even for the active unit) or RNG change");
        assert_eq!(active_id(&s), Some(P1));
    }
}

#[test]
fn effective_indomitable_resists_even_an_already_stunned_target() {
    let mut s = board(&QUAD);
    s.apply_stun(E1).unwrap();
    with_stance(&mut s, E1, true, true);
    let before = snapshot(&s);
    assert_eq!(s.apply_stun(E1), Ok(vec![Event::StunResisted { unit: E1 }]));
    assert_eq!(snapshot(&s), before);
    assert!(s.units[E1 as usize].stunned, "resisting does not clear an existing stun");
}

#[test]
fn ineffective_indomitable_does_not_resist() {
    let cases: [(&str, bool, bool, Morale); 3] = [
        ("capability without stance", true, false, Morale::Steady),
        ("stance flag without capability", false, true, Morale::Steady),
        ("Fleeing", true, true, Morale::Fleeing),
    ];
    for (name, capability, active, morale) in cases {
        let mut s = board(&QUAD);
        with_stance(&mut s, E1, capability, active);
        s.units[E1 as usize].morale = morale;
        assert!(!s.units[E1 as usize].is_indomitable_active(), "{name}");
        assert_eq!(s.apply_stun(E1), Ok(vec![Event::StunApplied { unit: E1 }]), "{name}");
        assert!(s.units[E1 as usize].stunned);
    }
}

// ------------------------------------------------------------- zone of control

/// `mover` on the lane beside `ctrl`; `dest` is two steps directly away from `ctrl`.
fn lane(mover: u32, ctrl: u32) -> (BattleState, Hex) {
    let (mover_at, ctrl_at, dest) =
        if mover == P1 { (hex(1, -2), hex(2, -2), hex(-1, -2)) } else { (hex(0, -2), hex(-1, -2), hex(2, -2)) };
    let far = if mover == P1 { (E2, hex(2, 2)) } else { (P2, hex(-2, 2)) };
    let mut s = board(&[(mover, mover_at), (ctrl, ctrl_at), far]);
    s.units[mover as usize].fatigue = 0;
    (s, dest)
}

#[test]
fn stunned_unit_exerts_no_zone_of_control_even_with_two_hex_reach() {
    for (mover, ctrl) in [(P1, E1), (E1, P1)] {
        let (mut s, dest) = lane(mover, ctrl);
        s.units[ctrl as usize].melee_reach = MeleeReach::TwoHex;
        let at = s.units[mover as usize].pos;
        assert_eq!(s.enemy_controllers_at(mover, at), vec![ctrl]);
        assert!(s.preview_move(mover, dest).unwrap().first_threat.is_some());
        s.apply_stun(ctrl).unwrap();
        assert_eq!(s.enemy_controllers_at(mover, at), Vec::<u32>::new());
        assert_eq!(s.preview_move(mover, dest).unwrap().first_threat, None);
    }
}

#[test]
fn leaving_a_stunned_controller_draws_no_opportunity_attack() {
    for (mover, ctrl) in [(P1, E1), (E1, P1)] {
        let (mut free, dest) = lane(mover, ctrl);
        let mut stunned = free.clone();
        stunned.apply_stun(ctrl).unwrap();
        let rng_before = stunned.rng.clone();
        let ev = stunned.apply(Command::Move { unit: mover, to: dest }).unwrap();
        assert!(matches!(ev.as_slice(), [Event::Moved { unit, .. }] if *unit == mover), "{ev:?}");
        assert_eq!(stunned.rng, rng_before, "no reaction roll");
        assert_eq!(stunned.units[mover as usize].pos, dest);
        // Control: the same move without stun provokes an attack.
        let ev = free.apply(Command::Move { unit: mover, to: dest }).unwrap();
        assert!(ev.iter().any(|e| matches!(e, Event::Attacked { attacker, .. } if *attacker == ctrl)), "{ev:?}");
    }
}

#[test]
fn stunned_units_stay_targetable_damageable_and_block_hexes() {
    for (attacker, target) in [(P1, E1), (E1, P1)] {
        let (mut s, _) = lane(attacker, target);
        let stats_before = s.units[target as usize].stats.clone();
        let (chance, defense) = (s.hit_chance(attacker, target), s.units[target as usize].effective_defense());
        s.apply_stun(target).unwrap();
        assert_eq!(s.units[target as usize].stats, stats_before);
        assert_eq!(s.units[target as usize].effective_defense(), defense);
        assert_eq!(s.hit_chance(attacker, target), chance, "stun changes no defensive stat");
        assert_eq!(s.legal_targets(attacker), vec![target]);
        assert!(s.preview_attack(attacker, target).is_ok());
        let blocked = s.units[target as usize].pos;
        assert!(s.reachable(attacker).iter().all(|&(h, _)| h != blocked), "still occupies its hex");
        // A guaranteed hit (rigged seed) on an unarmoured target deals the same positive
        // damage as against an otherwise identical unstunned control.
        let seed = (1..).find(|&n| first_draw(n) < chance).unwrap();
        s.units[attacker as usize].stats.dmg_min = 10;
        s.units[attacker as usize].stats.dmg_max = 10;
        s.units[target as usize].armour = 0;
        s.units[target as usize].stats.armour = 0;
        s.rng = Rng::new(seed);
        let mut control = s.clone();
        control.units[target as usize].stunned = false;
        let hp_before = s.units[target as usize].hp;
        let ev = s.apply(Command::Attack { attacker, target }).unwrap();
        let control_ev = control.apply(Command::Attack { attacker, target }).unwrap();
        let hit = ev
            .iter()
            .find_map(|e| match e {
                Event::Attacked { target: t, hit, hp_loss, .. } if *t == target => Some((*hit, *hp_loss)),
                _ => None,
            })
            .expect("an Attacked event");
        assert!(hit.0 && hit.1 > 0, "the rigged roll hits and costs HP: {hit:?}");
        assert_eq!(ev, control_ev, "stun changes no attack outcome");
        assert_eq!(s.units[target as usize].hp, hp_before - hit.1);
        assert_eq!(s.units[target as usize].hp, control.units[target as usize].hp);
        assert!(s.units[target as usize].stunned, "damage does not end the stun");
    }
}

// ------------------------------------------------------------- scheduling

#[test]
fn fresh_skipped_turn_pins_event_order_and_resources() {
    let mut s = board(&QUAD);
    s.units[E1 as usize].fatigue = 30;
    s.units[E1 as usize].hp -= 5;
    s.apply_stun(E1).unwrap();
    assert!(s.enemy_controllers_at(P2, s.units[P2 as usize].pos).is_empty());
    let (rng, hp, armour) = (s.rng.clone(), s.units[E1 as usize].hp, s.units[E1 as usize].armour);
    assert_eq!(
        end_turn(&mut s),
        vec![started(P2)],
        "P2 is before E1 in the queue and is unaffected"
    );
    let ev = end_turn(&mut s);
    assert_eq!(ev, vec![started(E1), Event::StunExpired { unit: E1 }, started(E2)]);
    let e1 = &s.units[E1 as usize];
    assert_eq!((e1.ap, e1.fatigue, e1.stunned), (0, 30 - FATIGUE_REGEN, false));
    assert_eq!((e1.hp, e1.armour, s.rng.clone()), (hp, armour, rng), "no damage, no RNG");
    assert_eq!(s.queue, vec![E2]);
    assert_eq!(active_id(&s), Some(E2));
    assert_eq!(s.enemy_controllers_at(P2, s.units[P2 as usize].pos), vec![E1], "ZoC returns after expiry");
}

#[test]
fn active_target_is_forfeited_immediately_in_order() {
    for starter in [P1, E1] {
        let order = if starter == P1 { [(P1, hex(-3, 3)), (E1, hex(3, -3)), (P2, hex(-4, 3))] } else { [(E1, hex(3, -3)), (P1, hex(-3, 3)), (E2, hex(4, -3))] };
        let mut s = board(&order);
        let next = order[1].0;
        let u = &mut s.units[starter as usize];
        (u.ap, u.fatigue) = (5, 10);
        let (hp, armour, rng) = (u.hp, u.armour, s.rng.clone());
        let ev = s.apply_stun(starter).unwrap();
        assert_eq!(ev, vec![Event::StunApplied { unit: starter }, Event::StunExpired { unit: starter }, started(next)]);
        let u = &s.units[starter as usize];
        assert_eq!((u.ap, u.fatigue, u.stunned, u.hp, u.armour), (0, 10, false, hp, armour));
        assert_eq!(s.rng, rng);
        assert_eq!(active_id(&s), Some(next));
        assert!(!s.queue.contains(&starter));
        assert_ne!(active_id(&s), Some(starter), "no stunned actor remains active");
    }
}

#[test]
fn wait_return_consumes_stun_without_a_second_refresh() {
    let mut s = board(&QUAD);
    s.apply(Command::Wait { unit: P1 }).unwrap();
    assert_eq!(s.queue, vec![P2, E1, E2, P1]);
    s.units[P1 as usize].fatigue = 20;
    s.apply_stun(P1).unwrap();
    assert_eq!(s.queue, vec![P2, E1, E2, P1], "pending queue position kept");
    end_turn(&mut s);
    end_turn(&mut s);
    let ev = end_turn(&mut s);
    // P1's fatigue 20 lowers its round-2 initiative below P2's.
    assert_eq!(ev, vec![Event::StunExpired { unit: P1 }, Event::RoundStarted { round: 2 }, started(P2)]);
    let p1 = &s.units[P1 as usize];
    assert_eq!((p1.stunned, p1.ap, p1.fatigue), (false, 0, 20), "the Wait return got no refresh");
    let ev = end_turn(&mut s);
    assert_eq!(ev, vec![started(P1)]);
    assert_eq!(s.units[P1 as usize].fatigue, 20 - FATIGUE_REGEN, "the round-2 visit regenerates once");
}

#[test]
fn stun_applied_after_a_completed_turn_survives_the_round_rollover() {
    let mut s = board(&QUAD);
    s.units[P1 as usize].fatigue = 20; // round-2 order: P2, P1, E1, E2
    end_turn(&mut s);
    assert!(!s.queue.contains(&P1));
    let ev = s.apply_stun(P1).unwrap();
    assert_eq!(ev, vec![Event::StunApplied { unit: P1 }], "nothing to forfeit this round");
    assert_eq!(s.queue, vec![P2, E1, E2]);
    end_turn(&mut s);
    end_turn(&mut s);
    assert!(s.units[P1 as usize].stunned, "begin_round must not clear a pending stun");
    let ev = end_turn(&mut s);
    assert_eq!(ev, vec![Event::RoundStarted { round: 2 }, started(P2)]);
    let ev = end_turn(&mut s);
    assert_eq!(ev, vec![started(P1), Event::StunExpired { unit: P1 }, started(E1)]);
    let p1 = &s.units[P1 as usize];
    assert_eq!((p1.ap, p1.fatigue, p1.stunned), (0, 20 - FATIGUE_REGEN, false));
    assert_eq!((s.round, active_id(&s)), (2, Some(E1)));
}

#[test]
fn consecutive_stunned_actors_each_consume_one_entry() {
    let mut s = board(&QUAD);
    s.apply_stun(P2).unwrap();
    s.apply_stun(E1).unwrap();
    let ev = end_turn(&mut s);
    assert_eq!(
        ev,
        vec![
            started(P2),
            Event::StunExpired { unit: P2 },
            started(E1),
            Event::StunExpired { unit: E1 },
            started(E2),
        ]
    );
    assert_eq!(s.queue, vec![E2]);
    assert!(s.units.iter().all(|u| !u.stunned));
}

#[test]
fn every_queued_actor_stunned_rolls_into_the_next_round() {
    let mut s = board(&QUAD);
    for id in [P2, E1, E2] {
        s.apply_stun(id).unwrap();
    }
    let ev = s.apply_stun(P1).unwrap();
    assert_eq!(
        ev,
        vec![
            Event::StunApplied { unit: P1 },
            Event::StunExpired { unit: P1 },
            started(P2),
            Event::StunExpired { unit: P2 },
            started(E1),
            Event::StunExpired { unit: E1 },
            started(E2),
            Event::StunExpired { unit: E2 },
            Event::RoundStarted { round: 2 },
            started(P1),
        ]
    );
    assert!(s.units.iter().all(|u| !u.stunned));
    assert_eq!((s.round, active_id(&s)), (2, Some(P1)));
    assert_eq!(s.units[P1 as usize].ap, 9, "the new round's visit is a normal turn");
}

#[test]
fn stun_consumed_at_the_time_limit_ends_the_battle() {
    let mut s = board(&QUAD);
    s.round = LAST_ROUND;
    for id in [P2, E1, E2] {
        s.apply_stun(id).unwrap();
    }
    let ev = s.apply_stun(P1).unwrap();
    assert_eq!(
        ev.last(),
        Some(&Event::BattleEnded { outcome: Outcome::Defeat, reason: EndReason::TimeLimit })
    );
    assert_eq!(ev.iter().filter(|e| matches!(e, Event::StunExpired { .. })).count(), 4);
    assert_eq!((s.round, s.outcome(), s.end_reason()), (LAST_ROUND, Some(Outcome::Defeat), Some(EndReason::TimeLimit)));
    assert!(s.active_unit().is_none());
    assert_eq!(s.apply_stun(P1), Err(RuleError::BattleOver));
}

// ------------------------------------------------------------- EndRound

#[test]
fn stun_and_end_round_skip_consume_a_single_entry() {
    let mut s = board(&QUAD);
    s.apply_stun(P2).unwrap();
    let ev = s.apply(Command::EndRound { unit: P1 }).unwrap();
    assert_eq!(ev, vec![started(P2), Event::StunExpired { unit: P2 }, started(E1)]);
    assert_eq!(active_id(&s), Some(E1), "no extra lost turn: the next eligible actor is E1");
    assert_eq!(s.queue, vec![E1, E2]);
    assert!(!s.units[P2 as usize].stunned);
    end_turn(&mut s);
    let ev = end_turn(&mut s);
    assert_eq!(ev, vec![Event::RoundStarted { round: 2 }, started(P1)]);
    let ev = end_turn(&mut s);
    assert_eq!(ev, vec![started(P2)], "the skip was per round; P2 plays round 2");
    assert_eq!(active_id(&s), Some(P2));
}

#[test]
fn end_round_without_stun_is_unchanged() {
    let mut s = board(&QUAD);
    let ev = s.apply(Command::EndRound { unit: P1 }).unwrap();
    assert_eq!(ev, vec![started(P2), started(E1)]);
    assert_eq!(active_id(&s), Some(E1));
}

// ------------------------------------------------------------- fleeing and retreating

#[test]
fn fleeing_stunned_unit_forfeits_its_turn_after_one_rally_check() {
    let seeds: Vec<u64> = (1..200).filter(|&n| first_draw(n) >= 50).take(1).chain((1..200).filter(|&n| first_draw(n) < 50).take(1)).collect();
    for seed in seeds {
        let mut s = board(&[(P1, hex(-3, 3)), (E1, hex(0, 2)), (E2, hex(3, -3))]);
        s.rng = Rng::new(seed);
        s.units[E1 as usize].morale = Morale::Fleeing;
        s.units[E1 as usize].fatigue = 30;
        s.apply_stun(E1).unwrap();
        assert_eq!(s.rng, Rng::new(seed), "applying stun draws nothing");
        let ev = end_turn(&mut s);
        let rallied = first_draw(seed) < 50;
        let after = if rallied { Morale::Breaking } else { Morale::Fleeing };
        assert_eq!(
            ev,
            vec![
                started(E1),
                Event::SelfRallyChecked { unit: E1, roll: first_draw(seed), chance: 50, before: Morale::Fleeing, after },
                Event::StunExpired { unit: E1 },
                started(E2),
            ]
        );
        assert_eq!(s.rng, rng_after_one_draw(seed), "exactly the one existing rally draw");
        let e1 = &s.units[E1 as usize];
        assert_eq!((e1.ap, e1.fatigue, e1.stunned, e1.morale), (0, 30 - FATIGUE_REGEN, false, after));
        assert_eq!(active_id(&s), Some(E2), "the Flee turn was forfeited");
    }
}

#[test]
fn stunned_fleeing_unit_on_an_edge_escapes_one_visit_later() {
    let edge = hex(3, -4);
    let mut s = board(&[(P1, hex(-3, 3)), (E1, edge), (E2, hex(3, -3))]);
    s.units[E1 as usize].morale = Morale::Fleeing;
    s.apply_stun(E1).unwrap();
    let ev = end_turn(&mut s);
    assert_eq!(ev, vec![started(E1), Event::StunExpired { unit: E1 }, started(E2)]);
    assert!(s.units[E1 as usize].is_present() && !s.units[E1 as usize].fled, "no free escape while stunned");
    end_turn(&mut s); // E2 ends round 1
    assert_eq!(s.round, 2);
    while active_id(&s) != Some(E1) {
        end_turn(&mut s);
    }
    let ev = s.apply(Command::Flee { unit: E1 }).unwrap();
    assert_eq!(ev.first(), Some(&Event::Fled { unit: E1 }));
    assert!(s.units[E1 as usize].fled);
}

#[test]
fn stunned_retreating_unit_forfeits_withdraw_turns_including_an_edge_escape() {
    for (name, at) in [("interior", hex(-3, 3)), ("edge", hex(-4, 3))] {
        let mut s = board(&[(E1, hex(3, -3)), (P2, at), (E2, hex(2, -3)), (P1, hex(-2, 3))]);
        s.units[P2 as usize].retreating = true;
        s.apply_stun(P2).unwrap();
        let ev = end_turn(&mut s);
        assert_eq!(ev, vec![started(P2), Event::StunExpired { unit: P2 }, started(E2)], "{name}");
        assert_eq!((s.units[P2 as usize].pos, s.units[P2 as usize].fled), (at, false), "{name}");
        end_turn(&mut s);
        end_turn(&mut s);
        assert_eq!(s.round, 2, "{name}");
        while active_id(&s) != Some(P2) {
            end_turn(&mut s);
        }
        let ev = s.apply(Command::Withdraw { unit: P2 }).unwrap();
        if at == hex(-4, 3) {
            assert_eq!(ev.first(), Some(&Event::Fled { unit: P2 }), "{name}: delayed escape on the next visit");
        } else {
            assert!(matches!(ev.first(), Some(Event::Moved { .. })), "{name}: {ev:?}");
        }
    }
}

#[test]
fn retreat_order_exits_are_not_turns_and_ignore_stun() {
    // APPROXIMATION: the party order's immediate edge exits are not a scheduled turn.
    let mut s = board(&[(P1, hex(-3, 3)), (P2, hex(-4, 3)), (E1, hex(3, -3))]);
    s.apply_stun(P2).unwrap();
    let ev = s.apply(Command::Retreat { unit: P1 }).unwrap();
    assert_eq!(ev, vec![Event::RetreatOrdered { unit: P1 }, Event::Fled { unit: P2 }]);
}

// ------------------------------------------------------------- turn-start processing

#[test]
fn fresh_skipped_visit_runs_regeneration_and_passive_resets_once() {
    let mut s = board(&QUAD);
    {
        let e1 = &mut s.units[E1 as usize];
        e1.fatigue = 40;
        e1.berserk_used = true;
        e1.indomitable_active = true; // inert flag (no capability), so stun applies
    }
    let rng = s.rng.clone();
    assert_eq!(s.apply_stun(E1), Ok(vec![Event::StunApplied { unit: E1 }]));
    assert_eq!(s.rng, rng);
    end_turn(&mut s);
    let ev = end_turn(&mut s);
    assert_eq!(ev, vec![started(E1), Event::StunExpired { unit: E1 }, started(E2)]);
    let e1 = &s.units[E1 as usize];
    assert_eq!((e1.fatigue, e1.berserk_used, e1.indomitable_active, e1.ap), (40 - FATIGUE_REGEN, false, false, 0));
    assert!(e1.turn_started);
    assert_eq!(s.rng, rng, "no self-rally draw for a Steady unit");
}

#[test]
fn queue_activation_helper_is_consistent() {
    // Guards the fixture helper used by ad-hoc scenarios: an activated unit leads the queue.
    let mut s = board(&QUAD);
    activate(&mut s, E1);
    assert_eq!(active_id(&s), Some(E1));
    assert_eq!(s.apply_stun(E1).unwrap()[..2], [Event::StunApplied { unit: E1 }, Event::StunExpired { unit: E1 }]);
}
