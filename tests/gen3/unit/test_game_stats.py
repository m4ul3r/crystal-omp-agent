"""The cartridge's own tallies, rather than a second set that drifts.

The harness kept its own step counter and published 0 across a run that had
walked more than fifty thousand steps -- the increment lived on the
grass-grinding path, and every goto/travel step, which is most of them, missed
it. Reading the count the game already maintains fixes that class of bug rather
than one instance of it: it counts what the player did, it survives a restart,
and it does not need updating when the harness grows a new way to move.
"""

import struct

import pytest

from pokeagent.state import GAME_STATS, GAME_STATS_OFFSET, game_stats

pytestmark = pytest.mark.unit

BASE = 0x2025734          # gSaveBlock1, from the linker map


class FakeEmu:
    """Just enough emulator to serve one struct read."""

    def __init__(self, values):
        top = max(GAME_STATS.values()) + 1
        self.blob = struct.pack(f"<{top}I", *(values + [0] * (top - len(values))))
        self.asked = None

    def resolve(self, name):
        assert name == "gSaveBlock1", name
        return BASE

    def read(self, addr, length):
        self.asked = (addr, length)
        return self.blob[:length]


def test_it_reads_the_documented_offset():
    """global.h:703 puts gameStats at 0x1540 inside gSaveBlock1. If that moves,
    every number below becomes a plausible-looking lie."""
    emu = FakeEmu([0] * 16)
    game_stats(emu)
    addr, length = emu.asked
    assert addr == BASE + GAME_STATS_OFFSET
    assert length == 4 * (max(GAME_STATS.values()) + 1)


def test_each_stat_comes_from_its_own_index():
    """Indices are from constants/game_stat.h. Distinct values per slot means
    an off-by-one shows up as swapped stats rather than as a rounder number."""
    values = [0] * 16
    values[5] = 54_382       # GAME_STAT_STEPS
    values[7] = 2_086        # GAME_STAT_TOTAL_BATTLES
    values[8] = 2_069        # GAME_STAT_WILD_BATTLES
    values[9] = 17           # GAME_STAT_TRAINER_BATTLES
    values[11] = 5           # GAME_STAT_POKEMON_CAPTURES
    values[14] = 3           # GAME_STAT_EVOLVED_POKEMON
    values[15] = 312         # GAME_STAT_USED_POKECENTER

    stats = game_stats(FakeEmu(values))
    assert stats["steps"] == 54_382
    assert stats["battles"] == 2_086
    assert stats["wild_battles"] == 2_069
    assert stats["trainer_battles"] == 17
    assert stats["captures"] == 5
    assert stats["evolutions"] == 3
    assert stats["pokecenter_visits"] == 312


def test_wild_and_trainer_battles_add_up_to_the_total():
    """A cheap sanity check on the mapping: if `battles` were pointed at the
    wrong index this stops holding for real save data."""
    values = [0] * 16
    values[7], values[8], values[9] = 100, 80, 20
    stats = game_stats(FakeEmu(values))
    assert stats["wild_battles"] + stats["trainer_battles"] == stats["battles"]


def test_a_fresh_save_reads_as_zero_not_garbage():
    """Zeroes are the assertion. A read at the wrong offset returns junk far
    more often than it returns exactly zero."""
    assert set(game_stats(FakeEmu([0] * 16)).values()) == {0}
