"""A boxed Pokemon's level is DERIVED, because the box format has no level.

This matters for the whole evolution half of the dex: 22 of the missing
species are evolutions of Pokemon already sitting in the PC, and "raise MARILL
to 18" is unanswerable without knowing what level the boxed MARILL is. The
party's level is plaintext in the struct tail; a boxed mon has EXP and nothing
else (pokeagent/pokemon.py:217-221 only fills `level` for the party tail).

The derivation is the game's own loop, `GetLevelFromBoxMonExp`
(pret/src/pokemon_1.c:1846-1852), and the test that actually guards it is
`test_derivation_matches_the_games_own_stored_level`, which is an integration
check against a real save: the party carries BOTH exp and a stored level, so
any off-by-one in the table walk shows up as a mismatch. Six mons on four
different growth rates agreed when this landed.
"""

import pytest

from pokeagent.names import Names


class FakeSym:
    def __init__(self, span):
        self._span = span

    def size(self, symbol):
        assert symbol == "gExperienceTables"
        return self._span


class FakeEmu:
    """Serves one synthetic experience table and one growth rate."""

    def __init__(self, table_rows, growth_rate=0):
        self.rows = table_rows
        self.growth_rate = growth_rate
        self.sym = FakeSym(len(table_rows) * 101 * 4)

    def resolve(self, symbol):
        return 0x1000

    def read(self, addr, span):
        import struct

        out = bytearray()
        for row in self.rows:
            for lv in range(101):
                out += struct.pack("<I", row[lv])
        return bytes(out)


def _names(rows, growth_rate=0):
    n = Names.__new__(Names)
    n.emu = FakeEmu(rows, growth_rate)

    class BS:
        pass

    bs = BS()
    bs.growth_rate = growth_rate
    n.base_stats = lambda species_id: bs
    return n


def _linear_row(step=100):
    """level N needs N*step exp -- easy to reason about at the boundaries.

    Shaped like the REAL table at the low end, which is `(0, 1, 8, 27, ...)`
    on every growth rate: index 0 is 0 and level 1 costs 1, not 0. A fixture
    with table[1] == 0 tests a table the ROM does not have.
    """
    return [0, 1] + [lv * step for lv in range(2, 101)]


@pytest.mark.unit
def test_exact_threshold_is_the_new_level_not_the_old_one():
    # The loop is `while table[level] <= exp`, so landing EXACTLY on a
    # threshold means you ARE that level. An implementation using `<` instead
    # sits one level low forever and would never fire an evolution.
    n = _names([_linear_row(100)])
    assert n.level_from_exp(1, 1800) == 18


@pytest.mark.unit
def test_one_point_short_is_the_previous_level():
    n = _names([_linear_row(100)])
    assert n.level_from_exp(1, 1799) == 17


@pytest.mark.unit
def test_the_lowest_real_exp_is_level_one():
    """A level-1 mon holds table[1] exp, which is 1 -- not 0.

    exp=0 derives as 0, and that is not a bug to paper over: it is what
    `GetLevelFromMonExp` itself returns for an impossible input, and matching
    the game beats inventing a floor it does not have.
    """
    n = _names([_linear_row(100)])
    assert n.level_from_exp(1, 1) == 1
    assert n.level_from_exp(1, 0) == 0


@pytest.mark.unit
def test_level_is_capped_at_100():
    """Past the last row the walk must stop, not run off the table."""
    n = _names([_linear_row(100)])
    assert n.level_from_exp(1, 99_999_999) == 100


@pytest.mark.unit
def test_each_growth_rate_reads_its_own_row():
    """A mon must be scored against ITS row.

    Reading row 0 for everything is the bug this catches: it would put a slow
    grower several levels high and fire evolutions that have not happened.
    """
    fast = _linear_row(50)
    slow = _linear_row(200)
    n_fast = _names([fast, slow], growth_rate=0)
    n_slow = _names([fast, slow], growth_rate=1)
    assert n_fast.level_from_exp(1, 1000) == 20
    assert n_slow.level_from_exp(1, 1000) == 5


@pytest.mark.unit
def test_a_table_that_is_not_whole_rows_is_refused():
    """Rather than silently reading a misaligned row."""
    n = Names.__new__(Names)
    n.emu = FakeEmu([_linear_row()])
    n.emu.sym = FakeSym(101 * 4 + 3)
    with pytest.raises(ValueError, match="whole number"):
        _ = n._exp_table
