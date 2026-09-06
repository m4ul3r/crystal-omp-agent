#!/usr/bin/env python
"""Throwaway: is a cracked tile safe on the SECOND tile of a hold, not the third?

`MachBikeTransition_TrySpeedUp` (pret/src/bike.c:250-254) does:

    sMachBikeSpeedCallbacks[bikeFrameCounter](direction);   // starts the move
    bikeSpeed = counter + (counter >> 1);
    if (bikeFrameCounter < 2) bikeFrameCounter++;           // THEN increments

and `PerStepCallback_806A07C` (field_tasks.c:674, :692-696) reads
`PlayerGetDestCoords` + `GetPlayerSpeed()` AFTER that, where
`GetPlayerSpeed()` = sMachBikeSpeeds[bikeFrameCounter] and
sMachBikeSpeeds = {SPEED_NORMAL, SPEED_FAST, SPEED_FASTEST} = {1,2,4}
(bike.c:121 with SPEED_STANDING=0 leading the enum, include/bike.h:20-24).

So the counter seen by the fall check is one HIGHER than the one that moved
the player:

    tile 1 of a hold: moved with counter 0, check sees 1 -> speed 2 -> FALL
    tile 2 of a hold: moved with counter 1, check sees 2 -> speed 4 -> SAFE
    tile 3+:          counter pinned at 2  -> speed 4 -> SAFE

If true, ONE solid run-up tile is enough and my "third tile" rule was
off by one, which is what made every crossing read as NO ROUTE.

Test on 2F column 11, cracks at (11,5) (11,6):
    from (11,4): crack is tile 1 -> expect FALL to SkyPillar_1F
    from (11,3): crack is tile 2 -> expect SURVIVE, ride on to (11,13)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver, _HOLD  # noqa: E402

STATE = sys.argv[1] if len(sys.argv) > 1 else "saves/rq.state"


def trial(start, frames, label):
    d = Driver(STATE, live=False)
    if d.at_title():
        d.resume_from_title()
    if d.map_name() != "SkyPillar_2F":
        print(f"{label}: save is on {d.map_name()}, need SkyPillar_2F")
        return
    if not d.on_bike():
        d.mount_bike()
    if d.pos() != start:
        d.goto(*start, label="runup", on_battle="fight")
        d.settle(90)
    if d.pos() != start:
        print(f"{label}: could not reach {start}, at {d.pos()}")
        return
    print(f"{label}: at {d.pos()} speed {d.bike_speed()}, holding DOWN:{frames}")
    d.emu.run_sequence(f"{_HOLD['D']}:{frames}")
    d.settle(120)
    print(f"{label}: -> {d.map_name()} {d.pos()} speed {d.bike_speed()}")
    fell = d.map_name() != "SkyPillar_2F"
    print(f"{label}: VERDICT {'FELL' if fell else 'SURVIVED'}")
    return not fell


# crack as tile 1 -> should fall
trial((11, 4), 40, "A crack-is-tile-1 from (11,4)")
# crack as tile 2 -> should survive if the off-by-one is real
trial((11, 3), 60, "B crack-is-tile-2 from (11,3)")
