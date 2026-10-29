//! UI state machine. Translates input into `tactics_core` commands and renders core state.
//! No game rules live here: every decision is asked of the core (`Battle`, `ai::decide`).

use std::collections::HashMap;

use tactics_core::campaign::{BattleReport, Campaign};
use tactics_core::hex::round_axial;
use tactics_core::*;

mod strategic;

use crate::font;
use crate::gfx::{rgb, rgba, Color, Framebuffer};

pub const W: usize = 1400;
pub const H: usize = 820;
const TOP: i32 = 64;
const SIDE_X: i32 = 1000;
const HEX: f32 = 44.0;
const SQ3: f32 = 1.732_050_8;
const LOG_LINE_H: i32 = 18;
const LOG_CHARS: usize = 31;

// Palette.
const BG: Color = rgb(20, 22, 28);
const PANEL: Color = rgb(30, 34, 43);
const PANEL_EDGE: Color = rgb(62, 68, 82);
const TEXT: Color = rgb(230, 225, 211);
const DIM: Color = rgb(150, 150, 150);
const GOLD: Color = rgb(235, 190, 80);
const PLAYER: Color = rgb(70, 150, 215);
const ENEMY: Color = rgb(206, 80, 72);
const GOOD: Color = rgb(110, 200, 110);
const BAD: Color = rgb(235, 100, 90);

#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum Action {
    NewCampaign,
    QuickBattle,
    Quit,
    EndTurn,
    Resume,
    AbandonToMenu,
    ReturnToMenu,
    /// Leave the campaign result screen and head home.
    Continue,
    Pause,
    OpenTown,
    LeaveTown,
    SelectSite(usize),
    /// Travel to the selected site.
    Travel,
    /// Travel to the accepted contract's site.
    Depart,
    Accept(usize),
    CancelContract,
    Hire(usize),
    Rest,
    ToggleDeploy(u32),
    Fight,
    Retreat,
    DismissNotice,
    /// Page through the fallen on the campaign-over screen.
    FallenPrev,
    FallenNext,
}

#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum Key {
    Enter,
    Escape,
    Space,
    Up,
    Down,
}

#[derive(Copy, Clone, Debug)]
pub enum Input {
    MouseMove(f32, f32),
    MouseDown(f32, f32),
    Wheel(f32),
    Key(Key),
}

#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum Screen {
    Menu,
    Battle,
    Result,
    Overworld,
    Town,
    GameOver,
}

#[derive(Clone, Debug, Default)]
pub struct Options {
    /// AI controls both sides; the game starts a battle by itself.
    pub autoplay: bool,
    /// An autopilot plays the whole campaign (menu, town, travel, AI battles, results).
    pub autoplay_campaign: bool,
    pub seed: Option<u64>,
}

#[derive(Copy, Clone)]
struct Rect {
    x: i32,
    y: i32,
    w: i32,
    h: i32,
}

impl Rect {
    fn contains(&self, px: f32, py: f32) -> bool {
        px >= self.x as f32 && py >= self.y as f32 && px < (self.x + self.w) as f32 && py < (self.y + self.h) as f32
    }
}

struct Button {
    action: Action,
    rect: Rect,
    label: String,
    enabled: bool,
    /// Hit area only (drawn by the screen itself, e.g. map sites).
    hidden: bool,
}

struct LogLine {
    text: String,
    color: Color,
}

struct Floating {
    pos: (f32, f32),
    text: String,
    color: Color,
    age: f32,
    life: f32,
}

struct Flash {
    unit: UnitId,
    age: f32,
}

struct Shot {
    from: (f32, f32),
    to: (f32, f32),
    age: f32,
}

struct Ghost {
    pos: (f32, f32),
    team: Team,
    glyph: char,
    age: f32,
}

struct MoveAnim {
    unit: UnitId,
    path: Vec<Hex>,
    age: f32,
}

pub struct ResultInfo {
    pub outcome: Outcome,
    pub timed_out: bool,
    pub rounds: u32,
    pub survivors: Vec<(String, i32, i32)>,
    pub casualties: Vec<String>,
    pub enemies_down: usize,
    pub enemies_total: usize,
}

pub struct Game {
    opts: Options,
    screen: Screen,
    paused: bool,
    quit: bool,
    battle: Option<Battle>,
    selected: Option<UnitId>,
    reach: HashMap<Hex, i32>,
    mouse: (f32, f32),
    log: Vec<LogLine>,
    log_scroll: usize,
    floats: Vec<Floating>,
    flashes: Vec<Flash>,
    shots: Vec<Shot>,
    ghosts: Vec<Ghost>,
    moves: Vec<MoveAnim>,
    toast: Option<(String, f32)>,
    busy: f32,
    pending_result: Option<f32>,
    result: Option<ResultInfo>,
    next_seed: u64,
    pace: f32,
    time: f32,
    frames: u64,
    // Campaign state (None in a Quick Battle).
    campaign: Option<Campaign>,
    report: Option<BattleReport>,
    site_sel: Option<usize>,
    travel_clock: f32,
    notice: Option<strategic::Notice>,
    leveled: Vec<u32>,
    menu_focus: usize,
    fallen_page: usize,
    ap_timer: f32,
    ap_started: bool,
    view: (&'static str, f32),
    world_bg: std::cell::OnceCell<Framebuffer>,
}

// ------------------------------------------------------------------ geometry

fn map_origin() -> (f32, f32) {
    let total_w = 12.5 * SQ3 * HEX;
    let total_h = HEX * (1.5 * 9.0 + 2.0);
    let left = (SIDE_X as f32 - total_w) / 2.0;
    let top = TOP as f32 + ((H as i32 - TOP) as f32 - total_h) / 2.0;
    (left + SQ3 * HEX / 2.0, top + HEX)
}

pub fn hex_center(h: Hex) -> (f32, f32) {
    let (ox, oy) = map_origin();
    (ox + HEX * SQ3 * (h.q as f32 + h.r as f32 / 2.0), oy + HEX * 1.5 * h.r as f32)
}

pub fn pixel_to_hex(x: f32, y: f32) -> Hex {
    let (ox, oy) = map_origin();
    let (px, py) = (x - ox, y - oy);
    let q = (SQ3 / 3.0 * px - py / 3.0) / HEX;
    let r = (2.0 / 3.0 * py) / HEX;
    round_axial(q as f64, r as f64)
}

fn hex_corners(c: (f32, f32), size: f32) -> [(f32, f32); 6] {
    let mut out = [(0.0, 0.0); 6];
    for (i, o) in out.iter_mut().enumerate() {
        let a = (60.0 * i as f32 - 30.0).to_radians();
        *o = (c.0 + size * a.cos(), c.1 + size * a.sin());
    }
    out
}

fn hash(h: Hex) -> u32 {
    let mut v = (h.q.wrapping_mul(73_856_093) ^ h.r.wrapping_mul(19_349_663)) as u32;
    v ^= v >> 13;
    v = v.wrapping_mul(0x5bd1_e995);
    v ^ (v >> 15)
}

fn team_color(t: Team) -> Color {
    match t {
        Team::Player => PLAYER,
        Team::Enemy => ENEMY,
    }
}

fn wrap(text: &str, width: usize) -> Vec<String> {
    let mut lines = Vec::new();
    let mut cur = String::new();
    for word in text.split_whitespace() {
        if !cur.is_empty() && cur.len() + 1 + word.len() > width {
            lines.push(std::mem::take(&mut cur));
        }
        if !cur.is_empty() {
            cur.push(' ');
        }
        cur.push_str(word);
    }
    if !cur.is_empty() {
        lines.push(cur);
    }
    lines
}

// ---------------------------------------------------------------------- game

impl Game {
    pub fn new(opts: Options) -> Game {
        let seed = opts.seed.unwrap_or_else(|| {
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_or(1, |d| d.as_nanos() as u64)
        });
        let pace = if opts.autoplay || opts.autoplay_campaign { 0.2 } else { 1.0 };
        Game {
            opts,
            screen: Screen::Menu,
            paused: false,
            quit: false,
            battle: None,
            selected: None,
            reach: HashMap::new(),
            mouse: (0.0, 0.0),
            log: Vec::new(),
            log_scroll: 0,
            floats: Vec::new(),
            flashes: Vec::new(),
            shots: Vec::new(),
            ghosts: Vec::new(),
            moves: Vec::new(),
            toast: None,
            busy: 0.0,
            pending_result: None,
            result: None,
            next_seed: seed,
            pace,
            time: 0.0,
            frames: 0,
            campaign: None,
            report: None,
            site_sel: None,
            travel_clock: 0.0,
            notice: None,
            leveled: Vec::new(),
            menu_focus: 0,
            fallen_page: 0,
            ap_timer: 0.0,
            ap_started: false,
            view: ("menu", 0.0),
            world_bg: std::cell::OnceCell::new(),
        }
    }

    /// Every battle side is AI-controlled (autoplay modes).
    fn ai_all(&self) -> bool {
        self.opts.autoplay || self.opts.autoplay_campaign
    }

    #[cfg(test)]
    pub fn screen(&self) -> Screen {
        self.screen
    }

    #[cfg(test)]
    pub fn paused(&self) -> bool {
        self.paused
    }

    pub fn quit_requested(&self) -> bool {
        self.quit
    }

    #[cfg(test)]
    pub fn battle(&self) -> Option<&Battle> {
        self.battle.as_ref()
    }

    #[cfg(test)]
    pub fn result(&self) -> Option<&ResultInfo> {
        self.result.as_ref()
    }

    fn ai_turn(&self) -> bool {
        self.battle
            .as_ref()
            .is_some_and(|b| b.outcome().is_none() && (self.ai_all() || b.active().team == Team::Enemy))
    }

    fn human_turn(&self) -> bool {
        self.screen == Screen::Battle
            && !self.paused
            && self.pending_result.is_none()
            && self.busy <= 0.0
            && self.battle.as_ref().is_some_and(|b| b.outcome().is_none())
            && !self.ai_turn()
    }

    // ----- flow ------------------------------------------------------------

    /// Quick Battle: the fixed skirmish with the default four mercenaries.
    pub fn start_battle(&mut self) {
        self.campaign = None;
        self.report = None;
        let seed = self.next_seed;
        self.next_seed = self.next_seed.wrapping_add(1);
        self.start_battle_with(scenario::skirmish(seed), seed);
    }

    fn start_battle_with(&mut self, b: Battle, seed: u64) {
        self.log.clear();
        self.log_scroll = 0;
        self.floats.clear();
        self.flashes.clear();
        self.shots.clear();
        self.ghosts.clear();
        self.moves.clear();
        self.toast = None;
        self.result = None;
        self.pending_result = None;
        self.paused = false;
        self.busy = 0.5 * self.pace;
        self.push_log(format!("Battle begins. Seed {seed}."), TEXT);
        self.push_log("Round 1".to_string(), GOLD);
        let first = b.active_id();
        self.battle = Some(b);
        self.on_turn_started(first);
        self.screen = Screen::Battle;
    }

    fn to_menu(&mut self) {
        self.screen = Screen::Menu;
        self.paused = false;
        self.battle = None;
        self.result = None;
        self.pending_result = None;
        self.campaign = None;
        self.report = None;
        self.notice = None;
        self.site_sel = None;
        self.leveled.clear();
        self.floats.clear();
        self.fallen_page = 0;
    }

    fn perform(&mut self, action: Action) {
        match action {
            Action::QuickBattle => self.start_battle(),
            Action::Quit => self.quit = true,
            Action::EndTurn => {
                if self.human_turn() {
                    self.do_command(Command::EndTurn, true);
                }
            }
            Action::Resume => self.paused = false,
            Action::AbandonToMenu | Action::ReturnToMenu => self.to_menu(),
            other => self.perform_strategic(other),
        }
    }

    fn on_turn_started(&mut self, unit: UnitId) {
        self.selected = Some(unit);
        self.refresh_reach();
    }

    fn refresh_reach(&mut self) {
        self.reach.clear();
        if let Some(b) = &self.battle {
            if b.outcome().is_none() {
                self.reach = b.reachable(b.active_id()).into_iter().collect();
            }
        }
    }

    fn push_log(&mut self, text: String, color: Color) {
        for line in wrap(&text, LOG_CHARS) {
            self.log.push(LogLine { text: line, color });
        }
        if self.log.len() > 500 {
            self.log.drain(..100);
        }
    }

    fn toast(&mut self, msg: impl Into<String>) {
        self.toast = Some((msg.into(), 1.6));
    }

    /// Send a command to the core and turn the resulting events into log lines and effects.
    fn do_command(&mut self, cmd: Command, by_human: bool) {
        let Some(battle) = self.battle.as_mut() else { return };
        match battle.apply(cmd) {
            Ok(events) => self.handle_events(events),
            Err(e) => {
                debug_assert!(by_human, "AI produced an illegal command {cmd:?}: {e:?}");
                self.toast(e.describe());
            }
        }
    }

    fn handle_events(&mut self, events: Vec<Event>) {
        for ev in events {
            let Some(b) = self.battle.as_ref() else { return };
            match ev {
                Event::Moved { unit, path, cost } => {
                    let name = b.unit(unit).name.clone();
                    let steps = path.len().saturating_sub(1);
                    self.busy = self.busy.max((steps as f32 * 0.13 + 0.12) * self.pace.max(0.4));
                    self.moves.push(MoveAnim { unit, path, age: 0.0 });
                    let team = b.unit(unit).team;
                    self.push_log(format!("{name} moves ({cost} AP)"), team_color(team).lighten(0.3));
                }
                Event::Attacked { attacker, target, kind, chance, roll, hit, damage, armor_loss, hp_loss } => {
                    let (an, tn) = (b.unit(attacker).name.clone(), b.unit(target).name.clone());
                    let (apos, tpos) = (hex_center(b.unit(attacker).pos), hex_center(b.unit(target).pos));
                    let team = b.unit(attacker).team;
                    self.busy = self.busy.max(0.55 * self.pace.max(0.4));
                    if kind == AttackKind::Ranged {
                        self.shots.push(Shot { from: apos, to: tpos, age: 0.0 });
                    }
                    if hit {
                        let verb = if kind == AttackKind::Ranged { "shoots" } else { "hits" };
                        self.push_log(
                            format!("{an} {verb} {tn} for {damage} ({armor_loss} armor, {hp_loss} hp) {chance}%"),
                            team_color(team).lighten(0.35),
                        );
                        self.floats.push(Floating {
                            pos: (tpos.0, tpos.1 - 26.0),
                            text: format!("-{damage}"),
                            color: if hp_loss > 0 { BAD } else { rgb(190, 205, 230) },
                            age: 0.0,
                            life: 1.1,
                        });
                        self.flashes.push(Flash { unit: target, age: 0.0 });
                    } else {
                        self.push_log(format!("{an} misses {tn} ({chance}%, rolled {roll})"), DIM);
                        self.floats.push(Floating { pos: (tpos.0, tpos.1 - 26.0), text: "MISS".into(), color: DIM, age: 0.0, life: 0.9 });
                    }
                }
                Event::Died { unit } => {
                    let u = b.unit(unit);
                    let (name, team, glyph, pos) = (u.name.clone(), u.team, u.archetype.glyph(), hex_center(u.pos));
                    self.ghosts.push(Ghost { pos, team, glyph, age: 0.0 });
                    self.push_log(format!("{name} dies!"), GOLD);
                    self.floats.push(Floating { pos: (pos.0, pos.1 - 6.0), text: "DEAD".into(), color: GOLD, age: 0.0, life: 1.3 });
                }
                Event::RoundStarted { round } => self.push_log(format!("Round {round}"), GOLD),
                Event::TurnStarted { unit, .. } => {
                    self.busy = self.busy.max(0.3 * self.pace);
                    self.on_turn_started(unit);
                }
                Event::BattleEnded { outcome, timed_out } => {
                    self.log_end(outcome, timed_out);
                    self.pending_result = Some(1.4 * self.pace.max(0.3));
                }
            }
        }
        self.refresh_reach();
        self.log_scroll = 0;
    }

    fn log_end(&mut self, outcome: Outcome, timed_out: bool) {
        let msg = match (outcome, timed_out) {
            (Outcome::Victory, _) => "Victory! The bandits are routed.",
            (Outcome::Defeat, true) => "The battle drags on; the company withdraws.",
            (Outcome::Defeat, false) => "Defeat. The company is destroyed.",
        };
        self.push_log(msg.to_string(), GOLD);
        let Some(b) = self.battle.as_ref() else { return };
        if let Some(c) = self.campaign.as_mut() {
            match c.apply_battle(b) {
                Ok(report) => {
                    self.leveled = report.mercs.iter().filter(|m| m.leveled_up()).map(|m| m.id).collect();
                    self.report = Some(report);
                }
                Err(e) => debug_assert!(false, "campaign refused the battle result: {e:?}"),
            }
        }
        self.result = Some(ResultInfo {
            outcome,
            timed_out,
            rounds: b.round(),
            survivors: b.living(Team::Player).map(|u| (u.name.clone(), u.hp, u.stats.max_hp)).collect(),
            casualties: b.casualties(Team::Player).map(|u| u.name.clone()).collect(),
            enemies_down: b.casualties(Team::Enemy).count(),
            enemies_total: b.units().iter().filter(|u| u.team == Team::Enemy).count(),
        });
    }

    // ----- update ----------------------------------------------------------

    pub fn update(&mut self, dt: f32) {
        self.frames += 1;
        self.time += dt;
        if self.opts.autoplay && self.screen == Screen::Menu && self.frames > 3 {
            self.start_battle();
        }
        self.update_view(dt);
        self.tick_strategic(dt);
        self.floats.iter_mut().for_each(|f| f.age += dt);
        self.floats.retain(|f| f.age < f.life);
        self.flashes.iter_mut().for_each(|f| f.age += dt);
        self.flashes.retain(|f| f.age < 0.3);
        self.shots.iter_mut().for_each(|s| s.age += dt);
        self.shots.retain(|s| s.age < 0.22);
        self.ghosts.iter_mut().for_each(|g| g.age += dt);
        self.ghosts.retain(|g| g.age < 0.7);
        for m in &mut self.moves {
            m.age += dt;
        }
        let step_time = 0.13 * self.pace.max(0.4);
        self.moves.retain(|m| m.age < (m.path.len().saturating_sub(1)) as f32 * step_time);
        if let Some((_, t)) = &mut self.toast {
            *t -= dt;
            if *t <= 0.0 {
                self.toast = None;
            }
        }
        if self.screen != Screen::Battle || self.paused {
            return;
        }
        if let Some(t) = &mut self.pending_result {
            *t -= dt;
            if *t <= 0.0 {
                self.pending_result = None;
                self.screen = Screen::Result;
            }
            return;
        }
        self.busy = (self.busy - dt).max(0.0);
        if self.busy <= 0.0 && self.ai_turn() {
            let cmd = self.battle.as_ref().map(ai::decide);
            if let Some(cmd) = cmd {
                self.do_command(cmd, false);
                if cmd == Command::EndTurn {
                    self.busy = self.busy.max(0.2 * self.pace);
                }
            }
        }
    }

    // ----- input -----------------------------------------------------------

    fn buttons(&self) -> Vec<Button> {
        let cx = W as i32 / 2;
        let btn = |action, y, label: &str, w| Button {
            action,
            rect: Rect { x: cx - w / 2, y, w, h: 54 },
            label: label.to_string(),
            enabled: true,
            hidden: false,
        };
        if self.paused {
            let abandon = if self.campaign.is_some() { "ABANDON CAMPAIGN" } else { "ABANDON TO MENU" };
            return vec![
                btn(Action::Resume, 330, "RESUME", 340),
                btn(Action::AbandonToMenu, 400, abandon, 340),
                btn(Action::Quit, 470, "QUIT", 340),
            ];
        }
        match self.screen {
            Screen::Menu => vec![
                btn(Action::NewCampaign, 380, "NEW CAMPAIGN", 340),
                btn(Action::QuickBattle, 446, "QUICK BATTLE", 340),
                btn(Action::Quit, 512, "QUIT", 340),
            ],
            Screen::Result if self.report.is_some() => {
                vec![btn(Action::Continue, 620, "CONTINUE", 340), btn(Action::Quit, 690, "QUIT", 340)]
            }
            Screen::Result => vec![btn(Action::ReturnToMenu, 620, "RETURN TO MENU", 340), btn(Action::Quit, 690, "QUIT", 340)],
            Screen::GameOver => {
                let mut v = vec![btn(Action::ReturnToMenu, 706, "RETURN TO MENU", 340)];
                v.extend(self.game_over_pager());
                v
            }
            Screen::Overworld | Screen::Town => self.strategic_buttons(),
            Screen::Battle => vec![Button {
                action: Action::EndTurn,
                rect: Rect { x: SIDE_X + 20, y: 452, w: 360, h: 46 },
                label: "END TURN  [SPACE]".to_string(),
                enabled: self.human_turn(),
                hidden: false,
            }],
        }
    }

    pub fn input(&mut self, input: Input) {
        match input {
            Input::MouseMove(x, y) => self.mouse = (x, y),
            Input::Wheel(dy) => {
                if self.screen == Screen::GameOver && dy != 0.0 {
                    self.perform(if dy > 0.0 { Action::FallenPrev } else { Action::FallenNext });
                }
                if self.screen == Screen::Battle {
                    let max = self.log.len().saturating_sub(1);
                    if dy > 0.0 {
                        self.log_scroll = (self.log_scroll + 2).min(max);
                    } else if dy < 0.0 {
                        self.log_scroll = self.log_scroll.saturating_sub(2);
                    }
                }
            }
            Input::MouseDown(x, y) => {
                self.mouse = (x, y);
                let hit = self.buttons().into_iter().find(|b| b.rect.contains(x, y));
                if let Some(b) = hit {
                    if b.enabled {
                        self.perform(b.action);
                    }
                    return;
                }
                if self.screen == Screen::Battle && !self.paused {
                    self.click_map(x, y);
                }
            }
            Input::Key(k) => match (self.screen, k) {
                (Screen::Menu, Key::Up) => self.menu_focus = (self.menu_focus + 2) % 3,
                (Screen::Menu, Key::Down) => self.menu_focus = (self.menu_focus + 1) % 3,
                (Screen::Menu, Key::Enter | Key::Space) => {
                    self.perform([Action::NewCampaign, Action::QuickBattle, Action::Quit][self.menu_focus])
                }
                (Screen::Menu, Key::Escape) => self.perform(Action::Quit),
                (Screen::Result, Key::Enter | Key::Escape) => {
                    self.perform(if self.report.is_some() { Action::Continue } else { Action::ReturnToMenu })
                }
                (Screen::GameOver, Key::Enter | Key::Escape) => self.perform(Action::ReturnToMenu),
                (Screen::Overworld | Screen::Town, k) => self.strategic_key(k),
                (Screen::Battle, Key::Escape) => {
                    if self.pending_result.is_none() {
                        self.paused = !self.paused;
                    }
                }
                (Screen::Battle, Key::Enter) if self.paused => self.paused = false,
                (Screen::Battle, Key::Space | Key::Enter) => self.perform(Action::EndTurn),
                _ => {}
            },
        }
    }

    fn click_map(&mut self, x: f32, y: f32) {
        let Some(b) = self.battle.as_ref() else { return };
        if x >= SIDE_X as f32 || y < TOP as f32 {
            return;
        }
        let hex = pixel_to_hex(x, y);
        if !b.map().contains(hex) {
            return;
        }
        let clicked = b.unit_at(hex);
        if !self.human_turn() {
            // Inspecting is allowed at any time.
            if let Some(id) = clicked {
                self.selected = Some(id);
            }
            return;
        }
        let aid = b.active_id();
        let team = b.active().team;
        match clicked {
            Some(id) if b.unit(id).team != team => {
                // Attack if possible; otherwise just inspect the target and say why not.
                if let Err(e) = b.attack_preview(aid, id) {
                    self.selected = Some(id);
                    self.toast(e.describe());
                } else {
                    self.selected = Some(aid);
                    self.do_command(Command::Attack { target: id }, true);
                }
            }
            Some(id) => self.selected = Some(id),
            None => {
                self.selected = Some(aid);
                self.do_command(Command::Move { to: hex }, true);
            }
        }
    }

    // ----- rendering -------------------------------------------------------

    pub fn render(&self, fb: &mut Framebuffer) {
        fb.clear(BG);
        match self.screen {
            Screen::Menu => self.render_menu(fb),
            Screen::Overworld => self.render_overworld(fb),
            Screen::Town => self.render_town(fb),
            Screen::GameOver => self.render_game_over(fb),
            Screen::Battle | Screen::Result => {
                self.render_battle(fb);
                if self.screen == Screen::Result {
                    self.render_result(fb);
                }
            }
        }
        if self.paused {
            self.render_pause(fb);
        }
    }

    fn draw_buttons(&self, fb: &mut Framebuffer) {
        for b in self.buttons().into_iter().filter(|b| !b.hidden) {
            let hover = b.enabled && b.rect.contains(self.mouse.0, self.mouse.1);
            let (fill, edge, tc) = if !b.enabled {
                (rgb(36, 38, 46), rgb(55, 58, 68), rgb(95, 95, 100))
            } else if hover {
                (rgb(88, 70, 34), GOLD, rgb(255, 235, 190))
            } else {
                (rgb(52, 58, 74), rgb(110, 118, 140), TEXT)
            };
            let r = b.rect;
            fb.fill_rect(r.x, r.y, r.w, r.h, fill);
            fb.rect_outline(r.x, r.y, r.w, r.h, 2, edge);
            let scale = if r.h >= 50 { 3 } else { 2 };
            fb.text_centered(r.x + r.w / 2, r.y + (r.h - font::text_height(scale)) / 2, &b.label, scale, tc);
        }
    }

    fn render_menu(&self, fb: &mut Framebuffer) {
        // Backdrop: a drifting field of dim hexes.
        for r in -1..14 {
            for q in -8..18 {
                let h = Hex::new(q, r);
                let (cx, cy) = (hex_center(h).0 - 120.0 + 200.0, hex_center(h).1 - 100.0);
                let v = hash(h) % 100;
                let c = if v < 55 { rgb(26, 30, 38) } else if v < 75 { rgb(24, 38, 34) } else if v < 90 { rgb(36, 32, 30) } else { rgb(30, 36, 48) };
                fb.fill_poly(&hex_corners((cx, cy), HEX - 2.0), c);
            }
        }
        fb.fill_rect(0, 90, W as i32, 260, rgba(10, 11, 15, 190));
        fb.text_centered(W as i32 / 2 + 4, 124, "HIRELING'S OATH", 8, rgba(0, 0, 0, 200));
        fb.text_centered(W as i32 / 2, 120, "HIRELING'S OATH", 8, GOLD);
        fb.text_centered(W as i32 / 2, 220, "A MERCENARY TACTICS GAME", 3, TEXT);
        fb.text_centered(W as i32 / 2, 270, "LEAD A MERCENARY COMPANY ACROSS THE REGION AND ONTO THE HEX GRID", 2, DIM);
        self.draw_buttons(fb);
        let focus = Rect { x: W as i32 / 2 - 170, y: 380 + 66 * self.menu_focus as i32, w: 340, h: 54 };
        fb.rect_outline(focus.x - 5, focus.y - 5, focus.w + 10, focus.h + 10, 2, GOLD);
        fb.text_centered(W as i32 / 2, 592, "UP/DOWN + ENTER, OR CLICK    ESC: QUIT", 2, DIM);
        let help = [
            "CAMPAIGN: TAKE CONTRACTS, TRAVEL, FIGHT, GET PAID, HIRE AND LEVEL UP",
            "IN BATTLE: CLICK A HIGHLIGHTED HEX TO MOVE, CLICK AN ENEMY TO ATTACK",
            "SPACE: END TURN   ESC: PAUSE   MOUSE WHEEL: SCROLL LOG",
        ];
        for (i, l) in help.iter().enumerate() {
            fb.text_centered(W as i32 / 2, 650 + i as i32 * 26, l, 2, DIM);
        }
    }

    fn render_pause(&self, fb: &mut Framebuffer) {
        fb.fill_rect(0, 0, W as i32, H as i32, rgba(0, 0, 0, 160));
        let cx = W as i32 / 2;
        fb.fill_rect(cx - 220, 230, 440, 330, PANEL);
        fb.rect_outline(cx - 220, 230, 440, 330, 2, PANEL_EDGE);
        fb.text_centered(cx, 262, "PAUSED", 5, GOLD);
        self.draw_buttons(fb);
        fb.text_centered(cx, 536, "ESC: RESUME", 2, DIM);
    }

    fn render_result(&self, fb: &mut Framebuffer) {
        if let Some(rep) = &self.report {
            return self.render_campaign_result(fb, rep);
        }
        let Some(r) = &self.result else { return };
        fb.fill_rect(0, 0, W as i32, H as i32, rgba(0, 0, 0, 185));
        let cx = W as i32 / 2;
        let (title, col) = match r.outcome {
            Outcome::Victory => ("VICTORY", GOOD),
            Outcome::Defeat => ("DEFEAT", BAD),
        };
        fb.fill_rect(cx - 400, 40, 800, 750, PANEL);
        fb.rect_outline(cx - 400, 40, 800, 750, 2, PANEL_EDGE);
        fb.text_centered(cx, 70, title, 8, col);
        let sub = match (r.outcome, r.timed_out) {
            (Outcome::Victory, _) => "THE BANDITS ARE ROUTED".to_string(),
            (Outcome::Defeat, true) => "THE BATTLE DRAGGED ON AND THE COMPANY WITHDREW".to_string(),
            (Outcome::Defeat, false) => "THE COMPANY WAS DESTROYED".to_string(),
        };
        fb.text_centered(cx, 145, &sub, 2, TEXT);
        fb.text_centered(
            cx,
            178,
            &format!("ROUNDS FOUGHT: {}    ENEMIES DOWN: {}/{}", r.rounds, r.enemies_down, r.enemies_total),
            2,
            DIM,
        );
        let mut y = 235;
        fb.text(cx - 360, y, &format!("SURVIVORS ({})", r.survivors.len()), 3, GOOD);
        y += 34;
        if r.survivors.is_empty() {
            fb.text(cx - 340, y, "NONE", 2, DIM);
            y += 24;
        }
        for (name, hp, max) in &r.survivors {
            fb.text(cx - 340, y, name, 2, TEXT);
            fb.text(cx + 40, y, &format!("HP {hp}/{max}"), 2, DIM);
            y += 24;
        }
        y += 14;
        fb.text(cx - 360, y, &format!("CASUALTIES ({})", r.casualties.len()), 3, BAD);
        y += 34;
        if r.casualties.is_empty() {
            fb.text(cx - 340, y, "NONE - NOBODY FELL", 2, DIM);
        }
        for name in &r.casualties {
            fb.text(cx - 340, y, &format!("{name}  - FELL IN BATTLE"), 2, TEXT);
            y += 24;
        }
        self.draw_buttons(fb);
        fb.text_centered(cx, 756, "ENTER: RETURN TO MENU", 2, DIM);
    }

    fn visual_pos(&self, u: &Unit) -> (f32, f32) {
        let step_time = 0.13 * self.pace.max(0.4);
        if let Some(m) = self.moves.iter().find(|m| m.unit == u.id) {
            let t = (m.age / step_time).min((m.path.len() - 1) as f32);
            let i = t.floor() as usize;
            let a = hex_center(m.path[i]);
            if i + 1 < m.path.len() {
                let b = hex_center(m.path[i + 1]);
                let f = t - i as f32;
                return (a.0 + (b.0 - a.0) * f, a.1 + (b.1 - a.1) * f);
            }
            return a;
        }
        hex_center(u.pos)
    }

    fn draw_terrain(&self, fb: &mut Framebuffer, h: Hex, t: Terrain) {
        let c = hex_center(h);
        let base = match t {
            Terrain::Plain => rgb(122, 144, 82),
            Terrain::Forest => rgb(48, 98, 58),
            Terrain::Hill => rgb(158, 128, 82),
            Terrain::Rock => rgb(100, 104, 112),
            Terrain::Water => rgb(50, 98, 160),
        };
        let v = hash(h);
        let shade = (v % 7) as f32 / 60.0;
        fb.fill_poly(&hex_corners(c, HEX), base.lighten(shade));
        let (x, y) = c;
        match t {
            Terrain::Plain => {
                for i in 0..3 {
                    let ox = ((v >> (i * 5)) % 40) as f32 - 20.0;
                    let oy = ((v >> (i * 5 + 3)) % 30) as f32 - 15.0;
                    fb.line(x + ox, y + oy, x + ox + 2.0, y + oy - 5.0, 1.5, rgb(98, 122, 64));
                }
            }
            Terrain::Forest => {
                for (ox, oy, s) in [(-12.0, 6.0, 1.0), (10.0, 8.0, 0.9), (-1.0, -8.0, 1.15)] {
                    fb.fill_rect((x + ox - 1.5) as i32, (y + oy + 8.0 * s) as i32, 3, 5, rgb(70, 48, 30));
                    fb.fill_poly(&[(x + ox, y + oy - 14.0 * s), (x + ox + 10.0 * s, y + oy + 9.0 * s), (x + ox - 10.0 * s, y + oy + 9.0 * s)], rgb(28, 70, 40));
                    fb.fill_poly(&[(x + ox, y + oy - 14.0 * s), (x + ox + 4.0 * s, y + oy - 4.0 * s), (x + ox - 4.0 * s, y + oy - 4.0 * s)], rgb(44, 96, 56));
                }
            }
            Terrain::Hill => {
                fb.fill_poly(&[(x - 24.0, y + 14.0), (x - 8.0, y - 10.0), (x + 6.0, y - 10.0), (x + 24.0, y + 14.0)], rgb(128, 100, 62));
                fb.fill_poly(&[(x - 14.0, y + 14.0), (x - 2.0, y - 4.0), (x + 12.0, y - 4.0), (x + 24.0, y + 14.0)], rgb(180, 150, 100));
                fb.line(x - 6.0, y - 8.0, x + 4.0, y - 8.0, 2.0, rgb(205, 180, 130));
            }
            Terrain::Rock => {
                fb.fill_poly(&[(x - 22.0, y + 12.0), (x - 14.0, y - 14.0), (x + 4.0, y - 20.0), (x + 22.0, y - 4.0), (x + 18.0, y + 14.0)], rgb(70, 74, 82));
                fb.fill_poly(&[(x - 14.0, y - 14.0), (x + 4.0, y - 20.0), (x + 8.0, y - 4.0), (x - 8.0, y + 2.0)], rgb(138, 142, 150));
            }
            Terrain::Water => {
                for (ox, oy) in [(-14.0, -8.0), (4.0, 2.0), (-8.0, 12.0)] {
                    fb.line(x + ox, y + oy, x + ox + 14.0, y + oy, 2.0, rgb(120, 170, 225));
                }
            }
        }
        fb.poly_outline(&hex_corners(c, HEX), 1.2, base.darken(0.45));
    }

    fn draw_token(&self, fb: &mut Framebuffer, pos: (f32, f32), team: Team, glyph: char, alpha: f32, flash: f32) {
        let base = team_color(team);
        let col = base.lerp(rgb(255, 255, 255), flash).fade(alpha);
        fb.disc(pos.0 + 2.0, pos.1 + 4.0, HEX * 0.52, rgba(0, 0, 0, (90.0 * alpha) as u8));
        fb.disc(pos.0, pos.1, HEX * 0.52, rgb(15, 15, 20).fade(alpha));
        fb.disc(pos.0, pos.1, HEX * 0.47, col);
        fb.disc(pos.0, pos.1 - 3.0, HEX * 0.34, base.lighten(0.18).lerp(rgb(255, 255, 255), flash).fade(alpha * 0.6));
        let s = glyph.to_string();
        fb.text_centered(pos.0 as i32, pos.1 as i32 - 10, &s, 3, rgb(255, 255, 255).fade(alpha));
    }

    fn draw_unit(&self, fb: &mut Framebuffer, u: &Unit, active: bool) {
        let pos = self.visual_pos(u);
        let flash = self
            .flashes
            .iter()
            .find(|f| f.unit == u.id)
            .map_or(0.0, |f| (1.0 - f.age / 0.3).clamp(0.0, 1.0));
        if active {
            let pulse = 0.5 + 0.5 * (self.time * 6.0).sin();
            fb.circle(pos.0, pos.1, HEX * 0.54, HEX * 0.54 + 3.0 + pulse * 2.0, GOLD);
        }
        if self.selected == Some(u.id) {
            fb.circle(pos.0, pos.1, HEX * 0.58 + 4.0, HEX * 0.58 + 6.0, rgb(255, 255, 255));
        }
        self.draw_token(fb, pos, u.team, u.archetype.glyph(), 1.0, flash);
        // Armor and HP bars under the token.
        let bw = HEX * 1.1;
        let (bx, by) = (pos.0 - bw / 2.0, pos.1 + HEX * 0.56);
        fb.fill_rect(bx as i32 - 1, by as i32 - 1, bw as i32 + 2, 11, rgb(10, 10, 12));
        let hp_frac = u.hp as f32 / u.stats.max_hp as f32;
        let hp_col = if hp_frac > 0.5 { rgb(80, 190, 90) } else if hp_frac > 0.25 { rgb(220, 180, 60) } else { rgb(220, 70, 60) };
        fb.fill_rect(bx as i32, by as i32, (bw * hp_frac) as i32, 6, hp_col);
        let ar_frac = if u.stats.max_armor > 0 { u.armor as f32 / u.stats.max_armor as f32 } else { 0.0 };
        fb.fill_rect(bx as i32, by as i32 + 7, (bw * ar_frac) as i32, 3, rgb(150, 170, 200));
        if active {
            let t = format!("{}AP", u.ap);
            fb.text_shadow((pos.0 - font::text_width(&t, 1) as f32 / 2.0) as i32, (pos.1 - HEX * 0.52 - 11.0) as i32, &t, 1, GOLD);
        }
    }

    fn render_battle(&self, fb: &mut Framebuffer) {
        let Some(b) = self.battle.as_ref() else { return };
        let active_id = b.active_id();
        let live_turn = b.outcome().is_none();
        let human = self.human_turn();
        let mouse_hex = pixel_to_hex(self.mouse.0, self.mouse.1);
        let hover = (self.mouse.0 < SIDE_X as f32 && self.mouse.1 >= TOP as f32 && b.map().contains(mouse_hex)).then_some(mouse_hex);

        // Terrain.
        for h in b.map().hexes() {
            if let Some(t) = b.map().terrain(h) {
                self.draw_terrain(fb, h, t);
            }
        }
        // Corpses.
        for u in b.units().iter().filter(|u| !u.alive) {
            let (x, y) = hex_center(u.pos);
            let c = team_color(u.team).darken(0.55);
            fb.line(x - 9.0, y - 9.0, x + 9.0, y + 9.0, 4.0, rgba(0, 0, 0, 150));
            fb.line(x - 9.0, y + 9.0, x + 9.0, y - 9.0, 4.0, rgba(0, 0, 0, 150));
            fb.line(x - 8.0, y - 8.0, x + 8.0, y + 8.0, 2.5, c);
            fb.line(x - 8.0, y + 8.0, x + 8.0, y - 8.0, 2.5, c);
        }

        // Movement range and hover path.
        let mut path_info: Option<(Vec<Hex>, i32)> = None;
        if human && live_turn {
            for (&h, &cost) in &self.reach {
                let is_hover = hover == Some(h);
                fb.fill_poly(&hex_corners(hex_center(h), HEX - 1.0), rgba(255, 255, 255, if is_hover { 90 } else { 42 }));
                let t = cost.to_string();
                let c = hex_center(h);
                fb.text_centered(c.0 as i32, c.1 as i32 + 14, &t, 1, rgba(255, 255, 255, 190));
            }
            if let Some(h) = hover {
                if self.reach.contains_key(&h) {
                    path_info = b.move_path(active_id, h).ok();
                }
            }
        }
        if let Some((path, _)) = &path_info {
            for w in path.windows(2) {
                let (a, c) = (hex_center(w[0]), hex_center(w[1]));
                fb.line(a.0, a.1, c.0, c.1, 4.0, rgba(20, 20, 20, 160));
                fb.line(a.0, a.1, c.0, c.1, 2.5, GOLD);
            }
            for h in &path[1..] {
                let c = hex_center(*h);
                fb.disc(c.0, c.1, 4.0, GOLD);
            }
        }

        // Attackable enemies pulse red; hovered hex gets an outline.
        let mut tooltip: Vec<(String, Color, i32)> = Vec::new();
        let mut los_line: Option<((f32, f32), (f32, f32), bool)> = None;
        if human && live_turn {
            let pulse = 0.5 + 0.5 * (self.time * 5.0).sin();
            for u in b.living(b.active().team.opponent()) {
                if b.attack_preview(active_id, u.id).is_ok() {
                    let c = hex_center(u.pos);
                    fb.poly_outline(&hex_corners(c, HEX - 2.0), 3.0, ENEMY.lighten(0.2).fade(0.55 + 0.4 * pulse));
                }
            }
            if let Some(h) = hover {
                if let Some(tid) = b.unit_at(h) {
                    if b.unit(tid).team != b.active().team {
                        let from = hex_center(b.active().pos);
                        let to = hex_center(b.unit(tid).pos);
                        match b.attack_preview(active_id, tid) {
                            Ok(p) => {
                                tooltip.push((format!("HIT CHANCE {}%", p.hit.chance), GOLD, 2));
                                tooltip.push((format!("{} {}-{} DMG  {} AP", p.weapon.name.to_uppercase(), p.weapon.dmg_min, p.weapon.dmg_max, p.weapon.ap_cost), TEXT, 1));
                                let hb = p.hit;
                                tooltip.push((format!("SKILL {}  WEAPON {:+}  DEFENSE -{}", hb.skill, hb.weapon_mod, hb.defense), DIM, 1));
                                tooltip.push((format!("TERRAIN -{}  HEIGHT {:+}  RANGE {:+}", hb.terrain_defense, hb.height, hb.range), DIM, 1));
                                if p.kind == AttackKind::Ranged {
                                    los_line = Some((from, to, true));
                                }
                            }
                            Err(e) => {
                                let (head, extra) = match e {
                                    ActionError::NoLineOfSight => ("TARGET NOT VISIBLE", true),
                                    ActionError::OutOfRange => ("OUT OF RANGE", false),
                                    ActionError::NotEnoughAp { .. } => ("NOT ENOUGH AP", false),
                                    _ => ("CANNOT ATTACK", false),
                                };
                                tooltip.push((head.to_string(), BAD, 2));
                                tooltip.push((e.describe().to_uppercase(), DIM, 1));
                                if extra {
                                    los_line = Some((from, to, false));
                                }
                            }
                        }
                    }
                } else if let Some((_, cost)) = &path_info {
                    tooltip.push((format!("MOVE: {cost} AP"), GOLD, 2));
                    let left = b.active().ap - cost;
                    tooltip.push((format!("{left} AP LEFT"), DIM, 1));
                } else if b.map().terrain(h).is_some_and(|t| t.passable()) && !self.reach.contains_key(&h) && h != b.active().pos {
                    if let Err(e) = b.move_path(active_id, h) {
                        tooltip.push((e.describe().to_uppercase(), BAD, 1));
                    }
                }
            }
        }
        if let Some(h) = hover {
            fb.poly_outline(&hex_corners(hex_center(h), HEX - 1.5), 2.0, rgba(255, 255, 255, 170));
        }
        if let Some((a, c, ok)) = los_line {
            let col = if ok { rgba(255, 255, 255, 150) } else { rgba(235, 90, 80, 200) };
            fb.line(a.0, a.1, c.0, c.1, 2.0, col);
            if !ok {
                let m = ((a.0 + c.0) / 2.0, (a.1 + c.1) / 2.0);
                fb.line(m.0 - 7.0, m.1 - 7.0, m.0 + 7.0, m.1 + 7.0, 3.0, BAD);
                fb.line(m.0 - 7.0, m.1 + 7.0, m.0 + 7.0, m.1 - 7.0, 3.0, BAD);
            }
        }

        // Units (back to front by row so lower tokens overlap upper ones).
        let mut order: Vec<&Unit> = b.units().iter().filter(|u| u.alive).collect();
        order.sort_by(|a, c| self.visual_pos(a).1.total_cmp(&self.visual_pos(c).1));
        for u in order {
            self.draw_unit(fb, u, live_turn && u.id == active_id);
        }
        for g in &self.ghosts {
            let f = 1.0 - g.age / 0.7;
            self.draw_token(fb, (g.pos.0, g.pos.1 - g.age * 14.0), g.team, g.glyph, f, 1.0);
        }
        for s in &self.shots {
            let t = (s.age / 0.22).min(1.0);
            let p = (s.from.0 + (s.to.0 - s.from.0) * t, s.from.1 + (s.to.1 - s.from.1) * t);
            let t0 = (t - 0.25).max(0.0);
            let p0 = (s.from.0 + (s.to.0 - s.from.0) * t0, s.from.1 + (s.to.1 - s.from.1) * t0);
            fb.line(p0.0, p0.1, p.0, p.1, 3.0, rgb(250, 240, 200));
        }
        for f in &self.floats {
            let k = f.age / f.life;
            let y = f.pos.1 - k * 30.0;
            let w = font::text_width(&f.text, 3);
            fb.text_shadow(f.pos.0 as i32 - w / 2, y as i32 - 10, &f.text, 3, f.color.fade(1.0 - k * k));
        }

        self.render_top_strip(fb, b);
        self.render_sidebar(fb, b, hover);
        if !tooltip.is_empty() && !self.paused {
            self.draw_tooltip(fb, &tooltip);
        }
        if let Some((msg, t)) = &self.toast {
            let w = font::text_width(msg, 2) + 24;
            let x = SIDE_X / 2 - w / 2;
            fb.fill_rect(x, H as i32 - 50, w, 30, rgba(60, 20, 18, (255.0 * (t / 0.5).min(1.0)) as u8));
            fb.text(x + 12, H as i32 - 43, &msg.to_uppercase(), 2, rgb(255, 210, 200).fade((t / 0.5).min(1.0)));
        }
        if self.opts.autoplay {
            fb.text_shadow(12, H as i32 - 24, "AUTOPLAY - AI CONTROLS BOTH SIDES", 2, GOLD);
        } else if live_turn && !human && !self.paused {
            let txt = format!("{} IS ACTING...", b.active().name.to_uppercase());
            fb.text_shadow(12, H as i32 - 24, &txt, 2, ENEMY.lighten(0.3));
        }
    }

    fn draw_tooltip(&self, fb: &mut Framebuffer, lines: &[(String, Color, i32)]) {
        let w = lines.iter().map(|(t, _, s)| font::text_width(t, *s)).max().unwrap_or(0) + 20;
        let h = lines.iter().map(|(_, _, s)| font::text_height(*s) + 6).sum::<i32>() + 10;
        let mut x = self.mouse.0 as i32 + 18;
        let mut y = self.mouse.1 as i32 + 14;
        if x + w > W as i32 - 4 {
            x = self.mouse.0 as i32 - w - 10;
        }
        if y + h > H as i32 - 4 {
            y = H as i32 - 4 - h;
        }
        fb.fill_rect(x, y, w, h, rgba(12, 13, 18, 235));
        fb.rect_outline(x, y, w, h, 1, PANEL_EDGE);
        let mut ty = y + 8;
        for (t, c, s) in lines {
            fb.text(x + 10, ty, t, *s, *c);
            ty += font::text_height(*s) + 6;
        }
    }

    fn render_top_strip(&self, fb: &mut Framebuffer, b: &Battle) {
        fb.fill_rect(0, 0, W as i32, TOP, PANEL);
        fb.fill_rect(0, TOP - 2, W as i32, 2, PANEL_EDGE);
        fb.text(14, 12, &format!("ROUND {}", b.round()), 3, GOLD);
        fb.text(14, 40, "TURN ORDER", 1, DIM);
        let queue = b.turn_queue(18);
        let this_round = b.remaining_this_round();
        let mut x = 190;
        for (i, &id) in queue.iter().enumerate() {
            if i == this_round && i > 0 {
                fb.line(x as f32 - 2.0, 8.0, x as f32 - 2.0, TOP as f32 - 10.0, 2.0, PANEL_EDGE);
                fb.text(x + 4, 26, "NEXT", 1, DIM);
                x += 40;
            }
            let u = b.unit(id);
            let cy = 32.0;
            let cx = x as f32 + 20.0;
            let current = i == 0 && b.outcome().is_none();
            if current {
                fb.disc(cx, cy, 25.0, GOLD);
            }
            if self.selected == Some(id) {
                fb.circle(cx, cy, 22.0, 25.0, rgb(255, 255, 255));
            }
            fb.disc(cx, cy, 21.0, rgb(12, 12, 16));
            fb.disc(cx, cy, 18.0, team_color(u.team).darken(if i < this_round { 0.0 } else { 0.35 }));
            fb.text_centered(cx as i32, cy as i32 - 7, &u.archetype.glyph().to_string(), 2, rgb(255, 255, 255));
            let hp = u.hp as f32 / u.stats.max_hp as f32;
            fb.fill_rect(x + 6, 55, 28, 3, rgb(10, 10, 12));
            fb.fill_rect(x + 6, 55, (28.0 * hp) as i32, 3, GOOD);
            x += 52;
        }
    }

    fn render_sidebar(&self, fb: &mut Framebuffer, b: &Battle, hover: Option<Hex>) {
        let x0 = SIDE_X;
        fb.fill_rect(x0, TOP, W as i32 - x0, H as i32 - TOP, PANEL);
        fb.fill_rect(x0, TOP, 2, H as i32 - TOP, PANEL_EDGE);
        let px = x0 + 20;

        // Unit panel.
        let hovered_unit = hover.and_then(|h| b.unit_at(h));
        let uid = hovered_unit.or(self.selected).unwrap_or(b.active_id());
        let u = b.unit(uid);
        let mut y = TOP + 12;
        fb.text(px, y, &u.name.to_uppercase(), 3, team_color(u.team).lighten(0.25));
        y += 30;
        let tag = if u.team == Team::Player { "YOUR COMPANY" } else { "ENEMY" };
        let status = if !u.alive {
            "  - DEAD".to_string()
        } else if uid == b.active_id() && b.outcome().is_none() {
            "  - ACTING NOW".to_string()
        } else {
            String::new()
        };
        let lv = if u.roster_id.is_some() { format!(" - LV {}  KILLS {}", u.level, u.kills) } else { String::new() };
        fb.text(px, y, &format!("{} - {}{}{}", u.archetype.label().to_uppercase(), tag, lv, status), 1, TEXT);
        y += 14;
        fb.text(px, y, &u.archetype.description().to_uppercase(), 1, DIM);
        y += 22;
        let bar = |fb: &mut Framebuffer, y: i32, label: &str, val: i32, max: i32, col: Color| {
            fb.text(px, y, label, 2, DIM);
            let (bx, bw) = (px + 78, 190);
            fb.fill_rect(bx, y, bw, 16, rgb(12, 12, 16));
            let frac = if max > 0 { val as f32 / max as f32 } else { 0.0 };
            fb.fill_rect(bx + 1, y + 1, ((bw - 2) as f32 * frac) as i32, 14, col);
            fb.text(bx + bw + 10, y + 1, &format!("{val}/{max}"), 2, TEXT);
        };
        bar(fb, y, "HP", u.hp, u.stats.max_hp, rgb(80, 190, 90));
        y += 26;
        bar(fb, y, "ARMOR", u.armor, u.stats.max_armor, rgb(150, 170, 200));
        y += 26;
        fb.text(px, y, "AP", 2, DIM);
        for i in 0..AP_PER_TURN {
            let on = i < u.ap;
            fb.fill_rect(px + 78 + i * 21, y, 17, 16, if on { GOLD } else { rgb(12, 12, 16) });
        }
        fb.text(px + 78 + AP_PER_TURN * 21 + 10, y + 1, &format!("{}/{}", u.ap, AP_PER_TURN), 2, TEXT);
        y += 28;
        let m = if u.stats.melee_skill > 0 { u.stats.melee_skill.to_string() } else { "-".into() };
        let r = if u.stats.ranged_skill > 0 { u.stats.ranged_skill.to_string() } else { "-".into() };
        fb.text(px, y, &format!("DEFENSE {}   INITIATIVE {}", u.stats.defense, u.stats.initiative), 2, TEXT);
        y += 22;
        fb.text(px, y, &format!("MELEE SKILL {m}   RANGED SKILL {r}"), 2, TEXT);
        y += 26;
        let mw = u.archetype.melee_weapon();
        fb.text(px, y, &format!("{}: {}-{} DMG, {} AP, PIERCE {}%", mw.name.to_uppercase(), mw.dmg_min, mw.dmg_max, mw.ap_cost, mw.armor_ignore), 1, TEXT);
        y += 14;
        if let Some(rw) = u.archetype.ranged_weapon() {
            fb.text(px, y, &format!("{}: {}-{} DMG, {} AP, RANGE {}-{}, PIERCE {}%", rw.name.to_uppercase(), rw.dmg_min, rw.dmg_max, rw.ap_cost, rw.range_min, rw.range_max, rw.armor_ignore), 1, TEXT);
        }
        y += 18;
        let tname = b.map().terrain(u.pos).map_or("?", Terrain::name);
        fb.text(px, y, &format!("STANDING ON: {}", tname.to_uppercase()), 1, DIM);

        // Hex info.
        let iy = 388;
        fb.fill_rect(x0 + 12, iy - 6, W as i32 - x0 - 24, 54, rgb(24, 27, 35));
        if let Some(h) = hover {
            if let Some(t) = b.map().terrain(h) {
                let (c, r) = h.to_offset();
                let mv = t.move_cost().map_or("IMPASSABLE".to_string(), |c| format!("MOVE {c} AP"));
                fb.text(px, iy, &format!("HEX {c},{r}: {}", t.name().to_uppercase()), 2, TEXT);
                let mut extra = vec![mv];
                if t.defense_bonus() > 0 {
                    extra.push(format!("+{} DEFENSE", t.defense_bonus()));
                }
                if t.is_high_ground() {
                    extra.push(format!("+{} HIT FROM HERE", tactics_core::battle::HILL_ATTACK_BONUS));
                }
                if t.blocks_los() {
                    extra.push("BLOCKS SIGHT".into());
                }
                fb.text(px, iy + 24, &extra.join("  "), 1, DIM);
            }
        } else {
            fb.text(px, iy, "HOVER A HEX FOR INFO", 2, DIM);
        }

        // Buttons.
        self.draw_buttons_in_battle(fb);

        // Log.
        let ly = 512;
        fb.text(px, ly, "COMBAT LOG", 2, GOLD);
        if self.log_scroll > 0 {
            fb.text(px + 200, ly + 3, "SCROLLED - WHEEL DOWN", 1, DIM);
        }
        let area_h = H as i32 - (ly + 26) - 8;
        let rows = (area_h / LOG_LINE_H).max(1) as usize;
        fb.fill_rect(x0 + 12, ly + 22, W as i32 - x0 - 24, area_h + 6, rgb(20, 22, 29));
        let end = self.log.len().saturating_sub(self.log_scroll);
        let start = end.saturating_sub(rows);
        for (i, l) in self.log[start..end].iter().enumerate() {
            fb.text(px, ly + 28 + i as i32 * LOG_LINE_H, &l.text.to_uppercase(), 2, l.color);
        }
    }

    fn draw_buttons_in_battle(&self, fb: &mut Framebuffer) {
        if !self.paused {
            self.draw_buttons(fb);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn game(autoplay: bool) -> Game {
        Game::new(Options { autoplay, seed: Some(7), ..Options::default() })
    }

    fn click_hex(g: &mut Game, h: Hex) {
        let (x, y) = hex_center(h);
        g.input(Input::MouseMove(x, y));
        g.input(Input::MouseDown(x, y));
    }

    fn run(g: &mut Game, seconds: f32) {
        for _ in 0..(seconds * 60.0) as usize {
            g.update(1.0 / 60.0);
        }
    }

    #[test]
    fn hex_pixel_roundtrip() {
        for h in scenario::skirmish_map().hexes() {
            let (x, y) = hex_center(h);
            assert_eq!(pixel_to_hex(x, y), h);
            assert_eq!(pixel_to_hex(x + 5.0, y - 6.0), h);
        }
    }

    #[test]
    fn map_fits_in_the_map_area() {
        for h in scenario::skirmish_map().hexes() {
            let (x, y) = hex_center(h);
            assert!(x - HEX * 0.86 >= 0.0 && x + HEX * 0.86 <= SIDE_X as f32, "{h:?}");
            assert!(y - HEX >= TOP as f32 && y + HEX <= H as f32, "{h:?}");
        }
    }

    #[test]
    fn menu_keys_and_buttons() {
        let mut g = game(false);
        assert_eq!(g.screen(), Screen::Menu);
        g.input(Input::Key(Key::Escape));
        assert!(g.quit_requested());
        // Enter starts the focused entry: New Campaign first.
        let mut g = game(false);
        g.input(Input::Key(Key::Enter));
        assert_eq!(g.screen(), Screen::Overworld);
        // Down + Enter: Quick Battle; Down twice: Quit; Up wraps.
        let mut g = game(false);
        g.input(Input::Key(Key::Down));
        g.input(Input::Key(Key::Enter));
        assert_eq!(g.screen(), Screen::Battle);
        let mut g = game(false);
        g.input(Input::Key(Key::Up));
        g.input(Input::Key(Key::Enter));
        assert!(g.quit_requested());
        let mut g = game(false);
        // Click the Quick Battle button, then Resume/Abandon via the pause menu.
        g.input(Input::MouseDown(W as f32 / 2.0, 470.0));
        assert_eq!(g.screen(), Screen::Battle);
        g.input(Input::Key(Key::Escape));
        assert!(g.paused());
        g.input(Input::MouseDown(W as f32 / 2.0, 340.0)); // Resume
        assert!(!g.paused());
        g.input(Input::Key(Key::Escape));
        g.input(Input::MouseDown(W as f32 / 2.0, 410.0)); // Abandon
        assert_eq!(g.screen(), Screen::Menu);
        assert!(g.battle().is_none());
        let mut g = game(false);
        g.input(Input::MouseDown(W as f32 / 2.0, 540.0)); // Quit on menu
        assert!(g.quit_requested());
        let mut g = game(false);
        g.input(Input::MouseDown(W as f32 / 2.0, 400.0)); // New Campaign
        assert_eq!(g.screen(), Screen::Overworld);
    }

    #[test]
    fn click_to_move_then_enemy_turn_then_back_to_player() {
        let mut g = game(false);
        g.start_battle();
        run(&mut g, 1.0);
        let b = g.battle().unwrap();
        assert_eq!(b.active().team, Team::Player);
        let id = b.active_id();
        let start = b.active().pos;
        let (&target, &cost) = g.reach.iter().min_by_key(|(h, _)| (h.q, h.r)).unwrap();
        click_hex(&mut g, target);
        let b = g.battle().unwrap();
        assert_eq!(b.unit(id).pos, target);
        assert_ne!(b.unit(id).pos, start);
        assert_eq!(b.unit(id).ap, AP_PER_TURN - cost);
        // Clicking while the move animation plays is ignored.
        click_hex(&mut g, start);
        assert_eq!(g.battle().unwrap().unit(id).pos, target);
        run(&mut g, 1.0);
        // End turn with Space; the AI should act on its own and eventually hand back control.
        g.input(Input::Key(Key::Space));
        let mut guard = 0;
        while !g.human_turn() && g.battle().unwrap().outcome().is_none() && guard < 6000 {
            g.update(1.0 / 60.0);
            guard += 1;
        }
        assert!(guard < 6000, "control never returned to the player");
    }

    #[test]
    fn illegal_click_is_rejected_without_state_change() {
        let mut g = game(false);
        g.start_battle();
        run(&mut g, 1.0);
        let before = g.battle().unwrap().active().clone();
        // A far-away hex that is not reachable.
        click_hex(&mut g, Hex::from_offset(11, 9));
        let after = g.battle().unwrap().active();
        assert_eq!((before.pos, before.ap), (after.pos, after.ap));
        assert!(g.toast.is_some());
    }

    #[test]
    fn autoplay_reaches_result_screen_and_menu_resets() {
        let mut g = game(true);
        let mut frames = 0;
        while g.screen() != Screen::Result && frames < 60_000 {
            g.update(1.0 / 60.0);
            frames += 1;
        }
        assert_eq!(g.screen(), Screen::Result, "battle did not finish");
        let r = g.result().unwrap();
        assert_eq!(r.survivors.len() + r.casualties.len(), 4);
        g.input(Input::MouseDown(W as f32 / 2.0, 640.0));
        assert_eq!(g.screen(), Screen::Menu);
    }

    #[test]
    fn every_screen_renders() {
        let mut fb = Framebuffer::new(W, H);
        let mut g = game(false);
        g.render(&mut fb);
        g.start_battle();
        run(&mut g, 0.5);
        let dump = |fb: &Framebuffer, name: &str| {
            if let Ok(dir) = std::env::var("HO_DUMP_DIR") {
                std::fs::write(format!("{dir}/{name}.png"), crate::gfx::encode_png(fb)).unwrap();
            }
        };
        // Hover a reachable hex so the path and AP cost are drawn.
        let far = *g.reach.iter().max_by_key(|(h, c)| (**c, h.q)).unwrap().0;
        let (hx, hy) = hex_center(far);
        g.input(Input::MouseMove(hx, hy));
        g.render(&mut fb);
        dump(&fb, "hover-move");
        g.input(Input::Key(Key::Escape));
        g.render(&mut fb);
        dump(&fb, "pause");
        let mut a = game(true);
        for _ in 0..60_000 {
            a.update(1.0 / 60.0);
            if a.screen() == Screen::Result {
                break;
            }
        }
        a.render(&mut fb);
        dump(&fb, "result");
    }

    #[test]
    fn round_limit_defeat_renders_result_and_returns_to_menu() {
        // Two units separated by water never meet: the battle ends by the round limit.
        let map = Map::from_ascii(&["..~..", "..~..", "..~.."]);
        let units = vec![
            Unit::new(0, "Lone", Team::Player, Archetype::Swordsman, Hex::from_offset(0, 1)),
            Unit::new(1, "Far", Team::Enemy, Archetype::Raider, Hex::from_offset(4, 1)),
        ];
        let mut g = game(true);
        g.start_battle_with(Battle::new(map, units, 1), 1);
        let mut fb = Framebuffer::new(W, H);
        let mut frames = 0;
        while g.screen() != Screen::Result && frames < 60_000 {
            g.update(1.0 / 60.0);
            g.render(&mut fb); // must never panic, including right at the timeout
            frames += 1;
        }
        assert_eq!(g.screen(), Screen::Result);
        assert!(g.battle().unwrap().timed_out());
        let r = g.result().unwrap();
        assert!(r.timed_out && r.outcome == Outcome::Defeat);
        g.render(&mut fb);
        g.input(Input::MouseDown(W as f32 / 2.0, 640.0)); // Return to Menu
        assert_eq!(g.screen(), Screen::Menu);
        g.render(&mut fb);
    }

    #[test]
    fn wrap_breaks_on_words() {
        assert_eq!(wrap("aaa bbb ccc", 7), vec!["aaa bbb", "ccc"]);
        assert_eq!(wrap("", 7), Vec::<String>::new());
    }
}
