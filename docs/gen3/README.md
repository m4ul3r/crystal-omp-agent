# sapphire-omarchy-widget

Drive **Pokémon Sapphire** headlessly, built for agents — plus an Omarchy bar
widget that shows the run live.

This is a port of [crystal-omp-agent][crystal] (Pokémon Crystal, PyBoy, the
pokecrystal rgbds disassembly) to Generation 3. It does **not** reimplement
the game: it runs the real ROM inside [mGBA][mgba] and uses the
[pret/pokeruby][pokeruby] decompilation as the machine interface.

The governing rule, inherited from the original: **never hardcode game data.**
Every address, struct offset, text encoding, map grid, base stat, move power
and type multiplier is parsed out of the decompilation or read from the ROM's
own tables at runtime.

```
$ ./sapphire --state saves/lab.state status
frame=102058 map=LittlerootTown_ProfessorBirchsLab pos=(6,5) lead=SWAMPY L5 21/21 money=3000 badges=0/8
```

## Running it as an idle game

`--minutes 0` runs until stopped. That is the mode this project is actually
for: a fixed budget ends the run and leaves a frozen widget that looks stuck,
which is exactly what happened on the first overnight attempt -- it exited
cleanly at 07:18 and was still sitting there at 09:28.

Supervised, so it survives a crash and outlives the shell that started it:

```sh
omp hub start pokeagent \
  .venv/bin/python scripts/play.py \
    --state saves/live-run.state --minutes 0 --feed default --session live
```

or with any process supervisor you like; the only requirements are
`LD_LIBRARY_PATH=vendor/lib` and a restart policy.

- `hub ps` / `hub logs pokeagent` -- is it alive, what is it doing
- `hub stop pokeagent` -- stop it
- the widget's bar goes blank and its tooltip reads "run ended" when the feed
  stops, so a dead run looks dead rather than looking like a frozen game

The working state is re-saved on every autosave tick, so a restart RESUMES
where it died instead of replaying from wherever the file was at launch.


## Why a ROM build is part of the setup

`scripts/build_rom.sh` builds `pokesapphire_rev2.gba` from the decompilation
and checks it byte-for-byte against your own cartridge dump. We never *play*
the built ROM. We build it because the link step also emits
`pokesapphire_rev2.sym` — 50,963 entries of `address binding size name` —
and if the built ROM is identical to yours, that table describes yours
exactly:

```
02025734 g 00003ac0 gSaveBlock1
03004360 g 00000258 gPlayerParty
081febc0 g 00002d10 gBaseStats
```

That is the Sapphire analog of `pokecrystal.sym`, and it is what lets the
harness read `gSaveBlock1.money` instead of `0x02025BC4`.

It also buys something Crystal never had: **function** symbols. `gMain.callback2`
and the `gTasks` entries are function pointers, so "which screen am I on" is
an exact question — `Task_NewGameSpeech16` *is* the gender menu — where the
Crystal harness had to pattern-match decoded screen text.

## Setup

You supply the ROM. It must be **Sapphire (US, Rev 2)**, sha1
`89b45fb172e6b55d51fc0e61989775187f6fe63c`, saved as `pokesapphire.gba` in the
repo root. It is gitignored and never committed.

```sh
git clone --recursive <this repo>          # pret/pokeruby is a submodule
cp /path/to/your/dump.gba pokesapphire.gba

# System dependencies (Arch/Omarchy):
sudo pacman -S --needed libmgba arm-none-eabi-binutils arm-none-eabi-gcc \
                        base-devel git perl libpng
# ...or, with no root available:
./scripts/vendor_toolchain.sh              # unpacks the same packages into ./vendor

uv venv --python 3.11 .venv                # mgba ships wheels for cp310/cp311
uv pip install --python .venv/bin/python -e '.[dev]'

./scripts/build_rom.sh                     # agbcc, then the ROM + symbol table
.venv/bin/python -m pytest tests           # 444 unit tests
```

Nothing derived from the cartridge is committed -- not the ROM, not
savestates, and not the widget's art. The party sprites and the Torchic mark
are Pokémon graphics lifted out of the decomp, so they are generated locally
from your own checkout rather than shipped:

```sh
.venv/bin/python widget/make_sprites.py    # 384 party sprites -> widget/poke.run/sprites/
.venv/bin/python widget/make_logo.py       # the Torchic mark
```

Both are optional: the widget falls back to a glyph without the mark, and
`widget/install.sh` prints these exact commands rather than quietly
installing an art-less widget.

Then create a timeline and play the opening:

```sh
.venv/bin/python scripts/newgame.py --state saves/run.state --name AGENT
.venv/bin/python scripts/to_starter.py --state saves/run.state --starter MUDKIP
.venv/bin/python -m pytest tests -m integration    # 33 emulator-in-the-loop tests
```

## Control surfaces

| You want | Use |
|---|---|
| A long session where the model decides each step | a persistent kernel holding one `trek.Driver` |
| Another process poking a running game | `serve.py` (NDJSON on stdin/stdout) |
| One-shot shell command | `./sapphire <cmd>` (loads, acts, saves back) |
| To watch it happen | `LiveFeed` + the `sapphire.run` bar widget |

```python
from trek import Driver
d = Driver("saves/run.state")
d.travel("OldaleTown")
d.observe()          # position, tiles, party, bag, badges, flags, battle, tasks
```

```sh
echo '{"cmd":"run","name":"goto","kwargs":{"x":7,"y":15}}' | ./serve.py --state saves/run.state
./sapphire actions                    # the whole validated action table
```

Every verb goes through one table (`pokeagent/registry.py`), validated
against live state before it runs:

```
$ echo '{"cmd":"run","name":"attack","kwargs":{"slot":0}}' | ./serve.py --state saves/lab.state
{"id": null, "ok": false, "error": "attack: needs an active battle (ui.battle=False)"}
```

## Layout

```
sapphire                launcher (venv + vendored libmgba)
trek.py                 the Driver: one warm process that plays the game
serve.py                NDJSON server around one Driver
pokeagent/
  paths.py              every location, env-overridable
  symbols.py            the .sym table: name -> (address, size)
  charmap.py            text codec from pret/charmap.txt
  cconst.py             #define values, with expression evaluation
  cstruct.py            struct field offsets, from the headers' own annotations
  names.py              ROM tables: species, moves, items, base stats, type chart
  pokemon.py            Gen-3 substructure decryption
  emu.py                mGBA wrapper: memory, input DSL, savestates
  state.py              structured game state
  nav.py                map decode, elevation-aware BFS, cross-map routing
  behaviors.py          metatile semantics from metatile_behavior.c
  menus.py              cursor-accurate menu driving
  naming.py             the naming keyboard
  battle.py             battle play from the engine's own cursors
  tactics.py            Gen-3 damage math and move recommendation
  live.py               the live feed publisher
  registry.py           the action table
  cli.py                the ./sapphire CLI
widget/sapphire.run/    Omarchy bar widget (QML)
scripts/                build, vendor, new game, opening story
pret/                   pokeruby, pinned (submodule)
```

## What Gen 3 changed

Porting was not mechanical. The differences that mattered:

**Pokémon are encrypted.** A Gen-2 party mon was a flat struct. In Gen 3 the
48 interesting bytes are XOR-encrypted with `personality ^ otId` and the four
12-byte substructures are permuted by `personality % 24`, with a checksum.
Reading a species is an algorithm (`pokemon.py`). HP and level are plaintext.

**Maps have elevation.** A tile carries a 4-bit `z`, the player carries their
own, and a step is refused when they disagree unless either is the wildcard 0
or the bridge value 15. BFS state is `(x, y, z)`. Bridges are nonsense
without it.

**Ledges move two tiles**, not one, and are checked *after* collision so they
override the impassable flag.

**No flat text layer.** Crystal decoded a 20×18 tilemap straight to
characters. Sapphire renders through BG tilemaps and windows, so screen text
comes from the engine's own string buffers (`gStringVar4`,
`gDisplayedStringBattle`) and "what does this look like" is answered by an
actual screenshot.

**No banking.** The GBA has a flat 32-bit address space, so Crystal's whole
banked-read apparatus — and its "WRAM banks ≥ 1 silently return garbage"
gotcha — simply does not exist.

## One module deliberately not ported

Crystal has `hookevents.py`: signature-validated PyBoy program-counter hooks
on `PromptButton`, the menu open/2D handlers and battle end, so the harness
could tell when a text page had actually completed. It exists because Crystal
had no better signal — its alternative was polling decoded screen text.

Sapphire does not need it. `gMain.callback2` and every `gTasks` entry are
function pointers that the symbol table names, and `sLockFieldControls` is
the engine's own "the player may not move" flag. Those are exact, always
available, and free. Adding PC hooks on top would be a second, weaker source
of truth for questions already answered — so this is a decision, not a gap.

Everything else in `crystalagent/` has a counterpart here, plus four modules
Gen 3 needs and Gen 2 did not: `pokemon.py` (substructure crypto),
`cstruct.py` (struct offsets from header annotations), `behaviors.py`
(metatile semantics) and `naming.py`.

## Gotchas

Learned by playing, each with the engine's own citation.

1. **`sLockFieldControls` is the truth about who owns input**
   (`src/script.c:179-191`). It is the analog of Crystal's `wScriptMode`.
   `gPlayerAvatar.preventStep` covers cutscenes that freeze the avatar
   without taking the script lock. Check both; never infer "stuck" from
   position not changing.
2. **A script's wait outlives its message box.** `Task_FieldMessageBox`
   disappearing does not mean the script stopped waiting for A. Advance
   scenes on a *stalled signature*, not on the box.
3. **A story script drops and retakes the lock between beats.** Returning on
   the first release leaves you thinking a scene ended mid-sentence.
   `advance_scene` requires the release to hold.
4. **Never blind-press A through a scene that can ask a question.** Doing so
   names your starter `AAAAAAAAAA`. `advance_scene` stops at the naming
   keyboard and hands the decision back.
5. **`in_battle()` goes true ~60 frames before `gBattleMons` is readable.**
   Use `battle_ready()`, or every species and level reads as zero.
6. **`currentElevation` is a 4-bit field** sharing a byte with
   `previousElevation`. An unmasked read gives 0x33 and every `goto` returns
   no-path.
7. **`map.json` coordinates are the *initial* placement.** Scripts move NPCs
   (`setobjectxyperm`), so talk to `Driver.live_npcs()` positions.
8. **Standing on a warp does not fire it.** A warp triggers on the step that
   *enters* the tile, with the direction still held when the step completes.
   Use `take_warp`.
9. **Array strides are not struct sizes.** `struct BattleMove` is 9 declared
   bytes and a 12-byte array element. Derive strides from symbol sizes.
10. **Story gates are variables, not geography.** `VAR_LITTLEROOT_STATE`
    blocks Route 101 until you talk to the rival. Read the scripts.
11. **A move's slot is the engine slot**, never its position in a sorted
    list.
12. **Menu cursors live in memory** (`gMenu.cursorPos`,
    `gActionSelectionCursor`, `gMoveSelectionCursor`). Read them and verify
    the cursor arrived before pressing A. Mashing A at the wall clock
    oscillates forever because the press lands on NO.

## The widget

```sh
widget/install.sh          # installs to ~/.config/omarchy/plugins/sapphire.run
```

Then attach a feed in the driving process:

```python
from pokeagent.live import LiveFeed
feed = LiveFeed("default").attach(d)
```

The bar shows the lead Pokémon; the popup adds the live framebuffer, the
party with HP bars, the ledger and the narration — including the harness's
own reasoning for each battle turn.

![bar](docs/widget-bar.png)

![popup](docs/widget-popup.png)

See `widget/README.md`.

[crystal]: https://github.com/<upstream>/crystal-omp-agent
[mgba]: https://mgba.io
[pokeruby]: https://github.com/pret/pokeruby
