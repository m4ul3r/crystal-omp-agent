"""Chain through already-beaten E4 members to `member` and fight them.
Beaten members are passed by (never talked to). Saves milestone on win.
usage: python scripts/e4_chain.py <MEMBER>
"""
import sys
sys.path.insert(0, ".")
from trek import Driver
from e4_helpers import make_policy

STATE = "saves/omp_speed_run.state"
ROOMS = {
    "WILL": ("WILLS_ROOM", "BEAT_ELITE_4_WILL", 7),
    "KOGA": ("KOGAS_ROOM", "BEAT_ELITE_4_KOGA", 7),
    "BRUNO": ("BRUNOS_ROOM", "BEAT_ELITE_4_BRUNO", 7),
    "KAREN": ("KARENS_ROOM", "BEAT_ELITE_4_KAREN", 7),
    "LANCE": ("LANCES_ROOM", "BEAT_CHAMPION_LANCE", 3),
}

from trek import Driver as _D
_orig_fight = _D.fight
_log_n = [0]
CURRENT = [None]


def _policy_fight(self, max_frames=90000, policy=None):
    use = policy or CURRENT[0]
    def pol(rows, me, enemy):
        _log_n[0] += 1
        act = use(rows, me, enemy) if use else None
        if _log_n[0] % 4 == 1:
            print(f"[t{_log_n[0]}] me={me.get('name')} "
                  f"{me.get('hp')}/{me.get('max_hp')} "
                  f"enemy={enemy.get('name')} "
                  f"{enemy.get('hp')}/{enemy.get('max_hp')} act={act}",
                  flush=True)
        return act
    return _orig_fight(self, max_frames=max_frames, policy=use)

_D.fight = _policy_fight

ORDER = ["WILL", "KOGA", "BRUNO", "KAREN", "LANCE"]


def enter_room(d, member):
    goal = ROOMS[member][0]
    for _ in range(8):
        cur = d.map_name()
        if goal in cur:
            return
        if "POKECENTER_1F" in cur:
            d.goto(6, 9)
            d.goto(6, 8)
            d.goto(9, 7)
            d.goto(16, 7)
            d.step_dir("U")
            d.goto(15, 3)
            d._step("L")
        else:
            d.goto(4, 3)
            d._step("U")
        d.settle()
        print("  entered", d.map_name(), d.pos(), flush=True)
    raise RuntimeError(f"never reached {goal}, stuck in {d.map_name()}")


def main():
    member = sys.argv[1].upper()
    d = Driver(STATE)
    print("start", d.map_name(), d.pos(), flush=True)
    enter_room(d, member)

    room, flag, ty = ROOMS[member]
    m0 = d.observe().get("money")
    beaten = d._event_flag(flag)
    print(f"[target] {member} beaten={beaten} money={m0}", flush=True)
    if beaten:
        print("[already beaten -- nothing to do]")
        return

    prefer = (["STRENGTH", "SWIFT", "FLAMETHROWER", "CUT"]
              if member == "LANCE" else None)
    CURRENT[0] = make_policy(d.names, prefer=prefer)

    d.goto(5, ty + 1)
    d.talk_to(5, ty)
    import time
    t0 = time.time()
    while d.battle() == 0 and time.time() - t0 < 20:
        d.press(".:40")
    if d.battle():
        d.fight(max_frames=260000, policy=CURRENT[0])
    else:
        print("[battle resolved inside talk_to]", flush=True)
    d.settle()
    d.close_menus()

    m1 = d.observe().get("money")
    won = d.battle() == 0 and (d._event_flag(flag) or m1 > m0)
    print(f"[money] {m0} -> {m1}", flush=True)
    if won:
        d.save(f"e4-{member.lower()}-won.state")
        print("[milestone saved]", flush=True)
    else:
        print("[NOT WON -- inspect working state]", flush=True)


if __name__ == "__main__":
    main()
