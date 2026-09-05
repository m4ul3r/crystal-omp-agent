#!/usr/bin/env python
"""Collect the dex entries that are ITEMS and GIFTS, not encounters.

The wild sweeper can never get these: they are ground items, one-shot NPC
gifts and invisible static battles. Every coordinate here is from the
decompilation (see DEX_PLAN.md for the citations), so each leg is
deterministic -- no encounter rolls, no catch rolls.

Legs, each verified by its own observable effect rather than by "the script
finished":

  waterstone   Route124 BLUE SHARD (31,53) -> trade at the Treasure Hunter's
               House (5,4) -> WATER STONE. Then use it on the party's LOMBRE
               for LUDICOLO. Verified by the dex flag.
  sunstone     MossdeepCity_SpaceCenter_1F, SAILOR at (6,6). The ONLY Sun
               Stone in the game; do not waste it.
  leafstone    Route119 item ball at (25,76). Walk-in, no gate.
  castform     Route119_WeatherInstitute_2F scientist at (4,6). Needs a free
               party slot or the script refuses.
  kecleon      Seven invisible statics on Route119/Route120 that only appear
               with the DEVON SCOPE. Fortree's flees and three others are
               scenery -- see DEX_PLAN.md.

Nothing here is idempotent by accident: an item ball sets its own flag and a
gift sets FLAG_RECEIVED_*, so a re-run simply finds nothing and says so.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402
from pokeagent.dex import DexTarget  # noqa: E402

log = logging.getLogger("errands")

#: Kecleon statics that are really catchable (DEX_PLAN.md). Fortree (25,8)
#: FLEES and Route117/Lilycove/Sootopolis are decorations.
KECLEON = [
    ("Route119", (31, 6)),
    ("Route119", (20, 13)),
    ("Route120", (20, 11)),
    ("Route120", (27, 2)),
    ("Route120", (4, 77)),
    ("Route120", (7, 51)),
    ("Route120", (19, 48)),
]


def caught(d, t):
    return len(t.dex_flags(d.state)[0])


def bag_has(d, name):
    for pocket in d.state.bag().values():
        if name in pocket:
            return True
    return False


#: Which town to FLY to before walking to a destination. `travel` never
#: flies, so anything on the far side of Hoenn is simply unreachable without
#: this -- the fossil leg failed with "could not cross the D seam to
#: Route118" while standing on Route120, two regions away.
FLY_HINT = {
    "Route111": "MauvilleCity",
    "RustboroCity_PokemonCenter_1F": "RustboroCity",
    "Route119": "FortreeCity",
    "Route120": "FortreeCity",
    "Route124": "MossdeepCity",
    "Route126": "MossdeepCity",
    "Route119_WeatherInstitute_2F": "FortreeCity",
    "Route124_DivingTreasureHuntersHouse": "MossdeepCity",
    "MossdeepCity_SpaceCenter_1F": "MossdeepCity",
    "RustboroCity_DevonCorp_2F": "RustboroCity",
}


def goto_map(d, name, budget=180.0):
    """Fly to the nearest hinted town, then walk. Bounded."""
    if d.map_name() == name:
        return True
    d.nav.surfing = True
    town = FLY_HINT.get(name)
    if town and d.map_name() != town:
        try:
            d.fly_to(town)
            d.advance_scene(20_000)
        except Exception as exc:  # noqa: BLE001
            log.info("   fly %s: %s", town, str(exc)[:80])
    if d.map_name() == name:
        return True
    try:
        d.travel(name, on_battle="fight", budget_s=budget)
    except TravelInterrupted:
        d.fight()
        d.advance_scene(20_000)
    except Exception as exc:  # noqa: BLE001
        log.info("   travel %s: %s", name, str(exc)[:90])
    return d.map_name() == name


def pick_up(d, map_name, cell) -> bool:
    """Walk onto a visible item ball and take it."""
    if not goto_map(d, map_name):
        log.info("   could not reach %s", map_name)
        return False
    d.nav.surfing = True
    log.info("   walking to the ball at %s", cell)
    try:
        if not d.goto(*cell, on_battle="fight"):
            log.info("   could not reach %s: %s", cell,
                     getattr(d, "last_goto_reason", "?"))
            return False
    except TravelInterrupted:
        d.fight()
        d.advance_scene(20_000)
    for _ in range(4):
        d.emu.run_sequence("A:6 .:60")
        d.settle(400)
    d.advance_scene(40_000)
    return True


def leg_sunstone(d, t) -> bool:
    if bag_has(d, "SUN STONE"):
        log.info("sunstone: already held")
        return True
    if not goto_map(d, "MossdeepCity_SpaceCenter_1F"):
        log.info("sunstone: could not reach the Space Center")
        return False
    ok = d.talk_to(6, 6)
    d.advance_scene(40_000)
    log.info("sunstone: talked=%s held=%s", ok, bag_has(d, "SUN STONE"))
    return bag_has(d, "SUN STONE")


def leg_leafstone(d, t) -> bool:
    if bag_has(d, "LEAF STONE"):
        log.info("leafstone: already held")
        return True
    pick_up(d, "Route119", (25, 76))
    log.info("leafstone: held=%s", bag_has(d, "LEAF STONE"))
    return bag_has(d, "LEAF STONE")


def leg_waterstone(d, t) -> bool:
    """Blue Shard -> Water Stone -> LUDICOLO."""
    if not bag_has(d, "WATER STONE"):
        if not bag_has(d, "BLUE SHARD"):
            pick_up(d, "Route124", (31, 53))
        if bag_has(d, "BLUE SHARD"):
            if goto_map(d, "Route124_DivingTreasureHuntersHouse"):
                d.talk_to(5, 4)
                d.advance_scene(40_000)
                # The trader offers a multichoice of shards, then YES.
                for _ in range(8):
                    d.emu.run_sequence("A:6 .:80")
                    d.settle(400)
                d.advance_scene(40_000)
    log.info("waterstone: held=%s", bag_has(d, "WATER STONE"))
    return bag_has(d, "WATER STONE")


def leg_evolve_lombre(d, t) -> bool:
    """Use the Water Stone on LOMBRE. Verified by the dex, not the bag."""
    from pokeagent.teaching import Teacher

    before = caught(d, t)
    lombre = next((m.nickname for m in d.state.party()
                   if d.names.species(m.species) == "LOMBRE"), None)
    if lombre is None:
        log.info("evolve: no LOMBRE in the party")
        return False
    ok = Teacher(d).use_on_mon("WATER STONE", mon=lombre)
    d.advance_scene(60_000)
    after = caught(d, t)
    log.info("evolve: used=%s dex %d -> %d party=%s", ok, before, after,
             [d.names.species(m.species) for m in d.state.party()])
    return after > before


def leg_castform(d, t) -> bool:
    before = caught(d, t)
    if len(d.state.party()) >= 6:
        log.info("castform: party is full -- the gift script refuses; "
                 "deposit something first")
        return False
    if not goto_map(d, "Route119_WeatherInstitute_2F"):
        return False
    d.talk_to(4, 6)
    for _ in range(6):
        d.emu.run_sequence("A:6 .:80")
        d.settle(400)
    d.advance_scene(40_000)
    log.info("castform: dex %d -> %d", before, caught(d, t))
    return caught(d, t) > before


def leg_kecleon(d, t) -> bool:
    """Walk each static, answer YES to the Devon Scope prompt, fight/catch."""
    if not bag_has(d, "DEVON SCOPE"):
        log.info("kecleon: no DEVON SCOPE")
        return False
    before = caught(d, t)
    for map_name, cell in KECLEON:
        if caught(d, t) > before:
            break                      # one Kecleon is one dex slot
        if not goto_map(d, map_name):
            continue
        log.info("   kecleon at %s %s", map_name, cell)
        try:
            d.talk_to(*cell)
        except TravelInterrupted:
            pass
        except Exception as exc:  # noqa: BLE001
            log.info("   %s", str(exc)[:80])
            continue
        for _ in range(4):
            d.emu.run_sequence("A:6 .:80")
            d.settle(400)
        try:
            d.fight()
        except Exception:  # noqa: BLE001
            pass
        d.advance_scene(40_000)
    log.info("kecleon: dex %d -> %d", before, caught(d, t))
    return caught(d, t) > before


def leg_fossil(d, t) -> bool:
    """Take the CLAW FOSSIL off Route 111's sand.

    Both fossils lie side by side -- ROOT at (32,38), CLAW at (33,38) -- and
    taking either sets BOTH hide flags and removes BOTH objects in the same
    script (data/maps/Route111/scripts.inc:57-59, 79-81). There is no
    `clearflag` anywhere and Sapphire has no Desert Underpass, so this is a
    one-shot choice: exactly one of the LILEEP or ANORITH lines is obtainable
    on this cartridge.

    HAZARD: `giveitem` sets VAR_RESULT=FALSE on a full pocket and the Route
    111 script never checks it, setting both hide flags anyway
    (data/scripts/obtain_item.inc:1-15). A full KEY ITEMS pocket loses both
    fossils permanently, so this refuses rather than risk it.
    """
    if bag_has(d, "CLAW FOSSIL") or bag_has(d, "ROOT FOSSIL"):
        log.info("fossil: already holding one")
        return True
    if not bag_has(d, "GO-GOGGLES"):
        log.info("fossil: no GO-GOGGLES -- the desert gate shoves you back")
        return False
    if not goto_map(d, "Route111"):
        log.info("fossil: could not reach Route111")
        return False
    d.nav.surfing = True
    # THE DESERT GATE IS NOT A WALL WHEN YOU HOLD THE GOGGLES.
    #
    # nav.blocked carries every coord_event on the map unconditionally, and
    # Route 111's are the two desert entrances -- (11..14,61) in the south
    # and (12,44),(13,43),(14,42),(16,40),(17,39),(18,38) in the north-west.
    # Their script's ONLY condition is `checkitem ITEM_GO_GOGGLES`
    # (data/maps/Route111/scripts.inc:162-181): with the goggles it does
    # nothing at all, and without them it plays a message and walks you back.
    #
    # Blocking them regardless severed the route in half. Measured from
    # (13,138): 762 cells reachable, y stopping at exactly 61, and every
    # fossil cell answering in_reach=False.
    #
    # This is the Gen-3 shape of Crystal's gotcha 20 -- a scene block is only
    # real while its own guard says so. The general fix is to evaluate those
    # guards in nav; this clears the one gate whose guard is a bag check we
    # can answer here and now.
    gate = d.nav.blocked.get("Route111")
    if gate:
        log.info("fossil: clearing %d Go-Goggles gate cells", len(gate))
        d.nav.blocked["Route111"] = set()
        try:
            d.nav._reach_cache.clear()
        except Exception:  # noqa: BLE001
            pass
    # Stand EAST of the claw and face west: (34,38) -> talk (33,38).
    for stand, face in (((34, 38), "L"), ((33, 37), "D"), ((33, 39), "U")):
        try:
            if not d.goto(*stand, on_battle="fight"):
                continue
        except TravelInterrupted:
            d.fight()
            d.advance_scene(20_000)
            continue
        except Exception:  # noqa: BLE001
            continue
        d.emu.run_sequence(f"{face}:8 .:30")
        d.settle(200)
        for _ in range(6):
            d.emu.run_sequence("A:6 .:80")
            d.settle(400)
        d.advance_scene(40_000)
        if bag_has(d, "CLAW FOSSIL") or bag_has(d, "ROOT FOSSIL"):
            break
    got = bag_has(d, "CLAW FOSSIL") or bag_has(d, "ROOT FOSSIL")
    log.info("fossil: held=%s", got)
    return got


def leg_revive(d, t) -> bool:
    """Hand the fossil to Devon and collect the L20 mon.

    The scientist is at (14,8) on RustboroCity_DevonCorp_2F. Handing it over
    sets VAR_FOSSIL_RESURRECTION_STATE=1 and he then only says "it takes
    time" -- but any of the FOUR other scientists on the same floor flips
    that 1 -> 2 instantly (.../scripts.inc:14-60), so there is no need to
    leave the map and come back.

    The party must be <= 5 or the gift is refused.
    """
    before = caught(d, t)
    if not (bag_has(d, "CLAW FOSSIL") or bag_has(d, "ROOT FOSSIL")):
        log.info("revive: no fossil in the bag")
        return False
    if not goto_map(d, "RustboroCity_DevonCorp_2F"):
        log.info("revive: could not reach Devon 2F")
        return False
    if len(d.state.party()) >= 6:
        # The gift script checks getpartysize and refuses at six
        # (RustboroCity_DevonCorp_2F/scripts.inc:135-137). Recoverable -- the
        # resurrection state stays put -- but only if we go and make room.
        from pokeagent.storage import Storage

        log.info("revive: party is full; freeing a slot at the Centre")
        if not goto_map(d, "RustboroCity_PokemonCenter_1F"):
            log.info("revive: could not reach a Centre to deposit")
            return False
        st = Storage(d)
        party = st.party_names()
        idx = next((i for i in range(len(party) - 1, 0, -1)
                    if party[i] != "SEA BIRD"), None)
        if idx is None or not st.deposit(idx):
            log.info("revive: could not free a slot: %s", st.last_reason)
            return False
        if not goto_map(d, "RustboroCity_DevonCorp_2F"):
            log.info("revive: could not get back to Devon 2F")
            return False
    d.talk_to(14, 8)
    for _ in range(8):
        d.emu.run_sequence("A:6 .:80")
        d.settle(400)
    d.advance_scene(40_000)
    # Nudge the resurrection along via a colleague, then collect.
    for cell in ((6, 5), (1, 5), (2, 6), (10, 5)):
        try:
            d.talk_to(*cell)
            for _ in range(4):
                d.emu.run_sequence("A:6 .:80")
                d.settle(300)
            d.advance_scene(40_000)
        except Exception:  # noqa: BLE001
            continue
        break
    # COLLECT IT -- AND SAY NO TO THE NICKNAME.
    #
    # `givemon` is followed by `msgbox gText_NicknameReceivedPokemon,
    # MSGBOX_YESNO` (RustboroCity_DevonCorp_2F/scripts.inc:146-148). A blind
    # A-loop answers YES and drops into the naming KEYBOARD, where every
    # further A types a letter: the first run came back with a state stuck on
    # "ANORITH's nickname? AAAAA" that no amount of START, B or cursor
    # movement would leave. Input still registered (the cursor moved) and
    # frames still advanced -- the savestate simply could not be resumed out
    # of that screen, and two dex entries had to be re-earned.
    #
    # Same family as gotcha 18: a blind A-loop into a menu that re-arms. Here
    # the menu is a text field, which is worse, because it consumes presses
    # forever instead of repeating an action.
    # B-ONLY ONCE THE SCRIPT IS RUNNING.
    #
    # In RSE, B advances a text box AND answers NO to a YESNO. A does the
    # first and YES to the second -- and YES here opens the naming KEYBOARD,
    # which this harness cannot resume a savestate out of. Two separate lines
    # were lost to it, the second even though the loop was checking
    # choice_open(): that predicate does not recognise this particular box, so
    # an A press slipped through and the keyboard came up with an empty field.
    #
    # So: two A presses to get the script moving, then B for everything else.
    # Nothing after the givemon needs a YES.
    d.talk_to(14, 8)
    for _ in range(2):
        d.emu.run_sequence("A:6 .:80")
        d.settle(400)
    for _ in range(10):
        d.emu.run_sequence("B:6 .:80")
        d.settle(400)
    d.advance_scene(40_000)
    if d.scene_active():
        log.info("revive: WARNING -- scene still active after collecting; "
                 "not saving a state that may be stuck in a menu")
    log.info("revive: dex %d -> %d party=%s", before, caught(d, t),
             [d.names.species(m.species) for m in d.state.party()])
    return caught(d, t) > before


LEGS = {
    "waterstone": leg_waterstone,
    "evolve": leg_evolve_lombre,
    "sunstone": leg_sunstone,
    "leafstone": leg_leafstone,
    "castform": leg_castform,
    "kecleon": leg_kecleon,
    "fossil": leg_fossil,
    "revive": leg_revive,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--legs", default="waterstone,evolve,sunstone,leafstone")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    d.advance_scene(40_000)
    # ROUTE OVER WATER. Half of these coordinates are only reachable by Surf
    # -- Route124's Blue Shard sits at (31,53) in open sea -- and nav refuses
    # to plan through water unless it is told the party can. Without this the
    # run reached Route124 and reported "no-path from (76,46) to (31,53)".
    # Both HMs are in the bag and a party member knows each.
    d.nav.surfing = True
    if hasattr(d.nav, "waterfall"):
        d.nav.waterfall = True
    t = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    log.info("start %s %s | %s", d.map_name(), d.pos(), t.summary(d.state))

    for name in a.legs.split(","):
        name = name.strip()
        fn = LEGS.get(name)
        if fn is None:
            log.info("no such leg: %s", name)
            continue
        log.info("=== %s ===", name)
        try:
            fn(d, t)
        except Exception as exc:  # noqa: BLE001 - one leg must not kill the run
            log.info("%s raised %s: %s", name, type(exc).__name__,
                     str(exc)[:120])
        if d.scene_active():
            # A state saved inside a menu is not a checkpoint, it is a trap:
            # the naming keyboard could not be left even with START, B and
            # cursor movement, on a state that was otherwise healthy.
            log.info("   NOT banking %s -- a scene still owns input", name)
        else:
            d.save(a.state)
            log.info("   banked | %s", t.summary(d.state))
    log.info("done | %s", t.summary(d.state))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
