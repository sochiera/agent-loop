//! Enemy (and autoplay) decision making. Pure function of the battle state:
//! it only ever returns commands that `Battle::apply` will accept.

use crate::battle::{Battle, Command};
use crate::hex::Hex;
use crate::unit::{AttackKind, Unit, UnitId};

/// Value of attacking `target` with the given hit chance and weapon.
fn attack_score(target: &Unit, chance: i32, avg_damage: f32, ignore_pct: i32) -> f32 {
    let expected = chance as f32 / 100.0 * avg_damage;
    let soak = target.hp as f32 + target.armor as f32 * (100 - ignore_pct) as f32 / 100.0;
    // Prefer finishing weak targets: scale by how much of the target one hit removes.
    let kill_bonus = if avg_damage >= soak { 15.0 } else { 0.0 };
    expected + kill_bonus + (avg_damage / soak.max(1.0)) * 10.0
}

/// Choose the next command for the active unit.
pub fn decide(battle: &Battle) -> Command {
    if battle.outcome().is_some() {
        return Command::EndTurn;
    }
    let me = battle.active();
    let foes: Vec<&Unit> = battle.living(me.team.opponent()).collect();
    if foes.is_empty() {
        return Command::EndTurn;
    }

    // 1. Attack the best target available from where we stand.
    let mut best: Option<(f32, UnitId)> = None;
    for t in &foes {
        if let Ok(p) = battle.preview_from(me, me.pos, me.ap, t) {
            let s = attack_score(t, p.hit.chance, p.weapon.avg_damage(), p.weapon.armor_ignore);
            if best.is_none_or(|(bs, _)| s > bs) {
                best = Some((s, t.id));
            }
        }
    }
    if let Some((_, target)) = best {
        return Command::Attack { target };
    }

    // 2. Move to the reachable hex from which the best attack is possible
    //    (ranged units find a firing position, melee units get adjacent).
    let reachable = battle.reachable(me.id);
    let mut best_move: Option<(f32, Hex)> = None;
    for &(hex, cost) in &reachable {
        let terrain_def = battle.map().terrain(hex).map_or(0, |t| t.defense_bonus()) as f32;
        let engaged = foes.iter().any(|f| f.pos.is_adjacent(hex));
        for t in &foes {
            if let Ok(p) = battle.preview_from(me, hex, me.ap - cost, t) {
                let mut s = attack_score(t, p.hit.chance, p.weapon.avg_damage(), p.weapon.armor_ignore);
                s += terrain_def * 0.3 - cost as f32 * 0.2;
                if p.kind == AttackKind::Ranged && engaged {
                    s -= 6.0; // an archer in melee is a poor archer
                }
                if best_move.is_none_or(|(bs, _)| s > bs) {
                    best_move = Some((s, hex));
                }
            }
        }
    }
    if let Some((_, to)) = best_move {
        return Command::Move { to };
    }

    // 3. Nobody to hit this turn: advance toward the nearest opponent.
    let targets: Vec<Hex> = foes.iter().map(|f| f.pos).collect();
    let dist = battle.distance_map(&targets);
    let here = battle.map().index(me.pos).map_or(i32::MAX, |i| dist[i]);
    let mut best_adv: Option<(i32, i32, Hex)> = None; // (distance, cost, hex)
    for &(hex, cost) in &reachable {
        let Some(i) = battle.map().index(hex) else { continue };
        let key = (dist[i], cost, hex);
        if best_adv.is_none_or(|b| (key.0, key.1) < (b.0, b.1)) {
            best_adv = Some(key);
        }
    }
    match best_adv {
        Some((d, _, hex)) if d < here => Command::Move { to: hex },
        _ => Command::EndTurn,
    }
}
