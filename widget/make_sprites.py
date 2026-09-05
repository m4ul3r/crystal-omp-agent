#!/usr/bin/env python3
"""Emit a party sprite for every species, for the widget's party list.

Same derivation as ``make_logo.py`` and for the same reason: the art's source of
truth is the decompilation at ``pret/graphics/pokemon/<slug>/front.png``, so it
is re-derived rather than committed by hand and a different checkout produces a
different-but-correct set.

Keying is by PALETTE INDEX 0, never by colour. The GBA does not draw colour 0 of
a sprite's palette, so pret parks a loud unused value there -- ``#31a452`` green
for ``front.png``. Keying on that colour instead would punch holes in every
species drawn in the same green, which is most of the Grass line. See
``make_logo.py`` for the full note.

These are small: the widget shows them beside a name in a list, so 40px is
plenty and the whole set is well under a megabyte.

Usage::

    .venv/bin/python widget/make_sprites.py            # every species
    .venv/bin/python widget/make_sprites.py --only combusken,wingull
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from widget.make_logo import key_index_0, resample, squared  # noqa: E402

#: Where the decomp keeps its battle sprites.
SOURCE = ROOT / "pret" / "graphics" / "pokemon"

#: Installed alongside the plugin; Panel.qml resolves "sprites/<slug>.png".
OUT = ROOT / "widget" / "poke.run" / "sprites"

#: Big enough to read beside a name at the popup's text size, small enough that
#: 386 of them do not bloat the plugin.
SIZE = 40

#: Directories under graphics/pokemon that are not species.
SKIP = {"unown", "castform", "deoxys", "question_mark", "egg", "double_question_mark"}


def slug_for(name: str) -> str:
    """The decomp's directory name for a ROM species name.

    The ROM spells them for a Game Boy screen -- "NIDORAN♀", "MR. MIME",
    "FARFETCH'D" -- and pret spells them for a filesystem. Normalising to
    lowercase alphanumerics covers every Hoenn species and all but a handful
    of the national dex; the gendered Nidoran are special-cased because
    stripping their symbol would collide the two.
    """
    raw = name.strip().lower()
    if "nidoran" in raw:
        return "nidoran_f" if "\u2640" in name else "nidoran_m"
    return "".join(ch for ch in raw if ch.isalnum())


def asked_for(directory: str) -> str:
    """The filename `Model.js:spriteSlug` will actually request.

    The two names are NOT the same, and assuming they were cost Ho-Oh and
    Mr. Mime their sprites. pret spells a directory `mr_mime`; the widget sees
    the ROM's "MR. MIME", strips everything non-alphanumeric and asks for
    `mrmime.png`. Underscore-free is therefore the rule -- except the gendered
    Nidoran, which the widget special-cases precisely because stripping the
    symbol would collide them, so their underscores must survive here too.

    Named after the consumer's question rather than the producer's filesystem,
    because the consumer is the one who has to find the file.
    """
    if directory.startswith("nidoran"):
        return directory
    return directory.replace("_", "")


def build(directory: str) -> bool:
    src = SOURCE / directory / "front.png"
    if not src.exists():
        return False
    keyed = key_index_0(src)
    square = squared(keyed.image, keyed.bbox)
    OUT.mkdir(parents=True, exist_ok=True)
    resample(square, SIZE).save(OUT / f"{asked_for(directory)}.png")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", help="comma-separated slugs, for a quick rebuild")
    a = ap.parse_args(argv)

    if a.only:
        slugs = [s.strip() for s in a.only.split(",") if s.strip()]
    else:
        slugs = sorted(
            p.name for p in SOURCE.iterdir()
            if p.is_dir() and p.name not in SKIP and (p / "front.png").exists()
        )

    made = [s for s in slugs if build(s)]
    missed = [s for s in slugs if s not in made]
    total = sum(f.stat().st_size for f in OUT.glob("*.png"))
    print(f"{len(made)} sprites -> {OUT.relative_to(ROOT)} ({total // 1024} KiB)")
    if missed:
        print(f"no front.png for: {', '.join(missed[:8])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
