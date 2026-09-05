# DRIVER HANDBOOK — operating the crystal-agent harness

Practical guide for agents driving Pokémon Crystal through this repo.
Companion docs: `AGENTS.md` (session protocol + gotchas — binding),
`DESIGN.md` (decision-boundary doctrine), `PROGRESS.md` (live journal).
This file is about HOW to call the machine.

## Choose your control surface

| You want | Use |
|---|---|
| Long play session, model decides each step | persistent Python kernel holding ONE `trek.Driver` (see below) |
| Scripted batch run / external decider loop | `autopilot.py` stdin NDJSON |
| Another process pokes a running game | `serve.py` NDJSON |
| One-shot shell command | `trek.py <leg>` (boots emulator per call — fine occasionally, wasteful in loops) |

**Warm-process rule** (`AGENTS.md`): boot `Driver(state)` once per session
and compose calls against it. Cold-booting per action wastes ~1 s+ each and
re-reads nothing useful. Cell timings under ~50 ms prove you're warm;
seconds mean you're re-booting.

```python
from trek import Driver
d = Driver("saves/YOURS.state")     # never saves/default.state implicitly
```

## Actions go through the registry

Every verb a decider may invoke is defined once in
`crystalagent/registry.py`. Validation happens BEFORE execution: unknown
verbs, unknown kwargs, missing kwargs, and preconditions are rejected with
a human sentence (`ValueError: fight: needs an active battle
(ui.battle=False)`). Rejection is information, not an obstacle — fix the
decision or the precondition, don't bypass.

| Action | Required kwargs | Optional kwargs | Precondition |
|---|---|---|---|
| `goto` | x, y | label, map_name | not in battle |
| `walk` | path | label | not in battle |
| `fight` | — | max_frames, policy, require_decision | **in battle** |
| `catch` | — | ball, max_balls, nickname | **in battle** |
| `heal` | — | tries | — |
| `talk_to` | x, y | label, facing | not in battle |
| `mart_buy` | x, y, item_name | qty, label | not in battle |
| `use_item` | item_name | target_slot, mon, field | not in battle |
| `heal_party` | — | items, max_items_per_mon | not in battle |
| `settle` | — | quiet, spacing, max_frames | — |
| `drain_scene` | — | max_frames | — |
| `catch_up` | — | nickname, ball, max_balls, max_encounters, label | not in battle |
| `resolve_choice` | — | choice | — |
| `who_fights` | — | — | **in battle** |
| `gym_scout` | map | — | — |
| `travel` | dest_map | label | not in battle |
| `name_prompt` | name | — | not in battle |
| `step_dir` | mv | max_frames | not in battle |
| `press` | seq | — | — |
| `use_cut` | tree_x, tree_y | label, forget_move | not in battle |
| `deposit` | mon | — | not in battle |
| `withdraw` | mon | — | not in battle |
| `box_list` | — | — | not in battle |
| `use_field_move` | move | facing | not in battle |
| `teach_tm` | tm, mon | forget | not in battle |

`d.route(dest_map)` remains a warm-Driver planning method; it is not a
registry action and cannot be sent through serve/autopilot `run`.

Call it from anywhere:

```python
from crystalagent.registry import resolve
resolve(d, "goto", {"x": 6, "y": 5})
d.last_goto_reason        # None on success; see failure table below
```

## Reading the world

- `d.observe()` — full snapshot: position, tiles (here/N/E/S/W terrain
  kinds), party (hp/pp/status/moves+max_pp), bag, money, badges, flags,
  npc cells, ui.textbox/ui.battle, frame, and `enemy` (species/level/hp)
  WHILE ui.battle is up — the key is ABSENT otherwise, so read it with
  obs.get('enemy') (independently verified by omp-fresh). This is the
  serve contract.
- `game_state(emu, names)` — deeper: DVs, shininess, forms, nicknames,
  play time. Egg slots carry `egg: true`; a resting egg shows 0 HP — that
  is NOT a fainted mon.
- `d.map_view(map_name=None)` — ASCII reachable-region view, global
  coordinates in the rulers. Glyphs: `@` you, `.` floor, `%` grass,
  `~` water, `O` warp, `^` ledge, `=` ice, `x` pit, `!` live-blocked,
  `N` NPC, `#` wall, `,` walkable but in a component you CANNOT reach
  from here, `o` a warp into such a component, space wall/off-map. Quote
  coordinates straight back into `goto`/`talk_to`.
  Every unreachable component also gets an `offregion:` line under the
  grid: cell count, bounding box, and the warps (or the `changeblock`)
  that open it. Blanks are walls — a walkable wing is never invisible
  (that bug hid Rocket base B3F's western half; `FUCK_I_MESSED_UP.md` #51).
- `d.grid_drift()` / `d.sync_grid()` — the decoded grid is static
  `.blk` data; these compare it against the LIVE block map in WRAM
  (`d.live_grid()`) and push any difference into nav so pathing sees it.
  Only `changeblock` cells can drift (`nav.conditional()` lists them);
  audited 0 drift across 53 savestates. `map_view()` prints a `DRIFT:`
  line when they disagree.
- `./crystal --state S screen --png /tmp/x.png` — the REAL framebuffer.
  When the question is "what am I looking at", read that image; the text
  screen decode is for dialog and menus, not terrain.
- `d.status()` — one-line summary for logs.

## Failure signatures → first response

| Signal | Meaning | First response |
|---|---|---|
| `d.last_goto_reason = "no-path ..."` | grid/truth says unreachable | check goal cell; maybe sealed (`!`) or wrong map |
| `"no-progress (N idle passes)"` | steps consumed, world unchanged | stuck budget tripped; look at screen text |
| `"...; script-scene-active"` | wScriptMode != 0, a scene owns input | wait/settle, or walk around the scene cell |
| `"replan-storm"` / `"pass-cap"` | thrashing between plans | anchor nearer, split the journey |
| `"; last-block=npc on target cell"` | wanderer parked on path | wait or approach from another side |
| `TravelError: map seam ... ping-pong` | warp cycle | anchor at a waypoint and relaunch |
| `flush_dialog()` returns `"menu"` | a choice box opened mid-drain | handle deliberately — mashing would pick something |
| registry `ValueError` | bad decision shape/precondition | read the sentence; correct the call |

## Recipes

- **Catch**: get into battle (wilds trigger via `fight`'s caller normally);
  `resolve(d, "catch", {"nickname": "NAME"})` throws balls with catch math
  and names it. Check `observe().bag` for ball stock first.
- **Train**: `d.train(target_level)` rotates the party, heals on the rail
  via Pokécenter visits. Heal verification drains straggler pages before
  asserting — if you must patch, don't; report instead.
- **Grind (leveling the bench — learned the hard way, session claude-wren)**:
  1. Exp is SPLIT among participants; lead-and-switch banking gives half and
     never catches a mon up. To close big level gaps the trainee must be the
     SOLE participant and land the KO itself — full exp.
  2. Pick grounds by base-exp, not convenience: Tauros/Miltank (Routes 38/39,
     base 211/200) beat everything pre-Mahogany; Golbat/Machop in Mt. Mortar
     next. exp = base*level/7, halved per extra participant.
  3. Recipe: trainee leads; policy = trainee fights solo while enemy level is
     manageable and hp>35%, else switch to the anchor. Arm the SAME policy on
     `d.default_policy` (intercepted battles bite otherwise) AND pass it to
     every explicit fight. Set `d.learn_policy` BEFORE grinding — level-ups
     will fire learn prompts mid-block.
  4. Pace inside grass with `step_dir` (alternating raw taps only TURN in
     place); check ui.textbox every iteration (a faced NPC eats all input).
  5. Chunk it: ~20 fights -> heal rail -> `d.save()` -> repeat, printing
     party each chunk. `d.train(target)` automates rotate+heal and is the
     first tool to reach for; drive it in chunks and verify levels between.
- **Shop**: `resolve(d, "mart_buy", {...})` waits passively for the shop
  list after the clerk talks (no A presses in that window). Never mash A
  near an open shop list (`AGENTS.md` gotcha 13).
- **New moves**: read `d.lead()["moves"]` immediately before deciding;
  declining a level-up learn is permanent in GSC. HM CUT prefers birds
  (`use_cut(tree_x, tree_y)` handles teaching + swinging).
- **Heal**: `heal` action (must be inside a Pokécenter) — verifies actual
  HP post-jingle; raises if the party is still hurt rather than lying.

## Autopilot / serve protocols (quick reference)

Autopilot stdin commands: `decision`, `observe`, `screen`, `memory`,
`save`, `quit`. Cycle replies carry `obs`, `ok`, optional `error`/`detail`,
and `mem_tail` (recent rolling-memory lines). `why` belongs to journal cycle
records, not replies. `{"cmd":"memory","args":{"tail":N}}` returns
`{frontier, tail}` — the long-horizon view without reading files. Journal
lines are JSONL in `journal/<session>.jsonl` with wall-clock `t`; cycle
records include `used` frame spend.

Serve commands: `observe`, `status`, `save`, `load`, `run`
(registry-resolved), `quit`.

Malformed requests get structured `ok:false`, never a dead pipe.

## Changing the harness

| Owner | Change here |
|---|---|
| `crystalagent/driver/core.py` | construction, lifecycle, save/reload, shared diagnostics |
| `crystalagent/driver/world.py` | observation, map/state queries, objects, missables |
| `crystalagent/driver/ui.py` | input, menus, text, choices, naming |
| `crystalagent/driver/battle.py` | fight/catch/learn/train orchestration and tactics facade |
| `crystalagent/driver/inventory.py` | items, TM/HM, PC, field moves, marts, healing |
| `crystalagent/driver/navigation.py` | movement, goto/route/travel, warps, NPC approach |
| `crystalagent/battle.py` | low-level battle data, pack operations, item normalization |
| `crystalagent/nav.py` | map parsing, terrain, scripts, reachability, route graph |
| `crystalagent/state.py` | symbol-derived WRAM/SRAM decoding |
| `crystalagent/registry.py` | serve/autopilot action names, kwargs, preconditions |

`crystalagent/driver/__init__.py` is the sole concrete `Driver` facade. Add a
method to exactly one ownership mixin; do not add a forwarding copy elsewhere.

## Hygiene

- Forks before risk: `saves/<you>-pre-N.state` (+ `.meta`). Milestones:
  `saves/<you>-<kind>-N.state`. Never touch another session's states.
- `.meta` stamps pyboy version + ROM sha256 and loads refuse mismatches —
  NEVER rebuild the ROM casually; hook signatures and every fork are
  build-coupled.
- Input/text event hooks are live by default (`CRYSTAL_HOOKS=0` disables).
  After a ROM rebuild they self-disable with a warning until re-pinned.
- Keep `.venv/bin/python -m pytest tests` green (unit lane; the
  emulator-in-the-loop scenarios in `tests/integration/` are excluded
  from the default run). Run them explicitly: `.venv/bin/python -m
  pytest -m integration` -- each scenario forks a milestone savestate
  (+ `.meta`) into a temp path and never mutates `claude_saves/`.
  Run `trek gc --keep 3` occasionally (dry-run default; protects
  milestones).
