use tactics_core::ai;
use tactics_core::battle::{HILL_ATTACK_BONUS, MAX_HIT_CHANCE, MIN_HIT_CHANCE};
use tactics_core::scenario;
use tactics_core::*;

fn at(col: i32, row: i32) -> Hex {
    Hex::from_offset(col, row)
}

fn open_map(w: usize, h: usize) -> Map {
    let row = ".".repeat(w);
    let rows: Vec<&str> = (0..h).map(|_| row.as_str()).collect();
    Map::from_ascii(&rows)
}

/// Units listed as (team, archetype, pos); ids are list order; unit 0 acts first
/// only if it has the highest initiative, so tests that need that say so.
fn battle_with(map: Map, spec: &[(Team, Archetype, Hex)], seed: u64) -> Battle {
    let units = spec
        .iter()
        .enumerate()
        .map(|(i, (t, a, p))| Unit::new(i, &format!("u{i}"), *t, *a, *p))
        .collect();
    Battle::new(map, units, seed)
}

// ---------------------------------------------------------------- pathing

#[test]
fn path_prefers_cheaper_route_over_fewer_hexes() {
    // Straight east along row 0 through two forest hexes costs 2 + 4 + 4 + 2 = 12;
    // the five-hex detour over plain costs 10, so the search must take it.
    let map = Map::from_ascii(&["..ff..", "......"]);
    let b = battle_with(
        map,
        &[(Team::Player, Archetype::Swordsman, at(0, 0)), (Team::Enemy, Archetype::Raider, at(5, 1))],
        1,
    );
    let reach = b.search(at(0, 0), 100);
    let path = reach.path_to(b.map(), at(4, 0)).unwrap();
    let cost = reach.cost(b.map(), at(4, 0)).unwrap();
    assert_eq!(path.first(), Some(&at(0, 0)));
    assert_eq!(path.last(), Some(&at(4, 0)));
    assert_eq!(cost, 10);
    assert_eq!(path.len(), 6);
    let step_sum: i32 = path[1..].iter().map(|h| b.map().terrain(*h).unwrap().move_cost().unwrap()).sum();
    assert_eq!(step_sum, cost, "reported cost equals the sum of terrain costs on the path");
    // One plain step then the forest hex: 2 + 4.
    assert_eq!(b.move_path(0, at(2, 0)).unwrap().1, 6);
}

#[test]
fn hill_and_forest_cost_more_than_plain() {
    let map = Map::from_ascii(&[".fh."]);
    let b = battle_with(
        map,
        &[(Team::Player, Archetype::Swordsman, at(0, 0)), (Team::Enemy, Archetype::Raider, at(3, 0))],
        1,
    );
    assert_eq!(b.move_path(0, at(1, 0)).unwrap().1, 4);
    let plain_cost = 2;
    let hill = Terrain::Hill.move_cost().unwrap();
    assert!(hill > plain_cost);
    assert!(Terrain::Forest.move_cost().unwrap() > plain_cost);
}

#[test]
fn pathing_avoids_impassable_and_occupied_hexes() {
    // A wall of rock and water with a single gap that is occupied by a unit, plus a long way round.
    let map = Map::from_ascii(&[
        "..#..", //
        "..~..", //
        "..#..", //
        ".....",
    ]);
    let b = battle_with(
        map,
        &[(Team::Player, Archetype::Swordsman, at(0, 1)), (Team::Enemy, Archetype::Raider, at(1, 1))],
        1,
    );
    // Cannot move onto the occupied hex or the impassable ones.
    assert_eq!(b.move_path(0, at(1, 1)), Err(ActionError::Occupied));
    assert_eq!(b.move_path(0, at(2, 0)), Err(ActionError::Impassable));
    assert_eq!(b.move_path(0, at(2, 1)), Err(ActionError::Impassable));
    // Path to the far side never crosses a blocked hex or a unit.
    let reach = b.search(at(0, 1), 100);
    let path = reach.path_to(b.map(), at(4, 1)).expect("far side reachable by the south route");
    for h in &path[1..] {
        assert!(b.map().terrain(*h).unwrap().passable());
        assert!(b.unit_at(*h).is_none());
    }
}

#[test]
fn enclosed_hex_has_no_path() {
    // A plain pocket whose six neighbours are all rock or water.
    let mut map = Map::new(7, 7, Terrain::Plain);
    let pocket = at(3, 3);
    for (i, n) in pocket.neighbors().into_iter().enumerate() {
        map.set(n, if i % 2 == 0 { Terrain::Rock } else { Terrain::Water });
    }
    let b = battle_with(
        map,
        &[(Team::Player, Archetype::Swordsman, at(0, 0)), (Team::Enemy, Archetype::Raider, at(6, 6))],
        1,
    );
    assert_eq!(b.map().terrain(pocket), Some(Terrain::Plain));
    assert_eq!(b.move_path(0, pocket), Err(ActionError::NoPath));
    assert!(b.search(at(0, 0), 100).cost(b.map(), pocket).is_none());
}

#[test]
fn reachable_set_is_limited_by_ap() {
    let b = battle_with(
        open_map(12, 5),
        &[(Team::Player, Archetype::Swordsman, at(0, 2)), (Team::Enemy, Archetype::Raider, at(11, 2))],
        1,
    );
    let reach = b.reachable(0);
    // 9 AP on open plain (2 per hex) = 4 hexes at most.
    assert!(!reach.is_empty());
    for (h, cost) in &reach {
        assert!(*cost <= AP_PER_TURN, "{h:?} costs {cost}");
        assert!(*cost <= 8);
        assert_eq!(*cost, h.distance(at(0, 2)) * 2);
        assert!(h.distance(at(0, 2)) <= 4);
    }
    assert!(reach.iter().any(|(h, _)| h.distance(at(0, 2)) == 4));
    assert!(reach.iter().all(|(h, _)| *h != at(0, 2)));
    assert_eq!(b.move_path(0, at(8, 2)).unwrap_err(), ActionError::NotEnoughAp { need: 16, have: 9 });
}

// ------------------------------------------------------------------- AP

#[test]
fn move_and_attack_spend_ap_and_are_rejected_when_short() {
    let mut b = battle_with(
        open_map(8, 3),
        &[(Team::Player, Archetype::Swordsman, at(0, 1)), (Team::Enemy, Archetype::Raider, at(3, 1))],
        3,
    );
    assert_eq!(b.active_id(), 0);
    assert_eq!(b.active().ap, AP_PER_TURN);
    b.apply(Command::Move { to: at(1, 1) }).unwrap();
    assert_eq!(b.active().ap, 7);
    // Out of reach for sword, too far.
    assert_eq!(b.apply(Command::Attack { target: 1 }), Err(ActionError::OutOfRange));
    b.apply(Command::Move { to: at(2, 1) }).unwrap();
    assert_eq!(b.active().ap, 5);
    b.apply(Command::Attack { target: 1 }).unwrap();
    assert_eq!(b.active().ap, 1);
    // 1 AP left: neither another attack nor a move is possible.
    assert_eq!(b.apply(Command::Attack { target: 1 }), Err(ActionError::NotEnoughAp { need: 4, have: 1 }));
    assert_eq!(b.apply(Command::Move { to: at(1, 1) }), Err(ActionError::NotEnoughAp { need: 2, have: 1 }));
    assert_eq!(b.active().ap, 1, "rejected actions must not change state");
}

#[test]
fn ap_resets_at_turn_start_and_order_follows_initiative() {
    // Initiatives: Swordsman 100, Archer 90, Raider 80, Brute 50.
    let mut b = battle_with(
        open_map(8, 4),
        &[
            (Team::Player, Archetype::Archer, at(0, 0)),
            (Team::Enemy, Archetype::Brute, at(7, 3)),
            (Team::Player, Archetype::Swordsman, at(0, 2)),
            (Team::Enemy, Archetype::Raider, at(7, 0)),
        ],
        9,
    );
    let q = b.turn_queue(4);
    assert_eq!(q, vec![2, 0, 3, 1]);
    assert_eq!(b.active_id(), 2);
    b.apply(Command::Move { to: at(1, 2) }).unwrap();
    assert_eq!(b.active().ap, 7);
    b.apply(Command::EndTurn).unwrap();
    assert_eq!(b.unit(2).ap, 0);
    assert_eq!(b.active_id(), 0);
    assert_eq!(b.active().ap, AP_PER_TURN);
    for _ in 0..3 {
        b.apply(Command::EndTurn).unwrap();
    }
    // Back to round 2, swordsman again with full AP.
    assert_eq!(b.round(), 2);
    assert_eq!(b.active_id(), 2);
    assert_eq!(b.active().ap, AP_PER_TURN);
}

// -------------------------------------------------------------------- LOS

#[test]
fn rock_and_forest_block_line_of_sight_but_endpoints_do_not() {
    let map = Map::from_ascii(&[".....", "..#..", ".....", ".f...", "....."]);
    let b = battle_with(
        map,
        &[(Team::Player, Archetype::Archer, at(0, 0)), (Team::Enemy, Archetype::Raider, at(4, 4))],
        1,
    );
    // Straight row with nothing between is visible.
    assert!(b.line_of_sight(at(0, 0), at(4, 0)));
    // Rock on the line blocks.
    assert!(!b.line_of_sight(at(1, 1), at(3, 1)));
    // Symmetry.
    assert!(!b.line_of_sight(at(3, 1), at(1, 1)));
    // Standing in forest (an endpoint) does not block your own shot.
    assert!(b.line_of_sight(at(1, 3), at(4, 3)));
    // Forest in between blocks.
    assert!(!b.line_of_sight(at(0, 3), at(3, 3)));
    // Adjacent hexes always see each other.
    assert!(b.line_of_sight(at(1, 3), at(2, 3)));
}

#[test]
fn ranged_attack_rejected_out_of_range_without_los_and_inside_min_range() {
    let map = Map::from_ascii(&["..........", "....#.....", ".........."]);
    let mut b = battle_with(
        map,
        &[(Team::Player, Archetype::Archer, at(0, 1)), (Team::Enemy, Archetype::Raider, at(9, 1)), (Team::Enemy, Archetype::Raider, at(3, 1))],
        5,
    );
    // Raider 2 at distance 3, LOS along row 1: rock at col 4 is beyond it.
    assert!(b.attack_preview(0, 2).is_ok());
    assert_eq!(b.attack_preview(0, 1), Err(ActionError::OutOfRange));
    // Move the archer so the rock sits between archer and target.
    let map2 = Map::from_ascii(&["....#....."]);
    let b2 = battle_with(
        map2,
        &[(Team::Player, Archetype::Archer, at(2, 0)), (Team::Enemy, Archetype::Raider, at(6, 0))],
        5,
    );
    assert_eq!(b2.attack_preview(0, 1), Err(ActionError::NoLineOfSight));
    // The command itself is rejected and spends nothing.
    let mut b2 = b2;
    assert_eq!(b2.apply(Command::Attack { target: 1 }), Err(ActionError::NoLineOfSight));
    assert_eq!(b2.active().ap, AP_PER_TURN);
    // Adjacent: archer falls back to the dagger (melee).
    b.apply(Command::Move { to: at(2, 1) }).unwrap();
    let p = b.attack_preview(0, 2).unwrap();
    assert_eq!(p.kind, AttackKind::Melee);
}

// ------------------------------------------------------------- hit chance

/// Hit chance as if `a` had a full turn of AP (so it works for non-active units too).
fn chance(b: &Battle, a: UnitId, t: UnitId) -> HitBreakdown {
    let (au, tu) = (b.unit(a), b.unit(t));
    b.preview_from(au, au.pos, AP_PER_TURN, tu).unwrap().hit
}

#[test]
fn hit_chance_formula_terrain_hill_and_range() {
    // Plain vs plain: chance = attacker skill - defender defense.
    let plain = battle_with(
        open_map(6, 3),
        &[(Team::Player, Archetype::Swordsman, at(1, 1)), (Team::Enemy, Archetype::Raider, at(2, 1))],
        1,
    );
    let base = chance(&plain, 0, 1);
    assert_eq!(base.chance, Archetype::Swordsman.stats().melee_skill - Archetype::Raider.stats().defense);
    assert_eq!((base.terrain_defense, base.height, base.range), (0, 0, 0));

    // Target in forest: +15 defense.
    let forest = battle_with(
        Map::from_ascii(&[".f...", ".....", "....."]),
        &[(Team::Player, Archetype::Swordsman, at(0, 0)), (Team::Enemy, Archetype::Raider, at(1, 0))],
        1,
    );
    let f = chance(&forest, 0, 1);
    assert_eq!(f.terrain_defense, 15);
    assert_eq!(f.chance, base.chance - 15);

    // Attacker on a hill: bonus. Target on a hill: penalty.
    let hill_attacker = battle_with(
        Map::from_ascii(&["h....", ".....", "....."]),
        &[(Team::Player, Archetype::Swordsman, at(0, 0)), (Team::Enemy, Archetype::Raider, at(1, 0))],
        1,
    );
    assert_eq!(chance(&hill_attacker, 0, 1).height, HILL_ATTACK_BONUS);
    assert_eq!(chance(&hill_attacker, 0, 1).chance, base.chance + HILL_ATTACK_BONUS);
    let hill_target = battle_with(
        Map::from_ascii(&[".h...", ".....", "....."]),
        &[(Team::Player, Archetype::Swordsman, at(0, 0)), (Team::Enemy, Archetype::Raider, at(1, 0))],
        1,
    );
    assert_eq!(chance(&hill_target, 0, 1).height, -HILL_ATTACK_BONUS);
    assert_eq!(chance(&hill_target, 0, 1).chance, base.chance - HILL_ATTACK_BONUS);

    // Ranged accuracy drops with distance beyond the minimum range.
    let ranged = battle_with(
        open_map(10, 1),
        &[(Team::Player, Archetype::Archer, at(0, 0)), (Team::Enemy, Archetype::Raider, at(2, 0)), (Team::Enemy, Archetype::Raider, at(6, 0))],
        1,
    );
    let near = chance(&ranged, 0, 1);
    let far = chance(&ranged, 0, 2);
    assert_eq!(near.range, 0);
    assert!(far.range < 0);
    assert_eq!(near.chance - far.chance, -far.range);
}

#[test]
fn hit_chance_is_clamped_to_floor_and_ceiling() {
    // Floor: archer's dagger (skill 45) against a shield-bearer (35) in forest (+15) is
    // 45 - 35 - 15 = -5 before clamping.
    let floor = battle_with(
        Map::from_ascii(&[".f"]),
        &[(Team::Player, Archetype::Archer, at(0, 0)), (Team::Enemy, Archetype::ShieldBearer, at(1, 0))],
        1,
    );
    let b = chance(&floor, 0, 1);
    let raw = b.skill + b.weapon_mod - b.defense - b.terrain_defense + b.height + b.range;
    assert!(raw < MIN_HIT_CHANCE);
    assert_eq!(b.chance, MIN_HIT_CHANCE);

    // Ceiling: stack a hill bonus on a high-skill attacker against a defenceless target
    // by granting the attacker an absurd skill.
    let mut units = vec![
        Unit::new(0, "a", Team::Player, Archetype::Swordsman, at(0, 0)),
        Unit::new(1, "b", Team::Enemy, Archetype::Brute, at(1, 0)),
    ];
    units[0].stats.melee_skill = 200;
    let ceil = Battle::new(Map::from_ascii(&["h."]), units, 1);
    let c = chance(&ceil, 0, 1);
    assert!(c.skill + c.height - c.defense > MAX_HIT_CHANCE);
    assert_eq!(c.chance, MAX_HIT_CHANCE);
}

// ------------------------------------------------- rolls, damage and death

#[test]
fn seeded_rolls_are_reproducible() {
    let run = |seed| {
        let mut b = battle_with(
            open_map(6, 3),
            &[(Team::Player, Archetype::Swordsman, at(1, 1)), (Team::Enemy, Archetype::Brute, at(2, 1))],
            seed,
        );
        let mut evs = Vec::new();
        evs.extend(b.apply(Command::Attack { target: 1 }).unwrap());
        evs.extend(b.apply(Command::EndTurn).unwrap());
        evs.extend(b.apply(Command::EndTurn).unwrap());
        evs.extend(b.apply(Command::Attack { target: 1 }).unwrap());
        (evs, b.unit(1).hp, b.unit(1).armor)
    };
    assert_eq!(run(77), run(77));
    let outcomes: std::collections::HashSet<_> = (0..20).map(|s| format!("{:?}", run(s).0)).collect();
    assert!(outcomes.len() > 1, "different seeds should give different rolls");
}

#[test]
fn damage_goes_through_armor_then_hp_and_is_bounded_by_weapon() {
    let mut saw_hit = false;
    for seed in 0..40 {
        let mut b = battle_with(
            open_map(6, 3),
            &[(Team::Player, Archetype::Swordsman, at(1, 1)), (Team::Enemy, Archetype::Raider, at(2, 1))],
            seed,
        );
        let before = (b.unit(1).hp, b.unit(1).armor);
        let ev = b.apply(Command::Attack { target: 1 }).unwrap();
        let Event::Attacked { hit, damage, armor_loss, hp_loss, chance, roll, .. } = ev[0].clone() else { panic!() };
        assert_eq!(hit, roll < chance);
        if hit {
            saw_hit = true;
            let w = Archetype::Swordsman.melee_weapon();
            assert!((w.dmg_min..=w.dmg_max).contains(&damage));
            assert_eq!(armor_loss + hp_loss, damage);
            assert_eq!(b.unit(1).armor, before.1 - armor_loss);
            assert_eq!(b.unit(1).hp, before.0 - hp_loss);
            assert!(hp_loss >= damage * w.armor_ignore / 100);
        } else {
            assert_eq!((b.unit(1).hp, b.unit(1).armor), before);
            assert_eq!((damage, armor_loss, hp_loss), (0, 0, 0));
        }
    }
    assert!(saw_hit);
}

#[test]
fn death_removes_unit_from_turn_order_and_ends_battle() {
    // Weak lone target: keep attacking until it dies.
    let mut b = battle_with(
        open_map(6, 3),
        &[(Team::Player, Archetype::Swordsman, at(1, 1)), (Team::Enemy, Archetype::BanditArcher, at(2, 1))],
        11,
    );
    let mut died = false;
    'outer: for _ in 0..200 {
        loop {
            match b.apply(Command::Attack { target: 1 }) {
                Ok(ev) => {
                    if ev.iter().any(|e| matches!(e, Event::Died { unit: 1 })) {
                        died = true;
                        break 'outer;
                    }
                }
                Err(_) => break,
            }
        }
        b.apply(Command::EndTurn).unwrap(); // swordsman -> archer (acts but we just end)
        b.apply(Command::EndTurn).unwrap();
    }
    assert!(died);
    assert!(!b.unit(1).alive);
    assert_eq!(b.unit(1).hp, 0);
    assert_eq!(b.outcome(), Some(Outcome::Victory));
    assert!(!b.turn_queue(10).contains(&1));
    assert_eq!(b.unit_at(at(2, 1)), None, "corpses do not occupy hexes");
    assert_eq!(b.apply(Command::EndTurn), Err(ActionError::BattleOver));
    assert_eq!(b.casualties(Team::Enemy).count(), 1);
}

#[test]
fn dead_units_are_skipped_mid_round() {
    let mut b = battle_with(
        open_map(8, 4),
        &[
            (Team::Player, Archetype::Swordsman, at(0, 0)),
            (Team::Enemy, Archetype::Raider, at(7, 0)),
            (Team::Enemy, Archetype::Brute, at(7, 3)),
        ],
        2,
    );
    // Order: swordsman(100), raider(80), brute(50). Kill the raider by hand-edit through the public clone API.
    assert_eq!(b.turn_queue(3), vec![0, 1, 2]);
    let mut units: Vec<Unit> = b.units().to_vec();
    units[1].alive = false;
    units[1].hp = 0;
    let mut b2 = Battle::new(b.map().clone(), units, 2);
    assert_eq!(b2.turn_queue(2), vec![0, 2]);
    b2.apply(Command::EndTurn).unwrap();
    assert_eq!(b2.active_id(), 2);
    b.apply(Command::EndTurn).unwrap();
    assert_eq!(b.active_id(), 1);
}

// ---------------------------------------------------------------------- AI

#[test]
fn ai_ranged_unit_attacks_visible_target_in_range() {
    let b = battle_with(
        open_map(10, 3),
        &[(Team::Enemy, Archetype::BanditArcher, at(0, 1)), (Team::Player, Archetype::Swordsman, at(4, 1))],
        1,
    );
    assert_eq!(b.active_id(), 1, "swordsman has higher initiative; end its turn first");
    let mut b = b;
    b.apply(Command::EndTurn).unwrap();
    assert_eq!(b.active_id(), 0);
    assert_eq!(ai::decide(&b), Command::Attack { target: 1 });
}

#[test]
fn ai_ranged_unit_moves_to_firing_position_when_out_of_range() {
    let mut b = battle_with(
        open_map(14, 3),
        &[(Team::Enemy, Archetype::BanditArcher, at(0, 1)), (Team::Player, Archetype::Swordsman, at(8, 1))],
        1,
    );
    b.apply(Command::EndTurn).unwrap();
    let cmd = ai::decide(&b);
    match cmd {
        Command::Move { to } => {
            // Ends within bow range of the target with AP left to shoot.
            let (_, cost) = b.move_path(0, to).unwrap();
            // Ends inside bow range of the target and keeps enough AP to shoot.
            let d = to.distance(at(8, 1));
            assert!((2..=6).contains(&d), "to={to:?} d={d}");
            assert!(AP_PER_TURN - cost >= Archetype::BanditArcher.ranged_weapon().unwrap().ap_cost);
        }
        other => panic!("expected a move, got {other:?}"),
    }
}

#[test]
fn ai_melee_unit_advances_and_never_makes_illegal_moves() {
    let b = scenario::skirmish(5);
    let mut b = b;
    for step in 0..400 {
        if b.outcome().is_some() {
            break;
        }
        let cmd = ai::decide(&b);
        assert!(b.apply(cmd).is_ok(), "illegal AI command {cmd:?} at step {step}");
    }
    assert!(b.outcome().is_some());
}

#[test]
fn ai_avoids_unreachable_targets_without_looping() {
    // Enemy sealed behind water: nothing to do but end the turn.
    let map = Map::from_ascii(&["..~..", "..~..", "..~.."]);
    let mut b = battle_with(
        map,
        &[(Team::Enemy, Archetype::Raider, at(4, 1)), (Team::Player, Archetype::Swordsman, at(0, 1))],
        1,
    );
    b.apply(Command::EndTurn).unwrap();
    for _ in 0..5 {
        let cmd = ai::decide(&b);
        if cmd == Command::EndTurn {
            return;
        }
        b.apply(cmd).unwrap();
    }
    panic!("AI kept acting without progress");
}

// -------------------------------------------------------------- integration

fn play_to_end(seed: u64) -> (Option<Outcome>, u32, Vec<(bool, i32, i32)>) {
    let mut b = scenario::skirmish(seed);
    let mut actions = 0;
    while b.outcome().is_none() {
        let cmd = ai::decide(&b);
        b.apply(cmd).expect("AI commands are legal");
        actions += 1;
        assert!(actions < 5000, "battle did not terminate");
    }
    let state = b.units().iter().map(|u| (u.alive, u.hp, u.armor)).collect();
    (b.outcome(), b.round(), state)
}

#[test]
fn full_battle_terminates_and_is_reproducible() {
    for seed in [1u64, 2, 3, 42, 1234] {
        let a = play_to_end(seed);
        let b = play_to_end(seed);
        assert!(a.0.is_some());
        assert!(a.1 <= MAX_ROUNDS);
        assert_eq!(a, b, "seed {seed} must replay identically");
    }
}

#[test]
fn scenario_is_well_formed() {
    let b = scenario::skirmish(0);
    let m = b.map();
    assert_eq!((m.width(), m.height()), (12, 10));
    let mut kinds = std::collections::HashSet::new();
    for h in m.hexes() {
        kinds.insert(m.terrain(h).unwrap());
    }
    assert!(kinds.len() >= 4);
    let players = b.living(Team::Player).count();
    let enemies = b.living(Team::Enemy).count();
    assert!((3..=4).contains(&players));
    assert!((4..=5).contains(&enemies));
    let arch: std::collections::HashSet<_> = b.living(Team::Player).map(|u| u.archetype).collect();
    assert!(arch.len() >= 3);
    let earch: std::collections::HashSet<_> = b.living(Team::Enemy).map(|u| u.archetype).collect();
    assert!(earch.len() >= 2);
    let mut seen = std::collections::HashSet::new();
    for u in b.units() {
        assert!(m.terrain(u.pos).unwrap().passable());
        assert!(seen.insert(u.pos), "two units share a hex");
    }
}

#[test]
fn units_block_movement_through_a_corridor() {
    // One-hex-wide corridor (rock above and below) with an enemy plugging it.
    let map = Map::from_ascii(&["#####", ".....", "#####"]);
    let b = battle_with(
        map,
        &[(Team::Player, Archetype::Swordsman, at(0, 1)), (Team::Enemy, Archetype::Raider, at(2, 1))],
        1,
    );
    assert!(b.move_path(0, at(1, 1)).is_ok());
    assert_eq!(b.move_path(0, at(3, 1)), Err(ActionError::NoPath));
    assert_eq!(b.move_path(0, at(2, 1)), Err(ActionError::Occupied));
}

#[test]
fn stalemate_hits_the_round_limit_and_counts_as_defeat() {
    let map = Map::from_ascii(&["..~..", "..~..", "..~.."]);
    let mut b = battle_with(
        map,
        &[(Team::Player, Archetype::Swordsman, at(0, 1)), (Team::Enemy, Archetype::Raider, at(4, 1))],
        1,
    );
    let mut ended = None;
    for _ in 0..(MAX_ROUNDS * 4) {
        match b.apply(Command::EndTurn) {
            Ok(ev) => {
                if let Some(Event::BattleEnded { outcome, timed_out }) = ev.last() {
                    ended = Some((*outcome, *timed_out));
                }
            }
            Err(e) => {
                assert_eq!(e, ActionError::BattleOver);
                break;
            }
        }
    }
    assert_eq!(ended, Some((Outcome::Defeat, true)));
    assert!(b.timed_out());
    assert!(b.round() <= MAX_ROUNDS);
    // Terminal-state queries stay valid: the UI keeps rendering the finished battle.
    let active = b.active_id();
    assert!(active < b.units().len());
    let _ = b.active();
    let _ = b.turn_queue(10);
    let _ = b.remaining_this_round();
    assert_eq!(b.apply(Command::EndTurn), Err(ActionError::BattleOver));
}

#[test]
fn kills_are_counted_per_unit_and_reported_for_roster_units() {
    let map = open_map(4, 1);
    let units = vec![
        Unit::mercenary(0, 7, "Merc", Archetype::Swordsman, 3, 12, at(0, 0)),
        Unit::new(1, "Victim", Team::Enemy, Archetype::BanditArcher, at(1, 0)),
    ];
    let mut b = Battle::new(map, units, 3);
    assert_eq!(b.unit(0).hp, 12, "persistent units start with their carried-over HP");
    assert_eq!(b.unit(0).stats.max_hp, Archetype::Swordsman.stats().max_hp + 2 * unit::HP_PER_LEVEL);
    assert_eq!(b.unit(0).armor, b.unit(0).stats.max_armor, "armor is repaired between battles");
    let mut guard = 0;
    while b.outcome().is_none() && guard < 200 {
        let cmd = ai::decide(&b);
        b.apply(cmd).unwrap();
        guard += 1;
    }
    let outs = b.player_outcomes();
    assert_eq!(outs.len(), 1, "only roster units are reported");
    assert_eq!(outs[0].roster_id, 7);
    assert_eq!(outs[0].dead, !b.unit(0).alive);
    assert_eq!(outs[0].final_hp, b.unit(0).hp);
    let expected = if b.outcome() == Some(Outcome::Victory) { 1 } else { 0 };
    assert_eq!(outs[0].kills, expected);
    assert!(b.units().iter().all(|u| u.team == Team::Player || u.roster_id.is_none()));
}
