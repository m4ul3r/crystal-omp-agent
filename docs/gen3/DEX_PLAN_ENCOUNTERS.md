# Hoenn Pokedex completion plan — encounters (Pokemon Sapphire)

Written for a future session with no memory of this run. Everything here is
derived from the vendored pokeruby decompilation at `pret/` and this repo's
own Python. Every data claim carries a `path:line`. Nothing was taken from a
walkthrough; the two places general knowledge is used are marked
**[UNVERIFIED]**.

Run state this was written against (reported, not re-measured): `saves/live-run.state`
— 6/8 badges, 22 species CAUGHT, 88 seen, 23 balls, at Lilycove.
Field moves held: CUT, FLY, SURF, STRENGTH, ROCK SMASH. **Not** held: DIVE, WATERFALL.
Rods held: OLD ROD, GOOD ROD. Pokeblock Case held.

---

## 1. Accessor surface — `pokeagent/dex.py`

Construct once against a live backend, then reuse; the dataset, the evolution
table and the encounter table are all read in `__init__` (`pokeagent/dex.py:1035-1073`).

```python
from pokeagent import dex
target = dex.DexTarget(emu, names, consts, mapdata, spec=driver.spec)
```

| Accessor | Returns | Defined |
|---|---|---|
| `DexTarget.entries` | all 202 regional-dex slots as `DexEntry` | `dex.py:1063` |
| `DexTarget.achievable` | `tuple[DexEntry, ...]` a solo player can get | `dex.py:1067`, built by `_partition` `dex.py:1097-1147` |
| `DexTarget.out_of_reach` | `tuple[OutOfReach, ...]` with `.reason` + `.detail` | `dex.py:1067` |
| `DexTarget.achievable_natdex` | `frozenset[int]` of national dex numbers | `dex.py:1068` |
| `DexTarget.out_of_reach_by_reason()` | `{reason: (OutOfReach, ...)}` | `dex.py:1240-1245` |
| `DexTarget.by_dex` / `.by_natdex` / `.by_species` | index dicts | `dex.py:1064-1066` |
| `DexTarget.dex_flags(state)` | `(caught, seen)` as frozensets of **national** dex numbers | `dex.py:1310-1338` |
| `DexTarget.progress(state)` | `{caught, seen, achievable, caught_achievable, percent, remaining}` | `dex.py:1340-1372` |
| `DexTarget.missing(state)` | achievable entries whose caught bit is clear | `dex.py:1374-1382` |
| `DexTarget.choice_locked(state)` | natdex numbers killed by the starter/fossil choice | `dex.py:1163-1225` |
| `DexTarget.plan(state)` | ordered `list[Step]`, cheapest route per missing species | `dex.py:1636-1690` |
| `DexTarget.sweep(state)` | `plan()` folded into `{group: [Step]}` | `dex.py:1692-1698` |
| `DexTarget.routes(species, owned)` | every route to one species, cheapest first | `dex.py:1618-1634` |
| `DexTarget.summary(state)` | one-line status string | `dex.py:1700-1707` |
| `DexTarget.starters` | `sStarterMons`, read from ROM | `dex.py:1384-1401` |
| `DexTarget.held_starter(owned)` | which starter this save took, `0` if none | `dex.py:1606-1616` |
| `DexTarget.owned_species(state)` | species ids in party **and** PC boxes | `dex.py:1424-1462` |
| `DexTarget.box_free_slots()` | empty PC slots (catching capacity) | `dex.py:1403-1422` |
| `DexTarget.event_only` | achievable-on-paper, distribution-only in practice | `dex.py:1247-1259` |
| `DexTarget.area_to_map(slug)` / `.unmapped_areas()` | dataset label -> decomp map names | `dex.py:1267-1288` |
| `DexTarget.warnings` | anything unresolved, loudly | `dex.py:1059` |

### The wild table — `DexTarget.wild` (`class WildTable`, `dex.py:534-655`)

| Accessor | Returns | Defined |
|---|---|---|
| `wild.for_map(map_name)` | `tuple[WildSlot, ...]` for one decomp map name | `dex.py:644-645` |
| `wild.for_species(species_id)` | `tuple[WildSlot, ...]` everywhere that species appears | `dex.py:641-642` |
| `wild.species` | `frozenset[int]` of every wild-obtainable species id | `dex.py:647-649` |
| `wild.slots` | every slot on every map | `dex.py:565` |
| `wild.unnamed_maps` | `(group, num)` pairs `nav` could not name | `dex.py:564` |

`WildSlot` fields (`dex.py:511-531`): `map_name`, `map_group`, `map_num`,
`kind`, `slot`, `species`, `min_level`, `max_level`, `slot_chance` (percent),
`encounter_rate` (the table's own step-roll rate).

`kind` is one of `land`, `water`, `rock_smash`, `dive`, `old_rod`, `good_rod`,
`super_rod` — `_slot_kind` (`dex.py:589-605`) splits `fishing` by rod and
relabels `water` on any `Underwater*` map as `dive`.

> **Caveat for the caller:** `WildSlot` has no `.method` and no `.chance`.
> `scripts/build_guide.py:170-176` reads `getattr(slot, "method")` and
> `getattr(slot, "chance")`, which is why every `method`/`chance` in
> `docs/guide/encounters.json` is `null`. Use `.kind` and `.slot_chance`.

### Other useful accessors

| Accessor | Returns | Defined |
|---|---|---|
| `target.evolutions.evolutions(sp)` / `.pre_evolutions(sp)` | forward/back edges of `gEvolutionTable` | `dex.py:389-395` |
| `target.evolutions.chain(sp)` / `.roots(sp)` | downstream / base forms | `dex.py:397-419` |
| `target.evolutions.describe(evo)` | `"use a MOON STONE on SKITTY"` | `dex.py:421-434` |
| `target.evolutions.natdex(sp)` / `.hoenn_dex(sp)` / `.species_of_natdex(n)` | dex-number translation | `dex.py:370-383` |
| `acquire.STATIC_NOTES` | the puzzle conditions no table records | `pokeagent/acquire.py:68-79` |
| `fishing.ROD_PREFERENCE` | `(SUPER, GOOD, OLD)` | `pokeagent/fishing.py:141` |

---

## 2. Slot-chance arithmetic

Read from the header the engine itself branches on
(`pret/src/data/wild_encounters.json:6-35`, expanded into
`ENCOUNTER_CHANCE_*` macros by `pret/src/data/wild_encounters.json.txt:5-21`;
parsed by `dex.py:657-704`). Rates sum to 100, so the raw rate **is** the
percent.

| Table | Slot -> % |
|---|---|
| `land_mons` (12) | 0:20 1:20 2:10 3:10 4:10 5:10 6:5 7:5 8:4 9:4 10:1 11:1 |
| `water_mons` (5) | 0:60 1:30 2:5 3:4 4:1 |
| `rock_smash_mons` (5) | 0:60 1:30 2:5 3:4 4:1 |
| `fishing_mons` OLD ROD (slots 0-1) | 0:70 1:30 |
| `fishing_mons` GOOD ROD (slots 2-4) | 2:60 3:20 4:20 |
| `fishing_mons` SUPER ROD (slots 5-9) | 5:40 6:40 7:15 8:4 9:1 |

Rod->slot grouping is in the data, not transcribed:
`wild_encounters.json:31-35` (`old_rod [0,1]`, `good_rod [2,3,4]`,
`super_rod [5,6,7,8,9]`), read back by `dex.py:686-693`.

`encounter_rate` (per table) is how often a step rolls at all: 20 on most
routes, 15 on Route119, 10 in caves and on most fishing tables, 4 on ocean
water and in Seafloor Cavern / Cave of Origin, 25 in the Safari Zone,
9 on Safari water, 30/35 on the better fishing tables, 1 in Petalburg City
and Sootopolis water.

---

## 3. Where the Sapphire tables live (citation anchors)

`gWildMonHeaders` carries **both** versions behind `#ifdef`
(`wild_encounters.json.txt:26-31`). The Sapphire half is
`pret/src/data/wild_encounters.json:9477-18918` — every entry whose
`base_label` ends `_Sapphire`. The Ruby half is lines `1-9476`. `WildTable`
reads the live ROM instead, which cannot pick the wrong branch (`dex.py:544-551`).

Line of each Sapphire `base_label`, for citing a single map:

| Map | Line | Map | Line | Map | Line |
|---|---|---|---|---|---|
| PetalburgCity | 9478 | SeafloorCavern_Room4 | 12058 | Route110 | 15254 |
| SlateportCity | 9567 | SeafloorCavern_Room5 | 12127 | Route111 | 15408 |
| LilycoveCity | 9656 | SeafloorCavern_Room6 | 12196 | Route112 | 15592 |
| MossdeepCity | 9745 | SeafloorCavern_Room7 | 12350 | Route113 | 15661 |
| SootopolisCity | 9834 | SeafloorCavern_Room8 | 12504 | Route114 | 15730 |
| EverGrandeCity | 9923 | CaveOfOrigin_Entrance | 12573 | Route115 | 15914 |
| MeteorFalls_1F_1R | 10012 | CaveOfOrigin_1F | 12642 | Route116 | 16068 |
| MeteorFalls_1F_2R | 10166 | CaveOfOrigin_B1F | 12711 | Route117 | 16137 |
| MeteorFalls_B1F_1R | 10320 | CaveOfOrigin_B2F | 12780 | Route118 | 16291 |
| MeteorFalls_B1F_2R | 10474 | CaveOfOrigin_B3F | 12849 | Route119 | 16445 |
| RusturfTunnel | 10628 | VictoryRoad_1F | 12918 | Route120 | 16599 |
| GraniteCave_1F | 10697 | VictoryRoad_B1F | 12987 | Route121 | 16753 |
| GraniteCave_B1F | 10766 | VictoryRoad_B2F | 13086 | Route122 | 16907 |
| GraniteCave_B2F | 10835 | ShoalCave_LowTideEntranceRoom | 13240 | Route123 | 16996 |
| GraniteCave_StevensRoom | 10934 | ShoalCave_LowTideInnerRoom | 13394 | Route124 | 17150 |
| PetalburgWoods | 11003 | ShoalCave_LowTideStairsRoom | 13548 | Route125 | 17239 |
| JaggedPass | 11072 | ShoalCave_LowTideLowerRoom | 13617 | Route126 | 17328 |
| FieryPath | 11141 | ShoalCave_LowTideIceRoom | 13686 | Route127 | 17417 |
| MtPyre_1F | 11210 | NewMauville_Entrance | 13755 | Route128 | 17506 |
| MtPyre_2F | 11279 | NewMauville_Inside | 13824 | Route129 | 17595 |
| MtPyre_3F | 11348 | AbandonedShip_Rooms_B1F | 13893 | Route130 | 17684 |
| MtPyre_4F | 11417 | AbandonedShip_HiddenFloorCorridors | 13982 | Route131 | 17838 |
| MtPyre_5F | 11486 | SkyPillar_1F | 14071 | Route132 | 17927 |
| MtPyre_6F | 11555 | SkyPillar_3F | 14140 | Route133 | 18016 |
| MtPyre_Exterior | 11624 | SkyPillar_5F | 14209 | Route134 | 18105 |
| MtPyre_Summit | 11693 | Route101 | 14278 | SafariZone_Northwest | 18194 |
| SeafloorCavern_Entrance | 11762 | Route102 | 14347 | SafariZone_Northeast | 18348 |
| SeafloorCavern_Room1 | 11851 | Route103 | 14501 | SafariZone_Southwest | 18447 |
| SeafloorCavern_Room2 | 11920 | Route104 | 14655 | SafariZone_Southeast | 18601 |
| SeafloorCavern_Room3 | 11989 | Route105 | 14809 | DewfordTown | 18670 |
| | | Route106 | 14898 | PacifidlogTown | 18759 |
| | | Route107 | 14987 | Underwater1 | 18848 |
| | | Route108 | 15076 | Underwater2 | 18882 |
| | | Route109 | 15165 | | |

97 maps carry a Sapphire table. `Underwater1` emerges to `Route124`
(`pret/data/maps/Underwater1/map.json:19-21`) and `Underwater2` to `Route126`
(`pret/data/maps/Underwater2/map.json:24-26`).

Dex-number sources: national numbers `pret/include/constants/species.h:452-864`,
Hoenn numbers `species.h:866-1281`.

---

## 4. Exclusions — what is dropped and why

### 4a. Ruby version exclusives (7)

Grounded by the ROM, not by a wiki: each species appears in the **Ruby** half
of `wild_encounters.json` and is absent from the Sapphire half, or is
`#ifdef`-swapped in `pret/constants/version.inc`.

| Hoenn# | Nat# | Species | Evidence |
|---|---|---|---|
| 22 | 273 | Seedot | Ruby half only, e.g. `wild_encounters.json:4936` (Route101), `:6304` (Route114); Sapphire Route101/114 carry WURMPLE/LOTAD instead (`:14278`, `:15730`) |
| 23 | 274 | Nuzleaf | Ruby half only, `wild_encounters.json:6329,6334`; the Sapphire counterpart slot is LOMBRE (`:15730` block) |
| 24 | 275 | Shiftry | no wild slot in either half; only reachable from Nuzleaf + LEAF STONE (`pret/src/data/pokemon/evolution.h:147`) |
| 69 | 303 | Mawile | Ruby half only, `wild_encounters.json:1375,1434,3226,3660,4640`; Sapphire's Granite Cave / Victory Road / Sky Pillar slots hold SABLEYE instead (`:10766`, `:12918`, `:14071`) |
| 123 | 335 | Zangoose | Ruby half only, `wild_encounters.json:6324,6339,6344,6349`; Sapphire Route114 has SEVIPER (`:15730`) |
| 126 | 338 | Solrock | Ruby half only, `wild_encounters.json:606,656,750,904,1058`; Sapphire Meteor Falls has LUNATONE (`:10012`) |
| 199 | 383 | Groudon | `pret/constants/version.inc:21-25` — `SPECIES_GROUDON_OR_KYOGRE` is `SPECIES_KYOGRE` under `SAPPHIRE`; the only use is the Cave of Origin static (`pret/data/maps/CaveOfOrigin_B4F/scripts.inc:57`) |

### 4b. Trade evolutions (6)

`EVO_TRADE` / `EVO_TRADE_ITEM` in the ROM's own `gEvolutionTable`. A second
player and a second cartridge are required, so these are dropped.
`dex.py:1112-1127` applies the same rule.

| Hoenn# | Nat# | Species | Evidence |
|---|---|---|---|
| 41 | 65 | Alakazam | `evolution.h:45` `[KADABRA] = {{EVO_TRADE, 0, ALAKAZAM}}` |
| 59 | 76 | Golem | `evolution.h:52` `[GRAVELER] = {{EVO_TRADE, 0, GOLEM}}` |
| 75 | 68 | Machamp | `evolution.h:48` `[MACHOKE] = {{EVO_TRADE, 0, MACHAMP}}` |
| 177 | 367 | Huntail | `evolution.h:190` `[CLAMPERL] = {{EVO_TRADE_ITEM, DEEP_SEA_TOOTH, HUNTAIL}, ...}` |
| 178 | 368 | Gorebyss | `evolution.h:191` `{EVO_TRADE_ITEM, DEEP_SEA_SCALE, GOREBYSS}` |
| 186 | 230 | Kingdra | `evolution.h:73` `[SEADRA] = {{EVO_TRADE_ITEM, DRAGON_SCALE, KINGDRA}}` |

**Milotic is NOT excluded.** External datasets call it a trade evolution
(that is the Gen 5+ Prism Scale rule); this cartridge's table says
`[FEEBAS] = {{EVO_BEAUTY, 170, MILOTIC}}` (`evolution.h:155`) — an entirely
in-cartridge evolution. `dex.py:1112-1127` keeps it for exactly this reason.

### 4c. Event / distribution only (3)

| Hoenn# | Nat# | Species | Evidence |
|---|---|---|---|
| 197 | 381 | Latios | `pret/constants/version.inc:27-31` — under `SAPPHIRE`, `SPECIES_LATIAS_OR_LATIOS` is `SPECIES_LATIOS`, and its only use is the Southern Island encounter (`pret/data/maps/SouthernIsland_Interior/scripts.inc:64`), which needs the Eon Ticket. The **roamer** in Sapphire is LATIAS (`pret/include/constants/species.h:1282-1286`), so Latios has no in-game source. `dex.py:1153-1161` encodes the same split. |
| 201 | 385 | Jirachi | no in-ROM source; the engine's own completion rating discounts it (`pret/src/birch_pc.c:94-102`, cited by `dex.py:1247-1259`) |
| 202 | 386 | Deoxys | same |

### 4d. Not an exclusion, but a hard fork: choice-locked lines

`DexTarget.choice_locked(state)` (`dex.py:1163-1225`) drops these once the
choice is made — they are as gone as a version exclusive.

* **Starter, 1 of 3** (`sStarterMons`, `pret/src/starter_choose.c:50`). Taking
  one kills the other two lines: **6 entries lost**.
* **Fossil, 1 of 2.** Taking either fossil hides BOTH objects:
  `pret/data/maps/Route111/scripts.inc:55-58` (Root Fossil sets
  `FLAG_HIDE_ROOT_FOSSIL` *and* `FLAG_HIDE_CLAW_FOSSIL`) and `:77-80` (Claw
  Fossil, same). pokeruby has no Desert Underpass. **2 entries lost.**

### 4e. Counting

```
202 regional-dex slots            (species.h:1146-1281, HOENN_DEX_* <= 202)
 -  7 Ruby exclusives
 -  6 trade evolutions
 -  3 event-only
= 186 achievable in Sapphire
 -  6 unchosen starter lines
 -  2 unchosen fossil line
= 178 obtainable in ONE save file
```

> The module docstring at `dex.py:10-31` says "188 of the 202 reachable
> entries" and "Seven Hoenn entries are Ruby exclusives, seven more only
> evolve on a link trade". That prose does not reconcile with its own
> arithmetic (202-7-7-2 = 186, and Latios is dropped on top of that). Trust
> `len(target.achievable)` at runtime, not the docstring. The exclusion list
> in 4a-4c above is derived from the ROM and is what this plan uses.

---

## 5. Per-species acquisition table

Sorted by Hoenn dex number (the objective's own ordering). `Nat#` is what
`dex_flags()` returns.

Notation: **L**=land, **W**=water/Surf, **RS**=Rock Smash, **DV**=Dive,
**OR/GR/SR**=Old/Good/Super Rod. `sN` = slot N; percentages from section 2.
All wild rows cite the Sapphire `base_label` anchor from section 3.
All evolution rows cite `pret/src/data/pokemon/evolution.h`.

| # | Nat | Species | How | Where (repo map constants) | Rate | Gate |
|---|---|---|---|---|---|---|
| 1 | 252 | Treecko | gift (starter, 1 of 3) | Route101 | — | choice-locked (`starter_choose.c:50`) |
| 2 | 253 | Grovyle | evolve Treecko L16 | party | — | `evolution.h:134` |
| 3 | 254 | Sceptile | evolve Grovyle L36 | party | — | `evolution.h:135` |
| 4 | 255 | Torchic | gift (starter) | Route101 | — | choice-locked |
| 5 | 256 | Combusken | evolve Torchic L16 | party | — | `evolution.h:136` |
| 6 | 257 | Blaziken | evolve Combusken L36 | party | — | `evolution.h:137` |
| 7 | 258 | Mudkip | gift (starter) | Route101 | — | choice-locked |
| 8 | 259 | Marshtomp | evolve Mudkip L16 | party | — | `evolution.h:138` |
| 9 | 260 | Swampert | evolve Marshtomp L36 | party | — | `evolution.h:139` |
| 10 | 261 | Poochyena | L | Route103 s4-7 (10/10/5/5); Route101 s8-11 (4/4/1/1); Route102 s6-8,10 | 20 | none |
| 11 | 262 | Mightyena | evolve Poochyena L18 | party | — | `evolution.h:140` |
| 12 | 263 | Zigzagoon | L | Route101/102/103/104 s0-5, PetalburgWoods, Route110/116/117/118/119/120/121/123 | 20 | none |
| 13 | 264 | Linoone | L Route119 s1 (20), Route120 s1 (20), Route121/123 s2 (10) | Route119 | 15/20 | none; or evolve Zigzagoon L20 (`evolution.h:141`) |
| 14 | 265 | Wurmple | L | Route101 s0 (20), Route102/104 s1 (20), PetalburgWoods s1 (20) | 20 | none |
| 15 | 266 | Silcoon | L PetalburgWoods s4 (10) | PetalburgWoods | 20 | or Wurmple L7, PID branch `evolution.h:142-143` |
| 16 | 267 | Beautifly | evolve Silcoon L10 | party | — | `evolution.h:144` |
| 17 | 268 | Cascoon | L PetalburgWoods s5 (10) | PetalburgWoods | 20 | or Wurmple L7 `evolution.h:143` |
| 18 | 269 | Dustox | evolve Cascoon L10 | party | — | `evolution.h:145` |
| 19 | 270 | Lotad | L | Route114 s1 (20), s4 (10); Route102 s4,s5 (10/10) | 20 | none |
| 20 | 271 | Lombre | L Route114 s6,s7 (5/5) | Route114 | 20 | or Lotad L14 `evolution.h:146` |
| 21 | 272 | Ludicolo | evolve Lombre + WATER STONE | party | — | stone: Lilycove Dept 5F (`LilycoveCity_DepartmentStore_5F/scripts.inc:26`) |
| 25 | 276 | Taillow | L | Route116 s5-7 (10/5/5); Route115 s1,s3,s4; Route104 s6,s7; PetalburgWoods s8,s10 | 20 | none |
| 26 | 277 | Swellow | L Route115 s5 (10) | Route115 | 20 | or Taillow L22 `evolution.h:150` |
| 27 | 278 | Wingull | L Route103/104/115/118/121/123 s8-11; W s1 (30), s2 (5) on every ocean map | 20/4 | none |
| 28 | 279 | Pelipper | W s3 (4), s4 (1) on ocean maps | any surf route | 4 | or Wingull L25 `evolution.h:152` |
| 29 | 280 | Ralts | **L Route102 s9 (4%) — only location in the game** | Route102 (`:14347`) | 20 | none |
| 30 | 281 | Kirlia | evolve Ralts L20 | party | — | `evolution.h:195` |
| 31 | 282 | Gardevoir | evolve Kirlia L30 | party | — | `evolution.h:196` |
| 32 | 283 | Surskit | L s11 (1%) Route102/114/117/120; W s4 (1%) Route102/111/114/117/120 | Route117 (`:16137`) | 20/4 | none |
| 33 | 284 | Masquerain | evolve Surskit L22 | party | — | `evolution.h:153` |
| 34 | 285 | Shroomish | **L PetalburgWoods s2 (10), s7 (5) — only location** | PetalburgWoods (`:11003`) | 20 | none |
| 35 | 286 | Breloom | evolve Shroomish L23 | party | — | `evolution.h:151` |
| 36 | 287 | Slakoth | **L PetalburgWoods s9 (4), s11 (1) — only location** | PetalburgWoods (`:11003`) | 20 | none |
| 37 | 288 | Vigoroth | evolve Slakoth L18 | party | — | `evolution.h:186` |
| 38 | 289 | Slaking | evolve Vigoroth L36 | party | — | `evolution.h:187` |
| 39 | 63 | Abra | L s5 (10%) GraniteCave_1F / _B1F / _B2F / _StevensRoom | GraniteCave_1F (`:10697`) | 10 | FLASH helpful, not required |
| 40 | 64 | Kadabra | evolve Abra L16 | party | — | `evolution.h:44` |
| 42 | 290 | Nincada | **L Route116 s2 (10), s4 (10) — only location** | Route116 (`:16068`) | 20 | none |
| 43 | 291 | Ninjask | evolve Nincada L20 | party | — | `evolution.h:148` |
| 44 | 292 | Shedinja | evolve Nincada L20 with a free party slot + a spare Poke Ball | party | — | `evolution.h:149` (`EVO_LEVEL_SHEDINJA`) |
| 45 | 293 | Whismur | L RusturfTunnel (all 12 slots), Route116 s1 (20), s3 (10) | RusturfTunnel (`:10628`) | 10/20 | Rusturf needs the tunnel opened |
| 46 | 294 | Loudred | L VictoryRoad_1F s3 (10) | VictoryRoad_1F | 10 | or Whismur L20 `evolution.h:188` |
| 47 | 295 | Exploud | evolve Loudred L40 | party | — | `evolution.h:189` |
| 48 | 296 | Makuhita | L GraniteCave_1F s1,s2 (20/10), s4,s6,s7; _StevensRoom; VictoryRoad_1F s5 | GraniteCave_1F (`:10697`) | 10 | none |
| 49 | 297 | Hariyama | L VictoryRoad_1F s1 (20), _B1F s1 (20) | VictoryRoad | 10 | or Makuhita L24 `evolution.h:161` |
| 50 | 118 | Goldeen | OR s1 (30) + GR s3 (20) on fresh water: PetalburgCity, Route102/111/114/117/120, MeteorFalls, VictoryRoad_B2F, SafariZone_Northwest/Southwest | 10-35 | a rod |
| 51 | 119 | Seaking | SR s7,s8,s9 (15/4/1) SafariZone_Northwest / _Southwest | SafariZone (`:18194`,`:18447`) | 35 | SUPER ROD + Safari entry; or Goldeen L33 `evolution.h:75` |
| 52 | 129 | Magikarp | OR s0 (70) everywhere with water | any | 10-35 | a rod |
| 53 | 130 | Gyarados | SR SootopolisCity s7,s8,s9 (15/4/1) | SootopolisCity (`:9834`) | 10 | SUPER ROD; or Magikarp L20 `evolution.h:79` |
| 54 | 298 | Azurill | **breed only** — hatch from Marill/Azumarill at the Route117 Day-Care | party | — | `evolution.h:181` (`AZURILL --friendship--> MARILL`), `dex.py:1560-1590` |
| 55 | 183 | Marill | W s0 (60) PetalburgCity/Route102/111/114/117/120; L Route117 s4 (10), Route120 s4 (10) | Route117 (`:16137`) | 4/20 | SURF for the water slots |
| 56 | 184 | Azumarill | evolve Marill L18 | party | — | `evolution.h:110` |
| 57 | 74 | Geodude | RS Route111/Route114 (all 5 slots), GraniteCave_B2F s0 (60), SafariZone_Northeast (all 5), VictoryRoad_B1F s1 (30); L GraniteCave_1F s8-11 | Route111 (`:15408`) | 20/25 | ROCK SMASH |
| 58 | 75 | Graveler | RS VictoryRoad_B1F s0 (60), s2-s4 | VictoryRoad_B1F (`:12987`) | 20 | ROCK SMASH + Victory Road; or Geodude L25 `evolution.h:51` |
| 60 | 299 | Nosepass | **RS GraniteCave_B2F s1 (30%) — only source in the game** | GraniteCave_B2F (`:10835`) | 20 | ROCK SMASH |
| 61 | 300 | Skitty | **L Route116 s10, s11 (1%/1%) — only wild source**; also the NPC trade (gives SKITTY, wants PIKACHU) | Route116 (`:16068`) | 20 | trade needs a PIKACHU (`pret/src/trade.c:871-880`) |
| 62 | 301 | Delcatty | evolve Skitty + MOON STONE | party | — | `evolution.h:156`; stone at Lilycove Dept 5F (`:23`) |
| 63 | 41 | Zubat | L GraniteCave (all), MeteorFalls_1F_1R, SeafloorCavern, CaveOfOrigin, ShoalCave, VictoryRoad_1F | GraniteCave_1F (`:10697`) | 4-10 | none |
| 64 | 42 | Golbat | L MeteorFalls_1F_2R/_B1F_1R/_B1F_2R s0 (20), SeafloorCavern s8-11, CaveOfOrigin, ShoalCave, VictoryRoad, SkyPillar | MeteorFalls_1F_2R (`:10166`) | 4-10 | or Zubat L22 `evolution.h:28` |
| 65 | 169 | Crobat | evolve Golbat at high friendship | party | — | `evolution.h:29` (`EVO_FRIENDSHIP`) |
| 66 | 72 | Tentacool | W s0 (60) on every ocean map | any ocean route | 4 | SURF |
| 67 | 73 | Tentacruel | W AbandonedShip s4 (1); SR AbandonedShip s7 (15), s8 (4), s9 (1) | AbandonedShip_Rooms_B1F (`:13893`) | 4/20 | or Tentacool L30 `evolution.h:50` |
| 68 | 302 | Sableye | L CaveOfOrigin_1F/_B1F/_B2F/_B3F s3,s4,s5 (10/10/10); GraniteCave_B2F s6-s11; VictoryRoad_B2F s1 (20); SkyPillar s0 (20) | CaveOfOrigin_1F (`:12642`) | 4-10 | Cave of Origin opens in the Sootopolis story beat |
| 70 | 304 | Aron | L GraniteCave_B1F s1 (20), s2,s3; _B2F s1 (20); _StevensRoom s8-11; VictoryRoad_1F s8,s10 | GraniteCave_B1F (`:10766`) | 10 | none |
| 71 | 305 | Lairon | L VictoryRoad_1F s2 (10), _B1F s2 (10), _B2F s2 (10) | VictoryRoad (`:12918`) | 10 | or Aron L32 `evolution.h:192` |
| 72 | 306 | Aggron | evolve Lairon L42 | party | — | `evolution.h:193` |
| 73 | 66 | Machop | L Route112 s2 (10), s5 (10); JaggedPass s2 (10); FieryPath s3 (10) | Route112 (`:15592`) | 10-20 | none |
| 74 | 67 | Machoke | evolve Machop L28 | party | — | `evolution.h:47` |
| 76 | 307 | Meditite | L MtPyre_Exterior s1 (20), s3 (10); VictoryRoad_B1F s9 (4), s11 (1) | MtPyre_Exterior (`:11624`) | 10 | none |
| 77 | 308 | Medicham | L VictoryRoad_B1F s3 (10), _B2F s3 (10) | VictoryRoad_B1F (`:12987`) | 10 | or Meditite L37 `evolution.h:182` |
| 78 | 309 | Electrike | L Route110 s1 (20), s3 (10); Route118 s1 (20), s3 (10) | Route118 (`:16291`) | 20 | none |
| 79 | 310 | Manectric | L Route118 s5 (10) | Route118 (`:16291`) | 20 | or Electrike L26 `evolution.h:162` |
| 80 | 311 | Plusle | **L Route110 s4 (10), s6 (5) — only location** | Route110 (`:15254`) | 20 | none |
| 81 | 312 | Minun | **L Route110 s10, s11 (1%/1%) — only location** | Route110 (`:15254`) | 20 | none |
| 82 | 81 | Magnemite | L NewMauville_Entrance / _Inside s1 (20), s3,s5,s7,s9,s11 | NewMauville_Entrance (`:13755`) | 10 | Basement Key from Wattson |
| 83 | 82 | Magneton | L NewMauville_Inside s11 (1) | NewMauville_Inside (`:13824`) | 10 | or Magnemite L30 `evolution.h:57` |
| 84 | 100 | Voltorb | L NewMauville_Entrance / _Inside s0 (20), s2,s4,s6,s8,s10; plus 3 static L25 "item balls" | NewMauville_Inside (`pret/data/maps/NewMauville_Inside/scripts.inc:166,181,196`) | 10 | Basement Key |
| 85 | 101 | Electrode | L NewMauville_Inside s10 (1) | NewMauville_Inside (`:13824`) | 10 | or Voltorb L30 `evolution.h:66` |
| 86 | 313 | Volbeat | **L Route117 s6,s7 (5/5), s8,s9 (4/4) — only location** | Route117 (`:16137`) | 20 | none |
| 87 | 314 | Illumise | **L Route117 s10 (1%) — only location** | Route117 (`:16137`) | 20 | none |
| 88 | 43 | Oddish | L Route110 s5 (10), Route117 s5 (10), Route119/120/121/123 s3 (10); SafariZone all four quadrants s0/s1 (20/20) | SafariZone_Southwest (`:18447`) | 20/25 | none |
| 89 | 44 | Gloom | L SafariZone_Northwest/_Northeast s5 (10), s6 (5); _Southwest/_Southeast s6 (5); Route121/123 s7 (5) | SafariZone (`:18194`) | 25 | or Oddish L21 `evolution.h:30` |
| 90 | 45 | Vileplume | evolve Gloom + LEAF STONE | party | — | `evolution.h:31`; stone at Lilycove Dept 5F (`:27`) |
| 91 | 182 | Bellossom | evolve Gloom + SUN STONE | party | — | `evolution.h:32`; stone at Lilycove Dept 5F (`:22`) and a gift at MossdeepCity_SpaceCenter_1F (`scripts.inc:37`) |
| 92 | 84 | Doduo | **L SafariZone_Northwest s4 (10), s7 (5); _Southwest s5 (10); _Southeast s5 (10) — Safari only** | SafariZone (`:18194`) | 25 | Safari entry |
| 93 | 85 | Dodrio | L SafariZone_Northwest s8 (4), s10 (1) | SafariZone_Northwest (`:18194`) | 25 | Safari; or Doduo L31 `evolution.h:58` |
| 94 | 315 | Roselia | **L Route117 s1 (20), s3 (10) — only location** | Route117 (`:16137`) | 20 | none |
| 95 | 316 | Gulpin | **L Route110 s2 (10), s7 (5) — only location** | Route110 (`:15254`) | 20 | none |
| 96 | 317 | Swalot | evolve Gulpin L26 | party | — | `evolution.h:187` |
| 97 | 318 | Carvanha | GR s4 (20) + SR s6-s9 on Route118 and Route119 | Route118 (`:16291`) | 30 | GOOD ROD |
| 98 | 319 | Sharpedo | SR s5 (40) on Route103/118/122/124/125/126/127/129-134, MossdeepCity, PacifidlogTown | Route118 (`:16291`) | 30 | **SUPER ROD**; or Carvanha L30 `evolution.h:156` |
| 99 | 320 | Wailmer | GR s4 (20) + SR s5-s9 on most ocean maps | Route105-109 etc. | 30 | GOOD ROD |
| 100 | 321 | Wailord | W Route129 s4 (1%) | Route129 (`:17595`) | 4 | or Wailmer L40 `evolution.h:154` |
| 101 | 322 | Numel | L Route112 s0 (20), s1 (20); JaggedPass s0 (20); FieryPath s0 (20) | Route112 (`:15592`) | 20 | none |
| 102 | 323 | Camerupt | evolve Numel L33 | party | — | `evolution.h:163` |
| 103 | 218 | Slugma | **L FieryPath s5 (10%) — only location** | FieryPath (`:11141`) | 10 | none |
| 104 | 219 | Magcargo | evolve Slugma L38 | party | — | `evolution.h:120` |
| 105 | 324 | Torkoal | **L FieryPath s4 (10), s8 (4), s9 (4) — only location** | FieryPath (`:11141`) | 10 | none |
| 106 | 88 | Grimer | **L FieryPath s1 (20), s6 (5) — only location** | FieryPath (`:11141`) | 10 | none |
| 107 | 89 | Muk | evolve Grimer L38 | party | — | `evolution.h:59` |
| 108 | 109 | Koffing | **L FieryPath s10, s11 (1%/1%) — only location** | FieryPath (`:11141`) | 10 | none |
| 109 | 110 | Weezing | evolve Koffing L35 | party | — | `evolution.h:69` |
| 110 | 325 | Spoink | **L JaggedPass s4 (10), s6 (5), s9 (4), s11 (1) — only location** | JaggedPass (`:11072`) | 20 | none |
| 111 | 326 | Grumpig | evolve Spoink L32 | party | — | `evolution.h:181` |
| 112 | 27 | Sandshrew | L Route111 s0 (20), s2 (10), s6 (5); Route113 s2 (10), s5 (10), s7 (5) | Route111 (`:15408`) | 10/20 | none |
| 113 | 28 | Sandslash | evolve Sandshrew L22 | party | — | `evolution.h:22` |
| 114 | 327 | Spinda | **L Route113 s0,s1 (20/20), s3,s4,s6,s8,s10 — only location** | Route113 (`:15661`) | 20 | none |
| 115 | 227 | Skarmory | **L Route113 s9 (4%), s11 (1%) — only location** | Route113 (`:15661`) | 20 | none |
| 116 | 328 | Trapinch | **L Route111 s1 (20), s3 (10), s7 (5) — only location** | Route111 (`:15408`) | 10 | none |
| 117 | 329 | Vibrava | evolve Trapinch L35 | party | — | `evolution.h:157` |
| 118 | 330 | Flygon | evolve Vibrava L45 | party | — | `evolution.h:158` |
| 119 | 331 | Cacnea | **L Route111 s4 (10), s5 (10) — only location** | Route111 (`:15408`) | 10 | none |
| 120 | 332 | Cacturne | evolve Cacnea L32 | party | — | `evolution.h:166` |
| 121 | 333 | Swablu | L Route114 s0 (20), s2 (10), s3 (10); Route115 s0 (20), s2 (10) | Route114 (`:15730`) | 20 | none |
| 122 | 334 | Altaria | L SkyPillar_5F s9 (4), s10 (1), s11 (1) | SkyPillar_5F (`:14209`) | 10 | or Swablu L35 `evolution.h:183` |
| 124 | 336 | Seviper | **L Route114 s5 (10), s8 (4), s9 (4), s10 (1) — only location** | Route114 (`:15730`) | 20 | none |
| 125 | 337 | Lunatone | **L MeteorFalls_1F_1R s5 (10), s6 (5), s7 (5); other MeteorFalls rooms s3,s4,s5,s7; W MeteorFalls s2 (5), s3 (4), s4 (1) — only maps** | MeteorFalls_1F_1R (`:10012`) | 4/10 | none |
| 127 | 339 | Barboach | GR s4 (20) + SR s5-s9 at Route111/114/120, MeteorFalls, VictoryRoad_B2F | Route111 (`:15408`) | 30 | GOOD ROD |
| 128 | 340 | Whiscash | SR s7 (15), s8 (4), s9 (1) at MeteorFalls_1F_2R/_B1F_1R/_B1F_2R, VictoryRoad_B2F | MeteorFalls_1F_2R (`:10166`) | 30 | SUPER ROD; or Barboach L30 `evolution.h:153` |
| 129 | 341 | Corphish | GR s4 (20) + SR s5-s9 at PetalburgCity, Route102, Route117 | Route117 (`:16137`) | 30 | GOOD ROD |
| 130 | 342 | Crawdaunt | evolve Corphish L30 | party | — | `evolution.h:154` |
| 131 | 343 | Baltoy | **L Route111 s8 (4), s9 (4), s10 (1), s11 (1) — only location** | Route111 (`:15408`) | 10 | none |
| 132 | 344 | Claydol | L SkyPillar_1F/_3F/_5F s4 (10), s7-s11 | SkyPillar_1F (`:14071`) | 10 | Sky Pillar opens late; or Baltoy L36 `evolution.h:152` |
| 133 | 345 | Lileep | **fossil** — hand the ROOT FOSSIL to Devon | RustboroCity_DevonCorp_2F (`scripts.inc:145`) | — | fossil taken at Route111 (`scripts.inc:55-58`); mutually exclusive with Anorith |
| 134 | 346 | Cradily | evolve Lileep L40 | party | — | `evolution.h:194` |
| 135 | 347 | Anorith | **fossil** — hand the CLAW FOSSIL to Devon | RustboroCity_DevonCorp_2F (`scripts.inc:165`) | — | Route111 (`scripts.inc:77-80`); mutually exclusive with Lileep |
| 136 | 348 | Armaldo | evolve Anorith L40 | party | — | `evolution.h:195` |
| 137 | 174 | Igglybuff | **breed only** — hatch from Jigglypuff/Wigglytuff at Route117 Day-Care | party | — | `evolution.h:104` (`IGGLYBUFF --friendship--> JIGGLYPUFF`) |
| 138 | 39 | Jigglypuff | **L Route115 s6 (5%), s7 (5%) — only location** | Route115 (`:15914`) | 20 | none |
| 139 | 40 | Wigglytuff | evolve Jigglypuff + MOON STONE | party | — | `evolution.h:27`; Lilycove Dept 5F (`:23`) |
| 140 | 349 | Feebas | **fishing, any rod, on 6 randomly-seeded water tiles of Route119** | Route119 | — | `pret/src/wild_encounter.c:23` (`gWildFeebasRoute119Data = {20, 25, SPECIES_FEEBAS}`), `:38` (`NUM_FEEBAS_SPOTS 6`), `:101-114` (spot selection, seeded from `easyChatPairs[0]`); area screen agrees it is Route119-only (`pret/src/pokedex_area_screen.c:93-96`) |
| 141 | 350 | Milotic | evolve Feebas at Beauty >= 170, then level up | party | — | `evolution.h:155` (`EVO_BEAUTY`); needs Pokeblocks — **not** a trade evolution on this cartridge |
| 142 | 351 | Castform | **gift**, L25 holding MYSTIC WATER | Route119_WeatherInstitute_2F (`scripts.inc:65-66`) | — | one-time; sets `FLAG_RECEIVED_CASTFORM` |
| 143 | 120 | Staryu | **SR LilycoveCity s7 (15%) — only source in the game** | LilycoveCity (`:9656`) | 10 | **SUPER ROD** |
| 144 | 121 | Starmie | evolve Staryu + WATER STONE | party | — | `evolution.h:76`; Lilycove Dept 5F (`:26`) |
| 145 | 352 | Kecleon | L s11 (1%) Route118/119/121/123, s10 (1%) Route120; plus one static L30 | Route120 static (`pret/data/maps/Route120/scripts.inc:222`) | 15/20 | static needs the DEVON SCOPE (`Route120/scripts.inc:53-54`) |
| 146 | 353 | Shuppet | L MtPyre_1F..6F/_Exterior/_Summit s0 (20); Route121/123 s1 (20), s3 (10) | MtPyre_1F (`:11210`) | 10/20 | none |
| 147 | 354 | Banette | L SkyPillar_1F/_3F/_5F s5 (10), s6 (5) | SkyPillar_1F (`:14071`) | 10 | or Shuppet L37 `evolution.h:191` |
| 148 | 355 | Duskull | **L MtPyre_4F/_5F/_6F s8 (4), s9 (4), s10 (1), s11 (1); MtPyre_Summit s7 (5), s8 (4), s9 (4) — only maps** | MtPyre_Summit (`:11693`) | 10 | none |
| 149 | 356 | Dusclops | evolve Duskull L37 | party | — | `evolution.h:185` |
| 150 | 357 | Tropius | **L Route119 s8 (4%), s9 (4%), s10 (1%) — only location** | Route119 (`:16445`) | 15 | none |
| 151 | 358 | Chimecho | **L MtPyre_Summit s10 (1%), s11 (1%) — only location** | MtPyre_Summit (`:11693`) | 10 | none |
| 152 | 359 | Absol | **L Route120 s8 (4%), s9 (4%) — only location** | Route120 (`:16599`) | 20 | none |
| 153 | 37 | Vulpix | **L MtPyre_Exterior s5 (10), s6 (5), s7 (5) — only location** | MtPyre_Exterior (`:11624`) | 10 | none |
| 154 | 38 | Ninetales | evolve Vulpix + FIRE STONE | party | — | `evolution.h:26`; Lilycove Dept 5F (`:24`) |
| 155 | 172 | Pichu | **breed only** — hatch from Pikachu/Raichu at Route117 Day-Care | party | — | `evolution.h:102`; parent is Safari-only |
| 156 | 25 | Pikachu | **L SafariZone_Southwest / _Southeast s8 (5%), s10 (1%) — Safari only** | SafariZone_Southwest (`:18447`) | 25 | Safari entry |
| 157 | 26 | Raichu | evolve Pikachu + THUNDER STONE | party | — | `evolution.h:21`; Lilycove Dept 5F (`:25`) |
| 158 | 54 | Psyduck | **W SafariZone_Northwest / _Southwest s0 (60), s1 (30), s2 (5) — Safari only** | SafariZone_Southwest (`:18447`) | 9 | Safari entry + SURF |
| 159 | 55 | Golduck | W SafariZone_Northwest s3 (4), s4 (1) | SafariZone_Northwest (`:18194`) | 9 | or Psyduck L33 `evolution.h:37` |
| 160 | 360 | Wynaut | **egg gift** at Lavaridge; also L Route130 (all 12 slots) | LavaridgeTown (`scripts.inc:287`), Route130 (`:17684`) | 20 | Route130 land table is **Mirage Island**, present only when the day's seed matches (`dex.py:781-785`, citing `src/overworld.c:1041-1043`, `src/time_events.c:42`). Take the egg. |
| 161 | 202 | Wobbuffet | L SafariZone_Southwest/_Southeast s7 (5), s9 (4), s11 (1) | SafariZone (`:18447`) | 25 | Safari; or Wynaut L15 `evolution.h:184` |
| 162 | 177 | Natu | **L SafariZone_Northeast s4 (10), s7 (5); _Southwest/_Southeast s4 (10) — Safari only** | SafariZone_Southwest (`:18447`) | 25 | Safari entry |
| 163 | 178 | Xatu | L SafariZone_Northeast s8 (4), s10 (1) | SafariZone_Northeast (`:18348`) | 25 | Safari; or Natu L25 `evolution.h:107` |
| 164 | 203 | Girafarig | **L SafariZone_Southwest / _Southeast s2 (10), s3 (10) — Safari only** | SafariZone_Southwest (`:18447`) | 25 | Safari entry |
| 165 | 231 | Phanpy | **L SafariZone_Northeast s0 (20), s2 (10) — Safari only** | SafariZone_Northeast (`:18348`) | 25 | Safari entry |
| 166 | 232 | Donphan | evolve Phanpy L25 | party | — | `evolution.h:127` |
| 167 | 127 | Pinsir | **L SafariZone_Northwest s9 (4%), s11 (1%) — Safari only** | SafariZone_Northwest (`:18194`) | 25 | Safari entry |
| 168 | 214 | Heracross | **L SafariZone_Northeast s9 (4%), s11 (1%) — Safari only** | SafariZone_Northeast (`:18348`) | 25 | Safari entry |
| 169 | 111 | Rhyhorn | **L SafariZone_Northwest s0 (20), s2 (10) — Safari only** | SafariZone_Northwest (`:18194`) | 25 | Safari entry |
| 170 | 112 | Rhydon | evolve Rhyhorn L42 | party | — | `evolution.h:71` |
| 171 | 361 | Snorunt | **L ShoalCave_LowTideIceRoom s6 (5%), s9 (4%), s11 (1%) — only location** | ShoalCave_LowTideIceRoom (`:13686`) | 10 | Shoal Cave low tide, off Route125 by SURF |
| 172 | 362 | Glalie | evolve Snorunt L42 | party | — | `evolution.h:180` |
| 173 | 363 | Spheal | **L ShoalCave low-tide rooms s1 (20), s3,s5,s7,s9,s11; W s2 (5), s3 (4), s4 (1) — only maps** | ShoalCave_LowTideEntranceRoom (`:13240`) | 4/10 | SURF to Route125 |
| 174 | 364 | Sealeo | evolve Spheal L32 | party | — | `evolution.h:164` |
| 175 | 365 | Walrein | evolve Sealeo L44 | party | — | `evolution.h:165` |
| 176 | 366 | Clamperl | **DV Underwater1 / Underwater2 s0 (60%), s2 (5%) — only source** | Underwater1 (`:18848`) under Route124; Underwater2 (`:18882`) under Route126 | 4 | **DIVE** |
| 179 | 369 | Relicanth | **DV Underwater1 / Underwater2 s3 (4%), s4 (1%) — only source** | Underwater1/2 (`:18848`,`:18882`) | 4 | **DIVE**; also a prerequisite for the Regis |
| 180 | 222 | Corsola | **SR EverGrandeCity s7 (15%) / Route128 s7 (15%) — only wild source**; also the NPC trade (gives CORSOLA, wants BELLOSSOM) | EverGrandeCity (`:9923`), Route128 (`:17506`) | 10/30 | **SUPER ROD**, or the trade (`pret/src/trade.c:872-880`) |
| 181 | 170 | Chinchou | **DV Underwater1 / Underwater2 s1 (30%) — only source** | Underwater1/2 (`:18848`,`:18882`) | 4 | **DIVE** |
| 182 | 171 | Lanturn | evolve Chinchou L27 | party | — | `evolution.h:101` |
| 183 | 370 | Luvdisc | GR s3 (20%) + SR s5 (40%) at EverGrandeCity and Route128 | EverGrandeCity (`:9923`), Route128 (`:17506`) | 10/30 | GOOD ROD + travel past Mossdeep |
| 184 | 116 | Horsea | **SR Route132 / Route133 / Route134 s7 (15%) — only source** | Route132 (`:17927`), Route133 (`:18016`), Route134 (`:18105`) | 30 | **SUPER ROD** |
| 185 | 117 | Seadra | evolve Horsea L32 | party | — | `evolution.h:72` |
| 187 | 371 | Bagon | **L MeteorFalls_B1F_2R s2 (10%), s4 (10%), s6 (5%) — only location** | MeteorFalls_B1F_2R (`:10474`) | 10 | **WATERFALL** to reach the back room |
| 188 | 372 | Shelgon | evolve Bagon L30 | party | — | `evolution.h:197` |
| 189 | 373 | Salamence | evolve Shelgon L50 | party | — | `evolution.h:198` |
| 190 | 374 | Beldum | **gift**, L5 in a Poke Ball | MossdeepCity_StevensHouse (`scripts.inc:85,90-91`) | — | object hidden behind `FLAG_HIDE_BELDUM_BALL_STEVENS_HOUSE` (`map.json:38`); post-Champion |
| 191 | 375 | Metang | evolve Beldum L20 | party | — | `evolution.h:199` |
| 192 | 376 | Metagross | evolve Metang L45 | party | — | `evolution.h:200` |
| 193 | 377 | Regirock | **static**, L40, one shot | DesertRuins (`scripts.inc:61`) | — | Sealed Chamber: `FLAG_REGI_DOORS_OPENED` (`SealedChamber_InnerRoom/scripts.inc:9-12,33`) needs `CheckRelicanthWailord` to pass; outer room needs `FLAG_SYS_BRAILLE_DIG` (`SealedChamber_OuterRoom/scripts.inc:17,111-113`). Reached via **DIVE** off Route134. |
| 194 | 378 | Regice | **static**, L40, one shot | IslandCave (`scripts.inc:80`) | — | same prerequisite |
| 195 | 379 | Registeel | **static**, L40, one shot | AncientTomb (`scripts.inc:61`) | — | same prerequisite |
| 196 | 380 | Latias | **roamer**, L40 | roams Hoenn (no fixed map) | — | `ROAMER_SPECIES = SPECIES_LATIAS` under `SAPPHIRE` (`species.h:1282-1286`); created by `InitRoamer` from the TV script (`pret/data/scripts/tv.inc:49-51`, `pret/src/roamer.c:62-70,83-88`) — i.e. after the Champion TV scene |
| 198 | 382 | Kyogre | **static**, L45, one shot | CaveOfOrigin_B4F (`scripts.inc:57`) | — | `SPECIES_GROUDON_OR_KYOGRE = SPECIES_KYOGRE` (`pret/constants/version.inc:21-25`) |
| 200 | 384 | Rayquaza | **static**, L70, one shot | SkyPillar_Top (`scripts.inc:16`) | — | MACH BIKE for the cracked floors (`pokeagent/acquire.py:74`) |

Rows 22/23/24 (Seedot line), 41 (Alakazam), 59 (Golem), 69 (Mawile),
75 (Machamp), 123 (Zangoose), 126 (Solrock), 177/178 (Huntail/Gorebyss),
186 (Kingdra), 197 (Latios), 199 (Groudon), 201 (Jirachi), 202 (Deoxys) are
deliberately absent — see section 4.

### In-game trades (`pret/src/trade.c:851-882`)

Three, and none of them is required for the dex:

| NPC gives | NPC wants | Note |
|---|---|---|
| MAKUHITA "MAKIT" (`trade.c:854`) | SLAKOTH (`trade.c:861`) | Makuhita is common wild; Slakoth is PetalburgWoods-only |
| SKITTY "SKITIT" (`trade.c:863`) | PIKACHU (`trade.c:870`) | Pikachu is Safari-only, so this trade costs more than it gives |
| CORSOLA "COROSO" (`trade.c:872-873`) | BELLOSSOM (`trade.c:880`) | **The only non-SUPER-ROD route to Corsola.** Bellossom = Gloom + SUN STONE. Worth doing if the Super Rod is far off. |

---

## 6. Priority split — NOW vs GATED

Capabilities assumed present: CUT, FLY, SURF, STRENGTH, ROCK SMASH,
OLD ROD, GOOD ROD, Pokeblock Case, 6 badges, Lilycove reached.

### 6a. Free wins available this minute, in order

**Tier 0 — buy stones, evolve what is already held.** All six evolution
stones are on one counter: `LilycoveCity_DepartmentStore_5F` clerk far left,
`Pokemart_StatBoostersAndStones` — SUN, MOON, FIRE, THUNDER, WATER, LEAF
(`pret/data/maps/LilycoveCity_DepartmentStore_5F/scripts.inc:22-27`). Lilycove
is already reached, so this unlocks, with no travel at all:
Ludicolo, Vileplume, Bellossom, Delcatty, Wigglytuff, Starmie, Ninetales,
Raichu — each the moment its pre-form is in hand.

**Tier 1 — Safari Zone (`Route121_SafariZoneEntrance`).** Costs 500 and the
Pokeblock Case, both held (`scripts.inc:62-63` checks `ITEM_POKEBLOCK_CASE`,
`:66` checks 500). This is the single densest map group left and **eleven of
its species exist nowhere else**: Doduo, Dodrio, Natu, Xatu, Girafarig,
Phanpy, Pinsir, Heracross, Rhyhorn, Wobbuffet, Pikachu, plus Psyduck/Golduck
on its water. Bring SURF for the NW/SW ponds; bring the GOOD ROD for
Goldeen. Note the Northwest and Northeast quadrants are behind the
MACH BIKE and ACRO BIKE respectively (`dex.py:733-734` names them exactly that).

**Tier 2 — Route 121 / 123 / Mt Pyre (adjacent to Lilycove).**
Shuppet, Gloom, Oddish, Linoone, Kecleon (1%), then Mt Pyre for
Meditite, Vulpix (only location), Duskull (only maps), Chimecho (1%, only
location). Mt Pyre Summit is the only Chimecho tile in the game.

**Tier 3 — Route 119 / 120 (Fortree side).** Tropius (only location),
Absol (only location), Kecleon, Surskit, Marill, plus **Feebas** — six
Route119 water tiles, any rod, seeded from `easyChatPairs[0]`
(`wild_encounter.c:101-114`). Feebas is the longest-tail item in the whole
plan; start it early and fish it opportunistically.

**Tier 4 — the desert / volcano block.** Route111 (Trapinch, Cacnea, Baltoy,
Sandshrew — all Route111-only except Sandshrew), Route112/JaggedPass
(Numel, Machop, Spoink), FieryPath (Torkoal, Slugma, Grimer, Koffing — all
four are FieryPath-only), Route113 (Spinda, Skarmory — both Route113-only).

**Tier 5 — Shoal Cave, off Route125 by SURF.** Spheal (only map group) and
Snorunt (`ShoalCave_LowTideIceRoom` only, 5/4/1%).

**Tier 6 — backfill the low dex numbers.** PetalburgWoods (Shroomish,
Slakoth, Silcoon, Cascoon — Woods-only), Route102 (**Ralts, 4%, the only
Ralts tile in the game**), Route116 (Nincada, Skitty), Route110 (Plusle,
Minun, Gulpin — all Route110-only), Route117 (Roselia, Volbeat, Illumise —
all Route117-only), Route115 (Jigglypuff — only location), GraniteCave
(Aron, Abra, Sableye, and **Nosepass by ROCK SMASH on B2F, 30%, the only
source**), RusturfTunnel (Whismur).

**Tier 7 — breeding at the Route117 Day-Care.** Azurill (from Marill),
Igglybuff (from Jigglypuff), Pichu (from a Safari Pikachu). All three have
no other source at all.

**Also now:** New Mauville (Voltorb, Magnemite, Electrode 1%, Magneton 1%) if
the Basement Key is held; the Wynaut egg at Lavaridge
(`LavaridgeTown/scripts.inc:287`), which makes Mirage Island irrelevant.

### 6b. Gated behind the SUPER ROD

Source: `MossdeepCity_House3` (`scripts.inc:12-13`, sets
`FLAG_RECEIVED_SUPER_ROD`). Mossdeep is Surf-reachable from Route124/125, so
this is likely a travel problem rather than a badge problem — **confirm with
the gates scout before planning around it.** [UNVERIFIED: whether the run's
current story flags allow the Route124 crossing.]

Species that have **no** non-Super-Rod wild source:

| Species | Where | Alternative |
|---|---|---|
| Staryu (#143) | LilycoveCity SR s7, 15% | none — Super Rod or nothing |
| Starmie (#144) | — | evolve Staryu + WATER STONE |
| Horsea (#184) | Route132/133/134 SR s7, 15% | none |
| Seadra (#185) | — | evolve Horsea L32 |
| Corsola (#180) | EverGrandeCity / Route128 SR s7, 15% | **the NPC trade for a Bellossom** (`trade.c:872-880`) |
| Sharpedo (#98) | many, SR s5, 40% | evolve Carvanha L30 (Carvanha is GOOD ROD) |
| Seaking (#51) | SafariZone SR s7-s9 | evolve Goldeen L33 |
| Gyarados (#53) | SootopolisCity SR s7-s9 | evolve Magikarp L20 |
| Whiscash (#128) | MeteorFalls / VictoryRoad_B2F SR | evolve Barboach L30 |
| Tentacruel (#67) | AbandonedShip SR / W 1% | evolve Tentacool L30 |

So the Super Rod is **strictly required for exactly two lines**: Staryu/Starmie
and Horsea/Seadra — plus Corsola unless you spend a Bellossom.

### 6c. Gated behind DIVE (HM08 + badge 7)

HM08 comes from Steven's house in Mossdeep
(`pokeagent/quest.py:463-467`, `require="FLAG_BADGE07_GET"`).

* **Clamperl (#176), Chinchou (#181), Relicanth (#179)** — `Underwater1`
  (under Route124) and `Underwater2` (under Route126) are the only Dive
  tables in the game (`:18848`, `:18882`). Lanturn follows from Chinchou.
* **Regirock / Regice / Registeel (#193-195)** — the Sealed Chamber is
  reached underwater off Route134, and `FLAG_REGI_DOORS_OPENED` requires
  `CheckRelicanthWailord` to pass (`SealedChamber_InnerRoom/scripts.inc:10-12`)
  plus the braille Dig (`SealedChamber_OuterRoom/scripts.inc:17,111-113`).
  So Dive gates the Regis twice over: for the room, and for the Relicanth.

### 6d. Gated behind WATERFALL (HM07 + badge 8)

HM07 is in Cave of Origin B3F (`pokeagent/quest.py:478-482`).

* **Bagon / Shelgon / Salamence (#187-189)** — `MeteorFalls_B1F_2R` is the
  only Bagon table (`:10474`, slots 2/4/6 at 10/10/5%), and the back room is
  behind the Meteor Falls waterfall.
* Waterfall also opens Ever Grande, hence Victory Road (Hariyama, Lairon,
  Loudred, Medicham, Graveler by Rock Smash) and the EverGrandeCity fishing
  table (Luvdisc, Corsola).

### 6e. Gated behind story progress / post-game

| Species | Gate |
|---|---|
| Sableye (#68) at Cave of Origin | the Sootopolis story beat (Granite Cave B1F/B2F and Victory Road also carry it) |
| Kyogre (#198) | Cave of Origin B4F, story climax (`CaveOfOrigin_B4F/scripts.inc:57`) |
| Claydol (#132), Banette (#147), Altaria (#122) | Sky Pillar opens in the Rayquaza sequence |
| Rayquaza (#200) | Sky Pillar summit + MACH BIKE (`acquire.py:74`) |
| Beldum (#190) -> Metang -> Metagross | Steven's house, `FLAG_HIDE_BELDUM_BALL_STEVENS_HOUSE`, post-Champion |
| Latias (#196) | roamer, created by the Champion TV scene (`pret/data/scripts/tv.inc:49-51`) |
| Castform (#142) | Weather Institute story beat (already passed if Route119 is open) |

---

## 7. Things a run can permanently miss

### 7a. One-time, one-shot — no second chance

| Species | Source | Failure mode |
|---|---|---|
| Regirock | `DesertRuins/scripts.inc:61-63` sets `FLAG_HIDE_REGIROCK` after the battle | KO it and it is gone |
| Regice | `IslandCave/scripts.inc:80-82` | same |
| Registeel | `AncientTomb/scripts.inc:61-63` | same |
| Kyogre | `CaveOfOrigin_B4F/scripts.inc:57` | same |
| Rayquaza | `SkyPillar_Top/scripts.inc:16` | same |
| Latias | roamer; flees every turn | needs Mean Look / Block or a Master Ball |
| Kecleon (Route120 static) | `Route120/scripts.inc:222` | wild slots at 1% remain as a fallback on 5 routes |
| **Lileep OR Anorith** | Route111 — taking one hides both (`scripts.inc:55-58`, `:77-80`) | permanent; no Desert Underpass in pokeruby |
| **Starter, 1 of 3** | `sStarterMons` (`starter_choose.c:50`) | permanent |
| Castform | `Route119_WeatherInstitute_2F/scripts.inc:65-66` | one-time gift |
| Beldum | `MossdeepCity_StevensHouse/scripts.inc:85` | one-time gift |
| Wynaut (egg) | `LavaridgeTown/scripts.inc:287` | one-time; Route130 Mirage Island is the only backup and it is a daily lottery |

### 7b. Fishing-only species (no land/surf/dive slot anywhere)

| Rod | Species |
|---|---|
| OLD ROD reaches | Magikarp, Goldeen |
| GOOD ROD reaches | Corphish, Barboach, Carvanha, Wailmer, Luvdisc, (+ the above) |
| **SUPER ROD only** | **Staryu, Horsea, Corsola** (Corsola also by NPC trade); Sharpedo, Seaking, Gyarados, Whiscash also appear only on Super Rod slots but each has an evolution fallback |
| Any rod, special code | **Feebas** — 6 seeded tiles on Route119 (`wild_encounter.c:23,38,101-114`) |

Derived lines that therefore also depend on a rod: Crawdaunt (Corphish),
Whiscash (Barboach), Sharpedo (Carvanha), Wailord (Wailmer), Starmie
(Staryu), Seadra (Horsea), Milotic (Feebas), Seaking (Goldeen),
Gyarados (Magikarp).

### 7c. Safari-Zone-only species

Every one of these exists on **no other map in the Sapphire tables**
(`:18194`, `:18348`, `:18447`, `:18601`):

Doduo, Dodrio, Natu, Xatu, Girafarig, Phanpy, Pinsir, Heracross, Rhyhorn,
Wobbuffet, Pikachu, Psyduck, Golduck.

And through them, by evolution or breeding: Donphan (Phanpy), Rhydon
(Rhyhorn), Raichu + **Pichu** (Pikachu), Xatu (Natu), Dodrio (Doduo).

Entry costs 500 and requires the Pokeblock Case
(`Route121_SafariZoneEntrance/scripts.inc:62-66`) — both held. The Northwest
quadrant needs the MACH BIKE and the Northeast the ACRO BIKE
(`dex.py:733-734`), and only one bike can be held at a time, so **plan two
visits**. Heracross and Pinsir sit at 4%/1% in the two bike quadrants; budget
Safari balls accordingly.

### 7d. Single-tile-table species (one map, nowhere else)

High-risk if that map is ever skipped: Ralts (Route102, 4%), Shroomish /
Slakoth / Silcoon / Cascoon (PetalburgWoods), Nincada + Skitty (Route116),
Plusle / Minun / Gulpin (Route110), Roselia / Volbeat / Illumise (Route117),
Jigglypuff (Route115), Slugma / Torkoal / Grimer / Koffing (FieryPath),
Spoink (JaggedPass), Trapinch / Cacnea / Baltoy (Route111), Spinda / Skarmory
(Route113), Seviper (Route114), Lunatone (MeteorFalls), Tropius (Route119),
Absol (Route120), Vulpix (MtPyre_Exterior), Duskull (MtPyre 4F-6F/Summit),
Chimecho (MtPyre_Summit, 1%), Snorunt (ShoalCave_LowTideIceRoom),
Spheal (ShoalCave), Nosepass (GraniteCave_B2F Rock Smash, 30%),
Bagon (MeteorFalls_B1F_2R), Clamperl / Chinchou / Relicanth (Underwater1/2).

---

## 8. Method -> map index

**Rock Smash tables (5 maps only):** GraniteCave_B2F (`:10835`, Geodude +
**Nosepass 30%**), Route111 (`:15408`, all Geodude), Route114 (`:15730`, all
Geodude), SafariZone_Northeast (`:18348`, all Geodude), VictoryRoad_B1F
(`:12987`, Graveler 60% + Geodude 30%).

**Dive tables (2 maps only):** Underwater1 (`:18848`, under Route124),
Underwater2 (`:18882`, under Route126). Identical tables:
Clamperl 60 / Chinchou 30 / Clamperl 5 / Relicanth 4 / Relicanth 1.

**Fresh-water fishing (Goldeen/Barboach/Corphish/Whiscash lines):**
PetalburgCity, Route102, Route111, Route114, Route117, Route120,
MeteorFalls (all four rooms), VictoryRoad_B2F, SafariZone_Northwest,
SafariZone_Southwest.

**Ocean fishing (Wailmer/Sharpedo/Tentacool lines):** every other water map.
The interesting Super Rod slot-7 (15%) outliers: LilycoveCity -> **Staryu**,
EverGrandeCity + Route128 -> **Corsola**, Route132/133/134 -> **Horsea**,
SootopolisCity -> **Gyarados** (s7/s8/s9).

**Maps whose land table is a single species:** RusturfTunnel (Whismur),
MtPyre_1F/2F/3F (Shuppet), Route130 (Wynaut, Mirage Island only).

---

## 9. How to re-derive any of this without trusting this file

```python
from trek import Driver
from pokeagent import dex
d = Driver("saves/live-run.state", game="sapphire")
t = dex.DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)

t.summary(d.state)                     # one-line status
t.progress(d.state)                    # counts against the achievable set
[ (e.dex, e.natdex, e.name) for e in t.missing(d.state) ]
t.out_of_reach_by_reason()             # the exclusion list, from the ROM
t.wild.for_species(d.names.species_id("RALTS"))
t.wild.for_map("SafariZone_Southwest")
t.routes(species_id, owned=t.owned_species(d.state))
```

The wild tables in section 5 were read out of
`pret/src/data/wild_encounters.json:9477-18918` (the `_Sapphire` half) rather
than from a running emulator, because this document had to be produced without
starting the ROM. `WildTable` reads the same bytes from the live cartridge
(`dex.py:544-551`), so the two must agree; if they ever do not, **the ROM
wins** and this file is wrong.
