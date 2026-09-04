"""Water HMs go through the overworld A press, never the party menu.

FUCK_I_MESSED_UP.md #75 (which retracts #70): surfing at TOHJO_FALLS
(9,12) facing UP at a 0x33 COLL_WATERFALL tile, with WATERFALL learned and
RISINGBADGE in hand, START -> POKéMON -> GOLDEEN -> WATERFALL answered
"Can't use that here." while a single plain A press from the identical tile
and facing answered "Do you want to use WATERFALL?" and climbed
(9,12) -> (9,7).

The engine's A handler dispatches on the FACED TILE
(engine/overworld/events.asm:1085-1125 -> TryWhirlpoolOW /
TryWaterfallOW), which is why the button works where the menu does not.
Live-verified for WHIRLPOOL at DRAGONS_DEN_B1F (10,20) -- the same tile
#70 wrongly wrote off as decorative.
"""
import pytest

import crystalagent.driver.inventory as inventory_driver
from crystalagent.driver import Driver

pytestmark = pytest.mark.unit


class FakeEmu:
    def __init__(self):
        self.frame = 0
        self.rows = [" " * 20 for _ in range(18)]
        self.u8 = {"wPlayerDirection": 0x4}      # UP

    def tick(self, n=1):
        self.frame += n

    def screen_text(self):
        return list(self.rows)

    def read_u8(self, name):
        return self.u8.get(name, 0)


def rows(*lines):
    out = [" " * 20 for _ in range(18)]
    for i, text in lines:
        out[i] = text.ljust(20)[:20]
    return out


ASK = rows((14, "Do you want to use"), (8, "▶YES"), (10, " NO"))


def field_driver(monkeypatch, tiles, knowers=None, badges=("RISING",),
                 pos=(9, 12)):
    d = Driver.__new__(Driver)
    d.emu = FakeEmu()
    d.names = None
    d.presses = []
    d.tiles = dict(tiles)
    d.settle = lambda **kw: None
    d.close_menus = lambda **kw: True
    d.sync_grid = lambda: []
    d.field_moves = lambda: dict(knowers if knowers is not None
                                 else {"WATERFALL": "GOLDEEN",
                                       "WHIRLPOOL": "TENTACOOL"})
    d.tile_at = lambda x, y: d.tiles.get((x, y), "water")
    d.pos = lambda: (0, 0) + tuple(d._pos)
    d._pos = list(pos)
    d.textbox = lambda: any("Do you want" in r for r in d.emu.screen_text())
    monkeypatch.setattr(inventory_driver, "game_state", lambda emu, names: {
        "player": {"johto_badges": list(badges)}})

    def press(seq):
        d.presses.append(seq)
        d.emu.tick(5)
    d.press = press
    return d


# -- facing ------------------------------------------------------------------

def test_facing_decodes_wplayerdirection():
    """DOWN 0, UP 4, LEFT 8, RIGHT 12 -- direction << 2, the encoding
    CheckMapCanWaterfall masks with $c."""
    d = Driver.__new__(Driver)
    d.emu = FakeEmu()
    for raw, want in ((0x0, "D"), (0x4, "U"), (0x8, "L"), (0xC, "R")):
        d.emu.u8["wPlayerDirection"] = raw
        assert d.facing() == want


# -- refusals, all before the A press ---------------------------------------

def test_refuses_a_move_with_no_knower(monkeypatch):
    d = field_driver(monkeypatch, {(9, 11): "waterfall"},
                     knowers={"WATERFALL": None})
    assert d.use_field_move("WATERFALL") is False
    assert d.last_field_reason.startswith("no-knower")
    assert d.presses == []


def test_refuses_without_the_badge_the_engine_checks(monkeypatch):
    d = field_driver(monkeypatch, {(9, 11): "waterfall"}, badges=("GLACIER",))
    assert d.use_field_move("WATERFALL") is False
    assert d.last_field_reason.startswith("no-badge")
    assert d.presses == []


def test_refuses_when_the_faced_cell_is_not_the_obstacle(monkeypatch):
    d = field_driver(monkeypatch, {(9, 11): "water"})
    assert d.use_field_move("WATERFALL") is False
    assert "wrong-tile" in d.last_field_reason
    assert "'water'" in d.last_field_reason      # says what it IS
    assert d.presses == []


def test_refuses_a_move_with_no_overworld_arm(monkeypatch):
    d = field_driver(monkeypatch, {(9, 11): "waterfall"})
    assert d.use_field_move("FLY") is False
    assert d.last_field_reason.startswith("unknown-move")


# -- the A path --------------------------------------------------------------

def test_waterfall_answers_the_prompt_and_verifies_by_position(monkeypatch):
    d = field_driver(monkeypatch, {(9, 11): "waterfall"})
    real = d.press

    def press(seq):
        real(seq)
        if seq.startswith("A") and d.emu.rows == ASK:
            d._pos[1] = 7                        # climbed (9,12) -> (9,7)
            d.emu.rows = [" " * 20 for _ in range(18)]
        elif seq.startswith("A"):
            d.emu.rows = ASK                     # "Do you want to use ...?"
    d.press = press
    assert d.waterfall() is True
    assert d.last_field_reason == "used"
    assert d.pos()[2:] == (9, 7)


def test_whirlpool_verifies_by_the_tile_it_dissolved(monkeypatch):
    """WHIRLPOOL does not move the player: DisappearWhirlpool rewrites the
    block, so the proof is the live grid (sync_grid + tile_at)."""
    d = field_driver(monkeypatch, {(9, 13): "whirlpool"},
                     badges=("GLACIER", "RISING"))
    d.emu.u8["wPlayerDirection"] = 0x0           # facing DOWN
    real = d.press

    def press(seq):
        real(seq)
        if seq.startswith("A") and d.emu.rows == ASK:
            d.tiles[(9, 13)] = "water"
            d.emu.rows = [" " * 20 for _ in range(18)]
        elif seq.startswith("A"):
            d.emu.rows = ASK
    d.press = press
    assert d.whirlpool() is True
    assert d.tile_at(9, 13) == "water"
    assert d.pos()[2:] == (9, 12)                # never moved


def test_no_prompt_is_reported_and_menus_are_closed(monkeypatch):
    """A field move that fails leaves its menu open, and an open menu eats
    all movement input (AGENTS.md gotcha 17)."""
    d = field_driver(monkeypatch, {(9, 11): "waterfall"})
    closed = []
    d.close_menus = lambda **kw: closed.append(True) or True
    assert d.waterfall() is False
    assert d.last_field_reason.startswith("no-prompt")
    assert closed == [True]
