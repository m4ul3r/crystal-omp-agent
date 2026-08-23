#!/usr/bin/env python3
"""Journey driver: reusable primitives for long play sessions, run as legs
in a single persistent process (no per-command emulator reload).

Usage: .venv/bin/python trek.py <leg> [args]   (see main() dispatch)
"""

import json
import sys
from collections import deque
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from crystalagent import paths
from crystalagent.battle import Battle, BattleData, bag_item_index, cancel_pack, goto_pocket, _norm_item
from crystalagent.charmap import Charmap
from crystalagent.emu import Crystal, parse_sequence
from crystalagent.menus import Menus, battle_menu_up
from crystalagent.names import Names
from crystalagent.nav import MapData, STEP, WARPS, WALKABLE, HOPS, CONN_NAME
from crystalagent.state import game_state, status_line
from crystalagent.symfile import Symbols

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
                    w = self.warps.get(M, {}).get((nx, ny))
                    if w is not None:
                        # stepping ONTO a warp tile fires it (arrival never
                        # re-triggers, so landing on a warp tile is fine --
                        # gate doors land on exactly such tiles); you can
                        # never stand on one mid-path otherwise
                        if cross:
                            land = self._warp_landing(M, (nx, ny))
                            if land:
                                exits.append(((land[0], land[1]), d))
                        continue
                    c = grid[ny][nx]
                    if c in WALKABLE or c in WARPS or c in HOPS:
                        plain.append((nxt, d))
                    elif c in _SIDE_WALL_BLOCKED and \
                            d not in _SIDE_WALL_BLOCKED[c]:
                        # one-way wall: enterable from every facing except
                        # its blocked side(s)
                        plain.append((nxt, d))
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

    # -- observations ------------------------------------------------------

    def pos(self):
        e = self.emu
        return (e.read_u8("wMapGroup"), e.read_u8("wMapNumber"),
                e.read_u8("wXCoord"), e.read_u8("wYCoord"))

    def map_name(self):
        g, n, _, _ = self.pos()
        return self.names.maps.get((g, n), f"?{g}:{n}")

    def battle(self):
        return self.emu.read_u8("wBattleMode")

    def textbox(self):
        return self.emu.tilemap()[12 * 20] == 0x79

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
            n = min(e.read_u8(count_sym), 20)
            if not n:
                continue
            bank, addr = e.sym[list_sym]
            raw = e.read((bank, addr), n * 2)
            for i in range(n):
                name = _norm_item(self.names.items.get(raw[i * 2],
                                                       f"?{raw[i * 2]}"))
                bag[name] = bag.get(name, 0) + raw[i * 2 + 1]
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
        return {
            "map": loc["map"], "group": loc["map_group"],
            "number": loc["map_number"], "x": loc["x"], "y": loc["y"],
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

    def _step(self, mv):
        """step_dir, but switch to a held step when the target cell is a
        warp tile so doors actually trigger."""
        x, y = self.pos()[2:]
        dx, dy = STEP[mv]
        if self._is_warp_cell(x + dx, y + dy):
            return self.step_hold(mv)
        return self.step_dir(mv)

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

    def dismiss_keyboard(self, name=None):
        """Confirm a naming screen. With a name, actually type it; without,
        confirm with the minimal name (fast path)."""
        if name:
            print(f"  naming keyboard: typing {name!r}", flush=True)
            self.type_name(name)
            return
        print("  naming keyboard: confirming", flush=True)
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
        print(f"  typing name {''.join(chars)!r}", flush=True)
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

    def flush_dialog(self, max_frames=6000, quiet_frames=40):
        """Press A while a textbox is up; return once it's been gone a bit.
        Handles a naming keyboard if one appears."""
        f0, quiet = self.emu.frame, 0
        while self.emu.frame - f0 < max_frames:
            if self.battle():
                return "battle"
            if self.textbox():
                self.press("A:2 .:8")
                quiet = 0
            elif self.keyboard_open():
                self.dismiss_keyboard()
                quiet = 0
            else:
                self.press(".:8")
                quiet += 8
                if quiet >= quiet_frames:
                    return "done"
        return "timeout"

    def fight(self, max_frames=90000, policy=None):
        """Play a battle out with real move selection (best expected
        damage, auto-POTION at low HP, flee hopeless wilds). Pauses at a
        naming keyboard (post-catch nickname prompt) to type
        self._pending_nickname if one is set."""
        if not self.battle():
            return self.lead()
        self._resolve_learn_flow()   # repair a wedged mid-learn state
        f0 = self.emu.frame
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
        self._resolve_learn_flow(4000)   # sweep post-battle leftovers
        self.flush_dialog(3000)
        self.emu.save(self.state_path)   # keep watch.py near-live mid-leg
        lead = self.lead()
        print(f"  battle [{outcome}, {self.emu.frame - f0} frames] -> "
              f"{lead['name']} L{lead['level']} {lead['hp']}/{lead['max_hp']}",
              flush=True)
        return lead


    _LEARN_MARKERS = ("TRYING TO LEARN", "WANTS TO LEARN",
                      "DELETE A MOVE", "FORGET A MOVE", "MAKE ROOM",
                      "STOP LEARNING")

    def _learn_prompt_up(self, rows):
        joined = "".join(rows).upper()
        return any(m in joined for m in self._LEARN_MARKERS)

    def _battle_text_handler(self, rows):
        """Modal-text hook for Battle.play: drive the level-up move-learning
        flow to a deterministic DECLINE (keep the current moveset). The
        flow is a two-stage prompt: "Delete a move and make room?"
        (answer NO) -> "Stop learning <MOVE>?" (answer YES -- the trap:
        B here means "don't stop" and loops the flow forever). Blind
        A-mashing derails into party menus and wedges the battle
        (Bugsy gym, Scyther 0 HP, wBattleMode stuck at 2).
        Returns True when this frame's input was consumed."""
        if not self._learn_prompt_up(rows):
            return False
        joined = "".join(rows).upper()
        if "YES" in joined and "NO" in joined:
            if "STOP LEARNING" in joined:
                self.press("A:6 .:20")   # YES: confirm stopping
            else:
                self.press("B:6 .:20")   # NO: keep the current moveset
        else:
            self.press("A:4 .:16")       # advance the flow's text pages
        return True

    def _resolve_learn_flow(self, max_frames=8000):
        """Drive any on-screen move-learning flow to completion (declining
        the swap). Used to repair wedged states and sweep post-battle
        leftovers; safe to call when no flow is present."""
        f0 = self.emu.frame
        while self.emu.frame - f0 < max_frames:
            rows = self.emu.screen_text()
            if not self._learn_prompt_up(rows):
                return True
            self._battle_text_handler(rows)
        return False


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

    # -- field HM: CUT -----------------------------------------------------

    _CUT_TREE_BYTE = 0x12

    def _party_knows_cut(self):
        """(knows, party_index): does any party member know CUT?"""
        for idx, mon in enumerate(self.observe()["party"]):
            if any(m.get("name") == "CUT" for m in mon.get("moves", [])):
                return True, idx
        return False, None

    def _teach_hm01(self):
        """Teach HM01 CUT to the first able party member via PACK ->
        TM/HM pocket. Raises RuntimeError if the flow doesn't complete."""
        self.press("START:4 .:40")
        self.press("D:4 .:12"); self.press("D:4 .:12")
        self.press("A:4 .:60")                       # PACK
        for _ in range(8):
            if self.emu.read_u8("wJumptableIndex") == 8:
                break                                 # TM/HM pocket
            self.press("L:4 .:18")
        else:
            raise RuntimeError("use_cut: TM/HM pocket never opened")
        self.press(".:35")
        self.press("D:4 .:15"); self.press("D:4 .:25")
        self.press("A:4 .:80")                        # submenu
        self.press(".:40")
        if "USE" not in "".join(self.emu.screen_text()).upper():
            raise RuntimeError("use_cut: HM01 USE submenu not found")
        self.press("A:6 .:30")                        # USE
        for _ in range(20):                           # boot texts -> YES/NO
            s = "".join(self.emu.screen_text()).upper()
            if "YES" in s and "NO" in s:
                break
            self.press("A:4 .:45")
        else:
            raise RuntimeError("use_cut: learn prompt never appeared")
        self.press("A:5 .:60")                        # YES: teach
        self.press("A:5 .:80")                        # first able mon
        self.press("A:5 .:90")                        # forget first move
        for _ in range(14):
            if not self.textbox():
                break
            self.press("A:4 .:50")
        self.press("B:4 .:25"); self.press("B:4 .:30")
        knows, _idx = self._party_knows_cut()
        if not knows:
            raise RuntimeError("use_cut: teaching HM01 failed verification")

    def use_cut(self, tree_x, tree_y, label=""):
        """Cut down the small tree at (tree_x, tree_y) on the current map:
        teaches HM01 CUT via the pack flow if nobody knows it yet, walks to
        a standable cell beside the tree, faces it, and uses START ->
        POKéMON -> mon -> field-move CUT. Verifies the tree's collision
        actually cleared and steps onto its cell."""
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
            print("  no one knows CUT; teaching HM01", flush=True)
            self._teach_hm01()
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
        for (ax, ay), face in cands:
            if self.goto(ax, ay, label or "use_cut approach"):
                self.press(f"{face}:4 .:10")
                placed = True
                break
        if not placed:
            raise RuntimeError(
                f"use_cut: no reachable approach beside the tree "
                f"({tree_x}, {tree_y})")

        # START -> POKEMON -> (knower or first mon) -> field-move CUT row
        self.press("START:4 .:40")
        if not self._wait_screen(lambda s: "EXIT" in s):
            raise RuntimeError("use_cut: START menu never opened")
        self.press("D:4 .:15")                        # POKEMON entry
        self.press("A:5 .:40")
        if not self._wait_screen(lambda s: "CANCEL" in s and
                                 ("ABLE" in s or "EGG" in s)):
            raise RuntimeError("use_cut: party list never opened")
        if knower:
            for _ in range(knower):
                self.press("D:4 .:15")                # cursor onto the mon
        self.press("A:6 .:40")
        if not self._wait_screen(lambda s: "STATS" in s and "SWITCH" in s):
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
        # verify by walking onto the former tree cell (the static grid
        # still shows $12 -- cut trees are swapped only in the engine's
        # block memory)
        r = self._step(face)
        if self.pos()[2:] != (tree_x, tree_y):
            raise RuntimeError(
                f"use_cut: tree at {(tree_x, tree_y)} still standing after "
                f"CUT (step {r} -> {self.pos()[2:]})")
        print(f"  [cut] tree at {(tree_x, tree_y)} removed; stepped {r} "
              f"-> {self.map_name()} {self.pos()[2:]}", flush=True)
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

    def use_item(self, item_name, target_slot=0, field=True):
        """Use an item from the pack outside battle (heals/status on party
        member `target_slot`). Returns True if the item was confirmed."""
        e = self.emu
        idx = bag_item_index(e, self.names, item_name, "items")
        if idx is None:
            print(f"  no {item_name} in bag", flush=True)
            return False
        self.press("START:4 .:25")               # open START menu
        if not self.menu.select_label("PACK", max_presses=8):
            self.press("B:4 .:10")
            print("  could not open PACK", flush=True)
            return False
        if not goto_pocket(self.menu, "items"):
            cancel_pack(self.menu)
            return False
        if not self.menu.select_abs(idx):
            cancel_pack(self.menu)
            return False
        # item submenu (USE/GIVE/TOSS/QUIT) pops up after a beat
        if not self.menu.wait_for_label("USE", 300) or \
                not self.menu.select_label("USE", max_presses=4):
            cancel_pack(self.menu)
            print(f"  no USE option for {item_name}", flush=True)
            return False
        used = True
        # healing/status items ask for a target ("Use on which PM?");
        # the party menu swallows the first A during setup, so press
        # until it actually closes
        have_target = self.menu.wait_for(
            lambda r: any("CANCEL" in x for x in r), timeout_frames=400)
        if have_target:
            steps = 0
            while steps < target_slot and \
                    any("CANCEL" in r for r in self.emu.screen_text()):
                self.press("D:6 .:6")
                steps += 1
            f0 = self.emu.frame
            while any("CANCEL" in r for r in self.emu.screen_text()):
                if self.emu.frame - f0 > 1200:
                    return False   # menu refuses to close: something's off
                self.press("A:6 .:18")
            self.flush_dialog(3000)
        else:
            used = False   # submenu confirmed but nothing happened
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
            print(f"[{label}] from {self.map_name()} {self.pos()[2:]}", flush=True)
        for token in path.split():
            d, _, n = token.partition("*")
            d, n = d[0].upper(), int(n or 1)
            done = stuck = 0
            while done < n:
                r = self._step(d)
                if r == "battle":
                    self.fight()
                elif r == "warp":
                    self.settle()
                    print(f"  -> {self.map_name()} {self.pos()[2:]}", flush=True)
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
                        print(f"  BLOCKED {d} at {self.map_name()} {self.pos()[2:]}",
                              flush=True)
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
        entry_map = self.map_name()
        replans = idle = passes = 0
        if label or goal_map != self.map_name():
            print(f"[goto {goal}"
                  f"{'' if goal_map == self.map_name() else ' -> ' + goal_map}]"
                  f"{' ' + label if label else ''}".rstrip(), flush=True)
        while replans < 20 and idle < 40 and passes < 60:
            passes += 1
            cur_map, cur = self.map_name(), self.pos()[2:]
            if exit_warp_goal:
                if cur_map != entry_map:
                    print(f"  -> left through warp {goal}", flush=True)
                    return True
            elif cur_map == goal_map and cur == goal:
                return True
            # a warp-tile goal fires on arrival: standing at that warp's
            # landing cell means we walked through it (e.g. goto on a
            # Pokécenter door ends inside, not stuck bouncing on the mat)
            land = (self.nav.warps.get(goal_map, {}).get(goal)
                    and self.nav._warp_landing(goal_map, goal))
            if land and land[0] == cur_map and \
                    abs(cur[0] - land[1][0]) + abs(cur[1] - land[1][1]) <= 2:
                print(f"  -> arrived through warp {goal}", flush=True)
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
                        print(f"  no static path {cur_map} {cur} -> "
                              f"{goal}", flush=True)
                        return False
                    replans += 1
                    if replans % 5 == 1:
                        print(f"  threading {cur} -> {goal} past NPCs",
                              flush=True)
            else:
                path = self.nav.find_route(cur_map, cur, goal_map, goal,
                                           avoid)
                if not path:
                    relaxed = self.nav.find_route(cur_map, cur, goal_map,
                                                  goal)
                    if not relaxed:
                        print(f"  no static path {cur_map} {cur} -> "
                              f"{goal_map} {goal}", flush=True)
                        return False
                    self.press(".:40")  # beat for genuinely moving NPCs
                    replans += 1
                    if replans % 5 == 0:
                        print(f"  threading {cur} -> {goal} past NPCs",
                              flush=True)
                    path = relaxed
            moved = False
            for mv in path:
                r = self._step(mv)
                if r == "battle":
                    self.fight()
                    moved = True
                elif r == "warp":
                    self.settle()
                    print(f"  -> {self.map_name()} {self.pos()[2:]}", flush=True)
                    moved = True
                    # step_hold keeps the key down through the transition,
                    # so the player glides past the modeled landing cell;
                    # replan from the live position rather than trust the
                    # rest of the precomputed path
                    break
                elif r == "moved":
                    moved = True
                elif r == "blocked":
                    print(f"  blocked {mv} at {self.map_name()} "
                          f"{self.pos()[2:]}"
                          f"{' [textbox]' if self.textbox() else ''}",
                          flush=True)
                    if self.textbox():
                        self.flush_dialog()
                    else:
                        self.press(".:40")  # let a wandering NPC step aside
                    replans += 1
                    break
            else:
                continue   # path exhausted; loop re-checks arrival/replans
            if not moved:
                idle += 1
        print(f"  GAVE UP at {self.map_name()} {self.pos()[2:]} -> "
              f"{goal_map} {goal}", flush=True)
        return False

    # -- cross-map routing (edge source: data/mapgraph.json) ----------------

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
                if fires and script_is_disruptive(self.nav._repo, camel,
                                                  script):
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

    def route(self, dest_map):
        """Plan-only cross-map route to `dest_map`: BFS over mapgraph.json's
        validated warp/connection edges, expanded into per-leg steps --
        [{"kind": "walk", "map", "x", "y"}, {"kind": "warp"|"connection",
        "from", "to", "dir", ...}, ...]. Raises LookupError on unreachable;
        never moves the player."""
        self._refresh_nav_blocks()
        dest = self._resolve_map(dest_map)
        src = self.map_name()
        if dest == src:
            return []
        adj = self._mg_edges()
        prev = {src: None}
        q = deque([src])
        while q:
            m = q.popleft()
            if m == dest:
                break
            for e in sorted(adj.get(m, ()),
                            key=lambda e: (e["to_map"], e["kind"],
                                           json.dumps(e["cells"]))):
                nxt = e["to_map"]
                if nxt in prev:
                    continue
                cands = self._edge_steps(e)
                if cands:
                    prev[nxt] = (m, e, cands)
                    q.append(nxt)
        if dest not in prev:
            raise LookupError(f"no routable mapgraph path {src} -> {dest}")
        hops, m = [], dest
        while prev[m]:
            hops.append(prev[m])
            m = prev[m][0]
        steps = []
        for frm, e, cands in reversed(hops):
            (ax, ay), d = cands[0]
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
        return steps

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
        that edge's other approaches."""
        dest = self._resolve_map(dest_map)
        self._refresh_nav_blocks()
        if self.map_name() == dest:
            return []
        steps = self.route(dest)
        print(f"[travel -> {dest}] {len(steps)} steps from "
              f"{self.map_name()} {self.pos()[2:]}"
              f"{' ' + label if label else ''}".rstrip(), flush=True)
        _edge_counts = {}
        for i, st in enumerate(steps):
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
                    continue
                # this approach may sit on the far side of a one-way ledge
                # or wall -- fall back to the edge's other approaches
                for alt in alts:
                    if [alt["x"], alt["y"]] == [st["x"], st["y"]]:
                        continue
                    print(f"  approach {(st['x'], st['y'])} unreachable; "
                          f"trying {alt['dir']} from "
                          f"{(alt['x'], alt['y'])}", flush=True)
                    if self.goto(alt["x"], alt["y"], f"travel -> {dest}"):
                        nxt["dir"] = alt["dir"]
                        break
                else:
                    raise TravelError(
                        f"leg {i}: no path to any approach of the next "
                        f"{nxt['kind'] if nxt else 'transition'} on {cur}")
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
                elif r == "blocked":
                    if self.textbox():
                        self.flush_dialog()
                    else:
                        break
                elif r != "warp" and self.map_name() == st["from"]:
                    continue          # stepped but the warp didn't fire
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
            print(f"  -> {here} {(mx, my)} (drift {drift})", flush=True)
            if here == dest:
                return steps      # landed on the destination: done
        return steps

    def grind(self, pace="D U", target_level=13, min_hp=7, max_battles=80):
        """Pace in grass fighting encounters until target level / low HP."""
        battles = 0

        def done():
            lead = self.lead()
            if lead["level"] >= target_level:
                return "leveled"
            if lead["hp"] <= min_hp:
                return "low-hp"
            return None

        while battles < max_battles:
            stop = done()
            if stop:
                return stop
            for token in pace.split():
                d, _, n = token.partition("*")
                moved = 0
                while moved < int(n or 1):
                    r = self.step_dir(d[0].upper())
                    if r == "battle":
                        self.fight()
                        battles += 1
                        stop = done()   # stop mid-pace, don't wander on
                        if stop:
                            return stop
                        break
                    elif r == "moved":
                        moved += 1
        return "max-battles"

    def _standable(self, name, c):
        """Path-existence is not enough: cross-map BFS treats any goal as
        reachable (warp tiles, counters). Standing spots must be real."""
        try:
            grid = self.nav.grid(name)
            if 0 <= c[0] < len(grid[0]) and 0 <= c[1] < len(grid):
                return grid[c[1]][c[0]] in WALKABLE or grid[c[1]][c[0]] in HOPS
            return False
        except KeyError:
            return False

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

    def talk_to(self, x, y, label=""):
        """Walk next to the NPC at (x,y) (or across a counter from them),
        face them, and talk. Fights any trainer battle that triggers
        (sight-lines are slow: polls for wBattleMode after the dialog).
        Returns 'battle' | 'talked' | False."""
        if self.battle():
            self.fight()
        self.settle()
        spot = self._approach_cell(x, y)
        if spot is None:
            print(f"  no approach to ({x},{y})", flush=True)
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
            return "battle"
        return "talked"

    def save(self, name=None):
        target = Path(paths.SAVES_DIR) / name if name else self.state_path
        self.emu.save(target)
        if name:  # also update the working state
            self.emu.save(self.state_path)
        print(f"[saved {target.name}] {self.status()}", flush=True)

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
        want = _norm_item(item_name)
        shop_open = any("¥" in r for r in self.emu.screen_text())
        if not shop_open:
            if self.talk_to(x, y, label or "clerk") != "talked":
                return False
            if not any("¥" in r for r in self.emu.screen_text()):
                print("  shop menu did not open", flush=True)
                self.press("B:4 .:10")
                return False
        bought = False
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
                    print("  no quantity picker", flush=True)
                    break
                def picker_qty():
                    for s in self.emu.screen_text():
                        if "×" in s:
                            try:
                                return int(s.split("×")[1].split()[0])
                            except (IndexError, ValueError):
                                return None
                    return None

                tries = 0
                while tries < 20:                # UP adds one; presses can
                    if picker_qty() in (qty, None):  # be swallowed early
                        break
                    self.press("U:4 .:14")
                    tries += 1
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
        print(f"  mart_buy {item_name} x{qty}: "
              f"{'ok' if ok else 'FAILED'} ({before} -> {after})", flush=True)
        return ok


# -- legs -------------------------------------------------------------------

def heal_pokecenter(d):
    """From inside any Pokécenter: talk to the nurse, wait out the jingle."""
    d.goto(3, 3, "nurse counter")
    d.step_dir("U")            # face her (blocked step = turn)
    d.press("A:2 .:20")
    d.flush_dialog()           # "shall we heal?" -> A = yes
    d.press(".:300")           # heal jingle
    d.flush_dialog()           # "we hope to see you again"
    lead = d.lead()
    print(f"  healed: {lead['name']} {lead['hp']}/{lead['max_hp']}", flush=True)


def leg_to_violet(d):
    """Cherrygrove Pokecenter -> Route 30 -> Route 31 -> Violet City."""
    d.goto(3, 7, "pokecenter door")
    d.walk("D", "exit pokecenter")
    d.goto(16, 0, "city north exit")
    d.walk("U", "cross to Route 30")
    d.goto(5, 0, "route 30 north end")     # BFS threads the ledges/trainers
    d.walk("U", "cross to Route 31")
    d.goto(4, 6, "route 31 gate")
    print(f"  now in {d.map_name()} {d.pos()[2:]}", flush=True)


def leg_errand1(d):
    """Route 30 -> Mr. Pokemon's house: receive the Mystery Egg + Pokedex."""
    d.goto(17, 5, "Mr. Pokemon's door")
    d.flush_dialog(2000)
    d.goto(3, 6, "approach Mr. Pokemon")  # he stands at (3,5)
    d.step_dir("U")
    d.press("A:2 .:20")
    d.flush_dialog(30000)                # egg + Oak + Pokedex: very long
    print(f"  done: {d.map_name()} {d.pos()[2:]}", flush=True)


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
    print(f"  now in {d.map_name()} {d.pos()[2:]}", flush=True)


def leg_route29(d):
    # From Route 29 grass (44,10) west to Cherrygrove. Path along y=8-10.
    d.walk("U*2", "back to path")           # out of grass to y=8
    d.walk("L*18", "route 29 west")         # long straight, trees at gaps
    print(d.status())

def env_flag(name):
    import os
    return os.environ.get(name, "").strip().lower() not in ("", "0", "no",
                                                             "false")


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        sys.exit("usage: trek.py <leg> [<state>] [args...]\n"
             "legs: walk PATH | goto X Y [MAP] | talk X Y | "
             "grind [PACE] [LEVEL] | catch [NAME] | fight |\n"
             "      flush | heal | route MAP | travel MAP |\n"
             "      route29 | to_violet |\n"
             "      errand1 errand2 errand3 errand4 violet\n"
             "goto MAP: CONST_NAME or CamelCase (e.g. VIOLET_CITY) -- routes\n"
             "across maps via warps + edge connections\n"
             "<state>: savestate path ('' or omitted = saves/default.state)")
    leg, rest = argv[0], list(argv[1:])
    spec = {
        "walk": (1, 1), "goto": (2, 3), "talk": (2, 2),
        "route": (1, 1), "travel": (1, 1),
        "mart": (4, 4),
        "fight": (0, 0), "flush": (0, 0), "route29": (0, 0), "heal": (0, 0),
        "to_violet": (0, 0), "errand1": (0, 0), "errand2": (0, 0),
        "errand3": (0, 0), "errand4": (0, 0), "violet": (0, 0),
    }
    arity = spec.get(leg)
    if arity is None:
        sys.exit(f"unknown leg {leg!r}; legs: {', '.join(sorted(spec))}")
    lo, hi = arity
    # state path comes right after the leg: '' = default, or a *.state file;
    # anything else is the leg's first real argument
    state_arg = None
    if rest and (rest[0] == "" or rest[0].endswith(".state")):
        state_arg = rest.pop(0) or None
    if not lo <= len(rest) <= hi:
        usage = {"walk": "PATH", "goto": "X Y [MAP]", "talk": "X Y",
                 "grind": "[PACE] [LEVEL]", "mart": "X Y ITEM QTY",
                 "route": "MAP", "travel": "MAP"}.get(leg, "")
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
    if leg == "walk":
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
    elif leg == "grind":
        gargs = [rest[0], int(rest[1])] if len(rest) > 1 else rest
        print(d.grind(*gargs), flush=True)
    elif leg == "catch":
        d.catch(nickname=rest[0] if rest else None)
    elif leg == "fight":
        d.fight()
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
    if leg != "route":   # route is a pure plan: don't rewrite the state
        d.save()
    print(f"[end] {d.status()}", flush=True)


if __name__ == "__main__":
    main()
