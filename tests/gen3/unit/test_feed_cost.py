"""The feed must not cost the run its speed.

Attaching the widget feed once throttled the emulator from 1028 fps to 12 --
an 87x slowdown -- because every publish rebuilt the rich status blocks
(311 ms a snapshot, 253 ms of it the stage ladder rebuilding the living-dex
evolution chains) at 4 Hz. An unattended run then managed one battle in five
minutes, and the loop looked wedged rather than slow.

These tests pin the fix without measuring wall-clock time, which would flake:
the expensive blocks are derived from a cheap fingerprint, so the assertion is
"how many times was the expensive path entered", not "how fast was it".
"""

import pytest

from pokeagent.live import LiveFeed

pytestmark = pytest.mark.unit


class FakeMon:
    def __init__(self, species=1, level=5, is_egg=False):
        self.species = species
        self.level = level
        self.is_egg = is_egg
        self.nickname = "EMBER"
        self.hp = 20
        self.max_hp = 20
        self.status_line = ""
        self.fainted = False


class FakeState:
    def __init__(self):
        self._party = [FakeMon()]
        self._badges = 0

    def party(self):
        return list(self._party)

    def badges(self):
        return self._badges


class FakeEmu:
    frame = 0
    observer = None


class FakeDriver:
    def __init__(self):
        self.state = FakeState()
        self.emu = FakeEmu()

    def in_battle(self):
        return False


@pytest.fixture()
def feed_and_driver():
    feed = LiveFeed("test-cost")
    driver = FakeDriver()
    feed.driver = driver
    builds = []

    def counted(drv, in_battle):
        builds.append(1)
        return {"objective": {"name": "x", "percent": 0.0}}

    feed._build_extras = counted
    return feed, driver, builds


def test_repeated_publishes_derive_the_rich_blocks_once(feed_and_driver):
    feed, driver, builds = feed_and_driver
    for _ in range(50):
        feed._extras(driver, False)
    assert len(builds) == 1, "50 publishes must not mean 50 dex recounts"


def test_the_cached_block_is_still_returned(feed_and_driver):
    feed, driver, builds = feed_and_driver
    first = feed._extras(driver, False)
    again = feed._extras(driver, False)
    assert again == first
    assert again["objective"]["name"] == "x", "a cache that drops data is worse"


def test_a_level_up_invalidates_the_cache(feed_and_driver):
    """Progress must show up immediately; that is the whole point of the
    widget. Time-based expiry alone would leave a level-up unreported for
    seconds."""
    feed, driver, builds = feed_and_driver
    feed._extras(driver, False)
    driver.state._party[0].level = 6
    feed._extras(driver, False)
    assert len(builds) == 2


def test_a_new_badge_invalidates_the_cache(feed_and_driver):
    feed, driver, builds = feed_and_driver
    feed._extras(driver, False)
    driver.state._badges = 1
    feed._extras(driver, False)
    assert len(builds) == 2


def test_entering_a_battle_invalidates_the_cache(feed_and_driver):
    feed, driver, builds = feed_and_driver
    feed._extras(driver, False)
    feed._extras(driver, True)
    assert len(builds) == 2


def test_a_caught_mon_invalidates_the_cache(feed_and_driver):
    feed, driver, builds = feed_and_driver
    feed._extras(driver, False)
    driver.state._party.append(FakeMon(species=4, level=3))
    feed._extras(driver, False)
    assert len(builds) == 2


def test_the_fingerprint_survives_a_partyless_boot_screen(feed_and_driver):
    """A boot or intro screen has no party and no save block; reading one
    raises, and the feed's contract is to report rather than crash."""
    feed, driver, builds = feed_and_driver

    def boom():
        raise RuntimeError("no save block yet")

    driver.state.party = boom
    driver.state.badges = boom
    key = feed._extras_fingerprint(driver, False)
    assert key == ((), None, False)


def test_time_expiry_still_refreshes_a_static_run(feed_and_driver):
    """Nothing in the fingerprint changes while the player walks, but the dex
    plan's own 'next target' can, so the cache must not be permanent."""
    feed, driver, builds = feed_and_driver
    feed.extras_every = 0.0
    feed._extras(driver, False)
    feed._extras(driver, False)
    assert len(builds) == 2
