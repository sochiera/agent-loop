//! Launch assignment and end-to-end coverage for T68 Killing Frenzy.
//! The temporary buff and its numbers are T67 APPROXIMATION; launch assignment is an additional
//! APPROXIMATION, not a perk or equipment system.

use bb_rules::hex::{Hex, hex};
use bb_rules::rules::ATTACK_AP;
use bb_rules::scenario::{self, LaunchArgs, TerrainPreset, launch_battle_for, parse_args};
use bb_rules::{BattleState, Command, EndReason, Event, Outcome, Rng, Side, ai};
use std::process::Command as Process;

const PLAYER_PARK: [Hex; 3] = [hex(-4, 2), hex(-4, 3), hex(-4, 4)];
const ENEMY_PARK: [Hex; 3] = [hex(4, -4), hex(4, -3), hex(4, -2)];
const ACTOR_AT: Hex = hex(0, -2);
const FIRST_VICTIM_AT: Hex = hex(1, -2);
const FOLLOWUP_VICTIM_AT: Hex = hex(0, -1);

fn parsed(argv: &[&str]) -> LaunchArgs {
    parse_args(argv.iter().map(|s| (*s).to_owned())).expect("valid launch arguments")
}

#[test]
fn parser_defaults_repeats_order_terrains_and_each_existing_option_compose() {
    let defaults = parsed(&[]);
    assert!(!defaults.killing_frenzy);
    assert_eq!(defaults, LaunchArgs { seed: 1, terrain: TerrainPreset::Flat, ..defaults });

    let existing = [
        ("--adrenaline", 0),
        ("--rotation", 1),
        ("--footwork", 2),
        ("--fast-adaptation", 3),
        ("--dodge", 4),
        ("--nine-lives", 5),
        ("--berserk", 6),
        ("--pathfinder", 7),
        ("--indomitable", 8),
        ("--two-hex", 9),
        ("--stunning-blow", 10),
    ];
    for terrain in [TerrainPreset::Flat, TerrainPreset::Hills, TerrainPreset::Swamp] {
        let name = match terrain { TerrainPreset::Flat => "flat", TerrainPreset::Hills => "hills", TerrainPreset::Swamp => "swamp" };
        for (flag, index) in existing {
            let args = parsed(&["--killing-frenzy", "--killing-frenzy", "--seed", "17", flag, "--terrain", name]);
            assert_eq!((args.seed, args.terrain, args.killing_frenzy), (17, terrain, true), "{flag} {name}");
            let mut expected = defaults;
            expected.seed = 17;
            expected.terrain = terrain;
            expected.killing_frenzy = true;
            match index {
                0 => expected.adrenaline = true,
                1 => expected.rotation = true,
                2 => expected.footwork = true,
                3 => expected.fast_adaptation = true,
                4 => expected.dodge = true,
                5 => expected.nine_lives = true,
                6 => expected.berserk = true,
                7 => expected.pathfinder = true,
                8 => expected.indomitable = true,
                9 => expected.two_hex = true,
                _ => expected.stunning_blow = true,
            }
            assert_eq!(args, expected, "{flag} {name}");
        }
    }

    let all = parsed(&[
        "--killing-frenzy", "--seed", "9", "--terrain", "swamp", "--adrenaline", "--rotation", "--footwork",
        "--fast-adaptation", "--dodge", "--nine-lives", "--berserk", "--pathfinder", "--indomitable",
        "--two-hex", "--stunning-blow", "--killing-frenzy",
    ]);
    assert_eq!(all, LaunchArgs {
        seed: 9, terrain: TerrainPreset::Swamp, adrenaline: true, rotation: true, footwork: true,
        fast_adaptation: true, dodge: true, nine_lives: true, berserk: true, pathfinder: true,
        indomitable: true, two_hex: true, stunning_blow: true, killing_frenzy: true,
    });
    assert_eq!(parsed(&["--terrain", "hills", "--seed", "3", "--killing-frenzy", "--seed", "4"]),
        LaunchArgs { seed: 4, terrain: TerrainPreset::Hills, killing_frenzy: true, ..defaults });
    assert_eq!(parse_args(["--killing-frenzy", "--seed", "bad"].map(String::from).into_iter()), Err("--seed needs a u64".into()));
    assert_eq!(parse_args(["--killing-frenzy", "--terrain", "bog"].map(String::from).into_iter()), Err("--terrain needs flat, hills or swamp".into()));
}

#[test]
fn launch_assignment_changes_only_player_capability_and_keeps_counters_zero() {
    let mut args = parsed(&["--killing-frenzy"]);
    for terrain in [TerrainPreset::Flat, TerrainPreset::Hills, TerrainPreset::Swamp] {
        args.terrain = terrain;
        args.seed = 31;
        let base = scenario::battle(args.seed, terrain);
        let mut selected = launch_battle_for(args);
        assert_eq!(selected.rng, base.rng);
        assert_eq!(selected.queue, base.queue);
        assert_eq!(selected.round, base.round);
        assert_eq!(selected.elevations, base.elevations);
        assert_eq!(selected.ground, base.ground);
        for (unit, baseline) in selected.units.iter().zip(&base.units) {
            assert_eq!(unit.has_killing_frenzy, unit.side == Side::Player, "{}", unit.name);
            assert_eq!(unit.killing_frenzy_turns, 0, "{}", unit.name);
            let mut comparable = unit.clone();
            comparable.has_killing_frenzy = false;
            assert_eq!(&comparable, baseline, "no other unit field changes for {}", unit.name);
        }
        for unit in selected.units.iter_mut().filter(|u| u.side == Side::Player) {
            unit.has_killing_frenzy = false;
        }
        assert_eq!(selected.units, base.units);
    }
}

fn encounter(args: LaunchArgs) -> BattleState {
    let mut state = launch_battle_for(args);
    for (unit, at) in state.units.iter_mut().zip(PLAYER_PARK.into_iter().chain(ENEMY_PARK)) {
        unit.pos = at;
    }
    state.units[0].pos = ACTOR_AT;
    state.units[3].pos = FIRST_VICTIM_AT;
    state.units[4].pos = FOLLOWUP_VICTIM_AT;
    state.units[3].hp = 1;
    state.units[3].armour = 0;
    state.units[4].armour = 0;
    state.units[0].ap = ATTACK_AP;
    state.units[0].fatigue = 0;
    state.units[0].turn_started = true;
    state.queue.retain(|&id| id != 0);
    state.queue.insert(0, 0);
    assert!(state.units[0].has_killing_frenzy, "capability comes from parsed LaunchArgs");
    assert_eq!(state.units[0].killing_frenzy_turns, 0, "the encounter does not grant an effect");
    state
}

fn rng_for_hit(state: &BattleState, command: Command, required: impl Fn(&[Event]) -> bool) -> Rng {
    (1..100_000).map(Rng::new).find(|rng| {
        let mut probe = state.clone();
        probe.rng = rng.clone();
        probe.apply(command).is_ok_and(|events| required(&events))
    }).expect("a deterministic RNG state satisfies the scripted encounter")
}

#[test]
fn parsed_loadout_kill_activates_and_berserk_pays_for_a_boosted_followup() {
    let args = parsed(&["--seed", "13", "--terrain", "hills", "--killing-frenzy", "--berserk"]);
    let mut state = encounter(args);
    let kill = Command::Attack { attacker: 0, target: 3 };
    state.rng = rng_for_hit(&state, kill, |events| {
        events.iter().any(|e| matches!(e, Event::Attacked { attacker: 0, target: 3, hit: true, .. }))
            && events.iter().any(|e| matches!(e, Event::Died { unit: 3 }))
            && events.iter().any(|e| matches!(e, Event::BerserkTriggered { unit: 0, ap_gained: 4 }))
    });
    let events = state.apply(kill).unwrap();
    assert!(events.iter().any(|e| matches!(e, Event::Died { unit: 3 })));
    assert!(events.iter().any(|e| matches!(e, Event::BerserkTriggered { unit: 0, ap_gained: 4 })));
    assert_eq!(state.units[0].ap, ATTACK_AP, "the AP refund makes exactly one follow-up affordable");
    assert_eq!(state.units[0].killing_frenzy_turns, 2);
    assert!(state.units[0].is_killing_frenzy_active());

    let preview = state.preview_attack(0, 4).unwrap();
    assert_eq!((preview.damage_min, preview.damage_max), (25, 43));
    let followup = Command::Attack { attacker: 0, target: 4 };
    state.rng = rng_for_hit(&state, followup, |events| events.iter().any(|e| matches!(e,
        Event::Attacked { attacker: 0, target: 4, hit: true, .. })));
    let events = state.apply(followup).unwrap();
    let damage = events.iter().find_map(|e| match e { Event::Attacked { dmg, .. } => Some(*dmg), _ => None }).unwrap();
    assert!((preview.damage_min..=preview.damage_max).contains(&damage));
    assert_eq!(state.units[0].ap, 0);
}

#[test]
fn ai_paid_kill_uses_the_launch_passive_without_a_frenzy_policy() {
    let mut state = encounter(parsed(&["--seed", "7", "--killing-frenzy"]));
    state.rng = rng_for_hit(&state, Command::Attack { attacker: 0, target: 3 }, |events| events.iter().any(|e|
        matches!(e, Event::Attacked { attacker: 0, target: 3, hit: true, .. }) && events.iter().any(|e| matches!(e, Event::Died { unit: 3 }))));
    let command = ai::plan_turn(&state).into_iter().next().expect("AI produces the paid melee attack");
    assert_eq!(command, Command::Attack { attacker: 0, target: 3 });
    let events = state.apply(command).unwrap();
    assert!(events.iter().any(|e| matches!(e, Event::Died { unit: 3 })));
    assert_eq!(state.units[0].killing_frenzy_turns, 2);
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct ReplayResult {
    outcome: Outcome,
    reason: EndReason,
    rounds: u32,
    hash: u64,
    commands: u32,
    command_sequence: Vec<Command>,
    event_sequence: Vec<Event>,
}

fn replay(args: LaunchArgs) -> ReplayResult {
    let mut state = launch_battle_for(args);
    let mut commands = 0;
    let mut command_sequence = Vec::new();
    let mut event_sequence = Vec::new();
    while state.outcome().is_none() {
        let command = ai::plan_turn(&state).into_iter().next().expect("running battle has an AI action");
        event_sequence.extend(state.apply(command).expect("AI command is valid"));
        command_sequence.push(command);
        commands += 1;
        assert!(commands <= 10_000, "bounded deterministic replay");
    }
    ReplayResult {
        outcome: state.outcome().unwrap(), reason: state.end_reason().unwrap(), rounds: state.round,
        hash: state.state_hash(), commands, command_sequence, event_sequence,
    }
}

type ReplayPin = (Outcome, EndReason, u32, u64);

fn seed_one_pins(terrain: TerrainPreset) -> (ReplayPin, ReplayPin) {
    match terrain {
        TerrainPreset::Flat => (
            (Outcome::Victory, EndReason::Escape, 3, 0xfff5_a4e0_df8d_d8cc),
            (Outcome::Victory, EndReason::Elimination, 3, 0x6194_5464_1aa3_6990),
        ),
        TerrainPreset::Hills => (
            (Outcome::Victory, EndReason::Escape, 4, 0x7754_1ad9_bf3e_9fe7),
            (Outcome::Victory, EndReason::Escape, 5, 0x40f4_469b_553e_7548),
        ),
        TerrainPreset::Swamp => (
            (Outcome::Victory, EndReason::Elimination, 4, 0x8d53_9a24_9246_8f5a),
            (Outcome::Victory, EndReason::Escape, 5, 0x85d8_8af3_d338_0288),
        ),
    }
}

#[test]
fn bounded_seed_one_replays_repeat_for_frenzy_and_all_options_on_every_terrain() {
    let all = parsed(&[
        "--adrenaline", "--rotation", "--footwork", "--fast-adaptation", "--dodge", "--nine-lives", "--berserk",
        "--pathfinder", "--indomitable", "--two-hex", "--stunning-blow", "--killing-frenzy",
    ]);
    for terrain in [TerrainPreset::Flat, TerrainPreset::Hills, TerrainPreset::Swamp] {
        let (frenzy_only, all_options) = seed_one_pins(terrain);
        for (args, expected) in [
            (LaunchArgs { seed: 1, terrain, ..parsed(&["--killing-frenzy"]) }, frenzy_only),
            (LaunchArgs { seed: 1, terrain, ..all }, all_options),
        ] {
            let first = replay(args);
            let second = replay(args);
            assert_eq!(first, second, "{terrain:?} {args:?}");
            assert_eq!((first.outcome, first.reason, first.rounds, first.hash), expected, "headless_smoke pin {terrain:?} {args:?}");
        }
    }
}

#[test]
fn headless_smoke_binary_matches_seed_one_frenzy_replays_on_every_terrain() {
    const ALL_OPTIONS: [&str; 12] = [
        "--adrenaline", "--rotation", "--footwork", "--fast-adaptation", "--dodge", "--nine-lives",
        "--berserk", "--pathfinder", "--indomitable", "--two-hex", "--stunning-blow", "--killing-frenzy",
    ];
    let exe = env!("CARGO_BIN_EXE_headless_smoke");
    for (terrain, terrain_arg) in [
        (TerrainPreset::Flat, "flat"), (TerrainPreset::Hills, "hills"), (TerrainPreset::Swamp, "swamp"),
    ] {
        let (frenzy_only, all_options) = seed_one_pins(terrain);
        for (flags, expected) in [(&["--killing-frenzy"][..], frenzy_only), (&ALL_OPTIONS[..], all_options)] {
            let mut argv = vec!["--seed".to_owned(), "1".to_owned(), "--terrain".to_owned(), terrain_arg.to_owned()];
            argv.extend(flags.iter().map(|flag| (*flag).to_owned()));
            let in_process = replay(parse_args(argv).expect("binary arguments parse through the shared parser"));
            let replay_line = format!("OUTCOME={:?} REASON={:?} ROUNDS={} HASH={:016x}",
                in_process.outcome, in_process.reason, in_process.rounds, in_process.hash);
            assert_eq!((in_process.outcome, in_process.reason, in_process.rounds, in_process.hash), expected,
                "in-process replay pin for {terrain_arg} {flags:?}");

            let output = Process::new(exe)
                .args(["--seed", "1", "--terrain", terrain_arg])
                .args(flags)
                .output()
                .expect("headless_smoke binary starts");
            assert!(output.status.success(), "headless_smoke failed for {terrain_arg} {flags:?}: {}",
                String::from_utf8_lossy(&output.stderr));
            let stdout = String::from_utf8(output.stdout).expect("headless_smoke stdout is UTF-8");
            assert_eq!(stdout.trim(), replay_line, "actual headless_smoke output for {terrain_arg} {flags:?}");
        }
    }
}
