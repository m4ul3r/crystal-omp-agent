"""Live feed: the driving emulator publishes its own frames.

Guards the contract watch.py now depends on -- sliced ticks that render
exactly the owed frame, atomic frame/state artefacts, incremental note
reads, log narration, and wall-clock pacing.
"""
import json
import logging
import time

import pytest
from PIL import Image

import crystalagent.live as live_mod
from crystalagent.emu import Crystal
from crystalagent.live import FeedReader, LiveFeed, list_feeds, map_rows

pytestmark = pytest.mark.unit


class FakeScreen:
    def __init__(self):
        self.image = Image.new("RGB", (160, 144), (17, 34, 51))


class FakePy:
    """Stands in for PyBoy: records (count, render) per tick."""

    def __init__(self):
        self.calls = []
        self.frame_count = 0
        self.screen = FakeScreen()

    def tick(self, count=1, render=True, sound=False):
        self.calls.append((count, render))
        self.frame_count += count
        return True


class FakeEmu:
    """Enough emulator for the feed: a screen, a frame counter, and reads
    that fail the way a title screen fails."""

    def __init__(self):
        self.py = FakePy()
        self._observer = None

    @property
    def frame(self):
        return self.py.frame_count

    def observe(self, obs):
        self._observer = obs

    def screen_text(self):
        return ["TITLE" + " " * 15] * 18

    def read(self, *a, **k):
        raise ValueError("no game yet")

def feed(tmp_path, **kw):
    kw.setdefault("fps", 1000.0)      # publish on demand in tests
    kw.setdefault("state_hz", 1000.0)
    return LiveFeed(FakeEmu(), names=None, nav=None,
                    name=kw.pop("name", "t"), directory=tmp_path, **kw)


# -- tick slicing -----------------------------------------------------------

def test_tick_without_observer_is_one_batch():
    emu = Crystal.__new__(Crystal)          # no ROM needed for tick()
    emu.py = FakePy()
    emu._observer = None
    emu.tick(40)
    assert emu.py.calls == [(40, False)]


def test_tick_slices_and_renders_only_the_owed_frame():
    emu = Crystal.__new__(Crystal)
    emu.py = FakePy()
    emu._observer = None

    class Obs:
        slice_frames = 8

        def __init__(self):
            self.owe = [True, False, False, True, False]
            self.seen = []

        def due(self):
            return self.owe.pop(0)

        def after_slice(self, n, rendered):
            self.seen.append((n, rendered))

    obs = Obs()
    emu.observe(obs)
    emu.tick(40)
    assert emu.py.calls == [(8, True), (8, False), (8, False),
                            (8, True), (8, False)]
    assert obs.seen == emu.py.calls          # observer sees what pyboy did


def test_tick_slice_never_overshoots_the_request():
    emu = Crystal.__new__(Crystal)
    emu.py = FakePy()
    emu._observer = None
    emu.observe(type("O", (), {"slice_frames": 8,
                               "due": lambda self: False,
                               "after_slice": lambda self, n, r: None})())
    emu.tick(10)
    assert emu.py.calls == [(8, False), (2, False)]
    assert sum(c for c, _ in emu.py.calls) == 10


# -- publishing -------------------------------------------------------------

def test_publish_writes_frame_and_reports_unreadable_state(tmp_path):
    f = feed(tmp_path)
    f.publish(render=True)
    r = FeedReader("t", directory=tmp_path)
    assert r.alive(ttl=30)
    assert r.png()[:8] == b"\x89PNG\r\n\x1a\n"
    s = r.state()
    assert s["source"] == "live" and s["name"] == "t"
    # a boot/intro screen has no game to read: SAID, not faked
    assert s["error"] and "party" not in s
    assert s["screen"][0].startswith("TITLE")


def test_dead_feed_reads_as_dead(tmp_path):
    r = FeedReader("nothing", directory=tmp_path)
    assert not r.alive()
    assert r.age == float("inf")
    assert r.png() is None and r.state() is None and r.notes() == []


def test_list_feeds_sorts_by_freshness(tmp_path):
    feed(tmp_path, name="old").publish(render=True)
    time.sleep(0.01)
    LiveFeed(FakeEmu(), names=None, nav=None, name="new",
             directory=tmp_path).publish(render=True)
    assert [f["name"] for f in list_feeds(tmp_path)] == ["new", "old"]


def test_publish_is_atomic(tmp_path):
    """Readers must never see a half-written frame: no .tmp left behind and
    the published bytes always decode."""
    f = feed(tmp_path)
    for _ in range(3):
        f.publish(render=True)
    assert not list(tmp_path.glob("*.tmp"))
    Image.open(FeedReader("t", directory=tmp_path).png_path).load()


def test_state_rate_limited_below_frame_rate(tmp_path):
    f = feed(tmp_path, fps=1000.0, state_hz=0.25)
    f.publish(render=True)
    first = f.json_path.stat().st_mtime_ns
    f.publish(render=True)                   # frame yes, state not yet due
    assert f.json_path.stat().st_mtime_ns == first
    assert f.frames_published == 2


# -- narration --------------------------------------------------------------

def test_notes_are_incremental_and_start_at_eof(tmp_path):
    f = feed(tmp_path)
    f.note("before the viewer connected")
    r = FeedReader("t", directory=tmp_path)
    assert r.notes() == []                   # first read starts live
    f.note("goto (3,3)")
    f.note("arrived")
    rows = r.notes()
    assert [x["msg"] for x in rows] == ["goto (3,3)", "arrived"]
    assert [x["i"] for x in rows] == [1, 2]
    assert r.notes() == []                   # nothing new


def test_notes_backfill_returns_recent_history(tmp_path):
    f = feed(tmp_path)
    for i in range(5):
        f.note(f"line {i}")
    rows = FeedReader("t", directory=tmp_path).notes(backfill=2)
    assert [x["msg"] for x in rows] == ["line 3", "line 4"]


def test_partial_last_line_is_not_consumed(tmp_path):
    f = feed(tmp_path)
    f.note("complete")
    r = FeedReader("t", directory=tmp_path)
    r.notes()
    with open(f.log_path, "a", encoding="utf-8") as fh:
        fh.write('{"i": 9, "msg": "half writ')
    assert r.notes() == []                   # waits for the newline
    with open(f.log_path, "a", encoding="utf-8") as fh:
        fh.write('ten"}\n')
    assert [x["msg"] for x in r.notes()] == ["half written"]


def test_driver_log_lines_become_notes(tmp_path):
    f = feed(tmp_path)
    r = FeedReader("t", directory=tmp_path)
    r.notes()
    log = logging.getLogger("trek")
    f.attach()
    try:
        log.info("goto: BFS 12 steps -> (3,3)")
        log.warning("no-path (blocked)")
    finally:
        f.detach()
    rows = [x for x in r.notes() if x["src"] == "log"]
    assert [x["msg"] for x in rows] == ["goto: BFS 12 steps -> (3,3)",
                                        "no-path (blocked)"]
    assert rows[1]["level"] == "WARNING"
    assert log.level <= logging.INFO         # INFO lifted so handlers see it
    assert f.emu._observer is None            # detach released the emulator


def test_long_notes_are_truncated(tmp_path):
    f = feed(tmp_path)
    r = FeedReader("t", directory=tmp_path)
    r.notes()
    f.note("x" * 500)
    msg = r.notes()[0]["msg"]
    assert len(msg) == 240 and msg.endswith("…")


# -- pacing -----------------------------------------------------------------

def test_pace_holds_the_requested_speed(tmp_path, monkeypatch):
    f = feed(tmp_path, speed=1.0)
    clock, slept = [100.0], []
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    f.after_slice(6, rendered=False)         # 6 frames owe 0.1 s at 1x
    assert slept and abs(slept[-1] - 0.1) < 1e-9
    clock[0] += 0.1
    f.after_slice(6, rendered=False)
    assert abs(slept[-1] - 0.1) < 1e-9


def test_pace_never_buys_a_fastforward_burst(tmp_path, monkeypatch):
    f = feed(tmp_path, speed=1.0)
    clock, slept = [100.0], []
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    f.after_slice(6, rendered=False)
    clock[0] += 5.0                          # a long stall (disk, a big read)
    f.after_slice(6, rendered=False)
    assert len(slept) == 1                   # anchor reset, no catch-up sprint
    clock[0] += 0.0
    f.after_slice(6, rendered=False)
    assert abs(slept[-1] - 0.1) < 1e-9


def test_speed_zero_does_not_sleep(tmp_path, monkeypatch):
    f = feed(tmp_path, speed=0.0)
    monkeypatch.setattr(time, "sleep",
                        lambda s: pytest.fail("unlimited feed slept"))
    f.after_slice(8, rendered=False)


# -- map rows ---------------------------------------------------------------

class MapEmu:
    """Only wObjectStructs is read by map_rows."""

    def __init__(self, npcs=()):
        from crystalagent.state import (NUM_OBJECT_STRUCTS, OBJECT_LENGTH,
                                        _OBJ_MAP_X, _OBJ_MAP_Y)
        buf = bytearray(NUM_OBJECT_STRUCTS * OBJECT_LENGTH)
        buf[0] = 1                                     # player slot occupied
        for slot, (x, y) in enumerate(npcs, start=1):
            base = slot * OBJECT_LENGTH
            buf[base] = 1
            buf[base + _OBJ_MAP_X] = x + 4
            buf[base + _OBJ_MAP_Y] = y + 4
        self.buf = bytes(buf)

    def read(self, name, length=1):
        assert name == "wObjectStructs"
        return self.buf[:length]


class FakeNav:
    def __init__(self, grid):
        self.consts = {"ROOM": object()}
        self._grid = grid

    def grid(self, const):
        return self._grid


class FakeNames:
    maps = {(1, 2): "ROOM"}


def test_map_rows_marks_player_npcs_and_terrain():
    from crystalagent.nav import WARPS

    warp = sorted(WARPS)[0]
    grid = [[0x00, 0x14, warp], [0x29, 0x01, 0x00]]
    gs = {"location": {"map_group": 1, "map_number": 2, "x": 0, "y": 0}}
    rows = map_rows(MapEmu(npcs=[(2, 1)]), FakeNames(), FakeNav(grid), gs)
    assert rows == ['@"W', '~#N']


def test_map_rows_marks_static_item_balls_even_when_not_live_sprites(
        monkeypatch):
    monkeypatch.setattr(
        live_mod, "item_cells",
        lambda emu, nav, const: {(1, 0): "ANTIDOTE"})
    grid = [[0x00, 0x00, 0x00]]
    gs = {"location": {"map_group": 1, "map_number": 2, "x": 0, "y": 0}}
    rows = map_rows(MapEmu(), FakeNames(), FakeNav(grid), gs)
    assert rows == ["@I."]


def test_map_rows_is_none_without_a_decoded_grid():
    gs = {"location": {"map_group": 9, "map_number": 9, "x": 0, "y": 0}}
    assert map_rows(MapEmu(), FakeNames(), FakeNav([[0]]), gs) is None
