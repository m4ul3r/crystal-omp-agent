#!/usr/bin/env python3
"""Journey driver: reusable primitives for long play sessions, run as legs
in a single persistent process (no per-command emulator reload).

Usage: .venv/bin/python trek.py <leg> [args]   (see main() dispatch)
"""

import heapq
import json
import logging
import sys
from collections import deque
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from crystalagent import paths
from crystalagent.battle import Battle, BattleData, bag_item_index, bag_quantity, cancel_pack, goto_pocket, _norm_item
from crystalagent.charmap import Charmap
from crystalagent.emu import Crystal, parse_sequence, InputError
from crystalagent import hookevents
from crystalagent.menus import Menus, battle_menu_up, dialog_press_safe, CURSORS
from crystalagent.names import Names
from crystalagent.nav import MapData, STEP, WARPS, WALKABLE, HOPS, CONN_NAME, ICE, COLL_PIT
from crystalagent.nav import WATER as _NAV_WATER
from crystalagent.nav import ICE as _NAV_ICE
from crystalagent.schemas import validate_observe, validate_route
from crystalagent.state import game_state, status_line
from crystalagent.symfile import Symbols

log = logging.getLogger("trek")

DIRS = {"U": "UP", "D": "DOWN", "L": "LEFT", "R": "RIGHT"}

class TravelError(RuntimeError):
    """travel(): a transition landed somewhere the plan didn't expect."""


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
            for d, (dx, dy) in STEP.items():
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
    (" ", "unreachable from here"),
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
    """ASCII view of the region reachable on foot/surf from `pos`, cropped
    to its bounding box (+1). Global coordinates in the rulers so the
    deciding LLM can quote them straight back to goto()/talk_to()."""
    grid = nav.grid(map_name)
    hgt, wid = len(grid), len(grid[0])
    reach = _reach(nav, map_name, pos, surf)
    blocked = set(getattr(nav, "blocked", {}).get(map_name, ()))
    npc_set = {tuple(c) for c in npcs}
    xs = [x for x, _ in reach | {tuple(pos)}]
    ys = [y for _, y in reach | {tuple(pos)}]
    ox, oy = max(min(xs) - 1, 0), max(min(ys) - 1, 0)
    ex, ey = min(max(xs) + 1, wid - 1), min(max(ys) + 1, hgt - 1)
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
                row.append(" ")
        lines.append(f"{y:4d} " + "".join(row))
    used = {ch for line in lines[3:] for ch in line.split(" ", 1)[-1]}
    legend = [f"  {g} {desc}" for g, desc in _GLYPH_LEGEND
              if g != " " and g in used]
    if legend:
        lines.append("legend:")
        lines += legend
    return "\n".join(lines)


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


def script_is_disruptive(repo, camel_file, script_label):
    """True if the coord_event's script moves the player or makes an NPC
    follow them (applymovement/follow) -- i.e. it would physically undo
    progress, like Route 32's Cooltrainer push-back. Pure-dialog triggers
    (the Slowpoke Tail pitch) are left routable: walk/goto already flush
    textboxes."""
    key = (camel_file, script_label)
    if key not in _script_body_cache:
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
        _script_body_cache[key] = (
            False if body is None
            else any(kw in instr for instr in body
                     for kw in _DISRUPTIVE_KEYWORDS))
    return _script_body_cache[key]


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
    Handles const_def / const / const_skip / const_next like rgbds would."""
    import re as _re
    flags, idx = {}, None
    for line in open(path, encoding="utf-8"):
        s = line.strip()
        if s.startswith("const_def"):
            idx = 0
            continue
        m = _re.match(r"const_next\s+(\d+)$", s)
        if m:
            idx = int(m.group(1))
            continue
        m = _re.match(r"const_skip\b", s)
        if m:
            idx += 1
            continue
        m = _re.match(r"const\s+(\w+)$", s)
        if m and idx is not None:
            flags[m.group(1)] = idx
            idx += 1
    return flags


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



class Driver:
    def __init__(self, state_path=None):
        self.state_path = Path(state_path or paths.DEFAULT_STATE)
        sym = Symbols(paths.SYM)
        cm = Charmap(paths.CHARMAP)
        self.emu = Crystal(paths.ROM, sym, cm, self.state_path)
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
        # appends {'mon','forgot','learned','slot'} here and logs a
        # LEARN line. Inspect after train()/fight() before trusting
        # slot-based policies. Never cleared automatically.
        self.move_changes = []
        self.hooks = hookevents.install(self.emu)

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
        player's cell (render_map_view); pass a CamelCase or CONST map
        name to view another map from its origin cell (0,0)."""
        if map_name and map_name != self.map_name():
            const = next((c for c, camel in self.nav.camel.items()
                          if camel == map_name or c == map_name), None)
            if const is None:
                raise SystemExit(f"unknown map {map_name!r}")
            return render_map_view(self.nav, const, (0, 0))
        return render_map_view(self.nav, self._map_const(),
                               self.pos()[2:], npcs=self.npc_cells(),
                               surf=bool(getattr(self.nav, "surf", False)))

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

    def status(self):
        return status_line(game_state(self.emu, self.names))

    def npc_cells(self):
        """Live NPC positions (walk-cell coords) from the object structs.
        Struct map coords are player coords + 4; slot 0 is the player."""
        bank, base = self.emu.sym["wObjectStructs"]
        stride = self.emu.sym.addr("wObject1Struct") - base
        cells = set()
        for i in range(1, 13):
            b = self.emu.read((bank, base + i * stride), 18)
            if b[0]:
                cells.add((b[16] - 4, b[17] - 4))
        return cells

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

            def kind(b):
                if b in (0x14, 0x18):
                    return "grass"
                if b == 0x00:
                    return "floor"
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
                return "blocked"

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
                obs["enemy"] = {"species": en["species"], "name": en["name"],
                                "level": en["level"], "hp": en["hp"],
                                "max_hp": en["max_hp"]}
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

    def _is_warp_cell(self, x, y):
        try:
            grid = self.nav.grid(self.map_name())
            return grid[y][x] in WARPS
        except (KeyError, IndexError):   # unknown map or off the map edge
            return False

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
        except (KeyError, IndexError):
            return False

    def _mount_surf(self, mv):
        """Face the water and start surfing: walking into water does NOT
        prompt in GSC -- you must face it and press A ('The water is
        calm... SURF?' -> YES). Ends riding ON the water cell."""
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
        return ("moved" if self.emu.read_u8("wPlayerState") == 4
                else "blocked")

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

    # -- actions -----------------------------------------------------------

    def press(self, seq):
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
        rows = self.emu.screen_text()
        opts = self._choice_labels(rows)
        if choice not in opts or \
                not any(c in r for r in rows for c in CURSORS):
            return {"answered": False, "chose": None, "options": opts}
        ok = bool(self.menu.select_label(choice, max_presses=6))
        return {"answered": ok,
                "chose": choice if ok else None, "options": opts}
    def _naming_sig(self):
        """WRAM signature of naming-screen state; NamingScreen writes
        these BEFORE rendering (engine/menus/naming_screen.asm), so a
        delta beats every screen-text check on fade-in frames."""
        e = self.emu
        return (e.read_u8("wNamingScreenType"),
                e.read_u8("wNamingScreenDestinationPointer"))

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

    def dismiss_keyboard(self, name=None):
        """Confirm a naming screen. With a name, actually type it; without,
        confirm with the minimal name (fast path)."""
        if name:
            log.info(f"  naming keyboard: typing {name!r}")
            for _ in range(12):       # B = backspace: clear stray chars
                self.press("B:3 .:10")
            self.type_name(name)
            return
        log.info("  naming keyboard: confirming")
        for _ in range(12):           # clear strays so decline is clean
            self.press("B:3 .:10")
        self.press("START:4 .:20 A:4 .:30")          # jump to END, confirm
        if self.keyboard_open():                      # empty name refused:
            self.press("A:2 .:10 START:4 .:20 A:4 .:30")  # type one letter

    def type_name(self, name, max_len=10):
        """Type `name` on the naming keyboard (uppercase only -- the game
        renders names in caps anyway). The grid is deterministic
        (data/text/name_input_chars.asm); every move/press is verified
        against WRAM (cursor struct + name length), since input on this
        screen drops presses that land mid-animation."""
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
            if self._naming_sig() != sig0 or self.keyboard_open():
                self.dismiss_keyboard()
                quiet = 0
                continue
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
            if self._naming_sig() != sig0 or self.keyboard_open():
                self.dismiss_keyboard()
                quiet = 0
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

    def fight(self, max_frames=90000, policy=None):
        """Play a battle out with real move selection (best expected
        damage, auto-POTION at low HP, flee hopeless wilds). Pauses at a
        naming keyboard (post-catch nickname prompt) to type
        self._pending_nickname if one is set. `policy=None` falls back
        to self.default_policy (still None by default): scripted battles
        the driver intercepts on its own (talk_to, goto, travel) obey a
        pre-armed policy instead of silently fighting with the default."""
        if policy is None:
            policy = self.default_policy
        if not self.battle():
            return self.lead()
        self._resolve_learn_flow()   # repair a wedged mid-learn state
        moves0 = self._party_moves()   # learn-transparency baseline
        f0 = self.emu.frame
        money0 = game_state(self.emu, self.names)["player"]["money"]
        b = Battle(self.emu, self.names, self.bdata)
        name = self._resolve_nickname(self._pending_nickname,
                                      b.enemy()["name"])
        outcome = b.play(policy=policy, max_frames=max_frames,
                         want_nickname=bool(name),
                         text_handler=self._battle_text_handler)
        for _ in range(3):                       # naming handoff loop
            if outcome != "naming" or not self.keyboard_open():
                break
            self._pending_nickname = None
            self.dismiss_keyboard(name)
            outcome = b.play(policy=policy, max_frames=max_frames,
                             text_handler=self._battle_text_handler)
        self._pending_nickname = None
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
        elif outcome in ("timeout", "stuck"):
            # Burn ZERO blind retries: dump the frozen battle so the wedge
            # is diagnosable (the historic Bridget/Jigglypuff freeze cost
            # ~10 retries before anyone looked at the screen).
            try:
                me, enemy = b.me(), b.enemy()
                log.warning("  [fight diagnostic] frozen screen:")
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
        # Scratch sidecar, NOT the working state: a snapshot taken during
        # battle resolution must never become a resumable fork if the leg
        # crashes before the next real save. watch.py can still open
        # <name>.watch.state from its checkpoint browser.
        if self.state_path:
            self.emu.save(Path(self.state_path).with_suffix(".watch.state"))
        lead = self.lead()
        log.info(f"  battle [{outcome}, {self.emu.frame - f0} frames] -> "
              f"{lead['name']} L{lead['level']} {lead['hp']}/{lead['max_hp']}",
              )
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
                      "STOP LEARNING", "FORGOTTEN")

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

    def _learn_prompt_up(self, rows):
        joined = "".join(rows).upper()
        return any(m in joined for m in self._LEARN_MARKERS)

    def _battle_text_handler(self, rows):
        """Modal-text hook for Battle.play: drive the level-up move-learning
        flow. Returns True when this frame's input was consumed.

        ACCEPT/REPLACE policy (wren pt4, documented from the code -- this
        is what actually gets sacrificed):
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
        Completed swaps are surfaced by _diff_learned_moves (LEARN log
        line + d.move_changes entry) from _resolve_learn_flow / fight().
        Blind A-mashing derails into party menus and wedges the battle."""
        if not self._learn_prompt_up(rows):
            return False
        joined = "".join(rows).upper()
        if "CAN" in joined and "BE FORGOTTEN" in joined:
            # "HM moves can't be forgotten": the refusal text. Acknowledge
            # it; the move menu reopens and the cursor must MOVE off the HM.
            self.press("A:4 .:16 D:4 .:16")
            return True
        if "FORGOTTEN" in joined:
            # "Which move should be forgotten?" move menu is up
            cur = [r.strip().upper() for r in rows if "▶" in r or "▷" in r]
            on_hm = any(hm in r for r in cur for hm in self.HM_MOVES)
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
            if "STOP LEARNING" in joined:
                # decline path confirm; in learn mode B loops back so the
                # make-room prompt can be answered YES this time
                self.press("B:6 .:20" if self.learn_moves else "A:6 .:20")
            elif self.learn_moves:
                self.press("A:6 .:25")     # YES: make room for the new move
            else:
                self.press("B:6 .:20")     # NO: keep the current moveset
        else:
            self.press("A:4 .:16")         # advance the flow's text pages
        return True

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
                break
            if before is None:       # snapshot only once a flow is real
                before = self._party_moves()
            self._battle_text_handler(rows)
        else:
            done = False
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
        d.move_changes ({'mon','forgot','learned','slot'}, slot 1-based)
        so policies that press fixed move slots can notice their mapping
        broke (Morty lesson: BITE -> SCARY FACE at slot 1 cost three
        whiteouts). Moves landing in previously EMPTY slots shift no
        existing slot and are not recorded; a mon whose label changed
        (evolution without a nickname, party reorder) is skipped rather
        than misattributed."""
        if not before:
            return []
        after = self._party_moves()
        if not hasattr(self, "move_changes"):
            self.move_changes = []     # bare/duck-typed drivers
        changes = []
        for (b_label, b_mv), (a_label, a_mv) in zip(before, after):
            if b_label != a_label:
                continue
            for i, old in enumerate(b_mv):
                new = a_mv[i] if i < len(a_mv) else None
                if old and new and old != new:
                    changes.append({"mon": a_label, "forgot": old,
                                    "learned": new, "slot": i + 1})
        for c in changes:
            log.warning(f"LEARN: {c['mon']} forgot {c['forgot']} -> "
                        f"learned {c['learned']} (slot {c['slot']})")
        self.move_changes.extend(changes)
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

    def catch(self, ball="POKE BALL", max_balls=10, nickname=None):
        """Throw `ball` at the current wild until it connects or the budget
        runs out; flees rather than KO the target once out of balls.
        `nickname`: str (applied to whatever is caught), dict keyed by
        species name, or callable(species_name) -> str|None."""
        thrown = [0]

        def pol(rows, me, enemy):
            dry = bag_item_index(self.emu, self.names, ball, "balls") is None
            if dry or thrown[0] >= max_balls:
                return "flee"
            thrown[0] += 1
            return ("ball", ball)

        self._pending_nickname = nickname
        try:
            return self.fight(policy=pol)
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
                continue
            obs = self.observe()
            cx, cy = obs["x"], obs["y"]
            near = sorted(grass, key=lambda c: abs(c[0] - cx)
                          + abs(c[1] - cy))[:8]
            for tx, ty in near:
                try:
                    self.goto(tx, ty, "into the grass")
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
        silent 'kept training hurt' would be worse."""
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

    def train(self, target_level, max_battles=150):
        """Rotation-train every non-egg party member to >= target_level in
        the nearest grass patch on the current map; returns the min party
        level. Caller must stand on a map WITH grass (ValueError otherwise)
        -- explicit failure beats silently wandering in search of one.
        Level-up learns are accepted per _battle_text_handler's policy;
        any REPLACED move is logged (LEARN: ...) and appended to
        d.move_changes -- check it before reusing slot-based policies."""
        import random
        grass = self._grass_cells()
        if not grass:
            raise ValueError(f"no grass on {self.map_name()} -- walk/travel "
                             "to a route with grass first")
        log.info(f"[train] target L{target_level}, cap {max_battles} battles",
              )
        battles = dry = 0
        changes0 = len(self.move_changes)
        while True:
            obs = self.observe()
            party = obs["party"]
            members = [(i, m) for i, m in enumerate(party)
                       if not m.get("egg")]
            underleveled = any(m["level"] < target_level
                               for _, m in members)
            if not underleveled or battles >= max_battles:
                break
            lead = party[0]
            sick = any(m.get("status") == "PSN" or m["hp"] <= 0
                       for _, m in members)
            if sick or lead["hp"] / max(lead["max_hp"], 1) < 0.35:
                log.info(f"  train: healing rail ({lead['species']} "
                      f"{lead['hp']}/{lead['max_hp']})")
                self._train_heal()
                continue               # relocate grass from wherever we land
            elig = sorted((i for i, m in members
                           if m["hp"] > 0 and m["level"] < target_level),
                          key=lambda i: party[i]["level"])
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
                        self.goto(tx, ty, "into the grass")
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

    def teach_hm(self, hm_tag, move_name, forget_move=None):
        """Teach the HM whose pocket row reads '<hm_tag> <move_name>'
        (e.g. 'H3', 'SURF') to the first ABLE party member via PACK ->
        TM/HM pocket. `forget_move` names the move to delete if the
        learner already knows four (default: whatever the cursor starts
        on, slot 1). Label/WRAM-driven throughout: menus remember their
        last cursor slot, so blind press counts are never safe.
        Raises RuntimeError (with menus closed) if the flow fails."""
        def scr():
            return "".join(self.emu.screen_text()).upper()
        def bail(msg):
            self.close_menus()
            raise RuntimeError(f"teach_hm {move_name}: {msg}")
        self.press("START:4 .:40")
        if not self._wait_screen(lambda s: "EXIT" in s):
            bail("START menu never opened")
        if not self.menu.select_label("PACK"):
            bail("PACK entry not found in START menu")
        for _ in range(8):
            if self.emu.read_u8("wJumptableIndex") == 8:
                break                                 # TM/HM pocket
            self.press("L:4 .:18")
        else:
            bail("TM/HM pocket never opened")
        self.press(".:35")
        # cursor onto the HM row: rendered e.g. "H1 CUT", NOT "HM01"
        # (and "FURY CUTTER" contains "CUT" -- match the H prefix too)
        def on_hm01():
            return any(hm_tag in r and move_name in r
                       for r in self.cursor_rows())
        for _ in range(10):                           # go to list top
            if on_hm01():
                break
            self.press("U:4 .:14")
        for _ in range(12):
            if on_hm01():
                break
            self.press("D:4 .:16")
        else:
            bail("HM01 row never under cursor")
        self.press("A:4 .:80")                        # submenu
        use_up = False
        for _ in range(6):                            # spin-up can be slow
            if "USE" in scr():
                use_up = True
                break
            self.emu.tick(30)
        if not use_up:
            bail("HM01 USE submenu not found")
        self.press(".:35")                            # gotcha 2: settle
        self.press("A:6 .:30")                        # USE
        for _ in range(20):                           # boot texts -> YES/NO
            s = scr()
            if "YES" in s and "NO" in s:
                break
            self.press("A:4 .:45")
        else:
            bail("teach prompt never appeared")
        self.press("A:5 .:60")                        # YES: teach
        if not self._wait_screen(lambda s: "CANCEL" in s and "ABLE" in s):
            bail("party list never opened")
        # pick the first ABLE mon -- only the CURSOR mon matters; other
        # party members legitimately show NOT ABLE. The ABLE tag renders
        # on the row BELOW the cursor row ("▶ AA" / "L22 ABLE"). The
        # D-scan wraps, so every row gets visited wherever it starts.
        def able_under_cursor():
            rows = self.emu.screen_text()
            for i, r in enumerate(rows):
                if "▶" in r or "▷" in r:
                    tag = rows[i + 1].upper() if i + 1 < len(rows) else ""
                    return "ABLE" in tag and "NOT ABLE" not in tag
            return False
        picked = False
        for _ in range(8):
            if able_under_cursor():
                picked = True
                break
            self.press("D:4 .:15")
        if not picked:
            bail("no party member is ABLE to learn CUT")
        self.press("A:5 .:80")                        # choose the mon
        # either it learns outright (<4 moves) or asks to delete a move
        for _ in range(20):
            if self._party_knows(move_name)[0]:
                break
            s = scr()
            if "YES" in s and "NO" in s:
                self.press("A:5 .:70")                # YES: delete one
                if forget_move:                       # move list is up
                    want = forget_move.upper()
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
        # postcondition: overworld interactive again, move actually known
        if not self.close_menus():
            raise RuntimeError(f"teach_hm {move_name}: a menu is still "
                               "open after teaching")
        knows, _idx = self._party_knows(move_name)
        if not knows:
            raise RuntimeError(f"teach_hm {move_name}: teaching failed "
                               "verification")
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
                # slot menu: STATS / SWITCH / ITEM / CANCEL
                if not self.menu.select_label("SWITCH", max_presses=6):
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
        hit = False
        # the party list stays visible behind the submenu box, so scan
        # every cursor row -- not just the first one
        def cut_on_cursor():
            rows = [r.strip().upper() for r in self.emu.screen_text()
                    if ("▶" in r or "▷" in r)]
            return any("CUT" in r for r in rows)
        for _ in range(8):
            if cut_on_cursor():
                hit = True
                break
            self.press("D:4 .:16")
        if not hit:
            for _ in range(8):
                self.press("U:4 .:16")
                if cut_on_cursor():
                    hit = True
                    break
        if not hit:
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


    def _wait_screen(self, pred, frames=500):
        """Tick (no input) until pred(uppercase screen text) is true."""
        n = 0
        while n < frames:
            if pred("".join(self.emu.screen_text()).upper()):
                return True
            self.emu.tick(10)
            n += 10
        return False

    def _pocket_select(self, idx, item_name, max_steps=40):
        """Steer the items-pocket cursor to absolute index `idx` and
        confirm with A. The pocket REMEMBERS its cursor between opens
        (pack.asm restores wItemsPocketCursor/wItemsPocketScrollPosition
        into the scrolling menu), so a fresh open can start mid-list:
        top-of-list screen scrapes miss and DOWN-only walks can never
        climb back up (leg-2 'no potion visible' with 2 in the bag).
        Navigate on the live WRAM index (wMenuScrollPosition +
        wMenuCursorY) in BOTH directions, then verify the highlighted
        row's TEXT really is the item before pressing A. The verify
        normalizes BOTH sides (_item_row_matches: case/space/hyphen/POKe
        blind, quantity-digit and edge-clip tolerant) and prefers the
        ACTIVE list cursor row over a stale submenu leftover (wren pt4:
        two-word 'SUPER POTION' never confirmed)."""
        want = _norm_item(item_name)
        last, stuck = None, 0
        for _ in range(max_steps):
            cur = self.menu.scroll_abs()
            if cur == idx:
                break
            stuck = stuck + 1 if cur == last else 0
            if stuck >= 3:
                return False    # cursor pinned: list edge or wrong menu
            last = cur
            self.press("D:6 .:4" if cur < idx else "U:6 .:4")
        else:
            return False
        self.press(".:10")      # let the row repaint before scraping
        scrape = self.menu.cursor_row()
        got = scrape[1] if scrape else None
        if got is not None and _item_row_matches(got, want):
            self.press("A:6 .:18")
            return True
        # cursor_row returns the FIRST glyph row on screen; a stale ▷/▶
        # leftover higher up (submenu remnants, START-menu row) shadows
        # the live selection -- rescan for an ACTIVE ▶ row naming the
        # item before giving up
        for row in self.emu.screen_text():
            x = row.find("▶")
            if x >= 0 and _item_row_matches(row[x + 1:], want):
                self.press("A:6 .:18")
                return True
        log.info(f"  pocket row mismatch: want {item_name!r} "
                 f"(norm {want}), cursor row {got!r}")
        return False        # WRAM/screen disagree: never blind-A

    def _party_target(self, slot, max_steps=12):
        """Steer the party-menu cursor to row `slot` (0-based; eggs count
        as rows) on the live WRAM cursor (wMenuCursorY, 1-based -- the
        same source battle.py steers its in-battle party menu with) and
        confirm with A. The menu persists its cursor between opens and
        REVIVE's fainted-target flow opens on the first ABLE mon, so
        blind press counts from an assumed top row are never safe."""
        last, stuck = None, 0
        for _ in range(max_steps):
            cur = self.emu.read_u8("wMenuCursorY") - 1
            if cur == slot:
                self.press("A:6 .:18")
                return True
            stuck = stuck + 1 if cur == last else 0
            if stuck >= 3:
                return False    # cursor pinned: wrong menu / list edge
            last = cur
            self.press("D:6 .:6" if cur < slot else "U:6 .:6")
        return False

    def use_item(self, item_name, target_slot=0, field=True):
        """Use an item from the pack outside battle (heals/status on party
        member `target_slot`). Returns True if the item was confirmed."""
        e = self.emu
        idx = bag_item_index(e, self.names, item_name, "items")
        if idx is None:
            log.info(f"  no {item_name} in bag")
            return False
        def _start_menu_up(s):
            return "PACK" in s   # START menu row; paints a beat late
        self.press("START:4 .:25")               # open START menu
        if not self._wait_screen(_start_menu_up, 90):
            # Post-warp the START press sometimes lands during the fade;
            # blind D/A presses here WALK THE PLAYER (once onto a ladder).
            # Gotcha 2: the menu input loop isn't running the frame the
            # menu is drawn -- settle, drain stragglers, retry ONCE.
            log.info("  START menu slow to open; settling and retrying")
            self.settle()
            if self.textbox():
                self.flush_dialog()
            self.press("START:4 .:25")
            if not self._wait_screen(_start_menu_up, 90):
                log.warning("  START menu did not open")
                return False
        if not self.menu.select_label("PACK", max_presses=8):
            self.press("B:4 .:10")
            log.info("  could not open PACK")
            return False
        if not goto_pocket(self.menu, "items"):
            cancel_pack(self.menu)
            return False
        before = bag_quantity(e, self.names, item_name)
        if not self._pocket_select(idx, item_name):
            cancel_pack(self.menu)
            log.info(f"  could not put the pocket cursor on {item_name}")
            return False
        # item submenu (USE/GIVE/TOSS/QUIT) pops up after a beat
        if not self.menu.wait_for_label("USE", 300) or \
                not self.menu.select_label("USE", max_presses=4):
            cancel_pack(self.menu)
            log.info(f"  no USE option for {item_name}")
            return False
        # consumption is the only truth: the menus can flow perfectly
        # while a swallowed A used nothing (bag read-back below)
        used = False
        # healing/status items ask for a target party list. Two traps
        # (wren pt3 REVIVE repro: returned False, bag never decremented,
        # while a manual pack drive worked):
        #   * the cursor does NOT start on row 0 -- wPartyMenuCursor
        #     persists between opens, and fainted-target flows (REVIVE)
        #     open on the first ABLE mon -- so blind D-press counts pick
        #     the wrong target ("won't have any effect", nothing used);
        #     steer on the live WRAM row instead;
        #   * the revive jingle + "... came to!" message pace slowly over
        #     a party menu that keeps CANCEL drawn -- gate on the bag
        #     read-back, never on the menu closing.
        have_target = self.menu.wait_for(
            lambda r: any("CANCEL" in x for x in r), timeout_frames=400)
        if have_target:
            if not self._party_target(target_slot):
                log.info(f"  could not put the party cursor on "
                         f"slot {target_slot}")
            else:
                confirms = 0
                f0 = last_a = self.emu.frame
                while self.emu.frame - f0 < 4500:
                    after = bag_quantity(e, self.names, item_name)
                    if after is None or (before is not None
                                         and after < before):
                        used = True
                        break
                    if self.textbox():
                        self.press("A:6 .:18")   # page the item message
                    elif confirms < 3 and self.emu.frame - last_a > 400 \
                            and any("CANCEL" in r
                                    for r in self.emu.screen_text()):
                        # party menus swallow the confirm A during setup
                        # (gotcha 2); an unchanged bag proves nothing was
                        # used yet, so a re-press can't double-consume
                        self.press("A:6 .:18")
                        confirms += 1
                        last_a = self.emu.frame
                    else:
                        self.press(".:20")       # jingle: input is deaf
                if used:
                    self.flush_dialog(3000)
        # close any leftover UI (pack, stat screens) until the field is back
        def _field_clear(rows):
            bad = ("▶", "▷", "CANCEL", "QUIT", "EXIT", "USE", "TOSS")
            return not any(b in r for r in rows for b in bad)
        f0 = self.emu.frame
        while self.emu.frame - f0 < 900 and not _field_clear(self.emu.screen_text()):
            self.press("B:6 .:14")
        return used

    def walk(self, path, label=""):
        """Walk a path like 'L*12 U*3 D'. Handles battles, NPC dialogs, and
        map transitions along the way; reports blocks instead of looping."""
        if label:
            log.info(f"[{label}] from {self.map_name()} {self.pos()[2:]}")
        for token in path.split():
            d, _, n = token.partition("*")
            d, n = d[0].upper(), int(n or 1)
            done = stuck = 0
            while done < n:
                r = self._step(d)
                if r == "battle":
                    self.fight()
                    if self._whiteout_stop(f"walk '{path}'"):
                        return False
                elif r == "warp":
                    self.settle()
                    log.info(f"  -> {self.map_name()} {self.pos()[2:]}")
                    done += 1
                    stuck = 0
                elif r == "moved":
                    done += 1
                    stuck = 0
                else:
                    if self.textbox():
                        self.flush_dialog()
                        continue
                    stuck += 1
                    if stuck == 2:
                        self.press("B:4 .:10")  # close a stray menu, then retry
                    if stuck >= 4:
                        log.warning(f"  BLOCKED {d} at {self.map_name()} {self.pos()[2:]}",
                              )
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

    def goto(self, x, y, label="", map_name=None):
        """BFS-pathfind to (x,y) and walk it. Defaults to the current map;
        pass map_name (CONST_NAME or CamelCase) to route across maps via
        warp events and edge connections. Replans around NPC bumps; fights
        encounters on the way."""
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
                self.last_goto_reason = (
                    f"target ({x},{y}) outside {goal_map} bounds "
                    f"{len(grid[0])}x{len(grid)} -- pass map_name or use "
                    f"travel for cross-map goals")
                log.warning(f"  GAVE UP ({self.last_goto_reason})")
                return False
        entry_map = self.map_name()
        replans = idle = passes = drains = 0
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
                    # unreachable": relaxed (ignore-NPC) routes let
                    # step_dir handle the bumps -- waiting never moves
                    # trainers.
                    path = self.nav.find_path(cur_map, cur, goal)
                    if not path:
                        log.info(f"  no static path {cur_map} {cur} -> "
                              f"{goal}")
                        reason = f"no-path {cur_map} {cur} -> {goal}"
                        self.last_goto_reason = reason
                        return False
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
                        log.info(f"  no static path {cur_map} {cur} -> "
                              f"{goal_map} {goal}")
                        reason = (f"no-path cross-map {cur_map} -> "
                                  f"{goal_map} {goal}")
                        self.last_goto_reason = reason
                        return False
                    replans += 1
                    if replans % 5 == 0:
                        log.info(f"  threading {cur} -> {goal} past NPCs",
                              )
                    path = relaxed
            moved = False
            for mv in path:
                r = self._step(mv)
                if r == "battle":
                    self.fight()
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
        self.last_goto_reason = reason
        log.warning(f"  GAVE UP ({reason}) at {self.map_name()} "
              f"{self.pos()[2:]} -> {goal_map} {goal}")
        return False

    # Deliberate-trip opt-in (FABLE_FEEDBACK failure pattern 5): after
    # confirming from maps/<Map>.asm that a scene script is safe
    # (talk-only, sets scene NOOP), set d.trip_scenes = True for the one
    # goto that must cross its cell, then clear it. Never leave it on.
    trip_scenes = False

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
                if self.trip_scenes:
                    log.info(f"  [trip_scenes] crossing {const} scene cell "
                          f"{(x, y)} unblocked")
                    continue
                cells.add((x, y))
            if cells:
                blocks[const] = cells
        self.nav.blocked = blocks

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
                        f"{nxt['kind'] if nxt else 'transition'} on {cur}")
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
            for _attempt in range(4):
                r = self._step(st["dir"])
                if r == "battle":
                    self.fight()      # encounter mid-transition; then retry
                    if self._whiteout_stop("travel"):
                        raise TravelError(
                            f"leg {i}: wiped mid-travel, auto-healed at "
                            f"{self.map_name()} -- relaunch travel()")
                elif r == "blocked":
                    if self.textbox():
                        # scripted scene on the transition cell: page it
                        # out (bounded); a battle it starts is caught by
                        # the next attempt's _step -> the fight path above
                        self._drain_scene()
                    else:
                        break
                elif r != "warp" and self.map_name() == st["from"]:
                    # stepped but the warp didn't fire. On a multi-warp
                    # door row (Sprout Tower 1F's double door) the held
                    # step GLIDES across every door tile without firing
                    # (gotcha 12); each retry then re-crosses the row
                    # from the other side -- the observed (8,15)<->(11,15)
                    # ping-pong. We are off the modeled approach now, so
                    # drive straight back onto the warp tile instead.
                    if st["kind"] == "warp":
                        fr = self._held_warp_entry(st)
                        if fr == "warp":
                            r = "warp"
                            break
                    continue
                else:
                    break
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
        bypasses the check entirely."""
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
        shop_open = any("¥" in r for r in self.emu.screen_text())
        if not shop_open:
            if self.talk_to(x, y, label or "clerk") != "talked":
                return False
            opened = False
            for _attempt in range(2):
                # the list pops in frames AFTER talk_to's flush returns;
                # wait it out passively -- an A press here buys whatever
                # the cursor sits on (gotcha 13, cost a session 1800 yen)
                for _ in range(20):
                    if any("¥" in r for r in self.emu.screen_text()):
                        opened = True
                        break
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
                self.press("B:4 .:10 .:20")
                raise RuntimeError(
                    f"mart_buy: shop menu did not open at ({x},{y}) -- "
                    f"clerk talk failed twice (registry actions must not "
                    f"fail as a silent log line)")
        for _ in range(40):                       # bounded item search
            rows = self.emu.screen_text()
            cur = self._shop_cursor_row(rows)
            target = next((i for i, r in enumerate(rows)
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
                self.press("A:6 .:30")
                self.flush_dialog(3000)
                bought = True
                break                             # one purchase per call
            if cur < 0:
                break
            self.press("D:6 .:12" if (target is None or target > cur)
                       else "U:6 .:12")
        for _ in range(10):                       # leave the shop cleanly:
            rows = self.emu.screen_text()         # B only -- flush_dialog's
            if not (any("¥" in r or "▶" in r or "▷" in r for r in rows)
                    or self.textbox()):
                break                             # A-mashing buys things!
            self.press("B:6 .:16")
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

def heal_pokecenter(d):
    """From inside any Pokécenter: talk to the nurse, wait out the jingle.
    Verifies the location on entry and the actual heal on exit -- an
    unverified 'healed' claim once masked a failed goto entirely."""
    if "POKECENTER" not in d.map_name():
        raise RuntimeError(
            f"heal_pokecenter: not inside a Pokécenter (on {d.map_name()})")

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

    def _nurse():
        d.goto(3, 3, "nurse counter")
        d.step_dir("U")        # face her (blocked step = turn)
        d.press("A:2 .:20")
        d.flush_dialog()       # intro page(s) -- stops ("menu") at the
        # heal prompt. The YES/NO box is a deliberate choice: cursor
        # defaults to YES, but an extra stray A earlier can leave it on
        # NO (omp-fresh variant), so navigate explicitly.
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
    # success: the player is still ON the counter tile facing the nurse;
    # the next A-bearing routine re-opens her prompt (two leg-2 wedges).
    # Step off south -- every center's counter row is y=3 with open
    # floor below -- and settle so no residual prompt stays armed.
    if d.step_dir("D") != "moved":
        d.step_dir("D")            # first press may only turn in place
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
