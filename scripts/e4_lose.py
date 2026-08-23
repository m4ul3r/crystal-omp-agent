"""Deliberate loss to Karen: flee-spam until wipe -> whiteout -> lobby.
Then heal at the Plateau nurse and save. Restores PP for the real attempt.
"""
import sys
sys.path.insert(0, ".")
from trek import Driver

STATE = "saves/omp_speed_run.state"


from trek import Driver as _D
_orig_fight = _D.fight
_n = [0]
def _log_fight(self, max_frames=90000, policy=None):
    def pol(rows, me, enemy):
        _n[0] += 1
        if _n[0] % 3 == 1:
            print(f"[t{_n[0]}] me={me.get('name')} L{me.get('level')} "
                  f"{me.get('hp')}/{me.get('max_hp')} "
                  f"enemy={enemy.get('name')} L{enemy.get('level')} "
                  f"{enemy.get('hp')}/{enemy.get('max_hp')}", flush=True)
        return None
    return _orig_fight(self, max_frames=max_frames, policy=pol)
_D.fight = _log_fight

def main():
    d = Driver(STATE)
    print("start", d.map_name(), d.pos(), flush=True)
    if "KARENS_ROOM" not in d.map_name():
        d.goto(4, 3)
        d._step("U")
        d.settle()
    print("room:", d.map_name(), d.pos(), flush=True)
    m0 = d.observe().get("money")
    d.goto(5, 8)
    d.talk_to(5, 7)
    import time
    t0 = time.time()
    while d.battle() == 0 and time.time() - t0 < 20:
        d.press(".:40")
    if not d.battle():
        print("[battle over inside talk_to]", flush=True)
    else:
        out = d.fight(max_frames=80000, policy=lambda r, me, e: "flee")
        print("[outcome]", out, flush=True)
        d.settle()
    # whiteout should have moved us; find out where
    import time as _t
    t1 = _t.time()
    while _t.time() - t1 < 30:
        d.press(".:60")
        if "ROUTE_23" in d.map_name() or "POKECENTER" in d.map_name():
            break
    d.settle()
    print("after wipe:", d.map_name(), d.pos(),
          "money", d.observe().get("money"), flush=True)
    if "ROUTE_23" in d.map_name() or "POKECENTER" in d.map_name():
        # walk into the lobby if we're outside
        if "ROUTE_23" in d.map_name():
            d._step("U")
            d.settle()
        d.goto(3, 9)
        d.step_dir("U")
        d.press("A:2 .:30")
        d.flush_dialog()
        d.press(".:300")
        d.flush_dialog()
        m = d.lead()
        assert m["hp"] == m["max_hp"], "nurse heal failed"
        d.save()
        print("[healed + saved]", flush=True)


if __name__ == "__main__":
    main()
