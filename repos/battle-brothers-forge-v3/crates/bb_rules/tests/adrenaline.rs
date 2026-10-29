//! Headless Adrenaline action (partial UC-COMBAT-066, UC-COMBAT-003; capability
//! flag, costs and duplicate rejection are an APPROXIMATION): validation order,
//! costs, the pending lifecycle across `begin_round`, initiative-key combination,
//! hash bits. Fixtures clear the stock scenario (`alive = false`) and place only
//! the units they need; the first placed unit is the active, started starter
//! (9 AP, no fatigue). Scenario initiative: players 100, enemies 90.

use bb_rules::hex::{Hex, hex};
use bb_rules::rules::{ADRENALINE_AP, ADRENALINE_FATIGUE, ADRENALINE_INITIATIVE_BONUS, adrenaline_availability};
use bb_rules::scenario::{self, TerrainPreset};
use bb_rules::*;

const P1: u32 = 0;
const P2: u32 = 1;
const E1: u32 = 3;
const E2: u32 = 4;

const POS: [Hex; 5] = [hex(-3, 3), hex(-3, 2), hex(-2, 1), hex(2, -1), hex(3, -2)];

fn adr(unit: u32) -> Command {
    Command::Adrenaline { unit }
}

/// Only `placed` units are present, queued in that order; `capable` units have
/// the capability flag.
fn board(placed: &[u32], capable: &[u32]) -> BattleState {
    let mut s = BattleState::new(7);
    for u in &mut s.units {
        u.alive = false;
        u.turn_started = false;
    }
    for &id in placed {
        let u = &mut s.units[id as usize];
        u.alive = true;
        u.pos = POS[id as usize];
    }
    for &id in capable {
        s.units[id as usize].has_adrenaline = true;
    }
    s.queue = placed.to_vec();
    let active = &mut s.units[placed[0] as usize];
    active.ap = 9;
    active.fatigue = 0;
    active.turn_started = true;
    s
}

fn snapshot(s: &BattleState) -> (u64, Rng, Vec<u32>, Vec<Unit>) {
    (s.state_hash(), s.rng.clone(), s.queue.clone(), s.units.clone())
}

fn assert_rejected(s: &mut BattleState, cmd: Command, err: RuleError) {
    let before = snapshot(s);
    assert_eq!(s.apply(cmd), Err(err));
    assert_eq!(snapshot(s), before, "rejected command mutated state");
}

/// Ends turns until the next round has started.
fn end_turns_to_next_round(s: &mut BattleState) {
    let round = s.round;
    while s.round == round {
        let a = s.queue[0];
        let u = &s.units[a as usize];
        let cmd = if u.retreating {
            Command::Withdraw { unit: a }
        } else if u.morale == Morale::Fleeing {
            Command::Flee { unit: a }
        } else {
            Command::EndTurn { unit: a }
        };
        s.apply(cmd).unwrap();
    }
}

#[test]
fn success_for_both_sides_costs_exactly_and_changes_nothing_else() {
    for starter in [P1, E1] {
        let other = if starter == P1 { E1 } else { P1 };
        let mut s = board(&[starter, other], &[starter]);
        let (queue, round, rng, stats, other_unit) =
            (s.queue.clone(), s.round, s.rng.clone(), s.units[starter as usize].stats.clone(), s.units[other as usize].clone());
        let ev = s.apply(adr(starter)).unwrap();
        assert_eq!(ev, vec![Event::AdrenalineUsed { unit: starter }]);
        let u = &s.units[starter as usize];
        assert_eq!((u.ap, u.fatigue), (9 - ADRENALINE_AP, ADRENALINE_FATIGUE));
        assert_eq!((ADRENALINE_AP, ADRENALINE_FATIGUE, ADRENALINE_INITIATIVE_BONUS), (1, 20, 2000));
        assert!(u.adrenaline_pending && u.has_adrenaline);
        assert_eq!(u.stats, stats);
        assert_eq!(s.queue, queue);
        assert_eq!(s.round, round);
        assert_eq!(s.rng, rng);
        assert_eq!(s.units[other as usize], other_unit);
        assert!(s.outcome().is_none());
    }
}

#[test]
fn units_default_without_capability_or_pending_effect() {
    for u in scenario::units() {
        assert!(!u.has_adrenaline && !u.adrenaline_pending);
    }
    let mut s = BattleState::new(1);
    let a = s.queue[0];
    assert_rejected(&mut s, adr(a), RuleError::MissingAdrenaline);
}

#[test]
fn missing_capability_is_rejected_purely() {
    let mut s = board(&[P1, E1], &[E1]);
    assert_rejected(&mut s, adr(P1), RuleError::MissingAdrenaline);
}

#[test]
fn repeated_use_is_rejected_without_extra_cost_even_after_wait() {
    let mut s = board(&[P1, P2], &[P1]);
    s.apply(adr(P1)).unwrap();
    assert_rejected(&mut s, adr(P1), RuleError::AdrenalinePending);
    s.apply(Command::Wait { unit: P1 }).unwrap();
    assert_eq!(s.queue, vec![P2, P1]);
    assert!(s.units[P1 as usize].adrenaline_pending, "Wait must not consume the effect");
    s.units[P2 as usize].ap = 0;
    s.apply(Command::EndTurn { unit: P2 }).unwrap();
    assert_eq!(s.queue, vec![P1]);
    let u = &s.units[P1 as usize];
    assert_eq!((u.ap, u.fatigue), (8, 20));
    assert_rejected(&mut s, adr(P1), RuleError::AdrenalinePending);
    assert_eq!((s.units[0].ap, s.units[0].fatigue), (8, 20));
}

#[test]
fn ap_and_fatigue_limits() {
    let mut s = board(&[P1, E1], &[P1]);
    s.units[0].ap = 0;
    assert_rejected(&mut s, adr(P1), RuleError::NotEnoughAp);

    // Exact AP of 1 and exact fatigue cap succeed.
    let mut s = board(&[P1, E1], &[P1]);
    s.units[0].ap = 1;
    s.units[0].fatigue = s.units[0].stats.fatigue_max - ADRENALINE_FATIGUE;
    s.apply(adr(P1)).unwrap();
    assert_eq!((s.units[0].ap, s.units[0].fatigue), (0, s.units[0].stats.fatigue_max));

    // One over the cap is rejected.
    let mut s = board(&[P1, E1], &[P1]);
    s.units[0].fatigue = s.units[0].stats.fatigue_max - ADRENALINE_FATIGUE + 1;
    assert_rejected(&mut s, adr(P1), RuleError::TooFatigued);

    // Checked addition near u32::MAX, even with a cap of u32::MAX.
    let mut s = board(&[P1, E1], &[P1]);
    s.units[0].stats.fatigue_max = u32::MAX;
    s.units[0].fatigue = u32::MAX - ADRENALINE_FATIGUE + 1;
    assert_rejected(&mut s, adr(P1), RuleError::TooFatigued);
    s.units[0].fatigue = u32::MAX - ADRENALINE_FATIGUE;
    s.apply(adr(P1)).unwrap();
    assert_eq!(s.units[0].fatigue, u32::MAX);
}

#[test]
fn error_precedence() {
    // Missing capability beats pending, AP and fatigue.
    let mut s = board(&[P1, E1], &[]);
    s.units[0].adrenaline_pending = true;
    s.units[0].ap = 0;
    s.units[0].fatigue = 100;
    assert_rejected(&mut s, adr(P1), RuleError::MissingAdrenaline);
    // Pending beats AP and fatigue.
    s.units[0].has_adrenaline = true;
    assert_rejected(&mut s, adr(P1), RuleError::AdrenalinePending);
    // AP beats fatigue.
    s.units[0].adrenaline_pending = false;
    assert_rejected(&mut s, adr(P1), RuleError::NotEnoughAp);
    s.units[0].ap = 1;
    assert_rejected(&mut s, adr(P1), RuleError::TooFatigued);

    // Active-unit, battle-over, retreating and Fleeing precedence over capability errors.
    let mut s = board(&[P1, E1], &[P1]);
    assert_rejected(&mut s, adr(E1), RuleError::NotActiveUnit);
    let mut s = board(&[P1, E1], &[]);
    s.units[0].morale = Morale::Fleeing;
    assert_rejected(&mut s, adr(P1), RuleError::FleeingUnit);
    s.units[0].retreating = true;
    assert_rejected(&mut s, adr(P1), RuleError::RetreatingUnit);
    s.outcome = Some(Outcome::Victory);
    assert_rejected(&mut s, adr(P1), RuleError::BattleOver);
}

#[test]
fn current_round_is_not_reordered_and_the_next_round_is() {
    // Control: without Adrenaline the next round is P1, P2, E1.
    let mut control = board(&[E1, P1, P2], &[]);
    end_turns_to_next_round(&mut control);
    assert_eq!(control.queue, vec![P1, P2, E1]);

    let mut s = board(&[E1, P1, P2], &[E1]);
    s.apply(adr(E1)).unwrap();
    assert_eq!(s.queue, vec![E1, P1, P2]);
    s.apply(Command::EndTurn { unit: E1 }).unwrap();
    assert_eq!(s.queue, vec![P1, P2]);
    assert_eq!(s.round, 1);
    end_turns_to_next_round(&mut s);
    assert_eq!(s.round, 2);
    // Key 90 - 20/2 + 2000 beats 100.
    assert_eq!(s.queue, vec![E1, P1, P2]);
    assert!(s.units.iter().all(|u| !u.adrenaline_pending));
    assert_eq!(s.units[E1 as usize].stats.initiative, 90);
}

#[test]
fn wait_keeps_the_effect_until_the_next_round() {
    let mut s = board(&[E1, P1], &[E1]);
    s.apply(adr(E1)).unwrap();
    s.apply(Command::Wait { unit: E1 }).unwrap();
    assert!(s.units[E1 as usize].adrenaline_pending);
    assert_eq!(s.queue, vec![P1, E1]);
    end_turns_to_next_round(&mut s);
    assert_eq!(s.queue, vec![E1, P1]);
}

#[test]
fn end_round_leads_to_the_boosted_order() {
    let mut s = board(&[P1, P2, E1], &[]);
    s.units[E1 as usize].has_adrenaline = true;
    // Only a player can EndRound, so let the player act first and Wait for the enemy.
    s.apply(Command::EndTurn { unit: P1 }).unwrap();
    s.apply(Command::EndTurn { unit: P2 }).unwrap();
    s.apply(adr(E1)).unwrap();
    s.apply(Command::EndTurn { unit: E1 }).unwrap();
    assert_eq!(s.round, 2);
    assert_eq!(s.queue, vec![E1, P1, P2]);

    // EndRound from a boosted player.
    let mut s = board(&[P1, E1], &[P1]);
    s.units[P1 as usize].stats.initiative = 10;
    s.apply(adr(P1)).unwrap();
    s.apply(Command::EndRound { unit: P1 }).unwrap();
    assert_eq!(s.round, 1);
    s.apply(Command::EndTurn { unit: E1 }).unwrap();
    assert_eq!(s.round, 2);
    assert_eq!(s.queue, vec![P1, E1]);
}

#[test]
fn multiple_boosted_units_ties_and_large_values() {
    // Equal key (100 - 20/2 + 2000) on P1 and P2: id breaks the tie; E1 is last.
    let mut s = board(&[P2, P1, E1], &[P1, P2]);
    s.units[P1 as usize].turn_started = true;
    s.units[P1 as usize].ap = 9;
    s.apply(adr(P2)).unwrap();
    s.apply(Command::EndTurn { unit: P2 }).unwrap();
    s.apply(adr(P1)).unwrap();
    s.apply(Command::EndTurn { unit: P1 }).unwrap();
    end_turns_to_next_round(&mut s);
    assert_eq!(s.queue, vec![P1, P2, E1]);

    // A boosted low-initiative unit beats an unboosted one, but not a larger base.
    let mut s = board(&[E1, P1, P2], &[E1, P2]);
    s.units[P1 as usize].stats.initiative = u32::MAX;
    s.units[P2 as usize].stats.initiative = 0;
    s.units[P2 as usize].turn_started = true;
    s.units[P2 as usize].ap = 9;
    s.apply(adr(E1)).unwrap();
    s.apply(Command::EndTurn { unit: E1 }).unwrap();
    s.apply(Command::EndTurn { unit: P1 }).unwrap();
    s.apply(adr(P2)).unwrap();
    s.apply(Command::EndTurn { unit: P2 }).unwrap();
    assert_eq!(s.queue, vec![P1, E1, P2]);

    // A boosted unit with u32::MAX initiative stays well-defined.
    let mut s = board(&[P1, P2], &[P1]);
    s.units[P1 as usize].stats.initiative = u32::MAX;
    s.apply(adr(P1)).unwrap();
    end_turns_to_next_round(&mut s);
    assert_eq!(s.queue, vec![P1, P2]);
}

#[test]
fn fatigue_combines_and_the_turn_start_refresh_still_applies() {
    // Heavy fatigue reduces the boosted key: 90 - 80/2 + 2000 still wins, but 100 + 2000 - 0 beats it.
    let mut s = board(&[E1, E2, P1], &[E1, E2]);
    s.units[E1 as usize].fatigue = 60; // 80 after the cost
    s.apply(adr(E1)).unwrap();
    s.apply(Command::EndTurn { unit: E1 }).unwrap();
    s.units[E2 as usize].stats.initiative = 90;
    s.apply(adr(E2)).unwrap(); // fatigue 20
    s.apply(Command::EndTurn { unit: E2 }).unwrap();
    s.apply(Command::EndTurn { unit: P1 }).unwrap();
    assert_eq!(s.round, 2);
    // E2: 90 - 10 + 2000; E1: 90 - 40 + 2000; P1: 100.
    assert_eq!(s.queue, vec![E2, E1, P1]);
    // The started unit got its normal refresh after ordering.
    let e2 = &s.units[E2 as usize];
    assert_eq!((e2.ap, e2.fatigue), (9, 5));
    assert!(!e2.adrenaline_pending);
}

#[test]
fn bonus_expires_before_the_following_round() {
    let mut s = board(&[E1, P1], &[E1]);
    s.apply(adr(E1)).unwrap();
    end_turns_to_next_round(&mut s);
    assert_eq!(s.queue[0], E1);
    assert!(!s.units[E1 as usize].adrenaline_pending);
    end_turns_to_next_round(&mut s);
    assert_eq!(s.round, 3);
    assert_eq!(s.queue, vec![P1, E1]);
    // May be used again once consumed.
    let a = s.queue[0];
    s.apply(Command::EndTurn { unit: a }).unwrap();
    assert_eq!(s.apply(adr(E1)).map(|e| e.len()), Ok(1));
}

#[test]
fn fleeing_and_retreat_intent_do_not_cancel_the_bonus() {
    // Fleeing penalty combines: 90 - 10 - 1000 + 2000 = 1080 > 100.
    let mut s = board(&[E1, P1, E2], &[E1]);
    s.units[E1 as usize].pos = hex(0, 0);
    s.units[E1 as usize].ap = 4;
    s.apply(adr(E1)).unwrap();
    s.units[E1 as usize].morale = Morale::Fleeing;
    assert!(s.units[E1 as usize].adrenaline_pending);
    end_turns_to_next_round(&mut s);
    assert_eq!(s.queue, vec![E1, P1, E2]);

    // Retreat intent keeps the pending flag; the automatic turn still happens.
    let mut s = board(&[P1, P2, E1], &[P1]);
    s.units[P1 as usize].pos = hex(0, 0);
    s.units[P1 as usize].ap = 4;
    s.units[P2 as usize].stats.initiative = 150;
    s.apply(adr(P1)).unwrap();
    s.apply(Command::Retreat { unit: P1 }).unwrap();
    assert!(s.units[P1 as usize].adrenaline_pending && s.units[P1 as usize].retreating);
    end_turns_to_next_round(&mut s);
    assert_eq!(s.queue[0], P1);
    assert!(s.units[P1 as usize].retreating);
    assert!(!s.units[P1 as usize].adrenaline_pending);
}

#[test]
fn dead_or_escaped_pending_units_are_not_reintroduced() {
    let mut s = board(&[E1, P1, P2], &[E1]);
    s.apply(adr(E1)).unwrap();
    s.units[E1 as usize].alive = false;
    s.queue.retain(|&id| id != E1);
    end_turns_to_next_round(&mut s);
    assert_eq!(s.queue, vec![P1, P2]);
    assert!(!s.units[E1 as usize].adrenaline_pending);

    let mut s = board(&[E1, P1, P2], &[E1]);
    s.apply(adr(E1)).unwrap();
    s.units[E1 as usize].fled = true;
    s.queue.retain(|&id| id != E1);
    end_turns_to_next_round(&mut s);
    assert_eq!(s.queue, vec![P1, P2]);
    assert!(!s.units[E1 as usize].adrenaline_pending);

    // A real escape: a Fleeing unit on the map edge leaves with the effect pending.
    let mut s = board(&[E1, P1, E2], &[E1]);
    s.units[E1 as usize].pos = hex(4, -2);
    s.apply(adr(E1)).unwrap();
    s.units[E1 as usize].morale = Morale::Fleeing;
    s.apply(Command::Flee { unit: E1 }).unwrap();
    assert!(s.units[E1 as usize].fled);
    end_turns_to_next_round(&mut s);
    assert!(!s.queue.contains(&E1));
    assert!(!s.units[E1 as usize].adrenaline_pending);
}

#[test]
fn rejected_and_accepted_commands_consume_no_rng() {
    let mut s = board(&[P1, E1], &[P1]);
    let rng = s.rng.clone();
    s.apply(adr(P1)).unwrap();
    assert_eq!(s.rng, rng);
    let _ = s.apply(adr(P1));
    assert_eq!(s.rng, rng);
}

#[test]
fn hash_bits_are_independent_and_defaults_are_unchanged() {
    let base = board(&[P1, E1], &[]);
    let h0 = base.state_hash();
    let mut cap = base.clone();
    cap.units[0].has_adrenaline = true;
    let mut pend = base.clone();
    pend.units[0].adrenaline_pending = true;
    let mut both = cap.clone();
    both.units[0].adrenaline_pending = true;
    let hashes = [h0, cap.state_hash(), pend.state_hash(), both.state_hash()];
    for i in 0..4 {
        for j in i + 1..4 {
            assert_ne!(hashes[i], hashes[j], "{i} vs {j}");
        }
    }
    // Setting and clearing round-trips.
    both.units[0].has_adrenaline = false;
    both.units[0].adrenaline_pending = false;
    assert_eq!(both.state_hash(), h0);

    // Pinned initial hashes of the stock scenarios (unchanged by the new bits).
    for (terrain, expected) in [(TerrainPreset::Flat, FLAT_HASH), (TerrainPreset::Hills, HILLS_HASH)] {
        assert_eq!(scenario::battle(1, terrain).state_hash(), expected, "{terrain:?}");
    }
}

const FLAT_HASH: u64 = 0x8c00302113cbe668;
const HILLS_HASH: u64 = 0xb759683cdff4bfa2;

// --- Availability query ------------------------------------------------------------

#[test]
fn availability_agrees_with_apply_on_every_accepted_and_rejected_case() {
    let maxes = [0, 19, 20, 21, 100, u32::MAX];
    let fatigues = [0, 1, 79, 80, 81, 100, u32::MAX - ADRENALINE_FATIGUE, u32::MAX - ADRENALINE_FATIGUE + 1, u32::MAX];
    let mut accepted = 0;
    let mut kinds = std::collections::BTreeSet::new();
    for has in [false, true] {
        for pending in [false, true] {
            for ap in [0, ADRENALINE_AP - 1, ADRENALINE_AP, 9] {
                for fatigue_max in maxes {
                    for fatigue in fatigues {
                        let mut s = board(&[P1, E1], &[]);
                        let u = &mut s.units[P1 as usize];
                        (u.has_adrenaline, u.adrenaline_pending, u.ap, u.fatigue, u.stats.fatigue_max) =
                            (has, pending, ap, fatigue, fatigue_max);
                        let before = snapshot(&s);
                        let query = adrenaline_availability(&s.units[P1 as usize]);
                        assert_eq!(snapshot(&s), before, "the query is pure");
                        let applied = s.apply(adr(P1)).map(|_| ());
                        assert_eq!(query, applied, "has={has} pending={pending} ap={ap} max={fatigue_max} fatigue={fatigue}");
                        match applied {
                            Ok(()) => accepted += 1,
                            Err(e) => {
                                kinds.insert(format!("{e:?}"));
                                assert_eq!(snapshot(&s), before, "rejection is pure");
                            }
                        }
                    }
                }
            }
        }
    }
    assert!(accepted > 0);
    let want = ["AdrenalinePending", "MissingAdrenaline", "NotEnoughAp", "TooFatigued"];
    assert_eq!(kinds.iter().map(String::as_str).collect::<Vec<_>>(), want, "every unit-level rejection is exercised");
}

#[test]
fn availability_error_precedence_matches_apply_and_ignores_battle_gates() {
    let mut u = board(&[P1, E1], &[]).units[0].clone();
    u.adrenaline_pending = true;
    u.ap = 0;
    u.fatigue = u32::MAX;
    assert_eq!(adrenaline_availability(&u), Err(RuleError::MissingAdrenaline));
    u.has_adrenaline = true;
    assert_eq!(adrenaline_availability(&u), Err(RuleError::AdrenalinePending));
    u.adrenaline_pending = false;
    assert_eq!(adrenaline_availability(&u), Err(RuleError::NotEnoughAp));
    u.ap = ADRENALINE_AP;
    assert_eq!(adrenaline_availability(&u), Err(RuleError::TooFatigued), "fatigue overflow is rejected, not wrapped");
    u.fatigue = u.stats.fatigue_max - ADRENALINE_FATIGUE;
    assert_eq!(adrenaline_availability(&u), Ok(()));
    // Unit-level only: an enemy, a Fleeing or a retreating unit is judged by its own budget and
    // flags; whether it may act is `apply`'s job and keeps its higher precedence there.
    u.morale = Morale::Fleeing;
    u.retreating = true;
    assert_eq!(adrenaline_availability(&u), Ok(()));
    let mut s = board(&[P1, E1], &[P1]);
    s.units[0].morale = Morale::Fleeing;
    assert_rejected(&mut s, adr(P1), RuleError::FleeingUnit);
}

#[test]
fn availability_flips_with_use_and_the_next_round() {
    let mut s = board(&[P1, P2, E1], &[P1, P2]);
    assert_eq!(adrenaline_availability(&s.units[0]), Ok(()));
    let rng = s.rng.clone();
    s.apply(adr(P1)).unwrap();
    assert_eq!(adrenaline_availability(&s.units[0]), Err(RuleError::AdrenalinePending));
    s.apply(Command::Wait { unit: P1 }).unwrap();
    assert_eq!(adrenaline_availability(&s.units[0]), Err(RuleError::AdrenalinePending), "still pending after Wait");
    end_turns_to_next_round(&mut s);
    assert!(!s.units[0].adrenaline_pending);
    assert_eq!(s.rng, rng);
    // The next round refreshed AP; the 20 fatigue is still below the cap.
    s.units[0].ap = 9;
    assert_eq!(adrenaline_availability(&s.units[0]), Ok(()));
}
