#!/usr/bin/env bash
# Clone and build a Gen 1/2 decompilation, for the same reason as the Gen-3
# build: the link step emits the .sym that lets the harness read memory by
# name instead of by magic address.
#
# Unlike the GBA build this also produces a ROM the harness can legitimately
# run, because the Game Boy games build to a byte-exact cartridge from source
# with no external compiler.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GAME="${1:-crystal}"
case "$GAME" in
  crystal) REPO=https://github.com/pret/pokecrystal; DIR=pokecrystal; TARGET=crystal ;;
  gold)    REPO=https://github.com/pret/pokegold;    DIR=pokegold;    TARGET=gold ;;
  red)     REPO=https://github.com/pret/pokered;     DIR=pokered;     TARGET=red ;;
  *) echo "unknown game $GAME (crystal|gold|red)" >&2; exit 1 ;;
esac

command -v rgbasm >/dev/null || export PATH="$ROOT/vendor/bin:$PATH"
command -v rgbasm >/dev/null || { echo "rgbds missing -- run scripts/vendor_toolchain.sh"; exit 1; }

mkdir -p "$ROOT/decomp"
[ -d "$ROOT/decomp/$DIR" ] || git clone --depth 1 "$REPO" "$ROOT/decomp/$DIR"
make -C "$ROOT/decomp/$DIR" -j"$(nproc)" "$TARGET"
echo ":: $(ls -la "$ROOT/decomp/$DIR"/*.gbc 2>/dev/null | wc -l) ROM(s) and $(wc -l < "$ROOT/decomp/$DIR/$DIR.sym") symbols"
