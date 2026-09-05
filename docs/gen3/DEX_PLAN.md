# DEX_PLAN - how to finish the Sapphire Pokédex

Source-verified capture plan, produced by four parallel research passes over
`pret/` (the pokeruby decompilation vendored at `pret/`). **Every claim here
carries a `path:line` citation** — this file exists because guessing cost this
project real hours, and because a walkthrough would have been wrong about at
least four of the items below.

Coordinates are in `pret/data/maps/<Map>/map.json` `x`/`y` space, which is the
same space the harness navigates in.

## Standing corrections to earlier assumptions

These were all believed, acted on, and are false. Read them first.

| Belief | Truth | Citation |
|---|---|---|
| HM08 DIVE is missing at `VictoryRoad_B2F (13,8)` | That ball is a **FULL HEAL**. HM08 is already in the bag. | `data/maps/VictoryRoad_B2F/map.json:63-78` → `data/item_ball_scripts.inc:533-535` |
| Evolution stones are sold at Lilycove Dept. Store 5F | **No stone is sold anywhere in the game.** 5F is a *decoration* shop: the opcode is `pokemartdecoration2`, so the `.2byte`s are DECOR_ ids that the disassembler renders with colliding `ITEM_` names. `ITEM_SUN_STONE`(93) is really `DECOR_BALTOY_DOLL`. | `include/macros/event.inc:995-999`; `src/scrcmd.c:1773-1779`; `include/constants/decorations.h:85-107` |
| Mirage Tower / Desert Underpass hold the second fossil | **Neither map exists in Sapphire** — both are Emerald additions. Grep for `MirageTower|Underpass` over `pret/` returns nothing. | grep over `pret/` |
| Both fossil lines are obtainable | **One only.** Taking either sets *both* hide flags and removes *both* objects in one script; no `clearflag` exists anywhere. | `data/maps/Route111/scripts.inc:57-59, 79-81` |
| A lone MARILL can breed AZURILL | **No.** Egg generation needs `numParents == 2`, and compatibility is 0 for same-gender. Ditto does not exist in Sapphire (zero entries in the encounter table). | `src/daycare.c:755, 862-911` |
| Vitamins / a massage NPC raise friendship | Vitamins are **dead code** (`FRIENDSHIP_EVENT_VITAMIN` is never passed to `AdjustFriendship`), and there is **no massage NPC in Sapphire**. | `include/pokemon.h:122`; grep `Massage` → 0 hits |
| Time of day / honey / ash affect encounters | **None exist in Gen 3.** `WildPokemonHeader` has exactly four tables: land, water, rockSmash, fishing. | `include/wild_encounter.h:17-25` |
| Pelipper can learn DIVE | Its learnset is **FLY + SURF only**. It does learn TM46 THIEF. | `src/data/pokemon/tmhm_learnsets.h:6839-6860` |

## What the save already holds

Confirmed by reading the bag (`poke_balls` is a separate pocket from `items` —
reading the wrong key is what hid this):

`HM01`-`HM08` (all eight), `DEVON SCOPE`, `OLD/GOOD/SUPER ROD`, `MACH BIKE`,
`GO-GOGGLES`, `BLUE ORB`, `ITEMFINDER`, `POKEBLOCK CASE`.

That is every gate for the plan below except money and balls.

## Evolutions of Pokémon already owned — 22 slots, no travel

Levels for boxed mons are derived from EXP (`Names.level_from_exp`, a
transcription of `GetLevelFromBoxMonExp`, `src/pokemon_1.c:1846-1852`) because
the box format has no level field.

**Four are already past their threshold and evolve on their very next
level-up:** BARBOACH L35→WHISCASH, LOUDRED L40→EXPLOUD, MARILL L25→AZUMARILL,
NATU L25→XATU.

Remaining level work totals **170 levels**: WURMPLE +4 (→SILCOON, then
BEAUTIFLY at 10), CASCOON +5, CORPHISH +5, SANDSHREW +8, SWABLU +10, GOLDEEN
+11, SHUPPET +11, MACHOP +13, GULPIN +13, SLAKOTH +13 (→VIGOROTH, then SLAKING
at 36), TENTACOOL +16, NUMEL +17, WAILMER +21, GRIMER +23.

Three need stones and one needs friendship — see below.

## Stones: where they actually are

| Stone | Where | Coords | Needs |
|---|---|---|---|
| **SUN STONE** — *exactly one in the game* | `MossdeepCity_SpaceCenter_1F`, talk to the SAILOR | (6,6) | nothing; only gate is `FLAG_RECEIVED_SUN_STONE_MOSSDEEP` |
| **LEAF STONE** | `Route119` item ball | (25,76) | nothing |
| **WATER STONE** | `Route124` BLUE SHARD ball → trade at `Route124_DivingTreasureHuntersHouse` | ball (31,53); house warp (70,48); trader (5,4) | SURF only |

Citations: `data/maps/MossdeepCity_SpaceCenter_1F/scripts.inc:31-43`;
`data/maps/Route119/map.json:275-284`; `data/maps/Route124/map.json:120-130`;
`data/maps/Route124_DivingTreasureHuntersHouse/scripts.inc:235-260`.

GLOOM needs **two** copies — one eats the Leaf Stone (→VILEPLUME), one the Sun
Stone (→BELLOSSOM) (`src/data/pokemon/evolution.h:33-34`). ODDISH is 40% of
`Route117` grass, the same map as the Day Care.

## CROBAT and AZURILL

- **CROBAT**: friendship ≥ 220, GOLBAT starts at 70 (`src/pokemon_3.c:296-300`;
  `src/data/pokemon/base_stats.h:1419`). **Level-ups are the only efficient
  lever**: +5/+3/+2 by tier, ×1.5 holding the SOOTHE BELL
  (`src/pokemon_3.c:652-661, 708-709`). Walking gives 1 point per ~256 steps
  and the bell does not amplify it. Soothe Bell:
  `SlateportCity_PokemonFanClub` (6,2), gated on the *lead* mon's friendship
  ≥150 — so lead a different, already-friendly mon to claim it.
- **AZURILL**: needs two opposite-gender MARILL. Second MARILL is `Route117`
  grass L13. Day Care deposit clerk `Route117_PokemonDayCare` (2,2); the egg is
  handed over by a *different* NPC **outside** at `Route117` (47,4). Deposit
  needs ≥2 valid party mons; the egg needs a free party slot; the roll fires
  every 256 steps (`src/daycare.c:755`; `data/scripts/day_care.inc:1-45, 81-129`).

## Fossils — pick ONE line, and mind the pocket

Both sit on open sand on `Route111`: ROOT at **(32,38)**, CLAW at **(33,38)**.
GO-GOGGLES gates the desert (a `checkitem`, not a flag).

> **Hazard:** `giveitem` sets `VAR_RESULT=FALSE` on a full pocket and the
> Route111 script **never checks it**, setting both hide flags anyway. A full
> KEY ITEMS pocket loses both fossils permanently.
> (`data/scripts/obtain_item.inc:1-15`; `data/maps/Route111/scripts.inc:55-58`)

Revive at `RustboroCity_DevonCorp_2F`, scientist at **(14,8)** → L20 mon.
Party must be ≤5. **Wait-skip:** talk to any of the four other scientists
((6,5), (1,5), (2,6), (10,5)) to flip the resurrection state 1→2 instantly
rather than leaving the map (`.../scripts.inc:14-60`). LILEEP/ANORITH evolve at
L40 (`evolution.h:182-183`).

## Species a grass sweeper will never catch

1. **FEEBAS** — 6 of 447 `Route119` water tiles, fishing only, and 50% per cast
   even on a right tile. **Fully derivable from the savestate**: read the u16 at
   `SaveBlock1+0x2DD6`, iterate `s = 12345 + 0x41C64E6D*s` taking `(s>>16) % 447`
   six times (0→447; 1/2/3 are rejected and redrawn), then enumerate Route119
   water tiles row-major with per-third bases `{0, 131, 298}`.
   (`src/wild_encounter.c:23, 75-140, 598-606`)
2. **KECLEON** — invisible objects, not encounters. With the DEVON SCOPE, walk
   onto `Route119` (31,6), (20,13) and `Route120` (20,11), (27,2), (4,77),
   (7,51), (19,48) and answer YES. **Skip Fortree (25,8)** — it flees; skip
   Route117/Lilycove/Sootopolis — decorations.
   (`data/scripts/static_pokemon.inc:31-156`)
3. **NOSEPASS** — Rock Smash, `GraniteCave_B2F`, 30% per rock, 7 rocks, and they
   use `FLAG_TEMP_*` so **re-entering the map respawns them** — infinitely
   farmable. (`src/data/wild_encounters.h:3290-3299`)
4. **GRAVELER** — Rock Smash, `VictoryRoad_B1F`, 70%.
5. **Super-Rod only** — HORSEA, STARYU (`LilycoveCity` water, its only wild slot
   in the game), CORSOLA, SHARPEDO, WHISCASH.
6. **DIVE only** — RELICANTH (5%), CLAMPERL (65%), CHINCHOU (30%) on
   `Underwater1`/`Underwater2`, entered by diving from `Route124`/`Route126`.
7. **Safari Zone only, 13 species** — PIKACHU, WOBBUFFET, GIRAFARIG, NATU, XATU,
   HERACROSS, PHANPY, RHYHORN, DODUO, DODRIO, PINSIR, PSYDUCK, GOLDUCK.
   ¥500 entry, **30 Safari Balls, 500 steps**, and the ball is hard-coded —
   no other ball can be used. The POKEBLOCK CASE is a hard entry gate.
   (`src/safari_zone.c:56-86`; `src/battle/battle_main.c:5571-5575`)
8. **CASTFORM** — an NPC gift, never wild: `Route119_WeatherInstitute_2F` (4,6).
9. **Zero wild slots anywhere — evolution only.** Do not spend an encounter on
   CRAWDAUNT, STARMIE, LANTURN, MILOTIC, HUNTAIL, GOREBYSS.
10. **1-2% grass, cap the attempts** — CHIMECHO (`MtPyre_Summit`), SKITTY
    (`Route116`), SURSKIT (four routes).

## Genuinely unreachable on this cartridge

Do not spend time here. 16 slots + 2 from the fossil choice = **18**, so the
ceiling for this save is **178**, not 180.

- **Version exclusive (7)**: SEEDOT, NUZLEAF, SHIFTRY, MAWILE, ZANGOOSE,
  SOLROCK, +1 — Ruby only, trade only.
- **Trade evolution (6)**: ALAKAZAM, GOLEM, MACHAMP, HUNTAIL, GOREBYSS, KINGDRA.
- **Event only (3)**: LATIOS, JIRACHI, DEOXYS.
- **Fossil choice (2)**: whichever of the LILEEP/ANORITH lines is not taken.
- **LATIAS** is roaming, which has no map — `gSaveBlock1.roamer` is relocated
  between routes (`src/wild_encounter.c:456-463`).
