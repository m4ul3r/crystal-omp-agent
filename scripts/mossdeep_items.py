#!/usr/bin/env python
"""Collect HM08 DIVE and the SUPER ROD, the two items Mossdeep still owes.

Neither is gated on badge 7 -- Steven hands DIVE over on the first frame you
stand in his house while `VAR_STEVENS_HOUSE_STATE == 0`
(`pret/data/maps/MossdeepCity_StevensHouse/scripts.inc:25-51`); only USING Dive
checks `FLAG_BADGE07_GET`. The Super Rod is a plain `giveitem` from the fisher
in House 3 (`MossdeepCity_House3/scripts.inc:12-13`).

Both are verified by the FLAG the script sets, never by having walked
somewhere: `FLAG_RECEIVED_HM08` and `FLAG_RECEIVED_SUPER_ROD`.
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver  # noqa: E402

log = logging.getLogger("items")

#: Mossdeep's own warp table (`MossdeepCity/map.json`).
STEVENS_DOOR = (19, 10)
HOUSE3_DOOR = (49, 6)
GYM_DOOR = (38, 9)


def leave_gym(d) -> bool:
    """Out of the gym and into the city."""
    if d.map_name() != "MossdeepCity_Gym":
        return True
    # The gym's exit warps are (9,29)/(10,29); the floor between here and there
    # is the puzzle, so ask the game rather than the static grid.
    if not d.reach_cell(9, 29, map_name="MossdeepCity_Gym", on_battle="fight"):
        log.info("could not reach the gym door: %s", d.last_goto_reason)
        return False
    return d.take_warp(9, 29) or d.map_name() == "MossdeepCity"


def collect(d, door, flag, label) -> bool:
    """Walk through `door`, let the script run, and check its flag."""
    if d.state.flag(flag):
        log.info("%s: already have it", label)
        return True
    if d.map_name() != "MossdeepCity" and not d.travel("MossdeepCity"):
        log.info("%s: could not get back to MossdeepCity", label)
        return False
    if not d.take_warp(*door):
        log.info("%s: door %s refused: %s", label, door, d.last_warp_reason)
        return False
    # Steven's gift is an OnFrame script: it fires by standing there. The Super
    # Rod needs the fisher talked to.
    for _ in range(3):
        d.advance_scene(90000)
        if d.state.flag(flag):
            break
    if not d.state.flag(flag):
        for spot in ((4, 4), (5, 4), (4, 3)):
            try:
                if d.talk_to(*spot):
                    d.advance_scene(90000)
            except Exception:  # noqa: BLE001 - nobody there is not an error
                continue
            if d.state.flag(flag):
                break
    got = d.state.flag(flag)
    log.info("%s: %s", label, "GOT IT" if got else "still missing")
    d.take_warp(*d.exits()[0].values()) if False else None
    return got

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    log.info("start %s %s badges %d", d.map_name(), d.pos(),
             len(d.state.badges()))
    if not leave_gym(d):
        return 1

    ok = True
    for door, flag, label in (
        (STEVENS_DOOR, "FLAG_RECEIVED_HM08", "HM08 DIVE"),
        (HOUSE3_DOOR, "FLAG_RECEIVED_SUPER_ROD", "SUPER ROD"),
    ):
        if not collect(d, door, flag, label):
            ok = False
        if d.map_name() != "MossdeepCity":
            d.travel("MossdeepCity")

    log.info("RESULT dive=%s rod=%s | field moves %s",
             d.state.flag("FLAG_RECEIVED_HM08"),
             d.state.flag("FLAG_RECEIVED_SUPER_ROD"),
             {k: v for k, v in d.field_moves().items() if v})
    if a.out:
        d.save(a.out)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
