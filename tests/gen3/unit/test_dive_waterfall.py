"""DIVE and WATERFALL: the guards, and the tile rules they come from.

Both are needed to finish the game and the dex -- Sootopolis, the Seafloor
Cavern, Ever Grande and every underwater species sit behind them -- and both
are the same shape as the field moves that already burned this project: an
A-press on a specific tile, with the engine silently refusing anything else.

The rules, so the constants below are auditable rather than transcribed:

* `MetatileBehavior_IsDiveable` (src/metatile_behavior.c:927-935): semi-deep
  0x11, unused-deep 0x12, Sootopolis-deep 0x14.
* `MetatileBehavior_IsNotSurfacable` (:937-944): no-surfacing 0x19, seaweed
  0x2A -- an underwater ceiling you may not come up through.
* `MB_WATERFALL` 0x13 (:1062-1068).
* `TrySetDiveWarp` (src/field_control_avatar.c:917-935) acts on the tile the
  player is STANDING on, and `SetDiveWarp` (src/overworld.c:583-600) passes
  (x, y) straight through -- you surface or sink at the same coordinates.
* `GetInteractedWaterScript` (src/field_control_avatar.c:503-517) climbs a
  waterfall only with badge 8, already surfing, and facing NORTH.
"""

import pytest

from pokeagent import nav as nav_mod

pytestmark = pytest.mark.unit


def test_the_tile_constants_match_the_engine_predicates():
    """Named from the header, not guessed -- a wrong byte here is a silent
    refusal the driver would report as 'wrong-tile' forever."""
    assert nav_mod.DIVEABLE == {0x11, 0x12, 0x14}
    assert nav_mod.NO_SURFACING == {0x19, 0x2A}
    assert nav_mod.WATERFALL == 0x13
    # A waterfall is NOT diveable and not a surfacing blocker; overlapping
    # these sets would make one mechanic shadow the other.
    assert nav_mod.WATERFALL not in nav_mod.DIVEABLE
    assert nav_mod.WATERFALL not in nav_mod.NO_SURFACING


def test_dive_refuses_before_pressing_anything():
    """A field move that presses A on the wrong tile leaves a message box
    open, and an open box eats every later movement input. So each refusal
    must happen BEFORE the press and name itself."""
    from pokeagent.trek import Driver

    class FakeState:
        def __init__(self, badge7=True): self._b7 = badge7
        def flag(self, name): return self._b7 if name == "FLAG_BADGE07_GET" else False

    class FakeEmu:
        def __init__(self): self.pressed = []
        def run_sequence(self, seq): self.pressed.append(seq)

    def driver(badge7=True, knows=True, behavior=0x11, underwater=False):
        d = Driver.__new__(Driver)
        d.state = FakeState(badge7)
        d.emu = FakeEmu()
        d.field_moves = lambda: {"DIVE": "SEA BIRD" if knows else None}
        d.map_name = lambda: "Underwater2" if underwater else "Route126"
        d.pos = lambda: (10, 10)
        cell = type("C", (), {"behavior": behavior, "kind": "water"})()
        d.nav = type("N", (), {"cell": staticmethod(lambda *a: cell)})()
        return d

    # No badge: refuse, and press NOTHING.
    d = driver(badge7=False)
    assert d.dive() is False
    assert d.last_field_reason == "no-badge"
    assert d.emu.pressed == []

    # Badge but nobody knows the move.
    d = driver(knows=False)
    assert d.dive() is False
    assert d.last_field_reason == "no-knower"
    assert d.emu.pressed == []

    # Standing on ordinary water is not standing on DEEP water.
    d = driver(behavior=0x10)
    assert d.dive() is False
    assert d.last_field_reason == "wrong-tile"
    assert d.emu.pressed == []

    # Underwater under a seaweed ceiling: cannot surface here.
    d = driver(behavior=0x2A, underwater=True)
    assert d.dive() is False
    assert d.last_field_reason == "no-surfacing-here"
    assert d.emu.pressed == []


def test_waterfall_refuses_unless_surfing_north_at_a_waterfall():
    from pokeagent.trek import Driver

    class FakeState:
        def __init__(self, badge8=True): self._b8 = badge8
        def flag(self, name): return self._b8 if name == "FLAG_BADGE08_GET" else False

    class FakeEmu:
        def __init__(self): self.pressed = []
        def run_sequence(self, seq): self.pressed.append(seq)

    def driver(badge8=True, knows=True, surfing=True, faced=0x13):
        d = Driver.__new__(Driver)
        d.state = FakeState(badge8)
        d.emu = FakeEmu()
        d.field_moves = lambda: {"WATERFALL": "SEA BIRD" if knows else None}
        d.is_surfing = lambda: surfing
        d.map_name = lambda: "Route119"
        d.pos = lambda: (18, 26)
        cell = type("C", (), {"behavior": faced})()
        d.nav = type("N", (), {"cell": staticmethod(lambda *a: cell)})()
        return d

    d = driver(badge8=False)
    assert d.climb_waterfall() is False
    assert d.last_field_reason == "no-badge"
    assert d.emu.pressed == []

    d = driver(knows=False)
    assert d.climb_waterfall() is False
    assert d.last_field_reason == "no-knower"
    assert d.emu.pressed == []

    # On foot at the base of a waterfall: the engine will not offer the climb.
    d = driver(surfing=False)
    assert d.climb_waterfall() is False
    assert d.last_field_reason == "not-surfing"
    assert d.emu.pressed == []

    # Surfing north at ordinary water is not a waterfall.
    d = driver(faced=0x10)
    assert d.climb_waterfall() is False
    assert d.last_field_reason == "wrong-tile"
    assert d.emu.pressed == []


def test_nav_only_climbs_northward_and_only_when_able():
    """A waterfall is a wall in every direction but north, and a wall
    entirely until badge 8 -- otherwise the planner routes down waterfalls
    and through cliffs it cannot use."""
    from pokeagent.nav import MapData

    nav = MapData.__new__(MapData)
    nav.surfing = False
    nav.waterfall = False
    nav.blocked = {}

    wall = type("C", (), {"passable": False, "behavior": nav_mod.WATERFALL,
                          "elevation": 3, "collision": 1, "kind": "blocked"})()
    land = type("C", (), {"passable": True, "behavior": 0, "elevation": 3,
                          "collision": 0, "kind": "floor"})()
    nav.cell = lambda m, x, y: wall if (x, y) == (5, 4) else land
    nav._dir_blocked = lambda a, b, d: False
    nav._is_ledge = lambda c, d: False
    nav._is_water = lambda c: False

    # Cannot climb without the badge, even facing north.
    assert nav.step("M", 5, 5, 3, "U") is None
    nav.waterfall = True
    # Now north works...
    assert nav.step("M", 5, 5, 3, "U") == (5, 4, 3)
    # ...and the same tile approached from above still is not a road.
    nav.cell = lambda m, x, y: wall if (x, y) == (5, 6) else land
    assert nav.step("M", 5, 5, 3, "D") is None


def test_a_mon_with_no_damaging_pp_is_recognised_as_useless():
    """The zombie lead, observed live.

    ROCKY sat at L43 with PROTECT 0, MUD-SLAP 0, HEADBUTT 0 and HARDEN 17 --
    full HP, slot 0, and completely unable to damage anything. It led every
    encounter, spent three turns on a zero-power move, was retired for
    changing nothing, and got switched out. Every wild battle, indefinitely,
    because nothing in the loop treated PP as a reason to visit a Centre.

    `power` comes from the ROM's own move table, so a status move is
    recognised without a hardcoded list of names.
    """
    import sys
    sys.path.insert(0, "scripts")
    import play as playmod

    class Move:
        def __init__(self, power): self.power = power

    POWER = {1: 0, 2: 70, 3: 0}      # 1 = status, 2 = damaging, 3 = status

    class Names:
        @staticmethod
        def move_data(mid): return Move(POWER[mid])

    class Mon:
        def __init__(self, moves, pp, hp=100, egg=False):
            self.moves, self.pp, self.hp, self.is_egg = moves, pp, hp, egg

    sess = playmod.Session.__new__(playmod.Session)
    sess.d = type("D", (), {"names": Names()})()

    # The zombie: a damaging move at 0 PP, a status move with PP to spare.
    assert sess._out_of_offence(Mon([2, 1, 0, 0], [0, 17, 0, 0])) is True
    # Same moveset with PP on the damaging move is fine.
    assert sess._out_of_offence(Mon([2, 1, 0, 0], [5, 17, 0, 0])) is False
    # A STATUS-ONLY MOVESET IS NOT A PP PROBLEM. It is permanent, and a nurse
    # cannot touch it -- so it must NOT read as "out of offence", which is
    # what sends the run to a Centre. A caught CASCOON knowing only HARDEN at
    # 30/30 healed on a loop for an hour at full HP because this said True.
    assert sess._out_of_offence(Mon([1, 3, 0, 0], [20, 20, 0, 0])) is False
    # It is still useless, and `_unarmed` is what says so.
    assert sess._unarmed(Mon([1, 3, 0, 0], [20, 20, 0, 0])) is True
    assert sess._unarmed(Mon([2, 1, 0, 0], [0, 17, 0, 0])) is False
    # A fainted mon is not a PP problem, and an egg is not a mon.
    assert sess._out_of_offence(Mon([2, 0, 0, 0], [0, 0, 0, 0], hp=0)) is False
    assert sess._out_of_offence(Mon([2, 0, 0, 0], [0, 0, 0, 0], egg=True)) is False
