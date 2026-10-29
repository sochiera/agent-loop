//! Seeded procedural battle maps with a connectivity guarantee.
//!
//! [`generate`] scatters forests, hills, rocks and lakes over a 12x10 field, picks spawn
//! hexes in the west (player) and east (enemy) zones, and then *carves* a path through any
//! obstacle that separates two spawn hexes. [`verify`] re-checks every promise and is used by
//! the tests for hundreds of seeds.

use std::cmp::Reverse;
use std::collections::BinaryHeap;

use crate::hex::Hex;
use crate::map::{Map, Terrain};
use crate::rng::Rng;

pub const MAP_W: i32 = 12;
pub const MAP_H: i32 = 10;
pub const MAX_PLAYER_SPAWNS: usize = 6;
pub const MAX_ENEMY_SPAWNS: usize = 10;
/// Columns (offset coordinates) reserved for each side's spawn zone.
const ZONE_COLS: i32 = 3;
/// At most this share of the map may be impassable (rock + water) before extras are cleared.
const MAX_BLOCKED_PERCENT: usize = 24;

#[derive(Clone, Debug)]
pub struct Layout {
    pub map: Map,
    pub player_spawns: Vec<Hex>,
    pub enemy_spawns: Vec<Hex>,
}

/// Grow a connected blob of `size` hexes from a random start and paint it.
fn blob(map: &mut Map, rng: &mut Rng, terrain: Terrain, size: usize) {
    let start = Hex::from_offset(rng.below(MAP_W as u32) as i32, rng.below(MAP_H as u32) as i32);
    let mut cells = vec![start];
    let mut guard = 0;
    while cells.len() < size && guard < size * 20 {
        guard += 1;
        let from = cells[rng.below(cells.len() as u32) as usize];
        let nb = from.neighbors()[rng.below(6) as usize];
        if map.contains(nb) && !cells.contains(&nb) {
            cells.push(nb);
        }
    }
    for c in cells {
        map.set(c, terrain);
    }
}

fn zone_spawns(map: &Map, rng: &mut Rng, west: bool, count: usize) -> Vec<Hex> {
    let col = if west { 1 } else { MAP_W - 2 };
    let row = 2 + rng.below((MAP_H - 4) as u32) as i32;
    let center = Hex::from_offset(col, row);
    let (lo, hi) = if west { (0, ZONE_COLS) } else { (MAP_W - ZONE_COLS, MAP_W) };
    let mut zone: Vec<Hex> = map
        .hexes()
        .filter(|h| {
            let (c, _) = h.to_offset();
            (lo..hi).contains(&c)
        })
        .collect();
    zone.sort_by_key(|h| (h.distance(center), h.to_offset().1, h.to_offset().0));
    zone.truncate(count);
    zone
}

/// Passable hexes reachable from `start` (terrain only; units are ignored). Indexed like the map.
pub fn connected_from(map: &Map, start: Hex) -> Vec<bool> {
    let mut seen = vec![false; map.len()];
    let Some(s) = map.index(start) else { return seen };
    if !map.terrain(start).is_some_and(Terrain::passable) {
        return seen;
    }
    seen[s] = true;
    let mut stack = vec![start];
    while let Some(h) = stack.pop() {
        for nb in h.neighbors() {
            if let Some(i) = map.index(nb) {
                if !seen[i] && map.terrain(nb).is_some_and(Terrain::passable) {
                    seen[i] = true;
                    stack.push(nb);
                }
            }
        }
    }
    seen
}

/// Make `target` reachable from the component `reached` by clearing the cheapest path of obstacles.
fn carve_to(map: &mut Map, reached: &[bool], target: Hex) {
    let n = map.len();
    let mut dist = vec![i32::MAX; n];
    let mut prev = vec![usize::MAX; n];
    let t = map.index(target).expect("spawn on map");
    dist[t] = 0;
    let mut heap = BinaryHeap::new();
    heap.push(Reverse((0, t)));
    while let Some(Reverse((d, i))) = heap.pop() {
        if d > dist[i] {
            continue;
        }
        if reached[i] {
            // Walk back to the target, clearing obstacles.
            let mut cur = i;
            loop {
                let h = map.hex_at(cur);
                if !map.terrain(h).is_some_and(Terrain::passable) {
                    map.set(h, Terrain::Plain);
                }
                if cur == t {
                    return;
                }
                cur = prev[cur];
            }
        }
        for nb in map.hex_at(i).neighbors() {
            let Some(ni) = map.index(nb) else { continue };
            let step = if map.terrain(nb).is_some_and(Terrain::passable) { 1 } else { 4 };
            if d + step < dist[ni] {
                dist[ni] = d + step;
                prev[ni] = i;
                heap.push(Reverse((d + step, ni)));
            }
        }
    }
}

/// Build the battlefield for `seed` with `players` (1..=6) and `enemies` (1..=10) spawn hexes.
pub fn generate(seed: u64, players: usize, enemies: usize) -> Layout {
    let players = players.clamp(1, MAX_PLAYER_SPAWNS);
    let enemies = enemies.clamp(1, MAX_ENEMY_SPAWNS);
    let mut rng = Rng::new(seed ^ 0x6D61_7067_656E_0001);
    let mut map = Map::new(MAP_W, MAP_H, Terrain::Plain);

    for _ in 0..rng.range_inclusive(3, 5) {
        let size = rng.range_inclusive(3, 7) as usize;
        blob(&mut map, &mut rng, Terrain::Forest, size);
    }
    for _ in 0..rng.range_inclusive(2, 4) {
        let size = rng.range_inclusive(2, 5) as usize;
        blob(&mut map, &mut rng, Terrain::Hill, size);
    }
    for _ in 0..rng.range_inclusive(0, 2) {
        let size = rng.range_inclusive(3, 8) as usize;
        blob(&mut map, &mut rng, Terrain::Water, size);
    }
    for _ in 0..rng.range_inclusive(3, 6) {
        let size = rng.range_inclusive(1, 2) as usize;
        blob(&mut map, &mut rng, Terrain::Rock, size);
    }

    // Keep the field fightable: clear random obstacles beyond the cap.
    let cap = map.len() * MAX_BLOCKED_PERCENT / 100;
    let mut blocked: Vec<Hex> = map.hexes().filter(|&h| map.terrain(h).is_some_and(|t| !t.passable())).collect();
    while blocked.len() > cap {
        let i = rng.below(blocked.len() as u32) as usize;
        map.set(blocked.swap_remove(i), Terrain::Plain);
    }

    let player_spawns = zone_spawns(&map, &mut rng, true, players);
    let enemy_spawns = zone_spawns(&map, &mut rng, false, enemies);
    for &h in player_spawns.iter().chain(&enemy_spawns) {
        if !map.terrain(h).is_some_and(Terrain::passable) {
            map.set(h, Terrain::Plain);
        }
    }

    // Guarantee: every spawn hex is in one connected passable region.
    let anchor = player_spawns[0];
    for &h in player_spawns.iter().chain(&enemy_spawns).skip(1) {
        let reached = connected_from(&map, anchor);
        if !reached[map.index(h).expect("spawn on map")] {
            carve_to(&mut map, &reached, h);
        }
    }

    Layout { map, player_spawns, enemy_spawns }
}

/// Check every promise of [`generate`]; the error names the first violation.
pub fn verify(layout: &Layout) -> Result<(), String> {
    let map = &layout.map;
    if (map.width(), map.height()) != (MAP_W, MAP_H) {
        return Err("wrong map size".into());
    }
    let all: Vec<Hex> = layout.player_spawns.iter().chain(&layout.enemy_spawns).copied().collect();
    if layout.player_spawns.is_empty() || layout.enemy_spawns.is_empty() {
        return Err("a side has no spawn hexes".into());
    }
    for (i, &h) in all.iter().enumerate() {
        match map.terrain(h) {
            None => return Err(format!("spawn {h:?} is off the map")),
            Some(t) if !t.passable() => return Err(format!("spawn {h:?} is on impassable {}", t.name())),
            _ => {}
        }
        if all[..i].contains(&h) {
            return Err(format!("spawn {h:?} is used twice"));
        }
    }
    let reached = connected_from(map, all[0]);
    for &h in &all {
        if !reached[map.index(h).expect("checked above")] {
            return Err(format!("spawn {h:?} cannot reach {:?}", all[0]));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generation_is_deterministic() {
        let a = generate(99, 4, 5);
        let b = generate(99, 4, 5);
        assert_eq!(a.player_spawns, b.player_spawns);
        assert_eq!(a.enemy_spawns, b.enemy_spawns);
        assert!(a.map.hexes().all(|h| a.map.terrain(h) == b.map.terrain(h)));
    }

    #[test]
    fn carving_connects_a_walled_off_spawn() {
        let mut map = Map::new(MAP_W, MAP_H, Terrain::Plain);
        for row in 0..MAP_H {
            map.set(Hex::from_offset(6, row), Terrain::Rock);
        }
        let a = Hex::from_offset(1, 4);
        let b = Hex::from_offset(10, 4);
        let reached = connected_from(&map, a);
        assert!(!reached[map.index(b).unwrap()]);
        carve_to(&mut map, &reached, b);
        assert!(connected_from(&map, a)[map.index(b).unwrap()]);
    }
}
