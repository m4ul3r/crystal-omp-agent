"""Supervised Karen fight with per-turn trajectory logging."""
import sys
sys.path.insert(0, ".")
from trek import Driver
from e4_helpers import e4_policy

STATE = "saves/omp_speed_run.state"

calls = [0]


def logged_policy(rows, me, enemy):
    calls[0] += 1
    try:
        act = e4_policy(rows, me, enemy)
        if calls[0] % 5 == 1:
            emy = {k: enemy.get(k) for k in ("name", "level", "hp", "max_hp")}
            mine = {k: me.get(k) for k in ("hp", "max_hp", "status")}
            mvs = [(m.get("name"), m.get("pp")) for m in me.get("moves", [])]
            print(f"[t{calls[0]:3d}] enemy={emy} me={mine} moves={mvs} "
                  f"act={act}", flush=True)
        return act
    except Exception as e:
        print("[policy error]", repr(e), flush=True)
        return None


orig_fight = Driver.fight
def _patched_fight(self, max_frames=90000, policy=None):
    return orig_fight(self, max_frames=max_frames, policy=logged_policy)
Driver.fight = _patched_fight


def main():
    d = Driver(STATE)
    print("start", d.map_name(), d.pos(), flush=True)
    # enter Karen's room from Bruno's
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
    if d.battle():
        out = d.fight(max_frames=260000, policy=logged_policy)
        print("[outcome]", out, flush=True)
    else:
        print("[no battle]", flush=True)
    d.settle()
    d.close_menus()
    m1 = d.observe().get("money")
    print("money", m0, "->", m1, "pos", d.map_name(), d.pos(), flush=True)
    for m in d.observe()["party"]:
        print(m["species"], m["hp"], "/", m["max_hp"], flush=True)


if __name__ == "__main__":
    main()
