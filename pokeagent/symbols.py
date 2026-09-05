"""Symbol table: name -> (address, size).

Parses ``pokesapphire_rev2.sym``, which `make syms` produces from the linked
ELF (pret/Makefile:336-337)::

    02025734 g 00003ac0 gSaveBlock1
    03004360 g 00000258 gPlayerParty
    081febc0 g 00002d10 gBaseStats

Columns are ``address binding size name``; binding is ``g`` (global) or ``l``
(file-local static). The build filters to sections 02 (EWRAM), 03 (IWRAM),
08/09 (ROM), so RAM variables and ROM data tables come out of one file.

This is the Sapphire analog of pokecrystal's ``.sym``, and it exists for the
same reason: so nothing in this harness ever hardcodes an address. Gen 3 is
kinder than Gen 2 here -- the GBA has a flat 32-bit address space, so there
is no bank to get wrong (crystalagent/emu.py's whole ``_banked`` dance and
the "WRAM banks >= 1 silently return garbage" gotcha simply do not exist).

Struct field offsets are NOT in this file -- they come from the C headers via
:mod:`pokeagent.cconst`. What lives here is where each object starts.
"""

import re
from dataclasses import dataclass

from . import paths

_LINE = re.compile(r"^([0-9a-fA-F]{8}) ([gl]) ([0-9a-fA-F]{8}) (\S+)$")

EWRAM = 0x02000000
IWRAM = 0x03000000
ROM = 0x08000000


@dataclass(frozen=True, slots=True)
class Symbol:
    name: str
    addr: int
    size: int
    local: bool

    @property
    def region(self):
        return {0x02: "ewram", 0x03: "iwram", 0x08: "rom", 0x09: "rom"}.get(
            self.addr >> 24, "?"
        )


class Symbols:
    """Parsed ``.sym``. Global symbols win over file-local ones of the same
    name (several translation units define private statics that collide)."""

    def __init__(self, path=None):
        self.path = paths.require(
            path or paths.SYM,
            "symbol table",
            "build it: scripts/build_rom.sh (it also proves your ROM matches)",
        )
        self.by_name: dict[str, Symbol] = {}
        #: Every symbol, including the file-local duplicates that `by_name`
        #: has to drop. Three different translation units define a static
        #: called `MainCB2`; resolving an address must still name the right
        #: one, so the address index is built from this, not from by_name.
        self.all: list[Symbol] = []
        self._sorted: list[Symbol] | None = None
        for line in self.path.read_text().splitlines():
            m = _LINE.match(line)
            if not m:
                continue
            addr, binding, size, name = m.groups()
            sym = Symbol(name, int(addr, 16), int(size, 16), binding == "l")
            self.all.append(sym)
            prev = self.by_name.get(name)
            # A global definition always beats a local one; between two locals
            # keep the first so the table is deterministic.
            if prev is None or (prev.local and not sym.local):
                self.by_name[name] = sym

    def __len__(self):
        return len(self.by_name)

    def __contains__(self, name):
        return name in self.by_name

    def __getitem__(self, name) -> Symbol:
        try:
            return self.by_name[name]
        except KeyError:
            raise KeyError(
                f"no symbol {name!r} in {self.path.name}. "
                f"Try Symbols.find({name.strip('g')!r}) to search."
            ) from None

    def addr(self, name) -> int:
        return self[name].addr

    def size(self, name) -> int:
        return self[name].size

    def find(self, pattern) -> list[Symbol]:
        """Symbols whose name matches `pattern` (case-insensitive regex)."""
        rx = re.compile(pattern, re.I)
        return sorted(
            (s for s in self.by_name.values() if rx.search(s.name)),
            key=lambda s: s.addr,
        )

    def at(self, addr) -> Symbol | None:
        """Which object contains `addr`. Answers "what did I just read?" when
        a pointer chase lands somewhere unexpected."""
        if self._sorted is None:
            # `.gcc2_compiled.` and friends are assembler bookkeeping emitted
            # at the top of every object file. They carry no size and would
            # otherwise shadow the real function that starts at the same
            # address, turning `at()` into noise.
            self._sorted = sorted(
                (s for s in self.all if not s.name.startswith(".")),
                key=lambda s: s.addr,
            )
        import bisect

        i = bisect.bisect_right([s.addr for s in self._sorted], addr) - 1
        if i < 0:
            return None
        s = self._sorted[i]
        # A zero-size symbol is a label, not an object: only an exact hit counts.
        if s.addr == addr or (s.size and addr < s.addr + s.size):
            return s
        return None
