"""Vary our own timing, so two runs are not the same run.

The emulator is deterministic and so were we. Every wait in this harness was a
fixed frame count -- ``settle(900)``, ``advance_scene(120000)``, ``"A:4 .:16"``
-- and the game's RNG advances with frames. So the same savestate plus the same
code produced the same encounters, the same IVs, the same natures and the same
misses, for everybody. Two people running this would meet the same Zigzagoon
with the same stats, and nobody would ever see a shiny that the frame counts did
not already imply.

That is a different problem from `entropy.py`, which reseeds `gRngValue`
directly. This one is upstream of it: even with an identical seed, WHEN we press
a button decides what the seed becomes by the time the game rolls for anything.

So every wait gets a small random offset. Small on purpose -- these are settles
and timeouts whose job is "long enough", and a few frames either way never
changes whether a menu was ready, only which side of a roll we land on.

Reproducibility is kept as an opt-in, because it is how this project debugs:

    POKEAGENT_SEED=1234 .venv/bin/python scripts/play.py ...

pins the sequence so a bug found once can be found again. Unset -- the default
-- it seeds from the OS and the run is nobody else's run.
"""

from __future__ import annotations

import logging
import os
import random

log = logging.getLogger(__name__)

#: Fraction of a wait that may be added or removed. Deliberately modest: these
#: are "wait long enough" numbers, and the point is to move the RNG off a fixed
#: rail, not to make timeouts unpredictable.
DEFAULT_SPREAD = 0.12

#: Smallest jitter worth applying, in frames. Below this the offset rounds to
#: nothing and the call is pure overhead.
MIN_FRAMES = 2

_ENV_SEED = "POKEAGENT_SEED"


def _make_rng() -> tuple[random.Random, int | None]:
    raw = os.environ.get(_ENV_SEED)
    if raw:
        try:
            seed = int(raw, 0)
        except ValueError:
            seed = abs(hash(raw)) & 0xFFFFFFFF
        log.info("jitter: pinned to %s=%s (this run is reproducible)",
                 _ENV_SEED, raw)
        return random.Random(seed), seed
    seed = int.from_bytes(os.urandom(8), "little")
    return random.Random(seed), None


_rng, _pinned = _make_rng()


def pinned() -> int | None:
    """The seed if this run was pinned, else None."""
    return _pinned


def reseed(seed=None) -> None:
    """Re-seed the jitter stream. Used by tests that need determinism."""
    global _rng, _pinned
    if seed is None:
        _rng = random.Random(int.from_bytes(os.urandom(8), "little"))
        _pinned = None
    else:
        _rng = random.Random(seed)
        _pinned = seed


def frames(base: int, spread: float = DEFAULT_SPREAD) -> int:
    """`base` frames, give or take.

    Never returns less than 1, and never less than half of `base`: a settle
    that collapses to nothing would turn a timing fix back into a timing bug.
    """
    base = int(base)
    if base <= 0:
        return base
    swing = int(base * spread)
    if swing < MIN_FRAMES:
        # Short waits still get a frame or two, because a run made of hundreds
        # of them accumulates a real difference.
        swing = MIN_FRAMES if base > MIN_FRAMES else 0
    if not swing:
        return base
    return max(1, base // 2, base + _rng.randint(-swing, swing))


def pick(seq):
    """Choose from a sequence on the jitter stream, so route and tile choices
    vary between runs the same way waits do."""
    items = list(seq)
    return _rng.choice(items) if items else None


def chance(p: float) -> bool:
    """True with probability `p`, on the jitter stream."""
    return _rng.random() < p


def sequence(dsl: str, spread: float = DEFAULT_SPREAD) -> str:
    """Jitter the WAIT segments of an input DSL string.

    ``"A:4 .:16"`` becomes ``"A:4 .:15"`` or ``"A:4 .:18"``. Only the dot
    segments move: the number of frames a BUTTON is held changes whether the
    press registers at all, and that is not something to randomise.
    """
    out = []
    for token in dsl.split():
        if token.startswith(".:"):
            try:
                out.append(f".:{frames(int(token[2:]), spread)}")
                continue
            except ValueError:
                pass
        out.append(token)
    return " ".join(out)
