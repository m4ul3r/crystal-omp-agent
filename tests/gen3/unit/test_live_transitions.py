"""A fade is not a frame.

The widget publishes a few frames a second out of sixty, so it samples fades:
every warp, battle start, heal and map load dips to black or peaks to white.
Catching one pins a featureless rectangle on screen for a sixth of a second,
which was reported three times as the game screen "flashing / flickering".

Measured on sixty seconds of real play before the fix: 366 frames published,
nine degenerate -- five at mean 0.0 (pure black), one at 225.0 (near white),
the rest deeply dark. The flash that started the investigation was NOT uniform:
it had the scene's full geometry at a mean of 201 against a neighbourhood of
104, so uniformity alone does not catch it and the outlier test exists.
"""

import collections

import pytest
from PIL import Image

from pokeagent.live import LiveFeed


def _feed():
    f = LiveFeed.__new__(LiveFeed)
    f._means = collections.deque(maxlen=8)
    return f


def _noise(level=128, spread=90):
    """A frame with real content at a given brightness."""
    im = Image.new("RGB", (240, 160), (level, level, level))
    px = im.load()
    for y in range(0, 160, 2):
        for x in range(0, 240, 2):
            v = max(0, min(255, level + (spread if (x + y) % 4 else -spread)))
            px[x, y] = (v, v, v)
    return im


@pytest.mark.unit
def test_pure_black_is_a_transition():
    assert _feed()._is_transition(Image.new("RGB", (240, 160), (0, 0, 0)))


@pytest.mark.unit
def test_pure_white_is_a_transition():
    assert _feed()._is_transition(Image.new("RGB", (240, 160), (255, 255, 255)))


@pytest.mark.unit
def test_an_ordinary_frame_is_published():
    f = _feed()
    frame = _noise(120)
    for _ in range(5):
        f._is_transition(frame)
    assert f._is_transition(frame) is False


@pytest.mark.unit
def test_a_bright_flash_with_real_content_is_caught():
    """The 201-against-104 case: geometry intact, brightness doubled."""
    f = _feed()
    for _ in range(5):
        f._is_transition(_noise(104))
    assert f._is_transition(_noise(201)) is True


@pytest.mark.unit
def test_a_sudden_darkening_is_caught_too():
    f = _feed()
    for _ in range(5):
        f._is_transition(_noise(128))
    assert f._is_transition(_noise(40)) is True


@pytest.mark.unit
def test_no_verdict_before_there_is_a_neighbourhood():
    """With nothing to compare against, a frame with content goes out.

    Withholding the first frames would leave the widget on "Loading..." for a
    second every time a run starts, which is the opposite of the fix.
    """
    f = _feed()
    assert f._is_transition(_noise(200)) is False


@pytest.mark.unit
def test_the_detector_only_counts_now():
    """The filter still IDENTIFIES fades; it no longer withholds them.

    A game may legitimately sit on a black screen, and the console shows a fade
    on every warp, battle and heal. Publishing those is honest and smooth;
    holding them back froze the feed on a stale frame and then flashed black
    when the cap tripped.
    """
    assert not hasattr(LiveFeed, "MAX_TRANSITION_SKIPS")
    # A REAL feed, not the __new__ stub the filter tests use: the counter is
    # set up in __init__ and that is the thing under test here.
    live = LiveFeed("flicker-contract")
    assert live.fades_seen == 0
    assert live.frames_published == 0
    assert not hasattr(live, "frames_skipped")
    # The detector itself still works -- it just no longer gates the write.
    assert _feed()._is_transition(Image.new("RGB", (240, 160), (0, 0, 0)))
