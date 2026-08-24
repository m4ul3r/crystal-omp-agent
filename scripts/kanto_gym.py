"""Kanto gym runner.
usage: python scripts/kanto_gym.py <GymMap> <leaderX> <leaderY> <FlagName>
Walks to the leader, fights with the standard policy, verifies the badge
flag, saves a milestone.
"""
import sys
import time
sys.path.insert(0, ".")
from trek import Driver
from e4_helpers import make_policy

STATE = "saves/omp_speed_run.state"


def main():
    gym_map = sys.argv[1]
    lx, ly = int(sys.argv[2]), int(sys.argv[3])
    flag = sys.argv[4]

    d = Driver(STATE)
    log = lambda *a: print(time.strftime("%H:%M:%S"), *a, flush=True)
    if gym_map not in d.map_name():
        d.travel(gym_map)
        d.settle()
    log("gym:", d.map_name(), d.pos())

    m0 = d.observe().get("money")
    d.talk_to(lx, ly)
    t0 = time.time()
    while d.battle() == 0 and time.time() - t0 < 15:
        d.press(".:40")
    if d.battle():
        d.fight(max_frames=200000, policy=make_policy(d.names))
    else:
        log("[no battle -- already beaten?]")
    d.settle()
    d.close_menus()

    beaten = d._event_flag(flag)
    m1 = d.observe().get("money")
    won = d.battle() == 0 and (beaten or m1 > m0)
    log(f"flag={beaten} money {m0}->{m1}")
    if won:
        d.save()
        log("[WON -- working state saved]")
    else:
        log("[NOT WON]")


if __name__ == "__main__":
    main()
