use crate::hex::Hex;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Side {
    Player,
    Enemy,
}

/// Morale states used by the slice's melee stat modifiers; `Fleeing` also removes
/// player control and ZoC (see `rules`). The modifiers are an APPROXIMATION; no
/// original-runtime confirmation exists.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Morale {
    Steady,
    Confident,
    Wavering,
    Breaking,
    Fleeing,
}

impl Morale {
    /// Shared percentage scaling the base melee skill and defense (not any added
    /// Dodge bonus; Fleeing 70% follows the
    /// catalog's UC-COMBAT-018 text). Transitions are a downward step after a
    /// failed surviving-heavy-hit or ally-death Resolve check, the Fleeing-to-Breaking
    /// self-rally at a turn start and a one-step rise after a passed positive
    /// Resolve check on a melee kill (see `rules`), plus the direct drop to
    /// Breaking when an ordinary move arrives on a map-edge hex. Ranged effects,
    /// other morale triggers and other positive transitions (Rally the Troops)
    /// are not implemented in this slice.
    pub const fn modifier_percent(self) -> u32 {
        match self {
            Morale::Steady => 100,
            Morale::Confident => 110,
            Morale::Wavering => 90,
            Morale::Breaking => 80,
            Morale::Fleeing => 70,
        }
    }

    /// One step down after a failed check: Confident to Steady, Steady to
    /// Wavering, Wavering to Breaking, Breaking to Fleeing. Fleeing stays
    /// Fleeing. Never raises morale.
    pub const fn lowered(self) -> Morale {
        match self {
            Morale::Confident => Morale::Steady,
            Morale::Steady => Morale::Wavering,
            Morale::Wavering => Morale::Breaking,
            Morale::Breaking | Morale::Fleeing => Morale::Fleeing,
        }
    }

    /// One step up after a passed positive check: Breaking to Wavering,
    /// Wavering to Steady, Steady to Confident. Confident stays Confident and
    /// Fleeing stays Fleeing (leaving Fleeing is the separate self-rally). Pure:
    /// performs no check or mutation.
    pub const fn raised(self) -> Morale {
        match self {
            Morale::Breaking => Morale::Wavering,
            Morale::Wavering => Morale::Steady,
            Morale::Steady | Morale::Confident => Morale::Confident,
            Morale::Fleeing => Morale::Fleeing,
        }
    }

    /// Stable state-hash codes. Keep these explicit and independent of enum layout.
    pub(crate) const fn state_hash_code(self) -> u64 {
        match self {
            Morale::Steady => 0,
            Morale::Confident => 1,
            Morale::Wavering => 2,
            Morale::Breaking => 3,
            Morale::Fleeing => 4,
        }
    }
}

/// Ordinary melee attack reach. Adjacent (the default) reaches distance 1;
/// TwoHex reaches distances 1 and 2. The two-hex reach itself is catalog-stated
/// (UC-COMBAT-051); the enum, its hash encoding and the absence of weapon
/// profiles are an APPROXIMATION. It never changes zones of control, which stay
/// at distance 1.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum MeleeReach {
    #[default]
    Adjacent,
    TwoHex,
}

impl MeleeReach {
    /// Purely geometric ordinary melee reach. Distance zero is excluded;
    /// presence, hostility, resources and obstruction are caller concerns.
    /// Widened arithmetic keeps extreme `i32` coordinates from overflowing.
    pub fn reaches(self, from: Hex, to: Hex) -> bool {
        let dq = i64::from(from.q) - i64::from(to.q);
        let dr = i64::from(from.r) - i64::from(to.r);
        let distance = (dq.abs() + dr.abs() + (dq + dr).abs()) / 2;
        match self {
            MeleeReach::Adjacent => distance == 1,
            MeleeReach::TwoHex => (1..=2).contains(&distance),
        }
    }
}

/// Stats. Every number is an invented slice value (APPROXIMATION); the names
/// ActionPoints, Fatigue and Initiative are OBSERVED-NAME-ONLY.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Stats {
    pub hp: u32,
    pub armour: u32,
    pub ap_max: u32,
    pub fatigue_max: u32,
    pub initiative: u32,
    pub melee_skill: u32,
    pub defense: u32,
    /// Unmodified base Resolve, the stat used by morale checks. Morale does not
    /// modify it. Scenario values are invented (APPROXIMATION).
    pub resolve: u32,
    pub dmg_min: u32,
    pub dmg_max: u32,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Unit {
    pub id: u32,
    pub name: &'static str,
    pub side: Side,
    pub stats: Stats,
    /// Current morale state. Scenario units start Steady and deteriorate
    /// through the surviving heavy-damage and ally-death checks in `rules`; a
    /// Fleeing unit can recover (to Breaking) through the turn-start self-rally
    /// check, a killer rises one step through its kill check, and an ordinary
    /// move onto a map-edge hex drops a non-Fleeing mover directly to Breaking.
    pub morale: Morale,
    pub hp: u32,
    pub armour: u32,
    pub ap: u32,
    pub fatigue: u32,
    pub pos: Hex,
    pub alive: bool,
    /// Escaped from the battlefield by reaching a map edge while Fleeing. An
    /// escapee keeps `alive`, HP and equipment values; it is simply no longer
    /// present on the battlefield (see `is_present`).
    pub fled: bool,
    /// Used `Wait` in the current round.
    pub waited: bool,
    /// Turn refresh (AP, fatigue regen) already done in the current round.
    pub turn_started: bool,
    /// Pending `EndRound`: forfeit the remaining scheduled turn this round
    /// (refresh at the turn start, then AP 0 and automatic advance). Cleared when
    /// the next round begins.
    pub skip_turn: bool,
    /// Party-retreat intent set by `Command::Retreat` on every present player.
    /// Separate from morale: it persists across rounds and self-rally, is reset
    /// only by `Unit::new` for a new battle, and makes the unit's turns
    /// automatic `Withdraw` turns (ordinary commands are rejected).
    pub retreating: bool,
    /// Headless capability flag: the unit may use `Command::Adrenaline`. Not a
    /// perk system; no scenario unit has it (APPROXIMATION).
    pub has_adrenaline: bool,
    /// Adrenaline used and not yet consumed: the next `begin_round` adds
    /// `ADRENALINE_INITIATIVE_BONUS` to this unit's queue key, then clears it.
    pub adrenaline_pending: bool,
    /// Headless capability flag: the unit may use `Command::Rotation`. Not a
    /// perk system; no scenario unit has it (APPROXIMATION).
    pub has_rotation: bool,
    /// Headless capability flag: the unit may use `Command::Footwork`. Not a
    /// perk system; no scenario unit has it (APPROXIMATION).
    pub has_footwork: bool,
    /// Headless capability flag: the unit gains Fast Adaptation stacks from melee
    /// misses (partial UC-COMBAT-067). Not a perk system; the default scenario
    /// has none and `--fast-adaptation` grants it to player units only (APPROXIMATION).
    pub has_fast_adaptation: bool,
    /// Consecutive-miss stacks of a Fast Adaptation attacker; each adds
    /// `FAST_ADAPTATION_BONUS_PER_STACK` to its melee hit chance and any hit
    /// resets them. Ignored and left untouched when `has_fast_adaptation` is false.
    pub fast_adaptation_stacks: u32,
    /// Headless capability flag: the unit gains `DODGE_DEFENSE_PERCENT` of its
    /// current initiative as melee defense (partial UC-COMBAT-058). Not a perk
    /// system; the default scenario has none and `--dodge` grants it to player units
    /// only (APPROXIMATION).
    pub has_dodge: bool,
    /// Headless capability flag: the unit survives its first otherwise-lethal melee hit
    /// at `NINE_LIVES_SURVIVAL_HP` (partial UC-COMBAT-059). Not a perk system; the default
    /// scenario has none and `--nine-lives` grants it to player units only, unused
    /// (APPROXIMATION).
    pub has_nine_lives: bool,
    /// Whether the once-per-battle protection has been consumed. Only constructing a
    /// fresh unit resets it; waiting, turns, rounds, morale and retreat leave it alone.
    pub nine_lives_used: bool,
    /// Passive capability flag: the unit regains AP after its first ordinary melee kill in a
    /// scheduled turn (partial UC-COMBAT-038). Not a perk system; `--berserk` grants it to
    /// player units only (APPROXIMATION).
    pub has_berserk: bool,
    /// Whether this turn's refund has been consumed. Only the once-per-round turn refresh
    /// in `begin_turn` clears it; a Wait return, another unit's action, opportunity
    /// attacks and rejected commands leave it alone.
    pub berserk_used: bool,
    /// Passive capability flag: each attempted step costs `PATHFINDER_MOVE_FATIGUE`
    /// instead of `MOVE_FATIGUE` (partial UC-COMBAT-057). Not a perk system; `--pathfinder`
    /// grants it to player units only (APPROXIMATION). AP costs, attacks and skills are unaffected.
    pub has_pathfinder: bool,
    /// Headless capability flag: the unit may use `Command::Indomitable` (partial
    /// UC-COMBAT-016). Not a perk system; no scenario unit has it (APPROXIMATION).
    pub has_indomitable: bool,
    /// Indomitable stance activated and not yet expired. Only the once-per-round fresh
    /// turn refresh in `begin_turn` clears it, or a rule-driven morale check that makes
    /// the unit Fleeing; Wait returns, other turns and round changes leave it alone.
    /// Whether it takes effect is `is_indomitable_active` (APPROXIMATION).
    pub indomitable_active: bool,
    /// Ordinary melee attack reach (partial UC-COMBAT-051). Every unit starts
    /// `Adjacent`; only the `--two-hex` launch option grants `TwoHex` (player units
    /// only), and no weapon profile exists (APPROXIMATION).
    pub melee_reach: MeleeReach,
    /// Pending stun (partial UC-COMBAT-028/016): the unit exerts no zone of control
    /// and its next scheduled queue entry is forfeited. Set only by
    /// `BattleState::apply_stun`; cleared when that entry is consumed in
    /// `advance_turn`. Boolean storage and next-entry duration are an APPROXIMATION.
    pub stunned: bool,
    /// Headless capability flag: the unit may use `Command::StunningBlow`, a paid
    /// adjacent melee skill whose hit stuns a surviving target. Not a perk, weapon or
    /// mastery system; no scenario unit has it (APPROXIMATION).
    pub has_stunning_blow: bool,
    /// Headless capability flag: a lethal paid melee attack (`Command::Attack` or
    /// `Command::StunningBlow`) grants a temporary melee damage bonus (partial
    /// UC-COMBAT-055). Default scenarios do not assign it; `--killing-frenzy` grants it to
    /// player units only. The launch assignment is an APPROXIMATION:
    /// the catalog supplies only the temporary-buff concept and the Berserk interaction;
    /// the bonus, duration, refresh timing and paid-kill trigger restriction are slice choices.
    pub has_killing_frenzy: bool,
    /// Fresh `begin_turn` entries the Killing Frenzy bonus survives, in addition to the rest
    /// of the current turn. Set to 2 (refreshed, never stacked) by a lethal paid melee
    /// attack by a capable present unit; saturating-decremented once per fresh
    /// `begin_turn`, so stunned and `EndRound`-skipped entries age it too. Wait returns and
    /// `begin_round` leave it alone. Whether it takes effect is `is_killing_frenzy_active`
    /// (APPROXIMATION).
    pub killing_frenzy_turns: u8,
}

/// Percent of current initiative a Dodge-capable unit adds to its effective
/// defense. The 15% figure is catalog-stated (UC-COMBAT-058); everything around
/// it is an APPROXIMATION (see `Unit::dodge_defense_bonus`).
pub const DODGE_DEFENSE_PERCENT: u32 = 15;

impl Unit {
    pub fn new(id: u32, name: &'static str, side: Side, stats: Stats, pos: Hex) -> Unit {
        Unit {
            id,
            name,
            side,
            hp: stats.hp,
            armour: stats.armour,
            ap: 0,
            fatigue: 0,
            stats,
            morale: Morale::Steady,
            pos,
            alive: true,
            fled: false,
            waited: false,
            turn_started: false,
            skip_turn: false,
            retreating: false,
            has_adrenaline: false,
            adrenaline_pending: false,
            has_rotation: false,
            has_footwork: false,
            has_fast_adaptation: false,
            fast_adaptation_stacks: 0,
            has_dodge: false,
            has_nine_lives: false,
            nine_lives_used: false,
            has_berserk: false,
            berserk_used: false,
            has_pathfinder: false,
            has_indomitable: false,
            indomitable_active: false,
            melee_reach: MeleeReach::Adjacent,
            stunned: false,
            has_stunning_blow: false,
            has_killing_frenzy: false,
            killing_frenzy_turns: 0,
        }
    }

    /// Shared battlefield-presence predicate: alive and not escaped. Occupancy,
    /// queues, targets, surround participants, outcome checks, AI and native
    /// tokens all use it.
    pub fn is_present(&self) -> bool {
        self.alive && !self.fled
    }

    /// Pure geometry: whether `at` lies within this unit's ordinary melee reach.
    /// Adjacent accepts distance 1; TwoHex accepts distances 1 and 2; distance
    /// zero is excluded. Presence, hostility, zones of control and resources are
    /// caller responsibilities, and nothing between the hexes is inspected
    /// (no obstruction check; APPROXIMATION). Widened arithmetic keeps extreme
    /// coordinates from overflowing.
    pub fn melee_reaches(&self, at: Hex) -> bool {
        self.melee_reach.reaches(self.pos, at)
    }

    /// Effective melee skill, leaving the base value in `Stats` unchanged.
    /// Integer division floors fractional values; saturation at `u32::MAX` and
    /// flooring are slice APPROXIMATION choices without runtime confirmation.
    pub fn effective_melee_skill(&self) -> u32 {
        effective_stat(self.stats.melee_skill, self.morale.modifier_percent())
    }

    /// Whether the Indomitable stance currently reduces incoming melee damage: the
    /// capability, the active flag, battlefield presence and non-Fleeing morale are all
    /// required. APPROXIMATION (UC-COMBAT-016 lists the 50% reduction; the gating is a
    /// slice choice). Pure: reads only `self`.
    pub fn is_indomitable_active(&self) -> bool {
        self.has_indomitable
            && self.indomitable_active
            && self.is_present()
            && self.morale != Morale::Fleeing
    }

    /// Whether the Killing Frenzy damage bonus applies to this unit's melee attacks now:
    /// the capability, a nonzero counter and battlefield presence are all required.
    /// APPROXIMATION (see `has_killing_frenzy`). Pure: reads only `self`.
    pub fn is_killing_frenzy_active(&self) -> bool {
        self.has_killing_frenzy && self.killing_frenzy_turns > 0 && self.is_present()
    }

    /// Current initiative: `i64(base initiative) - i64(fatigue / 2)`, with the
    /// integer division applied before the subtraction. Signed and unclamped.
    /// APPROXIMATION: the fatigue-to-initiative formula is a slice choice. The
    /// queue-only Fleeing penalty and pending Adrenaline bonus are not part of it.
    pub fn current_initiative(&self) -> i64 {
        i64::from(self.stats.initiative) - i64::from(self.fatigue / 2)
    }

    /// Dodge defense bonus: zero without `has_dodge`, otherwise
    /// `floor(max(current_initiative, 0) * DODGE_DEFENSE_PERCENT / 100)` in
    /// widened arithmetic. The catalog states 15% of current initiative
    /// (UC-COMBAT-058); the flooring, the zero floor and the capability flag are
    /// APPROXIMATION choices. Computed from live fatigue on every call.
    pub fn dodge_defense_bonus(&self) -> u32 {
        if !self.has_dodge {
            return 0;
        }
        let initiative = u128::from(self.current_initiative().max(0).unsigned_abs());
        (initiative * u128::from(DODGE_DEFENSE_PERCENT) / 100).min(u128::from(u32::MAX)) as u32
    }

    /// Effective defense, leaving the base value in `Stats` unchanged: the
    /// morale-adjusted base defense plus `dodge_defense_bonus`, saturating at
    /// `u32::MAX`. Morale scales only the base value, not the added Dodge bonus.
    /// Integer division floors fractional values; saturation at `u32::MAX` and
    /// flooring are slice APPROXIMATION choices without runtime confirmation.
    pub fn effective_defense(&self) -> u32 {
        effective_stat(self.stats.defense, self.morale.modifier_percent()).saturating_add(self.dodge_defense_bonus())
    }
}

fn effective_stat(base: u32, percentage: u32) -> u32 {
    (u64::from(base) * u64::from(percentage) / 100).min(u64::from(u32::MAX)) as u32
}
