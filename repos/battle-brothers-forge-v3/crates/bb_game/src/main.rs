//! Native window client. All rules go through `bb_rules::apply`; this file only maps input.

mod font;
mod layout;
mod render;

use std::sync::Arc;
use std::time::{Duration, Instant};

use bb_rules::rules::{adrenaline_availability, indomitable_availability};
use bb_rules::scenario::{self, LaunchArgs, TerrainPreset};
use bb_rules::{BattleState, Command, Event, Hex, Morale, Side, ai};
use render::{ConfirmAction, TargetAction};
use winit::application::ApplicationHandler;
use winit::dpi::LogicalSize;
use winit::event::{ElementState, MouseButton, WindowEvent};
use winit::event_loop::{ActiveEventLoop, ControlFlow, EventLoop};
use winit::keyboard::{Key, NamedKey};
use winit::window::{Window, WindowId};

const AI_STEP: Duration = Duration::from_millis(250);
const ERROR_TIME: Duration = Duration::from_secs(2);
/// Clicks right after a confirmation dialog (End Round or Retreat) closes are
/// swallowed, so a double click on Confirm or Cancel cannot fall through to the
/// map underneath.
const MODAL_CLICK_GUARD: Duration = Duration::from_millis(350);

/// Confirmation intent captured when an End Round or Retreat dialog opens. It is
/// only a request: the command for `action` is submitted on confirmation if all
/// fields still match. At most one exists at a time.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct PendingConfirm {
    action: ConfirmAction,
    actor: u32,
    round: u32,
    state_hash: u64,
}

/// Targeting intent captured on activation of Rotation, Footwork or Stunning Blow. It is only a request:
/// the `action`'s command is submitted on a legal target click if all fields still match.
/// At most one exists at a time; it is never a reservation and holds no battle data.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct PendingTargeting {
    action: TargetAction,
    actor: u32,
    round: u32,
    state_hash: u64,
}

struct App {
    window: Option<Arc<Window>>,
    gpu: Option<render::Gpu>,
    state: BattleState,
    seed: u64,
    /// Selected terrain; restart and new seed rebuild the battle on it.
    terrain: TerrainPreset,
    /// `--adrenaline` loadout; restart and new seed rebuild the battle with it.
    adrenaline: bool,
    /// `--rotation` loadout; restart and new seed rebuild the battle with it.
    rotation: bool,
    /// `--footwork` loadout; restart and new seed rebuild the battle with it.
    footwork: bool,
    /// `--fast-adaptation` loadout; restart and new seed rebuild the battle with it
    /// (stacks restart at zero).
    fast_adaptation: bool,
    /// `--dodge` loadout; restart and new seed rebuild the battle with it.
    dodge: bool,
    /// `--nine-lives` loadout; restart and new seed rebuild the battle with it (unused again).
    nine_lives: bool,
    /// `--berserk` loadout; restart and new seed rebuild the battle with the passive unused again.
    berserk: bool,
    /// `--pathfinder` loadout; restart and new seed rebuild the battle with its movement discount.
    pathfinder: bool,
    /// `--indomitable` loadout; restart and new seed rebuild the battle with the capability and the
    /// stance inactive.
    indomitable: bool,
    /// `--two-hex` loadout; restart and new seed rebuild the battle with player units on
    /// `MeleeReach::TwoHex` (APPROXIMATION; enemies stay adjacent-only).
    two_hex: bool,
    /// `--stunning-blow` launch loadout; restart and new seed restore the capability on fresh units.
    stunning_blow: bool,
    /// `--killing-frenzy` launch loadout; restart and new seed restore the capability with a zero counter.
    killing_frenzy: bool,
    control_overlay: bool,
    /// Height-number overlay preference (H); independent of Z and of battle state.
    height_overlay: bool,
    pending_confirm: Option<PendingConfirm>,
    /// Open Rotation, Footwork or Stunning Blow targeting mode, if any. Exclusive with `pending_confirm`.
    targeting: Option<PendingTargeting>,
    /// Map clicks before this instant are ignored (set when a dialog closes or targeting
    /// completes or is cancelled).
    click_guard_until: Option<Instant>,
    mouse: (f32, f32),
    log: Vec<String>,
    error: Option<(String, Instant)>,
    last_ai: Instant,
}

impl App {
    #[cfg(test)]
    fn new(seed: u64) -> App {
        App::with_terrain(seed, TerrainPreset::Flat)
    }

    #[cfg(test)]
    fn with_terrain(seed: u64, terrain: TerrainPreset) -> App {
        App::with_loadout(seed, terrain, false)
    }

    #[cfg(test)]
    fn with_loadout(seed: u64, terrain: TerrainPreset, adrenaline: bool) -> App {
        App::with_flags(seed, terrain, adrenaline, false)
    }

    #[cfg(test)]
    fn with_flags(seed: u64, terrain: TerrainPreset, adrenaline: bool, rotation: bool) -> App {
        App::from_args(LaunchArgs { seed, terrain, adrenaline, rotation, footwork: false, fast_adaptation: false, dodge: false, nine_lives: false, berserk: false, pathfinder: false, indomitable: false, two_hex: false, stunning_blow: false, killing_frenzy: false })
    }

    /// The height overlay starts on for Hills and off for Flat and Swamp.
    fn from_args(args: LaunchArgs) -> App {
        App {
            window: None,
            gpu: None,
            state: scenario::launch_battle_for(args),
            seed: args.seed,
            terrain: args.terrain,
            adrenaline: args.adrenaline,
            rotation: args.rotation,
            footwork: args.footwork,
            fast_adaptation: args.fast_adaptation,
            dodge: args.dodge,
            nine_lives: args.nine_lives,
            berserk: args.berserk,
            pathfinder: args.pathfinder,
            indomitable: args.indomitable,
            two_hex: args.two_hex,
            stunning_blow: args.stunning_blow,
            killing_frenzy: args.killing_frenzy,
            control_overlay: false,
            height_overlay: args.terrain == TerrainPreset::Hills,
            pending_confirm: None,
            targeting: None,
            click_guard_until: None,
            mouse: (0.0, 0.0),
            log: Vec::new(),
            error: None,
            last_ai: Instant::now(),
        }
    }

    /// The launch configuration this session was started with, for `seed`.
    fn launch_args(&self, seed: u64) -> LaunchArgs {
        LaunchArgs {
            seed,
            terrain: self.terrain,
            adrenaline: self.adrenaline,
            rotation: self.rotation,
            footwork: self.footwork,
            fast_adaptation: self.fast_adaptation,
            dodge: self.dodge,
            nine_lives: self.nine_lives,
            berserk: self.berserk,
            pathfinder: self.pathfinder,
            indomitable: self.indomitable,
            two_hex: self.two_hex,
            stunning_blow: self.stunning_blow,
            killing_frenzy: self.killing_frenzy,
        }
    }

    fn restart(&mut self, seed: u64) {
        self.seed = seed;
        self.state = scenario::launch_battle_for(self.launch_args(seed));
        self.log.clear();
        self.error = None;
        self.pending_confirm = None;
        self.targeting = None;
        self.click_guard_until = None;
    }

    /// R/N: restart, and if targeting was open also arm the click guard (restart
    /// itself resets it) so a follow-up click cannot Move in the rebuilt battle.
    fn restart_cancelling_targeting(&mut self, seed: u64, now: Instant) {
        let was_targeting = self.targeting.is_some();
        self.restart(seed);
        if was_targeting {
            self.click_guard_until = Some(now + MODAL_CLICK_GUARD);
        }
    }

    fn name(&self, id: u32) -> &'static str {
        self.state.units[id as usize].name
    }

    /// Every submission, whoever issued it, ends any targeting: the intent was
    /// captured against a state this command is about to change.
    fn run(&mut self, cmd: Command) {
        self.targeting = None;
        match self.state.apply(cmd) {
            Ok(events) => {
                for e in events {
                    let line = match e {
                        Event::Moved { unit, path } => Some(format!("{} moves {}", self.name(unit), path.len())),
                        Event::Attacked { attacker, target, hit, roll, chance, dmg, absorbed, hp_loss } => Some(if hit {
                            format!("{} hits {} roll {}<{} dmg {} arm -{} hp -{}", self.name(attacker), self.name(target), roll, chance, dmg, absorbed, hp_loss)
                        } else {
                            format!("{} misses {} roll {}>={}", self.name(attacker), self.name(target), roll, chance)
                        }),
                        Event::MoraleChecked { unit, roll, chance, before, after } => Some(if roll < chance {
                            format!("{} holds {:?} r{}<{}", self.name(unit), before, roll, chance)
                        } else {
                            format!("{} {:?}>{:?} r{}>={}", self.name(unit), before, after, roll, chance)
                        }),
                        // Recovery or continued flight first: log lines are truncated to 31 chars.
                        Event::SelfRallyChecked { unit, roll, chance, after, .. } => Some(if after == Morale::Fleeing {
                            format!("{} keeps fleeing r{}>={}", self.name(unit), roll, chance)
                        } else {
                            format!("{} rallies r{}<{}", self.name(unit), roll, chance)
                        }),
                        Event::AllyDeathMoraleChecked { unit, fallen, roll, chance, before, after } => {
                            // Result and fallen ally first; roll details only if they fit.
                            let (u, f) = (self.name(unit), self.name(fallen));
                            let mut line = if roll < chance {
                                format!("{u} holds {before:?}, {f} died")
                            } else if before == after {
                                format!("{u} still {after:?}, {f} died")
                            } else {
                                format!("{u} {before:?}>{after:?}, {f} died")
                            };
                            let detail = format!(" r{}{}{}", roll, if roll < chance { "<" } else { ">=" }, chance);
                            if line.chars().count() + detail.chars().count() <= 31 {
                                line.push_str(&detail);
                            }
                            Some(line)
                        }
                        // Improvement shows the step; unchanged morale (failed check or
                        // already Confident) reads "stays". Both fit 31 chars.
                        Event::KillMoraleChecked { unit, roll, chance, before, after, .. } => Some(if before != after {
                            format!("{} {:?}>{:?} r{}<{}", self.name(unit), before, after, roll, chance)
                        } else {
                            format!("{} stays {:?} r{}{}{}", self.name(unit), after, roll, if roll < chance { "<" } else { ">=" }, chance)
                        }),
                        // "P1 edge Steady>Breaking" is 24 chars at most.
                        Event::EdgeMoraleChanged { unit, before, after, .. } => {
                            Some(format!("{} edge {:?}>{:?}", self.name(unit), before, after))
                        }
                        Event::Died { unit } => Some(format!("{} dies", self.name(unit))),
                        Event::NineLivesTriggered { unit } => Some(format!("{} nine lives: survives", self.name(unit))),
                        Event::BerserkTriggered { unit, ap_gained } => {
                            Some(format!("{} berserk: +{} AP", self.name(unit), ap_gained))
                        }
                        Event::Fled { unit } => Some(format!("{} escapes", self.name(unit))),
                        Event::RetreatOrdered { .. } => Some("retreat ordered".to_string()),
                        Event::AdrenalineUsed { unit } => Some(format!("{} adrenaline: next round", self.name(unit))),
                        Event::Rotated { unit, target } => {
                            Some(format!("{} swaps with {}", self.name(unit), self.name(target)))
                        }
                        Event::FootworkUsed { unit, .. } => Some(format!("{} footwork", self.name(unit))),
                        Event::IndomitableUsed { unit } => Some(format!("{} indomitable", self.name(unit))),
                        Event::RoundStarted { round } => Some(format!("round {round}")),
                        Event::BattleEnded { outcome, reason } => Some(format!("{outcome:?} {reason:?}")),
                        Event::StunApplied { unit } => Some(format!("{} stunned", self.name(unit))),
                        Event::StunExpired { unit } => Some(format!("{} skips turn: stun ends", self.name(unit))),
                        Event::StunResisted { unit } => Some(format!("{} resists stun", self.name(unit))),
                        Event::TurnStarted { .. } => None,
                    };
                    self.log.extend(line);
                }
            }
            Err(e) => self.error = Some((format!("{e:?}"), Instant::now())),
        }
    }

    /// The active player unit, if it is the human's turn. A Fleeing or
    /// retreating player unit is driven automatically (`ai_step`) and never
    /// offers human actions.
    fn human_unit(&self) -> Option<u32> {
        self.state
            .active_unit()
            .filter(|u| u.side == Side::Player && u.morale != Morale::Fleeing && !u.retreating)
            .map(|u| u.id)
    }

    /// The present, controllable active player unit of a running battle: who End Round and
    /// Retreat act for.
    fn order_actor(&self) -> Option<u32> {
        self.human_unit().filter(|&id| self.state.units[id as usize].is_present())
    }

    /// The unit the Adrenaline button and shortcut act for: the present, controllable active
    /// player unit whose rules availability query succeeds. Pure.
    fn adrenaline_actor(&self) -> Option<u32> {
        self.order_actor().filter(|&id| adrenaline_availability(&self.state.units[id as usize]).is_ok())
    }

    /// Submits exactly one `Command::Adrenaline` when it is available; otherwise a silent no-op.
    fn use_adrenaline(&mut self) {
        if let Some(me) = self.adrenaline_actor() {
            self.run(Command::Adrenaline { unit: me });
        }
    }

    /// The unit the Indomitable button and shortcut act for: the present, controllable active
    /// player unit whose rules availability query succeeds. Pure.
    fn indomitable_actor(&self) -> Option<u32> {
        self.order_actor().filter(|&id| indomitable_availability(&self.state.units[id as usize]).is_ok())
    }

    /// Submits exactly one `Command::Indomitable` when it is available; otherwise a silent no-op.
    fn use_indomitable(&mut self) {
        if let Some(me) = self.indomitable_actor() {
            self.run(Command::Indomitable { unit: me });
        }
    }

    /// The unit the Recover button and shortcut act for: the present, controllable active
    /// player unit whose rules availability query succeeds. Zero fatigue is eligible. Pure.
    fn recover_actor(&self) -> Option<u32> {
        self.order_actor().filter(|&id| self.state.recover_availability(id).is_ok())
    }

    /// Submits exactly one `Command::Recover` when it is available; otherwise a silent no-op.
    /// The command completes the turn, so a success arms the click guard from the input
    /// event's `now`: a double click cannot reach the newly active unit.
    fn use_recover(&mut self, now: Instant) {
        if let Some(me) = self.recover_actor() {
            self.run(Command::Recover { unit: me });
            self.click_guard_until = Some(now + MODAL_CLICK_GUARD);
        }
    }

    /// The unit the Rotation button and shortcut act for: the present, controllable active
    /// player unit with at least one legal Rotation target according to the rules. Pure.
    fn rotation_actor(&self) -> Option<u32> {
        self.order_actor().filter(|&id| !self.state.legal_rotation_targets(id).is_empty())
    }

    /// The unit the Footwork button and shortcut act for: the present, controllable active
    /// player unit with at least one legal Footwork destination according to the rules. Pure.
    fn footwork_actor(&self) -> Option<u32> {
        self.order_actor().filter(|&id| !self.state.legal_footwork_destinations(id).is_empty())
    }

    /// The unit the Stunning Blow button and shortcut act for: the present, controllable active
    /// player unit with at least one legal Stunning Blow target according to the rules. Pure.
    fn stunning_blow_actor(&self) -> Option<u32> {
        self.order_actor().filter(|&id| !self.state.legal_stunning_blow_targets(id).is_empty())
    }

    /// The actor `action` is currently available for. Pure.
    fn targeting_actor(&self, action: TargetAction) -> Option<u32> {
        match action {
            TargetAction::Rotation => self.rotation_actor(),
            TargetAction::Footwork => self.footwork_actor(),
            TargetAction::StunningBlow => self.stunning_blow_actor(),
        }
    }

    /// Whether the open targeting intent still matches the battle: same actor (still
    /// controllable), round and state hash. Pure.
    fn targeting_intent_current(&self, intent: PendingTargeting) -> bool {
        self.order_actor() == Some(intent.actor) && self.state.round == intent.round && self.state.state_hash() == intent.state_hash
    }

    /// Ends targeting and swallows follow-up clicks for `MODAL_CLICK_GUARD`.
    fn end_targeting(&mut self, now: Instant) -> Option<PendingTargeting> {
        self.click_guard_until = Some(now + MODAL_CLICK_GUARD);
        self.targeting.take()
    }

    /// Another action is about to run: cancel open targeting and arm the click guard so a
    /// follow-up click cannot reach the map. Does nothing when targeting is not open.
    fn cancel_targeting_for_action(&mut self, now: Instant) {
        if self.targeting.is_some() {
            self.end_targeting(now);
        }
    }

    /// Button and shortcut of `action` (2 Rotation, 3 Footwork, 6 Stunning Blow). A second activation of the
    /// open skill cancels. Otherwise, when the skill is available, capture actor, round and
    /// hash and open targeting; if the other skill's targeting is open this switches directly
    /// (no guard change). An unavailable skill leaves the current mode unchanged. Never
    /// submits a command and touches neither state nor RNG; a disabled activation is a silent
    /// no-op.
    fn toggle_targeting(&mut self, action: TargetAction, now: Instant) {
        if self.targeting.is_some_and(|t| t.action == action) {
            self.end_targeting(now);
        } else if let Some(actor) = self.targeting_actor(action) {
            self.targeting = Some(PendingTargeting { action, actor, round: self.state.round, state_hash: self.state.state_hash() });
        }
    }

    /// Map click while targeting. The click never reaches Move, Attack or another skill.
    /// Stale intent is discarded with feedback; a click that is not on a legal target keeps
    /// targeting open with feedback; a legal target clears targeting first, then submits
    /// exactly one command for the intent's action (`Rotation`, `Footwork` or `StunningBlow`). Legality is
    /// always the rules' query and preview, never client arithmetic.
    fn select_target(&mut self, now: Instant) {
        let Some(intent) = self.targeting else { return };
        let name = match intent.action {
            TargetAction::Rotation => "ROTATION",
            TargetAction::Footwork => "FOOTWORK",
            TargetAction::StunningBlow => "STUN BLOW",
        };
        if !self.targeting_intent_current(intent) {
            self.end_targeting(now);
            self.error = Some((format!("{name} DISCARDED: BATTLE CHANGED"), Instant::now()));
            return;
        }
        let h: Hex = layout::pixel_to_hex(self.mouse.0, self.mouse.1);
        let cmd = match intent.action {
            TargetAction::Rotation => self
                .state
                .legal_rotation_targets(intent.actor)
                .into_iter()
                .find(|&id| self.state.units[id as usize].pos == h)
                .filter(|&id| self.state.preview_rotation(intent.actor, id).is_ok())
                .map(|target| Command::Rotation { unit: intent.actor, target }),
            TargetAction::Footwork => self
                .state
                .legal_footwork_destinations(intent.actor)
                .into_iter()
                .find(|&to| to == h)
                .filter(|&to| self.state.preview_footwork(intent.actor, to).is_ok())
                .map(|to| Command::Footwork { unit: intent.actor, to }),
            TargetAction::StunningBlow => self
                .state
                .legal_stunning_blow_targets(intent.actor)
                .into_iter()
                .find(|&id| self.state.units[id as usize].pos == h)
                .filter(|&id| self.state.preview_stunning_blow(intent.actor, id).is_ok())
                .map(|target| Command::StunningBlow { attacker: intent.actor, target }),
        };
        match cmd {
            Some(cmd) => {
                self.end_targeting(now);
                self.run(cmd);
            }
            None => {
                let msg = match intent.action {
                    TargetAction::Rotation => "NOT A SWAP TARGET: CLICK A MARKED ALLY OR PRESS ESC",
                    TargetAction::Footwork => "NOT A STEP HEX: CLICK A MARKED HEX OR PRESS ESC",
                    TargetAction::StunningBlow => "NOT A STUN TARGET: CLICK A MARKED ENEMY OR PRESS ESC",
                };
                self.error = Some((msg.to_string(), Instant::now()));
            }
        }
    }

    /// Opens a confirmation dialog. Executes nothing and touches neither state nor RNG. While
    /// one is pending, another launch is ignored: it can neither switch nor confirm it.
    fn open_confirm(&mut self, action: ConfirmAction) {
        if self.pending_confirm.is_some() {
            return;
        }
        self.targeting = None;
        if let Some(actor) = self.order_actor() {
            self.pending_confirm =
                Some(PendingConfirm { action, actor, round: self.state.round, state_hash: self.state.state_hash() });
        }
    }

    fn close_confirm(&mut self, now: Instant) -> Option<PendingConfirm> {
        self.click_guard_until = Some(now + MODAL_CLICK_GUARD);
        self.pending_confirm.take()
    }

    fn cancel_confirm(&mut self, now: Instant) {
        self.close_confirm(now);
    }

    /// Clears the pending intent first, then submits exactly one command for its action if
    /// the stored actor, round and state hash still match and that actor is still under
    /// player control. Stale intent is discarded without a command.
    fn confirm_pending(&mut self, now: Instant) {
        let Some(pending) = self.close_confirm(now) else { return };
        let current = self.order_actor() == Some(pending.actor)
            && self.state.round == pending.round
            && self.state.state_hash() == pending.state_hash;
        if current {
            self.run(match pending.action {
                ConfirmAction::EndRound => Command::EndRound { unit: pending.actor },
                ConfirmAction::Retreat => Command::Retreat { unit: pending.actor },
            });
        } else {
            let what = match pending.action {
                ConfirmAction::EndRound => "END ROUND",
                ConfirmAction::Retreat => "RETREAT",
            };
            self.error = Some((format!("{what} DISCARDED: BATTLE CHANGED"), Instant::now()));
        }
    }

    /// Left mouse press. UI routing comes first: with the modal open only its
    /// buttons act; otherwise launch-button clicks never reach the hex handler.
    fn left_click(&mut self, now: Instant) {
        let (x, y) = self.mouse;
        if self.pending_confirm.is_some() {
            match layout::modal_hit(x, y) {
                Some(layout::ModalHit::Confirm) => self.confirm_pending(now),
                Some(layout::ModalHit::Cancel) => self.cancel_confirm(now),
                None => {}
            }
            return;
        }
        if self.click_guard_until.is_some_and(|until| now < until) {
            return;
        }
        match layout::ui_hit(x, y) {
            Some(layout::UiHit::EndRound) => {
                self.cancel_targeting_for_action(now);
                self.open_confirm(ConfirmAction::EndRound);
            }
            Some(layout::UiHit::Retreat) => {
                self.cancel_targeting_for_action(now);
                self.open_confirm(ConfirmAction::Retreat);
            }
            Some(layout::UiHit::Adrenaline) => {
                self.cancel_targeting_for_action(now);
                self.use_adrenaline();
            }
            Some(layout::UiHit::Indomitable) => {
                self.cancel_targeting_for_action(now);
                self.use_indomitable();
            }
            Some(layout::UiHit::Recover) => {
                self.cancel_targeting_for_action(now);
                self.use_recover(now);
            }
            Some(layout::UiHit::Rotation) => self.toggle_targeting(TargetAction::Rotation, now),
            Some(layout::UiHit::Footwork) => self.toggle_targeting(TargetAction::Footwork, now),
            Some(layout::UiHit::StunningBlow) => self.toggle_targeting(TargetAction::StunningBlow, now),
            None if self.targeting.is_some() => self.select_target(now),
            None => self.click(),
        }
    }

    fn click(&mut self) {
        let Some(me) = self.human_unit() else { return };
        let h: Hex = layout::pixel_to_hex(self.mouse.0, self.mouse.1);
        if self.state.reachable(me).iter().any(|(r, _)| *r == h) {
            self.run(Command::Move { unit: me, to: h });
        } else if let Some(t) = self.state.units.iter().find(|u| u.is_present() && u.pos == h && u.side == Side::Enemy) {
            let target = t.id;
            self.run(Command::Attack { attacker: me, target });
        } else {
            self.run(Command::Move { unit: me, to: h });
        }
    }

    /// Returns true when the application should quit. While a confirmation
    /// dialog is open only Enter (confirm) and Escape (cancel) act; while targeting
    /// is open Escape cancels it instead of quitting.
    fn key(&mut self, key: &Key, now: Instant) -> bool {
        if self.pending_confirm.is_some() {
            match key {
                Key::Named(NamedKey::Enter) => self.confirm_pending(now),
                Key::Named(NamedKey::Escape) => self.cancel_confirm(now),
                _ => {}
            }
            return false;
        }
        match key {
            Key::Named(NamedKey::Escape) if self.targeting.is_some() => {
                self.end_targeting(now);
            }
            Key::Named(NamedKey::Escape) => return true,
            Key::Character(c) => self.character_key_at(c, now),
            _ => {}
        }
        false
    }

    #[cfg(test)]
    fn character_key(&mut self, key: &str) {
        self.character_key_at(key, Instant::now());
    }

    fn character_key_at(&mut self, key: &str, now: Instant) {
        if self.pending_confirm.is_some() {
            return;
        }
        let key = key.to_ascii_lowercase();
        // H and Z are display preferences and 2/3/6 toggle targeting itself; every other
        // action shortcut cancels targeting before its normal handling.
        match key.as_str() {
            "x" | "v" | "1" | "4" | "5" | "e" | "w" => self.cancel_targeting_for_action(now),
            _ => {}
        }
        match key.as_str() {
            "r" => self.restart_cancelling_targeting(self.seed, now),
            "n" => self.restart_cancelling_targeting(self.seed + 1, now),
            "z" => self.control_overlay = !self.control_overlay,
            "h" => self.height_overlay = !self.height_overlay,
            "x" => self.open_confirm(ConfirmAction::EndRound),
            "v" => self.open_confirm(ConfirmAction::Retreat),
            "1" => self.use_adrenaline(),
            "2" => self.toggle_targeting(TargetAction::Rotation, now),
            "3" => self.toggle_targeting(TargetAction::Footwork, now),
            "4" => self.use_indomitable(),
            "5" => self.use_recover(now),
            "6" => self.toggle_targeting(TargetAction::StunningBlow, now),
            "e" => {
                if let Some(me) = self.human_unit() {
                    self.run(Command::EndTurn { unit: me });
                }
            }
            "w" => {
                if let Some(me) = self.human_unit() {
                    self.run(Command::Wait { unit: me });
                }
            }
            _ => {}
        }
    }

    /// One automatic command when it is not a human turn (enemy units and
    /// Fleeing or retreating player units) and no confirmation is pending. Returns whether it
    /// was the AI's turn to act.
    fn ai_step(&mut self) -> bool {
        if self.pending_confirm.is_some() || self.state.outcome().is_some() || self.human_unit().is_some() {
            return false;
        }
        if let Some(cmd) = ai::plan_turn(&self.state).first().copied() {
            self.run(cmd);
        }
        true
    }

    /// The vertices of the current frame. Reads the app state only.
    fn frame(&self) -> Vec<render::Vertex> {
        let me = self.human_unit();
        // Only a currently valid intent is rendered; a stale one is dropped on the next click.
        let targeting = self.targeting.filter(|&intent| self.targeting_intent_current(intent)).map(|intent| intent.action);
        let reach = if targeting.is_some() { Vec::new() } else { me.map(|u| self.state.reachable(u)).unwrap_or_default() };
        let hover = Some(layout::pixel_to_hex(self.mouse.0, self.mouse.1));
        let scene = render::Scene {
            state: &self.state,
            selected: me,
            hover,
            reach: &reach,
            log: &self.log,
            error: self.error.as_ref().map(|(s, _)| s.as_str()),
            seed: self.seed,
            control_overlay: self.control_overlay,
            height_overlay: self.height_overlay,
            confirm: self.pending_confirm.map(|p| p.action),
            targeting,
        };
        render::build_scene(&scene)
    }

    fn draw(&mut self) {
        if self.error.as_ref().is_some_and(|(_, t)| t.elapsed() > ERROR_TIME) {
            self.error = None;
        }
        let verts = self.frame();
        if let Some(g) = &mut self.gpu {
            g.draw(&verts);
        }
    }
}

impl ApplicationHandler for App {
    fn resumed(&mut self, el: &ActiveEventLoop) {
        if self.window.is_some() {
            return;
        }
        let attrs = Window::default_attributes()
            .with_title("battle-brothers-rust slice")
            .with_inner_size(LogicalSize::new(1280.0, 720.0))
            .with_resizable(false);
        let window = match el.create_window(attrs) {
            Ok(w) => Arc::new(w),
            Err(e) => {
                eprintln!("cannot create window: {e}");
                std::process::exit(1);
            }
        };
        match render::Gpu::new(window.clone()) {
            Ok(g) => self.gpu = Some(g),
            Err(e) => {
                eprintln!("graphics init failed: {e}");
                std::process::exit(1);
            }
        }
        self.window = Some(window);
    }

    fn window_event(&mut self, el: &ActiveEventLoop, _: WindowId, event: WindowEvent) {
        match event {
            WindowEvent::CloseRequested => el.exit(),
            WindowEvent::Resized(s) => {
                if let Some(g) = &mut self.gpu {
                    g.resize(s.width, s.height);
                }
            }
            WindowEvent::CursorMoved { position, .. } => {
                if let Some(w) = &self.window {
                    let s = w.inner_size();
                    self.mouse = (position.x as f32 * layout::WIN_W / s.width.max(1) as f32, position.y as f32 * layout::WIN_H / s.height.max(1) as f32);
                }
            }
            WindowEvent::MouseInput { state: ElementState::Pressed, button: MouseButton::Left, .. } => {
                self.left_click(Instant::now())
            }
            WindowEvent::KeyboardInput { event, .. } if key_press_accepted(event.state, event.repeat) => {
                if self.key(&event.logical_key, Instant::now()) {
                    el.exit();
                }
            }
            WindowEvent::RedrawRequested => self.draw(),
            _ => {}
        }
    }

    fn about_to_wait(&mut self, el: &ActiveEventLoop) {
        if self.last_ai.elapsed() >= AI_STEP && self.ai_step() {
            self.last_ai = Instant::now();
        }
        el.set_control_flow(ControlFlow::WaitUntil(Instant::now() + Duration::from_millis(33)));
        if let Some(w) = &self.window {
            w.request_redraw();
        }
    }
}

/// Only the initial press of a key acts; auto-repeat events are ignored.
fn key_press_accepted(state: ElementState, repeat: bool) -> bool {
    state == ElementState::Pressed && !repeat
}

fn main() {
    let args = match scenario::parse_args(std::env::args().skip(1)) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("{e}");
            std::process::exit(2);
        }
    };
    let el = match EventLoop::new() {
        Ok(el) => el,
        Err(e) => {
            eprintln!("cannot start event loop: {e}");
            std::process::exit(1);
        }
    };
    if let Err(e) = el.run_app(&mut App::from_args(args)) {
        eprintln!("event loop error: {e}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bb_rules::rules::{STUNNING_BLOW_AP, STUNNING_BLOW_FATIGUE};

    #[test]
    fn control_overlay_starts_disabled_and_z_toggles_without_changing_battle() {
        let mut app = App::new(17);
        assert!(!app.control_overlay);
        let hash = app.state.state_hash();
        let rng = app.state.rng.clone();

        app.character_key("Z");
        assert!(app.control_overlay);
        app.character_key("z");
        assert!(!app.control_overlay);
        assert_eq!(app.state.state_hash(), hash);
        assert_eq!(app.state.rng, rng);
    }

    #[test]
    fn control_overlay_preference_survives_restart_and_new_seed() {
        let mut app = App::new(17);
        app.character_key("z");
        assert!(app.control_overlay);

        app.character_key("r");
        assert_eq!(app.seed, 17);
        assert!(app.control_overlay);
        assert_eq!(app.state.rng.state, BattleState::new(17).rng.state);

        app.character_key("n");
        assert_eq!(app.seed, 18);
        assert!(app.control_overlay);
        assert_eq!(app.state.rng.state, BattleState::new(18).rng.state);
    }

    // --- Terrain preset and height overlay ----------------------------------------

    #[test]
    fn terrain_preset_selects_the_battle_and_the_initial_height_preference() {
        let flat = App::new(7);
        assert_eq!(flat.terrain, TerrainPreset::Flat);
        assert!(!flat.height_overlay);
        assert!(flat.state.elevations.is_flat());
        assert_eq!(flat.state.state_hash(), BattleState::new(7).state_hash());

        let flat = App::with_terrain(7, TerrainPreset::Flat);
        assert!(!flat.height_overlay);
        assert_eq!(flat.state.state_hash(), BattleState::new(7).state_hash());

        let hills = App::with_terrain(7, TerrainPreset::Hills);
        assert_eq!(hills.terrain, TerrainPreset::Hills);
        assert!(hills.height_overlay);
        assert!(!hills.control_overlay, "Z stays off");
        assert_eq!(hills.state.state_hash(), scenario::battle(7, TerrainPreset::Hills).state_hash());
        assert_eq!(hills.state.elevations.get(bb_rules::hex(0, -1)), Some(3));
        assert_eq!(hills.state.units, BattleState::new(7).units);
        assert_eq!(hills.state.rng, BattleState::new(7).rng);
    }

    #[test]
    fn parsed_swamp_launch_builds_the_app_scene_and_restarts_with_the_same_configuration() {
        const CAPABILITY_FLAGS: [&str; 9] = [
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
        let cases: [(Vec<&str>, u16); 3] = [
            (Vec::new(), 0),
            (vec!["--pathfinder"], 1 << 7),
            (CAPABILITY_FLAGS.to_vec(), 0b1_1111_1111),
        ];

        for (flags, expected_mask) in cases {
            let mut argv = vec!["--seed", "1", "--terrain", "swamp"];
            argv.extend(flags);
            let args = scenario::parse_args(argv.into_iter().map(String::from)).unwrap();
            let mut app = App::from_args(args);
            assert_eq!((app.seed, app.terrain), (1, TerrainPreset::Swamp));
            assert!(!app.height_overlay, "Swamp height numbers start off");
            assert!(!app.control_overlay, "Z starts off");
            assert!(app.state.elevations.is_flat());
            assert!(app.state.ground.entries().any(|(_, kind)| kind == bb_rules::terrain::TerrainKind::Swamp));
            for unit in &app.state.units {
                let actual = [
                    unit.has_adrenaline,
                    unit.has_rotation,
                    unit.has_footwork,
                    unit.has_fast_adaptation,
                    unit.has_dodge,
                    unit.has_nine_lives,
                    unit.has_berserk,
                    unit.has_pathfinder,
                    unit.has_indomitable,
                ]
                .into_iter()
                .enumerate()
                .fold(0u16, |mask, (bit, enabled)| mask | (u16::from(enabled) << bit));
                assert_eq!(actual, if unit.side == Side::Player { expected_mask } else { 0 }, "{}", unit.name);
            }

            let before_ap = app.state.units[0].ap;
            let before_fatigue = app.state.units[0].fatigue;
            let before_hash = app.state.state_hash();
            let before_rng = app.state.rng.clone();
            let destination = bb_rules::hex(-1, 3);
            let preview = app.state.preview_move(0, destination).unwrap();
            let expected_fatigue = if expected_mask & (1 << 7) != 0 { 3 } else { 6 };
            assert_eq!(preview.ap_cost, 6);
            assert_eq!(preview.fatigue_cost, expected_fatigue);
            assert_eq!(app.state.state_hash(), before_hash, "preview is read-only");
            assert_eq!(app.state.rng, before_rng, "preview consumes no RNG");

            let scene = app.frame();
            assert!(!scene.is_empty(), "the accepted renderer builds a scene");
            assert_eq!(app.state.state_hash(), before_hash, "scene construction is read-only");
            assert_eq!(app.state.rng, before_rng, "scene construction consumes no RNG");

            app.run(Command::Move { unit: 0, to: destination });
            assert_eq!(app.state.units[0].pos, destination);
            assert_eq!(before_ap - app.state.units[0].ap, 6);
            assert_eq!(app.state.units[0].fatigue - before_fatigue, expected_fatigue);
            assert_eq!(app.state.rng, before_rng, "the initial move consumes no RNG");
            let moved_hash = app.state.state_hash();
            let moved_rng = app.state.rng.clone();
            assert!(!app.frame().is_empty(), "renderer builds the post-move scene");
            assert_eq!(app.state.state_hash(), moved_hash, "rendering does not change battle state");
            assert_eq!(app.state.rng, moved_rng, "rendering consumes no RNG");

            app.character_key("h");
            app.character_key("z");
            let preferences = (app.height_overlay, app.control_overlay);
            assert_eq!(preferences, (true, true));

            for (key, seed) in [("r", 1), ("n", 2)] {
                let actor = &mut app.state.units[0];
                actor.ap = 1;
                actor.fatigue = 73;
                actor.adrenaline_pending = actor.has_adrenaline;
                actor.fast_adaptation_stacks = if actor.has_fast_adaptation { 4 } else { 0 };
                actor.nine_lives_used = actor.has_nine_lives;
                actor.berserk_used = actor.has_berserk;
                actor.indomitable_active = actor.has_indomitable;
                app.error = Some(("stale error".into(), Instant::now()));

                app.character_key(key);
                let fresh_args = LaunchArgs { seed, ..args };
                let fresh = scenario::launch_battle_for(fresh_args);
                assert_eq!(app.launch_args(seed), fresh_args);
                assert_eq!((app.seed, app.terrain), (seed, TerrainPreset::Swamp));
                assert_eq!((app.height_overlay, app.control_overlay), preferences);
                assert_eq!(app.state.state_hash(), fresh.state_hash());
                assert_eq!(app.state.rng, fresh.rng);
                assert_eq!(app.state.units, fresh.units, "resources and temporary capability state reset");
                assert!(app.log.is_empty());
                assert!(app.error.is_none());
                assert!(app.pending_confirm.is_none());
                assert!(app.targeting.is_none());
            }
        }
    }

    #[test]
    fn h_toggles_only_the_height_preference_without_touching_the_battle() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            let mut app = App::with_terrain(17, terrain);
            let initial = app.height_overlay;
            let before = snapshot(&app);
            let elevations = app.state.elevations.clone();
            app.character_key("h");
            assert_eq!(app.height_overlay, !initial);
            app.character_key("H");
            assert_eq!(app.height_overlay, initial);
            app.character_key("H");
            assert_eq!(app.height_overlay, !initial);
            assert_eq!(snapshot(&app), before, "H changes no battle state, RNG, log or seed");
            assert_eq!(app.state.elevations, elevations);
            assert!(app.error.is_none() && app.pending_confirm.is_none());
            assert!(!press(&mut app, ch("h")), "H never quits");
            assert_eq!(app.height_overlay, initial);
        }
    }

    #[test]
    fn height_and_zoc_preferences_are_independent() {
        let mut app = App::with_terrain(3, TerrainPreset::Hills);
        app.character_key("z");
        assert!(app.control_overlay && app.height_overlay);
        app.character_key("h");
        assert!(app.control_overlay && !app.height_overlay);
        app.character_key("z");
        assert!(!app.control_overlay && !app.height_overlay);
        app.character_key("h");
        assert!(!app.control_overlay && app.height_overlay);
    }

    #[test]
    fn terrain_and_height_preference_survive_restart_and_new_seed() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            for pref in [false, true] {
                let mut app = App::with_terrain(17, terrain);
                app.height_overlay = pref;
                open_dialog(&mut app);
                app.restart(app.seed);
                assert!(app.pending_confirm.is_none(), "the confirmation is still cleared");
                assert_eq!((app.terrain, app.seed, app.height_overlay), (terrain, 17, pref));
                assert_eq!(app.state.state_hash(), scenario::battle(17, terrain).state_hash());
                assert_eq!(app.state.rng, scenario::battle(17, terrain).rng);

                app.character_key("r");
                assert_eq!((app.terrain, app.seed, app.height_overlay), (terrain, 17, pref));
                assert_eq!(app.state.state_hash(), scenario::battle(17, terrain).state_hash());

                app.run(Command::Wait { unit: 0 });
                open_dialog(&mut app);
                app.character_key("x"); // ignored while the modal is open
                press(&mut app, named(NamedKey::Escape));
                app.character_key("n");
                assert!(app.pending_confirm.is_none());
                assert_eq!((app.terrain, app.seed, app.height_overlay), (terrain, 18, pref));
                assert_eq!(app.state.state_hash(), scenario::battle(18, terrain).state_hash());
                assert_eq!(app.state.rng, scenario::battle(18, terrain).rng);
                assert!(app.log.is_empty());
            }
        }
        // Toggling changes only the preference; a later restart keeps the toggled value.
        let mut app = App::with_terrain(2, TerrainPreset::Hills);
        app.character_key("h");
        app.character_key("n");
        assert!(!app.height_overlay);
        app.character_key("h");
        app.character_key("r");
        assert!(app.height_overlay);
        assert!(!app.state.elevations.is_flat());
    }

    #[test]
    fn h_is_consumed_while_either_confirmation_is_open() {
        for retreat in [false, true] {
            for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
                let mut app = App::with_terrain(1, terrain);
                if retreat {
                    open_retreat(&mut app);
                } else {
                    open_dialog(&mut app);
                }
                let (pending, pref, before) = (app.pending_confirm, app.height_overlay, snapshot(&app));
                for key in ["h", "H", "h"] {
                    assert!(!press(&mut app, ch(key)));
                    app.character_key(key);
                }
                assert_eq!((app.pending_confirm, app.height_overlay), (pending, pref));
                assert_eq!(snapshot(&app), before);
                // Once the dialog is closed H works again.
                press(&mut app, named(NamedKey::Escape));
                press(&mut app, ch("h"));
                assert_eq!(app.height_overlay, !pref);
            }
        }
    }

    #[test]
    fn h_works_during_enemy_turns_and_after_the_battle_ended() {
        let mut app = App::with_terrain(1, TerrainPreset::Hills);
        app.state.queue = vec![3, 0];
        assert_eq!(app.human_unit(), None);
        press(&mut app, ch("h"));
        assert!(!app.height_overlay);
        app.state.outcome = Some(bb_rules::Outcome::Victory);
        press(&mut app, ch("h"));
        assert!(app.height_overlay);
        app.draw(); // no window/GPU: only builds the scene
        assert!(!app.state.elevations.is_flat());
    }

    #[test]
    fn drawing_with_either_preference_leaves_the_battle_unchanged() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            let mut app = App::with_terrain(5, terrain);
            let before = snapshot(&app);
            let elevations = app.state.elevations.clone();
            for _ in 0..2 {
                app.draw();
                app.character_key("h");
                app.draw();
            }
            assert_eq!(snapshot(&app), before);
            assert_eq!(app.state.elevations, elevations);
        }
    }

    // --- End Round confirmation ---------------------------------------------

    fn center(r: layout::Rect) -> (f32, f32) {
        (r.x + r.w / 2.0, r.y + r.h / 2.0)
    }

    fn click_at(app: &mut App, pt: (f32, f32), now: Instant) {
        app.mouse = pt;
        app.left_click(now);
    }

    fn press(app: &mut App, key: Key) -> bool {
        app.key(&key, Instant::now())
    }

    fn named(k: NamedKey) -> Key {
        Key::Named(k)
    }

    fn ch(c: &str) -> Key {
        Key::Character(c.into())
    }

    /// (state hash, rng, queue, round, log length, seed, overlay preference).
    fn snapshot(app: &App) -> (u64, bb_rules::Rng, Vec<u32>, u32, usize, u64, bool) {
        (app.state.state_hash(), app.state.rng.clone(), app.state.queue.clone(), app.state.round, app.log.len(), app.seed, app.control_overlay)
    }

    /// P1 acts, E1 is next, and P2 follows E1 so a normal EndTurn can demonstrate the
    /// stunned queue entry being consumed. This only arranges the encounter; it grants no skill.
    fn arrange_stun_encounter(mut app: App) -> App {
        for unit in &mut app.state.units {
            unit.alive = false;
            unit.fled = false;
            unit.turn_started = false;
            unit.ap = 0;
            unit.fatigue = 0;
        }
        for (id, pos) in [(0usize, bb_rules::hex(0, -2)), (3, bb_rules::hex(1, -2)), (1, bb_rules::hex(-4, 2))] {
            let unit = &mut app.state.units[id];
            unit.alive = true;
            unit.pos = pos;
        }
        app.state.units[0].ap = app.state.units[0].stats.ap_max;
        app.state.units[0].turn_started = true;
        app.state.units[0].stats.dmg_min = 1;
        app.state.units[0].stats.dmg_max = 1;
        app.state.queue = vec![0, 3, 1];
        assert_stun_fixture_valid(&app.state);
        app
    }

    /// T64 compatibility fixture: capability is deliberately granted in test code.
    fn stun_app() -> App {
        let mut app = arrange_stun_encounter(App::new(1));
        app.state.units[0].has_stunning_blow = true;
        app
    }

    /// T65 fixture: the capability comes only from the parsed launch option.
    fn parsed_stun_app(terrain: TerrainPreset) -> (App, LaunchArgs) {
        let terrain = match terrain {
            TerrainPreset::Flat => "flat",
            TerrainPreset::Hills => "hills",
            TerrainPreset::Swamp => "swamp",
        };
        let args = scenario::parse_args(["--seed", "1", "--terrain", terrain, "--stunning-blow"].map(String::from)).unwrap();
        let app = arrange_stun_encounter(App::from_args(args));
        (app, args)
    }

    fn assert_stun_fixture_valid(state: &BattleState) {
        let present: Vec<_> = state.units.iter().filter(|u| u.is_present()).collect();
        for (i, unit) in present.iter().enumerate() {
            assert!(bb_rules::hex::map_hexes().contains(&unit.pos) && !unit.pos.is_obstacle(), "{} fixture position", unit.name);
            assert!(present[..i].iter().all(|other| other.pos != unit.pos), "{} shares a fixture position", unit.name);
            assert!(unit.hp > 0 && unit.hp <= unit.stats.hp && unit.armour <= unit.stats.armour, "{} fixture health", unit.name);
            assert!(unit.ap <= unit.stats.ap_max && unit.fatigue <= unit.stats.fatigue_max, "{} fixture resources", unit.name);
            assert!(unit.stats.dmg_min <= unit.stats.dmg_max, "{} fixture damage range", unit.name);
        }
        assert_eq!(state.queue, vec![0, 3, 1]);
    }

    fn force_stunning_blow_result(app: &mut App, hit: bool) {
        assert_stun_fixture_valid(&app.state);
        let chance = app.state.preview_stunning_blow(0, 3).expect("valid stun preview").hit_chance;
        let seed = (0u64..10_000)
            .find(|&seed| {
                let mut rng = bb_rules::Rng::new(seed);
                (((rng.next_u64() % 100) as u32) < chance) == hit
            })
            .expect("a deterministic roll for either result");
        app.state.rng = bb_rules::Rng::new(seed);
    }

    fn has_stun_marker(app: &App) -> bool {
        app.frame().iter().any(|vertex| vertex.color == render::STUN_MARKER_TEXT)
    }

    fn open_dialog(app: &mut App) {
        press(app, ch("x"));
        assert!(app.pending_confirm.is_some());
    }

    /// Stock scenario, only P1 left in the queue: End Round exhausts the queue
    /// and the next round starts with a player unit again.
    fn last_player_app() -> App {
        let mut app = App::new(1);
        app.state.queue = vec![0];
        app
    }

    #[test]
    fn x_and_the_button_open_the_dialog_without_executing_anything() {
        let mut app = App::new(1);
        let before = snapshot(&app);
        assert!(!press(&mut app, ch("x")));
        let expected = PendingConfirm { action: ConfirmAction::EndRound, actor: 0, round: 1, state_hash: app.state.state_hash() };
        assert_eq!(app.pending_confirm, Some(expected));
        assert_eq!(snapshot(&app), before);
        assert!(app.error.is_none());

        let mut app = App::new(1);
        click_at(&mut app, center(layout::END_ROUND_BUTTON), Instant::now());
        assert_eq!(app.pending_confirm, Some(expected));
        assert_eq!(snapshot(&app), before);

        // Upper-case X (shift/caps lock) opens it too.
        let mut app = App::new(1);
        app.character_key("X");
        assert_eq!(app.pending_confirm, Some(expected));
    }

    #[test]
    fn escape_and_cancel_close_the_dialog_without_quitting_or_changing_the_battle() {
        let mut app = App::new(1);
        let before = snapshot(&app);
        open_dialog(&mut app);
        assert!(!press(&mut app, named(NamedKey::Escape)), "Escape cancels the dialog instead of quitting");
        assert!(app.pending_confirm.is_none());
        assert_eq!(snapshot(&app), before);

        open_dialog(&mut app);
        click_at(&mut app, center(layout::CANCEL_BUTTON), Instant::now());
        assert!(app.pending_confirm.is_none());
        assert_eq!(snapshot(&app), before);

        // Outside the dialog Escape still quits.
        assert!(press(&mut app, named(NamedKey::Escape)));
    }

    #[test]
    fn modal_consumes_map_clicks_and_gameplay_shortcuts() {
        let mut app = App::new(1);
        open_dialog(&mut app);
        let before = snapshot(&app);
        let pending = app.pending_confirm;

        let reachable = app.state.reachable(0)[0].0;
        let reachable_px = layout::hex_to_pixel(reachable);
        let enemy_px = layout::hex_to_pixel(app.state.units[3].pos);
        let now = Instant::now() + Duration::from_secs(10);
        for pt in [
            reachable_px,
            enemy_px,
            (10.0, 10.0),
            (layout::DIALOG.x + 5.0, layout::DIALOG.y + 5.0),
            center(layout::END_ROUND_BUTTON),
            center(layout::END_ROUND_BUTTON),
        ] {
            click_at(&mut app, pt, now);
            assert_eq!(app.pending_confirm, pending, "click at {pt:?} must not resolve the dialog");
            assert_eq!(snapshot(&app), before);
        }
        for key in ["e", "w", "x", "x", "x", "z", "Z", "r", "n", "q"] {
            assert!(!press(&mut app, ch(key)));
            assert_eq!(app.pending_confirm, pending, "key {key} must not resolve the dialog");
            assert_eq!(snapshot(&app), before, "key {key}");
        }
        assert!(app.error.is_none());
        assert_eq!(app.mouse, center(layout::END_ROUND_BUTTON));
    }

    #[test]
    fn confirm_button_and_enter_submit_exactly_one_end_round() {
        for use_enter in [false, true] {
            let mut app = App::new(1);
            let mut direct = app.state.clone();
            direct.apply(Command::EndRound { unit: 0 }).unwrap();

            open_dialog(&mut app);
            if use_enter {
                assert!(!press(&mut app, named(NamedKey::Enter)));
            } else {
                click_at(&mut app, center(layout::CONFIRM_BUTTON), Instant::now());
            }
            assert!(app.pending_confirm.is_none());
            assert_eq!(app.state.state_hash(), direct.state_hash());
            assert_eq!(app.state.rng, direct.rng);
            assert_eq!(app.state.queue, direct.queue);
            assert_eq!(app.state.units[0].ap, 0, "the issuer forfeits its AP");
            assert_eq!(app.state.active_unit().unwrap().side, Side::Enemy);
            assert!(app.state.queue.starts_with(&[3, 4, 5]), "enemies remain scheduled: {:?}", app.state.queue);
            assert!(app.state.units[1].skip_turn && app.state.units[2].skip_turn);
            assert!(app.error.is_none());
        }
    }

    #[test]
    fn a_second_confirmation_input_submits_nothing_even_after_a_new_player_round() {
        let mut app = last_player_app();
        open_dialog(&mut app);
        let now = Instant::now();
        click_at(&mut app, center(layout::CONFIRM_BUTTON), now);
        assert_eq!(app.state.round, 2);
        assert_eq!(app.human_unit(), Some(0), "the next round starts with a player unit");
        let after = snapshot(&app);

        // Enter, a repeated Confirm click and a repeated launch-position click all do nothing.
        press(&mut app, named(NamedKey::Enter));
        assert_eq!(snapshot(&app), after);
        click_at(&mut app, center(layout::CONFIRM_BUTTON), now);
        assert_eq!(snapshot(&app), after);
        assert!(app.error.is_none(), "the swallowed click must not reach the map handler");
        assert!(app.pending_confirm.is_none());

        // The guard expires, after which map clicks work again.
        let until = app.click_guard_until.unwrap();
        click_at(&mut app, center(layout::CONFIRM_BUTTON), until + Duration::from_millis(1));
        assert!(snapshot(&app) != after || app.error.is_some());
    }

    #[test]
    fn repeated_launch_clicks_and_x_never_confirm() {
        let mut app = App::new(1);
        let before = snapshot(&app);
        let now = Instant::now();
        click_at(&mut app, center(layout::END_ROUND_BUTTON), now);
        for _ in 0..4 {
            click_at(&mut app, center(layout::END_ROUND_BUTTON), now);
            press(&mut app, ch("x"));
        }
        assert!(app.pending_confirm.is_some());
        assert_eq!(snapshot(&app), before);
    }

    #[test]
    fn launch_clicks_never_reach_the_hex_handler() {
        let mut app = App::new(1);
        let before = snapshot(&app);
        // Disabled: enemy turn. The click is consumed (no "Unreachable" feedback either).
        app.state.queue = vec![3, 0];
        let enemy_turn = snapshot(&app);
        click_at(&mut app, center(layout::END_ROUND_BUTTON), Instant::now());
        assert!(app.pending_confirm.is_none());
        assert!(app.error.is_none());
        assert_eq!(snapshot(&app), enemy_turn);
        assert_ne!(enemy_turn, before);
    }

    #[test]
    fn dialog_only_opens_for_a_living_active_player_in_a_running_battle() {
        let mut enemy_turn = App::new(1);
        enemy_turn.state.queue = vec![3, 0];
        let mut over = App::new(1);
        over.state.outcome = Some(bb_rules::Outcome::Victory);
        let mut dead = App::new(1);
        dead.state.units[0].alive = false;
        for app in [&mut enemy_turn, &mut over, &mut dead] {
            let before = snapshot(app);
            press(app, ch("x"));
            click_at(app, center(layout::END_ROUND_BUTTON), Instant::now());
            assert!(app.pending_confirm.is_none());
            assert_eq!(snapshot(app), before);
        }
        // Control: a second player unit active.
        let mut second = App::new(1);
        second.state.queue = vec![1, 0];
        press(&mut second, ch("x"));
        assert_eq!(second.pending_confirm.map(|p| p.actor), Some(1));
    }

    #[test]
    fn stale_intent_is_discarded_without_submitting_a_command() {
        type Tamper = fn(&mut App);
        let cases: [(&str, Tamper); 6] = [
            ("actor", |a| a.pending_confirm.as_mut().unwrap().actor = 1),
            ("round", |a| a.pending_confirm.as_mut().unwrap().round += 1),
            ("hash", |a| a.pending_confirm.as_mut().unwrap().state_hash ^= 1),
            ("state changed", |a| a.state.units[0].ap -= 1),
            ("enemy took control", |a| a.state.queue = vec![3, 0]),
            ("battle over", |a| a.state.outcome = Some(bb_rules::Outcome::Defeat)),
        ];
        for (name, tamper) in cases {
            for use_enter in [false, true] {
                let mut app = App::new(1);
                open_dialog(&mut app);
                tamper(&mut app);
                let before = snapshot(&app);
                if use_enter {
                    press(&mut app, named(NamedKey::Enter));
                } else {
                    click_at(&mut app, center(layout::CONFIRM_BUTTON), Instant::now());
                }
                assert!(app.pending_confirm.is_none(), "{name}");
                assert_eq!(snapshot(&app), before, "{name}: no command may be submitted");
                assert!(app.error.is_some(), "{name}: the player is told the intent was dropped");
            }
        }
    }

    #[test]
    fn restart_and_new_seed_clear_the_dialog_and_keep_the_overlay_preference() {
        let mut app = App::new(17);
        app.character_key("z");
        open_dialog(&mut app);
        app.restart(17);
        assert!(app.pending_confirm.is_none());
        assert!(app.control_overlay);
        assert_eq!(app.state.state_hash(), BattleState::new(17).state_hash());

        open_dialog(&mut app);
        app.restart(18);
        assert!(app.pending_confirm.is_none());
        assert!(app.control_overlay);
        assert_eq!(app.seed, 18);
        // The restarted battle needs a fresh dialog; an old confirmation cannot fire.
        press(&mut app, named(NamedKey::Enter));
        assert_eq!(app.state.state_hash(), BattleState::new(18).state_hash());
    }

    #[test]
    fn ai_commands_are_blocked_while_a_confirmation_is_pending() {
        let mut app = App::new(1);
        app.state.queue = vec![3, 4, 5, 0];
        let intent = PendingConfirm { action: ConfirmAction::EndRound, actor: 0, round: 1, state_hash: 0 };
        app.pending_confirm = Some(intent);
        let before = snapshot(&app);
        assert!(!app.ai_step());
        assert_eq!(snapshot(&app), before);
        assert_eq!(app.pending_confirm, Some(intent));

        app.pending_confirm = None;
        assert!(app.ai_step(), "control: the AI acts once nothing is pending");
        assert_ne!(snapshot(&app), before);
    }

    #[test]
    fn queued_player_count_includes_the_active_unit_and_wait_deferred_players() {
        let mut app = App::new(1);
        assert_eq!(render::queued_player_units(&app.state), 3);
        app.run(Command::Wait { unit: 0 });
        assert_eq!(app.human_unit(), Some(1));
        assert_eq!(render::queued_player_units(&app.state), 3, "the Wait-deferred P1 is still queued");
        app.run(Command::EndTurn { unit: 1 });
        assert_eq!(render::queued_player_units(&app.state), 2);
        app.state.units[2].alive = false;
        app.state.queue.retain(|&id| id != 2);
        assert_eq!(render::queued_player_units(&app.state), 1);
    }

    #[test]
    fn morale_check_reaches_the_log_with_the_state_change_visible_early() {
        // P1 hits E1 for 20 unarmoured damage; Resolve 0 makes the check fail.
        // Scan seeds (deterministic) for the first attack roll that hits.
        let app = (1..200)
            .map(|seed| {
                let mut app = App::new(seed);
                app.state.units[0].pos = bb_rules::hex(1, 0);
                app.state.units[3].pos = bb_rules::hex(2, 0);
                app.state.units[3].armour = 0;
                app.state.units[3].stats.resolve = 0;
                app.state.units[0].stats.dmg_min = 20;
                app.state.units[0].stats.dmg_max = 20;
                app.run(Command::Attack { attacker: 0, target: 3 });
                app
            })
            .find(|app| app.log.iter().any(|l| l.contains("hits")))
            .unwrap();
        assert_eq!(app.log.len(), 2, "attack line then morale line: {:?}", app.log);
        assert!(app.log[0].starts_with("P1 hits E1"));
        let line = &app.log[1];
        assert!(line.starts_with("E1 Steady>Wavering r"), "{line}");
        assert!(line.contains(">=0"), "{line}");
        assert!(line.chars().count() <= 31, "log lines are truncated to 31 chars: {line}");
        assert_eq!(app.state.units[3].morale, bb_rules::Morale::Wavering);
    }

    #[test]
    fn nine_lives_survival_is_logged_right_after_the_hit() {
        // P1 hits E1 for a lethal 20; E1 is protected, so only 4 HP are lost (no morale check).
        let app = (1..200)
            .map(|seed| {
                let mut app = App::new(seed);
                app.state.units[0].pos = bb_rules::hex(1, 0);
                app.state.units[3].pos = bb_rules::hex(2, 0);
                app.state.units[3].armour = 0;
                app.state.units[3].hp = 5;
                app.state.units[3].has_nine_lives = true;
                app.state.units[0].stats.dmg_min = 20;
                app.state.units[0].stats.dmg_max = 20;
                app.run(Command::Attack { attacker: 0, target: 3 });
                app
            })
            .find(|app| app.log.iter().any(|l| l.contains("hits")))
            .unwrap();
        assert_eq!(app.log.len(), 2, "attack line then trigger line: {:?}", app.log);
        assert!(app.log[0].ends_with("hp -4"), "{:?}", app.log);
        assert_eq!(app.log[1], "E1 nine lives: survives");
        assert!(app.log[1].chars().count() <= 31, "log lines are truncated to 31 chars");
        assert!(app.state.units[3].alive && app.state.units[3].hp == 1);
    }

    #[test]
    fn berserk_refund_is_logged_after_the_kill_lines() {
        // P1 kills the 1-HP E1 on a hitting seed; the refund is the last line of the attack.
        let app = (1..200)
            .map(|seed| {
                let mut app = App::new(seed);
                app.state.units[0].pos = bb_rules::hex(1, 0);
                app.state.units[3].pos = bb_rules::hex(2, 0);
                app.state.units[3].armour = 0;
                app.state.units[3].hp = 1;
                app.state.units[0].has_berserk = true;
                app.run(Command::Attack { attacker: 0, target: 3 });
                app
            })
            .find(|app| app.log.iter().any(|l| l == "E1 dies"))
            .unwrap();
        let last = app.log.last().unwrap();
        assert_eq!(last, "P1 berserk: +4 AP");
        assert!(last.chars().count() <= 31, "log lines are truncated to 31 chars");
        assert_eq!(app.state.units[0].ap, 9);
        assert!(app.state.units[0].berserk_used);
    }

    /// Build P1's launch-granted Berserk encounter with a legal AP budget and deterministic hit.
    fn consume_launch_berserk(app: &mut App) {
        assert!(app.state.units[0].has_berserk && !app.state.units[0].berserk_used);
        let park = [
            bb_rules::hex(-4, 2),
            bb_rules::hex(-4, 3),
            bb_rules::hex(-4, 4),
            bb_rules::hex(4, -4),
            bb_rules::hex(4, -3),
            bb_rules::hex(4, -2),
        ];
        for (unit, at) in app.state.units.iter_mut().zip(park) {
            unit.pos = at;
            unit.turn_started = false;
        }
        app.state.units[0].pos = bb_rules::hex(0, -2);
        app.state.units[3].pos = bb_rules::hex(1, -2);
        app.state.units[0].ap = 5;
        app.state.units[0].fatigue = 0;
        app.state.units[0].stats.melee_skill = 100;
        app.state.units[0].turn_started = true;
        app.state.units[3].hp = 1;
        app.state.units[3].armour = 0;
        app.state.queue.retain(|&unit| unit != 0);
        app.state.queue.insert(0, 0);

        let attack = Command::Attack { attacker: 0, target: 3 };
        let rng = (1u64..10_000)
            .map(bb_rules::Rng::new)
            .find(|rng| {
                let mut probe = app.state.clone();
                probe.rng = rng.clone();
                matches!(probe.apply(attack).ok().as_deref(), Some(events)
                    if events.iter().any(|event| matches!(event, Event::Attacked { attacker: 0, target: 3, hit: true, .. }))
                    && events.iter().any(|event| matches!(event, Event::Died { unit: 3 }))
                    && events.iter().any(|event| matches!(event, Event::BerserkTriggered { unit: 0, ap_gained: 4 })))
            })
            .expect("some RNG state makes the launch-enabled attack kill");
        app.state.rng = rng;
        app.run(attack);
        assert_eq!((app.state.units[0].ap, app.state.units[0].fatigue), (5, bb_rules::rules::ATTACK_FATIGUE));
        assert!(app.state.units[0].berserk_used);
        assert!(app.log.iter().any(|line| line == "P1 berserk: +4 AP"), "{:?}", app.log);
    }

    #[test]
    fn killing_frenzy_launch_argument_survives_r_and_n_after_real_activation() {
        let args = scenario::parse_args([
            "--seed", "17", "--terrain", "flat", "--killing-frenzy", "--berserk", "--stunning-blow",
        ].map(String::from)).unwrap();
        for (key, seed) in [("r", 17), ("n", 18)] {
            let mut app = App::from_args(args);
            assert_eq!(app.launch_args(17), args);
            assert!(app.killing_frenzy && app.state.units[0].has_killing_frenzy);
            assert!(app.state.units.iter().filter(|u| u.side == Side::Enemy).all(|u| !u.has_killing_frenzy));
            assert!(app.state.units.iter().all(|u| u.killing_frenzy_turns == 0));

            let park = [
                bb_rules::hex(-4, 2), bb_rules::hex(-4, 3), bb_rules::hex(-4, 4),
                bb_rules::hex(4, -4), bb_rules::hex(4, -3), bb_rules::hex(4, -2),
            ];
            for (unit, at) in app.state.units.iter_mut().zip(park) {
                unit.pos = at;
                unit.turn_started = false;
            }
            app.state.units[0].pos = bb_rules::hex(0, -2);
            app.state.units[3].pos = bb_rules::hex(1, -2);
            app.state.units[4].pos = bb_rules::hex(0, -1);
            app.state.units[0].ap = bb_rules::rules::ATTACK_AP;
            app.state.units[0].fatigue = 0;
            app.state.units[0].stats.melee_skill = 100;
            app.state.units[0].turn_started = true;
            app.state.units[3].hp = 1;
            app.state.units[3].armour = 0;
            app.state.queue.retain(|&unit| unit != 0);
            app.state.queue.insert(0, 0);

            let attack = Command::Attack { attacker: 0, target: 3 };
            let rng = (1u64..10_000).map(bb_rules::Rng::new).find(|rng| {
                let mut probe = app.state.clone();
                probe.rng = rng.clone();
                matches!(probe.apply(attack).ok().as_deref(), Some(events)
                    if events.iter().any(|event| matches!(event, Event::Attacked { attacker: 0, target: 3, hit: true, .. }))
                    && events.iter().any(|event| matches!(event, Event::Died { unit: 3 }))
                    && events.iter().any(|event| matches!(event, Event::BerserkTriggered { unit: 0, ap_gained: 4 })))
            }).expect("launch-enabled player can make a paid melee kill");
            app.state.rng = rng;
            app.run(attack);
            assert_eq!(app.state.units[0].killing_frenzy_turns, 2);
            assert!(app.state.units[0].is_killing_frenzy_active());
            assert_eq!(app.state.units[0].ap, bb_rules::rules::ATTACK_AP, "Berserk refund is present");

            // The launch-enabled Stunning Blow control needs 6 AP to open targeting.
            app.state.units[0].ap = bb_rules::rules::STUNNING_BLOW_AP;
            app.character_key("6");
            assert_eq!(app.targeting.map(|t| t.action), Some(TargetAction::StunningBlow));
            app.error = Some(("transient error".into(), Instant::now()));
            app.character_key(key);

            let fresh_args = LaunchArgs { seed, ..args };
            let fresh = scenario::launch_battle_for(fresh_args);
            assert_eq!(app.launch_args(seed), fresh_args);
            assert_eq!(app.state.units, fresh.units, "R/N rebuilds a fresh capability with zero Frenzy counter");
            assert!(app.state.units.iter().all(|u| u.killing_frenzy_turns == 0));
            assert_eq!(app.state.state_hash(), fresh.state_hash());
            assert_eq!(app.state.rng, fresh.rng);
            assert!(app.log.is_empty() && app.error.is_none());
            assert!(app.pending_confirm.is_none() && app.targeting.is_none());
            assert!(app.click_guard_until.is_some(), "the existing targeting restart guard remains in force");
        }
        assert!(!App::with_terrain(1, TerrainPreset::Flat).killing_frenzy);
        assert!(!App::with_flags(1, TerrainPreset::Flat, true, true).killing_frenzy);
    }

    #[test]
    fn pathfinder_launch_argument_reaches_app_and_old_constructors_stay_disabled() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            let terrain_arg = if terrain == TerrainPreset::Hills { "hills" } else { "flat" };
            let args = scenario::parse_args(["--seed", "6", "--terrain", terrain_arg, "--pathfinder"].map(String::from)).unwrap();
            let app = App::from_args(args);
            assert!(app.pathfinder);
            assert_eq!(app.launch_args(6), args);
            assert!(app.state.units.iter().all(|u| u.has_pathfinder == (u.side == Side::Player)));
            assert!(!App::with_terrain(6, terrain).pathfinder);
            assert!(!App::with_flags(6, terrain, true, true).pathfinder);
            assert!(App::with_terrain(6, terrain).state.units.iter().all(|u| !u.has_pathfinder));
        }
    }

    #[test]
    fn launch_pathfinder_move_and_berserk_state_survive_restart_and_new_seed() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            let args = LaunchArgs {
                seed: 17,
                terrain,
                adrenaline: true,
                rotation: true,
                footwork: true,
                fast_adaptation: true,
                dodge: true,
                nine_lives: true,
                berserk: true,
                pathfinder: true,
                indomitable: false,
                two_hex: false,
                stunning_blow: false, killing_frenzy: false,
            };
            let mut app = App::from_args(args);
            app.character_key("h");
            app.character_key("z");
            let preferences = (app.height_overlay, app.control_overlay);

            for (key, seed) in [("r", 17), ("n", 18)] {
                let destination = bb_rules::hex(-2, 3);
                let preview = app.state.preview_move(0, destination).unwrap();
                let rng = app.state.rng.clone();
                app.run(Command::Move { unit: 0, to: destination });
                assert_eq!((preview.ap_cost, preview.fatigue_cost), (2, 1));
                assert_eq!((app.state.units[0].pos, app.state.units[0].ap, app.state.units[0].fatigue), (destination, 7, preview.fatigue_cost));
                assert_eq!(app.state.rng, rng, "no reaction was needed for the App move");
                assert!(app.log.iter().any(|line| line == "P1 moves 1"));

                consume_launch_berserk(&mut app);
                assert!(app.state.units[0].berserk_used, "the actual App::run attack consumes it");
                app.state.units[0].ap = 1;
                app.state.units[0].fatigue = 50;
                app.state.units[0].fast_adaptation_stacks = 7;
                app.character_key(key);

                let fresh_args = LaunchArgs { seed, ..args };
                let fresh = scenario::launch_battle_for(fresh_args);
                assert_eq!(app.launch_args(seed), fresh_args);
                assert_eq!((app.seed, app.terrain), (seed, terrain));
                assert_eq!((app.height_overlay, app.control_overlay), preferences, "H/Z preferences survive");
                assert_eq!(app.state.state_hash(), fresh.state_hash());
                assert_eq!(app.state.rng, fresh.rng);
                assert!(app.state.units.iter().zip(&fresh.units).all(|(unit, initial)| {
                    unit.has_berserk == (unit.side == Side::Player)
                        && !unit.berserk_used
                        && unit.has_nine_lives == (unit.side == Side::Player)
                        && !unit.nine_lives_used
                        && unit.has_fast_adaptation == (unit.side == Side::Player)
                        && unit.fast_adaptation_stacks == 0
                        && unit.has_adrenaline == (unit.side == Side::Player)
                        && unit.has_rotation == (unit.side == Side::Player)
                        && unit.has_footwork == (unit.side == Side::Player)
                        && unit.has_dodge == (unit.side == Side::Player)
                        && unit.has_pathfinder == (unit.side == Side::Player)
                        && unit.ap == initial.ap
                        && unit.fatigue == initial.fatigue
                }), "restart restores fresh resources and every selected capability");
                assert!(app.log.is_empty());
            }
        }
    }

    #[test]
    fn passed_morale_check_is_logged_as_holding() {
        let app = (1..200)
            .map(|seed| {
                let mut app = App::new(seed);
                app.state.units[0].pos = bb_rules::hex(1, 0);
                app.state.units[3].pos = bb_rules::hex(2, 0);
                app.state.units[3].armour = 0;
                app.state.units[3].stats.resolve = 100;
                app.state.units[0].stats.dmg_min = 20;
                app.state.units[0].stats.dmg_max = 20;
                app.run(Command::Attack { attacker: 0, target: 3 });
                app
            })
            .find(|app| app.log.iter().any(|l| l.contains("hits")))
            .unwrap();
        assert!(app.log[1].starts_with("E1 holds Steady r"), "{:?}", app.log);
        assert!(app.log[1].chars().count() <= 31);
    }

    /// P1 kills E1 (1 HP) at a 95% hit chance, so the first seed whose roll hits works.
    fn ally_death_app(resolve: u32) -> App {
        (1..200)
            .map(|seed| {
                let mut app = App::new(seed);
                app.state.units[0].pos = bb_rules::hex(1, 0);
                app.state.units[3].pos = bb_rules::hex(2, 0);
                app.state.units[0].stats.melee_skill = 1000;
                app.state.units[3].hp = 1;
                app.state.units[3].armour = 0;
                for id in [4, 5] {
                    app.state.units[id].stats.resolve = resolve;
                }
                app.run(Command::Attack { attacker: 0, target: 3 });
                app
            })
            .find(|app| app.log.iter().any(|l| l.contains("hits")))
            .unwrap()
    }

    #[test]
    fn ally_death_checks_reach_the_log_after_the_death_line() {
        // Resolve 0: both surviving enemies fail, fallen ally named before the numbers.
        let app = ally_death_app(0);
        assert_eq!(app.log.len(), 5, "{:?}", app.log);
        assert!(app.log[0].starts_with("P1 hits E1"));
        assert_eq!(app.log[1], "E1 dies");
        assert!(app.log[2].starts_with("E2 Steady>Wavering, E1 died"), "{:?}", app.log);
        assert!(app.log[3].starts_with("E3 Steady>Wavering, E1 died"), "{:?}", app.log);
        assert!(app.log[2..].iter().all(|l| l.chars().count() <= 31), "{:?}", app.log);
        assert!(app.log[4].starts_with("P1 "), "the kill check follows the ally checks: {:?}", app.log);
        assert!(app.state.units[4..6].iter().all(|u| u.morale == bb_rules::Morale::Wavering));

        // Resolve 100: both hold.
        let app = ally_death_app(100);
        assert!(app.log[2].starts_with("E2 holds Steady, E1 died"), "{:?}", app.log);
        assert!(app.log[3].starts_with("E3 holds Steady, E1 died"), "{:?}", app.log);
        assert!(app.log[2..].iter().all(|l| l.chars().count() <= 31), "{:?}", app.log);

        // An already-Fleeing ally still gets a (logged) check.
        let app = (1..200)
            .map(|seed| {
                let mut app = App::new(seed);
                app.state.units[0].pos = bb_rules::hex(1, 0);
                app.state.units[3].pos = bb_rules::hex(2, 0);
                app.state.units[0].stats.melee_skill = 1000;
                app.state.units[3].hp = 1;
                app.state.units[3].armour = 0;
                app.state.units[4].morale = bb_rules::Morale::Fleeing;
                app.state.units[4].stats.resolve = 0;
                app.run(Command::Attack { attacker: 0, target: 3 });
                app
            })
            .find(|app| app.log.iter().any(|l| l.contains("hits")))
            .unwrap();
        assert!(app.log[2].starts_with("E2 still Fleeing, E1 died"), "{:?}", app.log);
    }

    /// P1 kills E1 (1 HP) at a 95% hit chance; the first seed whose roll hits works.
    fn kill_morale_app(resolve: u32, morale: bb_rules::Morale) -> App {
        (1..200)
            .map(|seed| {
                let mut app = App::new(seed);
                app.state.units[0].pos = bb_rules::hex(1, 0);
                app.state.units[3].pos = bb_rules::hex(2, 0);
                app.state.units[0].stats.melee_skill = 1000;
                app.state.units[0].stats.resolve = resolve;
                app.state.units[0].morale = morale;
                app.state.units[3].hp = 1;
                app.state.units[3].armour = 0;
                app.run(Command::Attack { attacker: 0, target: 3 });
                app
            })
            .find(|app| app.log.iter().any(|l| l.contains("hits")))
            .unwrap()
    }

    #[test]
    fn kill_morale_checks_reach_the_log_after_the_ally_checks() {
        use bb_rules::Morale;
        // Resolve 100 always passes: the improvement shows the step.
        let app = kill_morale_app(100, Morale::Steady);
        assert_eq!(app.log.len(), 5, "attack, death, two ally checks, kill check: {:?}", app.log);
        assert_eq!(app.log[1], "E1 dies");
        assert!(app.log[2].contains("E1 died") && app.log[3].contains("E1 died"), "{:?}", app.log);
        assert!(app.log[4].starts_with("P1 Steady>Confident r"), "{:?}", app.log);
        assert!(app.log[4].contains("<100"), "{:?}", app.log);
        assert_eq!(app.state.units[0].morale, Morale::Confident);

        // Resolve 0 always fails: unchanged morale reads "stays", not as an improvement.
        let app = kill_morale_app(0, Morale::Steady);
        assert!(app.log[4].starts_with("P1 stays Steady r"), "{:?}", app.log);
        assert!(app.log[4].ends_with(">=0"), "{:?}", app.log);
        assert_eq!(app.state.units[0].morale, Morale::Steady);

        // A passed check at Confident cannot improve: still "stays".
        let app = kill_morale_app(100, Morale::Confident);
        assert!(app.log[4].starts_with("P1 stays Confident r"), "{:?}", app.log);
        assert!(app.log[4].contains("<100"), "{:?}", app.log);

        // The kill-check lines (and the lines after the attack) fit the 31-char log limit.
        for (resolve, morale) in [(100, Morale::Breaking), (100, Morale::Wavering), (0, Morale::Confident)] {
            let app = kill_morale_app(resolve, morale);
            assert!(app.log[1..].iter().all(|l| l.chars().count() <= 31), "{:?}", app.log);
        }
    }

    #[test]
    fn edge_morale_change_is_logged_briefly_after_the_move_line() {
        use bb_rules::Morale;
        for (before, line) in [
            (Morale::Confident, "P1 edge Confident>Breaking"),
            (Morale::Steady, "P1 edge Steady>Breaking"),
            (Morale::Wavering, "P1 edge Wavering>Breaking"),
        ] {
            let mut app = App::new(3);
            app.state.units[0].pos = bb_rules::hex(0, 3);
            app.state.units[0].morale = before;
            app.run(Command::Move { unit: 0, to: bb_rules::hex(0, 4) });
            assert_eq!(app.log, vec!["P1 moves 1".to_string(), line.to_string()]);
            assert!(app.log.iter().all(|l| l.chars().count() <= 31), "{:?}", app.log);
            assert_eq!(app.state.units[0].morale, Morale::Breaking);
            assert!(app.error.is_none());
        }

        // Already Breaking: no change, so no event and no extra line.
        let mut app = App::new(3);
        app.state.units[0].pos = bb_rules::hex(0, 3);
        app.state.units[0].morale = Morale::Breaking;
        app.run(Command::Move { unit: 0, to: bb_rules::hex(0, 4) });
        assert_eq!(app.log, vec!["P1 moves 1".to_string()]);
    }

    #[test]
    fn indomitable_is_logged_briefly_through_the_command_path() {
        let mut app = App::new(3);
        app.state.units[0].has_indomitable = true;
        let (queue, ap) = (app.state.queue.clone(), app.state.units[0].ap);
        app.run(Command::Indomitable { unit: 0 });
        assert_eq!(app.log, vec!["P1 indomitable".to_string()]);
        assert!(app.log.iter().all(|l| l.is_ascii() && l.chars().count() <= 31), "{:?}", app.log);
        assert_eq!(app.state.queue, queue);
        assert_eq!(app.state.units[0].ap, ap - bb_rules::rules::INDOMITABLE_AP);
        assert!(app.state.units[0].indomitable_active);
        assert!(app.error.is_none());
    }

    #[test]
    fn adrenaline_is_logged_briefly_as_next_round() {
        let mut app = App::new(3);
        app.state.units[0].has_adrenaline = true;
        let (queue, ap) = (app.state.queue.clone(), app.state.units[0].ap);
        app.run(Command::Adrenaline { unit: 0 });
        assert_eq!(app.log, vec!["P1 adrenaline: next round".to_string()]);
        assert!(app.log.iter().all(|l| l.is_ascii() && l.chars().count() <= 31), "{:?}", app.log);
        assert_eq!(app.state.queue, queue);
        assert_eq!(app.state.units[0].ap, ap - 1);
        assert!(app.error.is_none());
    }

    #[test]
    fn rotation_is_logged_naming_both_participants() {
        let mut app = App::new(3);
        app.state.units[0].has_rotation = true;
        app.run(Command::Rotation { unit: 0, target: 2 });
        assert_eq!(app.log, vec!["P1 swaps with P3".to_string()]);
        assert!(app.log.iter().all(|l| l.is_ascii() && l.chars().count() <= 31), "{:?}", app.log);
        assert!(app.error.is_none());
        // A swap onto the map edge logs the swap, then the edge morale line.
        let mut app = App::new(3);
        app.state.units[0].has_rotation = true;
        app.run(Command::Rotation { unit: 0, target: 1 });
        assert_eq!(app.log, vec!["P1 swaps with P2".to_string(), "P1 edge Steady>Breaking".to_string()]);
    }

    #[test]
    fn footwork_is_logged_concisely_and_edge_arrival_follows() {
        let mut app = App::new(3);
        app.state.units[0].has_footwork = true;
        app.run(Command::Footwork { unit: 0, to: Hex { q: -2, r: 3 } });
        assert_eq!(app.log, vec!["P1 footwork".to_string()]);
        assert!(app.log.iter().all(|l| l.is_ascii() && l.chars().count() <= 31), "{:?}", app.log);
        assert!(app.error.is_none());
        // A step onto the map edge logs the step, then the edge morale line.
        let mut app = App::new(3);
        app.state.units[0].has_footwork = true;
        app.run(Command::Footwork { unit: 0, to: Hex { q: -3, r: 4 } });
        assert_eq!(app.log, vec!["P1 footwork".to_string(), "P1 edge Steady>Breaking".to_string()]);
        // A rejected Footwork logs nothing and reports the rule error.
        let mut app = App::new(3);
        app.run(Command::Footwork { unit: 0, to: Hex { q: -2, r: 3 } });
        assert!(app.log.is_empty());
        assert_eq!(app.error.as_ref().map(|e| e.0.as_str()), Some("MissingFootwork"));
    }

    // --- Retreat confirmation ---------------------------------------------------

    fn open_retreat(app: &mut App) {
        press(app, ch("v"));
        assert_eq!(app.pending_confirm.map(|p| p.action), Some(ConfirmAction::Retreat));
    }

    /// A confirmed order and the same command applied directly must leave identical battle state.
    fn retreat_states_equal(app: &App, direct: &BattleState) {
        assert_eq!(app.state.state_hash(), direct.state_hash());
        assert_eq!(app.state.rng, direct.rng);
        assert_eq!(app.state.queue, direct.queue);
        assert_eq!(app.state.outcome(), direct.outcome());
        assert_eq!(app.state.round, direct.round);
    }

    #[test]
    fn v_and_the_button_open_the_retreat_dialog_without_executing_anything() {
        let mut app = App::new(1);
        let before = snapshot(&app);
        assert!(!press(&mut app, ch("v")));
        let expected = PendingConfirm { action: ConfirmAction::Retreat, actor: 0, round: 1, state_hash: app.state.state_hash() };
        assert_eq!(app.pending_confirm, Some(expected));
        assert_eq!(snapshot(&app), before);
        assert!(app.error.is_none());
        assert!(app.state.units.iter().all(|u| !u.retreating));

        let mut app = App::new(1);
        click_at(&mut app, center(layout::RETREAT_BUTTON), Instant::now());
        assert_eq!(app.pending_confirm, Some(expected));
        assert_eq!(snapshot(&app), before);

        // Upper-case V (shift/caps lock) opens it too.
        let mut app = App::new(1);
        app.character_key("V");
        assert_eq!(app.pending_confirm, Some(expected));
    }

    #[test]
    fn retreat_only_opens_for_a_present_controllable_active_player_in_a_running_battle() {
        let mut enemy_turn = App::new(1);
        enemy_turn.state.queue = vec![3, 0];
        let mut over = App::new(1);
        over.state.outcome = Some(bb_rules::Outcome::Victory);
        let mut dead = App::new(1);
        dead.state.units[0].alive = false;
        let mut escaped = App::new(1);
        escaped.state.units[0].fled = true;
        let mut fleeing = fleeing_app();
        let mut retreating = retreating_app();
        for app in [&mut enemy_turn, &mut over, &mut dead, &mut escaped, &mut fleeing, &mut retreating] {
            let before = snapshot(app);
            press(app, ch("v"));
            press(app, ch("V"));
            click_at(app, center(layout::RETREAT_BUTTON), Instant::now());
            assert!(app.pending_confirm.is_none());
            assert_eq!(snapshot(app), before);
            assert!(app.error.is_none(), "a rejected launch gives no rules error");
        }
        // Control: a second player unit active.
        let mut second = App::new(1);
        second.state.queue = vec![1, 0];
        press(&mut second, ch("v"));
        assert_eq!(second.pending_confirm.map(|p| (p.action, p.actor)), Some((ConfirmAction::Retreat, 1)));
    }

    #[test]
    fn escape_and_cancel_close_the_retreat_dialog_without_a_command() {
        let mut app = App::new(1);
        let before = snapshot(&app);
        open_retreat(&mut app);
        assert!(!press(&mut app, named(NamedKey::Escape)), "Escape cancels the dialog instead of quitting");
        assert!(app.pending_confirm.is_none());
        assert_eq!(snapshot(&app), before);

        open_retreat(&mut app);
        click_at(&mut app, center(layout::CANCEL_BUTTON), Instant::now());
        assert!(app.pending_confirm.is_none());
        assert_eq!(snapshot(&app), before);
        assert!(app.state.units.iter().all(|u| !u.retreating));
        assert_eq!(app.human_unit(), Some(0), "the player keeps control");
        assert!(press(&mut app, named(NamedKey::Escape)), "outside the dialog Escape quits");
    }

    #[test]
    fn retreat_modal_consumes_map_clicks_and_unrelated_shortcuts() {
        let mut app = App::new(1);
        open_retreat(&mut app);
        let before = snapshot(&app);
        let pending = app.pending_confirm;
        let reachable_px = layout::hex_to_pixel(app.state.reachable(0)[0].0);
        let enemy_px = layout::hex_to_pixel(app.state.units[3].pos);
        let now = Instant::now() + Duration::from_secs(10);
        for pt in [
            reachable_px,
            enemy_px,
            (10.0, 10.0),
            (layout::DIALOG.x + 5.0, layout::DIALOG.y + 5.0),
            center(layout::END_ROUND_BUTTON),
            center(layout::RETREAT_BUTTON),
            center(layout::RETREAT_BUTTON),
        ] {
            click_at(&mut app, pt, now);
            assert_eq!(app.pending_confirm, pending, "click at {pt:?} must not resolve or switch the dialog");
            assert_eq!(snapshot(&app), before);
        }
        for key in ["e", "w", "x", "X", "v", "V", "v", "z", "Z", "r", "n", "q"] {
            assert!(!press(&mut app, ch(key)));
            assert_eq!(app.pending_confirm, pending, "key {key} must not resolve or switch the dialog");
            assert_eq!(snapshot(&app), before, "key {key}");
        }
        assert!(app.error.is_none());
    }

    #[test]
    fn opening_another_action_never_switches_or_confirms_the_pending_one() {
        // End Round first, then Retreat launches.
        let mut app = App::new(1);
        open_dialog(&mut app);
        let pending = app.pending_confirm;
        let before = snapshot(&app);
        press(&mut app, ch("v"));
        click_at(&mut app, center(layout::RETREAT_BUTTON), Instant::now() + Duration::from_secs(5));
        assert_eq!(app.pending_confirm, pending);
        assert_eq!(snapshot(&app), before);
        assert_eq!(pending.map(|p| p.action), Some(ConfirmAction::EndRound));

        // Retreat first, then End Round launches.
        let mut app = App::new(1);
        open_retreat(&mut app);
        let pending = app.pending_confirm;
        let before = snapshot(&app);
        press(&mut app, ch("x"));
        click_at(&mut app, center(layout::END_ROUND_BUTTON), Instant::now() + Duration::from_secs(5));
        assert_eq!(app.pending_confirm, pending);
        assert_eq!(snapshot(&app), before);
        assert_eq!(pending.map(|p| p.action), Some(ConfirmAction::Retreat));
    }

    #[test]
    fn confirmed_retreat_matches_the_direct_command_with_immediate_edge_escape() {
        for use_enter in [false, true] {
            let mut app = App::new(1);
            let mut direct = app.state.clone();
            direct.apply(Command::Retreat { unit: 0 }).unwrap();
            let mut direct_app = App::new(1);
            direct_app.run(Command::Retreat { unit: 0 });

            open_retreat(&mut app);
            if use_enter {
                assert!(!press(&mut app, named(NamedKey::Enter)));
            } else {
                click_at(&mut app, center(layout::CONFIRM_BUTTON), Instant::now());
            }
            assert!(app.pending_confirm.is_none());
            retreat_states_equal(&app, &direct);
            assert_eq!(app.log, direct_app.log, "same logging as the existing retreat path");
            assert_eq!(app.log, vec!["retreat ordered".to_string(), "P2 escapes".to_string()]);
            assert!(app.state.units[1].fled, "the edge unit leaves immediately");
            assert!(app.state.units[0].retreating && app.state.units[0].is_present());
            assert_eq!(app.human_unit(), None, "the interior units withdraw automatically");
            assert!(app.error.is_none());
            // The existing driver then performs Withdraw turns.
            assert!(app.ai_step());
            assert!(app.log.iter().any(|l| l.starts_with("P1 moves")), "{:?}", app.log);
        }
    }

    #[test]
    fn confirmed_terminal_retreat_ends_the_battle_like_the_direct_command() {
        let mut app = App::new(1);
        for id in [1, 2] {
            app.state.units[id].alive = false;
        }
        app.state.units[0].pos = bb_rules::hex(-4, 3);
        app.state.queue = vec![0, 3, 4, 5];
        let mut direct = app.state.clone();
        direct.apply(Command::Retreat { unit: 0 }).unwrap();
        assert_eq!(direct.outcome(), Some(bb_rules::Outcome::Retreat));

        open_retreat(&mut app);
        click_at(&mut app, center(layout::CONFIRM_BUTTON), Instant::now());
        retreat_states_equal(&app, &direct);
        assert_eq!(app.state.outcome(), Some(bb_rules::Outcome::Retreat));
        assert_eq!(app.log, vec!["retreat ordered".to_string(), "P1 escapes".to_string(), "Retreat Escape".to_string()]);
        assert!(!app.ai_step(), "nothing is driven after the battle ended");
    }

    #[test]
    fn end_round_and_retreat_dispatch_cannot_be_confused() {
        // End Round opened, Retreat key pressed, Enter: exactly EndRound ran.
        let mut app = App::new(1);
        let mut end_round = app.state.clone();
        end_round.apply(Command::EndRound { unit: 0 }).unwrap();
        open_dialog(&mut app);
        press(&mut app, ch("v"));
        press(&mut app, named(NamedKey::Enter));
        assert_eq!(app.state.state_hash(), end_round.state_hash());
        assert!(app.state.units.iter().all(|u| !u.retreating));

        // Retreat opened, End Round launches, Enter: exactly Retreat ran.
        let mut app = App::new(1);
        let mut retreat = app.state.clone();
        retreat.apply(Command::Retreat { unit: 0 }).unwrap();
        open_retreat(&mut app);
        press(&mut app, ch("x"));
        press(&mut app, named(NamedKey::Enter));
        assert_eq!(app.state.state_hash(), retreat.state_hash());
        assert_ne!(retreat.state_hash(), end_round.state_hash());
        assert!(app.state.units[0].retreating);
        assert!(!app.state.units[1].skip_turn, "End Round would have forfeited P2's turn");
    }

    #[test]
    fn stale_retreat_intent_is_discarded_without_submitting_a_command() {
        type Tamper = fn(&mut App);
        let cases: [(&str, Tamper); 6] = [
            ("actor", |a| a.pending_confirm.as_mut().unwrap().actor = 1),
            ("round", |a| a.pending_confirm.as_mut().unwrap().round += 1),
            ("hash", |a| a.pending_confirm.as_mut().unwrap().state_hash ^= 1),
            ("state changed", |a| a.state.units[0].ap -= 1),
            ("enemy took control", |a| a.state.queue = vec![3, 0]),
            ("battle over", |a| a.state.outcome = Some(bb_rules::Outcome::Defeat)),
        ];
        for (name, tamper) in cases {
            for use_enter in [false, true] {
                let mut app = App::new(1);
                open_retreat(&mut app);
                tamper(&mut app);
                let before = snapshot(&app);
                if use_enter {
                    press(&mut app, named(NamedKey::Enter));
                } else {
                    click_at(&mut app, center(layout::CONFIRM_BUTTON), Instant::now());
                }
                assert!(app.pending_confirm.is_none(), "{name}");
                assert_eq!(snapshot(&app), before, "{name}: no command may be submitted");
                assert!(app.state.units.iter().all(|u| !u.retreating), "{name}");
                let (msg, _) = app.error.as_ref().expect("the player is told the intent was dropped");
                assert!(msg.starts_with("RETREAT DISCARDED"), "{name}: {msg}");
            }
        }
        // The actor losing control (becoming Fleeing) after opening also drops the intent.
        let mut app = App::new(1);
        open_retreat(&mut app);
        app.state.units[0].morale = Morale::Fleeing;
        let before = snapshot(&app);
        press(&mut app, named(NamedKey::Enter));
        assert_eq!(snapshot(&app), before);
        assert!(app.state.units.iter().all(|u| !u.retreating));
        assert!(app.error.is_some());
    }

    #[test]
    fn repeated_retreat_launch_and_confirmation_inputs_submit_at_most_one_command() {
        let mut app = App::new(1);
        let before = snapshot(&app);
        let now = Instant::now();
        click_at(&mut app, center(layout::RETREAT_BUTTON), now);
        for _ in 0..4 {
            click_at(&mut app, center(layout::RETREAT_BUTTON), now);
            press(&mut app, ch("v"));
            press(&mut app, ch("V"));
        }
        assert!(app.pending_confirm.is_some());
        assert_eq!(snapshot(&app), before, "repeated launches never confirm");

        click_at(&mut app, center(layout::CONFIRM_BUTTON), now);
        let after = snapshot(&app);
        assert_eq!(app.log.iter().filter(|l| *l == "retreat ordered").count(), 1);
        // Repeats of every confirm path and of the launch inputs do nothing at all.
        press(&mut app, named(NamedKey::Enter));
        click_at(&mut app, center(layout::CONFIRM_BUTTON), now);
        click_at(&mut app, center(layout::RETREAT_BUTTON), now);
        press(&mut app, named(NamedKey::Enter));
        assert_eq!(snapshot(&app), after);
        assert!(app.pending_confirm.is_none());
        assert_eq!(app.log.iter().filter(|l| *l == "retreat ordered").count(), 1);
    }

    #[test]
    fn click_guard_swallows_clicks_after_either_dialog_closes_until_it_expires() {
        for retreat in [false, true] {
            for confirm in [false, true] {
                let mut app = App::new(1);
                if retreat {
                    open_retreat(&mut app);
                } else {
                    open_dialog(&mut app);
                }
                let t0 = Instant::now();
                click_at(&mut app, center(if confirm { layout::CONFIRM_BUTTON } else { layout::CANCEL_BUTTON }), t0);
                assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD));
                let after = snapshot(&app);
                // Inside the guard window: launch buttons and map clicks are swallowed.
                let inside = t0 + MODAL_CLICK_GUARD - Duration::from_millis(1);
                for pt in [center(layout::RETREAT_BUTTON), center(layout::END_ROUND_BUTTON), layout::hex_to_pixel(app.state.units[3].pos), (10.0, 10.0)] {
                    click_at(&mut app, pt, inside);
                    assert_eq!(snapshot(&app), after, "retreat={retreat} confirm={confirm} {pt:?}");
                    assert!(app.pending_confirm.is_none() && app.error.is_none());
                }
                // Once it expires a launch click works again (after Cancel the player is still in control).
                if !confirm {
                    click_at(&mut app, center(layout::RETREAT_BUTTON), t0 + MODAL_CLICK_GUARD);
                    assert_eq!(app.pending_confirm.map(|p| p.action), Some(ConfirmAction::Retreat));
                }
            }
        }
    }

    #[test]
    fn restart_and_new_seed_clear_a_pending_retreat_and_keep_the_overlay_preference() {
        let mut app = App::new(17);
        app.character_key("z");
        open_retreat(&mut app);
        app.restart(17);
        assert!(app.pending_confirm.is_none());
        assert!(app.control_overlay);
        assert_eq!(app.state.state_hash(), BattleState::new(17).state_hash());

        open_retreat(&mut app);
        app.character_key("v"); // ignored: modal open
        assert!(app.pending_confirm.is_some());
        app.restart(18);
        assert!(app.pending_confirm.is_none());
        assert!(app.control_overlay);
        assert_eq!(app.seed, 18);
        press(&mut app, named(NamedKey::Enter));
        assert_eq!(app.state.state_hash(), BattleState::new(18).state_hash(), "an old confirmation cannot fire");
        assert!(app.state.units.iter().all(|u| !u.retreating));
        assert_eq!(app.click_guard_until, None);
    }

    #[test]
    fn automatic_commands_are_paused_while_a_retreat_dialog_is_open() {
        let mut app = App::new(1);
        app.state.queue = vec![3, 4, 5, 0];
        app.pending_confirm = Some(PendingConfirm { action: ConfirmAction::Retreat, actor: 0, round: 1, state_hash: 0 });
        let before = snapshot(&app);
        assert!(!app.ai_step());
        assert_eq!(snapshot(&app), before);
        assert!(app.pending_confirm.is_some());

        // A retreating party's automatic driver is paused as well while it is open.
        let mut app = retreating_app();
        app.pending_confirm = Some(PendingConfirm { action: ConfirmAction::Retreat, actor: 0, round: 1, state_hash: 0 });
        let before = snapshot(&app);
        assert!(!app.ai_step());
        assert_eq!(snapshot(&app), before);
        app.pending_confirm = None;
        assert!(app.ai_step(), "control: the driver acts once nothing is pending");
    }

    #[test]
    fn opening_and_cancelling_either_dialog_does_not_touch_battle_state_or_rng() {
        for retreat in [false, true] {
            let mut app = App::new(5);
            app.run(Command::Wait { unit: 0 });
            let before = snapshot(&app);
            let units = app.state.units.clone();
            if retreat {
                open_retreat(&mut app);
            } else {
                open_dialog(&mut app);
            }
            assert_eq!(snapshot(&app), before);
            app.draw(); // no window/GPU: only builds the scene
            assert_eq!(snapshot(&app), before);
            press(&mut app, named(NamedKey::Escape));
            assert_eq!(snapshot(&app), before);
            assert_eq!(app.state.units, units);
        }
    }

    // --- Fleeing player units ------------------------------------------------

    /// Stock scenario with the active P1 Fleeing (not on a map edge).
    fn fleeing_app() -> App {
        let mut app = App::new(1);
        app.state.units[0].morale = Morale::Fleeing;
        app
    }

    #[test]
    fn fleeing_player_offers_no_human_actions_or_end_round_confirmation() {
        let mut app = fleeing_app();
        assert_eq!(app.state.active_unit().map(|u| u.id), Some(0));
        assert_eq!(app.human_unit(), None);
        assert_eq!(app.order_actor(), None);
        let before = snapshot(&app);

        for key in ["x", "X", "e", "w"] {
            app.character_key(key);
            assert_eq!(snapshot(&app), before, "key {key}");
        }
        assert!(app.pending_confirm.is_none());
        // Map clicks (reachable hex, enemy, the launch button) do nothing, not even an error.
        let now = Instant::now();
        for pt in [layout::hex_to_pixel(app.state.reachable(0)[0].0), layout::hex_to_pixel(app.state.units[3].pos), center(layout::END_ROUND_BUTTON)] {
            click_at(&mut app, pt, now);
            assert_eq!(snapshot(&app), before);
            assert!(app.error.is_none() && app.pending_confirm.is_none());
        }
    }

    #[test]
    fn fleeing_player_turns_are_driven_automatically_and_log_the_escape() {
        let mut app = fleeing_app();
        app.state.units[0].pos = bb_rules::hex(-2, 2); // two steps from the edge
        assert!(app.ai_step(), "the automatic driver acts for a fleeing player");
        assert_eq!(app.log.last().map(String::as_str), Some("P1 moves 1"));
        assert_eq!(app.state.active_unit().map(|u| u.id), Some(0), "still P1's turn until escape or budget");
        // Drive to the edge; the escape is logged and control returns to the next player.
        let mut guard = 0;
        while app.state.units[0].is_present() {
            guard += 1;
            assert!(guard < 10);
            assert!(app.ai_step());
        }
        assert!(app.state.units[0].alive && app.state.units[0].fled);
        assert!(app.log.iter().any(|l| l == "P1 escapes"), "{:?}", app.log);
        assert!(app.log.iter().all(|l| l.chars().count() <= 31));
        assert_eq!(app.human_unit(), Some(1));
        assert!(!app.ai_step(), "a human turn is not driven");
    }

    #[test]
    fn retreat_outcome_is_logged_and_stops_the_driver() {
        let mut app = App::new(1);
        for id in [1, 2] {
            app.state.units[id].alive = false;
        }
        app.state.queue = vec![0, 3, 4, 5];
        app.state.units[0].morale = Morale::Fleeing;
        app.state.units[0].pos = bb_rules::hex(-4, 3);
        assert!(app.ai_step());
        assert_eq!(app.log, vec!["P1 escapes".to_string(), "Retreat Escape".to_string()]);
        assert!(!app.ai_step());
    }

    #[test]
    fn confirmation_excludes_fleeing_units_and_does_not_forfeit_their_turns() {
        let mut app = App::new(1);
        app.state.units[2].morale = Morale::Fleeing;
        assert_eq!(render::queued_player_units(&app.state), 2, "P3 is fleeing and not counted");
        open_dialog(&mut app);
        click_at(&mut app, center(layout::CONFIRM_BUTTON), Instant::now());
        assert!(app.state.units[1].skip_turn && !app.state.units[2].skip_turn);
        // P2 was passed over; the fleeing P3 now acts, automatically.
        assert_eq!(app.state.active_unit().map(|u| u.id), Some(2));
        assert_eq!(app.human_unit(), None);
        assert!(app.ai_step());
        assert_eq!(app.state.units[2].ap, 7);
    }

    // --- Party retreat ----------------------------------------------------------------

    /// Stock scenario after the party order from P1 (P2 stands on a map edge).
    fn retreating_app() -> App {
        let mut app = App::new(1);
        app.run(Command::Retreat { unit: 0 });
        app
    }

    #[test]
    fn retreat_order_is_logged_briefly_before_the_immediate_edge_exits() {
        let app = retreating_app();
        assert_eq!(app.log, vec!["retreat ordered".to_string(), "P2 escapes".to_string()]);
        assert!(app.log.iter().all(|l| l.chars().count() <= 31));
        assert!(app.error.is_none());
    }

    #[test]
    fn retreating_players_offer_no_human_actions_or_end_round_confirmation() {
        let mut app = retreating_app();
        assert_eq!(app.state.active_unit().map(|u| u.id), Some(0), "the interior issuer keeps its turn");
        assert_eq!(app.human_unit(), None);
        assert_eq!(app.order_actor(), None);
        let before = snapshot(&app);
        for key in ["x", "X", "e", "w"] {
            app.character_key(key);
            assert_eq!(snapshot(&app), before, "key {key}");
        }
        assert!(app.pending_confirm.is_none());
        let now = Instant::now();
        for pt in [layout::hex_to_pixel(app.state.reachable(0)[0].0), layout::hex_to_pixel(app.state.units[3].pos), center(layout::END_ROUND_BUTTON)] {
            click_at(&mut app, pt, now);
            assert_eq!(snapshot(&app), before);
            assert!(app.error.is_none() && app.pending_confirm.is_none());
        }
    }

    #[test]
    fn withdrawing_player_turns_are_driven_automatically_at_the_ai_cadence() {
        let mut app = retreating_app();
        app.state.units[0].pos = bb_rules::hex(-2, 2); // two steps from the edge
        assert!(app.ai_step(), "the automatic driver acts for a retreating player");
        assert_eq!(app.log.last().map(String::as_str), Some("P1 moves 1"));
        let mut guard = 0;
        while app.state.units[0].is_present() {
            guard += 1;
            assert!(guard < 10);
            assert!(app.ai_step());
        }
        assert!(app.log.iter().any(|l| l == "P1 escapes"), "{:?}", app.log);
        // P3 is retreating as well, so no turn ever returns to the human.
        let mut guard = 0;
        while app.state.outcome().is_none() {
            guard += 1;
            assert!(guard < 300);
            assert_eq!(app.human_unit(), None);
            assert!(app.ai_step());
        }
        assert!(!app.ai_step());
    }

    #[test]
    fn confirmation_excludes_retreating_units_and_does_not_forfeit_their_turns() {
        let mut app = App::new(1);
        app.state.units[2].retreating = true;
        assert_eq!(render::queued_player_units(&app.state), 2, "P3 is retreating and not counted");
        open_dialog(&mut app);
        click_at(&mut app, center(layout::CONFIRM_BUTTON), Instant::now());
        assert!(app.state.units[1].skip_turn && !app.state.units[2].skip_turn);
        assert_eq!(app.state.active_unit().map(|u| u.id), Some(2));
        assert_eq!(app.human_unit(), None);
        assert!(app.ai_step());
        assert_eq!(app.state.units[2].ap, 7);
    }

    #[test]
    fn restart_clears_retreat_intent() {
        let mut app = retreating_app();
        assert!(app.state.units.iter().any(|u| u.retreating));
        app.restart(1);
        assert!(app.state.units.iter().all(|u| !u.retreating));
        assert_eq!(app.human_unit(), Some(0));
    }

    // --- Self-rally at a fleeing unit's turn start --------------------------------

    /// Stock scenario, P2 Fleeing off the map edge with the given Resolve; P1 is
    /// active and ending its turn starts P2's.
    fn self_rally_app(resolve: u32) -> App {
        let mut app = App::new(1);
        app.state.units[1].morale = Morale::Fleeing;
        app.state.units[1].pos = bb_rules::hex(-1, 2); // two steps from the edge
        app.state.units[1].stats.resolve = resolve;
        app
    }

    #[test]
    fn successful_self_rally_logs_recovery_and_hands_control_to_the_player() {
        let mut app = self_rally_app(100);
        app.run(Command::EndTurn { unit: 0 });
        assert_eq!(app.state.units[1].morale, Morale::Breaking);
        let line = app.log.last().unwrap();
        assert!(line.starts_with("P2 rallies r"), "{line}");
        assert!(line.chars().count() <= 31, "{line}");
        assert_eq!(app.human_unit(), Some(1));
        assert!(!app.ai_step(), "the player is in control, nothing is driven");
        assert!(app.log.iter().all(|l| !l.contains("moves")));
    }

    #[test]
    fn failed_self_rally_logs_continued_flight_and_the_driver_keeps_fleeing() {
        let mut app = self_rally_app(0);
        app.run(Command::EndTurn { unit: 0 });
        assert_eq!(app.state.units[1].morale, Morale::Fleeing);
        let line = app.log.last().unwrap();
        assert!(line.starts_with("P2 keeps fleeing r"), "{line}");
        assert!(line.chars().count() <= 31, "{line}");
        assert_eq!(app.human_unit(), None);
        assert!(app.ai_step());
        assert_eq!(app.log.last().map(String::as_str), Some("P2 moves 1"));
        assert!(app.log.iter().filter(|l| l.contains("fleeing") || l.contains("rallies")).count() == 1);
    }

    // --- Adrenaline loadout, button and shortcut ------------------------------------

    fn loadout_app(seed: u64, terrain: TerrainPreset) -> App {
        App::with_loadout(seed, terrain, true)
    }

    /// Number of pending markers and whether the skill button is drawn enabled in the frame.
    fn frame_markers(app: &App) -> usize {
        app.frame().iter().filter(|v| v.color == render::PENDING_MARKER).count() / 6
    }

    fn frame_button_enabled(app: &App) -> bool {
        match render::skill_fill(&app.frame(), layout::ADRENALINE_BUTTON) {
            Some(c) if c == render::SKILL_ENABLED => true,
            Some(c) if c == render::SKILL_DISABLED => false,
            other => panic!("exactly one button state is drawn: {other:?}"),
        }
    }

    #[test]
    fn loadout_selects_player_only_capability_and_the_default_app_is_unchanged() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            let plain = App::with_terrain(7, terrain);
            assert!(!plain.adrenaline);
            assert_eq!(plain.state.state_hash(), scenario::battle(7, terrain).state_hash());
            assert!(plain.state.units.iter().all(|u| !u.has_adrenaline));
            let app = loadout_app(7, terrain);
            assert!(app.adrenaline);
            assert_eq!(app.state.state_hash(), scenario::adrenaline_battle(7, terrain).state_hash());
            assert_eq!(app.state.rng, plain.state.rng);
            assert!(app.state.units.iter().all(|u| u.has_adrenaline == (u.side == Side::Player) && !u.adrenaline_pending));
            assert_eq!(app.height_overlay, terrain == TerrainPreset::Hills, "overlay default unchanged");
        }
    }

    #[test]
    fn button_and_shortcut_submit_exactly_the_direct_command() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            let mut direct = loadout_app(5, terrain);
            let active = direct.state.active_unit().unwrap().id;
            direct.run(Command::Adrenaline { unit: active });
            assert_eq!(direct.log, vec!["P1 adrenaline: next round".to_string()]);

            let mut by_key = loadout_app(5, terrain);
            assert!(!press(&mut by_key, ch("1")));
            let mut by_click = loadout_app(5, terrain);
            click_at(&mut by_click, center(layout::ADRENALINE_BUTTON), Instant::now());
            for app in [&by_key, &by_click] {
                assert_eq!(app.state.state_hash(), direct.state.state_hash());
                assert_eq!(app.state.rng, direct.state.rng);
                assert_eq!(app.state.queue, direct.state.queue);
                assert_eq!(app.log, direct.log, "exactly one command, one log line");
                assert!(app.error.is_none());
                assert_eq!(app.state.units[0].ap, 8);
                assert!(app.state.units[0].adrenaline_pending);
            }
            // Corners of the shared rectangle click too.
            let r = layout::ADRENALINE_BUTTON;
            let mut corner = loadout_app(5, terrain);
            click_at(&mut corner, (r.x + 0.5, r.y + 0.5), Instant::now());
            assert_eq!(corner.state.state_hash(), direct.state.state_hash());
            let mut outside = loadout_app(5, terrain);
            click_at(&mut outside, (r.right() + 0.5, r.y + 0.5), Instant::now());
            assert_ne!(outside.state.state_hash(), direct.state.state_hash());
        }
    }

    #[test]
    fn repeated_input_does_not_double_submit() {
        assert!(key_press_accepted(ElementState::Pressed, false));
        assert!(!key_press_accepted(ElementState::Pressed, true), "auto-repeat is dropped before key()");
        assert!(!key_press_accepted(ElementState::Released, false));
        let mut app = loadout_app(1, TerrainPreset::Flat);
        press(&mut app, ch("1"));
        let after = snapshot(&app);
        let log = app.log.clone();
        press(&mut app, ch("1"));
        click_at(&mut app, center(layout::ADRENALINE_BUTTON), Instant::now());
        click_at(&mut app, center(layout::ADRENALINE_BUTTON), Instant::now());
        assert_eq!(snapshot(&app), after);
        assert_eq!(app.log, log);
        assert!(app.error.is_none(), "a disabled activation is a silent no-op");
        assert_eq!(app.state.units[0].ap, 8);
    }

    #[test]
    fn disabled_states_are_silent_no_ops_that_never_reach_the_map() {
        let button = center(layout::ADRENALINE_BUTTON);
        type Setup = fn(&mut App);
        let cases: [(&str, Setup); 11] = [
            ("no capability", |a| a.state.units[0].has_adrenaline = false),
            ("pending", |a| a.state.units[0].adrenaline_pending = true),
            ("no AP", |a| a.state.units[0].ap = 0),
            ("fatigue cap", |a| a.state.units[0].fatigue = 81),
            ("fatigue overflow", |a| {
                a.state.units[0].stats.fatigue_max = u32::MAX;
                a.state.units[0].fatigue = u32::MAX;
            }),
            ("enemy turn", |a| a.state.queue = vec![3, 0]),
            ("battle over", |a| a.state.outcome = Some(bb_rules::Outcome::Victory)),
            ("fleeing", |a| a.state.units[0].morale = Morale::Fleeing),
            ("retreating", |a| a.state.units[0].retreating = true),
            ("dead active unit", |a| a.state.units[0].alive = false),
            ("escaped active unit", |a| a.state.units[0].fled = true),
        ];
        for (what, setup) in cases {
            let mut app = loadout_app(1, TerrainPreset::Flat);
            setup(&mut app);
            assert_eq!(app.adrenaline_actor(), None, "{what}");
            assert!(!frame_button_enabled(&app), "{what}");
            let before = snapshot(&app);
            let units = app.state.units.clone();
            press(&mut app, ch("1"));
            click_at(&mut app, button, Instant::now());
            assert_eq!(snapshot(&app), before, "{what}");
            assert_eq!(app.state.units, units, "{what}");
            assert!(app.error.is_none() && app.pending_confirm.is_none() && app.log.is_empty(), "{what}");
        }
        // Without the loadout the button is always disabled.
        let mut app = App::new(1);
        let before = snapshot(&app);
        press(&mut app, ch("1"));
        click_at(&mut app, button, Instant::now());
        assert_eq!(snapshot(&app), before);
        assert!(app.error.is_none() && app.log.is_empty() && !frame_button_enabled(&app));
        // Control: the same press one pixel left of the strip's button is a map click, which
        // reports an invalid move, proving the button press above was not routed to the map.
        let mut control = loadout_app(1, TerrainPreset::Flat);
        let r = layout::ADRENALINE_BUTTON;
        click_at(&mut control, (r.x - 2.0, r.y + 10.0), Instant::now());
        assert!(control.error.is_some());
    }

    #[test]
    fn modals_consume_the_button_and_shortcut() {
        for retreat in [false, true] {
            let mut app = loadout_app(1, TerrainPreset::Flat);
            if retreat {
                open_retreat(&mut app);
            } else {
                open_dialog(&mut app);
            }
            let (pending, before) = (app.pending_confirm, snapshot(&app));
            press(&mut app, ch("1"));
            app.character_key("1");
            click_at(&mut app, center(layout::ADRENALINE_BUTTON), Instant::now());
            assert_eq!(app.pending_confirm, pending, "the dialog stays open");
            assert_eq!(snapshot(&app), before);
            assert!(app.error.is_none() && app.log.is_empty());
            assert!(app.state.units.iter().all(|u| !u.adrenaline_pending));
            // Cancel, then the guard swallows the button until it expires.
            let t0 = Instant::now();
            click_at(&mut app, center(layout::CANCEL_BUTTON), t0);
            click_at(&mut app, center(layout::ADRENALINE_BUTTON), t0 + MODAL_CLICK_GUARD - Duration::from_millis(1));
            assert!(!app.state.units[0].adrenaline_pending && app.error.is_none());
            click_at(&mut app, center(layout::ADRENALINE_BUTTON), t0 + MODAL_CLICK_GUARD);
            assert!(app.state.units[0].adrenaline_pending);
            assert_eq!(app.log, vec!["P1 adrenaline: next round".to_string()]);
        }
        // A button click can neither open a dialog nor confirm one.
        let mut app = loadout_app(1, TerrainPreset::Flat);
        click_at(&mut app, center(layout::ADRENALINE_BUTTON), Instant::now());
        assert!(app.pending_confirm.is_none());
        assert!(app.state.units[0].adrenaline_pending);
    }

    #[test]
    fn loadout_and_overlay_preferences_survive_restart_and_new_seed() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            let mut app = loadout_app(17, terrain);
            app.character_key("z");
            app.character_key("h");
            let (zoc, height) = (app.control_overlay, app.height_overlay);
            press(&mut app, ch("1"));
            assert!(app.state.units[0].adrenaline_pending);
            open_dialog(&mut app);
            app.character_key("r"); // ignored while the modal is open
            assert!(app.pending_confirm.is_some());
            press(&mut app, named(NamedKey::Escape));
            app.restart(17);
            assert!(app.pending_confirm.is_none() && app.click_guard_until.is_none());
            for (key, seed) in [("r", 17), ("n", 18), ("n", 19), ("r", 19)] {
                press(&mut app, ch("1"));
                app.character_key(key);
                assert_eq!((app.seed, app.terrain, app.adrenaline), (seed, terrain, true));
                assert_eq!((app.control_overlay, app.height_overlay), (zoc, height));
                assert_eq!(app.state.state_hash(), scenario::adrenaline_battle(seed, terrain).state_hash());
                assert_eq!(app.state.rng, scenario::adrenaline_battle(seed, terrain).rng);
                assert!(app.state.units.iter().all(|u| !u.adrenaline_pending));
                assert!(app.log.is_empty() && app.error.is_none());
                assert!(frame_button_enabled(&app) && frame_markers(&app) == 0);
            }
        }
        // Without the flag R/N keep rebuilding the default scenario.
        let mut app = App::with_terrain(4, TerrainPreset::Hills);
        app.character_key("n");
        assert!(!app.adrenaline);
        assert_eq!(app.state.state_hash(), scenario::battle(5, TerrainPreset::Hills).state_hash());
    }

    #[test]
    fn button_and_markers_stay_synchronized_through_use_wait_rounds_retreat_and_restart() {
        let mut app = loadout_app(3, TerrainPreset::Flat);
        assert!(frame_button_enabled(&app) && frame_markers(&app) == 0);
        press(&mut app, ch("1"));
        assert!(!frame_button_enabled(&app), "pending disables it");
        assert_eq!(frame_markers(&app), 1);
        // Wait returns P1 to control later in the round; the effect is still pending.
        press(&mut app, ch("w"));
        assert_eq!(frame_markers(&app), 1);
        assert_eq!(app.state.active_unit().map(|u| u.id), Some(1));
        assert!(frame_button_enabled(&app), "P2 is a fresh capable unit");
        press(&mut app, ch("1"));
        assert_eq!(frame_markers(&app), 2);
        // Finish the round by End Round: pending is consumed on the new round.
        press(&mut app, ch("x"));
        press(&mut app, named(NamedKey::Enter));
        for _ in 0..40 {
            if app.state.round > 1 || app.state.outcome().is_some() {
                break;
            }
            app.ai_step();
        }
        assert!(app.state.round > 1, "the next round started");
        assert!(app.state.units.iter().all(|u| !u.adrenaline_pending));
        assert_eq!(frame_markers(&app), 0);
        assert_eq!(app.adrenaline_actor().is_some(), frame_button_enabled(&app));

        // A retreating active unit is automatic: no human actions, button disabled, marker kept.
        let mut app = loadout_app(3, TerrainPreset::Flat);
        press(&mut app, ch("1"));
        press(&mut app, ch("v"));
        press(&mut app, named(NamedKey::Enter));
        assert!(app.state.units.iter().filter(|u| u.side == Side::Player).all(|u| u.retreating || !u.is_present()));
        assert_eq!(app.adrenaline_actor(), None);
        assert!(!frame_button_enabled(&app));
        let before = snapshot(&app);
        press(&mut app, ch("1"));
        click_at(&mut app, center(layout::ADRENALINE_BUTTON), Instant::now() + MODAL_CLICK_GUARD);
        assert_eq!(snapshot(&app), before);
        assert_eq!(frame_markers(&app), app.state.units.iter().filter(|u| u.is_present() && u.adrenaline_pending).count());

        // Restart clears everything and re-enables the button.
        app.character_key("r");
        assert_eq!(frame_markers(&app), 0);
        assert!(frame_button_enabled(&app));
    }

    #[test]
    fn button_enabled_state_agrees_with_the_app_actor_in_every_phase() {
        let mut app = loadout_app(2, TerrainPreset::Flat);
        for step in 0..60 {
            if app.state.outcome().is_some() {
                break;
            }
            let reach: Vec<_> = Vec::new();
            let sc = render::Scene {
                state: &app.state,
                selected: app.human_unit(),
                hover: None,
                reach: &reach,
                log: &[],
                error: None,
                seed: app.seed,
                control_overlay: false,
                height_overlay: false,
                confirm: None,
                targeting: None,
            };
            assert_eq!(render::adrenaline_enabled(&sc), app.adrenaline_actor().is_some(), "step {step}");
            assert_eq!(frame_button_enabled(&app), app.adrenaline_actor().is_some(), "step {step}");
            if app.human_unit().is_some() {
                if step % 2 == 0 {
                    app.character_key("1");
                }
                app.character_key("e");
            } else {
                app.ai_step();
            }
        }
    }

    #[test]
    fn drawing_the_adrenaline_ui_changes_nothing() {
        let mut app = loadout_app(6, TerrainPreset::Hills);
        press(&mut app, ch("1"));
        let before = snapshot(&app);
        let units = app.state.units.clone();
        app.draw();
        let _ = app.frame();
        assert_eq!(snapshot(&app), before);
        assert_eq!(app.state.units, units);
    }

    // --- Rotation loadout, button, shortcut and ally targeting ---------------------------

    fn rot_app() -> App {
        App::with_flags(1, TerrainPreset::Flat, false, true)
    }

    fn px(app: &App, unit: usize) -> (f32, f32) {
        layout::hex_to_pixel(app.state.units[unit].pos)
    }

    fn click_unit(app: &mut App, unit: usize, now: Instant) {
        let pt = px(app, unit);
        click_at(app, pt, now);
    }

    /// Opens targeting through the shortcut and checks the captured intent.
    fn open_targeting(app: &mut App) {
        press(app, ch("2"));
        let intent = app.targeting.expect("targeting is open");
        assert_eq!((intent.actor, intent.round, intent.state_hash), (app.human_unit().unwrap(), app.state.round, app.state.state_hash()));
    }

    /// No log line and no feedback: for "nothing happened" checks.
    fn quiet(app: &App) -> bool {
        app.log.is_empty() && app.error.is_none()
    }

    /// Whether the frame draws the Rotation targeting display (SWAP hex fills).
    fn frame_targeting(app: &App) -> bool {
        app.frame().iter().any(|v| v.color == render::SWAP_FILL || v.color == render::SWAP_HOVER)
    }

    fn rotation_look(app: &App) -> Option<[f32; 4]> {
        render::skill_fill(&app.frame(), layout::ROTATION_BUTTON)
    }

    #[test]
    fn rotation_loadout_selects_player_only_capability_and_the_default_app_is_unchanged() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            for (adrenaline, rotation) in [(false, false), (true, false), (false, true), (true, true)] {
                let app = App::with_flags(7, terrain, adrenaline, rotation);
                assert_eq!((app.adrenaline, app.rotation), (adrenaline, rotation));
                assert_eq!(app.state.state_hash(), scenario::launch_battle_with(7, terrain, adrenaline, rotation).state_hash());
                assert_eq!(app.state.rng, BattleState::new(7).rng);
                for u in &app.state.units {
                    assert_eq!((u.has_adrenaline, u.has_rotation), (adrenaline && u.side == Side::Player, rotation && u.side == Side::Player));
                }
                assert_eq!(app.height_overlay, terrain == TerrainPreset::Hills, "overlay default unchanged");
                assert!(app.targeting.is_none());
            }
            // The existing constructors do not know the flag.
            assert!(!App::with_terrain(7, terrain).rotation);
            assert!(!loadout_app(7, terrain).rotation);
            assert_eq!(loadout_app(7, terrain).state.state_hash(), scenario::adrenaline_battle(7, terrain).state_hash());
        }
        assert_eq!(rot_app().state.state_hash(), scenario::rotation_battle(1, TerrainPreset::Flat).state_hash());
    }

    #[test]
    fn button_and_shortcut_open_targeting_without_submitting_a_command() {
        for how in 0..4 {
            let mut app = rot_app();
            let (before, units) = (snapshot(&app), app.state.units.clone());
            let expected = PendingTargeting { action: TargetAction::Rotation, actor: 0, round: 1, state_hash: app.state.state_hash() };
            match how {
                0 => assert!(!press(&mut app, ch("2"))),
                1 => app.character_key("2"),
                2 => click_at(&mut app, center(layout::ROTATION_BUTTON), Instant::now()),
                _ => {
                    let r = layout::ROTATION_BUTTON;
                    click_at(&mut app, (r.x + 0.5, r.y + 0.5), Instant::now());
                }
            }
            assert_eq!(app.targeting, Some(expected), "how {how}");
            assert_eq!(snapshot(&app), before, "no command: state, RNG, queue and log are untouched");
            assert_eq!(app.state.units, units);
            assert!(quiet(&app) && app.pending_confirm.is_none() && app.click_guard_until.is_none());
            assert!(frame_targeting(&app));
            assert_eq!(rotation_look(&app), Some(render::SKILL_ACTIVE));
        }
        // Outside the rectangle it is an ordinary map click.
        let mut app = rot_app();
        let r = layout::ROTATION_BUTTON;
        click_at(&mut app, (r.right() + 0.5, r.y + 0.5), Instant::now());
        assert!(app.targeting.is_none() && app.error.is_some());
    }

    #[test]
    fn disabled_rotation_is_a_silent_no_op_that_never_reaches_the_map() {
        type Setup = fn(&mut App);
        let cases: [(&str, Setup); 13] = [
            ("no AP", |a| a.state.units[0].ap = 2),
            ("fatigue cap", |a| a.state.units[0].fatigue = 76),
            ("fatigue overflow", |a| {
                a.state.units[0].stats.fatigue_max = u32::MAX;
                a.state.units[0].fatigue = u32::MAX;
            }),
            ("no capability", |a| a.state.units[0].has_rotation = false),
            ("no adjacent ally", |a| a.state.units[0].pos = bb_rules::hex(0, 3)),
            ("enemy turn", |a| a.state.queue = vec![3, 0]),
            ("battle over", |a| a.state.outcome = Some(bb_rules::Outcome::Victory)),
            ("fleeing", |a| a.state.units[0].morale = Morale::Fleeing),
            ("retreating", |a| a.state.units[0].retreating = true),
            ("dead active unit", |a| a.state.units[0].alive = false),
            ("escaped active unit", |a| a.state.units[0].fled = true),
            ("allies unavailable", |a| {
                a.state.units[1].alive = false;
                a.state.units[2].morale = Morale::Fleeing;
            }),
            ("all adjacent allies stunned", |a| {
                a.state.apply_stun(1).unwrap();
                a.state.apply_stun(2).unwrap();
            }),
        ];
        for (what, setup) in cases {
            let mut app = rot_app();
            setup(&mut app);
            assert_eq!(app.rotation_actor(), None, "{what}");
            assert_eq!(rotation_look(&app), Some(render::SKILL_DISABLED), "{what}");
            let (before, units) = (snapshot(&app), app.state.units.clone());
            press(&mut app, ch("2"));
            click_at(&mut app, center(layout::ROTATION_BUTTON), Instant::now());
            assert!(app.targeting.is_none(), "{what}");
            assert_eq!(snapshot(&app), before, "{what}");
            assert_eq!(app.state.units, units, "{what}");
            assert!(quiet(&app) && app.pending_confirm.is_none(), "{what}");
        }
        // Without the flag the button is always disabled.
        let mut app = App::new(1);
        let before = snapshot(&app);
        press(&mut app, ch("2"));
        click_at(&mut app, center(layout::ROTATION_BUTTON), Instant::now());
        assert_eq!(snapshot(&app), before);
        assert!(app.targeting.is_none() && quiet(&app));
        assert_eq!(rotation_look(&app), Some(render::SKILL_DISABLED));
        // A modal consumes the activation.
        for retreat in [false, true] {
            let mut app = rot_app();
            if retreat {
                open_retreat(&mut app);
            } else {
                open_dialog(&mut app);
            }
            let (pending, before) = (app.pending_confirm, snapshot(&app));
            press(&mut app, ch("2"));
            app.character_key("2");
            click_at(&mut app, center(layout::ROTATION_BUTTON), Instant::now() + Duration::from_secs(5));
            assert!(app.targeting.is_none());
            assert_eq!(app.pending_confirm, pending);
            assert_eq!(snapshot(&app), before);
            assert!(quiet(&app));
        }
    }

    #[test]
    fn selecting_a_legal_ally_submits_exactly_the_direct_rotation_command() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            for target in [1usize, 2] {
                let mut direct = App::with_flags(5, terrain, false, true);
                direct.run(Command::Rotation { unit: 0, target: target as u32 });
                assert_eq!(direct.log.first().map(String::as_str), Some(format!("P1 swaps with P{}", target + 1).as_str()));

                let mut app = App::with_flags(5, terrain, false, true);
                assert_eq!(app.state.legal_rotation_targets(0), vec![1, 2]);
                open_targeting(&mut app);
                let t0 = Instant::now();
                click_unit(&mut app, target, t0);
                assert!(app.targeting.is_none(), "targeting is cleared");
                assert_eq!(app.state.state_hash(), direct.state.state_hash());
                assert_eq!(app.state.rng, direct.state.rng);
                assert_eq!(app.state.queue, direct.state.queue);
                assert_eq!(app.state.units, direct.state.units);
                assert_eq!(app.log, direct.log, "exactly one command, same log");
                assert!(app.error.is_none());
                assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD));
                assert_eq!(app.state.units[0].ap, 9 - 3);
                assert_eq!(app.state.units[0].fatigue, 25);
                assert_eq!(app.human_unit(), Some(0), "the actor keeps its turn");
            }
        }
    }

    #[test]
    fn stunned_ally_is_an_invalid_target_while_an_unstunned_ally_stays_available() {
        let mut app = rot_app();
        app.state.apply_stun(1).unwrap();
        assert_eq!(app.state.legal_rotation_targets(0), vec![2]);
        open_targeting(&mut app);
        assert_eq!(rotation_look(&app), Some(render::SKILL_ACTIVE));
        assert!(frame_targeting(&app), "P3 remains marked for a swap");

        let before = snapshot(&app);
        let units = app.state.units.clone();
        click_unit(&mut app, 1, Instant::now() + Duration::from_secs(1));
        assert_eq!(snapshot(&app), before, "the invalid target submits no command");
        assert_eq!(app.state.units, units);
        assert!(app.targeting.is_some(), "invalid clicks keep targeting open");
        assert_eq!(app.log, Vec::<String>::new(), "the click does not fall through to another command");
        assert_eq!(app.error.as_ref().map(|e| e.0.as_str()), Some("NOT A SWAP TARGET: CLICK A MARKED ALLY OR PRESS ESC"));

        let mut app = rot_app();
        open_targeting(&mut app);
        app.state.apply_stun(1).unwrap(); // state changed after the intent was captured
        let before = snapshot(&app);
        click_unit(&mut app, 1, Instant::now() + Duration::from_secs(1));
        assert_eq!(snapshot(&app), before);
        assert!(app.targeting.is_none());
        assert_eq!(app.error.as_ref().map(|e| e.0.as_str()), Some("ROTATION DISCARDED: BATTLE CHANGED"));
    }

    #[test]
    fn invalid_clicks_keep_targeting_and_never_fall_through_to_move_or_attack() {
        let mut app = rot_app();
        open_targeting(&mut app);
        let intent = app.targeting;
        let (before, units) = (snapshot(&app), app.state.units.clone());
        let reachable = layout::hex_to_pixel(app.state.reachable(0)[0].0);
        let far = layout::hex_to_pixel(bb_rules::hex(0, 0));
        // An enemy placed next to P1 would be an Attack target without targeting.
        let now = Instant::now() + Duration::from_secs(10);
        for (what, pt) in [
            ("reachable empty hex", reachable),
            ("unreachable empty hex", far),
            ("enemy", px(&app, 3)),
            ("the actor itself", px(&app, 0)),
            ("off the map", (5.0, 700.0)),
            ("log area", (layout::PANEL_X + 5.0, 560.0)),
        ] {
            click_at(&mut app, pt, now);
            assert_eq!(app.targeting, intent, "{what}: targeting stays open");
            assert_eq!(snapshot(&app), before, "{what}: no command");
            assert_eq!(app.state.units, units, "{what}");
            let (msg, _) = app.error.take().unwrap_or_else(|| panic!("{what}: concise selection feedback"));
            assert!(msg.starts_with("NOT A SWAP TARGET"), "{what}: {msg}");
            assert!(msg.chars().count() <= 70 && msg.is_ascii());
        }
        // An ally that is not a legal target (out of reach) is rejected as well.
        let mut app = rot_app();
        app.state.units[2].pos = bb_rules::hex(-3, 0);
        assert_eq!(app.state.legal_rotation_targets(0), vec![1]);
        open_targeting(&mut app);
        let before = snapshot(&app);
        click_unit(&mut app, 2, now);
        assert!(app.targeting.is_some() && app.error.is_some());
        assert_eq!(snapshot(&app), before);
        // The same click without targeting is the ordinary map click (a Move error here),
        // proving the invalid click above was handled by targeting, not by the map.
        let mut control = rot_app();
        click_at(&mut control, far, now);
        assert!(control.error.as_ref().is_some_and(|(m, _)| !m.starts_with("NOT A SWAP")));
        // An enemy adjacent to the actor is still not selectable.
        let mut app = rot_app();
        app.state.units[3].pos = bb_rules::hex(-2, 3);
        open_targeting(&mut app);
        let before = snapshot(&app);
        click_unit(&mut app, 3, now);
        assert_eq!(snapshot(&app), before, "no Attack");
        assert!(app.targeting.is_some());
    }

    #[test]
    fn a_selection_dispatches_exactly_once_even_with_repeated_clicks() {
        let mut app = rot_app();
        open_targeting(&mut app);
        let t0 = Instant::now();
        click_unit(&mut app, 1, t0);
        let after = snapshot(&app);
        let swaps = |a: &App| a.log.iter().filter(|l| l.contains("swaps")).count();
        assert_eq!(swaps(&app), 1);
        // The swapped ally now stands where P1 stood: repeated clicks inside the guard do nothing.
        let inside = t0 + MODAL_CLICK_GUARD - Duration::from_millis(1);
        for _ in 0..3 {
            click_unit(&mut app, 1, inside);
            click_unit(&mut app, 0, inside);
        }
        assert_eq!(snapshot(&app), after);
        assert!(app.error.is_none());
        // After the guard a click is an ordinary map click, never another Rotation.
        click_unit(&mut app, 1, t0 + MODAL_CLICK_GUARD);
        assert_eq!(swaps(&app), 1);
        // Keyboard repeats are dropped before key(), so a held 2 does not toggle.
        assert!(!key_press_accepted(ElementState::Pressed, true));
        assert!(app.targeting.is_none());
    }

    #[test]
    fn escape_two_and_the_button_cancel_targeting_without_quitting_or_a_command() {
        for how in 0..3 {
            let mut app = rot_app();
            let before = snapshot(&app);
            open_targeting(&mut app);
            let t0 = Instant::now();
            match how {
                0 => assert!(!app.key(&named(NamedKey::Escape), t0), "Escape cancels instead of quitting"),
                1 => assert!(!app.key(&ch("2"), t0)),
                _ => click_at(&mut app, center(layout::ROTATION_BUTTON), t0),
            }
            assert!(app.targeting.is_none(), "how {how}");
            assert_eq!(snapshot(&app), before);
            assert!(quiet(&app));
            assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD), "the cancel arms the click guard");
            assert_eq!(rotation_look(&app), Some(render::SKILL_ENABLED));
            // Outside targeting and confirmation Escape still quits.
            assert!(press(&mut app, named(NamedKey::Escape)));
            // The mode can be reopened.
            app.click_guard_until = None;
            open_targeting(&mut app);
        }
    }

    #[test]
    fn confirmation_modals_take_priority_and_opening_one_clears_targeting() {
        for (retreat, by_button) in [(false, false), (true, false), (false, true), (true, true)] {
            let mut app = rot_app();
            open_targeting(&mut app);
            let before = snapshot(&app);
            match (retreat, by_button) {
                (false, false) => {
                    press(&mut app, ch("x"));
                }
                (true, false) => {
                    press(&mut app, ch("v"));
                }
                (false, true) => click_at(&mut app, center(layout::END_ROUND_BUTTON), Instant::now()),
                (true, true) => click_at(&mut app, center(layout::RETREAT_BUTTON), Instant::now()),
            }
            assert!(app.targeting.is_none(), "opening a confirmation clears targeting");
            let pending = app.pending_confirm.expect("the dialog opened");
            assert_eq!(pending.action, if retreat { ConfirmAction::Retreat } else { ConfirmAction::EndRound });
            assert_eq!(snapshot(&app), before);
            // Rotation inputs are consumed by the modal: nothing starts, nothing cancels the dialog.
            press(&mut app, ch("2"));
            click_at(&mut app, center(layout::ROTATION_BUTTON), Instant::now() + Duration::from_secs(5));
            assert!(app.targeting.is_none());
            assert_eq!(app.pending_confirm, Some(pending));
            assert_eq!(snapshot(&app), before);
            // Escape closes the dialog (not the app), and the guard then swallows the button.
            let t0 = Instant::now();
            assert!(!app.key(&named(NamedKey::Escape), t0));
            assert!(app.pending_confirm.is_none() && app.targeting.is_none());
            click_at(&mut app, center(layout::ROTATION_BUTTON), t0 + Duration::from_millis(1));
            assert!(app.targeting.is_none());
            click_at(&mut app, center(layout::ROTATION_BUTTON), t0 + MODAL_CLICK_GUARD);
            assert!(app.targeting.is_some());
        }
    }

    #[test]
    fn other_actions_cancel_targeting_before_their_normal_handling() {
        // Shortcuts.
        for key in ["e", "w", "x", "v", "1", "r", "n", "E", "X"] {
            let mut app = App::with_flags(1, TerrainPreset::Flat, true, true);
            open_targeting(&mut app);
            press(&mut app, ch(key));
            assert!(app.targeting.is_none(), "{key}");
            match key.to_ascii_lowercase().as_str() {
                "e" => assert_eq!(app.human_unit(), Some(1), "EndTurn ran"),
                "w" => assert_eq!(app.state.queue.last(), Some(&0), "Wait ran"),
                "x" | "v" => assert!(app.pending_confirm.is_some(), "{key}"),
                "1" => assert!(app.state.units[0].adrenaline_pending),
                _ => assert_eq!(app.human_unit(), Some(0)),
            }
        }
        // Buttons.
        for (button, what) in [(layout::ADRENALINE_BUTTON, "adrenaline"), (layout::END_ROUND_BUTTON, "end"), (layout::RETREAT_BUTTON, "retreat")] {
            let mut app = App::with_flags(1, TerrainPreset::Flat, true, true);
            open_targeting(&mut app);
            click_at(&mut app, center(button), Instant::now());
            assert!(app.targeting.is_none(), "{what}");
            assert_eq!(app.state.units[0].adrenaline_pending, what == "adrenaline");
            assert_eq!(app.pending_confirm.is_some(), what != "adrenaline");
        }
        // Even a disabled other action ends targeting (Adrenaline is not in this loadout).
        let mut app = rot_app();
        open_targeting(&mut app);
        press(&mut app, ch("1"));
        assert!(app.targeting.is_none() && quiet(&app));
        // A key that is no action keeps it.
        let mut app = rot_app();
        open_targeting(&mut app);
        press(&mut app, ch("q"));
        press(&mut app, ch("3"));
        assert!(app.targeting.is_some());
    }

    #[test]
    fn h_and_z_toggle_without_disturbing_targeting() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            let mut app = App::with_flags(1, terrain, false, true);
            open_targeting(&mut app);
            let (intent, before, height) = (app.targeting, snapshot(&app), app.height_overlay);
            for key in ["z", "h", "Z", "H", "z", "h"] {
                press(&mut app, ch(key));
                assert_eq!(app.targeting, intent, "{key}");
            }
            assert_eq!((app.control_overlay, app.height_overlay), (true, !height));
            assert_eq!(app.state.state_hash(), before.0);
            assert!(frame_targeting(&app));
            assert!(app.click_guard_until.is_none() && quiet(&app));
        }
    }

    #[test]
    fn the_click_guard_swallows_the_follow_up_click_after_completing_or_cancelling() {
        for complete in [false, true] {
            let mut app = rot_app();
            open_targeting(&mut app);
            let t0 = Instant::now();
            if complete {
                click_unit(&mut app, 2, t0);
            } else {
                app.key(&named(NamedKey::Escape), t0);
            }
            assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD));
            let after = snapshot(&app);
            let inside = t0 + MODAL_CLICK_GUARD - Duration::from_millis(1);
            for pt in [px(&app, 3), (10.0, 10.0), center(layout::END_ROUND_BUTTON), center(layout::ROTATION_BUTTON), px(&app, 1)] {
                click_at(&mut app, pt, inside);
                assert_eq!(snapshot(&app), after, "{pt:?}");
                assert!(app.error.is_none() && app.pending_confirm.is_none() && app.targeting.is_none());
            }
            // Expired: the next click is an ordinary map click again.
            click_at(&mut app, (10.0, 10.0), t0 + MODAL_CLICK_GUARD);
            assert!(app.error.is_some());
        }
    }

    #[test]
    fn restart_and_new_seed_clear_targeting_and_keep_the_selected_loadout() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            for adrenaline in [false, true] {
                let mut app = App::with_flags(17, terrain, adrenaline, true);
                app.character_key("z");
                for (key, seed) in [("r", 17), ("n", 18), ("n", 19), ("r", 19)] {
                    open_targeting(&mut app);
                    click_unit(&mut app, 3, Instant::now() + Duration::from_secs(9)); // invalid: keeps it
                    assert!(app.targeting.is_some());
                    let t0 = Instant::now();
                    app.key(&ch(key), t0);
                    assert!(app.targeting.is_none(), "{key} clears targeting");
                    assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD), "{key} arms the guard");
                    assert!(quiet(&app) && app.log.is_empty());
                    assert_eq!((app.seed, app.terrain, app.adrenaline, app.rotation), (seed, terrain, adrenaline, true));
                    let fresh = scenario::launch_battle_with(seed, terrain, adrenaline, true);
                    assert_eq!(app.state.state_hash(), fresh.state_hash());
                    assert_eq!(app.state.rng, fresh.rng);
                    assert!(app.state.units.iter().all(|u| u.has_rotation == (u.side == Side::Player)));
                    assert!(app.control_overlay);
                    assert_eq!(rotation_look(&app), Some(render::SKILL_ENABLED));
                }
            }
        }
        // Restart after a Rotation was played restores the full capability and budgets.
        let mut app = rot_app();
        open_targeting(&mut app);
        click_unit(&mut app, 2, Instant::now());
        assert_eq!(app.state.units[0].ap, 6);
        app.restart(1);
        assert_eq!(app.state.units[0].ap, 9);
        assert!(app.state.units[0].has_rotation);
        // Without the flag R/N keep rebuilding the default scenario.
        let mut app = App::with_terrain(4, TerrainPreset::Hills);
        app.character_key("n");
        assert!(!app.rotation);
        assert_eq!(app.state.state_hash(), scenario::battle(5, TerrainPreset::Hills).state_hash());
    }

    #[test]
    fn stale_targeting_is_discarded_with_feedback_and_no_command() {
        type Tamper = fn(&mut App);
        let cases: [(&str, Tamper); 9] = [
            ("actor", |a| a.targeting.as_mut().unwrap().actor = 1),
            ("round", |a| a.targeting.as_mut().unwrap().round += 1),
            ("hash", |a| a.targeting.as_mut().unwrap().state_hash ^= 1),
            ("state changed", |a| a.state.units[2].hp -= 1),
            ("AP spent", |a| a.state.units[0].ap = 2),
            ("capability lost", |a| a.state.units[0].has_rotation = false),
            ("enemy took control", |a| a.state.queue = vec![3, 0]),
            ("actor fleeing", |a| a.state.units[0].morale = Morale::Fleeing),
            ("battle over", |a| a.state.outcome = Some(bb_rules::Outcome::Defeat)),
        ];
        for (name, tamper) in cases {
            for target in [1usize, 2] {
                let mut app = rot_app();
                open_targeting(&mut app);
                tamper(&mut app);
                if name != "state changed" {
                    // Stale intents are not drawn.
                    assert!(!frame_targeting(&app), "{name}: only valid intent is rendered");
                }
                let (before, units) = (snapshot(&app), app.state.units.clone());
                let t0 = Instant::now();
                click_unit(&mut app, target, t0);
                assert!(app.targeting.is_none(), "{name}");
                assert_eq!(snapshot(&app), before, "{name}: no command may be submitted");
                assert_eq!(app.state.units, units, "{name}");
                let (msg, _) = app.error.as_ref().unwrap_or_else(|| panic!("{name}: the player is told the intent was dropped"));
                assert_eq!(msg, "ROTATION DISCARDED: BATTLE CHANGED", "{name}");
                assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD));
            }
        }
        // Eligibility is revalidated even when actor, round and hash still match.
        let mut app = rot_app();
        open_targeting(&mut app);
        app.state.units[2].pos = bb_rules::hex(-3, 0);
        app.targeting.as_mut().unwrap().state_hash = app.state.state_hash();
        let before = snapshot(&app);
        click_unit(&mut app, 2, Instant::now());
        assert!(app.targeting.is_some(), "a current intent with a non-legal click is only an invalid click");
        assert_eq!(snapshot(&app), before);
        assert!(app.error.as_ref().unwrap().0.starts_with("NOT A SWAP TARGET"));
    }

    #[test]
    fn targeting_is_cleared_by_command_submission_and_never_blocks_automatic_turns() {
        // Any submitted command (here a direct Wait and a hotkey End Turn) clears it.
        let mut app = rot_app();
        open_targeting(&mut app);
        app.run(Command::Wait { unit: 0 });
        assert!(app.targeting.is_none());
        let mut app = rot_app();
        open_targeting(&mut app);
        press(&mut app, ch("e"));
        assert!(app.targeting.is_none());
        // Even a rejected command ends it: the intent no longer describes the state.
        let mut app = rot_app();
        open_targeting(&mut app);
        app.run(Command::Move { unit: 0, to: bb_rules::hex(0, 0) });
        assert!(app.targeting.is_none() && app.error.is_some());

        // Control passes to the enemy behind the intent's back: the AI still acts at once.
        let mut app = rot_app();
        open_targeting(&mut app);
        app.state.queue = vec![3, 4, 5, 0];
        assert!(!frame_targeting(&app));
        let before = snapshot(&app);
        assert!(app.ai_step(), "stale targeting does not block automatic turns");
        assert_ne!(snapshot(&app), before);
        assert!(app.targeting.is_none());
        // A fleeing actor is driven automatically as well.
        let mut app = rot_app();
        open_targeting(&mut app);
        app.state.units[0].morale = Morale::Fleeing;
        assert!(app.ai_step());
        assert!(app.targeting.is_none());
        // A valid intent on a human turn is not touched by the driver, which also does not act.
        let mut app = rot_app();
        open_targeting(&mut app);
        assert!(!app.ai_step());
        assert!(app.targeting.is_some());
    }

    #[test]
    fn targeting_frames_and_cancelling_consume_no_rng_and_change_no_state() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            let mut app = App::with_flags(6, terrain, true, true);
            let (before, units, elevations) = (snapshot(&app), app.state.units.clone(), app.state.elevations.clone());
            open_targeting(&mut app);
            for target in [1usize, 2, 3] {
                app.mouse = px(&app, target);
                app.draw();
                let _ = app.frame();
            }
            click_at(&mut app, (10.0, 10.0), Instant::now() + Duration::from_secs(9)); // invalid click
            app.draw();
            press(&mut app, named(NamedKey::Escape));
            app.error = None;
            assert_eq!(snapshot(&app), before);
            assert_eq!(app.state.units, units);
            assert_eq!(app.state.elevations, elevations);
        }
    }

    #[test]
    fn rotation_button_enabled_state_agrees_with_the_app_actor_in_every_phase() {
        let mut app = App::with_flags(2, TerrainPreset::Flat, true, true);
        for step in 0..60 {
            if app.state.outcome().is_some() {
                break;
            }
            let reach: Vec<_> = Vec::new();
            let sc = render::Scene {
                state: &app.state,
                selected: app.human_unit(),
                hover: None,
                reach: &reach,
                log: &[],
                error: None,
                seed: app.seed,
                control_overlay: false,
                height_overlay: false,
                confirm: None,
                targeting: None,
            };
            assert_eq!(render::rotation_enabled(&sc), app.rotation_actor().is_some(), "step {step}");
            assert_eq!(rotation_look(&app) == Some(render::SKILL_ENABLED), app.rotation_actor().is_some(), "step {step}");
            if app.human_unit().is_some() {
                if step % 3 == 0 {
                    app.character_key("2");
                    app.character_key("2");
                }
                app.character_key("e");
            } else {
                app.ai_step();
            }
        }
    }

    #[test]
    fn other_actions_that_cancel_targeting_arm_the_click_guard() {
        // Keys, including a disabled one (1 without --adrenaline), and the Adrenaline button.
        for how in ["1", "e", "w", "button"] {
            let mut app = App::with_flags(1, TerrainPreset::Flat, how == "button", true);
            open_targeting(&mut app);
            let t0 = Instant::now();
            if how == "button" {
                click_at(&mut app, center(layout::ADRENALINE_BUTTON), t0);
            } else {
                assert!(!app.key(&ch(how), t0));
            }
            assert!(app.targeting.is_none(), "{how}");
            assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD), "{how}");
            let after = snapshot(&app);
            let reachable = layout::hex_to_pixel(app.state.reachable(app.state.active_unit().unwrap().id)[0].0);
            click_at(&mut app, reachable, t0 + MODAL_CLICK_GUARD - Duration::from_millis(1));
            assert_eq!(snapshot(&app), after, "{how}: the follow-up click must not Move");
            assert!(app.error.is_none());
            // After the guard expires the same click is an ordinary map click again.
            click_at(&mut app, reachable, t0 + MODAL_CLICK_GUARD);
            assert_ne!(snapshot(&app), after, "{how}");
        }
        // Without targeting nothing arms the guard.
        let mut app = rot_app();
        press(&mut app, ch("1"));
        click_at(&mut app, center(layout::ADRENALINE_BUTTON), Instant::now());
        assert!(app.click_guard_until.is_none());
    }

    #[test]
    fn restart_and_new_seed_cancelling_targeting_arm_the_click_guard() {
        for key in ["r", "n"] {
            let mut app = rot_app();
            open_targeting(&mut app);
            let t0 = Instant::now();
            assert!(!app.key(&ch(key), t0));
            assert!(app.targeting.is_none());
            assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD), "{key}");
            let after = snapshot(&app);
            let reachable = layout::hex_to_pixel(app.state.reachable(0)[0].0);
            click_at(&mut app, reachable, t0 + MODAL_CLICK_GUARD - Duration::from_millis(1));
            assert_eq!(snapshot(&app), after, "{key}: the follow-up click must not Move in the rebuilt battle");
            assert!(app.error.is_none() && app.state.units[0].pos == bb_rules::hex(-3, 3));
            click_at(&mut app, reachable, t0 + MODAL_CLICK_GUARD);
            assert_ne!(snapshot(&app), after, "{key}: ordinary clicks work once the guard expires");
            // Without targeting a restart keeps its ordinary behaviour: no guard.
            let mut plain = rot_app();
            plain.key(&ch(key), Instant::now());
            assert!(plain.click_guard_until.is_none(), "{key}");
        }
        // A guard left by an earlier cancel is replaced by the restart's own window.
        let mut app = rot_app();
        open_targeting(&mut app);
        let t0 = Instant::now();
        app.key(&ch("r"), t0 + Duration::from_millis(100));
        assert_eq!(app.click_guard_until, Some(t0 + Duration::from_millis(100) + MODAL_CLICK_GUARD));
    }

    // --- Footwork loadout, button, shortcut and destination targeting --------------------

    fn launch(terrain: TerrainPreset, adrenaline: bool, rotation: bool, footwork: bool) -> LaunchArgs {
        LaunchArgs { seed: 1, terrain, adrenaline, rotation, footwork, fast_adaptation: false, dodge: false, nine_lives: false, berserk: false, pathfinder: false, indomitable: false, two_hex: false, stunning_blow: false, killing_frenzy: false }
    }

    /// Only Footwork: P1 (-3,3) is active with free neighbours (-2,3), (-2,2), (-4,4), (-3,4).
    fn fw_app() -> App {
        App::from_args(launch(TerrainPreset::Flat, false, false, true))
    }

    /// Adrenaline, Rotation and Footwork together.
    fn all_app() -> App {
        App::from_args(launch(TerrainPreset::Flat, true, true, true))
    }

    const ACTIONS: [TargetAction; 2] = [TargetAction::Rotation, TargetAction::Footwork];

    fn skill_key(action: TargetAction) -> &'static str {
        match action {
            TargetAction::Rotation => "2",
            TargetAction::Footwork => "3",
            TargetAction::StunningBlow => "6",
        }
    }

    fn skill_button(action: TargetAction) -> layout::Rect {
        match action {
            TargetAction::Rotation => layout::ROTATION_BUTTON,
            TargetAction::Footwork => layout::FOOTWORK_BUTTON,
            TargetAction::StunningBlow => layout::STUNNING_BLOW_BUTTON,
        }
    }

    fn skill_look(app: &App, action: TargetAction) -> Option<[f32; 4]> {
        render::skill_fill(&app.frame(), skill_button(action))
    }

    fn footwork_look(app: &App) -> Option<[f32; 4]> {
        skill_look(app, TargetAction::Footwork)
    }

    fn dest_px(h: Hex) -> (f32, f32) {
        layout::hex_to_pixel(h)
    }

    /// A pixel whose click is a legal target of `action` for P1 in the stock loadouts.
    fn legal_point(app: &App, action: TargetAction) -> (f32, f32) {
        match action {
            TargetAction::Rotation => px(app, 1),
            TargetAction::Footwork => dest_px(app.state.legal_footwork_destinations(0)[0]),
            TargetAction::StunningBlow => px(app, 3),
        }
    }

    /// Opens `action` through its shortcut and checks the captured, action-tagged intent.
    fn open_action(app: &mut App, action: TargetAction) {
        press(app, ch(skill_key(action)));
        let intent = app.targeting.expect("targeting is open");
        assert_eq!(
            intent,
            PendingTargeting { action, actor: app.human_unit().unwrap(), round: app.state.round, state_hash: app.state.state_hash() }
        );
    }

    /// Whether the frame draws the Footwork targeting display (STEP hex fills).
    fn frame_stepping(app: &App) -> bool {
        app.frame().iter().any(|v| v.color == render::STEP_FILL || v.color == render::STEP_HOVER)
    }

    fn frame_swapping(app: &App) -> bool {
        frame_targeting(app)
    }

    /// Whether the frame draws the Stunning Blow targeting display (rose enemy hex fills).
    fn frame_stunning(app: &App) -> bool {
        app.frame().iter().any(|v| v.color == render::STUN_TARGET_FILL || v.color == render::STUN_TARGET_HOVER)
    }

    /// Whether the frame draws exactly `action`'s display (and none of the other's).
    fn frame_shows(app: &App, action: TargetAction) -> bool {
        match action {
            TargetAction::Rotation => frame_swapping(app) && !frame_stepping(app) && !frame_stunning(app),
            TargetAction::Footwork => frame_stepping(app) && !frame_swapping(app) && !frame_stunning(app),
            TargetAction::StunningBlow => frame_stunning(app) && !frame_swapping(app) && !frame_stepping(app),
        }
    }

    #[test]
    fn footwork_loadout_composes_with_every_flag_and_keeps_budgets_effects_and_rng() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            for bits in 0..8u8 {
                let (adrenaline, rotation, footwork) = (bits & 1 != 0, bits & 2 != 0, bits & 4 != 0);
                let args = launch(terrain, adrenaline, rotation, footwork);
                let app = App::from_args(args);
                assert_eq!((app.adrenaline, app.rotation, app.footwork), (adrenaline, rotation, footwork));
                assert_eq!(app.state.state_hash(), scenario::launch_battle_for(args).state_hash());
                assert_eq!(app.state.rng, BattleState::new(1).rng, "no RNG consumed");
                let base = scenario::battle(1, terrain);
                for (b, u) in base.units.iter().zip(&app.state.units) {
                    let player = u.side == Side::Player;
                    assert_eq!((u.has_adrenaline, u.has_rotation, u.has_footwork), (player && adrenaline, player && rotation, player && footwork));
                    assert_eq!((u.pos, u.ap, u.fatigue, u.adrenaline_pending, &u.stats), (b.pos, b.ap, b.fatigue, false, &b.stats));
                }
                assert_eq!(app.state.queue, base.queue);
                assert_eq!(app.height_overlay, terrain == TerrainPreset::Hills);
                assert!(app.targeting.is_none());
                assert_eq!(footwork_look(&app), Some(if footwork { render::SKILL_ENABLED } else { render::SKILL_DISABLED }));
            }
            // The existing constructors do not know the flag.
            assert!(!App::with_terrain(7, terrain).footwork);
            assert!(!loadout_app(7, terrain).footwork);
            assert!(!App::with_flags(7, terrain, true, true).footwork);
            assert_eq!(App::with_flags(7, terrain, true, true).state.state_hash(), scenario::launch_battle_with(7, terrain, true, true).state_hash());
        }
        assert_eq!(fw_app().state.state_hash(), scenario::footwork_battle(1, TerrainPreset::Flat).state_hash());
    }

    #[test]
    fn footwork_availability_persists_through_restart_and_new_seed() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            for (adrenaline, rotation) in [(false, false), (true, true)] {
                let mut app = App::from_args(LaunchArgs { seed: 17, terrain, adrenaline, rotation, footwork: true, fast_adaptation: false, dodge: false, nine_lives: false, berserk: false, pathfinder: false, indomitable: false, two_hex: false, stunning_blow: false, killing_frenzy: false });
                for (key, seed) in [("r", 17), ("n", 18), ("n", 19), ("r", 19)] {
                    open_action(&mut app, TargetAction::Footwork);
                    app.key(&ch(key), Instant::now());
                    assert!(app.targeting.is_none());
                    assert_eq!((app.seed, app.terrain, app.adrenaline, app.rotation, app.footwork), (seed, terrain, adrenaline, rotation, true));
                    let fresh = scenario::launch_battle_for(LaunchArgs { seed, terrain, adrenaline, rotation, footwork: true, fast_adaptation: false, dodge: false, nine_lives: false, berserk: false, pathfinder: false, indomitable: false, two_hex: false, stunning_blow: false, killing_frenzy: false });
                    assert_eq!(app.state.state_hash(), fresh.state_hash());
                    assert_eq!(app.state.rng, fresh.rng);
                    assert!(app.state.units.iter().all(|u| u.has_footwork == (u.side == Side::Player)));
                    assert_eq!(footwork_look(&app), Some(render::SKILL_ENABLED));
                }
            }
        }
        // Restart after a Footwork was played restores the full capability and budgets.
        let mut app = fw_app();
        open_action(&mut app, TargetAction::Footwork);
        let pt = legal_point(&app, TargetAction::Footwork);
        click_at(&mut app, pt, Instant::now());
        assert_eq!((app.state.units[0].ap, app.state.units[0].fatigue), (6, 20));
        app.restart(1);
        assert_eq!((app.state.units[0].ap, app.state.units[0].fatigue), (9, 0));
        assert!(app.state.units[0].has_footwork);
        // Without the flag R/N keep rebuilding the default scenario.
        let mut app = App::with_terrain(4, TerrainPreset::Hills);
        app.character_key("n");
        assert!(!app.footwork);
        assert_eq!(app.state.state_hash(), scenario::battle(5, TerrainPreset::Hills).state_hash());
    }

    #[test]
    fn fast_adaptation_option_is_stored_with_zero_stacks_on_player_units_only() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            for bits in 0..16u8 {
                let args = LaunchArgs {
                    seed: 3,
                    terrain,
                    adrenaline: bits & 1 != 0,
                    rotation: bits & 2 != 0,
                    footwork: bits & 4 != 0,
                    fast_adaptation: bits & 8 != 0,
                    dodge: false,
                    nine_lives: false, berserk: false, pathfinder: false, indomitable: false, two_hex: false, stunning_blow: false, killing_frenzy: false,
                };
                let app = App::from_args(args);
                assert_eq!(app.fast_adaptation, args.fast_adaptation);
                assert_eq!((app.adrenaline, app.rotation, app.footwork), (args.adrenaline, args.rotation, args.footwork));
                assert_eq!(app.state.state_hash(), scenario::launch_battle_for(args).state_hash());
                assert!(app.state.units.iter().all(|u| {
                    u.has_fast_adaptation == (args.fast_adaptation && u.side == Side::Player) && u.fast_adaptation_stacks == 0
                }));
            }
            assert!(!App::with_terrain(7, terrain).fast_adaptation);
            assert!(!App::with_flags(7, terrain, true, true).fast_adaptation);
            assert!(App::with_terrain(7, terrain).state.units.iter().all(|u| !u.has_fast_adaptation));
        }
    }

    #[test]
    fn fast_adaptation_persists_through_restart_and_new_seed_with_stacks_reset() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            let args = LaunchArgs { seed: 17, terrain, adrenaline: true, rotation: false, footwork: true, fast_adaptation: true, dodge: false, nine_lives: false, berserk: false, pathfinder: false, indomitable: false, two_hex: false, stunning_blow: false, killing_frenzy: false };
            let mut app = App::from_args(args);
            // Preferences are toggled away from their defaults; rebuilding must keep them.
            app.character_key("h");
            app.character_key("z");
            let (height, control) = (app.height_overlay, app.control_overlay);
            for (key, seed) in [("r", 17), ("n", 18), ("n", 19), ("r", 19)] {
                for u in 0..3 {
                    app.state.units[u].fast_adaptation_stacks = 5 + u as u32;
                }
                app.character_key(key);
                assert_eq!((app.seed, app.terrain), (seed, terrain));
                assert_eq!((app.adrenaline, app.rotation, app.footwork, app.fast_adaptation), (true, false, true, true));
                assert_eq!((app.height_overlay, app.control_overlay), (height, control), "H/Z preferences survive");
                let fresh = scenario::launch_battle_for(LaunchArgs { seed, ..args });
                assert_eq!(app.state.state_hash(), fresh.state_hash());
                assert_eq!(app.state.rng, fresh.rng);
                assert!(app.state.units.iter().all(|u| u.fast_adaptation_stacks == 0));
                assert!(app.state.units.iter().all(|u| u.has_fast_adaptation == (u.side == Side::Player)));
            }
        }
        // Without the option R/N keep rebuilding the unchanged scenario.
        let mut app = App::with_terrain(4, TerrainPreset::Hills);
        app.character_key("n");
        assert!(!app.fast_adaptation);
        assert_eq!(app.state.state_hash(), scenario::battle(5, TerrainPreset::Hills).state_hash());
    }

    #[test]
    fn dodge_option_is_stored_and_granted_to_player_units_only_for_all_32_subsets() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            for bits in 0..32u8 {
                let args = LaunchArgs {
                    seed: 3,
                    terrain,
                    adrenaline: bits & 1 != 0,
                    rotation: bits & 2 != 0,
                    footwork: bits & 4 != 0,
                    fast_adaptation: bits & 8 != 0,
                    dodge: bits & 16 != 0,
                    nine_lives: false, berserk: false, pathfinder: false, indomitable: false, two_hex: false, stunning_blow: false, killing_frenzy: false,
                };
                let app = App::from_args(args);
                assert_eq!(app.dodge, args.dodge);
                assert_eq!(app.launch_args(3), args, "the stored selections round-trip");
                assert_eq!(app.state.state_hash(), scenario::launch_battle_for(args).state_hash());
                assert!(app.state.units.iter().all(|u| u.has_dodge == (args.dodge && u.side == Side::Player)));
            }
            // The parsed command line reaches the App unchanged.
            let parsed = scenario::parse_args(["--seed", "6", "--terrain", if terrain == TerrainPreset::Hills { "hills" } else { "flat" }, "--dodge", "--dodge"].map(String::from)).unwrap();
            let app = App::from_args(parsed);
            assert!(app.dodge && app.seed == 6 && app.terrain == terrain);
            // The existing constructors do not know the flag.
            assert!(!App::with_terrain(7, terrain).dodge);
            assert!(!App::with_flags(7, terrain, true, true).dodge);
            assert!(App::with_terrain(7, terrain).state.units.iter().all(|u| !u.has_dodge));
        }
    }

    #[test]
    fn dodge_persists_through_restart_and_new_seed_with_all_selections_and_fresh_resources() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            let args = LaunchArgs { seed: 17, terrain, adrenaline: true, rotation: true, footwork: true, fast_adaptation: true, dodge: true, nine_lives: false, berserk: false, pathfinder: false, indomitable: false, two_hex: false, stunning_blow: false, killing_frenzy: false };
            let mut app = App::from_args(args);
            app.character_key("h");
            app.character_key("z");
            let (height, control) = (app.height_overlay, app.control_overlay);
            for (key, seed) in [("r", 17), ("n", 18), ("n", 19), ("r", 19)] {
                for u in 0..3 {
                    app.state.units[u].fatigue = 40 + u as u32;
                    app.state.units[u].ap = 1;
                    app.state.units[u].fast_adaptation_stacks = 5 + u as u32;
                }
                app.character_key(key);
                assert_eq!((app.seed, app.terrain), (seed, terrain));
                assert_eq!(app.launch_args(seed), LaunchArgs { seed, ..args });
                assert_eq!((app.height_overlay, app.control_overlay), (height, control), "H/Z preferences survive");
                let fresh = scenario::launch_battle_for(LaunchArgs { seed, ..args });
                assert_eq!(app.state.state_hash(), fresh.state_hash());
                assert_eq!(app.state.rng, fresh.rng);
                assert!(app.state.units.iter().all(|u| u.fatigue == 0 && u.fast_adaptation_stacks == 0));
                assert!(app.state.units.iter().all(|u| u.has_dodge == (u.side == Side::Player)));
            }
        }
        // A Dodge-only configuration keeps the other options off; without it R/N stay plain.
        let mut app = App::from_args(LaunchArgs { dodge: true, ..App::with_terrain(4, TerrainPreset::Hills).launch_args(4) });
        app.character_key("n");
        assert_eq!((app.adrenaline, app.rotation, app.footwork, app.fast_adaptation, app.dodge), (false, false, false, false, true));
        let mut app = App::with_terrain(4, TerrainPreset::Hills);
        app.character_key("n");
        assert!(!app.dodge);
        assert_eq!(app.state.state_hash(), scenario::battle(5, TerrainPreset::Hills).state_hash());
    }

    /// Consume P1's Nine Lives through a real protected hit: E1 (active) strikes the adjacent
    /// 5-HP P1 for a lethal 20 under an RNG state that hits, via the App's own command path.
    fn consume_nine_lives(app: &mut App) {
        assert!(app.state.units[0].has_nine_lives && !app.state.units[0].nine_lives_used);
        app.state.units[0].pos = bb_rules::hex(1, 0);
        app.state.units[3].pos = bb_rules::hex(2, 0);
        app.state.units[0].hp = 5;
        app.state.units[0].armour = 0;
        app.state.units[3].stats.dmg_min = 20;
        app.state.units[3].stats.dmg_max = 20;
        app.state.units[3].ap = 9;
        app.state.units[3].fatigue = 0;
        app.state.units[3].turn_started = true;
        app.state.queue.retain(|&q| q != 3);
        app.state.queue.insert(0, 3);
        let attack = Command::Attack { attacker: 3, target: 0 };
        let rng = (1u64..2_000)
            .map(bb_rules::Rng::new)
            .find(|rng| {
                let mut probe = app.state.clone();
                probe.rng = rng.clone();
                matches!(probe.apply(attack).ok().as_deref(), Some([Event::Attacked { hit: true, .. }, ..]))
            })
            .expect("some RNG state hits");
        app.state.rng = rng;
        app.run(attack);
        assert!(app.state.units[0].alive && app.state.units[0].hp == 1 && app.state.units[0].nine_lives_used);
        assert!(app.log.iter().any(|l| l == "P1 nine lives: survives"), "{:?}", app.log);
    }

    #[test]
    fn nine_lives_option_is_stored_and_granted_to_player_units_only_unused_for_all_64_subsets() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            for bits in 0..64u8 {
                let args = LaunchArgs {
                    seed: 3,
                    terrain,
                    adrenaline: bits & 1 != 0,
                    rotation: bits & 2 != 0,
                    footwork: bits & 4 != 0,
                    fast_adaptation: bits & 8 != 0,
                    dodge: bits & 16 != 0,
                    nine_lives: bits & 32 != 0,
                    berserk: false,
                    pathfinder: false,
                    indomitable: false,
                    two_hex: false,
                    stunning_blow: false, killing_frenzy: false,
                };
                let app = App::from_args(args);
                assert_eq!(app.nine_lives, args.nine_lives);
                assert_eq!(app.launch_args(3), args, "the stored selections round-trip");
                assert_eq!(app.state.state_hash(), scenario::launch_battle_for(args).state_hash());
                assert!(app.state.units.iter().all(|u| u.has_nine_lives == (args.nine_lives && u.side == Side::Player) && !u.nine_lives_used));
            }
            let parsed = scenario::parse_args(["--seed", "6", "--terrain", if terrain == TerrainPreset::Hills { "hills" } else { "flat" }, "--nine-lives", "--nine-lives"].map(String::from)).unwrap();
            let app = App::from_args(parsed);
            assert!(app.nine_lives && app.seed == 6 && app.terrain == terrain);
            // The existing constructors do not know the flag.
            assert!(!App::with_terrain(7, terrain).nine_lives);
            assert!(!App::with_flags(7, terrain, true, true).nine_lives);
            assert!(App::with_terrain(7, terrain).state.units.iter().all(|u| !u.has_nine_lives));
        }
    }

    #[test]
    fn consumed_nine_lives_is_restored_by_restart_and_new_seed_with_all_selections_and_preferences() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            let args = LaunchArgs { seed: 17, terrain, adrenaline: true, rotation: true, footwork: true, fast_adaptation: true, dodge: true, nine_lives: true, berserk: true, pathfinder: true, indomitable: false, two_hex: false, stunning_blow: false, killing_frenzy: false };
            let mut app = App::from_args(args);
            app.character_key("h");
            app.character_key("z");
            let (height, control) = (app.height_overlay, app.control_overlay);
            for (key, seed) in [("r", 17), ("n", 18), ("n", 19), ("r", 19)] {
                consume_nine_lives(&mut app);
                for u in 0..3 {
                    app.state.units[u].fatigue = 40 + u as u32;
                    app.state.units[u].ap = 1;
                    app.state.units[u].fast_adaptation_stacks = 5 + u as u32;
                }
                app.character_key(key);
                assert_eq!((app.seed, app.terrain), (seed, terrain));
                assert_eq!(app.launch_args(seed), LaunchArgs { seed, ..args });
                assert_eq!((app.height_overlay, app.control_overlay), (height, control), "H/Z preferences survive");
                let fresh = scenario::launch_battle_for(LaunchArgs { seed, ..args });
                assert_eq!(app.state.state_hash(), fresh.state_hash());
                assert_eq!(app.state.rng, fresh.rng);
                assert!(app.state.units.iter().all(|u| u.fatigue == 0 && u.fast_adaptation_stacks == 0 && !u.nine_lives_used));
                assert!(app.state.units.iter().all(|u| u.has_nine_lives == (u.side == Side::Player)), "unused protection is restored");
                assert!(app.state.units.iter().all(|u| u.has_berserk == (u.side == Side::Player) && !u.berserk_used));
                assert!(app.state.units.iter().all(|u| u.has_pathfinder == (u.side == Side::Player)));
                assert!(app.log.is_empty());
            }
        }
        // A Nine-Lives-only configuration keeps the other options off; without it R/N stay plain.
        let mut app = App::from_args(LaunchArgs { nine_lives: true, ..App::with_terrain(4, TerrainPreset::Hills).launch_args(4) });
        consume_nine_lives(&mut app);
        app.character_key("n");
        assert_eq!((app.adrenaline, app.rotation, app.footwork, app.fast_adaptation, app.dodge, app.nine_lives), (false, false, false, false, false, true));
        assert!(app.state.units[0].has_nine_lives && !app.state.units[0].nine_lives_used);
        let mut app = App::with_terrain(4, TerrainPreset::Hills);
        app.character_key("n");
        assert!(!app.nine_lives);
        assert_eq!(app.state.state_hash(), scenario::battle(5, TerrainPreset::Hills).state_hash());
    }

    #[test]
    fn footwork_button_and_shortcut_open_targeting_without_submitting_a_command() {
        for how in 0..4 {
            let mut app = fw_app();
            let (before, units) = (snapshot(&app), app.state.units.clone());
            let expected =
                PendingTargeting { action: TargetAction::Footwork, actor: 0, round: 1, state_hash: app.state.state_hash() };
            match how {
                0 => assert!(!press(&mut app, ch("3"))),
                1 => app.character_key("3"),
                2 => click_at(&mut app, center(layout::FOOTWORK_BUTTON), Instant::now()),
                _ => {
                    let r = layout::FOOTWORK_BUTTON;
                    click_at(&mut app, (r.x + 0.5, r.y + 0.5), Instant::now());
                }
            }
            assert_eq!(app.targeting, Some(expected), "how {how}");
            assert_eq!(snapshot(&app), before, "no command: state, RNG, queue and log are untouched");
            assert_eq!(app.state.units, units);
            assert!(quiet(&app) && app.pending_confirm.is_none() && app.click_guard_until.is_none());
            assert!(frame_shows(&app, TargetAction::Footwork));
            assert_eq!(footwork_look(&app), Some(render::SKILL_ACTIVE));
        }
        // Outside the rectangle it is an ordinary map click.
        let mut app = fw_app();
        let r = layout::FOOTWORK_BUTTON;
        click_at(&mut app, (r.right() + 0.5, r.y + 0.5), Instant::now());
        assert!(app.targeting.is_none() && app.error.is_some());
    }

    #[test]
    fn disabled_footwork_is_a_silent_no_op_that_never_reaches_the_map() {
        type Setup = fn(&mut App);
        let cases: [(&str, Setup); 11] = [
            ("no AP", |a| a.state.units[0].ap = 2),
            ("fatigue cap", |a| a.state.units[0].fatigue = 81),
            ("fatigue overflow", |a| {
                a.state.units[0].stats.fatigue_max = u32::MAX;
                a.state.units[0].fatigue = u32::MAX;
            }),
            ("no capability", |a| a.state.units[0].has_footwork = false),
            ("no free adjacent hex", |a| {
                a.state.units[0].pos = bb_rules::hex(-4, 4);
                let free = a.state.legal_footwork_destinations(0);
                for (i, h) in free.into_iter().enumerate() {
                    a.state.units[3 + i].pos = h;
                }
            }),
            ("enemy turn", |a| a.state.queue = vec![3, 0]),
            ("battle over", |a| a.state.outcome = Some(bb_rules::Outcome::Victory)),
            ("fleeing", |a| a.state.units[0].morale = Morale::Fleeing),
            ("retreating", |a| a.state.units[0].retreating = true),
            ("dead active unit", |a| a.state.units[0].alive = false),
            ("escaped active unit", |a| a.state.units[0].fled = true),
        ];
        for (what, setup) in cases {
            let mut app = fw_app();
            setup(&mut app);
            assert!(app.state.legal_footwork_destinations(0).is_empty() || app.footwork_actor().is_none(), "{what}");
            assert_eq!(app.footwork_actor(), None, "{what}");
            assert_eq!(footwork_look(&app), Some(render::SKILL_DISABLED), "{what}");
            let (before, units) = (snapshot(&app), app.state.units.clone());
            press(&mut app, ch("3"));
            click_at(&mut app, center(layout::FOOTWORK_BUTTON), Instant::now());
            assert!(app.targeting.is_none(), "{what}");
            assert_eq!(snapshot(&app), before, "{what}");
            assert_eq!(app.state.units, units, "{what}");
            assert!(quiet(&app) && app.pending_confirm.is_none() && app.click_guard_until.is_none(), "{what}");
        }
        // Without the flag the button is always disabled.
        let mut app = App::with_flags(1, TerrainPreset::Flat, true, true);
        let before = snapshot(&app);
        press(&mut app, ch("3"));
        click_at(&mut app, center(layout::FOOTWORK_BUTTON), Instant::now());
        assert_eq!(snapshot(&app), before);
        assert!(app.targeting.is_none() && quiet(&app));
        assert_eq!(footwork_look(&app), Some(render::SKILL_DISABLED));
        // A modal consumes the activation.
        for retreat in [false, true] {
            let mut app = fw_app();
            if retreat {
                open_retreat(&mut app);
            } else {
                open_dialog(&mut app);
            }
            let (pending, before) = (app.pending_confirm, snapshot(&app));
            press(&mut app, ch("3"));
            app.character_key("3");
            click_at(&mut app, center(layout::FOOTWORK_BUTTON), Instant::now() + Duration::from_secs(5));
            assert!(app.targeting.is_none());
            assert_eq!(app.pending_confirm, pending);
            assert_eq!(snapshot(&app), before);
            assert!(quiet(&app));
        }
    }

    #[test]
    fn selecting_a_legal_destination_submits_exactly_the_direct_footwork_command() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            let probe = App::from_args(LaunchArgs { seed: 5, terrain, adrenaline: false, rotation: false, footwork: true, fast_adaptation: false, dodge: false, nine_lives: false, berserk: false, pathfinder: false, indomitable: false, two_hex: false, stunning_blow: false, killing_frenzy: false });
            let legal = probe.state.legal_footwork_destinations(0);
            assert!(legal.len() >= 3);
            for &to in &legal {
                let mut direct = App::from_args(LaunchArgs { seed: 5, terrain, adrenaline: false, rotation: false, footwork: true, fast_adaptation: false, dodge: false, nine_lives: false, berserk: false, pathfinder: false, indomitable: false, two_hex: false, stunning_blow: false, killing_frenzy: false });
                direct.run(Command::Footwork { unit: 0, to });
                assert_eq!(direct.log.first().map(String::as_str), Some("P1 footwork"));

                let mut app = App::from_args(LaunchArgs { seed: 5, terrain, adrenaline: false, rotation: false, footwork: true, fast_adaptation: false, dodge: false, nine_lives: false, berserk: false, pathfinder: false, indomitable: false, two_hex: false, stunning_blow: false, killing_frenzy: false });
                open_action(&mut app, TargetAction::Footwork);
                let t0 = Instant::now();
                click_at(&mut app, dest_px(to), t0);
                assert!(app.targeting.is_none(), "targeting is cleared");
                assert_eq!(app.state.state_hash(), direct.state.state_hash(), "{to:?}");
                assert_eq!(app.state.rng, direct.state.rng);
                assert_eq!(app.state.queue, direct.state.queue);
                assert_eq!(app.state.units, direct.state.units);
                assert_eq!(app.log, direct.log, "exactly one command, same log");
                assert!(app.error.is_none());
                assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD));
                assert_eq!(app.state.units[0].pos, to);
                assert_eq!((app.state.units[0].ap, app.state.units[0].fatigue), (9 - 3, 20));
                assert_eq!(app.human_unit(), Some(0), "the actor keeps its turn");
            }
        }
    }

    #[test]
    fn footwork_arriving_on_the_map_edge_matches_the_direct_command_and_logs_the_edge() {
        let mut direct = fw_app();
        let edge = direct
            .state
            .legal_footwork_destinations(0)
            .into_iter()
            .find(|&h| direct.state.preview_footwork(0, h).unwrap().edge_penalty.is_some())
            .expect("an edge destination exists");
        direct.run(Command::Footwork { unit: 0, to: edge });
        assert_eq!(direct.log, vec!["P1 footwork".to_string(), "P1 edge Steady>Breaking".to_string()]);

        let mut app = fw_app();
        open_action(&mut app, TargetAction::Footwork);
        // The hovered preview announces the penalty the click is about to apply.
        app.mouse = dest_px(edge);
        let verts = app.frame();
        assert!(verts.iter().any(|v| v.color == render::PREVIEW_WARN));
        click_at(&mut app, dest_px(edge), Instant::now());
        assert_eq!(app.log, direct.log);
        assert_eq!(app.state.units, direct.state.units);
        assert_eq!(app.state.units[0].morale, Morale::Breaking);
        assert_eq!(app.state.state_hash(), direct.state.state_hash());
        assert_eq!(app.state.rng, direct.state.rng);
    }

    #[test]
    fn footwork_under_enemy_control_provokes_nothing_and_matches_the_direct_command() {
        let setup = |a: &mut App| {
            a.state.units[3].pos = bb_rules::hex(-2, 2); // E1 adjacent to P1: its zone of control
            assert!(!a.state.enemy_controllers_at(0, a.state.units[0].pos).is_empty());
        };
        let mut direct = fw_app();
        setup(&mut direct);
        let to = bb_rules::hex(-2, 3);
        assert!(!direct.state.enemy_controllers_at(0, to).is_empty(), "the destination is also controlled");
        assert!(direct.state.legal_footwork_destinations(0).contains(&to));
        let enemy_before = direct.state.units[3].clone();
        direct.run(Command::Footwork { unit: 0, to });
        assert_eq!(direct.log, vec!["P1 footwork".to_string()], "no opportunity attack, no reaction");

        let mut app = fw_app();
        setup(&mut app);
        open_action(&mut app, TargetAction::Footwork);
        click_at(&mut app, dest_px(to), Instant::now());
        assert_eq!(app.log, direct.log);
        assert_eq!(app.state.units, direct.state.units);
        assert_eq!(app.state.rng, direct.state.rng, "no RNG consumed by Footwork");
        assert_eq!(app.state.units[3], enemy_before, "the enemy did not act");
        assert_eq!(app.state.state_hash(), direct.state.state_hash());
    }

    #[test]
    fn invalid_footwork_clicks_keep_targeting_and_never_fall_through_to_move_attack_or_rotation() {
        let mut app = all_app();
        open_action(&mut app, TargetAction::Footwork);
        let intent = app.targeting;
        let (before, units) = (snapshot(&app), app.state.units.clone());
        let legal = app.state.legal_footwork_destinations(0);
        let two_away = app.state.reachable(0).into_iter().map(|(h, _)| h).find(|h| !legal.contains(h) && h.distance(app.state.units[0].pos) == 2).unwrap();
        let far = bb_rules::hex(0, 0);
        let now = Instant::now() + Duration::from_secs(10);
        for (what, pt) in [
            ("reachable non-adjacent hex", dest_px(two_away)),
            ("unreachable empty hex", dest_px(far)),
            ("occupied ally (a Rotation target)", px(&app, 1)),
            ("other ally", px(&app, 2)),
            ("enemy", px(&app, 3)),
            ("the actor itself", px(&app, 0)),
            ("off the map", (5.0, 700.0)),
            ("log area", (layout::PANEL_X + 5.0, 560.0)),
        ] {
            click_at(&mut app, pt, now);
            assert_eq!(app.targeting, intent, "{what}: targeting stays open");
            assert_eq!(snapshot(&app), before, "{what}: no command");
            assert_eq!(app.state.units, units, "{what}");
            let (msg, _) = app.error.take().unwrap_or_else(|| panic!("{what}: concise selection feedback"));
            assert!(msg.starts_with("NOT A STEP HEX"), "{what}: {msg}");
            assert!(msg.chars().count() <= 70 && msg.is_ascii());
        }
        // The same ordinary click without targeting does Move, proving targeting consumed it above.
        let mut control = all_app();
        click_at(&mut control, dest_px(two_away), now);
        assert_eq!(control.state.units[0].pos, two_away);
        // An enemy adjacent to the actor is still not an Attack.
        let mut app = fw_app();
        app.state.units[3].pos = bb_rules::hex(-2, 3);
        open_action(&mut app, TargetAction::Footwork);
        let before = snapshot(&app);
        click_unit(&mut app, 3, now);
        assert_eq!(snapshot(&app), before, "no Attack");
        assert!(app.targeting.is_some());
        // A destination made illegal (occupied) after opening is an invalid click, not a command.
        let mut app = fw_app();
        open_action(&mut app, TargetAction::Footwork);
        let to = app.state.legal_footwork_destinations(0)[0];
        app.state.units[3].pos = to;
        app.targeting.as_mut().unwrap().state_hash = app.state.state_hash();
        let before = snapshot(&app);
        click_at(&mut app, dest_px(to), now);
        assert!(app.targeting.is_some());
        assert_eq!(snapshot(&app), before);
        assert!(app.error.as_ref().unwrap().0.starts_with("NOT A STEP HEX"));
        // Rotation mode never accepts a Footwork destination.
        let mut app = all_app();
        open_action(&mut app, TargetAction::Rotation);
        let before = snapshot(&app);
        click_at(&mut app, dest_px(legal[0]), now);
        assert_eq!(snapshot(&app), before);
        assert!(app.targeting.is_some() && app.error.as_ref().unwrap().0.starts_with("NOT A SWAP TARGET"));
    }

    #[test]
    fn a_footwork_selection_dispatches_exactly_once_even_with_repeated_clicks() {
        let mut app = fw_app();
        open_action(&mut app, TargetAction::Footwork);
        let to = app.state.legal_footwork_destinations(0)[0];
        let t0 = Instant::now();
        click_at(&mut app, dest_px(to), t0);
        let after = snapshot(&app);
        let steps = |a: &App| a.log.iter().filter(|l| l.contains("footwork")).count();
        assert_eq!(steps(&app), 1);
        let inside = t0 + MODAL_CLICK_GUARD - Duration::from_millis(1);
        for _ in 0..3 {
            click_at(&mut app, dest_px(to), inside);
            click_unit(&mut app, 0, inside);
        }
        assert_eq!(snapshot(&app), after);
        assert!(app.error.is_none());
        click_at(&mut app, dest_px(to), t0 + MODAL_CLICK_GUARD);
        assert_eq!(steps(&app), 1);
        assert!(app.targeting.is_none());
    }

    #[test]
    fn switching_between_the_targeted_skills_is_direct_and_never_cross_wires_commands() {
        for (from, to) in [(TargetAction::Rotation, TargetAction::Footwork), (TargetAction::Footwork, TargetAction::Rotation)] {
            for by_button in [false, true] {
                let mut app = all_app();
                open_action(&mut app, from);
                assert_eq!(app.click_guard_until, None);
                let (before, units) = (snapshot(&app), app.state.units.clone());
                if by_button {
                    click_at(&mut app, center(skill_button(to)), Instant::now());
                } else {
                    press(&mut app, ch(skill_key(to)));
                }
                let intent = app.targeting.expect("switched, not closed");
                assert_eq!(
                    intent,
                    PendingTargeting { action: to, actor: 0, round: 1, state_hash: app.state.state_hash() },
                    "a fresh intent of the other skill"
                );
                assert_eq!(app.click_guard_until, None, "switching arms no guard");
                assert_eq!(snapshot(&app), before, "no command");
                assert_eq!(app.state.units, units);
                assert!(quiet(&app) && app.pending_confirm.is_none());
                assert!(frame_shows(&app, to), "only the new mode is drawn");
                assert_eq!(skill_look(&app, to), Some(render::SKILL_ACTIVE));
                assert_eq!(skill_look(&app, from), Some(render::SKILL_ENABLED));
                // The old mode's target is now an invalid click for the new mode.
                let wrong = legal_point(&app, from);
                let right = legal_point(&app, to);
                click_at(&mut app, wrong, Instant::now());
                assert_eq!(snapshot(&app), before, "no cross-wired command");
                assert_eq!(app.targeting, Some(intent));
                assert!(app.error.take().is_some());
                // The new mode's target submits only its own command.
                let t0 = Instant::now();
                click_at(&mut app, right, t0);
                assert!(app.targeting.is_none());
                let count = |what: &str| app.log.iter().filter(|l| l.contains(what)).count();
                match to {
                    TargetAction::Rotation => assert_eq!((count("swaps"), count("footwork")), (1, 0)),
                    TargetAction::Footwork => assert_eq!((count("swaps"), count("footwork")), (0, 1)),
                    TargetAction::StunningBlow => unreachable!("not part of the two-skill loop"),
                }
                assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD));
            }
        }
    }

    #[test]
    fn activating_an_unavailable_targeted_skill_leaves_the_current_mode_unchanged() {
        for (open, other) in [(TargetAction::Rotation, TargetAction::Footwork), (TargetAction::Footwork, TargetAction::Rotation)] {
            // A loadout without the other skill.
            let mut app = match open {
                TargetAction::Rotation => rot_app(),
                TargetAction::Footwork => fw_app(),
                TargetAction::StunningBlow => unreachable!("not part of the two-skill loop"),
            };
            open_action(&mut app, open);
            let (intent, before, units) = (app.targeting, snapshot(&app), app.state.units.clone());
            press(&mut app, ch(skill_key(other)));
            app.character_key(skill_key(other));
            click_at(&mut app, center(skill_button(other)), Instant::now());
            assert_eq!(app.targeting, intent, "{open:?}: unchanged");
            assert_eq!(snapshot(&app), before);
            assert_eq!(app.state.units, units);
            assert!(quiet(&app) && app.click_guard_until.is_none());
            assert!(frame_shows(&app, open));
            assert_eq!(skill_look(&app, other), Some(render::SKILL_DISABLED));
        }
        // The other skill is owned but currently unavailable (no free hex / no ally).
        let mut app = all_app();
        app.state.units[0].pos = bb_rules::hex(-4, 4);
        for (i, h) in app.state.legal_footwork_destinations(0).into_iter().enumerate() {
            app.state.units[3 + i].pos = h;
        }
        assert!(app.footwork_actor().is_none() && app.rotation_actor().is_some());
        open_action(&mut app, TargetAction::Rotation);
        let (intent, before) = (app.targeting, snapshot(&app));
        press(&mut app, ch("3"));
        assert_eq!((app.targeting, snapshot(&app)), (intent, before));
    }

    #[test]
    fn a_second_activation_of_the_same_skill_cancels_and_arms_the_guard() {
        for action in ACTIONS {
            for how in 0..3 {
                let mut app = all_app();
                let before = snapshot(&app);
                open_action(&mut app, action);
                let t0 = Instant::now();
                match how {
                    0 => assert!(!app.key(&ch(skill_key(action)), t0)),
                    1 => assert!(!app.key(&named(NamedKey::Escape), t0), "Escape cancels instead of quitting"),
                    _ => click_at(&mut app, center(skill_button(action)), t0),
                }
                assert!(app.targeting.is_none(), "{action:?} how {how}");
                assert_eq!(snapshot(&app), before);
                assert!(quiet(&app));
                assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD), "the cancel arms the click guard");
                assert_eq!(skill_look(&app, action), Some(render::SKILL_ENABLED));
                assert!(press(&mut app, named(NamedKey::Escape)), "outside targeting Escape quits");
                app.click_guard_until = None;
                open_action(&mut app, action);
            }
        }
    }

    #[test]
    fn modals_take_priority_over_both_targeting_modes_and_opening_one_clears_targeting() {
        for action in ACTIONS {
            for (retreat, by_button) in [(false, false), (true, false), (false, true), (true, true)] {
                let mut app = all_app();
                open_action(&mut app, action);
                let before = snapshot(&app);
                match (retreat, by_button) {
                    (false, false) => {
                        press(&mut app, ch("x"));
                    }
                    (true, false) => {
                        press(&mut app, ch("v"));
                    }
                    (false, true) => click_at(&mut app, center(layout::END_ROUND_BUTTON), Instant::now()),
                    (true, true) => click_at(&mut app, center(layout::RETREAT_BUTTON), Instant::now()),
                }
                assert!(app.targeting.is_none(), "opening a confirmation clears targeting");
                let pending = app.pending_confirm.expect("the dialog opened");
                assert_eq!(snapshot(&app), before);
                // Every targeting input is consumed by the modal: nothing starts, nothing switches.
                let later = Instant::now() + Duration::from_secs(5);
                for a in ACTIONS {
                    press(&mut app, ch(skill_key(a)));
                    app.character_key(skill_key(a));
                    click_at(&mut app, center(skill_button(a)), later);
                    let pt = legal_point(&app, a);
                    click_at(&mut app, pt, later);
                }
                assert!(app.targeting.is_none());
                assert_eq!(app.pending_confirm, Some(pending));
                assert_eq!(snapshot(&app), before);
                assert!(quiet(&app));
                // Escape closes the dialog (not the app); the guard then swallows the button.
                let t0 = Instant::now();
                assert!(!app.key(&named(NamedKey::Escape), t0));
                assert!(app.pending_confirm.is_none() && app.targeting.is_none());
                click_at(&mut app, center(skill_button(action)), t0 + Duration::from_millis(1));
                assert!(app.targeting.is_none());
                click_at(&mut app, center(skill_button(action)), t0 + MODAL_CLICK_GUARD);
                assert_eq!(app.targeting.map(|t| t.action), Some(action));
            }
        }
    }

    #[test]
    fn other_actions_cancel_either_targeting_mode_before_their_normal_handling() {
        for action in ACTIONS {
            for key in ["e", "w", "x", "v", "1", "r", "n", "E", "X"] {
                let mut app = all_app();
                open_action(&mut app, action);
                let t0 = Instant::now();
                app.key(&ch(key), t0);
                assert!(app.targeting.is_none(), "{action:?} {key}");
                assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD), "{action:?} {key}");
                match key.to_ascii_lowercase().as_str() {
                    "e" => assert_eq!(app.human_unit(), Some(1), "EndTurn ran"),
                    "w" => assert_eq!(app.state.queue.last(), Some(&0), "Wait ran"),
                    "x" | "v" => assert!(app.pending_confirm.is_some(), "{key}"),
                    "1" => assert!(app.state.units[0].adrenaline_pending),
                    _ => assert_eq!(app.human_unit(), Some(0)),
                }
            }
            for (button, what) in [(layout::ADRENALINE_BUTTON, "adrenaline"), (layout::END_ROUND_BUTTON, "end"), (layout::RETREAT_BUTTON, "retreat")] {
                let mut app = all_app();
                open_action(&mut app, action);
                let t0 = Instant::now();
                click_at(&mut app, center(button), t0);
                assert!(app.targeting.is_none(), "{what}");
                assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD), "{what}");
                assert_eq!(app.state.units[0].adrenaline_pending, what == "adrenaline");
                assert_eq!(app.pending_confirm.is_some(), what != "adrenaline");
            }
            // Even a disabled immediate action ends targeting (Adrenaline is not in this loadout).
            let mut app = fw_app();
            open_action(&mut app, TargetAction::Footwork);
            press(&mut app, ch("1"));
            assert!(app.targeting.is_none() && quiet(&app));
            // A key that is no action keeps it.
            let mut app = all_app();
            open_action(&mut app, action);
            press(&mut app, ch("q"));
            assert!(app.targeting.is_some());
        }
    }

    #[test]
    fn h_and_z_toggle_without_disturbing_either_targeting_mode() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            for action in ACTIONS {
                let mut app = App::from_args(launch(terrain, false, true, true));
                open_action(&mut app, action);
                let (intent, before, height) = (app.targeting, snapshot(&app), app.height_overlay);
                for key in ["z", "h", "Z", "H", "z", "h"] {
                    press(&mut app, ch(key));
                    assert_eq!(app.targeting, intent, "{key}");
                }
                assert_eq!((app.control_overlay, app.height_overlay), (true, !height));
                assert_eq!(app.state.state_hash(), before.0);
                assert!(frame_shows(&app, action));
                assert!(app.click_guard_until.is_none() && quiet(&app));
            }
        }
    }

    #[test]
    fn the_click_guard_swallows_the_follow_up_click_after_completing_or_cancelling_either_mode() {
        for action in ACTIONS {
            for complete in [false, true] {
                let mut app = all_app();
                open_action(&mut app, action);
                let t0 = Instant::now();
                if complete {
                    let pt = legal_point(&app, action);
                    click_at(&mut app, pt, t0);
                } else {
                    app.key(&named(NamedKey::Escape), t0);
                }
                assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD));
                let after = snapshot(&app);
                let inside = t0 + MODAL_CLICK_GUARD - Duration::from_millis(1);
                for pt in [px(&app, 3), (10.0, 10.0), center(layout::END_ROUND_BUTTON), center(skill_button(action)), center(skill_button(ACTIONS[1])), px(&app, 1)] {
                    click_at(&mut app, pt, inside);
                    assert_eq!(snapshot(&app), after, "{pt:?}");
                    assert!(app.error.is_none() && app.pending_confirm.is_none() && app.targeting.is_none());
                }
                click_at(&mut app, (10.0, 10.0), t0 + MODAL_CLICK_GUARD);
                assert!(app.error.is_some(), "expired: an ordinary map click again");
            }
        }
    }

    #[test]
    fn restart_and_new_seed_cancelling_either_mode_arm_the_click_guard() {
        for action in ACTIONS {
            for key in ["r", "n"] {
                let mut app = all_app();
                open_action(&mut app, action);
                let t0 = Instant::now();
                assert!(!app.key(&ch(key), t0));
                assert!(app.targeting.is_none());
                assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD), "{key}");
                assert!(app.log.is_empty() && app.error.is_none());
                let after = snapshot(&app);
                let reachable = layout::hex_to_pixel(app.state.reachable(0)[0].0);
                click_at(&mut app, reachable, t0 + MODAL_CLICK_GUARD - Duration::from_millis(1));
                assert_eq!(snapshot(&app), after, "{key}: the follow-up click must not Move in the rebuilt battle");
                assert!(app.error.is_none() && app.state.units[0].pos == bb_rules::hex(-3, 3));
                click_at(&mut app, reachable, t0 + MODAL_CLICK_GUARD);
                assert_ne!(snapshot(&app), after, "{key}: ordinary clicks work once the guard expires");
                assert!(app.footwork && app.rotation && app.adrenaline, "the loadout survives the rebuild");
                // Without targeting a restart arms nothing.
                let mut plain = all_app();
                plain.key(&ch(key), Instant::now());
                assert!(plain.click_guard_until.is_none(), "{key}");
            }
        }
    }

    #[test]
    fn stale_footwork_targeting_is_discarded_with_feedback_and_no_command() {
        type Tamper = fn(&mut App);
        let cases: [(&str, Tamper); 11] = [
            ("actor", |a| a.targeting.as_mut().unwrap().actor = 1),
            ("round", |a| a.targeting.as_mut().unwrap().round += 1),
            ("hash", |a| a.targeting.as_mut().unwrap().state_hash ^= 1),
            ("state changed", |a| a.state.units[2].hp -= 1),
            ("AP spent", |a| a.state.units[0].ap = 2),
            ("capability lost", |a| a.state.units[0].has_footwork = false),
            ("enemy took control", |a| a.state.queue = vec![3, 0]),
            ("actor fleeing", |a| a.state.units[0].morale = Morale::Fleeing),
            ("actor retreating", |a| a.state.units[0].retreating = true),
            ("battle over", |a| a.state.outcome = Some(bb_rules::Outcome::Defeat)),
            ("actor moved", |a| a.state.units[0].pos = bb_rules::hex(-2, 3)),
        ];
        for (name, tamper) in cases {
            for dest in 0..2usize {
                let mut app = fw_app();
                open_action(&mut app, TargetAction::Footwork);
                let to = app.state.legal_footwork_destinations(0)[dest];
                tamper(&mut app);
                if name != "state changed" && name != "actor moved" {
                    assert!(!frame_stepping(&app), "{name}: only valid intent is rendered");
                }
                let (before, units) = (snapshot(&app), app.state.units.clone());
                let t0 = Instant::now();
                click_at(&mut app, dest_px(to), t0);
                assert!(app.targeting.is_none(), "{name}");
                assert_eq!(snapshot(&app), before, "{name}: no command may be submitted");
                assert_eq!(app.state.units, units, "{name}");
                let (msg, _) = app.error.as_ref().unwrap_or_else(|| panic!("{name}: the player is told the intent was dropped"));
                assert_eq!(msg, "FOOTWORK DISCARDED: BATTLE CHANGED", "{name}");
                assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD));
            }
        }
        // A stale intent is discarded before the click could be an eligibility error.
        let mut app = fw_app();
        open_action(&mut app, TargetAction::Footwork);
        app.state.units[0].ap = 2;
        click_at(&mut app, (10.0, 10.0), Instant::now());
        assert_eq!(app.error.as_ref().unwrap().0, "FOOTWORK DISCARDED: BATTLE CHANGED");
    }

    #[test]
    fn footwork_targeting_is_cleared_by_submission_and_never_blocks_automatic_turns() {
        let mut app = fw_app();
        open_action(&mut app, TargetAction::Footwork);
        app.run(Command::Wait { unit: 0 });
        assert!(app.targeting.is_none());
        let mut app = fw_app();
        open_action(&mut app, TargetAction::Footwork);
        press(&mut app, ch("e"));
        assert!(app.targeting.is_none());
        // Even a rejected command ends it.
        let mut app = fw_app();
        open_action(&mut app, TargetAction::Footwork);
        app.run(Command::Move { unit: 0, to: bb_rules::hex(0, 0) });
        assert!(app.targeting.is_none() && app.error.is_some());
        // Control passes to the enemy behind the intent's back: the AI still acts at once.
        let mut app = fw_app();
        open_action(&mut app, TargetAction::Footwork);
        app.state.queue = vec![3, 4, 5, 0];
        assert!(!frame_stepping(&app));
        let before = snapshot(&app);
        assert!(app.ai_step(), "stale targeting does not block automatic turns");
        assert_ne!(snapshot(&app), before);
        assert!(app.targeting.is_none());
        let mut app = fw_app();
        open_action(&mut app, TargetAction::Footwork);
        app.state.units[0].morale = Morale::Fleeing;
        assert!(app.ai_step());
        assert!(app.targeting.is_none());
        // A valid intent on a human turn is not touched by the driver, which also does not act.
        let mut app = fw_app();
        open_action(&mut app, TargetAction::Footwork);
        assert!(!app.ai_step());
        assert!(app.targeting.is_some());
        // The AI never casts Footwork.
        let mut app = fw_app();
        app.state.queue = vec![3, 0];
        for _ in 0..3 {
            if app.human_unit().is_some() {
                break;
            }
            app.ai_step();
        }
        assert!(!app.log.iter().any(|l| l.contains("footwork")));
    }

    #[test]
    fn footwork_frames_switching_and_cancelling_consume_no_rng_and_change_no_state() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            let mut app = App::from_args(launch(terrain, true, true, true));
            let (before, units, elevations) = (snapshot(&app), app.state.units.clone(), app.state.elevations.clone());
            open_action(&mut app, TargetAction::Footwork);
            for h in app.state.legal_footwork_destinations(0) {
                app.mouse = dest_px(h);
                app.draw();
                let _ = app.frame();
            }
            press(&mut app, ch("2"));
            press(&mut app, ch("3"));
            click_at(&mut app, (10.0, 10.0), Instant::now() + Duration::from_secs(9)); // invalid click
            app.draw();
            press(&mut app, named(NamedKey::Escape));
            app.error = None;
            assert_eq!(snapshot(&app), before);
            assert_eq!(app.state.units, units);
            assert_eq!(app.state.elevations, elevations);
        }
    }

    #[test]
    fn footwork_button_enabled_state_agrees_with_the_app_actor_in_every_phase() {
        let mut app = App::from_args(launch(TerrainPreset::Flat, true, true, true));
        for step in 0..60 {
            if app.state.outcome().is_some() {
                break;
            }
            let reach: Vec<_> = Vec::new();
            let sc = render::Scene {
                state: &app.state,
                selected: app.human_unit(),
                hover: None,
                reach: &reach,
                log: &[],
                error: None,
                seed: app.seed,
                control_overlay: false,
                height_overlay: false,
                confirm: None,
                targeting: None,
            };
            assert_eq!(render::footwork_enabled(&sc), app.footwork_actor().is_some(), "step {step}");
            assert_eq!(footwork_look(&app) == Some(render::SKILL_ENABLED), app.footwork_actor().is_some(), "step {step}");
            if app.human_unit().is_some() {
                if step % 3 == 0 {
                    app.character_key("3");
                    app.character_key("3");
                }
                app.character_key("e");
            } else {
                app.ai_step();
            }
        }
    }

    // --- Indomitable loadout, button and shortcut ------------------------------------------

    fn indo_args(terrain: TerrainPreset, adrenaline: bool, rotation: bool, footwork: bool) -> LaunchArgs {
        LaunchArgs { indomitable: true, ..launch(terrain, adrenaline, rotation, footwork) }
    }

    /// Only Indomitable: P1 is the active, started player unit with 9 AP and no fatigue.
    fn indo_app() -> App {
        App::from_args(indo_args(TerrainPreset::Flat, false, false, false))
    }

    /// Rotation, Footwork and Indomitable together (targeting can be open).
    fn indo_target_app() -> App {
        App::from_args(indo_args(TerrainPreset::Flat, false, true, true))
    }

    fn indomitable_look(app: &App) -> Option<[f32; 4]> {
        render::skill_fill(&app.frame(), layout::INDOMITABLE_BUTTON)
    }

    /// Number of drawn INDOMITABLE unit-row markers.
    fn frame_indomitable_markers(app: &App) -> usize {
        render::indomitable_marker_rows(&app.frame())
    }

    #[test]
    fn indomitable_launch_argument_reaches_app_and_old_constructors_stay_disabled() {
        for (terrain, terrain_arg) in [(TerrainPreset::Flat, "flat"), (TerrainPreset::Hills, "hills")] {
            let args = scenario::parse_args(["--seed", "6", "--terrain", terrain_arg, "--indomitable"].map(String::from)).unwrap();
            assert!(args.indomitable);
            let app = App::from_args(args);
            assert!(app.indomitable);
            assert_eq!(app.launch_args(6), args);
            assert_eq!(app.state.state_hash(), scenario::launch_battle_for(args).state_hash());
            assert!(app.state.units.iter().all(|u| u.has_indomitable == (u.side == Side::Player) && !u.indomitable_active));
            assert!(!App::with_terrain(6, terrain).indomitable);
            assert!(!App::with_flags(6, terrain, true, true).indomitable);
            assert!(App::with_flags(6, terrain, true, true).state.units.iter().all(|u| !u.has_indomitable));
        }
    }

    #[test]
    fn indomitable_is_stored_and_granted_to_player_units_only_inactive_for_all_512_subsets() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            let base = scenario::battle(11, terrain);
            for bits in 0..512u16 {
                let flag = |i: u32| bits >> i & 1 != 0;
                let args = LaunchArgs {
                    seed: 11,
                    terrain,
                    adrenaline: flag(0),
                    rotation: flag(1),
                    footwork: flag(2),
                    fast_adaptation: flag(3),
                    dodge: flag(4),
                    nine_lives: flag(5),
                    berserk: flag(6),
                    pathfinder: flag(7),
                    indomitable: flag(8),
                    two_hex: false,
                    stunning_blow: false, killing_frenzy: false,
                };
                let app = App::from_args(args);
                assert_eq!(app.indomitable, flag(8), "bits {bits}");
                assert_eq!(app.launch_args(11), args, "bits {bits}");
                assert_eq!(app.state.rng, base.rng, "no RNG consumed: bits {bits}");
                assert!(
                    app.state.units.iter().all(|u| u.has_indomitable == (u.side == Side::Player && flag(8)) && !u.indomitable_active && !u.is_indomitable_active()),
                    "bits {bits}"
                );
                assert_eq!(app.height_overlay, terrain == TerrainPreset::Hills);
            }
        }
    }

    #[test]
    fn indomitable_button_and_shortcut_submit_exactly_the_direct_command() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            let new = || App::from_args(indo_args(terrain, false, false, false));
            let mut direct = new();
            direct.run(Command::Indomitable { unit: 0 });
            assert_eq!(direct.log, vec!["P1 indomitable".to_string()]);

            let mut by_key = new();
            assert!(!press(&mut by_key, ch("4")));
            let mut by_click = new();
            click_at(&mut by_click, center(layout::INDOMITABLE_BUTTON), Instant::now());
            let r = layout::INDOMITABLE_BUTTON;
            let mut corner = new();
            click_at(&mut corner, (r.x + 0.5, r.y + 0.5), Instant::now());
            for app in [&by_key, &by_click, &corner] {
                assert_eq!(app.state.state_hash(), direct.state.state_hash());
                assert_eq!(app.state.rng, direct.state.rng);
                assert_eq!(app.state.queue, direct.state.queue);
                assert_eq!(app.log, direct.log, "exactly one command, one log line");
                assert!(app.error.is_none());
                assert_eq!((app.state.units[0].ap, app.state.units[0].fatigue), (9 - bb_rules::rules::INDOMITABLE_AP, bb_rules::rules::INDOMITABLE_FATIGUE));
                assert!(app.state.units[0].is_indomitable_active());
            }
            let mut outside = new();
            click_at(&mut outside, (r.right() + 0.5, r.y + 0.5), Instant::now());
            assert_ne!(outside.state.state_hash(), direct.state.state_hash());
            // Uppercase 4 is the same key.
            let mut upper = new();
            press(&mut upper, ch("4"));
            assert_eq!(upper.state.state_hash(), direct.state.state_hash());
        }
    }

    #[test]
    fn indomitable_repeated_input_does_not_double_submit_and_the_look_turns_active() {
        let mut app = indo_app();
        assert_eq!(indomitable_look(&app), Some(render::SKILL_ENABLED));
        assert_eq!(frame_indomitable_markers(&app), 0);
        press(&mut app, ch("4"));
        assert_eq!(indomitable_look(&app), Some(render::SKILL_ACTIVE));
        assert_eq!(frame_indomitable_markers(&app), 1);
        let after = snapshot(&app);
        let (log, units) = (app.log.clone(), app.state.units.clone());
        press(&mut app, ch("4"));
        click_at(&mut app, center(layout::INDOMITABLE_BUTTON), Instant::now());
        click_at(&mut app, center(layout::INDOMITABLE_BUTTON), Instant::now());
        assert_eq!(snapshot(&app), after);
        assert_eq!((app.log.clone(), app.state.units.clone()), (log, units));
        assert!(app.error.is_none(), "a repeated activation is a silent no-op");
        assert_eq!(indomitable_look(&app), Some(render::SKILL_ACTIVE), "still active");
        assert_eq!(app.state.units[0].ap, 9 - bb_rules::rules::INDOMITABLE_AP);
        assert!(app.state.units[0].fatigue == bb_rules::rules::INDOMITABLE_FATIGUE);
    }

    #[test]
    fn indomitable_disabled_states_are_silent_no_ops_that_never_reach_the_map() {
        let button = center(layout::INDOMITABLE_BUTTON);
        type Setup = fn(&mut App);
        let cases: [(&str, Setup); 10] = [
            ("no capability", |a| a.state.units[0].has_indomitable = false),
            ("no AP", |a| a.state.units[0].ap = bb_rules::rules::INDOMITABLE_AP - 1),
            ("fatigue cap", |a| a.state.units[0].fatigue = a.state.units[0].stats.fatigue_max - bb_rules::rules::INDOMITABLE_FATIGUE + 1),
            ("fatigue overflow", |a| {
                a.state.units[0].stats.fatigue_max = u32::MAX;
                a.state.units[0].fatigue = u32::MAX;
            }),
            ("enemy turn", |a| a.state.queue = vec![3, 0]),
            ("battle over", |a| a.state.outcome = Some(bb_rules::Outcome::Victory)),
            ("fleeing", |a| a.state.units[0].morale = Morale::Fleeing),
            ("retreating", |a| a.state.units[0].retreating = true),
            ("dead active unit", |a| a.state.units[0].alive = false),
            ("escaped active unit", |a| a.state.units[0].fled = true),
        ];
        for (what, setup) in cases {
            let mut app = indo_app();
            setup(&mut app);
            assert_eq!(app.indomitable_actor(), None, "{what}");
            assert_eq!(indomitable_look(&app), Some(render::SKILL_DISABLED), "{what}");
            let before = snapshot(&app);
            let units = app.state.units.clone();
            press(&mut app, ch("4"));
            click_at(&mut app, button, Instant::now());
            assert_eq!(snapshot(&app), before, "{what}");
            assert_eq!(app.state.units, units, "{what}");
            assert!(app.error.is_none() && app.pending_confirm.is_none() && app.log.is_empty(), "{what}");
        }
        // The exact affordable boundary still passes.
        let mut app = indo_app();
        app.state.units[0].ap = bb_rules::rules::INDOMITABLE_AP;
        app.state.units[0].fatigue = app.state.units[0].stats.fatigue_max - bb_rules::rules::INDOMITABLE_FATIGUE;
        assert_eq!(app.indomitable_actor(), Some(0));
        assert_eq!(indomitable_look(&app), Some(render::SKILL_ENABLED));
        // Without the loadout the button is always disabled.
        let mut app = App::new(1);
        let before = snapshot(&app);
        press(&mut app, ch("4"));
        click_at(&mut app, button, Instant::now());
        assert_eq!(snapshot(&app), before);
        assert!(app.error.is_none() && app.log.is_empty());
        assert_eq!(indomitable_look(&app), Some(render::SKILL_DISABLED));
        // Control: a press just left of the strip's button is a map click, which reports an
        // invalid move, proving the button presses above were not routed to the map.
        let mut control = indo_app();
        let r = layout::INDOMITABLE_BUTTON;
        click_at(&mut control, (r.x - 2.0, r.y + 10.0), Instant::now());
        assert!(control.error.is_some());
    }

    #[test]
    fn indomitable_active_look_needs_the_selected_controllable_active_player() {
        let mut app = indo_app();
        press(&mut app, ch("4"));
        assert_eq!(indomitable_look(&app), Some(render::SKILL_ACTIVE));
        // An enemy's turn selects nobody: the stance is still on the unit, the button is not active.
        app.state.queue.insert(0, 3);
        assert_eq!(app.human_unit(), None);
        assert!(app.state.units[0].is_indomitable_active());
        assert_eq!(indomitable_look(&app), Some(render::SKILL_DISABLED));
        assert_eq!(frame_indomitable_markers(&app), 1, "the unit-row marker follows the unit, not the selection");
        // A Fleeing or retreating active unit keeps no human controls.
        let mut app = indo_app();
        press(&mut app, ch("4"));
        app.state.units[0].retreating = true;
        assert_eq!(indomitable_look(&app), Some(render::SKILL_DISABLED));
        assert_eq!(frame_indomitable_markers(&app), 1, "retained retreat protection stays visible");
        app.state.units[0].morale = Morale::Fleeing;
        assert_eq!(frame_indomitable_markers(&app), 0, "Fleeing removes it through the rules query");
    }

    #[test]
    fn indomitable_modals_consume_the_button_and_shortcut() {
        for retreat in [false, true] {
            let mut app = indo_app();
            if retreat {
                open_retreat(&mut app);
            } else {
                open_dialog(&mut app);
            }
            let (pending, before) = (app.pending_confirm, snapshot(&app));
            press(&mut app, ch("4"));
            app.character_key("4");
            click_at(&mut app, center(layout::INDOMITABLE_BUTTON), Instant::now());
            assert_eq!(app.pending_confirm, pending, "the dialog stays open");
            assert_eq!(snapshot(&app), before);
            assert!(app.error.is_none() && app.log.is_empty());
            assert!(app.state.units.iter().all(|u| !u.indomitable_active));
            // Cancel, then the guard swallows the button until it expires.
            let t0 = Instant::now();
            click_at(&mut app, center(layout::CANCEL_BUTTON), t0);
            click_at(&mut app, center(layout::INDOMITABLE_BUTTON), t0 + MODAL_CLICK_GUARD - Duration::from_millis(1));
            assert!(!app.state.units[0].indomitable_active && app.error.is_none());
            click_at(&mut app, center(layout::INDOMITABLE_BUTTON), t0 + MODAL_CLICK_GUARD);
            assert!(app.state.units[0].indomitable_active);
            assert_eq!(app.log, vec!["P1 indomitable".to_string()]);
        }
        // A button click can neither open a dialog nor confirm one.
        let mut app = indo_app();
        click_at(&mut app, center(layout::INDOMITABLE_BUTTON), Instant::now());
        assert!(app.pending_confirm.is_none());
        assert!(app.state.units[0].indomitable_active);
    }

    #[test]
    fn indomitable_cancels_either_targeting_mode_arms_the_guard_then_activates() {
        for action in ACTIONS {
            for how in ["key", "button"] {
                let mut app = indo_target_app();
                open_action(&mut app, action);
                let t0 = Instant::now();
                if how == "key" {
                    assert!(!app.key(&ch("4"), t0));
                } else {
                    click_at(&mut app, center(layout::INDOMITABLE_BUTTON), t0);
                }
                let what = format!("{action:?}/{how}");
                assert!(app.targeting.is_none(), "{what}");
                assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD), "{what}");
                assert!(app.state.units[0].is_indomitable_active(), "{what}");
                assert_eq!(app.log, vec!["P1 indomitable".to_string()], "{what}: exactly one command");
                // The follow-up map click is swallowed until the guard expires.
                let after = snapshot(&app);
                let reachable = layout::hex_to_pixel(app.state.reachable(0)[0].0);
                click_at(&mut app, reachable, t0 + MODAL_CLICK_GUARD - Duration::from_millis(1));
                assert_eq!(snapshot(&app), after, "{what}: the follow-up click must not Move");
                click_at(&mut app, reachable, t0 + MODAL_CLICK_GUARD);
                assert_ne!(snapshot(&app), after, "{what}: ordinary clicks work again once the guard expires");
            }
        }
        // Unavailable: targeting is still cancelled and the guard armed, but no command runs.
        for how in ["key", "button"] {
            let mut app = indo_target_app();
            app.state.units[0].has_indomitable = false;
            open_action(&mut app, TargetAction::Rotation);
            let t0 = Instant::now();
            let units = app.state.units.clone();
            if how == "key" {
                app.key(&ch("4"), t0);
            } else {
                click_at(&mut app, center(layout::INDOMITABLE_BUTTON), t0);
            }
            assert!(app.targeting.is_none() && app.click_guard_until == Some(t0 + MODAL_CLICK_GUARD), "{how}");
            assert_eq!(app.state.units, units, "{how}");
            assert!(quiet(&app), "{how}");
        }
        // Without targeting nothing arms the guard.
        let mut app = indo_target_app();
        press(&mut app, ch("4"));
        click_at(&mut app, center(layout::INDOMITABLE_BUTTON), Instant::now());
        assert!(app.click_guard_until.is_none());
    }

    #[test]
    fn indomitable_does_not_disturb_the_other_skill_shortcuts_or_preferences() {
        let mut app = indo_target_app();
        // 2 and 3 still open targeting and H/Z stay display preferences.
        open_action(&mut app, TargetAction::Rotation);
        press(&mut app, ch("3"));
        assert_eq!(app.targeting.map(|t| t.action), Some(TargetAction::Footwork));
        press(&mut app, ch("h"));
        press(&mut app, ch("z"));
        assert_eq!(app.targeting.map(|t| t.action), Some(TargetAction::Footwork));
        assert!(app.state.units.iter().all(|u| !u.indomitable_active));
        // The strip hit regions resolve to their own skill.
        for (b, key) in [(layout::ROTATION_BUTTON, "2"), (layout::FOOTWORK_BUTTON, "3")] {
            let mut app = indo_target_app();
            click_at(&mut app, center(b), Instant::now());
            assert_eq!(app.targeting.map(|t| skill_key(t.action)), Some(key));
            assert!(app.state.units.iter().all(|u| !u.indomitable_active) && quiet(&app));
        }
    }

    #[test]
    fn indomitable_persists_through_restart_and_new_seed_with_a_fresh_inactive_stance() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills] {
            let args = LaunchArgs { seed: 17, indomitable: true, ..launch(terrain, true, true, true) };
            let mut app = App::from_args(LaunchArgs { fast_adaptation: true, dodge: true, nine_lives: true, berserk: true, pathfinder: true, ..args });
            let args = app.launch_args(17);
            app.character_key("h");
            app.character_key("z");
            let (height, control) = (app.height_overlay, app.control_overlay);
            for (key, seed) in [("r", 17), ("n", 18), ("n", 19), ("r", 19)] {
                app.character_key("1");
                app.character_key("4");
                assert!(app.state.units[0].is_indomitable_active() && app.state.units[0].adrenaline_pending, "{key}");
                app.character_key("3");
                assert!(app.targeting.is_some());
                app.state.units[1].nine_lives_used = true;
                app.character_key(key);
                assert_eq!((app.seed, app.terrain, app.indomitable), (seed, terrain, true));
                assert_eq!(app.launch_args(seed), LaunchArgs { seed, ..args });
                assert_eq!((app.height_overlay, app.control_overlay), (height, control), "H/Z preferences survive");
                assert!(app.targeting.is_none() && app.pending_confirm.is_none());
                let fresh = scenario::launch_battle_for(LaunchArgs { seed, ..args });
                assert_eq!(app.state.state_hash(), fresh.state_hash());
                assert_eq!(app.state.rng, fresh.rng);
                assert!(app.state.units.iter().all(|u| !u.indomitable_active && !u.adrenaline_pending && !u.nine_lives_used));
                assert!(app.state.units.iter().all(|u| u.has_indomitable == (u.side == Side::Player) && u.ap == fresh.units[u.id as usize].ap && u.fatigue == 0));
                assert_eq!(indomitable_look(&app), Some(render::SKILL_ENABLED));
                assert_eq!(frame_indomitable_markers(&app), 0);
            }
        }
        // An Indomitable-only configuration keeps the other options off; without it R/N stay plain.
        let mut app = App::from_args(LaunchArgs { indomitable: true, ..App::with_terrain(4, TerrainPreset::Hills).launch_args(4) });
        app.character_key("n");
        assert_eq!((app.adrenaline, app.rotation, app.footwork, app.pathfinder, app.indomitable), (false, false, false, false, true));
        let mut app = App::with_terrain(4, TerrainPreset::Hills);
        app.character_key("n");
        assert!(!app.indomitable);
        assert_eq!(app.state.state_hash(), scenario::battle(5, TerrainPreset::Hills).state_hash());
    }

    #[test]
    fn indomitable_button_enabled_state_agrees_with_the_app_actor_in_every_phase() {
        let mut app = App::from_args(indo_args(TerrainPreset::Flat, true, true, true));
        let mut used = 0;
        for step in 0..80 {
            if app.state.outcome().is_some() {
                break;
            }
            let look = indomitable_look(&app);
            let active_shown = app.human_unit().is_some_and(|id| app.state.units[id as usize].is_indomitable_active());
            assert_eq!(look == Some(render::SKILL_ENABLED), app.indomitable_actor().is_some(), "step {step}");
            assert_eq!(look == Some(render::SKILL_ACTIVE), active_shown && app.order_actor().is_some(), "step {step}");
            if app.human_unit().is_some() {
                if step % 4 == 0 {
                    let was = app.indomitable_actor().is_some();
                    let before = app.log.len();
                    app.character_key("4");
                    used += usize::from(was);
                    assert_eq!(app.log.len() - before, usize::from(was), "step {step}: one command or none");
                }
                app.character_key("e");
            } else {
                app.ai_step();
            }
        }
        assert!(used > 0, "the walk exercised at least one activation");
    }

    #[test]
    fn drawing_the_indomitable_ui_changes_nothing() {
        let mut app = indo_app();
        press(&mut app, ch("4"));
        let before = snapshot(&app);
        let units = app.state.units.clone();
        for _ in 0..3 {
            let a = app.frame();
            let b = app.frame();
            assert_eq!(a.len(), b.len());
        }
        assert_eq!(snapshot(&app), before);
        assert_eq!(app.state.units, units);
    }

    // --- Recover button and shortcut ---------------------------------------------------------

    /// Stock Flat battle with a player queue [P1, P2, E1]: P1 is active with 9 AP, P2 acts next.
    fn recover_app(fatigue: u32) -> App {
        let mut app = App::from_args(launch(TerrainPreset::Flat, true, true, true));
        app.state.queue = vec![0, 1, 3];
        app.state.units[0].ap = bb_rules::rules::RECOVER_AP;
        app.state.units[0].fatigue = fatigue;
        let u = &app.state.units[0];
        assert!(u.ap <= u.stats.ap_max && u.fatigue <= u.stats.fatigue_max, "fixture within Stats maxima");
        app
    }

    fn recover_look(app: &App) -> Option<[f32; 4]> {
        render::skill_fill(&app.frame(), layout::RECOVER_BUTTON)
    }

    fn units_and_log(app: &App) -> (Vec<bb_rules::Unit>, Vec<String>) {
        (app.state.units.clone(), app.log.clone())
    }

    #[test]
    fn recover_button_and_shortcut_submit_exactly_one_direct_recover() {
        for fatigue in [0, 1, 40, 41, 99, 100] {
            let mut direct = recover_app(fatigue);
            direct.run(Command::Recover { unit: 0 });
            assert_eq!(direct.state.units[0].fatigue, fatigue / 2);
            assert_eq!(direct.state.units[0].ap, 0);
            assert_eq!(direct.state.active_unit().map(|u| u.id), Some(1));

            let mut by_key = recover_app(fatigue);
            assert_eq!(recover_look(&by_key), Some(render::SKILL_ENABLED), "fatigue {fatigue}");
            assert!(!press(&mut by_key, ch("5")));
            let mut upper = recover_app(fatigue);
            press(&mut upper, ch("5"));
            let mut by_click = recover_app(fatigue);
            click_at(&mut by_click, center(layout::RECOVER_BUTTON), Instant::now());
            let r = layout::RECOVER_BUTTON;
            let mut corner = recover_app(fatigue);
            click_at(&mut corner, (r.x + 0.5, r.y + 0.5), Instant::now());
            for (what, app) in [("key", &by_key), ("upper", &upper), ("click", &by_click), ("corner", &corner)] {
                assert_eq!(app.state.state_hash(), direct.state.state_hash(), "{what} {fatigue}");
                assert_eq!(app.state.rng, direct.state.rng, "{what}");
                assert_eq!(app.state.queue, direct.state.queue, "{what}");
                assert_eq!(units_and_log(app), units_and_log(&direct), "{what}");
                assert!(app.error.is_none() && app.pending_confirm.is_none() && app.targeting.is_none(), "{what}");
            }
            let mut outside = recover_app(fatigue);
            click_at(&mut outside, (r.right() + 0.5, r.y + 0.5), Instant::now());
            assert_ne!(outside.state.state_hash(), direct.state.state_hash());
        }
    }

    #[test]
    fn recover_unavailable_states_are_silent_no_ops_that_never_reach_the_map() {
        type Setup = fn(&mut App);
        let cases: [(&str, Setup); 8] = [
            ("AP 8", |a| a.state.units[0].ap = bb_rules::rules::RECOVER_AP - 1),
            ("AP 0", |a| a.state.units[0].ap = 0),
            ("enemy turn", |a| a.state.queue = vec![3, 0]),
            ("fleeing", |a| a.state.units[0].morale = Morale::Fleeing),
            ("retreating", |a| a.state.units[0].retreating = true),
            ("battle over", |a| a.state.outcome = Some(bb_rules::Outcome::Victory)),
            ("dead active unit", |a| a.state.units[0].alive = false),
            ("escaped active unit", |a| a.state.units[0].fled = true),
        ];
        for (what, setup) in cases {
            let mut app = recover_app(40);
            setup(&mut app);
            assert_eq!(app.recover_actor(), None, "{what}");
            assert_eq!(recover_look(&app), Some(render::SKILL_DISABLED), "{what}");
            let before = snapshot(&app);
            let units = units_and_log(&app);
            press(&mut app, ch("5"));
            click_at(&mut app, center(layout::RECOVER_BUTTON), Instant::now());
            assert_eq!(snapshot(&app), before, "{what}");
            assert_eq!(units_and_log(&app), units, "{what}");
            assert!(app.error.is_none() && app.pending_confirm.is_none(), "{what}");
            assert_eq!(app.click_guard_until, None, "{what}: nothing completed, so no guard");
        }
        // Control: a press just left of the button is a map click, which reports an invalid move,
        // proving the presses above were consumed by the (disabled) button.
        let mut control = recover_app(40);
        let r = layout::RECOVER_BUTTON;
        click_at(&mut control, (r.x - 2.0, r.y + 10.0), Instant::now());
        assert!(control.error.is_some());
    }

    #[test]
    fn recover_zero_fatigue_and_the_exact_ap_boundary_are_eligible() {
        let app = recover_app(0);
        assert_eq!(app.recover_actor(), Some(0));
        let mut app = recover_app(0);
        press(&mut app, ch("5"));
        assert_eq!(app.state.units[0].fatigue, 0);
        assert_eq!(app.state.units[0].ap, 0);
        assert_eq!(app.state.active_unit().map(|u| u.id), Some(1));
        // The AP row is the feedback: the actor shows 0 AP, and the next unit is active.
        assert_eq!(recover_look(&app), Some(render::SKILL_ENABLED), "P2 can Recover too");
    }

    #[test]
    fn recover_arms_the_click_guard_from_the_input_instant_and_protects_the_next_unit() {
        for how in ["key", "button"] {
            let mut app = recover_app(40);
            let t0 = Instant::now();
            if how == "key" {
                assert!(!app.key(&ch("5"), t0));
            } else {
                click_at(&mut app, center(layout::RECOVER_BUTTON), t0);
            }
            assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD), "{how}");
            assert_eq!(app.state.active_unit().map(|u| u.id), Some(1), "{how}");
            let after = snapshot(&app);
            let units = units_and_log(&app);
            let inside = t0 + MODAL_CLICK_GUARD - Duration::from_millis(1);
            // A second button click and a map click inside the window do nothing.
            click_at(&mut app, center(layout::RECOVER_BUTTON), inside);
            let target = layout::hex_to_pixel(app.state.reachable(1)[0].0);
            click_at(&mut app, target, inside);
            assert_eq!(snapshot(&app), after, "{how}: no Recover and no Move for the next unit");
            assert_eq!(units_and_log(&app), units, "{how}");
            assert!(app.error.is_none(), "{how}");
            // Ordinary clicks work at guard expiry.
            click_at(&mut app, target, t0 + MODAL_CLICK_GUARD);
            assert_ne!(snapshot(&app), after, "{how}: the next unit moves once the guard expires");
            assert!(app.state.units[1].ap < app.state.units[1].stats.ap_max);
        }
        // At expiry the Recover button works again for the new unit.
        let mut app = recover_app(40);
        let t0 = Instant::now();
        click_at(&mut app, center(layout::RECOVER_BUTTON), t0);
        click_at(&mut app, center(layout::RECOVER_BUTTON), t0 + MODAL_CLICK_GUARD);
        assert_eq!(app.state.units[1].ap, 0);
        assert_eq!(app.state.active_unit().map(|u| u.id), Some(3));
    }

    #[test]
    fn recover_cancels_targeting_through_the_existing_path_then_submits_one_command() {
        for action in ACTIONS {
            for how in ["key", "button"] {
                let mut direct = recover_app(41);
                direct.run(Command::Recover { unit: 0 });
                let mut app = recover_app(41);
                open_action(&mut app, action);
                let t0 = Instant::now();
                if how == "key" {
                    assert!(!app.key(&ch("5"), t0));
                } else {
                    click_at(&mut app, center(layout::RECOVER_BUTTON), t0);
                }
                let what = format!("{action:?}/{how}");
                assert!(app.targeting.is_none(), "{what}");
                assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD), "{what}");
                assert_eq!(app.state.state_hash(), direct.state.state_hash(), "{what}");
                assert_eq!(app.state.rng, direct.state.rng, "{what}");
                assert_eq!(units_and_log(&app), units_and_log(&direct), "{what}: exactly one Recover");
                assert!(app.error.is_none(), "{what}");
            }
        }
        // Unavailable: targeting is still cancelled and the guard armed, but no command runs.
        for how in ["key", "button"] {
            let mut app = recover_app(41);
            app.state.units[0].ap = bb_rules::rules::RECOVER_AP - 1;
            open_action(&mut app, TargetAction::Rotation);
            let before = (app.state.state_hash(), app.state.rng.clone());
            let t0 = Instant::now();
            if how == "key" {
                app.key(&ch("5"), t0);
            } else {
                click_at(&mut app, center(layout::RECOVER_BUTTON), t0);
            }
            assert!(app.targeting.is_none(), "{how}");
            assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD), "{how}");
            assert_eq!((app.state.state_hash(), app.state.rng.clone()), before, "{how}");
            assert!(app.log.is_empty() && app.error.is_none(), "{how}");
        }
    }

    #[test]
    fn confirmation_dialogs_consume_the_recover_shortcut_and_button() {
        for key in ["x", "v"] {
            let mut app = recover_app(41);
            press(&mut app, ch(key));
            let pending = app.pending_confirm;
            assert!(pending.is_some());
            let before = snapshot(&app);
            let units = units_and_log(&app);
            assert!(!press(&mut app, ch("5")));
            click_at(&mut app, center(layout::RECOVER_BUTTON), Instant::now());
            assert_eq!(app.pending_confirm, pending, "{key}: the dialog stays open");
            assert_eq!(snapshot(&app), before, "{key}");
            assert_eq!(units_and_log(&app), units, "{key}");
            assert_eq!(app.click_guard_until, None, "{key}");
            assert!(app.error.is_none(), "{key}");
        }
    }

    #[test]
    fn recover_adds_no_ui_state_and_a_held_key_is_dropped_before_it_acts() {
        assert!(key_press_accepted(ElementState::Pressed, false));
        assert!(!key_press_accepted(ElementState::Pressed, true), "auto-repeat is dropped before key()");
        assert!(!key_press_accepted(ElementState::Released, false));
        // Recover leaves no pending dialog or targeting behind, and drawing is pure.
        let mut app = recover_app(41);
        press(&mut app, ch("5"));
        assert!(app.pending_confirm.is_none() && app.targeting.is_none());
        let before = snapshot(&app);
        let units = units_and_log(&app);
        for _ in 0..3 {
            let (a, b) = (app.frame(), app.frame());
            assert_eq!(a.len(), b.len());
        }
        assert_eq!(snapshot(&app), before);
        assert_eq!(units_and_log(&app), units);
    }

    #[test]
    fn recover_rows_show_the_halved_fatigue_and_forfeited_ap_as_feedback() {
        let mut app = recover_app(41);
        app.draw();
        press(&mut app, ch("5"));
        let row = |id: usize| {
            let u = &app.state.units[id];
            format!("AP {}/{} FAT {}/{}", u.ap, u.stats.ap_max, u.fatigue, u.stats.fatigue_max)
        };
        assert_eq!(row(0), "AP 0/9 FAT 20/100");
        assert_eq!(app.state.active_unit().map(|u| u.id), Some(1));
        let verts = app.frame();
        assert!(!verts.is_empty());
    }

    // --- --two-hex launch loadout (APPROXIMATION: player-only MeleeReach::TwoHex) -------

    const ALL_NINE: [&str; 9] = [
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

    fn parsed(argv: &[&str]) -> LaunchArgs {
        scenario::parse_args(argv.iter().map(|a| a.to_string())).unwrap()
    }

    fn assert_player_only_two_hex(app: &App) {
        for u in &app.state.units {
            let want = if u.side == Side::Player { bb_rules::MeleeReach::TwoHex } else { bb_rules::MeleeReach::Adjacent };
            assert_eq!(u.melee_reach, want, "{}", u.name);
        }
    }

    #[test]
    fn two_hex_launch_argument_reaches_app_and_old_constructors_stay_adjacent() {
        for (terrain, arg) in [(TerrainPreset::Flat, "flat"), (TerrainPreset::Hills, "hills"), (TerrainPreset::Swamp, "swamp")] {
            let args = parsed(&["--seed", "6", "--terrain", arg, "--two-hex", "--two-hex"]);
            assert!(args.two_hex);
            let app = App::from_args(args);
            assert!(app.two_hex);
            assert_eq!(app.launch_args(6), args);
            assert_player_only_two_hex(&app);
            assert_eq!(app.state.state_hash(), scenario::launch_battle_for(args).state_hash());
            let plain = scenario::battle(6, terrain);
            assert_eq!(app.state.rng, plain.rng, "no RNG consumed");
            assert_eq!(app.state.queue, plain.queue);
            for (u, base) in app.state.units.iter().zip(&plain.units) {
                assert_eq!((&u.stats, u.pos, u.hp, u.armour, u.ap, u.fatigue), (&base.stats, base.pos, base.hp, base.armour, base.ap, base.fatigue));
            }
            // Without the flag every compatibility constructor stays adjacent-only.
            for old in [App::new(6), App::with_terrain(6, terrain), App::with_loadout(6, terrain, true), App::with_flags(6, terrain, true, true), App::from_args(parsed(&["--terrain", arg]))] {
                assert!(!old.two_hex);
                assert!(old.state.units.iter().all(|u| u.melee_reach == bb_rules::MeleeReach::Adjacent));
                assert!(!old.launch_args(6).two_hex);
            }
        }
    }

    #[test]
    fn two_hex_survives_restart_and_new_seed_with_fresh_state_and_every_other_selection() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills, TerrainPreset::Swamp] {
            let arg = match terrain {
                TerrainPreset::Flat => "flat",
                TerrainPreset::Hills => "hills",
                TerrainPreset::Swamp => "swamp",
            };
            for with_all in [false, true] {
                let mut argv = vec!["--seed", "17", "--terrain", arg, "--two-hex"];
                if with_all {
                    argv.extend(ALL_NINE);
                }
                let args = parsed(&argv);
                let mut app = App::from_args(args);
                app.character_key("h");
                app.character_key("z");
                let preferences = (app.height_overlay, app.control_overlay);
                // R keeps the seed; N advances it by one (18, then 19 after the next N).
                for (key, seed) in [("r", 17), ("n", 18), ("r", 18), ("n", 19)] {
                    let actor = &mut app.state.units[0];
                    actor.ap = 1;
                    actor.fatigue = 73;
                    actor.hp = 1;
                    actor.pos = bb_rules::hex(-1, 3);
                    actor.adrenaline_pending = actor.has_adrenaline;
                    actor.fast_adaptation_stacks = if actor.has_fast_adaptation { 4 } else { 0 };
                    actor.nine_lives_used = actor.has_nine_lives;
                    actor.indomitable_active = actor.has_indomitable;
                    // Scripted tampering with reach is also undone by the rebuild.
                    app.state.units[3].melee_reach = bb_rules::MeleeReach::TwoHex;
                    app.state.units[1].melee_reach = bb_rules::MeleeReach::Adjacent;
                    app.log.push("stale".into());
                    app.error = Some(("stale".into(), Instant::now()));
                    app.character_key(key);
                    let fresh_args = LaunchArgs { seed, ..args };
                    let fresh = scenario::launch_battle_for(fresh_args);
                    assert!(app.two_hex);
                    assert_eq!(app.launch_args(seed), fresh_args);
                    assert_eq!((app.seed, app.terrain), (seed, terrain));
                    assert_eq!((app.height_overlay, app.control_overlay), preferences, "H and Z preferences survive");
                    assert_player_only_two_hex(&app);
                    assert_eq!(app.state.units, fresh.units, "initial resources and temporary state are restored");
                    assert_eq!(app.state.rng, fresh.rng);
                    assert_eq!(app.state.state_hash(), fresh.state_hash());
                    assert!(app.log.is_empty() && app.error.is_none());
                    assert_eq!(app.state.units[0].has_dodge, with_all);
                }
            }
        }
    }

    /// P1 (-1,-2) active, ally P2 at (0,-2) between it and E1 at (1,-2); the rest parked.
    fn behind_ally_app(argv: &[&str]) -> App {
        let mut app = App::from_args(parsed(argv));
        let parked = [bb_rules::hex(-4, 2), bb_rules::hex(-4, 3), bb_rules::hex(-4, 4), bb_rules::hex(4, -4), bb_rules::hex(4, -3), bb_rules::hex(4, -2)];
        for (u, at) in app.state.units.iter_mut().zip(parked) {
            u.pos = at;
        }
        app.state.units[0].pos = bb_rules::hex(-1, -2);
        app.state.units[1].pos = bb_rules::hex(0, -2);
        app.state.units[3].pos = bb_rules::hex(1, -2);
        app.state.queue.retain(|&id| id != 0);
        app.state.queue.insert(0, 0);
        app.state.units[0].ap = app.state.units[0].stats.ap_max;
        app.state.units[0].turn_started = true;
        let spots: Vec<_> = app.state.units.iter().map(|u| u.pos).collect();
        for (i, at) in spots.iter().enumerate() {
            assert!(bb_rules::hex::map_hexes().contains(at) && !at.is_obstacle() && !spots[..i].contains(at), "{at:?}");
        }
        for u in &app.state.units {
            assert!(u.hp <= u.stats.hp && u.armour <= u.stats.armour && u.ap <= u.stats.ap_max && u.fatigue <= u.stats.fatigue_max);
        }
        assert_eq!(app.human_unit(), Some(0));
        app
    }

    fn attack_rows(app: &App, target: u32) -> Option<[String; 3]> {
        let plan = app.state.preview_attack(0, target).ok()?;
        Some([
            format!("ATTACK {}: HIT {}%", app.name(target), plan.hit_chance),
            format!("COST {} AP {} FAT", plan.ap_cost, plan.fatigue_cost),
            format!("DAMAGE ON HIT {}-{}", plan.damage_min, plan.damage_max),
        ])
    }

    fn shown_rows(app: &App, rows: &[String; 3]) -> usize {
        let frame = app.frame();
        rows.iter().enumerate().filter(|(i, r)| render::preview_row_shown(&frame, *i, r)).count()
    }

    #[test]
    fn distance_two_hover_and_click_attack_from_behind_an_ally_matches_the_direct_command() {
        for terrain_arg in ["flat", "hills", "swamp"] {
            let mut app = behind_ally_app(&["--seed", "5", "--terrain", terrain_arg, "--two-hex"]);
            let now = Instant::now();
            assert_eq!(app.state.units[0].pos.distance(app.state.units[3].pos), 2);
            let rows = attack_rows(&app, 3).expect("rules preview at distance two");
            // Hover only: rules-derived attack information, nothing submitted.
            app.mouse = px(&app, 3);
            let (hash, rng) = (app.state.state_hash(), app.state.rng.clone());
            assert_eq!(shown_rows(&app, &rows), 3, "{terrain_arg}: preview rows from preview_attack");
            assert_eq!((app.state.state_hash(), &app.state.rng), (hash, &rng));
            assert!(app.log.is_empty() && app.error.is_none());

            let mut direct = app.state.clone();
            let direct_events = direct.apply(Command::Attack { attacker: 0, target: 3 }).expect("direct attack");
            let start = app.state.units[0].pos;
            let ap_before = app.state.units[0].ap;
            let pt = px(&app, 3);
            click_at(&mut app, pt, now);
            assert!(app.error.is_none(), "{:?}", app.error);
            assert_eq!(app.state.units[0].pos, start, "no movement first");
            assert!(ap_before > app.state.units[0].ap);
            assert_eq!(app.state.units, direct.units);
            assert_eq!(app.state.rng, direct.rng);
            assert_eq!(app.state.state_hash(), direct.state_hash());
            assert!(direct_events.iter().any(|e| matches!(e, Event::Attacked { attacker: 0, target: 3, .. })));
            assert!(!direct_events.iter().any(|e| matches!(e, Event::Moved { .. })));
            assert!(app.log.iter().any(|l| l.starts_with("P1 hits E1") || l.starts_with("P1 misses E1")), "{:?}", app.log);
        }
    }

    #[test]
    fn adjacent_only_twin_and_distance_three_targets_offer_no_attack_and_submit_nothing() {
        let now = Instant::now();
        // The same geometry without --two-hex: no information, a click is rejected and changes nothing.
        let mut twin = behind_ally_app(&["--seed", "5"]);
        assert!(attack_rows(&twin, 3).is_none());
        twin.mouse = px(&twin, 3);
        let rows = [format!("ATTACK E1: HIT {}%", twin.state.hit_chance(0, 3)), String::new(), String::new()];
        assert!(!render::preview_row_shown(&twin.frame(), 0, &rows[0]), "no attack information for an out-of-reach target");
        let (hash, rng, units) = (twin.state.state_hash(), twin.state.rng.clone(), twin.state.units.clone());
        let pt = px(&twin, 3);
            click_at(&mut twin, pt, now);
        assert!(twin.error.is_some());
        assert_eq!((twin.state.state_hash(), &twin.state.rng, &twin.state.units), (hash, &rng, &units));

        // Distance three stays outside the two-hex reach.
        let mut far = behind_ally_app(&["--seed", "5", "--two-hex"]);
        far.state.units[3].pos = bb_rules::hex(2, -2);
        assert_eq!(far.state.units[0].pos.distance(far.state.units[3].pos), 3);
        assert!(attack_rows(&far, 3).is_none());
        far.mouse = px(&far, 3);
        assert!(!render::preview_row_shown(&far.frame(), 0, &format!("ATTACK E1: HIT {}%", far.state.hit_chance(0, 3))));
        let (hash, rng, units) = (far.state.state_hash(), far.state.rng.clone(), far.state.units.clone());
        let pt = px(&far, 3);
            click_at(&mut far, pt, now);
        assert!(far.error.is_some());
        assert_eq!((far.state.state_hash(), &far.state.rng, &far.state.units), (hash, &rng, &units));
    }

    #[test]
    fn two_hex_attack_input_keeps_modal_isolation_and_the_click_guard() {
        let now = Instant::now();
        let mut app = behind_ally_app(&["--seed", "5", "--two-hex"]);
        // An open confirmation consumes the map click: no attack is submitted.
        open_dialog(&mut app);
        let before = (app.state.state_hash(), app.state.rng.clone());
        let pt = px(&app, 3);
            click_at(&mut app, pt, now);
        assert_eq!((app.state.state_hash(), &app.state.rng), (before.0, &before.1));
        press(&mut app, named(NamedKey::Escape));
        // The click guard armed by closing the dialog swallows the follow-up click.
        let pt = px(&app, 3);
            click_at(&mut app, pt, now);
        assert_eq!((app.state.state_hash(), &app.state.rng), (before.0, &before.1));
        // Once the guard has expired the same click attacks.
        let pt = px(&app, 3);
            click_at(&mut app, pt, now + MODAL_CLICK_GUARD + Duration::from_millis(1));
        assert_ne!(app.state.state_hash(), before.0);
        assert!(app.log.iter().any(|l| l.contains("E1")));
    }

    #[test]
    fn two_hex_preview_and_frame_building_consume_no_rng_and_change_no_state() {
        let mut app = behind_ally_app(&["--seed", "5", "--two-hex"]);
        app.mouse = px(&app, 3);
        let (hash, rng, units, queue) = (app.state.state_hash(), app.state.rng.clone(), app.state.units.clone(), app.state.queue.clone());
        let (a, b) = (app.frame(), app.frame());
        assert_eq!(a.len(), b.len());
        assert_eq!((app.state.state_hash(), &app.state.rng, &app.state.units, &app.state.queue), (hash, &rng, &units, &queue));
    }

    #[test]
    fn two_hex_distance_two_target_shows_unavailable_rows_and_obeys_targeting_precedence() {
        let now = Instant::now();
        // Resource-unavailable rows come from the same rules errors as at distance one.
        for (ap, fatigue, row) in [(1u32, 0u32, "UNAVAILABLE: NOT ENOUGH AP"), (9, 100, "UNAVAILABLE: TOO FATIGUED")] {
            let mut app = behind_ally_app(&["--seed", "5", "--two-hex"]);
            app.state.units[0].ap = ap;
            app.state.units[0].fatigue = fatigue;
            app.mouse = px(&app, 3);
            let frame = app.frame();
            let head = format!("ATTACK E1: HIT {}%", app.state.hit_chance(0, 3));
            assert!(render::preview_row_shown(&frame, 0, &head) && render::preview_row_shown(&frame, 1, row), "{row}");
            let (hash, rng) = (app.state.state_hash(), app.state.rng.clone());
            let pt = px(&app, 3);
            click_at(&mut app, pt, now);
            assert!(app.error.is_some());
            assert_eq!((app.state.state_hash(), &app.state.rng), (hash, &rng));
        }
        // Open Rotation targeting takes precedence: an enemy click is not an attack.
        let mut app = behind_ally_app(&["--seed", "5", "--two-hex", "--rotation"]);
        press(&mut app, ch("2"));
        assert!(app.targeting.is_some());
        let (hash, rng) = (app.state.state_hash(), app.state.rng.clone());
        let pt = px(&app, 3);
        click_at(&mut app, pt, now);
        assert_eq!((app.state.state_hash(), &app.state.rng), (hash, &rng), "no attack while targeting");
    }

    #[test]
    fn stunning_blow_status_is_logged_in_event_order_and_lives_through_the_skip() {
        let mut app = stun_app();
        force_stunning_blow_result(&mut app, true);
        app.run(Command::StunningBlow { attacker: 0, target: 3 });
        assert_eq!(app.log.len(), 2, "the hit event precedes exactly one application line: {:?}", app.log);
        assert!(app.log[0].starts_with("P1 hits E1"), "the attack actually hit: {:?}", app.log);
        assert_eq!(app.log[1], "E1 stunned");
        assert!(app.state.units[3].stunned && app.state.units[3].alive);
        assert_eq!(app.state.queue, vec![0, 3, 1], "stunning an inactive target does not advance the attacker");
        assert!(has_stun_marker(&app), "the successful hit draws the target marker");

        let log_before = app.log.len();
        app.state.units[0].ap = app.state.units[0].stats.ap_max;
        app.state.units[0].fatigue = 0;
        force_stunning_blow_result(&mut app, true);
        app.run(Command::StunningBlow { attacker: 0, target: 3 });
        assert_eq!(app.log.len(), log_before + 1, "the second real hit logs, but idempotent reapplication emits no status event");
        assert!(app.log.last().unwrap().starts_with("P1 hits E1"));
        assert_eq!(app.log.iter().filter(|line| line.as_str() == "E1 stunned").count(), 1);
        assert!(app.state.units[3].stunned && has_stun_marker(&app));

        app.run(Command::EndTurn { unit: 0 });
        assert_eq!(app.state.queue, vec![1], "normal advancement consumes E1's queue entry");
        assert_eq!(app.state.active_unit().map(|u| u.id), Some(1));
        assert!(!app.state.units[3].stunned);
        assert!(!has_stun_marker(&app), "expiry removes the marker");
        assert_eq!(app.log[3], "E1 skips turn: stun ends");
        assert_eq!(app.log.iter().filter(|line| line.contains("stun")).count(), 2);
        assert!(app.log[1].chars().count() <= 31 && app.log[3].chars().count() <= 31);
    }

    #[test]
    fn an_effective_indomitable_target_resists_a_real_hit_without_a_marker() {
        let mut app = stun_app();
        app.state.units[3].has_indomitable = true;
        app.state.units[3].indomitable_active = true;
        force_stunning_blow_result(&mut app, true);
        app.run(Command::StunningBlow { attacker: 0, target: 3 });
        assert_eq!(app.log.len(), 2, "hit then resistance in event order: {:?}", app.log);
        assert!(app.log[0].starts_with("P1 hits E1"));
        assert_eq!(app.log[1], "E1 resists stun");
        assert!(!app.state.units[3].stunned);
        assert!(!has_stun_marker(&app), "a resistance event has no STUN marker");
    }

    #[test]
    fn stunning_blow_miss_and_lethal_hit_emit_no_false_stun_status() {
        let mut miss = stun_app();
        force_stunning_blow_result(&mut miss, false);
        miss.run(Command::StunningBlow { attacker: 0, target: 3 });
        assert!(miss.log[0].starts_with("P1 misses E1"), "the deterministic stream misses");
        assert!(miss.log.iter().all(|line| !line.contains("stun")), "miss has no status event: {:?}", miss.log);
        assert!(!miss.state.units[3].stunned && !has_stun_marker(&miss));

        let mut lethal = stun_app();
        lethal.state.units[3].hp = 1;
        lethal.state.units[3].armour = 0;
        lethal.state.units[0].stats.dmg_min = 20;
        lethal.state.units[0].stats.dmg_max = 20;
        force_stunning_blow_result(&mut lethal, true);
        lethal.run(Command::StunningBlow { attacker: 0, target: 3 });
        assert!(!lethal.state.units[3].alive, "the deterministic hit is lethal");
        assert!(lethal.log.iter().any(|line| line == "E1 dies"));
        assert!(lethal.log.iter().all(|line| !line.contains("stun")), "lethal hit has no status event: {:?}", lethal.log);
        assert!(!lethal.state.units[3].stunned && !has_stun_marker(&lethal));
    }

    #[test]
    fn every_new_stock_name_status_line_fits_the_log_and_uses_supported_glyphs() {
        for name in ["P1", "P2", "P3", "E1", "E2", "E3"] {
            for line in [format!("{name} stunned"), format!("{name} skips turn: stun ends"), format!("{name} resists stun")] {
                assert!(line.chars().count() <= 31, "{line:?} exceeds the existing log limit");
                for ch in line.chars() {
                    assert!(font::glyph(ch).iter().any(|&row| row != 0) || ch == ' ', "{ch:?} has no font glyph in {line:?}");
                }
            }
        }
    }


    // --- Stunning Blow button, shortcut and enemy targeting ---------------------------------

    /// `stun_app` (P1 (0,-2) with a programmatically granted Stunning Blow, E1 (1,-2) adjacent,
    /// P2 far) plus Adrenaline, Rotation and Footwork, with P2 moved next to P1 (0,-1).
    fn trio_app() -> App {
        let mut app = stun_app();
        app.state.units[0].has_adrenaline = true;
        app.state.units[0].has_rotation = true;
        app.state.units[0].has_footwork = true;
        app.state.units[1].pos = bb_rules::hex(0, -1);
        assert_stun_fixture_valid(&app.state);
        assert_eq!(app.state.legal_stunning_blow_targets(0), vec![3]);
        assert_eq!(app.state.legal_rotation_targets(0), vec![1]);
        assert!(!app.state.legal_footwork_destinations(0).is_empty());
        app
    }

    const MODES: [TargetAction; 3] = [TargetAction::Rotation, TargetAction::Footwork, TargetAction::StunningBlow];

    fn blow_look(app: &App) -> Option<[f32; 4]> {
        skill_look(app, TargetAction::StunningBlow)
    }

    fn log_count(app: &App, what: &str) -> usize {
        app.log.iter().filter(|l| l.contains(what)).count()
    }

    #[test]
    fn stunning_blow_launch_option_defaults_off_and_default_apps_keep_their_capabilities() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills, TerrainPreset::Swamp] {
            let app = App::with_terrain(1, terrain);
            assert!(app.state.units.iter().all(|u| !u.has_stunning_blow));
            assert_eq!(app.state.state_hash(), scenario::battle(1, terrain).state_hash());
            assert_eq!(blow_look(&app), Some(render::SKILL_DISABLED));
            assert_eq!(app.stunning_blow_actor(), None);
        }
        assert_eq!(stun_app().stunning_blow_actor(), Some(0));
        assert_eq!(blow_look(&stun_app()), Some(render::SKILL_ENABLED));
    }

    #[test]
    fn parsed_stunning_blow_loadout_uses_the_existing_controls_and_matches_the_direct_command() {
        for terrain in [TerrainPreset::Flat, TerrainPreset::Hills, TerrainPreset::Swamp] {
            for use_button in [false, true] {
                let (mut app, args) = parsed_stun_app(terrain);
                let (mut direct, _) = parsed_stun_app(terrain);
                assert!(args.stunning_blow);
                assert_eq!(app.launch_args(1), args);
                assert_eq!(app.stunning_blow_actor(), Some(0));
                assert!(app.state.units.iter().all(|unit| {
                    unit.has_stunning_blow == (unit.side == Side::Player) && !unit.stunned
                }));

                force_stunning_blow_result(&mut app, true);
                force_stunning_blow_result(&mut direct, true);
                direct.run(Command::StunningBlow { attacker: 0, target: 3 });

                let started = Instant::now();
                if use_button {
                    click_at(&mut app, center(layout::STUNNING_BLOW_BUTTON), started);
                } else {
                    assert!(!press(&mut app, ch("6")));
                }
                assert_eq!(app.targeting.map(|intent| intent.action), Some(TargetAction::StunningBlow));
                assert_eq!(app.state.units.iter().filter(|unit| unit.stunned).count(), 0, "targeting itself has no status effect");
                click_unit(&mut app, 3, started + Duration::from_millis(1));

                assert!(app.targeting.is_none());
                assert_eq!(app.state.units, direct.state.units, "{terrain:?}, button={use_button}");
                assert_eq!(app.state.rng, direct.state.rng);
                assert_eq!(app.state.queue, direct.state.queue);
                assert_eq!(app.state.outcome, direct.state.outcome);
                assert_eq!(app.log, direct.log);
                assert!(app.state.units[3].stunned, "the successful hit applies the accepted effect");
            }
        }
    }

    #[test]
    fn stunning_blow_option_persists_through_r_n_and_cancels_open_targeting() {
        for key in ["r", "n"] {
            let (mut app, args) = parsed_stun_app(TerrainPreset::Flat);
            open_action(&mut app, TargetAction::StunningBlow);
            assert!(app.targeting.is_some());
            let t0 = Instant::now();
            assert!(!app.key(&ch(key), t0));
            let seed = if key == "r" { 1 } else { 2 };
            let fresh_args = LaunchArgs { seed, ..args };
            let fresh = scenario::launch_battle_for(fresh_args);
            assert_eq!(app.launch_args(seed), fresh_args, "{key}");
            assert_eq!(app.state.units, fresh.units, "{key}: capability returns without effects");
            assert_eq!(app.state.state_hash(), fresh.state_hash());
            assert_eq!(app.state.rng, fresh.rng);
            assert!(app.state.units.iter().all(|unit| {
                unit.has_stunning_blow == (unit.side == Side::Player) && !unit.stunned
            }));
            assert!(app.targeting.is_none() && app.pending_confirm.is_none());
            assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD), "{key}: targeting cancellation keeps its guard");
        }

    }

    #[test]
    fn restart_clears_a_pending_confirmation_and_restores_the_stunning_blow_option() {
        // While a confirmation is open, keyboard R/N is consumed by the modal. Exercise the
        // same restart path directly to verify it clears the pending intent and restores the
        // selected launch capability as a fresh, unstunned battle.
        for (key, next_seed) in [("r", 1), ("n", 2)] {
            let args = scenario::parse_args(["--seed", "1", "--stunning-blow"].map(String::from)).unwrap();
            let mut app = App::from_args(args);
            app.character_key("x");
            assert_eq!(app.pending_confirm.map(|pending| pending.action), Some(ConfirmAction::EndRound));
            app.restart_cancelling_targeting(next_seed, Instant::now());
            let fresh_args = LaunchArgs { seed: next_seed, ..args };
            let fresh = scenario::launch_battle_for(fresh_args);
            assert_eq!(app.launch_args(next_seed), fresh_args);
            assert_eq!(app.state.units, fresh.units);
            assert!(app.state.units.iter().all(|unit| unit.has_stunning_blow == (unit.side == Side::Player) && !unit.stunned));
            assert!(app.pending_confirm.is_none());
            assert_eq!(app.seed, next_seed, "{key}");
        }
    }

    #[test]
    fn stunning_blow_button_and_shortcut_open_targeting_without_submitting_a_command() {
        for how in 0..4 {
            let mut app = stun_app();
            let (before, units) = (snapshot(&app), app.state.units.clone());
            let expected = PendingTargeting { action: TargetAction::StunningBlow, actor: 0, round: 1, state_hash: app.state.state_hash() };
            match how {
                0 => assert!(!press(&mut app, ch("6"))),
                1 => app.character_key("6"),
                2 => click_at(&mut app, center(layout::STUNNING_BLOW_BUTTON), Instant::now()),
                _ => {
                    let r = layout::STUNNING_BLOW_BUTTON;
                    click_at(&mut app, (r.x + 0.5, r.y + 0.5), Instant::now());
                }
            }
            assert_eq!(app.targeting, Some(expected), "how {how}");
            assert_eq!(snapshot(&app), before, "no command: state, RNG, queue and log are untouched");
            assert_eq!(app.state.units, units);
            assert!(quiet(&app) && app.pending_confirm.is_none() && app.click_guard_until.is_none());
            assert!(frame_shows(&app, TargetAction::StunningBlow));
            assert_eq!(blow_look(&app), Some(render::SKILL_ACTIVE));
        }
        // Outside the rectangle it is an ordinary map click.
        let mut app = stun_app();
        let r = layout::STUNNING_BLOW_BUTTON;
        click_at(&mut app, (r.right() + 0.5, r.y + 0.5), Instant::now());
        assert!(app.targeting.is_none() && app.error.is_some());
    }

    #[test]
    fn unavailable_stunning_blow_is_a_silent_no_op_that_never_reaches_the_map() {
        type Setup = fn(&mut App);
        let cases: [(&str, Setup); 15] = [
            ("no capability", |a| a.state.units[0].has_stunning_blow = false),
            ("not enough AP", |a| a.state.units[0].ap = STUNNING_BLOW_AP - 1),
            ("fatigue cap", |a| a.state.units[0].fatigue = a.state.units[0].stats.fatigue_max - STUNNING_BLOW_FATIGUE + 1),
            ("fatigue overflow", |a| {
                a.state.units[0].stats.fatigue_max = u32::MAX;
                a.state.units[0].fatigue = u32::MAX;
            }),
            ("no adjacent enemy", |a| a.state.units[3].pos = bb_rules::hex(0, 2)),
            ("enemy dead", |a| a.state.units[3].alive = false),
            ("enemy escaped", |a| a.state.units[3].fled = true),
            ("enemy turn", |a| a.state.queue = vec![3, 0, 1]),
            ("battle over", |a| a.state.outcome = Some(bb_rules::Outcome::Victory)),
            ("fleeing", |a| a.state.units[0].morale = Morale::Fleeing),
            ("retreating", |a| a.state.units[0].retreating = true),
            ("dead active unit", |a| a.state.units[0].alive = false),
            ("escaped active unit", |a| a.state.units[0].fled = true),
            ("only an ally adjacent", |a| a.state.units[3].pos = bb_rules::hex(3, -2)),
            ("only a TwoHex-distance enemy", |a| {
                a.state.units[0].melee_reach = bb_rules::MeleeReach::TwoHex;
                a.state.units[3].pos = bb_rules::hex(2, -2);
            }),
        ];
        for (what, setup) in cases {
            let mut app = stun_app();
            setup(&mut app);
            assert_eq!(app.stunning_blow_actor(), None, "{what}");
            assert_eq!(blow_look(&app), Some(render::SKILL_DISABLED), "{what}");
            let (before, units) = (snapshot(&app), app.state.units.clone());
            press(&mut app, ch("6"));
            app.character_key("6");
            click_at(&mut app, center(layout::STUNNING_BLOW_BUTTON), Instant::now());
            assert!(app.targeting.is_none(), "{what}");
            assert_eq!(snapshot(&app), before, "{what}");
            assert_eq!(app.state.units, units, "{what}");
            assert!(quiet(&app) && app.pending_confirm.is_none(), "{what}");
        }
        // The AP/fatigue boundaries are inclusive: exactly enough is available.
        let mut app = stun_app();
        app.state.units[0].ap = STUNNING_BLOW_AP;
        app.state.units[0].fatigue = app.state.units[0].stats.fatigue_max - STUNNING_BLOW_FATIGUE;
        assert_eq!(app.stunning_blow_actor(), Some(0));
        // A modal consumes the activation.
        for retreat in [false, true] {
            let mut app = stun_app();
            if retreat {
                open_retreat(&mut app);
            } else {
                open_dialog(&mut app);
            }
            let (pending, before) = (app.pending_confirm, snapshot(&app));
            press(&mut app, ch("6"));
            app.character_key("6");
            click_at(&mut app, center(layout::STUNNING_BLOW_BUTTON), Instant::now() + Duration::from_secs(5));
            assert!(app.targeting.is_none());
            assert_eq!(app.pending_confirm, pending);
            assert_eq!(snapshot(&app), before);
            assert!(quiet(&app));
        }
    }

    #[test]
    fn only_adjacent_enemies_are_targets_even_with_two_hex_reach() {
        let mut app = stun_app();
        app.state.units[0].melee_reach = bb_rules::MeleeReach::TwoHex;
        app.state.units[4].alive = true;
        app.state.units[4].pos = bb_rules::hex(2, -2);
        // The ordinary attack reaches the distance-2 enemy; the skill does not.
        assert!(app.state.preview_attack(0, 4).is_ok());
        assert_eq!(app.state.legal_stunning_blow_targets(0), vec![3]);
        open_action(&mut app, TargetAction::StunningBlow);
        let (before, intent) = (snapshot(&app), app.targeting);
        let pt = px(&app, 4);
        click_at(&mut app, pt, Instant::now());
        assert_eq!(snapshot(&app), before, "no Attack and no StunningBlow on the distance-2 enemy");
        assert_eq!(app.targeting, intent);
        assert!(app.error.as_ref().is_some_and(|(m, _)| m.starts_with("NOT A STUN TARGET")));
        // Only the adjacent enemy is highlighted: exactly one marked hex.
        let marked = |app: &App| render::hexes_filled(&app.frame(), render::STUN_TARGET_FILL);
        assert_eq!(marked(&app), vec![app.state.units[3].pos]);
    }

    #[test]
    fn selecting_a_legal_enemy_submits_exactly_the_direct_stunning_blow_command() {
        type Setup = fn(&mut App);
        let cases: [(&str, bool, Setup); 6] = [
            ("hit", true, |_| {}),
            ("miss", false, |_| {}),
            ("hit on an effective Indomitable target", true, |a| {
                a.state.units[3].has_indomitable = true;
                a.state.units[3].indomitable_active = true;
            }),
            ("miss on an Indomitable target", false, |a| {
                a.state.units[3].has_indomitable = true;
                a.state.units[3].indomitable_active = true;
            }),
            ("hit on an already stunned target", true, |a| a.state.units[3].stunned = true),
            ("lethal hit", true, |a| {
                a.state.units[3].hp = 1;
                a.state.units[3].armour = 0;
                a.state.units[0].stats.dmg_min = 20;
                a.state.units[0].stats.dmg_max = 20;
            }),
        ];
        for (what, hit, setup) in cases {
            let mut direct = stun_app();
            setup(&mut direct);
            force_stunning_blow_result(&mut direct, hit);
            direct.run(Command::StunningBlow { attacker: 0, target: 3 });

            let mut app = stun_app();
            setup(&mut app);
            force_stunning_blow_result(&mut app, hit);
            let (ap_before, fatigue_before) = (app.state.units[0].ap, app.state.units[0].fatigue);
            open_action(&mut app, TargetAction::StunningBlow);
            let t0 = Instant::now();
            click_unit(&mut app, 3, t0);
            assert!(app.targeting.is_none(), "{what}: targeting is cleared");
            assert_eq!(app.state.state_hash(), direct.state.state_hash(), "{what}");
            assert_eq!(app.state.rng, direct.state.rng, "{what}");
            assert_eq!(app.state.queue, direct.state.queue, "{what}");
            assert_eq!(app.state.units, direct.state.units, "{what}");
            assert_eq!(app.log, direct.log, "{what}: exactly one command, same ordered log");
            assert!(app.error.is_none(), "{what}");
            assert_eq!(has_stun_marker(&app), has_stun_marker(&direct), "{what}");
            assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD), "{what}");
            assert_eq!(app.state.units[0].ap, ap_before - STUNNING_BLOW_AP, "{what}");
            assert_eq!(app.state.units[0].fatigue, fatigue_before + STUNNING_BLOW_FATIGUE, "{what}");
            let hit_lines = log_count(&app, "P1 hits E1") + log_count(&app, "P1 misses E1");
            assert_eq!(hit_lines, 1, "{what}: one attack resolution");
            assert_eq!(log_count(&app, "P1 hits E1"), hit as usize, "{what}");
        }
        // The accepted stun lifecycle is reused unchanged.
        let mut app = stun_app();
        force_stunning_blow_result(&mut app, true);
        open_action(&mut app, TargetAction::StunningBlow);
        click_unit(&mut app, 3, Instant::now());
        assert_eq!(app.log.last().map(String::as_str), Some("E1 stunned"));
        assert!(app.state.units[3].stunned && has_stun_marker(&app));
        let mut app = stun_app();
        app.state.units[3].has_indomitable = true;
        app.state.units[3].indomitable_active = true;
        force_stunning_blow_result(&mut app, true);
        open_action(&mut app, TargetAction::StunningBlow);
        click_unit(&mut app, 3, Instant::now());
        assert_eq!(app.log.last().map(String::as_str), Some("E1 resists stun"));
        assert!(!app.state.units[3].stunned && !has_stun_marker(&app));
        let mut app = stun_app();
        force_stunning_blow_result(&mut app, false);
        open_action(&mut app, TargetAction::StunningBlow);
        click_unit(&mut app, 3, Instant::now());
        assert!(app.log.iter().all(|l| !l.contains("stun")) && !has_stun_marker(&app));
    }

    #[test]
    fn invalid_clicks_keep_stunning_blow_targeting_and_never_fall_through_to_move_or_attack() {
        let mut app = stun_app();
        app.state.units[4].alive = true;
        app.state.units[4].pos = bb_rules::hex(2, -2);
        app.state.units[2].alive = true;
        app.state.units[2].pos = bb_rules::hex(-1, -2);
        open_action(&mut app, TargetAction::StunningBlow);
        let intent = app.targeting;
        let (before, units) = (snapshot(&app), app.state.units.clone());
        let reachable = layout::hex_to_pixel(app.state.reachable(0)[0].0);
        let far = layout::hex_to_pixel(bb_rules::hex(0, 3));
        let now = Instant::now() + Duration::from_secs(10);
        for (what, pt) in [
            ("reachable empty hex", reachable),
            ("unreachable empty hex", far),
            ("enemy two hexes away", px(&app, 4)),
            ("adjacent ally", px(&app, 2)),
            ("far ally", px(&app, 1)),
            ("the actor itself", px(&app, 0)),
            ("a dead enemy's hex", layout::hex_to_pixel(app.state.units[5].pos)),
            ("off the map", (5.0, 700.0)),
            ("log area", (layout::PANEL_X + 5.0, 560.0)),
            ("a disabled skill button", center(layout::ROTATION_BUTTON)),
        ] {
            click_at(&mut app, pt, now);
            assert_eq!(app.targeting, intent, "{what}: targeting stays open");
            assert_eq!(snapshot(&app), before, "{what}: no command");
            assert_eq!(app.state.units, units, "{what}");
            if what == "a disabled skill button" {
                assert!(app.error.is_none(), "a disabled skill is a silent no-op");
                continue;
            }
            let (msg, _) = app.error.take().unwrap_or_else(|| panic!("{what}: concise selection feedback"));
            assert!(msg.starts_with("NOT A STUN TARGET"), "{what}: {msg}");
            assert!(msg.chars().count() <= 70 && msg.is_ascii());
        }
        // Without targeting the same enemy click is an Attack, proving the invalid clicks above
        // were consumed by targeting and not by the map.
        let mut control = stun_app();
        let pt = px(&control, 3);
        click_at(&mut control, pt, now);
        assert_eq!(control.state.units[0].ap, control.state.units[0].stats.ap_max - bb_rules::rules::ATTACK_AP);
        assert!(control.log.first().is_some_and(|l| l.starts_with("P1 hits E1") || l.starts_with("P1 misses E1")));
    }

    #[test]
    fn stale_stunning_blow_targeting_is_discarded_with_feedback_and_no_command() {
        type Tamper = fn(&mut App);
        let cases: [(&str, Tamper); 11] = [
            ("actor", |a| a.targeting.as_mut().unwrap().actor = 1),
            ("round", |a| a.targeting.as_mut().unwrap().round += 1),
            ("hash", |a| a.targeting.as_mut().unwrap().state_hash ^= 1),
            ("state changed", |a| a.state.units[1].hp -= 1),
            ("AP spent", |a| a.state.units[0].ap = 2),
            ("capability lost", |a| a.state.units[0].has_stunning_blow = false),
            ("enemy took control", |a| a.state.queue = vec![3, 0, 1]),
            ("actor fleeing", |a| a.state.units[0].morale = Morale::Fleeing),
            ("actor retreating", |a| a.state.units[0].retreating = true),
            ("battle over", |a| a.state.outcome = Some(bb_rules::Outcome::Defeat)),
            ("target moved away", |a| a.state.units[3].pos = bb_rules::hex(3, -2)),
        ];
        for (name, tamper) in cases {
            let mut app = stun_app();
            open_action(&mut app, TargetAction::StunningBlow);
            let pt = px(&app, 3);
            tamper(&mut app);
            let (before, units) = (snapshot(&app), app.state.units.clone());
            let t0 = Instant::now();
            click_at(&mut app, pt, t0);
            assert!(app.targeting.is_none(), "{name}");
            assert_eq!(snapshot(&app), before, "{name}: no command may be submitted");
            assert_eq!(app.state.units, units, "{name}");
            assert_eq!(app.error.as_ref().map(|e| e.0.as_str()), Some("STUN BLOW DISCARDED: BATTLE CHANGED"), "{name}");
            assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD), "{name}");
        }
        // A stale intent is discarded before the click could be an eligibility error.
        let mut app = stun_app();
        open_action(&mut app, TargetAction::StunningBlow);
        app.state.units[0].ap = 2;
        click_at(&mut app, (10.0, 10.0), Instant::now());
        assert_eq!(app.error.as_ref().unwrap().0, "STUN BLOW DISCARDED: BATTLE CHANGED");
        // Only a still-valid intent is drawn.
        let mut app = stun_app();
        open_action(&mut app, TargetAction::StunningBlow);
        app.state.units[0].ap = 2;
        assert!(!frame_stunning(&app));
    }

    #[test]
    fn switching_between_the_three_targeting_modes_is_consistent_and_submits_only_its_own_command() {
        for from in MODES {
            for to in MODES.into_iter().filter(|&m| m != from) {
                for by_button in [false, true] {
                    let mut app = trio_app();
                    open_action(&mut app, from);
                    let (before, units) = (snapshot(&app), app.state.units.clone());
                    if by_button {
                        click_at(&mut app, center(skill_button(to)), Instant::now());
                    } else {
                        press(&mut app, ch(skill_key(to)));
                    }
                    let intent = PendingTargeting { action: to, actor: 0, round: 1, state_hash: app.state.state_hash() };
                    assert_eq!(app.targeting, Some(intent), "{from:?}->{to:?}");
                    assert_eq!(app.click_guard_until, None, "switching arms no guard");
                    assert_eq!(snapshot(&app), before);
                    assert_eq!(app.state.units, units);
                    assert!(quiet(&app));
                    assert!(frame_shows(&app, to), "only the new mode is drawn");
                    assert_eq!(skill_look(&app, to), Some(render::SKILL_ACTIVE));
                    assert_eq!(skill_look(&app, from), Some(render::SKILL_ENABLED));
                    // The old mode's target is an invalid click for the new mode.
                    let wrong = legal_point(&app, from);
                    click_at(&mut app, wrong, Instant::now());
                    assert_eq!((snapshot(&app), app.targeting), (before.clone(), Some(intent)), "{from:?}->{to:?}: no cross-wired command");
                    assert!(app.error.take().is_some());
                    // The new mode's target submits only its own command.
                    let t0 = Instant::now();
                    let right = legal_point(&app, to);
                    click_at(&mut app, right, t0);
                    assert!(app.targeting.is_none());
                    let attacks = log_count(&app, "P1 hits E1") + log_count(&app, "P1 misses E1");
                    let counts = (log_count(&app, "swaps"), log_count(&app, "footwork"), attacks);
                    assert_eq!(
                        counts,
                        match to {
                            TargetAction::Rotation => (1, 0, 0),
                            TargetAction::Footwork => (0, 1, 0),
                            TargetAction::StunningBlow => (0, 0, 1),
                        },
                        "{from:?}->{to:?}"
                    );
                    assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD));
                }
            }
        }
    }

    #[test]
    fn activating_an_unavailable_skill_leaves_the_stunning_blow_mode_and_the_others_unchanged() {
        // Stunning Blow open; the others are not in this loadout.
        let mut app = stun_app();
        open_action(&mut app, TargetAction::StunningBlow);
        let (intent, before, units) = (app.targeting, snapshot(&app), app.state.units.clone());
        for other in [TargetAction::Rotation, TargetAction::Footwork] {
            press(&mut app, ch(skill_key(other)));
            app.character_key(skill_key(other));
            click_at(&mut app, center(skill_button(other)), Instant::now());
            assert_eq!(app.targeting, intent, "{other:?}");
            assert_eq!(snapshot(&app), before);
            assert_eq!(app.state.units, units);
            assert!(quiet(&app) && app.click_guard_until.is_none());
            assert!(frame_shows(&app, TargetAction::StunningBlow));
            assert_eq!(skill_look(&app, other), Some(render::SKILL_DISABLED));
        }
        // Another mode open; Stunning Blow is missing or has no target.
        type Setup = fn(&mut App);
        let setups: [(&str, Setup); 3] = [
            ("no capability", |a| a.state.units[0].has_stunning_blow = false),
            ("no adjacent enemy", |a| a.state.units[3].pos = bb_rules::hex(3, -2)),
            ("one AP short", |a| a.state.units[0].ap = STUNNING_BLOW_AP - 1),
        ];
        for open in [TargetAction::Rotation, TargetAction::Footwork] {
            for (what, setup) in setups {
                let mut app = trio_app();
                setup(&mut app);
                open_action(&mut app, open);
                let (intent, before) = (app.targeting, snapshot(&app));
                press(&mut app, ch("6"));
                app.character_key("6");
                click_at(&mut app, center(layout::STUNNING_BLOW_BUTTON), Instant::now());
                assert_eq!((app.targeting, snapshot(&app)), (intent, before), "{open:?} / {what}");
                assert!(quiet(&app) && app.click_guard_until.is_none());
                assert_eq!(blow_look(&app), Some(render::SKILL_DISABLED), "{what}");
            }
        }
    }

    #[test]
    fn repeat_six_escape_and_the_button_cancel_stunning_blow_targeting_and_arm_the_guard() {
        for how in 0..3 {
            let mut app = trio_app();
            let before = snapshot(&app);
            open_action(&mut app, TargetAction::StunningBlow);
            let t0 = Instant::now();
            match how {
                0 => assert!(!app.key(&ch("6"), t0)),
                1 => assert!(!app.key(&named(NamedKey::Escape), t0), "Escape cancels instead of quitting"),
                _ => click_at(&mut app, center(layout::STUNNING_BLOW_BUTTON), t0),
            }
            assert!(app.targeting.is_none(), "how {how}");
            assert_eq!(snapshot(&app), before);
            assert!(quiet(&app));
            assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD), "the cancel arms the click guard");
            assert_eq!(blow_look(&app), Some(render::SKILL_ENABLED));
            assert!(press(&mut app, named(NamedKey::Escape)), "outside targeting Escape quits");
            app.click_guard_until = None;
            open_action(&mut app, TargetAction::StunningBlow);
        }
        // Auto-repeat events never reach the handler, so a held 6 cannot toggle.
        assert!(!key_press_accepted(ElementState::Pressed, true));
        assert!(!key_press_accepted(ElementState::Released, false));
        assert!(key_press_accepted(ElementState::Pressed, false));
    }

    #[test]
    fn modals_and_other_actions_take_precedence_over_stunning_blow_targeting() {
        for (retreat, by_button) in [(false, false), (true, false), (false, true), (true, true)] {
            let mut app = trio_app();
            open_action(&mut app, TargetAction::StunningBlow);
            let before = snapshot(&app);
            match (retreat, by_button) {
                (false, false) => {
                    press(&mut app, ch("x"));
                }
                (true, false) => {
                    press(&mut app, ch("v"));
                }
                (false, true) => click_at(&mut app, center(layout::END_ROUND_BUTTON), Instant::now()),
                (true, true) => click_at(&mut app, center(layout::RETREAT_BUTTON), Instant::now()),
            }
            assert!(app.targeting.is_none(), "opening a confirmation clears targeting");
            let pending = app.pending_confirm.expect("the dialog opened");
            assert_eq!(snapshot(&app), before);
            // Every targeting input is consumed by the modal.
            let later = Instant::now() + Duration::from_secs(5);
            for a in MODES {
                press(&mut app, ch(skill_key(a)));
                app.character_key(skill_key(a));
                click_at(&mut app, center(skill_button(a)), later);
                let pt = legal_point(&app, a);
                click_at(&mut app, pt, later);
            }
            assert!(app.targeting.is_none());
            assert_eq!(app.pending_confirm, Some(pending));
            assert_eq!(snapshot(&app), before);
            assert!(quiet(&app));
            let t0 = Instant::now();
            assert!(!app.key(&named(NamedKey::Escape), t0));
            assert!(app.pending_confirm.is_none() && app.targeting.is_none());
            click_at(&mut app, center(layout::STUNNING_BLOW_BUTTON), t0 + Duration::from_millis(1));
            assert!(app.targeting.is_none(), "the guard swallows the button");
            click_at(&mut app, center(layout::STUNNING_BLOW_BUTTON), t0 + MODAL_CLICK_GUARD);
            assert_eq!(app.targeting.map(|t| t.action), Some(TargetAction::StunningBlow));
        }
        for key in ["e", "w", "x", "v", "1", "r", "n", "E", "X"] {
            let mut app = trio_app();
            open_action(&mut app, TargetAction::StunningBlow);
            let t0 = Instant::now();
            app.key(&ch(key), t0);
            assert!(app.targeting.is_none(), "{key}");
            assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD), "{key}");
            match key.to_ascii_lowercase().as_str() {
                "e" => assert_eq!(app.human_unit(), None, "EndTurn ran: the enemy is next"),
                "w" => assert_eq!(app.state.queue.last(), Some(&0), "Wait ran"),
                "x" | "v" => assert!(app.pending_confirm.is_some(), "{key}"),
                "1" => assert!(app.state.units[0].adrenaline_pending),
                _ => assert_eq!(app.human_unit(), Some(0)),
            }
        }
        for (button, what) in [(layout::ADRENALINE_BUTTON, "adrenaline"), (layout::END_ROUND_BUTTON, "end"), (layout::RETREAT_BUTTON, "retreat")] {
            let mut app = trio_app();
            open_action(&mut app, TargetAction::StunningBlow);
            let t0 = Instant::now();
            click_at(&mut app, center(button), t0);
            assert!(app.targeting.is_none(), "{what}");
            assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD), "{what}");
            assert_eq!(app.state.units[0].adrenaline_pending, what == "adrenaline");
        }
        // H and Z are display preferences and leave the mode open.
        let mut app = trio_app();
        open_action(&mut app, TargetAction::StunningBlow);
        let (intent, hash) = (app.targeting, app.state.state_hash());
        for key in ["z", "h", "Z", "H"] {
            press(&mut app, ch(key));
            assert_eq!(app.targeting, intent, "{key}");
        }
        assert_eq!(app.state.state_hash(), hash);
        assert!(frame_shows(&app, TargetAction::StunningBlow));
        // A key that is no action keeps it.
        press(&mut app, ch("q"));
        assert_eq!(app.targeting, intent);
    }

    #[test]
    fn restart_and_new_seed_cancel_stunning_blow_targeting_and_the_granted_capability_does_not_survive() {
        for key in ["r", "n"] {
            let mut app = trio_app();
            open_action(&mut app, TargetAction::StunningBlow);
            let t0 = Instant::now();
            assert!(!app.key(&ch(key), t0));
            assert!(app.targeting.is_none());
            assert_eq!(app.click_guard_until, Some(t0 + MODAL_CLICK_GUARD), "{key}");
            assert!(app.log.is_empty() && app.error.is_none());
            // This fixture granted the capability manually, outside the selected launch loadout.
            // The rebuilt battle follows default launch args, which still leave it disabled.
            assert!(app.state.units.iter().all(|u| !u.has_stunning_blow));
            assert_eq!(blow_look(&app), Some(render::SKILL_DISABLED));
            let after = snapshot(&app);
            let reachable = layout::hex_to_pixel(app.state.reachable(0)[0].0);
            click_at(&mut app, reachable, t0 + MODAL_CLICK_GUARD - Duration::from_millis(1));
            assert_eq!(snapshot(&app), after, "{key}: the follow-up click must not Move in the rebuilt battle");
            click_at(&mut app, reachable, t0 + MODAL_CLICK_GUARD);
            assert_ne!(snapshot(&app), after, "{key}: ordinary clicks work once the guard expires");
        }
    }

    #[test]
    fn a_stunning_blow_selection_dispatches_exactly_once_through_the_guard_and_its_expiry() {
        let mut app = stun_app();
        force_stunning_blow_result(&mut app, true);
        open_action(&mut app, TargetAction::StunningBlow);
        let t0 = Instant::now();
        click_unit(&mut app, 3, t0);
        let after = snapshot(&app);
        let units = app.state.units.clone();
        assert_eq!(app.state.units[0].fatigue, STUNNING_BLOW_FATIGUE);
        // Repeated clicks and the shortcut inside the guard: clicks do nothing.
        let inside = t0 + MODAL_CLICK_GUARD - Duration::from_millis(1);
        for _ in 0..3 {
            click_unit(&mut app, 3, inside);
            click_unit(&mut app, 0, inside);
            click_at(&mut app, center(layout::STUNNING_BLOW_BUTTON), inside);
        }
        assert_eq!(snapshot(&app), after);
        assert_eq!(app.state.units, units);
        assert!(app.error.is_none() && app.targeting.is_none());
        assert_eq!(log_count(&app, "P1 hits E1") + log_count(&app, "P1 misses E1"), 1);
        // At expiry a click is an ordinary map click, never another Stunning Blow: P1 has 3 AP
        // left, so the Attack is rejected and nothing is spent.
        click_unit(&mut app, 3, t0 + MODAL_CLICK_GUARD);
        assert_eq!(app.state.units[0].fatigue, STUNNING_BLOW_FATIGUE);
        assert_eq!(app.state.units[0].ap, app.state.units[0].stats.ap_max - STUNNING_BLOW_AP);
        assert_eq!(app.error.as_ref().map(|e| e.0.as_str()), Some("NotEnoughAp"));
        assert_eq!(log_count(&app, "P1 hits E1") + log_count(&app, "P1 misses E1"), 1);
        assert!(app.targeting.is_none());
    }

    #[test]
    fn hovering_a_stunning_blow_target_shows_the_rules_preview_and_hides_the_ordinary_ones() {
        let hover_frame = |app: &mut App, pt: (f32, f32)| {
            app.mouse = pt;
            app.frame()
        };
        let mut app = stun_app();
        let plan = app.state.preview_stunning_blow(0, 3).unwrap();
        // Control: without targeting the hover shows the ordinary attack information.
        let pt = px(&app, 3);
        let verts = hover_frame(&mut app, pt);
        assert!(render::preview_row_shown(&verts, 0, &format!("ATTACK E1: HIT {}%", plan.hit_chance)));
        assert!(render::preview_row_shown(&verts, 1, &format!("COST {} AP {} FAT", bb_rules::rules::ATTACK_AP, bb_rules::rules::ATTACK_FATIGUE)));

        let (before, hash, rng) = (snapshot(&app), app.state.state_hash(), app.state.rng.clone());
        open_action(&mut app, TargetAction::StunningBlow);
        let verts = hover_frame(&mut app, pt);
        assert!(render::preview_row_shown(&verts, 0, &format!("STUN BLOW E1: HIT {}%", plan.hit_chance)));
        assert!(render::preview_row_shown(&verts, 1, &format!("COST {} AP {} FAT (P1 ONLY). ESC OR 6 CANCELS.", plan.ap_cost, plan.fatigue_cost)));
        assert!(render::preview_row_shown(&verts, 2, &format!("DAMAGE ON HIT {}-{} BEFORE ARMOUR. MISSES DEAL 0.", plan.damage_min, plan.damage_max)));
        assert!(render::preview_row_shown(&verts, 3, "A SURVIVING HIT ATTEMPTS TO STUN. MISSES DO NOT."));
        assert!(!render::preview_row_shown(&verts, 0, &format!("ATTACK E1: HIT {}%", plan.hit_chance)), "the ordinary attack preview is suppressed");
        // Hovering an empty reachable hex shows instructions, not a movement preview.
        let reachable = layout::hex_to_pixel(app.state.reachable(0)[0].0);
        let verts = hover_frame(&mut app, reachable);
        assert!(render::preview_row_shown(&verts, 0, "STUN BLOW: P1 PICKS A MARKED ENEMY TO STRIKE. ESC OR 6 CANCELS."));
        assert!(render::hexes_filled(&verts, render::REACH_FILL).is_empty(), "no movement highlights while targeting");
        // Hover, frames and drawing are pure.
        app.draw();
        assert_eq!(snapshot(&app), before);
        assert_eq!((app.state.state_hash(), &app.state.rng), (hash, &rng));
    }
}
