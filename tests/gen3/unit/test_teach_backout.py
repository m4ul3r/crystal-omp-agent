"""Leaving the move-forget list is a SEQUENCE, and the presses must be long.

`Teacher.teach()` used to learn the move and then leave the field locked
forever: `sLockFieldControls` stuck at 1, so every later movement press was
eaten and any savestate written afterwards was unusable.

What it was actually waiting for, read off a screenshot rather than guessed:
the BATTLE MOVES list, cursor on SURF, description box reading **"HM moves
can't be forgotten now."** It was not waiting for a menu to close -- it was
waiting to be told to give up.

Cancelling that costs three different presses: **B** asks "give up trying to
learn X?", **A** answers YES, a further **B** leaves the bag. Pressing only
one of them, however many times, gets nowhere. Measured on a wedged teach:

    24 B presses across three hold lengths  -> lock still 1
    20 A presses                            -> lock still 1
    direct DOWN/LEFT/RIGHT                  -> player never moved
    B/A/B with 16-frame holds               -> cleared in 7 rounds

These are source-shape tests. The behaviour needs an emulator, and it is
covered by driving a real teach; what is pinned here is the part that silently
regresses -- someone simplifying the loop back to a single button, or
shortening the holds to the `B:4` that did not work.
"""

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[3] / "pokeagent" / "teaching.py"


def _back_out_body() -> str:
    """The method's source. It is the last one in its class, so 'the next def'
    is not a safe end anchor -- fall back to end of file."""
    text = SRC.read_text()
    start = text.index("def _back_out(self)")
    nxt = text.find("\n    def ", start + 10)
    return text[start:] if nxt == -1 else text[start:nxt]


@pytest.mark.unit
def test_back_out_presses_both_buttons():
    """A single-button loop cannot leave this list."""
    body = _back_out_body()
    assert "B:16" in body, "B must be pressed with a long hold"
    assert "A:16" in body, "A must answer the give-up prompt"


@pytest.mark.unit
def test_back_out_does_not_use_the_short_hold_that_failed():
    body = _back_out_body()
    assert "B:4 " not in body, \
        "B:4 was measured not to move this box; do not go back to it"


@pytest.mark.unit
def test_the_sequence_order_is_b_then_a_then_b():
    """B asks, A confirms, B exits -- in that order."""
    body = _back_out_body()
    seqs = re.findall(r'"([AB]):16 \.\:40"', body)
    assert seqs[:3] == ["B", "A", "B"], seqs


@pytest.mark.unit
def test_it_still_gives_up_rather_than_looping_forever():
    """Bounded: a wedge must end the call, not spin in it."""
    body = _back_out_body()
    assert re.search(r"for _ in range\(\d+\)", body), \
        "the retry loop must stay bounded"
    assert "scene_active" in body, \
        "it must stop as soon as control comes back"
