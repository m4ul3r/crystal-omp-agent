"""heal_pokecenter recovery (wren pt4/pt5): called outside a Pokécenter it
walks into the current map's PC via travel() instead of exploding; when no
PC warp exists it raises HealError, and registry resolve('heal') turns that
into a structured {'ok': False, ...} instead of a bare RuntimeError."""
import pytest

import trek
from crystalagent import registry

pytestmark = pytest.mark.unit


class FakeEmu:
    def __init__(self):
        self.frame = 0
        self.u8 = {}

    def tick(self, n=1):
        self.frame += n

    def read_u8(self, sym):
        return self.u8.get(sym, 0)


class FakeWorldDriver:
    """Duck-typed d for heal_pokecenter: tracks map, travels, nurse visits."""

    def __init__(self, start_map, heal_on_visit=1, travel_lands=True):
        self.map = start_map
        self.heal_on = heal_on_visit
        self.travel_lands = travel_lands
        self.travels = []
        self.gotos = 0
        self.healed = False
        self.steps = []
        self.emu = FakeEmu()
        self.names = None

        class M:
            def wait_for(self, pred, timeout_frames=600, quiet=False):
                return False

            def select_label(self, label, **kw):
                return True

        self.menu = M()

    def map_name(self):
        return self.map

    def travel(self, dest_map, label=""):
        self.travels.append(dest_map)
        if self.travel_lands:
            self.map = dest_map
        return []

    def textbox(self):
        return False

    def goto(self, x, y, label=""):
        self.gotos += 1
        if self.gotos >= self.heal_on:
            self.healed = True
        return True

    def step_dir(self, mv):
        self.steps.append(mv)
        return "moved" if mv == "D" else "blocked"

    def facing(self):
        # heal steps AWAY from whatever it faces; this fake models the
        # Johto counter (nurse above, floor below), so it faces UP
        return "U"

    def press(self, seq):
        self.emu.tick(5)

    def flush_dialog(self, *a, **k):
        return "done"

    def settle(self, **kw):
        pass

    def lead(self):
        return {"name": "GATOR", "hp": 24, "max_hp": 24}

    def party(self):
        hp = 24 if self.healed else 7
        return [{"species": "TOTODILE", "hp": hp, "max_hp": 24}]


FAKE_GRAPH = {"edges": [
    {"from_map": "VIOLET_CITY", "to_map": "VIOLET_POKECENTER_1F",
     "kind": "warp", "cells": [31, 25], "routable": True},
    {"from_map": "VIOLET_CITY", "to_map": "VIOLET_MART",
     "kind": "warp", "cells": [24, 20], "routable": True},
    # unroutable PC edge must NOT count as a known way in
    {"from_map": "SPROUT_TOWER_1F", "to_map": "VIOLET_POKECENTER_1F",
     "kind": "warp", "cells": [0, 0], "routable": False},
]}


def world(monkeypatch, d):
    monkeypatch.setattr(trek, "game_state",
                        lambda emu, names: {"party": d.party()})
    monkeypatch.setattr(trek, "mapgraph", lambda: FAKE_GRAPH)


# -- inside a Pokécenter: behavior unchanged ---------------------------------

def test_heal_inside_pokecenter_never_travels(monkeypatch):
    d = FakeWorldDriver("VIOLET_POKECENTER_1F")
    world(monkeypatch, d)
    assert trek.heal_pokecenter(d) is None      # return shape unchanged
    assert d.travels == []
    assert d.healed
    assert d.steps and d.steps[-1] == "D"       # step-off preserved


# -- town map with a known PC warp: enter first, then heal -------------------

def test_heal_from_town_enters_pokecenter_then_heals(monkeypatch):
    d = FakeWorldDriver("VIOLET_CITY")
    world(monkeypatch, d)
    assert trek.heal_pokecenter(d) is None
    assert d.travels == ["VIOLET_POKECENTER_1F"]  # PC, not the mart
    assert d.healed
    assert "POKECENTER" in d.map_name()


def test_heal_detour_bounded_by_tries(monkeypatch):
    d = FakeWorldDriver("VIOLET_CITY", travel_lands=False)
    world(monkeypatch, d)
    with pytest.raises(trek.HealError) as ei:
        trek.heal_pokecenter(d, tries=3)
    assert len(d.travels) == 3                   # bounded, not forever
    assert ei.value.map_name == "VIOLET_CITY"
    assert d.gotos == 0                          # never talked to a nurse


def test_heal_detour_survives_travel_exception(monkeypatch):
    """A TravelError on attempt 1 is retried, not propagated."""
    d = FakeWorldDriver("VIOLET_CITY")
    world(monkeypatch, d)
    real_travel, calls = d.travel, []

    def flaky(dest_map, label=""):
        calls.append(dest_map)
        if len(calls) == 1:
            raise trek.TravelError("glide landed weird")
        return real_travel(dest_map, label)

    d.travel = flaky
    assert trek.heal_pokecenter(d, tries=2) is None
    assert len(calls) == 2 and d.healed


# -- dungeon (no routable PC warp): structured failure ------------------------

def test_heal_from_dungeon_raises_healerror_with_map(monkeypatch):
    d = FakeWorldDriver("SPROUT_TOWER_2F")
    world(monkeypatch, d)
    with pytest.raises(trek.HealError, match="SPROUT_TOWER_2F"):
        trek.heal_pokecenter(d)
    assert d.travels == [] and d.gotos == 0


def test_unroutable_pc_edge_does_not_count(monkeypatch):
    d = FakeWorldDriver("SPROUT_TOWER_1F")
    world(monkeypatch, d)
    with pytest.raises(trek.HealError):
        trek.heal_pokecenter(d)
    assert d.travels == []


def test_healerror_is_still_a_runtimeerror():
    assert issubclass(trek.HealError, RuntimeError)


# -- registry resolve('heal') path --------------------------------------------

def test_registry_heal_returns_structured_failure(monkeypatch):
    d = FakeWorldDriver("SPROUT_TOWER_2F")
    world(monkeypatch, d)
    out = registry.resolve(d, "heal", {})
    assert out["ok"] is False
    assert out["map"] == "SPROUT_TOWER_2F"
    assert "SPROUT_TOWER_2F" in out["reason"]


def test_registry_heal_success_shape_unchanged(monkeypatch):
    d = FakeWorldDriver("VIOLET_POKECENTER_1F")
    world(monkeypatch, d)
    assert registry.resolve(d, "heal", {}) is None
    assert d.healed


def test_registry_heal_accepts_tries_kwarg(monkeypatch):
    d = FakeWorldDriver("VIOLET_CITY", travel_lands=False)
    world(monkeypatch, d)
    out = registry.resolve(d, "heal", {"tries": 1})
    assert out["ok"] is False and len(d.travels) == 1


def test_registry_heal_propagates_non_heal_runtimeerrors(monkeypatch):
    """Only the can't-reach-a-nurse case is softened; a failed heal at the
    counter (party not fully healed) still raises like before."""
    d = FakeWorldDriver("VIOLET_POKECENTER_1F", heal_on_visit=99)
    world(monkeypatch, d)
    with pytest.raises(RuntimeError, match="not fully healed"):
        registry.resolve(d, "heal", {})
