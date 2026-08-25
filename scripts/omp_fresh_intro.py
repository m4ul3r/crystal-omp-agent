"""Fresh-boot intro for session omp-fresh: raw power-on -> named player ->
overworld control -> save omp_saves/omp-fresh-intro.state. Run: .venv/bin/python
scripts/vega_intro.py"""
import logging, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("vega")

import trek  # noqa: E402  (registers everything)
from crystalagent import emu as emu_mod  # noqa: E402

STATE = Path("omp_saves/omp-fresh-intro2.state")
PLAYER = "HERDR"

_orig = emu_mod.Crystal.__init__
def _patched(self, rom, sym, cm, state_path):
    # raw boot when our working state doesn't exist yet
    _orig(self, rom, sym, cm,
         None if (state_path and not Path(state_path).exists()) else state_path)
emu_mod.Crystal.__init__ = _patched

d = trek.Driver(str(STATE))
e = d.emu

# 1) pulse START through the title/GS logo until the main menu decodes
for i in range(40):
    e.tick(260)
    d.press("START:8 .:20")
    if "NEW GAME" in "\n".join(e.screen_text()):
        log.info("main menu after %d pulses", i + 1)
        break
else:
    sys.exit("never reached main menu")

# 2) NEW GAME -> intro cutscene
d.press("A:6 .:30")                       # NEW GAME is default cursor
e.tick(60)

# 3) mash through Oak intro; answer the player-naming keyboard
deadline = time.time() + 600
named = False
while time.time() < deadline:
    if d.keyboard_open():
        log.info("naming keyboard up; typing %s", PLAYER)
        d.type_name(PLAYER)
        d.press("A:4 .:20 START:4 .:20 A:4 .:40")   # END + confirm
        named = True
        e.tick(120)
        continue
    d.press(".:20")
    e.tick(10)
    if d.keyboard_open():
        continue
    d.press("A:4")
    e.tick(30)
    try:
        g, n = e.read_u8("wMapGroup"), e.read_u8("wMapNumber")
        sm = e.read_u8("wScriptMode")
    except Exception:
        continue
    if named and (g, n) != (0, 0) and sm == 0:
        break
else:
    sys.exit("intro never reached overworld")

d.settle()
g, n, x, y = d.pos()
log.info("overworld: map=%s (%d,%d) script=%d",
         d.map_name(), x, y, e.read_u8("wScriptMode"))
d.save()
log.info("saved %s", STATE)
