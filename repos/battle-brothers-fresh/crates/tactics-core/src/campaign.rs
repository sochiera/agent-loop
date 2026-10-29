//! The strategic layer: company, roster, contracts, travel, wages, rewards, XP, healing,
//! hiring and game over. Graphics-free and deterministic from the campaign seed.
//!
//! The campaign never re-implements combat: [`Campaign::begin_battle`] builds a regular
//! [`Battle`] from the roster and [`Campaign::apply_battle`] reads the per-unit outcome back.
//!
//! ## Rules summary
//! * One day passes per rest day or travel day. Every day the company pays the sum of all
//!   mercenary wages; gold may go negative (debt).
//! * Resting in town heals every mercenary [`REST_HEAL_PER_DAY`] HP (capped at max HP).
//! * Travel is abstract: town <-> site only, `SiteDef::days` days each way.
//! * Contract pay is collected only when the company is back in town after a victory.
//! * **Game over** (checked after every day and after every battle): the company is destroyed
//!   when no mercenary is alive; it is bankrupt when `gold < 0` and no living mercenary is fit to
//!   work (HP at least half of max), or when debt exceeds [`DEBT_LIMIT`].

use crate::battle::{Battle, Outcome};
use crate::mapgen;
use crate::rng::Rng;
use crate::unit::{Archetype, LevelBonus, Team, Unit};

pub const START_GOLD: i32 = 300;
pub const ROSTER_CAP: usize = 6;
pub const MAX_DEPLOY: usize = 5;
pub const BOARD_SIZE: usize = 3;
pub const RECRUIT_POOL: usize = 3;
pub const REST_HEAL_PER_DAY: i32 = 12;
pub const DEBT_LIMIT: i32 = 300;
pub const XP_PARTICIPATION: u32 = 10;
pub const XP_VICTORY: u32 = 10;
pub const MAX_LEVEL: u32 = 8;
/// Extra days beyond the travel time that a contract stays open.
const DEADLINE_SLACK: u32 = 5;
/// A fresh set of recruits walks into town every this many days.
const RECRUIT_REFRESH_DAYS: u32 = 4;
/// Size of the overworld map in abstract map units (the app scales it).
pub const MAP_UNITS_W: f32 = 1000.0;
pub const MAP_UNITS_H: f32 = 700.0;

// ------------------------------------------------------------------- world data

#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum SiteKind {
    Mill,
    Gallows,
    Ruins,
    Camp,
    Fort,
}

#[derive(Copy, Clone, Debug)]
pub struct SiteDef {
    pub name: &'static str,
    pub kind: SiteKind,
    /// Travel days from the home town (one way).
    pub days: u32,
    /// Position on the overworld map, in map units.
    pub pos: (f32, f32),
    /// Typical contract difficulty (1-3) at this site.
    pub base_difficulty: u8,
    pub blurb: &'static str,
}

pub const TOWN_NAME: &str = "Harrowgate";
pub const TOWN_POS: (f32, f32) = (200.0, 430.0);

pub const SITES: [SiteDef; 5] = [
    SiteDef { name: "Mill Ford", kind: SiteKind::Mill, days: 1, pos: (340.0, 360.0), base_difficulty: 1, blurb: "A river crossing plagued by thieves" },
    SiteDef { name: "Gallows Hill", kind: SiteKind::Gallows, days: 2, pos: (420.0, 590.0), base_difficulty: 1, blurb: "A windswept hill with a bad name" },
    SiteDef { name: "Ashen Ruins", kind: SiteKind::Ruins, days: 3, pos: (520.0, 200.0), base_difficulty: 2, blurb: "A burnt manor, now a hideout" },
    SiteDef { name: "Black Pine Camp", kind: SiteKind::Camp, days: 4, pos: (760.0, 560.0), base_difficulty: 2, blurb: "A palisaded camp deep in the pines" },
    SiteDef { name: "Stonewatch Pass", kind: SiteKind::Fort, days: 5, pos: (880.0, 160.0), base_difficulty: 3, blurb: "A ruined watchtower above the pass" },
];

#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum EnemyKind {
    Bandits,
    Ambushers,
    Warband,
}

impl EnemyKind {
    pub fn label(self) -> &'static str {
        match self {
            EnemyKind::Bandits => "Bandit gang",
            EnemyKind::Ambushers => "Archer ambush",
            EnemyKind::Warband => "Brute warband",
        }
    }

    /// (raiders, bandit archers, brutes) for a difficulty of 1-3.
    fn mix(self, difficulty: u8) -> (usize, usize, usize) {
        let d = difficulty.clamp(1, 3) as usize - 1;
        let table: [(usize, usize, usize); 3] = match self {
            EnemyKind::Bandits => [(2, 1, 0), (3, 1, 1), (3, 2, 2)],
            EnemyKind::Ambushers => [(1, 2, 0), (2, 3, 0), (2, 4, 1)],
            EnemyKind::Warband => [(1, 1, 1), (2, 1, 1), (3, 1, 3)],
        };
        table[d]
    }
}

/// Enemy archetypes for a contract, front line first. Deterministic in `seed`.
pub fn enemy_party(kind: EnemyKind, difficulty: u8, seed: u64) -> Vec<Archetype> {
    let mut rng = Rng::new(seed ^ 0x656E_656D_7900_0001);
    let (mut raiders, archers, brutes) = kind.mix(difficulty);
    if difficulty >= 2 && rng.below(2) == 0 {
        raiders += 1;
    }
    let mut v = Vec::new();
    v.extend(std::iter::repeat_n(Archetype::Brute, brutes));
    v.extend(std::iter::repeat_n(Archetype::Raider, raiders));
    v.extend(std::iter::repeat_n(Archetype::BanditArcher, archers));
    v
}

// --------------------------------------------------------------------- roster

pub fn wage_for(archetype: Archetype, level: u32) -> i32 {
    let base = match archetype {
        Archetype::Archer => 4,
        _ => 5,
    };
    base + level.saturating_sub(1) as i32
}

pub fn hire_cost_for(archetype: Archetype, level: u32) -> i32 {
    wage_for(archetype, level) * 16
}

/// Total XP needed to *reach* `level` (level 1 needs 0).
pub fn xp_to_reach(level: u32) -> u32 {
    if level <= 1 {
        0
    } else {
        10 * (level - 1) * (level + 2)
    }
}

pub fn kill_xp(difficulty: u8) -> u32 {
    12 + 4 * difficulty as u32
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Mercenary {
    pub id: u32,
    pub name: String,
    pub archetype: Archetype,
    pub level: u32,
    pub xp: u32,
    pub hp: i32,
    /// Career kills.
    pub kills: u32,
    /// Whether this mercenary goes into the next battle.
    pub deploy: bool,
}

impl Mercenary {
    pub fn max_hp(&self) -> i32 {
        self.archetype.stats_at_level(self.level).max_hp
    }

    pub fn wage(&self) -> i32 {
        wage_for(self.archetype, self.level)
    }

    pub fn bonus(&self) -> LevelBonus {
        LevelBonus::for_level(self.level)
    }

    /// XP threshold of the next level, or `None` at the level cap.
    pub fn next_level_xp(&self) -> Option<u32> {
        (self.level < MAX_LEVEL).then(|| xp_to_reach(self.level + 1))
    }

    pub fn wounded(&self) -> bool {
        self.hp < self.max_hp()
    }

    /// Fit enough to take a job: at least half HP.
    pub fn fit_for_work(&self) -> bool {
        self.hp * 2 >= self.max_hp()
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Fallen {
    pub name: String,
    pub archetype: Archetype,
    pub level: u32,
    pub day: u32,
    pub kills: u32,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Recruit {
    pub name: String,
    pub archetype: Archetype,
    pub level: u32,
    pub cost: i32,
    pub wage: i32,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Contract {
    pub id: u32,
    pub site: usize,
    pub kind: EnemyKind,
    /// 1-3 skulls.
    pub difficulty: u8,
    pub pay: i32,
    /// Last day on which the company may still *arrive* at the site.
    pub deadline: u32,
    pub seed: u64,
    /// Victory achieved; pay is due on return to town.
    pub completed: bool,
}

impl Contract {
    pub fn site_def(&self) -> &'static SiteDef {
        &SITES[self.site]
    }
}

// ---------------------------------------------------------------------- phases

#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum Dest {
    Site,
    Town,
}

#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub struct Travel {
    pub dest: Dest,
    pub site: usize,
    pub days_total: u32,
    pub days_done: u32,
}

impl Travel {
    /// Fraction of the way from the town to the site (0 = town, 1 = site), whatever the direction.
    pub fn progress_from_town(&self, partial_day: f32) -> f32 {
        let f = ((self.days_done as f32 + partial_day) / self.days_total.max(1) as f32).clamp(0.0, 1.0);
        match self.dest {
            Dest::Site => f,
            Dest::Town => 1.0 - f,
        }
    }
}

#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum Phase {
    Town,
    Travel(Travel),
    /// Arrived at the accepted contract's site: Fight or Retreat.
    Encounter,
    /// Arrived at a site with no contract: nothing to fight.
    Empty,
    Battle,
    /// Battle finished and applied; waiting for the player to continue.
    Aftermath,
    GameOver,
}

#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum GameOverReason {
    CompanyDestroyed,
    Bankrupt,
}

impl GameOverReason {
    pub fn describe(self) -> &'static str {
        match self {
            GameOverReason::CompanyDestroyed => "The company was destroyed: no mercenary is left alive.",
            GameOverReason::Bankrupt => "The company went bankrupt: in debt, with no one fit to work.",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum CampaignEvent {
    DayPassed { day: u32, wages: i32 },
    ContractLapsed { site: usize },
    Arrived { site: usize, encounter: bool },
    ArrivedHome,
    Paid { amount: i32 },
    GameOver(GameOverReason),
}

#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum CampaignError {
    WrongPhase,
    NoSuchItem,
    NotEnoughGold,
    RosterFull,
    ContractAlreadyAccepted,
    NoContract,
    TooLate,
    DeployLimit,
    BattleNotOver,
}

impl CampaignError {
    pub fn describe(self) -> &'static str {
        match self {
            CampaignError::WrongPhase => "Not possible right now",
            CampaignError::NoSuchItem => "No such entry",
            CampaignError::NotEnoughGold => "Not enough gold",
            CampaignError::RosterFull => "The roster is full",
            CampaignError::ContractAlreadyAccepted => "You already hold a contract",
            CampaignError::NoContract => "No contract accepted",
            CampaignError::TooLate => "Too late: the contract has lapsed",
            CampaignError::DeployLimit => "Deployment limit reached",
            CampaignError::BattleNotOver => "The battle is not over",
        }
    }
}

// --------------------------------------------------------------------- reports

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MercReport {
    pub id: u32,
    pub name: String,
    pub archetype: Archetype,
    pub hp_before: i32,
    pub hp_after: i32,
    pub max_hp: i32,
    pub dead: bool,
    pub kills: u32,
    pub xp_gained: u32,
    pub level_before: u32,
    pub level_after: u32,
    /// Stat increase from the levels gained in this battle.
    pub gain: LevelBonus,
}

impl MercReport {
    pub fn leveled_up(&self) -> bool {
        self.level_after > self.level_before
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BattleReport {
    pub outcome: Outcome,
    pub timed_out: bool,
    pub rounds: u32,
    pub enemies_down: usize,
    pub enemies_total: usize,
    pub mercs: Vec<MercReport>,
    /// Gold that will be paid on return to town (0 unless the contract was won).
    pub pay_pending: i32,
}

#[derive(Clone, Debug)]
pub struct Summary {
    pub company: String,
    pub days: u32,
    pub contracts_completed: u32,
    pub contracts_failed: u32,
    pub gold: i32,
    pub battles: u32,
    pub fallen: Vec<Fallen>,
    pub survivors: usize,
    pub reason: Option<GameOverReason>,
}

// -------------------------------------------------------------------- campaign

const NAMES: [&str; 28] = [
    "Brannoch", "Teague", "Wren", "Halvard", "Isolde", "Corvin", "Maeve", "Osric", "Perrin", "Sable", "Tamsin", "Ulric",
    "Varyn", "Wystan", "Yorick", "Brisa", "Cadoc", "Dunstan", "Elsbeth", "Fenwick", "Garrick", "Hesper", "Ingram",
    "Joss", "Kestrel", "Lysander", "Morwen", "Nerys",
];

const COMPANY_NAMES: [&str; 6] = [
    "The Ashen Lanterns",
    "The Grey Standard",
    "The Hollow Crows",
    "The Silver Thorns",
    "The Iron Marigolds",
    "The Red Heron Company",
];

const HIREABLE: [Archetype; 4] = [Archetype::Swordsman, Archetype::ShieldBearer, Archetype::Archer, Archetype::Halberdier];

#[derive(Clone, Debug)]
pub struct Campaign {
    seed: u64,
    rng: Rng,
    pub company: String,
    pub gold: i32,
    pub day: u32,
    pub roster: Vec<Mercenary>,
    pub fallen: Vec<Fallen>,
    pub board: Vec<Contract>,
    pub recruits: Vec<Recruit>,
    /// The accepted contract (also kept after victory until the pay is collected).
    pub active: Option<Contract>,
    pub phase: Phase,
    /// Site the company currently stands at (encounter, empty site, battle, aftermath).
    pub at_site: Option<usize>,
    pub last_report: Option<BattleReport>,
    pub contracts_completed: u32,
    pub contracts_failed: u32,
    pub battles: u32,
    pub over: Option<GameOverReason>,
    next_merc_id: u32,
    next_contract_id: u32,
}

impl Campaign {
    pub fn new(seed: u64) -> Campaign {
        let mut rng = Rng::new(seed ^ 0x6361_6D70_0000_0001);
        let company = COMPANY_NAMES[rng.below(COMPANY_NAMES.len() as u32) as usize].to_string();
        let mut c = Campaign {
            seed,
            rng,
            company,
            gold: START_GOLD,
            day: 0,
            roster: Vec::new(),
            fallen: Vec::new(),
            board: Vec::new(),
            recruits: Vec::new(),
            active: None,
            phase: Phase::Town,
            at_site: None,
            last_report: None,
            contracts_completed: 0,
            contracts_failed: 0,
            battles: 0,
            over: None,
            next_merc_id: 1,
            next_contract_id: 1,
        };
        for arch in [Archetype::Swordsman, Archetype::ShieldBearer, Archetype::Archer, Archetype::Halberdier] {
            let name = c.fresh_name();
            c.push_merc(name, arch, 1);
        }
        c.restock_board();
        c.restock_recruits(true);
        c
    }

    pub fn seed(&self) -> u64 {
        self.seed
    }

    // ----- queries ---------------------------------------------------------

    pub fn daily_wages(&self) -> i32 {
        self.roster.iter().map(Mercenary::wage).sum()
    }

    pub fn merc(&self, id: u32) -> Option<&Mercenary> {
        self.roster.iter().find(|m| m.id == id)
    }

    /// Roster ids that go into the next battle (flagged, at most [`MAX_DEPLOY`]).
    pub fn deployed_ids(&self) -> Vec<u32> {
        let flagged: Vec<u32> = self.roster.iter().filter(|m| m.deploy).map(|m| m.id).take(MAX_DEPLOY).collect();
        if flagged.is_empty() {
            self.roster.iter().map(|m| m.id).take(MAX_DEPLOY).collect()
        } else {
            flagged
        }
    }

    pub fn is_over(&self) -> bool {
        self.over.is_some()
    }

    /// Gold the pending (won) contract will pay on return to town.
    pub fn pay_pending(&self) -> i32 {
        self.active.as_ref().filter(|c| c.completed).map_or(0, |c| c.pay)
    }

    pub fn summary(&self) -> Summary {
        Summary {
            company: self.company.clone(),
            days: self.day,
            contracts_completed: self.contracts_completed,
            contracts_failed: self.contracts_failed,
            gold: self.gold,
            battles: self.battles,
            fallen: self.fallen.clone(),
            survivors: self.roster.len(),
            reason: self.over,
        }
    }

    /// One-line statement of what the player should do next.
    pub fn goal(&self) -> String {
        match self.phase {
            Phase::GameOver => "The campaign is over.".into(),
            Phase::Town => match &self.active {
                Some(c) if c.completed => "Contract won - return to town for pay.".into(),
                Some(c) => format!(
                    "GOAL: travel to {} ({} days) and beat the {} - arrive by day {}",
                    c.site_def().name,
                    c.site_def().days,
                    c.kind.label().to_lowercase(),
                    c.deadline
                ),
                None => "GOAL: accept a contract on the town's contract board.".into(),
            },
            Phase::Travel(t) => match t.dest {
                Dest::Site => format!(
                    "Travelling to {}: day {} of {}. Wages are paid daily.",
                    SITES[t.site].name,
                    t.days_done + 1,
                    t.days_total
                ),
                Dest::Town => format!("Returning to {}: day {} of {}.", TOWN_NAME, t.days_done + 1, t.days_total),
            },
            Phase::Encounter => "Enemies ahead! Fight, or retreat to town without pay.".into(),
            Phase::Empty => "Nothing here. Return to town.".into(),
            Phase::Battle => "Battle in progress.".into(),
            Phase::Aftermath => "The battle is over. Continue.".into(),
        }
    }

    // ----- internal helpers --------------------------------------------------

    fn push_merc(&mut self, name: String, archetype: Archetype, level: u32) -> u32 {
        let id = self.next_merc_id;
        self.next_merc_id += 1;
        let deploy = self.roster.iter().filter(|m| m.deploy).count() < MAX_DEPLOY;
        let mut m = Mercenary { id, name, archetype, level, xp: xp_to_reach(level), hp: 0, kills: 0, deploy };
        m.hp = m.max_hp();
        self.roster.push(m);
        id
    }

    fn fresh_name(&mut self) -> String {
        let used = |n: &str, c: &Campaign| {
            c.roster.iter().any(|m| m.name == n)
                || c.fallen.iter().any(|f| f.name == n)
                || c.recruits.iter().any(|r| r.name == n)
        };
        let free: Vec<&str> = NAMES.iter().copied().filter(|n| !used(n, self)).collect();
        if !free.is_empty() {
            return free[self.rng.below(free.len() as u32) as usize].to_string();
        }
        // Every base name is taken (the fallen keep theirs): add a Roman suffix, II, III, ...
        // without bound. Each round scans all bases, so it ends after at most
        // (names in use + 1) rounds.
        let start = self.rng.below(NAMES.len() as u32) as usize;
        for suffix in 2u32.. {
            for k in 0..NAMES.len() {
                let name = format!("{} {}", NAMES[(start + k) % NAMES.len()], roman(suffix));
                if !used(&name, self) {
                    return name;
                }
            }
        }
        unreachable!("the suffix range is unbounded")
    }

    fn normalize_deploy(&mut self) {
        let flagged = self.roster.iter().filter(|m| m.deploy).count();
        if flagged == 0 {
            for m in self.roster.iter_mut().take(MAX_DEPLOY) {
                m.deploy = true;
            }
        } else if flagged > MAX_DEPLOY {
            let mut seen = 0;
            for m in self.roster.iter_mut().filter(|m| m.deploy) {
                seen += 1;
                if seen > MAX_DEPLOY {
                    m.deploy = false;
                }
            }
        }
    }

    fn new_seed(&mut self) -> u64 {
        ((self.rng.next_u32() as u64) << 32) | self.rng.next_u32() as u64
    }

    fn generate_contract(&mut self) -> Contract {
        let taken: Vec<usize> = self.board.iter().map(|c| c.site).chain(self.active.as_ref().map(|c| c.site)).collect();
        let free: Vec<usize> = (0..SITES.len()).filter(|s| !taken.contains(s)).collect();
        let site = if free.is_empty() {
            self.rng.below(SITES.len() as u32) as usize
        } else {
            free[self.rng.below(free.len() as u32) as usize]
        };
        let def = &SITES[site];
        let mut difficulty = def.base_difficulty;
        match self.rng.below(4) {
            0 if difficulty > 1 => difficulty -= 1,
            3 if difficulty < 3 => difficulty += 1,
            _ => {}
        }
        let kind = [EnemyKind::Bandits, EnemyKind::Ambushers, EnemyKind::Warband][self.rng.below(3) as usize];
        let pay = 100 + 100 * difficulty as i32 + 20 * def.days as i32 + 5 * self.rng.below(5) as i32;
        let deadline = self.day + def.days + DEADLINE_SLACK + self.rng.below(4);
        let id = self.next_contract_id;
        self.next_contract_id += 1;
        Contract { id, site, kind, difficulty, pay, deadline, seed: self.new_seed(), completed: false }
    }

    fn lapsed(&self, c: &Contract) -> bool {
        self.day + SITES[c.site].days > c.deadline
    }

    fn restock_board(&mut self) {
        let day = self.day;
        self.board.retain(|c| day + SITES[c.site].days <= c.deadline);
        while self.board.len() < BOARD_SIZE {
            let c = self.generate_contract();
            self.board.push(c);
        }
    }

    fn make_recruit(&mut self) -> Recruit {
        let archetype = HIREABLE[self.rng.below(HIREABLE.len() as u32) as usize];
        let level = if self.rng.below(4) == 0 { 2 } else { 1 };
        let name = self.fresh_name();
        Recruit { name, archetype, level, cost: hire_cost_for(archetype, level), wage: wage_for(archetype, level) }
    }

    fn restock_recruits(&mut self, replace_all: bool) {
        if replace_all {
            self.recruits.clear();
        } else if self.recruits.len() >= RECRUIT_POOL {
            let i = self.rng.below(self.recruits.len() as u32) as usize;
            self.recruits.remove(i);
        }
        while self.recruits.len() < RECRUIT_POOL {
            let r = self.make_recruit();
            self.recruits.push(r);
        }
    }

    /// Advance the calendar by one day: pay wages, heal if resting, lapse contracts, restock,
    /// check for game over.
    fn pass_day(&mut self, resting: bool, events: &mut Vec<CampaignEvent>) {
        self.day += 1;
        let wages = self.daily_wages();
        self.gold -= wages;
        if resting {
            for m in &mut self.roster {
                m.hp = (m.hp + REST_HEAL_PER_DAY).min(m.max_hp());
            }
        }
        events.push(CampaignEvent::DayPassed { day: self.day, wages });
        if self.phase == Phase::Town {
            if let Some(c) = self.active.as_ref().filter(|c| !c.completed && self.lapsed(c)) {
                events.push(CampaignEvent::ContractLapsed { site: c.site });
                self.active = None;
                self.contracts_failed += 1;
            }
        }
        self.restock_board();
        if self.day.is_multiple_of(RECRUIT_REFRESH_DAYS) && self.phase == Phase::Town {
            self.restock_recruits(false);
        }
        self.check_game_over(events);
    }

    fn check_game_over(&mut self, events: &mut Vec<CampaignEvent>) {
        if self.over.is_some() {
            return;
        }
        let reason = if self.roster.is_empty() {
            Some(GameOverReason::CompanyDestroyed)
        } else if self.gold < -DEBT_LIMIT || (self.gold < 0 && !self.roster.iter().any(Mercenary::fit_for_work)) {
            Some(GameOverReason::Bankrupt)
        } else {
            None
        };
        if let Some(r) = reason {
            self.over = Some(r);
            events.push(CampaignEvent::GameOver(r));
            // A finished battle is still reported before the curtain falls.
            if self.phase != Phase::Aftermath {
                self.phase = Phase::GameOver;
            }
        }
    }

    // ----- town actions ------------------------------------------------------

    fn require_town(&self) -> Result<(), CampaignError> {
        if self.phase == Phase::Town {
            Ok(())
        } else {
            Err(CampaignError::WrongPhase)
        }
    }

    pub fn accept_contract(&mut self, board_index: usize) -> Result<(), CampaignError> {
        self.require_town()?;
        if self.active.is_some() {
            return Err(CampaignError::ContractAlreadyAccepted);
        }
        if board_index >= self.board.len() {
            return Err(CampaignError::NoSuchItem);
        }
        let c = self.board.remove(board_index);
        self.active = Some(c);
        Ok(())
    }

    /// Drop the accepted contract (only before leaving town and before it was won).
    pub fn cancel_contract(&mut self) -> Result<(), CampaignError> {
        self.require_town()?;
        match &self.active {
            Some(c) if !c.completed => {
                self.active = None;
                self.restock_board();
                Ok(())
            }
            _ => Err(CampaignError::NoContract),
        }
    }

    /// Rest one day in town: wages are paid, everyone heals.
    pub fn rest(&mut self) -> Result<Vec<CampaignEvent>, CampaignError> {
        self.require_town()?;
        let mut ev = Vec::new();
        self.pass_day(true, &mut ev);
        Ok(ev)
    }

    pub fn hire(&mut self, recruit_index: usize) -> Result<u32, CampaignError> {
        self.require_town()?;
        let Some(r) = self.recruits.get(recruit_index) else { return Err(CampaignError::NoSuchItem) };
        if self.roster.len() >= ROSTER_CAP {
            return Err(CampaignError::RosterFull);
        }
        if self.gold < r.cost {
            return Err(CampaignError::NotEnoughGold);
        }
        let r = self.recruits.remove(recruit_index);
        self.gold -= r.cost;
        let id = self.push_merc(r.name, r.archetype, r.level);
        Ok(id)
    }

    /// Toggle whether a mercenary joins the next battle.
    pub fn set_deploy(&mut self, id: u32, on: bool) -> Result<(), CampaignError> {
        self.require_town()?;
        let deployed = self.roster.iter().filter(|m| m.deploy).count();
        let Some(m) = self.roster.iter_mut().find(|m| m.id == id) else { return Err(CampaignError::NoSuchItem) };
        if on && !m.deploy {
            if deployed >= MAX_DEPLOY {
                return Err(CampaignError::DeployLimit);
            }
            m.deploy = true;
        } else if !on && m.deploy {
            if deployed <= 1 {
                return Err(CampaignError::DeployLimit);
            }
            m.deploy = false;
        }
        Ok(())
    }

    // ----- travel ------------------------------------------------------------

    /// Start travelling from town to `site`. Any site may be visited; only the accepted
    /// contract's site holds an encounter.
    pub fn depart(&mut self, site: usize) -> Result<(), CampaignError> {
        self.require_town()?;
        if site >= SITES.len() {
            return Err(CampaignError::NoSuchItem);
        }
        if let Some(c) = self.active.as_ref().filter(|c| c.site == site && !c.completed) {
            if self.lapsed(c) {
                self.active = None;
                self.contracts_failed += 1;
                return Err(CampaignError::TooLate);
            }
        }
        self.phase = Phase::Travel(Travel { dest: Dest::Site, site, days_total: SITES[site].days, days_done: 0 });
        Ok(())
    }

    fn start_return(&mut self, site: usize) {
        self.phase = Phase::Travel(Travel { dest: Dest::Town, site, days_total: SITES[site].days, days_done: 0 });
    }

    /// Advance a journey by one day (wages are paid, arrival is resolved).
    pub fn travel_day(&mut self) -> Result<Vec<CampaignEvent>, CampaignError> {
        let Phase::Travel(mut t) = self.phase else { return Err(CampaignError::WrongPhase) };
        let mut ev = Vec::new();
        self.pass_day(false, &mut ev);
        if self.over.is_some() {
            return Ok(ev);
        }
        t.days_done += 1;
        if t.days_done < t.days_total {
            self.phase = Phase::Travel(t);
            return Ok(ev);
        }
        match t.dest {
            Dest::Site => {
                self.at_site = Some(t.site);
                let fight = self.active.as_ref().is_some_and(|c| c.site == t.site && !c.completed);
                self.phase = if fight { Phase::Encounter } else { Phase::Empty };
                ev.push(CampaignEvent::Arrived { site: t.site, encounter: fight });
            }
            Dest::Town => {
                self.at_site = None;
                self.phase = Phase::Town;
                ev.push(CampaignEvent::ArrivedHome);
                if let Some(c) = self.active.take_if(|c| c.completed) {
                    self.gold += c.pay;
                    self.contracts_completed += 1;
                    ev.push(CampaignEvent::Paid { amount: c.pay });
                }
                self.restock_board();
                self.restock_recruits(true);
                self.check_game_over(&mut ev);
            }
        }
        Ok(ev)
    }

    /// Give up the encounter (or an empty site) and head home. A held contract is forfeited, no pay.
    pub fn retreat(&mut self) -> Result<(), CampaignError> {
        if !matches!(self.phase, Phase::Encounter | Phase::Empty) {
            return Err(CampaignError::WrongPhase);
        }
        let site = self.at_site.expect("at a site");
        if self.active.take().is_some() {
            self.contracts_failed += 1;
        }
        self.start_return(site);
        Ok(())
    }

    // ----- battle ------------------------------------------------------------

    /// Fight the encounter: builds the battle from the roster and the contract's seed.
    pub fn begin_battle(&mut self) -> Result<Battle, CampaignError> {
        if self.phase != Phase::Encounter {
            return Err(CampaignError::WrongPhase);
        }
        let contract = self.active.clone().ok_or(CampaignError::NoContract)?;
        self.normalize_deploy();
        let ids = self.deployed_ids();
        let enemies = enemy_party(contract.kind, contract.difficulty, contract.seed);
        let layout = mapgen::generate(contract.seed, ids.len(), enemies.len());
        debug_assert_eq!(mapgen::verify(&layout), Ok(()));
        let mut units = Vec::new();
        for (&id, &pos) in ids.iter().zip(&layout.player_spawns) {
            let m = self.merc(id).expect("deployed id is on the roster");
            units.push(Unit::mercenary(units.len(), m.id, &m.name, m.archetype, m.level, m.hp, pos));
        }
        let mut counts = std::collections::HashMap::<Archetype, usize>::new();
        for (&arch, &pos) in enemies.iter().zip(&layout.enemy_spawns) {
            let n = counts.entry(arch).or_insert(0);
            *n += 1;
            units.push(Unit::new(units.len(), &format!("{} {}", arch.label(), n), Team::Enemy, arch, pos));
        }
        self.phase = Phase::Battle;
        Ok(Battle::new(layout.map, units, contract.seed ^ 0xB477_1E5E))
    }

    /// Apply a finished battle to the roster. The dead are removed for good, survivors keep
    /// their remaining HP, XP and level-ups are awarded.
    pub fn apply_battle(&mut self, battle: &Battle) -> Result<BattleReport, CampaignError> {
        if self.phase != Phase::Battle {
            return Err(CampaignError::WrongPhase);
        }
        let outcome = battle.outcome().ok_or(CampaignError::BattleNotOver)?;
        let victory = outcome == Outcome::Victory;
        let difficulty = self.active.as_ref().map_or(1, |c| c.difficulty);
        let mut mercs = Vec::new();
        for o in battle.player_outcomes() {
            let Some(idx) = self.roster.iter().position(|m| m.id == o.roster_id) else { continue };
            let m = &mut self.roster[idx];
            let (hp_before, level_before) = (m.hp, m.level);
            let mut report = MercReport {
                id: m.id,
                name: m.name.clone(),
                archetype: m.archetype,
                hp_before,
                hp_after: 0,
                max_hp: m.max_hp(),
                dead: o.dead,
                kills: o.kills,
                xp_gained: 0,
                level_before,
                level_after: level_before,
                gain: LevelBonus::default(),
            };
            if o.dead {
                let dead = self.roster.remove(idx);
                self.fallen.push(Fallen { name: dead.name, archetype: dead.archetype, level: dead.level, day: self.day, kills: dead.kills + o.kills });
            } else {
                let xp = XP_PARTICIPATION + o.kills * kill_xp(difficulty) + if victory { XP_VICTORY } else { 0 };
                m.kills += o.kills;
                m.xp += xp;
                m.hp = o.final_hp.clamp(1, m.max_hp());
                while m.level < MAX_LEVEL && m.xp >= xp_to_reach(m.level + 1) {
                    m.level += 1;
                    m.hp += crate::unit::HP_PER_LEVEL;
                }
                m.hp = m.hp.min(m.max_hp());
                report.hp_after = m.hp;
                report.max_hp = m.max_hp();
                report.xp_gained = xp;
                report.level_after = m.level;
                report.gain = LevelBonus::between(level_before, m.level);
            }
            mercs.push(report);
        }
        self.normalize_deploy();
        self.battles += 1;
        let pay_pending = if victory {
            if let Some(c) = self.active.as_mut() {
                c.completed = true;
            }
            self.pay_pending()
        } else {
            if self.active.take().is_some() {
                self.contracts_failed += 1;
            }
            0
        };
        let report = BattleReport {
            outcome,
            timed_out: battle.timed_out(),
            rounds: battle.round(),
            enemies_down: battle.casualties(Team::Enemy).count(),
            enemies_total: battle.units().iter().filter(|u| u.team == Team::Enemy).count(),
            mercs,
            pay_pending,
        };
        self.phase = Phase::Aftermath;
        self.last_report = Some(report.clone());
        let mut ev = Vec::new();
        self.check_game_over(&mut ev);
        Ok(report)
    }

    /// Leave the result screen: head home (or end the campaign if it is over).
    pub fn continue_after_battle(&mut self) -> Result<(), CampaignError> {
        if self.phase != Phase::Aftermath {
            return Err(CampaignError::WrongPhase);
        }
        if self.over.is_some() {
            self.phase = Phase::GameOver;
        } else {
            let site = self.at_site.expect("battle happened at a site");
            self.start_return(site);
        }
        Ok(())
    }
}

/// Roman numeral for `n >= 1` (standard subtractive form; unbounded in practice).
fn roman(mut n: u32) -> String {
    const TABLE: [(u32, &str); 13] =
        [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")];
    let mut out = String::new();
    for (v, sym) in TABLE {
        while n >= v {
            out.push_str(sym);
            n -= v;
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn xp_thresholds_grow() {
        assert_eq!(xp_to_reach(1), 0);
        assert_eq!(xp_to_reach(2), 40);
        assert_eq!(xp_to_reach(3), 100);
        assert!(xp_to_reach(5) > xp_to_reach(4));
    }

    #[test]
    fn enemy_party_scales_with_difficulty() {
        for kind in [EnemyKind::Bandits, EnemyKind::Ambushers, EnemyKind::Warband] {
            let n: Vec<usize> = (1..=3).map(|d| enemy_party(kind, d, 5).len()).collect();
            assert!(n[0] < n[1] && n[1] < n[2], "{kind:?}: {n:?}");
            assert!(n[2] <= mapgen::MAX_ENEMY_SPAWNS);
        }
    }

    #[test]
    fn roman_numerals_are_complete_and_distinct() {
        assert_eq!(roman(2), "II");
        assert_eq!(roman(4), "IV");
        assert_eq!(roman(9), "IX");
        assert_eq!(roman(14), "XIV");
        assert_eq!(roman(40), "XL");
        assert_eq!(roman(1994), "MCMXCIV");
        let all: std::collections::HashSet<String> = (1..500).map(roman).collect();
        assert_eq!(all.len(), 499);
    }

    /// Regression: with every base name and every "II".."X"/"X+" variant taken (the dead keep
    /// their names forever), name generation used to loop forever and freeze rest/return-to-town.
    #[test]
    fn fresh_name_terminates_when_all_old_candidates_are_taken() {
        let mut c = Campaign::new(1);
        let fallen = |name: String| Fallen { name, archetype: Archetype::Swordsman, level: 1, day: 0, kills: 0 };
        for base in NAMES {
            c.fallen.push(fallen(base.to_string()));
            for suffix in ["II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "X+"] {
                c.fallen.push(fallen(format!("{base} {suffix}")));
            }
        }
        let mut seen = std::collections::HashSet::new();
        for _ in 0..200 {
            let n = c.fresh_name();
            assert!(!c.fallen.iter().any(|f| f.name == n) && !c.roster.iter().any(|m| m.name == n), "{n} reused");
            assert!(seen.insert(n.clone()), "{n} handed out twice");
            c.fallen.push(fallen(n));
        }
        // Recruit refresh keeps working in such a late campaign.
        c.restock_recruits(true);
        assert_eq!(c.recruits.len(), RECRUIT_POOL);
    }
}
