# Edge cases

Provenance: copied from `/home/jan/Sources/total-brothers/docs/battle-brothers-use-cases.md` SHA256 `0ccec5426118234ae18853aadaebf334de33eebbb097a323099b542f36e1ac87` (4418 lines, 330 `UC-*` ids, 77 `UNCONFIRMED` markers). This is a reusable player-loop catalog for a mercenary company, not a kingdom or Total War brief, and not a recovered numeric formula sheet. Authority and implementation are separate columns. Catalog confidence is not original-runtime confirmation. The local Battle Brothers install was not executed. Prototype screenshots from the old slice are rejected as original-game references.

Parity is not claimed for any row. Only `UC-COMBAT-065` has a separate static implementation note, and that note does not accept the catalog's 9 AP cost, perk gate, or full-turn claim.

Inspection frames linked here are official Steam store assets, analysis-only, not shipped game art. See [authentic reference](05-authentic-reference.md).

![](reference-images/steam-ss-5.jpg)

## Trace table

| ID | Title | Authority | Confidence | Implementation | Code | Tests | Next evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| UC-EDGE-001 | Retreat with wounded brothers | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-EDGE-002 | Starving company | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-EDGE-003 | No contracts available | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-EDGE-004 | Company wipe on the field | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-EDGE-005 | Abandoned / razed settlement | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-EDGE-006 | Broke (0 crowns) but not dead | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-EDGE-007 | Stash full after a huge battle | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-EDGE-008 | Night arrival at a city | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-EDGE-009 | Chased into a settlement | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-EDGE-010 | Last brother fleeing in combat | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-EDGE-011 | Contract deadline vs sea voyage duration | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-EDGE-012 | Enemy party kills the contract target first | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-EDGE-013 | All houses hostile | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-EDGE-014 | Overencumbered food rotting while tools empty | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-EDGE-015 | Ironman death of a key sergeant | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-EDGE-016 | Map seed with lopsided houses (10/5/2 fiefs) | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-EDGE-017 | Player attacks allied NPCs in a joint battle | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-EDGE-018 | Save compatibility when content flags change | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-EDGE-019 | All medicine gone, multiple injuries | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-EDGE-020 | Famed item on a champion who retreated | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |

## Scenarios

Preconditions, main flows, alternate and exception flows, and observable results are copied. `UNCONFIRMED` markers are kept.

### UC-EDGE-001 — Retreat with wounded brothers

**Tags:** `combat | company`
**Actor:** Player
**Precondition:** Several brothers have smashed legs / nets; player hits Retreat.
**Main flow:**
1. Slow units may be caught and die despite ZoC defence bonus.
2. Healthy edge units pop out first.
3. Survivors keep their injuries; some gear may be left on the field if dead.
**Alternate / exception flows:**
- Auto-path should prefer not walking through three ZoCs if a longer path exists. UNCONFIRMED vanilla quality.
- Player can still manually walk wounded to edge then retreat.
**Observable result:** A netted brother can die on auto-retreat while a fresh brother on the border lives.

### UC-EDGE-002 — Starving company

**Tags:** `company | economy`
**Actor:** System
**Precondition:** Food = 0 for one or more days.
**Main flow:**
1. Mood collapses. Desertion events fire. No instant HP death.
2. Buying food in the next town is the fix.
3. Hunting fights can drop meat.
**Alternate / exception flows:**
- Gluttons break first. Spartans last.
- Paymaster does not feed people.
**Observable result:** After 2–4 hungry days (lock), at least one desertion event is eligible; HP bars on the world map are unchanged.

### UC-EDGE-003 — No contracts available

**Tags:** `economy | overworld`
**Actor:** Player
**Precondition:** All nearby boards empty (low relations, night, tiny hamlets, crisis vacuum).
**Main flow:**
1. Player must travel, wait for restock, hunt wild camps, trade, or arena.
2. UI should not imply a broken game: 'no work today' copy.
3. Agent retinue helps find who still has jobs.
**Alternate / exception flows:**
- Waiting in town with clock paused will never restock — must leave so time flows, or camp outside.
**Observable result:** A tester can survive a week with zero contracts via hunting/trade without a hard lock.

### UC-EDGE-004 — Company wipe on the field

**Tags:** `combat | meta`
**Actor:** System
**Precondition:** All deployed brothers die; reserves exist in town roster.
**Main flow:**
1. **Design lock needed.** Recommended: battle lost, remaining reserves still form the company on the world map, possibly with a mood disaster and nearby enemies still there.
2. If the design instead ends the campaign whenever a battle wipes, document it — vanilla community memory is mixed. UNCONFIRMED.
3. If no reserves and no living brothers: UC-SAVE-003.
**Alternate / exception flows:**
- Dogs left alive do not count as a company.
**Observable result:** QA script both interpretations until PO ticks one; the catalog flags this as a lock.

### UC-EDGE-005 — Abandoned / razed settlement

**Tags:** `overworld | economy`
**Actor:** Player
**Precondition:** Town destroyed by greenskins or abandoned situation.
**Main flow:**
1. No shops, no recruits, no contracts. Maybe a ruins fight.
2. Active contract to that town fails or redirects (lock: fail).
3. Roads still exist. Fog stays revealed.
**Alternate / exception flows:**
- If destruction is off, a lasting negative situation instead of ruins.
**Observable result:** Clicking the ruins does not open a marketplace.

### UC-EDGE-006 — Broke (0 crowns) but not dead

**Tags:** `economy | company`
**Actor:** Player
**Precondition:** Crowns = 0, wages due.
**Main flow:**
1. Unpaid flag. Mood down. Desertion risk. Shops won't sell.
2. Player can still fight, loot, and complete contracts for pay.
3. Cannot hire.
**Alternate / exception flows:**
- Negative gold should not happen; clamp at 0.
- Advance on a new contract can rescue.
**Observable result:** At 0 gold the company still walks; noon ticks fail to pay and trigger desertion weights.

### UC-EDGE-007 — Stash full after a huge battle

**Tags:** `economy`
**Actor:** Player
**Precondition:** Loot > empty cells.
**Main flow:**
1. Player must discard or leave items. No silent deletion of famed items — warn.
2. Tools/meds/ammo clamp at cap; excess lost if not warned.
**Alternate / exception flows:**
- Quartermaster/cart is the long-term fix.
**Observable result:** Trying to take 40 swords into 10 cells forces a leftover-on-field confirm.

### UC-EDGE-008 — Night arrival at a city

**Tags:** `overworld | economy`
**Actor:** Player
**Precondition:** Enter a city at night.
**Main flow:**
1. Tavern only. Cannot hire, smith, temple, train.
2. Player can camp outside until dawn or drink rumours.
**Alternate / exception flows:**
- Sea travel UI closed at night? UNCONFIRMED — recommend closed with other services.
**Observable result:** Building buttons except tavern are disabled with 'returns at dawn' tooltips.

### UC-EDGE-009 — Chased into a settlement

**Tags:** `overworld | combat`
**Actor:** Player
**Precondition:** Faster enemy on the same tile as a town the player clicks.
**Main flow:**
1. Player enters; world combat cancelled. Enemy waits outside or despawns.
2. Leaving immediately may re-aggro.
**Alternate / exception flows:**
- Hostile town cannot be used as sanctuary.
**Observable result:** Clicking a friendly village while hunted drops the engage prompt and opens the town.

### UC-EDGE-010 — Last brother fleeing in combat

**Tags:** `combat | meta`
**Actor:** System
**Precondition:** Only one brother left, he is Fleeing.
**Main flow:**
1. Player cannot issue orders. He may rally or run off the map (retreat) or die.
2. If he exits: retreat result, campaign continues with that wounded man.
3. If he dies: game over if he was the last in the whole roster.
**Alternate / exception flows:**
- Rally the Troops cannot be used if the only brother is the one fleeing unless he has it and is not fleeing — he can't.
**Observable result:** The player watches the last man run; no secret clickable attack.

### UC-EDGE-011 — Contract deadline vs sea voyage duration

**Tags:** `economy | overworld`
**Actor:** Player
**Precondition:** Deadline 3 days, sea route 5 days.
**Main flow:**
1. UI must show voyage duration before pay.
2. Accepting a voyage that overruns the deadline is allowed but will fail the contract on arrival.
**Alternate / exception flows:**
- Block voyages that guarantee fail? Prefer warn, don't block.
**Observable result:** A confirm on docks says 'arrives after contract expiry' in warning colour.

### UC-EDGE-012 — Enemy party kills the contract target first

**Tags:** `economy`
**Actor:** System
**Precondition:** Hunt contract; a noble patrol wipes the brigands.
**Main flow:**
1. Contract fails or auto-poor-completes. UNCONFIRMED vanilla — lock: fail with explanation.
2. Do not leave the player with an uncompletable marker forever.
**Alternate / exception flows:**
- Player attacking the patrol does not complete the original hunt.
**Observable result:** The coin marker clears with a fail message, not an immortal quest.

### UC-EDGE-013 — All houses hostile

**Tags:** `economy | overworld`
**Actor:** Player
**Precondition:** Player betrayed everyone.
**Main flow:**
1. Only wild camps, southern remaining faction, or trade via smuggling (if any) remain.
2. Game still playable until wipe. Recovery via Negotiator / time / crisis end thaw.
**Alternate / exception flows:**
- This is a valid 'you messed up' state, not a crash.
**Observable result:** Every northern town refuses entry; wilderness camps still fightable.

### UC-EDGE-014 — Overencumbered food rotting while tools empty

**Tags:** `economy`
**Actor:** Player
**Precondition:** Food will rot in 1 day, armour broken, 0 tools, 0 gold.
**Main flow:**
1. Player must choose: fight for loot, sell rotting food now, or dismiss mouths.
2. Systems must keep ticking independently (spoil vs repair vs wages).
**Alternate / exception flows:**
- No automatic bailout crate.
**Observable result:** Next day: food gone, armour still broken, wages failed — three separate logs.

### UC-EDGE-015 — Ironman death of a key sergeant

**Tags:** `meta | company`
**Actor:** Player
**Precondition:** Only Rally user dies in ironman.
**Main flow:**
1. No reload. Company continues weaker. Player may train another Rally later.
**Alternate / exception flows:**
- Ambition that required 'have Rally' if already completed stays completed.
**Observable result:** Autosave after battle has the sergeant gone forever.

### UC-EDGE-016 — Map seed with lopsided houses (10/5/2 fiefs)

**Tags:** `overworld`
**Actor:** System
**Precondition:** Generator rolled unbalanced fiefs.
**Main flow:**
1. Valid. Weak house offers fewer contracts; strong house dominates civil war.
2. Do not silently rebalance after day 1.
**Alternate / exception flows:**
- Tooltip on relations can show fief counts for debug/QA.
**Observable result:** One house owns most of the map; that is not a generation bug.

### UC-EDGE-017 — Player attacks allied NPCs in a joint battle

**Tags:** `combat`
**Actor:** Player
**Precondition:** Great-battle contract with allied soldiers.
**Main flow:**
1. Friendly fire: disabled for clicks on allies (recommended) OR enabled with relation hit. Lock: disable targeting allies.
2. AI allies can still die to enemy AoE.
**Alternate / exception flows:**
- Killing allies on purpose should be hard.
**Observable result:** Clicking an allied footman does not swing.

### UC-EDGE-018 — Save compatibility when content flags change

**Tags:** `meta`
**Actor:** Player
**Precondition:** Save made with southern content on; load with it off.
**Main flow:**
1. Refuse load or strip south with a warning. Do not crash on missing city-state IDs.
**Alternate / exception flows:**
- Prefer refuse.
**Observable result:** Error dialog, return to menu.
---

### UC-EDGE-019 — All medicine gone, multiple injuries

**Tags:** `company | economy`
**Actor:** Player
**Precondition:** 3 injuries, 0 medicine.
**Main flow:**
1. Timers freeze. Brothers can still be fielded with penalties.
2. Temple can still cut time for crowns without medicine.
3. HP regen continues.
**Alternate / exception flows:**
- Buying 1 medicine advances all? Vanilla: 1 per injury per day — 3 meds/day to progress all three.
**Observable result:** Without meds the injury days remaining do not drop on the road; after a temple payment they do.

### UC-EDGE-020 — Famed item on a champion who retreated

**Tags:** `combat | economy`
**Actor:** Player
**Precondition:** Champion fled the map.
**Main flow:**
1. Item is lost unless the champion’s party is fought again. UNCONFIRMED whether the same champion respawns.
2. Player has incentive to intercept routers.
**Alternate / exception flows:**
- Do not drop the famed item on the empty hex if the unit lived.
**Observable result:** Loot screen has no named weapon after a champion escapes.
---
