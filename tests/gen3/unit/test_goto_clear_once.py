"""`goto` must ask to clear the road once, not once per round.

Clearing scenery is a WALK -- it costs a nested `goto` for every side of every
rock -- so asking again on each of the loop's 144 rounds multiplies out to
millions of frames. The run stays "in progress" by every counter inside
`goto` while the picture never changes, which is the most expensive shape of
failure this project has: Victory Road B1F (9,9) burned roughly two hours that
way before a watchdog dumped the stack.

The invariant is small and worth pinning: however many times the route comes
back blocked, `clear_the_way` is consulted at most once per `goto` call.
"""

import pytest


class _Recorder:
    """Minimal stand-in that reports the road blocked forever.

    It answers `goto`'s questions the way a rock-blocked map does -- a path
    exists once live objects are ignored, never with them -- so the loop takes
    the clearing branch on every round.
    """

    def __init__(self):
        self.clear_calls = 0
        self.settles = 0

    def clear_the_way(self, target):
        self.clear_calls += 1
        return False  # the rock does not budge

    def settle(self, frames):
        self.settles += 1


def _run_branch(rec, rounds):
    """Replay `goto`'s blocked-route branch, with the guard under test.

    Mirrors trek.py's structure: `tried_clearing` is initialised once OUTSIDE
    the loop, and the branch is taken on every round.
    """
    tried_clearing = False
    for _ in range(rounds):
        if not tried_clearing:
            tried_clearing = True
            if rec.clear_the_way((1, 1)):
                continue
        rec.settle(120)
    return rec


@pytest.mark.unit
def test_clearing_is_asked_once_however_many_rounds():
    rec = _run_branch(_Recorder(), rounds=144)
    assert rec.clear_calls == 1, (
        "clear_the_way must be consulted once per goto; once per round is the "
        "ten-million-frame freeze")
    # Every other round still settles, so the branch keeps its normal
    # let-the-wanderer-move behaviour.
    assert rec.settles == 144


@pytest.mark.unit
def test_the_guard_is_per_call_not_global():
    """A later goto must be allowed to try clearing again.

    The rock may genuinely be smashable next time -- Rock Smash could have
    been taught, or a different route is being walked -- so the flag has to be
    call-local. A module-level or instance-level latch would permanently
    disable clearing after one failure.
    """
    rec = _Recorder()
    _run_branch(rec, rounds=10)
    _run_branch(rec, rounds=10)
    assert rec.clear_calls == 2


@pytest.mark.unit
def test_goto_initialises_the_flag_outside_its_loop():
    """Guard against the obvious regression: resetting it each round.

    If `tried_clearing = False` is moved inside the `while`, the count goes
    back to one-per-round and the freeze returns, so pin the source shape.
    """
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[3] / "pokeagent" / "trek.py"
    text = src.read_text()
    init = text.index("tried_clearing = False")
    loop = text.index("while attempt < max_replans", init)
    between = text[init:loop]
    assert "\n" in between and between.count("while") == 0, \
        "tried_clearing must be initialised BEFORE goto's replan loop"
    # And it must actually gate the call.
    assert re.search(r"not tried_clearing", text), \
        "the clearing branch must test tried_clearing"
