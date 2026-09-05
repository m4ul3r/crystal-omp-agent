"""Notice when the run has stopped getting anywhere, and say what is frozen.

Every long stall this project has had looked the same from the outside: the
loop kept running, the log kept scrolling, nothing changed. 1171 warp attempts
at Slateport's museum. 615 at the cable car. Hundreds of turns of a Lottad
using STRENGTH against a Grimer while both HP bars sat still. In each case the
harness was busy and the run was dead, and a person had to notice.

The detector is deliberately dumb: take a SIGNATURE of everything that counts
as progress, and if it has not changed in a while, escalate. Dumb is the point
-- a stall predicate that needs to understand what the run is doing will only
recognise the stalls somebody already thought of.

What escalation means is the caller's business. This names the problem and
proposes the next lever; it never presses one itself, because a watchdog that
takes actions is a second agent nobody is auditing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
import faulthandler
import json
import os
import signal
import threading
import time
from pathlib import Path
from . import paths

log = logging.getLogger(__name__)

#: Cycles at the same signature before each lever is proposed. Sized against
#: real stalls: a legitimate slow patch (a long walk, a training block) moves
#: SOMETHING -- position, HP, exp -- within a handful of cycles, so these are
#: generous rather than twitchy.
NUDGE_AFTER = 12
RETARGET_AFTER = 30
BLOCKED_AFTER = 60


@dataclass(frozen=True)
class Verdict:
    """What the watchdog thinks, and what it suggests doing about it."""

    stuck: bool
    cycles: int
    lever: str | None
    why: str

    def __bool__(self) -> bool:  # `if verdict:` reads as "are we stuck"
        return self.stuck


@dataclass
class Watchdog:
    """Track a progress signature and escalate while it stays put.

    `signature` is whatever the caller decides counts as progress. Anything
    that changes on a healthy run and freezes on a stuck one belongs in it;
    anything that changes on its OWN (a frame counter, wall-clock, an RNG
    seed) must not, or the watchdog will never fire.
    """

    nudge_after: int = NUDGE_AFTER
    retarget_after: int = RETARGET_AFTER
    blocked_after: int = BLOCKED_AFTER
    last: tuple | None = None
    cycles: int = 0
    fired: set[str] = field(default_factory=set)

    def reset(self) -> None:
        """Progress happened. Forget everything, including what we proposed."""
        self.cycles = 0
        self.fired.clear()

    def observe(self, signature) -> Verdict:
        """Feed one cycle's signature and get a verdict.

        A lever is proposed at most ONCE per stall. Re-proposing the same
        escalation every cycle is how a stuck run produces a thousand
        identical log lines -- the exact noise this exists to replace.
        """
        sig = tuple(signature)
        if sig != self.last:
            self.last = sig
            self.reset()
            return Verdict(False, 0, None, "moving")

        self.cycles += 1
        for threshold, lever in (
            (self.blocked_after, "blocked"),
            (self.retarget_after, "retarget"),
            (self.nudge_after, "nudge"),
        ):
            if self.cycles >= threshold and lever not in self.fired:
                self.fired.add(lever)
                why = (
                    f"{self.cycles} cycles with no change to "
                    f"{self._describe(sig)}"
                )
                log.warning("[watchdog] %s -> %s", why, lever)
                return Verdict(True, self.cycles, lever, why)

        return Verdict(
            self.cycles >= self.nudge_after, self.cycles, None,
            f"{self.cycles} cycles unchanged",
        )

    @staticmethod
    def _describe(sig) -> str:
        """The signature as something a person can read in a log line."""
        parts = []
        for item in sig:
            if isinstance(item, (list, tuple)):
                item = "/".join(str(x) for x in item)
            parts.append(str(item))
        return " | ".join(parts) or "(empty)"


def progress_signature(driver, objective=None) -> tuple:
    """Everything that counts as the run getting somewhere.

    Position and map catch a walker pinned against geometry. Badges, money and
    the dex catch a run that is moving but achieving nothing. Party levels and
    HP catch a training loop that is fighting without winning -- and the
    Lottad-versus-Grimer stall, where the only thing that changed was a turn
    counter this deliberately does not include.

    NOTHING here may advance on its own. A frame count or a clock would make
    the signature always-fresh and the watchdog permanently blind.

    Raises whatever the driver raises when POSITION cannot be read; the caller
    must treat that as "no observation this cycle", never as a change.
    """
    try:
        party = tuple(
            (m.species, m.level, m.hp) for m in (driver.state.party() or ())
        )
    except Exception:  # noqa: BLE001 - a watchdog must never end the run
        party = ()
    try:
        badges = len(driver.state.badges())
    except Exception:  # noqa: BLE001
        badges = -1
    # Position is the CORE signal and the one field with no safe default.
    # Substituting a sentinel turns an unreadable frame into an apparent move
    # to "?", which resets the stall clock -- so a driver that cannot be read
    # would look like a driver making progress. Let it raise: the caller skips
    # the cycle, and no observation is strictly better than a false one.
    pos = driver.pos()
    where = (driver.map_name(), pos[0], pos[1])
    try:
        money = driver.state.money()
    except Exception:  # noqa: BLE001
        money = -1
    goal = getattr(objective, "detail", None) or getattr(objective, "name", "")
    return (where, badges, money, party, str(goal))


# ---------------------------------------------------------------------------
# StallWatch: notice when the PICTURE stops changing.
#
# The Watchdog above judges a live driver from inside the decision loop, which
# means it only gets a vote when that loop comes back. The failure this adds
# cover for is the loop that never returns: `goto` retrying a step the engine
# refuses, emulator ticking, feed publishing, log holding its last line from
# before the walk began. Nothing above can see it, and the only outward
# symptom is that the screen stops changing -- reported twice now by a human
# watching the widget, both times after many wasted minutes.
#
# Measured live rather than assumed: Victory Road B1F (9,9) facing D, frames
# 138,164,696 -> 138,183,657 across twelve seconds, PNG byte-identical.
#
# It reads the published feed instead of the driver on purpose. The mGBA core
# is not thread-safe and the main thread is inside it constantly, so sampling
# `driver.pos()` from a watchdog thread races the core -- corrupting a run to
# diagnose a hang. `live/<feed>.json` is written by the driving thread itself
# and is the exact artefact the human is looking at.
# ---------------------------------------------------------------------------
STALL_NONE = ""
STALL_PINNED = "pinned"
STALL_WEDGED = "wedged"


def install_sigusr1() -> bool:
    """Make SIGUSR1 dump every thread's stack.

    Cheap insurance, and the only stack this machine will give up: yama's
    ``ptrace_scope`` refuses a non-parent ``py-spy dump``, and there is no
    passwordless sudo, so an external sampler is not available at all.
    """
    if not hasattr(signal, "SIGUSR1"):
        return False
    try:
        faulthandler.register(signal.SIGUSR1, all_threads=True, chain=False)
        return True
    except Exception:  # noqa: BLE001 - a debug aid must never break a run
        return False


class StallWatch:
    """Watch a published feed and report when the picture stops changing.

    Poll-only and side-effect free: it never touches the driver, so it is safe
    to run alongside a thread that owns the emulator core.
    """

    def __init__(self, feed_name="default", idle_s=150.0, sample_s=10.0,
                 log=None, feed_dir=None):
        self.path = Path(feed_dir or paths.LIVE_DIR) / f"{feed_name}.json"
        self.idle_s = float(idle_s)
        self.sample_s = float(sample_s)
        self.log = log
        self.kind = STALL_NONE
        self.detail = ""
        self._stop = threading.Event()
        self._thread = None
        # Last observation and when the picture last actually CHANGED.
        self._where = None
        self._frame = None
        self._changed_at = time.time()
        self._dumped = False

    # ---- lifecycle ---------------------------------------------------

    def start(self) -> "StallWatch":
        install_sigusr1()
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run, name="stallwatch", daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def clear(self) -> None:
        """Forget the current stall, e.g. after abandoning a map."""
        self.kind = STALL_NONE
        self.detail = ""
        self._changed_at = time.time()
        self._dumped = False

    # ---- the observation --------------------------------------------

    @property
    def stalled(self) -> bool:
        return self.kind != STALL_NONE

    def _read(self):
        try:
            d = json.loads(self.path.read_text())
        except Exception:  # noqa: BLE001 - a missing/partial feed is not a stall
            return None
        pos = d.get("pos") or {}
        where = (d.get("map"), pos.get("x"), pos.get("y"))
        if where[0] is None:
            return None
        return where, d.get("frame")

    def _run(self) -> None:
        while not self._stop.wait(self.sample_s):
            obs = self._read()
            if obs is None:
                continue
            where, frame = obs
            moved = where != self._where
            ticked = frame != self._frame
            self._where, self._frame = where, frame
            if moved:
                # Real movement is the only thing that resets the clock. A
                # frame counter climbing on its own is exactly the failure.
                self._changed_at = time.time()
                if self.stalled:
                    self.clear()
                continue
            idle = time.time() - self._changed_at
            if idle < self.idle_s:
                continue
            self._flag(STALL_PINNED if ticked else STALL_WEDGED, where, idle,
                       frame)

    def _flag(self, kind, where, idle, frame) -> None:
        self.kind = kind
        m, x, y = where
        self.detail = (f"{kind} at {m} ({x},{y}) for {idle:.0f}s "
                       f"(frame {frame})")
        if self._dumped:
            return
        self._dumped = True
        if self.log:
            if kind == STALL_PINNED:
                self.log.info(
                    "[stall] %s -- frames are advancing but the player is "
                    "not moving, so the loop is pressing something that "
                    "cannot work; abandoning this map", self.detail)
            else:
                self.log.info(
                    "[stall] %s -- the frame counter is not advancing "
                    "either, so nothing is ticking the core and the process "
                    "is blocked in Python; stack follows", self.detail)
        try:
            faulthandler.dump_traceback(all_threads=True)
        except Exception:  # noqa: BLE001
            pass
        # A wedged process cannot be rescued from a pacing loop, and leaving
        # it burning a session is strictly worse than dying loudly.
        if kind == STALL_WEDGED and os.environ.get("CRYSTAL_STALL_ABORT"):
            os._exit(75)  # EX_TEMPFAIL: restart=on-failure will relaunch
