"""tallow: chain travel() across maps, KO every wild, checkpoint each leg.

    .venv/bin/python scripts/tallow_journey.py saves/tallow.state MAP1 MAP2 ...
"""
import logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tallow")
import trek

STATE, targets = sys.argv[1], sys.argv[2:]
d = trek.Driver(STATE)
d.encounter_policy = lambda frame: "ko"


def settle_dialog(tag, rounds=30):
    for _ in range(rounds):
        r = d.flush_dialog()
        if r == "menu" or d.menu_open():
            d.resolve_choice("YES")
            continue
        if not d.textbox():
            break


for t in targets:
    dest = d._resolve_map(t)
    if d.map_name() == dest:
        continue
    ok = False
    for attempt in range(4):
        try:
            d.travel(dest)
            d.settle()
            if d.map_name() == dest:
                ok = True
                break
            log.info("[leg %s] landed on %s, retrying", t, d.map_name())
        except Exception as ex:
            log.info("[leg %s] attempt %d: %r", t, attempt, ex)
            settle_dialog("leg")
            d.drain_scene()
            d.settle()
    if not ok:
        raise SystemExit(f"could not reach {dest}: {d.map_name()} {d.pos()}")
    m = d.lead()
    log.info("[leg] -> %s %s lead L%d %d/%d", dest, d.pos()[2:], m["level"],
             m["hp"], m["max_hp"])
    d.save()
log.info("journey complete: %s %s", d.map_name(), d.pos()[2:])
