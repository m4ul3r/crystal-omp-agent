#!/usr/bin/env python3
"""Journey driver: reusable primitives for long play sessions, run as legs
in a single persistent process (no per-command emulator reload).

Usage: .venv/bin/python trek.py <leg> [args]   (see main() dispatch)
"""

import json
import logging
import sys
from pathlib import Path

from crystalagent import paths
from crystalagent.state import game_state


from crystalagent.driver import DecisionRequired, Driver, HealError, TravelError
from crystalagent.driver.inventory import heal_pokecenter
from crystalagent.nav import (
    TrekNav, coord_events, mapgraph, render_map_view, scene_consts, scene_vars,
    script_advances_scene, script_guards, script_is_disruptive,
)

log = logging.getLogger("trek")

def leg_to_violet(d):
    """Cherrygrove Pokecenter -> Route 30 -> Route 31 -> Violet City."""
    d.goto(3, 7, "pokecenter door")
    d.walk("D", "exit pokecenter")
    d.goto(16, 0, "city north exit")
    d.walk("U", "cross to Route 30")
    d.goto(5, 0, "route 30 north end")     # BFS threads the ledges/trainers
    d.walk("U", "cross to Route 31")
    d.goto(4, 6, "route 31 gate")
    log.info(f"  now in {d.map_name()} {d.pos()[2:]}")


def leg_errand1(d):
    """Route 30 -> Mr. Pokemon's house: receive the Mystery Egg + Pokedex."""
    d.goto(17, 5, "Mr. Pokemon's door")
    d.flush_dialog(2000)
    d.goto(3, 6, "approach Mr. Pokemon")  # he stands at (3,5)
    d.step_dir("U")
    d.press("A:2 .:20")
    d.flush_dialog(30000)                # egg + Oak + Pokedex: very long
    log.info(f"  done: {d.map_name()} {d.pos()[2:]}")


def leg_errand2(d):
    """Back south to Cherrygrove; rival fight triggers heading east."""
    d.goto(2, 7, "house exit")
    d.walk("D", "leave house")
    d.goto(6, 53, "route 30 south end")
    d.walk("D", "into Cherrygrove")
    d.save("pre-rival.state")
    d.goto(39, 6, "east exit (rival ambush en route)")
    d.walk("R*2", "cross to Route 29")


def leg_errand3(d):
    """Route 29 east, into New Bark, deliver the egg at Elm's lab."""
    d.goto(59, 8, "route 29 east end")
    d.walk("R", "into New Bark")
    d.goto(6, 3, "Elm's lab door")
    d.flush_dialog(8000)                 # officer scene (includes naming)
    d.goto(5, 4, "walk up to Elm")
    d.step_dir("U")
    d.press("A:2 .:20")
    d.flush_dialog(30000)                # egg handover, gate clears here
    d.save("egg-delivered.state")


def leg_errand4(d):
    """Leave the lab (aide gives Poke Balls), trek back west to Route 30."""
    d.goto(4, 11, "lab exit")            # aide scene fires on the way
    d.walk("D", "leave lab")
    d.goto(0, 8, "town west exit")
    d.walk("L", "onto Route 29")
    d.goto(0, 6, "route 29 west end")    # catch tutorial fires at x=53
    d.walk("L*2", "into Cherrygrove")
    d.goto(16, 0, "city north exit")
    d.walk("U", "onto Route 30")


def leg_violet(d):
    """Route 30 north (gate now clear) -> Route 31 -> gate -> Violet City."""
    d.goto(5, 0, "route 30 north end")
    d.walk("U", "cross to Route 31")
    d.goto(4, 6, "route 31 gate door")
    d.flush_dialog(1500)
    if d.map_name() != "ROUTE_31_VIOLET_GATE":
        d.goto(4, 7, "gate door (south half)")
    d.goto(0, 4, "gate west door")
    d.flush_dialog(1500)
    log.info(f"  now in {d.map_name()} {d.pos()[2:]}")


def leg_route29(d):
    # From Route 29 grass (44,10) west to Cherrygrove. Path along y=8-10.
    d.walk("U*2", "back to path")           # out of grass to y=8
    d.walk("L*18", "route 29 west")         # long straight, trees at gaps
    print(d.status())

def env_flag(name):
    import os
    return os.environ.get(name, "").strip().lower() not in ("", "0", "no",
                                                             "false")


def audit_saves():
    """`trek states`: table of saves/*.state -- frame count from the .meta
    sidecar, META MISSING marker when absent, age since last write."""
    import time
    rows = []
    for p in sorted(Path(paths.SAVES_DIR).glob("*.state")):
        if p.name.endswith(".watch.state"):
            continue
        meta = p.with_name(p.name + ".meta")
        frame = None
        if meta.exists():
            try:
                frame = json.loads(meta.read_text()).get("frames")
            except Exception:
                pass
        rows.append((frame, p.name, time.time() - p.stat().st_mtime))
    print(f"{'frames':>9}  {'state':42} {'meta':13} age")
    for frame, name, age in sorted(rows,
                                   key=lambda r: (r[0] is None, r[0] or 0)):
        print(f"{frame if frame is not None else '-':>9}  {name:42} "
              f"{'ok' if frame is not None else 'META MISSING':13} "
              f"{age / 3600:.1f}h")



def gc_saves(apply=False, keep=3):
    """`trek gc`: checkpoint lifecycle. Dry-run by default; lists
    disposable saves -- 1-byte stubs and stale numbered series
    (<session>-<kind>-<n>.state, keeping the newest `keep` per series).
    Never touches: anything named in PROGRESS.md (milestones),
    default.state, the watch viewer's state."""
    progress = Path("PROGRESS.md")
    protected = {w for w in (progress.read_text().split()
                             if progress.exists() else [])
                 if w.endswith(".state")}
    protected.add(paths.DEFAULT_STATE.name)
    protected.add("watch.state")

    stubs, series = [], {}
    for p in sorted(Path(paths.SAVES_DIR).glob("*.state")):
        if p.name in protected or p.name.endswith(".watch.state"):
            continue
        if p.stat().st_size <= 1:
            stubs.append(p)
            continue
        parts = p.stem.rsplit("-", 1)
        if len(parts) == 2 and parts[1].isdigit():
            series.setdefault(parts[0], []).append(p)

    victims = list(stubs)
    for base, ps in sorted(series.items()):
        ps.sort(key=lambda p: int(p.stem.rsplit("-", 1)[1]))
        victims += ps[:-keep] if len(ps) > keep else []

    if not victims:
        print("gc: nothing to clean")
        return
    print(f"gc: {len(victims)} file(s)"
          f"{' (dry run; pass --apply to delete)' if not apply else ''}")
    total = 0
    for p in victims:
        size = p.stat().st_size
        meta = Path(f"{p}.meta")
        extra = meta.stat().st_size if meta.exists() else 0
        print(f"  {'DELETE' if apply else 'would delete'} {p.name} "
              f"({size + extra} bytes)")
        total += size + extra
        if apply:
            p.unlink()
            if meta.exists():
                meta.unlink()
    print(f"gc: {'freed' if apply else 'reclaimable'} {total} bytes")



def main():
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(message)s")
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        sys.exit(
            "usage: trek.py <command> [<state>] [args...]\n"
            "commands:\n"
            "  walk PATH | goto X Y [MAP] | talk X Y\n"
            "  route MAP | travel MAP | mart X Y ITEM QTY\n"
            "  verify FLAG... | states | train LEVEL | gc [--apply] [--keep N]\n"
            "  map [MAP_NAME] | catch [NICKNAME] | fight | flush | heal\n"
            "  route29 | to_violet | errand1 | errand2 | errand3 | errand4 | violet\n"
            "goto MAP accepts CONST_NAME or CamelCase and routes across maps.\n"
            "<state>: pass your own savestate; omission is refused unless\n"
            "CRYSTAL_ALLOW_DEFAULT=1 deliberately enables the shared default"
        )
    leg, rest = argv[0], list(argv[1:])
    spec = {
        "walk": (1, 1), "goto": (2, 3), "talk": (2, 2),
        "route": (1, 1), "travel": (1, 1),
        "mart": (4, 4),
        "verify": (1, 10), "states": (0, 0), "train": (1, 1),
        "gc": (0, 2), "map": (0, 1),
        "catch": (0, 1), "fight": (0, 0), "flush": (0, 0),
        "heal": (0, 0), "route29": (0, 0),
        "to_violet": (0, 0), "errand1": (0, 0), "errand2": (0, 0),
        "errand3": (0, 0), "errand4": (0, 0), "violet": (0, 0),
    }
    arity = spec.get(leg)
    if arity is None:
        sys.exit(f"unknown leg {leg!r}; legs: {', '.join(sorted(spec))}")
    lo, hi = arity
    if leg == "states":
        audit_saves()
        return
    if leg == "gc":
        gc_saves(apply="--apply" in rest,
                 keep=int(rest[rest.index("--keep") + 1])
                 if "--keep" in rest else 3)
        return
    # state path comes right after the leg: '' = default, or a *.state file;
    # anything else is the leg's first real argument
    state_arg = None
    if rest and (rest[0] == "" or rest[0].endswith(".state")):
        state_arg = rest.pop(0) or None
    if not lo <= len(rest) <= hi:
        usage = {"walk": "PATH", "goto": "X Y [MAP]", "talk": "X Y",
                 "mart": "X Y ITEM QTY", "catch": "[NICKNAME]",
                 "route": "MAP", "travel": "MAP", "map": "[MAP_NAME]",
                 "train": "TARGET_LEVEL"}.get(leg, "")
        sys.exit(f"usage: trek.py {leg} [<state>] {usage}".rstrip())
    if state_arg is None and not env_flag("CRYSTAL_ALLOW_DEFAULT"):
        sys.exit(f"refusing to run on shared {paths.DEFAULT_STATE} "
              "implicitly. Pass your own fork: trek <leg> "
              "saves/<agent>.state ... (or '' + CRYSTAL_ALLOW_DEFAULT=1 "
              "to use default.state deliberately)")
    try:
        d = Driver(state_arg)
    except FileNotFoundError as e:
        sys.exit(f"no such state file: {e.filename}")
    print(f"[start] {d.status()}", flush=True)
    if leg == "map":
        print(d.map_view(rest[0] if rest else None))
    elif leg == "walk":
        d.walk(rest[0])
    elif leg == "goto":
        d.goto(int(rest[0]), int(rest[1]),
               map_name=rest[2] if len(rest) > 2 else None)
    elif leg == "talk":
        print(d.talk_to(int(rest[0]), int(rest[1])), flush=True)
    elif leg == "route":
        print(json.dumps(d.route(rest[0]), indent=1), flush=True)
    elif leg == "travel":
        d.travel(rest[0])
    elif leg == "mart":
        d.mart_buy(int(rest[0]), int(rest[1]), rest[2], int(rest[3]))
    elif leg == "verify":
        ok = True
        s = game_state(d.emu, d.names)
        badge_names = {b.upper() for b in s["player"]["johto_badges"]
                       + s["player"]["kanto_badges"]}
        for name in rest:
            bare = name.upper()
            if bare.endswith("_BADGE"):
                bare = bare[:-len("_BADGE")]
            if bare in badge_names:
                print(f"{name}: SET (badge)")
                continue
            try:
                set_ = d._event_flag(name)
            except ValueError as e:
                print(f"{name}: UNKNOWN ({e})")
                ok = False
                continue
            print(f"{name}: {'SET' if set_ else 'clear'}")
            ok = ok and set_
        if not ok:
            sys.exit(1)   # any requested flag missing/unknown -> nonzero
    elif leg == "catch":
        d.catch(nickname=rest[0] if rest else None)
    elif leg == "fight":
        d.fight()
    elif leg == "train":
        d.train(int(rest[0]))
    elif leg == "flush":
        print(f"flush_dialog -> {d.flush_dialog()}", flush=True)
    elif leg == "route29":
        leg_route29(d)
    elif leg == "heal":
        d.heal()
    elif leg == "to_violet":
        leg_to_violet(d)
    elif leg == "errand1":
        leg_errand1(d)
    elif leg == "errand2":
        leg_errand2(d)
    elif leg == "errand3":
        leg_errand3(d)
    elif leg == "errand4":
        leg_errand4(d)
    elif leg == "violet":
        leg_violet(d)
    if leg not in ("route", "verify", "map", "states", "gc"):
        # both are pure reads: don't rewrite the state
        d.save()
    print(f"[end] {d.status()}", flush=True)


if __name__ == "__main__":
    main()
