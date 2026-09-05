#!/usr/bin/env bash
# Build pokesapphire_rev2.gba + its symbol table from the pret decompilation.
#
# The ROM this produces is byte-identical to a real Sapphire (Rev 2, US)
# cartridge dump -- that is the whole point. We do NOT ship or use the built
# ROM to play; we build it so that `pokesapphire_rev2.sym` (50k symbols:
# address, binding, size, name) describes the user's own dump exactly.
#
# This is the Sapphire analog of pokecrystal's `pokecrystal.sym`, and it is
# what lets the harness read game state by NAME instead of by magic address.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PRET="$ROOT/pret"
JOBS="${JOBS:-$(nproc)}"

# Prefer a real system toolchain; fall back to ./vendor (see vendor_toolchain.sh).
if ! command -v arm-none-eabi-cpp >/dev/null 2>&1; then
  export PATH="$ROOT/vendor/bin:$PATH"
fi
for prog in arm-none-eabi-as arm-none-eabi-ld arm-none-eabi-objdump arm-none-eabi-cpp; do
  command -v "$prog" >/dev/null || { echo "missing $prog -- run scripts/vendor_toolchain.sh"; exit 1; }
done

# agbcc: the only from-source dependency. pokeruby needs this exact ancient
# compiler to reproduce the original code generation byte for byte.
if [ ! -x "$PRET/tools/agbcc/bin/agbcc" ]; then
  echo ":: bootstrapping agbcc"
  [ -d "$ROOT/agbcc" ] || git clone --depth 1 https://github.com/pret/agbcc "$ROOT/agbcc"
  ( cd "$ROOT/agbcc" && ./build.sh && ./install.sh "$PRET" )
fi

echo ":: building sapphire_rev2 (-j$JOBS)"
make -C "$PRET" -j"$JOBS" GAME_VERSION=SAPPHIRE GAME_REVISION=2 COMPARE=1 all syms

# COMPARE=1 already ran sha1sum -c against pret/sapphire_rev2.sha1. Cross-check
# the user's own dump too: if these differ, every symbol address is a lie.
if [ -f "$ROOT/pokesapphire.gba" ]; then
  cmp "$PRET/pokesapphire_rev2.gba" "$ROOT/pokesapphire.gba" \
    && echo ":: your ROM matches the build -- symbols are valid for it" \
    || { echo "!! your ROM is NOT sapphire rev2; symbols would be wrong" >&2; exit 1; }
fi

ln -sfn pret/pokesapphire_rev2.sym "$ROOT/pokesapphire_rev2.sym"
echo ":: $(wc -l < "$PRET/pokesapphire_rev2.sym") symbols -> pokesapphire_rev2.sym"
