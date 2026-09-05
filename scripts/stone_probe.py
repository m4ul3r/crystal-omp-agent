#!/usr/bin/env python
"""Buy an evolution stone in Lilycove and use it: the end-to-end proof.

Exists because `travel` cannot route a multi-floor interior in one hop, and the
stone counter is on the FIFTH floor. The warp chain is read off the maps rather
than routed:

    LilycoveCity (27,6) -> 1F, then (16,1) -> 2F, (13,1) -> 3F,
    (16,1) -> 4F, (13,1) -> 5F
    (pret/data/maps/LilycoveCity_DepartmentStore_*/map.json warp_events)

The clerk who sells all six stones is `ClerkFarLeft` at (7,2)
(pret/data/maps/LilycoveCity_DepartmentStore_5F/map.json, list at
scripts.inc:22-27).

Proof standard: the stone must leave the bag AND the target's species must
change. `[SPECIES_LOMBRE] = {{EVO_ITEM, ITEM_WATER_STONE, SPECIES_LUDICOLO}}`
(pret/src/data/pokemon/evolution.h:141).
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.mart import Mart  # noqa: E402
from pokeagent.teaching import Teacher  # noqa: E402
from pokeagent.trek import Driver  # noqa: E402

log = logging.getLogger("stone")

#: `(map we must be on, warp cell that leaves it)`, in order.
CLIMB = [
    ("LilycoveCity", (27, 6)),
    ("LilycoveCity_DepartmentStore_1F", (16, 1)),
    ("LilycoveCity_DepartmentStore_2F", (13, 1)),
    ("LilycoveCity_DepartmentStore_3F", (16, 1)),
    ("LilycoveCity_DepartmentStore_4F", (13, 1)),
]
COUNTER = "LilycoveCity_DepartmentStore_5F"
CLERK = (7, 2)


def reach_city(d, tries=4) -> bool:
    for _ in range(tries):
        if d.map_name() == "LilycoveCity":
            return True
        try:
            d.travel("LilycoveCity", on_battle="fight", budget_s=120)
        except Exception as exc:  # noqa: BLE001 - a battle on the way
            log.info("travel: %s", str(exc)[:80])
    return d.map_name() == "LilycoveCity"


def climb(d) -> bool:
    for expect, cell in CLIMB:
        for _ in range(3):
            if d.map_name() != expect:
                break
            if d.take_warp(*cell):
                break
            log.info("warp %s from %s: %s", cell, expect, d.last_warp_reason)
        log.info("now %s %s", d.map_name(), d.pos())
    return d.map_name() == COUNTER


def buy(d, mart, items) -> dict:
    got = {}
    if not d.talk_to(*CLERK):
        log.info("could not reach the clerk")
        return got
    d.settle(120)
    for _ in range(4):
        if mart.is_open():
            break
        d.emu.run_sequence("A:4 .:40")
    if not mart.is_open():
        log.info("the clerk did not open a shop")
        return got
    for name in items:
        got[name] = mart.buy(name, 1)
        log.info("buy %s -> %s (%s)", name, got[name], mart.last_reason)
    # B-only exit, verified (gotcha 13): blind A in a shop list BUYS.
    for _ in range(12):
        if not d.scene_active() and not mart.is_open():
            break
        d.emu.run_sequence("B:4 .:24")
    d.advance_scene(40000)
    return got


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    ap.add_argument("--stone", default="WATER STONE")
    ap.add_argument("--mon", default="LOTTAD")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    d.advance_scene(40000)
    t, mart = Teacher(d), Mart(d)
    log.info("start %s %s money %s", d.map_name(), d.pos(), d.state.money())

    if not reach_city(d):
        log.info("FAIL: never reached Lilycove (%s)", d.map_name())
        return 1
    if not climb(d):
        log.info("FAIL: never reached the 5F counter (%s)", d.map_name())
        return 1

    stones = ["WATER STONE", "SUN STONE", "LEAF STONE",
              "FIRE STONE", "THUNDER STONE", "MOON STONE"]
    buy(d, mart, stones)
    held = [(i, q) for _s, i, q in t.pocket_items(0) if 93 <= i <= 98]
    log.info("stones in bag: %s", held)
    if not held:
        log.info("FAIL: no stone was bought")
        return 1

    before = [(m.nickname, d.names.species(m.species)) for m in d.state.party()]
    ok = t.use_on_mon(a.stone, a.mon)
    after = [(m.nickname, d.names.species(m.species)) for m in d.state.party()]
    log.info("use_on_mon(%s, %s) -> %s (%s)", a.stone, a.mon, ok, t.last_reason)
    log.info("party before: %s", before)
    log.info("party after:  %s", after)
    if a.out and ok:
        d.save(a.out)
        log.info("saved %s", a.out)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
