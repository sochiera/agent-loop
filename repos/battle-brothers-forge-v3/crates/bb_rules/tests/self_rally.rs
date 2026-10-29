//! Turn-start self-rally of Fleeing units (partial UC-COMBAT-018/019,
//! APPROXIMATION): eligibility, the one-draw formula, recovery to Breaking,
//! event/RNG ordering and the once-per-round guard. Fixtures clear the stock
//! scenario (`alive = false`) and place only the units they need. The first
//! queued unit is the already started starter; `EndTurn` for it starts the
//! next queued unit's turn, which is where the check happens.

use bb_rules::hex::{hex, Hex};
use bb_rules::*;

const P1: u32 = 0;
const P2: u32 = 1;
const P3: u32 = 2;
const E1: u32 = 3;
const E2: u32 = 4;

const CENTER: Hex = hex(0, 2);
const NEXT: Hex = hex(1, 2);
const FAR: Hex = hex(3, -3);
const STARTER_POS: Hex = hex(-3, 3);

fn started(unit: u32) -> Event {
    Event::TurnStarted { unit }
}

/// Only `placed` units are present, queued in that order; the first one is the
/// active, already started starter (9 AP, no fatigue). Units in `fleeing` are Fleeing.
fn board(seed: u64, placed: &[(u32, Hex)], fleeing: &[u32]) -> BattleState {
    let mut s = BattleState::new(seed);
    for u in &mut s.units {
        u.alive = false;
        u.turn_started = false; // `BattleState::new` already started unit 0
    }
    for &(id, pos) in placed {
        let u = &mut s.units[id as usize];
        u.alive = true;
        u.pos = pos;
    }
    for &id in fleeing {
        s.units[id as usize].morale = Morale::Fleeing;
    }
    s.queue = placed.iter().map(|p| p.0).collect();
    let active = &mut s.units[placed[0].0 as usize];
    active.ap = 9;
    active.fatigue = 0;
    active.turn_started = true;
    for &(id, pos) in placed {
        let u = &s.units[id as usize];
        assert!(u.pos.in_map() && !u.pos.is_obstacle());
        assert!(placed.iter().filter(|p| p.1 == pos).count() == 1, "unit {id} shares a hex");
    }
    s
}

fn first_draw(seed: u64) -> u32 {
    (Rng::new(seed).next_u64() % 100) as u32
}

/// Starter P2, fleeing player `F = P1` at the centre, optional extra units.
fn player_case(seed: u64, extra: &[(u32, Hex)]) -> BattleState {
    let mut placed = vec![(P2, STARTER_POS), (P1, CENTER)];
    placed.extend_from_slice(extra);
    board(seed, &placed, &[P1])
}

/// Starter E2, fleeing enemy `F = E1` at the centre, optional extra units.
fn enemy_case(seed: u64, extra: &[(u32, Hex)]) -> BattleState {
    let mut placed = vec![(E2, FAR), (E1, CENTER)];
    placed.extend_from_slice(extra);
    board(seed, &placed, &[E1])
}

fn end_turn(s: &mut BattleState, unit: u32) -> Vec<Event> {
    s.apply(Command::EndTurn { unit }).unwrap()
}

fn checked(unit: u32, roll: u32, chance: u32, after: Morale) -> Event {
    Event::SelfRallyChecked { unit, roll, chance, before: Morale::Fleeing, after }
}

fn rng_after_one_draw(seed: u64) -> Rng {
    let mut r = Rng::new(seed);
    r.next_u64();
    r
}

// --- Formula, boundary and both sides ----------------------------------------------

#[test]
fn resolve_zero_always_fails_and_consumes_one_draw_for_either_side() {
    for (mut s, f, starter) in [(player_case(1, &[]), P1, P2), (enemy_case(1, &[]), E1, E2)] {
        s.units[f as usize].stats.resolve = 0;
        let ev = end_turn(&mut s, starter);
        assert_eq!(ev, vec![started(f), checked(f, first_draw(1), 0, Morale::Fleeing)]);
        assert_eq!(s.units[f as usize].morale, Morale::Fleeing);
        assert_eq!(s.rng, rng_after_one_draw(1));
    }
}

#[test]
fn resolve_one_hundred_and_above_always_succeed_with_chance_capped_at_one_hundred() {
    for resolve in [100, 101, 250, u32::MAX] {
        for (mut s, f, starter) in [(player_case(7, &[]), P1, P2), (enemy_case(7, &[]), E1, E2)] {
            s.units[f as usize].stats.resolve = resolve;
            let ev = end_turn(&mut s, starter);
            assert_eq!(ev, vec![started(f), checked(f, first_draw(7), 100, Morale::Breaking)], "resolve {resolve}");
            assert_eq!(s.units[f as usize].morale, Morale::Breaking);
            assert_eq!(s.units[f as usize].stats.resolve, resolve, "base Resolve is untouched");
            assert_eq!(s.rng, rng_after_one_draw(7));
        }
    }
}

#[test]
fn roll_equal_to_chance_fails_and_one_point_more_succeeds() {
    let roll = first_draw(3);
    assert!(roll > 0 && roll < 100, "pick a seed with an interior first roll");
    let mut fail = player_case(3, &[]);
    fail.units[P1 as usize].stats.resolve = roll;
    assert_eq!(end_turn(&mut fail, P2)[1], checked(P1, roll, roll, Morale::Fleeing));
    assert_eq!(fail.units[P1 as usize].morale, Morale::Fleeing);

    let mut pass = player_case(3, &[]);
    pass.units[P1 as usize].stats.resolve = roll + 1;
    assert_eq!(end_turn(&mut pass, P2)[1], checked(P1, roll, roll + 1, Morale::Breaking));
    assert_eq!(pass.units[P1 as usize].morale, Morale::Breaking);
}

#[test]
fn success_and_failure_change_nothing_but_morale_and_the_skip_mark() {
    for resolve in [0, 100] {
        let mut s = player_case(5, &[]);
        s.units[P1 as usize].stats.resolve = resolve;
        s.units[P1 as usize].fatigue = 40;
        let mut expected = s.clone();
        end_turn(&mut s, P2);
        // Reference: only the ordinary refresh, then the morale change.
        expected.queue.remove(0);
        expected.units[P2 as usize].ap = 9; // untouched starter state
        let e = &mut expected.units[P1 as usize];
        e.turn_started = true;
        e.ap = e.stats.ap_max;
        e.fatigue = 25;
        if resolve == 100 {
            e.morale = Morale::Breaking;
        }
        expected.rng = rng_after_one_draw(5);
        for id in [P1, P2] {
            let (a, b) = (&s.units[id as usize], &expected.units[id as usize]);
            assert_eq!(
                (a.ap, a.fatigue, a.hp, a.armour, a.morale, a.pos, a.skip_turn, a.turn_started, a.waited),
                (b.ap, b.fatigue, b.hp, b.armour, b.morale, b.pos, b.skip_turn, b.turn_started, b.waited),
                "unit {id}, resolve {resolve}"
            );
        }
        assert_eq!(s.rng, expected.rng);
        assert_eq!(s.queue, vec![P1]);
    }
}

// --- Eligibility -----------------------------------------------------------------------

#[test]
fn adjacent_present_enemies_prevent_the_check_including_fleeing_ones() {
    // Ordinary and fleeing adjacent enemies both block, and consume nothing.
    let mut ordinary = player_case(2, &[(E1, NEXT)]);
    let mut fleeing = player_case(2, &[(E1, NEXT)]);
    fleeing.units[E1 as usize].morale = Morale::Fleeing;
    for s in [&mut ordinary, &mut fleeing] {
        s.units[P1 as usize].stats.resolve = 100;
        let ev = end_turn(s, P2);
        assert_eq!(ev, vec![started(P1)]);
        assert_eq!(s.units[P1 as usize].morale, Morale::Fleeing);
        assert_eq!(s.rng, Rng::new(2), "no draw");
    }
    // The fleeing enemy exerts no ZoC, yet still blocks the rally.
    assert!(fleeing.enemy_controllers_at(P1, CENTER).is_empty());
    // Enemy fleeing unit next to a player: same, from the other side.
    let mut s = enemy_case(2, &[(P1, NEXT)]);
    s.units[E1 as usize].stats.resolve = 100;
    assert_eq!(end_turn(&mut s, E2), vec![started(E1)]);
    assert_eq!(s.rng, Rng::new(2));
}

#[test]
fn distant_dead_and_escaped_enemies_do_not_prevent_the_check() {
    let mut distant = player_case(2, &[(E1, hex(2, 2))]);
    let mut dead = player_case(2, &[(E1, NEXT)]);
    dead.units[E1 as usize].alive = false;
    let mut escaped = player_case(2, &[(E1, NEXT)]);
    escaped.units[E1 as usize].fled = true;
    for s in [&mut distant, &mut dead, &mut escaped] {
        s.units[P1 as usize].stats.resolve = 100;
        let ev = end_turn(s, P2);
        assert_eq!(ev, vec![started(P1), checked(P1, first_draw(2), 100, Morale::Breaking)]);
    }
}

#[test]
fn adjacent_allies_do_not_prevent_the_check() {
    let mut s = player_case(2, &[(P3, NEXT)]);
    s.units[P1 as usize].stats.resolve = 100;
    assert_eq!(end_turn(&mut s, P2).len(), 2);
}

#[test]
fn only_fleeing_units_are_checked_and_ordinary_morale_states_consume_nothing() {
    for morale in [Morale::Steady, Morale::Confident, Morale::Wavering, Morale::Breaking] {
        let mut s = player_case(2, &[]);
        s.units[P1 as usize].morale = morale;
        assert_eq!(end_turn(&mut s, P2), vec![started(P1)]);
        assert_eq!(s.rng, Rng::new(2));
        assert_eq!(s.units[P1 as usize].morale, morale);
    }
}

#[test]
fn edge_units_get_no_draw_and_keep_the_immediate_escape_even_with_zero_ap() {
    let edge = hex(-4, 2);
    for resolve in [0, 100] {
        let mut s = board(4, &[(P2, STARTER_POS), (P1, edge), (E1, FAR)], &[P1]);
        s.units[P1 as usize].stats.resolve = resolve;
        s.units[P1 as usize].stats.ap_max = 0;
        let ev = end_turn(&mut s, P2);
        assert_eq!(ev, vec![started(P1)]);
        assert_eq!(s.rng, Rng::new(4), "edge units are not rolled for");
        assert_eq!(s.units[P1 as usize].ap, 0);
        let ev = s.apply(Command::Flee { unit: P1 }).unwrap();
        assert_eq!(ev[0], Event::Fled { unit: P1 });
        assert!(s.units[P1 as usize].fled && s.units[P1 as usize].morale == Morale::Fleeing);
    }
}

// --- Ordering, recovery and queue -------------------------------------------------------

#[test]
fn turn_started_precedes_self_rally_and_the_refresh_is_ordinary() {
    let mut s = player_case(9, &[]);
    {
        let u = &mut s.units[P1 as usize];
        u.stats.resolve = 100;
        u.ap = 1;
        u.fatigue = 40;
    }
    let ev = end_turn(&mut s, P2);
    assert_eq!(ev, vec![started(P1), checked(P1, first_draw(9), 100, Morale::Breaking)]);
    assert!(!ev.iter().any(|e| matches!(e, Event::MoraleChecked { .. })));
    let u = &s.units[P1 as usize];
    assert_eq!((u.ap, u.fatigue), (9, 25), "refresh and regeneration happen before and independent of the check");
}

#[test]
fn round_start_events_precede_the_check_of_the_new_rounds_first_unit() {
    // P2 and P1 both flee; P3 is ordinary and leads round 2. Only the checked
    // unit that gets its turn started draws, after RoundStarted and TurnStarted.
    let mut s = board(6, &[(P3, STARTER_POS), (P1, CENTER)], &[P1]);
    s.units[P1 as usize].stats.resolve = 100;
    let ev = end_turn(&mut s, P3);
    assert_eq!(ev, vec![started(P1), checked(P1, first_draw(6), 100, Morale::Breaking)]);
    // P1 and P3 both finish round 1; round 2 begins with the original order.
    end_turn(&mut s, P1);
    assert_eq!(s.round, 2);
    assert_eq!(s.rng, rng_after_one_draw(6), "ordinary units draw nothing");
}

#[test]
fn recovery_restores_commands_and_zone_of_control_and_keeps_the_queue_order() {
    let mut s = player_case(1, &[(E1, hex(3, 0)), (P3, hex(-2, 3))]);
    s.units[P1 as usize].stats.resolve = 100;
    let queue_before: Vec<u32> = s.queue.iter().copied().filter(|&id| id != P2).collect();
    assert_eq!(s.enemy_controllers_at(E1, hex(1, 2)).len(), 0, "a fleeing unit exerts no ZoC");
    end_turn(&mut s, P2);
    assert_eq!(s.queue, queue_before, "no reorder after recovery");
    assert_eq!(s.active_unit().unwrap().id, P1);
    assert_eq!(s.enemy_controllers_at(E1, hex(1, 2)), vec![P1], "ZoC is back");
    assert!(s.preview_move(P1, hex(-1, 2)).is_ok());
    s.apply(Command::Move { unit: P1, to: hex(-1, 2) }).unwrap();

    // Recover completes the turn, so keep the follow-up Flee assertion on an
    // independent state where P1 is still active and eligible for commands.
    let mut recovered = s.clone();
    recovered.units[P1 as usize].ap = 9;
    recovered.apply(Command::Recover { unit: P1 }).unwrap();
    assert!(!recovered.queue.contains(&P1));
    assert_ne!(recovered.active_unit().unwrap().id, P1);

    assert_eq!(s.apply(Command::Flee { unit: P1 }), Err(RuleError::NotFleeing));
}

#[test]
fn failure_keeps_flee_only_control_and_the_queue() {
    let mut s = player_case(1, &[(E1, hex(3, 0)), (P3, hex(-2, 3))]);
    s.units[P1 as usize].stats.resolve = 0;
    let queue_before: Vec<u32> = s.queue.iter().copied().filter(|&id| id != P2).collect();
    end_turn(&mut s, P2);
    assert_eq!(s.queue, queue_before);
    assert_eq!(s.apply(Command::Wait { unit: P1 }), Err(RuleError::FleeingUnit));
    assert_eq!(s.apply(Command::Recover { unit: P1 }), Err(RuleError::FleeingUnit));
    assert_eq!(s.apply(Command::EndTurn { unit: P1 }), Err(RuleError::FleeingUnit));
    assert_eq!(s.apply(Command::EndRound { unit: P1 }), Err(RuleError::FleeingUnit));
    assert_eq!(s.apply(Command::Move { unit: P1, to: hex(-1, 2) }), Err(RuleError::FleeingUnit));
    assert!(s.enemy_controllers_at(E1, hex(1, 2)).is_empty());
    assert_eq!(ai::plan_turn(&s), vec![Command::Flee { unit: P1 }]);
}

#[test]
fn rejected_commands_and_queries_after_the_check_stay_pure() {
    let mut s = player_case(1, &[(E1, hex(3, 0))]);
    s.units[P1 as usize].stats.resolve = 0;
    end_turn(&mut s, P2);
    let (hash, rng, queue) = (s.state_hash(), s.rng.clone(), s.queue.clone());
    let _ = s.apply(Command::Wait { unit: P1 });
    let _ = s.apply(Command::Flee { unit: E1 });
    let _ = s.preview_move(P1, hex(-1, 2));
    let _ = ai::plan_turn(&s);
    let _ = s.reachable(P1);
    assert_eq!((s.state_hash(), s.rng.clone(), s.queue.clone()), (hash, rng, queue));
}

#[test]
fn the_next_round_uses_the_current_morale_for_initiative() {
    // P1 (initiative 100) recovers; P3 (initiative 100) keeps fleeing and sorts last.
    let mut s = board(1, &[(P2, STARTER_POS), (P1, CENTER), (P3, hex(-2, 2)), (E1, FAR)], &[P1, P3]);
    s.units[P1 as usize].stats.resolve = 100;
    s.units[P3 as usize].stats.resolve = 0;
    s.units[P3 as usize].stats.ap_max = 0; // stays on the board instead of escaping
    let ev = end_turn(&mut s, P2);
    assert_eq!(ev, vec![started(P1), checked(P1, first_draw(1), 100, Morale::Breaking)]);
    assert_eq!(s.queue, vec![P1, P3, E1]);
    end_turn(&mut s, P1);
    // P3's check fails (Resolve 0), draw consumed.
    assert_eq!(s.active_unit().unwrap().id, P3);
    assert_eq!(s.units[P3 as usize].morale, Morale::Fleeing);
    let mut guard = 0;
    while s.round == 1 {
        guard += 1;
        assert!(guard < 40);
        let active = s.active_unit().unwrap().id;
        let cmd = if s.units[active as usize].morale == Morale::Fleeing {
            Command::Flee { unit: active }
        } else {
            Command::EndTurn { unit: active }
        };
        s.apply(cmd).unwrap();
    }
    // Round 2: P1 (Breaking, no penalty) ties P2 on initiative and wins on id; fleeing P3 is last.
    assert_eq!(s.round, 2);
    assert_eq!(s.queue, vec![P1, P2, E1, P3]);
    assert_eq!(s.units[P1 as usize].morale, Morale::Breaking);
}

// --- At most once per round ------------------------------------------------------------

#[test]
fn waiting_after_recovery_neither_rechecks_nor_refreshes() {
    let mut s = player_case(1, &[(P3, hex(-2, 3)), (E1, FAR)]);
    s.units[P1 as usize].stats.resolve = 100;
    end_turn(&mut s, P2);
    s.apply(Command::Move { unit: P1, to: hex(-1, 2) }).unwrap();
    let (ap, fatigue) = (s.units[P1 as usize].ap, s.units[P1 as usize].fatigue);
    let rng = s.rng.clone();
    s.apply(Command::Wait { unit: P1 }).unwrap();
    // Pass every other unit so P1 comes back to the front.
    while s.active_unit().unwrap().id != P1 {
        let id = s.active_unit().unwrap().id;
        let ev = end_turn(&mut s, id);
        assert!(!ev.iter().any(|e| matches!(e, Event::SelfRallyChecked { .. })));
    }
    assert_eq!(s.round, 1);
    assert_eq!((s.units[P1 as usize].ap, s.units[P1 as usize].fatigue), (ap, fatigue), "no second refresh");
    assert_eq!(s.rng, rng, "no new draw: the other units were ordinary and P1 was not re-checked");
}

#[test]
fn a_failed_unit_fleeing_again_in_the_same_turn_is_not_rechecked() {
    let mut s = board(1, &[(P2, STARTER_POS), (P1, hex(-1, 0)), (E1, FAR)], &[P1]);
    s.units[P1 as usize].stats.resolve = 0;
    end_turn(&mut s, P2);
    let after_check = s.rng.clone();
    for _ in 0..2 {
        let ev = s.apply(Command::Flee { unit: P1 }).unwrap();
        assert!(!ev.iter().any(|e| matches!(e, Event::SelfRallyChecked { .. } | Event::MoraleChecked { .. })));
        assert_eq!(s.rng, after_check, "Flee itself never draws");
        assert_eq!(s.active_unit().unwrap().id, P1, "still the same turn");
    }
}

#[test]
fn a_new_round_allows_exactly_one_new_check() {
    // P1 cannot afford a step (0 AP), so its turn ends at once each round.
    let mut s = board(8, &[(P2, STARTER_POS), (P1, hex(-1, 0)), (E1, FAR)], &[P1]);
    s.units[P1 as usize].stats.resolve = 0;
    s.units[P1 as usize].stats.ap_max = 0;
    let mut checks = Vec::new();
    let mut guard = 0;
    while s.round < 3 {
        guard += 1;
        assert!(guard < 20);
        let id = s.active_unit().unwrap().id;
        let cmd = if id == P1 { Command::Flee { unit: P1 } } else { Command::EndTurn { unit: id } };
        for e in s.apply(cmd).unwrap() {
            if let Event::SelfRallyChecked { .. } = e {
                checks.push(s.round);
            }
        }
    }
    assert_eq!(checks, vec![1, 2], "one check per round: {checks:?}");
    assert_eq!(s.units[P1 as usize].morale, Morale::Fleeing);
}

#[test]
fn collapse_after_the_turn_started_does_not_trigger_an_extra_check() {
    let mut s = board(1, &[(P1, CENTER), (P2, STARTER_POS), (E1, FAR)], &[]);
    s.units[P1 as usize].turn_started = true;
    s.units[P1 as usize].stats.resolve = 100;
    // P1 becomes Fleeing mid-turn (as after a failed heavy-hit check).
    s.units[P1 as usize].morale = Morale::Fleeing;
    let rng = s.rng.clone();
    let ev = s.apply(Command::Flee { unit: P1 }).unwrap();
    assert!(!ev.iter().any(|e| matches!(e, Event::SelfRallyChecked { .. })));
    assert_eq!(s.rng, rng);
    assert_eq!(s.units[P1 as usize].morale, Morale::Fleeing);
}

#[test]
fn a_unit_that_waited_and_then_collapsed_is_not_checked_when_it_returns() {
    let mut s = board(1, &[(P1, CENTER), (P2, STARTER_POS), (E1, FAR)], &[]);
    s.units[P1 as usize].stats.resolve = 100;
    s.apply(Command::Wait { unit: P1 }).unwrap();
    s.units[P1 as usize].morale = Morale::Fleeing; // collapsed while waiting in the queue
    let rng = s.rng.clone();
    let mut seen = Vec::new();
    while s.active_unit().unwrap().id != P1 {
        let id = s.active_unit().unwrap().id;
        seen.extend(end_turn(&mut s, id));
    }
    assert!(!seen.iter().any(|e| matches!(e, Event::SelfRallyChecked { .. })));
    assert_eq!(s.rng, rng);
    assert_eq!(s.units[P1 as usize].morale, Morale::Fleeing);
}

// --- EndRound interaction --------------------------------------------------------------

#[test]
fn recovery_clears_a_leftover_skip_mark_and_the_unit_is_not_passed_over() {
    let mut s = player_case(1, &[(E1, FAR)]);
    s.units[P1 as usize].stats.resolve = 100;
    s.units[P1 as usize].skip_turn = true; // leftover, e.g. from a hand-built state
    let ev = end_turn(&mut s, P2);
    assert_eq!(ev, vec![started(P1), checked(P1, first_draw(1), 100, Morale::Breaking)]);
    assert!(!s.units[P1 as usize].skip_turn);
    assert_eq!(s.active_unit().unwrap().id, P1);
    assert!(s.units[P1 as usize].ap > 0);
}

#[test]
fn end_round_does_not_mark_a_fleeing_player_and_a_recovered_one_keeps_its_turn() {
    let mut s = board(1, &[(P2, STARTER_POS), (P1, CENTER), (E1, FAR)], &[P1]);
    s.units[P1 as usize].stats.resolve = 100;
    let ev = s.apply(Command::EndRound { unit: P2 }).unwrap();
    assert_eq!(ev, vec![started(P1), checked(P1, first_draw(1), 100, Morale::Breaking)]);
    assert!(!s.units[P1 as usize].skip_turn);
    assert_eq!(s.active_unit().unwrap().id, P1, "the recovered unit acts after the forfeit");
    // It now behaves as an ordinary player: a later EndRound ends the round for it.
    s.apply(Command::EndRound { unit: P1 }).unwrap();
    assert_eq!(s.active_unit().unwrap().id, E1);
}

#[test]
fn skipped_ordinary_players_never_draw_and_end_round_forfeits_are_unchanged() {
    let mut s = board(1, &[(P1, CENTER), (P2, STARTER_POS), (P3, hex(-2, 3)), (E1, FAR)], &[]);
    let rng = s.rng.clone();
    let ev = s.apply(Command::EndRound { unit: P1 }).unwrap();
    assert_eq!(ev, vec![started(P2), started(P3), started(E1)]);
    assert_eq!(s.rng, rng);
}
