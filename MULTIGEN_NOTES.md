# Notes from porting this harness to Gen 3

This harness was built for Pokémon Crystal. A port to Pokémon Sapphire
(pokeruby + mGBA) has been running for a while, and it turned up two defects
that are **not** Gen-2 specific and that this PR fixes. Everything else the
port needed is generation-specific and stays out of here.

## What this PR changes, and why it is safe

**1. `crystalagent/paths.py` — `REPO_ROOT` is overridable.**

```python
REPO_ROOT = Path(os.environ.get("CRYSTAL_REPO", TOOL_DIR.parent))
```

The default is unchanged, so an existing checkout behaves exactly as before.
The reason it matters is that the decompilation is a **runtime** dependency,
not merely a build-time one: `charmap.asm`, `map_constants.asm`,
`battle_constants.asm` and the map data are parsed on demand. With the path
pinned to `TOOL_DIR.parent`, a checkout anywhere other than inside a
pokecrystal clone cannot use the package at all — and could not be told
otherwise, because there was no environment variable to set.

**2. `crystalagent/state.py` — the battle constants are parsed at first use.**

`_STATUS_BITS` and `SLP_MASK` were computed at module scope, so
`import crystalagent.state` read the disassembly. Combined with (1) that meant
a bare clone could not **collect** the test suite, let alone run it:

```
$ pytest tests            # on an unmodified checkout, outside pokecrystal
!!!!!! Interrupted: 43 errors during collection !!!!!!
```

With the two changes, pointing `CRYSTAL_REPO` at a plain `pret/pokecrystal`
clone (no build):

```
$ CRYSTAL_REPO=/tmp/pokecrystal pytest tests
653 passed, 31 skipped, 16 deselected, 7 errors
```

The 7 remaining errors are `pokecrystal.sym` not existing — those tests need a
**built** ROM, which is a toolchain matter and unrelated to this change.

`_STATUS_BITS` and `SLP_MASK` are still module attributes, via PEP 562
`__getattr__`, because `tests/unit/test_parser_values.py` reads them directly.
Same numbers, same source; only the timing changes. That test passes.

## Bugs found by playing, that Gen 2 shares

These cost real time in the port and the *class* of each applies here.

**A struct stride read from a C header rather than from the linker map.**
`include/pokemon.h` declares `struct Evolution` as three `u16`s — 6 bytes. The
real array is padded to 8. Reading it at stride 6 produced, confidently,
"Shedinja: evolve REGIROCK at level 20". The same mistake hit `struct
BattleMove` (header says 9, real 12) and gave *every move zero power*, which
silently made the battle maths choose at random for hours. The fix is to derive
strides from the symbol's own size and refuse when it does not divide evenly.
This harness reads Gen-2 structs the same way.

**Any menu that re-arms itself turns a blind A-loop into a repeat-action loop.**
Already documented here for Bill's PC (gotcha 18) and the mart (gotcha 13). It
recurred twice more in Gen 3: a shop that bought one item per press, and a
move-selection screen where a blind A sent whatever the cursor happened to sit
on — which was slot 0, a move with no PP, once per turn for a whole battle.
Worth promoting from three worked examples to a stated invariant: **never press
A into a menu you have not read.**

**"Owning an HM" is not "being able to use it."** The field-move permission is a
badge check, not an inventory check. Gating a route on the item sent a run
walking across the map twice for something a single hop would have reached.
The same distinction exists in Gen 2.

**A savestate search is the honest escape hatch when a static model is wrong.**
Two Gen-3 puzzles have no representation in the block map at all — rotating
gates in Fortree's gym are neither metatiles nor object events, so a decoded
grid reports the room fully connected while the first step is refused. Rather
than model them, the emulator became the transition function: a node is
(position, puzzle state), the moves are the four steps, and a best-first search
over savestates replays the winning sequence on the real timeline. Crystal has
the same shape of problem in the Ice Path and the Rocket base.

## Where the Gen-3 work lives

It is a sibling package (`pokeagent/`) plus its own driver, so
`crystalagent/` is untouched by it — this repository's Crystal support keeps
working, which was the condition the port held itself to. This PR is
deliberately only the two generation-agnostic fixes; the rest is not proposed
here.
