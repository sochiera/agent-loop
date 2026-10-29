//! Pointy-top axial hex maths, map and BFS. APPROXIMATION: slice design.

pub const RADIUS: i32 = 4;
const GRID: usize = (2 * RADIUS + 1) as usize;

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Hex {
    pub q: i32,
    pub r: i32,
}

pub const fn hex(q: i32, r: i32) -> Hex {
    Hex { q, r }
}

/// Fixed neighbour order; it matters for determinism.
pub const NEIGHBOR_DIRS: [(i32, i32); 6] = [(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)];

/// Impassable, fixed.
pub const OBSTACLES: [Hex; 5] = [hex(0, 0), hex(1, -1), hex(-1, 1), hex(2, 1), hex(-2, -1)];

impl Hex {
    pub fn s(self) -> i32 {
        -self.q - self.r
    }

    pub fn distance(self, o: Hex) -> u32 {
        let dq = (self.q - o.q).abs();
        let dr = (self.r - o.r).abs();
        let ds = (self.s() - o.s()).abs();
        ((dq + dr + ds) / 2) as u32
    }

    pub fn neighbors(self) -> [Hex; 6] {
        NEIGHBOR_DIRS.map(|(dq, dr)| hex(self.q + dq, self.r + dr))
    }

    pub fn in_map(self) -> bool {
        self.q.abs().max(self.r.abs()).max(self.s().abs()) <= RADIUS
    }

    pub fn is_obstacle(self) -> bool {
        OBSTACLES.contains(&self)
    }

    fn index(self) -> usize {
        ((self.q + RADIUS) as usize) * GRID + (self.r + RADIUS) as usize
    }
}

/// All 61 map hexes in a stable order.
pub fn map_hexes() -> Vec<Hex> {
    let mut v = Vec::new();
    for q in -RADIUS..=RADIUS {
        for r in -RADIUS..=RADIUS {
            let h = hex(q, r);
            if h.in_map() {
                v.push(h);
            }
        }
    }
    v
}

/// BFS distances from `from` over map hexes that are not obstacles and not in
/// `occupied`. `from` itself has distance 0 (it may be in `occupied`).
pub fn bfs_distances(from: Hex, occupied: &[Hex]) -> Vec<(Hex, u32)> {
    bfs(from, occupied).0
}

fn bfs(from: Hex, occupied: &[Hex]) -> (Vec<(Hex, u32)>, Vec<Option<Hex>>) {
    let mut parent: Vec<Option<Hex>> = vec![None; GRID * GRID];
    let mut seen = vec![false; GRID * GRID];
    let mut order = vec![(from, 0u32)];
    seen[from.index()] = true;
    let mut i = 0;
    while i < order.len() {
        let (cur, d) = order[i];
        i += 1;
        for n in cur.neighbors() {
            if !n.in_map() || n.is_obstacle() || occupied.contains(&n) || seen[n.index()] {
                continue;
            }
            seen[n.index()] = true;
            parent[n.index()] = Some(cur);
            order.push((n, d + 1));
        }
    }
    (order, parent)
}

/// Shortest deterministic path, excluding `from`, including `to`.
/// `None` when `to` is unreachable, blocked, off-map or equal to `from`.
pub fn bfs_path(from: Hex, to: Hex, occupied: &[Hex]) -> Option<Vec<Hex>> {
    if to == from || !to.in_map() || to.is_obstacle() || occupied.contains(&to) {
        return None;
    }
    let (_, parent) = bfs(from, occupied);
    parent[to.index()]?;
    let mut path = vec![to];
    let mut cur = to;
    while let Some(p) = parent[cur.index()] {
        if p == from {
            break;
        }
        path.push(p);
        cur = p;
    }
    path.reverse();
    Some(path)
}
