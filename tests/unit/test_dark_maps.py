"""The FLASH gate: which maps are pitch dark, and what that blocks.

Motivated live (session claude-wren pt13): `field_moves()` reported
``FLASH: None`` for a party that had just become Champion and crossed to
Kanto. `missables()` says what you do not HAVE; nothing said what that
COSTS. The answer turned out to be three objectives at once -- ROCK_TUNNEL
(the Kanto shortcut), SILVER_CAVE_ROOM_1 (Red) and the WHIRL_ISLANDS
(Lugia) -- all gated by one uncollected HM.

The discriminator is the map's PALETTE, not its tileset. Verified against
`data/maps/maps.asm`:

    line 165  map RockTunnel1F,        TILESET_DARK_CAVE, ... PALETTE_DARK
    line 135  map MountMortar1FOutside,TILESET_DARK_CAVE, ... PALETTE_NITE
    line 139  map IcePath1F,           TILESET_ICE_PATH,  ... PALETTE_NITE

Keying on TILESET_DARK_CAVE would invent a FLASH requirement for Mt. Mortar,
which the game does not have.
"""

import pytest

from crystalagent import missables
from crystalagent.paths import REPO_ROOT

pytestmark = pytest.mark.unit


def test_palette_is_the_flash_signal_not_the_tileset():
    """data/maps/maps.asm:165 vs :135 -- same tileset, different palette."""
    flags = missables.parse_map_flags(REPO_ROOT)
    rock = flags["RockTunnel1F"]
    mortar = flags["MountMortar1FOutside"]
    assert rock["tileset"] == mortar["tileset"] == "TILESET_DARK_CAVE"
    assert rock["palette"] == "PALETTE_DARK"
    assert mortar["palette"] == "PALETTE_NITE"


def test_dark_maps_are_exactly_the_flash_required_set():
    """13 maps in the source carry PALETTE_DARK."""
    dark = missables.dark_map_names(REPO_ROOT)
    assert "RockTunnel1F" in dark
    assert "RockTunnelB1F" in dark
    assert "SilverCaveRoom1" in dark
    assert "DarkCaveVioletEntrance" in dark
    assert "WhirlIslandLugiaChamber" in dark
    assert len(dark) == 13


def test_maps_that_merely_look_dark_are_not_included():
    """Ice Path and Mt. Mortar are navigable without FLASH."""
    dark = missables.dark_map_names(REPO_ROOT)
    for name in ("IcePath1F", "IcePathB1F", "IcePathB2FMahoganySide",
                 "MountMortar1FOutside", "MountMortar1FInside",
                 "MountMortar2FInside"):
        assert name not in dark, name


def test_every_dark_map_is_a_cave_environment():
    """Sanity on the parse: a mis-split row would drag in towns/routes."""
    flags = missables.parse_map_flags(REPO_ROOT)
    for name in missables.dark_map_names(REPO_ROOT):
        assert flags[name]["environment"] == "CAVE", name


def test_parse_covers_the_whole_file():
    """A regex that silently matched only some rows would understate the
    gate; the real file declares hundreds of maps."""
    flags = missables.parse_map_flags(REPO_ROOT)
    assert len(flags) > 200
    assert all(v["palette"].startswith("PALETTE_") for v in flags.values())
