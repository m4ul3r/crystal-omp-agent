#!/usr/bin/env python
"""Level the party at Sky Pillar 1F -- the best XP reachable on seven badges.

Everything else this run can walk to is L30-35 filler: Sootopolis' lake is
Magikarp and Tentacool, and twenty battles there moved nobody a single level.
Sky Pillar 1F is **L47-50 Claydol / Banette / Golbat / Sableye at a 10%
encounter rate** (`pret/src/data/wild_encounters.json`), and it is not gated --
only the crumbling floors on 2F and 4F want the Mach Bike, and this never goes
above 1F.

It also pays a dex dividend: CLAYDOL and BANETTE are both new.

Route: `Route131 (36,6)` -> SkyPillar_Entrance -> SkyPillar_Outside ->
SkyPillar_1F (`Route131/map.json:105-111`).

Healing is a Fly round trip, because there is no Center anywhere near the
pillar and the run has no money for potions.
"""
import argparse
import logging
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402

log = logging.getLogger("skypillar")

FLOOR = "SkyPillar_1F"
#: The sea chain to the pillar, ONE MAP CONNECTION AT A TIME. `travel` will not
#: plan Mossdeep -> Route131 in a single call -- too much open water -- but each
#: of these is a single edge and goes through fine.
#: Only as far as Route128. From there `sail_north` takes over, because the
#: last three legs must be crossed at controlled latitudes.
SEA_CHAIN = ["Route127", "Route128"]

#: Routes 129/130/131's SOUTHERN water is a closed system -- its only exit is
#: Pacifidlog, and nothing in it can reach Route128. Landing there means the
#: voyage is unrecoverable and has to restart from Mossdeep.
SOUTH_TRAP = ("Route129", "Route130", "Route131")

HOPS = [
    ("Route131", (36, 6)),
    ("SkyPillar_Entrance", (14, 4)),
    ("SkyPillar_Outside", (14, 5)),
]
#: PACIFIDLOG IS NOT FLYABLE -- this run never visited it, so its
#: FLAG_VISITED_PACIFIDLOG_TOWN is clear and the Fly map greys it out.
#: Mossdeep is the nearest landing that IS unlocked, and it has a Center.
HEAL_TOWN = "MossdeepCity"

#: Where to bank progress after every leg. Without this the voyage was thrown
#: away on any failure and every pass restarted from inside Sootopolis Gym --
#: five minutes of sailing discarded each time.
BANK: str | None = None


def bank(d) -> None:
    """Persist the voyage so the next pass resumes where this one stopped."""
    if BANK:
        try:
            d.save(BANK)
        except Exception:  # noqa: BLE001
            pass


def go(d, dest, tries=5) -> bool:
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
        except Exception as exc:  # noqa: BLE001
            log.info("  travel %s: %s", dest, str(exc)[:70])
            if d.in_battle():
                d.fight(policy=Driver.damage_first)
            d.advance_scene(40000)
        if d.map_name() == dest:
            return True
    return d.map_name() == dest


def escape_gym(d) -> bool:
    """Out of Sootopolis Gym. Routing cannot cross its ice, so a save left on
    that floor made every `travel` call fail from the first move -- the grind
    reported "could not climb (at SootopolisCity_Gym_1F)" forever."""
    if not d.map_name().startswith("SootopolisCity_Gym"):
        return True
    if d.map_name() == "SootopolisCity_Gym_B1F":
        d.reach_cell(11, 22, map_name=d.map_name(), on_battle="fight")
        d.take_warp(11, 22)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from ice_run import read_floor, floor_path, run_path

    for _ in range(4):
        if not d.map_name().startswith("SootopolisCity_Gym"):
            return True
        d.close_menus()
        walls, thin, cracked, _stairs = read_floor(d)
        blocked = {(o["x"], o["y"]) for o in d.live_npcs()
                   if not o.get("player")}
        path = floor_path(walls, thin, cracked, blocked, d.pos(),
                          [(8, 25), (9, 25)])
        if path is None:
            break
        run_path(d, path, d.map_name())
        for door in ((8, 25), (9, 25)):
            if d.pos() == door and d.take_warp(*door):
                break
    return not d.map_name().startswith("SootopolisCity_Gym")



#: The pillar's own sea. Route131 is TWO water bodies: the southern one every
#: ordinary route lands in, and a northern strip (y<=13 at its right edge) that
#: holds the door at (36,6). They never touch, so `travel("Route131")` -- which
#: is happy with either -- reached the map and could never reach the warp. The
#: northern strip is entered by keeping to low latitudes the whole way:
#: Route128 --down--> Route129 (arrives at y=0) --left--> Route130 --left-->
#: Route131. Connection offsets are all 0, so latitude carries across a seam.
NORTH_HOPS = [
    ("Route128", "D", None),      # drop into Route129 at its top row
    ("Route129", "L", 10),        # left edge, north of the divide
    ("Route130", "L", 11),
]


def _edge_cells(d, m, side, comp, ymax):
    """Reachable cells of `comp` sitting on `side`, low latitudes first."""
    g = d.nav.grid(m)
    w, h = len(g[0]), len(g)
    out = []
    for (x, y) in comp:
        on = (side == "L" and x == 0) or (side == "R" and x == w - 1) \
            or (side == "D" and y == h - 1) or (side == "U" and y == 0)
        if on and (ymax is None or y <= ymax):
            out.append((x, y))
    return sorted(out, key=lambda c: (c[1], c[0]))


def sail_north(d) -> bool:
    """Cross the seams at a latitude that keeps us in the pillar's own sea."""
    for _ in range(6):
        here = d.map_name()
        if here == "Route131":
            return True
        step = dict((m, (s, y)) for m, s, y in NORTH_HOPS).get(here)
        if step is None:
            return False
        side, ymax = step
        comp = set(d.nav.reachable(here, d.pos(), d.elevation()))
        cells = _edge_cells(d, here, side, comp, ymax)
        if not cells:
            log.info("  no %s-edge cell on %s at y<=%s", side, here, ymax)
            return False
        for cell in cells[:6]:
            try:
                d.reach_cell(*cell, map_name=here, on_battle="fight")
            except Exception:  # noqa: BLE001
                if d.in_battle():
                    d.fight(policy=Driver.damage_first)
            if d.pos() != cell or d.map_name() != here:
                continue
            for _ in range(3):
                d.step_dir(side)
                if d.map_name() != here:
                    break
            if d.map_name() != here:
                break
        if d.map_name() == here:
            log.info("  could not cross %s off %s", side, here)
            return False
        log.info("  %s -%s-> %s %s", here, side, d.map_name(), d.pos())
        bank(d)
    return d.map_name() == "Route131"

def climb(d) -> bool:
    """Route131 -> the first floor of the pillar."""
    if not escape_gym(d):
        log.info("  stuck inside the gym at %s", d.pos())
        return False
    bank(d)
    # FLY OUT FIRST. Sootopolis is a crater whose only walking exit is a dive,
    # so `travel("Route131")` from inside it fails on its first move -- the
    # grind logged "could not climb (at SootopolisCity)" on a loop. Pacifidlog
    # is the closest landing to the pillar.
    # ONLY from the crater. This used to fire on every pass, so each attempt
    # flew back to Mossdeep and threw away whatever sea legs the previous pass
    # had walked -- the log was nothing but "flew to MossdeepCity".
    if d.map_name() in ("SootopolisCity",):
        if not d.fly_to(HEAL_TOWN):
            log.info("  could not fly to %s from %s", HEAL_TOWN, d.map_name())
        else:
            log.info("  flew to %s", d.map_name())
    for _ in range(8):
        if d.map_name() == FLOOR:
            return True
        here = d.map_name()
        door = dict(HOPS).get(here)
        if door is None:
            # Walk the sea chain rather than asking for one long plan.
            if here in ("Route128",) + SOUTH_TRAP:
                if sail_north(d):
                    continue
                # Stranded in the southern sea. Nothing down here reaches
                # Route128, so the only way back to the pillar's water is to
                # fly out and re-enter from Mossdeep.
                if here in SOUTH_TRAP and d.fly_to(HEAL_TOWN):
                    log.info("  southern sea is a trap; restarting from %s",
                             d.map_name())
                    bank(d)
                    continue
                return False
            if here in SEA_CHAIN:
                nxt = SEA_CHAIN[SEA_CHAIN.index(here) + 1:] or ["Route131"]
                if not go(d, nxt[0], tries=3):
                    log.info("  stuck at %s heading for %s", here, nxt[0])
                    bank(d)
                    return False
                bank(d)
                continue
            for leg in SEA_CHAIN:
                if go(d, leg, tries=2):
                    break
            else:
                bank(d)
                return False
            bank(d)
            continue
        # APPROACH FROM BELOW, NEVER STRAIGHT AT IT. Route131's door sits at
        # (36,6) in open water near the right seam, and routing at the tile
        # itself walked off the edge into Route130/129 instead -- the log read
        # "Route131 -> Route129". Standing one tile south first keeps the
        # approach inside the map, and the warp fires on the step that ENTERS
        # the tile (harness gotcha 15).
        below = (door[0], door[1] + 1)
        for stand in (below, door):
            try:
                d.reach_cell(*stand, map_name=here, on_battle="fight")
            except Exception:  # noqa: BLE001
                if d.in_battle():
                    d.fight(policy=Driver.damage_first)
            if d.map_name() != here:
                break
            if d.pos() == stand:
                break
        if d.map_name() == here and not d.take_warp(*door):
            log.info("  %s door %s refused: %s", here, door,
                     d.last_warp_reason)
            return False
        bank(d)
        log.info("  %s -> %s %s", here, d.map_name(), d.pos())
    return d.map_name() == FLOOR


def heal_trip(d) -> bool:
    """Fly out, heal, come back. There is no Center near the pillar."""
    log.info("healing (lead %s)", d.state.party()[0].hp)
    if not d.fly_to(HEAL_TOWN):
        return False
    # STRAIGHT TO THE CENTER DOOR. Walking whatever warps `exits()` happens to
    # list wandered into the wrong buildings and left the party hurt on the
    # far side of the ocean, which is what kept the voyage bouncing back down
    # the sea chain. The map's own warp table names the door.
    if "PokemonCenter" not in d.map_name():
        for w in (d.nav.info(HEAL_TOWN).warps or []):
            if "POKEMON_CENTER" not in str(w.dest_map):
                continue
            try:
                d.reach_cell(w.x, w.y + 1, map_name=HEAL_TOWN,
                             on_battle="fight")
            except Exception:  # noqa: BLE001
                pass
            if d.take_warp(w.x, w.y):
                break
    ok = d.heal()
    for e in d.exits():
        if e.get("kind") == "warp":
            d.take_warp(e["x"], e["y"])
            break
    log.info("healed=%s", ok)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    ap.add_argument("--minutes", type=float, default=240.0)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    global BANK
    BANK = a.out or a.state
    d = Driver(a.state)
    rng = random.Random(11)
    deadline = time.time() + a.minutes * 60
    battles = 0
    start = [m.level for m in d.state.party()]
    log.info("START %s %s levels %s", d.map_name(), d.pos(), start)

    while time.time() < deadline:
        if d.in_battle():
            d.fight(policy=Driver.damage_first)
            d.advance_scene(40000)
            battles += 1
            if battles % 5 == 0:
                log.info("%d battles | levels %s | %.0f min left", battles,
                         [m.level for m in d.state.party()],
                         (deadline - time.time()) / 60)
                if a.out:
                    d.save(a.out)
            continue
        d.close_menus()
        lead = d.state.party()[0]
        # PUSH ON WHILE TRAVELLING. The sea legs cost HP, and healing at a
        # third meant flying back to Mossdeep from Route131 -- the pillar's
        # doorstep -- and re-sailing the whole chain, forever. Only a genuinely
        # critical lead justifies giving up the distance; once ON the floor,
        # heal freely.
        floor = d.map_name() == FLOOR
        if lead.hp * (3 if floor else 6) < lead.max_hp:
            if not heal_trip(d):
                log.info("heal trip failed; carrying on")
            continue
        if d.map_name() != FLOOR:
            if not climb(d):
                log.info("could not climb (at %s)", d.map_name())
                d.settle(120)
            continue
        for _ in range(8):
            if d.in_battle():
                break
            d.step_dir(rng.choice("UDLR"))
            d.settle(12)

    log.info("DONE %d battles | levels %s -> %s", battles, start,
             [m.level for m in d.state.party()])
    if a.out:
        d.save(a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
