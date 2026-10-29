use bb_rules::hex::{self, OBSTACLES, bfs_path, map_hexes};
use bb_rules::rules::soak;
use bb_rules::*;

fn fresh() -> BattleState {
    BattleState::new(1)
}

/// Make unit `id` active with a clean slate.
fn activate(s: &mut BattleState, id: u32) {
    s.queue.retain(|&q| q != id);
    s.queue.insert(0, id);
    s.units[id as usize].ap = 9;
}

#[test]
fn hex_distance_and_neighbors() {
    assert_eq!(hex(0, 0).distance(hex(3, -3)), 3);
    assert_eq!(hex(-3, 3).distance(hex(3, -3)), 6);
    assert_eq!(hex(1, 1).distance(hex(1, 1)), 0);
    let n = hex(0, 0).neighbors();
    assert_eq!(n, [hex(1, 0), hex(1, -1), hex(0, -1), hex(-1, 0), hex(-1, 1), hex(0, 1)]);
    assert!(n.iter().all(|h| h.distance(hex(0, 0)) == 1));
}

#[test]
fn map_has_61_hexes_and_obstacles_block() {
    assert_eq!(map_hexes().len(), 61);
    assert!(!hex(5, -1).in_map());
    let mut s = fresh();
    for ob in OBSTACLES {
        assert!(ob.is_obstacle());
        assert!(map_hexes().contains(&ob));
    }
    activate(&mut s, 0);
    // (-2,-1) is an obstacle; P1 is far but destination validity comes first.
    assert_eq!(s.apply(Command::Move { unit: 0, to: hex(-2, -1) }), Err(RuleError::Unreachable));
    assert_eq!(s.apply(Command::Move { unit: 0, to: hex(9, 9) }), Err(RuleError::Unreachable));
    assert_eq!(s.apply(Command::Move { unit: 0, to: hex(-3, 3) }), Err(RuleError::Unreachable));
    assert_eq!(s.apply(Command::Move { unit: 0, to: hex(-4, 3) }), Err(RuleError::Unreachable));
}

#[test]
fn bfs_avoids_obstacles_and_units_and_is_repeatable() {
    // (-1,0) -> (1,0) must go around the obstacle at (0,0).
    let p = bfs_path(hex(-1, 0), hex(1, 0), &[]).unwrap();
    assert!(!p.contains(&hex(0, 0)));
    assert_eq!(p.len(), 5); // walls at (0,0) (1,-1) (-1,1) force a detour
    assert_eq!(p, bfs_path(hex(-1, 0), hex(1, 0), &[]).unwrap());
    // A unit in the way is avoided too.
    let blocked = [hex(0, -1)];
    let p2 = bfs_path(hex(-1, 0), hex(1, 0), &blocked).unwrap();
    assert!(!p2.contains(&hex(0, -1)) && !p2.contains(&hex(0, 0)));
    assert_eq!(bfs_path(hex(-1, 0), hex(0, 0), &[]), None);
    assert_eq!(bfs_path(hex(-1, 0), hex(-1, 0), &[]), None);
    let _ = hex::RADIUS;
}

#[test]
fn move_costs_ap_and_fatigue_and_errors_keep_state() {
    let mut s = fresh();
    let id = s.active_unit().unwrap().id;
    let from = s.units[id as usize].pos;
    let target = s.reachable(id).iter().find(|(_, c)| *c == 4).unwrap().0;
    let ev = s.apply(Command::Move { unit: id, to: target }).unwrap();
    assert!(matches!(&ev[0], Event::Moved { path, .. } if path.len() == 2));
    let u = &s.units[id as usize];
    assert_eq!((u.ap, u.fatigue, u.pos == target), (5, 4, true));
    assert_ne!(u.pos, from);

    // NotEnoughAp: ap 3, 2-step move.
    let mut s = fresh();
    let id = s.active_unit().unwrap().id;
    s.units[id as usize].ap = 3;
    let far = s.reachable(id).len();
    assert!(far < 20);
    let t = map_hexes().into_iter().find(|h| {
        let p = bfs_path(s.units[id as usize].pos, *h, &[]);
        p.is_some_and(|p| p.len() == 2) && !s.units.iter().any(|u| u.pos == *h)
    }).unwrap();
    let before = s.state_hash();
    assert_eq!(s.apply(Command::Move { unit: id, to: t }), Err(RuleError::NotEnoughAp));
    assert_eq!(s.state_hash(), before);

    // TooFatigued: AP is fine, fatigue is not.
    s.units[id as usize].ap = 9;
    s.units[id as usize].fatigue = 99;
    let before = s.state_hash();
    assert_eq!(s.apply(Command::Move { unit: id, to: t }), Err(RuleError::TooFatigued));
    assert_eq!(s.state_hash(), before);
}

#[test]
fn turn_start_refreshes_ap_and_regenerates_fatigue() {
    let mut s = fresh();
    let first = s.active_unit().unwrap().id;
    assert_eq!(s.units[first as usize].ap, 9);
    // Give the next unit fatigue and ap leftovers, then end the first turn.
    let next = s.queue[1];
    s.units[next as usize].fatigue = 40;
    s.units[next as usize].ap = 1;
    s.apply(Command::EndTurn { unit: first }).unwrap();
    assert_eq!(s.active_unit().unwrap().id, next);
    assert_eq!(s.units[next as usize].ap, 9);
    assert_eq!(s.units[next as usize].fatigue, 25);
    // Saturates at zero.
    let n2 = s.queue[1];
    s.units[n2 as usize].fatigue = 5;
    s.apply(Command::EndTurn { unit: next }).unwrap();
    assert_eq!(s.units[n2 as usize].fatigue, 0);
}

#[test]
fn recover_sets_active_unit_fatigue_to_half_current_value() {
    let mut s = fresh();
    activate(&mut s, 0);
    s.units[0].stats.fatigue_max = 100;
    s.units[0].fatigue = 40;

    s.apply(Command::Recover { unit: 0 }).unwrap();

    assert_eq!(s.units[0].fatigue, 20);
    assert_eq!(s.units[0].ap, 0);
    assert!(!s.queue.contains(&0));
    assert_eq!(s.active_unit().unwrap().id, 1);
}

#[test]
fn recover_keeps_zero_fatigue_at_zero() {
    let mut s = fresh();
    activate(&mut s, 0);
    s.units[0].stats.fatigue_max = 100;
    s.units[0].fatigue = 0;

    s.apply(Command::Recover { unit: 0 }).unwrap();

    assert_eq!(s.units[0].fatigue, 0);
    assert_eq!(s.units[0].ap, 0);
    assert!(!s.queue.contains(&0));
    assert_eq!(s.active_unit().unwrap().id, 1);
}

#[test]
fn initiative_queue_ties_by_id_and_fatigue_matters() {
    let s = fresh();
    // Players (100) before enemies (90); ties by id.
    assert_eq!(s.queue, vec![0, 1, 2, 3, 4, 5]);
    let mut s = fresh();
    s.units[0].fatigue = 40; // 100 - 20 = 80 < 90
    s.units[3].fatigue = 20; // 90 - 10 = 80, ties with P1 -> id 0 first
    // Walk to the next round through EndTurn and observe the new queue.
    for _ in 0..6 {
        let id = s.active_unit().unwrap().id;
        s.units[0].fatigue = 40;
        s.units[3].fatigue = 20;
        s.apply(Command::EndTurn { unit: id }).unwrap();
    }
    assert_eq!(s.round, 2);
    // Fatigue was 40/20 at round start before any regen: keys 80,100,100,80,90,90.
    assert_eq!(s.queue, vec![1, 2, 4, 5, 0, 3]);
}

#[test]
fn wait_moves_to_end_keeps_ap_and_only_once() {
    let mut s = fresh();
    let first = s.active_unit().unwrap().id;
    s.units[first as usize].ap = 7;
    s.apply(Command::Wait { unit: first }).unwrap();
    assert_eq!(*s.queue.last().unwrap(), first);
    assert_ne!(s.active_unit().unwrap().id, first);
    for _ in 0..5 {
        let id = s.active_unit().unwrap().id;
        s.apply(Command::EndTurn { unit: id }).unwrap();
    }
    assert_eq!(s.active_unit().unwrap().id, first);
    assert_eq!(s.units[first as usize].ap, 7, "no refresh after waiting");
    assert_eq!(s.apply(Command::Wait { unit: first }), Err(RuleError::AlreadyWaited));
}

#[test]
fn not_active_not_adjacent_invalid_target() {
    let mut s = fresh();
    assert_eq!(s.apply(Command::EndTurn { unit: 3 }), Err(RuleError::NotActiveUnit));
    assert_eq!(s.apply(Command::Attack { attacker: 3, target: 0 }), Err(RuleError::NotActiveUnit));
    assert_eq!(s.apply(Command::Attack { attacker: 0, target: 3 }), Err(RuleError::NotAdjacent));
    assert_eq!(s.apply(Command::Attack { attacker: 0, target: 1 }), Err(RuleError::InvalidTarget));
    assert_eq!(s.apply(Command::Attack { attacker: 0, target: 0 }), Err(RuleError::InvalidTarget));
    assert_eq!(s.apply(Command::Attack { attacker: 0, target: 99 }), Err(RuleError::InvalidTarget));
}

#[test]
fn hit_chance_values_and_clamp() {
    let mut s = fresh();
    assert_eq!(s.hit_chance(0, 3), 55);
    assert_eq!(s.hit_chance(3, 0), 40);
    s.units[0].stats.melee_skill = 200;
    assert_eq!(s.hit_chance(0, 3), 95);
    s.units[0].stats.melee_skill = 0;
    assert_eq!(s.hit_chance(0, 3), 5);
}

fn surround_fixture() -> BattleState {
    let mut s = fresh();
    // The attacker and target use distinct, walkable map hexes. Supporting
    // units remain at their distant scenario positions until a test places them.
    s.units[0].pos = hex(1, 0);
    s.units[3].pos = hex(2, 0);
    activate(&mut s, 0);

    let walkable: Vec<_> = s.units.iter().map(|u| u.pos).collect();
    assert!(walkable.iter().all(|h| map_hexes().contains(h) && !h.is_obstacle()));
    for i in 0..walkable.len() {
        assert!(!walkable[..i].contains(&walkable[i]), "fixture hexes must be distinct");
    }
    s
}

#[test]
fn surround_bonus_adds_five_points_per_additional_adjacent_ally() {
    let mut s = surround_fixture();
    assert_eq!(s.hit_chance(0, 3), 55);

    s.units[1].pos = hex(3, -1);
    assert_eq!(s.hit_chance(0, 3), 60);

    s.units[2].pos = hex(1, 1);
    assert_eq!(s.hit_chance(0, 3), 65);
}

#[test]
fn surround_bonus_is_direction_independent() {
    let mut s = surround_fixture();
    let ring = [hex(3, 0), hex(3, -1), hex(1, 0), hex(1, 1)];
    for attacker_slot in 0..ring.len() {
        s.units[0].pos = ring[attacker_slot];
        s.units[1].pos = ring[(attacker_slot + 1) % ring.len()];
        s.units[2].pos = ring[(attacker_slot + 2) % ring.len()];
        assert_eq!(s.hit_chance(0, 3), 65);
    }
}

#[test]
fn enemy_attackers_receive_the_same_surround_bonus() {
    let mut s = fresh();
    s.units[0].pos = hex(2, 0);
    s.units[3].pos = hex(1, 0);
    assert_eq!(s.hit_chance(3, 0), 50 - 10);

    s.units[4].pos = hex(3, -1);
    assert_eq!(s.hit_chance(4, 0), 50 - 10 + 5);

    s.units[5].pos = hex(1, 1);
    assert_eq!(s.hit_chance(5, 0), 50 - 10 + 10);
}

#[test]
fn surround_ignores_dead_distant_and_target_side_units() {
    let mut s = surround_fixture();
    s.units[1].pos = hex(3, -1);
    s.units[1].alive = false;
    // This living ally remains distant; an adjacent unit on the target's side
    // also contributes nothing.
    s.units[4].pos = hex(1, 1);
    assert_eq!(s.hit_chance(0, 3), 55);
}

#[test]
fn nonadjacent_or_dead_attacker_gets_no_surround_bonus() {
    let mut s = surround_fixture();
    s.units[0].pos = hex(0, 1);
    s.units[1].pos = hex(3, -1);
    s.units[2].pos = hex(1, 1);
    assert_eq!(s.hit_chance(0, 3), 55);

    s.units[0].pos = hex(1, 0);
    s.units[0].alive = false;
    assert_eq!(s.hit_chance(0, 3), 55);
}

#[test]
fn surround_is_added_before_the_existing_hit_chance_clamp() {
    let mut s = surround_fixture();
    s.units[1].pos = hex(3, -1);
    s.units[2].pos = hex(1, 1);

    s.units[0].stats.melee_skill = 0;
    s.units[3].stats.defense = 50;
    assert_eq!(s.hit_chance(0, 3), 5, "negative base chance plus surround clamps at 5");

    s.units[0].stats.melee_skill = 90;
    s.units[3].stats.defense = 0;
    assert_eq!(s.hit_chance(0, 3), 95);
}

#[test]
fn moving_or_killing_a_supporting_ally_immediately_removes_its_bonus() {
    let mut s = surround_fixture();
    s.units[1].pos = hex(3, -1);
    assert_eq!(s.hit_chance(0, 3), 60);

    activate(&mut s, 1);
    // P2 departs a hex controlled by E1 and E3. Seed 3's first two rolls (53, 61)
    // miss both reactions (45% each), so the move completes.
    s.rng = Rng::new(3);
    let events = s.apply(Command::Move { unit: 1, to: hex(4, -1) }).unwrap();
    assert_eq!(s.units[1].pos, hex(4, -1));
    assert_eq!(events.iter().filter(|e| matches!(e, Event::Attacked { hit: false, .. })).count(), 2);
    assert_eq!(s.hit_chance(0, 3), 55);

    s.units[1].pos = hex(3, -1);
    s.units[1].alive = true;
    assert_eq!(s.hit_chance(0, 3), 60);
    s.units[1].alive = false;
    assert_eq!(s.hit_chance(0, 3), 55);
}

#[test]
fn hit_chance_preview_is_pure_and_attack_reports_the_same_chance() {
    let mut s = surround_fixture();
    s.units[1].pos = hex(3, -1);
    let before = s.state_hash();
    let preview = s.hit_chance(0, 3);
    assert_eq!(preview, 60);
    assert_eq!(s.state_hash(), before);

    let events = s.apply(Command::Attack { attacker: 0, target: 3 }).unwrap();
    assert!(events.iter().any(|event| matches!(event,
        Event::Attacked { attacker: 0, target: 3, chance, .. } if *chance == preview
    )));
}

#[test]
fn armour_example_from_plan() {
    let (armour, hp) = (40, 60);
    let (a, l) = soak(armour, 30);
    assert_eq!((armour - a, hp - l), (10, 57));
    let (a2, l2) = soak(armour - a, 30);
    assert_eq!((a2, l2), (10, 23));
    assert_eq!((10 - a2, 57 - l2), (0, 34));
}

fn adjacent_pair(s: &mut BattleState) {
    s.units[0].pos = hex(1, 0);
    s.units[3].pos = hex(2, 0);
    activate(s, 0);
}

#[test]
fn attack_costs_and_errors_keep_state() {
    let mut s = fresh();
    adjacent_pair(&mut s);
    s.units[0].ap = 3;
    let h = s.state_hash();
    assert_eq!(s.apply(Command::Attack { attacker: 0, target: 3 }), Err(RuleError::NotEnoughAp));
    assert_eq!(s.state_hash(), h);
    s.units[0].ap = 9;
    s.units[0].fatigue = 95;
    let h = s.state_hash();
    assert_eq!(s.apply(Command::Attack { attacker: 0, target: 3 }), Err(RuleError::TooFatigued));
    assert_eq!(s.state_hash(), h);
    s.units[0].fatigue = 0;
    let ev = s.apply(Command::Attack { attacker: 0, target: 3 }).unwrap();
    assert!(matches!(ev[0], Event::Attacked { attacker: 0, target: 3, chance: 55, .. }));
    assert_eq!((s.units[0].ap, s.units[0].fatigue), (5, 10));
}

#[test]
fn death_removes_from_queue_and_map_occupancy() {
    let mut s = fresh();
    adjacent_pair(&mut s);
    s.units[3].hp = 1;
    s.units[3].armour = 0;
    s.units[0].stats.melee_skill = 200; // always hits
    let ev = s.apply(Command::Attack { attacker: 0, target: 3 }).unwrap();
    assert!(ev.contains(&Event::Died { unit: 3 }));
    assert!(!s.queue.contains(&3));
    assert!(!s.units[3].alive);
    // Hex is free again.
    assert!(s.reachable(0).iter().any(|(h, _)| *h == hex(2, 0)));
    assert!(s.legal_targets(0).is_empty());
}

#[test]
fn victory_defeat_timelimit_and_battle_over() {
    let mut s = fresh();
    for id in 3..6 {
        s.units[id].alive = false;
    }
    s.queue.retain(|&q| q < 3);
    adjacent_pair(&mut s);
    s.units[3].alive = true;
    s.units[3].hp = 1;
    s.units[3].armour = 0;
    s.queue.push(3);
    s.units[0].stats.melee_skill = 200;
    let ev = s.apply(Command::Attack { attacker: 0, target: 3 }).unwrap();
    assert!(ev.contains(&Event::BattleEnded { outcome: Outcome::Victory, reason: EndReason::Elimination }));
    assert_eq!(s.outcome(), Some(Outcome::Victory));
    assert_eq!(s.apply(Command::EndTurn { unit: 0 }), Err(RuleError::BattleOver));
    assert!(s.active_unit().is_none());

    let mut s = fresh();
    for id in 0..3 {
        s.units[id].alive = false;
    }
    s.queue.retain(|&q| q >= 3);
    s.units[3].pos = hex(1, 0);
    s.units[0].pos = hex(2, 0);
    s.units[0].alive = true;
    s.units[0].hp = 1;
    s.units[0].armour = 0;
    activate(&mut s, 3);
    s.units[3].stats.melee_skill = 200;
    s.apply(Command::Attack { attacker: 3, target: 0 }).unwrap();
    assert_eq!(s.outcome(), Some(Outcome::Defeat));
    assert_eq!(s.end_reason(), Some(EndReason::Elimination));

    let mut s = fresh();
    s.round = 40;
    s.queue = vec![5];
    let ev = s.apply(Command::EndTurn { unit: 5 }).unwrap();
    assert_eq!(s.outcome(), Some(Outcome::Defeat));
    assert_eq!(s.end_reason(), Some(EndReason::TimeLimit));
    assert!(ev.contains(&Event::BattleEnded { outcome: Outcome::Defeat, reason: EndReason::TimeLimit }));
    assert_eq!(s.apply(Command::Wait { unit: 5 }), Err(RuleError::BattleOver));
}

#[test]
fn ai_attacks_weakest_neighbour_and_approaches_otherwise() {
    let mut s = fresh();
    s.units[0].pos = hex(1, 0);
    s.units[1].pos = hex(2, -1);
    s.units[3].pos = hex(2, 0);
    s.units[1].hp = 30;
    s.units[0].hp = 50;
    activate(&mut s, 3);
    assert_eq!(ai::plan_turn(&s), vec![Command::Attack { attacker: 3, target: 1 }]);
    // Equal hp: lowest id.
    s.units[1].hp = 50;
    assert_eq!(ai::plan_turn(&s), vec![Command::Attack { attacker: 3, target: 0 }]);
    // No neighbour: step towards a target.
    let s = fresh();
    let cmd = ai::plan_turn(&s);
    assert!(matches!(cmd[..], [Command::Move { unit: 0, .. }]));
}

#[test]
fn determinism_and_seed_sensitivity() {
    fn play(seed: u64) -> (u64, Vec<Event>) {
        let mut s = BattleState::new(seed);
        let mut all = Vec::new();
        while s.outcome().is_none() {
            let c = ai::plan_turn(&s)[0];
            all.extend(s.apply(c).unwrap());
        }
        (s.state_hash(), all)
    }
    let (h1, e1) = play(7);
    let (h2, e2) = play(7);
    assert_eq!((h1, &e1), (h2, &e2));
    let rolls = |e: &[Event]| e.iter().filter_map(|x| if let Event::Attacked { roll, .. } = x { Some(*roll) } else { None }).collect::<Vec<_>>();
    let (_, e3) = play(8);
    assert_ne!(rolls(&e1), rolls(&e3));
}

#[test]
fn rules_cargo_toml_has_no_dependencies() {
    let toml = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/Cargo.toml")).unwrap();
    let mut in_deps = false;
    for line in toml.lines().map(str::trim) {
        if line.starts_with('[') {
            in_deps = line.contains("dependencies");
            continue;
        }
        assert!(!(in_deps && !line.is_empty() && !line.starts_with('#')), "dependency entry: {line}");
    }
}
