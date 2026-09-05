#!/usr/bin/env python
"""Seafloor Cavern -> Kyogre -> HM07 WATERFALL -> badge 8.

Every beat is verified by the FLAG or VAR its own script sets, never by having
arrived somewhere -- the badge-7 run lost eight sessions to primitives that
reported success they had not achieved. Citations are to the decomp under
`pret/`, read by the route scout:

* Route128 has a `dive` connection to Underwater4, offset 0, and both layouts
  are 120x40, so (x,y) maps 1:1 (`Route128/map.json:28-32`).
* `Underwater4 (38,26)` warps to `Underwater_SeafloorCavern`, which surfaces
  you at `SeafloorCavern_Entrance (10,17)` (`Underwater_SeafloorCavern
  /scripts.inc:35-37`).
* The boss trigger is `SeafloorCavern_Room9 (17,42)` while
  `VAR_SEAFLOOR_CAVERN_STATE == 0`; it ends by warping to Route128 (38,22) and
  sets VAR_SOOTOPOLIS_STATE=1 (`Room9/scripts.inc:193-215`).
* Sootopolis is reached by surfacing under it from the Route126 dive field
  (`Underwater_SootopolisCity/scripts.inc:5-7`).
* HM07 WATERFALL is an item ball at `CaveOfOrigin_B3F (6,5)` -- the only one in
  the game (`item_ball_scripts.inc:477-479`).
* Kyogre is the coord_event at `CaveOfOrigin_B4F (9,13)`; the flag block runs
  after the battle whatever its outcome, and sets FLAG_LEGENDARY_BATTLE_
  COMPLETED, which is what unlocks the city and the gym (`B4F/scripts.inc:66-79`).
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402
from pokeagent import nav as nav_mod  # noqa: E402
from boulder_solver import walk as boulder_walk, snapshot, solve  # noqa: E402

log = logging.getLogger("badge8")


def note(d, msg):
    log.info("%s | at %s %s", msg, d.map_name(), d.pos())


def go(d, dest, tries=6) -> bool:
    """`travel` that fights instead of raising.

    Its default is `on_battle="raise"`, and every route in this chain is open
    sea -- wall-to-wall encounters. The very first leg died on a Tentacool at
    MossdeepCity (33,37). Retried because a long journey can be interrupted
    more than once.
    """
    if d.map_name() == dest:
        return True
    for _ in range(tries):
        try:
            if d.travel(dest, on_battle="fight"):
                return True
        except TravelInterrupted:
            if d.in_battle():
                d.fight(policy=Driver.damage_first)
            d.advance_scene(40000)
            continue
        except Exception as exc:  # noqa: BLE001
            # TravelError too: a seam refuses while a wild is on screen, or a
            # surf leg starts from the wrong side. Fight anything up, settle,
            # and let the next attempt re-plan from where we actually are.
            log.info("  travel to %s: %s", dest, str(exc)[:90])
            if d.in_battle():
                d.fight(policy=Driver.damage_first)
            d.advance_scene(40000)
            continue
        if d.map_name() == dest:
            return True
    return d.map_name() == dest


def dive_here(d, tries=14) -> bool:
    """Find a diveable tile on this map, stand on it, and go under."""
    if d.underwater():
        return True
    here = d.map_name()
    grid = d.nav.grid(here)
    spots = [(x, y) for y, row in enumerate(grid) for x, c in enumerate(row)
             if c is not None and c.behavior in nav_mod.DIVEABLE]
    if not spots:
        log.info("no diveable tile on %s", here)
        return False
    px, py = d.pos()
    spots.sort(key=lambda p: abs(p[0] - px) + abs(p[1] - py))
    for spot in spots[:tries]:
        try:
            if not d.goto(*spot, on_battle="fight"):
                continue
        except Exception:  # noqa: BLE001 - a battle mid-route is not fatal
            continue
        if d.dive():
            note(d, "dived")
            return True
        log.info("  dive at %s refused: %s", spot, d.last_field_reason)
    return False


def surface(d) -> bool:
    """Come up. Underwater maps set their own dive warp, so anywhere works
    unless the ceiling forbids it."""
    if not d.underwater():
        return True
    if d.dive():
        note(d, "surfaced")
        return True
    grid = d.nav.grid(d.map_name())
    for y, row in enumerate(grid):
        for x, c in enumerate(row):
            if c is None or c.collision or c.behavior in nav_mod.NO_SURFACING:
                continue
            try:
                if d.goto(x, y, on_battle="fight") and d.dive():
                    note(d, "surfaced")
                    return True
            except Exception:  # noqa: BLE001
                continue
    return False


def beat_seafloor(d) -> bool:
    """Route128 -> Underwater4 -> Seafloor Cavern -> the Aqua leader."""
    if d.state.var("VAR_SEAFLOOR_CAVERN_STATE"):
        log.info("BEAT seafloor: already done")
        return True
    # ALREADY INSIDE counts as done. The boss var only flips at the very end,
    # so without this the chain re-entered this beat from within the cavern and
    # spent its whole budget trying to travel back out to Route 128 -- a
    # journey that starts with a warp it cannot approach from the inside.
    if d.map_name().startswith(("SeafloorCavern", "Underwater_SeafloorCavern")):
        log.info("BEAT seafloor: already in the cavern (%s)", d.map_name())
        return True
    if d.map_name() != "Route128" and not go(d, "Route128"):
        log.info("could not reach Route128")
        return False
    if not dive_here(d):
        return False
    # Underwater4 shares Route128's grid, so the cavern hatch is a plain walk.
    # ARRIVING COUNTS. The hatch is a warp on the route, so walking to it drops
    # through before `goto` can report success -- and the first run read that
    # ("left Underwater4 for Underwater_SeafloorCavern mid-route") as a failure
    # while standing exactly where it wanted to be.
    if d.map_name() == "Underwater4":
        try:
            d.goto(38, 26, on_battle="fight")
        except Exception:  # noqa: BLE001 - dropping through the hatch is the goal
            pass
    if d.map_name() == "Underwater4":
        if not d.take_warp(38, 26):
            log.info("hatch refused: %s", d.last_warp_reason)
            return False
    if d.map_name() != "Underwater_SeafloorCavern":
        log.info("expected the cavern, standing in %s", d.map_name())
        return False
    note(d, "in the cavern's underwater room")
    if not surface(d):
        return False
    return True


#: The cavern as a graph, read from each room's own warp_events. A room is a
#: set of doors, not one step in a fixed chain -- Room2 has three onward doors
#: and the run wedged its boulders against the first one, which under a fixed
#: chain was fatal.
CAVERN = {
    "SeafloorCavern_Entrance": {(10, 1): "SeafloorCavern_Room1"},
    "SeafloorCavern_Room1": {(6, 2): "SeafloorCavern_Room2",
                             (17, 13): "SeafloorCavern_Room5"},
    "SeafloorCavern_Room2": {(12, 2): "SeafloorCavern_Room7",
                             (5, 2): "SeafloorCavern_Room6",
                             (5, 19): "SeafloorCavern_Room4"},
    "SeafloorCavern_Room4": {(4, 1): "SeafloorCavern_Room5",
                             (13, 1): "SeafloorCavern_Room2",
                             (9, 10): "SeafloorCavern_Room5"},
    "SeafloorCavern_Room5": {(15, 12): "SeafloorCavern_Room4",
                             (7, 17): "SeafloorCavern_Room4",
                             (4, 1): "SeafloorCavern_Room1"},
    "SeafloorCavern_Room6": {(4, 1): "SeafloorCavern_Room3",
                             (11, 21): "SeafloorCavern_Room2"},
    "SeafloorCavern_Room7": {(5, 1): "SeafloorCavern_Room3",
                             (3, 23): "SeafloorCavern_Room2"},
    "SeafloorCavern_Room3": {(8, 1): "SeafloorCavern_Room8",
                             (10, 13): "SeafloorCavern_Room7",
                             (4, 15): "SeafloorCavern_Room6"},
    "SeafloorCavern_Room8": {(5, 2): "SeafloorCavern_Room9",
                             (5, 12): "SeafloorCavern_Room3"},
}


def hops_to_room9(room):
    """Rooms ordered by how few doors they are from Room9."""
    dist = {"SeafloorCavern_Room9": 0}
    frontier = ["SeafloorCavern_Room9"]
    while frontier:
        nxt = []
        for here in frontier:
            for src, doors in CAVERN.items():
                if here in doors.values() and src not in dist:
                    dist[src] = dist[here] + 1
                    nxt.append(src)
        frontier = nxt
    return dist.get(room)


def beat_room9(d) -> bool:
    """Through the cavern to the boss trigger at Room9 (17,42).

    Routes over the room GRAPH, retrying whichever onward door is closest to
    Room9 and falling back to the others. Boulders persist across a map
    reload -- Gen 3 keeps them in the save -- so a room whose boulders have
    been shoved into a dead arrangement cannot be reset, only gone around.
    """
    if d.state.var("VAR_SEAFLOOR_CAVERN_STATE"):
        return True
    tried = {}
    for _ in range(40):
        here = d.map_name()
        if here == "SeafloorCavern_Room9":
            break
        if here not in CAVERN:
            log.info("lost the cavern: standing in %s", here)
            return False
        # Doors that get closer to Room9 first, then the rest.
        doors = sorted(
            CAVERN[here].items(),
            key=lambda kv: (hops_to_room9(kv[1]) if hops_to_room9(kv[1])
                            is not None else 99, tried.get((here, kv[0]), 0)),
        )
        moved = False
        for door, dest in doors:
            if tried.get((here, door), 0) >= 3:
                continue
            tried[(here, door)] = tried.get((here, door), 0) + 1
            for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                spot = (door[0] + dx, door[1] + dy)
                cell = d.nav.cell(here, *spot)
                if cell is None or cell.collision:
                    continue
                # ASK BEFORE WALKING. Room2 is a ladder of two one-wide columns
                # with a boulder at every rung junction: pushing up column 11
                # jams against the boulder above, so (12,3) is genuinely
                # unreachable and every attempt on it was ten wasted minutes.
                # The same BFS that walks the room answers this in a second.
                if d.boulder_signature():
                    walls, boulders, others, _rocks, _elev = snapshot(d)
                    if solve(walls, boulders, others, d.pos(),
                             [spot]) is None:
                        log.info("  %s: %s is unreachable, skipping", here, spot)
                        continue
                try:
                    if d.boulder_signature():
                        d.clear_rocks()
                        boulder_walk(d, spot)
                    else:
                        d.reach_cell(*spot, map_name=here, on_battle="fight")
                except Exception as exc:  # noqa: BLE001
                    log.info("  %s: %s", here, str(exc)[:70])
                    if d.in_battle():
                        d.fight(policy=Driver.damage_first)
                if d.map_name() != here:
                    moved = True
                    break
                if d.pos() == spot and d.take_warp(*door):
                    moved = True
                    break
            if moved:
                note(d, f"{here} -> {d.map_name()}")
                break
        if not moved:
            log.info("  no door out of %s worked from %s", here, d.pos())
    if d.map_name() != "SeafloorCavern_Room9":
        log.info("never reached Room9 (in %s)", d.map_name())
        return False

    try:
        d.reach_cell(17, 42, map_name="SeafloorCavern_Room9",
                     on_battle="fight")
    except Exception:  # noqa: BLE001
        if d.in_battle():
            d.fight(policy=Driver.damage_first)
    for _ in range(8):
        d.advance_scene(120000)
        if d.in_battle():
            d.fight(policy=Driver.damage_first)
        if d.state.var("VAR_SEAFLOOR_CAVERN_STATE"):
            break
    log.info("BEAT seafloor -> VAR_SEAFLOOR_CAVERN_STATE=%s SOOTOPOLIS=%s",
             d.state.var("VAR_SEAFLOOR_CAVERN_STATE"),
             d.state.var("VAR_SOOTOPOLIS_STATE"))
    return bool(d.state.var("VAR_SEAFLOOR_CAVERN_STATE"))


def beat_sootopolis(d) -> bool:
    """Dive under Route126 and surface inside the city."""
    if d.map_name() == "SootopolisCity":
        return True
    if not go(d, "Route126"):
        log.info("could not reach Route126")
        return False
    if not dive_here(d):
        return False
    # Underwater2 -> the basin under the city.
    if d.map_name() != "Underwater_SootopolisCity":
        if not go(d, "Underwater_SootopolisCity"):
            log.info("could not swim to the city basin")
            return False
    if not surface(d):
        return False
    ok = d.map_name() == "SootopolisCity"
    log.info("BEAT sootopolis -> %s %s", d.map_name(), d.pos())
    return ok


def beat_escort(d) -> bool:
    """The Steven/Wallace cutscene that opens the Cave of Origin."""
    if d.state.var("VAR_SOOTOPOLIS_STATE") >= 2:
        return True
    if not d.reach_cell(25, 6, map_name="SootopolisCity", on_battle="fight"):
        log.info("could not reach the escort trigger: %s", d.last_goto_reason)
        return False
    for _ in range(6):
        d.advance_scene(120000)
        if d.state.var("VAR_SOOTOPOLIS_STATE") >= 2:
            break
    log.info("BEAT escort -> VAR_SOOTOPOLIS_STATE=%s",
             d.state.var("VAR_SOOTOPOLIS_STATE"))
    return d.state.var("VAR_SOOTOPOLIS_STATE") >= 2


def beat_cave(d) -> bool:
    """Down the Cave of Origin, collecting HM07, and wake Kyogre."""
    if d.state.flag("FLAG_LEGENDARY_BATTLE_COMPLETED"):
        return True
    hops = [
        ("SootopolisCity", (31, 16)),
        ("CaveOfOrigin_Entrance", (9, 5)),
        ("CaveOfOrigin_1F", (14, 5)),
        ("CaveOfOrigin_B1F", (5, 11)),
        ("CaveOfOrigin_B2F", (8, 14)),
    ]
    for name, door in hops:
        if d.map_name() != name and not go(d, name):
            log.info("could not reach %s", name)
            return False
        if not d.reach_cell(*door, map_name=name, on_battle="fight"):
            log.info("could not reach %s %s: %s", name, door,
                     d.last_goto_reason)
            return False
        if not d.take_warp(*door):
            log.info("%s door %s refused: %s", name, door, d.last_warp_reason)
            return False
        note(d, f"through {name}")

    # HM07 WATERFALL: the game's only copy.
    if not d.state.flag("FLAG_ITEM_CAVE_OF_ORIGIN_B3F_1"):
        if d.reach_cell(6, 5, map_name="CaveOfOrigin_B3F", on_battle="fight"):
            d.advance_scene(60000)
        log.info("HM07 WATERFALL: %s",
                 "GOT IT" if d.state.flag("FLAG_ITEM_CAVE_OF_ORIGIN_B3F_1")
                 else "MISSED")

    if not d.reach_cell(12, 6, map_name="CaveOfOrigin_B3F", on_battle="fight"):
        log.info("could not reach the B4F stair: %s", d.last_goto_reason)
        return False
    if not d.take_warp(12, 6):
        return False
    # Kyogre. The flag block runs after the battle whatever happens, so a loss
    # still advances the plot -- but a win is a dex entry.
    if not d.reach_cell(9, 13, map_name="CaveOfOrigin_B4F", on_battle="fight"):
        log.info("could not reach Kyogre: %s", d.last_goto_reason)
        return False
    for _ in range(8):
        d.advance_scene(120000)
        if d.in_battle():
            d.fight(policy=Driver.damage_first)
        if d.state.flag("FLAG_LEGENDARY_BATTLE_COMPLETED"):
            break
    log.info("BEAT kyogre -> LEGENDARY_BATTLE_COMPLETED=%s",
             d.state.flag("FLAG_LEGENDARY_BATTLE_COMPLETED"))
    return d.state.flag("FLAG_LEGENDARY_BATTLE_COMPLETED")


def beat_wallace(d) -> bool:
    """Badge 8. The ice floor cracks under repeated steps; Wallace is at (8,2)."""
    if d.state.flag("FLAG_BADGE08_GET"):
        return True
    if d.map_name() != "SootopolisCity" and not go(d, "SootopolisCity"):
        return False
    if not d.reach_cell(31, 32, map_name="SootopolisCity", on_battle="fight"):
        log.info("could not reach the gym door: %s", d.last_goto_reason)
        return False
    if not d.take_warp(31, 32):
        log.info("gym door refused: %s", d.last_warp_reason)
        return False
    for attempt in range(6):
        if d.map_name() == "SootopolisCity_Gym_B1F":
            # Fell through the ice: climb back and try again.
            d.reach_cell(11, 22, map_name="SootopolisCity_Gym_B1F",
                         on_battle="fight")
            d.take_warp(11, 22)
        if not d.reach_cell(8, 3, map_name="SootopolisCity_Gym_1F",
                            on_battle="fight"):
            log.info("attempt %d: no route to Wallace: %s", attempt,
                     d.last_goto_reason)
            continue
        d.emu.run_sequence("U:4 .:20")
        d.emu.run_sequence("A:4 .:60")
        d.advance_scene(90000)
        if d.in_battle():
            d.fight(policy=Driver.damage_first)
        for _ in range(5):
            d.advance_scene(150000)
        if d.state.flag("FLAG_BADGE08_GET"):
            break
    log.info("BEAT wallace -> BADGE08=%s badges=%d",
             d.state.flag("FLAG_BADGE08_GET"), len(d.state.badges()))
    return d.state.flag("FLAG_BADGE08_GET")


BEATS = [
    ("seafloor", beat_seafloor),
    ("room9", beat_room9),
    ("sootopolis", beat_sootopolis),
    ("escort", beat_escort),
    ("cave", beat_cave),
    ("wallace", beat_wallace),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    ap.add_argument("--beat", action="append")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    log.info("START %s %s badges %d", d.map_name(), d.pos(),
             len(d.state.badges()))
    for name, fn in BEATS:
        if a.beat and name not in a.beat:
            continue
        log.info("BEAT %s", name)
        # Heal first: every one of these ends in a fight, and the badge-7 run
        # walked into the hideout with a fainted lead.
        try:
            d.heal_party()
        except Exception:  # noqa: BLE001 - no items is not a failure
            pass
        if not fn(d):
            log.info("STOPPED at beat %s", name)
            if a.out:
                d.save(a.out)
            return 1
        if a.out:
            d.save(a.out)
    log.info("CHAIN DONE at %s badges %d", d.map_name(),
             len(d.state.badges()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
