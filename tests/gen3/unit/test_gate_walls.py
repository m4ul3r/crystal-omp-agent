"""A gate that fires is not a gate that blocks.

Both cases here are real and both cost a run. The desert must stay a wall or
the pathfinder walks at sandstorm forever; the rival ambush must NOT be a wall
or Route 119 is severed at a two-cell corridor -- and the north half of Route
119 holds the Weather Institute, so every plan collapsed to "no walkable
route" while the player stood still at 99% CPU.

These assert against the decompilation's own scripts, which is the only
authority on what a script does to the player.
"""

import pytest

from pokeagent import gates


@pytest.mark.unit
def test_desert_gate_displaces_the_player():
    """Route 111 without GO-GOGGLES: message, then walk the player back."""
    # `displaces_player` is the SCRIPT-SHAPE question -- does this thing walk
    # the player -- and is what these two tests are about. Whether it is a
    # wall RIGHT NOW is `GateReader.blocks`, which also weighs the guard.
    walls = [g for g in gates.gates_for_map("Route111")
             if gates.displaces_player(g.map_name, g.script)]
    assert (11, 61) in [(g.x, g.y) for g in walls]


@pytest.mark.unit
def test_rival_ambush_is_a_scene_not_a_wall():
    """Route 119's rival fights you and releases; it moves you nowhere.

    Its only player movement is Common_Movement_WalkInPlaceFastestDown, which
    turns the player on the spot. Counting that as displacement is what
    severed the map.
    """
    ambush = [g for g in gates.gates_for_map("Route119") if (g.x, g.y) == (25, 31)]
    assert ambush, "the (25,31) coord_event should still exist in the map data"
    assert not gates.displaces_player(ambush[0].map_name, ambush[0].script)


@pytest.mark.unit
def test_walking_in_place_is_not_displacement():
    """The discriminator itself, stated directly."""
    assert not gates.displaces_player(
        "Route119", "Route119_EventScript_1511C5"
    )


@pytest.mark.unit
def test_a_pushback_two_gotos_away_is_still_found():
    """Route 111's push-back sits behind `goto` + `call_if_eq`.

    A single-level scan reads that script as harmless, which would open the
    desert and hang the walk. The chain has to be followed.
    """
    assert gates.displaces_player("Route111", "Route111_EventScript_150116")


@pytest.mark.unit
def test_the_desert_gate_names_the_item_it_wants():
    """Route 111's guard is a BAG check, not a var.

    `Gate.var` is None for it, which `is_closed` reads as "unconditional" --
    i.e. shut forever. That walled off the desert, and with it BOTH fossils,
    while the GO-GOGGLES sat in the Key Items pocket. Measured from
    (13,138): 762 cells reachable and y stopping at exactly 61; clearing the
    ten gate cells took the same fill to 2229 with both fossils in reach.
    """
    desert = [g for g in gates.gates_for_map("Route111")
              if gates.displaces_player(g.map_name, g.script)]
    assert desert, "Route 111 should still have its push-back gates"
    wanted = {gates.required_item(g.map_name, g.script) for g in desert}
    assert wanted == {"ITEM_GO_GOGGLES"}


@pytest.mark.unit
def test_a_gate_stops_being_a_wall_once_its_item_is_held():
    class FakeNames:
        def item(self, item_id):
            return "GO-GOGGLES"

    class FakeConsts:
        items = {"ITEM_GO_GOGGLES": 279}

    class FakeState:
        def __init__(self, held):
            self.names = FakeNames()
            self.consts = FakeConsts()
            self._held = held

        def bag(self):
            return {"key_items": {"GO-GOGGLES": 1}} if self._held else {}

        def var(self, name):
            raise KeyError(name)

    desert = [g for g in gates.gates_for_map("Route111")
              if gates.displaces_player(g.map_name, g.script)][0]

    assert gates.GateReader(FakeState(False)).blocks(desert) is True
    assert gates.GateReader(FakeState(True)).blocks(desert) is False
