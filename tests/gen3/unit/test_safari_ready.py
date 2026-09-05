"""A Safari battle is READY even though the player has no battle mon.

Sixteen species live in the Safari Zone and the harness caught one of them,
then reported `no battle frame (battle_ready never came true)` for the rest --
so `Catcher.plan()` was never even consulted. The cause is in the engine, not
the harness's reading of it: on every controller pass a Safari battle memsets
the PLAYER-side `gBattleMons` entry to zero.

    if ((gBattleTypeFlags & BATTLE_TYPE_SAFARI)
     && GetBattlerSide(gActiveBattler) == 0)
        MEMSET_ALT(&gBattleMons[gActiveBattler], 0, 0x58, i, ptr);
        -- pret/src/battle_main.c:3711-3715

`battle_ready()` demanded a non-zero species from EVERY battler, which in the
Safari is a condition the game guarantees will never hold.
"""

import pytest

from pokeagent.state import BATTLE_TYPE, GameState

pytestmark = pytest.mark.unit

SAFARI = BATTLE_TYPE["safari"]
WILD = BATTLE_TYPE["wild"]
MON_SIZE = 0x58


class _Emu:
    """Two battlers: index 0 the player, index 1 the foe."""

    def __init__(self, flags, species):
        self.flags = flags
        self.species = species          # {battler_index: (species, level)}

    def resolve(self, name):
        return 0x1000 if name == "gBattleMons" else 0x2000

    def u8(self, addr):
        if addr == "gBattlersCount":
            return 2
        # gMain.inBattle: bit 1 set
        return 0b10

    def u16(self, name):
        if name == "gBattleTypeFlags":
            return self.flags
        return 0

    def read(self, addr, size):
        idx = (addr - 0x1000) // MON_SIZE
        species, level = self.species.get(idx, (0, 0))
        raw = bytearray(size)
        raw[0:2] = species.to_bytes(2, "little")
        raw[0x2A] = level
        return bytes(raw)


def _state(flags, species):
    gs = GameState.__new__(GameState)
    gs.emu = _Emu(flags, species)
    gs.battle_mon = {"species": 0, "level": 0x2A}
    gs._battle_mon_size = MON_SIZE
    gs.in_battle = lambda: True
    return gs


def test_a_safari_battle_is_ready_with_only_the_foe_populated():
    """The exact live shape: player zeroed by the engine, foe real."""
    gs = _state(SAFARI | WILD, {0: (0, 0), 1: (263, 27)})
    assert gs.battle_ready() is True


def test_a_safari_battle_is_not_ready_before_the_foe_arrives():
    """Readiness must still WAIT -- the transition animation runs ~60 frames."""
    gs = _state(SAFARI | WILD, {0: (0, 0), 1: (0, 0)})
    assert gs.battle_ready() is False


def test_an_ordinary_wild_battle_still_demands_both_sides():
    """The Safari exemption must not loosen normal battles."""
    gs = _state(WILD, {0: (0, 0), 1: (263, 27)})
    assert gs.battle_ready() is False, "a zeroed player side is NOT ready here"

    gs = _state(WILD, {0: (257, 36), 1: (263, 27)})
    assert gs.battle_ready() is True
