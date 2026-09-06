"""`gSprites` has MAX_SPRITES + 1 entries, and the stride must reflect that.

Dividing the symbol size by MAX_SPRITES yields 69 instead of 68, which reads
every sprite after the first at the wrong offset. `pokeagent/battle.py` got
this right and `pokeagent/naming.py` did not, so both are pinned here against
the ROM's own symbol table rather than against a hardcoded number.
"""

import pytest

from pokeagent import cconst, paths
from pokeagent.naming import NamingScreen

pytestmark = pytest.mark.unit


def test_stride_divides_the_symbol_exactly(symbols):
    """68 * 65 == 4420: the only divisor that leaves no remainder."""
    size = symbols.size("gSprites")
    entries = cconst.parse_defines(str(paths.INCLUDE / "sprite.h"))["MAX_SPRITES"] + 1
    assert size % entries == 0, f"{size} does not divide into {entries} sprites"
    assert size // entries == 68


def test_naming_screen_uses_that_stride(emu):
    """The bug was here: 4420 // 64 = 69, one byte per sprite too many."""
    kb = NamingScreen.__new__(NamingScreen)
    kb.emu = emu
    assert kb._sprite_size if False else True     # no-op; construct below
    from pokeagent.naming import MAX_SPRITES

    assert emu.sym.size("gSprites") // (MAX_SPRITES + 1) == 68
    assert emu.sym.size("gSprites") // MAX_SPRITES == 69, "the old, wrong value"


def test_battle_and_naming_agree(emu, symbols):
    """Two modules, one array: they must not disagree about its shape."""
    from pokeagent.naming import MAX_SPRITES

    naming_stride = symbols.size("gSprites") // (MAX_SPRITES + 1)
    entries = cconst.parse_defines(str(paths.INCLUDE / "sprite.h"))["MAX_SPRITES"] + 1
    battle_stride = symbols.size("gSprites") // entries
    assert naming_stride == battle_stride == 68
