# Catalog scope excerpt

Provenance: copied from `/home/jan/Sources/total-brothers/docs/battle-brothers-use-cases.md` SHA256 `0ccec5426118234ae18853aadaebf334de33eebbb097a323099b542f36e1ac87` (4418 lines, 330 `UC-*` ids, 77 `UNCONFIRMED` markers). This is a reusable player-loop catalog for a mercenary company, not a kingdom or Total War brief, and not a recovered numeric formula sheet. Authority and implementation are separate columns. Catalog confidence is not original-runtime confirmation. The local Battle Brothers install was not executed. Prototype screenshots from the old slice are rejected as original-game references.

Parity is not claimed for any row. Only `UC-COMBAT-065` has a separate static implementation note, and that note does not accept the catalog's 9 AP cost, perk gate, or full-turn claim.
The following section is copied verbatim from the catalog. It is planning language inside that catalog. It is not acceptance of this repository, and the checked-in Rust slice does not satisfy it.

# Must-have for vertical slice vs full catalog

The clone is a **mercenary company sim**. The vertical slice must prove that loop, not a kingdom layer.

## Vertical slice (smallest playable clone)

A tester can, in one sitting, without placeholders that crash:

1. **New campaign** with one origin (default rebuilding company), three-tier combat & economy difficulty, optional ironman, seed, banner, company name (`UC-META-001`–`005`, `UC-ORIGIN-001`–`003`, `UC-ORIGIN-018`–`019`).
2. **World map** pauseable travel, day/night, terrain speed, fog, settlements, roaming brigands, click-to-engage (`UC-WORLD-001`–`012`, `017`, `024`–`027`, `029`).
3. **One civilian contract loop**: browse, negotiate (at least accept / haggle-once / walk away), hunt or escort, success and fail, pay, relations, renown (`UC-CONTRACT-001`–`010`, `013`, `037`).
4. **Hex combat** with 9 AP, initiative bar, ZoC + opportunity attacks, surround, height, hit% tooltip, armour then HP, morale including Fleeing, player retreat to edge, loot (`UC-COMBAT-001`–`024`, `049`–`054`).
5. **Brothers**: names, backgrounds, traits, wages, food, mood, temp injuries, permadeath, level-up with 3 stats + 1 perk from a **reduced** perk tree (Colossus, Recover, Student, Shield Expert, Rotation, a weapon mastery, Nimble **or** Battle Forged) (`UC-BROTHER-001`–`016`, `UC-COMBAT-064`–`068`).
6. **Stash + marketplace** buy/sell, tools/meds/ammo/food, durability repair on the road (`UC-INV-001`–`007`).
7. **Camp stance** time compression, faster repair/heal, vision penalty (`UC-CAMP-001`–`004`, `009`–`010`).
8. **Settlement** market, tavern rumour stub, recruits, temple (`UC-SETTLE-001`–`008`).
9. **Save/load** (non-ironman slots + ironman single slot), company wipe game-over (`UC-SAVE-001`–`003`, `006`–`007`).
10. **UI** world HUD, tactical HUD, tooltips, pause, overlays (`UC-UI-001`–`012`, `014`).
11. **Edge**: starve, 0 gold, no contracts, night-closed shops, retreat with wounds (`UC-EDGE-001`–`003`, `006`, `008`).
12. **AV intent**: original placeholder art/audio buses, no ripped assets (`UC-AV-001`–`006`).

Out of slice (do not block slice sign-off): origins beyond default, retinue, sea travel, famed items, late crises, holy war, arena, taxidermist, ambitions beyond 1–2 stubs, full event bible, champion AI, location-fortification maps.

**Slice exit test:** start a Beginner non-ironman campaign → take a nearby contract → win a 6-v-6 brigand fight without ignoring ZoC → get paid in town → camp one night to repair → save/load → dismiss nobody → lose a brother permanently and keep playing.

## Full catalog (post-slice)

Ship in this order unless PO re-ranks:

| Wave | Content | Representative IDs |
| --- | --- | --- |
| F1 | Remaining origins + field-cap overrides | `UC-ORIGIN-005`–`017` |
| F2 | House contract gates, ambitions, standard | `UC-CONTRACT-007`, `UC-EVENT-003`–`005` |
| F3 | Location assaults, palisades, lure-garrison | `UC-WORLD-015`–`016`, `UC-COMBAT-001` |
| F4 | Full perk tree, veteran levels, dogs | `UC-BROTHER-005`–`006`, `UC-COMBAT-043`, `055`–`068` |
| F5 | Famed items, attachments, taxidermist | `UC-INV-008`–`010` |
| F6 | Retinue 5 slots + cart | `UC-SETTLE-015`–`019` |
| F7 | Sea travel, Agent distant boards | `UC-WORLD-018`, `UC-SETTLE-016` |
| F8 | Event pipeline + 50+ original events | `UC-EVENT-001`–`002`, `013` |
| F9 | Crises 1–4, destruction flag, holy sites | `UC-EVENT-006`–`011`, `UC-CONTRACT-017`–`020` |
| F10 | Retirement endings + score | `UC-SAVE-004`–`005`, `UC-EVENT-018` |
| F11 | Arena, southern shops, anatomist/cult origins | `UC-CONTRACT-021`, `UC-ORIGIN-012`+ |
| F12 | Accessibility, remappable keys, HiDPI Linux | `UC-UI-013`, `UC-AV-006` |

Mods (Legends tents, extra perk groups, OPR tabletop) are **not** in the full vanilla catalog.

## Open design locks (PO must tick)

1. Field-wipe with living reserves: continue vs game-over (`UC-EDGE-004`).
2. Exact hit-chance clamp (5–95%?).
3. Whether Wait exists in combat (`UC-COMBAT-069`).
4. Autosave trigger list for ironman (`UC-META-025`).
5. Sea-travel duration vs food/wages (`UC-WORLD-018`).
6. Omit manhunter/captive templates? (`UC-CONTRACT-033`).
7. Fog-clear vs location-scout extra radius (`UC-WORLD-008`).
8. Champion flee = lost famed item (`UC-EDGE-020`).
9. Numeric tables: XP, wages, haggle annoyance, renown deltas, terrain multipliers — already listed as data, not code.

## Traceability cheat-sheet

| Tag | Meaning |
| --- | --- |
| `overworld` | Pauseable map, travel, parties, fog, camp, towns as places |
| `combat` | Hex battle |
| `company` | Roster individuals, mood, injuries, origins |
| `economy` | Crowns, contracts, shops, supplies, relations, renown |
| `meta` | New game, save, difficulty, ironman, retirement, options |

---

# Sources (public systems, not a copy of any page)

Mechanics in this brief were cross-checked against public documentation. Clone prose and names must still be original. Wiki pages can be outdated vs the latest DLC patch — hence UNCONFIRMED flags.

- Overhype Studios developer blogs (tactical combat, stats, factions, contracts/negotiation, camping, retreat, desertion, worldmap fog, sea travel, late crises, retirement, retinue, location environments): `https://battlebrothersgame.com/`
- Battle Brothers community wiki (Combat Mechanics, Game Mechanics, Contracts, Origins, Attributes, Perks, Morale, Global Map, Settlements, Buildings, Keyboard Shortcuts, Late Game Crises, Ambitions, Retirement, Provisions, Tools, Medical Supplies, Armor Attachments, Named items, Retinue, Factions): `https://battlebrothers.fandom.com/`
- Steam store description for the high-level product loop (mercenary sandbox, not kingdom): `https://store.steampowered.com/app/365360/Battle_Brothers/`
- Community economy/renown guides (noble-contract gates ~800–1050 renown) — treat thresholds as UNCONFIRMED until data-mined in-house.
- **Explicitly not sources for vanilla:** Legendmod tent camp, One Page Rules "Battle Brothers" tabletop, Reforged perk groups.

If a future playtest of the reference game contradicts a number here, **playtest wins**, this catalog gets a lock update, and the use-case ID stays stable.

---
