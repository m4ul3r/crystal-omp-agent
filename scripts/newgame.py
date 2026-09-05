#!/usr/bin/env python
"""Drive a raw power-on to the start of a new game, and save a checkpoint.

The Sapphire analog of Crystal's ``scripts/newgame_bedroom.py``. Where that one
had to recognise screens by decoding the tilemap, this one asks the engine
directly: ``gTasks`` holds function pointers and the symbol table names them,
so "the gender menu is up" is ``Task_NewGameSpeech16 in tasks()`` -- exact,
not a text-matching heuristic.

    scripts/newgame.py --state saves/run.state --name AGENT [--girl]
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent import paths  # noqa: E402
from pokeagent.cconst import Constants  # noqa: E402
from pokeagent.charmap import Charmap  # noqa: E402
from pokeagent.emu import Sapphire  # noqa: E402
from pokeagent.names import Names  # noqa: E402
from pokeagent.naming import NamingScreen  # noqa: E402
from pokeagent.state import GameState  # noqa: E402
from pokeagent.symbols import Symbols  # noqa: E402

log = logging.getLogger("newgame")

#: src/main_menu.c:971 -- the task that owns the boy/girl menu.
GENDER_TASK = "Task_NewGameSpeech16"


def drive_until(emu, predicate, what, tap="A:4 .:16", max_frames=60000):
    """Tap a button until `predicate` holds. Loud on timeout: a silent
    give-up here would save a checkpoint in the wrong place."""
    spent = 0
    while spent < max_frames:
        if predicate():
            return
        emu.run_sequence(tap)
        spent += 20
    raise TimeoutError(f"never reached {what} within {max_frames} frames")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=str(paths.SAVES_DIR / "newgame.state"))
    ap.add_argument("--name", default="AGENT")
    ap.add_argument("--girl", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s"
    )

    sym, cm, consts = Symbols(), Charmap(), Constants()
    emu = Sapphire(sym=sym, charmap=cm)
    names = Names(emu, cm, consts)
    st = GameState(emu, names, consts)
    kb = NamingScreen(emu, st)

    def tasks():
        return st.tasks()

    log.info("booting through the attract sequence...")
    drive_until(emu, lambda: st.callback_name() == "MainCB2", "the title screen",
                tap=".:30", max_frames=8000)
    log.info("title screen  @%d", emu.frame)

    drive_until(emu, lambda: any("MainMenu" in t for t in tasks()), "the main menu",
                tap="START:4 .:20", max_frames=4000)
    log.info("main menu     @%d", emu.frame)

    # NEW GAME is the top entry on a cartridge with no save file.
    drive_until(emu, lambda: any("NewGameSpeech" in t for t in tasks()),
                "Birch's speech", max_frames=6000)
    log.info("birch speech  @%d", emu.frame)

    drive_until(emu, lambda: GENDER_TASK in tasks(), "the boy/girl menu")
    log.info("gender menu   @%d -> %s", emu.frame, "girl" if args.girl else "boy")
    if args.girl:
        emu.run_sequence("DOWN:6 .:16")
    emu.run_sequence("A:6 .:40")

    drive_until(emu, kb.is_open, "the naming keyboard")
    log.info("naming keyboard @%d", emu.frame)
    typed = kb.type(args.name)
    log.info("typed %r", typed)

    # The rest of the speech, the truck ride, and the first room.
    drive_until(emu, lambda: st.callback_name() == "CB2_Overworld", "the overworld")
    emu.tick(240)
    log.info("overworld     @%d", emu.frame)

    out = Path(args.state)
    out.parent.mkdir(parents=True, exist_ok=True)
    emu.save_state(out)
    log.info("saved %s", out)
    log.info("%s", st.status_line())
    log.info("player name in save block: %r", st.player_name())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
