//! Headless smoke: both sides played by `ai::plan_turn`, twice, same seed.
//! `--adrenaline` gives player units the capability for one conservative activation
//! in the ordinary attack policy; an eligible Stunning Blow now takes priority.
//! `--rotation` and `--footwork` only grant
//! their capabilities for scripted commands; AI use of those skills is deferred.
//! `--fast-adaptation` is a passive, not a skill: no AI policy selects it, and every
//! ordinary AI attack by a player unit receives its accepted effect automatically.
//! `--dodge` is likewise a passive: no AI policy selects it, and every attack against a
//! Dodge-capable player defender automatically uses the accepted Dodge defense.
//! `--nine-lives` is a passive too: stock seed-1 AI play may never deal a lethal melee hit, so
//! the smoke only proves valid, deterministic, bounded play; protection itself is covered by
//! scripted encounters.
//! `--berserk` is passive as well: it needs no activation command or AI selection policy; kills
//! can refund AP, visible in the replayed AP values and `BerserkTriggered` event.
//! `--pathfinder` is passive movement fatigue reduction. The shared move preview and unit
//! resource display already show its effect, and movement affordability reads it without an
//! activation control or a new AI selection policy.
//! `--indomitable` grants the stance capability (inactive). The deterministic AI may activate it
//! for a wounded actor with a current non-Fleeing adjacent enemy when resources also reserve an
//! attack and no eligible Stunning Blow takes priority; stock seed-1 smoke need not meet that policy. Focused legal encounters cover activation
//! followed by attack, while each smoke run still validates repeatability and bounded completion.
//! `--two-hex` gives player units `MeleeReach::TwoHex` (APPROXIMATION, enemies stay adjacent-only).
//! Ordinary Attack selection uses `legal_targets`, so an affordable distance-two target is attacked
//! without moving first; Stunning Blow still requires adjacency and stock approach planning still
//! seeks adjacent hexes.
//! `--stunning-blow` grants the capability to player units only. T66 adds
//! deterministic Stunning Blow selection for either faction when that unit has
//! the capability; exact target filtering, ordering and priority are APPROXIMATION.
//! `--killing-frenzy` grants its passive to player units only (APPROXIMATION). Accepted
//! melee execution activates it after a paid melee kill; AI policy is unchanged.

use bb_rules::scenario::{self, LaunchArgs};
use bb_rules::{EndReason, Outcome, ai};

const MAX_COMMANDS: u32 = 10_000;

fn run(args: LaunchArgs) -> Result<(Outcome, EndReason, u32, u64), String> {
    let mut s = scenario::launch_battle_for(args);
    let mut n = 0;
    while s.outcome().is_none() {
        let cmds = ai::plan_turn(&s);
        let Some(&cmd) = cmds.first() else { return Err("plan_turn returned an empty list during a running battle".into()) };
        n += 1;
        if n > MAX_COMMANDS {
            return Err(format!("exceeded {MAX_COMMANDS} commands"));
        }
        s.apply(cmd).map_err(|e| format!("RuleError {e:?} from AI command {cmd:?}"))?;
    }
    Ok((s.outcome().unwrap(), s.end_reason().unwrap(), s.round, s.state_hash()))
}

fn main() {
    let args = match scenario::parse_args(std::env::args().skip(1)) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("{e}");
            std::process::exit(2);
        }
    };
    let results = (run(args), run(args));
    match results {
        (Ok(a), Ok(b)) if a == b => {
            println!("OUTCOME={:?} REASON={:?} ROUNDS={} HASH={:016x}", a.0, a.1, a.2, a.3);
        }
        (Ok(a), Ok(b)) => {
            eprintln!("non-deterministic: {a:?} vs {b:?}");
            std::process::exit(1);
        }
        (Err(e), _) | (_, Err(e)) => {
            eprintln!("smoke failed: {e}");
            std::process::exit(1);
        }
    }
}
