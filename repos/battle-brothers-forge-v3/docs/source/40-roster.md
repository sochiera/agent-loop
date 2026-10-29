# Roster

Provenance: copied from `/home/jan/Sources/total-brothers/docs/battle-brothers-use-cases.md` SHA256 `0ccec5426118234ae18853aadaebf334de33eebbb097a323099b542f36e1ac87` (4418 lines, 330 `UC-*` ids, 77 `UNCONFIRMED` markers). This is a reusable player-loop catalog for a mercenary company, not a kingdom or Total War brief, and not a recovered numeric formula sheet. Authority and implementation are separate columns. Catalog confidence is not original-runtime confirmation. The local Battle Brothers install was not executed. Prototype screenshots from the old slice are rejected as original-game references.

Parity is not claimed for any row. Only `UC-COMBAT-065` has a separate static implementation note, and that note does not accept the catalog's 9 AP cost, perk gate, or full-turn claim.

Inspection frames linked here are official Steam store assets, analysis-only, not shipped game art. See [authentic reference](05-authentic-reference.md).

![](reference-images/steam-ss-9.jpg)

## Trace table

| ID | Title | Authority | Confidence | Implementation | Code | Tests | Next evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| UC-BROTHER-001 | Inspect a brother’s character sheet | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-002 | Background determines ranges, gear, wage, events | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-003 | Traits — positive, negative, acquired | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-004 | Talent stars bias level-up rolls | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-005 | Level 1–11 perk points and the 7-row tree | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-006 | Perk identities the clone must support (vanilla set) | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-007 | Mood meter separate from combat morale | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-008 | Inter-brother relations / friendships / rivalries | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-009 | Titles earned | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-010 | Daily wage formula | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-011 | Dismiss a brother | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-012 | Desertion when unpaid or unfed | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-013 | Temporary injuries on the world map | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-014 | Permanent injuries | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-015 | Death is permanent | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-016 | Food consumption per brother | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-017 | Hire backlog: already-levelled recruits | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-018 | Roster cap 20, field cap 12 | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-019 | Barber / appearance | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-020 | Names generated from culture tables | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-021 | Reserve vs field XP and mood | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-022 | Level-up can happen mid-travel | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-023 | Drunk and hangover | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-024 | Acquired fear after a rout vs a faction | public catalog copy, not recovered bytecode | unconfirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-025 | Greedy wage and demand-pay events | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-026 | Bright vs Dumb XP | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-027 | Tiny / Huge / Fat / Athletic body traits | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |
| UC-BROTHER-028 | Obituary / fallen list (nice-to-have) | public catalog copy, not recovered bytecode | catalog-stated, not runtime-confirmed | absent | gap | gap | native reference comparison; original runtime was not executed |

## Scenarios

Preconditions, main flows, alternate and exception flows, and observable results are copied. `UNCONFIRMED` markers are kept.

### UC-BROTHER-001 — Inspect a brother’s character sheet

**Tags:** `company | ui`
**Actor:** Player
**Precondition:** Roster has at least one brother.
**Main flow:**
1. Sheet shows: name, title, portrait, background, traits, talent stars, level, XP, daily wage, mood, relations-to-other-bros if any, equipment, attributes (HP, fatigue, resolve, initiative, melee/ranged skill and defence, vision, AP).
**Alternate / exception flows:**
- Stars (1–3) on attributes mean talent: higher rolls on level-up for that stat.
**Observable result:** A tester can screenshot one sheet and verify every field is populated.

### UC-BROTHER-002 — Background determines ranges, gear, wage, events

**Tags:** `company`
**Actor:** System
**Precondition:** A recruit is generated.
**Main flow:**
1. Background sets base attribute ranges, likely traits, starting equipment, hire cost, daily wage base (about 3–35 crowns, swordmaster-tier exception ~35–400 hire).
2. Unlocks background-specific road events.
**Alternate / exception flows:**
- Military backgrounds may spawn already levelled (player then spends their perk/stat backlog on hire).
**Observable result:** A farmhand and a sellsword of equal level have visibly different wage and stat spreads.

### UC-BROTHER-003 — Traits — positive, negative, acquired

**Tags:** `company`
**Actor:** System
**Precondition:** Generation or events.
**Main flow:**
1. Each brother has 0–N traits (Brave, Fat, Tiny, Bright, Dumb, Greedy, Spartan, Gluttonous, Night Owl, etc.).
2. Traits modify stats, food use, wages, morale caps, event weights.
3. Fear/hate of a enemy type can be **acquired** after traumatic battles.
**Alternate / exception flows:**
- Some traits are mutually exclusive.
- Events can add Old, remove Ailing, etc.
**Observable result:** Trait icons on the sheet have tooltips with mechanical effects, not only flavour.

### UC-BROTHER-004 — Talent stars bias level-up rolls

**Tags:** `company`
**Actor:** Player
**Precondition:** Level-up screen.
**Main flow:**
1. Primary stats (melee skill, melee def) roll 1–4 depending on stars.
2. Secondary (ranged skill/def, HP, fatigue, resolve) 2–5.
3. Initiative 3–6.
4. Player picks **three** attributes to apply the rolled values to.
**Alternate / exception flows:**
- Veteran levels (>11): no perk, max +1 on three stats, XP cost 4000+1000×(lvl-11), cap 33 (reference after WotN DLC).
**Observable result:** A 3-star melee-skill brother shows higher offered numbers than a 0-star on the same level-up.

### UC-BROTHER-005 — Level 1–11 perk points and the 7-row tree

**Tags:** `company`
**Actor:** Player
**Precondition:** Brother levels up (XP thresholds 200/500/1000/2000/3500/5000/7000/9000/12000/15000).
**Main flow:**
1. Gain 1 perk point and a 3-stat roll.
2. Perk tree has 7 rows. Unlock next row after spending 1, then 2, … points (must invest in earlier rows to open later).
3. Picks are permanent.
**Alternate / exception flows:**
- Student: extra perk at 11 then inert.
- Gifted: extra stat roll as a perk.
- Cannot respec in vanilla — clone same unless PO adds a costly shrine deviation.
**Observable result:** A level 2 brother has exactly one perk chosen; row 7 is still locked.

### UC-BROTHER-006 — Perk identities the clone must support (vanilla set)

**Tags:** `company | combat`
**Actor:** Player
**Precondition:** Perk screen open.
**Main flow:**
1. Tier 1 examples: Fast Adaptation, Crippling Strikes, Colossus, Nine Lives, Bags & Belts, Pathfinder, Adrenaline, Recover, Student.
2. Later: weapon masteries, Shield Expert, Brawny, Rotation, Rally, Taunt, Dodge, Relentless, Anticipation, Backstabber, Underdog, Footwork, Nimble vs Battle Forged, Berserk, Head Hunter, Killing Frenzy, Fearsome, Indomitable, Lone Wolf, Reach Advantage, Quick Hands, Steel Brow, Fortified Mind, Executioner, Bullseye, Gifted.
3. Each perk has a tooltip matching its mechanical description (original wording OK).
**Alternate / exception flows:**
- Mod perk trees (Legends groups) are out of scope unless PO says otherwise.
**Observable result:** Choosing Nimble vs Battle Forged is possible by row 6; both can exist on different brothers.

### UC-BROTHER-007 — Mood meter separate from combat morale

**Tags:** `company`
**Actor:** System
**Precondition:** World map time passing.
**Main flow:**
1. Mood moves from events, food variety, unpaid wages, starvation, deaths, victories, tavern drinks, reserve-duty boredom, origin rules.
2. Good mood: chance to start battles Confident.
3. Bad mood: lower max morale and desertion risk.
**Alternate / exception flows:**
- Mood is not reset each fight.
- Paymaster blocks some 'demand more pay' events.
**Observable result:** After unpaid days, icons go sour without any combat.

### UC-BROTHER-008 — Inter-brother relations / friendships / rivalries

**Tags:** `company`
**Actor:** System
**Precondition:** Two brothers share the roster long enough or via events.
**Main flow:**
1. Events can create friends (mood link, save attempts) or rivals (brawls, injuries).
2. Death of a friend is a larger mood hit.
**Alternate / exception flows:**
- UNCONFIRMED how much of a hidden numeric matrix vanilla uses vs pure events.
- Clone minimum: event-driven flags, not a full social sim.
**Observable result:** An event can reference two named brothers; later, one leaving hits the other extra.

### UC-BROTHER-009 — Titles earned

**Tags:** `company`
**Actor:** System
**Precondition:** Kills / feats thresholds.
**Main flow:**
1. Display title updates (e.g. the Slayer). Purely cosmetic + some event weights. UNCONFIRMED mechanical bonuses.
**Alternate / exception flows:**
- Does not replace background.
**Observable result:** Sheet shows a title string under the name after enough kills.

### UC-BROTHER-010 — Daily wage formula

**Tags:** `company | economy`
**Actor:** System
**Precondition:** Brother exists.
**Main flow:**
1. Wage = background base × 0.9–1.1 random × level add (+2 per level is a simplified wiki line; another source uses a curve). Lock one formula.
2. Greedy +2 (or similar) immediately.
3. Veteran levels still increase pay.
**Alternate / exception flows:**
- Dismissing avoids future wages; hiring cost is separate.
- Indebted/captive origins may be unpaid or differently paid.
**Observable result:** Level-up increases the listed daily wage before the next noon tick.

### UC-BROTHER-011 — Dismiss a brother

**Tags:** `company | economy`
**Actor:** Player
**Precondition:** Roster > origin minimum (can dismiss even last? — if last, campaign ends).
**Main flow:**
1. Confirm. Brother leaves. On non-Expert he drops gear in stash; on Expert he keeps it (economic difficulty).
2. Mood of remaining may shift.
**Alternate / exception flows:**
- Cannot dismiss during combat.
- Some origin characters may be flagged undismissable. UNCONFIRMED.
**Observable result:** Roster count drops; stash may gain his kit.

### UC-BROTHER-012 — Desertion when unpaid or unfed

**Tags:** `company | economy`
**Actor:** System
**Precondition:** One or more days of no pay and/or no food; mood critical.
**Main flow:**
1. A brother deserts (event).
2. Expert: takes equipment. Lower difficulties: dumps gear in stash (dev blog).
3. Paymaster halves desertion chance.
**Alternate / exception flows:**
- Not all desert at once — staggered.
- Lone remaining deserter can end the campaign.
**Observable result:** An event names the deserter; he is gone; gear handling matches difficulty.

### UC-BROTHER-013 — Temporary injuries on the world map

**Tags:** `company`
**Actor:** Player
**Precondition:** Brother left combat with a temp injury.
**Main flow:**
1. Timer in days. Each day consumes 1 medicine per injury to progress.
2. Penalties apply in the next battles if fielded.
3. Temple: pay to cut remaining time (e.g. 2–4 days → 1–2).
4. Camp doubles recovery speed (with travelling ticks still happening).
**Alternate / exception flows:**
- No medicine: injury frozen.
- Light HP loss heals without medicine.
**Observable result:** A broken leg shows remaining days; paying at a temple shortens it.

### UC-BROTHER-014 — Permanent injuries

**Tags:** `company`
**Actor:** System
**Precondition:** A death-save, event, or grievous wound applies a permanent injury.
**Main flow:**
1. Lasting stat penalties (collapsed lung = fatigue, missing eye = vision, etc.).
2. Cannot be temple-healed in vanilla.
3. Retiring a permanently injured brother can unlock Drill Sergeant retinue (reference).
**Alternate / exception flows:**
- Some origins/events can mutate body in other ways (anatomist) — keep optional.
**Observable result:** The trait/injury stays on the sheet forever; stats are lower.

### UC-BROTHER-015 — Death is permanent

**Tags:** `company | combat`
**Actor:** System
**Precondition:** Brother died.
**Main flow:**
1. Removed from roster. Equipment on corpse lootable.
2. Company-wide mood hit. Morale shock already happened in battle.
3. No grave-rez, no temple-rez, no item-rez.
**Alternate / exception flows:**
- Nine Lives already consumed in that fight does not persist as extra life next fight except as the perk still existing.
- Obituary / fallen list is a nice-to-have.
**Observable result:** He cannot be rehired; the name is gone from formation.

### UC-BROTHER-016 — Food consumption per brother

**Tags:** `company | economy`
**Actor:** System
**Precondition:** Daily tick.
**Main flow:**
1. Default 2 units. Gluttonous more, Spartan less.
2. Terrain multiplier on the company applies after.
**Alternate / exception flows:**
- Zero food: mood then desertion, not instant HP death (reference: desert before starving to death).
**Observable result:** Adding a glutton increases daily food burn immediately.

### UC-BROTHER-017 — Hire backlog: already-levelled recruits

**Tags:** `company | economy`
**Actor:** Player
**Precondition:** A level 3 deserter is for hire.
**Main flow:**
1. On hire, player immediately assigns unspent stat rolls and perk points from those levels.
**Alternate / exception flows:**
- Cannot skip the assignment forever — force on hire or store as 'unspent' with a HUD badge. Vanilla: distribute after hire.
**Observable result:** Leaving the town without spending the points still keeps them unspent with a reminder.

### UC-BROTHER-018 — Roster cap 20, field cap 12

**Tags:** `company | combat`
**Actor:** Player
**Precondition:** Hiring.
**Main flow:**
1. Cannot hire above 20 (or origin override).
2. Cannot field above 12 (or origin override 16).
**Alternate / exception flows:**
- Retinue followers do not count as roster.
- Dogs do not count as roster.
**Observable result:** 21st hire is blocked; 13th formation slot does not exist for default origin.

### UC-BROTHER-019 — Barber / appearance

**Tags:** `company | ui`
**Actor:** Player
**Precondition:** Settlement with barber, daytime.
**Main flow:**
1. Player may change hair/beard/for a fee. Stats unchanged.
**Alternate / exception flows:**
- Unavailable at night.
**Observable result:** Portrait updates everywhere.

### UC-BROTHER-020 — Names generated from culture tables

**Tags:** `company`
**Actor:** System
**Precondition:** Recruit spawned.
**Main flow:**
1. Northern, southern, and other cultures use different name lists.
2. No unique IP NPC names reserved as default recruit names.
**Alternate / exception flows:**
- Duplicate names in one roster should be rare; allow a discriminator if they happen.
**Observable result:** A southern city recruit has a southern-style name.

### UC-BROTHER-021 — Reserve vs field XP and mood

**Tags:** `company | combat`
**Actor:** Player
**Precondition:** Brother in reserve during a win.
**Main flow:**
1. No combat XP. Mood may drop from sitting out unless Drill Sergeant.
2. Avoids death and injuries.
**Alternate / exception flows:**
- Events can still target reserves on the world map.
**Observable result:** XP bar unchanged; a mood tooltip mentions reserve duty.

### UC-BROTHER-022 — Level-up can happen mid-travel

**Tags:** `company | overworld`
**Actor:** Player
**Precondition:** XP crossed a threshold after battle or event.
**Main flow:**
1. Level-up UI can be done immediately or queued.
2. Unspent levels should be obvious (glow on portrait).
**Alternate / exception flows:**
- Entering combat with unspent points is allowed but suboptimal.
**Observable result:** A ding and a perk-available badge appear on the world map.
---

### UC-BROTHER-023 — Drunk and hangover

**Tags:** `company | combat`
**Actor:** Player
**Precondition:** Tavern round over-bought.
**Main flow:**
1. Drunk: combat penalties this day; then hangover next.
2. Mood was up when drinking.
**Alternate / exception flows:**
- Reserve drunks.
**Observable result:** A drunk brother has a status icon and worse hit % if fielded.

### UC-BROTHER-024 — Acquired fear after a rout vs a faction

**Tags:** `company | combat`
**Actor:** System
**Precondition:** Brother survived a horrific fight vs beasts/undead/greenskins.
**Main flow:**
1. Chance to gain Fear of X: morale checks worse against that type.
2. Hate of X is the aggressive counterpart from other events.
**Alternate / exception flows:**
- Can be later removed by rare events. UNCONFIRMED.
**Observable result:** Sheet shows a new fear trait after the battle summary.

### UC-BROTHER-025 — Greedy wage and demand-pay events

**Tags:** `company | economy`
**Actor:** System
**Precondition:** Greedy trait or high-level veteran.
**Main flow:**
1. Events demand a raise. Paymaster blocks these.
2. Refusing tanks mood / risk desertion.
**Alternate / exception flows:**
- Paying increases that brother’s wage permanently.
**Observable result:** A dialog names the greedy brother; after pay his listed wage is higher.

### UC-BROTHER-026 — Bright vs Dumb XP

**Tags:** `company`
**Actor:** System
**Precondition:** Two brothers, same fight.
**Main flow:**
1. Bright +10% XP, Dumb −15%.
**Alternate / exception flows:**
- Stacks with Student +20% until cap.
**Observable result:** After the same battle the Bright bar is visibly ahead of the Dumb bar.

### UC-BROTHER-027 — Tiny / Huge / Fat / Athletic body traits

**Tags:** `company | combat`
**Actor:** System
**Precondition:** Generated recruit.
**Main flow:**
1. Body traits modify HP, fatigue, initiative, and some item fit flavour.
2. They also change event weights (fat cares more about food variety).
**Alternate / exception flows:**
- Cannot surgically change body traits in vanilla.
**Observable result:** A Tiny brother has different HP/init than a Huge brother of the same background.

### UC-BROTHER-028 — Obituary / fallen list (nice-to-have)

**Tags:** `company | ui | meta`
**Actor:** Player
**Precondition:** At least one death.
**Main flow:**
1. A chronicle lists dead names, levels, cause. Not resurrection.
2. Optional for vertical slice; required for full catalog flavour.
**Alternate / exception flows:**
- Ironman still writes the list.
**Observable result:** Pause menu shows fallen brothers after a campaign week of blood.


## Catalog section introductions

These paragraphs sat above the use cases in the source catalog and are copied verbatim.

# 6. Brothers as individuals

There is **no resurrection**. Dead is dead. People have names, moods, and wages.
