"""Resumable grind: pace near the Victory Road entrance until the lead hits
the target level. Saves after every won battle; safe to re-run.

usage: python scripts/grind_vr.py [target_level] [battle_budget]
"""
import sys
sys.path.insert(0, ".")
from trek import Driver

STATE = "saves/omp_speed_run.state"


def lead_level(d):
    m = d.lead()
    return (m["level"], m["hp"], m["max_hp"])


def to_victory_road(d):
    if "VICTORY_ROAD" in d.map_name():
        return
    # PC 1F -> south warp carpets -> ROUTE_23 -> south warp -> VR (13,5)
    d.goto(5, 12)
    d._step("D")
    d.settle()
    print("on", d.map_name(), d.pos())
    if "ROUTE_23" in d.map_name():
        d.goto(9, 12)
        d._step("D")
        d.settle()
        print("on", d.map_name(), d.pos())


def pace_step(d, mv):
    """One pacing step; fights any wild it triggers."""
    r = d.step_dir(mv)
    if r == "battle":
        d.fight()
        return True
    return False


def main():
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    budget = int(sys.argv[2]) if len(sys.argv) > 2 else 18

    d = Driver(STATE)
    lvl, hp, mhp = lead_level(d)
    print(f"start {d.map_name()} {d.pos()} L{lvl} {hp}/{mhp}")

    if "VICTORY_ROAD" not in d.map_name() and lvl < target:
        to_victory_road(d)

    fights = 0
    axis = ["D", "U"]          # vertical pacing corridor
    stuck = 0
    while lvl < target and fights < budget:
        mv = axis[0]
        before = d.pos()
        got_wild = pace_step(d, mv)
        after = d.pos()
        if got_wild:
            fights += 1
            lvl, hp, mhp = lead_level(d)
            print(f"[fight {fights}] now L{lvl} {hp}/{mhp} at {d.map_name()} {after}")
            d.save()
            continue
        if after == before:
            stuck += 1
            axis.reverse()
            if stuck > 6:
                print("corridor wedged; stopping for re-plan at", d.pos())
                break
        else:
            stuck = 0
        # heal lead if badly hurt (max potion), else keep going
        lvl, hp, mhp = lead_level(d)
        if hp * 3 < mhp:
            ok = d.use_item("MAX POTION", target_slot=0)
            print("[heal]", ok)
            if not ok:
                print("heal failed; retreating")
                break

    lvl, hp, mhp = lead_level(d)
    print(f"end {d.map_name()} {d.pos()} L{lvl} {hp}/{mhp} fights={fights}")


if __name__ == "__main__":
    main()
