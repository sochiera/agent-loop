# Native smokes

Doctor without `--smoke` exited 0. Sandbox probe status `pass` with checks `gate`, `readonly`, and `writable` true. That probe is not a Cargo test inside bwrap. `commands.test` was not executed by doctor. `runtime_verified` from config-check is false.

Quota helper at the same stage, raw line: `weekly_left=50% weekly_used=50% 5h_left=10% 5h_used=90%`. Under the plan's comparison this is `weekly_left` 50 >= 33 and `5h_left` 10 > 0. No Codex or Grok smoke was started after that measurement. The remaining finish budget was too short for those long inferences. Their text, resume, readonly, and image capabilities are untested. Codex `actual_model` is unattested. Grok `modelUsage` was not observed, so `grok-4.7-build` is not claimed.

| Profile | Route role | text | resume | readonly | image | Result |
| --- | --- | --- | --- | --- | --- | --- |
| haiku-high | single Claude call | fail | not reached | not reached | not reached | exit 1, status `unknown_fail`, capabilities `[]` |
| sonnet-medium | not called | untested | untested | untested | untested | account then disabled |
| opus-medium | not called | untested | untested | untested | untested | account then disabled |
| grok-medium | hard implementer, normal | untested | untested | untested | untested | not started |
| luna-xhigh | easy implementer | untested | untested | untested | untested | not started |
| sol-medium-strong | strong review / PO fallback | untested | untested | untested | untested | not started |
| sol-medium-normal | disabled | n/a | n/a | n/a | n/a | not smoked |
| terra | disabled | n/a | n/a | n/a | n/a | not smoked |

Haiku record: binary `/home/jan/.local/share/claude/versions/2.1.287`, version `2.1.287 (Claude Code)`, SHA256 `3920489a5109cff5786a1a392c25277408ff22bc796d5edb9c16a60e5a1718f0`. Model pinned `claude-haiku-5-5`, effort `high`. `actual_model` null. Elapsed about 0.975s. Stderr: `[claude-code:unrecognized_model]` for `claude-haiku-5-5`. Native result text: `Failed to authenticate: OAuth session expired and could not be refreshed`. No second login and no API billing call. Because that result is an OAuth failure, `accounts.claude.enabled` was set false in the installed TOML only. Sonnet and Opus were not smoked. Their model strings are not proven bad. They are UNAVAILABLE because the shared Claude account is disabled after this OAuth failure, not because a separate route was tested.

Evidence JSON: `/home/jan/.hermes/cache/scratch/bb-forge-v3-preparation/runtime-state/forge/battle-brothers-forge-v3/smoke/haiku-high.json`.
