# Fable Results — WREN's run to the Zephyr Badge

Model: claude-fable-5 · Session: fresh boot → Zephyr Badge, all in `claude_saves/`
Persona: **WREN** (see `persona.md`) — cautious river kid, Totodile **GATOR**, Pidgey **REED**.

## Final state
- `claude_saves/wren-zephyr-badge.state` — Violet Gym, **1/8 badges (ZEPHYR)**
- Party: GATOR (Totodile) L16 — Scratch/Leer/Rage/Water Gun; REED (Pidgey) L4
- Bag: TM31 Mud-Slap, HM05 Flash, 1 Potion, 5 Poké Balls, ~¥1680
- Milestones: `wren-starter`, `wren-egg-delivered`, `wren-pre-tower`, `wren-tower-cleared`, `wren-pre-gym`, `wren-zephyr-badge`
- One warm `Driver` kernel the whole session; one driver reboot (deliberate, for a state reload)

## Run log (condensed)
fresh boot → named WREN → Totodile nicknamed GATOR → Mystery Egg errand (Mr. Pokémon
+ Oak's Pokédex) → rival beaten in Cherrygrove, named SILVER → egg delivered, 5 Poké
Balls from aide → Route 30/31, caught REED → Violet Mart shopping → Sprout Tower: all
sages + Elder Li (HM05) → Violet Gym: Abe, Rod, Falkner → **Zephyr Badge**.

## What went well
- **The registry/travel/fight stack carried the overworld.** Multi-map `travel` legs
  (New Bark ↔ Mr. Pokémon's, Routes 29–31) worked with zero manual pathing; `fight`'s
  best-move selection won ~30 battles without a single loss when driven properly.
- **`d.catch(nickname=...)` is excellent.** One call: ball math, throw, naming keyboard.
- **Checkpoint discipline paid for itself.** The one real disaster (GATOR fainting)
  cost only ~10 minutes because `wren.state` had been saved minutes earlier.
- **Repo-as-map.** Reading `maps/*.asm` for ball/NPC/trainer coordinates was always
  correct and much faster than probing (Totodile ball (7,3), clerk (1,3), Falkner (5,1)).
- **`map_view()` for ground truth.** Every time nav lied, the ASCII view explained why
  (sealed `!` coord-event cells, NPC parked in a choke, warp actually elsewhere).

## What went badly
1. **Scripted-scene textboxes stall `travel`/`goto` into replan-storms.** The single
   biggest time sink. Elm's phone call, the rival ambush, the aide hand-offs — each
   produced 20-replan GAVE UP walls of `[textbox]` spam. The fix was always the same
   (drain with A until `wScriptMode==0`), but `travel` never does it itself. A built-in
   "textbox blocking → drain one page" policy would remove ~40% of this session's friction.
2. **Blind menu inputs into an unverified context fainted GATOR.** I pressed a
   potion-menu sequence while a trainer battle was actually on screen; the presses
   played battle turns and GATOR fainted. Lesson (now hard-learned): verify screen
   state between every menu press; never send a canned sequence after a `goto` that
   ended in `[textbox]`.
3. **Sprout Tower's mapgraph is wrong.** `travel` to 2F/3F targeted unreachable stairs;
   ascent/descent had to be done warp-by-warp with `map_view`. `tower.py` exists in the
   repo for exactly this reason — I should have read it *before* entering, not after.
4. **`use_item` / `heal` / `mart_buy` first-call races.** Every one of these failed on
   first invocation ("START menu did not open", "party not fully healed", "shop menu
   did not open") and succeeded on retry after a settle+drain. Gotcha 2 (menu-setup
   swallowing) in AGENTS.md is real and applies to the high-level verbs too.
5. **A swallowed DOWN press bought Poké Balls instead of Potions.** Caught it by
   checking money+bag afterwards; redid the purchase with cursor verification. Never
   trust a shop sequence you didn't read back from the screen.
6. **1F exit ping-pong.** `travel` oscillated between (8,15)↔(11,15) around the tower's
   double-door; manual step-onto-warp-with-held-direction fixed it (gotcha 12).

## Deviations from persona
- Wren's "no partner ever faints" rule was broken once (the blind-input incident);
  honored by reloading the checkpoint and replaying that stretch carefully.
- REED stayed L4 — the plan to train him fell to time pressure; GATOR soloed everything.

## Advice for the next session (from this run)
- After ANY failed `goto` whose reason ends in `[textbox]`: stop navigating, drain
  A-with-checks until `wScriptMode==0`, then re-plan. Do not loop `travel`.
- In Sprout Tower / any wrong-grid map: navigate with `map_view()` + manual `press`,
  and use `hop(x, y, dir)`-style held steps onto `O` cells.
- Before any canned menu sequence, assert the expected screen text is present.
- `heal`/`mart_buy`/`use_item`: expect first-call failure; settle, drain, retry once.

## Post-run fixes (subagent batch, same day)

Three subagents fixed the harness lowlights; full suite green (112 passed), plus a
live smoke test: from the `wren-pre-tower` fork, `travel` now reaches SPROUT_TOWER_3F
end-to-end (previously impossible), and the sage scene on the walkway surfaces one
actionable GAVE UP instead of a 20-replan storm.
- **TrekFixer** (`trek.py`): `_drain_scene` auto-drains scene textboxes inside
  goto/travel (aborts on choice menus/battles); one settle-drain-retry inside
  `use_item`/`heal_pokecenter`/`mart_buy`; `_held_warp_entry` fixes the double-door
  glide ping-pong. 17 new unit tests.
- **MapgraphFixer** (`nav.py`, `build_mapgraph.py`, `data/mapgraph.json`): region-aware
  graph — connected components per map, per-edge from/to regions; Sprout Tower's true
  walkway topology now encoded. 11 new unit tests.
- **RouteWirer** (`trek.py`): route/travel Dijkstra converted to (map, region) nodes;
  replans from the live cell after region-seam drift. 5 new unit tests.

## Persona reflection

Root cause of drift: the persona lived in prose, not the control loop — adherence
degraded exactly proportionally to tooling friction (early game in character; Sprout
Tower onward, pure optimizer). Violations: name unverified (AWREN), one faint from
blind menu inputs, entered the tower hurt, REED benched at L4, zero optional NPC
talks, a mis-purchase Wren would never make. Countermeasures for next session:
persona invariants as machine-checkable pre-action gates (full-HP before landmarks,
read back names after keyboards, assert bag deltas after purchases), a persona audit
at every checkpoint save, roleplay at decision points (REED leads vs grass sages),
and slowing down instead of batching blind inputs when the harness misbehaves.

## Leg 2 — Hive Badge (model-driven navigation)

Per user direction, this leg was driven by model decisions, not predetermined
routes: every waypoint chosen from `map_view()`/`observe()`, `goto` used only as
a local leg executor, no whole-route `travel` calls. Result: **HIVE badge 2/8**.

Highlights:
- Persona gates held this time: healed before Union Cave, the Well, and the gym;
  walked BACK up Route 32 for the Togepi egg (Wren wouldn't leave an egg);
  declined the ¥1,000,000 SlowpokeTail scam; every purchase cursor-verified.
- GATOR **evolved to CROCONAW** (L18) in Union Cave; finished L22 after Bugsy.
- Story: Slowpoke Well grunts cleared, Kurt scene, gym full-cleared (Al, Benny,
  Josh, Bugsy). TM49 Fury Cutter acquired.
- Honest miss: REED is still L4. The route offered no safe fights for him
  (everything L9+ vs his 17 HP); Wren keeping him out IS the no-faints rule,
  but next leg needs deliberate low-level grass time for him.
- New harness bug found (reported in PROGRESS.md): `_drain_scene`'s choice-menu
  guard misfires on BLANK pre-battle trainer textboxes — it should require a
  real cursor glyph before refusing to press A.
