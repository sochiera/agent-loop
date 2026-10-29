//! Per-hex elevation and ground kind.
//!
//! Elevation. APPROXIMATION: the height range 0–3 and the melee hit
//! modifiers come from the catalog text of UC-COMBAT-010 (catalog-stated, not
//! runtime-confirmed); the storage, errors and hash encoding are slice design.
//! Height belongs to map hexes, not units, and does not affect passability.
//!
//! Ground kind (`TerrainKind`, `TerrainMap`). The 4-AP swamp step is catalog-stated
//! in UC-COMBAT-005 ("Murky swamp water 4 AP"), and typical snow is catalog-stated
//! as 3 AP; neither is runtime-confirmed. The catalog's light-snow 2-AP exception
//! is not implemented. Fatigue, destination charging, Pathfinder composition,
//! composition with the elevation surcharge and storage/hash design are
//! APPROXIMATION choices. Ground kind belongs to map hexes and does not affect passability
//! (obstacles may carry a kind but stay impassable). Snow is configurable through
//! `TerrainMap::set` only; it has no preset, launch option or native marking.

use std::collections::BTreeMap;

use crate::hex::{Hex, RADIUS};

/// Highest valid elevation level.
pub const MAX_ELEVATION: u8 = 3;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ElevationError {
    /// The hex is outside the map.
    OffMap,
    /// The height is above `MAX_ELEVATION`.
    InvalidHeight,
}

/// Overflow-safe map membership: `Hex::in_map` negates and subtracts `i32`
/// coordinates, which can overflow for extreme values, so widen first.
fn on_map(at: Hex) -> bool {
    let (q, r) = (i64::from(at.q), i64::from(at.r));
    let radius = i64::from(RADIUS);
    q.abs().max(r.abs()).max((q + r).abs()) <= radius
}

/// Heights of map hexes. `Default` is height 0 everywhere. Only nonzero heights
/// are stored, in axial order (`q`, then `r`), so equal maps compare and hash
/// identically whatever order they were configured in.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ElevationMap {
    heights: BTreeMap<Hex, u8>,
}

impl ElevationMap {
    /// Height of an in-map hex; `None` off the map.
    pub fn get(&self, at: Hex) -> Option<u8> {
        on_map(at).then(|| self.heights.get(&at).copied().unwrap_or(0))
    }

    /// Sets the height of an in-map hex (obstacles included). Rejected input
    /// leaves the map untouched; the off-map check comes first. Height 0 removes
    /// the entry so the map keeps its canonical representation.
    pub fn set(&mut self, at: Hex, height: u8) -> Result<(), ElevationError> {
        if !on_map(at) {
            return Err(ElevationError::OffMap);
        }
        if height > MAX_ELEVATION {
            return Err(ElevationError::InvalidHeight);
        }
        if height == 0 {
            self.heights.remove(&at);
        } else {
            self.heights.insert(at, height);
        }
        Ok(())
    }

    /// True while every hex is at height 0.
    pub fn is_flat(&self) -> bool {
        self.heights.is_empty()
    }

    /// Nonzero entries sorted by axial coordinates (`q`, then `r`).
    pub fn entries(&self) -> impl Iterator<Item = (Hex, u8)> + '_ {
        self.heights.iter().map(|(&h, &v)| (h, v))
    }
}

/// Kind of ground on a map hex. `Ground` is the default.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum TerrainKind {
    Ground,
    Swamp,
    Snow,
}

impl TerrainKind {
    /// Explicit `state_hash` code; `Ground` (0) is never hashed.
    pub(crate) const fn hash_code(self) -> u64 {
        match self {
            TerrainKind::Ground => 0,
            TerrainKind::Swamp => 1,
            TerrainKind::Snow => 2,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TerrainError {
    /// The hex is outside the map.
    OffMap,
}

/// Ground kinds of map hexes. `Default` is `Ground` everywhere. Only non-`Ground`
/// entries are stored, in axial order (`q`, then `r`), so equal maps compare and
/// hash identically whatever order they were configured in.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct TerrainMap {
    kinds: BTreeMap<Hex, TerrainKind>,
}

impl TerrainMap {
    /// Kind of an in-map hex; `None` off the map.
    pub fn get(&self, at: Hex) -> Option<TerrainKind> {
        on_map(at).then(|| self.kinds.get(&at).copied().unwrap_or(TerrainKind::Ground))
    }

    /// Sets the kind of an in-map hex (obstacles included). Off-map input is
    /// rejected without mutation. Assigning `Ground` removes the entry so the map
    /// keeps its canonical representation.
    pub fn set(&mut self, at: Hex, kind: TerrainKind) -> Result<(), TerrainError> {
        if !on_map(at) {
            return Err(TerrainError::OffMap);
        }
        match kind {
            TerrainKind::Ground => {
                self.kinds.remove(&at);
            }
            TerrainKind::Swamp | TerrainKind::Snow => {
                self.kinds.insert(at, kind);
            }
        }
        Ok(())
    }

    /// True while every hex is `Ground`.
    pub fn is_all_ground(&self) -> bool {
        self.kinds.is_empty()
    }

    /// Non-`Ground` entries sorted by axial coordinates (`q`, then `r`).
    pub fn entries(&self) -> impl Iterator<Item = (Hex, TerrainKind)> + '_ {
        self.kinds.iter().map(|(&h, &k)| (h, k))
    }
}
