#!/usr/bin/env python3
"""Walk east/south on Route 29 toward the Route 30 west connection."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from trek import Driver

B = ("up", "down", "left", "right", "a", "b", "start", "select")


def rel(e):
    for b in B:
        e.py.button_release(b)
    e.tick(3)


def hold(d, mv, f=22):
    e = d.emu
    e.py.button_press(mv)
    e.tick(f)
    e.py.button_release(mv)
    e.tick(12)


def main():
    target_x = int(sys.argv[1]) if len(sys.argv) > 1 else 33
    d = Driver()
    print("start:", d.map_name(), d.pos()[2:], flush=True)
    stuck = 0
    while d.map_name() == "ROUTE_29" and d.pos()[2] < target_x:
        if d.battle():
            d.fight()
            rel(d.emu)
            continue
        b0 = d.pos()[2:]
        moved = False
        for mv in ("right", "up", "down"):
            hold(d, mv)
            rel(d.emu)
            if d.pos()[2:] != b0:
                moved = True
                break
        if not moved:
            stuck += 1
            print("stuck at", d.pos()[2:], flush=True)
            if stuck > 3:
                break
        else:
            stuck = 0
            if d.pos()[2] % 5 == 0:
                print("progress", d.pos()[2:], flush=True)
    print("now:", d.map_name(), d.pos()[2:])
    d.save()


if __name__ == "__main__":
    main()
