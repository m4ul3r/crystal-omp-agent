#!/usr/bin/env python3
"""Fresh boot -> NEW GAME -> named trainer -> control in the bedroom.

Same job as scripts/newgame_bedroom.py, minus the LiveFeed plumbing that
this build of trek.Driver does not have (no `live=` kwarg, no `d.live`).

    .venv/bin/python scripts/newgame_claude.py --state saves/claude.state \
        --name CLAUDE
"""
import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import trek                                                  # noqa: E402
from crystalagent.state import game_state, status_line        # noqa: E402

BEDROOM = "PLAYERS_HOUSE_2F"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", default="saves/claude.state")
    ap.add_argument("--name", default="CLAUDE")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger("trek")

    state = Path(args.state)
    if state.exists():
        sys.exit(f"{state} exists and this leg starts a NEW game -- move it "
                 f"aside or pass --state")

    d = trek.Driver(str(state), fresh=True)
    e = d.emu

    log.info("boot: fresh power-on, new game as %s", args.name)
    for pulse in range(60):
        e.tick(240)
        d.press("START:8 .:20")
        if e.screen_contains("NEW GAME"):
            log.info("main menu after %d START pulses", pulse + 1)
            break
    else:
        sys.exit("never reached the main menu")

    d.press("A:6 .:30")
    e.tick(60)

    log.info("intro: Oak's speech, gender and the clock")
    named = False
    deadline = time.time() + 1800
    while time.time() < deadline:
        if d.keyboard_open():
            log.info("naming keyboard: typing %s", args.name)
            d.type_name(args.name)
            named = True
            e.tick(120)
            continue
        d.press(".:20")
        e.tick(10)
        if d.keyboard_open():
            continue
        d.press("A:4")
        e.tick(30)
        group, number = e.read_u8("wMapGroup"), e.read_u8("wMapNumber")
        if named and (group, number) != (0, 0) and e.read_u8("wScriptMode") == 0:
            break
    else:
        sys.exit("intro never handed over overworld control")

    d.settle()
    where, (x, y) = d.map_name(), d.pos()[2:]
    log.info("overworld control: %s (%d,%d)", where, x, y)
    if where != BEDROOM:
        sys.exit(f"expected to wake up in {BEDROOM}, got {where} ({x},{y})")
    gs = game_state(d.emu, d.names)
    log.info("player=%r %s", gs["player"]["name"], status_line(gs))
    d.save()
    log.info("saved %s", state)


if __name__ == "__main__":
    main()
