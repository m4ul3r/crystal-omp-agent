"""Rotating-gate escalation belongs to the two maps that have gates.

`gRotatingGate_PuzzleCount` is set by `special RotatingGate_InitPuzzle` and
NOTHING clears it on the way out, so after one visit to Fortree's gym it reads 7
for the rest of the run. `goto` escalates to `solve_gate_maze` whenever a walk
fails and gates appear to be present, so that stale global made the escalation
fire on ordinary maps:

    travel to MtPyre_1F ended at Route122
    (rotating gates: 4000 nodes explored without reaching (21, 29) on Route122)

Four thousand nodes is four thousand savestates and about half an hour, spent
trying to solve a gate puzzle on a mountain path that has no gates. It is also
where 1,288 leaked scratch directories came from.
"""

import pytest

from pokeagent.trek import Driver

pytestmark = pytest.mark.unit


class _Emu:
    """Reports a stale non-zero gate count, exactly like the real global."""

    def __init__(self, count=7):
        self.count = count
        self.reads = 0

    def u8(self, name):
        if name == "gRotatingGate_PuzzleCount":
            self.reads += 1
            return self.count
        return 0

    def resolve(self, _name):
        return 0x4000

    def read(self, _addr, size):
        return bytes(range(size))


class _State:
    """`gate_signature` reads the orientations from the var block."""

    def _sb1(self, _field):
        return 0x4000


def _driver(map_name, count=7):
    d = object.__new__(Driver)
    d.emu = _Emu(count)
    d.state = _State()
    d.map_name = lambda: map_name
    return d


def test_the_decomp_names_exactly_two_gate_maps():
    """Derived from pret/data/maps/*/scripts.inc, not hardcoded here."""
    assert Driver.gate_maps() == frozenset(
        {"FortreeCity_Gym", "Route110_TrickHousePuzzle6"}
    )


def test_an_ordinary_map_reports_no_gates_despite_the_stale_count():
    """The exact live failure: Route 122, count still 7 from Fortree."""
    d = _driver("Route122")
    assert d.gate_signature() == ()
    assert d.emu.reads == 0, "read the stale global instead of checking the map"


def test_other_innocent_maps_too():
    for name in ("MtPyre_1F", "LilycoveCity", "SafariZone_Southwest",
                 "SlateportCity_Harbor", "AquaHideout_B2F"):
        assert _driver(name).gate_signature() == (), name


def test_a_real_gate_map_still_reads_its_gates():
    d = _driver("FortreeCity_Gym", count=7)
    sig = d.gate_signature()
    assert sig and len(sig) == 7
    assert d.emu.reads >= 1

    d2 = _driver("Route110_TrickHousePuzzle6", count=4)
    assert len(d2.gate_signature()) == 4


def test_a_gate_map_with_no_puzzle_loaded_yet_reports_none():
    """Entering the gym before the special runs must not invent gates."""
    assert _driver("FortreeCity_Gym", count=0).gate_signature() == ()


def test_an_absurd_count_is_refused():
    """A garbage read must not become a 200-gate search space."""
    assert _driver("FortreeCity_Gym", count=99).gate_signature() == ()
