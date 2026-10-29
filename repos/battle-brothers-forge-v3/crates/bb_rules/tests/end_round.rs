//! `Command::EndRound` (APPROXIMATION based on UC-COMBAT-017, no runtime
//! confirmation): marked player units forfeit their remaining scheduled turn
//! automatically; enemies keep their ordinary turns. Fixtures start from the
//! stock scenario (queue 0,1,2,3,4,5) and rearrange `queue` / positions directly.
//! SplitMix64 draws for seed 1 are 65, 19, 90, ... (mod 100): a 95% attacker hits first.

use bb_rules::hex::hex;
use bb_rules::rules::LAST_ROUND;
use bb_rules::*;

const P1: u32 = 0;
const P2: u32 = 1;
const P3: u32 = 2;
const E1: u32 = 3;
const E2: u32 = 4;
const E3: u32 = 5;

fn started(unit: u32) -> Event {
    Event::TurnStarted { unit }
}

fn skips(s: &BattleState) -> Vec<bool> {
    s.units.iter().map(|u| u.skip_turn).collect()
}

fn with_queue(queue: &[u32]) -> BattleState {
    let mut s = BattleState::new(1);
    s.queue = queue.to_vec();
    s
}

#[test]
fn new_units_have_no_pending_skip() {
    let s = BattleState::new(1);
    assert_eq!(skips(&s), vec![false; 6]);
}

#[test]
fn command_issuer_forfeits_its_own_ap() {
    let mut s = BattleState::new(1);
    assert_eq!(s.units[P1 as usize].ap, 9);
    s.apply(Command::EndRound { unit: P1 }).unwrap();
    assert_eq!(s.units[P1 as usize].ap, 0);
    assert!(s.units[P1 as usize].alive && !s.queue.contains(&P1));
    // Also when the round continues with an enemy still to act.
    assert_eq!(s.active_unit().unwrap().id, E1);
}

#[test]
fn mixed_queue_skips_consecutive_players_and_stops_at_first_enemy() {
    let mut s = BattleState::new(1);
    let rng = s.rng.clone();
    let ev = s.apply(Command::EndRound { unit: P1 }).unwrap();
    // P2 and P3 get their scheduled refresh, are passed over, E1 is the new active unit.
    assert_eq!(ev, vec![started(P2), started(P3), started(E1)]);
    assert_eq!(s.queue, vec![E1, E2, E3]);
    assert_eq!(s.active_unit().unwrap().id, E1);
    assert_eq!(skips(&s), vec![true, true, true, false, false, false]);
    assert_eq!((s.units[0].ap, s.units[1].ap, s.units[2].ap), (0, 0, 0));
    assert!(s.units[1].alive && s.units[2].alive);
    assert_eq!(s.rng, rng);

    // Enemies need their ordinary commands, in their existing order.
    assert_eq!(s.apply(Command::EndTurn { unit: E1 }).unwrap(), vec![started(E2)]);
    assert_eq!(s.apply(Command::EndTurn { unit: E2 }).unwrap(), vec![started(E3)]);
    let ev = s.apply(Command::EndTurn { unit: E3 }).unwrap();
    assert_eq!(ev, vec![Event::RoundStarted { round: 2 }, started(P1)]);
    assert_eq!(s.round, 2);
    assert_eq!(skips(&s), vec![false; 6]);
    assert_eq!(s.queue, vec![P1, P2, P3, E1, E2, E3]);
    assert_eq!(s.units[P1 as usize].ap, s.units[P1 as usize].stats.ap_max);
}

#[test]
fn interleaved_queue_passes_over_only_marked_players() {
    let mut s = with_queue(&[P1, E1, P2, E2, P3, E3]);
    let ev = s.apply(Command::EndRound { unit: P1 }).unwrap();
    assert_eq!(ev, vec![started(E1)]);
    // EndTurn honors the pending skip.
    assert_eq!(s.apply(Command::EndTurn { unit: E1 }).unwrap(), vec![started(P2), started(E2)]);
    assert_eq!(s.apply(Command::EndTurn { unit: E2 }).unwrap(), vec![started(P3), started(E3)]);
    assert_eq!(s.queue, vec![E3]);
}

#[test]
fn enemy_wait_honors_pending_skip() {
    let mut s = with_queue(&[P1, E1, P2, E2, P3, E3]);
    s.apply(Command::EndRound { unit: P1 }).unwrap();
    let ev = s.apply(Command::Wait { unit: E1 }).unwrap();
    assert_eq!(ev, vec![started(P2), started(E2)]);
    assert_eq!(s.queue, vec![E2, P3, E3, E1]);
    assert_eq!(s.apply(Command::EndTurn { unit: E2 }).unwrap(), vec![started(P3), started(E3)]);
    assert_eq!(s.apply(Command::EndTurn { unit: E3 }).unwrap(), vec![]);
    assert_eq!(s.active_unit().unwrap().id, E1);
    assert!(s.units[E1 as usize].waited);
}

#[test]
fn players_deferred_by_wait_are_marked_and_get_no_second_refresh() {
    let mut s = BattleState::new(1);
    s.apply(Command::Wait { unit: P1 }).unwrap();
    assert_eq!(s.queue, vec![P2, P3, E1, E2, E3, P1]);
    s.units[P1 as usize].fatigue = 20;
    let (ap_before, fatigue_before) = (s.units[P1 as usize].ap, s.units[P1 as usize].fatigue);
    assert!(ap_before > 0);

    let ev = s.apply(Command::EndRound { unit: P2 }).unwrap();
    assert_eq!(ev, vec![started(P3), started(E1)]);
    assert_eq!(skips(&s), vec![true, true, true, false, false, false]);

    s.apply(Command::EndTurn { unit: E1 }).unwrap();
    s.apply(Command::EndTurn { unit: E2 }).unwrap();
    // The waited P1 is passed over with no TurnStarted: no refresh, no regeneration, AP forfeited.
    let ev = s.apply(Command::EndTurn { unit: E3 }).unwrap();
    assert_eq!(ev, vec![Event::RoundStarted { round: 2 }, started(P2)]);
    assert_eq!((s.units[P1 as usize].ap, s.units[P1 as usize].fatigue), (0, fatigue_before));
    assert!(!s.units[P1 as usize].skip_turn);
    // Round 2 is P1's next scheduled turn (fatigue 20 -> initiative 90, ties by id): one regeneration there.
    assert_eq!(s.queue, vec![P2, P3, P1, E1, E2, E3]);
    s.apply(Command::EndTurn { unit: P2 }).unwrap();
    assert_eq!(s.apply(Command::EndTurn { unit: P3 }).unwrap(), vec![started(P1)]);
    assert_eq!(s.units[P1 as usize].fatigue, fatigue_before - 15);
    assert_eq!(s.units[P1 as usize].ap, 9);
}

#[test]
fn already_finished_players_are_not_marked() {
    let mut s = BattleState::new(1);
    s.apply(Command::EndTurn { unit: P1 }).unwrap();
    s.apply(Command::EndRound { unit: P2 }).unwrap();
    assert_eq!(skips(&s), vec![false, true, true, false, false, false]);
    s.apply(Command::EndTurn { unit: E1 }).unwrap();
    s.apply(Command::EndTurn { unit: E2 }).unwrap();
    let ev = s.apply(Command::EndTurn { unit: E3 }).unwrap();
    assert_eq!(ev, vec![Event::RoundStarted { round: 2 }, started(P1)]);
    // P1 finished before the command, so it is a normal player in round 2.
    assert_eq!(s.active_unit().unwrap().id, P1);
}

#[test]
fn dead_players_are_not_marked() {
    let mut s = BattleState::new(1);
    s.units[P3 as usize].alive = false;
    s.queue.retain(|&q| q != P3);
    s.apply(Command::EndRound { unit: P1 }).unwrap();
    assert!(!s.units[P3 as usize].skip_turn);
}

#[test]
fn scheduled_regeneration_uses_fatigue_gained_before_the_skipped_turn() {
    let mut s = with_queue(&[P1, E1, E2, E3, P2, P3]);
    s.units[E1 as usize].pos = hex(-2, 2); // adjacent to P3 (-3,2) and P1 (-3,3)
    s.units[E1 as usize].stats.melee_skill = 200;
    s.units[P3 as usize].fatigue = 30;
    s.apply(Command::EndRound { unit: P1 }).unwrap();
    let ev = s.apply(Command::Attack { attacker: E1, target: P3 }).unwrap();
    let Event::Attacked { hit, .. } = ev[0] else { panic!("expected an attack: {ev:?}") };
    let after_attack = 30 + if hit { 5 } else { 2 };
    assert_eq!(s.units[P3 as usize].fatigue, after_attack);
    assert!(s.units[P3 as usize].skip_turn);
    s.apply(Command::EndTurn { unit: E1 }).unwrap();
    s.apply(Command::EndTurn { unit: E2 }).unwrap();
    assert_eq!(s.units[P2 as usize].fatigue, 0);
    let ev = s.apply(Command::EndTurn { unit: E3 }).unwrap();
    assert_eq!(&ev[..2], [started(P2), started(P3)]);
    // Regeneration used the post-attack value, not the value at the time of EndRound.
    assert_eq!(s.units[P3 as usize].fatigue, after_attack - 15);
    assert_eq!(s.units[P3 as usize].ap, 0);
}

#[test]
fn enemies_act_normally_and_marked_players_keep_opportunity_attacks() {
    let mut s = with_queue(&[P1, E1, P2, P3, E2, E3]);
    s.units[E1 as usize].pos = hex(-2, 2); // adjacent to P3 (-3,2) and P1 (-3,3)
    s.units[P3 as usize].stats.melee_skill = 200;
    s.units[P1 as usize].stats.melee_skill = 200;
    s.apply(Command::EndRound { unit: P1 }).unwrap();
    assert_eq!(s.active_unit().unwrap().id, E1);
    let ev = s.apply(Command::Move { unit: E1, to: hex(-1, 2) }).unwrap();
    let attackers: Vec<u32> = ev
        .iter()
        .filter_map(|e| if let Event::Attacked { attacker, target: 3, .. } = e { Some(*attacker) } else { None })
        .collect();
    assert!(attackers.contains(&P1) && attackers.contains(&P3), "{ev:?}");
    // Reaction fire is free; the forfeited AP stays zero and the units are alive.
    assert_eq!(s.units[P3 as usize].ap, 0);
    assert!(s.units[P3 as usize].alive && s.units[P3 as usize].skip_turn);
    // The enemy can also attack a marked player normally.
    let mut t = with_queue(&[P1, E1, P2, P3, E2, E3]);
    t.units[E1 as usize].pos = hex(-2, 2);
    t.apply(Command::EndRound { unit: P1 }).unwrap();
    let ev = t.apply(Command::Attack { attacker: E1, target: P3 }).unwrap();
    assert!(matches!(ev[0], Event::Attacked { attacker: E1, target: P3, .. }));
}

#[test]
fn active_enemy_death_from_reaction_honors_pending_skips() {
    let mut s = with_queue(&[P1, E1, P2, P3, E2, E3]);
    s.units[E1 as usize].pos = hex(-2, 2);
    s.units[E1 as usize].hp = 1;
    s.units[P1 as usize].stats.melee_skill = 200; // 95%: the first draw (65) hits
    s.apply(Command::EndRound { unit: P1 }).unwrap();
    let ev = s.apply(Command::Move { unit: E1, to: hex(-1, 2) }).unwrap();
    let died = ev.iter().position(|e| *e == Event::Died { unit: E1 }).expect("E1 dies");
    // E2 and E3 each take an ally-death check straight after Died, then P1's kill check, then the normal skips.
    assert!(matches!(ev[died + 1], Event::AllyDeathMoraleChecked { unit: E2, fallen: E1, .. }), "{ev:?}");
    assert!(matches!(ev[died + 2], Event::AllyDeathMoraleChecked { unit: E3, fallen: E1, .. }), "{ev:?}");
    assert!(matches!(ev[died + 3], Event::KillMoraleChecked { unit: P1, fallen: E1, .. }), "{ev:?}");
    assert_eq!(ev[died + 4..], [started(P2), started(P3), started(E2)]);
    assert_eq!(s.queue, vec![E2, E3]);
    assert!(s.units[P2 as usize].alive && s.units[P3 as usize].alive);
}

#[test]
fn next_round_when_only_marked_players_remain() {
    let mut s = with_queue(&[P1, P2, P3]);
    s.units[P1 as usize].fatigue = 40;
    let ev = s.apply(Command::EndRound { unit: P1 }).unwrap();
    assert_eq!(ev, vec![started(P2), started(P3), Event::RoundStarted { round: 2 }, started(P2)]);
    assert_eq!(s.round, 2);
    assert_eq!(skips(&s), vec![false; 6]);
    // P1 forfeited its turn with 40 fatigue (the regeneration comes at its next
    // scheduled turn), so the new queue ranks it by 100 - 40 / 2 = 80, after the enemies.
    assert_eq!(s.active_unit().unwrap().id, P2);
    assert_eq!(s.queue, vec![P2, P3, E1, E2, E3, P1]);
    assert_eq!(s.queue.len(), 6);
}

#[test]
fn new_round_gives_normal_player_control() {
    let mut s = with_queue(&[P1, P2, P3]);
    s.apply(Command::EndRound { unit: P1 }).unwrap();
    let active = s.active_unit().unwrap().id;
    assert_eq!(s.units[active as usize].side, Side::Player);
    assert_eq!(s.units[active as usize].ap, 9);
    assert_eq!(s.apply(Command::EndTurn { unit: active }).unwrap().len(), 1);
    assert_eq!(skips(&s), vec![false; 6]);
    let next = s.active_unit().unwrap().id;
    assert_eq!(s.apply(Command::Wait { unit: next }).map(|e| e.len()), Ok(1));
}

#[test]
fn final_round_exhaustion_ends_battle_without_spurious_turns() {
    let mut s = with_queue(&[P1, P2, P3]);
    s.round = LAST_ROUND;
    let ev = s.apply(Command::EndRound { unit: P1 }).unwrap();
    assert_eq!(
        ev,
        vec![started(P2), started(P3), Event::BattleEnded { outcome: Outcome::Defeat, reason: EndReason::TimeLimit }]
    );
    assert_eq!(s.round, LAST_ROUND);
    assert_eq!(s.outcome(), Some(Outcome::Defeat));
    assert_eq!(s.end_reason(), Some(EndReason::TimeLimit));
    assert!(s.active_unit().is_none());
    let hash = s.state_hash();
    for cmd in [
        Command::EndRound { unit: P1 },
        Command::EndRound { unit: E1 },
        Command::EndTurn { unit: P1 },
        Command::Wait { unit: P1 },
    ] {
        assert_eq!(s.apply(cmd), Err(RuleError::BattleOver));
    }
    assert_eq!(s.state_hash(), hash);
}

#[test]
fn final_round_with_enemies_remaining_waits_for_their_commands() {
    let mut s = with_queue(&[P1, P2, E1]);
    s.round = LAST_ROUND;
    assert_eq!(s.apply(Command::EndRound { unit: P1 }).unwrap(), vec![started(P2), started(E1)]);
    let ev = s.apply(Command::EndTurn { unit: E1 }).unwrap();
    assert_eq!(ev, vec![Event::BattleEnded { outcome: Outcome::Defeat, reason: EndReason::TimeLimit }]);
}

#[test]
fn rejections_leave_state_and_rng_unchanged() {
    let mut s = BattleState::new(1);
    let (hash, rng) = (s.state_hash(), s.rng.clone());
    // Inactive player and inactive enemy: NotActiveUnit, checked before the side rule.
    assert_eq!(s.apply(Command::EndRound { unit: P2 }), Err(RuleError::NotActiveUnit));
    assert_eq!(s.apply(Command::EndRound { unit: E1 }), Err(RuleError::NotActiveUnit));
    assert_eq!((s.state_hash(), &s.rng), (hash, &rng));

    // Active enemy.
    let mut s = with_queue(&[E1, P1, P2, P3, E2, E3]);
    let (hash, rng) = (s.state_hash(), s.rng.clone());
    assert_eq!(s.apply(Command::EndRound { unit: E1 }), Err(RuleError::NotPlayerUnit));
    assert_eq!((s.state_hash(), &s.rng), (hash, &rng));
    assert_eq!(skips(&s), vec![false; 6]);
    assert_eq!(s.queue, vec![E1, P1, P2, P3, E2, E3]);
}

#[test]
fn battle_over_takes_precedence_for_every_actor() {
    let mut s = BattleState::new(1);
    s.outcome = Some(Outcome::Victory);
    for unit in [P1, P2, E1] {
        assert_eq!(s.apply(Command::EndRound { unit }), Err(RuleError::BattleOver));
    }
}

#[test]
fn end_round_and_automatic_skips_draw_no_rng() {
    let mut s = with_queue(&[P1, E1, P2, P3, E2, E3]);
    let rng = s.rng.clone();
    s.apply(Command::EndRound { unit: P1 }).unwrap();
    s.apply(Command::EndTurn { unit: E1 }).unwrap();
    s.apply(Command::EndTurn { unit: E2 }).unwrap();
    s.apply(Command::EndTurn { unit: E3 }).unwrap();
    assert_eq!(s.rng, rng);
}

#[test]
fn state_hash_sees_pending_skip_intent_only_when_set() {
    let a = BattleState::new(1);
    let mut b = a.clone();
    assert_eq!(a.state_hash(), b.state_hash());
    b.units[P2 as usize].skip_turn = true;
    assert_ne!(a.state_hash(), b.state_hash());
    let mut c = a.clone();
    c.units[P3 as usize].skip_turn = true;
    assert_ne!(b.state_hash(), c.state_hash());
    // The flag bit is distinct from waited / turn_started.
    let mut d = a.clone();
    d.units[P2 as usize].waited = true;
    assert_ne!(b.state_hash(), d.state_hash());
}
