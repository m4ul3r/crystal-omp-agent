#!/usr/bin/env python
"""REGIROCK, REGICE and REGISTEEL -- the Sealed Chamber and the three ruins.

A `chain.py` leg: `--state PATH` is mutated IN PLACE, every species already
flagged CAUGHT is skipped, and nothing here assumes where the previous leg
parked the save.

WHAT THE CARTRIDGE ACTUALLY REQUIRES (read, not remembered)
-----------------------------------------------------------
1. **Getting under Route 134.** `Route134_MapScript1_1525BB` is an ON_RESUME
   `setdivewarp MAP_UNDERWATER_ROUTE134, 255, 8, 6`
   (pret/data/maps/Route134/scripts.inc:5-7), so a dive from ANY of the
   twelve diveable tiles at (59..62, 30..32) lands on the fixed cell (8,6).
   `Underwater_Route134` is 22 open cells with one warp, (8,8), into
   `Underwater_SealedChamber` (map.json:14-24).

2. **Surfacing into the chamber.** `Underwater_SealedChamber` has a
   MAP_SCRIPT_ON_DIVE_WARP that branches on the player's own coordinates
   (scripts.inc:5-19): at (12,44) it points the dive warp at
   `MAP_SEALED_CHAMBER_OUTER_ROOM 10,19`, and ANYWHERE ELSE it points it at
   `MAP_ROUTE134 60,31`. That single script is both the door in and the door
   out, and it is why the exit here steps OFF (12,44) before surfacing.

3. **DIG opens the inner room.** `ShouldDoBrailleDigEffect`
   (pret/src/braille_puzzles.c:27-42) is true only on
   SEALED_CHAMBER_OUTER_ROOM at x in {9,10,11} and y == 3; `DoBrailleDigEffect`
   (:44-56) rewrites six metatiles and sets FLAG_SYS_BRAILLE_DIG. Until then
   `SealedChamber_OuterRoom_MapScript1_15F0EB` covers the (10,2) warp with
   `METATILE_Cave_EntranceCover` on every map load (scripts.inc:16-27) -- and
   the SHIPPED layout has that warp OPEN, so nav must be told with
   `sync_grid()` both before and after.

4. **RELICANTH FIRST, WAILORD LAST.** `CheckRelicanthWailord`
   (braille_puzzles.c:58-69) is exactly::

       if (GetMonData(&gPlayerParty, MON_DATA_SPECIES2, 0) == SPECIES_RELICANTH)
           if (GetMonData(&gPlayerParty[gPlayerPartyCount - 1], ...) == SPECIES_WAILORD)

   -- party SLOT 0 and party SLOT count-1, nothing about the other slots. The
   braille sign at (10,4) of SealedChamber_InnerRoom runs
   `SealedChamber_InnerRoom_EventScript_15F1E8` (scripts.inc:4-36), which
   `specialvar VAR_RESULT, CheckRelicanthWailord` and only then sets
   FLAG_REGI_DOORS_OPENED.

5. **One puzzle per ruin, and they are three DIFFERENT puzzles.**

   * Desert Ruins -> REGIROCK: the STRENGTH field move at x in {9,10,11},
     y == 23 (`ShouldDoBrailleStrengthEffect`, braille_puzzles.c:71-84;
     `DoBrailleStrengthEffect` :86-99 sets FLAG_SYS_BRAILLE_STRENGTH).
     NOTE this is the PARTY-MENU field move, not the boulder A-press that
     `Driver.use_strength` drives -- `SetUpFieldMove_Strength`
     (pret/src/fldeff_strength.c:47-65) takes the braille branch BEFORE it
     looks for a pushable boulder.
   * Island Cave -> REGICE: no move at all, a WAIT.
     `IslandCave_EventScript_15EF59` (scripts.inc:44-51) calls
     `special DoBrailleWait`; `Task_BrailleWait` (braille_puzzles.c:153-204)
     counts 7200 frames -- two minutes -- and only then runs
     `S_OpenRegiceChamber`, which sets FLAG_SYS_BRAILLE_WAIT
     (scripts.inc:31-42). A BUTTON PRESS DURING THE COUNT IS FATAL to the
     attempt (case 2 destroys the task on the next press), so this stage is
     the one place in the harness that must advance frames with `.` only --
     `advance_scene` presses A and would kill it.
   * Ancient Tomb -> REGISTEEL: the FLY field move at exactly (8,25)
     (`ShouldDoBrailleFlyEffect`, braille_puzzles.c:101-110). Fly is normally
     refused indoors, and `SetUpFieldMove_Fly`
     (pret/src/pokemon_menu.c:832-846) checks the braille case FIRST, which is
     the only reason it is offered here at all.

   All three then open the same (8,20) warp into the inner room, arriving at
   (8,11), with the Regi object at (8,7).

6. **The Regi is a one-shot.** `DesertRuins_EventScript_15CB85`
   (scripts.inc:53-66) sets FLAG_HIDE_REGIROCK *before* the battle, so a Regi
   that faints or is fled from is gone for good. Catch rate is 3
   (pret/src/data/pokemon/base_stats.h) and the bag holds ULTRA BALLs, which
   is about 0.8% a throw at full HP -- so this SAVESCUMS: a scratch state is
   banked at the action menu and reloaded after a failed run of throws.
   That works because `VBlankIntr` calls `Random()` on EVERY frame
   (pret/src/main.c:328), so reloading and idling a different number of
   frames genuinely re-rolls the fight instead of replaying it.

    scripts/regis.py --state saves/regi.state
    scripts/regis.py --state saves/regi.state --stages chamber,regirock
"""

import argparse
import logging
import sys
import time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402
from pokeagent.dex import DexTarget  # noqa: E402
from pokeagent.menus import Menus  # noqa: E402
from pokeagent.storage import Storage  # noqa: E402
from pokeagent.teaching import Teacher  # noqa: E402
from share_grind import unwedge  # noqa: E402

log = logging.getLogger("regis")

BOX_SIZE = 30

#: `sPokemonMenuActions` row ids for the field moves, in the header's own
#: numbering (pret/include/pokemon_menu.h:6-33): POKEMENU_FIRST_FIELD_MOVE_ID
#: is 10 and the field-move table order follows `sPokeMenuFieldMoves`
#: (pret/src/pokemon_menu.c:154-161).
POKEMENU = {
    "CUT": 10, "FLASH": 11, "ROCK SMASH": 12, "STRENGTH": 13, "SURF": 14,
    "FLY": 15, "DIVE": 16, "WATERFALL": 17, "TELEPORT": 18, "DIG": 19,
    "SECRET POWER": 20, "MILK DRINK": 21, "SOFT BOILED": 22,
    "SWEET SCENT": 23,
}

#: The party the Sealed Chamber demands, in order. Slot 0 and the LAST slot
#: are the only ones `CheckRelicanthWailord` looks at; the three in between
#: carry the field moves the whole errand needs -- DIVE and STRENGTH (LOMBRE),
#: DIG (whoever gets taught TM28), SURF and FLY (PELIPPER).
LEAD = "RELICANTH"
TAIL = "WAILORD"

#: (ruin map, species, opener) -- see the module docstring for the citations.
RUINS = (
    ("DesertRuins", "REGIROCK", "strength", "FLAG_SYS_BRAILLE_STRENGTH"),
    ("IslandCave", "REGICE", "wait", "FLAG_SYS_BRAILLE_WAIT"),
    ("AncientTomb", "REGISTEEL", "fly", "FLAG_SYS_BRAILLE_FLY"),
)

#: A route is not a Fly destination, so the last hop is walked from a town
#: the map's own connections name (pret/data/maps/*/map.json connections).
GATEWAYS = {
    # NOT SlateportCity, even though it is the map's own left-hand
    # connection: Route134's west third is sealed off by the currents (see
    # `ride_plan`). `nav.route("MossdeepCity", "Route134")` is the eastern
    # chain -- Route127/128/129/130/131, Pacifidlog, Route132/133 -- and
    # PACIFIDLOG IS NOT A FLY TARGET on this line
    # (FLAG_VISITED_PACIFIDLOG_TOWN is clear), so Mossdeep is the only stop
    # that reaches the right side of the map.
    "Route134": ("MossdeepCity",),
    "Route105": ("PetalburgCity", "DewfordTown"),
    "Route111": ("MauvilleCity", "FallarborTown", "LavaridgeTown"),
    "Route120": ("FortreeCity", "LilycoveCity"),
    "Route114": ("FallarborTown", "VerdanturfTown"),
}

#: Indoor maps and the OUTDOOR warp tile that opens them. `travel` answers
#: "no walkable route" for every one of these -- nav will not plan a journey
#: that has to step through a door on a map it is not standing on -- and Fly
#: answers "not a region-map fly target", so the route and the door are the
#: two hops this leg has to make itself.
#: (pret/data/maps/<route>/map.json warp_events, matched by dest_map)
DOORS = {
    "Route114_FossilManiacsHouse": ("Route114", 29, 5),
    "DesertRuins": ("Route111", 29, 87),
    "IslandCave": ("Route105", 9, 20),
    "AncientTomb": ("Route120", 7, 55),
}


# ---- small shared plumbing ----------------------------------------------

def dex(d) -> DexTarget:
    return DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)


def caught_ids(d, target) -> set:
    caught, _seen = target.dex_flags(d.state)
    return caught


def registered(d, target, species: str) -> bool:
    """Is `species` CAUGHT right now, off the live dex bitfields?

    `dex_flags` reports NATIONAL dex numbers, and the species->natdex table
    lives on `DexTarget.evolutions` (`gSpeciesToNationalPokedexNum`,
    pokeagent/dex.py:357-360) -- the same read `underwater.py:94-106` uses.
    """
    sid = d.consts.species.get("SPECIES_" + species.upper())
    if not sid:
        return False
    return target.evolutions.natdex(sid) in caught_ids(d, target)


def flee_all(frame):
    return "flee"


def bank(d, path):
    if path:
        d.save(path)


def surf_on(d) -> bool:
    if (d.field_moves() or {}).get("SURF"):
        d.nav.surfing = True
        return True
    return False


def emerge(d, tries: int = 3) -> bool:
    """Get off the seafloor, because nothing upstream can.

    `chain.normalize` and `Driver.flight.step_outside` both answer "indoors
    and could not step outside" on an UNDERWATER map --
    `Overworld_MapTypeAllowsTeleportAndFly` refuses MAP_TYPE_UNDERWATER
    (pret/src/overworld.c) and there is no warp to walk through. The
    canonical save this leg inherits is parked on Underwater1 at (10,33),
    which is where the RELICANTH hunt left it, so EVERY journey here starts
    with a surfacing that the harness has no verb for.
    """
    for _ in range(tries):
        if not d.underwater():
            return True
        here = d.map_name()
        if d.dive():
            log.info("  surfaced onto %s %s", d.map_name(), d.pos())
            continue
        gates = [g for g in d.nav.dive_gates(here, "emerge")]
        if not gates:
            log.info("  %s has no emerge gate", here)
            return False
        gates.sort(key=lambda g: abs(g[0] - d.pos()[0]) + abs(g[1] - d.pos()[1]))
        for gx, gy in gates[:6]:
            try:
                if not d.goto(gx, gy, on_battle="fight"):
                    continue
            except TravelInterrupted:
                d.fight(policy=flee_all)
                d.advance_scene(40_000)
                continue
            if d.dive():
                break
        if not d.underwater():
            log.info("  surfaced onto %s %s", d.map_name(), d.pos())
            return True
    return not d.underwater()


def reach(d, map_name: str, budget: float = 480.0) -> bool:
    """Fly as close as the region map allows, walk the route, take the door."""
    if d.map_name() == map_name:
        return True
    door = DOORS.get(map_name)
    if door:
        route, dx, dy = door
        if not reach(d, route, budget):
            return False
        d.sync_grid()
        for _ in range(2):
            if d.take_warp(dx, dy):
                break
            log.info("  door (%d,%d) on %s refused: %s", dx, dy, route,
                     d.last_warp_reason)
            d.sync_grid()
        return d.map_name() == map_name
    unwedge(d)
    surf_on(d)
    if d.underwater():
        emerge(d)
    try:
        if not d.flight.flyable_here():
            d.flight.step_outside()
        if d.fly_to(map_name):
            log.info("  flew to %s", d.map_name())
            return True
    except Exception as exc:  # noqa: BLE001
        log.debug("  fly %s: %s", map_name, str(exc)[:90])
    surf_on(d)
    # A ROUTE IS WALKED FROM A TOWN THAT TOUCHES IT, and which town matters:
    # `travel` plans from wherever the save happens to be parked and the
    # cheapest wrong guess costs minutes of pathing before it answers "no
    # walkable route". So try from here first, then from each town the map's
    # own connections name.
    for town in (None,) + GATEWAYS.get(map_name, ()):
        if town is not None:
            try:
                if not d.flight.flyable_here():
                    d.flight.step_outside()
                if not d.fly_to(town):
                    continue
                log.info("  retrying %s from %s", map_name, town)
            except Exception as exc:  # noqa: BLE001
                log.debug("  fly %s: %s", town, str(exc)[:90])
                continue
        try:
            d.travel(map_name, on_battle="fight", budget_s=budget)
        except TravelInterrupted:
            d.fight(policy=flee_all)
            d.advance_scene(40_000)
            try:
                d.travel(map_name, on_battle="fight", budget_s=budget)
            except Exception as exc:  # noqa: BLE001
                log.info("  travel %s: %s", map_name, str(exc)[:120])
        except Exception as exc:  # noqa: BLE001
            log.info("  travel %s: %s", map_name, str(exc)[:120])
        if d.map_name() == map_name:
            return True
    return d.map_name() == map_name


# ---- the party-menu field moves -----------------------------------------

def field_move_holder(d, move: str):
    """Party indexes whose moveset contains `move`, cheapest check first."""
    mid = d.consts.moves.get("MOVE_" + move.replace(" ", "_"))
    out = []
    for i, mon in enumerate(d.state.party()):
        if not mon.is_egg and mid in (mon.moves or ()):
            out.append(i)
    return out


def use_field_move(d, move: str, tries: int = 3) -> bool:
    """START -> POKeMON -> a mon that knows `move` -> that move.

    `Driver` has no path to a party-menu field move at all: `use_strength`
    drives the boulder A-press script and `dive`/`climb_waterfall` drive the
    overworld A button, none of which reach `SetUpFieldMove_*`. All three
    Regi puzzles and the Sealed Chamber's DIG are party-menu moves, so this
    is the missing surface.

    Nothing here counts rows. `sPokeMenuOptionsOrder` / `sPokeMenuOptionsNo`
    (pret/src/pokemon_menu.c:113-115, filled by `sub_8089A8C` at :193-229) ARE
    the option list the popup drew, and `gLastFieldPokeMenuOpened` says which
    mon it belongs to -- so the right row is read off the engine and the mon
    is verified rather than trusted. `teaching._pick_party_member`'s own
    docstring records that asking for slot N can land on slot N+1.
    """
    want = POKEMENU[move.upper()]
    holders = field_move_holder(d, move)
    if not holders:
        log.info("  no party member knows %s", move)
        return False
    menus = Menus(d.emu, d.state)
    teacher = Teacher(d)
    order_addr = d.emu.resolve("sPokeMenuOptionsOrder")
    for _ in range(tries):
        # Try every slot: what matters is landing on a popup that OFFERS the
        # move, not which index the picker thinks it moved to.
        for slot in list(holders) + [i for i in range(len(d.state.party()))
                                     if i not in holders]:
            d.close_menus()
            d.settle(300)
            d.emu.run_sequence("START:6 .:90")
            d.settle(600)
            if not menus.select_index(1):            # POKeMON
                continue
            d.settle(900)
            if not teacher._pick_party_member(slot):
                continue
            d.settle(700)
            count = d.emu.u8("sPokeMenuOptionsNo")
            if not 0 < count <= 8:
                d.close_menus()
                continue
            rows = list(d.emu.read(order_addr, count))
            if want not in rows:
                d.close_menus()
                continue
            who = d.emu.u8("gLastFieldPokeMenuOpened")
            log.info("  %s offered by party slot %d (rows %s)", move, who, rows)
            if not menus.select_index(rows.index(want)):
                d.close_menus()
                continue
            return True
    log.info("  could not reach %s in the party menu", move)
    return False


# ---- stage: TM28 and a DIG knower ---------------------------------------

def has_item(d, name: str) -> bool:
    bag = d.state.bag() or {}
    for pocket in bag.values():
        if isinstance(pocket, dict) and pocket.get(name):
            return True
    return False


def ensure_tm28(d) -> bool:
    """TM28 DIG is a one-off gift, and the canonical line never collected it.

    `Route114_FossilManiacsHouse_EventScript_15C1C3` (scripts.inc:9-20) gives
    ITEM_TM28_DIG once and sets FLAG_RECEIVED_TM28; the giver is the only
    object on the map, OBJ_EVENT_GFX_LITTLE_BOY_1 at (3,2) (map.json:12-27).
    """
    if has_item(d, "TM28"):
        return True
    if d.state.flag("FLAG_RECEIVED_TM28"):
        log.info("  FLAG_RECEIVED_TM28 is set but TM28 is not in the bag")
        return False
    if not reach(d, "Route114_FossilManiacsHouse"):
        log.info("  could not reach the Fossil Maniac's house (at %s)",
                 d.map_name())
        return False
    for _ in range(3):
        if d.talk_to(3, 2):
            d.advance_scene(60_000)
            d.close_menus()
        if has_item(d, "TM28"):
            log.info("  TM28 collected")
            return True
        d.settle(400)
    log.info("  the boy did not hand over TM28 (%s)", d.last_talk_reason)
    return has_item(d, "TM28")


def ensure_dig(d) -> bool:
    """A party member that knows DIG, so the Outer Room can be opened."""
    if field_move_holder(d, "DIG"):
        return True
    if not ensure_tm28(d):
        return False
    teacher = Teacher(d)
    tm28 = teacher._item_id("TM28")
    # Prefer a member that is NOT carrying a field move this errand needs and
    # is not one of the two the chamber checks by species.
    reserved = {"PELIPPER", "LOMBRE", "LUDICOLO", LEAD, TAIL}
    cands = []
    for i, mon in enumerate(d.state.party()):
        if mon.is_egg:
            continue
        name = d.names.species(mon.species).upper()
        try:
            if not d.names.learns_tm(mon.species, tm28):
                continue
        except Exception:  # noqa: BLE001
            continue
        cands.append((name in reserved, -(mon.level or 0), i, name))
    if not cands:
        log.info("  nobody in the party can learn TM28")
        return False
    cands.sort()
    _res, _lvl, index, name = cands[0]
    mon = d.state.party()[index]
    label = mon.nickname or name
    log.info("  teaching TM28 DIG to %s (slot %d)", label, index)
    ok = teacher.teach("TM28", label)
    log.info("  teach -> %s (%s)", ok, getattr(teacher, "last_reason", None))
    return bool(field_move_holder(d, "DIG"))


# ---- stage: the party the chamber demands -------------------------------

def party_species(d) -> list:
    return [d.names.species(m.species).upper()
            for m in d.state.party() if not m.is_egg]


def boxed_slot(d, target, species: str):
    """Flat box slot holding `species`, re-read on every call.

    Slots move: a withdraw empties one and a deposit fills the first free one,
    so a position cached before an earlier operation is a position that no
    longer means anything.
    """
    for slot, boxed in target.boxed():
        if d.names.species(boxed.species).upper() == species:
            return slot
    return None


#: Pokemon Centres whose PC can actually be USED. `Storage.open` walks to
#: `pc_cells()[0]` and stands one tile below it, and every Centre 1F puts its
#: PC at (10,1) -- but FALLARBOR TOWN parks a stationary
#: MOVEMENT_TYPE_FACE_UP object on (10,2)
#: (pret/data/maps/FallarborTown_PokemonCenter_1F/map.json), so the approach
#: cell is permanently occupied and `goto` answers "stalled 12x at (10,3)
#: heading for (10,2)" forever. `share_grind.to_center` heals at the NEAREST
#: Centre, which from Route 114 is exactly that one. Audited across all
#: fifteen Centre_1F maps, the other blocked pair is (11,2) in Petalburg and
#: Rustboro, which does not sit on the approach; these four are clear.
PC_TOWNS = ("SlateportCity", "LilycoveCity", "MossdeepCity", "MauvilleCity")


def pc_approach_clear(d):
    """A `Storage` whose PC this driver can actually stand in front of."""
    st = Storage(d)
    cells = st.pc_cells()
    if not cells:
        return None
    x, y = cells[0]
    taken = {(o["x"], o["y"]) for o in d.live_npcs() if not o["player"]}
    if (x, y + 1) in taken:
        log.info("  %s has an NPC on the PC approach %s", d.map_name(),
                 (x, y + 1))
        return None
    return st


def to_pc(d):
    if d.in_pokecenter():
        st = pc_approach_clear(d)
        if st is not None:
            return st
    for town in PC_TOWNS:
        if not reach(d, town):
            continue
        try:
            d.heal_at_nearest_center()
        except Exception as exc:  # noqa: BLE001
            log.info("  heal at %s: %s", town, str(exc)[:90])
        if not d.in_pokecenter():
            continue
        st = pc_approach_clear(d)
        if st is not None:
            log.info("  PC at %s", d.map_name())
            return st
    return None


def build_party(d, target) -> bool:
    """Make the party exactly LEAD ... TAIL, verified by species.

    Built with the STORAGE system rather than the party menu's SWITCH: a
    deposit compacts `gPlayerParty` and a withdraw fills the first free slot,
    so "empty to one, withdraw in order" produces a known order, while
    `teaching._pick_party_member`'s own comments record that the party
    picker's index does not reliably map to the slot the engine acts on.
    """
    have = party_species(d)
    if have and have[0] == LEAD and have[-1] == TAIL:
        log.info("  party already %s", have)
        return True
    wanted = [LEAD]
    for name in ("LOMBRE", "LUDICOLO"):
        if name in have or boxed_slot(d, target, name) is not None:
            wanted.append(name)
            break
    # BY SPECIES, NOT BY INDEX. `field_move_holder` numbers raw party slots
    # while `party_species` drops eggs, so pairing the two silently names the
    # wrong mon whenever an egg is aboard.
    for mon in d.state.party():
        if mon.is_egg:
            continue
        if d.consts.moves.get("MOVE_DIG") in (mon.moves or ()):
            wanted.append(d.names.species(mon.species).upper())
            break
    if "PELIPPER" in have:
        wanted.append("PELIPPER")
    wanted.append(TAIL)
    # de-dup, keep order, LEAD first and TAIL last
    seen, order = set(), []
    for name in wanted:
        if name not in seen:
            seen.add(name)
            order.append(name)
    log.info("  target party order: %s", order)

    st = to_pc(d)
    if st is None:
        log.info("  no usable PC reached (at %s)", d.map_name())
        return False
    # 1. Empty the party down to a single member. `deposit` refuses the last
    #    one on purpose, so slot 1 is the one that always exists to give up.
    for _ in range(6):
        if len(st.party_names()) <= 1:
            break
        if not st.deposit(1):
            log.info("  deposit refused: %s", getattr(st, "last_reason", "?"))
            st.close()
            return False
    # 2. Withdraw the lead, then send the leftover away so the lead compacts
    #    into slot 0.
    for name in order:
        slot = boxed_slot(d, target, name)
        if slot is None:
            log.info("  %s is not in the boxes", name)
            continue
        if not st.withdraw(slot // BOX_SIZE, slot % BOX_SIZE):
            log.info("  withdraw %s refused: %s", name,
                     getattr(st, "last_reason", "?"))
            st.close()
            return False
        if name == order[0]:
            # the survivor of step 1 is still sitting in slot 0
            if not st.deposit(0):
                log.info("  could not clear slot 0: %s",
                         getattr(st, "last_reason", "?"))
                st.close()
                return False
    st.close()
    have = party_species(d)
    log.info("  party is now %s", have)
    return bool(have) and have[0] == LEAD and have[-1] == TAIL


# ---- stage: the Sealed Chamber ------------------------------------------

def swim_to(d, x: int, y: int, tries: int = 14) -> bool:
    """Step-walk the seafloor, answering the encounters it throws up."""
    here = d.map_name()
    for _ in range(tries):
        if d.pos() == (x, y):
            return True
        path = d.nav.find_path(here, d.pos(), (x, y), start_z=d.elevation())
        if not path:
            log.info("  no path %s %s -> (%d,%d)", here, d.pos(), x, y)
            return False
        for key in path:
            before = d.pos()
            try:
                moved = d.step_dir(key) and d.pos() != before
            except TravelInterrupted:
                moved = False
            if moved:
                continue
            if d.state.in_battle() or d.scene_active():
                try:
                    d.fight(policy=flee_all)
                except Exception as exc:  # noqa: BLE001
                    log.info("  seafloor fight: %s", str(exc)[:90])
                d.advance_scene(40_000)
                unwedge(d)
                break
            log.info("  refused %s at %s: %s", key, d.pos(),
                     d.last_step_reason)
            return False
    return d.pos() == (x, y)


# ---- riding the Route 132/133/134 currents -------------------------------

#: `MB_UNUSED_EASTWARD_CURRENT` .. `MB_SOUTHWARD_CURRENT`
#: (pret/include/constants/metatile_behaviors.h:84-87) and the direction key
#: each one drags the player.
CURRENT_PUSH = {0x50: "R", 0x51: "L", 0x52: "U", 0x53: "D"}

STEPS = ("U", "D", "L", "R")
HOLD = {"U": "UP", "D": "DOWN", "L": "LEFT", "R": "RIGHT"}


def ride_plan(d, map_name, start, targets, start_z=None, banned=()):
    """`[(direction, landing_cell)]` from `start` to any of `targets`.

    NAV CANNOT PLAN THIS AND NEITHER CAN `goto`. Route 134 is a current maze:
    `TryDoMetatileBehaviorForcedMovement` (pret/src/field_player_avatar.c:
    329-336) runs BEFORE the keypad is read, so a step onto a current tile is
    not a step -- the player is carried until the flow ends. nav's grid knows
    the behaviour but `find_path` treats every open cell as a normal step, so
    `goto (60,30)` answered "stalled 12x at (17,9)" five times over: east of
    (17,9) is MB_WESTWARD_CURRENT and every step onto it washed straight back.

    So the search is over REST CELLS, one keypress per edge, each edge ending
    wherever the slide stops. A current whose push is BLOCKED counts as a rest
    cell, because `DoForcedMovement` (:368-395) returns 0 on a collision and
    `player_step` then falls through to the keypad -- the input comes back.

    EVERY STEP GOES THROUGH `nav.step`, NOT THROUGH `collision == 0`. The
    first version tested the collision byte alone and planned
    (77,5) -> U -> (50,4) on Route133; the step never happened, four replans
    in a row logged "ride drifted: pressed U, expected (50,4), at (77,5)",
    and the reason is elevation -- `nav.step` mirrors `GetCollisionAtCoords`
    including the elevation rule and the ledge/rail/slope overrides that a
    collision byte knows nothing about.

    Measured with this model: from the Slateport seam only 222 rest cells are
    reachable and max x is 24, so the dive tiles at (59..62, 30..32) are
    UNREACHABLE from the west at any cost.
    """
    nav = d.nav

    def behavior(x, y):
        c = nav.cell(map_name, x, y)
        return None if c is None else c.behavior

    def slide(x, y, z, limit=200):
        for _ in range(limit):
            key = CURRENT_PUSH.get(behavior(x, y))
            if key is None:
                return (x, y, z)
            nxt = nav.step(map_name, x, y, z, key)
            if nxt is None:
                return (x, y, z)      # blocked push: the input comes back
            x, y, z = nxt
        return (x, y, z)

    if start_z is None:
        cell = nav.cell(map_name, *start)
        start_z = cell.elevation if cell is not None else 3
    want = {tuple(t)[:2] for t in targets}
    begin = slide(start[0], start[1], start_z)
    if begin[:2] in want:
        return []
    prev = {begin: None}
    queue = deque([begin])
    found = None
    while queue and found is None:
        cur = queue.popleft()
        for key in STEPS:
            if ((cur[0], cur[1]), key) in banned:
                continue
            nxt = nav.step(map_name, cur[0], cur[1], cur[2], key)
            if nxt is None:
                continue
            land = slide(*nxt)
            if land in prev:
                continue
            prev[land] = (cur, key)
            if land[:2] in want:
                found = land
                break
            queue.append(land)
    if found is None:
        return None
    legs = []
    node = found
    while prev[node] is not None:
        parent, key = prev[node]
        legs.append((key, node[:2]))
        node = parent
    legs.reverse()
    return legs


def ride_press(d, key, settle_rounds: int = 40) -> tuple:
    """One STEP in `key`, then wait out however far the current carries us.

    THE FIRST PRESS ONLY TURNS. `Driver.step_dir` (trek.py:741-746) documents
    it and presses again; a bare `run_sequence("UP:8")` does not, which is why
    the first version of this reported "ride drifted: pressed U, expected
    (50,4), at (77,5)" -- the player had turned north and gone nowhere, and
    the plan was thrown away as wrong when the input was.
    """
    try:
        d.step_dir(key)
    except TravelInterrupted:
        return d.pos()
    last = d.pos()
    stable = 0
    for _ in range(settle_rounds):
        d.emu.run_sequence(".:16")
        now = d.pos()
        if now == last and not d.moving():
            stable += 1
            if stable >= 2:
                break
        else:
            stable = 0
        last = now
    return d.pos()


def ride_to(d, targets, budget: int = 320) -> bool:
    """Press toward `targets` and ACCEPT wherever the current puts you.

    THE SLIDE CANNOT BE PREDICTED FROM THE GRID, and two models were tried
    before this one. `player_step` runs `TryDoMetatileBehaviorForcedMovement`
    on every frame whether or not a key is held
    (pret/src/field_player_avatar.c:265-278), so a current tile is not a cell
    you step onto and leave -- it owns the avatar for as long as frames pass,
    including the frames a caller spends settling. Planned routes therefore
    desynced immediately: the log reads "pressed U at (77,5) ... got (77,5)",
    then the same for (76,5), (75,5), (74,5), (73,5) -- the player was being
    carried one tile west between the press and the read, and every replan
    proposed the same first leg from a cell it was no longer on.

    So no plan is kept. Each press is chosen greedily against the Manhattan
    distance to the nearest target, the drift is accepted, and progress is
    judged only by getting closer. That is the one thing the currents cannot
    invalidate, and it is why the flow direction matters: on Routes 132/133/134
    it runs WEST and SOUTH, which is where every target of this errand lies.
    """
    want = {tuple(t) for t in targets}
    best = None
    stuck = 0
    for _ in range(budget):
        pos = d.pos()
        if pos in want:
            return True
        if d.in_battle() or d.state.in_battle() or d.scene_active():
            try:
                d.fight(policy=flee_all)
            except Exception as exc:  # noqa: BLE001
                log.info("  ride fight: %s", str(exc)[:90])
            d.advance_scene(40_000)
            unwedge(d)
            continue
        tx, ty = min(want, key=lambda t: abs(t[0] - pos[0]) + abs(t[1] - pos[1]))
        dist = abs(tx - pos[0]) + abs(ty - pos[1])
        if best is None or dist < best:
            best, stuck = dist, 0
        else:
            stuck += 1
            if stuck > 60:
                log.info("  ride gave up %s away from %s at %s", best,
                         (tx, ty), pos)
                return False
        order = []
        if tx < pos[0]:
            order.append("L")
        elif tx > pos[0]:
            order.append("R")
        if ty < pos[1]:
            order.append("U")
        elif ty > pos[1]:
            order.append("D")
        order += [k for k in ("L", "D", "U", "R") if k not in order]
        for key in order:
            before = d.pos()
            if ride_press(d, key) != before:
                break
    return d.pos() in want


def dive_gate_attempt(d) -> bool:
    """From wherever we stand on Route134, get onto the seafloor."""
    surf_on(d)
    gates = list(d.nav.dive_gates("Route134", "dive"))
    if not gates:
        log.info("  Route134 has no dive gate")
        return False
    log.info("  on Route134 at %s, surfing=%s, %d dive gates",
             d.pos(), d.is_surfing(), len(gates))
    if not ride_to(d, gates):
        log.info("  could not ride to a dive gate (at %s)", d.pos())
        return False
    if d.dive():
        return True
    log.info("  dive refused at %s: %s", d.pos(), d.last_field_reason)
    for gx, gy in gates:
        if d.pos() == (gx, gy):
            continue
        if not ride_to(d, [(gx, gy)], tries=3):
            continue
        if d.dive():
            return True
        log.info("  dive refused at (%d,%d): %s", gx, gy,
                 d.last_field_reason)
    return False


#: `nav.route("MossdeepCity", "Route134")`, verbatim. Walked one hop at a
#: time: `travel` across nine sea maps in one call is a single budget with no
#: partial credit, and every one of these maps can stall its own goto on a
#: current -- so each hop gets its own attempt and its own rider fallback.
EAST_CHAIN = ("Route127", "Route128", "Route129", "Route130", "Route131",
              "PacifidlogTown", "Route132", "Route133", "Route134")


def seam_toward(d, map_name, dest_map):
    """`([seam cells], direction)` for the connection to `dest_map`."""
    grid = d.nav.grid(map_name)
    height = len(grid)
    width = len(grid[0]) if height else 0
    for edge in d.nav.exits(map_name):
        if edge.get("kind") != "connection" or edge.get("dest") != dest_map:
            continue
        side = edge.get("direction")
        if side == "L":
            return [(0, y) for y in range(height)], "L"
        if side == "R":
            return [(width - 1, y) for y in range(height)], "R"
        if side == "U":
            return [(x, 0) for x in range(width)], "U"
        if side == "D":
            return [(x, height - 1) for x in range(width)], "D"
    return None, None


def dive_entry_rows(d):
    """Route133 seam ROWS whose Route134 side can reach the dive tiles.

    WHICH ROW YOU CROSS ON DECIDES THE WHOLE ERRAND. Crossing the
    Route133->Route134 seam anywhere lands the player at x=79 of Route134 and
    the westward current immediately carries them off; from row 7 the flow
    parks them at (60,7), whose rest-cell component does NOT contain a single
    dive tile -- measured, and exactly how one run reached Route134 and still
    reported "no current-aware plan from (60, 7)". Only rows 26..34 drift into
    the pocket around (59..62, 30..32). Computed here rather than tabulated,
    off the same `ride_plan` the walk uses.
    """
    grid = d.nav.grid("Route134")
    height = len(grid)
    width = len(grid[0]) if height else 0
    gates = list(d.nav.dive_gates("Route134", "dive"))
    rows = []
    for y in range(height):
        if grid[y][width - 1].collision:
            continue
        if ride_plan(d, "Route134", (width - 1, y), gates) is not None:
            rows.append(y)
    return rows


def ride_chain(d, deadline, state_path=None) -> bool:
    """Mossdeep -> Route134's dive pocket, one map hop at a time."""
    if d.map_name() not in EAST_CHAIN and not reach(d, "MossdeepCity"):
        log.info("  could not reach MossdeepCity (at %s)", d.map_name())
        return False
    surf_on(d)
    for i, nxt in enumerate(EAST_CHAIN):
        here = d.map_name()
        if here == nxt:
            continue
        if here in EAST_CHAIN and EAST_CHAIN.index(here) > i:
            continue                      # already past this hop
        if time.time() > deadline:
            log.info("  out of time on the chain at %s", here)
            return False
        for _ in range(2):
            cells, side = seam_toward(d, d.map_name(), nxt)
            if nxt == "Route134" and cells:
                rows = set(dive_entry_rows(d))
                picked = [c for c in cells if c[1] in rows]
                if picked:
                    log.info("  crossing into Route134 on rows %s",
                             sorted(rows)[:12])
                    cells = picked
                if ride_to(d, cells):
                    for _ in range(4):
                        ride_press(d, side)
                        if d.map_name() == nxt:
                            break
                if d.map_name() == nxt:
                    break
                log.info("  could not reach the Route134 seam rows from %s",
                         d.pos())
            try:
                d.travel(nxt, on_battle="fight", budget_s=300)
            except TravelInterrupted:
                d.fight(policy=flee_all)
                d.advance_scene(40_000)
            except Exception as exc:  # noqa: BLE001
                log.info("  travel %s: %s", nxt, str(exc)[:110])
            if d.map_name() == nxt:
                break
            cells, side = seam_toward(d, d.map_name(), nxt)
            if not cells:
                log.info("  %s has no connection to %s", d.map_name(), nxt)
                break
            if ride_to(d, cells):
                for _ in range(4):
                    ride_press(d, side)
                    if d.map_name() == nxt:
                        break
            if d.map_name() == nxt:
                break
        if d.map_name() != nxt:
            log.info("  chain stalled on %s %s heading for %s",
                     d.map_name(), d.pos(), nxt)
            return False
        log.info("  chain: %s %s", d.map_name(), d.pos())
        # BANK EVERY HOP. Nine sea maps of encounters is ten minutes of wall
        # clock; a failure on the last seam should not charge that again.
        if state_path and nxt != "Route134":
            bank(d, state_path)
    return d.map_name() == "Route134"


def descend_to_chamber(d, state_path, deadline) -> bool:
    """Route 134 -> the seafloor -> SealedChamber_OuterRoom.

    ROUTE 134 IS ENTERED FROM THE EAST, and that is measured rather than
    preferred. Walking on from Slateport puts the player at x~2 and every
    step east then answered "blocked moving R" -- (19,8), (17,9), (15,10),
    (13,11), (17,13) in one run -- because the flow on Routes 132/133/134
    runs WEST. The rest-cell search in `ride_plan` puts a number on it: from
    the Slateport seam only 222 rest cells are reachable and max x is 24,
    while the dive tiles sit at (59..62, 30..32). From the Route133 seam they
    are nine keypresses away.
    """
    if d.map_name() in ("SealedChamber_OuterRoom", "SealedChamber_InnerRoom"):
        return True
    if d.map_name() != "Underwater_SealedChamber":
        if d.map_name() != "Underwater_Route134":
            for attempt in range(3):
                if d.map_name() == "Route134" and dive_gate_attempt(d):
                    break
                if d.map_name() == "Route134":
                    # A REST CELL WITH NO PLAN IS A DEAD END, not a detour:
                    # the currents that parked us here run west and there is
                    # no way back east across them. Fly out and re-enter the
                    # map on a row that works.
                    log.info("  leaving Route134 to re-enter on a good row")
                    if not reach(d, "MossdeepCity"):
                        return False
                if time.time() > deadline:
                    log.info("  out of time before the descent")
                    return False
                if not ride_chain(d, deadline, state_path):
                    return False
            if d.map_name() != "Underwater_Route134":
                log.info("  still on %s %s after diving (surfing=%s)",
                         d.map_name(), d.pos(), d.is_surfing())
                return False
            log.info("  underwater at %s %s", d.map_name(), d.pos())
            bank(d, state_path)
        # (8,8) is the only warp out of Underwater_Route134.
        if not d.take_warp(8, 8):
            log.info("  warp (8,8) refused: %s", d.last_warp_reason)
            return False
        log.info("  %s %s", d.map_name(), d.pos())
        bank(d, state_path)
    # The chamber's ON_DIVE_WARP branch that opens the door is the one at
    # (12,44) and no other cell will do.
    if not swim_to(d, 12, 44):
        return False
    if not d.dive():
        log.info("  could not surface at (12,44): %s", d.last_field_reason)
        return False
    log.info("  surfaced into %s %s", d.map_name(), d.pos())
    return d.map_name() == "SealedChamber_OuterRoom"


def open_chamber(d, state_path, deadline) -> bool:
    """FLAG_SYS_BRAILLE_DIG, then FLAG_REGI_DOORS_OPENED."""
    if d.state.flag("FLAG_REGI_DOORS_OPENED"):
        log.info("  the Regi doors are already open")
        return True
    # HEAL BEFORE THE SEA CROSSING. The party the chamber demands is led by a
    # L35 RELICANTH -- `CheckRelicanthWailord` reads party SLOT 0 and nothing
    # else -- and the Route 134 crossing is wall-to-wall TENTACOOL. A faint in
    # there costs a whiteout and, worse, the walk back.
    if not d.underwater() and d.map_name() not in (
            "SealedChamber_OuterRoom", "SealedChamber_InnerRoom"):
        try:
            d.heal_at_nearest_center()
        except Exception as exc:  # noqa: BLE001
            log.info("  heal: %s", str(exc)[:90])
    if not descend_to_chamber(d, state_path, deadline):
        return False
    bank(d, state_path)
    if d.map_name() == "SealedChamber_OuterRoom":
        surf_on(d)
        d.sync_grid()
        if not d.state.flag("FLAG_SYS_BRAILLE_DIG"):
            reached = False
            for x in (10, 9, 11):
                if d.pos() == (x, 3) or d.goto(x, 3, on_battle="fight"):
                    reached = True
                    break
            if not reached:
                log.info("  could not stand on the dig tile: %s",
                         d.last_goto_reason)
                return False
            log.info("  digging at %s", d.pos())
            if not use_field_move(d, "DIG"):
                return False
            d.advance_scene(60_000)
            d.settle(900)
            d.close_menus()
            if not d.state.flag("FLAG_SYS_BRAILLE_DIG"):
                log.info("  DIG did not set FLAG_SYS_BRAILLE_DIG (pos %s)",
                         d.pos())
                return False
            log.info("  FLAG_SYS_BRAILLE_DIG set")
            bank(d, state_path)
        d.sync_grid()
        if not d.take_warp(10, 2):
            log.info("  the inner door refused: %s", d.last_warp_reason)
            return False
    log.info("  inner room: %s %s", d.map_name(), d.pos())
    have = party_species(d)
    log.info("  party order at the braille: %s", have)
    for _ in range(3):
        if not d.talk_to(10, 4, facing="U"):
            log.info("  the braille at (10,4) answered nothing: %s",
                     d.last_talk_reason)
            break
        d.advance_scene(120_000)
        d.settle(1200)
        d.close_menus()
        if d.state.flag("FLAG_REGI_DOORS_OPENED"):
            log.info("  FLAG_REGI_DOORS_OPENED set")
            bank(d, state_path)
            return True
    log.info("  the chamber did not open; party was %s "
             "(CheckRelicanthWailord wants slot 0 RELICANTH and slot "
             "count-1 WAILORD, braille_puzzles.c:58-69)", have)
    return False


def leave_chamber(d, state_path) -> bool:
    """Back out to Route 134 so the ruins are reachable by Fly."""
    if d.map_name() == "SealedChamber_InnerRoom":
        if not d.take_warp(10, 19):
            log.info("  inner exit refused: %s", d.last_warp_reason)
            return False
    if d.map_name() == "SealedChamber_OuterRoom":
        surf_on(d)
        if not d.goto(10, 19, on_battle="fight"):
            log.info("  could not reach the outer room's water: %s",
                     d.last_goto_reason)
            return False
        if not d.dive():
            log.info("  could not dive out: %s", d.last_field_reason)
            return False
    if d.map_name() == "Underwater_SealedChamber":
        # Off (12,44) the ON_DIVE_WARP branch points at Route134 (60,31).
        if not swim_to(d, 7, 1):
            return False
        if not d.take_warp(7, 1):
            log.info("  chamber exit warp refused: %s", d.last_warp_reason)
            return False
    if d.map_name() == "Underwater_Route134":
        if not swim_to(d, 8, 6):
            return False
        if not d.dive():
            log.info("  could not surface: %s", d.last_field_reason)
            return False
    log.info("  out at %s %s", d.map_name(), d.pos())
    bank(d, state_path)
    return not d.underwater()


# ---- stage: one ruin ----------------------------------------------------

def open_ruin(d, ruin: str, opener: str, flag: str) -> bool:
    if d.state.flag(flag):
        return True
    if opener == "strength":
        for x in (10, 9, 11):
            if d.pos() == (x, 23) or d.goto(x, 23, on_battle="fight"):
                break
        else:
            log.info("  could not stand on the strength tile: %s",
                     d.last_goto_reason)
            return False
        log.info("  STRENGTH at %s", d.pos())
        if not use_field_move(d, "STRENGTH"):
            return False
        d.advance_scene(60_000)
    elif opener == "fly":
        if d.pos() != (8, 25) and not d.goto(8, 25, on_battle="fight"):
            log.info("  could not stand on (8,25): %s", d.last_goto_reason)
            return False
        log.info("  FLY at %s", d.pos())
        if not use_field_move(d, "FLY"):
            return False
        d.advance_scene(60_000)
    elif opener == "wait":
        if not braille_wait(d):
            return False
    d.settle(900)
    d.close_menus()
    ok = d.state.flag(flag)
    log.info("  %s -> %s", flag, ok)
    return ok


def braille_wait(d, budget_frames: int = 12_000) -> bool:
    """Island Cave: read the braille, then DO NOT TOUCH ANYTHING.

    `Task_BrailleWait` (pret/src/braille_puzzles.c:153-204) starts a 7200
    frame countdown and `BrailleWait_CheckButtonPress` watches A, B, START,
    SELECT and the whole d-pad (:206-219). Case 2 -- reached the moment any of
    them is pressed -- destroys the task on the NEXT press, so the harness's
    usual `advance_scene`, which presses A to clear dialogue, is exactly the
    wrong tool. Frames are advanced with `.` and nothing else, and the flag
    is polled so a success costs no more than it has to.
    """
    if not d.talk_to(8, 20, facing="U"):
        log.info("  the Island Cave braille answered nothing: %s",
                 d.last_talk_reason)
        return False
    log.info("  waiting out the two minutes (7200 frames, no input)")
    spent = 0
    while spent < budget_frames:
        d.emu.run_sequence(".:240")
        spent += 240
        if d.state.flag("FLAG_SYS_BRAILLE_WAIT"):
            log.info("  chamber opened after %d idle frames", spent)
            break
    else:
        log.info("  %d frames and FLAG_SYS_BRAILLE_WAIT is still clear",
                 spent)
        return False
    # `IslandCave_EventScript_15EF95` ends on `waitbuttonpress`.
    d.emu.run_sequence("A:4 .:60")
    d.advance_scene(60_000)
    return True


def ball_policy(d, species: str, counter: dict, cap: int):
    """Throw ULTRA BALLs at the Regi and nothing else.

    The single MASTER BALL is not spent here on purpose -- RAYQUAZA is the
    only thing in this dex worth it -- so the ball is named explicitly rather
    than left to `Catcher._pick_ball`, which sorts by price and would reach
    for it. YAWN, when the lead happens to know it, is worth one turn: sleep
    is the x2 status multiplier in the Gen 3 catch formula and doubles every
    throw after it.
    """
    def policy(frame):
        enemy = frame.get("enemy") or {}
        name = (enemy.get("species") or "").upper()
        if species not in name:
            return "flee"
        balls = (frame.get("bag") or {}).get("poke_balls") or {}
        status = " ".join(enemy.get("status") or ()).upper()
        if counter["thrown"] >= cap:
            counter["capped"] = True
            return "flee"
        if "SLEEP" not in status and not counter.get("yawned"):
            for mv in frame.get("moves") or ():
                if mv["name"].upper() == "YAWN" and mv["pp"]:
                    counter["yawned"] = True
                    return ("attack", mv["slot"])
        for pick in ("ULTRA BALL", "GREAT BALL", "POKE BALL"):
            for held, count in balls.items():
                if count and held.upper().replace("é", "E") == pick:
                    counter["thrown"] += 1
                    return ("ball", held)
        counter["out_of_balls"] = True
        return "flee"
    return policy


def catch_regi(d, target, species: str, deadline: float, state_path,
               scratch: Path, cap: int = 24) -> bool:
    """Talk to the Regi and savescum the throws until it is registered.

    The Regi object's own script sets FLAG_HIDE_<REGI> BEFORE the battle
    starts (e.g. DesertRuins_EventScript_15CB85, scripts.inc:53-66), so the
    encounter is strictly one-shot: a faint, a flee or a whiteout loses it
    permanently. Hence a scratch state banked at the ruin's door and reloaded
    on failure -- and the k-frame idle after each load, because the RNG is
    stepped once per VBlank (pret/src/main.c:328) and a state reloaded and
    replayed identically would fail identically.
    """
    if registered(d, target, species):
        return True
    regi_map = d.map_name()
    if not d.goto(8, 8, on_battle="fight"):
        log.info("  could not stand under the Regi: %s", d.last_goto_reason)
        return False
    d.save(scratch)
    cycle = 0
    while time.time() < deadline:
        cycle += 1
        if cycle > 1:
            d.load(scratch, adopt=False)
            d.emu.run_sequence(".:%d" % (7 * cycle + (cycle % 5)))
        counter = {"thrown": 0}
        if not d.talk_to(8, 7, facing="U"):
            log.info("  the Regi did not answer an A press: %s",
                     d.last_talk_reason)
            return False
        d.advance_scene(60_000)
        try:
            d.battle_policy = ball_policy(d, species, counter, cap)
            if d.in_battle() or d.state.in_battle():
                d.fight(policy=d.battle_policy)
            else:
                # The cry-and-fade intro can still be running.
                for _ in range(6):
                    d.advance_scene(60_000)
                    if d.in_battle() or d.state.in_battle():
                        break
                if d.in_battle() or d.state.in_battle():
                    d.fight(policy=d.battle_policy)
        finally:
            d.battle_policy = None
        d.advance_scene(60_000)
        if registered(d, target, species):
            log.info("  %s CAUGHT on cycle %d (%d balls thrown)", species,
                     cycle, counter["thrown"])
            return True
        log.info("  cycle %d: %d balls, no %s (map %s)", cycle,
                 counter["thrown"], species, d.map_name())
        if counter.get("out_of_balls"):
            log.info("  out of balls")
            return False
        if d.map_name() != regi_map:
            # a whiteout, or the escape rope path -- the scratch state is the
            # only way back to the door
            pass
    log.info("  out of time on %s", species)
    return False


def do_ruin(d, target, ruin: str, species: str, opener: str, flag: str,
            deadline: float, state_path, scratch: Path) -> bool:
    if registered(d, target, species):
        log.info("%s already registered", species)
        return True
    if not d.state.flag("FLAG_REGI_DOORS_OPENED"):
        log.info("%s: the Regi doors are not open", species)
        return False
    log.info("=== %s (%s)", species, ruin)
    if d.map_name() != ruin:
        route = RUIN_ROUTE[ruin]
        if not reach(d, ruin):
            log.info("  could not reach %s (at %s, via %s)", ruin,
                     d.map_name(), route)
            return False
    d.sync_grid()
    if not open_ruin(d, ruin, opener, flag):
        return False
    d.sync_grid()
    if d.pos()[1] > 12:
        if not d.take_warp(8, 20):
            log.info("  the ruin door refused: %s", d.last_warp_reason)
            return False
    log.info("  inner chamber: %s %s", d.map_name(), d.pos())
    ok = catch_regi(d, target, species, deadline, state_path, scratch)
    if ok:
        bank(d, state_path)
    return ok


# ---- driver -------------------------------------------------------------

ALL_STAGES = ("dig", "party", "chamber", "regirock", "regice", "registeel")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--stages", default="all")
    ap.add_argument("--minutes", type=float, default=90.0)
    ap.add_argument("--cap", type=int, default=24,
                    help="ULTRA BALLs thrown per savescum cycle")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    stages = (ALL_STAGES if a.stages == "all"
              else tuple(s.strip() for s in a.stages.split(",") if s.strip()))
    deadline = time.time() + a.minutes * 60

    state = str(Path(a.state))
    d = Driver(state)
    scratch = Path(state).with_suffix(".regiscratch")
    if d.at_title():
        d.resume_from_title()
    d.advance_scene(40_000)
    unwedge(d)
    surf_on(d)
    if d.underwater():
        emerge(d)
    target = dex(d)
    before = caught_ids(d, target)
    log.info("start: %s %s | dex %d", d.map_name(), d.pos(), len(before))

    want = {name: registered(d, target, name)
            for _m, name, _o, _f in RUINS}
    log.info("already registered: %s", {k: v for k, v in want.items() if v})
    if all(want.values()):
        log.info("all three Regis are already in the dex; nothing to do")
        return 0

    d.battle_policy = None
    if "dig" in stages and not d.state.flag("FLAG_REGI_DOORS_OPENED"):
        log.info("=== stage dig")
        if not ensure_dig(d):
            log.info("no DIG knower: the Sealed Chamber cannot be opened")
            return 1
        bank(d, state)
    if "party" in stages and not d.state.flag("FLAG_REGI_DOORS_OPENED"):
        log.info("=== stage party")
        if not build_party(d, target):
            log.info("could not build the RELICANTH-first/WAILORD-last party")
            return 1
        bank(d, state)
    if "chamber" in stages:
        log.info("=== stage chamber")
        if not open_chamber(d, state, deadline):
            return 1
        if not leave_chamber(d, state):
            log.info("opened the chamber but could not get back out")
            bank(d, state)
            return 1
        bank(d, state)

    got = []
    for ruin, species, opener, flag in RUINS:
        if species.lower() not in stages:
            continue
        if time.time() > deadline:
            log.info("out of time before %s", species)
            break
        try:
            if do_ruin(d, target, ruin, species, opener, flag, deadline,
                       state, scratch):
                got.append(species)
                bank(d, state)
        except Exception as exc:  # noqa: BLE001
            log.info("%s failed: %s", species, str(exc)[:200])
            unwedge(d)
    bank(d, state)
    after = caught_ids(d, target)
    log.info("done: dex %d -> %d (+%s) | new %s", len(before), len(after),
             len(after - before), got)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
