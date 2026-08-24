"""Homeward + Kanto land route: current map -> ... -> VIRIDIAN_CITY.
One continuous process, checkpoint after every verified leg.
"""
import sys
import time
sys.path.insert(0, ".")
from trek import Driver

STATE = "saves/omp_speed_run.state"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def travel_leg(d, dest, tries=3):
    goal = d._resolve_map(dest)
    if d.map_name() == goal:
        return True
    for i in range(tries):
        try:
            d.travel(goal)
            d.settle()
            if d.map_name() == goal:
                return True
            log(f"  [travel {dest}] landed {d.map_name()} {d.pos()}")
        except Exception as e:
            log(f"  [travel {dest}] {i}: {e!r}")
    return d.map_name() == goal


def hop(d, stand, mv, want_sub):
    """goto stand cell, hold step `mv`, expect map name containing want_sub."""
    d.goto(*stand)
    d._step(mv)
    d.settle()
    ok = want_sub in d.map_name()
    log(f"hop {stand} {mv} -> {d.map_name()} {d.pos()} ok={ok}")
    return ok


def main():
    d = Driver(STATE)
    log("start", d.map_name(), d.pos())
    # --- walk south off Ecruteak onto Route 36 if starting in the city
    if "ECRUTEAK" in d.map_name():
        for _ in range(8):
            r = d.step_dir("D")
            if r == "battle":
                d.fight()
                continue
            d.settle()
            if "ROUTE_36" in d.map_name():
                break

    # --- Route 36 -> park gate -> park -> gate -> Route 35 -> Goldenrod
    if "ROUTE_36" not in d.map_name():
        if not travel_leg(d, "ROUTE_36"):
            raise RuntimeError("no Route 36")
    for _ in range(3):                      # silence the grass
        d.close_menus()
        if d.use_item("MAX REPEL"):
            break
    # hand-walk to below the gate doors at (18,8)/(18,9)
    p = d.pos()[2:]
    while cap > 0:
        cap -= 1
        p = d.pos()[2:]
        if p[1] < 10 and p[0] == 18:
            break
        if p[1] < 10 or p[0] != 18:
            mv = "L" if p[0] > 18 else "R"
            if p[1] >= 10:
                mv = "U"
        else:
            break
        r = d.step_dir(mv)
        if r == "battle":
            d.fight()
            continue
        if r == "blocked":
            for alt in ("D", "L", "R", "U"):
                if alt != mv and d.step_dir(alt) in ("moved", "warp"):
                    break
    d._step("U")
    d.settle()
    log("gate hop ->", d.map_name(), d.pos())
    if "NATIONAL_PARK_GATE" not in d.map_name():
        raise RuntimeError("no R36 park gate")
    # inside gate: walk west/north to park exit (0,4)/(0,5)
    d.goto(1, 4)
    d._step("L")
    d.settle()
    log("gate ->", d.map_name(), d.pos())
    if "NATIONAL_PARK" not in d.map_name():
        raise RuntimeError("park entry failed")
    d.save()

    # National Park -> Route 35 gate (south-west of park)
    if not travel_leg(d, "ROUTE_35"):
        raise RuntimeError("no Route 35")
    # Route 35 south to the Goldenrod gate doors (9,33)/(10,33)
    d.goto(9, 34)
    d._step("D")
    d.settle()
    log("r35 ->", d.map_name(), d.pos())
    if "GOLDENROD_GATE" not in d.map_name():
        raise RuntimeError("goldenrod gate fail")
    # through the gate south to Goldenrod
    d.goto(3, 6)
    d._step("D")
    d.settle()
    log("->", d.map_name(), d.pos())
    if "GOLDENROD" not in d.map_name():
        raise RuntimeError("goldenrod fail")
    d.save()

    # --- Goldenrod -> Ilex -> Azalea ---
    if not travel_leg(d, "ILEX_FOREST"):
        raise RuntimeError("no Ilex")
    d.goto(8, 26)
    d.use_cut(8, 25)
    d.settle()
    if not travel_leg(d, "AZALEA_TOWN", tries=2):
        raise RuntimeError("no Azalea")
    d.save()

    # --- Azalea -> Union Cave -> Route 33 ---
    if not travel_leg(d, "UNION_CAVE_1F"):
        raise RuntimeError("no Union Cave")
    if not travel_leg(d, "ROUTE_33"):
        raise RuntimeError("no Route 33")
    d.save()

    # --- Route 33 -> Violet -> Cherrygrove -> New Bark ---
    if not travel_leg(d, "VIOLET_CITY"):
        raise RuntimeError("no Violet")
    d.save()
    if not travel_leg(d, "CHERRYGROVE_CITY"):
        raise RuntimeError("no Cherrygrove")
    d.save()
    if not travel_leg(d, "NEW_BARK_TOWN"):
        raise RuntimeError("no New Bark")
    d.save()
    log("home:", d.map_name(), d.pos())


if __name__ == "__main__":
    main()
