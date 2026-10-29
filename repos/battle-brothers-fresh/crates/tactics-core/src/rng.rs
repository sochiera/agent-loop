//! Small hand-written PCG32 generator so battles are reproducible from a seed.

#[derive(Clone, Debug)]
pub struct Rng {
    state: u64,
    inc: u64,
}

impl Rng {
    pub fn new(seed: u64) -> Self {
        let mut rng = Rng { state: 0, inc: (seed << 1) | 1 };
        rng.next_u32();
        rng.state = rng.state.wrapping_add(seed ^ 0x9E37_79B9_7F4A_7C15);
        rng.next_u32();
        rng
    }

    pub fn next_u32(&mut self) -> u32 {
        let old = self.state;
        self.state = old
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(self.inc);
        let xorshifted = (((old >> 18) ^ old) >> 27) as u32;
        let rot = (old >> 59) as u32;
        xorshifted.rotate_right(rot)
    }

    /// Uniform integer in `0..bound` (`bound > 0`), using rejection to avoid bias.
    pub fn below(&mut self, bound: u32) -> u32 {
        assert!(bound > 0);
        let threshold = bound.wrapping_neg() % bound;
        loop {
            let r = self.next_u32();
            if r >= threshold {
                return r % bound;
            }
        }
    }

    /// Uniform integer in `lo..=hi`.
    pub fn range_inclusive(&mut self, lo: i32, hi: i32) -> i32 {
        debug_assert!(lo <= hi);
        lo + self.below((hi - lo + 1) as u32) as i32
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn same_seed_same_stream() {
        let mut a = Rng::new(42);
        let mut b = Rng::new(42);
        for _ in 0..100 {
            assert_eq!(a.next_u32(), b.next_u32());
        }
    }

    #[test]
    fn different_seed_differs() {
        let mut a = Rng::new(1);
        let mut b = Rng::new(2);
        let same = (0..32).filter(|_| a.next_u32() == b.next_u32()).count();
        assert!(same < 32);
    }

    #[test]
    fn below_stays_in_bounds_and_covers() {
        let mut r = Rng::new(7);
        let mut seen = [false; 10];
        for _ in 0..1000 {
            let v = r.below(10) as usize;
            seen[v] = true;
        }
        assert!(seen.iter().all(|s| *s));
        for _ in 0..200 {
            let v = r.range_inclusive(5, 8);
            assert!((5..=8).contains(&v));
        }
    }
}
