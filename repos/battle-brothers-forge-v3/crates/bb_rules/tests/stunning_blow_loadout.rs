//! T65 `--stunning-blow` launch option (APPROXIMATION assignment of the accepted T62 capability)
//! and T66 deterministic AI skill selection.

use std::process::Command as Process;

use bb_rules::ai;
use bb_rules::scenario::{
    LaunchArgs, TerrainPreset, adrenaline_battle, battle, footwork_battle, launch_battle,
    launch_battle_for, launch_battle_with, parse_args, rotation_battle,
};
use bb_rules::{BattleState, Command, Event, Outcome, EndReason, Side};

const TERRAINS: [TerrainPreset; 3] = [TerrainPreset::Flat, TerrainPreset::Hills, TerrainPreset::Swamp];
const TERRAIN_ARGS: [&str; 3] = ["flat", "hills", "swamp"];
const EXISTING_FLAGS: [&str; 10] = [
    "--adrenaline",
    "--rotation",
    "--footwork",
    "--fast-adaptation",
    "--dodge",
    "--nine-lives",
    "--berserk",
    "--pathfinder",
    "--indomitable",
    "--two-hex",
];
const MAX_REPLAY_COMMANDS: usize = 10_000;

fn args(seed: u64, terrain: TerrainPreset, bits: u16, stunning_blow: bool) -> LaunchArgs {
    LaunchArgs {
        seed,
        terrain,
        adrenaline: bits & (1 << 0) != 0,
        rotation: bits & (1 << 1) != 0,
        footwork: bits & (1 << 2) != 0,
        fast_adaptation: bits & (1 << 3) != 0,
        dodge: bits & (1 << 4) != 0,
        nine_lives: bits & (1 << 5) != 0,
        berserk: bits & (1 << 6) != 0,
        pathfinder: bits & (1 << 7) != 0,
        indomitable: bits & (1 << 8) != 0,
        two_hex: bits & (1 << 9) != 0,
        stunning_blow,
        killing_frenzy: false,
    }
}

fn parsed(argv: &[&str]) -> LaunchArgs {
    parse_args(argv.iter().map(|s| (*s).to_owned())).expect("valid launch arguments")
}

#[test]
fn option_defaults_off_is_idempotent_order_independent_and_preserves_parser_errors() {
    assert_eq!(parsed(&[]), args(1, TerrainPreset::Flat, 0, false));
    assert!(!parsed(&["--terrain", "swamp", "--seed", "7"]).stunning_blow);

    for (terrain, terrain_arg) in TERRAINS.into_iter().zip(TERRAIN_ARGS) {
        assert_eq!(parsed(&["--stunning-blow"]), args(1, TerrainPreset::Flat, 0, true));
        assert_eq!(parsed(&["--stunning-blow", "--stunning-blow"]), args(1, TerrainPreset::Flat, 0, true));
        assert_eq!(parsed(&["--stunning-blow", "--seed", "9", "--terrain", terrain_arg]), args(9, terrain, 0, true));
        assert_eq!(parsed(&["--terrain", terrain_arg, "--seed", "9", "--stunning-blow"]), args(9, terrain, 0, true));
        assert_eq!(parsed(&["--seed", "9", "--stunning-blow", "--terrain", terrain_arg, "--stunning-blow"]), args(9, terrain, 0, true));

        for (bit, flag) in EXISTING_FLAGS.into_iter().enumerate() {
            assert_eq!(
                parsed(&[flag, "--stunning-blow", "--terrain", terrain_arg]),
                args(1, terrain, 1 << bit, true),
                "{flag} on {terrain:?}"
            );
            assert_eq!(
                parsed(&["--terrain", terrain_arg, flag]),
                args(1, terrain, 1 << bit, false),
                "{flag} alone on {terrain:?}"
            );
        }

        let mut combined: Vec<&str> = vec!["--stunning-blow", "--seed", "9", "--terrain", terrain_arg];
        combined.extend(EXISTING_FLAGS);
        assert_eq!(parsed(&combined), args(9, terrain, (1 << EXISTING_FLAGS.len()) - 1, true));
    }

    // The new option has no value, so the following value-taking option still parses normally.
    assert_eq!(
        parsed(&["--stunning-blow", "--seed", "5", "--terrain", "swamp"]),
        args(5, TerrainPreset::Swamp, 0, true)
    );
    for near_miss in ["--stunning_blow", "--stunningblow", "--stunning-blow=1"] {
        assert!(!parsed(&[near_miss]).stunning_blow, "{near_miss}");
    }
    assert_eq!(
        parse_args(["--seed".to_owned(), "not-a-number".to_owned()]),
        Err("--seed needs a u64".to_owned())
    );
    assert_eq!(
        parse_args(["--terrain".to_owned(), "bog".to_owned()]),
        Err("--terrain needs flat, hills or swamp".to_owned())
    );
}

#[test]
fn launch_assignment_changes_only_player_capability_and_starts_without_stun_effects() {
    for terrain in TERRAINS {
        for seed in [1, 42] {
            for bits in [0, (1 << EXISTING_FLAGS.len()) - 1] {
                let selected = args(seed, terrain, bits, true);
                let without = args(seed, terrain, bits, false);
                let enabled = launch_battle_for(selected);
                let baseline = launch_battle_for(without);
                let scenario = battle(seed, terrain);

                for unit in &enabled.units {
                    assert_eq!(unit.has_stunning_blow, unit.side == Side::Player, "{} {terrain:?}", unit.name);
                    assert!(!unit.stunned, "{} starts unstunned", unit.name);
                }
                let mut normalized = enabled.clone();
                for unit in &mut normalized.units {
                    unit.has_stunning_blow = false;
                }
                assert_eq!(normalized.units, baseline.units, "only the selected capability differs");
                assert_eq!(enabled.rng, baseline.rng);
                assert_eq!(enabled.queue, baseline.queue);
                assert_eq!(enabled.round, baseline.round);
                assert_eq!(enabled.elevations, baseline.elevations);
                assert_eq!(enabled.ground, baseline.ground);
                assert_eq!((enabled.outcome, enabled.end_reason), (baseline.outcome, baseline.end_reason));
                assert_eq!(baseline.rng, scenario.rng);
                assert_eq!(baseline.queue, scenario.queue);

                let no_loadout = launch_battle_for(args(seed, terrain, 0, false));
                let mut selected_only = launch_battle_for(args(seed, terrain, 0, true));
                assert_eq!(no_loadout.units, scenario.units);
                for unit in &mut selected_only.units {
                    unit.has_stunning_blow = false;
                }
                assert_eq!(selected_only.units, no_loadout.units);
                assert_ne!(launch_battle_for(args(seed, terrain, 0, true)).state_hash(), no_loadout.state_hash());
                // State normalization above is only for the player capability; no other unit data or effects differ.
                no_loadout.units.iter().for_each(|u| assert!(!u.stunned));
            }
        }
    }
}

#[test]
fn compatibility_constructors_keep_stunning_blow_disabled() {
    for terrain in TERRAINS {
        for state in [
            battle(1, terrain),
            launch_battle_for(args(1, terrain, 0, false)),
            launch_battle(1, terrain, true),
            launch_battle_with(1, terrain, true, true),
            adrenaline_battle(1, terrain),
            rotation_battle(1, terrain),
            footwork_battle(1, terrain),
        ] {
            assert!(state.units.iter().all(|unit| !unit.has_stunning_blow));
        }
    }
}

type Replay = (Vec<Command>, Vec<Event>, BattleState, usize);

fn replay(launch: LaunchArgs) -> Replay {
    let mut state = launch_battle_for(launch);
    let mut commands = Vec::new();
    let mut events = Vec::new();
    while state.outcome().is_none() {
        let plan = ai::plan_turn(&state);
        let command = *plan.first().expect("AI returns a command while the battle runs");
        commands.push(command);
        events.extend(state.apply(command).unwrap_or_else(|error| panic!("{launch:?}: {error:?} from {command:?}")));
        assert!(commands.len() <= MAX_REPLAY_COMMANDS, "{launch:?} exceeded replay bound");
    }
    let count = commands.len();
    (commands, events, state, count)
}

/// Newly measured T66 seed-1 outcomes and Stunning Blow command counts.
const MEASURED: [(TerrainPreset, bool, (Outcome, EndReason, u32, u64, usize)); 6] = [
    (TerrainPreset::Flat, false, (Outcome::Victory, EndReason::Elimination, 4, 0xf5ac_285b_cd8e_73a7, 6)),
    (TerrainPreset::Flat, true, (Outcome::Victory, EndReason::Elimination, 3, 0x10c9_c265_2446_5fb3, 3)),
    (TerrainPreset::Hills, false, (Outcome::Victory, EndReason::Elimination, 5, 0xc8a1_11db_ba69_e14c, 8)),
    (TerrainPreset::Hills, true, (Outcome::Victory, EndReason::Escape, 5, 0x4d02_8e67_e292_3a58, 7)),
    (TerrainPreset::Swamp, false, (Outcome::Victory, EndReason::Elimination, 5, 0x8ea1_5328_33f3_856b, 4)),
    (TerrainPreset::Swamp, true, (Outcome::Victory, EndReason::Escape, 5, 0x89dc_5a1d_5aed_a147, 6)),
];

#[test]
fn deterministic_ai_replays_on_every_preset_match_headless_smoke_for_both_loadouts() {
    let exe = env!("CARGO_BIN_EXE_headless_smoke");
    for (terrain, combined, want) in MEASURED {
        let terrain_arg = match terrain {
            TerrainPreset::Flat => "flat",
            TerrainPreset::Hills => "hills",
            TerrainPreset::Swamp => "swamp",
        };
        let bits = if combined { (1 << EXISTING_FLAGS.len()) - 1 } else { 0 };
        let launch = args(1, terrain, bits, true);
        let (commands_a, events_a, state_a, count_a) = replay(launch);
        let (commands_b, events_b, state_b, count_b) = replay(launch);
        assert_eq!(commands_a, commands_b, "{terrain:?} combined={combined}");
        assert_eq!(events_a, events_b, "{terrain:?} combined={combined}");
        assert_eq!(state_a.units, state_b.units);
        assert_eq!(state_a.rng, state_b.rng);
        assert_eq!(count_a, count_b);
        assert!(count_a <= MAX_REPLAY_COMMANDS);
        let skill_count = commands_a.iter().filter(|command| matches!(command, Command::StunningBlow { .. })).count();
        let got = (state_a.outcome().unwrap(), state_a.end_reason().unwrap(), state_a.round, state_a.state_hash());
        assert_eq!((got.0, got.1, got.2, got.3, skill_count), want, "{terrain:?} combined={combined}");

        let mut process = Process::new(exe);
        process.args(["--seed", "1", "--terrain", terrain_arg, "--stunning-blow"]);
        if combined {
            process.args(EXISTING_FLAGS);
        }
        let output = process.output().expect("headless_smoke runs");
        assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
        let expected = format!("OUTCOME={:?} REASON={:?} ROUNDS={} HASH={:016x}\n", got.0, got.1, got.2, got.3);
        assert_eq!(String::from_utf8_lossy(&output.stdout), expected, "{terrain:?} combined={combined}");
    }
}
