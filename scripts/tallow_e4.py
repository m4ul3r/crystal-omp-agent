"""tallow: the Elite Four + Lance, one member per call (fork per member).

    .venv/bin/python scripts/tallow_e4.py saves/tallow.state WILL|KOGA|BRUNO|KAREN|LANCE
"""
import logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("tallow")
from scripts.tallow_lib import boot, settle_dialog, heal_at, matchup_policy, set_lead, tactics_policy

state, member = sys.argv[1], sys.argv[2].upper()
ROOMS = {"WILL": "WILLS_ROOM", "KOGA": "KOGAS_ROOM", "BRUNO": "BRUNOS_ROOM", "KAREN": "KARENS_ROOM", "LANCE": "LANCES_ROOM"}
TRAINER_Y = {"LANCE": 3}
TABLE = {
    "WILL": {"SLOWBRO": "SUGAR", "XATU": "EMBER", "JYNX": "EMBER", "EXEGGUTOR": "EMBER"},
    "KOGA": {"MUK": "CRUST", "CROBAT": "CRUST", "FORRETRESS": "EMBER", "ARIADOS": "EMBER", "VENOMOTH": "EMBER"},
    "BRUNO": {"ONIX": "BRINE", "HITMONTOP": "EMBER", "HITMONCHAN": "EMBER", "HITMONLEE": "EMBER", "MACHAMP": "EMBER"},
    "KAREN": {"UMBREON": "CRUMB", "VILEPLUME": "EMBER", "GENGAR": "EMBER", "MURKROW": "EMBER", "HOUNDOOM": "BRINE"},
    "LANCE": {},
}
LEAD = {"WILL": "EMBER", "KOGA": "EMBER", "BRUNO": "EMBER", "KAREN": "EMBER", "LANCE": "EMBER"}

d = boot(state)
party = lambda: [(m["nick"], m["level"], m["hp"], m["max_hp"]) for m in d.observe()["party"]]


def enter_room(goal):
    for _ in range(6):
        cur = d.map_name()
        if goal in cur:
            return True
        if "POKECENTER_1F" in cur:
            d.goto(6, 9); d.goto(6, 8); d.goto(9, 7); d.step_dir("U"); d.goto(15, 3); d._step("L")
        else:
            d.goto(4, 3); d._step("U")
        d.settle(); settle_dialog(d)
        log.info("  entered %s %s", d.map_name(), d.pos()[2:])
    return False


if "POKECENTER_1F" in d.map_name():
    heal_at(d, "INDIGO_PLATEAU_POKECENTER_1F")
    set_lead(d, LEAD[member], "EMBER" if LEAD[member] != "EMBER" else "CRUST")
    d.save(f"tallow-pre-{member.lower()}.state", force=True); d.save(force=True)
set_lead(d, LEAD[member], "EMBER" if LEAD[member] != "EMBER" else "CRUST")
assert enter_room(ROOMS[member]), d.map_name()
ty = TRAINER_Y.get(member, 7)
m0 = d.observe()["money"]
def lance_policy(frame):
    """EMBER carries: potion early (a Dragonite Hyper Beam is ~100), never let it
    faint without a REVIVE, everyone else is a revive-turn shield."""
    if not isinstance(frame, dict):
        return ("attack", 0)
    me = frame.get("me") or {}
    party = frame.get("party") or d.observe()["party"]
    bag = frame.get("bag") or d.observe()["bag"]
    hp, mx = me.get("hp", 0), max(me.get("max_hp", 1), 1)
    nicks = [m.get("nick") for m in party]
    ember_i = nicks.index("EMBER") if "EMBER" in nicks else None
    ember_hp = party[ember_i]["hp"] if ember_i is not None else 0
    cur = d.emu.read_u8("wCurBattleMon")
    active = nicks[cur] if cur < len(nicks) else None
    moves = frame.get("moves") or []
    def best_attack():
        scored = [(i, (m.get("power") or 0) * (m.get("effect_mult") if m.get("effect_mult") is not None else 1))
                  for i, m in enumerate(moves) if (m.get("pp") or 0) > 0 and (m.get("power") or 0) > 0
                  and str(m.get("name", "")).upper() not in ("SELFDESTRUCT", "EXPLOSION", "FLY", "CUT", "STRENGTH")]
        return ("attack", max(scored, key=lambda t: t[1])[0]) if scored else ("attack", 0)
    if active == "EMBER":
        status = me.get("status")
        if status and bag.get("FULLRESTORE"):
            act = ("item", "FULL RESTORE")
        elif hp < 115 and bag.get("HYPERPOTION"):
            act = ("item", "HYPER POTION")
        elif hp < 115 and bag.get("FULLRESTORE"):
            act = ("item", "FULL RESTORE")
        else:
            act = best_attack()
    else:
        if ember_hp > 0 and frame.get("can_switch", True):
            act = ("switch", ember_i)
        elif ember_hp <= 0 and bag.get("REVIVE") and ember_i is not None:
            act = ("item", "REVIVE", ember_i)
        else:
            act = best_attack()
    log.info("  [lance] T%s %s %s/%s vs %s %s -> %s | party %s", frame.get("turn"), me.get("name"), hp, mx,
             (frame.get("enemy") or {}).get("name"), (frame.get("enemy") or {}).get("hp"), act,
             [(m.get("nick"), m.get("hp")) for m in party])
    return act
d.default_policy = matchup_policy(d, TABLE[member], fallback=lance_policy if member == "LANCE" else None)
log.info("[%s] party %s money %s", member, party(), m0)
if member == "LANCE":
    # nav refuses the (4,5)/(5,5) approach scene cells: goto (5,6), step U by hand
    assert d.goto(5, 6), d.last_goto_reason
    d.step_hold("U"); d.settle(); settle_dialog(d)
    for _ in range(40):
        if d.battle():
            break
        d.press("A:4 .:30")
else:
    d.goto(5, ty + 1)
    d.talk_to(5, ty)
for _ in range(40):
    if d.battle():
        break
    d.press("A:4 .:30")
if d.battle():
    d.fight(max_frames=300000)
d.settle(); settle_dialog(d); d.close_menus()
obs = d.observe()
won = d.battle() == 0 and obs["money"] > m0
log.info("[%s] %s money %s -> %s party %s", member, "WON" if won else "not verified", m0, obs["money"], party())
log.info("%s", d.last_battle.summary() if d.last_battle else None)
if won:
    for m in d.observe()["party"]:
        if m["hp"] <= 0 and d.observe()["bag"].get("REVIVE"):
            log.info("revive %s: %s", m["nick"], d.use_item("REVIVE", mon=m["nick"]))
    log.info("heal_party: %s", d.heal_party(items=["HYPER POTION", "FULL RESTORE"]))
    log.info("party %s bag %s", party(), {k: v for k, v in d.observe()["bag"].items() if k in ("HYPERPOTION", "FULLRESTORE", "REVIVE")})
    d.save(f"tallow-e4-{member.lower()}.state", force=True); d.save(force=True)
else:
    d.save("tallow-e4-dbg.state", force=True)
