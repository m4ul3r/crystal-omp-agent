"""A dry LEAD means a trip to the nurse, even if the bench is full.

The battle layer sends out slot 0, so a lead on empty PP makes tactics report
"no usable move can touch ZIGZAGOON (moveset: none)" and flee -- every
encounter, including the dex-new ones the sweep came for. Found live with an
L100 PELIPPER leading on empty PP while five other party members were full:
`pp_dry()` said False, `hurt()` said False, the nurse was never visited, and
the run fled its way across four routes catching nothing.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

import collect  # noqa: E402


class _Mon:
    def __init__(self, moves, pp, hp=100):
        self.moves = moves
        self.pp = pp
        self.hp = hp
        self.max_hp = 100
        self.is_egg = False
        self.nickname = "M"


class _Names:
    """Move 1 has power, move 2 is a status move."""

    def move_data(self, mid):
        return type("D", (), {"power": 40 if mid == 1 else 0})()


class _Collector:
    def __init__(self, party):
        names = _Names()
        state = type("S", (), {"party": staticmethod(lambda: party)})()
        self.d = type("D", (), {"names": names, "state": state})()

    pp_dry = collect.Collector.pp_dry


@pytest.mark.unit
def test_dry_lead_with_a_full_bench_is_dry():
    lead = _Mon(moves=[1, 2], pp=[0, 5])       # damaging move out of PP
    bench = _Mon(moves=[1, 2], pp=[20, 20])
    assert _Collector([lead, bench]).pp_dry() is True


@pytest.mark.unit
def test_lead_with_pp_is_not_dry():
    lead = _Mon(moves=[1, 2], pp=[10, 0])
    bench = _Mon(moves=[1, 2], pp=[0, 0])
    assert _Collector([lead, bench]).pp_dry() is False


@pytest.mark.unit
def test_status_only_pp_does_not_count():
    """PP on a zero-power move cannot touch anything."""
    lead = _Mon(moves=[2, 2], pp=[30, 30])
    assert _Collector([lead]).pp_dry() is True


@pytest.mark.unit
def test_empty_party_is_dry():
    assert _Collector([]).pp_dry() is True


@pytest.mark.unit
def test_fainted_lead_falls_through_to_the_bench():
    """A fainted slot 0 is not the battler; the bench decides."""
    lead = _Mon(moves=[1, 2], pp=[20, 20], hp=0)
    bench = _Mon(moves=[1, 2], pp=[20, 20])
    assert _Collector([lead, bench]).pp_dry() is False
