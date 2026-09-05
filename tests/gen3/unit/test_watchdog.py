"""The stall detector, tested against the shapes that actually happened."""

import pytest

from pokeagent.watchdog import Watchdog, progress_signature

pytestmark = pytest.mark.unit


def test_a_moving_run_never_fires():
    w = Watchdog(nudge_after=3)
    for i in range(50):
        assert not w.observe(("Route101", i, 0))


def test_a_frozen_signature_escalates_once_per_lever():
    """The cable-car stall: 615 identical attempts, no lever, no complaint.

    Each lever must fire exactly once. Re-proposing every cycle is how a stuck
    run writes a thousand identical lines, which is the noise this replaces.
    """
    w = Watchdog(nudge_after=3, retarget_after=6, blocked_after=9)
    levers = [w.observe(("Route112", 21, 34)).lever for _ in range(30)]
    assert [l for l in levers if l] == ["nudge", "retarget", "blocked"]


def test_progress_resets_the_escalation():
    """A run that gets going again must be able to stall later and be caught."""
    w = Watchdog(nudge_after=2)
    for _ in range(5):
        w.observe(("A", 1, 1))
    assert w.cycles >= 2
    assert not w.observe(("B", 2, 2))
    assert w.cycles == 0
    assert w.fired == set()
    fired = [w.observe(("B", 2, 2)).lever for _ in range(5)]
    assert "nudge" in fired, "a second stall must escalate again"


def test_the_verdict_says_what_is_frozen():
    """A watchdog that says only 'stuck' sends a person back to the logs."""
    w = Watchdog(nudge_after=1)
    w.observe(("MauvilleCity", 4, 20))
    v = w.observe(("MauvilleCity", 4, 20))
    assert v.stuck and v.lever == "nudge"
    assert "MauvilleCity" in v.why and "cycles" in v.why


def test_the_signature_excludes_self_advancing_values():
    """The whole detector dies if any component moves on its own.

    The Lottad-vs-Grimer stall had a turn counter cycling T1..T5 forever while
    both HP bars sat still. Had the signature carried that counter, it would
    have looked like progress for as long as the run survived.
    """
    class FakeMon:
        species, level, hp = "LOTTAD", 30, 67

    class FakeState:
        def party(self): return [FakeMon()]
        def badges(self): return [1, 2, 3]
        def money(self): return 1000

    class FakeDriver:
        state = FakeState()
        def pos(self): return (21, 34)
        def map_name(self): return "Route112"

    d = FakeDriver()
    first = progress_signature(d)
    second = progress_signature(d)
    assert first == second, (
        "an unchanged game must produce an unchanged signature; anything "
        "self-advancing in here blinds the watchdog permanently"
    )


def test_an_unreadable_position_raises_rather_than_inventing_one():
    """A failed read must never be mistaken for movement.

    Substituting a sentinel position looks like a move to "?", which resets
    the stall clock -- so a driver that cannot be read would read as a driver
    making progress, which is precisely backwards. The caller skips the cycle.
    """
    class Exploding:
        def pos(self): raise RuntimeError("no avatar yet")
        def map_name(self): raise RuntimeError("no avatar yet")

    with pytest.raises(RuntimeError):
        progress_signature(Exploding())


def test_secondary_reads_still_degrade_quietly():
    """Only position is load-bearing; a missing party must not end the run."""
    class Partial:
        state = None
        def pos(self): return (3, 4)
        def map_name(self): return "LittlerootTown"

    sig = progress_signature(Partial())
    assert sig[0] == ("LittlerootTown", 3, 4)


def test_party_hp_is_part_of_progress():
    """Training that fights without winning is a stall, and looks busy."""
    w = Watchdog(nudge_after=2)
    frozen = (("Route102", 5, 5), 3, 900, (("ZIGZAGOON", 6, 20),), "train")
    w.observe(frozen)
    w.observe(frozen)
    assert w.observe(frozen).stuck
    healthier = (("Route102", 5, 5), 3, 900, (("ZIGZAGOON", 7, 24),), "train")
    assert not w.observe(healthier), "a level-up is progress"
