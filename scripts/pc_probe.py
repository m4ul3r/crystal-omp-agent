#!/usr/bin/env python
"""Learn the RSE Pokemon Storage UI by driving it and reading its own cursor.

22 of the missing dex species are evolutions of Pokemon sitting in the PC, and
there is no way to get a boxed mon into the party except through this UI. The
harness has never driven it -- `deposit`/`withdraw` exist only in the Crystal
tree -- so this probe establishes the button sequence and the readable state
before any driver is written against it.

What makes it drivable at all: the storage system keeps its cursor in two
plain EWRAM bytes, so nothing here has to guess from pixels.

    sBoxCursorArea      0 = the box grid, 1 = the party
                        (src/pokemon_storage_system_4.c:948-958)
    sBoxCursorPosition  index within that area
    gPokemonStorage.currentBox   which of the 14 boxes is shown
                        (include/pokemon.h:325, boxes[14][30] at 0x0004)

That is the Gen-3 answer to Crystal's gotcha 18: a box list draws its
selection with a sprite and has no cursor glyph to read, so a blind "press A
until the text stops changing" loop is a repeat-action loop. Read the index,
move deliberately, verify against the party.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver  # noqa: E402

log = logging.getLogger("pcprobe")

MB_PC = 0x83          # include/constants/metatile_behaviors.h:135


def pc_cells(d, map_name=None):
    """Every PC tile on this map, by metatile BEHAVIOUR.

    `find_tiles(kind)` cannot answer this: `behaviors.kind()` has no "pc"
    case, so a PC counter classifies as "blocked" like any other solid tile.
    """
    map_name = map_name or d.map_name()
    grid = d.nav.grid(map_name)
    return [(x, y)
            for y, row in enumerate(grid)
            for x, c in enumerate(row)
            if c.behavior == MB_PC]


def cursor(d):
    """``(area, position, current_box)`` straight out of EWRAM.

    `area` is a signed byte in the ROM but only 0 and 1 are ever meaningful,
    so it is read unsigned and reported as-is; 0xff would mean "not in a
    selectable area".
    """
    out = []
    for where in ("sBoxCursorArea", "sBoxCursorPosition",
                  ("gPokemonStorage", 0)):
        try:
            out.append(d.emu.u8(where))
        except Exception:  # noqa: BLE001
            out.append(None)
    return tuple(out)


def show(d, label):
    a, p, b = cursor(d)
    log.info("%-24s area=%-4s pos=%-4s box=%-4s scene=%s", label, a, p, b,
             d.scene_active())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--map", default=None,
                    help="fly here first (a town with a Pokemon Center)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    d.advance_scene(40_000)
    log.info("start %s %s", d.map_name(), d.pos())

    if a.map and d.map_name() != a.map:
        log.info("flying to %s", a.map)
        d.fly_to(a.map)
        d.advance_scene(20_000)
        log.info("landed %s %s", d.map_name(), d.pos())

    cells = pc_cells(d)
    log.info("PC tiles on %s: %s", d.map_name(), cells)
    if not cells:
        # Not in the Centre yet -- report what the town offers so the caller
        # can pick a warp, rather than guessing at a door.
        for e in d.nav.exits(d.map_name())[:12]:
            log.info("  exit -> %s at %s", e.get("dest"), e.get("cross_at"))
        return 1

    target = cells[0]
    log.info("approaching PC at %s", target)
    if not d.talk_to(*target):
        log.info("talk_to failed: %s", getattr(d, "last_goto_reason", "?"))
        return 1
    show(d, "after talk_to")

    # The PC menu is a multichoice whose first case is the storage system
    # (data/scripts/pc.inc:20-24), and the storage menu's first entry is
    # WITHDRAW POKEMON (src/pokemon_storage_system.c:28-33). So option 0
    # twice lands in the box grid.
    for step in range(3):
        d.emu.run_sequence("A:4 .:40")
        d.settle(400)
        show(d, f"A press {step + 1}")

    # THE REAL TEST. If the box grid is up, its cursor answers the D-pad, and
    # that is the whole basis for driving this deliberately instead of
    # mashing A. The grid is 6 wide by 5 tall (boxes[14][30]), so RIGHT
    # should be +1 and DOWN should be +6.
    for key in ("RIGHT", "DOWN", "LEFT", "UP"):
        before = cursor(d)
        d.emu.run_sequence(f"{key}:4 .:20")
        d.settle(120)
        after = cursor(d)
        log.info("  %-6s %s -> %s%s", key, before, after,
                 "   MOVED" if after != before else "   no change")

    log.info("box switching (L/R change currentBox):")
    for key in ("R", "L"):
        before = cursor(d)
        d.emu.run_sequence(f"{key}:4 .:30")
        d.settle(200)
        log.info("  %-6s %s -> %s", key, before, cursor(d))

    # Leave the UI rather than saving a state parked inside a menu.
    for _ in range(6):
        d.emu.run_sequence("B:4 .:24")
    d.settle(400)
    show(d, "after backing out")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
