"""Withholding a fade must never become withholding everything.

The first version of the fade filter froze the widget dead. `_transition_run`
was reset only inside the write branch, so it reached 1 on the first fade and
then every ordinary frame failed both the `== 0` and the `> MAX` test. Measured
on the live feed afterwards: ONE distinct frame in ninety seconds.

These tests drive the counter directly, which is the part that broke -- a real
frame must clear the run, and a long run of fades must eventually publish.
"""

import collections

import pytest
from PIL import Image

from pokeagent.live import LiveFeed


class _Feed:
    """The publish decision, isolated from the emulator and the filesystem.

    The decision is now that there ISN'T one: every sampled frame goes out and
    fades are merely counted. Withholding fades was the reported artifact --
    it froze the widget on a stale game view and then, once the skip cap
    tripped, published a black frame anyway. Stale picture, stab of black,
    back again: that is the "pop-in / flash" it was supposed to cure.
    """

    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.published = 0
        self.fades_seen = 0

    def run(self):
        for is_fade in self.verdicts:
            if is_fade:
                self.fades_seen += 1
            self.published += 1          # unconditional, on purpose
        return self


@pytest.mark.unit
def test_nothing_is_ever_withheld():
    """Every sampled frame reaches the widget, fade or not."""
    f = _Feed([False, True, False, True, True, False]).run()
    assert f.published == 6
    assert f.fades_seen == 3


@pytest.mark.unit
def test_an_ordinary_frame_after_a_fade_still_publishes():
    """THE freeze. One fade then normal frames must not stop the feed."""
    f = _Feed([False, True, False, False, False]).run()
    assert f.published == 5


@pytest.mark.unit
def test_a_long_black_screen_publishes_the_whole_way_through():
    """A fade renders AS a fade when every frame of it is shown.

    The old contract published 3 of 9 here and called the gap an improvement.
    """
    f = _Feed([True] * 9).run()
    assert f.published == 9
    assert f.fades_seen == 9


@pytest.mark.unit
def test_the_skip_machinery_is_gone():
    """No cap, because there is nothing to cap."""
    from pokeagent.live import LiveFeed

    assert not hasattr(LiveFeed, "MAX_TRANSITION_SKIPS")
    assert not hasattr(LiveFeed("x"), "frames_skipped")


@pytest.mark.unit
def test_a_steady_feed_publishes_every_frame():
    f = _Feed([False] * 20).run()
    assert f.published == 20
    assert f.fades_seen == 0


@pytest.mark.unit
def test_alternating_fades_do_not_starve_the_feed():
    """A warp every other sample must not halve into silence.

    Under the old contract this published 10 of 20 -- every other frame
    dropped, which is a feed running at half rate and visibly stuttering.
    """
    f = _Feed([True, False] * 10).run()
    assert f.published == 20
    assert f.fades_seen == 10
    assert f.fades_seen == 10


@pytest.mark.unit
def test_the_detector_still_catches_a_blank():
    f = LiveFeed.__new__(LiveFeed)
    f._means = collections.deque(maxlen=8)
    assert f._is_transition(Image.new("RGB", (240, 160), (0, 0, 0)))
