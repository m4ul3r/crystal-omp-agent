#!/usr/bin/env python
"""The Driver: one warm process that plays Pokemon Sapphire.

Same shape as the Crystal harness's ``trek.py`` -- boot once, compose calls
against a single ``Driver`` -- with the same doctrine behind it: the model
decides WHAT (goto here, fight, catch), the code executes HOW (pathfind, walk
menus, wait out animations, verify arrival).

Two things are genuinely better than the Crystal original, both because the
GBA build gives us named symbols for code as well as data:

* **"A scene owns input" is a fact, not a guess.** ``gPlayerAvatar.preventStep``
  (include/global.fieldmap.h:0x06) is the engine's own flag. Crystal inferred
  the same condition from "position stopped changing" plus screen text, which
  is why its journal has a whole failure class about stuck movement.
* **"Which screen am I on" is exact.** ``gTasks`` and ``gMain.callback2`` are
  function pointers; the symbol table names them.

Every primitive that can fail sets a ``last_*_reason`` string rather than
returning a bare False, because an unexplained falsy return was the single
most expensive thing in the Crystal run (its RETROSPECTIVE P1-4).
"""

import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent import cstruct, jitter, paths
from pokeagent.behaviors import Behaviors
from pokeagent.cconst import Constants
from pokeagent.charmap import Charmap
from pokeagent.emu import Sapphire
from pokeagent.names import Names
import time as _time

from pokeagent import nav as nav_mod
from pokeagent.nav import DIRS, MAP_OFFSET, MapData
from pokeagent.state import GameState
from pokeagent.symbols import Symbols

log = logging.getLogger("trek")

#: Frames a held direction needs to complete one tile of walking.
STEP_FRAMES = 16
#: Frames to hold through a warp. The warp only fires if the key is still
#: down when the step completes -- Crystal's gotcha 12, and the engine works
#: the same way here.
WARP_HOLD = 32

_HOLD = {"U": "UP", "D": "DOWN", "L": "LEFT", "R": "RIGHT"}


class TravelError(RuntimeError):
    pass


class TravelInterrupted(TravelError):
    """A battle started mid-journey.

    Movement must never silently auto-fight -- what to do about an encounter
    is a decision, and the predecessor project lists "the harness decided for
    the model" as the cause of every one of its multi-whiteout runs. So the
    journey stops and hands the battle back, with everything needed to
    resume.
    """

    def __init__(self, map_name, pos, dest_map):
        super().__init__(
            f"a battle started at {map_name} {pos} while travelling to "
            f"{dest_map}; resolve it, then call travel() again"
        )
        self.map_name = map_name
        self.pos = pos
        self.dest_map = dest_map


#: `struct ObjectEvent` is 0x24 bytes ("size = 0x24" in
#: pret/include/global.fieldmap.h) and the array holds OBJECT_EVENTS_COUNT of
#: them (constants/global.h:44).
OBJECT_EVENT_SIZE = 0x24
OBJECT_EVENTS_COUNT = 16


class Driver:
    """A live game plus every verb the model can invoke on it."""

    def __init__(self, state_path=None, fresh=False, live=None, game="sapphire",
                 brain=None):
        """Open a game.

        `game` is a GameSpec id (see pokeagent/gamespec.py). The whole stack
        is built by that generation's adapter, so nothing below this line
        branches on generation -- which is what lets the same Driver drive
        Crystal and Sapphire.
        """
        from pokeagent import gamespec
        from pokeagent.adapters import base as adapters

        self.spec = gamespec.get(game)
        self.adapter = adapters.resolve(self.spec)
        self.state_path = Path(state_path) if state_path else None

        backend = self.adapter.open(
            state_path=None if fresh else self.state_path, fresh=fresh
        )
        self.backend = backend
        self.sym = backend.sym
        self.charmap = backend.charmap
        self.consts = backend.consts
        self.emu = backend.emu
        self.names = backend.names
        self.state = backend.state
        self.nav = backend.nav
        self.behaviors = backend.extra.get("behaviors")
        self.capabilities = backend.capabilities
        self.avatar = cstruct.layout("PlayerAvatar", "global.fieldmap.h")

        # Failure diagnostics. None means "the last call succeeded".
        self.last_step_reason = None
        self.last_goto_reason = None
        self.last_warp_reason = None
        self.last_talk_reason = None
        self.last_menu_reason = None
        self.last_scene_reason = None
        self.last_heal_reason = None
        self.last_fish_reason = None
        self.last_fish_detail = ""
        self.last_fly_reason = None
        self.last_fly_detail = ""

        # PUBLISH BY DEFAULT when driving a real save.
        #
        # Publishing used to be opt-in per script, and that failed three times
        # in one day: swapping the play loop for `collect.py`, then
        # `safari_probe.py`, then `badge7_chain.py` each left the widget frozen
        # on a stale frame while the emulator worked perfectly. The third time
        # it was reported as "it hasn't moved in over 5000 seconds", and the
        # measurement agreed exactly -- `live/default.png` was 89 minutes old,
        # because the only publisher running was the from-zero run writing to a
        # different feed name.
        #
        # A human watching cannot tell "no publisher" from "hung game", so the
        # default is inverted: any Driver opening a state file under the saves
        # directory attaches a feed named after that file, and tests are
        # untouched because they fork into temp paths. `live=False` opts out.
        self.feed = live
        if live is None and state_path and not fresh:
            self.feed = self._autofeed(state_path)

        # The local model, and the boundary that decides what it may answer.
        # See pokeagent/smallchoices.py: the harness computes what it can
        # compute exactly and the model only breaks ties or supplies flavour,
        # because gemma4:e4b measured 5/5 on single-hop type questions but
        # wrong on move->type->matchup inference and on numeric judgment.
        from pokeagent.smallchoices import SmallChoices

        self.brain = brain
        self.choices = SmallChoices(brain)

        # Battle stack. Built lazily: constructing Tactics reads ROM tables
        # that a pure-navigation session never needs.
        self._tactics = None
        self._battle = None
        self._fishing = None
        self._flight = None

    # ---- reading -------------------------------------------------------

    def pos(self):
        loc = self.state.location()
        return loc.x, loc.y

    def map_name(self):
        return self.state.location().map_name

    def facing(self):
        return self.state.facing()

    #: `PLAYER_AVATAR_FLAG_MACH_BIKE` (include/global.fieldmap.h:244).
    MACH_BIKE_FLAG = 1 << 1
    #: `GetPlayerSpeed` returns `sMachBikeSpeeds[bikeFrameCounter]`, and that
    #: table is `{SPEED_NORMAL, SPEED_FAST, SPEED_FASTEST}` = `{1, 2, 4}`
    #: (bike.c:121, :1044-1055). `ForcedMovement_MuddySlope` climbs only when
    #: the speed is STRICTLY above 3 (field_player_avatar.c:498), so the
    #: counter must have reached 2 -- nothing slower gets up the slope.
    MACH_BIKE_SPEEDS = (1, 2, 4)

    def on_bike(self) -> bool:
        """Is the player on the Mach Bike right now?"""
        try:
            base = self.emu.resolve("gPlayerAvatar")
            return bool(self.emu.u8(base) & self.MACH_BIKE_FLAG)
        except Exception:  # noqa: BLE001 - no symbol, no bike
            return False

    def bike_speed(self) -> int:
        """`GetPlayerSpeed()` for the Mach Bike, or 0 when not riding it.

        Read rather than counted: the acceleration is reset by things the
        driver does not see (a ledge, a bumpy slope, any refused step), and a
        run-up that is believed rather than measured is how a slope silently
        slides you back down.
        """
        if not self.on_bike():
            return 0
        try:
            base = self.emu.resolve("gPlayerAvatar")
            counter = self.emu.u8(base + 0x0A)
            return self.MACH_BIKE_SPEEDS[min(counter, 2)]
        except Exception:  # noqa: BLE001
            return 0

    @property
    def teacher(self):
        """Lazy `Teacher`, for the same reason `flight` is lazy: constructing
        it parses the item tables, and a session that never opens the bag has
        no use for them."""
        t = getattr(self, "_teacher", None)
        if t is None:
            from pokeagent.teaching import Teacher
            t = self._teacher = Teacher(self)
        return t

    def mount_bike(self) -> bool:
        """Get on the Mach Bike, and tell nav it may plan muddy slopes.

        The bike is the ONLY thing that climbs a muddy slope, and a muddy
        slope is the only way from the Safari Zone's southern half into the
        north-west quadrant -- 7 species that were unreachable while nothing
        could ride. `nav.mach_bike` gates that pathing and is kept honest
        against the avatar rather than against our intent.

        Refuses indoors: `MetatileBehavior`/`IsBikingAllowed` reject it and the
        bag would be left open (gotcha 7).
        """
        self.last_bike_reason = None
        if self.on_bike():
            self.nav.mach_bike = True
            return True
        try:
            pocket = self.state.bag().get("key_items") or {}
        except Exception:  # noqa: BLE001
            pocket = {}
        if not any(str(k).upper() == "MACH BIKE" for k in pocket):
            self.last_bike_reason = "no-bike"
            return False
        if not self.teacher.use_key_item("MACH BIKE"):
            self.last_bike_reason = (
                getattr(self.teacher, "last_reason", None) or "bag-refused")
            self.close_menus()
            return False
        self.settle(60)
        if not self.on_bike():
            # Indoors, on water, or a tile that refuses -- the engine said no.
            self.last_bike_reason = "engine-refused"
            self.close_menus()
            return False
        self.nav.mach_bike = True
        return True

    def dismount_bike(self) -> bool:
        """Get off, and stop planning routes only a bike can walk."""
        self.last_bike_reason = None
        if not self.on_bike():
            self.nav.mach_bike = False
            return True
        if not self.teacher.use_key_item("MACH BIKE"):
            self.last_bike_reason = (
                getattr(self.teacher, "last_reason", None) or "bag-refused")
            self.close_menus()
            return False
        self.settle(60)
        self.nav.mach_bike = self.on_bike()
        return not self.on_bike()

    def climb_slope(self, d: str = "U", run_up: int = 4) -> bool:
        """Ride UP a muddy slope, holding the direction through the run-up.

        `ForcedMovement_MuddySlope` (field_player_avatar.c:494-504) slides you
        back and calls `Bike_UpdateBikeCounterSpeed(0)` unless you are moving
        NORTH at a speed strictly above 3. `sMachBikeSpeeds` tops out at 4 and
        only when `bikeFrameCounter` has reached 2, which takes two tiles of
        CONTINUOUS held movement -- so the key must go down before the run-up
        and stay down over the slope. Releasing between tiles is what makes a
        slope look impassable when it is merely uphill.

        Returns True when the player actually ended up further along `d`.
        """
        self.last_bike_reason = None
        if d != "U":
            self.last_bike_reason = "not-uphill"
            return False
        if not self.on_bike() and not self.mount_bike():
            return False
        here = self.map_name()
        before = self.pos()
        # ONE held press, long enough for the run-up AND the slope. Split
        # presses reset the counter, which is the whole bug this avoids.
        tiles = max(2, run_up) + 4
        self.emu.run_sequence(f"{_HOLD[d]}:{tiles * 12}")
        self.settle(60)
        after = self.pos()
        # A MAP CHANGE IS THE BEST OUTCOME, NOT A FAILURE. Riding north off
        # the top of a map lands at the BOTTOM of the next one, so y jumps up
        # rather than down: the first working climb ended at
        # `SafariZone_Northwest (8,27)` from `Southwest (8,8)` and a naive
        # `after.y >= before.y` reported "slid back" about a success.
        if self.map_name() != here:
            return True
        if after == before:
            self.last_bike_reason = "did-not-move"
            return False
        if after[1] >= before[1]:
            self.last_bike_reason = f"slid back: {before} -> {after}"
            return False
        return True

    def elevation(self):
        """The player's live z, which BFS needs as its start elevation.

        `currentElevation` is a 4-bit field sharing its byte with
        `previousElevation` (include/global.fieldmap.h:0x0B), so the whole
        byte reads as e.g. 0x33. Masking matters: an unmasked 51 matches no
        tile, and every goto returns no-path.
        """
        base = self.emu.resolve("gObjectEvents")
        oe = cstruct.layout("ObjectEvent", "global.fieldmap.h")
        # THE PLAYER IS NOT ALWAYS gObjectEvents[0]. This read the array's
        # first element outright, which is whichever object the engine happened
        # to load first -- a trainer, an item ball, a boulder -- and returned
        # ITS level as the player's. `gPlayerAvatar.objectEventId` says which
        # slot is ours (`GetCollisionAtCoords` itself indexes that way, via
        # `&gObjectEvents[gPlayerAvatar.objectEventId]` in
        # `CheckForPlayerAvatarCollision`, field_player_avatar.c:584).
        #
        # Every route's START LEVEL comes from here, so on any map where the
        # player is not slot 0 the planner was seeded with a stranger's
        # elevation and could plan across seams the engine refuses.
        slot = self._avatar("objectEventId")
        if slot >= OBJECT_EVENTS_COUNT:
            slot = 0
        addr = base + slot * OBJECT_EVENT_SIZE + oe["currentElevation"]
        return self.emu.u8(addr) & 0x0F

    def _avatar(self, field_name):
        return self.emu.u8(self.emu.resolve("gPlayerAvatar") + self.avatar[field_name])

    def scene_active(self) -> bool:
        """True when a script, message or cutscene owns input.

        Two engine flags, both read directly rather than inferred:

        * ``sLockFieldControls`` (src/script.c:179-191) is set by every script
          that freezes the player -- the exact analog of Crystal's
          ``wScriptMode``, which its harness could only ever guess at.
        * ``gPlayerAvatar.preventStep`` covers cutscenes that freeze the
          avatar without taking the script lock.

        Crystal inferred this condition from "position stopped changing" plus
        screen text, which is the root of a whole failure class in its
        journal (a stray menu silently eating movement, gotcha 7).
        """
        return bool(self.emu.u8("sLockFieldControls") or self._avatar("preventStep"))

    def moving(self) -> bool:
        return bool(self._avatar("runningState") or self._avatar("tileTransitionState"))

    def dialog_open(self) -> bool:
        return "Task_FieldMessageBox" in self.state.tasks()

    def in_battle(self) -> bool:
        return self.state.in_battle()

    def observe(self) -> dict:
        """The full snapshot the decider works from."""
        snap = self.state.snapshot()
        x, y = self.pos()
        m = self.map_name()
        tiles = {}
        for d, (dx, dy) in DIRS.items():
            c = self.nav.cell(m, x + dx, y + dy)
            tiles[d] = c.kind if c else "offmap"
        here = self.nav.cell(m, x, y)
        tiles["here"] = here.kind if here else "offmap"
        snap["tiles"] = tiles
        snap["ui"]["scene"] = self.scene_active()

        snap["ui"]["dialog"] = self.dialog_open()
        snap["ui"]["callback"] = self.state.callback_name()
        snap["ui"]["tasks"] = self.state.tasks()
        snap["location"]["elevation"] = self.elevation()
        snap["npcs"] = [
            {"x": o["x"], "y": o["y"], "gfx": o.get("graphics_id", "")}
            for o in self.nav.info(m).objects
        ]
        return snap

    def live_npcs(self) -> list[dict]:
        """Where the NPCs actually are right now, from ``gObjectEvents``.

        The map JSON gives each object's *initial* placement, but scripts move
        them: the rival's bedroom runs ``setobjectxyperm 1, 7, 2`` on map
        transition, so the JSON coordinate is already wrong by the time you
        walk in. Talking to a static coordinate therefore misses.

        Coordinates here are the engine's padded ones, so MAP_OFFSET is
        subtracted to bring them into the same space as `pos()` and the map
        JSON.
        """
        oe = cstruct.layout("ObjectEvent", "global.fieldmap.h")
        base = self.emu.resolve("gObjectEvents")
        size = self.sym.size("gObjectEvents") // 16
        out = []
        for i in range(16):
            raw = self.emu.read(base + i * size, size)
            if not raw[oe["active"]] & 1:
                continue
            cx = int.from_bytes(
                raw[oe["currentCoords"] : oe["currentCoords"] + 2], "little", signed=True
            )
            cy = int.from_bytes(
                raw[oe["currentCoords"] + 2 : oe["currentCoords"] + 4], "little", signed=True
            )
            out.append(
                {
                    "index": i,
                    "player": i == 0,
                    "local_id": raw[oe["localId"]],
                    "graphics_id": raw[oe["graphicsId"]],
                    "x": cx - MAP_OFFSET,
                    "y": cy - MAP_OFFSET,
                    "elevation": raw[oe["currentElevation"]] & 0x0F,
                    "facing": raw[oe["facingDirection"]] & 0x0F,
                }
            )
        return out

    def status(self, missing=True) -> str:
        line = self.state.status_line()
        if missing:
            from pokeagent import missables

            fragment = missables.status_fragment(self.state)
            if fragment:
                line += " | " + fragment
        return line

    # ---- waiting -------------------------------------------------------

    def settle(self, max_frames=900, quiet=6):
        """Advance until the world stops changing: no movement in flight, no
        fade running, and the frame's state stable for `quiet` samples."""
        fade = self.emu.resolve("gPaletteFade")
        stable = 0
        spent = 0
        while spent < max_frames:
            self.emu.tick(jitter.frames(4))
            spent += 4
            busy = self.moving() or bool(self.emu.u8(fade + 7) & 0x80)
            stable = 0 if busy else stable + 1
            if stable >= quiet:
                return True
        return False

    def flush_dialog(self, max_frames=6000):
        """Advance messages with A until the box closes.

        Stops the moment input is no longer owned by a scene, so it cannot
        keep pressing A into the overworld and re-trigger the NPC in front of
        you (Crystal gotcha 8) or, worse, buy something (its gotcha 13).
        """
        spent = 0
        while spent < max_frames:
            if not self.dialog_open() and not self.scene_active():
                self.settle(120)
                return True
            self.emu.run_sequence(jitter.sequence("A:4 .:16"))
            spent += 20
        return False

    def drain_scene(self, max_frames=12000):
        """Wait out a cutscene that owns input, WITHOUT pressing anything.

        Some scenes advance on their own and an A press during them selects
        something. Use this when `scene_active()` is true but no message box
        is up.
        """
        spent = 0
        while spent < max_frames and self.scene_active():
            self.emu.tick(20)
            spent += 20
        return not self.scene_active()

    def naming_open(self) -> bool:
        """True while the nickname/naming keyboard owns input."""
        from pokeagent.naming import NamingScreen

        return NamingScreen(self.emu, self.state).is_open()

    def learn_open(self) -> bool:
        """True while a "wants to learn X" prompt is waiting for a slot.

        This fires OUTSIDE a battle too. Evolving is the case that caught the
        loop: Torchic became Combusken at L16 and Combusken's DOUBLE KICK
        arrived during the evolution scene, where `advance_scene` was pressing
        A at every stall. The prompt took an A, the forget-screen took the
        next, and the cursor's resting slot -- SCRATCH, the mon's strongest
        move -- was overwritten while FOCUS ENERGY, a 0-power status move,
        survived. The policy that exists to prevent exactly that was never
        consulted, because nothing told the scene runner this was a question.
        """
        return self.battle.at_learn_prompt()

    def choice_open(self) -> bool:
        """True while a YES/NO-style choice box is waiting for an answer."""
        from pokeagent.menus import Menus

        m = Menus(self.emu, self.state)
        lo, hi = m.bounds()
        return self.scene_active() and hi > lo and hi - lo <= 3

    def advance_scene(self, max_frames=120000, stall_rounds=6, release_rounds=12,
                      on_prompt="stop"):
        """Run a cutscene to completion, pressing A only when it is STALLED.

        Story scenes mix two kinds of waiting: stretches that advance on
        their own (walking NPCs, fades) and prompts that block until you
        press A. Pressing A through the first kind is how you accidentally
        answer a menu; never pressing it means the scene hangs forever.

        So: watch a signature of (tasks, position, message). While it keeps
        changing, just tick. When it has been identical for `stall_rounds`
        samples and a scene still owns input, press A once and resume
        watching. Returns True when input comes back to the player.
        """
        last, same, spent, free = None, 0, 0, 0
        backing = 0
        while spent < max_frames:
            if not self.scene_active() and not self.dialog_open():
                # A story script releases the lock between beats and takes it
                # straight back (Birch's lab walks you in after the first
                # message). Returning on the first release leaves the caller
                # thinking the scene is over while it is mid-sentence, so the
                # release has to HOLD before we believe it.
                free += 1
                if free >= release_rounds:
                    self.settle(jitter.frames(240))
                    return True
                self.emu.tick(jitter.frames(20))
                spent += 20
                continue
            free = 0
            # A scene that asks a QUESTION must never be answered blindly.
            # Mashing A through the Birch lab sequence nicknames your starter
            # "AAAAAAAAAA", which is the predecessor's stray-A naming bug
            # reproduced exactly. Stop and hand the decision back.
            if self.naming_open():
                self.last_scene_reason = "naming keyboard open; name it deliberately"
                if on_prompt == "stop":
                    return False
            # The same rule, for the other question a scene can ask. An
            # evolution's move-learn prompt used to eat two blind A presses
            # here and overwrite whichever slot the cursor happened to rest
            # on -- the mon's best move -- with the learn policy never asked.
            if self.learn_open():
                self.last_scene_reason = (
                    "move-learn prompt open; choose the slot deliberately"
                )
                if on_prompt == "stop":
                    return False
            # A full-screen menu we did not open. Catching a new species pops
            # the Pokedex entry, one more A opens the Pokedex ITSELF, and from
            # there every stall-press just navigates it: a run sat on
            # Route 101 for three minutes burning 40k frames a step inside
            # Task_PokedexMainScreen. A is "advance the text"; in a menu it
            # means "choose something", so these get B.
            owned = self._foreign_menu()
            if owned:
                self.last_scene_reason = f"backing out of {owned}"
                self.emu.run_sequence(jitter.sequence("B:4 .:24"))
                spent += 28
                same = 0
                backing = 3      # the lock outlives the menu by a press or two
                continue
            if backing and not self.dialog_open():
                # The menu is gone but sLockFieldControls is still set, and A
                # will not clear it -- measured: the Pokedex shut on the first
                # B and control came back on the third.
                backing -= 1
                self.emu.run_sequence(jitter.sequence("B:4 .:24"))
                spent += 28
                same = 0
                continue
            sig = (tuple(self.state.tasks()), self.pos(), self.state.message())
            same = same + 1 if sig == last else 0
            last = sig
            if same >= stall_rounds:
                self.emu.run_sequence(jitter.sequence("A:4 .:16"))
                same = 0
                spent += 20
            else:
                self.emu.tick(jitter.frames(20))
                spent += 20
        return False

    # ---- movement ------------------------------------------------------

    def predict_step(self, direction):
        """Where a step WOULD land, as the driver itself models it.

        `nav.step` answers from the static grid alone. That grid is exactly
        right and still not the whole truth: an NPC body blocks a tile the
        map data says is floor. So the driver's prediction is the static rule
        plus the live object list, recomputed every call -- never cached,
        because a stale block severed the map three separate times in the
        predecessor project (its journal #11, #66).
        """
        m = self.map_name()
        self._mark_npcs(m)
        x, y = self.pos()
        return self.nav.step(m, x, y, self.elevation(), direction)

    def step_dir(self, d, verify=True):
        """One step. Returns True only if the position actually changed."""
        self.last_step_reason = None
        if d not in DIRS:
            self.last_step_reason = f"bad direction {d!r}"
            return False
        if self.scene_active():
            self.last_step_reason = "scene-owns-input (gPlayerAvatar.preventStep)"
            return False
        before = self.pos()
        before_map = self.map_name()
        self.emu.run_sequence(f"{_HOLD[d]}:{STEP_FRAMES}")
        self.settle(jitter.frames(240))
        if not verify:
            return True
        if self.map_name() != before_map:
            return True
        if self.pos() == before:
            # Facing a different way costs a step in Gen 3 too: if the player
            # was not already facing `d`, the first press only turns.
            self.emu.run_sequence(f"{_HOLD[d]}:{STEP_FRAMES}")
            self.settle(jitter.frames(240))
            if self.pos() == before and self.map_name() == before_map:
                self.last_step_reason = f"blocked moving {d} from {before}"
                return False
        return True

    def walk(self, path):
        """Walk a sequence of direction letters, verifying each step.

        A step that moves the player somewhere other than the adjacent cell
        ENDS the chunk. `step_dir` is satisfied by any movement at all, and a
        ledge jump travels TWO cells on one press -- so every direction after
        it in the same chunk was applied from a square the planner never chose.
        On Route 114, which is lined with ledges, that desync walked the run
        into corners it could not leave and cost minutes per attempt. Stopping
        early is free: `goto` re-plans from wherever we actually are.
        """
        for i, d in enumerate(path):
            before = self.pos()
            before_map = self.map_name()
            dx, dy = DIRS[d]
            nxt = self.nav.cell(before_map, before[0] + dx, before[1] + dy)
            if (d == "U" and nxt is not None
                    and nxt.behavior == nav_mod.WATERFALL
                    and self.is_surfing()):
                # A waterfall is not a step, it is a CLIMB, and the engine
                # only offers it while surfing and facing north.
                if not self.climb_waterfall():
                    self.last_step_reason = (
                        f"could not climb the waterfall at "
                        f"{(before[0], before[1] - 1)}: "
                        f"{self.last_field_reason}"
                    )
                    return False
                return True
            if (nxt is not None and self.nav.surfing
                    and self.nav._is_water(nxt) and not self.is_surfing()):
                # A land->water step is a MOUNT, not a walk: face the water,
                # press A, answer YES. Walking at the shore does nothing.
                if not self._mount_surf(d):
                    self.last_step_reason = (
                        f"could not mount SURF facing {d} at {before}"
                    )
                    return False
                continue
            if not self.step_dir(d):
                self.last_step_reason = (
                    f"{self.last_step_reason} (step {i + 1}/{len(path)} of {path})"
                )
                return False
            if self.map_name() != before_map:
                return True
            dx, dy = DIRS[d]
            if self.pos() != (before[0] + dx, before[1] + dy):
                # Not a failure -- a jump. The planner just needs to know.
                self.last_step_reason = (
                    f"moved {before} -> {self.pos()} stepping {d} "
                    f"(a ledge jump, not the adjacent cell); re-planning"
                )
                return True
        return True

    def goto(self, x, y, map_name=None, label="", on_battle="raise",
             max_replans=12):
        """Pathfind to (x, y) on the current map and walk it.

        Replans after every step-group, because an NPC can move into the path
        and because a warp can teleport us mid-walk.

        A wild encounter stops the walk, on the same terms as ``travel``: the
        player cannot move during a battle, so every remaining replan is spent
        pathing from a position that cannot change. Before this checked, a
        single step into Route 101's grass cost eleven seconds of futile
        replanning and then returned False with "replan-cap reached" -- the
        encounter, which is the whole point of walking in grass, was never
        mentioned.
        """
        self.last_goto_reason = None
        self._surf_sync()
        target_map = map_name or self.map_name()
        # A wild encounter is not a routing failure, and charging it against
        # the replan budget made long grassy walks fail for the wrong reason:
        # the walk to Meteor Falls' door crosses Route 114's grass, every
        # encounter ate one of the sixty attempts, and it gave up at (28,58)
        # reporting "replan-cap reached" as though no path existed. Only an
        # actual REPLAN counts against the cap.
        # The cap counts STALLS, not replans. A replan is the normal cost of
        # walking: chunks are six steps, so a 166-step route needs at least
        # twenty-eight of them, and each wild encounter adds one more. Charging
        # those against a stall budget made a legitimate journey fail for the
        # wrong reason -- the walk to the Weather Institute crossed eighty rows
        # of Route 119, spent all sixty attempts making real progress, and gave
        # up at (21,46) saying "replan-cap reached" as though the route were
        # broken. Positional progress resets the counter; a walk that moves
        # nowhere is the only thing that spends it.
        #
        # `rounds` is a backstop, not a policy: a walk that oscillates makes
        # progress by this measure forever, and `goto` called outside a journey
        # has no deadline to stop it.
        attempt = 0
        rounds = 0
        battles = 0
        # Clearing scenery is a WALK, so it costs a nested goto per rock side.
        # Retrying it on every round of this loop multiplies out to roughly ten
        # million frames: 144 rounds x 4 sides x a fresh 144-round inner goto.
        # That is two hours of a run that is "making progress" by every counter
        # here and visibly frozen on one tile -- Victory Road B1F (9,9), found
        # by the stall watchdog dumping this stack. Once per goto is enough:
        # if the road did not open the first time, it will not open on the
        # hundredth ask with nothing else changed.
        tried_clearing = False
        while attempt < max_replans and rounds < max_replans * 12:
            journey = getattr(self, "_journey_deadline", None)
            if journey is not None and _time.time() > journey:
                self.last_goto_reason = (
                    f"journey budget spent at {self.pos()} heading for "
                    f"{(x, y)}"
                )
                return False
            if self.in_battle():
                if on_battle == "fight":
                    self.fight()
                    self.advance_scene(40000)
                    battles += 1
                    if battles > 200:
                        self.last_goto_reason = (
                            f"{battles} battles heading for {(x, y)} -- "
                            "the walk is not making progress"
                        )
                        return False
                    continue
                raise TravelInterrupted(target_map, self.pos(), target_map)
            rounds += 1
            if self.map_name() != target_map:
                self.last_goto_reason = (
                    f"left {target_map} for {self.map_name()} mid-route"
                )
                return False
            here = self.pos()
            if here == (x, y):
                return True
            if self.scene_active():
                self.drain_scene(3000)
            self._mark_npcs(target_map)
            # A cell we are deliberately walking TO is not a wall. Meteor
            # Falls' cutscene lives on a coord_event at (14,18), `_mark_gates`
            # marked it like any other scripted cell, and find_path then had
            # no route to a destination it had just blocked -- so goto spent
            # its whole budget replanning without taking a step, instantly and
            # silently. `_mark_gates` already exempts the cell we are standing
            # on; the cell we are heading for needs the same exemption.
            self.nav.blocked.get(target_map, set()).discard((x, y))
            path = self.nav.find_path(target_map, here, (x, y), self.elevation())
            if path is None:
                # Distinguish "there is no way there" from "a person is
                # standing in it". NPCs wander, and two of them either side of
                # the player is a common, transient box -- Route 111 produced
                # exactly that, (18,101) and (19,100), and the run read it as
                # a permanent no-path and abandoned the journey to badge 4.
                #
                # A real player waits a second and walks around. So: if the
                # route exists once the wanderers are ignored, they are the
                # obstruction, and the right answer is to let them move.
                marks = self.nav.blocked.get(target_map)
                self.nav.blocked.pop(target_map, None)
                self.nav._reach_cache.clear()
                if self.nav.find_path(target_map, here, (x, y),
                                      self.elevation()) is not None:
                    # A route exists once live objects are ignored, so
                    # something is standing in it. Two very different things
                    # look identical here: a wanderer, who will move, and a
                    # breakable rock, which will not. Route 111's pair are
                    # rocks and were nearly diagnosed as trainers.
                    self._mark_npcs(target_map)
                    if target_map == self.map_name() and not tried_clearing:
                        tried_clearing = True
                        if self.clear_the_way((x, y)):
                            continue
                    self.settle(120)
                    continue
                # PUT THE MARKS BACK. This experiment is a question, not a
                # decision, and leaving the set empty on the way out handed
                # every later planner a map with no bodies on it: reach_cell
                # asked route_legs right after this returned False and was
                # given the trainer-occupied Lavaridge spring at (10,19) as
                # its first hop -- the exact hole the exclusion exists for.
                if marks:
                    self.nav.mark_blocked(target_map, marks)
                self.last_goto_reason = (
                    f"no-path from {here} to {(x, y)} on {target_map}"
                    f"{'; scene-active' if self.scene_active() else ''}"
                )
                return False
            if not path:
                return True
            # Walk a chunk, then re-plan: cheap insurance against a wanderer.
            chunk = path[:6]
            before = self.pos()
            if not self.walk(chunk):
                # A scene owning the input is not a wall, and marking the cell
                # ahead as blocked is exactly the wrong response: the map is
                # fine and the PLAYER is frozen. Route 114 drops the walker
                # onto (28,72) at elevation 5 with gPlayerAvatar.preventStep
                # set, and goto re-planned the identical six moves against a
                # player who could not take one -- twenty-four times, then a
                # replan-cap. Clear the scene and try again; only a genuine
                # refusal marks the cell.
                if "scene-owns-input" in (self.last_step_reason or ""):
                    self.advance_scene(40000)
                    self.settle(200)
                    continue
                self.nav.blocked.setdefault(target_map, set()).add(
                    self._ahead(chunk[-1])
                )
                attempt += 1
                continue
            # Ledges are ONE-WAY, so a wanderer does not merely lose ground --
            # it can lose the destination. Interrupted mid-chunk on Route 114
            # the walker dropped a ledge into (28,72), a cell with exactly ONE
            # forward-reachable cell (itself), and then spent 120 replans and
            # three minutes re-planning a route that had stopped existing.
            # Being trapped is not a routing retry; say so on the first pass.
            if self.pos() == before:
                attempt += 1
            else:
                attempt = 0
            if self.pos() != before and self.nav.find_path(
                    target_map, self.pos(), (x, y), self.elevation()) is None:
                room = len(self.nav.reachable(
                    target_map, self.pos(), self.elevation()))
                self.last_goto_reason = (
                    f"walked into a dead end at {self.pos()} heading for "
                    f"{(x, y)}: {room} cell(s) reachable from there and "
                    f"{(x, y)} is not one of them (one-way ledge)"
                )
                return False
        self.last_goto_reason = (
            f"stalled {attempt}x at {self.pos()} heading for {(x, y)}"
            if attempt >= max_replans else
            f"walked {rounds} chunks without arriving at {(x, y)} "
            f"(now {self.pos()})"
        )
        return False

    def _ahead(self, d):
        x, y = self.pos()
        dx, dy = DIRS[d]
        return (x + dx, y + dy)

    def _mark_gates(self, map_name):
        """Add coord_event cells the game will refuse to let us cross.

        Route 111's desert is the case: ten cells guarded on VAR_TEMP_3, and
        the script behind them checks for GO-GOGGLES and pushes the player
        back. Without this the pathfinder plans straight through a sandstorm
        it cannot enter, and the loop walks at it forever -- which is exactly
        what it did.

        `closed_gates` evaluates each script's own guard chain, so a gate
        whose condition is already satisfied does NOT block (Crystal's gotcha
        20: a spent ambush that blocks forever is its own bug).
        """
        try:
            from pokeagent.gates import GateReader

            closed = GateReader(self.state).closed_gates(map_name)
        except Exception as exc:  # noqa: BLE001 - a hint must not stop routing
            log.debug("gate marking unavailable: %s", exc)
            return
        here = self.pos()
        self.nav.mark_blocked(map_name, [
            (g.x, g.y) for g in closed
            if getattr(g, "x", None) is not None
            and getattr(g, "y", None) is not None
            and (g.x, g.y) != here
        ])

    def _mark_npcs(self, map_name):
        """Live NPC bodies block movement. Recomputed every plan, never
        cached -- a stale block severed Crystal's map three separate times
        (its journal #11, #66)."""
        self.nav.mark_blocked(map_name, (), replace=True)
        bodies = []
        base = self.emu.resolve("gObjectEvents")
        oe = cstruct.layout("ObjectEvent", "global.fieldmap.h")
        size = self.sym.size("gObjectEvents") // 16
        me = self.pos()
        for i in range(1, 16):
            raw = self.emu.read(base + i * size, size)
            if not (raw[oe["active"]] if "active" in oe else raw[0] & 1):
                continue
            cx = int.from_bytes(raw[oe["currentCoords"] : oe["currentCoords"] + 2], "little", signed=True)
            cy = int.from_bytes(raw[oe["currentCoords"] + 2 : oe["currentCoords"] + 4], "little", signed=True)
            cell = (cx - 7, cy - 7)
            if cell != me:
                bodies.append(cell)
        self.nav.mark_blocked(map_name, bodies)
        # Scripted refusals block just as hard as a body standing there.
        self._mark_gates(map_name)

    def reach_cell(self, x, y, map_name=None, on_battle="raise", max_legs=24):
        """Walk to (x, y) wherever it is: plan first, ask the game when planning fails.

        The planned router handles the common case in seconds. When it
        returns False -- drift, a body on a spring, a landing pad mistaken
        for a spring, any modelling error at all -- the savestate maze
        search takes over, because the emulator is the one transition
        function that cannot be wrong. Its answer is replayed on the real
        timeline.
        """
        target = (int(x), int(y))

        def arrived():
            return self.pos() == target and (
                map_name is None or self.map_name() == map_name
            )

        # VERIFY, do not trust. `_reach_cell_planned` returned True from inside
        # Mossdeep's gym while the player stood at (2,23) -- its own reason
        # string said "walked 360 chunks without arriving at (8,4) (now
        # (2,23))". A primitive that reports arrival it did not achieve poisons
        # everything above it: the badge chain believed it was beside the gym
        # leaders and spent its talk on empty floor.
        if self._reach_cell_planned(x, y, map_name=map_name,
                                    on_battle=on_battle, max_legs=max_legs):
            if arrived():
                return True
            self.last_goto_reason = (
                f"the planned walk claimed {target} but stopped at "
                f"{self.pos()}; escalating"
            )
        why = self.last_goto_reason
        # ROTATING GATES FIRST, when the map has them. They are invisible to
        # every static model -- not metatiles, not object events, so
        # `grid_drift` reads zero and nav happily plans through an arm -- and
        # Fortree's gym trapped the run completely: 205 cells "reachable",
        # Winona and the exit both among them, and the first step refused.
        # Walking into a gate rotates it, so the search has to be over gate
        # CONFIGURATIONS, which is what `solve_gate_maze` does.
        if self.gate_signature():
            if self.solve_gate_maze(x, y, map_name=map_name,
                                    on_battle=on_battle) and arrived():
                return True
        if self.solve_warp_maze(x, y, map_name=map_name,
                                on_battle=on_battle) and arrived():
            return True
        # A SWITCHED FLOOR is neither gates nor warps. Mossdeep's gym is 173
        # `MB_SLIDE_*` arrows: nav plans across them as if they were floor, the
        # walk "arrives" somewhere else entirely, and no warp table mentions
        # them. The same savestate search solves it once the node carries the
        # switch flags -- the emulator is the transition function, so a slide
        # chain nobody has modelled is simply followed.
        if self.switch_signature() and self.solve_gate_maze(
                x, y, map_name=map_name, on_battle=on_battle,
                signature=self.switch_signature, require_signature=False,
                extra_moves=("A",),
        ) and arrived():
            return True
        # A BOULDER ROOM is the same shape again: the obstruction moves, so the
        # node has to carry where it moved to. Strength must be on first or
        # every push is a wall -- and the flag dies with the map change.
        if self.boulder_signature():
            self.clear_rocks()
            self.use_strength()
            if self.solve_gate_maze(
                    x, y, map_name=map_name, on_battle=on_battle,
                    signature=self.boulder_signature,
                    require_signature=False,
            ) and arrived():
                return True
        self.last_goto_reason = self.last_goto_reason or why
        return False

    def _reach_cell_planned(self, x, y, map_name=None, on_battle="raise",
                            max_legs=24):
        """Walk to (x, y) even when the way there leaves the map and comes back.

        `goto` is single-map and single-component: it answers "walk here" and
        correctly says no-path when the target sits in a pocket. Some rooms
        are ONLY pockets. Lavaridge's gym floor drops the player through holes
        onto B1F and back up a ladder into a different part of 1F, and
        Flannery stands in a component the door cannot reach -- 36 walkable
        cells away and two floors round. `travel` cannot express it either,
        because start and destination are the same MAP and it answers "you are
        already there".

        So route to the CELL, take each leg, and finish with a plain goto.
        """
        target_map = map_name or self.map_name()
        for _ in range(max_legs):
            if self.in_battle():
                if on_battle != "fight":
                    raise TravelInterrupted(self.map_name(), self.pos(), target_map)
                self.fight()
                self.advance_scene(40000)
                continue
            if self.scene_active():
                self.advance_scene(40000)
            here, cell = self.map_name(), self.pos()
            if here == target_map and self.goto(
                    x, y, map_name=target_map, on_battle=on_battle,
                    max_replans=40):
                return True
            legs = self.nav.route_legs(here, cell, target_map, max_hops=40,
                                       dest_cell=(x, y))
            if not legs:
                self.last_goto_reason = (
                    f"no route to {(x, y)} on {target_map} from {here} {cell}"
                    f" -- even through warps"
                )
                return False
            # Execute the WHOLE plan, not just its first hop. Replanning after
            # every leg sounds safer and is the opposite in a hole-maze:
            # Lavaridge's gym is twenty warps between two floors, arrival
            # drift lands each hop a cell or two off the modelled spot, and a
            # fresh plan from the drifted spot need not agree with the old
            # one -- the walker bounced between floors until the leg budget
            # died. The plan is correct as a SEQUENCE; only abandon it when a
            # hop lands on a map the plan never named.
            followed = True
            for leg in legs:
                edge = leg["edge"]
                if self.in_battle():
                    if on_battle != "fight":
                        raise TravelInterrupted(
                            self.map_name(), self.pos(), target_map)
                    self.fight()
                    self.advance_scene(40000)
                if edge.get("kind") == "warp":
                    try:
                        took = self._enter_warp(edge["x"], edge["y"],
                                                on_battle=on_battle)
                    except TravelInterrupted:
                        if on_battle != "fight":
                            raise
                        self.fight()
                        self.advance_scene(40000)
                        followed = False
                        break
                    if not took:
                        followed = False
                        break
                elif not self._cross_seam(self.map_name(), edge, on_battle):
                    followed = False
                    break
                if self.map_name() != leg["to_map"]:
                    followed = False
                    break
            if followed:
                continue
            edge = legs[0]["edge"]
            if edge.get("kind") == "warp":
                # take_warp walks to the door with goto's default policy, and
                # a gym is full of trainers: the first one turned a routing
                # call into an exception the caller never asked for. A battle
                # on the way is the normal case here, not an error.
                try:
                    took = self.take_warp(edge["x"], edge["y"])
                except TravelInterrupted:
                    if on_battle != "fight":
                        raise
                    self.fight()
                    self.advance_scene(40000)
                    continue
                if not took:
                    self.last_goto_reason = (
                        f"could not take the warp at "
                        f"{(edge['x'], edge['y'])}: {self.last_warp_reason}"
                    )
                    return False
            elif not self._cross_seam(here, edge, on_battle):
                self.last_goto_reason = (
                    f"could not cross the {edge.get('direction')} seam "
                    f"heading for {(x, y)}"
                )
                return False
        self.last_goto_reason = f"leg budget exhausted heading for {(x, y)}"
        return False

    _GATE_MAPS = None

    @classmethod
    def gate_maps(cls) -> frozenset:
        """Maps whose own scripts run `special RotatingGate_InitPuzzle`.

        Read from the decomp rather than hardcoded, so a ROM or map change
        cannot leave this silently wrong; cached because it is static.
        """
        if cls._GATE_MAPS is None:
            found = set()
            root = paths.PRET / "data" / "maps"
            try:
                for script in root.glob("*/scripts.inc"):
                    try:
                        if "RotatingGate_InitPuzzle" in script.read_text(
                            errors="ignore"
                        ):
                            found.add(script.parent.name)
                    except OSError:
                        continue
            except Exception:  # noqa: BLE001 - no decomp: trust the count
                cls._GATE_MAPS = frozenset()
                return cls._GATE_MAPS
            cls._GATE_MAPS = frozenset(found)
            log.debug("[gates] gate maps: %s", sorted(found))
        return cls._GATE_MAPS

    #: Rooms whose layout is switched rather than walked. The flags are the
    #: state a search has to treat as part of the node.
    SWITCH_FLAGS = {
        "MossdeepCity_Gym": (
            "FLAG_MOSSDEEP_GYM_SWITCH_1", "FLAG_MOSSDEEP_GYM_SWITCH_2",
            "FLAG_MOSSDEEP_GYM_SWITCH_3", "FLAG_MOSSDEEP_GYM_SWITCH_4",
        ),
    }

    def switch_signature(self) -> tuple:
        """The current map's puzzle switches, as a tuple of booleans.

        Mossdeep's gym floor is 173 `MB_SLIDE_*` arrows and the four switches
        re-point them, so the same tile is a different place depending on the
        flags. A search over positions alone would call two genuinely different
        rooms the same node and prune the way out.
        """
        names = self.SWITCH_FLAGS.get(self.map_name(), ())
        out = []
        for n in names:
            try:
                out.append(bool(self.state.flag(n)))
            except Exception:  # noqa: BLE001 - unknown flag: treat as clear
                out.append(False)
        return tuple(out)

    def gate_signature(self) -> tuple:
        """The rotating gates' orientations, one byte each, or () if none.

        Fortree's gym and the Trick House are ROTATING GATE puzzles
        (`special RotatingGate_InitPuzzle`), and the gates are neither
        metatiles nor object events -- nothing in the block map or in
        gObjectEvents mentions them, so `grid_drift` reads zero and nav plans
        straight through an arm. Walking into one PUSHES it, which is the whole
        puzzle.

        The orientations are readable: `RotatingGate_GetGateOrientation` is
        `((u8 *)GetVarPointer(0x4000))[gateId]` (src/rotating_gate.c:653-660),
        one byte per gate at the base of the var block, and
        `gRotatingGate_PuzzleCount` says how many. That makes a search over
        gate configurations well founded instead of a guess.
        """
        # THE COUNT IS A GLOBAL AND IT DOES NOT RESET. `RotatingGate_InitPuzzle`
        # sets `gRotatingGate_PuzzleCount` when you enter a gate map and nothing
        # clears it when you leave, so after one visit to Fortree's gym it reads
        # 7 FOREVER -- on every route, in every cave. `goto` escalates to
        # `solve_gate_maze` when a walk fails and gates are present, so this
        # made the escalation fire everywhere: measured on Route 122,
        # "rotating gates: 4000 nodes explored without reaching (21,29)", which
        # is 4000 savestates and half an hour to walk into a mountain that has
        # no gates in it at all. It is also where the 1,288 leaked scratch
        # directories came from.
        #
        # Only two maps in the game run that special, and the decomp is the
        # authority on which (grep RotatingGate_InitPuzzle in
        # pret/data/maps/*/scripts.inc): FortreeCity_Gym and
        # Route110_TrickHousePuzzle6.
        if self.map_name() not in self.gate_maps():
            return ()
        try:
            n = self.emu.u8("gRotatingGate_PuzzleCount")
        except Exception:  # noqa: BLE001 - no puzzle on this map
            return ()
        if not n or n > 16:
            return ()
        try:
            base = self.state._sb1("vars")
            return tuple(self.emu.u8(base + i) for i in range(n))
        except Exception:  # noqa: BLE001
            return ()

    def solve_gate_maze(self, x, y, map_name=None, on_battle="fight",
                        max_nodes=4000, budget_s=480, signature=None,
                        require_signature=True, extra_moves=()) -> bool:
        """Reach (x, y) past ROTATING GATES by asking the game.

        Fortree's gym trapped the run completely: nav said both Winona at
        (4,1) and the exit warp at (2,24) were reachable across 205 cells with
        zero grid drift, and the very first step was refused -- a gate arm sits
        at (16,20), right beside where the walker stood. There is nothing to
        fix in the grid, because the gates are not in it.

        So the emulator is the transition function, exactly as for the warp
        maze. A node is (position, gate orientations); the moves are the four
        steps, because walking into a gate is how you rotate it. Breadth-first
        over savestates until the target is stood on, then the winning move
        sequence is REPLAYED on the real timeline.

        Bounded by nodes and wall clock: a puzzle this cannot crack must cost
        a known amount of time and then hand back control.
        """
        import shutil
        import tempfile

        target = (int(x), int(y))
        here = map_name or self.map_name()
        self.last_goto_reason = None
        # The SIGNATURE is what makes a node more than a position: two visits
        # to the same tile with the room in different states are different
        # nodes. Gates were the first such room; Mossdeep's gym is another,
        # where the state is the four switch flags rather than gate arms.
        signature = signature or self.gate_signature
        if require_signature and not signature():
            self.last_goto_reason = f"{here} has no rotating gates"
            return False

        deadline = _time.time() + budget_s
        tmp = tempfile.mkdtemp(prefix="gatemaze-")
        home = self.state_path
        try:
            root = os.path.join(tmp, "root.state")
            self.save(root)

            def node_key():
                # THE MAP IS PART OF THE NODE. Keyed on position alone, the search
                # walks out through a warp and calls a same-numbered tile on
                # another map "arrived": it reported reaching (8,4) while standing
                # in MossdeepCity_House1, because the gym has a door and (8,4)
                # exists on both sides of it.
                return (self.map_name(), self.pos(), signature())

            start = node_key()
            if start[1] == target and start[0] == here:
                return True
            # BEST-FIRST, not breadth-first. The state space is every cell times
            # every gate configuration, and plain BFS spent 500 nodes wandering the
            # south of Fortree's gym while Winona stood at the top. Expanding the
            # node CLOSEST to the target first turns that into a search that
            # actually arrives; the dedupe on (position, gates) is what keeps it
            # honest, because a gate config revisited is a real repeat.
            import heapq

            def far(pos):
                return abs(pos[0] - target[0]) + abs(pos[1] - target[1])

            seen = {start}
            counter = 0
            # (distance, tiebreak, statefile, moves)
            frontier = [(far(start[1]), 0, root, [])]
            nodes = 0
            winner = None
            while frontier and winner is None:
                _dist, _tb, statefile, moves = heapq.heappop(frontier)
                for mv in list("URDL") + list(extra_moves):
                    if _time.time() > deadline or nodes >= max_nodes:
                        frontier = []
                        break
                    self.load(statefile, adopt=False)
                    if self.in_battle():
                        if on_battle == "fight":
                            self.fight()
                        else:
                            continue
                    before = self.pos()
                    if mv == "A":
                        # PRESSING IS A MOVE. Mossdeep's arrows are re-pointed by
                        # four switches that are `bg_events` of type "sign" -- you
                        # face them and press A. With only the four steps in the
                        # move set the search explored 7,480 nodes and could not
                        # solve the room, because no amount of walking flips a
                        # switch. The node signature already carries the flags, so
                        # a press that changes one is simply a new node.
                        self.emu.run_sequence("A:4 .:24")
                        self.settle(60)
                        self.advance_scene(20000)
                        self.sync_grid()
                    else:
                        self.step_dir(mv)
                        self.settle(20)
                    key = node_key()
                    nodes += 1
                    if key in seen:
                        continue
                    seen.add(key)
                    child = os.path.join(tmp, f"n{nodes}.state")
                    self.save(child)
                    path = moves + [mv]
                    if key[1] == target and key[0] == here:
                        winner = path
                        break
                    # A step that neither moved us nor turned a gate is a wall;
                    # keep it out of the frontier rather than re-expanding it.
                    if key[1] != before or key[2] != start[2] or key[0] != here:
                        counter += 1
                        heapq.heappush(
                            frontier, (far(key[1]), counter, child, path)
                        )
            log.info("[gates] explored %d nodes; %s", nodes,
                     f"solved in {len(winner)} steps" if winner else "no solution")
            self.load(root, adopt=False)
            if winner is None:
                self.last_goto_reason = (
                    f"rotating gates: {nodes} nodes explored without reaching "
                    f"{target} on {here}"
                )
                return False
            for mv in winner:
                if self.in_battle() and on_battle == "fight":
                    self.fight()
                if mv == "A":
                    self.emu.run_sequence("A:4 .:24")
                    self.settle(60)
                    self.advance_scene(20000)
                    self.sync_grid()
                else:
                    self.step_dir(mv)
                    self.settle(20)
            ok = self.pos() == target and self.map_name() == here
            if not ok:
                self.last_goto_reason = (
                    f"the solution replayed to {self.map_name()} {self.pos()}, "
                    f"not {here} {target}"
                )
            return ok
        finally:
            self.state_path = home
            # Node and time limits must also bound scratch disk usage.
            shutil.rmtree(tmp, ignore_errors=True)

    def solve_warp_maze(self, x, y, map_name=None, on_battle="fight",
                        max_nodes=48, budget_s=600) -> bool:
        """Reach (x, y) through a same-map warp maze by ASKING THE GAME.

        Lavaridge's gym is twenty springs between two floors, and every
        static model of it failed somewhere: arrival drift, a trainer parked
        on a spring, landing pads that look like springs. The emulator is the
        one transition function with no model error, so this searches over
        savestates: from each state, step on every reachable in-maze spring,
        save the result, and breadth-first until the target cell is reachable
        on foot. The found hop sequence is then REPLAYED on the real
        timeline. Solved the real gym in 15 nodes / 10 hops.

        Candidate springs are passable warp tiles with a NONZERO behavior
        (landing pads are behavior 0 and never fire) whose destination stays
        inside the maze -- the start map and the target map. Doors are solid
        and excluded by passability.
        """
        import hashlib
        import shutil
        import tempfile

        target_map = map_name or self.map_name()
        # The maze is the CLOSURE of contact-warp destinations, not just the
        # two maps someone happened to name: Lavaridge's springs all point at
        # a floor that is neither the start nor the target argument when the
        # caller is already standing on 1F. Expand until no passable nonzero
        # warp leads anywhere new (capped -- a gym is two floors, not a
        # region).
        maze_maps = {target_map, self.map_name()}
        for _ in range(4):
            grew = False
            for m in tuple(maze_maps):
                try:
                    info = self.nav.info(m)
                except Exception:  # noqa: BLE001
                    continue
                for w in info.warps:
                    c = self.nav.cell(m, w.x, w.y)
                    if c is None or not c.passable or not c.behavior:
                        continue
                    dest = self.nav.const_to_name(w.dest_map)
                    if dest and dest not in maze_maps:
                        maze_maps.add(dest)
                        grew = True
            if not grew or len(maze_maps) >= 6:
                break
        t0 = _time.time()
        tmp = tempfile.mkdtemp(prefix="warpmaze")
        # Cleaned in the `finally` at the end of this method; see the note in
        # solve_gate_maze about a search that was bounded everywhere but disk.

        def unfreeze():
            for _ in range(6):
                if not self.scene_active():
                    break
                self.advance_scene(20000)
                self.settle(200)
            self.settle(150)

        #: Mossdeep's gym teleporters are METATILES, not warp_events. The map
        #: declares exactly two warps -- both the exit -- while stepping on
        #: behaviour 0x0E moves you across the room: measured, one LEFT press
        #: from (2,22) landed on (8,17). `MB_MOSSDEEP_GYM_WARP` is
        #: pret/include/constants/metatile_behaviors.h:18, and reading the
        #: grid for it is the only way to see these at all.
        MB_MOSSDEEP_GYM_WARP = 0x0E

        def springs_here():
            m = self.map_name()
            r = self.nav.reachable(m, self.pos(), self.elevation())
            out = []
            for w in self.nav.info(m).warps:
                c = self.nav.cell(m, w.x, w.y)
                dest = self.nav.const_to_name(w.dest_map)
                if (c is not None and c.passable and c.behavior
                        and dest in maze_maps and (w.x, w.y) in r):
                    out.append((w.x, w.y))
            # Behaviour-driven teleporters, which no warp table mentions.
            try:
                grid = self.nav.grid(m)
            except Exception:  # noqa: BLE001 - no grid, no extra springs
                return out
            for yy, row in enumerate(grid):
                for xx, cell in enumerate(row):
                    if cell is None or not cell.passable:
                        continue
                    if cell.behavior != MB_MOSSDEEP_GYM_WARP:
                        continue
                    if (xx, yy) in r and (xx, yy) not in out:
                        out.append((xx, yy))
            return out

        def arrived():
            if self.map_name() != target_map:
                return False
            r = self.nav.reachable(target_map, self.pos(), self.elevation())
            return (x, y) in r or any(
                (x + dx, y + dy) in r
                for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0))
            )

        def key_now():
            r = sorted(self.nav.reachable(
                self.map_name(), self.pos(), self.elevation()))
            return (self.map_name(),
                    hashlib.md5(repr(r).encode()).hexdigest())

        # `load()` REPOINTS state_path, and this search loads dozens of temp
        # forks. Leaking that pointer sent the play loop's periodic
        # `d.save()` into /tmp for the rest of the session: the working state
        # stopped advancing the moment the solver first ran, and a later
        # restart replayed from two badges earlier. The search is a question
        # asked of a scratch timeline; it must leave the real one alone.
        home = self.state_path
        try:
            start = f"{tmp}/s0.state"
            self.save(start)
            frontier = [(start, [])]
            seen: set = set()
            found = None
            n = 0
            while frontier and found is None and len(seen) < max_nodes \
                    and _time.time() - t0 < budget_s:
                state, hops = frontier.pop(0)
                self.load(state, adopt=False)
                unfreeze()
                if arrived():
                    found = hops
                    break
                k = key_now()
                if k in seen:
                    continue
                seen.add(k)
                for sp in springs_here():
                    self.load(state, adopt=False)
                    unfreeze()
                    before = (self.map_name(), self.pos())
                    try:
                        self.goto(*sp, map_name=self.map_name(),
                                  on_battle=on_battle, max_replans=25)
                    except TravelInterrupted:
                        if on_battle != "fight":
                            raise
                        self.fight()
                    unfreeze()
                    if (self.map_name(), self.pos()) == before:
                        continue
                    n += 1
                    st = f"{tmp}/s{n}.state"
                    self.save(st)
                    frontier.append((st, hops + [sp]))
            if found is None:
                self.load(start, adopt=False)
                unfreeze()
                self.last_goto_reason = (
                    f"warp-maze search exhausted ({len(seen)} states) heading "
                    f"for {(x, y)} on {target_map}"
                )
                return False
            log.info("[maze] solved in %d states / %d hops: %s",
                     len(seen), len(found), found)
            # Replay on the real timeline.
            self.load(start, adopt=False)
            unfreeze()
            for sp in found:
                try:
                    self.goto(*sp, map_name=self.map_name(),
                              on_battle=on_battle, max_replans=30)
                except TravelInterrupted:
                    if on_battle != "fight":
                        raise
                    self.fight()
                unfreeze()
            return self.goto(x, y, map_name=target_map, on_battle=on_battle,
                             max_replans=30) or arrived()
        finally:
            self.state_path = home
            shutil.rmtree(tmp, ignore_errors=True)

    def can_dive(self) -> bool:
        """DIVE is usable: badge 7 held and a party member knows it."""
        try:
            return bool(self.state.flag("FLAG_BADGE07_GET")) and \
                bool(self.field_moves().get("DIVE"))
        except Exception:  # noqa: BLE001
            return False

    def can_waterfall(self) -> bool:
        """WATERFALL is usable: badge 8 held and a party member knows it."""
        try:
            return bool(self.state.flag("FLAG_BADGE08_GET")) and \
                bool(self.field_moves().get("WATERFALL"))
        except Exception:  # noqa: BLE001
            return False

    def underwater(self) -> bool:
        """Is this map MAP_TYPE_UNDERWATER? Decides dive vs emerge."""
        return self.map_name().startswith("Underwater")

    def close_menus(self) -> bool:
        """Back out of whatever box is open, with B only.

        Every field-move refusal leaves a message box up ("Can't use that
        here"), and an open box eats all movement input. Both `dive()` and
        `climb_waterfall()` already called this on their failure paths -- it
        just did not exist, so the first refused surface raised
        AttributeError instead of recovering. That is proof those paths had
        never run: `dive()` had only ever been called where it worked.
        """
        from pokeagent.menus import Menus

        return Menus(self.emu, self.state).close()

    def dive(self) -> bool:
        """Dive or surface, whichever this map calls for.

        `TrySetupDiveDownScript` / `TrySetupDiveEmergeScript`
        (src/field_control_avatar.c:519-537) both act on the tile the player
        is STANDING on -- not the faced tile -- and both are reached by the A
        button. Going down needs `MetatileBehavior_IsDiveable`; coming up
        needs an underwater map and a surfacable ceiling. Refuses before
        pressing, with the reason on `last_field_reason`.
        """
        self.last_field_reason = None
        if not self.can_dive():
            self.last_field_reason = (
                "no-knower" if self.state.flag("FLAG_BADGE07_GET")
                else "no-badge"
            )
            return False
        here = self.map_name()
        cell = self.nav.cell(here, *self.pos())
        if cell is None:
            self.last_field_reason = "no-cell"
            return False
        going_up = self.underwater()
        if going_up:
            if cell.behavior in nav_mod.NO_SURFACING:
                self.last_field_reason = "no-surfacing-here"
                return False
        elif cell.behavior not in nav_mod.DIVEABLE:
            self.last_field_reason = "wrong-tile"
            return False
        # DOWN IS A, UP IS B. `TrySetupDiveEmergeScript` is gated on
        # `input->pressedBButton` (src/field_control_avatar.c:233), while the
        # descent hangs off the A handler at :521. Pressing A underwater does
        # nothing at all, which is why every surface attempt in the Seafloor
        # Cavern reported "pressed A but the map did not change" while standing
        # on a perfectly surfacable tile. Same shape as Crystal's gotcha 19.
        key = "B" if going_up else "A"
        self.emu.run_sequence(f"{key}:4 .:40")
        self.advance_scene(60000)
        self.settle(600)
        if self.map_name() != here:
            self.settle(300)
            return True
        self.close_menus()
        self.last_field_reason = f"pressed {key} but the map did not change"
        return False

    def climb_waterfall(self) -> bool:
        """Climb the waterfall we are facing.

        `GetInteractedWaterScript` (src/field_control_avatar.c:503-517) is
        strict in a way worth encoding: badge 8, the FACED tile must be
        MB_WATERFALL, and `IsPlayerSurfingNorth()` -- you must already be
        surfing AND facing north. Anything else runs
        S_CannotUseWaterfall, which leaves a message box open (Crystal's
        gotcha 17), so every refusal path closes menus.
        """
        self.last_field_reason = None
        if not self.can_waterfall():
            self.last_field_reason = (
                "no-knower" if self.state.flag("FLAG_BADGE08_GET")
                else "no-badge"
            )
            return False
        if not self.is_surfing():
            self.last_field_reason = "not-surfing"
            return False
        x, y = self.pos()
        faced = self.nav.cell(self.map_name(), x, y - 1)
        if faced is None or faced.behavior != nav_mod.WATERFALL:
            self.last_field_reason = "wrong-tile"
            return False
        before = (self.map_name(), self.pos())
        self.emu.run_sequence(f"{_HOLD['U']}:6 .:16 A:4 .:40")
        self.advance_scene(60000)
        self.settle(600)
        if (self.map_name(), self.pos()) != before:
            return True
        self.close_menus()
        self.last_field_reason = "pressed A facing north but nothing moved"
        return False

    def can_surf(self) -> bool:
        """SURF is usable: badge 5 held and a party member knows the move."""
        try:
            return bool(self.state.flag("FLAG_BADGE05_GET")) and \
                bool(self.field_moves().get("SURF"))
        except Exception:  # noqa: BLE001
            return False

    def _surf_sync(self) -> None:
        """Tell nav which field moves are available right now.

        Every one of these turns a wall into a road, so the planner must be
        told before it plans -- and told again when the party changes, which
        is why this runs per journey rather than once at startup.
        """
        dirty = False
        for attr, live in (("surfing", self.can_surf()),
                           ("waterfall", self.can_waterfall())):
            if getattr(self.nav, attr) != live:
                setattr(self.nav, attr, live)
                dirty = True
        if dirty:
            self.nav._reach_cache.clear()

    def is_surfing(self) -> bool:
        """PLAYER_AVATAR_FLAG_SURFING (0x8) from the live avatar."""
        try:
            return bool(self.emu.u8("gPlayerAvatar") & 0x8)
        except Exception:  # noqa: BLE001
            return False

    def _mount_surf(self, d) -> bool:
        """Face the water and start surfing: A, then YES to the prompt.

        The overworld A handler dispatches on the FACED tile (the same
        engine path as waterfall/whirlpool -- Crystal gotcha 19: never the
        party menu).
        """
        self.emu.run_sequence(f"{_HOLD[d]}:6 .:16")
        self.emu.run_sequence("A:4 .:40")
        self.settle(300)
        if self.choice_open():
            self.resolve_choice("YES")
        self.advance_scene(30000)
        self.settle(500)
        return self.is_surfing()

    def _enter_warp(self, x, y, on_battle="raise") -> bool:
        """Fire a warp by whichever mechanism its tile actually uses.

        Doors are SOLID and fire on the entering step -- that is take_warp's
        choreography. But contact warps are ordinary WALKABLE tiles that fire
        the moment you stand on them: Lavaridge's springs (0x68/0x69), cracked
        floors, Mt. Pyre's holes. Walking onto one with plain goto is the
        entire job, and the mid-walk map change goto reports as "left the map"
        is the success condition. Applying door choreography to a spring
        stalls: enter, stand, nothing -- because standing IS the trigger and
        we were already teleported... except when an NPC body or approach
        geometry made take_warp's entry never complete.
        """
        here = self.map_name()
        cell = self.nav.cell(here, x, y)
        if cell is not None and cell.passable:
            self.goto(x, y, map_name=here, on_battle=on_battle,
                      max_replans=20)
            if self.map_name() != here:
                self.settle(300)
                return True
            # Standing on it and still here: not a contact warp after all
            # (or the script needs a held direction); fall through to the
            # door choreography, which also handles walk-off edges.
        if self.take_warp(x, y):
            return True
        # Third mechanism: an A-PRESS script door. Petalburg's gym rooms are
        # bg_events sitting on the warp cells -- SpeedRoomDoor and friends --
        # and neither walking at them nor door choreography moves a script
        # that only answers the A button. Face the door, press A, let the
        # script slide it open, and try the entering step once more.
        if self._bg_script_at(here, x, y):
            px, py = self.pos()
            dx, dy = x - px, y - py
            face = {(0, -1): "U", (0, 1): "D", (-1, 0): "L", (1, 0): "R"}.get(
                (dx, dy))
            if face is None:
                for d_, (ddx, ddy) in DIRS.items():
                    if self.goto(x - ddx, y - ddy, map_name=here,
                                 max_replans=15, on_battle=on_battle):
                        face = d_
                        break
            if face is not None:
                self.emu.run_sequence(f"{_HOLD[face]}:6 .:20 A:4 .:60")
                self.advance_scene(40000)
                self.settle(400)
                before = self.map_name()
                self.emu.run_sequence(f"{_HOLD[face]}:{WARP_HOLD}")
                self.settle(600)
                if self.map_name() != before:
                    self.settle(300)
                    return True
                # Some door scripts warp the player themselves.
                if self.map_name() != here or self.pos() == (x, y):
                    return True
        return False

    def _bg_script_at(self, map_name, x, y) -> str | None:
        """The bg_event script on a cell, or None. Cached per map."""
        import json as _json
        cache = getattr(self, "_bg_cache", None)
        if cache is None:
            cache = self._bg_cache = {}
        if map_name not in cache:
            table = {}
            try:
                j = _json.loads(
                    (paths.MAPS / map_name / "map.json").read_text())
                for b in j.get("bg_events", ()):
                    if b.get("script"):
                        table[(int(b["x"]), int(b["y"]))] = b["script"]
            except Exception:  # noqa: BLE001 - no json means no doors
                pass
            cache[map_name] = table
        return cache[map_name].get((x, y))

    #: Feed name for a save that is not the main one; `live-run.state` is what
    #: the widget watches, so it keeps the name the widget expects.
    FEED_ALIASES = {"live-run": "default"}

    def heartbeat(self, msg) -> None:
        """Tell the watcher the run is THINKING, not hung.

        Route planning is pure Python: the emulator does not tick, so the feed
        publishes nothing and the widget holds its last frame. Measured on the
        way to Mossdeep, that gap reached 137 seconds, and from outside a
        137-second-old picture is indistinguishable from a crash -- which is
        how "it hasn't moved in over 5000 seconds" gets reported about a run
        that is working.

        A note costs nothing and reaches the panel's narration line, so long
        silences now say what they are doing.
        """
        feed = getattr(self, "feed", None)
        if feed is None:
            return
        try:
            feed.note(msg, src="nav")
            feed.publish()
        except Exception as exc:  # noqa: BLE001 - a dead widget never stops a run
            log.debug("heartbeat: %s", exc)

    def _warp_dest(self, map_name, x, y):
        """Where the warp at (x, y) leads, per the map's own warp_events."""
        try:
            for e in self.nav.exits(map_name):
                if e.get("kind") == "warp" and int(e.get("x", -1)) == int(x) \
                        and int(e.get("y", -1)) == int(y):
                    return e.get("dest")
        except Exception:  # noqa: BLE001 - unreadable map: do not block the warp
            return None
        return None

    def _autofeed(self, state_path):
        """Attach a LiveFeed for a save under `paths.SAVES_DIR`, or None.

        Deliberately narrow: only real saves publish, so the unit and
        integration lanes -- which fork milestones into temp directories --
        stay silent and fast.
        """
        try:
            path = Path(state_path).resolve()
            if paths.SAVES_DIR.resolve() not in path.parents:
                return None
            stem = path.name.split(".")[0]
            name = self.FEED_ALIASES.get(stem, stem)
            from pokeagent.live import LiveFeed

            feed = LiveFeed(name).attach(self)
            log.info("[live] publishing %s to live/%s.png", path.name, name)
            return feed
        except Exception as exc:  # noqa: BLE001 - a dead widget never stops a run
            log.debug("autofeed: %s", exc)
            return None

    def take_warp(self, x, y, on_battle="fight"):
        """Step ONTO a warp tile from an adjacent cell, holding the key.

        Standing on a warp does not fire it -- the warp triggers on the step
        that enters the tile, and the direction must still be held when that
        step completes. Both are true in Gen 3 as they were in Gen 2
        (Crystal gotchas 12 and 15).
        """
        self.last_warp_reason = None
        # TELL NAV ABOUT SURF FIRST. `take_warp` plans its own approach route,
        # so it needs the same knowledge `goto` and `travel` sync per journey --
        # and without it every water approach is invisible. Mt Pyre's door is
        # the case that found this: Route 122 is a sea route, the door at
        # (22,29) has exactly ONE open neighbour (22,30) at elevation 3, and
        # reaching it means surfing to (22,32) and dismounting north onto the
        # shore. With `nav.surfing` still False that dismount is not a legal
        # step, so every approach came back unreachable and the chain reported
        # "no approach to warp (22,29) on Route122 fired a map change" while
        # sitting on water 44 steps away. Synced, the same query answers with a
        # path immediately.
        self._surf_sync()
        before_map = self.map_name()
        for d, (dx, dy) in DIRS.items():
            approach = (x - dx, y - dy)
            if self.nav.cell(before_map, *approach) is None:
                continue
            if self.pos() != approach:
                # FIGHT THE WAY THERE. `goto` defaults to RAISING on a wild
                # encounter, and this passed no policy -- so on a sea route
                # full of them, one Tentacool ended the caller outright:
                # "TravelInterrupted: a battle started at Route122 (6, 27)
                # while travelling to Route122" killed a badge-chain script
                # that had already walked most of the way to Mt Pyre's door.
                # A warp approach is not a journey the caller can resume
                # halfway, so it resolves its own interruptions by default.
                try:
                    if not self.goto(*approach, map_name=before_map,
                                     on_battle=on_battle):
                        continue
                except TravelInterrupted:
                    if on_battle != "fight":
                        raise
                    self.fight()
                    if not self.goto(*approach, map_name=before_map,
                                     on_battle=on_battle):
                        continue
            self.emu.run_sequence(f"{_HOLD[d]}:{WARP_HOLD}")
            self.settle(600)
            if self.map_name() != before_map:
                self.settle(300)
                # ANY map change used to count as success, and that is a false
                # positive waiting to happen: called with Slateport's harbour
                # door while the player had drifted onto Route 134, a step
                # across an ordinary map CONNECTION reported the warp taken.
                # When the map data names where this warp goes, hold it to that.
                want = self._warp_dest(before_map, x, y)
                if want and self.map_name() != want:
                    self.last_warp_reason = (
                        f"stepping {d} from {approach} left {before_map} for "
                        f"{self.map_name()}, not the warp's {want}"
                    )
                    return False
                return True
            # Some warps are edges, not doors: Mt. Chimney's Jagged Pass
            # descent at (20,41) does nothing when ENTERED -- the player just
            # stands on it -- and fires on the NEXT step in the same
            # direction, walking off the edge. Entry-only meant the run could
            # ride up the mountain and never come down. If entering left us
            # standing on the tile, follow through.
            if self.pos() == (x, y):
                self.emu.run_sequence(f"{_HOLD[d]}:{WARP_HOLD}")
                self.settle(600)
                if self.map_name() != before_map:
                    self.settle(300)
                    return True
        # ROTATING GATES sit between the player and the door, and nothing in
        # the grid says so, so `goto` to every approach cell "succeeds" at
        # planning and fails at walking. Fortree's gym locked the run IN: it
        # could not reach Winona and could not reach the exit at (2,24)
        # either, and reported the same line four times a minute.
        #
        # The savestate search is the only thing that can answer a gate, so
        # try it on each approach and then take the door normally. Guarded on
        # `_warp_gate_retry` because `solve_gate_maze` itself walks, and a
        # walk that ends on a warp can re-enter here.
        if self.gate_signature() and not getattr(self, "_warp_gate_retry", False):
            self._warp_gate_retry = True
            try:
                for d, (dx, dy) in DIRS.items():
                    approach = (x - dx, y - dy)
                    if self.nav.cell(before_map, *approach) is None:
                        continue
                    if self.pos() != approach and not self.solve_gate_maze(
                            *approach, map_name=before_map):
                        continue
                    self.emu.run_sequence(f"{_HOLD[d]}:{WARP_HOLD}")
                    self.settle(600)
                    if self.map_name() != before_map:
                        self.settle(300)
                        return True
            finally:
                self._warp_gate_retry = False
        self.last_warp_reason = (
            f"no approach to warp ({x},{y}) on {before_map} fired a map change"
        )
        return False

    def travel(self, dest_map, max_legs=40, on_battle="raise", budget_s=None):
        """Cross maps: route over the warp/connection graph, leg by leg.

        Grass routes interrupt. A wild encounter mid-journey used to surface
        as "could not cross the U seam", because the scene lock made goto
        refuse and nothing said why. Now a scene is advanced before each leg
        and a battle stops the journey explicitly.
        """
        # A leg that "succeeds" without moving anywhere burns the budget in
        # silence: the Route 114 probe spent all forty legs oscillating and
        # reported only "gave up after 40 legs", which names the symptom and
        # hides the cause. Count each transition actually attempted; the third
        # identical one is a cycle, not bad luck, and it says so.
        self._surf_sync()
        self.heartbeat(f"planning a route to {dest_map}")
        # A WALL-CLOCK bound, because a leg count does not bound TIME: one leg
        # across Route 119 is a wall of grass, every step starts a battle, and
        # the whole journey ran for over four hundred seconds inside a single
        # `step()`. Nothing saved, no heartbeat published, and the stall
        # watchdog cannot see inside a call that never returns -- so a slow
        # journey looked exactly like a wedged one for seventeen minutes.
        # Returning False mid-journey is not failure: travel re-plans from
        # wherever it stopped, and the caller gets to save and re-evaluate
        # first.
        deadline = None if budget_s is None else _time.time() + budget_s
        # Publish it: the budget was only checked BETWEEN legs, and the wedge
        # is inside one -- `goto` will fight up to two hundred battles before
        # giving up, which across Route 119's grass is half an hour without
        # ever returning to the loop that was watching the clock. Every
        # nested walk checks this.
        prev_deadline = getattr(self, "_journey_deadline", None)
        if prev_deadline is not None:
            deadline = (prev_deadline if deadline is None
                        else min(deadline, prev_deadline))
        self._journey_deadline = deadline
        try:
            attempts: dict[tuple[str, str], int] = {}
            #: One step-outside per journey. Retrying it would loop on a building
            #: whose door genuinely does not help.
            stepped_out = False
            for _leg in range(max_legs):
                self.heartbeat(f"leg {_leg + 1} to {dest_map} from "
                               f"{self.map_name()} {self.pos()}")
                if deadline is not None and _time.time() > deadline:
                    self.last_goto_reason = (
                        f"travel budget spent at "
                        f"{self.map_name()} {self.pos()} heading for {dest_map}; "
                        f"resuming next cycle"
                    )
                    log.info("[travel] %s", self.last_goto_reason)
                    return False
                if self.in_battle():
                    if on_battle == "fight":
                        self.fight()
                        self.advance_scene(40000)
                    else:
                        raise TravelInterrupted(self.map_name(), self.pos(), dest_map)
                if self.scene_active():
                    self.advance_scene(40000)
                here = self.map_name()
                if here == dest_map:
                    return True
                # Reachability-aware: the exit has to be one we can WALK to. Route
                # 104's northern seam to Rustboro is real, listed, and unusable
                # from its southern half -- the two halves only meet through
                # Petalburg Woods -- so map-level routing planned that seam and the
                # journey failed twelve times with nothing to say but "could not
                # cross the U seam". Re-planned every leg, so a warp that lands
                # somewhere unexpected is corrected on the next pass.
                # A long way round can be many hops -- Route 112's southern half
                # reaches Rustboro only via Mauville, Verdanturf and Rusturf
                # Tunnel, which is seven -- and the old 40-hop default quietly
                # returned nothing for journeys that were perfectly possible.
                # Mark the live bodies on THIS map before the first plan, not just
                # after it. `mark_blocked` is what invalidates the reachability
                # cache, so skipping it here meant the opening plan was drawn
                # against whatever a previous `goto` had cached -- and if an NPC
                # had been standing in a doorway at that moment, the cached answer
                # said the exit did not exist. The plan came back None, travel
                # raised "no walkable route", and the marking below -- which would
                # have fixed it -- was inside the `if legs:` branch it never
                # reached.
                #
                # Live on a brand-new save: Littleroot's north seam to Route 101,
                # every gate variable satisfied and the connection right there in
                # exits(), reported unreachable until the cache was cleared.
                self._mark_npcs(here)
                legs = self.nav.route_legs(here, self.pos(), dest_map,
                                           max_hops=80, deadline=deadline)
                # Plan, then LEARN, then re-plan. `_mark_npcs` only knows the map
                # that is loaded, so the first plan is drawn on maps whose shut
                # gates are invisible -- and Route 111's desert is shut. The
                # router therefore proposed Mauville -> Route 111 -> Route 113,
                # walked into the sandstorm, failed, and proposed it again: three
                # identical legs and a loop. Marking the gates on every map the
                # plan names costs one pass over already-parsed script data and
                # reroutes around the desert via Fiery Path, which is the road the
                # game actually intends.
                # LIVE BODIES BEFORE PLANNING, not just shut gates.
                #
                # `goto` marks NPCs every pass and `mark_blocked` invalidates the
                # reachability cache, but travel marked only gates -- so it planned
                # against whatever the cache still held from the last goto. If an
                # NPC had been standing in a doorway then, the cached answer said
                # the exit did not exist, and travel reported "no walkable route"
                # for a road that was wide open.
                #
                # Caught on a brand-new save: Littleroot's north seam to Route 101,
                # every gate variable already satisfied (INTRO_STATE 8,
                # LITTLEROOT_STATE 1) and the connection right there in exits().
                # route_legs said None; marking NPCs (which clears the cache) made
                # the same call return a one-leg plan and the walk succeeded
                # immediately. NPCs wander, so a reachability answer that outlives
                # them is not an answer.
                if legs:
                    for leg in legs:
                        self._mark_gates(leg["to_map"])
                    self._mark_gates(here)
                    self._mark_npcs(here)
                    legs = self.nav.route_legs(
                        here, self.pos(), dest_map, max_hops=80,
                        deadline=deadline)
                if legs:
                    edge = legs[0]["edge"]
                    nxt = legs[0]["to_map"]
                elif not stepped_out and not self.flight.flyable_here():
                    # INDOORS IS NOT NOWHERE. `route_legs` plans over the warp and
                    # seam graph, and from inside a building the only edge is the
                    # door -- which it will not cross to reach a destination that
                    # is several maps beyond it. So a story step asked for from
                    # a lab, a Centre or a shop answers "no walkable route" and
                    # the caller gives up on the DESTINATION rather than on the
                    # building.
                    #
                    # That is exactly how a fresh run stalled: the very first
                    # objective is "beat the rival on Route 103", it was issued
                    # while standing in Birch's lab, and it failed eight times
                    # with "no walkable route from
                    # LittlerootTown_ProfessorBirchsLab to Route103". Eight
                    # failures tripped the sticky give-up, the loop fell through
                    # to training, and it then ground 240 battles on Route 101
                    # taking its starter from L5 to L20 without ever advancing the
                    # story. The same trap caught the collector at Devon Corp 2F.
                    #
                    # `step_outside` already knows how to leave a building; it was
                    # only ever wired into Fly. Walk out and re-plan ONCE.
                    stepped_out = True
                    if self.flight.step_outside():
                        log.info("travel: stepped outside to %s to plan for %s",
                                 self.map_name(), dest_map)
                        continue
                    raise TravelError(
                        f"no walkable route from {here} to {dest_map}: indoors "
                        f"and could not step outside{self._gate_hint(here)}"
                    )
                else:
                    # NO graph fallback. The map graph knows which maps touch;
                    # it does not know which of them can be REACHED from where the
                    # player is standing, and a map's halves are often separate
                    # places. Route 112 is split by the mountain, so the graph
                    # cheerfully proposed crossing north into Route 113 from a
                    # component with no northern border at all -- and travel tried
                    # it 318 times, because a plan that cannot be walked fails
                    # exactly the same way each attempt.
                    #
                    # route_legs already answers the real question. When it says
                    # no, that is the answer.
                    raise TravelError(
                        f"no walkable route from {here} to {dest_map}"
                        f"{self._gate_hint(here)}"
                    )
                seen = attempts[(here, nxt)] = attempts.get((here, nxt), 0) + 1
                if seen >= 3:
                    loop = " -> ".join(m for m, _ in sorted(
                        attempts, key=lambda k: -attempts[k])[:4])
                    raise TravelError(
                        f"stuck in a loop heading for {dest_map}: tried "
                        f"{here} -> {nxt} {seen} times without progress "
                        f"(cycling {loop}){self._gate_hint(here)}"
                    )
                if edge["kind"] == "dive":
                    # A vertical seam: walk to a diveable tile, then press A.
                    gate = edge.get("cross_at")
                    if gate and self.pos() != tuple(gate):
                        if not self.goto(*gate, map_name=here,
                                         on_battle=on_battle, max_replans=40):
                            raise TravelError(
                                f"could not reach the dive gate {tuple(gate)} on "
                                f"{here}: {self.last_goto_reason}"
                            )
                    if not self.dive():
                        raise TravelError(
                            f"could not dive to {nxt} from {self.pos()}: "
                            f"{self.last_field_reason}"
                        )
                    continue
                if edge["kind"] == "warp":
                    if not self.take_warp(edge["x"], edge["y"]):
                        # Jagged Pass's exits are ash grass: a wild encounter can
                        # open in the middle of the entry-plus-follow-through and
                        # eat the firing step. That is a BATTLE, not a broken
                        # warp, and reporting "could not take the warp" for it
                        # sent the caller away from a door that works fine.
                        if self.in_battle():
                            if on_battle == "fight":
                                self.fight()
                                self.advance_scene(40000)
                                continue
                            raise TravelInterrupted(
                                self.map_name(), self.pos(), dest_map)
                        raise TravelError(
                            f"could not take the warp to {nxt}: {self.last_warp_reason}"
                        )
                else:
                    if not self._cross_seam(here, edge, on_battle):
                        # SAME RECOVERY AS THE WARP BRANCH ABOVE. A wild that
                        # appears on the walk to the crossing cell leaves the
                        # crossing untried, and reporting it as "could not cross
                        # the seam" sends the caller away from a border that works.
                        #
                        # Route 116 is wall-to-wall grass and Rustboro is through
                        # its west edge: a fresh run logged `could not cross the L
                        # seam to RustboroCity` six times, abandoned "trigger the
                        # stolen Devon Goods errand", and ground the route instead
                        # at 0 badges. Driven by hand -- fight the wild, then
                        # goto(0,8) and one step L -- it crossed first try.
                        if self.in_battle():
                            if on_battle == "fight":
                                self.fight()
                                self.advance_scene(40000)
                                continue
                            raise TravelInterrupted(
                                self.map_name(), self.pos(), dest_map)
                        raise TravelError(
                            f"could not cross the {edge['direction']} seam to "
                            f"{nxt}{self._gate_hint(here)}"
                        )
            raise TravelError(f"gave up after {max_legs} legs heading for {dest_map}")
        finally:
            self._journey_deadline = prev_deadline

    #: Tasks that own the whole screen and must be BACKED OUT of, never
    #: advanced. Matched by prefix because the engine names variants
    #: (Task_PokedexMainScreen, Task_PokedexResultsScreen, ...).
    FOREIGN_MENUS = (
        "Task_Pokedex",
        "Task_PokemonSummaryScreen",
        "Task_BagMenu",
        "Task_StartMenu",
        "Task_PokemonStorageSystem",
        "Task_TrainerCard",
        "Shop_",                 # the mart's BUY list; A here spends money
        # The overworld party screen and the summary page it opens. Their task
        # names are compiler-generated, so they are listed literally: a run
        # sat frozen on Route 116 for fifteen minutes inside sub_8089D94 with
        # sLockFieldControls set, because the promotion code opened the party
        # menu, landed on SUMMARY instead of SWITCH, and nothing recognised
        # the screen as something to back out of.
        "HandleDefaultPartyMenu",
        "sub_8089D94",           # party mon summary
        "sub_806B58C",           # party menu input
    )

    def _foreign_menu(self):
        """The name of a full-screen menu currently open, or None."""
        try:
            tasks = self.state.tasks()
        except Exception:  # noqa: BLE001
            return None
        for name in tasks:
            for prefix in self.FOREIGN_MENUS:
                if name.startswith(prefix):
                    return name
        return None

    def _gate_hint(self, map_name):
        """", because <gate>" when a live coord_event explains the refusal.

        Movement failures used to say only WHERE they stopped. Two towns in one
        night were diagnosed by hand-grepping the decompilation for the
        variable that shuts the road; the data was in the repo the whole time,
        so the error says it now.
        """
        try:
            from pokeagent.gates import GateReader

            x, y = self.pos()
            why = GateReader(self.state).explain(map_name, x, y)
        except Exception as err:  # noqa: BLE001 - a hint must never mask the error
            log.debug("gate hint unavailable: %s", err)
            return ""
        return f", because {why}" if why else ""

    # ---- the map the game is actually using -------------------------------
    #
    # `MAP_OFFSET` (include/fieldmap.h:19) is the 7-tile border the engine
    # keeps around every map, so map coordinate (x, y) lives at grid index
    # (x + 7, y + 7).
    MAP_OFFSET = 7

    def live_grid(self, rect=None) -> dict:
        """`{(x, y): Cell}` read from `gBackupMapLayout` -- the map the engine
        is walking on right now, barriers and all.

        The .blk files are the map as SHIPPED. A switch puzzle rewrites it at
        runtime, and with only the static grid the pathfinder sees Wattson
        sitting in a component it cannot enter and reports, correctly, that
        there is no route.
        """
        base = self.emu.resolve("gBackupMapLayout")
        width = self.emu.u32(base)
        height = self.emu.u32(base + 4)
        ptr = self.emu.u32(base + 8)
        if not ptr or not 0 < width <= 512 or not 0 < height <= 512:
            return {}
        info = self.nav.info(self.map_name())
        x0, y0, x1, y1 = rect or (0, 0, info.width - 1, info.height - 1)
        raw = bytes(self.emu.read(ptr, width * height * 2))
        out = {}
        for y in range(max(0, y0), min(info.height, y1 + 1)):
            gy = y + self.MAP_OFFSET
            if gy >= height:
                continue
            row = (gy * width) * 2
            for x in range(max(0, x0), min(info.width, x1 + 1)):
                gx = x + self.MAP_OFFSET
                if gx >= width:
                    continue
                off = row + gx * 2
                entry = raw[off] | (raw[off + 1] << 8)
                out[(x, y)] = self.nav.cell_from_entry(self.map_name(), entry)
        return out

    def grid_drift(self, rect=None) -> list:
        """Cells where the live map disagrees with the shipped one.

        Empty is the normal case and worth keeping that way: a long drift list
        means the decode is wrong, not that the game changed its mind.
        """
        name = self.map_name()
        out = []
        for (x, y), live in self.live_grid(rect).items():
            static = self.nav.grid(name)[y][x]
            if (live.collision, live.elevation) != (static.collision,
                                                    static.elevation):
                out.append((x, y, static, live))
        return out

    def sync_grid(self, rect=None) -> int:
        """Reconcile observed live tiles, including barriers that closed."""
        name = self.map_name()
        # Send unchanged observations too: they remove stale overrides after
        # a barrier closes or an older savestate is loaded. Missing cells are
        # unknown, not evidence that the shipped tile has returned.
        changed = self.nav.set_live_cells(name, self.live_grid(rect))
        if changed:
            log.info("synced %d live cells on %s", changed, name)
        return changed

    #: Map-object scripts that are scenery a field move removes, and the HM
    #: that removes each. Read from the map's own object list rather than by
    #: guessing at graphics ids: the Route 111 pair renders as NPCs to
    #: anything reading live positions, and was very nearly diagnosed as two
    #: wandering trainers boxing the player in.
    FIELD_OBSTACLES = {
        "S_BreakableRock": "ROCK SMASH",
        "S_CuttableTree": "CUT",
        "S_PushableBoulder": "STRENGTH",
    }

    def field_obstacles(self, map_name=None) -> list:
        """`[(x, y, hm)]` for scenery on this map that a field move clears.

        Positions are LIVE, because a script can move an object away from
        where the map file puts it.
        """
        name = map_name or self.map_name()
        try:
            objects = self.nav.info(name).objects or []
        except Exception:  # noqa: BLE001
            return []
        wanted = {}
        for obj in objects:
            hm = self.FIELD_OBSTACLES.get(str(obj.get("script") or ""))
            if hm:
                wanted[(obj["x"], obj["y"])] = hm
        if not wanted:
            return []
        live = {(o["x"], o["y"]) for o in self.live_npcs() if not o["player"]}
        # A cleared rock is gone from the live list; a moved one is still
        # there at a different cell, so match on presence rather than identity.
        return [(x, y, hm) for (x, y), hm in wanted.items() if (x, y) in live]

    def boulder_signature(self) -> tuple:
        """Where the pushable boulders are, right now.

        A search across a boulder room needs this in its node key: the same
        tile with a boulder shoved aside is a different world, and dedupe on
        position alone throws the solution away.
        """
        here = self.map_name()
        try:
            objects = self.nav.info(here).objects or []
        except Exception:  # noqa: BLE001
            return ()
        if not any(str(o.get("script") or "") == "S_PushableBoulder"
                   for o in objects):
            return ()
        # BOULDERS ONLY. Including every live object put the patrolling Aqua
        # grunts in the key, so each of their steps minted a "new" world and
        # the search wandered 3,936 nodes without deduping anything. The
        # graphics id is the engine's own answer to "is this a boulder"
        # (OBJ_EVENT_GFX_PUSHABLE_BOULDER, include/constants/event_objects.h:93).
        return tuple(sorted(
            (o["x"], o["y"]) for o in self.live_npcs()
            if not o.get("player") and o.get("graphics_id") == 87))

    def clear_rocks(self, limit=8) -> int:
        """Smash every breakable rock on this map. Returns how many went.

        A savestate search cannot do this for itself: its moves are the four
        steps, so a rock in the corridor is an eternal wall. Seafloor Cavern
        Room2 has two of them among seven boulders, which is why 2,600 nodes
        of searching found no route.
        """
        gone = 0
        for _ in range(limit):
            rocks = [(x, y) for x, y, hm in self.field_obstacles()
                     if hm == "ROCK SMASH"]
            if not rocks:
                break
            here, before = self.map_name(), len(rocks)
            for rx, ry in rocks:
                try:
                    if self.smash_rock(rx, ry):
                        gone += 1
                except Exception:  # noqa: BLE001 - unreachable rock is fine
                    continue
                if self.map_name() != here:
                    return gone
            if len([1 for _x, _y, hm in self.field_obstacles()
                    if hm == "ROCK SMASH"]) == before:
                break
        if gone:
            log.info("smashed %d rock(s) on %s", gone, self.map_name())
        return gone

    def use_strength(self) -> bool:
        """Turn Strength on for this map, so boulders can be shoved.

        `S_PushableBoulder` (pret/data/field_move_scripts.inc:124-139) is an A
        press on a boulder: it needs badge 4, checks the party for move 70, and
        asks YES/NO before setting FLAG_SYS_USE_STRENGTH. Only then does
        `sub_8058F6C` (src/field_player_avatar.c:641) let a step move one.

        The flag is cleared on every map change (src/overworld.c:225,235), so
        this is per-room and idempotent.
        """
        self.last_field_reason = None
        if self.state.flag("FLAG_SYS_USE_STRENGTH"):
            return True
        if not self.field_moves().get("STRENGTH"):
            self.last_field_reason = "no party member knows STRENGTH"
            return False
        boulders = [(x, y) for x, y, hm in self.field_obstacles()
                    if hm == "STRENGTH"]
        if not boulders:
            self.last_field_reason = "no boulder on this map"
            return False
        for bx, by in boulders:
            for dx, dy, facing in ((0, 1, "U"), (0, -1, "D"),
                                   (1, 0, "L"), (-1, 0, "R")):
                stand = (bx + dx, by + dy)
                cell = self.nav.cell(self.map_name(), *stand)
                if cell is None or cell.collision:
                    continue
                if not self.goto(*stand, on_battle="fight"):
                    continue
                self.emu.run_sequence(f"{facing}:4 .:20")
                self.emu.run_sequence("A:4 .:40")
                self.advance_scene(40000)
                if self.choice_open():
                    self.resolve_choice("YES")
                self.advance_scene(60000)
                if self.state.flag("FLAG_SYS_USE_STRENGTH"):
                    log.info("strength is on (%s)", self.map_name())
                    return True
                self.close_menus()
        self.last_field_reason = "pressed A on every boulder side, flag unset"
        return False

    def smash_rock(self, x, y) -> bool:
        """Clear a breakable rock at (x, y). False with `last_field_reason`.

        The overworld A button dispatches on the tile being FACED, so this
        walks adjacent, bumps to turn, and presses A -- the same lesson as
        Crystal's gotcha 19, where driving a water HM from the party menu got
        "Can't use that here" on a perfectly good tile.
        """
        self.last_field_reason = None
        who = self.field_moves().get("ROCK SMASH")
        if not who:
            self.last_field_reason = "no party member knows ROCK SMASH"
            return False
        for dx, dy, facing in ((0, 1, "U"), (0, -1, "D"), (1, 0, "L"), (-1, 0, "R")):
            stand = (x + dx, y + dy)
            cell = self.nav.cell(self.map_name(), *stand)
            if cell is None or not cell.passable:
                continue
            # A SMALL budget: this is a step or two onto an adjacent
            # cell, not a journey. The default twelve replans (144
            # rounds, each settling 120 frames) turns four sides of one
            # rock into ~69k frames of standing still, and clear_the_way
            # multiplies that by every obstacle on the map.
            if self.pos() != stand and not self.goto(*stand,
                                                     max_replans=3):
                continue
            # Bumping into the rock turns the player toward it without moving.
            self.step_dir(facing)
            self.emu.run_sequence("A:4 .:40")
            if "ROCK SMASH" not in (self.state.message() or "").upper():
                continue
            self.resolve_choice("YES")
            self.advance_scene(60_000)
            still = {(o["x"], o["y"]) for o in self.live_npcs() if not o["player"]}
            if (x, y) not in still:
                log.info("smashed the rock at %s", (x, y))
                self.nav.blocked.get(self.map_name(), set()).discard((x, y))
                return True
        self.last_field_reason = f"could not reach or face the rock at {(x, y)}"
        return False

    def clear_the_way(self, target) -> bool:
        """Smash whatever breakable scenery stands between here and `target`.

        RE-ENTRANT, and that has to be guarded: `goto` calls this when a route
        is blocked by something, and clearing a rock means walking to it --
        which calls `goto`. Without the guard the two called each other until
        Python gave up, and every journey to Rusturf Tunnel died with
        "maximum recursion depth exceeded" instead of a reason.

        Called when a route exists on the static grid but not once live
        objects are marked: if the things doing the blocking are rocks and
        somebody knows ROCK SMASH, the road is not shut, it is just closed.
        """
        if getattr(self, "_clearing", False):
            return False
        obstacles = [o for o in self.field_obstacles() if o[2] == "ROCK SMASH"]
        if not obstacles:
            return False

        def route_open() -> bool:
            # WITH the live objects marked. Without them the static grid always
            # says the road is open -- which is true and useless, and made the
            # first version of this decline to smash anything.
            self._mark_npcs(self.map_name())
            return self.nav.find_path(
                self.map_name(), self.pos(), target, self.elevation()) is not None

        cleared = False
        self._clearing = True
        try:
            for x, y, _hm in obstacles:
                if route_open():
                    break
                if self.smash_rock(x, y):
                    cleared = True
        finally:
            self._clearing = False
        return cleared

    def _cross_seam(self, here, edge, on_battle="raise"):
        """Walk off the edge of a map into a connected one."""
        info = self.nav.info(here)
        d = edge["direction"]
        x, y = self.pos()
        if d in "UD":
            edge_y = 0 if d == "U" else info.height - 1
            candidates = [(cx, edge_y) for cx in range(info.width)]
        else:
            edge_x = 0 if d == "L" else info.width - 1
            candidates = [(edge_x, cy) for cy in range(info.height)]
        # Only cells we can actually get to are worth trying; the far side of
        # a river on the same row is not a candidate.
        reachable = self.nav.reachable(here, (x, y), self.elevation())
        candidates = [c for c in candidates if c in reachable]
        # The PLAN already chose a cell, and it chose it for a reason: a seam
        # is a whole border and different cells land on different MAPS.
        # Route 111's west edge touches both Route 113 and Route 112, so
        # ignoring `cross_at` and re-ranking locally crossed to Route 112 on a
        # leg planned for Route 113 -- travel then re-planned from the wrong
        # map, walked back, and oscillated Mauville -> Route 111 -> Route 112
        # -> Route 111 -> Mauville until the leg budget ran out. Honour the
        # planned cell when we can still reach it; rank only when there is no
        # plan to honour (callers that reach here directly).
        # A seam is a whole border and different cells along it land on
        # DIFFERENT MAPS: Route 111's west edge gives Route 113 for rows 0-19
        # and Route 112 for rows 20-79. Crossing wherever ranked best turned a
        # leg planned for Route 113 into an arrival on Route 112, and travel
        # then re-planned from the wrong map and walked back -- the Mauville
        # oscillation. So keep only crossings that land where the plan said,
        # and try the planned cell first. Falling back to another cell is fine
        # and often necessary (the walker knows about elevation and NPCs that
        # the planner does not); falling back to another MAP never is.
        planned = edge.get("cross_at")
        dest = edge.get("dest")
        if dest is not None:
            same_map = []
            for c in candidates:
                land = self.nav.connection_landing(here, d, *c, dest_name=dest)
                if land is not None:
                    same_map.append(c)
            if same_map:
                candidates = same_map
        if planned is not None:
            planned = tuple(planned)
            candidates = ([planned] if planned in candidates else []) + \
                [c for c in candidates if c != planned]
        # Nearest-first was the obvious order and the wrong one. A seam is a
        # whole border and different cells land in different places: standing
        # near y=7 on Verdanturf's east edge, the closest crossing lands on
        # Route 117 (0,7), which is a ONE-CELL pocket. The player arrived
        # unable to move in any direction -- raw d-pad included, no scene, no
        # dialog -- and the run was finished. Two cells further down lands on
        # 698 cells of open road.
        #
        # So rank by how much walkable map each crossing lands on, and only
        # use distance to break ties. nav already computes the landing; this
        # just stops throwing it away.
        # PRICED ON A SAMPLE. `_landing_room` is a reachability BFS on the far
        # side, and a seam is a whole border -- Route 119's is 130 cells. This
        # sort alone spent 163 seconds against a 40-second budget without the
        # player taking a single step, which is what "just standing there"
        # looked like from outside. nav._rank_probe picks an even spread; the
        # cells it does not price keep border order behind the ones it does,
        # so every candidate is still offered, just not all measured.
        probe = set(self.nav._rank_probe(candidates))

        def rank(cell):
            if cell not in probe:
                # Unpriced: sorts after everything measured, order preserved.
                return (1, abs(cell[0] - x) + abs(cell[1] - y))
            try:
                room = self.nav._landing_room(here, edge, cell)
            except Exception:  # noqa: BLE001 - never let ranking end a journey
                room = 0
            return (-room, abs(cell[0] - x) + abs(cell[1] - y))

        # Ranking is for callers with no plan. Sorting a planned crossing is
        # how the previous attempt undid itself: `cross_at` was put first and
        # then sorted straight back out, so a leg planned for Route 113 at row
        # 7 crossed at row 70 onto Route 112.
        if planned is None:
            candidates.sort(key=rank)
        elif len(candidates) > 1:
            head, tail = candidates[:1], candidates[1:]
            tail.sort(key=rank)
            candidates = head + tail
        for cx, cy in candidates[:12]:
            # Twelve candidates x a 60-replan goto is minutes of walking, and
            # `goto` bailing on the journey deadline looks exactly like a
            # crossing that did not work -- so the loop kept spending the
            # budget it had already exhausted, twelve times over. Measured at
            # 164s against a 40s budget. Stop when the journey is out of time
            # and let travel replan; a seam that needs more than one budget is
            # a routing problem, not something to brute-force here.
            journey = getattr(self, "_journey_deadline", None)
            if journey is not None and _time.time() > journey:
                self.last_goto_reason = (
                    f"out of time crossing the {d} seam to {edge.get('dest')}"
                )
                return False
            if not self._settle_interruption(here, edge["dest"], on_battle):
                return False
            # A seam crossing is a cross-map leg and the walk to it can be the
            # length of the map -- 130 rows, here. `goto`'s replan cap is
            # sized for local movement, so use the bigger budget rather than
            # silently falling through to a crossing that lands elsewhere.
            budget = 60 if planned is not None else 12
            # PASS THE BATTLE POLICY DOWN. This called `goto` with the default,
            # which RAISES, so `travel(on_battle="fight")` still died the moment
            # a wild appeared during a seam crossing:
            #   TravelInterrupted: a battle started at Route124 (62,25) while
            #   travelling to Route124
            # Sea routes are wall-to-wall encounters and every trip to Mossdeep
            # crosses two of them, so the journey could almost never finish. The
            # caller asked for "fight"; honour it all the way down.
            if not self.goto(cx, cy, map_name=here, max_replans=budget,
                             on_battle=on_battle):
                continue
            for _ in range(3):
                self.emu.run_sequence(f"{_HOLD[d]}:{WARP_HOLD}")
                self.settle(400)
                if self.map_name() != here:
                    return True
                if self.in_battle():
                    if not self._settle_interruption(here, edge["dest"], on_battle):
                        return False
                    break
        return False

    def _settle_interruption(self, here, dest, on_battle):
        """Deal with a battle or scene that started mid-journey.

        Returns False when the caller should give up this attempt; raises
        when the policy says a battle is the caller's decision.
        """
        if self.in_battle():
            if on_battle != "fight":
                raise TravelInterrupted(here, self.pos(), dest)
            self.fight()
            self.advance_scene(40000)
        if self.scene_active():
            self.advance_scene(40000)
        return True

    def talk_to(self, x, y, facing=None):
        """Face an NPC at (x, y) from an adjacent tile and press A."""
        self.last_talk_reason = None
        m = self.map_name()
        counter = self.consts.behaviors.get("MB_COUNTER")
        for d, (dx, dy) in DIRS.items():
            if facing and d != facing:
                continue
            stand = (x - dx, y - dy)
            cell = self.nav.cell(m, *stand)
            # A shop or Pokemon Centre clerk stands behind an MB_COUNTER tile.
            # You cannot step onto it, you talk ACROSS it -- so the approach
            # cell is one further out. Without this the nurse is unreachable
            # and heal() reports "nothing answered an A press".
            if cell is not None and not cell.passable and cell.behavior == counter:
                stand = (x - 2 * dx, y - 2 * dy)
                cell = self.nav.cell(m, *stand)
            if cell is None or not cell.passable:
                continue
            if self.pos() != stand and not self.goto(*stand, map_name=m):
                continue
            # Turn to face them, then talk. A tap without facing does nothing.
            self.emu.run_sequence(f"{_HOLD[d]}:4 .:10")
            self.emu.run_sequence("A:4 .:24")
            if self.dialog_open() or self.scene_active() or self.in_battle():
                return True
        self.last_talk_reason = f"nothing at ({x},{y}) on {m} answered an A press"
        return False

    # ---- what have I not collected? ---------------------------------------

    def missables(self, kind="key"):
        """Un-collected key items and HMs, evaluated live against the flags.

        This exists because of one specific failure in the predecessor
        project: HM02 FLY sat with an NPC for an entire playthrough because
        nothing ever surfaced that it was uncollected, and every trip of that
        run was made on foot.
        """
        from pokeagent import missables

        return missables.missing_items(self.state, kind=kind)

    def field_moves(self):
        """Per HM, which party member actually knows the move.

        "The HM is in the bag" is not "I can use it".
        """
        from pokeagent import missables

        return missables.field_moves(self.state)

    @property
    def flight(self):
        """The FLY driver, built once per Driver and reused.

        Lazy for the same reason `battle` is: constructing it parses the
        region map's ROM tables, which a session that never flies has no use
        for.
        """
        if self._flight is None:
            from pokeagent.flying import Flight

            self._flight = Flight(self)
        return self._flight

    def fly_to(self, destination, max_frames=40_000) -> bool:
        """Fly to a town, city or unlocked landmark. True only on arrival.

        `destination` is a map name (``"SlateportCity"``), a town name, or a
        MAPSEC constant. Refuses -- having pressed nothing -- with
        `last_fly_reason` set to one of ``no-knower``, ``no-badge``,
        ``indoors``, ``unknown-destination`` or ``not-visited``. See
        :mod:`pokeagent.flying` for where each condition comes from in the
        engine.
        """
        # SURFACE FIRST. Fly is refused on MAP_TYPE_UNDERWATER
        # (Overworld_MapTypeAllowsTeleportAndFly), and a save left down there
        # is a dead end for every script that starts by flying: the Safari
        # sweep failed its first move and restart-looped for twenty-five
        # minutes against "indoors -- Underwater2 is MAP_TYPE_UNDERWATER".
        # Surfacing is one dive() toggle and nothing else in the harness did it.
        if self.underwater():
            self.dive()
        ok = self.flight.fly_to(destination, max_frames=max_frames)
        self.last_fly_reason = self.flight.last_reason
        self.last_fly_detail = self.flight.last_detail
        return ok

    def fly_destinations(self):
        """Every region-map fly target, the reachable ones first."""
        return self.flight.destinations()

    def needs_flash(self, map_name=None):
        from pokeagent import missables

        return missables.needs_flash(map_name or self.map_name())

    # ---- healing -----------------------------------------------------------

    #: include/constants/event_objects.h:64
    NURSE_GFX = 58

    def in_pokecenter(self) -> bool:
        return "PokemonCenter" in self.map_name()

    def heal(self, tries=2) -> bool:
        """Talk to the Pokemon Centre nurse and verify the party came back up.

        The nurse's cell is read from the map's own object list rather than
        assumed: Centre interiors are not all laid out alike, and hardcoding
        (7,2) is the kind of thing that works until it silently does not.

        Verified by HP, not by the jingle: a heal that reports success while
        the party is still hurt is worse than one that fails loudly.
        """
        self.last_heal_reason = None
        if not self.in_pokecenter():
            self.last_heal_reason = f"{self.map_name()} is not a Pokemon Centre"
            return False

        def hurt():
            return [m for m in self.state.party() if not m.is_egg and m.hp < m.max_hp]

        def spent():
            """Mons with a move at zero PP.

            The nurse restores PP as well as HP, and this used to ask only
            about HP -- so a party at full health with nothing left to attack
            with was told "nothing to heal" and the heal returned True without
            talking to anybody. The loop's own trigger is PP exhaustion, so it
            asked again, and again: a fresh run sat in Oldale's Centre with a
            L18 COMBUSKEN at 54/54 and DOUBLE KICK, PECK and EMBER all at 0,
            logging "healing: EMBER out of damaging PP" forever.
            """
            out = []
            for m in self.state.party():
                if m.is_egg:
                    continue
                if any(mid and pp == 0 for mid, pp in zip(m.moves, m.pp)):
                    out.append(m)
            return out

        if not hurt() and not spent():
            return True

        nurse = next(
            (n for n in self.live_npcs() if n["graphics_id"] == self.NURSE_GFX), None
        )
        if nurse is None:
            objs = self.nav.info(self.map_name()).objects
            cell = next(
                ((o["x"], o["y"]) for o in objs if o.get("graphics_id") == "OBJ_EVENT_GFX_NURSE"),
                None,
            )
            if cell is None:
                self.last_heal_reason = f"no nurse on {self.map_name()}"
                return False
            nurse = {"x": cell[0], "y": cell[1]}

        for _ in range(tries):
            if not self.talk_to(nurse["x"], nurse["y"]):
                self.last_heal_reason = f"could not reach the nurse: {self.last_talk_reason}"
                continue
            # "Shall I heal your POKeMON?" is a real YES/NO box.
            for _ in range(40):
                if self.choice_open():
                    self.resolve_choice("YES")
                    break
                if not self.scene_active() and not self.dialog_open():
                    break
                self.emu.run_sequence(jitter.sequence("A:4 .:16"))
            self.advance_scene(40000)
            # Success is BOTH halves restored, for the same reason the check
            # above asks about both: a nurse that took the party and handed
            # back empty movesets has not healed it.
            if not hurt() and not spent():
                return True
        broke = hurt() or spent()
        self.last_heal_reason = (
            "the nurse was talked to but the party is still spent: "
            + ", ".join(
                f"{m.nickname} {m.hp}/{m.max_hp}"
                + ("" if m.hp < m.max_hp else " (no PP)")
                for m in broke
            )
        )
        return False

    def heal_at_nearest_center(self, max_hops=12) -> bool:
        """Route to the closest REACHABLE Pokemon Centre and heal there.

        "Closest" used to mean fewest hops on the map graph, which is the same
        mistake `travel` had its graph fallback removed for: the graph knows
        which maps touch, not which can be walked to from where the player is
        standing. With a fainted Lottad in Fiery Path it picked Lavaridge --
        two hops away on the graph, unreachable on foot until Mt. Chimney --
        and every cycle threw "no walkable route", logged a traceback and
        tried the identical centre again.

        `route_legs` answers the question that matters, so candidates are
        ranked by the length of a route that actually exists, and a centre
        that fails is followed by the next one rather than ending the attempt.
        """
        self.last_heal_reason = None
        here, cell = self.map_name(), self.pos()
        candidates = []
        for name in self.nav.index:
            if "PokemonCenter_1F" not in name:
                continue
            if name == here:
                candidates.append((0, name))
                continue
            legs = self.nav.route_legs(here, cell, name, max_hops=max_hops)
            if legs:
                candidates.append((len(legs), name))
        if not candidates:
            self.last_heal_reason = (
                f"no Pokemon Centre is walkable from {here} {cell}"
            )
            return False
        candidates.sort()
        for _, name in candidates[:3]:
            # A battle on the way to heal is the NORMAL case -- the party is
            # hurt because it has been fighting, and the road to a Centre runs
            # through the same grass. Treating an encounter as a reason to
            # abandon this Centre and try the next one burned all three
            # candidates in three battles and then gave up, which is how a
            # zombie lead stayed at 0 PP while walking past two Centres.
            # Fight it and resume the SAME journey.
            for _attempt in range(12):
                try:
                    if self.map_name() != name:
                        self.travel(name, on_battle="fight")
                    if self.map_name() == name and self.heal():
                        return True
                    break
                except TravelInterrupted:
                    self.fight()
                    self.advance_scene(40000)
                    continue
                except TravelError as exc:
                    log.info("could not heal at %s: %s", name, str(exc)[:90])
                    self.last_heal_reason = f"{name}: {str(exc)[:90]}"
                    break
        return False

    # ---- battle ----------------------------------------------------------

    @property
    def tactics(self):
        if self._tactics is None:
            from pokeagent.tactics import Tactics

            self._tactics = Tactics(self.emu, self.names, self.consts, self.state)
        return self._tactics

    @property
    def battle(self):
        if self._battle is None:
            from pokeagent.battle import BattleSession

            self._battle = BattleSession(
                self.emu, self.names, self.consts, self.state, self.tactics
            )
        return self._battle

    def battle_frame(self):
        """Everything about the current turn in one read."""
        return self.battle.frame()

    def outlook(self):
        """Every move of mine scored against the mon actually standing there,
        with the game's own damage formula. None before gBattleMons fills."""
        return self.tactics.outlook()

    def recommend(self):
        """(action, why). The reason is part of the contract: a harness-made
        choice must never be silent.

        When a brain is attached it may break a tie between moves whose damage
        spans overlap -- but only then, and only over options the maths has
        already declared equivalent. Anything the formula can separate stays
        with the formula.
        """
        analysis = self.tactics.outlook()
        if analysis is None:
            return None, self.tactics.last_outlook_reason
        action, why = self.tactics.recommend(analysis)
        if self.choices.enabled and isinstance(action, tuple) and action[0] == "attack":
            picked = self.choices.tied_move(analysis, action[1])
            if picked != action[1]:
                name = next(
                    (m["name"] for m in analysis["moves"] if m["slot"] == picked),
                    f"slot {picked}",
                )
                return ("attack", picked), (
                    f"{name} (slot {picked}) -- tie broken by the local model: "
                    f"{self.choices.last_reason}"
                )
        return action, why

    def explain(self):
        analysis = self.tactics.outlook()
        return (
            self.tactics.explain(analysis)
            if analysis
            else f"no analysis: {self.tactics.last_outlook_reason}"
        )

    @staticmethod
    def damage_first(frame) -> tuple:
        """Always the hardest-hitting move available. Nothing else.

        The scored tactics lost Tate & Liza TWICE from a winning position --
        it spent turns on SAND-ATTACK, EMBER into a Rock/Psychic and HEADBUTT
        while Lunatone sat on 2 HP, because its "certain KO first, then heal,
        then cure, then switch" ladder keeps finding something to do that is
        not damage. This policy has one rule, power x type multiplier, and it
        took the badge in 16 seconds on the same savestate.

        Returns None when nothing can attack, which hands the turn back to the
        harness so switching and item use still work.
        """
        best, score = None, -1.0
        for i, mv in enumerate(frame.get("moves") or []):
            if not mv or not mv.get("pp"):
                continue
            power = mv.get("power") or 0
            if power <= 0:
                continue
            rank = power * (mv.get("effect_mult") or 1.0)
            if rank > score:
                best, score = i, rank
        if best is not None:
            return ("attack", best)
        # EVERY MOVE DRY -> STRUGGLE. The engine substitutes it automatically
        # when you pick a slot with no PP, so choosing one is how the turn gets
        # taken. Declining instead left the harness cycling switch (which its
        # own verifier rejected) and flee (refused in a trainer battle) until
        # the battle timed out, with a fully healthy bench behind the dry mon.
        moves = frame.get("moves") or []
        if moves and not any((m or {}).get("pp") for m in moves):
            return ("attack", 0)
        return None

    def fight(self, policy=None, max_frames=200000, on_learn=None,
              on_nickname=None):
        """Play the battle out.

        `on_nickname(species)` names a catch. Left None the species name is
        used, which is what declining the prompt would have given -- never a
        keyboard full of A's.
        """
        # THE DEFAULT POLICY MATTERS MORE THAN THE PASSED ONE.
        #
        # Nearly every wild battle in a run does NOT come through the play
        # loop's own fight(): it interrupts a journey, and `goto`/`travel`/
        # `_cross_seam` call THIS method with no policy at all. So the loop's
        # catch decision -- the whole reason the run has a Pokedex objective --
        # was consulted only during deliberate grinding, and every encounter
        # met on the road was simply knocked out. The run KO'd a CARVANHA and a
        # GOLDEEN, neither of them registered as caught, within a minute of
        # each other.
        #
        # `battle_policy` lets the loop install its decision once and have it
        # apply everywhere, without every call site having to remember.
        if policy is None:
            policy = getattr(self, "battle_policy", None)
        if on_learn is None and getattr(self, "choices", None) is not None:
            on_learn = self._learn_hook
        # NICKNAMES ARE OFF BY DEFAULT, and the reason is what a watcher sees.
        #
        # Naming was routed through the small-decision boundary, which is a
        # defensible place for it -- except the keyboard cursor cannot be
        # driven on the CATCH naming screen. Every catch therefore spent a
        # model call (up to 19 s, or a 60 s open circuit) and then failed three
        # times over with "could not move the cursor to 'Z' at (6,3)", leaving
        # the nickname prompt on screen again and again. Reported twice from
        # the couch, the second time as "8 hours later and we're still stuck
        # trying to give lottad a pokeball": from outside, a re-offered prompt
        # with a ball sprite on it is indistinguishable from a stuck loop.
        #
        # A nickname has NO bearing on the Pokedex -- `caught` is set by
        # `atkF1_trysetcaughtmondexflags` on a successful catch and knows
        # nothing about names. So the default is to decline the prompt and take
        # the species name, which is instant and cannot fail. Pass
        # `on_nickname=` explicitly to name something on purpose.
        if on_nickname is not None:
            pass
        return self.battle.play(
            policy=policy, max_frames=max_frames, on_learn=on_learn,
            on_nickname=on_nickname,
        )

    def _learn_hook(self, prompt):
        """Answer a move-learn prompt: maths first, model only on a tie.

        The damage-aware ranking decides whenever it can separate the
        candidates -- a certain answer is never handed to a 4B model. When two
        or more score IDENTICALLY it becomes a preference between equals,
        which is the boundary `smallchoices` exists to police, so the local
        model gets it with the ROM's own numbers as context and a tighter
        timeout, because a battle turn is waiting on the answer.
        """
        b = self.battle
        default = b.default_learn(prompt)
        if default is None:
            return None                     # declining is a real answer
        try:
            mine = b._learner_types(prompt)
            values = {
                m["slot"]: b.move_value(m, mine) for m in prompt["current"]
            }
            candidates = [m for m in prompt["current"] if not m["hm"]]
            best = values.get(default, 0.0)
            tied = [m for m in candidates
                    if abs(values.get(m["slot"], 0.0) - best) < 1e-9]
            if len(tied) < 2:
                return default
            gaps = ()
            try:
                from pokeagent import team as teammod

                t = teammod.Team(self.names, self.consts, self.state)
                gaps = tuple(t.coverage(t.party()).get("gaps", ()))
            except Exception:  # noqa: BLE001 - context is a nicety, not a gate
                pass
            return self.choices.tied_learn(
                prompt, tied, values, default,
                coverage_gaps=gaps, owner_types=mine,
            )
        except Exception as exc:  # noqa: BLE001 - never lose a battle to this
            log.debug("learn hook fell back to the default (%s)", exc)
            return default

    def attack(self, slot=None):
        return self.battle.attack(slot)

    def switch_to(self, party_index):
        return self.battle.switch_to(party_index)

    def use_battle_item(self, item_name, target=None):
        return self.battle.use_item(item_name, target)

    def throw_ball(self, ball=None):
        return self.battle.throw_ball(ball)

    def flee(self):
        return self.battle.flee()

    # ---- fishing ---------------------------------------------------------

    @property
    def fishing(self):
        if self._fishing is None:
            from pokeagent.fishing import Fishing

            self._fishing = Fishing(self)
        return self._fishing

    def fish(self, rod=None):
        """Cast a rod at the water ahead and reel whatever bites.

        Returns True only when a wild encounter actually started, so a caller
        can loop on it. `last_fish_reason` is one of `no-rod`, `wrong-tile`,
        `cast-failed`, `got-away`, `no-bite`; `last_fish_detail` carries the
        prose and `self.fishing.last_steps` the `tStep` sequence the engine's
        own state machine went through.
        """
        ok = self.fishing.fish(rod)
        self.last_fish_reason = self.fishing.last_reason
        self.last_fish_detail = self.fishing.last_detail
        return ok

    # ---- checkpoints ----------------------------------------------------

    def render_map(self, map_name=None):
        """ASCII art for humans. Decide from find_tiles/exits instead."""
        name = map_name or self.map_name()
        return self.nav.render(name, here=self.pos() if name == self.map_name() else None)

    def find_tiles(self, kind, map_name=None):
        return self.nav.find_tiles(map_name or self.map_name(), kind)

    def exits(self, map_name=None):
        return self.nav.exits(map_name or self.map_name())

    def press(self, seq):
        """Raw input DSL. The escape hatch: prefer a real verb, because a raw
        press is the one thing the harness cannot verify the meaning of."""
        self.emu.run_sequence(seq)
        self.settle(jitter.frames(240))
        return True

    def resolve_choice(self, choice="YES"):
        from pokeagent.menus import Menus
        return Menus(self.emu, self.state).resolve_choice(choice)

    def save(self, path=None):
        target = Path(path) if path else self.state_path
        if target is None:
            raise ValueError("no state path; pass one to save()")
        self.emu.save_state(target)
        log.info("saved %s @%d  %s", target.name, self.emu.frame, self.status())
        return target

    def load(self, path, adopt=True):
        """Load a savestate. `adopt=False` keeps the current save target.

        Adopting is right for the CLI (load a file, work on it, save it back)
        and wrong for any internal SEARCH: a solver that forks scratch states
        would silently redirect every later `save()` into its temp directory.
        That is not hypothetical -- it cost this run two badges of working-
        timeline progress before anyone noticed.
        """
        self.emu.load_state(path)
        if adopt:
            self.state_path = Path(path)
        return self


def main(argv=None):
    """Small CLI so a leg can be run without writing a script."""
    import argparse

    ap = argparse.ArgumentParser(description="drive Sapphire")
    ap.add_argument("leg", choices=["status", "observe", "goto", "walk", "travel", "map"])
    ap.add_argument("state")
    ap.add_argument("args", nargs="*")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if a.verbose else logging.INFO, format="%(message)s"
    )

    d = Driver(a.state)
    try:
        if a.leg == "status":
            print(d.status())
        elif a.leg == "observe":
            import json

            print(json.dumps(d.observe(), indent=1, default=str))
        elif a.leg == "map":
            here = d.pos()
            print(d.nav.render(a.args[0] if a.args else d.map_name(), here=here))
        elif a.leg == "goto":
            ok = d.goto(int(a.args[0]), int(a.args[1]))
            print("ok" if ok else f"FAILED: {d.last_goto_reason}")
            print(d.status())
        elif a.leg == "walk":
            ok = d.walk(a.args[0])
            print("ok" if ok else f"FAILED: {d.last_step_reason}")
            print(d.status())
        elif a.leg == "travel":
            d.travel(a.args[0])
            print(d.status())
    finally:
        if a.leg not in ("status", "observe", "map"):
            d.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
