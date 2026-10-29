//! Headless balance probe: `cargo run -p tactics-core --example sim --release -- [N]`
//! Plays N seeded battles with the AI on both sides and prints win rates.
use tactics_core::{ai, scenario, Outcome, Team};

fn main() {
    let n: u64 = std::env::args().nth(1).and_then(|s| s.parse().ok()).unwrap_or(500);
    let (mut wins, mut rounds, mut surv, mut timeouts) = (0u64, 0u64, 0u64, 0u64);
    for seed in 0..n {
        let mut b = scenario::skirmish(seed);
        while b.outcome().is_none() {
            let cmd = ai::decide(&b);
            b.apply(cmd).expect("legal");
        }
        if b.outcome() == Some(Outcome::Victory) {
            wins += 1;
        }
        if b.timed_out() {
            timeouts += 1;
        }
        rounds += b.round() as u64;
        surv += b.living(Team::Player).count() as u64;
    }
    println!(
        "{n} battles: victory {:.1}%, avg rounds {:.1}, avg player survivors {:.2}, timeouts {timeouts}",
        wins as f64 * 100.0 / n as f64,
        rounds as f64 / n as f64,
        surv as f64 / n as f64
    );
}
