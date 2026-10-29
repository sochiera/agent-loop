//! Placeholder drawing generated in code: coloured triangles via wgpu.

use std::sync::Arc;

use bb_rules::rules::{
    ADRENALINE_AP, ADRENALINE_FATIGUE, ADRENALINE_INITIATIVE_BONUS, AttackPreview, FOOTWORK_AP, FOOTWORK_FATIGUE, FootworkPreview, INDOMITABLE_AP,
    INDOMITABLE_FATIGUE, RECOVER_AP, ROTATION_AP, ROTATION_FATIGUE, RotationPreview, STUNNING_BLOW_AP, STUNNING_BLOW_FATIGUE, SWAMP_MOVE_AP,
    adrenaline_availability, indomitable_availability,
};
use bb_rules::{BattleState, EndReason, Hex, MeleeReach, Morale, Outcome, RuleError, Side, TerrainKind, hex::map_hexes, rules::MovePreview};
use bytemuck::{Pod, Zeroable};
use winit::window::Window;

use crate::font;
use crate::layout::{self, HEX_SIZE, PANEL_X, Rect, WIN_H, WIN_W};

const MAX_VERTS: usize = 600_000;

#[repr(C)]
#[derive(Clone, Copy, Pod, Zeroable)]
pub struct Vertex {
    pos: [f32; 2],
    pub(crate) color: [f32; 4],
}

type Rgba = [f32; 4];
const GREY: Rgba = [0.42, 0.42, 0.44, 1.0];
const BROWN: Rgba = [0.22, 0.14, 0.09, 1.0];
const GREEN: Rgba = [0.55, 0.85, 0.50, 1.0];
const RED: Rgba = [0.85, 0.20, 0.18, 1.0];
const BLUE: Rgba = [0.22, 0.38, 0.85, 1.0];
const WHITE: Rgba = [0.95, 0.95, 0.95, 1.0];
const DIM: Rgba = [0.5, 0.5, 0.5, 1.0];
const YELLOW: Rgba = [0.98, 0.85, 0.2, 1.0];
const BG: Rgba = [0.08, 0.08, 0.10, 1.0];
const CONTROL_MARKER: Rgba = [0.98, 0.48, 0.08, 1.0];
const CONTROL_MARKER_GLYPH: Rgba = [0.12, 0.06, 0.02, 1.0];
const PREVIEW_BG: Rgba = [0.04, 0.09, 0.14, 0.92];
const PREVIEW_COST: Rgba = [0.55, 0.85, 0.95, 1.0];
pub(crate) const PREVIEW_WARN: Rgba = [1.0, 0.62, 0.25, 1.0];
const PREVIEW_NOTE: Rgba = [0.78, 0.78, 0.82, 1.0];
const BUTTON_BORDER: Rgba = [0.0, 0.0, 0.0, 1.0];
const BUTTON_ENABLED: Rgba = [0.55, 0.30, 0.10, 1.0];
const BUTTON_DISABLED: Rgba = [0.17, 0.17, 0.20, 1.0];
const BUTTON_DISABLED_TEXT: Rgba = [0.45, 0.45, 0.48, 1.0];
const DIALOG_BORDER: Rgba = [0.95, 0.70, 0.15, 1.0];
const DIALOG_BG: Rgba = [0.10, 0.09, 0.13, 1.0];
const CONFIRM_FILL: Rgba = [0.60, 0.16, 0.14, 1.0];
const CANCEL_FILL: Rgba = [0.20, 0.30, 0.45, 1.0];
const FLEEING: Rgba = [0.85, 0.30, 0.75, 1.0];
const FLED: Rgba = [0.45, 0.62, 0.78, 1.0];
/// Skill buttons use their own fills so they read apart from the order buttons.
pub(crate) const SKILL_ENABLED: Rgba = [0.10, 0.38, 0.40, 1.0];
pub(crate) const SKILL_DISABLED: Rgba = [0.13, 0.17, 0.18, 1.0];
/// A skill button while its targeting mode is open.
pub(crate) const SKILL_ACTIVE: Rgba = [0.18, 0.62, 0.30, 1.0];
/// Rotation targeting: legal ally hexes, the hovered one, and the SWAP label.
pub(crate) const SWAP_FILL: Rgba = [0.12, 0.55, 0.62, 1.0];
pub(crate) const SWAP_HOVER: Rgba = [0.30, 0.85, 0.92, 1.0];
pub(crate) const SWAP_TEXT: Rgba = [0.60, 0.97, 1.0, 1.0];
/// Footwork targeting: legal destination hexes, the hovered one, and the STEP label.
/// Violet, distinct from the Rotation teal, movement green and attack red.
pub(crate) const STEP_FILL: Rgba = [0.48, 0.34, 0.78, 1.0];
pub(crate) const STEP_HOVER: Rgba = [0.76, 0.64, 0.98, 1.0];
pub(crate) const STEP_TEXT: Rgba = [0.92, 0.86, 1.0, 1.0];
/// Stunning Blow targeting: legal enemy hexes, the hovered one, and the STUN label. Rose, distinct
/// from the Rotation teal, Footwork violet, movement green and the plain attack red.
pub(crate) const STUN_TARGET_FILL: Rgba = [0.72, 0.20, 0.42, 1.0];
pub(crate) const STUN_TARGET_HOVER: Rgba = [0.98, 0.52, 0.72, 1.0];
pub(crate) const STUN_TARGET_TEXT: Rgba = [1.0, 0.86, 0.92, 1.0];
pub(crate) const PENDING_MARKER: Rgba = [0.98, 0.72, 0.18, 1.0];
const PENDING_MARKER_TEXT: Rgba = [0.12, 0.07, 0.02, 1.0];
/// Fast Adaptation stack indicator text on a unit row's HP/armour line.
const ADAPT_MARKER_TEXT: Rgba = [0.60, 0.92, 0.80, 1.0];
/// Active Killing Frenzy tag, shown on the unit row's HP/armour line. Presentation is an
/// APPROXIMATION; visibility comes solely from `Unit::is_killing_frenzy_active`.
const FRENZY_MARKER_LABEL: &str = "FRENZY";
const FRENZY_MARKER_TEXT: Rgba = [1.0, 0.56, 0.24, 1.0];
const FRENZY_MARKER_X: f32 = 1134.0;
const FRENZY_MARKER_DY: f32 = 21.0;
const HEIGHT_BADGE: Rgba = [0.78, 0.86, 0.98, 1.0];
const HEIGHT_LEGEND_NOTE: Rgba = [0.72, 0.76, 0.62, 1.0];
const HEIGHT_BADGE_GLYPH: Rgba = [0.05, 0.08, 0.16, 1.0];
/// Passable Swamp hex base fill: dark olive, distinct from Ground grey, the obstacle brown and
/// every action highlight, all of which keep precedence over it. APPROXIMATION (colour choice).
const SWAMP_FILL: Rgba = [0.28, 0.35, 0.18, 1.0];
const SWAMP_BADGE: Rgba = [0.93, 0.80, 0.45, 1.0];
const SWAMP_BADGE_GLYPH: Rgba = [0.20, 0.12, 0.02, 1.0];
const SWAMP_LEGEND_NOTE: Rgba = [0.72, 0.76, 0.62, 1.0];
/// Height badge box relative to the hex centre (top-left corner, size). It sits in
/// the upper-left corner pocket of the hex: inside the hex outline and more than
/// the 22 px active-unit ring away from the centre, left of the AP-cost label,
/// the name text and the ZoC marker and above the health and armour bars.
const HEIGHT_BADGE_OFFSET: (f32, f32) = (-28.25, -16.25);
const HEIGHT_BADGE_SIZE: (f32, f32) = (7.0, 9.0);
/// Height legend rows (x, first y, row step), in the free window corner left of
/// the map's top row; every row ends above the map (y 97).
const HEIGHT_LEGEND: (f32, f32, f32) = (16.0, 44.0, 18.0);
/// Swamp `S` badge box relative to the hex centre (top-left corner, size). It mirrors the height
/// badge through the centre: the lower-right corner pocket, inside the hex outline, more than the
/// 22 px active-unit ring away from the centre, right of the name, AP-cost label and bars and
/// below the ZoC marker. APPROXIMATION (placement).
const SWAMP_BADGE_OFFSET: (f32, f32) = (21.25, 7.25);
const SWAMP_BADGE_SIZE: (f32, f32) = (7.0, 9.0);
/// Swamp legend rows (x, first y, row step): right of the height legend, above the map's top row
/// (y 97), left of the skill strip (x 684). Drawn only while a passable Swamp hex exists.
const SWAMP_LEGEND: (f32, f32, f32) = (340.0, 44.0, 18.0);
/// Stunning Blow `STUN` label (scale 1, 23x7 px): the same placement as `SWAP`, on the marked
/// enemy's own token.
const STUN_LABEL_OFFSET: (f32, f32) = SWAP_LABEL_OFFSET;
/// Rotation `SWAP` label (scale 1, 23x7 px) relative to a marked ally's hex centre: on the
/// lower half of its own token, below the name (which ends at +7) and above the bars (from +20),
/// so it stays clear of every neighbouring token, name, bar and the hex-corner overlays.
const SWAP_LABEL_OFFSET: (f32, f32) = (-11.5, 8.0);
/// Movement preview box (x, y, w, h): below the map (which ends near y 573) and
/// above the error bar (y 660), left of the side panel (x 888).
/// Footwork `STEP` label (scale 1, 23x7 px) relative to a marked destination hex's centre:
/// the hex is empty, so the label sits in its middle, clear of the height badge and control marker.
const STEP_LABEL_OFFSET: (f32, f32) = (-11.5, -3.5);
const PREVIEW_BOX: (f32, f32, f32, f32) = (16.0, 584.0, 860.0, 72.0);
/// Outcome banner: the same strip, which is free once the battle is over (no preview).
const BANNER_BOX: (f32, f32, f32, f32) = PREVIEW_BOX;

/// Collects triangles in pixel space; converted to NDC for the GPU.
pub struct Painter {
    pub verts: Vec<Vertex>,
}

impl Painter {
    fn v(&mut self, p: (f32, f32), c: Rgba) {
        self.verts.push(Vertex { pos: [p.0 / WIN_W * 2.0 - 1.0, 1.0 - p.1 / WIN_H * 2.0], color: c });
    }
    fn tri(&mut self, a: (f32, f32), b: (f32, f32), c: (f32, f32), col: Rgba) {
        self.v(a, col);
        self.v(b, col);
        self.v(c, col);
    }
    fn rect(&mut self, x: f32, y: f32, w: f32, h: f32, c: Rgba) {
        self.tri((x, y), (x + w, y), (x, y + h), c);
        self.tri((x + w, y), (x + w, y + h), (x, y + h), c);
    }
    fn fan(&mut self, pts: &[(f32, f32)], c: Rgba) {
        for i in 1..pts.len() - 1 {
            self.tri(pts[0], pts[i], pts[i + 1], c);
        }
    }
    fn hex_shape(&mut self, centre: (f32, f32), fill: Rgba) {
        let outer = layout::corners(centre, HEX_SIZE);
        self.fan(&outer, [0.0, 0.0, 0.0, 1.0]);
        self.fan(&layout::corners(centre, HEX_SIZE - 2.0), fill);
    }
    fn control_marker(&mut self, centre: (f32, f32)) {
        self.circle((centre.0, centre.1 - 28.0), 5.0, CONTROL_MARKER);
        self.text(centre.0 - 3.0, centre.1 - 32.0, 1.2, CONTROL_MARKER_GLYPH, "!");
    }
    /// Small numeric height badge (heights are single digits, 0-3) in `centre`'s hex.
    fn height_badge(&mut self, centre: (f32, f32), height: u8) {
        let (x, y) = (centre.0 + HEIGHT_BADGE_OFFSET.0, centre.1 + HEIGHT_BADGE_OFFSET.1);
        self.rect(x, y, HEIGHT_BADGE_SIZE.0, HEIGHT_BADGE_SIZE.1, HEIGHT_BADGE);
        self.text(x + 1.0, y + 1.0, 1.0, HEIGHT_BADGE_GLYPH, &height.to_string());
    }
    /// Small `S` badge marking a passable Swamp hex, persistent whatever fills the hex.
    fn swamp_badge(&mut self, centre: (f32, f32)) {
        let (x, y) = (centre.0 + SWAMP_BADGE_OFFSET.0, centre.1 + SWAMP_BADGE_OFFSET.1);
        self.rect(x, y, SWAMP_BADGE_SIZE.0, SWAMP_BADGE_SIZE.1, SWAMP_BADGE);
        self.text(x + 1.0, y + 1.0, 1.0, SWAMP_BADGE_GLYPH, "S");
    }
    /// Skill button in the top-right strip: the shared rectangle, the title at
    /// `SKILL_TITLE_SCALE` and the cost and effect rows at scale 1 below it.
    fn skill_button(&mut self, r: Rect, label: &str, info: &[String; 2], look: SkillLook) {
        let (fill, text) = match look {
            SkillLook::Enabled => (SKILL_ENABLED, WHITE),
            SkillLook::Active => (SKILL_ACTIVE, WHITE),
            SkillLook::Disabled => (SKILL_DISABLED, BUTTON_DISABLED_TEXT),
        };
        self.rect(r.x, r.y, r.w, r.h, BUTTON_BORDER);
        self.rect(r.x + 2.0, r.y + 2.0, r.w - 4.0, r.h - 4.0, fill);
        self.text(r.x + SKILL_TEXT_X, r.y + SKILL_TITLE_DY, layout::SKILL_TITLE_SCALE, text, label);
        for (i, line) in info.iter().enumerate() {
            self.text(r.x + SKILL_TEXT_X, r.y + SKILL_INFO_DY + i as f32 * 8.0, 1.0, text, line);
        }
    }
    fn adrenaline_button(&mut self, enabled: bool) {
        let look = if enabled { SkillLook::Enabled } else { SkillLook::Disabled };
        self.skill_button(layout::ADRENALINE_BUTTON, layout::ADRENALINE_LABEL, &adrenaline_info_lines(), look);
    }
    fn rotation_button(&mut self, look: SkillLook) {
        self.skill_button(layout::ROTATION_BUTTON, layout::ROTATION_LABEL, &rotation_info_lines(), look);
    }
    fn footwork_button(&mut self, look: SkillLook) {
        self.skill_button(layout::FOOTWORK_BUTTON, layout::FOOTWORK_LABEL, &footwork_info_lines(), look);
    }
    fn indomitable_button(&mut self, look: SkillLook) {
        self.skill_button(layout::INDOMITABLE_BUTTON, layout::INDOMITABLE_LABEL, &indomitable_info_lines(), look);
    }
    fn stunning_blow_button(&mut self, look: SkillLook) {
        self.skill_button(layout::STUNNING_BLOW_BUTTON, layout::STUNNING_BLOW_LABEL, &stunning_blow_info_lines(), look);
    }
    fn recover_button(&mut self, enabled: bool) {
        let look = if enabled { SkillLook::Enabled } else { SkillLook::Disabled };
        self.skill_button(layout::RECOVER_BUTTON, layout::RECOVER_LABEL, &recover_info_lines(), look);
    }
    /// Active-Indomitable marker, scale 1 at `INDOMITABLE_MARKER_X` on the line `DODGE_DY` below
    /// the unit row at `y`. Read-only text.
    fn indomitable_marker(&mut self, y: f32) {
        self.text(INDOMITABLE_MARKER_X, y + DODGE_DY, 1.0, INDOMITABLE_MARKER_TEXT, INDOMITABLE_MARKER_LABEL);
    }
    /// Pending-Adrenaline marker on the AP/fatigue line of the unit row at `y`.
    fn pending_marker(&mut self, y: f32) {
        let (x, w) = pending_marker_box();
        self.rect(x, y, w, 7.0 + 2.0 * PENDING_MARKER_PAD, PENDING_MARKER);
        self.text(x + PENDING_MARKER_PAD, y + PENDING_MARKER_PAD, 1.0, PENDING_MARKER_TEXT, PENDING_MARKER_TEXT_LABEL);
    }
    /// Fast Adaptation stack indicator, right aligned at scale 1 on the HP/armour line of the
    /// unit row at `y`, vertically centred in that scale-2 line. Read-only text.
    fn adaptation_marker(&mut self, y: f32, stacks: u32) {
        let (x, _) = adaptation_marker_box(stacks);
        self.text(x, y + ADAPT_MARKER_DY, 1.0, ADAPT_MARKER_TEXT, &adaptation_marker_label(stacks));
    }
    /// Active Killing Frenzy tag, scale 1 in the free slot on the HP/armour line.
    fn frenzy_marker(&mut self, y: f32) {
        self.text(
            FRENZY_MARKER_X,
            y + FRENZY_MARKER_DY,
            1.0,
            FRENZY_MARKER_TEXT,
            FRENZY_MARKER_LABEL,
        );
    }
    /// Dodge defense breakdown, scale 1 at `PANEL_X` on the line `DODGE_DY` below the unit
    /// row at `y`. Read-only text; the numbers come from the `Unit` defense methods.
    fn dodge_breakdown(&mut self, y: f32, u: &bb_rules::Unit) {
        self.text(PANEL_X, y + DODGE_DY, 1.0, DODGE_TEXT, &dodge_breakdown_label(u));
    }
    /// Nine Lives indicator, scale 1, right aligned inside the window on the line `NINE_LIVES_DY`
    /// below the unit row at `y`. Read-only text driven by `Unit.nine_lives_used`.
    fn nine_lives_marker(&mut self, y: f32, used: bool) {
        let (x, _) = nine_lives_marker_box(used);
        self.text(x, y + NINE_LIVES_DY, 1.0, nine_lives_marker_color(used), nine_lives_marker_label(used));
    }
    /// Two-hex reach tag, scale 1, right aligned inside the window on the name/morale line of
    /// the unit row at `y`. Read-only text driven by `Unit.melee_reach`.
    fn reach_marker(&mut self, y: f32) {
        let (x, _) = reach_marker_box();
        self.text(x, y + REACH_MARKER_DY, 1.0, REACH_MARKER_TEXT, REACH_MARKER_LABEL);
    }
    /// Read-only status text driven directly by `Unit.stunned` and battlefield presence.
    fn stun_marker(&mut self, y: f32) {
        let (x, _) = stun_marker_box();
        self.text(x, y + STUN_MARKER_DY, 1.0, STUN_MARKER_TEXT, STUN_MARKER_LABEL);
    }
    fn circle(&mut self, c: (f32, f32), r: f32, col: Rgba) {
        let pts: Vec<(f32, f32)> = (0..24)
            .map(|i| {
                let a = i as f32 / 24.0 * std::f32::consts::TAU;
                (c.0 + r * a.cos(), c.1 + r * a.sin())
            })
            .collect();
        self.fan(&pts, col);
    }
    /// Text at integer pixel `scale` (scale 2 = 10x14 px glyph cell, advance 12).
    /// Pixel width of `s` as drawn by `text` (no trailing gap).
    pub fn text_width(scale: f32, s: &str) -> f32 {
        (s.chars().count() as f32 * 6.0 - 1.0).max(0.0) * scale
    }
    /// Bordered button filling exactly `r`, label centred at scale 2.
    fn button(&mut self, r: Rect, fill: Rgba, label_col: Rgba, label: &str) {
        self.rect(r.x, r.y, r.w, r.h, BUTTON_BORDER);
        self.rect(r.x + 2.0, r.y + 2.0, r.w - 4.0, r.h - 4.0, fill);
        let tx = r.x + (r.w - Painter::text_width(2.0, label)) / 2.0;
        self.text(tx, r.y + (r.h - 14.0) / 2.0, 2.0, label_col, label);
    }
    pub fn text(&mut self, x: f32, y: f32, scale: f32, col: Rgba, s: &str) {
        let mut cx = x;
        for ch in s.chars() {
            for (row, bits) in font::glyph(ch).iter().enumerate() {
                for colm in 0..5 {
                    if bits & (0x10 >> colm) != 0 {
                        self.rect(cx + colm as f32 * scale, y + row as f32 * scale, scale, scale, col);
                    }
                }
            }
            cx += 6.0 * scale;
        }
    }
}

/// Left padding of the text inside a skill button, and the offsets of its title and of the first
/// of its two cost/effect rows (8 px apart) below the button top.
const SKILL_TEXT_X: f32 = 10.0;
const SKILL_TITLE_DY: f32 = 5.0;
const SKILL_INFO_DY: f32 = 19.0;

/// How a skill button is drawn.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SkillLook {
    Disabled,
    Enabled,
    /// Rotation, Footwork or Stunning Blow: its targeting mode is open (a second activation cancels).
    /// Indomitable: the selected unit's stance is effective (activation stays unavailable).
    Active,
}

/// The action a targeting mode will submit; at most one mode is open at a time.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TargetAction {
    Rotation,
    Footwork,
    StunningBlow,
}

/// Which confirmation modal is open.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ConfirmAction {
    EndRound,
    Retreat,
}

pub struct Scene<'a> {
    pub state: &'a BattleState,
    pub selected: Option<u32>,
    pub hover: Option<Hex>,
    pub reach: &'a [(Hex, u32)],
    pub log: &'a [String],
    pub error: Option<&'a str>,
    pub seed: u64,
    /// User preference; the marker list is empty when it is not the living
    /// active player's turn or the battle has ended.
    pub control_overlay: bool,
    /// User preference: draw every map hex's current height (H). Independent of
    /// `control_overlay`, of whose turn it is and of the battle outcome.
    pub height_overlay: bool,
    /// The confirmation modal that is open, if any (pending intent held by the client).
    pub confirm: Option<ConfirmAction>,
    /// The targeting mode of the currently valid intent the client holds, if any. The scene
    /// still gates it on the selection being the controllable active player with legal
    /// targets for that action.
    pub targeting: Option<TargetAction>,
}

/// The selected unit when it is the present, human-controlled (neither Fleeing
/// nor retreating) active player unit of a running battle.
fn selected_active_player(sc: &Scene<'_>) -> Option<u32> {
    let s = sc.state;
    if s.outcome().is_some() {
        return None;
    }
    let selected = sc.selected?;
    let unit = s.unit(selected)?;
    if !unit.is_present() || unit.side != Side::Player || unit.morale == Morale::Fleeing || unit.retreating {
        return None;
    }
    s.active_unit().is_some_and(|active| active.id == selected && active.side == Side::Player).then_some(selected)
}

/// Whether the End Round button is enabled: the selection is the living
/// active player unit of a running battle.
pub fn end_round_enabled(sc: &Scene<'_>) -> bool {
    selected_active_player(sc).is_some()
}

/// Retreat is offered under exactly the End Round conditions.
pub fn retreat_enabled(sc: &Scene<'_>) -> bool {
    selected_active_player(sc).is_some()
}

/// Adrenaline is offered when the selection is the present, controllable active
/// player unit and the rules availability query accepts it (capability, not
/// pending, AP, fatigue). Pure: reads the state only.
pub fn adrenaline_enabled(sc: &Scene<'_>) -> bool {
    selected_active_player(sc)
        .and_then(|id| sc.state.unit(id))
        .is_some_and(|u| adrenaline_availability(u).is_ok())
}

/// Scale-1 text rows inside the Adrenaline button: the rules costs and the
/// next-round effect, formatted from the accepted constants. APPROXIMATION:
/// slice presentation of UC-COMBAT-066 / UC-UI-003/008, not original-UI parity.
fn adrenaline_info_lines() -> [String; 2] {
    [format!("COST {ADRENALINE_AP} AP {ADRENALINE_FATIGUE} FAT"), format!("NEXT ROUND INIT +{ADRENALINE_INITIATIVE_BONUS}")]
}

/// Rotation is offered when the selection is the present, controllable active player
/// unit and the rules report at least one legal ally target. Pure: reads the state only.
pub fn rotation_enabled(sc: &Scene<'_>) -> bool {
    selected_active_player(sc).is_some_and(|id| !sc.state.legal_rotation_targets(id).is_empty())
}

/// Footwork is offered when the selection is the present, controllable active player
/// unit and the rules report at least one legal destination. Pure: reads the state only.
pub fn footwork_enabled(sc: &Scene<'_>) -> bool {
    selected_active_player(sc).is_some_and(|id| !sc.state.legal_footwork_destinations(id).is_empty())
}

/// The open targeting mode and its actor: the client holds an intent and the scene still
/// offers that action. Pure.
pub fn targeting_mode(sc: &Scene<'_>) -> Option<(TargetAction, u32)> {
    let action = sc.targeting?;
    let actor = selected_active_player(sc)?;
    match action {
        TargetAction::Rotation => rotation_enabled(sc),
        TargetAction::Footwork => footwork_enabled(sc),
        TargetAction::StunningBlow => stunning_blow_enabled(sc),
    }
    .then_some((action, actor))
}

/// The actor of the open Rotation targeting mode, if that is the open mode. Pure.
pub fn rotation_targeting_actor(sc: &Scene<'_>) -> Option<u32> {
    targeting_mode(sc).filter(|&(action, _)| action == TargetAction::Rotation).map(|(_, actor)| actor)
}

/// The actor of the open Footwork targeting mode, if that is the open mode. Pure.
pub fn footwork_targeting_actor(sc: &Scene<'_>) -> Option<u32> {
    targeting_mode(sc).filter(|&(action, _)| action == TargetAction::Footwork).map(|(_, actor)| actor)
}

/// The actor of the open Stunning Blow targeting mode, if that is the open mode. Pure.
pub fn stunning_blow_targeting_actor(sc: &Scene<'_>) -> Option<u32> {
    targeting_mode(sc).filter(|&(action, _)| action == TargetAction::StunningBlow).map(|(_, actor)| actor)
}

/// Stunning Blow is offered when the selection is the present, controllable active player unit and
/// the rules report at least one legal target. Pure: reads the state only.
pub fn stunning_blow_enabled(sc: &Scene<'_>) -> bool {
    selected_active_player(sc).is_some_and(|id| !sc.state.legal_stunning_blow_targets(id).is_empty())
}

/// Scale-1 text rows inside the Stunning Blow button: the rules costs and the effect.
/// APPROXIMATION: slice presentation of the T60-T63 skill (costs are independent approximations).
fn stunning_blow_info_lines() -> [String; 2] {
    [format!("COST {STUNNING_BLOW_AP} AP {STUNNING_BLOW_FATIGUE} FAT"), "HIT MAY STUN TARGET".to_string()]
}

/// Scale-1 text rows inside the Rotation button: the rules cost and the effect.
/// APPROXIMATION: slice presentation of UC-COMBAT-039 / UC-UI-003/008, not original-UI parity.
fn rotation_info_lines() -> [String; 2] {
    [format!("COST {ROTATION_AP} AP {ROTATION_FATIGUE} FAT"), "SWAP ADJACENT ALLY".to_string()]
}

/// Indomitable is offered when the selection is the present, controllable active player unit and
/// the rules availability query accepts it (capability, not already active, AP, fatigue). Pure.
pub fn indomitable_enabled(sc: &Scene<'_>) -> bool {
    selected_active_player(sc)
        .and_then(|id| sc.state.unit(id))
        .is_some_and(|u| indomitable_availability(u).is_ok())
}

/// Whether the selected, controllable active player has an effective Indomitable stance (the
/// button's active appearance). Reads the rules query. Pure.
pub fn indomitable_stance_shown(sc: &Scene<'_>) -> bool {
    selected_active_player(sc).and_then(|id| sc.state.unit(id)).is_some_and(|u| u.is_indomitable_active())
}

/// Scale-1 text rows inside the Indomitable button: the rules costs and the effect.
/// APPROXIMATION: slice presentation of UC-COMBAT-016 (unconfirmed), not original-UI parity.
fn indomitable_info_lines() -> [String; 2] {
    [format!("COST {INDOMITABLE_AP} AP {INDOMITABLE_FATIGUE} FAT"), "HALF MELEE DAMAGE".to_string()]
}

/// Recover is offered when the selection is the present, controllable active player unit and
/// the rules availability query accepts it (AP; zero fatigue is eligible). Pure.
pub fn recover_enabled(sc: &Scene<'_>) -> bool {
    selected_active_player(sc).is_some_and(|id| sc.state.recover_availability(id).is_ok())
}

/// Scale-1 text rows inside the Recover button: the rules cost and the effect.
/// APPROXIMATION: slice presentation of UC-COMBAT-065 (cost and turn behavior are
/// catalog-stated, runtime-unconfirmed), not original-UI parity.
fn recover_info_lines() -> [String; 2] {
    [format!("COST {RECOVER_AP} AP"), "HALVE FAT, END TURN".to_string()]
}

/// The cost/effect rows of the skill buttons in strip order (Adrenaline, Rotation, Footwork,
/// Indomitable), then Stunning Blow. Test helper for the layout fit checks.
#[cfg(test)]
pub(crate) fn skill_info_rows() -> [[String; 2]; 5] {
    [adrenaline_info_lines(), rotation_info_lines(), footwork_info_lines(), indomitable_info_lines(), stunning_blow_info_lines()]
}

/// Scale-1 label of the active-Indomitable marker in a unit row.
pub const INDOMITABLE_MARKER_LABEL: &str = "INDOMITABLE";
const INDOMITABLE_MARKER_TEXT: Rgba = [0.95, 0.8, 0.45, 1.0];
/// Marker x on the Dodge/Nine Lives line (`DODGE_DY`): the 65 px label spans 1092..1157, right
/// of the widest Dodge breakdown (ends at x 1085) and left of `NINE LIVES READY` (starts at 1177).
const INDOMITABLE_MARKER_X: f32 = 1092.0;

/// Scale-1 text rows inside the Footwork button: the rules cost and the effect.
/// APPROXIMATION: slice presentation of UC-COMBAT-039 / UC-UI-003/008, not original-UI parity.
fn footwork_info_lines() -> [String; 2] {
    [format!("COST {FOOTWORK_AP} AP {FOOTWORK_FATIGUE} FAT"), "STEP TO ADJACENT HEX".to_string()]
}

/// Text rows of the Footwork targeting box (at most four, the box capacity). With a
/// hovered legal destination they come from that hex's `FootworkPreview`: the actor-only
/// cost, departure and destination, the absence of opportunity attacks, the cancel keys
/// and a warning when the rules predict the edge-morale penalty. Without one they are
/// instructions. Nothing implies the step leaves the battle. APPROXIMATION: slice wording,
/// not original-UI parity.
fn footwork_target_lines(state: &BattleState, actor: u32, hovered: Option<&FootworkPreview>) -> Vec<(String, Rgba)> {
    let me = state.units[actor as usize].name;
    let Some(plan) = hovered else {
        return vec![
            (format!("FOOTWORK: {me} PICKS A MARKED HEX TO STEP TO. ESC OR 3 CANCELS."), PREVIEW_COST),
            (format!("COST AP {FOOTWORK_AP} FAT {FOOTWORK_FATIGUE} ({me} ONLY). NO OPPORTUNITY ATTACKS."), PREVIEW_NOTE),
            ("HOVER A MARKED HEX TO PREVIEW THE STEP.".to_string(), PREVIEW_NOTE),
        ];
    };
    let mut lines = vec![
        (format!("STEP {me}  COST AP {} FAT {} ({me} ONLY)", plan.ap_cost, plan.fatigue_cost), PREVIEW_COST),
        (
            format!(
                "{me} FROM ({},{}) TO ({},{})  NO OPPORTUNITY ATTACKS  ESC OR 3 CANCELS",
                plan.from.q, plan.from.r, plan.to.q, plan.to.r
            ),
            PREVIEW_NOTE,
        ),
    ];
    if let Some(at) = plan.edge_penalty {
        lines.push((format!("! {me} ARRIVES AT EDGE ({},{}): MORALE BECOMES BREAKING", at.q, at.r), PREVIEW_WARN));
    }
    lines
}

/// Text rows of the Stunning Blow targeting box (at most four, the box capacity). With a hovered
/// legal enemy they come from that target's `preview_stunning_blow`: hit chance, the skill's own
/// costs and the conditional pre-armour damage range, then one stun row. The stun row only says
/// what a surviving hit attempts, never that it lands: an effective Indomitable stance resists it
/// (checked first, as `apply_stun` does) and an already stunned target gains nothing. Without a
/// hovered target they are instructions. APPROXIMATION: slice wording, not original-UI parity.
fn stunning_blow_target_lines(state: &BattleState, actor: u32, hovered: Option<&AttackPreview>) -> Vec<(String, Rgba)> {
    let me = state.units[actor as usize].name;
    let Some(plan) = hovered else {
        return vec![
            (format!("STUN BLOW: {me} PICKS A MARKED ENEMY TO STRIKE. ESC OR 6 CANCELS."), PREVIEW_COST),
            (format!("COST AP {STUNNING_BLOW_AP} FAT {STUNNING_BLOW_FATIGUE} ({me} ONLY). ADJACENT ENEMIES ONLY."), PREVIEW_NOTE),
            ("A SURVIVING HIT ATTEMPTS TO STUN. HOVER A MARKED ENEMY TO PREVIEW.".to_string(), PREVIEW_NOTE),
        ];
    };
    let target = &state.units[plan.target as usize];
    let stun_note = if target.is_indomitable_active() {
        ("INDOMITABLE: A SURVIVING HIT IS RESISTED, NO STUN.", PREVIEW_WARN)
    } else if target.stunned {
        ("ALREADY STUNNED: A SURVIVING HIT CHANGES NOTHING.", PREVIEW_WARN)
    } else {
        ("A SURVIVING HIT ATTEMPTS TO STUN. MISSES DO NOT.", PREVIEW_NOTE)
    };
    vec![
        (format!("STUN BLOW {}: HIT {}%", target.name, plan.hit_chance), YELLOW),
        (format!("COST {} AP {} FAT ({me} ONLY). ESC OR 6 CANCELS.", plan.ap_cost, plan.fatigue_cost), PREVIEW_COST),
        (format!("DAMAGE ON HIT {}-{} BEFORE ARMOUR. MISSES DEAL 0.", plan.damage_min, plan.damage_max), PREVIEW_COST),
        (stun_note.0.to_string(), stun_note.1),
    ]
}

/// Text rows of the Rotation targeting box (at most four, the box capacity). With a
/// hovered legal ally they come from that target's `RotationPreview`: the actor-only
/// cost, both destinations, the absence of opportunity attacks and one warning per
/// predicted edge-morale penalty (zero, one or two), naming its participant. Without
/// one they are instructions. APPROXIMATION: slice wording, not original-UI parity.
fn rotation_target_lines(state: &BattleState, actor: u32, hovered: Option<&RotationPreview>) -> Vec<(String, Rgba)> {
    let name = |id: u32| state.units[id as usize].name;
    let me = name(actor);
    let Some(plan) = hovered else {
        return vec![
            (format!("ROTATION: {me} PICKS A MARKED ALLY TO SWAP HEXES. ESC OR 2 CANCELS."), PREVIEW_COST),
            (format!("COST AP {ROTATION_AP} FAT {ROTATION_FATIGUE} ({me} ONLY). NO OPPORTUNITY ATTACKS."), PREVIEW_NOTE),
            ("HOVER A MARKED ALLY TO PREVIEW BOTH DESTINATIONS.".to_string(), PREVIEW_NOTE),
        ];
    };
    let mut lines = vec![
        (
            format!(
                "SWAP {me} WITH {}  COST AP {} FAT {} ({me} ONLY)",
                name(plan.target),
                plan.ap_cost,
                plan.fatigue_cost
            ),
            PREVIEW_COST,
        ),
        (
            format!(
                "{me} TO ({},{})  {} TO ({},{})  NO OPPORTUNITY ATTACKS  ESC OR 2 CANCELS",
                plan.unit_to.q,
                plan.unit_to.r,
                name(plan.target),
                plan.target_to.q,
                plan.target_to.r
            ),
            PREVIEW_NOTE,
        ),
    ];
    for (id, at) in &plan.edge_penalties {
        lines.push((format!("! {} ARRIVES AT EDGE ({},{}): MORALE BECOMES BREAKING", name(*id), at.q, at.r), PREVIEW_WARN));
    }
    lines
}

/// Scale-1 text of the pending marker in a unit row: the skill and when its
/// initiative bonus applies.
pub const PENDING_MARKER_TEXT_LABEL: &str = "ADRENALINE: NEXT RD";
const PENDING_MARKER_PAD: f32 = 4.0;

/// Pending marker box (x, width) on a unit row's AP/fatigue line: right aligned in the
/// panel, clear of the resource text, which ends near x 1140 at its widest.
fn pending_marker_box() -> (f32, f32) {
    let w = Painter::text_width(1.0, PENDING_MARKER_TEXT_LABEL) + 2.0 * PENDING_MARKER_PAD;
    (WIN_W - 8.0 - w, w)
}

/// Scale-1 Fast Adaptation indicator text for a capable unit; zero stacks included.
fn adaptation_marker_label(stacks: u32) -> String {
    format!("ADAPT {stacks}")
}

/// Scale-1 Dodge defense breakdown: the Dodge contribution and the total effective melee
/// defense (not a percentage). Zero bonuses included.
fn dodge_breakdown_label(u: &bb_rules::Unit) -> String {
    format!("DODGE +{} DEF {}", u.dodge_defense_bonus(), u.effective_defense())
}

/// Offset of the breakdown's text top below the row top: the AP/fatigue line occupies
/// rows 36..50 and the pending-Adrenaline box ends at 51; the 7 px text ends at 58, inside
/// the 60 px row pitch. The longest label (`DODGE +644245094 DEF 4294967295`) is 185 px wide.
const DODGE_DY: f32 = 51.0;
const DODGE_TEXT: Rgba = [0.55, 0.85, 0.95, 1.0];

/// Scale-1 Nine Lives indicator text for a present capable unit. APPROXIMATION: presentation
/// of the launch option's passive (UC-COMBAT-059 stays partially covered).
fn nine_lives_marker_label(used: bool) -> &'static str {
    if used { "NINE LIVES USED" } else { "NINE LIVES READY" }
}

const NINE_LIVES_READY_TEXT: Rgba = [0.95, 0.55, 0.65, 1.0];
const NINE_LIVES_USED_TEXT: Rgba = [0.42, 0.42, 0.46, 1.0];

fn nine_lives_marker_color(used: bool) -> Rgba {
    if used { NINE_LIVES_USED_TEXT } else { NINE_LIVES_READY_TEXT }
}

/// Offset of the Nine Lives text top below the row top: the same line as the Dodge breakdown
/// (`DODGE_DY`), below the AP/fatigue line and the pending-Adrenaline box.
const NINE_LIVES_DY: f32 = DODGE_DY;

/// Indicator (x, width), right aligned 8 px inside the window. The longest label,
/// `NINE LIVES READY`, is 95 px wide and starts at x 1177; the longest Dodge breakdown ends at
/// x 1085 on the same line, so they never overlap.
fn nine_lives_marker_box(used: bool) -> (f32, f32) {
    let w = Painter::text_width(1.0, nine_lives_marker_label(used));
    (WIN_W - 8.0 - w, w)
}

/// Offset of the indicator's text top below the HP/armour line top (the line is 14 px tall).
const ADAPT_MARKER_DY: f32 = 3.0;

/// Indicator (x, width): right aligned to the same panel edge as the pending-Adrenaline marker.
/// The longest label, `ADAPT 4294967295`, is 95 px wide and starts at x 1177, clear of
/// the stock HP/armour text (ends near x 1115) and on a different line than the pending marker.
fn adaptation_marker_box(stacks: u32) -> (f32, f32) {
    let w = Painter::text_width(1.0, &adaptation_marker_label(stacks));
    (WIN_W - 8.0 - w, w)
}

/// Scale-1 tag of a present unit with `MeleeReach::TwoHex`. APPROXIMATION: presentation of the
/// `--two-hex` launch option, not a weapon or polearm display.
pub const REACH_MARKER_LABEL: &str = "REACH 2";
const REACH_MARKER_TEXT: Rgba = [0.55, 0.78, 1.0, 1.0];
/// Offset of the tag's text top below the row top: the name/morale line is 14 px tall at scale 2.
const REACH_MARKER_DY: f32 = 3.0;

/// Tag (x, width), right aligned 8 px inside the window: the 41 px label starts at x 1231, right
/// of the longest stock morale label (`MORALE CONFIDENT 110%` after a two-character name ends at
/// x 1182) and on a line no other indicator uses.
fn reach_marker_box() -> (f32, f32) {
    let w = Painter::text_width(1.0, REACH_MARKER_LABEL);
    (WIN_W - 8.0 - w, w)
}

/// Scale-1 marker for a present stunned unit. APPROXIMATION: partial native status presentation.
pub const STUN_MARKER_LABEL: &str = "STUN";
pub(crate) const STUN_MARKER_TEXT: Rgba = [1.0, 0.48, 0.66, 1.0];
const STUN_MARKER_DY: f32 = 3.0;

/// Tag (x, width), sharing the name/morale row and ending 4 px before the REACH 2 slot.
fn stun_marker_box() -> (f32, f32) {
    let w = Painter::text_width(1.0, STUN_MARKER_LABEL);
    (reach_marker_box().0 - 4.0 - w, w)
}

/// Present player units that the party order would affect: Fleeing units and
/// units whose turns have finished included, dead and escaped units excluded.
pub fn present_player_units(state: &BattleState) -> usize {
    state.units.iter().filter(|u| u.is_present() && u.side == Side::Player).count()
}

/// Present player units still queued this round, including the active unit and
/// units deferred by Wait. Fleeing and retreating units are excluded: End Round
/// does not forfeit their automatic turns.
pub fn queued_player_units(state: &BattleState) -> usize {
    state
        .queue
        .iter()
        .filter(|&&id| state.unit(id).is_some_and(|u| u.is_present() && u.side == Side::Player && u.morale != Morale::Fleeing && !u.retreating))
        .count()
}

/// Text rows of the End Round confirmation: (scale, colour, text).
fn end_round_dialog_lines(queued: usize) -> Vec<(f32, Rgba, String)> {
    vec![
        (3.0, YELLOW, "END ROUND?".to_string()),
        (2.0, WHITE, format!("PLAYER UNITS STILL QUEUED: {queued}")),
        (2.0, PREVIEW_NOTE, "(INCLUDES THE ACTIVE UNIT AND WAITING UNITS)".to_string()),
        (2.0, WHITE, "CONFIRMING FORFEITS THE REMAINING ACTIONS OF".to_string()),
        (2.0, WHITE, "ALL QUEUED PLAYER UNITS THIS ROUND.".to_string()),
        (2.0, WHITE, "ENEMIES STILL TAKE THEIR TURNS.".to_string()),
    ]
}

/// Text rows of the Retreat confirmation: (scale, colour, text). Only implemented
/// behaviour is stated: no renown or defense-bonus claims.
fn retreat_dialog_lines(present: usize) -> Vec<(f32, Rgba, String)> {
    vec![
        (3.0, YELLOW, "RETREAT?".to_string()),
        (2.0, WHITE, format!("PLAYER UNITS PRESENT: {present}")),
        (2.0, PREVIEW_NOTE, "(INCLUDES FLEEING UNITS AND FINISHED TURNS)".to_string()),
        (2.0, WHITE, "ALL PRESENT PLAYER UNITS WILL WITHDRAW.".to_string()),
        (2.0, WHITE, "UNITS ON A MAP EDGE ESCAPE IMMEDIATELY;".to_string()),
        (2.0, WHITE, "THE REST MOVE AUTOMATICALLY AND MAY DIE.".to_string()),
        (2.0, WHITE, "THE ORDER CANNOT BE CANCELLED AFTER CONFIRMING.".to_string()),
    ]
}

fn dialog_lines(state: &BattleState, action: ConfirmAction) -> Vec<(f32, Rgba, String)> {
    match action {
        ConfirmAction::EndRound => end_round_dialog_lines(queued_player_units(state)),
        ConfirmAction::Retreat => retreat_dialog_lines(present_player_units(state)),
    }
}

const DIALOG_TEXT_X: f32 = layout::DIALOG.x + 30.0;
const DIALOG_TEXT_Y: f32 = layout::DIALOG.y + 24.0;
const DIALOG_ROW_STEP: f32 = 30.0;

/// APPROXIMATION of the catalogued End Round (UC-COMBAT-017) and Retreat
/// (UC-COMBAT-022) confirmations (UC-UI-007): the wording, layout and look are
/// slice placeholders, not original-UI parity.
fn draw_dialog(p: &mut Painter, state: &BattleState, action: ConfirmAction) {
    let d = layout::DIALOG;
    p.rect(d.x - 4.0, d.y - 4.0, d.w + 8.0, d.h + 8.0, DIALOG_BORDER);
    p.rect(d.x, d.y, d.w, d.h, DIALOG_BG);
    for (i, (scale, col, line)) in dialog_lines(state, action).iter().enumerate() {
        let y = DIALOG_TEXT_Y + if i == 0 { 0.0 } else { 16.0 } + i as f32 * DIALOG_ROW_STEP;
        p.text(DIALOG_TEXT_X, y, *scale, *col, line);
    }
    p.button(layout::CONFIRM_BUTTON, CONFIRM_FILL, WHITE, "CONFIRM (ENTER)");
    p.button(layout::CANCEL_BUTTON, CANCEL_FILL, WHITE, "CANCEL (ESC)");
}

/// Non-obstacle map hexes controlled by a living active player unit.
/// Uses the rules query as the authority and does not mutate battle state.
pub fn control_overlay_hexes(sc: &Scene<'_>) -> Vec<Hex> {
    let s = sc.state;
    if !sc.control_overlay {
        return Vec::new();
    }
    let Some(selected) = selected_active_player(sc) else { return Vec::new() };

    map_hexes()
        .into_iter()
        .filter(|hex| !hex.is_obstacle() && !s.enemy_controllers_at(selected, *hex).is_empty())
        .collect()
}

/// Planned move for the hovered hex, independent of the Z overlay preference.
/// `None` unless the selection is the living active player unit and the rules
/// accept a move there; occupied (attack target), blocked and unaffordable hexes
/// are rejected by `preview_move`. Hidden while any targeting mode is open. Read-only.
pub fn movement_preview(sc: &Scene<'_>) -> Option<MovePreview> {
    if targeting_mode(sc).is_some() {
        return None;
    }
    let selected = selected_active_player(sc)?;
    sc.state.preview_move(selected, sc.hover?).ok()
}

/// Live morale badge for the unit panel. The percentage comes from the rules
/// model and describes melee skill/defense scaling, not final hit chance.
fn morale_panel_label(morale: Morale) -> (String, Rgba) {
    let (state, color) = match morale {
        Morale::Steady => ("STEADY", WHITE),
        Morale::Confident => ("CONFIDENT", GREEN),
        Morale::Wavering => ("WAVERING", YELLOW),
        Morale::Breaking => ("BREAKING", RED),
        Morale::Fleeing => ("FLEEING", FLEEING),
    };
    (format!("MORALE {state} {}%", morale.modifier_percent()), color)
}

/// Text rows of the movement preview box (at most four, the box capacity).
/// Planned costs exclude reaction effects. The edge row is conditional wording for
/// `plan.first_edge_penalty`, a route consequence of the mover's current morale and
/// not a prediction of arrival (APPROXIMATION from UC-COMBAT-048). When both
/// warnings show, the note rows are combined so neither warning is dropped.
fn movement_preview_lines(plan: &MovePreview) -> Vec<(String, Rgba)> {
    let melee_budget = if plan.attack_budget_available { "YES" } else { "NO" };
    let mut lines = vec![
        (
            format!("MOVE AP {} FAT {} LEFT {} MELEE BUDGET {}", plan.ap_cost, plan.fatigue_cost, plan.ap_remaining, melee_budget),
            PREVIEW_COST,
        ),
    ];
    if let Some(threat) = &plan.first_threat {
        let n = threat.controllers.len();
        lines.push((
            format!(
                "! WARNING: LEAVING ({},{}) - {} CONTROLLER{}",
                threat.departure.q,
                threat.departure.r,
                n,
                if n == 1 { "" } else { "S" }
            ),
            PREVIEW_WARN,
        ));
    }
    if let Some(edge) = plan.first_edge_penalty {
        lines.push((
            format!("! IF REACHED: EDGE ({},{}) LOWERS MORALE TO BREAKING, NOT EXTRACTED.", edge.q, edge.r),
            PREVIEW_WARN,
        ));
    }
    match (&plan.first_threat, plan.first_edge_penalty) {
        (Some(_), Some(_)) => {
            lines.push(("REACTIONS MAY STOP MOVE, ADD FATIGUE, EFFECTS EXCLUDED FROM COSTS.".to_string(), PREVIEW_NOTE));
        }
        (Some(_), None) => {
            lines.push(("REACTION ATTACKS MAY STOP THE MOVE AND ADD FATIGUE.".to_string(), PREVIEW_NOTE));
            lines.push(("PLANNED COSTS DO NOT INCLUDE REACTION EFFECTS.".to_string(), PREVIEW_NOTE));
        }
        (None, _) => lines.push(("PLANNED COSTS DO NOT INCLUDE REACTION EFFECTS.".to_string(), PREVIEW_NOTE)),
    }
    lines
}

/// Text rows of the height legend: (text, colour). The H shortcut and its state,
/// what the badge means and the one rules effect that is easy to miss. APPROXIMATION:
/// slice presentation of the UC-COMBAT-010 / UC-UI-003/006 elevation display, not
/// original-UI parity.
fn height_legend_lines(on: bool) -> [(&'static str, Rgba); 3] {
    [
        (if on { "H HEIGHT NUMBERS ON" } else { "H HEIGHT NUMBERS OFF" }, YELLOW),
        ("BADGE = HEIGHT 0-3", HEIGHT_BADGE),
        ("UPHILL +1 AP PER LEVEL", HEIGHT_LEGEND_NOTE),
    ]
}

/// Whether `h` is a passable (non-obstacle) Swamp hex, read from `BattleState.ground`.
fn is_passable_swamp(s: &BattleState, h: Hex) -> bool {
    !h.is_obstacle() && s.ground.get(h) == Some(TerrainKind::Swamp)
}

/// Whether any passable Swamp hex exists; it switches on the Swamp legend and the attack row in
/// the preview strip.
pub fn has_visible_swamp(s: &BattleState) -> bool {
    map_hexes().into_iter().any(|h| is_passable_swamp(s, h))
}

/// Text rows of the Swamp legend: (text, colour). The AP value is the accepted rules constant.
/// APPROXIMATION: slice presentation of the T48 terrain, not original-UI parity.
fn swamp_legend_lines() -> [(String, Rgba); 3] {
    [
        ("S = SWAMP".to_string(), SWAMP_BADGE),
        (format!("BASE MOVE {SWAMP_MOVE_AP} AP"), SWAMP_LEGEND_NOTE),
        ("FATIGUE IN MOVE PREVIEW".to_string(), SWAMP_LEGEND_NOTE),
    ]
}

/// Preview-strip rows for an adjacent hovered enemy. `preview_attack` owns valid-plan
/// costs, chance and conditional pre-armour damage; resource failures retain the live
/// chance query and explain why a plan is unavailable. Other query errors suppress it.
fn attack_preview_lines(s: &BattleState, attacker: u32, target: &bb_rules::Unit) -> Option<Vec<(String, Rgba)>> {
    let target_row = |chance| (format!("ATTACK {}: HIT {chance}%", target.name), YELLOW);
    match s.preview_attack(attacker, target.id) {
        Ok(plan) => Some(vec![
            target_row(plan.hit_chance),
            (format!("COST {} AP {} FAT", plan.ap_cost, plan.fatigue_cost), PREVIEW_COST),
            (format!("DAMAGE ON HIT {}-{}", plan.damage_min, plan.damage_max), PREVIEW_COST),
            ("BEFORE ARMOUR. MISSES DEAL 0.".to_string(), PREVIEW_NOTE),
        ]),
        Err(RuleError::NotEnoughAp) => Some(vec![
            target_row(s.hit_chance(attacker, target.id)),
            ("UNAVAILABLE: NOT ENOUGH AP".to_string(), PREVIEW_WARN),
        ]),
        Err(RuleError::TooFatigued) => Some(vec![
            target_row(s.hit_chance(attacker, target.id)),
            ("UNAVAILABLE: TOO FATIGUED".to_string(), PREVIEW_WARN),
        ]),
        Err(_) => None,
    }
}

/// Bottom-left controls hint; must end left of the side panel.
pub const SHORTCUT_HINT: &str = "E END TURN  W WAIT  X END ROUND  V RETREAT  R RESTART  N SEED  ESC QUIT";

pub fn build_scene(sc: &Scene) -> Vec<Vertex> {
    let mut p = Painter { verts: Vec::with_capacity(100_000) };
    let s = sc.state;
    p.rect(0.0, 0.0, WIN_W, WIN_H, BG);

    // Top bar.
    let active = s.active_unit();
    let top = match active {
        Some(u) => format!("ROUND {}  ACTIVE {} ({})  SEED {}", s.round, u.name, if u.side == Side::Player { "PLAYER" } else { "ENEMY" }, sc.seed),
        None => format!("ROUND {}  BATTLE OVER  SEED {}", s.round, sc.seed),
    };
    p.text(16.0, 12.0, 2.0, WHITE, &top);

    let (lx, ly, step) = HEIGHT_LEGEND;
    for (i, (line, col)) in height_legend_lines(sc.height_overlay).iter().enumerate() {
        p.text(lx, ly + i as f32 * step, 2.0, *col, line);
    }

    let swamp_map = has_visible_swamp(s);
    if swamp_map {
        let (sx, sy, sstep) = SWAMP_LEGEND;
        for (i, (line, col)) in swamp_legend_lines().iter().enumerate() {
            p.text(sx, sy + i as f32 * sstep, 2.0, *col, line);
        }
    }

    // A targeting mode replaces the ordinary action display: no movement highlights,
    // costs, preview or attack labels, so exactly one action is shown.
    let targeting = targeting_mode(sc);
    let swap_ids = rotation_targeting_actor(sc).map(|a| s.legal_rotation_targets(a)).unwrap_or_default();
    let swap_hexes: Vec<Hex> = swap_ids.iter().map(|&id| s.units[id as usize].pos).collect();
    let step_hexes: Vec<Hex> = footwork_targeting_actor(sc).map(|a| s.legal_footwork_destinations(a)).unwrap_or_default();
    let stun_ids = stunning_blow_targeting_actor(sc).map(|a| s.legal_stunning_blow_targets(a)).unwrap_or_default();
    let stun_hexes: Vec<Hex> = stun_ids.iter().map(|&id| s.units[id as usize].pos).collect();
    let reach: &[(Hex, u32)] = if targeting.is_some() { &[] } else { sc.reach };

    // Hexes.
    let hover_target = sc
        .hover
        .and_then(|h| s.units.iter().find(|u| u.is_present() && u.pos == h))
        .filter(|_| targeting.is_none())
        .filter(|t| {
            sc.selected.is_some_and(|sel| {
                !s.units[sel as usize].retreating && s.units[sel as usize].side != t.side && s.legal_targets(sel).contains(&t.id)
            })
        });
    let control_hexes = control_overlay_hexes(sc);
    for h in map_hexes() {
        let c = layout::hex_to_pixel(h);
        let swamp = is_passable_swamp(s, h);
        let mut fill = if h.is_obstacle() {
            BROWN
        } else if swamp {
            SWAMP_FILL
        } else {
            GREY
        };
        if reach.iter().any(|(r, _)| *r == h) {
            fill = GREEN;
        }
        if hover_target.is_some_and(|t| t.pos == h) {
            fill = RED;
        }
        if swap_hexes.contains(&h) {
            fill = if sc.hover == Some(h) { SWAP_HOVER } else { SWAP_FILL };
        }
        if step_hexes.contains(&h) {
            fill = if sc.hover == Some(h) { STEP_HOVER } else { STEP_FILL };
        }
        if stun_hexes.contains(&h) {
            fill = if sc.hover == Some(h) { STUN_TARGET_HOVER } else { STUN_TARGET_FILL };
        }
        p.hex_shape(c, fill);
        if control_hexes.contains(&h) {
            p.control_marker(c);
        }
        if sc.height_overlay {
            p.height_badge(c, s.elevations.get(h).unwrap_or(0));
        }
        if swamp {
            p.swamp_badge(c);
        }
    }
    for (h, cost) in reach {
        let c = layout::hex_to_pixel(*h);
        p.text(c.0 - 6.0, c.1 - 7.0, 2.0, [0.05, 0.2, 0.05, 1.0], &cost.to_string());
    }

    // Tokens.
    for u in s.units.iter().filter(|u| u.is_present()) {
        let c = layout::hex_to_pixel(u.pos);
        if active.is_some_and(|a| a.id == u.id) {
            p.circle(c, 22.0, YELLOW);
        }
        if sc.selected == Some(u.id) {
            p.circle(c, 20.0, WHITE);
        }
        p.circle(c, 18.0, if u.side == Side::Player { BLUE } else { RED });
        p.text(c.0 - 12.0, c.1 - 7.0, 2.0, WHITE, u.name);
        let bar = |p: &mut Painter, y: f32, v: u32, max: u32, col: Rgba| {
            p.rect(c.0 - 18.0, y, 36.0, 4.0, [0.0, 0.0, 0.0, 1.0]);
            p.rect(c.0 - 18.0, y, 36.0 * v as f32 / max.max(1) as f32, 4.0, col);
        };
        bar(&mut p, c.1 + 20.0, u.hp, u.stats.hp, [0.2, 0.9, 0.2, 1.0]);
        bar(&mut p, c.1 + 25.0, u.armour, u.stats.armour, [0.8, 0.8, 0.9, 1.0]);
    }
    // Preserve the pre-existing above-target chance label only where no Swamp legend/badge can collide.
    if let (Some(t), Some(sel), false) = (hover_target, sc.selected, swamp_map) {
        let c = layout::hex_to_pixel(t.pos);
        p.text(c.0 - 36.0, c.1 - 46.0, 2.0, YELLOW, &format!("HIT {}%", s.hit_chance(sel, t.id)));
    }

    for &id in &swap_ids {
        let c = layout::hex_to_pixel(s.units[id as usize].pos);
        p.text(c.0 + SWAP_LABEL_OFFSET.0, c.1 + SWAP_LABEL_OFFSET.1, 1.0, SWAP_TEXT, "SWAP");
    }

    for &h in &step_hexes {
        let c = layout::hex_to_pixel(h);
        p.text(c.0 + STEP_LABEL_OFFSET.0, c.1 + STEP_LABEL_OFFSET.1, 1.0, STEP_TEXT, "STEP");
    }

    for &h in &stun_hexes {
        let c = layout::hex_to_pixel(h);
        p.text(c.0 + STUN_LABEL_OFFSET.0, c.1 + STUN_LABEL_OFFSET.1, 1.0, STUN_TARGET_TEXT, "STUN");
    }

    let preview_lines = match targeting {
        Some((TargetAction::Rotation, actor)) => {
            let hovered = sc.hover.and_then(|h| swap_ids.iter().copied().find(|&id| s.units[id as usize].pos == h));
            let plan = hovered.and_then(|id| s.preview_rotation(actor, id).ok());
            Some(rotation_target_lines(s, actor, plan.as_ref()))
        }
        Some((TargetAction::Footwork, actor)) => {
            let hovered = sc.hover.filter(|h| step_hexes.contains(h));
            let plan = hovered.and_then(|to| s.preview_footwork(actor, to).ok());
            Some(footwork_target_lines(s, actor, plan.as_ref()))
        }
        Some((TargetAction::StunningBlow, actor)) => {
            let hovered = sc.hover.and_then(|h| stun_ids.iter().copied().find(|&id| s.units[id as usize].pos == h));
            let plan = hovered.and_then(|id| s.preview_stunning_blow(actor, id).ok());
            Some(stunning_blow_target_lines(s, actor, plan.as_ref()))
        }
        // An occupied enemy hex has no movement preview, so attack information never shares the strip with it.
        None => movement_preview(sc).map(|plan| movement_preview_lines(&plan)).or_else(|| {
            let (t, sel) = hover_target.zip(selected_active_player(sc))?;
            attack_preview_lines(s, sel, t)
        }),
    };
    if let Some(lines) = preview_lines {
        let (x, y, w, h) = PREVIEW_BOX;
        p.rect(x, y, w, h, PREVIEW_BG);
        for (i, (line, col)) in lines.iter().enumerate() {
            p.text(x + 8.0, y + 6.0 + i as f32 * 16.0, 2.0, *col, line);
        }
    }

    // Side panel.
    p.rect(PANEL_X - 12.0, 44.0, WIN_W - PANEL_X + 12.0, WIN_H - 44.0, [0.12, 0.12, 0.15, 1.0]);
    for (i, u) in s.units.iter().enumerate() {
        let y = 52.0 + i as f32 * 60.0;
        let col = if !u.alive {
            DIM
        } else if u.fled {
            FLED
        } else if active.is_some_and(|a| a.id == u.id) {
            YELLOW
        } else {
            WHITE
        };
        if u.fled && u.alive {
            p.text(PANEL_X, y, 2.0, col, &format!("{} FLED", u.name));
        } else if u.alive {
            p.text(PANEL_X, y, 2.0, col, u.name);
            let (label, morale_color) = morale_panel_label(u.morale);
            let label_x = PANEL_X + Painter::text_width(2.0, u.name) + 10.0;
            p.text(label_x, y, 2.0, morale_color, &label);
        } else {
            p.text(PANEL_X, y, 2.0, col, &format!("{} DEAD", u.name));
        }
        p.text(PANEL_X, y + 18.0, 2.0, col, &format!("HP {}/{} ARM {}/{}", u.hp, u.stats.hp, u.armour, u.stats.armour));
        p.text(PANEL_X, y + 36.0, 2.0, col, &format!("AP {}/{} FAT {}/{}", u.ap, u.stats.ap_max, u.fatigue, u.stats.fatigue_max));
        if u.is_present() && u.stunned {
            p.stun_marker(y);
        }
        if u.is_present() && u.melee_reach == MeleeReach::TwoHex {
            p.reach_marker(y);
        }
        if u.is_present() && u.has_fast_adaptation {
            p.adaptation_marker(y + 18.0, u.fast_adaptation_stacks);
        }
        if u.is_killing_frenzy_active() {
            p.frenzy_marker(y);
        }
        if u.is_present() && u.adrenaline_pending {
            p.pending_marker(y + 36.0);
        }
        if u.is_present() && u.has_dodge {
            p.dodge_breakdown(y, u);
        }
        if u.is_present() && u.has_nine_lives {
            p.nine_lives_marker(y, u.nine_lives_used);
        }
        if u.is_indomitable_active() {
            p.indomitable_marker(y);
        }
    }
    p.adrenaline_button(adrenaline_enabled(sc));
    let look = |action: TargetAction, enabled: bool| match (targeting.map(|(a, _)| a) == Some(action), enabled) {
        (true, _) => SkillLook::Active,
        (false, true) => SkillLook::Enabled,
        (false, false) => SkillLook::Disabled,
    };
    p.rotation_button(look(TargetAction::Rotation, rotation_enabled(sc)));
    p.footwork_button(look(TargetAction::Footwork, footwork_enabled(sc)));
    p.stunning_blow_button(look(TargetAction::StunningBlow, stunning_blow_enabled(sc)));
    p.indomitable_button(if indomitable_stance_shown(sc) {
        SkillLook::Active
    } else if indomitable_enabled(sc) {
        SkillLook::Enabled
    } else {
        SkillLook::Disabled
    });
    p.recover_button(recover_enabled(sc));
    // End Round and Retreat launch buttons (APPROXIMATION of the catalogued controls).
    for (rect, label, enabled) in [
        (layout::END_ROUND_BUTTON, layout::END_ROUND_LABEL, end_round_enabled(sc)),
        (layout::RETREAT_BUTTON, layout::RETREAT_LABEL, retreat_enabled(sc)),
    ] {
        if enabled {
            p.button(rect, BUTTON_ENABLED, WHITE, label);
        } else {
            p.button(rect, BUTTON_DISABLED, BUTTON_DISABLED_TEXT, label);
        }
    }
    // Turn queue.
    let q: Vec<&str> = s.queue.iter().map(|&id| s.units[id as usize].name).collect();
    p.text(PANEL_X, 420.0, 2.0, WHITE, "QUEUE");
    p.text(PANEL_X, 440.0, 2.0, WHITE, &q.join(" "));
    p.text(PANEL_X, 460.0, 2.0, YELLOW, &format!("Z ZOC OVERLAY {}", if sc.control_overlay { "ON" } else { "OFF" }));
    p.text(PANEL_X, 480.0, 2.0, WHITE, "LEAVING ! MAY TRIGGER");
    p.text(PANEL_X, 498.0, 2.0, WHITE, "FREE ATTACKS");
    // Log.
    p.text(PANEL_X, 526.0, 2.0, WHITE, "LOG");
    for (i, l) in sc.log.iter().rev().take(6).rev().enumerate() {
        let l: String = l.chars().take(31).collect();
        p.text(PANEL_X, 546.0 + i as f32 * 16.0, 2.0, DIM, &l);
    }
    p.text(16.0, WIN_H - 24.0, 2.0, DIM, SHORTCUT_HINT);
    if let Some(e) = sc.error {
        p.rect(0.0, WIN_H - 60.0, 860.0, 28.0, [0.4, 0.05, 0.05, 1.0]);
        p.text(16.0, WIN_H - 54.0, 2.0, WHITE, e);
    }

    // Banner.
    if let Some(o) = s.outcome() {
        // The finished-battle preview box is hidden, so the banner takes that strip below the
        // map; no hex (nor its height badge) lies under it.
        let (bx, by, bw, bh) = BANNER_BOX;
        p.rect(bx, by, bw, bh, [0.0, 0.0, 0.0, 0.85]);
        let (t, col) = match o {
            Outcome::Victory => ("VICTORY", GREEN),
            Outcome::Defeat => ("DEFEAT", RED),
            Outcome::Retreat => ("RETREAT", YELLOW),
        };
        p.text(bx + 16.0, by + 8.0, 5.0, col, t);
        if s.end_reason() == Some(EndReason::TimeLimit) {
            p.text(bx + 254.0, by + 14.0, 2.0, WHITE, "TIME LIMIT");
        }
        if s.end_reason() == Some(EndReason::Escape) {
            p.text(bx + 254.0, by + 14.0, 2.0, WHITE, "UNITS ESCAPED");
        }
        p.text(bx + 254.0, by + 40.0, 2.0, WHITE, "R RESTART  N NEW SEED");
    }

    // Modal last so it sits above every other element.
    if let Some(action) = sc.confirm {
        draw_dialog(&mut p, s, action);
    }
    p.verts
}

/// How many unit rows of `verts` carry the INDOMITABLE marker geometry. Test helper.
#[cfg(test)]
pub(crate) fn indomitable_marker_rows(verts: &[Vertex]) -> usize {
    (0..6)
        .filter(|&row| {
            let mut p = Painter { verts: Vec::new() };
            p.indomitable_marker(52.0 + row as f32 * 60.0);
            verts.windows(p.verts.len()).any(|w| w.iter().zip(&p.verts).all(|(a, b)| a.pos == b.pos && a.color == b.color))
        })
        .count()
}

/// Whether `verts` hold preview-strip row `row` drawn with exactly `text`, in any preview text
/// colour. Test helper for the App-level hover assertions.
#[cfg(test)]
pub(crate) fn preview_row_shown(verts: &[Vertex], row: usize, text: &str) -> bool {
    let (x, y, _, _) = PREVIEW_BOX;
    [YELLOW, PREVIEW_COST, PREVIEW_NOTE, PREVIEW_WARN].into_iter().any(|col| {
        let mut p = Painter { verts: Vec::new() };
        p.text(x + 8.0, y + 6.0 + row as f32 * 16.0, 2.0, col, text);
        verts.windows(p.verts.len()).any(|w| w.iter().zip(&p.verts).all(|(a, b)| a.pos == b.pos && a.color == b.color))
    })
}

/// Fill of reachable-hex highlights. Test helper.
#[cfg(test)]
pub(crate) const REACH_FILL: Rgba = GREEN;

/// The map hexes whose inner fill is `fill`, in map order. Test helper.
#[cfg(test)]
pub(crate) fn hexes_filled(verts: &[Vertex], fill: Rgba) -> Vec<Hex> {
    map_hexes()
        .into_iter()
        .filter(|&h| {
            let mut p = Painter { verts: Vec::new() };
            p.fan(&layout::corners(layout::hex_to_pixel(h), HEX_SIZE - 2.0), fill);
            verts.windows(p.verts.len()).any(|w| w.iter().zip(&p.verts).all(|(a, b)| a.pos == b.pos && a.color == b.color))
        })
        .collect()
}

/// The fill colour of the skill button drawn on `r`: the first vertex of its inner fill
/// rectangle (2 px inside the border). Test helper.
#[cfg(test)]
pub(crate) fn skill_fill(verts: &[Vertex], r: Rect) -> Option<Rgba> {
    let at = |v: &Vertex| ((v.pos[0] + 1.0) / 2.0 * WIN_W, (1.0 - v.pos[1]) / 2.0 * WIN_H);
    verts
        .iter()
        .filter(|v| [SKILL_ENABLED, SKILL_DISABLED, SKILL_ACTIVE].contains(&v.color))
        .find(|v| {
            let p = at(v);
            (p.0 - (r.x + 2.0)).abs() < 0.01 && (p.1 - (r.y + 2.0)).abs() < 0.01
        })
        .map(|v| v.color)
}

#[cfg(test)]
mod tests {
    use super::*;
    use bb_rules::Command;
    use bb_rules::rules::{ATTACK_AP, ATTACK_FATIGUE};
    use bb_rules::{BattleState, Morale, RuleError, hex};

    fn fixture() -> BattleState {
        let mut state = BattleState::new(23);
        for unit in &mut state.units {
            unit.alive = false;
        }
        state.units[0].alive = true;
        state.units[0].pos = hex(-2, 0);
        state.units[1].alive = true;
        state.units[1].pos = hex(-4, 2);
        state.units[3].alive = true;
        state.units[3].pos = hex(-1, 0);
        state.units[4].alive = true;
        state.units[4].pos = hex(-3, 0);
        // This dead enemy is adjacent to (0, 3), which must remain unmarked.
        state.units[5].pos = hex(1, 3);
        state.queue = vec![0];
        state
    }

    fn scene<'a>(state: &'a BattleState, selected: Option<u32>, enabled: bool) -> Scene<'a> {
        Scene {
            state,
            selected,
            hover: None,
            reach: &[],
            log: &[],
            error: None,
            seed: 23,
            control_overlay: enabled,
            height_overlay: false,
            confirm: None,
            targeting: None,
        }
    }

    #[test]
    fn overlay_uses_living_enemy_control_once_and_skips_obstacles() {
        let state = fixture();
        let sc = scene(&state, Some(0), true);
        let marked = control_overlay_hexes(&sc);

        // The two living enemies at (-1, 0) and (-3, 0) control overlapping
        // adjacent hexes. The selected tile is shared; obstacle hexes are
        // omitted even when an enemy is adjacent to them.
        assert_eq!(marked.len(), 8);
        for h in [
            hex(-2, 0), hex(0, -1), hex(-1, -1), hex(-2, 1),
            hex(-3, -1), hex(-4, 0), hex(-4, 1), hex(-3, 1),
        ] {
            assert_eq!(marked.iter().filter(|&&candidate| candidate == h).count(), 1, "{h:?}");
        }
        assert!(hex(0, 0).is_obstacle());
        assert!(!marked.contains(&hex(0, 0)));
        assert!(!marked.contains(&hex(-1, 1)));
        assert!(!marked.contains(&hex(-2, -1)));
        assert!(!marked.contains(&hex(0, 3)), "dead enemy control must be ignored");
        assert!(!marked.contains(&hex(-4, 3)), "allied control must be ignored");
        assert!(marked.contains(&hex(-4, 0)), "map-edge hexes remain eligible");
    }

    #[test]
    fn overlay_is_hidden_without_a_valid_active_player_or_during_enemy_turns() {
        let mut state = fixture();
        assert!(control_overlay_hexes(&scene(&state, None, true)).is_empty());
        assert!(control_overlay_hexes(&scene(&state, Some(99), true)).is_empty());

        state.queue = vec![3];
        assert!(control_overlay_hexes(&scene(&state, Some(0), true)).is_empty());
        state.queue = vec![0];

        state.units[0].alive = false;
        assert!(control_overlay_hexes(&scene(&state, Some(0), true)).is_empty());
        state.units[0].alive = true;
        assert!(control_overlay_hexes(&scene(&state, Some(1), true)).is_empty(), "an ally that is not active is invalid selection");

        state.outcome = Some(Outcome::Victory);
        assert!(control_overlay_hexes(&scene(&state, Some(0), true)).is_empty());
    }

    #[test]
    fn control_marker_stays_inside_hex_and_clear_of_unit_rings() {
        let centre = (500.0, 300.0);
        let mut painter = Painter { verts: Vec::new() };
        painter.control_marker(centre);

        let corners = layout::corners(centre, HEX_SIZE);
        for vertex in &painter.verts {
            let point = ((vertex.pos[0] + 1.0) * WIN_W / 2.0, (1.0 - vertex.pos[1]) * WIN_H / 2.0);
            let dx = point.0 - centre.0;
            let dy = point.1 - centre.1;
            assert!(dx.hypot(dy) > 22.0, "marker overlaps active/selected unit rings at {point:?}");

            let crosses: Vec<_> = (0..corners.len())
                .map(|i| {
                    let a = corners[i];
                    let b = corners[(i + 1) % corners.len()];
                    (b.0 - a.0) * (point.1 - a.1) - (b.1 - a.1) * (point.0 - a.0)
                })
                .collect();
            assert!(
                crosses.iter().all(|cross| *cross >= -0.001) || crosses.iter().all(|cross| *cross <= 0.001),
                "marker spills outside the hex at {point:?}"
            );
        }
    }

    #[test]
    fn scene_draws_one_corner_marker_per_controlled_hex_only_when_enabled_and_is_pure() {
        let state = fixture();
        let hash = state.state_hash();
        let rng = state.rng.clone();
        let reach = [(hex(-1, -1), 4)];
        let mut enabled = scene(&state, Some(0), true);
        enabled.reach = &reach;
        let verts = build_scene(&enabled);
        let marker_vertices: Vec<_> = verts.iter().filter(|v| v.color == CONTROL_MARKER).collect();
        assert_eq!(marker_vertices.len(), 8 * 66);

        let has_marker_at = |h: Hex| {
            let centre = layout::hex_to_pixel(h);
            let marker_edge = (centre.0 + 5.0, centre.1 - 28.0);
            let ndc = [marker_edge.0 / WIN_W * 2.0 - 1.0, 1.0 - marker_edge.1 / WIN_H * 2.0];
            marker_vertices.iter().any(|v| {
                (v.pos[0] - ndc[0]).abs() < 0.001 && (v.pos[1] - ndc[1]).abs() < 0.001
            })
        };
        for h in [
            hex(-2, 0), hex(0, -1), hex(-1, -1), hex(-2, 1),
            hex(-3, -1), hex(-4, 0), hex(-4, 1), hex(-3, 1),
        ] {
            assert!(has_marker_at(h), "expected corner marker geometry at {h:?}");
        }
        assert!(!has_marker_at(hex(0, 0)), "obstacles must not have markers");

        let mut disabled = scene(&state, Some(0), false);
        disabled.reach = &reach;
        let disabled_verts = build_scene(&disabled);
        assert!(!disabled_verts.iter().any(|v| v.color == CONTROL_MARKER));
        let count_color = |vertices: &[Vertex], color: Rgba| vertices.iter().filter(|v| v.color == color).count();
        assert!(count_color(&verts, GREEN) > 0, "reachable tiles keep their green fill");
        assert_eq!(count_color(&verts, GREEN), count_color(&disabled_verts, GREEN));
        let ap_label_color = [0.05, 0.2, 0.05, 1.0];
        assert!(count_color(&verts, ap_label_color) > 0, "reachable AP labels remain present");
        assert_eq!(count_color(&verts, ap_label_color), count_color(&disabled_verts, ap_label_color));
        for _ in 0..3 {
            let _ = control_overlay_hexes(&enabled);
            let _ = build_scene(&enabled);
        }
        assert_eq!(state.state_hash(), hash);
        assert_eq!(state.rng, rng);
    }

    // --- Movement preview -------------------------------------------------

    /// Mover P1 (-2,-2) with 9 AP, enemy E1 at (1,-3): the unobstructed lane
    /// from the rules tests. Hovering (2,-2) first threatens departure (0,-2).
    fn lane_fixture() -> BattleState {
        let mut state = BattleState::new(23);
        for unit in &mut state.units {
            unit.alive = false;
        }
        state.units[0].alive = true;
        state.units[0].pos = hex(-2, -2);
        state.units[0].ap = 9;
        state.units[3].alive = true;
        state.units[3].pos = hex(1, -3);
        state.queue = vec![0];
        state
    }

    fn hovering<'a>(state: &'a BattleState, selected: Option<u32>, hover: Option<Hex>, enabled: bool) -> Scene<'a> {
        Scene { hover, ..scene(state, selected, enabled) }
    }

    fn to_px(v: &Vertex) -> (f32, f32) {
        ((v.pos[0] + 1.0) * WIN_W / 2.0, (1.0 - v.pos[1]) * WIN_H / 2.0)
    }

    fn panel_row_y(id: u32) -> f32 {
        52.0 + id as f32 * 60.0
    }

    fn label_text(morale: Morale) -> String {
        let state = match morale {
            Morale::Steady => "STEADY",
            Morale::Confident => "CONFIDENT",
            Morale::Wavering => "WAVERING",
            Morale::Breaking => "BREAKING",
            Morale::Fleeing => "FLEEING",
        };
        format!("MORALE {state} {}%", morale.modifier_percent())
    }

    fn label_colour(morale: Morale) -> Rgba {
        match morale {
            Morale::Steady => WHITE,
            Morale::Confident => GREEN,
            Morale::Wavering => YELLOW,
            Morale::Breaking => RED,
            Morale::Fleeing => FLEEING,
        }
    }

    /// Find a text run in the actual CPU scene geometry at the requested location.
    fn text_geometry(verts: &[Vertex], x: f32, y: f32, scale: f32, col: Rgba, text: &str) -> Option<Vec<(f32, f32)>> {
        let mut expected = Painter { verts: Vec::new() };
        expected.text(x, y, scale, col, text);
        verts
            .windows(expected.verts.len())
            .find(|window| {
                window.iter().zip(&expected.verts).all(|(got, want)| got.pos == want.pos && got.color == want.color)
            })
            .map(|window| window.iter().map(to_px).collect())
    }

    fn morale_label_x(state: &BattleState, id: u32) -> f32 {
        PANEL_X + Painter::text_width(2.0, state.units[id as usize].name) + 10.0
    }

    #[test]
    fn morale_panel_renders_all_state_labels_for_both_sides() {
        let mut state = BattleState::new(23);
        for morale in [Morale::Steady, Morale::Confident, Morale::Wavering, Morale::Breaking, Morale::Fleeing] {
            state.units[0].morale = morale;
            state.units[3].morale = morale;
            let verts = build_scene(&scene(&state, Some(0), false));
            for id in [0, 3] {
                let x = morale_label_x(&state, id);
                assert!(
                    text_geometry(&verts, x, panel_row_y(id), 2.0, label_colour(morale), &label_text(morale)).is_some(),
                    "missing {:?} label geometry for {:?} unit {}",
                    morale,
                    state.units[id as usize].side,
                    id
                );
            }
        }
    }

    #[test]
    fn morale_panel_reflects_resolve_check_on_next_scene_without_mutation() {
        // Seed 2's first draw is 10, below the fixture's 55% melee chance.
        let mut state = BattleState::new(2);
        state.units[0].pos = hex(1, 0);
        state.units[0].ap = 9;
        state.units[0].stats.dmg_min = 15;
        state.units[0].stats.dmg_max = 15;
        state.units[3].pos = hex(2, 0);
        state.units[3].armour = 0;
        state.units[3].stats.armour = 0;
        state.units[3].stats.resolve = 0;
        state.queue.retain(|&id| id != 0);
        state.queue.insert(0, 0);

        let row_y = panel_row_y(3);
        let label_x = morale_label_x(&state, 3);
        let before_hash = state.state_hash();
        let before_rng = state.rng.clone();
        let steady = build_scene(&scene(&state, Some(0), false));
        assert!(text_geometry(&steady, label_x, row_y, 2.0, WHITE, "MORALE STEADY 100%").is_some());
        for _ in 0..3 {
            let _ = build_scene(&scene(&state, Some(0), false));
        }
        assert_eq!(state.state_hash(), before_hash);
        assert_eq!(state.rng, before_rng);

        let events = state.apply(bb_rules::Command::Attack { attacker: 0, target: 3 }).unwrap();
        assert!(matches!(
            events.as_slice(),
            [
                bb_rules::Event::Attacked { hit: true, hp_loss: 15, .. },
                bb_rules::Event::MoraleChecked {
                    unit: 3,
                    chance: 0,
                    before: Morale::Steady,
                    after: Morale::Wavering,
                    ..
                }
            ]
        ));
        assert_eq!(state.units[3].morale, Morale::Wavering);

        let after_hash = state.state_hash();
        let after_rng = state.rng.clone();
        let wavering = build_scene(&scene(&state, Some(0), false));
        assert!(text_geometry(&wavering, label_x, row_y, 2.0, YELLOW, "MORALE WAVERING 90%").is_some());
        for _ in 0..3 {
            let _ = build_scene(&scene(&state, Some(0), false));
        }
        assert_eq!(state.state_hash(), after_hash);
        assert_eq!(state.rng, after_rng);
    }

    #[test]
    fn morale_panel_keeps_dead_indication_and_active_name_highlighting() {
        let mut state = BattleState::new(23);
        state.units[1].alive = false;
        let verts = build_scene(&scene(&state, Some(0), false));

        assert!(text_geometry(&verts, PANEL_X, panel_row_y(0), 2.0, YELLOW, "P1").is_some());
        assert!(text_geometry(
            &verts,
            PANEL_X,
            panel_row_y(1),
            2.0,
            DIM,
            "P2 DEAD"
        )
        .is_some());
        let dead_x = morale_label_x(&state, 1);
        for morale in [Morale::Steady, Morale::Confident, Morale::Wavering, Morale::Breaking, Morale::Fleeing] {
            assert!(text_geometry(&verts, dead_x, panel_row_y(1), 2.0, label_colour(morale), &label_text(morale)).is_none());
        }
    }

    #[test]
    fn longest_morale_label_fits_name_row_inside_panel_and_clears_resource_rows() {
        let mut state = BattleState::new(23);
        state.units[0].morale = Morale::Confident;
        let verts = build_scene(&scene(&state, Some(0), false));
        let x = morale_label_x(&state, 0);
        let y = panel_row_y(0);
        let points = text_geometry(&verts, x, y, 2.0, GREEN, "MORALE CONFIDENT 110%")
            .expect("longest morale label geometry should be emitted by build_scene");
        let min_x = points.iter().map(|p| p.0).fold(f32::INFINITY, f32::min);
        let max_x = points.iter().map(|p| p.0).fold(f32::NEG_INFINITY, f32::max);
        let min_y = points.iter().map(|p| p.1).fold(f32::INFINITY, f32::min);
        let max_y = points.iter().map(|p| p.1).fold(f32::NEG_INFINITY, f32::max);
        assert!(min_x >= PANEL_X, "label starts outside the panel: {min_x}");
        assert!(max_x < WIN_W, "label ends outside the panel: {max_x}");
        assert!(min_y + 0.001 >= y && max_y <= y + 14.001, "label escapes the existing name row: y={min_y}..{max_y}");
        assert!(max_y < y + 18.0, "label overlaps the HP/armour row");
        assert!(max_y < y + 36.0, "label overlaps the AP/fatigue row");
        assert!(text_geometry(&verts, PANEL_X, y + 18.0, 2.0, YELLOW, "HP 60/60 ARM 40/40").is_some());
        assert!(text_geometry(&verts, PANEL_X, y + 36.0, 2.0, YELLOW, "AP 9/9 FAT 0/100").is_some());
    }

    fn preview_colours() -> [Rgba; 4] {
        [PREVIEW_BG, PREVIEW_COST, PREVIEW_WARN, PREVIEW_NOTE]
    }

    fn preview_vertices(verts: &[Vertex]) -> Vec<(f32, f32, Rgba)> {
        let colours = preview_colours();
        verts.iter().filter(|v| colours.contains(&v.color)).map(|v| (v.pos[0], v.pos[1], v.color)).collect()
    }

    /// Whether `verts` contain the exact geometry of `text` drawn at preview row `row`.
    fn has_row(verts: &[Vertex], row: usize, col: Rgba, text: &str) -> bool {
        let mut expected = Painter { verts: Vec::new() };
        expected.text(PREVIEW_BOX.0 + 8.0, PREVIEW_BOX.1 + 6.0 + row as f32 * 16.0, 2.0, col, text);
        let same = |a: &Vertex, b: &Vertex| a.pos == b.pos && a.color == b.color;
        verts.windows(expected.verts.len()).any(|w| w.iter().zip(&expected.verts).all(|(a, b)| same(a, b)))
    }

    #[test]
    fn preview_shows_planned_costs_and_first_threat_warning() {
        let state = lane_fixture();
        let verts = build_scene(&hovering(&state, Some(0), Some(hex(2, -2)), false));
        assert!(has_row(&verts, 0, PREVIEW_COST, "MOVE AP 8 FAT 8 LEFT 1 MELEE BUDGET NO"));
        assert!(has_row(&verts, 1, PREVIEW_WARN, "! WARNING: LEAVING (0,-2) - 1 CONTROLLER"));
        assert!(has_row(&verts, 2, PREVIEW_NOTE, "REACTION ATTACKS MAY STOP THE MOVE AND ADD FATIGUE."));
        assert!(has_row(&verts, 3, PREVIEW_NOTE, "PLANNED COSTS DO NOT INCLUDE REACTION EFFECTS."));

        // A second controller of the same departure changes only the count.
        let mut two = lane_fixture();
        two.units[4].alive = true;
        two.units[4].pos = hex(0, -1);
        let verts = build_scene(&hovering(&two, Some(0), Some(hex(2, -2)), false));
        assert!(has_row(&verts, 1, PREVIEW_WARN, "! WARNING: LEAVING (0,-2) - 2 CONTROLLERS"));
        assert!(!has_row(&verts, 1, PREVIEW_WARN, "! WARNING: LEAVING (0,-2) - 1 CONTROLLER"));

        // Entering adjacency only at the destination: costs, no warning or reaction text.
        let verts = build_scene(&hovering(&state, Some(0), Some(hex(0, -2)), false));
        assert!(has_row(&verts, 0, PREVIEW_COST, "MOVE AP 4 FAT 4 LEFT 5 MELEE BUDGET YES"));
        assert!(has_row(&verts, 1, PREVIEW_NOTE, "PLANNED COSTS DO NOT INCLUDE REACTION EFFECTS."));
        assert!(!verts.iter().any(|v| v.color == PREVIEW_WARN));
    }

    #[test]
    fn pathfinder_launch_preview_displays_discounted_fatigue_and_boundary_budget_matches_execution() {
        use bb_rules::scenario::{LaunchArgs, TerrainPreset, launch_battle_for};

        let encounter = || {
            let mut state = launch_battle_for(LaunchArgs {
                seed: 1,
                terrain: TerrainPreset::Flat,
                adrenaline: false,
                rotation: false,
                footwork: false,
                fast_adaptation: false,
                dodge: false,
                nine_lives: false,
                berserk: false,
                pathfinder: true,
                indomitable: false,
                two_hex: false,
                stunning_blow: false, killing_frenzy: false,
            });
            for unit in &mut state.units {
                unit.alive = false;
            }
            state.units[0].alive = true;
            state.units[0].pos = hex(-2, -2);
            state.units[0].ap = 9;
            state.units[0].fatigue = 0;
            state.units[0].stats.fatigue_max = 12;
            state.units[0].turn_started = true;
            state.units[3].alive = true;
            state.units[3].pos = hex(4, -2);
            state.queue = vec![0];
            state
        };
        let destination = hex(0, -2);
        let mut state = encounter();
        let mut twin = state.clone();
        twin.units[0].has_pathfinder = false;
        let rng = state.rng.clone();
        let hash = state.state_hash();

        let plan = movement_preview(&hovering(&state, Some(0), Some(destination), false)).unwrap();
        let twin_plan = movement_preview(&hovering(&twin, Some(0), Some(destination), false)).unwrap();
        assert_eq!(plan.path, vec![hex(-1, -2), destination]);
        assert_eq!((plan.path.clone(), plan.ap_cost, plan.ap_remaining), (twin_plan.path.clone(), twin_plan.ap_cost, twin_plan.ap_remaining));
        assert_eq!((plan.fatigue_cost, twin_plan.fatigue_cost), (2, 4));
        assert!(plan.attack_budget_available);
        assert!(!twin_plan.attack_budget_available, "the fatigue discount crosses the nominal melee budget boundary");

        let verts = build_scene(&hovering(&state, Some(0), Some(destination), false));
        assert!(has_row(&verts, 0, PREVIEW_COST, "MOVE AP 4 FAT 2 LEFT 5 MELEE BUDGET YES"));
        let twin_verts = build_scene(&hovering(&twin, Some(0), Some(destination), false));
        assert!(has_row(&twin_verts, 0, PREVIEW_COST, "MOVE AP 4 FAT 4 LEFT 5 MELEE BUDGET NO"));
        assert_eq!((state.state_hash(), state.rng.clone()), (hash, rng.clone()), "preview and scene building are pure");

        let events = state.apply(Command::Move { unit: 0, to: destination }).unwrap();
        assert_eq!(events, vec![bb_rules::Event::Moved { unit: 0, path: plan.path.clone() }]);
        assert_eq!((state.units[0].pos, state.units[0].ap, state.units[0].fatigue), (destination, 5, plan.fatigue_cost));
        assert_eq!(state.rng, rng, "there were no reactions");
        assert_eq!(state.units[0].fatigue, plan.fatigue_cost, "the displayed FAT value is actual expenditure");
    }

    #[test]
    fn preview_is_hidden_for_invalid_destinations_selections_and_finished_battles() {
        let mut state = lane_fixture();
        // E2 adjacent to the mover makes (-1,-2) a legal attack target.
        state.units[4].alive = true;
        state.units[4].pos = hex(-1, -2);
        let shown = |state: &BattleState, selected, hover| movement_preview(&hovering(state, selected, hover, false)).is_some();
        assert!(shown(&state, Some(0), Some(hex(-1, -3))), "control: a plain valid destination");

        assert!(!shown(&state, Some(0), None));
        assert!(!shown(&state, Some(0), Some(hex(-1, -2))), "attack target");
        assert!(!shown(&state, Some(0), Some(hex(-2, -2))), "own hex");
        assert!(!shown(&state, Some(0), Some(hex(-2, -1))), "obstacle");
        assert!(!shown(&state, Some(0), Some(hex(9, 9))), "off-map");
        assert!(!shown(&state, None, Some(hex(-1, -3))), "missing selection");
        assert!(!shown(&state, Some(99), Some(hex(-1, -3))), "unknown selection");
        assert!(!shown(&state, Some(3), Some(hex(-1, -3))), "enemy selection");

        let mut poor = state.clone();
        poor.units[0].ap = 1;
        assert!(!shown(&poor, Some(0), Some(hex(-1, -3))), "unaffordable");
        let mut tired = state.clone();
        tired.units[0].fatigue = tired.units[0].stats.fatigue_max;
        assert!(!shown(&tired, Some(0), Some(hex(-1, -3))), "too fatigued");

        let mut inactive = state.clone();
        inactive.units[1].alive = true;
        inactive.units[1].pos = hex(-4, 2);
        inactive.queue = vec![1, 0];
        assert!(!shown(&inactive, Some(0), Some(hex(-1, -3))), "selected unit is not active");
        let mut enemy_turn = state.clone();
        enemy_turn.queue = vec![3, 0];
        assert!(!shown(&enemy_turn, Some(0), Some(hex(-1, -3))), "enemy turn");
        let mut dead = state.clone();
        dead.units[0].alive = false;
        assert!(!shown(&dead, Some(0), Some(hex(-1, -3))), "dead selection");
        let mut over = state.clone();
        over.outcome = Some(Outcome::Victory);
        assert!(!shown(&over, Some(0), Some(hex(-1, -3))), "completed battle");

        for hidden in [&poor, &inactive, &enemy_turn, &over] {
            let verts = build_scene(&hovering(hidden, Some(0), Some(hex(-1, -3)), true));
            assert!(preview_vertices(&verts).is_empty());
        }
    }

    #[test]
    fn preview_is_independent_of_the_overlay_preference_and_pure() {
        let state = lane_fixture();
        let (hash, rng) = (state.state_hash(), state.rng.clone());
        let on = build_scene(&hovering(&state, Some(0), Some(hex(2, -2)), true));
        let off = build_scene(&hovering(&state, Some(0), Some(hex(2, -2)), false));
        let (with_z, without_z) = (preview_vertices(&on), preview_vertices(&off));
        assert!(!with_z.is_empty());
        assert_eq!(with_z.len(), without_z.len());
        assert!(with_z.iter().zip(&without_z).all(|(a, b)| a == b));
        assert!(on.iter().any(|v| v.color == CONTROL_MARKER));
        assert!(!off.iter().any(|v| v.color == CONTROL_MARKER));
        for _ in 0..3 {
            let _ = build_scene(&hovering(&state, Some(0), Some(hex(2, -2)), true));
        }
        assert_eq!(state.state_hash(), hash);
        assert_eq!(state.rng, rng);
    }

    #[test]
    fn preview_text_stays_in_its_box_clear_of_other_ui() {
        let (bx, by, bw, bh) = PREVIEW_BOX;
        let mut state = lane_fixture();
        state.units[4].alive = true;
        state.units[4].pos = hex(0, -1);
        let log = vec!["line".to_string(); 8];
        let mut sc = hovering(&state, Some(0), Some(hex(2, -2)), true);
        sc.log = &log;
        sc.error = Some("NOT ENOUGH AP");
        let verts = build_scene(&sc);

        let colours = preview_colours();
        let preview: Vec<(f32, f32)> = verts.iter().filter(|v| colours.contains(&v.color)).map(to_px).collect();
        assert!(!preview.is_empty());
        let eps = 0.01;
        for &(x, y) in &preview {
            assert!(x >= bx - eps && x <= bx + bw + eps && y >= by - eps && y <= by + bh + eps, "({x},{y}) outside the preview box");
        }
        // Text (not just the backdrop) fills four rows and stays inside the backdrop.
        let text: Vec<(f32, f32)> = verts.iter().filter(|v| colours[1..].contains(&v.color)).map(to_px).collect();
        let (max_x, max_y) = text.iter().fold((0.0f32, 0.0f32), |m, p| (m.0.max(p.0), m.1.max(p.1)));
        assert!(max_x <= bx + bw - 8.0 && max_y <= by + bh - 4.0, "text reaches the box edge: {max_x},{max_y}");

        // Clear of the map, tokens and their bars, the side panel, error bar and controls.
        let map_bottom = map_hexes().iter().map(|h| layout::hex_to_pixel(*h).1 + HEX_SIZE).fold(0.0f32, f32::max);
        assert!(by > map_bottom, "box overlaps map hexes");
        assert!(bx + bw < PANEL_X - 12.0, "box overlaps the side panel");
        let error_bar_top = WIN_H - 60.0;
        assert!(by + bh < error_bar_top, "box overlaps the error bar");
        let error_vertices = verts.iter().filter(|v| v.color == [0.4, 0.05, 0.05, 1.0]).map(to_px);
        assert!(error_vertices.clone().count() > 0);
        assert!(error_vertices.fold(f32::MAX, |m, p| m.min(p.1)) >= by + bh);
        assert!(by + bh < WIN_H - 24.0, "box overlaps the controls hint");
        // Side-panel and log text start right of the box.
        let panel_left = verts.iter().filter(|v| v.color == DIM || v.color == WHITE).map(to_px).filter(|p| p.1 > by && p.1 < by + bh);
        assert!(panel_left.fold(f32::MAX, |m, p| m.min(p.0)) >= bx + bw);
    }

    // --- Map-edge warning in the preview (APPROXIMATION, UC-COMBAT-048) ---------

    const EDGE_ROW: fn(i32, i32) -> String =
        |q, r| format!("! IF REACHED: EDGE ({q},{r}) LOWERS MORALE TO BREAKING, NOT EXTRACTED.");

    /// P1 on the inner hex (3,-2) with the edge hex (4,-2) as destination. An enemy at
    /// (2,-1) touches the start (a reaction on departure) when `threat` is set.
    fn edge_fixture(threat: bool) -> BattleState {
        let mut state = lane_fixture();
        state.units[0].pos = hex(3, -2);
        state.units[3].alive = threat;
        state.units[3].pos = hex(2, -1);
        state
    }

    /// Negative-coordinate variant: P1 (-1,-2) -> edge (-2,-2) with three controllers.
    fn negative_edge_fixture() -> BattleState {
        let mut state = lane_fixture();
        state.units[0].pos = hex(-1, -2);
        state.units[3].pos = hex(0, -2);
        for (id, at) in [(4, hex(-1, -1)), (5, hex(0, -3))] {
            state.units[id].alive = true;
            state.units[id].pos = at;
        }
        state
    }

    const WARNING_LEAVING: &str = "! WARNING: LEAVING (3,-2) - 1 CONTROLLER";
    const COSTS_NOTE: &str = "PLANNED COSTS DO NOT INCLUDE REACTION EFFECTS.";
    const REACTION_NOTE: &str = "REACTION ATTACKS MAY STOP THE MOVE AND ADD FATIGUE.";
    const COMBINED_NOTE: &str = "REACTIONS MAY STOP MOVE, ADD FATIGUE, EFFECTS EXCLUDED FROM COSTS.";

    #[test]
    fn preview_supports_all_four_reaction_and_edge_warning_combinations() {
        let hover = Some(hex(4, -2));
        // Neither: interior destination on the same fixture.
        let none = build_scene(&hovering(&edge_fixture(false), Some(0), Some(hex(3, -3)), false));
        assert!(has_row(&none, 0, PREVIEW_COST, "MOVE AP 2 FAT 2 LEFT 7 MELEE BUDGET YES"));
        assert!(has_row(&none, 1, PREVIEW_NOTE, COSTS_NOTE));
        assert!(!none.iter().any(|v| v.color == PREVIEW_WARN));

        // Edge only.
        let state = edge_fixture(false);
        assert_eq!(movement_preview(&hovering(&state, Some(0), hover, false)).unwrap().first_edge_penalty, Some(hex(4, -2)));
        let verts = build_scene(&hovering(&state, Some(0), hover, false));
        assert!(has_row(&verts, 0, PREVIEW_COST, "MOVE AP 2 FAT 2 LEFT 7 MELEE BUDGET YES"));
        assert!(has_row(&verts, 1, PREVIEW_WARN, &EDGE_ROW(4, -2)));
        assert!(has_row(&verts, 2, PREVIEW_NOTE, COSTS_NOTE));
        assert!(!has_row(&verts, 1, PREVIEW_WARN, WARNING_LEAVING) && !has_row(&verts, 2, PREVIEW_NOTE, REACTION_NOTE));

        // Reaction only: the edge hex is not on the route.
        let mut state = edge_fixture(true);
        state.units[0].morale = Morale::Breaking;
        let verts = build_scene(&hovering(&state, Some(0), hover, false));
        assert!(has_row(&verts, 1, PREVIEW_WARN, WARNING_LEAVING));
        assert!(has_row(&verts, 2, PREVIEW_NOTE, REACTION_NOTE));
        assert!(has_row(&verts, 3, PREVIEW_NOTE, COSTS_NOTE));
        assert!(!has_row(&verts, 2, PREVIEW_WARN, &EDGE_ROW(4, -2)));

        // Both: compact rows keep every message.
        let state = edge_fixture(true);
        let verts = build_scene(&hovering(&state, Some(0), hover, false));
        assert!(has_row(&verts, 0, PREVIEW_COST, "MOVE AP 2 FAT 2 LEFT 7 MELEE BUDGET YES"));
        assert!(has_row(&verts, 1, PREVIEW_WARN, WARNING_LEAVING));
        assert!(has_row(&verts, 2, PREVIEW_WARN, &EDGE_ROW(4, -2)));
        assert!(has_row(&verts, 3, PREVIEW_NOTE, COMBINED_NOTE));
        assert!(!has_row(&verts, 4, PREVIEW_NOTE, COSTS_NOTE));
    }

    #[test]
    fn edge_warning_is_conditional_and_comes_from_the_rules_preview() {
        let state = negative_edge_fixture();
        let sc = hovering(&state, Some(0), Some(hex(-2, -2)), false);
        let plan = movement_preview(&sc).unwrap();
        assert_eq!(plan.first_edge_penalty, Some(hex(-2, -2)));
        let lines = movement_preview_lines(&plan);
        let edge = lines.iter().find(|l| l.0.contains("EDGE")).unwrap();
        assert_eq!(edge.0, EDGE_ROW(-2, -2));
        assert!(edge.0.contains("IF REACHED") && edge.0.contains("BREAKING") && edge.0.contains("NOT EXTRACTED"));
        assert_eq!(edge.1, PREVIEW_WARN);
        assert!(lines.iter().any(|l| l.0 == "! WARNING: LEAVING (-1,-2) - 3 CONTROLLERS"));

        // The rows follow the rules field: no edge text without it, and a different
        // hex shows that hex.
        let mut plan = plan;
        plan.first_edge_penalty = None;
        assert!(!movement_preview_lines(&plan).iter().any(|l| l.0.contains("EDGE")));
        plan.first_edge_penalty = Some(hex(0, -4));
        assert!(movement_preview_lines(&plan).iter().any(|l| l.0 == EDGE_ROW(0, -4)));

        // Breaking mover and interior routes show nothing, whatever the edge.
        let mut breaking = negative_edge_fixture();
        breaking.units[0].morale = Morale::Breaking;
        let verts = build_scene(&hovering(&breaking, Some(0), Some(hex(-2, -2)), false));
        assert!(!has_row(&verts, 2, PREVIEW_WARN, &EDGE_ROW(-2, -2)));
        assert!(has_row(&verts, 1, PREVIEW_WARN, "! WARNING: LEAVING (-1,-2) - 3 CONTROLLERS"));
    }

    #[test]
    fn edge_warning_respects_the_preview_gates_and_ignores_the_z_preference() {
        let state = edge_fixture(true);
        let hover = Some(hex(4, -2));
        let shown = |state: &BattleState, selected, hover| movement_preview(&hovering(state, selected, hover, false)).is_some();
        assert!(shown(&state, Some(0), hover));
        assert!(!shown(&state, Some(0), None));
        assert!(!shown(&state, Some(0), Some(hex(3, -2))), "own hex");
        assert!(!shown(&state, Some(0), Some(hex(2, -1))), "attack target");
        assert!(!shown(&state, None, hover));
        assert!(!shown(&state, Some(3), hover), "enemy selection");
        let mut poor = state.clone();
        poor.units[0].ap = 1;
        let mut enemy_turn = state.clone();
        enemy_turn.queue = vec![3, 0];
        let mut over = state.clone();
        over.outcome = Some(Outcome::Victory);
        for hidden in [&poor, &enemy_turn, &over] {
            for z in [true, false] {
                let verts = build_scene(&hovering(hidden, Some(0), hover, z));
                assert!(preview_vertices(&verts).is_empty());
                assert!(!has_row(&verts, 2, PREVIEW_WARN, &EDGE_ROW(4, -2)));
            }
        }

        let (hash, rng) = (state.state_hash(), state.rng.clone());
        let on = build_scene(&hovering(&state, Some(0), hover, true));
        let off = build_scene(&hovering(&state, Some(0), hover, false));
        let (with_z, without_z) = (preview_vertices(&on), preview_vertices(&off));
        assert!(!with_z.is_empty() && with_z == without_z);
        assert!(has_row(&on, 2, PREVIEW_WARN, &EDGE_ROW(4, -2)) && has_row(&off, 2, PREVIEW_WARN, &EDGE_ROW(4, -2)));
        let again = build_scene(&hovering(&state, Some(0), hover, true));
        assert!(on.len() == again.len() && on.iter().zip(&again).all(|(a, b)| a.pos == b.pos && a.color == b.color), "scene is deterministic");
        assert_eq!((state.state_hash(), state.rng.clone()), (hash, rng));
    }

    #[test]
    fn combined_warning_text_fits_the_preview_box_clear_of_other_ui() {
        let (bx, by, bw, bh) = PREVIEW_BOX;
        let state = negative_edge_fixture();
        let log = vec!["line".to_string(); 8];
        let mut sc = hovering(&state, Some(0), Some(hex(-2, -2)), true);
        sc.log = &log;
        sc.error = Some("NOT ENOUGH AP");
        let verts = build_scene(&sc);
        let plan = movement_preview(&sc).unwrap();
        let lines = movement_preview_lines(&plan);
        assert_eq!(lines.len(), 4, "cost, reaction, edge, one combined note");

        // Every row is emitted exactly where laid out, inside its own 14px band and the box.
        let mut previous_bottom = by;
        for (i, (text, col)) in lines.iter().enumerate() {
            let (x, y) = (bx + 8.0, by + 6.0 + i as f32 * 16.0);
            let geometry = text_geometry(&verts, x, y, 2.0, *col, text).unwrap_or_else(|| panic!("row {i} missing: {text}"));
            let (min_x, max_x) = geometry.iter().fold((f32::MAX, 0.0f32), |m, p| (m.0.min(p.0), m.1.max(p.0)));
            let (min_y, max_y) = geometry.iter().fold((f32::MAX, 0.0f32), |m, p| (m.0.min(p.1), m.1.max(p.1)));
            assert!(min_x >= x - 0.01 && max_x <= x + Painter::text_width(2.0, text) + 0.01, "row {i} width");
            assert!(max_x <= bx + bw - 8.0, "row {i} reaches the box edge: {max_x}");
            assert!(min_y >= previous_bottom - 0.01 && max_y <= y + 14.0 + 0.01, "row {i} overlaps its neighbour");
            assert!(max_y <= by + bh - 4.0, "row {i} reaches the box bottom: {max_y}");
            previous_bottom = max_y;
        }

        // All preview geometry stays in the box, which clears the map, panel, error bar and controls.
        let eps = 0.01;
        for (x, y) in verts.iter().filter(|v| preview_colours().contains(&v.color)).map(to_px) {
            assert!(x >= bx - eps && x <= bx + bw + eps && y >= by - eps && y <= by + bh + eps, "({x},{y}) outside the box");
        }
        let map_bottom = map_hexes().iter().map(|h| layout::hex_to_pixel(*h).1 + HEX_SIZE).fold(0.0f32, f32::max);
        assert!(by > map_bottom && bx + bw < PANEL_X - 12.0 && by + bh < WIN_H - 60.0 && by + bh < WIN_H - 24.0);
        let error_top = verts.iter().filter(|v| v.color == [0.4, 0.05, 0.05, 1.0]).map(to_px).fold(f32::MAX, |m, p| m.min(p.1));
        assert!(error_top >= by + bh);
    }

    #[test]
    fn every_preview_row_character_has_a_supported_glyph() {
        let fallback = crate::font::glyph('\u{1}');
        let supported = |c: char| c == ' ' || crate::font::glyph(c) != fallback;
        for h in [hex(-4, 0), hex(0, -4), hex(4, -2), hex(-2, -2)] {
            for (threat, edge) in [(true, true), (true, false), (false, true), (false, false)] {
                let plan = MovePreview {
                    path: vec![h],
                    ap_cost: u32::MAX,
                    fatigue_cost: u32::MAX,
                    ap_remaining: u32::MAX,
                    attack_budget_available: threat,
                    first_threat: threat.then(|| bb_rules::rules::MoveThreat { departure: h, controllers: vec![1, 2] }),
                    first_edge_penalty: edge.then_some(h),
                };
                for (text, _) in movement_preview_lines(&plan) {
                    if let Some(c) = text.chars().find(|c| !supported(*c)) {
                        panic!("unsupported glyph {c:?} in {text:?} (threat {threat}, edge {edge})");
                    }
                }
            }
        }
    }

    #[test]
    fn every_possible_preview_row_fits_the_box_width() {
        let (bx, _, bw, _) = PREVIEW_BOX;
        let limit = bw - 16.0;
        for h in map_hexes() {
            for controllers in [vec![1], vec![1, 2, 3, 4, 5, 6]] {
                for (threat, edge) in [(true, true), (true, false), (false, true), (false, false)] {
                    let plan = MovePreview {
                        path: vec![h],
                        ap_cost: u32::MAX,
                        fatigue_cost: u32::MAX,
                        ap_remaining: u32::MAX,
                        attack_budget_available: edge,
                        first_threat: threat.then(|| bb_rules::rules::MoveThreat { departure: h, controllers: controllers.clone() }),
                        first_edge_penalty: edge.then_some(h),
                    };
                    let lines = movement_preview_lines(&plan);
                    assert!(lines.len() <= 4, "{threat} {edge}");
                    for (text, _) in &lines {
                        assert!(Painter::text_width(2.0, text) <= limit, "{text} is too wide at {h:?} from x {bx}");
                    }
                }
            }
        }
    }

    // --- End Round button and confirmation modal -----------------------------

    fn stock() -> BattleState {
        BattleState::new(1)
    }

    fn same(a: &Vertex, b: &Vertex) -> bool {
        a.pos == b.pos && a.color == b.color
    }

    fn contains_run(verts: &[Vertex], run: &[Vertex]) -> bool {
        !run.is_empty() && verts.windows(run.len()).any(|w| w.iter().zip(run).all(|(a, b)| same(a, b)))
    }

    fn text_run(x: f32, y: f32, scale: f32, col: Rgba, text: &str) -> Vec<Vertex> {
        let mut p = Painter { verts: Vec::new() };
        p.text(x, y, scale, col, text);
        p.verts
    }

    fn bounds(verts: impl Iterator<Item = (f32, f32)>) -> (f32, f32, f32, f32) {
        verts.fold((f32::MAX, f32::MAX, f32::MIN, f32::MIN), |b, p| (b.0.min(p.0), b.1.min(p.1), b.2.max(p.0), b.3.max(p.1)))
    }

    fn rect_bounds(r: Rect) -> (f32, f32, f32, f32) {
        (r.x, r.y, r.right(), r.bottom())
    }

    fn close(a: (f32, f32, f32, f32), b: (f32, f32, f32, f32)) -> bool {
        (a.0 - b.0).abs() < 0.01 && (a.1 - b.1).abs() < 0.01 && (a.2 - b.2).abs() < 0.01 && (a.3 - b.3).abs() < 0.01
    }

    fn button_run(r: Rect, fill: Rgba, label_col: Rgba) -> Vec<Vertex> {
        let mut p = Painter { verts: Vec::new() };
        p.button(r, fill, label_col, layout::END_ROUND_LABEL);
        p.verts
    }

    #[test]
    fn button_is_drawn_exactly_on_the_shared_hit_rectangle_and_reflects_availability() {
        let state = stock();
        let enabled = build_scene(&scene(&state, Some(0), false));
        assert!(contains_run(&enabled, &button_run(layout::END_ROUND_BUTTON, BUTTON_ENABLED, WHITE)));
        assert!(!enabled.iter().any(|v| v.color == BUTTON_DISABLED));
        let border: Vec<_> = enabled.iter().filter(|v| v.color == BUTTON_BORDER).map(to_px).filter(|p| p.1 > 600.0 && p.0 < layout::RETREAT_BUTTON.x).collect();
        assert!(close(bounds(border.into_iter()), rect_bounds(layout::END_ROUND_BUTTON)), "drawn border equals the hit area");

        let mut enemy_turn = stock();
        enemy_turn.queue = vec![3, 0];
        let mut over = stock();
        over.outcome = Some(Outcome::Victory);
        let mut dead = stock();
        dead.units[0].alive = false;
        let disabled_cases = [
            (&state, None),
            (&state, Some(3)),
            (&enemy_turn, Some(0)),
            (&enemy_turn, Some(3)),
            (&over, Some(0)),
            (&dead, Some(0)),
        ];
        for (st, selected) in disabled_cases {
            let verts = build_scene(&scene(st, selected, false));
            assert!(contains_run(&verts, &button_run(layout::END_ROUND_BUTTON, BUTTON_DISABLED, BUTTON_DISABLED_TEXT)), "{selected:?}");
            assert!(!verts.iter().any(|v| v.color == BUTTON_ENABLED), "{selected:?}");
        }
    }

    #[test]
    fn button_label_stays_inside_the_button_and_clear_of_other_ui() {
        let r = layout::END_ROUND_BUTTON;
        let run = button_run(r, BUTTON_ENABLED, WHITE);
        let (x0, y0, x1, y1) = bounds(run.iter().map(to_px));
        assert!(close((x0, y0, x1, y1), rect_bounds(r)));
        let label: Vec<_> = run.iter().filter(|v| v.color == WHITE).map(to_px).collect();
        assert!(!label.is_empty());
        let (lx0, ly0, lx1, ly1) = bounds(label.into_iter());
        assert!(lx0 >= r.x + 4.0 && lx1 <= r.right() - 4.0 && ly0 >= r.y + 4.0 && ly1 <= r.bottom() - 4.0);

        // Fully loaded scene: long log, error bar, ZoC legend, hover preview.
        let state = lane_fixture();
        let log = vec!["a log line of thirty-one chars".to_string(); 8];
        let mut sc = hovering(&state, Some(0), Some(hex(2, -2)), true);
        sc.log = &log;
        sc.error = Some("NOT ENOUGH AP");
        let verts = build_scene(&sc);
        let own = button_run(r, BUTTON_ENABLED, WHITE);
        let start = verts.windows(own.len()).position(|w| w.iter().zip(&own).all(|(a, b)| same(a, b))).expect("button drawn");
        let background = [BG, [0.12, 0.12, 0.15, 1.0]];
        for (i, v) in verts.iter().enumerate() {
            if (start..start + own.len()).contains(&i) || background.contains(&v.color) {
                continue;
            }
            let (x, y) = to_px(v);
            let inside = x > r.x - 0.01 && x < r.right() + 0.01 && y > r.y - 0.01 && y < r.bottom() + 0.01;
            assert!(!inside, "vertex {i} ({x},{y}) colour {:?} overlaps the launch button", v.color);
        }
    }

    #[test]
    fn dialog_is_drawn_last_with_the_documented_text_buttons_and_count() {
        let state = stock();
        let hash = state.state_hash();
        let rng = state.rng.clone();
        let closed = build_scene(&scene(&state, Some(0), false));
        let open = build_scene(&Scene { confirm: Some(ConfirmAction::EndRound), ..scene(&state, Some(0), false) });
        assert!(open.len() > closed.len());
        assert!(open.iter().zip(&closed).all(|(a, b)| same(a, b)), "the scene below the modal is unchanged");

        let mut dialog = Painter { verts: Vec::new() };
        draw_dialog(&mut dialog, &state, ConfirmAction::EndRound);
        assert!(open.len() >= dialog.verts.len() && contains_run(&open[open.len() - dialog.verts.len()..], &dialog.verts), "modal is the last thing drawn");

        // Text rows including the queued count (P1, P2, P3 at the start).
        let lines = end_round_dialog_lines(3);
        let joined: Vec<&str> = lines.iter().map(|l| l.2.as_str()).collect();
        assert!(joined.contains(&"PLAYER UNITS STILL QUEUED: 3"));
        assert!(joined.iter().any(|l| l.contains("FORFEITS")));
        assert!(joined.iter().any(|l| l.contains("ENEMIES STILL TAKE THEIR TURNS")));
        for (i, (scale, col, line)) in lines.iter().enumerate() {
            let y = DIALOG_TEXT_Y + if i == 0 { 0.0 } else { 16.0 } + i as f32 * DIALOG_ROW_STEP;
            assert!(contains_run(&open, &text_run(DIALOG_TEXT_X, y, *scale, *col, line)), "row {i}: {line}");
        }
        assert!(contains_run(&open, &button_run_labelled(layout::CONFIRM_BUTTON, CONFIRM_FILL, "CONFIRM (ENTER)")));
        assert!(contains_run(&open, &button_run_labelled(layout::CANCEL_BUTTON, CANCEL_FILL, "CANCEL (ESC)")));

        // The count follows the queue: a Wait-deferred player is counted, a finished one is not.
        let mut deferred = stock();
        deferred.queue = vec![1, 2, 3, 4, 5, 0];
        assert_eq!(queued_player_units(&deferred), 3);
        let mut finished = stock();
        finished.queue = vec![2, 3, 4, 5];
        assert_eq!(queued_player_units(&finished), 1);
        let verts = build_scene(&Scene { confirm: Some(ConfirmAction::EndRound), ..scene(&finished, Some(2), false) });
        let row = DIALOG_TEXT_Y + 16.0 + DIALOG_ROW_STEP;
        assert!(contains_run(&verts, &text_run(DIALOG_TEXT_X, row, 2.0, WHITE, "PLAYER UNITS STILL QUEUED: 1")));
        assert!(!contains_run(&verts, &text_run(DIALOG_TEXT_X, row, 2.0, WHITE, "PLAYER UNITS STILL QUEUED: 3")));

        assert_eq!(state.state_hash(), hash);
        assert_eq!(state.rng, rng);
    }

    fn button_run_labelled(r: Rect, fill: Rgba, label: &str) -> Vec<Vertex> {
        let mut p = Painter { verts: Vec::new() };
        p.button(r, fill, WHITE, label);
        p.verts
    }

    #[test]
    fn dialog_geometry_and_text_stay_inside_the_window_dialog_and_buttons() {
        let state = stock();
        let verts = build_scene(&Scene { confirm: Some(ConfirmAction::EndRound), ..scene(&state, Some(0), false) });
        for v in &verts {
            assert!(v.pos[0] >= -1.0001 && v.pos[0] <= 1.0001 && v.pos[1] >= -1.0001 && v.pos[1] <= 1.0001, "vertex outside the window");
        }
        let mut dialog = Painter { verts: Vec::new() };
        draw_dialog(&mut dialog, &state, ConfirmAction::EndRound);
        let d = layout::DIALOG;
        let frame = bounds(dialog.verts.iter().map(to_px));
        assert!(close(frame, (d.x - 4.0, d.y - 4.0, d.right() + 4.0, d.bottom() + 4.0)));

        // Text sits inside the dialog with margin and above the buttons.
        let text_colours = [YELLOW, WHITE, PREVIEW_NOTE];
        let dialog_text = dialog.verts.iter().filter(|v| text_colours.contains(&v.color)).map(to_px).filter(|p| p.1 < layout::CONFIRM_BUTTON.y);
        let (x0, y0, x1, y1) = bounds(dialog_text);
        assert!(x0 >= d.x + 16.0 && x1 <= d.right() - 16.0 && y0 >= d.y + 8.0 && y1 <= layout::CONFIRM_BUTTON.y - 8.0, "{x0},{y0},{x1},{y1}");

        for (r, fill, label) in [(layout::CONFIRM_BUTTON, CONFIRM_FILL, "CONFIRM (ENTER)"), (layout::CANCEL_BUTTON, CANCEL_FILL, "CANCEL (ESC)")] {
            let run = button_run_labelled(r, fill, label);
            assert!(close(bounds(run.iter().map(to_px)), rect_bounds(r)));
            let text = run.iter().filter(|v| v.color == WHITE).map(to_px);
            let (lx0, ly0, lx1, ly1) = bounds(text);
            assert!(lx0 >= r.x + 4.0 && lx1 <= r.right() - 4.0 && ly0 >= r.y + 4.0 && ly1 <= r.bottom() - 4.0, "{label} label overflows");
        }
    }

    // --- Retreat button and confirmation modal ---------------------------------

    fn retreat_button_run(fill: Rgba, label_col: Rgba) -> Vec<Vertex> {
        button_run_labelled_with(layout::RETREAT_BUTTON, fill, label_col, layout::RETREAT_LABEL)
    }

    fn button_run_labelled_with(r: Rect, fill: Rgba, label_col: Rgba, label: &str) -> Vec<Vertex> {
        let mut p = Painter { verts: Vec::new() };
        p.button(r, fill, label_col, label);
        p.verts
    }

    #[test]
    fn retreat_button_is_drawn_on_the_shared_hit_rectangle_and_matches_end_round_availability() {
        let state = stock();
        let enabled = build_scene(&scene(&state, Some(0), false));
        assert!(contains_run(&enabled, &retreat_button_run(BUTTON_ENABLED, WHITE)));
        assert!(contains_run(&enabled, &button_run(layout::END_ROUND_BUTTON, BUTTON_ENABLED, WHITE)), "End Round unchanged");
        let border: Vec<_> = enabled.iter().filter(|v| v.color == BUTTON_BORDER).map(to_px).filter(|p| p.1 > 600.0 && p.0 >= layout::RETREAT_BUTTON.x).collect();
        assert!(close(bounds(border.into_iter()), rect_bounds(layout::RETREAT_BUTTON)), "drawn border equals the hit area");

        let mut enemy_turn = stock();
        enemy_turn.queue = vec![3, 0];
        let mut over = stock();
        over.outcome = Some(Outcome::Victory);
        let mut dead = stock();
        dead.units[0].alive = false;
        let mut fleeing = stock();
        fleeing.units[0].morale = Morale::Fleeing;
        let mut retreating = stock();
        retreating.units[0].retreating = true;
        let mut escaped = stock();
        escaped.units[0].fled = true;
        let cases = [
            (&state, None),
            (&state, Some(3)),
            (&state, Some(1)),
            (&enemy_turn, Some(0)),
            (&enemy_turn, Some(3)),
            (&over, Some(0)),
            (&dead, Some(0)),
            (&fleeing, Some(0)),
            (&retreating, Some(0)),
            (&escaped, Some(0)),
        ];
        for (st, selected) in cases {
            let sc = scene(st, selected, false);
            assert!(!retreat_enabled(&sc), "{selected:?}");
            let verts = build_scene(&sc);
            assert!(contains_run(&verts, &retreat_button_run(BUTTON_DISABLED, BUTTON_DISABLED_TEXT)), "{selected:?}");
            assert!(contains_run(&verts, &button_run(layout::END_ROUND_BUTTON, BUTTON_DISABLED, BUTTON_DISABLED_TEXT)), "{selected:?}");
            assert!(!verts.iter().any(|v| v.color == BUTTON_ENABLED), "{selected:?}");
        }
        assert!(retreat_enabled(&scene(&state, Some(0), false)));
        let mut second = stock();
        second.queue = vec![1, 0];
        assert!(retreat_enabled(&scene(&second, Some(1), false)));
    }

    #[test]
    fn retreat_button_label_stays_inside_the_button_and_clear_of_other_ui() {
        let r = layout::RETREAT_BUTTON;
        let run = retreat_button_run(BUTTON_ENABLED, WHITE);
        assert!(close(bounds(run.iter().map(to_px)), rect_bounds(r)));
        let label: Vec<_> = run.iter().filter(|v| v.color == WHITE).map(to_px).collect();
        let (lx0, ly0, lx1, ly1) = bounds(label.into_iter());
        assert!(lx0 >= r.x + 4.0 && lx1 <= r.right() - 4.0 && ly0 >= r.y + 4.0 && ly1 <= r.bottom() - 4.0);

        // Fully loaded scene: long log, error bar, ZoC legend, hover preview.
        let state = lane_fixture();
        let log = vec!["a log line of thirty-one chars".to_string(); 8];
        let mut sc = hovering(&state, Some(0), Some(hex(2, -2)), true);
        sc.log = &log;
        sc.error = Some("NOT ENOUGH AP");
        let verts = build_scene(&sc);
        let start = verts.windows(run.len()).position(|w| w.iter().zip(&run).all(|(a, b)| same(a, b))).expect("button drawn");
        let background = [BG, [0.12, 0.12, 0.15, 1.0]];
        let end_round = button_run(layout::END_ROUND_BUTTON, BUTTON_ENABLED, WHITE);
        let end_start = verts.windows(end_round.len()).position(|w| w.iter().zip(&end_round).all(|(a, b)| same(a, b))).expect("End Round drawn");
        for (i, v) in verts.iter().enumerate() {
            if (start..start + run.len()).contains(&i) || (end_start..end_start + end_round.len()).contains(&i) || background.contains(&v.color) {
                continue;
            }
            let (x, y) = to_px(v);
            let inside = |r: Rect| x > r.x - 0.01 && x < r.right() + 0.01 && y > r.y - 0.01 && y < r.bottom() + 0.01;
            assert!(!inside(r) && !inside(layout::END_ROUND_BUTTON), "vertex {i} ({x},{y}) colour {:?} overlaps a launch button", v.color);
        }
        // The shortcut hint stays left of the panel (and so of both buttons).
        assert!(16.0 + Painter::text_width(2.0, SHORTCUT_HINT) < PANEL_X - 12.0);
    }

    #[test]
    fn party_count_includes_fleeing_and_finished_units_but_not_dead_or_escaped() {
        let mut state = stock();
        assert_eq!(present_player_units(&state), 3);
        state.queue = vec![2, 3, 4, 5]; // P1 and P2 have finished their turns
        assert_eq!(present_player_units(&state), 3);
        state.units[1].morale = Morale::Fleeing;
        assert_eq!(present_player_units(&state), 3, "Fleeing units are counted");
        state.units[2].retreating = true;
        assert_eq!(present_player_units(&state), 3);
        state.units[2].alive = false;
        assert_eq!(present_player_units(&state), 2, "dead units are not counted");
        state.units[1].fled = true;
        assert_eq!(present_player_units(&state), 1, "escaped units are not counted");
        state.units[0].alive = false;
        assert_eq!(present_player_units(&state), 0);
        // Enemies never count.
        assert_eq!(present_player_units(&stock()), stock().units.iter().filter(|u| u.side == Side::Player).count());
    }

    #[test]
    fn retreat_dialog_states_the_implemented_consequences_and_the_count_only() {
        let lines = retreat_dialog_lines(3);
        let joined: Vec<&str> = lines.iter().map(|l| l.2.as_str()).collect();
        assert_eq!(joined[0], "RETREAT?");
        assert!(joined.contains(&"PLAYER UNITS PRESENT: 3"));
        let all = joined.join(" ");
        for needle in ["ALL PRESENT PLAYER UNITS WILL WITHDRAW", "MAP EDGE ESCAPE IMMEDIATELY", "MOVE AUTOMATICALLY AND MAY DIE", "CANNOT BE CANCELLED AFTER CONFIRMING"] {
            assert!(all.contains(needle), "{needle}");
        }
        for banned in ["RENOWN", "BONUS", "DEFENSE", "DEFENCE", "PENALT"] {
            assert!(!all.contains(banned), "unimplemented consequence advertised: {banned}");
        }

        // Count follows the party, not the queue: finished and Fleeing units are included.
        let mut state = stock();
        state.queue = vec![2, 3, 4, 5];
        state.units[1].morale = Morale::Fleeing;
        state.units[2].alive = false;
        let verts = build_scene(&Scene { confirm: Some(ConfirmAction::Retreat), ..scene(&state, None, false) });
        let row = DIALOG_TEXT_Y + 16.0 + DIALOG_ROW_STEP;
        assert!(contains_run(&verts, &text_run(DIALOG_TEXT_X, row, 2.0, WHITE, "PLAYER UNITS PRESENT: 2")));
        // It is not the End Round dialog.
        assert!(!contains_run(&verts, &text_run(DIALOG_TEXT_X, 194.0, 3.0, YELLOW, "END ROUND?")));
        assert!(contains_run(&verts, &text_run(DIALOG_TEXT_X, 194.0, 3.0, YELLOW, "RETREAT?")));
        assert!(contains_run(&verts, &button_run_labelled(layout::CONFIRM_BUTTON, CONFIRM_FILL, "CONFIRM (ENTER)")));
        assert!(contains_run(&verts, &button_run_labelled(layout::CANCEL_BUTTON, CANCEL_FILL, "CANCEL (ESC)")));
        // And the End Round dialog is unaffected.
        let end_round = build_scene(&Scene { confirm: Some(ConfirmAction::EndRound), ..scene(&state, None, false) });
        assert!(contains_run(&end_round, &text_run(DIALOG_TEXT_X, 194.0, 3.0, YELLOW, "END ROUND?")));
        assert!(!contains_run(&end_round, &text_run(DIALOG_TEXT_X, 194.0, 3.0, YELLOW, "RETREAT?")));
    }

    #[test]
    fn retreat_dialog_is_drawn_last_pure_and_inside_the_window_dialog_and_buttons() {
        let state = stock();
        let hash = state.state_hash();
        let rng = state.rng.clone();
        let closed = build_scene(&scene(&state, Some(0), false));
        let open = build_scene(&Scene { confirm: Some(ConfirmAction::Retreat), ..scene(&state, Some(0), false) });
        assert!(open.len() > closed.len());
        assert!(open.iter().zip(&closed).all(|(a, b)| same(a, b)), "the scene below the modal is unchanged");
        let mut dialog = Painter { verts: Vec::new() };
        draw_dialog(&mut dialog, &state, ConfirmAction::Retreat);
        assert_eq!(open.len() - closed.len(), dialog.verts.len());
        assert!(contains_run(&open[open.len() - dialog.verts.len()..], &dialog.verts), "modal is the last thing drawn");
        // The modal background is fully opaque.
        assert_eq!(DIALOG_BG[3], 1.0);
        for v in &open {
            assert!(v.pos[0] >= -1.0001 && v.pos[0] <= 1.0001 && v.pos[1] >= -1.0001 && v.pos[1] <= 1.0001, "vertex outside the window");
        }

        let d = layout::DIALOG;
        assert!(close(bounds(dialog.verts.iter().map(to_px)), (d.x - 4.0, d.y - 4.0, d.right() + 4.0, d.bottom() + 4.0)));
        let text_colours = [YELLOW, WHITE, PREVIEW_NOTE];
        let dialog_text = dialog.verts.iter().filter(|v| text_colours.contains(&v.color)).map(to_px).filter(|p| p.1 < layout::CONFIRM_BUTTON.y);
        let (x0, y0, x1, y1) = bounds(dialog_text);
        assert!(x0 >= d.x + 16.0 && x1 <= d.right() - 16.0 && y0 >= d.y + 8.0 && y1 <= layout::CONFIRM_BUTTON.y - 8.0, "{x0},{y0},{x1},{y1}");
        // Every row is drawn (none pushed under the buttons or clipped away).
        for (i, (scale, col, line)) in retreat_dialog_lines(3).iter().enumerate() {
            let y = DIALOG_TEXT_Y + if i == 0 { 0.0 } else { 16.0 } + i as f32 * DIALOG_ROW_STEP;
            assert!(contains_run(&open, &text_run(DIALOG_TEXT_X, y, *scale, *col, line)), "row {i}: {line}");
            assert!(DIALOG_TEXT_X + Painter::text_width(*scale, line) <= d.right() - 16.0, "row {i} too wide");
        }

        assert_eq!(state.state_hash(), hash);
        assert_eq!(state.rng, rng);
    }

    // --- Fleeing and escaped units ----------------------------------------------

    const FLED_ROW: Rgba = FLED;

    #[test]
    fn fled_rows_are_distinct_from_dead_and_escapees_vanish_from_tokens_and_counts() {
        let mut state = BattleState::new(23);
        state.units[1].alive = false; // P2 dead
        state.units[2].fled = true; // P3 escaped alive
        let verts = build_scene(&scene(&state, Some(0), false));
        assert!(text_geometry(&verts, PANEL_X, panel_row_y(1), 2.0, DIM, "P2 DEAD").is_some());
        assert!(text_geometry(&verts, PANEL_X, panel_row_y(2), 2.0, FLED_ROW, "P3 FLED").is_some());
        assert!(text_geometry(&verts, PANEL_X, panel_row_y(2), 2.0, DIM, "P3 DEAD").is_none());
        assert!(text_geometry(&verts, PANEL_X, panel_row_y(1), 2.0, FLED_ROW, "P2 FLED").is_none());
        // No morale label for either, and the escapee keeps its HP row.
        for id in [1, 2] {
            let x = morale_label_x(&state, id);
            assert!(text_geometry(&verts, x, panel_row_y(id), 2.0, WHITE, &label_text(Morale::Steady)).is_none());
        }
        assert!(text_geometry(&verts, PANEL_X, panel_row_y(2) + 18.0, 2.0, FLED_ROW, "HP 60/60 ARM 40/40").is_some());

        // Tokens: present units draw their name on the map, the escapee does not.
        let token = |u: &bb_rules::Unit| {
            let c = layout::hex_to_pixel(u.pos);
            text_geometry(&verts, c.0 - 12.0, c.1 - 7.0, 2.0, WHITE, u.name).is_some()
        };
        assert!(token(&state.units[0]));
        assert!(!token(&state.units[2]));
        assert!(!token(&state.units[1]));
        let mut present = state.clone();
        present.units[2].fled = false;
        let verts = build_scene(&scene(&present, Some(0), false));
        let c = layout::hex_to_pixel(present.units[2].pos);
        assert!(text_geometry(&verts, c.0 - 12.0, c.1 - 7.0, 2.0, WHITE, "P3").is_some(), "control: a present unit has a token");

        assert_eq!(queued_player_units(&state), 1, "only P1 is present and queued");
        state.queue = vec![0, 1, 2];
        assert_eq!(queued_player_units(&state), 1, "dead and escaped units are not counted even if queued");
    }

    #[test]
    fn escapees_are_not_hover_targets() {
        let mut state = BattleState::new(23);
        state.units[0].pos = hex(1, 0);
        state.units[3].pos = hex(2, 0);
        let hovered = |state: &BattleState| {
            build_scene(&hovering(state, Some(0), Some(hex(2, 0)), false)).iter().filter(|v| v.color == RED).count()
        };
        let present = hovered(&state);
        let label = |state: &BattleState| {
            let c = layout::hex_to_pixel(hex(2, 0));
            let verts = build_scene(&hovering(state, Some(0), Some(hex(2, 0)), false));
            text_geometry(&verts, c.0 - 36.0, c.1 - 46.0, 2.0, YELLOW, "HIT 55%").is_some()
        };
        assert!(label(&state), "control: the present enemy shows its hit chance");
        state.units[3].fled = true;
        assert!(!label(&state));
        assert!(hovered(&state) < present, "the red target highlight is gone");
    }

    #[test]
    fn fleeing_players_are_not_selectable_for_orders_or_the_end_round_button() {
        let mut state = BattleState::new(23);
        state.units[0].morale = Morale::Fleeing;
        let sc = hovering(&state, Some(0), Some(hex(-3, 2)), true);
        assert!(!end_round_enabled(&sc));
        assert!(movement_preview(&sc).is_none());
        assert!(control_overlay_hexes(&sc).is_empty());
        let verts = build_scene(&sc);
        assert!(contains_run(&verts, &button_run(layout::END_ROUND_BUTTON, BUTTON_DISABLED, BUTTON_DISABLED_TEXT)));
        assert!(!verts.iter().any(|v| v.color == BUTTON_ENABLED));
        assert_eq!(queued_player_units(&state), 2);
        // The panel shows the label with its percentage.
        let x = morale_label_x(&state, 0);
        assert!(text_geometry(&verts, x, panel_row_y(0), 2.0, FLEEING, "MORALE FLEEING 70%").is_some());
    }

    #[test]
    fn retreating_players_are_not_selectable_for_orders_previews_or_hit_labels() {
        let mut state = BattleState::new(23);
        state.units[0].retreating = true;
        let sc = hovering(&state, Some(0), Some(hex(-3, 2)), true);
        assert!(!end_round_enabled(&sc));
        assert!(movement_preview(&sc).is_none());
        assert!(control_overlay_hexes(&sc).is_empty());
        let verts = build_scene(&sc);
        assert!(contains_run(&verts, &button_run(layout::END_ROUND_BUTTON, BUTTON_DISABLED, BUTTON_DISABLED_TEXT)));
        assert!(!verts.iter().any(|v| v.color == BUTTON_ENABLED));
        assert_eq!(queued_player_units(&state), 2, "the retreating player is not counted");

        // Control: the same selection works without the order.
        state.units[0].retreating = false;
        let sc = hovering(&state, Some(0), Some(hex(-3, 2)), true);
        assert!(end_round_enabled(&sc));
        assert_eq!(queued_player_units(&state), 3);

        // The hit label never shows for a retreating selection.
        let mut state = BattleState::new(23);
        state.units[0].pos = hex(1, 0);
        state.units[3].pos = hex(2, 0);
        let label = |state: &BattleState| {
            let c = layout::hex_to_pixel(hex(2, 0));
            let verts = build_scene(&hovering(state, Some(0), Some(hex(2, 0)), false));
            text_geometry(&verts, c.0 - 36.0, c.1 - 46.0, 2.0, YELLOW, "HIT 55%").is_some()
        };
        assert!(label(&state), "control");
        state.units[0].retreating = true;
        assert!(!label(&state));
    }

    #[test]
    fn retreat_banner_and_escape_note() {
        let mut state = BattleState::new(23);
        state.outcome = Some(Outcome::Retreat);
        state.end_reason = Some(EndReason::Escape);
        let verts = build_scene(&scene(&state, None, false));
        let (bx, by, bw, bh) = BANNER_BOX;
        let (hx, hy, rx, ry) = (bx + 16.0, by + 8.0, bx + 254.0, by + 14.0);
        assert!(contains_run(&verts, &text_run(hx, hy, 5.0, YELLOW, "RETREAT")));
        assert!(!contains_run(&verts, &text_run(hx, hy, 5.0, RED, "DEFEAT")));
        // The texts stay inside the banner box and the headline ends left of the reason.
        assert!(hx + Painter::text_width(5.0, "RETREAT") < rx);
        assert!(hy + 35.0 < by + bh);
        assert!(contains_run(&verts, &text_run(rx, ry, 2.0, WHITE, "UNITS ESCAPED")));
        assert!(rx + Painter::text_width(2.0, "UNITS ESCAPED") < bx + bw);
        assert!(contains_run(&verts, &text_run(rx, by + 40.0, 2.0, WHITE, "R RESTART  N NEW SEED")));
        assert!(by + 40.0 + 14.0 < by + bh && rx + Painter::text_width(2.0, "R RESTART  N NEW SEED") < bx + bw);

        state.outcome = Some(Outcome::Victory);
        let verts = build_scene(&scene(&state, None, false));
        assert!(contains_run(&verts, &text_run(hx, hy, 5.0, GREEN, "VICTORY")));
        assert!(contains_run(&verts, &text_run(rx, ry, 2.0, WHITE, "UNITS ESCAPED")));
        state.end_reason = Some(EndReason::Elimination);
        let verts = build_scene(&scene(&state, None, false));
        assert!(!contains_run(&verts, &text_run(rx, ry, 2.0, WHITE, "UNITS ESCAPED")));
    }

    #[test]
    fn shortcut_hint_lists_end_round_and_retreat() {
        let state = stock();
        let verts = build_scene(&scene(&state, Some(0), false));
        let hint = "E END TURN  W WAIT  X END ROUND  V RETREAT  R RESTART  N SEED  ESC QUIT";
        assert_eq!(hint, SHORTCUT_HINT);
        assert!(contains_run(&verts, &text_run(16.0, WIN_H - 24.0, 2.0, DIM, hint)));
        assert!(16.0 + Painter::text_width(2.0, hint) < layout::PANEL_X - 12.0, "hint reaches the side panel");
    }

    // --- Height overlay ---------------------------------------------------------

    const AP_LABEL: Rgba = [0.05, 0.2, 0.05, 1.0];

    fn heights_on<'a>(state: &'a BattleState, selected: Option<u32>) -> Scene<'a> {
        Scene { height_overlay: true, ..scene(state, selected, false) }
    }

    fn badge_run(at: Hex, height: u8) -> Vec<Vertex> {
        let mut p = Painter { verts: Vec::new() };
        p.height_badge(layout::hex_to_pixel(at), height);
        p.verts
    }

    fn count_color(verts: &[Vertex], col: Rgba) -> usize {
        verts.iter().filter(|v| v.color == col).count()
    }

    /// Rules fixture on the Hills preset: only P1 (id 0) and E1 (id 3) are alive.
    fn hills_pair(p1: Hex, e1: Hex) -> BattleState {
        let mut state = bb_rules::scenario::battle(23, bb_rules::scenario::TerrainPreset::Hills);
        for unit in &mut state.units {
            unit.alive = false;
        }
        state.units[0].alive = true;
        state.units[0].pos = p1;
        state.units[0].ap = 9;
        state.units[3].alive = true;
        state.units[3].pos = e1;
        state.queue = vec![0];
        state
    }

    #[test]
    fn badge_shows_each_height_zero_to_three_and_only_that_digit() {
        let at = hex(2, 1);
        let mut state = stock();
        for h in 0..=3u8 {
            state.elevations.set(at, h).unwrap();
            let verts = build_scene(&heights_on(&state, None));
            assert!(contains_run(&verts, &badge_run(at, h)), "height {h}");
            for other in (0..=3u8).filter(|&o| o != h) {
                assert!(!contains_run(&verts, &badge_run(at, other)), "height {h} drew {other}");
            }
        }
    }

    #[test]
    fn badge_follows_live_elevation_changes() {
        let at = hex(-2, -1);
        let mut state = stock();
        let off = build_scene(&heights_on(&state, None));
        assert!(contains_run(&off, &badge_run(at, 0)));
        state.elevations.set(at, 3).unwrap();
        let raised = build_scene(&heights_on(&state, None));
        assert!(contains_run(&raised, &badge_run(at, 3)) && !contains_run(&raised, &badge_run(at, 0)));
        state.elevations.set(at, 1).unwrap();
        let lowered = build_scene(&heights_on(&state, None));
        assert!(contains_run(&lowered, &badge_run(at, 1)) && !contains_run(&lowered, &badge_run(at, 3)));
    }

    #[test]
    fn every_map_hex_gets_a_badge_including_occupied_and_blocked_ones() {
        let state = bb_rules::scenario::battle(1, bb_rules::scenario::TerrainPreset::Hills);
        let verts = build_scene(&heights_on(&state, Some(0)));
        let mut occupied = 0;
        let mut blocked = 0;
        for h in map_hexes() {
            let height = state.elevations.get(h).unwrap();
            assert!(contains_run(&verts, &badge_run(h, height)), "{h:?} height {height}");
            occupied += state.units.iter().any(|u| u.pos == h) as usize;
            blocked += (h.is_obstacle() && height > 0) as usize;
        }
        assert!(occupied >= 6 && blocked >= 1, "fixture covers occupied and blocked hexes");
        // Exactly one filled badge rectangle (6 vertices) per map hex on top of the legend text.
        let off = build_scene(&scene(&state, Some(0), false));
        assert_eq!(count_color(&verts, HEIGHT_BADGE) - count_color(&off, HEIGHT_BADGE), 61 * 6);
        assert_eq!(count_color(&verts, HEIGHT_BADGE_GLYPH), map_hexes().iter().map(|&h| badge_run(h, state.elevations.get(h).unwrap()).iter().filter(|v| v.color == HEIGHT_BADGE_GLYPH).count()).sum::<usize>());
    }

    #[test]
    fn badges_are_hidden_when_the_preference_is_off() {
        let state = bb_rules::scenario::battle(1, bb_rules::scenario::TerrainPreset::Hills);
        let off = build_scene(&scene(&state, Some(0), false));
        assert_eq!(count_color(&off, HEIGHT_BADGE_GLYPH), 0);
        for h in map_hexes() {
            assert!(!contains_run(&off, &badge_run(h, state.elevations.get(h).unwrap())), "{h:?}");
        }
    }

    #[test]
    fn badges_stay_during_enemy_turns_after_the_battle_and_under_the_modal() {
        let mut state = bb_rules::scenario::battle(1, bb_rules::scenario::TerrainPreset::Hills);
        let all_drawn = |verts: &[Vertex], state: &BattleState| {
            map_hexes().into_iter().all(|h| contains_run(verts, &badge_run(h, state.elevations.get(h).unwrap())))
        };
        state.queue = vec![3, 0];
        assert!(all_drawn(&build_scene(&heights_on(&state, None)), &state), "enemy turn");
        state.queue = vec![0];
        state.outcome = Some(Outcome::Defeat);
        assert!(all_drawn(&build_scene(&heights_on(&state, Some(0))), &state), "battle over");
        let state = bb_rules::scenario::battle(1, bb_rules::scenario::TerrainPreset::Hills);
        let modal = build_scene(&Scene { confirm: Some(ConfirmAction::EndRound), ..heights_on(&state, Some(0)) });
        assert!(all_drawn(&modal, &state), "modal open");
        // The modal is still the last thing drawn.
        let mut p = Painter { verts: Vec::new() };
        draw_dialog(&mut p, &state, ConfirmAction::EndRound);
        let tail = &modal[modal.len() - p.verts.len()..];
        assert!(tail.iter().zip(&p.verts).all(|(a, b)| same(a, b)));
    }

    #[test]
    fn height_overlay_scene_is_pure_and_independent_of_the_zoc_overlay() {
        let state = bb_rules::scenario::battle(23, bb_rules::scenario::TerrainPreset::Hills);
        let (hash, rng, elevations) = (state.state_hash(), state.rng.clone(), state.elevations.clone());
        let a = build_scene(&heights_on(&state, Some(0)));
        let b = build_scene(&heights_on(&state, Some(0)));
        assert!(a.len() == b.len() && a.iter().zip(&b).all(|(x, y)| same(x, y)), "deterministic");
        assert_eq!((state.state_hash(), &state.rng, &state.elevations), (hash, &rng, &elevations));
        // Z adds only ZoC markers; the badges are untouched by it.
        let with_zoc = build_scene(&Scene { control_overlay: true, ..heights_on(&state, Some(0)) });
        for h in map_hexes() {
            assert!(contains_run(&with_zoc, &badge_run(h, state.elevations.get(h).unwrap())));
        }
        assert_eq!(count_color(&with_zoc, HEIGHT_BADGE_GLYPH), count_color(&a, HEIGHT_BADGE_GLYPH));
        assert_eq!(count_color(&a, CONTROL_MARKER), 0);
    }

    #[test]
    fn height_text_uses_only_supported_glyphs() {
        let fallback = crate::font::glyph('\u{1}');
        let supported = |c: char| c == ' ' || crate::font::glyph(c) != fallback;
        for on in [false, true] {
            for (text, _) in height_legend_lines(on) {
                assert!(text.chars().all(supported), "{text:?}");
            }
        }
        for digit in 0..=3u8 {
            assert!(digit.to_string().chars().all(supported));
        }
    }

    #[test]
    fn legend_shows_the_h_shortcut_with_its_state_in_free_space() {
        let state = stock();
        for on in [false, true] {
            let verts = build_scene(&Scene { height_overlay: on, ..scene(&state, Some(0), false) });
            let (x, y, step) = HEIGHT_LEGEND;
            let lines = height_legend_lines(on);
            assert!(lines[0].0.starts_with("H "));
            assert!(lines[0].0.ends_with(if on { "ON" } else { "OFF" }));
            let mut rows = Vec::new();
            for (i, (text, col)) in lines.iter().enumerate() {
                let run = text_run(x, y + i as f32 * step, 2.0, *col, text);
                assert!(contains_run(&verts, &run), "legend row {i}");
                rows.push(bounds(run.iter().map(to_px)));
            }
            // Rows are disjoint, above the map's top row (y 97) and left of the side panel.
            for (i, r) in rows.iter().enumerate() {
                assert!(r.0 >= 0.0 && r.1 >= 40.0 && r.3 < 97.0 && r.2 < PANEL_X - 12.0, "{r:?}");
                if i > 0 {
                    assert!(r.1 > rows[i - 1].3, "rows overlap");
                }
            }
            // Clear of the top bar text, the shortcut hint and the map hexes.
            let top = bounds(text_run(16.0, 12.0, 2.0, WHITE, &format!("ROUND {}  ACTIVE P1 (PLAYER)  SEED {}", state.round, 23)).iter().map(to_px));
            assert!(rows[0].1 > top.3);
            for h in map_hexes() {
                let c = layout::hex_to_pixel(h);
                for r in &rows {
                    let hb = bounds(layout::corners(c, HEX_SIZE).into_iter());
                    assert!(r.2 < hb.0 || r.0 > hb.2 || r.3 < hb.1 || r.1 > hb.3, "legend overlaps {h:?}");
                }
            }
        }
    }

    /// Distance from the origin to the nearest point of an axis-aligned box.
    fn nearest_to_origin(b: (f32, f32, f32, f32)) -> f32 {
        let nx = 0.0f32.clamp(b.0, b.2);
        let ny = 0.0f32.clamp(b.1, b.3);
        nx.hypot(ny)
    }

    fn overlap(a: (f32, f32, f32, f32), b: (f32, f32, f32, f32)) -> bool {
        a.0 < b.2 && b.0 < a.2 && a.1 < b.3 && b.1 < a.3
    }

    #[test]
    fn badge_geometry_is_inside_the_hex_and_clear_of_rings_labels_bars_and_markers() {
        for c in map_hexes().into_iter().map(layout::hex_to_pixel).chain([(500.0, 300.0)]) {
            let mut badge = Painter { verts: Vec::new() };
            badge.height_badge(c, 3);
            let b = bounds(badge.verts.iter().map(to_px));
            let rel = (b.0 - c.0, b.1 - c.1, b.2 - c.0, b.3 - c.1);
            // Inside the hex outline (HEX_SIZE), with every corner and edge point.
            let corners = layout::corners(c, HEX_SIZE);
            for p in badge.verts.iter().map(to_px) {
                let crosses: Vec<f32> = (0..6)
                    .map(|i| {
                        let (a, bb) = (corners[i], corners[(i + 1) % 6]);
                        (bb.0 - a.0) * (p.1 - a.1) - (bb.1 - a.1) * (p.0 - a.0)
                    })
                    .collect();
                assert!(crosses.iter().all(|x| *x >= -0.01) || crosses.iter().all(|x| *x <= 0.01), "badge leaves the hex at {p:?}");
            }
            // Clear of the active (22 px) and selected unit rings and the token.
            assert!(nearest_to_origin(rel) > 22.0, "badge touches the unit ring: {rel:?}");
            // Clear of the AP-cost label (widest case: two digits), the unit name text,
            // the health and armour bars and the ZoC marker (circle plus glyph).
            let mut ap = Painter { verts: Vec::new() };
            ap.text(c.0 - 6.0, c.1 - 7.0, 2.0, AP_LABEL, "88");
            let mut name = Painter { verts: Vec::new() };
            name.text(c.0 - 12.0, c.1 - 7.0, 2.0, WHITE, "P1");
            let mut zoc = Painter { verts: Vec::new() };
            zoc.control_marker(c);
            let others = [
                bounds(ap.verts.iter().map(to_px)),
                bounds(name.verts.iter().map(to_px)),
                (c.0 - 18.0, c.1 + 20.0, c.0 + 18.0, c.1 + 29.0),
                bounds(zoc.verts.iter().map(to_px)),
            ];
            for o in others {
                assert!(!overlap(b, o), "badge {b:?} overlaps {o:?}");
            }
            // The digit sits inside the filled rectangle.
            let glyph = bounds(badge.verts.iter().filter(|v| v.color == HEIGHT_BADGE_GLYPH).map(to_px));
            assert!(glyph.0 >= b.0 && glyph.1 >= b.1 && glyph.2 <= b.2 && glyph.3 <= b.3);
        }
    }

    #[test]
    fn badge_coexists_with_zoc_ap_labels_rings_and_bars_on_edge_hexes() {
        // Both overlays on, a unit on an edge hex with a reachable neighbour label.
        let mut state = bb_rules::scenario::battle(23, bb_rules::scenario::TerrainPreset::Hills);
        for unit in &mut state.units {
            unit.alive = false;
        }
        state.units[0].alive = true;
        state.units[0].pos = hex(-4, 2);
        state.units[0].ap = 9;
        state.units[3].alive = true;
        state.units[3].pos = hex(-3, 0);
        state.queue = vec![0];
        let reach = state.reachable(0);
        assert!(!reach.is_empty());
        let sc = Scene { reach: &reach, control_overlay: true, height_overlay: true, ..scene(&state, Some(0), true) };
        let verts = build_scene(&sc);
        let edge_hexes: Vec<Hex> = map_hexes().into_iter().filter(|h| h.q.abs().max(h.r.abs()).max((h.q + h.r).abs()) == 4).collect();
        assert_eq!(edge_hexes.len(), 24);
        for h in map_hexes() {
            assert!(contains_run(&verts, &badge_run(h, state.elevations.get(h).unwrap())), "{h:?}");
        }
        assert!(count_color(&verts, CONTROL_MARKER) > 0, "ZoC markers still drawn");
        assert!(reach.iter().all(|(h, cost)| contains_run(&verts, &text_run(layout::hex_to_pixel(*h).0 - 6.0, layout::hex_to_pixel(*h).1 - 7.0, 2.0, AP_LABEL, &cost.to_string()))));
        // The occupied edge hex shows its ring, name and bars next to its badge.
        let c = layout::hex_to_pixel(hex(-4, 2));
        assert!(contains_run(&verts, &badge_run(hex(-4, 2), state.elevations.get(hex(-4, 2)).unwrap())));
        assert!(contains_run(&verts, &text_run(c.0 - 12.0, c.1 - 7.0, 2.0, WHITE, "P1")));
    }

    #[test]
    fn hills_scene_shows_rules_derived_uphill_costs() {
        // P1 on (-2,0) (height 0); (-1,0) is height 1, so the step costs MOVE_AP + 1.
        let state = hills_pair(hex(-2, 0), hex(3, -3));
        let reach = state.reachable(0);
        let cost_of = |h: Hex| reach.iter().find(|(r, _)| *r == h).map(|(_, c)| *c);
        assert_eq!(state.elevations.get(hex(-1, 0)), Some(1));
        assert_eq!(cost_of(hex(-1, 0)), Some(3), "uphill step from the rules query");
        assert_eq!(cost_of(hex(-3, 0)), Some(2), "level step");
        let sc = Scene { reach: &reach, ..heights_on(&state, Some(0)) };
        let verts = build_scene(&sc);
        for (h, cost) in &reach {
            let c = layout::hex_to_pixel(*h);
            assert!(contains_run(&verts, &text_run(c.0 - 6.0, c.1 - 7.0, 2.0, AP_LABEL, &cost.to_string())), "{h:?} cost {cost}");
        }
        // A flat map would list 2 for the same hex, so the rules query really supplies the 3.
        let flat = {
            let mut f = state.clone();
            f.elevations = Default::default();
            f
        };
        assert_eq!(flat.reachable(0).iter().find(|(r, _)| *r == hex(-1, 0)).map(|(_, c)| *c), Some(2));
        // The preview box shows the rules plan too (hovering the height 1 hex).
        let preview = Scene { hover: Some(hex(-1, 0)), ..sc };
        let plan = state.preview_move(0, hex(-1, 0)).unwrap();
        assert_eq!(plan.ap_cost, 3);
        let first = &movement_preview_lines(&plan)[0];
        assert_eq!(first.0, "MOVE AP 3 FAT 2 LEFT 6 MELEE BUDGET YES");
        assert!(has_row(&build_scene(&preview), 0, PREVIEW_COST, &first.0));
    }

    #[test]
    fn hills_scene_shows_elevation_adjusted_hit_percentages() {
        let flat_chance = |a: Hex, t: Hex| {
            let mut s = hills_pair(a, t);
            s.elevations = Default::default();
            s.hit_chance(0, 3)
        };
        // Attacker uphill of the target (+10) and downhill of it (-10).
        for (attacker, target, delta) in [(hex(-1, 0), hex(-2, 0), 10i32), (hex(-2, 0), hex(-1, 0), -10), (hex(-3, 0), hex(-2, 0), 0)] {
            let state = hills_pair(attacker, target);
            let chance = state.hit_chance(0, 3);
            assert_eq!(chance as i32, flat_chance(attacker, target) as i32 + delta, "{attacker:?} -> {target:?}");
            let sc = Scene { hover: Some(target), ..heights_on(&state, Some(0)) };
            let verts = build_scene(&sc);
            let c = layout::hex_to_pixel(target);
            assert!(contains_run(&verts, &text_run(c.0 - 36.0, c.1 - 46.0, 2.0, YELLOW, &format!("HIT {chance}%"))), "HIT {chance}%");
        }
    }

    /// Triangle bounding boxes drawn strictly after the last copy of `run`.
    fn drawn_after(verts: &[Vertex], run: &[Vertex]) -> Vec<(f32, f32, f32, f32)> {
        let start = verts.windows(run.len()).rposition(|w| w.iter().zip(run).all(|(a, b)| same(a, b))).expect("run present") + run.len();
        verts[start..].chunks(3).map(|t| bounds(t.iter().map(to_px))).collect()
    }

    #[test]
    fn badges_stay_uncovered_after_battle_completion() {
        let base = bb_rules::scenario::battle(1, bb_rules::scenario::TerrainPreset::Hills);
        for (outcome, reason) in [
            (Outcome::Victory, EndReason::Escape),
            (Outcome::Victory, EndReason::Elimination),
            (Outcome::Defeat, EndReason::Elimination),
            (Outcome::Defeat, EndReason::TimeLimit),
            (Outcome::Retreat, EndReason::Escape),
        ] {
            let mut state = base.clone();
            state.outcome = Some(outcome);
            state.end_reason = Some(reason);
            let verts = build_scene(&heights_on(&state, None));
            for h in map_hexes() {
                let run = badge_run(h, state.elevations.get(h).unwrap());
                let badge = bounds(run.iter().map(to_px));
                assert!(contains_run(&verts, &run), "{h:?}");
                for later in drawn_after(&verts, &run) {
                    assert!(!overlap(badge, later), "{outcome:?}/{reason:?}: {h:?} badge {badge:?} is covered by {later:?}");
                }
            }
        }
    }

    // --- Adrenaline button and pending markers ----------------------------------

    /// Stock scenario with the opt-in loadout; P1 is the active, started player unit.
    fn loadout() -> BattleState {
        bb_rules::scenario::adrenaline_battle(1, bb_rules::scenario::TerrainPreset::Flat)
    }

    fn skill_button_border(verts: &[Vertex], r: Rect) -> (f32, f32, f32, f32) {
        // A button's own border rectangle is the only BUTTON_BORDER geometry in its part of the top strip.
        bounds(verts.iter().filter(|v| v.color == BUTTON_BORDER).map(to_px).filter(|p| p.1 < 44.0 && p.0 >= r.x - 1.0 && p.0 <= r.right() + 1.0))
    }

    fn marker_rects(verts: &[Vertex]) -> usize {
        verts.iter().filter(|v| v.color == PENDING_MARKER).count() / 6
    }

    fn pending_marker_run(row: u32) -> Vec<Vertex> {
        let mut p = Painter { verts: Vec::new() };
        p.pending_marker(panel_row_y(row) + 36.0);
        p.verts
    }

    fn skill_button_run(enabled: bool) -> Vec<Vertex> {
        let mut p = Painter { verts: Vec::new() };
        p.adrenaline_button(enabled);
        p.verts
    }

    fn rotation_button_run(look: SkillLook) -> Vec<Vertex> {
        let mut p = Painter { verts: Vec::new() };
        p.rotation_button(look);
        p.verts
    }

    fn indomitable_button_run(look: SkillLook) -> Vec<Vertex> {
        let mut p = Painter { verts: Vec::new() };
        p.indomitable_button(look);
        p.verts
    }

    fn footwork_button_run(look: SkillLook) -> Vec<Vertex> {
        let mut p = Painter { verts: Vec::new() };
        p.footwork_button(look);
        p.verts
    }

    #[test]
    fn adrenaline_button_states_follow_the_rules_availability_query() {
        let state = loadout();
        let sc = scene(&state, Some(0), false);
        assert!(adrenaline_enabled(&sc));
        let verts = build_scene(&sc);
        assert!(contains_run(&verts, &skill_button_run(true)));
        assert_eq!(skill_fill(&verts, layout::ADRENALINE_BUTTON), Some(SKILL_ENABLED));

        let mut cases: Vec<(&str, BattleState, Option<u32>)> = Vec::new();
        cases.push(("default scenario has no capability", stock(), Some(0)));
        cases.push(("nothing selected", loadout(), None));
        cases.push(("enemy selected", loadout(), Some(3)));
        let mut c = loadout();
        c.units[0].adrenaline_pending = true;
        cases.push(("pending", c, Some(0)));
        let mut c = loadout();
        c.units[0].ap = ADRENALINE_AP - 1;
        cases.push(("not enough AP", c, Some(0)));
        let mut c = loadout();
        c.units[0].fatigue = c.units[0].stats.fatigue_max - ADRENALINE_FATIGUE + 1;
        cases.push(("fatigue over the cap", c, Some(0)));
        let mut c = loadout();
        c.units[0].stats.fatigue_max = u32::MAX;
        c.units[0].fatigue = u32::MAX;
        cases.push(("fatigue overflow", c, Some(0)));
        let mut c = loadout();
        c.queue = vec![3, 0];
        cases.push(("enemy turn", c, Some(0)));
        let mut c = loadout();
        c.outcome = Some(Outcome::Victory);
        cases.push(("battle over", c, Some(0)));
        let mut c = loadout();
        c.units[0].morale = Morale::Fleeing;
        cases.push(("fleeing", c, Some(0)));
        let mut c = loadout();
        c.units[0].retreating = true;
        cases.push(("retreating", c, Some(0)));
        let mut c = loadout();
        c.units[0].alive = false;
        cases.push(("dead", c, Some(0)));
        let mut c = loadout();
        c.units[0].fled = true;
        cases.push(("escaped", c, Some(0)));
        for (what, st, selected) in &cases {
            let sc = scene(st, *selected, false);
            assert!(!adrenaline_enabled(&sc), "{what}");
            let verts = build_scene(&sc);
            assert!(contains_run(&verts, &skill_button_run(false)), "{what}");
            assert_eq!(skill_fill(&verts, layout::ADRENALINE_BUTTON), Some(SKILL_DISABLED), "{what}");
        }
        // The exact boundary that still passes.
        let mut c = loadout();
        c.units[0].ap = ADRENALINE_AP;
        c.units[0].fatigue = c.units[0].stats.fatigue_max - ADRENALINE_FATIGUE;
        assert!(adrenaline_enabled(&scene(&c, Some(0), false)));
    }

    #[test]
    fn adrenaline_button_is_drawn_exactly_on_the_shared_hit_rectangle() {
        for enabled in [true, false] {
            let state = if enabled { loadout() } else { stock() };
            let verts = build_scene(&scene(&state, Some(0), false));
            assert!(close(skill_button_border(&verts, layout::ADRENALINE_BUTTON), rect_bounds(layout::ADRENALINE_BUTTON)), "enabled={enabled}");
        }
    }

    #[test]
    fn adrenaline_button_shows_label_costs_and_next_round_effect_from_the_rules_constants() {
        assert_eq!(adrenaline_info_lines(), ["COST 1 AP 20 FAT".to_string(), "NEXT ROUND INIT +2000".to_string()]);
        assert_eq!((ADRENALINE_AP, ADRENALINE_FATIGUE, ADRENALINE_INITIATIVE_BONUS), (1, 20, 2000));
        let r = layout::ADRENALINE_BUTTON;
        for (state, text) in [(loadout(), WHITE), (stock(), BUTTON_DISABLED_TEXT)] {
            let verts = build_scene(&scene(&state, Some(0), false));
            assert!(text_geometry(&verts, r.x + SKILL_TEXT_X, r.y + SKILL_TITLE_DY, layout::SKILL_TITLE_SCALE, text, "ADRENALINE (1)").is_some());
            assert!(text_geometry(&verts, r.x + SKILL_TEXT_X, r.y + SKILL_INFO_DY, 1.0, text, "COST 1 AP 20 FAT").is_some());
            assert!(text_geometry(&verts, r.x + SKILL_TEXT_X, r.y + SKILL_INFO_DY + 8.0, 1.0, text, "NEXT ROUND INIT +2000").is_some());
        }
    }

    #[test]
    fn adrenaline_button_text_stays_inside_the_button_on_supported_glyphs() {
        let r = layout::ADRENALINE_BUTTON;
        let label_w = Painter::text_width(layout::SKILL_TITLE_SCALE, layout::ADRENALINE_LABEL);
        assert!(SKILL_TEXT_X + label_w + 8.0 <= r.w, "label fits the button width");
        let info = adrenaline_info_lines();
        for line in &info {
            assert!(SKILL_TEXT_X + Painter::text_width(1.0, line) + 8.0 <= r.w, "{line}");
        }
        // Title rows 5..13.75, info rows 19..26 and 27..34, inside the 2 px border.
        assert!(SKILL_TITLE_DY + 7.0 * layout::SKILL_TITLE_SCALE < SKILL_INFO_DY && SKILL_INFO_DY + 8.0 + 7.0 + 2.0 <= r.h, "label and two scale-1 rows fit the button height");
        let fallback = crate::font::glyph('\u{1}');
        let supported = |c: char| c == ' ' || crate::font::glyph(c) != fallback;
        for text in info.iter().map(String::as_str).chain([layout::ADRENALINE_LABEL, PENDING_MARKER_TEXT_LABEL]) {
            if let Some(c) = text.chars().find(|c| !supported(*c)) {
                panic!("unsupported glyph {c:?} in {text:?}");
            }
        }
        // All of the button's own geometry is inside its rectangle.
        let (x0, y0, x1, y1) = bounds(skill_button_run(true).iter().map(to_px));
        assert!(close((x0, y0, x1, y1), rect_bounds(r)), "{x0} {y0} {x1} {y1} vs {r:?}");
    }

    #[test]
    fn adrenaline_button_is_clear_of_the_window_edge_top_bar_and_other_controls() {
        let r = layout::ADRENALINE_BUTTON;
        assert!(r.x >= 0.0 && r.y >= 0.0 && r.right() <= WIN_W && r.bottom() <= WIN_H);
        let mut state = loadout();
        state.round = 99;
        let sc = Scene { seed: u64::MAX, ..scene(&state, Some(0), false) };
        let verts = build_scene(&sc);
        let top = format!("ROUND 99  ACTIVE P1 (PLAYER)  SEED {}", u64::MAX);
        let top_geo = text_geometry(&verts, 16.0, 12.0, 2.0, WHITE, &top).expect("top bar text");
        let top_box = bounds(top_geo.into_iter());
        assert!(top_box.2 < r.x, "top bar text ends at {} left of the button at {}", top_box.2, r.x);
        // Every unit row (first line y 52) and the panel background (y 44) start below the button.
        assert!(r.bottom() < 44.0 && layout::ROTATION_BUTTON.bottom() < 44.0);
        for other in [layout::END_ROUND_BUTTON, layout::RETREAT_BUTTON] {
            assert!(r.bottom() < other.y);
        }
        // Nothing but the button is drawn in its rectangle apart from the BG fill below it.
        let in_strip = |v: &&Vertex| {
            let p = to_px(v);
            p.0 >= r.x && p.1 < 44.0 && p.0 <= WIN_W
        };
        let button = skill_button_run(true);
        let rotation = rotation_button_run(SkillLook::Disabled);
        let footwork = footwork_button_run(SkillLook::Disabled);
        let strip: Vec<&Vertex> = verts.iter().skip(6).filter(in_strip).collect();
        let indomitable = indomitable_button_run(SkillLook::Disabled);
        assert_eq!(strip.len(), button.len() + rotation.len() + footwork.len() + indomitable.len(), "only the four skill buttons are drawn in the top-right strip");
    }

    #[test]
    fn pending_marker_appears_for_every_present_pending_unit_including_finished_turns() {
        let mut state = loadout();
        let none = build_scene(&scene(&state, Some(0), false));
        assert_eq!(marker_rects(&none), 0);
        // P2 already finished its turn (not queued), P3 is still queued, P1 is active.
        state.units[0].adrenaline_pending = true;
        state.units[1].adrenaline_pending = true;
        state.units[2].adrenaline_pending = true;
        state.queue.retain(|&id| id != 1);
        let verts = build_scene(&scene(&state, Some(0), false));
        assert_eq!(marker_rects(&verts), 3);
        for row in 0..3 {
            assert!(contains_run(&verts, &pending_marker_run(row)), "row {row}");
        }
        // Enemies can carry it too (capability is scriptable), and it is shown for them as well.
        state.units[3].adrenaline_pending = true;
        assert_eq!(marker_rects(&build_scene(&scene(&state, Some(0), false))), 4);
        // Without a selection or during an enemy turn the markers stay.
        state.queue = vec![3, 0];
        assert_eq!(marker_rects(&build_scene(&scene(&state, None, false))), 4);
    }

    #[test]
    fn pending_marker_is_omitted_for_dead_and_escaped_rows() {
        let mut state = loadout();
        for id in 0..3 {
            state.units[id].adrenaline_pending = true;
        }
        state.units[1].alive = false;
        state.units[2].fled = true;
        let verts = build_scene(&scene(&state, Some(0), false));
        assert_eq!(marker_rects(&verts), 1);
        assert!(contains_run(&verts, &pending_marker_run(0)));
        assert!(!contains_run(&verts, &pending_marker_run(1)) && !contains_run(&verts, &pending_marker_run(2)));
        // A row that is dead keeps its DEAD text; an escaped one its FLED text.
        assert!(text_geometry(&verts, PANEL_X, panel_row_y(1), 2.0, DIM, "P2 DEAD").is_some());
        assert!(text_geometry(&verts, PANEL_X, panel_row_y(2), 2.0, FLED, "P3 FLED").is_some());
    }

    #[test]
    fn pending_marker_disappears_after_the_next_round_consumes_it() {
        let mut state = loadout();
        state.apply(Command::Adrenaline { unit: 0 }).unwrap();
        assert!(state.units[0].adrenaline_pending);
        assert_eq!(marker_rects(&build_scene(&scene(&state, Some(0), false))), 1);
        // Still shown after Wait and after the unit's turn ended within the same round.
        state.apply(Command::Wait { unit: 0 }).unwrap();
        assert_eq!(marker_rects(&build_scene(&scene(&state, None, false))), 1);
        let round = state.round;
        while state.round == round {
            let a = state.queue[0];
            let cmd = if state.units[a as usize].morale == Morale::Fleeing { Command::Flee { unit: a } } else { Command::EndTurn { unit: a } };
            state.apply(cmd).unwrap();
            if state.round == round {
                assert_eq!(marker_rects(&build_scene(&scene(&state, None, false))), 1, "kept for the rest of the round");
            }
        }
        assert!(!state.units[0].adrenaline_pending);
        assert_eq!(marker_rects(&build_scene(&scene(&state, None, false))), 0);
    }

    #[test]
    fn pending_marker_geometry_is_explained_inside_the_window_and_clear_of_row_text() {
        let (x, w) = pending_marker_box();
        assert!(x >= PANEL_X && x + w <= WIN_W, "inside the side panel and the window");
        let h = 7.0 + 2.0 * PENDING_MARKER_PAD;
        let text_w = Painter::text_width(1.0, PENDING_MARKER_TEXT_LABEL);
        assert!(text_w + 2.0 * PENDING_MARKER_PAD <= w);
        assert_eq!(PENDING_MARKER_TEXT_LABEL, "ADRENALINE: NEXT RD");
        // Widest plausible AP/fatigue text ends left of the marker; the box stays within the
        // row's 60 px pitch, below its HP/armour line and above the next row.
        let widest = Painter::text_width(2.0, "AP 99/99 FAT 999/999");
        assert!(PANEL_X + widest + 8.0 <= x, "resource text ends at {} marker starts at {x}", PANEL_X + widest);
        assert!(36.0 + h <= 60.0 && 36.0 >= 18.0 + 14.0);
        // Measured on a scene: the marker box and text of each row sit in the row's own band.
        let mut state = loadout();
        for u in 0..3 {
            state.units[u].adrenaline_pending = true;
        }
        let verts = build_scene(&scene(&state, Some(0), false));
        for row in 0..3 {
            let top = panel_row_y(row as u32) + 36.0;
            let run = pending_marker_run(row as u32);
            let (x0, y0, x1, y1) = bounds(run.iter().map(to_px));
            assert!(contains_run(&verts, &run));
            assert!(x0 >= x - 0.01 && x1 <= x + w + 0.01 && y0 >= top - 0.01 && y1 <= top + h + 0.01, "row {row}: {x0} {y0} {x1} {y1}");
            // The row's own resource text is untouched by the marker.
            let res = format!("AP {}/{} FAT {}/{}", state.units[row].ap, state.units[row].stats.ap_max, state.units[row].fatigue, state.units[row].stats.fatigue_max);
            let geo = text_geometry(&verts, PANEL_X, panel_row_y(row as u32) + 36.0, 2.0, if row == 0 { YELLOW } else { WHITE }, &res)
                .expect("resource text");
            assert!(bounds(geo.into_iter()).2 < x0);
        }
        // Marker text is scale 1 on the amber box, dark.
        assert!(text_geometry(&verts, x + PENDING_MARKER_PAD, panel_row_y(0) + 36.0 + PENDING_MARKER_PAD, 1.0, PENDING_MARKER_TEXT, PENDING_MARKER_TEXT_LABEL).is_some());
    }

    #[test]
    fn adrenaline_scene_is_pure() {
        let mut state = loadout();
        state.units[1].adrenaline_pending = true;
        let (hash, rng, units, queue) = (state.state_hash(), state.rng.clone(), state.units.clone(), state.queue.clone());
        let a = build_scene(&scene(&state, Some(0), false));
        let b = build_scene(&scene(&state, Some(0), false));
        assert!(a.len() == b.len() && a.iter().zip(&b).all(|(x, y)| same(x, y)));
        assert_eq!((state.state_hash(), &state.rng, &state.units, &state.queue), (hash, &rng, &units, &queue));
    }

    // --- Rotation button and ally-targeting mode ---------------------------------------

    /// Stock scenario with the opt-in Rotation loadout: P1 (-3,3) is active, P2 (-4,3) stands
    /// on the map edge and P3 (-3,2) is interior, so both are legal targets.
    fn rot() -> BattleState {
        bb_rules::scenario::rotation_battle(1, bb_rules::scenario::TerrainPreset::Flat)
    }

    fn targeting<'a>(state: &'a BattleState, hover: Option<Hex>) -> Scene<'a> {
        Scene { targeting: Some(TargetAction::Rotation), hover, ..scene(state, Some(0), false) }
    }

    fn unit_hex(state: &BattleState, id: usize) -> Hex {
        state.units[id].pos
    }

    /// Both participants stand on map-edge hexes: each arrival is predicted to break morale.
    fn two_penalty_state() -> BattleState {
        let mut state = rot();
        state.units[0].pos = hex(-4, 3);
        state.units[1].pos = hex(-4, 4);
        state.units[2].pos = hex(-2, 1);
        state
    }

    fn swap_label_drawn(verts: &[Vertex], at: Hex) -> bool {
        let c = layout::hex_to_pixel(at);
        text_geometry(verts, c.0 + SWAP_LABEL_OFFSET.0, c.1 + SWAP_LABEL_OFFSET.1, 1.0, SWAP_TEXT, "SWAP").is_some()
    }

    fn swap_hex_fills(verts: &[Vertex], col: Rgba) -> usize {
        // One hex fill is a four-triangle fan.
        count_color(verts, col) / 12
    }

    #[test]
    fn rotation_button_states_follow_the_rules_legal_targets() {
        let state = rot();
        let sc = scene(&state, Some(0), false);
        assert!(rotation_enabled(&sc));
        assert_eq!(rotation_targeting_actor(&sc), None, "enabled is not targeting");
        let verts = build_scene(&sc);
        assert!(contains_run(&verts, &rotation_button_run(SkillLook::Enabled)));
        assert_eq!(skill_fill(&verts, layout::ROTATION_BUTTON), Some(SKILL_ENABLED));
        // The two buttons are independent: this loadout has no Adrenaline.
        assert_eq!(skill_fill(&verts, layout::ADRENALINE_BUTTON), Some(SKILL_DISABLED));

        let mut cases: Vec<(&str, BattleState, Option<u32>)> = Vec::new();
        cases.push(("default scenario has no capability", stock(), Some(0)));
        cases.push(("nothing selected", rot(), None));
        cases.push(("enemy selected", rot(), Some(3)));
        let mut c = rot();
        c.units[0].ap = ROTATION_AP - 1;
        cases.push(("not enough AP", c, Some(0)));
        let mut c = rot();
        c.units[0].fatigue = c.units[0].stats.fatigue_max - ROTATION_FATIGUE + 1;
        cases.push(("fatigue over the cap", c, Some(0)));
        let mut c = rot();
        c.units[0].pos = hex(0, 3);
        cases.push(("no adjacent ally", c, Some(0)));
        let mut c = rot();
        c.queue = vec![3, 0];
        cases.push(("enemy turn", c, Some(0)));
        let mut c = rot();
        c.outcome = Some(Outcome::Victory);
        cases.push(("battle over", c, Some(0)));
        let mut c = rot();
        c.units[0].morale = Morale::Fleeing;
        cases.push(("fleeing", c, Some(0)));
        let mut c = rot();
        c.units[0].retreating = true;
        cases.push(("retreating", c, Some(0)));
        let mut c = rot();
        c.units[0].alive = false;
        cases.push(("dead", c, Some(0)));
        let mut c = rot();
        c.units[0].fled = true;
        cases.push(("escaped", c, Some(0)));
        for (what, st, selected) in &cases {
            let sc = scene(st, *selected, false);
            assert!(!rotation_enabled(&sc), "{what}");
            let verts = build_scene(&sc);
            assert!(contains_run(&verts, &rotation_button_run(SkillLook::Disabled)), "{what}");
            assert_eq!(skill_fill(&verts, layout::ROTATION_BUTTON), Some(SKILL_DISABLED), "{what}");
            // Requesting targeting on an ineligible scene draws the ordinary display instead.
            let sc = Scene { targeting: Some(TargetAction::Rotation), ..scene(st, *selected, false) };
            assert_eq!(rotation_targeting_actor(&sc), None, "{what}");
            assert!(!build_scene(&sc).iter().any(|v| v.color == SWAP_FILL || v.color == SWAP_TEXT), "{what}");
        }
        // The exact boundary that still passes.
        let mut c = rot();
        c.units[0].ap = ROTATION_AP;
        c.units[0].fatigue = c.units[0].stats.fatigue_max - ROTATION_FATIGUE;
        assert!(rotation_enabled(&scene(&c, Some(0), false)));
        // The rules query is the authority: removing the only legal targets disables it.
        let mut c = rot();
        c.units[1].alive = false;
        assert!(rotation_enabled(&scene(&c, Some(0), false)), "P3 is still legal");
        c.units[2].morale = Morale::Fleeing;
        assert!(c.legal_rotation_targets(0).is_empty() && !rotation_enabled(&scene(&c, Some(0), false)));
    }

    #[test]
    fn open_targeting_shows_the_active_button_look() {
        let state = rot();
        let verts = build_scene(&targeting(&state, None));
        assert!(contains_run(&verts, &rotation_button_run(SkillLook::Active)));
        assert_eq!(skill_fill(&verts, layout::ROTATION_BUTTON), Some(SKILL_ACTIVE));
        assert_eq!(rotation_targeting_actor(&targeting(&state, None)), Some(0));
        // Selection of a non-actor or no selection draws nothing of it.
        let sc = Scene { selected: None, ..targeting(&state, None) };
        assert_eq!(rotation_targeting_actor(&sc), None);
    }

    #[test]
    fn rotation_button_is_drawn_exactly_on_the_shared_hit_rectangle() {
        for (state, look) in [(rot(), SkillLook::Enabled), (stock(), SkillLook::Disabled)] {
            let verts = build_scene(&scene(&state, Some(0), false));
            assert!(close(skill_button_border(&verts, layout::ROTATION_BUTTON), rect_bounds(layout::ROTATION_BUTTON)), "{look:?}");
            assert!(close(skill_button_border(&verts, layout::ADRENALINE_BUTTON), rect_bounds(layout::ADRENALINE_BUTTON)));
        }
    }

    #[test]
    fn rotation_button_shows_label_costs_and_effect_from_the_rules_constants() {
        assert_eq!(rotation_info_lines(), ["COST 3 AP 25 FAT".to_string(), "SWAP ADJACENT ALLY".to_string()]);
        assert_eq!((ROTATION_AP, ROTATION_FATIGUE), (3, 25));
        let r = layout::ROTATION_BUTTON;
        for (state, text) in [(rot(), WHITE), (stock(), BUTTON_DISABLED_TEXT)] {
            let verts = build_scene(&scene(&state, Some(0), false));
            assert!(text_geometry(&verts, r.x + SKILL_TEXT_X, r.y + SKILL_TITLE_DY, layout::SKILL_TITLE_SCALE, text, "ROTATION (2)").is_some());
            assert!(text_geometry(&verts, r.x + SKILL_TEXT_X, r.y + SKILL_INFO_DY, 1.0, text, "COST 3 AP 25 FAT").is_some());
            assert!(text_geometry(&verts, r.x + SKILL_TEXT_X, r.y + SKILL_INFO_DY + 8.0, 1.0, text, "SWAP ADJACENT ALLY").is_some());
        }
    }

    #[test]
    fn rotation_button_text_stays_inside_the_button_on_supported_glyphs() {
        let r = layout::ROTATION_BUTTON;
        assert!(SKILL_TEXT_X + Painter::text_width(layout::SKILL_TITLE_SCALE, layout::ROTATION_LABEL) + 8.0 <= r.w);
        for line in rotation_info_lines() {
            assert!(SKILL_TEXT_X + Painter::text_width(1.0, &line) + 8.0 <= r.w, "{line}");
        }
        let fallback = crate::font::glyph('\u{1}');
        let supported = |c: char| c == ' ' || crate::font::glyph(c) != fallback;
        for text in rotation_info_lines().iter().map(String::as_str).chain([layout::ROTATION_LABEL, "SWAP"]) {
            if let Some(c) = text.chars().find(|c| !supported(*c)) {
                panic!("unsupported glyph {c:?} in {text:?}");
            }
        }
        for look in [SkillLook::Disabled, SkillLook::Enabled, SkillLook::Active] {
            assert!(close(bounds(rotation_button_run(look).iter().map(to_px)), rect_bounds(r)), "{look:?}: all geometry is inside the rectangle");
        }
    }

    #[test]
    fn skill_strip_buttons_are_disjoint_and_clear_of_the_top_bar_and_unit_rows() {
        let (a, r, f, i) = (layout::ADRENALINE_BUTTON, layout::ROTATION_BUTTON, layout::FOOTWORK_BUTTON, layout::INDOMITABLE_BUTTON);
        assert!(a.right() < r.x && r.right() < f.x && f.right() < i.x && i.right() <= WIN_W);
        for (b, x) in [(a, 684.0), (r, 834.0), (f, 984.0), (i, 1134.0)] {
            assert_eq!((b.x, b.y, b.w, b.h), (x, 3.0, 144.0, 40.0));
        }
        let mut state = rot();
        state.round = 99;
        let sc = Scene { seed: u64::MAX, ..scene(&state, Some(0), false) };
        let verts = build_scene(&sc);
        let top = format!("ROUND 99  ACTIVE P1 (PLAYER)  SEED {}", u64::MAX);
        let top_box = bounds(text_geometry(&verts, 16.0, 12.0, 2.0, WHITE, &top).expect("top bar text").into_iter());
        assert!(top_box.2 < a.x, "top bar text ends at {} left of the strip at {}", top_box.2, a.x);
        for b in [a, r, f, i] {
            assert!(b.bottom() < 44.0, "the strip ends above the panel background and the first unit row (y 52)");
        }
        // Only the four buttons are drawn in the strip, and each run is complete.
        let in_strip = |v: &&Vertex| to_px(v).1 < 44.0 && to_px(v).0 >= a.x;
        let strip: Vec<&Vertex> = verts.iter().skip(6).filter(in_strip).collect();
        assert_eq!(
            strip.len(),
            skill_button_run(false).len()
                + rotation_button_run(SkillLook::Enabled).len()
                + footwork_button_run(SkillLook::Disabled).len()
                + indomitable_button_run(SkillLook::Disabled).len()
        );
        assert!(contains_run(&verts, &skill_button_run(false)) && contains_run(&verts, &rotation_button_run(SkillLook::Enabled)));
        assert!(contains_run(&verts, &footwork_button_run(SkillLook::Disabled)));
        assert!(contains_run(&verts, &indomitable_button_run(SkillLook::Disabled)));
        // The unit rows' first text line starts below the strip.
        assert!(text_geometry(&verts, PANEL_X, panel_row_y(0), 2.0, YELLOW, "P1").is_some());
        assert!(panel_row_y(0) > a.bottom().max(r.bottom()).max(f.bottom()).max(i.bottom()));
    }

    #[test]
    fn legal_allies_get_a_swap_marker_and_nothing_else_does() {
        let state = rot();
        assert_eq!(state.legal_rotation_targets(0), vec![1, 2]);
        let verts = build_scene(&targeting(&state, None));
        assert_eq!(swap_hex_fills(&verts, SWAP_FILL), 2, "one fill per legal ally");
        assert_eq!(swap_hex_fills(&verts, SWAP_HOVER), 0);
        for id in [1, 2] {
            assert!(swap_label_drawn(&verts, unit_hex(&state, id)), "P{}", id + 1);
        }
        for id in [0, 3, 4, 5] {
            assert!(!swap_label_drawn(&verts, unit_hex(&state, id)), "unit {id} is not a legal target");
        }
        // A non-adjacent ally is not marked (the rules query decides, not the client).
        let mut far = rot();
        far.units[2].pos = hex(-3, 0);
        let verts = build_scene(&targeting(&far, None));
        assert_eq!(swap_hex_fills(&verts, SWAP_FILL), 1);
        assert!(swap_label_drawn(&verts, unit_hex(&far, 1)) && !swap_label_drawn(&verts, unit_hex(&far, 2)));
        // Without targeting no marker exists at all.
        let verts = build_scene(&scene(&state, Some(0), false));
        assert!(!verts.iter().any(|v| v.color == SWAP_FILL || v.color == SWAP_HOVER || v.color == SWAP_TEXT));
        // The hovered legal ally is highlighted differently from the other marked one.
        let verts = build_scene(&targeting(&state, Some(unit_hex(&state, 2))));
        assert_eq!((swap_hex_fills(&verts, SWAP_FILL), swap_hex_fills(&verts, SWAP_HOVER)), (1, 1));
        // Hovering an unmarked hex highlights nothing extra.
        let verts = build_scene(&targeting(&state, Some(unit_hex(&state, 3))));
        assert_eq!((swap_hex_fills(&verts, SWAP_FILL), swap_hex_fills(&verts, SWAP_HOVER)), (2, 0));

        // A stunned ally is omitted by the shared rules plan while another adjacent ally
        // remains targetable; hovering the stunned token shows instructions, never its preview.
        let mut mixed = rot();
        mixed.apply_stun(1).unwrap();
        assert_eq!(mixed.legal_rotation_targets(0), vec![2]);
        let verts = build_scene(&targeting(&mixed, Some(unit_hex(&mixed, 1))));
        assert_eq!((swap_hex_fills(&verts, SWAP_FILL), swap_hex_fills(&verts, SWAP_HOVER)), (1, 0));
        assert!(!swap_label_drawn(&verts, unit_hex(&mixed, 1)));
        assert!(swap_label_drawn(&verts, unit_hex(&mixed, 2)));
        assert!(has_row_prefix(&verts, 0, "ROTATION:"));
        assert!(!has_row_prefix(&verts, 0, "SWAP P1 WITH P2"));
    }

    /// Full footprint of a unit's token: ring (22 px), name text and both bars (to +29).
    fn token_footprint(at: Hex) -> (f32, f32, f32, f32) {
        let c = layout::hex_to_pixel(at);
        (c.0 - 22.0, c.1 - 22.0, c.0 + 22.0, c.1 + 29.0)
    }

    fn boxes_apart(a: (f32, f32, f32, f32), b: (f32, f32, f32, f32)) -> bool {
        a.2 <= b.0 || b.2 <= a.0 || a.3 <= b.1 || b.3 <= a.1
    }

    #[test]
    fn swap_label_clears_neighbouring_tokens_names_bars_and_own_overlays() {
        // The stock arrangement (the reported overlap of P2's label with P3's token and name).
        let state = rot();
        let verts = build_scene(&targeting(&state, None));
        for target in [1usize, 2] {
            let at = unit_hex(&state, target);
            let c = layout::hex_to_pixel(at);
            let geo = text_geometry(&verts, c.0 + SWAP_LABEL_OFFSET.0, c.1 + SWAP_LABEL_OFFSET.1, 1.0, SWAP_TEXT, "SWAP").expect("label");
            let label = bounds(geo.into_iter());
            for other in (0..6).filter(|&o| o != target) {
                assert!(boxes_apart(label, token_footprint(unit_hex(&state, other))), "unit {target}'s label overlaps unit {other}");
            }
            // Below its own name (ends at +7), above its own bars (from +20), inside the hex and
            // clear of the corner pocket badge and the ZoC marker.
            assert!(label.1 >= c.1 + 7.0 && label.3 <= c.1 + 20.0, "{label:?}");
            assert!(label.0 > c.0 - 28.0 && label.2 < c.0 + 28.0);
            let marker = (c.0 - 5.0, c.1 - 33.0, c.0 + 5.0, c.1 - 23.0);
            let badge = (c.0 - 28.25, c.1 - 16.25, c.0 - 21.25, c.1 - 7.25);
            assert!(boxes_apart(label, marker) && boxes_apart(label, badge));
        }
        // An occupied neighbour in each of the six directions, drawn in the scene: the target at
        // the centre, P1 on one neighbour and the other units on four of the others.
        let dirs = [hex(1, 0), hex(1, -1), hex(0, -1), hex(-1, 0), hex(-1, 1), hex(0, 1)];
        let centre = hex(0, 0);
        for actor_dir in 0..6 {
            let mut s = rot();
            s.units[1].pos = centre;
            s.units[0].pos = hex(centre.q + dirs[actor_dir].q, centre.r + dirs[actor_dir].r);
            for (k, id) in [2usize, 3, 4, 5].into_iter().enumerate() {
                let d = dirs[(actor_dir + 1 + k) % 6];
                s.units[id].pos = hex(centre.q + d.q, centre.r + d.r);
            }
            let sc = Scene { hover: None, ..targeting(&s, None) };
            if !s.legal_rotation_targets(0).contains(&1) {
                continue; // an obstacle hex on this direction: not a playable arrangement
            }
            let verts = build_scene(&sc);
            let c = layout::hex_to_pixel(centre);
            let label = bounds(
                text_geometry(&verts, c.0 + SWAP_LABEL_OFFSET.0, c.1 + SWAP_LABEL_OFFSET.1, 1.0, SWAP_TEXT, "SWAP").expect("label").into_iter(),
            );
            for other in [0usize, 2, 3, 4, 5] {
                assert!(boxes_apart(label, token_footprint(unit_hex(&s, other))), "direction {actor_dir}: overlaps unit {other}");
            }
        }
        // Pure geometry for every direction regardless of obstacles.
        let c = layout::hex_to_pixel(centre);
        let label = (c.0 + SWAP_LABEL_OFFSET.0, c.1 + SWAP_LABEL_OFFSET.1, c.0 - SWAP_LABEL_OFFSET.0, c.1 + SWAP_LABEL_OFFSET.1 + 7.0);
        for d in dirs {
            assert!(boxes_apart(label, token_footprint(hex(d.q, d.r))), "{d:?}");
        }
    }

    #[test]
    fn targeting_suppresses_movement_highlights_previews_and_attack_labels() {
        let mut state = rot();
        state.units[3].pos = hex(-2, 3); // an enemy next to P1 in attack range
        let reach = state.reachable(0);
        assert!(!reach.is_empty());
        let step = reach[0].0;
        let ordinary = |hover: Hex| Scene { hover: Some(hover), reach: &reach, ..scene(&state, Some(0), false) };
        let rotating = |hover: Hex| Scene { targeting: Some(TargetAction::Rotation), ..ordinary(hover) };
        let plan = state.preview_move(0, step).expect("a reachable destination previews");
        let rows = movement_preview_lines(&plan);
        let c = layout::hex_to_pixel(step);
        let cost = reach[0].1.to_string();
        let enemy = unit_hex(&state, 3);
        let ec = layout::hex_to_pixel(enemy);
        let hit = format!("HIT {}%", state.hit_chance(0, 3));

        // Control: the ordinary scene shows the green highlight, its cost, the preview and the attack label.
        let verts = build_scene(&ordinary(step));
        assert!(count_color(&verts, GREEN) > 0);
        assert!(text_geometry(&verts, c.0 - 6.0, c.1 - 7.0, 2.0, [0.05, 0.2, 0.05, 1.0], &cost).is_some());
        assert!(rows.iter().enumerate().all(|(i, (t, col))| has_row(&verts, i, *col, t)));
        assert!(text_geometry(&build_scene(&ordinary(enemy)), ec.0 - 36.0, ec.1 - 46.0, 2.0, YELLOW, &hit).is_some());

        // Targeting: none of it, whatever the pointer is over.
        for hover in [step, enemy, unit_hex(&state, 1)] {
            let verts = build_scene(&rotating(hover));
            assert_eq!(count_color(&verts, GREEN), 0, "no movement highlight at {hover:?}");
            assert!(text_geometry(&verts, c.0 - 6.0, c.1 - 7.0, 2.0, [0.05, 0.2, 0.05, 1.0], &cost).is_none());
            assert!(!rows.iter().any(|(t, col)| has_row(&verts, 0, *col, t) || has_row(&verts, 1, *col, t)));
            assert!(text_geometry(&verts, ec.0 - 36.0, ec.1 - 46.0, 2.0, YELLOW, &hit).is_none(), "no attack label");
            assert!(!has_row_prefix(&verts, 0, "MOVE AP"));
        }
        assert!(movement_preview(&rotating(step)).is_none());
        assert!(movement_preview(&ordinary(step)).is_some());
    }

    /// Whether row `row` of the preview box starts with `prefix` in any preview colour.
    fn has_row_prefix(verts: &[Vertex], row: usize, prefix: &str) -> bool {
        let first = |col: Rgba| {
            let mut expected = Painter { verts: Vec::new() };
            expected.text(PREVIEW_BOX.0 + 8.0, PREVIEW_BOX.1 + 6.0 + row as f32 * 16.0, 2.0, col, prefix);
            contains_run(verts, &expected.verts)
        };
        [PREVIEW_COST, PREVIEW_NOTE, PREVIEW_WARN, YELLOW].into_iter().any(first)
    }

    const ROW0: &str = "SWAP P1 WITH P2  COST AP 3 FAT 25 (P1 ONLY)";

    #[test]
    fn hovered_target_preview_shows_cost_destinations_and_one_edge_warning() {
        // P2 stands on the map edge: P1 arrives there; P2 arrives on an interior hex.
        let state = rot();
        let pv = state.preview_rotation(0, 1).unwrap();
        assert_eq!(pv.edge_penalties, vec![(0, hex(-4, 3))]);
        let verts = build_scene(&targeting(&state, Some(unit_hex(&state, 1))));
        assert!(has_row(&verts, 0, PREVIEW_COST, ROW0));
        assert!(has_row(&verts, 1, PREVIEW_NOTE, "P1 TO (-4,3)  P2 TO (-3,3)  NO OPPORTUNITY ATTACKS  ESC OR 2 CANCELS"));
        assert!(has_row(&verts, 2, PREVIEW_WARN, "! P1 ARRIVES AT EDGE (-4,3): MORALE BECOMES BREAKING"));
        assert!(!has_row_prefix(&verts, 3, "!") && preview_text_rows(&verts) == 3);
        assert!(!has_row_prefix(&verts, 0, "ROTATION:"), "the hovered preview replaces the instructions");
    }

    #[test]
    fn hovered_target_preview_with_no_edge_warning_has_two_rows() {
        let state = rot();
        assert!(state.preview_rotation(0, 2).unwrap().edge_penalties.is_empty());
        let verts = build_scene(&targeting(&state, Some(unit_hex(&state, 2))));
        assert!(has_row(&verts, 0, PREVIEW_COST, "SWAP P1 WITH P3  COST AP 3 FAT 25 (P1 ONLY)"));
        assert!(has_row(&verts, 1, PREVIEW_NOTE, "P1 TO (-3,2)  P3 TO (-3,3)  NO OPPORTUNITY ATTACKS  ESC OR 2 CANCELS"));
        assert_eq!(preview_text_rows(&verts), 2);
        assert!(!verts.iter().any(|v| v.color == PREVIEW_WARN));
    }

    #[test]
    fn hovered_target_preview_with_two_edge_warnings_names_both_participants_in_four_rows() {
        let state = two_penalty_state();
        let pv = state.preview_rotation(0, 1).unwrap();
        assert_eq!(pv.edge_penalties, vec![(0, hex(-4, 4)), (1, hex(-4, 3))]);
        let verts = build_scene(&targeting(&state, Some(unit_hex(&state, 1))));
        assert!(has_row(&verts, 0, PREVIEW_COST, ROW0));
        assert!(has_row(&verts, 1, PREVIEW_NOTE, "P1 TO (-4,4)  P2 TO (-4,3)  NO OPPORTUNITY ATTACKS  ESC OR 2 CANCELS"));
        assert!(has_row(&verts, 2, PREVIEW_WARN, "! P1 ARRIVES AT EDGE (-4,4): MORALE BECOMES BREAKING"));
        assert!(has_row(&verts, 3, PREVIEW_WARN, "! P2 ARRIVES AT EDGE (-4,3): MORALE BECOMES BREAKING"));
        assert_eq!(preview_text_rows(&verts), 4);
        // Text stays inside the preview box, clear of the error bar and the side panel.
        let (bx, by, bw, bh) = PREVIEW_BOX;
        let mut sc = targeting(&state, Some(unit_hex(&state, 1)));
        sc.error = Some("NOT A SWAP TARGET");
        let verts = build_scene(&sc);
        let colours = preview_colours();
        let text: Vec<(f32, f32)> = verts.iter().filter(|v| colours[1..].contains(&v.color)).map(to_px).collect();
        let (max_x, max_y) = text.iter().fold((0.0f32, 0.0f32), |m, p| (m.0.max(p.0), m.1.max(p.1)));
        assert!(max_x <= bx + bw - 8.0 && max_y <= by + bh - 4.0, "text reaches the box edge: {max_x},{max_y}");
        assert!(by + bh < WIN_H - 60.0 && bx + bw < PANEL_X - 12.0);
    }

    /// Number of preview-box text rows drawn (rows with any text-coloured geometry).
    fn preview_text_rows(verts: &[Vertex]) -> usize {
        let (_, by, _, _) = PREVIEW_BOX;
        let colours = &preview_colours()[1..];
        let mut rows: Vec<usize> = verts
            .iter()
            .filter(|v| colours.contains(&v.color))
            .map(|v| to_px(v).1)
            .filter(|y| *y >= by && *y < by + 72.0)
            .map(|y| ((y - by - 6.0) / 16.0).floor() as usize)
            .collect();
        rows.sort_unstable();
        rows.dedup();
        rows.len()
    }

    #[test]
    fn without_a_hovered_target_the_box_shows_instructions() {
        let state = rot();
        for hover in [None, Some(unit_hex(&state, 0)), Some(unit_hex(&state, 3)), Some(hex(0, 0))] {
            let verts = build_scene(&targeting(&state, hover));
            assert!(has_row(&verts, 0, PREVIEW_COST, "ROTATION: P1 PICKS A MARKED ALLY TO SWAP HEXES. ESC OR 2 CANCELS."), "{hover:?}");
            assert!(has_row(&verts, 1, PREVIEW_NOTE, "COST AP 3 FAT 25 (P1 ONLY). NO OPPORTUNITY ATTACKS."));
            assert!(has_row(&verts, 2, PREVIEW_NOTE, "HOVER A MARKED ALLY TO PREVIEW BOTH DESTINATIONS."));
            assert_eq!(preview_text_rows(&verts), 3);
        }
        // Outside targeting the box is empty for a non-destination hover.
        let verts = build_scene(&hovering(&state, Some(0), Some(unit_hex(&state, 1)), false));
        assert_eq!(preview_text_rows(&verts), 0);
    }

    #[test]
    fn every_possible_rotation_row_fits_four_rows_the_box_width_and_supported_glyphs() {
        let state = stock();
        let limit = PREVIEW_BOX.2 - 16.0;
        let fallback = crate::font::glyph('\u{1}');
        let supported = |c: char| c == ' ' || crate::font::glyph(c) != fallback;
        let check = |lines: Vec<(String, Rgba)>, what: &str| {
            assert!(!lines.is_empty() && lines.len() <= 4, "{what}: {} rows", lines.len());
            for (text, _) in &lines {
                assert!(Painter::text_width(2.0, text) <= limit, "{what}: {text} is too wide");
                if let Some(c) = text.chars().find(|c| !supported(*c)) {
                    panic!("{what}: unsupported glyph {c:?} in {text:?}");
                }
            }
        };
        for actor in 0..6u32 {
            check(rotation_target_lines(&state, actor, None), "instructions");
            for target in 0..6u32 {
                for a in map_hexes() {
                    for b in [hex(-4, 4), hex(4, -4), hex(-4, 0), hex(0, 0)] {
                        for n in 0..=2usize {
                            let mut edge = vec![(actor, b), (target, a)];
                            edge.sort_unstable_by_key(|(id, _)| *id);
                            edge.truncate(n);
                            let plan = RotationPreview {
                                unit: actor,
                                target,
                                unit_to: a,
                                target_to: b,
                                ap_cost: u32::MAX,
                                fatigue_cost: u32::MAX,
                                edge_penalties: edge,
                            };
                            let lines = rotation_target_lines(&state, actor, Some(&plan));
                            assert_eq!(lines.len(), 2 + n);
                            check(lines, "preview");
                        }
                    }
                }
            }
        }
    }

    #[test]
    fn overlay_preferences_are_preserved_while_targeting() {
        let mut state = bb_rules::scenario::rotation_battle(1, bb_rules::scenario::TerrainPreset::Hills);
        state.units[3].pos = hex(-2, 3); // an enemy whose control P1 stands in
        let make = |rotating: bool, zoc: bool, heights: bool| Scene {
            targeting: rotating.then_some(TargetAction::Rotation),
            control_overlay: zoc,
            height_overlay: heights,
            ..scene(&state, Some(0), false)
        };
        for (zoc, heights) in [(false, false), (true, false), (false, true), (true, true)] {
            let (plain, rotating) = (build_scene(&make(false, zoc, heights)), build_scene(&make(true, zoc, heights)));
            assert_eq!(count_color(&rotating, CONTROL_MARKER), count_color(&plain, CONTROL_MARKER), "ZoC {zoc}");
            assert_eq!(count_color(&rotating, HEIGHT_BADGE), count_color(&plain, HEIGHT_BADGE), "heights {heights}");
            assert_eq!(count_color(&rotating, CONTROL_MARKER) > 0, zoc);
            // The legend always uses the badge colour, so compare against the heights-off scene.
            let off = build_scene(&make(true, zoc, false));
            assert_eq!(count_color(&rotating, HEIGHT_BADGE) > count_color(&off, HEIGHT_BADGE), heights);
            // The overlays' own legend text is unchanged too.
            let legend = height_legend_lines(heights);
            assert!(text_geometry(&rotating, HEIGHT_LEGEND.0, HEIGHT_LEGEND.1, 2.0, legend[0].1, legend[0].0).is_some());
        }
    }

    #[test]
    fn modal_banner_and_panel_stay_on_top_of_and_unchanged_by_targeting() {
        let state = rot();
        let sc = Scene { confirm: Some(ConfirmAction::EndRound), ..targeting(&state, None) };
        let verts = build_scene(&sc);
        let cancel = button_run_labelled(layout::CANCEL_BUTTON, CANCEL_FILL, "CANCEL (ESC)");
        let tail = &verts[verts.len() - cancel.len()..];
        assert!(tail.iter().zip(&cancel).all(|(a, b)| same(a, b)), "the modal is drawn last");
        // The panel rows and resource text are the same with and without targeting.
        for row in 0..3u32 {
            let u = &state.units[row as usize];
            let res = format!("AP {}/{} FAT {}/{}", u.ap, u.stats.ap_max, u.fatigue, u.stats.fatigue_max);
            let col = if row == 0 { YELLOW } else { WHITE };
            assert!(text_geometry(&verts, PANEL_X, panel_row_y(row) + 36.0, 2.0, col, &res).is_some());
        }
        // A finished battle never shows targeting even if the client still held an intent.
        let mut over = rot();
        over.outcome = Some(Outcome::Victory);
        let verts = build_scene(&targeting(&over, None));
        assert!(!verts.iter().any(|v| v.color == SWAP_FILL || v.color == SWAP_TEXT));
        assert!(text_geometry(&verts, PREVIEW_BOX.0 + 16.0, PREVIEW_BOX.1 + 8.0, 5.0, GREEN, "VICTORY").is_some());
    }

    #[test]
    fn rotation_scene_is_pure() {
        for hover in [None, Some(hex(-4, 3)), Some(hex(-3, 2))] {
            let state = rot();
            let (hash, rng, units, queue, elev) = (state.state_hash(), state.rng.clone(), state.units.clone(), state.queue.clone(), state.elevations.clone());
            let a = build_scene(&targeting(&state, hover));
            let b = build_scene(&targeting(&state, hover));
            assert!(a.len() == b.len() && a.iter().zip(&b).all(|(x, y)| same(x, y)));
            assert_eq!((state.state_hash(), &state.rng, &state.units, &state.queue, &state.elevations), (hash, &rng, &units, &queue, &elev));
        }
    }

    // --- Footwork button and destination-targeting mode --------------------------------

    /// Stock scenario with the opt-in Footwork loadout: P1 (-3,3) is active.
    fn fw() -> BattleState {
        bb_rules::scenario::footwork_battle(1, bb_rules::scenario::TerrainPreset::Flat)
    }

    fn stepping<'a>(state: &'a BattleState, hover: Option<Hex>) -> Scene<'a> {
        Scene { targeting: Some(TargetAction::Footwork), hover, ..scene(state, Some(0), false) }
    }

    fn step_label_drawn(verts: &[Vertex], at: Hex) -> bool {
        let c = layout::hex_to_pixel(at);
        text_geometry(verts, c.0 + STEP_LABEL_OFFSET.0, c.1 + STEP_LABEL_OFFSET.1, 1.0, STEP_TEXT, "STEP").is_some()
    }

    fn step_markers_drawn(verts: &[Vertex]) -> usize {
        map_hexes().into_iter().filter(|&h| step_label_drawn(verts, h)).count()
    }

    #[test]
    fn footwork_button_states_follow_the_rules_legal_destinations() {
        let state = fw();
        let sc = scene(&state, Some(0), false);
        assert!(footwork_enabled(&sc));
        assert_eq!(footwork_targeting_actor(&sc), None, "enabled is not targeting");
        let verts = build_scene(&sc);
        assert!(contains_run(&verts, &footwork_button_run(SkillLook::Enabled)));
        assert_eq!(skill_fill(&verts, layout::FOOTWORK_BUTTON), Some(SKILL_ENABLED));
        // The three buttons are independent: this loadout has only Footwork.
        assert_eq!(skill_fill(&verts, layout::ADRENALINE_BUTTON), Some(SKILL_DISABLED));
        assert_eq!(skill_fill(&verts, layout::ROTATION_BUTTON), Some(SKILL_DISABLED));

        let mut cases: Vec<(&str, BattleState, Option<u32>)> = Vec::new();
        cases.push(("default scenario has no capability", stock(), Some(0)));
        cases.push(("nothing selected", fw(), None));
        cases.push(("enemy selected", fw(), Some(3)));
        let mut c = fw();
        c.units[0].ap = FOOTWORK_AP - 1;
        cases.push(("not enough AP", c, Some(0)));
        let mut c = fw();
        c.units[0].fatigue = c.units[0].stats.fatigue_max - FOOTWORK_FATIGUE + 1;
        cases.push(("fatigue over the cap", c, Some(0)));
        let mut blocked = fw();
        blocked.units[0].pos = hex(-4, 4); // a map corner with three neighbours
        let free = blocked.legal_footwork_destinations(0);
        assert!(free.len() <= 3, "the three enemies can fill every legal destination: {free:?}");
        for (i, h) in free.into_iter().enumerate() {
            blocked.units[3 + i].pos = h;
        }
        assert!(blocked.legal_footwork_destinations(0).is_empty());
        cases.push(("no free adjacent hex", blocked, Some(0)));
        let mut c = fw();
        c.queue = vec![3, 0];
        cases.push(("enemy turn", c, Some(0)));
        let mut c = fw();
        c.outcome = Some(Outcome::Victory);
        cases.push(("battle over", c, Some(0)));
        let mut c = fw();
        c.units[0].morale = Morale::Fleeing;
        cases.push(("fleeing", c, Some(0)));
        let mut c = fw();
        c.units[0].retreating = true;
        cases.push(("retreating", c, Some(0)));
        let mut c = fw();
        c.units[0].alive = false;
        cases.push(("dead", c, Some(0)));
        let mut c = fw();
        c.units[0].fled = true;
        cases.push(("escaped", c, Some(0)));
        for (what, st, selected) in &cases {
            let sc = scene(st, *selected, false);
            assert!(!footwork_enabled(&sc), "{what}");
            let verts = build_scene(&sc);
            assert!(contains_run(&verts, &footwork_button_run(SkillLook::Disabled)), "{what}");
            assert_eq!(skill_fill(&verts, layout::FOOTWORK_BUTTON), Some(SKILL_DISABLED), "{what}");
            // Requesting targeting on an ineligible scene draws the ordinary display instead.
            let sc = Scene { targeting: Some(TargetAction::Footwork), ..scene(st, *selected, false) };
            assert_eq!(footwork_targeting_actor(&sc), None, "{what}");
            assert!(!build_scene(&sc).iter().any(|v| v.color == STEP_FILL || v.color == STEP_HOVER || v.color == STEP_TEXT), "{what}");
        }
        // The exact boundary that still passes.
        let mut c = fw();
        c.units[0].ap = FOOTWORK_AP;
        c.units[0].fatigue = c.units[0].stats.fatigue_max - FOOTWORK_FATIGUE;
        assert!(footwork_enabled(&scene(&c, Some(0), false)));
    }

    #[test]
    fn footwork_button_uses_the_active_look_only_for_its_own_open_mode() {
        let mut state = fw();
        for u in state.units.iter_mut() {
            u.has_rotation = u.side == Side::Player;
        }
        let verts = build_scene(&stepping(&state, None));
        assert!(contains_run(&verts, &footwork_button_run(SkillLook::Active)));
        assert_eq!(skill_fill(&verts, layout::FOOTWORK_BUTTON), Some(SKILL_ACTIVE));
        assert_eq!(skill_fill(&verts, layout::ROTATION_BUTTON), Some(SKILL_ENABLED), "Rotation stays available, not active");
        assert_eq!(footwork_targeting_actor(&stepping(&state, None)), Some(0));
        assert_eq!(rotation_targeting_actor(&stepping(&state, None)), None);
        let verts = build_scene(&targeting(&state, None));
        assert_eq!(skill_fill(&verts, layout::ROTATION_BUTTON), Some(SKILL_ACTIVE));
        assert_eq!(skill_fill(&verts, layout::FOOTWORK_BUTTON), Some(SKILL_ENABLED));
        assert_eq!(footwork_targeting_actor(&targeting(&state, None)), None);
        assert_eq!(targeting_mode(&targeting(&state, None)), Some((TargetAction::Rotation, 0)));
        assert_eq!(targeting_mode(&stepping(&state, None)), Some((TargetAction::Footwork, 0)));
    }

    #[test]
    fn footwork_button_is_drawn_exactly_on_the_shared_hit_rectangle_and_shows_rules_costs() {
        for look in [SkillLook::Disabled, SkillLook::Enabled, SkillLook::Active] {
            let verts = footwork_button_run(look);
            assert!(close(skill_button_border(&verts, layout::FOOTWORK_BUTTON), rect_bounds(layout::FOOTWORK_BUTTON)), "{look:?}");
            assert!(close(bounds(verts.iter().map(to_px)), rect_bounds(layout::FOOTWORK_BUTTON)), "{look:?}: all geometry is inside");
        }
        assert_eq!(footwork_info_lines(), ["COST 3 AP 20 FAT".to_string(), "STEP TO ADJACENT HEX".to_string()]);
        assert_eq!((FOOTWORK_AP, FOOTWORK_FATIGUE), (3, 20));
        let r = layout::FOOTWORK_BUTTON;
        for (state, text) in [(fw(), WHITE), (stock(), BUTTON_DISABLED_TEXT)] {
            let verts = build_scene(&scene(&state, Some(0), false));
            assert!(text_geometry(&verts, r.x + SKILL_TEXT_X, r.y + SKILL_TITLE_DY, layout::SKILL_TITLE_SCALE, text, "FOOTWORK (3)").is_some());
            assert!(text_geometry(&verts, r.x + SKILL_TEXT_X, r.y + SKILL_INFO_DY, 1.0, text, "COST 3 AP 20 FAT").is_some());
            assert!(text_geometry(&verts, r.x + SKILL_TEXT_X, r.y + SKILL_INFO_DY + 8.0, 1.0, text, "STEP TO ADJACENT HEX").is_some());
        }
    }

    #[test]
    fn all_four_skill_buttons_fit_labels_rows_and_supported_glyphs() {
        let fallback = crate::font::glyph('\u{1}');
        let supported = |c: char| c == ' ' || crate::font::glyph(c) != fallback;
        let all = [
            (layout::ADRENALINE_BUTTON, layout::ADRENALINE_LABEL, adrenaline_info_lines()),
            (layout::ROTATION_BUTTON, layout::ROTATION_LABEL, rotation_info_lines()),
            (layout::FOOTWORK_BUTTON, layout::FOOTWORK_LABEL, footwork_info_lines()),
            (layout::INDOMITABLE_BUTTON, layout::INDOMITABLE_LABEL, indomitable_info_lines()),
        ];
        for (r, label, info) in &all {
            assert!(SKILL_TEXT_X + Painter::text_width(layout::SKILL_TITLE_SCALE, label) + 8.0 <= r.w, "{label}");
            for line in info {
                assert!(SKILL_TEXT_X + Painter::text_width(1.0, line) + 8.0 <= r.w, "{line}");
            }
            for text in info.iter().map(String::as_str).chain([*label]) {
                if let Some(c) = text.chars().find(|c| !supported(*c)) {
                    panic!("unsupported glyph {c:?} in {text:?}");
                }
            }
        }
        // Every text row of each button is drawn inside its own rectangle, clear of its neighbours.
        let mut state = fw();
        for u in state.units.iter_mut() {
            u.has_rotation = u.side == Side::Player;
            u.has_adrenaline = u.side == Side::Player;
            u.has_indomitable = u.side == Side::Player;
        }
        let verts = build_scene(&scene(&state, Some(0), false));
        for (r, label, info) in &all {
            let geo = text_geometry(&verts, r.x + SKILL_TEXT_X, r.y + SKILL_TITLE_DY, layout::SKILL_TITLE_SCALE, WHITE, label).expect("label drawn");
            let b = bounds(geo.into_iter());
            assert!(b.0 >= r.x && b.2 <= r.right() && b.1 >= r.y && b.3 <= r.bottom(), "{label}: {b:?}");
            for (i, line) in info.iter().enumerate() {
                let geo = text_geometry(&verts, r.x + SKILL_TEXT_X, r.y + SKILL_INFO_DY + i as f32 * 8.0, 1.0, WHITE, line).expect("row drawn");
                let b = bounds(geo.into_iter());
                assert!(b.0 >= r.x && b.2 <= r.right() && b.1 >= r.y && b.3 <= r.bottom(), "{line}: {b:?}");
            }
        }
    }

    #[test]
    fn legal_destinations_get_a_step_marker_and_nothing_else_does() {
        let state = fw();
        let legal = state.legal_footwork_destinations(0);
        assert!(!legal.is_empty());
        let verts = build_scene(&stepping(&state, None));
        assert_eq!(swap_hex_fills(&verts, STEP_FILL), legal.len(), "one fill per legal destination");
        assert_eq!(swap_hex_fills(&verts, STEP_HOVER), 0);
        assert_eq!(step_markers_drawn(&verts), legal.len());
        for &h in &legal {
            assert!(step_label_drawn(&verts, h), "{h:?}");
        }
        for h in map_hexes().into_iter().filter(|h| !legal.contains(h)) {
            assert!(!step_label_drawn(&verts, h), "{h:?} is not a legal destination");
        }
        // Distinct from the Rotation marker colours, and no Rotation marker exists.
        assert!(![STEP_FILL, STEP_HOVER, STEP_TEXT].iter().any(|c| [SWAP_FILL, SWAP_HOVER, SWAP_TEXT, GREEN, RED].contains(c)));
        assert!(!verts.iter().any(|v| v.color == SWAP_FILL || v.color == SWAP_HOVER || v.color == SWAP_TEXT));
        // Occupied or blocked neighbours are not marked: the rules query decides.
        let mut crowded = fw();
        crowded.units[1].pos = legal[0];
        let verts = build_scene(&stepping(&crowded, None));
        assert_eq!(swap_hex_fills(&verts, STEP_FILL), crowded.legal_footwork_destinations(0).len());
        assert!(!step_label_drawn(&verts, legal[0]));
        // Without targeting no marker exists at all.
        let verts = build_scene(&scene(&state, Some(0), false));
        assert!(!verts.iter().any(|v| v.color == STEP_FILL || v.color == STEP_HOVER || v.color == STEP_TEXT));
        // The hovered legal hex is highlighted differently from the other marked ones.
        let verts = build_scene(&stepping(&state, Some(legal[0])));
        assert_eq!((swap_hex_fills(&verts, STEP_FILL), swap_hex_fills(&verts, STEP_HOVER)), (legal.len() - 1, 1));
        // Hovering an unmarked hex highlights nothing extra.
        let verts = build_scene(&stepping(&state, Some(unit_hex(&state, 3))));
        assert_eq!((swap_hex_fills(&verts, STEP_FILL), swap_hex_fills(&verts, STEP_HOVER)), (legal.len(), 0));
    }

    #[test]
    fn step_label_stays_inside_its_empty_hex_clear_of_overlays() {
        let c = (0.0f32, 0.0f32);
        let label = (c.0 + STEP_LABEL_OFFSET.0, c.1 + STEP_LABEL_OFFSET.1, c.0 - STEP_LABEL_OFFSET.0, c.1 + STEP_LABEL_OFFSET.1 + 7.0);
        assert_eq!(label.2 - label.0, Painter::text_width(1.0, "STEP"));
        assert!(label.0 > -28.0 && label.2 < 28.0 && label.1 > -28.0 && label.3 < 28.0, "inside the hex");
        let marker = (c.0 - 5.0, c.1 - 33.0, c.0 + 5.0, c.1 - 23.0);
        let badge = (c.0 - 28.25, c.1 - 16.25, c.0 - 21.25, c.1 - 7.25);
        assert!(boxes_apart(label, marker) && boxes_apart(label, badge));
        // The Z and H overlays still draw while stepping, in the same amount as without targeting.
        let mut state = bb_rules::scenario::footwork_battle(1, bb_rules::scenario::TerrainPreset::Hills);
        state.units[3].pos = hex(-2, 3);
        let make = |stepping: bool, zoc: bool, heights: bool| Scene {
            targeting: stepping.then_some(TargetAction::Footwork),
            control_overlay: zoc,
            height_overlay: heights,
            ..scene(&state, Some(0), false)
        };
        for (zoc, heights) in [(false, false), (true, false), (false, true), (true, true)] {
            let (plain, step) = (build_scene(&make(false, zoc, heights)), build_scene(&make(true, zoc, heights)));
            assert_eq!(count_color(&step, CONTROL_MARKER), count_color(&plain, CONTROL_MARKER), "ZoC {zoc}");
            assert_eq!(count_color(&step, HEIGHT_BADGE), count_color(&plain, HEIGHT_BADGE), "heights {heights}");
            assert_eq!(count_color(&step, CONTROL_MARKER) > 0, zoc);
            let off = build_scene(&make(true, zoc, false));
            assert_eq!(count_color(&step, HEIGHT_BADGE) > count_color(&off, HEIGHT_BADGE), heights);
            // Every STEP label keeps clear of the hex's own height badge and ZoC marker.
            for h in state.legal_footwork_destinations(0) {
                assert!(step_label_drawn(&step, h));
            }
        }
    }

    const FW_ROW0: &str = "STEP P1  COST AP 3 FAT 20 (P1 ONLY)";

    #[test]
    fn hovered_destination_preview_shows_cost_coordinates_and_the_edge_warning() {
        // P1 starts at (-3,3); (-4,3)/(-3,4)-style neighbours on the map edge predict the penalty.
        let state = fw();
        let legal = state.legal_footwork_destinations(0);
        let edge = legal.iter().copied().find(|&h| state.preview_footwork(0, h).unwrap().edge_penalty.is_some()).expect("an edge destination");
        let plain = legal.iter().copied().find(|&h| state.preview_footwork(0, h).unwrap().edge_penalty.is_none()).expect("an interior destination");

        let pv = state.preview_footwork(0, edge).unwrap();
        let verts = build_scene(&stepping(&state, Some(edge)));
        assert!(has_row(&verts, 0, PREVIEW_COST, FW_ROW0));
        let route = format!("P1 FROM (-3,3) TO ({},{})  NO OPPORTUNITY ATTACKS  ESC OR 3 CANCELS", pv.to.q, pv.to.r);
        assert!(has_row(&verts, 1, PREVIEW_NOTE, &route));
        let warn = format!("! P1 ARRIVES AT EDGE ({},{}): MORALE BECOMES BREAKING", edge.q, edge.r);
        assert!(has_row(&verts, 2, PREVIEW_WARN, &warn));
        assert_eq!(preview_text_rows(&verts), 3);
        assert!(!has_row_prefix(&verts, 0, "FOOTWORK:"), "the hovered preview replaces the instructions");
        assert!(!verts.iter().any(|v| v.color == SWAP_FILL || v.color == SWAP_TEXT));

        let pv = state.preview_footwork(0, plain).unwrap();
        let verts = build_scene(&stepping(&state, Some(plain)));
        assert!(has_row(&verts, 0, PREVIEW_COST, FW_ROW0));
        let route = format!("P1 FROM (-3,3) TO ({},{})  NO OPPORTUNITY ATTACKS  ESC OR 3 CANCELS", pv.to.q, pv.to.r);
        assert!(has_row(&verts, 1, PREVIEW_NOTE, &route));
        assert_eq!(preview_text_rows(&verts), 2);
        assert!(!verts.iter().any(|v| v.color == PREVIEW_WARN));

        // Text stays inside the preview box, clear of the error bar and the side panel.
        let (bx, by, bw, bh) = PREVIEW_BOX;
        let mut sc = stepping(&state, Some(edge));
        sc.error = Some("NOT A STEP HEX");
        let verts = build_scene(&sc);
        let colours = preview_colours();
        let text: Vec<(f32, f32)> = verts.iter().filter(|v| colours[1..].contains(&v.color)).map(to_px).collect();
        let (max_x, max_y) = text.iter().fold((0.0f32, 0.0f32), |m, p| (m.0.max(p.0), m.1.max(p.1)));
        assert!(max_x <= bx + bw - 8.0 && max_y <= by + bh - 4.0, "text reaches the box edge: {max_x},{max_y}");
    }

    #[test]
    fn preview_copies_the_rules_edge_prediction_and_never_implies_extraction() {
        let state = fw();
        for h in state.legal_footwork_destinations(0) {
            let pv = state.preview_footwork(0, h).unwrap();
            let lines = footwork_target_lines(&state, 0, Some(&pv));
            assert_eq!(lines.len(), 2 + usize::from(pv.edge_penalty.is_some()), "{h:?}");
            for (t, _) in &lines {
                assert!(!["ESCAPE", "EXIT", "FLEE", "EXTRACT", "LEAVE"].iter().any(|w| t.contains(w)), "{t}");
            }
        }
    }

    #[test]
    fn without_a_hovered_destination_the_box_shows_footwork_instructions() {
        let state = fw();
        let legal = state.legal_footwork_destinations(0);
        let unmarked = map_hexes().into_iter().find(|h| !legal.contains(h) && *h != unit_hex(&state, 0)).unwrap();
        for hover in [None, Some(unit_hex(&state, 0)), Some(unit_hex(&state, 3)), Some(unmarked)] {
            let verts = build_scene(&stepping(&state, hover));
            assert!(has_row(&verts, 0, PREVIEW_COST, "FOOTWORK: P1 PICKS A MARKED HEX TO STEP TO. ESC OR 3 CANCELS."), "{hover:?}");
            assert!(has_row(&verts, 1, PREVIEW_NOTE, "COST AP 3 FAT 20 (P1 ONLY). NO OPPORTUNITY ATTACKS."));
            assert!(has_row(&verts, 2, PREVIEW_NOTE, "HOVER A MARKED HEX TO PREVIEW THE STEP."));
            assert_eq!(preview_text_rows(&verts), 3);
        }
    }

    #[test]
    fn every_possible_footwork_row_fits_four_rows_the_box_width_and_supported_glyphs() {
        let state = stock();
        let limit = PREVIEW_BOX.2 - 16.0;
        let fallback = crate::font::glyph('\u{1}');
        let supported = |c: char| c == ' ' || crate::font::glyph(c) != fallback;
        let check = |lines: Vec<(String, Rgba)>, what: &str| {
            assert!(!lines.is_empty() && lines.len() <= 4, "{what}: {} rows", lines.len());
            for (text, _) in &lines {
                assert!(Painter::text_width(2.0, text) <= limit, "{what}: {text} is too wide");
                if let Some(c) = text.chars().find(|c| !supported(*c)) {
                    panic!("{what}: unsupported glyph {c:?} in {text:?}");
                }
            }
        };
        for actor in 0..6u32 {
            check(footwork_target_lines(&state, actor, None), "instructions");
            for from in map_hexes() {
                for to in map_hexes() {
                    for edge in [None, Some(to)] {
                        let plan = FootworkPreview { unit: actor, from, to, ap_cost: u32::MAX, fatigue_cost: u32::MAX, edge_penalty: edge };
                        let lines = footwork_target_lines(&state, actor, Some(&plan));
                        assert_eq!(lines.len(), 2 + usize::from(edge.is_some()));
                        check(lines, "preview");
                    }
                }
            }
        }
    }

    #[test]
    fn footwork_targeting_suppresses_movement_highlights_previews_attack_labels_and_rotation_markers() {
        let mut state = fw();
        for u in state.units.iter_mut() {
            u.has_rotation = u.side == Side::Player;
        }
        state.units[3].pos = hex(-2, 3); // an enemy next to P1 in attack range
        let reach = state.reachable(0);
        assert!(!reach.is_empty());
        let step = reach[0].0;
        let ordinary = |hover: Hex| Scene { hover: Some(hover), reach: &reach, ..scene(&state, Some(0), false) };
        let stepping = |hover: Hex| Scene { targeting: Some(TargetAction::Footwork), ..ordinary(hover) };
        let plan = state.preview_move(0, step).expect("a reachable destination previews");
        let rows = movement_preview_lines(&plan);
        let c = layout::hex_to_pixel(step);
        let cost = reach[0].1.to_string();
        let enemy = unit_hex(&state, 3);
        let ec = layout::hex_to_pixel(enemy);
        let hit = format!("HIT {}%", state.hit_chance(0, 3));

        let verts = build_scene(&ordinary(step));
        assert!(count_color(&verts, GREEN) > 0);
        assert!(text_geometry(&verts, c.0 - 6.0, c.1 - 7.0, 2.0, [0.05, 0.2, 0.05, 1.0], &cost).is_some());
        assert!(rows.iter().enumerate().all(|(i, (t, col))| has_row(&verts, i, *col, t)));
        assert!(text_geometry(&build_scene(&ordinary(enemy)), ec.0 - 36.0, ec.1 - 46.0, 2.0, YELLOW, &hit).is_some());

        for hover in [step, enemy, unit_hex(&state, 1)] {
            let verts = build_scene(&stepping(hover));
            assert_eq!(count_color(&verts, GREEN), 0, "no movement highlight at {hover:?}");
            assert!(text_geometry(&verts, c.0 - 6.0, c.1 - 7.0, 2.0, [0.05, 0.2, 0.05, 1.0], &cost).is_none());
            assert!(!rows.iter().any(|(t, col)| has_row(&verts, 0, *col, t) || has_row(&verts, 1, *col, t)));
            assert!(text_geometry(&verts, ec.0 - 36.0, ec.1 - 46.0, 2.0, YELLOW, &hit).is_none(), "no attack label");
            assert!(!has_row_prefix(&verts, 0, "MOVE AP"));
            assert!(!verts.iter().any(|v| v.color == SWAP_FILL || v.color == SWAP_HOVER || v.color == SWAP_TEXT), "no Rotation marker");
        }
        assert!(movement_preview(&stepping(step)).is_none());
        assert!(movement_preview(&ordinary(step)).is_some());
        // Rotation targeting never shows STEP markers either.
        let rotating = Scene { targeting: Some(TargetAction::Rotation), ..ordinary(step) };
        assert!(!build_scene(&rotating).iter().any(|v| v.color == STEP_FILL || v.color == STEP_HOVER || v.color == STEP_TEXT));
    }

    #[test]
    fn modal_banner_and_panel_stay_on_top_of_and_unchanged_by_footwork_targeting() {
        let state = fw();
        let sc = Scene { confirm: Some(ConfirmAction::Retreat), ..stepping(&state, None) };
        let verts = build_scene(&sc);
        let cancel = button_run_labelled(layout::CANCEL_BUTTON, CANCEL_FILL, "CANCEL (ESC)");
        let tail = &verts[verts.len() - cancel.len()..];
        assert!(tail.iter().zip(&cancel).all(|(a, b)| same(a, b)), "the modal is drawn last");
        for row in 0..3u32 {
            let u = &state.units[row as usize];
            let res = format!("AP {}/{} FAT {}/{}", u.ap, u.stats.ap_max, u.fatigue, u.stats.fatigue_max);
            let col = if row == 0 { YELLOW } else { WHITE };
            assert!(text_geometry(&verts, PANEL_X, panel_row_y(row) + 36.0, 2.0, col, &res).is_some());
        }
        let mut over = fw();
        over.outcome = Some(Outcome::Victory);
        let verts = build_scene(&stepping(&over, None));
        assert!(!verts.iter().any(|v| v.color == STEP_FILL || v.color == STEP_TEXT));
        assert!(text_geometry(&verts, PREVIEW_BOX.0 + 16.0, PREVIEW_BOX.1 + 8.0, 5.0, GREEN, "VICTORY").is_some());
    }

    #[test]
    fn footwork_scene_is_pure() {
        let state = fw();
        for hover in [None, Some(hex(-4, 3)), Some(hex(-3, 2)), Some(hex(-2, 3))] {
            let (hash, rng, units, queue, elev) = (state.state_hash(), state.rng.clone(), state.units.clone(), state.queue.clone(), state.elevations.clone());
            let a = build_scene(&stepping(&state, hover));
            let b = build_scene(&stepping(&state, hover));
            assert!(a.len() == b.len() && a.iter().zip(&b).all(|(x, y)| same(x, y)));
            assert_eq!((state.state_hash(), &state.rng, &state.units, &state.queue, &state.elevations), (hash, &rng, &units, &queue, &elev));
        }
    }

    // --- Fast Adaptation stack indicator ------------------------------------------------

    fn fa_args() -> bb_rules::scenario::LaunchArgs {
        bb_rules::scenario::LaunchArgs {
            seed: 1,
            terrain: bb_rules::scenario::TerrainPreset::Flat,
            adrenaline: false,
            rotation: false,
            footwork: false,
            fast_adaptation: true,
            dodge: false,
            nine_lives: false,
            berserk: false,
            pathfinder: false,
            indomitable: false,
            two_hex: false,
            stunning_blow: false, killing_frenzy: false,
        }
    }

    /// Expected label geometry, derived independently of the production box helper: scale 1,
    /// right edge 8 px inside the window, 3 px below the top of the HP/armour line.
    fn adapt_run(row: u32, stacks: u32) -> Vec<Vertex> {
        let label = format!("ADAPT {stacks}");
        let x = WIN_W - 8.0 - (label.chars().count() as f32 * 6.0 - 1.0);
        text_run(x, panel_row_y(row) + 18.0 + 3.0, 1.0, ADAPT_MARKER_TEXT, &label)
    }

    fn adapt_rows(verts: &[Vertex]) -> usize {
        (0..6).filter(|&r| verts_has_row(verts, r)).count()
    }

    /// True when any `ADAPT ...` text starts on `row`'s HP/armour line (any stack digits).
    fn verts_has_row(verts: &[Vertex], row: u32) -> bool {
        let (top, bottom) = (panel_row_y(row) + 18.0, panel_row_y(row) + 32.0);
        verts.iter().filter(|v| v.color == ADAPT_MARKER_TEXT).map(to_px).any(|p| p.1 >= top && p.1 <= bottom)
    }

    #[test]
    fn adaptation_indicator_shows_zero_and_counts_for_capable_present_rows_of_both_factions() {
        let mut state = bb_rules::scenario::launch_battle_for(fa_args());
        let verts = build_scene(&scene(&state, Some(0), false));
        for row in 0..3 {
            assert!(contains_run(&verts, &adapt_run(row, 0)), "player row {row} shows ADAPT 0");
        }
        assert_eq!(adapt_rows(&verts), 3, "enemies are incapable under the launch option");
        state.units[1].fast_adaptation_stacks = 7;
        state.units[2].fast_adaptation_stacks = u32::MAX;
        // Enemies can carry the capability through scripted state and then show it too.
        state.units[4].has_fast_adaptation = true;
        state.units[4].fast_adaptation_stacks = 12;
        let verts = build_scene(&scene(&state, None, false));
        for (row, stacks) in [(0, 0), (1, 7), (2, u32::MAX), (4, 12)] {
            assert!(contains_run(&verts, &adapt_run(row, stacks)), "row {row} shows ADAPT {stacks}");
        }
        assert_eq!(adapt_rows(&verts), 4);
    }

    #[test]
    fn adaptation_indicator_is_omitted_for_incapable_dead_and_escaped_rows() {
        let mut state = bb_rules::scenario::launch_battle_for(fa_args());
        for u in &mut state.units {
            u.fast_adaptation_stacks = 5;
        }
        state.units[0].has_fast_adaptation = false; // stacks are stored but ignored: no marker
        state.units[1].alive = false;
        state.units[2].fled = true;
        let verts = build_scene(&scene(&state, Some(0), false));
        assert_eq!(adapt_rows(&verts), 0);
        assert_eq!(count_color(&verts, ADAPT_MARKER_TEXT), 0);
        assert!(text_geometry(&verts, PANEL_X, panel_row_y(1), 2.0, DIM, "P2 DEAD").is_some());
        assert!(text_geometry(&verts, PANEL_X, panel_row_y(2), 2.0, FLED, "P3 FLED").is_some());
        // The default scenario has no capable unit at all.
        let plain = BattleState::new(1);
        assert_eq!(count_color(&build_scene(&scene(&plain, Some(0), false)), ADAPT_MARKER_TEXT), 0);
    }

    #[test]
    fn longest_adaptation_label_fits_and_clears_row_text_and_the_pending_marker() {
        let label = format!("ADAPT {}", u32::MAX);
        assert_eq!(label, "ADAPT 4294967295");
        let w = Painter::text_width(1.0, &label);
        assert_eq!(w, 95.0);
        let mut state = bb_rules::scenario::launch_battle_for(fa_args());
        for u in 0..3 {
            state.units[u].fast_adaptation_stacks = u32::MAX;
            state.units[u].adrenaline_pending = true;
        }
        for morale in [Morale::Fleeing, Morale::Wavering, Morale::Breaking, Morale::Confident, Morale::Steady] {
            state.units[0].morale = morale;
            let verts = build_scene(&scene(&state, Some(0), false));
            for row in 0..3u32 {
                let y = panel_row_y(row);
                let run = adapt_run(row, u32::MAX);
                assert!(contains_run(&verts, &run), "{morale:?} row {row}");
                let m = bounds(run.iter().map(to_px));
                assert!(m.0 >= PANEL_X && m.2 <= WIN_W - 8.0 + 0.01, "inside the panel: {m:?}");
                assert!(m.1 >= y + 18.0 && m.3 <= y + 32.0 + 0.01, "within the HP/armour line: {m:?}");
                // Stock HP/armour text ends left of the marker.
                let u = &state.units[row as usize];
                let hp = format!("HP {}/{} ARM {}/{}", u.hp, u.stats.hp, u.armour, u.stats.armour);
                let col = if row == 0 { YELLOW } else { WHITE };
                let hp_geo = text_geometry(&verts, PANEL_X, y + 18.0, 2.0, col, &hp).expect("hp text");
                assert!(bounds(hp_geo.into_iter()).2 + 8.0 <= m.0);
                // Name and morale label sit on the row's first line, above the marker.
                let (label_text, morale_color) = morale_panel_label(u.morale);
                let morale_x = PANEL_X + Painter::text_width(2.0, u.name) + 10.0;
                let mg = text_geometry(&verts, morale_x, y, 2.0, morale_color, &label_text).expect("morale label");
                let mb = bounds(mg.into_iter());
                assert!(mb.3 < m.1 || mb.2 < m.0, "morale label {mb:?} vs marker {m:?}");
                // The pending-Adrenaline marker on the AP line is a separate, disjoint box.
                let p = bounds(pending_marker_run(row).iter().map(to_px));
                assert!(m.3 < p.1, "marker {m:?} above pending box {p:?}");
                // The next row starts below this marker.
                assert!(m.3 < panel_row_y(row + 1));
            }
        }
    }

    #[test]
    fn adaptation_label_uses_only_supported_glyphs() {
        let fallback = crate::font::glyph('\u{1}');
        let supported = |text: &str| text.chars().all(|c| c == ' ' || font::glyph(c) != fallback);
        assert!(!supported("ADAPT \u{1}"), "the predicate rejects an unsupported character");
        assert!(!supported("ADAPT é"));
        for stacks in [0, 9, 10, 4_294_967_295, u32::MAX] {
            let label = adaptation_marker_label(stacks);
            assert!(supported(&label), "unsupported glyph in {label:?}");
        }
    }

    /// Duel fixture: capable P1 beside E1 on the lane `r = -2`, everyone else parked, damage 1
    /// so every hit is armour-only and nonlethal.
    fn adapt_duel() -> BattleState {
        let mut s = bb_rules::scenario::launch_battle_for(fa_args());
        let park = [hex(-4, 2), hex(-4, 3), hex(-4, 4), hex(4, -4), hex(4, -3), hex(4, -2)];
        for (u, at) in s.units.iter_mut().zip(park) {
            u.pos = at;
        }
        for u in &mut s.units {
            u.stats.melee_skill = 0;
            u.stats.defense = 0;
            u.stats.dmg_min = 1;
            u.stats.dmg_max = 1;
        }
        s.units[0].pos = hex(0, -2);
        s.units[3].pos = hex(1, -2);
        s.units[0].stats.melee_skill = 50;
        s
    }

    /// Ready P1 with fresh budgets and rig the RNG so the next attack roll is `roll`.
    fn ready_swing(s: &mut BattleState, roll: u32) {
        s.queue.retain(|&q| q != 0);
        s.queue.insert(0, 0);
        s.units[0].ap = 9;
        s.units[0].fatigue = 0;
        s.units[0].turn_started = true;
        for k in 1u64.. {
            let cand = s.rng.state.wrapping_add(k);
            if (bb_rules::Rng::new(cand).next_u64() % 100) as u32 == roll {
                s.rng = bb_rules::Rng::new(cand);
                return;
            }
        }
    }

    fn hit_label_drawn(s: &BattleState) -> Option<u32> {
        let c = layout::hex_to_pixel(s.units[3].pos);
        let verts = build_scene(&hovering(s, Some(0), Some(s.units[3].pos), false));
        let chance = s.hit_chance(0, 3);
        text_geometry(&verts, c.0 - 36.0, c.1 - 46.0, 2.0, YELLOW, &format!("HIT {chance}%")).map(|_| chance)
    }

    #[test]
    fn a_real_miss_raises_the_indicator_and_hit_preview_and_a_hit_resets_both() {
        use bb_rules::Event;
        let mut s = adapt_duel();
        let morale: Vec<Morale> = s.units.iter().map(|u| u.morale).collect();
        let base = s.hit_chance(0, 3);
        assert_eq!(base, 50);
        assert_eq!(hit_label_drawn(&s), Some(base));
        assert!(contains_run(&build_scene(&hovering(&s, Some(0), Some(hex(1, -2)), false)), &adapt_run(0, 0)));

        for expected in 1..=2u32 {
            ready_swing(&mut s, 99);
            let ev = s.apply(Command::Attack { attacker: 0, target: 3 }).unwrap();
            assert!(matches!(ev[0], Event::Attacked { hit: false, .. }), "{ev:?}");
            assert_eq!(s.units[0].fast_adaptation_stacks, expected);
            let verts = build_scene(&hovering(&s, Some(0), Some(hex(1, -2)), false));
            assert!(contains_run(&verts, &adapt_run(0, expected)), "ADAPT {expected} after the miss");
            assert!(!contains_run(&verts, &adapt_run(0, expected - 1)));
            ready_swing_budget(&mut s);
            assert_eq!(hit_label_drawn(&s), Some(base + 10 * expected), "preview follows the counter");
        }

        ready_swing(&mut s, 0);
        let chance = s.hit_chance(0, 3);
        let ev = s.apply(Command::Attack { attacker: 0, target: 3 }).unwrap();
        assert!(matches!(ev[0], Event::Attacked { hit: true, chance: c, .. } if c == chance), "{ev:?}");
        assert_eq!(s.units[0].fast_adaptation_stacks, 0);
        let verts = build_scene(&hovering(&s, Some(0), Some(hex(1, -2)), false));
        assert!(contains_run(&verts, &adapt_run(0, 0)), "ADAPT 0 after the hit");
        ready_swing_budget(&mut s);
        assert_eq!(hit_label_drawn(&s), Some(base));
        assert_eq!(s.units.iter().map(|u| u.morale).collect::<Vec<_>>(), morale, "no morale change intervened");
        assert!(s.units[3].alive && s.units[3].hp == s.units[3].stats.hp, "nonlethal, armour only");
    }

    /// Fresh budgets only, so the preview is not affected by spent AP or fatigue.
    fn ready_swing_budget(s: &mut BattleState) {
        s.units[0].ap = 9;
        s.units[0].fatigue = 0;
    }

    #[test]
    fn adaptation_scene_is_pure() {
        let mut state = adapt_duel();
        state.units[0].fast_adaptation_stacks = u32::MAX;
        state.units[3].has_fast_adaptation = true;
        let (hash, rng, units, queue) = (state.state_hash(), state.rng.clone(), state.units.clone(), state.queue.clone());
        let a = build_scene(&hovering(&state, Some(0), Some(hex(1, -2)), false));
        let b = build_scene(&hovering(&state, Some(0), Some(hex(1, -2)), false));
        assert!(a.len() == b.len() && a.iter().zip(&b).all(|(x, y)| same(x, y)));
        assert_eq!((state.state_hash(), &state.rng, &state.units, &state.queue), (hash, &rng, &units, &queue));
    }

    // --- Dodge defense breakdown --------------------------------------------------------

    fn dodge_args() -> bb_rules::scenario::LaunchArgs {
        bb_rules::scenario::LaunchArgs { dodge: true, fast_adaptation: false, ..fa_args() }
    }

    /// Expected breakdown geometry, derived independently of the production helper: scale 1 at
    /// `PANEL_X`, 51 px below the row top.
    fn dodge_run(row: u32, label: &str) -> Vec<Vertex> {
        text_run(PANEL_X, panel_row_y(row) + 51.0, 1.0, DODGE_TEXT, label)
    }

    /// True when any breakdown text starts in `row`'s own breakdown band (any digits).
    fn has_dodge_row(verts: &[Vertex], row: u32) -> bool {
        let (top, bottom) = (panel_row_y(row) + 51.0, panel_row_y(row) + 58.0);
        verts.iter().filter(|v| v.color == DODGE_TEXT).map(to_px).any(|p| p.1 >= top - 0.01 && p.1 <= bottom + 0.01)
    }

    fn dodge_rows(verts: &[Vertex]) -> Vec<u32> {
        (0..6).filter(|&r| has_dodge_row(verts, r)).collect()
    }

    #[test]
    fn dodge_breakdown_shows_bonus_and_total_defense_for_capable_present_rows_of_both_factions() {
        let mut state = bb_rules::scenario::launch_battle_for(dodge_args());
        let verts = build_scene(&scene(&state, Some(0), false));
        // Player units: initiative 100, fatigue 0, defense 10 -> +15 on top of 10.
        for row in 0..3 {
            assert!(contains_run(&verts, &dodge_run(row, "DODGE +15 DEF 25")), "player row {row}");
        }
        assert_eq!(dodge_rows(&verts), vec![0, 1, 2], "enemies are incapable under the launch option");
        // Enemies show the breakdown too once scripted state gives them the capability.
        state.units[3].has_dodge = true; // initiative 90 -> floor(13.5) = 13, defense 5
        state.units[4].has_dodge = true;
        state.units[4].stats.initiative = 0; // zero bonus is still drawn
        // Zero bonus on a player unit, and a negative current initiative (floored at zero).
        state.units[1].stats.initiative = 0;
        state.units[2].fatigue = 300; // 100 - 150 < 0
        let verts = build_scene(&scene(&state, None, false));
        assert!(contains_run(&verts, &dodge_run(0, "DODGE +15 DEF 25")));
        assert!(contains_run(&verts, &dodge_run(1, "DODGE +0 DEF 10")));
        assert!(contains_run(&verts, &dodge_run(2, "DODGE +0 DEF 10")));
        assert!(contains_run(&verts, &dodge_run(3, "DODGE +13 DEF 18")));
        assert!(contains_run(&verts, &dodge_run(4, "DODGE +0 DEF 5")));
        assert_eq!(dodge_rows(&verts), vec![0, 1, 2, 3, 4]);
        // The default scenario has no capable unit at all.
        let plain = BattleState::new(1);
        assert_eq!(count_color(&build_scene(&scene(&plain, Some(0), false)), DODGE_TEXT), 0);
    }

    #[test]
    fn dodge_breakdown_includes_fleeing_and_retreating_and_omits_dead_and_escaped_rows() {
        let mut state = bb_rules::scenario::launch_battle_for(dodge_args());
        state.units[0].morale = Morale::Fleeing; // base defense is morale-scaled: 10 * 70% = 7
        state.units[1].retreating = true;
        let verts = build_scene(&scene(&state, None, false));
        assert!(contains_run(&verts, &dodge_run(0, "DODGE +15 DEF 22")), "fleeing keeps its row");
        assert!(contains_run(&verts, &dodge_run(1, "DODGE +15 DEF 25")), "retreating keeps its row");
        state.units[0].morale = Morale::Steady;
        state.units[0].alive = false;
        state.units[1].retreating = false;
        state.units[1].fled = true;
        let verts = build_scene(&scene(&state, None, false));
        assert_eq!(dodge_rows(&verts), vec![2]);
        assert_eq!(count_color(&verts, DODGE_TEXT), count_color(&text_run(0.0, 0.0, 1.0, DODGE_TEXT, "DODGE +15 DEF 25"), DODGE_TEXT));
        assert!(text_geometry(&verts, PANEL_X, panel_row_y(0), 2.0, DIM, "P1 DEAD").is_some());
        assert!(text_geometry(&verts, PANEL_X, panel_row_y(1), 2.0, FLED, "P2 FLED").is_some());
        // Incapable rows (capability off, other passives on) never draw it.
        let mut incapable = bb_rules::scenario::launch_battle_for(fa_args());
        incapable.units[0].adrenaline_pending = true;
        assert_eq!(count_color(&build_scene(&scene(&incapable, Some(0), false)), DODGE_TEXT), 0);
    }

    #[test]
    fn longest_dodge_breakdown_fits_and_clears_row_text_markers_the_next_row_and_the_queue() {
        let label = format!("DODGE +{} DEF {}", 644_245_094u32, u32::MAX);
        assert_eq!(label, "DODGE +644245094 DEF 4294967295");
        let w = Painter::text_width(1.0, &label);
        assert_eq!(w, 185.0);
        let mut state = bb_rules::scenario::launch_battle_for(dodge_args());
        for u in &mut state.units {
            u.has_dodge = true;
            u.has_fast_adaptation = true;
            u.fast_adaptation_stacks = u32::MAX;
            u.adrenaline_pending = true;
            u.stats.initiative = u32::MAX;
            u.stats.defense = u32::MAX;
            u.fatigue = 0;
        }
        for u in 0..6 {
            assert_eq!(state.units[u].dodge_defense_bonus(), 644_245_094);
            assert_eq!(state.units[u].effective_defense(), u32::MAX);
        }
        for morale in [Morale::Fleeing, Morale::Steady] {
            state.units[0].morale = morale;
            if morale == Morale::Fleeing {
                // Morale scales only the base defense; the bonus is added after it and saturates.
                assert_eq!(state.units[0].effective_defense(), (u32::MAX as u64 * 70 / 100) as u32 + 644_245_094);
            }
            let total = state.units[0].effective_defense();
            let verts = build_scene(&scene(&state, Some(0), false));
            for row in 0..6u32 {
                let y = panel_row_y(row);
                let expected = if row == 0 { format!("DODGE +644245094 DEF {total}") } else { label.clone() };
                let run = dodge_run(row, &expected);
                assert!(contains_run(&verts, &run), "{morale:?} row {row}");
                let m = bounds(run.iter().map(to_px));
                assert!(m.0 >= PANEL_X && m.2 <= PANEL_X + w && m.2 <= WIN_W - 8.0, "inside the panel and the window: {m:?}");
                assert!(m.1 >= y + 51.0 - 0.01 && m.3 <= y + 58.01, "within its own band: {m:?}");
                // The AP/fatigue line (scale 2, 14 px tall from y + 36) ends above it.
                let u = &state.units[row as usize];
                let res = format!("AP {}/{} FAT {}/{}", u.ap, u.stats.ap_max, u.fatigue, u.stats.fatigue_max);
                let col = if row == 0 { YELLOW } else { WHITE };
                let rg = bounds(text_geometry(&verts, PANEL_X, y + 36.0, 2.0, col, &res).expect("resource text").into_iter());
                assert!(rg.3 < m.1, "resource text {rg:?} above {m:?}");
                // The pending-Adrenaline box ends at or above the breakdown and the Fast Adaptation
                // indicator (HP line) lies above it.
                let pend = bounds(pending_marker_run(row).iter().map(to_px));
                assert!(pend.3 <= m.1 + 0.01, "pending box {pend:?} above {m:?}");
                assert!(contains_run(&verts, &pending_marker_run(row)) && contains_run(&verts, &adapt_run(row, u32::MAX)));
                let ad = bounds(adapt_run(row, u32::MAX).iter().map(to_px));
                assert!(ad.3 < m.1);
                // The next row (or, after the last, the queue heading at y 420) starts below it.
                let next = if row == 5 { 420.0 } else { panel_row_y(row + 1) };
                assert!(m.3 < next, "{m:?} vs next {next}");
            }
            assert!(text_geometry(&verts, PANEL_X, 420.0, 2.0, WHITE, "QUEUE").is_some());
        }
    }

    #[test]
    fn dodge_label_uses_only_supported_glyphs() {
        let fallback = crate::font::glyph('\u{1}');
        let supported = |text: &str| text.chars().all(|c| c == ' ' || font::glyph(c) != fallback);
        assert!(!supported("DODGE \u{1}") && !supported("DEF é"));
        let mut state = bb_rules::scenario::launch_battle_for(dodge_args());
        for (init, def, fatigue) in [(0, 0, 0), (100, 10, 0), (u32::MAX, u32::MAX, 0), (3, 7, u32::MAX)] {
            let u = &mut state.units[0];
            (u.stats.initiative, u.stats.defense, u.fatigue) = (init, def, fatigue);
            let label = dodge_breakdown_label(u);
            assert!(label.starts_with("DODGE +") && label.contains(" DEF "), "{label:?}");
            assert!(supported(&label), "unsupported glyph in {label:?}");
        }
    }

    /// Duel fixture: Dodge-capable P1 beside E1 on the lane `r = -2`, everyone else parked, damage
    /// 1 so every hit is armour-only and nonlethal. All units have defense 0 and initiative 100.
    fn dodge_duel() -> BattleState {
        let mut s = bb_rules::scenario::launch_battle_for(dodge_args());
        let park = [hex(-4, 2), hex(-4, 3), hex(-4, 4), hex(4, -4), hex(4, -3), hex(4, -2)];
        for (u, at) in s.units.iter_mut().zip(park) {
            u.pos = at;
            u.stats.melee_skill = 0;
            u.stats.defense = 0;
            u.stats.initiative = 100;
            u.stats.dmg_min = 1;
            u.stats.dmg_max = 1;
        }
        s.units[0].pos = hex(0, -2);
        s.units[3].pos = hex(1, -2);
        s.units[0].stats.melee_skill = 50;
        s.units[3].stats.melee_skill = 50;
        s
    }

    /// Make `unit` the ready active unit with fresh budgets and rig the RNG so its next attack
    /// roll is `roll`.
    fn ready_unit_swing(s: &mut BattleState, unit: u32, roll: u32) {
        s.queue.retain(|&q| q != unit);
        s.queue.insert(0, unit);
        s.units[unit as usize].ap = 9;
        s.units[unit as usize].fatigue = 0;
        s.units[unit as usize].turn_started = true;
        for k in 1u64.. {
            let cand = s.rng.state.wrapping_add(k);
            if (bb_rules::Rng::new(cand).next_u64() % 100) as u32 == roll {
                s.rng = bb_rules::Rng::new(cand);
                return;
            }
        }
    }

    #[test]
    fn displayed_defense_hit_preview_and_resolved_attack_agree_for_both_directions() {
        use bb_rules::Event;
        // Player attacks a scripted Dodge-capable enemy: the preview drawn on the map is the
        // attacker's skill minus the total defense drawn in the defender's row.
        let mut s = dodge_duel();
        s.units[3].has_dodge = true;
        let defender_def = s.units[3].effective_defense();
        assert_eq!((s.units[3].dodge_defense_bonus(), defender_def), (15, 15));
        let verts = build_scene(&hovering(&s, Some(0), Some(s.units[3].pos), false));
        assert!(contains_run(&verts, &dodge_run(3, "DODGE +15 DEF 15")));
        let chance = hit_label_drawn(&s).expect("HIT label drawn");
        assert_eq!(chance, s.hit_chance(0, 3));
        assert_eq!(chance, 50 - defender_def);
        ready_unit_swing(&mut s, 0, 99);
        let ev = s.apply(Command::Attack { attacker: 0, target: 3 }).unwrap();
        assert!(matches!(ev[0], Event::Attacked { chance: c, hit: false, .. } if c == chance), "{ev:?}");

        // Enemy attacks the launch-granted player Dodge: no preview is drawn for enemies, but the
        // rules chance still equals skill minus the breakdown's total.
        let mut s = dodge_duel();
        let verts = build_scene(&scene(&s, None, false));
        assert!(contains_run(&verts, &dodge_run(0, "DODGE +15 DEF 15")));
        let chance = s.hit_chance(3, 0);
        assert_eq!(chance, 50 - s.units[0].effective_defense());
        assert_eq!(chance, 35);
        ready_unit_swing(&mut s, 3, 0);
        let ev = s.apply(Command::Attack { attacker: 3, target: 0 }).unwrap();
        assert!(matches!(ev[0], Event::Attacked { chance: c, hit: true, .. } if c == chance), "{ev:?}");
    }

    #[test]
    fn breakdown_follows_live_fatigue_from_actions_and_turn_start_regeneration() {
        use bb_rules::rules::{ATTACK_FATIGUE, DEFENDER_MISS_FATIGUE_GAIN, FATIGUE_REGEN};
        use bb_rules::Event;
        let expect = |fatigue: u32| format!("DODGE +{} DEF {}", (100 - fatigue / 2) * 15 / 100, (100 - fatigue / 2) * 15 / 100);
        let mut s = dodge_duel();
        s.units[3].has_dodge = true;
        let verts = build_scene(&scene(&s, Some(0), false));
        assert!(contains_run(&verts, &dodge_run(0, &expect(0))) && contains_run(&verts, &dodge_run(3, &expect(0))));
        // A real attack tires the attacker and the defender; both rows update immediately.
        ready_unit_swing(&mut s, 0, 99);
        let ev = s.apply(Command::Attack { attacker: 0, target: 3 }).unwrap();
        assert!(matches!(ev[0], Event::Attacked { hit: false, .. }));
        assert_eq!((s.units[0].fatigue, s.units[3].fatigue), (ATTACK_FATIGUE, DEFENDER_MISS_FATIGUE_GAIN));
        let verts = build_scene(&scene(&s, Some(0), false));
        assert!(contains_run(&verts, &dodge_run(0, &expect(ATTACK_FATIGUE))), "attacker row: {}", expect(ATTACK_FATIGUE));
        assert!(contains_run(&verts, &dodge_run(3, &expect(DEFENDER_MISS_FATIGUE_GAIN))));
        assert!(!contains_run(&verts, &dodge_run(0, &expect(0))), "no stale value");
        assert_ne!(expect(ATTACK_FATIGUE), expect(0));
        // Movement fatigue shows as well.
        s.units[0].fatigue = 60;
        let verts = build_scene(&scene(&s, Some(0), false));
        assert!(contains_run(&verts, &dodge_run(0, &expect(60))));
        // Turn-start regeneration lowers fatigue and raises the bonus again at the next round.
        s.units[0].turn_started = false;
        let round = s.round;
        while s.round == round || s.units[0].fatigue == 60 {
            let a = s.queue[0];
            let cmd = if s.units[a as usize].morale == Morale::Fleeing { Command::Flee { unit: a } } else { Command::EndTurn { unit: a } };
            s.apply(cmd).unwrap();
            assert!(s.outcome().is_none());
        }
        assert_eq!(s.units[0].fatigue, 60 - FATIGUE_REGEN);
        let verts = build_scene(&scene(&s, None, false));
        assert!(contains_run(&verts, &dodge_run(0, &expect(60 - FATIGUE_REGEN))), "{}", expect(60 - FATIGUE_REGEN));
        assert!(!contains_run(&verts, &dodge_run(0, &expect(60))));
    }

    #[test]
    fn dodge_scene_is_pure_and_unchanged_without_the_capability() {
        let mut state = dodge_duel();
        state.units[3].has_dodge = true;
        state.units[0].adrenaline_pending = true;
        let (hash, rng, units, queue, elev) =
            (state.state_hash(), state.rng.clone(), state.units.clone(), state.queue.clone(), state.elevations.clone());
        let a = build_scene(&hovering(&state, Some(0), Some(hex(1, -2)), false));
        let b = build_scene(&hovering(&state, Some(0), Some(hex(1, -2)), false));
        assert!(a.len() == b.len() && a.iter().zip(&b).all(|(x, y)| same(x, y)));
        assert_eq!((state.state_hash(), &state.rng, &state.units, &state.queue, &state.elevations), (hash, &rng, &units, &queue, &elev));
        // Removing the capability removes exactly the breakdown text and nothing else.
        let mut plain = state.clone();
        for u in &mut plain.units {
            u.has_dodge = false;
        }
        let without = build_scene(&hovering(&plain, Some(0), Some(hex(1, -2)), false));
        assert_eq!(dodge_rows(&a), vec![0, 1, 2, 3]);
        assert!(dodge_rows(&without).is_empty());
    }

    // --- Nine Lives READY/USED indicator ------------------------------------------------

    fn nine_args() -> bb_rules::scenario::LaunchArgs {
        bb_rules::scenario::LaunchArgs { nine_lives: true, fast_adaptation: false, ..fa_args() }
    }

    /// Expected indicator geometry, derived independently of the production box helper: scale 1,
    /// right edge 8 px inside the window, 51 px below the row top.
    fn nine_run(row: u32, used: bool) -> Vec<Vertex> {
        let (label, col) = if used { ("NINE LIVES USED", [0.42, 0.42, 0.46, 1.0]) } else { ("NINE LIVES READY", [0.95, 0.55, 0.65, 1.0]) };
        let x = WIN_W - 8.0 - (label.chars().count() as f32 * 6.0 - 1.0);
        text_run(x, panel_row_y(row) + 51.0, 1.0, col, label)
    }

    /// Rows whose own indicator band holds text in the READY / USED colour.
    fn nine_rows(verts: &[Vertex], used: bool) -> Vec<u32> {
        let col = nine_lives_marker_color(used);
        (0..6u32)
            .filter(|&r| {
                let (top, bottom) = (panel_row_y(r) + 51.0, panel_row_y(r) + 58.0);
                verts.iter().filter(|v| v.color == col).map(to_px).any(|p| p.1 >= top - 0.01 && p.1 <= bottom + 0.01)
            })
            .collect()
    }

    #[test]
    fn nine_lives_indicator_shows_ready_and_used_for_capable_present_rows_of_both_factions() {
        let mut state = bb_rules::scenario::launch_battle_for(nine_args());
        let verts = build_scene(&scene(&state, Some(0), false));
        for row in 0..3 {
            assert!(contains_run(&verts, &nine_run(row, false)), "player row {row} READY");
        }
        assert_eq!(nine_rows(&verts, false), vec![0, 1, 2], "enemies are incapable under the launch option");
        assert!(nine_rows(&verts, true).is_empty());
        // Scripted capable enemies; one has consumed its protection.
        state.units[3].has_nine_lives = true;
        state.units[4].has_nine_lives = true;
        state.units[4].nine_lives_used = true;
        state.units[1].nine_lives_used = true;
        let verts = build_scene(&scene(&state, Some(0), false));
        assert!(contains_run(&verts, &nine_run(0, false)) && contains_run(&verts, &nine_run(2, false)) && contains_run(&verts, &nine_run(3, false)));
        assert!(contains_run(&verts, &nine_run(1, true)) && contains_run(&verts, &nine_run(4, true)));
        assert_eq!(nine_rows(&verts, false), vec![0, 2, 3]);
        assert_eq!(nine_rows(&verts, true), vec![1, 4]);
        assert_ne!(NINE_LIVES_READY_TEXT, NINE_LIVES_USED_TEXT);
        assert!(NINE_LIVES_USED_TEXT[..3].iter().all(|c| (c - 0.44).abs() < 0.1), "dim gray");
        // Incapable rows draw nothing, even when `used` is set or other passives are on.
        let mut incapable = bb_rules::scenario::launch_battle_for(fa_args());
        incapable.units[0].nine_lives_used = true;
        incapable.units[3].nine_lives_used = true;
        incapable.units[0].adrenaline_pending = true;
        let verts = build_scene(&scene(&incapable, Some(0), false));
        assert_eq!(count_color(&verts, NINE_LIVES_READY_TEXT) + count_color(&verts, NINE_LIVES_USED_TEXT), 0);
    }

    #[test]
    fn nine_lives_indicator_includes_fleeing_and_retreating_and_omits_dead_and_escaped_rows() {
        let mut state = bb_rules::scenario::launch_battle_for(nine_args());
        state.units[0].morale = Morale::Fleeing;
        state.units[1].retreating = true;
        state.units[1].nine_lives_used = true;
        let verts = build_scene(&scene(&state, None, false));
        assert!(contains_run(&verts, &nine_run(0, false)), "fleeing keeps its row");
        assert!(contains_run(&verts, &nine_run(1, true)), "retreating keeps its row");
        state.units[0].morale = Morale::Steady;
        state.units[0].alive = false;
        state.units[1].retreating = false;
        state.units[1].fled = true;
        let verts = build_scene(&scene(&state, None, false));
        assert_eq!(nine_rows(&verts, false), vec![2]);
        assert!(nine_rows(&verts, true).is_empty());
        assert_eq!(count_color(&verts, NINE_LIVES_READY_TEXT), count_color(&nine_run(2, false), NINE_LIVES_READY_TEXT));
        assert!(text_geometry(&verts, PANEL_X, panel_row_y(0), 2.0, DIM, "P1 DEAD").is_some());
        assert!(text_geometry(&verts, PANEL_X, panel_row_y(1), 2.0, FLED, "P2 FLED").is_some());
    }

    #[test]
    fn real_protected_hit_turns_ready_into_used_and_a_later_lethal_hit_removes_the_row_indicator() {
        use bb_rules::hex::hex as h;
        let mut s = bb_rules::scenario::launch_battle_for(nine_args());
        let park = [h(-4, 2), h(-4, 3), h(-4, 4), h(4, -4), h(4, -3), h(4, -2)];
        for (u, at) in s.units.iter_mut().zip(park) {
            u.pos = at;
        }
        s.units[0].pos = h(0, -2);
        s.units[3].pos = h(1, -2);
        s.units[0].hp = 10;
        s.units[0].armour = 0;
        s.units[0].stats.defense = 0;
        s.units[3].stats.melee_skill = 100;
        s.units[3].stats.dmg_min = 30;
        s.units[3].stats.dmg_max = 30;
        s.units[3].ap = 9;
        s.units[3].fatigue = 0;
        s.units[3].turn_started = true;
        s.queue.retain(|&q| q != 3);
        s.queue.insert(0, 3);
        let attack = bb_rules::Command::Attack { attacker: 3, target: 0 };
        let hits = |ev: &[bb_rules::Event]| matches!(ev.first(), Some(bb_rules::Event::Attacked { hit: true, .. }));
        let rng = (1u64..2_000)
            .map(bb_rules::Rng::new)
            .find(|rng| {
                let mut probe = s.clone();
                probe.rng = rng.clone();
                probe.apply(attack).is_ok_and(|a| hits(&a)) && probe.apply(attack).is_ok_and(|b| hits(&b))
            })
            .expect("two hits occur under some RNG state");
        s.rng = rng;
        let before = build_scene(&scene(&s, Some(3), false));
        assert!(contains_run(&before, &nine_run(0, false)) && nine_rows(&before, true).is_empty());
        let ev = s.apply(attack).unwrap();
        assert!(ev.iter().any(|e| matches!(e, bb_rules::Event::NineLivesTriggered { unit: 0 })));
        assert!(s.units[0].alive && s.units[0].hp == 1);
        let used = build_scene(&scene(&s, Some(3), false));
        assert!(contains_run(&used, &nine_run(0, true)), "survival: READY becomes USED");
        assert!(nine_rows(&used, false).iter().all(|&r| r != 0));
        assert!(text_geometry(&used, PANEL_X, panel_row_y(0), 2.0, WHITE, "P1").is_some(), "the row is still present");
        let ev = s.apply(attack).unwrap();
        assert!(ev.iter().any(|e| matches!(e, bb_rules::Event::Died { unit: 0 })));
        let dead = build_scene(&scene(&s, Some(3), false));
        assert!(!nine_rows(&dead, false).contains(&0) && nine_rows(&dead, true).is_empty(), "the dead row has no indicator");
        assert!(text_geometry(&dead, PANEL_X, panel_row_y(0), 2.0, DIM, "P1 DEAD").is_some());
        assert_eq!(nine_rows(&build_scene(&scene(&s, Some(3), false)), false), vec![1, 2]);
    }

    #[test]
    fn longest_nine_lives_indicator_shares_the_dodge_line_and_clears_everything_for_every_row() {
        assert_eq!(Painter::text_width(1.0, "NINE LIVES READY"), 95.0, "the longest label");
        assert_eq!(Painter::text_width(1.0, "NINE LIVES USED"), 89.0);
        assert_eq!(nine_lives_marker_box(false), (WIN_W - 8.0 - 95.0, 95.0));
        let mut state = bb_rules::scenario::launch_battle_for(nine_args());
        for u in &mut state.units {
            u.has_nine_lives = true;
            u.has_dodge = true;
            u.has_fast_adaptation = true;
            u.fast_adaptation_stacks = u32::MAX;
            u.adrenaline_pending = true;
            u.stats.initiative = u32::MAX;
            u.stats.defense = u32::MAX;
            u.fatigue = 0;
        }
        for used in [false, true] {
            for u in &mut state.units {
                u.nine_lives_used = used;
            }
            let verts = build_scene(&scene(&state, Some(0), false));
            for row in 0..6u32 {
                let y = panel_row_y(row);
                let run = nine_run(row, used);
                assert!(contains_run(&verts, &run), "used {used} row {row}");
                let m = bounds(run.iter().map(to_px));
                assert!(m.2 <= WIN_W - 8.0 + 0.01 && m.0 >= 1177.0 - 0.01, "right aligned inside the window: {m:?}");
                assert!(m.1 >= y + 51.0 - 0.01 && m.3 <= y + 58.01, "within its own band: {m:?}");
                // Shares the line with the longest Dodge breakdown without overlapping it.
                let total = state.units[row as usize].effective_defense();
                let dodge = dodge_run(row, &format!("DODGE +{} DEF {total}", state.units[row as usize].dodge_defense_bonus()));
                assert!(contains_run(&verts, &dodge), "row {row} dodge breakdown is unchanged");
                let d = bounds(dodge.iter().map(to_px));
                assert!(d.2 < m.0, "dodge {d:?} ends left of the indicator {m:?}");
                assert!((d.1 - m.1).abs() < 0.01, "same line: top {} vs {}", d.1, m.1);
                // Resource text, pending box and Fast Adaptation indicator sit above it and clear.
                let u = &state.units[row as usize];
                let res = format!("AP {}/{} FAT {}/{}", u.ap, u.stats.ap_max, u.fatigue, u.stats.fatigue_max);
                let col = if row == 0 { YELLOW } else { WHITE };
                let rg = bounds(text_geometry(&verts, PANEL_X, y + 36.0, 2.0, col, &res).expect("resource text").into_iter());
                assert!(rg.3 < m.1, "resource text {rg:?} above {m:?}");
                let pend = bounds(pending_marker_run(row).iter().map(to_px));
                assert!(pend.3 <= m.1 + 0.01, "pending box {pend:?} above {m:?}");
                assert!(contains_run(&verts, &pending_marker_run(row)) && contains_run(&verts, &adapt_run(row, u32::MAX)));
                assert!(bounds(adapt_run(row, u32::MAX).iter().map(to_px)).3 < m.1);
                // The next row (or, after the last, the queue heading at y 420) starts below it.
                let next = if row == 5 { 420.0 } else { panel_row_y(row + 1) };
                assert!(m.3 < next, "{m:?} vs next {next}");
            }
            assert!(text_geometry(&verts, PANEL_X, 420.0, 2.0, WHITE, "QUEUE").is_some());
        }
    }

    #[test]
    fn nine_lives_labels_use_only_supported_glyphs() {
        let fallback = crate::font::glyph('\u{1}');
        let supported = |text: &str| text.chars().all(|c| c == ' ' || font::glyph(c) != fallback);
        assert!(!supported("NINE \u{1}") && !supported("LIVES é"));
        for used in [false, true] {
            let label = nine_lives_marker_label(used);
            assert!(label.starts_with("NINE LIVES "), "{label:?}");
            assert!(supported(label), "unsupported glyph in {label:?}");
        }
        assert_eq!((nine_lives_marker_label(false), nine_lives_marker_label(true)), ("NINE LIVES READY", "NINE LIVES USED"));
    }

    #[test]
    fn nine_lives_scene_is_pure_and_unchanged_without_the_capability() {
        let mut state = bb_rules::scenario::launch_battle_for(nine_args());
        state.units[4].has_nine_lives = true;
        state.units[4].nine_lives_used = true;
        state.units[0].adrenaline_pending = true;
        state.units[1].has_dodge = true;
        let (hash, rng, units, queue, elev) =
            (state.state_hash(), state.rng.clone(), state.units.clone(), state.queue.clone(), state.elevations.clone());
        let a = build_scene(&scene(&state, Some(0), false));
        let b = build_scene(&scene(&state, Some(0), false));
        assert!(a.len() == b.len() && a.iter().zip(&b).all(|(x, y)| same(x, y)));
        assert_eq!((state.state_hash(), &state.rng, &state.units, &state.queue, &state.elevations), (hash, &rng, &units, &queue, &elev));
        // Removing the capability removes exactly the indicator text and nothing else.
        let mut plain = state.clone();
        for u in &mut plain.units {
            u.has_nine_lives = false;
        }
        let without = build_scene(&scene(&plain, Some(0), false));
        let (ready, used) = (count_color(&a, NINE_LIVES_READY_TEXT), count_color(&a, NINE_LIVES_USED_TEXT));
        assert!(ready > 0 && used > 0);
        assert_eq!(count_color(&without, NINE_LIVES_READY_TEXT) + count_color(&without, NINE_LIVES_USED_TEXT), 0);
        let mut p = Painter { verts: Vec::new() };
        p.nine_lives_marker(panel_row_y(0), false);
        assert_eq!(ready, 3 * count_color(&p.verts, NINE_LIVES_READY_TEXT));
        assert_eq!(a.len() - without.len(), ready + used, "only the indicator vertices differ");
        assert_eq!(count_color(&a, DODGE_TEXT), count_color(&without, DODGE_TEXT));
        assert_eq!(count_color(&a, PENDING_MARKER), count_color(&without, PENDING_MARKER));
    }

    // --- Indomitable button and active-stance marker --------------------------------------

    fn indo_args() -> bb_rules::scenario::LaunchArgs {
        bb_rules::scenario::LaunchArgs { indomitable: true, fast_adaptation: false, ..fa_args() }
    }

    /// Stock scenario with the Indomitable launch option: P1 is the active, started player unit.
    fn indo() -> BattleState {
        bb_rules::scenario::launch_battle_for(indo_args())
    }

    fn marker_run(row: u32) -> Vec<Vertex> {
        text_run(1092.0, panel_row_y(row) + 51.0, 1.0, INDOMITABLE_MARKER_TEXT, "INDOMITABLE")
    }

    /// Rows that carry the marker (derived from its own geometry, independent of the rules).
    fn marker_rows(verts: &[Vertex]) -> Vec<u32> {
        (0..6).filter(|&r| contains_run(verts, &marker_run(r))).collect()
    }

    #[test]
    fn indomitable_button_follows_the_rules_availability_query_with_three_appearances() {
        let state = indo();
        let sc = scene(&state, Some(0), false);
        assert!(indomitable_enabled(&sc) && !indomitable_stance_shown(&sc));
        assert_eq!(skill_fill(&build_scene(&sc), layout::INDOMITABLE_BUTTON), Some(SKILL_ENABLED));
        // The rules query decides: every unit-level refusal and every control refusal disables.
        let mut cases: Vec<(&str, BattleState, Option<u32>)> = Vec::new();
        let mut c = indo();
        c.units[0].has_indomitable = false;
        cases.push(("no capability", c, Some(0)));
        let mut c = indo();
        c.units[0].ap = INDOMITABLE_AP - 1;
        cases.push(("no AP", c, Some(0)));
        let mut c = indo();
        c.units[0].fatigue = c.units[0].stats.fatigue_max - INDOMITABLE_FATIGUE + 1;
        cases.push(("fatigue cap", c, Some(0)));
        let mut c = indo();
        c.units[0].stats.fatigue_max = u32::MAX;
        c.units[0].fatigue = u32::MAX;
        cases.push(("fatigue overflow", c, Some(0)));
        cases.push(("no selection", indo(), None));
        cases.push(("non-active selection", indo(), Some(1)));
        cases.push(("enemy selection", indo(), Some(3)));
        let mut c = indo();
        c.queue = vec![3, 0];
        cases.push(("enemy turn", c, Some(0)));
        let mut c = indo();
        c.outcome = Some(bb_rules::Outcome::Victory);
        cases.push(("battle over", c, Some(0)));
        for (what, edit) in [("fleeing", 0), ("retreating", 1), ("dead", 2), ("escaped", 3)] {
            let mut c = indo();
            match edit {
                0 => c.units[0].morale = Morale::Fleeing,
                1 => c.units[0].retreating = true,
                2 => c.units[0].alive = false,
                _ => c.units[0].fled = true,
            }
            cases.push((what, c, Some(0)));
        }
        for (what, st, selected) in &cases {
            let sc = scene(st, *selected, false);
            assert!(!indomitable_enabled(&sc), "{what}");
            let verts = build_scene(&sc);
            assert!(contains_run(&verts, &indomitable_button_run(SkillLook::Disabled)), "{what}");
            assert_eq!(skill_fill(&verts, layout::INDOMITABLE_BUTTON), Some(SKILL_DISABLED), "{what}");
        }
        // The exact affordable boundary still passes.
        let mut c = indo();
        c.units[0].ap = INDOMITABLE_AP;
        c.units[0].fatigue = c.units[0].stats.fatigue_max - INDOMITABLE_FATIGUE;
        assert!(indomitable_enabled(&scene(&c, Some(0), false)));
        // An effective stance on the selected controllable active player draws the active look
        // (and activation is no longer offered); a capability without a stance does not.
        let mut c = indo();
        c.units[0].indomitable_active = true;
        let sc = scene(&c, Some(0), false);
        assert!(indomitable_stance_shown(&sc) && !indomitable_enabled(&sc));
        let verts = build_scene(&sc);
        assert!(contains_run(&verts, &indomitable_button_run(SkillLook::Active)));
        assert_eq!(skill_fill(&verts, layout::INDOMITABLE_BUTTON), Some(SKILL_ACTIVE));
        // Not for a non-active, enemy or unselected unit, nor once the stance stops being effective.
        for (selected, queue) in [(None, vec![0]), (Some(1), vec![0]), (Some(0), vec![3, 0])] {
            let mut d = c.clone();
            d.queue = queue;
            assert!(!indomitable_stance_shown(&scene(&d, selected, false)));
        }
        for edit in 0..3 {
            let mut d = c.clone();
            match edit {
                0 => d.units[0].morale = Morale::Fleeing,
                1 => d.units[0].retreating = true,
                _ => d.units[0].has_indomitable = false,
            }
            assert!(!indomitable_stance_shown(&scene(&d, Some(0), false)), "edit {edit}");
        }
        // Rotation and Footwork keep their own looks beside it.
        assert_eq!(skill_fill(&verts, layout::ROTATION_BUTTON), Some(SKILL_DISABLED));
        assert_eq!(skill_fill(&verts, layout::FOOTWORK_BUTTON), Some(SKILL_DISABLED));
    }

    #[test]
    fn indomitable_button_is_drawn_on_the_shared_rectangle_and_shows_rules_costs_and_effect() {
        for look in [SkillLook::Disabled, SkillLook::Enabled, SkillLook::Active] {
            let verts = indomitable_button_run(look);
            assert!(close(skill_button_border(&verts, layout::INDOMITABLE_BUTTON), rect_bounds(layout::INDOMITABLE_BUTTON)), "{look:?}");
            assert!(close(bounds(verts.iter().map(to_px)), rect_bounds(layout::INDOMITABLE_BUTTON)), "{look:?}: all geometry is inside");
        }
        assert_eq!(indomitable_info_lines(), ["COST 5 AP 25 FAT".to_string(), "HALF MELEE DAMAGE".to_string()]);
        assert_eq!((INDOMITABLE_AP, INDOMITABLE_FATIGUE), (5, 25));
        assert_eq!(layout::INDOMITABLE_LABEL, "INDOMITABLE (4)");
        let r = layout::INDOMITABLE_BUTTON;
        let mut active = indo();
        active.units[0].indomitable_active = true;
        for (state, text) in [(indo(), WHITE), (stock(), BUTTON_DISABLED_TEXT), (active, WHITE)] {
            let verts = build_scene(&scene(&state, Some(0), false));
            assert!(text_geometry(&verts, r.x + SKILL_TEXT_X, r.y + SKILL_TITLE_DY, layout::SKILL_TITLE_SCALE, text, "INDOMITABLE (4)").is_some());
            assert!(text_geometry(&verts, r.x + SKILL_TEXT_X, r.y + SKILL_INFO_DY, 1.0, text, "COST 5 AP 25 FAT").is_some());
            assert!(text_geometry(&verts, r.x + SKILL_TEXT_X, r.y + SKILL_INFO_DY + 8.0, 1.0, text, "HALF MELEE DAMAGE").is_some());
        }
        // Every title uses the shared scale and every cost/effect row stays at scale 1.
        assert_eq!(layout::SKILL_TITLE_SCALE, 1.25);
        let verts = build_scene(&scene(&indo(), Some(0), false));
        for (r, label, rows) in [
            (layout::ADRENALINE_BUTTON, layout::ADRENALINE_LABEL, adrenaline_info_lines()),
            (layout::ROTATION_BUTTON, layout::ROTATION_LABEL, rotation_info_lines()),
            (layout::FOOTWORK_BUTTON, layout::FOOTWORK_LABEL, footwork_info_lines()),
            (layout::INDOMITABLE_BUTTON, layout::INDOMITABLE_LABEL, indomitable_info_lines()),
        ] {
            let text = if label == layout::INDOMITABLE_LABEL { WHITE } else { BUTTON_DISABLED_TEXT };
            assert!(text_geometry(&verts, r.x + SKILL_TEXT_X, r.y + SKILL_TITLE_DY, 1.25, text, label).is_some(), "{label}");
            for (i, row) in rows.iter().enumerate() {
                assert!(text_geometry(&verts, r.x + SKILL_TEXT_X, r.y + SKILL_INFO_DY + 8.0 * i as f32, 1.0, text, row).is_some(), "{row}");
            }
        }
        assert_eq!(rotation_info_lines()[1], "SWAP ADJACENT ALLY");
        let fallback = crate::font::glyph('\u{1}');
        let supported = |c: char| c == ' ' || crate::font::glyph(c) != fallback;
        for text in indomitable_info_lines().iter().map(String::as_str).chain([layout::INDOMITABLE_LABEL, INDOMITABLE_MARKER_LABEL]) {
            if let Some(c) = text.chars().find(|c| !supported(*c)) {
                panic!("unsupported glyph {c:?} in {text:?}");
            }
        }
    }

    #[test]
    fn indomitable_marker_follows_the_rules_query_for_both_factions() {
        let mut state = indo();
        assert!(marker_rows(&build_scene(&scene(&state, Some(0), false))).is_empty(), "capability alone draws nothing");
        // Both factions: the marker is rules-driven, not selection- or side-driven.
        for id in [0usize, 4] {
            state.units[id].has_indomitable = true;
            state.units[id].indomitable_active = true;
        }
        let verts = build_scene(&scene(&state, Some(0), false));
        assert_eq!(marker_rows(&verts), vec![0, 4]);
        assert_eq!(build_scene(&scene(&state, None, false)).iter().filter(|v| v.color == INDOMITABLE_MARKER_TEXT).count(), verts.iter().filter(|v| v.color == INDOMITABLE_MARKER_TEXT).count());
        // The exact geometry: scale 1, x 1092, row top + 51, 65 px wide.
        let run = marker_run(0);
        let m = bounds(run.iter().map(to_px));
        assert!(m.0 >= 1092.0 && m.2 <= 1092.0 + 65.0, "inside the 65 px label cell starting at x 1092: {m:?}");
        assert!(m.1 >= panel_row_y(0) + 51.0 - 0.01 && m.3 <= panel_row_y(0) + 58.01);
        assert_eq!(Painter::text_width(1.0, INDOMITABLE_MARKER_LABEL), 65.0);
        // Lifecycle: expiry, Fleeing, death and escape remove it; retreat protection stays.
        let mut expired = state.clone();
        expired.units[0].indomitable_active = false;
        assert_eq!(marker_rows(&build_scene(&scene(&expired, Some(0), false))), vec![4], "expiry");
        for (what, edit, gone) in [("fleeing", 0, true), ("dead", 1, true), ("escaped", 2, true), ("retreating", 3, false)] {
            let mut d = state.clone();
            match edit {
                0 => d.units[4].morale = Morale::Fleeing,
                1 => d.units[4].alive = false,
                2 => d.units[4].fled = true,
                _ => d.units[4].retreating = true,
            }
            assert_eq!(d.units[4].is_indomitable_active(), !gone, "{what}");
            let rows = marker_rows(&build_scene(&scene(&d, Some(0), false)));
            assert_eq!(rows, if gone { vec![0] } else { vec![0, 4] }, "{what}");
        }
        // Finished turns, other selections and an ended battle do not hide a rules-effective stance.
        let mut d = state.clone();
        d.queue.retain(|&id| id != 4);
        assert_eq!(marker_rows(&build_scene(&scene(&d, Some(0), false))), vec![0, 4]);
        d.outcome = Some(bb_rules::Outcome::Victory);
        assert_eq!(marker_rows(&build_scene(&scene(&d, Some(0), false))), vec![0, 4]);
        // A stance flag without the capability is inert.
        let mut d = state.clone();
        d.units[4].has_indomitable = false;
        assert_eq!(marker_rows(&build_scene(&scene(&d, Some(0), false))), vec![0]);
    }

    #[test]
    fn indomitable_marker_fits_between_maximum_width_dodge_text_and_nine_lives_in_every_row() {
        let mut state = bb_rules::scenario::launch_battle_for(bb_rules::scenario::LaunchArgs { dodge: true, nine_lives: true, ..indo_args() });
        for u in &mut state.units {
            u.has_indomitable = true;
            u.indomitable_active = true;
            u.has_nine_lives = true;
            u.has_dodge = true;
            u.has_fast_adaptation = true;
            u.fast_adaptation_stacks = u32::MAX;
            u.adrenaline_pending = true;
            u.stats.initiative = u32::MAX;
            u.stats.defense = u32::MAX;
            u.fatigue = 0;
        }
        for used in [false, true] {
            for u in &mut state.units {
                u.nine_lives_used = used;
            }
            let verts = build_scene(&scene(&state, Some(0), false));
            assert_eq!(marker_rows(&verts), vec![0, 1, 2, 3, 4, 5]);
            for row in 0..6u32 {
                let y = panel_row_y(row);
                let m = bounds(marker_run(row).iter().map(to_px));
                let nine = bounds(nine_run(row, used).iter().map(to_px));
                let u = &state.units[row as usize];
                let dodge = bounds(dodge_run(row, &format!("DODGE +{} DEF {}", u.dodge_defense_bonus(), u.effective_defense())).iter().map(to_px));
                assert!(contains_run(&verts, &nine_run(row, used)) && contains_run(&verts, &pending_marker_run(row)) && contains_run(&verts, &adapt_run(row, u32::MAX)));
                assert!(dodge.2 < m.0, "dodge {dodge:?} ends left of the marker {m:?}");
                assert!(m.2 < nine.0, "marker {m:?} ends left of Nine Lives {nine:?}");
                assert!((m.1 - dodge.1).abs() < 0.01 && (m.1 - nine.1).abs() < 0.01, "one shared line");
                assert!(m.2 <= WIN_W - 8.0 && m.1 >= y + 51.0 - 0.01 && m.3 <= y + 58.01);
                // Resource text, the pending box and the adapt indicator sit above it; the next row below.
                let pend = bounds(pending_marker_run(row).iter().map(to_px));
                assert!(pend.3 <= m.1 + 0.01, "pending box {pend:?} above {m:?}");
                let next = if row == 5 { 420.0 } else { panel_row_y(row + 1) };
                assert!(m.3 < next);
            }
        }
        assert!(text_geometry(&build_scene(&scene(&state, Some(0), false)), PANEL_X, 420.0, 2.0, WHITE, "QUEUE").is_some());
    }

    #[test]
    fn indomitable_scene_is_pure_and_unchanged_without_the_stance() {
        let mut state = indo();
        state.units[0].indomitable_active = true;
        state.units[4].has_indomitable = true;
        state.units[4].indomitable_active = true;
        state.units[1].adrenaline_pending = true;
        let (hash, rng, units, queue, elev) =
            (state.state_hash(), state.rng.clone(), state.units.clone(), state.queue.clone(), state.elevations.clone());
        let a = build_scene(&scene(&state, Some(0), false));
        let b = build_scene(&scene(&state, Some(0), false));
        assert!(a.len() == b.len() && a.iter().zip(&b).all(|(x, y)| same(x, y)));
        assert_eq!((state.state_hash(), &state.rng, &state.units, &state.queue, &state.elevations), (hash, &rng, &units, &queue, &elev));
        assert_eq!(marker_rows(&a), vec![0, 4]);
        // Removing the stance removes exactly the markers and flips the button back to enabled.
        let mut plain = state.clone();
        for u in &mut plain.units {
            u.indomitable_active = false;
        }
        let without = build_scene(&scene(&plain, Some(0), false));
        assert!(count_color(&a, INDOMITABLE_MARKER_TEXT) > 0);
        assert_eq!(count_color(&without, INDOMITABLE_MARKER_TEXT), 0);
        assert_eq!(skill_fill(&without, layout::INDOMITABLE_BUTTON), Some(SKILL_ENABLED));
        assert_eq!(skill_fill(&a, layout::INDOMITABLE_BUTTON), Some(SKILL_ACTIVE));
    }
    // --- Swamp terrain presentation ----------------------------------------------------

    use bb_rules::scenario::{TerrainPreset, battle};

    /// Independently configured map: Swamp set hex by hex (including two obstacle hexes), with
    /// heights on two of the Swamp hexes, instead of through the preset.
    fn mixed_map() -> BattleState {
        let mut state = BattleState::new(23);
        for at in [hex(-3, -1), hex(2, 2), hex(3, -3), hex(-4, 1), hex(0, 0), hex(1, -1)] {
            state.ground.set(at, TerrainKind::Swamp).unwrap();
        }
        state.elevations.set(hex(-3, -1), 2).unwrap();
        state.elevations.set(hex(2, 2), 3).unwrap();
        state
    }

    /// Swamp on obstacle hexes only: nothing passable, so no marking or relocation.
    fn obstacle_only_swamp() -> BattleState {
        let mut state = BattleState::new(23);
        state.ground.set(hex(0, 0), TerrainKind::Swamp).unwrap();
        state.ground.set(hex(2, 1), TerrainKind::Swamp).unwrap();
        state
    }

    /// Straight RGBA raster of `verts` (source-over, pixel-centre sampling).
    fn raster(verts: &[Vertex]) -> Vec<Rgba> {
        raster_in(verts, (0, 0, WIN_W as usize, WIN_H as usize))
    }

    /// `raster` limited to the pixel region (x0, y0, x1, y1); other pixels stay black.
    fn raster_in(verts: &[Vertex], region: (usize, usize, usize, usize)) -> Vec<Rgba> {
        let (w, h) = (WIN_W as usize, WIN_H as usize);
        let mut px = vec![[0.0, 0.0, 0.0, 1.0]; w * h];
        for t in verts.chunks_exact(3) {
            let p: Vec<(f32, f32)> = t.iter().map(to_px).collect();
            let edge = |a: (f32, f32), b: (f32, f32), c: (f32, f32)| (b.0 - a.0) * (c.1 - a.1) - (b.1 - a.1) * (c.0 - a.0);
            let area = edge(p[0], p[1], p[2]);
            if area == 0.0 {
                continue;
            }
            let b = bounds(p.iter().copied());
            let (x0, x1) = ((b.0.floor().max(0.0) as usize).max(region.0), (b.2.ceil() as usize).min(region.2.min(w)));
            let (y0, y1) = ((b.1.floor().max(0.0) as usize).max(region.1), (b.3.ceil() as usize).min(region.3.min(h)));
            for y in y0..y1 {
                for x in x0..x1 {
                    let c = (x as f32 + 0.5, y as f32 + 0.5);
                    let ws = [edge(p[1], p[2], c), edge(p[2], p[0], c), edge(p[0], p[1], c)];
                    if ws.iter().all(|e| *e * area >= 0.0) {
                        let src = t[0].color;
                        let dst = &mut px[y * w + x];
                        for i in 0..3 {
                            dst[i] = src[i] * src[3] + dst[i] * (1.0 - src[3]);
                        }
                    }
                }
            }
        }
        px
    }

    fn pixel(px: &[Rgba], x: f32, y: f32) -> Rgba {
        px[y.floor() as usize * WIN_W as usize + x.floor() as usize]
    }

    /// Colour of the hex's base fill at a probe point that no label, ring, bar or badge uses.
    fn fill_of(px: &[Rgba], h: Hex) -> Rgba {
        let c = layout::hex_to_pixel(h);
        pixel(px, c.0 + 24.0, c.1 - 2.0)
    }

    /// Whether the 7x9 S badge of `h` is fully visible: every pixel of its box equals the pixel of
    /// the badge painted alone over the same background.
    fn swamp_badge_visible(px: &[Rgba], h: Hex) -> bool {
        let c = layout::hex_to_pixel(h);
        let mut alone = Painter { verts: Vec::new() };
        alone.rect(0.0, 0.0, WIN_W, WIN_H, BG);
        alone.swamp_badge(c);
        let (x0, y0) = (c.0 + SWAMP_BADGE_OFFSET.0, c.1 + SWAMP_BADGE_OFFSET.1);
        let reference = raster_in(&alone.verts, (x0 as usize - 1, y0 as usize - 1, x0 as usize + 9, y0 as usize + 11));
        (0..9).all(|dy| (0..7).all(|dx| pixel(px, x0 + dx as f32 + 0.5, y0 + dy as f32 + 0.5) == pixel(&reference, x0 + dx as f32 + 0.5, y0 + dy as f32 + 0.5)))
    }

    fn badge_pixels_of_colour(px: &[Rgba], h: Hex, col: Rgba) -> usize {
        let c = layout::hex_to_pixel(h);
        let (x0, y0) = (c.0 + SWAMP_BADGE_OFFSET.0, c.1 + SWAMP_BADGE_OFFSET.1);
        (0..9).flat_map(|dy| (0..7).map(move |dx| (dx, dy))).filter(|&(dx, dy)| pixel(px, x0 + dx as f32 + 0.5, y0 + dy as f32 + 0.5) == col).count()
    }

    fn passable_swamp(state: &BattleState) -> Vec<Hex> {
        map_hexes().into_iter().filter(|&h| !h.is_obstacle() && state.ground.get(h) == Some(TerrainKind::Swamp)).collect()
    }

    fn only_alive(state: &mut BattleState, ids: &[usize]) {
        for u in &mut state.units {
            u.alive = false;
        }
        for &i in ids {
            state.units[i].alive = true;
        }
    }

    /// P1 (id 0) active at the Swamp hex (0,-3), E1 (id 3) adjacent at (1,-4).
    fn swamp_duel() -> BattleState {
        hover_duel(battle(23, TerrainPreset::Swamp))
    }

    fn hover_duel(mut state: BattleState) -> BattleState {
        only_alive(&mut state, &[0, 3]);
        state.units[0].pos = hex(0, -3);
        state.units[0].ap = state.units[0].stats.ap_max;
        state.units[3].pos = hex(1, -4);
        state.queue = vec![0];
        state
    }

    fn strip_text(verts: &[Vertex], row: usize, col: Rgba, text: &str) -> Option<(f32, f32, f32, f32)> {
        let (x, y, _, _) = PREVIEW_BOX;
        text_geometry(verts, x + 8.0, y + 6.0 + row as f32 * 16.0, 2.0, col, text).map(|g| bounds(g.into_iter()))
    }

    fn assert_attack_strip_matches_preview(verts: &[Vertex], state: &BattleState, attacker: u32, target: u32) -> Vec<(f32, f32, f32, f32)> {
        let plan = state.preview_attack(attacker, target).expect("valid attack preview");
        let target_name = state.unit(target).unwrap().name;
        let expected = [
            (format!("ATTACK {target_name}: HIT {}%", plan.hit_chance), YELLOW),
            (format!("COST {} AP {} FAT", plan.ap_cost, plan.fatigue_cost), PREVIEW_COST),
            (format!("DAMAGE ON HIT {}-{}", plan.damage_min, plan.damage_max), PREVIEW_COST),
            ("BEFORE ARMOUR. MISSES DEAL 0.".to_string(), PREVIEW_NOTE),
        ];
        let (bx, by, bw, bh) = PREVIEW_BOX;
        expected
            .iter()
            .enumerate()
            .map(|(row, (line, colour))| {
                let geometry = strip_text(verts, row, *colour, line).unwrap_or_else(|| panic!("missing attack row {row}: {line}"));
                assert!(geometry.0 >= bx && geometry.1 >= by && geometry.2 <= bx + bw && geometry.3 <= by + bh, "{geometry:?}");
                if row > 0 {
                    assert!(strip_text(verts, row, YELLOW, line).is_none(), "row {row} has its required colour");
                }
                geometry
            })
            .collect()
    }

    fn assert_unavailable_attack_rows(verts: &[Vertex], state: &BattleState, error: RuleError, warning: &str) {
        assert_eq!(state.preview_attack(0, 3), Err(error));
        let chance = state.hit_chance(0, 3);
        assert!(strip_text(verts, 0, YELLOW, &format!("ATTACK E1: HIT {chance}%")).is_some());
        assert!(strip_text(verts, 1, PREVIEW_WARN, warning).is_some());
        assert!(strip_text(verts, 2, PREVIEW_COST, &format!("DAMAGE ON HIT {}-{}", state.units[0].stats.dmg_min, state.units[0].stats.dmg_max)).is_none());
        assert!(strip_text(verts, 1, PREVIEW_COST, &format!("COST {ATTACK_AP} AP {ATTACK_FATIGUE} FAT")).is_none());
        assert!(!has_row_prefix(verts, 2, "DAMAGE ON HIT"));
        assert!(!has_row_prefix(verts, 1, "COST "));
    }

    fn legend_bounds() -> (f32, f32, f32, f32) {
        let (x, y, step) = SWAMP_LEGEND;
        let lines = swamp_legend_lines();
        let right = lines.iter().map(|(t, _)| x + Painter::text_width(2.0, t)).fold(0.0, f32::max);
        (x, y, right, y + 2.0 * step + 14.0)
    }

    #[test]
    fn swamp_fill_is_distinct_terrain_driven_and_leaves_obstacles_alone() {
        for state in [battle(23, TerrainPreset::Swamp), mixed_map()] {
            let px = raster(&build_scene(&scene(&state, None, false)));
            let swamp = passable_swamp(&state);
            assert!(!swamp.is_empty());
            for h in map_hexes() {
                let want = if h.is_obstacle() {
                    BROWN
                } else if swamp.contains(&h) {
                    SWAMP_FILL
                } else {
                    GREY
                };
                assert_eq!(fill_of(&px, h), want, "{h:?}");
            }
        }
        // Distinct from every other hex fill, so it can never be mistaken for a highlight.
        for other in [GREY, BROWN, GREEN, RED, SWAP_FILL, SWAP_HOVER, STEP_FILL, STEP_HOVER] {
            assert_ne!(SWAMP_FILL, other);
        }
        // Terrain comes from BattleState.ground, not coordinates: the Swamp columns of the
        // preset are plain Ground once the layer is reset, and a lone configured hex is Swamp.
        let mut reset = battle(23, TerrainPreset::Swamp);
        reset.ground = Default::default();
        reset.ground.set(hex(3, 1), TerrainKind::Swamp).unwrap();
        let px = raster(&build_scene(&scene(&reset, None, false)));
        assert_eq!(fill_of(&px, hex(3, 1)), SWAMP_FILL);
        assert_eq!(fill_of(&px, hex(0, 2)), GREY);
    }

    #[test]
    fn swamp_badges_cover_exactly_the_passable_swamp_hexes() {
        for state in [battle(23, TerrainPreset::Swamp), mixed_map()] {
            let verts = build_scene(&scene(&state, None, false));
            let px = raster(&verts);
            let swamp = passable_swamp(&state);
            for h in map_hexes() {
                let drawn = contains_run(&verts, &{
                    let mut p = Painter { verts: Vec::new() };
                    p.swamp_badge(layout::hex_to_pixel(h));
                    p.verts
                });
                assert_eq!(drawn, swamp.contains(&h), "{h:?}");
                assert_eq!(badge_pixels_of_colour(&px, h, SWAMP_BADGE) > 0, swamp.contains(&h), "{h:?}");
                if swamp.contains(&h) {
                    assert!(swamp_badge_visible(&px, h), "{h:?}");
                }
            }
        }
        // The two obstacle hexes the mixed map marks as Swamp keep the plain obstacle look.
        let state = mixed_map();
        assert_eq!(state.ground.get(hex(0, 0)), Some(TerrainKind::Swamp));
        let px = raster(&build_scene(&scene(&state, None, false)));
        assert_eq!(fill_of(&px, hex(0, 0)), BROWN);
        assert_eq!(badge_pixels_of_colour(&px, hex(1, -1), SWAMP_BADGE), 0);
    }

    #[test]
    fn s_badges_stay_visible_over_every_highlight_occupant_overlay_and_battle_state() {
        let mut state = battle(23, TerrainPreset::Swamp);
        for u in state.units.iter_mut().filter(|u| u.side == Side::Player) {
            u.has_rotation = true;
            u.has_footwork = true;
        }
        only_alive(&mut state, &[0, 1, 3]);
        state.units[0].pos = hex(0, 2);
        state.units[0].ap = 9;
        state.units[1].pos = hex(0, 1);
        state.units[3].pos = hex(1, 2);
        state.queue = vec![0];
        for (at, height) in [(hex(0, 3), 1), (hex(-1, 2), 2), (hex(1, 1), 3)] {
            state.elevations.set(at, height).unwrap();
        }
        let reach = state.reachable(0);
        let swamp = passable_swamp(&state);
        assert!(swamp.iter().any(|h| reach.iter().any(|(r, _)| r == h)));
        assert!(!state.legal_rotation_targets(0).is_empty() && !state.legal_footwork_destinations(0).is_empty());

        fn overlays<'a>(s: &'a BattleState, zoc: bool, heights: bool) -> Scene<'a> {
            Scene { control_overlay: zoc, height_overlay: heights, ..scene(s, Some(0), zoc) }
        }
        // (name, scene, hex expected to carry the named highlight fill)
        for (zoc, heights) in [(false, false), (true, false), (false, true), (true, true)] {
            let base = |s| overlays(s, zoc, heights);
            let movement = Scene { reach: &reach, hover: Some(hex(0, 3)), ..base(&state) };
            let attack = Scene { hover: Some(hex(1, 2)), ..base(&state) };
            let rotation = Scene { targeting: Some(TargetAction::Rotation), hover: Some(hex(0, 1)), ..base(&state) };
            let footwork = Scene { targeting: Some(TargetAction::Footwork), hover: state.legal_footwork_destinations(0).first().copied(), ..base(&state) };
            for (name, sc, probe, fill) in [
                ("movement", &movement, hex(0, 3), GREEN),
                ("attack", &attack, hex(1, 2), RED),
                ("rotation", &rotation, hex(0, 1), SWAP_HOVER),
                ("footwork", &footwork, footwork.hover.unwrap(), STEP_HOVER),
            ] {
                let px = raster(&build_scene(sc));
                assert_eq!(fill_of(&px, probe), fill, "{name} highlight keeps precedence over the Swamp fill");
                for &h in &swamp {
                    assert!(swamp_badge_visible(&px, h), "{name} zoc {zoc} heights {heights}: {h:?}");
                }
            }
        }

        // Completed battles keep the badges (the banner strip is below the map).
        let mut over = state.clone();
        over.outcome = Some(Outcome::Victory);
        over.end_reason = Some(EndReason::Elimination);
        let px = raster(&build_scene(&scene(&over, Some(0), false)));
        for &h in &swamp {
            assert!(swamp_badge_visible(&px, h), "completed: {h:?}");
        }
    }

    #[test]
    fn s_badge_geometry_is_inside_the_hex_and_clear_of_rings_labels_bars_and_markers() {
        let neighbours = [(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)];
        for c in map_hexes().into_iter().map(layout::hex_to_pixel).chain([(500.0, 300.0)]) {
            let mut badge = Painter { verts: Vec::new() };
            badge.swamp_badge(c);
            let b = bounds(badge.verts.iter().map(to_px));
            assert!(close(b, (c.0 + 21.25, c.1 + 7.25, c.0 + 28.25, c.1 + 16.25)), "{b:?}");
            let rel = (b.0 - c.0, b.1 - c.1, b.2 - c.0, b.3 - c.1);
            let corners = layout::corners(c, HEX_SIZE);
            for p in badge.verts.iter().map(to_px) {
                let crosses: Vec<f32> = (0..6)
                    .map(|i| {
                        let (a, bb) = (corners[i], corners[(i + 1) % 6]);
                        (bb.0 - a.0) * (p.1 - a.1) - (bb.1 - a.1) * (p.0 - a.0)
                    })
                    .collect();
                assert!(crosses.iter().all(|x| *x >= -0.01) || crosses.iter().all(|x| *x <= 0.01), "badge leaves the hex at {p:?}");
            }
            assert!(nearest_to_origin(rel) > 22.0, "badge touches the unit ring: {rel:?}");
            let glyph = bounds(badge.verts.iter().filter(|v| v.color == SWAMP_BADGE_GLYPH).map(to_px));
            assert!(glyph.0 >= b.0 && glyph.1 >= b.1 && glyph.2 <= b.2 && glyph.3 <= b.3);

            // Everything one hex can draw (own and neighbours', relative to each centre).
            let items_at = |m: (f32, f32)| {
                let text = |x: f32, y: f32, s: f32, t: &str| {
                    let mut p = Painter { verts: Vec::new() };
                    p.text(x, y, s, WHITE, t);
                    bounds(p.verts.iter().map(to_px))
                };
                let mut zoc = Painter { verts: Vec::new() };
                zoc.control_marker(m);
                let mut height = Painter { verts: Vec::new() };
                height.height_badge(m, 3);
                vec![
                    text(m.0 - 6.0, m.1 - 7.0, 2.0, "88"),
                    text(m.0 - 12.0, m.1 - 7.0, 2.0, "P1"),
                    text(m.0 + SWAP_LABEL_OFFSET.0, m.1 + SWAP_LABEL_OFFSET.1, 1.0, "SWAP"),
                    text(m.0 + STEP_LABEL_OFFSET.0, m.1 + STEP_LABEL_OFFSET.1, 1.0, "STEP"),
                    (m.0 - 18.0, m.1 + 20.0, m.0 + 18.0, m.1 + 29.0),
                    bounds(zoc.verts.iter().map(to_px)),
                    bounds(height.verts.iter().map(to_px)),
                ]
            };
            let step = |q: i32, r: i32| {
                let (o, n) = (layout::hex_to_pixel(hex(0, 0)), layout::hex_to_pixel(hex(q, r)));
                (c.0 + n.0 - o.0, c.1 + n.1 - o.1)
            };
            let mut others = items_at(c);
            for (q, r) in neighbours {
                others.extend(items_at(step(q, r)));
            }
            for o in others {
                assert!(!overlap(b, o), "badge {b:?} overlaps {o:?}");
            }
            // Rings of the own and neighbouring tokens (circles, not their bounding squares).
            for (q, r) in std::iter::once((0, 0)).chain(neighbours) {
                let m = step(q, r);
                assert!(nearest_to_origin((b.0 - m.0, b.1 - m.1, b.2 - m.0, b.3 - m.1)) > 22.0, "ring of ({q},{r})");
            }
        }
    }

    #[test]
    fn swamp_legend_is_exact_in_bounds_glyph_supported_and_clear_of_existing_ui() {
        let state = battle(23, TerrainPreset::Swamp);
        assert_eq!(SWAMP_MOVE_AP, 4);
        assert_eq!(swamp_legend_lines().map(|(t, _)| t), ["S = SWAMP".to_string(), "BASE MOVE 4 AP".to_string(), "FATIGUE IN MOVE PREVIEW".to_string()]);
        assert_eq!((SWAMP_LEGEND.0, SWAMP_LEGEND.1, SWAMP_LEGEND.2), (340.0, 44.0, 18.0));
        for heights in [false, true] {
            let sc = Scene { height_overlay: heights, ..scene(&state, None, false) };
            let verts = build_scene(&sc);
            for (i, (line, col)) in swamp_legend_lines().iter().enumerate() {
                assert!(text_geometry(&verts, 340.0, 44.0 + 18.0 * i as f32, 2.0, *col, line).is_some(), "{line}");
            }
            // The height legend is preserved, on the left and clear of the Swamp legend.
            let h = height_legend_lines(heights);
            let mut height_right = 0.0f32;
            for (i, (line, col)) in h.iter().enumerate() {
                let g = text_geometry(&verts, 16.0, 44.0 + 18.0 * i as f32, 2.0, *col, line).expect("height legend row");
                height_right = height_right.max(bounds(g.into_iter()).2);
            }
            let lb = legend_bounds();
            assert!(height_right < lb.0 - 20.0, "{height_right} {lb:?}");
            // Window, top bar (ends y 26), skill strip (starts x 684) and the map (starts y 97).
            assert!(lb.0 >= 0.0 && lb.2 <= 684.0 - 20.0 && lb.1 > 12.0 + 14.0 && lb.3 < 97.0, "{lb:?}");
            let map_top = map_hexes().into_iter().map(|h| layout::hex_to_pixel(h).1 - HEX_SIZE).fold(f32::MAX, f32::min);
            assert!(lb.3 < map_top);
            // Nothing but the legend's own text lies in its rectangle.
            let mut alone = Painter { verts: Vec::new() };
            alone.rect(0.0, 0.0, WIN_W, WIN_H, BG);
            for (i, (line, col)) in swamp_legend_lines().iter().enumerate() {
                alone.text(340.0, 44.0 + 18.0 * i as f32, 2.0, *col, line);
            }
            let (got, want) = (raster(&verts), raster(&alone.verts));
            for y in lb.1 as usize..=lb.3.ceil() as usize {
                for x in lb.0 as usize..=lb.2.ceil() as usize {
                    assert_eq!(got[y * WIN_W as usize + x], want[y * WIN_W as usize + x], "({x},{y})");
                }
            }
            // The skill buttons are untouched by the legend.
            for r in [layout::ADRENALINE_BUTTON, layout::INDOMITABLE_BUTTON] {
                assert!(lb.2 < r.x);
            }
        }
        for (line, _) in swamp_legend_lines() {
            for ch in line.chars().filter(|c| *c != ' ') {
                assert_ne!(font::glyph(ch), [0; 7], "unsupported glyph {ch:?} in {line}");
            }
        }
    }

    #[test]
    fn all_ground_maps_keep_the_above_target_label_and_add_the_attack_strip() {
        let maps = [BattleState::new(23), battle(23, TerrainPreset::Flat), battle(23, TerrainPreset::Hills), obstacle_only_swamp()];
        for mut state in maps {
            assert!(!has_visible_swamp(&state));
            state = hover_duel(state);
            let sc = Scene { hover: Some(hex(1, -4)), height_overlay: true, ..scene(&state, Some(0), true) };
            let verts = build_scene(&sc);
            for col in [SWAMP_FILL, SWAMP_BADGE, SWAMP_BADGE_GLYPH] {
                assert_eq!(count_color(&verts, col), 0);
            }
            assert!(text_geometry(&verts, 340.0, 44.0, 2.0, SWAMP_BADGE, "S = SWAMP").is_none());
            let c = layout::hex_to_pixel(hex(1, -4));
            let hit = format!("HIT {}%", state.hit_chance(0, 3));
            assert!(text_geometry(&verts, c.0 - 36.0, c.1 - 46.0, 2.0, YELLOW, &hit).is_some(), "above-target label unchanged");
            assert_attack_strip_matches_preview(&verts, &state, 0, 3);
            let px = raster(&verts);
            assert_ne!(pixel(&px, 20.0, 600.0), BG);
            assert!(count_color(&verts, PREVIEW_BG) > 0);
        }
    }

    #[test]
    fn hovered_enemy_at_1_minus_4_puts_all_attack_rows_in_the_strip_clear_of_the_legend_and_badges() {
        let state = swamp_duel();
        assert_eq!(state.legal_targets(0), vec![3]);
        let sc = Scene { hover: Some(hex(1, -4)), height_overlay: true, control_overlay: true, ..scene(&state, Some(0), true) };
        let verts = build_scene(&sc);
        // The preview background and all four rows, at scale two.
        let (bx, by, bw, bh) = PREVIEW_BOX;
        let mut bg = Painter { verts: Vec::new() };
        bg.rect(bx, by, bw, bh, PREVIEW_BG);
        assert!(contains_run(&verts, &bg.verts));
        let rows = assert_attack_strip_matches_preview(&verts, &state, 0, 3);

        // The rejected T49 collision: the label above the target would have met the legend.
        let c = layout::hex_to_pixel(hex(1, -4));
        let old = (c.0 - 36.0, c.1 - 46.0, c.0 - 36.0 + Painter::text_width(2.0, &format!("HIT {}%", state.hit_chance(0, 3))), c.1 - 46.0 + 14.0);
        let legend = legend_bounds();
        assert!(overlap(old, legend), "fixture reproduces the T49 collision: {old:?} vs {legend:?}");
        assert!(text_geometry(&verts, c.0 - 36.0, c.1 - 46.0, 2.0, YELLOW, &format!("HIT {}%", state.hit_chance(0, 3))).is_none());

        // All rendered rows meet neither legend row nor any terrain badge.
        for row in &rows {
            assert!(!overlap(*row, legend));
            for (i, (line, col)) in swamp_legend_lines().iter().enumerate() {
                let g = text_geometry(&verts, 340.0, 44.0 + 18.0 * i as f32, 2.0, *col, line).expect("legend row");
                assert!(!overlap(*row, bounds(g.into_iter())));
            }
        }
        let px = raster(&verts);
        for h in passable_swamp(&state) {
            let c = layout::hex_to_pixel(h);
            let badge = (c.0 + 21.25, c.1 + 7.25, c.0 + 28.25, c.1 + 16.25);
            assert!(rows.iter().all(|row| !overlap(*row, badge)), "{h:?}");
            assert!(swamp_badge_visible(&px, h), "{h:?}");
        }
        // The hovered hex keeps its red attack fill and the enemy token its place.
        assert_eq!(fill_of(&px, hex(1, -4)), RED);
    }

    #[test]
    fn attack_row_covers_each_enemy_and_the_hit_chance_clamp_limits() {
        for (enemy, name) in [(3usize, "E1"), (4, "E2"), (5, "E3")] {
            let mut state = battle(23, TerrainPreset::Swamp);
            only_alive(&mut state, &[0, enemy]);
            state.units[0].pos = hex(0, 0 + 1);
            state.units[0].ap = 9;
            state.units[enemy].pos = hex(1, 1);
            state.queue = vec![0];
            assert_eq!(state.units[enemy].name, name);
            for (skill, defense, want) in [(60, 5, None), (1000, 0, Some(95)), (0, 1000, Some(5))] {
                state.units[0].stats.melee_skill = skill;
                state.units[enemy].stats.defense = defense;
                let chance = state.hit_chance(0, enemy as u32);
                if let Some(w) = want {
                    assert_eq!(chance, w);
                }
                let verts = build_scene(&Scene { hover: Some(hex(1, 1)), ..scene(&state, Some(0), false) });
                assert_attack_strip_matches_preview(&verts, &state, 0, enemy as u32);
            }
        }
        // A player target is never a legal attack target for a player: no row, no red fill.
        let mut state = battle(23, TerrainPreset::Swamp);
        only_alive(&mut state, &[0, 1]);
        state.units[0].pos = hex(0, 1);
        state.units[1].pos = hex(1, 1);
        state.queue = vec![0];
        let verts = build_scene(&Scene { hover: Some(hex(1, 1)), ..scene(&state, Some(0), false) });
        assert!(strip_text(&verts, 0, YELLOW, "ATTACK P2: HIT 50%").is_none());
        assert!(!verts.iter().any(|v| v.color == PREVIEW_BG));
        // An enemy-controlled active unit shows no row for the hovered player either.
        let mut state = battle(23, TerrainPreset::Swamp);
        only_alive(&mut state, &[0, 3]);
        state.units[0].pos = hex(0, 1);
        state.units[3].pos = hex(1, 1);
        state.queue = vec![3];
        for selected in [3u32, 0] {
            let verts = build_scene(&Scene { hover: Some(hex(0, 1)), ..scene(&state, Some(selected), false) });
            assert!(!verts.iter().any(|v| v.color == PREVIEW_BG), "selected {selected}");
        }
    }

    #[test]
    fn attack_strip_rows_match_preview_on_flat_hills_swamp_and_mixed_terrain() {
        let maps = [
            battle(23, TerrainPreset::Flat),
            battle(23, TerrainPreset::Hills),
            battle(23, TerrainPreset::Swamp),
            mixed_map(),
        ];
        for map in maps {
            let state = hover_duel(map);
            assert_eq!(state.legal_targets(0), vec![3]);
            let preview = state.preview_attack(0, 3).unwrap();
            let verts = build_scene(&Scene { hover: Some(hex(1, -4)), ..scene(&state, Some(0), false) });
            let rows = assert_attack_strip_matches_preview(&verts, &state, 0, 3);
            assert_eq!(rows.len(), 4);
            assert!(strip_text(&verts, 1, PREVIEW_COST, &format!("COST {} AP {} FAT", preview.ap_cost, preview.fatigue_cost)).is_some());
            assert!(strip_text(&verts, 2, PREVIEW_COST, &format!("DAMAGE ON HIT {}-{}", preview.damage_min, preview.damage_max)).is_some());
            assert!(!has_row_prefix(&verts, 0, "MOVE AP"), "an occupied enemy hex has no movement preview");
            assert!(movement_preview(&Scene { hover: Some(hex(1, -4)), ..scene(&state, Some(0), false) }).is_none());
        }
    }

    #[test]
    fn existing_attack_and_stunning_blow_rows_follow_killing_frenzy_and_expiry() {
        let args = bb_rules::scenario::parse_args(["--killing-frenzy", "--stunning-blow", "--berserk"].map(String::from)).unwrap();
        let mut state = bb_rules::scenario::launch_battle_for(args);
        let park = [hex(-4, 2), hex(-4, 3), hex(-4, 4), hex(4, -4), hex(4, -3), hex(4, -2)];
        for (unit, at) in state.units.iter_mut().zip(park) {
            unit.pos = at;
        }
        state.units[0].pos = hex(0, -2);
        state.units[3].pos = hex(1, -2);
        state.units[4].pos = hex(0, -1);
        state.units[3].hp = 1;
        state.units[3].armour = 0;
        state.units[4].armour = 0;
        state.units[0].ap = state.units[0].stats.ap_max;
        state.units[0].turn_started = true;
        state.queue = vec![0, 1, 2, 3, 4, 5];
        assert!(state.units[0].has_killing_frenzy && state.units[0].has_stunning_blow && state.units[0].has_berserk);
        assert_eq!(state.units[0].killing_frenzy_turns, 0);
        assert_eq!(state.legal_stunning_blow_targets(0), vec![3, 4]);
        assert!(frenzy_rows(&build_scene(&scene(&state, None, false))).is_empty(), "no marker before a real qualifying kill");

        let ordinary_damage = |state: &BattleState| {
            let preview = state.preview_attack(0, 4).unwrap();
            let (hash, rng) = (state.state_hash(), state.rng.clone());
            let verts = build_scene(&Scene { hover: Some(hex(0, -1)), ..scene(state, Some(0), false) });
            assert_attack_strip_matches_preview(&verts, state, 0, 4);
            assert_eq!((state.state_hash(), state.rng.clone()), (hash, rng), "scene and preview are pure");
            (preview.damage_min, preview.damage_max)
        };
        let stunning_damage = |state: &BattleState| {
            let preview = state.preview_stunning_blow(0, 4).unwrap();
            let (hash, rng) = (state.state_hash(), state.rng.clone());
            let verts = build_scene(&blow_scene(state, Some(hex(0, -1))));
            assert!(preview_row(&verts, 2, &format!("DAMAGE ON HIT {}-{} BEFORE ARMOUR. MISSES DEAL 0.", preview.damage_min, preview.damage_max)));
            assert_eq!((state.state_hash(), state.rng.clone()), (hash, rng), "scene and preview are pure");
            (preview.damage_min, preview.damage_max)
        };

        let base_attack = ordinary_damage(&state);
        let base_stun = stunning_damage(&state);
        let kill = Command::Attack { attacker: 0, target: 3 };
        let hit_rng = (1..100_000).map(bb_rules::Rng::new).find(|rng| {
            let mut probe = state.clone();
            probe.rng = rng.clone();
            probe.apply(kill).is_ok_and(|events| events.iter().any(|event| matches!(event, bb_rules::Event::Died { unit: 3 })))
        }).expect("the scripted paid attack can kill E1");
        state.rng = hit_rng;
        let events = state.apply(kill).unwrap();
        assert!(events.iter().any(|event| matches!(event, bb_rules::Event::Died { unit: 3 })));
        assert_eq!(state.units[0].killing_frenzy_turns, 2);
        assert_eq!(frenzy_rows(&build_scene(&scene(&state, None, false))), vec![0], "marker appears at counter 2");
        let active_attack = ordinary_damage(&state);
        let active_stun = stunning_damage(&state);

        // Pass two complete rounds through the real EndTurn/begin_turn path. The first fresh P1
        // entry ages 2 to 1; the next ages 1 to 0 and restores the base preview rows.
        let mut saw_counter_one = false;
        for _ in 0..40 {
            let active_id = state.active_unit().expect("battle remains active").id;
            if active_id == 0 && state.round == 2 && state.units[0].killing_frenzy_turns == 1 {
                assert_eq!(frenzy_rows(&build_scene(&scene(&state, None, false))), vec![0], "marker remains at counter 1");
                saw_counter_one = true;
            }
            if active_id == 0 && state.round >= 3 && state.units[0].killing_frenzy_turns == 0 {
                break;
            }
            state.apply(Command::EndTurn { unit: active_id }).expect("end the current fresh turn");
        }
        assert!(saw_counter_one, "real fresh-turn expiration path reached counter 1");
        assert_eq!((state.active_unit().map(|u| u.id), state.round, state.units[0].killing_frenzy_turns), (Some(0), 3, 0));
        assert!(frenzy_rows(&build_scene(&scene(&state, None, false))).is_empty(), "marker disappears at expiry");
        let expired_attack = ordinary_damage(&state);
        let expired_stun = stunning_damage(&state);
        assert_eq!(base_attack, base_stun);
        assert_eq!(active_attack, active_stun);
        assert_eq!(expired_attack, base_attack, "expired Frenzy returns the ordinary Attack row to base damage");
        assert_eq!(expired_stun, base_stun, "expired Frenzy returns the Stunning Blow row to base damage");
        assert_eq!((base_attack, active_attack), ((20, 35), (25, 43)));
    }

    #[test]
    fn attack_strip_uses_live_indomitable_and_preserves_odd_zero_and_full_width_damage_ranges() {
        for (minimum, maximum, active, expected) in [
            (13, 27, false, "DAMAGE ON HIT 13-27"),
            (13, 27, true, "DAMAGE ON HIT 6-13"),
            (1, 1, true, "DAMAGE ON HIT 0-0"),
            (0, u32::MAX, false, "DAMAGE ON HIT 0-4294967295"),
        ] {
            let mut state = hover_duel(battle(23, TerrainPreset::Flat));
            state.units[0].stats.dmg_min = minimum;
            state.units[0].stats.dmg_max = maximum;
            state.units[3].has_indomitable = true;
            state.units[3].indomitable_active = active;
            let plan = state.preview_attack(0, 3).unwrap();
            let target_name = state.units[3].name;
            let lines = [
                (format!("ATTACK {target_name}: HIT {}%", plan.hit_chance), YELLOW),
                (format!("COST {} AP {} FAT", plan.ap_cost, plan.fatigue_cost), PREVIEW_COST),
                (expected.to_string(), PREVIEW_COST),
                ("BEFORE ARMOUR. MISSES DEAL 0.".to_string(), PREVIEW_NOTE),
            ];
            for height_overlay in [false, true] {
                for control_overlay in [false, true] {
                    let sc = Scene { hover: Some(hex(1, -4)), height_overlay, control_overlay, ..scene(&state, Some(0), false) };
                    let verts = build_scene(&sc);
                    let rows = assert_attack_strip_matches_preview(&verts, &state, 0, 3);
                    assert!(strip_text(&verts, 2, PREVIEW_COST, expected).is_some(), "{minimum}..={maximum}, active={active}");
                    for (row, ((text, _), rect)) in lines.iter().zip(&rows).enumerate() {
                        assert!(Painter::text_width(2.0, text) <= PREVIEW_BOX.2 - 16.0, "row {row}: {text}");
                        assert!(rect.2 < PANEL_X - 12.0 && rect.3 < WIN_H - 60.0, "row {row}: {rect:?}");
                        assert!(text.chars().filter(|ch| *ch != ' ').all(|ch| font::glyph(ch) != [0; 7]), "unsupported glyph in {text}");
                        for other in &rows[row + 1..] {
                            assert!(!overlap(*rect, *other), "row {row}: {rect:?} vs {other:?}");
                        }
                    }
                }
            }
        }
    }

    #[test]
    fn attack_strip_keeps_the_target_chance_and_resource_warning_without_cost_or_damage() {
        let cases = [
            ("AP", RuleError::NotEnoughAp, "UNAVAILABLE: NOT ENOUGH AP"),
            ("fatigue", RuleError::TooFatigued, "UNAVAILABLE: TOO FATIGUED"),
            ("overflow", RuleError::TooFatigued, "UNAVAILABLE: TOO FATIGUED"),
        ];
        for (kind, error, warning) in cases {
            let mut state = hover_duel(battle(23, TerrainPreset::Flat));
            match kind {
                "AP" => state.units[0].ap = ATTACK_AP - 1,
                "fatigue" => state.units[0].fatigue = state.units[0].stats.fatigue_max,
                "overflow" => {
                    state.units[0].stats.fatigue_max = u32::MAX;
                    state.units[0].fatigue = u32::MAX;
                }
                _ => unreachable!(),
            }
            assert!(state.units[0].ap <= state.units[0].stats.ap_max);
            assert!(state.units[0].fatigue <= state.units[0].stats.fatigue_max);
            let verts = build_scene(&Scene { hover: Some(hex(1, -4)), ..scene(&state, Some(0), false) });
            assert_unavailable_attack_rows(&verts, &state, error, warning);
        }

        let mut exact = hover_duel(battle(23, TerrainPreset::Flat));
        exact.units[0].ap = ATTACK_AP;
        exact.units[0].fatigue = exact.units[0].stats.fatigue_max - ATTACK_FATIGUE;
        assert_attack_strip_matches_preview(
            &build_scene(&Scene { hover: Some(hex(1, -4)), ..scene(&exact, Some(0), false) }),
            &exact,
            0,
            3,
        );
    }

    #[test]
    fn attack_strip_suppresses_allied_dead_escaped_and_nonadjacent_targets() {
        for kind in ["allied", "dead", "escaped", "nonadjacent"] {
            let mut state = hover_duel(battle(23, TerrainPreset::Swamp));
            match kind {
                "allied" => {
                    state.units[1].alive = true;
                    state.units[1].pos = hex(1, -4);
                    state.units[3].alive = false;
                    state.units[3].pos = hex(4, -4);
                }
                "dead" => state.units[3].alive = false,
                "escaped" => state.units[3].fled = true,
                "nonadjacent" => state.units[3].pos = hex(2, -4),
                _ => unreachable!(),
            }
            let target_hex = if kind == "allied" { hex(1, -4) } else if kind == "nonadjacent" { hex(2, -4) } else { hex(1, -4) };
            let verts = build_scene(&Scene { hover: Some(target_hex), ..scene(&state, Some(0), false) });
            assert!(!has_row_prefix(&verts, 0, "ATTACK "), "{kind}");
            assert!(!has_row_prefix(&verts, 1, "UNAVAILABLE: "), "{kind}");
        }
    }

    #[test]
    fn attack_row_needs_a_running_battle_the_active_player_and_no_targeting_mode() {
        let mut state = swamp_duel();
        state.units[0].has_rotation = true;
        state.units[0].has_footwork = true;
        only_alive(&mut state, &[0, 1, 3]);
        state.units[1].pos = hex(-1, -3);
        let shown = |s: &BattleState, selected, targeting| {
            let verts = build_scene(&Scene { hover: Some(hex(1, -4)), targeting, ..scene(s, selected, false) });
            strip_text(&verts, 0, YELLOW, &format!("ATTACK E1: HIT {}%", s.hit_chance(0, 3))).is_some()
        };
        assert!(shown(&state, Some(0), None));
        assert!(!shown(&state, None, None), "no selection");
        assert!(!shown(&state, Some(1), None), "selected player is not the active unit");
        assert!(!shown(&state, Some(0), Some(TargetAction::Rotation)), "Rotation targeting open");
        assert!(!shown(&state, Some(0), Some(TargetAction::Footwork)), "Footwork targeting open");
        let mut retreating = state.clone();
        retreating.units[0].retreating = true;
        assert!(!shown(&retreating, Some(0), None));
        let mut fleeing = state.clone();
        fleeing.units[0].morale = Morale::Fleeing;
        assert!(!shown(&fleeing, Some(0), None));
        let mut over = state.clone();
        over.outcome = Some(Outcome::Defeat);
        over.end_reason = Some(EndReason::Elimination);
        assert!(!shown(&over, Some(0), None), "completed battle");
        let verts = build_scene(&Scene { hover: Some(hex(1, -4)), ..scene(&over, Some(0), false) });
        let (bx, by, ..) = BANNER_BOX;
        assert!(contains_run(&verts, &text_run(bx + 16.0, by + 8.0, 5.0, RED, "DEFEAT")), "outcome banner kept");
    }

    #[test]
    fn the_strip_shows_one_state_at_a_time_without_overlapping_rows() {
        let (bx, by, bw, bh) = PREVIEW_BOX;
        let inside = |b: (f32, f32, f32, f32)| b.0 >= bx && b.1 >= by && b.2 <= bx + bw && b.3 <= by + bh;
        let disjoint = |rows: &[(f32, f32, f32, f32)]| {
            for (i, a) in rows.iter().enumerate() {
                assert!(inside(*a), "{a:?}");
                for b in &rows[i + 1..] {
                    assert!(!overlap(*a, *b), "{a:?} {b:?}");
                }
            }
        };
        let mut state = battle(23, TerrainPreset::Swamp);
        for u in state.units.iter_mut().filter(|u| u.side == Side::Player) {
            u.has_rotation = true;
            u.has_footwork = true;
        }
        only_alive(&mut state, &[0, 1, 3]);
        state.units[0].pos = hex(-2, 3);
        state.units[0].ap = 9;
        state.units[1].pos = hex(-3, 3);
        state.units[3].pos = hex(-1, 3);
        state.queue = vec![0];

        // Attack feedback: four rules-derived rows.
        let verts = build_scene(&Scene { hover: Some(hex(-1, 3)), ..scene(&state, Some(0), false) });
        let rows = assert_attack_strip_matches_preview(&verts, &state, 0, 3);
        disjoint(&rows);

        // Ordinary movement preview (Swamp destination costs come from the rules plan).
        let plan = state.preview_move(0, hex(-1, 2)).unwrap();
        assert_eq!(plan.ap_cost, SWAMP_MOVE_AP);
        let verts = build_scene(&Scene { hover: Some(hex(-1, 2)), ..scene(&state, Some(0), false) });
        let lines = movement_preview_lines(&plan);
        let rows: Vec<_> = lines.iter().enumerate().map(|(i, (t, c))| strip_text(&verts, i, *c, t).unwrap()).collect();
        disjoint(&rows);
        assert!(!has_row_prefix(&verts, 0, "ATTACK "));

        // Targeting previews.
        let rot = state.preview_rotation(0, 1).unwrap();
        let verts = build_scene(&Scene { targeting: Some(TargetAction::Rotation), hover: Some(hex(-3, 3)), ..scene(&state, Some(0), false) });
        let rows: Vec<_> = rotation_target_lines(&state, 0, Some(&rot)).iter().enumerate().map(|(i, (t, c))| strip_text(&verts, i, *c, t).unwrap()).collect();
        disjoint(&rows);
        let dest = state.legal_footwork_destinations(0)[0];
        let fw = state.preview_footwork(0, dest).unwrap();
        let verts = build_scene(&Scene { targeting: Some(TargetAction::Footwork), hover: Some(dest), ..scene(&state, Some(0), false) });
        let rows: Vec<_> = footwork_target_lines(&state, 0, Some(&fw)).iter().enumerate().map(|(i, (t, c))| strip_text(&verts, i, *c, t).unwrap()).collect();
        disjoint(&rows);
        assert!(!has_row_prefix(&verts, 0, "ATTACK "));

        // Completed battle: the banner owns the strip and no preview text is drawn on it.
        state.outcome = Some(Outcome::Victory);
        state.end_reason = Some(EndReason::Elimination);
        let verts = build_scene(&Scene { hover: Some(hex(-1, 3)), ..scene(&state, Some(0), false) });
        assert!(contains_run(&verts, &text_run(bx + 16.0, by + 8.0, 5.0, GREEN, "VICTORY")));
        assert!(!verts.iter().any(|v| v.color == PREVIEW_BG));
        assert!(!has_row_prefix(&verts, 0, "ATTACK "));
    }

    #[test]
    fn attack_hover_scenes_are_pure_and_fit_the_vertex_budget() {
        for map in [battle(23, TerrainPreset::Flat), battle(23, TerrainPreset::Hills), battle(23, TerrainPreset::Swamp), mixed_map()] {
            let mut state = hover_duel(map);
            state.units[0].has_rotation = true;
            state.units[0].has_footwork = true;
            state.units[0].has_indomitable = true;
            state.units[0].fast_adaptation_stacks = 2;
            let (hash, rng, units, queue, outcome, reason) = (state.state_hash(), state.rng.clone(), state.units.clone(), state.queue.clone(), state.outcome, state.end_reason);
            let reach = state.reachable(0);
            let sc = Scene { reach: &reach, hover: Some(hex(1, -4)), control_overlay: true, height_overlay: true, ..scene(&state, Some(0), true) };
            let (a, b) = (build_scene(&sc), build_scene(&sc));
            assert!(a.len() == b.len() && a.iter().zip(&b).all(|(x, y)| same(x, y)));
            assert!(a.len() < MAX_VERTS, "{}", a.len());
            assert_attack_strip_matches_preview(&a, &state, 0, 3);
            assert_eq!((state.state_hash(), &state.rng, &state.units, &state.queue, state.outcome, state.end_reason), (hash, &rng, &units, &queue, outcome, reason));
        }
        // Rendering reads terrain only: the hash differs from the all-Ground map, the scene
        // geometry differs too, and neither changed the state.
        let (swamp, ground) = (battle(23, TerrainPreset::Swamp), battle(23, TerrainPreset::Flat));
        assert_ne!(swamp.state_hash(), ground.state_hash());
        assert_ne!(build_scene(&scene(&swamp, None, false)).len(), build_scene(&scene(&ground, None, false)).len());
    }

    // --- Recover button ------------------------------------------------------------

    fn recover_button_run(enabled: bool) -> Vec<Vertex> {
        let mut p = Painter { verts: Vec::new() };
        p.recover_button(enabled);
        p.verts
    }

    /// Active, started P1 on the stock Flat battle with `ap` and `fatigue`.
    fn recover_state(ap: u32, fatigue: u32) -> BattleState {
        let mut state = BattleState::new(1);
        let u = &mut state.units[0];
        u.ap = ap;
        u.fatigue = fatigue;
        // Keep the fixture's resources legal: raise the maxima to cover surplus AP or fatigue.
        u.stats.ap_max = u.stats.ap_max.max(ap);
        u.stats.fatigue_max = u.stats.fatigue_max.max(fatigue);
        assert!(u.ap <= u.stats.ap_max && u.fatigue <= u.stats.fatigue_max);
        state
    }

    #[test]
    fn recover_button_is_drawn_exactly_on_the_shared_hit_rectangle_with_the_existing_skill_look() {
        let r = layout::RECOVER_BUTTON;
        assert_eq!((r.x, r.y, r.w, r.h), (724.0, 48.0, 144.0, 40.0));
        assert_eq!(layout::ui_hit(r.x + 1.0, r.y + 1.0), Some(layout::UiHit::Recover));
        for enabled in [true, false] {
            let verts = recover_button_run(enabled);
            assert!(close(bounds(verts.iter().map(to_px)), rect_bounds(r)), "enabled={enabled}");
            let want = if enabled { SKILL_ENABLED } else { SKILL_DISABLED };
            assert_eq!(skill_fill(&verts, r), Some(want));
            let text = if enabled { WHITE } else { BUTTON_DISABLED_TEXT };
            assert!(text_geometry(&verts, r.x + SKILL_TEXT_X, r.y + SKILL_TITLE_DY, layout::SKILL_TITLE_SCALE, text, "RECOVER (5)").is_some());
            for (i, row) in ["COST 9 AP", "HALVE FAT, END TURN"].iter().enumerate() {
                assert!(text_geometry(&verts, r.x + SKILL_TEXT_X, r.y + SKILL_INFO_DY + i as f32 * 8.0, 1.0, text, row).is_some(), "{row}");
            }
        }
        // The cost row is formatted from the accepted rules constant.
        assert_eq!(recover_info_lines()[0], format!("COST {} AP", bb_rules::rules::RECOVER_AP));
        assert_eq!(layout::SKILL_TITLE_SCALE, 1.25);
    }

    #[test]
    fn recover_button_text_fits_and_uses_supported_glyphs() {
        let r = layout::RECOVER_BUTTON;
        let w = Painter::text_width(layout::SKILL_TITLE_SCALE, layout::RECOVER_LABEL);
        assert!(SKILL_TEXT_X + w + 8.0 <= r.w, "{w}");
        for line in recover_info_lines() {
            assert!(SKILL_TEXT_X + Painter::text_width(1.0, &line) + 8.0 <= r.w, "{line}");
            for c in line.chars().filter(|c| *c != ' ') {
                assert_ne!(font::glyph(c), [0; 7], "unsupported glyph {c:?}");
            }
        }
        for c in layout::RECOVER_LABEL.chars().filter(|c| *c != ' ') {
            assert_ne!(font::glyph(c), [0; 7], "unsupported glyph {c:?}");
        }
        assert!(SKILL_TITLE_DY + 7.0 * layout::SKILL_TITLE_SCALE < SKILL_INFO_DY && SKILL_INFO_DY + 8.0 + 7.0 + 2.0 <= r.h);
    }

    #[test]
    fn recover_button_enablement_follows_the_rules_query_for_the_selected_controllable_active_player() {
        let enabled = |state: &BattleState, selected: Option<u32>| {
            let sc = scene(state, selected, false);
            let on = recover_enabled(&sc);
            let verts = build_scene(&sc);
            let want = if on { SKILL_ENABLED } else { SKILL_DISABLED };
            assert_eq!(skill_fill(&verts, layout::RECOVER_BUTTON), Some(want));
            on
        };
        // Exact boundary, zero and odd fatigue are eligible.
        for (ap, fatigue) in [(bb_rules::rules::RECOVER_AP, 0), (bb_rules::rules::RECOVER_AP, 41), (9, 7)] {
            assert!(enabled(&recover_state(ap, fatigue), Some(0)), "AP {ap} fatigue {fatigue}");
        }
        for ap in 0..bb_rules::rules::RECOVER_AP {
            assert!(!enabled(&recover_state(ap, 20), Some(0)), "AP {ap}");
        }
        assert!(!enabled(&recover_state(9, 20), None), "nothing selected");
        assert!(!enabled(&recover_state(9, 20), Some(1)), "selected but not active");
        let mut enemy = recover_state(9, 20);
        enemy.queue = vec![3, 0];
        assert!(!enabled(&enemy, Some(3)), "enemy turn");
        assert!(!enabled(&enemy, Some(0)), "player not active");
        let mut fleeing = recover_state(9, 20);
        fleeing.units[0].morale = Morale::Fleeing;
        assert!(!enabled(&fleeing, Some(0)));
        let mut retreating = recover_state(9, 20);
        retreating.units[0].retreating = true;
        assert!(!enabled(&retreating, Some(0)));
        let mut absent = recover_state(9, 20);
        absent.units[0].alive = false;
        assert!(!enabled(&absent, Some(0)));
        let mut over = recover_state(9, 20);
        over.outcome = Some(Outcome::Victory);
        assert!(!enabled(&over, Some(0)));
        // Agreement with the rules query wherever the scene's own gates pass.
        for ap in [0, 8, 9, 15] {
            let state = recover_state(ap, 12);
            assert_eq!(recover_enabled(&scene(&state, Some(0), false)), state.recover_availability(0).is_ok());
        }
    }

    #[test]
    fn recover_button_clears_every_other_element_on_all_terrains_and_overlay_combinations() {
        let r = layout::RECOVER_BUTTON;
        let inside = (r.x as usize, r.y as usize, r.right().ceil() as usize, r.bottom().ceil() as usize);
        let px = |verts: &[Vertex]| raster_in(verts, inside);
        for preset in [TerrainPreset::Flat, TerrainPreset::Hills, TerrainPreset::Swamp] {
            let mut state = battle(1, preset);
            state.units[0].ap = 9;
            for (heights, zoc, selected) in [(false, false, Some(0)), (true, false, Some(0)), (false, true, Some(0)), (true, true, Some(0)), (true, true, None)] {
                let reach = state.reachable(0);
                let sc = Scene { height_overlay: heights, hover: Some(hex(0, 0)), reach: &reach, ..scene(&state, selected, zoc) };
                let verts = build_scene(&sc);
                let enabled = recover_enabled(&sc);
                // Nothing but the button (over the plain background) is drawn in its rectangle.
                let mut alone = Painter { verts: Vec::new() };
                alone.rect(0.0, 0.0, WIN_W, WIN_H, BG);
                alone.recover_button(enabled);
                let (got, want) = (px(&verts), px(&alone.verts));
                for y in inside.1..inside.3 {
                    for x in inside.0..inside.2 {
                        assert_eq!(got[y * WIN_W as usize + x], want[y * WIN_W as usize + x], "{preset:?} H={heights} Z={zoc} ({x},{y})");
                    }
                }
                // Map hexes (including their outlines), the legends and the panel are elsewhere.
                for h in map_hexes() {
                    let c = layout::hex_to_pixel(h);
                    let hexbox = Rect::new(c.0 - HEX_SIZE, c.1 - HEX_SIZE, 2.0 * HEX_SIZE, 2.0 * HEX_SIZE);
                    assert!(!r.overlaps(&hexbox), "{preset:?} {h:?}");
                }
            }
            let sc = scene(&state, Some(0), false);
            let verts = build_scene(&sc);
            let (lx, ly, step) = HEIGHT_LEGEND;
            for (i, (line, col)) in height_legend_lines(false).iter().enumerate() {
                let g = text_geometry(&verts, lx, ly + i as f32 * step, 2.0, *col, line).expect("height legend row");
                let b = bounds(g.into_iter());
                assert!(!r.overlaps(&Rect::new(b.0, b.1, b.2 - b.0, b.3 - b.1)));
            }
            if has_visible_swamp(&state) {
                let (lb, rb) = (legend_bounds(), rect_bounds(r));
                assert!(!overlap(lb, rb), "{lb:?}");
            }
        }
        // Skill strip, launch buttons, dialog and the side panel (background from PANEL_X - 12).
        for other in [layout::ADRENALINE_BUTTON, layout::ROTATION_BUTTON, layout::FOOTWORK_BUTTON, layout::INDOMITABLE_BUTTON, layout::END_ROUND_BUTTON, layout::RETREAT_BUTTON, layout::CONFIRM_BUTTON, layout::CANCEL_BUTTON, layout::DIALOG] {
            assert!(!r.overlaps(&other), "{other:?}");
        }
        assert!(r.y > layout::ROTATION_BUTTON.bottom() && r.right() < PANEL_X - 12.0);
        // The first unit row (y 52) and its text start in the side panel, right of the button.
        assert!(r.right() < PANEL_X);
        // The top bar for the longest seed ends above the button.
        assert!(r.y > 12.0 + 14.0);
    }

    #[test]
    fn recover_button_drawing_is_pure() {
        for preset in [TerrainPreset::Flat, TerrainPreset::Hills, TerrainPreset::Swamp] {
            let state = battle(1, preset);
            let (hash, rng) = (state.state_hash(), state.rng.clone());
            let sc = scene(&state, Some(0), false);
            let (a, b) = (build_scene(&sc), build_scene(&sc));
            assert!(a.len() == b.len() && a.iter().zip(&b).all(|(x, y)| same(x, y)));
            assert_eq!((state.state_hash(), state.rng.clone()), (hash, rng));
        }
    }

    // --- REACH 2 tag (APPROXIMATION: presentation of the --two-hex launch option) ---------

    const REACH_COLOR: Rgba = [0.55, 0.78, 1.0, 1.0];

    fn two_hex_args() -> bb_rules::scenario::LaunchArgs {
        bb_rules::scenario::LaunchArgs { fast_adaptation: false, two_hex: true, ..fa_args() }
    }

    /// Expected tag geometry, derived independently of the production box helper: scale 1, right
    /// edge 8 px inside the window, 3 px below the row top.
    fn reach_run(row: u32) -> Vec<Vertex> {
        let x = WIN_W - 8.0 - (7.0 * 6.0 - 1.0);
        text_run(x, panel_row_y(row) + 3.0, 1.0, REACH_COLOR, "REACH 2")
    }

    fn stun_run(row: u32) -> Vec<Vertex> {
        let reach_x = WIN_W - 8.0 - Painter::text_width(1.0, REACH_MARKER_LABEL);
        let x = reach_x - 4.0 - Painter::text_width(1.0, STUN_MARKER_LABEL);
        text_run(x, panel_row_y(row) + 3.0, 1.0, STUN_MARKER_TEXT, STUN_MARKER_LABEL)
    }

    fn stun_rows(verts: &[Vertex]) -> Vec<u32> {
        (0..6u32)
            .filter(|&r| {
                let (top, bottom) = (panel_row_y(r), panel_row_y(r) + 14.0);
                verts.iter().filter(|v| v.color == STUN_MARKER_TEXT).map(to_px).any(|p| p.1 >= top && p.1 <= bottom)
            })
            .collect()
    }

    /// Rows whose name/morale band holds text in the tag colour.
    fn reach_rows(verts: &[Vertex]) -> Vec<u32> {
        (0..6u32)
            .filter(|&r| {
                let (top, bottom) = (panel_row_y(r), panel_row_y(r) + 14.0);
                verts.iter().filter(|v| v.color == REACH_COLOR).map(to_px).any(|p| p.1 >= top && p.1 <= bottom)
            })
            .collect()
    }

    #[test]
    fn reach_tag_matches_the_marker_constants_and_uses_a_dedicated_colour() {
        assert_eq!(REACH_MARKER_LABEL, "REACH 2");
        assert_eq!(REACH_MARKER_TEXT, REACH_COLOR);
        assert_eq!(Painter::text_width(1.0, REACH_MARKER_LABEL), 41.0);
        assert_eq!(reach_marker_box(), (WIN_W - 8.0 - 41.0, 41.0));
        for other in [
            WHITE, DIM, YELLOW, GREEN, RED, FLEEING, FLED, FLED_ROW, ADAPT_MARKER_TEXT, DODGE_TEXT, INDOMITABLE_MARKER_TEXT, NINE_LIVES_READY_TEXT,
            NINE_LIVES_USED_TEXT, PENDING_MARKER, PENDING_MARKER_TEXT, CONTROL_MARKER, BLUE, STEP_HOVER, STEP_TEXT, SWAP_TEXT,
        ] {
            assert_ne!(other, REACH_COLOR);
        }
        for c in REACH_MARKER_LABEL.chars().filter(|c| *c != ' ') {
            assert!(crate::font::glyph(c).iter().any(|&row| row != 0), "{c} has a glyph");
        }
    }

    #[test]
    fn reach_tag_shows_for_present_two_hex_units_of_either_faction_only() {
        let mut state = bb_rules::scenario::launch_battle_for(two_hex_args());
        let verts = build_scene(&scene(&state, Some(0), false));
        for row in 0..3 {
            assert!(contains_run(&verts, &reach_run(row)), "player row {row}");
        }
        assert_eq!(reach_rows(&verts), vec![0, 1, 2], "enemies stay adjacent and draw no tag");
        assert_eq!(count_color(&verts, REACH_COLOR), 3 * reach_run(0).len());
        state.units[4].melee_reach = MeleeReach::TwoHex;
        let verts = build_scene(&scene(&state, Some(0), false));
        assert_eq!(reach_rows(&verts), vec![0, 1, 2, 4]);
        assert!(contains_run(&verts, &reach_run(4)));
        // Default launch and every Adjacent unit: no tag at all.
        let plain = BattleState::new(23);
        assert_eq!(count_color(&build_scene(&scene(&plain, Some(0), false)), REACH_COLOR), 0);
    }

    #[test]
    fn reach_tag_is_omitted_for_dead_and_escaped_units_and_their_rows_keep_their_text() {
        let mut state = bb_rules::scenario::launch_battle_for(two_hex_args());
        state.units[1].alive = false;
        state.units[2].fled = true;
        let verts = build_scene(&scene(&state, Some(0), false));
        assert_eq!(reach_rows(&verts), vec![0]);
        assert!(!contains_run(&verts, &reach_run(1)) && !contains_run(&verts, &reach_run(2)));
        assert!(text_geometry(&verts, PANEL_X, panel_row_y(1), 2.0, DIM, "P2 DEAD").is_some());
        assert!(text_geometry(&verts, PANEL_X, panel_row_y(2), 2.0, FLED_ROW, "P3 FLED").is_some());
    }

    #[test]
    fn reach_tag_colour_is_independent_of_the_active_highlight_and_selection() {
        let state = bb_rules::scenario::launch_battle_for(two_hex_args());
        let active = state.active_unit().unwrap().id;
        let verts = build_scene(&scene(&state, Some(active), false));
        let row = active.min(2);
        assert!(text_geometry(&verts, PANEL_X, panel_row_y(active), 2.0, YELLOW, state.units[active as usize].name).is_some(), "active row is highlighted");
        assert!(contains_run(&verts, &reach_run(row)), "the active row keeps the dedicated tag colour");
        let idle = build_scene(&scene(&state, None, false));
        assert_eq!(reach_rows(&idle), reach_rows(&verts));
    }

    #[test]
    fn reach_tag_clears_every_morale_label_for_both_factions() {
        for morale in [Morale::Steady, Morale::Confident, Morale::Wavering, Morale::Breaking, Morale::Fleeing] {
            let mut state = bb_rules::scenario::launch_battle_for(two_hex_args());
            for u in &mut state.units {
                u.melee_reach = MeleeReach::TwoHex;
                u.morale = morale;
            }
            let verts = build_scene(&scene(&state, Some(0), false));
            assert_eq!(reach_rows(&verts), vec![0, 1, 2, 3, 4, 5], "{morale:?}");
            for (id, u) in state.units.iter().enumerate() {
                let label = label_text(morale);
                let x = morale_label_x(&state, id as u32);
                let label_points = text_geometry(&verts, x, panel_row_y(id as u32), 2.0, label_colour(morale), &label).expect("morale label still drawn");
                let tag = reach_run(id as u32);
                assert!(contains_run(&verts, &tag));
                let (_, ly0, lx1, ly1) = bounds(label_points.into_iter());
                let (tx0, ty0, tx1, ty1) = bounds(tag.iter().map(to_px));
                assert!(lx1 < tx0, "{morale:?} {}: label ends {lx1}, tag starts {tx0}", u.name);
                assert!(tx1 <= WIN_W - 8.0 + 0.01 && tx0 >= PANEL_X, "tag stays inside the panel and window");
                assert!(ty0 + 0.01 >= panel_row_y(id as u32) && ty1 <= panel_row_y(id as u32) + 14.01, "tag stays on the name/morale line");
                assert!(ly0 + 0.01 >= panel_row_y(id as u32) && ly1 <= panel_row_y(id as u32) + 14.01);
            }
        }
    }

    #[test]
    fn reach_tag_clears_stock_names_and_the_widest_morale_label() {
        let (tag_x, _) = reach_marker_box();
        let state = BattleState::new(23);
        let widest = (0..6).map(|id| morale_label_x(&state, id) + Painter::text_width(2.0, &label_text(Morale::Confident))).fold(0.0, f32::max);
        assert!(widest + 8.0 < tag_x, "gap between the widest label ({widest}) and the tag ({tag_x})");
    }

    #[test]
    fn reach_tag_coexists_with_every_other_indicator_without_overlap() {
        let mut state = bb_rules::scenario::launch_battle_for(bb_rules::scenario::LaunchArgs {
            adrenaline: true,
            rotation: true,
            footwork: true,
            fast_adaptation: true,
            dodge: true,
            nine_lives: true,
            berserk: true,
            pathfinder: true,
            indomitable: true,
            ..two_hex_args()
        });
        for u in state.units.iter_mut().filter(|u| u.side == Side::Player) {
            u.adrenaline_pending = true;
            u.indomitable_active = true;
            u.fast_adaptation_stacks = u32::MAX;
        }
        let verts = build_scene(&scene(&state, Some(0), false));
        assert_eq!(reach_rows(&verts), vec![0, 1, 2]);
        for row in 0..3u32 {
            let y = panel_row_y(row);
            let tag = bounds(reach_run(row).iter().map(to_px));
            let u = &state.units[row as usize];
            let mut others: Vec<Vec<Vertex>> = vec![
                adapt_run(row, u32::MAX),
                text_run(PANEL_X, y + 51.0, 1.0, DODGE_TEXT, &dodge_breakdown_label(u)),
                text_run(INDOMITABLE_MARKER_X, y + 51.0, 1.0, INDOMITABLE_MARKER_TEXT, INDOMITABLE_MARKER_LABEL),
                nine_run(row, false),
                text_run(PANEL_X, y, 2.0, YELLOW, u.name),
                text_run(PANEL_X, y + 18.0, 2.0, YELLOW, &format!("HP {}/{} ARM {}/{}", u.hp, u.stats.hp, u.armour, u.stats.armour)),
            ];
            let mut pending = Painter { verts: Vec::new() };
            pending.pending_marker(y + 36.0);
            others.push(pending.verts);
            let (label, colour) = morale_panel_label(u.morale);
            others.push(text_run(morale_label_x(&state, row), y, 2.0, colour, &label));
            for run in &others {
                let b = bounds(run.iter().map(to_px));
                let disjoint = b.2 < tag.0 || b.0 > tag.2 || b.3 < tag.1 || b.1 > tag.3;
                assert!(disjoint, "row {row}: {b:?} overlaps tag {tag:?}");
            }
            assert!(contains_run(&verts, &adapt_run(row, u32::MAX)));
            assert!(contains_run(&verts, &nine_run(row, false)));
            assert!(contains_run(&verts, &text_run(PANEL_X, y + 51.0, 1.0, DODGE_TEXT, &dodge_breakdown_label(u))));
            assert!(contains_run(&verts, &text_run(INDOMITABLE_MARKER_X, y + 51.0, 1.0, INDOMITABLE_MARKER_TEXT, INDOMITABLE_MARKER_LABEL)));
            assert!(contains_run(&verts, &reach_run(row)));
        }
    }

    #[test]
    fn reach_tag_adds_only_its_own_geometry_and_scene_building_is_pure() {
        let with = bb_rules::scenario::launch_battle_for(two_hex_args());
        let mut without = with.clone();
        for u in &mut without.units {
            u.melee_reach = MeleeReach::Adjacent;
        }
        let (hash, rng, units, queue) = (with.state_hash(), with.rng.clone(), with.units.clone(), with.queue.clone());
        let a = build_scene(&scene(&with, Some(0), true));
        let b = build_scene(&scene(&with, Some(0), true));
        assert!(a.len() == b.len() && a.iter().zip(&b).all(|(x, y)| same(x, y)), "deterministic");
        assert_eq!((with.state_hash(), &with.rng, &with.units, &with.queue), (hash, &rng, &units, &queue));
        // Splice the three tags out: what remains is exactly the scene without any reach.
        let base = build_scene(&scene(&without, Some(0), true));
        let mut rest = a.clone();
        for row in (0..3).rev() {
            let run = reach_run(row);
            let at = rest.windows(run.len()).position(|w| w.iter().zip(&run).all(|(x, y)| same(x, y))).expect("tag present");
            rest.drain(at..at + run.len());
        }
        assert!(rest.len() == base.len() && rest.iter().zip(&base).all(|(x, y)| same(x, y)), "nothing else moved");
    }

    #[test]
    fn stun_tag_uses_scale_one_measured_text_dedicated_color_and_supported_glyphs() {
        assert_eq!(STUN_MARKER_LABEL, "STUN");
        assert_eq!(Painter::text_width(1.0, STUN_MARKER_LABEL), 23.0);
        assert_eq!(STUN_MARKER_TEXT, [1.0, 0.48, 0.66, 1.0]);
        assert_ne!(STUN_MARKER_TEXT, REACH_MARKER_TEXT);
        for other in [WHITE, DIM, YELLOW, GREEN, RED, FLEEING, FLED, ADAPT_MARKER_TEXT, DODGE_TEXT, INDOMITABLE_MARKER_TEXT,
            NINE_LIVES_READY_TEXT, NINE_LIVES_USED_TEXT, PENDING_MARKER, CONTROL_MARKER, BLUE, STEP_TEXT, SWAP_TEXT] {
            assert_ne!(STUN_MARKER_TEXT, other);
        }
        for c in STUN_MARKER_LABEL.chars() {
            assert!(crate::font::glyph(c).iter().any(|&row| row != 0), "{c} has a glyph");
        }
        let (x, w) = stun_marker_box();
        let (reach_x, _) = reach_marker_box();
        assert_eq!(w, Painter::text_width(1.0, STUN_MARKER_LABEL));
        assert!((x + w - (reach_x - 4.0)).abs() < 0.01, "STUN ends 4 px before the reserved REACH 2 slot");
    }

    #[test]
    fn stun_tag_reads_presence_and_stunned_state_for_both_sides_not_capability_flags() {
        let mut state = bb_rules::scenario::launch_battle_for(two_hex_args());
        for unit in &mut state.units {
            unit.has_stunning_blow = true;
            unit.stunned = false;
        }
        state.units[0].stunned = true;
        state.units[3].stunned = true;
        state.units[4].stunned = true;
        state.units[4].alive = false;
        state.units[5].stunned = true;
        state.units[5].fled = true;
        let verts = build_scene(&scene(&state, Some(0), false));
        assert_eq!(stun_rows(&verts), vec![0, 3]);
        assert!(contains_run(&verts, &stun_run(0)) && contains_run(&verts, &stun_run(3)));
        assert!(!contains_run(&verts, &stun_run(1)), "an unstunned capable unit has no tag");
        assert!(!contains_run(&verts, &stun_run(4)), "dead units have no tag even if stored stunned");
        assert!(!contains_run(&verts, &stun_run(5)), "escaped units have no tag even if stored stunned");
        assert_eq!(count_color(&verts, STUN_MARKER_TEXT), 2 * stun_run(0).len());
    }

    #[test]
    fn stun_tag_clears_stock_names_every_morale_state_and_reach_without_reflow() {
        let morale_states = [Morale::Steady, Morale::Confident, Morale::Wavering, Morale::Breaking, Morale::Fleeing];
        for morale in morale_states {
            let mut state = bb_rules::scenario::launch_battle_for(two_hex_args());
            for unit in &mut state.units {
                unit.morale = morale;
                unit.stunned = true;
            }
            // Exercise both reach modes; reach is a separate capability and never gates stun.
            state.units[3].melee_reach = MeleeReach::TwoHex;
            let verts = build_scene(&scene(&state, Some(0), false));
            assert_eq!(stun_rows(&verts), vec![0, 1, 2, 3, 4, 5], "{morale:?}");
            for (id, unit) in state.units.iter().enumerate() {
                let row = id as u32;
                let tag = stun_run(row);
                assert!(contains_run(&verts, &tag), "{morale:?} {}", unit.name);
                let (tx0, ty0, tx1, ty1) = bounds(tag.iter().map(to_px));
                let row_color = if row == 0 { YELLOW } else { WHITE };
                let name_points = text_geometry(&verts, PANEL_X, panel_row_y(row), 2.0, row_color, unit.name).expect("stock name remains visible");
                let (_, _, name_right, _) = bounds(name_points.into_iter());
                assert!(name_right < tx0, "{morale:?} {}: name ends {name_right}, STUN begins {tx0}", unit.name);
                let label = label_text(morale);
                let label_points = text_geometry(&verts, morale_label_x(&state, row), panel_row_y(row), 2.0, label_colour(morale), &label)
                    .expect("morale label remains visible");
                let (_, _, label_right, _) = bounds(label_points.into_iter());
                assert!(label_right < tx0, "{morale:?} {}: morale ends {label_right}, STUN begins {tx0}", unit.name);
                assert!((stun_marker_box().0 + Painter::text_width(1.0, STUN_MARKER_LABEL) - (reach_marker_box().0 - 4.0)).abs() < 0.01);
                assert!(ty0 >= panel_row_y(row) && ty1 <= panel_row_y(row) + 14.0);
                assert!(tx0 >= PANEL_X && tx1 < WIN_W);

                let reach = reach_run(row);
                if row < 3 || row == 3 {
                    assert!(contains_run(&verts, &reach), "REACH 2 stays at its existing location");
                    let (rx0, ry0, _, ry1) = bounds(reach.iter().map(to_px));
                    assert!(tx1 + 3.0 < rx0 && ry0 == ty0 && ry1 == ty1);
                } else {
                    // The default enemy reach is Adjacent, but its stunned tag has identical geometry.
                    assert!(!contains_run(&verts, &reach));
                }
            }
        }
    }

    #[test]
    fn stun_tag_clears_every_existing_passive_and_stance_indicator() {
        let mut state = bb_rules::scenario::launch_battle_for(bb_rules::scenario::LaunchArgs {
            adrenaline: true,
            rotation: true,
            footwork: true,
            fast_adaptation: true,
            dodge: true,
            nine_lives: true,
            berserk: true,
            pathfinder: true,
            indomitable: true,
            ..two_hex_args()
        });
        for u in state.units.iter_mut().filter(|u| u.side == Side::Player) {
            u.adrenaline_pending = true;
            u.indomitable_active = true;
            u.fast_adaptation_stacks = u32::MAX;
            u.stunned = true;
        }
        let verts = build_scene(&scene(&state, Some(0), false));
        for row in 0..3u32 {
            let y = panel_row_y(row);
            let u = &state.units[row as usize];
            let row_color = if row == 0 { YELLOW } else { WHITE };
            let stun = bounds(stun_run(row).iter().map(to_px));
            let mut others = vec![
                adapt_run(row, u32::MAX),
                text_run(PANEL_X, y + 51.0, 1.0, DODGE_TEXT, &dodge_breakdown_label(u)),
                text_run(INDOMITABLE_MARKER_X, y + 51.0, 1.0, INDOMITABLE_MARKER_TEXT, INDOMITABLE_MARKER_LABEL),
                nine_run(row, false),
                text_run(PANEL_X, y, 2.0, row_color, u.name),
                text_run(PANEL_X, y + 18.0, 2.0, row_color, &format!("HP {}/{} ARM {}/{}", u.hp, u.stats.hp, u.armour, u.stats.armour)),
                reach_run(row),
            ];
            let mut pending = Painter { verts: Vec::new() };
            pending.pending_marker(y + 36.0);
            others.push(pending.verts);
            let (label, color) = morale_panel_label(u.morale);
            others.push(text_run(morale_label_x(&state, row), y, 2.0, color, &label));
            for run in &others {
                let b = bounds(run.iter().map(to_px));
                let disjoint = b.2 < stun.0 || b.0 > stun.2 || b.3 < stun.1 || b.1 > stun.3;
                assert!(disjoint, "row {row}: {b:?} overlaps STUN {stun:?}");
                assert!(contains_run(&verts, run), "existing indicator/text keeps its geometry");
            }
            assert!(contains_run(&verts, &stun_run(row)));
        }
    }

    #[test]
    fn inactive_stun_adds_only_its_marker_geometry_and_scene_building_is_pure() {
        let mut with = BattleState::new(23);
        with.units[1].stunned = true; // inactive player unit; no overlays or hover
        let mut without = with.clone();
        without.units[1].stunned = false;
        let (hash, rng, units, queue) = (with.state_hash(), with.rng.clone(), with.units.clone(), with.queue.clone());
        let a = build_scene(&scene(&with, None, false));
        let b = build_scene(&scene(&with, None, false));
        assert!(a.len() == b.len() && a.iter().zip(&b).all(|(x, y)| same(x, y)), "deterministic");
        assert_eq!((with.state_hash(), &with.rng, &with.units, &with.queue), (hash, &rng, &units, &queue));
        assert_eq!(stun_rows(&a), vec![1]);
        let base = build_scene(&scene(&without, None, false));
        let run = stun_run(1);
        let at = a.windows(run.len()).position(|w| w.iter().zip(&run).all(|(x, y)| same(x, y))).expect("marker present");
        let mut rest = a;
        rest.drain(at..at + run.len());
        assert!(rest.len() == base.len() && rest.iter().zip(&base).all(|(x, y)| same(x, y)), "stun adds only its text geometry");
    }


    // --- Stunning Blow targeting display ---------------------------------------------------

    const TERRAINS: [TerrainPreset; 3] = [TerrainPreset::Flat, TerrainPreset::Hills, TerrainPreset::Swamp];

    /// The stock battle on `terrain` with P1 (active, at (-3,3)) granted Stunning Blow and E1
    /// placed on the adjacent hex (-2,3).
    fn blow_board(terrain: TerrainPreset) -> BattleState {
        let mut state = bb_rules::scenario::battle(1, terrain);
        state.units[0].has_stunning_blow = true;
        state.units[3].pos = hex(-2, 3);
        assert_eq!(state.legal_stunning_blow_targets(0), vec![3]);
        state
    }

    fn blow_scene<'a>(state: &'a BattleState, hover: Option<Hex>) -> Scene<'a> {
        Scene { hover, targeting: Some(TargetAction::StunningBlow), ..scene(state, Some(0), false) }
    }

    fn preview_row(verts: &[Vertex], row: usize, text: &str) -> bool {
        preview_row_shown(verts, row, text)
    }

    #[test]
    fn stunning_blow_button_is_drawn_on_its_rectangle_in_the_shared_skill_styling() {
        let capable = blow_board(TerrainPreset::Flat);
        let stock = BattleState::new(1);
        let look = |state: &BattleState, targeting: bool| {
            let sc = Scene { targeting: targeting.then_some(TargetAction::StunningBlow), ..scene(state, Some(0), false) };
            skill_fill(&build_scene(&sc), layout::STUNNING_BLOW_BUTTON)
        };
        assert_eq!(look(&stock, false), Some(SKILL_DISABLED), "no capability");
        assert_eq!(look(&capable, false), Some(SKILL_ENABLED));
        assert_eq!(look(&capable, true), Some(SKILL_ACTIVE));
        assert_eq!(look(&stock, true), Some(SKILL_DISABLED), "targeting needs the capability");
        let mut tired = blow_board(TerrainPreset::Flat);
        tired.units[0].ap = STUNNING_BLOW_AP - 1;
        assert_eq!(look(&tired, false), Some(SKILL_DISABLED), "resource gating");
        assert_eq!(look(&tired, true), Some(SKILL_DISABLED));
        let mut no_target = blow_board(TerrainPreset::Flat);
        no_target.units[3].pos = hex(3, -2);
        assert_eq!(look(&no_target, false), Some(SKILL_DISABLED), "empty legal-target query");
        // Not the active player's turn: disabled even with the capability.
        let mut enemy_turn = blow_board(TerrainPreset::Flat);
        enemy_turn.queue = vec![3, 0];
        assert_eq!(look(&enemy_turn, false), Some(SKILL_DISABLED));
        // Title and rows use the skill-button text layout.
        let r = layout::STUNNING_BLOW_BUTTON;
        let verts = build_scene(&scene(&capable, Some(0), false));
        assert!(contains_run(&verts, &text_run(r.x + SKILL_TEXT_X, r.y + SKILL_TITLE_DY, layout::SKILL_TITLE_SCALE, WHITE, "STUN BLOW (6)")));
        assert!(contains_run(&verts, &text_run(r.x + SKILL_TEXT_X, r.y + SKILL_INFO_DY, 1.0, WHITE, "COST 6 AP 25 FAT")));
        assert!(contains_run(&verts, &text_run(r.x + SKILL_TEXT_X, r.y + SKILL_INFO_DY + 8.0, 1.0, WHITE, "HIT MAY STUN TARGET")));
        assert_eq!(stunning_blow_info_lines()[0], format!("COST {STUNNING_BLOW_AP} AP {STUNNING_BLOW_FATIGUE} FAT"));
        let dim = build_scene(&scene(&stock, Some(0), false));
        assert!(contains_run(&dim, &text_run(r.x + SKILL_TEXT_X, r.y + SKILL_TITLE_DY, layout::SKILL_TITLE_SCALE, BUTTON_DISABLED_TEXT, "STUN BLOW (6)")));
    }

    #[test]
    fn legal_enemy_hexes_are_highlighted_the_hover_is_distinguished_and_ordinary_displays_are_suppressed() {
        for terrain in TERRAINS {
            let state = blow_board(terrain);
            let e1 = state.units[3].pos;
            let marker = |verts: &[Vertex]| contains_run(verts, &text_run(layout::hex_to_pixel(e1).0 + STUN_LABEL_OFFSET.0, layout::hex_to_pixel(e1).1 + STUN_LABEL_OFFSET.1, 1.0, STUN_TARGET_TEXT, "STUN"));
            // Only the legal target is marked; the other enemies and every ally are not.
            let verts = build_scene(&blow_scene(&state, None));
            assert_eq!(hexes_filled(&verts, STUN_TARGET_FILL), vec![e1], "{terrain:?}");
            assert!(hexes_filled(&verts, STUN_TARGET_HOVER).is_empty());
            assert!(marker(&verts), "{terrain:?}: STUN label on the target's token");
            // Hovering it switches to the hover fill; hovering elsewhere does not.
            let hovered = build_scene(&blow_scene(&state, Some(e1)));
            assert_eq!(hexes_filled(&hovered, STUN_TARGET_HOVER), vec![e1]);
            assert!(hexes_filled(&hovered, STUN_TARGET_FILL).is_empty());
            let elsewhere = build_scene(&blow_scene(&state, Some(hex(0, 0))));
            assert_eq!(hexes_filled(&elsewhere, STUN_TARGET_FILL), vec![e1]);
            // A second enemy out of reach and an ally stay unmarked.
            assert!(!hexes_filled(&verts, STUN_TARGET_FILL).contains(&state.units[4].pos));
            assert!(!hexes_filled(&verts, STUN_TARGET_FILL).contains(&state.units[1].pos));

            // Ordinary displays: reach highlights, attack fill, attack preview and hit label.
            let reach_hex = state.reachable(0)[0].0;
            let reach = [(reach_hex, 2)];
            let ordinary = Scene { reach: &reach, hover: Some(e1), targeting: None, ..scene(&state, Some(0), false) };
            let ordinary_verts = build_scene(&ordinary);
            assert_eq!(hexes_filled(&ordinary_verts, REACH_FILL), vec![reach_hex]);
            assert_eq!(hexes_filled(&ordinary_verts, RED), vec![e1], "control: ordinary hover marks the attack target");
            assert!(preview_row(&ordinary_verts, 0, &format!("ATTACK E1: HIT {}%", state.hit_chance(0, 3))));
            let targeting = Scene { reach: &reach, hover: Some(e1), ..blow_scene(&state, Some(e1)) };
            let t_verts = build_scene(&targeting);
            assert!(hexes_filled(&t_verts, REACH_FILL).is_empty(), "no movement highlights");
            assert!(hexes_filled(&t_verts, RED).is_empty(), "no attack fill");
            assert!(!preview_row(&t_verts, 0, &format!("ATTACK E1: HIT {}%", state.hit_chance(0, 3))));
            let c = layout::hex_to_pixel(e1);
            assert!(!contains_run(&t_verts, &text_run(c.0 - 36.0, c.1 - 46.0, 2.0, YELLOW, &format!("HIT {}%", state.hit_chance(0, 3)))));
            // Movement preview over an empty reachable hex is replaced by instructions.
            let step = Scene { hover: Some(reach_hex), ..blow_scene(&state, None) };
            assert!(movement_preview(&Scene { targeting: None, ..blow_scene(&state, Some(reach_hex)) }).is_some(), "control");
            assert!(movement_preview(&step).is_none());
            let step_verts = build_scene(&step);
            assert!(preview_row(&step_verts, 0, "STUN BLOW: P1 PICKS A MARKED ENEMY TO STRIKE. ESC OR 6 CANCELS."));
        }
    }

    #[test]
    fn preview_rows_come_from_the_rules_preview_and_distinguish_resistance_and_existing_stun() {
        let mut variants = Vec::new();
        for (indomitable, stunned) in [(false, false), (true, false), (false, true), (true, true)] {
            let mut state = blow_board(TerrainPreset::Flat);
            state.units[3].has_indomitable = indomitable;
            state.units[3].indomitable_active = indomitable;
            state.units[3].stunned = stunned;
            let plan = state.preview_stunning_blow(0, 3).unwrap();
            let sc = blow_scene(&state, Some(state.units[3].pos));
            let (hash, rng) = (state.state_hash(), state.rng.clone());
            let verts = build_scene(&sc);
            assert_eq!((state.state_hash(), state.rng.clone()), (hash, rng), "pure");
            assert!(preview_row(&verts, 0, &format!("STUN BLOW E1: HIT {}%", plan.hit_chance)));
            assert!(preview_row(&verts, 1, &format!("COST {} AP {} FAT (P1 ONLY). ESC OR 6 CANCELS.", plan.ap_cost, plan.fatigue_cost)));
            assert!(preview_row(&verts, 2, &format!("DAMAGE ON HIT {}-{} BEFORE ARMOUR. MISSES DEAL 0.", plan.damage_min, plan.damage_max)));
            assert_eq!((plan.ap_cost, plan.fatigue_cost), (STUNNING_BLOW_AP, STUNNING_BLOW_FATIGUE));
            let lines = stunning_blow_target_lines(&state, 0, Some(&plan));
            assert_eq!(lines.len(), 4);
            for (i, (text, _)) in lines.iter().enumerate() {
                assert!(preview_row(&verts, i, text), "row {i}: {text}");
                assert!(!text.contains("GUARANTEE") && !text.contains("WILL STUN") && !text.contains("STUNS THE"), "{text}");
            }
            variants.push((indomitable, stunned, lines[3].0.clone(), plan.damage_min, plan.damage_max));
        }
        // Normal, resisted and already-stunned notes are three distinct wordings; resistance wins
        // over an existing stun, as in `apply_stun`.
        let note = |i: bool, s: bool| variants.iter().find(|v| v.0 == i && v.1 == s).unwrap().2.clone();
        assert!(note(false, false).contains("ATTEMPTS TO STUN"));
        assert!(note(true, false).contains("INDOMITABLE") && note(true, false).contains("RESISTED"));
        assert!(note(false, true).contains("ALREADY STUNNED"));
        assert_eq!(note(true, true), note(true, false));
        assert!(note(false, false) != note(true, false) && note(false, false) != note(false, true) && note(true, false) != note(false, true));
        // The Indomitable damage range is the rules' halved one.
        let plain = variants.iter().find(|v| !v.0 && !v.1).unwrap();
        let halved = variants.iter().find(|v| v.0 && !v.1).unwrap();
        assert_eq!((halved.3, halved.4), (plain.3 / 2, plain.4 / 2));
        // Without a hovered target the box gives instructions.
        let state = blow_board(TerrainPreset::Flat);
        let verts = build_scene(&blow_scene(&state, None));
        for (i, (text, _)) in stunning_blow_target_lines(&state, 0, None).iter().enumerate() {
            assert!(preview_row(&verts, i, text), "{text}");
        }
        // An illegal hovered hex (an ally) shows the instructions too.
        let verts = build_scene(&blow_scene(&state, Some(state.units[1].pos)));
        assert!(preview_row(&verts, 0, "STUN BLOW: P1 PICKS A MARKED ENEMY TO STRIKE. ESC OR 6 CANCELS."));
    }

    #[test]
    fn every_stunning_blow_row_fits_the_preview_box_with_supported_glyphs_even_at_u32_max() {
        let (_, _, box_w, _) = PREVIEW_BOX;
        let max_plan = |target: u32| AttackPreview {
            attacker: 0,
            target,
            ap_cost: u32::MAX,
            fatigue_cost: u32::MAX,
            hit_chance: u32::MAX,
            damage_min: u32::MAX,
            damage_max: u32::MAX,
        };
        let mut checked = 0;
        for actor in 0..3u32 {
            for target in 3..6u32 {
                for (indomitable, stunned) in [(false, false), (true, false), (false, true), (true, true)] {
                    let mut state = BattleState::new(1);
                    state.units[target as usize].indomitable_active = indomitable;
                    state.units[target as usize].has_indomitable = indomitable;
                    state.units[target as usize].stunned = stunned;
                    for plan in [Some(max_plan(target)), None] {
                        let lines = stunning_blow_target_lines(&state, actor, plan.as_ref());
                        assert!(lines.len() <= 4, "the box holds four rows");
                        for (text, _) in &lines {
                            let w = Painter::text_width(2.0, text);
                            assert!(w + 16.0 <= box_w, "{text}: {w} wide in a {box_w} wide box");
                            for ch in text.chars().filter(|&c| c != ' ') {
                                assert!(crate::font::glyph(ch).iter().any(|&row| row != 0), "{ch:?} has no glyph in {text:?}");
                            }
                            checked += 1;
                        }
                    }
                }
            }
        }
        assert!(checked > 100);
        // The same through the scene: with maximum damage endpoints every drawn preview glyph
        // stays inside the box.
        let mut state = blow_board(TerrainPreset::Flat);
        state.units[0].stats.dmg_min = u32::MAX;
        state.units[0].stats.dmg_max = u32::MAX;
        let plan = state.preview_stunning_blow(0, 3).unwrap();
        assert_eq!((plan.damage_min, plan.damage_max), (u32::MAX, u32::MAX));
        let verts = build_scene(&blow_scene(&state, Some(state.units[3].pos)));
        assert!(preview_row(&verts, 2, &format!("DAMAGE ON HIT {}-{} BEFORE ARMOUR. MISSES DEAL 0.", u32::MAX, u32::MAX)));
        let (bx, by, bw, bh) = PREVIEW_BOX;
        let inside: Vec<(f32, f32)> = verts
            .iter()
            .filter(|v| preview_colours().contains(&v.color) || v.color == PREVIEW_WARN)
            .map(to_px)
            .filter(|&(x, y)| y >= by - 1.0 && x < PANEL_X - 12.0 && y < by + bh + 1.0)
            .collect();
        assert!(!inside.is_empty());
        let eps = 0.01;
        for &(x, y) in &inside {
            assert!(x >= bx - eps && x <= bx + bw + eps && y >= by - eps && y <= by + bh + eps, "({x},{y}) outside the preview box");
        }
        // And nothing drawn by the preview reaches the side panel or the error bar.
        assert!(inside.iter().all(|p| p.0 < PANEL_X - 12.0));
    }

    #[test]
    fn legends_badges_overlays_and_tokens_stay_unobscured_on_every_terrain() {
        for terrain in TERRAINS {
            let state = blow_board(terrain);
            for height in [false, true] {
                let with = |targeting: bool, hover: Option<Hex>| {
                    let sc = Scene {
                        hover,
                        height_overlay: height,
                        control_overlay: true,
                        targeting: targeting.then_some(TargetAction::StunningBlow),
                        ..scene(&state, Some(0), true)
                    };
                    build_scene(&sc)
                };
                let e1 = state.units[3].pos;
                let plain = with(false, None);
                for verts in [with(true, None), with(true, Some(e1))] {
                    // Legends.
                    let (lx, ly, step) = HEIGHT_LEGEND;
                    for (i, (line, col)) in height_legend_lines(height).iter().enumerate() {
                        assert!(contains_run(&verts, &text_run(lx, ly + i as f32 * step, 2.0, *col, line)), "{terrain:?} height legend {i}");
                        let row = Rect::new(lx, ly + i as f32 * step, Painter::text_width(2.0, line), 14.0);
                        assert!(!row.overlaps(&layout::STUNNING_BLOW_BUTTON), "{line} meets the button");
                    }
                    if has_visible_swamp(&state) {
                        let (sx, sy, sstep) = SWAMP_LEGEND;
                        for (i, (line, col)) in swamp_legend_lines().iter().enumerate() {
                            assert!(contains_run(&verts, &text_run(sx, sy + i as f32 * sstep, 2.0, *col, line)), "{terrain:?} swamp legend {i}");
                            let row = Rect::new(sx, sy + i as f32 * sstep, Painter::text_width(2.0, line), 14.0);
                            assert!(!row.overlaps(&layout::STUNNING_BLOW_BUTTON));
                        }
                    }
                    // Badges and ZoC markers on every hex.
                    for h in map_hexes() {
                        let c = layout::hex_to_pixel(h);
                        let mut p = Painter { verts: Vec::new() };
                        if height {
                            p.height_badge(c, state.elevations.get(h).unwrap_or(0));
                            assert!(contains_run(&verts, &p.verts), "{terrain:?} height badge {h:?}");
                            p.verts.clear();
                        }
                        if is_passable_swamp(&state, h) {
                            p.swamp_badge(c);
                            assert!(contains_run(&verts, &p.verts), "{terrain:?} swamp badge {h:?}");
                            p.verts.clear();
                        }
                        if control_overlay_hexes(&scene(&state, Some(0), true)).contains(&h) {
                            p.control_marker(c);
                            assert!(contains_run(&verts, &p.verts), "{terrain:?} ZoC marker {h:?}");
                        }
                    }
                    // Token information: names and bars of every present unit.
                    for u in state.units.iter().filter(|u| u.is_present()) {
                        let c = layout::hex_to_pixel(u.pos);
                        assert!(contains_run(&verts, &text_run(c.0 - 12.0, c.1 - 7.0, 2.0, WHITE, u.name)), "{} name", u.name);
                    }
                    // Everything the plain frame drew in the unit panel is still there.
                    let panel: Vec<&Vertex> = plain.iter().filter(|v| to_px(v).0 >= PANEL_X - 12.0 && to_px(v).1 >= 44.0 && to_px(v).1 < 400.0).collect();
                    let now: Vec<&Vertex> = verts.iter().filter(|v| to_px(v).0 >= PANEL_X - 12.0 && to_px(v).1 >= 44.0 && to_px(v).1 < 400.0).collect();
                    assert_eq!(panel.len(), now.len());
                    assert!(panel.iter().zip(&now).all(|(a, b)| same(a, b)), "{terrain:?}: panel rows unchanged");
                }
            }
        }
    }

    #[test]
    fn building_the_stunning_blow_scene_is_pure_and_deterministic() {
        for terrain in TERRAINS {
            let state = blow_board(terrain);
            let (hash, rng, units, queue) = (state.state_hash(), state.rng.clone(), state.units.clone(), state.queue.clone());
            for hover in [None, Some(state.units[3].pos), Some(state.units[1].pos), Some(hex(0, 0))] {
                let a = build_scene(&blow_scene(&state, hover));
                let b = build_scene(&blow_scene(&state, hover));
                assert!(a.len() == b.len() && a.iter().zip(&b).all(|(x, y)| same(x, y)));
            }
            assert_eq!((state.state_hash(), state.rng.clone(), state.units.clone(), state.queue.clone()), (hash, rng, units, queue));
        }
    }

    // --- Killing Frenzy native marker --------------------------------------------------

    const EXPECTED_FRENZY_TEXT: &str = "FRENZY";
    const EXPECTED_FRENZY_COLOR: Rgba = [1.0, 0.56, 0.24, 1.0];

    fn frenzy_run(row: u32) -> Vec<Vertex> {
        text_run(
            1134.0,
            panel_row_y(row) + 21.0,
            1.0,
            EXPECTED_FRENZY_COLOR,
            EXPECTED_FRENZY_TEXT,
        )
    }

    fn frenzy_rows(verts: &[Vertex]) -> Vec<u32> {
        (0..6)
            .filter(|&row| contains_run(verts, &frenzy_run(row)))
            .collect()
    }

    #[test]
    fn frenzy_marker_uses_the_requested_geometry_and_fits_between_stock_text_and_adapt() {
        assert_eq!(Painter::text_width(1.0, EXPECTED_FRENZY_TEXT), 35.0);
        assert_eq!(
            1134.0 + Painter::text_width(1.0, EXPECTED_FRENZY_TEXT),
            1169.0
        );
        assert_eq!(1177.0 - 1169.0, 8.0);
        for other in [
            WHITE,
            DIM,
            YELLOW,
            GREEN,
            RED,
            FLEEING,
            FLED,
            ADAPT_MARKER_TEXT,
            DODGE_TEXT,
            INDOMITABLE_MARKER_TEXT,
            NINE_LIVES_READY_TEXT,
            NINE_LIVES_USED_TEXT,
            PENDING_MARKER_TEXT,
            STUN_MARKER_TEXT,
            REACH_MARKER_TEXT,
        ] {
            assert_ne!(EXPECTED_FRENZY_COLOR, other, "FRENZY uses its own colour");
        }
        let fallback = crate::font::glyph('\u{1}');
        assert!(
            EXPECTED_FRENZY_TEXT
                .chars()
                .all(|c| font::glyph(c) != fallback)
        );

        let mut state = BattleState::new(23);
        for unit in &mut state.units {
            unit.has_killing_frenzy = true;
            unit.killing_frenzy_turns = 1;
            unit.hp = unit.stats.hp;
            unit.armour = unit.stats.armour;
            unit.has_fast_adaptation = true;
            unit.fast_adaptation_stacks = u32::MAX;
        }
        let verts = build_scene(&scene(&state, Some(0), false));
        for row in 0..6 {
            let y = panel_row_y(row);
            let run = frenzy_run(row);
            assert!(
                contains_run(&verts, &run),
                "row {row}: rendered FRENZY run at the requested origin"
            );
            let marker = bounds(run.iter().map(to_px));
            assert_eq!(marker.0, 1134.0);
            assert_eq!(marker.2, 1169.0, "the actual text geometry ends after its 35 px width");
            assert!(marker.1 >= y + 21.0 - 0.01 && marker.3 <= y + 28.0 + 0.01);

            let unit = &state.units[row as usize];
            let col = if row == 0 { YELLOW } else { WHITE };
            let hp = format!(
                "HP {}/{} ARM {}/{}",
                unit.hp, unit.stats.hp, unit.armour, unit.stats.armour
            );
            let hp_points = text_geometry(&verts, PANEL_X, y + 18.0, 2.0, col, &hp)
                .expect("stock HP/armour text remains");
            let hp_bounds = bounds(hp_points.into_iter());
            assert!(
                hp_bounds.2 + 8.0 <= marker.0,
                "row {row}: stock resources {hp_bounds:?} clear FRENZY {marker:?}"
            );

            let adapt = adapt_run(row, u32::MAX);
            assert!(
                contains_run(&verts, &adapt),
                "row {row}: maximum ADAPT remains rendered"
            );
            let adapt_bounds = bounds(adapt.iter().map(to_px));
            assert_eq!(adapt_bounds.0, 1177.0);
            assert!(
                marker.2 + 8.0 <= adapt_bounds.0,
                "row {row}: FRENZY {marker:?} clears ADAPT {adapt_bounds:?}"
            );
        }
    }

    #[test]
    fn frenzy_marker_uses_the_effective_predicate_for_all_rows_and_both_sides() {
        let mut state = BattleState::new(23);
        for unit in &mut state.units {
            unit.has_killing_frenzy = true;
        }

        for counter in [0, 1, 2, u8::MAX] {
            for unit in &mut state.units {
                unit.killing_frenzy_turns = counter;
            }
            for queue in [vec![0, 1, 2, 3, 4, 5], vec![3, 4, 5, 0, 1, 2]] {
                state.queue.clone_from(&queue);
                let active = state.active_unit().expect("scheduled unit").id;
                for selected in [None, Some(0), Some(5), Some(active)] {
                    let verts = build_scene(&scene(&state, selected, false));
                    let expected: Vec<u32> = if counter == 0 {
                        Vec::new()
                    } else {
                        (0..6).collect()
                    };
                    assert_eq!(
                        frenzy_rows(&verts),
                        expected,
                        "counter {counter}, active {active}, selected {selected:?}"
                    );
                    if counter == 0 {
                        let mut incapable = state.clone();
                        for unit in &mut incapable.units {
                            unit.has_killing_frenzy = false;
                        }
                        let plain = build_scene(&scene(&incapable, selected, false));
                        assert!(
                            verts.len() == plain.len()
                                && verts.iter().zip(&plain).all(|(a, b)| same(a, b)),
                            "a capability with zero duration leaves the scene unchanged");
                    }
                }
            }
        }

        // A stale nonzero counter has no marker when capability or battlefield presence is absent.
        for unit in &mut state.units {
            unit.has_killing_frenzy = true;
            unit.killing_frenzy_turns = u8::MAX;
        }
        state.units[1].has_killing_frenzy = false;
        state.units[2].alive = false;
        state.units[4].fled = true;
        let verts = build_scene(&scene(&state, None, false));
        assert_eq!(frenzy_rows(&verts), vec![0, 3, 5]);
        assert!(text_geometry(&verts, PANEL_X, panel_row_y(2), 2.0, DIM, "P3 DEAD").is_some());
        assert!(text_geometry(&verts, PANEL_X, panel_row_y(4), 2.0, FLED, "E2 FLED").is_some());
    }

    #[test]
    fn frenzy_marker_coexists_with_every_existing_unit_row_indicator() {
        let mut with = BattleState::new(23);
        for unit in &mut with.units {
            unit.has_killing_frenzy = true;
            unit.killing_frenzy_turns = 2;
            unit.has_fast_adaptation = true;
            unit.fast_adaptation_stacks = u32::MAX;
            unit.has_dodge = true;
            unit.has_nine_lives = true;
            unit.has_indomitable = true;
            unit.indomitable_active = true;
            unit.has_adrenaline = true;
            unit.adrenaline_pending = true;
            unit.melee_reach = MeleeReach::TwoHex;
            unit.stunned = true;
            unit.hp = unit.stats.hp;
            unit.armour = unit.stats.armour;
        }
        let mut without = with.clone();
        for unit in &mut without.units {
            unit.killing_frenzy_turns = 0;
        }

        let verts = build_scene(&scene(&with, Some(0), false));
        let baseline = build_scene(&scene(&without, Some(0), false));
        for row in 0..6 {
            let unit = &with.units[row as usize];
            let y = panel_row_y(row);
            let row_color = if row == 0 { YELLOW } else { WHITE };
            let hp = format!(
                "HP {}/{} ARM {}/{}",
                unit.hp, unit.stats.hp, unit.armour, unit.stats.armour
            );
            assert!(text_geometry(&verts, PANEL_X, y + 18.0, 2.0, row_color, &hp).is_some());
            let (morale, morale_color) = morale_panel_label(unit.morale);
            let morale_x = PANEL_X + Painter::text_width(2.0, unit.name) + 10.0;
            assert!(text_geometry(&verts, morale_x, y, 2.0, morale_color, &morale).is_some());
            assert!(contains_run(&verts, &stun_run(row)));
            assert!(contains_run(&verts, &reach_run(row)));
            assert!(contains_run(&verts, &adapt_run(row, u32::MAX)));
            assert!(contains_run(&verts, &pending_marker_run(row)));
            assert!(contains_run(
                &verts,
                &dodge_run(row, &dodge_breakdown_label(unit))
            ));
            assert!(contains_run(&verts, &nine_run(row, false)));
            assert!(contains_run(&verts, &marker_run(row)));
            assert!(contains_run(&verts, &frenzy_run(row)));
        }

        let mut without_frenzy = verts;
        for row in (0..6).rev() {
            let run = frenzy_run(row);
            let start = without_frenzy
                .windows(run.len())
                .position(|window| window.iter().zip(&run).all(|(got, want)| same(got, want)))
                .expect("FRENZY marker run exists");
            without_frenzy.drain(start..start + run.len());
        }
        assert!(
            without_frenzy.len() == baseline.len()
                && without_frenzy
                    .iter()
                    .zip(&baseline)
                    .all(|(a, b)| same(a, b)),
            "FRENZY adds only its own text vertices; every existing indicator retains exact geometry"
        );
    }

    #[test]
    fn frenzy_marker_is_present_in_ordinary_targeting_and_completed_scenes_without_mutation() {
        for terrain in TERRAINS {
            let mut state = blow_board(terrain);
            for id in [0usize, 4] {
                state.units[id].has_killing_frenzy = true;
                state.units[id].killing_frenzy_turns = 1;
            }
            let expected = vec![0, 4];
            let ordinary = scene(&state, None, false);
            let targeted = blow_scene(&state, Some(state.units[3].pos));
            let completed_state = {
                let mut completed = state.clone();
                completed.outcome = Some(Outcome::Victory);
                completed
            };
            let completed = scene(&completed_state, None, false);

            for sc in [&ordinary, &targeted, &completed] {
                let before = (
                    sc.state.state_hash(),
                    sc.state.rng.clone(),
                    sc.state.units.clone(),
                    sc.state.queue.clone(),
                );
                let verts = build_scene(sc);
                assert_eq!(
                    frenzy_rows(&verts),
                    expected,
                    "{terrain:?}, outcome {:?}, targeting {:?}",
                    sc.state.outcome,
                    sc.targeting
                );
                assert_eq!(
                    (
                        sc.state.state_hash(),
                        sc.state.rng.clone(),
                        sc.state.units.clone(),
                        sc.state.queue.clone()
                    ),
                    before
                );
            }
        }
    }
}

const SHADER: &str = "
struct VOut { @builtin(position) p: vec4<f32>, @location(0) c: vec4<f32> };
@vertex fn vs(@location(0) pos: vec2<f32>, @location(1) col: vec4<f32>) -> VOut {
    var o: VOut; o.p = vec4<f32>(pos, 0.0, 1.0); o.c = col; return o;

}
@fragment fn fs(i: VOut) -> @location(0) vec4<f32> { return i.c; }
";

pub struct Gpu {
    surface: wgpu::Surface<'static>,
    device: wgpu::Device,
    queue: wgpu::Queue,
    config: wgpu::SurfaceConfiguration,
    pipeline: wgpu::RenderPipeline,
    vbuf: wgpu::Buffer,
}

impl Gpu {
    pub fn new(window: Arc<Window>) -> Result<Gpu, String> {
        let instance = wgpu::Instance::default();
        let size = window.inner_size();
        let surface = instance.create_surface(window).map_err(|e| format!("create_surface: {e}"))?;
        let adapter = pollster::block_on(instance.request_adapter(&wgpu::RequestAdapterOptions {
            compatible_surface: Some(&surface),
            ..Default::default()
        }))
        .map_err(|e| format!("no usable GPU adapter: {e}"))?;
        let (device, queue) = pollster::block_on(adapter.request_device(&wgpu::DeviceDescriptor::default()))
            .map_err(|e| format!("request_device: {e}"))?;
        let mut config = surface
            .get_default_config(&adapter, size.width.max(1), size.height.max(1))
            .ok_or("surface not supported by adapter")?;
        config.present_mode = wgpu::PresentMode::Fifo;
        surface.configure(&device, &config);

        let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: None,
            source: wgpu::ShaderSource::Wgsl(SHADER.into()),
        });
        let layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: None,
            bind_group_layouts: &[],
            immediate_size: 0,
        });
        let attrs = wgpu::vertex_attr_array![0 => Float32x2, 1 => Float32x4];
        let pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: None,
            layout: Some(&layout),
            vertex: wgpu::VertexState {
                module: &shader,
                entry_point: Some("vs"),
                compilation_options: Default::default(),
                buffers: &[Some(wgpu::VertexBufferLayout {
                    array_stride: std::mem::size_of::<Vertex>() as u64,
                    step_mode: wgpu::VertexStepMode::Vertex,
                    attributes: &attrs,
                })],
            },
            fragment: Some(wgpu::FragmentState {
                module: &shader,
                entry_point: Some("fs"),
                compilation_options: Default::default(),
                targets: &[Some(config.format.into())],
            }),
            primitive: wgpu::PrimitiveState::default(),
            depth_stencil: None,
            multisample: wgpu::MultisampleState::default(),
            multiview_mask: None,
            cache: None,
        });
        let vbuf = device.create_buffer(&wgpu::BufferDescriptor {
            label: None,
            size: (MAX_VERTS * std::mem::size_of::<Vertex>()) as u64,
            usage: wgpu::BufferUsages::VERTEX | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });
        Ok(Gpu { surface, device, queue, config, pipeline, vbuf })
    }

    pub fn resize(&mut self, w: u32, h: u32) {
        if w > 0 && h > 0 {
            self.config.width = w;
            self.config.height = h;
            self.surface.configure(&self.device, &self.config);
        }
    }

    pub fn draw(&mut self, verts: &[Vertex]) {
        let tex = match self.surface.get_current_texture() {
            wgpu::CurrentSurfaceTexture::Success(t) | wgpu::CurrentSurfaceTexture::Suboptimal(t) => t,
            wgpu::CurrentSurfaceTexture::Outdated | wgpu::CurrentSurfaceTexture::Lost => {
                self.surface.configure(&self.device, &self.config);
                return;
            }
            _ => return,
        };
        let verts = &verts[..verts.len().min(MAX_VERTS)];
        self.queue.write_buffer(&self.vbuf, 0, bytemuck::cast_slice(verts));
        let view = tex.texture.create_view(&Default::default());
        let mut enc = self.device.create_command_encoder(&Default::default());
        {
            let mut pass = enc.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: None,
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view: &view,
                    depth_slice: None,
                    resolve_target: None,
                    ops: wgpu::Operations { load: wgpu::LoadOp::Clear(wgpu::Color::BLACK), store: wgpu::StoreOp::Store },
                })],
                depth_stencil_attachment: None,
                timestamp_writes: None,
                occlusion_query_set: None,
                multiview_mask: None,
            });
            pass.set_pipeline(&self.pipeline);
            pass.set_vertex_buffer(0, self.vbuf.slice(..));
            pass.draw(0..verts.len() as u32, 0..1);
        }
        self.queue.submit(Some(enc.finish()));
        self.queue.present(tex);
    }
}
