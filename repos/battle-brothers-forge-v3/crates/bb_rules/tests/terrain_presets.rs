//! Terrain presets of the scenario battle. Hills' hand-authored height layout
//! is an APPROXIMATION; Swamp layout and movement additions are covered in
//! `swamp_preset.rs`.

use std::process::Command as Process;

use bb_rules::hex::{hex, map_hexes};
use bb_rules::scenario::{
    LaunchArgs, TerrainPreset, adrenaline_battle, battle, footwork_battle, launch_battle, launch_battle_for, launch_battle_with, parse_args,
    rotation_battle,
};
use bb_rules::terrain::MAX_ELEVATION;
use bb_rules::*;

const MAX_COMMANDS: u32 = 10_000;
const SEEDS: [u64; 5] = [1, 2, 7, 23, 99];
const FLAT_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=dfce9b8d74bcaf93";
const HILLS_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=d0b704f0294173be";
const ROTATION_FLAT_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=eca98bcb3e525dfb";
const ROTATION_HILLS_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=17dd25afecfd72b6";
const FOOTWORK_FLAT_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=26f421838e407143";
const FOOTWORK_HILLS_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=9f83d1d40f1a2f6e";
const ROTATION_FOOTWORK_FLAT_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=dd2db2bc7d2f96eb";
const ROTATION_FOOTWORK_HILLS_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=c951d786ff9ab3e6";
const ADRENALINE_FLAT_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=d38ca185d725df1b";
const ADRENALINE_HILLS_SEED_1: &str = "OUTCOME=Victory REASON=Elimination ROUNDS=7 HASH=406c2bf145855863";
const ADRENALINE_ROTATION_FLAT_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=6b94625d3560a3b3";
const ADRENALINE_ROTATION_HILLS_SEED_1: &str = "OUTCOME=Victory REASON=Elimination ROUNDS=7 HASH=d91435785603942b";
const ADRENALINE_FOOTWORK_FLAT_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=dcd3fff2deaf550b";
const ADRENALINE_FOOTWORK_HILLS_SEED_1: &str = "OUTCOME=Victory REASON=Elimination ROUNDS=7 HASH=4cea2c6e5d5bd5d3";
const ALL_SKILLS_FLAT_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=fdc85eae43dd0ba3";
const ALL_SKILLS_HILLS_SEED_1: &str = "OUTCOME=Victory REASON=Elimination ROUNDS=7 HASH=93baae9abf5c755b";

fn expected_hills_height(h: Hex) -> u8 {
    if h == hex(0, -1) || h == hex(0, 1) {
        3
    } else {
        match h.q {
            0 => 2,
            -1 | 1 => 1,
            _ => 0,
        }
    }
}

#[test]
fn hills_heights_are_exact_on_every_map_hex_and_in_range() {
    let s = battle(1, TerrainPreset::Hills);
    let hexes = map_hexes();
    assert_eq!(hexes.len(), 61);
    for &h in &hexes {
        let want = expected_hills_height(h);
        assert!(want <= MAX_ELEVATION);
        assert_eq!(s.elevations.get(h), Some(want), "{h:?}");
        assert_eq!(TerrainPreset::Hills.height_at(h), want, "{h:?}");
    }
    // Spot checks of the documented layout.
    assert_eq!(s.elevations.get(hex(0, 0)), Some(2));
    assert_eq!(s.elevations.get(hex(0, -1)), Some(3));
    assert_eq!(s.elevations.get(hex(0, 1)), Some(3));
    assert_eq!(s.elevations.get(hex(0, -4)), Some(2));
    assert_eq!(s.elevations.get(hex(-1, 0)), Some(1));
    assert_eq!(s.elevations.get(hex(1, -3)), Some(1));
    assert_eq!(s.elevations.get(hex(2, 0)), Some(0));
    assert_eq!(s.elevations.get(hex(-4, 3)), Some(0));
    assert_eq!(s.elevations.get(hex(5, 0)), None, "off the map");
    // Entries equal exactly the nonzero hexes, in axial order.
    let nonzero: Vec<(Hex, u8)> = hexes.iter().map(|&h| (h, expected_hills_height(h))).filter(|&(_, v)| v > 0).collect();
    assert_eq!(s.elevations.entries().collect::<Vec<_>>(), nonzero);
    assert!(!s.elevations.is_flat());
}

#[test]
fn hills_covers_obstacles_without_changing_passability() {
    let s = battle(1, TerrainPreset::Hills);
    let flat = battle(1, TerrainPreset::Flat);
    let obstacles: Vec<Hex> = map_hexes().into_iter().filter(|h| h.is_obstacle()).collect();
    assert!(!obstacles.is_empty());
    assert!(obstacles.iter().any(|&h| s.elevations.get(h).unwrap() > 0), "some obstacle carries a height");
    for h in map_hexes() {
        assert_eq!(h.is_obstacle(), obstacles.contains(&h));
    }
    // Obstacles stay impassable on both maps.
    for h in obstacles {
        assert_eq!(s.preview_move(0, h).err(), Some(RuleError::Unreachable), "{h:?}");
        assert_eq!(flat.preview_move(0, h).err(), Some(RuleError::Unreachable), "{h:?}");
    }
}

#[test]
fn flat_is_exactly_battle_new() {
    for seed in SEEDS {
        let f = battle(seed, TerrainPreset::Flat);
        let n = BattleState::new(seed);
        assert_eq!(TerrainPreset::default(), TerrainPreset::Flat);
        assert!(f.elevations.is_flat());
        assert_eq!(f.elevations, n.elevations);
        assert_eq!(f.units, n.units);
        assert_eq!(f.rng, n.rng);
        assert_eq!(f.queue, n.queue);
        assert_eq!(f.round, n.round);
        assert_eq!(f.state_hash(), n.state_hash());
    }
}

#[test]
fn hills_changes_only_elevations_and_consumes_no_rng() {
    for seed in SEEDS {
        let h = battle(seed, TerrainPreset::Hills);
        let n = BattleState::new(seed);
        assert_eq!(h.units, n.units, "units, stats and placement");
        assert_eq!(h.rng, n.rng, "RNG stream untouched");
        assert_eq!(h.queue, n.queue);
        assert_eq!((h.round, h.outcome, h.end_reason), (n.round, n.outcome, n.end_reason));
        assert_ne!(h.elevations, n.elevations);
    }
}

#[test]
fn construction_is_deterministic_and_the_hash_is_terrain_sensitive() {
    for seed in SEEDS {
        let a = battle(seed, TerrainPreset::Hills);
        let b = battle(seed, TerrainPreset::Hills);
        assert_eq!(a.state_hash(), b.state_hash());
        assert_eq!(a.elevations, b.elevations);
        assert_ne!(a.state_hash(), battle(seed, TerrainPreset::Flat).state_hash());
        // Any single height change alters the hash.
        let mut c = a.clone();
        c.elevations.set(hex(2, 0), 1).unwrap();
        assert_ne!(c.state_hash(), a.state_hash());
    }
    assert_ne!(battle(1, TerrainPreset::Hills).state_hash(), battle(2, TerrainPreset::Hills).state_hash());
}

#[test]
fn preset_names_parse_exactly() {
    assert_eq!(TerrainPreset::parse("flat"), Some(TerrainPreset::Flat));
    assert_eq!(TerrainPreset::parse("hills"), Some(TerrainPreset::Hills));
    for bad in ["", "Flat", "HILLS", "hill", "mountains", " flat"] {
        assert_eq!(TerrainPreset::parse(bad), None, "{bad:?}");
    }
}

fn replay(seed: u64) -> (Vec<Event>, BattleState, u32) {
    let mut s = battle(seed, TerrainPreset::Hills);
    let mut events = Vec::new();
    let mut n = 0;
    while s.outcome().is_none() {
        let cmds = ai::plan_turn(&s);
        let cmd = *cmds.first().expect("plan_turn returns a command during a running battle");
        n += 1;
        assert!(n <= MAX_COMMANDS, "seed {seed} exceeded the smoke bound");
        events.extend(s.apply(cmd).unwrap_or_else(|e| panic!("seed {seed}: {e:?} from {cmd:?}")));
    }
    (events, s, n)
}

fn replay_loadout(args: LaunchArgs) -> (Vec<Command>, Vec<Event>, BattleState, u32) {
    let mut state = launch_battle_for(args);
    let mut commands = Vec::new();
    let mut events = Vec::new();
    let mut n = 0;
    while state.outcome().is_none() {
        let cmds = ai::plan_turn(&state);
        let command = *cmds
            .first()
            .expect("plan_turn returns a command during a running battle");
        n += 1;
        assert!(n <= MAX_COMMANDS, "{args:?} exceeded the smoke bound");
        commands.push(command);
        events.extend(state.apply(command).unwrap_or_else(|e| {
            panic!("{args:?}: {e:?} from {command:?}")
        }));
    }
    (commands, events, state, n)
}

#[test]
fn hills_ai_replay_is_deterministic_valid_and_terminates() {
    for seed in SEEDS {
        let (ea, a, na) = replay(seed);
        let (eb, b, nb) = replay(seed);
        assert_eq!(ea, eb, "seed {seed}: identical events");
        assert_eq!((na, a.state_hash(), a.outcome(), a.end_reason(), a.round), (nb, b.state_hash(), b.outcome(), b.end_reason(), b.round));
        assert_eq!(a.rng, b.rng);
        assert!(matches!(ea.last(), Some(Event::BattleEnded { .. })));
        assert_eq!(a.elevations, battle(seed, TerrainPreset::Hills).elevations, "heights never change during play");
    }
}

fn args(a: &[&str]) -> Result<(u64, TerrainPreset, bool), String> {
    parse_args(a.iter().map(|s| s.to_string())).map(|p| (p.seed, p.terrain, p.adrenaline))
}

#[test]
fn argument_parsing_covers_omitted_flat_hills_swamp_missing_and_invalid() {
    assert_eq!(args(&[]), Ok((1, TerrainPreset::Flat, false)));
    assert_eq!(args(&["--seed", "5"]), Ok((5, TerrainPreset::Flat, false)));
    assert_eq!(args(&["--terrain", "flat"]), Ok((1, TerrainPreset::Flat, false)));
    assert_eq!(args(&["--terrain", "hills"]), Ok((1, TerrainPreset::Hills, false)));
    assert_eq!(args(&["--terrain", "swamp"]), Ok((1, TerrainPreset::Swamp, false)));
    assert_eq!(args(&["--seed", "9", "--terrain", "hills"]), Ok((9, TerrainPreset::Hills, false)));
    assert_eq!(args(&["--terrain", "hills", "--seed", "9"]), Ok((9, TerrainPreset::Hills, false)));
    assert_eq!(args(&["--terrain", "hills", "--terrain", "flat"]), Ok((1, TerrainPreset::Flat, false)), "last wins");
    assert_eq!(args(&["--terrain", "flat", "--terrain", "swamp"]), Ok((1, TerrainPreset::Swamp, false)), "last wins");
    assert_eq!(args(&["--terrain", "swamp", "--terrain", "hills"]), Ok((1, TerrainPreset::Hills, false)), "last wins");
    let composed = parse_args(["--seed", "9", "--terrain", "swamp", "--adrenaline", "--rotation", "--footwork", "--fast-adaptation", "--dodge", "--nine-lives", "--berserk", "--pathfinder", "--indomitable"].map(String::from)).unwrap();
    assert_eq!((composed.seed, composed.terrain), (9, TerrainPreset::Swamp));
    assert!(composed.adrenaline && composed.rotation && composed.footwork && composed.fast_adaptation && composed.dodge && composed.nine_lives && composed.berserk && composed.pathfinder && composed.indomitable);
    assert_eq!(args(&["--other", "--seed", "3"]), Ok((3, TerrainPreset::Flat, false)), "unknown arguments are ignored as before");
    for bad in [&["--terrain"][..], &["--terrain", "mountains"], &["--terrain", "Hills"], &["--terrain", "Swamp"], &["--terrain", "SWAMP"], &["--terrain", ""], &["--terrain", "--seed", "3"], &["--seed", "3", "--terrain"]] {
        assert_eq!(args(bad), Err("--terrain needs flat, hills or swamp".to_string()), "{bad:?}");
    }
    for bad in [&["--seed"][..], &["--seed", "x"], &["--seed", "-1"], &["--terrain", "hills", "--seed"]] {
        assert_eq!(args(bad), Err("--seed needs a u64".to_string()), "{bad:?}");
    }
}

fn smoke(extra: &[&str]) -> std::process::Output {
    Process::new(env!("CARGO_BIN_EXE_headless_smoke")).args(extra).output().expect("run headless_smoke")
}

#[test]
fn headless_smoke_selects_terrain_and_rejects_bad_values() {
    let run = |extra: &[&str]| {
        let out = smoke(extra);
        assert!(out.status.success(), "{extra:?}: {}", String::from_utf8_lossy(&out.stderr));
        String::from_utf8(out.stdout).unwrap().trim().to_string()
    };
    assert_eq!(run(&["--seed", "1"]), FLAT_SEED_1);
    assert_eq!(run(&["--seed", "1", "--terrain", "flat"]), FLAT_SEED_1);
    assert_eq!(run(&[]), FLAT_SEED_1);
    let hills = run(&["--seed", "1", "--terrain", "hills"]);
    assert_eq!(hills, HILLS_SEED_1);
    // The binary agrees with an in-process replay of the same preset.
    let (_, s, _) = replay(1);
    assert!(hills.ends_with(&format!("HASH={:016x}", s.state_hash())), "{hills}");

    for (extra, msg) in [
        (&["--terrain"][..], "--terrain needs flat, hills or swamp"),
        (&["--terrain", "mountains"], "--terrain needs flat, hills or swamp"),
        (&["--seed", "2", "--terrain", "x"], "--terrain needs flat, hills or swamp"),
        (&["--seed"], "--seed needs a u64"),
    ] {
        let out = smoke(extra);
        assert_eq!(out.status.code(), Some(2), "{extra:?}");
        assert!(out.stdout.is_empty(), "no result line on a usage error");
        assert_eq!(String::from_utf8_lossy(&out.stderr).trim(), msg);
    }
}

// --- Opt-in Adrenaline loadout (`--adrenaline`) ------------------------------------

#[test]
fn adrenaline_flag_parses_and_composes_with_terrain_and_seed() {
    assert_eq!(args(&["--adrenaline"]), Ok((1, TerrainPreset::Flat, true)));
    assert_eq!(args(&["--seed", "4", "--adrenaline"]), Ok((4, TerrainPreset::Flat, true)));
    assert_eq!(args(&["--adrenaline", "--terrain", "hills"]), Ok((1, TerrainPreset::Hills, true)));
    assert_eq!(args(&["--terrain", "hills", "--adrenaline", "--seed", "9"]), Ok((9, TerrainPreset::Hills, true)));
    assert_eq!(args(&["--seed", "9", "--terrain", "flat", "--adrenaline"]), Ok((9, TerrainPreset::Flat, true)));
    assert_eq!(args(&["--adrenaline", "--adrenaline"]), Ok((1, TerrainPreset::Flat, true)), "repeats are harmless");
    assert_eq!(args(&["--adrenalin", "--seed", "2"]), Ok((2, TerrainPreset::Flat, false)), "near misses are unknown arguments");
    // The flag takes no value: the next option is still parsed.
    assert_eq!(args(&["--adrenaline", "hills"]), Ok((1, TerrainPreset::Flat, true)));
    // Errors keep their messages with the flag present.
    assert_eq!(args(&["--adrenaline", "--terrain", "x"]), Err("--terrain needs flat, hills or swamp".to_string()));
    assert_eq!(args(&["--adrenaline", "--seed"]), Err("--seed needs a u64".to_string()));
    assert_eq!(args(&["--seed", "--adrenaline"]), Err("--seed needs a u64".to_string()));
    let defaults = parse_args(Vec::<String>::new()).unwrap();
    assert_eq!((defaults.seed, defaults.terrain, defaults.adrenaline), (1, TerrainPreset::Flat, false));
}

#[test]
fn adrenaline_loadout_marks_player_units_only_and_changes_nothing_else() {
    for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
        for seed in SEEDS {
            let base = battle(seed, terrain);
            let loadout = adrenaline_battle(seed, terrain);
            for (b, l) in base.units.iter().zip(&loadout.units) {
                assert_eq!(l.has_adrenaline, l.side == Side::Player, "{}", l.name);
                assert!(!l.adrenaline_pending, "no pending effect at launch");
                let mut expected = b.clone();
                expected.has_adrenaline = l.has_adrenaline;
                assert_eq!(*l, expected, "only the capability flag differs ({})", l.name);
                assert_eq!((l.ap, l.fatigue, &l.stats), (b.ap, b.fatigue, &b.stats), "initial budgets unchanged");
            }
            assert_eq!(loadout.units.iter().filter(|u| u.has_adrenaline).count(), 3);
            assert_eq!(loadout.rng, base.rng, "no RNG consumed");
            assert_eq!(loadout.queue, base.queue);
            assert_eq!((loadout.round, loadout.outcome(), loadout.end_reason()), (base.round, base.outcome(), base.end_reason()));
            assert_eq!(loadout.elevations, base.elevations);
            assert_ne!(loadout.state_hash(), base.state_hash(), "the capability bit is hashed");
            // launch_battle selects between the two without other differences.
            assert_eq!(launch_battle(seed, terrain, true).state_hash(), loadout.state_hash());
            assert_eq!(launch_battle(seed, terrain, false).state_hash(), base.state_hash());
        }
    }
}

#[test]
fn default_scenarios_are_unchanged_without_the_flag() {
    for seed in SEEDS {
        let flat = launch_battle(seed, TerrainPreset::Flat, false);
        assert_eq!(flat.state_hash(), BattleState::new(seed).state_hash());
        assert_eq!(flat.units, BattleState::new(seed).units);
        assert_eq!(flat.rng, BattleState::new(seed).rng);
        assert!(flat.units.iter().all(|u| !u.has_adrenaline && !u.adrenaline_pending));
        let hills = launch_battle(seed, TerrainPreset::Hills, false);
        assert_eq!(hills.state_hash(), battle(seed, TerrainPreset::Hills).state_hash());
        assert!(hills.units.iter().all(|u| !u.has_adrenaline));
    }
    assert_eq!(battle(1, TerrainPreset::Flat).state_hash(), 0x8c00302113cbe668);
    assert_eq!(battle(1, TerrainPreset::Hills).state_hash(), 0xb759683cdff4bfa2);
}

#[test]
fn loadout_players_can_use_adrenaline_and_enemies_cannot() {
    let mut s = adrenaline_battle(1, TerrainPreset::Flat);
    let active = s.active_unit().unwrap().id;
    let side = s.units[active as usize].side;
    assert_eq!(side, Side::Player, "players lead the stock queue (initiative 100 vs 90)");
    let rng = s.rng.clone();
    s.apply(Command::Adrenaline { unit: active }).unwrap();
    assert!(s.units[active as usize].adrenaline_pending);
    assert_eq!(s.rng, rng);
    let mut e = adrenaline_battle(1, TerrainPreset::Flat);
    let enemy = e.units.iter().find(|u| u.side == Side::Enemy).unwrap().id;
    e.queue = vec![enemy];
    assert_eq!(e.apply(Command::Adrenaline { unit: enemy }), Err(RuleError::MissingAdrenaline));
}

fn smoke_line(extra: &[&str]) -> String {
    let out = smoke(extra);
    assert!(out.status.success(), "{extra:?}: {}", String::from_utf8_lossy(&out.stderr));
    String::from_utf8(out.stdout).unwrap().trim().to_string()
}

#[test]
fn headless_smoke_adrenaline_outputs_are_recorded_and_deterministic() {
    for (terrain, expected) in [
        ("flat", ADRENALINE_FLAT_SEED_1),
        ("hills", ADRENALINE_HILLS_SEED_1),
    ] {
        let opt_in = smoke_line(&["--seed", "1", "--terrain", terrain, "--adrenaline"]);
        assert_eq!(opt_in, expected, "{terrain}");
        assert_eq!(opt_in, smoke_line(&["--adrenaline", "--seed", "1", "--terrain", terrain]));
    }
    assert_eq!(smoke_line(&["--seed", "1"]), FLAT_SEED_1);
    assert_eq!(smoke_line(&["--seed", "1", "--terrain", "hills"]), HILLS_SEED_1);
}

#[test]
fn flat_and_hills_adrenaline_replays_match_commands_events_and_smoke_results() {
    for (terrain, expected) in [
        (TerrainPreset::Flat, ADRENALINE_FLAT_SEED_1),
        (TerrainPreset::Hills, ADRENALINE_HILLS_SEED_1),
    ] {
        let args = LaunchArgs {
            seed: 1,
            terrain,
            adrenaline: true,
            rotation: false,
            footwork: false,
            fast_adaptation: false,
            dodge: false,
            nine_lives: false, berserk: false, pathfinder: false, indomitable: false, two_hex: false, stunning_blow: false, killing_frenzy: false,
        };
        let (commands_a, events_a, state_a, n_a) = replay_loadout(args);
        let (commands_b, events_b, state_b, n_b) = replay_loadout(args);
        assert_eq!(commands_a, commands_b, "{terrain:?}: command stream");
        assert_eq!(events_a, events_b, "{terrain:?}: event stream");
        assert_eq!(
            (n_a, state_a.state_hash(), state_a.outcome(), state_a.end_reason(), state_a.round),
            (n_b, state_b.state_hash(), state_b.outcome(), state_b.end_reason(), state_b.round)
        );
        assert_eq!(state_a.rng, state_b.rng);
        assert!(n_a <= MAX_COMMANDS);
        assert!(
            events_a
                .iter()
                .any(|event| matches!(event, Event::AdrenalineUsed { .. })),
            "{terrain:?}: replay must exercise Adrenaline"
        );
        assert_eq!(
            format!(
                "OUTCOME={:?} REASON={:?} ROUNDS={} HASH={:016x}",
                state_a.outcome().unwrap(),
                state_a.end_reason().unwrap(),
                state_a.round,
                state_a.state_hash()
            ),
            expected
        );
        let terrain_arg = if terrain == TerrainPreset::Flat { "flat" } else { "hills" };
        assert_eq!(
            smoke_line(&["--seed", "1", "--terrain", terrain_arg, "--adrenaline"]),
            expected
        );
    }
}

// --- Opt-in Rotation loadout (`--rotation`) ------------------------------------------

fn args4(a: &[&str]) -> Result<(u64, TerrainPreset, bool, bool), String> {
    parse_args(a.iter().map(|s| s.to_string())).map(|p| (p.seed, p.terrain, p.adrenaline, p.rotation))
}

#[test]
fn rotation_flag_parses_and_composes_with_the_other_options() {
    use TerrainPreset::{Flat, Hills};
    assert_eq!(args4(&[]), Ok((1, Flat, false, false)), "absent by default");
    assert_eq!(args4(&["--rotation"]), Ok((1, Flat, false, true)));
    assert_eq!(args4(&["--seed", "4", "--rotation"]), Ok((4, Flat, false, true)));
    assert_eq!(args4(&["--rotation", "--terrain", "hills"]), Ok((1, Hills, false, true)));
    assert_eq!(args4(&["--adrenaline", "--rotation"]), Ok((1, Flat, true, true)));
    assert_eq!(args4(&["--rotation", "--adrenaline"]), Ok((1, Flat, true, true)), "order does not matter");
    assert_eq!(args4(&["--terrain", "hills", "--rotation", "--seed", "9", "--adrenaline"]), Ok((9, Hills, true, true)));
    assert_eq!(args4(&["--adrenaline"]), Ok((1, Flat, true, false)), "Adrenaline alone leaves Rotation off");
    assert_eq!(args4(&["--rotation", "--rotation"]), Ok((1, Flat, false, true)), "repeats are harmless");
    assert_eq!(args4(&["--rotations", "--seed", "2"]), Ok((2, Flat, false, false)), "near misses are unknown arguments");
    // The flag takes no value: the next option is still parsed.
    assert_eq!(args4(&["--rotation", "hills"]), Ok((1, Flat, false, true)));
    // Errors keep their messages with the flag present.
    assert_eq!(args4(&["--rotation", "--terrain", "x"]), Err("--terrain needs flat, hills or swamp".to_string()));
    assert_eq!(args4(&["--rotation", "--seed"]), Err("--seed needs a u64".to_string()));
    assert_eq!(args4(&["--seed", "--rotation"]), Err("--seed needs a u64".to_string()));
}

#[test]
fn every_terrain_and_loadout_combination_marks_player_units_only() {
    for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
        for seed in SEEDS {
            let base = battle(seed, terrain);
            for (adrenaline, rotation) in [(false, false), (true, false), (false, true), (true, true)] {
                let s = launch_battle_with(seed, terrain, adrenaline, rotation);
                for (b, l) in base.units.iter().zip(&s.units) {
                    let player = l.side == Side::Player;
                    assert_eq!((l.has_adrenaline, l.has_rotation), (player && adrenaline, player && rotation), "{}", l.name);
                    assert!(!l.adrenaline_pending);
                    let mut expected = b.clone();
                    expected.has_adrenaline = l.has_adrenaline;
                    expected.has_rotation = l.has_rotation;
                    assert_eq!(*l, expected, "only the capability flags differ ({})", l.name);
                    assert_eq!((l.ap, l.fatigue, &l.stats), (b.ap, b.fatigue, &b.stats), "initial budgets unchanged");
                }
                assert_eq!(s.rng, base.rng, "no RNG consumed");
                assert_eq!(s.queue, base.queue);
                assert_eq!((s.round, s.outcome(), s.end_reason()), (base.round, base.outcome(), base.end_reason()));
                assert_eq!(s.elevations, base.elevations);
                assert_eq!(s.state_hash() == base.state_hash(), !adrenaline && !rotation, "{terrain:?} {adrenaline} {rotation}");
            }
            // The named constructors agree with the combined one.
            assert_eq!(rotation_battle(seed, terrain).state_hash(), launch_battle_with(seed, terrain, false, true).state_hash());
            assert_eq!(adrenaline_battle(seed, terrain).state_hash(), launch_battle_with(seed, terrain, true, false).state_hash());
            assert_eq!(rotation_battle(seed, terrain).units.iter().filter(|u| u.has_rotation).count(), 3);
            assert!(rotation_battle(seed, terrain).units.iter().all(|u| !u.has_adrenaline));
            // The 3-argument constructor is unchanged.
            assert_eq!(launch_battle(seed, terrain, true).state_hash(), adrenaline_battle(seed, terrain).state_hash());
            assert_eq!(launch_battle(seed, terrain, false).state_hash(), base.state_hash());
            let both = launch_battle_with(seed, terrain, true, true);
            assert_ne!(both.state_hash(), rotation_battle(seed, terrain).state_hash());
            assert_ne!(both.state_hash(), adrenaline_battle(seed, terrain).state_hash());
        }
    }
}

#[test]
fn default_and_adrenaline_only_hashes_are_preserved() {
    for seed in SEEDS {
        assert_eq!(launch_battle_with(seed, TerrainPreset::Flat, false, false).state_hash(), BattleState::new(seed).state_hash());
        assert_eq!(launch_battle_with(seed, TerrainPreset::Flat, false, false).units, BattleState::new(seed).units);
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            assert_eq!(launch_battle_with(seed, terrain, true, false).state_hash(), adrenaline_battle(seed, terrain).state_hash());
        }
    }
    assert_eq!(launch_battle_with(1, TerrainPreset::Flat, false, false).state_hash(), 0x8c00302113cbe668);
    assert_eq!(launch_battle_with(1, TerrainPreset::Hills, false, false).state_hash(), 0xb759683cdff4bfa2);
}

#[test]
fn loadout_players_can_rotate_and_enemies_cannot() {
    let mut s = rotation_battle(1, TerrainPreset::Flat);
    let active = s.active_unit().unwrap().id;
    assert_eq!(s.units[active as usize].side, Side::Player);
    assert!(s.units[active as usize].has_rotation);
    let targets = s.legal_rotation_targets(active);
    assert!(!targets.is_empty(), "the stock start has an adjacent ally: {targets:?}");
    let rng = s.rng.clone();
    s.apply(Command::Rotation { unit: active, target: targets[0] }).unwrap();
    assert_eq!(s.rng, rng, "Rotation consumes no RNG");
    let mut e = rotation_battle(1, TerrainPreset::Flat);
    let enemy = e.units.iter().find(|u| u.side == Side::Enemy).unwrap().id;
    e.queue = vec![enemy];
    assert_eq!(e.apply(Command::Rotation { unit: enemy, target: 4 }), Err(RuleError::MissingRotation));
    assert!(e.legal_rotation_targets(enemy).is_empty());
    // Without the flag nobody can rotate.
    let plain = battle(1, TerrainPreset::Flat);
    assert!(plain.legal_rotation_targets(0).is_empty());
}

#[test]
fn headless_smoke_rotation_only_is_unchanged_and_adrenaline_composition_is_recorded() {
    for (terrain, plain_expected, rotation_expected, adrenaline_expected, both_expected) in [
        (
            "flat",
            FLAT_SEED_1,
            ROTATION_FLAT_SEED_1,
            ADRENALINE_FLAT_SEED_1,
            ADRENALINE_ROTATION_FLAT_SEED_1,
        ),
        (
            "hills",
            HILLS_SEED_1,
            ROTATION_HILLS_SEED_1,
            ADRENALINE_HILLS_SEED_1,
            ADRENALINE_ROTATION_HILLS_SEED_1,
        ),
    ] {
        let plain = smoke_line(&["--seed", "1", "--terrain", terrain]);
        let rotation = smoke_line(&["--seed", "1", "--terrain", terrain, "--rotation"]);
        let both = smoke_line(&["--seed", "1", "--terrain", terrain, "--rotation", "--adrenaline"]);
        let adrenaline = smoke_line(&["--seed", "1", "--terrain", terrain, "--adrenaline"]);
        assert_eq!(plain, plain_expected, "{terrain}: default remains unchanged");
        assert_eq!(rotation, rotation_expected, "{terrain}: Rotation-only remains unchanged");
        assert_eq!(adrenaline, adrenaline_expected, "{terrain}: Adrenaline");
        assert_eq!(both, both_expected, "{terrain}: Adrenaline + Rotation");
        assert_eq!(
            both,
            smoke_line(&["--adrenaline", "--rotation", "--terrain", terrain, "--seed", "1"])
        );
    }
}

// --- Opt-in Footwork loadout (`--footwork`) ------------------------------------------

fn parsed(a: &[&str]) -> Result<LaunchArgs, String> {
    parse_args(a.iter().map(|s| s.to_string()))
}

fn launch(seed: u64, terrain: TerrainPreset, adrenaline: bool, rotation: bool, footwork: bool) -> LaunchArgs {
        LaunchArgs { seed, terrain, adrenaline, rotation, footwork, fast_adaptation: false, dodge: false, nine_lives: false, berserk: false, pathfinder: false, indomitable: false, two_hex: false, stunning_blow: false, killing_frenzy: false }
}

#[test]
fn footwork_flag_parses_and_composes_with_the_other_options() {
    use TerrainPreset::{Flat, Hills};
    assert_eq!(parsed(&[]), Ok(launch(1, Flat, false, false, false)), "absent by default");
    assert_eq!(parsed(&["--footwork"]), Ok(launch(1, Flat, false, false, true)));
    assert_eq!(parsed(&["--seed", "4", "--footwork"]), Ok(launch(4, Flat, false, false, true)));
    assert_eq!(parsed(&["--footwork", "--terrain", "hills"]), Ok(launch(1, Hills, false, false, true)));
    assert_eq!(parsed(&["--footwork", "--rotation"]), Ok(launch(1, Flat, false, true, true)));
    assert_eq!(parsed(&["--adrenaline", "--footwork"]), Ok(launch(1, Flat, true, false, true)));
    assert_eq!(
        parsed(&["--footwork", "--terrain", "hills", "--adrenaline", "--seed", "9", "--rotation"]),
        Ok(launch(9, Hills, true, true, true)),
        "order does not matter"
    );
    assert_eq!(parsed(&["--rotation"]), Ok(launch(1, Flat, false, true, false)), "Footwork stays off without its flag");
    assert_eq!(parsed(&["--footwork", "--footwork"]), Ok(launch(1, Flat, false, false, true)), "repeats are harmless");
    assert_eq!(parsed(&["--footworks", "--seed", "2"]), Ok(launch(2, Flat, false, false, false)), "near misses are unknown");
    assert_eq!(parsed(&["--footwork", "hills"]), Ok(launch(1, Flat, false, false, true)), "the flag takes no value");
    assert_eq!(parsed(&["--footwork", "--terrain", "x"]), Err("--terrain needs flat, hills or swamp".to_string()));
    assert_eq!(parsed(&["--footwork", "--seed"]), Err("--seed needs a u64".to_string()));
    assert_eq!(parsed(&["--seed", "--footwork"]), Err("--seed needs a u64".to_string()));
}

#[test]
fn every_terrain_and_skill_flag_combination_marks_player_units_only() {
    for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
        for seed in SEEDS {
            let base = battle(seed, terrain);
            for bits in 0..8u8 {
                let (adrenaline, rotation, footwork) = (bits & 1 != 0, bits & 2 != 0, bits & 4 != 0);
                let s = launch_battle_for(launch(seed, terrain, adrenaline, rotation, footwork));
                for (b, l) in base.units.iter().zip(&s.units) {
                    let player = l.side == Side::Player;
                    assert_eq!(
                        (l.has_adrenaline, l.has_rotation, l.has_footwork),
                        (player && adrenaline, player && rotation, player && footwork),
                        "{} bits {bits}",
                        l.name
                    );
                    assert!(!l.adrenaline_pending);
                    let mut expected = b.clone();
                    (expected.has_adrenaline, expected.has_rotation, expected.has_footwork) =
                        (l.has_adrenaline, l.has_rotation, l.has_footwork);
                    assert_eq!(*l, expected, "only the capability flags differ ({})", l.name);
                    assert_eq!((l.ap, l.fatigue, &l.stats), (b.ap, b.fatigue, &b.stats), "initial budgets unchanged");
                }
                assert_eq!(s.rng, base.rng, "no RNG consumed");
                assert_eq!(s.queue, base.queue);
                assert_eq!((s.round, s.outcome(), s.end_reason()), (base.round, base.outcome(), base.end_reason()));
                assert_eq!(s.elevations, base.elevations);
                assert_eq!(s.state_hash() == base.state_hash(), bits == 0, "{terrain:?} bits {bits}");
            }
            // The named and legacy constructors agree with the shared one.
            assert_eq!(footwork_battle(seed, terrain).state_hash(), launch_battle_for(launch(seed, terrain, false, false, true)).state_hash());
            assert_eq!(footwork_battle(seed, terrain).units.iter().filter(|u| u.has_footwork).count(), 3);
            assert!(footwork_battle(seed, terrain).units.iter().all(|u| !u.has_adrenaline && !u.has_rotation));
            for (a, r) in [(false, false), (true, false), (false, true), (true, true)] {
                assert_eq!(
                    launch_battle_with(seed, terrain, a, r).state_hash(),
                    launch_battle_for(launch(seed, terrain, a, r, false)).state_hash(),
                    "existing loadouts are unchanged when Footwork is off"
                );
                assert_eq!(launch_battle_with(seed, terrain, a, r).units, launch_battle_for(launch(seed, terrain, a, r, false)).units);
            }
            assert_eq!(launch_battle(seed, terrain, true).state_hash(), adrenaline_battle(seed, terrain).state_hash());
            assert_eq!(rotation_battle(seed, terrain).state_hash(), launch_battle_for(launch(seed, terrain, false, true, false)).state_hash());
            let all = launch_battle_for(launch(seed, terrain, true, true, true));
            assert_ne!(all.state_hash(), footwork_battle(seed, terrain).state_hash());
        }
    }
}

#[test]
fn flat_without_flags_through_the_shared_constructor_is_exactly_the_default_scenario() {
    for seed in SEEDS {
        let s = launch_battle_for(launch(seed, TerrainPreset::Flat, false, false, false));
        assert_eq!(s.state_hash(), BattleState::new(seed).state_hash());
        assert_eq!(s.units, BattleState::new(seed).units);
    }
    assert_eq!(launch_battle_for(launch(1, TerrainPreset::Flat, false, false, false)).state_hash(), 0x8c00302113cbe668);
    assert_eq!(launch_battle_for(launch(1, TerrainPreset::Hills, false, false, false)).state_hash(), 0xb759683cdff4bfa2);
}

#[test]
fn loadout_players_can_footwork_and_enemies_cannot() {
    for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
        let mut s = footwork_battle(1, terrain);
        let active = s.active_unit().unwrap().id;
        assert_eq!(s.units[active as usize].side, Side::Player);
        let dests = s.legal_footwork_destinations(active);
        assert!(!dests.is_empty(), "{terrain:?}: the stock start has a free adjacent hex");
        let rng = s.rng.clone();
        s.apply(Command::Footwork { unit: active, to: dests[0] }).unwrap();
        assert_eq!(s.rng, rng, "Footwork consumes no RNG");
        let mut e = footwork_battle(1, terrain);
        let enemy = e.units.iter().find(|u| u.side == Side::Enemy).unwrap().id;
        e.queue = vec![enemy];
        let to = e.units[enemy as usize].pos.neighbors()[0];
        assert_eq!(e.apply(Command::Footwork { unit: enemy, to }), Err(RuleError::MissingFootwork));
        assert!(e.legal_footwork_destinations(enemy).is_empty());
        // Without the flag nobody can use Footwork.
        assert!(battle(1, terrain).legal_footwork_destinations(0).is_empty());
    }
}

#[test]
fn headless_smoke_footwork_only_is_unchanged_and_all_skills_use_new_adrenaline_policy() {
    for (
        terrain,
        plain_expected,
        rotation_expected,
        footwork_expected,
        rotation_footwork_expected,
        adrenaline_footwork_expected,
        all_expected,
    ) in [
        (
            "flat",
            FLAT_SEED_1,
            ROTATION_FLAT_SEED_1,
            FOOTWORK_FLAT_SEED_1,
            ROTATION_FOOTWORK_FLAT_SEED_1,
            ADRENALINE_FOOTWORK_FLAT_SEED_1,
            ALL_SKILLS_FLAT_SEED_1,
        ),
        (
            "hills",
            HILLS_SEED_1,
            ROTATION_HILLS_SEED_1,
            FOOTWORK_HILLS_SEED_1,
            ROTATION_FOOTWORK_HILLS_SEED_1,
            ADRENALINE_FOOTWORK_HILLS_SEED_1,
            ALL_SKILLS_HILLS_SEED_1,
        ),
    ] {
        let plain = smoke_line(&["--seed", "1", "--terrain", terrain]);
        let footwork = smoke_line(&["--seed", "1", "--terrain", terrain, "--footwork"]);
        let rotation = smoke_line(&["--seed", "1", "--terrain", terrain, "--rotation"]);
        let rotation_footwork = smoke_line(&["--seed", "1", "--terrain", terrain, "--rotation", "--footwork"]);
        let adrenaline_footwork = smoke_line(&["--seed", "1", "--terrain", terrain, "--adrenaline", "--footwork"]);
        let all = smoke_line(&["--seed", "1", "--terrain", terrain, "--adrenaline", "--rotation", "--footwork"]);
        assert_eq!(plain, plain_expected, "{terrain}: default remains unchanged");
        assert_eq!(rotation, rotation_expected, "{terrain}: Rotation-only remains unchanged");
        assert_eq!(footwork, footwork_expected, "{terrain}: Footwork-only remains unchanged");
        assert_eq!(
            rotation_footwork, rotation_footwork_expected,
            "{terrain}: no Adrenaline remains unchanged"
        );
        assert_eq!(adrenaline_footwork, adrenaline_footwork_expected, "{terrain}: Adrenaline + Footwork");
        assert_eq!(all, all_expected, "{terrain}: all skill flags");
        assert_eq!(
            all,
            smoke_line(&["--footwork", "--rotation", "--adrenaline", "--terrain", terrain, "--seed", "1"])
        );
    }
}

// --- Opt-in Fast Adaptation loadout (`--fast-adaptation`) ----------------------------
//
// Fast Adaptation is a passive: no AI policy selects it. Ordinary AI attacks by capable
// (player) units simply receive the accepted bonus. Smoke outputs recorded from this
// tree on seed 1 (APPROXIMATION: player-only assignment and the launch option).

const FAST_ADAPTATION_FLAT_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=03cd5060271badf3";
const FAST_ADAPTATION_HILLS_SEED_1: &str = "OUTCOME=Victory REASON=Elimination ROUNDS=5 HASH=9ad76a636259c440";
const ALL_FOUR_FLAT_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=f119e88eac2122c3";
const ALL_FOUR_HILLS_SEED_1: &str = "OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=7a501923872d8823";

fn flags(adrenaline: bool, rotation: bool, footwork: bool, fast_adaptation: bool) -> LaunchArgs {
    LaunchArgs { seed: 1, terrain: TerrainPreset::Flat, adrenaline, rotation, footwork, fast_adaptation, dodge: false, nine_lives: false, berserk: false, pathfinder: false, indomitable: false, two_hex: false, stunning_blow: false, killing_frenzy: false }
}

#[test]
fn fast_adaptation_flag_parses_and_composes_with_every_other_option() {
    use TerrainPreset::{Flat, Hills};
    assert_eq!(parsed(&[]).unwrap().fast_adaptation, false, "absent by default");
    assert_eq!(parsed(&["--fast-adaptation"]), Ok(flags(false, false, false, true)));
    assert_eq!(parsed(&["--fast-adaptation", "--fast-adaptation"]), Ok(flags(false, false, false, true)), "repeats are harmless");
    assert_eq!(
        parsed(&["--seed", "4", "--terrain", "hills", "--fast-adaptation"]),
        Ok(LaunchArgs { seed: 4, terrain: Hills, ..flags(false, false, false, true) })
    );
    assert_eq!(parsed(&["--fast-adaptation", "--terrain", "flat"]).unwrap().terrain, Flat);
    // Every subset of the four capability flags parses to exactly that subset, in either order.
    for bits in 0..16u8 {
        let names = ["--adrenaline", "--rotation", "--footwork", "--fast-adaptation"];
        let picked: Vec<&str> = names.iter().enumerate().filter(|(i, _)| bits >> i & 1 != 0).map(|(_, n)| *n).collect();
        let expected = flags(bits & 1 != 0, bits & 2 != 0, bits & 4 != 0, bits & 8 != 0);
        assert_eq!(parsed(&picked), Ok(expected), "bits {bits}");
        let mut reversed = picked.clone();
        reversed.reverse();
        assert_eq!(parsed(&reversed), Ok(expected), "bits {bits} reversed");
    }
    assert_eq!(parsed(&["--fast-adaptation", "hills"]).unwrap().terrain, Flat, "the flag takes no value");
    assert!(!parsed(&["--fast-adaptations"]).unwrap().fast_adaptation, "near misses are unknown");
    assert!(!parsed(&["--fast_adaptation"]).unwrap().fast_adaptation);
    assert_eq!(parsed(&["--fast-adaptation", "--terrain", "x"]), Err("--terrain needs flat, hills or swamp".to_string()));
    assert_eq!(parsed(&["--fast-adaptation", "--seed"]), Err("--seed needs a u64".to_string()));
}

#[test]
fn fast_adaptation_marks_player_units_only_with_zero_stacks_and_changes_nothing_else() {
    for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
        for seed in SEEDS {
            let base = battle(seed, terrain);
            for bits in 0..16u8 {
                let args = LaunchArgs { seed, terrain, ..flags(bits & 1 != 0, bits & 2 != 0, bits & 4 != 0, bits & 8 != 0) };
                let s = launch_battle_for(args);
                for (b, l) in base.units.iter().zip(&s.units) {
                    let player = l.side == Side::Player;
                    assert_eq!(l.has_fast_adaptation, player && args.fast_adaptation, "{} bits {bits}", l.name);
                    assert_eq!(l.fast_adaptation_stacks, 0, "{} starts with zero stacks", l.name);
                    let mut expected = b.clone();
                    (expected.has_adrenaline, expected.has_rotation, expected.has_footwork, expected.has_fast_adaptation) =
                        (l.has_adrenaline, l.has_rotation, l.has_footwork, l.has_fast_adaptation);
                    assert_eq!(*l, expected, "only the capability flags differ ({})", l.name);
                }
                assert_eq!(s.rng, base.rng, "no RNG consumed");
                assert_eq!(s.queue, base.queue);
                assert_eq!((s.round, s.outcome(), s.end_reason()), (base.round, base.outcome(), base.end_reason()));
                assert_eq!(s.elevations, base.elevations);
                assert_eq!(s.state_hash() == base.state_hash(), bits == 0, "{terrain:?} bits {bits}");
                // The other capability flags select the same units as before this option.
                let without = launch_battle_for(LaunchArgs { fast_adaptation: false, ..args });
                assert!(without.units.iter().all(|u| !u.has_fast_adaptation));
                assert_eq!(
                    s.units.iter().map(|u| (u.has_adrenaline, u.has_rotation, u.has_footwork)).collect::<Vec<_>>(),
                    without.units.iter().map(|u| (u.has_adrenaline, u.has_rotation, u.has_footwork)).collect::<Vec<_>>()
                );
            }
        }
    }
    assert_eq!(launch_battle_for(flags(false, false, false, false)).state_hash(), 0x8c00302113cbe668, "default unchanged");
}

fn smoke_fast_adaptation_line(terrain: &str, all: bool) -> String {
    if all {
        smoke_line(&["--seed", "1", "--terrain", terrain, "--adrenaline", "--rotation", "--footwork", "--fast-adaptation"])
    } else {
        smoke_line(&["--seed", "1", "--terrain", terrain, "--fast-adaptation"])
    }
}

#[test]
fn headless_smoke_fast_adaptation_outputs_are_recorded_and_other_runs_unchanged() {
    for (terrain, plain, alone, all_skills, all_four) in [
        ("flat", FLAT_SEED_1, FAST_ADAPTATION_FLAT_SEED_1, ALL_SKILLS_FLAT_SEED_1, ALL_FOUR_FLAT_SEED_1),
        ("hills", HILLS_SEED_1, FAST_ADAPTATION_HILLS_SEED_1, ALL_SKILLS_HILLS_SEED_1, ALL_FOUR_HILLS_SEED_1),
    ] {
        assert_eq!(smoke_line(&["--seed", "1", "--terrain", terrain]), plain, "{terrain}: default unchanged");
        assert_eq!(
            smoke_line(&["--seed", "1", "--terrain", terrain, "--adrenaline", "--rotation", "--footwork"]),
            all_skills,
            "{terrain}: three-skill run unchanged"
        );
        assert_eq!(smoke_fast_adaptation_line(terrain, false), alone, "{terrain}: Fast Adaptation alone");
        assert_eq!(smoke_fast_adaptation_line(terrain, true), all_four, "{terrain}: all four options");
        assert_ne!(alone, plain);
        assert_ne!(all_four, all_skills);
        assert_eq!(alone, smoke_line(&["--fast-adaptation", "--terrain", terrain, "--seed", "1"]), "option order is irrelevant");
    }
}

/// Replay like `replay_loadout`, additionally checking after every command that stacks follow
/// the accepted lifecycle; returns the number of actual player misses that raised a counter.
fn replay_tracking_stacks(args: LaunchArgs) -> (Vec<Command>, Vec<Event>, BattleState, u32, u32) {
    let mut state = launch_battle_for(args);
    let (mut commands, mut events) = (Vec::new(), Vec::new());
    let (mut n, mut raised) = (0, 0);
    while state.outcome().is_none() {
        let command = *ai::plan_turn(&state).first().expect("plan_turn returns a command during a running battle");
        n += 1;
        assert!(n <= MAX_COMMANDS, "{args:?} exceeded the smoke bound");
        let before: Vec<u32> = state.units.iter().map(|u| u.fast_adaptation_stacks).collect();
        commands.push(command);
        let new_events = state.apply(command).unwrap_or_else(|e| panic!("{args:?}: {e:?} from {command:?}"));
        // The last attack a capable unit made in this command decides its counter: a hit resets
        // it, a miss leaves at least one stack. Ordinary, reaction and opportunity attacks agree.
        for u in state.units.iter().filter(|u| u.has_fast_adaptation) {
            let last = new_events.iter().rev().find_map(|e| match *e {
                Event::Attacked { attacker, hit, .. } if attacker == u.id => Some(hit),
                _ => None,
            });
            match last {
                Some(true) => assert_eq!(u.fast_adaptation_stacks, 0, "{} hit resets", u.name),
                Some(false) => {
                    assert!(u.fast_adaptation_stacks >= 1, "{} miss leaves a stack", u.name);
                    raised += 1;
                }
                None => assert_eq!(u.fast_adaptation_stacks, before[u.id as usize], "{} unchanged without an attack", u.name),
            }
        }
        assert!(
            state.units.iter().all(|u| u.has_fast_adaptation || u.fast_adaptation_stacks == 0),
            "incapable units never accumulate stacks"
        );
        events.extend(new_events);
    }
    (commands, events, state, n, raised)
}

#[test]
fn fast_adaptation_replays_are_deterministic_valid_bounded_and_driven_by_real_misses() {
    let mut total_raised = 0;
    for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
        for (all, expected) in [
            (false, if terrain == TerrainPreset::Flat { FAST_ADAPTATION_FLAT_SEED_1 } else { FAST_ADAPTATION_HILLS_SEED_1 }),
            (true, if terrain == TerrainPreset::Flat { ALL_FOUR_FLAT_SEED_1 } else { ALL_FOUR_HILLS_SEED_1 }),
        ] {
            let args = LaunchArgs { terrain, ..flags(all, all, all, true) };
            let (ca, ea, sa, na, raised) = replay_tracking_stacks(args);
            let (cb, eb, sb, nb, _) = replay_tracking_stacks(args);
            assert_eq!(ca, cb, "{terrain:?}/{all}: command stream");
            assert_eq!(ea, eb, "{terrain:?}/{all}: event stream");
            assert_eq!((na, sa.state_hash(), sa.outcome(), sa.end_reason(), sa.round), (nb, sb.state_hash(), sb.outcome(), sb.end_reason(), sb.round));
            assert_eq!(sa.rng, sb.rng);
            assert!(na <= MAX_COMMANDS && matches!(ea.last(), Some(Event::BattleEnded { .. })));
            // The plain replay helper agrees, so the tracked run applied the same commands.
            let (plain_commands, plain_events, plain_state, _) = replay_loadout(args);
            assert_eq!((&plain_commands, &plain_events, plain_state.state_hash()), (&ca, &ea, sa.state_hash()));
            assert_eq!(
                format!("OUTCOME={:?} REASON={:?} ROUNDS={} HASH={:016x}", sa.outcome().unwrap(), sa.end_reason().unwrap(), sa.round, sa.state_hash()),
                expected
            );
            total_raised += raised;
        }
    }
    assert!(total_raised > 0, "at least one actual miss raised a Fast Adaptation counter across the replays");
}

// --- Opt-in Dodge passive (`--dodge`) -----------------------------------------------
//
// Dodge is a passive: no AI policy selects it. Attacks against capable (player) defenders
// automatically use the accepted Dodge defense. Smoke outputs recorded from this tree on
// seed 1 (APPROXIMATION: player-only assignment and the launch option).

const DODGE_FLAT_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=e55edadd8f630253";
const DODGE_HILLS_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=dce3433c8ab331fe";
const ALL_FIVE_FLAT_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=57f7c67846b45003";
const ALL_FIVE_HILLS_SEED_1: &str = "OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=ac49aa482cb73309";

const BERSERK_FLAT_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=b90d0af968bae726";
const BERSERK_HILLS_SEED_1: &str = "OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=ae18c48440cd1618";
const ALL_SEVEN_FLAT_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=17373b790c6aaffb";
const ALL_SEVEN_HILLS_SEED_1: &str = "OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=f471788f11714fdd";
const PATHFINDER_FLAT_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=9cca6ef915788869";
const PATHFINDER_HILLS_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=6c00a08d1c11386d";
const ALL_EIGHT_FLAT_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=96886e87d9af4633";
const ALL_EIGHT_HILLS_SEED_1: &str = "OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=409a61a4ec43cbb5";

const CAPABILITY_OPTIONS: [&str; 9] = ["--adrenaline", "--rotation", "--footwork", "--fast-adaptation", "--dodge", "--nine-lives", "--berserk", "--pathfinder", "--indomitable"];

/// The launch configuration for a 9-bit subset of the capability options (bit 0 adrenaline
/// ... bit 5 Nine Lives, bit 6 Berserk, bit 7 Pathfinder, bit 8 Indomitable).
fn subset(seed: u64, terrain: TerrainPreset, bits: u16) -> LaunchArgs {
    LaunchArgs {
        seed,
        terrain,
        adrenaline: bits & 1 != 0,
        rotation: bits & 2 != 0,
        footwork: bits & 4 != 0,
        fast_adaptation: bits & 8 != 0,
        dodge: bits & 16 != 0,
        nine_lives: bits & 32 != 0,
        berserk: bits & 64 != 0,
        pathfinder: bits & 128 != 0,
        indomitable: bits & 256 != 0,
        two_hex: false,
        stunning_blow: false, killing_frenzy: false,
    }
}

#[test]
fn dodge_flag_parses_defaults_to_false_repeats_harmlessly_and_composes_independently() {
    use TerrainPreset::{Flat, Hills};
    assert!(!parsed(&[]).unwrap().dodge, "absent by default");
    assert_eq!(parsed(&["--dodge"]), Ok(subset(1, Flat, 16)));
    assert_eq!(parsed(&["--dodge", "--dodge", "--dodge"]), Ok(subset(1, Flat, 16)), "repeats are harmless");
    assert_eq!(parsed(&["--seed", "4", "--terrain", "hills", "--dodge"]), Ok(subset(4, Hills, 16)));
    assert_eq!(parsed(&["--dodge", "--terrain", "flat"]).unwrap().terrain, Flat);
    // Every one of the 32 subsets parses to exactly that subset, in either order and with repeats.
    for bits in 0..32u16 {
        let picked: Vec<&str> = CAPABILITY_OPTIONS.iter().enumerate().filter(|(i, _)| bits >> i & 1 != 0).map(|(_, n)| *n).collect();
        let expected = subset(1, Flat, bits);
        assert_eq!(parsed(&picked), Ok(expected), "bits {bits}");
        let mut reversed = picked.clone();
        reversed.reverse();
        assert_eq!(parsed(&reversed), Ok(expected), "bits {bits} reversed");
        let doubled: Vec<&str> = picked.iter().chain(picked.iter()).copied().collect();
        assert_eq!(parsed(&doubled), Ok(expected), "bits {bits} repeated");
        let mut with_terrain = vec!["--seed", "9", "--terrain", "hills"];
        with_terrain.extend(&picked);
        assert_eq!(parsed(&with_terrain), Ok(subset(9, Hills, bits)), "bits {bits} with seed and terrain");
    }
    assert_eq!(parsed(&["--dodge", "hills"]).unwrap().terrain, Flat, "the flag takes no value");
    assert!(!parsed(&["--dodges"]).unwrap().dodge, "near misses are unknown");
    assert!(!parsed(&["--Dodge"]).unwrap().dodge);
    assert_eq!(parsed(&["--dodge", "--terrain", "x"]), Err("--terrain needs flat, hills or swamp".to_string()));
    assert_eq!(parsed(&["--dodge", "--seed"]), Err("--seed needs a u64".to_string()));
}

#[test]
fn dodge_marks_player_units_only_and_changes_nothing_else_across_all_32_subsets() {
    for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
        for seed in SEEDS {
            let base = battle(seed, terrain);
            assert!(base.units.iter().all(|u| !u.has_dodge && u.dodge_defense_bonus() == 0), "scenario units start incapable");
            for bits in 0..32u16 {
                let args = subset(seed, terrain, bits);
                let s = launch_battle_for(args);
                for (b, l) in base.units.iter().zip(&s.units) {
                    let player = l.side == Side::Player;
                    assert_eq!(l.has_dodge, player && args.dodge, "{} bits {bits}", l.name);
                    if !l.has_dodge {
                        assert_eq!(l.dodge_defense_bonus(), 0);
                        assert_eq!(l.effective_defense(), b.effective_defense());
                    }
                    let mut expected = b.clone();
                    (expected.has_adrenaline, expected.has_rotation, expected.has_footwork, expected.has_fast_adaptation, expected.has_dodge) =
                        (l.has_adrenaline, l.has_rotation, l.has_footwork, l.has_fast_adaptation, l.has_dodge);
                    assert_eq!(*l, expected, "only the capability flags differ ({})", l.name);
                    assert_eq!(l.fast_adaptation_stacks, 0);
                }
                assert_eq!(s.rng, base.rng, "no RNG consumed");
                assert_eq!(s.queue, base.queue);
                assert_eq!((s.round, s.outcome(), s.end_reason()), (base.round, base.outcome(), base.end_reason()));
                assert_eq!(s.elevations, base.elevations);
                assert_eq!(s.state_hash() == base.state_hash(), bits == 0, "{terrain:?} bits {bits}");
                // Selecting Dodge leaves the other four flags where they were.
                let without = launch_battle_for(LaunchArgs { dodge: false, ..args });
                assert!(without.units.iter().all(|u| !u.has_dodge));
                assert_eq!(
                    s.units.iter().map(|u| (u.has_adrenaline, u.has_rotation, u.has_footwork, u.has_fast_adaptation)).collect::<Vec<_>>(),
                    without.units.iter().map(|u| (u.has_adrenaline, u.has_rotation, u.has_footwork, u.has_fast_adaptation)).collect::<Vec<_>>()
                );
            }
        }
    }
    assert_eq!(launch_battle_for(subset(1, TerrainPreset::Flat, 0)).state_hash(), 0x8c00302113cbe668, "default unchanged");
    assert_eq!(launch_battle_for(subset(1, TerrainPreset::Hills, 0)).state_hash(), 0xb759683cdff4bfa2, "hills default unchanged");
    // The compatibility constructors still build exactly their old configurations.
    assert_eq!(launch_battle_with(3, TerrainPreset::Flat, true, true).state_hash(), launch_battle_for(subset(3, TerrainPreset::Flat, 3)).state_hash());
}

fn smoke_dodge_line(terrain: &str, all: bool) -> String {
    if all {
        smoke_line(&["--seed", "1", "--terrain", terrain, "--adrenaline", "--rotation", "--footwork", "--fast-adaptation", "--dodge"])
    } else {
        smoke_line(&["--seed", "1", "--terrain", terrain, "--dodge"])
    }
}

#[test]
fn headless_smoke_dodge_outputs_are_recorded_and_other_runs_unchanged() {
    for (terrain, plain, alone, all_four, all_five) in [
        ("flat", FLAT_SEED_1, DODGE_FLAT_SEED_1, ALL_FOUR_FLAT_SEED_1, ALL_FIVE_FLAT_SEED_1),
        ("hills", HILLS_SEED_1, DODGE_HILLS_SEED_1, ALL_FOUR_HILLS_SEED_1, ALL_FIVE_HILLS_SEED_1),
    ] {
        assert_eq!(smoke_line(&["--seed", "1", "--terrain", terrain]), plain, "{terrain}: default unchanged");
        assert_eq!(
            smoke_line(&["--seed", "1", "--terrain", terrain, "--adrenaline", "--rotation", "--footwork", "--fast-adaptation"]),
            all_four,
            "{terrain}: four-option run unchanged"
        );
        assert_eq!(smoke_dodge_line(terrain, false), alone, "{terrain}: Dodge alone");
        assert_eq!(smoke_dodge_line(terrain, true), all_five, "{terrain}: all five options");
        assert_ne!(alone, plain);
        assert_ne!(all_five, all_four);
        assert_eq!(alone, smoke_line(&["--dodge", "--terrain", terrain, "--seed", "1", "--dodge"]), "order and repeats are irrelevant");
    }
}

/// Replay like `replay_loadout`; additionally checks that every plain Attack's recorded chance
/// is the pre-command `hit_chance` and returns how many attacks hit a defender whose Dodge
/// contribution was nonzero at that moment.
fn replay_counting_dodged_attacks(args: LaunchArgs) -> (Vec<Command>, Vec<Event>, BattleState, u32, u32) {
    let mut state = launch_battle_for(args);
    let (mut commands, mut events) = (Vec::new(), Vec::new());
    let (mut n, mut dodged) = (0, 0);
    while state.outcome().is_none() {
        let command = *ai::plan_turn(&state).first().expect("plan_turn returns a command during a running battle");
        n += 1;
        assert!(n <= MAX_COMMANDS, "{args:?} exceeded the smoke bound");
        let expected = match command {
            Command::Attack { attacker, target } => Some((state.hit_chance(attacker, target), state.units[target as usize].dodge_defense_bonus())),
            _ => None,
        };
        commands.push(command);
        let new_events = state.apply(command).unwrap_or_else(|e| panic!("{args:?}: {e:?} from {command:?}"));
        if let Some((chance, bonus)) = expected {
            assert!(
                matches!(new_events.first(), Some(Event::Attacked { chance: c, .. }) if *c == chance),
                "the resolved attack uses the previewed chance"
            );
            dodged += u32::from(bonus > 0);
        }
        assert!(state.units.iter().all(|u| args.dodge && u.side == Side::Player || u.dodge_defense_bonus() == 0));
        events.extend(new_events);
    }
    (commands, events, state, n, dodged)
}

#[test]
fn dodge_replays_are_deterministic_valid_bounded_and_include_real_dodged_attacks() {
    let mut total_dodged = 0;
    for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
        for (bits, expected) in [
            (16u16, if terrain == TerrainPreset::Flat { DODGE_FLAT_SEED_1 } else { DODGE_HILLS_SEED_1 }),
            (31, if terrain == TerrainPreset::Flat { ALL_FIVE_FLAT_SEED_1 } else { ALL_FIVE_HILLS_SEED_1 }),
        ] {
            let args = subset(1, terrain, bits);
            let (ca, ea, sa, na, dodged) = replay_counting_dodged_attacks(args);
            let (cb, eb, sb, nb, _) = replay_counting_dodged_attacks(args);
            assert_eq!(ca, cb, "{terrain:?}/{bits}: command stream");
            assert_eq!(ea, eb, "{terrain:?}/{bits}: event stream");
            assert_eq!((na, sa.state_hash(), sa.outcome(), sa.end_reason(), sa.round), (nb, sb.state_hash(), sb.outcome(), sb.end_reason(), sb.round));
            assert_eq!(sa.rng, sb.rng);
            assert!(na <= MAX_COMMANDS && matches!(ea.last(), Some(Event::BattleEnded { .. })));
            let (plain_commands, plain_events, plain_state, _) = replay_loadout(args);
            assert_eq!((&plain_commands, &plain_events, plain_state.state_hash()), (&ca, &ea, sa.state_hash()));
            assert_eq!(
                format!("OUTCOME={:?} REASON={:?} ROUNDS={} HASH={:016x}", sa.outcome().unwrap(), sa.end_reason().unwrap(), sa.round, sa.state_hash()),
                expected
            );
            total_dodged += dodged;
        }
    }
    assert!(total_dodged > 0, "at least one attack hit a defender with a nonzero Dodge contribution across the replays");
}

// --- Nine Lives launch option (APPROXIMATION: a player-only launch option, not a perk system) ---

const NINE_LIVES_FLAT_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=e7d351c8b0dc9913";
const NINE_LIVES_HILLS_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=64286fc0a709803e";
const ALL_SIX_FLAT_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=cb45f871a3bb0b83";
const ALL_SIX_HILLS_SEED_1: &str = "OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=6812901431975589";

#[test]
fn nine_lives_flag_parses_defaults_to_false_repeats_harmlessly_and_composes_independently() {
    use TerrainPreset::{Flat, Hills};
    assert!(!parsed(&[]).unwrap().nine_lives, "absent by default");
    assert_eq!(parsed(&["--nine-lives"]), Ok(subset(1, Flat, 32)));
    assert_eq!(parsed(&["--nine-lives", "--nine-lives", "--nine-lives"]), Ok(subset(1, Flat, 32)), "repeats are harmless");
    assert_eq!(parsed(&["--seed", "4", "--terrain", "hills", "--nine-lives"]), Ok(subset(4, Hills, 32)));
    assert_eq!(parsed(&["--nine-lives", "--terrain", "flat"]).unwrap().terrain, Flat);
    for bits in 0..64u16 {
        let picked: Vec<&str> = CAPABILITY_OPTIONS.iter().enumerate().filter(|(i, _)| bits >> i & 1 != 0).map(|(_, n)| *n).collect();
        let expected = subset(1, Flat, bits);
        assert_eq!(parsed(&picked), Ok(expected), "bits {bits}");
        let mut reversed = picked.clone();
        reversed.reverse();
        assert_eq!(parsed(&reversed), Ok(expected), "bits {bits} reversed");
        let doubled: Vec<&str> = picked.iter().chain(picked.iter()).copied().collect();
        assert_eq!(parsed(&doubled), Ok(expected), "bits {bits} repeated");
        let mut with_terrain = vec!["--seed", "9", "--terrain", "hills"];
        with_terrain.extend(&picked);
        assert_eq!(parsed(&with_terrain), Ok(subset(9, Hills, bits)), "bits {bits} with seed and terrain");
    }
    assert_eq!(parsed(&["--nine-lives", "hills"]).unwrap().terrain, Flat, "the flag takes no value");
    for near_miss in ["--nine-live", "--nine_lives", "--ninelives", "--Nine-Lives", "--nine-lives=1"] {
        assert!(!parsed(&[near_miss]).unwrap().nine_lives, "{near_miss}");
    }
    assert_eq!(parsed(&["--nine-lives", "--terrain", "x"]), Err("--terrain needs flat, hills or swamp".to_string()));
    assert_eq!(parsed(&["--nine-lives", "--seed"]), Err("--seed needs a u64".to_string()));
}

#[test]
fn nine_lives_marks_player_units_only_unused_and_changes_nothing_else_across_all_64_subsets() {
    for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
        for seed in SEEDS {
            let base = battle(seed, terrain);
            assert!(base.units.iter().all(|u| !u.has_nine_lives && !u.nine_lives_used), "scenario units start incapable and unused");
            for bits in 0..64u16 {
                let args = subset(seed, terrain, bits);
                let s = launch_battle_for(args);
                for (b, l) in base.units.iter().zip(&s.units) {
                    let player = l.side == Side::Player;
                    assert_eq!(l.has_nine_lives, player && args.nine_lives, "{} bits {bits}", l.name);
                    assert!(!l.nine_lives_used, "protection starts unused: {} bits {bits}", l.name);
                    let mut expected = b.clone();
                    (expected.has_adrenaline, expected.has_rotation, expected.has_footwork, expected.has_fast_adaptation, expected.has_dodge) =
                        (l.has_adrenaline, l.has_rotation, l.has_footwork, l.has_fast_adaptation, l.has_dodge);
                    expected.has_nine_lives = l.has_nine_lives;
                    assert_eq!(*l, expected, "only the capability flags differ ({})", l.name);
                }
                assert_eq!(s.rng, base.rng, "no RNG consumed");
                assert_eq!(s.queue, base.queue);
                assert_eq!((s.round, s.outcome(), s.end_reason()), (base.round, base.outcome(), base.end_reason()));
                assert_eq!(s.elevations, base.elevations);
                assert_eq!(s.state_hash() == base.state_hash(), bits == 0, "{terrain:?} bits {bits}");
                // Selecting Nine Lives leaves the other five flags where they were.
                let without = launch_battle_for(LaunchArgs { nine_lives: false, ..args });
                assert!(without.units.iter().all(|u| !u.has_nine_lives));
                assert_eq!(
                    s.units.iter().map(|u| (u.has_adrenaline, u.has_rotation, u.has_footwork, u.has_fast_adaptation, u.has_dodge)).collect::<Vec<_>>(),
                    without.units.iter().map(|u| (u.has_adrenaline, u.has_rotation, u.has_footwork, u.has_fast_adaptation, u.has_dodge)).collect::<Vec<_>>()
                );
            }
        }
    }
    assert_eq!(launch_battle_for(subset(1, TerrainPreset::Flat, 0)).state_hash(), 0x8c00302113cbe668, "default unchanged");
    assert_eq!(launch_battle_for(subset(1, TerrainPreset::Hills, 0)).state_hash(), 0xb759683cdff4bfa2, "hills default unchanged");
    // The compatibility constructors still build exactly their old configurations (no Nine Lives).
    assert!(launch_battle_with(3, TerrainPreset::Flat, true, true).units.iter().all(|u| !u.has_nine_lives));
    assert!(adrenaline_battle(3, TerrainPreset::Hills).units.iter().all(|u| !u.has_nine_lives));
    assert!(rotation_battle(3, TerrainPreset::Hills).units.iter().all(|u| !u.has_nine_lives));
    assert!(footwork_battle(3, TerrainPreset::Hills).units.iter().all(|u| !u.has_nine_lives));
}

fn smoke_nine_lives_line(terrain: &str, all: bool) -> String {
    if all {
        smoke_line(&["--seed", "1", "--terrain", terrain, "--adrenaline", "--rotation", "--footwork", "--fast-adaptation", "--dodge", "--nine-lives"])
    } else {
        smoke_line(&["--seed", "1", "--terrain", terrain, "--nine-lives"])
    }
}

#[test]
fn headless_smoke_nine_lives_outputs_are_recorded_and_earlier_runs_unchanged() {
    for (terrain, plain, dodge, alone, all_five, all_six) in [
        ("flat", FLAT_SEED_1, DODGE_FLAT_SEED_1, NINE_LIVES_FLAT_SEED_1, ALL_FIVE_FLAT_SEED_1, ALL_SIX_FLAT_SEED_1),
        ("hills", HILLS_SEED_1, DODGE_HILLS_SEED_1, NINE_LIVES_HILLS_SEED_1, ALL_FIVE_HILLS_SEED_1, ALL_SIX_HILLS_SEED_1),
    ] {
        assert_eq!(smoke_line(&["--seed", "1", "--terrain", terrain]), plain, "{terrain}: default unchanged");
        assert_eq!(smoke_dodge_line(terrain, false), dodge, "{terrain}: Dodge alone unchanged");
        assert_eq!(smoke_dodge_line(terrain, true), all_five, "{terrain}: five-option run unchanged");
        assert_eq!(smoke_nine_lives_line(terrain, false), alone, "{terrain}: Nine Lives alone");
        assert_eq!(smoke_nine_lives_line(terrain, true), all_six, "{terrain}: all six options");
        assert_ne!(alone, plain, "the flag bit is part of the state hash");
        assert_ne!(all_six, all_five);
        assert_eq!(alone, smoke_line(&["--nine-lives", "--terrain", terrain, "--seed", "1", "--nine-lives"]), "order and repeats are irrelevant");
    }
}

#[test]
fn nine_lives_replays_are_deterministic_valid_bounded_and_match_the_smoke_results() {
    for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
        let flat = terrain == TerrainPreset::Flat;
        for (bits, expected) in [
            (32u16, if flat { NINE_LIVES_FLAT_SEED_1 } else { NINE_LIVES_HILLS_SEED_1 }),
            (63, if flat { ALL_SIX_FLAT_SEED_1 } else { ALL_SIX_HILLS_SEED_1 }),
        ] {
            let args = subset(1, terrain, bits);
            let (ca, ea, sa, na) = replay_loadout(args);
            let (cb, eb, sb, nb) = replay_loadout(args);
            assert_eq!(ca, cb, "{terrain:?}/{bits}: command stream");
            assert_eq!(ea, eb, "{terrain:?}/{bits}: event stream");
            assert_eq!((na, sa.state_hash(), sa.outcome(), sa.end_reason(), sa.round), (nb, sb.state_hash(), sb.outcome(), sb.end_reason(), sb.round));
            assert_eq!(sa.rng, sb.rng);
            assert!(na <= MAX_COMMANDS && matches!(ea.last(), Some(Event::BattleEnded { .. })));
            assert_eq!(
                format!("OUTCOME={:?} REASON={:?} ROUNDS={} HASH={:016x}", sa.outcome().unwrap(), sa.end_reason().unwrap(), sa.round, sa.state_hash()),
                expected
            );
            // Stock smoke need not trigger protection: it only has to stay valid; whatever it does
            // trigger is consistent with the final flags.
            let triggered: Vec<u32> = ea.iter().filter_map(|e| if let Event::NineLivesTriggered { unit } = e { Some(*unit) } else { None }).collect();
            for u in &sa.units {
                assert_eq!(u.nine_lives_used, triggered.contains(&u.id), "{terrain:?}/{bits}: {}", u.name);
            }
        }
        // Without the option the same configuration never reports protection.
        let (_, events, _, _) = replay_loadout(subset(1, terrain, 31));
        assert!(!events.iter().any(|e| matches!(e, Event::NineLivesTriggered { .. })));
    }
}

/// A legal encounter built from the opt-in launch configuration: everyone parked, E1 beside the
/// launch-granted P1 on the obstacle-free lane `r = -2`, E1 active and strong enough to kill P1
/// with one hit; P1 has 10 HP, no armour and no defense.
fn nine_lives_encounter(terrain: TerrainPreset) -> BattleState {
    let mut s = launch_battle_for(LaunchArgs { nine_lives: true, ..subset(1, terrain, 0) });
    let park = [hex(-4, 2), hex(-4, 3), hex(-4, 4), hex(4, -4), hex(4, -3), hex(4, -2)];
    for (u, at) in s.units.iter_mut().zip(park) {
        u.pos = at;
        u.turn_started = false;
    }
    s.units[0].pos = hex(0, -2);
    s.units[3].pos = hex(1, -2);
    let (p1, e1) = (0, 3);
    s.units[p1].hp = 10;
    s.units[p1].armour = 0;
    s.units[p1].stats.defense = 0;
    s.units[e1].stats.melee_skill = 100;
    s.units[e1].stats.dmg_min = 30;
    s.units[e1].stats.dmg_max = 30;
    s.units[e1].ap = 9;
    s.units[e1].fatigue = 0;
    s.units[e1].turn_started = true;
    s.queue.retain(|&q| q != e1 as u32);
    s.queue.insert(0, e1 as u32);
    assert!(s.units[p1].has_nine_lives && !s.units[p1].nine_lives_used);
    assert!(s.units.iter().skip(3).all(|u| !u.has_nine_lives), "enemies are never granted it");
    s
}

#[test]
fn scripted_encounter_from_the_launch_option_survives_once_then_dies_normally() {
    for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
        let attack = Command::Attack { attacker: 3, target: 0 };
        // Pick an RNG state under which both attacks hit (the encounter itself is fixed).
        let (mut s, first, second) = (1u64..2_000)
            .find_map(|k| {
                let mut s = nine_lives_encounter(terrain);
                s.rng = Rng::new(k);
                let first = s.apply(attack).ok()?;
                if !matches!(first.first(), Some(Event::Attacked { hit: true, .. })) {
                    return None;
                }
                let second = s.apply(attack).ok()?;
                matches!(second.first(), Some(Event::Attacked { hit: true, .. })).then(|| {
                    let mut again = nine_lives_encounter(terrain);
                    again.rng = Rng::new(k);
                    (again, first, second)
                })
            })
            .expect("two hits occur under some RNG state");
        let first_live = s.apply(attack).unwrap();
        assert_eq!(first_live, first, "deterministic");
        assert!(matches!(first_live[0], Event::Attacked { attacker: 3, target: 0, hit: true, hp_loss: 9, .. }), "actual loss 10 - 1 HP");
        assert_eq!(first_live[1], Event::NineLivesTriggered { unit: 0 });
        assert!(!first_live.iter().any(|e| matches!(e, Event::Died { .. })));
        assert!(s.units[0].alive && s.units[0].hp == bb_rules::rules::NINE_LIVES_SURVIVAL_HP && s.units[0].nine_lives_used);
        assert!(s.units[0].has_nine_lives, "the capability stays; only the use is consumed");
        let second_live = s.apply(attack).unwrap();
        assert_eq!(second_live, second);
        assert!(matches!(second_live[0], Event::Attacked { hit: true, .. }));
        assert_eq!(second_live[1], Event::Died { unit: 0 });
        assert!(!second_live.iter().any(|e| matches!(e, Event::NineLivesTriggered { .. })));
        assert!(!s.units[0].alive);
    }
}

#[test]
fn pathfinder_option_defaults_repeats_and_composes_with_all_launch_options() {
    use TerrainPreset::{Flat, Hills};

    assert!(!parsed(&[]).unwrap().pathfinder, "Pathfinder is off by default");
    assert!(!parsed(&[]).unwrap().berserk, "Berserk remains off by default");
    assert_eq!(parsed(&["--pathfinder"]), Ok(subset(1, Flat, 128)));
    assert_eq!(parsed(&["--pathfinder", "--pathfinder", "--pathfinder"]), Ok(subset(1, Flat, 128)), "repeats are harmless");
    assert_eq!(parsed(&["--seed", "4", "--terrain", "hills", "--pathfinder"]), Ok(subset(4, Hills, 128)));
    assert_eq!(parsed(&["--pathfinder", "--terrain", "flat"]).unwrap().terrain, Flat);
    assert_eq!(parsed(&["--berserk"]), Ok(subset(1, Flat, 64)));
    assert_eq!(parsed(&["--berserk", "--berserk", "--berserk"]), Ok(subset(1, Flat, 64)), "existing repeats remain harmless");
    assert_eq!(parsed(&["--seed", "4", "--terrain", "hills", "--berserk"]), Ok(subset(4, Hills, 64)));
    assert_eq!(parsed(&["--berserk", "--terrain", "flat"]).unwrap().terrain, Flat);

    // Every one of the 256 subsets parses in either order and with repeated flags.
    for bits in 0..=255u16 {
        let picked: Vec<&str> = CAPABILITY_OPTIONS.iter().enumerate().filter(|(i, _)| bits >> i & 1 != 0).map(|(_, name)| *name).collect();
        let expected = subset(1, Flat, bits);
        assert_eq!(parsed(&picked), Ok(expected), "bits {bits}");
        let mut reversed = picked.clone();
        reversed.reverse();
        assert_eq!(parsed(&reversed), Ok(expected), "bits {bits} reversed");
        let doubled: Vec<&str> = picked.iter().chain(picked.iter()).copied().collect();
        assert_eq!(parsed(&doubled), Ok(expected), "bits {bits} repeated");
        let mut with_context = vec!["--seed", "9", "--terrain", "hills"];
        with_context.extend(&picked);
        assert_eq!(parsed(&with_context), Ok(subset(9, Hills, bits)), "bits {bits} with seed and terrain");
    }
    assert_eq!(parsed(&["--pathfinder", "hills"]).unwrap().terrain, Flat, "the flag takes no value");
    assert_eq!(parsed(&["--berserk", "hills"]).unwrap().terrain, Flat, "Berserk still takes no value");
    for near_miss in ["--pathfinders", "--Pathfinder", "--pathfinder=1"] {
        assert!(!parsed(&[near_miss]).unwrap().pathfinder, "{near_miss}");
    }
    for near_miss in ["--berzerk", "--berserks", "--Berserk", "--berserk=1"] {
        assert!(!parsed(&[near_miss]).unwrap().berserk, "{near_miss}");
    }
    assert_eq!(parsed(&["--pathfinder", "--terrain", "x"]), Err("--terrain needs flat, hills or swamp".to_string()));
    assert_eq!(parsed(&["--pathfinder", "--seed"]), Err("--seed needs a u64".to_string()));
    assert_eq!(parsed(&["--berserk", "--terrain", "x"]), Err("--terrain needs flat, hills or swamp".to_string()));
    assert_eq!(parsed(&["--berserk", "--seed"]), Err("--seed needs a u64".to_string()));
}

#[test]
fn all_eight_capabilities_are_player_only_and_construction_changes_only_flags_for_all_256_subsets() {
    for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
        for seed in SEEDS {
            let base = battle(seed, terrain);
            assert!(base.units.iter().all(|u| !u.has_berserk && !u.berserk_used && !u.has_pathfinder));
            for bits in 0..=255u16 {
                let args = subset(seed, terrain, bits);
                let s = launch_battle_for(args);
                for (b, l) in base.units.iter().zip(&s.units) {
                    let player = l.side == Side::Player;
                    assert_eq!(l.has_berserk, player && args.berserk, "{} bits {bits}", l.name);
                    assert_eq!(l.has_pathfinder, player && args.pathfinder, "{} bits {bits}", l.name);
                    assert!(!l.berserk_used, "starts unused: {} bits {bits}", l.name);
                    assert_eq!(l.fast_adaptation_stacks, 0, "{} bits {bits}", l.name);
                    assert!(!l.nine_lives_used, "{} bits {bits}", l.name);
                    let mut expected = b.clone();
                    (expected.has_adrenaline, expected.has_rotation, expected.has_footwork, expected.has_fast_adaptation, expected.has_dodge) =
                        (l.has_adrenaline, l.has_rotation, l.has_footwork, l.has_fast_adaptation, l.has_dodge);
                    expected.has_nine_lives = l.has_nine_lives;
                    expected.has_berserk = l.has_berserk;
                    expected.has_pathfinder = l.has_pathfinder;
                    assert_eq!(*l, expected, "only selected capability flags differ ({})", l.name);
                }
                assert_eq!(s.rng, base.rng, "no RNG consumed");
                assert_eq!(s.queue, base.queue);
                assert_eq!((s.round, s.outcome(), s.end_reason()), (base.round, base.outcome(), base.end_reason()));
                assert_eq!(s.elevations, base.elevations);
                assert_eq!(s.state_hash() == base.state_hash(), bits == 0, "{terrain:?} bits {bits}");
                let without_pathfinder = launch_battle_for(LaunchArgs { pathfinder: false, ..args });
                assert!(without_pathfinder.units.iter().all(|u| !u.has_pathfinder));
                assert_eq!(
                    s.units.iter().map(|u| (u.has_adrenaline, u.has_rotation, u.has_footwork, u.has_fast_adaptation, u.has_dodge, u.has_nine_lives, u.has_berserk)).collect::<Vec<_>>(),
                    without_pathfinder.units.iter().map(|u| (u.has_adrenaline, u.has_rotation, u.has_footwork, u.has_fast_adaptation, u.has_dodge, u.has_nine_lives, u.has_berserk)).collect::<Vec<_>>()
                );
                let without_berserk = launch_battle_for(LaunchArgs { berserk: false, ..args });
                assert!(without_berserk.units.iter().all(|u| !u.has_berserk && !u.berserk_used));
                assert_eq!(s.units.iter().map(|u| u.has_adrenaline).collect::<Vec<_>>(), without_berserk.units.iter().map(|u| u.has_adrenaline).collect::<Vec<_>>());
                assert_eq!(s.units.iter().map(|u| u.has_nine_lives).collect::<Vec<_>>(), without_berserk.units.iter().map(|u| u.has_nine_lives).collect::<Vec<_>>());
            }
        }
    }
    assert_eq!(launch_battle_for(subset(1, TerrainPreset::Flat, 0)).state_hash(), 0x8c00302113cbe668, "default pin unchanged");
    assert_eq!(launch_battle_for(subset(1, TerrainPreset::Hills, 0)).state_hash(), 0xb759683cdff4bfa2, "Hills pin unchanged");
    assert!(launch_battle_with(3, TerrainPreset::Flat, true, true).units.iter().all(|u| !u.has_berserk && !u.has_pathfinder));
    assert!(adrenaline_battle(3, TerrainPreset::Hills).units.iter().all(|u| !u.has_berserk));
    assert!(rotation_battle(3, TerrainPreset::Hills).units.iter().all(|u| !u.has_berserk));
    assert!(footwork_battle(3, TerrainPreset::Hills).units.iter().all(|u| !u.has_berserk));
}

#[test]
fn launch_pathfinder_move_keeps_route_and_ap_but_discounts_preview_and_actual_fatigue() {
    let make_encounter = |pathfinder: bool| {
        let mut state = launch_battle_for(subset(1, TerrainPreset::Flat, if pathfinder { 128 } else { 0 }));
        for unit in &mut state.units {
            unit.alive = false;
        }
        state.units[0].alive = true;
        state.units[0].pos = hex(-2, -2);
        state.units[0].ap = 9;
        state.units[0].fatigue = 0;
        state.units[0].turn_started = true;
        state.units[3].alive = true;
        state.units[3].pos = hex(4, -2);
        state.queue = vec![0];
        state
    };
    let destination = hex(0, -2);
    let mut discounted = make_encounter(true);
    let mut twin = make_encounter(false);
    let before_rng = discounted.rng.clone();
    let pf_preview = discounted.preview_move(0, destination).unwrap();
    let twin_preview = twin.preview_move(0, destination).unwrap();

    assert!(discounted.units[0].has_pathfinder);
    assert!(!twin.units[0].has_pathfinder);
    assert_eq!(pf_preview.path, vec![hex(-1, -2), destination]);
    assert_eq!(pf_preview.path, twin_preview.path);
    assert_eq!(pf_preview.ap_cost, twin_preview.ap_cost);
    assert_eq!(pf_preview.ap_remaining, twin_preview.ap_remaining);
    assert_eq!((pf_preview.fatigue_cost, twin_preview.fatigue_cost), (2, 4));

    let pf_events = discounted.apply(Command::Move { unit: 0, to: destination }).unwrap();
    let twin_events = twin.apply(Command::Move { unit: 0, to: destination }).unwrap();
    assert_eq!(pf_events, twin_events, "no reactions means identical events");
    assert_eq!(pf_events, vec![Event::Moved { unit: 0, path: pf_preview.path.clone() }]);
    assert_eq!((discounted.units[0].pos, discounted.units[0].ap, discounted.units[0].fatigue), (destination, 9 - pf_preview.ap_cost, pf_preview.fatigue_cost));
    assert_eq!((twin.units[0].pos, twin.units[0].ap, twin.units[0].fatigue), (destination, 9 - twin_preview.ap_cost, twin_preview.fatigue_cost));
    assert_eq!(discounted.rng, before_rng, "no reaction roll was needed");
    assert_eq!(discounted.rng, twin.rng);
}

fn smoke_pathfinder_line(terrain: &str, all: bool) -> String {
    if all {
        smoke_line(&["--seed", "1", "--terrain", terrain, "--adrenaline", "--rotation", "--footwork", "--fast-adaptation", "--dodge", "--nine-lives", "--berserk", "--pathfinder"])
    } else {
        smoke_line(&["--seed", "1", "--terrain", terrain, "--pathfinder"])
    }
}

#[test]
fn headless_smoke_pathfinder_outputs_are_recorded_and_earlier_pins_stay_unchanged() {
    for (terrain, plain, seven, alone, all_eight) in [
        ("flat", FLAT_SEED_1, ALL_SEVEN_FLAT_SEED_1, PATHFINDER_FLAT_SEED_1, ALL_EIGHT_FLAT_SEED_1),
        ("hills", HILLS_SEED_1, ALL_SEVEN_HILLS_SEED_1, PATHFINDER_HILLS_SEED_1, ALL_EIGHT_HILLS_SEED_1),
    ] {
        assert_eq!(smoke_line(&["--seed", "1", "--terrain", terrain]), plain, "{terrain}: capability-free pin");
        assert_eq!(smoke_berserk_line(terrain, true), seven, "{terrain}: prior all-seven pin");
        assert_eq!(smoke_pathfinder_line(terrain, false), alone, "{terrain}: Pathfinder alone");
        assert_eq!(smoke_pathfinder_line(terrain, true), all_eight, "{terrain}: all eight options");
        assert_ne!(alone, plain);
        assert_ne!(all_eight, seven);
        assert_eq!(alone, smoke_line(&["--pathfinder", "--terrain", terrain, "--seed", "1", "--pathfinder"]), "order and repeats are harmless");
    }
}

#[test]
fn pathfinder_and_all_eight_replays_match_complete_commands_events_and_smoke_results() {
    for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
        let flat = terrain == TerrainPreset::Flat;
        let terrain_name = if flat { "flat" } else { "hills" };
        for (bits, expected) in [
            (128u16, if flat { PATHFINDER_FLAT_SEED_1 } else { PATHFINDER_HILLS_SEED_1 }),
            (255, if flat { ALL_EIGHT_FLAT_SEED_1 } else { ALL_EIGHT_HILLS_SEED_1 }),
        ] {
            let args = subset(1, terrain, bits);
            let (commands_a, events_a, state_a, n_a) = replay_loadout(args);
            let (commands_b, events_b, state_b, n_b) = replay_loadout(args);
            assert_eq!(commands_a, commands_b, "{terrain:?} mask {bits}: complete command replay");
            assert_eq!(events_a, events_b, "{terrain:?} mask {bits}: complete event replay");
            assert_eq!(
                (n_a, state_a.state_hash(), state_a.outcome(), state_a.end_reason(), state_a.round),
                (n_b, state_b.state_hash(), state_b.outcome(), state_b.end_reason(), state_b.round),
                "{terrain:?} mask {bits}: bounded replay result"
            );
            assert!(n_a <= MAX_COMMANDS && n_b <= MAX_COMMANDS);
            assert!(matches!(events_a.last(), Some(Event::BattleEnded { .. })), "battle must terminate cleanly");
            assert_eq!(
                format!("OUTCOME={:?} REASON={:?} ROUNDS={} HASH={:016x}", state_a.outcome().unwrap(), state_a.end_reason().unwrap(), state_a.round, state_a.state_hash()),
                expected
            );
            assert_eq!(smoke_pathfinder_line(terrain_name, bits == 255), expected);
        }
    }
}

fn smoke_berserk_line(terrain: &str, all: bool) -> String {
    if all {
        smoke_line(&["--seed", "1", "--terrain", terrain, "--adrenaline", "--rotation", "--footwork", "--fast-adaptation", "--dodge", "--nine-lives", "--berserk"])
    } else {
        smoke_line(&["--seed", "1", "--terrain", terrain, "--berserk"])
    }
}

#[test]
fn headless_smoke_berserk_outputs_are_recorded_and_earlier_runs_unchanged() {
    for (terrain, plain, alone, all_six, all_seven) in [
        ("flat", FLAT_SEED_1, BERSERK_FLAT_SEED_1, ALL_SIX_FLAT_SEED_1, ALL_SEVEN_FLAT_SEED_1),
        ("hills", HILLS_SEED_1, BERSERK_HILLS_SEED_1, ALL_SIX_HILLS_SEED_1, ALL_SEVEN_HILLS_SEED_1),
    ] {
        assert_eq!(smoke_line(&["--seed", "1", "--terrain", terrain]), plain, "{terrain}: default unchanged");
        assert_eq!(smoke_nine_lives_line(terrain, true), all_six, "{terrain}: six-option smoke unchanged");
        assert_eq!(smoke_berserk_line(terrain, false), alone, "{terrain}: Berserk alone");
        assert_eq!(smoke_berserk_line(terrain, true), all_seven, "{terrain}: all seven options");
        assert_ne!(alone, plain);
        assert_ne!(all_seven, all_six);
        assert_eq!(alone, smoke_line(&["--berserk", "--terrain", terrain, "--seed", "1", "--berserk"]), "order and repeats are irrelevant");
    }
}

#[test]
fn berserk_replays_are_deterministic_valid_bounded_and_match_smoke_results() {
    for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
        let flat = terrain == TerrainPreset::Flat;
        for (bits, expected) in [
            (64u16, if flat { BERSERK_FLAT_SEED_1 } else { BERSERK_HILLS_SEED_1 }),
            (127, if flat { ALL_SEVEN_FLAT_SEED_1 } else { ALL_SEVEN_HILLS_SEED_1 }),
        ] {
            let args = subset(1, terrain, bits);
            let (ca, ea, sa, na) = replay_loadout(args);
            let (cb, eb, sb, nb) = replay_loadout(args);
            assert_eq!(ca, cb, "{terrain:?}/{bits}: command stream");
            assert_eq!(ea, eb, "{terrain:?}/{bits}: event stream");
            assert_eq!((na, sa.state_hash(), sa.outcome(), sa.end_reason(), sa.round), (nb, sb.state_hash(), sb.outcome(), sb.end_reason(), sb.round));
            assert_eq!(sa.rng, sb.rng);
            assert!(na <= MAX_COMMANDS && matches!(ea.last(), Some(Event::BattleEnded { .. })));
            assert_eq!(
                format!("OUTCOME={:?} REASON={:?} ROUNDS={} HASH={:016x}", sa.outcome().unwrap(), sa.end_reason().unwrap(), sa.round, sa.state_hash()),
                expected
            );
            let berserk_events: Vec<u32> = ea.iter().filter_map(|e| if let Event::BerserkTriggered { unit, .. } = e { Some(*unit) } else { None }).collect();
            for unit in &berserk_events {
                assert!(*unit < 3 && sa.units[*unit as usize].has_berserk, "only enabled player units can refund: {terrain:?}/{bits}, unit {unit}");
            }
            assert!(sa.units.iter().filter(|u| u.berserk_used).all(|u| u.has_berserk), "a used flag implies capability");
        }
        let (_, events, _, _) = replay_loadout(subset(1, terrain, 63));
        assert!(!events.iter().any(|e| matches!(e, Event::BerserkTriggered { .. })), "without the option, no refund event");
    }
}

/// A legal scripted encounter from the player-only launch capability. P1 has 5 AP, so one
/// 4-AP attack is otherwise affordable but a second is not; a kill refund restores that budget.
fn berserk_refund_encounter() -> BattleState {
    let mut s = launch_battle_for(subset(1, TerrainPreset::Flat, 64));
    let park = [hex(-4, 2), hex(-4, 3), hex(-4, 4), hex(4, -4), hex(4, -3), hex(4, -2)];
    for (u, at) in s.units.iter_mut().zip(park) {
        u.pos = at;
        u.turn_started = false;
    }
    s.units[0].pos = hex(0, -2);
    s.units[3].pos = hex(1, -2);
    s.units[4].pos = hex(0, -1);
    s.units[0].ap = 5;
    s.units[0].fatigue = 0;
    s.units[0].stats.melee_skill = 100;
    s.units[0].turn_started = true;
    for id in [3usize, 4] {
        s.units[id].hp = 1;
        s.units[id].armour = 0;
    }
    s.queue.retain(|&q| q != 0);
    s.queue.insert(0, 0);
    let at: Vec<Hex> = s.units.iter().map(|u| u.pos).collect();
    for (i, h) in at.iter().enumerate() {
        assert!(map_hexes().contains(h) && !h.is_obstacle(), "fixture hex must be passable: {h:?}");
        assert!(!at[..i].contains(h), "fixture hexes must be distinct: {h:?}");
    }
    for u in s.units.iter().filter(|u| u.alive) {
        assert!(u.hp > 0 && u.hp <= u.stats.hp, "{} has legal HP", u.name);
        assert!(u.armour <= u.stats.armour && u.ap <= u.stats.ap_max && u.fatigue <= u.stats.fatigue_max, "{} has legal resources", u.name);
    }
    assert!(s.units[0].has_berserk && !s.units[0].berserk_used);
    assert!(s.units.iter().skip(3).all(|u| !u.has_berserk), "the option is player-only");
    s
}

#[test]
fn launch_berserk_refund_funds_a_second_attack_once_without_refunding_fatigue() {
    let first_attack = Command::Attack { attacker: 0, target: 3 };
    let second_attack = Command::Attack { attacker: 0, target: 4 };
    let encounter = (1u64..10_000)
        .find_map(|seed| {
            let mut with = berserk_refund_encounter();
            with.rng = Rng::new(seed);
            let mut without = with.clone();
            without.units[0].has_berserk = false;
            let first_with = with.apply(first_attack).ok()?;
            let first_without = without.apply(first_attack).ok()?;
            let hits_and_kills = |events: &[Event], target| {
                events.iter().any(|e| matches!(e, Event::Attacked { attacker: 0, target: t, hit: true, .. } if *t == target))
                    && events.iter().any(|e| matches!(e, Event::Died { unit } if *unit == target))
            };
            if !hits_and_kills(&first_with, 3) || !hits_and_kills(&first_without, 3) {
                return None;
            }
            let refund = first_with.iter().filter(|e| matches!(e, Event::BerserkTriggered { unit: 0, ap_gained: 4 })).count();
            if refund != 1 || first_without.iter().any(|e| matches!(e, Event::BerserkTriggered { .. })) {
                return None;
            }
            let after_refund = (with.units[0].ap, with.units[0].fatigue);
            let second_with = with.apply(second_attack).ok()?;
            if !hits_and_kills(&second_with, 4) || second_with.iter().any(|e| matches!(e, Event::BerserkTriggered { .. })) {
                return None;
            }
            Some((with, without, first_with, second_with, after_refund))
        })
        .expect("a deterministic RNG seed allows both kills");
    let (with, mut without, first, second, after_refund) = encounter;
    assert!(first.iter().any(|e| matches!(e, Event::BerserkTriggered { unit: 0, ap_gained: 4 })));
    assert_eq!(after_refund, (5, bb_rules::rules::ATTACK_FATIGUE), "the refund restores the second attack budget without refreshing fatigue");
    assert_eq!((with.units[0].ap, with.units[0].fatigue), (1, 2 * bb_rules::rules::ATTACK_FATIGUE));
    assert!(with.units[0].berserk_used);
    assert_eq!(second.iter().filter(|e| matches!(e, Event::Died { unit: 4 })).count(), 1);
    assert!(!second.iter().any(|e| matches!(e, Event::BerserkTriggered { .. })), "second kill gives no refund");
    assert_eq!(without.units[0].ap, 1, "without Berserk only one attack remains affordable");
    assert_eq!(without.units[0].fatigue, bb_rules::rules::ATTACK_FATIGUE);
    assert_eq!(without.apply(second_attack), Err(RuleError::NotEnoughAp));
}

// T46 recorded seed-1 smoke before the T47 AI policy existed. T47 keeps the same outputs because
// the stock encounters do not meet the wounded-and-threatened activation gate; focused legal AI
// fixtures in `indomitable_ai.rs` demonstrate activation and the subsequent attack. The launch
// option remains player-only (**APPROXIMATION**).

const INDOMITABLE_FLAT_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=f4049791f860bb83";
const INDOMITABLE_HILLS_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=4 HASH=fc9e225218a8006e";
const ALL_NINE_FLAT_SEED_1: &str = "OUTCOME=Victory REASON=Escape ROUNDS=3 HASH=bd7679eafc17ec03";
const ALL_NINE_HILLS_SEED_1: &str = "OUTCOME=Victory REASON=Elimination ROUNDS=4 HASH=87444e820db722a5";

fn smoke_indomitable_line(terrain: &str, all: bool) -> String {
    if all {
        smoke_line(&["--seed", "1", "--terrain", terrain, "--adrenaline", "--rotation", "--footwork", "--fast-adaptation", "--dodge", "--nine-lives", "--berserk", "--pathfinder", "--indomitable"])
    } else {
        smoke_line(&["--seed", "1", "--terrain", terrain, "--indomitable"])
    }
}

#[test]
fn indomitable_option_defaults_repeats_and_composes_with_all_launch_options_for_512_subsets() {
    use TerrainPreset::{Flat, Hills};

    assert!(!parsed(&[]).unwrap().indomitable, "Indomitable is off by default");
    assert!(!parsed(&["--pathfinder"]).unwrap().indomitable);
    assert_eq!(parsed(&["--indomitable"]), Ok(subset(1, Flat, 256)));
    assert_eq!(parsed(&["--indomitable", "--indomitable", "--indomitable"]), Ok(subset(1, Flat, 256)), "repeats are harmless");
    assert_eq!(parsed(&["--seed", "4", "--terrain", "hills", "--indomitable"]), Ok(subset(4, Hills, 256)));
    assert_eq!(parsed(&["--indomitable", "--terrain", "flat"]).unwrap().terrain, Flat);
    assert_eq!(parsed(&["--pathfinder"]), Ok(subset(1, Flat, 128)), "Pathfinder alone is unchanged");
    assert_eq!(subset(1, Flat, 511).indomitable, true);
    assert_eq!(subset(1, Flat, 255).indomitable, false, "bit 8 is the only Indomitable bit");

    assert_eq!(CAPABILITY_OPTIONS.len(), 9);
    for bits in 0..512u16 {
        let picked: Vec<&str> = CAPABILITY_OPTIONS.iter().enumerate().filter(|(i, _)| bits >> i & 1 != 0).map(|(_, name)| *name).collect();
        let expected = subset(1, Flat, bits);
        assert_eq!(parsed(&picked), Ok(expected), "bits {bits}");
        let mut reversed = picked.clone();
        reversed.reverse();
        assert_eq!(parsed(&reversed), Ok(expected), "bits {bits} reversed");
        let doubled: Vec<&str> = picked.iter().chain(picked.iter()).copied().collect();
        assert_eq!(parsed(&doubled), Ok(expected), "bits {bits} repeated");
        let mut with_context = vec!["--seed", "9", "--terrain", "hills"];
        with_context.extend(&picked);
        assert_eq!(parsed(&with_context), Ok(subset(9, Hills, bits)), "bits {bits} with seed and terrain");
    }
    assert_eq!(parsed(&["--indomitable", "hills"]).unwrap().terrain, Flat, "the flag takes no value");
    for near_miss in ["--indomitables", "--Indomitable", "--indomitable=1", "--indomitible"] {
        assert!(!parsed(&[near_miss]).unwrap().indomitable, "{near_miss}");
    }
    assert_eq!(parsed(&["--indomitable", "--terrain", "x"]), Err("--terrain needs flat, hills or swamp".to_string()));
    assert_eq!(parsed(&["--indomitable", "--seed"]), Err("--seed needs a u64".to_string()));
}

#[test]
fn all_nine_capabilities_are_player_only_inactive_and_construction_changes_only_flags_for_all_512_subsets() {
    for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
        for seed in SEEDS {
            let base = battle(seed, terrain);
            assert!(base.units.iter().all(|u| !u.has_indomitable && !u.indomitable_active));
            for bits in 0..512u16 {
                let args = subset(seed, terrain, bits);
                let s = launch_battle_for(args);
                for (b, l) in base.units.iter().zip(&s.units) {
                    let player = l.side == Side::Player;
                    assert_eq!(l.has_indomitable, player && args.indomitable, "{} bits {bits}", l.name);
                    assert!(!l.indomitable_active && !l.is_indomitable_active(), "stance starts inactive: {} bits {bits}", l.name);
                    assert_eq!(l.fast_adaptation_stacks, 0, "{} bits {bits}", l.name);
                    assert!(!l.nine_lives_used && !l.berserk_used, "{} bits {bits}", l.name);
                    let mut expected = b.clone();
                    (expected.has_adrenaline, expected.has_rotation, expected.has_footwork, expected.has_fast_adaptation, expected.has_dodge) =
                        (l.has_adrenaline, l.has_rotation, l.has_footwork, l.has_fast_adaptation, l.has_dodge);
                    (expected.has_nine_lives, expected.has_berserk, expected.has_pathfinder, expected.has_indomitable) =
                        (l.has_nine_lives, l.has_berserk, l.has_pathfinder, l.has_indomitable);
                    assert_eq!(*l, expected, "only selected capability flags differ ({})", l.name);
                }
                assert_eq!(s.rng, base.rng, "no RNG consumed");
                assert_eq!(s.queue, base.queue);
                assert_eq!((s.round, s.outcome(), s.end_reason()), (base.round, base.outcome(), base.end_reason()));
                assert_eq!(s.elevations, base.elevations);
                assert_eq!(s.state_hash() == base.state_hash(), bits == 0, "{terrain:?} bits {bits}");
                let without = launch_battle_for(LaunchArgs { indomitable: false, ..args });
                assert!(without.units.iter().all(|u| !u.has_indomitable));
                assert_eq!(s.units.iter().map(|u| u.has_pathfinder).collect::<Vec<_>>(), without.units.iter().map(|u| u.has_pathfinder).collect::<Vec<_>>());
                assert_eq!(s.state_hash() == without.state_hash(), !args.indomitable, "the capability bit is hashed: bits {bits}");
            }
        }
    }
    assert_eq!(launch_battle_for(subset(1, TerrainPreset::Flat, 0)).state_hash(), 0x8c00302113cbe668, "default pin unchanged");
    assert_eq!(launch_battle_for(subset(1, TerrainPreset::Hills, 0)).state_hash(), 0xb759683cdff4bfa2, "Hills pin unchanged");
    assert!(launch_battle_with(3, TerrainPreset::Flat, true, true).units.iter().all(|u| !u.has_indomitable));
    assert!(adrenaline_battle(3, TerrainPreset::Hills).units.iter().all(|u| !u.has_indomitable));
    assert!(rotation_battle(3, TerrainPreset::Hills).units.iter().all(|u| !u.has_indomitable));
    assert!(footwork_battle(3, TerrainPreset::Hills).units.iter().all(|u| !u.has_indomitable));
}

#[test]
fn launch_indomitable_encounter_activates_and_actually_halves_a_melee_hit() {
    use bb_rules::rules::{INDOMITABLE_AP, INDOMITABLE_FATIGUE, indomitable_availability};
    for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
        // Player 0 and enemy 3 alone on the lane r = -2; the enemy deals exactly 20 to bare armour.
        let make = || {
            let mut s = launch_battle_for(subset(1, terrain, 256));
            for u in &mut s.units {
                u.alive = false;
            }
            s.units[0].alive = true;
            s.units[0].pos = hex(0, -2);
            s.units[0].armour = 0;
            s.units[0].stats.defense = 0;
            s.units[3].alive = true;
            s.units[3].pos = hex(1, -2);
            s.units[3].stats.melee_skill = 100;
            (s.units[3].stats.dmg_min, s.units[3].stats.dmg_max) = (20, 20);
            s.units[0].ap = 9;
            s.units[0].fatigue = 0;
            s.units[0].turn_started = true;
            s.queue = vec![0];
            s
        };
        let mut stance = make();
        let mut twin = make();
        assert!(stance.units[0].has_indomitable && !stance.units[0].indomitable_active);
        assert!(stance.units.iter().filter(|u| u.side == Side::Enemy).all(|u| !u.has_indomitable));
        assert_eq!(indomitable_availability(&stance.units[0]), Ok(()));
        let before_rng = stance.rng.clone();
        assert_eq!(stance.apply(Command::Indomitable { unit: 0 }), Ok(vec![Event::IndomitableUsed { unit: 0 }]));
        assert_eq!(stance.rng, before_rng, "activation consumes no RNG");
        assert_eq!((stance.units[0].ap, stance.units[0].fatigue), (9 - INDOMITABLE_AP, INDOMITABLE_FATIGUE));
        assert!(stance.units[0].is_indomitable_active());
        assert_eq!(stance.apply(Command::Indomitable { unit: 0 }), Err(RuleError::IndomitableActive), "no duplicate activation");
        assert_eq!(indomitable_availability(&stance.units[0]), Err(RuleError::IndomitableActive));

        // Both boards pass the player's turn the same way; the enemy then swings with equal RNG.
        for s in [&mut stance, &mut twin] {
            s.queue = vec![3];
            s.units[3].ap = 9;
            s.units[3].fatigue = 0;
            s.units[3].turn_started = true;
            s.rng = Rng::new(0);
        }
        let seed = (0u64..)
            .find(|&k| {
                let mut probe = twin.clone();
                probe.rng = Rng::new(k);
                matches!(probe.apply(Command::Attack { attacker: 3, target: 0 }).unwrap()[0], Event::Attacked { hit: true, .. })
            })
            .unwrap();
        stance.rng = Rng::new(seed);
        twin.rng = Rng::new(seed);
        let hp = stance.units[0].hp;
        let a = stance.apply(Command::Attack { attacker: 3, target: 0 }).unwrap();
        let b = twin.apply(Command::Attack { attacker: 3, target: 0 }).unwrap();
        assert!(matches!(a[0], Event::Attacked { hit: true, dmg: 10, hp_loss: 10, .. }), "{terrain:?}: {a:?}");
        assert!(matches!(b[0], Event::Attacked { hit: true, dmg: 20, hp_loss: 20, .. }), "{terrain:?}: {b:?}");
        assert_eq!((stance.units[0].hp, twin.units[0].hp), (hp - 10, hp - 20));
        // Same hit roll and chance; the larger hit may add a heavy-hit morale check, so only the
        // `Attacked` rolls (not the final RNG states) are comparable.
        match (&a[0], &b[0]) {
            (Event::Attacked { roll: ra, chance: ca, .. }, Event::Attacked { roll: rb, chance: cb, .. }) => assert_eq!((ra, ca), (rb, cb)),
            other => panic!("{other:?}"),
        }
    }
}

#[test]
fn headless_smoke_indomitable_outputs_are_recorded_and_earlier_pins_stay_unchanged() {
    for (terrain, plain, eight, alone, all_nine) in [
        ("flat", FLAT_SEED_1, ALL_EIGHT_FLAT_SEED_1, INDOMITABLE_FLAT_SEED_1, ALL_NINE_FLAT_SEED_1),
        ("hills", HILLS_SEED_1, ALL_EIGHT_HILLS_SEED_1, INDOMITABLE_HILLS_SEED_1, ALL_NINE_HILLS_SEED_1),
    ] {
        assert_eq!(smoke_line(&["--seed", "1", "--terrain", terrain]), plain, "{terrain}: capability-free pin");
        assert_eq!(smoke_pathfinder_line(terrain, true), eight, "{terrain}: prior all-eight pin");
        assert_eq!(smoke_indomitable_line(terrain, false), alone, "{terrain}: Indomitable alone");
        assert_eq!(smoke_indomitable_line(terrain, true), all_nine, "{terrain}: all nine options");
        assert_ne!(alone, plain, "the capability bit is hashed");
        assert_ne!(all_nine, eight, "the capability bit is hashed");
        assert_eq!(alone, smoke_line(&["--indomitable", "--terrain", terrain, "--seed", "1", "--indomitable"]), "order and repeats are harmless");
    }
}

#[test]
fn indomitable_and_all_nine_replays_are_deterministic_and_match_smoke_results() {
    for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
        let flat = terrain == TerrainPreset::Flat;
        let terrain_name = if flat { "flat" } else { "hills" };
        for (bits, off_bits, expected) in [
            (256u16, 0u16, if flat { INDOMITABLE_FLAT_SEED_1 } else { INDOMITABLE_HILLS_SEED_1 }),
            (511, 255, if flat { ALL_NINE_FLAT_SEED_1 } else { ALL_NINE_HILLS_SEED_1 }),
        ] {
            let args = subset(1, terrain, bits);
            let (commands_a, events_a, state_a, n_a) = replay_loadout(args);
            let (commands_b, events_b, state_b, n_b) = replay_loadout(args);
            assert_eq!(commands_a, commands_b, "{terrain:?} mask {bits}: complete command replay");
            assert_eq!(events_a, events_b, "{terrain:?} mask {bits}: complete event replay");
            assert_eq!(
                (n_a, state_a.state_hash(), state_a.outcome(), state_a.end_reason(), state_a.round),
                (n_b, state_b.state_hash(), state_b.outcome(), state_b.end_reason(), state_b.round),
                "{terrain:?} mask {bits}: bounded replay result"
            );
            assert!(n_a <= MAX_COMMANDS && n_b <= MAX_COMMANDS);
            assert!(matches!(events_a.last(), Some(Event::BattleEnded { .. })), "battle must terminate cleanly");
            let casts: Vec<u32> = commands_a
                .iter()
                .filter_map(|command| match command {
                    Command::Indomitable { unit } => Some(*unit),
                    _ => None,
                })
                .collect();
            let activations: Vec<u32> = events_a
                .iter()
                .filter_map(|event| match event {
                    Event::IndomitableUsed { unit } => Some(*unit),
                    _ => None,
                })
                .collect();
            assert_eq!(casts, activations, "every emitted activation command is accepted exactly once");

            // T47 smoke may or may not cast. Without a cast, the Indomitable-off command/event
            // stream is still pinned equal; if it casts, only the two enabled-run replays must match.
            let (commands_off, events_off, state_off, n_off) = replay_loadout(subset(1, terrain, off_bits));
            if casts.is_empty() {
                assert_eq!((commands_a, events_a, n_a), (commands_off, events_off, n_off), "{terrain:?} mask {bits}: no-cast stream matches the Indomitable-off run");
                assert_eq!(state_a.rng, state_off.rng);
            } else {
                assert_ne!(commands_a, commands_off, "{terrain:?} mask {bits}: cast changes the command stream");
                assert_ne!(events_a, events_off, "{terrain:?} mask {bits}: cast changes the event stream");
                assert!(!events_off.iter().any(|event| matches!(event, Event::IndomitableUsed { .. })));
            }
            assert_ne!(state_a.state_hash(), state_off.state_hash(), "hashes include the capability bit");
            assert_eq!(
                format!("OUTCOME={:?} REASON={:?} ROUNDS={} HASH={:016x}", state_a.outcome().unwrap(), state_a.end_reason().unwrap(), state_a.round, state_a.state_hash()),
                expected
            );
            assert_eq!(smoke_indomitable_line(terrain_name, bits == 511), expected);
        }
    }
}
