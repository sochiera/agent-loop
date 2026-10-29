//! The one fixed battle. APPROXIMATION: all numbers are invented for the slice.

use crate::BattleState;
use crate::hex::{Hex, hex, map_hexes};
use crate::terrain::TerrainKind;
use crate::unit::{MeleeReach, Side, Stats, Unit};

/// Resolve of every scenario unit. APPROXIMATION: invented slice value, not a
/// catalog or runtime number.
pub const DEFAULT_RESOLVE: u32 = 50;

fn stats(hp: u32, armour: u32, initiative: u32, melee_skill: u32, defense: u32, dmg_min: u32, dmg_max: u32) -> Stats {
    Stats { hp, armour, ap_max: 9, fatigue_max: 100, initiative, melee_skill, defense, resolve: DEFAULT_RESOLVE, dmg_min, dmg_max }
}

pub fn units() -> Vec<Unit> {
    let p = |id, name, pos: Hex| Unit::new(id, name, Side::Player, stats(60, 40, 100, 60, 10, 20, 35), pos);
    let e = |id, name, pos: Hex| Unit::new(id, name, Side::Enemy, stats(50, 20, 90, 50, 5, 15, 30), pos);
    vec![
        p(0, "P1", hex(-3, 3)),
        p(1, "P2", hex(-4, 3)),
        p(2, "P3", hex(-3, 2)),
        e(3, "E1", hex(3, -3)),
        e(4, "E2", hex(4, -3)),
        e(5, "E3", hex(3, -2)),
    ]
}

/// Terrain of the fixed battle. `Flat` is the default scenario. `Hills` changes
/// heights; `Swamp` assigns a ground-kind layout and keeps height zero.
/// Units, stats, placement and the RNG stream are the same across presets.
/// APPROXIMATION: the hand-authored hill and Swamp layouts are slice designs;
/// the hill rules effects come from the UC-COMBAT-010 catalog text.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum TerrainPreset {
    #[default]
    Flat,
    Hills,
    Swamp,
}

impl TerrainPreset {
    /// Case-sensitive command-line spelling: `flat`, `hills` or `swamp`.
    pub fn parse(s: &str) -> Option<TerrainPreset> {
        match s {
            "flat" => Some(TerrainPreset::Flat),
            "hills" => Some(TerrainPreset::Hills),
            "swamp" => Some(TerrainPreset::Swamp),
            _ => None,
        }
    }

    /// The height this preset gives an in-map hex. Hills: 2 on the `q == 0`
    /// column, 1 on `q == -1` and `q == 1`, 0 elsewhere, with (0,-1) and (0,1)
    /// raised to 3. Obstacles are included; passability is untouched. Flat and
    /// Swamp have height zero everywhere.
    pub fn height_at(self, at: Hex) -> u8 {
        match self {
            TerrainPreset::Flat | TerrainPreset::Swamp => 0,
            TerrainPreset::Hills if at == hex(0, -1) || at == hex(0, 1) => 3,
            TerrainPreset::Hills => match at.q {
                0 => 2,
                -1 | 1 => 1,
                _ => 0,
            },
        }
    }

    /// The ground kind this preset gives an in-map hex. Only Swamp's passable
    /// hexes in the `q == -1`, `q == 0` and `q == 1` columns are Swamp; obstacles
    /// and all other hexes remain Ground. Flat and Hills are Ground everywhere.
    pub fn ground_at(self, at: Hex) -> TerrainKind {
        match self {
            TerrainPreset::Swamp if !at.is_obstacle() && (-1..=1).contains(&at.q) => {
                TerrainKind::Swamp
            }
            TerrainPreset::Flat | TerrainPreset::Hills | TerrainPreset::Swamp => TerrainKind::Ground,
        }
    }
}

/// The scenario battle for `seed` on the given terrain. `Flat` is exactly
/// `BattleState::new(seed)`; both terrain layers are configured through their
/// map APIs and consume no RNG. Swamp's hand-authored layout is an APPROXIMATION.
pub fn battle(seed: u64, preset: TerrainPreset) -> BattleState {
    let mut state = BattleState::new(seed);
    for at in map_hexes() {
        state.elevations.set(at, preset.height_at(at)).expect("preset heights are in range on in-map hexes");
        state.ground.set(at, preset.ground_at(at)).expect("preset ground kinds are in-map hexes");
    }
    state
}

/// The scenario battle with the opt-in Adrenaline loadout: `battle(seed, preset)`
/// with `has_adrenaline` set on every player unit and none on enemies. Nothing is
/// pending, no RNG is consumed and the RNG stream equals the default scenario's.
/// APPROXIMATION: a launch option for the playable slice, not a perk or skill
/// system (UC-COMBAT-066 is the catalog authority for the skill itself).
pub fn adrenaline_battle(seed: u64, preset: TerrainPreset) -> BattleState {
    let mut state = battle(seed, preset);
    for u in state.units.iter_mut().filter(|u| u.side == Side::Player) {
        u.has_adrenaline = true;
    }
    state
}

/// The scenario battle with the opt-in Rotation loadout: `battle(seed, preset)`
/// with `has_rotation` set on every player unit and none on enemies. Nothing else
/// changes, no RNG is consumed and the RNG stream equals the default scenario's.
/// APPROXIMATION: a launch option for the playable slice, not a perk or skill
/// system (UC-COMBAT-039 is the catalog authority for the skill itself).
pub fn rotation_battle(seed: u64, preset: TerrainPreset) -> BattleState {
    let mut state = battle(seed, preset);
    for u in state.units.iter_mut().filter(|u| u.side == Side::Player) {
        u.has_rotation = true;
    }
    state
}

/// The scenario battle with the opt-in Footwork loadout: `battle(seed, preset)`
/// with `has_footwork` set on every player unit and none on enemies. Nothing else
/// changes, no RNG is consumed and the RNG stream equals the default scenario's.
/// APPROXIMATION: a launch option for the playable slice, not a perk or skill
/// system (UC-COMBAT-039 is the catalog authority for the skill itself).
pub fn footwork_battle(seed: u64, preset: TerrainPreset) -> BattleState {
    let mut state = battle(seed, preset);
    for u in state.units.iter_mut().filter(|u| u.side == Side::Player) {
        u.has_footwork = true;
    }
    state
}

/// The battle a launch configuration selects: the opt-in Adrenaline loadout when
/// `adrenaline` is set, otherwise exactly `battle(seed, preset)`.
pub fn launch_battle(seed: u64, preset: TerrainPreset, adrenaline: bool) -> BattleState {
    launch_battle_with(seed, preset, adrenaline, false)
}

/// The battle for a full launch configuration: `battle(seed, preset)` with each
/// selected skill capability set on player units only. The flags compose and
/// are independent; with none set the result is exactly `battle(seed, preset)`.
pub fn launch_battle_with(seed: u64, preset: TerrainPreset, adrenaline: bool, rotation: bool) -> BattleState {
    launch_battle_for(LaunchArgs { seed, terrain: preset, adrenaline, rotation, footwork: false, fast_adaptation: false, dodge: false, nine_lives: false, berserk: false, pathfinder: false, indomitable: false, two_hex: false, stunning_blow: false, killing_frenzy: false })
}

/// The battle a full `LaunchArgs` selects: `battle(args.seed, args.terrain)` with each
/// selected capability (`adrenaline`, `rotation`, `footwork`, `fast_adaptation`, `dodge`,
/// `nine_lives`, `berserk`, `pathfinder`, `indomitable`, `stunning_blow`, `killing_frenzy`) set on player units only; `two_hex`
/// likewise gives player units `MeleeReach::TwoHex` while enemies keep `Adjacent`; `stunning_blow`
/// and `killing_frenzy` grant their capabilities to player units only. Fast Adaptation, Dodge, Nine Lives and
/// Berserk are passives: stacks start at zero and Nine Lives and Berserk start unused
/// (`nine_lives_used` and `berserk_used` stay false); Killing Frenzy counters stay zero.
/// Indomitable starts with the stance
/// inactive (`indomitable_active` stays false). The flags compose and are independent;
/// with none set the result is exactly `battle(seed, terrain)`. Nothing else changes and no
/// RNG is consumed. This is the constructor both binaries share.
pub fn launch_battle_for(args: LaunchArgs) -> BattleState {
    let mut state = battle(args.seed, args.terrain);
    for u in state.units.iter_mut().filter(|u| u.side == Side::Player) {
        u.has_adrenaline = args.adrenaline;
        u.has_rotation = args.rotation;
        u.has_footwork = args.footwork;
        u.has_fast_adaptation = args.fast_adaptation;
        u.has_dodge = args.dodge;
        u.has_nine_lives = args.nine_lives;
        u.has_berserk = args.berserk;
        u.has_pathfinder = args.pathfinder;
        u.has_indomitable = args.indomitable;
        u.has_stunning_blow = args.stunning_blow;
        u.has_killing_frenzy = args.killing_frenzy;
        if args.two_hex {
            u.melee_reach = MeleeReach::TwoHex;
        }
    }
    state
}

/// Parsed launch arguments shared by both binaries.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct LaunchArgs {
    pub seed: u64,
    pub terrain: TerrainPreset,
    /// `--adrenaline`: give player units the Adrenaline capability.
    pub adrenaline: bool,
    /// `--rotation`: give player units the Rotation capability.
    pub rotation: bool,
    /// `--footwork`: give player units the Footwork capability.
    pub footwork: bool,
    /// `--fast-adaptation`: give player units the passive Fast Adaptation capability
    /// (zero initial stacks). APPROXIMATION: a launch option, not a perk system
    /// (UC-COMBAT-067 is the catalog authority for the passive itself).
    pub fast_adaptation: bool,
    /// `--dodge`: give player units the passive melee Dodge capability. APPROXIMATION:
    /// a player-only launch option, not a perk system (UC-COMBAT-058 is the catalog
    /// authority for the passive itself).
    pub dodge: bool,
    /// `--nine-lives`: give player units the passive Nine Lives survival capability, unused.
    /// APPROXIMATION: a player-only launch option, not a perk system (UC-COMBAT-059 is the
    /// catalog authority for the passive itself; coverage there stays partial).
    pub nine_lives: bool,
    /// `--berserk`: give player units the passive melee Berserk capability. APPROXIMATION:
    /// a player-only launch option, not a perk system (UC-COMBAT-038 is the catalog authority
    /// for the passive itself).
    pub berserk: bool,
    /// `--pathfinder`: give player units the passive Pathfinder movement-fatigue capability.
    /// APPROXIMATION: player-only launch assignment, not a perk system (UC-COMBAT-057 is the
    /// catalog authority for the passive itself).
    pub pathfinder: bool,
    /// `--indomitable`: give player units the Indomitable stance capability, inactive.
    /// APPROXIMATION: player-only launch assignment, not a perk system (UC-COMBAT-016 stays
    /// unconfirmed in the catalog; costs and the melee-halving effect are approximations).
    pub indomitable: bool,
    /// `--two-hex`: give player units `MeleeReach::TwoHex` (enemies keep `Adjacent`).
    /// APPROXIMATION: a player-only launch assignment of the accepted reach rule
    /// (UC-COMBAT-051, catalog-stated, not runtime-confirmed), not a polearm,
    /// weapon or equipment system; no stats, resources, positions or RNG change.
    pub two_hex: bool,
    /// `--stunning-blow`: give player units the Stunning Blow capability. This is an
    /// **APPROXIMATION** launch assignment, not skill learning or a weapon system.
    pub stunning_blow: bool,
    /// `--killing-frenzy`: give player units the passive capability with counter zero.
    /// **APPROXIMATION** launch assignment, not perk learning.
    pub killing_frenzy: bool,
}

/// Shared `--seed N` / `--terrain flat|hills|swamp` / `--adrenaline` / `--rotation` /
/// `--footwork` / `--fast-adaptation` / `--dodge` / `--nine-lives` / `--berserk` /
/// `--pathfinder` / `--indomitable` / `--two-hex` / `--stunning-blow` / `--killing-frenzy` parsing of both binaries (the program name already skipped).
/// Defaults: seed 1, flat, no loadout. The capability flags are value-less. Unknown arguments are ignored;
/// a later repeat of a value-taking option wins. The error is a concise message.
pub fn parse_args<I: IntoIterator<Item = String>>(args: I) -> Result<LaunchArgs, String> {
    let mut parsed = LaunchArgs { seed: 1, terrain: TerrainPreset::Flat, adrenaline: false, rotation: false, footwork: false, fast_adaptation: false, dodge: false, nine_lives: false, berserk: false, pathfinder: false, indomitable: false, two_hex: false, stunning_blow: false, killing_frenzy: false };
    let mut args = args.into_iter();
    while let Some(a) = args.next() {
        match a.as_str() {
            "--seed" => parsed.seed = args.next().and_then(|v| v.parse().ok()).ok_or("--seed needs a u64")?,
            "--terrain" => {
                parsed.terrain = args.next().and_then(|v| TerrainPreset::parse(&v)).ok_or("--terrain needs flat, hills or swamp")?
            }
            "--adrenaline" => parsed.adrenaline = true,
            "--rotation" => parsed.rotation = true,
            "--footwork" => parsed.footwork = true,
            "--fast-adaptation" => parsed.fast_adaptation = true,
            "--dodge" => parsed.dodge = true,
            "--nine-lives" => parsed.nine_lives = true,
            "--berserk" => parsed.berserk = true,
            "--pathfinder" => parsed.pathfinder = true,
            "--indomitable" => parsed.indomitable = true,
            "--two-hex" => parsed.two_hex = true,
            "--stunning-blow" => parsed.stunning_blow = true,
            "--killing-frenzy" => parsed.killing_frenzy = true,
            _ => {}
        }
    }
    Ok(parsed)
}
