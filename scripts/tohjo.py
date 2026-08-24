"""Tohjo Falls complete traverse (one process):
entry pool -> waterfall climb at x8-11 band -> upper pool east ->
east channel down -> exit door (25,15) -> Route 27 east half.
"""
import sys
import time
sys.path.insert(0, ".")
from trek import Driver

STATE = "saves/omp_speed_run.state"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def stext(d):
    return "".join(d.emu.screen_text()).upper()


def prompt_yes(d):
    txt = stext(d)
    if "WATERFALL" in txt or "CALM" in txt:
        d.press("A:12 .:150")
        d.press(".:250")
        return True
    return False


def rgoto(d, x, y, tries=8, cap=50):
    for t in range(tries):
        try:
            d.goto(x, y)
            d.settle()
            if tuple(d.pos()[2:]) == (x, y):
                return True
        except Exception:
            pass
        d.close_menus()
        d.press(".:80")
        # fallback manual steering
        c = cap
        while c > 0:
            c -= 1
            p = tuple(d.pos()[2:])
            if p == (x, y):
                return True
            mv = ("R" if p[0] < x else "L") if p[0] != x else \
                 ("D" if p[1] < y else "U")
            r = d.step_dir(mv)
            if r == "battle":
                d.fight()
                continue
            if r == "blocked":
                break
    return tuple(d.pos()[2:]) == (x, y)


def main():
    d = Driver(STATE)
    log("start", d.map_name(), d.pos())
    assert "TOHJO" in d.map_name()
    d.enable_surf()

    # 1. swim to falls base
    if not rgoto(d, 9, 12):
        raise RuntimeError(f"cannot reach falls base, at {d.pos()}")

    # 2. face up + trigger waterfall
    climbed = False
    for i in range(10):
        if d.battle():
            d.fight()
            continue
        for _ in range(4):
            d.press("U:40 .:100")
            if d.emu.read_u8("wPlayerDirection") == 0x4:
                break
        d.press("A:20 .:200")
        txt = stext(d)
        p = d.pos()[2:]
        log(i, "pos", p, "prompt", "WATERFALL" in txt)
        if "WATERFALL" in txt:
            d.press("A:14 .:250")
            d.press(".:400")
            climbed = True
            continue
        if p[1] <= 7:
            climbed = True
            break
    if not climbed:
        raise RuntimeError("no waterfall prompt")

    # 3. cross upper pool east
    for wp in [(11, 5), (18, 5), (20, 6)]:
        if not rgoto(d, *wp, tries=3, cap=30):
            log("wp missed", wp, d.pos())

    # 4. descend east channel
    for wp in [(20, 9), (19, 12), (22, 13), (24, 14)]:
        if not rgoto(d, *wp, tries=3, cap=30):
            log("wp missed", wp, d.pos())

    # 5. exit door
    d.goto(25, 14)
    d._step("D")
    d.settle()
    log("exit ->", d.map_name(), d.pos())
    if "ROUTE_27" in d.map_name():
        d.save()
        log("[saved on Route 27 east half]")


if __name__ == "__main__":
    main()
