"""Live feed: the emulator that is BEING DRIVEN publishes its own frames.

The obvious way to build a viewer is to load ``saves/<x>.state`` into a second
emulator and tick that copy forward.  The Crystal harness did exactly that and
it lied twice.  The copy diverges from the agent's timeline the instant it ticks
(its own RNG, its own script timers), and between two ``save()`` calls the
viewer has nothing new to show -- so a five-minute walk to Petalburg, a whole
battle, an intro cutscene, all invisible.

So the flow is inverted here.  The driving process installs itself as the
emulator's tick observer.  ``Sapphire.tick`` (pokeagent/emu.py:241-254)
already advances in ``observer.slice_frames`` slices and calls
``observer.after_slice(emu)`` between them; the feed uses that hook to drop
three artefacts into ``paths.LIVE_DIR`` once every 1/fps of REAL time:

    <name>.png     the framebuffer the agent is actually running
    <name>.json    a compact snapshot: map, party, badges, money, message
    <name>.jsonl   narration: this process's own log lines, appended

Nothing is ever read back and nothing blocks the driver on a viewer.  Every
write is write-then-``os.replace`` because the desktop widget polls these paths
on its own clock and must never see half a frame or half a JSON object.

Publishing is best-effort by construction: a full disk, a vanished live dir or
a PNG encoder hiccup must not end a run that has been playing for an hour.
Failures are counted in ``errors`` and explained in ``last_publish_reason``
rather than swallowed (DESIGN rule 6), and the reason is logged once per
distinct cause so a persistent fault is visible without spamming the log.

Usage:

    d = Driver(state_path="saves/littleroot.state")
    feed = LiveFeed("default").attach(d)
    ...   # every d.goto()/d.fight() now streams
    feed.detach()
"""

import collections
import io

from PIL import ImageStat
import json
import logging
import itertools
import os
import time
from pathlib import Path

from . import paths

PNG_SUFFIX, JSON_SUFFIX, LOG_SUFFIX = ".png", ".json", ".jsonl"

#: Loggers whose records become feed narration.  These are the harness's own
#: loggers, so the widget reports what the agent DECIDED ("goto: Route 101",
#: "menu: bag") instead of guessing it from diffed snapshots -- and no call
#: site needs to know the feed exists.  ``pokeagent`` covers every
#: submodule; ``serve``/``newgame`` are the control surfaces above it.
NARRATED = ("trek", "pokeagent", "serve", "newgame")

#: A narration line longer than this is truncated; the widget shows one line.
NOTE_MAX = 240
#: The narration log rotates itself at this many lines, dropping the oldest
#: half.  A run left going overnight must not fill the disk.
NOTE_KEEP = 4000

log = logging.getLogger("pokeagent.live")


def feed_paths(name, directory=None) -> tuple[Path, Path, Path]:
    """``(png, json, jsonl)`` for one feed name."""
    d = Path(directory or paths.LIVE_DIR)
    return (
        d / f"{name}{PNG_SUFFIX}",
        d / f"{name}{JSON_SUFFIX}",
        d / f"{name}{LOG_SUFFIX}",
    )


def list_feeds(directory=None) -> list[dict]:
    """``[{name, age_s, live}]`` for every feed in the live dir, freshest
    first.  Used to answer "is anything playing right now" without opening an
    emulator."""
    d = Path(directory or paths.LIVE_DIR)
    out = []
    try:
        entries = sorted(d.glob(f"*{JSON_SUFFIX}"))
    except OSError:
        return out
    for path in entries:
        try:
            age = max(0.0, time.time() - path.stat().st_mtime)
        except OSError:
            continue
        live = None
        try:
            live = json.loads(path.read_bytes()).get("live")
        except (OSError, ValueError):
            pass
        out.append({"name": path.stem, "age_s": round(age, 2), "live": live})
    return sorted(out, key=lambda f: f["age_s"])


#: Unique suffix source for _atomic_write's sibling temp files.
_TMP_SEQ = itertools.count()


def _alive(pid: int) -> bool:
    """Is this pid still running?

    Signal 0 checks for existence without delivering anything. A stale claim
    from a crashed run must be reclaimable without manual cleanup, so
    "process is gone" has to be answerable cheaply and without raising.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # exists, owned by someone else
    except Exception:  # noqa: BLE001
        return False
    return True


def _atomic_write(path: Path, data: bytes) -> None:
    """Write-then-rename, so a reader polling this path never sees a partial
    file.  The temp file is a sibling because ``os.replace`` is only atomic
    within one filesystem.

    The temp name is UNIQUE per write.  It used to be a fixed
    ``.default.png.tmp``, so two publishers racing -- the emulator's tick
    observer and a driver publishing directly -- would have one ``os.replace``
    consume the sibling the other was still writing, and the loser raised
    ``FileNotFoundError`` and dropped its frame entirely.  A dropped frame is
    a visible flash in the widget, which is the third distinct cause of the
    flicker this run has chased.
    """
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{next(_TMP_SEQ)}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


class _Narrator(logging.Handler):
    """Bridges the harness's existing log records into the feed's narration
    file.  Deliberately swallows its own failures: a broken feed must never
    break the driver's logging, which is how the run gets debugged."""

    def __init__(self, feed: "LiveFeed"):
        super().__init__(level=logging.INFO)
        self.feed = feed

    def emit(self, record):
        try:
            msg = record.getMessage().strip()
        except Exception:
            return
        if msg:
            self.feed.note(msg, src=record.name, level=record.levelname)

    def handleError(self, record):
        pass


class LiveFeed:
    """Publisher half of the feed.  Owns no emulator: it renders and reads the
    one it is attached to, from that emulator's own thread, inside ``tick()``.

    ``slice_frames`` and ``after_slice`` are the tick-observer protocol
    ``Sapphire.tick`` expects; everything else is publishing.
    """

    def __init__(
        self,
        name: str = "default",
        fps: float = 12.0,
        state_hz: float = 4.0,
        slice_frames: int = 8,
        extras_every: float = 3.0,
        directory=None,
    ):
        self.name = name
        # A frame is a PNG encode plus a rename; a state snapshot decrypts the
        # party. Both are cheap, but neither needs to happen 60 times a second,
        # and the state is what the bar widget polls.
        self.fps = max(0.5, float(fps))
        self.state_hz = max(0.2, float(state_hz))
        self.slice_frames = max(1, int(slice_frames))
        #: Seconds between re-derivations of the rich blocks (objective, dex,
        #: team, stages). They cost ~300 ms and change only when the party,
        #: badges or box do, so they are sampled rather than recomputed per
        #: frame -- see _extras_fingerprint.
        self.extras_every = max(0.0, float(extras_every))
        self._extras_cache = None
        self._extras_key = None
        self._extras_due = 0.0

        self.png_path, self.json_path, self.log_path = feed_paths(name, directory)
        self.png_path.parent.mkdir(parents=True, exist_ok=True)

        #: Anything the play loop wants in the feed that the driver cannot be
        #: asked for -- the finish-time projection is computed from the event
        #: log on disk, not from the cartridge, so nothing here could derive
        #: it. Merged last, and only over keys it owns.
        self.extra = {}

        self.driver = None
        self.frames_published = 0
        #: Fades SEEN. Not the same as fades published: a LONE extreme is
        #: dropped now (see `_publish`), a sustained one is shown.
        self.fades_seen = 0
        #: Lone flashes dropped, and when the current run of extremes began.
        #: The window is the whole mechanism -- it is what separates a fade in
        #: flight from a screen that is genuinely black.
        self.flashes_dropped = 0
        self._extreme_since = None
        #: Brightness of the last few PUBLISHED-or-considered frames, for the
        #: outlier half of `_is_transition`.
        self._means = collections.deque(maxlen=8)
        self.states_published = 0
        self.notes_written = 0
        self.errors = 0
        #: Session counters the widget shows. `steps` and `frames` are added
        #: by the publisher; the rest are bumped by callers via count().
        self.counters = {
            "battles_won": 0, "caught": 0, "faints": 0, "saves": 0, "steps": 0,
        }
        #: Last failure reason per optional status block, so an unavailable
        #: block logs once rather than once per frame.
        self._extra_reasons = {}
        #: None while everything is landing; an explanatory string otherwise.
        self.last_publish_reason = None

        self._next_frame = 0.0
        self._next_state = 0.0
        self._handler = None
        self._narrated = []
        self._notes = self._count_notes()
        self._logged_reason = None

    def __repr__(self):
        return (
            f"<LiveFeed {self.name} fps={self.fps:g} frames={self.frames_published} "
            f"errors={self.errors}{' attached' if self.driver else ''}>"
        )

    # ---- publisher lifecycle -------------------------------------------

    @property
    def owner_path(self):
        """Sidecar naming the process that owns this feed."""
        return self.png_path.with_suffix(".owner")

    def _claim(self) -> None:
        """Take ownership of this feed name, or refuse.

        A stale claim is reclaimed silently -- a crashed run is the common
        case and must not need manual cleanup. A claim held by a process that
        is still alive is a hard error, because the alternative is two
        timelines in one file and a viewer that cannot tell.
        """
        try:
            holder = int(self.owner_path.read_text().split()[0])
        except Exception:  # noqa: BLE001 - absent, empty or malformed
            holder = None
        if holder is not None and holder != os.getpid() and _alive(holder):
            raise RuntimeError(
                f"live feed {self.name!r} is already being written by pid "
                f"{holder}; pass a different --feed name, or stop that "
                f"process. Two writers interleave two different games into "
                f"one file, which reads as flicker and cannot be filtered out"
            )
        self.owner_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.owner_path, f"{os.getpid()}\n".encode())

    def _release(self) -> None:
        """Give up the claim, but only if it is still ours."""
        try:
            if int(self.owner_path.read_text().split()[0]) == os.getpid():
                self.owner_path.unlink()
        except Exception:  # noqa: BLE001 - never let cleanup end a run
            pass

    def attach(self, driver) -> "LiveFeed":
        """Take over the emulator's tick slicing and the harness loggers.

        Refuses to displace another observer: two feeds fighting over
        ``emu.observer`` would silently halve each other's frame rate, and a
        silent halving is exactly the kind of thing nobody notices for a week.

        Also refuses a feed another LIVE PROCESS already owns. That guard is
        the expensive one: the in-process check above says nothing about a
        second interpreter, and three processes -- two respawning league loops
        (68 restarts between them, unnoticed for four hours) and a collector
        -- were all writing `live/default.*` at once. Three emulator
        timelines interleaving into one file at ~36 Hz IS the "pop-in / flash
        / flickering" that got reported, and no amount of fade filtering in
        the publisher can fix a feed with three authors.
        """
        if self.driver is not None:
            raise RuntimeError(f"live feed {self.name!r} is already attached")
        existing = getattr(driver.emu, "observer", None)
        if existing is not None and existing is not self:
            raise RuntimeError(
                f"{driver.emu} already has tick observer {existing!r}; "
                "detach it before attaching this feed"
            )
        self._claim()
        self.driver = driver
        driver.emu.observer = self
        driver.feed = self

        self._handler = _Narrator(self)
        for logger_name in NARRATED:
            lg = logging.getLogger(logger_name)
            # A handler only sees records that clear the logger's own level.
            # Asking to watch a run implies asking for its narration, so lift
            # INFO through rather than publishing an empty log.
            if lg.getEffectiveLevel() > logging.INFO:
                lg.setLevel(logging.INFO)
            lg.addHandler(self._handler)
            self._narrated.append(lg)

        self.note(
            f"live feed attached ({self.name}: {self.fps:g} fps, "
            f"state {self.state_hz:g} Hz) -> {self.png_path.parent}",
            src="feed",
        )
        self.publish(force=True)
        return self

    def detach(self) -> None:
        """Stop observing and leave a final, honest snapshot behind.

        The last published frame is whatever the fps throttle happened to owe,
        so a leg that ends on a menu would otherwise leave the viewer staring
        at that menu forever.  ``live: false`` in the final JSON is what lets
        the widget say "run ended" instead of "feed stale".
        """
        if self.driver is None:
            return
        driver = self.driver
        # Narrate while the driver is still bound: the closing line has to
        # carry the frame it closed on, or the log's last entry reads `null`.
        self.note("live feed detached", src="feed")
        self.driver = None
        if getattr(driver.emu, "observer", None) is self:
            driver.emu.observer = None
        if getattr(driver, "feed", None) is self:
            driver.feed = None

        for lg in self._narrated:
            lg.removeHandler(self._handler)
        self._narrated = []
        self._handler = None

        self._publish(driver.emu, driver, render=True, state=True, live=False)
        # After the final snapshot, so the last frame is still written by the
        # process that owns the feed.
        self._release()

    def __enter__(self) -> "LiveFeed":
        return self

    def __exit__(self, *exc) -> bool:
        self.detach()
        return False

    # ---- tick observer protocol (see Sapphire.tick) ---------------------

    def after_slice(self, emu) -> None:
        """Called by the emulator between input slices.  The framebuffer is
        already current -- mGBA renders every frame into the video buffer we
        handed it -- so there is nothing to re-render, only to encode."""
        if time.monotonic() >= self._next_frame:
            self.publish()

    # ---- publishing -----------------------------------------------------

    def publish(self, force: bool = False) -> bool:
        """Write the current frame, plus the state snapshot when it is due.
        Returns False (and explains itself in ``last_publish_reason``) when
        nothing could be written."""
        if self.driver is None:
            self._fail("live feed is not attached to a driver")
            return False
        now = time.monotonic()
        state_due = force or now >= self._next_state
        return self._publish(
            self.driver.emu, self.driver, render=True, state=state_due, live=True
        )

    #: A frame this close to uniform is a fade, not a picture. Measured on real
    #: play: a mid-fade frame has an extrema spread of 0 (pure black or pure
    #: white) while ordinary overworld frames span nearly the whole range --
    #: the darkest legitimate frame sampled still spread over 90 levels.
    TRANSITION_SPREAD = 24
    #: How far a frame's brightness may stray from its neighbours before it is
    #: treated as a fade in flight. 1.6 was chosen off the measurement that
    #: found the bug: flashes came in at 1.9x the local median, and ordinary
    #: play -- including walking indoors and into caves -- stayed inside 1.3x.
    TRANSITION_RATIO = 1.6
    #: How long a run of extreme frames may be withheld before the truth
    #: publishes anyway, measured in EMULATOR FRAMES rather than wall-clock
    #: seconds.
    #:
    #: Wall-clock was the previous attempt and it leaked, because the emulator
    #: does not run at realtime: a fade that lasts 40 frames in the game can
    #: span well over a second of real time under load, so it outlasted the
    #: window and published. Measured with a 1.2s wall bound live: still 9 of
    #: 106 frames outside the 40-210 band, including 192-byte pure-black ones.
    #:
    #: Frames are the fade's own units and are immune to emulation speed. A
    #: GBA warp or fly fade runs a few dozen frames; a black screen that MEANS
    #: something (a cutscene, a wipe) runs for hundreds. 180 is three seconds
    #: of game time, comfortably between the two.
    FADE_HOLD_FRAMES = 180

    def _is_transition(self, shot) -> bool:
        """Is this frame a fade extreme rather than a view of the game?

        Cheap on purpose: `getextrema()` over a 240x160 buffer is nothing, and
        it runs at the publish rate, not the frame rate. A fade is uniform, so
        the per-channel spread collapses; anything with real content keeps a
        wide spread even when it is dim.
        """
        try:
            rgb = shot.convert("RGB")
            ext = rgb.getextrema()
        except Exception:  # noqa: BLE001 - never let a diagnostic stop a frame
            return False
        if max(hi - lo for lo, hi in ext) <= self.TRANSITION_SPREAD:
            return True
        # Not uniform, but a fade is only uniform at its ENDS. Halfway through
        # one the picture is all there and twice as bright, which is what a
        # flash actually looks like -- the frame that started this
        # investigation had real geometry and a mean of 201 against a
        # neighbourhood of 104. So compare against what the last few frames
        # looked like and withhold the outliers too.
        try:
            mean = sum(ImageStat.Stat(rgb).mean) / 3.0
        except Exception:  # noqa: BLE001
            return False
        recent = self._means
        self._means.append(mean)
        if len(recent) < 4:
            return False
        ordered = sorted(recent)
        median = ordered[len(ordered) // 2]
        if median <= 1.0:
            return False
        ratio = mean / median
        return ratio >= self.TRANSITION_RATIO or ratio <= 1.0 / self.TRANSITION_RATIO

    def _publish(self, emu, driver, render: bool, state: bool, live: bool) -> bool:
        """The two writes, each isolated: a failing PNG encode must not cost
        the run its state snapshot, and neither may cost it the run."""
        now = time.monotonic()
        self._next_frame = now + 1.0 / self.fps
        ok = True
        if render:
            try:
                shot = emu.screenshot()
                # DROP A LONE FLASH, SHOW A SUSTAINED ONE.
                #
                # Two earlier designs both failed, and this is the third:
                #
                #   1. Withhold every extreme, bounded by a skip cap. That
                #      froze the widget on a stale view and, when the cap
                #      tripped, fired the black frame anyway -- stale picture,
                #      stab of black, back. It MANUFACTURED the artifact.
                #   2. Publish everything. Honest per frame, and still wrong,
                #      because of ALIASING: a Gen-3 battle flashes the screen
                #      white or black for one to three frames at 60 Hz (17-50
                #      ms), and sampling at ~10 Hz catches one of those and
                #      then HOLDS it for a full ~100 ms sample. An
                #      imperceptible hit-flash becomes a full-panel strobe.
                #      Measured on this run, mid-battle, 0.4 s apart:
                #      mean 142.7 -> 232.5 (white) -> 141.0 -> 4.3 (black)
                #      -> 143.8, against a normal band of 134-145.
                #
                # The discriminator is DURATION, not brightness. A real fade
                # lasts many samples; an animation flash is gone before the
                # next one. So a single extreme is dropped and the previous
                # frame stays up, and the moment a SECOND consecutive extreme
                # arrives it publishes -- a genuine fade to black shows, one
                # sample late.
                #
                # Cannot freeze: the hold is BOUNDED IN TIME, so a genuinely
                # long black screen always publishes once the window passes.
                #
                # One sample was the first attempt at that bound and it was
                # too short. A fly animation or a warp fade spans several
                # samples, so the second consecutive extreme published and the
                # panel strobed anyway -- measured after the fix, mid-travel:
                # 11 of 81 published frames outside the 40-210 band, some at
                # brightness 0.0, with flashes_dropped=17318 of 87089 frames
                # (20% of all samples were extremes). Suppressing only ISOLATED
                # extremes does nothing for a run that is constantly warping.
                extreme = self._is_transition(shot)
                publish_frame = True
                if extreme:
                    self.fades_seen += 1
                    # The GAME's clock, not the wall's: a wall-clock window
                    # leaked because the emulator does not run at realtime.
                    frame_no = getattr(emu, "frame", 0) or 0
                    if self._extreme_since is None:
                        self._extreme_since = frame_no
                    if frame_no - self._extreme_since < self.FADE_HOLD_FRAMES:
                        publish_frame = False
                        self.flashes_dropped += 1
                else:
                    self._extreme_since = None
                if publish_frame:
                    buf = io.BytesIO()
                    shot.save(buf, format="PNG")
                    _atomic_write(self.png_path, buf.getvalue())
                    self.frames_published += 1
            except Exception as err:
                self._fail(f"frame: {type(err).__name__}: {err}")
                ok = False
        if state:
            self._next_state = now + 1.0 / self.state_hz
            try:
                snap = self.snapshot(driver, live=live)
                _atomic_write(
                    self.json_path,
                    json.dumps(snap, ensure_ascii=False).encode("utf-8"),
                )
                self.states_published += 1
            except Exception as err:
                self._fail(f"state: {type(err).__name__}: {err}")
                ok = False
        if ok:
            self.last_publish_reason = self._logged_reason = None
        return ok

    def _fail(self, reason: str) -> None:
        """Count and explain a publish failure.  Logged only when the cause
        changes: a full disk fails every frame, and 12 identical warnings a
        second would bury the run's own narration."""
        self.errors += 1
        self.last_publish_reason = reason
        if reason != self._logged_reason:
            self._logged_reason = reason
            log.warning("live feed %s: %s", self.name, reason)

    def snapshot(self, driver=None, live: bool = True) -> dict:
        """The compact state the widget renders.

        Deliberately not ``GameState.snapshot()``: that carries the whole bag
        and every move's PP, which is the right payload for a decider and the
        wrong one for a 1 Hz poll from the desktop bar.  A boot or intro screen
        has no game yet, so a failed read is REPORTED in ``error`` rather than
        faked into plausible-looking zeroes.
        """
        driver = driver or self.driver
        if driver is None:
            raise RuntimeError("snapshot() needs a driver; attach the feed first")
        out = {
            "name": self.name,
            "t": time.time(),
            "live": live,
            "fps": self.fps,
            "frame": driver.emu.frame,
            "flashes_dropped": self.flashes_dropped,
            "frames_published": self.frames_published,
            "errors": self.errors,
        }
        gs = driver.state
        try:
            loc = gs.location()
            badges = gs.badges()
            in_battle = gs.in_battle()
            out.update(
                {
                    "map": loc.map_name,
                    "pos": {"x": loc.x, "y": loc.y, "facing": gs.facing()},
                    "player": gs.player_name(),
                    "money": gs.money(),
                    "play_time": gs.play_time(),
                    "badges": len(badges),
                    "badge_names": badges,
                    "in_battle": in_battle,
                    "message": gs.message(),
                    "party": [
                        {
                            "nickname": m.nickname,
                            "species": "EGG" if m.is_egg else driver.names.species(m.species),
                            "level": m.level,
                            "hp": m.hp,
                            "max_hp": m.max_hp,
                            "status": m.status_name,
                            "egg": m.is_egg,
                            "fainted": m.fainted,
                        }
                        for m in gs.party()
                    ],
                    "status": gs.status_line(),
                }
            )
            # Which of Gen 1-3 is running. The widget shows this in its header
            # so it is never ambiguous, and it drives the framebuffer aspect
            # (a Game Boy frame is 160x144, a GBA one 240x160).
            spec = getattr(driver, "spec", None)
            if spec is not None:
                out["game"] = {
                    "id": spec.id,
                    "name": spec.name,
                    "short_name": spec.short_name,
                    "generation": spec.generation,
                    "region": spec.region,
                    "core": spec.core,
                }
            out.update(self._extras(driver, in_battle))
        except Exception as err:
            # The widget needs to distinguish "no game running" from "the feed
            # is lying to you", so say which read failed.
            out["error"] = f"{type(err).__name__}: {err}"
        # Loop-supplied extras last: they are computed from the event log on
        # disk rather than from the cartridge, so nothing above can produce
        # them. Copied, not aliased -- a published dict must not keep mutating
        # after it is written.
        for key, value in (self.extra or {}).items():
            out[key] = value
        return out

    def _extras_fingerprint(self, driver, in_battle) -> tuple:
        """What the rich blocks actually depend on, read cheaply.

        The objective, the dex and the team change when the party, the badges
        or the box change -- not when the player takes a step. Recomputing
        them per publish cost 311 ms a snapshot (253 ms of it the ladder,
        which rebuilds the living-dex evolution chains), and at 4 Hz that is
        more than a second of work per second of wall clock: the feed starved
        the emulator to 12 fps from 1028, and an unattended run managed one
        battle in five minutes. Frames stay live; only the slow blocks are
        sampled.
        """
        gs = driver.state
        party = ()
        try:
            party = tuple(
                (m.species, m.level, m.is_egg) for m in gs.party()
            )
        except Exception:  # noqa: BLE001 - no party yet on a boot screen
            pass
        try:
            badges = gs.badges()
        except Exception:  # noqa: BLE001
            badges = None
        return (party, badges, bool(in_battle))

    def _extras(self, driver, in_battle) -> dict:
        """The optional, richer status blocks.

        Every one is best-effort and independently guarded: the objective
        engine, the dex dataset and the team analyser are all things that can
        be absent or can throw, and none of them is worth killing a run or a
        frame over. A block that cannot be computed is simply not published,
        which is the contract the widget was built against.

        Cached on ``_extras_fingerprint`` and re-derived at most every
        ``extras_every`` seconds, so a long walk does not pay for a dex
        recount on every frame.
        """
        now = time.monotonic()
        key = self._extras_fingerprint(driver, in_battle)
        if (
            self._extras_cache is not None
            and key == self._extras_key
            and now < self._extras_due
        ):
            return self._extras_cache

        out = self._build_extras(driver, in_battle)
        self._extras_cache = out
        self._extras_key = key
        self._extras_due = now + self.extras_every
        return out

    def _build_extras(self, driver, in_battle) -> dict:
        """Actually derive the rich blocks. See ``_extras`` for the caching."""
        out = {}

        try:
            # The stage ladder IS the objective once the game is complete, so
            # prefer it and fall back to the simple engine when the dex data
            # (which the ladder needs) is unavailable.
            from pokeagent.stages import Ladder

            ladder = Ladder(driver)
            published = ladder.as_dict()
            current = published["current"]
            out["objective"] = {
                "name": f"{current['rank']}. {current['name']}",
                "detail": current["detail"],
                "percent": current["percent"],
                "next_step": current["next_step"],
            }
            # Through as_dict() so each row carries `current`; building this
            # list from all_stages() dropped that flag, and the widget then
            # had no way to tell which rung was being worked on.
            out["stages"] = published["stages"]
        except Exception as err:  # noqa: BLE001
            self._note_extra_failure("stages", err)
            try:
                from pokeagent.objective import ObjectiveEngine

                out["objective"] = ObjectiveEngine(driver).current().as_dict()
            except Exception as err2:  # noqa: BLE001
                self._note_extra_failure("objective", err2)

        try:
            # What to go and get next, WITH the method -- "use LEAF STONE on
            # GLOOM" rather than a name the reader has to look up.
            from pokeagent.acquire import Acquisitions
            from pokeagent.dex import DexTarget

            target = DexTarget(
                driver.emu, driver.names, driver.consts, driver.nav,
                spec=driver.spec,
            )
            owned = {
                driver.names.species(m.species)
                for m in driver.state.party() if not m.is_egg
            }
            acq = Acquisitions(driver.emu, driver.names, target)
            todo = []
            for entry in target.achievable:
                if entry.rom_name in owned:
                    continue
                answer = acq.for_entry(entry)
                best = answer.best()
                if best is None:
                    continue
                todo.append({"species": entry.name, "how": best.detail,
                             "kind": best.kind})
                if len(todo) >= 5:
                    break
            if todo:
                out["dex_next"] = todo
        except Exception as err:  # noqa: BLE001
            self._note_extra_failure("dex_next", err)

        try:
            from pokeagent import dex as dexmod

            # The LIVING dex is the real target: species held right now,
            # not species ever registered.
            from pokeagent.living import LivingDex

            target = dexmod.DexTarget(
                driver.emu, driver.names, driver.consts, driver.nav,
                spec=driver.spec,
            )
            living = LivingDex(target).progress(driver.state)
            registered = target.progress(driver.state)
            out["dex"] = {
                "caught": living.held,
                "seen": registered.get("seen"),
                "achievable": living.target,
                "percent": round(living.percent, 1),
                "lines_complete": living.lines_complete,
                "lines_total": living.lines_total,
                "storage_used": living.storage_used,
                "storage_slots": living.storage_slots,
            }
        except Exception as err:  # noqa: BLE001
            self._note_extra_failure("dex", err)

        try:
            from pokeagent import team as teammod

            report = teammod.report(driver)
            out["team"] = {
                "min_level": report["parity"]["min"],
                "max_level": report["parity"]["max"],
                "spread": report["parity"]["spread"],
                "coverage_gaps": report["coverage"]["gaps"],
            }
        except Exception as err:  # noqa: BLE001
            self._note_extra_failure("team", err)

        if in_battle:
            try:
                battle = driver.state.battle()
                if battle.mons and len(battle.mons) > 1:
                    foe = battle.mons[1]
                    out["enemy"] = {
                        "species": foe["name"],
                        "level": foe["level"],
                        "hp": foe["hp"],
                        "max_hp": foe["max_hp"],
                    }
            except Exception as err:  # noqa: BLE001
                self._note_extra_failure("enemy", err)

        # Session counters first, then the cartridge's own tallies on top:
        # where both have an opinion the GAME wins. The harness counted its
        # own steps and published 0 across a run that had walked fifty
        # thousand, because only the grinding path incremented it. The game
        # has kept an honest count the whole time, and it survives restarts,
        # so a resumed run stops reporting a session as if it were the run.
        out["counters"] = dict(self.counters, frames=driver.emu.frame)
        try:
            from pokeagent.state import game_stats

            out["counters"].update(game_stats(driver.emu))
        except Exception as err:  # noqa: BLE001
            self._note_extra_failure("game_stats", err)
        return out

    def _note_extra_failure(self, block, err):
        """Log a failing status block ONCE per distinct reason.

        A block that is simply unavailable (no dex data, pre-game state) would
        otherwise emit a warning on every published frame.
        """
        reason = f"{block}: {type(err).__name__}: {err}"
        if self._extra_reasons.get(block) != reason:
            self._extra_reasons[block] = reason
            log.debug("live: %s status unavailable (%s)", block, reason)

    def count(self, key, n=1):
        """Bump a session counter the widget displays."""
        self.counters[key] = self.counters.get(key, 0) + n

    # ---- narration -------------------------------------------------------

    def _count_notes(self) -> int:
        """Existing line count, so note indices stay monotonic across runs
        that share a feed name."""
        try:
            with open(self.log_path, "rb") as f:
                return sum(1 for _ in f)
        except OSError:
            return 0

    def note(self, msg, src: str = "agent", level: str = "INFO") -> None:
        """Append one narration line.  Called by the log bridge for every
        harness INFO record, and directly for anything the caller wants the
        viewer to read."""
        msg = str(msg).strip().replace("\n", " ")
        if not msg:
            return
        if len(msg) > NOTE_MAX:
            msg = msg[: NOTE_MAX - 1] + "\u2026"
        row = {
            "i": self._notes,
            "frame": self.driver.emu.frame if self.driver else None,
            "t": time.strftime("%H:%M:%S"),
            "msg": msg,
            "src": src,
            "level": level,
        }
        self._notes += 1
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.notes_written += 1
        except OSError as err:
            # No log call here: this runs inside a logging handler, and logging
            # about a failed log is how you get infinite recursion.
            self.errors += 1
            self.last_publish_reason = f"note: {type(err).__name__}: {err}"
            return
        if self._notes % NOTE_KEEP == 0:
            self._rotate_notes()

    def _rotate_notes(self) -> None:
        """Drop the oldest half of the narration log.  Indices are preserved,
        so a reader that follows byte offsets re-syncs on the next line."""
        try:
            rows = self.log_path.read_text(encoding="utf-8").splitlines()
            _atomic_write(
                self.log_path, ("\n".join(rows[-NOTE_KEEP // 2 :]) + "\n").encode("utf-8")
            )
        except OSError as err:
            self.errors += 1
            self.last_publish_reason = f"rotate: {type(err).__name__}: {err}"
