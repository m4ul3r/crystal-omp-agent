"""The naming keyboard must wait for the engine's input state, not a frame count.

Every catch of the run took a default name because typing failed on character
one: "could not move the cursor to 'G' at (0,1)". The walk was pressing during
`MainState_WaitFadeIn`, where the D-pad is not read at all.
"""

import pytest

from pokeagent.naming import NamingScreen

pytestmark = pytest.mark.unit


class _Emu:
    """A keyboard that only starts reading input after `fade_frames`."""

    def __init__(self, fade_frames=200):
        self.frames = 0
        self.fade_frames = fade_frames
        self.presses = []

    # -- what NamingScreen reads --------------------------------------
    def u8(self, addr):
        # offset 0 is `state`; 2 == MainState_HandleInput
        if addr == 0:
            return 2 if self.frames >= self.fade_frames else 1
        return 0

    def tick(self, n=1):
        self.frames += n

    def run_sequence(self, seq):
        self.presses.append(seq)
        self.frames += 8

    def resolve(self, _name):
        return 0

    def read(self, *_a, **_k):
        return b"\x00" * 4


def _screen(emu):
    s = NamingScreen.__new__(NamingScreen)
    s.emu = emu
    s.nsd = {"state": 0, "cursorSpriteId": 15, "currentPage": 14}
    s._base = 0
    return s


def test_settle_waits_for_the_input_handler():
    emu = _Emu(fade_frames=200)
    s = _screen(emu)
    assert s._settle_open() is True
    assert emu.frames >= 200
    # It waited, it did not press: a press during the fade is swallowed.
    assert emu.presses == []


def test_settle_costs_only_what_the_fade_costs():
    """A quiet keyboard (already enabled) must not pay the old flat 30 frames."""
    emu = _Emu(fade_frames=0)
    s = _screen(emu)
    assert s._settle_open() is True
    assert emu.frames == 0


def test_settle_gives_up_rather_than_spinning_forever():
    emu = _Emu(fade_frames=10**6)
    s = _screen(emu)
    assert s._settle_open(frames=40) is False


def test_a_press_is_never_spent_outside_the_input_state():
    """`_move_to` must wait out a page-swap animation, not press through it."""
    emu = _Emu(fade_frames=10**6)          # never enabled
    s = _screen(emu)
    s.cursor = lambda: (0, 0)
    assert s._move_to(3, 3, tries=6) is False
    assert emu.presses == [], "pressed while the engine was not reading input"
