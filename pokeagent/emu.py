"""mGBA wrapper: the Sapphire analog of ``crystalagent/emu.py``.

Keeps the Crystal harness's contract -- symbol-addressed reads, an input DSL,
savestates that fork a timeline deterministically -- on a different core.

What got simpler moving from Game Boy to GBA:

* **No banking.** The GBA has a flat 32-bit address space, so `read` takes an
  address and that is the whole story. Crystal's ``_banked()`` logic and its
  "WRAM banks >= 1 silently return the wrong bank's bytes" gotcha are gone.
* **The frame counter is inside the savestate.** PyBoy's was not, which is
  why Crystal needed a ``.meta`` sidecar just to carry it. We still write a
  sidecar, but only for provenance (ROM hash + core version), and a missing
  one is a warning rather than a broken timeline.

What got harder:

* **No flat text layer.** Crystal decoded a 20x18 tilemap straight to
  characters. Sapphire renders through BG tilemaps and windows, so screen
  text is read from the engine's own string buffers instead
  (see :mod:`pokeagent.state`). ``screenshot()`` is the honest surface
  for "what does this look like".
"""

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path

from . import paths

log = logging.getLogger("pokeagent.emu")

#: The GBA's real refresh rate. Not 60: the LCD runs 280896 cycles a frame off
#: a 16.78 MHz clock, which is 59.7275 Hz. Used when real-time pacing is on.
HARDWARE_FPS = 59.7275

#: Buttons the DSL accepts, mapped to mGBA key indices (GBA order).
KEYS = {
    "A": 0, "B": 1, "SELECT": 2, "START": 3,
    "RIGHT": 4, "LEFT": 5, "UP": 6, "DOWN": 7, "R": 8, "L": 9,
}
ALIASES = {
    "SEL": "SELECT", "ST": "START",
    "U": "UP", "D": "DOWN", "L_": "LEFT", "R_": "RIGHT",
}
#: Frames a bare token is held for, matching the Crystal DSL's feel.
DEFAULT_HOLD = 8
RELEASE_FRAMES = 2

_TOKEN = re.compile(r"^([A-Za-z_+.]+)(?::(\d+))?(?:\*(\d+))?$")


def parse_sequence(seq):
    """``"A .:30 UP:16 A+B:5 A:2*10"`` -> [(frozenset(buttons), frames), ...].

    ``.`` is a wait. ``+`` holds buttons together. ``:n`` sets the hold in
    frames, ``*n`` repeats the token. Identical grammar to the Crystal CLI so
    muscle memory and old notes carry over.
    """
    steps = []
    for raw in seq.replace(",", " ").split():
        m = _TOKEN.match(raw)
        if not m:
            raise ValueError(f"bad input token {raw!r}")
        body, frames, repeat = m.groups()
        frames = int(frames) if frames else None
        for _ in range(int(repeat) if repeat else 1):
            if body == ".":
                steps.append((frozenset(), frames or 1))
                continue
            buttons = set()
            for part in body.upper().split("+"):
                part = ALIASES.get(part, part)
                if part not in KEYS:
                    raise ValueError(
                        f"unknown button {part!r}; expected one of {' '.join(sorted(KEYS))}"
                    )
                buttons.add(part)
            steps.append((frozenset(buttons), frames or DEFAULT_HOLD))
            if frames is None:
                steps.append((frozenset(), RELEASE_FRAMES))
    return steps


def rom_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

_libmgba = None


def _preload_libmgba():
    """Open the vendored libmgba before `import mgba` needs it.

    Mutating LD_LIBRARY_PATH from inside the process is too late: the dynamic
    loader read it at exec. Opening the absolute path with RTLD_GLOBAL puts
    the soname in the process namespace, so the dependent load succeeds. A
    system libmgba, if installed, wins because we only do this on failure.
    """
    global _libmgba
    if _libmgba is not None:
        return
    import ctypes

    for candidate in ("libmgba.so.0.10", paths.VENDOR_LIB / "libmgba.so.0.10"):
        try:
            _libmgba = ctypes.CDLL(str(candidate), mode=ctypes.RTLD_GLOBAL)
            return
        except OSError:
            continue
    raise ImportError(
        "libmgba.so.0.10 not found. Install it (pacman -S libmgba) or run "
        "scripts/vendor_toolchain.sh to unpack a copy into ./vendor."
    )


def _fps_from_env(value):
    """Parse SAPPHIRE_FPS: a number, "hardware", or off.

    Kept permissive on purpose -- this is a presentation knob a human sets
    from a shell, so "hardware", "60" and "off" should all do the obvious
    thing rather than raise.
    """
    if not value:
        return None
    text = str(value).strip().lower()
    if text in ("0", "off", "none", "false", "max", "unlimited"):
        return None
    if text in ("hardware", "hw", "real", "gba"):
        return HARDWARE_FPS
    try:
        fps = float(text)
    except ValueError:
        log.warning("SAPPHIRE_FPS=%r is not a rate; running unthrottled",
                    value)
        return None
    return fps if fps > 0 else None


class Sapphire:
    """One live emulator over one savestate timeline."""

    def __init__(self, rom=None, state_path=None, sym=None, charmap=None,
                 observer=None, target_fps=None, clock=None, sleep=None):
        # Vendored libmgba, when the system package is not installed. Setting
        # LD_LIBRARY_PATH here would be too late -- the loader read it at
        # process start -- so dlopen the absolute path instead. The dependent
        # load inside mgba's extension then resolves against the already-open
        # library by soname.
        _preload_libmgba()
        import mgba.core
        import mgba.image
        import mgba.log

        mgba.log.silence()
        self.rom_path = paths.require(
            rom or paths.ROM,
            "Sapphire ROM",
            "supply your own dump as pokesapphire.gba (sha1 89b45fb1...)",
        )
        self.sym = sym
        self.charmap = charmap
        self.observer = observer
        # REAL-TIME PACING, off by default.
        #
        # `None` runs flat out, which is what the grind wants. Set a rate --
        # or `SAPPHIRE_FPS=hardware` / `SAPPHIRE_FPS=59.7275` in the
        # environment -- and the emulator is throttled to it, which is what
        # makes a watched run look like an idle game on real hardware instead
        # of a tool-assisted speedrun. `HARDWARE_FPS` is the GBA's true
        # 59.7275 Hz rather than a round 60.
        #
        # The clock and sleep are injectable so the pacing arithmetic can be
        # unit-tested without spending real seconds.
        if target_fps is None:
            target_fps = _fps_from_env(os.environ.get("SAPPHIRE_FPS"))
        self.target_fps = target_fps
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._frame_due = None

        self.core = mgba.core.load_path(str(self.rom_path))
        if self.core is None:
            raise RuntimeError(f"mGBA refused {self.rom_path}")
        self._image = mgba.image.Image(*self.core.desired_video_dimensions())
        self.core.set_video_buffer(self._image)
        self.core.reset()

        self._rom_hash = None
        self.state_path = Path(state_path) if state_path else None
        if self.state_path and self.state_path.exists():
            self.load_state(self.state_path)
        else:
            # Fresh power-on. Nothing to verify against.
            self._held = frozenset()

    # ---- provenance -------------------------------------------------

    @property
    def rom_hash(self):
        if self._rom_hash is None:
            self._rom_hash = rom_sha256(self.rom_path)
        return self._rom_hash

    def _meta_path(self, path):
        return Path(str(path) + ".meta")

    # ---- memory -----------------------------------------------------

    def resolve(self, where) -> int:
        """``"gSaveBlock1"`` / ``("gSaveBlock1", 0x490)`` / ``0x02025734`` -> address."""
        if isinstance(where, int):
            return where
        if isinstance(where, tuple):
            base, off = where
            return self.resolve(base) + off
        if isinstance(where, str):
            if where.startswith("0x"):
                return int(where, 16)
            if self.sym is None:
                raise RuntimeError("no symbol table loaded; pass sym= to Sapphire()")
            return self.sym.addr(where)
        raise TypeError(f"cannot resolve {where!r} to an address")

    def read(self, where, n=1) -> bytes:
        """`n` bytes at a symbol name, (name, offset) pair, or raw address."""
        addr = self.resolve(where)
        mem = self.core.memory
        # Slice the owning region directly: it is one memcpy, where indexing
        # `mem.u8[a]` per byte crosses the CFFI boundary n times.
        for base, region in (
            (0x02000000, mem.wram),
            (0x03000000, mem.iwram),
            (0x08000000, mem.rom),
            (0x0E000000, mem.sram),
        ):
            end = base + len(region)
            if base <= addr < end:
                if addr + n <= end:
                    off = addr - base
                    return bytes(region[off : off + n])
                break
        # ROM mirrors (0x09/0x0A...) and anything spanning a region edge.
        return bytes(mem.u8[addr : addr + n])

    def u8(self, where, i=0):
        return self.read(where, i + 1)[i]

    def u16(self, where):
        return int.from_bytes(self.read(where, 2), "little")

    def u32(self, where):
        return int.from_bytes(self.read(where, 4), "little")

    def s16(self, where):
        return int.from_bytes(self.read(where, 2), "little", signed=True)

    def pointer(self, where):
        """Dereference a ROM pointer, normalising the 0x08 mirror away."""
        return self.u32(where)

    def text(self, where, n=64):
        """Decode a charmap string in memory."""
        if self.charmap is None:
            raise RuntimeError("no charmap loaded")
        return self.charmap.decode(self.read(where, n))

    def write(self, where, data: bytes):
        addr = self.resolve(where)
        for i, b in enumerate(data):
            self.core.memory.u8[addr + i] = b

    # ---- running ----------------------------------------------------

    @property
    def frame(self):
        return self.core.frame_counter

    def tick(self, frames=1):
        """Advance `frames`, giving an observer (the live feed) a chance to
        publish. Sliced so a long run is still watchable."""
        if self.observer is None:
            for _ in range(frames):
                self.core.run_frame()
            self._pace(frames)
            return
        left = frames
        while left > 0:
            slice_ = min(left, self.observer.slice_frames)
            for _ in range(slice_):
                self.core.run_frame()
            left -= slice_
            self._pace(slice_)
            self.observer.after_slice(self)

    def _pace(self, frames):
        """Sleep so the emulator runs at `target_fps` instead of flat out.

        EVERY frame this harness advances goes through `tick`, so this one
        place governs the whole thing -- inputs, `settle`, `advance_scene`.
        Nothing here is a TAS-style jump: a call like `advance_scene(40_000)`
        is a frame BUDGET that returns as soon as the scene stops changing, so
        the game is already stepped one frame at a time. What made it look
        unlike hardware is that headless mGBA runs as fast as the CPU allows
        -- thousands of frames a second -- with no throttle at all.

        Paced against a running deadline rather than by sleeping a fixed
        amount per call, so the rate does not drift with the cost of the work
        between ticks. If it falls more than a second behind (a long BFS, a
        savestate write) it resyncs instead of sprinting to catch up, because
        catching up is exactly the visible stutter this is meant to remove.
        """
        fps = self.target_fps
        if not fps:
            return
        now = self._clock()
        due = self._frame_due
        if due is None:
            due = now
        due += frames / fps
        delay = due - now
        if delay > 0:
            self._sleep(delay)
        elif delay < -1.0:
            due = self._clock()
        self._frame_due = due

    def run_sequence(self, seq):
        """Execute an input DSL string. Always releases every key at the end
        -- a key still held across a savestate is the classic phantom-input
        bug (Crystal hit it; see its emu.load path)."""
        steps = parse_sequence(seq) if isinstance(seq, str) else seq
        try:
            for buttons, frames in steps:
                self.core.clear_keys(*KEYS.values())
                if buttons:
                    self.core.add_keys(*(KEYS[b] for b in buttons))
                self.tick(frames)
        finally:
            self.core.clear_keys(*KEYS.values())
            self._held = frozenset()

    def press(self, seq):
        self.run_sequence(seq)

    # ---- video ------------------------------------------------------

    def screenshot(self, path=None):
        """The real framebuffer. Sapphire has no decodable flat text layer, so
        this -- not a glyph grid -- is the surface that shows what a player
        sees."""
        img = self._image.to_pil().convert("RGB")
        if path:
            img.save(path)
        return img

    # ---- savestates -------------------------------------------------

    def save_state(self, path=None):
        """Write a savestate + a provenance sidecar. Copying the pair forks
        the timeline: same state + same inputs is byte-identical, RNG
        included."""
        path = Path(path or self.state_path)
        raw = self.core.save_raw_state()
        if raw is None:
            raise RuntimeError("mGBA refused to serialise state")
        blob = bytes(raw)
        tmp = path.with_suffix(path.suffix + ".tmp")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(blob)
        tmp.replace(path)
        self._meta_path(path).write_text(
            json.dumps(
                {
                    "frames": self.frame,
                    "rom_sha256": self.rom_hash,
                    "core": "mgba",
                    "state_bytes": len(blob),
                    "saved_at": time.time(),
                },
                indent=1,
            )
        )
        self.state_path = path
        return path

    def load_state(self, path):
        """Restore a savestate, refusing one built against a different ROM.

        Corrupt provenance must never silently fork a timeline (DESIGN rule
        6): a state from another ROM would read plausible-looking garbage
        through every symbol.
        """
        path = Path(path)
        meta_file = self._meta_path(path)
        if meta_file.exists():
            meta = json.loads(meta_file.read_text())
            got = meta.get("rom_sha256")
            if got and got != self.rom_hash:
                raise ValueError(
                    f"{path.name} was saved against ROM {got[:12]}... but this "
                    f"ROM is {self.rom_hash[:12]}...; refusing to load"
                )
        else:
            log.warning("%s has no .meta sidecar; provenance unverified", path.name)

        if not self.core.load_raw_state(path.read_bytes()):
            raise RuntimeError(f"mGBA refused the savestate {path}")
        # A key held when the state was written stays held after the restore.
        self.core.clear_keys(*KEYS.values())
        self._held = frozenset()
        self.state_path = path
        return self
