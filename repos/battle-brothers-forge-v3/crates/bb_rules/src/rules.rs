//! Commands, turn order, AP, fatigue, attacks. APPROXIMATION unless noted.

use crate::hex::{self, Hex};
use crate::{
    BattleState, Command, EndReason, Event, MeleeReach, Morale, Outcome, Rng, RuleError, Side, TerrainKind,
    Unit,
};

pub const MOVE_AP: u32 = 2; // OBSERVED-NAME-ONLY (apCost): value invented
pub const MOVE_FATIGUE: u32 = 2;
/// Fatigue per attempted step for a `Unit::has_pathfinder` unit. UC-COMBAT-057
/// states a fatigue reduction (often 50%); the uniform 2-to-1 cost, its use for
/// both sides and every kind of step, and the capability flag are an APPROXIMATION.
pub const PATHFINDER_MOVE_FATIGUE: u32 = 1;

/// Fatigue one attempted step costs `u`. The single place that decides the walking
/// fatigue; previews, validation, execution, reachability, AI prefixes and the
/// automatic Flee/Withdraw step all call it. Pure: no state, RNG or events.
pub const fn move_fatigue(u: &Unit) -> u32 {
    if u.has_pathfinder {
        PATHFINDER_MOVE_FATIGUE
    } else {
        MOVE_FATIGUE
    }
}
/// AP to enter a Swamp hex. UC-COMBAT-005 states "Murky swamp water 4 AP"
/// (catalog-stated, not runtime-confirmed). Ground costs `MOVE_AP`.
pub const SWAMP_MOVE_AP: u32 = 4;
/// Swamp fatigue per attempted step as a multiple of the Ground `move_fatigue`
/// (4 for a normal unit, 2 for a Pathfinder unit). APPROXIMATION: the catalog
/// gives no swamp fatigue number; doubling keeps the Pathfinder halving uniform.
pub const SWAMP_FATIGUE_FACTOR: u32 = 2;
/// AP to enter a typical Snow hex. UC-COMBAT-005 states 3 AP for typical snow
/// (catalog-stated, not runtime-confirmed); its light-snow 2-AP exception is
/// excluded. The catalog does not confirm this step's fatigue cost.
pub const SNOW_MOVE_AP: u32 = 3;
/// Snow fatigue per attempted step as a multiple of Ground `move_fatigue`
/// (4 for a normal unit, 2 for a Pathfinder unit). Fatigue and Pathfinder
/// composition are APPROXIMATIONs; the catalog gives no snow fatigue number.
pub const SNOW_FATIGUE_FACTOR: u32 = 2;

/// AP of entering a hex of `kind`, before any uphill surcharge.
pub const fn terrain_move_ap(kind: TerrainKind) -> u32 {
    match kind {
        TerrainKind::Ground => MOVE_AP,
        TerrainKind::Swamp => SWAMP_MOVE_AP,
        TerrainKind::Snow => SNOW_MOVE_AP,
    }
}

/// Fatigue of entering a hex of `kind` for `u`, derived from the Ground baseline
/// `move_fatigue` so the Pathfinder capability is decided in one place.
pub const fn terrain_move_fatigue(u: &Unit, kind: TerrainKind) -> u32 {
    match kind {
        TerrainKind::Ground => move_fatigue(u),
        TerrainKind::Swamp => move_fatigue(u) * SWAMP_FATIGUE_FACTOR,
        TerrainKind::Snow => move_fatigue(u) * SNOW_FATIGUE_FACTOR,
    }
}
pub const ATTACK_AP: u32 = 4;
pub const ATTACK_FATIGUE: u32 = 10;
/// AP spent by `Command::StunningBlow`. APPROXIMATION: an independent skill cost,
/// not a recovered original value.
pub const STUNNING_BLOW_AP: u32 = 6;
/// Fatigue added by `Command::StunningBlow`. APPROXIMATION, see `STUNNING_BLOW_AP`.
pub const STUNNING_BLOW_FATIGUE: u32 = 25;
/// AP spent by `Command::Recover`. Catalog-stated in UC-COMBAT-065 (9 AP),
/// without original-runtime confirmation; spending the whole turn is likewise
/// catalog-stated. See `docs/forge/implemented/slice-state.md`.
pub const RECOVER_AP: u32 = 9;
/// Defender fatigue added by a successful attack. APPROXIMATION based on UC-COMBAT-006.
pub const DEFENDER_HIT_FATIGUE_GAIN: u32 = 5;
/// Defender fatigue added by a missed attack. APPROXIMATION of the catalog's dodge/block gain.
pub const DEFENDER_MISS_FATIGUE_GAIN: u32 = 2;
pub const FATIGUE_REGEN: u32 = 15;
pub const LAST_ROUND: u32 = 40;
/// HP lost from one surviving hit that triggers a morale check. Traced to
/// UC-COMBAT-018 ("15+ HP damage"); Fearsome's 1+ variant is not modeled.
pub const MORALE_CHECK_HP_LOSS_THRESHOLD: u32 = 15;
/// Percentage points per additional living ally adjacent to a melee target.
/// APPROXIMATION reconstructed from catalog text; original-runtime parity is unconfirmed.
pub const SURROUND_BONUS_PER_ADDITIONAL_ALLY: i32 = 5;
/// Melee hit points gained when the attacker stands higher than the target (UC-COMBAT-010).
pub const ELEVATION_DOWNHILL_BONUS: i128 = 10;
/// Melee hit points lost per level when the attacker stands lower (UC-COMBAT-010).
pub const ELEVATION_UPHILL_PENALTY_PER_LEVEL: i128 = 10;
/// Tag opening the non-Ground terrain suffix of `state_hash` (ASCII "TERR").
/// Tag for the Killing Frenzy counter suffix of `BattleState::state_hash` ("FRNZ").
pub const KILLING_FRENZY_HASH_TAG: u64 = 0x4652_4e5a;
pub const TERRAIN_HASH_TAG: u64 = 0x5445_5252;
/// Tag opening the nonzero-elevation suffix of `state_hash` (ASCII "ELEV").
pub const ELEVATION_HASH_TAG: u64 = 0x454c_4556;
/// Initiative points subtracted for a Fleeing unit at the next round's queue
/// sort. Traced to UC-COMBAT-003 ("Fleeing: -1000 initiative"). The current
/// queue is never reordered when a unit starts fleeing.
pub const FLEEING_INITIATIVE_PENALTY: i64 = 1000;
/// AP spent by `Command::Adrenaline`. APPROXIMATION: UC-COMBAT-066 is the catalog
/// authority for the skill; the cost is not runtime-confirmed.
pub const ADRENALINE_AP: u32 = 1;
/// Fatigue added by `Command::Adrenaline`. APPROXIMATION, see `ADRENALINE_AP`.
pub const ADRENALINE_FATIGUE: u32 = 20;
/// Initiative added to a pending unit's queue key at the next round's sort.
/// Traced to UC-COMBAT-066/UC-COMBAT-003; the bonus never changes base initiative.
pub const ADRENALINE_INITIATIVE_BONUS: i64 = 2000;
/// AP spent by `Command::Rotation`. Traced to UC-COMBAT-039 ("3 AP + fatigue cost").
/// A fixed skill cost: no movement or uphill surcharge applies.
pub const ROTATION_AP: u32 = 3;
/// Fatigue added by `Command::Rotation`. APPROXIMATION: the catalog gives no
/// number, so 25 is an explicit slice choice, not a recovered value.
pub const ROTATION_FATIGUE: u32 = 25;
/// AP spent by `Command::Footwork`. Follows the shared UC-COMBAT-039 precondition
/// ("3 AP + fatigue cost"). A fixed skill cost: no movement or uphill surcharge.
pub const FOOTWORK_AP: u32 = 3;
/// Fatigue added by `Command::Footwork`. APPROXIMATION: the catalog gives no
/// number, so 20 is an explicit slice choice, not a recovered value.
pub const FOOTWORK_FATIGUE: u32 = 20;
/// Melee hit points gained per Fast Adaptation stack. UC-COMBAT-067 supplies the
/// catalog percentage ("+10% (wiki; older guides 8%)"); reading it as additive
/// percentage points is an APPROXIMATION without original-runtime confirmation.
pub const FAST_ADAPTATION_BONUS_PER_STACK: i128 = 10;
/// Hit points a Nine Lives defender is left with after surviving a lethal melee hit.
/// APPROXIMATION: UC-COMBAT-059 says "a sliver of HP" / "low HP" but supplies no exact
/// value, so 1 HP is an explicit slice choice, not a recovered number.
pub const NINE_LIVES_SURVIVAL_HP: u32 = 1;
/// AP a Berserk attacker regains after its first ordinary melee kill in a scheduled
/// turn. The 4 AP figure is catalog-stated (UC-COMBAT-038); the cap at `ap_max`, the
/// trigger scope and the scheduling are APPROXIMATION choices (see `Unit::has_berserk`).
pub const BERSERK_AP_REFUND: u32 = 4;
/// Percentage added to each melee damage value while Killing Frenzy is active.
/// APPROXIMATION: UC-COMBAT-055 supplies only the temporary-buff concept and the
/// Berserk interaction, so the 25% bonus is an explicit slice choice, not a recovered value.
pub const KILLING_FRENZY_BONUS_PERCENT: u64 = 25;
/// Value of `Unit::killing_frenzy_turns` after a qualifying kill: the rest of the current
/// turn plus the next fresh turn. APPROXIMATION, see `KILLING_FRENZY_BONUS_PERCENT`.
pub const KILLING_FRENZY_TURNS: u8 = 2;
/// AP spent by `Command::Indomitable`. APPROXIMATION: UC-COMBAT-016 lists the
/// costs but its fidelity row is unconfirmed; the values are not runtime-confirmed.
pub const INDOMITABLE_AP: u32 = 5;
/// Fatigue added by `Command::Indomitable`. APPROXIMATION, see `INDOMITABLE_AP`.
pub const INDOMITABLE_FATIGUE: u32 = 25;

/// Whether `unit` itself can afford and use Adrenaline: the unit-level checks of
/// `Command::Adrenaline`, in `apply`'s order (`MissingAdrenaline`,
/// `AdrenalinePending`, `NotEnoughAp`, `TooFatigued`). It does not look at the
/// battle (active unit, controllability, presence); callers pair it with those
/// gates. Pure: reads only `unit`, consumes no RNG.
pub fn adrenaline_availability(unit: &Unit) -> Result<(), RuleError> {
    if !unit.has_adrenaline {
        return Err(RuleError::MissingAdrenaline);
    }
    if unit.adrenaline_pending {
        return Err(RuleError::AdrenalinePending);
    }
    if unit.ap < ADRENALINE_AP {
        return Err(RuleError::NotEnoughAp);
    }
    if unit
        .fatigue
        .checked_add(ADRENALINE_FATIGUE)
        .is_none_or(|f| f > unit.stats.fatigue_max)
    {
        return Err(RuleError::TooFatigued);
    }
    Ok(())
}

/// Whether `unit` itself can afford and use Indomitable: the unit-level checks of
/// `Command::Indomitable`, in `apply`'s order (`MissingIndomitable`,
/// `IndomitableActive`, `NotEnoughAp`, `TooFatigued`; the fatigue sum is checked).
/// It does not look at the battle (running, active unit, controllability,
/// presence); callers supply those gates. Pure: reads only `unit`, consumes no RNG.
pub fn indomitable_availability(unit: &Unit) -> Result<(), RuleError> {
    if !unit.has_indomitable {
        return Err(RuleError::MissingIndomitable);
    }
    if unit.indomitable_active {
        return Err(RuleError::IndomitableActive);
    }
    if unit.ap < INDOMITABLE_AP {
        return Err(RuleError::NotEnoughAp);
    }
    if unit
        .fatigue
        .checked_add(INDOMITABLE_FATIGUE)
        .is_none_or(|f| f > unit.stats.fatigue_max)
    {
        return Err(RuleError::TooFatigued);
    }
    Ok(())
}

/// Overflow-safe "in the map and not an obstacle". `Hex::in_map` does signed
/// arithmetic that overflows for extreme coordinates, so each axis is bounded
/// first; the sum `q + r` is then safe.
fn is_passable_map_hex(h: Hex) -> bool {
    let radius = hex::RADIUS.unsigned_abs();
    h.q.unsigned_abs() <= radius && h.r.unsigned_abs() <= radius && h.in_map() && !h.is_obstacle()
}

/// Whether `h` lies on the outermost ring of the map (where a Fleeing unit escapes).
fn is_edge(h: Hex) -> bool {
    h.q.abs().max(h.r.abs()).max(h.s().abs()) == hex::RADIUS
}

/// Whether arriving at `at` would drop `u` to Breaking under the map-edge rule
/// (UC-COMBAT-048, APPROXIMATION): `at` is an edge hex and `u` is present, has no
/// retreat intent and is Confident, Steady or Wavering. Shared by `walk`, which
/// evaluates the actual state at arrival, and `preview_move`, which evaluates the
/// current state. Callers decide whether the move is exempt (Flee and Withdraw).
fn edge_penalty_applies(u: &Unit, at: Hex) -> bool {
    is_edge(at)
        && u.is_present()
        && !u.retreating
        && matches!(
            u.morale,
            Morale::Confident | Morale::Steady | Morale::Wavering
        )
}

/// Armour soak. Returns (absorbed, hp_loss). HP loss saturates at `u32::MAX` if
/// the mathematical armour-leak sum exceeds the event/state representation.
/// This saturation is a representation choice within the existing armour-model
/// APPROXIMATION, not a new fidelity claim.
pub fn soak(armour: u32, dmg: u32) -> (u32, u32) {
    let absorbed = armour.min(dmg);
    let hp_loss = (u64::from(dmg - absorbed) + if absorbed > 0 { u64::from(dmg / 10) } else { 0 })
        .min(u64::from(u32::MAX)) as u32;
    (absorbed, hp_loss)
}

/// Base melee damage after an active Killing Frenzy: `floor(base * 125 / 100)` in widened
/// arithmetic, saturating at `u32::MAX`. APPROXIMATION, see `KILLING_FRENZY_BONUS_PERCENT`.
fn damage_after_killing_frenzy(damage: u32, attacker: &Unit) -> u32 {
    if attacker.is_killing_frenzy_active() {
        let boosted = u64::from(damage) * (100 + KILLING_FRENZY_BONUS_PERCENT) / 100;
        boosted.min(u64::from(u32::MAX)) as u32
    } else {
        damage
    }
}

/// Damage supplied to armour soak for one base melee value: the attacker's Killing
/// Frenzy bonus first, then the defender's Indomitable reduction. Shared by execution
/// and both attack-preview APIs.
fn melee_damage(damage: u32, attacker: &Unit, defender: &Unit) -> u32 {
    damage_after_indomitable(damage_after_killing_frenzy(damage, attacker), defender)
}

/// Damage supplied to armour soak after an effective Indomitable stance.
/// The stance halves each sampled value with integer flooring and permits zero;
/// this is an APPROXIMATION of UC-COMBAT-016, without original-runtime confirmation.
fn damage_after_indomitable(damage: u32, defender: &Unit) -> u32 {
    if defender.is_indomitable_active() {
        damage / 2
    } else {
        damage
    }
}

/// **APPROXIMATION:** melee attack information for the current battle snapshot.
/// The inclusive damage range is conditional on a hit and is supplied to armour
/// soak before armour processing; it does not predict HP loss. Both endpoints include an
/// active attacker's Killing Frenzy bonus and the defender's Indomitable reduction.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AttackPreview {
    pub attacker: u32,
    pub target: u32,
    pub ap_cost: u32,
    pub fatigue_cost: u32,
    pub hit_chance: u32,
    pub damage_min: u32,
    pub damage_max: u32,
}

/// Private validated attack plan shared by previews and the ordinary and
/// Stunning Blow attack executions.
struct AttackPlan {
    preview: AttackPreview,
}

/// First departure on a planned route from which enemies could react.
/// `controllers` keeps the deterministic `enemy_controllers_at` order.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MoveThreat {
    pub departure: Hex,
    pub controllers: Vec<u32>,
}

/// Read-only plan for a `Command::Move`: the full route and its nominal costs
/// before any reaction effects. `first_threat` only reports potential reactions
/// (UC-COMBAT-007/008, APPROXIMATION): no hit, damage, survival or arrival
/// prediction. The mover may be stopped, or killed, before reaching `path.last()`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MovePreview {
    /// Excludes the starting hex, includes the destination.
    pub path: Vec<Hex>,
    pub ap_cost: u32,
    pub fatigue_cost: u32,
    /// AP left after paying the full nominal route cost, before reactions.
    pub ap_remaining: u32,
    /// Whether one basic melee attack's AP and fatigue budgets fit after this
    /// planned move. APPROXIMATION: this does not establish target availability
    /// or account for reaction effects.
    pub attack_budget_available: bool,
    pub first_threat: Option<MoveThreat>,
    /// First arrival hex on `path` where the mover's current morale would take the
    /// map-edge penalty (UC-COMBAT-048, APPROXIMATION). A conditional route
    /// consequence from current state only: not a prediction of arrival, and
    /// reactions before it are not simulated. The start is never reported unless
    /// it reappears as an arrival. `None` when no hex qualifies or the mover is
    /// already Breaking.
    pub first_edge_penalty: Option<Hex>,
}

/// Validated `Command::Rotation` plan for the current battle snapshot.
/// This is a read-only preview, not a reservation: revalidate by submitting the
/// command because earlier commands may change positions, budgets, capability,
/// morale or presence. Rotation itself remains deterministic and provokes no
/// reactions.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RotationPreview {
    pub unit: u32,
    pub target: u32,
    pub unit_to: Hex,
    pub target_to: Hex,
    pub ap_cost: u32,
    pub fatigue_cost: u32,
    /// Participants whose arrival would change their current morale to Breaking,
    /// paired with that arrival hex and sorted by unit id.
    pub edge_penalties: Vec<(u32, Hex)>,
}

/// Validated `Command::Footwork` plan for the current battle snapshot.
/// This is a read-only preview, not a reservation: revalidate by submitting the
/// command because earlier commands may change positions, budgets, capability,
/// morale or presence. Footwork itself is deterministic and provokes no reactions.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FootworkPreview {
    pub unit: u32,
    pub from: Hex,
    pub to: Hex,
    pub ap_cost: u32,
    pub fatigue_cost: u32,
    /// The destination, when arriving there would change the actor's current
    /// morale to Breaking (map-edge rule); `None` otherwise.
    pub edge_penalty: Option<Hex>,
}

impl BattleState {
    pub fn new(seed: u64) -> BattleState {
        let mut s = BattleState {
            units: crate::scenario::units(),
            rng: Rng::new(seed),
            round: 1,
            queue: Vec::new(),
            outcome: None,
            end_reason: None,
            elevations: Default::default(),
            ground: Default::default(),
        };
        s.begin_round();
        s.begin_turn();
        s
    }

    fn occupied(&self) -> Vec<Hex> {
        self.units
            .iter()
            .filter(|u| u.is_present())
            .map(|u| u.pos)
            .collect()
    }

    /// Queue: present units by `initiative - fatigue / 2` (minus
    /// `FLEEING_INITIATIVE_PENALTY` for Fleeing units, plus
    /// `ADRENALINE_INITIATIVE_BONUS` for pending Adrenaline) descending, ties by
    /// id. Pending flags are cleared for every unit once the queue is built.
    fn begin_round(&mut self) -> Vec<Event> {
        for u in &mut self.units {
            u.waited = false;
            u.turn_started = false;
            u.skip_turn = false;
        }
        let key = |id: &u32| {
            let u = &self.units[*id as usize];
            let penalty = if u.morale == Morale::Fleeing {
                FLEEING_INITIATIVE_PENALTY
            } else {
                0
            };
            let bonus = if u.adrenaline_pending {
                ADRENALINE_INITIATIVE_BONUS
            } else {
                0
            };
            u.current_initiative() - penalty + bonus
        };
        let mut q: Vec<u32> = self
            .units
            .iter()
            .filter(|u| u.is_present())
            .map(|u| u.id)
            .collect();
        q.sort_by(|a, b| key(b).cmp(&key(a)).then(a.cmp(b)));
        self.queue = q;
        for u in &mut self.units {
            u.adrenaline_pending = false;
        }
        vec![Event::RoundStarted { round: self.round }]
    }

    /// Refresh AP and regenerate fatigue, once per round per unit (not after a Wait).
    fn begin_turn(&mut self) -> Vec<Event> {
        let Some(&id) = self.queue.first() else {
            return vec![];
        };
        let u = &mut self.units[id as usize];
        if u.turn_started {
            return vec![];
        }
        u.turn_started = true;
        u.berserk_used = false;
        // Indomitable expires here, at the unit's next fresh turn, before TurnStarted.
        u.indomitable_active = false;
        // Killing Frenzy ages once per fresh entry (stunned and skipped entries included).
        u.killing_frenzy_turns = u.killing_frenzy_turns.saturating_sub(1);
        u.ap = u.stats.ap_max;
        u.fatigue = u.fatigue.saturating_sub(FATIGUE_REGEN);
        let mut ev = vec![Event::TurnStarted { unit: id }];
        self.self_rally(id, &mut ev);
        ev
    }

    /// Self-rally check at a Fleeing unit's scheduled turn start, right after the
    /// refresh. APPROXIMATION based on UC-COMBAT-018/019 (invented formula and
    /// destination, not a recovered rule): eligible when the unit is present,
    /// Fleeing, not on a map edge (the immediate edge escape keeps precedence) and
    /// no present enemy is adjacent (a Fleeing enemy counts; dead and escaped ones
    /// do not). One draw: chance = min(resolve, 100), success when
    /// `next_u64() % 100 < chance`. Success moves Fleeing to Breaking and clears
    /// a pending `skip_turn`; failure changes nothing. No AP or fatigue is charged.
    /// The caller's once-per-round `turn_started` guard makes this at most one
    /// check per unit per round.
    fn self_rally(&mut self, unit: u32, ev: &mut Vec<Event>) {
        let u = &self.units[unit as usize];
        if !u.is_present() || u.morale != Morale::Fleeing || is_edge(u.pos) {
            return;
        }
        let engaged = self
            .units
            .iter()
            .any(|e| e.is_present() && e.side != u.side && e.pos.distance(u.pos) == 1);
        if engaged {
            return;
        }
        let chance = u.stats.resolve.min(100);
        let roll = (self.rng.next_u64() % 100) as u32;
        let u = &mut self.units[unit as usize];
        let before = u.morale;
        if roll < chance {
            u.morale = Morale::Breaking;
            u.skip_turn = false;
        }
        ev.push(Event::SelfRallyChecked {
            unit,
            roll,
            chance,
            before,
            after: u.morale,
        });
    }

    fn check_outcome(&mut self, ev: &mut Vec<Event>) {
        let present = |side| self.units.iter().any(|u| u.is_present() && u.side == side);
        let escaped = |side: Option<Side>| {
            self.units
                .iter()
                .any(|u| u.alive && u.fled && side.is_none_or(|s| u.side == s))
        };
        // Players are judged first: all players gone is never a win, even when
        // the enemies left too (UC-COMBAT-023).
        let outcome = if !present(Side::Player) {
            Some(if escaped(Some(Side::Player)) {
                Outcome::Retreat
            } else {
                Outcome::Defeat
            })
        } else if !present(Side::Enemy) {
            Some(Outcome::Victory)
        } else {
            None
        };
        if let Some(o) = outcome {
            let reason = if escaped(None) {
                EndReason::Escape
            } else {
                EndReason::Elimination
            };
            self.outcome = Some(o);
            self.end_reason = Some(reason);
            ev.push(Event::BattleEnded { outcome: o, reason });
        }
    }

    /// Starts the next living unit's turn after the active unit left `queue`.
    /// An exhausted queue begins the next round, or ends the battle at the time limit.
    /// A stunned unit gets the same turn-start processing, then its stun is
    /// consumed with the entry (see `apply_stun`). A unit marked by `EndRound`
    /// still gets its once-per-round turn-start
    /// refresh (none after a Wait), then loses its AP and is passed over, except
    /// Fleeing and retreating units: a pending skip never suppresses their
    /// automatic turns.
    fn advance_turn(&mut self, ev: &mut Vec<Event>) {
        loop {
            if self.queue.is_empty() {
                if self.round + 1 > LAST_ROUND {
                    self.outcome = Some(Outcome::Defeat);
                    self.end_reason = Some(EndReason::TimeLimit);
                    ev.push(Event::BattleEnded {
                        outcome: Outcome::Defeat,
                        reason: EndReason::TimeLimit,
                    });
                    return;
                }
                self.round += 1;
                ev.extend(self.begin_round());
            }
            ev.extend(self.begin_turn());
            let Some(&id) = self.queue.first() else {
                return;
            };
            // Stun consumes the entry first, so a combined `EndRound` skip costs
            // nothing extra and Fleeing/retreating automatic turns are forfeited too.
            if self.units[id as usize].stunned {
                let u = &mut self.units[id as usize];
                u.ap = 0;
                u.stunned = false;
                ev.push(Event::StunExpired { unit: id });
                self.queue.remove(0);
                continue;
            }
            let u = &self.units[id as usize];
            if !u.skip_turn || u.morale == Morale::Fleeing || u.retreating {
                return;
            }
            self.units[id as usize].ap = 0;
            self.queue.remove(0);
        }
    }

    /// Stun system effect (partial UC-COMBAT-028/016); not a player action. The only
    /// skill that inflicts it is `Command::StunningBlow`. Rejections (`BattleOver`, then `InvalidTarget` for
    /// an unknown, dead or escaped unit) change nothing. An effective Indomitable
    /// stance (`is_indomitable_active`) yields only `StunResisted`; an already
    /// stunned target is an idempotent success with no events and no stacking.
    /// Otherwise `stunned` is set (`StunApplied`). An inactive target keeps its
    /// queue position and resources and forfeits its next entry in `advance_turn`;
    /// the active target is advanced at once through normal advancement, which
    /// consumes its current entry (`StunExpired`, then the next turn's events).
    /// Consumes no RNG itself. The stun survives `begin_round`. Catalog-stated:
    /// stunned units skip a turn and exert no ZoC, and Indomitable grants stun
    /// immunity. APPROXIMATION: boolean storage, next-entry duration, active-target
    /// scheduling, Wait/EndRound/fleeing interactions, event order, hash encoding.
    pub fn apply_stun(&mut self, unit: u32) -> Result<Vec<Event>, RuleError> {
        if self.outcome.is_some() {
            return Err(RuleError::BattleOver);
        }
        let target = self
            .units
            .get(unit as usize)
            .filter(|u| u.is_present())
            .ok_or(RuleError::InvalidTarget)?;
        if target.is_indomitable_active() {
            return Ok(vec![Event::StunResisted { unit }]);
        }
        if target.stunned {
            return Ok(vec![]);
        }
        self.units[unit as usize].stunned = true;
        let mut ev = vec![Event::StunApplied { unit }];
        if self.queue.first() == Some(&unit) {
            self.advance_turn(&mut ev);
        }
        Ok(ev)
    }

    /// Shared melee resolution (ordinary attack, Stunning Blow, opportunity attack): roll, damage (raised first by an active attacker's Killing Frenzy, then halved for an effective Indomitable defender), armour, incoming defender fatigue,
    /// Nine Lives interception, death handling, the heavy-damage morale check and the killer's positive kill check. `pay_costs` charges the
    /// attacker's AP and fatigue; opportunity attacks pass `None`. `stun_on_hit` (only
    /// `Command::StunningBlow`) calls `apply_stun` on a hit against a surviving target
    /// after `Attacked` and any `NineLivesTriggered`, before the heavy-hit morale check,
    /// so an effective Indomitable stance resists before that check can cancel it. No
    /// extra RNG. Validation and elimination checks stay with the caller. Returns
    /// whether the attack hit.
    fn resolve_attack(
        &mut self,
        attacker: u32,
        target: u32,
        costs: Option<(u32, u32)>,
        chance: u32,
        stun_on_hit: bool,
        ev: &mut Vec<Event>,
    ) -> bool {
        let (dmg_min, dmg_max) = {
            let a = &self.units[attacker as usize];
            (a.stats.dmg_min, a.stats.dmg_max)
        };
        // Modulo bias (2^64 % 100 != 0) is negligible and accepted.
        let roll = (self.rng.next_u64() % 100) as u32;
        let hit = roll < chance;
        // Fast Adaptation bookkeeping (APPROXIMATION, UC-COMBAT-067): the chance above used
        // the pre-attack counter; a capable attacker now gains a stack on a miss or resets on
        // any hit (armour-only and lethal included). No RNG, no event.
        let a = &mut self.units[attacker as usize];
        if a.has_fast_adaptation {
            a.fast_adaptation_stacks = if hit {
                0
            } else {
                a.fast_adaptation_stacks.saturating_add(1)
            };
        }
        let (mut dmg, mut absorbed, mut hp_loss) = (0, 0, 0);
        let mut protected = false;
        if let Some((ap_cost, fatigue_cost)) = costs {
            let a = &mut self.units[attacker as usize];
            a.ap -= ap_cost;
            a.fatigue += fatigue_cost;
        }
        if hit {
            let width = u64::from(dmg_max) - u64::from(dmg_min) + 1;
            let sample = u64::from(dmg_min) + self.rng.next_u64() % width;
            dmg = sample as u32;
            // The base range is sampled unmodified; an active Killing Frenzy raises the
            // sampled value, then Indomitable reduces it before armour. The hit still
            // counts at zero damage and no extra RNG is consumed.
            dmg = melee_damage(dmg, &self.units[attacker as usize], &self.units[target as usize]);
            let t = &mut self.units[target as usize];
            (absorbed, hp_loss) = soak(t.armour, dmg);
            t.armour -= absorbed;
            // Nine Lives (partial UC-COMBAT-059): an unused, capable, present defender whose
            // would-be loss reaches zero HP stays at 1 HP instead. `Attacked` then reports
            // the actual HP lost. No RNG, and AP, fatigue and armour are untouched.
            if t.has_nine_lives
                && !t.nine_lives_used
                && t.is_present()
                && t.hp > 0
                && hp_loss >= t.hp
            {
                hp_loss = t.hp - NINE_LIVES_SURVIVAL_HP.min(t.hp);
                t.nine_lives_used = true;
                protected = true;
            }
            t.hp = t.hp.saturating_sub(hp_loss);
        }
        let defender_fatigue_gain = if hit {
            DEFENDER_HIT_FATIGUE_GAIN
        } else {
            DEFENDER_MISS_FATIGUE_GAIN
        };
        let defender = &mut self.units[target as usize];
        defender.fatigue = defender
            .fatigue
            .saturating_add(defender_fatigue_gain)
            .min(defender.stats.fatigue_max);
        ev.push(Event::Attacked {
            attacker,
            target,
            hit,
            roll,
            chance,
            dmg,
            absorbed,
            hp_loss,
        });
        if protected {
            ev.push(Event::NineLivesTriggered { unit: target });
        }
        if self.units[target as usize].hp == 0 {
            self.units[target as usize].alive = false;
            self.queue.retain(|&id| id != target);
            ev.push(Event::Died { unit: target });
            self.check_ally_death_morale(target, ev);
            self.check_kill_morale(attacker, target, ev);
        } else {
            if stun_on_hit
                && hit
                && let Ok(stun_events) = self.apply_stun(target)
            {
                ev.extend(stun_events);
            }
            if hit && hp_loss >= MORALE_CHECK_HP_LOSS_THRESHOLD {
                // The defender survived, so `hp_loss` is the actual HP lost.
                self.check_morale(target, ev);
            }
        }
        hit
    }

    /// Berserk (partial UC-COMBAT-038), called after an ordinary `Command::Attack` or a
    /// `Command::StunningBlow` has been resolved, so after the death's ally-death and kill-morale events and before
    /// the outcome check. A present, capable attacker that has not used it this turn and
    /// actually killed `target` regains `BERSERK_AP_REFUND` AP, capped at `ap_max`, and
    /// the use is consumed; the event records the AP actually gained (possibly 0).
    /// Opportunity attacks never reach this (a miss, a surviving target or Nine Lives
    /// interception cannot trigger it either). No RNG, no fatigue, HP or armour change, and
    /// the turn is neither advanced nor refreshed. 4 AP and once-per-turn are
    /// catalog-stated; the paid-melee-only trigger (ordinary or Stunning Blow), the cap, both-side capability and
    /// the event slot are an APPROXIMATION.
    fn berserk_refund(&mut self, attacker: u32, target: u32, ev: &mut Vec<Event>) {
        if self.units[target as usize].alive {
            return;
        }
        let a = &mut self.units[attacker as usize];
        if !a.has_berserk || a.berserk_used || !a.is_present() {
            return;
        }
        let ap_gained = a.stats.ap_max.saturating_sub(a.ap).min(BERSERK_AP_REFUND);
        a.ap += ap_gained;
        a.berserk_used = true;
        ev.push(Event::BerserkTriggered {
            unit: attacker,
            ap_gained,
        });
    }

    /// Killing Frenzy (partial UC-COMBAT-055), called after an ordinary `Command::Attack` or
    /// `Command::StunningBlow` and its Berserk handling, before the outcome check. A present,
    /// capable attacker that actually killed `target` has its counter set to
    /// `KILLING_FRENZY_TURNS` (a refresh, never a stack). The killing blow itself used the
    /// pre-kill modifier. Misses, survivors, Nine Lives interceptions and opportunity
    /// attacks cannot reach this with a dead target. No RNG, no event. The paid-kill trigger
    /// restriction, duration and refresh timing are an APPROXIMATION.
    fn killing_frenzy_refresh(&mut self, attacker: u32, target: u32) {
        if self.units[target as usize].alive {
            return;
        }
        let a = &mut self.units[attacker as usize];
        if a.has_killing_frenzy && a.is_present() {
            a.killing_frenzy_turns = KILLING_FRENZY_TURNS;
        }
    }

    /// Negative Resolve check, one RNG draw. APPROXIMATION (invented formula, not
    /// a recovered rule): chance = min(resolve, 100), pass when
    /// `next_u64() % 100 < chance`. Failure lowers morale one step; Breaking
    /// goes to Fleeing and Fleeing stays Fleeing. The draw is consumed in every
    /// state. A unit entering Fleeing loses a pending `EndRound` skip.
    /// Returns (roll, chance, before, after).
    fn negative_morale_check(&mut self, unit: u32) -> (u32, u32, Morale, Morale) {
        let chance = self.units[unit as usize].stats.resolve.min(100);
        let roll = (self.rng.next_u64() % 100) as u32;
        let u = &mut self.units[unit as usize];
        let before = u.morale;
        if roll >= chance {
            u.morale = before.lowered();
            if u.morale == Morale::Fleeing {
                u.skip_turn = false;
                // A Fleeing unit loses its Indomitable stance (APPROXIMATION).
                u.indomitable_active = false;
            }
        }
        (roll, chance, before, u.morale)
    }

    /// Negative check after a surviving heavy hit (`MoraleChecked`).
    fn check_morale(&mut self, unit: u32, ev: &mut Vec<Event>) {
        let (roll, chance, before, after) = self.negative_morale_check(unit);
        ev.push(Event::MoraleChecked {
            unit,
            roll,
            chance,
            before,
            after,
        });
    }

    /// Negative checks for the allies of a unit that just died in melee, called
    /// directly after `Died`. The trigger traces to UC-COMBAT-018; the affected
    /// set is an APPROXIMATION: every present same-side ally (turn finished,
    /// waiting, marked, or already Fleeing), regardless of distance, in ascending
    /// id order. The casualty, enemies, dead units and escapees are excluded.
    /// One check per ally per death; morale changes do not propagate further,
    /// and the queue is never reordered.
    fn check_ally_death_morale(&mut self, fallen: u32, ev: &mut Vec<Event>) {
        let side = self.units[fallen as usize].side;
        let allies: Vec<u32> = self
            .units
            .iter()
            .filter(|u| u.is_present() && u.side == side)
            .map(|u| u.id)
            .collect();
        for unit in allies {
            let (roll, chance, before, after) = self.negative_morale_check(unit);
            ev.push(Event::AllyDeathMoraleChecked {
                unit,
                fallen,
                roll,
                chance,
                before,
                after,
            });
        }
    }

    /// Positive Resolve check for the killer of `fallen`, called after the
    /// ally-death checks of that death. UC-COMBAT-018 is the catalog authority
    /// for checks on kills; the killer-only affected set, formula and scheduling
    /// are an APPROXIMATION without original-runtime confirmation. Eligible: the
    /// killer is present and not Fleeing (a retreating killer stays eligible and
    /// keeps its retreat intent). At the map edge the effective chance is zero;
    /// elsewhere it is min(resolve, 100). One draw: success when
    /// `next_u64() % 100 < chance`, applying `Morale::raised`. The draw and event
    /// happen even at the edge or when the killer is already Confident. No other
    /// unit, skip flag or queue order changes.
    fn check_kill_morale(&mut self, killer: u32, fallen: u32, ev: &mut Vec<Event>) {
        let u = &self.units[killer as usize];
        if !u.is_present() || u.morale == Morale::Fleeing {
            return;
        }
        let chance = if is_edge(u.pos) {
            0
        } else {
            u.stats.resolve.min(100)
        };
        let roll = (self.rng.next_u64() % 100) as u32;
        let u = &mut self.units[killer as usize];
        let before = u.morale;
        if roll < chance {
            u.morale = before.raised();
        }
        ev.push(Event::KillMoraleChecked {
            unit: killer,
            fallen,
            roll,
            chance,
            before,
            after: u.morale,
        });
    }

    /// Executes an accepted move step by step with opportunity attacks.
    /// APPROXIMATION of UC-COMBAT-007/008, see `enemy_controllers_at`. Never fails:
    /// the full path was validated by the caller, and a step that incoming
    /// fatigue makes unaffordable just stops the walk.
    ///
    /// `edge_penalty` is true for ordinary `Command::Move` and false for the
    /// automatic Flee/Withdraw step. After each step actually taken onto a map-edge
    /// hex, a present, non-Fleeing, non-retreating mover at Confident, Steady or
    /// Wavering drops directly to Breaking (`EdgeMoraleChanged`, after the `Moved`
    /// segment that ends on the edge and before any later departure reaction).
    /// UC-COMBAT-048 is the catalog authority; direct assignment to Breaking, the
    /// application to both sides and the scheduling are an APPROXIMATION. No RNG,
    /// no Resolve check and no AP or fatigue cost; the walk then continues.
    fn walk(&mut self, mover: u32, path: Vec<Hex>, edge_penalty: bool, ev: &mut Vec<Event>) {
        let mut done: Vec<Hex> = Vec::new();
        for step in path {
            {
                // The full cost of the intended step (destination terrain and uphill surcharge
                // included) is checked and paid before any reaction, so a stopped attempt
                // still spends it.
                let (ap_cost, step_fatigue) = self.step_costs(
                    &self.units[mover as usize],
                    self.units[mover as usize].pos,
                    step,
                );
                let u = &mut self.units[mover as usize];
                if u.ap < ap_cost
                    || u.fatigue
                        .checked_add(step_fatigue)
                        .is_none_or(|f| f > u.stats.fatigue_max)
                {
                    break;
                }
                u.ap -= ap_cost;
                u.fatigue += step_fatigue;
            }
            let controllers = self.enemy_controllers_at(mover, self.units[mover as usize].pos);
            if !controllers.is_empty() && !done.is_empty() {
                ev.push(Event::Moved {
                    unit: mover,
                    path: std::mem::take(&mut done),
                });
            }
            let mut stopped = false;
            for controller in controllers {
                let chance = self.hit_chance(controller, mover);
                stopped |= self.resolve_attack(controller, mover, None, chance, false, ev);
                if !self.units[mover as usize].alive {
                    break;
                }
            }
            if stopped {
                break;
            }
            self.units[mover as usize].pos = step;
            done.push(step);
            if edge_penalty && is_edge(step) {
                let u = &mut self.units[mover as usize];
                if edge_penalty_applies(u, step) {
                    let before = u.morale;
                    u.morale = Morale::Breaking;
                    ev.push(Event::Moved {
                        unit: mover,
                        path: std::mem::take(&mut done),
                    });
                    ev.push(Event::EdgeMoraleChanged {
                        unit: mover,
                        at: step,
                        before,
                        after: Morale::Breaking,
                    });
                }
            }
        }
        if !done.is_empty() {
            ev.push(Event::Moved {
                unit: mover,
                path: done,
            });
        }
        if !self.units[mover as usize].alive {
            // The helper already removed the mover from the queue.
            self.check_outcome(ev);
            if self.outcome.is_none() {
                self.advance_turn(ev);
            }
        }
    }

    /// Present, non-Fleeing units on the other side adjacent to `at`, in
    /// ascending id order. Read-only: consumes no RNG. APPROXIMATION based on
    /// UC-COMBAT-007: every present non-Fleeing, non-stunned unit is treated as
    /// melee-capable (stunned units exert no ZoC, catalog-stated);
    /// ranged, smoke, net/root and per-creature exceptions are deferred.
    pub fn enemy_controllers_at(&self, unit: u32, at: Hex) -> Vec<u32> {
        let Some(u) = self.units.get(unit as usize) else {
            return vec![];
        };
        self.units
            .iter()
            .filter(|c| {
                c.is_present()
                    && c.morale != Morale::Fleeing
                    && !c.stunned
                    && c.side != u.side
                    && c.pos.distance(at) == 1
            })
            .map(|c| c.id)
            .collect()
    }

    /// Shared by `apply` and `preview_move`: battle-over, then active-unit checks.
    fn ensure_active(&self, actor: u32) -> Result<(), RuleError> {
        if self.outcome.is_some() {
            return Err(RuleError::BattleOver);
        }
        if self.queue.first() != Some(&actor) {
            return Err(RuleError::NotActiveUnit);
        }
        Ok(())
    }

    /// `ensure_active`, then reject a retreating or Fleeing actor: ordinary
    /// action commands (and movement previews) are not available to it, only
    /// `Command::Withdraw` (retreating) or `Command::Flee` (Fleeing). Retreat
    /// intent is reported first when a unit has both.
    fn ensure_controllable(&self, actor: u32) -> Result<(), RuleError> {
        self.ensure_active(actor)?;
        if self.units[actor as usize].retreating {
            return Err(RuleError::RetreatingUnit);
        }
        if self.units[actor as usize].morale == Morale::Fleeing {
            return Err(RuleError::FleeingUnit);
        }
        Ok(())
    }

    /// Whether `Command::Recover` would be accepted for `unit` now, with the
    /// error `apply` returns: battle over, not the active unit, retreating,
    /// Fleeing, absent, then too little AP. Zero fatigue is eligible. Pure:
    /// reads the state only, draws no RNG. `apply` runs this same query.
    pub fn recover_availability(&self, unit: u32) -> Result<(), RuleError> {
        self.ensure_controllable(unit)?;
        let u = self
            .units
            .get(unit as usize)
            .ok_or(RuleError::NotActiveUnit)?;
        if !u.is_present() {
            return Err(RuleError::NotActiveUnit);
        }
        if u.ap < RECOVER_AP {
            return Err(RuleError::NotEnoughAp);
        }
        Ok(())
    }

    /// One automatic command for the validated active Fleeing or retreating
    /// unit (`Command::Flee` and `Command::Withdraw` share it).
    /// APPROXIMATION (no catalog route rule): the target is a passable,
    /// unoccupied map-edge hex at the smallest BFS distance (existing neighbour
    /// order, occupied hexes block); ties go to the endpoint farthest from its
    /// nearest present enemy, then to the lowest axial coordinate (q, then r).
    /// The route offers no safe-route guarantee and may cross enemy ZoC. Only
    /// the first step is attempted, through `walk`, so reactions, costs and
    /// stops are the ordinary ones, including the destination terrain and uphill
    /// AP surcharge of that step (`step_costs`); the route is still the fewest-steps BFS route, not
    /// the cheapest. Consumes no RNG itself. Every call moves,
    /// spends the attempted step's budget, escapes or ends the turn.
    fn automatic_step(&mut self, unit: u32, ev: &mut Vec<Event>) {
        let (pos, side, ap, fatigue, fmax) = {
            let u = &self.units[unit as usize];
            (u.pos, u.side, u.ap, u.fatigue, u.stats.fatigue_max)
        };
        if is_edge(pos) {
            self.escape(unit, ev);
            return;
        }
        let occupied = self.occupied();
        let nearest_enemy = |h: Hex| {
            self.units
                .iter()
                .filter(|e| e.is_present() && e.side != side)
                .map(|e| e.pos.distance(h))
                .min()
                .unwrap_or(u32::MAX)
        };
        let exit = hex::bfs_distances(pos, &occupied)
            .into_iter()
            .filter(|&(h, d)| d >= 1 && is_edge(h))
            .min_by_key(|&(h, d)| (d, std::cmp::Reverse(nearest_enemy(h)), h))
            .map(|(h, _)| h);
        let step = exit
            .and_then(|h| hex::bfs_path(pos, h, &occupied))
            .and_then(|p| p.first().copied());
        let affordable = |step: Hex| {
            let (ap_cost, step_fatigue) = self.step_costs(&self.units[unit as usize], pos, step);
            ap >= ap_cost && fatigue.checked_add(step_fatigue).is_some_and(|f| f <= fmax)
        };
        let Some(step) = step.filter(|&s| affordable(s)) else {
            self.queue.retain(|&id| id != unit);
            self.advance_turn(ev);
            return;
        };
        self.walk(unit, vec![step], false, ev);
        // A killed mover was already handled (outcome check and advance) by `walk`;
        // a stopped mover is not at `step`, so it cannot escape.
        let u = &self.units[unit as usize];
        if u.is_present() && u.pos == step && is_edge(step) {
            self.escape(unit, ev);
        }
    }

    /// A unit leaves the battle alive: it keeps `alive`, HP and equipment, but
    /// loses queue membership and occupancy. No outcome check or advancement.
    fn leave_battle(&mut self, unit: u32, ev: &mut Vec<Event>) {
        self.units[unit as usize].fled = true;
        self.queue.retain(|&id| id != unit);
        ev.push(Event::Fled { unit });
    }

    /// The active unit leaves the battle (see `leave_battle`). Outcome is checked
    /// before advancing, and no turn starts after the battle ended.
    fn escape(&mut self, unit: u32, ev: &mut Vec<Event>) {
        self.leave_battle(unit, ev);
        self.check_outcome(ev);
        if self.outcome.is_none() {
            self.advance_turn(ev);
        }
    }

    /// Party retreat order from the validated active player `issuer`.
    /// APPROXIMATION of UC-COMBAT-022 steps 1-2 (scheduling and route
    /// integration are slice choices): every present player gets retreat intent
    /// (turn finished, deferred, Fleeing or active) and loses a pending skip. All
    /// of them already on a map edge then leave at once in ascending id order,
    /// with no movement cost and no reactions, and without advancing between
    /// exits. The outcome is checked once; if the battle continues, an issuer
    /// still present keeps its turn without a refresh, otherwise the turn
    /// advances exactly once. Consumes no RNG. Defense bonuses are not modeled.
    fn retreat(&mut self, issuer: u32, ev: &mut Vec<Event>) {
        for u in &mut self.units {
            if u.is_present() && u.side == Side::Player {
                u.retreating = true;
                u.skip_turn = false;
            }
        }
        ev.push(Event::RetreatOrdered { unit: issuer });
        let on_edge: Vec<u32> = self
            .units
            .iter()
            .filter(|u| u.is_present() && u.retreating && is_edge(u.pos))
            .map(|u| u.id)
            .collect();
        for unit in on_edge {
            self.leave_battle(unit, ev);
        }
        self.check_outcome(ev);
        if self.outcome.is_none() && !self.units[issuer as usize].is_present() {
            self.advance_turn(ev);
        }
    }

    /// Height of `at` as a step-cost input; off-map hexes count as 0 (never on a route).
    fn height_at(&self, at: Hex) -> u32 {
        u32::from(self.elevations.get(at).unwrap_or(0))
    }

    /// Ground kind of `at` as a step-cost input; off-map hexes count as Ground (never on a route).
    fn kind_at(&self, at: Hex) -> TerrainKind {
        self.ground.get(at).unwrap_or(TerrainKind::Ground)
    }

    /// AP of one step between adjacent hexes: the destination terrain's base
    /// (`terrain_move_ap`) plus `max(to - from, 0)`. Level and downhill steps add
    /// nothing; the starting terrain adds no departure charge. APPROXIMATION:
    /// uphill-only surcharge of one AP per level, from UC-COMBAT-005 and
    /// UC-COMBAT-010 (climbing costs AP), composed additively with the terrain base;
    /// not confirmed against the original runtime. The single place that does height
    /// arithmetic.
    pub(crate) fn step_ap_cost(&self, from: Hex, to: Hex) -> u32 {
        terrain_move_ap(self.kind_at(to)) + self.height_at(to).saturating_sub(self.height_at(from))
    }

    /// `(AP, fatigue)` of `u` taking one step `from` -> `to`: the single place that
    /// combines destination terrain, uphill surcharge and the unit's fatigue
    /// capability. Pure: no state, RNG or events.
    pub(crate) fn step_costs(&self, u: &Unit, from: Hex, to: Hex) -> (u32, u32) {
        (
            self.step_ap_cost(from, to),
            terrain_move_fatigue(u, self.kind_at(to)),
        )
    }

    /// Total `(AP, fatigue)` of `u` walking `path` from its current position
    /// (excludes the start, includes the destination). Saturating sums: a route
    /// is at most a few dozen steps, so saturation is unreachable in practice.
    pub(crate) fn route_costs(&self, u: &Unit, path: &[Hex]) -> (u32, u32) {
        let mut from = u.pos;
        path.iter().fold((0u32, 0u32), |(ap, fatigue), &to| {
            let (step_ap, step_fatigue) = self.step_costs(u, from, to);
            from = to;
            (
                ap.saturating_add(step_ap),
                fatigue.saturating_add(step_fatigue),
            )
        })
    }

    /// Number of leading steps of `path` affordable in sequence by the acting
    /// unit `u` from its current position, AP and fatigue, using `step_costs` and
    /// checked fatigue sums.
    pub(crate) fn affordable_prefix(&self, u: &Unit, path: &[Hex]) -> usize {
        let (mut from, mut ap_left, mut fat) = (u.pos, u.ap, u.fatigue);
        let fatigue_max = u.stats.fatigue_max;
        for (i, &to) in path.iter().enumerate() {
            let (cost, step_fatigue) = self.step_costs(u, from, to);
            match fat.checked_add(step_fatigue) {
                Some(f) if ap_left >= cost && f <= fatigue_max => {
                    ap_left -= cost;
                    fat = f;
                    from = to;
                }
                _ => return i,
            }
        }
        path.len()
    }

    /// Shared by `apply` and `preview_move` so route, costs and errors cannot
    /// drift. `unit` must already be the validated active unit. The route is the
    /// fewest-steps BFS route (not the cheapest in AP), so a rejection may reflect
    /// that route's terrain and uphill costs.
    fn plan_move(&self, unit: u32, to: Hex) -> Result<(Vec<Hex>, u32, u32), RuleError> {
        let u = &self.units[unit as usize];
        let path = hex::bfs_path(u.pos, to, &self.occupied()).ok_or(RuleError::Unreachable)?;
        let (ap_cost, fatigue_cost) = self.route_costs(u, &path);
        if u.ap < ap_cost {
            return Err(RuleError::NotEnoughAp);
        }
        if u.fatigue
            .checked_add(fatigue_cost)
            .is_none_or(|f| f > u.stats.fatigue_max)
        {
            return Err(RuleError::TooFatigued);
        }
        Ok((path, ap_cost, fatigue_cost))
    }

    /// Shared validated plan for `Command::Rotation` and its read-only preview.
    /// Keeps actor gates, target restrictions, coordinate checks, affordability
    /// and predicted edge consequences in the command's existing precedence.
    fn plan_rotation(&self, unit: u32, target: u32) -> Result<RotationPreview, RuleError> {
        self.ensure_controllable(unit)?;
        if !self.units[unit as usize].is_present() {
            return Err(RuleError::NotActiveUnit);
        }
        let actor = &self.units[unit as usize];
        if !actor.has_rotation {
            return Err(RuleError::MissingRotation);
        }
        let other = self
            .units
            .get(target as usize)
            .filter(|other| other.id != unit && other.is_present() && other.side == actor.side)
            .filter(|other| other.morale != Morale::Fleeing && !other.retreating && !other.stunned)
            .ok_or(RuleError::InvalidTarget)?;
        let (actor_pos, target_pos) = (actor.pos, other.pos);
        if !is_passable_map_hex(actor_pos) || !is_passable_map_hex(target_pos) {
            return Err(RuleError::Unreachable);
        }
        if actor_pos.distance(target_pos) != 1 {
            return Err(RuleError::NotAdjacent);
        }
        if actor.ap < ROTATION_AP {
            return Err(RuleError::NotEnoughAp);
        }
        if actor
            .fatigue
            .checked_add(ROTATION_FATIGUE)
            .is_none_or(|fatigue| fatigue > actor.stats.fatigue_max)
        {
            return Err(RuleError::TooFatigued);
        }

        let mut arrivals = [(unit, target_pos), (target, actor_pos)];
        arrivals.sort_unstable_by_key(|(id, _)| *id);
        let edge_penalties = arrivals
            .into_iter()
            .filter(|(id, at)| edge_penalty_applies(&self.units[*id as usize], *at))
            .collect();

        Ok(RotationPreview {
            unit,
            target,
            unit_to: target_pos,
            target_to: actor_pos,
            ap_cost: ROTATION_AP,
            fatigue_cost: ROTATION_FATIGUE,
            edge_penalties,
        })
    }

    /// Describe the accepted Rotation for the current state without mutating it,
    /// emitting events or consuming RNG. The result is a snapshot; submitting a
    /// later command revalidates it against that command's then-current state.
    pub fn preview_rotation(&self, unit: u32, target: u32) -> Result<RotationPreview, RuleError> {
        self.plan_rotation(unit, target)
    }

    /// Accepted Rotation target ids for the current snapshot, in ascending id
    /// order. This is recomputed on every call and returns an empty vector if the
    /// actor cannot currently rotate.
    pub fn legal_rotation_targets(&self, unit: u32) -> Vec<u32> {
        (0..self.units.len())
            .map(|index| index as u32)
            .filter(|&target| self.preview_rotation(unit, target).is_ok())
            .collect()
    }

    /// Shared validated plan for `Command::Footwork` and its read-only preview.
    /// Actor gates keep the Rotation precedence (`ensure_controllable`, presence,
    /// capability); coordinates are checked with the overflow-safe passability
    /// helper before any distance arithmetic, then adjacency, AP and fatigue.
    fn plan_footwork(&self, unit: u32, to: Hex) -> Result<FootworkPreview, RuleError> {
        self.ensure_controllable(unit)?;
        let actor = &self.units[unit as usize];
        if !actor.is_present() {
            return Err(RuleError::NotActiveUnit);
        }
        if !actor.has_footwork {
            return Err(RuleError::MissingFootwork);
        }
        let from = actor.pos;
        if !is_passable_map_hex(from)
            || !is_passable_map_hex(to)
            || to == from
            || self.units.iter().any(|u| u.is_present() && u.pos == to)
        {
            return Err(RuleError::Unreachable);
        }
        if from.distance(to) != 1 {
            return Err(RuleError::NotAdjacent);
        }
        if actor.ap < FOOTWORK_AP {
            return Err(RuleError::NotEnoughAp);
        }
        if actor
            .fatigue
            .checked_add(FOOTWORK_FATIGUE)
            .is_none_or(|fatigue| fatigue > actor.stats.fatigue_max)
        {
            return Err(RuleError::TooFatigued);
        }
        Ok(FootworkPreview {
            unit,
            from,
            to,
            ap_cost: FOOTWORK_AP,
            fatigue_cost: FOOTWORK_FATIGUE,
            edge_penalty: edge_penalty_applies(actor, to).then_some(to),
        })
    }

    /// Describe the accepted Footwork for the current state without mutating it,
    /// emitting events or consuming RNG. The result is a snapshot; submitting a
    /// later command revalidates it against that command's then-current state.
    pub fn preview_footwork(&self, unit: u32, to: Hex) -> Result<FootworkPreview, RuleError> {
        self.plan_footwork(unit, to)
    }

    /// Accepted Footwork destinations for the current snapshot, in the existing
    /// neighbour order. Recomputed on every call; empty if the actor cannot use
    /// Footwork now (including an actor at invalid coordinates). Never panics.
    pub fn legal_footwork_destinations(&self, unit: u32) -> Vec<Hex> {
        let Some(actor) = self.units.get(unit as usize) else {
            return vec![];
        };
        if !is_passable_map_hex(actor.pos) {
            return vec![];
        }
        actor
            .pos
            .neighbors()
            .into_iter()
            .filter(|&to| self.preview_footwork(unit, to).is_ok())
            .collect()
    }

    /// Shared validated plan for `Command::Attack` and its read-only query.
    /// Validation order matches ordinary attack execution: actor/control gates,
    /// target validity, reach (`NotAdjacent` means out of `Unit::melee_reaches`),
    /// AP, then checked fatigue affordability.
    fn plan_attack(&self, attacker: u32, target: u32) -> Result<AttackPlan, RuleError> {
        self.plan_melee(attacker, target, false)
    }

    /// Shared validated plan for `Command::StunningBlow` and its read-only queries.
    /// Order: running battle, active controllable present attacker, capability,
    /// valid hostile present target, distance exactly one (`MeleeReach::Adjacent`
    /// even for a TwoHex attacker), AP, then checked fatigue affordability.
    fn plan_stunning_blow(&self, attacker: u32, target: u32) -> Result<AttackPlan, RuleError> {
        self.plan_melee(attacker, target, true)
    }

    /// One validation path for the paid melee actions; `skill` selects Stunning Blow's
    /// presence and capability gates, adjacent-only reach and costs.
    fn plan_melee(&self, attacker: u32, target: u32, skill: bool) -> Result<AttackPlan, RuleError> {
        self.ensure_controllable(attacker)?;
        let actor = &self.units[attacker as usize];
        if skill {
            if !actor.is_present() {
                return Err(RuleError::NotActiveUnit);
            }
            if !actor.has_stunning_blow {
                return Err(RuleError::MissingStunningBlow);
            }
        }
        let defender = self
            .units
            .get(target as usize)
            .filter(|unit| unit.is_present() && unit.side != actor.side)
            .ok_or(RuleError::InvalidTarget)?;
        let in_reach = if skill {
            MeleeReach::Adjacent.reaches(actor.pos, defender.pos)
        } else {
            actor.melee_reaches(defender.pos)
        };
        if !in_reach {
            return Err(RuleError::NotAdjacent);
        }
        let (ap_cost, fatigue_cost) = if skill {
            (STUNNING_BLOW_AP, STUNNING_BLOW_FATIGUE)
        } else {
            (ATTACK_AP, ATTACK_FATIGUE)
        };
        if actor.ap < ap_cost {
            return Err(RuleError::NotEnoughAp);
        }
        if actor
            .fatigue
            .checked_add(fatigue_cost)
            .is_none_or(|fatigue| fatigue > actor.stats.fatigue_max)
        {
            return Err(RuleError::TooFatigued);
        }

        Ok(AttackPlan {
            preview: AttackPreview {
                attacker,
                target,
                ap_cost,
                fatigue_cost,
                // Capture chance and damage inputs before costs, RNG, incoming
                // fatigue or any attack-triggered passive/morale changes.
                hit_chance: self.hit_chance(attacker, target),
                damage_min: melee_damage(actor.stats.dmg_min, actor, defender),
                damage_max: melee_damage(actor.stats.dmg_max, actor, defender),
            },
        })
    }

    /// Describe an ordinary melee attack legal in the current snapshot.
    /// This **APPROXIMATION** is read-only and consumes no RNG. The inclusive
    /// damage range is conditional on a hit and is the value supplied to armour
    /// soak, before armour processing. This snapshot does not reserve legality
    /// or resources; submitting `Attack` revalidates against the then-current state.
    pub fn preview_attack(&self, attacker: u32, target: u32) -> Result<AttackPreview, RuleError> {
        self.plan_attack(attacker, target).map(|plan| plan.preview)
    }

    /// **APPROXIMATION:** describe a `Command::StunningBlow` legal in the current
    /// snapshot: the ordinary damage range and hit chance with the skill's own costs.
    /// Read-only, no RNG, and returns exactly the errors `apply` would. The stun is
    /// conditional on a hit against a survivor and is not part of the preview.
    pub fn preview_stunning_blow(&self, attacker: u32, target: u32) -> Result<AttackPreview, RuleError> {
        self.plan_stunning_blow(attacker, target).map(|plan| plan.preview)
    }

    /// Targets accepted by `preview_stunning_blow` now, in ascending id order. Empty
    /// when the actor, capability or resources do not allow the skill. Read-only.
    pub fn legal_stunning_blow_targets(&self, attacker: u32) -> Vec<u32> {
        self.units
            .iter()
            .map(|t| t.id)
            .filter(|&t| self.plan_stunning_blow(attacker, t).is_ok())
            .collect()
    }

    /// What `Command::Move { unit, to }` would plan, without executing it.
    /// Read-only, consumes no RNG, and returns exactly the errors `apply` would.
    /// `first_threat` is the first departure (the start, then every path hex
    /// except the destination) where `enemy_controllers_at` is non-empty;
    /// entering enemy adjacency only at the destination is not a threat.
    pub fn preview_move(&self, unit: u32, to: Hex) -> Result<MovePreview, RuleError> {
        self.ensure_controllable(unit)?;
        let (path, ap_cost, fatigue_cost) = self.plan_move(unit, to)?;
        let start = self.units[unit as usize].pos;
        let first_threat = std::iter::once(start)
            .chain(path.iter().copied().take(path.len() - 1))
            .find_map(|departure| {
                let controllers = self.enemy_controllers_at(unit, departure);
                (!controllers.is_empty()).then_some(MoveThreat {
                    departure,
                    controllers,
                })
            });
        let mover = &self.units[unit as usize];
        // `plan_move` has already established that both planned costs are
        // affordable; these values describe that full route before reactions.
        let ap_remaining = mover.ap - ap_cost;
        let attack_budget_available = ap_remaining >= ATTACK_AP
            && mover
                .fatigue
                .checked_add(fatigue_cost)
                .and_then(|fatigue| fatigue.checked_add(ATTACK_FATIGUE))
                .is_some_and(|fatigue| fatigue <= mover.stats.fatigue_max);
        let first_edge_penalty = path
            .iter()
            .copied()
            .find(|&h| edge_penalty_applies(mover, h));
        Ok(MovePreview {
            path,
            ap_cost,
            fatigue_cost,
            ap_remaining,
            attack_budget_available,
            first_threat,
            first_edge_penalty,
        })
    }

    pub fn apply(&mut self, cmd: Command) -> Result<Vec<Event>, RuleError> {
        if let Command::Attack { attacker, target } = cmd {
            let plan = self.plan_attack(attacker, target)?.preview;
            let mut events = Vec::new();
            self.resolve_attack(
                attacker,
                target,
                Some((plan.ap_cost, plan.fatigue_cost)),
                plan.hit_chance,
                false,
                &mut events,
            );
            self.berserk_refund(attacker, target, &mut events);
            self.killing_frenzy_refresh(attacker, target);
            self.check_outcome(&mut events);
            return Ok(events);
        }

        if let Command::StunningBlow { attacker, target } = cmd {
            let plan = self.plan_stunning_blow(attacker, target)?.preview;
            let mut events = Vec::new();
            self.resolve_attack(
                attacker,
                target,
                Some((plan.ap_cost, plan.fatigue_cost)),
                plan.hit_chance,
                true,
                &mut events,
            );
            self.berserk_refund(attacker, target, &mut events);
            self.killing_frenzy_refresh(attacker, target);
            self.check_outcome(&mut events);
            return Ok(events);
        }

        if let Command::Footwork { unit, to } = cmd {
            let plan = self.plan_footwork(unit, to)?;
            let mut events = vec![Event::FootworkUsed {
                unit: plan.unit,
                from: plan.from,
                to: plan.to,
            }];
            let actor = &mut self.units[plan.unit as usize];
            actor.ap -= plan.ap_cost;
            actor.fatigue += plan.fatigue_cost;
            actor.pos = plan.to;
            // The plan already selected this consequence from the pre-step snapshot.
            if let Some(at) = plan.edge_penalty {
                let before = actor.morale;
                actor.morale = Morale::Breaking;
                events.push(Event::EdgeMoraleChanged {
                    unit: plan.unit,
                    at,
                    before,
                    after: Morale::Breaking,
                });
            }
            return Ok(events);
        }

        if let Command::Rotation { unit, target } = cmd {
            let plan = self.plan_rotation(unit, target)?;
            let mut events = vec![Event::Rotated {
                unit: plan.unit,
                target: plan.target,
            }];
            {
                let actor = &mut self.units[plan.unit as usize];
                actor.ap -= plan.ap_cost;
                actor.fatigue += plan.fatigue_cost;
                actor.pos = plan.unit_to;
            }
            self.units[plan.target as usize].pos = plan.target_to;
            // The plan already selected these consequences from the pre-swap
            // snapshot, in event order; execution applies that exact selection.
            for &(id, at) in &plan.edge_penalties {
                let actor = &mut self.units[id as usize];
                let before = actor.morale;
                actor.morale = Morale::Breaking;
                events.push(Event::EdgeMoraleChanged {
                    unit: id,
                    at,
                    before,
                    after: Morale::Breaking,
                });
            }
            return Ok(events);
        }

        let actor = match cmd {
            Command::Move { unit, .. }
            | Command::Recover { unit }
            | Command::Wait { unit }
            | Command::EndTurn { unit }
            | Command::EndRound { unit }
            | Command::Flee { unit }
            | Command::Retreat { unit }
            | Command::Withdraw { unit }
            | Command::Adrenaline { unit }
            | Command::Rotation { unit, .. }
            | Command::Footwork { unit, .. }
            | Command::Indomitable { unit } => unit,
            Command::Attack { attacker, .. } | Command::StunningBlow { attacker, .. } => attacker,
        };
        self.ensure_active(actor)?;
        // Eligibility: Flee needs a Fleeing actor and Withdraw a retreating one;
        // everything else must have neither retreat intent nor Fleeing morale.
        match cmd {
            Command::Flee { .. } if self.units[actor as usize].morale != Morale::Fleeing => {
                return Err(RuleError::NotFleeing);
            }
            Command::Withdraw { .. } if !self.units[actor as usize].retreating => {
                return Err(RuleError::NotRetreating);
            }
            Command::Flee { .. } | Command::Withdraw { .. } => {}
            _ => self.ensure_controllable(actor)?,
        }
        if matches!(cmd, Command::Recover { .. }) {
            self.recover_availability(actor)?;
        }
        if matches!(
            cmd,
            Command::Retreat { .. } | Command::Adrenaline { .. } | Command::Indomitable { .. }
        ) && !self.units[actor as usize].is_present()
        {
            return Err(RuleError::NotActiveUnit);
        }
        if matches!(cmd, Command::EndRound { .. } | Command::Retreat { .. })
            && self.units[actor as usize].side != Side::Player
        {
            return Err(RuleError::NotPlayerUnit);
        }
        if matches!(cmd, Command::Adrenaline { .. }) {
            adrenaline_availability(&self.units[actor as usize])?;
        }
        if matches!(cmd, Command::Indomitable { .. }) {
            indomitable_availability(&self.units[actor as usize])?;
        }
        let active = actor;
        let mut ev = Vec::new();
        match cmd {
            // APPROXIMATION of UC-COMBAT-066: no turn advancement, no RNG.
            Command::Adrenaline { .. } => {
                let u = &mut self.units[active as usize];
                u.ap -= ADRENALINE_AP;
                u.fatigue += ADRENALINE_FATIGUE;
                u.adrenaline_pending = true;
                ev.push(Event::AdrenalineUsed { unit: active });
            }
            // APPROXIMATION of UC-COMBAT-016: no turn advancement, no RNG.
            Command::Indomitable { .. } => {
                let u = &mut self.units[active as usize];
                u.ap -= INDOMITABLE_AP;
                u.fatigue += INDOMITABLE_FATIGUE;
                u.indomitable_active = true;
                ev.push(Event::IndomitableUsed { unit: active });
            }
            // Fatigue halving is RECONSTRUCTED from static evidence. Cost and
            // whole-turn scheduling are catalog-stated in UC-COMBAT-065, but
            // have no original-runtime confirmation. Rounding, surplus-AP
            // forfeiture, zero-fatigue acceptance and no perk gate remain
            // APPROXIMATION choices.
            Command::Recover { .. } => {
                let unit = &mut self.units[active as usize];
                unit.fatigue /= 2;
                unit.ap = 0;
                self.queue.remove(0);
                self.advance_turn(&mut ev);
            }
            Command::Move { to, .. } => {
                let (path, _, _) = self.plan_move(active, to)?;
                self.walk(active, path, true, &mut ev);
            }
            Command::Attack { .. } | Command::StunningBlow { .. } => {
                unreachable!("paid melee attacks use their shared validated plan")
            }
            Command::Wait { .. } => {
                if self.units[active as usize].waited {
                    return Err(RuleError::AlreadyWaited);
                }
                self.units[active as usize].waited = true;
                self.queue.remove(0);
                self.queue.push(active);
                self.advance_turn(&mut ev);
            }
            Command::EndTurn { .. } => {
                self.queue.remove(0);
                self.advance_turn(&mut ev);
            }
            Command::Flee { .. } | Command::Withdraw { .. } => self.automatic_step(active, &mut ev),
            Command::Retreat { .. } => self.retreat(active, &mut ev),
            // APPROXIMATION based on UC-COMBAT-017 (catalog text, no runtime
            // confirmation): the confirmation dialog belongs to the UI layer.
            Command::EndRound { .. } => {
                for &id in &self.queue {
                    let u = &mut self.units[id as usize];
                    if u.is_present()
                        && u.side == Side::Player
                        && u.morale != Morale::Fleeing
                        && !u.retreating
                    {
                        u.skip_turn = true;
                    }
                }
                self.units[active as usize].ap = 0;
                self.queue.remove(0);
                self.advance_turn(&mut ev);
            }
            Command::Rotation { .. } | Command::Footwork { .. } => {
                unreachable!("Rotation and Footwork are handled by their shared plans above")
            }
        }
        Ok(ev)
    }

    /// Hit chance in percent, including morale-adjusted melee attributes (and the
    /// target's optional Dodge defense bonus, via `effective_defense`), the
    /// basic melee surround bonus, the melee elevation modifier and the optional
    /// Fast Adaptation bonus.
    /// APPROXIMATION: clamp(effective_melee_skill - effective_defense + 5
    /// points per additional living adjacent ally + elevation + fast adaptation,
    /// 5, 95), where fast adaptation is `stacks * FAST_ADAPTATION_BONUS_PER_STACK`
    /// for a capable attacker (else 0; UC-COMBAT-067, see that constant) and
    /// elevation (UC-COMBAT-010, catalog-stated) is +10 when the attacker's hex
    /// is higher than the target's, -10 per level when lower, else 0. All three bonuses
    /// apply only to a present, hostile, adjacent pair. The catalog
    /// exceptions are not modeled. "Adjacent pair" means within the attacker's
    /// `melee_reaches`; surround still counts present same-side units at distance
    /// 1 from the defender, so a two-hex attacker is not itself counted
    /// (APPROXIMATION: composition of reach with the existing modifiers). `i128` keeps the arithmetic in signed range.
    pub fn hit_chance(&self, attacker: u32, target: u32) -> u32 {
        let attacker_unit = &self.units[attacker as usize];
        let target_unit = &self.units[target as usize];
        let a = i128::from(attacker_unit.effective_melee_skill());
        let d = i128::from(target_unit.effective_defense());
        let engaged = attacker_unit.is_present()
            && target_unit.is_present()
            && attacker_unit.side != target_unit.side
            && attacker_unit.melee_reaches(target_unit.pos);
        let (surround_bonus, elevation_modifier, adaptation_bonus) = if engaged {
            let adjacent_allies = self
                .units
                .iter()
                .filter(|unit| {
                    unit.is_present()
                        && unit.side == attacker_unit.side
                        && unit.pos.distance(target_unit.pos) == 1
                })
                .count();
            let surround = i128::try_from(adjacent_allies.saturating_sub(1))
                .unwrap_or(i128::MAX)
                .saturating_mul(i128::from(SURROUND_BONUS_PER_ADDITIONAL_ALLY));
            let height = |u: &Unit| i128::from(self.elevations.get(u.pos).unwrap_or(0));
            let (ah, th) = (height(attacker_unit), height(target_unit));
            let elevation = if ah > th {
                ELEVATION_DOWNHILL_BONUS
            } else {
                (ah - th) * ELEVATION_UPHILL_PENALTY_PER_LEVEL
            };
            let adaptation = if attacker_unit.has_fast_adaptation {
                i128::from(attacker_unit.fast_adaptation_stacks) * FAST_ADAPTATION_BONUS_PER_STACK
            } else {
                0
            };
            (surround, elevation, adaptation)
        } else {
            (0, 0, 0)
        };
        (a - d)
            .saturating_add(surround_bonus)
            .saturating_add(elevation_modifier)
            .saturating_add(adaptation_bonus)
            .clamp(5, 95) as u32
    }

    /// Hexes the unit can reach with its current AP and fatigue, with AP cost.
    /// Candidates keep the BFS order; each is judged on its own BFS route, with
    /// the same AP and fatigue totals (terrain and uphill costs included) as `plan_move`,
    /// and lists that route's AP cost. A pure geometric and budget preview: it
    /// ignores zones of control, so an opportunity attack can still stop a move
    /// short of any listed hex.
    pub fn reachable(&self, unit: u32) -> Vec<(Hex, u32)> {
        let u = &self.units[unit as usize];
        let occupied = self.occupied();
        hex::bfs_distances(u.pos, &occupied)
            .into_iter()
            .filter(|&(_, d)| d >= 1)
            .filter_map(|(h, _)| {
                let path = hex::bfs_path(u.pos, h, &occupied)?;
                let (ap_cost, fatigue_cost) = self.route_costs(u, &path);
                let fatigue_ok = u
                    .fatigue
                    .checked_add(fatigue_cost)
                    .is_some_and(|f| f <= u.stats.fatigue_max);
                (ap_cost <= u.ap && fatigue_ok).then_some((h, ap_cost))
            })
            .collect()
    }

    /// Present enemies within the unit's `melee_reaches`, in id order. Geometry
    /// and hostility only; the AP and fatigue gates stay with `preview_attack`.
    pub fn legal_targets(&self, unit: u32) -> Vec<u32> {
        let u = &self.units[unit as usize];
        self.units
            .iter()
            .filter(|t| t.is_present() && t.side != u.side && u.melee_reaches(t.pos))
            .map(|t| t.id)
            .collect()
    }

    /// FNV-1a 64 over an explicit, fixed serialization. Each unit appends its
    /// base Resolve after the flag word. Flag bits: alive 0, waited 1,
    /// turn_started 2, skip_turn 3, morale code at bits 4..=6 (Fleeing = 4, bit
    /// 6), fled bit 7, retreating bit 8, has_adrenaline bit 9,
    /// adrenaline_pending bit 10, has_rotation bit 11, has_footwork bit 12,
    /// has_fast_adaptation bit 13, fast_adaptation_stacks bits 14..=45, has_dodge bit 46, has_nine_lives bit 47, nine_lives_used bit 48, has_berserk bit 49, berserk_used bit 50, has_pathfinder bit 51, has_indomitable bit 52, indomitable_active bit 53, TwoHex melee reach bit 54 (Adjacent contributes zero), stunned bit 55, has_stunning_blow bit 56, has_killing_frenzy bit 57. Outcome and end reason gained Retreat/Escape = 3. States
    /// without Fleeing, fled units or the new terminal values hash as before.
    /// A map with any nonzero elevation appends `ELEVATION_HASH_TAG`, the entry
    /// count and per entry `q`, `r`, height, sorted by (`q`, `r`); a flat map
    /// appends nothing, so flat-map hashes are unchanged. After it, a map with any
    /// non-Ground hex appends `TERRAIN_HASH_TAG`, the entry count and per entry `q`,
    /// `r`, kind code (Swamp = 1, Snow = 2), sorted by (`q`, `r`); an all-Ground map appends
    /// nothing. Last, if any unit has a nonzero `killing_frenzy_turns`, the hash appends
    /// `KILLING_FRENZY_HASH_TAG`, the entry count and per entry the unit id and counter
    /// (ascending unit order); with all counters zero nothing is appended.
    pub fn state_hash(&self) -> u64 {
        let mut h: u64 = 0xcbf2_9ce4_8422_2325;
        let mut put = |v: u64| {
            for b in v.to_le_bytes() {
                h ^= u64::from(b);
                h = h.wrapping_mul(0x0000_0100_0000_01b3);
            }
        };
        put(self.rng.state);
        put(u64::from(self.round));
        put(self.queue.len() as u64);
        for &q in &self.queue {
            put(u64::from(q));
        }
        put(match self.outcome {
            None => 0,
            Some(Outcome::Victory) => 1,
            Some(Outcome::Defeat) => 2,
            Some(Outcome::Retreat) => 3,
        });
        put(match self.end_reason {
            None => 0,
            Some(EndReason::Elimination) => 1,
            Some(EndReason::TimeLimit) => 2,
            Some(EndReason::Escape) => 3,
        });
        for u in &self.units {
            put(u64::from(u.id));
            put(u64::from(u.hp));
            put(u64::from(u.armour));
            put(u64::from(u.ap));
            put(u64::from(u.fatigue));
            put(u.pos.q as i64 as u64);
            put(u.pos.r as i64 as u64);
            put(u64::from(u.alive)
                | u64::from(u.waited) << 1
                | u64::from(u.turn_started) << 2
                | u64::from(u.skip_turn) << 3
                | u.morale.state_hash_code() << 4
                | u64::from(u.fled) << 7
                | u64::from(u.retreating) << 8
                | u64::from(u.has_adrenaline) << 9
                | u64::from(u.adrenaline_pending) << 10
                | u64::from(u.has_rotation) << 11
                | u64::from(u.has_footwork) << 12
                | u64::from(u.has_fast_adaptation) << 13
                | u64::from(u.fast_adaptation_stacks) << 14
                | u64::from(u.has_dodge) << 46
                | u64::from(u.has_nine_lives) << 47
                | u64::from(u.nine_lives_used) << 48
                | u64::from(u.has_berserk) << 49
                | u64::from(u.berserk_used) << 50
                | u64::from(u.has_pathfinder) << 51
                | u64::from(u.has_indomitable) << 52
                | u64::from(u.indomitable_active) << 53
                | u64::from(u.melee_reach == MeleeReach::TwoHex) << 54
                | u64::from(u.stunned) << 55
                | u64::from(u.has_stunning_blow) << 56
                | u64::from(u.has_killing_frenzy) << 57);
            put(u64::from(u.stats.resolve));
        }
        if !self.elevations.is_flat() {
            put(ELEVATION_HASH_TAG);
            put(self.elevations.entries().count() as u64);
            for (at, height) in self.elevations.entries() {
                put(at.q as i64 as u64);
                put(at.r as i64 as u64);
                put(u64::from(height));
            }
        }
        if !self.ground.is_all_ground() {
            put(TERRAIN_HASH_TAG);
            put(self.ground.entries().count() as u64);
            for (at, kind) in self.ground.entries() {
                put(at.q as i64 as u64);
                put(at.r as i64 as u64);
                put(kind.hash_code());
            }
        }
        let frenzy = self.units.iter().filter(|u| u.killing_frenzy_turns > 0);
        let entries = frenzy.clone().count();
        if entries > 0 {
            put(KILLING_FRENZY_HASH_TAG);
            put(entries as u64);
            for u in frenzy {
                put(u64::from(u.id));
                put(u64::from(u.killing_frenzy_turns));
            }
        }
        h
    }
}
