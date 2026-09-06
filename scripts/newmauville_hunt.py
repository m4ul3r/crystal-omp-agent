#!/usr/bin/env python
"""New Mauville: MAGNEMITE, VOLTORB, and the THUNDER STONE behind the door.

Four dex holes sit behind one door -- MAGNEMITE, MAGNETON, VOLTORB, ELECTRODE
-- and only two of them are worth hunting. MAGNETON and ELECTRODE are 1% slots
on `NewMauville_Inside` and both are `EVO_LEVEL 30` from the basics
(`pret/src/data/pokemon/evolution.h:57,:67`), so catching MAGNEMITE and VOLTORB
closes all four. Both are 50% slots on `NewMauville_Entrance`, a 5x5 room of
encounter tiles that needs no door opened at all
(`pret/src/data/wild_encounters.json`, MAP_NEW_MAUVILLE_ENTRANCE: six VOLTORB
and six MAGNEMITE slots, L22-26).

ALL OF THE DIFFICULTY IS GETTING IN, and the reason previous routing gave up is
a real gap in the nav model rather than a missing road.

The geography, measured off the shipped `.blk` data:

* The only warp into New Mauville is `Route110 (35,24)`
  (`pret/data/maps/Route110/map.json:435-441`). Its only walkable neighbour is
  `(35,25)`, on a four-cell elevation-3 shelf at x33-35, y25-28.
* The shelf touches water only at `(32,25)..(32,28)`, which belong to a
  160-cell water body spanning x14-37, y20-37 -- the "door pond". That body
  contains NO map-edge cell, so no surfer can enter it across a map seam.
* Route110's water splits into a 200-cell WEST pond and that 160-cell EAST
  pond, separated by the Seaside Cycling Road running down x26-28. The two
  are separate *water* components; the road cells between them are floor.
* The engine only lets you MOUNT Surf from elevation 3:
  `IsPlayerFacingSurfableFishableWater` needs collision ==
  COLLISION_ELEVATION_MISMATCH **and** `PlayerGetZCoord() == 3`
  (`pret/src/field_player_avatar.c:1121-1134`), and dismounting needs the
  target tile at elevation 3 exactly (`CanStopSurfing`, same file). Water here
  is elevation 1, the cycling road is elevation 4, and its water crossings are
  elevation 15.
* The walk-reachable elevation-3 shore on Route110 is `(7,19)`, `(10,19)` and
  `(12..14,19)` -- the north bank of the WEST pond, reached by entering
  Route110 from Mauville City's x12-18 seam. There is NO elevation-3 shore on
  the east pond except the door shelf itself.

So the route has to cross the Cycling Road *while surfing*, and it does,
because the engine says so:

    else if (MetatileBehavior_IsWaterWildEncounter(curMetatileBehavior) == TRUE
     || (TestPlayerAvatarFlags(PLAYER_AVATAR_FLAG_SURFING)
         && MetatileBehavior_IsBridge(curMetatileBehavior) == TRUE))
                                            -- pret/src/wild_encounter.c:480-481

"surfing on a bridge tile" is a state the ROM rolls water encounters for. The
road cells at `(26..28, 25)` are MB_WARP_OR_BRIDGE (0x70, elevation 15,
collision 0); elevation 15 never mismatches (`IsElevationMismatchAt`), and
`CanStopSurfing` refuses to dismount onto them because they are not elevation
3. A surfer therefore swims straight under the road and keeps the blob.

`nav.step` cannot express that. Its surf seam
(`pokeagent/nav.py:505-529,624-625`) asks whether the tile it is STANDING on
is water, and a bridge cell is not, so leaving one back onto water is treated
as a fresh MOUNT and refused at z=1. That is why `find_path` answers "no path"
from every Route110 land cell to the shelf, and why `reachable` splits the map.
The crossing is therefore hand-walked with `step_dir`, which asks the engine
instead of the model. Verified on the emulator: (25,25) -> (26,25) -> (28,25)
-> (30,25) -> (32,25), elevation 1 and `is_surfing()` True the whole way, then
one more step east dismounts onto (33,25) at elevation 3.

The door to `NewMauville_Inside` is a coord_event at `(4,2)` that fires while
`VAR_NEW_MAUVILLE_STATE == 0`: one message, then a YES/NO on the BASEMENT KEY
(`pret/data/maps/NewMauville_Entrance/scripts.inc`). Answering YES rewrites the
door metatiles with `setmetatile`, so `sync_grid()` is mandatory before the
warp at `(4,1)` is walkable -- the static grid still reads a wall.

Inside, the THUNDER STONE is an item ball at `(39,4)`
(`pret/data/item_ball_scripts.inc:325-326`). An item ball is an object_event
and blocks its own cell, so it is picked up from an adjacent tile. The route to
it runs through the barrier puzzle: `VAR_TEMP_1`/`VAR_TEMP_2` are reset to 0 on
every map transition (`NewMauville_Inside_MapScript1_15E593`) and the shipped
`.blk` draws BOTH barrier sets as walls, so at least one button has to be
stepped on before anything opens. Buttons are the coord_events listed in
`BARRIER_BUTTONS`; each writes its barrier set with `setmetatile`, hence a
`sync_grid()` after every press.

The three L25 VOLTORB inside are static object_events that set their hide flag
BEFORE the battle starts (`NewMauville_Inside/scripts.inc:171,186,201`), so
fleeing one destroys it permanently. This script never enters `Inside` to hunt
-- the Entrance table is 50/50 and infinite -- and when it does go in for the
stone it fights those three to a KO rather than fleeing, because they are worth
nothing to a postgame dex that already has VOLTORB.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.menus import Menus  # noqa: E402
from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402

import collect  # noqa: E402
from share_grind import to_center, unwedge  # noqa: E402

log = logging.getLogger("newmauville")

ENTRANCE = "NewMauville_Entrance"
INSIDE = "NewMauville_Inside"
ROUTE = "Route110"

#: National dex numbers, because `DexTarget.achievable` keys on them. MAGNETON
#: (82) and ELECTRODE (101) are deliberately absent: they are EVO_LEVEL 30 off
#: these two and are not worth farming as 1% slots.
MAGNEMITE, VOLTORB = 81, 100
WANT = (MAGNEMITE, VOLTORB)

#: Mauville City's south seam. Route110's north edge is walkable at x12-18
#: and lands in the 797-cell component that owns the west pond's shore; the
#: other seam cell (x28) lands in a 574-cell component with no shore at all.
MAUVILLE_SEAM = (15, 19)
#: Elevation-3 north bank of the west pond. Facing D from here mounts Surf.
WEST_SHORE = (14, 19)
#: The crossing. y=25 is one of five rows where (26..28, y) are elevation-15
#: bridge cells with water on both sides; y=24 is skipped because a cyclist
#: object_event stands at (27,24).
CROSS_ROW = 25
WEST_STAGE = (25, CROSS_ROW)
#: Last water cell before the shelf. (33,25) is the elevation-3 dismount.
EAST_STAGE = (32, CROSS_ROW)
DISMOUNT = (33, CROSS_ROW)
#: The warp cell and the only tile you can fire it from.
DOOR_WARP = (35, 24)
SHELF = (35, DOOR_WARP[1] + 1)

#: The BASEMENT KEY prompt. Stepping U onto (4,2) from here fires it.
DOOR_APPROACH = (4, 3)
#: Warp from the entrance room down into Inside, walled until the door opens.
INSIDE_WARP = (4, 1)

THUNDER_STONE_BALL = (39, 4)

#: `NewMauville_Inside` coord_events, `(cell, which VAR_TEMP it sets to 1)`.
#: Pressing one raises that var's barrier set and hides the other's
#: (`EventScript_15E5AA` / `_15E5C2`).
BARRIER_BUTTONS = (
    ((30, 38), 1),
    ((4, 26), 1),
    ((16, 22), 1),
    ((6, 11), 1),
    ((13, 10), 1),
    ((18, 36), 2),
    ((25, 18), 2),
    ((2, 11), 2),
    ((17, 10), 2),
)

#: Top cell of each four-cell barrier column, by the var that RAISES it.
#: `VAR_TEMP_1 == 1` puts the green set up and hides the blue set;
#: `VAR_TEMP_2 == 1` does the reverse. Both are 0 on entry
#: (`NewMauville_Inside_MapScript1_15E593`) and the shipped `.blk` draws every
#: column shut, so nothing opens until a button is pressed.
BARRIER_GREEN = ((37, 33), (28, 22), (10, 24), (21, 2))
BARRIER_BLUE = ((23, 34), (10, 16), (10, 0))

#: Initial object_event cells: five item balls and the three static VOLTORB
#: that wear the item-ball sprite. Only a fallback -- `_live_objects` reads
#: `gObjectEvents`, because each of these hangs off a save flag.
INSIDE_OBJECTS = frozenset({
    (32, 25), (16, 22), (39, 4), (17, 10), (2, 11),
    (25, 18), (6, 11), (13, 10),
})

#: Cells the stone can be taken from. It sits behind the (21,2) column, so
#: reaching one of these means VAR_TEMP_2 is up.
STONE_APPROACHES = ((38, 4), (39, 5), (39, 3))

#: Encounters we do not want end in ONE turn. The stock training policy fed
#: every declined VOLTORB to a level-27 laggard and spent seven turns and the
#: laggard's HP on each -- forty seconds of emulator per encounter, on a map
#: whose whole value is the NEXT encounter.
def _flee_policy(_frame):
    return "flee"


def _adjacent(cell):
    x, y = cell
    return [(x, y + 1, "U"), (x, y - 1, "D"), (x - 1, y, "R"), (x + 1, y, "L")]


def _neighbours(cell):
    x, y = cell
    return [(x, y - 1), (x + 1, y), (x, y + 1), (x - 1, y)]


class Hunt:
    def __init__(self, driver, collector):
        self.d = driver
        self.c = collector
        # THROW, DO NOT TRAIN. `Collector.base_policy` is the team's training
        # policy, and on a map whose only value is the NEXT encounter it turns
        # every declined VOLTORB into a seven-turn exp exercise -- it switched
        # in a L27 laggard, chipped the target down with BUBBLEBEAM and
        # fainted, forty seconds of emulator per encounter. Installed here
        # rather than in `hunt` so the walks inside NewMauville_Inside, where
        # every tile is an encounter tile, get it too.
        collector.base_policy = lambda: _flee_policy

    # ---- battles ---------------------------------------------------------

    def caught(self) -> set:
        try:
            got, _seen = self.c.target.dex_flags(self.d.state)
            return set(got)
        except Exception:  # noqa: BLE001
            return set()

    def outstanding(self) -> list:
        got = self.caught()
        return [s for s in WANT if s not in got]

    def _battle(self):
        """Hand the encounter in front of us to the catch-aware code."""
        try:
            self.c.fight()
        except Exception as exc:  # noqa: BLE001 - never lose the run to one battle
            log.info("  battle: %s", str(exc)[:90])
        self.d.advance_scene(20000)

    def walk_leg(self, x, y, tries=10, budget=90.0):
        """`goto`, absorbing wild encounters, until we stand on (x, y).

        Bounded by WALL CLOCK as well as attempts. `tries` alone is not a
        bound: each `goto` gets its own `budget`, so a target the walker
        cannot reach costs `tries * budget` -- four attempts at 90s on a
        single unreachable tile is six minutes, and the barrier solver asks
        about four tiles per button. The watchdog dumped a faulthandler
        traceback out of exactly that.
        """
        d = self.d
        stop = time.time() + budget * 1.5
        for i in range(tries):
            if d.pos() == (x, y):
                return True
            if time.time() > stop:
                log.info("  goto (%d,%d) out of time at %s", x, y, d.pos())
                return False
            if d.scene_active():
                unwedge(d)
                d.advance_scene(30000)
            try:
                d._journey_deadline = min(stop, time.time() + budget)
                d.goto(x, y, on_battle="raise")
            except TravelInterrupted:
                self._battle()
                continue
            except Exception as exc:  # noqa: BLE001
                log.info("  goto (%d,%d): %s", x, y, str(exc)[:80])
            if d.pos() == (x, y):
                return True
            log.info("  goto (%d,%d) attempt %d landed on %s (%s)",
                     x, y, i + 1, d.pos(), d.last_goto_reason)
        return d.pos() == (x, y)

    # ---- getting there ---------------------------------------------------

    def to_route110(self) -> bool:
        """Stand on Route110, in the component that owns the west pond."""
        d = self.d
        if d.map_name() == ROUTE:
            return True
        if not d.flight.flyable_here():
            # Fly is refused indoors, and `heal_at_nearest_center` cannot
            # leave the Elite Four plateau on its own; `to_center` walks the
            # one-way chain out until Fly is legal again.
            to_center(d)
        if not d.fly_to("MauvilleCity"):
            log.info("could not fly to Mauville: %s",
                     getattr(d, "last_field_reason", None))
            return False
        if not self.walk_leg(*MAUVILLE_SEAM):
            return False
        # The seam is not a warp: it fires on the step that crosses it.
        for _ in range(4):
            d.step_dir("D")
            if d.map_name() == ROUTE:
                return True
        log.info("stepping off Mauville's south edge left us on %s",
                 d.map_name())
        return False

    def cross_cycling_road(self) -> bool:
        """Swim east under the Seaside Cycling Road, by hand.

        `nav` refuses this: leaving an elevation-15 bridge cell for water at
        z=1 trips its MOUNT guard (`nav.py:624-625`), which only permits a
        land->water step from elevation 3. The engine has no such rule for a
        player who is ALREADY surfing, so the steps are asked of the engine
        directly. Each press can advance two cells, so this is a loop on
        position rather than a fixed count.
        """
        d = self.d
        for _ in range(12):
            here = d.pos()
            if not d.is_surfing():
                log.info("  dismounted early at %s", here)
                return here[0] >= DISMOUNT[0]
            if here[0] >= EAST_STAGE[0]:
                return True
            if here[1] != CROSS_ROW:
                log.info("  drifted off row %d to %s", CROSS_ROW, here)
                return False
            if not d.step_dir("R"):
                if d.in_battle():
                    self._battle()
                    continue
                log.info("  blocked crossing at %s: %s", here,
                         d.last_step_reason)
                return False
            if d.in_battle():
                self._battle()
        return d.pos()[0] >= EAST_STAGE[0]

    def route_in(self) -> bool:
        """Littleroot-to-the-door, resumable from anywhere on the way."""
        d = self.d
        if d.at_title():
            log.info("save is on the title screen; resuming")
            d.resume_from_title()
        unwedge(d)
        if d.map_name() in (ENTRANCE, INSIDE):
            return True
        if not self.to_route110():
            return False
        log.info("on %s at %s", d.map_name(), d.pos())
        # Walk the north bank first. From the water this dismounts onto the
        # only elevation-3 shore the west pond has.
        if not self.walk_leg(*WEST_SHORE):
            return False
        # `goto` mounts Surf itself: `walk` turns a land->water step into
        # face-A-YES once `nav.surfing` is set, and `_surf_sync` sets it from
        # `can_surf()`.
        if not self.walk_leg(*WEST_STAGE, budget=150.0):
            return False
        if not d.is_surfing():
            log.info("reached %s without the surf blob", d.pos())
            return False
        log.info("staged at %s, surfing, elevation %s", d.pos(), d.elevation())
        if not self.cross_cycling_road():
            return False
        log.info("crossed to %s", d.pos())
        if d.is_surfing() and not d.step_dir("R"):
            log.info("could not dismount onto %s: %s", DISMOUNT,
                     d.last_step_reason)
            return False
        if not self.walk_leg(*SHELF):
            return False
        if not d.take_warp(*DOOR_WARP):
            log.info("warp %s refused: %s", DOOR_WARP, d.last_warp_reason)
            return False
        log.info("inside: %s %s", d.map_name(), d.pos())
        return d.map_name() == ENTRANCE

    # ---- the door --------------------------------------------------------

    def open_door(self) -> bool:
        """Answer the BASEMENT KEY prompt and sync the rewritten metatiles."""
        d = self.d
        if d.state.var("VAR_NEW_MAUVILLE_STATE"):
            # ALREADY OPEN IS NOT ALREADY WALKABLE. Pacing the room steps on
            # (4,2) sooner or later, `advance_scene` answers the YES/NO box
            # (the cursor starts on YES), and the door opens with nobody
            # watching -- so the metatile rewrite has never been read back and
            # (4,1) is still a wall in the static grid. Skipping this sync is
            # what made the first run answer "no approach to warp (4,1)" on a
            # door that was standing open.
            d.sync_grid()
            log.info("door already open; warp cell %s",
                     d.nav.cell(ENTRANCE, *INSIDE_WARP))
            return True
        if "BASEMENT KEY" not in (d.state.bag().get("key_items") or {}):
            log.info("no BASEMENT KEY; the door stays shut")
            return False
        if not self.walk_leg(*DOOR_APPROACH):
            return False
        if not d.step_dir("U"):
            log.info("could not step onto the trigger: %s", d.last_step_reason)
            return False
        menus = Menus(d.emu, d.state)
        # "The door is closed." comes first and is NOT the choice, even though
        # `choice_open()` already reads True behind it.
        for _ in range(6):
            if "BASEMENT KEY" in (d.state.message() or ""):
                break
            d.emu.run_sequence("A:6 .:70")
            d.settle(600)
        if not menus.resolve_choice("YES"):
            log.info("could not answer the BASEMENT KEY prompt")
            return False
        d.settle(900)
        d.advance_scene(30000)
        opened = bool(d.state.var("VAR_NEW_MAUVILLE_STATE"))
        # setmetatile leaves the static grid reading a wall, so the warp at
        # (4,1) is unusable until the live grid is read back.
        d.sync_grid()
        log.info("door state %s; warp cell %s",
                 d.state.var("VAR_NEW_MAUVILLE_STATE"),
                 d.nav.cell(ENTRANCE, *INSIDE_WARP))
        return opened

    # ---- the hunt --------------------------------------------------------

    def hunt(self, deadline) -> int:
        """Pace the entrance room until MAGNEMITE and VOLTORB are both in."""
        d = self.d
        got = 0
        while time.time() < deadline:
            want = self.outstanding()
            if not want:
                break
            if d.map_name() != ENTRANCE:
                if not self.walk_leg(*SHELF) or not d.take_warp(*DOOR_WARP):
                    log.info("lost the room; standing on %s at %s",
                             d.map_name(), d.pos())
                    break
            if self.c.balls() <= self.c.BALL_FLOOR and not self.restock():
                log.info("out of balls with %s still missing", want)
                break
            before = len(self.caught())
            # SHORT CHUNKS. `pace_map` runs to the deadline it is handed and
            # has no idea which species this map owes -- it does not return
            # when they close. Paced in one long block it kept farming
            # VOLTORB for four more minutes after both were registered, so
            # the loop above only gets to notice at chunk boundaries.
            got += self.c.pace_map(min(deadline, time.time() + 75.0))
            after = len(self.caught())
            if after > before:
                self.c.save()
            log.info("dex %d -> %d; still missing %s",
                     before, after, self.outstanding())
        return got

    def restock(self) -> bool:
        """Fly out for Great Balls, then walk the whole route back in."""
        log.info("restocking balls (%d left)", self.c.balls())
        if not self.c.restock_balls():
            return False
        self.c.save()
        return self.route_in()

    # ---- the THUNDER STONE ----------------------------------------------

    def _barrier_state(self):
        d = self.d
        return (d.state.var("VAR_TEMP_1"), d.state.var("VAR_TEMP_2"))

    def _reach(self):
        d = self.d
        try:
            return d.nav.reachable(INSIDE, d.pos(), d.elevation())
        except Exception:  # noqa: BLE001
            return set()

    def _pick_up(self, cell) -> bool:
        """Take whatever object_event sits on `cell`, from an adjacent tile.

        An item ball blocks its own cell, so it is never walked onto. Three of
        the cells this is called on are not item balls at all: (25,18), (6,11)
        and (13,10) are the static L25 VOLTORB drawn with the item-ball
        graphic, and talking to one opens a battle. They set their hide flag
        BEFORE the battle (`NewMauville_Inside/scripts.inc:171,186,201`), so
        the object is gone either way -- which is the point here, since the
        button underneath is what we want. The dex loses nothing: VOLTORB is
        already registered off the entrance table.
        """
        d = self.d
        reach = self._reach()
        for x, y, _face in _adjacent(cell):
            if (x, y) not in reach:
                continue
            if not self.walk_leg(x, y, tries=3, budget=45.0):
                continue
            try:
                d.talk_to(*cell)
            except Exception as exc:  # noqa: BLE001
                log.info("  talk_to %s: %s", cell, str(exc)[:70])
                continue
            if d.in_battle():
                self._battle()
                return True
            for _ in range(8):
                if not d.scene_active():
                    break
                d.emu.run_sequence("A:6 .:70")
                d.settle(500)
            d.advance_scene(20000)
            return True
        log.info("  no free tile beside %s", cell)
        return False

    def _press(self, cell) -> bool:
        """Walk onto a barrier button and read the rewritten grid back.

        A button is an ordinary passable floor tile with a coord_event on it,
        so it is WALKED ONTO -- not "stand beside it and step". And the walk
        is the nav PATH, hand-fed to `walk`, not `goto`: on this map `goto`
        pinned at (31,36) for 150 seconds with the watchdog reporting "frames
        are advancing but the player is not moving", while the identical
        six-step path D D L L D D walked first time. Every tile here is an
        encounter tile, so the replanning loop inside `goto` re-enters
        `advance_scene` constantly and gets no turn to move.
        """
        d = self.d
        for _ in range(6):
            if d.pos() == cell:
                break
            if d.in_battle():
                self._battle()
            path = d.nav.find_path(INSIDE, d.pos(), cell, d.elevation())
            if path is None:
                log.info("  no path to button %s from %s", cell, d.pos())
                return False
            if not d.walk("".join(path)) and d.pos() != cell:
                if d.in_battle():
                    self._battle()
                    continue
                log.info("  blocked walking to %s: %s", cell,
                         d.last_step_reason)
        if d.pos() != cell:
            return False
        d.advance_scene(20000)
        d.sync_grid()
        var = dict(BARRIER_BUTTONS)[cell]
        got = self._barrier_state()
        log.info("  pressed %s -> barriers %s", cell, got)
        # The coord_event only fires while its own var reads 0, so a press
        # that did not flip the var did not happen -- and re-walking onto the
        # same tile never will.
        return got[var - 1] == 1

    def _live_objects(self) -> set:
        """Cells the running game currently has an object_event on.

        Not `map.json`: five of these are item balls behind
        FLAG_ITEM_NEW_MAUVILLE_INSIDE_1..5 and three are the static VOLTORB
        behind FLAG_HIDE_VOLTORB_*_NEW_MAUVILLE, so which of them exist
        depends on the save. `gObjectEvents` is the only honest answer.
        """
        try:
            return {(n["x"], n["y"]) for n in self.d.live_npcs()
                    if not n.get("player")}
        except Exception:  # noqa: BLE001
            return set(INSIDE_OBJECTS)

    def _barrier_overrides(self, temps) -> dict:
        """`{(x,y): impassable}` for the barrier columns under `temps`.

        A barrier is a four-cell column and only its BOTTOM TWO cells ever
        open: the "hidden" writes keep y+0 and y+1 impassable (the emitter
        housing) and pass y+2 and y+3 (`EventScript_15E5DA` / `_15E728`).
        Modelling all four as a gate is what makes a plan that cannot walk.
        """
        t1, t2 = temps
        out = {}
        if not t1 and not t2:
            return out            # the shipped .blk: every column shut
        raised, hidden = (BARRIER_GREEN, BARRIER_BLUE) if t1 \
            else (BARRIER_BLUE, BARRIER_GREEN)
        for x, y in raised:
            for k in range(4):
                out[(x, y + k)] = True
        for x, y in hidden:
            out[(x, y)] = True
            out[(x, y + 1)] = True
            out[(x, y + 2)] = False
            out[(x, y + 3)] = False
        return out

    def _model_component(self, start, temps, blocked):
        """Walkable cells from `start`, on the STATIC grid plus overrides.

        Deliberately not `nav.reachable`: nav's live cells already hold the
        CURRENT barrier state, and planning a future one on top of them
        double-applies. `nav.grid` is the undecorated .blk.
        """
        grid = self.d.nav.grid(INSIDE)
        h, w = len(grid), len(grid[0])
        ov = self._barrier_overrides(temps)

        def open_at(x, y):
            if not (0 <= x < w and 0 <= y < h):
                return False
            if (x, y) in ov:
                return not ov[(x, y)]
            return grid[y][x].collision == 0

        seen = {start}
        queue = [start]
        while queue:
            x, y = queue.pop()
            for nx, ny in ((x, y - 1), (x + 1, y), (x, y + 1), (x - 1, y)):
                if (nx, ny) in seen or (nx, ny) in blocked:
                    continue
                if not open_at(nx, ny):
                    continue
                seen.add((nx, ny))
                queue.append((nx, ny))
        return seen

    def _plan_stone(self):
        """The shortest ("clear" | "press") sequence that opens (21,4)-(21,5).

        A greedy "press whatever button is nearest" loop cannot solve this: the
        two buttons in the arrival chamber toggle each other, so it ping-pongs
        (30,38) <-> (18,36) forever, which is exactly what the first attempt
        did. The search is over (where we stand, which barrier set is up,
        which objects we have removed) and it is tiny -- nine buttons, eight
        objects, three barrier states.
        """
        start = (self.d.pos(), self._barrier_state(), frozenset())
        alive = self._live_objects()
        queue = [(start, [])]
        seen = set()
        while queue:
            (pos, temps, cleared), path = queue.pop(0)
            key = (pos, temps, cleared)
            if key in seen:
                continue
            seen.add(key)
            blocked = (alive - cleared) - {pos}
            reach = self._model_component(pos, temps, blocked)
            if any(c in reach for c in STONE_APPROACHES):
                return path
            for cell in sorted(alive - cleared):
                beside = [c for c in _neighbours(cell) if c in reach]
                if not beside:
                    continue
                queue.append(((beside[0], temps, cleared | {cell}),
                              path + [("clear", cell)]))
            for cell, var in BARRIER_BUTTONS:
                if cell in (alive - cleared) or cell not in reach:
                    continue
                nxt = (1, 0) if var == 1 else (0, 1)
                if nxt == temps:
                    continue
                queue.append(((cell, nxt, cleared),
                              path + [("press", cell)]))
        return None

    def thunder_stone(self, deadline) -> bool:
        """Fetch the THUNDER STONE at (39,4), solving the barriers on the way."""
        d = self.d
        if not self.open_door():
            return False
        if d.map_name() != INSIDE:
            # (4,1) IS the warp cell; `take_warp` walks the approach itself,
            # but only after the door rewrite has been read back.
            if not d.take_warp(*INSIDE_WARP):
                log.info("warp into Inside refused: %s", d.last_warp_reason)
                return False
        for lap in range(24):
            if time.time() > deadline:
                log.info("thunder stone: out of budget")
                return False
            # The barriers are written with setmetatile, so the static grid
            # is wrong the moment a button is pressed; and an item ball is an
            # object_event that blocks its own cell, which no grid records.
            d.sync_grid()
            d.nav.mark_blocked(INSIDE, self._live_objects() - {d.pos()},
                               replace=True)
            if any(c in self._reach() for c in STONE_APPROACHES):
                if not self._pick_up(THUNDER_STONE_BALL):
                    return False
                have = d.state.bag().get("items") or {}
                log.info("bag now holds THUNDER STONE x%s",
                         have.get("THUNDER STONE"))
                return "THUNDER STONE" in have
            plan = self._plan_stone()
            if not plan:
                log.info("thunder stone: no plan from %s in barrier state %s",
                         d.pos(), self._barrier_state())
                return False
            log.info("  plan (%d steps): %s", len(plan), plan[:4])
            kind, cell = plan[0]
            done = self._pick_up(cell) if kind == "clear" else self._press(cell)
            if not done:
                log.info("thunder stone: could not %s %s", kind, cell)
                return False
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state", default="saves/newmauville.state")
    ap.add_argument("--minutes", type=float, default=25.0)
    ap.add_argument("--feed", default=None,
                    help="live feed name; defaults to the state file's own")
    ap.add_argument("--stone", action="store_true",
                    help="also fetch the THUNDER STONE inside")
    ap.add_argument("--stone-minutes", type=float, default=8.0)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(args.state)
    feed = args.feed or getattr(getattr(d, "feed", None), "name", None) \
        or Path(args.state).stem
    c = collect.Collector(d, feed_name=feed)
    h = Hunt(d, c)

    before = sorted(h.outstanding())
    log.info("dex %d caught; missing here: %s", len(h.caught()), before)
    if not h.route_in():
        log.info("FAILED to reach %s", ENTRANCE)
        return 1
    c.save()
    got = h.hunt(time.time() + args.minutes * 60.0)
    after = sorted(h.outstanding())
    log.info("caught %d new; dex %d; still missing here: %s",
             got, len(h.caught()), after)
    c.save()

    if args.stone:
        if h.thunder_stone(time.time() + args.stone_minutes * 60.0):
            log.info("THUNDER STONE obtained")
            c.save()
        else:
            log.info("THUNDER STONE not obtained")
    return 0 if not after else 2


if __name__ == "__main__":
    raise SystemExit(main())
