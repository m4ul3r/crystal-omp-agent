"""Parsers for the disassembly's own constant files.

Every number the harness needs about the game is defined once, in an
``.asm`` file in the pokecrystal checkout. Copying one into Python is how
the type ids ended up 9 apart from the engine's (see
``battle._parse_types``) and how accuracy spent weeks reported as
"everything hits", so the rule is: parse the file, never retype it.

Three shapes cover everything used here:

- ``const`` enumerations (``macros/const.asm``): status bits, substatus
  bits, type ids.
- ``DEF NAME EQU <literal>`` definitions: SLP_MASK, BASE_STAT_LEVEL.
- ``db num, den`` ratio tables: the accuracy/evasion stage multipliers.

The macro semantics implemented here are exactly the ones in
``macros/const.asm``: ``const_def [start [, inc]]`` seeds the counter and
step, ``const NAME`` assigns then advances, ``const_skip [n]`` advances
without naming, ``const_next N`` jumps the counter forward, and
``shift_const NAME`` defines ``NAME`` as ``1 << counter`` plus ``NAME_F``
as the shift itself. Anything else on a line is ignored, which is what
makes ``DEF PHYSICAL EQU const_value`` (an expression, not a literal)
harmless instead of a wrong answer.
"""

import re
from pathlib import Path

_CONST_DEF = re.compile(r"^\s+const_def(?:\s+(-?\d+)\s*(?:,\s*(-?\d+))?)?\s*(?:;.*)?$")
_CONST = re.compile(r"^\s+const\s+(\w+)\s*(?:;.*)?$")
_SHIFT_CONST = re.compile(r"^\s+shift_const\s+(\w+)\s*(?:;.*)?$")
_CONST_SKIP = re.compile(r"^\s+const_skip(?:\s+(-?\d+))?\s*(?:;.*)?$")
_CONST_NEXT = re.compile(r"^\s+const_next\s+(-?\d+)\s*(?:;.*)?$")
_DEF = re.compile(r"^\s*DEF\s+(\w+)\s+EQU\s+(\S+)\s*(?:;.*)?$")
_DB_PAIR = re.compile(r"^\s+db\s+(\d+)\s*,\s*(\d+)\s*(?:;.*)?$")

_cache = {}


def _lines(path):
    path = Path(path)
    key = ("lines", str(path.resolve()))
    if key not in _cache:
        _cache[key] = path.read_text().splitlines()
    return _cache[key]


def _literal(text):
    """``42`` / ``$2a`` / ``%101`` -> int; anything else -> None.

    Expressions (``const_value``, ``TYPES_END + UNUSED_TYPES - 1``) are
    deliberately skipped: guessing at them is how a wrong constant gets
    into the harness quietly.
    """
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"\$[0-9a-fA-F]+", text):
        return int(text[1:], 16)
    if re.fullmatch(r"%[01]+", text):
        return int(text[1:], 2)
    return None


def parse_const_defs(path):
    """``{NAME: value}`` for every ``const``/``shift_const`` in a file."""
    key = ("const", str(Path(path).resolve()))
    if key in _cache:
        return _cache[key]
    out = {}
    value, inc = 0, 1
    for line in _lines(path):
        m = _CONST_DEF.match(line)
        if m:
            value = int(m.group(1)) if m.group(1) else 0
            inc = int(m.group(2)) if m.group(2) else 1
            continue
        m = _CONST_NEXT.match(line)
        if m:
            value = int(m.group(1))
            continue
        m = _CONST_SKIP.match(line)
        if m:
            value += inc * (int(m.group(1)) if m.group(1) else 1)
            continue
        m = _SHIFT_CONST.match(line)
        if m:
            out[m.group(1)] = 1 << value
            out[m.group(1) + "_F"] = value
            value += inc
            continue
        m = _CONST.match(line)
        if m:
            out[m.group(1)] = value
            value += inc
    _cache[key] = out
    return out


def parse_defs(path):
    """``{NAME: value}`` for every ``DEF NAME EQU <literal>`` in a file."""
    key = ("def", str(Path(path).resolve()))
    if key in _cache:
        return _cache[key]
    out = {}
    for line in _lines(path):
        m = _DEF.match(line)
        if not m:
            continue
        val = _literal(m.group(2))
        if val is not None:
            out[m.group(1)] = val
    _cache[key] = out
    return out


def parse_ratio_table(path, label):
    """``[(num, den), ...]`` from the ``db n, m`` rows following ``label:``.

    Stops at the first non-``db`` line, so the next table in the same file
    is never swallowed.
    """
    key = ("ratio", str(Path(path).resolve()), label)
    if key in _cache:
        return _cache[key]
    rows, seen = [], False
    for line in _lines(path):
        if not seen:
            seen = line.startswith(f"{label}:")
            continue
        m = _DB_PAIR.match(line)
        if m:
            rows.append((int(m.group(1)), int(m.group(2))))
        elif line.strip() and not line.strip().startswith(";"):
            break
    _cache[key] = rows
    return rows
