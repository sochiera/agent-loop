//! T59 `--two-hex` launch loadout (APPROXIMATION): a player-only launch assignment of the
//! accepted `MeleeReach::TwoHex` rule (partial UC-COMBAT-051, catalog-stated, not
//! runtime-confirmed). It is not a polearm, weapon or equipment system; reach rules,
//! costs, hashing and AI policy are the accepted T58 ones.

use bb_rules::hex::{hex, map_hexes};
use bb_rules::scenario::{
    LaunchArgs, TerrainPreset, adrenaline_battle, battle, footwork_battle, launch_battle, launch_battle_for, launch_battle_with, parse_args,
    rotation_battle,
};
use bb_rules::*;

const TERRAINS: [TerrainPreset; 3] = [TerrainPreset::Flat, TerrainPreset::Hills, TerrainPreset::Swamp];
const MAX_REPLAY_COMMANDS: usize = 10_000;

const FLAGS: [&str; 9] = [
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

fn parse(argv: &[&str]) -> LaunchArgs {
    parse_args(argv.iter().map(|s| s.to_string())).expect("valid arguments")
}

fn terrain_arg(terrain: TerrainPreset) -> &'static str {
    match terrain {
        TerrainPreset::Flat => "flat",
        TerrainPreset::Hills => "hills",
        TerrainPreset::Swamp => "swamp",
    }
}

/// Launch arguments with the nine capability flags chosen by `bits` (bit i = `FLAGS[i]`).
fn subset(seed: u64, terrain: TerrainPreset, bits: u16, two_hex: bool) -> LaunchArgs {
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
        two_hex,
        stunning_blow: false, killing_frenzy: false,
    }
}

fn two_hex_only(seed: u64, terrain: TerrainPreset) -> LaunchArgs {
    subset(seed, terrain, 0, true)
}

fn combined(seed: u64, terrain: TerrainPreset) -> LaunchArgs {
    subset(seed, terrain, 0x1FF, true)
}

#[test]
fn parsing_defaults_to_false_and_accepts_the_value_less_flag_with_repeats_and_any_order() {
    assert!(!parse(&[]).two_hex);
    assert!(!parse(&["--seed", "7", "--terrain", "swamp", "--dodge"]).two_hex);
    assert_eq!(parse(&["--two-hex"]), LaunchArgs { two_hex: true, ..parse(&[]) });
    assert_eq!(parse(&["--two-hex", "--two-hex", "--two-hex"]), parse(&["--two-hex"]));
    assert_eq!(parse(&["--two-hex", "--seed", "9"]), parse(&["--seed", "9", "--two-hex"]));
    assert_eq!(parse(&["--seed", "9", "--two-hex", "--terrain", "hills", "--two-hex"]), parse(&["--terrain", "hills", "--seed", "9", "--two-hex"]));
    // The flag takes no value: the next option is still parsed as itself.
    let args = parse(&["--two-hex", "--seed", "5", "--two-hex", "--terrain", "swamp"]);
    assert_eq!((args.seed, args.terrain, args.two_hex), (5, TerrainPreset::Swamp, true));
    // Near-miss spellings are unknown arguments, which parse_args ignores.
    for near in ["--two_hex", "--twohex", "--two-hex=1", "two-hex"] {
        assert!(!parse(&[near]).two_hex, "{near}");
    }
    assert!(parse_args(["--two-hex", "--seed"].map(String::from)).is_err(), "other errors are unchanged");
}

#[test]
fn flag_composes_independently_with_seed_terrain_each_capability_and_the_combined_loadout() {
    for terrain in TERRAINS {
        for seed in [1u64, 42, u64::MAX] {
            let base = ["--seed", &seed.to_string(), "--terrain", terrain_arg(terrain)];
            let mut argv: Vec<&str> = base.to_vec();
            argv.push("--two-hex");
            assert_eq!(parse(&argv), two_hex_only(seed, terrain));
        }
        for (i, flag) in FLAGS.iter().enumerate() {
            let with = parse(&["--terrain", terrain_arg(terrain), flag, "--two-hex"]);
            assert_eq!(with, subset(1, terrain, 1 << i, true), "{flag}");
            let without = parse(&["--terrain", terrain_arg(terrain), flag]);
            assert_eq!(without, subset(1, terrain, 1 << i, false), "{flag}");
        }
        let mut argv = vec!["--two-hex", "--terrain", terrain_arg(terrain), "--seed", "1"];
        argv.extend(FLAGS);
        assert_eq!(parse(&argv), combined(1, terrain));
        // All 512 capability subsets are independent of the new flag.
        for bits in 0..512u16 {
            let mut argv = vec!["--two-hex"];
            for (i, flag) in FLAGS.iter().enumerate() {
                if bits >> i & 1 != 0 {
                    argv.push(flag);
                }
            }
            assert_eq!(parse(&argv), subset(1, TerrainPreset::Flat, bits, true), "bits {bits}");
        }
    }
}

fn reaches(state: &BattleState) -> Vec<(Side, MeleeReach)> {
    state.units.iter().map(|u| (u.side, u.melee_reach)).collect()
}

#[test]
fn assignment_is_player_only_and_changes_no_stats_resources_positions_or_rng() {
    for terrain in TERRAINS {
        for seed in [1u64, 2, 99] {
            let base = battle(seed, terrain);
            for args in [two_hex_only(seed, terrain), combined(seed, terrain)] {
                let state = launch_battle_for(args);
                for u in &state.units {
                    let want = if u.side == Side::Player { MeleeReach::TwoHex } else { MeleeReach::Adjacent };
                    assert_eq!(u.melee_reach, want, "{} {terrain:?}", u.name);
                }
                assert_eq!(state.rng, base.rng, "the initial RNG is untouched");
                assert_eq!(state.round, base.round);
                assert_eq!(state.queue, base.queue);
                assert_eq!(state.elevations, base.elevations);
                assert_eq!(state.ground, base.ground);
                // The reach is the only difference from the same capabilities without it.
                let mut without = launch_battle_for(LaunchArgs { two_hex: false, ..args });
                for (a, b) in state.units.iter().zip(&without.units) {
                    assert_eq!((a.stats.clone(), a.pos, a.hp, a.armour, a.ap, a.fatigue, a.morale), (b.stats.clone(), b.pos, b.hp, b.armour, b.ap, b.fatigue, b.morale));
                }
                for u in without.units.iter_mut().filter(|u| u.side == Side::Player) {
                    u.melee_reach = MeleeReach::TwoHex;
                }
                assert_eq!(without.units, state.units);
                assert_eq!(without.state_hash(), state.state_hash());
                assert_eq!(state.outcome, None);
            }
        }
    }
}

#[test]
fn the_flag_alone_differs_from_the_default_only_by_the_reach_hash_bit_and_compat_constructors_stay_adjacent() {
    for terrain in TERRAINS {
        let base = battle(1, terrain);
        assert_eq!(launch_battle_for(subset(1, terrain, 0, false)).state_hash(), base.state_hash(), "default pin");
        assert_eq!(launch_battle_for(subset(1, terrain, 0, false)).units, base.units);
        let two = launch_battle_for(two_hex_only(1, terrain));
        assert_ne!(two.state_hash(), base.state_hash());
        let mut reset = two.clone();
        for u in &mut reset.units {
            u.melee_reach = MeleeReach::Adjacent;
        }
        assert_eq!(reset.state_hash(), base.state_hash(), "TwoHex is the only difference");
        // Compatibility constructors supply false.
        for state in [
            launch_battle(1, terrain, false),
            launch_battle(1, terrain, true),
            launch_battle_with(1, terrain, true, true),
            adrenaline_battle(1, terrain),
            rotation_battle(1, terrain),
            footwork_battle(1, terrain),
            battle(1, terrain),
        ] {
            assert!(state.units.iter().all(|u| u.melee_reach == MeleeReach::Adjacent), "{terrain:?}");
        }
    }
    assert_eq!(parse(&[]), LaunchArgs { seed: 1, terrain: TerrainPreset::Flat, ..subset(1, TerrainPreset::Flat, 0, false) });
    assert!(reaches(&battle(1, TerrainPreset::Flat)).iter().all(|&(_, r)| r == MeleeReach::Adjacent));
}

type Run = (Vec<Command>, Vec<Event>, BattleState);

fn replay(args: LaunchArgs) -> Run {
    let mut state = launch_battle_for(args);
    let (mut commands, mut events) = (Vec::new(), Vec::new());
    while state.outcome().is_none() {
        let plan = ai::plan_turn(&state);
        assert_eq!(plan.len(), 1, "{args:?}: one AI command per plan");
        let command = plan[0];
        commands.push(command);
        events.extend(state.apply(command).unwrap_or_else(|e| panic!("{args:?}: {e:?} from {command:?}")));
        assert!(commands.len() <= MAX_REPLAY_COMMANDS, "{args:?} exceeded the replay bound");
    }
    (commands, events, state)
}

#[test]
fn complete_ai_replays_are_repeatable_valid_and_bounded_on_every_preset() {
    for terrain in TERRAINS {
        for args in [two_hex_only(1, terrain), combined(1, terrain)] {
            let (c1, e1, s1) = replay(args);
            let (c2, e2, s2) = replay(args);
            assert_eq!(c1, c2, "{args:?}");
            assert_eq!(e1, e2, "{args:?}");
            assert_eq!(s1.state_hash(), s2.state_hash());
            assert_eq!(s1.rng, s2.rng);
            assert_eq!((s1.outcome, s1.end_reason, s1.round), (s2.outcome, s2.end_reason, s2.round));
            assert_eq!(s1.units, s2.units);
            assert!(s1.outcome().is_some() && !c1.is_empty());
        }
    }
}

/// Current post-T60 measured seed-1 headless results of `headless_smoke` for each
/// TwoHex configuration: (outcome, end reason, rounds, state hash). The binary
/// prints exactly these. Non-TwoHex replay pins live in their existing tests.
const MEASURED: [(TerrainPreset, bool, (Outcome, EndReason, u32, u64)); 6] = [
    (TerrainPreset::Flat, false, (Outcome::Victory, EndReason::Escape, 2, 0x2202abb4c514f685)),
    (TerrainPreset::Flat, true, (Outcome::Victory, EndReason::Escape, 2, 0x4fd6ba9dd1767299)),
    (TerrainPreset::Hills, false, (Outcome::Victory, EndReason::Escape, 3, 0xd523f58695a2ad96)),
    (TerrainPreset::Hills, true, (Outcome::Victory, EndReason::Elimination, 3, 0xec982429bed34670)),
    (TerrainPreset::Swamp, false, (Outcome::Victory, EndReason::Escape, 4, 0x1b928b9fc5eb363e)),
    (TerrainPreset::Swamp, true, (Outcome::Victory, EndReason::Elimination, 4, 0xdec7c86aa2062269)),
];

#[test]
fn in_process_replays_match_the_measured_headless_binary_results() {
    for (terrain, all, want) in MEASURED {
        let args = if all { combined(1, terrain) } else { two_hex_only(1, terrain) };
        let (_, _, state) = replay(args);
        let got = (state.outcome().unwrap(), state.end_reason().unwrap(), state.round, state.state_hash());
        assert_eq!(got, want, "{terrain:?} combined={all}");
    }
}

/// Run the real `headless_smoke` binary and compare its printed result with the replay.
#[test]
fn headless_binary_output_equals_the_in_process_replay() {
    let exe = env!("CARGO_BIN_EXE_headless_smoke");
    for terrain in TERRAINS {
        for all in [false, true] {
            let mut cmd = std::process::Command::new(exe);
            cmd.args(["--seed", "1", "--terrain", terrain_arg(terrain), "--two-hex"]);
            if all {
                cmd.args(FLAGS);
            }
            let out = cmd.output().expect("headless_smoke runs");
            assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
            let (_, _, state) = replay(if all { combined(1, terrain) } else { two_hex_only(1, terrain) });
            let want = format!(
                "OUTCOME={:?} REASON={:?} ROUNDS={} HASH={:016x}\n",
                state.outcome().unwrap(),
                state.end_reason().unwrap(),
                state.round,
                state.state_hash()
            );
            assert_eq!(String::from_utf8_lossy(&out.stdout), want, "{terrain:?} combined={all}");
        }
    }
}

const P1: u32 = 0;
const P2: u32 = 1;
const E1: u32 = 3;
const PARK: [Hex; 6] = [hex(-4, 2), hex(-4, 3), hex(-4, 4), hex(4, -4), hex(4, -3), hex(4, -2)];

/// P1 active at (-1,-2), P2 ally between at (0,-2) and E1 at (1,-2): E1 is at distance 2 from
/// P1 behind an intervening ally. All other units are parked far away.
fn behind_ally(args: LaunchArgs) -> BattleState {
    let mut state = launch_battle_for(args);
    for (unit, spot) in state.units.iter_mut().zip(PARK) {
        unit.pos = spot;
    }
    state.units[P1 as usize].pos = hex(-1, -2);
    state.units[P2 as usize].pos = hex(0, -2);
    state.units[E1 as usize].pos = hex(1, -2);
    state.queue.retain(|&id| id != P1);
    state.queue.insert(0, P1);
    state.units[P1 as usize].ap = state.units[P1 as usize].stats.ap_max;
    state.units[P1 as usize].turn_started = true;
    let spots: Vec<_> = state.units.iter().map(|u| u.pos).collect();
    for (i, spot) in spots.iter().enumerate() {
        assert!(map_hexes().contains(spot) && !spot.is_obstacle() && !spots[..i].contains(spot), "{spot:?}");
    }
    state
}

#[test]
fn scripted_encounter_executes_a_distance_two_attack_that_an_adjacent_twin_cannot() {
    for terrain in TERRAINS {
        let mut state = behind_ally(two_hex_only(1, terrain));
        assert_eq!(state.units[P1 as usize].pos.distance(state.units[E1 as usize].pos), 2);
        assert_eq!(state.legal_targets(P1), vec![E1]);
        let preview = state.preview_attack(P1, E1).expect("preview at distance two");
        // The AI picks the same attack without moving first.
        let plan = ai::plan_turn(&state);
        assert_eq!(plan, vec![Command::Attack { attacker: P1, target: E1 }], "{terrain:?}");
        let before_pos = state.units[P1 as usize].pos;
        let events = state.apply(plan[0]).expect("distance-two attack applies");
        assert!(events.iter().any(|e| matches!(e, Event::Attacked { attacker: P1, target: E1, .. })));
        assert!(!events.iter().any(|e| matches!(e, Event::Moved { .. })), "no movement first");
        assert_eq!(state.units[P1 as usize].pos, before_pos);
        assert_eq!(state.units[P1 as usize].ap, state.units[P1 as usize].stats.ap_max - preview.ap_cost);

        // Adjacent-only twin (no --two-hex): the same geometry offers no attack.
        let mut twin = behind_ally(subset(1, terrain, 0, false));
        assert!(twin.legal_targets(P1).is_empty());
        assert!(twin.preview_attack(P1, E1).is_err());
        let before = twin.state_hash();
        assert!(twin.apply(Command::Attack { attacker: P1, target: E1 }).is_err());
        assert_eq!(twin.state_hash(), before, "a rejected attack changes nothing");
        assert!(!matches!(ai::plan_turn(&twin)[0], Command::Attack { .. }));

        // Distance three remains outside reach, with or without the flag.
        let mut far = behind_ally(two_hex_only(1, terrain));
        far.units[E1 as usize].pos = hex(2, -2);
        assert_eq!(far.units[P1 as usize].pos.distance(far.units[E1 as usize].pos), 3);
        assert!(far.legal_targets(P1).is_empty());
        assert!(far.preview_attack(P1, E1).is_err());
        assert!(far.apply(Command::Attack { attacker: P1, target: E1 }).is_err());
    }
}

#[test]
fn enemies_keep_adjacent_reach_against_two_hex_players() {
    let mut state = behind_ally(two_hex_only(1, TerrainPreset::Flat));
    state.queue.retain(|&id| id != E1);
    state.queue.insert(0, E1);
    state.units[E1 as usize].ap = state.units[E1 as usize].stats.ap_max;
    state.units[E1 as usize].turn_started = true;
    state.units[P2 as usize].pos = hex(-1, -3);
    // E1 at (1,-2) vs P1 at (-1,-2): distance two, and enemies are Adjacent-only.
    assert!(!state.legal_targets(E1).contains(&P1));
}
