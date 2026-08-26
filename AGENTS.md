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

**Before your first drive, read `HANDBOOK.md`** — the operational guide:
control surfaces (trek legs vs serve/autopilot NDJSON vs warm kernel),
the action registry with preconditions, observe()/map_view field maps,
failure signatures and their first responses (`d.last_goto_reason`), and
recipes for catch/train/shop/moves.

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
.venv/bin/python trek.py goto X Y [MAP]   # BFS pathfind + walk to (x,y);
                                          # MAP (e.g. VIOLET_CITY) routes
                                          # across maps via warps+connections
.venv/bin/python trek.py walk 'L*5 U*2'
.venv/bin/python trek.py talk X Y          # approach NPC/trainer at (x,y) and talk
.venv/bin/python trek.py catch             # throw balls at the current wild
.venv/bin/python trek.py fight             # play out current battle smartly
.venv/bin/python trek.py heal              # nurse cycle inside a Pokecenter
.venv/bin/python trek.py to_violet         # scripted journey legs (see main())
```

There is NO bundled "grind" leg: pacing in grass, choosing targets, and
stopping are MODEL decisions. Compose them yourself over a warm process
(`serve.py` / `autopilot.py`): `observe()` gives terrain (`tiles`: which
neighbors are grass/water/warp), party levels/HP; `run step_dir {mv}`
takes one step; wilds trigger `run fight`. Decide when you've trained
enough by reading `observe()` -- never loop blindly in code.

```sh
.venv/bin/python serve.py --state saves/<agent>.state   # NDJSON on stdin/stdout
# {"cmd":"observe"} -> full snapshot incl. tiles; {"cmd":"run","name":"step_dir","args":{"mv":"D"}}
```

Signature: `trek.py <leg> [<state>] [args...]`. The state file mutates in
place and is re-saved after every battle and at the end. Omitting `<state>`
REFUSES to run unless
`CRYSTAL_ALLOW_DEFAULT=1` is exported -- `saves/default.state` is a shared
fork point and silent mutation cost a session real progress once. Fork
first if the leg is risky:

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
| Walkable map, BFS with ledges + NPC avoidance | `trek.Driver.goto(x,y)` — escalates by ITSELF to a bounded savestate search when the failure smells like wrong map data (no-path/replan-storm), and refuses to when it is a live NPC/scene/menu; debug render: `MapData.render(map_const)` |
| Fight battles | `Driver.fight(policy=...)` — YOU pick per turn; with nothing steering it logs `auto: attack slot 0 (SURF) -- the HARNESS is choosing` so auto-pilot is never silent |
| Talk to an NPC / trigger a trainer | `Driver.talk_to(x, y)` or `trek talk X Y` — walks adjacent (handles counters), faces them, flushes dialog, fights trainer battles that trigger |
| Buy from a Poké Mart | `Driver.mart_buy(x, y, item, qty)` or `trek mart X Y ITEM QTY` — clerk at (x,y); B-only exit (see gotcha 13) |
| Decide a wild encounter | `d.encounter_policy = lambda frame: 'catch'` — asked ONCE per wild for `'ko'`/`'catch'`/`'flee'`/`('ball', NAME)`; trainers are never asked |
| Decide every turn yourself | `d.fight(require_decision=True)` or `d.decide_all = True` — a turn your policy declines raises `trek.DecisionRequired` (`.frame`, `.kind`, `.options`) instead of the harness guessing |
| See the whole battle in one read | `d.battle_frame()` → `{me, enemy, party, bag, turn, wild, can_switch, moves}`; each move carries `power`/`pp`/`effect_mult` (type effectiveness vs THIS enemy) |
| Real damage/type maths for THIS turn | `d.outlook()` → every move of mine scored with the game's own formula against the mon actually standing there (type multiplier, the Gen-2 **per-TYPE** physical/special split, STAB, badge boost, 85-100% roll, hits-to-KO), each move's `effective_accuracy` after the live accuracy/evasion STAGES (a listed 100% is 60% into two Minimizes), both sides' `my_status`/`their_status`/`my_confused` and `turn_loss` (the share of my turns paralysis/sleep/confusion eats), plus the enemy's moves aimed back and who is faster; `None` before the battle mon block is populated |
| A decision with a stated reason | `d.tactics.recommend(analysis, frame)` → `(action, why)`: certain KO first (EFFECTIVE accuracy breaks ties), then heal, then cure a turn-eating PAR/SLP/FRZ with the cheapest ROM-priced item in the bag, then switch to a mon that RESISTS what is incoming, else best expected damage |
| Audit the maths as a table | `d.tactics.explain(d.outlook())` — one line per move: multiplier, phys/spec, damage span, % of its HP, effective accuracy (with `(listed N)` when the stages moved it), STAB, and each enemy move with `LETHAL` marked; the header carries each side's status and `-N% turns` |
| Audit a battle afterwards | `d.last_battle` (`.rows()`, `.summary()`, `.free_hits()`) — free hits are the switch-in/item turns that wiped the party at Koga |
| Mid-battle actions | policy returns `('attack', slot)`, `('switch', party_idx)`, `('item','SUPER POTION')`, `('ball','GREAT BALL')`, or `'flee'` |
| Name a caught Pokémon | `d.catch(nickname="BUBBLES")` (str, species-keyed dict, or callable) or `trek catch NICKNAME` — types it on the naming keyboard; without a name the prompt is declined |
| Use items out of battle | `Driver.use_item('POTION', target_slot=0)` or `use_item('FULL RESTORE', mon='BROOK')` (nickname; exclusive with `target_slot`). True only on a bag decrement; `d.last_item_reason` says why not (`'no-effect'` = the engine's own "It won't have any effect", not a failure) |
| Heal the whole party from the bag | `Driver.heal_party()` → `{'BROOK': 'FULL RESTORE', 'GATOR': 'already full', 'REED': 'no item'}` — cheapest sufficient item per mon, prices/heal amounts read from the ROM's own tables; `items=[...]` whitelists what it may spend |
| Grind without surrendering control | `d.pace(steps, box=(x_lo,x_hi,y_lo,y_hi))` — random walk clamped to a box (keeps you out of stairwells), stops with `stopped='battle'` and the battle STILL UP |
| Walk where the map data lies | `d.goto(x, y)` already escalates; `d.reach(x, y)` is the same call with a bigger search budget (200 moves / 140 nodes) for floors whose decoded grid is wrong (Victory Road, Rocket base, Ice Path) |
| Teach a TM/HM to a NAMED mon | `d.teach_tm('TM23', 'GATOR', forget='BITE')` (tag or move name; nickname or species) — refuses BEFORE pressing anything with `d.last_tm_reason` in `unknown-tm`/`not-in-bag`/`cannot-learn`/`already-knows`, off the species' own `tmhm` learnset and the live `wTMsHMs` counts; `d.teach_hm('H3','SURF')` still teaches the first ABLE mon |
| Level-up learns | `d.learn_policy = f` to decide them yourself; the DEFAULT (`d.default_learn_policy`) never trades a damaging move for a status move, ranks by ROM base power, and never names an HM move |
| Why did that primitive return False? | `d.last_goto_reason` (nav), `d.last_step_reason` (one step / no decoded grid), `d.last_item_reason` (use_item), `d.last_menu_reason` (pack/pocket/party/START), `d.last_tm_reason` (teach_tm), `d.menu.last_reason` (Menus), `d.last_money_delta` (money moved during movement — it never should) |
| Menus anywhere | `d.menu.select_label('SAVE')` (instance method, cursor-glyph driven), `select_abs(i)` (scrolling lists), `wait_for_label('USE')`; open YES/NO box → `resolve_choice('YES')` |
| Read any game variable | `crystal sym <pattern>` then `crystal read <symbol> -n N [--text]` |
| What have I not collected? | `d.missables()` (key items + HMs, live) / `d.missables('all')` / `crystal missables [--all]` / the `missing:` fragment on `d.status()` — each row cites `maps/Foo.asm:NNN` and carries the giver's coordinates. HM02 FLY sat in Cianwood for a whole playthrough because nothing surfaced this |
| Can I actually use a field move? | `d.field_moves()` → `{'CUT': 'GATOR', 'FLY': None, ...}` — per HM, which party member knows it. "HM in the bag" is not "I can use it" |
| What is at (x,y)? What warps exist? | `d.tile_at(x,y)`, `d.tiles_in(x0,y0,x1,y1)`, `d.find_tiles('warp'\|'water'\|'grass'\|'ledge'\|'blocked'\|'npc')`, `d.exits()` → warps AND edge connections with destinations. **These are the decision interface; `map_view()` is art for humans** (gotcha 11) |
| Go through a door | `d.take_warp(x, y)` — routes adjacent, enters with the held/tapped step, tries each side, verifies the map changed; `d.last_warp_reason` on failure. Standing on a warp never fires it (gotcha 15) |

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
11. **Overworld screens decode as structure glyphs, not semantics** — and
    the same warning applies to the MAP ART. `map_view()` is a rendering
    for humans: it has a 5-column row gutter and a two-row x ruler, so
    reading a coordinate off it means counting characters, and a session
    miscounted three times in one run (walked an Ilex Forest wall 20x, put
    the Olivine pier warp at x=2 when it is x=3, found the Vermilion Port
    Passage exit only by grepping `warp_event`). **Decide from
    `find_tiles(kind)` / `exits()` / `tile_at(x,y)` / `tiles_in(rect)`**,
    which answer by absolute coordinate; `map_view()`'s annotation block
    prints the same data under the grid. Use coordinates for ground truth,
    `screen --png` + image read only when you need terrain visuals.
12. **Door/cutscene warps finish asynchronously AND need the key held.**
    `step_dir` releases the direction as soon as the step starts — doors
    silently don't trigger (the warp only fires if the key is still down
    when the step completes). Use `Driver._step`/`step_hold` (walk/goto do
    this automatically on warp tiles), and `settle()` before acting on a
    fresh warp.
13. **Never flush_dialog near an open shop list.** Blind A presses buy
    single items at ¥200 a pop. `mart_buy` exits with B-only presses.
14. **Warp arrival drifts past the modeled landing cell.** step_hold keeps
    the direction held through the transition, so you glide ~2 cells past
    where nav's BFS says you land (e.g. gate door -> (9,4) but you stop on
    (7,4)). goto replans after every warp for this reason -- don't reuse a
    hand-built path across a warp without re-reading position.
15. **Standing ON a warp does not fire it.** A warp triggers on the step
    that ENTERS its tile, and every door arrival leaves you standing on
    one. Use `take_warp(x, y)` (steps off, re-enters, tries each side —
    a south-wall door only fires when entered going DOWN) instead of
    stepping the planned direction; `travel` does this for you now.
16. **Check `missing:` in `status()` before planning a journey.** HM02
    FLY sat with Chuck's wife in Cianwood for an entire playthrough
    (`crystal missables`; `field_moves()['FLY'] is None` is the tell) and
    every trip of that run was on foot.
17. **A field move that fails leaves its menu open** ("Can't use that
    here" indoors), and an open menu eats all movement input (gotcha 7).
    Every field-move helper must `close_menus()` on every failure path.

## Session protocol

```
1. cat PROGRESS.md
2. ./crystal state            # sanity-check against PROGRESS.md
3. ./crystal missables        # what is still out there (HMs first)
4. work toward the stated objective (fork for risky attempts)
5. save checkpoints at meaningful boundaries: ./crystal save saves/<name>.state
6. update PROGRESS.md         # position, checkpoints, next objective, gotchas
```

Pre-flight before any long journey (the FLY lesson): `d.field_moves()` —
a `None` for FLY/SURF/STRENGTH means the trip is on foot and the item is
probably sitting with an NPC you already walked past.

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
