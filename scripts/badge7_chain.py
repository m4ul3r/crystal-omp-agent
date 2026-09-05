#!/usr/bin/env python
"""Drive the three story beats that stand between 6 badges and DIVE.

The run has been at 6/8 badges for eight sessions while I polished collection
machinery, and that was the wrong target: essentially every species still
missing sits behind DIVE (badge 7), WATERFALL (badge 8) or the SUPER ROD
(Mossdeep). The story IS the dex bottleneck.

The loop cannot enter this chain on its own -- its log says
"could not reach MtPyre_Summit: no walkable route" -- because every beat is
inside a multi-floor interior and `travel` plans over the map graph, not over
warp chains. So the chains are read off the maps and walked as single hops,
exactly as `revisit_lilycove.py` does.

Beats, with the var or flag each one actually sets (DEX_PLAN_GATES.md):

1. `MtPyre_Summit` (23,7)          VAR_MT_PYRE_STATE -> 1, and with it
                                    VAR_SLATEPORT_HARBOR_STATE -> 1
2. `SlateportCity_Harbor` (8,12)    VAR_SLATEPORT_HARBOR_STATE -> 2, clearing
                                    both FLAG_HIDE_GRUNT_*_BLOCKING_HIDEOUT
3. `AquaHideout_B2F` talk (23,19)   FLAG_EVIL_TEAM_ESCAPED_IN_SUBMARINE

Each beat VERIFIES its own var/flag rather than trusting that walking onto a
cell was enough, and the script stops at the first beat that will not land so
the failure is legible instead of cascading.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from collect import Collector  # noqa: E402
from pokeagent.trek import Driver  # noqa: E402

log = logging.getLogger("badge7")

#: `(map we should be standing on, warp cell that leaves it)` per hop. Read from
#: pret/data/maps/*/map.json warp_events -- the summit hangs off the EXTERIOR,
#: not off the interior floors, which is the part a graph route gets wrong.
# Each entry is (map we must be standing on, the warp cell ON THAT MAP that
# leaves it). Getting this backwards is what broke the first attempt: (17,18)
# is MtPyre_1F's door OUT to Route 122, not Route 122's door IN, so the chain
# asked to reach a cell on a map it had not entered yet. Route 122's own warp
# is (22,29) -- and tellingly, the phantom gate search had been flailing at
# (21,29), one tile from the door it could not be told to use.
PYRE_HOPS = [
    ("Route122", (22, 29)),
    ("MtPyre_1F", (3, 6)),
    ("MtPyre_Exterior", (19, 10)),
    ("MtPyre_Summit", None),
]
HIDEOUT_HOPS = [
    ("LilycoveCity", (70, 5)),
    ("AquaHideout_1F", (22, 1)),
]
#: B1F has THREE separate stairs down to B2F plus twenty-five self-warps -- the
#: floor is a teleport maze. Trying only (18,1) failed with "(18,1) did not
#: fire", so all three are tried before falling back to the maze solver.
B2F_STAIRS = [(18, 1), (12, 1), (3, 3)]


def var(d, name):
    try:
        return d.state.var(name)
    except Exception:  # noqa: BLE001
        return None


def travel_to(d, collector, dest, tries=3, budget=420.0) -> bool:
    for _ in range(tries):
        if d.map_name() == dest:
            return True
        if collector.goto_map(dest, budget=budget):
            return True
        log.info("  travel to %s ended at %s (%s)", dest, d.map_name(),
                 d.last_goto_reason)
    return d.map_name() == dest


def hop_chain(d, collector, hops) -> bool:
    """Walk a warp chain, one map at a time, verifying arrival at each."""
    for expect, cell in hops:
        if d.map_name() != expect:
            if not travel_to(d, collector, expect):
                log.info("  could not reach %s (at %s)", expect, d.map_name())
                return False
        if cell is None:
            continue
        before = d.map_name()
        for _ in range(3):
            if d.take_warp(*cell):
                break
            log.info("  warp %s on %s: %s", cell, before, d.last_warp_reason)
        if d.map_name() == before:
            log.info("  stuck on %s; %s did not fire", before, cell)
            return False
        log.info("  %s -> %s %s", before, d.map_name(), d.pos())
    return True


def beat_mt_pyre(d, collector) -> bool:
    if (var(d, "VAR_MT_PYRE_STATE") or 0) >= 1:
        log.info("BEAT 1 already done (VAR_MT_PYRE_STATE=%s)",
                 var(d, "VAR_MT_PYRE_STATE"))
        return True
    log.info("BEAT 1: Mt Pyre summit (23,7)")
    if not hop_chain(d, collector, PYRE_HOPS):
        return False
    # The scene fires on ENTERING one of the summit's coord_event cells; the
    # loop reached the summit before and died walking the last eleven tiles on
    # a journey budget, so this gets its own generous one.
    for attempt in range(4):
        if d.goto(23, 7, on_battle="fight"):
            break
        log.info("  goto (23,7) attempt %d: %s", attempt, d.last_goto_reason)
    d.advance_scene(120000)
    got = var(d, "VAR_MT_PYRE_STATE") or 0
    log.info("BEAT 1 -> VAR_MT_PYRE_STATE=%s at %s %s", got, d.map_name(),
             d.pos())
    return got >= 1


def beat_harbor(d, collector) -> bool:
    """Talk to Captain Stern; HE takes you to the harbour.

    I had this beat wrong and the map told me so. Trying to walk into the
    harbour, both of Slateport's doors refused: (40,7) is on the east boundary
    and a step there crosses the map CONNECTION to Route 134 instead
    ("stepping R from (39,7) left SlateportCity for Route134, not the warp's
    SlateportCity_Harbor"), and (28,12)'s only open approach is (28,13), which
    is occupied by a STATIONARY object -- 900 frames without it moving.

    That object is the beat. `pret/data/maps/SlateportCity/map.json` names it
    `SlateportCity_EventScript_CaptStern`, `MOVEMENT_TYPE_FACE_RIGHT`, un-hidden
    by the Mt Pyre scene. Talking to him plays the Gabby and Ty interview,
    clears FLAG_HIDE_STERN/SUBMARINE_SHADOW/GRUNT_1/GRUNT_2 at the harbour,
    sets VAR_SLATEPORT_STATE = 2 and finishes with

        warp MAP_SLATEPORT_CITY_HARBOR, 255, 11, 14

    (scripts.inc:482-534). The game walks you in; the door was never the way.
    Once inside, the harbour's own coord_events at (8,11)..(8,14) fire on
    VAR_SLATEPORT_HARBOR_STATE == 1, which the Mt Pyre beat already set.
    """
    if (var(d, "VAR_SLATEPORT_HARBOR_STATE") or 0) >= 2:
        log.info("BEAT 2 already done")
        return True
    log.info("BEAT 2: Captain Stern (28,13), then the harbour deck")

    if d.map_name() != "SlateportCity_Harbor":
        if d.map_name() != "SlateportCity" and not d.fly_to("SlateportCity"):
            log.info("  could not fly to Slateport (%s)", d.last_fly_reason)
            if not travel_to(d, collector, "SlateportCity"):
                return False
        try:
            d.talk_to(28, 13)
        except Exception as exc:  # noqa: BLE001 - the scene may interrupt
            log.info("  talking to Stern raised: %s", str(exc)[:80])
        # The interview is long: two NPCs walk off screen and the warp lands
        # last, so this gets a generous settle rather than one advance_scene.
        for _ in range(6):
            d.advance_scene(120000)
            if d.map_name() == "SlateportCity_Harbor":
                break
        log.info("  after Stern: %s %s | SLATEPORT_STATE=%s", d.map_name(),
                 d.pos(), var(d, "VAR_SLATEPORT_STATE"))

    if d.map_name() != "SlateportCity_Harbor":
        log.info("  Stern did not hand over the harbour (at %s)", d.map_name())
        return False

    for cell in ((8, 12), (8, 11), (8, 13), (8, 14)):
        if (var(d, "VAR_SLATEPORT_HARBOR_STATE") or 0) >= 2:
            break
        if d.goto(*cell, on_battle="fight"):
            d.advance_scene(120000)
    got = var(d, "VAR_SLATEPORT_HARBOR_STATE") or 0
    log.info("BEAT 2 -> VAR_SLATEPORT_HARBOR_STATE=%s at %s", got, d.map_name())
    return got >= 2


def beat_hideout(d, collector) -> bool:
    flag = "FLAG_EVIL_TEAM_ESCAPED_IN_SUBMARINE"
    if d.state.flag(flag):
        log.info("BEAT 3 already done")
        return True
    log.info("BEAT 3: Aqua Hideout B2F, talk (23,19)")
    if not hop_chain(d, collector, HIDEOUT_HOPS):
        return False
    if d.map_name() == "AquaHideout_1F":
        for cell in B2F_STAIRS:
            if d.take_warp(*cell):
                break
            log.info("  1F stair %s: %s", cell, d.last_warp_reason)
    for cell in B2F_STAIRS:
        if d.map_name() == "AquaHideout_B2F":
            break
        if d.map_name() != "AquaHideout_B1F":
            break
        if d.take_warp(*cell):
            break
        log.info("  B1F stair %s: %s", cell, d.last_warp_reason)
    # Still upstairs? The floor is a same-map warp maze, which is exactly what
    # `solve_warp_maze` exists for; `reach_cell` escalates into it.
    if d.map_name() != "AquaHideout_B2F":
        if not d.reach_cell(18, 2, map_name="AquaHideout_B2F",
                            on_battle="fight"):
            log.info("  could not get onto B2F from %s (%s)", d.map_name(),
                     d.last_goto_reason)
            return False
    # (23,19) IS WALKABLE BUT NOT REACHABLE. B2F is split into components
    # joined only by its own self-warps -- from the stairs at (18,2) just 180
    # cells are reachable and the grunt is not among them -- so a plain
    # `talk_to` plans a route that does not exist and fails silently. Reaching
    # him is `reach_cell`'s job, because that escalates into the warp-maze
    # search that already carried this run down here.
    if d.pos() != (24, 19):
        got = d.reach_cell(24, 19, map_name="AquaHideout_B2F",
                           on_battle="fight")
        log.info("  reach (24,19) -> %s at %s (%s)", got, d.pos(),
                 d.last_goto_reason)
    # THE FLAG IS SET BY THE AFTER-BATTLE SCRIPT, so the grunt must be BEATEN.
    # `AquaHideout_B2F_EventScript_15D8E1` is a
    # `trainerbattle_single TRAINER_HIDEOUT_B2F_GRUNT_1` whose defeat branch
    # (`_15D8FD`) plays the submarine departure and only then runs
    # `setflag FLAG_EVIL_TEAM_ESCAPED_IN_SUBMARINE` (scripts.inc:27-67).
    # Talking alone returned True and changed nothing.
    flag = "FLAG_EVIL_TEAM_ESCAPED_IN_SUBMARINE"
    for attempt in range(3):
        if d.state.flag(flag):
            break
        try:
            log.info("  talk (23,19) -> %s", d.talk_to(23, 19))
        except Exception as exc:  # noqa: BLE001 - the battle interrupts
            log.info("  talk raised: %s", str(exc)[:80])
        if d.in_battle():
            r = d.fight()
            log.info("  grunt battle: %s", (r or {}).get("outcome"))
        # The departure cutscene is long: two sprites walk off and the flag
        # lands after the last message.
        for _ in range(4):
            d.advance_scene(120000)
            if d.state.flag(flag):
                break
        log.info("  attempt %d -> %s=%s", attempt, flag, d.state.flag(flag))
    ok = bool(d.state.flag(flag))
    log.info("BEAT 3 -> %s=%s at %s", flag, ok, d.map_name())
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    ap.add_argument("--beat", type=int, default=0,
                    help="run only this beat (1-3); 0 runs the chain")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    d.advance_scene(40000)
    c = Collector(d, feed_name=None)
    log.info("start %s %s badges %d | PYRE=%s HARBOR=%s",
             d.map_name(), d.pos(), len(d.state.badges()),
             var(d, "VAR_MT_PYRE_STATE"),
             var(d, "VAR_SLATEPORT_HARBOR_STATE"))

    beats = [beat_mt_pyre, beat_harbor, beat_hideout]
    if a.beat:
        beats = [beats[a.beat - 1]]
    for fn in beats:
        # HEAL BEFORE EACH BEAT. The hideout beat ends in a trainer battle with
        # a line-of-sight grunt, and it was reached with the lead at 0/112 --
        # `SEA BIRD L36 0/112` in the log, fainted somewhere on the way through
        # the warp maze. A story beat that ends in a fight cannot be driven by
        # a party that cannot fight, and a whiteout would move the player and
        # undo the approach.
        if c.hurt():
            log.info("healing first (%s) from %s",
                     "no damaging PP" if c.pp_dry() else "hurt party",
                     d.map_name())
            # GET OUT FIRST. `heal_at_nearest_center` routes over the map
            # graph, and from inside the Aqua Hideout's B2F there is no Centre
            # to route to -- the previous attempt started there with the lead
            # at 0/112, failed to heal, and walked into a trainer anyway.
            # Flying leaves the dungeon (fly_to steps outdoors on its own) and
            # lands somewhere a nurse exists.
            if not c.heal():
                if d.fly_to("LilycoveCity"):
                    c.heal()
                else:
                    log.info("  could not fly out to heal (%s)",
                             d.last_fly_reason)
            lead = d.state.party()[0] if d.state.party() else None
            if lead:
                log.info("  lead now %s %s/%s", lead.nickname, lead.hp,
                         lead.max_hp)
        if not fn(d, c):
            log.info("STOPPED at %s -- leaving the rest for a later pass",
                     fn.__name__)
            if a.out:
                d.save(a.out)
            return 1
        if a.out:
            d.save(a.out)
    log.info("CHAIN DONE at %s badges %d", d.map_name(), len(d.state.badges()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
