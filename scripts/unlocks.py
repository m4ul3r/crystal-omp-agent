#!/usr/bin/env python
"""The deterministic unlocks that gate whole wild-encounter areas.

Each one is a single NPC or door, and each opens maps the sweeper has been
skipping for the whole run. Everything here is cited from the decompilation
at `pret/` rather than a walkthrough:

* BASEMENT KEY -- Wattson stands at MauvilleCity (29,9)
  (pret/data/maps/MauvilleCity/map.json:130-138) and
  `MauvilleCity_EventScript_Wattson` gives ITEM_BASEMENT_KEY
  (pret/data/maps/MauvilleCity/scripts.inc:288). The New Mauville door
  coord_event does `checkitem ITEM_BASEMENT_KEY`
  (pret/data/maps/NewMauville_Entrance/scripts.inc:29) and sets
  VAR_NEW_MAUVILLE_STATE=1. Opens MAGNEMITE, MAGNETON, VOLTORB, ELECTRODE.

* ACRO BIKE -- Rydel's shop warp is MauvilleCity (35,5)
  (pret/data/maps/MauvilleCity/map.json:174-180) and the swap is free.
  SafariZone_Northeast's inner area is Acro-gated and holds XATU, PHANPY,
  HERACROSS.

* WYNAUT EGG -- `LavaridgeTown_EventScript_EggWoman`
  (pret/data/maps/LavaridgeTown/scripts.inc:287) hands over an egg. This
  exists in SAPPHIRE; Mirage Island is a per-day RNG match against party
  personality and is not worth driving.

Run one leg at a time so a failure costs one errand, not the run.
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver, TravelError, TravelInterrupted  # noqa: E402

log = logging.getLogger("unlocks")

WATTSON = ("MauvilleCity", 29, 9)
BIKE_SHOP_WARP = ("MauvilleCity", 35, 5)
EGG_WOMAN = ("LavaridgeTown", None, None)   # resolved from the map's objects


def _pick_up(d, x: int, y: int, item: str, tries: int = 4) -> bool:
    """Face an item ball and press A until the item is in the bag.

    Two lessons in one helper:
      * An item ball is an `object_event`, so it BLOCKS its cell -- `goto`
        stalled twelve times at (2,15), one tile below the Moon Stone.
        `talk_to` walks to a walkable neighbour, faces the cell and presses A.
      * Caves are wall-to-wall encounters, and a wild mid-approach makes
        `talk_to` raise TravelInterrupted. Fight it and try again rather than
        losing the errand.
    """
    for attempt in range(tries):
        if _has(d, item):
            return True
        try:
            d.talk_to(x, y)
        except TravelInterrupted:
            log.info("  %s: wild on the way -- fighting", item.lower())
            d.fight()
            d.advance_scene(40_000)
            continue
        except Exception as exc:  # noqa: BLE001
            log.info("  %s: talk_to refused: %s", item.lower(), str(exc)[:90])
        for _ in range(8):
            if _has(d, item):
                return True
            d.emu.run_sequence("A:4 .:40")
            d.advance_scene(40_000)
    return _has(d, item)


def _enable_surf(d) -> bool:
    """Let nav plan over water when the party can actually SURF.

    `nav.surfing` is off by default and nav treats water as wall, so any
    destination across a channel reports "no approach to warp ...". New
    Mauville's door is on the far side of Route 110's water, which is why
    take_warp could never fire it.
    """
    try:
        if (d.field_moves() or {}).get("SURF"):
            d.nav.surfing = True
            return True
    except Exception as exc:  # noqa: BLE001
        log.debug("  surf check: %s", str(exc)[:70])
    return False


def _reach(d, map_name: str, budget: float = 300.0) -> bool:
    if d.map_name() == map_name:
        return True
    # FLY FIRST WHEN WALKING CANNOT WORK. These errands get run from wherever
    # the last job left the save, and the Ever Grande plateau has no walkable
    # route to anywhere -- "no walkable route from EverGrandeCity to
    # LavaridgeTown" killed the Wynaut egg leg outright. Fly is refused
    # indoors, so step out of the building first.
    try:
        if not d.flight.flyable_here():
            d.flight.step_outside()
        if d.fly_to(map_name):
            log.info("  flew to %s", d.map_name())
            return True
    except Exception as exc:  # noqa: BLE001 - fall through to walking
        log.debug("  fly to %s: %s", map_name, str(exc)[:80])
    for _ in range(3):
        try:
            if d.travel(map_name, on_battle="fight", budget_s=budget):
                return True
        except TravelInterrupted:
            d.fight()
            d.advance_scene(40_000)
        except TravelError as exc:
            log.info("travel %s: %s", map_name, str(exc)[:110])
            break
        if d.map_name() == map_name:
            return True
    return d.map_name() == map_name


def leg_basement_key(d) -> bool:
    """Talk to Wattson; he hands over the New Mauville key."""
    if _has(d, "BASEMENT KEY"):
        log.info("basement key: already held")
        return True
    name, x, y = WATTSON
    if not _reach(d, name):
        log.info("basement key: could not reach %s", name)
        return False
    for attempt in range(4):
        d.talk_to(x, y)
        d.advance_scene(40_000)
        if _has(d, "BASEMENT KEY"):
            log.info("basement key: OBTAINED on attempt %d", attempt + 1)
            return True
    log.info("basement key: talked to (%d,%d) and got nothing", x, y)
    return False


def leg_acro_bike(d) -> bool:
    """Swap the Mach Bike for the Acro Bike at Rydel's. Free, and reversible."""
    if _has(d, "ACRO BIKE"):
        log.info("acro bike: already held")
        return True
    name, x, y = BIKE_SHOP_WARP
    if not _reach(d, name):
        log.info("acro bike: could not reach %s", name)
        return False
    if not d.take_warp(x, y):
        log.info("acro bike: could not enter the shop (%s)",
                 d.last_warp_reason)
        return False
    log.info("acro bike: inside %s", d.map_name())
    # RYDEL'S EXACT CELL, not a scan of the map's objects. `missables` already
    # publishes it -- the run's own status line reads
    # `ACROBIKE(MauvilleCity_BikeShop 2,5)` -- so there is nothing to guess,
    # and a first attempt that iterated a non-existent `map_objects()` threw
    # away a working shop visit.
    for x, y in ((2, 5), (2, 4), (3, 5)):
        try:
            d.talk_to(x, y)
        except Exception as exc:  # noqa: BLE001
            log.debug("shop talk (%d,%d): %s", x, y, str(exc)[:70])
        d.advance_scene(40_000)
        # The swap is a YES/NO offer; A accepts, and the bike is exchanged
        # rather than added -- so MACH BIKE leaving is also proof.
        for _ in range(8):
            if _has(d, "ACRO BIKE"):
                break
            d.emu.run_sequence("A:4 .:40")
            d.advance_scene(40_000)
        if _has(d, "ACRO BIKE"):
            log.info("acro bike: OBTAINED (mach still held: %s)",
                     _has(d, "MACH BIKE"))
            return True
    log.info("acro bike: shop visited, still holding %s",
             "MACH BIKE" if _has(d, "MACH BIKE") else "neither bike")
    return False


def leg_wynaut_egg(d) -> bool:
    """Take the Lavaridge egg. Needs a free party slot to accept."""
    name = "LavaridgeTown"
    if not _reach(d, name):
        log.info("wynaut egg: could not reach %s", name)
        return False
    party = [m for m in d.state.party() if not m.is_egg]
    if len(party) >= 6:
        log.info("wynaut egg: party is full (%d) -- freeing a slot", len(party))
        # `Storage.deposit` takes a PARTY INDEX and needs a PC underfoot;
        # there is no `Driver.deposit` (that was a Crystal-era API and the
        # first attempt died on `'Driver' object has no attribute 'deposit'`).
        from pokeagent.storage import Storage

        if not Storage(d).pc_cells():
            try:
                d.heal_at_nearest_center()
            except Exception as exc:  # noqa: BLE001
                log.info("wynaut egg: centre: %s", str(exc)[:90])
        st = Storage(d)
        if not st.pc_cells():
            log.info("wynaut egg: no PC on %s", d.map_name())
            return False
        party = [m for m in d.state.party() if not m.is_egg]
        victim = min(range(len(party)), key=lambda i: party[i].level or 0)
        log.info("wynaut egg: depositing %s L%s", party[victim].nickname,
                 party[victim].level)
        if not st.deposit(victim):
            log.info("wynaut egg: deposit refused (%s)",
                     getattr(st, "last_reason", "?"))
            return False
        st.close()
        if not _reach(d, name):
            return False
    # THE EGG WOMAN'S EXACT CELL. `LavaridgeTown_EventScript_EggWoman` is
    # OLD_WOMAN_1 at (4,7) (pret/data/maps/LavaridgeTown/map.json:129-136),
    # and the script `giveegg SPECIES_WYNAUT` is gated on
    # FLAG_RECEIVED_LAVARIDGE_EGG plus a party slot
    # (LavaridgeTown/scripts.inc:271-289). There is no `Driver.map_objects`
    # -- the acro leg died on that same non-existent method.
    for x, y in ((4, 7), (4, 8), (3, 7), (5, 7)):
        try:
            d.talk_to(x, y)
        except Exception as exc:  # noqa: BLE001
            log.debug("wynaut egg: talk (%d,%d): %s", x, y, str(exc)[:70])
        for _ in range(10):
            d.emu.run_sequence("A:4 .:40")
            d.advance_scene(40_000)
            if any(m.is_egg for m in d.state.party()):
                log.info("wynaut egg: RECEIVED at (%d,%d)", x, y)
                return True
    log.info("wynaut egg: no egg in the party after talking")
    return False


def _has(d, item: str) -> bool:
    try:
        bag = d.state.bag()
    except Exception:  # noqa: BLE001
        return False
    want = item.upper()
    for pocket in bag.values():
        if not isinstance(pocket, dict):
            continue
        if any(str(k).upper() == want for k in pocket):
            return True
    return False


def leg_exp_share(d) -> bool:
    """Take the EXP. SHARE from Mr. Stone. This is the whole evolution plan.

    `RustboroCity_DevonCorp_3F_EventScript_GiveExpShare`
    (pret/data/maps/RustboroCity_DevonCorp_3F/scripts.inc:159-163) is gated
    on FLAG_DELIVERED_STEVEN_LETTER, which an 8-badge run has long since set.
    Mr. Stone stands at (15,5) and (17,5) -- the map lists him twice.

    With it, a BENCHED mon earns `calculatedExp / 2` from every kill
    (battle_script_commands.c:3375-3392) and that payout still sets
    `gLeveledUpInBattle` (:3527), so it EVOLVES afterwards
    (battle_main.c:5091-5113). Without it the target has to be switched in
    against each of Steven's six mons and can be one-shot doing it.
    """
    if _has(d, "EXP SHARE") or _has(d, "EXP. SHARE"):
        log.info("exp share: already held")
        return True
    if not _reach(d, "RustboroCity_DevonCorp_3F"):
        log.info("exp share: could not reach Devon Corp 3F")
        return False
    for x, y in ((15, 5), (17, 5)):
        try:
            d.talk_to(x, y)
        except Exception as exc:  # noqa: BLE001
            log.debug("stone talk (%d,%d): %s", x, y, str(exc)[:70])
        for _ in range(10):
            d.emu.run_sequence("A:4 .:40")
            d.advance_scene(40_000)
            if _has(d, "EXP SHARE") or _has(d, "EXP. SHARE"):
                log.info("exp share: OBTAINED")
                return True
    log.info("exp share: talked to Mr. Stone and got nothing")
    return False


def leg_new_mauville(d) -> bool:
    """Open New Mauville and take the THUNDER STONE inside.

    Four species live in there (MAGNEMITE, MAGNETON, VOLTORB, ELECTRODE) and
    the game's only fixed THUNDER STONE sits at NewMauville_Inside (39,4)
    (pret/data/item_ball_scripts.inc:325-326), which is what turns a boxed
    PIKACHU into RAICHU.

    The door is a coord_event at (4,2) that asks YES/NO after
    `checkitem ITEM_BASEMENT_KEY` and then opens itself with `setmetatile`
    (pret/data/maps/NewMauville_Entrance/scripts.inc:24-40). Two consequences:
    the box MUST be answered, and the opened tiles only exist at RUNTIME --
    the static .blk still reads them as wall, so nav needs `sync_grid` before
    it will route through. Same shape as the Elite Four doors.
    """
    if _enable_surf(d):
        log.info("new mauville: surf routing enabled")
    if not _reach(d, "NewMauville_Entrance"):
        # Route 110 holds the entrance warp; get to the route and take it.
        if not _reach(d, "Route110"):
            log.info("new mauville: could not reach Route110")
            return False
        # ROUTE 110's OWN WARP CELL is (35,24) -- (4,6) is the one INSIDE the
        # entrance pointing back out, and using it from the route asked nav to
        # approach a cell on the wrong map: "no approach to warp (4,6) on
        # Route110". Each side of a warp pair has its own coordinates.
        if not d.take_warp(35, 24):
            log.info("new mauville: could not enter the building (%s)",
                     d.last_warp_reason)
            return False
    log.info("new mauville: at %s %s", d.map_name(), d.pos())

    if int(d.state.var("VAR_NEW_MAUVILLE_STATE") or 0) == 0:
        # Stand ON the trigger; it fires on the step that ENTERS the cell.
        if not d.goto(4, 2, on_battle="fight"):
            log.info("new mauville: could not stand on the trigger (%s)",
                     d.last_goto_reason)
        for _ in range(10):
            if int(d.state.var("VAR_NEW_MAUVILLE_STATE") or 0):
                break
            try:
                if d.choice_open():
                    d.resolve_choice("YES")
                    d.advance_scene(40_000)
                    continue
            except Exception as exc:  # noqa: BLE001
                log.debug("new mauville: choice: %s", str(exc)[:70])
            d.emu.run_sequence("A:4 .:40")
            d.advance_scene(40_000)
        state = int(d.state.var("VAR_NEW_MAUVILLE_STATE") or 0)
        log.info("new mauville: door state = %d", state)
        if not state:
            log.info("new mauville: the door never opened")
            return False
    # The door tiles were written at runtime; tell nav about them.
    try:
        drift = d.sync_grid()
        if drift:
            log.info("new mauville: synced %d changed cells", drift)
    except Exception as exc:  # noqa: BLE001
        log.debug("new mauville: sync: %s", str(exc)[:70])

    if not d.take_warp(4, 1):
        log.info("new mauville: could not go through the open door (%s)",
                 d.last_warp_reason)
        return False
    log.info("new mauville: inside %s %s", d.map_name(), d.pos())

    # THUNDER STONE at (39,4). Ground items are picked up by stepping onto
    # the ball's cell.
    if not _has(d, "THUNDER STONE"):
        # Item balls are objects: face and press A, never walk onto them.
        _pick_up(d, 39, 4, "THUNDER STONE")
    log.info("new mauville: THUNDER STONE held = %s",
             _has(d, "THUNDER STONE"))
    return True


def leg_castform(d) -> bool:
    """Take the CASTFORM gift at the Weather Institute.

    `givemon SPECIES_CASTFORM, 25, ITEM_MYSTIC_WATER`
    (pret/data/maps/Route119_WeatherInstitute_2F/scripts.inc:65), reached via
    `EventScript_163D7A` and gated on FLAG_RECEIVED_CASTFORM. The script does
    `getpartysize / compare 6 / goto_if_eq` FIRST, so a full party is turned
    away -- and ours carries the Wynaut egg, which counts.

    The 2F objects are at (15,6), (10,8), (4,6) and (18,6); the scientist at
    (18,6) is the one who thanks you once Team Aqua has gone. Talk to each
    rather than guess which, and stop the moment the flag flips.
    """
    if d.state.flag("FLAG_RECEIVED_CASTFORM"):
        log.info("castform: already received")
        return True
    _enable_surf(d)
    # A FREE SLOT FIRST, or the gift is refused before it starts.
    party = [m for m in d.state.party() if not m.is_egg or True]
    if len(party) >= 6:
        from pokeagent.storage import Storage

        if not Storage(d).pc_cells():
            try:
                d.heal_at_nearest_center()
            except Exception as exc:  # noqa: BLE001
                log.info("castform: centre: %s", str(exc)[:90])
        st = Storage(d)
        if not st.pc_cells():
            log.info("castform: no PC on %s to free a slot", d.map_name())
            return False
        party = d.state.party()
        # Never deposit the egg -- it is a pending dex entry mid-hatch.
        cands = [(i, m) for i, m in enumerate(party) if not m.is_egg]
        if not cands:
            log.info("castform: nothing safe to deposit")
            return False
        idx, victim = min(cands, key=lambda p: p[1].level or 0)
        log.info("castform: depositing %s L%s", victim.nickname, victim.level)
        if not st.deposit(idx):
            log.info("castform: deposit refused (%s)",
                     getattr(st, "last_reason", "?"))
            return False
        st.close()

    # WALK IN THROUGH THE DOOR. `_reach` flies, and Fly cannot target an
    # interior map -- the first attempt landed on Route111 and gave up. The
    # Institute's door is Route119 (6,32) and 2F is up the (17,1) stairs
    # inside (map.json warp tables), so route to the ROUTE and take the
    # warps by hand.
    if d.map_name() != "Route119_WeatherInstitute_2F":
        if d.map_name() != "Route119_WeatherInstitute_1F":
            # FLY TO THE NEAREST TOWN FIRST. Fly only takes named
            # towns/cities, so `_reach("Route119")` flew nowhere and the walk
            # from JaggedPass died on Route111. Fortree sits on Route 119.
            try:
                if d.fly_to("FortreeCity"):
                    log.info("castform: flew to %s", d.map_name())
            except Exception as exc:  # noqa: BLE001
                log.debug("castform: fly Fortree: %s", str(exc)[:80])
            if not _reach(d, "Route119"):
                log.info("castform: could not reach Route119 (at %s)",
                         d.map_name())
                return False
            if not d.take_warp(6, 32):
                log.info("castform: could not enter the Institute (%s)",
                         d.last_warp_reason)
                return False
            log.info("castform: inside %s %s", d.map_name(), d.pos())
        if not d.take_warp(17, 1):
            log.info("castform: could not climb to 2F (%s)",
                     d.last_warp_reason)
            return False
        if d.map_name() != "Route119_WeatherInstitute_2F":
            log.info("castform: ended on %s, not 2F", d.map_name())
            return False
    log.info("castform: at %s %s", d.map_name(), d.pos())

    for x, y in ((18, 6), (15, 6), (10, 8), (4, 6)):
        try:
            d.talk_to(x, y)
        except Exception as exc:  # noqa: BLE001
            log.debug("castform: talk (%d,%d): %s", x, y, str(exc)[:70])
        for _ in range(10):
            d.emu.run_sequence("A:4 .:40")
            d.advance_scene(40_000)
            if d.state.flag("FLAG_RECEIVED_CASTFORM"):
                log.info("castform: RECEIVED from (%d,%d)", x, y)
                return True
    log.info("castform: flag still unset after talking to everyone")
    return False


def leg_moon_stone(d) -> bool:
    """Take the MOON STONE in Meteor Falls -- the only fixed one in the game.

    `finditem ITEM_MOON_STONE` at MeteorFalls_1F_1R (2,14)
    (pret/data/item_ball_scripts.inc:305-306, object_event in that map's
    map.json). Sapphire sells no evolution stones at all -- Lilycove 5F is a
    DECORATION list -- and the only other Moon Stone is Lunatone's 5% held
    item, so this single ball is what turns the boxed JIGGLYPUFF into
    WIGGLYTUFF and a SKITTY into DELCATTY.

    Routed the way CASTFORM finally worked: Fly only accepts named towns, so
    fly to the nearest one and walk in through the door. Fallarbor is the
    closest landing to Route 114, whose (8,63) warp is the cave mouth.
    """
    if _has(d, "MOON STONE"):
        log.info("moon stone: already held")
        return True
    _enable_surf(d)
    if d.map_name() != "MeteorFalls_1F_1R":
        for town in ("FallarborTown", "RustboroCity", "VerdanturfTown"):
            try:
                if d.fly_to(town):
                    log.info("moon stone: flew to %s", d.map_name())
                    break
            except Exception as exc:  # noqa: BLE001
                log.debug("moon stone: fly %s: %s", town, str(exc)[:70])
        if not _reach(d, "Route114"):
            log.info("moon stone: could not reach Route114 (at %s)",
                     d.map_name())
            return False
        if not d.take_warp(8, 63):
            log.info("moon stone: could not enter Meteor Falls (%s)",
                     d.last_warp_reason)
            return False
    log.info("moon stone: inside %s %s", d.map_name(), d.pos())

    # AN ITEM BALL IS AN object_event, SO IT BLOCKS THE CELL. You never walk
    # ONTO one -- you stand beside it, FACE it and press A. `goto(2,14)`
    # stalled twelve times at (2,15), one tile below the ball, which is
    # exactly what "arrived and cannot enter" looks like. `talk_to` walks to
    # a walkable neighbour, faces the cell and presses A.
    got = _pick_up(d, 2, 14, "MOON STONE")
    log.info("moon stone: held = %s", got)
    return got



def leg_fire_stone(d) -> bool:
    """FIRE STONE item ball, FieryPath (7,32).

    `finditem ITEM_FIRE_STONE` (pret/data/item_ball_scripts.inc:290;
    FieryPath/map.json:110-117, FLAG_ITEM_FIERY_PATH_2). Ninetales is a FIRE
    stone evolution -- the vendored dex dataset says "Ice stone", which is
    later-generation data: `ITEM_ICE_STONE` does not exist in this ROM
    (pret/include/constants/items.h has only SUN/MOON/FIRE/THUNDER/WATER/LEAF
    at ids 93-98) and `evolution.h:28` is EVO_ITEM ITEM_FIRE_STONE.
    """
    if _has(d, "FIRE STONE"):
        log.info("fire stone: already held")
        return True
    if d.map_name() != "FieryPath":
        for town in ("LavaridgeTown", "FallarborTown", "MauvilleCity"):
            try:
                if d.fly_to(town):
                    break
            except Exception as exc:  # noqa: BLE001
                log.debug("fire stone: fly %s: %s", town, str(exc)[:70])
        if not _reach(d, "FieryPath"):
            log.info("fire stone: could not reach FieryPath (at %s)", d.map_name())
            return False
    got = _pick_up(d, 7, 32, "FIRE STONE")
    log.info("fire stone: held = %s", got)
    return got


def leg_leaf_stone(d) -> bool:
    """LEAF STONE item ball, Route119 (25,76).

    Route119/map.json:274-284, FLAG_ITEM_ROUTE119_4. Renewable via GREEN
    shards at the Route 124 trader, so this one is a convenience rather than
    a scarce resource -- unlike the Sun Stone.
    """
    if _has(d, "LEAF STONE"):
        log.info("leaf stone: already held")
        return True
    if d.map_name() != "Route119":
        for town in ("FortreeCity", "MauvilleCity"):
            try:
                if d.fly_to(town):
                    break
            except Exception as exc:  # noqa: BLE001
                log.debug("leaf stone: fly %s: %s", town, str(exc)[:70])
        if not _reach(d, "Route119"):
            log.info("leaf stone: could not reach Route119 (at %s)", d.map_name())
            return False
    got = _pick_up(d, 25, 76, "LEAF STONE")
    log.info("leaf stone: held = %s", got)
    return got


def leg_sun_stone(d) -> bool:
    """The ONLY Sun Stone in Sapphire: the Mossdeep Space Center sailor (6,6).

    MossdeepCity_SpaceCenter_1F/map.json:41-52 and scripts.inc:31-42, gated on
    FLAG_RECEIVED_SUN_STONE_MOSSDEEP. It is not renewable here: Solrock is the
    Sun-Stone holder and is RUBY-exclusive, absent from every Sapphire
    encounter table. So this stone is spent on Gloom -> BELLOSSOM and never on
    Vileplume, which takes a renewable Leaf Stone instead.
    """
    if _has(d, "SUN STONE"):
        log.info("sun stone: already held")
        return True
    if d.map_name() != "MossdeepCity_SpaceCenter_1F":
        try:
            d.fly_to("MossdeepCity")
        except Exception as exc:  # noqa: BLE001
            log.info("sun stone: fly refused: %s", str(exc)[:70])
        if not _reach(d, "MossdeepCity"):
            log.info("sun stone: not at Mossdeep (at %s)", d.map_name())
            return False
        for wx, wy in ((7, 9), (8, 9)):
            if d.take_warp(wx, wy):
                break
        if d.map_name() != "MossdeepCity_SpaceCenter_1F":
            log.info("sun stone: could not enter the Space Center (%s at %s)",
                     d.last_warp_reason, d.map_name())
            return False
    for _ in range(3):
        if _has(d, "SUN STONE"):
            break
        try:
            d.talk_to(6, 6)
        except Exception as exc:  # noqa: BLE001
            log.info("sun stone: talk_to refused: %s", str(exc)[:80])
        for _ in range(8):
            if _has(d, "SUN STONE"):
                break
            d.emu.run_sequence("A:4 .:40")
            d.advance_scene(40_000)
    got = _has(d, "SUN STONE")
    log.info("sun stone: held = %s", got)
    return got


def leg_water_stone(d) -> bool:
    """A WATER STONE from the Route 124 shard trader -- an unlimited source.

    The blue shard sits in an item ball at Route124 (31,53)
    (map.json:120-130); the treasure hunter at
    Route124_DivingTreasureHuntersHouse (5,4) trades BLUE -> WATER STONE for
    ONE shard with no gating flag, and re-offers while shards remain
    (scripts.inc:235-238, :245-262).

    The Abandoned Ship ball at (31,11) is the other Water Stone and is far
    worse: it needs Dive plus the ROOM_1/2/4/6 key chain.

    The trade menu is a `multichoice` ordered by a VAR_TEMP_1 bitmask
    (RED=1, YELLOW=2, BLUE=4, GREEN=8; scripts.inc:30-51), so holding exactly
    ONE colour is what makes the option index deterministic. This leg does not
    yet pick options blind for that reason -- it reports what it is holding.
    """
    if _has(d, "WATER STONE"):
        log.info("water stone: already held")
        return True
    _enable_surf(d)
    if not _reach(d, "Route124"):
        log.info("water stone: could not reach Route124 (at %s)", d.map_name())
        return False
    if not _has(d, "BLUE SHARD"):
        _pick_up(d, 31, 53, "BLUE SHARD")
    log.info("water stone: blue shard = %s", _has(d, "BLUE SHARD"))
    return _has(d, "BLUE SHARD")


LEGS = {
    "key": leg_basement_key,
    "acro": leg_acro_bike,
    "egg": leg_wynaut_egg,
    "expshare": leg_exp_share,
    "newmauville": leg_new_mauville,
    "castform": leg_castform,
    "moonstone": leg_moon_stone,
    "firestone": leg_fire_stone,
    "leafstone": leg_leaf_stone,
    "sunstone": leg_sun_stone,
    "waterstone": leg_water_stone,
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--legs", default="key,acro")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    d.advance_scene(40_000)
    log.info("start %s %s", d.map_name(), d.pos())

    done = []
    for leg in [s.strip() for s in a.legs.split(",") if s.strip()]:
        fn = LEGS.get(leg)
        if fn is None:
            log.info("unknown leg %r", leg)
            continue
        log.info("=== %s ===", leg)
        try:
            ok = fn(d)
        except Exception as exc:  # noqa: BLE001 - one bad leg is not the run
            log.info("%s raised: %s", leg, str(exc)[:140])
            ok = False
        done.append((leg, ok))
        # Bank after every leg: an unlock is worth keeping even if the next
        # one fails, and these states feed the sweeper.
        if ok:
            d.save(a.state)
            log.info("banked %s after %s", a.state, leg)

    log.info("RESULT %s", done)
    log.info("bag now: key=%s acro=%s mach=%s",
             _has(d, "BASEMENT KEY"), _has(d, "ACRO BIKE"),
             _has(d, "MACH BIKE"))
    return 0 if all(ok for _, ok in done) else 1


if __name__ == "__main__":
    raise SystemExit(main())
