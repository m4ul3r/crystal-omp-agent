"""Live feed: the emulator that is BEING DRIVEN publishes its own frames.

watch.py used to *re-simulate*: it loaded `saves/<x>.state` into a second
emulator and ticked that copy forward at ~240x so battles looked animated.
Two lies came out of that. The copy diverged from the agent's timeline the
moment it ticked (its own RNG, its own script timers), and between two
`d.save()` calls the viewer had nothing new to show -- so an intro, a
shopping trip, or a five-minute journey was invisible.

The feed inverts the flow. The driving process installs itself as the
emulator's tick observer (`Crystal.observe`); `tick()` then advances in
short slices, and once every 1/fps of REAL time the observer renders the
frame it is actually running and drops three artefacts into `live/<name>`:

    <name>.png     the real frame, atomic replace
    <name>.json    game_state + status line + map rows, atomic replace
    <name>.jsonl   narration: this process's own log lines, appended

Nothing is read back, nothing blocks the driver on a viewer, and a missing
or stale feed is just "idle" in the UI. `speed` optionally paces the
emulator against the wall clock (speed=1 -> 60 emulated frames per real
second) so a human can actually follow the play.

Publisher (driving process):

    feed = LiveFeed(d.emu, d.names, nav=d.nav, name="watch", speed=2).attach()
    feed.note("heading for the bedroom")     # optional; log lines are automatic

Consumer (viewer):

    r = FeedReader("watch"); r.alive(), r.state(), r.png(), r.notes()
"""
import io
import json
import logging
import os
import time
from pathlib import Path

from . import missables, paths
from .nav import WARPS, HOPS
from .state import game_state, live_sprites, status_line

PNG_SUFFIX, JSON_SUFFIX, LOG_SUFFIX = ".png", ".json", ".jsonl"

# Loggers whose INFO lines become feed narration. These are the harness's
# own loggers -- every control surface (trek legs, serve, autopilot, the
# CLI) logs through them, so narration needs no call-site changes.
NARRATED = ("trek", "crystalagent", "serve", "autopilot")

GRASS_TILES = (0x14, 0x18)
WATER_TILE = 0x29
NOTE_MAX = 240            # a log line longer than this is truncated
NOTE_KEEP = 4000          # feed log rotates itself at this many lines
_ITEMBALLS_BY_MAP = None
_EVENT_BITS = None


def item_cells(emu, nav, map_const):
    """Uncollected static item-ball cells on ``map_const``.

    Item balls outside the camera are absent from ``wObjectStructs`` (the
    game only instantiates nearby sprites), so the live-NPC overlay cannot
    surface them. The disassembly is the complete object table; the live
    event bit decides whether each parsed ball still exists.
    """
    global _ITEMBALLS_BY_MAP, _EVENT_BITS
    if _ITEMBALLS_BY_MAP is None:
        sources = missables.parse_item_sources(
            paths.REPO_ROOT, lambda stem: nav.resolve(stem) or stem)
        by_map = {}
        for src in sources:
            if src.kind == "itemball" and src.x is not None:
                by_map.setdefault(src.map, []).append(src)
        _ITEMBALLS_BY_MAP = by_map
        _EVENT_BITS = missables.event_bits(paths.REPO_ROOT)

    bank, base = emu.sym["wEventFlags"]
    out = {}
    for src in _ITEMBALLS_BY_MAP.get(map_const, ()):
        collected = False
        bit = _EVENT_BITS.get(src.event) if src.event else None
        if bit is not None:
            collected = bool(emu.read((bank, base + bit // 8))[0]
                             >> (bit % 8) & 1)
        if not collected:
            out[(src.x, src.y)] = src.item
    return out


def feed_paths(name, directory=None):
    d = Path(directory or paths.LIVE_DIR)
    return (d / f"{name}{PNG_SUFFIX}", d / f"{name}{JSON_SUFFIX}",
            d / f"{name}{LOG_SUFFIX}")


def list_feeds(directory=None):
    """[{name, age_s}] for every feed in the live dir, freshest first."""
    d = Path(directory or paths.LIVE_DIR)
    now = time.time()
    out = []
    for p in d.glob(f"*{JSON_SUFFIX}"):
        try:
            out.append({"name": p.name[:-len(JSON_SUFFIX)],
                        "age_s": max(0.0, now - p.stat().st_mtime)})
        except OSError:
            continue
    return sorted(out, key=lambda f: f["age_s"])


def _atomic_write(path, data):
    """Write-then-rename so a reader never sees half a frame."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def map_rows(emu, names, nav, gs):
    """Current collision grid with player (``@``), live NPCs (``N``), and
    every uncollected static item ball (``I``). None when the map has no
    decoded grid -- the caller must not invent one."""
    loc = gs["location"]
    const = names.maps.get((loc["map_group"], loc["map_number"]))
    if const is None or const not in nav.consts:
        return None
    try:
        grid = nav.grid(const)
    except KeyError:
        return None
    px, py = loc["x"], loc["y"]
    npcs = {(s["map_x"], s["map_y"]) for s in live_sprites(emu) if s["slot"]}
    try:
        items = item_cells(emu, nav, const)
    except (AttributeError, KeyError, OSError, ValueError):
        items = {}

    def glyph(x, y, c):
        if (x, y) == (px, py):
            return "@"
        if (x, y) in items:
            return "I"
        if (x, y) in npcs:
            return "N"
        if c == 0x00:
            return "."
        if c in GRASS_TILES:
            return '"'
        if c in WARPS:
            return "W"
        if c in HOPS:
            return {"R": ">", "L": "<", "U": "^", "D": "v"}[HOPS[c]]
        if c == WATER_TILE:
            return "~"
        return "#"

    return ["".join(glyph(x, y, c) for x, c in enumerate(row))
            for y, row in enumerate(grid)]


class _Narrator(logging.Handler):
    """Bridges the driver's existing log lines into the feed, so the viewer
    reports what the agent DECIDED ("goto: ...", "auto: attack slot 0")
    instead of guessing from diffed snapshots."""

    def __init__(self, feed):
        super().__init__(level=logging.INFO)
        self.feed = feed

    def emit(self, record):
        try:
            msg = record.getMessage().strip()
        except Exception:
            return
        if msg:
            self.feed.note(msg, src="log", level=record.levelname)

    def handleError(self, record):
        pass          # a broken feed must never break the driver's logging


class LiveFeed:
    """Publisher half. Owns no emulator: it renders and reads the one it is
    attached to, from that emulator's own thread, inside `tick()`."""

    def __init__(self, emu, names, nav=None, name="live", fps=12.0,
                 state_hz=4.0, speed=0.0, slice_frames=8, directory=None):
        self.emu = emu
        self.names = names
        self.nav = nav
        self.name = name
        self.fps = max(0.5, float(fps))
        self.state_hz = max(0.2, float(state_hz))
        self.speed = max(0.0, float(speed))       # 0 = as fast as it runs
        self.slice_frames = max(1, int(slice_frames))
        self.png_path, self.json_path, self.log_path = \
            feed_paths(name, directory)
        self.png_path.parent.mkdir(parents=True, exist_ok=True)
        self.frames_published = 0
        self.errors = 0
        self._next_frame = 0.0
        self._next_state = 0.0
        self._pace_anchor = None      # (wall clock, emulated frames) pair
        self._pace_frames = 0
        self._notes = self._count_notes()
        self._handler = None
        self._attached_to = []

    # -- publisher lifecycle ----------------------------------------------

    def attach(self):
        """Take over the emulator's tick slicing and the harness loggers."""
        self.emu.observe(self)
        self._handler = _Narrator(self)
        for name in NARRATED:
            lg = logging.getLogger(name)
            # A handler only sees records that pass the logger's own level;
            # watching implies narration, so lift INFO through.
            if lg.getEffectiveLevel() > logging.INFO:
                lg.setLevel(logging.INFO)
            lg.addHandler(self._handler)
            self._attached_to.append(lg)
        self.note(f"live feed attached ({self.name}: {self.fps:g} fps, "
                  f"speed={'max' if not self.speed else f'{self.speed:g}x'})",
                  src="feed")
        self.publish(render=True)
        return self

    def detach(self):
        # publish BEFORE unhooking: the last rendered frame is whatever the
        # fps throttle happened to owe, so a leg that ends after a menu
        # leaves the viewer staring at that menu forever
        try:
            self.publish(render=True)
        except Exception as err:      # module has no `log`; name it here
            logging.getLogger(__name__).debug(
                f"live feed: final publish failed ({err})")
        self.emu.observe(None)
        for lg in self._attached_to:
            lg.removeHandler(self._handler)
        self._attached_to = []
        self._handler = None
        self.note("live feed detached", src="feed")

    def __enter__(self):
        return self.attach()

    def __exit__(self, *exc):
        self.detach()
        return False

    # -- tick observer protocol (see Crystal.tick) ------------------------

    def due(self):
        """True when the next slice must be RENDERED (a frame is owed)."""
        return time.monotonic() >= self._next_frame

    def after_slice(self, frames, rendered):
        if rendered:
            self.publish(render=False)
        if self.speed:
            self._pace(frames)

    def _pace(self, frames):
        """Hold the emulator to `speed` x real time. The anchor resets when
        we fall more than a second behind so a slow stretch never buys a
        fast-forward burst afterwards."""
        now = time.monotonic()
        if self._pace_anchor is None:
            self._pace_anchor, self._pace_frames = now, 0
        self._pace_frames += frames
        target = self._pace_anchor + self._pace_frames / (60.0 * self.speed)
        drift = target - now
        if drift > 0:
            time.sleep(min(drift, 0.25))
        elif drift < -1.0:
            self._pace_anchor, self._pace_frames = now, 0

    # -- publishing --------------------------------------------------------

    def publish(self, render=True):
        """Write the current frame (and, at state_hz, the state snapshot).
        `render` ticks one frame first -- callers inside tick() already
        rendered the last frame of their slice."""
        now = time.monotonic()
        self._next_frame = now + 1.0 / self.fps
        try:
            if render:
                self.emu.py.tick(1, True)
            buf = io.BytesIO()
            self.emu.py.screen.image.save(buf, format="PNG")
            _atomic_write(self.png_path, buf.getvalue())
            self.frames_published += 1
        except Exception:
            self.errors += 1
        if now >= self._next_state:
            self._next_state = now + 1.0 / self.state_hz
            try:
                _atomic_write(self.json_path, json.dumps(
                    self.snapshot(), ensure_ascii=False).encode())
            except Exception:
                self.errors += 1

    def snapshot(self):
        """State published alongside the frame. Boot/intro screens have no
        game yet, so a failed read is REPORTED, never faked."""
        out = {"name": self.name, "t": time.time(), "fps": self.fps,
               "speed": self.speed, "frame": self.emu.frame, "source": "live"}
        try:
            gs = game_state(self.emu, self.names, include_screen=True)
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"
            try:
                out["screen"] = self.emu.screen_text()
            except Exception:
                pass
            return out
        out.update(gs)
        out["status"] = status_line(gs)
        if self.nav is not None:
            try:
                out["map"] = map_rows(self.emu, self.names, self.nav, gs)
            except Exception:
                out["map"] = None
        return out

    # -- narration ---------------------------------------------------------

    def _count_notes(self):
        """Existing line count, so note indices stay monotonic across runs."""
        try:
            with open(self.log_path, "rb") as f:
                return sum(1 for _ in f)
        except OSError:
            return 0

    def note(self, msg, src="agent", level="INFO"):
        """Append one narration line to the feed log."""
        msg = str(msg).strip().replace("\n", " ")
        if not msg:
            return
        if len(msg) > NOTE_MAX:
            msg = msg[:NOTE_MAX - 1] + "…"
        row = {"i": self._notes, "frame": self.emu.frame,
               "t": time.strftime("%H:%M:%S"), "msg": msg, "src": src,
               "level": level}
        self._notes += 1
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            self.errors += 1
        if self._notes % NOTE_KEEP == 0:
            self._rotate_notes()

    def _rotate_notes(self):
        """Keep the log bounded without breaking readers: drop the oldest
        half, keeping indices (readers follow byte offsets and re-sync)."""
        try:
            rows = self.log_path.read_text(encoding="utf-8").splitlines()
            _atomic_write(self.log_path,
                          ("\n".join(rows[-NOTE_KEEP // 2:]) + "\n").encode())
        except OSError:
            self.errors += 1


class FeedReader:
    """Consumer half: read-only view of one feed. Never touches the
    publisher's emulator and never writes to the live dir."""

    def __init__(self, name, directory=None):
        self.name = name
        self.png_path, self.json_path, self.log_path = \
            feed_paths(name, directory)
        self._log_pos = None       # byte offset; None = start at EOF

    @property
    def age(self):
        """Seconds since the state file was written; inf when there is none."""
        try:
            return max(0.0, time.time() - self.json_path.stat().st_mtime)
        except OSError:
            return float("inf")

    def alive(self, ttl=4.0):
        return self.age <= ttl

    def png(self):
        try:
            return self.png_path.read_bytes() or None
        except OSError:
            return None

    def png_stamp(self):
        """(mtime_ns, size) of the frame file, for change detection."""
        try:
            st = self.png_path.stat()
            return st.st_mtime_ns, st.st_size
        except OSError:
            return None

    def state(self):
        try:
            return json.loads(self.json_path.read_bytes())
        except (OSError, ValueError):
            return None

    def notes(self, backfill=0):
        """Narration lines appended since the last call. The first call on
        an EXISTING log returns its last `backfill` lines (0 = start live at
        EOF); a log that does not exist yet is followed from its first line,
        so a viewer opened before the driver misses nothing."""
        try:
            data = self.log_path.read_bytes()
        except OSError:
            if self._log_pos is None:
                self._log_pos = 0
            return []
        if self._log_pos is None:
            rows = _decode_notes(data)
            self._log_pos = len(data)
            return rows[-backfill:] if backfill else []
        if self._log_pos > len(data):        # rotated/truncated: re-sync
            self._log_pos = 0
        chunk = data[self._log_pos:]
        cut = chunk.rfind(b"\n") + 1         # ignore a half-written line
        if cut <= 0:
            return []
        self._log_pos += cut
        return _decode_notes(chunk[:cut])


def _decode_notes(data):
    rows = []
    for line in data.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows
