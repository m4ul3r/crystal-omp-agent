"""tallow session: bedroom -> Elm's lab -> CYNDAQUIL named EMBER -> save.

    .venv/bin/python scripts/tallow_starter.py saves/tallow.state
"""
import logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tallow")
import trek
from crystalagent.state import game_state

STATE = sys.argv[1] if len(sys.argv) > 1 else "saves/tallow.state"
d = trek.Driver(STATE)
e = d.emu

from crystalagent.driver.navigation import TravelError
for attempt in range(8):
    try:
        d.travel("ELMS_LAB")
        break
    except TravelError as ex:
        log.info("travel retry %d: %s", attempt, ex)
        if d.menu_open():
            d.resolve_choice("YES")
        d.drain_scene()
        d.settle()
d.settle()
d.drain_scene()
assert d.map_name() == "ELMS_LAB", f"{d.map_name()} {d.pos()[2:]}"
log.info("in lab at %s", d.pos()[2:])


def settle_dialog(tag, rounds=30):
    """Page dialog; answer YES/NO boxes with YES; type EMBER on a keyboard."""
    for _ in range(rounds):
        r = d.flush_dialog()
        log.info("%s flush -> %s", tag, r)
        if d.keyboard_open():
            d.name_prompt("EMBER")
            continue
        if r == "menu" or d.menu_open():
            d.resolve_choice("YES")
            continue
        if not d.textbox():
            break


# Elm's entry speech ("help me with my research?" YES/NO)
settle_dialog("elm-entry")
d.settle()
log.info("lab: %s", d.pos()[2:])

# Cyndaquil ball at (6,3): YES -> receive -> naming keyboard -> EMBER
d._pending_nickname = "EMBER"
for i in range(5):
    d.goto(6, 4)
    d.step_dir("U")
    d.press("A:6 .:100")
    if d.textbox() or d.menu_open():
        break
settle_dialog("ball")
log.info("screen after ball:\n%s", "\n".join(e.screen_text()))
d.drain_scene()
d.settle()

gs = game_state(e, d.names)
party = [(m["species"], m.get("nickname"), m.get("level")) for m in gs["party"]]
log.info("party after starter: %s", party)
if not party:
    sys.exit("no party member -- starter flow failed")
d.save(force=True)
log.info("saved %s at %s %s", STATE, d.map_name(), d.pos()[2:])
