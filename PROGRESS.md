# PROGRESS — Pokémon Crystal run

_Last updated: session of Aug 23 2026 (harness upgrade session)._

## Where we are

- Checkpoint to resume from: **`saves/violet-arrived.state`** (frame 176067)
- Position: inside VIOLET_POKECENTER_1F at the nurse counter, fully healed
- Party: CYNDAQUIL "AA" L11, 33/33 HP
- Money: ₽3364 · Badges: none yet
- Bag: 1× POTION, no Poké Balls yet (balls come after delivering the egg —
  already done; see `egg-delivered.state`)

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

Sprout Tower (door at Violet City (23,5)): grind to ~L12 first if needed,
fight through the tower sages, beat ELDER LI (BELLSPROUT) for HM01 Cut.
Then ZEPHYR BADGE at Violet Gym (door (18,17), FALKNER, bird types —
Smokescreen/Leer won't cut it; keep TACKLE spam, consider grinding to L13+).
Checkpoints: `sprout-elder.state`, then `zephyr-badge.state`.

## Active sessions

| session | owns | working state |
|---------|------|---------------|
| tower agent | Sprout Tower -> Elder Li | `joey.state` (frame 238979, SPROUT_TOWER_2F) |
| ox-alpha | unassigned — forked from violet-arrived @176067 | `saves/ox-alpha.state` |

Rule: never write another session's working state or a milestone checkpoint;
promote progress under NEW filenames. See AGENTS.md "Multiple agents".

## Harness state (as of this session)

- Audit-and-fix session: `catch()` was broken since birth (undefined
  `max_frames` NameError + KO'd target when out of balls) — now throws up to
  `max_balls`, flees once dry. `grind` stops mid-pace on level/HP thresholds
  and no longer spins when blocked. trek CLI validates args (no tracebacks),
  accepts `<state>` optionally (`''` or omitted = default; forks must end in
  `.state`), and gained a `catch` leg. `-q/--quiet` now accepted after the
  subcommand too. Raw reads of $D000-$DFFF without a bank raise instead of
  returning wrong-bank garbage.
- Smart battles implemented and live-tested: `Driver.fight()` picks the best
  damaging move (power × type chart × STAB × accuracy from ROM data),
  auto-POTION below 30% HP (verified mid-battle), flees hopeless wilds
  (verified). ~3.5k frames per wild battle.
- Out-of-battle item use works end-to-end (`use_item('POTION')` healed
  21→26 and returned to a clean field).
- NOT yet exercised with real inventory: ball throws, party switching
  mid-battle (`switch_to`). Primitives are tested; first real use, watch it
  on a fork.

## Checkpoints

| file | frame | meaning |
|------|-------|---------|
| healed-1.state | 39549 | after first Pokecenter heal |
| pre-rival.state | 68317 | before rival ambush on Route 29 |
| egg-delivered.state | 81004 | egg handed to Elm |
| default.state | 106033 | pre-Journey fork point (Route 29) |
| joey.state | 198202 | Joey beaten, ended inside Violet gate |
| violet-arrived.state | 176067 | **current** — Violet City, healed, L11 |

## Gotchas discovered this run

(See AGENTS.md for the full list; newest here.)
- Battle menu cursor is `wMenuCursorY/X`, not `wMenuCursorPosition`
  (that one only writes on confirm) — cost a full debugging session.
- Move-selection menu has no literal "PP" text; detect it by ▶+move-name.
