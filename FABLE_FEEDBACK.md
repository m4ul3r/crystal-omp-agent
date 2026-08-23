# FABLE_FEEDBACK.md — field notes from the badges 4→7 run (Aug 23 2026)

Written by the claude-lex session after taking the run from 3 badges to 7
(FOG, MINERAL, STORM, GLACIER) and getting deep into the Ice Path en route
to Clair. This is the "what I wish I'd known" document: recurring failure
patterns, harness gaps, engine facts, and the techniques that actually
worked. Read PROGRESS.md for run state; read this for *how to work*.

## Where the run stands (so you can resume)

- Badges: ZEPHYR, HIVE, PLAIN, FOG, MINERAL, STORM, GLACIER (7/8).
- Party: TYPHLOSION L47 (Strength/Cut/Smokescreen/Ember), POLIWAG L4
  (Bubble/Surf), TOGEPI L10. Money ~₽26k. Working state:
  `saves/claude-lex2.state`. Milestones per badge exist (`*-badge.state`).
- In progress: ICE PATH crossing to Blackthorn for Clair (RISING).
  Status: on B1F, Strength armed, solving the boulder→hole puzzle.
  Solved plan (verified against collision bytes, partially executed):
  - Boulder (8,9) → hole (5,12): R-push from (7,9) → (9,9); D-push ×2 from
    above → (9,11); L-push ×5 from (10,11) → (4,11); walk around via row
    13 to (4,10); D-push → (4,12); walk to (3,12); R-push → sinks.
  - Boulder (7,8) → hole (4,7): L-push ×2 from (8,8) → (5,8); U-push ×2
    from (5,9) → (5,6); walk the LONG way (row 1 corridor → (5,3)→(5,4))
    into the NW pocket to (6,6); L-push → (4,6); goto (4,5); D-push → sinks.
  - Then walk onto hole (5,12) yourself → land B2F (4,12) → slide U (stops
    at (4,8) on the sunken boulder) → slide R (stops on island (8,8)) →
    walk to (9,11) stairs → B3F → (15,5) → B2F-Blackthorn → (3,15) →
    B1F south → (5,25) → 1F east spine → east chamber slides → exit
    (36,25)/(36,27) → BLACKTHORN_CITY.
  - GOTCHA that burned two attempts: boulder pushes register ~60-100
    frames AFTER step_dir returns, so naive retry loops double-push and
    overshoot. Wait `.:100` + re-read npc_cells before deciding to press
    again. Boulders RESET when you leave and re-enter the map (bounce
    through the (17,3)↔(17,1) warp pair); Strength must be re-armed per
    map entry (talk to any boulder, answer YES).
- After Blackthorn: Clair (Dragonair/Kingdra ~L37-40), then the Dragon's
  Den shrine quiz before she hands over the badge.

## The five failure patterns that cost the most time

1. **Assert-before-save.** A script that asserts and dies before `d.save()`
   silently rolls the timeline back to the last save. I lost the same
   progress 3-4 times this way (lighthouse climb twice, pharmacy trip).
   RULE: save immediately after every verified transition; put asserts
   AFTER the save, and roll back explicitly from milestones if the save
   turns out bad. Never `assert` between achievement and persistence.

2. **Unverified success claims.** `talk_to` returns 'talked' even when it
   faced an empty tile (wandering NPCs — Floria) or nothing happened.
   `heal_pokecenter` used to print "healed" outside the Pokécenter (fixed:
   now checks map + party HP, egg-aware). Whiteout HEALS the party, so
   `hp > 0` proves nothing about winning — check map/money/event flags.
   RULE: verify by reading WRAM (event flags, badge bits, bag, hp), never
   by "the flow completed".

3. **Mid-battle and mid-fade saves.** Saving while wBattleMode != 0, or
   while a menu-close fade is repainting, poisons the state file. Two
   poisoned saves required milestone rollbacks. `close_menus()` now waits
   out blank frames (a fade frame reports "no menu" and then the menu
   repaints ~50 frames later — never judge a blank screen).

4. **Static nav grid vs live map.** The BFS grid decodes DEFAULT blockdata.
   `changeblock` doors (Rocket hideout, Burned Tower ladder), warp landings,
   and boulder objects diverge from it. Symptoms: "no static path" through
   a door you just opened, or goto planning through a boulder. Fix pattern:
   step through changed cells manually, then let goto replan from the far
   side. Similarly, "inaccessible leftover" warp_events with plain-floor
   collision are DORMANT (never fire) but the BFS refuses to path across
   any warp-event cell — that sealed Burned Tower B1F's (10,7) corridor.

5. **Coord-event over-blocking / under-blocking.** The planner seals cells
   whose scene might fire (Goldenrod PC doors = inert GS Ball mobile event;
   Olivine rival; gym trigger cells). Many of these are the ONLY corridor.
   The documented remedy works: confirm from maps/*.asm that the script is
   safe (talk-only, sets scene NOOP), then step onto it deliberately and
   flush the cutscene with the wScriptMode==0 loop. Conversely a *pending*
   scene freezes all movement with no textbox (Burned Tower fall, sight-line
   trainers): if steps report blocked in all four directions with no menu,
   read wScriptMode — 1 means a script owns the player; feed it A/waits.

## Techniques that won

- **Ground truth first.** Reading the map .asm before acting turned the
  Farfetch'd chase (VAR_FACING branch table), Morty's pit-warp lattice, the
  hideout password flow, and the Ice Path warp graph into table-driven
  walks instead of trial and error. `grep warp_event/object_event/
  coord_event maps/X.asm` is the single highest-value move in this repo.
- **Savestate BFS (PyBoy save_state to BytesIO).** The universal key for
  anything nav can't model: ice slides, boulder puzzles, "is this room
  actually sealed?" proofs (Cianwood gym: 336-state exhaustion proved the
  choke was Lung's NPC, justifying the documented object-struct workaround).
  Two hard-earned refinements:
  - Wild encounters DELETE edges at random. Retry each probe with an RNG
    phase shift (`press('.:5*attempt')` then step) until battle-free; only
    fight as a last resort. Without this, closures differ run to run.
  - A snapshot taken with a pending battle/script (wScriptMode!=0) is
    poisoned: every probe from it reports blocked and its whole subtree
    dies (this hid B1F behind an 8-cell closure). Check wScriptMode/battle
    before snapshotting; resolve first.
  - Tool timeout is 10 minutes REAL (larger values are clamped) — keep
    state spaces small (single floor / single puzzle), stage the search.
- **Per-position facing tables** for VAR_FACING scripts (`talk_to(...,
  facing=)` was added for this).
- **Waypoint walkers with live-position resync** for invisible/fall floors
  (Morty). Compute each step from d.pos(), restart the route on any fall.
- **Deterministic replay**: same state + same input sequence = identical
  outcome, RNG included. A win lost to a crash-before-save can be replayed
  exactly. This saved the first Whitney win.

## Engine facts (verified live, save future sessions the discovery cost)

- Collision bytes: 0x00 floor, 0x07 wall, 0x12 cut tree, 0x23 ice,
  0x29 water, 0x60 HOLE (fall-through), 0x70-0x7f warps (0x71 door,
  0x72 ladder, 0x7a staircase), 0xb0-0xb7 directional side walls.
- SURF: walking into water does NOT prompt. Face water, press A, answer
  YES ("The water is calm..."). wPlayerState == 4 while surfing. Dismount
  is automatic. (All wired into `_step`/`enable_surf` now.)
- COLL_STAIRCASE (0x7a) rejects held-key entry; the warp only fires if the
  key is released at the right phase (`_step_warp_tap` handles it). Dept
  store staircases were the discovery case; the elevator is a clean
  alternative (bg_event panel + select_label of the floor).
- Badge bits (wJohtoBadges): ZEPHYR,HIVE,PLAIN,FOG,**MINERAL**,**STORM**,
  GLACIER,RISING — MINERAL is bit 4, STORM bit 5, opposite of trainer-card
  display order (state.py fixed; old logs are mislabeled).
- Key-items pocket is 1 byte/item (no quantity); (id,qty) stride hid
  SECRETPOTION etc. (fixed in `_bag`).
- The TM/HM pocket renders "HM01" as "H1 CUT"; "FURY CUTTER" contains
  "CUT"; 'POK' label-matches POKéDEX before POKéMON — always match the
  most specific string. Menus remember their last cursor slot; party menu
  WRAPS (use wMenuCursorY via `_party_cursor_to`, never press counts).
- HM field flows: `teach_hm(tag, move, forget_move=)` (generalized);
  first-ABLE-mon selection reads the tag on the row BELOW the cursor row.
  Strength activation is per map entry. Ice Path/Cianwood boulders reset
  on map reload.
- Declining a level-up move is PERMANENT (no relearner in GSC). fight()
  now learns by default, forgetting the first FORGET_PRIORITY match —
  Quilava's Flame Wheel was lost to the old decline-everything handler.
- Sight-range-1 trainers freeze your movement while their approach scene
  is pending (Bridget at Morty's choke, Lung in Cianwood). If movement
  dies next to a trainer, fight them first.
- Eusine stands ON the only descent tile in Burned Tower B1F after the
  beasts scene — talk to him and he leaves. NPCs standing on choke points
  is a repeated design pattern; check npc_cells() before assuming geometry.
- Whiteout: party fully healed, money halved, respawn at last-used
  Pokécenter. Two sea-route wipes cost ~₽6k total; crossing healthy with
  segment saves is always cheaper.

## Cianwood gym caveat (unresolved legit solution)

Savestate BFS exhausted the reachable (position × boulder) space twice and
proved Chuck's platform unreachable with Black Belt Lung's object at (5,5)
after his defeat (the middle-boulder push wedges (4,4); (5,4) is behind
Lung). I applied the precedented stuck-NPC remedy (zero the wObjectStructs
slot; the object respawns on map reload) and beat Chuck. If someone finds
the intended solution, document it and retire the workaround.

## Harness improvements made this session (in the working tree)

- nav/trek: WATER routing + surf mount in `_step`; `enable_surf()`.
- trek: `menu_open`/`close_menus` (fade-aware) postconditions;
  `cursor_rows`; `_party_cursor_to` (WRAM cursor); `teach_hm` generalized;
  `talk_to(facing=)`; goto blocked-step self-diagnosis ([textbox]/[stray
  menu]/[npc on target cell]) with auto-recovery; `heal_pokecenter`
  verified + egg-aware; `_bag` key-item stride fix.
- battle.py: wedge guard (2 misfires → forced attack, 12 → 'stuck');
  0-PP-aware attack(); learn-mode text handler; another agent added
  Disable-aware best_move (explains the historic Bridget 90k-frame wedges)
  and forced-switch handling.

## Open harness gaps (priority order)

1. **attack() reports success on game-rejected moves** (0 PP mid-turn via
   Spite/Disable edge cases): play() can't see the rejection, so the fails
   counter never trips. Policies must track PP via me['moves'] until play()
   verifies a turn actually elapsed (e.g. watch wBattleTurnCounter or enemy
   HP/PP deltas).
2. **use_battle_item can consume/toss the wrong item when select_abs
   desyncs** — it once burned ~9 potions in a wedge. It should verify the
   cursor is on the intended item (read the highlighted row text) before
   confirming, and re-check bag counts after.
3. **Nav should model live state**: changeblock doors, dormant vs live
   warp events (collision byte gates whether a warp_event fires — the data
   is already in the grid), NPC objects as dynamic obstacles with a
   "deliberately-trip" option for scene cells verified safe.
4. **Ice/slide modeling in nav** would replace savestate BFS for routine
   crossings: a slide is a deterministic function of (grid, entry cell,
   direction) — cheap to precompute.
5. **use_item is unsafe after warps** ("could not open PACK" then its
   blind presses WALK the player — it moved us onto a ladder mid-flow).
   It needs the same wait-for-menu verification as the battle pack flow.
6. **fight()'s auto-save (emu.save mid-leg for watch.py)** writes the
   working state during battles; combined with crash-before-save patterns
   this bakes mid-battle states into forks. Consider saving to a scratch
   sidecar instead.
7. **travel()/mapgraph** lacks water edges and prefers impossible border
   connections (Goldenrod→R35 "north edge" cells are decorative; the gate
   at (19,1) is the only link; Route 37→Ecruteak only crosses at x=8).
   Route-level knowledge keeps accruing in PROGRESS.md — mapgraph should
   ingest it.
8. **Tool/emulation budget**: a savestate BFS burns ~1-2s per probe with
   battles; cap searches to one floor, and remember the shell timeout is
   clamped to 10 minutes regardless of the requested value.

## Concurrency notes

Multiple agents edit trek.py/battle.py live. I hit a broken import
(IndentationError) and a mangled best_move mid-run; both were the other
agent's half-saved edits and both healed within a minute. Before deep
debugging an "impossible" traceback, re-check whether the file just
changed under you (`git diff`, re-import). Coordinate objectives through
PROGRESS.md's Active sessions table — an ox-alpha session duplicated the
Whitney objective from a stale fork this session; claim rows early and
mark competitors' rows redundant when you finish first.
