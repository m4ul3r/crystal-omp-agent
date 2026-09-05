#!/usr/bin/env bash
# Fetch the host dependencies this harness needs into ./vendor, WITHOUT root.
#
# The preferred install is the real thing:
#
#     sudo pacman -S --needed libmgba arm-none-eabi-binutils arm-none-eabi-gcc rgbds
#
# but an agent driving this repo has no way to answer a sudo/polkit prompt, so
# the same official Arch packages are unpacked into ./vendor instead. Nothing
# here is built from an untrusted source: the tarballs come straight off the
# configured pacman mirror. `pokeagent/paths.py` prefers the system copy
# and only falls back to ./vendor.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$ROOT/vendor"
PKGS=(
  "extra/libmgba-0.10.5-5-x86_64.pkg.tar.zst"
  "extra/arm-none-eabi-binutils-2.47-1-x86_64.pkg.tar.zst"
  "extra/arm-none-eabi-gcc-16.2.0-1-x86_64.pkg.tar.zst"
  # rgbds builds the Gen 1/2 decompilations (pokered, pokecrystal), whose
  # .sym is the Game Boy equivalent of the GBA symbol table.
  "extra/rgbds-1.0.2+hotfix-2-x86_64.pkg.tar.zst"
)

MIRROR="$(grep -m1 '^Server' /etc/pacman.d/mirrorlist | sed 's/Server = //; s|/\$repo/os/\$arch||')"
[ -n "$MIRROR" ] || { echo "no pacman mirror configured" >&2; exit 1; }

mkdir -p "$VENDOR/cache" "$VENDOR/root"
for p in "${PKGS[@]}"; do
  repo="${p%%/*}"; file="${p##*/}"
  if [ ! -f "$VENDOR/cache/$file" ]; then
    echo ":: fetching $file"
    curl -fsSL -o "$VENDOR/cache/$file" "$MIRROR/$repo/os/x86_64/$file"
  fi
  tar --zstd -xf "$VENDOR/cache/$file" -C "$VENDOR/root" --exclude '.*' --warning=no-unknown-keyword
done

# Flatten to the layout paths.py and the build expect.
rm -rf "$VENDOR/lib" "$VENDOR/include" "$VENDOR/bin"
ln -sfn root/usr/lib "$VENDOR/lib"
ln -sfn root/usr/include "$VENDOR/include"
ln -sfn root/usr/bin "$VENDOR/bin"

echo ":: vendored into $VENDOR"
"$VENDOR/bin/arm-none-eabi-as" --version | head -1
"$VENDOR/bin/arm-none-eabi-cpp" --version | head -1
"$VENDOR/bin/rgbasm" --version | head -1
ls "$VENDOR/lib/libmgba.so.0.10" >/dev/null && echo "libmgba.so.0.10 present"
