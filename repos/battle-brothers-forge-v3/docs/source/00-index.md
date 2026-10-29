# Mission index

Full mission: a faithful 1:1 native Rust Battle Brothers on Linux. This tree is a prepared bootstrap of the representative hex slice plus source catalogs. It does not satisfy that mission. A polished encounter is only an intermediate artifact. Nothing here lowers the mission to the old toy PASS.

Authority for import: independent review `import-plan-review-v2.md` VERDICT PASS for bootstrap only (2026-10-23T16:15:20+02:00). Historical review `import-plan-review.md` stays BLOCK. Controller is not started.

How to read confidence versus implementation: a catalog row can be stated, UNCONFIRMED, or only partly tied to static evidence. That is not the same column as code that exists today. Parity with the original game is not claimed.

## Map

| Document | What it is |
| --- | --- |
| [05-authentic-reference.md](05-authentic-reference.md) | Official store frames, hashes, links |
| [10-tactical.md](10-tactical.md) | `UC-COMBAT-*` scenarios |
| [20-campaign.md](20-campaign.md) | Meta, world, contracts, events, save |
| [30-company.md](30-company.md) | Origins and camp |
| [40-roster.md](40-roster.md) | Brothers |
| [50-content.md](50-content.md) | Settlements and inventory |
| [60-ui.md](60-ui.md) | UI and audio-visual intent |
| [80-catalog-scope.md](80-catalog-scope.md) | Catalog slice-versus-full excerpt, not acceptance |
| [90-edges.md](90-edges.md) | Edge cases |
| [fidelity.md](fidelity.md) | Authority and confidence against code, tests, and next evidence |
| [reference-images/](reference-images/) | Image bytes. Excluded from distributable game art |

Implemented state, ADRs, and cargo evidence are outside this source glob: `docs/forge/implemented/`, `docs/forge/adr/`, `docs/evidence/`.

## Catalog preamble, copied

Source file `/home/jan/Sources/total-brothers/docs/battle-brothers-use-cases.md`, SHA256 `0ccec5426118234ae18853aadaebf334de33eebbb097a323099b542f36e1ac87`. The preamble below keeps the catalog's own statement that this is a mercenary company, not a kingdom game, and its UNCONFIRMED rule. It is not a recovered formula sheet.

# Player Use-Case Catalog — Grim Mercenary Company Sim (Battle Brothers reference systems)

**Document type:** Design brief / testable player use cases  
**Audience:** Product Owner, Forge sprint, QA  
**Language:** English  
**Status:** Living catalog grounded in publicly documented gameplay of Overhype Studios' *Battle Brothers*. This is original design documentation for a Linux fan-clone. It does **not** copy assets, source, unique IP character names as product branding, or copyrighted prose.

## Product intent

This product is a **grim mercenary-company simulator**, not a grand-strategy kingdom game. The player is a captain of a small sellsword band: hire, feed, pay, and bury individuals; take contracts from settlements and rival houses; travel a procedurally generated continent in pauseable real time; and fight brutal hex-tactical battles where positioning, fatigue, morale, and attrition matter more than empire painting. There is no player-owned realm to develop. Victory is survival, reputation, and the option to retire with a story — not painting the map. This catalog documents the Battle Brothers–side loops the clone must reproduce as **player-facing behaviour**, described in original language.

Clone naming, banners, faction labels, and flavour copy must be original. Where the reference game uses unique named characters, cults, or artefact titles, this brief uses **generic archetypes** (e.g. “occult cult origin”, “southern city-states”, “northern houses”) rather than product branding.

## How to read this catalog

- Each item is a **use case** with Actor, Precondition, numbered Main flow, Alternate/exception flows, and an **Observable result** a tester can see without reading code.
- IDs are stable: `UC-<AREA>-NNN`.
- Traceability tags: `overworld` | `combat` | `company` | `economy` | `meta`.
- Mechanics taken from public wiki / official dev blogs are stated as design targets. If sources conflict, are incomplete, or look like a popular mod rather than vanilla, the case is marked **UNCONFIRMED**.
- Do **not** treat Legend-mod camp tents, OPR tabletop rules, or fan numerical patches as vanilla.
- Exact numeric constants (hit formulas, haggle annoyance, XP tables) are **design-lock candidates**. Implement behind data tables so PO can retune without recoding flows.

## Reference scope (vanilla + official DLC systems, not mods)

Public systems covered: new campaign / worldgen / difficulty / ironman; origins; overworld travel, time, weather, fog of war; contracts and negotiation; hex combat; brothers as individuals; inventory and shops; camping; settlements; events, ambitions, late crises; save/defeat/retirement; UI; audio-visual intent; edge cases.

Vanilla camping is **time compression + faster heal/repair + vision tradeoff**, not a tent-assignment minigame. Tent jobs (hunt/gather/train as camp stations) appear in popular mods; those are called out UNCONFIRMED for the reference game and optional for the clone.

## Conventions for testers

- **Company** = player party on the world map.
- **Brother** = a named individual in the roster (combatant).
- **Retinue follower** = non-combat specialist (DLC system).
- **House** = a northern noble faction; **city-state** = a southern urban faction.
- **Crowns** = currency.
- **Renown** = global business reputation (not the same as local relations).
- **Relations** = per-settlement / per-faction standing that moves prices and access.
- **Ironman** = single autosave, no manual save slots.
- **Permadeath** of brothers is default; there is **no resurrection** of dead roster members in the reference design.

---
