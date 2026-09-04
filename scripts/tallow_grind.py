"""tallow: persona-shaped leveling. The lowest under-target mon LEADS (full exp),
the anchor only steps in when the trainee is outleveled or low. Planned wild
species get caught on the way (encounter_policy in tallow_lib).

    .venv/bin/python scripts/tallow_grind.py saves/tallow.state UNION_CAVE_1F \\
        '{"FLOUR": 14, "CRUST": 14}' EMBER AZALEA_POKECENTER_1F AZALEA_TOWN,ROUTE_33 [max_battles]
"""
import json, logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tallow")
from scripts.tallow_lib import (boot, settle_dialog, travel, save_clean, heal_at,
                                trainee_policy, set_lead, owned_nicks, tactics_policy)

state, route, targets, anchor, center = sys.argv[1:6]
targets = json.loads(targets)
heal_via = [m for m in sys.argv[6].split(",") if m] if len(sys.argv) > 6 else []
max_battles = int(sys.argv[7]) if len(sys.argv) > 7 else 120

d = boot(state)
party = lambda: [(m["nick"], m["level"], m["hp"], m["max_hp"]) for m in d.observe()["party"]]


def go_route():
    for m in heal_via + [route]:
        assert travel(d, m), (m, d.map_name())


def needs():
    """{nick: gap} for mons under target (unowned planned nicks count as gap 99)."""
    have = {m["nick"]: m for m in d.observe()["party"] if not m.get("egg")}
    out = {}
    for nick, lvl in targets.items():
        if nick not in have:
            out[nick] = 99
        elif have[nick]["level"] < lvl:
            out[nick] = lvl - have[nick]["level"]
    return out


go_route()
cells = d.find_tiles("grass") or d.find_tiles("floor")
here = d.pos()[2:]
near = sorted(cells, key=lambda c: abs(c[0]-here[0]) + abs(c[1]-here[1]))[:16]
box = (min(c[0] for c in near), max(c[0] for c in near),
       min(c[1] for c in near), max(c[1] for c in near))
log.info("grind on %s box=%s targets=%s", route, box, targets)
battles = 0
while battles < max_battles:
    gap = needs()
    if not gap:
        break
    have = {m["nick"] for m in d.observe()["party"]}
    owned_gap = {k: v for k, v in gap.items() if k in have}
    if owned_gap:
        trainee = max(owned_gap, key=owned_gap.get)
        set_lead(d, trainee, anchor)
        d.default_policy = trainee_policy(d, trainee, anchor)
    else:                              # only hunting: anchor leads, hook catches
        set_lead(d, anchor)
        d.default_policy = tactics_policy(d)
    if d._bag().get("POKEBALL", 0) == 0 and any(k not in have for k in gap):
        raise SystemExit("out of POKE BALLs while a planned species is still unowned -- buy more")
    lead = d.lead()
    fainted = [m["nick"] for m in d.observe()["party"] if m["hp"] <= 0 and not m.get("egg")]
    if lead["hp"] < lead["max_hp"] * 0.4 or fainted:
        log.info("heal rail (%s %d/%d, fainted %s)", lead.get("nick"), lead["hp"], lead["max_hp"], fainted)
        heal_at(d, center)
        go_route()
    if tuple(d.pos()[2:]) not in set(near):
        d.goto(*near[0])
    r = d.pace(60, box=box, on_battle="fight")
    battles += r["battles"]
    settle_dialog(d)
    log.info("battles=%d stopped=%s party=%s gap=%s", battles, r["stopped"], party(), needs())
    if r["stopped"] in ("warp", "whiteout"):
        log.info("left the map: %s %s", d.map_name(), d.pos()[2:])
        if r["stopped"] == "whiteout":
            raise SystemExit("WHITEOUT")
        go_route()
    if battles and battles % 10 == 0:
        save_clean(d)
log.info("grind done: battles=%d party=%s moves=%s", battles, party(), d.move_changes)
heal_at(d, center)
save_clean(d)
