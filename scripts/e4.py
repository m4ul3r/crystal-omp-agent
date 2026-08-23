"""E4 member runner. usage: python scripts/e4.py <MEMBER>
MEMBER in WILL KOGA BRUNO KAREN LANCE. Assumes lobby start or any E4 room.
Saves milestone saves/e4-<member>-omp.state only on verified win.
"""
import sys
sys.path.insert(0, ".")
from trek import Driver

STATE = "saves/omp_speed_run.state"
ROOMS = {
    "WILL": "WILLS_ROOM",
    "KOGA": "KOGAS_ROOM",
    "BRUNO": "BRUNOS_ROOM",
    "KAREN": "KARENS_ROOM",
    "LANCE": "LANCES_ROOM",
}
TRAINER_Y = {"LANCE": 3}


def e4_policy(rows, me, enemy):
    """Early heals, status clears, PP-aware best attack."""
    if me.get("status"):
        return ("item", "FULL HEAL")
    frac = me["hp"] / max(me["max_hp"], 1)
    if frac < 0.55:
        return ("item", "HYPER POTION" if frac >= 0.3 else "MAX POTION")
    prefer = ["FLAMETHROWER", "STRENGTH", "SWIFT", "CUT"]
    by_name = {m["name"]: (i, m["pp"]) for i, m in enumerate(me["moves"])}
    for name in prefer:
        if name in by_name and by_name[name][1] > 0:
            return ("attack", by_name[name][0])
    return None




def money(d):
    return d.observe().get("money", d.lead().get("money"))


def heal_at_pc(d):
    """Nurse at (3,7); stand (3,9), face U, talk through the jingle."""
    d.goto(3, 9)
    d.step_dir("U")
    d.press("A:2 .:30")
    d.flush_dialog()
    d.press(".:300")
    d.flush_dialog()
    m = d.lead()
    assert m["hp"] == m["max_hp"], f"heal failed {m['hp']}/{m['max_hp']}"
    print("[healed]", m["name"], m["hp"], "/", m["max_hp"])


def enter_room(d, member):
    """From anywhere, chain transitions until inside `member`'s room."""
    goal = ROOMS[member]
    for _ in range(6):
        cur = d.map_name()
        if goal in cur:
            return
        if "POKECENTER_1F" in cur:
            # counter gaps + NE corridor are planner-hostile; hand-walk
            d.goto(6, 9)
            d.goto(6, 8)
            d.goto(9, 7)
            d.step_dir("U")
            d.goto(15, 3)
            d._step("L")             # warp fires sideways
        else:
            d.goto(4, 3)
            d._step("U")
        d.settle()
        print("  entered", d.map_name(), d.pos())
    raise RuntimeError(f"never reached {goal}, stuck in {d.map_name()}")




def main():
    member = sys.argv[1].upper()
    d = Driver(STATE)

    if "POKECENTER_1F" in d.map_name():
        heal_at_pc(d)
        d.save()

    enter_room(d, member)
    ty = TRAINER_Y.get(member, 7)
    m0 = money(d)

    d.goto(5, ty + 1)
    print("[talking]", member, "at", d.pos(), "money", m0)
    d.talk_to(5, ty)

    import time
    t0 = time.time()
    while d.battle() == 0 and time.time() - t0 < 20:
        d.press(".:40")
    if d.battle():
        d.fight(max_frames=260000, policy=e4_policy)
    print("[post-fight] battle=", d.battle(), "pos", d.pos())
    d.settle()
    d.close_menus()

    m1 = money(d)
    won = d.battle() == 0 and m1 > m0
    if won:
        d.save(f"e4-{member.lower()}-won.state")
        print("[milestone saved]")
    else:
        print("[NOT VERIFIED -- working state has post-fight position; "
              "inspect before saving]")


if __name__ == "__main__":
    main()
