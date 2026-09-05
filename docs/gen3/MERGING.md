# Merging this back upstream without breaking Crystal

Upstream is [`crystal-omp-agent`](https://github.com/<upstream>/crystal-omp-agent):
a headless Pokémon Crystal driver for LLM agents, built on PyBoy and the
pokecrystal disassembly's own build artifacts. This repository started as a
copy of it and grew a Gen-3 (Sapphire, mGBA) implementation beside the Gen-2
one, plus an Omarchy desktop widget.

The promise being kept here is that **Crystal keeps working**. This document
says exactly how the two code bases relate right now, what a merge would have
to reconcile, and which of the changes made here upstream actually wants.

Read `AGENTS.md` for the harness rules and `DECISIONS.md` for why the
generation split looks the way it does.

## The honest state of the fork

`pokeagent/gen2/` is a **port of `crystalagent/`, not a shared library**. It
does not import from the Gen-3 modules and the Gen-3 modules do not import
from it; the two meet only at `pokeagent/adapters/`. That is what makes
Crystal safe from Sapphire work, and it is also the thing that will rot if
nobody measures it.

Measured today, file by file:

| Module | Upstream | Here | Differing lines |
|---|---|---|---|
| `asmconst.py` | 136 | 136 | 0 |
| `charmap.py` | 83 | 83 | 0 |
| `names.py` | 54 | 54 | 0 |
| `nav.py` | 567 | 567 | 0 |
| `symfile.py` | 42 | 42 | 0 |
| `menus.py` | 391 | 398 | 9 |
| `emu.py` | 266 | 287 | 21 |
| `state.py` | 257 | 289 | 45 |

Five of eight are byte-identical, including the 567-line map and BFS layer --
the most valuable and most intricate code in either repository. Regenerate
that table before believing it:

```sh
for f in asmconst charmap emu menus names nav state symfile; do
  diff -u ../crystal-omp-agent/crystalagent/$f.py pokeagent/gen2/$f.py \
    | grep -c '^[+-][^+-]'
done
```

**Not ported at all** (11 modules, all Crystal-specific or superseded):
`battle.py`, `cli.py`, `decide.py`, `hookevents.py`, `live.py`,
`missables.py`, `paths.py`, `registry.py`, `rolling.py`, `schemas.py`,
`tactics.py`.

The consequence is blunt and should not be papered over: **there is no Gen-2
battle driver here.** `Gen2Adapter.CAPABILITIES` omits `battle`, and
`tests/integration/test_gen2_live.py` asserts that absence so the claim cannot
quietly become false. Anything upstream does in `crystalagent/battle.py` and
`tactics.py` is untouched by this repository and unaffected by a merge.

## What drifted, and which way it should flow

All three drifted files changed for the same reason -- **supporting pokered
(Gen 1) alongside pokecrystal** -- and none of it is Gen-3 contamination.
Every one of these is a bug fix upstream would benefit from:

1. **`emu.py`: the tilemap symbol.** pokecrystal calls it `wTilemap`, pokered
   calls it `wTileMap`. One capital letter, and it is the difference between a
   decoded screen and a `KeyError`. Resolved once against whichever symbol
   table is loaded, so a game with neither name fails loudly instead of
   reading address 0. **Harmless to Crystal** -- `wTilemap` still resolves
   first.

2. **`menus.py`: a third cursor glyph.** `CURSORS` gained `▲`, because
   pokered maps `$ed` twice in `constants/charmap.asm` -- to the town-map up
   arrow at line 85 and to `▶` at line 177 -- and the parser keeps the first,
   so Red's ordinary NEW GAME cursor decodes as `▲`. pokecrystal has no such
   collision (`▲` is `$61` there), so **matching all three is a no-op for
   Crystal** and finds the cursor in either game without the menu driver
   needing to know which game it is looking at.

3. **`state.py`: two dependencies cut.** The battle-status bits were being
   parsed out of `constants/battle_constants.asm` at import time, and
   `schemas` was a hard import. Both are now resolved defensively.

That third one deserves its own heading, because it is the change upstream
most needs.

### The dependency worth taking: stop demanding a parent checkout

Upstream's `crystalagent/paths.py` computes:

```python
TOOL_DIR  = Path(__file__).resolve().parents[1]   # crystal-agent/
REPO_ROOT = TOOL_DIR.parent                       # pokecrystal/
```

The harness must therefore be **a direct child of a pokecrystal checkout**.
`CRYSTAL_ROM` and `CRYSTAL_SYM` can relocate the ROM and symbols, but
`CHARMAP` and `MAP_CONSTANTS` have no override, so the `.asm` sources cannot
move. `state.py` then parsed `battle_constants.asm` at module scope, which
turned a missing checkout into an **import-time** failure: 45 of 49 unit
files failed to import, and even the pure-logic tests could not run.

That is not hypothetical. It is exactly what happened when this repository was
cloned on its own -- nothing ran, including tests that touch no game data.

Here the decompilation lives at `decomp/pokecrystal/` inside the project, and
every path is an explicit, overridable variable that is checked at the
boundary rather than assumed. **Recommended upstream regardless of whether
anything else merges**: it costs one file, breaks no existing layout (a
checkout that is already a child of pokecrystal keeps working), and it is the
difference between "clone and run the tests" and "clone, then discover the
tool needs a 400 MB sibling".

## Three ways to land this, and the tradeoffs

**Option A -- upstream adopts the adapter layer.** `crystalagent/` becomes
`pokeagent/gen2/`, `pokeagent/adapters/` arrives, Gen 3 comes along. One repo,
one test suite, no drift by construction.
*Cost*: upstream inherits mGBA, a vendored `libmgba`, a Qt widget and a much
larger dependency surface, to gain generations it may not want. The name stops
matching the contents.

**Option B -- cherry-pick the fixes, stay separate.** Take the three drifted
files and `paths.py`; leave the Gen-3 work here.
*Cost*: the fork keeps drifting, and every future Gen-2 fix has to be applied
twice by hand.
*This is the recommended default.* The changes worth taking are small,
self-contained, independently valuable, and all of them make upstream more
robust rather than more general.

**Option C -- extract a shared core package.** Both repos depend on a
`pokeagent-core` holding the emulator wrapper, charmap, symbol parsing and
BFS.
*Cost*: a third repository, a release process, and version skew between three
things instead of drift between two. Only worth it if Gen 1/2/4 work becomes
sustained rather than occasional.

## What would actually break Crystal, and what catches it

Not theory -- these are the coupling points that exist today:

- **Shared `pokeagent/paths.py`.** Both generations resolve artifacts through
  it. A Sapphire-shaped change to path resolution reaches Gen 2 directly. It
  is the only shared import `pokeagent/gen2/` has, and keeping that number at
  one is worth defending.
- **The adapter contract.** `base.Backend` is the surface both generations
  implement. Adding a required attribute for Sapphire silently breaks Gen 2
  unless Gen 2 gains it too.
- **`pokeagent/schemas.py`.** Gen 2's `state.py` validates through the Gen-3
  schemas, behind a `try/except ImportError` so a tightened schema degrades to
  an unvalidated read rather than an exception. Tightening a field Crystal
  populates differently is a live risk.
- **Savestates are version-coupled**, to both the ROM hash and the exact
  emulator build. `pyboy==2.7.0` is pinned for the same reason mGBA is
  vendored. A floating pin invalidates every `.state` on disk.

The lane that catches all of it:

```sh
.venv/bin/python -m pytest tests/integration/test_crystal_regression.py -m integration
```

Five scenarios, about six seconds, on the bundled ROM. It drives a power-on
cartridge through its own intro into the overworld and then **walks** --
because booting only exercises the ROM and the screen decode, while navigation
is where the game-specific assumptions actually live. It also opens Sapphire
and Crystal in one process and checks neither displaces the other's reads,
which is the concrete shape a shared-cache bug would take.

`test_gen2_live.py` (10 more scenarios) covers boot, screen decode, the menu
cursor in both Crystal and Red, and the absent battle driver.

**Run both before proposing anything upstream.** A merge request that has not
walked Crystal has not tested Crystal.

## Practical merge recipe (Option B)

1. Take `paths.py` first, on its own. It is independently valuable and
   unblocks running the test suite from a bare clone.
2. Take the `state.py` import-time fix with it -- module-scope `.asm` parsing
   is the other half of the same bug.
3. Take `emu.py`'s tilemap resolution and `menus.py`'s third cursor glyph only
   if upstream wants pokered support. They are no-ops for Crystal, but a no-op
   still needs a reason to exist.
4. Leave `nav.py`, `asmconst.py`, `charmap.py`, `names.py` and `symfile.py`
   alone. They are byte-identical; there is nothing to merge.
5. Re-run upstream's own lanes (`pytest tests`, then `pytest -m integration`
   against `claude_saves/` milestones). Upstream's integration lane forks
   milestone savestates into a temp path and re-hashes on teardown, so it
   proves it did not mutate them.

## What this repository owes upstream that is not code

Bugs found by playing here that upstream shares, all in
`FUCK_I_MESSED_UP.md`-style detail in `PROGRESS.md`:

- **A struct stride read from a C header rather than the linker map.**
  `include/pokemon.h` declares `struct Evolution` as three `u16`s = 6 bytes;
  the real array is padded to 8. Confidently produced "Shedinja: evolve
  REGIROCK at level 20". The same class of error hit `struct BattleMove`
  (header 9, real 12) and gave every move zero power. **Derive strides from
  the symbol's own size**; upstream reads Gen-2 structs the same way.
- **Any menu that re-arms itself turns a blind A-loop into a repeat-action
  loop.** Upstream already documents this for Bill's PC (gotcha 18) and the
  mart (gotcha 13). It recurred here twice more: a shop buying one item per
  press, and a party summary screen that froze a run for fifteen minutes with
  field controls locked. The general rule is worth promoting from three
  worked examples to a stated invariant.

### Movement-model findings (Gen 3 measured, the CLASS is generation-agnostic)

Every one of these was found the expensive way -- a loop walking at something
for half an hour -- and every one is a rule about the ENGINE that a faithful
decode does not supply. Upstream's Gen-2 nav does not need the elevation rules
(Crystal has no elevation), but the *shape* of the mistake is the transferable
part, and the last two apply verbatim.

- **A faithful decode can still be wrong about the game, and it looks like
  evidence.** `grid_drift()` compared the static `.blk` grid against the
  engine's own live block map and reported ZERO drift on a map the walker
  could not cross. On the strength of that reading a whole objective chain was
  rewritten around a SURF requirement that does not exist. When the model and
  the game disagree about walkability, the model is not exonerated by matching
  its own source data.
- **Elevation 0 is a transition; 15 is a bridge.** `ObjectEventUpdateZCoord`
  (`src/event_object_movement.c:7586-7598`) takes the tile's elevation unless
  either the current or previous tile is 0xF. So standing on an elevation-0
  tile makes you elevation 0, and `IsZCoordMismatchAt` (`:7528`) then admits
  you to ANY level -- that is the only mechanism by which the player changes
  elevation. Treating 0 like 15 sealed every level change in Hoenn.
- **Elevation belongs in the BFS closed set.** Keyed on `(x, y)`, an
  elevation-15 bridge cell -- which accepts any level and preserves it -- is
  closed by whichever wave reaches it first, severing the road it carries. A
  cell visited at one elevation is not the same state as that cell at another.
  Any harness that adds a per-tile movement dimension inherits this bug.
- **One tile, three warp mechanisms.** Doors are solid and fire on the
  entering step; contact warps (Lavaridge's springs, cracked floors, Mt.
  Pyre's holes) are walkable and fire on arrival; some warps are map EDGES
  that fire on the step *off* them. And Petalburg's gym doors are
  `bg_event` scripts on the warp cells that answer only the A button. A
  single `take_warp` cannot cover these; ours dispatches and falls through.
- **A body standing on a warp is not a solid door.** Lavaridge parks a
  trainer on a hot-spring tile; the router chose that hole every time because
  it was nearest, and no entry step could ever complete. Live object positions
  must exclude a warp from the usable set -- distinct from the collision that
  makes every door solid.
- **When the model runs out, ask the emulator.** `Driver.solve_warp_maze`
  forks savestates, steps on every reachable in-maze warp, BFSes over
  OBSERVED outcomes, and replays the winning sequence on the real timeline.
  It solved a gym that had defeated four successive static models in 15
  states. Upstream has the same primitive available (savestate determinism is
  its own headline feature) and Crystal has at least one comparable puzzle.
- **A search must never be able to redirect writes to the real timeline.**
  `Driver.load()` repointing `state_path` plus a solver that loads scratch
  forks meant every periodic save went to `/tmp` for hours. The run kept
  advancing and kept NOT being saved; a later restart replayed from two
  badges earlier. Upstream's `load()` has the same signature and the same
  hazard -- `load(..., adopt=False)` here is the fix.
- **A give-up that only one code path consults is not a give-up.** The travel
  objective honoured `_travel_given_up`; the story path called `head_for`
  unconditionally and logged *8125* identical failures against one wall.
- **Stall detectors must contain nothing that advances on its own.** Ours
  keyed on `(map, pos, battles, badges)`; the battle counter ticks during
  exactly the fights worth interrupting, so hundreds of dead turns read as
  progress. The signature is now position, badges, money and per-mon
  level/HP -- all of which freeze when the run does.

## Assets and third-party data: decisions for the maintainer

Nothing derived from the cartridge is committed here, and the history is clean
of it (verified: no ROM, savestate, symbol map or token blob has ever been
added on any branch). Three things need a decision that is properly yours
rather than ours:

1. **`data/dex/*.json` (~1.1 MB, vendored).** The dex objective
   (`pokeagent/dex.py`, `acquire.py`, `living.py`) depends on a regional-dex
   dataset. `data/dex/SOURCE.txt` records its exact provenance -- a commit
   hash and a repository URL -- but that repository is **private and not
   reachable from a clone of this fork**, and we cannot state its licence.
   Options: vendor it as-is (current state, works out of the box), replace it
   with a fetch step, or derive the same tables from the decomp (feasible --
   `docs/guide/*.json` is already generated that way by
   `scripts/build_guide.py`, which would remove the dependency entirely). We
   would take the third option given time; flagging rather than choosing.
2. **Generated art is untracked on purpose.** The 384 party sprites and the
   Torchic mark are Pokemon art extracted from the decomp's graphics tree.
   They are regenerated by committed scripts (`widget/make_sprites.py`,
   `widget/make_logo.py`), and `widget/install.sh` already prints the exact
   command when they are absent rather than installing a silently logo-less
   widget. If you would rather ship them, that is a licensing call, not a
   technical one. Documentation screenshots under `docs/` are kept, since
   they document this project's UI rather than distributing game assets --
   strip them too if you disagree.
3. **`vendor/` (libmgba) is untracked.** It is an extracted Arch package,
   reproducible via `scripts/vendor_libmgba.sh`. Prefer the system package;
   the vendored copy exists only because this machine's `python-mgba` could
   not find a matching shared library.

`pret/` is a proper submodule pinned to `pret/pokeruby` at
`63a8cbf0016b351a4e68f7036fa0b77e23d2f2c1`, mirroring upstream's relationship
to `pokecrystal`. It is never vendored.
