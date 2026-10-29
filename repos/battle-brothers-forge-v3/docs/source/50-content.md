# Settlements and inventory

Provenance: copied from `/home/jan/Sources/total-brothers/docs/battle-brothers-use-cases.md` SHA256 `0ccec5426118234ae18853aadaebf334de33eebbb097a323099b542f36e1ac87` (4418 lines, 330 `UC-*` ids, 77 `UNCONFIRMED` markers). This is a reusable player-loop catalog for a mercenary company, not a kingdom or Total War brief, and not a recovered numeric formula sheet. Authority and implementation are separate columns. Catalog confidence is not original-runtime confirmation. The local Battle Brothers install was not executed. Prototype screenshots from the old slice are rejected as original-game references.

Parity is not claimed for any row. Only `UC-COMBAT-065` has a separate static implementation note, and that note does not accept the catalog's 9 AP cost, perk gate, or full-turn claim.

Inspection frames linked here are official Steam store assets, analysis-only, not shipped game art. See [authentic reference](05-authentic-reference.md).

![](reference-images/steam-ss-2.jpg)

![](reference-images/steam-ss-7.jpg)

## Trace table

| ID | Title | Authority | Confidence | Implementation | Code | Tests | Next evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| UC-INV-001 | Company stash vs personal equipment | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-INV-002 | Durability on weapons, shields, armour | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-INV-003 | Tools, medicine, ammo as pooled supplies | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-INV-004 | Marketplace buy/sell | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-INV-005 | Specialist shops: weaponsmith, armorer, fletcher, alchemist | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-INV-006 | Trade goods arbitrage | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-INV-007 | Food stacks with spoil timers | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-INV-008 | Armor attachments | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-INV-009 | Taxidermist crafting | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-INV-010 | Famed / named items | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-INV-011 | Shop inventories restock over time | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-INV-012 | Cannot sell contract cargo / quest items | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-INV-013 | Formation editor lives in the inventory screen | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-INV-014 | Cart / wagon stash upgrades | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-INV-015 | Item comparison tooltips | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-SETTLE-001 | Settlement types | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-SETTLE-002 | Every settlement belongs to a faction | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-SETTLE-003 | Attached locations | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-SETTLE-004 | Buildings list | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-SETTLE-005 | Recruitment screen | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-SETTLE-006 | Tavern drinks and rumours | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-SETTLE-007 | Temple services | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-SETTLE-008 | Training hall packages | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-SETTLE-009 | Settlement situations | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-SETTLE-010 | Services closed to hostiles | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-SETTLE-011 | Prosperity and military grade gear | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-SETTLE-012 | Local events inside towns | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-SETTLE-013 | Docks building | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-SETTLE-014 | Kennel | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-SETTLE-015 | Retinue hiring is not a town building | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-INV-016 | Worth vs buy vs sell displayed | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-INV-017 | Ammo types share one pool | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-INV-018 | Wardog as accessory+unit | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-INV-019 | Repair-before-sell calculation as a player skill, not an auto | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-SETTLE-016 | Agent reveals distant boards | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-SETTLE-017 | Negotiator relation rates | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-SETTLE-018 | Recruiter extra names | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-SETTLE-019 | Bowyer / blacksmith / surgeon follower effects | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |

## Scenarios

Preconditions, main flows, alternate and exception flows, and observable results are copied. `UNCONFIRMED` markers are kept.

### UC-INV-001 — Company stash vs personal equipment

**Tags:** `economy | company | ui`
**Actor:** Player
**Precondition:** Inventory open (C/I).
**Main flow:**
1. Left/right: stash grid with a capacity; each brother has body, head, mainhand, offhand/shield, accessory, bag slots.
2. Drag and drop, or right-click equip.
3. Bags & Belts perk adds bag slots.
**Alternate / exception flows:**
- Stash full: cannot loot more; shops can still sell if paying and space — block if no space.
- Quartermaster / cart upgrades increase stash (retinue).
**Observable result:** Moving a helm from stash onto a brother updates both grids and fatigue penalty live.

### UC-INV-002 — Durability on weapons, shields, armour

**Tags:** `economy | combat`
**Actor:** System
**Precondition:** Items used in battle.
**Main flow:**
1. Combat reduces durability. At 0, item is broken: no armour value / weapon unusable until repaired.
2. Repair consumes tools (1 tool / 15 durability) or paid smith.
**Alternate / exception flows:**
- Famed items still wear but are worth repairing first.
- Alt+right-click marks stash items for repair (reference). Equipped/bags auto-repair.
**Observable result:** A battered shield shows a worn icon; after a day with tools it is healthier.

### UC-INV-003 — Tools, medicine, ammo as pooled supplies

**Tags:** `economy`
**Actor:** Player
**Precondition:** Stash.
**Main flow:**
1. Three pooled resources with max caps from economic difficulty (+Quartermaster +50 tools & +50 meds; Beginner extra caps).
2. Shops restock them. Ammo fuels bows/xbows/throwing/guns.
**Alternate / exception flows:**
- Cannot exceed cap; buying extra is blocked.
- Scavenger refunds some ammo/tools after fights.
**Observable result:** HUD shows tools/meds/ammo/food/crowns always.

### UC-INV-004 — Marketplace buy/sell

**Tags:** `economy`
**Actor:** Player
**Precondition:** Settlement market, not night (market may still be open — lock: marketplace open daytime; tavern always).
**Main flow:**
1. Prices use settlement buy and sell modifiers from size, attached locations, relations, situations, economic difficulty, origin.
2. Trade goods often sell above 'worth'; equipment sells at a low fraction (~15–18% in large towns — wiki examples).
3. Repair before selling if added value > tool cost.
**Alternate / exception flows:**
- Hostile relation: shop closed.
- Expert economy −10% sell prices.
**Observable result:** The same sword lists different crowns in a village vs a city, and after relations improve.

### UC-INV-005 — Specialist shops: weaponsmith, armorer, fletcher, alchemist

**Tags:** `economy`
**Actor:** Player
**Precondition:** Building present.
**Main flow:**
1. Higher-tier gear at ~25% premium vs market, full durability.
2. Sometimes a named/famed item.
3. Alt+click paid instant repair without using party tools; cost scales with item worth.
**Alternate / exception flows:**
- Not every town has every specialist.
- Night: closed.
**Observable result:** Alt-clicking a damaged named axe at a smith repairs it for crowns; tools unchanged.

### UC-INV-006 — Trade goods arbitrage

**Tags:** `economy | overworld`
**Actor:** Player
**Precondition:** Two settlements with different modifiers/situations.
**Main flow:**
1. Buy salt/amber/furs/spices cheap where produced (attached locations), sell where scarce.
2. Situations (well supplied, deserted, after siege) swing prices.
**Alternate / exception flows:**
- Beast-hunter origin worse at this.
- Caravan origin better stash for it.
**Observable result:** A tester can profit a loop without fighting if prices differ enough.

### UC-INV-007 — Food stacks with spoil timers

**Tags:** `economy`
**Actor:** Player
**Precondition:** Market or loot.
**Main flow:**
1. Each provision type has worth and spoil days (grain ~7, higher foods longer).
2. UI sort by spoil date.
3. Diversity can trigger good/bad food events.
**Alternate / exception flows:**
- Cook +3 days.
- Always eat soonest expiry.
**Observable result:** Two stacks of bread with different ages decrement the older first.

### UC-INV-008 — Armor attachments

**Tags:** `economy | combat`
**Actor:** Player
**Precondition:** Body armour in stash, attachment item available.
**Main flow:**
1. Player attaches at inventory or taxidermist.
2. Effects: extra durability, less fatigue, first-hit bone plating, direwolf fear aura, acid immunity cloak, etc.
3. Helmets cannot take these attachments (reference).
4. Replacing a pre-attached piece destroys the old one.
**Alternate / exception flows:**
- Crafting attachments at taxidermist consumes trophies + crowns.
**Observable result:** A wolf-pelt mantle on a brother applies −5 resolve to enemies in melee.

### UC-INV-009 — Taxidermist crafting

**Tags:** `economy`
**Actor:** Player
**Precondition:** Town with taxidermist, trophies in stash.
**Main flow:**
1. Recipe list: trophies + crowns → cloaks, potions, attachments, accessories.
2. Original recipe names; do not copy unique IP artefact titles.
**Alternate / exception flows:**
- Missing ingredients grey out recipes.
**Observable result:** Spending a pelt and crowns produces the attachment item in stash.

### UC-INV-010 — Famed / named items

**Tags:** `economy | combat`
**Actor:** System
**Precondition:** Champion loot, distant large camp, specialist shop, rumour.
**Main flow:**
1. Named items have rolled bonuses (e.g. armour durability ×1.1–1.25 and better fatigue on named armours).
2. Unique legendary artefacts are rarer still, often from unique sites.
3. Ambition: own at least one famed piece.
**Alternate / exception flows:**
- Same seed reproduces which locations hold named items (reference).
- Clone must use original names for legendaries.
**Observable result:** A named sword has a distinct name string and better stats than the base type.

### UC-INV-011 — Shop inventories restock over time

**Tags:** `economy | overworld`
**Actor:** System
**Precondition:** Player leaves and returns after days.
**Main flow:**
1. Stock rerolls on a timer. Larger/richer towns more and better.
2. Attached locations bias goods (grove → medicine, smelter → tools, kennel → dogs).
**Alternate / exception flows:**
- Sitting in town with clock paused does not restock. Must pass world time.
**Observable result:** Waiting two days outside and re-entering shows different market rows.

### UC-INV-012 — Cannot sell contract cargo / quest items

**Tags:** `economy`
**Actor:** Player
**Precondition:** Tagged cargo in stash.
**Main flow:**
1. Sell action disabled with reason.
**Alternate / exception flows:**
- Dropping on the ground on world map not allowed.
**Observable result:** Trader UI greys the item.

### UC-INV-013 — Formation editor lives in the inventory screen

**Tags:** `economy | combat | ui`
**Actor:** Player
**Precondition:** Inventory open on world map.
**Main flow:**
1. Drag portraits to formation/reserve as in UC-COMBAT-025.
2. Tab/D next brother, A previous (reference).
**Alternate / exception flows:**
- In combat, inventory is limited to the active unit + ground.
**Observable result:** Changing formation on the world map changes the next battle’s opening line.

### UC-INV-014 — Cart / wagon stash upgrades

**Tags:** `economy | company`
**Actor:** Player
**Precondition:** Retinue screen (DLC).
**Main flow:**
1. Upgrading the cart costs crowns and increases stash slots.
2. Visible on the company figurine optional.
**Alternate / exception flows:**
- Without DLC, a smaller fixed stash.
**Observable result:** After upgrade, stash grid has more cells.

### UC-INV-015 — Item comparison tooltips

**Tags:** `economy | ui`
**Actor:** Player
**Precondition:** Hovering an item while a brother is selected.
**Main flow:**
1. Diff vs currently equipped: damage, armour, fatigue, durability, skills.
**Alternate / exception flows:**
- Red/green deltas.
**Observable result:** Hovering a heavier mail shows −fatigue and +armour vs the current gambeson.
---

### UC-SETTLE-001 — Settlement types

**Tags:** `overworld | economy`
**Actor:** Player
**Precondition:** World generated.
**Main flow:**
1. Civilian: hamlet / village / town / city. Military: fortified manor / castle / stronghold / citadel.
2. Southern: city-states with bazaars and arenas.
3. Size drives prices, stock, recruit quality.
**Alternate / exception flows:**
- Typical northern map mix ~2 cities, 3 towns, 6 villages, 2 strongholds, 2 castles, 2 manors (reference typical) — generation target.
**Observable result:** Icons differ; a castle has worse civilian contracts but better armour.

### UC-SETTLE-002 — Every settlement belongs to a faction

**Tags:** `overworld | economy`
**Actor:** System
**Precondition:** Post-1.1 design: no unaligned towns.
**Main flow:**
1. Each town is a fief of a house or a city-state.
2. Capturing during civil war changes the owner and supply.
**Alternate / exception flows:**
- Independent EA towns are **not** to be reintroduced unless PO asks.
**Observable result:** Relations screen groups towns under their house.

### UC-SETTLE-003 — Attached locations

**Tags:** `overworld | economy`
**Actor:** System
**Precondition:** Town has 0–N attached sites (watchtower, barracks, grove, orchard, mill, mine, kennel, etc.).
**Main flow:**
1. They bias market goods, prices (more attachments → higher prices), and recruit backgrounds (barracks → soldiers, grove → herbalists).
**Alternate / exception flows:**
- Destroyed in crises: town loses the bonus until rebuilt or forever.
**Observable result:** A town with a kennel sells dogs; a town with a grove sells more medicine.

### UC-SETTLE-004 — Buildings list

**Tags:** `economy | ui`
**Actor:** Player
**Precondition:** Entered town daytime.
**Main flow:**
1. Possible buildings: Marketplace, Tavern, Barber, Temple, Training Hall, Weaponsmith, Armorer, Fletcher, Alchemist, Taxidermist, Kennel, Arena (south), Docks, Recruits, Occultist/cult shop if any — data per town.
2. Clicking opens that UI.
**Alternate / exception flows:**
- Night: only tavern (reference). No hiring at night.
- Missing building = not shown, not greyed-for-sale.
**Observable result:** A village might only have market+tavern+recruits; a city has a full row.

### UC-SETTLE-005 — Recruitment screen

**Tags:** `company | economy`
**Actor:** Player
**Precondition:** Recruits building, daytime, roster < cap, not hostile.
**Main flow:**
1. A small list of generated hopefuls with background, wage, hire cost (background + 1.25× gear worth + level formula 500×(level−1)^1.5), traits visible, stars visible.
2. Player hires for crowns or leaves.
3. Recruiter retinue: 2–4 extra names on the list.
**Alternate / exception flows:**
- List rerolls over days; hiring cheap bodies can cycle the pool (community tactic).
- Some backgrounds only appear with military attachments or during crises (crusader-types) and may leave after the crisis in vanilla.
**Observable result:** Hiring deducts crowns, adds a brother with his gear, refreshes the slot.

### UC-SETTLE-006 — Tavern drinks and rumours

**Tags:** `company | economy`
**Actor:** Player
**Precondition:** Tavern.
**Main flow:**
1. Buy a round: mood up, chance of drunk (combat penalty + hangover).
2. Price scales with roster size, relations, situations.
3. Pay patrons for rumours up to 4 times per visit (reference).
4. Rumours: famed item direction, camps, crises, situations.
**Alternate / exception flows:**
- Minstrel: more useful rumours +15% renown from actions (retinue).
- Drunk brothers should be reservable.
**Observable result:** After four rumour buys, the button disables until next visit/day.

### UC-SETTLE-007 — Temple services

**Tags:** `company | economy`
**Actor:** Player
**Precondition:** Temple, injured brothers, daytime.
**Main flow:**
1. Pay to accelerate temp injury healing; flavour prayer.
2. No resurrection.
3. Reduces gangrene-like risks vs untreated field wounds (dev blog).
**Alternate / exception flows:**
- Cannot remove permanent injuries.
**Observable result:** Paying halves remaining days on a selected injury.

### UC-SETTLE-008 — Training hall packages

**Tags:** `company | economy`
**Actor:** Player
**Precondition:** Training hall, daytime.
**Main flow:**
1. Three packages: sparring +50% XP next battle; veteran lessons +35% next 3; schooling +20% next 5.
2. Price scales with trainee level (example: 253 vs 1106 crowns L1 vs L10).
**Alternate / exception flows:**
- Buff icon on the brother.
- Does not grant levels in the building itself.
**Observable result:** After buying sparring, the next fight’s XP is 1.5× for that brother.

### UC-SETTLE-009 — Settlement situations

**Tags:** `economy | overworld`
**Actor:** System
**Precondition:** World events / caravans / raids.
**Main flow:**
1. Situations: well supplied, starving, terrified, recovering, after-fire, seasonal, etc.
2. They modify prices, recruit pool, and available contracts.
3. Displayed as icons on the town tooltip even from the world map if Agent retinue is hired (reveals contracts & situations at any distance).
**Alternate / exception flows:**
- Crisis occupations replace or stack.
**Observable result:** A 'well supplied' town is cheaper for food; a 'terrified' town pays more for protection jobs.

### UC-SETTLE-010 — Services closed to hostiles

**Tags:** `economy`
**Actor:** Player
**Precondition:** House relation hostile.
**Main flow:**
1. Cannot enter shops or take contracts. Guards may attack on the map near the town.
**Alternate / exception flows:**
- Tavern rumour via Agent still shows from outside if implemented.
- Healing at their temple denied.
**Observable result:** Clicking the town as a hostile shows a refusal or attack, not the market.

### UC-SETTLE-011 — Prosperity and military grade gear

**Tags:** `economy`
**Actor:** Player
**Precondition:** Comparing a hamlet and a stronghold.
**Main flow:**
1. Strongholds: military gear, expensive.
2. Cities: trade goods, more named-item shop chance.
3. Villages: cheap supplies, poor armour.
**Alternate / exception flows:**
- Attached mines/workshops change that village’s tools supply.
**Observable result:** A full mail is found in the castle armorer, not the hamlet stall.

### UC-SETTLE-012 — Local events inside towns

**Tags:** `company`
**Actor:** System
**Precondition:** On entering or while visiting.
**Main flow:**
1. Town events: brawls, conscription, festivals, thieves.
2. Distinct from road events.
**Alternate / exception flows:**
- Clock is paused so events cannot be 'waited out' inside without leaving.
**Observable result:** Entering can pop a dialog before the building list.

### UC-SETTLE-013 — Docks building

**Tags:** `overworld | economy`
**Actor:** Player
**Precondition:** Coastal settlement.
**Main flow:**
1. Opens sea-travel UI (UC-WORLD-018).
**Alternate / exception flows:**
- Inland towns omit it.
**Observable result:** A port city shows Docks; a mountain castle does not.

### UC-SETTLE-014 — Kennel

**Tags:** `economy | combat`
**Actor:** Player
**Precondition:** Kennel present.
**Main flow:**
1. Buy wardogs and dog armour.
**Alternate / exception flows:**
- No kennel: dogs only via loot/events.
**Observable result:** A dog item appears in stash after purchase.

### UC-SETTLE-015 — Retinue hiring is not a town building

**Tags:** `company | economy`
**Actor:** Player
**Precondition:** Enough renown; unlock conditions met.
**Main flow:**
1. Player opens the company campfire/retinue screen on the world map (or inventory).
2. Five follower slots (reference). Hire for a one-time cost, replace for a cost, no daily wage.
3. Examples: Lookout, Blacksmith, Scout, Scavenger, Cook, Surgeon, Negotiator, Paymaster, Quartermaster, Recruiter, Drill Sergeant, Agent, Minstrel, Bowyer, etc.
**Alternate / exception flows:**
- Unlock examples: Drill Sergeant requires retiring a permanently injured brother; Scout requires winning 5 beast fights; Lookout discover 10 locations — treat as data.
- Cart upgrade sits on the same screen.
**Observable result:** Hiring a Scout immediately increases world-map speed 15% and prevents terrain-sickness injuries.
---

### UC-INV-016 — Worth vs buy vs sell displayed

**Tags:** `economy | ui`
**Actor:** Player
**Precondition:** Tooltip on an item in a shop.
**Main flow:**
1. Show price the player pays/receives in this town now, not only abstract worth.
2. Debug/QA may show worth.
**Alternate / exception flows:**
- Modifiers from relations visible as a % line.
**Observable result:** A sword tooltips 'buy 420 (town +20%)' not just 'worth 350'.

### UC-INV-017 — Ammo types share one pool

**Tags:** `economy | combat`
**Actor:** Player
**Precondition:** Bows, crossbows, throwing, guns in the company.
**Main flow:**
1. Vanilla: a single ammo number fuels all of these (wiki supplies).
2. Guns may still use the pool — lock.
3. Empty pool: ranged skills disabled.
**Alternate / exception flows:**
- Clone deviation: split ammo types — mark as deviation if done.
**Observable result:** Firing a bow and a crossbow both decrement the same HUD ammo count.

### UC-INV-018 — Wardog as accessory+unit

**Tags:** `economy | combat`
**Actor:** Player
**Precondition:** Dog in inventory assigned to a brother.
**Main flow:**
1. Unleash in battle as a separate actor.
2. If the handler dies, dog may still fight this battle. UNCONFIRMED.
3. Dog armour from kennel/attachments.
**Alternate / exception flows:**
- Unassigned dogs do nothing in combat.
**Observable result:** Assigning a dog to a handler adds a dog portrait next battle.

### UC-INV-019 — Repair-before-sell calculation as a player skill, not an auto

**Tags:** `economy`
**Actor:** Player
**Precondition:** Damaged loot, tools available.
**Main flow:**
1. Game does not auto-repair for sale. Player decides.
2. UI may show 'repaired sell price' as a tooltip help — optional QoL.
**Alternate / exception flows:**
- Do not spend the last tools needed for the next fight without a confirm if QoL warning is on.
**Observable result:** Selling a 50% sword yields less than repairing then selling, matching the price formula.

### UC-SETTLE-016 — Agent reveals distant boards

**Tags:** `economy | overworld`
**Actor:** Player
**Precondition:** Agent follower hired.
**Main flow:**
1. Hovering any known settlement on the world map shows its contracts and situations without entering.
**Alternate / exception flows:**
- Unknown fogged towns still hidden.
**Observable result:** A far city tooltip lists two contracts while the company is in another biome.

### UC-SETTLE-017 — Negotiator relation rates

**Tags:** `economy`
**Actor:** Player
**Precondition:** Negotiator hired.
**Main flow:**
1. More haggle rounds, no relation hit on haggle, good relations decay slower, bad recover faster (15%).
**Alternate / exception flows:**
- Does not open hostile shops.
**Observable result:** A −20 relation recovers visibly faster over the same idle days than without him.

### UC-SETTLE-018 — Recruiter extra names

**Tags:** `company | economy`
**Actor:** Player
**Precondition:** Recruiter hired.
**Main flow:**
1. Each town list has 2–4 extra recruits (community/retinue description).
**Alternate / exception flows:**
- Quality still random.
**Observable result:** A village that showed 3 names now shows 5–7.

### UC-SETTLE-019 — Bowyer / blacksmith / surgeon follower effects

**Tags:** `economy | company`
**Actor:** Player
**Precondition:** Matching follower hired.
**Main flow:**
1. Bowyer: ammo-related world bonus. Blacksmith: repair 0-durability items, +speed, −tools. Surgeon: after-battle survive chance / arena safety (dev blogs).
2. Lock exact numbers to a retinue table.
**Alternate / exception flows:**
- Replacing a follower costs money; old effect ends immediately.
**Observable result:** With Blacksmith, a 0-durability named helm is repairable; without, it may be stuck. UNCONFIRMED 0-durability without blacksmith — wiki says blacksmith enables 0-durability repair.
