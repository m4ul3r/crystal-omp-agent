# AGENTS.md — playing Pokémon Sapphire as an agent

Instructions for AI agents driving the game in this repo. Read this before
your first command. Companion docs: `README.md` (setup and architecture),
`PROGRESS.md` (the live journal).

## The one rule

**Every session leaves a paper trail.** Before doing anything else:

1. Read `PROGRESS.md` — where the run stands, which checkpoint to resume
   from, what the next objective is.
2. Do your work.
3. Update `PROGRESS.md` at every milestone: new checkpoint, objective
   completed, or new gotcha discovered.

Context windows are the real enemy. Agents don't fail because the game is
hard; they fail re-learning what a previous session already knew.

## Quick start

```sh
./sapphire --state saves/lab.state status      # where am I?
./sapphire --state saves/lab.state state       # full JSON snapshot
./sapphire saves                               # list checkpoints
./sapphire actions                             # every verb, with preconditions
./sapphire --state saves/lab.state map         # ASCII map (art; see gotcha 1)
```

For anything longer than a couple of commands, use a **warm process** — boot
`Driver` once and compose against it. A cold boot per action costs a second
of ROM and symbol parsing and buys nothing.

```python
from trek import Driver
d = Driver("saves/mine.state")
d.travel("OldaleTown")
d.observe()
```

## Capabilities

| Need | Use |
|---|---|
| Structured state | `d.observe()`, `d.status()`, `./sapphire state` |
| Where am I, exactly | `d.pos()`, `d.map_name()`, `d.facing()`, `d.elevation()` |
| Which screen owns input | `d.state.callback_name()`, `d.state.tasks()` — function pointers resolved through the symbol table, so this is exact |
| Can I move at all | `d.scene_active()` — reads `sLockFieldControls` + `preventStep` |
| Walk somewhere | `d.goto(x, y)`, `d.walk("URDL")`, `d.step_dir("U")` |
| Cross maps | `d.travel("OldaleTown")`, `d.take_warp(x, y)` |
| What is at (x,y) | `d.nav.cell(map, x, y)`, `d.find_tiles(kind)`, `d.exits()` |
| Where are the NPCs *now* | `d.live_npcs()` — not `map.json`, which is initial placement |
| Would this step work | `d.predict_step("U")` — static grid plus live bodies |
| Talk to someone | `d.talk_to(x, y)` |
| Get through a cutscene | `d.advance_scene()` — presses A only when stalled, and STOPS at a naming prompt |
| Wait one out silently | `d.drain_scene()` — presses nothing |
| Fight | `d.fight(policy=None)`, or `d.attack(slot)` / `d.switch_to(i)` / `d.use_battle_item(n)` / `d.throw_ball()` / `d.flee()` |
| Understand a battle turn | `d.battle_frame()`, `d.outlook()`, `d.explain()` |
| Get advice with a reason | `d.recommend()` -> `(action, why)` |
| Audit a battle afterwards | `d.battle.summary_text()`, `d.battle.free_hits()` |
| Name something | `pokeagent.naming.NamingScreen(d.emu, d.state).type("NAME")` |
| Answer a YES/NO box | `d.resolve_choice("YES")` |
| Read any game variable | `./sapphire sym <pattern>`, then `./sapphire read <symbol> -n N` |
| Read a flag or var by name | `d.state.flag("FLAG_BADGE01_GET")`, `d.state.var("VAR_LITTLEROOT_STATE")` |
| Why did that return False? | `d.last_goto_reason`, `last_step_reason`, `last_warp_reason`, `last_talk_reason`, `last_scene_reason`, `d.battle.last_reason`, `d.tactics.last_outlook_reason` |
| Let a human watch | `LiveFeed(name).attach(d)` + the `sapphire.run` bar widget |

Battle maths comes from the ROM's own tables and the decompiled formula.
Never hardcode game data.

## Gotchas

All learned empirically, each with the engine citation.

1. **The ASCII map is art; coordinates are truth.** `d.nav.render()` has a
   row gutter and a column ruler, so reading a position off it means counting
   characters. Decide from `find_tiles(kind)`, `exits()` and
   `nav.cell(x, y)`, which answer by absolute coordinate. The predecessor
   project lost a session miscounting map art three times in one run.
2. **`sLockFieldControls` says whether you may move** (`src/script.c:179`).
   `gPlayerAvatar.preventStep` covers cutscenes that skip the script lock.
   `d.scene_active()` reads both. Never infer "stuck" from position.
3. **A script's wait outlives its message box.** `Task_FieldMessageBox`
   vanishing does not mean the script stopped waiting for A. Advance on a
   stalled signature.
4. **Story scripts drop and retake the lock between beats.** `advance_scene`
   requires the release to hold; a naive "lock is clear" check returns
   mid-sentence.
5. **Never blind-press A through a scene that can ask a question.** It names
   your starter `AAAAAAAAAA`. `advance_scene` returns False with
   `last_scene_reason` when the keyboard opens — name it deliberately.
6. **`in_battle()` precedes readable battle data by ~60 frames.** Use
   `d.state.battle_ready()` before reading `gBattleMons`, or everything is
   zero.
7. **`currentElevation` is a nibble.** Mask it. Unmasked it reads 0x33 and
   every `goto` returns no-path.
8. **`map.json` coordinates are initial placement.** Scripts move NPCs
   (`setobjectxyperm`). Use `d.live_npcs()`.
9. **Standing on a warp does not fire it.** It triggers on the step that
   enters, with the key still held. Use `take_warp`.
10. **A move's slot is the engine slot.** Never use a list index.
11. **Menu cursors are in memory.** `gMenu.cursorPos`,
    `gActionSelectionCursor`, `gMoveSelectionCursor`. Read, move, verify,
    then press A. Mashing A at the wall clock oscillates forever because the
    blind press lands on NO.
12. **Array strides are not declared struct sizes.** Derive from the symbol
    size and refuse a non-integral division.
13. **Story gates are variables.** `VAR_LITTLEROOT_STATE` blocks Route 101
    until you talk to the rival in their bedroom. Read `data/maps/*/scripts.inc`
    rather than guessing at geography.
14. **Never assume a ball position.** `sStarterMons` is
    `{TREECKO, TORCHIC, MUDKIP}` (`src/starter_choose.c:50`) and the picker
    opens on index 1. Read the table.
15. **Savestates fork timelines.** Same state + same inputs is byte-identical,
    RNG included. Save before risk, load to retry, run forks in parallel. Copy
    the `.meta` sidecar too — a load refuses on a ROM-hash mismatch, which is
    the point.

## Session protocol

```
1. cat PROGRESS.md
2. ./sapphire --state saves/<yours>.state status     # sanity-check it
3. work toward the stated objective; fork before anything risky
4. save checkpoints at meaningful boundaries
5. update PROGRESS.md: position, checkpoints, next objective, new gotchas
6. before yielding harness changes:
     .venv/bin/python -m pytest tests                 # unit lane, fast
     .venv/bin/python -m pytest tests -m integration  # emulator in the loop
```

## Multiple agents, one saves/ dir

Milestone checkpoints are shared read-only history. Each concurrent session
must:

1. Fork its own working state from the newest good milestone, named after
   itself: `cp saves/<milestone>.state saves/<agent>.state` (**and the
   `.meta`**).
2. Always pass that file explicitly (`--state`, or `Driver("saves/x.state")`).
3. Claim its objective in `PROGRESS.md` *before* starting, and record results
   when done. Promote a finished objective by saving a new milestone under a
   new filename — never overwrite one.

`serve.py` refuses `saves/default.state` without `--allow-default`, because
silently mutating a shared fork point is a real way to lose a run.
