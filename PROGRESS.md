## session claude pt10 - Radio Tower cleared, CLAIR BEATEN; badge 8 pending the Dragon Shrine (Aug 26 2026)

Still **7/8 badges** on the counter, but **Clair is beaten** -- the RISING
BADGE is handed over at the DRAGON SHRINE, which I could not enter (see
below). PANIC is a TYPHLOSION **L57** with CUT / FLAME WHEEL / SWIFT /
DYNAMICPUNCH. `saves/claude.state` == `saves/claude-clair-beaten.state`
(DRAGONS_DEN_B1F (19,22)). New milestones: `claude-warehouse`,
`claude-card-key`, `claude-radio-tower`, `claude-clair-beaten`,
`claude-whirlpool`.

### Blockers solved this leg

1. **BASEMENT KEY door** (#64): it is *used on* GOLDENROD_UNDERGROUND
   (18,6) -- stand at the door, press A. `grid_drift()` had already named
   the cell (static `0x71` door vs live `0x07` wall).
2. **Goldenrod switch-room shutters** (#65): read the door table out of
   the ROM source and **simulated all 7 switch positions offline** with
   `nav.set_cell` before touching a switch. Switches ADD 1/2/3 (a sum, not
   a bitmask) and door states PERSIST, so no single position works but
   **position 5 then position 6** does. Simulation predicted 40 steps; the
   walk then worked first try.
3. **CARD KEY** from the rescued Director in the warehouse, then the 3F
   card-key slot (the shutter opened and `sync_grid()` picked up all three
   cells).
4. **Radio Tower cleared** -- executives on 5F, Archer's HOUNDOOM, and the
   real Director's **CLEAR BELL**. That sets `EVENT_CLEARED_RADIO_TOWER`,
   which is what brings Clair back to her gym.
5. **Blackthorn Gym's two-floor boulder puzzle**: pushed boulder 1 into
   (8,3) and boulder 2 into (2,5) on 2F, which drop as bridges on 1F; and
   boulder 3 into (8,7) after re-entering the map to reset a mis-push.
6. **CLAIR BEATEN** (#69). The harness recommender lost the first attempt
   (she Hyper Potions; the recommender answered with heals and switches --
   five turns of `115->115` and `FREE HIT`). A five-line policy won:
   **SWIFT every turn** (never misses, so SMOKESCREEN is irrelevant) and a
   Hyper Potion only under 40%.
7. **WHIRLPOOL taught** to TENTACOOL by driving the PACK by hand (#68) --
   `teach_tm` cannot drive the TM/HM list (`tmhm_use: party list never
   opened`, five attempts).

### The one open blocker (#70)

The RISING BADGE is at the DRAGON SHRINE, entered at DRAGONS_DEN_B1F
**(19,29)** whose only approach is **(19,30)** on a strip (x14-23, y30-31)
bounded by water at x12-13 / x24-25. Proved, not guessed:

- the courtyard above the door dead-ends (row 29 is solid but for the door;
  walked both columns to y28);
- the eastern water arm ends at a **buoy line** `0x27` (x24-27, y23) --
  buoys block surf, verified by bumping one twice;
- the western arm ends at `0x24` at **(10,20)** with a buoy column at x11;
- WHIRLPOOL is learned, GLACIERBADGE is the only badge
  `WhirlpoolFunction` checks (engine/events/overworld.asm:1100),
  `wPlayerDirection` reads `0x00` (DOWN) at (10,19) -- and the move still
  says **"Can't use that here."**

`TryWhirlpoolMenu` tests the TILE ID via `CheckWhirlpoolTile`, not the
collision byte, so `tile_at`'s 'whirlpool' (collision `0x24`) is answering a
different question: (10,20) is probably decorative current.

**Next session, in order:**
1. Find the REAL whirlpool tiles: read `CheckWhirlpoolTile`'s tile list and
   compare against the live tilemap on DRAGONS_DEN_B1F.
2. Or probe a land route into the strip from the south-east: rows 31-33,
   x26-29 are floor, separated from the strip by `0xb2` up-walls at row 32
   -- crossing those on foot is allowed (the new `_SIDE_WALL_NO_SLIDE` rule
   only forbids starting a SLIDE across the wall edge).
3. Shrine -> elder quiz -> Clair hands over **RISING**.
4. Route 27 / Tohjo Falls -> Victory Road -> **Elite Four** (needs
   WATERFALL: HM07 is already in the bag, unteached).

Harness notes worth keeping: clear `nav.blocked[map]` when a "no path"
contradicts the live grid (a stale mark from an old `goto` severed the
switch room's main corridor, #66), and read the `▶` row label instead of
counting presses in any menu that remembers its cursor (the dept-store
elevator picked CANCEL twice, #67).

## session claude pt9 - hideout cleared, GLACIER badge (7/8), Ice Path solved, Basement Key (Aug 26 2026)

**7/8 badges.** PANIC is a TYPHLOSION **L53**. `saves/claude.state` ==
`saves/claude-basement-key.state` (GOLDENROD_UNDERGROUND_SWITCH_ROOM_ENTRANCES
(5,24)). New milestones: `claude-rocket-passwords`, `claude-hideout-cleared`,
`claude-pryce-ready`, `claude-glacier-badge`, `claude-blackthorn`,
`claude-basement-key`.

### Done

- **Rocket hideout cleared.** The B3F west wing that pt6 called unreachable
  took two calls with the component graph: B3F comp2 --(27,2)--> B2F comp0
  --(3,2)--> B3F comp0. Collected all three passwords (SLOWPOKETAIL,
  RATICATE TAIL from the grunts, HAIL GIOVANNI from the Murkrow at (7,2)),
  opened both locked doors, beat the boss-room guard and the B2F executive
  pair alongside Lance, KO'd the three Electrodes -> **HM06 WHIRLPOOL** and
  `EVENT_CLEARED_ROCKET_HIDEOUT`.
- Both locked doors were opened with `grid_drift()`/`sync_grid()` doing
  exactly their job: the changeblock fired, drift reported the two cells,
  sync patched nav, and `goto` routed through the new doorway.
- **PRYCE beaten -> GLACIERBADGE** (the fisher blocking Mahogany's gym door
  vanishes the moment the hideout clears). Also got TM16 ICY WIND.
- **ICE PATH solved end to end** (see `FUCK_I_MESSED_UP.md` #56-#61) --
  HM07 WATERFALL picked up at 1F (31,7), one STRENGTH boulder pushed into
  its hole to create the B2F stopper, and the 6-hop floor chain walked to
  **BLACKTHORN CITY**.
- Radio Tower 1F-5F climbed; **BASEMENT KEY** obtained on 5F. Bought 9
  Hyper Potions in Blackthorn.

### Harness fixes this leg

- `MapData._enterable` now includes **ICE**. It was missing, so every ice
  tile was a wall for `_reach`: Ice Path 1F reported 81 reachable cells and
  "582 walkable cells NOT reachable" (real answer: 254 and the stairs are
  in view). Mahogany Gym was rendering four rows.
- `_SIDE_WALL_NO_SLIDE`: a side-wall tile refuses to START a slide across
  its own wall edge. Verified live on ICE_PATH_1F (28,10) `$b2`. Narrow on
  purpose -- Union Cave crosses `$b2` upward onto plain floor, which an
  existing unit test asserts.
- Tests: **627 passed, 0 failed.**

### Next, in order

1. **Goldenrod Underground shutters** (#63): the warehouse doors (22,10)/
   (23,10) are sealed behind switch-operated shutters nav cannot see.
   Switch `bg_event`s are (16,1), (10,1), (2,1) + emergency (20,11);
   state byte `wUndergroundSwitchPositions` = 01:d963. Flip, then
   `sync_grid()` and re-plan. Alternative route: GOLDENROD_DEPT_STORE_B1F
   (17,2) also warps into the warehouse.
2. **CARD KEY** at GOLDENROD_UNDERGROUND_WAREHOUSE (12,8) -> Radio Tower
   3F locked door -> 5F -> clear the tower (`EVENT_CLEARED_RADIO_TOWER`).
3. **CLAIR** returns to Blackthorn Gym only then (`maps/BlackthornCity.asm`
   :38,66) -> badge 8 (RISING, via the Dragon's Den errand).
4. Route 27 / Tohjo Falls -> Victory Road -> Elite Four.

**Navigation recipe for ice/maze floors** (goto's executor mis-drives ice,
#58): plan with `nav.find_path`, execute one step at a time, re-plan after
every move, retry a stuck step once with a 24-frame press. That drove the
whole Ice Path. And always model live obstacles as walls
(`nav.set_cell(map, x, y, 0x07)`) -- `observe()['npcs']` only sees sprites
near the player.

## session claude2 - OWNS fresh save -> starter -> Route 29 (Aug 26 2026)

Claiming: boot a FRESH save as `saves/claude2.state` (+ `.meta`), drive
power-on -> New Bark Town -> claim a starter from Elm's lab, then walk out
toward Route 29. Working state: `saves/claude2.state`. Touches neither
`saves/default.state` nor `saves/claude.state`.

Progress: fresh boot landed (bedroom checkpoint `saves/claude2.state`),
Mom Pokégear/DST chain answered, Elm errand accepted, **CYNDAQUIL L5
claimed** (nickname declined). Milestone: `saves/claude2-starter.state`
(ELMS_LAB, clean overworld, lead 19/19).

New gotcha (2x reproduced): pressing A on a lab ball opens the POKEPIC
viewer; screen_text decodes the Cyndaquil front pic as a 7x7 letter box
("AHOV:dk" — NOT a keyboard), and hook-injected `d.press` keys do NOT
register in this state (waitbutton never sees them) -> looks like a hard
wedge. Escape/carry-on via RAW PyBoy input (`d.emu.py.button_press('a')`,
tick, release); then dialog flows normally. Matches deepseek's note in
`backup/DEEPSEEK_PROGRESS.md`.

**OBJECTIVE COMPLETE (20:54): standing on ROUTE_29 (59,8), lead CYNDAQUIL
L5 19/19, 1x POTION in bag, no balls yet (aide gives them later per
script). Milestone: `saves/claude2-route29.state`. Next session owns
Route 29 -> Violet City.**

Session gotchas beyond the pokepic one above:
- Mom's Pokégear intro is a 4-prompt chain: "SUNDAY, is it?" YES ->
"Is it Daylight Saving Time now?" YES -> "N AM DST, is that OK?" YES ->
"Come home to adjust your clock for DST" NO. `d.resolve_choice` silently
fails on these ("no choice cursor settled") — answer with raw presses.
- Elm's MustSayYes favor prompt opens while older paragraph text is still
the last visible page ("that I recently caught."); don't keyword-match
the question text, just answer YES on any choice during his intro.
- `step_dir` keys are U/D/L/R only (W/N KeyError).
- Lab exit: nav blocks rows y>=8 ('!' live-blocked aide cells); walk down
through (4/5,8) manually, drain the aide POTION scene at y=8, then door
warp (4,11)/(5,11) fires normally from below.


## session claude pt8 - the live feed was dead; landed it, suite green (Aug 26 2026)

Harness-only. Still 6/8 badges, `saves/claude.state` untouched.

**The "2 pre-existing `test_live_feed` failures" every pt1-pt7 entry
reported were a missing 12-line hook, not noise.** `crystalagent/live.py`
is a complete frame publisher that needs an emulator slicing its own
`tick()`; `crystalagent/emu.py` had `tick()` = one batch, no observer. So
the whole watch pipeline -- documented in AGENTS.md and HANDBOOK.md, used
by `watch.py` and `scripts/newgame_bedroom.py` -- could never attach.

Landed:
- `Crystal.observe(obs)` + sliced `tick()`: `obs.slice_frames` chunks,
  `obs.due()` decides whether the chunk renders, `after_slice(n, rendered)`
  reports what PyBoy did, never overshoots. No observer = old behaviour.
- `Driver(state, live={...})`, `Driver.live_attach()/live_detach()`.
- `paths.LIVE_DIR` (`$CRYSTAL_LIVE_DIR`, default `crystal-agent/live/`),
  referenced by `feed_paths()` and never defined.
- `live_attach` registers an atexit detach (a handler attached at
  interpreter shutdown printed `Error in sys.excepthook` x3).
- `LiveFeed.detach()` publishes a final frame, so a leg that ends after a
  menu no longer leaves the viewer on that menu.
- deleted `scripts/newgame_claude.py`: the fork exists only because
  `newgame_bedroom.py` was broken, and it now runs as documented.

**Tests: 627 passed, 0 failed** -- green for the first time this run.
Verified live: `newgame_bedroom.py --state saves/livetest.state --name
LIVE` boots from power-on to control in PLAYERS_HOUSE_2F (3,3) while
publishing `live/livetest.{png,json,jsonl}`; the published frame during
the run is the naming keyboard, which is never savestated and which the
old re-simulating viewer could not show.

To watch a run now:
```sh
.venv/bin/python watch.py                       # http://127.0.0.1:8123/
.venv/bin/python -c "import trek; d=trek.Driver('saves/claude.state', \
    live={'fps':12,'speed':2}); d.travel('MAHOGANY_TOWN')"
```

## session claude pt7 - the map view was accurate and INCOMPLETE; fixed (Aug 26 2026)

Harness-only, no game progress. Still 6/8 badges, `saves/claude.state`
unchanged (TEAM_ROCKET_BASE_B3F (16,1)).

**Question asked: is the map/screen view accurate? Answer: the DATA is
byte-exact; the PICTURE hid half the map.**

`nav.grid()` decodes `maps/<Name>.blk` + tileset collision statically. I
built the same grid from the engine's own live block map
(`wOverworldMapBlocks`: 3-block border all round, `stride = wMapWidth+6`,
block (bx,by) at `(by+3)*stride+bx+3`) and diffed: **0 mismatched cells
across all 53 `saves/claude-*.state`**, plus BURNED_TOWER_1F entered live
after the beasts, SPROUT_TOWER_3F, ROUTE_32 and TEAM_ROCKET_BASE_B3F.
Only `changeblock` cells can ever drift, and `nav.conditional()` already
names them in advance (BURNED_TOWER_1F: exactly (7,15) and (10,9)).

But `map_view()` is a **reachability** render: everything unreachable drew
as a SPACE, indistinguishable from wall. So B3F's 57-cell western wing --
which holds the rival and boss `coord_event`s -- rendered as void, and
pt6 spent ~25 calls rediscovering it from raw collision hex. The legend
even had `(" ", "unreachable from here")` and the renderer filtered that
line out (`if g != " "`).

### Changed

- `render_map_view`: unreachable components draw `,` (walkable) and `o`
  (their warps). Cells in the player's own component are untouched, so
  water-without-SURF still stays blank rather than claiming reachability.
- New `offregion:` annotation line per unreachable component: cell count,
  bbox, and **how to get in** -- its warps, or the `changeblock` when no
  warp touches it. Capped at 4 doors/4 lines; door-less components under
  8 cells draw but are not annotated.
- New `Driver.live_grid()` / `grid_drift()` / `sync_grid()`. `sync_grid()`
  pushes live drift into nav via `set_cell`, so pathing gets it too --
  on B3F, poking the Giovanni door merged 3 components into 2.
- `map_view()` prints a `DRIFT:` line when live and decoded disagree.
- Tests: 2 new in `tests/unit/test_map_interface.py` (a wing must draw and
  name its entrance; a decorative island draws but earns no line);
  `FakeNav` borrows the real `region_map`/`regions_at` so it cannot drift
  from `MapData`.

Tests: unit lane **625 passed** at the time, plus the 2 `test_live_feed`
failures I was still calling pre-existing (pt8 shows they were a missing
emu hook, now fixed: 627/0).
The integration lane cannot run in this checkout at all --
`tests/integration/conftest.py:23` forks `claude_saves/wren-*.state` and
there is no `claude_saves/` here (16 ERRORs, pre-existing). Verified
instead by sweeping all 53 states live: 0 render errors, 0 drift.

Also: `./crystal --state S screen --png FILE` writes the real framebuffer
and is the only surface that shows the room as a player sees it. Unused
for this entire run out of misplaced distrust of the TEXT screen decode.

## session claude pt6 - Lake of Rage cleared; parked inside the Rocket base (Pryce is gated) (Aug 26 2026)

Still **6/8 badges**. PANIC is a TYPHLOSION **L48**. `saves/claude.state`
== `saves/claude-rocket-base-b3f.state` (TEAM_ROCKET_BASE_B3F (16,1)).
New milestones: `claude-mahogany`, `claude-red-gyarados`,
`claude-rocket-base-b3f`.

### Why Pryce is not done

**The Mahogany gym is hard-gated by the Rocket hideout.** The fisher at
MAHOGANY_TOWN **(6,14)** sits on the only approach to the gym door (6,13)
and is guarded by `EVENT_MAHOGANY_TOWN_POKEFAN_M_BLOCKS_GYM`
(maps/MahoganyTown.asm:269). That event is SET by
**`RocketBaseElectrodeScript`** on B2F -- the same script that gives
**HM06 WHIRLPOOL** and sets `EVENT_CLEARED_ROCKET_HIDEOUT`. No hideout, no
Pryce, no shortcut.

### Done this leg

- Flew Olivine -> Ecruteak, surfed/walked Route 42 -> **Mahogany**.
- **Lake of Rage**: surfed to (18,22), beat the shiny **RED GYARADOS**
  (L30) -> **RED SCALE**; talked to **Lance** at (21,28) and agreed to help
  (he flies to Mahogany).
- Entered the hideout behind the souvenir shop (MAHOGANY_MART_1F (7,3)),
  cleared B1F (camera grunts + the 22 exploding traps are trivial at L48),
  crossed B2F, explored much of B3F (PROTEIN, ULTRA BALL).

### The base's connectivity (the part that matters next time)

```
B1F (27,2) entrance; row 2 runs x1..x26; the left corridor below row 5 is
    x3..x5 (NOT x1); ladder to B2F at (3,14).
B2F arrive (3,14). ROW 12 IS SOLID WALL x1..x23, so the bottom block
    CANNOT reach B2F's left ladders (3,2)/(3,6). Row 16 (x7..x28) is the
    through-corridor -> column 27 -> (27,14) ladder -> B3F.
    (23,14)/(22,15) are $90 WALL, not the transmitter door.
B3F arrive (27,14). rows 12-13 are the only east-west link across x15;
    column 16/17 is the only climb from row 12 up to rows 8-10; row 8 and
    row 10 are both split at x15 ($90).
    => B3F's WEST half (x1..x14) -- which holds the rival coord_event at
    (8,10) and the boss coord_events at (10,8)/(11,8) -- is NOT reachable
    from the (27,14) arrival. It must be entered from B2F's LEFT ladders
    (3,2)/(3,6), i.e. via B1F's internal warps ((5,15)->B1F#4,
    (25,2)->B1F#3).
```

**Next session, in order:** take B1F's internal warp branch to reach B2F's
left block -> B2F(3,2)/(3,6) -> B3F west half -> rival scene at (8,10) ->
boss at (10,8)/(11,8) -> back to B2F's electrode room ->
`RocketBaseElectrodeScript` gives HM06 WHIRLPOOL and un-gates
**Pryce**. Then Clair needs WATERFALL from ICE_PATH_1F (31,7).

Also learned: **FLY is outdoor-only** (three failed attempts from inside
Olivine Gym), and the party menu REMEMBERS its cursor -- read the `▶` row
instead of pressing D a fixed number of times, or you select the wrong mon.

## session claude pt5 - FLY usable + the LIGHTHOUSE solved -> badge 6 (MINERAL) (Aug 26 2026)

**6/8 badges: ZEPHYR / HIVE / PLAIN / FOG / STORM / MINERAL.** PANIC is a
TYPHLOSION L46. All four field moves now WORK:
`CUT=PANIC, FLY=NOCTOWL, SURF=TENTACOOL, STRENGTH=GEODUDE`.

`saves/claude.state` == `saves/claude-mineral-badge.state` (OLIVINE_GYM,
party full HP). New milestones: `claude-fly-usable`, `claude-jasmine-met`,
`claude-secretpotion`, `claude-amphy-healed`, `claude-mineral-badge`.

### The cheap win: a bird for FLY

ROUTE_39 grass (x4-5, y20-27) has **NOCTOWL and PIDGEOTTO at L16**, one
screen from Olivine; both learn FLY. Two prerequisites I got wrong first:
the party must have a **free slot** (a caught mon with 6 in the party goes
to the box, so the catch "fails" from the harness's point of view) and the
bag must actually contain balls. GREAT BALLs (Olivine mart) caught a
full-HP L16 Noctowl on the second throw. `teach_tm('HM02','NOCTOWL')` ->
True. Flying works: Olivine -> Cianwood was one menu.

Freeing the slot needs the PC: its collision byte is `$93` (COLL_PC), so
find it with `[(x,y) for ... grid[y][x]==0x93]` -> Olivine PC at **(9,1)**,
stand at (9,2) facing UP. **Do not blind-press in there** (see
FUCK_I_MESSED_UP #45 -- I deposited the L44 carry).

### The lighthouse, understood

Read the **collision byte** under each warp event, not the warp list:
`$72 COLL_LADDER` = climbable, `$70` = door, **`$60 COLL_PIT` = one-way
fall**, `$00` = plain floor (the warp event exists but never fires -- these
are the landing pads I spent a whole session hammering).

```
1F (3,11)=72 up        (16,13)/(17,13)=00 pads
2F (5,3)=72 up         (16,13)/(17,13)=60 pit down
3F (13,3)=72 (9,5)=72  (16,11)/(17,11)=60 pit down
4F (3,5)=72 (9,7)=72   (8,3)/(9,3)+(16,9)/(17,9)=60 pit
5F (9,15)=72           (16,7)/(17,7)=60 pit down
6F                     (16,5)/(17,5)=60 pit down
```

**Design: climb the left/middle LADDERS, descend the right-hand PIT chain,
and take one deliberate pit-fall on the way up.**

UP: 1F(3,11) -> 2F(5,3) -> 3F: (4,3),(3,4), down x2 (a trainer sits on
(3,9)), (4,14), east along **row 14** to (14,14), up x14 to (14,5),
(13,4), **(13,3)** -> 4F: (13,2), west row 2 to (12,2) (NPC blocks (11,2)),
(12,3),(11,3),(10,3), **(9,3) = PIT** -> 3F strip B, **(9,5)** ->
4F(9,5), **(9,7)** -> 5F, down x9 to **(9,15)** -> 6F, up x9 to (9,9),
Jasmine at (8,8).
DOWN: 6F row 8 east to x17, north into the (17,5) pit, then D,D per floor:
5F(17,7) -> 4F(17,9) -> 3F(17,11) -> 2F(17,13) -> 1F. Four falls.

I also had the 3F "three disconnected strips" wrong: I printed the grid
only to row 13 and never saw that **rows 14-15 are a full-width corridor**.

### Jasmine chain, in order

Talk to Jasmine on 6F (she asks for medicine) -> Cianwood pharmacy gives
the **SECRETPOTION** (it is a script gift, not a purchase; before meeting
her the pharmacist is only a shop -- blind A presses there bought 6
Potions) -> back to 6F, talk to Jasmine again (she takes it and heals
Amphy) -> she returns to OLIVINE_GYM. **MAGNEMITE L30 x2 + STEELIX L35, all
steel: Flame Wheel one-shots the lot** (3 turns, no damage taken).

### Harness note worth keeping

On the 2F ladder `d.press()` stopped delivering input entirely (six calls,
no movement, `wScriptMode 0`, no textbox). Raw
`d.emu.py.button_press(...)` worked instantly, and raw taps drove the whole
lighthouse. A 6-10 frame raw tap = exactly one cell; use `step_hold` only
for warp entry.

**Next objective:** Pryce (badge 7). Mahogany is reachable by land/Fly;
the gym opens after the Lake of Rage -> Team Rocket hideout chain. Then
Clair (needs WHIRLPOOL from the Rocket base and WATERFALL from ICE_PATH).

## session claude pt4 - UNBLOCKED: badges 4 and 5 (FOG + STORM), SURF/STRENGTH/FLY (Aug 26 2026)

**Two more gyms cleared.** Badges: **ZEPHYR / HIVE / PLAIN / FOG / STORM**
(5/8). PANIC is a **TYPHLOSION L44** with FLAME WHEEL. HM01 CUT, HM02 FLY,
HM03 SURF, HM04 STRENGTH, HM05 FLASH all in the bag; CUT/SURF/STRENGTH are
USABLE (PANIC / TENTACOOL / GEODUDE).

`saves/claude.state` == `saves/claude-storm-olivine.state`: Olivine
Pokécenter, party healed, ¥19.5k. New milestones: `claude-beasts-released`,
`claude-fog-badge`, `claude-surf`, `claude-surf-usable`, `claude-olivine`,
`claude-cianwood`, `claude-strength`, `claude-storm-badge`,
`claude-fly-hm`, `claude-storm-olivine`.

### The two retractions that unblocked everything

1. **Burned Tower B1F does not "slide".** `step_hold` HOLDS the direction
   through the transition, so on open floor it keeps walking — that is the
   whole "one step moved me 5 cells" mystery. `step_dir`/a 4-frame tap
   moves exactly one cell. **Use `step_hold` only to enter a warp tile
   with a wall behind it; tap everywhere else.**
2. **Ecruteak Gym is pits, not warps.** `find_tiles('warp')` returns just
   the two door tiles; the 31 interior cells are tile kind **`pit`**. A
   floor-only BFS finds Morty fine (falling costs only the walk back), so
   a fall-aware walker that re-plans after each fall solves the gym.

### Route knowledge worth keeping

- **Beasts:** `coord_event (10,6)` on BURNED_TOWER_B1F is live from the
  start (scene 0) but only fires on a TAPPED entry. Fall in at 1F (10,9),
  tap up the corridor, and the release runs. Until it runs the exit ladder
  is turned to floor by the map callback, so B1F is a one-way trip.
- **Morty**: 4 ghosts, all with HYPNOSIS. Ember/Flame Wheel swept them in
  5 turns at L33; carry AWAKENINGs and put a `SLP -> ('item','AWAKENING')`
  branch in the policy.
- **SURF**: HM03 from the DANCE_THEATER gentleman at (7,10) after beating
  all five Kimono Girls ((0,2),(2,1),(6,2),(9,1),(11,2)). **Nothing in a
  fire/rock starter party can learn it** — I fished a TENTACOOL with the
  OLD ROD (from the Route 32 Pokécenter fisherman) on Route 32's shore.
  Old Rod = 15% chance of a Surf-capable species per cast (KRABBY /
  GOLDEEN / TENTACOOL by fishgroup, data/wild/fish.asm). Grass hunting for
  Psyduck/Marill/Wooper/Slowpoke failed ~80 encounters straight; **fishing
  is the reliable way to get a water mon.**
  Cast loop that works: `use_item('OLD ROD', field=True)` (needs the
  key-item pocket fix), then press A to clear "Oh! A bite!", THEN poll
  `d.battle()`.
- **Cianwood**: surf Olivine (10,13) -> south edge of ROUTE_40 -> ROUTE_41
  west edge -> CIANWOOD_CITY. `travel` has no water edges in its mapgraph,
  so drive the seams by hand.
- **Chuck's gym** needs STRENGTH (HM04 in OLIVINE_CAFE (4,3)) and the
  boulders must be pushed in this order, or you seal the only gap:
  (3,8) push U, (5,8) push U, then from (5,7) push L, then walk up column
  4 and around via (3,4)->(3,3)->(3,2)->(4,2). Boulders reset on map
  re-entry if you get it wrong. Activate Strength from START -> POKéMON ->
  GEODUDE -> STRENGTH after every map entry; the first directional press
  only turns you, so press twice per push.
- **HM02 FLY**: Chuck's wife, CIANWOOD_CITY (10,46) (she walks; also try
  (11,46)), after beating Chuck. Read her dialog page by page — the give is
  on the 11th page.

### What blocks Jasmine (badge 6) right now

The **Olivine Lighthouse stairs**: 1F `(16,13)/(17,13)` and 2F
`(16,11)/(17,11)` are listed as warps up but do not fire from any side,
tapped or held. The pair that does work ((3,11) then (5,3)) lands in 3F's
left column, which is walled off from the x12-17 region holding the 4F
stairs. Jasmine needs the SECRETPOTION, which the Cianwood pharmacist only
offers after you have met her at the top of the lighthouse.

Also still open: **nobody can learn FLY** (no bird in the party) — catching
a HOOTHOOT/PIDGEY would make every remaining trip trivial, and that is the
cheapest next win.

New details/retractions in `FUCK_I_MESSED_UP.md` #34/#35 (retracted),
#39-#43.

## session claude pt3 - fixed the P0 harness blockers; run parked in Ecruteak (Aug 26 2026)

**Still 3 badges. The Elite Four was not reached.** This leg spent its
effort where it was worth spending: the six defects that made team-building
impossible are fixed and verified live, and the run is parked one step
before Morty with a written route through him.

`saves/claude.state` == `saves/claude-ecruteak-ready.state`:
**ECRUTEAK_POKECENTER_1F, healed**, PANIC the QUILAVA **L32** with
**FLAME WHEEL**, GROWLITHE L13 (caught), GEODUDE L7, BELLSPROUT L5,
TOGEPI L5. Bag: 5 SUPER POTION, **5 AWAKENING**, SQUIRTBOTTLE, HM01, HM05,
TM31/45/49, 13 POKé BALLs. Money ~2500.
New milestones: `claude-flamewheel`, `claude-growlithe`, `claude-team5`,
`claude-ecruteak`, `claude-pre-tower`, `claude-ecruteak-ready`.

### Harness fixes landed (unit lane still 593 green)

1. **`observe()['party']` now reports `egg`** (schema updated), and
   `train`'s heal rail raises instead of looping when every non-egg member
   is already full. That kills the 30-round-trip infinite heal.
2. **`train` refuses to rotate in a mon with no damaging move**, naming it.
   No more 60-battle zero-exp runs.
3. **A policy that raises is logged** (`[fight] policy RAISED …`) instead of
   being indistinguishable from a decline.
4. **`_auto_action` always re-resolves the slot via `best_move()`** -- the
   fallback can no longer be a status move in slot 0.
5. **The stall guard never swaps a ball for an attack.** A failed capture
   changes no vitals by design; the old code KO'd the target on the third
   throw. Proof: a GROWLITHE was caught on the FIRST attempt after the fix
   (four attempts failed before it).
6. **Key items are addressable**: `bag_item_index(..., pocket="key")` reads
   `wNumKeyItems`/`wKeyItems` (flat id array), and `use_item` auto-detects
   and switches pockets. SQUIRTBOTTLE/SECRETPOTION/CARD KEY are usable now.

### The Ecruteak route, written down so the next session does not re-derive it

- **Morty is absent** until the Burned Tower is done
  (`MORTY, the GYM LEADER, is absent.`).
- The **only** working hole down is BURNED_TOWER_1F **(10,9)**; every other
  B1F warp in that map file is commented *"inaccessible, left over from
  G/S"*. The rival battle is the `coord_event` at **(11,9)**
  (maps/BurnedTower1F.asm:298), i.e. the tile beside the hole.
- His team: GASTLY L21 / HAUNTER L21 / GENGAR L25 / HAUNTER L23, all with
  **HYPNOSIS**. Bring AWAKENINGs (5 are in the bag) and a sleep branch in
  the policy -- the rival's HAUNTER put PANIC to sleep and that alone lost
  the fight.
- **ECRUTEAK_GYM is a hole maze**: 33 warps, 31 of them interior floor
  traps returning `warp_id 3`. `goto` ping-pongs on the door
  (`map seam crossed 3x`). It needs a floor-only BFS *plus* knowledge of
  which pads are the intended route.
- **Burned Tower B1F slides**: one `step_dir` can move you five cells
  ((10,9) -> (10,4)). Plan one step at a time there, re-reading position.
- After any battle, `step_dir` returns `blocked` in every direction for a
  while; `close_menus()` + `settle()` clears it.

### What is genuinely blocked (needs harness work, not more play)

- Levelling the bench: exp is split with the L32 carry, and
  **`party_swap(0, n)` fails** (`cursor never reached first row 0`), so the
  documented "trainee must be sole participant" recipe cannot be executed.
- Naming caught/hatched mons: the new-species Pokédex page still eats the
  nickname prompt, and the Goldenrod Name Rater flow does not open the
  party list.
- Hole/slide-tile maps (Ecruteak Gym, Burned Tower B1F, Goldenrod Gym) have
  no nav model.

Details and log lines: `FUCK_I_MESSED_UP.md` #33-#38 and the fix table
above them.

## session claude pt2 - push for the ELITE FOUR: got to 3 badges, NOT the E4 (Aug 26 2026)

**Objective was the Elite Four. It was not reached.** Honest stopping
point: **3 badges (ZEPHYR / HIVE / PLAIN)**, PANIC the QUILAVA **L29**,
HM01 CUT + HM05 FLASH + SQUIRTBOTTLE in hand, standing in the Goldenrod
Pokécenter. Five badges, Victory Road and four HM quests still to go.

`saves/claude.state` == `saves/claude-3badges-goldenrod.state`.
Milestones added this leg: `claude-togepi-egg`, `claude-union-cave`,
`claude-azalea`, `claude-well-cleared`, `claude-hive-badge`, `claude-cut`,
`claude-goldenrod`, `claude-pre-whitney`, `claude-r34-trainers`,
`claude-plain-badge`, `claude-squirtbottle`,
`claude-3badges-goldenrod`.

Party: PANIC (QUILAVA L29, CUT/LEER/QUICK ATTACK/EMBER), BELLSPROUT L5,
TOGEPI L5 (hatched on Route 34), GEODUDE L6. **The bench is dead weight
and that is the single reason the run stalled** -- see below.

### What got done

1. Route 32 is **gated on the Togepi Egg** (`Route32CooltrainerMStopsYouScene`,
   maps/Route32.asm:87). The aide is in the **Violet Pokécenter**
   (VioletPokecenter1F.asm:30), NOT Elm's lab -- I walked to New Bark and
   back before grepping the event.
2. Union Cave -> Slowpoke Well (all Rocket grunts) -> Kurt -> **Bugsy /
   HIVE badge** (Ember one-shots the whole gym).
3. Ilex Forest: herded the Farfetch'd via `wFarfetchdPosition` (read the
   var, then approach the exact cell with the exact facing -- see the
   position/facing table in maps/IlexForest.asm:20-330) and took **HM01
   CUT**. The cut tree out of the forest is the only one on the map:
   `[(x,y) for ... grid[y][x] == d._CUT_TREE_BYTE]` -> **(8,25)**.
4. Goldenrod: Dept Store 2F for Super Potions, **Whitney / PLAIN badge**
   (see the tactic below), then the SQUIRTBOTTLE chain (meet Floria at
   Route 36 (33,12) FIRST, then both flower-shop NPCs).
5. Sudowoodo: watered it (key items need the manual pack flow, `use_item`
   cannot see them) and **lost** -- PANIC fainted at Sudowoodo 11/58.

### The Whitney tactic that worked (write this down)

Three whiteouts before it. Miltank walls a lone Quilava because CUT
(physical, normal) does 1-2 damage to it and Rollout ramps. What won:
**EMBER every single turn, no items at all.** Ember did 22-26 a hit:
Clefairy in 2, Miltank in 3, PANIC survived on 5/73. Items are a trap here
-- every heal is a free Rollout turn, and in this build three consecutive
SUPER POTIONs restored **0 HP** (bug #24).

Then: **leave the gym and re-enter** before talking to her, or she just
cries at you forever (`SCENE_GOLDENRODGYM_WHITNEY_STOPS_CRYING`).

### Why it stopped, and what the next session must fix FIRST

No team. Every attempt to build one hit a harness wall:

- **`train()` is unusable (P0).** With an egg in the party it heal-loops
  forever (an egg reads 0 HP and reads as fainted); with the egg hatched it
  ran **60 battles for zero bench exp**, because it rotates in mons that
  cannot KO the local encounters. ~55 wasted Pokécenter round trips.
- **A crashed policy is reported as "declined" and the harness then picks
  SLOT 0 (P0)** -- for my party that is GROWL/LEER/DEFENSE CURL. Two
  whiteouts came from this.
- **`outlook()['moves']` is not in battle-slot order (P0)** -- indexing
  `('attack', i)` off it picked LEER as "best move". Score by NAME and map
  back to `battle_frame()['moves']`.
- **`use_item` cannot see the KEY ITEM pocket** -- SQUIRTBOTTLE,
  SECRETPOTION, CARD KEY all unusable through the API.
- **`catch`/hatch nickname prompts are declined** whenever a new-species
  Pokédex page opens first, so only the starter carries a persona name.

Full details, with the log lines, in `FUCK_I_MESSED_UP.md` #20-#32.

**Concrete plan for the next session** (in order):
1. Fix `train`: skip egg slots in the heal check; refuse to rotate a mon
   whose moves cannot damage the map's encounter table; stop healing when
   the party is already full.
2. Make policy exceptions loud, and make the fallback "best damaging move".
3. Add key-item support to `bag_item_index`/`use_item`.
4. THEN build a team: the Violet City house trades a **BELLSPROUT for an
   ONIX** (I have the spare Bellsprout) -- an on-level Rock/Ground wall is
   exactly what this run lacks, and Route 46/Union Cave Geodude/Onix can be
   levelled once `train` works.
5. Sudowoodo again (Ember is resisted -- use CUT or a levelled second mon),
   then Ecruteak: Morty.

## session claude - NEW RUN from a fresh boot: CLAUDE + PANIC, Zephyr Badge (Aug 26 2026)

A brand-new game, played to badge 1, plus five harness fixes found by
playing it. Persona: `persona_claude.md` (CLAUDE, Cyndaquil -> PANIC,
reads map data before moving, decides turns from `outlook()`). Every
mistake and every harness wart is written up in **`FUCK_I_MESSED_UP.md`**
(19 entries) -- read that before repeating this route.

### Where the run stands

`saves/claude.state` (working) == `saves/claude-zephyr-badge.state`:
**VIOLET_GYM (5,2), ZEPHYR badge, PANIC the QUILAVA L16**, plus an
unnamed BELLSPROUT L5 on the bench. Bag: 7 POTION, PARLYZ HEAL, 1 POKE
BALL, TM31, HM05 FLASH. Money ~1400.

Milestones (all forked from this run, `saves/`, none overwritten):
`claude-starter` -> `claude-egg` -> `claude-pre-rival` ->
`claude-rival-beaten` -> `claude-egg-delivered` -> `claude-r29-tutorial` ->
`claude-violet-arrived` -> `claude-violet-shopped` -> `claude-tower-1f` ->
`claude-tower-3f` -> `claude-tower-cleared` -> `claude-pre-falkner` ->
`claude-zephyr-badge`.

**Next objective:** Route 32 -> Union Cave -> Azalea. Get a real second
team member first (Geodude on Route 46 or Onix in Union Cave -- the
Bellsprout is a passenger), and note only ONE Poké Ball is left, so buy
balls in Violet before leaving.

### Harness fixes landed in `trek.py` (unit lane still 593 green)

1. **`Driver(state, fresh=True)`** now exists. `AGENTS.md` documented it
   and `scripts/newgame_bedroom.py` called it, but the constructor only
   took `state_path` -- a new game was impossible. `fresh=True` skips the
   savestate load. (That script also wants a `live=` kwarg and `d.live`,
   which did not exist then; I forked `scripts/newgame_claude.py` without
   it and called `crystalagent/live.py` a half-landed feature. **pt8
   corrects that**: the publisher was complete, only the emu observer hook
   was missing, the LiveFeed is landed and the fork is deleted.)
2. **Gift Pokémon can be named.** `flush_dialog` always confirmed a
   naming keyboard with the empty/default name, and `_pending_nickname`
   was only read by `fight`/`catch` -- so Elm's starter came out named
   "CYNDAQUIL" twice. New `_take_pending_nickname()` is used by both flush
   paths; arm `d._pending_nickname = "PANIC"` before triggering the gift.
   Verified live: `{'species':'CYNDAQUIL','nick':'PANIC'}`.
3. **`type_name` waits for the keyboard to finish drawing.** The naming
   window slides in; typing into the animation dropped every press and
   `wNamingScreenCurNameLength` read back 38.
4. **No more phantom naming keyboards.** `_naming_sig()` deltas alone were
   read as "a keyboard opened" on every cutscene and evolution (the bytes
   are a union), and `dismiss_keyboard`'s START+A then landed in the
   OVERWORLD -- opening the START menu and the Pokédex mid-scene, after
   which everything read "blocked". Now gated by
   `_naming_screen_plausible()` (type < 8, lengths <= 10) AND
   `_naming_opened()`, which waits ~80 frames for a real DEL/END render
   before confirming. The whole trek to Violet ran with zero spurious
   confirms afterwards.

### Play notes worth keeping

- `travel` is excellent across routes and useless indoors: it dies on
  `coord_event` cells, which nav marks sealed (`ELMS_LAB` rows 8,
  `ROUTE_29` x=53 catch tutorial). Step those by hand.
- Every door this session needed `goto` the adjacent cell +
  `step_hold(dir)`. `take_warp` mis-sides south-wall doors and strands.
- `mart_buy` cannot answer BUY/SELL/QUIT; shop by hand and verify from
  the bag, not from the `×NN` glyph (asked for 6 Potions, got 7).
- Sprout Tower's floors are three disconnected regions per floor: 3F is
  reached from 2F's SOUTH staircase `(10,14)`, which is reached from 1F's
  `(2,6)`, which is reached from 2F's `(17,3)`. The middle 1F staircase
  `(6,4)` only serves the north half.
- `d.tactics.recommend` drove every turn (Ember at x2 on the tower's
  Bellsprouts, one-shotting Falkner's birds). Sole participant the whole
  way: Cyndaquil L5 -> Quilava L16 with no grinding detours.

## session ox-alpha-integration - emulator-in-the-loop test lane `tests/integration/` (Aug 26 2026)

Harness-only: no game progress, no milestone saved; `claude_saves/`
verified byte-identical (sha256+mtime, all 120 files) across the run.
Closes `BRIEF_integration_lane.md`. Owns: `tests/integration/**`,
`tests/conftest.py`, `pyproject.toml` addopts, the HANDBOOK/AGENTS
test-running lines. Deleted `scripts/verify_hardening.py` --
its live checks are folded into the lane (accuracy/evasion/paralysis in
`test_outlook_live_stages.py`, Indigo Plateau escalation in
`test_goto_escalation.py`); no divergent copy left.

Tests: default `.venv/bin/python -m pytest tests` = unit lane only
(integration excluded via addopts `-m "not integration"`).
`.venv/bin/python -m pytest -m integration`: **15 passed, 1 xfailed,
~14 s wall** (boot ~1 s per forked savestate; scenarios pay it
explicitly). Milestone forks per scenario:

| scenario | fork |
|---|---|
| edge slide / map-edge connection | `wren-well-cleared` |
| take_warp standing-on-tile + stale coords | `wren-well-cleared` |
| goto escalation on lying grid + heal | `wren-pre-e4` |
| trapped-switch refusal | `wren-zephyr-badge` |
| missables() both directions | `wren-champion` + `wren-kanto` |
| map interface vs disassembly | `wren-kanto` |
| outlook under poked stages (promoted verify_hardening) | `wren-zephyr-badge` |

**NEW LIVE BUG found by the lane** (xfail'd strict in
`test_take_warp_entry.py::test_called_while_standing_on_the_tile_reenters`,
left for trek.py's owner): `_reenter_warp` tries sides in STEP order
(R,L,U,D); on Kurt's house exit door (3,7) called while standing ON it,
the horizontal retry derails position bookkeeping so U/D are never
attempted and the player is stranded at (0,7). A manual step-off-U +
hold-D warps out fine -- the door IS enterable along its axis.

Fixture contract (`tests/integration/conftest.py`): every scenario forks
`claude_saves/<milestone>.state` PLUS `.meta` into tmp_path, deletes
every created file in teardown even on failure, re-checks the source
digest per fork and all of `claude_saves/` at session end. Determinism
in use: pace()'s Python RNG is seeded, everything under that is the
emulator's own same-state-same-input guarantee (gotcha 9).

## session claude-missables - un-collected items + a map DATA interface (Aug 26 2026)

Harness-only: no game progress, no milestone saved, `saves/` untouched.
Closes both gaps in `BRIEF_missables_and_mapview.md` plus its BONUS.
Tests: **553 -> 593 green** (`.venv/bin/python -m pytest tests -q`, ~7 s).

### [feat] GAP 1 -- nothing ever said HM02 FLY was still in Cianwood

New `crystalagent/missables.py` parses every item source in `maps/*.asm`
and all three giver forms: `verbosegiveitem`, plain `giveitem`, and
`itemball` (HM07 WATERFALL is an itemball -- a gift-only parser would
lose an HM). 322 sources, 164 distinct items, 144 gifts + 178 itemballs.

Coordinates are the hard part: the give sits in a named script, and that
label is the second-to-last field of an `object_event`. Resolved
directly, then through up to 3 hops of `iftrue`/`sjump` callers -- which
is what turns the S.S. Ticket (`ElmGiveTicketScript`, reached from
`ProfElmScript`) from "somewhere in ELMS_LAB" into "Elm at (5,2)", and
the MASTER BALL too (two hops, via `ElmCheckMasterBall`). 25 of 322 stay
unresolved (vending machines, cutscenes) and are REPORTED as x=y=None,
never guessed.

New surfaces:
- `d.missables(kind='key'|'all')` -- live rows
  `{item, have, map, x, y, event, source}`. Obtained = the guarding
  `EVENT_GOT_*` flag, or the bag (bag wins either way: never nag about
  something already held). `kind='key'` is the game's own KEY_ITEM pocket
  (`data/items/attributes.asm`) plus the HMs.
- `d.field_moves()` -> `{'CUT': 'GATOR', 'FLY': None, ...}`.
- `d.status()` now ends with e.g.
  `missing: FLY(CIANWOOD_CITY 10,46) WATERFALL(ICE_PATH_1F 31,7) +18 more`.
- `crystal missables [--all]`.

**Live proof on a fork of `wren-storm-badge` (Cianwood Gym, the exact
moment the run walked away from Fly):**

```
missing: FLY(CIANWOOD_CITY 10,46) WATERFALL(ICE_PATH_1F 31,7) ... +18 more
field moves: CUT=GATOR FLY=- SURF=GATOR STRENGTH=GATOR ...
HM_FLY  CIANWOOD_CITY  10,46  EVENT_GOT_HM02_FLY  maps/CianwoodCity.asm:100
```

Poking `EVENT_GOT_HM02_FLY` on the fork made the row disappear, so both
directions are confirmed. Also confirmed by accident: `wren-champion`,
`wren-postgame` and `wren-rematch-clear` ALL have that flag clear -- the
Champion runs never had Fly at all.

### [feat] GAP 2 -- stop making the model count characters

`map_view()`'s grid sits behind a 5-column gutter and a two-row ruler, so
reading a coordinate off it means counting monospace characters; that was
miscounted three times in one session (Ilex wall x20, Olivine pier x=2 vs
x=3, Vermilion Port Passage exit found only by grepping `warp_event`).

- `d.tile_at(x,y)` -- one cell, through the SAME `_tile_kind` classifier
  `observe()['tiles']` uses (verified live: all four neighbours agree).
- `d.tiles_in(x0,y0,x1,y1)` -- a rect keyed by absolute coords.
- `d.find_tiles(kind)` -- every warp/water/grass/ledge/sidewall/blocked/
  floor/npc cell, sorted. This is the call that was missing.
- `d.exits()` -- warps AND edge connections with destinations. Live:
  VERMILION_PORT_PASSAGE returns (15,0) and (16,0) -> VERMILION_CITY,
  exactly the pair that needed a grep (maps/VermilionPortPassage.asm:23-24).
- `map_view()` keeps the art but prints an annotation block under it
  (`warps: (0,7)->POKECENTER_2F (3,7)->OLIVINE_CITY`, `edge:`, `npcs:`,
  `water:`, and a line saying to decide from the structured calls).
  Warps outside the cropped window are counted, never dropped.

### [feat] BONUS -- entering warps, crossing edges, money noise

- `d.take_warp(x, y)`: standing ON a warp never fires it. Steps off,
  re-enters, and **tries each side** -- a south-wall door only fires when
  entered going DOWN, and re-entering Cianwood Gym's exit sideways
  (off RIGHT, back LEFT) left the map unchanged live. Refuses up front
  when (x,y) is not a warp on the CURRENT map, listing the map's real
  warps: stale coords from the map you just left had otherwise routed the
  walk into POKE_SEERS_HOUSE. `travel` now uses it, including for the
  "already standing on it" case that used to fail the leg with
  `warp D at (3,41) ... (step result: blocked)`.
- `Driver._slide_edge`: when a map-edge connection's planned row does not
  fire (Azalea's east edge crosses at y=14, the plan said 13; Route 32 ->
  Violet at x=8), travel slides along the edge up to 6 cells each way and
  retries with a held step instead of failing the leg.
- The money guard now warns only on a DECREASE. `MONEY +216 ... movement
  must never spend money` on trainer winnings was a false alarm.
- `use_cut`'s "CUT row missing" path now `close_menus()` before raising:
  a refused field move leaves the party menu open and an open menu eats
  all movement input (gotcha 7).

### Live vs unit-tested

Live on forks (`claude_saves/miss-check.state`, `warp-check.state`, both
deleted): the status line, `missables()` both directions, `field_moves()`,
`crystal missables`, `exits()`/`find_tiles()`/`tile_at()` agreement,
`map_view()` annotations, `take_warp` from a distance, on-tile re-entry,
and the stale-coordinate refusal.
Unit-tested only: `_slide_edge` (needs a real off-by-one edge to walk),
the money-guard polarity, and the itemball/caller-hop parser rows (those
assert against the real .asm, so they are disassembly-verified rather
than emulator-verified).

---

## session claude-wren pt12 - FLY, the S.S. Aqua, and KANTO (Aug 26 2026)

Actual gameplay session. **WREN is in Kanto.** Milestones:
`claude_saves/wren-fly.state`, `wren-ssaqua.state`, `wren-kanto.state`.

### What got done

1. **HM02 FLY** - never collected in the whole Johto run, so every trip was on
   foot. Walked New Bark -> Cherrygrove -> Violet -> Union Cave -> Azalea ->
   (Ilex maze failed, rerouted) -> Violet -> Ecruteak -> Olivine, surfed Route
   40/41 to Cianwood, and took it from Chuck's wife at (10,46).
   Taught to REED the Pidgeot over WHIRLWIND (a 0-power move).
2. **S.S. TICKET** from Prof. Elm - the Olivine sailor checks
   `checkitem S_S_TICKET` (maps/OlivinePort.asm:162) and only Elm gives it
   (maps/ElmsLab.asm:414), post-Champion.
3. **Rode the S.S. Aqua**, solved the first-voyage sidequest, got the
   **METAL COAT**, and disembarked at Vermilion. GATOR L79 -> L80 on the way.

### The S.S. Aqua sidequest chain (for next time)

The ship will NOT dock on a first voyage until `EVENT_FAST_SHIP_FOUND_GIRL`.
Sleeping in the bed (bg_event at (7,1) of FAST_SHIP_CABINS_SW_SSW_NW, entered
from 1F (15,8)) only says "refreshed" until then. The chain:
B1F on-duty sailor (30,6) -> asks you to find his buddy -> the lazy sailor
spawns at (4,26) of FAST_SHIP_CABINS_NNW_NNE_NE (1F door (19,8)) and battles
you -> the granddaughter is at (2,25) of the CAPTAIN'S cabin, which is
**only** reachable from 1F (3,13) on the WEST deck, which is **only** reachable
from the B1F ladder at (5,11).

**1F's east and west halves are not connected**: walking west along 1F stops
dead at x=10 on every row. B1F is the link, and B1F's full-width corridor is
at **rows 4-5** (its interior rooms at rows 7-15 are dead ends - I wasted many
turns probing those and concluding "B1F west is sealed").

### Harness notes from real play

- **`goto` now auto-escalates to the savestate search** ("escalating to a
  savestate search ... the decoded grid may be wrong"). Good - that P2 item is
  in. It still gives up too small a budget by default (60 moves / 40 nodes).
- **The money guard fires on winnings**: `MONEY +216 ... during goto --
  movement must never spend money`. Trainer payouts are a false positive;
  it should only warn on a DECREASE.
- **Map connections and cave mouths need `step_hold` / `_step_warp_tap`, and
  often a different row than `travel` picks.** `travel` failed at Azalea's east
  edge (39,13) when the real connection row is (39,14), and at Route 32 ->
  Violet (8,0). I wrote a `cross(direction)` helper that slides along the edge
  and retries; that pattern belongs in `travel` itself.
- **Standing ON a warp does not fire it** - you must ENTER it. Repeatedly cost
  me turns (Ilex Azalea gate, Union Cave north mouth, cabin doors). Step off
  and tap back on.
- **FLY is outdoor-only** and a failed indoor attempt leaves the party menu
  open with "Can't use that here", which **blocks all movement** until B'd out
  (gotcha 7 again). Any field-move helper must clean up on failure.
- The fly destination cursor moves with **UP/DOWN only** (it cycles the visited
  landmark list); LEFT/RIGHT do nothing.
- ~~`reach(1,5)` failed where `explore_bfs` found it in 3 moves~~ **RETRACTED
  (pt13, checked in code)**: `reach` is now a thin wrapper on
  `goto(..., escalate=(budget, nodes))`, and my comparison was not
  apples-to-apples - reach targets the EXACT cell while my `explore_bfs` call
  used the looser predicate `y <= 10`, which any of several cells satisfies.
  There is no evidence of a goal-check bug. Do not chase it.
- ~~`explore_bfs` results do not survive into the next tool call~~ **RETRACTED
  (pt13, reproduced on a fork of wren-kanto)**: the searched state DOES
  persist - position and `emu.frame` were byte-identical across two separate
  tool calls. The Ilex observation was almost certainly a *failed* `reach`
  restoring its root snapshot around the successful search. The real lesson is
  narrower: a failed search restores, so do not interleave a failed `reach`
  with a successful `explore_bfs` and expect the latter to stick.
- `talk_to` fails on piers//dock tiles and against NPCs on warp rows; facing
  by hand and pressing A works.

**Next objective:** the Kanto gym run (8 badges) and eventually Red on Mt.
Silver. Vermilion's Lt. Surge is right here; REED can now Fly between cities.


## session claude-hardening - RETROSPECTIVE backlog P0-P2 landed (Aug 25 2026)

Harness-only session: no game progress, no milestone saved, `saves/` and every
existing `claude_saves/` milestone untouched. Executed `RETROSPECTIVE.md`'s
backlog P0 1-3, P1 4-6, P2 7-8, P3 9-10 and **corrected four of its claims**
that turned out to be stale or wrong (each correction is marked inline in
RETROSPECTIVE.md; see §2.4, §2.5, §2.6, §2.7).

Tests: **479 -> 553 green** (`.venv/bin/python -m pytest tests -q`, ~14 s).
New files: `crystalagent/asmconst.py`, `tests/unit/test_parser_values.py`,
`tests/unit/test_failure_reasons.py`, `tests/unit/test_teach_tm.py`,
`tests/unit/test_money_watch.py`, `scripts/verify_hardening.py` (the live
check below, re-runnable on a fork).

### [feat] the model is now told the accuracy a move REALLY has

`outlook()` carries `effective_accuracy` beside listed `accuracy`, computed by
`tactics.effective_accuracy` -- a port of `BattleCommand_CheckHit.StatModifiers`
(engine/battle/effect_commands.asm:1758) over `AccuracyLevelMultipliers`,
including the engine's per-pass truncation and $ff cap. The stage bytes are
`wPlayerAccLevel`/`wPlayerEvaLevel`/`wEnemyAccLevel`/`wEnemyEvaLevel` -- NOT
the `wPlayerStatLevels` array the retrospective pointed at. `_score`,
`recommend()`'s KO tie-break and `explain()` all rank on the effective number.

Live proof (fork of `wren-all-37`, wild battle on ROUTE_39, then
`d.emu.write("wEnemyEvaLevel", 9)` as TEST SCAFFOLDING -- never harness code):

```
neutral:      SURF acc 100      CUT acc  95     (listed == effective)
+2 evasion:   SURF acc  60 (listed 100)   CUT acc  57 (listed 95)
```

Also new: `my_status`/`their_status`/`my_confused`/`turn_loss` on `outlook()`,
and a cure branch in `recommend()` that spends the cheapest ROM-priced item
(PARLYZ HEAL ¥200 before FULL HEAL ¥600) on PAR/SLP/FRZ when nothing lethal is
incoming and no KO is available. Live: a poked PAR bit read back as
`my_status ['PAR']`, `turn_loss 0.25`.

**Correction to the retrospective**: `faster` must NOT be changed to account
for paralysis. `ApplyPrzEffectOnSpeed` (engine/battle/core.asm:6585) writes the
halved speed straight into `wBattleMonSpeed`, so the raw compare already has
it; halving again double-counts.

### [feat] every parser is pinned to a VALUE

`tests/unit/test_parser_values.py` (19 tests) asserts specific numbers with the
disassembly file:line in each docstring: status bits (PSN 3 / BRN 4 / FRZ 5 /
PAR 6, SLP_MASK %111), stage bounds 7/13, the accuracy table's 13 rows, IRON
TAIL 75 / DYNAMICPUNCH 50 / TACKLE 95 / HYPER BEAM 90, TACKLE 35 PP, PARLYZ
HEAL's ¥200 and PAR-only cure mask, `items[1] == MASTER BALL`, `$ec -> ▷` /
`$ed -> ▶`, badge-boost order, FERALIGATR's types and TM learnset, and
WILLS_ROOM's warps. The weak "accuracy is in 1..100" test it replaces would
have passed for the bug it was written about.

New `crystalagent/asmconst.py` holds the shared `.asm` walkers
(`parse_const_defs` honouring const_def/const/const_skip/const_next/
shift_const, `parse_defs` for literal `DEF ... EQU`, `parse_ratio_table`).
`battle._parse_types` and `state._STATUS_BITS` now come from it -- the status
bits were the last hardcoded game data in the read path.

### [feat] no primitive fails silently any more

`Menus.last_reason`, `Battle.last_reason`, `Driver.last_menu_reason` /
`last_step_reason` / `last_tm_reason`, each with distinct text naming what was
being looked for (`tests/unit/test_failure_reasons.py` asserts the reasons are
non-empty AND mutually distinct). `Menus._expect_state` +
`select_label(..., expect=<predicate>)` fix the root cause of the pt11 item
bug: with `expect`, a True return means the target screen is really up.
`Driver._confirm_label` adapts that for duck-typed Menus fakes without losing
the verification, and `_open_pack` now verifies through `Driver._pack_up`.

`has_label`, `cursor_labels`, `select_row_text`'s cursor scan,
`Battle._my_move_list_up` and `Battle._wait_move_menu` now read EVERY cursor
glyph on a row, not the leftmost (gotcha 1: a submenu paints ▶ to the right of
a list's own ▷). `save(force=True)` logs the blockers it overrides -- it
already refused a dirty screen, contrary to §2.7.

### [feat] teach_tm, and a learn default that keeps its damage

`d.teach_tm('TM01', 'GATOR', forget='FURY CUTTER')` -> True, verified live:
DYNAMICPUNCH replaced FURY CUTTER, the TM left `wTMsHMs`, the overworld came
back clean. Refusals happen before any button press:
`teach_tm('TM30', 'GATOR')` -> `cannot-learn: FERALIGATR cannot learn SHADOW
BALL (TM30)` with the screen byte-identical afterwards.

Two gotchas worth remembering:
- TM/HM data comes from the `add_tm`/`add_hm` ORDER in
  constants/item_constants.asm. Item ids cannot be used (a plain
  `const ITEM_C3` sits between TM04 and TM05), and data/moves/tmhm_moves.asm
  is an rgbds `for` loop with no literal list to parse.
- The pocket ROW renders `01 DYNAMICPUNCH`, not `TM01`: the TM/HM prefix is
  drawn in graphics tiles. `Driver.pocket_tag` converts, and the cursor glyph
  sits BETWEEN tag and name (`H3▶SURF`), so the two halves are matched
  separately. teach_hm and teach_tm share every step now, including
  `_walk_forget_menu`.

`Driver.default_learn_policy` decides level-up learns when `learn_policy` is
None: ROM base power, `(power, name)` tie-breaks, never an HM move, never
trading a damaging move for a status move while <=2 damaging moves remain
(this is the Gyarados/HYDRO PUMP -> RAIN DANCE rule). Its decisions are stamped
`source='default'` on `move_changes`.

### [feat] goto escalates to the savestate search by itself

`goto(..., escalate=True)` runs `explore_bfs` when the failure is
grid-distrust (`GOTO_ESCALATE_ON`: no-path / unreachable / replan-storm /
no-progress / pass-cap) and refuses to when it is a live actor, scene, menu or
whiteout (`GOTO_NO_ESCALATE_ON`). Budget 60 moves / 40 nodes; `reach` is the
same call at 200/140. Nothing had ever called `reach`.

Live, on a fork of `wren-team-leveled` (INDIGO_PLATEAU_POKECENTER_1F):

| check | result | frames |
|---|---|---|
| decoded grid made to lie (a wall column that is not there), `goto(8,11)` | **True** via search, 3 steps / 17 states, reason cleared | 3,889 |
| `goto(3,8)` -- the retrospective's storm case | False: `replan-storm ...; search exhausted (40 nodes)` | 12,553 |
| `goto(16,1)` | False: `unreachable ...; search exhausted (40 nodes)` | 10,862 |

**Correction to the retrospective**: `(3,8)` is NOT wrong static data. The
savestate search -- ground truth -- explored all 25 reachable states within 60
moves and never reached it, so that cell really is a wall; the storm was the
harness being asked for the impossible. `(16,1)` is in the map's top-right
region, reachable only through the warp at `(14,3)`: a `travel`/`route` job.
The escalation's value is that failure is now PROVEN instead of guessed, at
about 1.5-3 s of wall clock.

### [feat] money cannot move during navigation unnoticed

`Driver._money_watch` wraps `goto`, `walk` and `pace` (outer entry points only,
so a purchase during a nested dialog drain is reported once, with the map and
cell) and logs `MONEY -1200 (now 11800) during goto (3, 4) at GOLDENROD_CITY
(9, 12)`; the delta lands on `d.last_money_delta`. `travel` is deliberately not
wrapped: every leg it walks goes through `goto`.

**Correction to the retrospective**: "navigation must refuse to press A near a
clerk" is not implementable as written. Clerk identity does not exist at
runtime -- `object_event` coordinates are parsed by scripts/build_mapgraph.py
and then discarded, and `wObjectStructs` carries no sprite id. Watching the
wallet is the honest version.

### Left open (deliberately)

P3-11 multi-turn/sacrifice planning and P3-12 trainer-item awareness both need
enemy-roster/trainer-attribute data `battle_frame()` does not carry -- separate
work, not a step here. P4-13 (splitting `trek.py`, now ~6,000 lines) untouched.

---

## session claude-wren pt11 - items fixed, BATTLE.md, second model-driven E4 (Aug 25 2026)

Cleared the Elite Four again with **GATOR benched** and every action chosen by
the model, driving from the new `BATTLE.md`. Milestone
`claude_saves/wren-e4b-clear.state`.

| leader | pt10 turns | pt11 turns | note |
|---|---|---|---|
| WILL | 6 | 6 | BROOK untouched both runs |
| KOGA | 15 | **10** | arrived clean; killed Muk before Minimize stacked |
| BRUNO | 5 | 5 | arrived at 215/215 instead of 52/215 poisoned |
| KAREN | 7 | 7 | no FULL RESTORE needed this time |
| LANCE | 12 | 17 | Thunder Wave paralysed BOTH my mons; see below |

45 turns pt10 -> 45 turns pt11, but far less damage taken: one faint (RIPTIDE,
deliberate) and GATOR again ended **L78 293/293, never in a battle**.

### [fix] out-of-battle `use_item` -- my hypothesis was WRONG

I blamed a remembered party cursor that could not climb upward. ItemFixer
disproved it live (`_party_target` was already bidirectional, and the field
party list is a 2D menu with `_2DMENU_WRAP_UP_DOWN`,
engine/pokemon/party_menu.asm:661). The real cause:
`Menus.select_label('PACK')` returns True from the **cursor glyph alone** and
never checks that the pack actually opened. On the frames right after the
START menu is drawn its input loop is not running (gotcha 2), so that A is
swallowed; `goto_pocket` then burned its budget on `wJumptableIndex` 128 (the
START menu) and `use_item` returned False **with no log line**, leaving the
START menu OPEN -- which then ate the next caller's input (gotcha 7). That is
why identical calls alternated between working and failing.

New API, all verified live this session:
- `use_item(name, mon="BROOK")` -- nickname targeting; unknown name raises
  ValueError listing the party.
- `heal_party()` -> per-mon outcomes, cheapest sufficient item first.
  Live: BROOK 173/215 PSN -> 215/215 clean using **ANTIDOTE + HYPER POTION**,
  spending **zero** of the two FULL RESTOREs (which then paid for Lance).
- A full-HP mon returns False with reason `no-effect`, consuming nothing.

### New: BATTLE.md

The mechanics distilled into a file I drive from: damage formula and where
STAB/badge/variation apply, the per-TYPE physical/special split, real type
ids, accuracy as a 0-255 byte, never-miss moves, fixed-damage effects, the
move-choice rule, speed, switching (trapped / free-on-faint), status and buffs
worth pre-empting, and the turn-loop recipe. Each claim cites the disassembly
or a live measurement.

### What the doctrine bought, and what it cost

- **Koga 15 -> 10 turns.** Killed Muk before Minimize stacked instead of
  whiffing into it; BROOK left the room UN-poisoned, so Bruno was fought at
  full HP instead of 52/215.
- **Onix**: Dragonbreath over Iron Tail -- both certainly KO, but Onix has
  152 Defense vs 54 Sp.Def, so the special move is both reliable AND aimed at
  the weak side.
- **Aerodactyl**: took the 75% Iron Tail again (only one-shot available) and
  it landed.
- **Lance's Blizzard Dragonite**: paralysed and slower, "attack twice" was a
  coin flip on BROOK's life, so I switched to RIPTIDE -- **ICE is 0.5x on
  WATER/FLYING**, which turned a 119-140 hit into 38-45. It chipped with
  DRAGON RAGE, fainted, and BROOK entered FREE and one-shot the remainder.
- **New cost discovered**: Lance's L47 Dragonites carry THUNDER WAVE and
  paralysed BOTH BROOK (148 -> 37 speed) and RIPTIDE (118 -> 29). Paralysis
  cost two full turns to full-paralysis and flipped turn order for most of
  the fight -- that is the whole 12 -> 17 turn difference. Curing it with the
  cheap FULL HEAL during Charizard (its best hit is 28-34) bought back the
  speed needed to out-run the L50 ace's Outrage.
- **Accuracy-lowering moves matter too**: Karen's Umbreon SAND ATTACKed and my
  "100%" WING ATTACK missed. BATTLE.md 5 covers evasion; accuracy-down
  belongs in the same bucket.


## session claude-wren pt10 - E4 REMATCH, every turn decided by the model (Aug 25 2026)

Re-ran the whole Elite Four with **GATOR (L78 Feraligatr) benched** and every
single action chosen by the model, one turn per tool call, via
`d.decide_all = True` -> `trek.DecisionRequired` -> a one-shot action queue.
No autonomous policy ran. Milestone `claude_saves/wren-rematch-clear.state`.

| leader | turns | notes |
|---|---|---|
| WILL | 6 | BROOK solo, untouched 211/211, zero switches |
| KOGA | 15 | Muk/Crobat MINIMIZE + TOXIC; two switches to SNAG |
| BRUNO | 5 | BROOK solo at 52/215 poisoned, every kill a one-shot |
| KAREN | 7 | in-battle FULL RESTORE, then BROOK solo |
| LANCE | 12 | RIPTIDE sacrificed to chip the L50 ace into one-shot range |

45 turns, one faint (RIPTIDE, deliberate). **GATOR ended L78 293/293 -- never
gained a level, never lost a hit point, never entered a battle**, which is the
verifiable form of the handicap.

### Architecture: battles cannot be delegated to a subagent

The live emulator lives in the coordinator's Python kernel. Subagents get
INDEPENDENT kernels, so a battle subagent would have to boot its own Driver
from a savestate -- impossible mid-battle. Turn-by-turn play must happen in
the deciding context. Cost is context; the mechanism is
`d.fight(policy=one_shot_queue, require_decision=True)` raising
`DecisionRequired` each turn, which resumes cleanly on the next call.

### [fix] EVERY move's accuracy read as 100%

`BattleData` did `"accuracy": min(rec[4], 100)`, but the ROM stores accuracy
on a 0-255 scale (`DEF percent EQUS "* $ff / 100"`, macros/data.asm:23), so
IRON TAIL's 75% is the byte 191 -> reported 100. Every move above ~39% looked
perfectly reliable. Now `round(rec[4] * 100 / 255)`: IRON TAIL 75,
DYNAMICPUNCH 50, HYPER PUMP 80, BLIZZARD 70, WING ATTACK 100.
This is why pt8's IRON TAIL kept missing Crobat "inexplicably".

### New: never-miss moves are modelled

FAINT ATTACK / SWIFT are `EFFECT_ALWAYS_HIT` (data/moves/moves.asm:201) --
they ignore accuracy AND evasion. Koga's Muk and Crobat blanked two listed
100% WING ATTACKs in a row behind MINIMIZE/DOUBLE TEAM while SNAG's 15-18
damage FAINT ATTACK finished each on demand. `outlook()` now sets
`never_misses`, `_score` rewards it, and `recommend()` breaks KO ties by
reliability (unmissable > listed 100% > bigger-but-chancier).

### Decisions that a "highest damage" picker gets wrong

- **Jynx / Onix / Gengar**: two moves both one-shot -> the bigger number is
  worth nothing and the miss chance is worth everything. Against Gengar a
  whiff hands it the turn it needs for DESTINY BOND, so the 75% IRON TAIL is
  strictly wrong even though it hits harder.
- **Aerodactyl**: the INVERSE -- IRON TAIL x2 (297-350) was the ONLY
  one-shot, so the 75% gamble was correct (expected damage taken ~18 vs ~55
  for a guaranteed second turn of ROCK SLIDE). The principle is not "prefer
  accuracy", it is "prefer accuracy only when the certain option also kills".
- **Slowbro**: chose physical WING ATTACK over DRAGONBREATH because AMNESIA
  was coming; it used it (spdef 81 -> 162, DRAGONBREATH 87-103 -> 45-54)
  while WING ATTACK stayed 89-105.
- **Lance's L50 ace**: RIPTIDE's SURF is resisted (0.5x, 20-24) so its best
  move was DRAGON RAGE's flat 40 -- the fixed-damage move the old
  power-based picker discarded. Two of them chipped 162 -> 82, RIPTIDE died,
  BROOK entered FREE on the faint (no switch-in hit) and one-shot it.
- **Hitmonchan**: ICE PUNCH is x4 on a Dragonite but its Sp.Atk is 45, so it
  was only 30-36. 4x off a weak attacker < 1x off a strong one; read the
  damage span, not the multiplier.

### Corrections to my own reasoning, caught by the numbers

- I expected ELECTRIC to be 2x on DRAGON/FLYING. **DRAGON RESISTS ELECTRIC
  (0.5x)** in Gen 2, so THUNDER was x1. The harness was right, I was wrong.
- I assumed FLYING was resisted by FLYING; it is neutral.
- I assumed SURF was 4x on AERODACTYL/CHARIZARD; FLYING is neutral to WATER,
  so it is 2x.

### Still broken / notes

- Out-of-battle `use_item` is unreliable: REVIVE and one HYPER POTION worked,
  FULL RESTORE always returned False and later HYPER POTIONs too. The
  IN-BATTLE item action works perfectly (`('item','FULL RESTORE')` cured
  poison and healed 49 -> 202). Heal inside battle until this is fixed.
- A stray A press near the League clerk bought an ULTRA BALL (money 1219 ->
  19). Gotcha 13 applies to `goto` too, not just flush_dialog.
- Field poison ticks between rooms; BROOK walked into Bruno at 71/215.

[fix] ItemFixer (Aug 25): field `use_item` root cause was NOT the party
cursor -- `Menus.select_label('PACK')` confirms from the cursor GLYPH after a
2-frame A, so on some frame parities the START menu swallowed it, the pack
never opened, and use_item returned False *silently with the START menu still
up* (gotcha 7), making the next call fail too. `_open_pack` now retries the
confirm until the pack is verifiably open and every exit clears the field.
Also: `use_item(item, mon='BROOK')` (nickname, exclusive with target_slot),
`d.last_item_reason` ('used'/'no-effect'/...), and `d.heal_party()` ->
{mon: item} spending the cheapest sufficient item (ROM heal/price tables).


## session claude-wren pt9 - CHAMPION (Aug 25 2026)

**Beat the Elite Four and Lance.** `BEAT_CHAMPION_LANCE=True`, Hall of Fame
registered, 8/8 badges. Milestones: `claude_saves/wren-champion.state`,
`wren-pre-e4.state`, `wren-pre-lance.state`.

| leader | result | who did it |
|---|---|---|
| WILL | won | SNAG (FAINT ATTACK x2 on PSYCHIC) fell to Exeggutor; GATOR closed |
| KOGA | won, no faints | BROOK solo, 155/211 left |
| BRUNO | won in 5 turns, every one a one-shot | BROOK solo |
| KAREN | won | BROOK took four, fainted; GATOR finished Vileplume |
| LANCE | won | GATOR solo'd all six, L78 -> L79, ended 59/293 |

### The bug that mattered: every SPECIAL type id was wrong by 9

`crystalagent.battle._parse_types` matched `const NAME` lines and counted
from zero, silently ignoring `const_next 19` (constants/type_constants.asm:22),
which jumps the unused-type block. So the parser produced FIRE=11, PSYCHIC=15,
DARK=18 where the game uses 20/24/27. The matchup table was keyed by those
wrong ids, and **ROM move types and the WRAM type bytes are REAL ids**, so
every special-type lookup missed the table and returned a flat 1.0: no
super-effective, no resistance, no immunity, for FIRE/WATER/GRASS/ELECTRIC/
PSYCHIC/ICE/DRAGON/DARK.

Caught it live, not by reading code: the first Will attempt printed
`FAINT ATTACK x1` into EXEGGUTOR and `?24/FLYING` for a Xatu. DARK is 2x on
PSYCHIC. After the fix the same battle read `FAINT ATTACK x2` and the type
names resolved. Every "best move" this harness has ever chosen for a
special-type attack was picked from a flat chart.

### New: crystalagent/tactics.py (real Gen-2 combat maths)

Derived from the disassembly, cited in the module docstring:
`DamageCalc` (effect_commands.asm:2900) -> `Stab` (1214: badge boost, then
`d + d/2`, then the type rows) -> `DamageVariation` (1496: 85-100%), plus
`DoBadgeTypeBoosts` (misc.asm:147, `d/8`, PLAYER's turn only) and the
physical/special boundary `DEF SPECIAL` (type_constants.asm:26) -- in Gen 2
the move's TYPE picks Atk/Def vs SpA/SpD, not the move.

- `read_side/read_battle` read the in-battle structs, which the engine keeps
  STAGE-MODIFIED (`ApplyStatLevelMultiplierOnAllStats`, core.asm:6671), so
  Screech/Swords Dance are already in the numbers.
- Fixed-damage effects are honoured, not discarded: DRAGON RAGE is
  `EFFECT_STATIC_DAMAGE` power 40 (a flat 40), SEISMIC TOSS is level damage.
  The old power-based picker threw both away for "having no power".
- Immunity beats fixed damage (SEISMIC TOSS does nothing to a GHOST).
- `d.outlook()`, `d.tactics.recommend()`, `d.tactics.explain()` -- see
  AGENTS.md. 27 unit tests, type ids and the chart come from the real files.

Live decisions this produced, none of them "highest base power":
- vs SLOWBRO: HYDRO PUMP over STRENGTH -- high Defense, ordinary Sp.Def.
- vs ONIX: IRON TAIL over WING ATTACK -- STEEL is 2x on ROCK, FLYING is 1x.
- vs FORRETRESS: WING ATTACK read x1, not x2 -- 2x BUG x 0.5 STEEL.
- vs AERODACTYL/CHARIZARD: HYDRO PUMP x2, NOT the 4x I assumed by hand --
  FLYING is neutral to WATER. The chart corrected me.

### Notes for next time

- Badge boost with 8 Johto badges covers FLYING/BUG/NORMAL/GHOST/STEEL/
  FIGHTING/ICE/DRAGON -- three of BROOK's four moves. WATER is NOT boosted
  (that needs CASCADEBADGE), so GATOR's SURF never gets it.
- E4 rooms: walk north, step AROUND the beaten leader (they keep standing on
  the centre column), then the top door needs `_step_warp_tap`. The League
  door out of the Plateau PC is the (14,3) warp, and the static grid claims
  the corridor at x=16 is unreachable -- `step_hold("U")` walks it anyway.
- Shop lists draw the cursor as U+25B6 while they own input and U+25B7 under
  a textbox (gotcha 1). A one-glyph reader goes blind after each purchase.
- `use_item` out of battle is unreliable here (REVIVE worked, FULL RESTORE
  returned False); in-battle healing through the policy works.


## session claude-wren pt8 - DRAGONITE for the E4 (Aug 25 2026)

**Where the run stands:** 8/8 badges, VICTORY_ROAD (13,10), party healed.
Milestone `claude_saves/wren-dragonite.state`.

| mon | | level | moves |
|---|---|---|---|
| BROOK | **DRAGONITE** | **55** | WING ATTACK / DRAGONBREATH / THUNDER WAVE / IRON TAIL |
| GATOR | Feraligatr | 78 | CUT / STRENGTH / HYDRO PUMP / SURF |
| RIPTIDE | Gyarados | 48 | SURF / WHIRLPOOL / DRAGON RAGE / WATERFALL |
| SNAG | Sudowoodo | 45 | FAINT ATTACK / MIMIC / FLAIL / DYNAMICPUNCH |
| REED | Pidgeot | 41 | WING ATTACK / WHIRLWIND / GUST / QUICK ATTACK |
| PEBBLE | Togetic | 39 | SAFEGUARD / DOUBLE-EDGE / METRONOME / SHADOW BALL |

BROOK went L26 -> L48 -> **L55** (Dratini -> Dragonair@30 -> Dragonite@55) in ~470
Victory Road fights. What made it cheap: BROOK leads to bank half-share exp, then a
guard (RIPTIDE/SNAG) kills; the incoming mon eats the switch turn, so BROOK usually
takes zero damage. Soloing at L40 was tried and is strictly worse - it dies (2 fights).
Never chip Victory Road Gravelers: they EXPLODE (measured, enemy 94->0 and RIPTIDE
58->0 in one turn). Guards must one-shot, not soften.

### The bug that ate ~9 minutes: a REFUSED switch wedges the battle

Signature: `fight()` logs `frozen screen` 20-30x with an IDENTICAL me/enemy line and
returns `'timeout'` with the battle STILL LIVE, so the next `pace()` walks back into
the same battle. 60 "fights" / 535s / zero exp.

Root cause, confirmed by dumping the screen instead of trusting the diagnostic: the foe
(ONIX) had BOUND our mon, the policy asked for `('switch', idx)`, the harness drove the
party menu + SWITCH submenu, and the engine refused with `RIPTIDE can't be recalled!`.
Nobody handled the refusal, so the party menu stayed open with both cursor glyphs on
screen at once (U+25B7 party list, U+25B6 submenu) and nothing ever changed again.
`battle_frame()`'s `can_switch` was lying: it lists party indexes without asking whether
switching is legal at all.

Session workaround (outer loop, no trek.py edits): `fight_guarded(policy)` re-fights with
an attack-only policy after clearing menus if `observe()['ui']['battle']` is still true.
That took the same grind to 25 fights per 35 s block. `LockedTurnFixer` owns the real fix.

### learn_policy auto-trades are actively harmful

The default accepted every level-up move: RIPTIDE lost **HYDRO PUMP for RAIN DANCE**,
BROOK lost AGILITY for SAFEGUARD. Armed `wren_learn`: never trade a damaging move for a
status move; otherwise replace the weakest move only if the new one is stronger. It then
correctly took WING ATTACK over SAFEGUARD at L55.

### TM teaching: `teach_tm()` returns 'cursor-miss', drive the pocket by screen

`teach_tm()` failed on TM23/TM24. The pocket list SCROLLS and re-indexes as items are
consumed, so the item must be found by reading the live cursor row, never by index -
my first blind DOWN-scan landed on TM45 and started teaching ATTRACT to the Dragonite
(caught it at the "Delete an older move?" prompt; B backs out, then YES to
"Stop learning X?"). Working sequence: START -> PACK -> R until `wCurPocket == 3` ->
verify a pocket row is visible (`H1 CUT` / `H3 SURF`) -> DOWN until the label is on the
U+25B6 row -> A -> USE -> walk the party cursor to the mon by name -> A -> advance to
"Which move should be forgotten?" -> DOWN to the victim -> A.
Taught: TM24 DRAGONBREATH and TM23 IRON TAIL to BROOK, HM03 SURF to RIPTIDE (repairing
the Rain Dance trade).

### [fix] LockedTurnFixer (Aug 25, verified live by Main)

Disassembly-confirmed root cause, same as the screen dump above:
`engine/battle/core.asm TryPlayerSwitch .check_trapped` -- a confirmed SWITCH with
`wPlayerWrapCount != 0` (BIND/WRAP/FIRE SPIN/CLAMP/WHIRLPOOL) or
`wEnemySubStatus5 & (1<<SUBSTATUS_CANT_RUN)` (MEAN LOOK/SPIDER WEB) prints
`BattleText_MonCantBeRecalled` and `jp BattleMenuPKMN_Loop` -- back into the still-open
party list, switch un-done, no turn consumed. Same shape for RUN (`.cant_escape` falls
through to `jp BattleMenu`).

What landed: `battle.trapped()` is tri-state off wPlayerWrapCount + CANT_RUN;
`switch_blocked_reason()` (WRAM truth + a latch for the observed refusal text);
`switch_to()` drives PKMN -> bidirectional party-row select -> SWITCH submenu -> refusal
check -> dismiss + exit, never leaving a menu open; `menus._cursor_xs()` sees EVERY cursor
glyph (the old leftmost-only reader could not see the submenu's U+25B6 next to the party
list's U+25B7); forced turns (recharge/rampage/rollout/Encore/Bide) are waited out, never
re-sent; an unchanged-screen breaker substitutes after 2 no-ops and returns the new
outcome `'stalled'`; `decide.battle_frame`'s `can_switch` is EMPTY while switching is
illegal (it was lying); `fight()` now reports UNRESOLVED instead of a bare 'timeout' and
caps the diagnostic at 3 dumps/battle. 408 unit tests + full suite green.

**Live proof, same policy that wedged, no outer guard: 20 fights in 29 s, 20/20 resolved,
0 wedges** (before: 60 "fights", 535 s, zero exp). The breaker fired once, out loud:
`[battle] ('attack', 0) changed nothing for 2 turns: substituting ('attack', 2)`.
BROOK L55 -> **L56** during the proof run.

Unrelated gotcha re-learned the hard way: `d.save(force=True)` while the START menu is
open persists the open menu, and gotcha 7 then eats every movement input after the
reload -- `move_settled` returned `blocked` on four floor tiles. Close menus before
forcing a save.

**Next:** run the E4 from Will with GATOR L78 + BROOK (Dragonite) L56 as the spine;
milestone to resume from is `claude_saves/wren-dragonite.state`.

## session claude-wren pt7 - decision-first harness + team leveling (Aug 25 2026)

**[fix] The 60-"fights"/535s/zero-exp wedge was a REFUSED SWITCH.** ONIX BOUND
the active mon (`wPlayerWrapCount`), `TryPlayerSwitch .check_trapped` answered
the confirmed SWITCH with `BattleText_MonCantBeRecalled` and `jp
BattleMenuPKMN_Loop`, and the harness sat on the still-open party menu until
`fight()` timed out with the battle live. `switch_to` now drives the nested
BattleMonMenu (two cursor glyphs, `wMenuCursorY` 1/2/3), absorbs the refusal,
and `battle_frame`'s `can_switch` is EMPTY while trapped; unchanged screens
recover to the action menu instead of re-sending, bail as 'stalled', and say
when the battle is still live. Diagnostic capped at 3 dumps per battle.

**Where the run stands:** 8/8 badges, standing in INDIGO_PLATEAU_POKECENTER_1F.
Milestone `claude_saves/wren-team-leveled.state`. Money ¥13,519 (two whiteouts).
E4: Will and Koga were beaten on an accidental entry, then a deliberate
whiteout at Bruno got us out of the seal (the gauntlet resets, so it must be
re-run from Will).

**Team after this session's grind (Victory Road top floor, box-clamped pacing):**
- GATOR  Feraligatr L71 - CUT / STRENGTH / HYDRO PUMP / SURF
- RIPTIDE Gyarados  L42 - HYDRO PUMP / WHIRLPOOL / DRAGON RAGE / WATERFALL
- REED   Pidgeot    L41 - WING ATTACK / WHIRLWIND / GUST / QUICK ATTACK
- SNAG   Sudowoodo  L40 - FAINT ATTACK / MIMIC / FLAIL / DYNAMICPUNCH
- PEBBLE Togetic    L39 - SAFEGUARD / DOUBLE-EDGE / METRONOME / SHADOW BALL
- BROOK  Dratini    L26 - WRAP / DRAGON RAGE / THUNDER WAVE / TWISTER
Deltas: RIPTIDE 30->42, REED 38->41, PEBBLE 37->39 (+TM30 SHADOW BALL, it had
ZERO damaging moves before), SNAG 39->40, BROOK 12->26, GATOR 65->71.

**Exp reality check (why the band is 39-42, not 71):** matching GATOR across
five mons is ~1.3M exp; the L45 band is ~300k. Throughput measured in-cave:
~1 fight/second, ~15-18 fights per heal cycle, ~2 levels per 20 fights early
and slowing. BROOK is slow-growth AND frail (one hit per battle even when
switching out immediately), so it gates on heal trips, not on exp.

**New wedge to fix next (reproducible):** with BROOK out and GATOR fainted,
battles freeze - repeating `[fight diagnostic] frozen screen` with
me=DRATINI L26 37/69 vs ONIX L32 69/69, all four moves showing PP, ending in
'timeout'. BROOK gained nothing in rounds 5-7 because of it. Suspect the
executor cannot drive one of WRAP/DRAGON RAGE/THUNDER WAVE/TWISTER (fixed-
damage/status moves have power 0/None, which my own policy filters out, so the
fallback picks slot 0). Worth a fixer: policy-chosen action that produces no
screen change must be detected and substituted after 2 tries, not spun on.

**Navigation notes for next session:** Victory Road's Route-23 entrance
(13,10) is a ONE-WAY drop - the pocket only connects downward; the exit is
(13,6)->(13,5) reached from the middle band, and the (0,11)<->(0,27) /
(13,17)<->(13,31) stair pairs need `_step_warp_tap` (held keys bounce). The
Plateau PC's south door is (5,13)/(6,13); mapgraph keeps routing to (0,13),
which is the 2F stairs. Nurse is at (3,7) (goto (3,8) storms; `reach` it).

## session claude-wren pt6 - GLACIER BADGE (DONE, Aug 25 2026)

**7/8 badges** (milestone `claude_saves/wren-glacier-badge.state`; FERALIGATR L55,
SUDOWOODO L38, TOGETIC L37, PIDGEOT L37, **RIPTIDE** the Red Gyarados L30, ¥42156).

Leg: Ecruteak -> Route 42 -> Mahogany -> Route 43 (toll not charged) -> Lake of
Rage (RIPTIDE caught with chip-then-Great-Ball policy, RED SCALE from Lance) ->
Rocket Hideout B1-B3 (passwords SLOWPOKETAIL + RATICATE TAIL, Murkrow HAIL
GIOVANNI, ExecutiveF at the transmitter door, Electrodes, HM06 from Lance) ->
Mahogany Gym ice puzzle solved by empirical slide probing -> Pryce swept.

Warnings for next sessions:
- `goto` cross-warp/complex-interior targets still silently no-op; the Rocket
  base B2F middle complex is enterable ONLY via the (14,12)/(15,12) bg-event
  door from row 13 (A-press) after Lance's heal scene; renders of B2F/B1F
  mislead (row off-by-two in places) - trust live `tiles` probes.
- B1F west room exits via the (5,15) teleporter to (25,1), not row 16.
- Ice gym: `slide()` helper (press+settle, log p0->p1, fight on event) beats
  any BFS; dead-end pocket at (9,2).
[fix] GridTruthFixer (Aug 25): B2F/B1F "off-by-rows/phantom walls" diagnosed —
grid() decode is byte-exact vs the ROM (blk/tilecoll/GetCoordTileCollision all
verified); the real liars were changeblock doors: nav now exposes them via
conditional()/cell_kind()=='conditional'/render() '?' ((14,12)-(15,12) B2F door,
B3F (10,9)-(11,9), Mahogany Mart stairs (7,3)); note: row 16 has a REAL pipe
pillar at x6 (block $3e). tests/unit/test_wren_pt6_nav_grid.py (10 green).
[fix] PackCursorFixer (Aug 25): party/pack rows are now TEXT-targeted — new
Menus.select_row_text / Driver.select_menu_row finds the row naming the label
(word-bounded or _item_row_matches), steps the exact cursor delta (column-band
glyph pick ignores the party ▶ behind submenus and stale ▷ leftovers), verifies
every press, and scroll-searches D-then-U at list pins; party_swap SWITCH,
use_cut CUT, and _pocket_select's confirm all use it (field moves above SWITCH
no longer misfire). tests/unit/test_wren_pt6_submenu_rows.py (11 green).
[fix] HealRecoveryFixer (Aug 25): heal_pokecenter called mid-composite outside
a PC no longer explodes — it now travel()s into the current map's routable
Pokécenter warp first (bounded `tries` kwarg); genuinely unreachable -> new
HealError (carries map, still a RuntimeError), and registry resolve('heal')
returns {'ok': False, 'reason', 'map'} instead of propagating. Success shape
unchanged. tests/unit/test_heal_recovery.py (11 green).
[fix] GotoNoopFixer (Aug 25): goto no longer no-ops silently — every
navigation failure funnels through _goto_fail: d.last_goto_reason now
distinguishes 'outside-bounds:'/'unreachable:'/'target-occupied:' (NPC
standing on the goal, 3-pass wander tolerance), and a new goto(...,
strict=True) kwarg raises TravelError on those (handoffs — manual battle,
choice menu — still return False); travel's approach-fallback TravelError
carries the last goto reason. tests/unit/test_goto_loud_failures.py (10 green).
[fix] ObstacleFixer (Aug 25): tiles{} now names field obstacles instead of
'blocked' (whirlpool $24, waterfall $33, buoy $27/$c0-$c7, sidewall-<dirs>
$b0-$b7 via _tile_kind), and new Driver.clear_obstacle(dir, tries=6) answers
the INVISIBLE whirlpool/waterfall/surf-mount ask (wScriptMode==2, textbox
False, BLANK glyphs) with the fuzzed pause->A->pause cadence (A gaps >=40f) ->
'moved'|'cleared-not-moved'|'failed'; new Driver.move_settled(dir) presses then
polls pos to 3 stable reads (no more mid-slide samples), paging battles/
textboxes en route. tests/unit/test_wren_pt6_obstacles.py (13 green).
[fix] ExploreBfsFixer (Aug 25, finished by Main): Driver.explore_bfs(goal,
max_moves, dirs, forbid_maps, on_battle, max_nodes) promotes the savestate
BFS hand-rolled 10+ times this run (ice slides, Rocket base, Tohjo Falls) —
in-memory snapshots, frontier keyed by (map,x,y), settled moves that page
textboxes/fight intercepts, goal checked after every move incl. mid-move map
changes; winning state is left LOADED. Its own fixture was broken (passed a
bare row list where FakePy keys maps by id, so every cell read as wall):
normalized. tests/unit/test_wren_pt6_explore_bfs.py (13 green).
[fix] SpriteNavFixer (Aug 25, finished by Main): live sprite truth —
state.decode_object_structs/live_sprites read wObjectStructs (wMapObjects is
STATIC defs; reading it made pushed boulders look reset). New Driver.sprites()
(slot 0 = player), npc_cells() now derives from it (degrades to empty set),
observe()['sprites']. goto no longer storms against a squatter: cells on the
relaxed path that sprites occupy are classified — stationary -> immediate
'blocked-by-stationary-npc: <cell>', wanderer -> waits in WANDER_WAIT_CHUNK
(150f) slices up to WANDER_WAIT_FRAMES (600f) then replans, else
'waited-for-wanderer: still blocked'; unreadable table keeps the legacy
'target-occupied'. tests/unit/test_wren_pt6_sprites.py (12 green).
[fix] TravelGapsFixer (Aug 25, PARTIAL — finished/tested by Main): what landed
is pack detection by SCREEN — _pack_pocket_banner/_pack_quantity_rows +
Driver._items_pocket_by_screen, wired as use_item's fallback when
goto_pocket's wJumptableIndex gate misreads in field context (live: 4
SUPER POTIONs in the bag, use_item returned False 4x without moving a
cursor). Tested in tests/unit/test_wren_pt6_pack_detect.py (9 green).
[fix] Main: _mount_surf verified by POSITION as well as wPlayerState, and
reports 'warp' when the mount carries you across a map seam — the New Bark ->
Route 27 crossing mounted successfully and still returned 'blocked', so the
seam was hand-rolled with raw presses. tests/unit/test_wren_pt6_surf_mount.py
(5 green).
[fix] MovementAutofightAgent (Aug 25): the movement primitives no longer
decide battles — move_settled surfaces 'battle' (party still in it) instead of
silently calling fight() with the DEFAULT policy (the pacing loop that reported
fights=0 while the harness fought ~20 battles and whited us out); `fight=True`
or the new class flag `auto_fight_steps` opts back in, and walk/goto/travel/
clear_obstacle now clear encounters ONLY through the single `_on_battle()` path
so a policy/encounter hook always applies. New `Driver.pace(steps, dirs, box,
on_battle)` replaces the hand-rolled grind loop: box=(x_lo,x_hi,y_lo,y_hi)
clamps the random walk so it cannot drift into a stairwell (the Victory Road
three-floor strand), 'return' stops on the first encounter for the model to
decide, 'fight' hands each to the policy and keeps going. Class attrs
`encounter_policy`/`decide_all` added for the encounter hook.
tests/unit/test_movement_no_autofight.py (26 green).
OPEN DEBT (not attempted): travel() still has no warp-graph fallback for
multi-floor interiors ('no routable mapgraph path RADIO_TOWER_5F ->
GOLDENROD_CITY' — descend floor-by-floor by hand), and Victory Road's
stair warps need _step_warp_tap (held keys bounce off COLL_STAIRCASE), so
explore_bfs over held moves alone cannot leave that floor.

### DOCTRINE (pt6, after user callout): the MODEL decides, not the harness
The run had drifted into auto-pilot. Evidence: a "pacing" loop logged
`fights=0` while move_settled silently fought ~20 battles, fed all exp to
GATOR and whited the party out; a ping-pong policy handed Koga ~10 free
switch-in hits and wiped 5 of 6 mons with no turn record to diagnose it;
a kernel reboot dropped learn_policy and the auto-forget traded a
deliberately-bought TM16 Icy Wind for Hydro Pump; ~78 of ~80 wild
encounters were auto-KO'd without ever asking. Inverted defaults:
- movement NEVER fights (move_settled surfaces 'battle'; auto_fight_steps
  gates primitives, auto_fight still gates journeys), new pace(box=...)
  for grinding without ceding control;
- every WILD asks `encounter_policy(frame)` once: 'ko'|'catch'|'flee'|
  ('ball', NAME); trainers never asked;
- `fight(require_decision=True)` / `decide_all` raise DecisionRequired
  (with the frame) instead of guessing; an unsteered fight logs
  'auto: attack slot N (<move>) -- the HARNESS is choosing';
- `battle_frame()` gives me/enemy/party/bag/turn/wild/can_switch/moves
  (power, pp, effect_mult) in ONE read, so a real policy is ~5 lines;
- `last_battle` TurnLog with .summary()/.free_hits() makes the Koga-style
  wipe a single line instead of a postmortem.
Also landed: Driver.reach(x,y) (goto, then explore_bfs when a floor's
decoded grid lies) and explore_bfs's staircase-tap retry + 'cells' set.
[fix] BattleFrameAgent (Aug 25): new `crystalagent/decide.py` — `battle_frame(b)`
(or `(emu, names, bdata)`) assembles in ONE call what every hand-written policy
was re-deriving (me/enemy with status, whole party, bag with spelling-proof
lookup, turn, wild, `can_switch` minus active/fainted/eggs, and my moves WITH
`effect_mult` vs the mon actually standing there); `TurnLog` keeps the
append-only per-turn record with `free_hits()` — the number nobody counted while
Koga got ~10 free switch-in hits — plus `summary()`/`explain()` one-liners and
`DecisionRequired` for "the MODEL answers this". battle.py gains
`my_hp()/enemy_hp()/hp_snapshot()`, a `status` key on me()/enemy(), and
`BattleData.effectiveness` now counts a duplicate defender type ONCE (engine
parity, CheckTypeMatchup: Water vs mono-WATER is 0.5x, was 0.25x).
tests/unit/test_decide_frame.py (21 green).
[fix] EncounterHookAgent (Aug 25): wilds are now a QUESTION, not a default —
`d.encounter_policy(frame)` is asked ONCE per wild encounter for
'ko'|'catch'|'flee'|('ball',NAME) ('catch' reuses catch()'s ball logic and picks
the cheapest ball in the pocket; trainers are never asked), `fight(...,
require_decision=True)` / `d.decide_all` raise `DecisionRequired` (carrying the
frame) instead of quietly playing best-damage, every turn lands on
`d.last_battle` (decide.TurnLog, plain list without the module) with one loud
`free_hits=N` line per battle, and a fight nothing is steering logs exactly one
`auto: attack slot 0 (SURF)` warning naming the harness's own pick. Battle.play
now receives a WRAPPER, so fakes/inspection must read `wrapped.policy` to see
who is steering. tests/unit/test_encounter_hook.py (27 green).

# PROGRESS — Pokémon Crystal run


## session claude-wren pt5c — grind campaign, whole team 37+ (Aug 24 2026)

User-directed team leveling. Final: FERALIGATR L52, SUDOWOODO **L38**,
TOGETIC L37 (**PEBBLE EVOLVED** via happiness mid-grind), PIDGEOT L37
(**REED EVOLVED** L36). Milestone claude_saves/wren-all-37.state.
Method (now a HANDBOOK recipe): solo-kill grinding on Route 38 Tauros/
Miltank (best base-exp nearby), trainee leads + fights its own weight,
GATOR anchor, chunked 18 fights -> heal rail -> save. Baseline ~23s/cycle;
**set_text_speed('FAST') cut it to ~15s** — SNAG went 20->37 in 133s.
PEBBLE (no damage moves) leveled by lead-and-switch banking instead.
Slowness root causes found while doing it: (1) a DECLINE-mid-battle learn
flow WEDGED at the forget menu (GATOR/SCREECH; learn_policy returned
DECLINE but the flow reached 'Which move should be forgotten?' with the
cursor parked on an HM) — my loop retried fight() ~150x with no circuit
breaker; both sides fixed session-locally (screen-text learn resolver +
wedge counter in the loop) but the trek DECLINE path needs a real fix;
(2) policies matching me['name'] against SPECIES break silently on
EVOLUTION (TOGEPI->TOGETIC left PEBBLE struggling to death 5 cycles) —
match rosters by nickname/slot, or provide a stable identity in `me`;
(3) learn_policy picked wrongly once (SNAG: forgot ROCK SLIDE for FAINT
ATTACK when MIMIC was the obvious junk — suspect policy exception ->
auto fallback; needs the warning line surfaced) — SNAG now has no rock
STAB; (4) benign level-up stat pages still trip the frozen-screen
diagnostic (spammy); whitelist 'grew to level' screens.
[fix] BattleIdentFixer (Aug 25): items (2)+(4) — me/enemy now carry stable
'nickname' (wCurBattleMon -> wPartyMonNicknames) + 'party_slot'; 'name' stays
species (compat). Wedge detector whitelists 'grew to'/stat-page screens as
PROGRESS (paged with plain A, no diagnostic); real freezes still 'wedged'.
tests/unit/test_battle_identity_wedge.py (8 green; guards+item flow 42 total).
[fix] TrekDeclineFixer (Aug 25): items (1)+(3) — root cause of the DECLINE
wedge: mid-battle _AskForgetMoveText scrolls through a 2-line box, so the
full 'is trying to learn X' sentence never fits one screen (consult regex
never fired) AND its middle pages carried no _LEARN_MARKERS (per-flow state
dropped mid-flow) -> make-room auto-YESed into the forget menu. Now: markers
cover all scroll pages, mon/move accumulate across frames before the ONE
policy consult, forget-menu-with-DECLINE safety net Bs out to 'Stop
learning' + confirms; raising policies log exception text + full args, and
move_changes entries carry 'source': 'policy'|'auto'|'auto-fallback' for
post-hoc audits. tests/unit/test_wren_pt5c_learn_decline.py (6) + pt5/pt4
learn tests updated; 29 green.


## session claude-wren pt5b — learn_policy + team training (Aug 24 2026)

User-directed: move-learn decisions now belong to the MODEL. LearnPolicyFixer
landed Driver.learn_policy(mon, new_move, current_moves) -> move|'DECLINE'|None
(consulted once at the prompt, HM/absent/raise all warn+auto-fallback; 58
tests green). WREN's session policy: sacrifice status moves first, never a
GOOD-coverage move, DECLINE status moves onto damage sets. Bite-for-Scary-Face
can never silently happen again (and note for the record: Feraligatr gets NO
Crunch in Gen 2 — Bite was irreplaceable; the auto slot-1 default destroyed
strictly-better coverage for a status move).
Team training (was a one-croc show): two Route 38 grass blocks with a
bank-and-switch policy armed via d.default_policy — PEBBLE L5→17 (Metronome
learned into the empty slot), REED L19→21 fighting his own weight class,
zero net faints after heals. Roster now REED 21 / PEBBLE 17 / GATOR 42 /
SNAG 20 (milestone wren-team-trained.state).
Field notes: (1) party-menu submenu lists FIELD MOVES above SWITCH for
HM-carriers — blind row counts fire Strength/Surf in the field (this cost
one field-Strength misfire and explains every earlier reorder failure;
navigate submenu by row TEXT); (2) alternating single direction taps only
TURN in place in GSC — pacing loops must use step_dir or double-taps; (3)
battle wedge diagnostics fire on benign level-up stat pages — consider
whitelisting 'grew to level' screens; (4) an NPC dialog opened by pacing
turns silently eats hundreds of inputs — pacing loops must check ui.textbox
every iteration (mine now do).


## session claude-wren pt5 — STORM BADGE (DONE, Aug 24 2026)

**5/8 badges** (milestone claude_saves/wren-storm-badge.state; FERALIGATR L41
144/144, PIDGEOTTO L19, TOGEPI L5, SUDOWOODO L20, ¥25382, TM01 DynamicPunch).
Leg: Routes 38/39 → Olivine (SILVER gym-front scene), lighthouse climbed to 6F
— the hole/ladder chain is: 4F row-3 tunnel UNDER Lass Connie(11,2) is FAKE
(blocked live); real route = 4F row 2 → fall (9,3) → 3F center → (9,5) ladder
→ 4F pocket → (9,7) → 5F center → (9,15) → 6F. Jasmine met; surfed Routes
40/41 (enable_surf + goto works on water); Cianwood: SECRETPOTION bought,
HM04 round-trip to Olivine café (STRENGTH taught — landed on GATOR over
SLASH, acceptable). Cianwood Gym boulder puzzle solution (get it right first
time, future sessions): push LEFT boulder (3,7) up once, RIGHT boulder (5,7)
up once, then stand (5,7) and push MIDDLE boulder LEFT into the freed (3,7);
corridor col 4 opens; row 4 west → row 3 west → row 2 → Chuck at (4,1).
Pushing the middle boulder UP plugs row 5/4 fatally (needs map re-entry
reset). Chuck first-try sweep with Surf/Strength split policy.
Harness: BattleItemFixer (in-battle item executor rewrite, norm_item public,
wedge-fingerprint reset) + TrekLearnFixer (pocket row matcher + LEARN
transparency: Driver.move_changes + 'LEARN: X forgot A -> learned B' lines;
learn flow documented — sacrifices FORGET_PRIORITY else slot 1) both landed;
full suite 176 green. BattleGuard validation observed working live (Surf PP
exhaustion mid-sea degraded gracefully, zero wedges).
Agent-behavior notes: goto still silently no-ops on unreachable targets
(returns without moving or raising — candidate fix); lighthouse mapgraph
warp expectations wrong (travel unusable there); party-switch second cursor
unreadable via screen glyphs (reorder flow needs WRAM like _party_target).
[fix] LearnPolicyFixer (Aug 24): move learns are now model-controllable —
Driver.learn_policy(mon, new_move, current_moves) -> move-to-forget |
'DECLINE' | None(auto); consulted once at the learn prompt before YES/NO,
cursor row verified before confirming; stale/HM/raising policies warn once
and fall back to auto. tests/unit/test_wren_pt5_learn_policy.py (8 green).


## session claude-wren pt4 — FOG BADGE (DONE, Aug 24 2026)

**4/8 badges** (milestone claude_saves/wren-fog-badge.state; FERALIGATR L35,
PIDGEOTTO L19, TOGEPI L5, SUDOWOODO **SNAG** L20 caught in one ball, ¥11831).
Leg: SquirtBottle quest (full chain: meet Floria AT the tree, talk to her IN
the shop, THEN teacher gives bottle — EVENT_TALKED_TO_FLORIA_AT_FLOWER_SHOP is
the hidden middle step), Sudowoodo caught, Routes 35/36/37 (Route 36's south
col into Route 35 lands in a fenced 5-cell pocket — dead end, go via the park),
Ecruteak, Burned Tower (SILVER beaten but 3 faints, then fell through the
floor, beast trio scene done, Eusine talked), GATOR **evolved FERALIGATR
L30** on Route 35. Morty took 3 whiteouts before the root cause surfaced:
train()'s learn flow had replaced BITE with SCARY FACE, so the "Bite" policy
was pressing a no-damage move at slot 1 all three fights; also in-battle
('item','SUPER POTION') opens the pack and stalls at the item description
(BattleGuard's new wedge cap caught it — returned 'wedged' with capped
diagnostics, exactly as designed). Fix was the Dance Theater: 5 Kimono Girls
beaten → HM03, SURF taught over Water Gun → Morty first-try sweep.
TrekGuard+BattleGuard batches verified live this leg: save() guard, policy
validation warnings, wedge cap, default_policy attr all behaving. Full suite
147 green (coord notified).
NEW BUGS for next fixer batch: (1) battle.py in-battle item executor stalls
on the pocket description page (repro: ('item','SUPER POTION') vs Morty);
(2) use_item('SUPER POTION'/'SUPERPOTION') returns False out of battle —
two-word item name resolution in _pocket_select; (3) fight()/train()
move-learn flow can silently replace a policy-critical move (Bite->Scary
Face) — surface move changes in the train/fight return value or log.
[fix] BattleItemFixer (Aug 24): bug (1) — battle.py's item executor now
steers the pocket/party cursors on live WRAM (battle-side _pocket_select /
_party_target), state-verifies every description/USE/"Use on which PM?" page
press, succeeds ONLY on a bag decrement, and on any stall Bs out to the
battle menu + reports failure; play() resets the wedge fingerprint on failed
actions so substitution (attack by turn 3) wins instead of 'wedged'. Bug (2)
shared normalizer: `from crystalagent.battle import norm_item` (spaces/
hyphens/case/é; _norm_item alias kept). tests/unit/test_battle_item_flow.py.
[fix] TrekLearnFixer (Aug 24): bug (2) — _pocket_select's row verify now
matches via _item_row_matches (both sides _norm_item'd: case/space/hyphen/
POKe blind, quantity-digit + edge-clip tolerant) and rescans for the ACTIVE
▶ row when a stale glyph shadows cursor_row; mismatches log want vs row.
Bug (3) — fight()/_resolve_learn_flow diff party moves around every learn
flow: each replaced slot logs "LEARN: GATOR forgot BITE -> learned SCARY
FACE (slot 1)" and appends {'mon','forgot','learned','slot'} to
d.move_changes (train warns when a run swapped slots); the accept/replace
policy (first FORGET_PRIORITY hit, else the move under the cursor = slot 1)
is now documented in _battle_text_handler. tests/unit/test_wren_pt4_trek.py.


## session moss-run owns persona timeline (Moss) to Zephyr + Hive Badge, working state omp_saves/moss-intro.state

Claimed Aug 24 2026 per OMP_BRIEF2.md + persona_moss.md. Player **MOSS**
(collector kid: catcher-first, 4+ nicknamed partners with plant names,
talks to every NPC, fair-fight streak). Fresh raw-boot timeline; ALL
states `omp_saves/moss-*`; never touches `saves/`. Rival to be **FERN**
(plant-name culture). Roster contract: ≥3 all-nicknamed L8+ party before
Falkner; ≥4 after Slowpoke Well; 15-ball budget for the Zephyr leg.
Ledger: `omp_saves/moss-ledger.md` ([S]/[W] harness observations).
**HIVE DONE Aug 24 2026 — OBJECTIVE COMPLETE** (`moss-hive.state`,
CLI-verified badges [ZEPHYR, HIVE]): BAYLEEF **FROND** L21 64/64,
PIDGEY **BRAMBLE** L12, SENTRET **THISTLE** L9, TOGEPI (hatched; name
garbage "AAAAAAAAAA" — rename at Goldenrod Name Rater), BELLSPROUT
**SPROUT** L7. ₽4939. Well cleared, Kurt finale auto-ran.
Scyther wall fell to BRAMBLE (Gust STAB 2x vs Bug > Grass's 0.25x).
Postmortem: `omp_saves/moss-postmortem.md`; ledger with all [S]/[W]:
`omp_saves/moss-ledger.md`.
COORD RUN-2 CLOSE-OUT (Aug 24): persona experiment validated end-to-end.
Same harness, opposite revealed preference vs omp-fresh (optimizer:
0 catches, solo carry; Moss: 4 catches, rotation, Scyther via
matchup-driven switch). 12 coordinator commits this cycle; every [W]
class moss-run surfaced got a same-session fix (naming freeze, stall
guards, resolve_choice verify/retry, auto_fight scoping, per-mon train
targets, gym_scout). Remaining gaps tracked below in OPEN BACKLOG.
OPEN BACKLOG (post-run-2, ranked by moss-postmortem §remaining):
a. resolve_choice yes/no variant silent-fail — watch regressions of the
   verify/retry path on aide-style boxes.
b. _event_flag vs party-truth disagreement post-egg (repro REFUTED on
   omp-fresh-egg-in-party.state: flag reads True there) — needs a
   failing state capture before code touch.
c. slot-faint mystery (THISTLE 27/27 -> 0/27 with no battle event);
   add all-slot hp asserts to pre-gym checks.
d. mart qty-box screen parsing is fragile; wrap single-unit cycles +
   bag-delta verification as fallback.
e. hatch-naming: egg in party => poll keyboard_open during walking
   segments (freeze shipped; detection cadence is the open half).
**LEG 3 DONE Aug 24 2026: PLAIN BADGE** (`moss-plain.state`, CLI-verified
badges [ZEPHYR, HIVE, PLAIN]): BAYLEEF **FROND** L29 40/87, PIDGEY
**BRAMBLE** L17, SENTRET **THISTLE** L10, TOGEPI **BUD** L5 (renamed at
Goldenrod Name Rater — AAAAAAAAAA fossil corrected), BELLSPROUT **SPROUT**
L7, VENONAT **SPORE** L5. ₽6684. Ilex herd completed with facing-table
method (VAR_FACING read live from wPlayerDirection d4de; talk_to corrupts
facing on the bird — raw A only). Whitney: Reflect opener vs Miltank +
Razor Leaf; crying-scene coord event (8,5) needed trip_scenes + a second
talk to yield badge/TM45. Ledger: `omp_saves/moss-ledger.md`.
**LEG 4 CLAIMED Aug 24 2026: FOG BADGE (Morty, Ecruteak).** Route:
Goldenrod errands (R36 Floria → Flower Shop Squirtbottle → Sudowoodo
(35,9) — Moss intends to CATCH it) → R36 → Ecruteak → Burned Tower
(rival row-9 sight line, B1F beasts scene at (10,6), Eusine) → Morty.
Per oxa-johto: fight() cannot drive Morty's battle — poll enemy HP>0
through intro scroll, detect the 2x2 battle menu via wTilemap pattern,
manual driver. Ledger continues `omp_saves/moss-ledger.md`.

## session claude-wren pt3 — PLAIN BADGE (DONE, Aug 24 2026)
**3/8 badges.** Azalea→Ilex (Farfetch'd herding solved by reading the position
state machine — wFarfetchdPosition + player-facing checks; stations 8/9 need
DOWN/LEFT-facing talks), HM01 CUT (GATOR), Route 34, Goldenrod. Togepi EGG
hatched → **PEBBLE**. REED evolved → **PIDGEOTTO L18**; GATOR CROCONAW L29
via first-ever `d.train(29)` (works great: rotation + heal rail).
Whitney took 4 attempts: (1) default policy lost to Attract+Rollout+Milk
Drink; (2) Mud-Slap opener fed Rollout ramp — bad plan; (3) talk_to
auto-fought with default policy before custom policy could attach (lesson:
approach gym leaders manually, fight() with policy from turn 1); (4) L29 +
Water Gun + Super Potion at <40HP = clean win. Crying scene: must step onto
the (8,5) coord event (lass intercept) before she yields the badge.
Also: SILVER rematch at Ilex gate won clean (split-exp policy with
fainted/egg guards); one earlier whiteout to him from a policy that switched
into fainted mons — reloaded per gotcha 9.
LegTwoFixer fixes verified live: heal step-away works every time; cursor-glyph
guard correctly identified real menus ~10 times. NEW BUGS for coord: d.save()
while a pack/menu layer is open bakes the stuck menu into the state; fight()'s
frozen-screen diagnostic spams hundreds of identical lines when a policy
returns an impossible action (switch to fainted mon) — needs a wedge cap;
TM49 got taught over MUD-SLAP during a stuck-menu unwind (A presses are never
safe while any menu layer is live — check glyphs first).
Milestones: wren-silver2-beaten, wren-goldenrod, wren-pre-whitney4,
**wren-plain-badge** (CROCONAW L29, PIDGEOTTO L18, TOGEPI L5, ¥6074).

[fix] battle.play now validates policy actions before executing (switch to
fainted/EGG/out-of-range slot, item/ball not in bag, dry attack slot →
one warning + default-action substitution feeding the fails guard: pure
default within 2 turns) and caps the frozen-screen wedge: after 3 identical
screen+vitals fingerprints it confirms (600f), re-syncs once, prints at most
2 diagnostics, returns 'wedged'. tests/unit/test_battle_policy_guards.py.
[fix] TrekGuard (Aug 24): d.save() now refuses to bake a dirty screen into a
state (battle/script/textbox/menu-cursor check after settle, bounded B
recovery, force=True bypass); fight(policy=None) falls back to new
d.default_policy so talk_to/goto/travel intercepts obey a pre-armed policy
(Whitney lesson — explicit fight(policy=) still wins); use_item targets the
party menu on live WRAM (wMenuCursorY) with a bag-gated, jingle-tolerant
confirm — REVIVE on a fainted mid-list slot works. 'wedged' outcome from
battle.play logs one line, no screen re-dump. tests/unit/test_wren_frictions.py.


## Full-repo code review done — CODE_REVIEW_PLAN.md (Aug 24 2026, session ox-alpha)

Six parallel review agents covered trek.py, battle/nav/menus, serve/autopilot/
watch/core, scripts/tests/hygiene. Findings + prioritized fix plan live in
**CODE_REVIEW_PLAN.md** (P0-P11). Nothing fixed yet; next engineering session
should start at that file's Work Plan. P11 folds in the prior field reviews
(backup/FABLE_FEEDBACK.md, fable_results.md, backup/DEEPSEEK_PROGRESS.md) with
duplicates confirmed / new items added / already-fixed items recorded. Top items: battle.py type-ID off-by-9 +
accuracy misread (move ranking wrong today), mart_buy money-leak bugs,
watch.py select/snapshot race + SSE duplicate history, autopilot stuck-digest
missing bag/money.

## session claude-wren pt2 — HIVE BADGE (DONE, Aug 24 2026)

Model-driven navigation this leg (decision-boundary doctrine): waypoints chosen
from map_view/observe, goto as local executor only, no full-route travel calls.
Violet: Togepi EGG from Elm's aide (triggered by the Route 32 call at (18,8),
walked back for it), verified mart run (4 Potions + 2 Antidotes). Route 32:
declined the SlowpokeTail scammer; Route 32 PC heal. Union Cave 1F traversed
(GATOR **evolved to CROCONAW L18** inside). Route 33 → Azalea. Slowpoke Well:
all 4 grunts beaten, Kurt scene done. Azalea Gym: Al/Benny/Josh beaten, Bugsy
beaten → **HIVE badge 2/8**, TM49. Final: CROCONAW L22, PIDGEY L4, EGG.
Milestones: claude_saves/wren-well-cleared, wren-pre-gym2, **wren-hive-badge**.
Field note for coord: the new _drain_scene choice-menu guard MISDETECTS
empty/blank pre-battle trainer textboxes as choice menus (repeated
"GAVE UP (blocked by choice menu...)" at Slowpoke Well and Azalea Gym where
the box contained no text and A safely started the trainer battle). Guard
should require an actual cursor glyph ($ec/$ed) before claiming 'menu'.
[fix] LegTwoFixer (Aug 24): landed in trek.py — _drain_scene now claims
'menu' ONLY on an actual cursor glyph ($ec/$ed); blank still-rendering boxes
get a bounded wait then A. use_item steers the persisted items-pocket cursor
via WRAM both ways, screen-verifies the row, and gates success on a bag
quantity read-back; heal_pokecenter steps south off the counter after a
confirmed heal (no re-armed nurse prompt). tests/unit/test_wren_frictions.py.

## session claude-wren — fresh boot → ZEPHYR BADGE (DONE, Aug 24 2026)

Persona run (persona.md at repo root): player intended "WREN" — actual
in-game name is **AWREN** (hit the known vega_intro stray-A naming bug,
coord fix 1/2 above; run used the old press-then-check order). Totodile
**GATOR** L16 (Scratch/Leer/Rage/Water Gun), Pidgey **REED** L4, rival
**SILVER**. Full story line completed: starter, Mystery Egg errand,
Cherrygrove rival fight, egg delivered, all Sprout Tower sages + Elder
Li (HM05), Violet Gym (Abe, Rod, Falkner) → **ZEPHYR BADGE 1/8**.
ALL states in `claude_saves/` (never touched `saves/`): working
`claude_saves/wren.state`, milestones wren-starter / wren-egg-delivered
/ wren-pre-tower / wren-tower-cleared / wren-pre-gym /
**wren-zephyr-badge**. Postmortem: `fable_results.md` at repo root.
Field notes for coord: (a) travel/goto replan-storm on scene textboxes
remains the top friction — drain is always the fix, never automated;
(b) Sprout Tower mapgraph stairs wrong both directions (tower.py probe
still the answer); (c) use_item/heal/mart_buy all fail first call to
menu-setup swallowing, succeed on settle+retry; (d) 1F tower exit
ping-pongs (8,15)↔(11,15) — held-direction door step needed.
[fix] TrekFixer (Aug 24): landed (a)(c)(d) in trek.py — goto/travel now
auto-drain scene textboxes via new `_drain_scene` (bounded A-paging to
wScriptMode==0, aborts on choice menus per gotcha 13, battles route to
the existing fight path); use_item/heal_pokecenter/mart_buy each
settle-drain-retry ONCE before surfacing their first-call failure
(gotcha 2); travel's warp legs fall back to `_held_warp_entry`
(held/tapped step back onto the door tile) when a held glide crosses a
multi-warp door row without firing (gotcha 12, Sprout Tower 1F).
Unit-tested in tests/unit/test_wren_frictions.py (17 tests).
[fix] MapgraphFixer (Aug 24): landed (b) — mapgraph.json is now
region-aware: build_mapgraph.py flood-fills each map's walkable grid and
edges carry from_regions/to_regions; nav.MapData gains region_map()/
regions_at()/plan_route() (region-aware map planner). Sprout Tower
1F↔3F now plans the real walkway chain (6,4)→(17,3)→(2,6)→(10,14) both
ways; locked in tests/unit/test_mapgraph_regions.py (11 tests). trek's
Dijkstra (`route`) can adopt the fields to filter edges by entry region.
[fix] RouteWirer (Aug 24): wired (b) into trek.py — route()'s Dijkstra
now runs on (map, region) nodes gated by the edges' from_regions/
to_regions (absent = wildcard; entry region from the live standing
cell), and travel() replans the remainder when a tolerated glide lands
across a region seam. Locked in tests/unit/test_route_regions.py (5).

## session ox-alpha-coord owns framework improvement loop + omp-fresh oversight

Coordinator session (Aug 24 2026, Herdr two-pane setup): player session
**omp-fresh** (pane w19:p1F) owns the fresh-boot run to ZEPHYR BADGE —
raw power-on boot, working state `omp_saves/omp-fresh.state`. It must NOT
read/fork anything in `saves/` and stores ALL its states under
`omp_saves/` (brief: OMP_BRIEF.md at repo root; postmortem will land in
omp_saves/omp-fresh-postmortem.md). Coordinator (pane w19:p1E) triages
omp-fresh's field reports and lands harness fixes + doc updates; fixes
are announced back as `[coord] fixed ...` messages over Herdr.
NOTE for all sessions: a concurrent process swept root *.md into backup/
today; PROGRESS.md was restored from backup/PROGRESS.md — keep it at root.
Coord fix 1 (Aug 24): fresh-boot scripts/vega_intro.py named the player
"A<NAME>" — its poll loop pressed A every iteration THEN checked
keyboard_open(), so one stray A landed on the naming keyboard's home
tile before type_name() (proof: vega's own "AVEGA" entry below). Fixed
by checking keyboard BEFORE pressing; omp-fresh redoing its intro with
the corrected order.
Coord fix 2 (Aug 24): reorder alone was insufficient — keyboard_open()
is screen-TEXT based (DEL/END glyphs, trek.py:905) so it lags the window
by several frames; the loop still committed one A after a true open.
omp-fresh diagnosed the flag lag live; vega_intro.py now does
press(".:20") -> tick(10) -> recheck BEFORE press("A:4"). Backlog
candidate: event-driven ui.keyboard flag (hookevents _2DMenu anchor or
a WRAM naming-state read) instead of screen-text sniffing.
Coord fix 3 (Aug 24): Driver.save(name) force-joined EVERY name onto
saves/ (Path(SAVES_DIR)/name), so omp-fresh's d.save("omp_saves/...")
silently wrote saves/omp_saves/... — milestone lost + isolation rule
broken by the harness itself. Now: bare names -> saves/, path-like
names honored verbatim (staticmethod Driver._save_target; unit tests
tests/unit/test_save_paths.py; full suite green). omp-fresh's starter
milestone recovered to omp_saves/omp-fresh-starter.state.
Coord fix 4 (Aug 24): goto's internal flush_dialog drains could mash A
through a MATERIALIZING naming keyboard (NameRival scene): hooks path
_flush_dialog_hooks pressed A on page_wait with no keyboard gate, and
text-based checks lag the render (same class as fix 2). Fix: new
Driver._naming_sig() WRAM signature (wNamingScreenType/DestPointer —
NamingScreen.asm writes them BEFORE rendering); both flush paths stop
pressing A the moment it changes and hand to dismiss_keyboard, which
now CLEARS stray chars (B=backspace xN, verified in source) before
typing/confirming — garbage names like "AA" can no longer be committed.
Suite green. Backlog intake from claude-wren postmortem: Sprout Tower
mapgraph stairs wrong both directions (b), first-call menu-setup
swallow in use_item/heal/mart_buy (c), 1F held-direction door exit (d).
Coord fix 5 (Aug 24, close-out): goto's whiteout-abort return left
last_goto_reason=None — now "whiteout-abort". (omp-fresh also reported
goto claiming success/no-reason with unchanged pos ×3; needs a live
repro before touching the arrival heuristics.)
MISSION COMPLETE (Aug 24): omp-fresh reached ZEPHYR BADGE from raw
power-on — omp_saves/omp-fresh-zephyr.state frame 385988, QUILAVA L15,
Togepi egg secured post-badge (omp-fresh-egg-in-party.state), zero
saves/ contact. ~386k frames / ~1h45m play over ~2h wall incl. 3 burned
naming forks. Postmortem: omp_saves/omp-fresh-postmortem.md.
IMPLEMENTATION ROUND 2 (Aug 24, post-signoff feedback pass; commits
1de2dcd + 49135ae on top of the clean baseline):
- heal race ROOT-CAUSED live (zephyr repro): _nurse discarded
  flush_dialog's 'menu' at the YES/NO box -> nobody answered YES ->
  stale pre-jingle HP raise. Fixed: deliberate YES via menu primitive +
  HP-keyed jingle settle. Also _flush_dialog_hooks now falls back to
  glyph-gated paging when a drained page_wait goes unactionable with a
  box visibly up. Verified live: raised before patch, HEAL OK 46/46
  after, second call clean.
- mart_buy raises loudly (shop-not-open, bag shortfall); qty picker
  verifies the xNN glyph per press (RIGHT/LEFT/UP/DOWN semantics).
- registry action `drain_scene` exposed (+ A-deaf B fallback);
  Driver.scene_busy() helper (sm==0 alone lies).
- goto: same-map out-of-bounds target fails fast with a wrong-map
  reason; GAVE UP hints d.trip_scenes on script-scene-active.
- train(): per-battle log now carries the party snapshot.
STILL OPEN (needs own sessions):
1. fight() outcome enum + auto-drain of victory chains (API change —
   callers + registry contract must move together).
2. map_view vs _grass_cells glyph mismatch on ROUTE_31 (data dig into
   collision bytes vs glyph table).
3. observe(): npc identity (sprite id/object_const) + player facing;
   nick vs nickname key unify; curated flag-list expansion.
4. ui.textbox dual-signal for non-$79 border renders — deliberately
   deferred: flush fallback covers the practical case; a loose glyph
   OR risks A-mashing inside shop/pack menus (gotcha 13).
ROUND 3 (Aug 24, team-building enablement; commit f4bc039):
omp-fresh's Operator Q&A answered the solo-run question (deliberate
risk-economy: no catch-on-sight primitive made catching high-risk-
low-reward) and its harness asks are now served:
- registry `catch_up` (nickname/ball/max_balls/max_encounters): grass
  belt pacing + wild engagement + ball budget in ONE call, structured
  outcome. Live-proven: BELLSPROUT->PEBBLE on the zephyr timeline.
- observe()['enemy'] while battling: species/level/hp for targeting.
Still open from Q&A: lead-swap/PC-deposit registry surface,
per-mon train() targets, roster-as-checkable-contract is a BRIEF
mechanism (next run's mandate), persona must be explicitly wired into
the instruction chain (persona.md existed; nothing pointed at it).
(step_dir warp-hold from the addendum is already covered by
_held_warp_entry/_step_warp_tap in the committed nav work.)

ROUND 4 LAUNCH (Aug 24): **moss-run** (same pane w19:p1F, fresh
timeline) owns persona run MOSS → ZEPHYR then HIVE badge. Brief:
OMP_BRIEF2.md + persona_moss.md — roster is a CLI-checkable contract,
strengths/weaknesses ledger (`omp_saves/moss-ledger.md`) is an explicit
operator mandate ([W] weak points are the product). All states
moss-* under omp_saves/. Smoke-test throwaway state omp-smoke-catch.*
may be gc'd by the moss session once intro lands.

## session omp-fresh owns fresh-boot run to Zephyr Badge, working state omp_saves/omp-fresh.state

**OBJECTIVE COMPLETE Aug 24 2026: ZEPHYR BADGE WON.** Milestone
`omp_saves/omp-fresh-zephyr.state` (verified frame 385988 VIOLET_GYM,
badges 1/8); final checkpoint `omp_saves/omp-fresh-egg-in-party.state`
(Violet PC healed, Togepi egg in party). Party: QUILAVA L15 46/46
(TACKLE/LEER/SMOKESCREEN/EMBER) + TOGEPI egg. Bag 10 balls, 3 potions,
TM31; ₽2539. Rival **AXIOM** (verified via CLI decoder).
Milestones this session (all in omp_saves/, new filename per milestone):
intro2 / starter / lab-exit / egg-got / rival-named / egg-delivered /
quilava-l14 / pregym / zephyr / egg-in-party.
Postmortem with ranked framework fixes: `omp_saves/omp-fresh-postmortem.md`.
Key gotchas discovered: goto's internal GAVE-UP drain mashes THROUGH
naming keyboards (cost 3 forks before manual-step workaround — coord has
since fixed in harness); Togepi egg aide appears only AFTER Zephyr badge
(SPECIALCALL_ASSISTANT fired by VioletGym.asm); Elm's "disaster!" phone
call fires BEFORE delivery too (blocks R30 (17,6)); R29 catch-tutorial
cells (53,8)/(53,9) seal the route corridor — trip_scenes or manual walk;
heal_pokecenter race hit 3x (verify-before-drain), manual drain fallback
works every time; mart qty selector keys are RIGHT=+10 LEFT=-10 UP=+1
DOWN=-1 and swallow presses unpredictably — verify ×N on screen per press.

## harness upgrade (ox-alpha, Aug 24 2026) — ai-plays-pokemon adoption

Engineering pass on the driver itself; NO timeline advanced. All existing
milestones/working states remain valid and load unchanged (provenance
guard warns once on legacy sidecars, then proceeds).

- **Pins + provenance:** `pyproject.toml` pins `pyboy==2.7.0` (savestate
  format is version-coupled). `Crystal.save` now stamps every `.meta`
  with pyboy version + ROM sha256; `Crystal.__init__` REFUSES to load a
  state stamped for another pyboy/ROM (legacy unstamped sidecars warn and
  load). Never rebuild the ROM without expecting old forks to be refused.
- **One action registry:** `crystalagent/registry.py` — single source of
  truth used by serve.py `run`, autopilot decisions, and documented legs.
  Unknown verbs/kwargs/preconditions (`ui.battle`) are rejected with a
  sentence instead of corrupting play. Fixed en route: serve.py cmd_run
  NameError (every `run` was broken), trek leg drift (catch/fight/flush/
  heal/route29 were unreachable, mart a silent no-op), autopilot
  journaling ok:true alongside errors.
- **Observability:** trek diagnostics are `logging` records on stderr
  (stdout is pure protocol); journal lines carry wall-clock `t`, cycles
  carry `used` frame spend.
- **Tests:** `tests/unit/` — battle math vs the real type chart, nav BFS/
  ledges/ice/side-walls/blocked cells, sym/charmap/input parsers, state
  decoders, menu classifiers, registry contract, rolling memory. Marker
  meta-test enforces discipline. Run: `.venv/bin/python -m pytest tests`.
  `scripts/trek_selftest.py` still passes (emulator-in-loop).
- **Knowledge layer:** `crystalagent/rolling.py` hierarchical rolling
  memory (SQLite, LEAF 20 / SOFT 100 / 3000 chars; naive summarizer until
  an LLM one is plugged in). Autopilot stdin gained `{"cmd":"memory"}`;
  cycle replies carry `mem_tail`. `trek map [MAP]` renders the reachable-
  region ASCII view with global-coordinate rulers and pruned legend —
  read-only leg. `crystalagent/schemas.py` validates observe/game_state/
  route/NDJSON/decision/journal shapes at the boundary (dicts out, loud
  errors in).
- **Event hooks:** `crystalagent/hookevents.py` registers signature-
  validated PyBoy hooks at sym anchors — PromptButton 00:0AAF (text page
  waiting), _2DMenu 00:202A + InitVerticalMenuCursor 00:1C10 (menus),
  ExitBattle 0F:769E. Verified live: START menu -> menu_open, neighbor
  dialog -> page_wait. flush_dialog goes event-driven when hooks are live;
  legacy cadence now refuses to mash when a menu cursor sits outside the
  textbox (returns "menu"). Kill switch: CRYSTAL_HOOKS=0. Signatures are
  ROM-build-coupled: after a ROM rebuild hooks self-disable (warning) and
  polling heuristics take over.
- **watch SSE:** `/stream` pushes snapshots+events at 4 Hz (max 4
  clients); page uses EventSource and falls back to the old pollers.
  Feed events persist to `saves/<base>.watch.jsonl` and survive restarts.
- **Checkpoint GC:** `trek gc [--apply] [--keep N]` — dry-run default,
  deletes 1-byte stubs + stale numbered series (newest N kept per
  series). Protects default.state, watch.state and anything named in
  PROGRESS.md.
- **DESIGN.md** documents the decision-boundary doctrine (9 rules).
- **oxa-johto field feedback, triaged same day:** mart_buy shop-list race
  fixed (passive wait-for-¥ after talk_to, no A presses in the window);
  heal_pokecenter drains straggler pages before HP verification; goto
  failures now set `d.last_goto_reason` (no-path / no-progress +
  script-scene-active / replan-storm / pass-cap / last-block=...) and the
  GAVE UP log carries it. trek map '@' off-by-one report did NOT
  reproduce (glyph column == pos() x on test state); watching for a repro.

## session oxa-johto owns fresh Johto run, working state saves/oxa-johto.state

Fresh timeline started Aug 24 2026 (ox-alpha operator mission): raw boot,
player **OXALPHA**, rival **OMEGA** (named at the Elm-lab cop scene on the
post-errand return — Crystal moved it out of the intro, confirmed in
maps/ElmsLab.asm NameRival special).

**ZEPHYR + HIVE BADGES WON** (Aug 24). Milestones this session:
`oxa-johto-intro` (bedroom), `oxa-johto-starter` (New Bark),
`oxa-johto-rival-named` (lab), `oxa-johto-egg-delivered`,
`oxa-johto-rival-beaten` (Cherrygrove), `oxa-johto-violet`,
`oxa-johto-zephyr-badge`, `oxa-johto-rival-beaten`, `oxa-johto-well-cleared`
(Slowpoke Well rockets all beaten), `oxa-johto-hive-badge`.
Working state `saves/oxa-johto.state` = healed AZALEA_POKECENTER_1F.
Party: QUILAVA "CINDER" L21 (Tackle/Leer/Smokescreen/Ember... verify with
d.lead()['moves']), TOGEPI EGG unhatched. Bag: 14 Poké Balls, LURE BALL,
TM31 Mud-Slap. Money ₽4754.

Story gates hit en route (all cleared):
- R32 Cooltrainer M blocks southbound until Zephyr badge + Togepi egg
  received (coord_event 18,8 SCENE_ROUTE32_COOLTRAINER_M_BLOCKS; he then
  hands you MIRACLE SEED — not collected yet, he's at R32 (19,8)).
  Egg comes from ELMS AIDE at VIOLET POKECENTER (not Elm's lab!).
- Slowpoke Well required before gym: Rocket grunt squats the AZALEA GYM
  approach until EVENT_CLEARED_SLOWPOKE_WELL. Kurt trigger = talk to KURT
  at his house (3,2) — NOT the granddaughter (5,3).
- Azalea gym maze: twins don't aggro from row 11; cross row 11 east and
  climb column 8 through Al's sight line.

Session gotchas (new):
- game_state "name" shows the SPECIES string; true nickname is a separate
  party field — verify with d.lead()['nickname'].
- talk_to can return 'talked' while hitting an INERT object (Kurt's
  granddaughter, item balls): cross-check maps/<map>.asm object_event
  coords before assuming a talk worked.
- After importlib.reload(trek)+new Driver, any stale kernel var bound to
  the OLD Driver's emu presses buttons on a GHOST emulator — always
  rebind raw-input refs to d.emu after a reload.
- fight() cells need tight frame budgets: a 30000-frame pre-drain +
  unbounded fight() blew a 900s eval timeout mid-battle (kernel survives;
  battle state was clean after interrupt).
- Union Cave 1F: entrance pocket exits via ROW 2 westward to x=9-11,
  NOT straight south (side-wall tiles $b2 block down at x=15-16).
**FOG BADGE WON Aug 24 — GOAL COMPLETE: new game driven to the 4th gym.**
Badges 4/8 (ZEPHYR HIVE PLAIN FOG). Party: QUILAVA "CINDER" L33 (CUT/
FLAME WHEEL/QUICK ATTACK/EMBER — Mud-Slap was replaced by level-up Flame
Wheel during the gym fights), TOGEPI L6. Milestone savestate (gc-proof):
`journal/oxa-fog4-gym-done.state` (+ .meta) — outside Ecruteak Gym.
`saves/` is UNSAFE for this series right now: a concurrent session's gc
deleted my named checkpoints within seconds AND overwrote
saves/backup/oxa-johto-working.state with its own data. Keep oxa-johto
forks in journal/ until the gc protection is fixed.

Route taken: Azalea west rival (Croconaw L16 — needs potions, solo fire
eats 2x water; 2 whiteouts before winning at full HP + 3 potions) -> Ilex
Farfetch'd herd (bird flees the direction you FACE; chase table in
maps/IlexForest.asm) -> Charcoal Master HM01 -> use_cut(8,25) forgets
TACKLE -> R34 trainers -> Goldenrod -> Whitney (won inside talk_to's
auto-fight; badge needs the Bridget cries coord-event at gym (8,5) via
step_hold, then re-talk) -> R35 grind to L26 -> Dept Store 2F SUPER
POTIONS (clerk counter faces DOWN from row 3, not up!) -> R36 Floria
(33,12) -> Goldenrod Flower Shop: talk SHOP Floria (wanders ~(5,6)) THEN
teacher -> Squirtbottle -> Sudowoodo at (35,9) yes/no -> R36 -> Ecruteak
-> Burned Tower (gym is LOCKED until the beasts: SCENE_FORCED_TO_LEAVE
ejects you) -> 1F rival battle (sight line row 9) -> fall -> B1F beasts
scene at (10,6) -> Eusine talk -> ladder (7,15) -> Morty.

Morty fight (Gastly L21, Haunter L21, Haunter L23 — no Gengar in this
build): fight() CANNOT drive this battle — it times out mashing A. Root
cause chain: (a) the battle-intro HP-bar scroll leaves wEnemyMonHP=0
until the intro text is advanced — poll (1,d216) > 0 and A-mash until
then; (b) the 2x2 battle menu renders FIGHT=0x85,0x88,0x86,0x87,0x93 in
wTilemap — detect THAT, not the decoded text; (c) menu cursor via
wMenuCursorY: FIGHT=(1,1), PACK=(2,1); move list slot = same var. Manual
driver: A until FIGHT pattern -> A -> cursor to slot -> A -> A-mash text.
Flame Wheel burned Haunter down. DO NOT whiteout mid-battle: the
auto-heal heals party WRAM but leaves the battle struct at 0 HP ->
zombie battle that re-poisons every retry; reload a clean savestate
instead.

Next objective (unclaimed): Olivine City (R39 coast, lighthouse Amphy ->
Jasmine -> Steel badge), Cianwood Chuck along the way. Catches still
pending (14 balls). TOGEPI hatched en route.
Operator Q&A Aug 24 (hub send to Main still self-routed/no peers; logged here):
1. Team: TOGEPI slot is an unhatched egg (egg:true, 0/18 is normal), not a
   faint. Solo-until-Zephyr was deliberate; catches start now — GEODUDE in
   Union Cave (tank), POLIWAG via R32 Old Rod (water/HM mule), flyer as CUT
   mule. 14 balls in bag.
2. Moves: forget order LEER > SMOKESCREEN > TACKLE; CUT goes to a bird, else
   forgets TACKLE on Quilava; always re-read d.lead()['moves'] first.
3. Harness fixes wanted: goto GAVE UP reason codes (pending-scene storms),
   mart_buy shop-open race + cursor verify (cost me ₽1800 of accidental
   balls), heal_pokecenter drain race; trek map @ renders one col right of
   pos() x. MVP tools: trek map, event hooks, goto auto-fight.


## session vega owns fresh Johto run, working state saves/vega.state
Fresh timeline started Aug 24 2026 (second ox-alpha mission): raw boot via
`scripts/vega_intro.py`, player **AVEGA**, rival to be **NOVA** (typed at
the Elm-lab cop scene on the post-errand return).

Milestones: `vega-intro` (bedroom), `vega-outside`,
`vega-lab-exit` (aide balls received), `vega-starter` (CYNDAQUIL L5),
`vega-pre-errand`. EVENT_GOT_MYSTERY_EGG_FROM_MR_POKEMON SET, CYNDAQUIL L7.
Milestones so far: `vega-rival-named` (**NOVA**, typed on the lab cop-scene
keyboard), `vega-egg-delivered` (EVENT_GAVE_MYSTERY_EGG_TO_ELM SET),
`vega-cherrygrove-2` (healed), `vega-violet-healed` (CYNDAQUIL L11 33/33,
healed VIOLET_POKECENTER_1F). Money ₽3000, no badges yet. Next objective:
supplies at Violet Mart -> Sprout Tower grind -> FALKNER (Zephyr).

**ZEPHYR BADGE WON** (Aug 24). Milestone `vega-zephyr-badge.state`
(VIOLET_GYM (5,2) post-fight). Party: QUILAVA L19, TOGEPI "A" L5 (hatched
en route; see hatch gotcha), WOOPER L5 caught in Union Cave. ~9 Poké Balls.

**HIVE BADGE WON** (Aug 24, late). All Slowpoke Well rockets beaten
(GruntM29/M2/M1/F1 — talk-to-trigger; sight-walking was unreliable), Kurt
finale auto-set EVENT_CLEARED_SLOWPOKE_WELL. Bugsy beaten by QUILAVA L27.
Party: QUILAVA L28, TOGEPI "A" L5, WOOPER L9. ₽3216.
Working `saves/vega.state` = healed AZALEA_POKECENTER_1F.
Union Cave traversed successfully via d.travel("ROUTE_33") from inside the
cave — the harness router handles the internal warps correctly; manual
step-walking does NOT (live layout diverges from nav grid mid-cave).

NEXT (HIVE badge checklist):
1. Slowpoke Well: re-enter via town door at (31,7) [NOT (17,16) — that's
   where you exit]. GruntM29 (15,7) beaten; remaining: GruntM2 (5,6),
   GruntM1 (5,2), GruntF1 (10,4) — sight trainers, walk their lines.
2. Talk to KURT inside the well after the last rocket -> sets
   EVENT_CLEARED_SLOWPOKE_WELL, clears the rocket squatting the gym path
   at town (10,16).
3. Bugsy gym (door 10,15). Twins don't aggro from row 11; cross row 11
   east, climb column 8 through Al's sight line (oxa-johto notes).

Cave gotchas (new):

- fight() party-menu wedge ("QUILAVA is already out" frozen screen):
  recover = B to close menu, then trek.Battle(...).play(default policy).
- The entrance pad (17,2)/(17,3): stepping D anywhere on it EXITS to R32.
  After entering, step L first; blacklist pad cells in any walker.
- Wild battles interrupt constantly in UC1F/R32 grass: check d.battle()
  every iteration; fight(policy=flee_policy) preserves HP/PP when just
  traveling. Quilava wiped twice vs ONIX/GEODUDE with 0-PP damaging moves.

More fresh-boot gotchas:
- d.goto() often does NOT raise on failure (logs GAVE UP, returns) --
  ALWAYS check d.pos() after goto instead of relying on exceptions.
- Violet Gym: keepers sight-fire on the middle column; fight them at
  full HP, then Falkner at (5,1) from (5,2) facing U. Wiped once entering
  at 21/45 HP; won cleanly at full HP L14.
- TOGEPI EGG HATCHES while walking (~1000+ steps). The hatch naming
  keyboard can appear MID-ROUTE and wedge movement (wScriptMode=1):
  close it with START+A (fast minimal name) — B-cancel does NOT close it.
  After our wedge, Togepi stayed 0/18 "fainted" until a nurse heal.
- Mart purchases: mart_buy's shop-open race still bites. Hand-rolled flow
  that works: face clerk, A once, WAIT passively ~4s for BUY/SELL/QUIT,
  A on BUY, cursor is already on POKé BALL, A opens qty(=1)+YES/NO,
  A confirms. Each cycle = 1 ball ₽200. wNumBalls WRAM read unreliable;
  verify by money delta.
- ALL of saves/ got swept into saves/backup/ by another process mid-session
  (second occurrence today). Restore with cp saves/backup/vega\* saves/.
  Consider per-session subdirectories.
- Sprout Tower 2F sage Nico (SPINRANDOM_FAST) permanently blocks the only
  south exit of the NE pocket and never triggers his own sight battle;
  tower skipped entirely (not required for Zephyr). Left unexplored.

Earlier fresh-boot gotchas:
- Elm's egg-delivery + officer scene: cop/rival naming fires as a coord
  event at lab (4,5)/(5,5); BFS seals it -- walk the aisle with step_hold
  and drain until keyboard_open(), then type_name().
- Elm's phone call ("It's a disaster!") blocks ROUTE_30 (17,6) after the
  egg; drain it before travel.
- R30 x=5 corridor: Mikey (5,23) + Joey cutscene objects; after the egg is
  delivered EVENT_ROUTE_30_BATTLE despawns the rattatas, but Mikey still
  blocks the single-tile corridor -- hop WEST over the ledges at row 24
  (step_hold L from (5,24)), then BugCatcherDon sight-fight at (1,10),
  then goto(5,4).
- to_violet leg works from R30 north end; R31 gate exit west warp lands
  VIOLET_CITY (37,25).
- ALWAYS d.save() in a finally block -- several crashes lost walked miles.

Gotchas (fresh-boot specific):
- step_hold() does NOT warp out of PLAYERS_HOUSE_2F stairs; raw press
  ("U:60 .:60") works. goto across a live textbox replan-storms -- drain
  scenes BEFORE nav, and after row-4 coord-event scenes step OFF the row
  before routing.
- Elm's lab: entry auto-dialog fires once; must FULLY drain Elm's speech
  (talk at (5,3) facing U) before ball tiles respond; ball A-press often
  needs one retry. Leaving the lab first time = aide balls scene mid-goto;
  drain then goto again.
- errand1 leg's flush_dialog(30000) is NOT enough for Mr Pokemon's egg
  scene -- verify EVENT_GOT_MYSTERY_EGG_FROM_MR_POKEMON after; explicit
  talk + long drain fixed it.


---

_Second timeline started Aug 23 (ox-alpha): fresh boot, player "GOLD",_
_CYNDAQUIL L5 chosen at Elm's Lab. Milestone `saves/cyndaquil-start.state`_
_(frame 32131), working state `saves/ox-alpha-new.state`. New Bark Town (6,5)._
_Last updated: session of Aug 23 2026 (claude-lex: 7 badges, Ice Path in progress). Field notes in FABLE_FEEDBACK.md -- read it for working techniques before resuming._

_omp_speed_run (ox-alpha, Aug 23): **JOHTO CHAMPION — Elite Four + Lance cleared.**
Milestones `saves/e4-{will,koga,bruno,karen,lance}-won.state` and
`champion-omp.state` (post-credits, New Bark). Working state
`saves/omp_speed_run.state`. Party: TYPHLOSION L63 (Strength/Cut/Swift/
Flamethrower), POLIWAG L4, TOGEPI L15. Details at the bottom of this file.

_Second timeline started Aug 23 (ox-alpha): fresh boot, player "GOLD",_

- **codex-luna fresh run:** New game completed, Cyndaquil chosen, and Violet
  City reached/healed. Working state `saves/codex-luna.state`; checkpoint
  `saves/codex-luna-plain.state` (Plain Badge, frame 946032), with prior
  `saves/codex-luna-hive.state` (frame 596685) as the clean Hive checkpoint.
  Player UNA, rival AA, QUILAVA L25 and Togepi egg, with Zephyr, Hive, and
  Plain Badges. The Ilex gate loop was resolved by completing Farfetch'd,
  obtaining HM01, teaching Cut, and cutting the tree at (8,25).

  Loop postmortem + anti-loop harness guards (whiteout abort, goto seam
  guard, save() rollback refusal, `trek verify`/`trek states`): see
  "Harness round 7" below.

- Checkpoint to resume from: **`saves/claude-lex2.state`**
- Badges (7/8): ZEPHYR HIVE PLAIN FOG MINERAL STORM GLACIER -- only RISING
  (Clair, Blackthorn) left.
- Position: ICE_PATH_B1F, mid boulder->hole puzzle (Strength armed).
  Full verified push plan + warp graph for the Blackthorn crossing is in
  FABLE_FEEDBACK.md "Where the run stands".
- Party: TYPHLOSION L47, POLIWAG L4 (Surf), TOGEPI L10. Money ~₽26k.
- GOTCHA: boulder pushes land ~60-100 frames AFTER step_dir returns --
  wait .:100 and re-read npc_cells before pressing again. Boulders reset
  on map re-entry; re-arm Strength per map entry.


## team-run (balanced team, this session)

- Fresh boot replayed with TYPED names: player GOLD, rival SILVER
  (named at the Elm-lab cop scene, NOT the intro -- Crystal moved it),
  CYNDAQUIL nicknamed CINDER. Milestone `saves/team-start.state`
  (New Bark free-roam, egg delivered, 5 balls).
- Roster so far (party verified in WRAM): CINDER CYNDAQUIL L11,
  IVY BELLSPROUT L5 (Route 31), GUST PIDGEY L4 (Route 31),
  BOULDER GEODUDE L4 (Dark Cave), HAUNT ZUBAT L2 (Dark Cave).
  6th slot SPLASH (POLIWAG, Old Rod) pending Zephyr badge.
- SUBSTITUTIONS vs plan: nite encounters are UNREACHABLE in this
  harness (see gotcha below) -> ORACLE/HootHoot became GUST/Pidgey,
  QUAGMIRE/Wooper became SPLASH/Poliwag (Old Rod, Violet pond group),
  HAUNT/Gastly became HAUNT/Zubat (same cave flavor, day table).
- Tools added: `scripts/wilds.py` (encounter tables, --grep),
  `Driver.train()` + `trek train LEVEL STATE` (rotation trainer with
  nurse rail). Trainer fixes landed in battle.py: USE-popup retry in
  use_battle_item, faint-guard in _forced_switch_up (potion target
  list used to wedge battles 90k frames each).
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

For codex-luna: the third badge is complete. Resume from
`saves/codex-luna-plain.state` for the actual fourth-badge objective: clear
the Burned Tower rival prerequisite and defeat Morty for the Fog Badge. Keep
the codex-luna state separate from the older multi-agent run below.

Head south through Route 32 (Violet City south exit around (19,42) area —
check maps/VioletCity.asm warps) toward the Route 32 Pokecenter, then
UNION CAVE. Grind POLIWAG up alongside Cyndaquil. Next badge is HIVE
(AZALEA, far south through Ilex Forest) — the journey legs need extending
(`to_azalea`). Checkpoints: `route32.state`, `union-cave.state`.

## Active sessions

| codex-luna | Plain Badge complete (3rd); next objective is Morty/Fog Badge | `saves/codex-luna.state` |
| retard_cannon | fresh save -> 8 Johto badges, speedrun | `saves/retard_cannon.state` (forked default.state @ frame 106506, Route 29) |
| ox-alpha (new game) | fresh boot -> Cyndaquil chosen | `saves/ox-alpha-new.state` (milestone: `cyndaquil-start.state`) |
| omp_speed_run | owns Elite Four -> Lance (Champion) | `saves/omp_speed_run.state` (forked from claude-lex2 @ frame 9125030, Indigo Plateau PC) |

| session | owns | working state |
|---------|------|---------------|
| tower agent | Sprout Tower -> Elder Li | `joey.state` (frame 238979, SPROUT_TOWER_2F) |
| ox-alpha (visibility) | L14 CYNDAQUIL, staging for Falkner rematch #3 | `visibility.state` |
| ox-alpha (p9) | done: mart_buy + step_hold + 2nd party member (`two-mon.state`) | `saves/ox-alpha.state` (SUPERSEDED by goal run below — file re-forked) |
| director | Union Cave traverse -> Azalea/Hive badge | saves/director.state |
| ox-alpha (goal run) | owns Goldenrod -> WHITNEY (Plain Badge) | `saves/ox-alpha.state` (forked from claude-lex2 @ frame 1132973, Dept Store 2F) |
| claude-lex | **DONE: PLAIN BADGE WON** (see milestone `plain-badge-healed.state`) — ox-alpha goal run above is now redundant, stand down | `saves/claude-lex2.state` (frame 2287621) |
| team-run | fresh-boot balanced-team run: 6 nicknamed mons, rotation training, badges Zephyr->Hive minimum | saves/team-run.state |
| ox-alpha (MoE) | owns MoE multi-agent harness build — worktree `../MoE-multiagents`, branch `MoE-multiagents` (plan: `MOE_PLAN.md`; scaffold committed `a9e152c`; conductor smoke green). No game-run ownership; smoke fork `MoE-multiagents/saves/moe-dev.state`. Zen key is FREE-TIER ONLY (`x-preview-f-free`, flaky 503/empty today) | n/a (harness work) |
| oxa-johto | fresh boot -> Zephyr badge progression | `saves/oxa-johto.state` |

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
| cyndaquil-start.state | 32131 | NEW TIMELINE: GOLD, CYNDAQUIL L5, New Bark Town |
| team-start.state | 240755 | TEAM-RUN: GOLD/SILVER/CINDER, egg done, New Bark |
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
- TEAM-RUN: PyBoy 2.7's in-game RTC reads are BROKEN (hHours reads
  constant garbage 201 -> GetTimeOfDay yields MORN only). Nite
  encounters are unreachable; day/morn tables only. wStartHour shifts
  the (broken) result but never reaches NITE. DO NOT use pyboy
  gameshark codes or hook_register: both CORRUPT the savestate
  (signature: garbage battle, PRIMEAPE L0 14592/14592, map=?).
  wTimeOfDay can be written per-frame but VBlank's UpdateTimeAndPals
  re-clobbers it before every encounter roll (race always lost).
- TEAM-RUN: post-battle leftover modals silently eat ALL movement
  input -> looks like '400 steps, zero encounters'. train() now runs
  close_menus() when menu_open() before pacing.
- TEAM-RUN: cross-map travel planner fails ROUTE_29 (59,8) -> west
  exit (0,6) with 'no static path' (corridor is open; same cells
  path fine from the east side). Hand-walk: L*55 U*2 L*4 L.
- TEAM-RUN: mart_buy qty can overshoot by +1/+2 (picker UP presses);
  verify bag counts and budget for it (cost ~400 extra once).
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

## Harness round 7 (ox-alpha, Aug 23): anti-loop guards + audits

Code changes (trek.py), live-tested on throwaway forks:
- WHITEOUT GUARD: fight() detects wipes (money dropped + every non-egg
  party member at full HP after battle = whiteout teleport; full HP
  alone proves nothing). goto/walk/talk_to/travel consume the flag and
  ABORT the leg by default instead of walking back into whatever just
  killed us (Falkner retry loop, director's 11x rival loss-loop).
  d.whiteout_policy = 'continue' restores blind resuming.
- GOTO SEAM GUARD: goto counts executed (from,to) map seams per call;
  crossing one 3x raises TravelError instead of ping-ponging (the
  Ilex-gate churn class above).
- save() refuses to overwrite a state whose .meta frame count is newer
  than the running emulation unless force=True -- accidental rollback
  now fails loudly inside the harness.
- New read-only legs: `trek verify FLAG...` (event flags plus bare
  badge names like HIVE/ZEPHYR_BADGE; exit 1 if any missing or unknown)
  and `trek states` (saves/ table: frames from .meta, META MISSING
  marker, age).

Ilex-loop postmortem (analysis session): the router model was SOUND --
a clean 43-move plan Azalea -> ILEX_FOREST_AZALEA_GATE -> forest
exists, the west connections dead-end in collision data, and sealing
the rival-neck cells yields 'no static path', not wandering. The churn
came from replanning while displaced east of Azalea: from the R33 /
UnionCave seam the shortest modeled route backtracks through exactly
those maps, and the cave mouth is saturated with trainer sight-lines,
so every pass fought and replanned. Anchor at a known waypoint after
any interrupted leg before re-running goto.
Live repro on a hive fork: SCENE_AZALEATOWN_RIVAL_BATTLE stays armed
(scene var 1) until the rival cutscene completes, so the (5,10)/(5,11)
neck cells trip a fight forever when the lead can't carry it. With the
whiteout guard the same attempt loses ONCE, prints [WHITEOUT] with
position, and aborts. Trigger and WIN that fight fresh before routing
through the neck.

State hygiene: codex-luna-plain-corrected.state, r42-stuck.state,
unitrun-pre-1/2 have NO .meta sidecar (frames unknown). The codex-luna
prefix kept moving during analysis (working state hit frame 1590995;
sudowoodo/ecruteak/rival-failed/gym-locked checkpoints appeared) --
treat any codex-luna* file as potentially live from another session:
fork before risky work, promote milestones under NEW names.

Round 8 (same day, "straight-through" hardening) -- two more bugs found
by live repro and fixed:
- THE SCENE SEAL NEVER SEALED: _refresh_nav_blocks built the per-map
  cell set but never stored it (blocks[const] = cells was missing), so
  nav.blocked was ALWAYS {} -- Route 32's Cooltrainer oscillation, the
  Azalea-neck rival trips mid-goto, and the director loss-loops all
  walked straight through "sealed" cells. Fixed + live-verified: armed
  AZALEA_TOWN neck now yields a clean 'no static path' refusal instead
  of three wipes. NOTE: routes that used to stumble through armed scenes
  will now abort -- that was the bug. Deliberate crossing still goes
  through d.trip_scenes = True for the one goto.
- WARP-TILE GOALS CAN'T LIE: goto's 'arrived through warp' shortcut
  accepted landing-side proximity on ANY map, blessing e.g.
  `goto 3 7 AZALEA_POKECENTER_1F` as success while standing OUTSIDE on
  the street (silent objective skip). Now requires cur_map == goal_map;
  ill-posed door-tile targets fail loudly via the seam guard instead.
  Door-exit pattern unchanged: omit map_name and target the CURRENT
  map's exit tile.
Also verified: battle.py 0-PP handling is already hardened (pre-check +
  rejection-screen detection + wedge guard); the PROGRESS 'STILL OPEN'
  note predates those fixes.

Round 9 (same day, continued straight-through hardening):
- WHITEOUT DETECTION now also trusts play()'s 'wipe' outcome directly
  (the money heuristic remains as the cutscene-resolve / broke-trainer
  fallback -- a loss that drops Y0 used to be invisible).
  fight() on 'timeout'/'stuck' dumps the frozen screen + both sides'
  HP/moves before returning, so wedges are diagnosed on attempt ONE
  (the Bridget/Jigglypuff freeze burned ~10 blind retries).
- route() REWRITTEN as cost-aware Dijkstra (walk distance + flat
  per-transition beat). Hop-count BFS treated the 20-map Johto ring
  (Azalea->R33->UnionCave->...->Goldenrod->R34->Ilex) as equal to the
  direct route and EXECUTED it whenever the direct approach was
  unavailable -- this is the mechanism behind the codex-luna Ilex loop
  AND Fable's Route-26 reversal. Plans costing more than
  DEFAULT_MAX_COST (700; override via route(dest, max_cost=...)) raise
  LookupError('detour ring') instead. Direct plans verified unchanged:
  Azalea gym -> gate = 2 transitions; -> Violet PC = 4 through the cave.
- route() also treats scene seals as plan truth: edges whose approach
  cells are behind an armed coord_event are dropped at PLAN time, so
  travel() fails with 'no path to any approach' up front rather than
  mid-leg.
- REGRESSION SUITE: scripts/trek_selftest.py -- 10 sections covering
  every guard from rounds 7-9, runs headless on throwaway forks in
  ~5 s, never touches saves/. Run it after ANY trek.py/nav.py change:
  .venv/bin/python scripts/trek_selftest.py

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

## BADGES 5+6: MINERAL + STORM (claude-lex, Aug 23)

Milestones: `mineral-badge-healed.state` (Olivine PC), `storm-badge.state`
(Cianwood gym, frame 3385501, TYPHLOSION L41). Party: TYPHLOSION L41
(Strength/Cut/Smokescreen/Ember), POLIWAG L4 (Bubble/Surf!), TOGEPI L10
(hatched mid-sea!). Badge bits note: MINERAL is bit 4, STORM bit 5
(state.py label order fixed — old logs said "STORM" for Jasmine's badge).

Big harness additions this session (all live-validated):
- SURF: nav routes water (nav.WATER, TrekNav override incl.), Driver
  .enable_surf(), _step auto-mounts (face water + A + YES — walking into
  water does NOT prompt in GSC). Sea legs Olivine<->Cianwood done 3x.
- teach_hm(tag, move): generalized HM teach ('H3' SURF -> Poliwag,
  'H4' STRENGTH -> first ABLE = TYPHLOSION, replaced Quick Attack).
- Learn-mode: fight() now LEARNS level-up moves (forgets first
  FORGET_PRIORITY match) instead of declining. attack() falls back off
  0-PP moves. _bag() key-items stride fixed (1 byte, not 2).
- heal_pokecenter verifies location+result (egg-aware).

Route/gotchas:
- Olivine rival coord event (13,12)/(13,13) seals the city corridor —
  trigger deliberately (step on it, flush; talk-only, sets NOOP).
- Lighthouse UP: 1F(3,11) 2F(5,3) 3F(13,3), 4F: fall hole (9,2)->D
  (0x60 tiles!), 3F(9,5) ladder glides through 4F to 5F(9,7), 5F(9,15)
  -> 6F. DOWN: east wing express — goto(16,4), step D repeatedly
  (holes at (16/17, 5/7/9/11/13) chain to 1F).
- Jasmine: SecretPotion handoff is JASMINE (8,8) on 6F, not Amphy;
  cutscene needs ~20k frames of patient flushing before
  EVENT_JASMINE_RETURNED_TO_GYM sets. Gym fight trivial for Ember.
- Sea crossing: proven column R40 (10,35) <-> R41 (40,3); Cianwood at
  R41 west edge row 10. Swimmer chains are DANGEROUS: two wipes (money
  7118 -> 1339) before learning to cross healthy + segment-save.
- Cianwood gym boulder puzzle: savestate-BFS proved the top is
  UNREACHABLE once Black Belt Lung (5,5) survives at the choke — the
  middle-boulder push wedges (4,4) and (5,4) is behind Lung. Applied the
  documented stuck-NPC remedy (zero Lung's wObjectStructs slot, restores
  on reload) and walked the right lane. If someone finds the legit
  solution, document it here.
- Chuck: talk from (4,2) -> he throws his boulder -> Primeape/Poliwrath,
  swept by best-move policy + Awakening/SP overlay (never needed).

NEXT: badge 7 GLACIER (Pryce, Mahogany): sail back, Ecruteak, Route 42
east (surf ponds), Mahogany, LAKE OF RAGE arc (Red Gyarados -> Lance ->
Rocket Hideout) unlocks the gym. Then badge 8 RISING (Clair): Route 44,
Ice Path (sliding puzzles — use savestate BFS), Blackthorn, Dragon's Den.

## BADGE 7: GLACIER + Ice Path (claude-lex, Aug 23)

Milestone: `storm-badge.state` -> `glacier-badge` chain; working state
`saves/claude-lex2.state`. Route 42 east (surf ponds), Mahogany, Lake of
Rage arc (Red Gyarados -> Lance -> Rocket Hideout), Pryce beaten. Then
Cianwood->Olivine->Blackthorn approach via Route 44 into ICE_PATH.

Current position: B1F boulder puzzle (plan verified against collision
bytes, partially executed -- full push list in FABLE_FEEDBACK.md).
Engine facts learned there: boulder pushes register ~60-100 frames after
step_dir returns (retry loops double-push); boulders RESET on map
re-entry and Strength must be re-armed per entry.

Harness additions this session: fade-aware close_menus, cursor_rows,
_party_cursor_to, talk_to(facing=), goto blocked-step self-diagnosis,
verified heal_pokecenter, key-item bag stride fix, battle wedge guard +
0-PP-aware attack() + learn-mode. Details: FABLE_FEEDBACK.md.

NEXT: finish the two boulder sinks, cross to Blackthorn (warp graph in
FABLE_FEEDBACK.md), Clair L37-40 Dragonair/Kingdra, Dragon's Den shrine.

## FRESH-RUN SMOKE TEST (ox-alpha2, Aug 23): power-on -> VIOLET CITY

Checkpoint: `saves/oxa9-run.state` (CYNDAQUIL L11, 5 Poké Balls, egg
delivered, aide balls received). Driven end-to-end by trek primitives:
new game -> starter -> Elm errand -> Mr Pokemon egg+dex -> return ->
VIOLET CITY. ~15 wilds fought en route incl. one WIPE mid-travel that
travel() recovered from autonomously (whiteout -> replan -> continue).

New-game boot recipe (not yet a primitive): raw Crystal(ROM), pulse
START every ~260 frames until the main menu decodes (title/GS logo are
tile art, no text), A on NEW GAME, A-mash intro, pick preset name,
poll until wScriptMode==0 overworld. Saved as ox-fresh-intro.state.

Gotchas found this run:
- grind() had NO idle cap -- pacing on a non-grass cell or a map seam
  spun forever until the shell timeout. Fixed: aborts 'no-encounters'
  after 300 fruitless steps.
- Route 30 rattata roadblock is story-gated: EVENT_ROUTE_30_BATTLE only
  sets when MYSTERY_EGG is handed to Elm (ElmsLab.asm:345). Talk-to-NPC
  does nothing before that.
- Saving mid-script poisons states (re-learned live): a d.save() during
  wScriptMode!=0 froze the lab scene; recovery = replay leg from clean
  checkpoint. fight()'s watch sidecar (.watch.state) prevents the worst
  variant of this.
- saves/ is shared: another live session deleted an ox-fresh-* scratch
  state mid-run. Unique per-agent prefixes are not enough -- treat any
  scratch state as volatile, save milestones immediately after verified
  transitions.
- Shell timeout clamped ~300s here (FABLE_FEEDBACK said 10 min): chunk
  long legs into <=250s primitive batches or use a persistent process.

## Session 2026-08-23 (Fable, claude-lex2 timeline) -- RISING badge + Indigo Plateau

**All 8 Johto badges obtained.** claude-lex2.state now sits healed inside
INDIGO_PLATEAU_POKECENTER_1F (milestone: indigo-plateau.state). Party:
TYPHLOSION L59, POLIWAG L4 (Surf/Waterfall/Whirlpool mule), TOGEPI L10.
Bag: 6 Max Potion, 9 Full Heal, 6 Revive, 8 Hyper, 8 Super, 4 Max Repel.

Route (Blackthorn onward):
- Blackthorn Gym: 2F sokoban solved (3 boulders into holes -> 1F bridges);
  wedge fix: Cody's sightline froze row 1 (all dirs blocked, wScriptMode=1)
  -- always ws()-drain before pushing. Clair beaten; wipe-causing battle.py
  pack bugs found & fixed (see commit 1c0410f).
- Dragon's Den: needs WHIRLPOOL (HM06 owned; taught to Poliwag). changeblock
  coords are CELL coords: gym 1F bridges appear exactly under 2F holes.
  Shrine quiz answers (Crystal): 1,1,2,1,2 -- wrong answers LOOP the same
  question, so cursor-blind mashing wedges. RISING badge -> rising-badge.state.
- Trek: Blackthorn -> R45 (one-way) -> R46 -> R29 -> New Bark -> R27 east
  (surf) -> Tohjo Falls (Waterfall up, ride down free) -> R26 north ->
  Victory Road gate -> Victory Road (3 ladder legs + rival ambush at
  (12,8)) -> Route 23 -> Indigo Plateau PC.

Milestones: gym2f-done, pre-clair, rising-badge, e4-prep, tohjo, vr-gate,
victory-road, route23, indigo-plateau (all .state under saves/).

Traps discovered (added to FABLE_FEEDBACK.md):
- travel() planner is unreliable near gates and reversed a whole
  Route-26 position back to Route 46 (cost ~40k frames). Hand-route
  long treks; never travel() toward VICTORY_ROAD/gate maps.
- Route29/46 gate south doors are COLL_WARP_CARPET_DOWN ($70): stand ON
  (4,7)/(5,7) and press D. Tohjo east door likewise: walk D onto (25,15).
- COLL_BUOY $27 is NOT surfable; whirlpool ($24) needs face+A+YES and
  respawns if a wild battle interrupts the crossing -- re-use it.
- step_dir false-'blocked'/'moved' in tall grass & after surf mounts:
  use raw d.press('<dir>:16 .:40') + coordinate verification.
- Victory Road one-way system: $b2 UP_WALL bands + $a3 hop-downs; the
  three-leg ladder chain is (1,49)->(1,35), (13,31)->(13,17), exit (13,5).

## CHAMPION — Elite Four + Lance cleared (omp_speed_run, ox-alpha, Aug 23)

**Run complete: all 8 Johto badges -> Elite Four (Will, Koga, Bruno, Karen)
-> Champion Lance -> Hall of Fame -> credits.** Milestones:
`e4-will-won.state`, `e4-koga-won.state`, `e4-bruno-won.state`,
`e4-karen-won.state`, `e4-lance-won.state`, `champion-omp.state`
(post-credits, NEW_BARK_TOWN (13,6), scriptMode 0). Working state
`saves/omp_speed_run.state`. Final party: TYPHLOSION L63
(Strength/Cut/Swift/Flamethrower), POLIWAG L4 (HM mule), TOGEPI L15.
Money ~16.5k.

Session log (from claude-lex2 fork @ Indigo Plateau PC):
1. Bought 4 FULL RESTORE at the Plateau 1F clerk (11,7).
2. Grounded Typhlosion L59->L60 in Victory Road entrance corridor
   (scripts/grind_vr.py); learned FLAMETHROWER at L60 (game replaced
   EMBER, not a FORGET_PRIORITY move -- fine, better spread).
3. Will/Koga/Bruno via scripts/e4.py talk_to->fight; milestone after each.
   Plateau PC 1F is planner-hostile (counter gaps only at x=6/8/9/10 on
   row 7->8, NE corridor up x=16/17, Wills warp entered SIDEWAYS from
   (15,3) pressing L). Route hand-coded in enter_room().
4. Karen wiped us twice. Root causes found by instrumenting fight()
   with a logging policy:
   - default policy heals at <30% HP with SUPER POTIONS (50hp) --
     far too late vs Karen's Gengar L45 (~55 dmg/turn burst);
   - mid-battle item flows wedge the battle permanently when their menu
     A-presses get swallowed: wBattleMode stays 2 and the game waits on
     our pack choice forever (seen in .watch.state sidecar: pack open,
     cursor on HYPER POTION). play() then reports fake "wipe"/"timeout".
5. Deliberate loss to Karen (flee-spam policy, scripts/e4_lose.py) ->
   whiteout -> respawn outside Plateau PC -> nurse heal restores PP ->
   saved. KEY FINDING: losing inside the E4 does NOT reset
   EVENT_BEAT_ELITE_4_* flags in this harness state, but DOES clear
   the *_ROOM_EXIT_OPEN door flags -> soft-lock (beaten members won't
   rebattle or reopen doors). Remedy: write the five EXIT_OPEN flags
   directly into wEventFlags (bank 1, base wEventFlags; bits parsed from
   constants/event_flags.asm), then walk through -- DoorsCallback applies
   the changeblock open-door on map entry.
6. scripts/e4_chain.py: walks through already-beaten rooms by flag,
   never talks to them, fights the target member with make_policy()
   (e4_helpers.py): heal at <55% HP with HYPER POTION, best damaging
   move by preference list. Karen won cleanly.
7. Lance (Champion) won with prefer=["STRENGTH","SWIFT",...] (fire is
   resisted by half his team). BEAT_CHAMPION_LANCE verified, Hall of
   Fame registration + credits driven through with A-mash (credits take
   seconds of wall time at raw emulation speed).

Gotchas added this session:
- talk_to fights trainer battles internally; any wrapper that assumes
  the battle starts AFTER talk_to returns will miss it. Route every
  fight through one patched Driver.fight if you need custom policy.
- Battle.me()["moves"] is [(move_id, pp)] tuples, NOT dicts; names via
  Names.moves. The policy contract differs from observe()'s format.
- E4 room entrances re-seal per entry (ENTRANCE_CLOSED changeblock at
  (4,14)); there is no backward retreat and no nurse access between
  members. Losing respawns you at the Plateau PC but keeps BEAT flags
  while clearing EXIT_OPEN flags (soft-lock; fix = flag writes above).
- select_label's confirm ('A:2 .:10') gets swallowed by some menus
  (START menu, battle pack); an explicit long press ('A:8 .:40+')
  after positioning works reliably.
- use_item('START menu did not open') flakes are timing races; retry
  loop + close_menus between attempts fixes them out of battle.

Next objectives for this timeline (post-game): S.S. Ticket phone call
from Elm -> Kanto badges, Red at Mt. Silver, catch legendaries.

## Session (battle planning, 2026-08-26): tactics.recommend gains two
evidenced behaviours (BRIEF_battle_planning.md implemented)

- Sacrifice line: Tactics.sacrifice_line() + recommend() branch. When the
  enemy's best move kills on its MINIMUM roll and no certain-KO-first exists
  (speed-respecting, BATTLE.md §8), recommend attacks for max expected
  damage -- fixed-damage moves compete on their flat value (Dragon Rage >
  resisted Surf, the RIPTIDE lesson) -- and never voluntarily switches; a
  faint's free entry beats a switch that concedes a hit. The assessment
  exposes successor + whether it finishes the chipped HP; needs frame roster
  moves/stats, which decide.read_party now decodes from wPartyMon1Moves/
  stats offsets (degrades to hits_to_ko=None if unreadable).
- Heal-aware burst: parse_trainer_items() reads data/trainers/attributes.asm
  ({class_id: items} with line provenance); outlook()['trainer'] carries live
  wTrainerClass + wOTPartyMon levels. expects_heal() = healer-class AND front
  mon is highest-level (AI_TryItem .IsHighestLevel gate, ai/items.asm:167;
  heals at half HP, :346). recommend() then prefers fewest hits-to-KO over
  bigger chip ("burst over chip") against Koga/Lance/Champion-class aces.
- The old "switch away from lethal to a resister" recommendation is GONE:
  doom analysis showed free faint-entry dominates voluntary switching.
  tests/unit/test_tactics.py rewritten accordingly; §7 reliability ordering
  untouched and still regression-pinned.
- Tests: tests/unit/test_tactics.py (new sacrifice/burst/heal sections),
  new tests/unit/test_party_read_fields.py. Full default pytest lane green.

No trek.py / integration-lane changes needed or made.
