"""PyBoy wrapper: symbol-addressed memory reads, screen-text decode,
an input DSL, and savestate files.
"""

import json
import hashlib
import sys
from importlib import metadata as _pkg_metadata
from pathlib import Path

SCREEN_W, SCREEN_H = 20, 18

_BUTTONS = {
    "A": "a", "B": "b",
    "START": "start", "ST": "start",
    "SELECT": "select", "SEL": "select",
    "UP": "up", "U": "up",
    "DOWN": "down", "D": "down",
    "LEFT": "left", "L": "left",
    "RIGHT": "right", "R": "right",
}


class InputError(ValueError):
    pass


def _rom_digest(path):
    """SHA-256 of the ROM file; savestates only load against the same ROM."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _pyboy_version():
    try:
        return _pkg_metadata.version("pyboy")
    except Exception:
        return "unknown"


def parse_sequence(text):
    """Parse the input DSL into [(set_of_buttons, frames), ...].

    Tokens are whitespace/comma separated:
      A            press A for 8 frames (then 2 release frames)
      A:2          press A for 2 frames
      UP:16        hold UP for 16 frames
      A+B:5        hold A and B together for 5 frames
      .            wait 1 frame            .:30  wait 30 frames
      A:2*5        repeat the token 5 times
    """
    steps = []
    for token in text.replace(",", " ").split():
        repeat = 1
        if "*" in token:
            token, n = token.rsplit("*", 1)
            repeat = int(n)
        if ":" in token:
            token, f = token.rsplit(":", 1)
            frames = int(f)
        else:
            frames = None
        if token in (".", "WAIT"):
            buttons = frozenset()
            frames = 1 if frames is None else frames
        else:
            try:
                buttons = frozenset(_BUTTONS[b.upper()] for b in token.split("+"))
            except KeyError as e:
                raise InputError(f"unknown button {e.args[0]!r} in token {token!r}")
            frames = 8 if frames is None else frames
        steps.extend([(buttons, frames)] * repeat)
    return steps


class Crystal:
    def __init__(self, rom_path, sym, charmap, state_path=None):
        import logging
        logging.getLogger("pyboy").setLevel(logging.CRITICAL)
        from pyboy import PyBoy

        self.sym = sym
        self.charmap = charmap
        self.py = PyBoy(str(rom_path), window="null", sound_emulated=False,
                        log_level="CRITICAL", symbols=None)
        self._pyboy_version = _pyboy_version()
        self._rom_sha256 = _rom_digest(rom_path)
        # PyBoy's frame counter is per-process; carry the cumulative count
        # across invocations in a sidecar next to the state file.
        self._base_frames = 0
        if state_path is not None:
            with open(state_path, "rb") as f:
                self.py.load_state(f)
            meta = Path(f"{state_path}.meta")
            stamped = {}
            if meta.exists():
                stamped = json.loads(meta.read_text())
                self._base_frames = stamped.get("frames", 0)
            missing = [k for k in ("pyboy", "rom_sha256") if k not in stamped]
            if missing:
                print(f"note: {meta.name} lacks provenance stamps "
                      f"({', '.join(missing)}); assuming compatible",
                      file=sys.stderr)
            for key, ours in (("pyboy", self._pyboy_version),
                              ("rom_sha256", self._rom_sha256)):
                theirs = stamped.get(key)
                if theirs is not None and theirs != ours:
                    raise RuntimeError(
                        f"{state_path}: written by {key}={theirs!r}, running "
                        f"{key}={ours!r}; refusing to load (savestate format "
                        "is version-coupled)")
        self._start_count = self.py.frame_count

    # -- memory ------------------------------------------------------------

    def resolve(self, name_or_addr):
        if isinstance(name_or_addr, tuple):
            return name_or_addr
        if isinstance(name_or_addr, int):
            return 0, name_or_addr
        return self.sym[name_or_addr]

    def read(self, name_or_addr, length=1):
        bank, addr = self.resolve(name_or_addr)
        mem = self.py.memory
        if 0xD000 <= addr < 0xE000 and bank < 1:
            raise ValueError(
                f"$D000-$DFFF is SVBK-switched WRAM; read it through a "
                f"banked symbol (`crystal sym`), not raw address {addr:#06x}")
        if addr < 0x8000 or (0xD000 <= addr < 0xE000 and bank >= 1):
            data = mem[bank, addr] if length == 1 else mem[bank, addr:addr + length]
        else:
            data = mem[addr] if length == 1 else mem[addr:addr + length]
        return bytes([data]) if length == 1 else bytes(data)

    def read_u8(self, name):
        return self.read(name)[0]

    def read_be(self, name, length):
        return int.from_bytes(self.read(name, length), "big")

    def read_text(self, name, length):
        return self.charmap.decode(self.read(name, length))

    def write(self, name_or_addr, data):
        """Write bytes (or a single int) to WRAM/HRAM; banked symbols OK."""
        bank, addr = self.resolve(name_or_addr)
        if isinstance(data, int):
            data = bytes([data])
        mem = self.py.memory
        if 0xD000 <= addr < 0xE000 and bank < 1:
            raise ValueError(
                f"$D000-$DFFF is SVBK-switched WRAM; write it through a "
                f"banked symbol (`crystal sym`), not raw address {addr:#06x}")
        targets = [(bank, addr + i) for i in range(len(data))] \
            if addr < 0x8000 or (0xD000 <= addr < 0xE000 and bank >= 1) \
            else [addr + i for i in range(len(data))]
        for t, b in zip(targets, data):
            mem[t] = b

    # -- time --------------------------------------------------------------

    @property
    def frame(self):
        return self._base_frames + (self.py.frame_count - self._start_count)

    def tick(self, frames=1):
        self.py.tick(frames, False)

    def run_sequence(self, steps):
        """Execute parsed input steps; returns frames advanced."""
        start = self.frame
        for buttons, frames in steps:
            for b in buttons:
                self.py.button_press(b)
            self.tick(frames)
            if buttons:
                for b in buttons:
                    self.py.button_release(b)
                self.tick(2)  # let the release land so repeat presses register
        return self.frame - start

    # -- observation -------------------------------------------------------

    def tilemap(self):
        return self.read("wTilemap", SCREEN_W * SCREEN_H)

    def screen_text(self):
        """The 20x18 BG shadow tilemap (wTilemap) decoded through the charmap."""
        tm = self.tilemap()
        cm = self.charmap
        return [
            "".join(cm.cell(b) for b in tm[y * SCREEN_W:(y + 1) * SCREEN_W])
            for y in range(SCREEN_H)
        ]

    def screen_contains(self, needle):
        return any(needle in row for row in self.screen_text())

    def screenshot(self, path):
        self.py.tick(1, True)  # render one frame so the screen buffer is fresh
        self.py.screen.image.save(path)

    # -- persistence -------------------------------------------------------

    def save(self, state_path):
        state_path = Path(state_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        # write-then-rename: concurrent readers (watch.py reloads on mtime
        # change) must never see a half-written savestate
        tmp = state_path.with_suffix(state_path.suffix + ".tmp")
        with open(tmp, "wb") as f:
            self.py.save_state(f)
        tmp.replace(state_path)
        meta_tmp = Path(f"{state_path}.meta.tmp")
        meta_tmp.write_text(json.dumps({
            "frames": self.frame,
            "pyboy": self._pyboy_version,
            "rom_sha256": self._rom_sha256,
        }))
        meta_tmp.replace(Path(f"{state_path}.meta"))

    def stop(self):
        self.py.stop(save=False)
