//! Balance probe for the campaign: `cargo run -p tactics-core --example campaign_sim --release -- [SEEDS] [BATTLES]`
//! Plays autopiloted campaigns headlessly and prints aggregate results.
use tactics_core::autopilot::run_headless;

fn main() {
    let mut args = std::env::args().skip(1);
    let seeds: u64 = args.next().and_then(|s| s.parse().ok()).unwrap_or(200);
    let battles: u32 = args.next().and_then(|s| s.parse().ok()).unwrap_or(8);
    let (mut f3, mut w3) = ([0u32; 3], [0u32; 3]);
    let (mut fought, mut won, mut over, mut dead, mut days, mut gold, mut done) = (0u64, 0u64, 0u64, 0u64, 0u64, 0i64, 0u64);
    for seed in 0..seeds {
        let r = run_headless(seed, battles);
        for i in 0..3 {
            f3[i] += r.fights[i];
            w3[i] += r.wins[i];
        }
        fought += r.battles as u64;
        won += r.victories as u64;
        over += r.game_over as u64;
        dead += r.fallen.len() as u64;
        days += r.day as u64;
        gold += r.gold as i64;
        done += r.contracts_completed as u64;
    }
    println!(
        "{seeds} campaigns x {battles} battles: victory {:.1}%, game over {:.1}%, avg dead {:.2}, avg days {:.1}, avg gold {:.0}, avg contracts paid {:.2}",
        won as f64 * 100.0 / fought.max(1) as f64,
        over as f64 * 100.0 / seeds as f64,
        dead as f64 / seeds as f64,
        days as f64 / seeds as f64,
        gold as f64 / seeds as f64,
        done as f64 / seeds as f64,
    );
    for i in 0..3 {
        println!("  difficulty {}: {} fights, {:.1}% won", i + 1, f3[i], w3[i] as f64 * 100.0 / f3[i].max(1) as f64);
    }
}
