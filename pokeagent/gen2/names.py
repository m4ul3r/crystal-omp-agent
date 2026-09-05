"""Name tables decoded from the built ROM (via the .sym file) and from
constants/map_constants.asm. No hand-maintained lists: species, move, and
item names are read out of the same ROM the emulator runs.
"""

import re

NUM_POKEMON = 251
NUM_MOVES = 251
MON_NAME_LENGTH = 11  # 10 chars + "@"


def _rom_offset(bank, addr):
    if addr < 0x4000:
        return addr
    return bank * 0x4000 + (addr - 0x4000)


class Names:
    def __init__(self, rom_path, sym, charmap, map_constants_path):
        rom = open(rom_path, "rb").read()
        self._cm = charmap

        base = _rom_offset(*sym["PokemonNames"])
        self.species = {}
        for i in range(NUM_POKEMON):
            raw = rom[base + i * 10 : base + (i + 1) * 10]
            self.species[i + 1] = charmap.decode(raw).rstrip(" ")

        self.moves = self._walk(rom, _rom_offset(*sym["MoveNames"]), NUM_MOVES)
        self.items = self._walk(rom, _rom_offset(*sym["ItemNames"]), 255)

        self.maps = {}
        group = 0
        for line in open(map_constants_path, encoding="utf-8"):
            m = re.match(r"\tnewgroup\s+(\w+)", line)
            if m:
                group += 1
                num = 0
                continue
            m = re.match(r"\tmap_const\s+(\w+),", line)
            if m and group:
                num += 1
                self.maps[(group, num)] = m.group(1)

    def _walk(self, rom, base, count):
        """Read `count` consecutive '@'-terminated strings; ids start at 1."""
        out = {}
        pos = base
        for i in range(count):
            end = rom.index(b"\x50", pos)  # $50 = "@"
            out[i + 1] = self._cm.decode(rom[pos:end])
            pos = end + 1
        return out
