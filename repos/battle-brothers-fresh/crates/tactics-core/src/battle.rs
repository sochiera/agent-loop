//! Battle state and rules: movement, AP, line of sight, hit chance, damage,
//! turn order and win/loss. Everything here is deterministic for a given seed.

use std::cmp::Reverse;
use std::collections::BinaryHeap;

use crate::hex::Hex;
use crate::map::{Map, Terrain};
use crate::rng::Rng;
use crate::unit::{AttackKind, Team, Unit, UnitId, Weapon};

/// Action points every unit receives at the start of its turn.
pub const AP_PER_TURN: i32 = 9;
/// Safety valve: a battle that is still undecided after this many rounds is
/// called off and counts as a defeat for the company.
pub const MAX_ROUNDS: u32 = 40;
pub const MIN_HIT_CHANCE: i32 = 5;
pub const MAX_HIT_CHANCE: i32 = 95;
/// Hit-chance bonus for attacking from a hill / penalty for attacking uphill.
pub const HILL_ATTACK_BONUS: i32 = 10;
/// Ranged accuracy lost per hex beyond the weapon's minimum range.
pub const RANGE_PENALTY_PER_HEX: i32 = 3;

#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum Outcome {
    Victory,
    Defeat,
}

#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum Command {
    /// Move the active unit along the cheapest valid path.
    Move { to: Hex },
    /// Attack with the weapon appropriate to the distance (melee if adjacent).
    Attack { target: UnitId },
    EndTurn,
}

#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum ActionError {
    BattleOver,
    OutOfBounds,
    Impassable,
    Occupied,
    AlreadyThere,
    NoPath,
    NotEnoughAp { need: i32, have: i32 },
    TargetDead,
    SameTeam,
    OutOfRange,
    NoLineOfSight,
}

impl ActionError {
    pub fn describe(&self) -> String {
        match self {
            ActionError::BattleOver => "The battle is over".into(),
            ActionError::OutOfBounds => "Off the map".into(),
            ActionError::Impassable => "Impassable terrain".into(),
            ActionError::Occupied => "Hex is occupied".into(),
            ActionError::AlreadyThere => "Already there".into(),
            ActionError::NoPath => "No path".into(),
            ActionError::NotEnoughAp { need, have } => format!("Not enough AP ({need} needed, {have} left)"),
            ActionError::TargetDead => "Target is dead".into(),
            ActionError::SameTeam => "Cannot attack an ally".into(),
            ActionError::OutOfRange => "Out of range".into(),
            ActionError::NoLineOfSight => "Target not visible".into(),
        }
    }
}

/// Itemised hit chance, so the UI can explain the number.
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub struct HitBreakdown {
    pub skill: i32,
    pub weapon_mod: i32,
    pub defense: i32,
    pub terrain_defense: i32,
    pub height: i32,
    pub range: i32,
    pub chance: i32,
}

#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub struct AttackPreview {
    pub kind: AttackKind,
    pub weapon: Weapon,
    pub distance: i32,
    pub hit: HitBreakdown,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Event {
    Moved { unit: UnitId, path: Vec<Hex>, cost: i32 },
    Attacked {
        attacker: UnitId,
        target: UnitId,
        kind: AttackKind,
        chance: i32,
        roll: i32,
        hit: bool,
        damage: i32,
        armor_loss: i32,
        hp_loss: i32,
    },
    Died { unit: UnitId },
    TurnStarted { unit: UnitId, round: u32 },
    RoundStarted { round: u32 },
    BattleEnded { outcome: Outcome, timed_out: bool },
}

/// What happened to one persistent mercenary in a battle (reported back to the campaign).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct UnitOutcome {
    pub roster_id: u32,
    pub name: String,
    pub final_hp: i32,
    pub dead: bool,
    pub kills: u32,
}

/// Result of a shortest-path search over the map.
pub struct Reach {
    start: usize,
    dist: Vec<i32>,
    prev: Vec<usize>,
}

const UNSEEN: i32 = i32::MAX;
const NO_PREV: usize = usize::MAX;

impl Reach {
    pub fn cost(&self, map: &Map, h: Hex) -> Option<i32> {
        let i = map.index(h)?;
        (self.dist[i] != UNSEEN).then_some(self.dist[i])
    }

    /// Hexes from the start (included) to `to` (included).
    pub fn path_to(&self, map: &Map, to: Hex) -> Option<Vec<Hex>> {
        let mut i = map.index(to)?;
        if self.dist[i] == UNSEEN {
            return None;
        }
        let mut out = vec![map.hex_at(i)];
        while i != self.start {
            i = self.prev[i];
            out.push(map.hex_at(i));
        }
        out.reverse();
        Some(out)
    }
}

#[derive(Clone, Debug)]
pub struct Battle {
    map: Map,
    units: Vec<Unit>,
    rng: Rng,
    seed: u64,
    round: u32,
    order: Vec<UnitId>,
    cursor: usize,
    outcome: Option<Outcome>,
    timed_out: bool,
}

impl Battle {
    /// `units[i].id` must equal `i`.
    pub fn new(map: Map, units: Vec<Unit>, seed: u64) -> Battle {
        for (i, u) in units.iter().enumerate() {
            assert_eq!(u.id, i, "unit ids must be dense indices");
            assert!(map.terrain(u.pos).is_some_and(|t| t.passable()), "unit {} placed on bad terrain", u.name);
        }
        let mut b = Battle {
            map,
            units,
            rng: Rng::new(seed),
            seed,
            round: 1,
            order: Vec::new(),
            cursor: 0,
            outcome: None,
            timed_out: false,
        };
        b.order = b.build_order();
        b.cursor = 0;
        for u in b.units.iter_mut().filter(|u| u.alive) {
            u.ap = AP_PER_TURN;
        }
        b
    }

    // ----- queries ---------------------------------------------------------

    pub fn map(&self) -> &Map {
        &self.map
    }

    pub fn seed(&self) -> u64 {
        self.seed
    }

    pub fn round(&self) -> u32 {
        self.round
    }

    pub fn outcome(&self) -> Option<Outcome> {
        self.outcome
    }

    pub fn timed_out(&self) -> bool {
        self.timed_out
    }

    pub fn units(&self) -> &[Unit] {
        &self.units
    }

    pub fn unit(&self, id: UnitId) -> &Unit {
        &self.units[id]
    }

    pub fn active_id(&self) -> UnitId {
        self.order[self.cursor]
    }

    pub fn active(&self) -> &Unit {
        &self.units[self.active_id()]
    }

    pub fn living(&self, team: Team) -> impl Iterator<Item = &Unit> {
        self.units.iter().filter(move |u| u.alive && u.team == team)
    }

    pub fn casualties(&self, team: Team) -> impl Iterator<Item = &Unit> {
        self.units.iter().filter(move |u| !u.alive && u.team == team)
    }

    /// Per-unit outcome of every persistent (roster) player unit that took part.
    pub fn player_outcomes(&self) -> Vec<UnitOutcome> {
        self.units
            .iter()
            .filter_map(|u| {
                u.roster_id.map(|roster_id| UnitOutcome {
                    roster_id,
                    name: u.name.clone(),
                    final_hp: u.hp,
                    dead: !u.alive,
                    kills: u.kills,
                })
            })
            .collect()
    }

    /// Living unit standing on `h`.
    pub fn unit_at(&self, h: Hex) -> Option<UnitId> {
        self.units.iter().find(|u| u.alive && u.pos == h).map(|u| u.id)
    }

    /// Units still to act this round (current first), then the next round's order.
    pub fn turn_queue(&self, max: usize) -> Vec<UnitId> {
        let mut q: Vec<UnitId> = self.order[self.cursor..].iter().copied().filter(|&i| self.units[i].alive).collect();
        if self.outcome.is_none() {
            q.extend(self.build_order());
        }
        q.truncate(max);
        q
    }

    /// Number of entries of `turn_queue` that belong to the current round.
    pub fn remaining_this_round(&self) -> usize {
        self.order[self.cursor..].iter().filter(|&&i| self.units[i].alive).count()
    }

    fn build_order(&self) -> Vec<UnitId> {
        let mut ids: Vec<UnitId> = self.units.iter().filter(|u| u.alive).map(|u| u.id).collect();
        ids.sort_by_key(|&i| (Reverse(self.units[i].stats.initiative), i));
        ids
    }

    // ----- pathing ---------------------------------------------------------

    /// Dijkstra from `start`. Impassable terrain and hexes holding living units
    /// (other than `start`) are never entered. Ties resolve deterministically.
    pub fn search(&self, start: Hex, max_cost: i32) -> Reach {
        let n = self.map.len();
        let mut dist = vec![UNSEEN; n];
        let mut prev = vec![NO_PREV; n];
        let s = self.map.index(start).expect("start on map");
        dist[s] = 0;
        let mut heap = BinaryHeap::new();
        heap.push(Reverse((0, s)));
        while let Some(Reverse((d, i))) = heap.pop() {
            if d > dist[i] {
                continue;
            }
            for nb in self.map.hex_at(i).neighbors() {
                let Some(ni) = self.map.index(nb) else { continue };
                let Some(step) = self.map.terrain(nb).and_then(Terrain::move_cost) else { continue };
                if self.unit_at(nb).is_some() {
                    continue;
                }
                let nd = d + step;
                if nd <= max_cost && nd < dist[ni] {
                    dist[ni] = nd;
                    prev[ni] = i;
                    heap.push(Reverse((nd, ni)));
                }
            }
        }
        Reach { start: s, dist, prev }
    }

    /// Every hex the unit can reach with its current AP, with the AP cost (start excluded).
    pub fn reachable(&self, id: UnitId) -> Vec<(Hex, i32)> {
        let u = &self.units[id];
        if !u.alive {
            return Vec::new();
        }
        let reach = self.search(u.pos, u.ap);
        self.map
            .hexes()
            .filter(|&h| h != u.pos)
            .filter_map(|h| reach.cost(&self.map, h).map(|c| (h, c)))
            .collect()
    }

    /// Cheapest valid path for the unit to `to`: hexes (start included) and AP cost.
    pub fn move_path(&self, id: UnitId, to: Hex) -> Result<(Vec<Hex>, i32), ActionError> {
        let u = &self.units[id];
        let Some(t) = self.map.terrain(to) else { return Err(ActionError::OutOfBounds) };
        if to == u.pos {
            return Err(ActionError::AlreadyThere);
        }
        if !t.passable() {
            return Err(ActionError::Impassable);
        }
        if self.unit_at(to).is_some() {
            return Err(ActionError::Occupied);
        }
        let reach = self.search(u.pos, i32::MAX / 2);
        let Some(cost) = reach.cost(&self.map, to) else { return Err(ActionError::NoPath) };
        if cost > u.ap {
            return Err(ActionError::NotEnoughAp { need: cost, have: u.ap });
        }
        Ok((reach.path_to(&self.map, to).expect("cost implies path"), cost))
    }

    /// Terrain-only travel cost from the nearest of `sources`, ignoring units.
    /// Used by the AI to decide which way to advance.
    pub fn distance_map(&self, sources: &[Hex]) -> Vec<i32> {
        let mut dist = vec![UNSEEN; self.map.len()];
        let mut heap = BinaryHeap::new();
        for &s in sources {
            if let Some(i) = self.map.index(s) {
                dist[i] = 0;
                heap.push(Reverse((0, i)));
            }
        }
        while let Some(Reverse((d, i))) = heap.pop() {
            if d > dist[i] {
                continue;
            }
            for nb in self.map.hex_at(i).neighbors() {
                let Some(ni) = self.map.index(nb) else { continue };
                let Some(step) = self.map.terrain(nb).and_then(Terrain::move_cost) else { continue };
                let nd = d + step;
                if nd < dist[ni] {
                    dist[ni] = nd;
                    heap.push(Reverse((nd, ni)));
                }
            }
        }
        dist
    }

    // ----- line of sight & combat numbers ---------------------------------

    /// Whether a ranged attack could pass between the hexes. Only terrain
    /// strictly between them blocks (rock, forest); units never block.
    /// Symmetric: `line_of_sight(a, b) == line_of_sight(b, a)`.
    pub fn line_of_sight(&self, a: Hex, b: Hex) -> bool {
        let (from, to) = if (a.r, a.q) <= (b.r, b.q) { (a, b) } else { (b, a) };
        let line = from.line_to(to);
        if line.len() <= 2 {
            return true;
        }
        line[1..line.len() - 1]
            .iter()
            .all(|&h| !self.map.terrain(h).is_none_or(Terrain::blocks_los))
    }

    pub fn hit_breakdown(&self, attacker: &Unit, from: Hex, target: &Unit, kind: AttackKind, weapon: &Weapon) -> HitBreakdown {
        let skill = attacker.skill(kind);
        let t_from = self.map.terrain(from).unwrap_or(Terrain::Plain);
        let t_to = self.map.terrain(target.pos).unwrap_or(Terrain::Plain);
        let terrain_defense = t_to.defense_bonus();
        let height = if t_from.is_high_ground() {
            HILL_ATTACK_BONUS
        } else if t_to.is_high_ground() {
            -HILL_ATTACK_BONUS
        } else {
            0
        };
        let distance = from.distance(target.pos);
        let range = match kind {
            AttackKind::Melee => 0,
            AttackKind::Ranged => -RANGE_PENALTY_PER_HEX * (distance - weapon.range_min).max(0),
        };
        let raw = skill + weapon.hit_mod - target.stats.defense - terrain_defense + height + range;
        HitBreakdown {
            skill,
            weapon_mod: weapon.hit_mod,
            defense: target.stats.defense,
            terrain_defense,
            height,
            range,
            chance: raw.clamp(MIN_HIT_CHANCE, MAX_HIT_CHANCE),
        }
    }

    /// Can `attacker`, standing on `from` with `ap` action points, attack `target`?
    /// `from`/`ap` are parameters so the AI can ask "what if I moved there?".
    pub fn preview_from(&self, attacker: &Unit, from: Hex, ap: i32, target: &Unit) -> Result<AttackPreview, ActionError> {
        if !target.alive {
            return Err(ActionError::TargetDead);
        }
        if target.team == attacker.team {
            return Err(ActionError::SameTeam);
        }
        let distance = from.distance(target.pos);
        let (kind, weapon) = if distance == 1 {
            (AttackKind::Melee, attacker.archetype.melee_weapon())
        } else if let Some(w) = attacker.archetype.ranged_weapon().filter(|w| (w.range_min..=w.range_max).contains(&distance)) {
            (AttackKind::Ranged, w)
        } else {
            return Err(ActionError::OutOfRange);
        };
        if kind == AttackKind::Ranged && !self.line_of_sight(from, target.pos) {
            return Err(ActionError::NoLineOfSight);
        }
        if ap < weapon.ap_cost {
            return Err(ActionError::NotEnoughAp { need: weapon.ap_cost, have: ap });
        }
        let hit = self.hit_breakdown(attacker, from, target, kind, &weapon);
        Ok(AttackPreview { kind, weapon, distance, hit })
    }

    /// Preview of the active unit (or any unit, using its current AP) attacking `target`.
    pub fn attack_preview(&self, attacker: UnitId, target: UnitId) -> Result<AttackPreview, ActionError> {
        let a = &self.units[attacker];
        self.preview_from(a, a.pos, a.ap, &self.units[target])
    }

    // ----- commands --------------------------------------------------------

    /// Apply a command for the active unit. State only changes on `Ok`.
    pub fn apply(&mut self, cmd: Command) -> Result<Vec<Event>, ActionError> {
        if self.outcome.is_some() {
            return Err(ActionError::BattleOver);
        }
        let id = self.active_id();
        let mut events = Vec::new();
        match cmd {
            Command::Move { to } => {
                let (path, cost) = self.move_path(id, to)?;
                let u = &mut self.units[id];
                u.ap -= cost;
                u.pos = to;
                events.push(Event::Moved { unit: id, path, cost });
            }
            Command::Attack { target } => {
                if target >= self.units.len() {
                    return Err(ActionError::TargetDead);
                }
                let preview = self.attack_preview(id, target)?;
                self.units[id].ap -= preview.weapon.ap_cost;
                self.resolve_attack(id, target, &preview, &mut events);
                self.check_outcome(&mut events);
            }
            Command::EndTurn => {
                self.units[id].ap = 0;
                self.advance_turn(&mut events);
            }
        }
        Ok(events)
    }

    fn resolve_attack(&mut self, attacker: UnitId, target: UnitId, p: &AttackPreview, events: &mut Vec<Event>) {
        let roll = self.rng.below(100) as i32;
        let hit = roll < p.hit.chance;
        let (mut damage, mut armor_loss, mut hp_loss) = (0, 0, 0);
        if hit {
            damage = self.rng.range_inclusive(p.weapon.dmg_min, p.weapon.dmg_max);
            let t = &mut self.units[target];
            let direct = damage * p.weapon.armor_ignore / 100;
            let to_armor = damage - direct;
            armor_loss = to_armor.min(t.armor);
            hp_loss = direct + (to_armor - armor_loss);
            t.armor -= armor_loss;
            t.hp = (t.hp - hp_loss).max(0);
        }
        events.push(Event::Attacked { attacker, target, kind: p.kind, chance: p.hit.chance, roll, hit, damage, armor_loss, hp_loss });
        if self.units[target].hp == 0 {
            self.units[target].alive = false;
            self.units[attacker].kills += 1;
            events.push(Event::Died { unit: target });
        }
    }

    fn check_outcome(&mut self, events: &mut Vec<Event>) {
        if self.outcome.is_some() {
            return;
        }
        let outcome = if self.living(Team::Enemy).next().is_none() {
            Outcome::Victory
        } else if self.living(Team::Player).next().is_none() {
            Outcome::Defeat
        } else {
            return;
        };
        self.outcome = Some(outcome);
        events.push(Event::BattleEnded { outcome, timed_out: false });
    }

    fn advance_turn(&mut self, events: &mut Vec<Event>) {
        loop {
            self.cursor += 1;
            if self.cursor >= self.order.len() {
                if self.round >= MAX_ROUNDS {
                    // Keep `cursor` on the last valid order entry so terminal-state
                    // queries (`active`, `turn_queue`, ...) stay well-defined.
                    self.cursor = self.order.len() - 1;
                    self.outcome = Some(Outcome::Defeat);
                    self.timed_out = true;
                    events.push(Event::BattleEnded { outcome: Outcome::Defeat, timed_out: true });
                    return;
                }
                self.round += 1;
                self.order = self.build_order();
                self.cursor = 0;
                // Units still waiting for their turn show a full AP bar.
                for u in self.units.iter_mut().filter(|u| u.alive) {
                    u.ap = AP_PER_TURN;
                }
                events.push(Event::RoundStarted { round: self.round });
            }
            if self.units[self.order[self.cursor]].alive {
                break;
            }
        }
        let id = self.order[self.cursor];
        self.units[id].ap = AP_PER_TURN;
        events.push(Event::TurnStarted { unit: id, round: self.round });
    }
}
