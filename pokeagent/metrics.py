"""How long the game actually takes, measured rather than guessed.

The point of this is a sentence like "about 100 hours of idle time to beat the
game, about 300 to fill the Pokedex" -- and for that sentence to be honest it
has to come from runs, not from an estimate someone liked the sound of.

So every milestone is recorded when it happens with three clocks beside it:

* **wall** -- real seconds since the run began. This is the number a reader
  cares about, because it is how long their machine will be busy.
* **frames** -- emulator frames. Machine-independent: a faster box reaches the
  same frame count sooner, so frames are what you compare ACROSS runs.
* **play_time** -- the game's own in-game clock, which is what a human player
  would quote.

Events are append-only JSONL. A run that dies mid-way still leaves everything
it earned, and two runs can be concatenated and compared. The summary is
derived on read, never stored, so it cannot drift from the events.

Projection deliberately reports a RANGE and says what it is extrapolating
from. "300 hours" computed from two badges is a guess wearing a number's
clothes, and this module would rather say so.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)

#: Where run metrics live. Outside saves/ because they are not game state.
METRICS_DIR = Path(os.environ.get(
    "POKEAGENT_METRICS", paths.TOOL_DIR / "metrics"
))

#: Milestones worth timing. Anything else is noise for this purpose.
KINDS = (
    "start", "badge", "story", "species", "dex_percent", "elite_four",
    "champion", "stage",
)


@dataclass
class Event:
    kind: str
    label: str
    wall: float          # seconds since the run started
    frames: int          # emulator frames since the run started
    play_time: str       # the game's own clock
    at: float            # unix time, so runs can be placed in history
    detail: dict = field(default_factory=dict)


class Metrics:
    """Records milestones for one run, and reads back across all of them."""

    def __init__(self, driver, session="run", directory=None):
        self.d = driver
        self.session = session
        self.dir = Path(directory) if directory else METRICS_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{session}.jsonl"
        self.started = time.time()
        self.start_frame = self._frames()
        self._seen: set[tuple[str, str]] = set()
        self._load_seen()

    # ---- writing ---------------------------------------------------------

    def _frames(self) -> int:
        try:
            return int(self.d.emu.frame)
        except Exception:  # noqa: BLE001
            return 0

    def _play_time(self) -> str:
        try:
            return str(self.d.state.play_time())
        except Exception:  # noqa: BLE001
            return "?"

    def _load_seen(self) -> None:
        """Milestones this session already recorded, so a restart does not
        double-count. Keyed on (kind, label) because "badge 1" happens once."""
        if not self.path.exists():
            return
        for row in self.read(self.path):
            self._seen.add((row.get("kind", ""), row.get("label", "")))
        # A resumed run continues the earlier clock rather than restarting it,
        # otherwise "hours to badge 2" would measure only the final process.
        prior = [r for r in self.read(self.path) if r.get("kind") == "start"]
        if prior:
            self.started = time.time() - max(
                (r.get("wall", 0.0) for r in self.read(self.path)), default=0.0
            )
            self.start_frame = self.start_frame - max(
                (r.get("frames", 0) for r in self.read(self.path)), default=0
            )

    def record(self, kind: str, label: str, once=True, **detail) -> bool:
        """Note a milestone. Returns False if it was already recorded."""
        if kind not in KINDS:
            log.debug("metrics: unknown kind %r", kind)
        key = (kind, label)
        if once and key in self._seen:
            return False
        self._seen.add(key)
        event = Event(
            kind=kind, label=label,
            wall=round(time.time() - self.started, 1),
            frames=self._frames() - self.start_frame,
            play_time=self._play_time(),
            at=time.time(),
            detail=detail,
        )
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(event)) + "\n")
        except Exception as err:  # noqa: BLE001 - never lose a run to bookkeeping
            log.warning("metrics: could not write %s/%s: %s", kind, label, err)
            return False
        log.info("[metrics] %s %s at %s (%.0f min, %d frames)",
                 kind, label, event.play_time, event.wall / 60, event.frames)
        return True

    # ---- reading ---------------------------------------------------------

    @staticmethod
    def read(path) -> list[dict]:
        path = Path(path)
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    @classmethod
    def sessions(cls, directory=None) -> dict[str, list[dict]]:
        d = Path(directory) if directory else METRICS_DIR
        if not d.exists():
            return {}
        return {p.stem: cls.read(p) for p in sorted(d.glob("*.jsonl"))}

    def summary(self) -> dict:
        """This run so far, in the terms the README will want to quote."""
        rows = self.read(self.path)
        badges = [r for r in rows if r["kind"] == "badge"]
        species = [r for r in rows if r["kind"] == "species"]
        hours = (time.time() - self.started) / 3600.0
        out = {
            "session": self.session,
            "hours": round(hours, 2),
            "frames": self._frames() - self.start_frame,
            "play_time": self._play_time(),
            "badges": len(badges),
            "species_caught": len(species),
            "events": len(rows),
        }
        if badges:
            out["hours_per_badge"] = round(
                max(b["wall"] for b in badges) / 3600.0 / len(badges), 2
            )
            out["hours_to_last_badge"] = round(
                max(b["wall"] for b in badges) / 3600.0, 2
            )
        return out

    #: Two clocks, both restart-proof, and they measure different things.
    #:
    #: `play_time` is the cartridge's own HH:MM:SS -- what a human player would
    #: quote, and what "beat the game in N hours" conventionally means.
    #: `at` is a unix timestamp, so its deltas are REAL elapsed time, which is
    #: the "hours of idle time" claim: how long the machine actually ran.
    #:
    #: `wall` is neither. It is seconds since THIS session started, so it
    #: resets to zero on every restart -- and a gap measured across one is not
    #: just wrong, it can be negative. The live store already holds badge 1
    #: from one session and badge 2 from the next.
    @staticmethod
    def _play_seconds(row) -> float | None:
        stamp = row.get("play_time")
        if not stamp:
            return None
        try:
            hours, minutes, seconds = (int(p) for p in str(stamp).split(":"))
        except ValueError:
            return None
        return hours * 3600 + minutes * 60 + seconds

    @classmethod
    def _gaps(cls, rows, key) -> list:
        """Positive deltas between consecutive events on one clock.

        Non-positive gaps are dropped rather than clamped: they mean the two
        events came from different runs of the game entirely (a fork, a
        reload), and averaging a zero into the estimate quietly halves it.
        """
        stamps = [v for v in (key(r) for r in rows) if v is not None]
        stamps.sort()
        return [b - a for a, b in zip(stamps, stamps[1:]) if b > a]

    def projection(self, dex_target=188) -> dict:
        """Extrapolate, and say plainly how thin the evidence is.

        Eight badges from two is a fourfold extrapolation, and quoting a single
        number from that would be dishonest. So this returns a RANGE and the
        sample size that produced it, and the caller is expected to print both.
        """
        rows = self.read(self.path)
        badges = [r for r in rows if r["kind"] == "badge"]
        species = [r for r in rows if r["kind"] == "species"]
        # Count DISTINCT milestones, not rows. A replayed fork or a reloaded
        # state writes the same badge twice, and counting both shrinks the
        # "badges left" term -- the estimate then quietly falls as evidence is
        # duplicated, which is the opposite of what more data should do.
        n_badges = len({r["label"] for r in badges})
        n_species = len({r["label"] for r in species})
        out = {"badges_seen": n_badges, "species_seen": n_species}

        for name, key in (("play", self._play_seconds),
                          ("real", lambda r: r.get("at"))):
            gaps = self._gaps(badges, key)
            if not gaps:
                out[f"{name}_hours_to_eight_badges"] = None
                continue
            # Later badges cost more than earlier ones, so a flat mean
            # understates the finish. The last gap is the better predictor,
            # and the truth is somewhere between -- hence a range.
            mean_gap, last_gap = sum(gaps) / len(gaps), gaps[-1]
            done = [v for v in (key(r) for r in badges) if v is not None]
            spent = max(done) - min(done) if len(done) > 1 else 0
            remaining = max(0, 8 - n_badges)
            low = (spent + remaining * mean_gap) / 3600.0
            high = (spent + remaining * last_gap) / 3600.0
            out[f"{name}_hours_to_eight_badges"] = [round(min(low, high), 1),
                                                    round(max(low, high), 1)]
        out["badge_basis"] = (
            f"{n_badges} badges, "
            f"{len(self._gaps(badges, self._play_seconds))} usable gaps"
            if n_badges > 1 else
            f"{n_badges} badge: no gap to extrapolate from"
        )

        gaps = self._gaps(species, self._play_seconds)
        if len(gaps) >= 4:
            per = sum(gaps) / len(gaps)
            out["play_hours_to_full_dex"] = round(
                per * max(0, dex_target - n_species) / 3600.0, 1)
            out["dex_basis"] = (
                f"{n_species} species, {len(gaps)} gaps; assumes a constant "
                f"rate, which is optimistic -- late species need items, trades "
                f"or the sea"
            )
        else:
            out["play_hours_to_full_dex"] = None
            out["dex_basis"] = f"only {n_species} species timed"
        return out
