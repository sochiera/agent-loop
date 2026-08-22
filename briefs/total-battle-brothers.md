# Total Battle Brothers

Build Battle Brothers with a kingdom.

The player is a medieval ruler: one company of named soldiers (Battle Brothers)
and a realm to grow (Medieval: Total War, especially the Stainless Steel mod).
Tactical fights are Wesnoth / Battle Brothers hex battles. The campaign map is
a large, travel-across-it world — Battle Brothers overworld or a Total War
province map, same idea.

Single-player sandbox. No story campaign. No magic, no fantasy. Harsh and
realistic. Every soldier matters; this is not a game of 60-man blobs.

## References — steal the feel, not the IP

- **Battle Brothers:** grim tone, permadeath, a small company you know by name,
  wounds, gear, a map you cross on foot.
- **Battle for Wesnoth:** turn-based hex fights, terrain, melee and ranged.
- **Medieval: Total War / Stainless Steel:** a big political map, several
  settlements, developing towns, rival realms, and the sense of ruling a
  kingdom rather than only a warband.

## Done when

A player on Linux starts the game and, by looking at the screen rather than
reading logs, can:

1. See a large campaign map and play a hex battle on it
2. Rule a realm: several settlement sizes, population, wheat, gold, buildings
3. Recruit, train, and equip units from population (resources + months)
4. March the hero's company, garrison towns, fight rivals and other threats
5. Lose the realm, or remain the last ruling power
6. Save and load

Real 2D art is required (CC0 is fine; richer art later is welcome). Not a
headless sim. Simple audio: UI open/close, battle hits and cries, ambient
music.

## World

- One human player. Several AI realms are allowed. Neutral holdings and
  bandits / robber bands are allowed. A tiny 1v1 map with nobody else on it
  is the wrong shape.
- Starting setup varies: a duchy may begin with one village or with several
  settlements of mixed size (village / town / city).
- The map is large. Crossing it takes months. There is room for more than one
  neighbour, empty land, and trouble that is not a rival king.
- Defeat: lose every settlement AND the hero, with no heir and no town left
  to raise one. Victory: you are the last ruling duchy.

## Strategy layer

- Campaign map in the Battle Brothers / Total War vein: settlements on a
  large world, parties move by movement points / turn cost. Contact with a
  hostile party or settlement starts a battle.
- 1 turn = 1 month. A year is 13 months of 4 weeks. Training and gear take
  months.
- Exactly one hero per duchy: king and commander. The army moves only with
  the hero. Units without the hero stay put or garrison a settlement.
- Company: the hero leads at most 12 units.
- If the hero dies, a designated heir takes over. Settlements and soldiers
  lose morale; the game continues.

## Settlements

- Two resources: wheat and gold.
- Population grows by births and immigrants.
- Population is a pool: recruiting units and staffing buildings. A smith must
  be a resident — too few people and the smithy cannot run.
- Closing a building returns 1 population to the pool.
- Develop what you hold, found new settlements, take someone else's.

## Units

Each warrior has rolled talents. Talents change how their stats grow — not a
flat bonus, but which gains they keep when they train or when they fight.

Training and experience raise different stats. Experience comes only from
combat. Training comes from time in the right buildings. Training gains are
strong early, then diminish. A gifted swordsman and a gifted scout do not
become the same person just because both drilled and both survived.

Gear is a short kit choice, not an inventory puzzle: light or heavy armour;
bow; shield and one-hander; or a two-hander. Better kits cost more and take
longer, with diminishing returns. The player can only pick what the realm
can actually supply — no smith, no heavy plate.

Death is permanent. A unit may be stunned instead and take a wound
(temporary or permanent).

## Battle

Turn-based hex grid. You control individual units.

- Terrain modifiers.
- Melee and ranged.
- Morale changes hit chance only. No routs.

## Out of scope

Not now (maybe later): scripted story campaign, multiplayer, map editor.

Never: mass units (no 60-man blobs), magic, fantasy.

## How to decide

Do not ask. Lock the roster, buildings, faction count, bandits, starting
layouts, map size, numbers, and UI so a full sandbox campaign is playable.
Choose any stack that runs on Linux. Keep game rules independent of
presentation so they can be tested without the UI.
