"""tallow: withdraw SUGAR, leave Azalea west (rival), Ilex Forest: herd Farfetch'd
(facing table from maps/IlexForest.asm), HM01 CUT, cut the north tree, exit to Route 34.

    .venv/bin/python scripts/tallow_ilex.py saves/tallow.state
"""
import logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tallow")
from scripts.tallow_lib import (boot, settle_dialog, travel, save_clean, heal_at,
                                trainee_policy, set_lead)

# wFarfetchdPosition -> (cell, allowed player facings).  A facing the script
# lists sends the bird BACKWARD, so only the complement advances it.
BIRD = {
    1: ((14, 31), "UDLR"),
    2: ((15, 25), "ULR"),      # not DOWN
    3: ((20, 24), "URD"),      # not LEFT
    4: ((29, 22), "DLR"),      # not UP
    5: ((28, 31), "D"),        # only DOWN
    6: ((24, 35), "ULD"),      # not RIGHT
    7: ((22, 31), "UR"),       # not DOWN/LEFT
    8: ((15, 29), "D"),        # only DOWN
    9: ((10, 35), "UL"),       # not DOWN/RIGHT
}

d = boot(sys.argv[1])
party = lambda: [(m["nick"], m["level"], m["hp"], m["max_hp"]) for m in d.observe()["party"]]
d.default_policy = None       # EMBER leads the rival with the tactics policy

# 0) SUGAR back in the party, heal, fork before the rival
if "SUGAR" not in {m["nick"] for m in d.observe()["party"]}:
    assert travel(d, "AZALEA_POKECENTER_1F"), d.map_name()
    log.info("withdraw SUGAR: %s %s", d.withdraw("SUGAR"), d.last_pc_reason)
heal_at(d, "AZALEA_POKECENTER_1F")
set_lead(d, "EMBER")
save_clean(d, "tallow-pre-rival2.state")

# 1) west out of town: the rival ambush fires at the Ilex gate
for m in ["AZALEA_TOWN", "ILEX_FOREST_AZALEA_GATE"]:
    assert travel(d, m), (m, d.map_name())
    settle_dialog(d)
    log.info("[leg] %s %s party %s", m, d.pos()[2:], party())
log.info("rival beaten: %s", d._event_flag("EVENT_RIVAL_AZALEA_TOWN"))
heal_at(d, "AZALEA_POKECENTER_1F")
set_lead(d, "CRUST")
d.default_policy = trainee_policy(d, "CRUST", "EMBER", margin=4, hp_floor=0.35)
for m in ["AZALEA_TOWN", "ILEX_FOREST_AZALEA_GATE", "ILEX_FOREST"]:
    assert travel(d, m), (m, d.map_name())
save_clean(d, "tallow-ilex.state")

# 2) herd the bird
d.use_item("REPEL")
log.info("repel: %s", d.last_item_reason)
for step in range(12 if not d._event_flag("EVENT_HERDED_FARFETCHD") else 0):
    pos = max(1, d.emu.read_u8("wFarfetchdPosition"))
    if pos >= 10:
        break
    cell, facings = BIRD[pos]
    live = [(o["x"], o["y"]) for o in d.map_objects() if o["sprite"] == "SPRITE_BIRD"]
    log.info("bird position %d at %s (map_objects says %s)", pos, cell, live)
    done = False
    for f in facings:
        r = d.talk_to(*cell, facing=f)
        settle_dialog(d)
        d.drain_scene()
        d.settle()
        new = d.emu.read_u8("wFarfetchdPosition")
        log.info("  facing %s -> talk %s, position %d -> %d", f, r, pos, new)
        if new != pos:
            done = True
            break
    if not done:
        raise SystemExit(f"could not advance the bird from position {pos}")
log.info("herded=%s", d._event_flag("EVENT_HERDED_FARFETCHD"))
# 3) HM01 from the Charcoal Master (5,28)
if "HM01" not in d.observe()["bag"]:
    d.talk_to(5, 28)
    settle_dialog(d)
log.info("bag %s", d.observe()["bag"])
assert "HM01" in d.observe()["bag"], d.observe()["bag"]
save_clean(d, "tallow-hm01.state")

# 4) teach CUT to FLOUR (persona: birds carry CUT), cut the north tree, exit
flour = next(m for m in d.observe()["party"] if m["nick"] == "FLOUR")
forget = "SAND-ATTACK" if len(flour["moves"]) >= 4 else None
log.info("teach_tm HM01 -> FLOUR (forget %s): %s %s", forget,
         d.teach_tm("HM01", "FLOUR", forget=forget), d.last_tm_reason)
log.info("field moves %s", d.field_moves())
trees = d.find_tiles("cut-tree")
log.info("cut trees: %s", trees)
tree = min(trees, key=lambda c: c[1])      # the north one
ok = d.cut(*tree)
log.info("cut %s -> %s %s", tree, ok, d.last_field_reason)
if not ok:
    d.sync_grid()
assert travel(d, "ROUTE_34"), (d.map_name(), d.pos()[2:])
log.info("[leg] ROUTE_34 %s party %s bag %s", d.pos()[2:], party(), d.observe()["bag"])
save_clean(d, "tallow-r34.state")
