"""`exits` must be memoised, and must not lie after being cached.

Computing a warp's landing decodes the DESTINATION map's grid, so an uncached
`exits` decodes one grid per warp -- and `route_legs` asks every map in the
graph. That is the wedge the stall watchdog caught with the frame counter
flat: 150 seconds inside `nav.exits <- usable_exits <- route_legs` without a
single emulated frame, the same call this repo's journal has blamed three
separate times.

Safe to cache because nothing in the answer depends on live state: warps,
connections and landings all come from shipped map data.
"""

import pytest


@pytest.mark.unit
def test_exits_is_cached_and_stable():
    from pokeagent import nav as navmod

    calls = {"n": 0}

    class _Nav(navmod.MapData):
        def __init__(self):
            self._exits_cache = {}
            self._infos = {}

        def info(self, map_name):
            calls["n"] += 1
            raise AssertionError("info must not be reached on a cache hit")

    n = _Nav()
    n._exits_cache["Route101"] = [{"kind": "warp", "x": 1, "y": 2}]
    first = navmod.MapData.exits(n, "Route101")
    second = navmod.MapData.exits(n, "Route101")
    assert first == second == [{"kind": "warp", "x": 1, "y": 2}]
    assert calls["n"] == 0, "a cache hit must not recompute"


@pytest.mark.unit
def test_the_cache_is_keyed_per_map():
    """One map's exits must never be served for another's."""
    from pokeagent import nav as navmod

    class _Nav(navmod.MapData):
        def __init__(self):
            self._exits_cache = {}

    n = _Nav()
    n._exits_cache["A"] = [{"kind": "warp", "id": 0}]
    n._exits_cache["B"] = [{"kind": "warp", "id": 1}]
    assert navmod.MapData.exits(n, "A")[0]["id"] == 0
    assert navmod.MapData.exits(n, "B")[0]["id"] == 1
