//! Rules core of a small hex tactics slice. Std only, deterministic, no I/O.
//!
//! Most mechanics and numbers here are independent slice design. Recover maps
//! the statically observed current-fatigue / 2 effect; original-runtime parity
//! has not been tested. The names ActionPoints, Fatigue, Initiative, Morale,
//! apCost, EndTurn and WaitTurn are OBSERVED-NAME-ONLY.

pub mod ai;
pub mod hex;
pub mod rng;
pub mod rules;
pub mod scenario;
pub mod terrain;
pub mod unit;

pub use hex::{Hex, hex};
pub use rng::Rng;
pub use terrain::{ElevationError, ElevationMap, TerrainError, TerrainKind, TerrainMap};
pub use unit::{MeleeReach, Morale, Side, Stats, Unit};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Command {
    Move { unit: u32, to: Hex },
    Attack { attacker: u32, target: u32 },
    /// Spend 9 AP and complete the actor's scheduled turn while halving current
    /// fatigue. The effect is RECONSTRUCTED from static evidence; the cost and
    /// whole-turn action are catalog-stated in UC-COMBAT-065 without confirmation
    /// from the original runtime. Integer rounding, surplus-AP forfeiture,
    /// zero-fatigue acceptance and ungated availability remain APPROXIMATION
    /// choices.
    Recover { unit: u32 },
    Wait { unit: u32 },
    EndTurn { unit: u32 },
    /// Player-only: forfeit the remaining scheduled turns of the player side this round.
    EndRound { unit: u32 },
    /// Automatic turn of a Fleeing unit: at most one step toward a map edge, or
    /// an escape when already on one. Accepted only for a Fleeing active unit.
    Flee { unit: u32 },
    /// Player-only party order from the present, controllable active player:
    /// every present player unit gets retreat intent and those already on a map
    /// edge escape at once. Not a turn action (no AP or fatigue cost).
    Retreat { unit: u32 },
    /// Automatic turn of a retreating unit: the same single step toward a map
    /// edge as `Flee` (or an escape when already on one). Accepted only for a
    /// retreating active unit, whatever its morale.
    Withdraw { unit: u32 },
    /// Adrenaline (partial UC-COMBAT-066; capability flag and costs are an
    /// APPROXIMATION): the present, controllable active unit with
    /// `has_adrenaline` spends AP and fatigue to add initiative at the next
    /// round's ordering. Does not end the turn.
    Adrenaline { unit: u32 },
    /// Rotation (partial UC-COMBAT-039; capability flag, costs and target
    /// restrictions are an APPROXIMATION): the present, controllable active unit
    /// with `has_rotation` swaps hexes with an adjacent same-side `target`
    /// without opportunity attacks (UC-COMBAT-008). Does not end the turn.
    Rotation { unit: u32, target: u32 },
    /// Footwork (partial UC-COMBAT-039; capability flag, costs, the one-hex limit
    /// and edge integration are an APPROXIMATION): the present, controllable
    /// active unit with `has_footwork` steps to an adjacent passable, unoccupied
    /// hex without opportunity attacks (UC-COMBAT-008). Does not end the turn.
    Footwork { unit: u32, to: Hex },
    /// Indomitable (partial UC-COMBAT-016; capability flag, costs, duration and
    /// cancellation are an APPROXIMATION): the present, controllable active unit
    /// with `has_indomitable` spends AP and fatigue to halve incoming melee damage
    /// until its next fresh turn. Either faction; does not end the turn.
    Indomitable { unit: u32 },
    /// Stunning Blow (independent skill; name, capability flag, costs, adjacent-only
    /// targeting and event timing are an APPROXIMATION): the present, controllable
    /// active unit with `has_stunning_blow` makes a paid melee attack at distance
    /// exactly 1, whatever its ordinary reach. Damage and hit chance are the ordinary
    /// ones; a hit on a surviving target then calls `apply_stun`. Does not end the turn.
    StunningBlow { attacker: u32, target: u32 },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Outcome {
    Victory,
    Defeat,
    /// Every player unit left the battlefield and at least one escaped alive.
    Retreat,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EndReason {
    Elimination,
    TimeLimit,
    /// The result involves at least one surviving escapee (alive, fled unit).
    Escape,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Event {
    Moved { unit: u32, path: Vec<Hex> },
    /// `dmg` is the damage supplied to armour soak: the sampled value, raised first by an
    /// active attacker's Killing Frenzy (APPROXIMATION: `floor(base * 125 / 100)`,
    /// saturating at `u32::MAX`), then halved (integer division) when the target's
    /// Indomitable stance is effective.
    Attacked { attacker: u32, target: u32, hit: bool, roll: u32, chance: u32, dmg: u32, absorbed: u32, hp_loss: u32 },
    /// Resolve check after a surviving heavy hit, emitted right after `Attacked`.
    /// `roll < chance` passes (morale unchanged); failure lowers morale one step.
    MoraleChecked { unit: u32, roll: u32, chance: u32, before: Morale, after: Morale },
    /// Automatic self-rally check of an eligible Fleeing unit, emitted right after
    /// `TurnStarted`. `roll < chance` recovers Fleeing to Breaking; failure keeps
    /// Fleeing. Distinct from the heavy-hit `MoraleChecked`.
    SelfRallyChecked { unit: u32, roll: u32, chance: u32, before: Morale, after: Morale },
    /// Negative Resolve check of a surviving same-side ally after `fallen` died in
    /// melee, emitted directly after `Died` in ascending `unit` order (APPROXIMATION:
    /// every present ally, at any distance). Same formula and effects as
    /// `MoraleChecked`; distinct from it and from `SelfRallyChecked`.
    AllyDeathMoraleChecked { unit: u32, fallen: u32, roll: u32, chance: u32, before: Morale, after: Morale },
    /// Positive Resolve check of the present, non-Fleeing killer of `fallen`,
    /// emitted after `Died` and the ally-death checks of that death. `roll <
    /// chance` applies `Morale::raised`; failure leaves morale unchanged. One
    /// draw and one event per kill even when `before` is already Confident.
    /// APPROXIMATION: killer-only, no original-runtime confirmation.
    KillMoraleChecked { unit: u32, fallen: u32, roll: u32, chance: u32, before: Morale, after: Morale },
    /// An ordinary move arrived on a map-edge hex and lowered the mover's morale
    /// directly to Breaking (UC-COMBAT-048; the destination, both-side scope and
    /// scheduling are APPROXIMATION). Emitted only when morale changes, after the
    /// `Moved` segment ending at `at` and before any later departure reaction.
    /// Consumes no RNG; Flee/Withdraw movement never emits it.
    EdgeMoraleChanged { unit: u32, at: Hex, before: Morale, after: Morale },
    Died { unit: u32 },
    /// A Nine Lives defender survived an otherwise-lethal melee hit at
    /// `NINE_LIVES_SURVIVAL_HP`, emitted immediately after that hit's `Attacked`
    /// (partial UC-COMBAT-059). No `Died` or death-triggered morale check follows.
    NineLivesTriggered { unit: u32 },
    /// A Berserk attacker killed its target with an ordinary `Command::Attack` or a
    /// `Command::StunningBlow` and regained `ap_gained` AP (partial UC-COMBAT-038), emitted after the death's
    /// `Died`, ally-death and kill-morale events and before any `BattleEnded`.
    /// `ap_gained` is the actual gain after the `ap_max` cap and may be 0.
    BerserkTriggered { unit: u32, ap_gained: u32 },
    /// The unit reached a map edge while Fleeing and left the battle alive.
    Fled { unit: u32 },
    /// `Command::Retreat` was accepted from `unit`; emitted before any `Fled`
    /// of the immediate edge exits.
    RetreatOrdered { unit: u32 },
    /// `Command::Adrenaline` was accepted; the initiative bonus applies when the
    /// next round's queue is built.
    AdrenalineUsed { unit: u32 },
    /// `Command::Rotation` was accepted: `unit` and `target` exchanged hexes.
    /// Emitted first, before any `EdgeMoraleChanged` of the arrivals.
    Rotated { unit: u32, target: u32 },
    /// `Command::Footwork` was accepted: `unit` stepped from `from` to `to`.
    /// Emitted first, before any `EdgeMoraleChanged` of the arrival.
    FootworkUsed { unit: u32, from: Hex, to: Hex },
    /// `Command::Indomitable` was accepted; the stance is active. Emitted alone.
    IndomitableUsed { unit: u32 },
    /// `BattleState::apply_stun` stunned `unit`; emitted first, before any
    /// `StunExpired` of an immediately forfeited active entry.
    StunApplied { unit: u32 },
    /// `unit`'s stun was consumed together with its scheduled queue entry, after
    /// the turn-start processing of that visit and before the next unit's.
    StunExpired { unit: u32 },
    /// `apply_stun` was blocked by an effective Indomitable stance; nothing changed.
    StunResisted { unit: u32 },
    TurnStarted { unit: u32 },
    RoundStarted { round: u32 },
    BattleEnded { outcome: Outcome, reason: EndReason },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RuleError {
    NotActiveUnit,
    BattleOver,
    Unreachable,
    NotEnoughAp,
    TooFatigued,
    /// The target is outside the actor's reach. Historically "not adjacent":
    /// for ordinary Attack it now means outside `Unit::melee_reaches`
    /// (distance 1 for Adjacent, 1..=2 for TwoHex); Rotation and Footwork still
    /// require distance exactly 1.
    NotAdjacent,
    InvalidTarget,
    AlreadyWaited,
    /// `EndRound` was issued by an enemy-side unit.
    NotPlayerUnit,
    /// `Flee` was issued for an actor that is not Fleeing.
    NotFleeing,
    /// An ordinary action command was issued for a Fleeing actor.
    FleeingUnit,
    /// An ordinary action command, `Retreat` or a movement preview was issued
    /// for a unit that already has retreat intent.
    RetreatingUnit,
    /// `Withdraw` was issued for an actor without retreat intent.
    NotRetreating,
    /// `Adrenaline` was issued for an actor without `has_adrenaline`.
    MissingAdrenaline,
    /// `Adrenaline` was issued while the actor's effect is still pending.
    AdrenalinePending,
    /// `Rotation` was issued for an actor without `has_rotation`.
    MissingRotation,
    /// `Footwork` was issued for an actor without `has_footwork`.
    MissingFootwork,
    /// `Indomitable` was issued for an actor without `has_indomitable`.
    MissingIndomitable,
    /// `Indomitable` was issued while the actor's stance is still active.
    IndomitableActive,
    /// `StunningBlow` was issued for an actor without `has_stunning_blow`.
    MissingStunningBlow,
}

#[derive(Clone, Debug)]
pub struct BattleState {
    pub units: Vec<Unit>,
    pub rng: Rng,
    pub round: u32,
    /// Living units still to act this round; `queue[0]` is the active unit.
    pub queue: Vec<u32>,
    pub outcome: Option<Outcome>,
    pub end_reason: Option<EndReason>,
    /// Per-hex heights; flat by default. Configured headlessly only.
    pub elevations: ElevationMap,
    /// Per-hex ground kind; all `Ground` by default. Configured headlessly only.
    pub ground: TerrainMap,
}

impl BattleState {
    pub fn active_unit(&self) -> Option<&Unit> {
        if self.outcome.is_some() {
            return None;
        }
        self.queue.first().map(|&id| &self.units[id as usize])
    }

    pub fn outcome(&self) -> Option<Outcome> {
        self.outcome
    }

    pub fn end_reason(&self) -> Option<EndReason> {
        self.end_reason
    }

    pub fn unit(&self, id: u32) -> Option<&Unit> {
        self.units.get(id as usize)
    }
}
