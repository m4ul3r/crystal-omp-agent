"""Real-time pacing: the throttle that makes a watched run look like hardware.

`Sapphire.tick` is the single place every frame in this harness is advanced --
inputs, `settle`, `advance_scene` -- so the throttle lives there and this is
where its arithmetic is pinned. The emulator is not booted: pacing is pure
bookkeeping over an injected clock, and spending real seconds to test a sleep
would be the slowest possible way to learn nothing.
"""

import pytest

from pokeagent.emu import HARDWARE_FPS, Sapphire, _fps_from_env

pytestmark = pytest.mark.unit


class FakeClock:
    """A monotonic clock that only moves when the test says so."""

    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    def work(self, seconds):
        """Wall-clock spent between ticks, as real work would."""
        self.now += seconds


def pacer(fps):
    """A bare Sapphire wired for pacing only -- no ROM, no core."""
    emu = Sapphire.__new__(Sapphire)
    emu.target_fps = fps
    clock = FakeClock()
    emu._clock = clock.time
    emu._sleep = clock.sleep
    emu._frame_due = None
    return emu, clock


def test_unthrottled_never_sleeps():
    """The default has to stay flat out: the grind depends on it."""
    emu, clock = pacer(None)
    for _ in range(10):
        emu._pace(60)
    assert clock.slept == []


def test_paces_a_second_of_frames_into_a_second():
    emu, clock = pacer(60.0)
    emu._pace(60)
    assert clock.slept == pytest.approx([1.0])


def test_rate_does_not_drift_when_work_takes_time():
    """The deadline is cumulative, so slow work is absorbed, not added to.

    Sleeping a flat `frames / fps` per call would make every tick cost its
    own duration PLUS however long the surrounding work took, so a run would
    steadily fall behind real time. Here 120 frames at 60fps must take two
    seconds of wall clock whatever happens in between.
    """
    emu, clock = pacer(60.0)
    emu._pace(60)          # due 1001.0
    clock.work(0.4)        # real work between ticks
    emu._pace(60)          # due 1002.0, so only 0.6 left to sleep
    assert clock.slept == pytest.approx([1.0, 0.6])
    assert clock.now == pytest.approx(1002.0)


def test_work_slower_than_realtime_does_not_sleep():
    emu, clock = pacer(60.0)
    emu._pace(60)
    clock.work(5.0)        # far slower than the frames it produced
    emu._pace(60)
    assert clock.slept == pytest.approx([1.0])   # no second sleep


def test_resyncs_instead_of_sprinting_after_a_long_stall():
    """Falling far behind must NOT be repaid by running fast.

    A BFS or a savestate write can stall for seconds. Catching those frames
    up as fast as possible is exactly the visible stutter the throttle exists
    to remove, so the deadline is reset to now once it is more than a second
    stale and the next tick paces normally again.
    """
    emu, clock = pacer(60.0)
    emu._pace(60)
    clock.work(30.0)                 # a long stall
    emu._pace(60)                    # 29s behind -> resync, no sleep
    assert len(clock.slept) == 1
    before = clock.now
    emu._pace(60)                    # back to normal pacing
    assert clock.now == pytest.approx(before + 1.0)


def test_hardware_rate_is_the_real_gba_refresh():
    """59.7275 Hz, not 60: 280896 cycles a frame off a 16.78 MHz clock."""
    assert HARDWARE_FPS == pytest.approx(59.7275)
    assert _fps_from_env("hardware") == HARDWARE_FPS
    emu, clock = pacer(HARDWARE_FPS)
    emu._pace(round(HARDWARE_FPS))
    assert clock.slept[0] == pytest.approx(1.0, abs=0.01)


@pytest.mark.parametrize(
    "value,want",
    [
        (None, None), ("", None), ("0", None), ("off", None), ("none", None),
        ("max", None), ("bogus", None), ("-5", None),
        ("hardware", HARDWARE_FPS), ("hw", HARDWARE_FPS), ("gba", HARDWARE_FPS),
        ("60", 60.0), ("30", 30.0), ("59.7275", 59.7275),
    ],
)
def test_env_parsing(value, want):
    """A human sets this from a shell, so nonsense must degrade, not raise."""
    assert _fps_from_env(value) == want
