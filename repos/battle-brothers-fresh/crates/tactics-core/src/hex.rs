//! Hex coordinates. Axial `(q, r)` storage, pointy-top "odd-r" offset conversion
//! for authoring rectangular maps.

#[derive(Copy, Clone, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct Hex {
    pub q: i32,
    pub r: i32,
}

/// Axial neighbour offsets, clockwise starting east.
pub const DIRECTIONS: [(i32, i32); 6] = [(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)];

impl Hex {
    pub const fn new(q: i32, r: i32) -> Self {
        Hex { q, r }
    }

    /// Convert from odd-r offset (`col`, `row`) coordinates.
    pub fn from_offset(col: i32, row: i32) -> Self {
        Hex { q: col - (row - (row & 1)) / 2, r: row }
    }

    /// Convert to odd-r offset `(col, row)` coordinates.
    pub fn to_offset(self) -> (i32, i32) {
        (self.q + (self.r - (self.r & 1)) / 2, self.r)
    }

    pub fn distance(self, other: Hex) -> i32 {
        let dq = self.q - other.q;
        let dr = self.r - other.r;
        (dq.abs() + dr.abs() + (dq + dr).abs()) / 2
    }

    pub fn neighbors(self) -> [Hex; 6] {
        let mut out = [self; 6];
        for (i, (dq, dr)) in DIRECTIONS.iter().enumerate() {
            out[i] = Hex::new(self.q + dq, self.r + dr);
        }
        out
    }

    pub fn is_adjacent(self, other: Hex) -> bool {
        self.distance(other) == 1
    }

    /// Hexes on the straight line from `self` to `other`, both ends included.
    /// A tiny fixed nudge breaks ties deterministically on hex edges.
    pub fn line_to(self, other: Hex) -> Vec<Hex> {
        let n = self.distance(other);
        if n == 0 {
            return vec![self];
        }
        let (ax, az) = (self.q as f64 + 1e-6, self.r as f64 + 2e-6);
        let (bx, bz) = (other.q as f64 + 1e-6, other.r as f64 + 2e-6);
        (0..=n)
            .map(|i| {
                let t = i as f64 / n as f64;
                round_axial(ax + (bx - ax) * t, az + (bz - az) * t)
            })
            .collect()
    }
}

/// Round fractional axial coordinates to the nearest hex.
pub fn round_axial(q: f64, r: f64) -> Hex {
    let x = q;
    let z = r;
    let y = -x - z;
    let (mut rx, mut ry, mut rz) = (x.round(), y.round(), z.round());
    let (dx, dy, dz) = ((rx - x).abs(), (ry - y).abs(), (rz - z).abs());
    if dx > dy && dx > dz {
        rx = -ry - rz;
    } else if dy > dz {
        ry = -rx - rz;
    } else {
        rz = -rx - ry;
    }
    let _ = ry;
    Hex::new(rx as i32, rz as i32)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn offset_roundtrip() {
        for row in -3..12 {
            for col in -3..14 {
                assert_eq!(Hex::from_offset(col, row).to_offset(), (col, row));
            }
        }
    }

    #[test]
    fn offset_known_values() {
        assert_eq!(Hex::from_offset(0, 0), Hex::new(0, 0));
        assert_eq!(Hex::from_offset(0, 1), Hex::new(0, 1));
        assert_eq!(Hex::from_offset(0, 2), Hex::new(-1, 2));
        assert_eq!(Hex::from_offset(3, 3), Hex::new(2, 3));
    }

    #[test]
    fn neighbors_are_distance_one_and_distinct() {
        let h = Hex::new(3, -2);
        let n = h.neighbors();
        for a in n {
            assert_eq!(h.distance(a), 1);
        }
        for i in 0..6 {
            for j in (i + 1)..6 {
                assert_ne!(n[i], n[j]);
            }
        }
    }

    #[test]
    fn distance_basics() {
        assert_eq!(Hex::new(0, 0).distance(Hex::new(0, 0)), 0);
        assert_eq!(Hex::new(0, 0).distance(Hex::new(3, 0)), 3);
        assert_eq!(Hex::new(0, 0).distance(Hex::new(2, -3)), 3);
        assert_eq!(Hex::new(0, 0).distance(Hex::new(-2, 4)), 4);
        let a = Hex::new(2, 5);
        let b = Hex::new(-4, 1);
        assert_eq!(a.distance(b), b.distance(a));
    }

    #[test]
    fn offset_adjacent_rows_distance() {
        // Even row 0, col 0 touches row 1 cols -1 and 0.
        let a = Hex::from_offset(0, 0);
        assert_eq!(a.distance(Hex::from_offset(0, 1)), 1);
        assert_eq!(a.distance(Hex::from_offset(-1, 1)), 1);
        assert_eq!(a.distance(Hex::from_offset(1, 1)), 2);
    }

    #[test]
    fn line_includes_endpoints_and_has_unit_steps() {
        let a = Hex::new(0, 0);
        let b = Hex::new(5, -2);
        let l = a.line_to(b);
        assert_eq!(l.len() as i32, a.distance(b) + 1);
        assert_eq!(l[0], a);
        assert_eq!(*l.last().unwrap(), b);
        for w in l.windows(2) {
            assert_eq!(w[0].distance(w[1]), 1);
        }
    }
}
