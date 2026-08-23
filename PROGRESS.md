# PROGRESS — Pokémon Crystal run

_Last updated: session of Aug 23 2026 (mart_buy primitive + 2nd party member)._

## Where we are

- Checkpoint to resume from: **`saves/two-mon.state`** (frame 389033)
- Position: ROUTE_31 grass (10,13)
- Party: CYNDAQUIL "AA" L14 40/40, **POLIWAG L4** (fresh catch)
- Money: ₽857 · Badges: ZEPHYR
- Bag: ~9 Poké Balls, 3 POTIONS

## Story progress

1. Started game, picked Cyndaquil ("AA"), rival named "AA"
2. Healed at Cherrygrove Pokecenter (`healed-1.state`)
3. Rival ambush crossed on Route 29 (`pre-rival.state`)
4. Mystery Egg received from Mr. Pokemon + Pokedex; egg DELIVERED to Elm
   (`egg-delivered.state`, frame 81004). Aide gave 5 Poké Balls?? — NOT
   verified in bag; `wNumBalls` reads 0 at default.state. If balls are
   missing when needed, re-check the aide scene trigger.
5. Currently retracing west along Route 29 (catch-tutorial cutscene fires
   around x=53 — may already be done).
6. **YOUNGSTER JOEY DEFEATED** (`joey.state`): beat his RATTATA L4 with
   CYNDAQUIL L9->L10. Prize +₽64 (3300 -> 3364).
7. **VIOLET CITY REACHED** (`violet-arrived.state`, frame 176067): walked
   Route 30 -> Route 31 -> gate -> Violet City, healed at Pokecenter
   (CYNDAQUIL L11 33/33). Several wilds won en route.
8. **ZEPHYR BADGE WON** (`zephyr-badge.state`, frame 296052): both Bird
   Keepers (Abe, Rod) beaten by the stalled p5 session, then Falkner
   finished off by this session's new `talk_to` primitive. Lost the first
   attempt at L13/17HP (whiteout), re-entered and won cleanly at L14.
9. **SUPPLIES + 2ND PARTY MEMBER** (`two-mon.state`, frame 389033): bought
   balls/potions with the new `mart_buy` primitive (Violet Mart clerk at
   (1,3)), walked to Route 31 grass, caught POLIWAG L4.
10. ROUTE 32 OPENED + QUILAVA (`director-cave-entry.state`, frame 459287):
    accepted Togepi egg from Elms aide in Violet Pokecenter (4,3) —
    REQUIRED to unseal Route 32 descent (scene var; see gotcha below).
    Party: QUILAVA L16 (evolved from Cyndaquil en route), POLIWAG L4,
    TOGEPI egg. ₽1208.
11. **HIVE BADGE WON** (`director-badge-1.state`, frame 888158): Slowpoke
    Well cleared via Kurt sequence (Kurt's house talk despawns well guard
    — setevent EVENT_AZALEA_TOWN_SLOWPOKETAIL_ROCKET), 4 Rockets beaten,
    Bugsy defeated by QUILAVA L21 (learned QUICK ATTACK mid-fight).
    Party: QUILAVA L21, POLIWAG L4, TOGEPI egg. ₽4988.
12. ILEX PUSH STALLED AT RIVAL (`director.state` frame 891198): Azalea
    west crossing at (5,10)/(5,11) is planner-blocked (scene-var
    conservatism; physically safe at scene NOOP=0 — crossed manually via
    press). Stepping further west triggers the AZALEA RIVAL BATTLE
    cutscene mid-goto -> travel flushed dialog mid-cutscene, entered
    battle mangled, QUILAVA dropped 0/64 in a loss-loop (fight()
    re-entered 11x instead of letting whiteout resolve). Rolled back
    cleanly via per-cycle persistence. NEXT: trigger rival deliberately,
    advance cutscene fully, then fight fresh; OR add cutscene-aware
    pre-battle dialog handling.

## Route notes

- Route 29 west exit is at TOP-left (y=6-7); x=0-3 is wall at y>=8.
  goto(2,6) then walk L*3.
- Cherrygrove north exit: goto(16,0), walk U*3 -> Route 30 south end (6,53).
- Trainer sight-lines do NOT auto-trigger via BFS pathing reliably; talk to
  trainers directly (stand adjacent, face them, A, flush_dialog, then POLL
  up to ~2000 frames for wBattleMode before giving up — the transition is
  slow).
- The Route 31->Violet gate is finicky: from Route 31 side stand on the
  door cells (4,6)/(4,7), walk L to enter (warp fires sideways, not from a
  standing start). Inside, goto(0,4) then walk L*3 into Violet City.
- Violet Pokecenter door: goto(31,25), walk U*2.

## Next objective

Head south through Route 32 (Violet City south exit around (19,42) area —
check maps/VioletCity.asm warps) toward the Route 32 Pokecenter, then
UNION CAVE. Grind POLIWAG up alongside Cyndaquil. Next badge is HIVE
(AZALEA, far south through Ilex Forest) — the journey legs need extending
(`to_azalea`). Checkpoints: `route32.state`, `union-cave.state`.

## Active sessions

| session | owns | working state |
|---------|------|---------------|
| tower agent | Sprout Tower -> Elder Li | `joey.state` (frame 238979, SPROUT_TOWER_2F) |
| ox-alpha (visibility) | L14 CYNDAQUIL, staging for Falkner rematch #3 | `visibility.state` |
| ox-alpha (p9) | done: mart_buy + step_hold + 2nd party member (`two-mon.state`) | `saves/ox-alpha.state` |
| director | Union Cave traverse -> Azalea/Hive badge | saves/director.state |
| claude-lex | DONE: Ilex Forest cleared -> ROUTE_34_ILEX_FOREST_GATE | `saves/claude-lex.state` (frame 973763, gate (4,5)) |

Rule: never write another session's working state or a milestone checkpoint;
promote progress under NEW filenames. See AGENTS.md "Multiple agents".

## Harness state (as of tooling round 2)

- Round 1 audit fixes (catch(), grind early-exit, CLI validation, banked-WRAM
  reads) — see commit history.
- **Round 2 additions (all live-tested on disposable forks):**
  - `Driver.talk_to(x,y)` + `trek talk X Y`: walks adjacent (or across a
    counter — nurse case) to any NPC, faces them, talks; fights trainer
    battles that trigger and polls the slow sight-line transition. Verified
    vs nurse (plain dialog) AND Falkner (badge fight won).
  - `Driver.settle()`: door/cutscene warps finish asynchronously AFTER the
    step that triggered them — walk/goto/talk now settle before reading the
    map. This race cost p5 its gym attempt ("in: VIOLET_CITY" while actually
    inside the gym).
  - `goto` no longer deadlocks on NPCs: distinguishes "statically blocked"
    (gives up immediately with `no static path`) from "NPC in the way"
    (threads through NPC cells; step_dir handles bumps).
  - `emu.write(addr|sym, data)` for test setup; banked-WRAM guarded like
    read.
  - Item lookup normalized: repo names item 5 `"# BALL"` (POKé glyph);
    callers say `"POKE BALL"` — both work now. This bug made ball lookups
    impossible before.
  - Verified with real inventory: mid-battle `switch_to` (won a wild after
    switching) and real ball throw via `catch()` (`[caught]` on POLIWAG).
  - Gotcha learned: RAM-injected party members MUST also get nickname+OT
    bytes written (`wPartyMonNicknames`/`wPartyMonOTs`, 11B slots) or the
    text engine hard-freezes on "Go! ?????".
- **Round 3 additions (this session, all live-tested):**
  - `Driver.mart_buy(x,y,item,qty)` + `trek mart X Y ITEM QTY`: talks to the
    clerk, scrolls the shop list (é-safe name matching), sets quantity by
    polling the `×NN` picker (UP=+1 RIGHT=+10), confirms, exits with
    B-only presses. Verified: 4× POKE BALL and 3× POTION, exact money math.
    NOTE: never flush_dialog near an open shop list — blind A presses buy
    single items.
  - `Driver.step_hold` / `_step`: door warps ONLY fire if the direction is
    held through the whole step+transition; step_dir's early release skips
    them silently. walk/goto now hold automatically on warp tiles. This is
    why gym/pokecenter doors "didn't work" earlier.
  - `_norm_item` fixed: é was uppercased to É before the replace, so screen
    text "POKé BALL" still didn't match; also é→E mapping added.
  - `watch.py` live visualizer (round 3, this session): stdlib-HTTP
    dashboard on :8123 — screenshot, party/battle state, text screen,
    colored canvas collision-map (@/N/warps/ledges/grass/water), live vs
    idle dot (state-file mtime age), and an activity feed that diffs
    consecutive snapshots into events (map entry, battle start/end,
    level-ups, new party members, money delta, badges, new checkpoints in
    saves/). Read-only: safe to point at any session's working save.
    Run: `.venv/bin/python watch.py --state saves/<agent>.state`.
    Fixed en route: /shot.png used to tick the emulator without reloading
    (screen drifted from disk while idle) and called a nonexistent
    PIL `.update()` — screenshots never worked before; both fixed.
- **Round 5 additions (this session, live-tested on disposable forks):**
  - **Cross-map routing** (`nav.find_route` + `trek goto X Y [MAP]`):
    MapData now parses `connection` lines from data/maps/attributes.asm and
    `warp_event` tables from maps/*.asm, so BFS routes between maps.
    Landing math derived from EnterMapConnection + the connection macro
    (verified: Cherrygrove (16,0) U -> Route30 (6,53)). Warp tiles are
    never mid-path cells; single-map find_path now refuses to cross doors
    (fixes walking back over the lab door after exiting).
  - Live-verified: New Bark -> Route29 -> Cherrygrove end-to-end (fought
    3 wilds en route), Route31 -> gate -> exact cell, Route31 -> Route30
    south end. goto replans after every warp because step_hold drifts ~2
    cells past the modeled landing (new gotcha #14 in AGENTS.md).
  - `trek` now REFUSES to run implicitly on saves/default.state unless
    CRYSTAL_ALLOW_DEFAULT=1 -- a session ran `walk` without a state arg
    and silently mutated the shared fork point. default.state was NOT
    damaged by the audit run (its steps were blocked at (43,17); only ~300
    idle frames drifted, frame 106033 -> 106506).
  - Note: `trek flush` already existed (flushes dialog to quiet); it now
    prints its outcome ("done"/"battle"/"timeout").
- **Round 6 additions (this session): battle-watch latency**
  - Diagnosis: battles were never slow in the driver — wild entry measured
    at 504 frames trigger→menu (0.04 s wall), full battle 2972 frames in
    3.8 s, raw emulation 16k fps. The "frozen battle" was the dashboard:
    watch.py advanced its preview ONE frame per /shot.png request (~1 fps
    playback), and trek only wrote the state file at leg end, so panels
    went minutes stale mid-leg.
  - Fixes: watch.py now reloads-then-ticks toward real time on each poll
    (240x, capped 1800 frames/request; also actually calls _reload() which
    its comment always claimed). trek now autosaves the working state after
    every battle, so watch tracks a live session within ~0.2 s of each
    battle ending. emu.save writes tmp-then-rename: viewers can no longer
    read a half-written savestate during saves.
  - Verified live: paced/fought on a watched fork — state.json age stayed
    0.1–0.3 s across battles, screenshots animate between 1 s polls,
    request latency ~40 ms.
- **Round 4 additions (naming, this session):**
  - `Driver.type_name()` + `catch(nickname=...)` / `trek catch NICKNAME`:
    types real names on the post-catch naming keyboard. Grid parsed from
    data/text/name_input_chars.asm; every move + A press verified against
    WRAM (cursor struct via wNamingScreenCursorObjectPointer -> VAR1/VAR2,
    and wNamingScreenCurNameLength) because the naming screen drops presses
    landing mid-animation. Gotchas: the control row (case/DEL/END) moves by
    ZONE not cell — navigate only on char rows; START snaps to END;
    'é'.upper() == 'É' bit us a second time.
  - Default with no nickname requested: the YES/NO prompt is now declined
    (B), so catches keep species names instead of junk 'AA'. Verified all
    three paths live: named ('BUBBLES' BELLSPROUT), declined
    ('BELLSPROUT' stays), legacy minimal still exits cleanly. Existing
    'AA'/'AAAAAAAAAA' names can't be fixed in-game (no rename primitive).

## Checkpoints

| file | frame | meaning |
|------|-------|---------|
| healed-1.state | 39549 | after first Pokecenter heal |
| pre-rival.state | 68317 | before rival ambush on Route 29 |
| egg-delivered.state | 81004 | egg handed to Elm |
| default.state | 106506 | pre-Journey fork point (Route 29) |
| joey.state | 198202 | Joey beaten, ended inside Violet gate |
| violet-arrived.state | 176067 | Violet City, healed, L11 |
| gym-attempt.state | 279328 | p5's stalled badge attempt (superseded) |
| zephyr-badge.state | 296052 | ZEPHYR BADGE won, L14 |
| two-mon.state | **389033** | **current** — POLIWAG caught, party of 2, Route 31 |
| starter.state | ~62800 | CYNDAQUIL received, New Bark |
| egg-mrpokemon.state | 296910 | MYSTERY EGG obtained, Oak dex, healed 26/26 |
| visibility.state | live | working state — next: Route 29 east -> Elm, cop scene |

## visibility run notes (fresh boot, Aug 23)

- Player "AAAAAA" (default-name mash artifact), CYNDAQUIL L9 28/28,
  ₽3300 (+300 = beat rival in Cherrygrove — fight fired mid-goto leg).
- MISSED: aide's free POTION (lab exit cutscene skipped); harmless.
- DISCLOSURE: an errant trek walk without positional state arg ran on
  and moved default.state ~4 tiles east on ROUTE_29 (frame 106063 ->
  106320). Fork point shifted; no battles fired.
- Harness gaps found: (1) nav.MapData ignores map-edge CONNECTIONS
  (attributes.asm) so BFS cannot cross town/route edges — exits need
  manual steps (New Bark exits WEST at x=0 y=8, not north!). (2) crystal
  input runs its sequence ONCE unless --until is given — use DSL repeat
  (A:8 .:45*50) for dialog mashing. (3) trek goto crashed once with
  ValueError 'ELMS_LAB' (leg/state parsing) — unreproduced.

## Gotchas discovered this run

(See AGENTS.md for the full list; newest here.)
- Battle menu cursor is `wMenuCursorY/X`, not `wMenuCursorPosition`
  (that one only writes on confirm) — cost a full debugging session.
- Move-selection menu has no literal "PP" text; detect it by ▶+move-name.
- Door/cutscene warps complete asynchronously AFTER the triggering step;
  always `settle()` before trusting map/pos (new gotcha #12 in AGENTS.md).
- RAM-injected party mons need nickname+OT bytes or text freezes on
  "Go!" — test-setup-only hazard.
- Route 32 gate: Cooltrainer scene at the top of ROUTE_32 blocks southward
  travel until ZEPHYR BADGE (`.DontHaveZephyrBadge` branch) — re-fires
  every crossing, pushes you back. Falkner FIRST, then Route 32.
- Follower-NPC corruption: interrupting his `follow PLAYER` cutscene with
  savestate saves left obj1 glued to us blocking every exit. Workaround:
  zero wObjectStructs slot via emu.write (18 bytes). If movement "eats"
  all inputs near an NPC, check for a follower first.
- In-game SAVE + fresh boot does NOT offer CONTINUE (PyBoy boots with
  empty SRAM; no battery/reset API in this PyBoy build). Savestates only.
- Dialog boxes render at varying screen rows — never grep a single row
  for '┌'; scan whole screen_text().
- Route 32 north descent is sealed by a re-firing coord-event cutscene
  at (18,8) until Elms-aide egg scene sets the map scene
  (VioletPokecenter1F.asm:33 setmapscene). Sequence-broken saves hit an
  infinite push-back loop; talk to the aide first.
- trek goto on a far warp cell can ping-pong across map exits
  (cross-map re-route); use direct goto onto adjacent warp cells or
  wait for fix.
- Level-up move-learning menus inside battle are invisible to
  observe()-digest rails: fight() wedged 150k frames on "forget a move to
  make room". Screen-decode diffing (autopilot `screen` cmd) + raw A
  presses drove through it.
- coord-event blocking is conservative: cells whose scene token != live
  scene var are safe to walk but planner still seals them when it can't
  prove otherwise (AzaleaTown neck). Manual micro-step (press seq)
  bypasses planning safely once safety is confirmed from maps/*.asm.

## Ilex Forest cleared (claude-lex fork, Aug 23)

`saves/claude-lex.state` (frame 973763): player in ROUTE_34_ILEX_FOREST_GATE
at (4,5), QUILAVA L22 67/67 knows CUT (replaced LEER; kept QUICK ATTACK/
SMOKESCREEN/EMBER). Forked from director.state at ILEX_FOREST (7,29).

Working sequence:
1. **Farfetch'd chase** is facing-sensitive: each of positions 1..9
   (wFarfetchdPosition, readable via emu.read_u8) has facings that send
   the bird BACKWARD (IlexForest.asm .PositionN branches read VAR_FACING).
   Driven table-style — per position an allowed (stand-cell, facing) list:
   p1 any; p2 not-DOWN; p3 not-LEFT; p4 not-UP; p5 ONLY DOWN (stand 28,30);
   p6 not-RIGHT; p7 UP/RIGHT only; p8 ONLY DOWN (stand 15,28);
   p9 UP/LEFT only. goto(stand) -> step_dir(face) -> A -> flush_dialog ->
   settle. Do NOT use talk_to (it picks its own approach cell).
2. Talk to charcoal master (5,28) -> HM01 CUT (shows as "H1 CUT" in the
   TM/HM pocket screen text, NOT "HM01").
3. Teach CUT via PACK; pick the forgotten move deliberately (deleted LEER).
4. use_cut-style flow at tree (8,25): stand (8,26) face UP, START ->
   POKéMON (Menus.select_label('POKéM') — 'POK' matches POKéDEX) ->
   Quilava -> CUT row -> A. Tree clears; walk through.
5. goto(1,5) warps into ROUTE_34_ILEX_FOREST_GATE.

Bugs in the uncommitted trek.py use_cut (still unfixed there):
- `_teach_hm01` aborts if ANY party row shows "NOT ABLE" — false positive
  when non-lead mons can't learn CUT (trek.py:842-847). Should check only
  the cursor row.
- `_teach_hm01` force-forgets the FIRST move on a 4-move mon (trek.py:848).
- `_teach_hm01`'s B-B exit leaves the START menu OPEN — movement then
  silently blocks (gotcha 7) and the stray menu gets baked into saves.
- `use_cut`'s START->POKéMON nav assumes the cursor starts on POKéDEX
  (trek.py:904 single D press); the START menu REMEMBERS the last cursor
  slot, so after any PACK visit it opens ᴾᴷGEAR instead.
