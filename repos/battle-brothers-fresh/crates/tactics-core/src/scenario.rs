//! Hand-authored encounter used by the first playable slice.

use crate::battle::Battle;
use crate::hex::Hex;
use crate::map::Map;
use crate::unit::{Archetype, Team, Unit};

/// 12x10 map: a river with fords in the middle, forests and hills on both sides.
/// `.` plain, `f` forest, `h` hill, `#` rock, `~` water.
pub const MAP_ROWS: [&str; 10] = [
    "..f...#h....",
    ".ff..~~hh...",
    "..f..~#..f..",
    "....~~...ff.",
    "..h.......f.",
    ".hh...#.....",
    "..h..~~...h.",
    "....~~#..hh.",
    ".f...~..f.h.",
    "ff..#...ff..",
];

pub fn skirmish_map() -> Map {
    Map::from_ascii(&MAP_ROWS)
}

/// The player's four mercenaries against five bandits. `seed` drives every dice roll.
pub fn skirmish(seed: u64) -> Battle {
    let at = |col, row| Hex::from_offset(col, row);
    let roster: [(&str, Team, Archetype, Hex); 9] = [
        ("Brannoch", Team::Player, Archetype::Swordsman, at(2, 4)),
        ("Teague", Team::Player, Archetype::ShieldBearer, at(2, 3)),
        ("Wren", Team::Player, Archetype::Archer, at(1, 5)),
        ("Halvard", Team::Player, Archetype::Halberdier, at(2, 6)),
        ("Raider 1", Team::Enemy, Archetype::Raider, at(9, 3)),
        ("Raider 2", Team::Enemy, Archetype::Raider, at(8, 4)),
        ("Raider 3", Team::Enemy, Archetype::Raider, at(9, 5)),
        ("Bandit Archer", Team::Enemy, Archetype::BanditArcher, at(10, 4)),
        ("Brute", Team::Enemy, Archetype::Brute, at(10, 6)),
    ];
    let units = roster
        .iter()
        .enumerate()
        .map(|(id, (name, team, arch, pos))| Unit::new(id, name, *team, *arch, *pos))
        .collect();
    Battle::new(skirmish_map(), units, seed)
}
