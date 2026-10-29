//! Swamp preset plus parsed CLI launch and smoke coverage (APPROXIMATION: the layout is slice design).

use std::process::Command as Process;

use bb_rules::ai;
use bb_rules::hex::{Hex, hex, map_hexes};
use bb_rules::scenario::{
    LaunchArgs, TerrainPreset, adrenaline_battle, battle, footwork_battle, launch_battle,
    launch_battle_for, launch_battle_with, parse_args, rotation_battle,
};
use bb_rules::terrain::TerrainKind;
use bb_rules::{BattleState, Command, Event, RuleError, Side};

const MAX_COMMANDS: usize = 10_000;

fn launch_args(seed: u64, terrain: TerrainPreset, pathfinder: bool) -> LaunchArgs {
    LaunchArgs {
        seed,
        terrain,
        adrenaline: false,
        rotation: false,
        footwork: false,
        fast_adaptation: false,
        dodge: false,
        nine_lives: false,
        berserk: false,
        pathfinder,
        indomitable: false,
        two_hex: false,
        stunning_blow: false, killing_frenzy: false,
    }
}

fn is_swamp(at: Hex) -> bool {
    !at.is_obstacle() && (-1..=1).contains(&at.q)
}

fn assert_swamp_layout(state: &BattleState) {
    let hexes = map_hexes();
    assert_eq!(hexes.len(), 61);
    for at in &hexes {
        assert_eq!(state.elevations.get(*at), Some(0), "elevation at {at:?}");
        assert_eq!(
            state.ground.get(*at),
            Some(if is_swamp(*at) {
                TerrainKind::Swamp
            } else {
                TerrainKind::Ground
            }),
            "ground kind at {at:?}"
        );
    }
    assert!(state.elevations.is_flat());
    assert_eq!(
        state.ground.entries().collect::<Vec<_>>(),
        hexes
            .into_iter()
            .filter(|&at| is_swamp(at))
            .map(|at| (at, TerrainKind::Swamp))
            .collect::<Vec<_>>()
    );
}

fn capability_flags(unit: &bb_rules::Unit) -> [bool; 9] {
    [
        unit.has_adrenaline,
        unit.has_rotation,
        unit.has_footwork,
        unit.has_fast_adaptation,
        unit.has_dodge,
        unit.has_nine_lives,
        unit.has_berserk,
        unit.has_pathfinder,
        unit.has_indomitable,
    ]
}

fn assert_loadout(state: &BattleState, player_mask: u16) {
    assert_swamp_layout(state);
    for unit in &state.units {
        let expected = if unit.side == Side::Player {
            player_mask
        } else {
            0
        };
        let actual = capability_flags(unit)
            .into_iter()
            .enumerate()
            .fold(0u16, |mask, (bit, enabled)| {
                mask | (u16::from(enabled) << bit)
            });
        assert_eq!(actual, expected, "capabilities for {}", unit.name);
        assert!(
            !unit.adrenaline_pending,
            "Adrenaline starts inactive for {}",
            unit.name
        );
        assert_eq!(
            unit.fast_adaptation_stacks, 0,
            "initial stacks for {}",
            unit.name
        );
        assert!(
            !unit.nine_lives_used,
            "Nine Lives starts unused for {}",
            unit.name
        );
        assert!(
            !unit.berserk_used,
            "Berserk starts unused for {}",
            unit.name
        );
        assert!(
            !unit.indomitable_active,
            "Indomitable starts inactive for {}",
            unit.name
        );
    }
}

#[test]
fn swamp_layout_is_exact_and_deterministic_without_changing_units_or_rng() {
    for seed in [1, 7, 23] {
        let base = BattleState::new(seed);
        let flat = battle(seed, TerrainPreset::Flat);
        let hills = battle(seed, TerrainPreset::Hills);
        let swamp_a = battle(seed, TerrainPreset::Swamp);
        let swamp_b = battle(seed, TerrainPreset::Swamp);

        assert_swamp_layout(&swamp_a);
        for obstacle in map_hexes().into_iter().filter(|at| at.is_obstacle()) {
            assert_eq!(
                swamp_a.preview_move(0, obstacle),
                Err(RuleError::Unreachable),
                "obstacle {obstacle:?}"
            );
        }
        assert_eq!(swamp_a.ground, swamp_b.ground);
        assert_eq!(swamp_a.elevations, swamp_b.elevations);
        assert_eq!(swamp_a.state_hash(), swamp_b.state_hash());
        for other in [&flat, &hills, &swamp_a] {
            assert_eq!(
                other.units, base.units,
                "units and placements for seed {seed}"
            );
            assert_eq!(other.queue, base.queue, "turn order for seed {seed}");
            assert_eq!(other.rng, base.rng, "initial RNG for seed {seed}");
            assert_eq!(other.round, base.round);
        }
        assert_ne!(
            swamp_a.state_hash(),
            flat.state_hash(),
            "Swamp terrain must affect the hash"
        );
        assert_ne!(
            swamp_a.state_hash(),
            hills.state_hash(),
            "Swamp terrain must hash differently from Hills"
        );

        for unit in &swamp_a.units {
            assert!(map_hexes().contains(&unit.pos));
            assert!(!unit.pos.is_obstacle());
            assert_eq!(unit.pos, base.units[unit.id as usize].pos);
        }
    }
}

#[test]
fn terrain_names_are_case_sensitive_and_flat_hills_defaults_and_hashes_stay_unchanged() {
    assert_eq!(TerrainPreset::default(), TerrainPreset::Flat);
    assert_eq!(TerrainPreset::parse("flat"), Some(TerrainPreset::Flat));
    assert_eq!(TerrainPreset::parse("hills"), Some(TerrainPreset::Hills));
    assert_eq!(TerrainPreset::parse("swamp"), Some(TerrainPreset::Swamp));
    assert_eq!(TerrainPreset::parse("Swamp"), None);
    assert_eq!(TerrainPreset::parse("SWAMP"), None);

    let base = BattleState::new(1);
    let flat = battle(1, TerrainPreset::Flat);
    assert_eq!(flat.ground, base.ground);
    assert_eq!(flat.elevations, base.elevations);
    assert_eq!(flat.state_hash(), 0x8c00_3021_13cb_e668);

    let hills = battle(1, TerrainPreset::Hills);
    for at in map_hexes() {
        assert_eq!(
            hills.ground.get(at),
            Some(TerrainKind::Ground),
            "Hills ground at {at:?}"
        );
        let height = if at == hex(0, -1) || at == hex(0, 1) {
            3
        } else {
            match at.q {
                0 => 2,
                -1 | 1 => 1,
                _ => 0,
            }
        };
        assert_eq!(
            hills.elevations.get(at),
            Some(height),
            "Hills height at {at:?}"
        );
    }
    assert_eq!(hills.state_hash(), 0xb759_683c_dff4_bfa2);

    assert_eq!(
        parse_args(["--terrain".into(), "flat".into()])
            .unwrap()
            .terrain,
        TerrainPreset::Flat
    );
    assert_eq!(
        parse_args(["--terrain".into(), "hills".into()])
            .unwrap()
            .terrain,
        TerrainPreset::Hills
    );
    assert_eq!(
        parse_args(["--terrain".into(), "swamp".into()])
            .unwrap()
            .terrain,
        TerrainPreset::Swamp
    );
    assert_eq!(
        parse_args(["--terrain".into(), "hills".into(), "--terrain".into(), "swamp".into()])
            .unwrap()
            .terrain,
        TerrainPreset::Swamp,
        "last terrain value wins"
    );
    assert_eq!(
        parse_args(["--terrain".into(), "swamp".into(), "--terrain".into(), "flat".into()])
            .unwrap()
            .terrain,
        TerrainPreset::Flat,
        "last terrain value wins"
    );
    let cli = Process::new(env!("CARGO_BIN_EXE_headless_smoke"))
        .args(["--seed", "1", "--terrain", "swamp"])
        .output()
        .expect("run headless_smoke with Swamp terrain");
    assert!(cli.status.success(), "{}", String::from_utf8_lossy(&cli.stderr));
    assert!(String::from_utf8_lossy(&cli.stdout).starts_with("OUTCOME="));
}

#[test]
fn all_existing_launch_constructors_preserve_swamp_and_player_only_loadouts() {
    assert_loadout(&battle(5, TerrainPreset::Swamp), 0);
    assert_loadout(&adrenaline_battle(5, TerrainPreset::Swamp), 1 << 0);
    assert_loadout(&rotation_battle(5, TerrainPreset::Swamp), 1 << 1);
    assert_loadout(&footwork_battle(5, TerrainPreset::Swamp), 1 << 2);
    assert_loadout(&launch_battle(5, TerrainPreset::Swamp, true), 1 << 0);
    assert_loadout(
        &launch_battle_with(5, TerrainPreset::Swamp, true, true),
        (1 << 0) | (1 << 1),
    );

    let all_nine = LaunchArgs {
        seed: 5,
        terrain: TerrainPreset::Swamp,
        adrenaline: true,
        rotation: true,
        footwork: true,
        fast_adaptation: true,
        dodge: true,
        nine_lives: true,
        berserk: true,
        pathfinder: true,
        indomitable: true,
        two_hex: false,
        stunning_blow: false, killing_frenzy: false,
    };
    assert_loadout(&launch_battle_for(all_nine), 0b1_1111_1111);
}

#[test]
fn initial_mixed_ground_swamp_move_preview_matches_execution_with_pathfinder_variants() {
    let destination = hex(-1, 3);
    for (pathfinder, expected_fatigue) in [(false, 6), (true, 3)] {
        let mut argv = vec!["--seed", "1", "--terrain", "swamp"];
        if pathfinder {
            argv.push("--pathfinder");
        }
        let parsed = parse_args(argv.into_iter().map(String::from)).unwrap();
        assert_eq!(parsed.terrain, TerrainPreset::Swamp);
        assert_eq!(parsed.pathfinder, pathfinder);
        let mut state = launch_battle_for(parsed);
        assert_eq!(state.units[0].name, "P1");
        assert_eq!(state.units[0].pos, hex(-3, 3));
        let before_hash = state.state_hash();
        let before_rng = state.rng.clone();
        let before_ap = state.units[0].ap;
        let before_fatigue = state.units[0].fatigue;

        let preview = state.preview_move(0, destination).unwrap();
        assert_eq!(preview.path, vec![hex(-2, 3), destination]);
        assert_eq!(state.ground.get(preview.path[0]), Some(TerrainKind::Ground));
        assert_eq!(state.ground.get(preview.path[1]), Some(TerrainKind::Swamp));
        assert_eq!(preview.ap_cost, 6);
        assert_eq!(preview.fatigue_cost, expected_fatigue);
        assert_eq!(preview.ap_remaining, before_ap - 6);
        assert!(!preview.attack_budget_available);
        assert_eq!(state.state_hash(), before_hash, "preview is read-only");
        assert_eq!(state.rng, before_rng, "preview consumes no RNG");

        let events = state
            .apply(Command::Move {
                unit: 0,
                to: destination,
            })
            .unwrap();
        assert_eq!(
            events,
            vec![Event::Moved {
                unit: 0,
                path: preview.path.clone()
            }]
        );
        assert_eq!(state.units[0].pos, destination);
        assert_eq!(before_ap - state.units[0].ap, preview.ap_cost);
        assert_eq!(
            state.units[0].fatigue - before_fatigue,
            preview.fatigue_cost
        );
        assert_eq!(state.units[0].ap, preview.ap_remaining);
        assert_eq!(state.rng, before_rng, "unopposed move consumes no RNG");
        assert_swamp_layout(&state);
    }
}

fn replay_with_args(args: LaunchArgs) -> (Vec<Command>, Vec<Event>, BattleState, usize) {
    let mut state = launch_battle_for(args);
    let terrain = state.ground.clone();
    let elevations = state.elevations.clone();
    let mut commands = Vec::new();
    let mut events = Vec::new();

    while state.outcome().is_none() {
        assert!(
            commands.len() < MAX_COMMANDS,
            "AI replay exceeded {MAX_COMMANDS} commands"
        );
        let command = *ai::plan_turn(&state)
            .first()
            .expect("AI must provide a command while battle runs");
        let emitted = state
            .apply(command)
            .unwrap_or_else(|error| panic!("invalid AI command {command:?}: {error:?}"));
        commands.push(command);
        events.extend(emitted);
        assert_eq!(state.ground, terrain, "terrain changed during AI play");
        assert_eq!(
            state.elevations, elevations,
            "elevation changed during AI play"
        );
    }

    let command_count = commands.len();
    (commands, events, state, command_count)
}

fn replay(seed: u64, pathfinder: bool) -> (Vec<Command>, Vec<Event>, BattleState, usize) {
    replay_with_args(launch_args(seed, TerrainPreset::Swamp, pathfinder))
}

const CAPABILITY_OPTIONS: [&str; 9] = [
    "--adrenaline",
    "--rotation",
    "--footwork",
    "--fast-adaptation",
    "--dodge",
    "--nine-lives",
    "--berserk",
    "--pathfinder",
    "--indomitable",
];

fn smoke_line(args: &[String]) -> String {
    let output = Process::new(env!("CARGO_BIN_EXE_headless_smoke"))
        .args(args)
        .output()
        .expect("run headless_smoke");
    assert!(
        output.status.success(),
        "arguments {args:?}: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    String::from_utf8(output.stdout)
        .expect("headless_smoke writes UTF-8")
        .trim()
        .to_owned()
}

#[test]
fn parsed_swamp_smoke_runs_match_in_process_replays_for_launch_capabilities() {
    for (capability_mask, selected) in [
        (0u16, &[][..]),
        (1 << 7, &["--pathfinder"][..]),
        (0b1_1111_1111, &CAPABILITY_OPTIONS[..]),
    ] {
        let mut argv = vec![
            "--seed".to_owned(),
            "1".to_owned(),
            "--terrain".to_owned(),
            "swamp".to_owned(),
        ];
        argv.extend(selected.iter().map(|flag| (*flag).to_owned()));
        let args = parse_args(argv.clone()).expect("parse Swamp launch arguments");
        assert_eq!((args.seed, args.terrain), (1, TerrainPreset::Swamp));

        let initial = launch_battle_for(args);
        assert_loadout(&initial, capability_mask);
        let (_, _, replayed, _) = replay_with_args(args);
        let measured = format!(
            "OUTCOME={:?} REASON={:?} ROUNDS={} HASH={:016x}",
            replayed.outcome().unwrap(),
            replayed.end_reason().unwrap(),
            replayed.round,
            replayed.state_hash()
        );
        assert_eq!(smoke_line(&argv), measured, "capabilities {capability_mask:#011b}");
    }
}

#[test]
fn swamp_ai_replays_are_deterministic_valid_bounded_and_terrain_stable() {
    for seed in [1, 7, 23] {
        for pathfinder in [false, true] {
            let (commands_a, events_a, state_a, count_a) = replay(seed, pathfinder);
            let (commands_b, events_b, state_b, count_b) = replay(seed, pathfinder);
            assert_eq!(
                commands_a, commands_b,
                "commands, seed={seed}, Pathfinder={pathfinder}"
            );
            assert_eq!(
                events_a, events_b,
                "events, seed={seed}, Pathfinder={pathfinder}"
            );
            assert_eq!(count_a, count_b);
            assert!(count_a <= MAX_COMMANDS);
            assert!(state_a.outcome().is_some());
            assert!(matches!(events_a.last(), Some(Event::BattleEnded { .. })));
            assert_eq!(state_a.state_hash(), state_b.state_hash());
            assert_eq!(state_a.rng, state_b.rng);
            assert_eq!(state_a.outcome(), state_b.outcome());
            assert_eq!(state_a.end_reason(), state_b.end_reason());
            assert_eq!(state_a.round, state_b.round);
            assert_swamp_layout(&state_a);
        }
    }
}
