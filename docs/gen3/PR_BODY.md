# Add a Gen-3 (Sapphire / mGBA) lane beside the Crystal one

Branch: `gen3-sapphire-lane` · 258 files, +111,551 / −6

## Crystal is untouched, and that is checked rather than claimed

This branch modifies **three** files that already existed — `.gitignore`,
`README.md`, `pyproject.toml` — and nothing else. Verified mechanically
against `main`:

```
$ git diff --cached --stat HEAD -- crystalagent/ trek.py serve.py \
      autopilot.py tests/unit tests/integration
(empty)
```

`crystalagent/`, the Crystal `trek.py`/`serve.py`/`autopilot.py` and the whole
existing test suite are byte-identical to `main`. The −6 deletions are the
replaced lines of those three shared files.

## Why a lane instead of a merge

The generations share no imports. They meet only at `pokeagent/adapters/`,
which is the thing that keeps Sapphire work from breaking Crystal.

| Lane | Game | Emulator | Code | Docs |
|---|---|---|---|---|
| Gen 2 | Crystal | PyBoy | `crystalagent/` | `README.md` |
| Gen 3 | Sapphire | mGBA | `pokeagent/` | `docs/gen3/` |

`docs/gen3/MERGING.md` measures the relationship file by file instead of
asserting it: five of the eight modules this repo ports from `crystalagent/`
are **byte-identical**, including the 567-line map/BFS layer, and the three
that drifted did so to support **pokered (Gen 1)** — not Gen 3:

- `emu.py` — pokecrystal calls the tilemap `wTilemap`, pokered `wTileMap`.
- `menus.py` — pokered maps `$ed` twice in `charmap.asm`, so Red's ordinary
  cursor decodes as `▲`; matching all three glyphs is a no-op for Crystal.
- `state.py` — the battle-status bits were parsed at *module scope*, so
  importing the module required the disassembly on disk.

Those three are independently useful to Crystal and are listed in MERGING.md
as the cherry-pick set if you would rather take them alone.

**There is deliberately no Gen-2 battle driver in the new lane.**
`Gen2Adapter.CAPABILITIES` omits `battle` and an integration test asserts that
absence, so the claim cannot quietly become false. Crystal's `battle.py` and
`tactics.py` remain yours and are unaffected.

## What lands

```
pokeagent/            Gen-3 library: emulator, nav, battle, dex, menus, storage
  trek.py             the Gen-3 Driver: goto / talk_to / fight / catch / travel
  serve.py            NDJSON on stdin/stdout, one decision per line
  autopilot.py        the same pipe driven by an external decider
  adapters/           the only place the generations meet
  gen2/               port of crystalagent/, no Gen-3 imports
widget/poke.run/      Omarchy panel: live frame, party, dex, narration
scripts/              Gen-3 drivers, chains and one-off legs
tests/gen3/           776 unit tests; emulator scenarios marked `integration`
docs/gen3/            AGENTS.md (binding), MERGING.md, dex plans
data/dex/             vendored dex dataset, source commit in SOURCE.txt
pret                  submodule, pinned: the pokeruby decompilation
```

The widget reads a *published feed* rather than the emulator, so it works with
whichever lane is running.

## The one real incompatibility, surfaced not hidden

PyBoy publishes wheels for Python ≥3.12; the `mgba` wheels are built per
CPython minor and this tree's savestates are pinned to **3.11**. A savestate is
a whole machine state coupled to the core that wrote it, so this cannot be
floated without invalidating every `.state` on disk.

So neither emulator is a hard dependency any more:

```sh
uv pip install -e '.[gen2]'   # Crystal / Red, PyBoy, needs >=3.12
uv pip install -e '.[gen3]'   # Sapphire, mGBA, needs 3.11
```

`requires-python` widens to `>=3.11,<3.14` to admit either. Installing both in
one environment is unsupported and unnecessary — the lanes never import each
other. `testpaths` still collects both suites, and each skips itself when its
own build artifacts are absent.

## Verification

- **776 unit tests pass** in the Gen-3 lane (`tests/gen3/`), with no savestate
  and no ROM beyond the built symbol map.
- Every added `.py` file parses (258-file sweep, 0 failures).
- `./sapphire --help`, `python -m pokeagent.serve --help` and
  `python -m pokeagent.autopilot --help` all start.
- The Crystal lane is byte-identical to `main`, shown above.

## No ROM

No ROM is included and none ever will be. `scripts/build_rom.sh` reproduces
`pokesapphire_rev2` from the pinned `pret` submodule, which is what produces
the symbol map every read depends on.

## Honest state of the Gen-3 work

It plays Sapphire end to end: 8/8 badges, the Elite Four repeatable in one
process, and a live desktop feed. The Pokédex is **not** complete — 97/178
owned at the time of this branch, with 16 unreachable by design (version
exclusives, trade evolutions, events). The remaining blockers are written up
with `file:line` citations in `docs/gen3/DEX_GAP.txt` rather than left as
folklore, including the ones that were disproved by measurement and reverted.
