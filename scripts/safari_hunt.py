#!/usr/bin/env python
"""Hunt the two BIKE-GATED Safari Zone quadrants.

The zone is four maps -- `SafariZone_Southeast` (the entrance quadrant),
`Southwest`, `Northeast` and `Northwest` -- joined by MAP CONNECTIONS, not
warps: only Southeast has a warp at all, back to the gate
(`pret/data/maps/SafariZone_*/map.json`). The two northern quadrants are the
ones nothing had ever hunted, and each is behind a DIFFERENT bike.

NORTHEAST -- ACRO BIKE, bike rails
    Measured on the decoded grid: with the rail metatiles treated as the walls
    they are on foot, a BFS from the entrance warp (32,33) reaches 682 of
    Southeast's 754 open cells and NONE of the north edge (x=31..34, y=0) that
    crosses into Northeast. The only road is the rail staircase at
    (17..20,7) / (20..22,5) / (22..25,3), with two ISOLATED horizontal rails at
    (20,6) and (22,4) as its landings.

    `check_acro_bike_metatile` (src/field_player_avatar.c:611, table
    :172-180) rewrites collision 0 into 9..13 for BUMPY_SLOPE, the two
    ISOLATED rails and the two plain rails. On foot `sub_8058D0C` bounces off
    anything `> 8` (:558), so a walker cannot set a wheel on them. On the acro
    bike `WillPlayerCollideWithCollision` (src/bike.c:958-971) waves through
    11 and 13 for EAST/WEST -- riding ALONG a horizontal rail -- and refuses
    NORTH/SOUTH, which is what the SIDE JUMP is for.

    The side jump is `AcroBikeTransition_SideJump` (src/bike.c:659-684),
    reached from `AcroBikeHandleInputTurning` when the pressed direction
    matches `AcroBike_GetJumpDirection()`. That reads the input history
    (`sAcroBikeTricksList`, src/bike.c:127-134): the last d-pad entry must be
    the jump direction, the last A/B/START/SELECT entry must be B_BUTTON, and
    BOTH must have been held no longer than 4 frames (`sAcroBikeJumpTimerList`
    = {4,0}). So one short `B+UP` press from a standstill, with the keys
    released first so the history rolls over, is the whole trick.

NORTHWEST -- MACH BIKE, muddy slope
    Two `MB_MUDDY_SLOPE` tiles at Southwest (8,2) and (8,3) sever the only
    northward corridor; `scripts/safari_nw.py` already rides them and
    `Driver.climb_slope` already holds the key through the run-up.

RYDEL EXCHANGES, HE DOES NOT SELL. You hold exactly one bike, so a run that
wants the other quadrant has to go back to `MauvilleCity (35,5)` and swap
(`scripts/unlocks.py:142-180`). This script does that for you.

THE ZONE IS A CLOCK. `EnterSafariMode` sets `gNumSafariBalls = 30` and
`gSafariZoneStepCounter = 500` (pret/src/safari_zone.c:62-63). Every field
step decrements the counter (`SafariZoneTakeStep`, :74-89, called from
`TryStartStepCountScript`, src/field_control_avatar.c:589) and at zero the
game runs `gUnknown_081C3448` -- ding-dong, one message, then
`EventScript_1C341B`: `ExitSafariMode` and a warp back to the gate
(pret/data/scripts/safari_zone.inc:25-32, :7-12). Running out of balls does
the same via `sub_80C824C` (safari_zone.c:96-115). Getting from the entrance
to either northern quadrant costs 60-120 of those 500 steps before the first
blade of grass, which is why this script takes `--trips`: re-entry is another
500 and costs 500 of the 53,000 in the bag.
"""
import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from collect import Collector  # noqa: E402
from pokeagent import dex as dexmod  # noqa: E402
from pokeagent.trek import Driver  # noqa: E402
from safari_probe import GATE, enter, reach_gate  # noqa: E402
from share_grind import unwedge  # noqa: E402

log = logging.getLogger("safari_hunt")

#: `PLAYER_AVATAR_FLAG_ACRO_BIKE` (pret/include/global.fieldmap.h:245).
ACRO_BIKE_FLAG = 1 << 2

#: Rydel's, and his cell inside. Same pair `unlocks.leg_acro_bike` uses.
BIKE_SHOP_WARP = ("MauvilleCity", 35, 5)
RYDEL_CELLS = ((2, 5), (2, 4), (3, 5))

#: Every rail metatile in Southeast, from the decoded grid. nav reads them as
#: ordinary floor -- their COLLISION byte is 0 and the refusal lives in
#: `check_acro_bike_metatile`, which nav does not model -- so a plain `goto`
#: happily plans over them and then bumps forever. Blocking them makes the
#: walker route like a player, and the climb below drives them by hand.
SE_RAILS = frozenset({
    (22, 3), (23, 3), (24, 3), (25, 3),          # MB_HORIZONTAL_RAIL  0xD6
    (22, 4),                                     # MB_ISOLATED_...     0xD4
    (20, 5), (21, 5), (22, 5),                   # 0xD6
    (20, 6),                                     # 0xD4
    (17, 7), (18, 7), (19, 7), (20, 7),          # 0xD6
})
#: Foot of the staircase: the last ordinary floor cell west of the rails.
RAIL_FOOT = (16, 7)
#: Top of the staircase: the first ordinary floor cell east of the rails.
RAIL_TOP = (26, 3)
#: The climb, as (move, landing). "R" RIDES east until the bike stops; "^" is
#: one acro side jump north.
#:
#: Riding stops by itself in exactly the right places, and it is the
#: ELEVATION that stops it, not a wall: the staircase is an elevation-5
#: catwalk over elevation-3 ground, so (21,7) and (23,5) -- both open floor --
#: refuse the seam and the bike parks on (20,7) and (22,5), the two cells the
#: side jumps launch from. One `step_dir` covers up to three tiles at acro
#: speed, which is why these are waypoints and not per-tile presses.
RAIL_CLIMB = [
    ("R", (20, 7)),
    ("^", (20, 6)), ("^", (20, 5)),
    ("R", (22, 5)),
    ("^", (22, 4)), ("^", (22, 3)),
    ("R", (26, 3)),
]

AREAS = {
    "ne": ("SafariZone_Northeast", "ACRO BIKE"),
    "nw": ("SafariZone_Northwest", "MACH BIKE"),
    # SOUTHWEST NEEDS NO BIKE. It is reachable on foot from the entrance, and
    # it carries the same 20% SEAKING slot on the SUPER ROD table as Northwest
    # (docs/gen3/guide/encounters.json). A rod cast costs NO step, only balls,
    # so this is the cheapest quadrant in the zone -- the mach-bike trip that
    # was being planned for SEAKING was pure waste.
    "sw": ("SafariZone_Southwest", None),
}


# ---------------------------------------------------------------- bookkeeping

def counters(d) -> str:
    return (f"balls {d.state.safari_balls()} steps {d.state.safari_steps()} "
            f"in_safari {d.state.in_safari()}")


def held_bike(d) -> str:
    try:
        pocket = {str(k).upper() for k in (d.state.bag().get("key_items") or {})}
    except Exception:  # noqa: BLE001
        return ""
    for name in ("MACH BIKE", "ACRO BIKE"):
        if name in pocket:
            return name
    return ""


def on_acro(d) -> bool:
    """Is the player on the ACRO bike? `Driver.on_bike` only knows the Mach."""
    try:
        return bool(d.emu.u8(d.emu.resolve("gPlayerAvatar")) & ACRO_BIKE_FLAG)
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------ the bikes

def swap_bike(d, want: str) -> bool:
    """Hold `want` after this returns, exchanging at Rydel's if we must.

    The exchange is free and reversible, and it is an EXCHANGE: the bike we
    arrive with leaves the bag, so "the other one is gone" is proof of
    success, not a bug.
    """
    have = held_bike(d)
    if have == want:
        log.info("bike: already holding %s", want)
        return True
    if not have:
        log.info("bike: NEITHER bike is in the bag -- nothing to exchange")
        return False
    name, wx, wy = BIKE_SHOP_WARP
    log.info("bike: holding %s, want %s -- going to Rydel's", have, want)
    if d.map_name() != name:
        if not d.flight.flyable_here():
            d.flight.step_outside()
        if not d.fly_to(name) and not d.travel(name, on_battle="fight"):
            log.info("bike: could not reach %s (at %s)", name, d.map_name())
            return False
    if not d.take_warp(wx, wy):
        log.info("bike: could not enter the shop (%s)", d.last_warp_reason)
        return False
    log.info("bike: inside %s", d.map_name())
    for cx, cy in RYDEL_CELLS:
        try:
            d.talk_to(cx, cy)
        except Exception as exc:  # noqa: BLE001
            log.debug("shop talk (%d,%d): %s", cx, cy, str(exc)[:70])
        d.advance_scene(40_000)
        for _ in range(8):
            if held_bike(d) == want:
                break
            d.emu.run_sequence("A:4 .:40")
            d.advance_scene(40_000)
        if held_bike(d) == want:
            log.info("bike: EXCHANGED, now holding %s", want)
            # Out of the shop: the zone is outdoors and nothing else in here
            # is wanted.
            d.flight.step_outside()
            return True
    log.info("bike: shop visited, still holding %s", held_bike(d) or "neither")
    return False


def mount_acro(d) -> bool:
    """Get on the ACRO bike. `Driver.mount_bike` is Mach-only by construction."""
    if on_acro(d):
        return True
    if held_bike(d) != "ACRO BIKE":
        log.info("acro: not in the bag")
        return False
    if not d.teacher.use_key_item("ACRO BIKE"):
        log.info("acro: bag refused (%s)",
                 getattr(d.teacher, "last_reason", "?"))
        d.close_menus()
        return False
    d.settle(60)
    if not on_acro(d):
        log.info("acro: engine refused the mount at %s %s",
                 d.map_name(), d.pos())
        d.close_menus()
        return False
    return True


def dismount_acro(d) -> bool:
    if not on_acro(d):
        return True
    d.teacher.use_key_item("ACRO BIKE")
    d.settle(60)
    return not on_acro(d)


# ------------------------------------------------------------- the rail climb

def side_jump(d, key: str = "UP", tries: int = 4) -> bool:
    """One acro side jump. Returns True only if the player actually moved.

    The trick is a HISTORY match, so the keys must be released long enough for
    `Bike_UpdateDirTimerHistory` to push a fresh entry before the press, and
    the press itself must be short: `HasPlayerInputTakenLongerThanList`
    rejects a d-pad or B timer above 4 frames (src/bike.c:816-849).
    """
    before = d.pos()
    for attempt in range(tries):
        d.emu.run_sequence(".:20")          # roll the history over to DIR_NONE
        d.emu.run_sequence(f"B+{key}:{2 + attempt}")
        d.settle(60)
        if d.pos() != before or d.map_name().startswith("SafariZone") is False:
            return True
    return False


def ride(d, direction: str, want, hops: int = 6):
    """Hold `direction` until the bike stops moving. Returns the last cell."""
    for _ in range(hops):
        before = d.pos()
        if before == want:
            return before
        if not d.step_dir(direction):
            return d.pos()
        if d.pos() == before:
            return before
    return d.pos()


def climb_rails(d) -> bool:
    """Ride Southeast's rail staircase from `RAIL_FOOT` up to `RAIL_TOP`."""
    for i, (move, want) in enumerate(RAIL_CLIMB):
        last = i == len(RAIL_CLIMB) - 1
        if move == "^":
            side_jump(d, "UP")
        else:
            ride(d, move, want)
        here = d.pos()
        # The last leg runs east along row 3, which is open floor at the same
        # elevation all the way to x=34 -- so overshooting the nominal landing
        # is the expected outcome and any cell on that row past it is fine.
        good = here == want or (
            last and here[1] == want[1] and here[0] >= want[0])
        if not good:
            log.info("rails: %s ended at %s, wanted %s (%s)",
                     "side-jump" if move == "^" else f"ride {move}",
                     here, want, d.last_step_reason)
            return False
    log.info("rails: at the top %s", d.pos())
    return True


def climb_to_ne(d) -> bool:
    """From anywhere in the Safari Zone, get into the north-east quadrant."""
    if d.map_name() == "SafariZone_Northeast":
        return True
    if d.map_name() != "SafariZone_Southeast":
        if not d.travel("SafariZone_Southeast", on_battle="fight"):
            log.info("ne: could not reach Southeast: %s", d.last_goto_reason)
            return False
    # THE RAILS ARE WALLS TO THE PLANNER FROM HERE ON. Without this the route
    # to the foot of the staircase is planned straight across the staircase.
    d.nav.blocked.setdefault("SafariZone_Southeast", set()).update(SE_RAILS)
    if not d.goto(*RAIL_FOOT, on_battle="fight"):
        log.info("ne: could not reach the rail foot %s: %s",
                 RAIL_FOOT, d.last_goto_reason)
        return False
    if not mount_acro(d):
        return False
    log.info("ne: at %s on the acro bike; riding the staircase", d.pos())
    if not climb_rails(d):
        return False
    # OFF THE BIKE AT THE TOP, for the same reason safari_nw dismounts: a
    # rider cannot fish, and the hunt wants steps spent in grass.
    dismount_acro(d)
    if not d.goto(32, 1, on_battle="fight"):
        log.info("ne: could not cross the north pocket: %s",
                 d.last_goto_reason)
    for _ in range(4):
        if d.map_name() == "SafariZone_Northeast":
            break
        d.step_dir("U")
    arrived = d.map_name() == "SafariZone_Northeast"
    log.info("ne: %s (%s %s)", "ARRIVED" if arrived else "did not arrive",
             d.map_name(), d.pos())
    return arrived


def climb_to_nw(d) -> bool:
    from safari_nw import climb_to_nw as _climb

    return _climb(d)


# ------------------------------------------------------------------- the hunt

class SafariCollector(Collector):
    """`Collector` that plays a Safari encounter itself.

    `Battle.play` cannot: the loop needs `tactics.outlook()` and a move to
    reason about, and a Safari battle has NEITHER -- the engine zeroes the
    player's side of the field (`battle_main.c:3711-3715`) and the menu is
    BALL / POKEBLOCK / GO NEAR / RUN (`bx_battle_menu_t6_2`,
    `src/battle_controller_safari.c:207-228, :485`). Measured on this fork,
    twice: an encounter handed to `d.fight()` sat inside `Battle.play` while
    the frame counter advanced and the player did not move -- the watchdog
    reported "pinned at SafariZone_Northeast (30,27) for 840s", then again for
    730s and 640s, and one 18-minute trip spent 9 of its 424 steps.

    So the four options are driven directly. Those primitives are the
    library's and they are sound (`Battle.safari_ball` / `safari_go_near` /
    `safari_flee`, battle.py:1110-1174, each waiting on the OUTCOME rather
    than on a menu that may never come back); it is only the turn loop around
    them that a Safari battle breaks.

    The policy itself is `Catcher`'s, restated because we are no longer
    handing it a frame: exactly ONE GO NEAR, then throw every turn. The
    ROM's own tables make the first approach the only worthwhile one -- the
    catch bonus falls 4,3,2,1 while the flee penalty stays a flat 4
    (`pret/data/btl_attrs.s:387-391`) -- and there is no weakening phase to
    wait for.
    """

    #: Menu decisions per encounter. A wild mon flees on its own roll
    #: (`safariFleeRate * 5` percent per turn,
    #: battle_ai_script_commands.c:1669-1671), so this only has to outlast 30
    #: balls, never a stalemate.
    SAFARI_TURNS = 48

    #: How often the fallback press below was needed at all, over the whole
    #: run. Zero means the menu always arrived on its own and no input this
    #: script sends can ever be mistaken for a menu answer.
    blind_presses = 0

    def fight(self):
        d = self.d
        for _ in range(80):
            if d.state.battle_ready():
                break
            d.emu.tick(20)
        if not d.in_battle():
            return None
        try:
            safari = bool(d.battle.safari() or d.battle.at_safari_menu())
        except Exception:  # noqa: BLE001 - unreadable type is not a safari
            safari = False
        if not safari:
            return super().fight()
        frame, plan = None, None
        try:
            frame = d.battle_frame()
            plan = self.catcher.plan(frame) if frame else None
        except Exception as exc:  # noqa: BLE001 - never lose a battle here
            log.info("[catch] plan raised: %s", str(exc)[:90])
        enemy = ((frame or {}).get("enemy") or {}).get("species") or "?"
        if plan:
            log.info("[catch] going for it -- %s", plan.reason)
        else:
            log.info("[catch] running from %s: %s", enemy,
                     getattr(plan, "reason", None)
                     or getattr(self.catcher, "last_reason", None)
                     or "not wanted")
        return self.play_safari(bool(plan))

    def play_safari(self, want: bool):
        d, b = self.d, self.d.battle
        approached = False
        thrown = 0
        for _ in range(self.SAFARI_TURNS):
            if not d.in_battle() or b.outcome():
                break
            if b.naming_open():
                b.handle_nickname(None)
                continue
            if not b.at_action_menu():
                # NEVER PRESS A WHILE THE MENU MIGHT BE ARRIVING. Gen 3 battle
                # text advances on its own; the intro (sprite slide, "Wild X
                # appeared!") just takes longer than a short poll. An A press
                # issued in that window is consumed by the four-option box the
                # instant it becomes interactive, and its cursor is zeroed at
                # battle setup (`gActionSelectionCursor[i] = 0`,
                # src/battle_controllers.c:92) -- cursor 0 is BALL
                # (`bx_battle_menu_t6_2`, src/battle_controller_safari.c:207-228).
                #
                # Measured with A: roughly half of the encounters this hunt was
                # told to RUN from ended `B_OUTCOME_CAUGHT` with a Safari Ball
                # spent and `safari_flee` never even called, and one encounter
                # burned NINE balls that way.
                #
                # B is the fix, not a longer sleep: that menu handles A_BUTTON
                # and the d-pad and nothing else, so a stray B is discarded
                # while still dismissing any battle line that does wait on a
                # button.
                if b._wait(lambda: b.at_action_menu() or not b.active()
                           or b.outcome(), timeout_frames=600):
                    continue
                self.blind_presses += 1
                d.emu.run_sequence("B:2 .:10")
                continue
            if not want or d.state.safari_balls() <= 0:
                b.safari_flee()
            elif not approached:
                approached = True
                b.safari_go_near()
            else:
                thrown += 1
                b.safari_ball()
            d.settle(60)
        d.advance_scene(40_000)
        result = {"outcome": b.outcome_name(), "thrown": thrown,
                  "balls": d.state.safari_balls()}
        log.info("[safari] %s after %d ball(s), %d left (%d fallback press(es)"
                 " this run)", result["outcome"], thrown, result["balls"],
                 self.blind_presses)
        return result


def wanted_here(c, area: str) -> set:
    """Species this quadrant can still give us, by internal species id."""
    target_map, _bike = AREAS[area]
    try:
        want = c.missing()
        return {s.species for s in c.target.wild.for_map(target_map)} & want
    except Exception:  # noqa: BLE001
        return {-1}          # unreadable: assume there is still work


def safe_walk(c, path, deadline) -> str:
    """Walk `path`, one step at a time, stopping the instant a battle starts.

    THE SAFARI ZONE CANNOT BE PACED WITH `goto`, and this is the whole reason
    this function exists instead of `Collector.pace_map`.

    When an encounter fires mid-chunk, `step_dir` reports
    "scene-owns-input (gPlayerAvatar.preventStep)", `walk` returns False, and
    `goto` answers that by calling `advance_scene(40000)`
    (pokeagent/trek.py:933-936). That is right for a frozen player and wrong
    for a battle: `advance_scene` presses A whenever the picture stops
    changing, and A on the Safari four-option box is BALL -- its cursor is
    zeroed at battle setup (`gActionSelectionCursor[i] = 0`,
    src/battle_controllers.c:92) and cursor 0 emits the throw
    (`bx_battle_menu_t6_2`, src/battle_controller_safari.c:207-228).

    Traced, not inferred. One `goto` call across Northeast's grass:

        PRESS 'A:4 .:14' balls 30 | advance_scene:680 <- goto:934
        PRESS 'A:4 .:14' balls 29 | advance_scene:680 <- goto:934
        ... 25 presses, six balls gone, and the walk returned with
        tasks ['Task_HandleInput', 'Task_80B64D4', 'Task_NamingScreenMain']

    -- six Safari Balls spent on mons the catcher had never even been asked
    about, one of them caught and waiting to be named. Over a trip that is
    the entire ball budget.

    So the walk is driven here, with `Driver.walk` on one-step chunks: it
    presses the d-pad (which the Safari menu merely moves its cursor with) and
    A only to MOUNT SURF, never to answer a battle. Returns "battle", "left"
    (the trip ended under us), or "done".
    """
    d = c.d
    for step in path:
        if d.in_battle():
            return "battle"
        if not d.state.in_safari():
            return "left"
        if time.time() > deadline:
            return "done"
        d.walk([step])
        if d.in_battle():
            return "battle"
        if d.scene_active():
            # Waits it out and presses NOTHING (trek.py:563-574), which is
            # the distinction `advance_scene` does not make.
            d.drain_scene(6000)
            if d.in_battle():
                return "battle"
    return "done"


def _resolve(c, got: int) -> int:
    """Play the battle in front of us and say whether it added a dex entry."""
    before = c._caught_count()
    c.fight()
    c.d.advance_scene(20_000)
    if c._caught_count() > before:
        c.save()
        return got + 1
    return got


def pace_safari(c, deadline, terrain: str = "grass") -> int:
    """`Collector.pace_map`'s job -- cross this terrain until time runs out."""
    d = c.d
    got = 0
    if terrain == "water":
        # Water is only traversable with this set: it is what makes a
        # land->water step a MOUNT rather than a refused walk.
        d.nav.surfing = True
    cells = c.terrain_cells(terrain)
    if not cells:
        log.info("   no reachable %s on %s", terrain, d.map_name())
        return 0
    log.info("   %d reachable %s cells on %s, nearest %s", len(cells),
             terrain, d.map_name(), cells[0])
    i, blocked = 0, 0
    while time.time() < deadline and d.state.in_safari() and blocked < 8:
        if c.watch.stalled:
            log.info("   abandoning %s: %s", d.map_name(), c.watch.detail)
            c.watch.clear()
            break
        i += 1
        # STAY INSIDE THE PATCH. A wild encounter is rolled per step taken ON
        # the terrain, so a long leg across the map's bare floor spends the
        # Safari's 500-step clock without ever rolling. Picking the far side
        # of the map (`cells[(i * 7) % len(cells)]`, which is what
        # `Collector.pace_map` does) got 5 encounters out of a whole 424-step
        # trip. These are re-sorted from where we are STANDING each round and
        # a near one is taken, so a leg is a few tiles of grass rather than a
        # cross-country walk that happens to end on some.
        px, py = d.pos()
        near = sorted(cells,
                      key=lambda t: abs(t[0] - px) + abs(t[1] - py))
        target = near[2 + (i % 8)] if len(near) > 10 else near[-1]
        if target == d.pos():
            continue
        path = d.nav.find_path(d.map_name(), d.pos(), target, d.elevation())
        if not path:
            blocked += 1
            continue
        what = safe_walk(c, path, deadline)
        if what == "battle":
            got = _resolve(c, got)
            blocked = 0
        elif what == "left":
            break
        else:
            blocked = 0 if d.pos() == target else blocked + 1
    return got


def fish_safari(c, deadline, casts: int = 16) -> int:
    """Cast at the nearest shore. A cast costs no Safari STEP, only balls."""
    d = c.d
    got = 0
    for _ in range(casts):
        if time.time() > deadline or not d.state.in_safari():
            break
        # PLAY THE BATTLE THE LAST CAST STARTED. `Fishing.fish` returns False
        # for a rod that hooked something the caller never answered, and its
        # reason then reads `cast-failed: already in a battle` -- thirteen of
        # those in a row on one trip, every later cast refused while a Safari
        # battle sat open. A live battle is the FIRST thing to clear, not a
        # reason to skip to the next cast.
        if d.in_battle():
            got = _resolve(c, got)
            continue
        spot = c.water_edge()
        if spot is None:
            log.info("   no shore to fish from on %s", d.map_name())
            break
        cell, face = spot
        if d.pos() != cell:
            path = d.nav.find_path(d.map_name(), d.pos(), cell, d.elevation())
            if not path:
                break
            if safe_walk(c, path, deadline) == "battle":
                got = _resolve(c, got)
                continue
        if d.pos() != cell:
            continue
        if d.facing() != face:
            # TURN, DO NOT STEP: a held key walks onto the shore cell instead
            # of aiming the rod at the water.
            d.emu.run_sequence(f"{face}:4 .:12")
            if d.facing() != face:
                continue
        if not d.fish():
            if d.last_fish_reason == "no-rod":
                break
            if d.in_battle():
                got = _resolve(c, got)
            continue
        got = _resolve(c, got)
    return got


def hunt(c, area: str, deadline) -> int:
    """Spend the rest of the trip on this quadrant's encounter terrain.

    Sliced, and re-checked between slices, because the trip can END under the
    hunt's feet: at zero steps `gUnknown_081C3448` warps the player back to
    the gate (safari_zone.inc:25-32), and the pacer would then keep planning
    routes on a map we are no longer standing on.
    """
    d = c.d
    got = 0
    terrains = ["grass"]
    # WHICH TERRAIN IS WORTH SPENDING THE TRIP ON, per quadrant, from the
    # encounter tables rather than habit:
    #   nw  GOLDUCK is 5% of its SURF table and appears on no other reachable
    #       water, and SEAKING is 20% of its SUPER ROD table.
    #   sw  the same 20% SEAKING rod slot, but its SURF table is 100%
    #       PSYDUCK -- so surfing here is pure step cost for a species we
    #       already hold. Rod only.
    # A cast costs NO step (only balls), which makes "rod" the cheapest
    # terrain in the zone wherever it is offered.
    terrains += {"nw": ["rod", "water"], "sw": ["rod"]}.get(area, [])
    # A ROUND THAT SPENDS NO STEPS IS A WEDGE, NOT BAD LUCK. Measured: a
    # Northwest trip pinned at (6,30) and every `goto` came back "journey
    # budget spent at (6, 30) heading for (24, 14)", so `pace_map` burned six
    # walks, returned, and this loop immediately asked for six more -- twenty
    # minutes of churn with the step counter frozen at 282. An open menu eats
    # movement input (AGENTS.md gotcha 7), so try clearing one; if the counter
    # still has not moved, the trip is over and re-entering is cheaper than
    # pressing at a wall.
    stuck = 0
    while time.time() < deadline:
        if not d.state.in_safari() or not d.map_name().startswith("SafariZone"):
            log.info("hunt: the trip ended (%s at %s)", counters(d),
                     d.map_name())
            break
        left = wanted_here(c, area)
        if not left:
            log.info("hunt: this quadrant owes nothing more")
            break
        steps_before = d.state.safari_steps()
        for terrain in terrains:
            if time.time() >= deadline or not d.state.in_safari():
                break
            slice_end = min(deadline, time.time() + 180.0)
            got += (fish_safari(c, slice_end) if terrain == "rod"
                    else pace_safari(c, slice_end, terrain))
        if d.state.in_safari() and d.state.safari_steps() >= steps_before:
            stuck += 1
            log.info("hunt: a whole round spent no steps (%s at %s) -- "
                     "clearing input (attempt %d)", counters(d), d.pos(),
                     stuck)
            unwedge(d)
            d.close_menus()
            if stuck >= 2:
                log.info("hunt: still pinned at %s -- ending the trip",
                         d.pos())
                break
        else:
            stuck = 0
    return got


def to_area(d, target_map: str, tries: int = 6) -> bool:
    """Walk into an adjoining Safari quadrant on foot.

    The four sub-maps are joined by MAP CONNECTIONS, not warps -- only
    Southeast has a warp_event at all -- so there is no door to take: you
    cross the seam by stepping over it. `travel` knows how to do that; it just
    needs to be allowed to try more than once, because a wild encounter
    interrupts the crossing and leaves the player mid-seam.
    """
    for _ in range(tries):
        if d.map_name() == target_map:
            return True
        try:
            d.travel(target_map)
        except Exception as exc:            # noqa: BLE001
            log.info("  to_area: %s", str(exc)[:80])
        if d.in_battle():
            d.fight()
    return d.map_name() == target_map


def trip(d, c, area: str, minutes: float) -> int:
    """One Safari entry: 30 balls and 500 steps, start to eject."""
    target_map, _bike = AREAS[area]
    if not d.map_name().startswith("SafariZone"):
        if not d.flight.flyable_here():
            d.flight.step_outside()
        if not d.map_name().startswith(("Route121", "Lilycove")):
            d.fly_to("LilycoveCity")
        if not reach_gate(d, c):
            log.info("never reached %s (at %s)", GATE, d.map_name())
            return 0
        if not enter(d):
            log.info("could not get inside (at %s, %s)", d.map_name(),
                     counters(d))
            return 0
    log.info("INSIDE: %s %s | %s", d.map_name(), d.pos(), counters(d))

    if area == "sw":
        # No rail, no slope, no bike: the southwest quadrant is ordinary
        # ground from the entrance, so walking there IS the climb.
        if d.map_name() != target_map and not to_area(d, target_map):
            log.info("could not walk to %s | %s", target_map, counters(d))
            return 0
    else:
        climb = climb_to_ne if area == "ne" else climb_to_nw
        if not climb(d):
            log.info("could not reach %s | %s", target_map, counters(d))
            return 0
    log.info("in %s with %s", d.map_name(), counters(d))

    deadline = time.time() + minutes * 60.0
    got = hunt(c, area, deadline)
    log.info("trip over: %s at %s %s", counters(d), d.map_name(), d.pos())
    return got


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--area", choices=sorted(AREAS), required=True)
    ap.add_argument("--trips", type=int, default=1,
                    help="Safari entries. Each is 500 money for 30 balls and "
                         "500 steps (safari_zone.c:62-63).")
    ap.add_argument("--minutes", type=float, default=20.0,
                    help="wall-clock cap per trip; the 500-step counter "
                         "usually ends the trip first")
    ap.add_argument("--feed", default=None,
                    help="LiveFeed name. Defaults to the state file's stem, "
                         "which matters: Collector's StallWatch falls back to "
                         "the feed called 'default', and watching a feed some "
                         "OTHER run owns made this abandon Safari maps with "
                         "'pinned at EverGrandeCity_GlaciasRoom' -- a map this "
                         "run was nowhere near.")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    d.advance_scene(40_000)
    # A post-Champion save sits on the TITLE SCREEN with its SaveBlock intact:
    # map, party and dex all read normally and every step is refused.
    unwedge(d)
    log.info("start %s %s | bike %s | money %s", d.map_name(), d.pos(),
             held_bike(d) or "none", d.state.money())

    target = dexmod.DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    before, _ = target.dex_flags(d.state)
    log.info("dex %d caught", len(before))
    _map, bike = AREAS[a.area]
    # ASK WHAT THIS QUADRANT STILL OWES *THIS SAVE* BEFORE SPENDING ANYTHING.
    # A trip is 500 money, 500 steps and up to 20 minutes of wall clock, and
    # I spent two of them on Southwest before checking: a peer had reported
    # SEAKING and GOLDUCK as "did not land", which was true of THEIR fork and
    # false of the canonical line, where both were already registered. The
    # hunt loop was right to catch nothing; the run should never have started.
    # A fork-relative claim about what is missing is not evidence about this
    # state -- the dex flags in THIS save are.
    owed = {d.names.species(r.species)
            for r in target.wild.for_map(_map)
            if r.species in set(target.missing())}
    if not owed:
        log.info("SKIP: %s (%s) owes this save NOTHING -- not paying 500 money "
                 "and 500 steps to confirm it", a.area, _map)
        return 1
    log.info("%s owes %s", a.area, sorted(owed))
    # A quadrant with no bike requirement must not be forced through Rydel's:
    # the exchange is a real errand (fly to Mauville, walk in, swap, come
    # back) and swap_bike(None) would have read as "want no bike" and failed.
    if bike and not swap_bike(d, bike):
        log.info("FAIL: %s needs the %s", a.area, bike)
        return 1
    if not bike:
        log.info("%s needs no bike (holding %s)", a.area, held_bike(d) or "none")

    c = SafariCollector(d, per_map=a.minutes * 60.0,
                        feed_name=a.feed or Path(a.state).stem)
    got = 0
    for n in range(a.trips):
        log.info("=== trip %d/%d", n + 1, a.trips)
        got += trip(d, c, a.area, a.minutes)
        if a.out:
            d.save(a.out)

    after, _ = target.dex_flags(d.state)
    gained = sorted(set(after) - set(before))
    names = []
    for nat in gained:
        entry = next((e for e in target.achievable
                      if getattr(e, "natdex", None) == nat), None)
        names.append(d.names.species(entry.species) if entry else str(nat))
    log.info("RESULT dex %d -> %d | caught %d | new %s (natdex %s)",
             len(before), len(after), got, names, gained)
    if a.out:
        d.save(a.out)
        log.info("banked %s", a.out)
    return 0 if len(after) > len(before) else 1


if __name__ == "__main__":
    raise SystemExit(main())
