#!/usr/bin/env python3
"""Journey driver: reusable primitives for long play sessions, run as legs
in a single persistent process (no per-command emulator reload).

Usage: .venv/bin/python trek.py <leg> [args]   (see main() dispatch)
"""

import contextlib
import heapq
import inspect
import json
import logging
import random
import re
import sys
from collections import deque
from io import BytesIO
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from crystalagent import paths
from crystalagent.battle import (Battle, BattleData, bag_item_index,
                                 bag_quantity, cheapest_heal, goto_pocket,
                                 _norm_item)
from crystalagent.charmap import Charmap
from crystalagent.emu import Crystal, parse_sequence, InputError
from crystalagent import hookevents
from crystalagent import missables
from crystalagent.menus import Menus, battle_menu_up, dialog_press_safe, CURSORS
from crystalagent.names import Names
from crystalagent.nav import MapData, STEP, WARPS, WALKABLE, HOPS, CONN_NAME, ICE, COLL_PIT
from crystalagent.nav import WATER as _NAV_WATER
from crystalagent.nav import ICE as _NAV_ICE
from crystalagent.schemas import validate_observe, validate_route
from crystalagent.state import (game_state, status_line, live_sprites,
                                box_state, SPRITE_WANDERERS)
from crystalagent.symfile import Symbols

# -- model-facing decision vocabulary (wren pt6) ---------------------------
# crystalagent.decide carries the shared battle-decision vocabulary:
# battle_frame() (one dict with everything a turn decision needs),
# TurnLog (the per-turn record the Koga wipe had none of), and
# DecisionRequired (the harness refusing to pick for the model).
# Imported PER NAME and defensively: a live kernel may still hold an older
# crystalagent, and trek.py must keep driving battles without any of them
# (legacy (rows, me, enemy) policies, turn rows in a plain list).
try:
    from crystalagent.decide import battle_frame as _decide_frame
except Exception:
    _decide_frame = None
try:
    from crystalagent.decide import TurnLog as _TurnLog
except Exception:
    _TurnLog = None
try:
    from crystalagent.decide import DecisionRequired
except Exception:
    class DecisionRequired(RuntimeError):
        """A decision the harness refuses to make: raised by
        fight(require_decision=True) / d.decide_all when a policy returns
        None. Carries the decision frame (.frame) so the model can answer
        without re-reading the battle. Subclasses RuntimeError so existing
        `except RuntimeError` guards keep working."""

        def __init__(self, message, frame=None, kind=None, options=()):
            super().__init__(message)
            self.frame = frame
            self.kind = kind
            self.options = options


log = logging.getLogger("trek")


def _policy_style(pol):
    """Which call shape a battle/encounter policy declares: 'frame' for
    the wren-pt6 single-argument policy(frame), 'legacy' for the historic
    policy(rows, me, enemy). Anything uninspectable (builtins) or
    *args-shaped is legacy: every policy written before pt6 takes the
    triple and a live kernel still holds some."""
    if pol is None:
        return "legacy"
    try:
        params = list(inspect.signature(pol).parameters.values())
    except (TypeError, ValueError):
        return "legacy"
    slots = 0
    for p in params:
        if p.kind is inspect.Parameter.VAR_POSITIONAL:
            return "legacy"
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                      inspect.Parameter.POSITIONAL_OR_KEYWORD):
            slots += 1
    return "frame" if slots == 1 else "legacy"


def _turn_row(rec, **row):
    """Append one turn to a decide.TurnLog (or the plain-list fallback)
    and return the STORED row dict, so the next turn can fill in this
    turn's after-HP. Never raises: bookkeeping must not lose a battle."""
    put = getattr(rec, "record", None)
    if callable(put):
        try:
            stored = put(**row)
            return stored if isinstance(stored, dict) else row
        except Exception as err:
            log.warning(f"  [fight] turn log unusable ({err}); "
                        f"recording this turn plainly")
    if isinstance(rec, list):
        rec.append(row)
    return row


DIRS = {"U": "UP", "D": "DOWN", "L": "LEFT", "R": "RIGHT"}

class TravelError(RuntimeError):
    """travel(): a transition landed somewhere the plan didn't expect."""


class HealError(RuntimeError):
    """heal_pokecenter(): no nurse reachable from here. Carries the map
    name so callers (registry 'heal') can report a structured failure
    instead of exploding mid-composite. Subclasses RuntimeError so old
    `except RuntimeError` guards keep working."""

    def __init__(self, map_name, detail=""):
        self.map_name = map_name
        msg = f"heal_pokecenter: not inside a Pokécenter (on {map_name})"
        if detail:
            msg += f" -- {detail}"
        super().__init__(msg)


# EnterMapConnection: stepping off `letter`'s edge from (x, y) with the
# connection's `off` lands on the destination at -- (dw = dest width in
# cells, dh = dest height). Same math as nav._conn_landing.
_CONN_LAND = {
    "U": lambda dw, dh, off, x, y: (x - 2 * off, dh - 1),
    "D": lambda dw, dh, off, x, y: (x - 2 * off, 0),
    "L": lambda dw, dh, off, x, y: (dw - 1, y - 2 * off),
    "R": lambda dw, dh, off, x, y: (0, y - 2 * off),
}
_CONN_LETTER = {"north": "U", "south": "D", "west": "L", "east": "R"}

_mapgraph_json = None


def mapgraph():
    """data/mapgraph.json (validated warp/connection edges between maps),
    parsed once per process. Side-effect free."""
    global _mapgraph_json
    if _mapgraph_json is None:
        p = Path(__file__).parent / "data" / "mapgraph.json"
        _mapgraph_json = json.loads(p.read_text())
    return _mapgraph_json


# Side-wall collision bytes ($b0-$b7, hi nybble $b0; buoys $c0-$c7 behave
# alike but sit on water). Engine home/map.asm GetMovementPermissions sets
# a BLOCKING bit per facing: each byte refuses entry from specific sides
# only -- e.g. COLL_UP_WALL ($b2) can be entered moving up/left/right but
# never down. Verified live: Route 32 (4,71)->D onto (4,72)$b2 bumps, and
# Union Cave 1F's corridors cross $b2 rows upward. Base nav treats the
# whole family as solid, which sealed Union Cave's lower floor.
_SIDE_WALL_BLOCKED = {
    0xB0: {"L"},           # COLL_RIGHT_WALL
    0xB1: {"R"},           # COLL_LEFT_WALL
    0xB2: {"D"},           # COLL_UP_WALL
    0xB3: {"U"},           # COLL_DOWN_WALL
    0xB4: {"U", "L"},      # COLL_DOWN_RIGHT_WALL
    0xB5: {"U", "R"},      # COLL_DOWN_LEFT_WALL
    0xB6: {"D", "L"},      # COLL_UP_RIGHT_WALL
    0xB7: {"D", "R"},      # COLL_UP_LEFT_WALL
}


# A side-wall tile also blocks LEAVING it across its own wall edge, for any
# terrain -- not just a slide start. Derived from the engine instead of
# guessed: home/map.asm GetMovementPermissions indexes
# .MovementPermissionsData by `collision & 7` for any tile whose hi nybble
# is $b0/$c0 and ORs the entry into wTilePermissions, and
# engine/overworld/player_movement.asm .CheckLandPerms refuses the step
# when `wFacingDirection & wTilePermissions`. The two encodings are
# reversed (DOWN_MASK=1/UP_MASK=2/LEFT_MASK=4/RIGHT_MASK=8 vs
# FACE_DOWN=8/FACE_UP=4/FACE_LEFT=2/FACE_RIGHT=1), so the table's
# LEFT_MASK on $b2 forbids moving *UP* off the tile -- exactly the mirror
# of its entry rule.
#
# Proven live twice, both on plain floor (not ice): ICE_PATH_1F (28,10)
# and VICTORY_ROAD (5,58), where U off the $b2 tile onto $00 floor never
# moves while D and R both do. The old ICE-only narrowing let goto plan
# that step, the engine refused it, the cell was marked live-blocked and
# the walk died in a replan-storm (FUCK_I_MESSED_UP #56, #75).
_SIDE_WALL_EXIT_BLOCKED = {
    b: {{"U": "D", "D": "U", "L": "R", "R": "L"}[d] for d in dirs}
    for b, dirs in _SIDE_WALL_BLOCKED.items()
}


def _tile_kind(b):
    """Terrain word for a collision byte (observe()'s tiles{}). Field
    obstacles ($24 whirlpool, $33 waterfall, $27/$c0-$c7 buoys) and the
    $b0-$b7 side walls get their own words instead of generic 'blocked'
    so the deciding loop knows a bump there is clearable/directional."""
    if b in (0x14, 0x18):
        return "grass"
    if b == 0x00:
        return "floor"
    if b == 0x24:                       # COLL_WHIRLPOOL
        return "whirlpool"
    if b == 0x33:                       # COLL_WATERFALL
        return "waterfall"
    if b == 0x93:                       # COLL_PC: the box terminal you
        return "pc"                     # face and press A on (journal #45)
    if b == 0x27 or 0xC0 <= b <= 0xC7:  # COLL_BUOY + water side walls
        return "buoy"
    if b in _NAV_WATER:
        return "water"
    if b in WARPS:
        return "warp"
    if b in HOPS:
        return "ledge-" + HOPS[b].lower()
    if b in ICE:
        return "ice"
    if b == COLL_PIT:
        return "pit"
    if b in _SIDE_WALL_BLOCKED:
        return "sidewall-" + "".join(
            d for d in "UDLR" if d in _SIDE_WALL_BLOCKED[b]).lower()
    return "blocked"


class TrekNav(MapData):
    """nav.MapData + two routing extensions, kept local to trek.py so the
    shared crystalagent/nav.py stays untouched:

    - side walls expand directionally per _SIDE_WALL_BLOCKED;
    - `blocked[map]` cells (live coord_event triggers, refreshed by
      Driver._refresh_nav_blocks) are never routed through -- not even as
      the goal. Without this, BFS happily plans over e.g. Route 32 (18,8),
      where the Cooltrainer scene re-fires forever until Elm's aide's
      Togepi-egg scene flips the map's scene id."""

    def __init__(self, repo):
        super().__init__(repo)
        self.blocked = {}

    def _bfs(self, start_state, goal_state, avoid_map, avoid, cross):
        avoid = set(avoid)

        def blocked(m, x, y):
            return (x, y) in self.blocked.get(m, ())

        def expand(state):
            M, (x, y) = state
            grid = self.grid(M)
            hgt, wid = len(grid), len(grid[0])
            here = grid[y][x]
            plain, exits = [], []
            exit_walls = _SIDE_WALL_EXIT_BLOCKED.get(here, ())
            for d, (dx, dy) in STEP.items():
                if d in exit_walls:
                    continue     # crossing this tile's own wall edge
                # standing on a ledge lip and moving in its hop direction
                # jumps over the cliff tile, landing 2 cells away (the game
                # checks the tile you stand ON: engine .TryJump)
                if here in HOPS and HOPS[here] == d:
                    lx, ly = x + 2 * dx, y + 2 * dy
                    if M == avoid_map and (lx, ly) in avoid:
                        continue
                    if blocked(M, lx, ly):
                        continue
                    if self._enterable(M, lx, ly):
                        plain.append(((M, (lx, ly)), d))
                    continue
                if here in ICE:
                    # sliding floor: direction choice is only free on the
                    # entry cell; the slide carries you to its terminal
                    # cell (deterministic, see MapData.slide)
                    sx, sy = self.slide(M, x, y, d)
                    if (sx, sy) == (x, y):
                        continue
                    nx, ny = sx, sy
                else:
                    nx, ny = x + dx, y + dy
                nxt = (M, (nx, ny))
                if 0 <= nx < wid and 0 <= ny < hgt:
                    if M == avoid_map and (nx, ny) in avoid:
                        continue
                    if blocked(M, nx, ny):
                        continue
                    if nxt == goal_state:
                        plain.append((nxt, d))
                        continue
                    c = grid[ny][nx]
                    w = self.warps.get(M, {}).get((nx, ny))
                    if w is not None and (c in WARPS or c == COLL_PIT):
                        # stepping ONTO a live warp tile fires it (arrival
                        # never re-triggers, so landing on one is fine --
                        # gate doors land on exactly such tiles); you can
                        # never stand on one mid-path otherwise
                        if cross:
                            land = self._warp_landing(M, (nx, ny))
                            if land:
                                exits.append(((land[0], land[1]), d))
                        continue
                    # w is not None but collision says plain floor: DORMANT
                    # leftover warp_event that can never fire (sealed
                    # Burned Tower B1F corridors) -- walk across it
                    if c in WALKABLE or c in WARPS or c in HOPS or \
                            (self.surf and c in _NAV_WATER):
                        plain.append((nxt, d))
                    elif c in _SIDE_WALL_BLOCKED and \
                            d not in _SIDE_WALL_BLOCKED[c]:
                        # one-way wall: enterable from every facing except
                        # its blocked side(s)
                        plain.append((nxt, d))
                    elif c in _NAV_ICE:
                        # ice: the step becomes a deterministic slide; the
                        # edge lands on the slide's terminal cell
                        tx, ty = self.slide(M, x, y, d)
                        if (tx, ty) != (x, y) and \
                                not (M == avoid_map and (tx, ty) in avoid) \
                                and not blocked(M, tx, ty):
                            plain.append(((M, (tx, ty)), d))
                elif cross and CONN_NAME[d] in self.conns.get(M, {}):
                    land = self._conn_landing(M, CONN_NAME[d], x, y)
                    if land:
                        exits.append(((land[0], land[1]), d))
            return plain + exits

        prev = {start_state: None}
        q = deque([start_state])
        while q:
            cur = q.popleft()
            if cur == goal_state:
                moves = []
                while prev[cur]:
                    pcur, mv = prev[cur]
                    moves.append(mv)
                    cur = pcur
                return moves[::-1]
            for nxt, mv in expand(cur):
                if nxt not in prev:
                    prev[nxt] = (cur, mv)
                    q.append(nxt)
        return None


# --- ASCII map view ---------------------------------------------------------

_GLYPH_LEGEND = [
    ("@", "player"),
    (".", "floor / walkable"),
    ("%", "tall grass"),
    ("~", "water (surf to cross)"),
    ("O", "warp: door, stairs, ladder"),
    ("^", "one-way ledge hop"),
    ("=", "ice floor (slides)"),
    ("x", "pit"),
    ("!", "sealed or live-blocked cell"),
    ("N", "NPC"),
    ("#", "solid wall"),
    (",", "walkable, NOT reachable from here (needs another entrance)"),
    ("o", "warp in a region you cannot reach from here"),
    (" ", "wall / off-map"),
]


def _cell_glyph(c):
    if c == 0x14:
        return "%"
    if c in _NAV_WATER:
        return "~"
    if c in WARPS:
        return "O"
    if c in HOPS:
        return "^"
    if c in _NAV_ICE:
        return "="
    if c == COLL_PIT:
        return "x"
    if c in WALKABLE:
        return "."
    return "#"


def _reach(nav, map_name, start, surf):
    """Cells reachable with plain moves + ledge hops + ice slides; warps
    and map edges are walls for view purposes."""
    grid = nav.grid(map_name)
    hgt, wid = len(grid), len(grid[0])
    blocked = getattr(nav, "blocked", {}).get(map_name, ())
    seen = {tuple(start)}
    q = deque([tuple(start)])
    while q:
        x, y = q.popleft()
        here = grid[y][x]
        for d, (dx, dy) in STEP.items():
            if here in HOPS and HOPS[here] == d:
                nx, ny = x + 2 * dx, y + 2 * dy
            elif here in _NAV_ICE:
                nx, ny = nav.slide(map_name, x, y, d)
            else:
                nx, ny = x + dx, y + dy
            if not (0 <= nx < wid and 0 <= ny < hgt):
                continue
            if (nx, ny) in seen or (nx, ny) in blocked:
                continue
            if nav._enterable(map_name, nx, ny):
                seen.add((nx, ny))
                q.append((nx, ny))
    return seen


def render_map_view(nav, map_name, pos, npcs=(), surf=False):
    """ASCII view centred on the region reachable on foot/surf from `pos`,
    cropped to its bounding box (+1). Global coordinates in the rulers so
    the deciding LLM can quote them straight back to goto()/talk_to().

    Cells inside the window that are WALKABLE but belong to another
    connected component draw as `,` (and `o` for their warps) instead of
    blank: a blank used to be indistinguishable from wall, so a whole
    wing of a map -- Rocket base B3F's west half, reachable only from
    B2F's left ladders -- rendered as void and read as "nothing there".
    Components with no cell in the window are named by the `offregion:`
    annotation line instead."""
    grid = nav.grid(map_name)
    hgt, wid = len(grid), len(grid[0])
    reach = _reach(nav, map_name, pos, surf)
    blocked = set(getattr(nav, "blocked", {}).get(map_name, ()))
    npc_set = {tuple(c) for c in npcs}
    xs = [x for x, _ in reach | {tuple(pos)}]
    ys = [y for _, y in reach | {tuple(pos)}]
    ox, oy = max(min(xs) - 1, 0), max(min(ys) - 1, 0)
    ex, ey = min(max(xs) + 1, wid - 1), min(max(ys) + 1, hgt - 1)
    ids, _ = nav.region_map(map_name)
    mine = set(nav.regions_at(map_name, *pos))
    lines = [f"map={map_name} origin=({ox},{oy}) "
             f"pos=({pos[0]},{pos[1]})"]
    # two-row ruler: tens only where they change, units everywhere
    tens_row = "".join(
        str((x // 10) % 10) if (x - ox) % 10 == 0 else " "
        for x in range(ox, ex + 1))
    units_row = "".join(str(x % 10) for x in range(ox, ex + 1))
    lines.append("     " + tens_row)
    lines.append("     " + units_row)
    for y in range(oy, ey + 1):
        row = []
        for x in range(ox, ex + 1):
            if (x, y) == tuple(pos):
                row.append("@")
            elif (x, y) in npc_set:
                row.append("N")
            elif (x, y) in blocked:
                row.append("!")
            elif (x, y) in reach:
                row.append(_cell_glyph(grid[y][x]))
            else:
                row.append(_offregion_glyph(nav, map_name, ids, mine, x, y))
        lines.append(f"{y:4d} " + "".join(row))
    used = {ch for line in lines[3:] for ch in line.split(" ", 1)[-1]}
    legend = [f"  {g} {desc}" for g, desc in _GLYPH_LEGEND
              if g != " " and g in used]
    if legend:
        lines.append("legend:")
        lines += legend
    return "\n".join(lines)


def _offregion_glyph(nav, map_name, ids, mine, x, y):
    """Glyph for a cell outside `_reach`: `o` for a warp that opens onto a
    component the player cannot reach, `,` for that component's walkable
    floor, blank otherwise.

    Only OTHER components are revealed. A cell in the player's own
    component that simply is not walk-reachable (water without SURF, a CUT
    tree) keeps the old blank: it is already named by the annotation block
    when it matters, and drawing it would claim reachability the view
    cannot promise. What was genuinely missing is architecture the player
    can never touch from here -- Rocket base B3F's western wing rendered
    as void, and a session read that as "nothing there"."""
    coll = nav.grid(map_name)[y][x]
    here = ids[y][x]
    if here >= 0:
        return "o" if (coll in WARPS and here not in mine) else \
            ("," if here not in mine else " ")
    # warp-EVENT tiles belong to no component (stepping on one fires it):
    # name them by what they open onto, so a ladder into an unreachable
    # wing shows as `o`. regions_at() answers for ANY cell, so this must
    # be gated on the cell really being a warp -- otherwise every wall
    # beside the wing would draw as a door.
    if coll not in WARPS and (x, y) not in nav.warps.get(map_name, {}):
        return " "
    touches = set(nav.regions_at(map_name, x, y))
    return "o" if touches and not (touches & mine) else " "


_coord_event_cache = None
_FILE_CONSTS = {}
_shared_nav_md = None


def _file_const(stem):
    """maps/<Camel>.asm file stem -> map CONST (via MapData's pairing);
    falls back to the stem itself when there is no attributes entry."""
    if stem not in _FILE_CONSTS:
        const = None
        try:
            md = _shared_nav()
            for c, camel in md.camel.items():
                if camel == stem:
                    const = c
                    break
        except Exception:
            pass
        _FILE_CONSTS[stem] = const or stem
    return _FILE_CONSTS[stem]


def _shared_nav():
    global _shared_nav_md
    if _shared_nav_md is None:
        _shared_nav_md = TrekNav(paths.REPO_ROOT)
    return _shared_nav_md


_DISRUPTIVE_KEYWORDS = ("applymovement", "follow")
_script_body_cache = {}
_script_lines_cache = {}
# The leading guard chain of a coord_event script: `checkevent X` /
# `checkflag X` immediately followed by `iftrue LABEL` / `iffalse LABEL`.
# Those pairs run BEFORE anything happens, so if the live flag sends the
# script to a label that does nothing, the trigger is SPENT and its cell
# is walkable -- which is the whole difference between "the rival ambush
# is armed" and "I already fought him".
_GUARD_CHECK = re.compile(r"^(checkevent|checkflag)\s+([A-Z0-9_]+)\s*$")
_GUARD_JUMP = re.compile(r"^(iftrue|iffalse)\s+([A-Za-z_][\w.]*)\s*$")


def _script_body(repo, camel_file, script_label):
    """The instruction lines of one script label in maps/<Camel>.asm,
    comments and blanks dropped, stopping at the next label. None when
    the file or the label is not there."""
    key = (camel_file, script_label)
    if key not in _script_lines_cache:
        body = None
        p = Path(repo, "maps", f"{camel_file}.asm")
        try:
            lines = p.read_text(errors="replace").splitlines()
            start = None
            for i, line in enumerate(lines):
                if line.split(";")[0].strip().rstrip(":") == script_label \
                        and ":" in line:
                    start = i + 1
                    break
            if start is not None:
                body = []
                for line in lines[start:]:
                    s = line.split(";")[0].strip()
                    if not s:
                        continue
                    if s.endswith(":"):     # next label -> end of body
                        break
                    body.append(s)
        except OSError:
            pass
        _script_lines_cache[key] = body
    return _script_lines_cache[key]


def script_is_disruptive(repo, camel_file, script_label):
    """True if the coord_event's script moves the player or makes an NPC
    follow them (applymovement/follow) -- i.e. it would physically undo
    progress, like Route 32's Cooltrainer push-back. Pure-dialog triggers
    (the Slowpoke Tail pitch) are left routable: walk/goto already flush
    textboxes."""
    key = (camel_file, script_label)
    if key not in _script_body_cache:
        body = _script_body(repo, camel_file, script_label)
        _script_body_cache[key] = (
            False if body is None
            else any(kw in instr for instr in body
                     for kw in _DISRUPTIVE_KEYWORDS))
    return _script_body_cache[key]


def script_guards(repo, camel_file, script_label):
    """``[(check, NAME, jump, target), ...]`` for the script's leading
    guard chain, in order:

        PlateauRivalBattle1:
            checkevent EVENT_BEAT_RIVAL_IN_MT_MOON
            iffalse PlateauRivalScriptDone
            checkflag ENGINE_INDIGO_PLATEAU_RIVAL_FIGHT
            iftrue PlateauRivalScriptDone
        -> [('checkevent', 'EVENT_BEAT_RIVAL_IN_MT_MOON', 'iffalse',
             'PlateauRivalScriptDone'),
            ('checkflag', 'ENGINE_INDIGO_PLATEAU_RIVAL_FIGHT', 'iftrue',
             'PlateauRivalScriptDone')]

    Only the uninterrupted prefix of check/jump PAIRS is reported -- the
    part that provably runs before the script does anything -- so a
    caller can evaluate the live flags and find out whether this trigger
    still has teeth. Anything else (readvar/ifequal weekday tests,
    actual instructions) ends the chain."""
    body = _script_body(repo, camel_file, script_label) or []
    out = []
    i = 0
    while i + 1 < len(body):
        chk = _GUARD_CHECK.match(body[i])
        jmp = _GUARD_JUMP.match(body[i + 1])
        if not chk or not jmp:
            break
        out.append((chk.group(1), chk.group(2), jmp.group(1), jmp.group(2)))
        i += 2
    return out


_coord_event_cache = None


def coord_events(repo):
    """{map_const: [(x, y, scene_token, script_label)]} parsed from every
    maps/<Camel>.asm def_coord_events table. Cached per process."""
    global _coord_event_cache
    if _coord_event_cache is not None:
        return _coord_event_cache
    out = {}
    for path in Path(repo, "maps").glob("*.asm"):
        section = None
        for line in path.read_text(errors="replace").splitlines():
            s = line.strip()
            if s.startswith("def_"):
                section = s[4:].rstrip("s")
                continue
            if section != "coord_event" or not s.startswith("coord_event"):
                continue
            args = [a.strip() for a in s[len("coord_event"):].split(",")]
            if len(args) >= 4:
                out.setdefault(_file_const(path.stem), []).append(
                    (int(args[0]), int(args[1]), args[2], args[3]))
    _coord_event_cache = out
    return out


_scene_const_cache = None


def scene_consts(repo):
    """{map_const: [SCENE_* names in declaration order]} from every map's
    def_scene_scripts table (position = runtime scene id, matching the
    scene_const macro's const_def). Cached per process."""
    global _scene_const_cache
    if _scene_const_cache is not None:
        return _scene_const_cache
    out = {}
    for path in Path(repo, "maps").glob("*.asm"):
        names, in_scenes = [], False
        for line in path.read_text(errors="replace").splitlines():
            s = line.strip()
            if s.startswith("def_"):
                in_scenes = s == "def_scene_scripts"
                continue
            if in_scenes and s.startswith("scene_script"):
                names.append(s.split(",")[1].strip() if "," in s else None)
        if names:
            out[_file_const(path.stem)] = names
    _scene_const_cache = out
    return out


_scene_var_cache = None


def scene_vars(repo):
    """{map_const: WRAM symbol} from data/maps/scenes.asm scene_var lines
    (maps absent from that table have no persistent scene id). Cached."""
    global _scene_var_cache
    if _scene_var_cache is None:
        import re as _re
        p = Path(repo, "data", "maps", "scenes.asm")
        out = {}
        for line in p.read_text(errors="replace").splitlines():
            m = _re.match(r"\s*scene_var\s+([A-Z0-9_]+)\s*,\s*([A-Za-z0-9_]+)",
                          line)
            if m:
                out[m.group(1)] = m.group(2)
        _scene_var_cache = out
    return _scene_var_cache

# Naming-keyboard grids (9 cols x 4 char rows), parsed from
# data/text/name_input_chars.asm: each cell is 2 chars, space = empty key.
# Row 4 is controls: cols 0-2 case switch, 3-5 DEL, 6-8 END.
def _parse_name_grid(repo):
    import re as _re
    from pathlib import Path as _Path
    tables = {}
    text = (_Path(repo) / "data/text/name_input_chars.asm").read_text()
    for name in ("NameInputUpper", "NameInputLower"):
        m = _re.search(name + r":\n((?:\tdb \"[^\"]*\"\n)+)", text)
        rows = _re.findall(r'db "([^"]*)"', m.group(1))
        grid = {}
        for y, row in enumerate(rows[:4]):
            for x in range(9):
                ch = row[x * 2]
                if ch != " ":
                    grid[ch] = (x, y)
        tables[name] = grid
    return tables


NAME_GRIDS = None


def _parse_event_flags(path):
    """constants/event_flags.asm -> {EVENT_NAME: bit index into wEventFlags}.

    One `const` walker for the whole harness (crystalagent.missables reads
    the same table to name the flag guarding each item gift); the local
    copy this replaced silently ignored `const_skip N`'s count."""
    return missables.event_bits(Path(path).parent.parent)


_MOVE_LENGTH = 7    # battle_constants.asm move_struct
_MOVE_PP_OFF = 5    # MOVE_PP
# wStatusFlags bit 0 (ram_constants.asm STATUSFLAGS_POKEDEX_F)
_STATUSFLAGS_POKEDEX_F = 1 << 0

_event_flag_names = None   # lazily parsed {EVENT_NAME: bit}
_move_base_pps = None      # lazily read [base PP per move id - 1]


def _load_move_base_pps(rom_path, sym):
    """Base PP for every move, read straight from the ROM's Moves table."""
    bank, base = sym["Moves"]
    off = base if base < 0x4000 else bank * 0x4000 + (base - 0x4000)
    with open(rom_path, "rb") as f:
        rom = f.read()
    from crystalagent.names import NUM_MOVES
    return [rom[off + i * _MOVE_LENGTH + _MOVE_PP_OFF]
            for i in range(NUM_MOVES)]

_NUM_TMS, _NUM_HMS = 50, 7   # item_constants.asm DEF NUM_TMS / NUM_HMS

def _item_row_matches(row_text, want_norm):
    """True when a scraped pack-menu row names the wanted item (wren pt4:
    use_item('SUPER POTION') False). Both sides go through _norm_item, so
    the compare is blind to case, spaces, hyphens, and the POKe glyph.
    The row may carry trailing junk (quantity digits, scroll-arrow tiles
    at the box edge) -- covered by the prefix test -- and may lose
    trailing tiles at the screen edge, so a near-complete row that is
    itself a prefix of the wanted name (>= max(4, len-2) chars) also
    matches. A different item can never pass: the row is anchored at the
    cursor arrow, and prefix containment between distinct item names
    ('POTION' in 'SUPER POTION') fails in both directions."""
    row = _norm_item(row_text)
    if not row or not want_norm:
        return False
    if row.startswith(want_norm):
        return True
    return len(row) >= max(4, len(want_norm) - 2) and \
        want_norm.startswith(row)


# Pack pocket banners, data/items/pocket_names.asm (DrawPocketName paints
# one on every pocket screen).
_POCKET_BANNERS = ("ITEM POCKET", "BALL POCKET", "KEY POCKET", "TM POCKET")

# The pack's quantity column: '...  ×  4' (charmap ×), tolerating a plain
# 'x'/'X' decode and a scroll-arrow tile at the box edge.
_PACK_QTY_RE = re.compile(r"×\s*\d+|(?:^|\s)[xX]\s+\d+\s*[▼▲]?\s*$")


def _pack_pocket_banner(rows):
    """Which pocket banner the pack is drawing, or None."""
    for r in rows:
        up = r.upper()
        for name in _POCKET_BANNERS:
            if name in up:
                return name
    return None


def _pack_quantity_rows(rows):
    """True when any drawn row carries the pack's 'x N' quantity column
    -- no other field UI prints one."""
    return any(_PACK_QTY_RE.search(r) for r in rows)


# The pack's "Use on which PM?" target list prints 'hp/max' fractions; the
# item pocket's own column is '× n', so this never confuses the two.
_HP_FRACTION_RE = re.compile(r"\d\s*/\s*\d+")


def _party_target_list(rows):
    """True only when the pack's "Use on which PM?" party list is really
    drawn. "a CANCEL row is on screen" is NOT enough: the item pocket
    draws its own CANCEL row once scrolled to the bottom of the list, and
    accepting that as the target list let the party steering run while
    the POCKET's cursor was still live in wMenuCursorY (the party list is
    a 2D menu; wMenuScrollPosition there still holds the pocket's
    offset) -- an A fired at the wrong screen. Same predicate battle.py
    uses for the in-battle target list."""
    joined = "".join(rows).upper()
    if "USE ON WHICH" in joined:
        return True
    return any("CANCEL" in r for r in rows) and \
        any(_HP_FRACTION_RE.search(r) for r in rows)


def _no_effect_message(rows):
    """_ItemWontHaveEffectText ("It won't have any" / "effect.",
    data/text/common_3.asm) -- the engine refusing a LEGITIMATE no-op: a
    full-HP unstatused target, an ANTIDOTE on a clean mon, a POTION on a
    fainted one. Nothing is consumed and nothing ever will be, so this
    must be reported as its own outcome, never mashed through as if the
    A had been swallowed."""
    joined = "".join(rows).upper()
    return "HAVE ANY" in joined and "EFFECT" in joined


def _field_clear(rows):
    """No modal field UI left on screen -- no menu cursor, no pack /
    party-list / START-menu row. The postcondition every field-item flow
    has to restore: a stray START menu silently eats all movement input
    (gotcha 7)."""
    bad = ("▶", "▷", "CANCEL", "QUIT", "EXIT", "USE", "TOSS")
    return not any(b in r for r in rows for b in bad)


def _norm_name(text):
    """Canonical mon-nickname key: uppercase alphanumerics only, so
    'Brook', 'BROOK' and ' brook ' all address the same party member.
    (norm_item is the ITEM key -- it also rewrites '#' to POKE, which has
    no business happening to a nickname.)"""
    return re.sub(r"[^A-Z0-9]", "", str(text).upper())


_UNSET = object()       # use_item(target_slot=...) "argument not given"

# constants/item_data_constants.asm: ITEMATTR_STRUCT_LENGTH, with
# ITEMATTR_PRICE the first (little-endian) word of each entry.
_ITEMATTR_LENGTH = 7
# engine/items/item_effects.asm: the ItemEffects jumptable is
# `assert_table_length ITEM_B3` ("The items past ITEM_B3 do not have
# effect entries"). Every curative item sits well inside it.
_ITEM_EFFECTS_ENTRIES = 0xB3

_field_heal_table = None    # lazily read {norm item: heal/cure/price}


def _load_heal_table(rom_path, sym, names):
    """Every curative pack item, read out of the ROM's OWN tables so no
    game data is hardcoded here (AGENTS.md: "the repo is the map"):

      * HealingHPAmounts   (data/items/heal_hp.asm)     -- HP restored
      * StatusHealingActions (data/items/heal_status.asm) -- cured bits
      * the ItemEffects jumptable's ReviveEffect entries -- revives
      * ItemAttributes     (data/items/attributes.asm)  -- shop price

    Returns {normalized name: {'name', 'hp', 'cures', 'revives',
    'price'}}; 'cures' is a wPartyMon*Status bit mask, 'hp' 0 for items
    that restore none."""
    with open(rom_path, "rb") as f:
        rom = f.read()

    def off(label):
        bank, base = sym[label]
        return base if base < 0x4000 else bank * 0x4000 + (base - 0x4000)

    attrs = off("ItemAttributes")
    table = {}

    def row(item_id):
        name = names.items.get(item_id)
        if not name:
            return None
        base = attrs + (item_id - 1) * _ITEMATTR_LENGTH
        return table.setdefault(_norm_item(name), {
            "name": name, "hp": 0, "cures": 0, "revives": False,
            "price": int.from_bytes(rom[base:base + 2], "little")})

    p = off("HealingHPAmounts")          # dbw item, hp restored
    while rom[p] != 0xFF:
        got = row(rom[p])
        if got is not None:
            got["hp"] = int.from_bytes(rom[p + 1:p + 3], "little")
        p += 3
    p = off("StatusHealingActions")      # db item, menu text, status mask
    while rom[p] != 0xFF:
        got = row(rom[p])
        if got is not None:
            got["cures"] = rom[p + 2]
        p += 3
    jump, revive = off("ItemEffects"), sym["ReviveEffect"][1]
    for item_id in range(1, _ITEM_EFFECTS_ENTRIES + 1):
        p = jump + (item_id - 1) * 2
        if int.from_bytes(rom[p:p + 2], "little") == revive:
            got = row(item_id)
            if got is not None:
                got["revives"] = True
    return table



class Driver:
    def __init__(self, state_path=None, fresh=False, live=None):
        """fresh=True: power-on reset (no savestate loaded); `state_path` is
        then only the file a later save() writes to. Documented in AGENTS.md's
        capabilities map (`Driver(state, fresh=True)`) and used by
        scripts/newgame_bedroom.py.

        live={...}: attach a LiveFeed with those kwargs (name/fps/speed/
        state_hz/directory) so watch.py can show THIS emulator's frames.
        `live={}` takes the defaults; `d.live_attach(**kw)` does the same
        after construction. Both AGENTS.md and HANDBOOK.md promised this and
        the kwarg did not exist -- every watched leg died on TypeError."""
        self.state_path = Path(state_path or paths.DEFAULT_STATE)
        sym = Symbols(paths.SYM)
        cm = Charmap(paths.CHARMAP)
        self.emu = Crystal(paths.ROM, sym, cm,
                           None if fresh else self.state_path)
        # savestates can carry phantom held keys; force-release everything
        for b in ("up", "down", "left", "right", "a", "b", "start", "select"):
            self.emu.py.button_release(b)
        self.emu.tick(10)
        self.names = Names(paths.ROM, sym, cm, paths.MAP_CONSTANTS)
        self.nav = TrekNav(paths.REPO_ROOT)
        self.menu = Menus(self.emu)
        self.bdata = BattleData(paths.REPO_ROOT, sym, paths.ROM)
        self._pending_nickname = None
        self.last_choice_options = []   # labels of the last refused box
        self.auto_fight = True   # False: nav battles bubble to the decider
        self.encounter_events = []   # decision-transparency journal
        self._whiteout_pending = False   # set by fight() on a detected wipe
        self.whiteouts = 0
        self.whiteout_policy = "abort"   # 'abort' | 'continue' (old behavior)
        # Battle policy used by every fight() the driver starts on the
        # player's behalf (talk_to trainer intercepts, goto/travel/walk
        # encounter intercepts, use_cut, registry 'fight') when no
        # explicit policy is passed. Whitney lesson (wren pt3): talk_to
        # auto-fought the gym leader with the DEFAULT policy before a
        # custom one could attach -- set d.default_policy BEFORE the
        # approach. Explicit fight(policy=...) args always win.
        self.default_policy = None
        # Level-up learn transparency (wren pt4: the learn flow replaced
        # BITE with SCARY FACE and a slot-1 policy whiffed through three
        # whiteouts): every resolved learn flow that REPLACES a move
        # appends {'mon','forgot','learned','slot','source'} here and
        # logs a LEARN line ('source': 'policy' | 'auto' |
        # 'auto-fallback' -- who decided the sacrifice; see
        # _diff_learned_moves). Inspect after train()/fight() before trusting
        # slot-based policies. Never cleared automatically.
        self.move_changes = []
        self.hooks = hookevents.install(self.emu)
        self.live = None
        if live is not None:
            self.live_attach(**live)

    def live_attach(self, **kw):
        """Publish this emulator's frames/state/log to `live/<name>.*` for
        watch.py; returns the LiveFeed. The DRIVING emulator does the
        rendering (inside emu.tick's slices), so the viewer never has to
        re-simulate a savestate and can show the title screen, Oak's
        speech and the naming keyboard -- none of which is ever saved.

        `name` defaults to the working state's stem. Idempotent: a second
        call detaches the previous feed first, and an atexit hook detaches
        the last one -- a narration handler still attached at interpreter
        shutdown writes to closed streams and prints `Error in
        sys.excepthook` three times after an otherwise clean leg."""
        import atexit
        from crystalagent.live import LiveFeed
        if getattr(self, "live", None) is not None:
            self.live.detach()
        kw.setdefault("name", self.state_path.stem)
        self.live = LiveFeed(self.emu, self.names, self.nav, **kw).attach()
        atexit.register(self.live_detach)
        return self.live

    def live_detach(self):
        if getattr(self, "live", None) is not None:
            self.live.detach()
            self.live = None

    # -- decision defaults (session claude-wren pt6) ------------------------
    # The harness must never decide a battle the model did not ask it to.
    # Live evidence: a model-written pacing loop reported fights=0 while
    # move_settled quietly fought ~20 encounters with the DEFAULT policy,
    # fed every exp share to the wrong mon, and whited the party out.
    #
    # auto_fight (instance, True) gates the JOURNEY helpers -- walk/goto/
    # travel -- which are allowed to clear encounters that merely stand
    # between us and a destination. Set it False and those bubble the
    # battle back to the decider instead.
    #
    # auto_fight_steps (class, False) gates the STEP PRIMITIVES:
    # move_settled(fight=None) and pace(). One step is not a journey, so
    # a battle walked into is SURFACED ('battle') with the battle still
    # up. Flip this True (or pass fight=True) to opt a caller back into
    # the old swallow-it behaviour.
    auto_fight_steps = False

    # encounter_policy: optional per-driver hook letting the MODEL decide
    # a wild's disposition ('ko' | 'catch' | 'flee' | ('ball', NAME)) and
    # each battle turn's decision. None = no hook (default_policy / AUTO).
    encounter_policy = None
    # decide_all: True means every battle needs an explicit decision --
    # refuse to auto-pilot rather than silently pick for the model.
    decide_all = False
    # last_item_reason: machine-readable diagnosis of the most recent
    # use_item call ('used' | 'no-effect' | 'not-in-bag' | 'no-pack' |
    # 'pocket-miss' | 'no-use-option' | 'target-miss' | 'not-consumed').
    # 'no-effect' is the engine's own legitimate no-op, NOT a failure.
    last_item_reason = None
    # last_menu_reason: why the most recent menu primitive (pack, pocket,
    # party list, START menu) answered False. Every one of those used to
    # return a bare False, which is how "use_item did nothing" stayed
    # undiagnosable for a whole session.
    last_menu_reason = None
    # last_step_reason: why a single step could not be modelled or taken
    # (no decoded grid for this map, a blocked walk, a battle handoff).
    last_step_reason = None
    # last_tm_reason: teach_tm's machine-readable diagnosis.
    last_tm_reason = None
    # last_pc_reason: why the most recent deposit/withdraw answered False
    # ('deposited' | 'withdrawn' | 'no-such-mon' | 'not-in-box' |
    # 'last-mon' | 'box-full' | 'party-full' | 'holds-mail' | 'no-pc' |
    # 'no-list' | 'target-miss' | 'unchanged' | 'over-applied').
    last_pc_reason = None
    # last_field_reason: why the most recent use_field_move/waterfall/
    # whirlpool answered False ('used' on success).
    last_field_reason = None
    # last_money_delta: money change observed across the last movement
    # call (see _money_watch); non-zero means something SPENT while
    # navigating -- the ¥1200 of ESCAPE ROPEs an A-mash once bought.
    last_money_delta = 0

    def _menu_fail(self, reason):
        """Record why a menu primitive answered False, and say so once.

        Mirrors _item_fail without touching the UI: these are the inner
        primitives, and the caller (use_item, teach_tm) owns the exit."""
        self.last_menu_reason = reason
        log.info(f"  menu: {reason}")
        return False

    def _confirm_label(self, label, expect, **kw):
        """``Menus.select_label`` with the reached-state check, tolerating
        older or duck-typed Menus objects that predate `expect`.

        The fallback does the SAME verification here rather than trusting
        a cursor-glyph success -- that trust is the bug (gotcha 2: the A
        pressed on the frame a menu is drawn is swallowed)."""
        try:
            return self.menu.select_label(label, expect=expect, **kw)
        except TypeError:
            pass
        if not self.menu.select_label(label, **kw):
            why = getattr(self.menu, "last_reason", None)
            return self._menu_fail(
                f"select_label({label}): row not confirmed"
                + (f"; {why}" if why else ""))
        for _ in range(3):
            if expect(self.emu.screen_text()):
                return True
            self.press("A:2 .:10")
            self.press(".:12")
        return self._menu_fail(
            f"select_label({label}): state not reached after 3 confirms")

    @contextlib.contextmanager
    def _money_watch(self, where):
        """Log any money change across a MOVEMENT call.

        Navigation must never spend money. It did once: an A-mash beside a
        Poke Mart clerk bought ¥1200 of ESCAPE ROPEs one 200¥ press at a
        time, and nothing noticed until the wallet was read hours later
        (AGENTS.md gotcha 13). Clerk identity does not exist at runtime --
        object_event sprite ids are not in WRAM -- so watch the symptom
        instead: the wallet, around every movement entry point. Only the
        OUTER call is wrapped, so a purchase during a nested dialog drain
        is reported once, with the map and cell it happened on.

        Only a DECREASE warns. Trainer winnings arrive mid-walk all the
        time and `MONEY +216 ... movement must never spend money` was a
        false alarm that trained the reader to ignore the line; the delta
        is still recorded either way.
        """
        try:
            before = self.emu.read_be("wMoney", 3)
        except Exception:            # duck-typed fakes without a wallet
            yield
            return
        try:
            yield
        finally:
            after = self.emu.read_be("wMoney", 3)
            if after != before:
                self.last_money_delta = after - before
            if after < before:
                log.warning(
                    f"  MONEY {after - before:+d} (now {after}) during "
                    f"{where} at {self.map_name()} {self.pos()[2:]} -- "
                    f"movement must never SPEND money")

    # -- observations ------------------------------------------------------

    def pos(self):
        e = self.emu
        return (e.read_u8("wMapGroup"), e.read_u8("wMapNumber"),
                e.read_u8("wXCoord"), e.read_u8("wYCoord"))

    def map_name(self):
        g, n, _, _ = self.pos()
        return self.names.maps.get((g, n), f"?{g}:{n}")

    def _map_const(self):
        """Position's CONST_NAME: grid()/camel are keyed by CONST_NAME
        (nav.camel maps CONST_NAME -> CamelCase)."""
        name = self.map_name()
        for const, camel in self.nav.camel.items():
            if camel == name:
                return const
        return name                       # already CONST_NAME (or unknown)

    def map_view(self, map_name=None):
        """ASCII view of the region reachable on foot/surf from the
        player's cell, with an ANNOTATION BLOCK under the grid naming the
        interesting cells by absolute coordinate.

        **This is art for humans. Decisions must come from the structured
        surface** -- `find_tiles`, `exits`, `tile_at`, `tiles_in`. The
        grid has a 5-column row gutter and a two-row x ruler, so answering
        "what is at x=15?" from it means counting characters in a
        monospace row, and a driving model got that wrong three times in
        one session (walked into an Ilex Forest wall 20x, put the Olivine
        pier warp at x=2 when it is x=3, and could only find the Vermilion
        Port Passage exit by grepping `warp_event`). The annotation block
        exists so the art can never disagree with the data.

        Pass a CamelCase or CONST map name to view another map from its
        origin cell (0,0)."""
        const = self._map_const()
        if map_name and map_name != self.map_name():
            const = next((c for c, camel in self.nav.camel.items()
                          if camel == map_name or c == map_name), None)
            if const is None:
                raise SystemExit(f"unknown map {map_name!r}")
            art = render_map_view(self.nav, const, (0, 0))
            npcs = ()
        else:
            npcs = sorted(self.npc_cells())
            art = render_map_view(self.nav, const, self.pos()[2:],
                                  npcs=npcs,
                                  surf=bool(getattr(self.nav, "surf", False)))
        return art + "\n" + self._map_annotations(const, art, npcs)

    # -- the structured map interface (decisions come from HERE) -----------

    def _grid_of(self, map_name=None):
        """(grid, const) for a map name / the current map."""
        const = self._map_const() if map_name is None else next(
            (c for c, camel in self.nav.camel.items()
             if camel == map_name or c == map_name), map_name)
        return self.nav.grid(const), const

    def tile_at(self, x, y, map_name=None):
        """Terrain word for ONE cell -- the same classifier
        `observe()['tiles']` uses, so the two can never disagree.
        'off-map' outside the grid."""
        grid, _ = self._grid_of(map_name)
        if not (0 <= y < len(grid) and 0 <= x < len(grid[0])):
            return "off-map"
        return _tile_kind(grid[y][x])

    def tiles_in(self, x0, y0, x1, y1, map_name=None):
        """A rectangle as ``{(x, y): kind}`` -- absolute coordinates, no
        gutters, no rulers, nothing to count. Bounds are inclusive and
        may be given in either order."""
        x0, x1 = sorted((int(x0), int(x1)))
        y0, y1 = sorted((int(y0), int(y1)))
        grid, _ = self._grid_of(map_name)
        out = {}
        for y in range(max(y0, 0), min(y1, len(grid) - 1) + 1):
            for x in range(max(x0, 0), min(x1, len(grid[0]) - 1) + 1):
                out[(x, y)] = _tile_kind(grid[y][x])
        return out

    def find_tiles(self, kind, map_name=None):
        """Every cell of a terrain `kind`, sorted -- the call that removes
        the character counting from the driving loop.

        `kind`: any word `tile_at` returns ('warp', 'water', 'grass',
        'floor', 'blocked', 'buoy', 'ice', 'pit', 'whirlpool',
        'waterfall', 'pc'), the FAMILY names 'ledge'/'sidewall' (which
        match 'ledge-up', 'sidewall-ur', ...), or 'npc' for live sprite
        cells.

        `find_tiles('warp')` answers off the collision bytes; `exits()`
        answers off the map's warp_events and edge connections. Both are
        useful and they are not the same question -- a warp tile with no
        event goes nowhere, and an event can sit on an odd byte."""
        want = str(kind).strip().lower()
        if want == "npc":
            if map_name not in (None, self.map_name(), self._map_const()):
                return []                 # live sprites are this map only
            return sorted(self.npc_cells())
        grid, _ = self._grid_of(map_name)
        out = []
        for y, row in enumerate(grid):
            for x, byte in enumerate(row):
                k = _tile_kind(byte)
                if k == want or (want in ("ledge", "sidewall")
                                 and k.startswith(want + "-")):
                    out.append((x, y))
        return sorted(out)

    def exits(self, map_name=None):
        """Every way OFF this map, with destinations -- the thing
        `grep warp_event maps/Foo.asm` was being used for.

        Warps come from the map's `warp_event`s and edge connections from
        its `connection` lines (the same nav data `travel` routes on):

            [{'kind': 'warp', 'x': 3, 'y': 14, 'to': 'VERMILION_PORT',
              'warp_id': 1},
             {'kind': 'connection', 'dir': 'north', 'to': 'ROUTE_6',
              'edge': 'y=0'}]
        """
        grid, const = self._grid_of(map_name)
        out = [{"kind": "warp", "x": x, "y": y, "to": dest, "warp_id": wid}
               for (x, y), (dest, wid) in
               sorted(self.nav.warps.get(const, {}).items())]
        edges = {"north": "y=0", "south": f"y={len(grid) - 1}",
                 "west": "x=0", "east": f"x={len(grid[0]) - 1}"}
        for d, (dest, _off) in sorted(
                self.nav.conns.get(const, {}).items()):
            out.append({"kind": "connection", "dir": d, "to": dest,
                        "edge": edges.get(d, "?")})
        return out

    def live_grid(self):
        """Collision grid decoded from the LIVE block map in WRAM -- the map
        the ENGINE is walking right now -- in nav.grid()'s shape.

        `wOverworldMapBlocks` holds the loaded map with a 3-block border on
        every side (stride = wMapWidth + 6, map block (bx,by) at
        (by+3)*stride + bx+3; the +1/+1 in GetMapScreenCoords is the screen
        anchor, not the map origin). Block ids go through the same tileset
        collision table nav uses, so any difference from nav.grid() is a
        real disagreement, not a decode artefact."""
        const = self._map_const()
        camel = self.nav.camel[const]
        w = self.emu.read_u8("wMapWidth")
        h = self.emu.read_u8("wMapHeight")
        stride = w + 6
        raw = self.emu.read("wOverworldMapBlocks", stride * (h + 6) + 8)
        table = self.nav._tileset_coll(self.nav.tileset[camel])
        grid = [[0x07] * (2 * w) for _ in range(2 * h)]
        for by in range(h):
            for bx in range(w):
                for i, c in enumerate(table[raw[(by + 3) * stride + bx + 3]]):
                    grid[by * 2 + i // 2][bx * 2 + i % 2] = c
        return grid

    def grid_drift(self):
        """Cells where the static decode disagrees with the live engine map:
        [(x, y, static_byte, live_byte), ...]. Empty is the normal answer.

        nav.grid() decodes maps/<Name>.blk, which is the map BEFORE any
        `changeblock` the map script fires (Burned Tower's basement ladder,
        Rocket base B3F's door to Giovanni, Goldenrod's underground doors).
        Those cells -- and only those -- can drift; conditional()/
        cell_kind()=='conditional' names them ahead of time. Audited across
        53 savestates of this run: 0 drift everywhere the events matched
        the default blockdata."""
        live, static = self.live_grid(), self.nav.grid(self._map_const())
        return [(x, y, static[y][x], live[y][x])
                for y in range(min(len(live), len(static)))
                for x in range(min(len(live[0]), len(static[0])))
                if live[y][x] != static[y][x]]

    def sync_grid(self):
        """Push every drifted cell into nav as a live override, so pathing
        uses the map the engine actually has. Returns the patched cells.

        Call it after any script that can open a door or drop a boulder --
        or just after arriving somewhere `conditional()` flags. Cheap
        (one WRAM read + a region-cache invalidation per cell) and
        idempotent; nav.clear_overrides() undoes it."""
        const = self._map_const()
        drift = self.grid_drift()
        for x, y, _static, live in drift:
            self.nav.set_cell(const, x, y, live)
        if drift:
            log.info(f"[sync_grid] {len(drift)} live cell(s) patched on "
                     f"{const}: " + " ".join(f"({x},{y})={l:#04x}"
                                             for x, y, _s, l in drift[:8]))
        return drift

    def _map_annotations(self, const, art, npcs=()):
        """The block printed under map_view's grid: every warp, NPC and
        water span the ART shows, by absolute coordinate.

        Built from the same calls a decision should use, so the picture
        and the data cannot drift apart. Cells outside the rendered window
        are counted, never silently dropped."""
        window = set()
        ox = self._view_origin(art)
        for line in art.splitlines():
            m = re.match(r"^(\s*\d+) (.*)$", line)
            if not m:
                continue
            y = int(m.group(1))
            for i, ch in enumerate(m.group(2)):
                if ch != " ":
                    window.add((ox + i, y))
        dest = {(e["x"], e["y"]): e["to"] for e in self.exits(const)
                if e["kind"] == "warp"}
        warps = self.find_tiles("warp", const)
        shown = [c for c in sorted(set(warps) | set(dest)) if c in window]
        hidden = len(set(warps) | set(dest)) - len(shown)
        lines = []
        if shown:
            lines.append("warps: " + "  ".join(
                f"({x},{y})" + (f"->{dest[(x, y)]}" if (x, y) in dest
                                else "->?")
                for x, y in shown)
                + (f"   (+{hidden} outside this view)" if hidden else ""))
        elif hidden:
            lines.append(f"warps: none in view (+{hidden} outside)")
        for e in self.exits(const):
            if e["kind"] == "connection":
                lines.append(f"edge:  {e['dir']} {e['edge']} -> {e['to']}")
        if npcs:
            lines.append("npcs:  " + " ".join(f"({x},{y})" for x, y in npcs))
        for line in self._offregion_lines(const):
            lines.append(line)
        for word in ("water", "grass"):
            cells = [c for c in self.find_tiles(word, const) if c in window]
            if cells:
                xs = [c[0] for c in cells]
                ys = [c[1] for c in cells]
                lines.append(f"{word}: rows {min(ys)}-{max(ys)}, "
                             f"x {min(xs)}-{max(xs)} ({len(cells)} cells)")
        try:
            drift = self.grid_drift() if const == self._map_const() else []
        except Exception as err:      # no live block map (other map, fake emu)
            log.debug(f"map_view: live grid unreadable ({err})")
            drift = []
        if drift:
            lines.append("DRIFT: the live engine map disagrees with the "
                         "decoded grid at " + "  ".join(
                             f"({x},{y}) {s:#04x}->{l:#04x}"
                             for x, y, s, l in drift[:6])
                         + (f"  (+{len(drift) - 6} more)"
                            if len(drift) > 6 else "")
                         + " -- a changeblock fired; call sync_grid()")
        lines.append("decide from find_tiles()/exits()/tiles_in(); "
                     "the grid above is for humans")
        return "\n".join(lines)

    def _offregion_lines(self, const):
        """One line per connected component the player cannot reach on this
        map, with its bounding box and the warp cells that open onto it.

        This is the fact that map_view's blanks used to swallow: Rocket
        base B3F's whole western wing (the rival and boss coord_events)
        hangs off two ladders that only B2F's left block can reach, and
        the render showed void. Sizes are cell counts, not blocks."""
        ids, count = self.nav.region_map(const)
        if count < 2:
            return []
        mine = set(self.nav.regions_at(const, *self.pos()[2:]))
        cells = {}
        for y, row in enumerate(ids):
            for x, rid in enumerate(row):
                if rid >= 0 and rid not in mine:
                    cells.setdefault(rid, []).append((x, y))
        if not cells:
            return []
        dest = {(e["x"], e["y"]): e["to"] for e in self.exits(const)
                if e["kind"] == "warp"}
        out = []
        for rid, pts in sorted(cells.items(),
                               key=lambda kv: -len(kv[1]))[:4]:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            doors = [f"({x},{y})->{dest[(x, y)]}"
                     for (x, y) in sorted(dest)
                     if rid in self.nav.regions_at(const, x, y)]
            if not doors:
                # no warp touches it: the wall itself opens, via a
                # changeblock the map script fires on an event
                doors = [f"changeblock at {c}"
                         for c in sorted(self.nav.conditional(const))
                         if any(abs(c[0] - x) + abs(c[1] - y) <= 1
                                for x, y in pts)][:3]
            if not doors:
                if len(pts) < 8:
                    continue    # a decorative 2-cell island, not a wing
                doors = ["no warp and no changeblock found"]
            extra = len(doors) - 4      # a warp-pad maze (Ecruteak Gym has
            doors = doors[:4]           # 26 pads into one region) is noise
            out.append(
                f"offregion: {len(pts)} walkable cells at x {min(xs)}-"
                f"{max(xs)}, y {min(ys)}-{max(ys)} -- NOT reachable from "
                f"here; enter via " + "  ".join(doors)
                + (f"  (+{extra} more)" if extra > 0 else ""))
        return out

    @staticmethod
    def _view_origin(art):
        """The x of the leftmost rendered column, from map_view's header."""
        m = re.search(r"origin=\((\d+),", art.splitlines()[0])
        return int(m.group(1)) if m else 0

    def battle(self):
        return self.emu.read_u8("wBattleMode")

    def textbox(self):
        return self.emu.tilemap()[12 * 20] == 0x79

    def cursor_rows(self):
        """Stripped upper-case screen rows that carry a menu cursor glyph."""
        return [r.strip().upper() for r in self.emu.screen_text()
                if ("▶" in r or "▷" in r)]

    def menu_open(self):
        """Is anything modal on screen (menu cursor or textbox)? The
        overworld draws neither, so this is the 'am I interactive' check
        every menu primitive must pass before returning (gotcha 7: a stray
        START menu silently eats all movement input)."""
        return bool(self.textbox()) or \
            any("▶" in r or "▷" in r for r in self.emu.screen_text())

    def scene_busy(self):
        """True while any scene owns the world: a script is running
        (wScriptMode != 0), a box/textbox is up, or a naming screen is
        open. wScriptMode alone LIES -- it reads 0 during naming screens
        and lags through chains (omp-fresh addendum #3); gate drains on
        this, not on sm == 0."""
        try:
            sm = self.emu.read_u8("wScriptMode")
        except Exception:
            sm = 0
        return bool(sm) or self.menu_open() or self.keyboard_open()

    def _screen_blank(self):
        """Menu open/close transitions render a frame or two of nothing;
        judging menu state on one is a lie (the menu redraws right after)."""
        return sum(1 for r in self.emu.screen_text() if r.strip()) < 2

    def close_menus(self, max_presses=14):
        """Postcondition helper: B out of any open menu/textbox stack until
        the overworld is interactive again. Blank fade frames are waited
        out, never judged, and 'closed' must hold on a settled re-check
        (the pack repaints ~50 frames after its close fade). Returns True
        when clean."""
        for _ in range(max_presses):
            if self._screen_blank():
                self.press(".:30")
                continue
            if not self.menu_open():
                self.press(".:40")            # outlast a pending repaint
                if not self.menu_open() and not self._screen_blank():
                    return True
                continue
            self.press("B:4 .:20")
        return not self.menu_open() and not self._screen_blank()

    def lead(self):
        s = game_state(self.emu, self.names)
        return s["party"][0] if s["party"] else None

    def status(self, missing=True):
        """One-line status. `missing=True` (default) appends a compact
        `missing: FLY(CIANWOOD_CITY 10,46) ...` fragment.

        This exists because a whole playthrough reached Champion without
        HM02 FLY -- it sat with Chuck's wife in Cianwood from the Storm
        Badge onward and NOTHING ever said so, so every journey of that
        run was on foot. status() is printed after almost every command,
        which makes it the one place a session cannot fail to look."""
        line = status_line(game_state(self.emu, self.names))
        if missing:
            try:
                frag = missables.status_fragment(self.missables())
            except Exception as exc:          # never break status()
                frag = f"missing: ? ({type(exc).__name__})"
            if frag:
                line += "  " + frag
        return line

    # -- missable items ----------------------------------------------------

    _item_sources = None

    def item_sources(self):
        """Every place in the world an item can be obtained
        (crystalagent.missables.parse_item_sources), parsed once."""
        if Driver._item_sources is None:
            Driver._item_sources = missables.parse_item_sources(
                paths.REPO_ROOT, _file_const)
        return Driver._item_sources

    def missables(self, kind="key"):
        """Items that are still out there, evaluated LIVE.

        `kind='key'` (default) is the subset that changes what the player
        can DO -- the game's own KEY_ITEM pocket plus the HMs;
        `kind='all'` is every giveitem/verbosegiveitem/itemball site in
        maps/. Each row is citable:

            {'item': 'HM_FLY', 'have': False, 'map': 'CIANWOOD_CITY',
             'x': 10, 'y': 46, 'event': 'EVENT_GOT_HM02_FLY',
             'source': 'maps/CianwoodCity.asm:100', ...}

        Obtained-ness comes from the guarding EVENT_GOT_* flag where the
        script has one, and from the bag otherwise."""
        def have_event(flag):
            try:
                return self._event_flag(flag)
            except ValueError:
                return None               # unknown flag: ask the bag
        return missables.missing_items(
            self.item_sources(), have_event=have_event, bag=self._bag(),
            repo=paths.REPO_ROOT, kind=kind)

    def field_moves(self):
        """``{'CUT': 'GATOR', 'FLY': None, ...}`` -- for each HM move,
        which party member can actually use it.

        "HM in the bag" is not "I can use it", and `FLY: None` is the
        single fact that would have saved an hour of walking."""
        out = {}
        for tag, const in sorted(missables.hm_moves(paths.REPO_ROOT).items()):
            name = next((n for n in self.names.moves.values()
                         if _norm_item(n) == _norm_item(const)),
                        const.replace("_", " "))
            knower = None
            for mon in game_state(self.emu, self.names)["party"]:
                if any(mv["name"] == name for mv in mon.get("moves", [])):
                    knower = mon.get("nickname") or mon.get("name")
                    break
            out[name] = knower
        return out

    def dark_maps(self):
        """Map CONSTs that are pitch dark without FLASH (13 of them:
        ROCK_TUNNEL, DARK_CAVE, the WHIRL_ISLANDS, SILVER_CAVE_ROOM_1).

        Keyed on the map's PALETTE, not its tileset -- MOUNT_MORTAR and the
        ICE_PATH are TILESET_DARK_CAVE / ICE_PATH but PALETTE_NITE, and
        need no FLASH (see missables.DARK_PALETTE)."""
        camel_to_const = {c: k for k, c in self.nav.camel.items()}
        out = set()
        for camel in missables.dark_map_names(paths.REPO_ROOT):
            const = camel_to_const.get(camel)
            if const:
                out.add(const)
        return out

    def needs_flash(self, map_name=None):
        """Is this map unusable without FLASH? Defaults to the current map."""
        return self._resolve_map(map_name) in self.dark_maps()

    def blocked_by(self):
        """Field-move gates I cannot currently pass, as
        ``{'FLASH': [MAP_CONST, ...]}``.

        `missables()` says what I do not HAVE; this says what that
        COSTS me. Live example that motivated it: a party with
        ``FLASH: None`` cannot use ROCK_TUNNEL (the Kanto shortcut),
        SILVER_CAVE_ROOM_1 (Red) or the WHIRL_ISLANDS (Lugia) -- three
        objectives gated by one uncollected HM."""
        out = {}
        if self.field_moves().get("FLASH") is None:
            dark = sorted(self.dark_maps())
            if dark:
                out["FLASH"] = dark
        return out

    # -- live sprites ------------------------------------------------------
    # Patience budget for an NPC squatting the only path: wanderers step
    # off on their own, so waiting beats storming 20 replans. Stationary
    # types never move -- those fail loudly instead of burning the window.
    WANDER_WAIT_CHUNK = 150      # frames between re-checks
    WANDER_WAIT_FRAMES = 600     # total patience per blocker cell

    def sprites(self):
        """LIVE overworld sprites from wObjectStructs (slot 0 = player).
        wMapObjects holds the map's STATIC definitions and never moves --
        reading it made pushed boulders look like they had reset."""
        return live_sprites(self.emu)

    def map_objects(self, map_name=None):
        """Every ``object_event`` this map DECLARES, read from its own
        source: ``[{'x','y','sprite','movement','script','event'}]``.

        The static counterpart of `sprites()`: those are live positions,
        these are the map's definitions -- which is what answers "where
        does this map keep its nurse/clerk" without hardcoding a layout."""
        const = self._resolve_map(map_name) if map_name else self._map_const()
        camel = self.nav.camel.get(const, const)
        return missables.parse_map_objects(
            Path(paths.REPO_ROOT, "maps", f"{camel}.asm"))

    def sprite_cell(self, sprite, map_name=None):
        """(x, y) of the first object_event with sprite constant `sprite`
        ('SPRITE_NURSE', 'SPRITE_CLERK'), or None. Coordinates are walk
        cells -- the same space `pos()` and `talk_to` use."""
        want = str(sprite).strip().upper()
        for o in self.map_objects(map_name):
            if o["sprite"].upper() == want:
                return o["x"], o["y"]
        return None

    def npc_cells(self):
        """Cells occupied by live NPCs (walk-cell coords, player excluded).
        Degrades to empty when the struct table cannot be read, so nav
        keeps working on reduced fakes/odd states."""
        try:
            return {(s["map_x"], s["map_y"])
                    for s in self.sprites() if s["slot"]}
        except Exception:
            return set()

    def _blocker_kind(self, cell):
        """'wanderer' | 'stationary' | None for the sprite standing on
        `cell`. None means "cannot tell" (unreadable table, or nothing
        there any more) -- callers fall back to legacy handling."""
        try:
            live = self.sprites()
        except Exception:
            return None
        for s in live:
            if s["slot"] and (s["map_x"], s["map_y"]) == cell:
                return ("wanderer" if s["movement"] in SPRITE_WANDERERS
                        else "stationary")
        return None

    def _wait_out_wanderer(self, cell):
        """Idle in WANDER_WAIT_CHUNK slices until the sprite on `cell`
        steps off, up to WANDER_WAIT_FRAMES. True = cell is free (or no
        longer knowable, so the walk may as well try)."""
        waited = 0
        while waited < self.WANDER_WAIT_FRAMES:
            self.press(f".:{self.WANDER_WAIT_CHUNK}")
            waited += self.WANDER_WAIT_CHUNK
            try:
                busy = any(s["slot"] and (s["map_x"], s["map_y"]) == cell
                           for s in self.sprites())
            except Exception:
                return True
            if not busy:
                log.info(f"  wanderer left {cell} after {waited}f")
                return True
        return False

    def _sprites_obs(self):
        """observe()'s sprite list; empty when the table is unreadable."""
        try:
            return self.sprites()
        except Exception:
            return []

    def _event_flag(self, name):
        """True if event flag EVENT_<name> (or bare <name>) is set, read
        banked from wEventFlags. Symbol must exist in
        constants/event_flags.asm -- unknown names raise."""
        global _event_flag_names
        if _event_flag_names is None:
            _event_flag_names = _parse_event_flags(
                paths.REPO_ROOT / "constants" / "event_flags.asm")
        for key in (name, "EVENT_" + name):
            if key in _event_flag_names:
                bit = _event_flag_names[key]
                break
        else:
            raise ValueError(f"unknown event flag {name!r}")
        bank, addr = self.emu.sym["wEventFlags"]
        return bool(self.emu.read((bank, addr + bit // 8))[0]
                    >> (bit % 8) & 1)

    _engine_flag_index = None

    def engine_flag(self, name):
        """True if engine flag ENGINE_<name> (or bare <name>) is set.

        The index comes from constants/engine_flags.asm; the (address,
        mask) pair comes from the ROM's OWN assembled `EngineFlags` table
        (data/events/engine_flags.asm, 3 bytes per entry: little-endian
        WRAM address then the mask), so no bit constant is retyped here.
        All those addresses are WRAM bank 1. Unknown names raise."""
        if Driver._engine_flag_index is None:
            from crystalagent.asmconst import parse_const_defs
            Driver._engine_flag_index = parse_const_defs(
                paths.REPO_ROOT / "constants" / "engine_flags.asm")
        table = Driver._engine_flag_index
        for key in (name, "ENGINE_" + str(name)):
            if key in table:
                idx = table[key]
                break
        else:
            raise ValueError(f"unknown engine flag {name!r}")
        bank, addr = self.emu.sym["EngineFlags"]
        entry = self.emu.read((bank, addr + 3 * idx), 3)
        target = int.from_bytes(entry[:2], "little")
        return bool(self.emu.read((1, target))[0] & entry[2])

    def _bag(self):
        """{ITEM: qty} across all pockets; names normalized like
        _norm_item ('# BALL' -> 'POKE BALL')."""
        e = self.emu
        bag = {}
        for count_sym, list_sym in (("wNumItems", "wItems"),
                                    ("wNumBalls", "wBalls"),
                                    ("wNumKeyItems", "wKeyItems")):
            n = min(e.read_u8(count_sym), 26)
            if not n:
                continue
            bank, addr = e.sym[list_sym]
            # key items are 1 byte each (no quantity); other pockets are
            # (id, qty) pairs -- the pair stride used to hide every other
            # key item and report garbage quantities
            step = 1 if list_sym == "wKeyItems" else 2
            raw = e.read((bank, addr), n * step)
            for i in range(n):
                name = _norm_item(self.names.items.get(raw[i * step],
                                                       f"?{raw[i * step]}"))
                qty = raw[i * step + 1] if step == 2 else 1
                bag[name] = bag.get(name, 0) + qty
        bank, addr = e.sym["wTMsHMs"]          # one count byte per TM/HM
        counts = e.read((bank, addr), _NUM_TMS + _NUM_HMS)
        for i, n in enumerate(counts):
            if n:
                key = (f"TM{i + 1:02d}" if i < _NUM_TMS
                       else f"HM{i - _NUM_TMS + 1:02d}")
                bag[key] = bag.get(key, 0) + n
        return bag

    def observe(self):
        """JSON-serializable full game snapshot (the serve.py contract)."""
        global _move_base_pps
        s = game_state(self.emu, self.names)
        loc = s["location"]
        if _move_base_pps is None:
            _move_base_pps = _load_move_base_pps(paths.ROM,
                                                 self.emu.sym)
        move_id_by_name = {n: i for i, n in self.names.moves.items()}
        party = []
        for m in s["party"]:
            moves = []
            for mv in m["moves"]:
                mid = move_id_by_name[mv["name"]]
                base = _move_base_pps[mid - 1]
                ups = mv["pp"] >> 6               # stored PP byte: bits 7-6
                cur = mv["pp"] & 0x3F             # are PP-Up count, 5-0 cur
                moves.append({"name": mv["name"], "pp": cur,
                              "max_pp": base + min(base // 5, 7) * ups})
            party.append({
                "species": m["name"], "nick": m["nickname"],
                "level": m["level"], "hp": m["hp"],
                "max_hp": m["max_hp"],
                "status": "+".join(m["status"]) or None,
                "moves": moves,
                # An egg reads 0 HP with a live-looking mon struct, so
                # anything that treats hp<=0 as "fainted" must be able to
                # see this. train()'s heal rail could not: with the Togepi
                # egg in the party it healed 30+ times in a row and never
                # fought (FUCK_I_MESSED_UP.md #20).
                "egg": bool(m.get("egg")),
            })
        flags = {}
        for const in ("GOT_MYSTERY_EGG_FROM_MR_POKEMON",
                      "GAVE_MYSTERY_EGG_TO_ELM",
                      "GOT_TOGEPI_EGG_FROM_ELMS_AIDE"):
            flags[const] = self._event_flag(const)
        flags["POKEDEX"] = bool(
            self.emu.read_u8("wStatusFlags") & _STATUSFLAGS_POKEDEX_F)
        # tile context: the deciding loop needs terrain (tall grass?
        # water? warp?) to own training/routing decisions -- without it
        # pacing decisions required a bundled leg like grind()
        tiles = {}
        try:
            g = self.nav.grid(self.map_name())
            cx, cy = self.pos()[2:]

            kind = _tile_kind

            tiles["here"] = kind(g[cy][cx])
            for dd, (dx, dy) in STEP.items():
                nx, ny = cx + dx, cy + dy
                tiles[dd.lower()] = (kind(g[ny][nx])
                                     if 0 <= ny < len(g)
                                     and 0 <= nx < len(g[0]) else "off-map")
        except Exception:
            pass
        obs = {
            "map": loc["map"], "group": loc["map_group"],
            "number": loc["map_number"], "x": loc["x"], "y": loc["y"],
            "tiles": tiles,
            "party": party,
            "bag": self._bag(),
            "money": s["player"]["money"],
            "badges": s["player"]["johto_badges"] + s["player"]["kanto_badges"],
            "flags": flags,
            "npcs": sorted([list(c) for c in self.npc_cells()]),
            "sprites": self._sprites_obs(),
            "ui": {"textbox": self.textbox(),
                   "battle": bool(s["battle"])},
            "frame": s["frame"],
        }
        if s["battle"]:
            # foe visibility during battles: targeting decisions for
            # catching/type-matching were blind without it (omp-fresh
            # Q&A #3.2)
            try:
                en = Battle(self.emu, self.names, self.bdata).enemy()
                tnames = {i: n for n, i in self.bdata.types.items()}
                obs["enemy"] = {"species": en["species"], "name": en["name"],
                                "level": en["level"], "hp": en["hp"],
                                "max_hp": en["max_hp"],
                                "types": [tnames.get(t, str(t))
                                          for t in en.get("types", [])]}
            except Exception:
                pass
        return validate_observe(obs)

    def settle(self, quiet=3, spacing=20, max_frames=900):
        """Wait until map + position stop changing: door/cutscene warps
        finish asynchronously a beat AFTER the step that triggered them,
        so anything acting on the map right after a step must settle."""
        last, still = None, 0
        f0 = self.emu.frame
        while self.emu.frame - f0 < max_frames:
            if self.battle():
                return
            cur = self.pos()
            if cur == last:
                still += spacing
                if still >= quiet * spacing:
                    return
            else:
                last, still = cur, 0
            self.press(f".:{spacing}")

    def _grid_miss(self, what, exc):
        """A cell question the decoded map cannot answer.

        IndexError is ordinary (a coordinate one past the map edge); a
        KeyError means this map has no decoded grid at all, which silently
        turns every door on it into "not a warp cell" -- so say so."""
        self.last_step_reason = f"{what}: {type(exc).__name__}: {exc}"
        if isinstance(exc, KeyError):
            log.info(f"  no decoded grid for {self.map_name()} ({what})")
        return False

    def _is_warp_cell(self, x, y):
        try:
            grid = self.nav.grid(self.map_name())
            return grid[y][x] in WARPS
        except (KeyError, IndexError) as exc:
            return self._grid_miss(f"warp-cell({x},{y})", exc)

    def step_hold(self, mv, hold=80):
        """Hold the direction through the whole step AND the map
        transition. Door warps only fire if the key is still down when
        the step completes -- step_dir's early release skips them."""
        before = self.pos()
        self.emu.py.button_press(DIRS[mv].lower())
        self.emu.tick(hold)
        self.emu.py.button_release(DIRS[mv].lower())
        self.emu.tick(2)
        self.settle(max_frames=400)
        if self.battle():
            return "battle"
        now = self.pos()
        if now[:2] != before[:2]:
            return "warp"
        return "moved" if now != before else "blocked"

    def _is_water_cell(self, x, y):
        try:
            grid = self.nav.grid(self.map_name())
            return grid[y][x] in _NAV_WATER
        except (KeyError, IndexError) as exc:
            return self._grid_miss(f"water-cell({x},{y})", exc)

    def _mount_surf(self, mv):
        """Face the water and start surfing: walking into water does NOT
        prompt in GSC -- you must face it and press A ('The water is
        calm... SURF?' -> YES). Ends riding ON the water cell.

        Verified by EITHER wPlayerState==PLAYER_SURF or the avatar having
        actually moved onto the target cell: at a map-edge seam (New Bark
        -> Route 27) the mount slid us onto the water and still reported
        'blocked' off the state byte alone, so callers hand-rolled raw
        presses to cross."""
        before = self.pos()
        x, y = before[2:]
        dx, dy = STEP[mv]
        target = (x + dx, y + dy)
        self.step_dir(mv)              # blocked step = turn toward water
        for _ in range(10):
            s = "".join(self.emu.screen_text()).upper()
            if "YES" in s and "NO" in s:
                break
            self.press("A:4 .:30")
        else:
            return "blocked"
        self.press("A:5 .:40")         # YES
        self.settle(max_frames=600)    # mount animation slides onto water
        now = self.pos()
        if now[:2] != before[:2]:
            return "warp"              # seam crossing rode us to the next map
        if self.emu.read_u8("wPlayerState") == 4 or now[2:] == target:
            return "moved"
        return "blocked"

    def _step(self, mv):
        """step_dir, but switch to a held step when the target cell is a
        warp tile so doors actually trigger, and to the surf-mount flow
        when stepping from land onto water."""
        x, y = self.pos()[2:]
        dx, dy = STEP[mv]
        if self._is_warp_cell(x + dx, y + dy):
            r = self.step_hold(mv)
            if r != "moved" and r != "warp":
                r = self._step_warp_tap(mv)
            return r
        if self.nav.surf and self._is_water_cell(x + dx, y + dy) and \
                self.emu.read_u8("wPlayerState") != 4:
            return self._mount_surf(mv)
        return self.step_dir(mv)

    def _step_warp_tap(self, mv):
        """COLL_STAIRCASE tiles (CheckWarpFacingDown, tile_events.asm:35)
        push the player back OFF the tile if the key is still held ~60+
        frames after arrival -- long holds never warp. Tap-and-release at
        varying durations shifts the frame phase until the stop-on-tile
        lands inside the warp check."""
        button = DIRS[mv].lower()
        for hold in (56, 44, 64, 36, 72, 52):
            before = self.pos()
            self.emu.py.button_press(button)
            self.emu.tick(hold)
            self.emu.py.button_release(button)
            self.emu.tick(2)
            self.settle(max_frames=400)
            if self.battle():
                return "battle"
            now = self.pos()
            if now[:2] != before[:2]:
                return "warp"
            if now != before:
                return "moved"
        return "blocked"

    def _held_warp_entry(self, st):
        """Gotcha 12 last resort for a door warp a plain transition step
        crossed without firing: re-approach along the warp tile's
        row/column and drive onto it -- held first when adjacent (doors
        need the key down through the step), then _step_warp_tap's
        phase-shifted taps (staircase tiles bounce held keys back off).
        Multi-warp door rows (Sprout Tower 1F's double door) make a held
        glide cross BOTH tiles without firing; walking back tap-by-tap
        lands on one. Returns 'warp' | 'battle' | None (couldn't fire)."""
        tx, ty = st["cell"]
        px, py = self.pos()[2:]
        dist = abs(px - tx) + abs(py - ty)
        if dist == 0 or dist > 3 or (px != tx and py != ty):
            return None         # on the tile, too far, or off its axes
        if px != tx:
            mv = "R" if tx > px else "L"
        else:
            mv = "D" if ty > py else "U"
        log.info(f"  held-entry fallback: {mv} onto warp {(tx, ty)} "
              f"from {(px, py)}")
        if dist == 1:
            r = self.step_hold(mv)
            if r in ("warp", "battle"):
                return r
        r = self._step_warp_tap(mv)
        return r if r in ("warp", "battle") else None

    # Distinct diagnosis for the last take_warp; None after a success.
    last_warp_reason = None

    def _warp_fail(self, reason):
        self.last_warp_reason = reason
        log.warning(f"  take_warp: {reason}")
        return False

    def _same_map_landing(self, target):
        """Where a SAME-MAP warp at `target` lands, or None when the warp
        leaves the map (or is not a warp here).

        A warp_event's destination is (map, warp id), and warp ids are
        1-based positions in that map's own `def_warp_events` -- the order
        nav.warps preserves. Victory Road's `warp_event 13, 31,
        VICTORY_ROAD, 5` therefore lands on the 5th entry, (13,17)."""
        try:
            const = self._map_const()
            table = self.nav.warps.get(const, {})
        except Exception:
            return None
        dest = table.get(tuple(target))
        if not dest or dest[0] != const:
            return None
        cells = list(table)
        idx = int(dest[1]) - 1
        return cells[idx] if 0 <= idx < len(cells) else None

    @staticmethod
    def _warp_fired(start_map, start_pos, target, now_map, now_pos,
                    landing=None):
        """Did the warp at `target` actually fire?

        A different map is the obvious yes. But Victory Road, the Ice Path
        and Silver Cave stack their floors inside ONE map and join them
        with same-map warp_events, so the map name never changes and only
        the position teleports: stepping onto (13,31) lands on (13,17)
        fourteen rows away. Judging by map alone reported those ladders as
        failures the caller could not act on (FUCK_I_MESSED_UP #77).

        A same-map yes needs the LANDING CELL, not a distance. "moved more
        than 3 cells" was the first rule, and it reports success for a
        walk: re-entering Kurt's house exit (3,7), the tap fallback walks
        west to (0,7) -- 4 cells from where we stood, same map, no warp --
        and take_warp answered True with the player still indoors
        (tests/integration/test_take_warp_entry.py). `landing` comes from
        the map's own warp table (_same_map_landing); arrival drifts up to
        ~2 cells past the modeled cell (gotcha 14), so a same-map yes is
        "we JUMPED, and we came down on the paired cell". Both halves are
        needed: without the jump, walking two cells away from a landing we
        were already standing on reads as a teleport. No pairing, same
        map: not fired."""
        if now_map != start_map:
            return True
        if now_pos == target or landing is None:
            return False              # standing on it is not entering
        jumped = abs(now_pos[0] - start_pos[0]) + \
            abs(now_pos[1] - start_pos[1]) > 2
        return jumped and abs(now_pos[0] - landing[0]) + \
            abs(now_pos[1] - landing[1]) <= 2

    def take_warp(self, x, y, label=""):
        """ENTER the warp at (x, y) -- and standing on it is not entering.

        A warp fires when the player STEPS ONTO its tile with the key
        still down; arriving on one never re-triggers it. So a leg that
        ends standing on the tile (every door arrival) needs a step OFF
        and back ON, which is what cost turns at the Ilex/Azalea gate, the
        Union Cave north mouth, the Olivine pier and three ship cabins.
        `travel` reported that as `warp D at (3,41) -- expected
        ILEX_FOREST_AZALEA_GATE ... (step result: blocked)` when the real
        answer was "you are already on it".

        Order: step off if we are on it, walk adjacent if we are not, then
        enter held (doors need the key down) with `_step_warp_tap`'s
        phase-shifted taps as the fallback (staircases bounce held keys).
        True only when the MAP CHANGED; every False sets
        `last_warp_reason`."""
        self.last_warp_reason = None
        target = (int(x), int(y))
        start_map = self.map_name()
        if label:
            log.info(f"[take_warp {target}] {label} from {start_map} "
                     f"{self.pos()[2:]}")
        # Coordinates belong to a MAP. A caller holding coords from the
        # map it just left would otherwise be routed somewhere unrelated
        # and warped into whatever is there (observed live: stale gym
        # coords sent the walk into POKE_SEERS_HOUSE).
        try:
            const = self._map_const()
            known = self.nav.warps.get(const, {})
            checkable = True
        except Exception:          # duck-typed driver with no map data
            const, known, checkable = start_map, {}, False
        if checkable and self.tile_at(*target) != "warp" \
                and target not in known:
            return self._warp_fail(
                f"{target} is not a warp on {const} "
                f"(tile={self.tile_at(*target)}); warps here: "
                f"{[(e['x'], e['y'], e['to']) for e in self.exits() if e['kind'] == 'warp']}")
        if self.pos()[2:] == target:
            return self._reenter_warp(target, start_map)
        px, py = self.pos()[2:]
        aligned = ((px == target[0]) != (py == target[1])) and \
            abs(px - target[0]) + abs(py - target[1]) <= 3
        if not aligned:
            for mv, (dx, dy) in STEP.items():
                nx, ny = target[0] + dx, target[1] + dy
                if self.tile_at(nx, ny) in ("blocked", "off-map"):
                    continue
                if self.goto(nx, ny, label or f"approach warp {target}"):
                    break
            else:
                return self._warp_fail(
                    f"no reachable cell adjacent to {target} "
                    f"(last goto: {self.last_goto_reason})")
        entry_pos = self.pos()[2:]
        r = self._held_warp_entry({"kind": "warp", "cell": target})
        if r == "battle":
            if not self._on_battle(f"take_warp {target}"):
                return self._warp_fail(
                    f"battle entering {target} and auto_fight=manual -- "
                    f"decide it, then retry")
        self.settle()
        landing = self._same_map_landing(target)
        if self._warp_fired(start_map, entry_pos, target, self.map_name(),
                            self.pos()[2:], landing):
            log.info(f"  -> {self.map_name()} {self.pos()[2:]}")
            return True
        if self.pos()[2:] == target:
            # we are standing on it now: re-enter properly instead of
            # reporting a failure the caller cannot act on
            return self._reenter_warp(target, start_map)
        return self._warp_fail(
            f"entered {target} from {self.pos()[2:]} but the map is still "
            f"{start_map} (entry result: {r})")

    def _reenter_warp(self, target, start_map):
        """Step off the warp we are standing on and back onto it, trying
        every walkable side.

        The side matters: a door only fires when entered along its own
        axis (`CheckWarpFacingDown` and friends,
        engine/overworld/tile_events.asm), so re-entering a south-wall
        door sideways does nothing at all -- observed live on Cianwood
        Gym's exit, where stepping off RIGHT and back LEFT left the map
        unchanged."""
        inv = {"U": "D", "D": "U", "L": "R", "R": "L"}
        tried = []
        for mv, (dx, dy) in STEP.items():
            nx, ny = target[0] + dx, target[1] + dy
            if self.tile_at(nx, ny) in ("blocked", "off-map"):
                continue
            if self.pos()[2:] != target:      # a previous side left us off
                back = self._axis_move(target)
                if back and self._step(back) == "battle":
                    if not self._on_battle(f"take_warp {target}"):
                        return self._warp_fail(
                            f"battle re-entering {target} and "
                            f"auto_fight=manual -- decide it, then retry")
                if self.map_name() != start_map:
                    self.settle()
                    log.info(f"  -> {self.map_name()} {self.pos()[2:]}")
                    return True
                if self.pos()[2:] != target:
                    # `_axis_move` is a single step and cannot always get
                    # back (a south-wall door's only step-off is vertical,
                    # so the horizontal attempts leave us one cell away on
                    # the wrong axis). Falling through with `continue` here
                    # silently ATE the remaining sides, so U/D were never
                    # tried and the caller stranded off-target -- found by
                    # tests/integration/test_take_warp_entry.py on Kurt's
                    # house exit (3,7). Walk back properly instead: the
                    # docstring promises every walkable side, so try them.
                    try:
                        self.goto(*target)
                    except Exception:
                        pass
                    if self.pos()[2:] != target:
                        continue
            if self._step(mv) == "battle":
                if not self._on_battle(f"take_warp {target}"):
                    return self._warp_fail(
                        f"battle stepping off {target} and "
                        f"auto_fight=manual -- decide it, then retry")
            if self.map_name() != start_map:
                return True               # the step off was itself a warp
            if self.pos()[2:] == target:
                continue                  # could not step off this way
            tried.append(mv)
            off = self.pos()[2:]
            r = self.step_hold(inv[mv])
            if r == "battle":
                if not self._on_battle(f"take_warp {target}"):
                    return self._warp_fail(
                        f"battle entering {target} and auto_fight=manual "
                        f"-- decide it, then retry")
            if self.map_name() == start_map and self.pos()[2:] == target:
                r = self._step_warp_tap(inv[mv])
            self.settle()
            if self._warp_fired(start_map, off, target, self.map_name(),
                                self.pos()[2:],
                                self._same_map_landing(target)):
                log.info(f"  -> {self.map_name()} {self.pos()[2:]} "
                         f"(entered {inv[mv]})")
                return True
        if not tried:
            return self._warp_fail(
                f"standing on {target} with no walkable neighbour to step "
                f"off to")
        return self._warp_fail(
            f"stepped off {target} and back on from {'/'.join(tried)} and "
            f"the map is still {start_map} -- not an active warp?")

    def _axis_move(self, target):
        """The move that steps from here onto `target`, when adjacent."""
        px, py = self.pos()[2:]
        for mv, (dx, dy) in STEP.items():
            if (px + dx, py + dy) == tuple(target):
                return mv
        return None

    # -- actions -----------------------------------------------------------

    def press(self, seq):
        # naming-screen freeze (moss-run postmortem): while a keyboard is
        # up, ONLY explicit type_name/dismiss_keyboard may type -- any
        # other A/B/START press would insert chars or commit garbage.
        if self.keyboard_open() and not getattr(self, "_naming_busy", False):
            seq = ",".join(t for t in seq.split()
                           if t.upper().startswith((".:", "D:")))
            if not seq:
                return
        self.emu.run_sequence(parse_sequence(seq))

    def step_dir(self, mv, max_frames=40):
        """Take exactly one step using the engine's own step state
        (wPlayerStepFlags: bit7 = step started, bit6 = step stopped).
        Returns 'moved' | 'blocked' | 'battle' | 'warp'."""
        before = self.pos()
        button = DIRS[mv].lower()
        for _attempt in range(2):  # a turn-in-place consumes the first "step"
            self.emu.py.button_press(button)
            started = False
            for _ in range(max_frames):
                self.emu.tick(1)
                if self.battle():
                    self.emu.py.button_release(button)
                    return "battle"
                if self.emu.read_u8("wPlayerStepFlags") & 0x80:
                    started = True
                    break
            self.emu.py.button_release(button)
            if not started:
                return "battle" if self.battle() else "blocked"
            for _ in range(48):  # ledge hops take longer than plain steps
                self.emu.tick(1)
                if self.emu.read_u8("wPlayerStepFlags") & 0x40:
                    break
            self.emu.tick(2)
            if self.battle():
                return "battle"
            now = self.pos()
            if now[:2] != before[:2]:
                return "warp"
            if now != before:
                return "moved"
        return "blocked"

    def _script_or_text(self):
        """Obstacle-prompt detector: wScriptMode != 0 OR a textbox. The
        whirlpool/waterfall/surf ask-menu raises wScriptMode==2 for ~60
        frames with textbox()==False and BLANK glyph text, so scene_busy's
        menu-cursor scrape never sees it."""
        try:
            if self.emu.read_u8("wScriptMode"):
                return True
        except Exception:
            pass
        return bool(self.textbox())

    def move_settled(self, mv, hold=40, max_frames=600, fight=None):
        """One directional move sampled SAFELY: press `mv` held `hold`
        frames, then poll pos() until it reads identical 3 times in a
        row -- a single read mid-slide/mid-walk reports the tile being
        crossed, so sampling right after the press lies. Textboxes are
        paged (A) en route.

        Battles are SURFACED, never swallowed: a step is not a journey,
        so an encounter returns 'battle' with the battle still up and
        the decision left to the caller (wren pt6 -- a model-written
        pacing loop reported fights=0 while this method fought ~20
        battles on the DEFAULT policy and whited the party out).
        `fight=True` restores the old play-it-out behaviour; `fight=None`
        follows self.auto_fight AND self.auto_fight_steps (False by
        default, i.e. surface it).
        Returns 'moved' | 'blocked' | 'warp' | 'battle'."""
        before = self.pos()
        self.press(f"{mv}:{hold}")
        last, stable = None, 0
        f0 = self.emu.frame
        while self.emu.frame - f0 < max_frames:
            if self.battle():
                if not self._step_fights(fight):
                    return "battle"
                self._on_battle(f"move_settled {mv}", fight=True)
                self.emu.tick(2)     # guarantee frame progress
                last, stable = None, 0
                continue
            if self.textbox():
                self.press("A:8 .:40")
                last, stable = None, 0
                continue
            cur = self.pos()
            if cur == last:
                stable += 1
                if stable >= 3:
                    break
            else:
                last, stable = cur, 1
            self.press(".:10")
        now = self.pos()
        if now[:2] != before[:2]:
            return "warp"
        return "moved" if now != before else "blocked"

    def _may_fight(self, fight=None):
        """Journey resolution of the tri-state `fight` argument: an
        explicit True/False wins, None follows self.auto_fight."""
        if fight is not None:
            return bool(fight)
        return bool(getattr(self, "auto_fight", True))

    def _step_fights(self, fight=None):
        """Step-primitive resolution of `fight`: an explicit True/False
        wins; None requires BOTH self.auto_fight and the opt-in
        self.auto_fight_steps, so the default is to surface the battle."""
        if fight is not None:
            return bool(fight)
        return (bool(getattr(self, "auto_fight", True))
                and bool(getattr(self, "auto_fight_steps", False)))

    def _on_battle(self, where="", fight=None):
        """The ONE path by which a nav/field helper plays out an encounter
        it walked into, so a policy/encounter hook always applies (every
        route goes through fight(), never a private shortcut).

        Returns True when the battle was fought -- the caller must still
        check _whiteout_stop() -- and False when it is handed BACK
        untouched (auto_fight=manual), with last_goto_reason set so the
        refusal is diagnosable instead of silent."""
        if not self._may_fight(fight):
            self.last_goto_reason = (
                f"battle during {where} (auto_fight=manual) -- "
                "decide: fight()/catch() yourself")
            log.info(f"  battle during {where}: handing it to the "
                     f"decider (auto_fight=manual)")
            return False
        self.fight()
        return True

    def clear_obstacle(self, direction, tries=6):
        """Clear a prompt-gated field obstacle one step in `direction`:
        whirlpools ($24), waterfalls ($33), and the surf-mount ask when
        stepping from land onto water. Live evidence (wren pt6): bumping
        one raises wScriptMode==2 for ~60 frames with textbox()==False
        and the YES/NO ask-menu drawn in BLANK glyphs -- real but
        invisible. A pause->A->pause cadence answers it; a fuzzer found
        sequences like '.:40 A:8 .:30' / 'U:40 .:40' work where tight
        mash loops always fail, so A presses keep >=40-frame gaps.
        Returns 'moved' (position or map changed), 'cleared-not-moved'
        (a prompt was answered but the follow-up step didn't take --
        retry a plain move), 'battle' (an encounter interrupted and
        auto_fight=manual: decide it, then retry), or 'failed' (no
        prompt ever appeared: plain wall)."""
        prompted = False
        for _attempt in range(tries):
            before = self.pos()
            self.press(f"{direction}:20 .:10")   # face + bump
            poked = False
            f0 = self.emu.frame
            while self.emu.frame - f0 < 90:
                if self.battle():
                    if not self._on_battle(f"clear_obstacle {direction}"):
                        return "battle"
                    self.emu.tick(2)
                    continue
                if self._script_or_text():
                    prompted = True
                    self.press("A:8 .:48")       # answer; >=40f gap
                    continue
                if not poked:
                    # facing-tile poke: the surf-mount ask ('The water
                    # is calm... SURF?') only appears on an explicit A
                    # while facing water -- bumping alone never asks.
                    self.press("A:8 .:40")
                    poked = True
                    continue
                self.press(".:10")
            for _ in range(8):                   # drain prompt chains
                if not self._script_or_text():
                    break
                prompted = True
                self.press("A:8 .:48")
            if self.pos() != before:             # the prompt itself moved
                return "moved"                   # us (surf mount)
            r = self.move_settled(direction, hold=40)
            if r == "battle":         # primitive surfaced it; one path
                if not self._on_battle(f"clear_obstacle {direction}"):
                    return "battle"
                continue
            if r in ("moved", "warp"):
                return "moved"
        return "cleared-not-moved" if prompted else "failed"

    def _pace_dirs(self, dirs, box):
        """The directions from the current cell that keep pace() inside
        `box` (x_lo, x_hi, y_lo, y_hi, inclusive). Already outside it (a
        warp dumped us elsewhere)? Only the moves that CLOSE the gap are
        offered, so the walk works its way back in instead of deadlocking."""
        if box is None:
            return list(dirs)
        x_lo, x_hi, y_lo, y_hi = box
        x, y = self.pos()[2:]

        def _miss(px, py):
            """Manhattan distance from (px, py) to the box; 0 = inside."""
            return (max(x_lo - px, 0, px - x_hi)
                    + max(y_lo - py, 0, py - y_hi))

        here = _miss(x, y)
        out = []
        for mv in dirs:
            dx, dy = STEP[mv]
            there = _miss(x + dx, y + dy)
            if there == 0 or there < here:
                out.append(mv)
        return out

    def pace(self, steps, dirs="UDLR", box=None, on_battle="return"):
        """Random-walk `steps` steps on the current map: the grinding /
        encounter-farming loop the driving model otherwise hand-rolls
        every session (and hand-rolled wrong -- see move_settled).

        `dirs`: directions to draw from ('LR' paces a corridor).
        `box`: (x_lo, x_hi, y_lo, y_hi) INCLUSIVE bounding box the walk
        may never leave -- an unclamped random walk drifted onto a
        staircase and stranded a live run three floors deep in Victory
        Road. Cells outside it are never stepped toward.
        `on_battle`: 'return' (default) STOPS the instant an encounter
        starts and leaves the battle up, so the model decides ko/catch/
        flee; 'fight' hands each one to the caller's policy (through
        fight(), so encounter_policy/default_policy apply) and keeps
        pacing.

        Returns {'steps': steps actually taken, 'battles': encounters
        seen, 'stopped': why it ended} where 'stopped' is 'steps'
        (budget spent), 'battle', 'boxed-in' (no legal direction),
        'blocked' (walls in every drawn direction), 'warp' (left the
        map), 'whiteout', or 'declined' (on_battle='fight' but
        auto_fight=manual -- the decider owns it)."""
        if on_battle not in ("return", "fight"):
            raise ValueError(f"pace: on_battle={on_battle!r} -- use "
                             f"'return' or 'fight'")
        budget = max(0, int(steps))
        picks = [c for c in str(dirs).upper() if c in STEP]
        if not picks:
            raise ValueError(f"pace: dirs={dirs!r} names no direction")
        if box is not None:
            box = tuple(int(v) for v in box)
            if len(box) != 4 or box[0] > box[1] or box[2] > box[3]:
                raise ValueError(
                    f"pace: box={box!r} must be (x_lo, x_hi, y_lo, y_hi) "
                    f"with lo <= hi")
        taken = battles = blocked = 0
        stopped = "steps"
        with self._money_watch(f"pace {budget} steps"):
            while taken < budget:
                legal = self._pace_dirs(picks, box)
                if not legal:
                    stopped = "boxed-in"
                    break
                r = self.move_settled(random.choice(legal), fight=False)
                if r == "battle":
                    battles += 1
                    if on_battle == "return":
                        stopped = "battle"
                        break
                    if not self._on_battle(f"pace step {taken + 1}"):
                        stopped = "declined"
                        break
                    if self._whiteout_stop("pace"):
                        stopped = "whiteout"
                        break
                    blocked = 0
                    continue
                if r == "warp":
                    taken += 1
                    stopped = "warp"   # off the map: the box means nothing
                    break
                if r == "moved":
                    taken += 1
                    blocked = 0
                    continue
                blocked += 1
                if blocked >= 8:
                    stopped = "blocked"
                    break
        log.info(f"  pace: {taken}/{budget} steps, {battles} battles, "
                 f"stopped={stopped}")
        return {"steps": taken, "battles": battles, "stopped": stopped}

    def keyboard_open(self):
        s = self.emu.screen_text()
        return any("DEL" in r for r in s) and any("END" in r for r in s)


    @staticmethod
    def _choice_labels(rows):
        """Options of an open choice box: cursor-row text plus neighbor
        rows inside the same frame ('│▶YES│'/'│ NO │' -> ['YES','NO']).
        Empty when no cursor glyph is on screen."""
        idx = next((i for i, r in enumerate(rows)
                    if any(c in r for c in CURSORS)), None)
        if idx is None:
            return []
        labels = []
        for j in range(max(0, idx - 3), min(len(rows), idx + 4)):
            t = rows[j]
            for c in CURSORS:
                t = t.replace(c, "")
            t = t.strip().strip("│┃").strip()
            if t and "─" not in t and "┌" not in t and "└" not in t:
                labels.append(t)
        return labels

    def resolve_choice(self, choice="YES"):
        """Deliberately answer an open choice box: verify `choice` is
        visible on screen, navigate the cursor onto it, confirm. The
        caller owns semantics; this executes precisely instead of
        blind-mashing -- the gotcha-13 counterpart deciders were
        missing (R29 tutorial, nurse prompts, mom's day-picker).
        Returns {'answered': bool, 'chose': str|None, 'options': [...]}."""
        # scenes open with STORY PAGES before the box (aide monologue):
        # page them out glyph-gated first, then classify what's left.
        fr = self.flush_dialog(max_frames=3000)
        if fr == "battle":
            return {"answered": False, "chose": None, "options": [],
                    "note": "battle started"}
        f0 = self.emu.frame
        while self.emu.frame - f0 < 90:
            rows = self.emu.screen_text()
            if any(c in r for r in rows for c in CURSORS):
                break
            self.emu.tick(6)
        rows = self.emu.screen_text()
        opts = self._choice_labels(rows)
        if choice not in opts or \
                not any(c in r for r in rows for c in CURSORS):
            return {"answered": False, "chose": None, "options": opts,
                    "note": "no choice cursor settled on screen"}
        # gotcha-2 variant: the box may still be settling when labels
        # first decode -- confirm-then-verify, one bounded retry
        for _attempt in range(2):
            self.press(".:12")
            ok = bool(self.menu.select_label(choice, max_presses=6))
            self.emu.tick(20)
            still = self._choice_labels(self.emu.screen_text())
            if ok and choice not in still:
                return {"answered": True, "chose": choice,
                        "options": opts}
            opts = still or opts
        return {"answered": False, "chose": None, "options": opts,
                "rows": [r.strip() for r in self.emu.screen_text()
                         if r.strip()][:8]}

    def who_fights(self):
        """Rank the party against the CURRENT battle's foe using the repo
        type chart (registry 'who_fights'; needs ui.battle). Switch
        decisions become evidence-based: best-move effectiveness per mon,
        healthiest and hardest-hitting first. Returns {'enemy': ...,
        'ranking': [...]} -- pair with fight(policy=('switch', slot))."""
        if not self.battle():
            raise ValueError("who_fights: needs an active battle "
                             "(ui.battle=False)")
        b = Battle(self.emu, self.names, self.bdata)
        enemy = b.enemy()
        tnames = {i: n for n, i in self.bdata.types.items()}
        etypes = enemy.get("types") or []
        move_id = {n: i for i, n in self.names.moves.items()}
        rows = []
        for i, m in enumerate(game_state(self.emu, self.names)["party"]):
            if m.get("egg"):
                continue
            best_eff, best_mv = 0.0, None
            for mv in m["moves"]:
                mid = move_id.get(mv["name"])
                if not mid:
                    continue
                mtype = self.bdata.moves[mid]["type"]
                eff = self.bdata.effectiveness(mtype, etypes)
                if eff > best_eff:
                    best_eff, best_mv = eff, mv["name"]
            rows.append({"slot": i, "mon": m.get("nickname") or m["name"],
                         "level": m["level"],
                         "hp": round(m["hp"] / max(m["max_hp"], 1), 2),
                         "best_move": best_mv, "eff": best_eff})
        rows.sort(key=lambda r: (-r["eff"], -r["level"]))
        return {"enemy": {"name": enemy["name"],
                          "types": [tnames.get(t, str(t)) for t in etypes],
                          "level": enemy["level"], "hp": enemy["hp"],
                          "max_hp": enemy["max_hp"]},
                "ranking": rows,
                "note": "send the top healthy ranked mon in via "
                        "fight(policy=('switch', slot))"}

    def gym_scout(self, map):
        """Read the repo's ground truth for a gym BEFORE entering:
        parse maps/<Map>.asm trainer references + data/trainers/parties.asm
        into [{trainer, group, mons: [{species, level, moves}]}] so roster
        evolution is planned, not discovered by wiping (repo-is-the-map).
        map: CONST ('VIOLET_GYM') or CamelCase."""
        const = self._resolve_map(map)
        path = paths.REPO_ROOT / "maps" / f"{const.title().replace('_', '')}.asm"
        if not path.exists():
            raise ValueError(f"gym_scout: no map source at {path}")
        text = path.read_text()
        wanted = []                       # (GROUP, TEMPLATE) pairs
        for m in re.finditer(r"loadtrainer\s+(\w+),\s*(\w+)", text):
            wanted.append((m.group(1), m.group(2)))
        for m in re.finditer(r"^\ttrainer\s+(\w+),\s*(\w+),", text, re.M):
            wanted.append((m.group(1), m.group(2)))
        if not wanted:
            raise ValueError(f"gym_scout: no trainers found in {const}")
        parties_path = paths.REPO_ROOT / "data/trainers/parties.asm"
        ptext = parties_path.read_text()
        out = []
        for group, template in wanted:
            camel = "".join(p.capitalize() for p in group.split("_"))
            gsec = re.search(
                rf"^({camel}Group:.*?)(?=^\w+Group:|\Z)",
                ptext, re.M | re.S)
            if not gsec:
                out.append({"trainer": template, "group": group,
                            "mons": [], "error": "group not in parties.asm"})
                continue
            base = re.sub(r"\d+$", "", template)

            def _norm(s):
                # 'AMYANDMAY1' vs parties 'AMY & MAY@': drop non-letters,
                # then the literal AND, from BOTH sides
                return re.sub(r"[^A-Z]", "", s.upper()).replace("AND", "")

            variants = {_norm(base), _norm(base.split("_")[-1])}

            def _is_template(line_name):
                cand = _norm(line_name)
                return any(cand == v for v in variants)

            tmatch = None
            for m in re.finditer(
                    r'db "([^"]+)@".*?\n((?:\s+db .*\n)+?)\s+db -1',
                    gsec.group(1)):
                if _is_template(m.group(1)):
                    tmatch = m
                    break
            if not tmatch:
                out.append({"trainer": base, "group": group,
                            "mons": [], "error": "template not found"})
                continue
            mons = []
            for line in tmatch.group(2).splitlines():
                fields = [f.strip() for f in line.strip().removeprefix("db").split(",")]
                if len(fields) < 2 or not fields[0].isdigit():
                    continue
                mon = {"level": int(fields[0]), "species": fields[1]}
                if "MOVES" in tmatch.group(0):
                    mon["moves"] = [f for f in fields[2:]
                                    if f and f != "NO_MOVE"]
                mons.append(mon)
            out.append({"trainer": base, "group": group, "mons": mons})
        return out

    def _naming_sig(self):
        """WRAM signature of naming-screen state; NamingScreen writes
        these BEFORE rendering (engine/menus/naming_screen.asm), so a
        delta beats every screen-text check on fade-in frames."""
        e = self.emu
        return (e.read_u8("wNamingScreenType"),
                e.read_u8("wNamingScreenDestinationPointer"))

    def _naming_screen_plausible(self):
        """True when the naming-screen WRAM union holds values a real
        NamingScreen call could have written. Those bytes ($c6d0-$c6d8)
        are UNIONED with other screen buffers, so a cutscene scribbling
        tilemap data through them moves _naming_sig() and used to be read
        as 'a keyboard opened' -- 30+ wasted B/START/A presses per scene,
        which is how a blind A press walked into the START menu and
        browsed the Pokedex mid-cutscene.

        wNamingScreenType is masked with NUM_NAMING_SCREEN_TYPES
        (constants/menu_constants.asm:129 -> 8 types) and the longest name
        the game ever asks for is 10 (a mon nickname), so anything outside
        those ranges is somebody else's data."""
        e = self.emu
        return (e.read_u8("wNamingScreenType") < 8
                and 1 <= e.read_u8("wNamingScreenMaxNameLength") <= 10
                and e.read_u8("wNamingScreenCurNameLength") <= 10)

    def _naming_opened(self, sig0):
        """Is a naming keyboard REALLY up? A WRAM signature delta alone is
        a guess (the bytes are unioned); a rendered DEL/END row is proof.
        On a delta we therefore wait briefly for the render and, if it
        never arrives, report False -- confirming a keyboard that is not
        there types START+A into the overworld, which opened the START
        menu and walked into the Pokedex twice this session. A real
        keyboard is patient: waiting costs nothing."""
        if self.keyboard_open():
            return True
        if self._naming_sig() == sig0 or not self._naming_screen_plausible():
            return False
        for _ in range(8):                       # ~80 frames of grace
            self.emu.tick(10)
            if self.keyboard_open():
                return True
        return False

    @staticmethod
    def _text_speed_byte(opts, mode):
        """wOptions low TEXT_DELAY_MASK bits select render delay
        (FAST=%001, MED=%011, SLOW=%101); upper option bits survive."""
        delays = {"FAST": 0b001, "MED": 0b011, "SLOW": 0b101}
        return (opts & ~0b111) | delays[mode]

    def set_text_speed(self, mode="FAST"):
        """Force fast text rendering: pages complete in fewer frames so
        drains stop paying the per-press tax (moss-run [W]: Elm speech
        cost 104 A presses on the default speed). Cheap + idempotent --
        safe to call on every drain entry; new-game resets re-apply."""
        try:
            self.emu.write("wOptions",
                           self._text_speed_byte(
                               self.emu.read_u8("wOptions"), mode))
            return True
        except Exception:
            return False

    def name_prompt(self, name):
        """Registry 'name_prompt': give a DELIBERATE name on whatever
        naming keyboard is currently open (hatch prompts, catch naming).
        The press() freeze blocks every other input source while this
        runs, so persona names land exactly once. Precondition: a naming
        screen must be up (keyboard_open)."""
        if not self.keyboard_open():
            raise ValueError(
                "name_prompt: no naming keyboard open -- poll "
                "keyboard_open() after hatches/catches first")
        self.dismiss_keyboard(name)

    def _take_pending_nickname(self):
        """Resolve the naming screen that just opened, consuming
        `_pending_nickname` if one is armed (gift mons: the starter,
        Togepi, Eevee, the Odd Egg's hatch). One-shot: the name never
        leaks into the next prompt."""
        name = self._pending_nickname
        if callable(name) or isinstance(name, dict):
            name = None       # species-keyed forms need a species; gifts
        self._pending_nickname = None
        self.dismiss_keyboard(name)
        return name

    def dismiss_keyboard(self, name=None):
        """Confirm a naming screen. With a name, actually type it; without,
        confirm with the minimal name (fast path). Runs with the naming
        freeze lifted -- this is the ONLY sanctioned typer."""
        was = getattr(self, "_naming_busy", False)
        self._naming_busy = True
        try:
            if name:
                log.info(f"  naming keyboard: typing {name!r}")
                for _ in range(12):   # B = backspace: clear stray chars
                    self.press("B:3 .:10")
                self.type_name(name)
                return
            log.info("  naming keyboard: confirming")
            for _ in range(12):       # clear strays so decline is clean
                self.press("B:3 .:10")
            self.press("START:4 .:20 A:4 .:30")          # END + confirm
            if self.keyboard_open():                  # empty refused:
                self.press("A:2 .:10 START:4 .:20 A:4 .:30")  # 1 letter
        finally:
            self._naming_busy = was

    def type_name(self, name, max_len=10):
        """Type `name` on the naming keyboard (uppercase only -- the game
        renders names in caps anyway). Runs with the freeze lifted."""
        was = getattr(self, "_naming_busy", False)
        self._naming_busy = True
        # The naming window SLIDES IN: the letter grid is on screen for
        # ~40 frames before DEL/END are drawn and the joypad loop reads
        # input. Typing into that animation silently drops every press --
        # a Cyndaquil got handed over as "CYNDAQUIL" that way. Wait for a
        # fully drawn keyboard first (gotcha 2, the naming-screen case).
        for _ in range(40):
            if self.keyboard_open():
                break
            self.emu.tick(10)
        else:
            log.warning("  type_name: keyboard never finished drawing "
                        "(no DEL/END row) -- typing anyway")
        global NAME_GRIDS
        if NAME_GRIDS is None:
            NAME_GRIDS = _parse_name_grid(paths.REPO_ROOT)
        grid = NAME_GRIDS["NameInputUpper"]

        def kb_cursor():
            p = self.emu.read("wNamingScreenCursorObjectPointer", 2)
            ptr = p[0] | (p[1] << 8)
            st = self.emu.read((1, ptr), 14)
            return st[12], st[13]

        def kb_step(btn, want):
            for _ in range(5):
                self.press(f"{btn}:8 .:16")
                if kb_cursor() == want:
                    return want
            return kb_cursor()

        def name_len():
            return self.emu.read_u8("wNamingScreenCurNameLength")

        chars = [c for c in name.upper()[:max_len] if c in grid]
        if not chars:
            chars = ["A"]
        log.info(f"  typing name {''.join(chars)!r}")
        self.press("START:6 .:20")               # snap to END zone (8,4)
        x, y = kb_step("U", (8, 3))              # control row moves by ZONE,
        for ch in chars:                         # so navigate on char rows
            tx, ty = grid[ch]
            for _ in range(12):                  # horizontal first
                if x == tx:
                    break
                x, y = kb_step("R" if tx > x else "L",
                               (x + (1 if tx > x else -1), y))
            for _ in range(6):                   # then vertical
                if y == ty:
                    break
                x, y = kb_step("D" if ty > y else "U",
                               (x, y + (1 if ty > y else -1)))
            before = name_len()
            for _ in range(3):                   # A adds the character
                self.press("A:8 .:16")
                if name_len() > before or name_len() >= max_len:
                    break
        self.press("START:6 .:20 A:10 .:40")     # snap to END, confirm
        self._naming_busy = was

    def _flush_dialog_hooks(self, max_frames, quiet_frames=40):
        """Event-driven advance: A only while the engine reports a page
        waiting for a button (PromptButton hook); stop the moment a menu
        or battle-end event fires -- zero blind presses."""
        f0, quiet = self.emu.frame, 0
        sig0 = self._naming_sig()
        self.set_text_speed()
        while self.emu.frame - f0 < max_frames:
            if self.battle():
                return "battle"
            if self._naming_opened(sig0):
                # A GIFT mon (starter, Togepi, Eevee...) also opens the
                # naming keyboard, and this path used to always confirm
                # empty -- the persona's PANIC came out of Elm's lab
                # called CYNDAQUIL twice. Honour _pending_nickname here
                # too, exactly like fight()/catch() do.
                self._take_pending_nickname()
                quiet = 0
                continue
            sig0 = self._naming_sig()   # re-baseline: no keyboard came up
            events = self.hooks.drain()
            kinds = [k for k, _ in events]
            if hookevents._STOP_EVENTS & set(kinds):
                self.press(".:12")
                return "menu"
            if "page_wait" in kinds and dialog_press_safe(
                    self.emu.screen_text()):
                self.press("A:2 .:8")
                quiet = 0
                continue
            self.press(".:8")
            quiet += 8
            if quiet >= quiet_frames:
                # A page_wait event is consumed on drain even when it
                # could not be acted on (stale/mid-transition), after
                # which the loop goes deaf while the box keeps waiting.
                # The visible textbox is the persistent signal: fall
                # back to glyph-gated paging instead of reporting done.
                if self.textbox():
                    if dialog_press_safe(self.emu.screen_text()):
                        self.press("A:2 .:8")
                        quiet = 0
                        continue
                    self.last_choice_options = \
                        self._choice_labels(self.emu.screen_text())
                    return "menu"   # cursor outside box: deliberate
                return "done"
        return "timeout"

    def flush_dialog(self, max_frames=6000, quiet_frames=40):
        """Press A while a textbox is up; return once it's been gone a
        bit. Handles a naming keyboard if one appears. With live hooks
        this is event-driven; otherwise the legacy cadence applies,
        gated by dialog_press_safe and a naming-screen WRAM delta so a
        fade-in keyboard never eats A presses as keystrokes."""
        f0, quiet = self.emu.frame, 0
        sig0 = self._naming_sig()
        self.set_text_speed()
        while self.emu.frame - f0 < max_frames:
            if self.battle():
                return "battle"
            if self.hooks is not None and \
                    self.hooks.has("page_wait"):
                return self._flush_dialog_hooks(max_frames, quiet_frames)
            rows = self.emu.screen_text()
            if self._naming_opened(sig0):
                self._take_pending_nickname()
                quiet = 0
            elif self._naming_sig() != sig0:
                sig0 = self._naming_sig()   # false alarm; re-baseline
            elif self.textbox() and dialog_press_safe(rows):
                self.press("A:2 .:8")
                quiet = 0
            elif self.textbox():
                # cursor glyph outside the box: a choice/menu opened --
                # report instead of blind-picking it (AGENTS.md gotcha 13)
                self.last_choice_options = self._choice_labels(rows)
                return "menu"
            else:
                self.press(".:8")
                quiet += 8
                if quiet >= quiet_frames:
                    return "done"
        return "timeout"

    def _drain_scene(self, max_pages=25, max_frames=6000):
        """Bounded auto-drain for a scripted scene blocking movement
        (Elm's phone call, the rival ambush, aide hand-offs): page
        through with A until the textbox is gone AND wScriptMode reads
        0, so the blocked step can be retried instead of replan-storming
        (the top friction of the claude-wren run). Movement phases
        between pages (applymovement) are waited out, never pressed
        into. Never mashes a choice menu -- but only an ACTUAL cursor
        glyph ($ec '▷' / $ed '▶') on screen is a menu (gotcha 13); a
        drawn-but-EMPTY textbox is a still-rendering page (leg-2: 8
        false 'blocked by choice menu' aborts on blank pre-battle
        trainer boxes), so wait briefly and page it. Returns
        'done' | 'battle' | 'menu' | 'timeout'."""
        self.set_text_speed()
        pages = 0
        f0 = self.emu.frame
        while pages < max_pages and self.emu.frame - f0 < max_frames:
            if self.battle():
                return "battle"
            if self.textbox():
                rows = self.emu.screen_text()
                if not dialog_press_safe(rows):
                    # dialog_press_safe fails on TWO very different
                    # screens: a real choice box (cursor glyph drawn)
                    # and a textbox whose text hasn't rendered yet --
                    # trainer boxes draw the frame a beat before the
                    # text. Only a cursor is a menu; a blank box just
                    # needs a short bounded wait, then A is safe.
                    for i in range(7):
                        if any(c in r for r in rows for c in CURSORS):
                            self.last_choice_options = \
                                self._choice_labels(rows)
                            return "menu"   # true choice: never blind-pick
                        if dialog_press_safe(rows) or i == 6:
                            break
                        self.press(".:10")  # let the page render
                        rows = self.emu.screen_text()
                self.press("A:2 .:8")
                pages += 1
                continue
            try:
                if self.emu.read_u8("wScriptMode"):
                    self.press(".:20")  # scene still running its script
                    continue
            except Exception:
                pass
            self.settle(max_frames=300)  # let a follow-on page land
            if self.battle():
                return "battle"
            if not self.textbox():
                return "done"
        return "timeout"

    def drain_scene(self, max_frames=6000):
        """Public scene-exit primitive (registry 'drain_scene'): page a
        scripted scene until interactive, then B once if a residual box
        ignores A -- some scene-enders are A-deaf (omp-fresh's Elm call
        needed one B after 40 A presses). Choice boxes still surface as
        'menu' (gotcha 13): answer them deliberately."""
        r = self._drain_scene(max_frames=max_frames)
        if r in ("done", "timeout") and \
                (self.textbox() or self.menu_open()):
            self.press("B:4 .:16")
            r = self._drain_scene(max_frames=min(max_frames, 2000))
        return r

    # -- battle decisions (wren pt6) ---------------------------------------
    # The harness used to decide battles by DEFAULT and say nothing about
    # it: a "pacing" loop reported fights=0 while ~20 battles fought
    # themselves (all the exp to the wrong mon, then a whiteout), ~78 of
    # ~80 wild encounters were auto-KO'd without the model being asked
    # 'KO / catch / flee', and a ping-pong switch policy fed Koga ~10 free
    # switch-in hits with no per-turn record to diagnose it from.
    #
    # last_battle: per-turn record of the LAST fight() -- a decide.TurnLog
    # when crystalagent.decide is importable, else a plain list of the same
    # row dicts. Written by EVERY fight(), so a battle that went wrong is
    # reviewable after the fact.
    last_battle = None
    # last_frame: the decision frame of the most recent turn (or encounter
    # consult). A policy that wants the frame without assembling it by hand
    # can read it; single-argument policies are handed it directly.
    last_frame = None
    # More than this many VOLUNTARY switch-ins in one battle gets a loud
    # end-of-battle line: each one is a free hit for the foe.
    FREE_HIT_LOUD = 2
    # Bare 'catch' disposition: cheapest ball in the pocket first, so a
    # plain 'catch' never burns an ULTRA BALL on a RATTATA.
    BALL_PREFERENCE = ("POKE BALL", "GREAT BALL", "ULTRA BALL")

    _tactics = None

    @property
    def tactics(self):
        """Type/damage analysis for this save (crystalagent.tactics).

        Badge-boosted attacking types are read live, because the boost is
        worth +1/8 damage and depends on which badges this file has
        (DoBadgeTypeBoosts, engine/battle/misc.asm:147). The ROM's heal
        table goes in too, so a mid-battle status cure names a real item
        at its real price instead of a guess."""
        if self._tactics is None:
            from crystalagent.tactics import Tactics, boosted_types
            self._tactics = Tactics(
                self.bdata, self.names, paths.REPO_ROOT,
                badge_types=boosted_types(self.emu, self.bdata,
                                          paths.REPO_ROOT),
                heal_table=self._heal_items())
        return self._tactics

    def outlook(self):
        """Real per-turn combat maths for the CURRENT battle, or None.

        Every one of my moves scored with the game's own damage formula
        against the enemy actually standing there -- type multiplier, the
        Gen-2 physical/special split (which is per TYPE), STAB, badge
        boost, the 85-100% spread, hits-to-KO -- plus the enemy's moves
        aimed back at me and who moves first. `d.tactics.explain(...)`
        renders it as one auditable line per move."""
        if not self.battle():
            return None
        return self.tactics.read(self.emu)

    def battle_frame(self):
        """The decision frame for the CURRENT battle -- the dict
        crystalagent.decide.battle_frame documents (me/enemy/party/bag/
        turn/wild/can_switch/moves) -- or None with no battle up (or no
        decide module). Exactly what encounter_policy and frame-shaped
        battle policies are handed, exposed so a model can read the same
        thing by hand instead of stitching game_state()/observe() together
        for every decision."""
        if not self.battle():
            return None
        return self._frame(Battle(self.emu, self.names, self.bdata))

    def _frame(self, b):
        """decide.battle_frame(b) for the live battle, or None. Never
        raises: the frame is an affordance, and losing it must not lose
        the battle -- policies fall back to (rows, me, enemy)."""
        if _decide_frame is None:
            return None
        try:
            frame = _decide_frame(b)
        except Exception as err:
            if not getattr(self, "_frame_warned", False):
                self._frame_warned = True
                log.warning(f"  [fight] battle_frame unavailable ({err}); "
                            f"policies get the legacy (rows, me, enemy)")
            return None
        self.last_frame = frame
        return frame

    @staticmethod
    def _ask(hook, frame, rows, me, enemy):
        """Call a decision hook in the shape it declares: pt6 hook(frame)
        or legacy hook(rows, me, enemy). A frame-shaped hook is handed
        None when crystalagent.decide is unavailable."""
        if _policy_style(hook) == "frame":
            return hook(frame)
        return hook(rows, me, enemy)

    def _consult_encounter(self, b, policy, must_decide):
        """Ask self.encounter_policy ONCE, the moment a WILD appears, what
        to do with it: 'ko' | 'catch' | 'flee' | ('ball', NAME). Returns
        (disposition, per-turn policy): 'catch' and 'flee' REPLACE the
        per-turn policy for this battle, 'ko' (and no answer) keep it.
        Trainer battles never come here -- there is nothing to decide.

        A hook that raises, or answers with something outside the
        vocabulary, logs ONE warning and KOs: a bad hook never wedges a
        battle. With decide_all/require_decision set, NO answer is an
        error (DecisionRequired) rather than a silent auto-KO."""
        hook = getattr(self, "encounter_policy", None)
        if hook is None and not must_decide:
            # nothing to ask and nothing to refuse: don't pay for a frame
            return None, policy
        frame = self._frame(b)
        try:
            rows = self.emu.screen_text()
        except Exception:
            rows = []
        try:
            me, enemy = b.me(), b.enemy()
        except Exception:
            me, enemy = {}, {}
        who = enemy.get("name") if isinstance(enemy, dict) else None

        def unanswered(why):
            if must_decide:
                raise DecisionRequired(
                    f"wild {who}: {why} -- answer 'ko' | 'catch' | 'flee' "
                    f"| ('ball', NAME)", frame=frame, kind="encounter",
                    options=("ko", "catch", "flee", "ball"))
            return None, policy

        if hook is None:
            return unanswered("no encounter_policy set")
        try:
            disp = self._ask(hook, frame, rows, me, enemy)
        except DecisionRequired:
            raise
        except Exception as err:
            log.warning(f"  [encounter] encounter_policy raised ({err}); "
                        f"KO'ing wild {who}")
            return "ko", policy
        if disp is None:
            return unanswered("encounter_policy returned None")
        kind = disp[0] if isinstance(disp, tuple) and disp else disp
        kind = kind.strip().lower() if isinstance(kind, str) else kind
        ball = disp[1] if isinstance(disp, tuple) and len(disp) > 1 else None
        if kind == "flee":
            log.info(f"  [encounter] wild {who}: flee")
            return "flee", lambda rows, me, enemy: "flee"
        if kind in ("catch", "ball"):
            ball = self._encounter_ball(ball)
            log.info(f"  [encounter] wild {who}: catch with {ball}")
            return f"catch:{ball}", self._ball_policy(ball)
        if kind == "ko":
            log.info(f"  [encounter] wild {who}: KO")
            return "ko", policy
        log.warning(f"  [encounter] encounter_policy answered {disp!r}, want "
                    f"'ko' | 'catch' | 'flee' | ('ball', NAME); KO'ing "
                    f"wild {who}")
        return "ko", policy

    def _new_turn_log(self):
        """A decide.TurnLog, or a plain list when the module is missing --
        a kernel reboot that drops the import must not drop the record."""
        if _TurnLog is not None:
            try:
                return _TurnLog()
            except Exception as err:
                log.warning(f"  [fight] decide.TurnLog unusable ({err}); "
                            f"recording turns in a plain list")
        return []

    def _action_label(self, act, me=None):
        """One readable phrase for a battle decision ('attack slot 0
        (SURF)'), so a log line names the move actually used."""
        kind = act[0] if isinstance(act, tuple) and act else act
        arg = act[1] if isinstance(act, tuple) and len(act) > 1 else None
        if kind == "attack":
            if not isinstance(arg, int):
                return "attack (best move)"
            name = "?"
            try:
                moves = (me or {}).get("moves") or []
                if arg < len(moves):
                    mid = moves[arg][0]
                    name = self.names.moves.get(mid, f"?id{mid}")
            except Exception:
                pass
            return f"attack slot {arg} ({name})"
        if kind == "switch":
            return f"switch to party slot {arg}"
        if kind in ("ball", "item"):
            return f"{kind} {arg}"
        return str(kind)

    def _auto_action(self, b, me, enemy, state, steered):
        """What the harness would have played SILENTLY, resolved here (not
        inside Battle.play's own fallback) so the log line can name the
        exact slot and move that gets used. Logged ONCE per battle:
        WARNING when nothing at all was steering, INFO when a policy
        merely declined this turn."""
        act = "attack"
        try:
            act = b._default_policy(me, enemy, 0.3)
        except Exception:
            pass
        kind = act[0] if isinstance(act, tuple) and act else act
        arg = act[1] if isinstance(act, tuple) and len(act) > 1 else None
        if kind == "attack":
            # ALWAYS re-resolve the slot through best_move(): Battle's own
            # heuristic sometimes hands back slot 0, and slot 0 is a status
            # move for most parties (TACKLE-over-EMBER cost a Scyther
            # fight; GROWL/LEER cost two whiteouts -- #21/#24).
            try:
                slot = b.best_move()
            except Exception:
                slot = None
            if slot is not None:
                act = ("attack", slot)
            elif not isinstance(arg, int):
                act = ("attack", 0)
        if state["autos"] == 0:
            label = self._action_label(act, me)
            if steered:
                log.info(f"  [fight] auto: {label} (policy declined this "
                         f"turn)")
            else:
                log.warning(f"  [fight] auto: {label} -- no policy, no "
                            f"default_policy, decide_all off: the HARNESS "
                            f"is choosing this battle")
        state["autos"] += 1
        return act, "auto"

    @staticmethod
    def _close_turn(state, me, enemy):
        """Fill the PREVIOUS turn's after-HP from the vitals read at the
        start of this one: that difference is what a free hit costs."""
        row = state.get("last_row")
        if not isinstance(row, dict):
            return
        if isinstance(me, dict) and row.get("my_hp_after") is None:
            row["my_hp_after"] = me.get("hp")
        if isinstance(enemy, dict) and row.get("enemy_hp_after") is None:
            row["enemy_hp_after"] = enemy.get("hp")

    def _turn_policy(self, b, policy, must_decide, disposition=None):
        """Wrap the per-turn policy so EVERY turn lands on self.last_battle
        and the harness never picks invisibly. Returns (state, wrapped).

        The wrapped policy ALWAYS returns a concrete action, so Battle.play
        can no longer fall back to its best-damage picker behind our back:
        with must_decide the missing decision raises DecisionRequired
        (carrying the frame), otherwise the harness's own pick is resolved
        here and logged."""
        style = _policy_style(policy)
        rec = self._new_turn_log()
        self.last_battle = rec
        state = {"turns": 0, "free_hits": 0, "autos": 0, "last_row": None,
                 "disposition": disposition, "log": rec}

        def wrapped(rows, me, enemy):
            state["turns"] += 1
            self._close_turn(state, me, enemy)
            # a legacy policy that cannot be refused never looks at the
            # frame: don't re-read the party and bag every turn for it
            frame = (self._frame(b) if style == "frame" or must_decide
                     else None)
            act = None
            if policy is not None:
                try:
                    act = (policy(frame) if style == "frame"
                           else policy(rows, me, enemy))
                except Exception as err:
                    # A raising policy used to be indistinguishable from a
                    # policy that declined, and the fallback then played
                    # slot 0 -- silent status-move spam for whole battles
                    # (FUCK_I_MESSED_UP.md #21). Say it out loud.
                    log.error(f"  [fight] policy RAISED "
                              f"{type(err).__name__}: {err} -- falling back "
                              f"to the harness pick for this turn")
                    act = None
            source = "policy"
            if act is None:
                if must_decide:
                    why = ("policy returned None" if policy is not None
                           else "no policy set")
                    raise DecisionRequired(
                        f"turn {state['turns']}: {why} and this fight "
                        f"requires a decision -- answer ('attack', slot) | "
                        f"('switch', party_index) | ('item', NAME) | "
                        f"('ball', NAME) | 'flee'",
                        frame=frame, kind="turn",
                        options=("attack", "switch", "item", "ball", "flee"))
                act, source = self._auto_action(
                    b, me, enemy, state, steered=policy is not None)
            kind = act[0] if isinstance(act, tuple) and act else act
            if kind == "switch":
                # the switch-in itself eats a hit; Koga got ~10 of them
                state["free_hits"] += 1
            note = source if not disposition else f"{source}/{disposition}"
            state["last_row"] = _turn_row(
                rec, actor="me", action=act, turn=state["turns"],
                enemy_species=(enemy.get("name")
                               if isinstance(enemy, dict) else None),
                enemy_hp_before=(enemy.get("hp")
                                 if isinstance(enemy, dict) else None),
                my_hp_before=me.get("hp") if isinstance(me, dict) else None,
                note=note)
            return act

        # who is actually steering, for anything inspecting the policy
        # Battle.play received (logs, tests, a decider asking "who chose
        # that?"): the wrapper is not the decision-maker, this is.
        wrapped.policy = policy
        wrapped.disposition = disposition
        return state, wrapped

    def _log_turns(self, b, state, outcome):
        """Close the last turn's record and say ONE loud line when the
        battle handed the foe repeated free hits -- the Koga wipe (10 free
        switch-in hits, 5 of 6 mons lost) must be visible at a glance.

        The LOUD number is switch-ins: decide.TurnLog also counts item uses
        and ball throws as ceded turns (they are), but a 4-ball catch is not
        an anomaly and must not cry wolf. Returns
        (switch_ins, ceded_turns)."""
        try:
            self._close_turn(state, b.me(), b.enemy())
        except Exception:
            pass
        free = state["free_hits"]
        ceded = free
        counter = getattr(state.get("log"), "free_hits", None)
        if callable(counter):
            try:
                ceded = counter()
            except Exception:
                ceded = free
        if free > self.FREE_HIT_LOUD:
            log.warning(f"  [fight] free_hits={free} in {state['turns']} "
                        f"turns ({outcome}): every switch-in handed the foe "
                        f"a free hit -- turn record on d.last_battle")
        return free, ceded

    FIGHT_DIAG_CAP = 3   # unresolved-battle dumps per battle (live: 20+)

    def _fight_diag(self, b, outcome):
        """Dump the unresolved battle's screen and both mons' vitals.

        Capped at FIGHT_DIAG_CAP dumps for as long as the SAME battle
        keeps coming back: a caller that retries fight() on a wedged
        battle re-entered this path every time and printed 20+ identical
        dumps per battle in the Victory Road grind. fight() clears the
        counter the moment wBattleMode goes quiet, so the next battle
        starts with a full budget."""
        printed = getattr(self, "_fight_diag_prints", 0)
        if printed >= self.FIGHT_DIAG_CAP:
            return
        self._fight_diag_prints = printed + 1
        try:
            me, enemy = b.me(), b.enemy()
            log.warning(f"  [fight diagnostic] frozen screen ({outcome}):")
            for r in self.emu.screen_text():
                if r.strip():
                    log.info(f"    | {r}")
            mv = [(self.names.moves.get(m, f"?id{m}"), p)
                  for m, p in me["moves"]]
            log.warning(f"  [fight diagnostic] me={me['name']} L{me['level']} "
                  f"{me['hp']}/{me['max_hp']} moves={mv}")
            log.warning(f"  [fight diagnostic] enemy={enemy['name']} "
                  f"L{enemy['level']} {enemy['hp']}/{enemy['max_hp']}",
                  )
        except Exception as diag_err:
            log.warning(f"  [fight diagnostic] unavailable: {diag_err}",
                  )
        if self._fight_diag_prints >= self.FIGHT_DIAG_CAP:
            log.warning(f"  [fight diagnostic] cap reached "
                        f"({self.FIGHT_DIAG_CAP} dumps): suppressing "
                        f"further dumps for this battle")

    def fight(self, max_frames=90000, policy=None, require_decision=False,
              consult_encounter=True, resume=4):
        """Play a battle out with real move selection (best expected
        damage, auto-POTION at low HP, flee hopeless wilds). Pauses at a
        naming keyboard (post-catch nickname prompt) to type
        self._pending_nickname if one is set. `policy=None` falls back
        to self.default_policy (still None by default): scripted battles
        the driver intercepts on its own (talk_to, goto, travel) obey a
        pre-armed policy instead of silently fighting with the default.

        wren pt6 -- the MODEL decides, the harness only reports:
        * a WILD battle asks self.encounter_policy ONCE, before the first
          turn, for a disposition ('ko' | 'catch' | 'flee' |
          ('ball', NAME)): 'catch' throws balls (catch()'s own logic),
          'flee' runs, 'ko' plays the battle out with `policy`. TRAINER
          battles never ask. consult_encounter=False suppresses the
          question for callers that ARE the disposition (catch()).
        * require_decision=True (or self.decide_all) refuses to pick: a
          turn whose policy returns None raises DecisionRequired carrying
          the frame, instead of quietly playing the best-damage move.
        * every turn is recorded on self.last_battle, and a battle with
          more than FREE_HIT_LOUD switch-ins says so in one line.
        * with nothing steering, the harness's pick is logged ('auto:
          attack slot 0 (SURF)') -- a pacing loop once reported fights=0
          while ~20 battles fought themselves.
        * a spent FRAME BUDGET is not a result: `resume` (default 4) more
          budgets are played out before anything is reported unresolved,
          because a long trainer battle just needs more frames (live:
          Lance, five of six down, "UNRESOLVED (timeout)" -- re-calling
          fight() finished it, FUCK_I_MESSED_UP.md #82).
        Policy shapes: policy(rows, me, enemy) (legacy, still supported)
        or policy(frame) -- a single-argument policy is handed the decide
        frame instead. Returns the lead mon, as before."""
        if policy is None:
            policy = self.default_policy
        mode = self.battle()
        if not mode:
            return self.lead()
        must_decide = bool(require_decision) or bool(
            getattr(self, "decide_all", False))
        self._resolve_learn_flow()   # repair a wedged mid-learn state
        moves0 = self._party_moves()   # learn-transparency baseline
        f0 = self.emu.frame
        money0 = game_state(self.emu, self.names)["player"]["money"]
        b = Battle(self.emu, self.names, self.bdata)
        try:
            enemy0 = b.enemy()
        except Exception:
            enemy0 = {}
        disposition = None
        if mode == 1 and consult_encounter:
            # ONE question per wild encounter, asked before any turn
            disposition, policy = self._consult_encounter(b, policy,
                                                          must_decide)
        state, turn_policy = self._turn_policy(b, policy, must_decide,
                                               disposition)
        name = self._resolve_nickname(self._pending_nickname,
                                      b.enemy()["name"])
        outcome = b.play(policy=turn_policy, max_frames=max_frames,
                         want_nickname=bool(name),
                         text_handler=self._battle_text_handler)
        for _ in range(3):                       # naming handoff loop
            if outcome != "naming" or not self.keyboard_open():
                break
            self._pending_nickname = None
            self.dismiss_keyboard(name)
            outcome = b.play(policy=turn_policy, max_frames=max_frames,
                             text_handler=self._battle_text_handler)
        # A spent frame budget is a CLOCK, not an outcome: play() stops
        # after max_frames and re-entering it picks the battle up exactly
        # where it left off. Doing that here is the difference between
        # "Lance took a while" and handing the caller a live battle
        # labelled UNRESOLVED (#82). 'stuck'/'stalled'/'wedged' are NOT
        # resumed -- those mean the battle stopped changing, and more
        # frames buy nothing.
        budgets = 0
        while outcome == "timeout" and self.battle() and budgets < resume:
            budgets += 1
            log.info(f"  [fight] frame budget ({max_frames}) spent with the "
                     f"battle still live -- resuming "
                     f"({budgets}/{resume})")
            outcome = b.play(policy=turn_policy, max_frames=max_frames,
                             text_handler=self._battle_text_handler)
        self._pending_nickname = None
        free_hits, ceded = self._log_turns(b, state, outcome)
        # surface mid-battle level-up swaps (b.play resolved them through
        # _battle_text_handler); the sweep below diffs its own window
        self._diff_learned_moves(moves0)
        self._resolve_learn_flow(4000)   # sweep post-battle leftovers
        self.flush_dialog(3000)
        # Wipe signature: play() reports 'wipe' when the party is down at
        # battle end -- authoritative by itself. The money heuristic below
        # covers wipes whose cutscene resolves during flush_dialog (and
        # the broke-trainer edge where the loss drops Y0), because full HP
        # after the fact proves nothing on its own.
        wiped = outcome == "wipe"
        if not wiped and money0 is not None and not self.battle():
            s = game_state(self.emu, self.names)
            wiped = s["player"].get("money", money0) < money0 and \
                all(m["hp"] == m["max_hp"]
                    for m in s["party"] if not m["egg"])
        if wiped and not self.battle():
            self.whiteouts += 1
            self._whiteout_pending = True
            log.warning(f"  [WHITEOUT] wiped -> {self.map_name()} "
                  f"{self.pos()[2:]}; auto-healed at last Pokécenter",
                  )
        elif outcome == "wedged":
            # battle.py already printed its own capped wedge diagnostic
            # (frozen screen + vitals fingerprint); don't re-dump the
            # screen here -- the duplicate dump is exactly the hundreds-
            # of-identical-lines spam from wren pt3.
            log.warning(f"  [fight] battle wedged (see battle.py "
                        f"diagnostic above)")
        elif outcome in ("timeout", "stuck", "stalled"):
            # Burn ZERO blind retries: dump the frozen battle so the wedge
            # is diagnosable (the historic Bridget/Jigglypuff freeze cost
            # ~10 retries before anyone looked at the screen).
            self._fight_diag(b, outcome)
        still_live = bool(self.battle())
        if still_live and outcome in ("timeout", "stuck", "stalled",
                                      "wedged"):
            # NEVER report an unresolved fight as if it were over: the
            # caller's next pace()/goto walks straight back into the same
            # live battle (60 'fights', 535s, zero exp on Victory Road).
            log.warning(
                f"  [fight] UNRESOLVED ({outcome}) after {budgets + 1} "
                f"budget(s) of {max_frames}f and the battle is STILL LIVE "
                f"-- calling fight() again RESUMES it from here (that is "
                f"what finished Lance); the next step would re-enter it "
                f"blind instead. Raise max_frames/resume, drive it "
                f"manually, or change the policy")
        if not still_live:
            # battle over: the next one gets a fresh diagnostic budget
            self._fight_diag_prints = 0
        # Scratch sidecar, NOT the working state: a snapshot taken during
        # battle resolution must never become a resumable fork if the leg
        # crashes before the next real save. watch.py can still open
        # <name>.watch.state from its checkpoint browser.
        if self.state_path:
            self.emu.save(Path(self.state_path).with_suffix(".watch.state"))
        lead = self.lead()
        # decision-transparency journal: scripted/auto battles must leave
        # a reviewable trace, or the decider stops making decisions
        # (DESIGN.md rule 1) and persona expression dies in automation
        events = getattr(self, "encounter_events", None)
        if events is not None:   # duck-typed test doubles may omit it
            events.append({
                "frame": f0, "map": self.map_name(),
                "enemy": enemy0.get("name"),
                "enemy_level": enemy0.get("level"),
                "outcome": outcome, "frames": self.emu.frame - f0,
                "moves0": sorted(moves0), "moves1": sorted(
                    self._party_moves()),
                "policy": "custom" if policy is not None else "default",
                "wild": mode == 1, "disposition": disposition,
                "turns": state["turns"], "free_hits": free_hits,
                "ceded_turns": ceded,
                "decided": state["turns"] - state["autos"],
                "battle_live": still_live,
            })
        return lead

    def _whiteout_stop(self, where):
        """Consume a pending wipe flag (set by fight()). Under the default
        'abort' policy, report and tell the caller to stop: continuing the
        plan that just wiped us is how gym legs turned into re-entry
        loops. d.whiteout_policy = 'continue' restores blind resuming."""
        if not self._whiteout_pending:
            return False
        self._whiteout_pending = False
        if self.whiteout_policy == "abort":
            log.warning(f"  [whiteout] aborting {where} -- party healed at "
                  f"{self.map_name()}; relaunch deliberately")
            return True
        return False


    _LEARN_MARKERS = ("TRYING TO LEARN", "WANTS TO LEARN",
                      "DELETE A MOVE", "FORGET A MOVE", "MAKE ROOM",
                      "STOP LEARNING", "FORGOTTEN",
                      # mid-battle _AskForgetMoveText scrolls through a
                      # 2-line box; these cover its middle pages ("But
                      # <MON> can't learn more than four moves." /
                      # "Delete an older move to make room for <MOVE>?")
                      # which used to trip NO marker and dropped the
                      # flow state mid-flow (the GATOR/SCREECH wedge).
                      # Apostrophe-free on purpose (charmap ligatures).
                      "LEARN MORE", "THAN FOUR MOVES", "DELETE AN OLDER")

    # moves we're happy to sacrifice when learning something new, most
    # expendable first: pure-status filler, then weak/situational attacks.
    # Declining a level-up move is PERMANENT in GSC (no relearner) -- the
    # old decline-everything default silently cost Quilava FLAME WHEEL.
    # NB: never list an HM move here -- the game refuses ("HM moves can't be
    # forgotten"), the menu reopens, and the flow loops forever (wedged a
    # whole Will battle at 0-HP Xatu for 200k frames).
    HM_MOVES = frozenset(["CUT", "FLY", "SURF", "STRENGTH", "FLASH",
                          "WHIRLPOOL", "WATERFALL"])
    FORGET_PRIORITY = ["SMOKESCREEN", "LEER", "GROWL", "CHARM", "TAIL WHIP",
                       "DEFENSE CURL", "SAND-ATTACK", "TACKLE", "MUD-SLAP",
                       "QUICK ATTACK", "BUBBLE", "EMBER", "SWIFT"]
    learn_moves = True   # accept level-up moves by default

    # learn_policy: optional per-driver hook that lets the driving model
    # decide level-up learns. None means default_learn_policy decides --
    # power-aware, off the ROM's Moves table, and it never trades a
    # damaging move for a status move (the old FORGET_PRIORITY default
    # silently traded GATOR's BITE for SCARY FACE -- three whiteouts --
    # and a Gyarados' HYDRO PUMP for RAIN DANCE). AUTO/FORGET_PRIORITY
    # still runs when the default returns None or falls back.
    # Contract: callable(mon_name: str, new_move: str,
    #                    current_moves: list[str]) -> decision, where the
    # decision is one of
    #   * a move name from current_moves -- forget THAT move,
    #   * 'DECLINE'                      -- do not learn new_move,
    #   * None                           -- fall back to the auto behavior.
    # Called ONCE per learn flow BEFORE any YES/NO is answered, so
    # 'DECLINE' answers NO cleanly. Mon and move are parsed off the
    # '<MON> is trying to/wants to learn <MOVE>' text and ACCUMULATED
    # across frames: the mid-battle variant scrolls that sentence through
    # a 2-line box, so mon and move are never on screen together (pt5c:
    # this silently skipped the policy and auto-accepted). A policy that raises, or
    # that names a move not on the forget menu, or that names an HM move
    # (the game refuses those), logs ONE warning and falls back to auto --
    # a bad policy never wedges a battle. Policy-driven replacements land
    # in the SAME LEARN log line / move_changes entry as auto ones.
    learn_policy = None
    _learn_flow = None   # per-flow policy state; live only while a flow is
    # who decided the LAST resolved learn: 'policy' (learn_policy's word
    # was followed), 'auto-fallback' (policy raised / stale / HM: auto
    # took over after a warning), 'auto' (no policy engaged). Stamped
    # onto move_changes entries by _diff_learned_moves, then reset.
    # NB: several flows diffed in ONE batch (fight()'s end-of-battle
    # diff) all get the last flow's source -- rare, documented caveat.
    _learn_source = "auto"

    def _learn_prompt_up(self, rows):
        joined = "".join(rows).upper()
        return any(m in joined for m in self._LEARN_MARKERS)

    def _battle_text_handler(self, rows):
        """Modal-text hook for Battle.play: drive the level-up move-learning
        flow. Returns True when this frame's input was consumed.

        When self.learn_policy is set it is consulted once per flow (see
        the learn_policy attribute for the full contract) at the first
        '<MON> is trying to/wants to learn <MOVE>' page, BEFORE any YES/NO
        is answered: a returned move name answers YES and walks the forget
        menu to THAT move (the cursor row is verified against the request
        with _item_row_matches tolerance before confirming); 'DECLINE'
        answers NO and confirms 'Stop learning'; None, an exception, a
        request not on the menu, or an HM request (game refusal detected)
        all fall back -- with one warning where applicable -- to the AUTO
        policy below.

        AUTO ACCEPT/REPLACE policy (wren pt4, documented from the code --
        this is what actually gets sacrificed):
        * learn_moves=True (default): answer YES to "make room?". On the
          "Which move should be forgotten?" menu, walk the cursor DOWN
          (wrapping) to the FIRST FORGET_PRIORITY move on the list and
          confirm it. When NONE of the mon's moves are in FORGET_PRIORITY,
          the move already under the cursor is confirmed -- the menu opens
          on SLOT 1, so the mon's OLDEST move is what silently disappears
          (how GATOR's BITE became SCARY FACE while a 'press slot 1'
          policy whiffed three Morty fights). HM moves are never
          confirmed: the game refuses, and the cursor is moved off them.
        * learn_moves=False: decline deterministically ("Stop learning
          <MOVE>?" -> YES; B there means "don't stop" and loops).
        Completed swaps (policy- or auto-driven alike) are surfaced by
        _diff_learned_moves (LEARN log line + d.move_changes entry) from
        _resolve_learn_flow / fight().
        Blind A-mashing derails into party menus and wedges the battle."""
        if not self._learn_prompt_up(rows):
            # Transient scroll frames of the mid-battle 2-line box (e.g.
            # "SCREECH." / "But GATOR" while page 2 scrolls in) carry no
            # marker; dropping the flow state there loses a policy
            # DECLINE and the make-room YES/NO then falls through to
            # learn_moves=True (the GATOR/SCREECH forget-menu wedge).
            # Tolerate a few marker-less frames before declaring the
            # flow over.
            st = self._learn_flow
            if st is not None and st.get("misses", 0) < 3:
                st["misses"] = st.get("misses", 0) + 1
            else:
                self._learn_flow = None    # flow over: drop per-flow state
            return False
        joined = "".join(rows).upper()
        st = self._learn_flow
        if st is None:                 # first frame of a fresh flow
            st = self._learn_flow = {"decision": None, "consulted": False,
                                     "answered": False, "mon": None,
                                     "move": None, "misses": 0}
        st["misses"] = 0
        if not st["consulted"] and not st["answered"]:
            self._consult_learn_policy(rows, st)
        decision = st["decision"]
        forget = decision if decision not in (None, "DECLINE") else None
        if "CAN" in joined and "BE FORGOTTEN" in joined:
            # "HM moves can't be forgotten": the refusal text. Acknowledge
            # it; the move menu reopens and the cursor must MOVE off the HM.
            if forget is not None:
                log.warning(f"learn_policy: game refused to forget "
                            f"{forget} (HM) -- falling back to auto")
                st["decision"] = None
                self._learn_source = "auto-fallback"
            self.press("A:4 .:16 D:4 .:16")
            return True
        if "FORGOTTEN" in joined:
            # "Which move should be forgotten?" move menu is up
            if decision == "DECLINE":
                # safety net: a DECLINE flow must never walk this menu
                # (live pt5c wedge: GATOR/SCREECH mid-battle, cursor
                # parked on an HM). B backs out to "Stop learning
                # <MOVE>?", which the YES/NO branch below confirms.
                self.press("B:6 .:20")
                return True
            cur = [r.strip().upper() for r in rows if "▶" in r or "▷" in r]
            on_hm = any(hm in r for r in cur for hm in self.HM_MOVES)
            if forget is not None:
                want = _norm_item(forget)
                if forget in self.HM_MOVES:
                    # don't even try: confirming loops through the refusal
                    log.warning(f"learn_policy chose HM move {forget}: the "
                                "game refuses those -- falling back to auto")
                    st["decision"] = forget = None
                    self._learn_source = "auto-fallback"
                elif not any(_item_row_matches(r.lstrip("▶▷ "), want)
                             for r in (x.strip().upper() for x in rows) if r):
                    log.warning(f"learn_policy chose {forget} but it is not "
                                "on the forget menu (stale moveset?) -- "
                                "falling back to auto")
                    st["decision"] = forget = None
                    self._learn_source = "auto-fallback"
            if forget is not None:
                # confirm ONLY once the cursor row itself names the
                # requested move (row-match tolerance); otherwise walk.
                want = _norm_item(forget)
                under = any(
                    x >= 0 and _item_row_matches(r[x + 1:], want)
                    for r, x in ((r, max(r.find("▶"), r.find("▷")))
                                 for r in rows))
                self.press("A:6 .:25" if under else "D:4 .:16")
                return True
            target = next((m for m in self.FORGET_PRIORITY if m in joined),
                          None)
            if on_hm:
                self.press("D:4 .:16")     # never confirm an HM move
            elif target is None or any(target in r for r in cur):
                self.press("A:6 .:25")     # forget the move under the cursor
            else:
                self.press("D:4 .:16")     # cursor toward the target (wraps)
            return True
        if "YES" in joined and "NO" in joined:
            st["answered"] = True          # policy window is closed now
            learn = (self.learn_moves if decision is None
                     else decision != "DECLINE")
            if "STOP LEARNING" in joined:
                # decline path confirm; in learn mode B loops back so the
                # make-room prompt can be answered YES this time
                self.press("B:6 .:20" if learn else "A:6 .:20")
            elif learn:
                self.press("A:6 .:25")     # YES: make room for the new move
            else:
                self.press("B:6 .:20")     # NO: keep the current moveset
        else:
            self.press("A:4 .:16")         # advance the flow's text pages
        return True

    def _consult_learn_policy(self, rows, st):
        """Ask self.learn_policy about the learn flow on screen (once per
        flow, before any YES/NO is answered; contract on the attribute).
        Mon and move are parsed off the '<MON> is trying to/wants to learn
        <MOVE>' text and ACCUMULATED on st across frames: the mid-battle
        variant scrolls that sentence through a 2-line box, so mon and
        move are NEVER on screen together there (pt5c: the old
        single-shot regex silently skipped the policy and auto-accepted
        SCREECH). A flow entered MID-WAY (the wedge-repair path) never
        shows either fragment, so the policy is skipped and auto applies.
        A policy that raises is logged once -- exception text plus the
        args it was called with -- and treated as None (auto): a bad
        policy must never wedge a battle."""
        policy = getattr(self, "learn_policy", None) \
            or self.default_learn_policy
        text = re.sub(r"\s+", " ", " ".join(rows)).upper()
        m = re.search(r"(\S+) (?:IS TRYING|WANTS) TO LEARN", text)
        if m:
            st["mon"] = m.group(1)
        m = re.search(r"(?:TRYING|WANTS) TO LEARN "
                      r"([A-Z0-9♂♀'.\- ]+?)[!?.]", text)
        if m:
            st["move"] = m.group(1).strip()
        mon, new_move = st.get("mon"), st.get("move")
        if mon is None or new_move is None:
            return              # sentence still scrolling: retry next frame
        st["consulted"] = True
        moves = next((list(mv) for label, mv in self._party_moves()
                      if label.upper() == mon), [])
        try:
            decision = policy(mon, new_move, moves)
        except Exception as e:
            self._learn_source = "auto-fallback"
            log.warning(f"learn_policy({mon!r}, {new_move!r}, {moves!r}) "
                        f"raised {e!r} -- falling back to auto")
            return
        if decision is not None:
            st["decision"] = str(decision).strip().upper()
            self._learn_source = ("policy" if getattr(self, "learn_policy",
                                                      None) else "default")

    _move_ids = None      # {'IRON TAIL': 231, ...}, lazily inverted

    def move_id(self, name):
        """Move id for a display name, or None. Inverted once from the
        ROM's own MoveNames table."""
        if self._move_ids is None:
            self._move_ids = {_norm_item(n): i
                              for i, n in self.names.moves.items()}
        return self._move_ids.get(_norm_item(name))

    def move_power(self, name):
        """Base power of a move by display name (0 for status moves and
        for anything the ROM table does not know)."""
        mid = self.move_id(name)
        rec = self.bdata.moves.get(mid) if mid else None
        return (rec or {}).get("power", 0)

    def default_learn_policy(self, mon, new_move, current):
        """The learn decision made when no learn_policy is set: never
        trade damage away for a status move.

        Same contract as learn_policy (a move name to forget, 'DECLINE',
        or None to fall through to AUTO). The old default was
        FORGET_PRIORITY, a hand-ranked NAME list that contains damaging
        moves and, on no match, confirmed slot 1 -- which is how a
        Gyarados traded HYDRO PUMP for RAIN DANCE and GATOR's BITE became
        SCARY FACE. Power comes from the ROM's Moves table, so nothing
        here is a guess about the move list.

        Rules, deterministic on (power, name) so the same flow always
        decides the same way:

        * a status move (power 0) being offered: with two or fewer
          damaging moves left, sacrifice a status move if there is one and
          otherwise DECLINE -- a moveset needs its damage; with three or
          more, still prefer a status move, else the weakest attack.
        * a damaging move being offered: prefer a status move, else the
          weakest attack when it is strictly weaker than the new move,
          else DECLINE (learning something worse is not an upgrade).

        HM moves are never named: the game refuses to delete them and the
        forget menu loops on the refusal.
        """
        try:
            power = {m: self.move_power(m) for m in current}
            new_power = self.move_power(new_move)
        except Exception:
            return None                       # no ROM data: let AUTO run
        forgettable = [m for m in current
                       if m.strip().upper() not in self.HM_MOVES]
        status = sorted((m for m in forgettable if power.get(m, 0) == 0),
                        key=lambda m: (power.get(m, 0), m))
        attacks = sorted((m for m in forgettable if power.get(m, 0) > 0),
                         key=lambda m: (power.get(m, 0), m))
        damaging = [m for m in current if power.get(m, 0) > 0]
        if new_power == 0:
            if status:
                return status[0]
            if len(damaging) <= 2:
                return "DECLINE"
            return attacks[0] if attacks else "DECLINE"
        if status:
            return status[0]
        if attacks and power[attacks[0]] < new_power:
            return attacks[0]
        return "DECLINE"

    def _resolve_learn_flow(self, max_frames=8000):
        """Drive any on-screen move-learning flow to completion. Used to
        repair wedged states and sweep post-battle leftovers; safe to call
        when no flow is present. WHICH move gets sacrificed is decided by
        _battle_text_handler (see its docstring); any completed swap is
        logged and recorded on d.move_changes via _diff_learned_moves."""
        f0 = self.emu.frame
        before = None
        done = True
        while self.emu.frame - f0 < max_frames:
            rows = self.emu.screen_text()
            if not self._learn_prompt_up(rows):
                if before is None:
                    break         # no flow on screen at all
                # mid-scroll transient of the 2-line mid-battle box (no
                # marker while "But <MON>" scrolls in): let the screen
                # settle before declaring the flow over.
                for _ in range(2):
                    self.emu.tick(24)
                    rows = self.emu.screen_text()
                    if self._learn_prompt_up(rows):
                        break
                else:
                    break
            if before is None:       # snapshot only once a flow is real
                before = self._party_moves()
            self._battle_text_handler(rows)
        else:
            done = False
        self._learn_flow = None    # never leak a decision into the next flow
        if before is not None:
            self._diff_learned_moves(before)
        return done

    def _party_moves(self):
        """[(mon label, [move names])] snapshot for learn-flow diffing.
        The label prefers the nickname so LEARN lines match how the party
        is addressed in play (GATOR, REED, ...)."""
        try:
            return [((m.get("nickname") or "").strip() or m.get("name", "?"),
                     [mv["name"] for mv in m.get("moves", [])])
                    for m in game_state(self.emu, self.names)["party"]]
        except Exception:
            return []                 # mid-transition WRAM: skip the diff

    def _diff_learned_moves(self, before):
        """Diff a _party_moves() snapshot against the party NOW: one clear
        LEARN log line per replaced move slot plus an entry on
        d.move_changes ({'mon','forgot','learned','slot','source'},
        slot 1-based; 'source' is 'policy' | 'auto' | 'auto-fallback' --
        who decided the sacrifice, so audits can tell a policy pick from
        a silent fallback: SNAG lost ROCK SLIDE to an exception-swallowed
        policy in pt5c) so policies that press fixed move slots can
        notice their mapping broke (Morty lesson: BITE -> SCARY FACE at
        slot 1 cost three whiteouts). Moves landing in previously EMPTY
        slots shift no existing slot and are not recorded; a mon whose
        label changed (evolution without a nickname, party reorder) is
        skipped rather than misattributed."""
        if not before:
            return []
        after = self._party_moves()
        if not hasattr(self, "move_changes"):
            self.move_changes = []     # bare/duck-typed drivers
        src = getattr(self, "_learn_source", "auto")
        changes = []
        for (b_label, b_mv), (a_label, a_mv) in zip(before, after):
            if b_label != a_label:
                continue
            for i, old in enumerate(b_mv):
                new = a_mv[i] if i < len(a_mv) else None
                if old and new and old != new:
                    changes.append({"mon": a_label, "forgot": old,
                                    "learned": new, "slot": i + 1,
                                    "source": src})
        for c in changes:
            log.warning(f"LEARN: {c['mon']} forgot {c['forgot']} -> "
                        f"learned {c['learned']} (slot {c['slot']})")
        self.move_changes.extend(changes)
        self._learn_source = "auto"    # consumed: next flow starts clean
        return changes


    def _resolve_nickname(self, nickname, species):
        """str passes through; dict is keyed by the wild's species name;
        callable gets the species name. None when nothing applies."""
        if nickname is None:
            return None
        if callable(nickname):
            return nickname(species)
        if isinstance(nickname, dict):
            return nickname.get(species)
        return nickname

    def _ball_policy(self, ball="POKE BALL", max_balls=10):
        """Per-turn policy that throws `ball` until it connects, the ball
        pocket runs dry, or `max_balls` are gone -- then flees rather than
        KO the target. Shared by catch() and the encounter hook's 'catch'
        disposition, so both throw balls exactly the same way."""
        thrown = [0]

        def pol(rows, me, enemy):
            dry = bag_item_index(self.emu, self.names, ball, "balls") is None
            if dry or thrown[0] >= max_balls:
                return "flee"
            thrown[0] += 1
            return ("ball", ball)

        return pol

    def _encounter_ball(self, name=None):
        """Which ball a bare 'catch' disposition throws: the named one, or
        the cheapest ball actually in the pocket -- answering 'catch' must
        not burn an ULTRA BALL on a RATTATA. Falls back to POKE BALL when
        the pocket cannot be read (the ball policy then flees on a dry
        bag rather than KO the target)."""
        if name:
            return name
        for cand in self.BALL_PREFERENCE:
            try:
                if bag_item_index(self.emu, self.names, cand,
                                  "balls") is not None:
                    return cand
            except Exception:
                break
        return self.BALL_PREFERENCE[0]

    def catch(self, ball="POKE BALL", max_balls=10, nickname=None):
        """Throw `ball` at the current wild until it connects or the budget
        runs out; flees rather than KO the target once out of balls.
        `nickname`: str (applied to whatever is caught), dict keyed by
        species name, or callable(species_name) -> str|None.
        This call IS the encounter disposition, so encounter_policy is not
        asked again for this battle."""
        self._pending_nickname = nickname
        try:
            return self.fight(policy=self._ball_policy(ball, max_balls),
                              consult_encounter=False)
        finally:
            self._pending_nickname = None

    # -- rotation trainer ---------------------------------------------------

    def catch_up(self, nickname=None, ball="POKE BALL", max_balls=6,
                 max_encounters=12, label=""):
        """Catch-composition primitive: pace into the nearest grass belt
        on the current map, engage wilds, and throw balls until a catch
        lands or a budget runs out. One tool call instead of ~40 lines of
        bespoke policy per session -- without it deciders price catching
        as high-risk-low-reward and run solo (omp-fresh Q&A #3.1).
        Detection is party-growth based; with a FULL party Crystal routes
        catches to the PC and this cannot see them, so keep a slot open.
        Raises ValueError on a grass-less map and RuntimeError when out
        of balls mid-hunt. Returns a structured outcome dict."""
        import random
        if label:
            log.info(f"[{label}] catch_up on {self.map_name()}")
        grass = self._grass_cells()
        if not grass:
            raise ValueError(f"no grass on {self.map_name()} -- travel to "
                             "a route with grass first")

        def _balls():
            return self._bag().get(_norm_item(ball), 0)

        if _balls() == 0:
            raise RuntimeError(f"catch_up: no {ball} in the bag")
        known = {m["name"] for m in game_state(self.emu, self.names)["party"]}
        stall_cycles = 0
        encounters = used_total = 0
        while encounters < max_encounters:
            if self.battle():
                encounters += 1
                b0 = _balls()
                self.catch(nickname=nickname, ball=ball, max_balls=max_balls)
                used_total += max(0, b0 - _balls())
                gs = game_state(self.emu, self.names)["party"]
                fresh = [m for m in gs if m["name"] not in known]
                if fresh:
                    m = fresh[-1]
                    log.info(f"  catch_up: caught {m['name']} "
                          f"({used_total} balls, {encounters} encounters)")
                    return {"caught": True, "species": m["name"],
                            "nick": m.get("nickname"),
                            "level": m["level"], "balls_used": used_total,
                            "encounters": encounters,
                            "party_size": len(gs)}
                if self._bag().get(_norm_item(ball), 0) == 0:
                    raise RuntimeError(
                        f"catch_up: out of {ball} after {encounters} "
                        f"encounters, {used_total} thrown -- restock")
                stall_cycles = 0
                continue
            # no battle this cycle: scene-sealed grass (R29 tutorial)
            # or unreachable belt -- a plain retry loops FOREVER here
            # (moss-run: ~4600 cycles until eval timeout killed the
            # kernel), so count and raise with the goto diagnosis.
            stall_cycles += 1
            if stall_cycles >= 4:
                raise RuntimeError(
                    f"catch_up: {stall_cycles} pace cycles, zero "
                    f"encounters on {self.map_name()} -- grass sealed "
                    f"or unreachable? last_goto_reason="
                    f"{self.last_goto_reason!r} last_choice_options="
                    f"{self.last_choice_options} (resolve_choice the "
                    f"box / d.trip_scenes the cell, then retry)")
            obs = self.observe()
            cx, cy = obs["x"], obs["y"]
            near = sorted(grass, key=lambda c: abs(c[0] - cx)
                          + abs(c[1] - cy))[:8]
            for tx, ty in near:
                try:
                    saved_af, self.auto_fight = self.auto_fight, True
                    try:
                        self.goto(tx, ty, "into the grass")
                    finally:
                        self.auto_fight = saved_af
                    break
                except Exception:
                    continue
            else:
                raise RuntimeError("catch_up: no reachable grass cell on "
                                   f"{self.map_name()}")
            steps = 0
            while not self.battle() and steps < 60:
                o = self.observe()
                tiles, npcs = o["tiles"], {tuple(c) for c in o["npcs"]}
                px, py = o["x"], o["y"]
                opts = []
                for dd, kind in tiles.items():
                    if dd == "here":
                        continue
                    mv = dd.upper()
                    dx, dy = STEP[mv]
                    if (px + dx, py + dy) in npcs:
                        continue
                    if kind == "grass":
                        opts += [mv] * 3      # bias toward re-entering
                    elif kind == "floor":
                        opts.append(mv)
                if not opts:
                    break                     # boxed in; outer relocates
                res = self.step_dir(random.choice(opts))
                if res == "battle":
                    break
                steps += 1
        return {"caught": False, "species": None, "nick": None,
                "balls_used": used_total, "encounters": encounters}

    # Probed plan-only (route()) and the shortest actual match wins, so the
    # list order is only a tie-breaker; covers the whole early-Johto span.
    _HEAL_CENTERS = ("CHERRYGROVE_POKECENTER_1F", "VIOLET_POKECENTER_1F",
                     "ROUTE_32_POKECENTER_1F", "AZALEA_POKECENTER_1F",
                     "GOLDENROD_POKECENTER_1F", "ECRUTEAK_POKECENTER_1F")

    def _grass_cells(self):
        """All tall/long-grass collision cells on the current map."""
        grid = self.nav.grid(self.map_name())
        return [(x, y) for y in range(len(grid))
                for x in range(len(grid[y])) if grid[y][x] in (0x14, 0x18)]

    def _train_heal(self):
        """Mid-training nurse trip: route to whichever Pokécenter actually
        routes shortest, heal, route back. Raises when nothing routes --
        silent 'kept training hurt' would be worse. Transit forces
        auto_fight: manual mode means the DECIDER owns battles at the
        train()/catch_up() call level, not every wild on the nurse rail
        (moss-run [W]: sticky flag starved the heal rail mid-leg)."""
        saved = self.auto_fight
        self.auto_fight = True
        try:
            self._train_heal_inner()
        finally:
            self.auto_fight = saved

    def _train_heal_inner(self):
        here = self.map_name()
        best, best_len = None, None
        for cand in self._HEAL_CENTERS:
            try:
                plan = self.route(cand)
            except Exception:
                continue
            if plan and (best_len is None or len(plan) < best_len):
                best, best_len = cand, len(plan)
        if best is None:
            raise RuntimeError(f"train: no routable Pokécenter from {here};"
                               " heal manually or move nearer a town")
        if best != here:
            self.travel(best)
            if "POKECENTER" not in self.map_name():
                raise RuntimeError(f"train: travel to {best} landed on "
                                   f"{self.map_name()}")
        heal_pokecenter(self)
        if best != here:
            self.travel(here)
        log.info("  train: nurse heal done")

    def train(self, target_level, max_battles=150, targets=None):
        """Rotation-train every non-egg party member to >= target_level in
        the nearest grass patch on the current map; returns the min party
        level. Caller must stand on a map WITH grass (ValueError otherwise)
        -- explicit failure beats silently wandering in search of one.
        `targets`: {nickname-or-species: level} per-mon goals; a mon at or
        above ITS goal stops counting toward done, so a carry can't mask a
        starving teammate (moss-run [W]: BRAMBLE sat L2 through 160
        battles while the carry ate the budget). Mon not named uses
        target_level. Biggest gap rotates in first.
        Level-up learns are accepted per _battle_text_handler's policy;
        any REPLACED move is logged (LEARN: ...) and appended to
        d.move_changes -- check it before reusing slot-based policies."""
        import random
        targets = {k.upper(): v for k, v in (targets or {}).items()}

        def _goal(m):
            return targets.get((m.get("nick") or "").upper(),
                               targets.get(m["species"].upper(),
                                           target_level))

        grass = self._grass_cells()
        if not grass:
            raise ValueError(f"no grass on {self.map_name()} -- walk/travel "
                             "to a route with grass first")
        log.info(f"[train] target L{target_level}, cap {max_battles} battles"
                 f"{', per-mon ' + str(targets) if targets else ''}",)
        battles = dry = 0
        changes0 = len(self.move_changes)
        while True:
            obs = self.observe()
            party = obs["party"]
            members = [(i, m) for i, m in enumerate(party)
                       if not m.get("egg")]
            underleveled = any(m["level"] < _goal(m) for _, m in members)
            if not underleveled or battles >= max_battles:
                break
            lead = party[0]
            sick = any(m.get("status") == "PSN" or m["hp"] <= 0
                       for _, m in members)
            if sick or lead["hp"] / max(lead["max_hp"], 1) < 0.35:
                # The rail is only worth walking if healing can actually
                # change the party. An already-full party that still looks
                # "sick" means something the nurse cannot fix (an egg read
                # as fainted, a permanent status): bail loudly instead of
                # round-tripping to the Pokécenter forever -- that loop ate
                # 30+ trips and zero battles (FUCK_I_MESSED_UP.md #20).
                if all(m["hp"] >= m["max_hp"] and not m.get("status")
                       for _, m in members):
                    raise RuntimeError(
                        "train: heal rail asked for while every non-egg "
                        "member is already full -- refusing to loop; check "
                        "for an egg or an unhealable status in the party")
                log.info(f"  train: healing rail ({lead['species']} "
                      f"{lead['hp']}/{lead['max_hp']})")
                self._train_heal()
                continue               # relocate grass from wherever we land
            # A mon with no damaging move cannot land a KO, so it earns no
            # exp no matter how many encounters it sees: rotating it in
            # burned 60 battles for zero levels. Keep it out of the
            # rotation and say so.
            ids = {n: i for i, n in self.names.moves.items()}

            def _can_damage(m):
                for mv in m["moves"]:
                    row = self.bdata.moves.get(ids.get(mv["name"], -1)) or {}
                    if row.get("power"):
                        return True
                return False
            blocked = [m["nick"] for _, m in members
                       if m["hp"] > 0 and m["level"] < _goal(m)
                       and not _can_damage(m)]
            elig = sorted((i for i, m in members
                           if m["hp"] > 0 and m["level"] < _goal(m)
                           and _can_damage(m)),
                          key=lambda i: _goal(party[i]) - party[i]["level"],
                          reverse=True)   # biggest gap rotates in first
            if not elig and blocked:
                raise RuntimeError(
                    "train: the only under-levelled members have no "
                    f"damaging move ({', '.join(blocked)}) -- they cannot "
                    "KO anything and will never gain exp; teach a damaging "
                    "move or raise them another way")
            if not elig:
                # everyone still under target is FAINTED: the rail above
                # only fires on lead-HP/poison, so revive explicitly
                # instead of reporting a bogus 'done' (bit a verify run:
                # Poliwag fainted -> elig empty -> exited at min L4/10).
                self._train_heal()
                continue
            if not self.battle():
                cx, cy = obs["x"], obs["y"]
                near = sorted(grass, key=lambda c: abs(c[0] - cx)
                              + abs(c[1] - cy))[:8]
                for tx, ty in near:
                    try:
                        saved_af, self.auto_fight = self.auto_fight, True
                        try:
                            self.goto(tx, ty, "into the grass")
                        finally:
                            self.auto_fight = saved_af
                        break
                    except Exception:
                        continue
                else:
                    raise RuntimeError("train: no reachable grass cell on "
                                       f"{self.map_name()}")
                while not self.battle():
                    if self.menu_open():
                        # a leftover post-battle modal silently eats every
                        # movement press (gotcha 7) -- 400 'dry steps' of
                        # nothing. B out of it before pacing on.
                        self.close_menus()
                        continue
                    o = self.observe()
                    tiles = o["tiles"]
                    npcs = {tuple(c) for c in o["npcs"]}
                    px, py = o["x"], o["y"]
                    opts = []
                    for dd, kind in tiles.items():
                        if dd == "here":
                            continue
                        mv = dd.upper()
                        dx, dy = STEP[mv]
                        if (px + dx, py + dy) in npcs:
                            continue
                        if kind == "grass":
                            opts += [mv] * 3      # bias toward re-entering
                        elif kind == "floor":
                            opts.append(mv)
                    if not opts:
                        break            # boxed in; outer loop relocates
                    res = self.step_dir(random.choice(opts))
                    if res == "battle":
                        break
                    dry += 1
                    if dry > 400:
                        raise RuntimeError(
                            "train: 400 steps, zero encounters -- grid "
                            "says grass but terrain disagrees?")
            nxt = elig[battles % len(elig)]
            tgt = party[nxt]
            switched = [False]

            def policy(rows, me, enemy, _nxt=nxt, _tgt=tgt,
                       _did=switched):
                """Once per battle: rotate the next underleveled member in;
                afterwards None lets the default smart attack policy take
                over. A hurting active mon FLEES instead of falling through
                to the default potion flow: its target-slot-0 heal lands on
                a full-HP lead ("no effect"), never consumes, and the
                potion target list wedged 150 battles straight. The nurse
                rail between battles does the healing instead."""
                if not _did[0] and _nxt and not (
                        me["name"] == _tgt["species"]
                        and me["level"] == _tgt["level"]
                        and me["max_hp"] == _tgt["max_hp"]):
                    _did[0] = True
                    return ("switch", _nxt)
                if me["hp"] / max(me["max_hp"], 1) < 0.30:
                    return "flee"       # trainer battles: fails, wedge
                return None             # guard degrades to plain attack

            self.fight(policy=policy)
            battles += 1
            dry = 0
            snap = [(m["species"], m["level"], m["hp"], m["max_hp"])
                    for _, m in members]
            log.info(f"  train: battle {battles}/{max_battles} {snap}")
            if battles % 10 == 0:
                self.save()
        final = [m["level"] for m in self.observe()["party"]
                 if not m.get("egg")]
        lo = min(final) if final else 0
        log.info(f"[train] done after {battles} battles: party min L{lo}"
              f"{' (target reached)' if lo >= target_level else ''}",
              )
        swapped = self.move_changes[changes0:]
        if swapped:
            log.warning(f"[train] {len(swapped)} move slot(s) changed by "
                        "level-up learns this run (LEARN lines above; "
                        "d.move_changes has details) -- re-check any "
                        "policy that presses fixed move slots")
        self.save()
        return lo

    # -- field HM: CUT -----------------------------------------------------

    _CUT_TREE_BYTE = 0x12

    def _party_knows_cut(self):
        """(knows, party_index): does any party member know CUT?"""
        for idx, mon in enumerate(self.observe()["party"]):
            if any(m.get("name") == "CUT" for m in mon.get("moves", [])):
                return True, idx
        return False, None

    def _party_knows(self, move_name):
        """(knows, party_index): does any party member know `move_name`?"""
        for idx, mon in enumerate(self.observe()["party"]):
            if any(m.get("name") == move_name for m in mon.get("moves", [])):
                return True, idx
        return False, None

    def _teach_hm01(self, forget_move=None):
        return self.teach_hm("H1", "CUT", forget_move)

    def _tmhm_pocket(self, max_presses=8):
        """START -> PACK -> the TM/HM pocket (pack.asm jumptable state 8).
        The pockets cycle on L, so at most 3 presses reach it."""
        self.press("START:4 .:40")
        if not self._wait_screen(lambda s: "EXIT" in s):
            return self._menu_fail("tmhm_pocket: START menu never opened")
        if not self.menu.select_label("PACK"):
            why = getattr(self.menu, "last_reason", None) or "no PACK row"
            return self._menu_fail(f"tmhm_pocket: {why}")
        for _ in range(max_presses):
            if self.emu.read_u8("wJumptableIndex") == 8:
                self.press(".:35")
                return True
            self.press("L:4 .:18")
        return self._menu_fail("tmhm_pocket: TM/HM pocket never opened "
                               "(wJumptableIndex never reached 8)")

    @staticmethod
    def pocket_tag(tag):
        """The text a TM/HM pocket ROW actually shows for 'TM01'/'HM03'.

        The 'TM'/'HM' prefix is drawn in GRAPHICS tiles, so the decoded
        row is '01 DYNAMICPUNCH' for a TM and 'H1 CUT' for an HM (live
        screen dump, Olivine pack) -- matching on 'TM01' never hits.
        """
        tag = str(tag).strip().upper()
        if tag.startswith("TM") and tag[2:].isdigit():
            return tag[2:]                       # 'TM01' -> '01'
        if tag.startswith("HM") and tag[2:].isdigit():
            return f"H{int(tag[2:])}"            # 'HM03' -> 'H3'
        return tag                               # already screen-shaped

    def _tmhm_row(self, tag, move_name):
        """Put the pocket cursor on the row naming this TM/HM.

        Rows render as '<tag> <MOVE>' -- '01 DYNAMICPUNCH', 'H1 CUT' --
        and a bare move match is not enough ('FURY CUTTER' contains
        'CUT'), so both halves must be on the row. They are tested
        SEPARATELY because the cursor glyph is painted between them
        ('H3▶SURF'). The list is walked UP to the top first, because the
        pocket remembers its cursor between opens."""
        tag = self.pocket_tag(tag)

        def on_row():
            return any(tag in r and move_name in r
                       for r in self.cursor_rows())
        for _ in range(10):
            if on_row():
                return True
            self.press("U:4 .:14")
        for _ in range(60):
            if on_row():
                return True
            self.press("D:4 .:16")
        return self._menu_fail(
            f"tmhm_row: no row reading '{tag} {move_name}' came under the "
            f"cursor")

    # The party list the TM/HM flow ends on is identified by the tags
    # PlacePartyMonTMHMCompatibility writes at hlcoord 12, 2 stepping two
    # rows per mon (engine/pokemon/party_menu.asm:300-347): 'ABLE' or
    # 'NOT ABLE'. CANCEL is deliberately NOT part of the test -- it is
    # the row after the last mon, so a SIX-mon party puts it at row 13,
    # underneath the description textbox, and requiring it made the
    # predicate unsatisfiable exactly when the party was full.
    def _tmhm_party_list_up(self, joined=None):
        if joined is None:
            joined = "".join(self.emu.screen_text()).upper()
        return "ABLE" in joined

    def _tmhm_use(self, max_steps=26):
        """Confirm the pocket row, take USE, answer the teach prompt's
        YES, and end on the party list.

        Written as a classify-then-act loop instead of a press script,
        because the press script could not finish the flow: it answered
        the teach prompt with ONE A and then only TICKED, waiting for the
        party list. Live (claude-goldeen checkpoint, HM07 -> GOLDEEN --
        FUCK_I_MESSED_UP.md #71/#68, five failed attempts) the YES/NO box
        eats the first A the frame it is drawn (gotcha 2), so the box was
        still up when the ticking started and the list never came.

        Every iteration reads the screen and acts on what is THERE, and
        the party-list test is checked BEFORE any press -- an A press on
        that list selects a mon, which is how a probe of this flow put
        'WATERFALL is not compatible' on screen by picking NOCTOWL."""
        self.press("A:4 .:60")                  # pocket row -> USE/QUIT
        for _ in range(max_steps):
            joined = "".join(self.emu.screen_text()).upper()
            if self._tmhm_party_list_up(joined):
                return True
            if Menus.has_label(self.emu.screen_text(), "YES"):
                self.press("A:5 .:45")          # teach prompt: YES
            elif "YES" in joined and "NO" in joined:
                self.press("U:4 .:16")          # cursor drifted onto NO
            elif Menus.has_label(self.emu.screen_text(), "USE"):
                self.press("A:5 .:40")
            elif self.textbox():
                self.press("A:4 .:40")          # "Booted up an HM." pages
            else:
                self.press(".:20")              # mid-repaint: poll
        return self._menu_fail(
            f"tmhm_use: party list never opened in {max_steps} steps "
            f"(row 14: {self.emu.screen_text()[14].strip()!r})")

    def _able_under_cursor(self):
        """Is the party row under the cursor ABLE to learn this TM/HM?

        Answered from wMenuCursorY, not from the cursor glyph: mon `n`
        (1-based, exactly what wMenuCursorY holds and what
        _party_cursor_to steers) has its name on screen row 2n-1 and its
        ABLE / NOT ABLE tag on row 2n, because
        PlacePartyMonTMHMCompatibility starts at hlcoord 12, 2 and adds
        2 * SCREEN_WIDTH per mon (party_menu.asm:300-330). The glyph scan
        is kept as a fallback for screens where the cursor row is painted
        but WRAM has not caught up."""
        rows = self.emu.screen_text()

        def verdict(text):
            up = text.upper()
            if "ABLE" not in up:
                return None
            return "NOT ABLE" not in up
        cur = self.emu.read_u8("wMenuCursorY")
        if 1 <= cur and 2 * cur < len(rows):
            tag = verdict(rows[2 * cur][12:])
            if tag is not None:
                return tag
        for i, r in enumerate(rows):
            if "▶" in r or "▷" in r:
                tag = verdict(rows[i + 1] if i + 1 < len(rows) else "")
                return bool(tag)
        return False

    def _walk_forget_menu(self, move_name, forget=None):
        """Drive whatever follows the party pick: an outright learn, or
        the "delete a move?" YES plus the move list. `forget` names the
        move to delete; without it the move already under the cursor goes
        (the list opens on SLOT 1, so that is the mon's OLDEST move).

        Shared by teach_hm and teach_tm: one implementation of the walk
        that decides which move disappears."""
        for _ in range(20):
            if self._party_knows(move_name)[0]:
                break
            s = "".join(self.emu.screen_text()).upper()
            if "YES" in s and "NO" in s:
                self.press("A:5 .:70")                # YES: delete one
                if forget:                            # move list is up
                    want = forget.upper()
                    self.press(".:30")
                    for _ in range(6):
                        if any(want in r for r in self.cursor_rows()):
                            break
                        self.press("D:4 .:16")
                self.press("A:5 .:90")                # forget cursor move
            else:
                self.press("A:4 .:45")
        for _ in range(14):                           # drain learn texts
            if not self.textbox():
                break
            self.press("A:4 .:50")
        return self._party_knows(move_name)[0]

    def teach_hm(self, hm_tag, move_name, forget_move=None):
        """Teach the HM whose pocket row reads '<hm_tag> <move_name>'
        (e.g. 'H3', 'SURF') to the first ABLE party member via PACK ->
        TM/HM pocket. `forget_move` names the move to delete if the
        learner already knows four (default: whatever the cursor starts
        on, slot 1). Label/WRAM-driven throughout: menus remember their
        last cursor slot, so blind press counts are never safe.
        Raises RuntimeError (with menus closed) if the flow fails.

        For a NAMED party member and a machine-readable failure instead
        of an exception, use teach_tm -- both drive the same steps."""
        def bail(msg):
            self.close_menus()
            raise RuntimeError(f"teach_hm {move_name}: {msg}")
        if not self._tmhm_pocket():
            bail(self.last_menu_reason or "TM/HM pocket never opened")
        if not self._tmhm_row(hm_tag, move_name):
            bail(self.last_menu_reason or "HM row never under cursor")
        if not self._tmhm_use():
            bail(self.last_menu_reason or "USE flow failed")
        # the D-scan wraps, so every row gets visited wherever it starts
        for _ in range(8):
            if self._able_under_cursor():
                break
            self.press("D:4 .:15")
        else:
            bail(f"no party member is ABLE to learn {move_name}")
        self.press("A:5 .:80")                        # choose the mon
        learned = self._walk_forget_menu(move_name, forget_move)
        # postcondition: overworld interactive again, move actually known
        if not self.close_menus():
            raise RuntimeError(f"teach_hm {move_name}: a menu is still "
                               "open after teaching")
        if not learned:
            raise RuntimeError(f"teach_hm {move_name}: teaching failed "
                               "verification")

    _tmhm_table = None      # {'TM01': 'DYNAMICPUNCH', ...}, lazily parsed
    _species_tmhm = None    # {SPECIES: [MOVE_CONST, ...]}

    def tmhm_moves(self):
        """``{'TM01': 'DYNAMICPUNCH', ..., 'HM07': 'WATERFALL'}`` -- which
        move each TM/HM teaches, in TM/HM number order."""
        if Driver._tmhm_table is None:
            from crystalagent.tactics import parse_tmhm_moves
            Driver._tmhm_table = parse_tmhm_moves(paths.REPO_ROOT)
        return Driver._tmhm_table

    def species_tmhm(self):
        """``{SPECIES: [MOVE_CONST, ...]}`` TM/HM learnsets (base stats)."""
        if Driver._species_tmhm is None:
            from crystalagent.tactics import parse_species_tmhm
            Driver._species_tmhm = parse_species_tmhm(paths.REPO_ROOT)
        return Driver._species_tmhm

    def tmhm_stock(self):
        """``{'TM23': count}`` for every TM/HM actually held.

        TMs do not live in the item pockets _bag() reads: wTMsHMs is a
        flat count-per-TMNUM array (ram/wram.asm:3109), TM01..TM50 then
        HM01..HM07, which is also the order the pocket lists them in."""
        tags = list(self.tmhm_moves())
        bank, addr = self.emu.sym["wTMsHMs"]
        raw = self.emu.read((bank, addr), len(tags))
        return {tag: n for tag, n in zip(tags, raw) if n}

    def _tm_fail(self, reason):
        self.last_tm_reason = reason
        log.warning(f"  teach_tm: {reason}")
        return False

    def _resolve_tm(self, tm):
        """'TM23' | 'IRON TAIL' | 'IRON_TAIL' -> (tag, move display name),
        or (None, None)."""
        table = self.tmhm_moves()
        key = str(tm).strip().upper().replace(" ", "")
        tag = key if key in table else next(
            (t for t, mv in table.items()
             if _norm_item(mv) == _norm_item(key)), None)
        if tag is None:
            return None, None
        const = table[tag]
        # the ROM's display name for that move constant ('IRON_TAIL' ->
        # 'IRON TAIL'); compared normalised so spacing never matters
        want = _norm_item(const)
        name = next((n for n in self.names.moves.values()
                     if _norm_item(n) == want), const.replace("_", " "))
        return tag, name

    def _party_row(self, mon):
        """0-based party row of the member named `mon` -- NICKNAME first,
        then species, since a model may say either. ValueError on an
        unknown name: teaching the wrong mon is worse than stopping."""
        want = _norm_name(mon)
        party = game_state(self.emu, self.names)["party"]
        for slot, m in enumerate(party):
            if _norm_name(m.get("nickname") or "") == want:
                return slot
        for slot, m in enumerate(party):
            if _norm_name(m.get("name") or m.get("species") or "") == want:
                return slot
        raise ValueError(
            f"teach_tm: no party member named {mon!r} (party: "
            f"{[m.get('nickname') for m in party]})")

    def teach_tm(self, tm, mon, forget=None):
        """Teach a TM (or HM) to a NAMED party member. True only when the
        move is really on that mon afterwards.

        `tm` is a tag or the move it teaches ('TM23', 'IRON TAIL');
        `mon` is a nickname or species; `forget` names the move to delete
        when the mon already knows four (default: the move the list opens
        on, i.e. its oldest).

        Everything checkable is checked BEFORE a single button is pressed,
        because a refusal mid-flow leaves menus open (gotcha 7) and the
        game's own "not compatible" path just wastes the TM's turn:

          'unknown-tm'    no such TM/HM tag or move
          'not-in-bag'    wTMsHMs holds none of that TM
          'cannot-learn'  the species' tmhm learnset excludes the move
          'already-knows' that mon already has it

        An unknown `mon`, or a `forget` the mon does not know / an HM move
        (the game refuses to delete those), raises ValueError.
        """
        self.last_tm_reason = None
        tag, move_name = self._resolve_tm(tm)
        if tag is None:
            return self._tm_fail(f"unknown-tm: {tm!r} names no TM/HM")
        slot = self._party_row(mon)
        party = game_state(self.emu, self.names)["party"]
        entry = party[slot]
        label = entry.get("nickname") or entry.get("name")
        stock = self.tmhm_stock()
        if not stock.get(tag):
            return self._tm_fail(f"not-in-bag: no {tag} ({move_name}) held")
        const = self.tmhm_moves()[tag]
        learnset = self.species_tmhm().get(entry.get("name")) \
            or self.species_tmhm().get(entry.get("species")) or []
        if const not in learnset:
            return self._tm_fail(
                f"cannot-learn: {entry.get('name')} cannot learn "
                f"{move_name} ({tag})")
        known = [m.get("name") for m in entry.get("moves", [])]
        if move_name in known:
            return self._tm_fail(f"already-knows: {label} already has "
                                 f"{move_name}")
        if forget is not None:
            if forget.strip().upper() in self.HM_MOVES:
                raise ValueError(
                    f"teach_tm: the game refuses to delete HM move "
                    f"{forget!r}")
            if forget not in known:
                raise ValueError(
                    f"teach_tm: {label} does not know {forget!r} "
                    f"(knows: {known})")
        log.info(f"[teach_tm] {tag} {move_name} -> {label} (slot {slot})"
                 + (f", forgetting {forget}" if forget else ""))
        if not self._tmhm_pocket():
            self.close_menus()
            return self._tm_fail(self.last_menu_reason or "no TM/HM pocket")
        if not self._tmhm_row(tag, move_name):
            self.close_menus()
            return self._tm_fail(self.last_menu_reason or "no TM row")
        if not self._tmhm_use():
            self.close_menus()
            return self._tm_fail(self.last_menu_reason or "USE flow failed")
        if not self._party_cursor_to(slot + 1):
            self.close_menus()
            return self._tm_fail(
                f"target-miss: could not put the party cursor on row "
                f"{slot + 1} ({label})")
        if not self._able_under_cursor():
            self.close_menus()
            return self._tm_fail(
                f"not-able: the game reports {label} NOT ABLE to learn "
                f"{move_name}")
        self.press("A:5 .:80")                        # choose the mon
        learned = self._walk_forget_menu(move_name, forget)
        self.close_menus()
        if not learned:
            return self._tm_fail(f"not-learned: {label} does not know "
                                 f"{move_name} after the flow")
        self.last_tm_reason = "learned"
        log.info(f"  {label} learned {move_name}")
        return True

    def _party_cursor_to(self, row, max_steps=12):
        """Move the party-menu cursor to 1-based `row` using the live
        wMenuCursorY (the menu wraps, so press counts from an unknown
        start are meaningless). Returns True on arrival."""
        for _ in range(max_steps):
            cur = self.emu.read_u8("wMenuCursorY")
            if cur == row:
                return True
            self.press("D:4 .:15" if cur < row else "U:4 .:15")
        return self.emu.read_u8("wMenuCursorY") == row

    def party_swap(self, row_a, row_b):
        """Swap two 1-based party slots via START -> POKéMON -> SWITCH.
        Verifies against wPartySpecies so a menu desync can't be mistaken
        for success. Returns True when the species really traded places."""
        from crystalagent.state import game_state
        before = [m["species"] for m in game_state(self.emu, self.names)["party"]]
        if row_a == row_b or max(row_a, row_b) > len(before):
            return False
        want = list(before)
        want[row_a - 1], want[row_b - 1] = want[row_b - 1], want[row_a - 1]

        for _ in range(3):
            self.press("START:4 .:45")
            if self.menu_open():
                break
            self.press(".:40")
        else:
            log.warning("  START menu did not open")
            return False
        # has_label() is a startswith test and POKéDEX also starts with
        # "POK", so steer by the row text instead of a label prefix.
        for _ in range(10):
            row = self.menu.cursor_row()
            if row and "MON" in row[1].upper() and "DEX" not in row[1].upper():
                self.press("A:4 .:25")
                break
            self.press("D:6 .:12")
        else:
            self.close_menus()
            log.info("  could not open the party menu")
            return False
        self.press(".:25")
        for row, label in ((row_a, "first"), (row_b, "second")):
            if not self._party_cursor_to(row):
                self.close_menus()
                log.info(f"  cursor never reached {label} row {row}")
                return False
            self.press("A:4 .:25")
            if label == "first":
                # slot menu: the mon's FIELD MOVES (CUT/SURF/STRENGTH/..)
                # list ABOVE the fixed STATS/SWITCH rows, so the row
                # count varies per mon -- steer by row TEXT, never by
                # position (wren pt6: blind counts fired Strength)
                if not self.select_menu_row("SWITCH", max_presses=8):
                    self.close_menus()
                    log.info("  SWITCH entry not found")
                    return False
                self.press(".:25")
        self.press(".:30")
        self.close_menus()
        after = [m["species"] for m in game_state(self.emu, self.names)["party"]]
        ok = after == want
        log.warning(f"  party_swap {row_a}<->{row_b}: {'ok' if ok else 'FAILED'} {after}",
              )
        return ok

    # -- Bill's PC: the boxes ---------------------------------------------
    # The PC is the one list in the game that RE-ARMS itself. A completed
    # deposit jumps the jumptable back to .Init with the cursor reset to
    # 0, so the list comes straight back up on the NEXT party member --
    # and "press A until the dialog stops changing" deposits again, and
    # again. That loop put five of six party members in the box in one
    # live session, including the run's only real fighter
    # (FUCK_I_MESSED_UP.md #72). It is also unreadable: the selection
    # cursor is an OAM sprite, so no ▶/▷ glyph exists (#73).
    #
    # So every primitive below
    #   (a) targets by INDEX read out of WRAM, never by press counts,
    #   (b) presses the confirming A exactly ONCE, and
    #   (c) decides success from observe()['party'] and the SRAM box,
    #       never from dialog text -- and refuses to act twice.
    #
    # The state machine is the engine's own: _DepositPKMN (bills_pc.asm:1)
    # and _WithdrawPKMN (:260) each run a five-entry jumptable over
    # wJumptableIndex --
    #   0 .Init   1 list joypad   2 prep submenu   3 submenu   4 exit
    # -- and the list selection is the sum
    #   wBillsPC_CursorPosition + wBillsPC_ScrollPosition
    # which is exactly what BillsPC_LoadMonStats (:1113) reads.
    PC_LIST_STATE = 1
    PC_SUBMENU_STATE = 3
    # BillsPC_PlaceString draws the PC's own prompt line at hlcoord 1,16
    # (bills_pc.asm:962): "Choose a ᴾᴹ." on a list, "It's your last ᴾᴹ!"
    # and "There's no room!" when the engine refuses.
    PC_PROMPT_ROW = 16

    def _pc_fail(self, reason, exit_ui=True):
        self.last_pc_reason = reason
        if exit_ui:
            self._pc_exit()
        log.warning(f"  pc: {reason}")
        return False

    def _pc_state(self):
        return self.emu.read_u8("wJumptableIndex")

    def _pc_index(self):
        """0-based selection in the open PC list (WRAM, not the screen)."""
        return self.emu.read_u8("wBillsPC_CursorPosition") + \
            self.emu.read_u8("wBillsPC_ScrollPosition")

    def _pc_prompt(self):
        rows = self.emu.screen_text()
        return rows[self.PC_PROMPT_ROW].strip() \
            if len(rows) > self.PC_PROMPT_ROW else ""

    def _pc_list_up(self):
        """The DEPOSIT/WITHDRAW mon list is up and polling the joypad."""
        return self._pc_state() == self.PC_LIST_STATE and \
            "Choose a" in self._pc_prompt()

    def _pc_closed(self):
        """The PC session is over and the overworld owns input again.

        Neither half of this can be dropped. close_menus()/menu_open()
        alone report 'clean' with a box list still on screen (no cursor
        glyph, and the list's textbox is at row 15, not the row-12 one
        `textbox()` looks for); wScriptMode alone is useless here -- it
        reads 1 on a perfectly interactive overworld (live: the
        claude-indigo-plateau checkpoint)."""
        return not self._pc_list_up() and not self.menu_open()

    def _pc_exit(self, max_presses=12):
        """B out of the PC. B is the only safe key here (gotcha 13's
        shop lesson, one screen over): A on a list confirms a deposit."""
        for _ in range(max_presses):
            if self._pc_closed():
                break
            self.press("B:6 .:24")
        self.settle()
        return self._pc_closed()

    def box_list(self):
        """The current PC box, read out of SRAM -- ``{'box': n, 'count': k,
        'capacity': 20, 'mons': [{species, name, nickname, level}, ...]}``.

        Never touches the screen or a menu, so it is safe to call at any
        time (including before opening the PC, to see whether a deposit
        even fits) and it is authoritative: the WITHDRAW list paints this
        same order."""
        return box_state(self.emu, self.names)

    # observe()['party'] calls them 'nick'/'species'-as-a-name while
    # game_state()/box_list() call them 'nickname'/'name'; a lookup that
    # only knew one shape silently found nothing.
    _NICK_KEYS = ("nickname", "nick")
    _SPECIES_KEYS = ("name", "species")

    @classmethod
    def _named_slot(cls, mon, entries):
        """Index of `mon` (nickname first, then species) in a list of
        party/box entries, or None. Nickname first because that is what a
        model says, species second because a box mon may be un-nicknamed."""
        want = _norm_name(mon)

        def match(entry, keys):
            return any(_norm_name(entry.get(k) or "") == want for k in keys)
        for keys in (cls._NICK_KEYS, cls._SPECIES_KEYS):
            for i, m in enumerate(entries):
                if match(m, keys):
                    return i
        return None

    def _pc_page(self, pred, presses=14):
        """Advance the PC's text pages with SINGLE A presses until
        `pred(rows)` holds.

        flush_dialog cannot do this job: cancelling the terminal's
        "Access whose PC?" menu leaves its ▶ painted behind the page that
        follows, and a stale glyph outside the box makes
        dialog_press_safe refuse every press (live: "BILLˢ PC accessed."
        never advanced). Only text pages sit between the rows this is
        asked to wait for, so an A press here cannot buy, teach or
        deposit anything."""
        for _ in range(presses):
            if pred(self.emu.screen_text()):
                return True
            self.press("A:4 .:40")
        return pred(self.emu.screen_text())

    def _pc_open(self, action):
        """Open BILL's PC and take `action` ('DEPOSIT' or 'WITHDRAW'),
        leaving its mon list up. Reuses a list that is already up (the
        flow re-arms itself after every confirm, so recovery from a bad
        deposit is a second target on the SAME list)."""
        if self._pc_list_up():
            return True
        if not self._pc_closed() and not self._pc_exit():
            return self._pc_fail("busy: a menu owns the screen and B would "
                                 "not clear it", exit_ui=False)
        cell = self._pc_tile()
        if cell is None:
            return self._pc_fail(
                f"no-pc: no COLL_PC ($93) tile on {self.map_name()} -- "
                f"stand in a Pokécenter or Bill's house "
                f"(find_tiles('pc'))", exit_ui=False)
        if self.talk_to(*cell, label="PC") != "talked":
            return self._pc_fail(f"no-pc: could not reach/use the PC at "
                                 f"{cell}", exit_ui=False)

        def box_menu(rows):
            return any("WITHDRAW" in r for r in rows)
        # terminal menu: "BILL's PC / <PLAYER>'s PC / PROF.OAK's PC /
        # TURN OFF" (engine/events/pokecenter_pc.asm:59-68), reached
        # after the "turned on the PC" page. Bill's own house PC skips
        # it, so it is optional -- the box menu's rows are the real gate.
        if not box_menu(self.emu.screen_text()) and \
                any("BILL" in r for r in self.emu.screen_text()):
            if not self.select_menu_row("BILL", max_presses=6):
                return self._pc_fail("no-list: the BILL's PC row would not "
                                     "confirm")
        if not self._pc_page(box_menu):
            return self._pc_fail(
                f"no-list: BILL's PC never drew its WITHDRAW/DEPOSIT menu "
                f"(row 14: {self.emu.screen_text()[14].strip()!r})")
        if not self.select_menu_row(action, max_presses=8):
            return self._pc_fail(f"no-list: the {action} row would not "
                                 f"confirm")
        f0 = self.emu.frame
        while self.emu.frame - f0 < 900:
            if self._pc_list_up():
                return True
            self.press(".:20")
        return self._pc_fail(f"no-list: {action} never drew a mon list "
                             f"(prompt: {self._pc_prompt()!r})")

    def _pc_tile(self):
        """The nearest PC tile on this map, or None. Journal #45 had to
        find this by hand because find_tiles had no word for $93."""
        here = self.pos()[2:]
        cells = self.find_tiles("pc")
        if not cells:
            return None
        return min(cells, key=lambda c: abs(c[0] - here[0])
                   + abs(c[1] - here[1]))

    def _pc_cursor_to(self, index, expect=None, max_steps=30):
        """Put the PC list's selection on 0-based `index`, verified against
        WRAM after every single press. `expect` (a species name) is
        cross-checked against the info panel PCMonInfo redraws, which is
        the only thing on screen that tracks this cursor (#73)."""
        for _ in range(max_steps):
            cur = self._pc_index()
            if cur == index:
                break
            self.press("D:4 .:18" if cur < index else "U:4 .:18")
        if self._pc_index() != index:
            return self._pc_fail(
                f"target-miss: the PC cursor stopped at {self._pc_index()} "
                f"short of {index}")
        if expect:
            self.press(".:24")            # let PCMonInfo finish repainting
            shown = self.menu.pc_info()["name"]
            if shown.upper() != str(expect).upper():
                return self._pc_fail(
                    f"target-miss: index {index} shows {shown!r}, expected "
                    f"{expect!r} -- the list is not what memory says")
        return True

    def _pc_confirm(self, action):
        """One A press to open the DEPOSIT/STATS/RELEASE/CANCEL box, then
        the labelled row. That box IS glyph-driven (a STATICMENU_CURSOR
        VerticalMenu, bills_pc.asm:228), so select_menu_row can read it --
        unlike the list behind it."""
        self.press("A:4 .:40")
        f0 = self.emu.frame
        while self.emu.frame - f0 < 600:
            if self._pc_state() == self.PC_SUBMENU_STATE:
                break
            self.press(".:15")
        else:
            return self._pc_fail(
                f"no-list: the {action} submenu never opened "
                f"(jumptable {self._pc_state()})")
        self.press(".:20")
        if not self.select_menu_row(action, max_presses=6):
            return self._pc_fail(f"no-list: no {action} row in the submenu")
        self.press(".:60")
        return True

    def deposit(self, mon):
        """Put the party member named `mon` (nickname or species) into the
        current box. True only when observe()['party'] really lost it.

        Refuses BEFORE pressing anything when the game would refuse or the
        result would be a whiteout risk; `last_pc_reason` says which:

          'no-such-mon'  nobody in the party answers to that name
          'last-mon'     it is the only mon that can fight
          'box-full'     the current box already holds 20
          'holds-mail'   the engine refuses to box a mail carrier

        Exactly ONE mon moves per call: the deposit list re-arms itself on
        the next party member, and a blind confirm loop empties the party
        (#72)."""
        self.last_pc_reason = None
        party = self.observe()["party"]
        slot = self._named_slot(mon, party)
        if slot is None:
            return self._pc_fail(
                f"no-such-mon: no party member named {mon!r} "
                f"(party: {[m['nick'] for m in party]})", exit_ui=False)
        entry = party[slot]
        if sum(1 for m in party if not m.get("egg")) <= 1 \
                and not entry.get("egg"):
            return self._pc_fail(
                f"last-mon: {entry['nick']} is the only mon that can fight "
                f"-- the engine refuses ('It's your last ᴾᴹ!')",
                exit_ui=False)
        box = self.box_list()
        if box["count"] >= box["capacity"]:
            return self._pc_fail(
                f"box-full: box {box['box']} already holds "
                f"{box['count']}/{box['capacity']} -- CHANGE BOX first",
                exit_ui=False)
        # observe()['party'] carries no held item; game_state does, and
        # BillsPC_CheckMail_PreventBlackout refuses a mail carrier outright
        held = (game_state(self.emu, self.names)["party"][slot].get("item")
                or "")
        if "MAIL" in held.upper():
            return self._pc_fail(f"holds-mail: {entry['nick']} carries "
                                 f"{held}", exit_ui=False)
        log.info(f"[deposit] {entry['nick']} ({entry['species']} "
                 f"L{entry['level']}) -> box {box['box']} "
                 f"({box['count']}/{box['capacity']})")
        if not self._pc_open("DEPOSIT"):
            return False
        if not self._pc_cursor_to(slot, expect=entry["species"]):
            return False
        if not self._pc_confirm("DEPOSIT"):
            return False
        return self._pc_settled("deposited", party, box,
                                gone=entry["nick"], delta=-1)

    def withdraw(self, mon):
        """Take the mon named `mon` (nickname or species) out of the
        current box and into the party. True only when
        observe()['party'] really gained it.

        `last_pc_reason`: 'not-in-box' (nothing in the box answers to that
        name -- box_list() shows what does), 'party-full' (six already).
        One mon per call, same reason as deposit."""
        self.last_pc_reason = None
        party = self.observe()["party"]
        box = self.box_list()
        index = self._named_slot(mon, box["mons"])
        if index is None:
            return self._pc_fail(
                f"not-in-box: nothing named {mon!r} in box {box['box']} "
                f"(holds: {[m['nickname'] for m in box['mons']]})",
                exit_ui=False)
        if len(party) >= 6:
            return self._pc_fail(
                "party-full: six already -- deposit one first", exit_ui=False)
        entry = box["mons"][index]
        log.info(f"[withdraw] {entry['nickname']} ({entry['name']} "
                 f"L{entry['level']}) <- box {box['box']} slot {index + 1}")
        if not self._pc_open("WITHDRAW"):
            return False
        if not self._pc_cursor_to(index, expect=entry["name"]):
            return False
        if not self._pc_confirm("WITHDRAW"):
            return False
        return self._pc_settled("withdrawn", party, box,
                                gained=entry["nickname"], delta=+1)

    def _pc_settled(self, done, party0, box0, gone=None, gained=None,
                    delta=0):
        """Did EXACTLY the one intended mon move? Judged on observed state
        (the live party plus the SRAM box), never on dialog text -- and
        loudly when more than one moved, because that is the #72 wound and
        a caller must not learn about it from a level-up log 20 minutes
        later."""
        self.press(".:60")
        party1 = self.observe()["party"]
        box1 = self.box_list()
        moved = len(party1) - len(party0)
        nicks0 = [m["nick"] for m in party0]
        nicks1 = [m["nick"] for m in party1]
        if moved != delta:
            if moved and abs(moved) > abs(delta):
                self._pc_exit()
                self.last_pc_reason = "over-applied"
                raise RuntimeError(
                    f"pc {done}: {abs(moved)} mons moved, not 1 "
                    f"({nicks0} -> {nicks1}) -- the PC list re-armed and "
                    f"something pressed A twice (FUCK_I_MESSED_UP.md #72)")
            return self._pc_fail(
                f"unchanged: party {nicks0} -> {nicks1}, box "
                f"{box0['count']} -> {box1['count']} (prompt: "
                f"{self._pc_prompt()!r})")
        if gone and _norm_name(gone) in [_norm_name(n) for n in nicks1]:
            return self._pc_fail(f"unchanged: {gone} is still in the party")
        if gained and _norm_name(gained) not in [_norm_name(n)
                                                 for n in nicks1]:
            return self._pc_fail(f"unchanged: {gained} did not join the "
                                 f"party")
        self._pc_exit()
        self.last_pc_reason = done
        log.info(f"  {done}: party {nicks0} -> {nicks1}, box "
                 f"{box0['count']} -> {box1['count']}")
        return True

    def use_cut(self, tree_x, tree_y, label="", forget_move=None):
        """Cut down the small tree at (tree_x, tree_y) on the current map:
        teaches HM01 CUT via the pack flow if nobody knows it yet (deleting
        `forget_move` if the learner is at four moves), walks to a standable
        cell beside the tree, faces it, and uses START -> POKéMON -> mon ->
        field-move CUT. Verifies the tree's collision actually cleared and
        steps onto its cell."""
        def scr():
            return "".join(self.emu.screen_text()).upper()
        if self.battle():
            self.fight()
        name = self.map_name()
        grid = self.nav.grid(name)
        hgt, wid = len(grid), len(grid[0])
        if not (0 <= tree_x < wid and 0 <= tree_y < hgt) or \
                grid[tree_y][tree_x] != self._CUT_TREE_BYTE:
            raise RuntimeError(f"use_cut: ({tree_x}, {tree_y}) is not a "
                               f"cuttable tree on {name}")
        knows, knower = self._party_knows_cut()
        if not knows:
            log.info("  no one knows CUT; teaching HM01")
            self._teach_hm01(forget_move=forget_move)
            knows, knower = self._party_knows_cut()
        # approach cell: any standable neighbour we can actually reach,
        # facing back toward the tree
        inv = {"U": "D", "D": "U", "L": "R", "R": "L"}
        cands = []
        for d, (dx, dy) in STEP.items():
            ax, ay = tree_x + dx, tree_y + dy
            if 0 <= ax < wid and 0 <= ay < hgt and \
                    self._standable(name, (ax, ay)):
                cands.append(((ax, ay), inv[d]))
        placed = False
        for round_ in range(3):          # wandering NPCs can blockade the
            for (ax, ay), face in cands: # only path; pause and retry
                if self.goto(ax, ay, label or "use_cut approach"):
                    self.press(f"{face}:4 .:10")
                    placed = True
                    break
            if placed:
                break
            log.info("  approach blocked (wandering NPC?); pausing")
            self.press(".:60 .:60 .:60 A:4 .:30")
        if not placed:
            raise RuntimeError(
                f"use_cut: no reachable approach beside the tree "
                f"({tree_x}, {tree_y})")

        # START -> POKEMON -> (knower or first mon) -> field-move CUT row
        self.press("START:4 .:40")
        if not self._wait_screen(lambda s: "EXIT" in s):
            raise RuntimeError("use_cut: START menu never opened")
        # label-driven: the START menu REMEMBERS its last cursor slot, so
        # a fixed press count opens the wrong entry after any PACK visit.
        # 'POKé' alone also matches POKéDEX -- include the M.
        if not self.menu.select_label("POKéM"):
            self.close_menus()
            raise RuntimeError("use_cut: POKéMON entry not found in "
                               "START menu")
        if not self._wait_screen(lambda s: "CANCEL" in s):
            self.close_menus()
            raise RuntimeError("use_cut: party list never opened")
        if not self._party_cursor_to((knower or 0) + 1):
            self.close_menus()
            raise RuntimeError("use_cut: party cursor never reached the "
                               "CUT knower")
        # confirm-until-open: the first A can land during menu setup and
        # get swallowed (gotcha 2)
        sub = False
        for _ in range(6):
            self.press("A:6 .:40")
            if self._wait_screen(lambda s: "STATS" in s and "SWITCH" in s,
                                 frames=80):
                sub = True
                break
        if not sub:
            self.close_menus()
            raise RuntimeError("use_cut: POKéMON submenu never opened")
        # the party list stays visible behind the submenu box and field
        # moves sit ABOVE STATS/SWITCH, so steer by row TEXT (wren pt6)
        if not self.select_menu_row("CUT", confirm=False, max_presses=10):
            # a field move refused (wrong mon, indoors, "Can't use that
            # here") leaves the party menu + submenu OPEN, and an open
            # menu eats every movement input afterwards (gotcha 7). Every
            # field-move failure path must close its own UI.
            self.close_menus()
            raise RuntimeError("use_cut: CUT row missing from the "
                               "POKéMON submenu")
        self.press("A:6 .:50")                        # use CUT
        for _ in range(12):
            s = scr()
            if "YES" in s and "NO" in s:
                self.press("A:5 .:45")                # confirm cut
            elif not self.textbox():
                break
            else:
                self.press("A:4 .:45")
        self.settle()
        # postcondition: nothing modal left on screen (a stray menu here
        # gets baked into the next save and eats all movement input)
        if not self.close_menus():
            raise RuntimeError("use_cut: a menu is still open after CUT")
        # verify by walking onto the former tree cell (the static grid
        # still shows $12 -- cut trees are swapped only in the engine's
        # block memory)
        r = self._step(face)
        if self.pos()[2:] != (tree_x, tree_y):
            raise RuntimeError(
                f"use_cut: tree at {(tree_x, tree_y)} still standing after "
                f"CUT (step {r} -> {self.pos()[2:]})")
        log.info(f"  [cut] tree at {(tree_x, tree_y)} removed; stepped {r} "
              f"-> {self.map_name()} {self.pos()[2:]}")
        return True

    # -- water HMs: the overworld A press, never the party menu ----------
    # The A button is a FIRST-CLASS field-move entry point. The overworld
    # A handler dispatches on wFacingTileID
    # (engine/overworld/events.asm:1085-1125):
    #     CheckCutTreeTile      -> TryCutOW
    #     CheckWhirlpoolTile    -> TryWhirlpoolOW
    #     CheckWaterfallTile    -> TryWaterfallOW
    #     CheckHeadbuttTreeTile -> TryHeadbuttOW
    #     otherwise             -> TrySurfOW
    # and each of those checks the party move and the badge itself before
    # asking "Do you want to use X?".
    #
    # The MENU path (START -> POKéMON -> mon -> move, WaterfallFunction /
    # WhirlpoolFunction) reaches the same CheckMapCanWaterfall, but that
    # predicate reads wTileUp / wPlayerDirection -- surrounding-tile state
    # GetMovementPermissions maintains for the overworld loop
    # (home/map.asm:1565) -- and from inside the menu it answers "Can't
    # use that here." Live, twice: at TOHJO_FALLS (9,12) facing UP at a
    # 0x33 COLL_WATERFALL tile the menu refused while a single plain A
    # press from the identical tile and facing answered "Do you want to
    # use WATERFALL?" and climbed (9,12) -> (9,7)
    # (FUCK_I_MESSED_UP.md #75, which retracts #70's wrong "the tile is
    # wrong" conclusion for WHIRLPOOL).
    #
    # wFacingTileID itself is only meaningful for a few frames after an A
    # press, so nothing here reads it; the FACED CELL's collision byte
    # (static grid, stable) and wPlayerDirection (stable) are the inputs.
    OW_FIELD_MOVES = {
        # move: (tile kind faced, badge the engine checks)
        "WATERFALL": ("waterfall", "RISING"),
        "WHIRLPOOL": ("whirlpool", "GLACIER"),
    }
    # wPlayerDirection holds direction << 2 (DOWN 0, UP 4, LEFT 8,
    # RIGHT 12); CheckMapCanWaterfall masks it with $c.
    FACING_BYTE = {"D": 0x0, "U": 0x4, "L": 0x8, "R": 0xC}

    def _field_fail(self, reason):
        """Field-move refusal. Closes menus on the way out: a field move
        that fails leaves its menu open, and an open menu eats all
        movement input (AGENTS.md gotcha 17)."""
        self.last_field_reason = reason
        self.close_menus()
        log.warning(f"  field move: {reason}")
        return False

    def facing(self):
        """Which way the player is facing: 'U' | 'D' | 'L' | 'R'."""
        raw = self.emu.read_u8("wPlayerDirection") & 0xC
        return {v: k for k, v in self.FACING_BYTE.items()}.get(raw, "?")

    def face(self, mv):
        """Turn to face `mv` without stepping (a short directional press
        against anything turns in place) and confirm via
        wPlayerDirection. True when we really face that way."""
        for _ in range(4):
            if self.facing() == mv:
                return True
            self.press(f"{mv}:6 .:12")
        return self.facing() == mv

    def use_field_move(self, move, facing=None):
        """Use a water HM (WATERFALL, WHIRLPOOL) on the tile we are
        FACING, through the overworld A press. True only when the world
        actually changed -- the waterfall moved us, or the whirlpool is
        gone from the live map.

        `facing` ('U'/'D'/'L'/'R') turns first. Everything checkable is
        checked before the A press; `last_field_reason` says which:

          'unknown-move'  not an A-dispatched field move
          'no-knower'     nobody in the party knows it (field_moves())
          'no-badge'      the engine's badge gate would refuse
          'no-facing'     could not turn to the requested direction
          'wrong-tile'    the faced cell is not that obstacle
          'no-prompt'     the A press produced no "use it?" question
          'unchanged'     the prompt was answered and nothing moved
        """
        self.last_field_reason = None
        move = str(move).strip().upper()
        spec = self.OW_FIELD_MOVES.get(move)
        if spec is None:
            return self._field_fail(
                f"unknown-move: {move!r} is not an A-dispatched field move "
                f"({'/'.join(self.OW_FIELD_MOVES)})")
        want_kind, badge = spec
        knower = self.field_moves().get(move)
        if not knower:
            return self._field_fail(f"no-knower: nobody in the party knows "
                                    f"{move}")
        badges = game_state(self.emu, self.names)["player"]["johto_badges"]
        if badge and badge not in badges:
            return self._field_fail(f"no-badge: {move} needs the {badge} "
                                    f"badge (have: {badges})")
        if facing and not self.face(facing):
            return self._field_fail(f"no-facing: could not turn {facing} "
                                    f"(facing {self.facing()})")
        mv = self.facing()
        if mv not in STEP:
            return self._field_fail(f"no-facing: wPlayerDirection reads "
                                    f"{self.emu.read_u8('wPlayerDirection'):#04x}")
        x, y = self.pos()[2:]
        dx, dy = STEP[mv]
        target = (x + dx, y + dy)
        kind = self.tile_at(*target)
        if kind != want_kind:
            return self._field_fail(
                f"wrong-tile: facing {mv} at {(x, y)} the cell {target} is "
                f"{kind!r}, not {want_kind!r}")
        log.info(f"[{move.lower()}] {knower} at {(x, y)} facing {mv} -> "
                 f"{target}")
        self.press("A:4 .:40")
        prompted = False
        for _ in range(14):
            rows = self.emu.screen_text()
            if Menus.has_label(rows, "YES"):
                prompted = True
                self.press("A:5 .:60")
            elif self.textbox():
                self.press("A:4 .:45")
            else:
                break
        self.settle()
        if self.pos()[2:] != (x, y):              # waterfall climbed
            log.info(f"  [{move.lower()}] moved {(x, y)} -> "
                     f"{self.pos()[2:]}")
            self.last_field_reason = "used"
            return True
        self.sync_grid()                          # whirlpool dissolved
        if self.tile_at(*target) != want_kind:
            log.info(f"  [{move.lower()}] {target} is now "
                     f"{self.tile_at(*target)!r}")
            self.last_field_reason = "used"
            return True
        if not prompted:
            return self._field_fail(
                f"no-prompt: A at {(x, y)} facing {mv} never asked to use "
                f"{move} (row 14: {self.emu.screen_text()[14].strip()!r})")
        return self._field_fail(
            f"unchanged: {move} was confirmed but {target} is still "
            f"{self.tile_at(*target)!r} and we are still at {(x, y)}")

    def waterfall(self, facing="U"):
        """Climb the waterfall above us (HM07). Waterfalls only go UP --
        CheckMapCanWaterfall requires FACE_UP."""
        return self.use_field_move("WATERFALL", facing)

    def whirlpool(self, facing=None):
        """Dissolve the whirlpool we are facing (HM06)."""
        return self.use_field_move("WHIRLPOOL", facing)


    def _wait_screen(self, pred, frames=500):
        """Tick (no input) until pred(uppercase screen text) is true."""
        n = 0
        while n < frames:
            if pred("".join(self.emu.screen_text()).upper()):
                return True
            self.emu.tick(10)
            n += 10
        return False

    def select_menu_row(self, label, max_presses=14, confirm=True,
                        match=None, confirm_seq="A:6 .:18"):
        """Text-targeted submenu/list selection (Menus.select_row_text):
        find the row whose text names `label` (or satisfies `match`),
        step the cursor exactly to it, verify after every press, then
        confirm. First-class because variable-layout submenus -- the
        party slot menu lists field moves ABOVE SWITCH -- and scrolled
        pack windows make positional press counts unsafe (wren pt6).
        The long default confirm press avoids the swallowed-A gotcha
        (START menu / pack, gotcha 2)."""
        fn = getattr(self.menu, "select_row_text", None)
        if fn is not None:
            return fn(label, max_presses=max_presses, confirm=confirm,
                      match=match, confirm_seq=confirm_seq)
        # duck-typed fakes / older Menus: best-effort select_label fallback
        legacy = getattr(self.menu, "select_label", None)
        if legacy is None:
            return False
        try:
            return legacy(label, max_presses=max_presses, confirm=confirm)
        except TypeError:
            try:
                return legacy(label, max_presses=max_presses)
            except TypeError:
                return legacy(label)

    def _pocket_select(self, idx, item_name, max_steps=40):
        """Steer the items-pocket cursor to absolute index `idx` and
        confirm with A. The pocket REMEMBERS its cursor between opens
        (pack.asm restores wItemsPocketCursor/wItemsPocketScrollPosition
        into the scrolling menu), so a fresh open can start mid-list:
        top-of-list screen scrapes miss and DOWN-only walks can never
        climb back up (leg-2 'no potion visible' with 2 in the bag).
        Navigate on the live WRAM index (wMenuScrollPosition +
        wMenuCursorY) in BOTH directions, then verify the highlighted
        row's TEXT really is the item before pressing A (wren pt6:
        select_menu_row -- _item_row_matches normalizes BOTH sides,
        case/space/hyphen/POKe blind, quantity-digit and edge-clip
        tolerant, and the column-band cursor pick ignores stale ▷/▶
        leftovers that shadowed 'SUPER POTION' in wren pt4)."""
        want = _norm_item(item_name)
        last, stuck = None, 0
        cur = None
        for _ in range(max_steps):
            cur = self.menu.scroll_abs()
            if cur == idx:
                break
            stuck = stuck + 1 if cur == last else 0
            if stuck >= 3:
                return self._menu_fail(
                    f"pocket_select({item_name}): cursor pinned at {cur} "
                    f"short of row {idx} -- list edge or wrong menu")
            last = cur
            self.press("D:6 .:4" if cur < idx else "U:6 .:4")
        else:
            return self._menu_fail(
                f"pocket_select({item_name}): stopped at {cur} after "
                f"{max_steps} steps, wanted row {idx}")
        self.press(".:10")      # let the row repaint before scraping
        # text-targeted verify + confirm: the helper re-checks the row
        # under the ACTIVE cursor and can correct a small WRAM/screen
        # disagreement by text -- but never blind-A's a mismatched row
        if getattr(self.menu, "select_row_text", None) is None and \
                hasattr(self.menu, "cursor_row"):
            # older Menus / duck-typed fakes: verify the highlighted row's
            # text directly (pre-pt6 algorithm), rescanning the visible
            # rows for the ACTIVE glyph when a stale leftover shadows it
            row = self.menu.cursor_row()
            texts = [row[1] if isinstance(row, tuple) else row]
            texts += [l for l in self.emu.screen_text() if "\u25b6" in l]
            if any(_item_row_matches(t.replace("\u25b6", " "), want)
                   for t in texts if t):
                self.press("A:6 .:18")
                return True
        elif self.select_menu_row(item_name, max_presses=4,
                                  match=lambda t: _item_row_matches(t, want)):
            return True
        # WRAM/screen disagree: never blind-A
        return self._menu_fail(
            f"pocket_select({item_name}): row mismatch (norm {want}), "
            f"cursor row {self.menu.cursor_row()!r}"
            + (f"; {self.menu.last_reason}"
               if getattr(self.menu, "last_reason", None) else ""))

    def _party_target(self, slot, max_steps=12):
        """Steer the field party menu to row `slot` (0-based; eggs count
        as rows) and confirm with A.

        Same discipline as battle.py's _party_row_select, and
        BIDIRECTIONAL for the same reason: InitPartyMenuWithCancel
        restores wPartyMenuCursor into wMenuCursorY
        (engine/pokemon/party_menu.asm:624), so a fresh open starts on
        whatever row was picked LAST -- a DOWN-only walk can never climb
        back to slot 0 -- and REVIVE's fainted-target flow opens on the
        first ABLE mon.

        Position is wMenuCursorY (1-based), the row PartyMenuSelect
        itself branches on. The party list is a 2D menu
        (PartyMenu2DMenuData through Load2DMenuData), so
        wMenuScrollPosition is NOT its position here -- it still holds
        the item pocket's scroll offset, which is why scroll_abs must
        never be used for this list."""
        # gotcha 2: the frame the list is drawn its input loop is not
        # running yet, so the first D/U (or a same-row A) is swallowed --
        # live evidence: a D press left wMenuCursorY unchanged at 1.
        self.press(".:16")
        last, stuck = None, 0
        cur = None
        for _ in range(max_steps):
            cur = self.emu.read_u8("wMenuCursorY") - 1
            if cur == slot:
                self.press("A:6 .:18")
                return True
            stuck = stuck + 1 if cur == last else 0
            if stuck >= 3:
                return self._menu_fail(
                    f"party_target({slot}): cursor pinned at row {cur} -- "
                    f"wrong menu or list edge")
            last = cur
            self.press("D:6 .:6" if cur < slot else "U:6 .:6")
        return self._menu_fail(f"party_target({slot}): stopped at row {cur} "
                               f"after {max_steps} steps")

    def _items_pocket_by_screen(self):
        """Fallback pack detection when goto_pocket's wJumptableIndex gate
        fails (wren pt6: field context can leave a non-pocket value there
        while the pack is plainly drawn). Steers by the drawn pocket
        banner: the pockets cycle ITEM <- BALL <- KEY <- TM on L, so at
        most 3 presses reach ITEM POCKET. A pack screen with an unreadable
        banner but visible 'x N' quantity rows counts as open --
        _pocket_select's row verification is the safety net for a wrong
        pocket. Returns True when the ITEMS pocket is (best-evidence) up."""
        for _ in range(4):
            rows = self.emu.screen_text()
            banner = _pack_pocket_banner(rows)
            if banner == "ITEM POCKET":
                log.info("  pack open on screen despite jumptable "
                         "mismatch; proceeding")
                return True
            if banner is None:
                if _pack_quantity_rows(rows):
                    log.info("  pack quantity rows on screen despite "
                             "jumptable mismatch; proceeding")
                    return True
                # nothing pack-like drawn: real miss
                return self._menu_fail(
                    "items_pocket: no pack banner and no quantity rows "
                    "on screen")
            self.press("L:4 .:12")  # cycle pockets toward ITEM POCKET
        return self._menu_fail(
            f"items_pocket: pocket banner still {banner!r} after 4 "
            f"L presses")

    def _start_menu_pack_row(self):
        """Get the START menu open with its PACK row drawn, and say so.
        Idempotent: a START menu left open by an earlier failure counts as
        already there (pressing START again would only close it)."""
        def _pack_row(s):
            return "PACK" in s
        if _pack_row("".join(self.emu.screen_text()).upper()):
            return True
        if self.menu_open():
            self.close_menus()      # a stray menu would eat the START press
        self.press("START:4 .:25")
        if self._wait_screen(_pack_row, 120):
            return True
        # Post-warp the START press sometimes lands during the fade;
        # blind D/A presses here WALK THE PLAYER (once onto a ladder).
        # Gotcha 2: the menu input loop isn't running the frame the menu
        # is drawn -- settle, drain stragglers, retry ONCE.
        log.info("  START menu slow to open; settling and retrying")
        self.settle()
        if self.textbox():
            self.flush_dialog()
        self.press("START:4 .:25")
        if self._wait_screen(_pack_row, 120):
            return True
        return self._menu_fail("start_menu: no PACK row drawn after two "
                               "START presses")

    # pack.asm jumptable states for the four pockets (goto_pocket's gate)
    _PACK_STATES = (2, 4, 6, 8)

    def _pack_up(self, rows=None):
        """Is the pack REALLY drawn? The jumptable's pocket state is the
        primary signal; field context can leave it stale, in which case
        the drawn pocket banner or the 'x N' quantity column proves it
        (wren pt6)."""
        if self.emu.read_u8("wJumptableIndex") in self._PACK_STATES:
            return True
        rows = self.emu.screen_text() if rows is None else rows
        return _pack_pocket_banner(rows) is not None or \
            bool(_pack_quantity_rows(rows))

    def _open_pack(self, max_confirms=3):
        """START -> PACK -> items pocket, with the pack open PROVED.

        This is the root cause of the pt10 field-item failures
        (`use_item` returning False with the bag untouched while the same
        items worked through the battle pack). Menus.select_label
        confirms a row with a 2-frame A and reports success from the
        CURSOR GLYPH alone -- it never looks at whether the pack opened.
        On the frames right after the START menu is drawn its input loop
        is not running yet (gotcha 2), so that A is swallowed on some
        frame parities and not others; live proof: two calls made from
        byte-identical savestates, one opened the pack and one left the
        START menu sitting there. goto_pocket then burned its whole
        budget on wJumptableIndex 128 (the START menu), the screen
        fallback saw no pocket banner and no quantity rows, and use_item
        returned False with NO log line at all -- leaving the START menu
        OPEN, which silently eats the caller's next input (gotcha 7), so
        the next call's START press merely closed it. That is the
        alternating success/failure the live log shows for identical
        calls.

        The fix is to retry the CONFIRM until the pack is verifiably up
        (jumptable pocket state, or the drawn pocket banner / quantity
        column when field context leaves the jumptable stale -- wren
        pt6). Re-pressing A on an already-open pack only re-opens the
        item submenu, which _pocket_select re-drives, so it is safe.

        The confirm now goes through select_label's `expect` gate, so the
        primitive's own answer means "the pack is up" -- and when it is
        not, the retry loop below is what recovers."""
        if not self._start_menu_pack_row():
            why = self.last_menu_reason or "START menu did not open"
            return self._menu_fail(f"open_pack: {why}")
        self.press(".:20")          # gotcha 2: let the input loop start
        if not self._confirm_label("PACK", self._pack_up, max_presses=8):
            reason = (getattr(self.menu, "last_reason", None)
                      or self.last_menu_reason or "PACK confirm unverified")
            if "state not reached" not in reason:
                return self._menu_fail(f"open_pack: {reason}")
            log.info(f"  {reason}; retrying the confirm")
        for _ in range(max_confirms):
            if goto_pocket(self.menu, "items") or \
                    self._items_pocket_by_screen():
                return True
            self.press("A:8 .:24")      # swallowed confirm: press again
        return self._menu_fail(
            f"open_pack: items pocket never came up in {max_confirms} "
            f"confirms")

    def _field_ui_clear(self):
        """Nothing modal is left on screen AND the pack's own jumptable is
        out of its pocket states (cancel_pack's gate, read directly so
        this needs no Menus)."""
        if self.emu.read_u8("wJumptableIndex") in self._PACK_STATES:
            return False
        return _field_clear(self.emu.screen_text())

    def _exit_field_ui(self, max_frames=1800):
        """B out of every field UI layer -- item message, party target
        list, pack, START menu -- until the overworld is interactive.

        Every use_item exit runs this. The old failure paths did
        `cancel_pack(); return False`, which is jumptable-gated and so
        left a stray START menu open on exactly the swallowed-A failure
        it was reporting; that menu then ate the caller's movement input
        (gotcha 7). B is also the safe key after a success: the item's
        "recovered NN HP!" prompt takes A or B, and an A drops straight
        back onto the target list where it would spend a SECOND item.

        Ends on a settling pause: without it the overworld has not
        re-latched input when the caller (or the next use_item) presses
        START, which is eaten and costs a whole retry cycle."""
        f0 = self.emu.frame
        while self.emu.frame - f0 < max_frames and not self._field_ui_clear():
            self.press("B:6 .:14")
        clear = self._field_ui_clear()
        self.press(".:30")
        return clear

    def _item_fail(self, reason, message, exit_ui=True):
        """The one exit for every use_item failure: log it, record the
        machine-readable reason on self.last_item_reason, and put the
        field back."""
        self.last_item_reason = reason
        log.info(f"  {message}")
        if exit_ui:
            self._exit_field_ui()
        return False

    def _party_slot(self, mon):
        """0-based party row of the member NICKNAMED `mon`, so callers
        stop hand-counting slots (and stop miscounting them after a
        party_swap). Comparison is case/space-blind; eggs are addressable
        because they occupy a row. Raises ValueError on an unknown name --
        silently healing the wrong mon is worse than stopping."""
        want = _norm_name(mon)
        party = game_state(self.emu, self.names)["party"]
        for slot, m in enumerate(party):
            if _norm_name(m.get("nickname") or "") == want:
                return slot
        raise ValueError(
            f"use_item: no party member named {mon!r} "
            f"(party: {[m.get('nickname') for m in party]})")

    def use_item(self, item_name, target_slot=_UNSET, field=True, *,
                 mon=None):
        """Use an item from the pack outside battle on party member
        `target_slot` (0-based) -- or on the member NICKNAMED `mon`,
        resolved against the live party. `target_slot` and `mon` are
        mutually exclusive (ValueError if both are given).

        True ONLY on a bag decrement: the menus can flow perfectly while
        a swallowed A used nothing. Every outcome also lands a
        machine-readable diagnosis on self.last_item_reason:

          'used'           the item was consumed
          'no-effect'      the ENGINE refused it (_ItemWontHaveEffectText:
                           full-HP unstatused target, POTION on a fainted
                           mon) -- a legitimate no-op that consumed
                           nothing, never a mechanical failure
          'not-in-bag' | 'no-pack' | 'pocket-miss' | 'no-use-option' |
          'target-miss' | 'not-consumed'   mechanical failures
        """
        if mon is not None:
            if target_slot is not _UNSET:
                raise ValueError(
                    "use_item: pass target_slot OR mon, not both")
            target_slot = self._party_slot(mon)
        elif target_slot is _UNSET:
            target_slot = 0
        e = self.emu
        self.last_item_reason = None
        # Which pocket holds it? Key items live in their own flat list and
        # used to be invisible here ('not-in-bag' for a SQUIRTBOTTLE that
        # observe() could see -- FUCK_I_MESSED_UP.md #23).
        pocket = "items"
        idx = bag_item_index(e, self.names, item_name, "items")
        if idx is None:
            idx, pocket = bag_item_index(e, self.names, item_name, "key"), "key"
        if idx is None:
            idx, pocket = (bag_item_index(e, self.names, item_name, "balls"),
                           "balls")
        if idx is None:
            return self._item_fail("not-in-bag", f"no {item_name} in bag",
                                   exit_ui=False)
        if not self._open_pack():
            return self._item_fail(
                "no-pack", f"could not open the pack for {item_name}")
        if pocket != "items" and not goto_pocket(self.menu, pocket):
            return self._item_fail(
                "pocket-miss", f"could not reach the {pocket} pocket for "
                f"{item_name}")
        before = (bag_quantity(e, self.names, item_name)
                  if pocket != "key" else 1)
        if not self._pocket_select(idx, item_name):
            return self._item_fail(
                "pocket-miss",
                f"could not put the pocket cursor on {item_name}")
        # item submenu (USE/GIVE/TOSS/QUIT) pops up after a beat
        if not self.menu.wait_for_label("USE", 300) or \
                not self.menu.select_label("USE", max_presses=4):
            return self._item_fail("no-use-option",
                                   f"no USE option for {item_name}")
        used, reason = self._confirm_field_item(item_name, target_slot,
                                                before)
        self._exit_field_ui()
        self.last_item_reason = reason
        if not used:
            log.info(f"  {item_name} not used on slot {target_slot}: "
                     f"{reason}")
        return used

    def _confirm_field_item(self, item_name, target_slot, before,
                            max_frames=4500, max_confirms=3):
        """Drive the pack's post-USE pages and report (used, reason).

        Healing/status items ask for a target party list; repels/ropes
        just run, and those are polled for consumption too. Two traps
        (wren pt3 REVIVE repro: returned False, bag never decremented,
        while a manual pack drive worked):
          * the target cursor does NOT start on row 0 -- see
            _party_target -- so blind press counts pick the wrong mon;
          * the revive jingle + "... came to!" message pace slowly over a
            party menu that keeps CANCEL drawn, so success gates on the
            bag read-back, never on the menu closing."""
        e = self.emu
        targeted = self.menu.wait_for(_party_target_list, timeout_frames=400)
        if targeted and not self._party_target(target_slot):
            return False, "target-miss"
        confirms = 0
        f0 = last_a = e.frame
        while e.frame - f0 < max_frames:
            after = bag_quantity(e, self.names, item_name)
            if after is None or (before is not None and after < before):
                return True, "used"
            rows = e.screen_text()
            if _no_effect_message(rows):
                # The engine's own refusal: nothing was consumed and
                # nothing will be. Stop here -- another A would drop back
                # onto the target list and spend the item on someone else.
                return False, "no-effect"
            if self.textbox():
                self.press("A:6 .:18")       # page the item message
            elif targeted and confirms < max_confirms and \
                    e.frame - last_a > 400 and _party_target_list(rows):
                # party menus swallow the confirm A during setup
                # (gotcha 2); an unchanged bag proves nothing was used
                # yet, so a re-press can't double-consume
                self.press("A:6 .:18")
                confirms += 1
                last_a = e.frame
            else:
                self.press(".:20")           # jingle: input is deaf
        return False, "not-consumed"

    def _heal_items(self):
        """{normalized name: curative properties} for this ROM, cached."""
        global _field_heal_table
        if _field_heal_table is None:
            _field_heal_table = _load_heal_table(paths.ROM, self.emu.sym,
                                                 self.names)
        return _field_heal_table

    def heal_party(self, items=None, max_items_per_mon=6):
        """Top every damaged/statused party member back up out of the bag,
        cheapest sufficient item first. Returns {mon label: outcome}:

            {'BROOK': 'FULL RESTORE', 'SNAG': 'already full',
             'REED': 'no item'}

        The outcome is the item (or ', '-joined items) actually consumed,
        'already full' for a mon that needed nothing, 'no item' when the
        bag holds nothing that would help, or use_item's failure reason.
        Healthy mons are never touched, and the run stops per mon as soon
        as the relevant items run out.

        `items`: optional whitelist of item names heal_party may spend.
        Heal amounts, cure masks and prices all come from the ROM's own
        tables (_load_heal_table), so 'cheapest sufficient' is the game's
        arithmetic, not a guess."""
        table = self._heal_items()
        allow = None if items is None else {_norm_item(n) for n in items}
        out = {}
        count = len(game_state(self.emu, self.names)["party"])
        for slot in range(count):
            spent = []
            label = None
            for _ in range(max_items_per_mon):
                mon = game_state(self.emu, self.names)["party"][slot]
                label = mon.get("nickname") or mon.get("name") or f"slot{slot}"
                if mon.get("egg"):
                    break
                status = self._status_byte(slot)
                need_hp = max(0, mon["max_hp"] - mon["hp"])
                if not need_hp and not status:
                    out[label] = ", ".join(spent) if spent else "already full"
                    break
                pick = cheapest_heal(table, self._bag(), allow, need_hp,
                                     status, mon["hp"] == 0)
                if pick is None:
                    out[label] = ", ".join(spent) + " (still hurt)" \
                        if spent else "no item"
                    break
                if not self.use_item(pick, target_slot=slot):
                    out[label] = ", ".join(spent + [
                        f"{pick}: {self.last_item_reason}"])
                    break
                spent.append(pick)
            else:
                out[label] = ", ".join(spent) + " (still hurt)"
        return out

    def _status_byte(self, slot):
        """Raw wPartyMon<slot>Status byte (the mask StatusHealingActions
        entries are tested against). game_state decodes it to names, but
        'which item cures this' needs the bits."""
        sym = self.emu.sym
        bank, base = sym["wPartyMon1"]
        base += slot * sym.offset("wPartyMon2", "wPartyMon1") + \
            sym.offset("wPartyMon1Status", "wPartyMon1")
        return self.emu.read((bank, base), 1)[0]

    def walk(self, path, label=""):
        """Walk a path like 'L*12 U*3 D'. Handles battles, NPC dialogs, and
        map transitions along the way; reports blocks instead of looping.

        Every False return leaves the reason on `last_step_reason`."""
        if label:
            log.info(f"[{label}] from {self.map_name()} {self.pos()[2:]}")
        self.last_step_reason = None
        with self._money_watch(f"walk '{path}'"):
            for token in path.split():
                d, _, n = token.partition("*")
                d, n = d[0].upper(), int(n or 1)
                done = stuck = 0
                while done < n:
                    r = self._step(d)
                    if r == "battle":
                        if not self._on_battle(f"walk '{path}'"):
                            self.last_step_reason = (
                                f"walk '{path}': battle handed to the "
                                f"caller at {self.map_name()} "
                                f"{self.pos()[2:]}")
                            return False
                        if self._whiteout_stop(f"walk '{path}'"):
                            self.last_step_reason = (
                                f"walk '{path}': whiteout during the walk")
                            return False
                    elif r == "moved":
                        done += 1
                        stuck = 0
                    else:
                        if self.textbox():
                            self.flush_dialog()
                            continue
                        stuck += 1
                        if stuck == 2:
                            # close a stray menu, then retry
                            self.press("B:4 .:10")
                        if stuck >= 4:
                            self.last_step_reason = (
                                f"walk '{path}': blocked stepping {d} at "
                                f"{self.map_name()} {self.pos()[2:]}"
                                f" (last step: {r})")
                            log.warning(f"  BLOCKED {d} at "
                                        f"{self.map_name()} {self.pos()[2:]}")
                            return False
        return True

    def _resolve_map(self, name):
        """CONST_NAME or CamelCase (case/space-insensitive) -> CONST_NAME;
        None = current map."""
        if name is None:
            return self.map_name()
        if name in self.nav.consts:
            return name
        want = name.upper().replace(" ", "_")
        if want in self.nav.consts:
            return want
        for const, camel in self.nav.camel.items():
            if camel.lower() == name.lower().replace(" ", ""):
                return const
        raise SystemExit(f"unknown map {name!r}")

    def _goto_fail(self, reason, strict, where=""):
        """Loud goto failure (wren pt6: 'goto silently no-ops on
        unreachable targets'): record the machine-checkable reason on
        d.last_goto_reason, log the GAVE UP, and either return False
        or -- strict=True -- raise TravelError so callers that never
        check the return value stop instead of drifting."""
        self.last_goto_reason = reason
        log.warning(f"  GAVE UP ({reason})"
                    f"{' at ' + where if where else ''}")
        if strict:
            raise TravelError(f"goto: {reason}")
        return False

    def _goto_walk(self, x, y, label="", map_name=None, strict=False):
        """One walking attempt at (x,y): plan, walk, replan around NPC
        bumps, fight encounters on the way.

        This is `goto` minus the savestate escalation; every failure sets
        d.last_goto_reason ('outside-bounds: ...' / 'unreachable: ...' /
        'target-occupied: ...' / the give-up diagnoses). Callers other
        than `goto` should not use it -- goto is what decides whether a
        failure is worth escalating."""
        self._refresh_nav_blocks()
        goal_map = self._resolve_map(map_name)
        goal = (x, y)
        # An exit-warp cell of the CURRENT map as the goal (map not
        # requested) means "walk out through this door": hold onto the
        # tile, and success = having left the map. Escalating to cross-map
        # routing here just bounces in and out forever.
        exit_warp_goal = (map_name is None and goal_map == self.map_name()
                          and self._is_warp_cell(x, y))
        if goal_map == self.map_name():
            grid = self.nav.grid(goal_map)
            if not (0 <= x < len(grid[0]) and 0 <= y < len(grid)):
                return self._goto_fail(
                    f"outside-bounds: target ({x},{y}) outside {goal_map} "
                    f"bounds {len(grid[0])}x{len(grid)} -- pass map_name "
                    f"or use travel for cross-map goals", strict)
        entry_map = self.map_name()
        replans = idle = passes = drains = occupied = 0
        edge_counts = {}    # (from_map, to_map): crossings this one call
        last_block = ""     # diagnosis text from the most recent blocked step
        reason = "unspecified"
        self.last_goto_reason = None
        if label or goal_map != self.map_name():
            log.info(f"[goto {goal}"
                  f"{'' if goal_map == self.map_name() else ' -> ' + goal_map}]"
                  f"{' ' + label if label else ''}".rstrip())
        while replans < 20 and idle < 40 and passes < 60:
            passes += 1
            cur_map, cur = self.map_name(), self.pos()[2:]
            if exit_warp_goal:
                if cur_map != entry_map:
                    log.info(f"  -> left through warp {goal}")
                    return True
            elif cur_map == goal_map and cur == goal:
                return True
            # a warp-tile goal fires the instant it is stepped on, so you
            # can never STAND on it when approaching from outside -- but
            # arrival never re-triggers, so coming out of goal_map's own
            # exit leaves you standing there. Accept proximity ONLY while
            # inside goal_map: requiring land[0] == cur_map instead once
            # blessed a PC-interior goal as "arrived" while we stood
            # outside on the street, having never walked in (silent
            # objective skip -- the straight-through killer).
            land = (self.nav.warps.get(goal_map, {}).get(goal)
                    and self.nav._warp_landing(goal_map, goal))
            if land and cur_map == goal_map and \
                    abs(cur[0] - land[1][0]) + abs(cur[1] - land[1][1]) <= 2:
                log.info(f"  -> arrived through warp {goal}")
                return True
            # NPCs scope to the replan's start map inside _bfs, so always
            # thread around them -- cross-map legs hit NPCs just the same
            avoid = self.npc_cells()
            if goal_map == cur_map:
                # Same-map goal: stay on this map. Routing through warps
                # here just bounce-exits (e.g. standing north of Union
                # Cave's entrance carpet, every "shortcut" leaves the map).
                path = self.nav.find_path(cur_map, cur, goal, avoid)
                if not path:
                    # distinguish "NPC in the way" from "statically
                    # unreachable": a relaxed (ignore-NPC) route means
                    # some sprite squats a cell we must step through.
                    path = self.nav.find_path(cur_map, cur, goal)
                    if not path:
                        return self._goto_fail(
                            f"unreachable: no path from {cur} to {goal} "
                            f"on {cur_map}", strict, f"{cur_map} {cur}")
                    # which cells on the relaxed walk are squatted?
                    cx, cy = cur
                    squatted = []
                    for mv in path:
                        dx, dy = STEP[mv]
                        cx, cy = cx + dx, cy + dy
                        if (cx, cy) in avoid:
                            squatted.append((cx, cy))
                    waited_out = False
                    for cell in squatted:
                        kind = self._blocker_kind(cell)
                        if kind == "stationary":
                            return self._goto_fail(
                                f"blocked-by-stationary-npc: {cell} on "
                                f"{cur_map} severs the only path to "
                                f"{goal} -- talk_to/face it, or route "
                                f"around", strict, f"{cur_map} {cur}")
                        if kind == "wanderer":
                            if self._wait_out_wanderer(cell):
                                waited_out = True
                                break
                            return self._goto_fail(
                                f"waited-for-wanderer: still blocked at "
                                f"{cell} on {cur_map} after "
                                f"{self.WANDER_WAIT_FRAMES}f", strict,
                                f"{cur_map} {cur}")
                    if waited_out:
                        continue        # replan against fresh sprites
                    if goal in avoid:
                        # unclassifiable blocker parked on the goal cell:
                        # walking there can only bump. Legacy diagnosis.
                        occupied += 1
                        if occupied >= 3:
                            return self._goto_fail(
                                f"target-occupied: NPC standing on {goal} "
                                f"({cur_map}) -- talk_to/face it from an "
                                f"adjacent cell instead", strict,
                                f"{cur_map} {cur}")
                    replans += 1
                    if replans % 5 == 1:
                        log.info(f"  threading {cur} -> {goal} past NPCs",
                              )
            else:
                path = self.nav.find_route(cur_map, cur, goal_map, goal,
                                           avoid)
                if not path:
                    relaxed = self.nav.find_route(cur_map, cur, goal_map,
                                                  goal)
                    if not relaxed:
                        return self._goto_fail(
                            f"unreachable: no route {cur_map} {cur} -> "
                            f"{goal_map} {goal}", strict,
                            f"{cur_map} {cur}")
                    replans += 1
                    if replans % 5 == 0:
                        log.info(f"  threading {cur} -> {goal} past NPCs",
                              )
                    path = relaxed
            moved = False
            for mv in path:
                r = self._step(mv)
                if r == "battle":
                    if not self._on_battle(f"goto {goal_map} {goal}"):
                        return False
                    if self._whiteout_stop(f"goto {goal_map} {goal}"):
                        self.last_goto_reason = "whiteout-abort"
                        return False
                    moved = True
                elif r == "warp":
                    here = self.map_name()
                    self.settle()
                    log.info(f"  -> {here} {self.pos()[2:]}")
                    key = (cur_map, here)
                    edge_counts[key] = edge_counts.get(key, 0) + 1
                    if edge_counts[key] > 2:
                        raise TravelError(
                            f"goto {goal_map} {goal}: map seam {key[0]} -> "
                            f"{key[1]} crossed {edge_counts[key]}x in one "
                            f"call -- ping-pong cycle, bailing; anchor at a "
                            f"known waypoint and relaunch")
                    moved = True
                    # step_hold keeps the key down through the transition,
                    # so the player glides past the modeled landing cell;
                    # replan from the live position rather than trust the
                    # rest of the precomputed path
                    break
                elif r == "moved":
                    moved = True
                elif r == "blocked":
                    # diagnose, don't just report: a blocked step on a
                    # grid-walkable cell is almost always a stray menu
                    # (gotcha 7), a textbox, or an NPC on the target cell
                    bx, by = self.pos()[2:]
                    dx, dy = STEP[mv]
                    if self.textbox():
                        cause = " [textbox]"
                    elif self.menu_open():
                        cause = " [stray menu -- closing]"
                    elif (bx + dx, by + dy) in self.npc_cells():
                        cause = " [npc on target cell]"
                    else:
                        cause = ""
                    last_block = (cause.strip(" []")
                                  or "unexplained blocked step")
                    log.info(f"  blocked {mv} at {self.map_name()} "
                          f"{(bx, by)}{cause}")
                    if self.textbox():
                        # a scripted scene (Elm's call, the rival ambush)
                        # re-raises its textbox faster than flush_dialog's
                        # quiet window -- replanning against it storms to
                        # GAVE UP. Page the scene out (bounded, to
                        # wScriptMode==0) and replan without charging a
                        # storm strike.
                        dr = "timeout"
                        if drains < 3:
                            drains += 1
                            dr = self._drain_scene()
                        if dr == "menu":
                            # a choice opened mid-scene: mashing would
                            # pick something (gotcha 13) -- surface it
                            # WITH its labels so the decider can answer
                            # deliberately in one call
                            self.last_choice_options = \
                                self._choice_labels(self.emu.screen_text())
                            reason = (f"blocked by choice menu "
                                      f"{self.last_choice_options} -- "
                                      f"resolve_choice('YES') if answering "
                                      f"is safe (gotcha 13)")
                            self.last_goto_reason = reason
                            log.warning(f"  GAVE UP ({reason}) at "
                                  f"{self.map_name()} {self.pos()[2:]}")
                            return False
                        if dr in ("done", "battle"):
                            # drained (or a battle started: the next
                            # pass's _step returns 'battle' and the
                            # existing fight path takes it) -- retry via
                            # a fresh plan, uncounted
                            break
                        self.flush_dialog()
                    elif self.menu_open():
                        self.close_menus()
                    else:
                        self.press(".:40")  # let a wandering NPC step aside
                    replans += 1
                    break
            else:
                continue   # path exhausted; loop re-checks arrival/replans
            if not moved:
                idle += 1
        if idle >= 40:
            reason = f"no-progress ({idle} idle passes)"
            try:
                if self.emu.read_u8("wScriptMode"):
                    reason += "; script-scene-active"
            except Exception:
                pass
        elif replans >= 20:
            reason = f"replan-storm ({replans} replans)"
        elif passes >= 60:
            reason = "pass-cap"
        if last_block:
            reason += f"; last-block={last_block}"
        if "script-scene-active" in reason:
            reason += ("; if crossing the scene cell is talk-only-safe, "
                       "set d.trip_scenes=True for this one goto")
        return self._goto_fail(
            reason, strict,
            f"{self.map_name()} {self.pos()[2:]} -> {goal_map} {goal}")

    # Escalation budget for goto's savestate search. Deliberately about a
    # third of reach()'s 200/140: one explore_bfs node costs a savestate
    # restore plus a settle of up to 1200 frames -- roughly a thousand
    # times a goto pass -- so this is a last resort, not a default.
    GOTO_ESCALATE_MOVES = 60
    GOTO_ESCALATE_NODES = 40
    # Failure classes where the decoded MAP is the suspect, so a
    # savestate search of the real geometry can still get there. The
    # Indigo Plateau Pokecenter renders (3,8) as wall while the avatar
    # walks it; that is a static-data lie, not a blocked route.
    GOTO_ESCALATE_ON = ("no-path", "unreachable", "replan-storm",
                        "no-progress", "pass-cap", "outside-bounds")
    # ... and classes where it CANNOT: a live actor, a running scene or an
    # open menu is not geometry, and burning minutes of savestate search
    # on one is worse than reporting it.
    GOTO_NO_ESCALATE_ON = ("npc", "target-occupied", "script-scene-active",
                           "choice menu", "whiteout", "manual",
                           "waited-for-wanderer")
    # Interactive handoffs: the caller (a model) is supposed to take over,
    # so these return False even under strict -- raising would stop the
    # very decision loop that can answer them.
    GOTO_HANDOFF = ("manual", "choice menu", "whiteout")
    _escalating = False

    def _should_escalate(self, reason):
        """Is this goto failure the kind a savestate search can fix?"""
        if not reason:
            return False
        low = reason.lower()
        if any(k in low for k in self.GOTO_NO_ESCALATE_ON):
            return False
        return any(k in low for k in self.GOTO_ESCALATE_ON)

    def goto(self, x, y, label="", map_name=None, strict=False,
             escalate=True):
        """BFS-pathfind to (x,y) and walk it. Defaults to the current map;
        pass map_name (CONST_NAME or CamelCase) to route across maps via
        warp events and edge connections. Replans around NPC bumps; fights
        encounters on the way.

        When the walk fails because the decoded MAP is wrong -- no path,
        replan storm, no progress -- goto escalates by itself to the
        savestate search (explore_bfs), which walks the real geometry
        instead of the parsed grid. That is what `reach` used to be for,
        and nothing called it: the Indigo Plateau Pokecenter renders (3,8)
        as wall while the avatar stands on it, so 20 replans burned and
        the leg was hand-driven with step_hold. Failures that a search
        cannot fix (an NPC in the way, a live scene, a choice menu, a
        whiteout) are reported immediately instead.

        `escalate`: True for the default budget, False to refuse, or a
        (max_moves, max_nodes) tuple to raise it (what reach does).

        Failure is loud, never silent: every False return sets
        d.last_goto_reason first. strict=True upgrades navigation
        failures to TravelError; interactive handoffs (manual battle,
        choice menu, whiteout recovery) still return False under strict
        so the decider can take over."""
        with self._money_watch(f"goto {(x, y)}"):
            if self._goto_walk(x, y, label, map_name, strict=False):
                return True
            reason = self.last_goto_reason
            if escalate and not self._escalating and \
                    self._should_escalate(reason):
                if isinstance(escalate, (tuple, list)):
                    moves, nodes = escalate
                else:
                    moves, nodes = (self.GOTO_ESCALATE_MOVES,
                                    self.GOTO_ESCALATE_NODES)
                goal, goal_map = (x, y), self._resolve_map(map_name)
                log.warning(f"  goto ({reason}) -- escalating to a "
                            f"savestate search ({moves} moves, {nodes} "
                            f"nodes): the decoded grid may be wrong")
                self._escalating = True
                try:
                    found = self.explore_bfs(
                        lambda dr: dr.pos()[2:] == goal
                        and dr.map_name() == goal_map,
                        max_moves=moves, max_nodes=nodes,
                        on_battle="fight")
                except Exception as exc:
                    # The search needs savestates; where they are not
                    # available the WALK's failure is still the answer.
                    log.warning(f"  savestate search unavailable "
                                f"({type(exc).__name__}: {exc})")
                    found = None
                finally:
                    self._escalating = False
                if (found or {}).get("found"):
                    self.last_goto_reason = None
                    log.info(f"  -> reached {goal} via savestate search")
                    return True
                reason = f"{reason}; search exhausted ({nodes} nodes)"
                self.last_goto_reason = reason
            if strict and reason and not any(
                    k in reason.lower() for k in self.GOTO_HANDOFF):
                raise TravelError(f"goto: {reason}")
            return False

    # Deliberate-trip opt-in (FABLE_FEEDBACK failure pattern 5): after
    # confirming from maps/<Map>.asm that a scene script is safe
    # (talk-only, sets scene NOOP), set d.trip_scenes = True for the one
    # goto that must cross its cell, then clear it. Never leave it on.
    trip_scenes = False

    # machine-checkable diagnosis of the most recent goto failure; None
    # until a goto has run (class default so fresh/old drivers both read)
    last_goto_reason = None

    def _scene_spent(self, camel, script):
        """True when the coord_event's OWN leading guard chain proves it
        does nothing right now.

        This is what makes a scene block expire. `nav.blocked` is
        recomputed from the map source on every goto, so a cell whose
        scene token still matches came back forever -- at
        INDIGO_PLATEAU_POKECENTER_1F the map declares exactly one scene
        (id 0 = RIVAL_BATTLE) and its post-battle script sets the scene
        to that same id, so (16,4)/(17,4) -- the only corridor to the
        League door -- were re-severed after every single failed goto and
        had to be cleared by hand three times (session claude pt12).

        The script says so itself:
            checkevent EVENT_BEAT_RIVAL_IN_MT_MOON / iffalse ...Done
            checkflag ENGINE_INDIGO_PLATEAU_RIVAL_FIGHT / iftrue ...Done
        Evaluate those live: if a guard jumps to a label that is not
        disruptive, walking the cell cannot push us anywhere. Flags that
        cannot be resolved are left alone (assume the worst)."""
        for check, name, jump, target in script_guards(self.nav._repo,
                                                       camel, script):
            try:
                val = (self._event_flag(name) if check == "checkevent"
                       else self.engine_flag(name))
            except Exception:
                continue                   # unknown flag: assume armed
            if val is not (jump == "iftrue"):
                continue                   # this guard falls through
            if not script_is_disruptive(self.nav._repo, camel, target):
                return True
        return False

    def _refresh_nav_blocks(self):
        """Mark every coord_event cell that would fire RIGHT NOW unwalkable
        for planning: its scene token matches the map's live scene id (or
        is SCENE_ALWAYS/-1), or the scene state can't be read (assume the
        worst). Scenes only ever move forward via setscene, so this keeps
        e.g. Route 32's eternally re-firing Cooltrainer cutscene out of
        BFS. Cheap: parses are process-cached, one WRAM byte per map."""
        events = coord_events(self.nav._repo)
        consts = scene_consts(self.nav._repo)
        syms = scene_vars(self.nav._repo)
        blocks = {}
        for const, evs in events.items():
            sym = syms.get(const)
            cur = None                     # None = no persistent scene id
            if sym:
                try:
                    cur = self.emu.read(self.emu.sym[sym], 1)[0]
                except Exception:
                    cur = "unreadable"
            order = {n: i for i, n in enumerate(consts.get(const, [])) if n}
            cells = set()
            camel = self.nav.camel.get(const, const)
            for x, y, tok, script in evs:
                v = -1 if tok.startswith("-") else order.get(tok)
                if v == -1:
                    fires = True           # SCENE_ALWAYS: fires every time
                elif v is None:
                    fires = True           # unknown scene: assume the worst
                elif cur is None:
                    fires = False          # no scene var -> engine sees -1
                elif cur == "unreadable":
                    fires = True           # can't tell -> assume the worst
                else:
                    fires = cur == v
                if not fires:
                    continue
                if not script_is_disruptive(self.nav._repo, camel, script):
                    continue
                if self._scene_spent(camel, script):
                    continue               # guard chain already answered
                if self.trip_scenes:
                    log.info(f"  [trip_scenes] crossing {const} scene cell "
                          f"{(x, y)} unblocked")
                    continue
                cells.add((x, y))
            if cells:
                blocks[const] = cells
        self.nav.blocked = blocks

    def blocked_cells(self, map_name=None):
        """The coord_event cells nav currently refuses to plan through,
        ``{map: {(x, y), ...}}`` (or one map's set). Refreshed from live
        scene/flag state first, so this is what BFS will actually see --
        the answer to "why is there no path" when the grid looks open."""
        self._refresh_nav_blocks()
        if map_name is None:
            return {m: set(c) for m, c in self.nav.blocked.items()}
        return set(self.nav.blocked.get(self._resolve_map(map_name), ()))

    def _mg_edges(self):
        """{from_map_const: [routable edges]} over data/mapgraph.json."""
        adj = {}
        for e in mapgraph()["edges"]:
            if e.get("routable"):
                adj.setdefault(e["from_map"], []).append(e)
        return adj

    def _edge_steps(self, e):
        """All ways to walk edge `e`: [((ax, ay), dir_letter), ...] sorted
        closest-first, where standing on (ax, ay) and stepping `dir` fires
        the warp/connection. Validated against this repo's collision grids;
        an edge the terrain doesn't allow yields []. None for maps with no
        grid. Multiple candidates matter: the same door can have a walkable
        approach from one side only (Union Cave's door is entered stepping
        UP off the ledge lip below it -- its north cell is walled off)."""
        try:
            grid = self.nav.grid(e["from_map"])
        except KeyError:
            return None
        hgt, wid = len(grid), len(grid[0])

        def standable(x, y):
            return (0 <= x < wid and 0 <= y < hgt
                    and (grid[y][x] in WALKABLE or grid[y][x] in HOPS)
                    and grid[y][x] not in WARPS)

        if e["kind"] == "warp":
            tx, ty = e["cells"]
            cands = [((tx - dx, ty - dy), d)
                     for d, (dx, dy) in STEP.items()
                     if standable(tx - dx, ty - dy)]
        elif e["kind"] == "connection":
            d = _CONN_LETTER[e["entry"]["heading"]]
            dx, dy = STEP[d]
            (x1, y1), (x2, y2) = e["cells"]
            cands = []
            for x in range(min(x1, x2), max(x1, x2) + 1):
                for y in range(min(y1, y2), max(y1, y2) + 1):
                    # stand ON the border band; the step in `d` leaves the
                    # map and fires the connection
                    nx, ny = x + dx, y + dy
                    if standable(x, y) and not (0 <= nx < wid
                                                and 0 <= ny < hgt):
                        cands.append(((x, y), d))
        else:
            return None
        if not cands:
            return []
        px, py = self.pos()[2:]
        cands.sort(key=lambda c: (abs(c[0][0] - px) + abs(c[0][1] - py),
                                  c[0]))
        return cands

    def _regions(self, m, x, y):
        """nav.regions_at with the planner's wildcard convention: (-1,)
        for maps with no grid (regions unknowable there) or cells sealed
        all around -- region -1 matches anything."""
        try:
            r = self.nav.regions_at(m, x, y)
        except KeyError:
            return (-1,)
        return r or (-1,)

    # Dijkstra edge costs: walking inside a map is ~1 unit/cell, every
    # map transition is a flat beat (door hold + settle + drift risk).
    # Hop-count BFS treated a 20-map detour ring (Azalea -> Route 33 ->
    # Union Cave -> ... -> Goldenrod -> Route 34 -> Ilex) as EQUAL to the
    # 2-hop direct route -- it then got EXECUTED whenever the direct
    # approach was unavailable. That ring is the user-reported Ilex loop
    # and Fable's Route-26 reversal, both burned tens of thousands of
    # frames before anyone looked.
    TRANSITION_COST = 60
    DEFAULT_MAX_COST = 700   # rejects Johto-ring plans (~1500+) while
                            # allowing legitimate multi-town routes

    def route(self, dest_map, max_cost=None):
        """Plan-only cross-map route to `dest_map`: Dijkstra over
        mapgraph.json's validated warp/connection edges with walk-distance
        costs, expanded into per-leg steps --
        [{"kind": "walk", "map", "x", "y"}, {"kind": "warp"|"connection",
        "from", "to", "dir", ...}, ...]. Nodes are (map, region) over
        nav.region_map components, gated by each edge's from_regions /
        to_regions (absent field = wildcard, for grid-less maps): a warp
        on a walled-off part of a map -- Sprout Tower 2F's (10,14) stairs
        seen from the east arrival area -- is never planned; the real
        route detours over the 1F walkway. The entry region comes from
        the CURRENT standing cell (self.pos()). Raises LookupError when
        nothing routes, or when the cheapest plan exceeds max_cost (a
        "detour ring" -- almost certainly not what a straight-through run
        wants; anchor closer or raise max_cost deliberately). Never moves
        the player."""
        self._refresh_nav_blocks()
        dest = self._resolve_map(dest_map)
        src = self.map_name()
        if max_cost is None:
            max_cost = self.DEFAULT_MAX_COST
        if dest == src:
            return []
        adj = self._mg_edges()
        px, py = self.pos()[2:]
        best, entry, prev, heap = {}, {}, {}, []
        for r in self._regions(src, px, py):
            node = (src, r)             # region -1 = wildcard
            best[node] = 0
            entry[node] = (px, py)    # where we'd start walking there
            prev[node] = None
            heap.append((0, node))
        heapq.heapify(heap)
        seen = set()
        goal = None
        while heap:
            cost, node = heapq.heappop(heap)
            if node in seen:
                continue
            seen.add(node)
            m, reg = node
            if m == dest:
                goal = node       # any region of the destination map
                break
            ex, ey = entry[node]
            # scene seals are plan-truth too: an edge whose only
            # approach cells sit behind an armed coord_event is not
            # routable RIGHT NOW, even though the collision grid says
            # otherwise (Route 32 descent, Azalea neck approaches)
            sealed = self.nav.blocked.get(m, ())
            for e in sorted(adj.get(m, ()),
                            key=lambda e: (e["to_map"], e["kind"],
                                           json.dumps(e["cells"]))):
                frm = e.get("from_regions")
                if frm is not None and reg >= 0 and reg not in frm:
                    continue    # warp sits on a walled-off part of m
                cands = [c for c in self._edge_steps(e)
                         if c[0] not in sealed
                         and (reg < 0
                              or reg in self._regions(m, *c[0]))]
                if not cands:
                    continue
                walk = min(abs(ax - ex) + abs(ay - ey)
                           for (ax, ay), _ in cands)
                ncost = cost + walk + self.TRANSITION_COST
                (ax, ay), d = min(cands, key=lambda c: (
                    abs(c[0][0] - ex) + abs(c[0][1] - ey), c[0]))
                land = (tuple(e["dest_cell"]) if e["kind"] == "warp"
                        else None)
                if land is None:
                    # connection landing depends on the departure cell;
                    # approximate with the far edge cell toward travel
                    grid = self.nav.grid(e["to_map"])
                    land = (len(grid[0]) - 1 if d == "L" else 0, ey)
                for nr in (e.get("to_regions") or (-1,)):
                    nxt = (e["to_map"], nr)
                    if nxt in seen:
                        continue
                    if nxt in best and best[nxt] <= ncost:
                        continue
                    best[nxt] = ncost
                    prev[nxt] = (node, e, (ax, ay), d, cands)
                    entry[nxt] = land
                    heapq.heappush(heap, (ncost, nxt))
        if goal is None:
            raise LookupError(f"no routable mapgraph path {src} -> {dest}")
        if best[goal] > max_cost:
            raise LookupError(
                f"cheapest {src} -> {dest} plan costs {best[goal]} "
                f"(> max {max_cost}) -- detour ring; anchor at a nearer "
                f"waypoint or pass a deliberate max_cost")
        hops, node = [], goal
        while prev[node]:
            pnode, e, a, d, cands = prev[node]
            hops.append((pnode[0], e, a, d, cands))
            node = pnode
        steps = []
        for frm, e, (ax, ay), d, cands in reversed(hops):
            steps.append({"kind": "walk", "map": frm, "x": ax, "y": ay,
                          "why": f"approach {e['kind']} to {e['to_map']}"})
            trans = {"kind": e["kind"], "from": frm, "to": e["to_map"],
                     "dir": d, "notes": e.get("notes"),
                     "approaches": [{"x": a[0][0], "y": a[0][1], "dir": a[1]}
                                    for a in cands]}
            if e["kind"] == "warp":
                trans["cell"] = list(e["cells"])
                trans["warp_id"] = e.get("warp_id")
                trans["dest"] = list(e["dest_cell"])
            else:
                trans["band"] = [list(c) for c in e["cells"]]
                trans["offset"] = e.get("offset")
            steps.append(trans)
        return validate_route(steps)

    def _landing(self, st, x, y):
        """Modeled landing cell for transition step `st` taken at (x, y)."""
        if st["kind"] == "warp":
            return tuple(st["dest"])
        grid = self.nav.grid(st["to"])
        return _CONN_LAND[st["dir"]](len(grid[0]), len(grid),
                                     st["offset"] or 0, x, y)

    def reach(self, x, y, label="", budget=200, nodes=140):
        """Walk to (x, y) on THIS map with a BIGGER savestate-search
        budget than goto's default.

        Victory Road (and the Rocket base, and Ice Path) have floors whose
        decoded grid disagrees with the live map: the walk reports
        'unexplained blocked step' / 'unreachable' for cells the avatar
        can plainly walk to. That escalation now lives in `goto` itself,
        so this is just goto with the budget raised -- the walk is still
        tried first, and only its failure pays for a search.
        Returns True when standing on (x, y)."""
        if self.pos()[2:] == (x, y):
            return True
        try:
            return self.goto(x, y, label, escalate=(budget, nodes))
        except TravelError:
            return self.pos()[2:] == (x, y)

    # How far along a map edge to search for the row/column that actually
    # crosses. Live misses were off by exactly one, but ledges and fences
    # can push the real band several cells away.
    EDGE_SLIDE = 6

    def _slide_edge(self, st, dest=""):
        """Cross a map-edge connection whose planned row/column does not
        fire, by sliding ALONG the edge and re-trying the held step.

        `travel` used to fail the whole leg here: Azalea Town's east edge
        crosses at y=14 while the plan said y=13, and Route 32 -> Violet
        at x=8. A hand-written `cross()` helper doing exactly this slide
        was what got a live session through both, so it belongs inside
        travel. Returns True when the map changed."""
        d = st["dir"]
        start_map = self.map_name()
        # slide perpendicular to the crossing direction: out one way,
        # back to the start, then out the other. Alternating U/D on the
        # spot just oscillates around the row that does not work.
        pairs = (("U", "D"), ("D", "U")) if d in ("L", "R") \
            else (("L", "R"), ("R", "L"))
        for mv, back in pairs:
            moved = 0
            for _ in range(self.EDGE_SLIDE):
                if self._step(mv) != "moved":
                    break
                moved += 1
                r = self.step_hold(d)
                if r == "battle":
                    if not self._on_battle(f"travel -> {dest}"):
                        return False
                if self.map_name() != start_map:
                    log.info(f"  edge slide: crossed {d} after {moved} "
                             f"{mv}-step(s) -- the planned row did not fire")
                    return True
            for _ in range(moved):        # back to the planned row
                if self._step(back) != "moved":
                    break

        return False

    def travel(self, dest_map, label=""):
        """Execute route(<dest_map>) leg by leg with the existing walk/
        _step/settle mechanics: goto each approach cell, hold through warps
        (_step picks step_hold on warp tiles -- the Route 31 gate only
        fires with the key held sideways), settle() after every transition,
        then verify landing map + cell. Small drift past the modeled
        landing is expected (held key glides ~2 cells; AGENTS.md gotcha
        14); anything worse raises TravelError. If an edge's approach cell
        is unreachable from our side (one-way ledges/walls), falls back to
        that edge's other approaches. A tolerated glide that lands across
        a region seam (door tile touching two rooms) replans the remainder
        from the live cell -- route() rereads pos() for its entry region."""
        dest = self._resolve_map(dest_map)
        self._refresh_nav_blocks()
        if self.map_name() == dest:
            return []
        steps = self.route(dest)
        log.info(f"[travel -> {dest}] {len(steps)} steps from "
              f"{self.map_name()} {self.pos()[2:]}"
              f"{' ' + label if label else ''}".rstrip())
        _edge_counts = {}
        i = 0
        while i < len(steps):
            st = steps[i]
            cur = self.map_name()
            if st["kind"] == "walk":
                if cur != st["map"]:
                    raise TravelError(f"leg {i}: plan expects "
                                      f"{st['map']}, we're on {cur}")
                nxt = steps[i + 1] if i + 1 < len(steps) else None
                alts = (nxt.get("approaches") if nxt else None) or []
                if self.goto(st["x"], st["y"], f"travel -> {dest}"):
                    for alt in alts:
                        if [alt["x"], alt["y"]] == [st["x"], st["y"]]:
                            nxt["dir"] = alt["dir"]
                    i += 1
                    continue
                # this approach may sit on the far side of a one-way ledge
                # or wall -- fall back to the edge's other approaches
                for alt in alts:
                    if [alt["x"], alt["y"]] == [st["x"], st["y"]]:
                        continue
                    log.info(f"  approach {(st['x'], st['y'])} unreachable; "
                          f"trying {alt['dir']} from "
                          f"{(alt['x'], alt['y'])}")
                    if self.goto(alt["x"], alt["y"], f"travel -> {dest}"):
                        nxt["dir"] = alt["dir"]
                        break
                else:
                    raise TravelError(
                        f"leg {i}: no path to any approach of the next "
                        f"{nxt['kind'] if nxt else 'transition'} on {cur} "
                        f"(last goto: {self.last_goto_reason})")
                i += 1
                continue
            key = json.dumps([st["kind"], st["from"], st["to"], st["dir"],
                              st.get("cell") or st.get("band")], sort_keys=True)
            edge_count = _edge_counts.get(key, 0) + 1
            _edge_counts[key] = edge_count
            if edge_count > 2:
                raise TravelError(
                    f"leg {i}: transition {st['from']} -> {st['to']} via "
                    f"{st['dir']} executed {edge_count}x this travel() -- "
                    f"bailing out instead of ping-ponging")
            if cur != st["from"]:
                raise TravelError(f"leg {i}: plan transitions from "
                                  f"{st['from']}, we're on {cur}")
            px, py = self.pos()[2:]
            expected = self._landing(st, px, py)
            r = None
            # Already standing ON the warp tile: a warp only fires on the
            # step that enters it, so stepping `dir` from here just walks
            # away. take_warp steps off and back on.
            if st["kind"] == "warp" and \
                    tuple(st.get("cell") or ()) == (px, py):
                if self.take_warp(px, py, f"travel -> {dest}"):
                    r = "warp"
                elif self.map_name() == st["from"]:
                    raise TravelError(
                        f"leg {i}: standing on warp {(px, py)} and could "
                        f"not enter it ({self.last_warp_reason})")
            if r is None:
                for _attempt in range(4):
                    r = self._step(st["dir"])
                    if r == "battle":
                        # encounter mid-transition; then retry
                        if not self._on_battle("travel"):
                            raise TravelError(
                                f"leg {i}: battle mid-travel with "
                                f"auto_fight=manual -- decide it "
                                f"(fight()/catch()), then relaunch travel()")
                        if self._whiteout_stop("travel"):
                            raise TravelError(
                                f"leg {i}: wiped mid-travel, auto-healed at "
                                f"{self.map_name()} -- relaunch travel()")
                    elif r == "blocked":
                        if self.textbox():
                            # scripted scene on the transition cell: page
                            # it out (bounded); a battle it starts is
                            # caught by the next attempt's _step -> the
                            # fight path above
                            self._drain_scene()
                        else:
                            break
                    elif r != "warp" and self.map_name() == st["from"]:
                        # stepped but the warp didn't fire. On a multi-warp
                        # door row (Sprout Tower 1F's double door) the held
                        # step GLIDES across every door tile without firing
                        # (gotcha 12); each retry then re-crosses the row
                        # from the other side -- the observed (8,15)<->
                        # (11,15) ping-pong. take_warp drives back onto the
                        # tile properly, including from ON it.
                        if st["kind"] == "warp" and st.get("cell"):
                            if self.take_warp(*st["cell"],
                                              f"travel -> {dest}"):
                                r = "warp"
                                break
                        continue
                    else:
                        break
            self.settle()
            if self.map_name() == st["from"] and st["kind"] == "connection":
                # Map-edge connections are a BAND, and the planned row can
                # be off by one (Azalea's east edge fires at y=14, the plan
                # said 13; Route 32 -> Violet at x=8). Slide along the edge
                # and retry with a held step rather than failing the leg.
                if self._slide_edge(st, dest):
                    r = "warp"
                self.settle()
            mx, my = self.pos()[2:]
            here = self.map_name()
            if here != st["to"]:
                raise TravelError(
                    f"leg {i}: {st['kind']} {st['dir']} at {(px, py)} -- "
                    f"expected {st['to']}, on {here} {(mx, my)} "
                    f"(step result: {r})")
            drift = abs(mx - expected[0]) + abs(my - expected[1])
            if drift > 3:
                raise TravelError(
                    f"leg {i}: landed {here} {(mx, my)}, modeled landing "
                    f"{expected} (drift {drift} > 3)")
            log.info(f"  -> {here} {(mx, my)} (drift {drift})")
            if here == dest:
                return steps      # landed on the destination: done
            # the glide can carry the landing across a region seam: the
            # rest of the plan then walks the wrong side of a wall.
            if drift and set(self._regions(here, mx, my)).isdisjoint(
                    self._regions(here, *expected)):
                log.info("  landing crossed a region seam; replanning "
                         "remainder from live cell")
                steps = steps[:i + 1] + self.route(dest)
            i += 1
        return steps

    # -- savestate breadth-first exploration --------------------------------

    def _explore_snap(self):
        """Current emulation as an in-memory savestate blob."""
        buf = BytesIO()
        self.emu.py.save_state(buf)
        return buf.getvalue()

    def _explore_restore(self, blob):
        self.emu.py.load_state(BytesIO(blob))
        self.emu.tick(5)            # let the restored frame re-latch

    def _explore_settled_move(self, mv, on_battle, max_frames=1200):
        """One directional move driven to a SETTLED end state for
        explore_bfs: ice slides and warp glides keep the avatar moving
        with no input, forced signs pop textboxes mid-move, and wilds/
        trainers intercept. Polls pos() until stable, answers textboxes
        with A at 40+ frame gaps, and resolves battles per `on_battle`
        ('fight' | 'skip'). A move that gets nowhere retries once with
        _step_warp_tap: COLL_STAIRCASE tiles push a held key straight
        back off, so Victory Road's inter-floor stairs read as walls to a
        held-key search (the avatar even STANDS on them without firing).
        Returns 'moved' | 'blocked' | 'skip' (skip = dead branch)."""
        before = self.pos()

        def _drive():
            last, quiet = None, 0
            f0 = self.emu.frame
            while self.emu.frame - f0 < max_frames:
                if self.battle():
                    if on_battle != "fight":
                        return "skip"
                    self.fight()
                    if getattr(self, "_whiteout_pending", False):
                        self._whiteout_pending = False   # the BRANCH died
                        return "skip"
                    last, quiet = None, 0
                    continue
                if self.textbox():
                    self.press("A:4 .:40")   # 40+ frame gap between answers
                    last, quiet = None, 0
                    continue
                cur = self.pos()
                if cur == last:
                    quiet += 1
                    if quiet >= 3:
                        break
                else:
                    last, quiet = cur, 0
                self.emu.tick(20)
            return None

        self.step_dir(mv)
        out = _drive()
        if out:
            return out
        if self.pos() != before:
            return "moved"
        try:
            self._step_warp_tap(mv)     # staircase phase-shifted taps
        except Exception:
            return "blocked"
        out = _drive()
        if out:
            return out
        return "moved" if self.pos() != before else "blocked"

    def explore_bfs(self, goal, max_moves=600, dirs="URDL", forbid_maps=(),
                    on_battle="fight", max_nodes=400):
        """Savestate breadth-first exploration (wren pt6: hand-rolled 10+
        times this run for ice slides, the Rocket base, Tohjo Falls).
        BFS over settled directional moves from the CURRENT state, with
        in-memory savestates as nodes and the frontier keyed by
        (map, x, y). `goal` is a callable(driver) -> bool evaluated
        after EVERY settled move -- a mid-move map change is an
        evaluation point too. States on `forbid_maps` (map names) are
        goal-checked but never expanded. on_battle='fight' plays
        intercepts out with fight(); 'skip' abandons that branch.
        Budgets: `max_moves` settled moves, `max_nodes` distinct
        (map, x, y) states -- snapshots live in memory only, keep the
        cap modest.

        Returns {'found': bool, 'state': bytes|None, 'steps': int,
        'visited': int, 'cells': set[(map, x, y)]}. On found, the winning
        savestate IS the loaded emulation state (the returned blob is a
        keepsake); on not-found the starting state is reloaded. `cells` is
        the frontier actually proven reachable -- read it to pick the next
        waypoint when a floor's static grid lies about its geometry."""
        forbid = set(forbid_maps)
        self.settle()
        if goal(self):
            return {"found": True, "state": self._explore_snap(),
                    "steps": 0, "visited": 1,
                    "cells": {(self.map_name(),) + self.pos()[2:]}}
        root = self._explore_snap()
        seen = {(self.map_name(),) + self.pos()[2:]}
        q = deque([(root, 0)])
        moves = 0
        while q and moves < max_moves and len(seen) < max_nodes:
            blob, depth = q.popleft()
            for mv in dirs:
                if moves >= max_moves or len(seen) >= max_nodes:
                    break
                self._explore_restore(blob)
                moves += 1
                out = self._explore_settled_move(mv, on_battle)
                if out == "skip":
                    continue              # dead branch; state is junk
                if goal(self):
                    state = self._explore_snap()
                    log.info(f"  explore_bfs: goal at {self.map_name()} "
                             f"{self.pos()[2:]} after {depth + 1} steps "
                             f"({moves} moves, {len(seen)} states)")
                    return {"found": True, "state": state,
                            "steps": depth + 1, "visited": len(seen),
                            "cells": set(seen)}
                if out == "blocked":
                    continue
                key = (self.map_name(),) + self.pos()[2:]
                if key in seen or key[0] in forbid:
                    continue
                seen.add(key)
                q.append((self._explore_snap(), depth + 1))
        self._explore_restore(root)
        log.info(f"  explore_bfs: no goal within budget "
                 f"({moves} moves, {len(seen)} states)")
        return {"found": False, "state": None, "steps": 0,
                "visited": len(seen), "cells": set(seen)}


    def _standable(self, name, c):
        """Path-existence is not enough: cross-map BFS treats any goal as
        reachable (warp tiles, counters). Standing spots must be real."""
        try:
            grid = self.nav.grid(name)
            if 0 <= c[0] < len(grid[0]) and 0 <= c[1] < len(grid):
                b = grid[c[1]][c[0]]
                from crystalagent.nav import WATER
                return b in WALKABLE or b in HOPS or \
                    (self.nav.surf and b in WATER)
            return False
        except KeyError:
            return False

    def enable_surf(self):
        """Turn on water routing once someone in the party knows SURF.
        The land->water step pops 'The water is calm... SURF?' -- goto's
        blocked-step handler flushes it (A = YES) and replans, so no other
        machinery changes. Verifies the party actually knows the move."""
        for mon in self.observe()["party"]:
            if any(m.get("name") == "SURF" for m in mon.get("moves", [])):
                self.nav.surf = True
                log.info("  [surf] water routing enabled")
                return True
        raise RuntimeError("enable_surf: nobody in the party knows SURF")

    def _approach_cell(self, x, y):
        """Cell to stand on to talk to the NPC at (x,y): an adjacent
        walkable cell if one exists, else (counters!) two cells out along
        a ray whose middle cell is blocked."""
        here = self.pos()[2:]
        name = self.map_name()
        npcs = self.npc_cells()
        grid = self.nav.grid(name)
        wid, hgt = len(grid[0]), len(grid)
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            c = (x + dx, y + dy)
            if self._standable(name, c) and \
                    self.nav.find_path(name, here, c, npcs) is not None:
                return c
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            mid, far = (x + dx, y + dy), (x + 2 * dx, y + 2 * dy)
            if not (0 <= far[0] < wid and 0 <= far[1] < hgt):
                continue
            if mid in npcs or grid[mid[1]][mid[0]] in WARPS:
                continue   # would need to pass an NPC or a warp
            if self._standable(name, far) and \
                    self.nav.find_path(name, here, far, npcs) is not None:
                return far
        return None

    def talk_to(self, x, y, label="", facing=None):
        """Walk next to the NPC at (x,y) (or across a counter from them),
        face them, and talk. Fights any trainer battle that triggers
        (sight-lines are slow: polls for wBattleMode after the dialog).
        `facing` ('U'/'D'/'L'/'R') forces which way the player faces when
        talking (i.e. which side to approach from) -- some scripts branch
        on VAR_FACING (e.g. the Ilex Farfetch'd chase sends the bird
        BACKWARD on the wrong facing). Returns 'battle' | 'talked' | False."""
        if self.battle():
            self.fight()
            if self._whiteout_stop(f"talk_to ({x},{y})"):
                return False
        self.settle()
        if facing:
            fdx, fdy = STEP[facing]
            spot = (x - fdx, y - fdy)     # stand opposite the facing dir
            if not self._standable(self.map_name(), spot):
                log.info(f"  facing={facing} spot {spot} not standable",
                      )
                return False
        else:
            spot = self._approach_cell(x, y)
        if spot is None:
            log.info(f"  no approach to ({x},{y})")
            return False
        if not self.goto(*spot, label or f"approach ({x},{y})"):
            return False
        fdx = (x > spot[0]) - (x < spot[0])
        fdy = (y > spot[1]) - (y < spot[1])
        facing = {(-1, 0): "L", (1, 0): "R", (0, -1): "U", (0, 1): "D"}[
            (fdx, fdy)]
        self.step_dir(facing)          # blocked step = turn toward the NPC
        self.press("A:2 .:20")
        outcome = self.flush_dialog(30000)
        # trainer triggers land slowly; poll before declaring it plain talk
        f0 = self.emu.frame
        while not self.battle() and self.emu.frame - f0 < 2400:
            self.press(".:60")
        if self.battle() or outcome == "battle":
            self.fight()
            if self._whiteout_stop(f"talk_to ({x},{y})"):
                return False
            return "battle"
        return "talked"

    @staticmethod
    def _save_target(default_path, name):
        """Resolve a save target: bare milestone names land in saves/;
        path-like names (absolute or containing a directory component)
        are honored verbatim so sessions can isolate their checkpoints."""
        if not name:
            return default_path
        p = Path(name)
        return p if len(p.parts) > 1 else Path(paths.SAVES_DIR) / name

    def _save_blockers(self):
        """Names of everything that makes the CURRENT screen unsafe to
        bake into a savestate: a live battle, a running script, a textbox,
        or any menu cursor glyph ($ec '▷' / $ed '▶'). Empty list = clean
        interactable overworld."""
        blockers = []
        if self.battle():
            blockers.append("battle")
        try:
            sm = self.emu.read_u8("wScriptMode")
        except Exception:
            sm = 0
        if sm:
            blockers.append(f"running script (wScriptMode={sm})")
        if self.textbox():
            blockers.append("textbox")
        if any(c in r for r in self.emu.screen_text() for c in CURSORS):
            blockers.append("menu cursor")
        return blockers

    def save(self, name=None, force=False):
        """Save the working state (plus a `name` milestone copy when given).
        Refuses to overwrite a file whose .meta frame count is NEWER than
        the running emulation unless force=True -- the accidental-rollback
        class (older checkpoint over post-badge progress) now fails loudly
        inside the harness instead of silently regressing.

        Also refuses to bake a DIRTY screen into the state (wren pt3: a
        stuck pack layer saved into wren.state poisoned every fork made
        from it): the game must be a clean interactable overworld --
        wScriptMode 0, no textbox, no menu cursor, not in battle. Dirty
        screens get a bounded B-press auto-recovery first; force=True
        bypasses the check but LOGS what it is overriding."""
        if force:
            # force is legitimate (rolling back a fork on purpose), but it
            # must never be QUIET: a state baked with a menu open reloads
            # with dead movement, because the open menu eats every input
            # (AGENTS.md gotcha 7), and every fork made from it inherits it.
            blockers = self._save_blockers()
            if blockers:
                log.warning(
                    f"  saving OVER blockers ({', '.join(blockers)}) because "
                    f"force=True -- a reloaded state with an open menu has "
                    f"dead movement (gotcha 7)")
        if not force:
            # legit saves happen right AFTER dialogs: settle before the
            # first check so a closing box isn't judged mid-fade
            self.settle(max_frames=300)
            blockers = self._save_blockers()
            for _ in range(4):
                if not blockers or "battle" in blockers:
                    break                 # never B-mash inside a battle
                self.press("B:4 .:20")    # bounded auto-recovery
                self.settle(max_frames=300)
                blockers = self._save_blockers()
            if blockers:
                raise RuntimeError(
                    "refusing to save a dirty screen ("
                    + ", ".join(blockers)
                    + ") -- close it first or pass force=True")
        target = self._save_target(self.state_path, name)
        meta = Path(str(target) + ".meta")
        if meta.exists() and not force:
            try:
                old = json.loads(meta.read_text()).get("frames", 0)
            except Exception:
                old = 0
            if old > self.emu.frame:
                raise RuntimeError(
                    f"refusing to overwrite {meta.name} (frame {old}) with "
                    f"frame {self.emu.frame} -- pass force=True to roll back")
        self.emu.save(target)
        if name:  # also update the working state
            self.emu.save(self.state_path)
        log.info(f"[saved {target.name}] {self.status()}")

    # -- shopping -----------------------------------------------------------

    def _shop_cursor_row(self, rows):
        from crystalagent.menus import _cursor_x
        for i, r in enumerate(rows):
            if _cursor_x(r) >= 0:
                return i
        return -1

    # The buy list is the only shop screen that shows the money window
    # ('¥nnnnn' at the top right) together with priced rows; the clerk's
    # BUY/SELL/QUIT menu shows neither. "How many?" plus the '×NN' glyph
    # is the quantity picker, which MUST NOT be left open: it holds the
    # script open and then every movement press is swallowed silently
    # (session claude pt12).
    def _shop_list_up(self, rows=None):
        rows = self.emu.screen_text() if rows is None else rows
        return any("¥" in r for r in rows)

    def _shop_picker_up(self, rows=None):
        rows = self.emu.screen_text() if rows is None else rows
        return any("How many?" in r or "×" in r for r in rows)

    def _shop_exit(self, max_presses=12):
        """B out of every shop screen -- picker, list, clerk menu, page --
        and verify. B only: A on a list buys whatever the cursor sits on
        (gotcha 13), and A on the picker buys `qty` of it.

        Returns True when nothing shop-shaped and nothing modal is left.
        The old loop stopped at "no ¥ and no cursor", which a quantity
        picker satisfies while still owning the input."""
        for _ in range(max_presses):
            rows = self.emu.screen_text()
            if not (self._shop_list_up(rows) or self._shop_picker_up(rows)
                    or self.textbox() or self.menu_open()):
                self.press(".:40")            # outlast a closing repaint
                if not self._shop_list_up() and not self._shop_picker_up() \
                        and not self.menu_open():
                    return True
                continue
            self.press("B:6 .:16")
        left = self.emu.screen_text()
        if self._shop_picker_up(left):
            log.warning("  mart: a QUANTITY PICKER is still open -- "
                        "movement will be swallowed until it closes")
        return not self._shop_list_up(left) and not self._shop_picker_up(left)

    def mart_buy(self, x, y, item_name, qty=1, label=""):
        """Talk to the clerk at (x,y) and buy `qty` of `item_name`.
        Returns True if the bag ended up holding the item."""
        def bag_count():
            total = 0
            for count_sym, list_sym in (("wNumItems", "wItems"),
                                        ("wNumBalls", "wBalls")):
                n = min(self.emu.read_u8(count_sym), 20)
                if not n:
                    continue
                idx = bag_item_index(self.emu, self.names, item_name,
                                     "balls" if list_sym == "wBalls"
                                     else "items")
                if idx is not None:
                    bank, addr = self.emu.sym[list_sym]
                    raw = self.emu.read((bank, addr), n * 2)
                    total += raw[idx * 2 + 1]
            return total

        before = bag_count()
        bought = False
        want = _norm_item(item_name)
        shop_open = self._shop_list_up()
        if not shop_open:
            if self.talk_to(x, y, label or "clerk") != "talked":
                return False
            opened = False
            for _attempt in range(2):
                # The clerk's own menu comes FIRST: "Welcome! How may I
                # help you?" over BUY / SELL / QUIT (a glyph menu, so
                # talk_to's flush_dialog correctly stops there). The
                # buy list only exists after BUY is taken, and taking it
                # has to be DELIBERATE -- a blind A here is gotcha 13.
                # Waiting passively for a '¥' that only BUY can produce
                # is what made this raise "FULL RESTORE x6 failed (bag
                # 0 -> 0)" at the Indigo Plateau mart with the item in
                # stock (session claude pt12).
                for _ in range(20):
                    if self._shop_list_up():
                        opened = True
                        break
                    if Menus.has_label(self.emu.screen_text(), "BUY") \
                            or any("BUY" in r for r in
                                   self.emu.screen_text()):
                        self.select_menu_row("BUY", max_presses=6)
                    self.press(".:8")
                if opened or _attempt:
                    break
                # gotcha 2 first-call race: the clerk A press can land the
                # frame the dialog engine isn't polling input yet --
                # settle, drain stragglers, re-talk ONCE before failing
                log.info("  shop menu slow to open; settling and "
                      "re-talking")
                self.settle()
                if self.textbox():
                    self.flush_dialog()
                if self.talk_to(x, y, label or "clerk") != "talked":
                    break
            if not opened:
                self._shop_exit()
                raise RuntimeError(
                    f"mart_buy: buy list did not open at ({x},{y}) -- "
                    f"clerk talk failed twice (registry actions must not "
                    f"fail as a silent log line)")
        # Only the LIST rows: the description textbox at the bottom
        # (rows 12-17) also carries item words, and the list itself is
        # name-row/price-row pairs in a 4-item window with a '▼' when
        # there is more below (live: ULTRA BALL / MAX REPEL / HYPER
        # POTION / MAX POTION, with FULL RESTORE off-window).
        list_rows = 12
        seen, flipped, direction = None, False, "D"
        for _ in range(40):                       # bounded item search
            rows = self.emu.screen_text()
            window = rows[:list_rows]
            cur = self._shop_cursor_row(window)
            target = next((i for i, r in enumerate(window)
                           if want in _norm_item(r)), None)
            if cur >= 0 and target is not None and target == cur:
                self.press("A:4 .:40")           # open quantity picker
                if not self.menu.wait_for(
                        lambda r: any("How many?" in s for s in r),
                        timeout_frames=400):
                    log.info("  no quantity picker")
                    break
                def picker_qty():
                    for s in self.emu.screen_text():
                        if "×" in s:
                            try:
                                return int(s.split("×")[1].split()[0])
                            except (IndexError, ValueError):
                                return None
                    return None

                # qty keys are RIGHT=+10 / LEFT=-10 / UP=+1 / DOWN=-1
                # and presses get swallowed unpredictably -- verify the
                # ×NN glyph after EVERY press or overshoot (omp-fresh hit
                # x51 once on UP-only blind presses).
                tries = 0
                while picker_qty() != qty and tries < 40:
                    v = picker_qty()
                    if v is None:
                        self.press(".:10")
                    else:
                        step = ("R" if v + 10 <= qty else
                                "L" if v - 10 >= qty else
                                "U" if v < qty else "D")
                        self.press(f"{step}:4 .:14")
                    tries += 1
                if picker_qty() != qty:
                    break
                self.press(".:10")
                self.press("A:6 .:40")           # confirm the quantity
                # ...which opens "N ITEM(S) will be ¥NNNN." over a
                # YES/NO box (live at the Indigo Plateau mart). Nothing
                # answered it before, so the purchase never happened and
                # `bought` was set anyway -- "bag 0 -> 0, bought=True".
                # flush_dialog cannot answer it either: a choice box is
                # exactly what it refuses to touch (gotcha 13).
                for _ in range(6):
                    rows = self.emu.screen_text()
                    if Menus.has_label(rows, "YES"):
                        self.press("A:6 .:40")
                        break
                    if any("YES" in r for r in rows):
                        self.press("U:4 .:14")   # cursor sat on NO
                        continue
                    self.press(".:15")
                self.flush_dialog(3000)          # "Here you are! Thanks!"
                bought = True
                break                             # one purchase per call
            if cur < 0:
                break
            if target is None:
                # off-window: walk the list, and REVERSE once it pins --
                # scrolling one way forever is how an in-stock item below
                # the window reported "not for sale"
                if window == seen:
                    if flipped:
                        break
                    direction, flipped = "U", True
                seen = window
                self.press(f"{direction}:6 .:12")
                continue
            self.press("D:6 .:12" if target > cur else "U:6 .:12")
        self._shop_exit()
        self.press(".:40")
        after = bag_count()
        ok = bought and after >= before + qty
        log.info(f"  mart_buy {item_name} x{qty}: "
              f"{'ok' if ok else 'FAILED'} ({before} -> {after})")
        if not ok:
            raise RuntimeError(
                f"mart_buy: {item_name} x{qty} failed "
                f"(bag {before} -> {after}, bought={bought})")
        return True


# -- legs -------------------------------------------------------------------

def _enter_local_pokecenter(d, tries):
    """heal called outside a Pokécenter: if the CURRENT map has a routable
    Pokécenter warp in the mapgraph, walk in via the normal travel
    machinery (goto approach + held warp entry) instead of exploding.
    Bounded by `tries` travel attempts; raises HealError otherwise."""
    here = d.map_name()
    pcs = sorted({e["to_map"] for e in mapgraph()["edges"]
                  if e.get("routable") and e["from_map"] == here
                  and "POKECENTER" in e["to_map"]})
    if not pcs:
        raise HealError(here, "no Pokécenter warp on this map")
    pc = pcs[0]
    tries = max(1, int(tries))
    last = None
    for attempt in range(1, tries + 1):
        log.info(f"  heal: not in a Pokécenter (on {here}); "
                 f"entering {pc} (try {attempt}/{tries})")
        try:
            d.travel(pc, label="heal detour")
        except Exception as e:            # TravelError, LookupError, ...
            last = e
            log.info(f"  heal detour attempt {attempt} failed: {e}")
        if "POKECENTER" in d.map_name():
            return
    raise HealError(d.map_name(),
                    f"couldn't enter {pc} after {tries} "
                    f"tr{'y' if tries == 1 else 'ies'}"
                    + (f" ({last})" if last else ""))


def heal_pokecenter(d, tries=2):
    """Talk to the nurse, wait out the jingle. Verifies the location on
    entry and the actual heal on exit -- an unverified 'healed' claim once
    masked a failed goto entirely. Called outside a Pokécenter, walks in
    first when the current map has a routable Pokécenter warp in the
    mapgraph (bounded by `tries`); raises HealError when it genuinely
    cannot reach a nurse (wren pt4/pt5: the old bare RuntimeError blew up
    whole composites over a recoverable one-map detour)."""
    if "POKECENTER" not in d.map_name():
        _enter_local_pokecenter(d, tries)

    def _hp_snapshot():
        return tuple(m["hp"] for m in game_state(d.emu, d.names)["party"])

    def _wait_heal_settled(timeout=1500):
        """The jingle animates HP upward; reading before it finishes is
        the stale-HP raise class (omp-fresh: 6/7 heals). Settled = HP
        stable across polls with no textbox and no owning script."""
        f0, prev = d.emu.frame, None
        while d.emu.frame - f0 < timeout:
            cur = _hp_snapshot()
            if cur == prev and not d.textbox() \
                    and d.emu.read_u8("wScriptMode") == 0:
                return True
            prev = cur
            d.emu.tick(10)
        return False

    def _nurse_cell():
        """The nurse's own coordinates, from this map's object_events.

        (3,3) was hardcoded, which is the Johto town layout: it put
        INDIGO_PLATEAU_POKECENTER_1F -- counter on row 8, nurse behind
        (3,7) -- permanently out of reach, and heal_pokecenter raised
        'party not fully healed' after routing to a cell with no nurse in
        front of it (FUCK_I_MESSED_UP.md #78). The map declares where she
        stands; ask it."""
        try:
            cell = d.sprite_cell("SPRITE_NURSE")
        except Exception as e:               # unparsed/absent map source
            log.info(f"  heal: cannot read {d.map_name()}'s objects ({e})")
            return None
        if cell is None:
            log.info(f"  heal: no SPRITE_NURSE object_event on "
                     f"{d.map_name()}")
        return cell

    def _nurse():
        cell = _nurse_cell()
        if cell is None:
            # last resort: the Johto counter layout this used to assume
            d.goto(3, 3, "nurse counter")
            d.step_dir("U")    # face her (blocked step = turn)
            d.press("A:2 .:20")
            d.flush_dialog()
        elif not d.talk_to(*cell, label="nurse"):
            raise HealError(d.map_name(),
                            f"could not reach the nurse at {cell}")
        # intro page(s) done -- flush stops ("menu") at the heal prompt.
        # The YES/NO box is a deliberate choice: cursor defaults to YES,
        # but an extra stray A earlier can leave it on NO (omp-fresh
        # variant), so navigate explicitly.
        if d.menu.wait_for(lambda rows: any("YES" in r for r in rows),
                           260):
            d.menu.select_label("YES", max_presses=4)
        _wait_heal_settled()   # HP-keyed jingle wait, not a blind frame
        d.flush_dialog()       # "we hope to see you again"
        d.settle()
        d.flush_dialog(1500)   # sweep straggler pages before verifying

    def _hurt():
        return [m for m in game_state(d.emu, d.names)["party"]
                if not m.get("egg") and m.get("hp", 0) < m.get("max_hp", 0)]

    _nurse()
    hurt = _hurt()
    if hurt:                   # late pages can sit between us and truth
        d.flush_dialog(2000)
        hurt = _hurt()
    if hurt:
        # gotcha 2 first-call race: the A that opens the nurse dialog is
        # swallowed when the counter goto ends on an unsettled frame --
        # settle, drain, and redo the interaction ONCE before raising
        log.info("  heal not confirmed; settling and retrying once")
        d.settle()
        d.flush_dialog(1500)
        _nurse()
        hurt = _hurt()
    lead = d.lead()
    log.info(f"  healed: {lead['name']} {lead['hp']}/{lead['max_hp']}",
          )
    if hurt:
        raise RuntimeError(
            f"heal_pokecenter: party not fully healed "
            f"({[(m['species'], m['hp'], m['max_hp']) for m in hurt]})")
    # success: the player is still standing in front of the nurse facing
    # her, and the next A-bearing routine re-opens her prompt (two leg-2
    # wedges). Step AWAY from whatever direction we are facing -- not
    # blindly south, which only held for the y=3 Johto counter -- and
    # settle so no residual prompt stays armed.
    away = {"U": "D", "D": "U", "L": "R", "R": "L"}.get(d.facing(), "D")
    if d.step_dir(away) != "moved":
        d.step_dir(away)           # first press may only turn in place
    d.settle()


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
        sys.exit("usage: trek.py <leg> [<state>] [args...]\n"
             "legs: walk PATH | goto X Y [MAP] | talk X Y | "
             "      flush | heal | train LEVEL | route MAP | travel MAP |\n"
             "      mart X Y ITEM QTY | catch [NICKNAME] | fight |\n"
             "      map [MAP_NAME] (read-only ASCII view) |\n"
             "      route29 | to_violet |\n"
             "      errand1 errand2 errand3 errand4 violet\n"
             "      verify FLAG... (event flags / badges; exit 1 if any\n"
             "      is missing or unknown -- read-only, no save) |\n"
             "      states (saves/ table: frame from .meta, missing-meta\n"
             "      marker, age) |\n"
             "      gc [--apply] [--keep N] (checkpoint lifecycle;\n"
             "      dry-run default, protects PROGRESS.md milestones)\n"
             "goto MAP: CONST_NAME or CamelCase (e.g. VIOLET_CITY) -- routes\n"
             "across maps via warps + edge connections\n"
             "<state>: savestate path ('' or omitted = saves/default.state)")
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
        heal_pokecenter(d)
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
