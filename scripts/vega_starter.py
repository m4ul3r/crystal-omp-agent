"""vega session: bedroom -> Elm's lab -> CYNDAQUIL starter -> save.
Usage: .venv/bin/python scripts/vega_starter.py [state]
Timeline facts baked in:
- mom scene on 1F row 4 is long (Pokegear + day-select + DST); drain hard.
- Elm's lab auto-dialog fires on first entry; then talk to Elm (5,2);
  only then do the ball tiles respond.
- ball A-press sometimes needs one retry (first press eats straggler page).
"""
import logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("vega")
import trek
from crystalagent.state import game_state

STATE = sys.argv[1] if len(sys.argv) > 1 else "saves/vega.state"
d = trek.Driver(STATE)
e = d.emu


def drain(rounds=150):
    """Answer every page/menu/day-picker until long-quiet (A = default)."""
    quiet = 0
    for _ in range(rounds):
        d.press(".:40")
        if d.keyboard_open():
            d.press("A:6 .:60")          # day picker / any naming
            quiet = 0
            continue
        if d.textbox() or d.menu_open():
            quiet = 0
            d.press("A:6 .:60")
        else:
            quiet += 1
    return quiet


def decline_keyboard(max_tries=10):
    """B out of a naming keyboard; last resort confirm empty via START+A."""
    for _ in range(max_tries):
        if not d.keyboard_open():
            return
        d.press("B:4 .:30")
    d.press("START:4 .:20 A:4 .:30")


# 1) bedroom -> downstairs
d.goto(7, 1)
d.press("U:60 .:60")
d.settle()
assert d.map_name() == "PLAYERS_HOUSE_1F", d.map_name()

# 2) row 4 -> mom scene
d.press("D:16 D:16 D:16 D:16 .:80")
drain(200)

# 3) out the front door, retrying if coord events refire
for attempt in range(6):
    try:
        d.goto(7, 6)
        break
    except RuntimeError:
        log.info("mom refire %d", attempt)
        drain(200)
d.settle()
d.press("D:60 .:60")
d.settle()
drain(50)
assert d.map_name() == "NEW_BARK_TOWN", f"{d.map_name()} {d.pos()[2:]}"
log.info("outside: %s %s", d.map_name(), d.pos()[2:])
d.save("vega-outside.state")

# 4) into Elm's lab; entry auto-dialog
d.goto(6, 4)
d.press("U:60 .:60")
d.settle()
assert d.map_name() == "ELMS_LAB", f"{d.map_name()} {d.pos()[2:]}"
drain(200)

# 5) full Elm speech (5,2)
d.goto(5, 3)
d.step_dir("U")
d.press("A:4 .:60")
drain(300)

# 6) Cyndaquil ball (6,3): prompt may need a second press
for i in range(5):
    d.goto(6, 4)
    d.step_dir("U")
    d.press("A:6 .:100")
    if d.textbox():
        break
# YES -> receive -> decline nickname keyboard -> sweep leftovers
for _ in range(60):
    d.press(".:40")
    if d.keyboard_open():
        decline_keyboard()
        continue
    if d.textbox() or d.menu_open():
        d.press("A:6 .:60")
drain(150)
d.settle()

gs = game_state(e, d.names)
party = [(m["species"], m.get("level")) for m in gs["party"]]
log.info("party after starter: %s", party)
if not party:
    sys.exit("no party member -- starter flow failed")

d.save("vega-starter.state")
log.info("saved vega-starter.state at %s %s", d.map_name(), d.pos()[2:])
