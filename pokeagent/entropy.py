"""Reseeding the game's RNG from host entropy.

The request: induce real randomness from what the computer is doing -- mouse
movement, CPU load, time of day -- and write it into wherever the seed lives.

The target is exact. `gRngValue` is a u32 in IWRAM (this build: 0x03004818, and
resolved through the symbol table rather than hardcoded), advanced by a plain
LCG (`pret/src/random.c:9-13`)::

    gRngValue = 1103515245 * gRngValue + 24691
    return gRngValue >> 16

so overwriting it is the whole job -- there is no state elsewhere to keep
consistent.

**The tension, stated up front.** This harness's retry model rests on
savestates being reproducible: same state plus same inputs is byte-identical,
RNG included, which is what makes forking a timeline a real search primitive
instead of a gamble. Injecting external entropy deliberately destroys that. So
the design is not "sprinkle randomness everywhere":

1. **Opt-in**, off by default.
2. **Every injection is journalled** with the frame it was written at and the
   value written, so a run remains *replayable* by replaying its injections
   even though it is no longer deterministic from the savestate alone. Losing
   reproducibility is acceptable; losing auditability is not.
3. **Suppressed while a savestate search is running** (`goto` escalation,
   `explore_bfs`), because those algorithms need determinism to terminate.
4. Written at a **safe boundary** -- on the overworld, between frames, never
   mid-battle-calculation, where the engine may already have latched a value.

And the payoff: this is what makes the shiny stage possible at all. Gen-3
shininess is decided when an individual is generated, from the personality and
the trainer/secret IDs. Reload a savestate, re-fight the same encounter with
the same inputs, and you get the same non-shiny Pokémon forever. Reseeding
between attempts is what turns "retry" into an actual independent trial.

Entropy sources, all cheap and all local:

* ``os.urandom`` -- the kernel's own pool, the backbone of the mix
* ``/proc/interrupts`` -- keyboard and mouse interrupt counters, so physically
  using the machine really does feed the game
* ``/proc/stat`` -- CPU jiffies across all cores
* ``/proc/loadavg``, ``/proc/meminfo`` -- load and memory pressure
* ``time.time_ns()`` and ``time.perf_counter_ns()`` -- wall clock and a
  monotonic counter, which covers "time of day"
"""

import hashlib
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("pokeagent.entropy")

#: pret/src/random.c:11
LCG_MULT, LCG_ADD = 1103515245, 24691
#: The seed symbol. Resolved by name; the address is not written down.
RNG_SYMBOL = "gRngValue"

#: Files whose contents change as the machine is used. Missing files are
#: skipped rather than fatal -- this must work in a container too.
PROC_SOURCES = (
    "/proc/interrupts",   # keyboard/mouse/USB interrupt counts
    "/proc/stat",         # per-core CPU jiffies
    "/proc/loadavg",
    "/proc/meminfo",
    "/proc/uptime",
)


@dataclass(slots=True)
class Injection:
    frame: int
    value: int
    sources: tuple
    reason: str

    def as_dict(self):
        return {"frame": self.frame, "value": self.value,
                "sources": list(self.sources), "reason": self.reason}


def _read(path, limit=4096):
    try:
        with open(path, "rb") as fh:
            return fh.read(limit)
    except OSError:
        return b""


def collect(extra=b"") -> tuple[bytes, tuple]:
    """Gather host entropy. Returns (material, source labels used)."""
    parts, used = [], []
    parts.append(os.urandom(32))
    used.append("urandom")
    for path in PROC_SOURCES:
        blob = _read(path)
        if blob:
            parts.append(blob)
            used.append(Path(path).name)
    parts.append(str(time.time_ns()).encode())
    parts.append(str(time.perf_counter_ns()).encode())
    used += ["time_ns", "perf_counter_ns"]
    if extra:
        parts.append(extra)
        used.append("caller")
    return b"".join(parts), tuple(used)


def seed_value(extra=b"") -> tuple[int, tuple]:
    """A 32-bit value from mixed host entropy.

    Hashed rather than XOR-folded so a slow-moving source (uptime) cannot
    dominate the low bits that the LCG's output shift actually surfaces.
    """
    material, used = collect(extra)
    digest = hashlib.blake2b(material, digest_size=8).digest()
    return int.from_bytes(digest[:4], "little") & 0xFFFFFFFF, used


class Entropy:
    """Injects host entropy into the game's RNG, audibly and on demand."""

    def __init__(self, driver, enabled=False, every_frames=0, journal=None):
        self.d = driver
        self.enabled = enabled
        #: 0 disables periodic reseeding; a positive value reseeds on a timer.
        self.every_frames = every_frames
        self.journal = Path(journal) if journal else None
        self.log: list = []
        self.injections = 0
        self.suppressed = 0
        self._last_frame = driver.emu.frame
        #: Set while a savestate search is running, which needs determinism.
        self.hold = False

    # ---- reading the RNG ---------------------------------------------------

    def current(self) -> int:
        return self.d.emu.u32(RNG_SYMBOL)

    def peek_next(self) -> int:
        """What the engine's next `Random()` would return, without advancing.

        Useful for auditing: it proves the module is looking at the real seed
        and understands the formula, rather than writing into a plausible
        address.
        """
        nxt = (LCG_MULT * self.current() + LCG_ADD) & 0xFFFFFFFF
        return nxt >> 16

    # ---- injecting --------------------------------------------------------

    def safe_moment(self) -> tuple[bool, str]:
        """Is now a safe time to overwrite the seed?

        Refusing in battle is not superstition: the engine latches damage and
        accuracy rolls across script commands, so replacing the seed
        mid-calculation can desync what the player sees from what the engine
        decided.
        """
        if self.hold:
            return False, "held: a savestate search needs determinism"
        try:
            if self.d.in_battle():
                return False, "in battle: rolls are latched mid-script"
            if self.d.scene_active():
                return False, "a scene owns input"
        except Exception as exc:  # noqa: BLE001
            return False, f"state unreadable: {exc}"
        return True, "overworld, idle"

    def inject(self, reason="manual", force=False) -> Injection | None:
        """Reseed now. Returns the Injection, or None when refused."""
        if not self.enabled and not force:
            return None
        ok, why = self.safe_moment()
        if not ok and not force:
            self.suppressed += 1
            log.debug("entropy: not injecting (%s)", why)
            return None

        value, sources = seed_value(extra=str(self.d.emu.frame).encode())
        self.d.emu.write(RNG_SYMBOL, value.to_bytes(4, "little"))
        record = Injection(frame=self.d.emu.frame, value=value,
                           sources=sources, reason=reason)
        self.log.append(record)
        self.injections += 1
        self._last_frame = self.d.emu.frame
        # Loud on purpose: this is the one thing in the harness that makes a
        # run irreproducible, so it must never happen quietly.
        log.info("entropy: reseeded gRngValue to %#010x at frame %d (%s; %s)",
                 value, record.frame, reason, ",".join(sources))
        self._journal(record)
        return record

    def tick(self, reason="periodic"):
        """Call after actions; reseeds on the frame timer when enabled."""
        if not self.enabled or self.every_frames <= 0:
            return None
        if self.d.emu.frame - self._last_frame < self.every_frames:
            return None
        return self.inject(reason=reason)

    def reseed_for_retry(self, attempt=0) -> Injection | None:
        """Reseed before re-attempting a forked encounter.

        This is the shiny-hunt primitive: without it, a reload plus the same
        inputs reproduces the same individual, so the retry is not a trial at
        all. Forced, because the caller has just reloaded and knows this is
        the right moment.
        """
        return self.inject(reason=f"retry #{attempt}", force=True)

    # ---- auditability ------------------------------------------------------

    def _journal(self, record):
        if self.journal is None:
            return
        try:
            self.journal.parent.mkdir(parents=True, exist_ok=True)
            with open(self.journal, "a", encoding="utf-8") as fh:
                import json

                fh.write(json.dumps(record.as_dict()) + "\n")
        except OSError as exc:
            log.warning("could not journal an injection: %s", exc)

    def stats(self):
        return {
            "enabled": self.enabled,
            "injections": self.injections,
            "suppressed": self.suppressed,
            "every_frames": self.every_frames,
            "last_value": self.log[-1].value if self.log else None,
        }

    def __enter__(self):
        """`with entropy.held():`-style suppression for deterministic work."""
        self.hold = True
        return self

    def __exit__(self, *exc):
        self.hold = False
        return False
