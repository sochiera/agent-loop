//! Terrain and the rectangular hex map.

use crate::hex::Hex;

#[derive(Copy, Clone, Debug, PartialEq, Eq, Hash)]
pub enum Terrain {
    Plain,
    Forest,
    Hill,
    Rock,
    Water,
}

impl Terrain {
    /// AP to enter this terrain, or `None` if impassable.
    pub fn move_cost(self) -> Option<i32> {
        match self {
            Terrain::Plain => Some(2),
            Terrain::Forest => Some(4),
            Terrain::Hill => Some(3),
            Terrain::Rock | Terrain::Water => None,
        }
    }

    pub fn passable(self) -> bool {
        self.move_cost().is_some()
    }

    /// Extra defense granted to a unit standing here.
    pub fn defense_bonus(self) -> i32 {
        match self {
            Terrain::Forest => 15,
            Terrain::Hill => 0,
            _ => 0,
        }
    }

    /// Whether this terrain stops ranged attacks passing *through* it.
    /// (The shooter's and the target's own hexes never block.)
    pub fn blocks_los(self) -> bool {
        matches!(self, Terrain::Rock | Terrain::Forest)
    }

    pub fn is_high_ground(self) -> bool {
        self == Terrain::Hill
    }

    pub fn name(self) -> &'static str {
        match self {
            Terrain::Plain => "Plain",
            Terrain::Forest => "Forest",
            Terrain::Hill => "Hill",
            Terrain::Rock => "Rock",
            Terrain::Water => "Water",
        }
    }

    fn from_char(c: char) -> Option<Terrain> {
        Some(match c {
            '.' => Terrain::Plain,
            'f' => Terrain::Forest,
            'h' => Terrain::Hill,
            '#' => Terrain::Rock,
            '~' => Terrain::Water,
            _ => return None,
        })
    }
}

#[derive(Clone, Debug)]
pub struct Map {
    width: i32,
    height: i32,
    tiles: Vec<Terrain>,
}

impl Map {
    pub fn new(width: i32, height: i32, fill: Terrain) -> Map {
        Map { width, height, tiles: vec![fill; (width * height) as usize] }
    }

    /// Build a map from rows of text: `.` plain, `f` forest, `h` hill, `#` rock, `~` water.
    /// Panics on malformed input (maps are authored in code).
    pub fn from_ascii(rows: &[&str]) -> Map {
        let height = rows.len() as i32;
        let width = rows[0].chars().count() as i32;
        let mut map = Map::new(width, height, Terrain::Plain);
        for (row, line) in rows.iter().enumerate() {
            assert_eq!(line.chars().count() as i32, width, "ragged map row {row}");
            for (col, c) in line.chars().enumerate() {
                let t = Terrain::from_char(c).unwrap_or_else(|| panic!("bad terrain char {c:?}"));
                map.set(Hex::from_offset(col as i32, row as i32), t);
            }
        }
        map
    }

    pub fn width(&self) -> i32 {
        self.width
    }

    pub fn height(&self) -> i32 {
        self.height
    }

    /// Row-major index (offset coordinates) if the hex is on the map.
    pub fn index(&self, h: Hex) -> Option<usize> {
        let (col, row) = h.to_offset();
        if col < 0 || row < 0 || col >= self.width || row >= self.height {
            None
        } else {
            Some((row * self.width + col) as usize)
        }
    }

    pub fn contains(&self, h: Hex) -> bool {
        self.index(h).is_some()
    }

    pub fn terrain(&self, h: Hex) -> Option<Terrain> {
        self.index(h).map(|i| self.tiles[i])
    }

    pub fn set(&mut self, h: Hex, t: Terrain) {
        if let Some(i) = self.index(h) {
            self.tiles[i] = t;
        }
    }

    pub fn len(&self) -> usize {
        self.tiles.len()
    }

    pub fn is_empty(&self) -> bool {
        self.tiles.is_empty()
    }

    /// All hexes in row-major order.
    pub fn hexes(&self) -> impl Iterator<Item = Hex> + '_ {
        let w = self.width;
        (0..self.height).flat_map(move |row| (0..w).map(move |col| Hex::from_offset(col, row)))
    }

    /// Hex for a row-major index (inverse of [`Map::index`]).
    pub fn hex_at(&self, index: usize) -> Hex {
        let i = index as i32;
        Hex::from_offset(i % self.width, i / self.width)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ascii_parsing_and_bounds() {
        let m = Map::from_ascii(&["..f", "h#~"]);
        assert_eq!((m.width(), m.height()), (3, 2));
        assert_eq!(m.terrain(Hex::from_offset(2, 0)), Some(Terrain::Forest));
        assert_eq!(m.terrain(Hex::from_offset(0, 1)), Some(Terrain::Hill));
        assert_eq!(m.terrain(Hex::from_offset(1, 1)), Some(Terrain::Rock));
        assert_eq!(m.terrain(Hex::from_offset(2, 1)), Some(Terrain::Water));
        assert_eq!(m.terrain(Hex::from_offset(3, 0)), None);
        assert_eq!(m.terrain(Hex::from_offset(-1, 0)), None);
        assert_eq!(m.hexes().count(), 6);
    }

    #[test]
    fn index_roundtrip() {
        let m = Map::new(12, 10, Terrain::Plain);
        for (i, h) in m.hexes().enumerate() {
            assert_eq!(m.index(h), Some(i));
            assert_eq!(m.hex_at(i), h);
        }
    }

    #[test]
    fn costs() {
        assert_eq!(Terrain::Plain.move_cost(), Some(2));
        assert!(Terrain::Forest.move_cost().unwrap() > 2);
        assert!(Terrain::Hill.move_cost().unwrap() > 2);
        assert_eq!(Terrain::Rock.move_cost(), None);
        assert_eq!(Terrain::Water.move_cost(), None);
    }
}
