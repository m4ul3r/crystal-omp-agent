# DEX_PLAN_GATES.md

**Story and tool critical path from the live save to a complete Hoenn dex, in execution order.**

Written from the vendored pokeruby decompilation at `pret/` (Sapphire build — every `.ifdef SAPPHIRE` branch is the live one) and from this repo's own Python. Every claim below carries a `path:line`. Anything not read out of those two sources is marked `[UNVERIFIED]`.

Non-goal: species-by-species wild encounter tables. A sibling document owns those. This document is *gates and tools* — the order in which roads open.

---

## 0. Where the run stands

| Fact | Value | Read by |
|---|---|---|
| Badges | 6/8 | `pokeagent/quest.py:658-676` (`Quest.badges()` / `next_gym()`) |
| Next gym in the spine | badge 7 — TateAndLiza, `MossdeepCity_Gym`, Mossdeep | `pokeagent/quest.py:46` |
| Last gym in the spine | badge 8 — Wallace, `SootopolisCity_Gym_1F`, Sootopolis | `pokeagent/quest.py:47` |
| Dex | 22 caught / 88 seen | `pokeagent/dex.py:1303-1357` (`dex_flags` / `progress`) |
| Field moves held | CUT, FLY, SURF, STRENGTH, ROCK SMASH | `pokeagent/missables.py:537-561` |
| Field moves **missing** | FLASH, DIVE, WATERFALL | same |
| Rods held | OLD ROD, GOOD ROD (SUPER ROD missing) | `pokeagent/quest.py:410-422` |

### What `d.field_moves()` actually means

`trek.py:2061-2068` delegates straight to `missables.field_moves(state)`. That function (`pokeagent/missables.py:537-561`) walks `state.party()` and maps each mon's move ids against the ROM's own `TMHMMoves` table, returning `{HM move name: nickname-or-None}`. It answers **"is the move on a party member right now"** — *not* "is the HM in the bag", and *not* "can I use it".

Usability adds the badge, and that is a separate call per move:

| Predicate | Badge gate | Definition |
|---|---|---|
| `d.can_surf()` | `FLAG_BADGE05_GET` | `trek.py:1284-1289` |
| `d.can_dive()` | `FLAG_BADGE07_GET` | `trek.py:1189-1194` |
| `d.can_waterfall()` | `FLAG_BADGE08_GET` | `trek.py:1197-1202` |

So: **SURF is held and usable.** A party member knows it, and badge 5 (Norman) is among the six held. `nav` is told about it per journey by `trek.py:1291-1302` (`_surf_sync`), which also flips `nav.waterfall` — so the router's idea of what is walkable changes the moment WATERFALL lands on a party member.

**Where SURF came from** (already collected, recorded here so a future session does not go looking): Wally's father in `PetalburgCity_WallysHouse`, static cell **(3,4)**, behind `FLAG_DEFEATED_PETALBURG_GYM`, setting `FLAG_RECEIVED_HM_SURF` — `pret/data/maps/PetalburgCity_WallysHouse/scripts.inc:9` (the badge branch) and `:20-26` (`giveitem ITEM_HM03_SURF` / `setflag FLAG_RECEIVED_HM_SURF`). The harness's own step is `pokeagent/quest.py:356-361`.

---

## 1. Tools still missing — exact acquisition

### HM08 DIVE

| | |
|---|---|
| Map | `MossdeepCity_StevensHouse` |
| Trigger | `map_script MAP_SCRIPT_ON_FRAME_TABLE` on `VAR_STEVENS_HOUSE_STATE == 0` — **walking in is the whole step**, there is nobody to talk to |
| Sets | `FLAG_RECEIVED_HM08`, `FLAG_OMIT_DIVE_FROM_STEVEN_LETTER`, then `VAR_STEVENS_HOUSE_STATE = 1` |
| Gate in the ROM | **none** — Steven hands it to whoever walks in |
| Gate that matters | `FLAG_BADGE07_GET`, because that is when Dive becomes *usable* |

Citations: `pret/data/maps/MossdeepCity_StevensHouse/scripts.inc:23-24` (the OnFrame table), `:26-49` (`MossdeepCity_StevensHouse_EventScript_StevenGivesDive`, with `giveitem ITEM_HM08_DIVE` at `:41` and `setflag FLAG_RECEIVED_HM08` at `:42`). Harness step: `pokeagent/quest.py:463-468`, gated `require="FLAG_BADGE07_GET"`.

**Do not walk in before badge 7 is safe to lose.** After the Seafloor Cavern climax the same var is force-set to 1 and Steven is hidden (`pret/data/maps/SeafloorCavern_Room9/scripts.inc:205-206`: `setflag FLAG_HIDE_STEVEN_STEVENS_HOUSE`, `setvar VAR_STEVENS_HOUSE_STATE, 1`) — but the Seafloor Cavern cannot be reached without Dive, so the ordering is self-enforcing. There is a second, fallback source behind the same flag (an item ball in the same house, `pokeagent/missables.py:598-603`).

### HM07 WATERFALL

| | |
|---|---|
| Map | `CaveOfOrigin_B3F` |
| Cell | **(6,5)** — an `OBJ_EVENT_GFX_ITEM_BALL`, not a person |
| Script | `CaveOfOrigin_B3F_EventScript_1B1A44` → `finditem ITEM_HM07_WATERFALL` |
| Flag | `FLAG_ITEM_CAVE_OF_ORIGIN_B3F_1` |
| Reachable when | `FLAG_LEGEND_ESCAPED_SEAFLOOR_CAVERN` is set (that is what unlocks Sootopolis' Cave of Origin) |

Citations: `pret/data/maps/CaveOfOrigin_B3F/map.json:14-26` (ball at 6,5, script + flag), `pret/data/item_ball_scripts.inc:477-479` (the `finditem`). Harness step: `pokeagent/quest.py:478-483`.

Route into B3F: `CaveOfOrigin_B3F` warps are B2F at (7,14) and **B4F at (12,6)** (`pret/data/maps/CaveOfOrigin_B3F/map.json:29-42`). So HM07 sits directly on the path down to Kyogre — pick it up on the way, not on a second trip.

### SUPER ROD

| | |
|---|---|
| Map | `MossdeepCity_House3` (a `MAP_TYPE_INDOOR` off Mossdeep, warp back to Mossdeep warp id 5) |
| Cell | **(4,4)** — `OBJ_EVENT_GFX_FISHERMAN`, `MossdeepCity_House3_EventScript_SuperRodFisherman` |
| Conversation | `MSGBOX_YESNO` — answer **YES** |
| Sets | `FLAG_RECEIVED_SUPER_ROD` (`0x98`) |
| Gate | **none** — unconditional, only guarded against repeats |

Citations: `pret/data/maps/MossdeepCity_House3/map.json:13-25` (object at 4,4 with the script), `pret/data/maps/MossdeepCity_House3/scripts.inc:7-16` (`giveitem ITEM_SUPER_ROD` at `:12`, `setflag FLAG_RECEIVED_SUPER_ROD` at `:13`), `pret/include/constants/flags.h:109` (flag id).

**This is the single highest-value dex tool still outstanding and it has no gate at all.** The rod decides which fishing slots exist (`pokeagent/quest.py:404-407` cites `src/wild_encounter.c:200-235`), and `WildTable._slot_kind` (`pokeagent/dex.py:590-606`) splits the ROM's fishing table by rod for exactly this reason. There is **no `StoryStep` for the SUPER ROD in `PROLOGUE`** — `quest.py` collects OLD and GOOD (`:410-422`) and stops. A driver must add it or walk there deliberately.

### HM05 FLASH — needed for Victory Road, and currently unheld

| | |
|---|---|
| Map | `GraniteCave_1F` |
| Cell | **(36,9)** — `OBJ_EVENT_GFX_HIKER`, `GraniteCave_1F_EventScript_15CBA7` |
| Sets | `FLAG_RECEIVED_HM05` |
| Gate | none |

Citations: `pret/data/maps/GraniteCave_1F/map.json:14-26`, `pret/data/maps/GraniteCave_1F/scripts.inc:7-13`.

Why it matters: `VictoryRoad_B1F` and `VictoryRoad_B2F` both declare `"requires_flash": true` (`pret/data/maps/VictoryRoad_B1F/map.json:7`, `pret/data/maps/VictoryRoad_B2F/map.json:7`); `VictoryRoad_1F` does not (`.../VictoryRoad_1F/map.json:7`). `missables` reads that key per map (`pokeagent/missables.py:39-41`). There is **no `StoryStep` for HM05 either** — the run walked past Granite Cave three times delivering Steven's letter.

---

## 2. The ordered story beats: 6/8 badges → Champion

The harness's chain is `PROLOGUE`, a flat tuple of `StoryStep` (`pokeagent/quest.py:93-492`). Each step is `(detail, kind, name, value, map, talk, choice, choice_among, stand, require)` (`pokeagent/quest.py:50-90`). `kind` is `"flag_unset"` (offer while the flag is clear) or `"var_lt"` (offer while the var is below `value`); `require` is the *offer* gate with grammar `FLAG_X` / `!FLAG_X` / `VAR_X>=n` / `field:MOVE` (`pokeagent/quest.py:678-711`). `field:FLY` asks `d.field_moves()`, i.e. can-use, not own.

`pending_story()` returns the **first** step still unsatisfied whose `require` holds (`pokeagent/quest.py:657-676`), and `next_objective()` prefers it over any gym (`pokeagent/quest.py:713-722`).

### The remaining chain

| # | Beat | Map | Where | Condition (kind / name / value) | `require` | Sets |
|---|---|---|---|---|---|---|
| B1 | Witness the orb theft on Mt. Pyre's summit | `MtPyre_Summit` | **stand (23,7)**; three `coord_event`s fire on `VAR_MT_PYRE_STATE == 0` | `var_lt VAR_MT_PYRE_STATE < 1` | `FLAG_BADGE06_GET` | `FLAG_MT_PYRE_ORB_STOLEN`, `VAR_SLATEPORT_STATE=1`, clears `FLAG_HIDE_STERN_SLATEPORT`, **`VAR_SLATEPORT_HARBOR_STATE=1`**, `VAR_MT_PYRE_STATE=1`; then gives `ITEM_RED_OR_BLUE_ORB` + `FLAG_RECEIVED_RED_OR_BLUE_ORB` |
| B2 | Watch the submarine theft at Slateport Harbor | `SlateportCity_Harbor` | **stand (8,12)**; four `coord_event`s fire on `VAR_SLATEPORT_HARBOR_STATE == 1` | `var_lt VAR_SLATEPORT_HARBOR_STATE < 2` | `VAR_SLATEPORT_HARBOR_STATE>=1` | `VAR_SLATEPORT_HARBOR_STATE=2`, **`FLAG_HIDE_GRUNT_1_BLOCKING_HIDEOUT`**, **`FLAG_HIDE_GRUNT_2_BLOCKING_HIDEOUT`** |
| B3 | Chase the team through the Lilycove hideout | `AquaHideout_B2F` | talk **(23,19)** — a line-of-sight grunt, `TRAINER_HIDEOUT_B2F_GRUNT_1` | `flag_unset FLAG_EVIL_TEAM_ESCAPED_IN_SUBMARINE` | `VAR_SLATEPORT_HARBOR_STATE>=2` | `FLAG_EVIL_TEAM_ESCAPED_IN_SUBMARINE`, `FLAG_HIDE_GRUNTS_LILYCOVE` |
| **G7** | **Beat Tate & Liza — badge 7** | `MossdeepCity_Gym` | leader cell resolved live from the gym's `object_events` by script label | not a `StoryStep`; `SPINE` row 7 | — | `FLAG_DEFEATED_MOSSDEEP_GYM`, **`FLAG_BADGE07_GET`**, clears `FLAG_HIDE_BRINEY_SLATEPORT_SHIPYARD` |
| B4 | Collect HM08 DIVE | `MossdeepCity_StevensHouse` | **no cell — walk in**, `OnFrame` on `VAR_STEVENS_HOUSE_STATE == 0` | `flag_unset FLAG_RECEIVED_HM08` | `FLAG_BADGE07_GET` | `FLAG_RECEIVED_HM08`, `VAR_STEVENS_HOUSE_STATE=1` |
| B5 | Follow the team to the bottom of the Seafloor Cavern | `SeafloorCavern_Room9` | `coord_event` at **x=17** on `VAR_SEAFLOOR_CAVERN_STATE == 0` | `flag_unset FLAG_LEGEND_ESCAPED_SEAFLOOR_CAVERN` | `FLAG_RECEIVED_HM08` | `VAR_ROUTE128_STATE=1`, `VAR_SOOTOPOLIS_STATE=1`, `FLAG_SYS_WEATHER_CTRL`, `FLAG_HIDE_SOOTOPOLIS_RESIDENTS`, **`FLAG_LEGEND_ESCAPED_SEAFLOOR_CAVERN`**, `FLAG_HIDE_STEVEN_STEVENS_HOUSE`, `VAR_STEVENS_HOUSE_STATE=1`, `VAR_SEAFLOOR_CAVERN_STATE=1`, then `warp MAP_ROUTE128, 255, 38, 22` |
| B6 | Pick up HM07 WATERFALL | `CaveOfOrigin_B3F` | item ball at **(6,5)** | `flag_unset FLAG_ITEM_CAVE_OF_ORIGIN_B3F_1` | `FLAG_LEGEND_ESCAPED_SEAFLOOR_CAVERN` | that flag |
| B7 | Face Kyogre at the bottom of the Cave of Origin | `CaveOfOrigin_B4F` | **stand (9,13)** | `var_lt VAR_CAVE_OF_ORIGIN_B4F_STATE < 1` | `FLAG_LEGEND_ESCAPED_SEAFLOOR_CAVERN` | `VAR_CAVE_OF_ORIGIN_B4F_STATE=1`, **`FLAG_LEGENDARY_BATTLE_COMPLETED`**, clears `FLAG_HIDE_SOOTOPOLIS_RESIDENTS` and `FLAG_SYS_WEATHER_CTRL`, `VAR_MT_PYRE_STATE=2`, clears `FLAG_HIDE_WALLACE_SOOTOPOLIS_GYM` |
| **G8** | **Beat Wallace — badge 8** | `SootopolisCity_Gym_1F` | leader cell from the gym map | `SPINE` row 8 | — | `FLAG_DEFEATED_SOOTOPOLIS_GYM`, **`FLAG_BADGE08_GET`**, `FLAG_RECEIVED_TM_WATER_PULSE` |
| B8 | Victory Road | `VictoryRoad_1F/B1F/B2F` | from `EverGrandeCity` warps at **(18,41)** and **(18,27)** | *not modelled by the harness* | — | — |
| B9 | Elite Four door guard | `EverGrandeCity_PokemonLeague` | guards stand at **(9,2)** and **(10,2)** while `FLAG_ENTERED_ELITE_FOUR` is clear | *not modelled* | — | `FLAG_ENTERED_ELITE_FOUR` |
| B10 | Sidney → Phoebe → Glacia → Drake → Steven | `EverGrandeCity_SidneysRoom` … `_ChampionsRoom` | — | *not modelled* | — | `FLAG_DEFEATED_ELITE_4_*` (`0x4DD`+), `FLAG_SYS_GAME_CLEAR` |

**Citations, beat by beat**

- B1 — step `pokeagent/quest.py:440-445`; scene `pret/data/maps/MtPyre_Summit/scripts.inc:29-70`; the state-setting block `…/scripts.inc:74-78` (`setvar VAR_SLATEPORT_HARBOR_STATE, 1` at `:76`, `setvar VAR_MT_PYRE_STATE, 1` at `:77`); triggers declared on `VAR_MT_PYRE_STATE == 0` at `pret/data/maps/MtPyre_Summit/map.json:118-143`.
- B2 — step `pokeagent/quest.py:447-452`; triggers on `VAR_SLATEPORT_HARBOR_STATE == 1` at `pret/data/maps/SlateportCity_Harbor/map.json:150-185`; scene `pret/data/maps/SlateportCity_Harbor/scripts.inc:48-79`, with `setvar VAR_SLATEPORT_HARBOR_STATE, 2` at `:64` and the two `FLAG_HIDE_GRUNT_*_BLOCKING_HIDEOUT` at `:75-76`. The harbour's `OnTransition` only arms the scene while the var is exactly 1 (`…/scripts.inc:5-11`) — miss it and the var never advances on its own.
- B3 — step `pokeagent/quest.py:454-459`; the hideout door is a Lilycove warp at **(70,5), elevation 1** → `MAP_AQUA_HIDEOUT_1F` (`pret/data/maps/LilycoveCity/map.json:290-296`); the chase ends in `AquaHideout_B2F_EventScript_15D8FD`, `setflag FLAG_EVIL_TEAM_ESCAPED_IN_SUBMARINE` at `pret/data/maps/AquaHideout_B2F/scripts.inc:67`.
- G7 — `pokeagent/quest.py:46` (spine row); badge script `pret/data/maps/MossdeepCity_Gym/scripts.inc:47-63` (`setflag FLAG_DEFEATED_MOSSDEEP_GYM` `:59`, `setflag FLAG_BADGE07_GET` `:60`). The gym is a **four-switch metatile puzzle**: `FLAG_MOSSDEEP_GYM_SWITCH_1..4` toggle arrows and are re-applied on load (`…/scripts.inc:5-45`, switch handlers `:79-162`). Toggling is idempotent-with-a-clear-branch, so a driver must read the flags rather than count presses. Tate & Liza are one leader and one badge — `leader_cell()` returns the first of `tate`/`liza` and either sprite starts the same **double** battle (`pokeagent/quest.py:614-637`).
- B4 — see §1.
- B5 — step `pokeagent/quest.py:471-476`; trigger `pret/data/maps/SeafloorCavern_Room9/map.json:115-124`; the flag avalanche `pret/data/maps/SeafloorCavern_Room9/scripts.inc:194-215`.
- B6 — see §1.
- B7 — step `pokeagent/quest.py:486-491`; scene `pret/data/maps/CaveOfOrigin_B4F/scripts.inc:24-79`, `setwildbattle SPECIES_GROUDON_OR_KYOGRE, 45` at `:56`, `setflag FLAG_LEGENDARY_BATTLE_COMPLETED` at `:74`.
- G8 — `pokeagent/quest.py:47`; `pret/data/maps/SootopolisCity_Gym_1F/scripts.inc:89-105` (`setflag FLAG_DEFEATED_SOOTOPOLIS_GYM` `:101`, `setflag FLAG_BADGE08_GET` `:102`).
- B9 — `pret/data/maps/EverGrandeCity_PokemonLeague/scripts.inc:5-12` (guards placed while `FLAG_ENTERED_ELITE_FOUR` is clear) and `:56-79` (`EverGrandeCity_PokemonLeague_1F_EventScript_DoorGuard`, `setflag FLAG_ENTERED_ELITE_FOUR` at `:78`). **Quirk worth knowing:** as vendored, that guard tests `goto_if_unset FLAG_BADGE06_GET` (`…/scripts.inc:65`), not badge 8. Do not rely on it as the badge-8 check; the physical gate is Waterfall (§2.2).
- B10 — flag ids `pret/include/constants/flags.h:767-768` (`FLAG_DEFEATED_ELITE_4_SYDNEY` `0x4DD`, `…PHOEBE` `0x4DE`), `FLAG_SYS_GAME_CLEAR` at `:784`.

### 2.1 Sootopolis: the doors are shut until Kyogre is faced

After B5 and before B7 the whole city is locked. `SootopolisCity_OnLoad` calls `SootopolisCity_EventScript_WeatherEventDoorsLocked` while `FLAG_LEGEND_ESCAPED_SEAFLOOR_CAVERN` is set, which calls `SootopolisCity_EventScript_LockCityDoors` while `FLAG_LEGENDARY_BATTLE_COMPLETED` is clear — nine `setmetatile … METATILE_Sootopolis_Door_Closed` including **the gym door at (31,32)** (`pret/data/maps/SootopolisCity/scripts.inc:7-33`).

Consequence for the driver: **badge 8 is unreachable until B7 is done.** A run that walks into Sootopolis after the Seafloor Cavern and heads for the gym will find a wall, and `blockers.to_warp()` will correctly report `every neighbour is wall` (`pokeagent/blockers.py:152-155`) — that is not a pathfinder bug.

Before B5, a *different* branch is used: `SootopolisCity_EventScript_ExpertBlockCaveOfOrigin` parks an NPC at (31,18) and closes the gym door (`…/scripts.inc:12-16`).

### 2.2 How each late-game map is physically reached

All of these are Dive/Waterfall geometry, not scripts. Read them off the map JSON:

| Destination | Route |
|---|---|
| Sootopolis City | Surf Route 126 → **Dive** (`Route126` dive-connects to `MAP_UNDERWATER2`, `pret/data/maps/Route126/map.json:23-27`) → warp at **(45,65)** on Underwater2 → `Underwater_SootopolisCity` (`pret/data/maps/Underwater2/map.json:31-37`) → surface: `setdivewarp MAP_SOOTOPOLIS_CITY, 255, 29, 53` (`pret/data/maps/Underwater_SootopolisCity/scripts.inc:5-7`) |
| Seafloor Cavern | Surf Route 128 → **Dive** (`Route128` dive-connects to `MAP_UNDERWATER4`, `pret/data/maps/Route128/map.json:28-32`) → warp at **(38,26)** on Underwater4 → `Underwater_SeafloorCavern` (`pret/data/maps/Underwater4/map.json:26-32`) → `SeafloorCavern_Entrance` (`setdivewarp`/`setescapewarp MAP_UNDERWATER_SEAFLOOR_CAVERN, 255, 6, 5`, `pret/data/maps/SeafloorCavern_Entrance/scripts.inc:5-8`) |
| Ever Grande City | Route 128 **right** connection, offset −40 (`pret/data/maps/Route128/map.json:23-27`) |
| Victory Road | `EverGrandeCity` warps **(18,41)** → `VICTORY_ROAD_1F` warp 0 and **(18,27)** → warp 1 (`pret/data/maps/EverGrandeCity/map.json:35-48`); B1F/B2F need FLASH |
| Pokémon League | `EverGrandeCity` warp **(18,5)** (`pret/data/maps/EverGrandeCity/map.json:21-27`) |

`[UNVERIFIED — general knowledge, flagged as the brief requires]` The climb from the Ever Grande beach to the League plateau is a waterfall; that is why badge 8 + HM07 is the real Elite Four gate rather than the door guard's flag test. The harness would prove it mechanically: `nav.waterfall` is only set true when `can_waterfall()` is (`trek.py:1291-1302`), so `route_legs` to `EverGrandeCity_PokemonLeague` returning nothing before HM07 is the confirmation to look for.

### 2.3 The harness stops at Kyogre

`PROLOGUE` ends with B7 (`pokeagent/quest.py:486-492`). There is **no step for badge 8, Victory Road, the League door guard, or any Elite Four member.** Once the eighth badge is held, `next_gym()` returns `None` and `next_objective()` returns `Objective("done", "all 8 badges held; the Elite Four is next")` (`pokeagent/quest.py:726-729`) — a terminal state with no plan behind it. Anyone driving to Champion must either extend `PROLOGUE` or drive B8–B10 by hand.

---

## 3. The Safari Zone

### 3.1 Getting in

Entry is a **`coord_event` at (8,4)** in `Route121_SafariZoneEntrance`, firing on `VAR_TEMP_1 == 0` into `Route121_SafariZoneEntrance_EventScript_15C383` (`pret/data/maps/Route121_SafariZoneEntrance/map.json:80-90`). Stand on it; do not try to talk to anyone.

The script (`pret/data/maps/Route121_SafariZoneEntrance/scripts.inc:47-87`):

1. `MSGBOX_YESNO` — answer **YES** (`:53-55`).
2. `checkitem ITEM_POKEBLOCK_CASE, 1` — no case, no entry (`:62-64`). **The run holds it** (`pokeagent/quest.py:425-430`, from `SlateportCity_ContestLobby` (1,5)).
3. Party/box space check via `CheckFreePokemonStorageSpace` (`:91-96`) — refuses when the party is 6 **and** every box is full.
4. `checkmoney 500` then `removemoney 500` (`:66-71`).
5. `special EnterSafariMode`; `setvar VAR_SAFARI_ZONE_STATE, 2`; `warp MAP_SAFARI_ZONE_SOUTHEAST, 255, 32, 33` (`:83-85`).

The four areas are `SafariZone_Southeast` / `_Southwest` / `_Northeast` / `_Northwest` (plus `SafariZone_RestHouse`), joined by ordinary map connections — SE connects **up** to NE and **left** to SW (`pret/data/maps/SafariZone_Southeast/map.json:13-23`). `pokeagent/dex.py:733-736` already maps the dataset's area labels onto those four map names.

### 3.2 Step limit, ball count, and where each lives

```c
void EnterSafariMode(void) {
    IncrementGameStat(GAME_STAT_ENTERED_SAFARI_ZONE);
    SetSafariZoneFlag();            // FLAG_SYS_SAFARI_MODE
    ClearAllPokeblockFeeders();
    gNumSafariBalls = 30;
    gSafariZoneStepCounter = 500;
}
```
— `pret/src/safari_zone.c:57-64`.

| Quantity | Symbol | Storage | Value |
|---|---|---|---|
| Balls | `gNumSafariBalls` | `EWRAM_DATA u8` — `pret/src/safari_zone.c:28` | **30** on entry (`:62`), 0 on exit (`:70`) |
| Steps | `gSafariZoneStepCounter` | `EWRAM_DATA u16` — `pret/src/safari_zone.c:29` | **500** on entry (`:63`), 0 on exit (`:71`) |
| "Am I in the zone" | `FLAG_SYS_SAFARI_MODE` | save-block flag | `GetSafariZoneFlag` / `SetSafariZoneFlag` / `ResetSafariZoneFlag`, `pret/src/safari_zone.c:42-54` |
| Script-visible state | `VAR_SAFARI_ZONE_STATE` | save-block var | 2 = inside (`…/scripts.inc:84`), 1 = leaving (`pret/data/scripts/safari_zone.inc:2,8`), 0 = out (`…/scripts.inc:14`) |

Neither counter is in the save block — both are EWRAM. **A savestate reload preserves them because it snapshots RAM, but nothing in `pokeagent/state.py` reads either symbol today** (grep for `gNumSafariBalls` / `gSafariZoneStepCounter` across `pokeagent/`, `trek.py`, `autopilot.py` returns nothing). Adding two `emu.u8`/`emu.u16` reads is the cheapest possible instrumentation and would let a driver retire deliberately instead of being ejected.

Decrement and expiry: `SafariZoneTakeStep` runs per step, decrements feeder counters and `gSafariZoneStepCounter`, and at 0 sets up `gUnknown_081C3448` (`pret/src/safari_zone.c:74-86`). That script is the "ding-dong, time's up" box → `EventScript_1C341B` → `setvar VAR_SAFARI_ZONE_STATE, 1`, `special ExitSafariMode`, `warp MAP_ROUTE121_SAFARI_ZONE_ENTRANCE, 255, 2, 5` (`pret/data/scripts/safari_zone.inc:7-11, 25-32`).

Out of balls: `sub_80C824C` (`pret/src/safari_zone.c:95-110`) returns to the field while `gNumSafariBalls != 0`; on `gBattleOutcome == 8` (`B_OUTCOME_NO_SAFARI_BALLS`, `pret/include/constants/battle.h:75`) it runs `gUnknown_081C340A` (`ExitSafariMode` + `setwarp` to the entrance, `pret/data/scripts/safari_zone.inc:1-5`); on `gBattleOutcome == 7` (`B_OUTCOME_CAUGHT`, `:74`) it runs the last-ball message `gUnknown_081C3459` (`…/safari_zone.inc:34-41`). The "SAFARI is over" branch in the ball script also fires only when `gNumSafariBalls` hits 0 (`pret/data/battle_scripts_2.s:91-93`). Manual retire: `SafariZoneRetirePrompt` → `gUnknown_081C342D`, a YES/NO box (`pret/src/safari_zone.c:88-91`, `pret/data/scripts/safari_zone.inc:15-24`). Remaining balls are visible in the START menu (`pret/src/start_menu.c:302-306`).

### 3.3 The battle type — the exact constant

```c
#define BATTLE_TYPE_SAFARI          0x0080
```
**`pret/include/constants/battle.h:54`.**

It is installed by plain **assignment**, not `|=`:

```c
gMain.savedCallback = sub_80C824C;
gBattleTypeFlags = BATTLE_TYPE_SAFARI;
```
**`pret/src/battle_setup.c:547-548`.**

So in a Safari encounter `gBattleTypeFlags == 0x0080` exactly: **`BATTLE_TYPE_WILD` (0x0004) is NOT set, and `BATTLE_TYPE_TRAINER` (0x0008) is NOT set.** The harness already decodes it — `pokeagent/state.py:55` maps `"safari": 0x0080` — so `d.state.battle().kinds == ("safari",)`, and `BattleSession.safari()` reads exactly that (`pokeagent/battle.py:377-387`).

### 3.4 Correcting the brief: `frame["wild"]` is **not** the closed door

The assignment says the catcher is blocked because `plan()` gates on `frame["wild"]` while Safari battles set `BATTLE_TYPE_SAFARI`. The code says otherwise, and getting this right saves the next session a wasted fix:

```python
"wild": not bool(flags & self.b["BATTLE_TYPE_TRAINER"]),
```
— `pokeagent/battle.py:470`. Since a Safari battle sets *only* `0x0080`, the trainer bit is clear, so **`frame["wild"]` is `True` in a Safari battle** and `Catcher.plan()`'s first guard (`pokeagent/catching.py:188`) passes. `Battle.wild` on the state side agrees for the same reason — `active and "trainer" not in kinds` (`pokeagent/state.py:84-86`).

**The actual closed door is `state.battle_ready()`.** It loops over *every* battler including the player's and refuses while any has species 0 or level 0:

```python
for i in range(max(1, self.emu.u8("gBattlersCount"))):
    ...
    if species == 0 or raw[b["level"]] == 0:
        return False
```
— `pokeagent/state.py:320-339`.

And in a Safari battle the engine deliberately zeroes the player-side battler:

```c
if ((gBattleTypeFlags & BATTLE_TYPE_SAFARI) && GetBattlerSide(gActiveBattler) == 0)
    MEMSET_ALT(&gBattleMons[gActiveBattler], 0, 0x58, i, ptr);
```
— `pret/src/battle_main.c:3711-3715`. (There is no player mon on the field at all — `battle_util.c:1287-1288` short-circuits, `battle_main.c:3922-3923` skips the send-out string.)

Therefore **`battle_ready()` can never return `True` inside the Safari Zone**, and both drivers spin their 80-iteration settle loop for nothing before proceeding on an unsettled read: `scripts/collect.py:328-331` and `scripts/play.py:1855-1858`. `collect.py:346-355` already records the symptom (`"[catch] no battle frame …"`).

**How to open the door, minimally and correctly:**

1. Make `battle_ready()` skip the player side when `gBattleTypeFlags & 0x0080` — the enemy battler (index 1) *is* populated, which is all a catch decision needs. `Tactics.read_battler` returns a `Combatant` with `species=0, name="-"` rather than `None` for the zeroed slot (`pokeagent/tactics.py:275-315`), so the frame builder itself does not crash on it.
2. Do **not** try to weaken the target. There are no moves: `frame["moves"]` is built from `me.moves`, all zero, so it comes back empty. The catch policy's HP-based timing (`THROW_BELOW = 0.34`, `pokeagent/catching.py:34`) can never trigger — the target is always at full HP. In a Safari battle the policy must **throw on turn 1**, or `GO NEAR` first (see below), and never call `inner(frame)`.
3. The throw plumbing already works: `throw_ball()` detects the Safari and routes to `safari_ball()` (`pokeagent/battle.py:1114-1120`), which picks cursor 0 on the four-option grid (`pokeagent/battle.py:1073-1094`). `flee()` routes to `safari_flee()`, cursor 3 (`pokeagent/battle.py:1219-1221`, `:1096-1112`).
4. `Catcher._pick_ball()` still reads the bag (`pokeagent/catching.py:311-328`) and will return a normal ball name; that is harmless because `throw_ball` ignores it in a Safari, but a `None` from an empty ball pocket would suppress the throw entirely — so a Safari-aware policy should bypass `_pick_ball` rather than depend on the bag. Note `balls_available()` (`pokeagent/catching.py:79-85`) also reads the bag, **not** `gNumSafariBalls`, so the `BALL_RESERVE = 3` guard (`:44`) is measuring the wrong pool inside the zone.

### 3.5 The four-option menu

The Safari controller installs its own action box, `bx_battle_menu_t6_2` (`pret/src/battle_controller_safari.c:186-263`), which drives the same `gActionSelectionCursor` 2×2 grid as the normal menu — which is why the harness's cursor driver works unchanged (`pokeagent/battle.py:51-65`). Layout `BALL / POKEBLOCK` over `GO NEAR / RUN`, i.e. cursor 0..3 (`pokeagent/battle.py:65`).

| Cursor | Emitted value (`…safari.c:197-212`) | Handler | Effect |
|---|---|---|---|
| 0 — BALL | 5 | `HandleAction_SafariZoneBallThrow`, `pret/src/battle_main.c:5566-5575` | `gNumSafariBalls--`, `gLastUsedItem = ITEM_SAFARI_BALL`, runs the ball-throw script |
| 1 — POKEBLOCK | 6 | `HandleAction_ThrowPokeblock`, `pret/src/battle_main.c:5577-5599` | `safariPkblThrowCounter` += 1 (cap 3); `safariFleeRate -= gUnknown_081FA70C[counter][flavour]`, floored at 1 |
| 2 — GO NEAR | 7 | `HandleAction_GoNear`, `pret/src/battle_main.c:5601-5626` | `safariCatchFactor += gUnknown_081FA71B[goNearCounter]` (cap 20); `safariFleeRate += gUnknown_081FA71F[goNearCounter]` (cap 20); counter += 1 up to 3 |
| 3 — RUN | 8 | `HandleAction_SafriZoneRun` | ends the encounter |

There is also a "watch carefully" action (`HandleAction_WatchesCarefully`, `pret/src/battle_main.c:5559-5565`) which is what the *enemy* side does each turn.

Tuning tables, read out of the ROM data rather than guessed (`pret/data/btl_attrs.s:380-391`):

```
gUnknown_081FA70C (pokeblock flee-rate reduction, [throwCount][flavour]):
    0,0,0 / 3,5,0 / 2,3,0 / 1,2,0 / 1,1,0
gUnknown_081FA71B (GO NEAR catch-factor bonus): 4, 3, 2, 1
gUnknown_081FA71F (GO NEAR flee-rate penalty): 4, 4, 4, 4
```

### 3.6 How a Safari catch is decided

Per-encounter initialisation (`pret/src/battle_main.c:3464-3468`):

```c
gBattleStruct->safariGoNearCounter   = 0;
gBattleStruct->safariPkblThrowCounter = 0;
gBattleStruct->safariCatchFactor = gBaseStats[species].catchRate * 100 / 1275;
gBattleStruct->safariFleeRate = 3;
```

Note `safariFleeRate` starts at a **constant 3**, not at the species' `safariZoneFleeRate` field (`pret/include/pokemon.h:305`, exposed by this repo as `SpeciesInfo.safari_flee_rate`, `pokeagent/names.py:76,197`). Fleeing is checked per turn as `Random() % 100 < safariFleeRate * 5` (`pret/src/battle_ai_script_commands.c:1668-1674`) — **15 % per turn at the default rate**, rising 20 points of flee-rate per GO NEAR.

The throw itself (`pret/src/battle_script_commands.c:9400-9510`):

```c
if (gLastUsedItem == ITEM_SAFARI_BALL)
    catch_rate = gBattleStruct->safariCatchFactor * 1275 / 100;   // :9402-9403
...
ball_multiplier = sBallCatchBonuses[gLastUsedItem - 2];           // :9450
odds = (catch_rate * ball_multiplier / 10)
     * (maxHP * 3 - hp * 2) / (3 * maxHP);                        // :9452
...
odds = Sqrt(Sqrt(16711680 / odds));
odds = 1048560 / odds;
for (shakes = 0; shakes < 4 && Random() < odds; shakes++) {}
if (shakes == 4) /* caught */                                     // :9486-9494
```

`ITEM_SAFARI_BALL` is id **5** (`pret/include/constants/items.h:11`), so `sBallCatchBonuses[3] = 15` (`pret/src/battle_script_commands.c:1033-1036`, commented "Ultra, Great, Poke, Safari").

**Consequences a driver must internalise:**

- The target is always at full HP, so the HP term collapses to `maxHP / (3·maxHP)` and `odds ≈ catch_rate / 2`.
- `safariCatchFactor` is capped at 20, so `catch_rate` maxes at `20 * 1275 / 100 = 255` and `odds` maxes around 127 — **never above 254**, so a Safari throw *always* goes through the four-shake routine. There is no guaranteed catch and no status to apply.
- GO NEAR is the only lever that raises the odds, and it costs flee rate 4-for-4 the first time, then 3, 2, 1 against a flat 4. **The first GO NEAR is the only clearly profitable one**; the fourth trades 1 point of catch factor for 4 of flee rate.
- POKEBLOCK only lowers flee rate, never raises catch odds — worth it only for a species you intend to spend several balls on, and the flavour index comes from the block thrown.
- `atkF1_trysetcaughtmondexflags` (`pret/src/battle_script_commands.c:9519-9532`) is what sets the **caught** dex bit, and it runs only on a successful catch. Seeing a Safari mon sets `seen`; only a catch moves `caught`. That is the 88-vs-22 gap in one sentence.

---

## 4. Things a run can walk past permanently

`pokeagent/missables.py` exists because a Crystal run reached Champion without FLY. Its module docstring names the Sapphire equivalents (`pokeagent/missables.py:9-24`): FLY behind a coordinate trigger on `VAR_ROUTE119_STATE == 0`, plus SURF, STRENGTH, FLASH, ROCK SMASH, CUT and DIVE, each behind "a chatty NPC nobody has to talk to".

Use `missables.missing_items(state, kind="key")` (`pokeagent/missables.py:582-623`) as the live audit: it walks every `giveitem` / `finditem` / `additem` / `bg_event` hidden item in the decomp, resolves each to a map and coordinates through `map.json`, and returns every source whose guarding flag is still clear. Key-item-ness comes from `gItems[].importance` read out of the ROM, not a hand-written list (`pokeagent/missables.py:539-549`). Known blind spot, reported rather than guessed: gives whose item is a *variable* (`runtime_gives()`, `pokeagent/missables.py:466+`).

Still outstanding for this save, by inspection of §1:

| Item | Where | Gate | Risk |
|---|---|---|---|
| SUPER ROD | `MossdeepCity_House3` (4,4) | none | Not in `PROLOGUE` at all. ~30 species live only in fishing slots. |
| HM05 FLASH | `GraniteCave_1F` (36,9) | none | Not in `PROLOGUE`. Blocks Victory Road B1F/B2F. |
| HM08 DIVE | `MossdeepCity_StevensHouse`, OnFrame | `VAR_STEVENS_HOUSE_STATE == 0` | The Seafloor Cavern climax force-sets that var to 1 and hides Steven — but it needs Dive first, so it cannot actually lock you out. |
| HM07 WATERFALL | `CaveOfOrigin_B3F` (6,5) | `FLAG_ITEM_CAVE_OF_ORIGIN_B3F_1` | An item ball; never disappears. |

### Physical blockers, and how to ask

`pokeagent/blockers.py` answers "why can't I get there" from map data in seconds. `FIELD_OBSTACLES` (`:31-35`) maps `S_BreakableRock` → ROCK SMASH, `S_CuttableTree` → CUT, `S_PushableBoulder` → STRENGTH. `Blockers.explain(dest_map=…)` / `explain(warp=…)` (`:239-262`) names the object in the way *and* whether anyone knows the HM that clears it. `to_warp()` (`:216-237`) checks both "something on the door's only approach" and "something in the corridor between here and it" — the second was the fix for a road held by two grunts that reported "no blockers found" 615 times.

When `explain` says `no walkable route from X`, that is usually honest and the answer is a *tool*, not a path: Dive for Sootopolis and the Seafloor Cavern, Waterfall for the Ever Grande plateau, FLASH for Victory Road's lower floors.

---

## 5. Ordered checklist

Do these in this order. After each, the listed capability or population becomes reachable.

**0. Before anything — two ungated detours, both cheap, both currently unmodelled.**

- [ ] **SUPER ROD** — `MossdeepCity_House3` (4,4), answer YES. → every `super_rod` slot in `WildTable` becomes live (`pokeagent/dex.py:590-606`); `METHOD_SURCHARGE["super_rod"] = 1.0` (`pokeagent/dex.py:117`) means the planner will start emitting those steps.
- [ ] **HM05 FLASH** — `GraniteCave_1F` (36,9). Teach it to a benched mon. → Victory Road B1F/B2F stop being dark.
- [ ] **Safari Zone sweep** — `Route121_SafariZoneEntrance`, stand on (8,4), YES, ¥500. 30 balls, 500 steps, four areas. → the sixteen-odd Safari-only species, behind **no badge at all**. Requires the §3.4 fix first, or every encounter will be declined for want of a battle frame.

**1. Mt. Pyre summit** — `MtPyre_Summit`, stand (23,7). → arms Slateport Harbor (`VAR_SLATEPORT_HARBOR_STATE = 1`); Mt. Pyre's own interior and exterior tables are already walkable.

**2. Slateport Harbor** — `SlateportCity_Harbor`, stand (8,12). → clears the two grunts blocking the Lilycove hideout door.

**3. Aqua Hideout** — Lilycove warp (70,5) → `AquaHideout_1F` → B1F → B2F, beat the grunt at (23,19). → `FLAG_EVIL_TEAM_ESCAPED_IN_SUBMARINE`; three item balls on B1F and one on B2F become collectable en route (`pret/data/maps/AquaHideout_B1F/map.json:51,77,103`, `…B2F/map.json:51`).

**4. Badge 7 — Tate & Liza** — `MossdeepCity_Gym`, four-switch puzzle, double battle. → `FLAG_BADGE07_GET`, which is what makes **DIVE usable** (`trek.py:1189-1194`).

**5. HM08 DIVE** — walk into `MossdeepCity_StevensHouse`. Teach it. → the four `Underwater*` maps and every `dive` encounter slot (`METHOD_SURCHARGE["dive"] = 1.5`, `pokeagent/dex.py:114`) become reachable: Underwater1 (Route 124), Underwater2 (126), Underwater3 (127), Underwater4 (128). Also: **Sootopolis City itself** becomes enterable via Underwater2 (45,65).

**6. Seafloor Cavern** — Route 128 → Dive → Underwater4 (38,26) → `SeafloorCavern_Entrance` → Room 9, coord trigger at x=17. → `FLAG_LEGEND_ESCAPED_SEAFLOOR_CAVERN`; Sootopolis' Cave of Origin opens (its *city* doors lock, see §2.1); you are warped out to Route 128 (38,22).

**7. HM07 WATERFALL** — `CaveOfOrigin_B3F` (6,5), on the way down. Teach it before the descent so it is not a second trip.

**8. Kyogre** — `CaveOfOrigin_B4F`, stand (9,13). L45, wild-battle rules — **this is a dex entry and a catchable legendary**; `pokeagent/dex.py` routes it as `ROUTE_STATIC`. → `FLAG_LEGENDARY_BATTLE_COMPLETED` unlocks every Sootopolis door including the gym at (31,32).

**9. Badge 8 — Wallace** — `SootopolisCity_Gym_1F`. → `FLAG_BADGE08_GET`, which makes **WATERFALL usable** (`trek.py:1197-1202`) and flips `nav.waterfall` on the next `_surf_sync` (`trek.py:1291-1302`). Route 126/127/128 waterfalls and the Ever Grande climb open.

**10. Sweep the water routes with the full toolkit.** With SURF + DIVE + WATERFALL + SUPER ROD all live, every `water`, `dive`, `old_rod`, `good_rod` and `super_rod` slot in `gWildMonHeaders` is reachable for the first time. Run `DexTarget.plan(state)` (`pokeagent/dex.py:1658-1711`) — it groups one cheapest step per missing species by map, and `sweep()` (`:1712-1717`) folds it into a route order. **Do this before the Elite Four**, not after: nothing in the post-game reopens anything, and the planner is at its most useful when every method is available and no story step is pending.

**11. Victory Road** — `EverGrandeCity` (18,41) or (18,27). Needs FLASH for B1F/B2F. Victory Road has its own encounter table and is a dex source in its own right.

**12. Elite Four** — `EverGrandeCity` (18,5); the door guards at (9,2)/(10,2) step aside and set `FLAG_ENTERED_ELITE_FOUR`. Sidney → Phoebe → Glacia → Drake → Steven. `FLAG_SYS_GAME_CLEAR` is the terminal flag; it also un-hides the S.S. Tidal at Slateport Harbor (`pret/data/maps/SlateportCity_Harbor/scripts.inc:10,12-14`), which is post-game content the dex objective may want.

### What is deliberately excluded from "achievable"

`DexTarget._partition()` (`pokeagent/dex.py:1093-1149`) drops slots with a sentence each, using the reason codes at `pokeagent/dex.py:70-74`:

| Reason | What it removes | Why |
|---|---|---|
| `version-exclusive` | the seven **Ruby**-only Hoenn entries | this cartridge is Sapphire; `paired_with` is read from `data/dex/sapphire.json` |
| `trade-evolution` / `needs-trade-partner` | species whose only route is `EVO_TRADE` / `EVO_TRADE_ITEM` | `Evolution.needs_trade`, `pokeagent/dex.py:253-255`; `_evolution_steps` refuses them at `:1534-1535` |
| `event-only` | **Jirachi**, **Deoxys**, and **Latios** (the Sapphire-side Eon twin) | `EVENT_METHODS`, `pokeagent/dex.py:78-82`; `_event_only_names`, `:1151-1154`. The engine agrees — `src/birch_pc.c:94-102` discounts exactly these when deciding whether the dex is "complete" |
| `unobtainable` | dataset-flagged slots with no route at all | `_any_route_exists`, `pokeagent/dex.py:1257-1262` |

Also excluded per-save rather than per-cartridge: `choice_locked()` (`pokeagent/dex.py:1156-1211`) removes the two starters not taken and any other one-of-N gift already spent. Read it live; do not assume.

---

## 6. Open items for the next session

1. **`state.battle_ready()` must special-case `BATTLE_TYPE_SAFARI`** (§3.4). Until then no Safari encounter reaches the catcher, and the Safari is sixteen-odd species behind no badge.
2. **A Safari branch in `Catcher.policy()`**: throw on turn 1 (optionally one GO NEAR first), never call the inner training policy, never consult `_pick_ball` or `balls_available` (both read the bag, not `gNumSafariBalls`).
3. **Two `StoryStep`s are missing from `PROLOGUE`**: SUPER ROD (`MossdeepCity_House3` (4,4), `flag_unset FLAG_RECEIVED_SUPER_ROD`, `choice="YES"`) and HM05 FLASH (`GraniteCave_1F` (36,9), `flag_unset FLAG_RECEIVED_HM05`).
4. **`PROLOGUE` stops at Kyogre** (`pokeagent/quest.py:492`); badge 8 is covered by `SPINE` but Victory Road and the Elite Four have no plan behind them (`pokeagent/quest.py:726-729`).
5. **Instrument the Safari counters**: `emu.u8("gNumSafariBalls")` and `emu.u16("gSafariZoneStepCounter")`. Two lines, and the difference between retiring on purpose and being thrown out.
