# Fidelity matrix

Columns are authority and confidence on one side, and today's implementation, code, tests, and the next evidence on the other. Parity is not claimed. Approximate slice numbers are not original rules.

| Topic | Authority | Confidence | Implemented state | Code | Tests | Next evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Recover current fatigue / 2 (`UC-COMBAT-065`) | Static bytecode `getFatigue() / 2` for the current value, plus the catalog sentence | partial | Integer halve of current fatigue on the active unit. Even fixtures only. No perk gate, no 9 AP cost, no "nothing else this round" | `crates/bb_rules/src/rules.rs` `Command::Recover` | `recover_sets_active_unit_fatigue_to_half_current_value` (40 to 20), `recover_keeps_zero_fatigue_at_zero` | Runtime numeric type, odd rounding, cost, eligibility. Original runtime not executed |
| Move AP and fatigue | Slice design. Name `apCost` is OBSERVED-NAME-ONLY | none for original numbers | `MOVE_AP = 2`, `MOVE_FATIGUE = 2`, marked invented in source | `crates/bb_rules/src/rules.rs` | `move_costs_ap_and_fatigue_and_errors_keep_state` | Do not promote to original rules |
| Attack AP and fatigue | Slice design | none for original numbers | `ATTACK_AP = 4`, `ATTACK_FATIGUE = 10` | `crates/bb_rules/src/rules.rs` | `attack_costs_and_errors_keep_state` | gap |
| Turn-start fatigue regen | Slice design | none for original numbers | `FATIGUE_REGEN = 15` subtracted at turn start | `crates/bb_rules/src/rules.rs` | `turn_start_refreshes_ap_and_regenerates_fatigue` | gap |
| Hit chance, armour soak, initiative order, Wait, EndTurn | Slice design. Several names are OBSERVED-NAME-ONLY | none for original formulas | Present as independent slice behavior | `crates/bb_rules/src/rules.rs`, `crates/bb_rules/src/lib.rs` | `hit_chance_values_and_clamp`, `armour_example_from_plan`, `initiative_queue_ties_by_id_and_fatigue_matters`, `wait_moves_to_end_keeps_ap_and_only_once` | gap versus catalog tooltips and 9 AP |
| Morale, injuries, perks other than the Recover effect above | Catalog text | catalog or UNCONFIRMED per row | absent | gap | gap | native reference comparison |
| Invalid move feedback | Reviewed layer A GUI behavior, not an original rule | slice-only | Click path surfaces feedback | `crates/bb_game/src/main.rs` | `crates/bb_game/tests/invalid_move_feedback.rs` | not original-UI evidence |
| Map, 3v3 scenario, AI | Slice design | none | 61-hex map, placeholder sides, one-command AI | `crates/bb_rules/src/hex.rs`, `scenario.rs`, `ai.rs` | rules suite and `headless_smoke` bin (0 unit tests) | gap |
| Campaign, company, roster, inventory, settlements, save, ironman | Catalog scenarios | per row; many UNCONFIRMED | absent. Layer B company/save bytes were not imported | gap | gap | see `docs/provenance/layer-b-deferred.md` |
| Official store frames | Steam appdetails 365360 and store page [1] [2] | image bytes hashed | Inspection references only. Not rules and not shipped art | `docs/source/reference-images/` | not a Rust test | original-runtime capture still missing |
| Rust test selection `test_one` | Operator config | n/a | Fail-closed: writes a JUnit error and exits 1. Pytest RED/GREEN is not a Rust node id | operator `commands.test_one` | not a game test | do not treat a green doctor parse as Rust selection |

Other catalog rows keep their own authority and confidence in the scenario files. Default implementation for those rows is absent, code gap, tests gap.

[1] https://store.steampowered.com/api/appdetails?appids=365360&l=english
[2] https://store.steampowered.com/app/365360
