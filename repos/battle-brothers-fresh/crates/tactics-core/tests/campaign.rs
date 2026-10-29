use tactics_core::autopilot::{self, play_battle, Step};
use tactics_core::campaign::*;
use tactics_core::mapgen;
use tactics_core::*;

fn travel_to_encounter(c: &mut Campaign) {
    let site = c.active.as_ref().expect("contract accepted").site;
    c.depart(site).unwrap();
    while matches!(c.phase, Phase::Travel(_)) {
        c.travel_day().unwrap();
    }
}

/// Accept the board contract with this index and arrive at its site.
fn start_encounter(c: &mut Campaign, board_index: usize) {
    c.accept_contract(board_index).unwrap();
    travel_to_encounter(c);
    assert_eq!(c.phase, Phase::Encounter);
}

fn return_home(c: &mut Campaign) -> Vec<CampaignEvent> {
    let mut all = Vec::new();
    while matches!(c.phase, Phase::Travel(_)) {
        all.extend(c.travel_day().unwrap());
    }
    all
}

// ------------------------------------------------------------------ start state

#[test]
fn campaign_starts_with_four_mercenaries_gold_and_day_zero() {
    let c = Campaign::new(1);
    assert_eq!(c.roster.len(), 4);
    assert_eq!(c.gold, START_GOLD);
    assert_eq!(c.day, 0);
    assert_eq!(c.phase, Phase::Town);
    assert!(c.active.is_none());
    assert!((2..=3).contains(&c.board.len()));
    assert!((2..=3).contains(&c.recruits.len()));
    let names: std::collections::HashSet<_> = c.roster.iter().map(|m| &m.name).collect();
    assert_eq!(names.len(), 4, "unique names");
    assert!(c.roster.iter().all(|m| m.level == 1 && m.xp == 0 && m.hp == m.max_hp()));
    assert!(SITES.len() >= 4);
    let days: std::collections::HashSet<_> = SITES.iter().map(|s| s.days).collect();
    assert!(days.len() >= 4, "sites at different travel distances");
}

#[test]
fn contracts_are_deterministic_per_seed_and_pay_scales_with_difficulty() {
    let a = Campaign::new(77);
    let b = Campaign::new(77);
    assert_eq!(a.board, b.board);
    assert_eq!(a.recruits, b.recruits);
    assert_ne!(Campaign::new(78).board, a.board);
    // Same site, higher difficulty -> better pay (jitter is far smaller than the step).
    let mut by_site: std::collections::HashMap<usize, Vec<(u8, i32)>> = Default::default();
    for seed in 0..300 {
        let c = Campaign::new(seed);
        for k in &c.board {
            assert!((1..=3).contains(&k.difficulty));
            assert!(k.deadline >= c.day + k.site_def().days);
            by_site.entry(k.site).or_default().push((k.difficulty, k.pay));
        }
    }
    for (site, v) in by_site {
        for &(d1, p1) in &v {
            for &(d2, p2) in &v {
                if d1 < d2 {
                    assert!(p1 < p2, "site {site}: difficulty {d1} pays {p1}, difficulty {d2} pays {p2}");
                }
            }
        }
    }
}

// ----------------------------------------------------------------------- travel

#[test]
fn travel_advances_days_and_deducts_wages_exactly() {
    let mut c = Campaign::new(3);
    let wages = c.daily_wages();
    assert_eq!(wages, c.roster.iter().map(|m| m.wage()).sum::<i32>());
    c.accept_contract(0).unwrap();
    let site = c.active.as_ref().unwrap().site;
    let days = SITES[site].days;
    let gold0 = c.gold;
    c.depart(site).unwrap();
    assert_eq!(c.day, 0, "departing takes no time itself");
    for i in 1..=days {
        let ev = c.travel_day().unwrap();
        assert_eq!(c.day, i);
        assert_eq!(c.gold, gold0 - wages * i as i32);
        assert!(ev.contains(&CampaignEvent::DayPassed { day: i, wages }));
    }
    assert_eq!(c.phase, Phase::Encounter);
    assert_eq!(c.at_site, Some(site));
    assert_eq!(c.travel_day(), Err(CampaignError::WrongPhase));
}

#[test]
fn only_the_accepted_contracts_site_has_an_encounter() {
    let mut c = Campaign::new(3);
    c.accept_contract(0).unwrap();
    let wanted = c.active.as_ref().unwrap().site;
    let other = (0..SITES.len()).find(|&s| s != wanted).unwrap();
    c.depart(other).unwrap();
    while matches!(c.phase, Phase::Travel(_)) {
        c.travel_day().unwrap();
    }
    assert_eq!(c.phase, Phase::Empty, "no contract there, so no enemies");
    assert_eq!(c.begin_battle().err(), Some(CampaignError::WrongPhase));
    c.retreat().unwrap();
    return_home(&mut c);
    assert_eq!(c.phase, Phase::Town);
    assert_eq!(c.active, None, "leaving without fighting forfeits the held contract");

    // Without any contract no site has an encounter either.
    let mut d = Campaign::new(3);
    d.depart(0).unwrap();
    while matches!(d.phase, Phase::Travel(_)) {
        d.travel_day().unwrap();
    }
    assert_eq!(d.phase, Phase::Empty);
}

#[test]
fn expired_contract_cannot_be_started_and_lapses_while_resting() {
    let mut c = Campaign::new(5);
    c.accept_contract(0).unwrap();
    let k = c.active.clone().unwrap();
    // Rest until it is no longer possible to arrive in time.
    c.gold = 100_000;
    while c.active.is_some() {
        c.rest().unwrap();
        assert!(c.day < 60);
    }
    assert!(c.day + SITES[k.site].days > k.deadline);
    assert_eq!(c.contracts_failed, 1);
    // Departing to a lapsed contract is refused and the contract dropped.
    let mut d = Campaign::new(5);
    d.accept_contract(0).unwrap();
    d.active.as_mut().unwrap().deadline = 0;
    let site = d.active.as_ref().unwrap().site;
    assert_eq!(d.depart(site), Err(CampaignError::TooLate));
    assert!(d.active.is_none());
}

// ---------------------------------------------------------------------- mapgen

#[test]
fn mapgen_produces_only_valid_maps_for_many_seeds() {
    let mut kinds = std::collections::HashSet::new();
    for seed in 0..500u64 {
        let players = 1 + (seed % 6) as usize;
        let enemies = 1 + (seed % 10) as usize;
        let layout = mapgen::generate(seed, players, enemies);
        mapgen::verify(&layout).unwrap_or_else(|e| panic!("seed {seed}: {e}"));
        assert_eq!(layout.player_spawns.len(), players);
        assert_eq!(layout.enemy_spawns.len(), enemies);
        // Independent re-check: spawns passable and mutually reachable.
        let reached = mapgen::connected_from(&layout.map, layout.player_spawns[0]);
        for h in layout.player_spawns.iter().chain(&layout.enemy_spawns) {
            assert!(layout.map.terrain(*h).unwrap().passable(), "seed {seed}");
            assert!(reached[layout.map.index(*h).unwrap()], "seed {seed}: {h:?} unreachable");
        }
        // The core accepts the layout as a battle: no unit on an impassable hex.
        let mut units = Vec::new();
        for &p in &layout.player_spawns {
            units.push(Unit::new(units.len(), "p", Team::Player, Archetype::Swordsman, p));
        }
        for &p in &layout.enemy_spawns {
            units.push(Unit::new(units.len(), "e", Team::Enemy, Archetype::Raider, p));
        }
        let _ = Battle::new(layout.map.clone(), units, seed);
        for h in layout.map.hexes() {
            kinds.insert(layout.map.terrain(h).unwrap());
        }
    }
    assert_eq!(kinds.len(), 5, "all terrain kinds appear across seeds");
}

#[test]
fn mapgen_varies_between_seeds_and_limits_obstacles() {
    let a = mapgen::generate(1, 4, 5);
    let b = mapgen::generate(2, 4, 5);
    assert!(a.map.hexes().any(|h| a.map.terrain(h) != b.map.terrain(h)));
    for seed in 0..100 {
        let l = mapgen::generate(seed, 5, 8);
        let blocked = l.map.hexes().filter(|&h| !l.map.terrain(h).unwrap().passable()).count();
        assert!(blocked * 100 <= l.map.len() * 24, "seed {seed}: {blocked} blocked");
    }
}

// ----------------------------------------------------------- battle <-> roster

#[test]
fn battle_uses_roster_units_with_wounds_and_level_bonuses() {
    let mut c = Campaign::new(11);
    c.roster[0].hp = 20;
    c.roster[1].level = 3;
    c.roster[1].hp = c.roster[1].max_hp();
    start_encounter(&mut c, 0);
    let b = c.begin_battle().expect("fight");
    let players: Vec<_> = b.units().iter().filter(|u| u.team == Team::Player).collect();
    assert_eq!(players.len(), c.deployed_ids().len());
    let u0 = players.iter().find(|u| u.roster_id == Some(c.roster[0].id)).unwrap();
    assert_eq!(u0.hp, 20, "wounded mercenaries start with reduced HP");
    assert!(u0.stats.max_hp > 20);
    let u1 = players.iter().find(|u| u.roster_id == Some(c.roster[1].id)).unwrap();
    let base = c.roster[1].archetype.stats();
    assert_eq!(u1.stats.max_hp, base.max_hp + 2 * HP_BONUS);
    assert_eq!(u1.stats.defense, base.defense + 2 * DEF_BONUS);
    assert_eq!(u1.level, 3);
    assert_eq!(u1.name, c.roster[1].name);
    // Enemies scale with the contract.
    let k = c.active.as_ref().unwrap();
    let enemies = b.units().iter().filter(|u| u.team == Team::Enemy).count();
    assert_eq!(enemies, enemy_party(k.kind, k.difficulty, k.seed).len());
    // Map came from the contract seed.
    let again = mapgen::generate(k.seed, players.len(), enemies);
    assert!(b.map().hexes().all(|h| b.map().terrain(h) == again.map.terrain(h)));
}

const HP_BONUS: i32 = tactics_core::unit::HP_PER_LEVEL;
const DEF_BONUS: i32 = tactics_core::unit::DEFENSE_PER_LEVEL;

#[test]
fn battle_result_is_applied_to_the_roster() {
    let mut saw_death = false;
    let mut saw_survivor_wound = false;
    for seed in 0..40u64 {
        let mut c = Campaign::new(seed);
        let before = c.roster.clone();
        let k = c.board[0].clone();
        start_encounter(&mut c, 0);
        let mut b = c.begin_battle().unwrap();
        assert_eq!(c.phase, Phase::Battle);
        assert_eq!(c.apply_battle(&b).err(), Some(CampaignError::BattleNotOver));
        play_battle(&mut b);
        let outcomes = b.player_outcomes();
        let report = c.apply_battle(&b).unwrap();
        let victory = report.outcome == Outcome::Victory;
        assert_eq!(c.phase, Phase::Aftermath);
        for o in &outcomes {
            let old = before.iter().find(|m| m.id == o.roster_id).unwrap();
            let r = report.mercs.iter().find(|m| m.id == o.roster_id).unwrap();
            assert_eq!(r.kills, o.kills);
            if o.dead {
                saw_death = true;
                assert!(c.merc(o.roster_id).is_none(), "the dead are removed");
                assert!(c.fallen.iter().any(|f| f.name == o.name));
                assert!(r.dead);
            } else {
                let m = c.merc(o.roster_id).unwrap();
                let xp = XP_PARTICIPATION + o.kills * kill_xp(k.difficulty) + if victory { XP_VICTORY } else { 0 };
                assert_eq!(r.xp_gained, xp);
                assert_eq!(m.xp, old.xp + xp);
                assert_eq!(m.kills, old.kills + o.kills);
                let gained_levels = m.level - old.level;
                assert_eq!(m.level, (1..=MAX_LEVEL).rev().find(|&l| m.xp >= xp_to_reach(l)).unwrap());
                assert_eq!(m.hp, (o.final_hp + gained_levels as i32 * HP_BONUS).min(m.max_hp()));
                assert_eq!(r.leveled_up(), gained_levels > 0);
                assert_eq!(r.gain, LevelBonus::between(old.level, m.level));
                if m.hp < m.max_hp() {
                    saw_survivor_wound = true;
                }
            }
        }
        // Mercenaries who stayed home are untouched.
        for m in &before {
            if !outcomes.iter().any(|o| o.roster_id == m.id) {
                assert_eq!(c.merc(m.id), Some(m));
            }
        }
        // Dead mercenaries never reappear on the roster or the recruit list.
        c.continue_after_battle().ok();
        if !c.is_over() {
            return_home(&mut c);
            for f in &c.fallen {
                assert!(c.roster.iter().all(|m| m.name != f.name));
                assert!(c.recruits.iter().all(|r| r.name != f.name));
            }
        }
    }
    assert!(saw_death && saw_survivor_wound, "40 battles should show both deaths and wounds");
}

#[test]
fn level_up_applies_stat_increases_and_wounds_persist_into_next_battle() {
    let mut c = Campaign::new(2);
    let id = c.roster[0].id;
    let (arch, hp0) = (c.roster[0].archetype, c.roster[0].max_hp());
    c.roster[0].xp = xp_to_reach(2) - 1; // one point short of level 2
    start_encounter(&mut c, 0);
    let mut b = c.begin_battle().unwrap();
    play_battle(&mut b);
    let report = c.apply_battle(&b).unwrap();
    if let Some(m) = c.merc(id) {
        assert_eq!(m.level, 2, "participation XP crosses the threshold");
        let r = report.mercs.iter().find(|r| r.id == id).unwrap();
        assert!(r.leveled_up());
        assert_eq!(r.gain, LevelBonus { hp: HP_BONUS, skill: tactics_core::unit::SKILL_PER_LEVEL, defense: DEF_BONUS });
        assert_eq!(m.max_hp(), hp0 + HP_BONUS);
        let s1 = arch.stats_at_level(1);
        let s2 = arch.stats_at_level(2);
        assert_eq!(s2.defense, s1.defense + DEF_BONUS);
        assert!(s2.melee_skill > s1.melee_skill || s2.ranged_skill > s1.ranged_skill);
        // Carry the wound into the next fight.
        if c.continue_after_battle().is_ok() && !c.is_over() {
            return_home(&mut c);
            let hp = c.merc(id).unwrap().hp;
            let k = c.board.iter().position(|k| k.difficulty == 1).unwrap_or(0);
            c.accept_contract(k).unwrap();
            travel_to_encounter(&mut c);
            let nb = c.begin_battle().unwrap();
            let u = nb.units().iter().find(|u| u.roster_id == Some(id)).expect("still deployed");
            assert_eq!(u.hp, hp, "the carried-over wound is what the next battle starts with");
        }
    }
}

// ------------------------------------------------------------------ payout etc.

#[test]
fn victory_pays_only_on_return_to_town_and_only_once() {
    let mut found = false;
    for seed in 0..60u64 {
        let mut c = Campaign::new(seed);
        // Pick the easiest contract.
        let i = (0..c.board.len()).min_by_key(|&i| c.board[i].difficulty).unwrap();
        let pay = c.board[i].pay;
        start_encounter(&mut c, i);
        let mut b = c.begin_battle().unwrap();
        play_battle(&mut b);
        let gold_before = c.gold;
        let r = c.apply_battle(&b).unwrap();
        if r.outcome != Outcome::Victory || c.is_over() {
            continue;
        }
        found = true;
        assert_eq!(c.gold, gold_before, "no pay at the battle site");
        assert_eq!(r.pay_pending, pay);
        assert!(c.active.as_ref().unwrap().completed);
        c.continue_after_battle().unwrap();
        let wages = c.daily_wages();
        let Phase::Travel(t) = c.phase else { panic!("heading home") };
        assert_eq!(t.dest, Dest::Town);
        assert_eq!(c.rest().err(), Some(CampaignError::WrongPhase), "cannot rest mid-journey");
        let g = c.gold;
        let mut paid = 0;
        for _ in 0..t.days_total {
            assert_eq!(c.contracts_completed, 0, "still unpaid on the road");
            for e in c.travel_day().unwrap() {
                if let CampaignEvent::Paid { amount } = e {
                    paid += amount;
                }
            }
        }
        assert_eq!(c.phase, Phase::Town);
        assert_eq!(paid, pay);
        assert_eq!(c.gold, g - wages * t.days_total as i32 + pay);
        assert_eq!(c.contracts_completed, 1);
        assert!(c.active.is_none());
        // Nothing more is paid while resting in town.
        let g2 = c.gold;
        let w2 = c.daily_wages();
        c.rest().unwrap();
        assert_eq!(c.gold, g2 - w2);
        assert_eq!(c.contracts_completed, 1);
        break;
    }
    assert!(found);
}

#[test]
fn retreat_and_defeat_give_no_pay() {
    // Retreat.
    let mut c = Campaign::new(4);
    start_encounter(&mut c, 0);
    c.retreat().unwrap();
    assert!(c.active.is_none());
    let before = c.gold;
    let ev = return_home(&mut c);
    assert!(!ev.iter().any(|e| matches!(e, CampaignEvent::Paid { .. })));
    assert!(c.gold < before);
    assert_eq!((c.contracts_completed, c.contracts_failed), (0, 1));

    // Defeat: badly wounded mercenaries against the hardest contract.
    let mut seen_defeat = false;
    for seed in 0..80u64 {
        let mut c = Campaign::new(seed);
        let i = (0..c.board.len()).max_by_key(|&i| c.board[i].difficulty).unwrap();
        start_encounter(&mut c, i);
        for m in &mut c.roster {
            m.hp = 1;
        }
        let mut b = c.begin_battle().unwrap();
        play_battle(&mut b);
        let r = c.apply_battle(&b).unwrap();
        if r.outcome != Outcome::Defeat {
            continue;
        }
        seen_defeat = true;
        assert_eq!(r.pay_pending, 0);
        assert!(c.active.is_none());
        assert_eq!(c.contracts_completed, 0);
        for m in &r.mercs {
            if m.dead {
                assert!(c.merc(m.id).is_none());
            }
        }
        if c.is_over() {
            c.continue_after_battle().unwrap();
            assert_eq!(c.phase, Phase::GameOver);
            assert_eq!(c.over, Some(GameOverReason::CompanyDestroyed));
        } else {
            c.continue_after_battle().unwrap();
            let g = c.gold;
            let ev = return_home(&mut c);
            assert!(!ev.iter().any(|e| matches!(e, CampaignEvent::Paid { .. })));
            assert!(c.gold <= g);
        }
        break;
    }
    assert!(seen_defeat);
}

// -------------------------------------------------------------- heal and hire

#[test]
fn resting_heals_per_day_and_caps_at_max_hp() {
    let mut c = Campaign::new(6);
    c.gold = 10_000;
    let max0 = c.roster[0].max_hp();
    c.roster[0].hp = 5;
    c.roster[1].hp = c.roster[1].max_hp() - 3;
    let wages = c.daily_wages();
    let g = c.gold;
    c.rest().unwrap();
    assert_eq!(c.roster[0].hp, 5 + REST_HEAL_PER_DAY);
    assert_eq!(c.roster[1].hp, c.roster[1].max_hp(), "capped at max");
    assert_eq!(c.gold, g - wages, "resting costs a day of wages");
    assert_eq!(c.day, 1);
    for _ in 0..20 {
        c.rest().unwrap();
    }
    assert_eq!(c.roster[0].hp, max0);
    assert!(c.roster.iter().all(|m| m.hp <= m.max_hp()));
}

#[test]
fn hiring_costs_gold_respects_cap_and_needs_funds() {
    let mut c = Campaign::new(8);
    let r = c.recruits[0].clone();
    c.gold = r.cost - 1;
    assert_eq!(c.hire(0), Err(CampaignError::NotEnoughGold));
    assert_eq!(c.roster.len(), 4);
    c.gold = r.cost + 500;
    let n = c.recruits.len();
    let id = c.hire(0).unwrap();
    assert_eq!(c.gold, 500);
    assert_eq!(c.roster.len(), 5);
    assert_eq!(c.recruits.len(), n - 1);
    let m = c.merc(id).unwrap();
    assert_eq!((m.name.as_str(), m.archetype, m.level), (r.name.as_str(), r.archetype, r.level));
    assert_eq!(m.hp, m.max_hp());
    assert_eq!(c.daily_wages(), c.roster.iter().map(|m| m.wage()).sum::<i32>());
    c.hire(0).unwrap();
    assert_eq!(c.roster.len(), ROSTER_CAP);
    assert_eq!(c.hire(0), Err(CampaignError::RosterFull));
    // At most MAX_DEPLOY are flagged for battle.
    assert!(c.roster.iter().filter(|m| m.deploy).count() <= MAX_DEPLOY);
    assert_eq!(c.deployed_ids().len(), MAX_DEPLOY);
    // Deployment toggles respect the limits.
    let off = c.roster.iter().find(|m| !m.deploy).unwrap().id;
    assert_eq!(c.set_deploy(off, true), Err(CampaignError::DeployLimit));
    let on = c.roster.iter().find(|m| m.deploy).unwrap().id;
    c.set_deploy(on, false).unwrap();
    c.set_deploy(off, true).unwrap();
    assert!(c.merc(off).unwrap().deploy && !c.merc(on).unwrap().deploy);
    // Hiring is a town action.
    c.accept_contract(0).unwrap();
    let site = c.active.as_ref().unwrap().site;
    c.depart(site).unwrap();
    assert_eq!(c.hire(0), Err(CampaignError::WrongPhase));
}

#[test]
fn contract_board_accept_and_cancel() {
    let mut c = Campaign::new(9);
    let n = c.board.len();
    let k = c.board[1].clone();
    c.accept_contract(1).unwrap();
    assert_eq!(c.active.as_ref(), Some(&k));
    assert_eq!(c.board.len(), n - 1);
    assert_eq!(c.accept_contract(0), Err(CampaignError::ContractAlreadyAccepted));
    c.cancel_contract().unwrap();
    assert!(c.active.is_none());
    assert_eq!(c.cancel_contract(), Err(CampaignError::NoContract));
    assert_eq!(c.accept_contract(99), Err(CampaignError::NoSuchItem));
}

// -------------------------------------------------------------------- game over

#[test]
fn game_over_conditions_trigger_exactly_as_documented() {
    // Broke and nobody fit to work -> bankrupt.
    let mut c = Campaign::new(1);
    c.gold = -1;
    for m in &mut c.roster {
        m.hp = 1;
    }
    let ev = c.rest().unwrap();
    assert!(ev.contains(&CampaignEvent::GameOver(GameOverReason::Bankrupt)));
    assert_eq!(c.over, Some(GameOverReason::Bankrupt));
    assert_eq!(c.phase, Phase::GameOver);
    assert_eq!(c.rest().err(), Some(CampaignError::WrongPhase));

    // In debt but one mercenary is fit (>= half HP): the company carries on.
    let mut c = Campaign::new(1);
    c.gold = -1;
    for m in c.roster.iter_mut().skip(1) {
        m.hp = 1;
    }
    c.rest().unwrap();
    assert!(!c.is_over());
    assert!(c.gold < 0);

    // Exactly half HP (before the day's healing) counts as fit; the day ends in debt.
    let mut c = Campaign::new(1);
    for m in &mut c.roster {
        m.hp = 1;
    }
    c.roster[0].hp = c.roster[0].max_hp() / 2;
    c.gold = c.daily_wages() - 1;
    c.rest().unwrap();
    assert_eq!(c.gold, -1);
    assert!(!c.is_over(), "a mercenary at half HP is fit to work");

    // Not in debt, nobody fit: fine (gold >= 0).
    let mut c = Campaign::new(1);
    c.gold = 10_000;
    for m in &mut c.roster {
        m.hp = 1;
    }
    c.rest().unwrap();
    assert!(!c.is_over());

    // Debt beyond the limit ends it even with healthy mercenaries.
    let mut c = Campaign::new(1);
    c.gold = -DEBT_LIMIT + 1;
    c.rest().unwrap();
    assert_eq!(c.over, Some(GameOverReason::Bankrupt));

    // Everyone dead -> company destroyed; the summary lists the fallen.
    let mut seen = false;
    for seed in 0..80u64 {
        let mut c = Campaign::new(seed);
        let i = (0..c.board.len()).max_by_key(|&i| c.board[i].difficulty).unwrap();
        start_encounter(&mut c, i);
        for m in &mut c.roster {
            m.hp = 1;
        }
        let mut b = c.begin_battle().unwrap();
        play_battle(&mut b);
        c.apply_battle(&b).unwrap();
        if c.roster.is_empty() {
            seen = true;
            assert_eq!(c.over, Some(GameOverReason::CompanyDestroyed));
            assert_eq!(c.phase, Phase::Aftermath, "the result is shown before the curtain falls");
            c.continue_after_battle().unwrap();
            assert_eq!(c.phase, Phase::GameOver);
            let s = c.summary();
            assert_eq!(s.survivors, 0);
            assert!(s.fallen.len() >= 4);
            assert_eq!(s.days, c.day);
            assert_eq!(s.reason, Some(GameOverReason::CompanyDestroyed));
            break;
        }
    }
    assert!(seen);
}

// ------------------------------------------------------------------ integration

#[test]
fn headless_ai_campaign_runs_three_cycles_deterministically() {
    let a = autopilot::run_headless(2024, 3);
    let b = autopilot::run_headless(2024, 3);
    assert_eq!(a, b, "same seed, same campaign");
    assert!(a.battles == 3 || a.game_over, "{a:?}");
    assert!(a.day > 0);
    for seed in 0..12 {
        let r = autopilot::run_headless(seed, 3);
        assert_eq!(r, autopilot::run_headless(seed, 3));
        assert!(r.battles <= 3);
    }
    // At least one of a handful of seeds sees a full three-contract run without game over.
    assert!((0..12).any(|s| {
        let r = autopilot::run_headless(s, 3);
        r.battles == 3 && !r.game_over && r.contracts_completed >= 1
    }));
}

#[test]
fn autopilot_steps_follow_the_phase() {
    let mut c = Campaign::new(3);
    let mut guard = 0;
    while !matches!(autopilot::next_step(&c), Step::Depart(_)) {
        match autopilot::next_step(&c) {
            Step::Accept(i) => c.accept_contract(i).unwrap(),
            Step::Hire(i) => {
                c.hire(i).unwrap();
            }
            Step::Rest => {
                c.rest().unwrap();
            }
            s => panic!("unexpected {s:?}"),
        }
        guard += 1;
        assert!(guard < 20);
    }
    let Step::Depart(site) = autopilot::next_step(&c) else { unreachable!() };
    c.depart(site).unwrap();
    assert_eq!(autopilot::next_step(&c), Step::Wait);
    while matches!(c.phase, Phase::Travel(_)) {
        c.travel_day().unwrap();
    }
    assert_eq!(autopilot::next_step(&c), Step::Fight);
}

#[test]
fn goal_text_tracks_the_phase() {
    let mut c = Campaign::new(3);
    assert!(c.goal().contains("accept a contract"));
    c.accept_contract(0).unwrap();
    let name = c.active.as_ref().unwrap().site_def().name;
    assert!(c.goal().contains(name));
    travel_to_encounter(&mut c);
    assert!(c.goal().to_lowercase().contains("fight"));
}
