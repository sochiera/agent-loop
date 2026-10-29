//! Units, archetypes and weapons. All numbers live here as plain data.

use crate::hex::Hex;

pub type UnitId = usize;

#[derive(Copy, Clone, Debug, PartialEq, Eq, Hash)]
pub enum Team {
    Player,
    Enemy,
}

impl Team {
    pub fn opponent(self) -> Team {
        match self {
            Team::Player => Team::Enemy,
            Team::Enemy => Team::Player,
        }
    }
}

#[derive(Copy, Clone, Debug, PartialEq, Eq, Hash)]
pub enum Archetype {
    Swordsman,
    ShieldBearer,
    Archer,
    Halberdier,
    Raider,
    BanditArcher,
    Brute,
}

#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum AttackKind {
    Melee,
    Ranged,
}

#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub struct Weapon {
    pub name: &'static str,
    pub dmg_min: i32,
    pub dmg_max: i32,
    pub ap_cost: i32,
    /// Added to the attacker's skill.
    pub hit_mod: i32,
    /// Percent of each hit that bypasses armor.
    pub armor_ignore: i32,
    pub range_min: i32,
    pub range_max: i32,
}

impl Weapon {
    pub fn avg_damage(&self) -> f32 {
        (self.dmg_min + self.dmg_max) as f32 / 2.0
    }
}

/// Max HP gained per level above 1.
pub const HP_PER_LEVEL: i32 = 5;
/// Melee and ranged skill gained per level above 1 (only for skills the archetype has).
pub const SKILL_PER_LEVEL: i32 = 2;
/// Defense gained per level above 1.
pub const DEFENSE_PER_LEVEL: i32 = 1;

/// Stat increase granted by levelling up (per level, or summed over several).
#[derive(Copy, Clone, Debug, Default, PartialEq, Eq)]
pub struct LevelBonus {
    pub hp: i32,
    pub skill: i32,
    pub defense: i32,
}

impl LevelBonus {
    /// Total bonus of a unit that has gone from level 1 to `level`.
    pub fn for_level(level: u32) -> LevelBonus {
        let n = level.saturating_sub(1) as i32;
        LevelBonus { hp: n * HP_PER_LEVEL, skill: n * SKILL_PER_LEVEL, defense: n * DEFENSE_PER_LEVEL }
    }

    /// Bonus gained going from level `from` up to level `to`.
    pub fn between(from: u32, to: u32) -> LevelBonus {
        let (a, b) = (LevelBonus::for_level(from), LevelBonus::for_level(to));
        LevelBonus { hp: b.hp - a.hp, skill: b.skill - a.skill, defense: b.defense - a.defense }
    }
}

#[derive(Copy, Clone, Debug)]
pub struct Stats {
    pub max_hp: i32,
    pub max_armor: i32,
    pub defense: i32,
    pub initiative: i32,
    pub melee_skill: i32,
    pub ranged_skill: i32,
}

const fn melee(name: &'static str, lo: i32, hi: i32, ap: i32, hit_mod: i32, ignore: i32) -> Weapon {
    Weapon { name, dmg_min: lo, dmg_max: hi, ap_cost: ap, hit_mod, armor_ignore: ignore, range_min: 1, range_max: 1 }
}

const fn bow(name: &'static str, lo: i32, hi: i32, ap: i32, hit_mod: i32, ignore: i32) -> Weapon {
    Weapon { name, dmg_min: lo, dmg_max: hi, ap_cost: ap, hit_mod, armor_ignore: ignore, range_min: 2, range_max: 6 }
}

impl Archetype {
    pub fn label(self) -> &'static str {
        match self {
            Archetype::Swordsman => "Swordsman",
            Archetype::ShieldBearer => "Shield-bearer",
            Archetype::Archer => "Archer",
            Archetype::Halberdier => "Halberdier",
            Archetype::Raider => "Raider",
            Archetype::BanditArcher => "Bandit Archer",
            Archetype::Brute => "Brute",
        }
    }

    /// One-letter badge used by the UI.
    pub fn glyph(self) -> char {
        match self {
            Archetype::Swordsman => 'S',
            Archetype::ShieldBearer => 'B',
            Archetype::Archer => 'A',
            Archetype::Halberdier => 'H',
            Archetype::Raider => 'R',
            Archetype::BanditArcher => 'X',
            Archetype::Brute => 'U',
        }
    }

    pub fn description(self) -> &'static str {
        match self {
            Archetype::Swordsman => "Accurate melee fighter",
            Archetype::ShieldBearer => "Very hard to hit, weak blows",
            Archetype::Archer => "Bow, range 2-6, needs line of sight",
            Archetype::Halberdier => "Slow heavy hits that pierce armor",
            Archetype::Raider => "Axe-wielding bandit",
            Archetype::BanditArcher => "Short bow, range 2-6",
            Archetype::Brute => "Huge, tough, hits very hard",
        }
    }

    /// Base stats of a level-1 unit.
    pub fn stats(self) -> Stats {
        let (max_hp, max_armor, defense, initiative, melee_skill, ranged_skill) = match self {
            Archetype::Swordsman => (60, 30, 20, 100, 72, 0),
            Archetype::ShieldBearer => (70, 45, 35, 70, 62, 0),
            Archetype::Archer => (45, 10, 10, 90, 45, 76),
            Archetype::Halberdier => (55, 25, 5, 60, 64, 0),
            Archetype::Raider => (55, 20, 12, 80, 64, 0),
            Archetype::BanditArcher => (35, 5, 8, 95, 45, 62),
            Archetype::Brute => (100, 30, 0, 50, 64, 0),
        };
        Stats { max_hp, max_armor, defense, initiative, melee_skill, ranged_skill }
    }

    /// Stats of a persistent unit of the given level (base stats plus [`LevelBonus`]).
    pub fn stats_at_level(self, level: u32) -> Stats {
        let mut s = self.stats();
        let b = LevelBonus::for_level(level);
        s.max_hp += b.hp;
        s.defense += b.defense;
        if s.melee_skill > 0 {
            s.melee_skill += b.skill;
        }
        if s.ranged_skill > 0 {
            s.ranged_skill += b.skill;
        }
        s
    }

    /// Weapon used against adjacent targets.
    pub fn melee_weapon(self) -> Weapon {
        match self {
            Archetype::Swordsman => melee("Sword", 18, 26, 4, 0, 15),
            Archetype::ShieldBearer => melee("Spear", 10, 16, 4, 0, 0),
            Archetype::Archer => melee("Dagger", 6, 10, 4, 0, 10),
            Archetype::Halberdier => melee("Halberd", 24, 34, 5, -5, 30),
            Archetype::Raider => melee("Hand axe", 16, 24, 4, 0, 10),
            Archetype::BanditArcher => melee("Knife", 5, 9, 4, 0, 5),
            Archetype::Brute => melee("Maul", 26, 38, 5, -5, 25),
        }
    }

    /// Weapon used against targets at distance `range_min..=range_max`.
    pub fn ranged_weapon(self) -> Option<Weapon> {
        match self {
            Archetype::Archer => Some(bow("Hunting bow", 16, 24, 5, 0, 20)),
            Archetype::BanditArcher => Some(bow("Short bow", 12, 18, 5, 0, 15)),
            _ => None,
        }
    }
}

#[derive(Clone, Debug)]
pub struct Unit {
    pub id: UnitId,
    pub name: String,
    pub team: Team,
    pub archetype: Archetype,
    pub pos: Hex,
    pub stats: Stats,
    pub hp: i32,
    pub armor: i32,
    pub ap: i32,
    pub alive: bool,
    pub level: u32,
    /// Enemies this unit has killed in the current battle.
    pub kills: u32,
    /// Campaign roster id for persistent mercenaries.
    pub roster_id: Option<u32>,
}

impl Unit {
    pub fn new(id: UnitId, name: &str, team: Team, archetype: Archetype, pos: Hex) -> Unit {
        let stats = archetype.stats();
        Unit {
            id,
            name: name.to_string(),
            team,
            archetype,
            pos,
            hp: stats.max_hp,
            armor: stats.max_armor,
            ap: 0,
            alive: true,
            stats,
            level: 1,
            kills: 0,
            roster_id: None,
        }
    }

    /// A persistent player mercenary: level-based stats, carried-over (possibly wounded) HP,
    /// armor repaired to full.
    pub fn mercenary(id: UnitId, roster_id: u32, name: &str, archetype: Archetype, level: u32, hp: i32, pos: Hex) -> Unit {
        let stats = archetype.stats_at_level(level);
        Unit {
            id,
            name: name.to_string(),
            team: Team::Player,
            archetype,
            pos,
            hp: hp.clamp(1, stats.max_hp),
            armor: stats.max_armor,
            ap: 0,
            alive: true,
            stats,
            level,
            kills: 0,
            roster_id: Some(roster_id),
        }
    }

    pub fn weapon(&self, kind: AttackKind) -> Option<Weapon> {
        match kind {
            AttackKind::Melee => Some(self.archetype.melee_weapon()),
            AttackKind::Ranged => self.archetype.ranged_weapon(),
        }
    }

    pub fn skill(&self, kind: AttackKind) -> i32 {
        match kind {
            AttackKind::Melee => self.stats.melee_skill,
            AttackKind::Ranged => self.stats.ranged_skill,
        }
    }
}
