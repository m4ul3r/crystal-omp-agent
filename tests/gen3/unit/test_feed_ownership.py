"""One feed, one writer.

This is the actual cause of the reported "pop-in / flash / flickering", found
after three separate attempts to filter it in the publisher. Three processes
were writing `live/default.*` at the same time: two `league_loop.py` runs that
had been respawning under an `on-failure` restart policy for four hours (68
restarts between them, entirely unnoticed) and a `collect.py` sweep.

Three emulator timelines interleaving into one file at roughly 36 Hz reads as
violent flicker, and no fade heuristic in the publisher can repair it -- the
frames are each individually valid, they just belong to different games. The
tell was arithmetic: the feed's own frame counter went BACKWARD by 1.9 million
between two reads, and `frames_published` rose by 85,679 in six seconds at a
nominal 12 Hz.

The in-process guard on `emu.observer` said nothing about any of it, because
it only ever knew about one interpreter.
"""

import os

import pytest

from pokeagent.live import LiveFeed, _alive


@pytest.mark.unit
def test_a_live_pid_is_alive_and_a_free_one_is_not():
    assert _alive(os.getpid())
    # A pid that cannot exist. -1 would signal a process GROUP, so use a value
    # that is merely absent rather than meaningful.
    assert not _alive(2 ** 22)


@pytest.mark.unit
def test_claiming_writes_our_pid(tmp_path):
    f = LiveFeed("solo", directory=tmp_path)
    f._claim()
    assert f.owner_path.read_text().strip() == str(os.getpid())


@pytest.mark.unit
def test_a_second_live_writer_is_refused(tmp_path):
    """The whole point: the second process must fail loudly instead of
    quietly interleaving a different game into the same file."""
    f = LiveFeed("contested", directory=tmp_path)
    f.owner_path.parent.mkdir(parents=True, exist_ok=True)
    f.owner_path.write_text(f"{os.getpid()}\n")

    other = LiveFeed("contested", directory=tmp_path)
    # Pretend the claim belongs to a different, living process.
    other.owner_path.write_text("1\n")           # pid 1 always exists

    with pytest.raises(RuntimeError, match="already being written by pid"):
        other._claim()


@pytest.mark.unit
def test_a_stale_claim_is_reclaimed_silently(tmp_path):
    """A crashed run must not require manual cleanup -- that would turn a
    crash into a permanently unusable feed name."""
    f = LiveFeed("crashed", directory=tmp_path)
    f.owner_path.parent.mkdir(parents=True, exist_ok=True)
    f.owner_path.write_text(f"{2 ** 22}\n")      # a pid that is long gone

    f._claim()

    assert f.owner_path.read_text().strip() == str(os.getpid())


@pytest.mark.unit
def test_reclaiming_our_own_feed_is_not_an_error(tmp_path):
    """Re-attaching in one process is legitimate and must not trip the guard."""
    f = LiveFeed("mine", directory=tmp_path)
    f._claim()
    f._claim()
    assert f.owner_path.read_text().strip() == str(os.getpid())


@pytest.mark.unit
def test_release_only_removes_our_own_claim(tmp_path):
    f = LiveFeed("shared", directory=tmp_path)
    f.owner_path.parent.mkdir(parents=True, exist_ok=True)
    f.owner_path.write_text("1\n")               # someone else's

    f._release()

    assert f.owner_path.exists(), "released a claim belonging to another pid"


@pytest.mark.unit
def test_release_clears_ours(tmp_path):
    f = LiveFeed("temp", directory=tmp_path)
    f._claim()
    f._release()
    assert not f.owner_path.exists()


@pytest.mark.unit
def test_a_malformed_claim_does_not_wedge_the_feed(tmp_path):
    f = LiveFeed("garbled", directory=tmp_path)
    f.owner_path.parent.mkdir(parents=True, exist_ok=True)
    f.owner_path.write_text("not-a-pid")

    f._claim()          # must not raise

    assert f.owner_path.read_text().strip() == str(os.getpid())
