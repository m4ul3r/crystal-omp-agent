#!/usr/bin/env python
"""The idle run: power-on to as far as the chain gets, at real hardware speed.

This is the project's from-zero demo. Not a tool-assisted speedrun and not a
savestate tour: the cartridge is reset, the title screen is sat through, a new
game is named, and the existing story chain then plays itself while the live
feed publishes every frame to the widget.

    scripts/idle_run.py --minutes 45 --fps hardware --feed idle

Three things make it an *idle game* rather than a harness demo:

* **Pacing.** ``Sapphire._pace`` (pokeagent/emu.py:303) throttles every frame
  the harness advances, because EVERY frame goes through ``tick``. Nothing had
  to be de-TASsed: ``advance_scene(40_000)`` was always a frame BUDGET that
  returns when the scene settles, so the game was already being stepped one
  frame at a time -- headless mGBA was simply running them at ~1300 fps. The
  rate is set through ``SAPPHIRE_FPS`` rather than a constructor argument
  because the Driver builds its own emulator through the adapter
  (pokeagent/adapters/gen3.py:61), and the play loop below builds a second
  one; an environment variable is the one lever both of them see.

* **Reuse.** Every leg here already existed and is already tested. This file
  is a chain, not an implementation: ``newgame.drive_until`` for the attract
  sequence, ``to_starter.drive_intro`` for truck -> clock -> rival -> starter,
  and ``play.Session`` -- the autonomous loop, objective engine and all -- for
  everything after that. Rewriting any of them would fork behaviour that has
  already been debugged against this ROM.

* **Honesty about where it stops.** An unattended run that dies quietly is
  worse than one that stops loudly, so each leg is checkpointed BEFORE the
  next risky one starts, the ``Chronicle`` observer timestamps every map the
  run enters, and the final report prints the map, the position, the frame and
  the reason. A leg that fails is reported and the run continues to the next
  one it can still attempt.
"""

import argparse
import logging
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# The legs being chained are scripts, not library modules; importing them
# needs their own directory on the path the same way `share_grind` is imported
# by the grind scripts.
sys.path.insert(0, str(ROOT / "scripts"))

from pokeagent import paths  # noqa: E402
from pokeagent.live import LiveFeed  # noqa: E402
from pokeagent.naming import NamingScreen  # noqa: E402
from pokeagent.trek import Driver  # noqa: E402

log = logging.getLogger("idle")


class Chronicle:
    """Tick observer that wraps the live feed and timestamps the run.

    The emulator allows exactly one observer (pokeagent/emu.py:286), and that
    slot belongs to the feed -- so this borrows it and forwards. What it buys
    is the thing an unattended run cannot otherwise produce: the wall-clock
    cost of each milestone. Sampling the location twice a second is enough to
    catch every map transition, and every read is guarded because the observer
    runs inside the tick loop, where an exception would end the run rather
    than lose a log line.
    """

    def __init__(self, feed, driver, sample=0.5, origin=None):
        self.feed = feed
        self.d = driver
        self.sample = sample
        # The tick loop reads this to size its slices.
        self.slice_frames = getattr(feed, "slice_frames", 8)
        self.marks = []
        self.seen = set()
        #: The whole RUN's clock, not this emulator's -- the play loop runs on
        #: a second machine and its milestones belong on the same timeline.
        self.started = origin if origin is not None else time.monotonic()
        #: Where this emulator's frame counter stood when the leg began. A
        #: savestate restores the counter with everything else, so an absolute
        #: frame number says nothing about how much THIS leg advanced.
        self.frame0 = getattr(driver.emu, "frame", 0)
        self._next = 0.0
        self._errors = 0

    @property
    def advanced(self):
        return getattr(self.d.emu, "frame", self.frame0) - self.frame0

    # ---- observer protocol ---------------------------------------------

    def after_slice(self, emu):
        try:
            self.feed.after_slice(emu)
        except Exception as exc:  # noqa: BLE001 - publishing must not end a run
            self._errors += 1
            if self._errors < 4:
                log.warning("live feed hiccup: %s", exc)
        now = time.monotonic()
        if now < self._next:
            return
        self._next = now + self.sample
        try:
            loc = self.d.state.location()
            name = loc.map_name
        except Exception:  # noqa: BLE001 - garbage during a power-on boot
            return
        if name and name not in self.seen:
            self.seen.add(name)
            self.mark(f"entered {name}")

    # ---- the timeline ---------------------------------------------------

    def where(self):
        """(map, (x, y)) or ('?', None) -- never raises."""
        try:
            loc = self.d.state.location()
            return loc.map_name, (loc.x, loc.y)
        except Exception:  # noqa: BLE001
            return "?", None

    def mark(self, note):
        at = time.monotonic() - self.started
        where, pos = self.where()
        frame = getattr(self.d.emu, "frame", 0)
        self.marks.append((at, frame, where, pos, note))
        log.info("[%s] %s  %s %s @%d", _mmss(at), note, where, pos, frame)
        feed = getattr(self.d, "feed", None)
        if feed is not None:
            try:
                feed.note(f"{note} ({where} {pos})", src="idle")
            except Exception:  # noqa: BLE001
                pass


def _mmss(seconds):
    return f"{int(seconds) // 60:d}:{int(seconds) % 60:02d}"


def _attach(driver, feed_name, origin=None):
    """Publish `driver` to `feed_name`, displacing whatever it came with.

    The Driver auto-attaches a feed named after its state file
    (pokeagent/trek.py:1840), so an explicitly requested name means REPLACING
    that publisher rather than adding a second one -- the emulator refuses two
    observers, and two processes writing one feed is the flicker bug the
    ownership sidecar exists to stop. Same displacement `elite_four.py:431`
    does, plus a Chronicle wrapped round the result.
    """
    existing = getattr(driver, "feed", None)
    if existing is not None and existing.name == feed_name:
        feed = existing
    else:
        if existing is not None:
            existing.detach()
        if getattr(driver.emu, "observer", None) is not None:
            driver.emu.observer = None
        feed = LiveFeed(feed_name).attach(driver)
    chron = Chronicle(feed, driver, origin=origin)
    driver.emu.observer = chron
    return feed, chron


def _detach(driver, feed):
    """Undo `_attach`. The feed's own detach only clears the observer slot when
    it still holds it, and Chronicle is holding it."""
    if getattr(driver.emu, "observer", None) is not None:
        driver.emu.observer = feed
    try:
        feed.detach()
    except Exception as exc:  # noqa: BLE001
        log.warning("could not detach the feed cleanly: %s", exc)
    driver.emu.observer = None


class Leg:
    """One stage of the chain, with its own frame and wall-clock accounting."""

    def __init__(self, name):
        self.name = name
        self.ok = False
        self.reason = "not attempted"
        self.wall = 0.0
        self.frames = 0
        self.where = "?"
        self.pos = None

    @property
    def fps(self):
        return self.frames / self.wall if self.wall > 0 else 0.0

    def line(self):
        mark = "ok  " if self.ok else "STOP"
        return (
            f"{mark} {self.name:<8} {_mmss(self.wall):>6}  "
            f"{self.frames:>8d} frames  {self.fps:6.1f} fps  "
            f"{self.where} {self.pos}  {self.reason}"
        )


# ---- leg 1: power-on to player control ---------------------------------


def boot(d, chron, name="AGENT", girl=False):
    """Title screen -> NEW GAME -> gender -> naming keyboard -> control.

    A straight replay of `scripts/newgame.py`'s sequence against a Driver that
    is already publishing, using that script's own `drive_until` so the screen
    recognition stays in one place: every wait is a predicate over
    ``gTasks``/the main callback out of the symbol table, never a press count,
    because the intro is full of variable-length fades.

    Sapphire does NOT hand over control in a bedroom. The first frame the
    player owns is inside the moving truck (``InsideOfTruck``); the bedroom and
    its clock come a minute later, in the intro leg.
    """
    import newgame

    emu, st = d.emu, d.state
    kb = NamingScreen(emu, st)

    def tasks():
        return st.tasks()

    newgame.drive_until(emu, lambda: st.callback_name() == "MainCB2",
                        "the title screen", tap=".:30", max_frames=8000)
    chron.mark("title screen")

    newgame.drive_until(emu, lambda: any("MainMenu" in t for t in tasks()),
                        "the main menu", tap="START:4 .:20", max_frames=4000)
    chron.mark("main menu")

    # NEW GAME is the top entry on a cartridge with no save file.
    newgame.drive_until(emu, lambda: any("NewGameSpeech" in t for t in tasks()),
                        "Birch's speech", max_frames=6000)
    chron.mark("birch's speech")

    newgame.drive_until(emu, lambda: newgame.GENDER_TASK in tasks(),
                        "the boy/girl menu")
    if girl:
        emu.run_sequence("DOWN:6 .:16")
    emu.run_sequence("A:6 .:40")
    chron.mark(f"gender: {'girl' if girl else 'boy'}")

    newgame.drive_until(emu, kb.is_open, "the naming keyboard")
    typed = kb.type(name)
    chron.mark(f"named {typed!r}")

    newgame.drive_until(emu, lambda: st.callback_name() == "CB2_Overworld",
                        "the overworld")
    emu.tick(240)
    chron.mark("player control")
    return st.player_name()


# ---- leg 2: control to a party ------------------------------------------


def intro(d, chron, starter=None, nickname="EMBER"):
    """Truck -> house -> clock -> rival -> Route 101 -> starter -> first fight.

    `to_starter.drive_intro` exists precisely so the play loop can call it:
    a brand-new game has no party, and every step of the loop assumes one.
    It reports failure rather than raising, so the chain can carry on.
    """
    import to_starter

    ok = to_starter.drive_intro(d, starter=starter, nickname=nickname)
    chron.mark("intro finished" if ok else "intro stopped")
    return ok


# ---- leg 3: the autonomous loop -----------------------------------------


def play(state_path, minutes, feed_name, use_brain, session, origin=None):
    """Hand the checkpoint to the existing play loop and let it run.

    `play.Session` builds its own Driver -- and therefore its own emulator --
    so this is a genuinely separate machine from the boot legs; the feed is
    re-pointed at the requested name and re-wrapped, and the frame accounting
    restarts.
    """
    import play as playmod

    sess = playmod.Session(
        str(state_path), minutes, use_brain=use_brain,
        feed_name=feed_name, session=session,
    )
    feed, chron = _attach(sess.d, feed_name, origin=origin)
    # Session cached whatever feed the Driver came with; point it at ours or
    # its notes and counters land in a file nobody is watching.
    sess.feed = feed
    try:
        sess.run()
    finally:
        _detach(sess.d, feed)
    return sess, chron


def _fork(src, dst):
    """Copy a savestate AND its provenance sidecar.

    The pair is the timeline (pokeagent/emu.py:366): same state plus same
    inputs replays byte-identically, RNG included, and a state whose `.meta`
    was left behind loses the frame count and the ROM hash it was verified
    against.
    """
    shutil.copy2(src, dst)
    meta = Path(str(src) + ".meta")
    if meta.exists():
        shutil.copy2(meta, str(dst) + ".meta")
    log.info("forked %s -> %s", Path(src).name, Path(dst).name)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--minutes", type=float, default=60.0,
                    help="total wall-clock budget for the whole chain; the "
                         "play loop gets whatever the boot legs leave")
    ap.add_argument("--fps", default="hardware",
                    help="'hardware' (59.7275), a number, or 'off' to run flat "
                         "out; sets SAPPHIRE_FPS for every emulator started")
    ap.add_argument("--feed", default="idle", help="live feed name")
    ap.add_argument("--prefix", default="idle",
                    help="checkpoint stem: saves/<prefix>-<step>.state")
    ap.add_argument("--name", default="AGENT")
    ap.add_argument("--girl", action="store_true")
    ap.add_argument("--starter", default=None,
                    help="overrides the GameSpec's starter")
    ap.add_argument("--nickname", default="EMBER")
    ap.add_argument("--brain", action="store_true",
                    help="let the local model break ties; off by default "
                         "because an unreachable ollama stalls a paced run")
    ap.add_argument("--resume", action="store_true",
                    help="skip legs whose checkpoint already exists")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if a.verbose else logging.INFO, format="%(message)s"
    )

    # Set BEFORE any emulator is constructed: the adapter does not take a rate,
    # and the play loop builds its emulator itself.
    os.environ["SAPPHIRE_FPS"] = a.fps

    saves = paths.SAVES_DIR
    saves.mkdir(parents=True, exist_ok=True)
    boot_state = saves / f"{a.prefix}-boot.state"
    starter_state = saves / f"{a.prefix}-starter.state"
    #: The play loop ADOPTS the file it is handed and refreshes it every
    #: autosave (scripts/play.py:382), which is right for a run that must
    #: survive a crash and wrong for a milestone. So it gets a working copy
    #: and the two boot checkpoints stay exactly what their names claim.
    play_state = saves / f"{a.prefix}-play.state"
    final_state = saves / f"{a.prefix}-final.state"
    canonical = saves / "line3.state"
    for path in (boot_state, starter_state, play_state, final_state):
        if canonical.exists() and path.resolve() == canonical.resolve():
            raise SystemExit("refusing to write the canonical timeline")

    deadline = time.monotonic() + a.minutes * 60.0
    legs = []
    started = time.monotonic()

    resume_from = None
    if a.resume:
        resume_from = next(
            (p for p in (play_state, starter_state, boot_state) if p.exists()),
            None,
        )
        if resume_from is None:
            log.info("--resume: no %s-* checkpoint yet; starting from power-on",
                     a.prefix)
        else:
            log.info("resuming from %s", resume_from.name)

    # ---- legs 1 and 2 share one emulator --------------------------------
    chrons = []
    if resume_from is None or resume_from == boot_state:
        d = Driver(str(resume_from) if resume_from else None,
                   fresh=resume_from is None)
        feed, chron = _attach(d, a.feed, origin=started)
        chrons.append(chron)
        log.info("emulator paced at %s fps (SAPPHIRE_FPS=%s)",
                 d.emu.target_fps or "unlimited", a.fps)
        try:
            if resume_from is None:
                leg = Leg("boot")
                t0, f0 = time.monotonic(), d.emu.frame
                try:
                    who = boot(d, chron, name=a.name, girl=a.girl)
                    leg.ok = True
                    leg.reason = f"player {who!r} has control"
                except Exception as exc:  # noqa: BLE001
                    leg.reason = f"{type(exc).__name__}: {exc}"
                    log.warning("boot failed: %s", exc, exc_info=a.verbose)
                leg.wall = time.monotonic() - t0
                leg.frames = d.emu.frame - f0
                leg.where, leg.pos = chron.where()
                legs.append(leg)
                log.info(leg.line())
                if not leg.ok:
                    return _report(legs, started, chrons)
                # Checkpoint BEFORE the risky leg, not after it.
                d.save(boot_state)

            leg = Leg("intro")
            t0, f0 = time.monotonic(), d.emu.frame
            try:
                if intro(d, chron, starter=a.starter, nickname=a.nickname):
                    leg.ok = True
                    leg.reason = "party is not empty"
                else:
                    leg.reason = "drive_intro reported failure"
            except Exception as exc:  # noqa: BLE001
                leg.reason = f"{type(exc).__name__}: {exc}"
                log.warning("intro failed: %s", exc, exc_info=a.verbose)
            leg.wall = time.monotonic() - t0
            leg.frames = d.emu.frame - f0
            leg.where, leg.pos = chron.where()
            legs.append(leg)
            log.info(leg.line())
            if d.state.party():
                d.save(starter_state)
            else:
                leg.reason += " (no party; the loop cannot be handed a game "
                leg.reason += "without one)"
                return _report(legs, started, chrons)
        finally:
            _detach(d, feed)

    # ---- leg 3: a fresh emulator on the working copy ---------------------
    left = (deadline - time.monotonic()) / 60.0
    leg = Leg("play")
    if legs:
        # A leg that never starts still has to say WHERE the run is standing,
        # or the closing line reads "stopped in ? None".
        leg.where, leg.pos = legs[-1].where, legs[-1].pos
    if not play_state.exists():
        if starter_state.exists():
            _fork(starter_state, play_state)
        else:
            leg.reason = "no starter checkpoint to hand the loop"
    if leg.reason == "not attempted" and left <= 0.5:
        leg.reason = f"out of budget ({left * 60:.0f}s left)"
    elif leg.reason == "not attempted":
        log.info("handing %s to the play loop for %.1f min",
                 play_state.name, left)
        t0 = time.monotonic()
        sess = pchron = None
        try:
            sess, pchron = play(play_state, left, a.feed, a.brain,
                                f"{a.prefix}-play", origin=started)
            leg.ok = True
            leg.reason = "budget spent"
        except KeyboardInterrupt:
            leg.reason = "interrupted"
        except Exception as exc:  # noqa: BLE001
            leg.reason = f"{type(exc).__name__}: {exc}"
            log.warning("play loop stopped: %s", exc, exc_info=a.verbose)
        leg.wall = time.monotonic() - t0
        if pchron is not None:
            chrons.append(pchron)
            leg.frames = pchron.advanced
            leg.where, leg.pos = pchron.where()
        if sess is not None:
            try:
                sess.d.save(final_state)
            except Exception as exc:  # noqa: BLE001
                log.warning("could not write %s: %s", final_state.name, exc)
    legs.append(leg)
    log.info(leg.line())

    return _report(legs, started, chrons)


def _report(legs, started, chrons):
    wall = time.monotonic() - started
    frames = sum(leg.frames for leg in legs)
    log.info("")
    log.info("---- idle run ----------------------------------------------")
    for leg in legs:
        log.info(leg.line())
    log.info("total %s  %d frames  %.1f fps average",
             _mmss(wall), frames, frames / wall if wall else 0.0)
    marks = sorted((m for c in (chrons or []) for m in c.marks), key=lambda m: m[0])
    if marks:
        log.info("milestones (wall clock from the start of the run):")
        for at, frame, where, pos, note in marks:
            log.info("  %6s  @%-8d %-28s %s %s", _mmss(at), frame, note, where, pos)
    last = legs[-1] if legs else None
    if last is not None:
        log.info("stopped in %s %s: %s", last.where, last.pos, last.reason)
    return 0 if last is not None and last.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
