"""Parse the rgblink .sym file into name -> (bank, address)."""

import re

_LINE = re.compile(r"^([0-9a-fA-F]{2,3}):([0-9a-fA-F]{4})\s+(\S+)$")


class Symbols:
    def __init__(self, path):
        self.by_name = {}
        with open(path, encoding="utf-8") as f:
            for line in f:
                m = _LINE.match(line.strip())
                if not m:
                    continue
                name = m.group(3)
                # first definition wins
                self.by_name.setdefault(name, (int(m.group(1), 16), int(m.group(2), 16)))

    def __contains__(self, name):
        return name in self.by_name

    def __getitem__(self, name):
        return self.by_name[name]

    def addr(self, name):
        return self.by_name[name][1]

    def bank(self, name):
        return self.by_name[name][0]

    def offset(self, field, base):
        """Byte offset of a labeled field from a base label (same bank)."""
        return self.addr(field) - self.addr(base)

    def find(self, pattern):
        rx = re.compile(pattern, re.IGNORECASE)
        return sorted(
            (name, bank, addr)
            for name, (bank, addr) in self.by_name.items()
            if rx.search(name)
        )
