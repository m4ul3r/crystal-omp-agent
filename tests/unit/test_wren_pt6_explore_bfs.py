"""explore_bfs (session claude-wren pt6): savestate BFS promoted into the
driver after being hand-rolled 10+ times this run (ice slides, Rocket
base, Tohjo Falls).

Synthetic grid world over a fake PyBoy: '#' wall, '.' floor, '~' ice
(slides carry the avatar across with NO input -- resolved by the settle
poll, not by step_dir), 'B' one-shot battle cell, 'T' one-shot forced
sign (textbox on arrival), 'W' warp to another map. Savestates are
pickled world dicts, so reloading a parent snapshot really does restore
consumed battles/signs -- the same contract the live emulator gives the
primitive."""
import pickle
from io import BytesIO
from types import SimpleNamespace

import pytest

from trek import Driver

pytestmark = pytest.mark.unit

_DIRS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}


class FakePy:
    """PyBoy stand-in driving a tick-based grid world with real
    wPlayerStepFlags protocol (0x80 step started / 0x40 step stopped),
    multi-cell ice slides, battle/sign cells, warps, and picklable
    save/load state. The frame counter is monotonic (never restored):
    explore budgets meter WORK, not world time."""

    STEP_FRAMES = 4

    def __init__(self, maps, start, warps=None):
        self.maps = maps            # {map_id: [row strings]}
        self.warps = warps or {}    # {(map_id, x, y): (map_id, x, y)}
        self._frame = 0
        self.state = {
            "map": start[0], "x": start[1], "y": start[2],
            "battle": 0, "textbox": False, "used": frozenset(),
            "step_flags": 0, "in_flight": None, "held": set(),
        }

    # -- PyBoy surface -------------------------------------------------------

    def save_state(self, f):
        f.write(pickle.dumps(self.state))

    def load_state(self, f):
        self.state = pickle.loads(f.read())

    def button_press(self, b):
        self.state["held"].add(b)

    def button_release(self, b):
        self.state["held"].discard(b)

    def tick(self, frames=1, render=False):
        for _ in range(int(frames)):
            self._frame += 1
            self._advance()

    # -- world mechanics -----------------------------------------------------

    def _cell(self, m, x, y):
        grid = self.maps[m]
        if 0 <= y < len(grid) and 0 <= x < len(grid[y]):
            return grid[y][x]
        return "#"

    def _enterable(self, m, x, y):
        return self._cell(m, x, y) != "#"

    def _advance(self):
        st = self.state
        if st["battle"] or st["textbox"]:
            return
        if st["in_flight"]:
            dx, dy, left = st["in_flight"]
            left -= 1
            if left > 0:
                st["in_flight"] = (dx, dy, left)
                st["step_flags"] = 0x80
                return
            st["x"] += dx
            st["y"] += dy
            st["in_flight"] = None
            st["step_flags"] = 0x40
            self._arrive(dx, dy)
            return
        for b, (dx, dy) in _DIRS.items():
            if b in st["held"]:
                if self._enterable(st["map"], st["x"] + dx, st["y"] + dy):
                    st["in_flight"] = (dx, dy, self.STEP_FRAMES)
                    st["step_flags"] = 0x80
                else:
                    st["step_flags"] = 0
                return
        st["step_flags"] = 0

    def _arrive(self, dx, dy):
        st = self.state
        key = (st["map"], st["x"], st["y"])
        cell = self._cell(*key)
        if cell == "B" and key not in st["used"]:
            st["used"] |= {key}
            st["battle"] = 1
        elif cell == "T" and key not in st["used"]:
            st["used"] |= {key}
            st["textbox"] = True
        elif cell == "W":
            st["map"], st["x"], st["y"] = self.warps[key]
        elif cell == "~":
            # ice: glide on in the same direction with no input
            if self._enterable(st["map"], st["x"] + dx, st["y"] + dy):
                st["in_flight"] = (dx, dy, self.STEP_FRAMES)
                st["step_flags"] = 0x80

    # -- WRAM ----------------------------------------------------------------

    def read_u8(self, name):
        st = self.state
        return {
            "wMapGroup": 0, "wMapNumber": st["map"],
            "wXCoord": st["x"], "wYCoord": st["y"],
            "wBattleMode": st["battle"],
            "wPlayerStepFlags": st["step_flags"],
        }.get(name, 0)


class FakeEmu:
    """The slice of crystalagent.emu.Crystal the primitive touches."""

    def __init__(self, py):
        self.py = py

    @property
    def frame(self):
        return self.py._frame

    def tick(self, frames=1):
        self.py.tick(frames, False)

    def read_u8(self, name):
        return self.py.read_u8(name)

    def tilemap(self):
        tm = [0] * 360
        if self.py.state["textbox"]:
            tm[240] = 0x79          # textbox() checks wTilemap[12*20]
        return tm

    def screen_text(self):
        return [""] * 18

    def run_sequence(self, steps):
        f0 = self.py._frame
        for buttons, frames in steps:
            if "a" in buttons and self.py.state["textbox"]:
                self.py.state["textbox"] = False
            self.py.tick(frames, False)
            if buttons:
                self.py.tick(2, False)
        return self.py._frame - f0


MAP_NAMES = {(0, 0): "MAP_ZERO", (0, 1): "MAP_ONE"}


def bfs_driver(maps, start, warps=None):
    # single-map tests pass a bare list of rows; FakePy keys maps by id
    if isinstance(maps, list):
        maps = {0: maps}
    py = FakePy(maps, start, warps)
    d = Driver.__new__(Driver)
    d.emu = FakeEmu(py)
    d.names = SimpleNamespace(maps=MAP_NAMES)
    return d, py


def at(map_id, x, y):
    return lambda d: d.pos() == (0, map_id, x, y)


# -- core: finds a shortest path, loads the winning state --------------------

def test_finds_three_move_goal():
    d, py = bfs_driver(["#####",
                        "#...#",
                        "###.#",
                        "#####"], (0, 1, 1))
    res = d.explore_bfs(at(0, 3, 2))
    assert res["found"] is True
    assert res["steps"] == 3                      # R R D -- BFS depth
    assert isinstance(res["state"], bytes) and res["state"]
    # the winning state is LOADED
    assert d.pos() == (0, 0, 3, 2)
    # and the blob is a genuine savestate of that same moment
    py.load_state(BytesIO(res["state"]))
    assert d.pos() == (0, 0, 3, 2)


def test_goal_at_start_is_zero_steps():
    d, _ = bfs_driver(["###",
                       "#.#",
                       "###"], (0, 1, 1))
    res = d.explore_bfs(at(0, 1, 1))
    assert res["found"] is True and res["steps"] == 0
    assert isinstance(res["state"], bytes)


def test_not_found_reloads_start():
    d, _ = bfs_driver(["#####",
                       "#..##",
                       "#####"], (0, 1, 1))
    res = d.explore_bfs(at(0, 9, 9))
    assert res["found"] is False and res["state"] is None
    assert res["visited"] == 2                    # (1,1) and (2,1)
    assert d.pos() == (0, 0, 1, 1)                # root restored


# -- slides: one settled move crosses many cells ------------------------------

def test_slide_multi_cell_is_one_move():
    d, py = bfs_driver(["#######",
                        "#.~~~.#",
                        "#######"], (0, 1, 1))
    res = d.explore_bfs(at(0, 5, 1))
    assert res["found"] is True
    assert res["steps"] == 1                      # R slides (1,1)->(5,1)
    assert d.pos() == (0, 0, 5, 1)


def test_slide_intermediate_cells_never_become_nodes():
    # goal unreachable: every expansion of R settles at (5,1), never on ice
    d, _ = bfs_driver(["#######",
                       "#.~~~.#",
                       "#######"], (0, 1, 1))
    res = d.explore_bfs(at(0, 3, 1))              # mid-ice cell
    assert res["found"] is False
    assert res["visited"] == 2                    # (1,1) and (5,1) only


# -- forced signs mid-path -----------------------------------------------------

def test_textbox_cell_answered_with_a():
    d, py = bfs_driver(["######",
                        "#..T.#",
                        "######"], (0, 1, 1))
    res = d.explore_bfs(at(0, 4, 1))
    assert res["found"] is True and res["steps"] == 3
    assert py.state["textbox"] is False           # sign was answered, not wedged


# -- battles -------------------------------------------------------------------

def battle_map():
    return ["######",
            "#..B.#",
            "######"]


def test_battle_intercept_fought_through():
    d, py = bfs_driver(battle_map(), (0, 1, 1))
    fights = []

    def fake_fight(max_frames=90000, policy=None):
        fights.append(d.pos())
        py.state["battle"] = 0
        return {"name": "GATOR"}

    d.fight = fake_fight
    res = d.explore_bfs(at(0, 4, 1))              # on_battle='fight' default
    assert res["found"] is True and res["steps"] == 3
    assert fights                                  # the intercept was played out
    assert d.pos() == (0, 0, 4, 1)


def test_battle_skip_abandons_branch():
    d, py = bfs_driver(battle_map(), (0, 1, 1))
    d.fight = lambda **kw: pytest.fail("on_battle='skip' must never fight")
    res = d.explore_bfs(at(0, 4, 1), on_battle="skip")
    assert res["found"] is False
    assert res["visited"] == 2                    # start + (2,1); B branch dead
    assert d.pos() == (0, 0, 1, 1)                # root restored
    assert d.battle() == 0                        # ...and it is not mid-battle


# -- forbidden maps: goal-checked on entry, never expanded ---------------------

WARP_MAPS = {0: ["#####",
                 "#..W#",
                 "#####"],
             1: ["#####",
                 "#...#",
                 "#####"]}
WARP_EDGES = {(0, 3, 1): (1, 1, 1)}


def test_warp_reaches_other_map():
    d, _ = bfs_driver(WARP_MAPS, (0, 1, 1), warps=WARP_EDGES)
    res = d.explore_bfs(at(1, 3, 1))
    assert res["found"] is True
    assert res["steps"] == 4                      # R, R(warp), R, R
    assert d.map_name() == "MAP_ONE" and d.pos()[2:] == (3, 1)


def test_forbid_maps_blocks_expansion():
    d, _ = bfs_driver(WARP_MAPS, (0, 1, 1), warps=WARP_EDGES)
    res = d.explore_bfs(at(1, 3, 1), forbid_maps=("MAP_ONE",))
    assert res["found"] is False                  # deeper cells unreachable
    assert d.pos() == (0, 0, 1, 1)                # root restored


def test_forbid_maps_still_evaluates_goal_on_entry():
    # the warp landing itself IS the goal: mid-move map change must be an
    # evaluation point even when the destination map is forbidden
    d, _ = bfs_driver(WARP_MAPS, (0, 1, 1), warps=WARP_EDGES)
    res = d.explore_bfs(at(1, 1, 1), forbid_maps=("MAP_ONE",))
    assert res["found"] is True and res["steps"] == 2
    assert d.map_name() == "MAP_ONE"


# -- budgets -------------------------------------------------------------------

def open_field(w=12, h=12):
    return ["#" * w] + ["#" + "." * (w - 2) + "#"] * (h - 2) + ["#" * w]


def test_caps_at_max_nodes():
    d, _ = bfs_driver(open_field(), (0, 5, 5))
    res = d.explore_bfs(at(0, 99, 99), max_nodes=10)
    assert res["found"] is False
    assert res["visited"] <= 10
    assert d.pos() == (0, 0, 5, 5)                # root restored


def test_caps_at_max_moves():
    d, py = bfs_driver(open_field(), (0, 5, 5))
    moves = []
    real = Driver._explore_settled_move

    def counting(mv, on_battle):
        moves.append(mv)
        return real(d, mv, on_battle)

    d._explore_settled_move = counting
    res = d.explore_bfs(at(0, 99, 99), max_moves=7)
    assert res["found"] is False
    assert len(moves) == 7                        # blocked bumps count too


# -- staircase tiles: held keys bounce off, taps fire them --------------------

class StairPy(FakePy):
    """'S' cells warp only when entered by a TAP (button released before
    the step completes) -- pokecrystal's COLL_STAIRCASE behaviour, which
    made Victory Road's inter-floor stairs invisible to a held search."""

    def _arrive(self, dx, dy):
        st = self.state
        key = (st["map"], st["x"], st["y"])
        if self._cell(*key) == "S":
            if not st["held"]:                 # tapped: the warp fires
                st["map"], st["x"], st["y"] = self.warps[key]
            else:                              # held: pushed back off
                st["x"] -= dx
                st["y"] -= dy
            return
        super()._arrive(dx, dy)


def stair_driver(maps, start, warps):
    py = StairPy(maps, start, warps)
    d = Driver.__new__(Driver)
    d.emu = FakeEmu(py)
    d.names = SimpleNamespace(maps=MAP_NAMES)
    return d, py


def test_staircase_reached_only_via_tap_fallback():
    maps = {0: ["#####",
                "#.S.#",
                "#####"],
            1: ["#####",
                "#...#",
                "#####"]}
    d, _ = stair_driver(maps, (0, 1, 1), {(0, 2, 1): (1, 2, 1)})
    res = d.explore_bfs(lambda dr: dr.pos()[1] == 1)
    assert res["found"] is True
    assert d.pos() == (0, 1, 2, 1)


def test_plain_wall_still_blocked_after_tap_retry():
    d, py = stair_driver({0: ["###",
                              "#.#",
                              "###"]}, (0, 1, 1), {})
    res = d.explore_bfs(lambda dr: dr.pos() == (0, 0, 9, 9), max_moves=8)
    assert res["found"] is False
    assert d.pos() == (0, 0, 1, 1)        # start state restored


# -- reach(): goto first, savestate search when the grid lies ----------------

def test_reach_prefers_goto_and_skips_the_search():
    d, _ = bfs_driver(["###", "#.#", "###"], (0, 1, 1))
    calls = {"goto": 0, "bfs": 0}

    def goto(x, y, label=""):
        calls["goto"] += 1
        return True
    d.goto = goto
    d.explore_bfs = lambda *a, **k: calls.__setitem__("bfs", 1) or {"found": False}
    assert d.reach(2, 1) is True
    assert calls == {"goto": 1, "bfs": 0}


def test_reach_falls_back_to_search_when_goto_lies():
    """goto reports failure (wrong static grid) but the cell IS walkable:
    the savestate search finds it and reach() returns True."""
    d, _ = bfs_driver(["#####", "#...#", "#####"], (0, 1, 1))
    d.goto = lambda x, y, label="": False
    d.last_goto_reason = "unexplained blocked step"
    assert d.reach(3, 1) is True
    assert d.pos() == (0, 0, 3, 1)


def test_reach_reports_false_when_truly_unreachable():
    d, _ = bfs_driver(["###", "#.#", "###"], (0, 1, 1))
    d.goto = lambda x, y, label="": False
    d.last_goto_reason = "unreachable"
    assert d.reach(9, 9, budget=12, nodes=8) is False
