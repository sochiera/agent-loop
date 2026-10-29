//! Strategic screens: overworld map, town (contract board / recruits / roster), travel,
//! encounter prompt, campaign result and game over. Like the rest of the app this only renders
//! `tactics_core::campaign` state and forwards commands; every rule lives in the core.

use tactics_core::autopilot::{self, Step};
use tactics_core::campaign as cp;
use tactics_core::campaign::{CampaignError, CampaignEvent, GameOverReason, Phase, SiteKind, SITES, TOWN_NAME, TOWN_POS};

use super::*;

/// Real seconds per travelled day (animation only; the core counts whole days).
const DAY_SECS: f32 = 0.9;
const DAY_SECS_AUTOPLAY: f32 = 0.6;
/// The overworld map units are drawn at 1:1 below the top strip.
const MAP_DY: f32 = 94.0;
const PANEL_X: i32 = SIDE_X;
const AUTOPILOT_DELAY: f32 = 0.7;
/// Fallen list on the campaign-over screen: rows live between the header and the pager, above Return to Menu.
pub(super) const FALLEN_PER_PAGE: usize = 8;
const FALLEN_Y0: i32 = 448;
const FALLEN_ROW_H: i32 = 24;
const FALLEN_PAGER_Y: i32 = 658;

fn fallen_pages(total: usize) -> usize {
    total.div_ceil(FALLEN_PER_PAGE).max(1)
}

/// `(index into the fallen list, y)` of every row shown on `page`.
pub(super) fn fallen_rows(page: usize, total: usize) -> Vec<(usize, i32)> {
    (page * FALLEN_PER_PAGE..total.min((page + 1) * FALLEN_PER_PAGE))
        .enumerate()
        .map(|(row, idx)| (idx, FALLEN_Y0 + row as i32 * FALLEN_ROW_H))
        .collect()
}

const SKULL: Color = rgb(222, 214, 196);

/// A modal message on the overworld (payout etc.), dismissed with OK.
pub struct Notice {
    pub title: String,
    pub color: Color,
    pub lines: Vec<(String, Color)>,
}

// ------------------------------------------------------------------ map geometry

fn town_screen() -> (f32, f32) {
    (TOWN_POS.0, TOWN_POS.1 + MAP_DY)
}

fn site_screen(i: usize) -> (f32, f32) {
    (SITES[i].pos.0, SITES[i].pos.1 + MAP_DY)
}

fn road_ctrl(i: usize) -> (f32, f32) {
    let (a, b) = (town_screen(), site_screen(i));
    let (mx, my) = ((a.0 + b.0) / 2.0, (a.1 + b.1) / 2.0);
    let (dx, dy) = (b.0 - a.0, b.1 - a.1);
    let len = (dx * dx + dy * dy).sqrt().max(1.0);
    let off = (i as f32 * 2.3 + 1.0).sin() * 0.16 * len;
    (mx - dy / len * off, my + dx / len * off)
}

/// Point on the road from the town (t = 0) to site `i` (t = 1).
fn road_point(i: usize, t: f32) -> (f32, f32) {
    let (a, c, b) = (town_screen(), road_ctrl(i), site_screen(i));
    let u = 1.0 - t;
    (u * u * a.0 + 2.0 * u * t * c.0 + t * t * b.0, u * u * a.1 + 2.0 * u * t * c.1 + t * t * b.1)
}

fn h2(x: i32, y: i32, salt: u32) -> u32 {
    let mut v = (x.wrapping_mul(73_856_093) ^ y.wrapping_mul(19_349_663)) as u32 ^ salt.wrapping_mul(0x9E37_79B1);
    v ^= v >> 13;
    v = v.wrapping_mul(0x5bd1_e995);
    v ^ (v >> 15)
}

fn vnoise(x: f32, y: f32) -> f32 {
    let (xi, yi) = (x.floor() as i32, y.floor() as i32);
    let (fx, fy) = (x - xi as f32, y - yi as f32);
    let s = |t: f32| t * t * (3.0 - 2.0 * t);
    let v = |a: i32, b: i32| (h2(a, b, 7) % 1000) as f32 / 1000.0;
    let top = v(xi, yi) + (v(xi + 1, yi) - v(xi, yi)) * s(fx);
    let bot = v(xi, yi + 1) + (v(xi + 1, yi + 1) - v(xi, yi + 1)) * s(fx);
    top + (bot - top) * s(fy)
}

fn dist((ax, ay): (f32, f32), (bx, by): (f32, f32)) -> f32 {
    ((ax - bx).powi(2) + (ay - by).powi(2)).sqrt()
}

fn tree(fb: &mut Framebuffer, x: f32, y: f32, s: f32, shade: f32) {
    fb.fill_rect((x - 1.0) as i32, (y + 6.0 * s) as i32, 3, (4.0 * s) as i32 + 1, rgb(70, 48, 30));
    fb.fill_poly(&[(x, y - 12.0 * s), (x + 8.0 * s, y + 8.0 * s), (x - 8.0 * s, y + 8.0 * s)], rgb(30, 76, 44).lighten(shade));
    fb.fill_poly(&[(x, y - 12.0 * s), (x + 3.5 * s, y - 3.0 * s), (x - 3.5 * s, y - 3.0 * s)], rgb(46, 100, 58).lighten(shade));
}

/// The region backdrop (land, river, lake, forests, mountains), drawn once.
fn build_world_bg() -> Framebuffer {
    let mut fb = Framebuffer::new(W, H);
    fb.clear(BG);
    let top = TOP;
    let landmarks: Vec<(f32, f32)> = std::iter::once(town_screen()).chain((0..SITES.len()).map(site_screen)).collect();
    // Land with soft colour patches.
    for gy in (top..H as i32).step_by(14) {
        for gx in (0..SIDE_X).step_by(14) {
            let n = vnoise(gx as f32 / 150.0, gy as f32 / 150.0) * 0.65 + vnoise(gx as f32 / 47.0, gy as f32 / 47.0) * 0.35;
            let meadow = rgb(120, 150, 86);
            let c = if n < 0.5 { rgb(98, 134, 78).lerp(meadow, n * 2.0) } else { meadow.lerp(rgb(160, 156, 96), (n - 0.5) * 1.6) };
            fb.fill_rect(gx, gy, 14, 14, c);
        }
    }
    // River winding top to bottom.
    let river: [(f32, f32); 8] = [
        (610.0, 0.0),
        (585.0, 110.0),
        (640.0, 225.0),
        (595.0, 335.0),
        (645.0, 440.0),
        (600.0, 530.0),
        (650.0, 625.0),
        (625.0, 700.0),
    ];
    for w in river.windows(2) {
        let (a, b) = (w[0], w[1]);
        for k in 0..24 {
            let (t0, t1) = (k as f32 / 24.0, (k + 1) as f32 / 24.0);
            let p = |t: f32| (a.0 + (b.0 - a.0) * t + (t * 9.0 + a.1).sin() * 6.0, a.1 + (b.1 - a.1) * t + MAP_DY);
            let (p0, p1) = (p(t0), p(t1));
            fb.line(p0.0, p0.1, p1.0, p1.1, 17.0, rgb(40, 86, 150));
            fb.line(p0.0, p0.1, p1.0, p1.1, 13.0, rgb(58, 112, 178));
        }
    }
    // A lake.
    let lake: Vec<(f32, f32)> = (0..18)
        .map(|k| {
            let a = k as f32 / 18.0 * std::f32::consts::TAU;
            (240.0 + a.cos() * (70.0 + (a * 3.0).sin() * 9.0), 150.0 + MAP_DY + a.sin() * (36.0 + (a * 2.0).cos() * 6.0))
        })
        .collect();
    fb.fill_poly(&lake, rgb(46, 98, 160));
    fb.poly_outline(&lake, 2.0, rgb(30, 70, 120));
    for k in 0..5 {
        let (x, y) = (200.0 + k as f32 * 17.0, 150.0 + MAP_DY + (k as f32 * 1.7).sin() * 10.0);
        fb.line(x, y, x + 12.0, y, 2.0, rgb(130, 180, 230));
    }
    // Forest stands.
    for k in 0..46 {
        let cx = (h2(k, 1, 11) % 960) as f32 + 20.0;
        let cy = (h2(k, 2, 11) % 680) as f32 + MAP_DY + 10.0;
        if landmarks.iter().any(|&l| dist(l, (cx, cy)) < 90.0) || dist((cx, cy), (240.0, 150.0 + MAP_DY)) < 100.0 {
            continue;
        }
        if (cx - 620.0).abs() < 55.0 {
            continue; // keep the river clear
        }
        let n = 4 + (h2(k, 3, 11) % 5) as i32;
        for t in 0..n {
            let ox = (h2(k, t + 10, 12) % 70) as f32 - 35.0;
            let oy = (h2(k, t + 30, 12) % 44) as f32 - 22.0;
            tree(&mut fb, cx + ox, cy + oy, 0.8 + (h2(k, t, 13) % 5) as f32 / 12.0, (h2(k, t, 14) % 6) as f32 / 40.0);
        }
    }
    // Mountains in the north-east.
    for k in 0..16 {
        let x = 700.0 + (h2(k, 5, 21) % 280) as f32;
        let y = 20.0 + (h2(k, 6, 21) % 140) as f32 + MAP_DY + (x - 700.0) * 0.25;
        if landmarks.iter().any(|&l| dist(l, (x, y)) < 70.0) || x > 975.0 {
            continue;
        }
        let s = 0.9 + (h2(k, 7, 21) % 6) as f32 / 8.0;
        fb.fill_poly(&[(x - 30.0 * s, y + 20.0), (x, y - 36.0 * s), (x + 30.0 * s, y + 20.0)], rgb(112, 108, 114));
        fb.fill_poly(&[(x, y - 36.0 * s), (x + 30.0 * s, y + 20.0), (x + 6.0 * s, y + 20.0)], rgb(86, 84, 92));
        fb.fill_poly(&[(x, y - 36.0 * s), (x + 9.0 * s, y - 20.0 * s), (x - 9.0 * s, y - 20.0 * s)], rgb(235, 236, 240));
    }
    // Roads: dusty dotted tracks town -> site.
    for i in 0..SITES.len() {
        for k in 0..60 {
            let p = road_point(i, k as f32 / 60.0);
            fb.disc(p.0, p.1, 3.2, rgb(186, 160, 108));
            fb.disc(p.0, p.1, 1.8, rgb(212, 190, 140));
        }
    }
    // Frame and vignette.
    fb.rect_outline(0, top, SIDE_X, H as i32 - top, 6, rgba(20, 16, 10, 140));
    fb.rect_outline(6, top + 6, SIDE_X - 12, H as i32 - top - 12, 2, rgba(235, 200, 120, 90));
    fb
}

fn draw_site_icon(fb: &mut Framebuffer, kind: SiteKind, (x, y): (f32, f32)) {
    let stone = rgb(170, 170, 176);
    let dark = rgb(60, 52, 46);
    fb.disc(x, y + 6.0, 30.0, rgba(0, 0, 0, 50));
    match kind {
        SiteKind::Mill => {
            fb.fill_rect(x as i32 - 15, y as i32 - 8, 30, 22, rgb(166, 128, 84));
            fb.fill_poly(&[(x - 20.0, y - 8.0), (x, y - 26.0), (x + 20.0, y - 8.0)], rgb(150, 66, 52));
            fb.fill_rect(x as i32 - 4, y as i32 + 2, 8, 12, dark);
            fb.circle(x + 22.0, y + 4.0, 6.0, 10.0, rgb(52, 96, 150));
            fb.line(x + 22.0, y - 6.0, x + 22.0, y + 14.0, 2.0, rgb(52, 96, 150));
            fb.line(x + 12.0, y + 4.0, x + 32.0, y + 4.0, 2.0, rgb(52, 96, 150));
        }
        SiteKind::Gallows => {
            fb.fill_poly(&[(x - 28.0, y + 16.0), (x - 8.0, y - 8.0), (x + 10.0, y - 8.0), (x + 28.0, y + 16.0)], rgb(150, 122, 80));
            fb.line(x, y - 8.0, x, y - 32.0, 3.5, dark);
            fb.line(x, y - 32.0, x + 16.0, y - 32.0, 3.5, dark);
            fb.line(x + 16.0, y - 32.0, x + 16.0, y - 21.0, 1.5, rgb(210, 190, 140));
            fb.disc(x + 16.0, y - 19.0, 2.5, rgb(210, 190, 140));
        }
        SiteKind::Ruins => {
            for (ox, h) in [(-18.0, 26.0), (-6.0, 34.0), (7.0, 18.0), (19.0, 28.0)] {
                fb.fill_rect((x + ox) as i32 - 4, (y + 14.0 - h) as i32, 8, h as i32, stone);
                fb.fill_rect((x + ox) as i32 - 4, (y + 14.0 - h) as i32, 3, h as i32, stone.lighten(0.25));
            }
            fb.fill_poly(&[(x - 26.0, y + 16.0), (x - 18.0, y + 8.0), (x + 26.0, y + 8.0), (x + 28.0, y + 16.0)], rgb(120, 118, 120));
            fb.disc(x + 2.0, y + 12.0, 4.0, rgb(40, 40, 44));
        }
        SiteKind::Camp => {
            for (ox, s) in [(-12.0, 1.0), (13.0, 0.85)] {
                fb.fill_poly(&[(x + ox - 15.0 * s, y + 12.0), (x + ox, y - 22.0 * s), (x + ox + 15.0 * s, y + 12.0)], rgb(190, 60, 50));
                fb.fill_poly(&[(x + ox, y - 22.0 * s), (x + ox + 15.0 * s, y + 12.0), (x + ox + 3.0, y + 12.0)], rgb(140, 40, 36));
            }
            for k in 0..8 {
                let sx = x - 28.0 + k as f32 * 8.0;
                fb.fill_poly(&[(sx - 2.5, y + 20.0), (sx, y + 10.0), (sx + 2.5, y + 20.0)], rgb(110, 80, 50));
            }
        }
        SiteKind::Fort => {
            fb.fill_rect(x as i32 - 13, y as i32 - 26, 26, 42, stone);
            fb.fill_rect(x as i32 - 13, y as i32 - 26, 8, 42, stone.lighten(0.2));
            for k in 0..3 {
                fb.fill_rect(x as i32 - 15 + k * 11, y as i32 - 33, 7, 8, stone);
            }
            fb.fill_rect(x as i32 - 3, y as i32 + 4, 7, 12, dark);
            fb.fill_rect(x as i32 - 2, y as i32 - 16, 5, 7, dark);
            fb.line(x, y - 33.0, x, y - 46.0, 2.0, dark);
            fb.fill_poly(&[(x, y - 46.0), (x + 14.0, y - 41.0), (x, y - 36.0)], ENEMY);
        }
    }
}

fn draw_town_icon(fb: &mut Framebuffer, (x, y): (f32, f32)) {
    fb.disc(x, y + 6.0, 44.0, rgba(0, 0, 0, 60));
    fb.disc(x, y + 4.0, 38.0, rgb(150, 124, 86));
    fb.circle(x, y + 4.0, 34.0, 38.0, rgb(112, 108, 112));
    for (ox, oy, w) in [(-24.0, 6.0, 16), (-4.0, 16.0, 14), (20.0, 10.0, 16), (-20.0, -14.0, 14), (22.0, -10.0, 14)] {
        let (hx, hy) = (x + ox, y + oy);
        fb.fill_rect(hx as i32 - w / 2, hy as i32 - 4, w, 12, rgb(196, 168, 122));
        fb.fill_poly(&[(hx - w as f32 / 2.0 - 2.0, hy - 4.0), (hx, hy - 15.0), (hx + w as f32 / 2.0 + 2.0, hy - 4.0)], rgb(150, 66, 52));
    }
    fb.fill_rect(x as i32 - 8, y as i32 - 22, 16, 28, rgb(160, 160, 168));
    fb.fill_rect(x as i32 - 8, y as i32 - 22, 5, 28, rgb(196, 196, 204));
    for k in 0..3 {
        fb.fill_rect(x as i32 - 9 + k * 6, y as i32 - 27, 4, 6, rgb(160, 160, 168));
    }
    fb.line(x, y - 27.0, x, y - 42.0, 2.0, rgb(60, 52, 46));
    fb.fill_poly(&[(x, y - 42.0), (x + 15.0, y - 37.0), (x, y - 32.0)], GOLD);
}

fn draw_skull(fb: &mut Framebuffer, cx: f32, cy: f32, size: f32, col: Color) {
    fb.disc(cx, cy - size * 0.1, size * 0.5, col);
    fb.fill_rect((cx - size * 0.28) as i32, (cy + size * 0.2) as i32, (size * 0.56) as i32, (size * 0.34) as i32, col);
    let eye = rgb(20, 20, 24);
    fb.disc(cx - size * 0.2, cy - size * 0.1, size * 0.14, eye);
    fb.disc(cx + size * 0.2, cy - size * 0.1, size * 0.14, eye);
    fb.fill_rect((cx - size * 0.04) as i32, (cy + size * 0.08) as i32, (size * 0.08).max(1.0) as i32, (size * 0.14).max(1.0) as i32, eye);
    for k in -1..=1 {
        fb.fill_rect((cx + k as f32 * size * 0.15 - 1.0) as i32, (cy + size * 0.34) as i32, 2, (size * 0.2) as i32, eye);
    }
}

fn skulls(fb: &mut Framebuffer, x: f32, y: f32, n: u8, size: f32) {
    for k in 0..3 {
        let col = if (k as u8) < n { SKULL } else { rgb(60, 62, 70) };
        draw_skull(fb, x + k as f32 * (size + 6.0), y, size, col);
    }
}

fn bar(fb: &mut Framebuffer, x: i32, y: i32, w: i32, h: i32, frac: f32, col: Color) {
    fb.fill_rect(x, y, w, h, rgb(10, 10, 14));
    fb.fill_rect(x + 1, y + 1, ((w - 2) as f32 * frac.clamp(0.0, 1.0)) as i32, h - 2, col);
}

fn hp_color(frac: f32) -> Color {
    if frac > 0.6 {
        rgb(80, 190, 90)
    } else if frac > 0.3 {
        rgb(220, 180, 60)
    } else {
        rgb(220, 70, 60)
    }
}

fn panel(fb: &mut Framebuffer, r: Rect, fill: Color, edge: Color) {
    fb.fill_rect(r.x, r.y, r.w, r.h, fill);
    fb.rect_outline(r.x, r.y, r.w, r.h, 2, edge);
}

fn main_skill(arch: Archetype, level: u32) -> i32 {
    let s = arch.stats_at_level(level);
    s.melee_skill.max(s.ranged_skill)
}

/// "MAX HP 65 (+5)  SKILL 74 (+2)  DEF 21 (+1)": visible level-ups.
fn stat_line(arch: Archetype, level: u32) -> String {
    let s = arch.stats_at_level(level);
    let b = LevelBonus::for_level(level);
    if level > 1 {
        format!("MAX HP {} (+{})  SKILL {} (+{})  DEF {} (+{})", s.max_hp, b.hp, main_skill(arch, level), b.skill, s.defense, b.defense)
    } else {
        format!("MAX HP {}  SKILL {}  DEF {}", s.max_hp, main_skill(arch, level), s.defense)
    }
}

fn rect(x: i32, y: i32, w: i32, h: i32) -> Rect {
    Rect { x, y, w, h }
}

fn button(action: Action, r: Rect, label: impl Into<String>, enabled: bool) -> Button {
    Button { action, rect: r, label: label.into(), enabled, hidden: false }
}

// ------------------------------------------------------------------------ Game

impl Game {
    pub fn view(&self) -> (&'static str, f32) {
        self.view
    }

    fn day_secs(&self) -> f32 {
        if self.opts.autoplay_campaign {
            DAY_SECS_AUTOPLAY
        } else {
            DAY_SECS
        }
    }

    fn phase(&self) -> Option<Phase> {
        self.campaign.as_ref().map(|c| c.phase)
    }

    /// Label of what is on screen (used by `--shot` to capture specific views).
    fn view_label(&self) -> &'static str {
        if self.paused {
            return "pause";
        }
        match self.screen {
            Screen::Menu => "menu",
            Screen::Town => "town",
            Screen::Result => "result",
            Screen::GameOver => "gameover",
            Screen::Battle => "battle",
            Screen::Overworld => {
                if self.notice.is_some() {
                    "notice"
                } else {
                    match self.phase() {
                        Some(Phase::Travel(_)) => "travel",
                        Some(Phase::Encounter | Phase::Empty) => "encounter",
                        _ => "overworld",
                    }
                }
            }
        }
    }

    pub(super) fn update_view(&mut self, dt: f32) {
        let label = self.view_label();
        if label != self.view.0 {
            self.view = (label, 0.0);
            if self.opts.autoplay_campaign {
                self.ap_timer = self.ap_timer.max(1.0);
            }
        } else {
            self.view.1 += dt;
        }
    }

    pub fn start_campaign(&mut self) {
        let seed = self.next_seed;
        self.next_seed = self.next_seed.wrapping_add(1);
        self.to_menu();
        self.campaign = Some(Campaign::new(seed));
        self.screen = Screen::Overworld;
        self.travel_clock = 0.0;
    }

    /// Where the party marker is drawn right now.
    fn party_pos(&self) -> (f32, f32) {
        let Some(c) = &self.campaign else { return town_screen() };
        let at = |s: Option<usize>| s.map_or(town_screen(), |s| road_point(s, 1.0));
        match c.phase {
            Phase::Travel(t) => road_point(t.site, t.progress_from_town(self.travel_clock / self.day_secs())),
            Phase::Encounter | Phase::Empty | Phase::Battle | Phase::Aftermath => at(c.at_site),
            Phase::Town | Phase::GameOver => town_screen(),
        }
    }

    fn after_campaign_change(&mut self) {
        if self.phase() == Some(Phase::GameOver) {
            self.screen = Screen::GameOver;
            self.notice = None;
        }
    }

    fn try_campaign<R>(&mut self, f: impl FnOnce(&mut Campaign) -> Result<R, CampaignError>) -> Option<R> {
        let r = f(self.campaign.as_mut()?);
        let out = match r {
            Ok(v) => Some(v),
            Err(e) => {
                self.toast(e.describe());
                None
            }
        };
        self.after_campaign_change();
        out
    }

    fn handle_campaign_events(&mut self, events: Vec<CampaignEvent>) {
        for ev in events {
            match ev {
                CampaignEvent::DayPassed { day, wages } => {
                    let p = self.party_pos();
                    self.floats.push(Floating {
                        pos: (p.0, p.1 - 34.0),
                        text: format!("DAY {day}  WAGES -{wages}"),
                        color: GOLD,
                        age: 0.0,
                        life: 1.6,
                    });
                }
                CampaignEvent::ContractLapsed { .. } => self.toast("The contract lapsed"),
                CampaignEvent::Paid { amount } => {
                    let gold = self.campaign.as_ref().map_or(0, |c| c.gold);
                    self.notice = Some(Notice {
                        title: "CONTRACT COMPLETE".into(),
                        color: GOOD,
                        lines: vec![
                            (format!("THE EMPLOYER PAYS {amount} GOLD"), GOLD),
                            (format!("THE COMPANY NOW HOLDS {gold} GOLD"), TEXT),
                            ("REST, HIRE, AND PICK THE NEXT CONTRACT IN TOWN".into(), DIM),
                        ],
                    });
                }
                CampaignEvent::ArrivedHome => self.toast(format!("Back in {TOWN_NAME}")),
                CampaignEvent::Arrived { .. } | CampaignEvent::GameOver(_) => {}
            }
        }
        self.after_campaign_change();
    }

    fn travel_one_day(&mut self) {
        let Some(c) = self.campaign.as_mut() else { return };
        if let Ok(ev) = c.travel_day() {
            self.handle_campaign_events(ev);
        }
    }

    fn depart(&mut self, site: usize) {
        if self.try_campaign(|c| c.depart(site)).is_some() {
            self.travel_clock = 0.0;
            self.leveled.clear();
            self.screen = Screen::Overworld;
        }
    }

    pub(super) fn perform_strategic(&mut self, action: Action) {
        match action {
            Action::NewCampaign => self.start_campaign(),
            Action::Pause => self.paused = true,
            Action::DismissNotice => self.notice = None,
            Action::OpenTown => {
                if self.phase() == Some(Phase::Town) {
                    self.screen = Screen::Town;
                }
            }
            Action::LeaveTown => self.screen = Screen::Overworld,
            Action::SelectSite(i) => {
                if self.phase() == Some(Phase::Town) {
                    self.site_sel = Some(i);
                }
            }
            Action::Travel => {
                if let Some(s) = self.site_sel {
                    self.depart(s);
                }
            }
            Action::Depart => {
                if let Some(s) = self.campaign.as_ref().and_then(|c| c.active.as_ref()).map(|k| k.site) {
                    self.depart(s);
                }
            }
            Action::Accept(i) => {
                if let Some(site) = self.try_campaign(|c| c.accept_contract(i).map(|()| c.active.as_ref().map(|k| k.site))) {
                    self.site_sel = site;
                    self.toast("Contract accepted");
                }
            }
            Action::CancelContract => {
                if self.try_campaign(|c| c.cancel_contract()).is_some() {
                    self.toast("Contract cancelled");
                }
            }
            Action::Hire(i) => {
                let name = self.campaign.as_ref().and_then(|c| c.recruits.get(i)).map(|r| r.name.clone());
                if self.try_campaign(|c| c.hire(i)).is_some() {
                    self.toast(format!("{} joins the company", name.unwrap_or_default()));
                }
            }
            Action::FallenPrev => self.fallen_page = self.fallen_page.saturating_sub(1),
            Action::FallenNext => {
                let total = self.campaign.as_ref().map_or(0, |c| c.fallen.len());
                self.fallen_page = (self.fallen_page + 1).min(fallen_pages(total) - 1);
            }
            Action::Rest => {
                if let Some(ev) = self.try_campaign(|c| c.rest()) {
                    let wages = ev.iter().find_map(|e| if let CampaignEvent::DayPassed { wages, .. } = e { Some(*wages) } else { None });
                    self.toast(format!("Rested a day: wounds heal, wages -{}", wages.unwrap_or(0)));
                    self.handle_campaign_events(ev.into_iter().filter(|e| !matches!(e, CampaignEvent::DayPassed { .. })).collect());
                }
            }
            Action::ToggleDeploy(id) => {
                let on = self.campaign.as_ref().and_then(|c| c.merc(id)).is_some_and(|m| !m.deploy);
                self.try_campaign(|c| c.set_deploy(id, on));
            }
            Action::Fight => {
                self.notice = None;
                if let Some(b) = self.try_campaign(|c| c.begin_battle()) {
                    self.report = None;
                    self.leveled.clear();
                    let seed = b.seed();
                    self.start_battle_with(b, seed);
                }
            }
            Action::Retreat => {
                if self.try_campaign(|c| c.retreat()).is_some() {
                    self.travel_clock = 0.0;
                    self.site_sel = None;
                }
            }
            Action::Continue => {
                if let Some(c) = self.campaign.as_mut() {
                    let _ = c.continue_after_battle();
                }
                self.battle = None;
                self.result = None;
                self.report = None;
                self.travel_clock = 0.0;
                self.site_sel = None;
                self.screen = Screen::Overworld;
                self.after_campaign_change();
            }
            _ => {}
        }
    }

    pub(super) fn strategic_key(&mut self, k: Key) {
        if self.paused {
            if matches!(k, Key::Escape | Key::Enter) {
                self.paused = false;
            }
            return;
        }
        if self.screen == Screen::Town {
            match k {
                Key::Escape => self.perform(Action::LeaveTown),
                Key::Enter => self.perform(Action::Depart),
                _ => {}
            }
            return;
        }
        if self.notice.is_some() {
            if matches!(k, Key::Enter | Key::Escape | Key::Space) {
                self.perform(Action::DismissNotice);
            }
            return;
        }
        match (self.phase(), k) {
            (Some(Phase::Encounter), Key::Enter) => self.perform(Action::Fight),
            (Some(Phase::Encounter), Key::Escape) => self.perform(Action::Retreat),
            (Some(Phase::Empty), Key::Enter | Key::Escape) => self.perform(Action::Retreat),
            (Some(Phase::Town), Key::Enter) => self.perform(Action::OpenTown),
            (_, Key::Escape) => self.paused = true,
            _ => {}
        }
    }

    pub(super) fn strategic_buttons(&self) -> Vec<Button> {
        let Some(c) = &self.campaign else { return Vec::new() };
        match self.screen {
            Screen::Town => self.town_buttons(c),
            _ => self.overworld_buttons(c),
        }
    }

    fn overworld_buttons(&self, c: &Campaign) -> Vec<Button> {
        let modal_btn = |action, x, label: &str, w| button(action, rect(x, 490, w, 54), label, true);
        if self.notice.is_some() {
            return vec![modal_btn(Action::DismissNotice, 350, "OK", 300)];
        }
        match c.phase {
            Phase::Encounter => {
                return vec![
                    modal_btn(Action::Fight, 190, "FIGHT", 300),
                    modal_btn(Action::Retreat, 510, "RETREAT TO TOWN", 300),
                ]
            }
            Phase::Empty => return vec![modal_btn(Action::Retreat, 350, "RETURN TO TOWN", 300)],
            _ => {}
        }
        let mut v = Vec::new();
        if c.phase == Phase::Town {
            for i in 0..SITES.len() {
                let (x, y) = site_screen(i);
                v.push(Button { action: Action::SelectSite(i), rect: rect(x as i32 - 42, y as i32 - 44, 84, 90), label: String::new(), enabled: true, hidden: true });
            }
            let (x, y) = town_screen();
            v.push(Button { action: Action::OpenTown, rect: rect(x as i32 - 46, y as i32 - 50, 92, 96), label: String::new(), enabled: true, hidden: true });
            v.push(button(Action::OpenTown, rect(PANEL_X + 14, 668, 372, 44), "ENTER TOWN  [ENTER]", true));
            if let Some(s) = self.site_sel {
                let label = format!("GO: {} ({} DAYS)", SITES[s].name.to_uppercase(), SITES[s].days);
                v.push(button(Action::Travel, rect(PANEL_X + 14, 718, 372, 44), label, true));
            } else {
                v.push(button(Action::Travel, rect(PANEL_X + 14, 718, 372, 44), "CLICK A SITE TO TRAVEL", false));
            }
        }
        v.push(button(Action::Pause, rect(PANEL_X + 14, 768, 372, 40), "MENU  [ESC]", true));
        v
    }

    /// Prev / Next buttons for the fallen list (only when it needs more than one page).
    pub(super) fn game_over_pager(&self) -> Vec<Button> {
        let total = self.campaign.as_ref().map_or(0, |c| c.fallen.len());
        let pages = fallen_pages(total);
        if pages <= 1 {
            return Vec::new();
        }
        let cx = W as i32 / 2;
        vec![
            button(Action::FallenPrev, rect(cx - 330, FALLEN_PAGER_Y, 150, 34), "< PREV", self.fallen_page > 0),
            button(Action::FallenNext, rect(cx + 180, FALLEN_PAGER_Y, 150, 34), "NEXT >", self.fallen_page + 1 < pages),
        ]
    }

    fn town_buttons(&self, c: &Campaign) -> Vec<Button> {
        let mut v = Vec::new();
        let mut y = 106;
        if c.active.is_some() {
            v.push(button(Action::CancelContract, rect(20 + 670 - 140, y + 112 - 46, 128, 36), "CANCEL", true));
            y += 118;
        }
        for i in 0..c.board.len() {
            v.push(button(Action::Accept(i), rect(20 + 670 - 140, y + 112 - 46, 128, 36), "ACCEPT", c.active.is_none()));
            y += 118;
        }
        for (i, r) in c.recruits.iter().enumerate() {
            let ok = c.gold >= r.cost && c.roster.len() < cp::ROSTER_CAP;
            v.push(button(Action::Hire(i), rect(710 + 670 - 110, 106 + i as i32 * 82 + 76 - 42, 100, 34), "HIRE", ok));
        }
        for (i, m) in c.roster.iter().enumerate() {
            let label = if m.deploy { "IN PARTY" } else { "RESERVE" };
            v.push(button(Action::ToggleDeploy(m.id), rect(710 + 670 - 100, 392 + i as i32 * 52 + 9, 92, 30), label, true));
        }
        v.push(button(Action::Rest, rect(20, 736, 440, 52), "REST A DAY", true));
        let dep = match &c.active {
            Some(k) => (format!("DEPART: {}", k.site_def().name.to_uppercase()), true),
            None => ("ACCEPT A CONTRACT".to_string(), false),
        };
        v.push(button(Action::Depart, rect(480, 736, 460, 52), dep.0, dep.1));
        v.push(button(Action::LeaveTown, rect(960, 736, 420, 52), "LEAVE TOWN", true));
        v
    }

    // ----- update ----------------------------------------------------------

    pub(super) fn tick_strategic(&mut self, dt: f32) {
        if self.paused {
            return;
        }
        if self.opts.autoplay_campaign && self.screen == Screen::Menu && !self.ap_started && self.frames > 3 {
            self.ap_started = true;
            self.start_campaign();
            self.ap_timer = 1.0;
        }
        if self.screen == Screen::Overworld && self.notice.is_none() && matches!(self.phase(), Some(Phase::Travel(_))) {
            self.travel_clock += dt;
            let day = self.day_secs();
            while self.travel_clock >= day && matches!(self.phase(), Some(Phase::Travel(_))) {
                self.travel_clock -= day;
                self.travel_one_day();
            }
            if !matches!(self.phase(), Some(Phase::Travel(_))) {
                self.travel_clock = 0.0;
            }
        }
        if self.opts.autoplay_campaign && self.ap_started && self.screen != Screen::Battle {
            self.ap_timer -= dt;
            if self.ap_timer <= 0.0 {
                self.ap_timer = AUTOPILOT_DELAY;
                self.autopilot_step();
            }
        }
    }

    /// One decision of the campaign autopilot (`--autoplay-campaign`), driven through the same
    /// actions a mouse click would perform.
    fn autopilot_step(&mut self) {
        let Some(c) = &self.campaign else { return };
        let step = autopilot::next_step(c);
        match self.screen {
            Screen::Overworld => {
                if self.notice.is_some() {
                    return self.perform(Action::DismissNotice);
                }
                match (c.phase, step) {
                    (Phase::Town, _) => self.perform(Action::OpenTown),
                    (Phase::Encounter, _) => self.perform(Action::Fight),
                    (Phase::Empty, _) => self.perform(Action::Retreat),
                    _ => {}
                }
            }
            Screen::Town => match step {
                Step::Rest => self.perform(Action::Rest),
                Step::Hire(i) => self.perform(Action::Hire(i)),
                Step::Accept(i) => self.perform(Action::Accept(i)),
                Step::Depart(_) => self.perform(Action::Depart),
                _ => self.perform(Action::LeaveTown),
            },
            Screen::Result => self.perform(if self.report.is_some() { Action::Continue } else { Action::ReturnToMenu }),
            Screen::GameOver => {
                self.ap_timer = 3.0;
                self.perform(Action::ReturnToMenu)
            }
            Screen::Menu | Screen::Battle => {}
        }
    }

    // ----- rendering -------------------------------------------------------

    fn draw_toast_at(&self, fb: &mut Framebuffer, cx: i32, y: i32) {
        if let Some((msg, t)) = &self.toast {
            let a = (t / 0.5).min(1.0);
            let w = font::text_width(msg, 2) + 24;
            fb.fill_rect(cx - w / 2, y, w, 30, rgba(60, 40, 18, (255.0 * a) as u8));
            fb.rect_outline(cx - w / 2, y, w, 30, 1, GOLD.fade(a));
            fb.text(cx - w / 2 + 12, y + 8, &msg.to_uppercase(), 2, rgb(255, 235, 190).fade(a));
        }
    }

    fn draw_modal(&self, fb: &mut Framebuffer, title: &str, color: Color, skull_count: Option<u8>, lines: &[(String, Color)]) {
        fb.fill_rect(0, 64, SIDE_X, H as i32 - 64, rgba(0, 0, 0, 130));
        let r = rect(150, 150, 700, 420);
        panel(fb, r, PANEL, PANEL_EDGE);
        fb.fill_rect(r.x, r.y, r.w, 6, color);
        fb.text_centered(r.x + r.w / 2, r.y + 28, title, 4, color);
        let mut y = r.y + 84;
        if let Some(n) = skull_count {
            skulls(fb, (r.x + r.w / 2) as f32 - 28.0, y as f32 + 14.0, n, 22.0);
            y += 40;
        }
        for (t, c) in lines {
            fb.text_centered(r.x + r.w / 2, y, t, 2, *c);
            y += 28;
        }
    }

    fn draw_merc_rows(&self, fb: &mut Framebuffer, c: &Campaign, x: i32, y0: i32, w: i32, row_h: i32) {
        for (i, m) in c.roster.iter().enumerate() {
            let y = y0 + i as i32 * row_h;
            fb.fill_rect(x, y, w, row_h - 4, rgb(24, 27, 35));
            let up = self.leveled.contains(&m.id);
            fb.fill_rect(x, y, 4, row_h - 4, if m.deploy { PLAYER } else { rgb(70, 74, 86) });
            fb.text(x + 12, y + 4, &m.name.to_uppercase(), 2, if up { GOLD } else { TEXT });
            let lv = format!("LV {}", m.level);
            fb.text(x + w - font::text_width(&lv, 2) - 8, y + 4, &lv, 2, GOLD);
            let tag = format!("{}  {}", m.archetype.label().to_uppercase(), if m.deploy { "PARTY" } else { "RESERVE" });
            fb.text(x + 12, y + 22, &tag, 1, DIM);
            if up {
                fb.text(x + w - 130, y + 22, "LEVEL UP!", 1, GOLD);
            } else if m.wounded() {
                fb.text(x + w - 130, y + 22, "WOUNDED", 1, BAD);
            }
            let bw = w - 110;
            let frac = m.hp as f32 / m.max_hp() as f32;
            bar(fb, x + 12, y + 33, bw, 11, frac, hp_color(frac));
            fb.text(x + 12 + bw + 6, y + 34, &format!("HP {}/{}", m.hp, m.max_hp()), 1, TEXT);
            let next = m.next_level_xp();
            let xf = next.map_or(1.0, |n| {
                let base = cp::xp_to_reach(m.level);
                (m.xp - base) as f32 / (n - base) as f32
            });
            bar(fb, x + 12, y + 47, bw, 6, xf, rgb(120, 140, 230));
            let xt = next.map_or(format!("XP {} MAX", m.xp), |n| format!("XP {}/{}", m.xp, n));
            fb.text(x + 12 + bw + 6, y + 47, &xt, 1, DIM);
            if row_h >= 70 {
                fb.text(x + 12, y + 57, &stat_line(m.archetype, m.level), 1, DIM);
            }
        }
    }

    pub(super) fn render_overworld(&self, fb: &mut Framebuffer) {
        let Some(c) = &self.campaign else { return };
        let bg = self.world_bg.get_or_init(build_world_bg);
        fb.blit_region(bg, 0, TOP as usize, SIDE_X as usize, H - TOP as usize);
        // Top strip.
        fb.fill_rect(0, 0, W as i32, TOP, PANEL);
        fb.fill_rect(0, TOP - 2, W as i32, 2, PANEL_EDGE);
        fb.text(14, 10, "THE REGION", 3, GOLD);
        fb.text(14, 40, &format!("{}  BASE: {}", c.company.to_uppercase(), TOWN_NAME.to_uppercase()), 1, DIM);
        let wages = c.daily_wages();
        let gold_col = if c.gold < 0 { BAD } else { GOLD };
        fb.text(420, 10, &format!("DAY {}", c.day), 3, TEXT);
        fb.text(600, 10, &format!("GOLD {}", c.gold), 3, gold_col);
        fb.text(820, 10, &format!("WAGES {}/DAY", wages), 2, DIM);
        fb.text(820, 36, &format!("CONTRACTS DONE {}", c.contracts_completed), 1, DIM);

        // Sites.
        let hover_site = if self.phase() == Some(Phase::Town) && self.notice.is_none() {
            self.buttons().iter().find(|b| b.rect.contains(self.mouse.0, self.mouse.1)).and_then(|b| match b.action {
                Action::SelectSite(i) => Some(i),
                _ => None,
            })
        } else {
            None
        };
        let contract_site = c.active.as_ref().map(|k| k.site);
        for (i, s) in SITES.iter().enumerate() {
            let p = site_screen(i);
            if contract_site == Some(i) {
                let pulse = 0.5 + 0.5 * (self.time * 4.0).sin();
                fb.circle(p.0, p.1 + 2.0, 40.0, 43.0 + pulse * 2.0, GOLD);
            }
            if self.site_sel == Some(i) {
                fb.circle(p.0, p.1 + 2.0, 36.0, 38.0, rgb(255, 255, 255));
            } else if hover_site == Some(i) {
                fb.circle(p.0, p.1 + 2.0, 36.0, 38.0, rgba(255, 255, 255, 150));
            }
            draw_site_icon(fb, s.kind, p);
            fb.text_centered(p.0 as i32 + 1, p.1 as i32 + 31, &s.name.to_uppercase(), 2, rgba(0, 0, 0, 160));
            fb.text_centered(p.0 as i32, p.1 as i32 + 30, &s.name.to_uppercase(), 2, rgb(255, 250, 235));
            fb.text_centered(p.0 as i32, p.1 as i32 + 50, &format!("{} DAY{}", s.days, if s.days == 1 { "" } else { "S" }), 1, rgb(255, 235, 190));
            if let Some(k) = c.active.as_ref().filter(|k| k.site == i) {
                skulls(fb, p.0 - 30.0, p.1 - 56.0, k.difficulty, 14.0);
            }
        }
        let tp = town_screen();
        draw_town_icon(fb, tp);
        fb.text_centered(tp.0 as i32 + 1, tp.1 as i32 + 47, &TOWN_NAME.to_uppercase(), 2, rgba(0, 0, 0, 170));
        fb.text_centered(tp.0 as i32, tp.1 as i32 + 46, &TOWN_NAME.to_uppercase(), 2, GOLD);
        if self.phase() == Some(Phase::Town) && self.buttons().iter().any(|b| b.hidden && matches!(b.action, Action::OpenTown) && b.rect.contains(self.mouse.0, self.mouse.1)) {
            fb.circle(tp.0, tp.1 + 4.0, 42.0, 44.0, rgba(255, 255, 255, 170));
        }

        // Party marker.
        let pp = self.party_pos();
        let pulse = 0.5 + 0.5 * (self.time * 5.0).sin();
        fb.disc(pp.0 + 2.0, pp.1 + 5.0, 13.0, rgba(0, 0, 0, 90));
        fb.circle(pp.0, pp.1, 14.0, 16.0 + pulse * 3.0, GOLD);
        fb.disc(pp.0, pp.1, 13.0, rgb(14, 14, 20));
        fb.disc(pp.0, pp.1, 10.0, PLAYER);
        fb.disc(pp.0, pp.1 - 2.0, 6.0, PLAYER.lighten(0.3));
        fb.line(pp.0 + 2.0, pp.1 - 4.0, pp.0 + 2.0, pp.1 - 30.0, 2.5, rgb(40, 30, 20));
        fb.fill_poly(&[(pp.0 + 2.0, pp.1 - 30.0), (pp.0 + 20.0, pp.1 - 24.0), (pp.0 + 2.0, pp.1 - 18.0)], GOLD);
        for f in &self.floats {
            let k = f.age / f.life;
            let y = f.pos.1 - k * 30.0;
            let w = font::text_width(&f.text, 2);
            fb.text_shadow(f.pos.0 as i32 - w / 2, y as i32 - 8, &f.text, 2, f.color.fade(1.0 - k * k));
        }

        // Status line.
        fb.fill_rect(0, H as i32 - 40, SIDE_X, 40, rgba(14, 12, 10, 215));
        fb.fill_rect(0, H as i32 - 40, SIDE_X, 2, GOLD.darken(0.4));
        let goal = c.goal().to_uppercase();
        let goal = goal.strip_prefix("GOAL: ").map_or_else(|| goal.clone(), str::to_string);
        fb.text(14, H as i32 - 29, "GOAL", 2, GOLD);
        fb.text(86, H as i32 - 29, &goal, 2, TEXT);
        if let (Some(s), Some(Phase::Town)) = (self.site_sel, self.phase()) {
            let d = &SITES[s];
            let txt = format!("{}: {} - {} DAYS - WAGES ABOUT {} GOLD EACH WAY", d.name, d.blurb, d.days, wages * d.days as i32);
            let w = font::text_width(&txt, 1) + 20;
            fb.fill_rect(12, H as i32 - 70, w, 22, rgba(14, 12, 10, 215));
            fb.text(22, H as i32 - 64, &txt.to_uppercase(), 1, rgb(255, 235, 190));
        }
        self.draw_toast_at(fb, SIDE_X / 2, TOP + 12);

        // Company panel.
        fb.fill_rect(PANEL_X, TOP, W as i32 - PANEL_X, H as i32 - TOP, PANEL);
        fb.fill_rect(PANEL_X, TOP, 2, H as i32 - TOP, PANEL_EDGE);
        let px = PANEL_X + 14;
        fb.text(px, TOP + 10, &c.company.to_uppercase(), 2, GOLD);
        fb.text(px, TOP + 34, &format!("DAY {}", c.day), 2, TEXT);
        fb.text(px + 110, TOP + 34, &format!("GOLD {}", c.gold), 2, gold_col);
        fb.text(px, TOP + 56, &format!("WAGES {}/DAY", wages), 2, DIM);
        fb.text(px + 190, TOP + 56, &format!("FALLEN {}", c.fallen.len()), 2, if c.fallen.is_empty() { DIM } else { BAD });
        fb.text(px, TOP + 82, &format!("COMPANY ({}/{})  PARTY {}/{}", c.roster.len(), cp::ROSTER_CAP, c.deployed_ids().len(), cp::MAX_DEPLOY), 1, GOLD);
        self.draw_merc_rows(fb, c, px, TOP + 96, 372, 76);

        self.draw_buttons(fb);

        // Modals.
        if let Some(n) = &self.notice {
            self.draw_modal(fb, &n.title, n.color, None, &n.lines);
            self.draw_buttons(fb);
        } else {
            match c.phase {
                Phase::Encounter => {
                    let k = c.active.as_ref();
                    let mut lines = Vec::new();
                    if let Some(k) = k {
                        lines.push((format!("AT {}", k.site_def().name.to_uppercase()), TEXT));
                        lines.push((format!("{} BLOCKS THE WAY", k.kind.label().to_uppercase()), BAD));
                        let party = cp::enemy_party(k.kind, k.difficulty, k.seed);
                        let count = |a: Archetype| party.iter().filter(|&&x| x == a).count();
                        lines.push((
                            format!(
                                "{} RAIDERS, {} ARCHERS, {} BRUTES ({} FOES)",
                                count(Archetype::Raider),
                                count(Archetype::BanditArcher),
                                count(Archetype::Brute),
                                party.len()
                            ),
                            TEXT,
                        ));
                        lines.push((format!("PAY ON VICTORY: {} GOLD (AT TOWN)", k.pay), GOLD));
                        lines.push(("RETREATING MEANS A WALK HOME WITH NO PAY".into(), DIM));
                        lines.push(("ENTER: FIGHT    ESC: RETREAT".into(), DIM));
                    }
                    self.draw_modal(fb, "ENEMIES AHEAD", BAD, k.map(|k| k.difficulty), &lines);
                    self.draw_buttons(fb);
                }
                Phase::Empty => {
                    let at = c.at_site.map_or("", |s| SITES[s].name);
                    let lines = vec![
                        (format!("{} IS QUIET: NO CONTRACT HERE", at.to_uppercase()), TEXT),
                        ("THE DAYS AND WAGES ARE SPENT. HEAD BACK.".into(), DIM),
                    ];
                    self.draw_modal(fb, "NOTHING HERE", DIM, None, &lines);
                    self.draw_buttons(fb);
                }
                _ => {}
            }
        }
    }

    pub(super) fn render_town(&self, fb: &mut Framebuffer) {
        let Some(c) = &self.campaign else { return };
        fb.clear(rgb(26, 24, 22));
        fb.fill_rect(0, 0, W as i32, TOP, PANEL);
        fb.fill_rect(0, TOP - 2, W as i32, 2, PANEL_EDGE);
        fb.text(14, 10, &TOWN_NAME.to_uppercase(), 4, GOLD);
        fb.text(14, 46, &c.company.to_uppercase(), 1, DIM);
        let gold_col = if c.gold < 0 { BAD } else { GOLD };
        fb.text(520, 12, &format!("DAY {}", c.day), 3, TEXT);
        fb.text(700, 12, &format!("GOLD {}", c.gold), 3, gold_col);
        fb.text(980, 12, &format!("WAGES {}/DAY", c.daily_wages()), 2, DIM);
        fb.text(980, 38, &format!("ROSTER {}/{}", c.roster.len(), cp::ROSTER_CAP), 2, DIM);

        // Contract board.
        fb.text(20, 78, "CONTRACT BOARD", 2, GOLD);
        let mut y = 106;
        let mut cards: Vec<(&cp::Contract, bool)> = Vec::new();
        if let Some(k) = &c.active {
            cards.push((k, true));
        }
        cards.extend(c.board.iter().map(|k| (k, false)));
        for (k, accepted) in cards {
            let r = rect(20, y, 670, 112);
            panel(fb, r, rgb(40, 36, 30), if accepted { GOLD } else { rgb(100, 88, 68) });
            let d = k.site_def();
            fb.text(r.x + 14, y + 10, &d.name.to_uppercase(), 3, if accepted { GOLD } else { TEXT });
            if accepted {
                fb.text(r.x + 14 + font::text_width(d.name, 3) + 16, y + 14, "ACCEPTED", 2, GOOD);
            }
            skulls(fb, (r.x + r.w - 110) as f32, (y + 24) as f32, k.difficulty, 20.0);
            fb.text(r.x + 14, y + 40, &format!("ENEMY: {}  ({} SKULL{})", k.kind.label().to_uppercase(), k.difficulty, if k.difficulty == 1 { "" } else { "S" }), 2, BAD.lighten(0.2));
            fb.text(r.x + 14, y + 62, &format!("PAY {} GOLD   TRAVEL {} DAY{}", k.pay, d.days, if d.days == 1 { "" } else { "S" }), 2, GOLD);
            let late = c.day + d.days > k.deadline;
            fb.text(r.x + 14, y + 84, &format!("ARRIVE BY DAY {}   {}", k.deadline, d.blurb.to_uppercase()), 1, if late { BAD } else { DIM });
            y += 118;
        }
        if c.board.is_empty() && c.active.is_none() {
            fb.text(30, 130, "NO CONTRACTS POSTED TODAY", 2, DIM);
        }

        // Recruits.
        fb.text(710, 78, "RECRUITS FOR HIRE", 2, GOLD);
        for (i, r) in c.recruits.iter().enumerate() {
            let rc = rect(710, 106 + i as i32 * 82, 670, 76);
            panel(fb, rc, rgb(32, 36, 44), rgb(80, 90, 110));
            fb.text(rc.x + 12, rc.y + 8, &r.name.to_uppercase(), 2, TEXT);
            fb.text(rc.x + 190, rc.y + 8, &format!("{}  LV {}", r.archetype.label().to_uppercase(), r.level), 2, PLAYER.lighten(0.3));
            let afford = c.gold >= r.cost;
            fb.text(rc.x + 12, rc.y + 30, &format!("COST {} GOLD   WAGE {}/DAY", r.cost, r.wage), 2, if afford { GOLD } else { BAD });
            fb.text(rc.x + 12, rc.y + 54, &stat_line(r.archetype, r.level), 1, DIM);
            if c.roster.len() >= cp::ROSTER_CAP {
                fb.text(rc.x + rc.w - 250, rc.y + 8, "ROSTER FULL", 1, BAD);
            } else if !afford {
                fb.text(rc.x + rc.w - 250, rc.y + 8, &format!("NEED {} MORE GOLD", r.cost - c.gold), 1, BAD);
            }
        }
        if c.recruits.is_empty() {
            fb.text(720, 130, "NO ONE IS LOOKING FOR WORK TODAY", 2, DIM);
        }

        // Roster.
        fb.text(710, 364, &format!("YOUR COMPANY   PARTY {}/{} FOR BATTLE", c.deployed_ids().len(), cp::MAX_DEPLOY), 2, GOLD);
        self.draw_town_roster(fb, c);

        fb.text(20, 600, "REST: EVERYONE HEALS 12 HP PER DAY, BUT WAGES ARE STILL DUE.", 1, DIM);
        fb.text(20, 616, "WOUNDED FIGHTERS START THE NEXT BATTLE WITH THEIR REMAINING HP.", 1, DIM);
        fb.text(20, 632, "ONLY THE PARTY (UP TO 5) FIGHTS AND EARNS XP; CLICK PARTY/RESERVE TO SWAP.", 1, DIM);
        fb.text(20, 648, &format!("A CONTRACT PAYS ONLY WHEN YOU ARE BACK IN {}.", TOWN_NAME.to_uppercase()), 1, DIM);
        let tip = if c.active.is_none() {
            "NEXT: ACCEPT A CONTRACT ON THE BOARD".to_string()
        } else {
            "NEXT: DEPART, OR REST AND HIRE FIRST".to_string()
        };
        fb.text(20, 676, &tip, 2, TEXT);
        self.draw_toast_at(fb, 350, 702);
        self.draw_buttons(fb);
    }

    fn draw_town_roster(&self, fb: &mut Framebuffer, c: &Campaign) {
        for (i, m) in c.roster.iter().enumerate() {
            let y = 392 + i as i32 * 52;
            let r = rect(710, y, 670, 48);
            fb.fill_rect(r.x, r.y, r.w, r.h, rgb(24, 27, 35));
            fb.fill_rect(r.x, r.y, 4, r.h, if m.deploy { PLAYER } else { rgb(70, 74, 86) });
            let up = self.leveled.contains(&m.id);
            fb.text(r.x + 12, y + 5, &m.name.to_uppercase(), 2, if up { GOLD } else { TEXT });
            fb.text(r.x + 150, y + 5, &format!("LV {}", m.level), 2, GOLD);
            fb.text(r.x + 215, y + 8, &m.archetype.label().to_uppercase(), 1, DIM);
            let frac = m.hp as f32 / m.max_hp() as f32;
            bar(fb, r.x + 12, y + 26, 160, 10, frac, hp_color(frac));
            fb.text(r.x + 180, y + 27, &format!("HP {}/{}", m.hp, m.max_hp()), 1, if m.wounded() { BAD.lighten(0.2) } else { TEXT });
            let next = m.next_level_xp();
            let xt = next.map_or(format!("XP {} MAX", m.xp), |n| format!("XP {}/{}", m.xp, n));
            fb.text(r.x + 290, y + 27, &xt, 1, DIM);
            fb.text(r.x + 12, y + 38, &format!("WAGE {}/DAY  KILLS {}", m.wage(), m.kills), 1, DIM);
            let b = m.bonus();
            if m.level > 1 {
                fb.text(r.x + 370, y + 8, &format!("+{} HP +{} SKILL +{} DEF", b.hp, b.skill, b.defense), 1, GOOD);
            }
            fb.text(r.x + 290, y + 38, &format!("DEF {}  SKILL {}", m.archetype.stats_at_level(m.level).defense, main_skill(m.archetype, m.level)), 1, DIM);
        }
    }

    pub(super) fn render_campaign_result(&self, fb: &mut Framebuffer, rep: &BattleReport) {
        fb.fill_rect(0, 0, W as i32, H as i32, rgba(0, 0, 0, 185));
        let cx = W as i32 / 2;
        let r = rect(cx - 480, 24, 960, 770);
        panel(fb, r, PANEL, PANEL_EDGE);
        let won = rep.outcome == Outcome::Victory;
        fb.text_centered(cx, 44, if won { "VICTORY" } else { "DEFEAT" }, 8, if won { GOOD } else { BAD });
        let sub = match (won, rep.timed_out) {
            (true, _) => "THE ENEMY IS ROUTED",
            (false, true) => "THE BATTLE DRAGGED ON AND THE COMPANY WITHDREW",
            (false, false) => "THE COMPANY WAS BEATEN",
        };
        fb.text_centered(cx, 118, sub, 2, TEXT);
        fb.text_centered(cx, 146, &format!("ROUNDS {}    ENEMIES DOWN {}/{}", rep.rounds, rep.enemies_down, rep.enemies_total), 2, DIM);

        let x0 = r.x + 24;
        for (x, t) in [(x0, "MERCENARY"), (x0 + 270, "HEALTH"), (x0 + 520, "KILLS"), (x0 + 600, "XP"), (x0 + 690, "LEVEL")] {
            fb.text(x, 184, t, 1, GOLD);
        }
        fb.fill_rect(x0, 196, r.w - 48, 2, PANEL_EDGE);
        let mut y = 206;
        for m in &rep.mercs {
            fb.fill_rect(x0 - 4, y, r.w - 40, 56, rgb(24, 27, 35));
            fb.text(x0 + 4, y + 8, &m.name.to_uppercase(), 2, if m.dead { BAD } else { TEXT });
            fb.text(x0 + 4, y + 30, &format!("{} LV {}", m.archetype.label().to_uppercase(), m.level_before), 1, DIM);
            if m.dead {
                fb.text(x0 + 270, y + 8, "KILLED IN ACTION", 2, BAD);
                fb.text(x0 + 270, y + 30, &format!("WAS {}/{}", m.hp_before, m.max_hp), 1, DIM);
            } else {
                let frac = m.hp_after as f32 / m.max_hp as f32;
                bar(fb, x0 + 270, y + 10, 170, 14, frac, hp_color(frac));
                fb.text(x0 + 448, y + 12, &format!("{}/{}", m.hp_after, m.max_hp), 1, TEXT);
                fb.text(x0 + 270, y + 32, &format!("HP LEFT {}  (WAS {})", m.hp_after, m.hp_before), 1, DIM);
            }
            fb.text(x0 + 530, y + 12, &m.kills.to_string(), 3, if m.kills > 0 { GOLD } else { DIM });
            if m.dead {
                fb.text(x0 + 600, y + 16, "-", 2, DIM);
            } else {
                fb.text(x0 + 600, y + 16, &format!("+{}", m.xp_gained), 2, rgb(150, 170, 255));
            }
            if m.leveled_up() {
                fb.text(x0 + 690, y + 8, &format!("LEVEL UP! {} > {}", m.level_before, m.level_after), 2, GOLD);
                fb.text(x0 + 690, y + 32, &format!("+{} HP +{} SKILL +{} DEF", m.gain.hp, m.gain.skill, m.gain.defense), 1, GOOD);
            } else if !m.dead {
                fb.text(x0 + 690, y + 16, &format!("LV {}", m.level_after), 2, DIM);
            }
            y += 62;
        }
        y = y.max(206 + 62 * 4) + 8;
        let fallen = rep.mercs.iter().filter(|m| m.dead).count();
        if won {
            fb.text_centered(cx, y, &format!("CONTRACT COMPLETE - {} GOLD IS PAID WHEN YOU REACH {}", rep.pay_pending, TOWN_NAME.to_uppercase()), 2, GOOD);
        } else {
            fb.text_centered(cx, y, "THE CONTRACT IS LOST - NO PAY", 2, BAD);
        }
        let line = if fallen == 0 { "NO MERCENARY FELL".to_string() } else { format!("{fallen} MERCENAR{} FELL AND WILL NOT RETURN", if fallen == 1 { "Y" } else { "IES" }) };
        fb.text_centered(cx, y + 28, &line, 2, if fallen == 0 { DIM } else { BAD });
        if let Some(c) = &self.campaign {
            if let Some(reason) = c.over {
                fb.text_centered(cx, y + 56, &reason.describe().to_uppercase(), 2, BAD);
            }
        }
        self.draw_buttons(fb);
        fb.text_centered(cx, 756, "ENTER: CONTINUE", 2, DIM);
    }

    pub(super) fn render_game_over(&self, fb: &mut Framebuffer) {
        fb.clear(rgb(18, 14, 14));
        let Some(c) = &self.campaign else { return };
        let s = c.summary();
        let cx = W as i32 / 2;
        let r = rect(cx - 420, 40, 840, 740);
        panel(fb, r, PANEL, PANEL_EDGE);
        let (title, col) = match s.reason {
            Some(GameOverReason::Bankrupt) => ("BANKRUPT", BAD),
            _ => ("COMPANY LOST", BAD),
        };
        fb.text_centered(cx, 64, "CAMPAIGN OVER", 4, DIM);
        fb.text_centered(cx, 110, title, 8, col);
        fb.text_centered(cx, 182, &s.company.to_uppercase(), 3, GOLD);
        if let Some(reason) = s.reason {
            fb.text_centered(cx, 220, &reason.describe().to_uppercase(), 1, TEXT);
        }
        let stats = [
            ("DAYS SURVIVED", s.days.to_string()),
            ("CONTRACTS COMPLETED", s.contracts_completed.to_string()),
            ("CONTRACTS FAILED", s.contracts_failed.to_string()),
            ("BATTLES FOUGHT", s.battles.to_string()),
            ("GOLD LEFT", s.gold.to_string()),
        ];
        let mut y = 258;
        for (k, v) in stats {
            fb.text(r.x + 160, y, k, 2, DIM);
            fb.text(r.x + 560, y, &v, 2, TEXT);
            y += 28;
        }
        y += 14;
        fb.text(r.x + 160, y, &format!("THE FALLEN ({})", s.fallen.len()), 3, BAD);
        if s.fallen.is_empty() {
            fb.text(r.x + 180, FALLEN_Y0, "NONE", 2, DIM);
        }
        let pages = fallen_pages(s.fallen.len());
        let page = self.fallen_page.min(pages - 1);
        for (idx, y) in fallen_rows(page, s.fallen.len()) {
            let f = &s.fallen[idx];
            fb.text(r.x + 180, y, &format!("{}  {} LV {}", f.name.to_uppercase(), f.archetype.label().to_uppercase(), f.level), 2, TEXT);
            fb.text(r.x + 560, y, &format!("DAY {}  {} KILLS", f.day, f.kills), 2, DIM);
        }
        if pages > 1 {
            fb.text_centered(cx, FALLEN_PAGER_Y + 10, &format!("PAGE {}/{}   (MOUSE WHEEL)", page + 1, pages), 2, DIM);
        }
        self.draw_buttons(fb);
        fb.text_centered(cx, 764, "ENTER: RETURN TO MENU", 2, DIM);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn game(seed: u64, autoplay: bool, autoplay_campaign: bool) -> Game {
        Game::new(Options { autoplay, autoplay_campaign, seed: Some(seed) })
    }

    fn click(g: &mut Game, a: Action) {
        let b = g
            .buttons()
            .into_iter()
            .find(|b| b.action == a)
            .unwrap_or_else(|| panic!("no button {a:?} on screen {:?}", g.screen));
        assert!(b.enabled, "{a:?} is disabled");
        let (x, y) = (b.rect.x as f32 + b.rect.w as f32 / 2.0, b.rect.y as f32 + b.rect.h as f32 / 2.0);
        g.input(Input::MouseMove(x, y));
        g.input(Input::MouseDown(x, y));
    }

    fn run_until(g: &mut Game, fb: &mut Framebuffer, what: &str, max_frames: usize, mut done: impl FnMut(&Game) -> bool) {
        for i in 0..max_frames {
            if done(g) {
                return;
            }
            g.update(1.0 / 60.0);
            if i % 30 == 0 {
                g.render(fb);
            }
        }
        panic!("timed out waiting for {what} (screen {:?}, phase {:?})", g.screen, g.phase());
    }

    fn dump(fb: &Framebuffer, name: &str) {
        if let Ok(dir) = std::env::var("HO_DUMP_DIR") {
            std::fs::write(format!("{dir}/{name}.png"), crate::gfx::encode_png(fb)).unwrap();
        }
    }

    /// menu -> campaign -> accept -> travel -> fight -> (AI-played battle) -> result -> continue
    /// -> back in town -> pay -> hire/heal -> second contract, using only mouse clicks.
    #[test]
    fn full_mouse_journey_through_the_campaign_loop() {
        let mut fb = Framebuffer::new(W, H);
        let mut won_one = false;
        for seed in 1..40u64 {
            // `autoplay` lets the AI play the battle itself; the menu hook never fires because we
            // click New Campaign before the first update.
            let mut g = game(seed, true, false);
            g.render(&mut fb);
            click(&mut g, Action::NewCampaign);
            assert_eq!(g.screen, Screen::Overworld);
            assert_eq!(g.phase(), Some(Phase::Town));
            g.render(&mut fb);
            dump(&fb, "overworld");

            click(&mut g, Action::OpenTown);
            assert_eq!(g.screen, Screen::Town);
            g.render(&mut fb);
            dump(&fb, "town");
            // Pick the easiest contract.
            let c = g.campaign.as_ref().unwrap();
            let pick = (0..c.board.len()).min_by_key(|&i| c.board[i].difficulty).unwrap();
            let k = c.board[pick].clone();
            click(&mut g, Action::Accept(pick));
            assert_eq!(g.campaign.as_ref().unwrap().active.as_ref(), Some(&k));
            assert_eq!(g.site_sel, Some(k.site), "the contract's site is pre-selected on the map");
            g.render(&mut fb);
            click(&mut g, Action::Depart);
            assert_eq!(g.screen, Screen::Overworld);
            let gold0 = g.campaign.as_ref().unwrap().gold;
            let wages = g.campaign.as_ref().unwrap().daily_wages();
            assert!(matches!(g.phase(), Some(Phase::Travel(_))));

            // Marker moves while days pass and wages are deducted.
            let start = g.party_pos();
            run_until(&mut g, &mut fb, "first travel day", 600, |g| g.campaign.as_ref().unwrap().day >= 1);
            assert_eq!(g.campaign.as_ref().unwrap().gold, gold0 - wages);
            if SITES[k.site].days > 1 {
                assert_ne!(g.party_pos(), start, "the marker animates along the road");
                dump(&fb, "travel");
            }
            run_until(&mut g, &mut fb, "arrival", 3000, |g| g.phase() == Some(Phase::Encounter));
            assert_eq!(g.campaign.as_ref().unwrap().day, SITES[k.site].days);
            g.render(&mut fb);
            dump(&fb, "encounter");

            click(&mut g, Action::Fight);
            assert_eq!(g.screen, Screen::Battle);
            let deployed = g.campaign.as_ref().unwrap().deployed_ids().len();
            let b = g.battle().unwrap();
            assert_eq!(b.units().iter().filter(|u| u.team == Team::Player).count(), deployed);
            assert!(b.units().iter().filter(|u| u.team == Team::Player).all(|u| u.roster_id.is_some()));
            run_until(&mut g, &mut fb, "result screen", 120_000, |g| g.screen == Screen::Result);
            let rep = g.report.clone().expect("campaign report");
            g.render(&mut fb);
            dump(&fb, "campaign-result");
            assert_eq!(rep.mercs.len(), deployed);

            click(&mut g, Action::Continue);
            if g.screen == Screen::GameOver {
                g.render(&mut fb);
                dump(&fb, "gameover");
                click(&mut g, Action::ReturnToMenu);
                assert_eq!(g.screen, Screen::Menu);
                continue;
            }
            assert_eq!(g.screen, Screen::Overworld);
            assert!(matches!(g.phase(), Some(Phase::Travel(t)) if t.dest == cp::Dest::Town));
            let gold_before_return = g.campaign.as_ref().unwrap().gold;
            run_until(&mut g, &mut fb, "return to town", 3000, |g| g.phase() == Some(Phase::Town) || g.screen == Screen::GameOver);
            if g.screen == Screen::GameOver {
                continue;
            }
            let c = g.campaign.as_ref().unwrap();
            if rep.outcome == Outcome::Victory {
                won_one = true;
                assert_eq!(c.contracts_completed, 1);
                assert!(g.notice.is_some(), "payout is announced");
                g.render(&mut fb);
                dump(&fb, "payout");
                let wages_home = SITES[k.site].days as i32 * c.roster.iter().map(|m| m.wage()).sum::<i32>();
                assert!(c.gold >= gold_before_return - wages_home + k.pay - 1, "contract paid on arrival");
                click(&mut g, Action::DismissNotice);
            } else {
                assert_eq!(c.contracts_completed, 0);
                assert!(g.notice.is_none(), "no pay after a defeat");
            }
            // Town again: heal and hire, then take the next contract.
            click(&mut g, Action::OpenTown);
            let before = g.campaign.as_ref().unwrap().clone();
            click(&mut g, Action::Rest);
            assert_eq!(g.campaign.as_ref().unwrap().day, before.day + 1);
            if g.screen == Screen::GameOver {
                continue;
            }
            for (i, r) in before.recruits.iter().enumerate() {
                if before.gold - before.daily_wages() >= r.cost && before.roster.len() < cp::ROSTER_CAP {
                    click(&mut g, Action::Hire(i));
                    assert_eq!(g.campaign.as_ref().unwrap().roster.len(), before.roster.len() + 1);
                    break;
                }
            }
            g.render(&mut fb);
            click(&mut g, Action::Accept(0));
            assert!(g.campaign.as_ref().unwrap().active.is_some(), "second contract accepted");
            click(&mut g, Action::Depart);
            assert!(matches!(g.phase(), Some(Phase::Travel(_))));
            if won_one {
                return;
            }
        }
        panic!("no seed produced a won first contract");
    }

    #[test]
    fn selecting_a_site_on_the_map_and_travelling_to_an_empty_site() {
        let mut g = game(5, false, false);
        let mut fb = Framebuffer::new(W, H);
        click(&mut g, Action::NewCampaign);
        click(&mut g, Action::SelectSite(1));
        assert_eq!(g.site_sel, Some(1));
        g.render(&mut fb);
        click(&mut g, Action::Travel);
        assert!(matches!(g.phase(), Some(Phase::Travel(t)) if t.site == 1));
        run_until(&mut g, &mut fb, "arrival", 3000, |g| g.phase() == Some(Phase::Empty));
        g.render(&mut fb);
        dump(&fb, "empty-site");
        click(&mut g, Action::Retreat);
        run_until(&mut g, &mut fb, "home", 3000, |g| g.phase() == Some(Phase::Town));
        assert_eq!(g.screen, Screen::Overworld);
    }

    #[test]
    fn retreat_from_an_encounter_leads_home_without_pay() {
        let mut g = game(6, false, false);
        let mut fb = Framebuffer::new(W, H);
        click(&mut g, Action::NewCampaign);
        click(&mut g, Action::OpenTown);
        click(&mut g, Action::Accept(0));
        click(&mut g, Action::Depart);
        run_until(&mut g, &mut fb, "arrival", 3000, |g| g.phase() == Some(Phase::Encounter));
        g.input(Input::Key(Key::Escape)); // retreat
        assert!(matches!(g.phase(), Some(Phase::Travel(t)) if t.dest == cp::Dest::Town));
        run_until(&mut g, &mut fb, "home", 3000, |g| g.phase() == Some(Phase::Town));
        let c = g.campaign.as_ref().unwrap();
        assert!(c.gold < cp::START_GOLD && c.active.is_none() && c.contracts_completed == 0);
        assert!(g.notice.is_none());
    }

    #[test]
    fn town_buttons_reflect_what_is_possible() {
        let mut g = game(7, false, false);
        click(&mut g, Action::NewCampaign);
        click(&mut g, Action::OpenTown);
        // No contract yet: cannot depart; cannot afford a hire after the gold is spent.
        let en = |g: &Game, a: Action| g.buttons().into_iter().find(|b| b.action == a).map(|b| b.enabled);
        assert_eq!(en(&g, Action::Depart), Some(false));
        assert_eq!(en(&g, Action::Accept(0)), Some(true));
        g.campaign.as_mut().unwrap().gold = 1;
        assert_eq!(en(&g, Action::Hire(0)), Some(false));
        click(&mut g, Action::Accept(0));
        assert_eq!(en(&g, Action::Accept(0)), Some(false), "one contract at a time");
        assert_eq!(en(&g, Action::Depart), Some(true));
        assert_eq!(en(&g, Action::CancelContract), Some(true));
        // Deployment toggling by mouse.
        let id = g.campaign.as_ref().unwrap().roster[0].id;
        click(&mut g, Action::ToggleDeploy(id));
        assert!(!g.campaign.as_ref().unwrap().merc(id).unwrap().deploy);
        // Esc leaves town.
        g.input(Input::Key(Key::Escape));
        assert_eq!(g.screen, Screen::Overworld);
    }

    #[test]
    fn bankruptcy_shows_the_campaign_over_screen_and_returns_to_menu() {
        let mut g = game(8, false, false);
        let mut fb = Framebuffer::new(W, H);
        click(&mut g, Action::NewCampaign);
        click(&mut g, Action::OpenTown);
        {
            let c = g.campaign.as_mut().unwrap();
            c.gold = -1;
            c.roster.iter_mut().for_each(|m| m.hp = 1);
        }
        click(&mut g, Action::Rest);
        assert_eq!(g.screen, Screen::GameOver);
        g.render(&mut fb);
        dump(&fb, "gameover");
        click(&mut g, Action::ReturnToMenu);
        assert_eq!(g.screen, Screen::Menu);
        assert!(g.campaign.is_none());
        g.render(&mut fb);
    }

    #[test]
    fn pause_works_on_the_overworld_and_abandons_the_campaign() {
        let mut g = game(9, false, false);
        let mut fb = Framebuffer::new(W, H);
        click(&mut g, Action::NewCampaign);
        g.input(Input::Key(Key::Escape));
        assert!(g.paused);
        g.render(&mut fb);
        g.input(Input::Key(Key::Escape));
        assert!(!g.paused);
        click(&mut g, Action::Pause);
        assert!(g.paused);
        click(&mut g, Action::AbandonToMenu);
        assert_eq!(g.screen, Screen::Menu);
        assert!(g.campaign.is_none() && !g.paused);
    }

    #[test]
    fn quick_battle_still_works_and_uses_no_campaign() {
        let mut g = game(10, true, false);
        click(&mut g, Action::QuickBattle);
        assert_eq!(g.screen, Screen::Battle);
        assert!(g.campaign.is_none());
        let mut fb = Framebuffer::new(W, H);
        run_until(&mut g, &mut fb, "result", 120_000, |g| g.screen == Screen::Result);
        assert!(g.report.is_none() && g.result.is_some());
        g.input(Input::Key(Key::Enter));
        assert_eq!(g.screen, Screen::Menu);
    }

    #[test]
    fn autopilot_plays_the_campaign_loop_by_itself() {
        let mut g = game(3, false, true);
        let mut fb = Framebuffer::new(W, H);
        let mut seen = std::collections::BTreeSet::new();
        let mut frames = 0;
        loop {
            g.update(1.0 / 60.0);
            if frames % 20 == 0 {
                g.render(&mut fb);
            }
            seen.insert(g.view().0);
            let c = g.campaign.as_ref();
            if c.is_some_and(|c| c.battles >= 1 && c.phase == Phase::Town && g.screen == Screen::Town) || (g.screen == Screen::Menu && frames > 10) {
                break;
            }
            frames += 1;
            assert!(frames < 400_000, "autopilot stalled in {:?} / {:?}", g.screen, g.phase());
        }
        for v in ["overworld", "town", "travel", "encounter", "battle", "result"] {
            assert!(seen.contains(v), "autopilot never showed {v}: {seen:?}");
        }
    }

    #[test]
    fn every_campaign_screen_renders_with_every_roster_size() {
        let mut fb = Framebuffer::new(W, H);
        let mut g = game(11, false, false);
        click(&mut g, Action::NewCampaign);
        {
            let c = g.campaign.as_mut().unwrap();
            c.gold = 5000;
            for i in 0..2 {
                c.hire(i).unwrap();
            }
            c.roster[0].hp = 3;
            c.roster[1].level = 4;
            c.roster[1].xp = cp::xp_to_reach(4) + 5;
        }
        g.leveled = vec![g.campaign.as_ref().unwrap().roster[1].id];
        g.render(&mut fb);
        for (i, _) in SITES.iter().enumerate() {
            click(&mut g, Action::SelectSite(i));
            g.mouse = site_screen(i);
            g.render(&mut fb);
        }
        g.notice = Some(Notice { title: "T".into(), color: GOOD, lines: vec![("LINE".into(), TEXT)] });
        g.render(&mut fb);
        g.notice = None;
        click(&mut g, Action::OpenTown);
        g.render(&mut fb);
        dump(&fb, "town-full");
        assert_eq!(g.campaign.as_ref().unwrap().roster.len(), 6);
    }

    /// Regression: the fallen list on the campaign-over screen used to be cut at 13 entries and its
    /// last rows were drawn underneath the Return to Menu button.
    #[test]
    fn long_fallen_list_is_paged_and_stays_above_the_button() {
        let mut g = game(12, false, false);
        let mut fb = Framebuffer::new(W, H);
        click(&mut g, Action::NewCampaign);
        {
            let c = g.campaign.as_mut().unwrap();
            for k in 0..23u32 {
                c.fallen.push(cp::Fallen { name: format!("Fallen{k}"), archetype: Archetype::Swordsman, level: 1, day: k, kills: 0 });
            }
            c.gold = -1;
            c.roster.iter_mut().for_each(|m| m.hp = 1);
        }
        click(&mut g, Action::OpenTown);
        click(&mut g, Action::Rest);
        assert_eq!(g.screen, Screen::GameOver);
        let total = g.campaign.as_ref().unwrap().fallen.len();
        let ret = g.buttons().into_iter().find(|b| b.action == Action::ReturnToMenu).unwrap().rect;
        let pages = total.div_ceil(FALLEN_PER_PAGE);
        assert!(pages >= 3);
        let mut shown = std::collections::BTreeSet::new();
        for page in 0..pages {
            assert_eq!(g.fallen_page, page);
            for (idx, y) in fallen_rows(page, total) {
                assert!(y + font::text_height(2) < ret.y, "row {idx} at y={y} collides with the button at y={}", ret.y);
                shown.insert(idx);
            }
            // The pager buttons do not overlap the list or the Return button either.
            for b in g.buttons().into_iter().filter(|b| matches!(b.action, Action::FallenPrev | Action::FallenNext)) {
                assert!(b.rect.y + b.rect.h <= ret.y);
                assert!(b.rect.y > fallen_rows(page, total).last().unwrap().1 + font::text_height(2));
            }
            g.render(&mut fb);
            dump(&fb, &format!("gameover-page{page}"));
            if page + 1 < pages {
                click(&mut g, Action::FallenNext);
            }
        }
        assert_eq!(shown.len(), total, "every fallen mercenary can be viewed");
        // Next is disabled on the last page; Prev goes back; the wheel pages too.
        assert!(!g.buttons().into_iter().find(|b| b.action == Action::FallenNext).unwrap().enabled);
        click(&mut g, Action::FallenPrev);
        assert_eq!(g.fallen_page, pages - 2);
        g.input(Input::Wheel(-1.0));
        assert_eq!(g.fallen_page, pages - 1);
        g.input(Input::Wheel(1.0));
        assert_eq!(g.fallen_page, pages - 2);
        click(&mut g, Action::ReturnToMenu);
        assert_eq!(g.screen, Screen::Menu);
        assert_eq!(g.fallen_page, 0);
    }
}
