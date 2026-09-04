# crystal-agent

Drive Pokémon Crystal headlessly on x86, built for agents (and TUIs). This
does **not** port the game — it runs the ROM built from this repo inside a
headless Game Boy Color core ([PyBoy](https://github.com/Baekalfen/PyBoy)),
and uses the disassembly's own build artifacts as the machine interface:

- **`pokecrystal.sym`** (emitted by rgblink) names every WRAM variable, so
  structured state (party, position, battle, money…) is a table lookup, not
  reverse engineering. Struct offsets are derived from labels
  (`wPartyMon1HP - wPartyMon1`), never hardcoded.
- **`constants/charmap.asm`** is the tile↔character encoding, so the 20×18
  text layer (dialog, menus) decodes losslessly to text — including the
  `┌─┐│└┘` textbox borders, which are real charmap entries.
- **`constants/map_constants.asm`** gives map names for `(group, number)`.

The full machine state lives in a savestate file. Every mutating command
loads it, advances the emulator, and writes it back — so each CLI call is one
deterministic transition, and **copying the state file forks the timeline**
(same state + same inputs ⇒ byte-identical result; Crystal's RNG derives
from the hardware divider, which the savestate captures).

## Setup

```sh
# 1. Build the ROM + sym (needs rgbds 1.0.3, see ../INSTALL.md)
cd .. && PATH=~/.local/opt/rgbds-1.0.3:$PATH make -j

# 2. Python env + project (back in crystal-agent/)
cd crystal-agent
python3 -m venv .venv
.venv/bin/pip install -e .
```

## Commands

```sh
./crystal boot [--frames N] [--force]   # power on, create the state file
./crystal input "A .:30 UP:16 A+B:5"    # run an input sequence (see DSL below)
./crystal input "A" --until "FIGHT"     # repeat a sequence until text appears
./crystal run 60                        # advance N frames, no input
./crystal mash A --until "NEW GAME"     # press repeatedly until text appears
./crystal screen [--raw|--png out.png]  # decoded text screen / tile ids / pixels
./crystal state [--screen]              # structured game state as JSON
./crystal saves                         # list savestate checkpoints
./crystal read wOptions -n 1 [--text]   # read any symbol (or 0xADDR) raw
./crystal sym "Badges"                  # search the 59k-entry symbol table
./crystal save PATH / load PATH         # checkpoint / restore (fork points)
./crystal --state other.state ...       # operate on an alternate timeline
```

Mutating commands print the decoded screen plus a status line
(`frame= map= pos= lead= BATTLE…`), so one call = act **and** observe.
`-q/--quiet` works before or after the subcommand.

### Trek driver CLI

For occasional one-shot legs, use
`trek.py <leg> [<state>] [args...]`. Gameplay legs refuse an omitted state;
pass your own fork, or deliberately set `CRYSTAL_ALLOW_DEFAULT=1` to use
`saves/default.state`.

The 22 commands are: `walk`, `goto`, `talk`, `route`, `travel`, `mart`,
`verify`, `states`, `train`, `gc`, `map`, `catch`, `fight`, `flush`, `heal`,
`route29`, `to_violet`, `errand1`, `errand2`, `errand3`, `errand4`, and
`violet`. Run `trek.py --help` for arguments and arities. For an interactive
long session, keep one `Driver` alive instead; see `HANDBOOK.md`.

### Input DSL

Whitespace/comma-separated tokens, executed in order:

| token    | meaning                                   |
|----------|-------------------------------------------|
| `A`      | press A for 8 frames (then 2 release frames) |
| `A:2`    | press A for 2 frames                      |
| `UP:16`  | hold UP for 16 frames (≈1 walking step)   |
| `A+B:5`  | hold A and B together                     |
| `.` / `.:30` | wait 1 / 30 frames                    |
| `A:2*10` | repeat a token 10 times                   |

Buttons: `A B START SELECT UP DOWN LEFT RIGHT` (or `ST SEL U D L R`).

## Agent workflow

The loop is: `input` → read the printed screen/status → decide → `input`.
Useful patterns, learned the hard way:

- **Dialogs**: advance with `mash A`. But an NPC you're *facing* re-enters
  conversation on the next A — escape with an interleaved move
  (`A:2 .:8 DOWN:16 *20`), and check the screen if position stops changing:
  a stray START menu silently eats all movement input.
- **Navigation**: don't guess — the repo is the map. `warp_event`/
  `object_event`/`coord_event` entries in `maps/*.asm` give exact door, NPC,
  and cutscene-trigger coordinates in the same space as `state`'s `x,y`.
- **Overworld screen**: map graphics render as stable per-tile glyphs
  (structure, not semantics); use `screen --png` + an image read when you
  need actual terrain, and coordinates for ground truth.
- **Search**: `save` before anything risky (a catch attempt, an RNG-dependent
  fight), try, `load` to retry — or run many `--state` forks in parallel.
- **Anything not in `state`**: find the label with `sym`, read it with
  `read`. `ram/wram.asm` documents the semantics.

## Layout

```
crystal          launcher (venv + PYTHONPATH)
crystalagent/
  driver/        Driver orchestration by owner: core/world/ui/battle/
                 inventory/navigation; __init__.py is the concrete facade
  registry.py    the 25 validated serve/autopilot action contracts
  symfile.py     .sym parser (name -> bank:addr)
  charmap.py     charmap.asm parser: text decode + 1-cell screen glyphs
  names.py       species/move/item names decoded from the ROM; map names
  emu.py         PyBoy wrapper: banked reads, input DSL, tilemap decode, states
  state.py       structured state (party/battle structs are sym-offset-driven)
  menus.py       menu navigation via the ▶/▷ cursor glyphs + scroll position
  battle.py      low-level battle data, pack operations, and item normalization
  nav.py         map truth: grids, terrain, scripts, reachability, route graph
  cli.py         the `crystal` CLI
trek.py          compatibility facade, scripted legs, and one-shot Trek CLI
serve.py         long-lived Driver NDJSON command server
autopilot.py     decision NDJSON loop with journal/checkpoint safety rails
saves/           savestate files (+ .meta sidecar carrying provenance/frames)
```

Gotcha worth knowing: WRAM symbols in banks ≥1 (most game state) must be
read with explicit bank addressing (`memory[bank, addr]`) — the game switches
`SVBK` during battles, and reads through the currently-mapped bank silently
return the wrong bank's bytes.
