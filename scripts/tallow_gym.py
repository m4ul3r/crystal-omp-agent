"""tallow: heal, fork tallow-pre-<tag>.state, enter a gym, fight trainers then the leader.

    .venv/bin/python scripts/tallow_gym.py saves/tallow.state bugsy AZALEA_POKECENTER_1F AZALEA_GYM \\
        '[[5,3],[8,8],[0,2],[4,10],[5,10]]' 5 7 [LEAD ANCHOR]
"""
import json, logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tallow")
from scripts.tallow_lib import (boot, settle_dialog, travel, save_clean, heal_at,
                                trainee_policy, set_lead, matchup_policy)

state, tag, center, gym = sys.argv[1:5]
trainers = json.loads(sys.argv[5])
lx, ly = int(sys.argv[6]), int(sys.argv[7])
lead, anchor = (sys.argv[8], sys.argv[9]) if len(sys.argv) > 9 else (None, None)
matchup = json.loads(sys.argv[10]) if len(sys.argv) > 10 else None

d = boot(state)
party = lambda: [(m["nick"], m["level"], m["hp"], m["max_hp"]) for m in d.observe()["party"]]
log.info("scout: %s", d.gym_scout(gym))
heal_at(d, center)
if lead:
    set_lead(d, lead)
    d.default_policy = trainee_policy(d, lead, anchor, margin=4, hp_floor=0.35)
if matchup:
    d.default_policy = matchup_policy(d, matchup)
save_clean(d, f"tallow-pre-{tag}.state")


def rest():
    """Persona: no potions while a Pokecenter is in town -- walk to the nurse."""
    heal_at(d, center)
    assert travel(d, gym), d.map_name()


assert travel(d, gym), d.map_name()
for tx, ty in trainers:
    log.info("trainer at (%d,%d)", tx, ty)
    d.talk_to(tx, ty)
    settle_dialog(d)
    log.info("  party %s", party())
    p = d.observe()["party"]
    if any(m["hp"] <= 0 for m in p) or p[0]["hp"] < p[0]["max_hp"] * 0.5:
        rest()
rest()
log.info("leader at (%d,%d); party %s", lx, ly, party())
d.talk_to(lx, ly)
settle_dialog(d)
obs = d.observe()
log.info("after leader: badges=%s bag=%s party=%s", obs["badges"], obs["bag"], party())
log.info("last battle:\n%s", d.last_battle.summary() if d.last_battle else None)
save_clean(d)
