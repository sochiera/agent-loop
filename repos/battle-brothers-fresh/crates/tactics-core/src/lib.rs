//! Deterministic hex-grid tactics rules for Hireling's Oath.
//!
//! This crate has no graphics, windowing or I/O dependencies. All state changes go
//! through [`Battle::apply`], and all randomness comes from the seeded [`rng::Rng`].

pub mod ai;
pub mod autopilot;
pub mod battle;
pub mod campaign;
pub mod hex;
pub mod map;
pub mod mapgen;
pub mod rng;
pub mod scenario;
pub mod unit;

pub use battle::{
    ActionError, AttackPreview, Battle, Command, Event, HitBreakdown, Outcome, UnitOutcome, AP_PER_TURN, MAX_ROUNDS,
};
pub use hex::Hex;
pub use map::{Map, Terrain};
pub use unit::{Archetype, AttackKind, LevelBonus, Stats, Team, Unit, UnitId, Weapon};
