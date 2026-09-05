"""Parse constants/charmap.asm: the game's tile-byte <-> character encoding.

Two products:
  - tokens: byte -> charmap token string ("A", "<PK>", "┌", ...) for decoding
    text data (names, strings) losslessly.
  - display: byte -> exactly one terminal cell, for rendering the 20x18
    wTilemap screen. Multi-char tokens get a single-cell stand-in; bytes not
    in the charmap (map graphics tiles) get a stable per-byte glyph so
    walls/floors/edges stay visually distinguishable.
"""

import re

_LINE = re.compile(r'^\s*charmap\s+"((?:[^"\\]|\\.)+)",\s+\$([0-9a-fA-F]+)(.*)$')

# Single-cell stand-ins for multi-char tokens that can appear on screen.
_CELL = {
    "<PK>": "ᴾ", "<MN>": "ᴹ", "<PO>": "ᴾ", "<KE>": "ᴷ",
    "'d": "ᵈ", "'l": "ˡ", "'m": "ᵐ", "'r": "ʳ", "'s": "ˢ",
    "'t": "ᵗ", "'v": "ᵛ",
    "<LV>": "ᴸ", "<ID>": "№", "<COLON>": ":", "<DOT>": ".",
    "<……>": "…", "<NULL>": " ",
}

# Stable fallback glyphs for non-text tiles (map graphics). One glyph per
# byte value so identical tiles render identically; chosen to not collide
# with real font characters.
_FALLBACK = (
    "▁▂▃▄▅▆▇▊▋▌▍▎▏▐▔▕▖▗▘▙▚▛▜▝▞▟░▒▓"
    "○●◐◑◒◓◔◕◖◗◜◝◞◟◠◡◢◣◤◥◦◧◨◩◪◫◬◭◮"
    "αβγδεζηθικλμνξπρστυφχψω∆∇∈∏∑√∝∞∟∠∫≈≠≤≥⊂⊃⊕⊗"
)


def _fallback(byte):
    return _FALLBACK[byte % len(_FALLBACK)]


class Charmap:
    def __init__(self, path):
        self.tokens = {}       # byte -> token, preferring non-"unused" entries
        first = {}             # byte -> very first token seen
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip() == "pushc":
                    break  # unown/ascii charmaps follow; not the game font
                m = _LINE.match(line)
                if not m:
                    continue
                token = m.group(1).replace('\\"', '"').replace("\\\\", "\\")
                byte = int(m.group(2), 16)
                unused = "unused" in m.group(3).lower()
                first.setdefault(byte, token)
                if not unused and byte not in self.tokens:
                    self.tokens[byte] = token
        for byte, token in first.items():
            self.tokens.setdefault(byte, token)

        self.display = {}
        for byte, token in self.tokens.items():
            if byte < 0x60:
                # kana/control region doubles as map-graphics tile ids in the
                # overworld; render those as generic tiles, not text
                continue
            cell = _CELL.get(token, token)
            if len(cell) != 1:
                cell = token.strip("<>")[:1] or _fallback(byte)  # "<BOLD_V>" -> "B"... prefer letter
                if token.startswith("<BOLD_") and len(token) == 8:
                    cell = token[6]
            self.display[byte] = cell

    def cell(self, byte):
        return self.display.get(byte, _fallback(byte))

    def decode(self, data, stop_at_terminator=True):
        """Decode game-encoded bytes to a string ('@' = terminator)."""
        out = []
        for b in data:
            tok = self.tokens.get(b)
            if stop_at_terminator and tok == "@":
                break
            out.append(tok if tok is not None else "<$%02x>" % b)
        return "".join(out)
