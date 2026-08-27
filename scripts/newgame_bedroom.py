#!/usr/bin/env python3
"""Fresh boot -> NEW GAME -> named trainer -> control in the bedroom.

Every frame of this is watchable: the driver attaches a LiveFeed
(`crystalagent/live.py`), so watch.py shows the title screen, Oak's
speech, the clock prompt, the naming keyboard and the walk-in -- none of
which is ever savestated, and all of which the old re-simulating viewer
was blind to.

    .venv/bin/python watch.py                     # http://127.0.0.1:8123/
    .venv/bin/python scripts/newgame_bedroom.py --state saves/watch.state

Stops the moment the game hands over control in PLAYERS_HOUSE_2F (the
new-game spawn, `data/maps/spawn_points.asm`: 3,3) and saves there.
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
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", default="saves/watch.state",
                    help="working savestate to create (default: %(default)s)")
    ap.add_argument("--name", default="OMP", help="trainer name to type")
    ap.add_argument("--speed", type=float, default=2.0,
                    help="emulation pace, x real time (0 = flat out)")
    ap.add_argument("--fps", type=float, default=12.0,
                    help="frames published to the viewer per second")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger("trek")

    state = Path(args.state)
    if state.exists():
        sys.exit(f"{state} exists and this leg starts a NEW game -- move it "
                 f"aside or pass --state")

    # fresh=True: power-on reset, no savestate to load.
    d = trek.Driver(str(state), fresh=True,
                    live={"name": state.stem, "fps": args.fps,
                          "speed": args.speed})
    note = d.live.note
    e = d.emu

    # 1) title / GS logo: pulse START until the main menu decodes
    note(f"boot: fresh power-on, heading for a new game as {args.name}")
    for pulse in range(60):
        e.tick(240)
        d.press("START:8 .:20")
        if e.screen_contains("NEW GAME"):
            log.info("main menu after %d START pulses", pulse + 1)
            break
    else:
        sys.exit("never reached the main menu")

    # 2) NEW GAME is the default cursor row
    note("main menu: NEW GAME")
    d.press("A:6 .:30")
    e.tick(60)

    # 3) InitGender -> InitClock -> OakSpeech (engine/menus/intro_menu.asm:61).
    #    Everything in there is a textbox or a default-cursor choice, so A
    #    carries it; the one prompt that must NOT be mashed is the naming
    #    keyboard (press() freezes on it by design), so type deliberately.
    note("intro: Oak's speech, gender and the clock")
    named = False
    deadline = time.time() + 1800
    while time.time() < deadline:
        if d.keyboard_open():
            note(f"naming keyboard: typing {args.name}")
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

    # 4) the game hands control over in the bedroom: stop there
    d.settle()
    where, (x, y) = d.map_name(), d.pos()[2:]
    note(f"overworld control: {where} ({x},{y})")
    if where != BEDROOM:
        sys.exit(f"expected to wake up in {BEDROOM}, got {where} ({x},{y})")
    gs = game_state(d.emu, d.names)
    log.info("player=%r %s", gs["player"]["name"], status_line(gs))
    d.save()
    note(f"checkpoint: {state} (in the bedroom, nothing else touched)")
    log.info("saved %s", state)


if __name__ == "__main__":
    main()
