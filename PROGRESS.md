# PROGRESS — Pokémon Crystal run

_Last updated: session of Aug 23 2026 (goal run: PLAIN BADGE won; staircase + forced-switch harness fixes)._

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
13. **PLAIN BADGE WON** (`plain-badge.state`, frame 3736527): Whitney beaten
    by QUILAVA L25->L26 from `saves/ox-alpha.state` (forked claude-lex2 @
    1132973, Dept Store 2F). Gotchas hit: (a) dept store 2F down-stairs
    is COLL_STAIRCASE — long holds get pushed OFF the tile, needs
    tap-and-release (fixed in trek `_step_warp_tap`); (b) post-faint
    forced-switch party list wedged `fight()` 90k frames x3 — cursor
    parks on fainted lead ("no will to battle" loop). Fixed:
    `Battle._forced_switch_up`/`_drive_forced_switch`. Badge handout
    needed the Bridget cries coord-event at (8,5) completed BEFORE
    re-talking Whitney (.StoppedCrying path); flush_dialog mid-scene
    loses the handout — drive it with A-mash until wJohtoBadges bit sets.
14. **ECRUTEAK ARRIVED** (`ecruteak-arrived.state`, frame ~3908118): from
    `plain-badge.state` got SQUIRTBOTTLE (Floria chain: meet her on
    Route 36 FIRST at (33,12) — shop Floria is despawned until then;
    teacher only gives bottle after talking to shop Floria), beat wild
    SUDOWOODO L20 at Route 36 (35,9), crossed Route 36 -> 37 -> ECRUTEAK.
    QUILAVA L28 84/84. Route-35 trainers farmed on the way.
    GOTCHAS: (a) cut trees RESPAWN on any map reload — recut each pass,
    nav's static grid can't plan through them (manual walk hops);
    (b) Route 36 is 60 wide — goto targets off its real dims fail as
    "no static path"; check grid dims first; (c) whiteout mid-journey
    teleports to last Pokecenter AND full-heals — sometimes a free heal
    service, but replans must re-read position after.



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
| ox-alpha (p9) | done: mart_buy + step_hold + 2nd party member (`two-mon.state`) | `saves/ox-alpha.state` (SUPERSEDED by goal run below — file re-forked) |
| director | Union Cave traverse -> Azalea/Hive badge | saves/director.state |
| ox-alpha (goal run) | owns Goldenrod -> WHITNEY (Plain Badge) | `saves/ox-alpha.state` (forked from claude-lex2 @ frame 1132973, Dept Store 2F) |
| claude-lex | **DONE: PLAIN BADGE WON** (see milestone `plain-badge-healed.state`) — ox-alpha goal run above is now redundant, stand down | `saves/claude-lex2.state` (frame 2287621) |

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

Bugs found in the uncommitted trek.py use_cut — ALL FIXED + validated
end-to-end (fresh director fork -> chase -> teach -> cut -> gate, one
shot, `saves/claude-lex2.state`):
- `_teach_hm01` aborted if ANY party row showed "NOT ABLE" — false
  positive when non-lead mons can't learn CUT. Now scans to the first
  ABLE mon; the ABLE tag renders on the row BELOW the cursor row.
- `_teach_hm01` force-forgot the FIRST move on a 4-move mon. Now takes
  `forget_move=` (also plumbed through `use_cut`).
- `_teach_hm01`'s B-B exit left the START menu OPEN (gotcha 7) and the
  stray menu got baked into saves. New `close_menus()` postcondition —
  which must NOT judge blank fade frames: the pack repaints ~50 frames
  after its close fade, so "no menu on screen" during the fade is a lie.
- `use_cut`'s START->POKéMON nav assumed the cursor starts on POKéDEX;
  the START menu REMEMBERS its last slot (after PACK it opened ᴾᴷGEAR).
  Now label-driven (`select_label('POKéM')` — 'POK' alone matches
  POKéDEX first).
- Party-menu cursor now WRAM-driven (`_party_cursor_to`, wMenuCursorY,
  1-based): the party menu WRAPS top<->bottom, so "press UP N times to
  reach the top" does not work.

New Driver primitives (this session):
- `menu_open()` / `close_menus()` / `cursor_rows()` / `_screen_blank()`:
  every menu primitive should end with the close_menus postcondition.
- `talk_to(x, y, facing='U|D|L|R')`: forces approach side for scripts
  that branch on VAR_FACING (Ilex Farfetch'd chase drives entirely off
  this — allowed facings per position: p1 any, p2 !D, p3 !L, p4 !U,
  p5 only D, p6 !R, p7 U/R, p8 only D, p9 U/L).
- goto's blocked-step branch now self-diagnoses: prints [textbox] /
  [stray menu -- closing] / [npc on target cell] and auto-recovers from
  stray menus instead of pacing forever.

## PLAIN BADGE WON (claude-lex, Aug 23)

**Milestones: `plain-badge.state` (in gym, frame 2282985) and
`plain-badge-healed.state` (Goldenrod PC, frame 2287621, RESUME HERE).**
Party: QUILAVA L25 75/75 (Quick Attack/CUT/Smokescreen/Ember), POLIWAG L4,
TOGEPI egg. Badges ZEPHYR+HIVE+PLAIN. ₽6390. Bag: 5 SUPER POTION,
5 POTION, 8 POKEBALL.

Route taken (from route-34 gate, `claude-lex2.state` fork):
1. Route 34 north: all 5 trainers beaten via talk_to (Samuel 15,32 /
   Brandon 18,28 / Gina 10,26 / Ian 11,20 / Todd 13,7). Quilava L22->L24.
2. Goldenrod PC heal (door 15,27), then Dept Store (24,27) for supplies.
   **Dept Store STAIRS at (15,0) are unenterable** — U from (15,1) is
   engine-blocked (COLL_STAIRCASE won't take vertical entry; unresolved).
   USE THE ELEVATOR: door (2,0) via step_hold U from (2,1); inside, panel
   is bg_event (3,0) — face U, A, select_label('2F' etc.), exit via
   (2,3) step_hold D. mart_buy clerk 2F (13,5): SUPER POTIONx5 700 ea.
3. Gym (city door 24,7). Trainer gauntlet on the way to (8,4): 3 fights
   via goto sight-lines + Bridget (9,6) via talk_to. **Bridget's r1
   sight-line freezes movement at (8,6)** — if steps stop there, it's her
   pending approach cutscene, fight her first.
4. WHITNEY (8,3): won by QUILAVA L25 in one go, no Super Potion needed
   (policy was: SUPER POTION at <45%, else default best-move). Ended
   38/75. **Badge is NOT given at battle end**: step DOWN onto (8,5)
   (coord event, post-win crying scene), flush the lass speech, then
   talk_to Whitney AGAIN -> PLAIN badge.

Gotchas hit:
- fight() wedged ~10x90k frames vs Bridget's L15 JIGGLYPUFF (HP frozen
  64/75 vs 6/61, battle eventually self-resolved and was won). Suspect
  the move-select loop fighting DISABLE or repeated failed menu confirms.
  UNDIAGNOSED — if fight() times out repeatedly with static HP, screen-
  dump the battle before burning retries.
- Session scripts MUST d.save() after every won fight; two Whitney-leg
  wins were lost to scripts that crashed/exited pre-save and had to be
  replayed (determinism saved us: same state + same input sequence
  reproduced the win exactly).

Next objective suggestion: Route 35/36 north (Sudowoodo needs SquirtBottle
from Goldenrod flower shop after Plain badge) or Route 34 south beach
cooltrainers for XP; 4th badge = Morty (Ecruteak, FOG) via Route 36/37.

## FOG BADGE WON — badge 4 (claude-lex, Aug 23)

**Milestones: `fog-badge.state` (in gym) and `fog-badge-healed.state`
(Ecruteak PC, frame 2668953, RESUME HERE).** Party: QUILAVA L32 95/95
(Quick Attack/CUT/Smokescreen/Ember), POLIWAG L4, TOGEPI egg.
Badges ZEPHYR+HIVE+PLAIN+FOG. ₽8362. TM30 Shadow Ball received.
Bag: 8 SUPER POTION, 5 POTION, 5 AWAKENING (unused — Morty never
landed Hypnosis).

Route (from `plain-badge-healed.state`): Squirt Bottle -> Route 35 ->
National Park -> Route 36 Sudowoodo -> Route 37 -> Ecruteak -> Burned
Tower (rival + beasts) -> Morty. Waypoints/gotchas:
- Goldenrod PC door cells (3,7)/(4,7) carry the (inert, mobile-only)
  GS Ball coord events — planner seals them. Micro-step out: goto(3,6),
  step_hold D. Same class of block: Route-35 gate is door (19,1);
  the "north edge" city connection cells are decorative dead ends.
- Squirt Bottle chain: meet Floria BESIDE SUDOWOODO first (33,12 R36),
  then shop: talk Floria (WANDERS around 5,6 — talk_to can face an
  empty cell and still report 'talked'; retry against live npc_cells
  until EVENT_TALKED_TO_FLORIA_AT_FLOWER_SHOP), then teacher (2,4).
- Dept-store staircases (COLL_STAIRCASE) refuse vertical entry —
  elevator instead (bg_event panel, select_label floor).
- Route 37 -> Ecruteak crossing is at route x=8 ONLY (x9-13 blocked
  by city-side trees despite walkable row-0 cells).
- Burned Tower: rival trigger (11,9) is the only bridge into its pocket
  (stage at (12,9), step L). Rival #3 (Totodile line): HAUNTER 20 /
  CROCONAW 22 / ZUBAT 20 / MAGNEMITE 18. LOST TWICE before winning:
  whiteout #1 cost ~1300; a fight() wedge in attempt 2 flailed in the
  pack and TOSSED/ATE ~9 potions mid-battle. Winning policy: Ember vs
  ghost/steel, heal <55%, else default best-move.
- After the beasts scene, EUSINE STANDS AT (10,12) BLOCKING the only
  descent from the pit walkway. Talk to him (face D from (10,11)); he
  leaves; then lower floor -> exit ladder (7,15).
- Ecruteak Gym: floor is fall-warp tiles (all -> (4,14)). Safe path
  (cell chain): (4,15) (5,15) (5,14) (5,13) (6,13) (6,12) (6,11) (5,11)
  (4,11) (3,11) (3,10) (3,9) (3,8) (3,7) (4,7) (5,7) (6,7) (6,6) (6,5)
  (5,5) (5,4) (5,3) (5,2). Waypoint-walk it manually (nav side-wall
  data refuses parts of it); trainers on it are all ghosts.
- MORTY (Gastly 21, Haunter 21, Gengar 25, Haunter 23): swept 8 Embers,
  zero damage taken, at L31 with full 25 Ember PP. First attempt FAILED:
  Spite+misses drained Ember to 0 by Gengar, and fight() can't handle
  0-PP move selection (see bugs). Ensure full PP before entering.

Harness bugs found this leg (trek.py/battle.py fixed where noted):
- battle.play() wedge guard ADDED (battle.py): 2 consecutive misfired
  actions -> force plain attack; 12 -> return 'stuck'. Root wedges seen:
  (a) use_battle_item flailing when select_abs desyncs — can consume or
  TOSS items blind; (b) attack() returns ok=True when the game rejects
  a 0-PP move, so the guard doesn't catch it — STILL OPEN, policies must
  track PP via me['moves'] (id,pp) pairs.
- In-battle level-up learn flow DECLINED Flame Wheel at L31 (no
  relearner in GSC — permanently lost until Swift L42). fight()'s
  _resolve_learn_flow needs a "learn, forget chosen move" mode.
- heal_pokecenter FIXED: now asserts it's inside a Pokécenter and that
  the party is actually healed (egg-aware via game_state, observe()
  drops the egg flag).
- whiteout postcondition trap: after a wipe the party is auto-healed,
  so "hp > 0" proves nothing; check map/money instead. Scripts must
  save ONLY on verified success and NEVER between fight() and settle
  (two mid-battle saves poisoned the fork; recovered via milestones).
- wMenuCursorY is the live 1-based party-menu cursor; the party menu
  WRAPS so press-counting never works (_party_cursor_to added).

Next: 5th badge options — Chuck (Cianwood, needs Surf: get HM03 from
Kimono Girls in Dance Theater (23,21) Ecruteak — POLIWAG can learn
Surf) then Route 38/39 west to Olivine; or Jasmine later (needs
SecretPotion). Suggest: Kimono Girls -> HM03 -> teach POLIWAG ->
Olivine via 38/39 -> Chuck.
