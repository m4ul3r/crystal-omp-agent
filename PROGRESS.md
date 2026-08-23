# PROGRESS — Pokémon Crystal run

_Last updated: session of Aug 23 2026 (mart_buy primitive + 2nd party member)._

## Where we are

- Checkpoint to resume from: **`saves/two-mon.state`** (frame 389033)
- Position: ROUTE_31 grass (10,13)
- Party: CYNDAQUIL "AA" L14 40/40, **POLIWAG L4** (fresh catch)
- Money: ~₽357 · Badges: ZEPHYR
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
| ox-alpha (visibility) | fresh boot -> starter -> early New Bark progress | `visibility.state` |
| ox-alpha (p9) | done: mart_buy + step_hold + 2nd party member (`two-mon.state`) | `saves/ox-alpha.state` |

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
| default.state | 106033 | pre-Journey fork point (Route 29) |
| joey.state | 198202 | Joey beaten, ended inside Violet gate |
| violet-arrived.state | 176067 | Violet City, healed, L11 |
| gym-attempt.state | 279328 | p5's stalled badge attempt (superseded) |
| zephyr-badge.state | 296052 | ZEPHYR BADGE won, L14 |
| two-mon.state | **389033** | **current** — POLIWAG caught, party of 2, Route 31 |

## Gotchas discovered this run

(See AGENTS.md for the full list; newest here.)
- Battle menu cursor is `wMenuCursorY/X`, not `wMenuCursorPosition`
  (that one only writes on confirm) — cost a full debugging session.
- Move-selection menu has no literal "PP" text; detect it by ▶+move-name.
- Door/cutscene warps complete asynchronously AFTER the triggering step;
  always `settle()` before trusting map/pos (new gotcha #12 in AGENTS.md).
- RAM-injected party mons need nickname+OT bytes or text freezes on
  "Go!" — test-setup-only hazard.
