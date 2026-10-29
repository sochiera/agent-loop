# Company origin and camp

Provenance: copied from `/home/jan/Sources/total-brothers/docs/battle-brothers-use-cases.md` SHA256 `0ccec5426118234ae18853aadaebf334de33eebbb097a323099b542f36e1ac87` (4418 lines, 330 `UC-*` ids, 77 `UNCONFIRMED` markers). This is a reusable player-loop catalog for a mercenary company, not a kingdom or Total War brief, and not a recovered numeric formula sheet. Authority and implementation are separate columns. Catalog confidence is not original-runtime confirmation. The local Battle Brothers install was not executed. Prototype screenshots from the old slice are rejected as original-game references.

Parity is not claimed for any row. Only `UC-COMBAT-065` has a separate static implementation note, and that note does not accept the catalog's 9 AP cost, perk gate, or full-turn claim.

Inspection frames linked here are official Steam store assets, analysis-only, not shipped game art. See [authentic reference](05-authentic-reference.md).

![](reference-images/steam-ss-1.jpg)

## Trace table

| ID | Title | Authority | Confidence | Implementation | Code | Tests | Next evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| UC-ORIGIN-001 | Select company origin | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-ORIGIN-002 | Customize banner and company name | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-ORIGIN-003 | Rebuilding company — default origin | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-ORIGIN-004 | Fresh company origin — poorer start | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-ORIGIN-005 | Peasant militia origin — larger cheap roster, lower equipment | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-ORIGIN-006 | Deserter origin — hunted by a house | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-ORIGIN-007 | Northern raiders origin — coastal/barbarian start | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-ORIGIN-008 | Trading caravan origin — wagons, trade focus | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-ORIGIN-009 | Poacher origin — ranged/wildcraft start | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-ORIGIN-010 | Beast hunter origin — trophy prices, worse trade goods | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-ORIGIN-011 | Manhunter origin — captives as extra fielded troops | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-ORIGIN-012 | Arena gladiators origin — tiny elite start in the south | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-ORIGIN-013 | Lone champion origin — one fighter plus maybe a hound | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-ORIGIN-014 | Oathbound zealots origin — rotating vows | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-ORIGIN-015 | Anatomist origin — corpse research | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-ORIGIN-016 | Occult cult origin — boons and demands | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-ORIGIN-017 | Southern mercenaries origin | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-ORIGIN-018 | Starting roster names, faces, backgrounds are generated | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-ORIGIN-019 | Player does not create a custom avatar captain as a combat unit | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-ORIGIN-020 | Banner ambition later uses the chosen standard | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-CAMP-001 | Make and break camp | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-CAMP-002 | Repair while camped | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-CAMP-003 | Treat wounds while camped | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-CAMP-004 | Scout / vision while camped | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-CAMP-005 | Hunt while camped | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-CAMP-006 | Gather while camped | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-CAMP-007 | Donate / alms as a camp or town action | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-CAMP-008 | Train in camp | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-CAMP-009 | Camp is dangerous in hostile territory | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-CAMP-010 | Time-skip until dawn as a reason to camp | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-CAMP-011 | Escort-caravan repair bonus similar to camp | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |

## Scenarios

Preconditions, main flows, alternate and exception flows, and observable results are copied. `UNCONFIRMED` markers are kept.

### UC-ORIGIN-001 — Select company origin

**Tags:** `meta | company`
**Actor:** Player
**Precondition:** New campaign screen.
**Main flow:**
1. Player browses origin cards: title, original short pitch, starting roster size, special rules, recommended difficulty.
2. Selecting an origin updates starting funds, map spawn region, and roster preview.
3. Player confirms.
**Alternate / exception flows:**
- Locked origins (DLC/content packs not enabled) show as locked with a non-marketing reason.
- Default origin is a small veteran band rebuilding after a failed hunt (generic 'rebuilding company').
**Observable result:** Confirmed campaign starts with that origin's roster and rules.

### UC-ORIGIN-002 — Customize banner and company name

**Tags:** `meta | company | ui`
**Actor:** Player
**Precondition:** Company creation step.
**Main flow:**
1. Player picks a banner from a heraldic set (colors, emblem).
2. Player enters a company name (or accepts a generated one).
3. Banner is shown on the world-map figurine and in the inventory header.
**Alternate / exception flows:**
- Empty name rejected.
- Profanity filter UNCONFIRMED — not required for vertical slice.
- Banner recolor is cosmetic only; no stat bonus.
**Observable result:** World-map party uses the chosen banner; pause menu shows the name.

### UC-ORIGIN-003 — Rebuilding company — default origin

**Tags:** `company | meta`
**Actor:** Player
**Precondition:** Default origin selected.
**Main flow:**
1. Start with a small handful of brothers (reference default: 3) with mixed low-tier gear.
2. No extreme special rules.
3. Tutorial-friendly contract density near spawn.
**Alternate / exception flows:**
- Exact starter backgrounds are data, not hardcoded UI.
**Observable result:** Roster has three named individuals, modest crowns, nearby village visible.

### UC-ORIGIN-004 — Fresh company origin — poorer start

**Tags:** `company | economy`
**Actor:** Player
**Precondition:** A 'new company' origin is selected.
**Main flow:**
1. Start with inexperienced hires and lower funds than default.
2. No unique combat rule.
**Alternate / exception flows:**
- UNCONFIRMED exact starter count vs default.
**Observable result:** Economy is tighter on day 1 than default origin on the same economic tier.

### UC-ORIGIN-005 — Peasant militia origin — larger cheap roster, lower equipment

**Tags:** `company | combat`
**Actor:** Player
**Precondition:** Militia origin selected.
**Main flow:**
1. Start with more bodies than default, peasant-tier weapons.
2. Special rule: can bring more than the standard 12 into battle (reference: militia can field extra, often up to 16). Treat max-field as data.
**Alternate / exception flows:**
- If clone drops extra-field, document the deviation.
- Wages still apply per head — larger roster burns food/gold.
**Observable result:** Battle deployment allows more than 12 slots for this origin only.

### UC-ORIGIN-006 — Deserter origin — hunted by a house

**Tags:** `company | overworld`
**Actor:** Player
**Precondition:** Deserter origin selected.
**Main flow:**
1. Start with a few well-armed deserters and low funds.
2. One northern house starts hostile and may send hunters.
3. Special rule: company always acts first in round 1 of combat (reference 'first to run').
**Alternate / exception flows:**
- Hostile house settlements refuse services until relations recover — or never, if design-locked as lasting hunt.
- UNCONFIRMED whether hostility can be fully cleared.
**Observable result:** Day-1 map shows a hostile house; first battle: player units occupy the top of the initiative bar in round 1.

### UC-ORIGIN-007 — Northern raiders origin — coastal/barbarian start

**Tags:** `company | overworld`
**Actor:** Player
**Precondition:** Raider origin selected.
**Main flow:**
1. Start in a northern/coastal biome with raider gear and rules that bias toward loot and hostility with civilians.
2. Some settlements may distrust the company.
**Alternate / exception flows:**
- UNCONFIRMED exact relation penalty and starting region.
**Observable result:** Spawn is northern; starting kit is raider-coded; some towns show colder relations.

### UC-ORIGIN-008 — Trading caravan origin — wagons, trade focus

**Tags:** `company | economy`
**Actor:** Player
**Precondition:** Caravan origin selected.
**Main flow:**
1. Start with extra stash space / donkeys-or-cart analogue and trade goods.
2. Combat roster may be weaker; income from trade is viable early.
**Alternate / exception flows:**
- UNCONFIRMED exact stash bonus.
**Observable result:** Stash capacity is higher than default; starting inventory includes trade goods.

### UC-ORIGIN-009 — Poacher origin — ranged/wildcraft start

**Tags:** `company | combat`
**Actor:** Player
**Precondition:** Poacher origin selected.
**Main flow:**
1. Start with hunters/poachers, bows, and a bonus to hunting-style content.
2. May have forest vision or beast-contract bias. UNCONFIRMED exact perks.
**Alternate / exception flows:**
- Night and forest fights are intended early tests.
**Observable result:** Starting roster is ranged-heavy with hunting weapons.

### UC-ORIGIN-010 — Beast hunter origin — trophy prices, worse trade goods

**Tags:** `company | economy`
**Actor:** Player
**Precondition:** Beast-hunter origin selected.
**Main flow:**
1. Start with hunters geared vs beasts.
2. Reference special: worse buy/sell on trade goods (~10%) but better trophy value.
**Alternate / exception flows:**
- Requires beast content enabled.
**Observable result:** Selling a pelt pays more than default origin; selling cloth pays less. Same town, same relations.

### UC-ORIGIN-011 — Manhunter origin — captives as extra fielded troops

**Tags:** `company | combat`
**Actor:** Player
**Precondition:** Manhunter origin selected (southern content).
**Main flow:**
1. Start with overseers plus indebted/captive fighters.
2. Can field more than 12 (reference 16).
3. Rule: if captives ≤ free men, company mood suffers (reference: equal or fewer indebted than non-indebted causes dissatisfaction — verify wording at lock).
**Alternate / exception flows:**
- Captives have reduced agency (perk refund timing differs — Student refund at 7 vs 11). UNCONFIRMED details.
- Do not use real-world slave-labour marketing copy; keep it grim-fantasy indenture and let PO legal-review.
**Observable result:** Deployment allows extra slots; mood UI warns when captive ratio rule is violated.

### UC-ORIGIN-012 — Arena gladiators origin — tiny elite start in the south

**Tags:** `company | combat`
**Actor:** Player
**Precondition:** Gladiator origin selected.
**Main flow:**
1. Start with very few high-skill fighters and southern gear.
2. Economy is brittle; a single death is campaign-threatening.
**Alternate / exception flows:**
- UNCONFIRMED exact count (often 3 elites).
**Observable result:** Roster is tiny and expensive-looking; spawn near a city-state with an arena building.

### UC-ORIGIN-013 — Lone champion origin — one fighter plus maybe a hound

**Tags:** `company | combat`
**Actor:** Player
**Precondition:** Lone-wolf origin selected.
**Main flow:**
1. Start with a single high-stat brother and funds to gear him.
2. Early fights must be pickable; company growth is a mid-game choice.
**Alternate / exception flows:**
- UNCONFIRMED whether dogs/hirelings start with him.
**Observable result:** Roster size is 1 on day 1.

### UC-ORIGIN-014 — Oathbound zealots origin — rotating vows

**Tags:** `company`
**Actor:** Player
**Precondition:** Oath origin selected.
**Main flow:**
1. Start with zealots.
2. Special: periodic oaths/vows constrain behaviour and grant bonuses when obeyed.
**Alternate / exception flows:**
- UNCONFIRMED oath list — implement as data.
- Breaking an oath has a visible penalty.
**Observable result:** A vow widget appears on the HUD with a timer and bonus/penalty.

### UC-ORIGIN-015 — Anatomist origin — corpse research

**Tags:** `company | combat`
**Actor:** Player
**Precondition:** Anatomist origin selected.
**Main flow:**
1. Start with scholars who gain extra value from enemy corpses/potions.
2. Events unique to this origin fire on the road.
**Alternate / exception flows:**
- Do not copy unique potion names from the reference IP; use generic 'mutagen draughts'.
**Observable result:** Post-battle loot UI offers research trophies not present in default origin.

### UC-ORIGIN-016 — Occult cult origin — boons and demands

**Tags:** `company`
**Actor:** Player
**Precondition:** Cult origin selected.
**Main flow:**
1. Start with poorly equipped cultists.
2. A patron occasionally demands a sacrifice and may grant a boon if loyal.
**Alternate / exception flows:**
- Do not brand the clone with unique cult deity names from the reference game.
- Refuse to implement real-world religious hate content; keep it fictional grimdark.
**Observable result:** A calendar event asks for a grim choice; refusing has a stated cost.

### UC-ORIGIN-017 — Southern mercenaries origin

**Tags:** `company | overworld`
**Actor:** Player
**Precondition:** Southern mercenary origin selected.
**Main flow:**
1. Spawn in the south with city-state appropriate gear and contacts.
**Alternate / exception flows:**
- Northern houses may treat the company as outsiders initially. UNCONFIRMED.
**Observable result:** Starting town is a city-state; gear art-set is southern.

### UC-ORIGIN-018 — Starting roster names, faces, backgrounds are generated

**Tags:** `company`
**Actor:** System
**Precondition:** Campaign starts.
**Main flow:**
1. Each starter brother has: display name, portrait, background, 1–3 traits, talent stars, equipment, daily wage.
2. Names come from culture tables matching origin/region.
**Alternate / exception flows:**
- Player cannot rename in vanilla reference — UNCONFIRMED; clone may allow rename as a QoL option flagged as deviation.
**Observable result:** Character sheet is fully filled; no 'Unit 1' placeholders.

### UC-ORIGIN-019 — Player does not create a custom avatar captain as a combat unit

**Tags:** `company | meta`
**Actor:** Player
**Precondition:** Company created.
**Main flow:**
1. The player is an off-map captain. There is no immortal hero avatar on the tactical map.
2. If the last brother dies, the campaign is lost (see UC-EDGE company wipe).
**Alternate / exception flows:**
- Lone-champion origin is still a mortal brother, not an avatar.
**Observable result:** Tactical map contains only rostered fighters, not a separate player character.

### UC-ORIGIN-020 — Banner ambition later uses the chosen standard

**Tags:** `company | economy`
**Actor:** Player
**Precondition:** Company has a banner choice stored.
**Main flow:**
1. When the 'commission a battle standard' ambition is taken (see events/ambitions), the item uses the chosen heraldry.
**Alternate / exception flows:**
- Until crafted, the world-map figurine still shows the banner art.
**Observable result:** Standard item in inventory matches creation-time colors.
---

### UC-CAMP-001 — Make and break camp

**Tags:** `overworld | company`
**Actor:** Player
**Precondition:** On world map, not inside a settlement, not in combat.
**Main flow:**
1. Player hits T or the camp button. Figurines pitch tents. Company stops walking.
2. Time passes faster than walking-in-place.
3. Break camp to move again.
**Alternate / exception flows:**
- Cannot camp on some tiles (deep water). UNCONFIRMED.
- Forced break if attacked.
**Observable result:** A camp icon appears; clock runs faster; movement commands cancel camp.

### UC-CAMP-002 — Repair while camped

**Tags:** `overworld | economy`
**Actor:** System
**Precondition:** Camped, tools > 0, damaged flagged items exist.
**Main flow:**
1. Repair rate doubles vs walking (reference).
2. Still 1 tool / 15 durability.
3. Blacksmith adds +33% speed and tool savings.
**Alternate / exception flows:**
- No tools: repair stalls, camp still heals/time-skips.
- Paid smith in town is faster at crown cost.
**Observable result:** A 60-durability dent closes in fewer hours than the same dent on the road.

### UC-CAMP-003 — Treat wounds while camped

**Tags:** `overworld | company`
**Actor:** System
**Precondition:** Camped, injuries present.
**Main flow:**
1. Injury timers and HP regen accelerate vs marching.
2. Medicine still required for temp injuries (1/injury/day equivalent, faster ticks).
3. Cook retinue +33% HP heal rate (attribute wiki).
**Alternate / exception flows:**
- No temple-quality cut of days unless a Surgeon follower exists (retinue, not a camp tent).
**Observable result:** An injury that would take 4 days on the road completes in ~2 camped, medicine allowing.

### UC-CAMP-004 — Scout / vision while camped

**Tags:** `overworld`
**Actor:** Player
**Precondition:** Camped.
**Main flow:**
1. Vision radius shrinks; company is easier for hostiles to spot.
2. Camping on hills/mountains mitigates this (dev blog: camp on high ground).
3. Lookout/Scout retinues still apply their bonuses to the reduced base.
**Alternate / exception flows:**
- Footprints still work.
- This is the vanilla 'scout' tradeoff — there is no assigned scout tent.
**Observable result:** A camp in a valley at night is jumped more easily than a camp on a hill at noon.

### UC-CAMP-005 — Hunt while camped

**Tags:** `overworld | economy`
**Actor:** Player
**Precondition:** Camped in wildlife terrain.
**Main flow:**
1. UNCONFIRMED in vanilla: no dedicated hunt-tent producing meat on a timer.
2. Closest vanilla: hunt **contracts**, beast fights, and random 'game in the bushes' events.
3. Clone optional: a simple camp action that spends hours for a chance of food/trophies, cancelled if attacked.
**Alternate / exception flows:**
- Do not ship Legends hunting-mode UI as if it were vanilla.
**Observable result:** If optional action is off, testers still get food via events/contracts only.

### UC-CAMP-006 — Gather while camped

**Tags:** `overworld | economy`
**Actor:** Player
**Precondition:** Camped near groves/swamps.
**Main flow:**
1. UNCONFIRMED vanilla gather tent.
2. Vanilla: buy medicine at groves' towns; events 'herbs along the way'.
3. Clone optional: hours-for-medicine mini-action.
**Alternate / exception flows:**
- Scout retinue prevents terrain sickness when marching, unrelated to gathering.
**Observable result:** Without the optional action, medicine does not magically accrue in camp.

### UC-CAMP-007 — Donate / alms as a camp or town action

**Tags:** `economy | company`
**Actor:** Player
**Precondition:** Player wants a mood/relation sink.
**Main flow:**
1. UNCONFIRMED as a camp button in vanilla.
2. Vanilla analogues: temple donations/treatment, beggar events, giving food in events.
3. Clone optional: donate crowns/food from camp menu for a small mood bump with a daily cap.
**Alternate / exception flows:**
- Must not become an infinite morale printer.
**Observable result:** If unimplemented, events still allow charitable choices.

### UC-CAMP-008 — Train in camp

**Tags:** `company`
**Actor:** Player
**Precondition:** Camped.
**Main flow:**
1. UNCONFIRMED vanilla camp XP.
2. Vanilla training is the **Training Hall** in towns (pay for +50% next fight / +35% next 3 / +20% next 5, price scales with level).
3. Drill Sergeant follower: more combat XP globally.
4. Clone optional: camp drill spending hours for tiny XP at a fatigue/mood cost.
**Alternate / exception flows:**
- Do not let camp training replace battles.
**Observable result:** Vanilla path: testers buy training in a town hall building, not by camping in a forest.

### UC-CAMP-009 — Camp is dangerous in hostile territory

**Tags:** `overworld | combat`
**Actor:** Player
**Precondition:** Camped near orc lands at night.
**Main flow:**
1. Roaming parties can engage the camp. Battle starts with possible unprepared deployment. UNCONFIRMED deployment penalty.
2. Player should break camp and move if a stack is incoming.
**Alternate / exception flows:**
- Pause-check before time-compressing is a UX requirement.
**Observable result:** A hostile token walking onto the camp hex starts combat; camp stance ends.

### UC-CAMP-010 — Time-skip until dawn as a reason to camp

**Tags:** `overworld`
**Actor:** Player
**Precondition:** It is night; player wants daylight shops or to avoid night battle.
**Main flow:**
1. Camping accelerates to morning without walking into ambushes as far.
2. Food and wages still tick for skipped time.
**Alternate / exception flows:**
- Cannot skip a contract deadline illegally — time still counts.
**Observable result:** After camp, HUD shows Morning; food and wage totals moved.

### UC-CAMP-011 — Escort-caravan repair bonus similar to camp

**Tags:** `overworld | economy`
**Actor:** System
**Precondition:** On an escort contract.
**Main flow:**
1. Repair rate doubled like camp (tools wiki).
2. Vision not necessarily camp-penalised because the party is moving.
**Alternate / exception flows:**
- Does not grant camp vision penalty.
**Observable result:** During a long escort, tools drain faster because repair is sped up.
---


## Catalog section introductions

These paragraphs sat above the use cases in the source catalog and are copied verbatim.

# 2. Company creation, banner, origin, starting roster

Origins are **campaign rulesets**: starting roster, funds, equipment, and lasting special rules. Flavour introductions must be original writing. Do not ship unique reference-IP character names as product branding.

# 8. Camp

Vanilla camp is a **stance on the world map** (hotkey T): time compression, faster repair/heal, worse scouting, more visibility to enemies. It is **not** the Legends-mod tent assignment board. Optional clone expansions are marked UNCONFIRMED.
