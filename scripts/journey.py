"""Chain travel() across named maps, verifying and checkpointing each leg.
usage: python scripts/journey.py MAP1 MAP2 ...
MAPs are resolved like trek's _resolve_map (CONST or CamelCase).
"""
import sys
sys.path.insert(0, ".")
from trek import Driver

STATE = "saves/omp_speed_run.state"


def main():
    targets = sys.argv[1:]
    d = Driver(STATE)
    print("start", d.map_name(), d.pos(), flush=True)
    for t in targets:
        dest = d._resolve_map(t)
        cur = d.map_name()
        if cur == dest:
            print(f"[skip] already on {dest}", flush=True)
            continue
        ok = False
        for attempt in range(3):
            try:
                d.travel(dest)
                d.settle()
                if d.map_name() == dest:
                    ok = True
                    break
                print(f"  [leg {t}] landed on {d.map_name()}, retrying",
                      flush=True)
            except Exception as e:
                print(f"  [leg {t}] attempt {attempt}: {e!r}", flush=True)
        if not ok:
            raise RuntimeError(f"could not reach {dest}, "
                               f"stuck at {d.map_name()} {d.pos()}")
        m = d.lead()
        print(f"[leg] -> {dest} {d.pos()} lead L{m['level']} "
              f"{m['hp']}/{m['max_hp']}", flush=True)
        d.save()
    print("journey complete:", d.map_name(), d.pos(), flush=True)


if __name__ == "__main__":
    main()
