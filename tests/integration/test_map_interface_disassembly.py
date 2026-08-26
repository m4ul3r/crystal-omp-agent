"""Integration: the map interface agrees with the disassembly.

Two classifiers that can drift apart is the bug this pins: `exits()`
answers off nav's parsed warp_events/connections while `tile_at()` /
`observe()['tiles']` answer off the decoded collision grid. If either
drifts from maps/*.asm or from its sibling, decisions made from
coordinates go into walls -- the class of miscount that cost three
walks-into-walls in one session (AGENTS.md gotcha 11).

Forked savestate: claude_saves/wren-kanto.state (boots in VERMILION_CITY,
next to water so the neighbour kinds are not all 'floor').
"""

import pytest

pytestmark = pytest.mark.integration

STEP = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}


def test_exits_match_disassembled_warp_events(fork_driver):
    """VERMILION_PORT_PASSAGE exits must contain the two north-edge
    warps to VERMILION CITY, sourced from the disassembly itself:

        maps/VermilionPortPassage.asm:23 warp_event 15, 0, VERMILION_CITY, 8
        maps/VermilionPortPassage.asm:24 warp_event 16, 0, VERMILION_CITY, 9

    (This exact exit was once found only by grepping the asm after
    map_view art miscounted the column.)"""
    d = fork_driver("wren-kanto")
    warps = {(e["x"], e["y"]): e["to"]
             for e in d.exits("VERMILION_PORT_PASSAGE")
             if e["kind"] == "warp"}
    assert warps.get((15, 0)) == "VERMILION_CITY", warps
    assert warps.get((16, 0)) == "VERMILION_CITY", warps


def test_tile_at_agrees_with_observe_tiles(fork_driver):
    """tile_at() and observe()['tiles'] claim to share one classifier;
    all four neighbours of the booted position must agree absolutely --
    not approximately, since both feed goto/take_warp decisions."""
    d = fork_driver("wren-kanto")
    px, py = d.pos()[2:]
    tiles = d.observe()["tiles"]
    for mv, (dx, dy) in STEP.items():
        want = d.tile_at(px + dx, py + dy)
        got = tiles[mv.lower()]
        assert want == got, \
            f"{mv} neighbour ({px + dx},{py + dy}): tile_at={want} " \
            f"observe={got}"
    assert tiles["here"] == d.tile_at(px, py)
