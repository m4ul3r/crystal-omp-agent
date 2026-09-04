"""claude-wren pt6: LIVE sprite positions and goto's blocked-by-NPC wait.

wMapObjects is the STATIC map defs -- journaled confusion had pushed
boulders 'reset' in the npcs list. The parser now decodes the live
wObjectStructs table (OBJECT_LENGTH-byte slots, standing-tile map coords,
mid-step sprites report the tile they are stepping INTO), observe()
exposes it as 'sprites', npc_cells()/goto's avoid set derive from it, and
goto waits out WANDER/SPIN blockers (up to Driver.WANDER_WAIT_FRAMES,
re-checking the LIVE position) before giving up -- distinguishing
'blocked-by-stationary-npc' from 'waited-for-wanderer: still blocked'."""
import pytest

from crystalagent.nav import STEP
from crystalagent.driver import Driver
from crystalagent.schemas import validate_observe
from crystalagent.state import (NUM_OBJECT_STRUCTS, OBJECT_LENGTH,
                                SPRITE_WANDERERS, decode_object_structs,
                                live_sprites)

pytestmark = pytest.mark.unit

# SPRITEMOVEDATA_* values (constants/map_object_constants.asm)
STILL, WANDER, SPIN_SLOW = 0x01, 0x02, 0x03
STANDING_DOWN, PLAYER_MV = 0x06, 0x0B


def slot_bytes(sprite=0, movement=0, cell=None, last_cell=None):
    """One OBJECT_LENGTH struct: sprite id +0, movement +3, standing-tile
    map coords (walk cell + 4) at +0x10/+0x11, last tile at +0x12/+0x13."""
    b = bytearray(OBJECT_LENGTH)
    b[0x00] = sprite
    b[0x03] = movement
    if cell is not None:
        b[0x10], b[0x11] = cell[0] + 4, cell[1] + 4
    if last_cell is not None:
        b[0x12], b[0x13] = last_cell[0] + 4, last_cell[1] + 4
    return bytes(b)


def build_buffer(slots):
    """Full wObjectStructs table; empty slots carry garbage coords to
    prove sprite==0 slots are skipped, not decoded."""
    buf = bytearray()
    for i in range(NUM_OBJECT_STRUCTS):
        buf += slots.get(i, slot_bytes(sprite=0, cell=(60, 60)))
    return bytes(buf)


# -- parser -------------------------------------------------------------------

def test_decode_player_plus_two_npcs_one_mid_step():
    buf = build_buffer({
        0: slot_bytes(sprite=1, movement=PLAYER_MV, cell=(10, 5)),
        3: slot_bytes(sprite=0x2F, movement=STILL, cell=(3, 7)),
        # mid-step wanderer: MAP_X/Y already hold the DESTINATION tile
        # (the one that collides); LAST_MAP_X/Y is where it came from
        7: slot_bytes(sprite=0x30, movement=WANDER, cell=(6, 7),
                      last_cell=(5, 7)),
    })
    assert decode_object_structs(buf) == [
        {"slot": 0, "map_x": 10, "map_y": 5, "movement": PLAYER_MV},
        {"slot": 3, "map_x": 3, "map_y": 7, "movement": STILL},
        {"slot": 7, "map_x": 6, "map_y": 7, "movement": WANDER},
    ]


def test_decode_skips_empty_and_survives_short_buffer():
    assert decode_object_structs(b"") == []
    # a truncated trailing slot never half-decodes
    buf = build_buffer({0: slot_bytes(sprite=1, cell=(1, 1)),
                        12: slot_bytes(sprite=2, cell=(9, 9))})[:-1]
    assert [s["slot"] for s in decode_object_structs(buf)] == [0]


class SpriteEmu:
    def __init__(self, buf):
        self.buf = buf
        self.reads = []

    def read(self, sym, length=1):
        self.reads.append((sym, length))
        return self.buf[:length]


def test_live_sprites_reads_the_whole_table_once():
    buf = build_buffer({0: slot_bytes(sprite=1, movement=PLAYER_MV,
                                      cell=(2, 2)),
                        1: slot_bytes(sprite=2, movement=WANDER,
                                      cell=(4, 2))})
    emu = SpriteEmu(buf)
    out = live_sprites(emu)
    assert emu.reads == [("wObjectStructs",
                          NUM_OBJECT_STRUCTS * OBJECT_LENGTH)]
    assert [s["slot"] for s in out] == [0, 1]


def test_driver_sprites_include_player_npc_cells_exclude_it():
    d = Driver.__new__(Driver)
    d.emu = SpriteEmu(build_buffer({
        0: slot_bytes(sprite=1, movement=PLAYER_MV, cell=(2, 2)),
        1: slot_bytes(sprite=2, movement=STILL, cell=(4, 2)),
        5: slot_bytes(sprite=9, movement=WANDER, cell=(1, 8)),
    }))
    assert [s["slot"] for s in d.sprites()] == [0, 1, 5]
    assert d.npc_cells() == {(4, 2), (1, 8)}


def test_observe_schema_accepts_sprites():
    obs = {
        "map": "M", "group": 1, "number": 2, "x": 0, "y": 0,
        "tiles": {}, "party": [], "bag": {}, "money": 0, "badges": [],
        "flags": {}, "npcs": [[4, 2]],
        "sprites": [{"slot": 0, "map_x": 0, "map_y": 0,
                     "movement": PLAYER_MV}],
        "ui": {"textbox": False, "battle": False}, "frame": 0,
    }
    assert validate_observe(obs) is obs


def test_spin_types_count_as_wanderers_standing_types_do_not():
    assert {WANDER, SPIN_SLOW, 0x0A, 0x1E, 0x1F} <= SPRITE_WANDERERS
    assert STILL not in SPRITE_WANDERERS
    assert STANDING_DOWN not in SPRITE_WANDERERS


# -- goto wait path -----------------------------------------------------------

class FakeEmu:
    frame = 0

    def read_u8(self, sym):
        return 0

    def screen_text(self):
        return [" " * 20 for _ in range(18)]


class FakeNav:
    """Single-row corridor: any avoided cell strictly between cur and the
    goal severs the ONLY path; the relaxed (no-avoid) plan always exists."""
    warps = {}
    blocked = {}

    def find_path(self, m, cur, goal, avoid=()):
        corridor = {(x, 0) for x in range(cur[0] + 1, goal[0] + 1)}
        if set(avoid) & corridor:
            return None
        return ["R"] * (goal[0] - cur[0])

    def grid(self, m):
        return [[0] * 32 for _ in range(18)]


def wait_driver(npcs):
    """Player at (0,0) on a one-row corridor; `npcs` are the live sprites
    the fake exposes. Stepping into an occupied cell blocks."""
    d = Driver.__new__(Driver)
    d.emu = FakeEmu()
    d.settle = lambda **kw: None
    d.auto_fight = True
    world = {"map": "TEST_MAP", "cell": (0, 0), "sprites": list(npcs),
             "presses": []}
    d._world = world
    d.map_name = lambda: world["map"]
    d.pos = lambda: (0, 0) + world["cell"]
    d.nav = FakeNav()
    d._refresh_nav_blocks = lambda: None
    d._resolve_map = lambda name: world["map"] if name is None else name
    d._is_warp_cell = lambda x, y: False
    d.textbox = lambda: False
    d.menu_open = lambda: False
    d.flush_dialog = lambda *a, **k: "done"
    d.sprites = lambda: [dict(s) for s in world["sprites"]]
    d.press = lambda seq: world["presses"].append(
        int(seq.rsplit(":", 1)[1]))

    def _step(mv):
        x, y = world["cell"]
        dx, dy = STEP[mv]
        tgt = (x + dx, y + dy)
        if tgt in {(s["map_x"], s["map_y"])
                   for s in world["sprites"] if s["slot"]}:
            return "blocked"
        world["cell"] = tgt
        return "moved"

    d._step = _step
    return d, world


def vacate_after_chunks(d, world, n):
    """The sprite steps off after the n-th wait-window re-check (presses
    of WANDER_WAIT_CHUNK+ frames; the courtesy .:40 nudges don't count)."""
    base_press, seen = d.press, {"n": 0}

    def press(seq):
        base_press(seq)
        if int(seq.rsplit(":", 1)[1]) >= Driver.WANDER_WAIT_CHUNK:
            seen["n"] += 1
            if seen["n"] >= n:
                world["sprites"] = []

    d.press = press
    return seen


def test_goto_waits_out_wanderer_on_path_then_arrives():
    """Replan storm against a WANDER sprite squatting the only corridor
    cell: goto waits, the sprite moves after 3 re-checks, goto replans
    and reaches the target."""
    d, world = wait_driver([{"slot": 1, "map_x": 1, "map_y": 0,
                             "movement": WANDER}])
    seen = vacate_after_chunks(d, world, 3)
    assert d.goto(2, 0) is True
    assert world["cell"] == (2, 0)
    assert seen["n"] == 3                     # freed on the third re-check
    assert d.last_goto_reason is None         # success leaves no diagnosis


def test_stationary_blocker_on_path_distinguished_no_wait_burn():
    d, world = wait_driver([{"slot": 1, "map_x": 1, "map_y": 0,
                             "movement": STANDING_DOWN}])
    assert d.goto(2, 0) is False
    assert d.last_goto_reason.startswith("blocked-by-stationary-npc")
    # standing types never move: the 600-frame window is NOT burned
    assert all(f < Driver.WANDER_WAIT_CHUNK for f in world["presses"])


def test_wanderer_that_never_moves_reports_wait_exhausted():
    d, world = wait_driver([{"slot": 1, "map_x": 1, "map_y": 0,
                             "movement": SPIN_SLOW}])
    assert d.goto(2, 0) is False
    assert d.last_goto_reason.startswith(
        "waited-for-wanderer: still blocked")
    waited = sum(f for f in world["presses"]
                 if f >= Driver.WANDER_WAIT_CHUNK)
    assert waited >= Driver.WANDER_WAIT_FRAMES


def test_goal_occupied_by_wanderer_waits_then_arrives():
    d, world = wait_driver([{"slot": 2, "map_x": 2, "map_y": 0,
                             "movement": WANDER}])
    vacate_after_chunks(d, world, 2)
    assert d.goto(2, 0) is True
    assert world["cell"] == (2, 0)


def test_goal_occupied_by_stationary_npc_distinguished():
    d, world = wait_driver([{"slot": 2, "map_x": 2, "map_y": 0,
                             "movement": STILL}])
    assert d.goto(2, 0) is False
    assert d.last_goto_reason.startswith("blocked-by-stationary-npc")
    assert "(2, 0)" in d.last_goto_reason


def test_unidentifiable_blocker_keeps_legacy_target_occupied():
    """Sprite table unreadable (old fakes, torn reads): goto falls back
    to the pre-existing 'target-occupied' diagnosis instead of waiting."""
    d, world = wait_driver([{"slot": 2, "map_x": 2, "map_y": 0,
                             "movement": WANDER}])

    def boom():
        raise AttributeError("no object structs on this fake")

    d.sprites = boom
    d.npc_cells = lambda: {(2, 0)}   # occupancy still visible
    assert d.goto(2, 0) is False
    assert "target-occupied" in d.last_goto_reason
