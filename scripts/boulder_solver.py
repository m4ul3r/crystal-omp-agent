#!/usr/bin/env python
"""Solve a Strength-boulder room offline, then walk the answer.

Seafloor Cavern Room2 holds seven pushable boulders and two breakable rocks.
The savestate search cannot do it: every node costs a savestate write, so
3,000 nodes is forty minutes, and its only moves are the four steps -- it can
never smash a rock and it deduped a shoved boulder as the same world until the
signature was fixed. Offline the same search is microseconds a node.

The push rule is the engine's, from `sub_8058F6C`
(pret/src/field_player_avatar.c:639-655): with FLAG_SYS_USE_STRENGTH set,
stepping into a PUSHABLE_BOULDER moves it one tile in the same direction if
`GetCollisionAtCoords` says that tile is free, and the player takes the cell
the boulder left.
"""
import argparse
import json
import logging
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver  # noqa: E402

log = logging.getLogger("boulders")

DIRS = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}
BOULDER_GFX = 87
ROCK_GFX = 86  # OBJ_EVENT_GFX_BREAKABLE_ROCK -- removable with Rock Smash


#: Elevations that match anything. `ObjectEventUpdateZCoord` keeps your
#: current level when either side is 15, and level 0 lets you step onto any
#: level at all -- that is how the game changes elevation.
_ANY_ELEVATION = (0, 15)


def snapshot(d, elevation_filter=False):
    """`(walls, boulders, others)` for the map we are standing on.

    `elevation_filter` is OFF by default, and that is a deliberate,
    load-bearing compromise. Elevation genuinely belongs in the wall set --
    see the Victory Road 1F case below -- but a STATIC filter keyed on the
    player's current level is too strict: level 0 is a wildcard that lets you
    change level, and B1F's verified route to (30,25) does exactly that. Fixed
    properly, `solve` would carry z in its search state the way `nav.step`
    already does; until then, switching this on trades a working B1F crossing
    for a correct 1F model. The tests pin both behaviours.

    ELEVATION AS A WALL. snapshot used to be collision-only, so the
    solver happily planned across elevation seams that both `nav.step` and the
    engine refuse -- and that single omission is what made Victory Road look
    impossible. On 1F the row y=25 is elevation 15 (a bridge, which carries
    whatever level you arrive with) flanked by elevation-4 cells at (6,25) and
    (10,25), while y=24 above it is elevation 3. Arriving from the 4 side you
    cross the bridge still carrying 4, and 4 -> 3 is an illegal seam: the
    engine refused (7,24), (8,24) and (9,24) every single time, which read as
    an inexplicable wall and got mistaken for a decoded-grid error.
    """
    name = d.map_name()
    grid = d.nav.grid(name)
    try:
        z = d.elevation()
    except Exception:  # noqa: BLE001 - no avatar, no elevation filter
        z = 0
    walls = set()
    for y, row in enumerate(grid):
        for x, cell in enumerate(row):
            if cell is None or cell.collision:
                walls.add((x, y))
            elif elevation_filter and z \
                    and cell.elevation not in _ANY_ELEVATION \
                    and cell.elevation != z:
                walls.add((x, y))
    elev = {(x, y): (cell.elevation if cell is not None else 0)
            for y, row in enumerate(grid) for x, cell in enumerate(row)}
    boulders, others, rocks = set(), set(), set()
    for o in d.live_npcs():
        if o.get("player"):
            continue
        gfx = o.get("graphics_id")
        cell = (o["x"], o["y"])
        if gfx == BOULDER_GFX:
            boulders.add(cell)
        elif gfx == ROCK_GFX:
            # NOT A WALL. A breakable rock is one A press away from gone, and
            # treating it as permanent is what made Room2 look unsolvable: the
            # rock at (7,6) sits on rung 6 between the corridor and the boulder
            # at (6,6), which is the only way up to the north-west door.
            rocks.add(cell)
        else:
            others.add(cell)
    # THE ENGINE ONLY LOADS OBJECTS NEAR THE CAMERA. Arriving in Room2 shows
    # two of its seven boulders, so a plan built from the live list alone walks
    # straight into one it cannot see and stalls -- which is what every
    # abandoned Room2 plan actually was. Fold in the map's own boulder cells
    # for the ones off-screen. Over-approximating is safe: the worst case is a
    # detour around a boulder that has already been pushed away, and the plan
    # is rebuilt every step as more of the room loads.
    try:
        for obj in (d.nav.info(name).objects or []):
            script = str(obj.get("script") or "")
            cell = (obj["x"], obj["y"])
            if _near(cell, d.pos()):
                continue
            if script == "S_PushableBoulder" and cell not in boulders:
                boulders.add(cell)
            elif script == "S_BreakableRock" and cell not in rocks:
                rocks.add(cell)
    except Exception:  # noqa: BLE001
        pass
    return walls, frozenset(boulders), frozenset(others), frozenset(rocks), \
        elev


def warp_cells(d) -> frozenset:
    """Door tiles. A boulder cannot be pushed onto one.

    Seafloor Cavern Room8 puts a boulder on (5,3), directly below the door at
    (5,2), and the only approach to that door is (5,3) -- so the planner kept
    choosing to shove the boulder up onto the warp, which the engine refuses.
    The player may of course walk onto a warp; only the push is illegal.
    """
    try:
        # Warp is a dataclass, not a dict -- subscripting it silently raised
        # and the except swallowed it, so every warp read as "no warps".
        return frozenset((w.x, w.y)
                         for w in (d.nav.info(d.map_name()).warps or []))
    except Exception:  # noqa: BLE001
        return frozenset()


def _near(cell, pos, radius=16):
    """Inside the window the engine keeps object events loaded in."""
    return abs(cell[0] - pos[0]) <= radius and abs(cell[1] - pos[1]) <= radius


def _step_z(z, from_e, dest_e):
    """The level you are on after the step, or None if the engine refuses it.

    Straight from `nav.step` (:608) and `ObjectEventUpdateZCoord`: a concrete
    destination level that differs from the one you are carrying is illegal,
    and 0/15 are the wildcards -- 15 keeps your level (a bridge), 0 lets you
    take any level next.
    """
    if z and dest_e not in _ANY_ELEVATION and dest_e != z:
        return None
    if dest_e == 15 or from_e == 15:
        return z
    return dest_e


def solve(walls, boulders, others, start, targets, limit=400_000,
          may_push=True, rocks=frozenset(), elev=None, start_z=0):
    """Shortest move string from `start` to any cell in `targets`.

    `may_push=False` treats boulders as walls. Always try that first: a shoved
    boulder is not undoable, and the shortest path will happily push one into a
    corner for one saved step. Room2 was walked into an unsolvable arrangement
    exactly that way -- boulders stacked at (6,6),(6,7) with the route gone.

    `elev` (a ``{cell: elevation}`` map) puts the player's LEVEL in the search
    state, which is the difference between a route the engine will walk and
    one it refuses. Victory Road 1F's y=25 row is an elevation-15 bridge
    flanked by elevation-4 cells with elevation-3 ground above it: crossing
    from the 4 side you keep level 4, and 4 -> 3 is illegal. A static filter
    cannot express that -- switching one on broke B1F, whose real route
    changes level through a wildcard tile -- but carrying `z` can, because it
    knows which level you are on when you arrive. Omit `elev` for the old
    level-blind behaviour.
    """
    goal = set(targets)
    z0 = start_z if elev else 0
    seen = {(start, boulders, z0)}
    queue = deque([(start, boulders, z0, "")])
    while queue:
        pos, boulders2, z, path = queue.popleft()
        if pos in goal:
            return path
        if len(seen) > limit:
            break
        for mv, (dx, dy) in DIRS.items():
            nxt = (pos[0] + dx, pos[1] + dy)
            if nxt in walls or nxt in others:
                continue
            nz = z
            if elev is not None:
                # OFF THE GRID IS A WALL. `elev.get(cell, 0)` would hand an
                # out-of-bounds cell elevation 0 -- the wildcard -- so the
                # search happily stepped outside the map and back in again,
                # walking round any seam it liked.
                if nxt not in elev:
                    continue
                nz = _step_z(z, elev.get(pos, 0), elev[nxt])
                if nz is None:
                    continue
            rocks2 = boulders2
            if nxt in boulders2:
                if not may_push:
                    continue
                beyond = (nxt[0] + dx, nxt[1] + dy)
                # A BOULDER CANNOT BE PUSHED INTO A ROCK, though the PLAYER may
                # walk into one and smash it. The engine checks
                # GetCollisionAtCoords on the far tile and a breakable rock is
                # an object standing there. Missing that asymmetry made the
                # planner keep choosing a five-move route that shoves Room2's
                # (6,6) boulder east into the rock at (7,6) -- a push the game
                # refuses -- instead of the long way round that actually works.
                if (beyond in walls or beyond in boulders2 or beyond in others
                        or beyond in rocks):
                    continue
                rocks2 = frozenset((boulders2 - {nxt}) | {beyond})
            state = (nxt, rocks2, nz)
            if state in seen:
                continue
            seen.add(state)
            queue.append((nxt, rocks2, nz, path + mv))
    return None


#: One belief per map, kept for the life of the process. Re-seeding from the
#: map defaults on every call was minting phantoms: after pushing (6,10) to
#: (6,9) and (11,14) to (11,13), a fresh seed put the originals BACK, and the
#: planner saw nine boulders in a room that has seven. Every real route was
#: then "unsolvable".
_BELIEF = {}
_LAST_MAP = [None]


#: Cells the ENGINE refused even though the decoded grid calls them open,
#: learned per map at runtime and REMEMBERED ACROSS RUNS.
#:
#: Victory Road refuses a whole class of ordinary steps -- (7,25), (8,25),
#: (9,25), (9,26), (16,34), (16,35) on 1F alone, none of them holding an
#: object -- so each crossing spent its budget rediscovering the same walls
#: and never got far enough to use the knowledge. On disk the map simply gets
#: more accurate every run.
_LEARNED_WALLS: dict = {}
_WALLS_PATH = Path(__file__).resolve().parents[1] / "data" / "learned_walls.json"


def _load_walls() -> None:
    if _LEARNED_WALLS:
        return
    try:
        raw = json.loads(_WALLS_PATH.read_text())
    except Exception:  # noqa: BLE001 - no file yet is the normal first run
        return
    for name, cells in (raw.get("walls", raw)).items():
        _LEARNED_WALLS[name] = {tuple(c) for c in cells}
    for name, cells in (raw.get("no_push") or {}).items():
        _NO_PUSH[name] = {tuple(c) for c in cells}


def _save_walls() -> None:
    try:
        _WALLS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _WALLS_PATH.write_text(json.dumps({
            "walls": {k: sorted(map(list, v))
                      for k, v in _LEARNED_WALLS.items()},
            "no_push": {k: sorted(map(list, v))
                        for k, v in _NO_PUSH.items()},
        }, indent=0))
    except Exception:  # noqa: BLE001 - never lose a run to bookkeeping
        pass


#: Cells the engine refuses to accept a BOULDER on, learned at runtime. This
#: is a different fact from "the player cannot walk here": Victory Road B1F
#: takes a boulder push down from (4,7) and refuses the landing at (4,8), and
#: recording that as a wall made the alcove at (4,6) unsolvable for walking
#: too -- which killed an 83-move plan that was otherwise fine.
_NO_PUSH: dict = {}


def learned_no_push(map_name: str) -> frozenset:
    _load_walls()
    return frozenset(_NO_PUSH.get(map_name, ()))


def note_no_push(map_name: str, cell) -> None:
    _load_walls()
    _NO_PUSH.setdefault(map_name, set()).add(tuple(cell))
    _save_walls()


def learned_walls(map_name: str) -> frozenset:
    _load_walls()
    return frozenset(_LEARNED_WALLS.get(map_name, ()))


#: How many independent refusals a cell needs before it is believed permanent.
#: One is not enough: three separate transient causes have each masqueraded as
#: terrain here (a body standing on the tile, a water tile we failed to mount,
#: a scene owning input), and every one of them killed a crossing that had
#: already been proven to work. Corroboration is cheap; a false permanent fact
#: is not.
WALL_CONFIRMATIONS = 3

#: Refusal tallies for this process only. Deliberately NOT persisted -- the
#: whole point is that a single sighting is not evidence.
_WALL_HITS: dict = {}


def note_wall(map_name: str, cell) -> None:
    """Remember a cell the engine refuses.

    The decoded grid and the engine disagree on real maps -- Victory Road B1F
    refused plain steps into (5,10) and (9,11), both of which `nav` calls
    walkable and neither of which holds an object. Without this the planner
    re-planned into the same wall every attempt and the walk died in the same
    three places. One refusal, one wall, and the next plan routes around it.
    """
    _load_walls()
    key = (map_name, tuple(cell))
    _WALL_HITS[key] = _WALL_HITS.get(key, 0) + 1
    if _WALL_HITS[key] < WALL_CONFIRMATIONS:
        log.info("  %s refused (%d/%d) -- not believing it yet", cell,
                 _WALL_HITS[key], WALL_CONFIRMATIONS)
        return
    before = len(_LEARNED_WALLS.setdefault(map_name, set()))
    _LEARNED_WALLS[map_name].add(tuple(cell))
    if len(_LEARNED_WALLS[map_name]) != before:
        _save_walls()


def belief(d):
    """Where the boulders are, as best we can know.

    `live_npcs()` only reports objects near the CAMERA -- two of Room2's seven
    on arrival -- so this seeds from the map once and then trusts what it can
    see, plus the pushes we make ourselves (recorded by `walk`).
    """
    name = d.map_name()
    # RE-ENTERING A ROOM RESETS ITS BOULDERS. Verified live: Room1's boulder
    # sat at (7,11) after two pushes, and stepping out to the Entrance and
    # back put it at (5,11), its map default. So a wedged room is recoverable
    # -- but only if the belief is thrown away with it.
    if _LAST_MAP[0] != name:
        _BELIEF.pop(name, None)
        _LAST_MAP[0] = name
    if name not in _BELIEF:
        _BELIEF[name] = {
            (o["x"], o["y"]) for o in (d.nav.info(name).objects or [])
            if str(o.get("script") or "") == "S_PushableBoulder"}
    seen = {(o["x"], o["y"]) for o in d.live_npcs()
            if not o.get("player") and o.get("graphics_id") == BOULDER_GFX}
    # Inside the loaded window the engine is authoritative; outside it, keep
    # what we believe.
    total = len({(o["x"], o["y"]) for o in (d.nav.info(name).objects or [])
                 if str(o.get("script") or "") == "S_PushableBoulder"})
    if len(seen) >= total:
        # The whole room is on camera: believe it outright rather than merging
        # stale defaults back in.
        _BELIEF[name] = set(seen)
    else:
        # ABSENCE IS NOT MOVEMENT. A boulder only ever moves because we shoved
        # it, and `note_push` records that. Dropping believed boulders merely
        # because they are off-camera -- `_near`'s radius is 16, far wider
        # than the engine's object window -- emptied the model mid-walk:
        # Victory Road B1F went from seven boulders to three at (15,13) and
        # the next plan was computed against a room that did not exist.
        _BELIEF[name] = set(_BELIEF[name]) | seen
    return frozenset(_BELIEF[name])


#: Rocks we have actually smashed, per map. A smashed rock stays gone until
#: the map reloads, and the reload clears this with the belief.
_ROCKS: dict = {}


def rock_belief(d) -> frozenset:
    """Where the BREAKABLE ROCKS are, as best we can know.

    Rocks used to come from `live_npcs()` alone, which only reports objects
    near the CAMERA. So a rock the planner could not see was simply not in the
    model, and the plan walked straight into it: Victory Road B1F failed
    identically every run with

        step R refused at (17,12) (wanted (18,12), pushing=False)

    and (18,12) is `S_BreakableRock` in the map's own object table, four
    screens from where the plan was made. Seeded from the map, corrected by
    what the camera can see, minus what we have smashed ourselves.
    """
    name = d.map_name()
    static = {(o["x"], o["y"]) for o in (d.nav.info(name).objects or [])
              if str(o.get("script") or "") == "S_BreakableRock"}
    if _LAST_MAP[0] != name:
        _ROCKS.pop(name, None)
    seen = {(o["x"], o["y"]) for o in d.live_npcs()
            if not o.get("player") and o.get("graphics_id") == ROCK_GFX}
    gone = _ROCKS.setdefault(name, set())
    # NEVER INFER "GONE" FROM ABSENCE. `_near`'s radius is wider than the
    # engine's actual object window, so a rock that is merely off-camera looks
    # identical to one that has been smashed -- and treating it as smashed
    # deleted the exact rock this belief exists for: (18,12) was seeded from
    # the map, then dropped because it was not in `live_npcs()`, and the plan
    # walked into it every single run. Only a smash WE made counts, and a map
    # reload clears the record along with the boulders.
    return frozenset((static | seen) - gone)


def note_smash(d, cell) -> None:
    _ROCKS.setdefault(d.map_name(), set()).add(cell)


def note_push(d, frm, to):
    """Record a push we just made, so the belief stays true off-camera."""
    name = d.map_name()
    if name in _BELIEF:
        _BELIEF[name] = (set(_BELIEF[name]) - {frm}) | {to}


FACE = {(0, 1): "U", (0, -1): "D", (1, 0): "L", (-1, 0): "R"}


def _rock_gone(d, cell) -> bool:
    return cell not in {(o["x"], o["y"]) for o in d.live_npcs()
                        if o.get("graphics_id") == ROCK_GFX}


def _smash(d, cell) -> bool:
    """Rock Smash a rock this room's own geometry hides.

    `Driver.smash_rock` routes with plain `goto`, which has no model of a
    pushable boulder -- so in a boulder room it reports "could not reach or
    face the rock" for a rock that is merely on the far side of one. The way
    to a rock here is the same planner that walks everything else.
    """
    for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        spot = (cell[0] + dx, cell[1] + dy)
        c = d.nav.cell(d.map_name(), *spot)
        if c is None or c.collision:
            continue
        # ROUTE TO THE ROCK WITHOUT PLANNING THROUGH ROCKS. Now that a
        # smashable rock is walkable to the planner, `smashing=True` here made
        # the approach plan straight into another rock, which called _smash,
        # which called walk -- a recursion that blew the stack on Victory Road
        # B1F. Getting NEXT TO a rock never requires removing one.
        if d.pos() != spot and not walk(d, spot, tries=4, smashing=False):
            continue
        d.emu.run_sequence(f"{FACE[(dx, dy)]}:4 .:20")
        for _ in range(3):
            d.emu.run_sequence("A:4 .:40")
            d.advance_scene(60000)
            if d.in_battle():
                d.fight(policy=Driver.damage_first)
                d.advance_scene(40000)
            if _rock_gone(d, cell):
                log.info("  smashed %s from %s", cell, spot)
                return True
        d.close_menus()
    return False


def _boulder_at(d, cell) -> bool:
    """Is there a boulder on `cell` RIGHT NOW?

    The cell is always adjacent to the player when this is asked, so it is
    inside the engine's object window and `live_npcs()` is authoritative --
    unlike the belief, which is a plan-time snapshot and can be stale by the
    time the move is executed. Keying "am I pushing?" on the stale belief let
    the engine shove a boulder the executor did not know about, so the push
    guard was never consulted and Victory Road B1F sealed its own route.
    """
    return any((o["x"], o["y"]) == tuple(cell)
               and o.get("graphics_id") == BOULDER_GFX
               and not o.get("player")
               for o in d.live_npcs())


def _push_keeps_a_door(d, map_name, walls, boulders, block, pos, delta,
                       target=None, rocks=frozenset()):
    """Would shoving the boulder ahead of `pos` keep the run alive?

    Two questions, and the second one matters more:

    * is a warp still reachable (the floor stays RECOVERABLE), and
    * is `target` still reachable (the ROUTE survives)?

    Asking only the first let Victory Road B1F shove (4,7) west to (3,7) --
    perfectly recoverable, a door was still reachable -- and then discover
    (30,25) had become unreachable, so an 83-move plan died on its first move
    and re-planned straight into "no solution".
    """
    dx, dy = delta
    ahead = (pos[0] + dx, pos[1] + dy)
    landing = (ahead[0] + dx, ahead[1] + dy)
    after = frozenset((set(boulders) - {ahead}) | {landing})

    if target is not None:
        # The route itself. Allow further pushes -- a plan is entitled to shove
        # more than one boulder -- but bound the search so the check stays
        # cheap next to the plan that produced it.
        if solve(walls, after, block, ahead, [target], rocks=rocks,
                 limit=300_000) is None:
            return False

    doors = warp_cells(d)
    if not doors:
        return True
    return solve(walls, after, block, ahead, doors,
                 may_push=False, limit=200_000) is not None


def walk(d, target, tries=8, smashing=False) -> bool:
    """Plan once against a coherent belief, walk it, re-plan only on surprise."""
    for attempt in range(tries):
        if d.pos() == target:
            return True
        for _ in range(4):
            if not d.scene_active() and not d.in_battle():
                break
            d.advance_scene(90000)
            if d.in_battle():
                d.fight(policy=Driver.damage_first)
                d.advance_scene(60000)
            elif d.scene_active():
                # A BOX NOBODY ANSWERED. advance_scene walks dialogue forward;
                # it does not dismiss a description panel left open by a failed
                # item use ("Restores the HP of a POKeMON by 200 points."), and
                # an open box eats every movement press. That one panel cost
                # fifteen minutes of a walk replanning the same three moves.
                d.close_menus()
        if not d.state.flag("FLAG_SYS_USE_STRENGTH"):
            d.use_strength()
        walls, _live, others, _live_rocks, elev = snapshot(d)
        walls = set(walls) | set(learned_walls(d.map_name()))
        known = belief(d)
        rocks = rock_belief(d)
        # A BREAKABLE ROCK IS ONLY A WALL WHEN WE CANNOT SMASH IT. This read
        # `others | rocks if smashing else others`, which is exactly backwards:
        # with Rock Smash available it treated every rock as permanent, and
        # without it let the planner walk straight through them. Victory Road
        # B1F is gated by the rock at (18,12), so with the belief finally
        # complete the solver answered "no solution" to a route the executor
        # can open with one A press.
        block = others if smashing else (others | rocks)
        # Push destinations that are illegal but walkable: breakable rocks and
        # door tiles both refuse a boulder while accepting the player.
        nopush = rocks | warp_cells(d) | learned_no_push(d.map_name())
        # PUSH-FREE FIRST and cheaply; only then pay for the full push search.
        # Room3's nine boulders need about a million states -- the 400,000
        # default found "no solution" for a route that exists in 21 moves.
        z = d.elevation()
        plan = solve(walls, known, block, d.pos(), [target], may_push=False,
                     rocks=nopush, elev=elev, start_z=z)
        if plan is None:
            plan = solve(walls, known, block, d.pos(), [target], rocks=nopush,
                         limit=4_000_000, elev=elev, start_z=z)
        if plan is None:
            log.info("  no solution to %s from %s (boulders %s)", target,
                     d.pos(), sorted(known))
            return False
        log.info("  plan %d: %d moves %s -> %s", attempt, len(plan), d.pos(),
                 target)
        here = d.map_name()
        for mv in plan:
            dx, dy = DIRS[mv]
            before = d.pos()
            ahead = (before[0] + dx, before[1] + dy)
            pushing = ahead in known or _boulder_at(d, ahead)
            if pushing:
                # THE FAR SIDE MATTERS TOO. The planner treats a breakable rock
                # as removable, so it will happily plan a push into one -- but
                # the engine checks GetCollisionAtCoords on that tile and
                # simply refuses. Room2's boulder at (6,6) is pushed east into
                # exactly such a rock at (7,6).
                beyond = (ahead[0] + dx, ahead[1] + dy)
                if beyond in rocks:
                    if not _smash(d, beyond):
                        log.info("  could not smash %s behind %s: %s", beyond,
                                 ahead, getattr(d, "last_field_reason", None))
                        break
                    note_smash(d, beyond)
                    rocks = frozenset(rocks - {beyond})
            if ahead in rocks:
                if not _smash(d, ahead):
                    log.info("  could not smash %s: %s", ahead,
                             getattr(d, "last_field_reason", None))
                    break
                note_smash(d, ahead)
                rocks = frozenset(rocks - {ahead})
            if pushing and not _push_keeps_a_door(
                    d, here, walls, known, block, before, (dx, dy),
                    target=target, rocks=nopush):
                # A SHOVE IS FOREVER. Sokoban pushes cannot be undone, and a
                # floor whose last door has just been sealed can only be reset
                # through a door it can no longer reach -- terminal, with no
                # Escape Rope in the bag. Victory Road B1F stranded the run at
                # (4,10) exactly this way, after which all seven of its doors
                # were unreachable and every later attempt failed instantly.
                log.info("  refusing push at %s %s: it seals the last door",
                         ahead, mv)
                note_wall(here, ahead)
                break

            # MOUNT SURF WHEN THE PLAN CROSSES WATER. `snapshot` calls a cell
            # a wall only when `collision` is set, and water has collision 0 --
            # so the planner has always been happy to route across water the
            # player cannot walk onto. Every one of those steps was refused and
            # then learned as a wall, which is why Victory Road's water-linked
            # halves looked disconnected. Stepping off water onto land needs no
            # help; the engine dismounts on its own.
            if not pushing and not d.is_surfing():
                cell_ahead = d.nav.cell(here, *ahead)
                if cell_ahead is not None and d.nav._is_water(cell_ahead):
                    if not d._mount_surf(mv):
                        log.info("  could not mount Surf at %s facing %s",
                                 before, mv)
                        note_wall(here, ahead)
                        break
                    if d.pos() == ahead:
                        continue
            d.step_dir(mv)
            d.settle(30)
            if d.in_battle():
                d.fight(policy=Driver.damage_first)
                d.advance_scene(40000)
            if d.map_name() != here:
                return d.pos() == target
            if d.pos() != ahead:
                # SAY WHY. A silent break here turned every divergence into an
                # identical "no solution" one attempt later, with nothing in
                # the log to separate a refused push from a scene stealing the
                # input or a ledge one-way.
                log.info("  step %s refused at %s (wanted %s, pushing=%s): %s",
                         mv, before, ahead, pushing, d.last_step_reason)
                if not pushing and not d.scene_active():
                    # Only a plain step tells us about the TILE; a refused
                    # push is about the boulder's far side, not this cell.
                    #
                    # NEVER LEARN WATER. A water refusal means we failed to
                    # mount Surf, not that the tile is solid, and a false wall
                    # is permanent once written to disk. Learning water this
                    # way -- before the Surf model existed -- walled off
                    # Victory Road B1F at (25,10)/(26,10)/(32,10) and turned a
                    # working crossing into an instant "no solution".
                    cell_ahead = d.nav.cell(here, *ahead)
                    body = any((o["x"], o["y"]) == tuple(ahead)
                               and not o.get("player")
                               for o in d.live_npcs())
                    if body:
                        # A TRAINER IS NOT TERRAIN. Victory Road B1F has three
                        # wandering trainers, and one standing on (8,7) refuses
                        # the step exactly like a wall does -- but it walks
                        # away a second later. Writing that to disk as
                        # permanent made the whole floor unsolvable and
                        # contradicted a crossing that had already been proven.
                        log.info("  %s is occupied by a body, not learning it",
                                 ahead)
                    elif cell_ahead is None or not d.nav._is_water(cell_ahead):
                        note_wall(here, ahead)
                elif pushing:
                    # A REFUSED PUSH IS ABOUT THE LANDING, NOT THIS TILE --
                    # but ONLY when the shove was legal to begin with. With
                    # FLAG_SYS_USE_STRENGTH clear the engine refuses every
                    # push regardless of what is behind the boulder, and
                    # `note_no_push` writes to disk permanently and shared.
                    # Shoal Cave's Lower Room is how this was found: its
                    # boulder at (25,3) only spawns near the camera, so
                    # `use_strength` (which enumerates LIVE objects) answers
                    # "no boulder on this map" from across the room, the walk
                    # arrives at (24,3) with strength still off, the push is
                    # refused, and (26,3) -- the only legal landing, and the
                    # only way to the Ice Room ladder -- was learned as
                    # impossible. Every later solve then answered "no
                    # solution" instantly, for this process and every one
                    # after it.
                    if d.state.flag("FLAG_SYS_USE_STRENGTH"):
                        note_no_push(here, (ahead[0] + dx, ahead[1] + dy))
                    else:
                        log.info("  push refused with STRENGTH off -- not "
                                 "learning %s",
                                 (ahead[0] + dx, ahead[1] + dy))
                if d.scene_active():
                    d.advance_scene(90000)
                    if d.in_battle():
                        d.fight(policy=Driver.damage_first)
                        d.advance_scene(60000)
                break
            if pushing:
                # We made this move, so we know exactly where it went -- even
                # once it scrolls off camera.
                moved_to = (ahead[0] + dx, ahead[1] + dy)
                note_push(d, ahead, moved_to)
                known = frozenset((set(known) - {ahead}) | {moved_to})
        else:
            # The plan ran to its last move without a single refusal. If we
            # are not on the target, the MODEL was wrong rather than the
            # execution -- a different fault from a blocked step, and worth
            # separating in the log.
            if d.pos() != target:
                log.info("  plan exhausted cleanly at %s (wanted %s)",
                         d.pos(), target)
        if d.pos() == target:
            return True
    return d.pos() == target


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    ap.add_argument("--to", required=True, help="x,y target on this map")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    tx, ty = (int(v) for v in a.to.split(","))
    log.info("START %s %s -> (%d,%d)", d.map_name(), d.pos(), tx, ty)
    ok = walk(d, (tx, ty))
    log.info("RESULT %s at %s %s", ok, d.map_name(), d.pos())
    if a.out:
        d.save(a.out)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
