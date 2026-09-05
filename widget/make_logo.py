#!/usr/bin/env python3
"""Regenerate the widget's Torchic mark from the decomp's own graphics.

WHY this is a script and not a one-off in an image editor: the mark is *derived*
art. Its source of truth lives in the decomp at
``pret/graphics/pokemon/<species>/`` and it has to be re-derivable -- for a
different species, for a checkout at a different commit, or simply to prove that
nobody hand-touched a pixel. Running this twice produces byte-identical files.

Both emitted stills come from ``front.png``, the 64x64 battle sprite: it is the
only Torchic art in the decomp with enough drawn pixels to still read at 32px.
``icon.png`` -- the 32x64 party icon, i.e. the two frames the games flip between
forever (``src/pokemon_icon.c:963``, ``sMonIconAnims``) -- was tried as the
animation source and rejected on the evidence: only 14x19 of its 32x32 field is
drawn, so in a 16-27px bar slot it collapses into mush, and its two frames
differ across 121 pixels, which reads as a flicker rather than as breathing.

So ``<slug>.gif`` is the battle sprite with a slow sinusoidal hop instead. The
idiom is still the game's -- Gen 3 bobs its party icons continuously, which is
what ``sMonIconAnims`` is doing -- but the motion is generated here, one whole
sprite pixel each way on a 2x-upscaled copy, so every frame stays exactly
opaque-or-absent. See ``build_gif`` for why that matters.

Keying the background
---------------------
These are indexed PNGs and **palette index 0 is the transparent one**: the GBA
never draws colour 0 of a sprite's palette, so pret parks a loud unused colour
there (``#31a452`` green for front.png, ``#629c83`` for icon.png). The correct
key is therefore *the index*, never the colour. Keying by colour would delete
any pixel that happened to be drawn in the same green -- Torchic has none, but
Treecko, Sceptile and Rayquaza very much do, and this script is meant to work
for all 386.

Usage::

    .venv/bin/python widget/make_logo.py                 # torchic
    .venv/bin/python widget/make_logo.py --species mudkip

Outputs land in ``docs/logo/`` (the auditable originals) and are copied into
``widget/poke.run/`` (what the installed plugin loads). Panel.qml names
``torchic.gif`` / ``torchic-32.png`` explicitly; point its ``markGif`` /
``markStill`` properties at another slug if you regenerate for another species.
"""

from __future__ import annotations

import argparse
import math
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops

# The idle hop. BOB_SCALE upscales the sprite by a whole number first so the hop
# can move in half-source-pixel steps while still never resampling; BOB_AMPLITUDE
# is in that upscaled space, so 2 means one real sprite pixel each way. Eight
# frames at 150ms is a 1.2s cycle: slow enough to read as breathing rather than
# as a jitter, and small enough that the whole GIF stays a few kilobytes.
BOB_SCALE = 2
BOB_AMPLITUDE = 2
BOB_FRAMES = 8
BOB_FRAME_MS = 150

# Sizes emitted from the battle sprite.
STILL_SIZES = (32, 64)

# The plugin directory the assets are copied into. Kept next to the QML that
# loads them so the installed plugin is self-contained: ~/.config/omarchy/
# plugins/poke.run/ must work even if this checkout is later moved or deleted.
PLUGIN_DIR = "widget/poke.run"


@dataclass(slots=True)
class Keyed:
    """An RGBA sprite plus the tight bounding box of its opaque pixels."""

    image: Image.Image
    bbox: tuple[int, int, int, int]


def key_index_0(path: Path) -> Keyed:
    """Load an indexed pret sprite and make palette index 0 transparent.

    Raises rather than guessing: an unexpectedly non-indexed PNG means the
    decomp changed format, and silently keying by colour instead would produce
    art with holes in it that nobody would notice until it shipped.
    """
    src = Image.open(path)
    if src.mode != "P":
        raise SystemExit(
            f"{path}: expected an indexed (mode P) PNG, got mode {src.mode!r}. "
            "pret sprites are indexed and index 0 is the transparent entry; "
            "without the index there is no safe way to key the background."
        )
    # tobytes() on a mode-P image is the raw index plane, one byte per pixel, in
    # row-major order with no padding -- exactly what we want to threshold.
    indices = src.tobytes()
    alpha = Image.frombytes("L", src.size, bytes(0 if i == 0 else 255 for i in indices))
    bbox = alpha.getbbox()
    if bbox is None:
        raise SystemExit(f"{path}: every pixel is palette index 0; nothing to draw.")
    rgba = src.convert("RGB")
    rgba.putalpha(alpha)
    return Keyed(rgba.convert("RGBA"), bbox)


def squared(image: Image.Image, bbox: tuple[int, int, int, int]) -> Image.Image:
    """Tight-crop to `bbox`, then centre it on the smallest transparent square.

    The decomp sprites sit in a 64x64 or 32x32 field with the art bottom-aligned
    and a wide margin, which at bar-icon sizes would spend most of the pixels on
    nothing. Cropping and re-centring is what makes a 20px mark legible.
    """
    crop = image.crop(bbox)
    side = max(crop.width, crop.height)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(crop, ((side - crop.width) // 2, (side - crop.height) // 2))
    return canvas


def _unpremultiply(image: Image.Image) -> Image.Image:
    """Undo premultiplication, in place over the output's bytes.

    Only ever called on the final small image (32x32 or 64x64), so the Python
    loop is a few thousand iterations.
    """
    data = bytearray(image.tobytes())
    for i in range(0, len(data), 4):
        a = data[i + 3]
        if a == 0:
            data[i] = data[i + 1] = data[i + 2] = 0
            continue
        if a == 255:
            continue
        for c in range(3):
            data[i + c] = min(255, (data[i + c] * 255 + a // 2) // a)
    return Image.frombytes("RGBA", image.size, bytes(data))


def resample(image: Image.Image, size: int) -> Image.Image:
    """Resize a square sprite to `size` by exact area-averaging.

    Pillow's RGBA resize interpolates the colour channels of fully transparent
    pixels -- which the decomp stores as black -- straight into the edges, so a
    naive resize gives the sprite a dark halo. Premultiplying first makes the
    filter linear in the right space; the alpha channel then carries the
    coverage and the colour comes back out afterwards.

    The supersample factor is chosen so `side * factor` is a whole multiple of
    `size`, which turns the reduction into an exact box filter over the original
    pixel grid instead of an approximation of one.
    """
    side = image.width
    if side != image.height:
        raise SystemExit(f"resample expects a square sprite, got {image.size}")

    # ImageChops.multiply is (a * b) / 255 with rounding, i.e. premultiplication.
    r, g, b, a = image.split()
    premultiplied = Image.merge(
        "RGBA",
        (
            ImageChops.multiply(r, a),
            ImageChops.multiply(g, a),
            ImageChops.multiply(b, a),
            a,
        ),
    )

    factor = size // _gcd(side, size)
    big = premultiplied.resize((side * factor, side * factor), Image.NEAREST)
    small = big.resize((size, size), Image.BOX)
    return _unpremultiply(small)


def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a


def build_gif(square: Image.Image, out: Path) -> tuple[tuple[int, int], int]:
    """Write the sprite as a looping GIF that hops gently in place.

    Everything here is integer NEAREST scaling and whole-pixel translation, on
    purpose. GIF has no alpha channel -- only a single fully transparent palette
    index -- so a half-covered edge pixel would have to be rounded to opaque or
    to gone, and the mark would wear a jagged fringe that changed shape every
    frame. Keeping every pixel exactly opaque or exactly absent is the only way
    the format can carry this art faithfully. The smooth downscale to bar size
    then happens on the GPU at draw time, where there is a real alpha channel to
    do it in.

    The canvas is grown by the hop amplitude on all four sides so the sprite
    never clips against the top or bottom of its own frame.
    """
    scaled = square.resize(
        (square.width * BOB_SCALE, square.height * BOB_SCALE), Image.NEAREST
    )
    side = scaled.width + 2 * BOB_AMPLITUDE
    offsets = [
        round(BOB_AMPLITUDE * math.sin(2 * math.pi * i / BOB_FRAMES))
        for i in range(BOB_FRAMES)
    ]

    canvases = []
    for offset in offsets:
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        canvas.paste(scaled, (BOB_AMPLITUDE, BOB_AMPLITUDE + offset))
        canvases.append(canvas)

    palette_frames = _to_shared_palette(canvases)
    palette_frames[0].save(
        out,
        save_all=True,
        append_images=palette_frames[1:],
        duration=BOB_FRAME_MS,
        loop=0,
        transparency=0,
        # Clear the canvas before each frame. The sprite moves, so leaving the
        # previous frame underneath would smear it into a two-headed bird.
        disposal=2,
        optimize=False,
    )
    return palette_frames[0].size, len(palette_frames)


def _to_shared_palette(frames: list[Image.Image]) -> list[Image.Image]:
    """Quantise binary-alpha RGBA frames onto one palette with index 0 clear.

    Pillow's own quantiser would pick a different palette per frame and would
    happily spend an entry on a colour that is actually the transparent hole, so
    the palette is built by hand from the colours the sprite actually uses.
    """
    colours: set[tuple[int, int, int]] = set()
    for frame in frames:
        data = frame.tobytes()
        for i in range(0, len(data), 4):
            a = data[i + 3]
            if a == 255:
                colours.add((data[i], data[i + 1], data[i + 2]))
            elif a != 0:
                raise SystemExit(
                    "GIF frames must have binary alpha; found alpha "
                    f"{a} after integer scaling, which means something "
                    "resampled the sprite."
                )
    ordered = sorted(colours)
    if len(ordered) > 255:
        raise SystemExit(f"{len(ordered)} colours; only 255 fit beside the clear index")

    lut = {colour: i + 1 for i, colour in enumerate(ordered)}
    # Index 0 is the transparent entry. Its RGB is never drawn, but black keeps
    # a viewer that ignores the transparency flag from flashing a stray colour.
    palette = [0, 0, 0] + [component for colour in ordered for component in colour]
    palette += [0] * (768 - len(palette))

    out = []
    for frame in frames:
        data = frame.tobytes()
        indexed = bytearray(frame.width * frame.height)
        for pixel in range(frame.width * frame.height):
            i = pixel * 4
            if data[i + 3] == 255:
                indexed[pixel] = lut[(data[i], data[i + 1], data[i + 2])]
        image = Image.frombytes("P", frame.size, bytes(indexed))
        image.putpalette(palette)
        out.append(image)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--species",
        default="torchic",
        help="decomp graphics directory name (default: torchic)",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="repository root holding pret/ and docs/ (default: this checkout)",
    )
    args = parser.parse_args(argv)

    repo: Path = args.repo.resolve()
    graphics = repo / "pret" / "graphics" / "pokemon" / args.species
    if not graphics.is_dir():
        raise SystemExit(
            f"{graphics} does not exist. The pret submodule holds the sprites; "
            "check the species slug and that pret/ is checked out."
        )

    out_dir = repo / "docs" / "logo"
    out_dir.mkdir(parents=True, exist_ok=True)
    plugin_dir = repo / PLUGIN_DIR
    if not plugin_dir.is_dir():
        raise SystemExit(f"{plugin_dir} does not exist; nothing to install the mark into")

    written: list[Path] = []

    front = key_index_0(graphics / "front.png")
    square = squared(front.image, front.bbox)
    for size in STILL_SIZES:
        path = out_dir / f"{args.species}-{size}.png"
        resample(square, size).save(path, optimize=True)
        written.append(path)
        print(f"{path.relative_to(repo)}  {size}x{size}  from front.png {front.bbox}")

    gif_path = out_dir / f"{args.species}.gif"
    gif_size, gif_frames = build_gif(square, gif_path)
    written.append(gif_path)
    print(
        f"{gif_path.relative_to(repo)}  {gif_size[0]}x{gif_size[1]}  "
        f"{gif_frames} frames @ {BOB_FRAME_MS}ms  from front.png"
    )

    for path in written:
        shutil.copyfile(path, plugin_dir / path.name)
    print(f"copied {len(written)} asset(s) into {PLUGIN_DIR}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
