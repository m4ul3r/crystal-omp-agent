"""A LONE extreme frame is dropped; a SUSTAINED one is published.

This is the third design for the reported "pop-in / flash / flickering", and
the two it replaces are the reason the tests are shaped this way:

  1. Withholding every extreme behind a skip cap froze the feed on a stale
     view and then fired the black frame anyway when the cap tripped.
  2. Publishing everything strobed, because a Gen-3 battle flashes for 1-3
     frames at 60 Hz and a ~10 Hz feed holds one of those for a full sample.

So the two properties that MUST hold together are "a single flash never
reaches disk" and "the feed can never freeze" -- and the second is what the
capped version violated. `test_a_sustained_fade_publishes_on_the_second_frame`
is the one that would catch a regression back to design 1.
"""

import io

import pytest
from PIL import Image

from pokeagent.live import LiveFeed


class FakeEmu:
    """Hands out whatever frame the test sets, and counts nothing else."""

    def __init__(self):
        self.frame = 0
        self.shot = None

    def screenshot(self):
        # Every sample advances the game clock a little, the way a real feed's
        # samples are separated by emulation. The hold is measured in FRAMES.
        self.frame += 10
        return self.shot


class FakeDriver:
    def __init__(self, emu):
        self.emu = emu


def solid(level):
    """A uniform frame: what the console shows at a fade's ends."""
    return Image.new("RGB", (240, 160), (level, level, level))


def picture(seed=0):
    """A frame with real content, in the normal brightness band."""
    im = Image.new("RGB", (240, 160))
    px = im.load()
    for y in range(160):
        for x in range(0, 240, 2):
            v = (x * 3 + y * 5 + seed) % 256
            px[x, y] = (v, 255 - v, (v * 2) % 256)
            px[x + 1, y] = (255 - v, v, v)
    return im


@pytest.fixture()
def feed(tmp_path):
    f = LiveFeed("t", directory=tmp_path, fps=1000.0)
    emu = FakeEmu()
    f.driver = FakeDriver(emu)
    return f, emu


def frames_on_disk(f):
    """The published PNG, decoded -- the only thing the widget can see."""
    if not f.png_path.exists():
        return None
    return Image.open(io.BytesIO(f.png_path.read_bytes())).convert("RGB")


def push(f, emu, img):
    emu.shot = img
    f._publish(emu, f.driver, render=True, state=False, live=True)


@pytest.mark.unit
def test_a_lone_black_flash_never_reaches_disk(feed):
    f, emu = feed
    # Establish the normal band: the outlier half of the detector needs a few
    # frames of history before it will call anything an outlier.
    for i in range(6):
        push(f, emu, picture(i))
    before = frames_on_disk(f)
    assert before is not None

    push(f, emu, solid(0))

    after = frames_on_disk(f)
    assert after.tobytes() == before.tobytes(), (
        "a single black frame overwrote the published picture"
    )
    assert f.flashes_dropped == 1


@pytest.mark.unit
def test_a_lone_white_flash_never_reaches_disk(feed):
    # The frame that started the investigation was BRIGHT, not dark: mean 232
    # against a neighbourhood of 141. A detector that only knows about black
    # would pass this test's dark sibling and still strobe on every hit.
    f, emu = feed
    for i in range(6):
        push(f, emu, picture(i))
    before = frames_on_disk(f)

    push(f, emu, solid(255))

    assert frames_on_disk(f).tobytes() == before.tobytes()
    assert f.flashes_dropped == 1


@pytest.mark.unit
def test_a_run_of_extremes_is_withheld_while_it_is_still_short(feed):
    """THE STROBE CASE, and the reason a one-sample bound was not enough.

    A fly animation or a warp fade spans SEVERAL samples, so a rule that only
    suppressed an isolated extreme published black on the second one and the
    panel strobed anyway. Measured mid-travel with that rule live: 11 of 81
    published frames outside the 40-210 brightness band, some at 0.0, with
    flashes_dropped at 17318 of 87089.
    """
    f, emu = feed
    for i in range(6):
        push(f, emu, picture(i))
    before = frames_on_disk(f)

    for _ in range(5):                      # a whole fade, back to back
        push(f, emu, solid(0))

    assert frames_on_disk(f).tobytes() == before.tobytes(), (
        "a fade in flight reached the widget and strobed the panel"
    )
    assert f.flashes_dropped == 5


@pytest.mark.unit
def test_a_fade_that_OUTLASTS_the_window_publishes(feed):
    """THE ANTI-FREEZE PROPERTY. A screen that is genuinely black must show.

    The bound is the GAME's frame counter, not wall-clock and not a sample
    count, so this rewinds the start of the run past FADE_HOLD_FRAMES. A
    wall-clock window leaked in production because the emulator does not run
    at realtime -- a 40-frame fade under load outlasted 1.2 real seconds.
    """
    f, emu = feed
    for i in range(6):
        push(f, emu, picture(i))

    push(f, emu, solid(0))                  # withheld: the run just began
    f._extreme_since = emu.frame - (f.FADE_HOLD_FRAMES + 1)
    push(f, emu, solid(0))                  # the fade has outlasted the window

    out = frames_on_disk(f)
    assert max(out.convert("L").getextrema()) == 0, (
        "a black screen that outlasted the hold never reached the widget -- "
        "the feed froze, which is the failure the capped version had"
    )


@pytest.mark.unit
def test_ordinary_play_publishes_every_frame(feed):
    """No cost to the normal case: nothing in the band is ever withheld."""
    f, emu = feed
    for i in range(12):
        push(f, emu, picture(i))
    assert f.flashes_dropped == 0
    assert f.frames_published == 12


@pytest.mark.unit
def test_recovery_after_a_flash_is_immediate(feed):
    """The frame AFTER a dropped flash publishes immediately.

    The window must not keep withholding once the picture is back -- a
    non-extreme frame clears the run outright.
    """
    f, emu = feed
    for i in range(6):
        push(f, emu, picture(i))
    push(f, emu, solid(0))
    published = f.frames_published
    push(f, emu, picture(99))
    assert f.frames_published == published + 1
