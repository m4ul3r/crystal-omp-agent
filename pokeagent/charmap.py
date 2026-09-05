"""Text codec, parsed from pret's ``charmap.txt``.

pokeruby's charmap is an *encoding* table -- `preproc` uses it to compile
``_("HELLO")`` literals into bytes at build time. We invert it to decode.

Three structural differences from pokecrystal's ``constants/charmap.asm``,
each of which is a way to get this wrong:

1. **The RHS can be several bytes.** ``PKMN = 53 54``, ``POKEBLOCK = 55 56 57
   58 59``. The reverse map is many-to-one, so decoding is longest-match,
   not a flat byte->glyph dict.
2. **Later blocks silently shadow earlier ones.** After the ``@ Hiragana``
   comment on line 158 the file re-maps bytes 01-A0 for the Japanese
   builds. Crystal delimited its alternate charmaps with ``pushc``/``popc``;
   here the only marker is a comment, so we stop at it. Decoding a US ROM
   with the Japanese block loaded turns every name into kana.
3. **Two tiers of control code**: ``FD <sel>`` is a placeholder (PLAYER,
   STR_VAR_1, ...) and ``FC <sel> [args]`` is an inline formatting command.
   Their argument counts are not in the file; they come from the engine and
   are listed in :data:`EXT_ARITY`.

Terminator is ``0xFF`` (``'$'``), space is ``0x00``.
"""

import re

from . import paths

#: Byte that ends a string.
EOS = 0xFF
#: Placeholder escape: one selector byte follows.
PLACEHOLDER = 0xFD
#: Inline formatting escape: one selector byte, then :data:`EXT_ARITY` args.
EXT_CTRL = 0xFC

#: Extra argument bytes consumed by each ``FC <sel>`` code, beyond the
#: selector itself. Sourced from the engine's own text interpreter, since
#: charmap.txt only hints at it in comments ("takes 3 bytes").
EXT_ARITY = {
    0x00: 0,  # NAME_END
    0x01: 1,  # COLOR
    0x02: 1,  # HIGHLIGHT
    0x03: 1,  # SHADOW
    0x04: 3,  # COLOR_HIGHLIGHT_SHADOW
    0x05: 1,  # PALETTE
    0x06: 1,  # SIZE
    0x08: 1,  # PAUSE <frames>
    0x09: 0,  # PAUSE_UNTIL_PRESS
    0x0B: 2,  # PLAY_BGM
    0x0C: 1,  # ESCAPE
    0x0D: 1,  # SHIFT_TEXT
    0x10: 2,  # PLAY_SE
    0x11: 0,  # CLEAR
    0x12: 1,  # SKIP
    0x13: 1,  # CLEAR_TO
    0x14: 1,  # UNKNOWN_14 (right-align to pixel width)
    0x15: 0,  # JPN
    0x16: 0,  # ENG
    0x17: 0,  # PAUSE_MUSIC
    0x18: 0,  # RESUME_MUSIC
}

#: Named single-byte controls rendered as plain text when decoding.
_CONTROL_TEXT = {0xFA: "\n", 0xFB: "\n", 0xFE: "\n"}

_LINE = re.compile(
    r"""^\s*
        (?: '((?:\\.|[^'])+)'      # 'A'  '\n'  'é'
          | ([A-Za-z_][A-Za-z0-9_]*) )   # PKMN  STR_VAR_1
        \s*=\s*
        ((?:[0-9A-Fa-f]{2}\s*)+?)
        \s*(?:@.*)?$""",
    re.X,
)
_ESCAPES = {"\\n": "\n", "\\l": "\n", "\\p": "\n", "\\\\": "\\", "\\'": "'"}


class Charmap:
    """Byte sequences <-> text, for the Latin (US/EU) block only."""

    def __init__(self, path=None):
        self.path = paths.require(
            path or paths.CHARMAP, "charmap.txt", "is the pret/ submodule checked out?"
        )
        #: token/char -> bytes, for encoding (names typed on a keyboard).
        self.encode_map: dict[str, bytes] = {}
        #: bytes -> text, for decoding. Longest key first when matching.
        self.decode_map: dict[bytes, str] = {}

        for line in self.path.read_text(encoding="utf-8").splitlines():
            # The Japanese blocks re-map bytes 01-A0. Everything a US ROM
            # renders is above this line.
            if line.strip().lower().startswith("@ hiragana"):
                break
            m = _LINE.match(line)
            if not m:
                continue
            lit, ident, hexes = m.groups()
            seq = bytes(int(b, 16) for b in hexes.split())
            token = _ESCAPES.get(lit, lit) if lit is not None else ident
            self.encode_map.setdefault(token, seq)
            # Identifier tokens (COLOR, RED, TRANSPARENT...) share byte values
            # with real characters -- the colour block at the tail of the file
            # maps RED=02, which must not shadow 'Á'=02. Only character
            # literals and multi-byte sequences enter the decode table.
            if lit is not None or len(seq) > 1:
                self.decode_map.setdefault(seq, token if lit is None else token)

        self._multi = sorted(
            (k for k in self.decode_map if len(k) > 1), key=len, reverse=True
        )
        self._single = {k[0]: v for k, v in self.decode_map.items() if len(k) == 1}

    def decode(self, data, stop_at_eos=True, keep_controls=False) -> str:
        """Bytes -> str. Stops at ``0xFF`` unless told otherwise.

        Control codes become ``{TOKEN}`` when `keep_controls`, else they are
        dropped (``FC``) or rendered as ``{PLAYER}``-style markers (``FD``),
        because a placeholder is real content the caller usually wants.
        """
        out, i, n = [], 0, len(data)
        while i < n:
            b = data[i]
            if b == EOS:
                if stop_at_eos:
                    break
                i += 1
                continue
            if b == EXT_CTRL and i + 1 < n:
                sel = data[i + 1]
                if keep_controls:
                    out.append(f"{{FC:{sel:02X}}}")
                i += 2 + EXT_ARITY.get(sel, 0)
                continue
            if b == PLACEHOLDER and i + 1 < n:
                sel = data[i + 1]
                out.append(self.decode_map.get(bytes((b, sel)), f"{{FD:{sel:02X}}}"))
                i += 2
                continue
            if b in _CONTROL_TEXT:
                out.append(_CONTROL_TEXT[b])
                i += 1
                continue
            for seq in self._multi:
                if data[i : i + len(seq)] == seq:
                    out.append(self.decode_map[seq])
                    i += len(seq)
                    break
            else:
                out.append(self._single.get(b, "\ufffd"))
                i += 1
        return "".join(out)

    def encode(self, text, pad_to=None) -> bytes:
        """str -> bytes, terminated with ``0xFF`` and optionally padded.

        Used to type names on the naming keyboard and to match strings
        against ROM tables without decoding every entry.
        """
        out = bytearray()
        i = 0
        while i < len(text):
            for token in sorted(self.encode_map, key=len, reverse=True):
                if len(token) > 1 and text.startswith(token, i):
                    out += self.encode_map[token]
                    i += len(token)
                    break
            else:
                ch = text[i]
                if ch not in self.encode_map:
                    raise ValueError(f"character {ch!r} is not in charmap.txt")
                out += self.encode_map[ch]
                i += 1
        out.append(EOS)
        if pad_to is not None:
            if len(out) > pad_to:
                raise ValueError(f"{text!r} encodes to {len(out)} > {pad_to} bytes")
            out += bytes(pad_to - len(out))
        return bytes(out)
