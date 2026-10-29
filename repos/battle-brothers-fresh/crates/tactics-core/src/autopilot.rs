//! A simple campaign "player": decides the next strategic step from the campaign state alone.
//! Used by the app's `--autoplay-campaign` and by the headless integration tests/simulation.

use crate::ai;
use crate::battle::Battle;
use crate::campaign::{Campaign, Phase, MAX_DEPLOY, ROSTER_CAP};

#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum Step {
    /// Rest one day in town.
    Rest,
    /// Hire the recruit at this index.
    Hire(usize),
    /// Accept the board contract at this index.
    Accept(usize),
    /// Travel to the accepted contract's site.
    Depart(usize),
    Fight,
    /// Leave an empty site / flee an encounter.
    Retreat,
    Continue,
    /// Nothing to decide right now (travelling, battle running).
    Wait,
    Done,
}

/// Highest contract difficulty the company is currently strong enough to take.
fn allowed_difficulty(c: &Campaign) -> u8 {
    let ids = c.deployed_ids();
    let ms: Vec<_> = ids.iter().filter_map(|&i| c.merc(i)).collect();
    if ms.is_empty() {
        return 1;
    }
    let n = ms.len() as f32;
    let avg_level = ms.iter().map(|m| m.level as f32).sum::<f32>() / n;
    let avg_hp = ms.iter().map(|m| m.hp as f32 / m.max_hp() as f32).sum::<f32>() / n;
    if ms.len() >= 5 && avg_level >= 2.4 && avg_hp >= 0.7 {
        3
    } else if ms.len() >= 4 && avg_hp >= 0.6 && (avg_level >= 1.6 || ms.len() >= 5) {
        2
    } else {
        1
    }
}

pub fn next_step(c: &Campaign) -> Step {
    match c.phase {
        Phase::GameOver => Step::Done,
        Phase::Travel(_) | Phase::Battle => Step::Wait,
        Phase::Encounter => Step::Fight,
        Phase::Empty => Step::Retreat,
        Phase::Aftermath => Step::Continue,
        Phase::Town => town_step(c),
    }
}

fn town_step(c: &Campaign) -> Step {
    let wages = c.daily_wages();
    // Heal up first, as long as the wages stay comfortably covered.
    let deployed: Vec<_> = c.deployed_ids().into_iter().filter_map(|i| c.merc(i)).collect();
    let hurt = deployed.iter().any(|m| m.hp * 4 < m.max_hp() * 3);
    if hurt && c.gold > wages * 3 && c.active.as_ref().is_none_or(|a| a.completed || c.day < a.deadline.saturating_sub(a.site_def().days + 1)) {
        return Step::Rest;
    }
    // Grow the company while a reserve remains.
    if c.roster.len() < ROSTER_CAP.min(MAX_DEPLOY + 1) {
        let reserve = 120;
        let best = c
            .recruits
            .iter()
            .enumerate()
            .filter(|(_, r)| c.gold - r.cost >= reserve)
            .max_by_key(|(i, r)| (r.level, std::cmp::Reverse(r.cost), std::cmp::Reverse(*i)));
        if let Some((i, _)) = best {
            return Step::Hire(i);
        }
    }
    match &c.active {
        Some(a) => Step::Depart(a.site),
        None => {
            let cap = allowed_difficulty(c);
            let pick = c
                .board
                .iter()
                .enumerate()
                .filter(|(_, k)| k.difficulty <= cap)
                .max_by_key(|(i, k)| (k.pay - 25 * k.site_def().days as i32, std::cmp::Reverse(*i)))
                .or_else(|| c.board.iter().enumerate().min_by_key(|(i, k)| (k.difficulty, *i)));
            match pick {
                Some((i, _)) => Step::Accept(i),
                None => Step::Rest,
            }
        }
    }
}

/// Play a battle to the end with the built-in AI on both sides.
pub fn play_battle(battle: &mut Battle) {
    let mut guard = 0;
    while battle.outcome().is_none() && guard < 10_000 {
        let cmd = ai::decide(battle);
        battle.apply(cmd).expect("AI commands are legal");
        guard += 1;
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RunSummary {
    pub battles: u32,
    pub victories: u32,
    pub contracts_completed: u32,
    pub day: u32,
    pub gold: i32,
    pub roster_levels: Vec<u32>,
    pub fallen: Vec<String>,
    pub game_over: bool,
    /// Battles fought / won per contract difficulty (index 0 = one skull).
    pub fights: [u32; 3],
    pub wins: [u32; 3],
}

/// Run the autopilot headlessly until `battles` battles have been fought and the company is back
/// in town (or the campaign ends). Deterministic in `seed`.
pub fn run_headless(seed: u64, battles: u32) -> RunSummary {
    let mut c = Campaign::new(seed);
    let mut victories = 0;
    let (mut fights, mut wins) = ([0u32; 3], [0u32; 3]);
    for _ in 0..100_000 {
        match next_step(&c) {
            Step::Done => break,
            Step::Rest => drop(c.rest().expect("town")),
            Step::Hire(i) => drop(c.hire(i).expect("affordable")),
            Step::Accept(i) => c.accept_contract(i).expect("board entry"),
            Step::Depart(site) => {
                if c.depart(site).is_err() {
                    // The contract lapsed while we dallied; pick another next step.
                    continue;
                }
            }
            Step::Wait => match c.phase {
                Phase::Travel(_) => drop(c.travel_day().expect("travelling")),
                _ => unreachable!("only travel waits in the headless runner"),
            },
            Step::Fight => {
                let d = c.active.as_ref().map_or(1, |a| a.difficulty) as usize - 1;
                let mut b = c.begin_battle().expect("encounter");
                play_battle(&mut b);
                let r = c.apply_battle(&b).expect("battle over");
                fights[d] += 1;
                if r.outcome == crate::Outcome::Victory {
                    victories += 1;
                    wins[d] += 1;
                }
            }
            Step::Retreat => c.retreat().expect("at a site"),
            Step::Continue => c.continue_after_battle().expect("aftermath"),
        }
        if c.battles >= battles && c.phase == Phase::Town {
            break;
        }
    }
    RunSummary {
        battles: c.battles,
        victories,
        contracts_completed: c.contracts_completed,
        day: c.day,
        gold: c.gold,
        roster_levels: c.roster.iter().map(|m| m.level).collect(),
        fallen: c.fallen.iter().map(|f| f.name.clone()).collect(),
        game_over: c.is_over(),
        fights,
        wins,
    }
}
