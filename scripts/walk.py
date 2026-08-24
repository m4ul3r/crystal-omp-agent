#!/usr/bin/env python3
"""Grid-path walker: find_path + execute with per-step verification and
replanning. Usage: walk.py STATE X Y [MAP] [--enter U|D|L|R]
--enter: after arriving at (x,y), step in the given direction (door entry).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from trek import Driver


def main():
    args = [a for a in sys.argv[1:]]
    enter = None
    if "--enter" in args:
        i = args.index("--enter")
        enter = args[i + 1]
        del args[i:i + 2]
    state = args[0]
    gx, gy = int(args[1]), int(args[2])
    goal_map = args[3] if len(args) > 3 else None
    d = Driver(state)

    def p():
        return tuple(d.pos()[2:])

    def m():
        return d.map_name()

    if goal_map is None:
        goal_map = m()
    print("start", (p(), m()), "->", (gx, gy), goal_map, flush=True)
    for attempt in range(40):
        if m() == goal_map and p() == (gx, gy):
            break
        if m() != goal_map:
            path = d.nav.find_route(m(), p(), goal_map, (gx, gy))
        else:
            path = d.nav.find_path(m(), p(), (gx, gy),
                                   avoid=d.npc_cells())
            if not path:
                path = d.nav.find_path(m(), p(), (gx, gy))
        if not path:
            print("no path; waiting out NPCs/scripts", flush=True)
            d.flush_dialog()
            d.close_menus()
            d.emu.tick(120)
            continue
        for mv in path:
            if d.battle():
                d.fight()
                d.settle()
            before = p()
            d._step(mv)
            d.settle()
            if d.battle():
                d.fight()
                d.settle()
            if p() != before and p() != (before[0] + {"L": -1, "R": 1}.get(mv, 0),
                                         before[1] + {"U": -1, "D": 1}.get(mv, 0)):
                print("  drift", mv, before, "->", p(), flush=True)
                break
        else:
            continue_drift = False
    if m() == goal_map and p() == (gx, gy):
        print("arrived", p(), flush=True)
        if enter:
            d._step(enter)
            d.settle()
            print("entered:", p(), m(), flush=True)
        d.save()
        print("saved")
    else:
        print("FAILED at", p(), m(), flush=True)


if __name__ == "__main__":
    main()
