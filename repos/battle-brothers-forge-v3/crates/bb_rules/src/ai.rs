//! Enemy/auto AI. APPROXIMATION: pure function of state.
//!
//! Bounded progress is structural, without a counter: the ordinary AI can issue
//! one 1-AP `Adrenaline` while its pending effect prevents repetition; every
//! other generated command that can leave the turn active (`Attack`, `Move`,
//! `Indomitable`, `StunningBlow`) costs at least 2 AP. Stunning Blow costs 6 AP,
//! above that minimum. An ordinary scheduled turn ends with
//! exactly one final command: either `Recover` or explicit `EndTurn`. Recover
//! spends 9 AP and completes that turn itself, so it is not followed by an
//! `EndTurn` for the same actor. Retreating and Fleeing actors keep their
//! separate one-command `Withdraw` and `Flee` policy above.
//! Without capabilities a 9-AP turn fits at most five commands that leave the
//! turn active before either final command. A Berserk unit regains at most
//! `BERSERK_AP_REFUND` (4) AP once per scheduled turn, so a normal 9-AP turn has
//! at most 13 AP to spend: one Adrenaline (1 AP) plus at most (13 - 1) / 2 = six
//! further commands, i.e. at most seven commands that leave the turn active
//! under the existing minimum costs before Recover or EndTurn. The
//! selection policy never reads `has_berserk`.

use crate::hex::{self, Hex};
use crate::rules::{
    ADRENALINE_AP, ADRENALINE_FATIGUE, ATTACK_AP, ATTACK_FATIGUE, INDOMITABLE_AP,
    INDOMITABLE_FATIGUE, MOVE_AP, adrenaline_availability, indomitable_availability,
};
use crate::{BattleState, Command, Morale};

/// Next command for the active unit (always exactly one, never empty while the
/// battle runs; empty only once the battle is over). A Fleeing active unit of
/// either side gets exactly one `Flee` command; its normal policy is untouched.
/// A retreating active unit gets exactly one `Withdraw`, before any other policy.
pub fn plan_turn(state: &BattleState) -> Vec<Command> {
    let Some(u) = state.active_unit() else { return vec![] };
    let id = u.id;

    if u.retreating {
        return vec![Command::Withdraw { unit: id }];
    }

    if u.morale == Morale::Fleeing {
        return vec![Command::Flee { unit: id }];
    }

    // Stunning Blow's capability, adjacency and resource checks share the rules
    // query. Target filtering, HP ordering and priority are an APPROXIMATION
    // inspired by the catalog; the policy applies to either faction.
    if let Some(target) = state
        .legal_stunning_blow_targets(id)
        .into_iter()
        .filter(|&target| {
            let defender = &state.units[target as usize];
            !defender.stunned && !defender.is_indomitable_active()
        })
        .min_by_key(|&target| (state.units[target as usize].hp, target))
    {
        return vec![Command::StunningBlow { attacker: id, target }];
    }

    // 1. Attack the weakest enemy in melee reach (lowest hp, then lowest id).
    //    `legal_targets` uses `Unit::melee_reaches`, so a TwoHex unit attacks an
    //    affordable distance-two target here without moving first.
    let targets = state.legal_targets(id);
    if !targets.is_empty() {
        let can_indomitable_then_attack = u.hp > 0
            && u64::from(u.hp) * 2 <= u64::from(u.stats.hp)
            && !state.enemy_controllers_at(id, u.pos).is_empty()
            && indomitable_availability(u).is_ok()
            && INDOMITABLE_AP
                .checked_add(ATTACK_AP)
                .is_some_and(|ap| u.ap >= ap)
            && u.fatigue
                .checked_add(INDOMITABLE_FATIGUE)
                .and_then(|fatigue| fatigue.checked_add(ATTACK_FATIGUE))
                .is_some_and(|fatigue| fatigue <= u.stats.fatigue_max);
        if can_indomitable_then_attack {
            return vec![Command::Indomitable { unit: id }];
        }

        let can_adrenaline_then_attack = adrenaline_availability(u).is_ok()
            && ADRENALINE_AP
                .checked_add(ATTACK_AP)
                .is_some_and(|ap| u.ap >= ap)
            && u.fatigue
                .checked_add(ADRENALINE_FATIGUE)
                .and_then(|fatigue| fatigue.checked_add(ATTACK_FATIGUE))
                .is_some_and(|fatigue| fatigue <= u.stats.fatigue_max);
        if can_adrenaline_then_attack {
            return vec![Command::Adrenaline { unit: id }];
        }

        if u.ap >= ATTACK_AP
            && u.fatigue
                .checked_add(ATTACK_FATIGUE)
                .is_some_and(|f| f <= u.stats.fatigue_max)
        {
            let t = targets
                .iter()
                .copied()
                .min_by_key(|&t| (state.units[t as usize].hp, t))
                .unwrap();
            return vec![Command::Attack { attacker: id, target: t }];
        }

        let attack_fatigue_exceeds_capacity = u
            .fatigue
            .checked_add(ATTACK_FATIGUE)
            .is_none_or(|fatigue| fatigue > u.stats.fatigue_max);
        let halved_fatigue_restores_attack_budget = (u.fatigue / 2)
            .checked_add(ATTACK_FATIGUE)
            .is_some_and(|fatigue| fatigue <= u.stats.fatigue_max);
        if u.fatigue > 0
            && attack_fatigue_exceeds_capacity
            && state.recover_availability(id).is_ok()
            && halved_fatigue_restores_attack_budget
        {
            return vec![Command::Recover { unit: id }];
        }
    }

    // 2. Approach. Adjacent actors keep their existing adjacent-hex candidates.
    //    TwoHex actors consider positions in the actor-rooted BFS list from which
    //    their reach permits an attack, including the occupied BFS origin. This
    //    deterministic shortest-step policy is an APPROXIMATION inspired by
    //    UC-COMBAT-020/051; weighted terrain costs and tactical safety are deferred.
    if u.ap >= MOVE_AP {
        let occupied: Vec<Hex> = state.units.iter().filter(|x| x.is_present()).map(|x| x.pos).collect();
        let dist = hex::bfs_distances(u.pos, &occupied);
        let dist_of = |h: Hex| dist.iter().find(|&&(x, _)| x == h).map(|&(_, d)| d);
        let mut best: Option<(u32, Hex)> = None;
        match u.melee_reach {
            crate::MeleeReach::Adjacent => {
                // Preserve the original candidate construction and iteration order.
                for e in state.units.iter().filter(|e| e.is_present() && e.side != u.side) {
                    let mut cands: Vec<(u32, Hex)> = Vec::new();
                    if u.pos.distance(e.pos) == 1 {
                        cands.push((0, u.pos));
                    }
                    for n in e.pos.neighbors() {
                        if n != u.pos && !occupied.contains(&n) {
                            if let Some(d) = dist_of(n) {
                                cands.push((d, n));
                            }
                        }
                    }
                    // Strictly smaller wins, so ties keep the first (neighbour order / lower id).
                    for c in cands {
                        if best.is_none_or(|b| c.0 < b.0) {
                            best = Some(c);
                        }
                    }
                }
            }
            crate::MeleeReach::TwoHex => {
                // Enemy iteration order, then stable BFS discovery order break ties.
                for e in state.units.iter().filter(|e| e.is_present() && e.side != u.side) {
                    for &(candidate, steps) in &dist {
                        if u.melee_reach.reaches(candidate, e.pos)
                            && best.is_none_or(|b| steps < b.0)
                        {
                            best = Some((steps, candidate));
                        }
                    }
                }
            }
        }
        if let Some((_, dest)) = best {
            // Keep the BFS destination and path; walk the longest prefix that the shared
            // step costs (uphill surcharge included) and fatigue allow. Plain `EndTurn`
            // when not even one step is affordable.
            if dest != u.pos {
                if let Some(path) = hex::bfs_path(u.pos, dest, &occupied) {
                    let steps = state.affordable_prefix(u, &path);
                    if steps > 0 {
                        return vec![Command::Move { unit: id, to: path[steps - 1] }];
                    }
                }
            }
        }
    }

    // 3. Nothing useful left.
    vec![Command::EndTurn { unit: id }]
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hex::hex;
    use crate::{Event, RuleError};

    /// B1-a: adjacent to an enemy with 3 AP (cannot attack) => exactly EndTurn.
    #[test]
    fn b1a_adjacent_without_ap_ends_turn() {
        for fatigue in [6, 0] {
            let mut s = BattleState::new(1);
            s.units[0].pos = hex(1, 0);
            s.units[4].pos = hex(2, 0);
            s.queue = vec![4, 0, 1, 2, 3, 5];
            s.units[4].ap = 3;
            s.units[4].fatigue = fatigue;
            assert_eq!(plan_turn(&s), vec![Command::EndTurn { unit: 4 }]);
            assert!(s.apply(Command::EndTurn { unit: 4 }).is_ok());
        }
    }

    /// B1-b: replay from the opening through plan_turn/apply to E2's first turn.
    #[test]
    fn b1b_replay_to_e2() {
        let mut s = BattleState::new(1);
        while s.active_unit().unwrap().id != 4 {
            assert!(s.round == 1, "E2 must act in round 1");
            let cmd = plan_turn(&s)[0];
            s.apply(cmd).unwrap();
        }
        let cmd = plan_turn(&s);
        assert_eq!(cmd, vec![Command::Move { unit: 4, to: hex(2, 0) }]);
        let ev = s.apply(cmd[0]).unwrap();
        // The first step lands on the edge ring (4,-2): the map-edge penalty splits the segment.
        assert_eq!(
            ev,
            vec![
                Event::Moved { unit: 4, path: vec![hex(4, -2)] },
                Event::EdgeMoraleChanged { unit: 4, at: hex(4, -2), before: Morale::Steady, after: Morale::Breaking },
                Event::Moved { unit: 4, path: vec![hex(3, -1), hex(2, 0)] },
            ]
        );
        assert_eq!(plan_turn(&s), vec![Command::EndTurn { unit: 4 }]);
        let _: Result<_, RuleError> = s.apply(Command::EndTurn { unit: 4 });
    }
}
