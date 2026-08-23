# AGENTS.md — playing Pokémon Crystal as an agent

Instructions for AI agents driving the game in this repo. Read this before
your first command; it encodes things that cost previous sessions real time.

## The one rule

**Every session must leave a paper trail.** Before doing anything else:

1. Read `PROGRESS.md` (next to this file) — it says where the run stands,
   which checkpoint to resume from, and what the next objective is.
2. Do your work.
3. Update `PROGRESS.md` at every milestone: new checkpoint saved, objective
   completed, or a new gotcha discovered. A session that dies without
   updating it forces the next one to re-learn everything.

Context windows are the real enemy — agents don't die because the game is
hard, they die re-exploring. `PROGRESS.md` + aggressive checkpoints are the
countermeasure.

## Quick start

```sh
./crystal state                       # where am I? (JSON + status line)
./crystal saves                       # list checkpoints
./crystal screen                      # decoded 20x18 text screen
./crystal input "UP:16 A .:30"        # act (prints screen + status after)
./crystal input "A:4" --until "FIGHT" --max-frames 3000
```

For anything longer than a couple of inputs use the persistent-process
driver (loads the ROM once, seconds instead of per-call overhead):

```sh
.venv/bin/python trek.py goto X Y          # BFS pathfind + walk to (x,y)
.venv/bin/python trek.py walk 'L*5 U*2'
.venv/bin/python trek.py grind 'D U' 13    # pace in grass until level 13
.venv/bin/python trek.py catch             # throw balls at the current wild
.venv/bin/python trek.py fight             # play out current battle smartly
.venv/bin/python trek.py heal              # nurse cycle inside a Pokecenter
.venv/bin/python trek.py to_violet         # scripted journey legs (see main())
```

Signature: `trek.py <leg> [<state>] [args...]`. The state file mutates in
place and is re-saved at the end; omit `<state>` (or pass `''`) for
`saves/default.state`. Fork first if the leg is risky:

```sh
cp saves/default.state saves/attempt.state     # (+ the .meta sidecar!)
cp saves/default.state.meta saves/attempt.state.meta
.venv/bin/python trek.py goto saves/attempt.state X Y
```

Forks must end in `.state` — that suffix is how trek distinguishes them
from the leg's own arguments.

## Capabilities map

| Need | Use |
|------|-----|
| Structured state (party/battle/money/badges) | `crystal state`, or `Driver.lead()` / `game_state()` |
| Walkable map, BFS with ledges + NPC avoidance | `trek.Driver.goto(x,y)`; debug render: `MapData.render(map_const)` |
| Fight battles | `Driver.fight()` — best-move selection from ROM move data, auto-POTION at <30% HP, flees hopeless wilds |
| Catch / flee / switch mid-battle | custom policy: `d.fight(policy=lambda rows, me, enemy: ('ball',))`; options: `'flee'`, `('attack', slot)`, `('switch', party_idx)`, `('item','POTION')`, `('ball','POKE BALL')` |
| Use items out of battle | `Driver.use_item('POTION', target_slot=0)` |
| Menus anywhere | `Menus.select_label('SAVE')` (cursor-glyph driven), `select_abs(i)` (scrolling lists), `wait_for_label('USE')` |
| Read any game variable | `crystal sym <pattern>` then `crystal read <symbol> -n N [--text]` |

Battle math comes from the repo itself: type chart parsed from
`data/types/type_matchups.asm`, move power/type/accuracy read out of the
ROM's `Moves` table via `pokecrystal.sym`. Don't hardcode game data.

## Gotchas (all learned empirically)

1. **Menu cursors come in two glyphs**: `▷` ($ec) for static vertical menus
   (START menu), `▶` ($ed) for battle/scrolling menus. Match both — see
   `menus._cursor_x`.
2. **A presses get swallowed during menu setup.** The frame a menu is drawn,
   its input loop isn't running yet. Always settle (`.:15`) after a menu
   appears before confirming, and prefer confirm-until-closed loops over
   single presses (see `use_item`'s target-confirm loop).
3. **The battle FIGHT/PKMN/PACK/RUN menu is a 2×2 grid.** Live cursor is
   `wMenuCursorY` (row) + `wMenuCursorX` (col); `wMenuCursorPosition` only
   gets written on confirm. Navigate with UP/DOWN then LEFT/RIGHT.
4. **The battle HUD stays on screen during enemy text**, so "screen shows
   FIGHT/RUN" ≠ "menu is interactive". Failed actions are fine (bounded),
   but never assume a visible label means a clickable menu.
5. **The party menu ("Use on which PM?") needs repeated A confirms** — the
   first lands during setup. Press until the CANCEL row disappears.
6. **WRAM banks ≥1 need explicit bank reads** (`memory[bank, addr]`) — the
   game switches SVBK constantly; unbanked reads silently return garbage.
   `emu.read` handles this when given `(bank, addr)` tuples from `sym`.
7. **A stray START menu silently eats all movement input.** If position
   stops changing, check the screen; `B:4 .:10` closes it.
8. **An NPC you're facing re-enters dialog on the next A.** Escape with an
   interleaved move (`A:2 .:8 DOWN:16 *20`).
9. **Savestates fork timelines.** Same state + same inputs ⇒ byte-identical
   result, RNG included. `save` before risk, `load` to retry, or run many
   `--state` forks in parallel. Copy the `.meta` sidecar too (frame count).
10. **The repo is the map.** Door/NPC/cutscene coordinates live in
    `maps/*.asm` (`warp_event`, `object_event`, `coord_event`) in the same
    coordinate space as `state`'s x,y. Don't guess layouts.
11. **Overworld screens decode as structure glyphs, not semantics.** Use
    coordinates for ground truth, `screen --png` + image read only when you
    need terrain visuals.

## Session protocol

```
1. cat PROGRESS.md
2. ./crystal state            # sanity-check against PROGRESS.md
3. work toward the stated objective (fork for risky attempts)
4. save checkpoints at meaningful boundaries: ./crystal save saves/<name>.state
5. update PROGRESS.md         # position, checkpoints, next objective, gotchas
```

Checkpoint naming: `<milestone>.state` (e.g. `violet-badge1.state`). Keep
names stable and referenced in PROGRESS.md.

## Multiple agents, one saves/ dir

Milestone checkpoints (`violet-arrived.state`, `zephyr-badge.state`, …) are
**shared read-only history**; `default.state` is contested — never assume it
still holds what PROGRESS.md says. Each concurrent session must:

1. Fork its own working state from the newest good milestone and name it
   after itself: `cp saves/<milestone>.state saves/<agent>.state` (plus the
   `.meta` sidecar).
2. Always pass that file explicitly so nothing else gets mutated:
   - trek: `.venv/bin/python trek.py <leg> saves/<agent>.state <args>`
   - CLI:  `./crystal --state saves/<agent>.state ...`
   - or export `CRYSTAL_STATE=$PWD/saves/<agent>.state` once per shell.
3. Claim its objective in PROGRESS.md *before* starting work ("session X
   owns Sprout Tower, working state `saves/x.state`") and record results
   when done. Promote a finished objective by saving a new milestone
   checkpoint — a new filename, never an overwrite of an existing one.
