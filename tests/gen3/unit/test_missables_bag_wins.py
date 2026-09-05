"""An item you are HOLDING is not missing, whatever its flag says.

This cost real time: `HM08 DIVE` was on the `missing:` status line for an
entire playthrough while sitting in the bag, and every route I planned assumed
Dive was unavailable. The cause is that HM08 has TWO sources behind one flag
-- Steven's gift and a fallback item ball -- Steven hands it over directly, so
the ball's guarding flag never gets set and the row never clears.

The guarding flag answers "have you been to the place". The bag answers "do
you have the thing", and for a unique item that is the question.

The second test is the one that keeps the fix honest: consumables must NOT be
suppressed this way, or holding a single Potion would hide every Potion on
the map under `kind='all'`.
"""

import pytest

from pokeagent import missables


class FakeConsts:
    def __init__(self):
        # Real constant spelling: hm_items() derives the HM roster from the
        # ITEM_HM<nn>_<MOVE> names and refuses to guess if none match.
        self.items = {
            "ITEM_HM08_DIVE": 344,
            "ITEM_POTION": 13,
            "ITEM_BASEMENT_KEY": 260,
        }
        self.flags = {"FLAG_ITEM_BALL": 1}


class FakeItemData:
    def __init__(self, importance):
        self.importance = importance


class FakeNames:
    """`is_key_item` reads gItems[].importance, the engine's own answer for
    "cannot be tossed or sold" -- non-zero for HMs, bikes and keys."""

    def item(self, item_id):
        return {344: "HM08", 13: "POTION", 260: "BASEMENT KEY"}[item_id]

    def item_data(self, item_id):
        return FakeItemData(0 if item_id == 13 else 1)


class FakeState:
    def __init__(self, bag, flags=()):
        self._bag = bag
        self._set = set(flags)
        self.consts = FakeConsts()
        self.names = FakeNames()

    def bag(self):
        return self._bag

    def flag(self, name):
        return name in self._set


def _sources(monkeypatch, rows):
    monkeypatch.setattr(missables, "parse_item_sources", lambda: tuple(rows))


def _src(item, flag, kind="ball", map_name="VictoryRoad_B2F", x=13, y=8):
    s = missables.ItemSource.__new__(missables.ItemSource)
    object.__setattr__(s, "item", item)
    object.__setattr__(s, "flag", flag)
    object.__setattr__(s, "kind", kind)
    object.__setattr__(s, "map", map_name)
    object.__setattr__(s, "x", x)
    object.__setattr__(s, "y", y)
    object.__setattr__(s, "source_line", f"{map_name}:1")
    object.__setattr__(s, "unresolved", None)
    return s


@pytest.mark.unit
def test_an_hm_in_the_bag_is_not_missing_even_with_its_flag_clear(monkeypatch):
    _sources(monkeypatch, [_src("ITEM_HM08_DIVE", "FLAG_ITEM_BALL")])
    state = FakeState({"tms_hms": {"HM08": 1}}, flags=())

    rows = missables.missing_items(state, "hm", FakeNames())

    assert rows == [], (
        "HM08 is in the bag; reporting it missing sent a whole run planning "
        "around a Dive it already had"
    )


@pytest.mark.unit
def test_a_key_item_in_the_bag_is_not_missing(monkeypatch):
    _sources(monkeypatch, [_src("ITEM_BASEMENT_KEY", "FLAG_ITEM_BALL")])
    state = FakeState({"key_items": {"BASEMENT KEY": 1}})

    assert missables.missing_items(state, "key", FakeNames()) == []


@pytest.mark.unit
def test_a_key_item_NOT_in_the_bag_is_still_reported(monkeypatch):
    """The fix must not silence the thing the report exists for."""
    _sources(monkeypatch, [_src("ITEM_BASEMENT_KEY", "FLAG_ITEM_BALL")])
    state = FakeState({"key_items": {}})

    rows = missables.missing_items(state, "key", FakeNames())

    assert [r["item"] for r in rows] == ["BASEMENT KEY"]


@pytest.mark.unit
def test_holding_one_consumable_does_not_hide_the_others(monkeypatch):
    """A single POTION in the bag must not clear every potion on the map.

    This is why the suppression is restricted to HMs and key items instead of
    being applied to everything in the bag.
    """
    _sources(monkeypatch, [
        _src("ITEM_POTION", "FLAG_ITEM_BALL", map_name="Route102", x=1, y=1),
    ])
    state = FakeState({"items": {"POTION": 1}})

    rows = missables.missing_items(state, "all", FakeNames())

    assert [r["item"] for r in rows] == ["POTION"]
