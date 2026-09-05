"""Which fishing slots a held rod can actually roll.

I got this wrong twice before reading the data, and both wrong versions
silently wanted NOTHING, which is the worst possible failure for a collection
step -- it looks like "nothing to do here" forever.

1. Filtered on `WildSlot.method`, an attribute that does not exist.
2. Re-derived the slot ranges from `ChooseWildMonIndex_Fishing`
   (src/wild_encounter.c:200-235) and matched `kind == "fishing"` -- but
   `dex.py` already splits the table per rod, so that kind never appears.

The real kinds are `old_rod`, `good_rod`, `super_rod`, which is the engine's
own split expressed in the data. These tests pin the mapping so a third
version cannot quietly want nothing again.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

import play as playmod  # noqa: E402


class _FakeState:
    def __init__(self, keys):
        self._keys = keys

    def bag(self):
        return {"key_items": {k: 1 for k in self._keys}}


class _FakeDriver:
    def __init__(self, keys):
        self.state = _FakeState(keys)


def _session(keys):
    s = playmod.Session.__new__(playmod.Session)
    s.d = _FakeDriver(keys)
    return s


@pytest.mark.unit
def test_no_rod_reaches_nothing():
    """The step must stay dormant for most of a run."""
    assert _session([])._rod_kinds() == frozenset()


@pytest.mark.unit
def test_each_rod_reaches_only_its_own_kind():
    assert _session(["GOOD ROD"])._rod_kinds() == frozenset({"good_rod"})
    assert _session(["OLD ROD"])._rod_kinds() == frozenset({"old_rod"})
    assert _session(["SUPER ROD"])._rod_kinds() == frozenset({"super_rod"})


@pytest.mark.unit
def test_rods_accumulate():
    got = _session(["OLD ROD", "GOOD ROD", "SUPER ROD"])._rod_kinds()
    assert got == frozenset({"old_rod", "good_rod", "super_rod"})


@pytest.mark.unit
def test_the_bag_may_spell_them_with_underscores_or_spaces():
    """The bag reader's key is a display name; the constants use underscores."""
    assert _session(["ITEM_GOOD_ROD"])._rod_kinds() == frozenset({"good_rod"})


@pytest.mark.unit
def test_other_key_items_reach_nothing():
    assert _session(["ITEMFINDER", "GO-GOGGLES", "DEVON SCOPE"])._rod_kinds() \
        == frozenset()
