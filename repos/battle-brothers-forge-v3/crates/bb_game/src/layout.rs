//! Pixel <-> hex and screen areas. Float maths lives here, never in bb_rules.

use bb_rules::{Hex, hex};

pub const WIN_W: f32 = 1280.0;
pub const WIN_H: f32 = 720.0;
pub const HEX_SIZE: f32 = 34.0;
pub const MAP_CENTER: (f32, f32) = (440.0, 335.0);
pub const PANEL_X: f32 = 900.0;
const SQRT3: f32 = 1.732_050_8;

/// Axis-aligned screen rectangle in window pixels. Rendering and hit testing
/// share the constants below so a drawn button is exactly its clickable area.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Rect {
    pub x: f32,
    pub y: f32,
    pub w: f32,
    pub h: f32,
}

impl Rect {
    pub const fn new(x: f32, y: f32, w: f32, h: f32) -> Rect {
        Rect { x, y, w, h }
    }
    pub fn right(&self) -> f32 {
        self.x + self.w
    }
    pub fn bottom(&self) -> f32 {
        self.y + self.h
    }
    pub fn contains(&self, px: f32, py: f32) -> bool {
        px >= self.x && px < self.right() && py >= self.y && py < self.bottom()
    }
    #[cfg(test)]
    pub fn overlaps(&self, o: &Rect) -> bool {
        self.x < o.right() && o.x < self.right() && self.y < o.bottom() && o.y < self.bottom()
    }
}

/// End Round launch button: side-panel space below the log (which ends near
/// y 640) and right of the controls hint and error bar.
pub const END_ROUND_BUTTON: Rect = Rect::new(PANEL_X, 654.0, 220.0, 44.0);
/// Retreat launch button: same row as End Round, in the free space to its right.
pub const RETREAT_BUTTON: Rect = Rect::new(1130.0, 654.0, 140.0, 44.0);
/// Skill strip: the unused band above the panel and unit rows (the panel background starts
/// at y 44, the first row at y 52), right of the top-bar text, which ends near x 674 for
/// the longest seed. Four equal 144x40 buttons side by side with 6 px gaps, each with its
/// scale-1.25 title over two scale-1 cost/effect rows. The strip starts left of the side
/// panel's x. APPROXIMATION: slice presentation, not original-UI parity.
pub const SKILL_STRIP_Y: f32 = 3.0;
pub const SKILL_BUTTON_W: f32 = 144.0;
pub const SKILL_BUTTON_H: f32 = 40.0;
pub const ADRENALINE_BUTTON: Rect = Rect::new(684.0, SKILL_STRIP_Y, SKILL_BUTTON_W, SKILL_BUTTON_H);
/// Rotation skill button: right of Adrenaline in the same strip, with a visible gap.
pub const ROTATION_BUTTON: Rect = Rect::new(834.0, SKILL_STRIP_Y, SKILL_BUTTON_W, SKILL_BUTTON_H);
/// Footwork skill button: right of Rotation in the same strip, with a visible gap.
pub const FOOTWORK_BUTTON: Rect = Rect::new(984.0, SKILL_STRIP_Y, SKILL_BUTTON_W, SKILL_BUTTON_H);
/// Indomitable skill button: right of Footwork, the last of the strip, ending 2 px inside the window.
pub const INDOMITABLE_BUTTON: Rect = Rect::new(1134.0, SKILL_STRIP_Y, SKILL_BUTTON_W, SKILL_BUTTON_H);
/// Recover button: one skill-sized button below the strip, in the free band between the
/// legends (Swamp legend ends near x 616), the map (top row from y 97) and the side panel
/// (background from x 888, first unit row y 52). Shares the skill button size, title scale
/// and two-row layout. APPROXIMATION: slice presentation, not original-UI parity.
pub const RECOVER_BUTTON: Rect = Rect::new(724.0, 48.0, SKILL_BUTTON_W, SKILL_BUTTON_H);
/// Stunning Blow button: one skill-sized button below the height legend (whose last row ends
/// at y 94) and left of the map, in the window's left margin clear of every map hex. Shares the
/// skill button size, title scale and two-row layout. APPROXIMATION: slice presentation, not
/// original-UI parity.
pub const STUNNING_BLOW_BUTTON: Rect = Rect::new(16.0, 100.0, SKILL_BUTTON_W, SKILL_BUTTON_H);
/// Scale of the skill titles; cost and effect rows stay at scale 1.
pub const SKILL_TITLE_SCALE: f32 = 1.25;
pub const ADRENALINE_LABEL: &str = "ADRENALINE (1)";
pub const ROTATION_LABEL: &str = "ROTATION (2)";
pub const FOOTWORK_LABEL: &str = "FOOTWORK (3)";
pub const INDOMITABLE_LABEL: &str = "INDOMITABLE (4)";
pub const RECOVER_LABEL: &str = "RECOVER (5)";
pub const STUNNING_BLOW_LABEL: &str = "STUN BLOW (6)";
pub const END_ROUND_LABEL: &str = "END ROUND (X)";
pub const RETREAT_LABEL: &str = "RETREAT (V)";
/// Confirmation modal, shared by End Round and Retreat. Centred on the window, above everything else.
pub const DIALOG: Rect = Rect::new(250.0, 170.0, 780.0, 340.0);
/// Confirm and Cancel sit inside the dialog, far from the launch button.
pub const CONFIRM_BUTTON: Rect = Rect::new(290.0, 430.0, 260.0, 52.0);
pub const CANCEL_BUTTON: Rect = Rect::new(730.0, 430.0, 260.0, 52.0);

/// Buttons of the non-modal UI.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum UiHit {
    EndRound,
    Retreat,
    Adrenaline,
    Rotation,
    Footwork,
    Indomitable,
    Recover,
    StunningBlow,
}

/// Buttons of the confirmation modal (End Round or Retreat).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ModalHit {
    Confirm,
    Cancel,
}

/// The UI button under the pointer when no modal is open.
pub fn ui_hit(x: f32, y: f32) -> Option<UiHit> {
    if END_ROUND_BUTTON.contains(x, y) {
        Some(UiHit::EndRound)
    } else if RETREAT_BUTTON.contains(x, y) {
        Some(UiHit::Retreat)
    } else if ADRENALINE_BUTTON.contains(x, y) {
        Some(UiHit::Adrenaline)
    } else if ROTATION_BUTTON.contains(x, y) {
        Some(UiHit::Rotation)
    } else if FOOTWORK_BUTTON.contains(x, y) {
        Some(UiHit::Footwork)
    } else if INDOMITABLE_BUTTON.contains(x, y) {
        Some(UiHit::Indomitable)
    } else if RECOVER_BUTTON.contains(x, y) {
        Some(UiHit::Recover)
    } else if STUNNING_BLOW_BUTTON.contains(x, y) {
        Some(UiHit::StunningBlow)
    } else {
        None
    }
}

/// The modal button under the pointer; the rest of the window is not a target.
pub fn modal_hit(x: f32, y: f32) -> Option<ModalHit> {
    if CONFIRM_BUTTON.contains(x, y) {
        Some(ModalHit::Confirm)
    } else if CANCEL_BUTTON.contains(x, y) {
        Some(ModalHit::Cancel)
    } else {
        None
    }
}

pub fn hex_to_pixel(h: Hex) -> (f32, f32) {
    let x = HEX_SIZE * SQRT3 * (h.q as f32 + h.r as f32 / 2.0);
    let y = HEX_SIZE * 1.5 * h.r as f32;
    (MAP_CENTER.0 + x, MAP_CENTER.1 + y)
}

pub fn pixel_to_hex(x: f32, y: f32) -> Hex {
    let px = (x - MAP_CENTER.0) / HEX_SIZE;
    let py = (y - MAP_CENTER.1) / HEX_SIZE;
    let qf = (SQRT3 / 3.0) * px - py / 3.0;
    let rf = 2.0 / 3.0 * py;
    let sf = -qf - rf;
    let (mut q, mut r, s) = (qf.round(), rf.round(), sf.round());
    let (dq, dr, ds) = ((q - qf).abs(), (r - rf).abs(), (s - sf).abs());
    if dq > dr && dq > ds {
        q = -r - s;
    } else if dr > ds {
        r = -q - s;
    }
    hex(q as i32, r as i32)
}

/// The six corner points of a pointy-top hex.
pub fn corners(c: (f32, f32), size: f32) -> [(f32, f32); 6] {
    std::array::from_fn(|i| {
        let a = std::f32::consts::PI / 180.0 * (60.0 * i as f32 - 30.0);
        (c.0 + size * a.cos(), c.1 + size * a.sin())
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn roundtrip() {
        for q in -4..=4 {
            for r in -4..=4 {
                let h = hex(q, r);
                let p = hex_to_pixel(h);
                assert_eq!(pixel_to_hex(p.0 + 3.0, p.1 - 2.0), h);
            }
        }
    }

    #[test]
    fn buttons_are_disjoint_inside_the_window_and_modal_buttons_inside_the_dialog() {
        for r in [END_ROUND_BUTTON, RETREAT_BUTTON, DIALOG, CONFIRM_BUTTON, CANCEL_BUTTON] {
            assert!(r.x >= 0.0 && r.y >= 0.0 && r.right() <= WIN_W && r.bottom() <= WIN_H);
        }
        for r in [CONFIRM_BUTTON, CANCEL_BUTTON] {
            assert!(r.x >= DIALOG.x && r.y >= DIALOG.y && r.right() <= DIALOG.right() && r.bottom() <= DIALOG.bottom());
        }
        assert!(!CONFIRM_BUTTON.overlaps(&CANCEL_BUTTON));
        assert!(!END_ROUND_BUTTON.overlaps(&CONFIRM_BUTTON));
        assert!(!END_ROUND_BUTTON.overlaps(&CANCEL_BUTTON));
        assert!(!END_ROUND_BUTTON.overlaps(&DIALOG), "launch button must not sit under the dialog");
    }

    #[test]
    fn retreat_button_is_disjoint_from_every_other_hit_area_and_inside_the_window() {
        let r = RETREAT_BUTTON;
        for other in [END_ROUND_BUTTON, CONFIRM_BUTTON, CANCEL_BUTTON, DIALOG] {
            assert!(!r.overlaps(&other), "{other:?}");
        }
        assert!(r.x >= PANEL_X && r.right() <= WIN_W && r.bottom() <= WIN_H);
        assert!(END_ROUND_BUTTON.right() < r.x, "buttons keep a visible gap");
        // Same row as End Round, below the log (six lines from y 546, 14 px tall) and the panel text.
        assert_eq!((r.y, r.h), (END_ROUND_BUTTON.y, END_ROUND_BUTTON.h));
        assert!(r.y > 546.0 + 5.0 * 16.0 + 14.0);
        // The shortcut hint and error bar are left of the side panel, so they cannot meet it.
        assert!(r.x > 16.0 + crate::render::Painter::text_width(2.0, crate::render::SHORTCUT_HINT));
    }

    #[test]
    fn skill_buttons_sit_in_the_top_right_strip_clear_of_every_other_hit_area_and_each_other() {
        let strip = [
            (ADRENALINE_BUTTON, UiHit::Adrenaline, 684.0),
            (ROTATION_BUTTON, UiHit::Rotation, 834.0),
            (FOOTWORK_BUTTON, UiHit::Footwork, 984.0),
            (INDOMITABLE_BUTTON, UiHit::Indomitable, 1134.0),
        ];
        // Clear of the longest top-bar line ("ROUND 99  ACTIVE P1 (PLAYER)  SEED <u64::MAX>").
        let top = format!("ROUND 99  ACTIVE P1 (PLAYER)  SEED {}", u64::MAX);
        let top_end = 16.0 + crate::render::Painter::text_width(2.0, &top);
        for (b, hit, x) in strip {
            assert_eq!((b.x, b.y, b.w, b.h), (x, SKILL_STRIP_Y, 144.0, 40.0), "{hit:?}");
            assert!(b.x > top_end && b.right() <= WIN_W && b.y >= 0.0 && b.bottom() <= 44.0, "above the panel background");
            for other in [END_ROUND_BUTTON, RETREAT_BUTTON, CONFIRM_BUTTON, CANCEL_BUTTON, DIALOG] {
                assert!(!b.overlaps(&other), "{other:?}");
            }
            assert_eq!(ui_hit(b.x + 1.0, b.y + 1.0), Some(hit));
            assert_eq!(ui_hit(b.right() - 0.5, b.bottom() - 0.5), Some(hit));
            assert_eq!(ui_hit(b.x + b.w / 2.0, b.y + b.h / 2.0), Some(hit));
            assert_eq!(ui_hit(b.x + 1.0, b.bottom()), None);
            assert_eq!(ui_hit(b.x - 0.5, b.y + 1.0), None, "the gap is not a target");
            assert_eq!(ui_hit(b.right(), b.y + 1.0), None, "the gap is not a target");
            assert_eq!(modal_hit(b.x + 1.0, b.y + 1.0), None);
        }
        for pair in strip.windows(2) {
            let (l, r) = (pair[0].0, pair[1].0);
            assert!(!l.overlaps(&r) && l.right() < r.x, "neighbouring buttons keep a visible gap");
        }
        for (i, (l, _, _)) in strip.iter().enumerate() {
            for (r, _, _) in &strip[i + 1..] {
                assert!(!l.overlaps(r));
            }
        }
    }

    #[test]
    fn button_labels_fit_their_buttons_with_a_margin() {
        for (r, label) in [(END_ROUND_BUTTON, END_ROUND_LABEL), (RETREAT_BUTTON, RETREAT_LABEL)] {
            let w = crate::render::Painter::text_width(2.0, label);
            assert!(w + 8.0 <= r.w, "{label}: {w} wide in a {} wide button", r.w);
        }
        let rows = crate::render::skill_info_rows();
        for ((r, label), info) in [
            (ADRENALINE_BUTTON, ADRENALINE_LABEL),
            (ROTATION_BUTTON, ROTATION_LABEL),
            (FOOTWORK_BUTTON, FOOTWORK_LABEL),
            (INDOMITABLE_BUTTON, INDOMITABLE_LABEL),
            (STUNNING_BLOW_BUTTON, STUNNING_BLOW_LABEL),
        ]
        .into_iter()
        .zip(&rows)
        {
            let w = crate::render::Painter::text_width(SKILL_TITLE_SCALE, label);
            assert!(10.0 + w + 8.0 <= r.w, "{label}: {w} wide in a {} wide button", r.w);
            for line in info {
                let w = crate::render::Painter::text_width(1.0, line);
                assert!(10.0 + w + 8.0 <= r.w, "{line}: {w} wide in a {} wide button", r.w);
            }
        }
    }

    #[test]
    fn hit_testing_uses_the_drawn_rectangles() {
        let r = END_ROUND_BUTTON;
        assert_eq!(ui_hit(r.x + 1.0, r.y + 1.0), Some(UiHit::EndRound));
        assert_eq!(ui_hit(r.right() - 0.5, r.bottom() - 0.5), Some(UiHit::EndRound));
        assert_eq!(ui_hit(r.x - 0.5, r.y + 1.0), None);
        assert_eq!(ui_hit(r.right(), r.y + 1.0), None);
        assert_eq!(ui_hit(CONFIRM_BUTTON.x + 1.0, CONFIRM_BUTTON.y + 1.0), None);

        let t = RETREAT_BUTTON;
        assert_eq!(ui_hit(t.x + 1.0, t.y + 1.0), Some(UiHit::Retreat));
        assert_eq!(ui_hit(t.right() - 0.5, t.bottom() - 0.5), Some(UiHit::Retreat));
        assert_eq!(ui_hit(t.x - 0.5, t.y + 1.0), None);
        assert_eq!(ui_hit(t.right(), t.y + 1.0), None);
        assert_eq!(ui_hit(t.x + 1.0, t.bottom()), None);
        assert_eq!(modal_hit(t.x + 1.0, t.y + 1.0), None, "Retreat launch button is not a modal target");

        let (c, x) = (CONFIRM_BUTTON, CANCEL_BUTTON);
        assert_eq!(modal_hit(c.x + c.w / 2.0, c.y + c.h / 2.0), Some(ModalHit::Confirm));
        assert_eq!(modal_hit(x.x + x.w / 2.0, x.y + x.h / 2.0), Some(ModalHit::Cancel));
        assert_eq!(modal_hit(r.x + 1.0, r.y + 1.0), None, "launch button is not a modal target");
        assert_eq!(modal_hit(DIALOG.x + 2.0, DIALOG.y + 2.0), None, "dialog body is not a target");
        assert_eq!(modal_hit(10.0, 10.0), None);
    }

    #[test]
    fn recover_button_has_its_own_hit_area_clear_of_the_strip_and_the_other_controls() {
        let b = RECOVER_BUTTON;
        assert_eq!((b.x, b.y, b.w, b.h), (724.0, 48.0, 144.0, 40.0));
        assert_eq!((b.w, b.h), (SKILL_BUTTON_W, SKILL_BUTTON_H));
        assert!(b.x >= 0.0 && b.right() <= WIN_W && b.bottom() <= WIN_H);
        for other in [ADRENALINE_BUTTON, ROTATION_BUTTON, FOOTWORK_BUTTON, INDOMITABLE_BUTTON, END_ROUND_BUTTON, RETREAT_BUTTON, CONFIRM_BUTTON, CANCEL_BUTTON, DIALOG] {
            assert!(!b.overlaps(&other), "{other:?}");
        }
        assert!(b.y > INDOMITABLE_BUTTON.bottom() && b.right() < PANEL_X - 12.0);
        assert_eq!(ui_hit(b.x + 1.0, b.y + 1.0), Some(UiHit::Recover));
        assert_eq!(ui_hit(b.right() - 0.5, b.bottom() - 0.5), Some(UiHit::Recover));
        assert_eq!(ui_hit(b.x + b.w / 2.0, b.y + b.h / 2.0), Some(UiHit::Recover));
        assert_eq!(ui_hit(b.x - 0.5, b.y + 1.0), None);
        assert_eq!(ui_hit(b.right(), b.y + 1.0), None);
        assert_eq!(ui_hit(b.x + 1.0, b.bottom()), None);
        assert_eq!(ui_hit(b.x + 1.0, b.y - 0.5), None);
        assert_eq!(modal_hit(b.x + 1.0, b.y + 1.0), None);
        // The four skill buttons keep their rectangles and hits.
        for (r, hit, x) in [
            (ADRENALINE_BUTTON, UiHit::Adrenaline, 684.0),
            (ROTATION_BUTTON, UiHit::Rotation, 834.0),
            (FOOTWORK_BUTTON, UiHit::Footwork, 984.0),
            (INDOMITABLE_BUTTON, UiHit::Indomitable, 1134.0),
        ] {
            assert_eq!((r.x, r.y, r.w, r.h), (x, SKILL_STRIP_Y, 144.0, 40.0));
            assert_eq!(ui_hit(r.x + 1.0, r.y + 1.0), Some(hit));
        }
        let w = crate::render::Painter::text_width(SKILL_TITLE_SCALE, RECOVER_LABEL);
        assert!(10.0 + w + 8.0 <= b.w);
        for line in ["COST 9 AP", "HALVE FAT, END TURN"] {
            assert!(10.0 + crate::render::Painter::text_width(1.0, line) + 8.0 <= b.w, "{line}");
        }
    }

    #[test]
    fn stunning_blow_button_has_its_own_hit_area_below_the_height_legend_and_left_of_the_map() {
        let b = STUNNING_BLOW_BUTTON;
        assert_eq!((b.x, b.y, b.w, b.h), (16.0, 100.0, 144.0, 40.0));
        assert_eq!((b.w, b.h), (SKILL_BUTTON_W, SKILL_BUTTON_H));
        assert!(b.x >= 0.0 && b.right() <= WIN_W && b.bottom() <= WIN_H);
        for other in [
            ADRENALINE_BUTTON,
            ROTATION_BUTTON,
            FOOTWORK_BUTTON,
            INDOMITABLE_BUTTON,
            RECOVER_BUTTON,
            END_ROUND_BUTTON,
            RETREAT_BUTTON,
            CONFIRM_BUTTON,
            CANCEL_BUTTON,
            DIALOG,
        ] {
            assert!(!b.overlaps(&other), "{other:?}");
        }
        // Below the three scale-2 legend rows (x 16, y 44, step 18, 14 px tall: the last ends at y 94).
        assert!(b.y > 44.0 + 2.0 * 18.0 + 14.0);
        // Clear of every map hex: each hex, with its outline, lies wholly right of the button.
        for h in bb_rules::hex::map_hexes() {
            let c = hex_to_pixel(h);
            let hex_box = Rect::new(c.0 - HEX_SIZE, c.1 - HEX_SIZE, 2.0 * HEX_SIZE, 2.0 * HEX_SIZE);
            assert!(!b.overlaps(&hex_box), "{h:?} reaches the button");
        }
        assert_eq!(ui_hit(b.x + 1.0, b.y + 1.0), Some(UiHit::StunningBlow));
        assert_eq!(ui_hit(b.right() - 0.5, b.bottom() - 0.5), Some(UiHit::StunningBlow));
        assert_eq!(ui_hit(b.x + b.w / 2.0, b.y + b.h / 2.0), Some(UiHit::StunningBlow));
        assert_eq!(ui_hit(b.x - 0.5, b.y + 1.0), None);
        assert_eq!(ui_hit(b.right(), b.y + 1.0), None);
        assert_eq!(ui_hit(b.x + 1.0, b.bottom()), None);
        assert_eq!(ui_hit(b.x + 1.0, b.y - 0.5), None);
        assert_eq!(modal_hit(b.x + 1.0, b.y + 1.0), None);
        // The other buttons keep their rectangles and hits.
        for (r, hit) in [
            (RECOVER_BUTTON, UiHit::Recover),
            (ADRENALINE_BUTTON, UiHit::Adrenaline),
            (ROTATION_BUTTON, UiHit::Rotation),
            (FOOTWORK_BUTTON, UiHit::Footwork),
            (INDOMITABLE_BUTTON, UiHit::Indomitable),
        ] {
            assert_eq!(ui_hit(r.x + 1.0, r.y + 1.0), Some(hit));
        }
        assert_eq!(STUNNING_BLOW_LABEL, "STUN BLOW (6)");
    }
}
