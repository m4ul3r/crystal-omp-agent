"""Struct field offsets, parsed from the decomp's own annotations.

The Crystal harness derived struct layouts from the symbol table
(``wPartyMon1HP - wPartyMon1``) precisely so no offset was ever retyped. Gen 3
does not put field labels in the symbol table, but pokeruby annotates every
field with its offset in a comment::

    struct SaveBlock1 /* 0x02025734 */
    {
        /*0x00*/ struct Coords16 pos;
        /*0x04*/ struct WarpData location;
        ...
        /*0x490*/ u32 money;
        /*0x1220*/ u8 flags[FLAGS_COUNT];
    };

so the offsets are still *in the source*, and we read them from there. A
transcribed constant rots silently when the decomp is updated; a parsed one
either keeps working or fails loudly.

Offsets are checked for monotonicity on parse, which catches a mis-scraped
comment immediately rather than at the point some field reads as garbage.
"""

import re
from functools import lru_cache

from . import paths

_STRUCT = re.compile(r"^\s*struct\s+(\w+)\s*(?:/\*[^*]*\*/)?\s*$")
_FIELD = re.compile(
    r"^\s*/\*\s*(0[xX][0-9a-fA-F]+|\d+)\s*\*/\s*"          # /*0x490*/
    r"(?:(?:const|volatile|struct|union|unsigned|signed)\s+)*"
    r"\w+"                                                  # the type name
    # Pointer stars and any `const` between them, e.g.
    #   const union AffineAnimCmd *const *affineAnims;
    # Without this the qualifier is read as the field name and the layout
    # silently gains a field called "const" at the wrong offset.
    r"(?:\s*\*+\s*(?:const\s*)?)*\s*"
    r"(\w+)\s*(?:\[|:|;)"                                   # name, then [] : or ;
)


class StructLayout(dict):
    """``field name -> byte offset``, plus the declaring struct's name."""

    def __init__(self, name, fields):
        super().__init__(fields)
        self.struct_name = name

    def __getitem__(self, field):
        try:
            return dict.__getitem__(self, field)
        except KeyError:
            near = ", ".join(sorted(self)[:8])
            raise KeyError(
                f"struct {self.struct_name} has no annotated field {field!r} "
                f"(known: {near}...)"
            ) from None

    def at(self, base, field):
        """Absolute address of `field` given the struct's base address."""
        return base + self[field]


@lru_cache(maxsize=None)
def layout(struct_name, header="global.h") -> StructLayout:
    """Parse ``struct <struct_name>`` out of ``include/constants/../<header>``.

    Only fields carrying an ``/*0x..*/`` annotation are returned -- that is
    every field in the save blocks and the Pokemon structs, which is what the
    harness reads.
    """
    path = paths.INCLUDE / header
    text = paths.require(
        path, f"header {header}", "is the pret/ submodule checked out?"
    ).read_text(encoding="utf-8", errors="replace")

    # Brace-counted scan. A naive "a line starting with } ends the struct"
    # rule stops at the first nested anonymous union -- struct
    # PokemonSummaryScreenStruct opens with `union { ... } monList;` and the
    # whole struct then parses as empty.
    fields, inside, depth = {}, False, 0
    for line in text.splitlines():
        if not inside:
            m = _STRUCT.match(line)
            if m and m.group(1) == struct_name:
                inside, depth = True, 0
            continue
        depth += line.count("{") - line.count("}")
        if depth <= 0 and "}" in line:
            break
        m = _FIELD.match(line)
        if m:
            off, name = m.groups()
            # Bitfields share a byte, so a repeat offset is legal; keep the
            # first name at each offset and record the rest too.
            fields[name] = int(off, 16 if off.lower().startswith("0x") else 10)

    if not fields:
        raise KeyError(
            f"no offset-annotated fields found for struct {struct_name} in {header}"
        )

    # A comment that scraped wrong almost always breaks the ordering. Catch it
    # here, not three layers up where money reads as a map id.
    ordered = list(fields.items())
    for (an, ao), (bn, bo) in zip(ordered, ordered[1:]):
        if bo < ao:
            raise ValueError(
                f"struct {struct_name}: {bn} at {bo:#x} follows {an} at {ao:#x} "
                "-- offset annotations are not monotonic, parse is wrong"
            )
    return StructLayout(struct_name, fields)


def size_of(struct_name, header="global.h", last_field_size=0):
    """Upper bound on a struct's size: last annotated offset + that field."""
    lay = layout(struct_name, header)
    return max(lay.values()) + last_field_size



#: Scalar widths for the sequential parser below.
_SCALAR = {
    "u8": 1, "s8": 1, "bool8": 1, "char": 1,
    "u16": 2, "s16": 2, "bool16": 2,
    "u32": 4, "s32": 4, "bool32": 4, "int": 4,
}
_SEQ_FIELD = re.compile(
    r"^\s*(u8|s8|bool8|char|u16|s16|bool16|u32|s32|bool32|int)\s+"
    r"(\w+)\s*(?:\[\s*(\d+)\s*\])?\s*;"
)


@lru_cache(maxsize=None)
def layout_sequential(struct_name, source) -> StructLayout:
    """Offsets for a struct that carries no ``/*0x..*/`` annotations.

    Computed by walking the declared scalar fields in order with natural
    alignment. Only safe for all-scalar structs -- ``struct Menu``
    (src/menu.c:14-25) is the case that needs it, since the menu code was
    decompiled without offset comments. Anything with nested structs or
    bitfields must use :func:`layout`, which reads the real offsets.
    """
    path = paths.PRET / source
    text = paths.require(path, source, "is the pret/ submodule checked out?").read_text(
        encoding="utf-8", errors="replace"
    )
    m = re.search(
        rf"struct\s+{struct_name}\s*\{{(.*?)\n\}}", text, re.S
    )
    if not m:
        raise KeyError(f"struct {struct_name} not found in {source}")

    fields, offset = {}, 0
    for line in m.group(1).splitlines():
        f = _SEQ_FIELD.match(line)
        if not f:
            continue
        ctype, name, count = f.groups()
        width = _SCALAR[ctype]
        if offset % width:                      # natural alignment
            offset += width - (offset % width)
        fields[name] = offset
        offset += width * (int(count) if count else 1)
    if not fields:
        raise KeyError(f"struct {struct_name} in {source} has no scalar fields")
    return StructLayout(struct_name, fields)