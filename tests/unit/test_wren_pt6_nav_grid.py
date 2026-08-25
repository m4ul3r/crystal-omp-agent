"""Rocket Hideout grid ground truth (session claude-wren pt6, Leg 5b).

Live play reported B2F/B1F static renders "off-by-rows, phantom walls".
Diagnosis: the block->cell decode has NO offset -- grid() was verified
byte-exact against the built ROM (TeamRocketBaseB2F_Blocks at 2b:791c and
TilesetFacilityColl at 37:6d90 match maps/TeamRocketBaseB2F.blk and
data/tilesets/facility_collision.asm) and against the engine's own lookup
(home/map.asm GetCoordTileCollision: table + block*4 + (y&1)*2 + (x&1),
tilecoll args db'd in order per gfx/tilesets.asm). What actually misled
live play were EVENT-CONDITIONAL cells: script `changeblock` doors render
as permanent walls. Those are now exposed via MapData.conditional() /
cell_kind() == 'conditional' / render() '?'.

One journaled claim is corrected here rather than asserted: "B2F row 16
x1..x28 walkable" is overbroad at exactly x==6. Block (3,8) is $3e
(tilecoll WALL,FLOOR,WALL,WALL), a pipe pillar occupying (6,15)-(6,17):
confirmed in the ROM bytes AND by rendering facility_metatiles.bin +
facility.png (vertical pipe tiles $50/$52 in the block's left column).
No changeblock targets that block, so it cannot have been floor live.
"""
from pathlib import Path

import pytest

from crystalagent.nav import MapData, WALKABLE, WARPS

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
POKECRYSTAL = ROOT.parent
needs_disasm = pytest.mark.skipif(
    not (POKECRYSTAL / "constants" / "map_constants.asm").exists(),
    reason="pokecrystal disassembly not present")

FLOOR, WALL = 0x00, 0x07
B2F = "TEAM_ROCKET_BASE_B2F"


@pytest.fixture(scope="module")
def nav():
    return MapData(POKECRYSTAL)


@needs_disasm
def test_b2f_dimensions(nav):
    grid = nav.grid(B2F)
    assert (len(grid[0]), len(grid)) == (30, 18)   # 15x9 blocks


@needs_disasm
def test_b2f_transmitter_door_split(nav):
    # Live ground truth: (14,13)/(15,13) floor, (14,12)/(15,12) blocked
    # until EVENT_OPENED_DOOR_TO_ROCKET_HIDEOUT_TRANSMITTER. Block (7,6)
    # is $1f (WALL,WALL,FLOOR,FLOOR); changeblock 14,12,$07 opens the top.
    grid = nav.grid(B2F)
    assert grid[13][14] == FLOOR and grid[13][15] == FLOOR
    assert grid[12][14] == WALL and grid[12][15] == WALL


@needs_disasm
def test_b2f_door_cells_are_conditional_not_wall(nav):
    cond = nav.conditional(B2F)
    assert cond[(14, 12)] == (FLOOR, WALL)
    assert cond[(15, 12)] == (FLOOR, WALL)
    # the always-floor bottom half of the door block is NOT conditional
    assert (14, 13) not in cond and (15, 13) not in cond
    assert nav.cell_kind(B2F, 14, 12) == "conditional"
    assert nav.cell_kind(B2F, 15, 12) == "conditional"
    assert nav.cell_kind(B2F, 14, 13) == "floor"


@needs_disasm
def test_b2f_render_marks_conditional(nav):
    row12 = nav.render(B2F)[12]
    assert row12[14] == "?" and row12[15] == "?"


@needs_disasm
def test_b2f_bottom_corridor_row16(nav):
    # Walkable x1..x28 EXCEPT the x==6 pipe pillar (block $3e; see module
    # docstring -- the journal's "x1..x28" was verified without ever
    # stepping the pillar cell, both sides being reachable around it).
    grid = nav.grid(B2F)
    blocked = {x for x in range(1, 29) if grid[16][x] not in WALKABLE}
    assert blocked == {6}
    assert grid[15][6] == WALL and grid[17][6] == WALL   # full pillar
    # and none of row 16 is event-conditional
    assert not any((x, 16) in nav.conditional(B2F) for x in range(30))


@needs_disasm
def test_b2f_electrode_room_upper_wall(nav):
    # Live ground truth: standing (22,13), the up-neighbor is blocked.
    grid = nav.grid(B2F)
    assert grid[13][22] == FLOOR
    assert grid[12][22] == WALL


@needs_disasm
def test_b3f_boss_door_conditional(nav):
    # changeblock 10, 8, $07 (EVENT_OPENED_DOOR_TO_GIOVANNIS_OFFICE):
    # only the door block's LOWER half actually changes byte.
    cond = nav.conditional("TEAM_ROCKET_BASE_B3F")
    assert cond[(10, 9)] == (FLOOR, WALL)
    assert cond[(11, 9)] == (FLOOR, WALL)


@needs_disasm
def test_mahogany_gym_entrance_walkable(nav):
    # (4,17)/(5,17): warp-carpet entrance cells -- enterable, not walls.
    grid = nav.grid("MAHOGANY_GYM")
    assert grid[17][4] in WARPS and grid[17][5] in WARPS
    assert nav._enterable("MAHOGANY_GYM", 4, 17)
    assert nav.cell_kind("MAHOGANY_GYM", 4, 17) == "warp"


@needs_disasm
def test_mahogany_mart_hidden_stairs_conditional(nav):
    # changeblock 6, 2, $1e uncovers the basement staircase: exactly the
    # (7,3) cell flips floor -> COLL_STAIRCASE_73 ($72... ladder byte).
    cond = nav.conditional("MAHOGANY_MART_1F")
    assert list(cond) == [(7, 3)]
    (lo, hi), = cond.values()
    assert lo == FLOOR and hi in WARPS   # staircase collision when open


def test_conditional_safe_on_synthetic_nav():
    # MapData built via __new__ (the unit-test pattern) has no
    # changeblocks attr; conditional()/render() must not blow up.
    md = MapData.__new__(MapData)
    md._grid_cache = {"M": [[FLOOR, WALL]]}
    md._coll_cache = {}
    md._cell_overrides = {}
    md.warps = {}
    md.warp_cells = {}
    md.conns = {}
    md.consts = {}
    md.surf = False
    assert md.conditional("M") == {}
    assert md.render("M") == [".#"]
    assert md.cell_kind("M", 0, 0) == "floor"
    assert md.cell_kind("M", 1, 0) == "wall"
