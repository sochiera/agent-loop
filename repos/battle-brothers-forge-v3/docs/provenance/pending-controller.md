# Pending controller commands

DO NOT EXECUTE these in the bootstrap unit. Controller launch is NOT STARTED. B1 stays blocked until a reviewed engine refuses `implement`, `red`, `fix`, and `escalate` before any write when no different-family reviewer is enabled, smoked, and admissible now, including wait and escalation. Then a new plan review must name that engine commit. Do not unblock by enabling `sol-medium-normal`, adding `strong` to Grok, or waiving family exclusion.

Init, only after an independent review accepts a local commit of this tree:

```bash
export PYTHONDONTWRITEBYTECODE=1
export GIT_OPTIONAL_LOCKS=0
export XDG_STATE_HOME=/home/jan/.hermes/cache/scratch/bb-forge-v3-preparation/runtime-state
export PYTHONPATH=/home/jan/Sources/agent-loop-forge-v3/src
# Use forge-next/src instead if the parent has not accepted v3 schema integration.
CFG=/home/jan/.config/forge/projects/battle-brothers-forge-v3.toml
/usr/bin/python3 -B -m forge --config "$CFG" init
```

`init` updates the branch by CAS from a private index (`docsindex.initialize`, `gitops.cas`). It does not refresh the host index or worktree. After the controller owns the branch, do not `git add` or commit from the host checkout. Private indexes and CAS leave the host checkout stale. Reads and builds for role agents use a fresh `git archive` of the pinned commit outside the target. Resync of the host checkout requires a stopped controller, expected OIDs before and after, preservation of index, worktree, and untracked files, and refusal on unexplained differences. Never reset a dirty checkout.

Continuous run, also DO NOT EXECUTE:

```bash
/usr/bin/python3 -B -m forge --config "$CFG" run
```

Parent quota gate before every later bounded long stage, and again between stages: `weekly_left >= 33` and `5h_left > 0` from `codex-weekly-left.py`. Unknown is a refusal. The engine is not that quota guard.
